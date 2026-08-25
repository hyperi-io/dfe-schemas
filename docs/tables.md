# Table definitions (`tables/`)

`tables/` holds tables written directly in ClickHouse types, as opposed to the
`meta/` schemas that map DFE's 13 primitives onto ClickHouse. Two sets live
here: the OTel telemetry tables the collector writes into, and the engine's own
working state.

They are here for the same reason every other schema is -- one definition, in
one repo. Owning the OTel tables is also what makes them correct on a cluster:
the exporter creates its tables over whichever connection it resolved and
senses no topology, so the same table ended up plain on one replica and
Replicated on the others, and a row written to the plain copy never
replicated.

## File shape

Same version tree as the rest of the repo -- `current` names the default
version, each version entry is a complete snapshot. A version entry carries
`table` (the DDL clauses), `columns`, and optionally `materialized_view`.

```yaml
current: "1.0.0"

versions:
  "1.0.0":
    date: "2026-08-26"
    type: model
    summary: "One row per gauge reading"
    table:
      engine: "MergeTree"
      partition_by: "toDate(_timestamp_load)"
      order_by: "ServiceName, MetricName, TimeUnix"
      index_granularity: 8192
      indexes:
        - "INDEX idx_time_minmax TimeUnix TYPE minmax GRANULARITY 1"
    columns:
      - name: "TimeUnix"
        ch_type: "DateTime"
        codec: "Delta(4), ZSTD(1)"
```

## `table` keys

| Key | Meaning |
|---|---|
| `engine` | The engine INTENT (`MergeTree`, `ReplacingMergeTree(updated_at)`). Resolved per topology at apply time, which is what adds `Replicated` and `ON CLUSTER`. |
| `partition_by` | Raw PARTITION BY expression. |
| `order_by` | Raw ORDER BY expression. Used when the sorting key is expressions rather than bare columns. |
| `index_granularity` | Rows per granule. 8192 for range scans, 2048 for point lookups. |
| `ttl_days` / `ttl_columns` | Retention. Omit both for a table that keeps everything. |
| `indexes` | Data-skipping indexes, emitted verbatim. |

An ORDER BY built from bare columns is expressed on the columns instead: give
each key column an `order` and leave `order_by` out.

## `columns` keys

| Key | Meaning |
|---|---|
| `name` | Column name, verbatim. |
| `ch_type` | Exact ClickHouse type, without any `LowCardinality` wrapper. |
| `lowcardinality` | Wraps `ch_type` in `LowCardinality(...)`. |
| `order` | Position in the sorting key, from 0. |
| `default` | DEFAULT expression. |
| `materialized` | MATERIALIZED expression. Mutually exclusive with `default`. |
| `codec` | CODEC arguments, e.g. `Delta(8), ZSTD(1)`. |
| `comment` | Column comment. |
| `max_dynamic_paths` | Typed sub-paths a `JSON` column holds before the rest spill to a shared map. `inherit` reads the value from the common header's `_json`. |

A nested `LowCardinality` is part of the type and stays in `ch_type`
(`Map(LowCardinality(String), String)`); only a top-level wrapper becomes the
`lowcardinality` flag.

## `materialized_view`

A materialised view has no columns to reconcile, only a SELECT. `to` names the
target table, and `{db}` in `select` is substituted at apply time.

```yaml
    materialized_view:
      name: otel_traces_trace_id_ts_mv
      to: otel_traces_trace_id_ts
      select: |-
        SELECT TraceId, min(Timestamp) AS Start, max(Timestamp) AS End
        FROM {db}.otel_traces
        WHERE TraceId != ''
        GROUP BY TraceId
```
