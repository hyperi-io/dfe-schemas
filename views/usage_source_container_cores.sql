-- The CPU each running DFE container requests, per k8s_cluster collection tick,
-- for the usage rollup. This is the Kubernetes source. A deployment that runs DFE
-- some other way swaps this view's body and keeps its columns.
--
-- A DFE container is one whose pod carries the part-of label, which k8sattributes
-- copies onto the k8s_cluster resource. A pod counts at a tick only when its own
-- phase reading at that same tick is Running: the receiver stamps every reading
-- of one collection with one timestamp. A second collector reporting the same
-- container at the same tick is taken once.
CREATE OR REPLACE VIEW {db}.usage_source_container_cores AS
SELECT
    request.TimeUnix AS tick,
    request.ResourceAttributes['${k8s_node_name_attribute}'] AS node,
    concat(
        request.ResourceAttributes['${k8s_pod_uid_attribute}'],
        '/',
        request.ResourceAttributes['${k8s_container_name_attribute}']
    ) AS container,
    max(request.Value) AS cores
FROM {db}.otel_metrics_gauge AS request
INNER JOIN
(
    SELECT TimeUnix, ResourceAttributes['${k8s_pod_uid_attribute}'] AS pod_uid
    FROM {db}.otel_metrics_gauge
    WHERE MetricName = '${k8s_pod_phase_metric}'
      AND Value = ${k8s_pod_phase_running}
      AND TimeUnix >= {from:DateTime}
      AND TimeUnix < {to:DateTime}
    GROUP BY TimeUnix, pod_uid
) AS running
    ON running.TimeUnix = request.TimeUnix
   AND running.pod_uid = request.ResourceAttributes['${k8s_pod_uid_attribute}']
WHERE request.MetricName = '${k8s_cpu_request_metric}'
  AND request.ResourceAttributes['${part_of_attribute}'] = '${part_of_value}'
  AND request.TimeUnix >= {from:DateTime}
  AND request.TimeUnix < {to:DateTime}
GROUP BY tick, node, container
