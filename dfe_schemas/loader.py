#  Project:      dfe-schemas
#  File:         dfe_schemas/loader.py
#  Purpose:      Read the version trees and the type registry, in the package
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The reader dfe-schemas needs to check its own content.

``make validate`` needed dfe-engine importable, so the source of truth could
not validate itself without its consumer -- a cycle, and one that got worse the
moment this package became the only place a table is defined. This module is
the loader vendored back in: version trees, the two column shapes, the type
registry and header composition.

Two column shapes meet here. ``tables/**`` are written in exact ClickHouse
types, because their columns are engine state and telemetry with nothing to map
from. ``common-header/**``, ``hunts/**``, ``meta/**`` and ``additional/**`` use
DFE primitives and map through ``registries/types.yaml``.

ruamel.yaml rather than PyYAML, and the same settings dfe-engine reads these
files with: YAML 1.1 resolves ``on``, ``off``, ``yes`` and ``no`` as booleans
and 1.2 does not, so two parsers over one tree is two answers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from dfe_schemas import schemas_root

__all__ = [
    "Column",
    "SchemaError",
    "TypeRegistry",
    "compose",
    "load_columns",
    "load_profile",
    "load_profile_exclude",
    "load_version_entry",
    "read_yaml",
    "resolve",
]

COMMON_HEADER_DIR = "common-header"
REGISTRIES_DIR = "registries"

# ClickHouse's own default is 1024; the header declares 2048 and a tables/
# column asking to inherit takes whatever the header says.
INHERIT = "inherit"
_HEADER_JSON_COLUMN = "_json"
_HEADER_PROFILE = "timeseries"


class SchemaError(Exception):
    """A definition file is missing, unparseable or malformed."""


def _yaml() -> YAML:
    parser = YAML(typ="safe")
    parser.default_flow_style = False
    return parser


def read_yaml(path: Path) -> Any:
    """Parse one YAML file, raising :class:`SchemaError` on anything unreadable."""
    if not path.is_file():
        raise SchemaError(f"not found: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            return _yaml().load(handle)
    except Exception as exc:
        raise SchemaError(f"could not parse {path}: {exc}") from exc


def resolve(ref: str, *, root: Path | None = None, suffix: str = ".yaml") -> Path:
    """One definition file, by its manifest ``defines`` reference.

    A reference is a path under the schemas root with no suffix, so the
    manifest never carries a file extension and a ``.sql`` view and a ``.yaml``
    table are addressed the same way.
    """
    base = root or schemas_root()
    path = base / f"{ref}{suffix}"
    if not path.is_file():
        raise SchemaError(f"{ref!r} does not resolve to a file under {base}")
    return path


# -- the column model -------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One column, from either definition shape.

    ``ch_override`` is the exact ClickHouse type a ``tables/**`` column names;
    ``primitive`` is what the rest of the tree declares and the registry maps.
    Exactly one of the two carries the type.
    """

    name: str
    primitive: str = "string"
    ch_override: str | None = None
    attribute: tuple[str, ...] = ()
    use_case: str | None = None
    order: int | None = None
    default: str | None = None
    codec: str | None = None
    comment: str | None = None
    expr: str | None = None
    max_dynamic_paths: int | None = None


def _attributes(raw: dict[str, Any]) -> tuple[str, ...]:
    declared = raw.get("attribute") or []
    if isinstance(declared, str):
        declared = [declared]
    return tuple(declared)


def _exact_column(raw: dict[str, Any], source: Path, json_paths: int) -> Column:
    """A ``tables/**`` column: an exact ClickHouse type, no primitive behind it."""
    name = raw.get("name")
    ch_type = raw.get("ch_type")
    if not name or not ch_type:
        raise SchemaError(f"column needs both 'name' and 'ch_type' in {source}: {raw!r}")

    attribute: list[str] = []
    if raw.get("lowcardinality"):
        attribute.append("lowcardinality")

    default = raw.get("default")
    if "materialized" in raw:
        if default is not None:
            raise SchemaError(f"column {name!r} in {source} sets both 'default' and 'materialized'")
        attribute.append("materialized")
        default = raw["materialized"]

    max_paths = raw.get("max_dynamic_paths")
    if max_paths == INHERIT:
        max_paths = json_paths

    return Column(
        name=name,
        ch_override=ch_type,
        attribute=tuple(attribute),
        order=raw.get("order"),
        default=default,
        codec=raw.get("codec"),
        comment=raw.get("comment"),
        max_dynamic_paths=max_paths,
    )


def _primitive_column(raw: dict[str, Any], source: Path) -> Column:
    """A header, hunts or meta column: a DFE primitive the registry maps."""
    name = raw.get("name")
    primitive = raw.get("type")
    if not name or not primitive:
        raise SchemaError(f"column needs both 'name' and 'type' in {source}: {raw!r}")
    return Column(
        name=name,
        primitive=primitive,
        ch_override=raw.get("ch_override"),
        attribute=_attributes(raw),
        use_case=raw.get("use_case"),
        order=raw.get("order"),
        default=raw.get("default"),
        codec=raw.get("codec"),
        comment=raw.get("comment"),
        expr=raw.get("expr"),
        max_dynamic_paths=raw.get("max_dynamic_paths"),
    )


# -- version trees ----------------------------------------------------------


def load_version_entry(
    path: Path, *, version: str | None = None, require_columns: bool = True
) -> dict[str, Any]:
    """The resolved version entry of a definition file.

    ``current`` names the default version and each entry is a complete
    snapshot, so a consumer pinning a version gets exactly what that version
    declared. ``require_columns`` is False for a config-only definition -- a
    core table whose columns come from composition carries a ``table`` block
    and nothing else.
    """
    data = read_yaml(path)
    if not data:
        raise SchemaError(f"empty definition: {path}")

    target = version or data.get("current")
    versions = data.get("versions") or {}
    if target and versions:
        if target not in versions:
            available = ", ".join(sorted(versions)) or "(none)"
            raise SchemaError(f"version {target!r} not in {path}; available: {available}")
        entry = versions[target]
        if require_columns and "columns" not in entry:
            raise SchemaError(f"version {target!r} in {path} declares no columns")
        return entry
    if "columns" in data:
        return {"columns": data["columns"]}
    raise SchemaError(f"{path} carries neither 'versions' nor 'columns'")


def _header_json_paths() -> int:
    """Typed sub-paths a JSON column holds before the rest spill to a map.

    Read from the common header's ``_json`` rather than restated, so the JSON
    columns in the stack cannot drift apart.
    """
    for column in load_profile(_HEADER_PROFILE):
        if column.name == _HEADER_JSON_COLUMN and column.max_dynamic_paths:
            return int(column.max_dynamic_paths)
    raise SchemaError(f"the {_HEADER_PROFILE} header declares no max_dynamic_paths on _json")


def load_columns(path: Path, *, version: str | None = None) -> list[Column]:
    """Every column a definition declares, in file order.

    The shape is taken from the columns themselves: a ``ch_type`` is the exact
    form, a ``type`` is a primitive. A file mixing the two is rejected rather
    than half-read.
    """
    raw_columns = load_version_entry(path, version=version)["columns"]
    exact = [raw for raw in raw_columns if "ch_type" in raw]
    if exact and len(exact) != len(raw_columns):
        mixed = sorted({raw.get("name", "?") for raw in raw_columns if "ch_type" not in raw})
        raise SchemaError(f"{path} mixes ch_type and primitive columns; primitives: {mixed}")
    if exact:
        json_paths = _header_json_paths() if any("max_dynamic_paths" in c for c in exact) else 0
        return [_exact_column(raw, path, json_paths) for raw in raw_columns]
    return [_primitive_column(raw, path) for raw in raw_columns]


def load_profile(
    name: str, *, version: str | None = None, root: Path | None = None
) -> list[Column]:
    """One common-header profile's columns."""
    base = root or schemas_root()
    return load_columns(base / COMMON_HEADER_DIR / f"{name}.yaml", version=version)


def load_profile_exclude(path: Path, *, version: str | None = None) -> list[str]:
    """Header columns a composed definition drops.

    ``hunts/results.yaml`` drops ``_raw`` and ``_tags``: a detection references
    its source by ``matched_uuid``, so a second copy of the payload text and an
    ngram index over it earn nothing. A name the profile does not carry is
    ignored, because a schema has to compose onto any profile.
    """
    data = read_yaml(path)
    target = version or (data or {}).get("current")
    entry = ((data or {}).get("versions") or {}).get(target) or {}
    return list(entry.get("profile_exclude") or [])


def compose(
    header: list[Column], extra: list[Column], *, exclude: list[str] | None = None
) -> list[Column]:
    """Header columns first, then whatever the composed definition adds.

    A column the header already carries is dropped from *extra*: the header
    wins, so a source cannot quietly redefine ``_org_id``.
    """
    if exclude:
        dropped = set(exclude)
        header = [column for column in header if column.name not in dropped]
    taken = {column.name for column in header}
    return list(header) + [column for column in extra if column.name not in taken]


# -- the type registry ------------------------------------------------------


@dataclass(frozen=True)
class ResolvedType:
    """A column's full ClickHouse type and the codec that goes with it."""

    ch_type: str
    codec: str | None


@dataclass
class TypeRegistry:
    """``registries/types.yaml``: primitives, their CH types, codecs and nullability.

    The same mapping dfe-engine renders through, read from the same file.
    """

    primitives: dict[str, dict[str, Any]]
    use_cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, *, root: Path | None = None) -> TypeRegistry:
        """Read the registry the package ships."""
        base = root or schemas_root()
        data = read_yaml(base / REGISTRIES_DIR / "types.yaml") or {}
        if not isinstance(data.get("primitives"), dict):
            raise SchemaError("registries/types.yaml must map its primitives under 'primitives'")
        return cls(
            primitives=data["primitives"],
            use_cases=data.get("use_cases") or {},
            attributes=data.get("attributes") or {},
        )

    def resolve(self, column: Column) -> ResolvedType:
        """The full ClickHouse type and codec for one column.

        An exact ``ch_type`` bypasses the primitive mapping and resolves
        NON-nullable, which is what a sorting key needs; a primitive takes the
        registry's own nullability unless an attribute overrides it. An
        explicit per-column codec always wins, because it is the only way to
        set one alongside an exact type.
        """
        if column.ch_override:
            ch_type = _apply_attributes(column.ch_override, column.attribute, nullable=False)
            return ResolvedType(
                ch_type=_with_max_dynamic_paths(ch_type, column.max_dynamic_paths),
                codec=column.codec,
            )

        definition = self.primitives.get(column.primitive)
        if definition is None:
            known = ", ".join(sorted(self.primitives))
            raise SchemaError(
                f"column {column.name!r}: unknown primitive {column.primitive!r} (known: {known})"
            )
        ch_type = _apply_attributes(
            definition["ch_type"], column.attribute, nullable=definition.get("nullable", True)
        )
        return ResolvedType(
            ch_type=_with_max_dynamic_paths(ch_type, column.max_dynamic_paths),
            codec=column.codec or definition.get("codec"),
        )


def _apply_attributes(base_type: str, attribute: tuple[str, ...], *, nullable: bool) -> str:
    """Wrap a base type in Nullable and LowCardinality, in ClickHouse's order."""
    if "nullable" in attribute:
        nullable = True
    if "not_null" in attribute:
        nullable = False
    ch_type = f"Nullable({base_type})" if nullable else base_type
    if "lowcardinality" in attribute:
        ch_type = f"LowCardinality({ch_type})"
    return ch_type


def _with_max_dynamic_paths(ch_type: str, max_dynamic_paths: int | None) -> str:
    """Apply ``max_dynamic_paths`` to a bare ``JSON``.

    A type already carrying parameters is left alone, and the setting is
    meaningless on anything but JSON, so both pass through rather than
    producing DDL ClickHouse rejects.
    """
    if not max_dynamic_paths or ch_type != "JSON":
        return ch_type
    return f"JSON(max_dynamic_paths={max_dynamic_paths})"


# Identifier charset for anything spliced into a DDL position. Everything the
# package ships is committed here, so this catches an edit rather than an
# attack -- but a name carrying a backtick or a semicolon still ends the
# statement early and starts another one.
_UNSAFE = re.compile(r"[`;()'\"\\]|\s")


def quote_ident(name: str, *, what: str = "identifier") -> str:
    """Backtick-quote *name* after checking it is safe in a DDL position."""
    if not name:
        raise SchemaError(f"empty {what}")
    if _UNSAFE.search(name):
        raise SchemaError(
            f"{what} {name!r}: backticks, quotes, parens, semicolons, backslashes "
            "and whitespace are not permitted"
        )
    return f"`{name}`"
