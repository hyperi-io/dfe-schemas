#  Project:      dfe-schemas
#  File:         tests/test_usage_rollup.py
#  Purpose:      The usage rollup's daily numbers, on a real ClickHouse running the whole manifest
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The usage tables hold the numbers a deployment is licensed on, so the values are exact.

The whole manifest is applied, rendered for self-hosted, to a real ClickHouse
with Keeper, then synthetic telemetry goes in: two DFE pods and one that is
not, a pod that goes Pending, a second collector reporting a container twice,
a counter that resets, two orgs and a new pod, and events either side of UTC
midnight. Two fixed days, five and four days back, keep clear of every
refresh window, and the rollup views write them the way the materialised views
do. One last test drives the materialised views themselves over the current
minutes.
"""

import time
from collections.abc import Iterator
from typing import Any

import pytest

import dfe_schemas
from dfe_schemas import usage
from dfe_schemas.clickhouse import Platform
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer
from tests.clickhouse_server import (
    ClickHouse,
    ClickHouseError,
    start_clickhouse,
    stop_clickhouse,
)

DB = "usage_test"
ROOT = dfe_schemas.schemas_root()
READER = "dfe_query_reader_role"
REFRESH_SECONDS = 90
MINUTE_VIEWS = ("usage_minute_cores", "usage_minute_ingest", "usage_minute_events")
USAGE_MVS = (
    "usage_minute_cores_mv",
    "usage_minute_ingest_mv",
    "usage_minute_events_mv",
    "usage_daily_mv",
)
LONG_AGO = "2020-01-01 00:00:00"


@pytest.fixture(scope="module")
def server() -> Iterator[ClickHouse]:
    client, name = start_clickhouse()
    try:
        yield client
    finally:
        stop_clickhouse(name)


@pytest.fixture(scope="module")
def deployed(server: ClickHouse) -> ClickHouse:
    """The whole manifest applied in order, then all_sources the way dfe-engine builds it."""
    manifest = load_manifest(root=ROOT)
    renderer = Renderer(manifest, platform=Platform.SELF_HOSTED, data_database=DB)
    for obj in manifest.objects:
        if obj.kind == "topic":
            continue
        try:
            for statement in renderer.render(obj).statements:
                server.run(statement)
        except ClickHouseError:
            # The engine skips an optional object the server will not take, so this does too.
            if not obj.optional:
                raise
    server.run(f"CREATE TABLE {DB}.syslog AS {DB}.main")
    server.run(
        f"CREATE OR REPLACE TABLE {DB}.all_sources (_timestamp_load DateTime64(3,'UTC'), "
        f"_timestamp DateTime64(3,'UTC'), _org_id LowCardinality(String)) "
        f"ENGINE = Merge({DB}, '^(main|syslog)$')"
    )
    return server


@pytest.fixture(scope="module")
def days(deployed: ClickHouse) -> tuple[str, str]:
    """Two consecutive UTC days, five and four days back, on the server's clock."""
    row = deployed.rows("SELECT toDate(now(), 'UTC') - 5 AS d1, toDate(now(), 'UTC') - 4 AS d2")[0]
    return row["d1"], row["d2"]


def _map(attributes: dict[str, str]) -> str:
    pairs = ", ".join(f"'{key}', '{value}'" for key, value in attributes.items())
    return f"map({pairs})"


def _container(pod: str, container: str, node: str) -> str:
    return _map(
        {
            usage.K8S_POD_UID_ATTRIBUTE: pod,
            usage.K8S_CONTAINER_NAME_ATTRIBUTE: container,
            usage.K8S_NODE_NAME_ATTRIBUTE: node,
        }
    )


def _pod(pod: str, node: str) -> str:
    return _map({usage.K8S_POD_UID_ATTRIBUTE: pod, usage.K8S_NODE_NAME_ATTRIBUTE: node})


def _insert_gauges(server: ClickHouse, rows: list[tuple[str, str, str, float]]) -> None:
    """``(metric, resource map, TimeUnix, value)`` rows, as k8s_cluster writes them."""
    values = ",\n".join(
        f"({resource}, 'k8s-cluster', '{metric}', toDateTime('{at}', 'UTC'), {value})"
        for metric, resource, at, value in rows
    )
    server.run(
        f"INSERT INTO {DB}.otel_metrics_gauge "
        f"(ResourceAttributes, ServiceName, MetricName, TimeUnix, Value) VALUES\n{values}"
    )


