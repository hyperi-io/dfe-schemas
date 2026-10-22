#  Project:   dfe-schemas
#  File:      tests/test_clickhouse_platform.py
#  Purpose:   Cover the platform cascade, the one engine form and the Keeper refusal
#  Language:  Python
#
#  License:   BUSL-1.1
#  Copyright: (c) 2026 HYPERI PTY LIMITED

"""The platform decides two things and must never decide the engine.

Cloud and self-hosted differ only in the CREATE DATABASE and a JSON table's
platform-only settings, so these tests pin the cascade that names the platform,
the one engine form both take, and the refusal a server without Keeper, or a
cluster without access storage in Keeper, gets instead of a second form.
"""

import pytest

from dfe_schemas.clickhouse import (
    PLATFORM_CHOICES,
    EngineSpec,
    Platform,
    PlatformProbe,
    ReplicationNotConfiguredError,
    create_database,
    engine_clause,
    parse_engine,
    platform_from_host,
    platform_json_settings,
    platform_only_setting_names,
    platform_override,
    realign_settings,
)
from dfe_schemas.loader import SchemaError

WRITE_MARKS = "write_marks_for_substreams_in_compact_parts"
# What a stock server lists, and what the two user_directories configs give.
LOCAL_ACCESS = ("users_xml", "local_directory")
APPENDED_REPLICATED = ("users_xml", "local_directory", "replicated")
REPLACED_REPLICATED = ("users_xml", "replicated")


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _FakeClient:
    """Answers the questions the probe asks, and records each one."""

    def __init__(
        self,
        *,
        shared_engine=0,
        keeper=True,
        macros=("cluster", "shard", "replica"),
        servers=1,
        replicated_database=False,
        directories=LOCAL_ACCESS,
    ):
        self._shared_engine = shared_engine
        self._keeper = keeper
        self._macros = macros
        # A Replicated database named like the cluster lists every replica a second time.
        replicas = [(1, number) for number in range(1, servers + 1)]
        self._cluster_rows = replicas * 2 if replicated_database else replicas
        self._directories = directories
        self.queries: list[str] = []
        self.parameters: list[dict[str, str] | None] = []

    def query(self, sql: str, parameters: dict[str, str] | None = None):
        self.queries.append(sql)
        self.parameters.append(parameters)
        if "system.table_engines" in sql:
            return _Result([[self._shared_engine]])
        if "system.tables" in sql:
            return _Result([[1 if self._keeper else 0]])
        if "system.macros" in sql:
            return _Result([[m, f"{m}-value"] for m in self._macros])
        if "system.clusters" in sql:
            return _Result([[self._cluster_aggregate(sql)]])
        if "system.user_directories" in sql:
            return _Result([[kind] for kind in self._directories])
        raise AssertionError(f"unexpected query: {sql}")

    def _cluster_aggregate(self, sql: str) -> int:
        if "uniqExact((shard_num, replica_num))" in sql:
            return len(set(self._cluster_rows))
        if "count()" in sql:
            return len(self._cluster_rows)
        raise AssertionError(f"unexpected system.clusters aggregate: {sql}")


class _ExplodingClient:
    def query(self, sql: str, parameters: dict[str, str] | None = None):
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


@pytest.mark.parametrize(
    ("engine", "clause"),
    [
        ("MergeTree", "ReplicatedMergeTree"),
        ("MergeTree()", "ReplicatedMergeTree"),
        ("ReplacingMergeTree(updated_at)", "ReplicatedReplacingMergeTree(updated_at)"),
        ("SummingMergeTree(a, b)", "ReplicatedSummingMergeTree(a, b)"),
    ],
)
def test_every_variant_takes_the_argumentless_replicated_form(engine, clause):
    """A version column is not optional: dropping it changes merge behaviour."""
    assert engine_clause(parse_engine(engine)) == clause


@pytest.mark.parametrize("variant", ["ReplicatedMergeTree", "SharedMergeTree"])
def test_a_declared_platform_prefix_is_refused(variant):
    with pytest.raises(ValueError, match="variant alone"):
        engine_clause(EngineSpec(variant))


def test_the_live_probe_reads_cloud_off_shared_merge_tree():
    probe = PlatformProbe(_FakeClient(shared_engine=1))
    assert probe.platform is Platform.CLOUD
    assert probe.origin == "probe"


def test_the_live_probe_reads_self_hosted_when_shared_merge_tree_is_absent():
    probe = PlatformProbe(_FakeClient(shared_engine=0), host="x.clickhouse.cloud")
    assert probe.platform is Platform.SELF_HOSTED
    assert probe.origin == "probe"


def test_the_probe_runs_once_per_connection():
    client = _FakeClient(shared_engine=1)
    probe = PlatformProbe(client)
    for _ in range(3):
        assert probe.platform is Platform.CLOUD
    assert sum("system.table_engines" in sql for sql in client.queries) == 1


@pytest.mark.parametrize("override", [Platform.SELF_HOSTED, "self_hosted"])
def test_an_override_wins_over_the_live_server(override):
    client = _FakeClient(shared_engine=1)
    probe = PlatformProbe(client, override=override)
    assert probe.platform is Platform.SELF_HOSTED
    assert probe.origin == "override"
    assert client.queries == []


