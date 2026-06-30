# DFE Schemas

Shared schema definitions for the Data Fusion Engine (DFE) platform.

This repo is the **single source of truth** for data structure definitions
that must be agreed upon across multiple DFE components (dfe-engine, dfe-loader,
dfe-receiver, dfe-archiver).

## Usage

Mount as a git submodule in each consuming project:

```bash
git submodule add https://github.com/hyperi-io/dfe-schemas.git schemas
```

After cloning:

```bash
git submodule update --init --recursive
```

Override the submodule path with `DFE_SCHEMAS_DIR`:

```bash
export DFE_SCHEMAS_DIR=/opt/dfe/schemas
```

## Structure

```
dfe-schemas/
├── common-header/          # Common header profiles
│   ├── timeseries.yaml     # Full event ingestion (9 cols, default)
│   ├── minimal.yaml        # High-volume structured data (4 cols)
│   └── passthrough.yaml    # Transparent bridge (4 cols)
├── hunt-results/           # Hunt output schemas
│   └── detection.yaml      # Detection result columns (6 cols)
├── VERSION                 # Repo-level version
└── README.md               # This file
```

## Per-File Schema Versioning

Every schema YAML file carries its own version history using a **version tree**.
Each version entry contains a complete column snapshot — no filtering or
reconstruction logic needed.

### File format

```yaml
current: "1.1.0"                    # Default version when no pin specified

versions:
  "1.0.0":
    date: "2026-01-15"
    type: model                     # model | addition | revision
    summary: "Initial 9-column common header"
    columns:
      - name: _timestamp
        type: datetime
        use_case: range
        order: 1
        expr: "@source: timestamp | now()"
        comment: "Event timestamp from source data"
      # ... complete column list for 1.0.0

  "1.1.0":
    date: "2026-03-15"
    type: addition
    summary: "Added _geo_point column"
    columns:
      - name: _timestamp
        type: datetime
        use_case: range
        order: 1
        expr: "@source: timestamp | now()"
        comment: "Event timestamp from source data"
      - name: _geo_point
        type: geo_point
        attribute: [nullable]
        comment: "Geographic coordinates"
      # ... complete column list for 1.1.0
```

### Why version tree (not since/until)?

Each version carries a **complete column snapshot**. To load version 1.0.0,
you read `versions."1.0.0".columns` — no filtering logic, no reconstruction.

- **Direct lookup** — O(1) version resolution
- **Dropped columns** — simply omit them from the new version's snapshot
- **Changed types** — the new version snapshot has the new type
- **Readable diffs** — git shows what changed between version entries
- **Immutable snapshots** — published versions are never modified

### Version metadata

| Field | Required | Description |
|-------|----------|-------------|
| `current` | Yes | Default version when consumer doesn't specify a pin |
| `versions` | Yes | Dict of version → `{date, type, summary, columns}` |
| `date` | Yes (per version) | When the version was created |
| `type` | Yes (per version) | Change category: `model`, `addition`, `revision` |
| `summary` | Yes (per version) | Human-readable change description |
| `columns` | Yes (per version) | Complete column snapshot for this version |

### Version type semantics (SchemaVer-inspired)

Uses SemVer format (`MAJOR.MINOR.PATCH`) with SchemaVer semantics:

| Change | Bump | Description | Migration safety |
|--------|------|-------------|------------------|
| **MODEL** | Major | Column removed, type changed, ORDER BY changed | Manual review required |
| **ADDITION** | Minor | New column added, new default expression | Safe auto-migrate (`ALTER TABLE ADD COLUMN`) |
| **REVISION** | Patch | Comment updated, attribute tweaked | No DDL change |

### How version pinning works

- **Source YAML** pins the header profile version via `header.version`:
  ```yaml
  source: windows_audit
  header:
    type: time_series
    version: "1.0.0"       # Pin to specific common header version
  schema:
    meta_schema: logs/windows_audit
    meta_schema_version: "2.1.0"   # Pin meta schema independently
  ```

- **No pin** → uses the file's `current` marker
- **Different sources can pin different versions** — one source on header v1.0.0,
  another on v1.1.0, from the same file

### Immutable versions

Published version entries are **never modified**. To change a schema:

1. Add a new version entry with the updated column snapshot
2. Update `current` to point to the new version
3. Commit

The engine's API enforces this — it only supports creating new versions
(cloning from an existing version as the base), never modifying published ones.

### Backward compatibility

Files without `current`/`versions` metadata (flat `columns:` list) work unchanged.
This ensures existing unversioned schema files continue to work.

---

## Common Header Profiles

Every DFE table gets a standardised header with underscore-prefixed system
fields.  The profile determines which header columns are included.

### Profiles

| Profile | Columns | Use Case |
|---------|---------|----------|
| `timeseries` (default) | 9 | Logs, alerts, audit trails — full event ingestion |
| `minimal` | 4 | Metrics, flow records — high-volume structured data |
| `passthrough` | 4 | Transparent bridge — no timestamp injection |

