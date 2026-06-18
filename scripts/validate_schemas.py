#  Project:      dfe-schemas
#  File:         scripts/validate_schemas.py
#  Purpose:      Validate every schema YAML against the meta-schema models and
#                the TypeRegistry, using dfe-engine's loader.
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED
"""Validate all schema YAML files against the dfe-engine meta-schema.

Walks the schema directories, loads each file through ``SchemaLoader``
(structural + Pydantic validation), then runs ``validate_columns`` against
the ``TypeRegistry`` (type/use_case/attribute/ch_override semantics).
Exits non-zero on the first batch of errors. Requires dfe-engine importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Directories that contain column-bearing schema YAML.
SCHEMA_DIRS = ("common-header", "meta", "hunts", "additional")


def main() -> int:
    """Validate every schema YAML; return 1 if any file is invalid."""
    repo_root = Path(__file__).resolve().parent.parent

    try:
        from dfe_engine.schema.schema_loader import SchemaLoader, SchemaLoadError
        from dfe_engine.source.type_registry import TypeRegistry
    except ImportError as exc:  # pragma: no cover - environment guard
        print(
            "dfe-engine is not importable. Install it first. Error: " + str(exc),
            file=sys.stderr,
        )
        return 2

    registry = TypeRegistry.default()
    errors: list[str] = []
    files: list[Path] = []
    for sub in SCHEMA_DIRS:
        files.extend(sorted((repo_root / sub).rglob("*.yaml")))

    for path in files:
        rel = path.relative_to(repo_root)
        try:
            columns = SchemaLoader.load_columns(path)
        except SchemaLoadError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        errors.extend(f"{rel}: {err}" for err in SchemaLoader.validate_columns(columns, registry))

    if errors:
        print("Schema validation FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"Validated {len(files)} schema files: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