def test_an_unknown_override_is_refused():
    with pytest.raises(ValueError, match="hybrid"):
        PlatformProbe(override="hybrid")


@pytest.mark.parametrize("override", ["auto", " AUTO ", None])
def test_auto_pins_nothing_and_the_probe_decides(override):
    client = _FakeClient(shared_engine=1)
    probe = PlatformProbe(client, override=override)
    assert probe.platform is Platform.CLOUD
    assert probe.origin == "probe"


@pytest.mark.parametrize(
    ("value", "platform"),
    [
        ("auto", None),
        (None, None),
        ("cloud", Platform.CLOUD),
        ("Self_Hosted", Platform.SELF_HOSTED),
        (Platform.CLOUD, Platform.CLOUD),
    ],
)
def test_a_configured_choice_parses_to_its_override(value, platform):
    assert platform_override(value) is platform


@pytest.mark.parametrize("value", ["", "hybrid", "selfhosted"])
def test_a_configured_choice_outside_the_list_is_refused_naming_the_list(value):
    with pytest.raises(ValueError, match="is not one of") as raised:
        platform_override(value)
    for choice in PLATFORM_CHOICES:
        assert choice in str(raised.value)


def test_the_choices_are_auto_and_every_platform():
    assert PLATFORM_CHOICES == ("auto", "cloud", "self_hosted")


@pytest.mark.parametrize(
    ("host", "platform"),
    [
        ("abc123.ap-southeast-2.aws.clickhouse.cloud", Platform.CLOUD),
        ("ABC123.EU-WEST-1.AWS.CLICKHOUSE.CLOUD.", Platform.CLOUD),
        ("svc.byoc-region.clickhouse-byoc.com", Platform.CLOUD),
        ("clickhouse.dfe.svc.cluster.local", Platform.SELF_HOSTED),
        ("localhost", Platform.SELF_HOSTED),
        ("10.0.0.5", Platform.SELF_HOSTED),
        # The suffix is a whole label, not a substring of one.
        ("evilclickhouse.cloud", Platform.SELF_HOSTED),
        ("clickhouse.cloud.example.com", Platform.SELF_HOSTED),
    ],
)
def test_the_hostname_names_the_platform(host, platform):
    assert platform_from_host(host) is platform


def test_the_hostname_decides_when_there_is_no_client():
    probe = PlatformProbe(host="abc.us-east-1.aws.clickhouse.cloud")
    assert probe.platform is Platform.CLOUD
    assert probe.origin == "hostname"


def test_a_failed_probe_falls_through_to_the_hostname():
    """An unreachable server must not take the caller down with it."""
    probe = PlatformProbe(_ExplodingClient(), host="abc.clickhouse-byoc.com")
    assert probe.platform is Platform.CLOUD
    assert probe.origin == "hostname"


def test_the_terminal_default_is_self_hosted():
    probe = PlatformProbe()
    assert probe.platform is Platform.SELF_HOSTED
    assert probe.origin == "default"


def test_a_self_hosted_database_is_replicated_and_the_only_on_cluster():
    sql = create_database("dfe", Platform.SELF_HOSTED)
    assert sql == (
        "CREATE DATABASE IF NOT EXISTS dfe ON CLUSTER '{cluster}'\n"
        "ENGINE = Replicated('/clickhouse/databases/dfe', '{shard}', '{replica}')"
    )


def test_a_cloud_database_takes_the_server_default():
    assert create_database("dfe", Platform.CLOUD) == "CREATE DATABASE IF NOT EXISTS dfe"


def test_the_keeper_path_follows_the_database_name():
    assert "'/clickhouse/databases/dfe_meta'" in create_database("dfe_meta", Platform.SELF_HOSTED)


@pytest.mark.parametrize("name", ["", "dfe/x", "dfe') --", "my db", "1dfe"])
def test_a_database_name_that_is_not_bare_is_refused(name):
    with pytest.raises(SchemaError, match="bare identifier"):
        create_database(name, Platform.SELF_HOSTED)


def test_the_write_marks_setting_is_self_hosted_only():
    assert platform_json_settings(Platform.SELF_HOSTED) == {WRITE_MARKS: 0}
    assert platform_json_settings(Platform.CLOUD) == {}
    assert platform_only_setting_names() == frozenset({WRITE_MARKS})


def test_realign_sets_a_missing_platform_setting_on_self_hosted():
    move = realign_settings({WRITE_MARKS: 0, "cap": 64}, {"cap": "64"})
    assert move.modify == ((WRITE_MARKS, "0"),)
    assert move.reset == ()
    assert move.statements("`d`.`t`") == (f"ALTER TABLE `d`.`t` MODIFY SETTING {WRITE_MARKS} = 0",)


def test_realign_resets_a_platform_setting_cloud_does_not_take():
    move = realign_settings({"cap": 64}, {"cap": "64", WRITE_MARKS: "0"})
    assert move.modify == ()
    assert move.reset == (WRITE_MARKS,)
    assert move.statements("`d`.`t`") == (f"ALTER TABLE `d`.`t` RESET SETTING {WRITE_MARKS}",)
    assert move.describe() == (f"reset {WRITE_MARKS}",)