def _insert_sums(
    server: ClickHouse,
    rows: list[tuple[str, str, str, str, str, str, float]],
    *,
    temporality: int = 2,
) -> None:
    """``(service, metric, resource map, attributes map, StartTimeUnix, TimeUnix, value)`` rows.

    One INSERT is one block, so a repeated row survives Replicated block deduplication.
    """
    values = ",\n".join(
        f"({resource}, '{service}', '{metric}', {attributes}, toDateTime('{start}', 'UTC'), "
        f"toDateTime('{at}', 'UTC'), {value}, {temporality}, true)"
        for service, metric, resource, attributes, start, at, value in rows
    )
    server.run(
        f"INSERT INTO {DB}.otel_metrics_sum (ResourceAttributes, ServiceName, MetricName, "
        "Attributes, StartTimeUnix, TimeUnix, Value, AggregationTemporality, IsMonotonic) "
        f"VALUES\n{values}"
    )


def _insert_events(
    server: ClickHouse, table: str, start: str, step_ms: int, orgs: str, n: int
) -> None:
    """*n* rows loaded *step_ms* apart from *start*, the org an expression of ``number``."""
    server.run(
        f"INSERT INTO {DB}.{table} (_timestamp_load, _timestamp, _org_id) "
        f"SELECT addMilliseconds(toDateTime64('{start}', 3, 'UTC'), number * {step_ms}) AS t, t, "
        f"{orgs} FROM numbers({n})"
    )


def _roll_up(server: ClickHouse, d1: str, d2: str) -> None:
    """Write the two days through the rollup views into both tables, as the views' refreshes do.

    The SELECT is wrapped in a subquery: 26.3 refuses a parameterised view that
    calls another as the direct source of an INSERT (code 46).
    """
    end = server.scalar(f"SELECT toString(toDate('{d2}') + 1)")
    window = f"from = '{d1} 00:00:00', to = '{end} 00:00:00'"
    for view in MINUTE_VIEWS:
        server.run(
            f"INSERT INTO {DB}.usage_minute (minute, metric, _org_id, value) "
            f"SELECT minute, metric, _org_id, value FROM ("
            f"SELECT minute, metric, _org_id, value FROM {DB}.{view}({window}))"
        )
    server.run(
        f"INSERT INTO {DB}.usage_daily (day, metric, _org_id, value) "
        f"SELECT day, metric, _org_id, value FROM ("
        f"SELECT day, metric, _org_id, value FROM {DB}.usage_daily_rollup("
        f"from = '{d1}', to = '{end}'))"
    )


