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

Every schema YAML file carries its own version history.  Git rollback is
repo-wide, so per-file versioning lives **inside** the YAML — you can have
v1, v2, and v3 in one file and pin any consumer to any version.

### File format

```yaml
# Top-level metadata
current: "1.1.0"                    # Default version when no pin specified

versions:                           # Human-readable version history
  "1.0.0":
    date: "2026-01-15"
    type: model                     # model | addition | revision
    summary: "Initial 9-column common header"
  "1.1.0":
    date: "2026-03-15"
    type: addition
    summary: "Added _geo_point column"

# Column definitions with lifecycle annotations
columns:
  - name: _timestamp
    type: datetime
    use_case: range
    order: 1
    comment: "@source: timestamp | now()"
    since: "1.0.0"                  # Introduced in this version

  - name: _geo_point
    type: geo_point
    attribute: [nullable]
    since: "1.1.0"                  # Added in 1.1.0
```

### Version fields

| Field | Required | Description |
|-------|----------|-------------|
| `current` | Yes | Default version when consumer doesn't specify a pin |
| `versions` | Yes | Dict of version → `{date, type, summary}` |
| `since` | Yes (per column) | Version when this column was introduced (inclusive) |
| `until` | No (per column) | Version when this column was removed (exclusive) |

### Version type semantics (SchemaVer-inspired)

Uses SemVer format (`MAJOR.MINOR.PATCH`) with SchemaVer semantics:

| Change | Bump | Description | Migration safety |
|--------|------|-------------|------------------|
| **MODEL** | Major | Column removed, type changed, ORDER BY changed | Manual review required |
| **ADDITION** | Minor | New column added, new default expression | Safe auto-migrate (`ALTER TABLE ADD COLUMN`) |
| **REVISION** | Patch | Comment updated, attribute tweaked | No DDL change |

### How version pinning works

Consumers specify a version; the engine filters columns:

```
columns where since <= target AND (until is None OR until > target)
```

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

### Dropped columns

Add `until` to the column.  The entry remains as version history:

```yaml
columns:
  - name: _legacy_field
    type: string
    since: "1.0.0"
    until: "2.0.0"         # Removed in v2.0.0
```

At version 2.0.0+, `_legacy_field` does not appear.  At 1.x.x, it does.

### Changed column types

Two entries for the same column name with `until`/`since` boundaries:

```yaml
columns:
  # v1 had _raw as text (full-text indexed)
  - name: _raw
    type: text
    use_case: text_search
    comment: "@captured: raw_payload"
    since: "1.0.0"
    until: "2.0.0"

  # v2 changed _raw to string (no full-text)
  - name: _raw
    type: string
    comment: "@captured: raw_payload"
    since: "2.0.0"
```

Version 1.x.x gets `_raw` as `text`.  Version 2.0.0+ gets `_raw` as `string`.

### Reconstructing any version

The engine can reconstruct any historical version from a single file:

```python
# Load current default
columns = SchemaLoader.load_columns("timeseries.yaml")

# Load specific version
columns = SchemaLoader.load_columns("timeseries.yaml", version="1.0.0")

# Load profile with version pin
columns = SchemaLoader.load_profile("timeseries", version="1.0.0")
```

### Backward compatibility

Files without `current`/`since` metadata work unchanged — all columns are
returned.  This ensures existing unversioned schema files continue to work.

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

| Column | Type | Attributes | ORDER BY | Comment (DDL Expression) |
|--------|------|------------|----------|--------------------------|
| `_timestamp_load` | `timestamp` | | 0 | `@generated: now64(3)` |
| `_timestamp` | `datetime` | use_case: range | 1 | `@source: timestamp \| now()` |
| `_timestamp_received` | `datetime` | | | `@source: first(timestamp_received/received_at)` |
| `_uuid` | `uuid` | | | `@generated: generateUUIDv7()` |
| `_org_id` | `string` | lowcardinality, dimension | 2 | `@source: org_id` |
| `_source` | `string` | lowcardinality, dimension | 3 | `@source: first(_source) \| topic_name` |
| `_raw` | `text` | text_search | | `@captured: raw_payload` |
| `_json` | `json` | | | `@captured: raw_payload as JSON` |
| `_tags` | `json` | | | `@source: first(tags/_tags/meta/metadata.tags)` |

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
    comment: "@generated: now64(3)"
    since: "1.0.0"
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Column name (underscore prefix for system fields) |
| `type` | Yes | Primitive type (see below) |
| `attribute` | No | List: `[lowcardinality]`, `[nullable]`, `[not_null]` |
| `use_case` | No | Query intent: `range`, `dimension`, `text_search`, `fulltext`, `bloom` |
| `default` | No | ClickHouse DEFAULT expression |
| `order` | No | Position in ORDER BY (0-based) |
| `comment` | No | Human description + DDL expression (see below) |
| `ch_override` | No | Exact ClickHouse type — bypasses primitive mapping |
| `since` | Yes* | Version when introduced (semver) |
| `until` | No | Version when removed (semver, exclusive) |

\* Required for versioned files.  Optional for backward compatibility with unversioned files.

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

### DDL Expressions (comment field)

The `comment` field carries directives that tell the **loader** how to
populate each column.  These are the DFE DDL Expression Language:

| Directive | Meaning | Example |
|-----------|---------|---------|
| `@generated: expr` | ClickHouse generates via DEFAULT — loader omits | `@generated: now64(3)` |
| `@source: field` | Extract from source data field | `@source: org_id` |
| `@source: field \| fallback` | Extract with fallback | `@source: timestamp \| now()` |
| `@source: first(a/b/c)` | First match from multiple fields | `@source: first(tags/_tags/meta)` |
| `@captured: what` | Captured from raw payload before transforms | `@captured: raw_payload` |
| `@captured: what as TYPE` | Captured and cast | `@captured: raw_payload as JSON` |

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

This schema is also versioned with `current`/`since` — the same per-file
versioning system applies to all schema types.

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
# → {"current": "1.0.0", "versions": {"1.0.0": {...}}}
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

The Rust loader should parse `current` and `since`/`until` from the YAML
and apply the same filtering logic when a version pin is specified.

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
# edit YAML files — add new version entry, add/modify columns
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
