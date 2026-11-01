-- The minutes usage_minute_cores_mv recomputes on every refresh: from the last
-- minute it wrote, or two hours back when that is later, to the last complete
-- minute. A late reading lands in a minute that is still in the window, so the
-- next refresh replaces that minute's row. A refresh that failed for longer
-- resumes from its last minute, at most five days back, the furthest
-- usage_daily_mv recomputes.
CREATE OR REPLACE VIEW {db}.usage_minute_cores_recent AS
SELECT minute, metric, _org_id, value
FROM {db}.usage_minute_cores(
    from = greatest(
        toStartOfMinute(now() - INTERVAL 5 DAY),
        least(
            toStartOfMinute(now() - INTERVAL 2 HOUR),
            (
                SELECT max(minute) FROM {db}.usage_minute
                WHERE metric IN ('${cores_deployed}', '${node_cores}')
            )
        )
    ),
    to = toStartOfMinute(now())
)
