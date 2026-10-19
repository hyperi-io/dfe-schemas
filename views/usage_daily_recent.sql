-- The days usage_daily_mv recomputes on every refresh: today and the two UTC
-- days before it. The minute rollups rewrite up to two hours back, which reaches
-- into yesterday for the first two hours of a day.
CREATE OR REPLACE VIEW {db}.usage_daily_recent AS
SELECT day, metric, _org_id, value
FROM {db}.usage_daily_rollup(
    from = toDate(now(), 'UTC') - 2,
    to = toDate(now(), 'UTC') + 1
)
