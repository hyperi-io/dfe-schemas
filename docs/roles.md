# ClickHouse roles (`roles/clickhouse.yaml`)

`roles/clickhouse.yaml` is the catalogue of ClickHouse tiers, service roles, the tenant role and their grants. The manifest creates each role through the renderer, and dfe-engine's reconciler binds users to them. An operator edits the tiers through governed-ops, and the engine reconciles whatever tiers the catalogue holds rather than a list in its code.

`{db}` stands for the deployment's data database, filled in from `clickhouse.data_database` when the grants render. A literal name would grant on a database that a deployment which renamed its data database does not have.

A tier is what an identity can do and how much it can consume: grants, a settings profile and a quota. A service role is a fixed identity for one component. A role with `mint_user: true` gets a ClickHouse user and a secret from the secrets provider, and the others are granted to an existing identity when it is bound.

## Org isolation

The tenant axis is one shared role, `dfe_tenant_role`, plus a per-user `SQL_current_tenant_id` setting pinned READONLY on each org user. A READONLY user setting rejects any override, including a `SETTINGS` clause in query text the user wrote, with code 452. The server must allow the `SQL_` custom-settings prefix. There is no per-org database: row policies fence an org to its `_org_id` rows, and a `USING 0` policy hides the tables without `_org_id`.

Every policy is RESTRICTIVE. A user without the tenant role is targeted by no policy and reads everything, and a holder reads the ids pinned on it. A PERMISSIVE policy would turn the table to default-deny for everyone else.

A tenant reaches six system tables: `columns`, `merge_tree_settings`, `settings`, `shared_merge_tree_settings`, `table_engines` and `tables`. HyperDX builds its field list from `system.columns` and detects server capabilities from the other five, so a tenant without them sees an empty source or a failed metadata call (code 497, which the HyperDX proxy returns as a 500). `merge_tree_settings` is the server's MergeTree defaults, one row per setting, the same shape as `system.settings`. HyperDX reads `shared_merge_tree_settings` on ClickHouse Cloud only. A self-hosted server has no such table, and ClickHouse 26.3 still accepts the grant. None of the six describes the cluster, other tenants or query history.

## Settings profiles and caps

A profile setting is a plain value, a mapping of `value` with `min` and `max`, or `value` with `const: true`. ClickHouse rejects a query that sets a constrained setting outside its bounds (code 452). Under `readonly: 2` a query may change any unconstrained setting, so a cap written as a plain value is a default the query can lift. The memory and time caps are constraints for that reason.

Time caps allow at least 60 seconds whatever the default, because HyperDX sends its team `queryTimeout` (60 unless the team sets one) on every query. `max_rows_to_read` stays a plain value because HyperDX's filter and key queries send `max_rows_to_read=0`.

The analyst tiers and the `query_reader` service role take `readonly: 2`, not 1. Clients such as HyperDX and BI tools send output settings like `date_time_output_format` with every query, and `readonly: 1` rejects the whole request. Hunt tiers read the data and write detections, so they are not read-only.

A whole org shares one pinned ClickHouse user, and a BI page fires several queries per view, so the hourly quotas are sized for an org rather than one person. The `errors` cap is generous for the same reason: a tight one locks the whole org out for the interval. Every analyst tier grants the whole data database, so a new source table is readable with no admin action and isolation comes from the row policy, not the grant.

The service roles:

| Role | What it holds |
|---|---|
| `loader` | Insert on the data database, and reads of `system.columns` and `system.tables` for its target schemas |
| `query_reader` | The restricted reader the platform HyperDX connection and the KEDA shim use, with the introspection grants the ClickHouse dashboards query |
| `hunt_runner` | Select and insert on the data database for the `INSERT ... SELECT` it builds from rule text, and `system.replicas` for how far behind each replica is. No `url()`, `s3()`, `remote()` or DDL |
| `otel_writer` | Insert on the OTel tables, a read of `otel_traces` for the materialised view its inserts fire, and the health scrape |
| `otel_reader` | Read on the data database, composed onto admin and infra-admin group users when they are bound, and never part of an analyst tier |

The introspection tables are named one by one rather than `system.*`, so a tenant never reaches them. `merge('system', '^metric_log')` skips a table the user cannot read, so when an upgrade rotates `metric_log` to `metric_log_0` the older history leaves the charts until that name is added to the list.

Each view grants SELECT to `query_reader` as part of its apply.
