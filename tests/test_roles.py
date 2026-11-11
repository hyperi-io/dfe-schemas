#  Project:      dfe-schemas
#  File:         tests/test_roles.py
#  Purpose:      Each ClickHouse role holds what its app runs, and its caps hold
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
from dfe_schemas.clickhouse import Platform
from dfe_schemas.loader import SchemaError, load_version_entry
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer

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


def _profiles(catalogue) -> dict[str, dict]:
    """Every tier's and service role's settings, by name."""
    entries = [*catalogue["tiers"], *catalogue["service_roles"]]
    return {entry["name"]: entry["settings"] for entry in entries if entry.get("settings")}


def _capped(catalogue, cap: str) -> dict[str, dict]:
    return {name: s[cap] for name, s in _profiles(catalogue).items() if cap in s}


def test_every_memory_cap_is_a_constraint_at_its_value(catalogue):
    """readonly=2 lets a query set any plain setting, so a plain cap lifts to 0."""
    capped = _capped(catalogue, "max_memory_usage")
    assert set(capped) == {
        "analyst_tier_1",
        "analyst_tier_2",
        "analyst_tier_3",
        "hunt_tier_1",
        "hunt_tier_2",
        "hunt_tier_3",
        "query_reader",
    }
    for name, cap in capped.items():
        assert cap == {"value": cap["value"], "min": 1, "max": cap["value"]}, name


def test_every_time_cap_is_a_constraint_that_admits_the_hyperdx_timeout(catalogue):
    """HyperDX sends max_execution_time=60 on every query unless its team sets one."""
    capped = _capped(catalogue, "max_execution_time")
    assert len(capped) == 7
    for name, cap in capped.items():
        assert cap == {"value": cap["value"], "min": 1, "max": max(cap["value"], 60)}, name


@pytest.mark.parametrize("setting", ["max_rows_to_read", "readonly"])
def test_rows_and_readonly_stay_plain_values(catalogue, setting):
    """HyperDX's key queries send max_rows_to_read=0, and readonly refuses a change on its own."""
    for name, value in _capped(catalogue, setting).items():
        assert isinstance(value, int), name


@pytest.mark.parametrize(
    ("settings", "rendered"),
    [
        pytest.param({"readonly": 2}, "readonly = 2", id="plain"),
        pytest.param(
            {"max_execution_time": {"value": 30, "min": 1, "max": 60}},
            "max_execution_time = 30 MIN 1 MAX 60",
            id="min-max",
        ),
        pytest.param(
            {"max_memory_usage": {"value": 5, "max": 5}}, "max_memory_usage = 5 MAX 5", id="max"
        ),
        pytest.param({"allow_ddl": {"value": 0, "const": True}}, "allow_ddl = 0 CONST", id="const"),
        pytest.param(
            {"b": 1, "a": {"value": 2, "min": 2}}, "a = 2 MIN 2, b = 1", id="sorted-mixed"
        ),
    ],
)
def test_a_profile_setting_renders_its_constraint(settings, rendered):
    assert Renderer.profile_settings(settings) == rendered


@pytest.mark.parametrize(
    ("setting", "problem"),
    [
        pytest.param({"min": 1, "max": 2}, "needs a value", id="no-value"),
        pytest.param({"value": 1, "ceiling": 2}, "unknown keys", id="unknown-key"),
        pytest.param({"value": 1, "max": "2"}, "must be a number", id="string-bound"),
        pytest.param({"value": True}, "must be a number", id="bool-value"),
        pytest.param({"value": 1, "const": "yes"}, "true or false", id="const-not-bool"),
        pytest.param({"value": 1, "max": 1, "const": True}, "no min or max", id="const-and-max"),
        pytest.param({"value": 0, "min": 1, "max": 60}, "outside", id="below-min"),
        pytest.param({"value": 90, "min": 1, "max": 60}, "outside", id="above-max"),
    ],
)
def test_a_malformed_constraint_is_refused(setting, problem):
    with pytest.raises(SchemaError, match=problem):
        Renderer.profile_settings({"max_execution_time": setting})


def test_the_rendered_reader_profile_carries_its_constraints():
    manifest = load_manifest(root=ROOT)
    reader = next(obj for obj in manifest.objects if obj.id == "role.query_reader")
    statements = Renderer(manifest, platform=Platform.SELF_HOSTED).render(reader).statements
    (profile,) = [s for s in statements if s.startswith("ALTER SETTINGS PROFILE")]
    assert profile == (
        "ALTER SETTINGS PROFILE `dfe_query_reader_profile` SETTINGS allow_ddl = 0, "
        "max_execution_time = 30 MIN 1 MAX 60, "
        "max_memory_usage = 2147483648 MIN 1 MAX 2147483648, "
        "max_rows_to_read = 10000000, readonly = 2"
    )
