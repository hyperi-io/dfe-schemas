# Manifest

`manifest.yaml` names every object a DFE deployment creates, in the order it creates them, and is one part of [dfe-schemas](architecture.md).

| Fact | Value |
|---|---|
| Repo | `hyperi-io/dfe-schemas`, file `manifest.yaml` |
| Package | the `dfe-schemas` wheel on PyPI, read by `dfe_schemas.manifest.load_manifest` |
| Config | the `parameters` block, each value supplied by a dfe-engine setting |
| Endpoints | none, it is a file |
| Metrics | none. `make render` prints the objects it rendered, by kind |

dfe-engine applies nothing that is not in the manifest, in the order it lists.

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
  accTitle: L4 Detail -- Manifest
  accDescr: The manifest holds parameters, databases and an ordered object list. Each object names a definition. The renderer reads both, and dfe-engine applies the objects in manifest order.
  subgraph mf["Manifest"]
    direction LR
    params("<b>Parameters</b><br/><span>values a deployment supplies</span>")
    dbs("<b>Databases</b><br/><span>addressed by key</span>")
    objs("<b>Objects</b><br/><span>the ordered list</span>")
    params ~~~ dbs ~~~ objs
  end
  subgraph next["Renders and applies"]
    direction LR
    defs[["<b>Definitions</b><br/><span>one file per object</span>"]]
    renderer("<b>Renderer</b><br/><span>checksummed statements</span>")
    engine("<b>dfe-engine</b><br/><span>applies in this order</span>")
    defs --> renderer --> engine
  end
  mf -->|defines, depends_on| next
  class params,dbs,objs part
  class defs,renderer part
  class engine control
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
  style mf fill:none,stroke:#0072B2,stroke-width:1.5px,stroke-dasharray:6 4
  style next fill:none,stroke:#0072B2,stroke-width:1.5px,stroke-dasharray:6 4
```

*Figure 1. L4 Detail -- Manifest.* Up: [L3 Components -- dfe-schemas](architecture.md). Down: none.

## How it works

1. dfe-engine loads the manifest at boot and applies nothing that is not in it.
2. Each object's `defines` names its definition file, and its `database` names a declared key.
3. The renderer substitutes the parameters and renders the objects in list order.
4. dfe-engine applies them in that order, so a `depends_on` always names something already created.

## Interfaces

The top-level keys:

| Key | Meaning |
|---|---|
| `manifest_version` | The format this file is written in. The reader rejects a version it does not know rather than guessing at the fields. |
| `parameters` | The deployment values the renderer substitutes: the data database, the default retention, Kafka tiered storage. Each names the setting that supplies it. The ClickHouse platform is not one of them: it is probed from the server. |
| `databases` | The databases objects land in, by KEY. A key has either `parameter` (the deployment supplies the name) or `name` (fixed). |
| `objects` | The list. |

A database is addressed by key rather than by name because a deployment can rename its data database. A literal would create the object, grant on it and query it in a database that deployment does not have.

Each entry under `objects` takes these keys:

| Key | Meaning |
|---|---|
| `id` | Unique across the manifest. Two definitions claiming one name means whichever applies second wins, so this is checked. |
| `kind` | `database`, `table`, `materialized_view`, `view`, `role` or `topic`. |
| `database` | The declared database key. A topic names none. |
| `defines` | The definition file, relative to the schemas root, without a suffix. `.yaml` for everything but a view, which is `.sql`. |
| `section` / `key` | Where inside `defines` the entry is, for the files that hold several: a role's section in `roles/clickhouse.yaml`, a topic's section in `topics/kafka.yaml`, a materialised view's name in a table file's `materialized_views`. |
| `header` | The common-header profile to compose, for a table with columns from one. |
| `compose` | A second definition composed over the header (or used alone), such as `hunts/results` for the detection table. |
| `topology` | `resolved` for an object with DDL that differs by ClickHouse platform, `fixed` for one that is the same everywhere. ClickHouse access objects and Kafka topics are `fixed`. The reader checks the value, and nothing renders by it: the platform comes from the renderer's caller. |
| `additive` | Whether an additive change may be applied automatically. A changed type, ORDER BY, PARTITION BY, TTL or codec is drift either way and is rejected. |
| `optional` | The apply may skip this object without failing the pass. |
| `depends_on` | Ids declared EARLIER in the list. |

Row policies are not manifest objects. dfe-engine's reconciler writes a RESTRICTIVE policy on every table that has `_org_id`, read off the live server, and a `USING 0` one on each tenant-reachable table that does not. It takes the policy name from `naming.row_policy` and the tenant setting from `tenant.setting` in `roles/clickhouse.yaml`. The predicate is the engine's: nothing reads the catalogue's `tenant.predicate`.

Per-source tables are not manifest objects either. The manifest is the set a deployment creates before anything streams. A source's table is created when that source is deployed, through the same table renderer.

## Scaling and failure

`depends_on` is checked against what precedes it, not against everything that exists. A view over a table declared after it is an apply that fails on a fresh cluster and succeeds on every re-run afterwards, so it breaks for whoever stands one up and nobody else.

`make render` (`scripts/render_manifest.py`) renders every object for both ClickHouse platforms and fails on:

* a duplicate `id`, or a `depends_on` that is unknown or declared later
* two objects rendering to the same qualified name
* a definition that will not render on some platform, such as a refreshable materialised view with a `refresh` that is not an `EVERY` or `AFTER` schedule
* a table that stores rows with an engine that is not `Replicated*`, or one that writes a Keeper path and replica into its engine
* a statement other than the self-hosted CREATE DATABASE that has `ON CLUSTER`
* a statement naming a `<database>.<object>` the manifest does not declare
* a checksum that differs between platforms

The checksum and the one engine form are on the [Renderer](renderer.md) page.

## Related

* [Definitions](definitions.md), [Renderer](renderer.md), [ClickHouse platforms](clickhouse-platform.md)
