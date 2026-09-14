#  Project:      dfe-schemas
#  File:         scripts/generate_meta_schemas.py
#  Purpose:      Derive one meta schema per store from an NDJSON dump of live rows.
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED
"""Generate ``meta/<provider>/<store>.yaml`` from a directory of NDJSON dumps.

Each ``<store>.jsonl`` in the dump directory is one store: one JSON object per
line, as a provider's export or a fetcher dump wrote it. The columns of the
generated schema are the UNION of top-level keys across every row, typed from
the values observed, so the schema describes what the provider actually sends
rather than what its documentation says. Stdlib only, and deterministic: the
same dump and the same arguments write byte-identical files, so a re-run is a
no-op and ``--check`` can gate it.

Typing rules, from the observed JSON kinds of a key across all rows:

- ``bool`` -> ``boolean`` (dimension); ``int`` -> ``integer`` (range);
  ``float``, or int and float mixed -> ``float`` (range).
- ``str``: every non-empty value a UUID -> ``uuid``; every one an IP address
  -> ``ip`` (range); every one RFC 3339 -> ``datetime`` (range); any value
  multi-line or longer than ``--text-min-length`` -> ``text`` (no index);
  otherwise ``string`` (dimension, ``lowcardinality`` when fewer than half the
  rows are distinct). A shape mixed with empty strings falls back to
  ``string``, because an empty string is not a valid UUID, IP or timestamp.
- ``dict`` -> ``json``, always ``not_null``: ClickHouse writes ``{}`` for an
  absent object, and the shipped header profiles keep JSON out of Nullable.
- ``list`` -> the element primitive with ``ch_override: Array(...)``, which is
  the documented escape hatch since the 13 primitives have no array; an
  absent list lands as ``[]``. Element kinds decide the inner type: all
  objects -> ``Array(JSON)``, all ints -> ``Array(Int64)``, floats ->
  ``Array(Float64)``, bools -> ``Array(Bool)``, anything else ->
  ``Array(String)``.
- Kinds that conflict across rows (a string on one row, a number on another)
  fall back to ``string`` and are reported.
- A key that is null on every row has no observable type and becomes a
  nullable ``string``.
- ``nullable`` is set on any column some row lacks or has null, except the
  ``json`` and array cases above. Integers that look like Unix epoch seconds
  stay ``integer``: ClickHouse reads a bare integer into ``DateTime64(3)`` as
  milliseconds, so typing them ``datetime`` would land every value in 1970.

The row key (``--row-key STORE=KEY``, default ``id``) leads the column list and
takes ``use_case: bloom``; other UUID columns take ``bloom`` when at least half
the rows are distinct and ``dimension`` otherwise. Every expression reads
``@source: <record-path>.<key>`` so a schema composes onto the fetcher's
snapshot envelope, where the provider's record sits under ``record``; pass an
empty ``--record-path`` for rows that land at the top level.

A target file that already holds other versions is refused: published versions
are immutable. To add a version, generate the new entry with ``--version`` into
a scratch ``--out-root`` and splice it into the file by hand.

A provider's arguments (stores, row keys, descriptions) live in an ``@`` file
beside this script, one argument per line, so a regeneration is one command.
The runZero stores, from a directory holding a dump of the ``.jsonl`` exports::

    scripts/generate_meta_schemas.py --dump /path/to/dump @scripts/meta-runzero.args
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_OUT_ROOT = "meta"
DEFAULT_VERSION = "1.0.0"
DEFAULT_RECORD_PATH = "record"
DEFAULT_ROW_KEY = "id"
DEFAULT_TEXT_MIN_LENGTH = 256

RESOURCE_TYPE = "core"
FIELD_TYPE = "base"

# Column names a record key may not take: the common header (the profile wins
# on a duplicate, silently dropping the record's column) and the snapshot
# envelope (the overlay would override the record's column).
DEFAULT_RESERVED = (
    "_timestamp_load,_timestamp,_timestamp_received,_uuid,_org_id,_source,_raw,_json,_tags,"
    "kind,snapshot_id,snapshot_at,timestamp,store,seq,total,row_count,completed_at,bytes,row_key"
)

# A column name ClickHouse and the loader take without quoting games.
_COLUMN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VERSION_LINE = re.compile(r'^  "(?P<version>[^"]+)":\s*$', re.MULTILINE)
_DATE_LINE = re.compile(r'^    date:\s*"(?P<date>[^"]+)"\s*$', re.MULTILINE)
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:?\d{2})?$")

# Unix epoch seconds between 2000-01-01 and 2100-01-01: the comment says so,
# the type stays integer (see the module docstring).
_EPOCH_MIN = 946_684_800
_EPOCH_MAX = 4_102_444_800

# Primitive -> the codec the type registry would have applied, restated on a
# column whose ch_override switches the automatic codec off.
_CODEC = {
    "string": "ZSTD(1)",
    "integer": "ZSTD(1)",
    "float": "ZSTD(1)",
    "boolean": "LZ4",
    "json": "ZSTD(3)",
}

_ARRAY_INNER = {
    "string": "String",
    "integer": "Int64",
    "float": "Float64",
    "boolean": "Bool",
    "json": "JSON",
}


class GenerateError(Exception):
    """A dump or an argument the generator cannot turn into a schema."""


@dataclass(slots=True)
class KeyStats:
    """What the dump showed for one top-level key of one store."""

    present: int = 0
    nulls: int = 0
    kinds: set[str] = field(default_factory=set)
    distinct: set[str] = field(default_factory=set)
    empty_strings: int = 0
    str_classes: set[str] = field(default_factory=set)
    max_len: int = 0
    element_kinds: set[str] = field(default_factory=set)
    int_zeros: int = 0
    int_nonzero: int = 0
    int_epoch_like: int = 0


@dataclass(frozen=True, slots=True)
class Column:
    """One generated column, in the field order the hand-written schemas use."""

    name: str
    type: str
    attribute: tuple[str, ...]
    use_case: str | None
    ch_override: str | None
    codec: str | None
    expr: str
    comment: str | None


@dataclass(frozen=True, slots=True)
class StoreSchema:
    """The generated schema for one store plus what the run should report."""

    store: str
    columns: tuple[Column, ...]
    rows: int
    notes: tuple[str, ...]

    @property
    def nullable_count(self) -> int:
        return sum(1 for col in self.columns if "nullable" in col.attribute)


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    raise GenerateError(f"unsupported JSON value type {type(value).__name__}")


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _str_class(text: str) -> str:
    if _UUID.match(text):
        return "uuid"
    if _is_ip(text):
        return "ip"
    if _RFC3339.match(text):
        return "rfc3339"
    if "\n" in text:
        return "multiline"
    return "plain"


def read_rows(path: Path) -> list[dict]:
    """Parse one NDJSON file into its rows; blank lines are skipped."""
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise GenerateError(f"{path.name}:{number}: not JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise GenerateError(f"{path.name}:{number}: a row must be a JSON object")
        rows.append(row)
    return rows


def profile(rows: list[dict]) -> dict[str, KeyStats]:
    """Accumulate per-key statistics over every row."""
    stats: dict[str, KeyStats] = {}
    for row in rows:
        for key, value in row.items():
            entry = stats.setdefault(key, KeyStats())
            entry.present += 1
            kind = _kind(value)
            if kind == "null":
                entry.nulls += 1
                continue
            entry.kinds.add(kind)
            entry.distinct.add(json.dumps(value, sort_keys=True))
            if kind == "str":
                if value == "":
                    entry.empty_strings += 1
                else:
                    entry.str_classes.add(_str_class(value))
                entry.max_len = max(entry.max_len, len(value))
            elif kind == "int":
                if value == 0:
                    entry.int_zeros += 1
                else:
                    entry.int_nonzero += 1
                    if _EPOCH_MIN <= value <= _EPOCH_MAX:
                        entry.int_epoch_like += 1
            elif kind == "list":
                for element in value:
                    entry.element_kinds.add(_kind(element))
    return stats


def _string_shape(stats: KeyStats, text_min_length: int) -> tuple[str, str | None]:
    """Resolve a key seen only as strings to (primitive, observation).

    The observation is the part of what the dump showed that the primitive
    does not already say; ``None`` when the primitive says it all.
    """
    classes = stats.str_classes
    if not classes:
        return "string", "empty string on every row observed"
    if stats.empty_strings and classes <= {"uuid", "ip", "rfc3339"}:
        shape = "/".join(sorted(classes))
        return "string", f"{shape} string, empty on some rows, so kept as a string"
    if classes == {"uuid"}:
        return "uuid", None
    if classes == {"ip"}:
        return "ip", None
    if classes == {"rfc3339"}:
        return "datetime", None
    if "multiline" in classes or stats.max_len > text_min_length:
        return "text", None
    return "string", None


def _int_observation(stats: KeyStats) -> str | None:
    if stats.int_nonzero == 0:
        return "0 on every row observed"
    if stats.int_epoch_like == stats.int_nonzero:
        if stats.int_zeros:
            return "Unix epoch seconds as exported, 0 when unset"
        return "Unix epoch seconds as exported"
    return None


def _array_type(stats: KeyStats) -> tuple[str, str]:
    """Resolve a list key to (element primitive, observation)."""
    kinds = stats.element_kinds
    if kinds and kinds == {"dict"}:
        return "json", "list of objects"
    if kinds and kinds == {"int"}:
        return "integer", "list of integers"
    if kinds and kinds <= {"int", "float"}:
        return "float", "list of numbers"
    if kinds and kinds == {"bool"}:
        return "boolean", "list of booleans"
    if not kinds:
        return "string", "list, empty on every row observed"
    if kinds == {"str"}:
        return "string", "list of strings"
    return "string", "list of mixed values, stored as strings"


def _low_cardinality(stats: KeyStats, rows: int) -> bool:
    return len(stats.distinct) * 2 < rows


def derive_column(
    key: str,
    stats: KeyStats,
    *,
    rows: int,
    row_key: str,
    record_path: str,
    text_min_length: int,
    notes: list[str],
) -> Column:
    """Turn one key's statistics into a column under the module's typing rules."""
    optional = stats.present < rows or stats.nulls > 0
    kinds = stats.kinds
    attribute: list[str] = []
    use_case: str | None = None
    ch_override: str | None = None
    codec: str | None = None
    is_row_key = key == row_key

    observation: str | None

    if not kinds:
        primitive = "string"
        observation = "null on every row observed, so the type is unknown"
    elif kinds == {"bool"}:
        primitive, observation, use_case = "boolean", None, "dimension"
    elif kinds == {"int"}:
        primitive, observation, use_case = "integer", _int_observation(stats), "range"
    elif kinds <= {"int", "float"}:
        primitive, observation, use_case = "float", None, "range"
    elif kinds == {"str"}:
        primitive, observation = _string_shape(stats, text_min_length)
        if primitive == "uuid":
            use_case = "bloom" if len(stats.distinct) * 2 >= rows else "dimension"
        elif primitive in {"ip", "datetime"}:
            use_case = "range"
        elif primitive == "string":
            use_case = "dimension"
            if _low_cardinality(stats, rows):
                attribute.append("lowcardinality")
    elif kinds == {"dict"}:
        primitive, observation = "json", None
    elif kinds == {"list"}:
        primitive, observation = _array_type(stats)
        ch_override = f"Array({_ARRAY_INNER[primitive]})"
        codec = _CODEC[primitive]
    else:
        primitive = "string"
        seen = ", ".join(sorted(kinds))
        observation = f"seen as {seen} on different rows, so stored as a string"
        notes.append(f"{key}: kinds conflict across rows ({seen})")

    parts: list[str] = []
    if is_row_key:
        if primitive not in {"string", "uuid"}:
            raise GenerateError(
                f"row key {key!r} resolved to {primitive!r}; a row key must be a string or UUID"
            )
        use_case = "bloom"
        attribute = [a for a in attribute if a != "lowcardinality"]
        parts.append("Row key of the export")
    if observation:
        parts.append(observation)

    if ch_override is not None:
        parts.append("[] when absent")
    elif primitive == "json":
        attribute.append("not_null")
        parts.append("{} when absent")
    elif optional:
        attribute.append("nullable")

    comment = "; ".join(parts)
    path = f"{record_path}.{key}" if record_path else key
    return Column(
        name=key,
        type=primitive,
        attribute=tuple(attribute),
        use_case=use_case,
        ch_override=ch_override,
        codec=codec,
        expr=f"@source: {path}",
        comment=comment[0].upper() + comment[1:] if comment else None,
    )


def derive_store(
    store: str,
    rows: list[dict],
    *,
    row_key: str,
    record_path: str,
    text_min_length: int,
    reserved: frozenset[str],
) -> StoreSchema:
    """Derive the whole column list for one store."""
    if not rows:
        raise GenerateError(f"{store}: no rows, nothing to derive")
    stats = profile(rows)
    if row_key not in stats:
        raise GenerateError(
            f"{store}: row key {row_key!r} is not a key of any row; pass --row-key {store}=<key>"
        )
    bad_names = sorted(k for k in stats if not _COLUMN_NAME.match(k))
    if bad_names:
        raise GenerateError(f"{store}: keys that cannot be column names: {', '.join(bad_names)}")
    clashes = sorted(k for k in stats if k in reserved)
    if clashes:
        raise GenerateError(
            f"{store}: keys that collide with reserved column names: {', '.join(clashes)}"
        )

    notes: list[str] = []
    ordered = [row_key, *sorted(k for k in stats if k != row_key)]
    columns = tuple(
        derive_column(
            key,
            stats[key],
            rows=len(rows),
            row_key=row_key,
            record_path=record_path,
            text_min_length=text_min_length,
            notes=notes,
        )
        for key in ordered
    )
    return StoreSchema(store=store, columns=columns, rows=len(rows), notes=tuple(notes))


def _yaml_string(text: str) -> str:
    """Quote a scalar the way the hand-written schemas do."""
    return json.dumps(text, ensure_ascii=True)


def render(
    schema: StoreSchema,
    *,
    provider_name: str,
    describe: str,
    version: str,
    date: str,
    record_path: str,
) -> str:
    """Render one schema file in the version-tree layout."""
    where = f"under `{record_path}`" if record_path else "at the top level of the payload"
    summary = f"Initial {provider_name} {schema.store} schema, from a live export"
    lines = [
        f"# Meta schema: {provider_name} {schema.store}",
        "#",
        f"# {describe}",
        "#",
        "# Columns are the union of keys across the rows of a live export, typed from",
        f"# the values observed; the record sits {where}, which is where every",
        "# expression reads from. Generated by scripts/generate_meta_schemas.py: a",
        "# changed export becomes a new version entry, never an edit of a published one.",
        "",
        f"current: {_yaml_string(version)}",
        f"resource_type: {RESOURCE_TYPE}",
        "",
        "versions:",
        f"  {_yaml_string(version)}:",
        f"    date: {_yaml_string(date)}",
        "    type: model",
        f"    summary: {_yaml_string(summary)}",
        "    columns:",
    ]
    for index, col in enumerate(schema.columns):
        if index:
            lines.append("")
        lines.append(f"      - name: {col.name}")
        lines.append(f"        type: {col.type}")
        if col.ch_override:
            lines.append(f"        ch_override: {_yaml_string(col.ch_override)}")
        if col.attribute:
            lines.append(f"        attribute: [{', '.join(col.attribute)}]")
        if col.use_case:
            lines.append(f"        use_case: {col.use_case}")
        lines.append(f"        expr: {_yaml_string(col.expr)}")
        if col.comment:
            lines.append(f"        comment: {_yaml_string(col.comment)}")
        if col.codec:
            lines.append(f"        codec: {_yaml_string(col.codec)}")
        lines.append(f"        _field_type: {FIELD_TYPE}")
    return "\n".join(lines) + "\n"


def existing_versions(path: Path) -> dict[str, str | None]:
    """Version -> date of the version entries a schema file already holds."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    versions = [m.group("version") for m in _VERSION_LINE.finditer(text)]
    dates = [m.group("date") for m in _DATE_LINE.finditer(text)]
    found: dict[str, str | None] = {}
    for index, version in enumerate(versions):
        found[version] = dates[index] if index < len(dates) else None
    return found


