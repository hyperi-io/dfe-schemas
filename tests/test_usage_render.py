#  Project:      dfe-schemas
#  File:         tests/test_usage_render.py
#  Purpose:      The usage rollup renders the shape that runs once per cluster and keeps its year
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""What the usage objects must render as, checked without a server.

A refreshable view that drops APPEND swaps its target for one refresh's result,
and one that takes ``all_replicas`` runs on every replica, so both would ship a
wrong number in every deployment while every query still succeeds. The names
the views read are the contract other components emit, so they come from
dfe_schemas.usage and nowhere else.
"""

import re
import shutil

import pytest

import dfe_schemas
from dfe_schemas import usage
from dfe_schemas.clickhouse import Platform
from dfe_schemas.loader import SchemaError
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer, ddl_violations

ROOT = dfe_schemas.schemas_root()
PLATFORMS = tuple(Platform)
USAGE_MVS = (
    "data.usage_minute_cores_mv",
    "data.usage_minute_ingest_mv",
    "data.usage_minute_events_mv",
    "data.usage_daily_mv",
)
PLACEHOLDER = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(root=ROOT)


@pytest.fixture(scope="module")
def rendered(manifest):
    return {
        platform: {
            obj.id: Renderer(manifest, platform=platform).render(obj) for obj in manifest.objects
        }
        for platform in PLATFORMS
    }


def _usage_views():
    return sorted((ROOT / "views").glob("usage_*.sql"))


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("object_id", USAGE_MVS)
def test_a_usage_view_refreshes_appending_and_coordinated(rendered, platform, object_id):
    """APPEND keeps history, and nothing opts a replica out of the shared schedule."""
    (statement,) = rendered[platform][object_id].statements
    assert re.search(r" REFRESH EVERY 5 MINUTE (OFFSET 1 MINUTE )?APPEND TO dfe\.usage_", statement)
    assert "all_replicas" not in statement
    assert "ON CLUSTER" not in statement
    assert "DEPENDS ON" not in statement
    assert ddl_violations(statement) == []


def test_the_daily_view_runs_a_minute_after_the_minute_views(rendered):
    statement = rendered[Platform.SELF_HOSTED]["data.usage_daily_mv"].statements[0]
    assert "REFRESH EVERY 5 MINUTE OFFSET 1 MINUTE APPEND TO dfe.usage_daily AS" in statement


def test_the_minute_table_keeps_seven_days_dropped_by_whole_day(rendered):
    sql = rendered[Platform.SELF_HOSTED]["data.usage_minute"].statements[0]
    assert "PARTITION BY toYYYYMMDD(minute)" in sql
    assert "TTL minute + INTERVAL 7 DAY DELETE WHERE minute >= 0" in sql
    assert "ttl_only_drop_parts = 1" in sql
    assert "ENGINE = ReplicatedReplacingMergeTree(computed_at)" in sql


def test_the_daily_table_keeps_four_hundred_days(rendered):
    sql = rendered[Platform.SELF_HOSTED]["data.usage_daily"].statements[0]
    assert "PARTITION BY toYYYYMM(day)" in sql
    assert "TTL day + INTERVAL 400 DAY DELETE\n" in sql
    assert "ttl_only_drop_parts = 1" in sql


def test_the_daily_retention_ignores_the_deployment_default(manifest):
    """A changed default must never shorten the audit year."""
    obj = next(o for o in manifest.objects if o.id == "data.usage_daily")
    sql = Renderer(manifest, default_ttl_days=30).render(obj).statements[0]
    assert "INTERVAL 400 DAY" in sql


@pytest.mark.parametrize("object_id", ["data.usage_minute", "data.usage_daily"])
def test_a_usage_table_grants_select_to_the_reader_role(rendered, object_id):
    table = object_id.split(".", 1)[1]
    statements = rendered[Platform.SELF_HOSTED][object_id].statements
    assert statements[-1] == f"GRANT SELECT ON dfe.{table} TO dfe_query_reader_role"


def test_the_events_source_is_a_merge_table_over_all_sources(rendered):
    for platform in PLATFORMS:
        (sql,) = rendered[platform]["data.usage_events"].statements
        assert sql.endswith("ENGINE = Merge(`dfe`, '^all_sources$')")
        assert ddl_violations(sql) == []


def test_a_merge_table_lands_in_the_deployment_database(manifest):
    obj = next(o for o in manifest.objects if o.id == "data.usage_events")
    sql = Renderer(manifest, data_database="warehouse").render(obj).statements[0]
    assert "ENGINE = Merge(`warehouse`, '^all_sources$')" in sql


def test_the_one_ddl_gate_still_refuses_a_plain_merge_tree():
    found = ddl_violations("CREATE TABLE d.t (x UInt8)\nENGINE = MergeTree\nORDER BY x")
    assert len(found) == 1
    assert "is not Replicated*" in found[0]


@pytest.mark.parametrize("path", _usage_views(), ids=lambda p: p.stem)
def test_every_usage_placeholder_names_a_contract_value(path):
    names = set(PLACEHOLDER.findall(path.read_text(encoding="utf-8")))
    assert names <= set(usage.SQL_VALUES), f"{path.name}: {sorted(names - set(usage.SQL_VALUES))}"


def test_no_usage_view_writes_a_contract_name_by_hand():
    """A literal beside the placeholder is a second copy that can drift."""
    for path in _usage_views():
        body = PLACEHOLDER.sub("", path.read_text(encoding="utf-8"))
        for name in (
            usage.K8S_CPU_REQUEST_METRIC,
            usage.K8S_POD_PHASE_METRIC,
            usage.NODE_LOGICAL_CPU_METRIC,
            usage.INGESTED_BYTES_METRIC,
            usage.PART_OF_ATTRIBUTE,
        ):
            assert name not in body, f"{path.name} writes {name!r} by hand"


def test_the_rendered_views_carry_the_contract_names(rendered):
    views = rendered[Platform.SELF_HOSTED]
    container = views["view.usage_source_container_cores"].statements[0]
    assert f"MetricName = '{usage.K8S_CPU_REQUEST_METRIC}'" in container
    assert f"MetricName = '{usage.K8S_POD_PHASE_METRIC}'" in container
    assert f"Value = {usage.K8S_POD_PHASE_RUNNING}" in container
    assert f"['{usage.PART_OF_ATTRIBUTE}'] = '{usage.PART_OF_VALUE}'" in container
    node = views["view.usage_source_node_cores"].statements[0]
    assert f"MetricName = '{usage.NODE_LOGICAL_CPU_METRIC}'" in node
    ingest = views["view.usage_minute_ingest"].statements[0]
    assert f"MetricName = '{usage.INGESTED_BYTES_METRIC}'" in ingest
    assert f"Attributes['{usage.ORG_ID_ATTRIBUTE}']" in ingest
    for obj in views.values():
        assert all("${" not in statement for statement in obj.statements), obj.id


def test_every_contract_value_sits_safely_in_a_sql_literal():
    for name, value in usage.SQL_VALUES.items():
        assert re.fullmatch(r"[A-Za-z0-9_./:-]*", value), f"{name}={value!r}"


def test_the_daily_rollup_sums_exactly_the_metrics_declared_as_sums(rendered):
    sql = rendered[Platform.SELF_HOSTED]["view.usage_daily_rollup"].statements[0]
    summed = {metric.name for metric in usage.METRICS if metric.daily == "sum"}
    assert summed == {usage.INGESTED_BYTES}
    assert f"if(metric = '{usage.INGESTED_BYTES}', sum(value), max(value))" in sql
    assert {metric.daily for metric in usage.METRICS} == {"sum", "max"}


def test_the_org_scoped_metrics_are_the_two_licensed_per_org():
    per_org = {metric.name for metric in usage.METRICS if metric.per_org}
    assert per_org == {usage.INGESTED_BYTES, usage.EVENTS_PER_SECOND}


# -- what the renderer refuses ---------------------------------------------


def _root(tmp_path, *, table: str, view: str | None = None):
    """A schemas root with the real registries and roles, one table and maybe one view."""
    for tree in ("registries", "roles"):
        shutil.copytree(ROOT / tree, tmp_path / tree)
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "t.yaml").write_text(table, encoding="utf-8")
    objects = """
  - id: db.data
    kind: database
    database: data
    topology: resolved
    additive: false
    depends_on: []
  - id: data.t
    kind: table
    database: data
    defines: tables/t
    topology: resolved
    additive: true
    depends_on: [db.data]
  - id: data.t_mv
    kind: materialized_view
    database: data
    defines: tables/t
    topology: resolved
    additive: false
    depends_on: [data.t]
