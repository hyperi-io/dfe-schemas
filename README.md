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

Read the manifest and render it for the ClickHouse platform a connection reaches:

```python
from dfe_schemas.clickhouse import PlatformProbe
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer

probe = PlatformProbe(client, host="clickhouse.example.internal")
probe.require_replication()  # a self-hosted server without Keeper is refused here
manifest = load_manifest()
renderer = Renderer(manifest, platform=probe.platform, data_database="dfe")
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
make render      # every object, every platform -- needs only this package
make test        # the pytest suite, which does the same and more
make validate PY=../dfe-engine/.venv/bin/python   # the meta schemas
```

`render` is the check that matters for the manifest: it fails on a duplicate
object name, on a dependency that is unknown or declared after its dependant,
on a definition that will not render on some platform, on a table that stores
rows and is not `Replicated*` or a table statement carrying `ON CLUSTER`, and on
a checksum that differs between platforms.

`validate` still borrows dfe-engine's loader for the `meta/` schemas, which is
the one remaining cross-repo step.

## Structure

```text
dfe-schemas/
|-- manifest.yaml      # THE apply manifest: every object, in dependency order
|-- common-header/     # header profiles: timeseries (9 col, default),
|                      #   minimal (5 col), passthrough (4 col)
|-- compat-profiles/   # compatibility profiles: rule rows that read another
|                      #   platform's header (dfe-legacy-2x, elastic-agent)
|-- meta/              # source meta schemas, by provider (aws/ azure/ gcp/ m365/
|                      #   runzero/ ...)
|-- additional/        # extra-field overlays: <package>/<data_stream>.yaml, the
|                      #   937 vendor overlays dfe-schemagen generates for the
|                      #   Elastic catalogue, and snapshot/envelope.yaml, the
|                      #   store-snapshot envelope every dump store composes
|-- derived/           # select lists over a base: <package>/<data_stream>.yaml
|                      #   over meta/elastic/ecs (generated), and beats/*_hunt
|-- hunts/             # hunt output (results.yaml) + runner checkpoint schema
|-- tables/            # tables in exact ClickHouse types:
|                      #   core/ (main, detection, detection_checkpoint)
|                      #   internal/ (engine state, the migration ledger, the lock)
|                      #   otel/ (the telemetry tables HyperDX reads)
|                      #   usage/ (usage per minute and per day, the licence numbers)
|                      #   meta/ (the dfe_meta governance projection)
|-- views/             # the built-in dfe_v_* parameterised views and the usage_*
|                      #   rollup views, as SQL
|-- roles/             # the ClickHouse tier, service-role and grant catalogue
|-- topics/            # the Kafka naming rule, defaults and bootstrap set
|-- sources/           # engine-owned source definitions (main, dfe-alerts)
|-- catalogue/         # sources shipped ready to create, one POST /sources body each
|-- registries/        # allow-lists: engines.yaml, types.yaml, field-maps/<standard>/
|-- dfe_schemas/       # the package: manifest reader, renderer, engine resolver
|-- scripts/           # render_manifest / validate_schemas / annotate + generate
|-- docs/manifest.md     # the manifest format, and how to add an object
|-- docs/meta-schema.md  # the YAML format reference (version tree, columns, types)
|-- docs/compat-profiles.md  # the compatibility profile format
|-- docs/tables.md     # the tables/ format reference (clauses, exact CH columns)
'-- Makefile           # render, validate
```

Every schema YAML carries its own **version tree** - each version entry is a
complete column snapshot with SchemaVer semantics (model / addition /
revision), published versions are immutable, and consumers pin versions
independently. Full format reference, column fields, the 13-primitive type
system, and the `@directive` expression language:
[docs/meta-schema.md](docs/meta-schema.md).

### Catalogue

`catalogue/` holds the sources DFE ships ready to create, one file per source and named for it. Each file is the `POST /sources` write body for the source's default intake, plus a `catalogue` block the write model ignores.

| Key | What it holds |
|-----|---------------|
| `package`, `data_stream` | The integration package and data stream the source carries |
| `intakes` | Every way the data can arrive: `beats`, `receiver`, `fetcher` |
| `framing` | Receiver intakes only: `line` or `body` |
| `beats` | The Beats module and fileset carrying the same source, where one does |
| `variants` | Every compiled transform for the source, with the body's `transform.variant` first |

The body's origin is the first of `fetcher`, `receiver` and `beats` the entry lists, so the body carries `fetcher` exactly when `fetcher` is an intake. The other intakes are derived at create time: `beats` matches `data_stream.dataset` equal to `<package>.<data_stream>` and `receiver` matches `_source` equal to the source name.

Schema layers name no version pin. A body without `schema` is a stream with no derived schema over `meta/elastic/ecs`: dfe-engine binds no schema for those. `fetcher` is offered only with a schema, because dfe-engine refuses a fetcher source on its own topic without one, and only for a stream a dfe-fetcher family polls.

`tests/test_catalogue.py` checks the format and `make validate` builds every body as dfe-engine's `SourceWriteRequest`.

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

