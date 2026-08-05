# DFE Schemas

Shared schema definitions for the Data Fusion Engine (DFE) platform - the
**single source of truth** for the data-structure definitions that multiple
DFE components must agree on (dfe-engine, dfe-loader, dfe-receiver,
dfe-archiver).

## Usage

Mount as a git submodule in each consuming project:

```bash
git submodule add https://github.com/hyperi-io/dfe-schemas.git schemas
git submodule update --init --recursive
```

Override the submodule path with `DFE_SCHEMAS_DIR`:

```bash
export DFE_SCHEMAS_DIR=/opt/dfe/schemas
```

Validate and render locally (needs dfe-engine importable - point `PY` at an
interpreter that has it):

```bash
make validate PY=../dfe-engine/.venv/bin/python
make render PY=../dfe-engine/.venv/bin/python
```

## Structure

```
dfe-schemas/
|-- common-header/     # header profiles: timeseries (9 col, default),
|                      #   minimal (5 col), passthrough (4 col)
|-- meta/              # source meta schemas, by provider (aws/ azure/ gcp/ m365/)
|-- additional/        # extra-field overlays (aws/)
|-- hunts/             # hunt output (results.yaml) + runner checkpoint schema
|-- argocd/            # self-contained Argo CD deploy unit (kustomize + Job)
|   '-- ddl/           # generated deploy SQL (make render - do not hand-edit)
|-- scripts/           # validate_schemas / render_ddl / annotate_meta_schemas
|-- docs/meta-schema.md  # the YAML format reference (version tree, columns, types)
'-- Makefile           # validate / render / check
```

Every schema YAML carries its own **version tree** - each version entry is a
complete column snapshot with SchemaVer semantics (model / addition /
revision), published versions are immutable, and consumers pin versions
independently. Full format reference, column fields, the 13-primitive type
system, and the `@directive` expression language:
[docs/meta-schema.md](docs/meta-schema.md).

## Deploying without the engine

`argocd/` is a self-contained Argo CD Application: a kustomization hashes the
rendered `ddl/` into a ConfigMap and a Job applies it to ClickHouse - so the
core tables deploy with or without dfe-engine present.

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
4. `make render` and commit the refreshed `argocd/ddl/` (CI gates on
   freshness).

Shipped files here are read-only defaults - customise by pointing
`DFE_SCHEMAS_DIR` at your own directory with only the profiles you override.
