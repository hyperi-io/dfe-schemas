#  Project:      dfe-schemas
#  File:         dfe_schemas/usage.py
#  Purpose:      The usage metrics contract: what the rollup reads and what it writes
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The usage metrics contract, declared once.

The usage rollup reads telemetry other components emit: the k8s_cluster and
hostmetrics receivers in dfe-infra's collector, the ingest counter scalo gives
dfe-receiver and dfe-fetcher, and the ``all_sources`` Merge table dfe-engine
keeps. Every name it reads is a constant here, and the ``views/usage_*.sql``
definitions name them as ``${placeholder}`` tokens the renderer fills from
:data:`SQL_VALUES`, so the SQL and an importer of this module cannot disagree.

It writes one row per metric, scope and minute to ``usage_minute`` and per
metric, scope and UTC day to ``usage_daily``. ``_org_id`` is
:data:`ALL_ORGS` on a deployment-wide row.
"""

from dataclasses import dataclass

__all__ = [
    "ALL_ORGS",
    "CORES_DEPLOYED",
    "EVENTS_PER_SECOND",
    "INGESTED_BYTES",
    "INGESTED_BYTES_METRIC",
    "K8S_CONTAINER_NAME_ATTRIBUTE",
    "K8S_CPU_REQUEST_METRIC",
    "K8S_NODE_NAME_ATTRIBUTE",
    "K8S_POD_PHASE_METRIC",
    "K8S_POD_PHASE_RUNNING",
    "K8S_POD_UID_ATTRIBUTE",
    "METRICS",
    "NODE_CORES",
    "NODE_LOGICAL_CPU_METRIC",
    "ORG_ID_ATTRIBUTE",
    "PART_OF_ATTRIBUTE",
    "PART_OF_VALUE",
    "SQL_VALUES",
    "USAGE_DAILY_TABLE",
    "USAGE_MINUTE_TABLE",
    "UsageMetric",
]

# -- what the rollup reads ---------------------------------------------------

# k8s_cluster gauges: the CPU a container requests, and its pod's phase.
K8S_CPU_REQUEST_METRIC = "k8s.container.cpu_request"
K8S_POD_PHASE_METRIC = "k8s.pod.phase"
# The receiver's phase numbering: 1 Pending, 2 Running, 3 Succeeded, 4 Failed, 5 Unknown.
K8S_POD_PHASE_RUNNING = 2
K8S_POD_UID_ATTRIBUTE = "k8s.pod.uid"
K8S_CONTAINER_NAME_ATTRIBUTE = "k8s.container.name"
K8S_NODE_NAME_ATTRIBUTE = "k8s.node.name"

# The pod label k8sattributes copies onto the k8s_cluster resource, which marks a DFE container.
PART_OF_ATTRIBUTE = "app.kubernetes.io/part-of"
PART_OF_VALUE = "dfe"

# hostmetrics: a node's logical CPU count, a non-monotonic sum.
NODE_LOGICAL_CPU_METRIC = "system.cpu.logical.count"

# The first-hop ingest counter: decoded payload bytes per accepted record, by org.
INGESTED_BYTES_METRIC = "usage_ingested_bytes_total"
ORG_ID_ATTRIBUTE = "org_id"

# -- what the rollup writes --------------------------------------------------

USAGE_MINUTE_TABLE = "usage_minute"
USAGE_DAILY_TABLE = "usage_daily"

# The _org_id of a row that covers the whole deployment rather than one org.
ALL_ORGS = ""

CORES_DEPLOYED = "cores_deployed"
NODE_CORES = "node_cores"
INGESTED_BYTES = "ingested_bytes"
EVENTS_PER_SECOND = "events_per_second"


@dataclass(frozen=True, slots=True)
class UsageMetric:
    """One metric the usage tables carry, and how a day of it is read.

    Attributes:
        name: The ``metric`` column value.
        unit: What ``value`` counts.
        per_org: Whether rows exist per org as well as for :data:`ALL_ORGS`.
        daily: ``max`` or ``sum``, the aggregate a day takes over its minutes.
    """

    name: str
    unit: str  # noqa: V107 - public API
    per_org: bool  # noqa: V107 - public API
    daily: str  # noqa: V107 - public API


METRICS: tuple[UsageMetric, ...] = (
    UsageMetric(CORES_DEPLOYED, unit="cores", per_org=False, daily="max"),
    UsageMetric(NODE_CORES, unit="cores", per_org=False, daily="max"),
    UsageMetric(INGESTED_BYTES, unit="bytes", per_org=True, daily="sum"),
    UsageMetric(EVENTS_PER_SECOND, unit="events/s", per_org=True, daily="max"),
)

# The ${name} tokens a usage view may carry, and the value each renders as. Every
# value lands inside a quoted SQL literal or as a bare number.
SQL_VALUES: dict[str, str] = {
    "k8s_cpu_request_metric": K8S_CPU_REQUEST_METRIC,
    "k8s_pod_phase_metric": K8S_POD_PHASE_METRIC,
    "k8s_pod_phase_running": str(K8S_POD_PHASE_RUNNING),
    "k8s_pod_uid_attribute": K8S_POD_UID_ATTRIBUTE,
    "k8s_container_name_attribute": K8S_CONTAINER_NAME_ATTRIBUTE,
    "k8s_node_name_attribute": K8S_NODE_NAME_ATTRIBUTE,
    "part_of_attribute": PART_OF_ATTRIBUTE,
    "part_of_value": PART_OF_VALUE,
    "node_logical_cpu_metric": NODE_LOGICAL_CPU_METRIC,
    "ingested_bytes_metric": INGESTED_BYTES_METRIC,
    "org_id_attribute": ORG_ID_ATTRIBUTE,
    "all_orgs": ALL_ORGS,
    "cores_deployed": CORES_DEPLOYED,
    "node_cores": NODE_CORES,
    "ingested_bytes": INGESTED_BYTES,
    "events_per_second": EVENTS_PER_SECOND,
}