def test_realign_leaves_a_setting_no_platform_manages():
    """An operator's own setting is not the platform's to remove."""
    move = realign_settings({"cap": 64}, {"cap": "64", "parts_to_throw_insert": "600"})
    assert move.statements("`d`.`t`") == ()


def test_realign_changes_nothing_on_a_table_already_there():
    move = realign_settings({WRITE_MARKS: 0}, {WRITE_MARKS: "0"})
    assert move.statements("`d`.`t`") == ()


def test_a_self_hosted_server_with_keeper_and_macros_passes():
    PlatformProbe(_FakeClient()).require_replication()


def test_a_keeperless_server_is_refused_with_the_reason_and_the_docs():
    probe = PlatformProbe(_FakeClient(keeper=False), host="ch.internal")
    with pytest.raises(ReplicationNotConfiguredError) as raised:
        probe.require_replication()
    message = str(raised.value)
    assert "self-hosted ClickHouse is missing Keeper" in message
    # The engine serves this text to signed-in callers, so it names no host.
    assert "ch.internal" not in message
    assert "Replicated database" in message
    assert "docs/clickhouse-platform.md" in message


def test_a_missing_macro_is_named():
    probe = PlatformProbe(_FakeClient(macros=("shard", "replica")))
    with pytest.raises(ReplicationNotConfiguredError, match=r"missing the \{cluster\} macro"):
        probe.require_replication()


def test_a_cluster_macro_naming_no_remote_servers_cluster_is_refused():
    probe = PlatformProbe(_FakeClient(servers=0))
    with pytest.raises(ReplicationNotConfiguredError, match="remote_servers cluster named by"):
        probe.require_replication()


def test_the_cluster_lookup_binds_the_macro_value():
    client = _FakeClient()
    PlatformProbe(client).require_replication()
    index = next(i for i, sql in enumerate(client.queries) if "system.clusters" in sql)
    assert "{name:String}" in client.queries[index]
    assert client.parameters[index] == {"name": "cluster-value"}


def test_a_single_node_keeps_local_access_storage():
    client = _FakeClient(servers=1, directories=LOCAL_ACCESS)
    PlatformProbe(client).require_replication()
    assert not any("system.user_directories" in sql for sql in client.queries)


def test_a_single_node_listed_twice_by_its_replicated_database_is_one_server():
    """system.clusters carries the database's own cluster of the same name."""
    client = _FakeClient(servers=1, replicated_database=True, directories=LOCAL_ACCESS)
    PlatformProbe(client).require_replication()
    assert not any("system.user_directories" in sql for sql in client.queries)


def test_replicas_listed_twice_by_a_replicated_database_count_once():
    probe = PlatformProbe(
        _FakeClient(servers=3, replicated_database=True, directories=LOCAL_ACCESS)
    )
    with pytest.raises(ReplicationNotConfiguredError, match="cluster has 3 servers"):
        probe.require_replication()


def test_a_cluster_writing_roles_to_local_storage_is_refused_with_the_docs():
    probe = PlatformProbe(_FakeClient(servers=2, directories=LOCAL_ACCESS), host="ch.internal")
    with pytest.raises(ReplicationNotConfiguredError) as raised:
        probe.require_replication()
    message = str(raised.value)
    assert "cluster has 2 servers" in message
    assert "local_directory access storage" in message
    assert "ch.internal" not in message
    assert "docs/clickhouse-platform.md" in message


def test_replicated_appended_after_local_storage_is_still_refused():
    """The first writable storage takes every CREATE ROLE, so local_directory wins."""
    probe = PlatformProbe(_FakeClient(servers=3, directories=APPENDED_REPLICATED))
    with pytest.raises(ReplicationNotConfiguredError, match="local_directory access storage"):
        probe.require_replication()


def test_a_cluster_with_access_storage_in_keeper_passes():
    PlatformProbe(_FakeClient(servers=2, directories=REPLACED_REPLICATED)).require_replication()


def test_a_cluster_with_no_writable_access_storage_is_refused():
    probe = PlatformProbe(_FakeClient(servers=2, directories=("users_xml",)))
    with pytest.raises(ReplicationNotConfiguredError, match="no writable access storage"):
        probe.require_replication()


def test_the_keeper_check_binds_the_table_name():
    client = _FakeClient()
    PlatformProbe(client).require_replication()
    index = next(i for i, sql in enumerate(client.queries) if "system.tables" in sql)
    assert "{name:String}" in client.queries[index]
    assert client.parameters[index] == {"name": "zookeeper_connection"}


def test_cloud_needs_no_keeper_check():
    client = _FakeClient(shared_engine=1, keeper=False, macros=())
    PlatformProbe(client).require_replication()
    assert not any("system.macros" in sql for sql in client.queries)


def test_the_keeper_check_needs_a_client():
    with pytest.raises(RuntimeError, match="needs a client"):
        PlatformProbe().require_replication()