@pytest.fixture(scope="module")
def rolled_up(deployed: ClickHouse, days: tuple[str, str]) -> ClickHouse:
    d1, d2 = days
    a, b, c = "node-a", "node-b", "node-c"
    phase, request = usage.K8S_POD_PHASE_METRIC, usage.K8S_CPU_REQUEST_METRIC
    _insert_gauges(
        deployed,
        [
            # 10:00, everything running: pa 0.75 on node-a, pb 2 on node-b, pc 8 on node-c.
            (request, _container("pa", "recv", a), f"{d1} 10:00:00", 0.5),
            (request, _container("pa", "side", a), f"{d1} 10:00:00", 0.25),
            (request, _container("pb", "load", b), f"{d1} 10:00:00", 2.0),
            (request, _container("pc", "other", c), f"{d1} 10:00:00", 8.0),
            (phase, _pod("pa", a), f"{d1} 10:00:00", 2),
            (phase, _pod("pb", b), f"{d1} 10:00:00", 2),
            (phase, _pod("pc", c), f"{d1} 10:00:00", 2),
            # 11:00, pb Pending with a bigger request: the peak only if Pending counted.
            (request, _container("pa", "recv", a), f"{d1} 11:00:00", 0.5),
            (request, _container("pa", "side", a), f"{d1} 11:00:00", 0.25),
            (request, _container("pb", "load", b), f"{d1} 11:00:00", 4.0),
            (phase, _pod("pa", a), f"{d1} 11:00:00", 2),
            (phase, _pod("pb", b), f"{d1} 11:00:00", 1),
            # Across midnight, pa alone.
            (request, _container("pa", "recv", a), f"{d2} 00:00:30", 0.5),
            (request, _container("pa", "side", a), f"{d2} 00:00:30", 0.25),
            (phase, _pod("pa", a), f"{d2} 00:00:30", 2),
        ],
    )
    # A second collector reporting pa/recv at the same 10:00 tick, in its own insert.
    _insert_gauges(deployed, [(request, _container("pa", "recv", a), f"{d1} 10:00:00", 0.5)])

    cpus = usage.NODE_LOGICAL_CPU_METRIC
    _insert_sums(
        deployed,
        [
            ("k8s-node", cpus, _map({usage.K8S_NODE_NAME_ATTRIBUTE: name}), "map()", t, t, n)
            for name, t, n in (
                (a, f"{d1} 10:00:10", 4),
                (b, f"{d1} 10:00:10", 8),
                (c, f"{d1} 10:00:10", 16),
                (a, f"{d1} 11:00:10", 4),
                (b, f"{d1} 11:00:10", 8),
                (a, f"{d2} 00:00:40", 4),
            )
        ],
    )

    counter, org = usage.INGESTED_BYTES_METRIC, usage.ORG_ID_ATTRIBUTE
    r1, r2 = _map({"service.instance.id": "r1"}), _map({"service.instance.id": "r2"})
    org_a, org_b = _map({org: "a"}), _map({org: "b"})
    restarted = f"{d2} 00:00:30"
    _insert_sums(
        deployed,
        [
            ("dfe-receiver", counter, r1, org_a, LONG_AGO, f"{d1} 23:58:00", 1000),
            ("dfe-receiver", counter, r1, org_a, LONG_AGO, f"{d1} 23:59:00", 1600),
            ("dfe-receiver", counter, r1, org_a, LONG_AGO, f"{d2} 00:00:00", 1900),
            ("dfe-receiver", counter, r1, org_a, restarted, f"{d2} 00:01:00", 200),
            ("dfe-receiver", counter, r1, org_a, restarted, f"{d2} 00:02:00", 500),
            ("dfe-receiver", counter, r1, org_b, LONG_AGO, f"{d1} 23:58:00", 10),
            ("dfe-receiver", counter, r1, org_b, LONG_AGO, f"{d1} 23:59:00", 50),
            ("dfe-receiver", counter, r1, org_b, LONG_AGO, f"{d2} 00:01:00", 90),
            ("dfe-receiver", counter, r2, org_b, f"{d1} 23:58:30", f"{d1} 23:59:00", 25),
        ],
    )

    # 120 events in D1's last minute, every third for org b, 30 for org b at noon, and 60
    # for org a in D2's first minute, in a second source table.
    _insert_events(deployed, "main", f"{d1} 23:59:00", 500, "if(number % 3 = 0, 'b', 'a')", 120)
    _insert_events(deployed, "main", f"{d1} 12:00:00", 1000, "'b'", 30)
    _insert_events(deployed, "syslog", f"{d2} 00:00:00", 250, "'a'", 60)

    _roll_up(deployed, d1, d2)
    return deployed


def _daily(server: ClickHouse, metric: str) -> dict[tuple[str, str], float]:
    rows = server.rows(
        f"SELECT toString(day) AS day, _org_id, value FROM {DB}.usage_daily FINAL "
        f"WHERE metric = '{metric}'"
    )
    return {(row["day"], row["_org_id"]): row["value"] for row in rows}


def _minute(server: ClickHouse, metric: str) -> dict[tuple[str, str], float]:
    rows = server.rows(
        f"SELECT toString(minute) AS minute, _org_id, value FROM {DB}.usage_minute FINAL "
        f"WHERE metric = '{metric}'"
    )
    return {(row["minute"], row["_org_id"]): row["value"] for row in rows}


