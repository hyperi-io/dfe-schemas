#  Project:   dfe-schemas
#  File:      tests/test_variants.py
#  Purpose:   The variant-block selector refuses rather than guesses
#  Language:  Python
#
#  License:   BUSL-1.1
#  Copyright: (c) 2026 HYPERI PTY LIMITED

"""A missing variant must fail the load, never read as an empty block."""

import pytest

from dfe_schemas.variants import VariantError, load_variant_blocks


def _write(tmp_path, text):
    path = tmp_path / "blocks.sql"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_block_is_selected_and_filled_in(tmp_path):
    blocks = load_variant_blocks(
        _write(
            tmp_path,
            "-- header comment\n\n"
            "-- @block create one\n"
            "-- a reason, dropped from the body\n"
            "CREATE X ${name} ON '{macro}'\n\n"
            "-- @block create two\n"
            "CREATE Y ${name}\n",
        )
    )
    assert blocks.select("create", "one", name="n") == "CREATE X n ON '{macro}'"
    assert blocks.select("create", "two", name="n") == "CREATE Y n"


def test_an_empty_variant_is_an_empty_body(tmp_path):
    blocks = load_variant_blocks(
        _write(tmp_path, "-- @block s one\na = 1\n-- @block s two\n-- nothing here\n")
    )
    assert blocks.select("s", "two") == ""


def test_a_block_missing_a_variant_fails_the_load(tmp_path):
    path = _write(tmp_path, "-- @block a one\nX\n-- @block a two\nY\n-- @block b one\nZ\n")
    with pytest.raises(VariantError, match="block 'b' has no two variant"):
        load_variant_blocks(path)


def test_a_repeated_block_and_variant_fails_the_load(tmp_path):
    path = _write(tmp_path, "-- @block a one\nX\n-- @block a one\nY\n")
    with pytest.raises(VariantError, match="appears twice"):
        load_variant_blocks(path)


def test_text_before_the_first_marker_fails_the_load(tmp_path):
    path = _write(tmp_path, "SELECT 1\n-- @block a one\nX\n")
    with pytest.raises(VariantError, match="before the first"):
        load_variant_blocks(path)


def test_a_file_with_no_markers_fails_the_load(tmp_path):
    with pytest.raises(VariantError, match="no '-- @block' markers"):
        load_variant_blocks(_write(tmp_path, "-- only a comment\n"))


def test_an_unreadable_file_fails_the_load(tmp_path):
    with pytest.raises(VariantError, match="could not read"):
        load_variant_blocks(tmp_path / "absent.sql")


def test_an_unknown_block_or_variant_is_refused(tmp_path):
    blocks = load_variant_blocks(_write(tmp_path, "-- @block a one\nX\n"))
    with pytest.raises(VariantError, match="no block 'b'"):
        blocks.select("b", "one")
    with pytest.raises(VariantError, match="no 'two' variant"):
        blocks.select("a", "two")


def test_an_unfilled_placeholder_is_refused(tmp_path):
    blocks = load_variant_blocks(_write(tmp_path, "-- @block a one\nCREATE ${name}\n"))
    with pytest.raises(VariantError, match="needs a value"):
        blocks.select("a", "one")