### timeseries (default) — 9 columns

| Column | Type | Attributes | ORDER BY | Expr | Comment |
|--------|------|------------|----------|------|---------|
| `_timestamp_load` | `timestamp` | | 0 | `@generated: now64(3)` | Insertion timestamp (ms precision) |
| `_timestamp` | `datetime` | use_case: range | 1 | `@source: timestamp \| now()` | Event timestamp from source data |
| `_timestamp_received` | `datetime` | | | `@source: first(timestamp_received/received_at)` | When the event was first received |
| `_uuid` | `uuid` | | | `@generated: generateUUIDv7()` | Time-ordered unique event identifier |
| `_org_id` | `string` | lowcardinality, dimension | 2 | `@source: org_id` | Tenant/organisation identifier |
| `_source` | `string` | lowcardinality, dimension | | `@source: first(_source) \| topic_name` | Data source label |
| `_raw` | `text` | text_search | | `@captured: raw_payload` | Original event payload as text |
| `_json` | `json` | | | `@captured: raw_payload as JSON` | Original event payload as JSON |
| `_tags` | `json` | | | `@source: first(tags/_tags/meta/metadata.tags)` | Event metadata tags |

### minimal — 4 columns

`_timestamp_load`, `_timestamp`, `_uuid`, `_org_id`

### passthrough — 4 columns

`_timestamp_load`, `_uuid`, `_org_id`, `_json`

---

## Column YAML Format (SchemaColumn)

```yaml
columns:
  - name: _timestamp_load
    type: timestamp
    default: "now64(3)"
    order: 0
    expr: "@generated: now64(3)"
    comment: "Insertion timestamp (ms precision)"
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Column name (underscore prefix for system fields) |
| `type` | Yes | Primitive type (see below) |
| `attribute` | No | List: `[lowcardinality]`, `[nullable]`, `[not_null]` |
| `use_case` | No | Query intent: `range`, `dimension`, `text_search`, `fulltext`, `bloom` |
| `default` | No | ClickHouse DEFAULT expression |
| `order` | No | Position in ORDER BY (0-based) |
| `expr` | No | DFE directive — tells the loader how to populate this column |
| `comment` | No | Human-readable column description |
| `ch_override` | No | Exact ClickHouse type — bypasses primitive mapping |

### DDL output

When generating DDL, `expr` and `comment` are combined into the ClickHouse
`COMMENT` clause:

```sql
-- Both expr and comment present:
`_timestamp` DateTime64(3) COMMENT '@source: timestamp | now() — Event timestamp from source data'

-- Expr only:
`_uuid` UUID DEFAULT generateUUIDv7() COMMENT '@generated: generateUUIDv7()'

-- Comment only:
`matched_uuid` UUID COMMENT 'UUID of the matching source record'
```

The loader parses `@` directives from the COMMENT string. The human-readable
description (after ` — `) is informational.

### 13 Primitive Types

| Primitive | ClickHouse Mapping | Notes |
|-----------|-------------------|-------|
| `string` | `String` / `LowCardinality(String)` | Fixed-width strings |
| `text` | `String` + full_text index | Text search capable |
| `integer` | `Int64` | 64-bit signed integer |
| `float` | `Float64` | 64-bit float |
| `boolean` | `Bool` | Boolean |
| `datetime` | `DateTime64(3)` | Millisecond precision |
| `timestamp` | `DateTime64(3)` | Alias for datetime with different codec |
| `date` | `Date` | Date only |
| `ip` | `IPv6` | IP addresses (v4 maps to v6) |
| `uuid` | `UUID` | UUID values |
| `json` | `JSON` | ClickHouse native JSON |
| `geo_point` | `Tuple(Float64, Float64)` | Geographic coordinates |
| `enum` | `Enum8`/`Enum16` | Enumerated types |

### DFE Expressions (expr field)

The `expr` field carries directives that tell the **loader** how to
populate each column.  These are the DFE DDL Expression Language:

| Directive | Meaning | Example |
|-----------|---------|---------|
| `@generated: expr` | ClickHouse generates via DEFAULT — loader omits | `@generated: now64(3)` |
| `@source: field` | Extract from source data field | `@source: org_id` |
| `@source: field \| fallback` | Extract with fallback | `@source: timestamp \| now()` |
| `@source: first(a/b/c)` | First match from multiple fields | `@source: first(tags/_tags/meta)` |
| `@captured: what` | Captured from raw payload before transforms | `@captured: raw_payload` |
| `@captured: what as TYPE` | Captured and cast | `@captured: raw_payload as JSON` |
| `@computed: expr` | Computed from other fields during data prep | `@computed: geoip(client_ip).country` |
| `@config: path` | Mapping is configurable | `@config: routing.org_id_field` |

See [dfe-loader/docs/DDL-EXPRESSION.md](https://github.com/hyperi-io/dfe-loader/blob/main/docs/DDL-EXPRESSION.md) for the full expression language reference.

---

## Hunt Detection Schema

The `hunt-results/detection.yaml` defines output columns appended after the
common header in hunt results tables.

| Column | Type | Description |
|--------|------|-------------|
| `matched_uuid` | `uuid` | UUID of the matching source record |
| `rule_id` | `string` (lowcardinality) | Rule identifier |
| `rule_name` | `string` (lowcardinality) | Human-readable rule name |
| `source_table` | `string` (lowcardinality) | Source table that was scanned |
| `hunt_name` | `string` (lowcardinality) | Parent hunt name |
| `severity` | `string` (lowcardinality) | Detection severity |

This schema uses the same per-file version tree system as all other schema types.

---

## Underscore Prefix Convention

All system fields use underscore prefix (`_timestamp`, `_org_id`, `_uuid`, etc.)
to avoid collision with source data fields.  Source data often contains
`timestamp`, `id`, `tags` — the underscore prefix provides clear visual
distinction and prevents name clashes.

---

## Resolution Chain

Both dfe-engine (Python) and dfe-loader (Rust) use the same resolution order:

```
1. DFE_SCHEMAS_DIR env var  →  {dir}/common-header/
2. schemas/common-header/   →  submodule checkout
3. schemas/profiles/        →  bundled fallback (in-package copy)
```

### Python (dfe-engine)

```python
from dfe_engine.schema.schema_loader import SchemaLoader

