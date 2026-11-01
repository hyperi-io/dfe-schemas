-- One org's events per day and source, over the landing table. `{db}` is
-- substituted with the deployment's data database at apply time.
CREATE OR REPLACE VIEW {db}.dfe_v_analytics_event_counts AS
SELECT
    toDate(_timestamp, 'UTC') AS event_date,
    ifNull(_source, 'unknown') AS source,
    count() AS event_count
FROM {db}.main
WHERE _org_id = {org_id:String}
  AND _timestamp >= {time_from:DateTime64(3)}
  AND _timestamp < {time_to:DateTime64(3)}
GROUP BY event_date, source
ORDER BY event_date DESC, event_count DESC
LIMIT {limit:UInt32}
