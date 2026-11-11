#  Project:      dfe-schemas
#  File:         tests/test_index_pruning.py
#  Purpose:      Each catalogue index prunes granules for the query shape docs/meta-schema.md names
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A rendered index has to skip granules for the query the docs tell users to write.

A server accepting the DDL proves nothing about pruning: ``isIPAddressInRange``
on a ``minmax`` IP column builds fine and reads every granule. Each case renders
its index from the catalogue, loads 1M rows (489 granules of 2048), runs the
"Query it as" shape from docs/meta-schema.md under ``EXPLAIN indexes = 1``, and
checks both the granules the index kept and the rows the query returns.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from dfe_schemas.ddl_catalogue import DDLCatalogue
from tests.clickhouse_server import ClickHouse, start_clickhouse, stop_clickhouse

_ROWS = 1_000_000
_DATABASE = "pruning"
_NEEDLE = 123456
# 123456 as an IPv4 address, stored IPv4-mapped.
_NEEDLE_IP = "toIPv6('0.1.226.64')"
_IP = "toIPv6(IPv4NumToString(toUInt32(number)))"
_TEXT = f"concat('event ', toString(number), if(number = {_NEEDLE}, ' connection refused', ' ok'))"

# (id, use case, cardinality, column type, value expression over `number`, WHERE, rows)
_CASES = [
    (
        "dimension",
        "dimension",
        "low",
        "LowCardinality(String)",
        "concat('s', toString(intDiv(number, 50000)))",
        "c = 's7'",
        50_000,
    ),
    (
        "exact_match_string",
        "exact_match",
        "unknown",
        "Nullable(String)",
        "toString(number)",
        f"c = '{_NEEDLE}'",
        1,
    ),
    ("exact_match_ip", "exact_match", "unknown", "Nullable(IPv6)", _IP, f"c = {_NEEDLE_IP}", 1),
    (
        "exact_match_ip_array",
        "exact_match",
        "unknown",
        "Array(IPv6)",
        f"[{_IP}]",
        f"has(c, {_NEEDLE_IP})",
        1,
    ),
    (
        "exact_match_integer",
        "exact_match",
        "unknown",
        "Nullable(Int64)",
        "number * 7",
        f"c = {_NEEDLE * 7}",
        1,
    ),
    (
        "range_integer",
        "range",
        "unknown",
        "Nullable(Int64)",
        "number",
        "c BETWEEN 1000 AND 1999",
        1000,
    ),
    (
        "range_ip",
        "range",
        "unknown",
        "Nullable(IPv6)",
        _IP,
        "c BETWEEN toIPv6('0.1.0.0') AND toIPv6('0.1.255.255')",
        65_536,
    ),
    (
        "word_search",
        "word_search",
        "unknown",
        "Nullable(String)",
        _TEXT,
        "hasToken(c, 'refused')",
        1,
    ),
    (
        "substring_search",
        "substring_search",
        "unknown",
        "Nullable(String)",
        _TEXT,
        "c LIKE '%efuse%'",
        1,
    ),
    (
        "key_search_key",
        "key_search",
        "unknown",
        "Map(LowCardinality(String), String)",
        "map(concat('k', toString(number)), concat('v', toString(number)))",
        f"mapContainsKey(c, 'k{_NEEDLE}')",
        1,
    ),
    (
        "key_search_value",
        "key_search",
        "unknown",
        "Map(LowCardinality(String), String)",
        "map(concat('k', toString(number)), concat('v', toString(number)))",
        f"has(mapValues(c), 'v{_NEEDLE}')",
        1,
    ),
]


@pytest.fixture(scope="module")
def clickhouse() -> Iterator[ClickHouse]:
    client, name = start_clickhouse()
    try:
        client.run(f"CREATE DATABASE IF NOT EXISTS {_DATABASE}")
        yield client
    finally:
        stop_clickhouse(name)


def _skip_index_granules(client: ClickHouse, table: str, where: str) -> tuple[int, int]:
    """(selected, initial) granules across every skip index the plan used."""
    body = client.run(
        f"EXPLAIN indexes = 1, json = 1 SELECT count() FROM {table} WHERE {where} FORMAT TSVRaw"
    )
    plan = json.loads(body)[0]["Plan"]
    found: list[dict] = []
    stack = [plan]
    while stack:
        node = stack.pop()
        found += [idx for idx in node.get("Indexes", []) if idx.get("Type") == "Skip"]
        stack += node.get("Plans", [])
    assert found, f"no skip index was used for WHERE {where}"
    last = found[-1]
    return last["Selected Granules"], found[0]["Initial Granules"]


@pytest.mark.parametrize(
    ("case", "use_case", "cardinality", "ch_type", "value", "where", "expected"),
    [pytest.param(*case, id=case[0]) for case in _CASES],
)
def test_the_catalogue_index_prunes_for_the_documented_query(
    clickhouse, case, use_case, cardinality, ch_type, value, where, expected
):
    catalogue = DDLCatalogue.load()
    table = f"{_DATABASE}.t_{case}"
    indexes = catalogue.index_defs(
        column="c",
        use_case=use_case,
        dims=None,
        cardinality=cardinality,
        name=lambda suffix: "`idx_c`" if suffix is None else f"`idx_c_{suffix}`",
    )
    clickhouse.run(
        f"CREATE TABLE {table} (`k` UInt64, `c` {ch_type}, {', '.join(indexes)}) "
        f"ENGINE = MergeTree ORDER BY k "
        f"SETTINGS index_granularity = {catalogue.table.index_granularity}"
    )
    clickhouse.run(f"INSERT INTO {table} SELECT number, {value} FROM numbers({_ROWS})")
    clickhouse.run(f"OPTIMIZE TABLE {table} FINAL")

    selected, initial = _skip_index_granules(clickhouse, table, where)

    assert initial >= 400
    assert selected < initial // 10, f"{use_case} kept {selected} of {initial} granules"
    assert clickhouse.scalar(f"SELECT count() FROM {table} WHERE {where}") == expected
