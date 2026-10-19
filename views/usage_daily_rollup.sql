-- Each UTC day of usage_minute, for usage_daily: the day's total ingested bytes,
-- and the day's highest minute of every other metric.
CREATE OR REPLACE VIEW {db}.usage_daily_rollup AS
SELECT
    toDate(minute, 'UTC') AS day,
    metric,
    _org_id,
    if(metric = '${ingested_bytes}', sum(value), max(value)) AS value
FROM {db}.usage_minute FINAL
WHERE minute >= toDateTime({from:Date}, 'UTC')
  AND minute < toDateTime({to:Date}, 'UTC')
GROUP BY day, metric, _org_id
