#  Project:      dfe-schemas
#  File:         tests/test_package.py
#  Purpose:      The package resolves the schema trees in both consumption modes
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

import pytest

import dfe_schemas
from dfe_schemas.clickhouse import Platform
from dfe_schemas.deploy_defaults import (
    DEFAULT_PROFILE_SETTINGS,
    JSON_TABLE_SETTINGS,
    SERVER_CONFIG,
    json_table_settings,
)

WRITE_MARKS = "write_marks_for_substreams_in_compact_parts"


def test_schemas_root_finds_the_trees():
    root = dfe_schemas.schemas_root()
    for tree in (
        "tables",
        "hunts",
        "common-header",
        "compat-profiles",
        "meta",
        "views",
        "roles",
        "topics",
    ):
        assert (root / tree).is_dir(), f"{tree} missing under {root}"
    # The file name matches the table name: a file called default.yaml declaring
    # a table called main is how the engine ended up creating dfe.default.
    assert (root / "tables" / "core" / "main.yaml").is_file()


def test_the_manifest_ships_beside_the_trees():
    assert dfe_schemas.manifest_path().is_file()


def test_deploy_defaults_shape():
    assert DEFAULT_PROFILE_SETTINGS["async_insert"] == 1
    assert DEFAULT_PROFILE_SETTINGS["wait_for_async_insert"] == 1
    improvements = SERVER_CONFIG["access_control_improvements"]
    assert improvements["select_from_system_db_requires_grant"] is True


def test_the_json_merge_cap_is_a_positive_count():
    cap = JSON_TABLE_SETTINGS["merge_max_dynamic_subcolumns_in_wide_part"]
    assert isinstance(cap, int)
    assert cap > 0


@pytest.mark.parametrize(
    "column_types",
    [
        ["DateTime64(3)", "JSON"],
        ["Nullable(JSON(max_dynamic_paths=2048))"],
        ["String", "Array(JSON)"],
    ],
)
def test_a_table_with_a_json_column_takes_the_json_settings(column_types):
    assert json_table_settings(column_types, platform=Platform.CLOUD) == JSON_TABLE_SETTINGS
    assert json_table_settings(column_types, platform=Platform.SELF_HOSTED) == {
        **JSON_TABLE_SETTINGS,
        WRITE_MARKS: 0,
    }


@pytest.mark.parametrize(
    "column_types",
    [
        [],
        ["String", "Map(LowCardinality(String), String)"],
        # A label inside an enum literal is not a column type.
        ["Enum8('JSON' = 1, 'XML' = 2)"],
        ["LowCardinality(String)", "Dynamic"],
    ],
)
def test_a_table_without_a_json_column_takes_none(column_types):
    for platform in Platform:
        assert json_table_settings(column_types, platform=platform) == {}


def test_the_settings_handed_out_are_a_copy():
    """A caller adding to its table's settings must not change every other table's."""
    first = json_table_settings(["JSON"], platform=Platform.SELF_HOSTED)
    first["index_granularity"] = 1
    first[WRITE_MARKS] = 1
    again = json_table_settings(["JSON"], platform=Platform.SELF_HOSTED)
    assert "index_granularity" not in again
    assert again[WRITE_MARKS] == 0


def test_the_platform_sql_ships_inside_the_package():
    """Read by import path, so a DFE_SCHEMAS_DIR pointing at an older tree cannot lose it."""
    from importlib import resources

    assert (resources.files("dfe_schemas") / "sql" / "platform.sql").is_file()


def test_version_attribute_exists():
    assert isinstance(dfe_schemas.__version__, str)