# Load profile at current version
columns = SchemaLoader.load_profile("timeseries")

# Load profile at pinned version
columns = SchemaLoader.load_profile("timeseries", version="1.0.0")

# Load any schema file with version
columns = SchemaLoader.load_columns("path/to/schema.yaml", version="2.0.0")

# Read version metadata
meta = SchemaLoader.load_version_metadata("path/to/schema.yaml")
# → {"current": "1.0.0", "versions": {"1.0.0": {"date": "...", "type": "...", "summary": "..."}}}
```

### Rust (dfe-loader)

```rust
fn resolve_profiles_dir() -> PathBuf {
    // 1. Env var
    if let Ok(dir) = std::env::var("DFE_SCHEMAS_DIR") {
        let candidate = PathBuf::from(dir).join("common-header");
        if candidate.is_dir() { return candidate; }
    }
    // 2. Submodule
    let submodule = PathBuf::from("schemas/common-header");
    if submodule.is_dir() { return submodule; }
    // 3. Bundled fallback
    PathBuf::from("schemas/profiles")
}
```

The Rust loader should parse `current` and `versions.<ver>.columns` from the
YAML, reading the column snapshot for the requested version directly.

---

## Shipped Schemas Are Read-Only

Files in this repo are **shipped defaults**.  To customise:

- Create a custom profile YAML in a separate directory
- Set `DFE_SCHEMAS_DIR` to point to your directory
- Your custom dir can include only the profiles you override

dfe-engine enforces this via `is_shipped_schema()` which detects paths
inside the submodule or bundled profiles directory.

---

## Updating Schemas

Changes go through this repo:

```bash
cd schemas                     # enter the submodule
git checkout -b my-change
# edit YAML files — add new version entry with complete column snapshot
git commit -am "feat: add _geo_point to timeseries (1.1.0)"
git push origin my-change
# open PR, merge to main
```

Then update the submodule pin in each consumer:

```bash
cd /projects/dfe-engine        # or dfe-loader
git submodule update --remote schemas
git add schemas
git commit -m "chore: update dfe-schemas to 1.1.0"
```

### Keeping bundled profiles in sync

Both dfe-engine and dfe-loader keep bundled copies as fallback:

- **dfe-engine:** `src/dfe_engine/schema/profiles/`
- **dfe-loader:** `schemas/profiles/`

After updating this repo, copy changed YAML files to the bundled location
so `pip install dfe-engine` and `cargo install dfe-loader` work without
a submodule checkout.

---

## Consumers

| Project | Language | Role | Schema Types Used |
|---------|----------|------|-------------------|
| **dfe-engine** | Python | DDL generation, schema builder, hunt output | All |
| **dfe-loader** | Rust | Table creation, field enrichment, auto-init | common-header |
| **dfe-receiver** | Rust | Field validation | common-header |
| **dfe-archiver** | Rust | Table detection | common-header |
| **dfe-control-plane** | Python | Schema management UI | All (via dfe-engine) |

---

## Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `scripts/validate_schemas.py` | Validate every schema YAML against dfe-engine meta-schema and TypeRegistry | `make validate` (needs dfe-engine importable) |
| `scripts/render_ddl.py` | Render reference ClickHouse DDL into `argocd/ddl/` | `make render` (needs dfe-engine; sets `DFE_SCHEMAS_DIR` to this repo) |
| `scripts/annotate_meta_schemas.py` | Add missing `resource_type: core` and column `_field_type: base` under `meta/` and `common-header/` (never overwrites existing keys) | `python3 scripts/annotate_meta_schemas.py` (stdlib only; `--dry-run`, `--fix-spacing`, `--schema-dir`) |