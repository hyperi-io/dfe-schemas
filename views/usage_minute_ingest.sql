-- Bytes ingested per UTC minute, per org and for all orgs, for usage_minute.
--
-- The counter is read per series (service, scope, resource and attributes), and
-- each sample adds its increase over the sample before it to the minute it falls
-- in. A cumulative sample lower than the one before is a counter reset, and adds
-- its whole value. A delta-temporality sample (OTLP AggregationTemporality 1) is
-- its own increase.
--
-- The read starts six hours before the window, so a series' first sample inside
-- the window has the one before it. A series with no earlier sample in the read
-- adds its value only when its StartTimeUnix falls inside the read: a counter
-- that started there counted from zero, one that started earlier has a baseline
-- the read cannot see. A sample with no org counts towards all orgs alone.
CREATE OR REPLACE VIEW {db}.usage_minute_ingest AS
SELECT
    toStartOfMinute(toDateTime(t, 'UTC')) AS minute,
    '${ingested_bytes}' AS metric,
    scope AS _org_id,
    sum(increase) AS value
FROM
(
    SELECT
        org,
        t,
        multiIf(
            temporality = 1, v,
            prev_t = toDateTime(0), if(started >= {from:DateTime} - INTERVAL 6 HOUR, v, 0),
            v >= prev_v, v - prev_v,
            v
        ) AS increase
    FROM
    (
        SELECT
            Attributes['${org_id_attribute}'] AS org,
            TimeUnix AS t,
            StartTimeUnix AS started,
            Value AS v,
            AggregationTemporality AS temporality,
            lagInFrame(Value, 1, 0) OVER series AS prev_v,
            lagInFrame(TimeUnix, 1, toDateTime(0)) OVER series AS prev_t
        FROM {db}.otel_metrics_sum
        WHERE MetricName = '${ingested_bytes_metric}'
          AND TimeUnix >= {from:DateTime} - INTERVAL 6 HOUR
          AND TimeUnix < {to:DateTime}
        WINDOW series AS (
            PARTITION BY ServiceName, ScopeName, ResourceAttributes, Attributes
            ORDER BY TimeUnix
            ROWS BETWEEN 1 PRECEDING AND CURRENT ROW
        )
    )
)
ARRAY JOIN arrayDistinct([org, '${all_orgs}']) AS scope
WHERE t >= {from:DateTime}
GROUP BY minute, scope
