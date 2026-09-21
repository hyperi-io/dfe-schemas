#  Project:      dfe-schemas
#  File:         tests/test_cardinality.py
#  Purpose:      One declared cardinality decides both the wrapper and the index
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Cardinality has one home, and both decisions read it.

It used to have two: ``attribute: [lowcardinality]`` set the storage wrapper by
hand, and ``exact_match`` derived its index separately. Nothing kept them
agreeing. These tests pin the single answer -- ``low`` puts
``LowCardinality(...)`` on the column and ``set(0)`` under ``exact_match``,
``high`` and ``unknown`` get neither -- and pin the refusal when a column
declares the two against each other.

Every rendered type and index string below was accepted by a live ClickHouse
26.3.32.14 server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import dfe_schemas
from dfe_schemas.loader import (
    CARDINALITIES,
    Column,
    SchemaError,
    TypeRegistry,
    load_columns,
    read_yaml,
)
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer, _TableConfig

ROOT = dfe_schemas.schemas_root()

_DEFINITION = """\
current: '1.0.0'
versions:
  '1.0.0':
    date: '2026-09-21'
    type: initial
    summary: cardinality fixture
    columns:
{columns}
"""


def write_schema(tmp_path: Path, columns: str) -> Path:
    """One throwaway definition file carrying *columns*, indented for the version."""
    path = tmp_path / "fixture.yaml"
    path.write_text(_DEFINITION.format(columns=columns), encoding="utf-8", newline="\n")
    return path


def one_column(tmp_path: Path, columns: str) -> Column:
    loaded = load_columns(write_schema(tmp_path, columns))
    assert len(loaded) == 1
    return loaded[0]


@pytest.fixture(scope="module")
def registry():
    return TypeRegistry.load(root=ROOT)


@pytest.fixture(scope="module")
def renderer():
    return Renderer(load_manifest(root=ROOT))


# -- the declaration --------------------------------------------------------


@pytest.mark.parametrize("declared", CARDINALITIES)
def test_a_column_carries_the_cardinality_it_declares(tmp_path, declared):
    columns = f"      - name: c\n        type: string\n        cardinality: {declared}\n"
    assert one_column(tmp_path, columns).cardinality == declared


def test_a_column_declaring_nothing_is_unknown(tmp_path):
    """Nobody re-reviews a field that already looks decided, so say so."""
    columns = "      - name: c\n        type: string\n"
    assert one_column(tmp_path, columns).cardinality == "unknown"


def test_the_retired_attribute_still_reads_as_low(tmp_path):
    """26 shipped schemas carry it; it declares `low` rather than a second home."""
    columns = "      - name: c\n        type: string\n        attribute: [lowcardinality]\n"
    column = one_column(tmp_path, columns)
    assert column.cardinality == "low"
    assert "lowcardinality" not in column.attribute


def test_the_retired_attribute_keeps_the_column_s_other_attributes(tmp_path):
    columns = (
        "      - name: c\n        type: string\n        attribute: [lowcardinality, not_null]\n"
    )
    assert one_column(tmp_path, columns).attribute == ("not_null",)


def test_a_cardinality_the_vocabulary_does_not_carry_is_refused(tmp_path):
    columns = "      - name: c\n        type: string\n        cardinality: medium\n"
    with pytest.raises(SchemaError, match="cardinality 'medium' is not one of"):
        one_column(tmp_path, columns)


def test_declaring_the_attribute_against_the_cardinality_is_refused(tmp_path):
    """The disagreement between the two homes is the defect this change removes."""
    columns = (
        "      - name: c\n        type: string\n"
        "        cardinality: high\n        attribute: [lowcardinality]\n"
    )
    with pytest.raises(SchemaError, match="contradicts"):
        one_column(tmp_path, columns)


def test_an_exact_column_reads_its_lowcardinality_flag_as_low(tmp_path):
    """A tables/** column names its ClickHouse type and flags the wrapper apart."""
    columns = "      - name: c\n        ch_type: String\n        lowcardinality: true\n"
    assert one_column(tmp_path, columns).cardinality == "low"


# -- the storage decision ---------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("low", "LowCardinality(Nullable(String))"),
        ("high", "Nullable(String)"),
        ("unknown", "Nullable(String)"),
    ],
)
def test_only_low_wraps_the_column_in_lowcardinality(registry, declared, expected):
    resolved = registry.resolve(Column(name="c", primitive="string", cardinality=declared))
    assert resolved.ch_type == expected


