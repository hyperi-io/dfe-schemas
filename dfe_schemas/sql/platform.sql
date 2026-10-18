-- The ClickHouse DDL that differs between ClickHouse Cloud and self-hosted
-- ClickHouse: one block per difference, one variant per platform.
--
-- ClickHouse cannot branch its own DDL (ENGINE = if() and a SETTINGS expression
-- are both refused), so dfe_schemas.clickhouse probes the platform once per
-- connection and takes the variant here that matches. Every block names every
-- platform, an empty one included, so a missing variant fails the load.
--
-- ${name} is filled in by the renderer. {cluster}, {shard} and {replica} are
-- server macros and pass through to ClickHouse untouched.

-- @block create_database self_hosted
-- The only ON CLUSTER DFE renders: the database replicates every table DDL
-- issued inside it, and ClickHouse refuses ON CLUSTER there (code 80).
CREATE DATABASE IF NOT EXISTS ${database} ON CLUSTER '{cluster}'
ENGINE = Replicated('/clickhouse/databases/${database}', '{shard}', '{replica}')

-- @block create_database cloud
-- Cloud creates a Shared database, which replicates on its own.
CREATE DATABASE IF NOT EXISTS ${database}

-- @block json_table_settings self_hosted
-- Per-substream marks measured ~16 MB per compact part on a JSON table, 0.4 MB without.
write_marks_for_substreams_in_compact_parts = 0

-- @block json_table_settings cloud
-- Cloud keeps the default: its compact parts grow to ~1 GiB.
