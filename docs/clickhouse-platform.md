# ClickHouse platforms: one probe, one DDL

Every DFE table is created with the same DDL on every ClickHouse. What differs between ClickHouse Cloud and self-hosted ClickHouse is detected from the server, so pointing a deployment at the other platform works with no setting changed. An operator can still pin the platform where the probe is wrong for them.

## One DDL, and what it needs

Every table takes the argumentless `Replicated<variant>` engine and no `ON CLUSTER`:

```sql
CREATE TABLE IF NOT EXISTS `dfe`.`hunt_lease` ( ... )
ENGINE = ReplicatedReplacingMergeTree(claimed)
```

Cloud rewrites it to `SharedMergeTree` on CREATE, plain `MergeTree` included (measured on Cloud 26.6). Self-hosted takes it inside a Replicated database, which supplies the replica path and name and replicates every DDL issued inside it to every replica. `dfe_schemas.clickhouse.engine_clause()` is the one place the form is written, and a variant's own arguments, such as a version column, are kept.

The database is the only statement that fans out. On self-hosted it is:

```sql
CREATE DATABASE IF NOT EXISTS dfe ON CLUSTER '{cluster}'
ENGINE = Replicated('/clickhouse/databases/dfe', '{shard}', '{replica}')
```

Inside a Replicated database ClickHouse refuses `ON CLUSTER` on a table outright (code 80), so nothing else may carry it. It also refuses a Keeper path and replica name written into the engine (code 36). `scripts/render_manifest.py` and `tests/test_render.py` fail the build on any table that is not `Replicated*`, any table naming its own Keeper path, and any other statement carrying `ON CLUSTER`.

The Replicated engines deduplicate an insert block byte-identical to a recent one (`insert_deduplicate`), and so a single node now deduplicates too, where plain `MergeTree`'s window was 0. That absorbs only a resend of the same block. A retry that `async_insert` coalesces with other rows forms a different block and lands again, so at-least-once delivery can still store a duplicate, and that is accepted. A writer whose identical rows must all land, such as a ledger, sends `insert_deduplicate = 0`.

Self-hosted ClickHouse therefore needs Keeper (embedded `keeper_server` on a single node, an ensemble on a cluster), the `{cluster}`, `{shard}` and `{replica}` macros, and a `remote_servers` cluster named by `{cluster}`. A single node declares a cluster of one. Keeper's data lives on the same persistent volume as ClickHouse's: lose one without the other and every replicated table goes read-only until `SYSTEM RESTORE REPLICA`.

A cluster of more than one server also needs its SQL users and roles in Keeper. DFE creates them once, and a `local_directory` storage keeps them on the one server that ran the statement. The `user_directories` block must REPLACE the default. Appending `<replicated>` leaves `local_directory` ahead of it, and the first writable storage takes every `CREATE ROLE`. A single node may keep local access storage.

A server missing any of these is refused at startup with a message naming what is missing. There is no plain `MergeTree` fallback, because a second engine form is how one table ended up plain on one replica and replicated on the others. A single node, for reference, with the access storage a cluster needs:

```xml
<clickhouse>
    <keeper_server>
        <tcp_port>9181</tcp_port>
        <server_id>1</server_id>
        <log_storage_path>/var/lib/clickhouse/coordination/log</log_storage_path>
        <snapshot_storage_path>/var/lib/clickhouse/coordination/snapshots</snapshot_storage_path>
        <raft_configuration>
            <server><id>1</id><hostname>localhost</hostname><port>9234</port></server>
        </raft_configuration>
    </keeper_server>
    <zookeeper><node><host>localhost</host><port>9181</port></node></zookeeper>
    <macros><cluster>dfe</cluster><shard>01</shard><replica>r1</replica></macros>
    <remote_servers>
        <dfe><shard><replica><host>localhost</host><port>9000</port></replica></shard></dfe>
    </remote_servers>
    <user_directories replace="replace">
        <users_xml><path>users.xml</path></users_xml>
        <replicated><zookeeper_path>/clickhouse/access/</zookeeper_path></replicated>
    </user_directories>
</clickhouse>
```

## The probe, and the SQL it picks from

`PlatformProbe` names the platform behind one connection and asks the server at most once. First match wins:

1. an override: `cloud` or `self_hosted` from a deployment setting (dfe-engine's `clickhouse.platform`), a test or a call site. `auto` is no override
2. `SELECT count() FROM system.table_engines WHERE name = 'SharedMergeTree'`, where more than 0 is Cloud
3. the hostname when there is no client: `*.clickhouse.cloud` or `*.clickhouse-byoc.com` is Cloud
4. self-hosted

`cloud_mode` is not the signal, because any server can set it (ClickHouse #76545).

ClickHouse cannot branch its own DDL. `ENGINE = if(...)` is refused (code 56 self-hosted, 497 Cloud) and so is an expression in `SETTINGS` (code 62). So each difference is written out once per platform in `dfe_schemas/sql/platform.sql`:

```sql
-- @block json_table_settings self_hosted
write_marks_for_substreams_in_compact_parts = 0

-- @block json_table_settings cloud
```

| Block | `self_hosted` | `cloud` |
|---|---|---|
| `create_database` | Replicated database, `ON CLUSTER '{cluster}'` | `CREATE DATABASE`, which Cloud makes Shared |
| `json_table_settings` | `write_marks_for_substreams_in_compact_parts = 0` | nothing |

`dfe_schemas/variants.py` picks a block by the probe's answer. It knows nothing about ClickHouse, and it fails the load when a block leaves a platform out. A new Cloud-versus-self-hosted difference is a new block in that file, never an `if` in a caller.

dfe-engine realigns existing JSON tables on every schema apply: `MODIFY SETTING` where a self-hosted table lacks the setting, `RESET SETTING` where a Cloud table carries it. A setting no platform manages is left alone.