def _on(values: dict[tuple[str, str], float], days: tuple[str, ...]) -> dict:
    return {key: value for key, value in values.items() if key[0][:10] in days}


def test_cores_deployed_is_the_peak_tick_of_running_containers(rolled_up, days):
    """10.75 at 10:00. Counting Pending makes 11:00 4.75, and the duplicate makes 10:00 11.25."""
    d1, d2 = days
    assert _on(_daily(rolled_up, usage.CORES_DEPLOYED), days) == {(d1, ""): 10.75, (d2, ""): 0.75}
    assert _on(_minute(rolled_up, usage.CORES_DEPLOYED), days) == {
        (f"{d1} 10:00:00", ""): 10.75,
        (f"{d1} 11:00:00", ""): 0.75,
        (f"{d2} 00:00:00", ""): 0.75,
    }


def test_node_cores_count_only_nodes_running_a_container(rolled_up, days):
    """All three nodes at 10:00. node-b runs only a Pending pod at 11:00."""
    d1, d2 = days
    assert _on(_daily(rolled_up, usage.NODE_CORES), days) == {(d1, ""): 28, (d2, ""): 4}
    assert _on(_minute(rolled_up, usage.NODE_CORES), days) == {
        (f"{d1} 10:00:00", ""): 28,
        (f"{d1} 11:00:00", ""): 4,
        (f"{d2} 00:00:00", ""): 4,
    }


def test_ingested_bytes_is_the_reset_aware_increase_per_day(rolled_up, days):
    """Org a's reset adds 200 from zero, not -1700. The new pod's first 25 counts."""
    d1, d2 = days
    assert _on(_daily(rolled_up, usage.INGESTED_BYTES), days) == {
        (d1, "a"): 600,
        (d1, "b"): 65,
        (d1, ""): 665,
        (d2, "a"): 800,
        (d2, "b"): 40,
        (d2, ""): 840,
    }


def test_the_counter_reset_lands_in_its_own_minute(rolled_up, days):
    d1, d2 = days
    minutes = _on(_minute(rolled_up, usage.INGESTED_BYTES), days)
    # The first sample of a counter that started before the read is its baseline.
    assert minutes[(f"{d1} 23:58:00", "a")] == 0
    assert minutes[(f"{d1} 23:59:00", "a")] == 600
    assert minutes[(f"{d2} 00:00:00", "a")] == 300
    assert minutes[(f"{d2} 00:01:00", "a")] == 200
    assert minutes[(f"{d2} 00:02:00", "a")] == 300


def test_events_per_second_is_the_highest_minute_either_side_of_midnight(rolled_up, days):
    d1, d2 = days
    assert _on(_daily(rolled_up, usage.EVENTS_PER_SECOND), days) == {
        (d1, ""): 2,
        (d1, "a"): 80 / 60,
        (d1, "b"): 40 / 60,
        (d2, ""): 1,
        (d2, "a"): 1,
    }
    assert _on(_minute(rolled_up, usage.EVENTS_PER_SECOND), days) == {
        (f"{d1} 12:00:00", "b"): 0.5,
        (f"{d1} 12:00:00", ""): 0.5,
        (f"{d1} 23:59:00", "a"): 80 / 60,
        (f"{d1} 23:59:00", "b"): 40 / 60,
        (f"{d1} 23:59:00", ""): 2,
        (f"{d2} 00:00:00", "a"): 1,
        (f"{d2} 00:00:00", ""): 1,
    }


def _engine_full(server: ClickHouse, table: str) -> str:
    return server.scalar(
        f"SELECT engine_full FROM system.tables WHERE database = '{DB}' AND name = '{table}'"
    )


def test_the_minute_table_keeps_seven_days(deployed):
    full = _engine_full(deployed, "usage_minute")
    assert "TTL minute + toIntervalDay(7)" in full
    assert "ttl_only_drop_parts = 1" in full
    assert full.startswith("ReplicatedReplacingMergeTree(")


def test_the_daily_table_keeps_four_hundred_days(deployed):
    full = _engine_full(deployed, "usage_daily")
    assert "TTL day + toIntervalDay(400)" in full
    assert "ttl_only_drop_parts = 1" in full
    assert full.startswith("ReplicatedReplacingMergeTree(")


