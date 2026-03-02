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

## Structure

```
dfe-schemas/
├── common-header/          # Common header profiles
│   ├── timeseries.yaml     # Full event ingestion (default)
│   ├── minimal.yaml        # High-volume structured data
│   └── passthrough.yaml    # Transparent bridge
├── hunt-results/           # Hunt output schemas
│   └── detection.yaml      # Detection result columns
├── VERSION                 # Schema version (semver)
└── README.md
```

## Common Header Profiles

Every DFE table gets a standardised header with underscore-prefixed system
fields. The profile determines which header columns are included:

| Profile | Columns | Use Case |
|---------|---------|----------|
| `timeseries` | `_timestamp_load`, `_timestamp`, `_timestamp_received`, `_uuid`, `_org_id`, `_source`, `_raw`, `_json`, `_tags` | Logs, alerts, audit trails (default) |
| `minimal` | `_timestamp_load`, `_timestamp`, `_uuid`, `_org_id` | Metrics, flow records |
| `passthrough` | `_timestamp_load`, `_uuid`, `_org_id`, `_json` | Transparent bridge |

## Column Format

Each YAML file uses the `SchemaColumn` format:

```yaml
columns:
  - name: _timestamp_load       # Column name
    type: timestamp             # Primitive type (13 types: string, text, integer, float, boolean, datetime, timestamp, date, ip, uuid, json, geo_point, enum)
    attribute: [lowcardinality]  # Storage attributes (optional)
    use_case: range             # Query use case (optional)
    default: "now64(3)"         # DEFAULT expression (optional)
    order: 0                    # Position in ORDER BY (optional)
    comment: "@generated"       # Human description + loader directives (optional)
```

## Versioning

The `VERSION` file uses semver:
- **Major**: Breaking changes (column removed, type changed)
- **Minor**: New columns or profiles added
- **Patch**: Comment/metadata changes only

Consuming projects pin to a specific commit via submodule. Bumping the
submodule pin is an explicit compatibility decision.

## Consumers

| Project | Role | How it reads schemas |
|---------|------|---------------------|
| **dfe-engine** (Python) | Schema builder, DDL generation, hunt output | `SchemaLoader.load_profile(name, profiles_dir=submodule_path)` |
| **dfe-loader** (Rust) | Table creation, field enrichment | Reads YAML directly via `serde_yaml` |
| **dfe-receiver** (Rust) | Field validation | Reads YAML for field name validation |
