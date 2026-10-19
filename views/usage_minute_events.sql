-- Events loaded per UTC minute, as the minute's average per second, per org and
-- for all orgs, for usage_minute. Counted by _timestamp_load off usage_events,
-- the Merge table over the engine's all_sources, in one pass: each org's minute
-- also counts towards the all-orgs row.
CREATE OR REPLACE VIEW {db}.usage_minute_events AS
SELECT
    minute,
    '${events_per_second}' AS metric,
    scope AS _org_id,
    sum(events) / 60 AS value
FROM
(
    SELECT
        toStartOfMinute(toDateTime(_timestamp_load, 'UTC')) AS minute,
        toString(_org_id) AS org,
        count() AS events
    FROM {db}.usage_events
    WHERE _timestamp_load >= {from:DateTime}
      AND _timestamp_load < {to:DateTime}
    GROUP BY minute, org
)
ARRAY JOIN arrayDistinct([org, '${all_orgs}']) AS scope
GROUP BY minute, scope