def test_the_reader_role_holds_select_over_both_tables(deployed):
    """26.3 folds each table's own GRANT into the role's database-wide one, so either row counts."""
    grants = deployed.rows(
        "SELECT ifNull(table, '*') AS table FROM system.grants "
        f"WHERE role_name = '{READER}' AND access_type = 'SELECT' AND database = '{DB}'"
    )
    granted = {row["table"] for row in grants}
    for table in (usage.USAGE_MINUTE_TABLE, usage.USAGE_DAILY_TABLE):
        assert "*" in granted or table in granted, granted


def test_a_user_holding_only_the_reader_role_reads_both_tables(deployed):
    deployed.run("CREATE USER IF NOT EXISTS usage_reader_test IDENTIFIED WITH no_password")
    deployed.run(f"GRANT {READER} TO usage_reader_test")
    deployed.run(f"ALTER USER usage_reader_test DEFAULT ROLE {READER}")
    for table in (usage.USAGE_MINUTE_TABLE, usage.USAGE_DAILY_TABLE):
        rows = deployed.rows(f"SELECT count() AS n FROM {DB}.{table}", user="usage_reader_test")
        assert rows[0]["n"] >= 0


def _refreshes(server: ClickHouse) -> dict[str, dict[str, Any]]:
    rows = server.rows(
        "SELECT view, status, exception, last_refresh_replica, "
        "toUnixTimestamp(last_success_time) AS last_success "
        f"FROM system.view_refreshes WHERE database = '{DB}'"
    )
    return {row["view"]: row for row in rows}


def _refresh(server: ClickHouse, view: str) -> None:
    """Trigger a refresh that starts after everything written so far, and wait for it."""
    written = server.scalar("SELECT toUnixTimestamp(now())")
    while server.scalar("SELECT toUnixTimestamp(now())") <= written:
        time.sleep(0.2)
    after = server.scalar("SELECT toUnixTimestamp(now())")
    server.run(f"SYSTEM REFRESH VIEW {DB}.{view}")
    deadline = time.monotonic() + REFRESH_SECONDS
    while time.monotonic() < deadline:
        state = _refreshes(server)[view]
        if (state["last_success"] or 0) >= after and state["status"] != "Running":
            return
        time.sleep(0.5)
    pytest.fail(f"{view} did not refresh in {REFRESH_SECONDS}s: {_refreshes(server)[view]}")


def test_every_usage_view_refreshes_coordinated_through_the_replicated_database(deployed):
    """last_refresh_replica is set only when Keeper coordinates the refresh, and empty in Atomic."""
    for view in USAGE_MVS:
        _refresh(deployed, view)
    states = _refreshes(deployed)
    assert set(states) == set(USAGE_MVS)
    for view, state in states.items():
        assert state["exception"] == "", f"{view}: {state['exception']}"
        assert state["last_refresh_replica"] == "r1", view


