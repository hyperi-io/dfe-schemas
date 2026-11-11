#  Project:      dfe-schemas
#  File:         tests/test_index_use_cases.py
#  Purpose:      A use case names a question, and renders the index that answers it
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A column declares the question it is asked, never the ClickHouse index.

The vocabulary (``registries/types.yaml``) and the index catalogue
(``registries/ddl.yaml``) are two halves of one contract: a use case the
registry accepts but the catalogue does not answer is a column that silently
gets no index, and a catalogue entry keyed on a name the registry rejects can
never fire. Both directions are pinned here.

Every index string below was accepted by a live ClickHouse 26.3.42.3 server.
"""

from __future__ import annotations

import pytest

import dfe_schemas
from dfe_schemas.ddl_catalogue import DDLCatalogue
from dfe_schemas.loader import Column, SchemaError, TypeRegistry, load_columns
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer, split_use_case
from tests.conftest import schema_yaml

ROOT = dfe_schemas.schemas_root()

EXPECTED = {
    "dimension": ["INDEX `idx_c` `c` TYPE set(0) GRANULARITY 4"],
    "exact_match": ["INDEX `idx_c` `c` TYPE bloom_filter GRANULARITY 4"],
    "range": ["INDEX `idx_c` `c` TYPE minmax GRANULARITY 4"],
    "word_search": ["INDEX `idx_c` `c` TYPE text(tokenizer=splitByNonAlpha) GRANULARITY 1"],
    "substring_search": ["INDEX `idx_c` `c` TYPE text(tokenizer=ngrams(3)) GRANULARITY 1"],
    "key_search": [
        "INDEX `idx_c_key` mapKeys(`c`) TYPE text(tokenizer=array) GRANULARITY 1",
        "INDEX `idx_c_value` mapValues(`c`) TYPE text(tokenizer=array) GRANULARITY 1",
    ],
    "similarity_search(768)": [
        "INDEX `idx_c` `c` TYPE vector_similarity('hnsw', 'cosineDistance', 768) GRANULARITY 100000000"
    ],
}


@pytest.fixture(scope="module")
def renderer():
    return Renderer(load_manifest(root=ROOT))


@pytest.mark.parametrize(("use_case", "expected"), sorted(EXPECTED.items()))
def test_a_use_case_renders_the_index_that_answers_it(renderer, use_case, expected):
    column = Column(name="c", use_case=use_case, attribute=("not_null",))
    assert renderer.tables.index_defs(column) == expected


def test_the_catalogue_answers_exactly_the_registry_vocabulary():
    """A name in one file and not the other is either an unindexed column or a dead entry."""
    assert DDLCatalogue.load(root=ROOT).use_cases == sorted(TypeRegistry.load(root=ROOT).use_cases)


def test_every_registry_use_case_renders_something(renderer):
    """A name the registry accepts and no catalogue entry answers gets no index at all."""
    for use_case in TypeRegistry.load(root=ROOT).use_cases:
        declared = "similarity_search(768)" if use_case == "similarity_search" else use_case
        column = Column(name="c", use_case=declared)
        assert renderer.tables.index_defs(column), f"{use_case} renders no index"


_SET = "INDEX `idx_c` `c` TYPE set(0) GRANULARITY 4"
_BLOOM = "INDEX `idx_c` `c` TYPE bloom_filter GRANULARITY 4"


@pytest.mark.parametrize(
    ("use_case", "cardinality", "attribute", "expected"),
    [
        ("exact_match", "low", ("not_null",), _SET),
        ("exact_match", "low", (), _BLOOM),
        ("exact_match", "high", ("not_null",), _BLOOM),
        ("exact_match", "unknown", (), _BLOOM),
        ("dimension", "low", ("not_null",), _SET),
        ("dimension", "low", (), _BLOOM),
        ("dimension", "unknown", (), _BLOOM),
    ],
)
def test_set_0_answers_only_a_bounded_column_that_is_not_nullable(
    renderer, use_case, cardinality, attribute, expected
):
    """set(0) is exact on a bounded column, but on 26.3 it prunes nothing on a Nullable one.

    LowCardinality(Nullable(String)) kept 977 of 977 granules under set(0) and
    4 under bloom_filter, so a Nullable column takes the bloom filter.
    """
    column = Column(name="c", use_case=use_case, cardinality=cardinality, attribute=attribute)
    assert renderer.tables.index_defs(column) == [expected]


@pytest.mark.parametrize("primitive", ["string", "uuid", "integer", "ip"])
def test_exact_match_answers_ids_numbers_and_addresses(primitive):
    """An IP or a numeric id is looked up one value at a time, like a string id."""
    registry = TypeRegistry.load(root=ROOT)
    assert primitive in registry.use_cases["exact_match"]["valid_primitives"]


def test_an_unknown_use_case_is_refused_with_the_column_named(renderer):
    with pytest.raises(SchemaError, match=r"column 'c': unknown index use case 'fulltext'"):
        renderer.tables.index_defs(Column(name="c", use_case="fulltext"))


_TABLE = (
    "table: {index_granularity: 2048, partition_column: t, partition_granularity: day,"
    " partition_functions: {day: toYYYYMMDD}}"
)
_ONE = "use_cases: {a: {indexes: [{type: minmax, granularity: 1}]}}"


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        pytest.param(f"{_TABLE}\n{_ONE}", "version", id="no_version"),
        pytest.param(f"version: 1\n{_ONE}", "table", id="no_table"),
        pytest.param(f"version: 1\n{_TABLE}\nuse_cases: {{}}", "use_cases", id="no_use_cases"),
        pytest.param(f"version: 1\n{_TABLE}\nuse_cases: {{a: {{}}}}", "indexes", id="no_indexes"),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: []}}}}", "non-empty", id="empty_list"
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{granularity: 1}}]}}}}",
            "type",
            id="no_type",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{type: minmax, granularity: 0}}]}}}}",
            "granularity",
            id="zero_granularity",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{type: minmax, granularity: 1, expression: lower(x)}}]}}}}",
            "expression",
            id="expression_without_col",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{type: minmax, granularity: 1, tokenizer: x}}]}}}}",
            "unknown keys",
            id="unknown_key",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{type: minmax, granularity: 1}}, {{type: minmax, granularity: 1}}]}}}}",
            "suffix",
            id="two_without_suffix",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: {{by_cardinality: {{low: [{{type: minmax, granularity: 1}}]}}}}}}}}",
            "exactly",
            id="cardinality_missing",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{type: minmax, granularity: 1}}], legacy: []}}}}",
            "nothing else",
            id="legacy_key",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{type: minmax, granularity: 1, when_nullable: {{type: bloom_filter}}}}]}}}}",
            "granularity",
            id="when_nullable_without_granularity",
        ),
        pytest.param(
            f"version: 1\n{_TABLE}\nuse_cases: {{a: {{indexes: [{{type: minmax, granularity: 1, when_nullable: {{type: bloom_filter, granularity: 4, suffix: x}}}}]}}}}",
            "unknown keys",
            id="when_nullable_with_a_suffix",
        ),
        pytest.param(
            f"version: 1\ntable: {{index_granularity: 2048}}\n{_ONE}",
            "exactly",
            id="table_incomplete",
        ),
        pytest.param(
            f"version: 1\ntable: {{index_granularity: 2048, partition_column: t, partition_granularity: hour, partition_functions: {{day: toYYYYMMDD}}}}\n{_ONE}",
            "hour",
            id="default_granularity_unmapped",
        ),
        pytest.param(
            f"version: 1\ntable: {{index_granularity: 2048, partition_column: t, partition_granularity: day, partition_functions: {{day: toYYYYMMDD(x)}}}}\n{_ONE}",
            "identifier",
            id="function_not_a_name",
        ),
    ],
)
def test_a_malformed_catalogue_is_refused(tmp_path, body, refusal):
    """Every table's defaults and indexes come from the catalogue, so a typo fails the load."""
    path = tmp_path / "ddl.yaml"
    path.write_text(body + "\n", encoding="utf-8")
    with pytest.raises(SchemaError, match=refusal):
        DDLCatalogue.from_file(path)


