# DFE Schemas

The **only** place a DFE table, view, TTL, ClickHouse role, grant or Kafka
bootstrap topic is defined. Every component that used to carry one of those in
code or in a chart reads it from here instead: dfe-engine applies it, and
dfe-infra, dfe-docker and dfe-deploy create none of it.

`manifest.yaml` is the list of every object a deployment creates, in the order
it creates them. Adding a table is an entry there plus its definition file --
never an engine release, never a Job, never an init container.

## Usage

Install the Python package. The schema trees and the manifest ship as package
data under `dfe_schemas/data/`, alongside the ClickHouse engine resolver, the
renderer and the declared deploy defaults:

```bash
pip install dfe-schemas
python -c "import dfe_schemas; print(dfe_schemas.schemas_root())"
```

Read the manifest and render it for a topology:

```python
from dfe_schemas.clickhouse import Topology
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer

manifest = load_manifest()
renderer = Renderer(manifest, topology=Topology.REPLICATED, data_database="dfe")
for obj in manifest.objects:
    rendered = renderer.render(obj)
    print(rendered.id, rendered.checksum[:12])
    for statement in rendered.statements:
        print(statement)
```

Point `DFE_SCHEMAS_DIR` at your own directory to override the shipped trees:

```bash
export DFE_SCHEMAS_DIR=/opt/dfe/schemas
```

## Checking it

```bash
make render      # every object, every topology -- needs only this package
make test        # the pytest suite, which does the same and more
make validate PY=../dfe-engine/.venv/bin/python   # the meta schemas
```

`render` is the check that matters for the manifest: it fails on a duplicate
object name, on a dependency that is unknown or declared after its dependant,
on a definition that will not render under some topology, and on a checksum
that differs between topologies.

`validate` still borrows dfe-engine's loader for the `meta/` schemas, which is
the one remaining cross-repo step.

## Structure

```
dfe-schemas/
|-- manifest.yaml      # THE apply manifest: every object, in dependency order
|-- common-header/     # header profiles: timeseries (9 col, default),
|                      #   minimal (5 col), passthrough (4 col)
|-- meta/              # source meta schemas, by provider (aws/ azure/ gcp/ m365/
|                      #   runzero/ ...)
|-- additional/        # extra-field overlays: aws/, and snapshot/envelope.yaml,
|                      #   the store-snapshot envelope every dump store composes
|-- hunts/             # hunt output (results.yaml) + runner checkpoint schema
|-- tables/            # tables in exact ClickHouse types:
|                      #   core/ (main, detection, detection_checkpoint)
|                      #   internal/ (engine state, the migration ledger, the lock)
|                      #   otel/ (the telemetry tables HyperDX reads)
|                      #   meta/ (the dfe_meta governance projection)
|-- views/             # the built-in dfe_v_* parameterised views, as SQL
|-- roles/             # the ClickHouse tier, service-role and grant catalogue
|-- topics/            # the Kafka naming rule, defaults and bootstrap set
|-- sources/           # engine-owned source definitions (main, dfe-alerts)
|-- registries/        # allow-lists: engines.yaml, types.yaml, field-maps/<standard>/
|-- dfe_schemas/       # the package: manifest reader, renderer, engine resolver
|-- scripts/           # render_manifest / validate_schemas / annotate + generate
|-- docs/manifest.md     # the manifest format, and how to add an object
|-- docs/meta-schema.md  # the YAML format reference (version tree, columns, types)
|-- docs/tables.md     # the tables/ format reference (clauses, exact CH columns)
'-- Makefile           # render, validate
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

One applier, one caller. dfe-engine reads the pinned wheel at boot, takes the
schema lock, renders every object in `manifest.yaml`, compares it against
`dfe.schema_migrations` and against the live server, applies what is absent or
additively changed, and records each object with the dfe-schemas version and
checksum it came from.

Nothing else creates a ClickHouse database, table, view, TTL, user, role or
grant, or a Kafka topic. Not an ArgoCD Job, not a compose init container, not
an app at runtime, not the OTel collector -- the exporter runs with
`create_schema: false` precisely because it senses no topology and would create
the same table plain on one replica and Replicated on the others.

Rendering is topology-aware and the topology is a parameter: sensed from the
live server where there is one, and otherwise the deployment's setting. One
definition renders plain for a keeperless node, `Replicated` for a Replicated
database or ClickHouse Cloud, and `Replicated` plus `ON CLUSTER` for an Atomic
database on a real cluster.

## Consumers

| Project | Language | Role | Schema types used |
|---------|----------|------|-------------------|
| **dfe-engine** | Python | Reads the manifest and applies it; the only DDL and topic principal | All |
| **dfe-loader** | Rust | Field enrichment | common-header |
| **dfe-receiver** | Rust | Field validation | common-header |
| **dfe-archiver** | Rust | Table detection | common-header |

Rust services slave from the DEPLOYED ClickHouse schema at runtime
(`system.columns`) - they never read this YAML directly.

## Adding a table

1. Write the definition. A table in exact ClickHouse types goes under
   `tables/<area>/<name>.yaml` ([docs/tables.md](docs/tables.md)); a view goes
   under `views/<name>.sql`.
2. Add it to `manifest.yaml`, after whatever it depends on, with its `kind`,
   `database`, `topology` and whether additive changes may be applied
   automatically ([docs/manifest.md](docs/manifest.md)).
3. `make render` and `make test`.
4. PR to main, then cut a release (CI `workflow_dispatch`, `from-head=true`) so
   the wheel carrying it reaches PyPI.
5. Raise the `dfe-schemas` floor in dfe-engine and relock (`pyproject.toml` +
   `uv.lock`). That is the only step that puts the new object in front of a
   deployment.

Changing an EXISTING table follows the same path, with a new version entry
rather than an edit: each version is a complete column snapshot, and a
published one is immutable.

## Versioning

The wheel's own semver is the only version this package has. dfe-infra's
`versions.yaml` records that same wheel version; dfe-deploy's `pins.yaml` spells
it as a lockstep stack tag (`2.2.0`), which is dfe-deploy's own numbering and
not a version of anything here.

Shipped files are read-only defaults - customise by pointing `DFE_SCHEMAS_DIR`
at your own directory with only the profiles you override.