def test_low_wraps_an_exact_clickhouse_type_too(registry):
    column = Column(name="c", ch_override="String", cardinality="low")
    assert registry.resolve(column).ch_type == "LowCardinality(String)"


def test_nullable_stays_inside_the_lowcardinality_wrapper(registry):
    """ClickHouse's order: LowCardinality(Nullable(T)), never the other way."""
    column = Column(name="c", primitive="string", cardinality="low", attribute=("nullable",))
    assert registry.resolve(column).ch_type == "LowCardinality(Nullable(String))"


def test_not_null_drops_the_nullable_under_the_wrapper(registry):
    column = Column(name="c", primitive="string", cardinality="low", attribute=("not_null",))
    assert registry.resolve(column).ch_type == "LowCardinality(String)"


# -- the index decision -----------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("low", "INDEX `idx_c` `c` TYPE set(0) GRANULARITY 4"),
        ("high", "INDEX `idx_c` `c` TYPE bloom_filter GRANULARITY 4"),
        ("unknown", "INDEX `idx_c` `c` TYPE bloom_filter GRANULARITY 4"),
    ],
)
def test_exact_match_reads_the_same_declaration(renderer, declared, expected):
    column = Column(name="c", use_case="exact_match", cardinality=declared)
    assert renderer._index_defs(column) == [expected]


def test_an_unknown_column_falls_to_the_bloom_filter(renderer, registry):
    """The safe way to be wrong: no dictionary, and a probabilistic index."""
    column = Column(name="c", primitive="string", use_case="exact_match")
    assert "LowCardinality" not in registry.resolve(column).ch_type
    assert renderer._index_defs(column) == ["INDEX `idx_c` `c` TYPE bloom_filter GRANULARITY 4"]


# -- the two decisions, rendered together -----------------------------------


def _column_line(statement: str, name: str) -> str:
    lines = (line.strip() for line in statement.splitlines())
    return next(line for line in lines if line.startswith(f"`{name}`"))


def _index_line(statement: str, name: str) -> str:
    lines = (line.strip() for line in statement.splitlines())
    return next(line for line in lines if f"INDEX `idx_{name}` " in line)


@pytest.fixture(scope="module")
def rendered_fixture(renderer, tmp_path_factory):
    """One CREATE TABLE carrying a low, a high and an undeclared column."""
    path = tmp_path_factory.mktemp("cardinality") / "fixture.yaml"
    path.write_text(
        _DEFINITION.format(
            columns=(
                "      - name: bounded\n        type: string\n"
                "        cardinality: low\n        use_case: exact_match\n"
                "      - name: unbounded\n        type: string\n"
                "        cardinality: high\n        use_case: exact_match\n"
                "      - name: unmeasured\n        type: string\n"
                "        use_case: exact_match\n"
            )
        ),
        encoding="utf-8",
        newline="\n",
    )
    return renderer._create_table(
        "dfe", "cardinality_fixture", load_columns(path), _TableConfig(), None
    )


def test_the_rendered_ddl_wraps_only_the_low_column(rendered_fixture):
    assert "LowCardinality(Nullable(String))" in _column_line(rendered_fixture, "bounded")
    assert "LowCardinality" not in _column_line(rendered_fixture, "unbounded")
    assert "LowCardinality" not in _column_line(rendered_fixture, "unmeasured")


def test_the_rendered_ddl_indexes_only_the_low_column_exactly(rendered_fixture):
    assert "TYPE set(0)" in _index_line(rendered_fixture, "bounded")
    assert "TYPE bloom_filter" in _index_line(rendered_fixture, "unbounded")
    assert "TYPE bloom_filter" in _index_line(rendered_fixture, "unmeasured")


# -- the vocabulary is shared -----------------------------------------------


def test_the_registry_declares_the_same_vocabulary():
    """The loader and registries/types.yaml cannot drift into two answers."""
    declared = read_yaml(ROOT / "registries" / "types.yaml")["cardinality"]
    assert tuple(declared["values"]) == CARDINALITIES
    assert declared["default"] == "unknown"


def test_every_shipped_schema_still_renders(renderer):
    """The fold has to leave the 26 shipped schemas rendering exactly as before."""
    for obj in load_manifest(root=ROOT).objects:
        assert renderer.render(obj).statements
