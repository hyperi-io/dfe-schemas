#  Project:      dfe-schemas
#  File:         tests/test_render.py
#  Purpose:      Rendering is topology-aware, and the checksum is not
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""What breaks silently is the pair these tests pin.

A literal engine is accepted on a cluster and then splits data across replicas:
every query succeeds and the answer depends which node answered. So the same
definition has to render three ways.

The checksum has to do the opposite. If the topology token reached it, every
cluster would read as drifted against every single node, and the migration
ledger would say the schema changed on a deploy that changed nothing.
"""

from __future__ import annotations

import pytest

import dfe_schemas
from dfe_schemas.clickhouse import Topology
from dfe_schemas.loader import SchemaError
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer, checksum, qualified_name, referenced_objects

ROOT = dfe_schemas.schemas_root()
TOPOLOGIES = (Topology.SINGLE, Topology.REPLICATED, Topology.REPLICATED_ON_CLUSTER)


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(root=ROOT)


def _render(manifest, topology, **kwargs):
    return {
        obj.id: Renderer(manifest, topology=topology, cluster="dfe_cluster", **kwargs).render(obj)
        for obj in manifest.objects
    }


@pytest.fixture(scope="module")
def rendered(manifest):
    return {topology: _render(manifest, topology) for topology in TOPOLOGIES}


def test_every_object_renders_for_every_topology(rendered, manifest):
    for topology in TOPOLOGIES:
        assert len(rendered[topology]) == len(manifest.objects)
        for obj in rendered[topology].values():
            assert obj.statements, f"{obj.id} rendered nothing"


def test_a_single_node_gets_the_plain_engine(rendered):
    sql = rendered[Topology.SINGLE]["data.main"].statements[0]
    assert "ENGINE = MergeTree()" in sql
    assert "ON CLUSTER" not in sql


def test_a_replicated_database_gets_no_on_cluster(rendered):
    """It propagates DDL itself; adding ON CLUSTER would double up."""
    sql = rendered[Topology.REPLICATED]["data.main"].statements[0]
    assert "ENGINE = ReplicatedMergeTree" in sql
    assert "ON CLUSTER" not in sql


def test_an_atomic_cluster_fans_the_ddl_out(rendered):
    sql = rendered[Topology.REPLICATED_ON_CLUSTER]["data.main"].statements[0]
    assert "ENGINE = ReplicatedMergeTree" in sql
    assert "ON CLUSTER dfe_cluster" in sql


def test_the_database_itself_is_created_cluster_wide(rendered):
    """Without it, ON CLUSTER table DDL lands on nodes with no database to hold it."""
    sql = rendered[Topology.REPLICATED_ON_CLUSTER]["db.data"].statements[0]
    assert sql == "CREATE DATABASE IF NOT EXISTS dfe ON CLUSTER dfe_cluster"


def test_a_view_is_replaced_cluster_wide(rendered):
    sql = rendered[Topology.REPLICATED_ON_CLUSTER]["view.dfe_v_system_health"].statements[0]
    assert sql.startswith(
        "CREATE OR REPLACE VIEW dfe.dfe_v_system_health ON CLUSTER dfe_cluster AS"
    )


def test_engine_parameters_survive_replication(rendered):
    """Dropping a version column changes merge behaviour, silently."""
    sql = rendered[Topology.REPLICATED]["data.alert_state"].statements[0]
    assert "ENGINE = ReplicatedReplacingMergeTree(last_fired_at)" in sql


def test_the_checksum_is_the_same_under_every_topology(rendered, manifest):
    for obj in manifest.objects:
        seen = {rendered[topology][obj.id].checksum for topology in TOPOLOGIES}
        assert len(seen) == 1, f"{obj.id} checksums differently per topology"


def test_the_checksum_moves_when_the_schema_does():
    one = checksum(["CREATE TABLE a (x String) ENGINE = MergeTree()"])
    two = checksum(["CREATE TABLE a (x Int64) ENGINE = MergeTree()"])
    assert one != two


def test_the_checksum_ignores_whitespace():
    assert checksum(["CREATE  TABLE a\n(x String)"]) == checksum(["CREATE TABLE a (x String)"])


def test_the_landing_table_is_named_main(rendered):
    assert rendered[Topology.SINGLE]["data.main"].name == "main"
    assert "`dfe`.`main`" in rendered[Topology.SINGLE]["data.main"].statements[0]


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


def test_the_ledger_carries_no_ttl(rendered):
    """A ledger that expires cannot answer which version an object came from."""
    assert "TTL " not in rendered[Topology.SINGLE]["data.schema_migrations"].statements[0]


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
    statements = rendered[Topology.SINGLE]["view.dfe_v_system_health"].statements
    assert statements[-1] == "GRANT SELECT ON dfe.dfe_v_system_health TO dfe_query_reader_role"


def test_a_tier_creates_its_role_before_its_quota(rendered):
    """ClickHouse rejects a quota naming a role that does not exist yet."""
    statements = rendered[Topology.SINGLE]["role.analyst_tier_2"].statements
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
    statements = rendered[Topology.SINGLE]["role.tenant"].statements
    granted = {s.split("system.")[1].split(" ")[0] for s in statements if "system." in s}
    assert granted == {"columns", "settings", "table_engines", "tables"}


def test_a_topic_renders_its_admin_spec(rendered):
    landing = rendered[Topology.SINGLE]["topic.main_land"]
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
    dlq = rendered[Topology.SINGLE]["topic.dfe_receiver_dlq"]
    assert dlq.topic["config"]["retention.ms"] == str(168 * 3_600_000)


def test_every_object_a_statement_names_is_in_the_manifest(rendered, manifest):
    declared = {qualified_name(obj) for obj in rendered[Topology.SINGLE].values()}
    for obj in rendered[Topology.SINGLE].values():
        missing = referenced_objects(obj) - declared
        assert not missing, f"{obj.id} names {sorted(missing)}, which nothing declares"


def test_a_reference_to_an_undeclared_object_is_visible():
    from dfe_schemas.render import RenderedObject

    rendered = RenderedObject(
        id="view.x",
        kind="view",
        database="dfe",
        name="x",
        topology="single",
        additive=True,
        optional=False,
        statements=("CREATE OR REPLACE VIEW dfe.x AS SELECT * FROM dfe.gone",),
        checksum="",
    )
    assert referenced_objects(rendered) == {"dfe.gone"}


def test_an_unknown_kind_does_not_render(manifest):
    from dataclasses import replace

    obj = replace(manifest.objects[0], kind="sequence")
    with pytest.raises(SchemaError, match="nothing renders"):
        Renderer(manifest).render(obj)
