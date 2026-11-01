-- The days usage_daily_mv recomputes on every refresh: today and the two UTC
-- days before it, and further back to the oldest minute a minute rollup wrote
-- since this view's last write, at most five days back. The minute rollups
-- rewrite up to two hours back, which reaches into yesterday for the first two
-- hours of a day, and further after a stalled refresh resumes. The ten minutes
-- of overlap catch a minute row written while the last daily refresh ran.
CREATE OR REPLACE VIEW {db}.usage_daily_recent AS
SELECT day, metric, _org_id, value
FROM {db}.usage_daily_rollup(
    from = greatest(
        toDate(now(), 'UTC') - 5,
        least(
            toDate(now(), 'UTC') - 2,
            ifNull(
                (
                    SELECT toDate(minOrNull(minute), 'UTC')
                    FROM {db}.usage_minute
                    WHERE minute >= toDateTime(toDate(now(), 'UTC') - 5, 'UTC')
                      AND computed_at > (SELECT max(computed_at) FROM {db}.usage_daily)
                          - INTERVAL 10 MINUTE
                ),
                toDate(now(), 'UTC') - 2
            )
        )
    ),
    to = toDate(now(), 'UTC') + 1
)
