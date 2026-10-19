-- Cores per UTC minute, for usage_minute. Deployment-wide, so _org_id is the
-- all-orgs value.
--
-- cores_deployed: the CPU requested by every running DFE container at one tick,
-- summed, and the highest tick in the minute.
-- node_cores: the logical CPUs of every node that ran a DFE container at some
-- tick in the minute. The two receivers collect on unrelated ticks, so the
-- minute is the tick the node count is taken on.
CREATE OR REPLACE VIEW {db}.usage_minute_cores AS
WITH containers AS
(
    SELECT tick, node, cores
    FROM {db}.usage_source_container_cores(from = {from:DateTime}, to = {to:DateTime})
)
SELECT
    toStartOfMinute(toDateTime(tick, 'UTC')) AS minute,
    '${cores_deployed}' AS metric,
    '${all_orgs}' AS _org_id,
    max(tick_cores) AS value
FROM
(
    SELECT tick, sum(cores) AS tick_cores
    FROM containers
    GROUP BY tick
)
GROUP BY minute
UNION ALL
SELECT
    minute,
    '${node_cores}' AS metric,
    '${all_orgs}' AS _org_id,
    sum(node_cores) AS value
FROM
(
    SELECT toStartOfMinute(toDateTime(tick, 'UTC')) AS minute, node, max(cores) AS node_cores
    FROM {db}.usage_source_node_cores(from = {from:DateTime}, to = {to:DateTime})
    GROUP BY minute, node
) AS nodes
INNER JOIN
(
    SELECT toStartOfMinute(toDateTime(tick, 'UTC')) AS minute, node
    FROM containers
    GROUP BY minute, node
) AS dfe_nodes USING (minute, node)
GROUP BY minute
