#  Project:      dfe-schemas
#  File:         tests/test_index_use_cases.py
#  Purpose:      A use case names a question, and renders the index that answers it
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A column declares the question it is asked, never the ClickHouse index.

The vocabulary and the templates are two halves of one contract: a use case the
registry accepts but no template renders is a column that silently gets no
index, and a template keyed on a name the registry rejects can never fire. Both
directions are pinned here.

Every index string below was accepted by a live ClickHouse 26.3.32.14 server.
"""

from __future__ import annotations

import pytest

import dfe_schemas
from dfe_schemas.loader import Column, SchemaError, TypeRegistry
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer, split_use_case
from tests.conftest import schema_yaml

ROOT = dfe_schemas.schemas_root()

EXPECTED = {
    "dimension": ["INDEX idx_c `c` TYPE set(0) GRANULARITY 4"],
    "exact_match": ["INDEX idx_c `c` TYPE bloom_filter GRANULARITY 4"],
    "range": ["INDEX idx_c `c` TYPE minmax GRANULARITY 4"],
    "word_search": ["INDEX idx_c `c` TYPE text(tokenizer=splitByNonAlpha) GRANULARITY 1"],
    "substring_search": ["INDEX idx_c `c` TYPE text(tokenizer=ngrams(3)) GRANULARITY 1"],
    "key_search": [
        "INDEX idx_c_key mapKeys(`c`) TYPE text(tokenizer=array) GRANULARITY 1",
        "INDEX idx_c_value mapValues(`c`) TYPE text(tokenizer=array) GRANULARITY 1",
    ],
    "similarity_search(768)": [
        "INDEX idx_c `c` TYPE vector_similarity('hnsw', 'cosineDistance', 768) GRANULARITY 1"
    ],
}


@pytest.fixture(scope="module")
def renderer():
    return Renderer(load_manifest(root=ROOT))


@pytest.mark.parametrize(("use_case", "expected"), sorted(EXPECTED.items()))
def test_a_use_case_renders_the_index_that_answers_it(renderer, use_case, expected):
    column = Column(name="c", use_case=use_case)
    assert renderer._index_defs(column) == expected


def test_every_registry_use_case_renders_something(renderer):
    """A name the registry accepts and no template answers gets no index at all."""
    for use_case in TypeRegistry.load(root=ROOT).use_cases:
        declared = "similarity_search(768)" if use_case == "similarity_search" else use_case
        column = Column(name="c", use_case=declared)
        assert renderer._index_defs(column), f"{use_case} renders no index"


def test_exact_match_on_a_low_cardinality_column_holds_every_value(renderer):
    """set(0) is exact where the column is bounded; the bloom filter is not."""
    column = Column(name="c", use_case="exact_match", attribute=("lowcardinality",))
    assert renderer._index_defs(column) == ["INDEX idx_c `c` TYPE set(0) GRANULARITY 4"]


def test_similarity_search_without_a_dimension_count_is_refused(renderer):
    """ClickHouse cannot infer the dimension count, so neither can we."""
    with pytest.raises(SchemaError, match="dimension count"):
        renderer._index_defs(Column(name="c", use_case="similarity_search"))


def test_a_column_with_no_use_case_gets_no_index(renderer):
    assert renderer._index_defs(Column(name="c")) == []


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
