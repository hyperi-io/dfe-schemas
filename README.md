# DFE Schemas

Shared schema definitions for the Data Fusion Engine (DFE) platform - the
**single source of truth** for the data-structure definitions that multiple
DFE components must agree on (dfe-engine, dfe-loader, dfe-receiver,
dfe-archiver).

## Usage

Two consumption modes, one content.

As a Python package (the schema trees ship as package data, plus the
`dfe_schemas.clickhouse` engine resolver and the declared deploy defaults):

```bash
pip install dfe-schemas
python -c "import dfe_schemas; print(dfe_schemas.schemas_root())"
```

Or mount as a git submodule in each consuming project:

```bash
git submodule add https://github.com/hyperi-io/dfe-schemas.git schemas
git submodule update --init --recursive
```

Override the submodule path with `DFE_SCHEMAS_DIR`:

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
|-- meta/              # source meta schemas, by provider (aws/ azure/ gcp/ m365/)
|-- additional/        # extra-field overlays (aws/)
|-- hunts/             # hunt output (results.yaml) + runner checkpoint schema
|-- tables/            # tables in exact ClickHouse types: otel/ + engine internal/
|-- scripts/           # validate_schemas / annotate_meta_schemas
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
2. Bump the submodule pin in each consumer
   (`git submodule update --remote schemas`, commit the pin).
3. Copy changed common-header profiles to the consumers' bundled fallback
   locations (dfe-engine: `src/dfe_engine/schema/profiles/`) so package
   installs work without a submodule checkout.

Shipped files here are read-only defaults - customise by pointing
`DFE_SCHEMAS_DIR` at your own directory with only the profiles you override.
