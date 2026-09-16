#  Project:      dfe-schemas
#  File:         tests/test_manifest.py
#  Purpose:      The apply manifest is the one list, and it checks itself
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The manifest's two failure modes are the ones worth pinning.

A duplicate id means whichever definition applies second silently wins. A
dependency declared LATER than its dependant is an apply that fails on a fresh
cluster and succeeds on every re-run afterwards, which is the worst shape a
bootstrap can have -- it only breaks for the customer standing one up.
"""

from __future__ import annotations

import shutil

import pytest

import dfe_schemas
from dfe_schemas.manifest import KINDS, ManifestError, load_manifest

ROOT = dfe_schemas.schemas_root()


@pytest.fixture
def manifest():
    return load_manifest(root=ROOT)


def _write(tmp_path, body: str):
    """A schemas root carrying *body* as its manifest and the real trees beside it."""
    for tree in ("tables", "hunts", "common-header", "registries", "views", "roles", "topics"):
        shutil.copytree(ROOT / tree, tmp_path / tree)
    (tmp_path / "manifest.yaml").write_text(body, encoding="utf-8")
    return tmp_path


_MINIMAL = """
manifest_version: 1
databases:
  - key: data
    parameter: data_database
objects:
  - id: db.data
    kind: database
    database: data
    topology: resolved
    additive: false
    depends_on: []
  - id: data.alert_state
    kind: table
    database: data
    defines: tables/internal/alert_state
    topology: resolved
    additive: true
    depends_on: [db.data]
"""


def test_the_shipped_manifest_loads(manifest):
    assert manifest.manifest_version == 1
    assert manifest.objects
    assert {obj.kind for obj in manifest.objects} <= set(KINDS)


def test_every_id_is_unique(manifest):
    ids = [obj.id for obj in manifest.objects]
    assert len(ids) == len(set(ids))


def test_every_dependency_is_declared_before_its_dependant(manifest):
    seen: set[str] = set()
    for obj in manifest.objects:
        for dependency in obj.depends_on:
            assert dependency in seen, (
                f"{obj.id} depends on {dependency}, declared later or not at all"
            )
        seen.add(obj.id)


def test_the_migration_ledger_comes_first(manifest):
    """The applier records every other object into it, its own row included."""
    tables = [obj.id for obj in manifest.by_kind("table")]
    assert tables[0] == "data.schema_migrations"


def test_the_landing_table_is_main(manifest):
    landing = next(obj for obj in manifest.objects if obj.id == "data.main")
    assert landing.defines == "tables/core/main"
    assert landing.header == "timeseries"


def test_the_data_database_comes_from_a_parameter(manifest):
    """A literal here would create objects in a database the deployment renamed."""
    assert manifest.database_name("data", data_database="other") == "other"
    assert manifest.database_name("meta", data_database="other") == "dfe_meta"


def test_an_unknown_database_key_is_an_error(manifest):
    with pytest.raises(ManifestError):
        manifest.database_name("nope", data_database="dfe")


def test_a_duplicate_id_is_refused(tmp_path):
    body = (
        _MINIMAL
        + """
  - id: data.alert_state
    kind: table
    database: data
    defines: tables/internal/hunt_state
    topology: resolved
    additive: true
    depends_on: [db.data]
"""
    )
    with pytest.raises(ManifestError, match="declared twice"):
        load_manifest(root=_write(tmp_path, body))


def test_a_forward_dependency_is_refused(tmp_path):
    body = _MINIMAL.replace("depends_on: [db.data]", "depends_on: [data.later]")
    with pytest.raises(ManifestError, match="not declared before it"):
        load_manifest(root=_write(tmp_path, body))


def test_a_definition_that_does_not_exist_is_refused(tmp_path):
    body = _MINIMAL.replace("tables/internal/alert_state", "tables/internal/nope")
    with pytest.raises(ManifestError, match="does not exist"):
        load_manifest(root=_write(tmp_path, body))


def test_an_undeclared_database_is_refused(tmp_path):
    body = _MINIMAL.replace("database: data\n    defines", "database: elsewhere\n    defines")
    with pytest.raises(ManifestError, match="is not declared"):
        load_manifest(root=_write(tmp_path, body))


def test_an_unsupported_manifest_version_is_refused(tmp_path):
    body = _MINIMAL.replace("manifest_version: 1", "manifest_version: 99")
    with pytest.raises(ManifestError, match="not supported"):
        load_manifest(root=_write(tmp_path, body))