"""
    if view is not None:
        (tmp_path / "views").mkdir()
        (tmp_path / "views" / "v.sql").write_text(view, encoding="utf-8")
        objects += """
  - id: view.v
    kind: view
    database: data
    defines: views/v
    topology: resolved
    additive: true
    depends_on: [db.data]
"""
    manifest = (
        "manifest_version: 1\n"
        "databases:\n  - key: data\n    parameter: data_database\n"
        f"objects:{objects}"
    )
    (tmp_path / "manifest.yaml").write_text(manifest, encoding="utf-8")
    return load_manifest(root=tmp_path)


def _table(*, engine: str, table_extra: str = "", refresh: str = "EVERY 5 MINUTE") -> str:
    return f"""
current: "1.0.0"
versions:
  "1.0.0":
    date: "2026-10-19"
    type: model
    summary: "test"
    table:
      name: "t"
      engine: "{engine}"{table_extra}
    materialized_view:
      name: t_mv
      to: t
      refresh: "{refresh}"
      select: |-
        SELECT 1 AS x
    columns:
      - name: "x"
        ch_type: "UInt8"
        order: 0
"""


def _render(manifest, object_id):
    obj = next(o for o in manifest.objects if o.id == object_id)
    return Renderer(manifest).render(obj)


@pytest.mark.parametrize(
    "refresh",
    [
        "EVERY 5 MINUTE SETTINGS all_replicas = 1",
        "EVERY 5 MINUTE DEPENDS ON dfe.other",
        "EVERY 0 MINUTE",
        "EVERY 5 MINUTES",
    ],
)
def test_a_refresh_outside_the_coordinated_shape_is_refused(tmp_path, refresh):
    manifest = _root(tmp_path, table=_table(engine="MergeTree", refresh=refresh))
    with pytest.raises(SchemaError, match="is not EVERY"):
        _render(manifest, "data.t_mv")


def test_a_merge_table_takes_no_merge_tree_clause(tmp_path):
    table = _table(
        engine="Merge({db}, '^x$')", table_extra='\n      ttl_days: 30\n      ttl_columns: ["x"]'
    )
    manifest = _root(tmp_path, table=table)
    with pytest.raises(SchemaError, match="stores no rows and takes none of"):
        _render(manifest, "data.t")


def test_an_unknown_placeholder_in_a_view_is_refused(tmp_path):
    view = "CREATE OR REPLACE VIEW {db}.v AS\nSELECT '${no_such_value}' AS x"
    manifest = _root(tmp_path, table=_table(engine="MergeTree"), view=view)
    with pytest.raises(SchemaError, match="no value for"):
        _render(manifest, "view.v")


def test_a_dollar_brace_that_is_no_placeholder_is_refused(tmp_path):
    view = "CREATE OR REPLACE VIEW {db}.v AS\nSELECT '${Not-A-Name}' AS x"
    manifest = _root(tmp_path, table=_table(engine="MergeTree"), view=view)
    with pytest.raises(SchemaError, match="is not a"):
        _render(manifest, "view.v")


def test_a_missing_keyed_view_is_refused(tmp_path):
    from dataclasses import replace

    manifest = _root(tmp_path, table=_table(engine="MergeTree"))
    obj = replace(next(o for o in manifest.objects if o.id == "data.t_mv"), key="gone")
    with pytest.raises(SchemaError, match="no materialized_views entry 'gone'"):
        Renderer(manifest).render(obj)
