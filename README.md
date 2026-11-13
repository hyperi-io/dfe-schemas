# dfe-schemas

Defines the core ClickHouse objects and Kafka bootstrap topics a DFE deployment creates.

[![PyPI](https://img.shields.io/pypi/v/dfe-schemas?logo=pypi)](https://pypi.org/project/dfe-schemas/)
[![Python Version](https://img.shields.io/python/required-version-toml?tomlFilePath=https://raw.githubusercontent.com/hyperi-io/dfe-schemas/main/pyproject.toml)](pyproject.toml)
[![License](https://img.shields.io/badge/license-BUSL--1.1-blue)](LICENSE)

## What it does

- `manifest.yaml` lists every object a deployment creates, in the order it creates them: databases, tables, views, TTLs, roles and grants, and the Kafka bootstrap topics. Adding a table is an entry there plus its definition file, never an engine release, a Job or an init container.
- A renderer turns each object into checksummed output: ClickHouse DDL for the platform a connection reaches, Cloud or self-hosted, and a topic spec for a Kafka topic.
- One table shape on every ClickHouse: the argumentless `Replicated<variant>` engine with no `ON CLUSTER`, which ClickHouse Cloud rewrites to `SharedMergeTree`.
- The source meta schemas, the compatibility profiles, the shipped source catalogue, and the registries of table engines, column types and field maps dfe-engine validates sources against.

dfe-engine reads this package at boot, takes the schema lock, renders the manifest, compares it with `dfe.schema_migrations` and the live server, and applies what is absent or additively changed. Nothing else creates a ClickHouse object or a Kafka topic: not an Argo CD Job, not a compose init container, not an app at runtime. The Rust apps read the deployed schema from `system.columns` and never read this YAML.

## Quick start

```bash
pip install dfe-schemas
python -c "import dfe_schemas; print(dfe_schemas.schemas_root())"
```

Render the manifest for the ClickHouse platform a connection reaches:

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
```

From a clone, `make render` renders every object on every platform with nothing but this package, and `make test` runs the pytest suite. `make validate PY=../dfe-engine/.venv/bin/python` checks the meta schemas with dfe-engine's loader.

## Configuration

The package reads its shipped trees unless a caller passes `load_manifest(root=...)`. `DFE_ENGINE_SCHEMAS_DIR` is a dfe-engine setting that points the engine at a directory of your own, holding only the profiles you override. The shipped files are read-only defaults. The wheel's own semver is the only version this package has. dfe-infra's `versions.yaml` records that wheel version per stack, and dfe-engine pins it as a floor.

## Adding a table

1. Write the definition: a table in exact ClickHouse types under `tables/<area>/<name>.yaml`, or a view under `views/<name>.sql`.
2. Add it to `manifest.yaml`, after whatever it depends on, with its `kind`, `database`, `topology` and whether additive changes may be applied automatically.
3. `make render` and `make test`.
4. Merge, cut a release so the wheel reaches PyPI, then raise the `dfe-schemas` floor in dfe-engine.

Changing an existing table is a new version entry, never an edit. Each version is a complete column snapshot and a published one is immutable.

## Documentation

- [docs/manifest.md](docs/manifest.md): the manifest format and how to add an object.
- [docs/tables.md](docs/tables.md): the `tables/` format in exact ClickHouse columns.
- [docs/meta-schema.md](docs/meta-schema.md): the YAML format for source schemas, the version tree, the type system and the `@directive` language.
- [docs/compat-profiles.md](docs/compat-profiles.md): the compatibility profile format.
- [docs/clickhouse-platform.md](docs/clickhouse-platform.md): how the platform is probed and what differs on Cloud.
- [docs/architecture.md](docs/architecture.md): why it is shaped this way, and the invariants.

The suite's published docs are at <https://dfe.docs.hyperi.io>.

## Help and contributing

How to work in this repo, the commands that prove a change and what tends to bite are in [CONTRIBUTING.md](CONTRIBUTING.md). A question that is not a bug goes in a new issue.

Open bugs: [this repo](https://github.com/hyperi-io/dfe-schemas/issues?q=is%3Aopen+is%3Aissue+type%3ABug+-label%3Abacklog+-label%3Anext-minor) | [whole DFE suite](https://github.com/search?type=issues&q=is%3Aopen+is%3Aissue+type%3ABug+-label%3Abacklog+-label%3Anext-minor+repo%3Ahyperi-io%2Fdfe-infra+repo%3Ahyperi-io%2Fdfe-docker+repo%3Ahyperi-io%2Fdfe-deploy+repo%3Ahyperi-io%2Fdfe-engine+repo%3Ahyperi-io%2Fdfe-loader+repo%3Ahyperi-io%2Fdfe-receiver+repo%3Ahyperi-io%2Fdfe-fetcher+repo%3Ahyperi-io%2Fdfe-archiver+repo%3Ahyperi-io%2Fdfe-transform-vrl+repo%3Ahyperi-io%2Fdfe-transform-vector+repo%3Ahyperi-io%2Fdfe-transform-elastic+repo%3Ahyperi-io%2Fdfe-ui+repo%3Ahyperi-io%2Fdfe-hyperdx+repo%3Ahyperi-io%2Fscalo-rs+repo%3Ahyperi-io%2Fscalo-py+repo%3Ahyperi-io%2Flogreducer+repo%3Ahyperi-io%2Fdfe-schemas+repo%3Ahyperi-io%2Fvector-vrl+repo%3Ahyperi-io%2Fculvert+repo%3Ahyperi-io%2Fdfe-clickhouse-rs+repo%3Ahyperi-io%2Ffactbook-rs)

## License

BUSL-1.1, see [LICENSE](LICENSE).
