# dfe-schemas

The one place a DFE deployment's ClickHouse databases, tables, views, roles and grants and its Kafka bootstrap topics are defined. dfe-engine reads this package at boot and applies what is missing, so nothing else in a DFE deployment creates any of them.

It ships the schema trees as package data, with the renderer that turns them into ClickHouse DDL for the platform a connection reaches and into topic specs for Kafka. `load_manifest(root=...)` renders a tree of your own instead.

## What it gives you

- `manifest.yaml`: every object a deployment creates, in dependency order. A new table is an entry there plus its definition file, with no engine release.
- One table shape on every ClickHouse: the argumentless `Replicated<variant>` engine with no `ON CLUSTER`, which ClickHouse Cloud rewrites to `SharedMergeTree`.
- `PlatformProbe`, which tells ClickHouse Cloud from self-hosted on a live connection, and whose `require_replication()` refuses a self-hosted server with no Keeper.
- A per-object checksum, the same on every platform, so an applier can tell what changed.
- Kafka bootstrap topics with their replication clamped to the deployment's broker count.
- The ClickHouse role and grant catalogue, and the declared ClickHouse settings defaults every DFE deployment applies.
- The registries of table engines, column types and default field maps that dfe-engine validates source definitions against.

## Example

```console
uv add dfe-schemas
```

```python
from dfe_schemas.clickhouse import Platform
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer

manifest = load_manifest()
renderer = Renderer(manifest, platform=Platform.SELF_HOSTED, data_database="dfe")

first = renderer.render(manifest.objects[0])
print(first.id, first.checksum[:12])
for statement in first.statements:
    print(statement)
```

```text
db.data 8a39e4f8fa91
CREATE DATABASE IF NOT EXISTS dfe ON CLUSTER '{cluster}'
ENGINE = Replicated('/clickhouse/databases/dfe', '{shard}', '{replica}')
```

`Platform.CLOUD` renders the same object for ClickHouse Cloud. Against a live server, `PlatformProbe(client).platform` names it for you.

Docs and source are at <https://github.com/hyperi-io/dfe-schemas>. Requires Python 3.14 or later. Licensed BUSL-1.1: <https://github.com/hyperi-io/dfe-schemas/blob/main/LICENSE>.
