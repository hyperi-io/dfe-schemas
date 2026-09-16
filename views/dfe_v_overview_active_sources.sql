-- Active sources in the last hour. `{db}` is substituted with the deployment's
-- data database at apply time - never write the name here.
CREATE OR REPLACE VIEW {db}.dfe_v_overview_active_sources AS
SELECT
    uniqExact(_source) AS active_sources,
    uniqExact(_org_id) AS active_orgs,
    count() AS rows_last_hour
FROM {db}.main
WHERE _timestamp_load >= now() - INTERVAL 1 HOUR
