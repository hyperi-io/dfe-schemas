#  Project:      dfe-schemas
#  File:         scripts/validate_schemas.py
#  Purpose:      Validate every schema YAML against the meta-schema models and
#                the registries, using dfe-engine's loader.
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
``sources/`` likewise: each file must build a Source.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Directories that contain column-bearing schema YAML.
SCHEMA_DIRS = ("common-header", "meta", "hunts", "additional")

TABLES_DIR = "tables"

SOURCES_DIR = "sources"

REGISTRIES_DIR = "registries"

ENGINE_ARGUMENT_RULES = ("none", "optional", "required")

# A definition here carries no `source`: the engine fills it from the
# deployment's landing-table setting. Validation supplies one to build the model.
SOURCE_NAME_PLACEHOLDER = "validate"


def _engine_registry_errors(*, path: Path) -> list[str]:
    """Every malformed entry in the engine registry, as one message each."""
    import yaml

    engines = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("engines") or []
    errors = []
    names = set()
    for entry in engines:
        name = entry.get("name") or ""
        if not (name) or ("(" in name) or (name.startswith(("Replicated", "Shared"))):
            errors.append(f"{name!r}: name must be a bare MergeTree-family variant")
        if name in names:
            errors.append(f"{name!r}: listed twice")
        names.add(name)
        rule = entry.get("arguments")
        if rule not in ENGINE_ARGUMENT_RULES:
            errors.append(f"{name!r}: arguments must be one of {ENGINE_ARGUMENT_RULES}")
        if bool(entry.get("argument_hint")) != (rule != "none"):
            errors.append(f"{name!r}: argument_hint is set exactly when arguments is not none")
        if not (entry.get("description")):
            errors.append(f"{name!r}: description is required")
    if "MergeTree" not in names:
        errors.append("MergeTree must be listed: it is the default engine")
    return errors


def main() -> int:
    """Validate every schema YAML; return 1 if any file is invalid."""
    repo_root = Path(__file__).resolve().parent.parent
    registries = repo_root / REGISTRIES_DIR

    try:
        from dfe_engine.schema.schema_loader import SchemaLoader, SchemaLoadError
        from dfe_engine.source.type_registry import TypeRegistry
    except ImportError as exc:  # pragma: no cover - environment guard
        print(
            "dfe-engine is not importable. Install it first. Error: " + str(exc),
            file=sys.stderr,
        )
        return 2

    registry = TypeRegistry.from_file(registries / "types.yaml")
    errors: list[str] = [
        f"{REGISTRIES_DIR}/engines.yaml: {err}"
        for err in _engine_registry_errors(path=registries / "engines.yaml")
    ]
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

    source_files = sorted((repo_root / SOURCES_DIR).rglob("*.yaml"))
    checked_sources = 0
    try:
        from dfe_engine.source.models import Source
    except ImportError:
        print(
            f"NOT VALIDATED: {len(source_files)} files under {SOURCES_DIR}/ -- "
            "the installed dfe-engine has no source model",
            file=sys.stderr,
        )
    else:
        import yaml
        from dfe_engine.source.engine_registry import EngineRegistry

        engines = EngineRegistry.from_file(registries / "engines.yaml")
        # The engine saves a source only when its engine follows the variant's argument rule; an older dfe-engine has no such check.
        check_arguments = getattr(engines, "validate_arguments", None)
        if check_arguments is None:
            print(
                f"NOT VALIDATED: engine arguments under {SOURCES_DIR}/ -- "
                "the installed dfe-engine has no argument check",
                file=sys.stderr,
            )

        for path in source_files:
            rel = path.relative_to(repo_root)
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                source = Source.model_validate({**doc, "source": SOURCE_NAME_PLACEHOLDER})
                if check_arguments is not None and source.schema_config.engine:
                    check_arguments(source.schema_config.engine)
                checked_sources += 1
            except Exception as exc:
                errors.append(f"{rel}: {exc}")

    if errors:
        print("Schema validation FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(
        f"Validated {len(files)} schema files, {checked_tables} table definitions "
        f"and {checked_sources} source definitions: OK"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
