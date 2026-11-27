# Renderer

The renderer turns one manifest object into the statements that create it on one ClickHouse platform, and is one part of [dfe-schemas](architecture.md).

| Fact | Value |
|---|---|
| Repo | `hyperi-io/dfe-schemas`, module `dfe_schemas.render` |
| Package | the `dfe-schemas` wheel on PyPI, class `Renderer` |
| Config | the platform, the data database, the default TTL, the broker count and Kafka tiered storage, all passed by dfe-engine |
| Endpoints | none, it is a library |
| Metrics | none. `make render` renders every object for both platforms |

The renderer runs inside dfe-engine and only builds statements. Applying them is the engine's job.

```mermaid
---
config:
  theme: base
  look: neo
  fontFamily: Inter, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif
  flowchart:
    curve: linear
    nodeSpacing: 40
    rankSpacing: 64
    padding: 18
    wrappingWidth: 280
    htmlLabels: true
  elk:
    preset: modelOrder
  themeVariables:
    fontSize: 15px
    lineColor: "#64748B"
    primaryTextColor: "#0F172A"
    nodeBorder: "#334155"
    clusterBorder: "#0072B2"
    edgeLabelBackground: "#FFFFFF"
  themeCSS: >-
    p{margin:0}
    .node[data-look] rect,.node[data-look] path,.node[data-look] polygon,.node[data-look] .outer-path{filter:drop-shadow(0 1px 2px rgba(15,23,42,.10)) drop-shadow(0 6px 14px rgba(15,23,42,.10))}
    .node rect{rx:10px;ry:10px}
    .cluster rect{rx:16px;ry:16px}
    .node .label div{min-width:150px}
    .node.decision .label div{min-width:0}
    .cluster[data-look] rect{filter:none}
    .nodeLabel{line-height:1.35}
    .nodeLabel b{font-weight:600;font-size:15px;letter-spacing:-0.01em}
    .nodeLabel span{font-size:12.5px;font-weight:400;color:#334155}
    .cluster-label span.nodeLabel{display:inline-block;background:#FFFFFF;color:#0B2A3F;border:1.5px solid #0072B2;border-radius:999px;padding:2px 12px;font-size:11.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase}
    .flowchart-link{stroke-width:1.75px}
    .labelBkg{background:transparent!important}
    span.edgeLabel{display:inline-block;background:#FFFFFF!important;color:#1E293B;border:1px solid #94A3B8;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:500}
    span.edgeLabel p{background:transparent!important}
    .label-icon{width:20px;height:20px;vertical-align:-4px;margin-right:6px}
    .icon-shape path[stroke-width="0"][fill="none"]{fill:#FFFFFF;stroke:#FFFFFF;stroke-width:12px;stroke-linejoin:round}
    .icon-shape p{background:transparent!important;color:#0F172A}
    .ingest .label-icon{color:#0E8A7D}
    .xform .label-icon{color:#7B4FB0}
    .load .label-icon{color:#0072B2}
    .bus .label-icon{color:#A8517F}
    .store .label-icon{color:#00785A}
    .ui .label-icon{color:#D9480F}
    .ext .label-icon{color:#64748B}
---
flowchart TB
  accTitle: L4 Detail -- Renderer
  accDescr: The renderer takes the manifest, the definitions and the platform the probe names. It builds the table DDL, adds the platform DDL and takes a checksum that leaves the platform out, then hands statements and checksum to dfe-engine.
  manifest("<b>Manifest</b><br/><span>object and database</span>")
  defs[["<b>Definitions</b><br/><span>columns, SQL, policy</span>"]]
  probe("<b>Platform probe</b><br/><span>Cloud or self-hosted</span>")
  subgraph rend["Renderer"]
    direction LR
    ddl("<b>Table DDL</b><br/><span>keys, indexes, TTL</span>")
    plat("<b>Platform DDL</b><br/><span>engine, database, settings</span>")
    sum("<b>Checksum</b><br/><span>platform left out</span>")
    ddl --> plat --> sum
  end
  app("<b>dfe-engine</b><br/><span>compares and applies</span>")
  manifest --> rend
  defs --> rend
  probe --> rend
  rend -->|statements, checksum| app
  class manifest,defs,probe part
  class ddl,plat,sum part
  class app control
  classDef ext fill:#F4F6F8,stroke:#94A3B8,stroke-width:1.5px,stroke-dasharray:5 4,color:#0F172A
  classDef ingest fill:#FFFFFF,stroke:#0E8A7D,stroke-width:3px,color:#0F172A
  classDef xform fill:#FFFFFF,stroke:#7B4FB0,stroke-width:3px,color:#0F172A
  classDef load fill:#FFFFFF,stroke:#0072B2,stroke-width:3px,color:#0F172A
  classDef bus fill:#FFFFFF,stroke:#A8517F,stroke-width:3px,color:#0F172A
  classDef store fill:#FFFFFF,stroke:#00785A,stroke-width:3px,color:#0F172A
  classDef ui fill:#FFFFFF,stroke:#D9480F,stroke-width:3px,color:#0F172A
  classDef decision fill:#FFFFFF,stroke:#9A6700,stroke-width:3px,color:#0F172A
  classDef error fill:#FFFFFF,stroke:#C2410C,stroke-width:3px,color:#0F172A
  classDef control fill:#FFFFFF,stroke:#334155,stroke-width:3px,color:#0F172A
  classDef part fill:#FFFFFF,stroke:#94A3B8,stroke-width:1.5px,color:#0F172A
  style rend fill:none,stroke:#0072B2,stroke-width:1.5px,stroke-dasharray:6 4
```

