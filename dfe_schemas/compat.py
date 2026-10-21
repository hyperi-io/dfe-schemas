#  Project:      dfe-schemas
#  File:         dfe_schemas/compat.py
#  Purpose:      Read the compatibility profiles and hand one out as a scalo.rules rule set
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Compatibility profiles: versioned rule rows that read another platform's header.

A profile under ``compat-profiles/`` is data, never code. Each version names the
header fields its rows set, the lookup tables they read and the rows themselves,
in the format scalo.rules compiles. dfe-engine compiles a bound profile into the
first hop's ordinary rule rows, so the receiver and the fetcher never learn that
profiles exist.

``profile.schema.json`` beside the profiles is what a profile document is.
:func:`rule_set` turns one version into the mapping ``scalo.rules.RuleSet.compile``
takes, with the lookup tables as shipped: a shipped ``aliases`` table is empty,
and the engine fills it from each registered source before it compiles.
"""

import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from dfe_schemas import schemas_root
from dfe_schemas.loader import SchemaError, read_yaml

__all__ = [
    "COMPAT_PROFILES_DIR",
    "SCHEMA_FILE",
    "load_compat_profile",
    "profile_ids",
    "profile_schema",
    "rule_set",
]

COMPAT_PROFILES_DIR = "compat-profiles"
SCHEMA_FILE = "profile.schema.json"


def _directory(root: Path | None) -> Path:
    return (root or schemas_root()) / COMPAT_PROFILES_DIR


def profile_ids(*, root: Path | None = None) -> list[str]:
    """Every shipped profile id, sorted: the file names under ``compat-profiles/``."""
    return sorted(path.stem for path in _directory(root).glob("*.yaml"))


def profile_schema(*, root: Path | None = None) -> dict[str, Any]:
    """The JSON schema every profile document validates against."""
    path = _directory(root) / SCHEMA_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaError(f"could not read {path}: {exc}") from exc


def load_compat_profile(profile_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """One profile document, as parsed.

    The id is looked up among the shipped files rather than joined onto a path,
    so an id carrying a path separator names nothing. A document whose ``id``
    differs from its file name is refused, because a binding names the id and
    the engine finds the file by it.

    Raises:
        SchemaError: No such profile, or its ``id`` is not its file name.
    """
    if profile_id not in profile_ids(root=root):
        raise SchemaError(f"no compatibility profile {profile_id!r} under {_directory(root)}")
    path = _directory(root) / f"{profile_id}.yaml"
    document = read_yaml(path)
    if not isinstance(document, dict) or document.get("id") != profile_id:
        raise SchemaError(f"{path} does not declare id {profile_id!r}")
    return document


def rule_set(
    profile_id: str,
    *,
    version: str | None = None,
    opt_in: Collection[str] = (),
    root: Path | None = None,
) -> dict[str, Any]:
    """One profile version as the mapping ``scalo.rules.RuleSet.compile`` takes.

    Args:
        profile_id: The profile's id.
        version: The version to read. None takes the profile's ``current``.
        opt_in: Ids of rows the profile leaves off by default that this binding
            turns on.
        root: The schemas root. None takes :func:`dfe_schemas.schemas_root`.

    Returns:
        ``name`` (the profile id), ``targets``, ``lookups`` and ``rules``. A row
        the version lists under ``opt_in`` is left out unless *opt_in* names it,
        and the rows keep the order the profile declares.

    Raises:
        SchemaError: No such profile or version, a version with no targets or
            no rules, or *opt_in* names a row the version does not offer.
    """
    document = load_compat_profile(profile_id, root=root)
    target = version or document.get("current")
    versions = document.get("versions") or {}
    if target not in versions:
        available = ", ".join(sorted(versions)) or "(none)"
        raise SchemaError(f"version {target!r} not in {profile_id}; available: {available}")
    entry = versions[target]
    if not entry.get("targets") or not entry.get("rules"):
        raise SchemaError(f"version {target!r} in {profile_id} declares no targets or no rules")
    offered = set(entry.get("opt_in") or ())
    unknown = sorted(set(opt_in) - offered)
    if unknown:
        raise SchemaError(
            f"{profile_id} {target} does not offer {', '.join(unknown)} to opt in to; "
            f"it offers: {', '.join(sorted(offered)) or '(none)'}"
        )
    rows = [row for row in entry["rules"] if row["id"] not in offered or row["id"] in opt_in]
    return {
        "name": profile_id,
        "targets": list(entry["targets"]),
        "lookups": {name: dict(table) for name, table in (entry.get("lookups") or {}).items()},
        "rules": rows,
    }
