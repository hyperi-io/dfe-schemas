-- The minutes usage_minute_ingest_mv recomputes on every refresh: from the last
-- minute it wrote, or two hours back when that is later, to the last complete
-- minute, read with the view's own six-hour lookback. A refresh that failed for
-- longer resumes from its last minute, at most five days back, the furthest
-- usage_daily_mv recomputes.
CREATE OR REPLACE VIEW {db}.usage_minute_ingest_recent AS
SELECT minute, metric, _org_id, value
FROM {db}.usage_minute_ingest(
    from = greatest(
        toStartOfMinute(now() - INTERVAL 5 DAY),
        least(
            toStartOfMinute(now() - INTERVAL 2 HOUR),
            (SELECT max(minute) FROM {db}.usage_minute WHERE metric = '${ingested_bytes}')
        )
    ),
    to = toStartOfMinute(now())
)