*Figure 1. L4 Detail -- Renderer.* Up: [L3 Components -- dfe-schemas](architecture.md). Down: none.

## How it works

1. **Table DDL** builds the columns, sorting key, skip indexes and TTL. Each index comes from the column's use case in `registries/ddl.yaml`.
2. **Platform DDL** writes the argumentless `Replicated<variant>` engine with no `ON CLUSTER`, through `engine_clause()`. Cloud rewrites it to `SharedMergeTree`, and a self-hosted server without Keeper is rejected rather than given a plain form. The CREATE DATABASE and a JSON table's platform-only settings are written once per platform in `dfe_schemas/sql/platform.sql`, and the **Platform probe** picks the block, once per connection.
3. **Checksum** is a sha256 over the statements with the platform token removed and whitespace collapsed.

A table with an engine that stores no rows is the one exception to step 2. `usage_events` is a `Merge` table over dfe-engine's `all_sources`, so it takes the engine it names.

## Interfaces

| Call | Returns |
|---|---|
| `Renderer(manifest, platform=..., data_database=..., default_ttl_days=..., broker_count=..., kafka_tiered_storage=...)` | A renderer bound to one deployment |
| `render_table(...)` | The statements for one table |
| `checksum(statements)` | The platform-free sha256 dfe-engine records |
| `PlatformProbe` | The platform behind one connection, asked at most once |

The checksum leaves out the database's `ON CLUSTER` and `ENGINE`, a table's `Replicated` prefix and empty argument list, and the platform-only settings. One schema reads as one checksum on Cloud and self-hosted, so the applier's ledger answers "has this object changed" rather than "was it applied somewhere else". A `.sql` view's opening comment block is stripped first, so a reworded comment never reads as a changed view.

## Scaling and failure

A render runs once per object at engine boot, and nothing in it grows with data volume. An object that breaks a check raises rather than render. The checks are listed in [CONTRIBUTING.md Context](../CONTRIBUTING.md#context).

## Related

- [ClickHouse platforms](clickhouse-platform.md): the probe and what a self-hosted server needs
- [Manifest](manifest.md), [Definitions](definitions.md)
