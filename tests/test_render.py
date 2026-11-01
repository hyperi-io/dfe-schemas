#  Project:      dfe-schemas
#  File:         tests/test_render.py
#  Purpose:      One DDL shape on every platform, and a platform-blind checksum
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""What breaks silently is the pair these tests pin.

A plain engine is accepted on a cluster and then splits data across replicas:
every query succeeds and the answer depends which node answered. So every table
renders the Replicated form with no ON CLUSTER, and only the CREATE DATABASE and
a JSON table's platform-only settings follow the platform.

The checksum has to do the opposite. If the platform token reached it, a server
swap between Cloud and self-hosted would read as drift on every object, and the
migration ledger would say the schema changed on a deploy that changed nothing.
"""

import shutil

import pytest

import dfe_schemas
from dfe_schemas.clickhouse import Platform
from dfe_schemas.deploy_defaults import JSON_TABLE_SETTINGS
from dfe_schemas.loader import Column, SchemaError
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import (
    Renderer,
    checksum,
    ddl_violations,
    one_ddl_violations,
    qualified_name,
    referenced_objects,
)

ROOT = dfe_schemas.schemas_root()
PLATFORMS = tuple(Platform)

# The manifest tables that carry a JSON column; every other table carries none.
JSON_TABLES = {"data.main", "data.detection", "data.otel_logs"}
# A Merge table stores no rows, so it takes no MergeTree SETTINGS at all.
DATALESS_TABLES = {"data.usage_events"}
MERGE_CAP = "merge_max_dynamic_subcolumns_in_wide_part"
WRITE_MARKS = "write_marks_for_substreams_in_compact_parts"


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(root=ROOT)


def _render(manifest, platform, **kwargs):
    return {
        obj.id: Renderer(manifest, platform=platform, **kwargs).render(obj)
        for obj in manifest.objects
    }


@pytest.fixture(scope="module")
def rendered(manifest):
    return {platform: _render(manifest, platform) for platform in PLATFORMS}


def test_every_object_renders_for_every_platform(rendered, manifest):
    for platform in PLATFORMS:
        assert len(rendered[platform]) == len(manifest.objects)
        for obj in rendered[platform].values():
            assert obj.statements, f"{obj.id} rendered nothing"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_table_is_replicated_and_no_table_ddl_fans_out(rendered, platform):
    """The CI gate on the one DDL shape, over every object the manifest renders."""
    tables = [obj for obj in rendered[platform].values() if obj.kind == "table"]
    assert len(tables) >= 20
    problems = [
        f"{obj.id}: {problem}"
        for obj in rendered[platform].values()
        for problem in one_ddl_violations(obj)
    ]
    assert problems == []


def test_a_table_renders_the_argumentless_replicated_engine(rendered):
    for platform in PLATFORMS:
        sql = rendered[platform]["data.main"].statements[0]
        assert "ENGINE = ReplicatedMergeTree\n" in sql
        assert "ON CLUSTER" not in sql


def test_a_self_hosted_database_is_replicated_across_the_cluster(rendered):
    sql = rendered[Platform.SELF_HOSTED]["db.data"].statements[0]
    assert sql == (
        "CREATE DATABASE IF NOT EXISTS dfe ON CLUSTER '{cluster}'\n"
        "ENGINE = Replicated('/clickhouse/databases/dfe', '{shard}', '{replica}')"
    )


def test_the_meta_database_is_replicated_too(rendered):
    sql = rendered[Platform.SELF_HOSTED]["db.meta"].statements[0]
    assert "ENGINE = Replicated('/clickhouse/databases/dfe_meta'" in sql


def test_a_cloud_database_carries_no_engine_and_no_on_cluster(rendered):
    assert rendered[Platform.CLOUD]["db.data"].statements[0] == "CREATE DATABASE IF NOT EXISTS dfe"


def test_a_view_carries_no_on_cluster(rendered):
    sql = rendered[Platform.SELF_HOSTED]["view.dfe_v_system_health"].statements[0]
    assert sql.startswith("CREATE OR REPLACE VIEW dfe.dfe_v_system_health AS")


def test_a_materialised_view_carries_no_on_cluster(rendered):
    sql = rendered[Platform.SELF_HOSTED]["data.otel_traces_trace_id_ts_mv"].statements[0]
    assert "ON CLUSTER" not in sql
    assert " TO dfe." in sql


@pytest.mark.parametrize("view", ["view.dfe_v_system_health", "view.dfe_v_system_table_sizes"])
def test_the_system_views_measure_the_data_database_not_an_org(rendered, view):
    """An org id is a tenant id, never a database name."""
    sql = rendered[Platform.SELF_HOSTED][view].statements[0]
    assert "database = 'dfe'" in sql
    assert "org_id" not in sql


def test_engine_parameters_survive_replication(rendered):
    """Dropping a version column changes merge behaviour, silently."""
    sql = rendered[Platform.CLOUD]["data.alert_state"].statements[0]
    assert "ENGINE = ReplicatedReplacingMergeTree(last_fired_at)" in sql


def test_the_checksum_is_the_same_on_every_platform(rendered, manifest):
    for obj in manifest.objects:
        seen = {rendered[platform][obj.id].checksum for platform in PLATFORMS}
        assert len(seen) == 1, f"{obj.id} checksums differently per platform"


def test_the_checksum_matches_the_plain_engine_form():
    """A ledger row recorded for MergeTree() still reads as unchanged."""
    replicated = checksum(["CREATE TABLE a (x String)\nENGINE = ReplicatedMergeTree\nORDER BY x"])
    plain = checksum(["CREATE TABLE a (x String)\nENGINE = MergeTree()\nORDER BY x"])
    assert replicated == plain


@pytest.mark.parametrize(
    ("statement", "problem"),
    [
        ("CREATE TABLE d.t (x UInt8)\nENGINE = MergeTree()\nORDER BY x", "is not Replicated*"),
        ("CREATE TABLE d.t ON CLUSTER c (x UInt8)\nENGINE = ReplicatedMergeTree", "ON CLUSTER"),
        ("CREATE TABLE d.t (x UInt8)", "declares no ENGINE"),
        ("ALTER TABLE d.t ON CLUSTER '{cluster}' ADD COLUMN y String", "ON CLUSTER"),
        ("CREATE OR REPLACE VIEW d.v ON CLUSTER c AS SELECT 1", "ON CLUSTER"),
        ("CREATE DATABASE IF NOT EXISTS d ON CLUSTER c", "ON CLUSTER"),
        (
            "CREATE MATERIALIZED VIEW d.mv ENGINE = SummingMergeTree ORDER BY x AS SELECT 1",
            "inner engine SummingMergeTree",
        ),
        (
            "CREATE TABLE d.t (x UInt8)\n"
            "ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/t', '{replica}')\nORDER BY x",
            "names its own Keeper path",
        ),
        (
            "CREATE TABLE d.t (x UInt8, v UInt8)\n"
            "ENGINE = ReplicatedReplacingMergeTree( '/zk/t' , 'r1', v)\nORDER BY x",
            "names its own Keeper path",
        ),
        (
            "CREATE MATERIALIZED VIEW d.mv ENGINE = ReplicatedSummingMergeTree('/zk/mv', '{replica}') "
            "ORDER BY x AS SELECT 1",
            "names its own Keeper path",
        ),
    ],
)
def test_the_gate_names_every_way_off_the_one_shape(statement, problem):
    found = ddl_violations(statement)
    assert len(found) == 1
    assert problem in found[0]


@pytest.mark.parametrize(
    "statement",
    [
        "-- a header\nCREATE TABLE d.t (x UInt8 COMMENT 'ENGINE = MergeTree')\n"
        "ENGINE = ReplicatedMergeTree\nORDER BY x",
        "CREATE DATABASE IF NOT EXISTS d ON CLUSTER '{cluster}'\n"
        "ENGINE = Replicated('/clickhouse/databases/d', '{shard}', '{replica}')",
        "CREATE MATERIALIZED VIEW IF NOT EXISTS d.mv TO d.t AS\nSELECT 1",
        "GRANT SELECT ON d.v TO r",
        # One literal is a Graphite config section, not a Keeper path.
        "CREATE TABLE d.t (x UInt8)\nENGINE = ReplicatedGraphiteMergeTree('graphite_rollup')\n"
        "ORDER BY x",
        "CREATE TABLE d.t (x UInt8, v UInt8)\nENGINE = ReplicatedReplacingMergeTree(v)\nORDER BY x",
        # A Merge table stores no rows, so no replica can hold data another lacks.
        "CREATE TABLE IF NOT EXISTS d.t (x UInt8)\nENGINE = Merge(`d`, '^all_sources$')",
        "CREATE MATERIALIZED VIEW IF NOT EXISTS d.mv REFRESH EVERY 5 MINUTE APPEND TO d.t AS\n"
        "SELECT 1",
    ],
)
def test_the_gate_passes_the_one_shape(statement):
    assert ddl_violations(statement) == []


def test_the_checksum_moves_when_the_schema_does():
    one = checksum(["CREATE TABLE a (x String) ENGINE = MergeTree()"])
    two = checksum(["CREATE TABLE a (x Int64) ENGINE = MergeTree()"])
    assert one != two


def test_the_checksum_ignores_whitespace():
    assert checksum(["CREATE  TABLE a\n(x String)"]) == checksum(["CREATE TABLE a (x String)"])


@pytest.mark.parametrize(
    ("one", "two"),
    [
        pytest.param("WHERE x = 'a  b'", "WHERE x = 'a b'", id="string_literal"),
        pytest.param("WHERE x = 'a\nb'", "WHERE x = 'a b'", id="newline_in_literal"),
        pytest.param("SELECT 1 AS `a  b`", "SELECT 1 AS `a b`", id="backtick_identifier"),
        pytest.param('SELECT 1 AS "a  b"', 'SELECT 1 AS "a b"', id="quoted_identifier"),
        pytest.param("WHERE x = 'it''s  a'", "WHERE x = 'it''s a'", id="doubled_quote"),
        pytest.param(r"WHERE x = 'it\'s  a'", r"WHERE x = 'it\'s a'", id="escaped_quote"),
    ],
)
def test_the_checksum_moves_when_whitespace_inside_quotes_does(one, two):
    assert checksum([one]) != checksum([two])


@pytest.mark.parametrize(
    ("one", "two"),
    [
        pytest.param("SELECT 'a'  ,\n'b'", "SELECT 'a' , 'b'", id="between_literals"),
        pytest.param(
            "SELECT 1 -- the view's  window\nFROM t",
            "SELECT 1 -- the view's window FROM t",
            id="apostrophe_in_a_line_comment",
        ),
        pytest.param("SELECT /* it's */  1", "SELECT /* it's */ 1", id="block_comment"),
    ],
)
def test_whitespace_outside_quotes_still_collapses(one, two):
    assert checksum([one]) == checksum([two])


# Each rendered literal was applied to ClickHouse 26.3.42.3 and read back from
# system.columns equal to the comment. The trailing and quote rows were refused
# with code 62, and the escape row stored a tab, before backslashes were doubled.
@pytest.mark.parametrize(
    ("comment", "literal"),
    [
        pytest.param("tenant's id", r"'tenant\'s id'", id="quote"),
        pytest.param("path C:\\temp\\", r"'path C:\\temp\\'", id="trailing_backslash"),
        pytest.param("regex \\d+ and \\t", r"'regex \\d+ and \\t'", id="escape_sequence"),
        pytest.param("it's a \\' quote", r"'it\'s a \\\' quote'", id="escaped_quote"),
    ],
)
def test_a_comment_reaches_clickhouse_as_written(manifest, comment, literal):
    column = Column(name="c", ch_override="String", comment=comment)
    assert Renderer(manifest)._column_def(column) == f"`c` String COMMENT {literal}"


def test_the_landing_table_is_named_main(rendered):
    assert rendered[Platform.SELF_HOSTED]["data.main"].name == "main"
    assert "`dfe`.`main`" in rendered[Platform.SELF_HOSTED]["data.main"].statements[0]


def test_the_landing_table_takes_the_deployment_retention(manifest):
    """It declares ttl_columns and no ttl_days, so the deployment default applies."""
    sql = (
        Renderer(manifest, default_ttl_days=45)
        .render(next(obj for obj in manifest.objects if obj.id == "data.main"))
        .statements[0]
    )
    assert "TTL _timestamp_load + INTERVAL 45 DAY" in sql


def test_a_declared_retention_beats_the_deployment_default(manifest):
    sql = (
        Renderer(manifest, default_ttl_days=45)
        .render(next(obj for obj in manifest.objects if obj.id == "data.alert_state"))
        .statements[0]
    )
    assert "INTERVAL 30 DAY" in sql


def _settings_clause(sql: str) -> str:
    """The SETTINGS clause of a rendered CREATE TABLE, up to the table COMMENT."""
    _, sep, tail = sql.partition("\nSETTINGS\n")
    assert sep, f"no SETTINGS clause in:\n{sql}"
    return tail.split("\nCOMMENT ", 1)[0]


def test_a_json_table_caps_the_subcolumns_a_merge_writes(rendered):
    """Uncapped, a merge over map-shaped JSON keys writes a stream per key and runs out of memory."""
    for platform in PLATFORMS:
        for object_id in sorted(JSON_TABLES):
            clause = _settings_clause(rendered[platform][object_id].statements[0])
            assert f"{MERGE_CAP} = " in clause, f"{object_id} ({platform}) has no {MERGE_CAP}"
            for name, value in JSON_TABLE_SETTINGS.items():
                assert f"{name} = {value}" in clause, f"{object_id} ({platform}): {clause}"


def test_a_json_table_takes_write_marks_off_on_self_hosted_only(rendered):
    for object_id in sorted(JSON_TABLES):
        self_hosted = _settings_clause(rendered[Platform.SELF_HOSTED][object_id].statements[0])
        cloud = _settings_clause(rendered[Platform.CLOUD][object_id].statements[0])
        assert f"{WRITE_MARKS} = 0" in self_hosted, f"{object_id}: {self_hosted}"
        assert WRITE_MARKS not in cloud, f"{object_id}: {cloud}"


def test_an_inherited_json_limit_reads_the_header_of_the_root_rendered(tmp_path):
    """otel_logs' BodyJson inherits _json's limit from the root's header, not the package's."""
    root = tmp_path / "schemas"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".*", "tests", "*.py", "*.lock"))
    header = root / "common-header" / "timeseries.yaml"
    text = header.read_text(encoding="utf-8")
    header.write_text(
        text.replace("max_dynamic_paths: 2048", "max_dynamic_paths: 7"),
        encoding="utf-8",
        newline="\n",
    )
    custom = load_manifest(root=root)
    logs = next(obj for obj in custom.objects if obj.id == "data.otel_logs")
    (statement,) = Renderer(custom).render(logs).statements[:1]
    assert "`BodyJson` JSON(max_dynamic_paths=7)" in statement


def test_a_table_without_a_json_column_carries_no_json_setting(rendered, manifest):
    """The cap means nothing without a JSON column, so it stays off every other table."""
    tables = [
        obj.id for obj in manifest.objects if obj.kind == "table" and obj.id not in DATALESS_TABLES
    ]
    assert set(tables) > JSON_TABLES
    for object_id in tables:
        clause = _settings_clause(rendered[Platform.SELF_HOSTED][object_id].statements[0])
        assert (MERGE_CAP in clause) is (object_id in JSON_TABLES), f"{object_id}: {clause}"
        assert (WRITE_MARKS in clause) is (object_id in JSON_TABLES), f"{object_id}: {clause}"


def test_the_ledger_carries_no_ttl(rendered):
    """A ledger that expires cannot answer which version an object came from."""
    assert "TTL " not in rendered[Platform.SELF_HOSTED]["data.schema_migrations"].statements[0]


def test_the_data_database_is_a_parameter(manifest):
    sql = (
        Renderer(manifest, data_database="warehouse")
        .render(next(obj for obj in manifest.objects if obj.id == "data.main"))
        .statements[0]
    )
    assert "`warehouse`.`main`" in sql


def test_the_meta_database_stays_fixed(manifest):
    sql = (
        Renderer(manifest, data_database="warehouse")
        .render(next(obj for obj in manifest.objects if obj.id == "meta.orgs"))
        .statements[0]
    )
    assert "`dfe_meta`.`orgs`" in sql


def test_a_view_grants_select_to_the_reader_role(rendered):
    statements = rendered[Platform.SELF_HOSTED]["view.dfe_v_system_health"].statements
    assert statements[-1] == "GRANT SELECT ON dfe.dfe_v_system_health TO dfe_query_reader_role"


def test_a_tier_creates_its_role_before_its_quota(rendered):
    """ClickHouse rejects a quota naming a role that does not exist yet."""
    statements = rendered[Platform.SELF_HOSTED]["role.analyst_tier_2"].statements
    role_at = next(i for i, s in enumerate(statements) if s.startswith("CREATE ROLE"))
    quota_at = next(i for i, s in enumerate(statements) if s.startswith("CREATE QUOTA"))
    assert role_at < quota_at


def test_a_role_grant_names_the_deployment_database(manifest):
    statements = (
        Renderer(manifest, data_database="warehouse")
        .render(next(obj for obj in manifest.objects if obj.id == "role.loader"))
        .statements
    )
    assert "GRANT INSERT ON warehouse.* TO `dfe_loader_role`" in statements


def test_the_tenant_role_grants_only_the_four_system_tables(rendered):
    statements = rendered[Platform.SELF_HOSTED]["role.tenant"].statements
    granted = {s.split("system.")[1].split(" ")[0] for s in statements if "system." in s}
    assert granted == {"columns", "settings", "table_engines", "tables"}


def test_a_topic_renders_its_admin_spec(rendered):
    landing = rendered[Platform.SELF_HOSTED]["topic.main_land"]
    assert landing.topic["name"] == "main_land"
    assert landing.topic["config"]["max.message.bytes"] == "16777216"
    assert "retention.ms" in landing.topic["config"]


def test_a_topic_replication_factor_is_clamped_to_the_brokers(manifest):
    """More replicas than brokers leaves a topic un-Ready forever."""
    obj = next(o for o in manifest.objects if o.id == "topic.dfe_loader_dlq")
    one = Renderer(manifest, broker_count=1).render(obj).topic
    three = Renderer(manifest, broker_count=3).render(obj).topic
    assert one is not None
    assert three is not None
    assert one["replication_factor"] == 1
    assert three["replication_factor"] == 3


def test_the_landing_topic_renders_its_tiering_key_only_when_asked(manifest):
    """A scale deploy with tiered storage on must still tier its landing topic."""
    obj = next(o for o in manifest.objects if o.id == "topic.main_land")
    off = Renderer(manifest).render(obj).topic
    on = Renderer(manifest, kafka_tiered_storage=True).render(obj).topic
    assert off is not None
    assert on is not None
    assert "remote.storage.enable" not in off["config"]
    assert on["config"]["remote.storage.enable"] == "true"


def test_a_dlq_renders_no_tiering_key_even_when_the_deployment_tiers(manifest):
    obj = next(o for o in manifest.objects if o.id == "topic.dfe_loader_dlq")
    dlq = Renderer(manifest, kafka_tiered_storage=True).render(obj).topic
    assert dlq is not None
    assert "remote.storage.enable" not in dlq["config"]


def test_a_dlq_keeps_a_poisoned_message_for_a_week(rendered):
    dlq = rendered[Platform.SELF_HOSTED]["topic.dfe_receiver_dlq"]
    assert dlq.topic["config"]["retention.ms"] == str(168 * 3_600_000)


def test_every_object_a_statement_names_is_in_the_manifest(rendered, manifest):
    declared = {qualified_name(obj) for obj in rendered[Platform.SELF_HOSTED].values()}
    for obj in rendered[Platform.SELF_HOSTED].values():
        missing = referenced_objects(obj) - declared
        assert not missing, f"{obj.id} names {sorted(missing)}, which nothing declares"


def test_a_reference_to_an_undeclared_object_is_visible():
    from dfe_schemas.render import RenderedObject

    rendered = RenderedObject(
        id="view.x",
        kind="view",
        database="dfe",
        name="x",
        platform="self_hosted",
        additive=True,
        optional=False,
        statements=("CREATE OR REPLACE VIEW dfe.x AS SELECT * FROM dfe.gone",),
        checksum="",
    )
    assert referenced_objects(rendered) == {"dfe.gone"}


def test_a_grant_wildcard_names_no_object():
    from dfe_schemas.render import RenderedObject

    rendered = RenderedObject(
        id="role.x",
        kind="role",
        database="dfe",
        name="x",
        platform="self_hosted",
        additive=True,
        optional=False,
        statements=("GRANT INSERT ON dfe.otel_* TO `r`", "GRANT SELECT ON dfe.kept TO `r`"),
        checksum="",
    )
    assert referenced_objects(rendered) == {"dfe.kept"}


def test_an_unknown_kind_does_not_render(manifest):
    from dataclasses import replace

    obj = replace(manifest.objects[0], kind="sequence")
    with pytest.raises(SchemaError, match="nothing renders"):
        Renderer(manifest).render(obj)
