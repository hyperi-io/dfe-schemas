# Registries (`registries/`)

The fixed lists dfe-engine validates and renders against live in `registries/`, and nowhere else.

| File | What it lists | Read for |
|------|---------------|----------|
| `engines.yaml` | Table engine variants a source may select. `arguments` is `none`, `optional` or `required`, and `argument_hint` is what goes inside the parentheses | dfe-engine's source save validation and `GET /api/v1/sources/engines`, the console's engine list |
| `types.yaml` | Column primitives and their ClickHouse type, codec and nullability, plus use-case, attribute and `ch_override` rules, and the labels and override options a schema form offers | dfe-engine's column validation and the schema form vocabulary it serves, and DDL rendering |
| `ddl.yaml` | The DDL catalogue: table defaults (index granularity, partition column and functions) and the index each use case renders | DDL rendering |
| `field-maps/<standard>/_default.yaml` | The default field map for each view standard: `cim`, `ecs`, `ocsf` and `sigma` | Seeding dfe-engine's field-map store |
| `catalogue-tags.yaml` | The [catalogue](catalogue.md)'s folders, with the name and description the console shows | `GET /api/v1/sources/catalogue-tags` |
| `elastic-variants.yaml` | Every variant dfe-transform-elastic registers, with the intakes each accepts | `tests/test_catalogue.py`, which checks the catalogue's elastic entries against it |

`make validate` checks every `engines.yaml` entry and loads `types.yaml` through dfe-engine's own registry. `make test` checks the rest.
