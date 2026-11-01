-- The minutes usage_minute_events_mv recomputes on every refresh: from the last
-- minute it wrote, or an hour back when that is later, to the last complete
-- minute. _timestamp_load is stamped on insert, so a minute only moves while
-- replication catches up, and the hour covers eleven missed refreshes. A refresh
-- that failed for longer resumes from its last minute, at most five days back,
-- the furthest usage_daily_mv recomputes.
CREATE OR REPLACE VIEW {db}.usage_minute_events_recent AS
SELECT minute, metric, _org_id, value
FROM {db}.usage_minute_events(
    from = greatest(
        toStartOfMinute(now() - INTERVAL 5 DAY),
        least(
            toStartOfMinute(now() - INTERVAL 1 HOUR),
            (SELECT max(minute) FROM {db}.usage_minute WHERE metric = '${events_per_second}')
        )
    ),
    to = toStartOfMinute(now())
)
