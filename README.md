# DFE Schemas

Shared schema definitions for the Data Fusion Engine (DFE) platform - the
**single source of truth** for the data-structure definitions that multiple
DFE components must agree on (dfe-engine, dfe-loader, dfe-receiver,
dfe-archiver).

## Usage

Install the Python package. The schema trees ship as package data under
`dfe_schemas/data/`, alongside the `dfe_schemas.clickhouse` engine resolver and
the declared deploy defaults:

```bash
pip install dfe-schemas
python -c "import dfe_schemas; print(dfe_schemas.schemas_root())"
```

Point `DFE_SCHEMAS_DIR` at your own directory to override the shipped trees:

```bash
export DFE_SCHEMAS_DIR=/opt/dfe/schemas
```

Validate locally (needs dfe-engine importable - point `PY` at an interpreter
that has it):

```bash
make validate PY=../dfe-engine/.venv/bin/python
```

## Structure

```
dfe-schemas/
|-- common-header/     # header profiles: timeseries (9 col, default),
|                      #   minimal (5 col), passthrough (4 col)
|-- meta/              # source meta schemas, by provider (aws/ azure/ gcp/ m365/
|                      #   runzero/ ...)
|-- additional/        # extra-field overlays: aws/, and snapshot/envelope.yaml,
|                      #   the store-snapshot envelope every dump store composes
|-- hunts/             # hunt output (results.yaml) + runner checkpoint schema
|-- tables/            # tables in exact ClickHouse types: otel/ + engine internal/
|-- sources/           # engine-owned source definitions (main, the landing table)
|-- registries/        # allow-lists: engines.yaml, types.yaml, field-maps/<standard>/
|-- scripts/           # validate_schemas / annotate_meta_schemas /
|                      #   generate_meta_schemas (a meta schema from live rows)
|-- docs/meta-schema.md  # the YAML format reference (version tree, columns, types)
|-- docs/tables.md     # the tables/ format reference (clauses, exact CH columns)
'-- Makefile           # validate
```

Every schema YAML carries its own **version tree** - each version entry is a
complete column snapshot with SchemaVer semantics (model / addition /
revision), published versions are immutable, and consumers pin versions
independently. Full format reference, column fields, the 13-primitive type
system, and the `@directive` expression language:
[docs/meta-schema.md](docs/meta-schema.md).

## Registries

The fixed lists dfe-engine validates and renders against live in `registries/`, and nowhere else.

| File | What it lists | Read by the engine for |
|------|---------------|------------------------|
| `engines.yaml` | Table engine variants a source may select. `arguments` is `none`, `optional` or `required`; `argument_hint` is what goes inside the parentheses | Source save validation and the UI engine dropdown |
| `types.yaml` | Column primitives and their ClickHouse type, codec and nullability, plus use-case, attribute and `ch_override` rules | Column validation and DDL rendering |
| `field-maps/<standard>/_default.yaml` | Default field map for each view standard (cim, ecs, ocsf, sigma) | Seeding the field-map store |

`make validate` checks every entry.

## How these reach ClickHouse

One applier, three callers. The `dfe-schema` entry point in the dfe-engine
image reads this tree and reconciles ClickHouse against it: dfe-infra runs it
as a Job before the data plane starts, dfe-docker runs it as a compose
init service, and the engine repeats it idempotently at boot. Rendering SQL
here and applying it separately was a SECOND definition of the same tables, so
it is gone -- there is one DDL path and it reads these files.

## Consumers

| Project | Language | Role | Schema types used |
|---------|----------|------|-------------------|
| **dfe-engine** | Python | DDL generation, schema builder, hunt output | All |
| **dfe-loader** | Rust | Table creation, field enrichment, auto-init | common-header |
| **dfe-receiver** | Rust | Field validation | common-header |
| **dfe-archiver** | Rust | Table detection | common-header |

Rust services slave from the DEPLOYED ClickHouse schema at runtime
(`system.columns`) - they never read this YAML directly.

## Updating schemas

1. Branch here, add a NEW version entry (complete column snapshot - never
   modify a published version), update `current`, commit, PR to main.
2. Cut a release (CI `workflow_dispatch`, `from-head=true`) so the wheel
   carrying the new trees reaches PyPI.
3. Raise the `dfe-schemas` floor in each consumer and relock
   (dfe-engine: `pyproject.toml` + `uv.lock`).

Shipped files here are read-only defaults - customise by pointing
`DFE_SCHEMAS_DIR` at your own directory with only the profiles you override.