def test_the_materialised_views_roll_up_the_current_minutes(deployed):
    """The live path: recent telemetry, each view refreshed, rows in both tables."""
    m0 = deployed.scalar("SELECT toString(toStartOfMinute(now()) - INTERVAL 3 MINUTE)")
    later = deployed.scalar(f"SELECT toString(toDateTime('{m0}', 'UTC') + INTERVAL 30 SECOND)")
    started = deployed.scalar(f"SELECT toString(toDateTime('{m0}', 'UTC') - INTERVAL 1 HOUR)")
    request, phase = usage.K8S_CPU_REQUEST_METRIC, usage.K8S_POD_PHASE_METRIC
    _insert_gauges(
        deployed,
        [
            (request, _container("pe", "recv", "node-e"), m0, 1.5),
            (phase, _pod("pe", "node-e"), m0, 2),
        ],
    )
    node = _map({usage.K8S_NODE_NAME_ATTRIBUTE: "node-e"})
    org_z = _map({usage.ORG_ID_ATTRIBUTE: "z"})
    r9 = _map({"service.instance.id": "r9"})
    counter = usage.INGESTED_BYTES_METRIC
    _insert_sums(
        deployed,
        [
            ("k8s-node", usage.NODE_LOGICAL_CPU_METRIC, node, "map()", m0, later, 6),
            ("dfe-receiver", counter, r9, org_z, started, m0, 100),
            ("dfe-receiver", counter, r9, org_z, started, later, 160),
        ],
    )
    _insert_events(deployed, "main", m0, 500, "'z'", 90)

    for view in USAGE_MVS:
        _refresh(deployed, view)

    minute = {
        (row["metric"], row["_org_id"]): row["value"]
        for row in deployed.rows(
            f"SELECT metric, _org_id, value FROM {DB}.usage_minute FINAL "
            f"WHERE minute = toDateTime('{m0}', 'UTC')"
        )
    }
    assert minute == {
        (usage.CORES_DEPLOYED, ""): 1.5,
        (usage.NODE_CORES, ""): 6,
        (usage.INGESTED_BYTES, "z"): 160,
        (usage.INGESTED_BYTES, ""): 160,
        (usage.EVENTS_PER_SECOND, "z"): 1.5,
        (usage.EVENTS_PER_SECOND, ""): 1.5,
    }
    daily = {
        (row["metric"], row["_org_id"]): row["value"]
        for row in deployed.rows(
            f"SELECT metric, _org_id, value FROM {DB}.usage_daily FINAL "
            f"WHERE day = toDate(toDateTime('{m0}', 'UTC'), 'UTC') AND _org_id IN ('z', '')"
        )
    }
    assert daily[(usage.CORES_DEPLOYED, "")] >= 1.5
    assert daily[(usage.INGESTED_BYTES, "z")] == 160
    assert daily[(usage.EVENTS_PER_SECOND, "z")] == 1.5


# -- one series at a time, through the ingest view, on a day no refresh reaches --


@pytest.fixture(scope="module")
def old_day(deployed: ClickHouse) -> str:
    """A UTC day twenty back: inside the otel retention, outside every refresh window."""
    return deployed.scalar("SELECT toString(toDate(now(), 'UTC') - 20)")


def _ingest_minutes(server: ClickHouse, org: str, day: str) -> dict[str, float]:
    rows = server.rows(
        f"SELECT toString(minute) AS minute, value FROM {DB}.usage_minute_ingest("
        f"from = '{day} 00:00:00', to = '{day} 23:59:00') WHERE _org_id = '{org}'"
    )
    return {row["minute"]: row["value"] for row in rows}


def _receiver_sample(org: str, start: str, at: str, value: float) -> tuple:
    resource = _map({"service.instance.id": f"r-{org}"})
    attributes = _map({usage.ORG_ID_ATTRIBUTE: org})
    return ("dfe-receiver", usage.INGESTED_BYTES_METRIC, resource, attributes, start, at, value)


def test_a_restart_that_climbs_past_the_last_value_counts_from_zero(deployed, old_day):
    """StartTimeUnix moved, so 150 is 150 bytes since the restart rather than 50 more."""
    restart = f"{old_day} 03:00:30"
    _insert_sums(
        deployed,
        [
            _receiver_sample("restarted", f"{old_day} 02:00:00", f"{old_day} 03:00:00", 100),
            _receiver_sample("restarted", restart, f"{old_day} 03:01:00", 150),
            _receiver_sample("restarted", restart, f"{old_day} 03:02:00", 180),
        ],
    )
    assert _ingest_minutes(deployed, "restarted", old_day) == {
        f"{old_day} 03:00:00": 100,
        f"{old_day} 03:01:00": 150,
        f"{old_day} 03:02:00": 30,
    }


def test_a_replayed_sample_counts_once(deployed, old_day):
    """A retried export repeats a point: the same series, start, time and value."""
    first = _receiver_sample("replayed", f"{old_day} 05:00:00", f"{old_day} 05:00:30", 100)
    second = _receiver_sample("replayed", f"{old_day} 05:00:30", f"{old_day} 05:01:00", 40)
    _insert_sums(deployed, [first, first, second, second], temporality=1)
    assert _ingest_minutes(deployed, "replayed", old_day) == {
        f"{old_day} 05:00:00": 100,
        f"{old_day} 05:01:00": 40,
    }

