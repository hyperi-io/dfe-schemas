-- The minutes usage_minute_events_mv recomputes on every refresh: the last hour
-- of complete minutes. _timestamp_load is stamped on insert, so a minute only
-- moves while replication catches up, and the hour covers eleven missed refreshes.
CREATE OR REPLACE VIEW {db}.usage_minute_events_recent AS
SELECT minute, metric, _org_id, value
FROM {db}.usage_minute_events(
    from = toStartOfMinute(now() - INTERVAL 1 HOUR),
    to = toStartOfMinute(now())
)
