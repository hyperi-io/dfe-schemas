#  Project:      dfe-schemas
#  File:         dfe_schemas/manifest.py
#  Purpose:      Read and check the apply manifest -- the one list of objects
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The apply manifest: every object a deployment creates, in the order it is.

One list, read by one applier. Before this, the order lived in three Python
tuples in dfe-engine, a Helm helper in dfe-infra and a resolver in dfe-docker,
and the only way to know what a deployment would create was to read all five.

Two checks earn the file its keep, and both fail loudly rather than rendering
something half-right:

* a DUPLICATE id, because two definitions claiming one name means whichever
  applies second silently wins
* a dependency on an id that is unknown or declared LATER, because a view over
  a table that does not exist yet is an apply that fails on a fresh cluster and
  succeeds on every re-run, which is the worst shape a bootstrap can have
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dfe_schemas import schemas_root
from dfe_schemas.loader import SchemaError, read_yaml

__all__ = [
    "KINDS",
    "Manifest",
    "ManifestError",
    "ManifestObject",
    "load_manifest",
]

MANIFEST_FILE = "manifest.yaml"
SUPPORTED_VERSION = 1

KINDS = ("database", "table", "materialized_view", "view", "role", "topic")
TOPOLOGIES = ("resolved", "fixed")

# Kinds whose definition file is SQL rather than a version tree.
_SQL_KINDS = ("view",)
# Kinds that name no ClickHouse database.
_DATABASELESS_KINDS = ("topic",)


class ManifestError(Exception):
    """The manifest is malformed, or an object in it does not check out."""


@dataclass(frozen=True)
class DatabaseDecl:
    """A database the manifest's objects land in.

    Exactly one of ``parameter`` and ``name`` is set: ``parameter`` names the
    deployment setting that supplies the real name, ``name`` is fixed.
    """

    key: str
    parameter: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ManifestObject:
    """One object: what it is, where it lands and what it needs first."""

    id: str
    kind: str
    topology: str
    additive: bool
    database: str | None = None
    defines: str | None = None
    section: str | None = None
    key: str | None = None
    header: str | None = None
    compose: str | None = None
    optional: bool = False
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    """The whole manifest, checked."""

    manifest_version: int
    parameters: dict[str, Any]
    databases: tuple[DatabaseDecl, ...]
    objects: tuple[ManifestObject, ...]
    root: Path

    def database_name(self, key: str, *, data_database: str) -> str:
        """The real database name for a declared key.

        A key bound to a parameter takes the deployment's value; a fixed one
        takes what it declares. An unknown key is an error rather than a
        default, because defaulting it would create the object in the wrong
        database and report success.
        """
        for declared in self.databases:
            if declared.key != key:
                continue
            if declared.name:
                return declared.name
            return data_database
        raise ManifestError(f"no database declared with key {key!r}")

    def by_kind(self, kind: str) -> tuple[ManifestObject, ...]:
        """Every object of one kind, in manifest order."""
        return tuple(obj for obj in self.objects if obj.kind == kind)


def _object(raw: dict[str, Any], index: int) -> ManifestObject:
    missing = [field for field in ("id", "kind", "topology", "additive") if field not in raw]
    if missing:
        raise ManifestError(f"object {index} is missing {', '.join(missing)}")
    return ManifestObject(
        id=str(raw["id"]),
        kind=str(raw["kind"]),
        topology=str(raw["topology"]),
        additive=bool(raw["additive"]),
        database=raw.get("database"),
        defines=raw.get("defines"),
        section=raw.get("section"),
        key=raw.get("key"),
        header=raw.get("header"),
        compose=raw.get("compose"),
        optional=bool(raw.get("optional", False)),
        depends_on=tuple(raw.get("depends_on") or ()),
    )


def _check(manifest: Manifest) -> None:
    """Every problem in the manifest at once, so one run names them all."""
    problems: list[str] = []
    keys = {declared.key for declared in manifest.databases}
    seen: set[str] = set()

    for obj in manifest.objects:
        if obj.id in seen:
            problems.append(f"{obj.id}: declared twice")
        seen.add(obj.id)

        if obj.kind not in KINDS:
            problems.append(f"{obj.id}: unknown kind {obj.kind!r}; known: {', '.join(KINDS)}")
        if obj.topology not in TOPOLOGIES:
            problems.append(
                f"{obj.id}: topology must be one of {', '.join(TOPOLOGIES)}, got {obj.topology!r}"
            )

        if obj.kind in _DATABASELESS_KINDS:
            if obj.database:
                problems.append(f"{obj.id}: a {obj.kind} names no database")
        elif obj.database not in keys:
            problems.append(
                f"{obj.id}: database {obj.database!r} is not declared; "
                f"declared keys: {', '.join(sorted(keys))}"
            )

        for dependency in obj.depends_on:
            if dependency == obj.id:
                problems.append(f"{obj.id}: depends on itself")
            elif dependency not in seen:
                problems.append(
                    f"{obj.id}: depends on {dependency!r}, which is not declared before it"
                )

        if obj.defines:
            suffix = ".sql" if obj.kind in _SQL_KINDS else ".yaml"
            if not (manifest.root / f"{obj.defines}{suffix}").is_file():
                problems.append(f"{obj.id}: defines {obj.defines}{suffix}, which does not exist")
        elif obj.kind != "database":
            problems.append(f"{obj.id}: a {obj.kind} needs a 'defines' reference")

    if problems:
        raise ManifestError(
            "the apply manifest does not check out:\n  - " + "\n  - ".join(problems)
        )


def load_manifest(*, root: Path | None = None) -> Manifest:
    """Read the manifest and check it, or raise :class:`ManifestError`."""
    base = root or schemas_root()
    try:
        data = read_yaml(base / MANIFEST_FILE) or {}
    except SchemaError as exc:
        raise ManifestError(str(exc)) from exc

    version = data.get("manifest_version")
    if version != SUPPORTED_VERSION:
        raise ManifestError(
            f"manifest_version {version!r} is not supported; this package reads {SUPPORTED_VERSION}"
        )

    databases = tuple(
        DatabaseDecl(
            key=str(entry["key"]), parameter=entry.get("parameter"), name=entry.get("name")
        )
        for entry in data.get("databases") or ()
    )
    objects = tuple(_object(raw, index) for index, raw in enumerate(data.get("objects") or ()))
    if not objects:
        raise ManifestError("the apply manifest declares no objects")

    manifest = Manifest(
        manifest_version=version,
        parameters=dict(data.get("parameters") or {}),
        databases=databases,
        objects=objects,
        root=base,
    )
    _check(manifest)
    return manifest