def test_the_shipped_table_defaults_partition_by_day_on_the_load_timestamp():
    table = DDLCatalogue.load(root=ROOT).table
    assert table.partition_expr(table.partition_column, table.partition_granularity) == (
        "toYYYYMMDD(_timestamp_load)"
    )
    assert table.partition_expr("x", "month") == "toYYYYMM(x)"
    with pytest.raises(SchemaError, match="unknown partition_granularity 'hour'"):
        table.partition_expr("x", "hour")


def test_similarity_search_without_a_dimension_count_is_refused(renderer):
    """ClickHouse cannot infer the dimension count, so neither can we."""
    with pytest.raises(SchemaError, match="dimension count"):
        renderer.tables.index_defs(Column(name="c", use_case="similarity_search"))


def test_a_column_with_no_use_case_gets_no_index(renderer):
    assert renderer.tables.index_defs(Column(name="c")) == []


@pytest.mark.parametrize("name", ["app-type", "a/b", "1st", "select"])
def test_an_index_name_survives_a_column_name_sql_would_choke_on(renderer, name):
    """The templates backticked the column and left the index name bare.

    `fortinet_firewall_app-type` parsed as `idx_fortinet_firewall_app` and then
    choked on the rest, so 22 of the fleet's tables would not create.
    """
    column = Column(name=name, use_case="dimension", attribute=("not_null",))
    assert renderer.tables.index_defs(column) == [
        f"INDEX `idx_{name}` `{name}` TYPE set(0) GRANULARITY 4"
    ]


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        (None, (None, None)),
        ("word_search", ("word_search", None)),
        ("similarity_search(768)", ("similarity_search", 768)),
    ],
)
def test_a_declared_use_case_splits_into_its_name_and_argument(declared, expected):
    assert split_use_case(declared) == expected


