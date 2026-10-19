-- The minutes usage_minute_ingest_mv recomputes on every refresh: the last two
-- hours of complete minutes, read with the view's own six-hour lookback.
CREATE OR REPLACE VIEW {db}.usage_minute_ingest_recent AS
SELECT minute, metric, _org_id, value
FROM {db}.usage_minute_ingest(
    from = toStartOfMinute(now() - INTERVAL 2 HOUR),
    to = toStartOfMinute(now())
)
