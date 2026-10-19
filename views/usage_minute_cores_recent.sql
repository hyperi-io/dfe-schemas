-- The minutes usage_minute_cores_mv recomputes on every refresh: the last two
-- hours of complete minutes. A late reading lands in a minute that is still in
-- the window, so the next refresh replaces that minute's row.
CREATE OR REPLACE VIEW {db}.usage_minute_cores_recent AS
SELECT minute, metric, _org_id, value
FROM {db}.usage_minute_cores(
    from = toStartOfMinute(now() - INTERVAL 2 HOUR),
    to = toStartOfMinute(now())
)