Every table renders one way on every ClickHouse: the argumentless
`Replicated<variant>` engine with no `ON CLUSTER`, inside a database that
replicates its own DDL. Cloud rewrites it to `SharedMergeTree`, and self-hosted
ClickHouse needs Keeper for it. The platform is probed from the server unless an
operator pins it, and picks only the CREATE DATABASE and a JSON table's
platform-only settings. `docs/clickhouse-platform.md` has the pattern.

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

## Context

### What this is

The schema and DDL source of truth for the DFE suite -- every table, view, TTL,
ClickHouse role, grant and Kafka bootstrap topic, plus the renderer. It ships as
the PyPI package `dfe-schemas`, NOT a git submodule: it was one, and the older
half of `docs/meta-schema.md` still describes one. dfe-engine reads the trees out
of the installed wheel unless `DFE_SCHEMAS_DIR` points elsewhere. Nothing here
touches a database -- this repo supplies definitions and dfe-engine applies them.

### Where things live

| Path | What it holds |
|------|---------------|
| `manifest.yaml` | Every object a deployment creates, in dependency order |
| `dfe_schemas/` | The package: manifest reader, renderer, loader, platform probe, `sql/platform.sql` |
| `tables/`, `views/`, `roles/`, `topics/` | Definitions in exact ClickHouse types, SQL, the role catalogue, the Kafka policy |
| `common-header/`, `meta/`, `additional/`, `hunts/` | Definitions in DFE primitives, mapped through `registries/types.yaml` |
| `compat-profiles/` | Compatibility profiles, their JSON schema, and rows in the scalo.rules format |
| `catalogue/` | Sources shipped ready to create, one `POST /sources` body per file |
| `registries/` | The allow-lists dfe-engine validates against |
| `docs/architecture.md` | Why it is shaped this way, and the invariants |

### Commands that prove a change

```bash
make render      # every object, every platform -- needs only this package
make test        # the pytest suite
make validate PY=../dfe-engine/.venv/bin/python   # the meta schemas
hyperi-ci check  # the quality gate CI runs
```

`make render` needs nothing but this package, and reports 72 objects across 2
platforms. Coverage is gated at 80% and measures 94.64%.

`make test` also starts ClickHouse in docker for `tests/test_usage_rollup.py`,
applies the whole manifest to it and checks the usage numbers. Without docker
those tests skip, and in CI they fail instead.

Two ways green lies. `make test` fails on a dirty repo root: the tokenizer guard
rglobs `schemas_root()`, which in a checkout IS the repo root, and excludes
nothing, so a `git worktree` under `.worktrees/` or a nested non-editable venv
gets read as this repo's content, and the count of parametrised cases moves with
it. This checkout had four worktrees under `.worktrees/`, collected 185 cases and
failed 8 of them against `timeseries.yaml` files belonging to other branches. CI
is green because a fresh runner has no worktree and no non-editable install.

And `make validate` is the only step needing
another repo, which is why it runs from `validate-schemas.yml` and not `ci.yml`
([docs/architecture.md](docs/architecture.md) has the reason).

### What tends to bite

| Don't | Do | Why |
|-------|----|-----|
| Trust `make render` to mean an index string is valid | Check `tests/test_tokenizers.py` | The renderer dropped a declared `index` and rendered the `use_case` template instead, so two header strings kept ClickHouse's retired `default` tokenizer. Engine v1.20.20 honoured the string and every new source failed with `Unknown tokenizer` (`8934979`) |
| Edit a published version entry | Add a new entry, as a complete snapshot | Consumers pin versions independently, so an in-place edit changes what an already-pinned consumer resolves. `generate_meta_schemas.py` refuses it |
| Merge a table and expect a deployment to get it | Release the wheel, raise the floor in dfe-engine, relock | Nothing reads this repo directly. The floor bump is the only step that puts an object in front of a deployment |
| Wrap an `ORDER BY` key in `Nullable` | Leave it bare, or mark it `not_null` | `_sorting_key` refuses a Nullable key rather than dropping it. JSON inside Nullable is a different case: ClickHouse 24.8 refused it, code 43 (`2f28227`), but 26.3.32.14 creates and fills a `Nullable(JSON)` column |
| Point `PY` at a bare `python` | Leave it at `uv run python` | A bare `python` is absent on a stock Linux box, so `make validate` died before validating anything (`6c79570`) |

### Where this sits

Generated from `dfe-infra/suite.yaml` via `dfe-stack suite`. Nothing in the suite
feeds this repo -- it declares no inbound edges and the package has one runtime
dependency, `ruamel.yaml`.

| Repo | Kind | Mechanism, outbound |
|------|------|---------------------|
| dfe-engine | `python-dep`, potential | Declares `dfe-schemas` by range in `pyproject.toml`, no upper bound. The only applier |
| dfe-infra | `version-pin`, lockstep | `versions.yaml` records this wheel's version, tagged with the stack release |
| dfe-deploy | `version-pin`, lockstep | `pins.yaml` pins the release tag, no artefact copied |

`make validate` needing dfe-engine is deliberately not an edge -- that cycle is
why `dfe_schemas/loader.py` exists. dfe-loader, dfe-receiver and dfe-archiver
take their columns from the common header but read the deployed ClickHouse
schema, never this YAML.
