-- Each node's logical CPU count, per hostmetrics collection tick, for the usage
-- rollup. This is the Kubernetes source: the node is the one the collector
-- daemonset stamps, the same name a container's reading carries.
CREATE OR REPLACE VIEW {db}.usage_source_node_cores AS
SELECT
    TimeUnix AS tick,
    ResourceAttributes['${k8s_node_name_attribute}'] AS node,
    max(Value) AS cores
FROM {db}.otel_metrics_sum
WHERE MetricName = '${node_logical_cpu_metric}'
  AND ResourceAttributes['${k8s_node_name_attribute}'] != ''
  AND TimeUnix >= {from:DateTime}
  AND TimeUnix < {to:DateTime}
GROUP BY tick, node