def test_a_malformed_use_case_is_refused():
    with pytest.raises(SchemaError, match="integer"):
        split_use_case("similarity_search(large)")


def test_no_schema_declares_a_retired_use_case():
    """The old vocabulary named the ClickHouse index, not the question."""
    retired = {"fulltext", "text_search", "bloom"}
    for path in schema_yaml(ROOT):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("use_case:"):
                continue
            value = stripped.split(":", 1)[1].strip()
            assert value not in retired, (
                f"{path.relative_to(ROOT)}:{number} declares the retired use case {value!r}"
            )


def test_the_ecs_meta_schema_indexes_nothing_and_keeps_its_mapping_comments():
    """A meta schema is the full column set. Each derived schema picks its own indexes."""
    columns = load_columns(ROOT / "meta" / "elastic" / "ecs.yaml")
    assert len(columns) > 1000
    assert [c.name for c in columns if c.use_case] == []
    assert all(c.comment and c.comment.startswith("Elasticsearch mapping type:") for c in columns)


@pytest.mark.parametrize(
    ("column", "refused"),
    [
        pytest.param(
            "- {name: _raw, type: text, use_case: substring_search}", False, id="use_case"
        ),
        pytest.param("- {name: _raw, type: text}", False, id="no_index"),
        pytest.param("- {name: _raw, type: text, index: exact_match}", True, id="index_word"),
        pytest.param(
            '- {name: _raw, type: text, use_case: substring_search, index: "text() GRANULARITY 64"}',
            True,
            id="index_beside_use_case",
        ),
    ],
)
def test_a_per_column_index_key_is_refused_by_name(tmp_path, column, refused):
    """Nothing reads `index:` on a column, so keeping it silently reads as config and behaves as a comment."""
    path = tmp_path / "header.yaml"
    path.write_text(
        'current: "1.0.0"\nversions:\n  "1.0.0":\n    columns:\n      ' + column + "\n",
        encoding="utf-8",
    )

    if refused:
        with pytest.raises(SchemaError, match=r"column '_raw'.*'index'"):
            load_columns(path)
    else:
        assert [c.name for c in load_columns(path)] == ["_raw"]


def test_the_common_header_raw_column_asks_for_the_substring_index(renderer):
    """The `_raw` column states its question, and the registry's template answers it."""
    raw = [c for c in load_columns(ROOT / "common-header" / "timeseries.yaml") if c.name == "_raw"]

    assert [c.use_case for c in raw] == ["substring_search"]
    assert renderer.tables.index_defs(raw[0]) == [
        "INDEX `idx__raw` `_raw` TYPE text(tokenizer=ngrams(3)) GRANULARITY 1"
    ]
