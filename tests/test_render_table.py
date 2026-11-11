#  Project:      dfe-schemas
#  File:         tests/test_render_table.py
#  Purpose:      render_table renders a non-manifest table through the manifest's own path
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""``render_table`` is the entry point for a table that is not a manifest object.

dfe-engine's source tables render through it. The golden below is the whole
statement, so a change to any clause shows as a diff on its own.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from dfe_schemas.loader import Column, SchemaError
from dfe_schemas.render import TableDDL, TableSpec, render_table

_COLUMNS = (
    Column(name="_timestamp_load", primitive="timestamp", order=0, default="now64(3)"),
    Column(
        name="_org_id", cardinality="low", attribute=("not_null",), order=1, use_case="dimension"
    ),
    Column(name="src_ip", primitive="ip", use_case="exact_match", expr="@source: src_ip"),
    Column(name="message", primitive="text", use_case="word_search", comment="it's the body"),
    Column(name="labels", primitive="map", use_case="key_search"),
    Column(name="_json", primitive="json", attribute=("not_null",), max_dynamic_paths=2048),
)

GOLDEN = """CREATE TABLE IF NOT EXISTS `{db}`.`events`
(
    `_timestamp_load` DateTime64(3,'UTC') DEFAULT now64(3) CODEC(Delta, LZ4),
    `_org_id` LowCardinality(String) CODEC(ZSTD(1)),
    `src_ip` Nullable(IPv6) COMMENT '@source: src_ip' CODEC(LZ4),
    `message` Nullable(String) COMMENT 'it\\'s the body' CODEC(ZSTD(3)),
    `labels` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    `_json` JSON(max_dynamic_paths=2048) CODEC(ZSTD(3)),
    INDEX `idx__org_id` `_org_id` TYPE set(0) GRANULARITY 4,
    INDEX `idx_src_ip` `src_ip` TYPE bloom_filter GRANULARITY 4,
    INDEX `idx_message` `message` TYPE text(tokenizer=splitByNonAlpha) GRANULARITY 1,
    INDEX `idx_labels_key` mapKeys(`labels`) TYPE text(tokenizer=array) GRANULARITY 1,
    INDEX `idx_labels_value` mapValues(`labels`) TYPE text(tokenizer=array) GRANULARITY 1
)
ENGINE = ReplicatedMergeTree
PARTITION BY toYYYYMMDD(_timestamp_load)
PRIMARY KEY (`_timestamp_load`, `_org_id`)
ORDER BY (`_timestamp_load`, `_org_id`)
TTL _timestamp_load + INTERVAL 90 DAY DELETE WHERE _timestamp_load >= 0
SETTINGS
    index_granularity = 2048,
    ttl_only_drop_parts = 1,
    merge_max_dynamic_subcolumns_in_wide_part = 64,
    write_marks_for_substreams_in_compact_parts = 0
COMMENT '@profile: timeseries'"""


_BASE = TableSpec(
    database="{db}",
    name="events",
    columns=_COLUMNS,
    ttl_days=90,
    ttl_columns=("_timestamp_load",),
    comment="@profile: timeseries",
)


def _spec(**overrides: Any) -> TableSpec:
    return replace(_BASE, **overrides)


def test_a_source_table_renders_the_golden_statement():
    assert render_table(_spec()) == GOLDEN


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param(
            {"order_by": "toStartOfHour(_timestamp_load)", "primary_key": "_org_id"},
            "PRIMARY KEY (_org_id)\nORDER BY (toStartOfHour(_timestamp_load))",
            id="raw_order_by_with_primary_key",
        ),
        pytest.param(
            {"sample_by": "cityHash64(_org_id)"},
            "SAMPLE BY cityHash64(_org_id)\nTTL",
            id="sample_by",
        ),
        pytest.param(
            {"partition_granularity": "month"}, "PARTITION BY toYYYYMM(_timestamp_load)", id="month"
        ),
        pytest.param(
            {"partition_by": "toDate(_timestamp_load)"},
            "PARTITION BY toDate(_timestamp_load)",
            id="raw_partition",
        ),
        pytest.param({"index_granularity": 8192}, "index_granularity = 8192", id="granularity"),
        pytest.param(
            {"ttl_only_drop_parts": False},
            "index_granularity = 2048,\n    merge_max",
            id="no_drop_parts",
        ),
        pytest.param(
            {"projection_order_by": "src_ip"},
            "PROJECTION src_ip_optimized (SELECT * ORDER BY `src_ip`)",
            id="projection",
        ),
    ],
)
def test_each_declared_clause_lands_where_clickhouse_wants_it(overrides, expected):
    assert expected in render_table(_spec(**overrides))


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        pytest.param({"ttl_columns": ()}, "no ttl_columns", id="ttl_over_nothing"),
        pytest.param({"ttl_columns": ("absent",)}, "absent column", id="ttl_over_absent_column"),
        pytest.param(
            {"partition_granularity": "hour"},
            "unknown partition_granularity 'hour'",
            id="granularity",
        ),
        pytest.param(
            {"columns": (*_COLUMNS, Column(name="k", order=2))}, "ORDER BY key", id="nullable_key"
        ),
    ],
)
def test_a_table_that_cannot_render_is_refused(overrides, refusal):
    with pytest.raises(SchemaError, match=refusal):
        render_table(_spec(**overrides))


def test_a_nullable_key_is_named_before_rendering():
    """A caller that drops such a key rather than refusing it can find it first."""
    columns = [*_COLUMNS, Column(name="k", order=2)]
    assert TableDDL.load().nullable_key_columns(columns) == ["k"]


def test_a_verbatim_index_wins_over_the_use_case():
    column = Column(
        name="_raw",
        primitive="text",
        use_case="substring_search",
        index="text(tokenizer = 'splitByNonAlpha') GRANULARITY 64",
    )
    assert TableDDL.load().index_defs(column) == [
        "INDEX `idx__raw` `_raw` TYPE text(tokenizer = 'splitByNonAlpha') GRANULARITY 64"
    ]


def test_a_dotted_ttl_or_projection_column_is_quoted():
    """`event.time` is one identifier only once quoted, so a bare one breaks the statement."""
    columns = (*_COLUMNS, Column(name="event.time", primitive="timestamp"))
    rendered = render_table(
        _spec(columns=columns, ttl_columns=("event.time",), projection_order_by="event.time")
    )
    assert "TTL `event.time` + INTERVAL 90 DAY DELETE WHERE `event.time` >= 0" in rendered
    assert "PROJECTION `event.time_optimized` (SELECT * ORDER BY `event.time`)" in rendered


def test_an_index_name_unsafe_in_ddl_is_refused():
    with pytest.raises(SchemaError, match="index name"):
        TableDDL.load().index_defs(Column(name="a`b", use_case="dimension"))