def parse_pairs(values: list[str] | None, option: str) -> dict[str, str]:
    """Parse repeated ``STORE=VALUE`` options."""
    pairs: dict[str, str] = {}
    for item in values or []:
        store, separator, value = item.partition("=")
        if not separator or not store or not value:
            raise GenerateError(f"{option} expects STORE=VALUE, got {item!r}")
        pairs[store] = value
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        fromfile_prefix_chars="@",
    )
    parser.add_argument(
        "--dump", type=Path, required=True, metavar="DIR", help="Directory of <store>.jsonl files"
    )
    parser.add_argument(
        "--provider", required=True, help="Provider name; files land in meta/<provider>/"
    )
    parser.add_argument(
        "--provider-name",
        default=None,
        help="How the provider is written in prose (default: the --provider value)",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=None,
        metavar="DIR",
        help=f"Schema tree root (default: {DEFAULT_OUT_ROOT}/ under the repo)",
    )
    parser.add_argument(
        "--store",
        action="append",
        dest="stores",
        metavar="STORE",
        help="Only this store (repeatable; default: every *.jsonl in the dump)",
    )
    parser.add_argument(
        "--row-key",
        action="append",
        dest="row_keys",
        metavar="STORE=KEY",
        help=f"The key that identifies a row of STORE (default: {DEFAULT_ROW_KEY})",
    )
    parser.add_argument(
        "--describe",
        action="append",
        dest="describes",
        metavar="STORE=TEXT",
        help="One-line description for the file header (default: '<provider> <store> export')",
    )
    parser.add_argument(
        "--record-path",
        default=DEFAULT_RECORD_PATH,
        help=f"Where the record sits in the landed payload (default: {DEFAULT_RECORD_PATH!r})",
    )
    parser.add_argument(
        "--reserved",
        default=DEFAULT_RESERVED,
        help="Comma-separated column names a record key must not collide with "
        "(default: the common header and the snapshot envelope)",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Version entry to write")
    parser.add_argument(
        "--date",
        default=None,
        help="Version date (default: the existing entry's date, else today UTC)",
    )
    parser.add_argument(
        "--text-min-length",
        type=int,
        default=DEFAULT_TEXT_MIN_LENGTH,
        help=f"Longest string still typed 'string' (default: {DEFAULT_TEXT_MIN_LENGTH})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any file would change (implies --dry-run)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_root = args.out_root if args.out_root is not None else repo_root / DEFAULT_OUT_ROOT
    out_dir = out_root / args.provider
    provider_name = args.provider_name or args.provider
    dry_run = args.dry_run or args.check

    try:
        row_keys = parse_pairs(args.row_keys, "--row-key")
        describes = parse_pairs(args.describes, "--describe")
        if not args.dump.is_dir():
            raise GenerateError(f"dump directory not found: {args.dump}")
        stores = args.stores or sorted(p.stem for p in args.dump.glob("*.jsonl"))
        if not stores:
            raise GenerateError(f"no *.jsonl in {args.dump}")
        reserved = frozenset(name for name in args.reserved.split(",") if name)
    except GenerateError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    changed = 0
    failed = 0
    for store in stores:
        path = out_dir / f"{store}.yaml"
        rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
        try:
            rows = read_rows(args.dump / f"{store}.jsonl")
            schema = derive_store(
                store,
                rows,
                row_key=row_keys.get(store, DEFAULT_ROW_KEY),
                record_path=args.record_path,
                text_min_length=args.text_min_length,
                reserved=reserved,
            )
            held = existing_versions(path)
            others = sorted(v for v in held if v != args.version)
            if others:
                raise GenerateError(
                    f"{rel} already holds version(s) {', '.join(others)}; "
                    "add a version entry by hand, published versions are immutable"
                )
            date = args.date or held.get(args.version) or dt.datetime.now(dt.UTC).date().isoformat()
            text = render(
                schema,
                provider_name=provider_name,
                describe=describes.get(store, f"{provider_name} {store} export"),
                version=args.version,
                date=date,
                record_path=args.record_path,
            )
        except GenerateError as exc:
            print(f"{rel}: {exc}", file=sys.stderr)
            failed += 1
            continue

        for note in schema.notes:
            print(f"{rel}: note: {note}")
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            print(
                f"{rel}: unchanged ({len(schema.columns)} columns, {schema.nullable_count} nullable)"
            )
            continue
        changed += 1
        verb = "would write" if dry_run else "wrote"
        print(
            f"{rel}: {verb} {len(schema.columns)} columns "
            f"({schema.nullable_count} nullable, row key {schema.columns[0].name}, "
            f"{schema.rows} rows read)"
        )
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    if failed:
        return 1
    if args.check and changed:
        print(f"{changed} file(s) out of date")
        return 1
    if changed:
        print(f"{'Would update' if dry_run else 'Updated'} {changed} file(s)")
    else:
        print("All schemas up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
