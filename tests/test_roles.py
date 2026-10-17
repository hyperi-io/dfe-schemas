#  Project:      dfe-schemas
#  File:         tests/test_roles.py
#  Purpose:      Each app's ClickHouse service role holds what its app runs
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""What each app's own ClickHouse identity has to be able to do.

The apps connect as these roles rather than the admin account, so a grant
missing here is refused at runtime and a broad one is reach a stolen
credential keeps. ClickHouse runs a materialised view's SELECT as the user
whose INSERT fired it, so a writer of a view's source table needs SELECT on
that table or the insert fails.
"""

import re

import pytest

import dfe_schemas
from dfe_schemas.loader import load_version_entry
from dfe_schemas.manifest import load_manifest

ROOT = dfe_schemas.schemas_root()
DB = "{db}"
# The collector's ClickHouse health scrape reads these, and nothing else in system.
COLLECTOR_SYSTEM_READS = {"metrics", "events", "parts"}
# The loader's schema fetch reads these.
LOADER_SYSTEM_READS = {"columns", "tables"}
VIEW_SOURCE = re.compile(r"\bFROM\s+\{db\}\.(\w+)", re.IGNORECASE)


@pytest.fixture(scope="module")
def catalogue():
    return load_version_entry(ROOT / "roles" / "clickhouse.yaml", require_columns=False)


@pytest.fixture(scope="module")
def roles(catalogue):
    return {entry["name"]: entry for entry in catalogue["service_roles"]}


def _grants(role: dict, privilege: str) -> set[str]:
    """The targets a role holds ``privilege`` on, as written in the catalogue."""
    prefix = f"{privilege} ON "
    return {g.removeprefix(prefix) for g in role.get("grants") or [] if g.startswith(prefix)}


def _view_sources() -> set[str]:
    """Every table in the data database a materialised view reads from."""
    sources = set()
    for path in sorted((ROOT / "tables").rglob("*.yaml")):
        view = load_version_entry(path, require_columns=False).get("materialized_view") or {}
        sources |= set(VIEW_SOURCE.findall(view.get("select", "")))
    return sources


def test_the_loader_reads_the_two_system_tables_it_queries(roles):
    loader = roles["loader"]
    assert _grants(loader, "SELECT") == {f"system.{t}" for t in LOADER_SYSTEM_READS}
    assert _grants(loader, "INSERT") == {f"{DB}.*"}


def test_the_otel_writer_is_a_minted_user(roles):
    assert roles["otel_writer"]["mint_user"] is True


def test_the_otel_writer_inserts_into_the_otel_tables_alone(roles):
    assert _grants(roles["otel_writer"], "INSERT") == {f"{DB}.otel_*"}


def test_the_otel_writer_reads_every_view_source_it_writes(roles):
    written = {s for s in _view_sources() if s.startswith("otel_")}
    assert written, "no materialised view reads an otel table, so this check proves nothing"
    reads = _grants(roles["otel_writer"], "SELECT")
    assert {f"{DB}.{s}" for s in written} <= reads


def test_the_otel_writer_reads_the_system_tables_the_collector_scrapes(roles):
    reads = _grants(roles["otel_writer"], "SELECT")
    assert {r.removeprefix("system.") for r in reads if r.startswith("system.")} == (
        COLLECTOR_SYSTEM_READS
    )


def test_the_otel_writer_sees_every_table_the_scrape_reports_on(roles):
    """system.parts lists only the tables a user can see, so the scrape needs SHOW TABLES."""
    assert _grants(roles["otel_writer"], "SHOW TABLES") == {f"{DB}.*", "system.*"}


def test_the_otel_writer_reads_no_data_table_it_does_not_feed_a_view_from(roles):
    reads = _grants(roles["otel_writer"], "SELECT")
    allowed = {f"{DB}.{s}" for s in _view_sources()} | {
        f"system.{t}" for t in COLLECTOR_SYSTEM_READS
    }
    assert reads <= allowed


def test_every_minted_service_role_is_a_manifest_object(roles):
    declared = {
        obj.key
        for obj in load_manifest(root=ROOT).objects
        if obj.kind == "role" and obj.section == "service_roles"
    }
    assert {name for name, entry in roles.items() if entry.get("mint_user")} <= declared
