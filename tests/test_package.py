#  Project:      dfe-schemas
#  File:         tests/test_package.py
#  Purpose:      The package resolves the schema trees in both consumption modes
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

from __future__ import annotations

import dfe_schemas
from dfe_schemas.deploy_defaults import DEFAULT_PROFILE_SETTINGS, SERVER_CONFIG


def test_schemas_root_finds_the_trees():
    root = dfe_schemas.schemas_root()
    for tree in ("tables", "hunts", "common-header", "meta"):
        assert (root / tree).is_dir(), f"{tree} missing under {root}"
    assert (root / "tables" / "core" / "default.yaml").is_file()


def test_deploy_defaults_shape():
    assert DEFAULT_PROFILE_SETTINGS["async_insert"] == 1
    assert DEFAULT_PROFILE_SETTINGS["wait_for_async_insert"] == 1
    improvements = SERVER_CONFIG["access_control_improvements"]
    assert improvements["select_from_system_db_requires_grant"] is True


def test_version_attribute_exists():
    assert isinstance(dfe_schemas.__version__, str)
