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

``tables/`` is validated separately: those files are written in exact
ClickHouse types, so the check is that each one builds a TableSpec.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Directories that contain column-bearing schema YAML.
SCHEMA_DIRS = ("common-header", "meta", "hunts", "additional")

TABLES_DIR = "tables"


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

    # The loader resolves refs against the schemas root, and the root being
    # validated is this checkout.
    os.environ.setdefault("DFE_SCHEMAS_DIR", str(repo_root))
    table_files = sorted((repo_root / TABLES_DIR).rglob("*.yaml"))
    checked_tables = 0
    try:
        from dfe_engine.schema.table_loader import (
            load_table_config,
            load_table_spec,
            load_view_ddl,
        )
    except ImportError:
        print(
            f"NOT VALIDATED: {len(table_files)} files under {TABLES_DIR}/ -- "
            "the installed dfe-engine has no table loader",
            file=sys.stderr,
        )
    else:
        for path in table_files:
            rel = path.relative_to(repo_root)
            ref = str(rel.with_suffix(""))
            try:
                entry = SchemaLoader.load_version_entry(path, require_columns=False)
                # A config-only definition (tables/core/) declares DDL config for
                # a table whose columns come from composition, so it validates
                # through the config loader rather than the spec loader.
                if "columns" in entry:
                    load_table_spec(ref, "dfe")
                    load_view_ddl(ref, "dfe")
                else:
                    load_table_config(ref, "dfe")
                checked_tables += 1
            except Exception as exc:
                errors.append(f"{rel}: {exc}")

    if errors:
        print("Schema validation FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"Validated {len(files)} schema files and {checked_tables} table definitions: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
