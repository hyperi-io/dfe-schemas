#  Project:      dfe-schemas
#  File:         scripts/annotate_meta_schemas.py
#  Purpose:      Add resource_type: core and column _field_type: base to versioned schemas.
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED
"""Annotate versioned schema YAML with resource_type and per-column _field_type.

By default walks ``meta/`` and ``common-header/``. Sets ``resource_type: core`` (after
``current``) and ``_field_type: base`` on columns that lack them. Never overwrites
existing values. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_SCHEMA_DIRS = ("meta", "common-header")

RESOURCE_TYPE = "core"
FIELD_TYPE = "base"
FIELD_TYPE_LINE = f"        _field_type: {FIELD_TYPE}\n"
RESOURCE_TYPE_LINE = f"resource_type: {RESOURCE_TYPE}\n"

_COLUMN_START = re.compile(r"^      - name:")
_COLUMNS_END = re.compile(r"^    \S")


def normalize_meta_spacing(text: str) -> str:
    """Match hand-edited meta YAML: no blank line before resource_type / _field_type; blank after."""
    text = re.sub(
        r"^(current:.*)\n\n(resource_type:)",
        r"\1\n\2",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        rf"^(resource_type: {RESOURCE_TYPE})\n(versions:)",
        r"\1\n\n\2",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^(        (?!_field_type:)\S.*)\n\n(        _field_type:)",
        r"\1\n\2",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        rf"^(        _field_type: {FIELD_TYPE})\n(      - name:)",
        r"\1\n\n\2",
        text,
        flags=re.MULTILINE,
    )
    return text


def _ensure_resource_type(lines: list[str]) -> bool:
    """Insert top-level resource_type after current when missing. Return True if changed."""
    header_end = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("versions:"):
            header_end = i
            break

    for i in range(header_end):
        if lines[i].startswith("resource_type:"):
            return False

    for i in range(header_end):
        if lines[i].startswith("current:"):
            lines.insert(i + 1, RESOURCE_TYPE_LINE)
            return True

    raise ValueError("missing top-level current:")


def _column_block_end(lines: list[str], start: int) -> int:
    """Index after the last line belonging to the column that starts at *start*."""
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if _COLUMN_START.match(line):
            break
        if _COLUMNS_END.match(line) and not line.startswith("      "):
            break
        if re.match(r"^  \S", line) and not line.startswith("    "):
            break
        i += 1
    return i


def _annotate_columns(lines: list[str]) -> int:
    """Add _field_type to each column block. Return count of columns updated."""
    updated = 0
    i = 0
    in_columns = False
    while i < len(lines):
        line = lines[i]
        if line.rstrip("\n") == "    columns:":
            in_columns = True
            i += 1
            continue
        if not in_columns:
            i += 1
            continue
        if _COLUMNS_END.match(line) and not line.startswith("      "):
            in_columns = False
            i += 1
            continue
        if not _COLUMN_START.match(line):
            i += 1
            continue

        end = _column_block_end(lines, i)
        block = lines[i:end]
        if any(l.lstrip().startswith("_field_type:") for l in block):
            i = end
            continue

        while block and block[-1].strip() == "":
            block.pop()
        block.append(FIELD_TYPE_LINE)
        lines[i:end] = block
        updated += 1
        i = i + len(block)

    return updated


def annotate_text(text: str) -> tuple[str, bool, int]:
    """Return (new_text, file_changed, columns_updated)."""
    lines = text.splitlines(keepends=True)
    if text and not text.endswith("\n"):
        if lines:
            lines[-1] = lines[-1] + "\n"

    rt_changed = _ensure_resource_type(lines)
    cols = _annotate_columns(lines)
    if rt_changed or cols > 0:
        new_text = normalize_meta_spacing("".join(lines))
        return new_text, True, cols
    return text, False, 0


def resolve_schema_dirs(repo_root: Path, args: argparse.Namespace) -> list[Path]:
    """Directories to scan for ``*.yaml`` schema files."""
    if args.meta_dir is not None:
        return [args.meta_dir]
    if args.schema_dir:
        return list(args.schema_dir)
    return [repo_root / name for name in DEFAULT_SCHEMA_DIRS]


def collect_schema_paths(roots: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"schema directory not found: {root}")
        paths.extend(sorted(root.rglob("*.yaml")))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema-dir",
        action="append",
        type=Path,
        dest="schema_dir",
        metavar="DIR",
        help="Schema root to process (repeatable; default: meta and common-header)",
    )
    parser.add_argument(
        "--meta-dir",
        type=Path,
        default=None,
        help="Process only this directory (overrides --schema-dir and defaults)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing files",
    )
    parser.add_argument(
        "--fix-spacing",
        action="store_true",
        help="Normalize blank lines around resource_type and _field_type only",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    try:
        schema_roots = resolve_schema_dirs(repo_root, args)
        paths = collect_schema_paths(schema_roots)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    files_changed = 0
    total_columns = 0

    for path in paths:
        rel = path.relative_to(repo_root)
        try:
            raw = path.read_text(encoding="utf-8")
            if args.fix_spacing:
                fixed = normalize_meta_spacing(raw)
                if fixed == raw:
                    continue
                if not args.dry_run:
                    path.write_text(fixed, encoding="utf-8")
                print(f"{rel}: spacing")
                files_changed += 1
                continue

            new_text, changed, cols = annotate_text(raw)
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            print(f"{rel}: {exc}", file=sys.stderr)
            return 1

        if not changed:
            continue

        print(f"{rel}: resource_type + {cols} column(s)")
        files_changed += 1
        total_columns += cols

        if not args.dry_run:
            path.write_text(new_text, encoding="utf-8")

    if files_changed:
        if args.fix_spacing:
            action = "Would fix spacing in" if args.dry_run else "Fixed spacing in"
            print(f"{action} {files_changed} file(s)")
        else:
            action = "Would update" if args.dry_run else "Updated"
            print(f"{action} {files_changed} file(s), {total_columns} column(s)")
    else:
        print("All schemas already annotated" if not args.fix_spacing else "Spacing already correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
