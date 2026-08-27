#  Project:   dfe-schemas
#  File:      tests/unit/test_ddl_engine.py
#  Purpose:   Cover engine selection -- the cascade, and what may emit ON CLUSTER
#  Language:  Python
#
#  License:   BUSL-1.1
#  Copyright: (c) 2026 HYPERI PTY LIMITED

"""Engine selection is the part that breaks silently.

Sending a Replicated form to a keeperless node hard-fails with code 36, which is
loud. The dangerous direction is the other one: a plain MergeTree on a cluster
is accepted and then splits data across replicas, so these tests pin which
inputs may produce a replicated form and which may fan DDL out with ON CLUSTER.
"""

from __future__ import annotations

import pytest

from dfe_schemas.clickhouse import EngineResolver, EngineSpec, Topology, parse_engine


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeClient:
    """Answers the three questions sensing asks, and records what it was asked."""

    def __init__(self, *, cloud=None, db_engine=None, macros=(), clusters=()):
        self._cloud = cloud
        self._db_engine = db_engine
        self._macros = macros
        self._clusters = clusters
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        if "cloud_mode" in sql:
            return _Result([[self._cloud]] if self._cloud is not None else [])
        if "system.databases" in sql:
            return _Result([[self._db_engine]] if self._db_engine is not None else [])
        if "macro = 'cluster'" in sql:
            hit = [m for m in self._macros if m == "cluster"]
            return _Result([["the_cluster"]] if hit else [])
        if "system.macros" in sql:
            return _Result([[m] for m in self._macros])
        if "system.clusters" in sql:
            return _Result([[c] for c in self._clusters])
        raise AssertionError(f"unexpected query: {sql}")


class _ExplodingClient:
    def query(self, sql: str):
        raise RuntimeError("server unreachable")


@pytest.mark.parametrize(
    ("engine", "variant", "params"),
    [
        ("MergeTree", "MergeTree", ""),
        ("ReplacingMergeTree(updated_at)", "ReplacingMergeTree", "updated_at"),
        ("SummingMergeTree(a, b)", "SummingMergeTree", "a, b"),
        ("  MergeTree()  ", "MergeTree", ""),
    ],
)
def test_parse_engine_splits_variant_from_params(engine, variant, params):
    spec = parse_engine(engine)
    assert (spec.variant, spec.params) == (variant, params)


def test_a_keeperless_node_gets_the_plain_form():
    """The Replicated forms are REJECTED there, so this one is load-bearing."""
    resolver = EngineResolver(client=_FakeClient(macros=()))
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "MergeTree()"
    assert resolved.on_cluster == ""
    assert resolved.origin == "sensed"


def test_cloud_gets_the_replicated_form_without_on_cluster():
    resolver = EngineResolver(client=_FakeClient(cloud="1"))
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "ReplicatedMergeTree"
    assert resolved.on_cluster == ""


@pytest.mark.parametrize("db_engine", ["Replicated", "Shared"])
def test_a_replicated_database_needs_no_on_cluster(db_engine):
    """It propagates DDL itself; adding ON CLUSTER would double up."""
    resolver = EngineResolver(client=_FakeClient(db_engine=db_engine))
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "ReplicatedMergeTree"
    assert resolved.on_cluster == ""


def test_an_atomic_database_with_cluster_macros_fans_out():
    resolver = EngineResolver(client=_FakeClient(db_engine="Atomic", macros=("shard", "replica", "cluster")))
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "ReplicatedMergeTree"
    assert resolved.on_cluster == " ON CLUSTER the_cluster"


def test_it_falls_back_to_a_multi_host_cluster_when_no_macro_names_one():
    resolver = EngineResolver(client=_FakeClient(db_engine="Atomic", macros=("shard", "replica"), clusters=("found",)))
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.on_cluster == " ON CLUSTER found"


def test_variant_params_survive_replication():
    """A version column is not optional -- dropping it changes merge behaviour."""
    resolver = EngineResolver(client=_FakeClient(cloud="1"))
    resolved = resolver.resolve(parse_engine("ReplacingMergeTree(updated_at)"), "db")

    assert resolved.clause == "ReplicatedReplacingMergeTree(updated_at)"


def test_an_override_wins_over_the_live_server():
    resolver = EngineResolver(client=_FakeClient(cloud="1"), override="single")
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "MergeTree()"
    assert resolved.origin == "config-override"


@pytest.mark.parametrize("named", ["replicated", "REPLICATED", " replicated "])
def test_a_named_topology_never_emits_on_cluster(named):
    """Only sensing an Atomic cluster may fan DDL out.

    A value typed into config says which engine form to use and nothing about
    broadcasting, so it must not silently reach every node.
    """
    resolver = EngineResolver(override=named)
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "ReplicatedMergeTree"
    assert resolved.on_cluster == ""


def test_the_setting_is_used_only_when_there_is_no_client():
    resolver = EngineResolver(topology_setting="replicated")
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "ReplicatedMergeTree"
    assert resolved.origin == "topology-setting"


def test_sensing_beats_the_setting_when_a_client_is_present():
    resolver = EngineResolver(client=_FakeClient(macros=()), topology_setting="replicated")
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "MergeTree()"
    assert resolved.origin == "sensed"


def test_the_terminal_default_is_the_form_that_works_everywhere():
    resolved = EngineResolver().resolve(EngineSpec("MergeTree"), "db")

    assert resolved.clause == "MergeTree()"
    assert resolved.origin == "default"


def test_a_failed_sense_falls_through_rather_than_raising():
    """An unreachable server must not take the caller down with it."""
    resolver = EngineResolver(client=_ExplodingClient(), topology_setting="replicated")
    resolved = resolver.resolve(EngineSpec("MergeTree"), "db")

    assert resolved.origin == "topology-setting"


def test_sensing_is_cached_per_database():
    client = _FakeClient(macros=())
    resolver = EngineResolver(client=client)
    resolver.resolve(EngineSpec("MergeTree"), "db")
    after_first = len(client.queries)
    resolver.resolve(EngineSpec("ReplacingMergeTree"), "db")

    assert len(client.queries) == after_first


def test_a_different_database_is_sensed_again():
    client = _FakeClient(macros=())
    resolver = EngineResolver(client=client)
    resolver.resolve(EngineSpec("MergeTree"), "one")
    after_first = len(client.queries)
    resolver.resolve(EngineSpec("MergeTree"), "two")

    assert len(client.queries) > after_first


def test_topology_reports_the_coarse_form():
    single = EngineResolver().resolve(EngineSpec("MergeTree"), "db")
    clustered = EngineResolver(client=_FakeClient(db_engine="Atomic", macros=("shard", "replica"))).resolve(
        EngineSpec("MergeTree"), "db"
    )

    assert single.topology == "single"
    assert clustered.topology == "replicated"
    assert Topology.REPLICATED_ON_CLUSTER.value == "replicated_on_cluster"
