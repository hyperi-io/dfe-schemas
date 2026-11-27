# Definitions

A definition describes one object a deployment creates, and the definition trees are one part of [dfe-schemas](architecture.md).

| Fact | Value |
|---|---|
| Repo | `hyperi-io/dfe-schemas`, the trees at the repo root |
| Package | the `dfe-schemas` wheel on PyPI, the trees as package data under `dfe_schemas/data/` |
| Config | `registries/types.yaml` maps DFE primitives to ClickHouse types. `DFE_ENGINE_SCHEMAS_DIR` points dfe-engine at another tree |
| Endpoints | none, they are files |
| Metrics | none |

Two families of definition exist, and which one a file belongs to decides how its column types resolve.

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
  accTitle: L4 Detail -- Definitions
  accDescr: The manifest names each definition file. Definitions come in two families: tables, views, roles and topics in exact ClickHouse types, SQL or policy, and common-header, meta, hunts, dlq and additional in DFE primitives that map through the type registry. The renderer reads them.
  manifest("<b>Manifest</b><br/><span>names each file</span>")
  subgraph defs["Definitions"]
    direction LR
    exact[["<b>Exact types</b><br/><span>tables, views, roles, topics</span>"]]
    prim[["<b>DFE primitives</b><br/><span>header, meta, hunts and more</span>"]]
    types("<b>Type registry</b><br/><span>registries/types.yaml</span>")
    exact ~~~ prim --> types
  end
  renderer("<b>Renderer</b><br/><span>checksummed statements</span>")
  manifest -->|names| defs
  defs --> renderer
  class exact,prim,types part
  class manifest,renderer part
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
  style defs fill:none,stroke:#0072B2,stroke-width:1.5px,stroke-dasharray:6 4
```

*Figure 1. L4 Detail -- Definitions.* Up: [L3 Components -- dfe-schemas](architecture.md). Down: [L4 Detail -- Meta schema to source table](meta-schema.md), [L4 Detail -- Column composition](meta-schema.md#composition-pipeline).

## How it works

1. `tables/**` is written in exact ClickHouse types, because those columns are engine state and telemetry with nothing to map from.
2. `common-header/**`, `meta/**`, `hunts/**`, `dlq/**` and `additional/**` use DFE primitives, and the **Type registry** maps each to a ClickHouse type.
3. Composition puts the header columns first, then what the composed definition adds. The header wins, so a source cannot redefine `_org_id`.
4. The **Manifest** names the file each object comes from, and the **Renderer** reads it.

A definition may drop header columns by name with `profile_exclude`. `hunts/results.yaml` drops `_raw` and `_tags`, because a detection references its source by `matched_uuid`. A second copy of the payload text, and an index over it, earn nothing.

## Interfaces

| Form | Meaning |
|---|---|
| `ch_type` | An exact ClickHouse type. It bypasses the primitive mapping and resolves NOT NULL, which is what a sorting key needs |
| `type` | A DFE primitive, mapped through `registries/types.yaml` |
| Version tree | Each version entry is a complete column snapshot, not a delta. `current` names the default |
| Flat list | A column list with no versions |

A file mixing `ch_type` and `type` is rejected rather than half-read.

Versions follow SchemaVer: model, addition, revision. A published version is immutable, because consumers pin versions independently and an in-place edit changes what a pinned consumer resolves.

## Scaling and failure

A definition that will not render on some platform fails `make render`. So does a Nullable `ORDER BY` key, a TTL over an absent column, and a view placeholder with no value. The full list is in [CONTRIBUTING.md Context](../CONTRIBUTING.md#context).

## Related

- [Meta schema system](meta-schema.md), the column format reference
- [Table definitions](tables.md), [Registries](registries.md), [Kafka topics](topics.md), [ClickHouse roles](roles.md)
- [Manifest](manifest.md), [Renderer](renderer.md)
