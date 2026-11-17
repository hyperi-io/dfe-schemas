# Contributing

We welcome contributions to this project. By contributing, you agree to the
terms outlined below.

## Commit Message Format

HyperI projects use [Conventional Commits](https://www.conventionalcommits.org/).
All commits must follow this format:

```text
<type>(<scope>): <subject>

[optional body]

[optional footer(s)]
```

### Types

| Type | Description | Severity signalled |
|------|-------------|--------------------|
| `feat` | A new feature | Minor (0.X.0) |
| `fix` | A bug fix | Patch (0.0.X) |
| `docs` | Documentation only | None |
| `style` | Code style (formatting, semicolons, etc.) | None |
| `refactor` | Code change that neither fixes a bug nor adds a feature | None |
| `perf` | Performance improvement | Patch (0.0.X) |
| `test` | Adding or correcting tests | None |
| `build` | Changes to build system or dependencies | None |
| `ci` | Changes to CI configuration | None |
| `chore` | Other changes that don't modify src or test files | None |
| `revert` | Reverts a previous commit | Varies |

### Breaking Changes

For breaking changes that require a major version bump, add `!` after the type
or include `BREAKING CHANGE:` in the footer:

```text
feat!: remove deprecated API endpoints

BREAKING CHANGE: The /v1/users endpoint has been removed. Use /v2/users instead.
```

### Examples

```text
feat(auth): add OAuth2 support for Google login

fix(api): handle null response from upstream service

docs: update installation instructions for Windows

refactor(core)!: restructure module exports

BREAKING CHANGE: Named exports are now used instead of default exports.
```

### Scope

Scope is optional but recommended. Use it to indicate the area of the codebase
affected (e.g., `api`, `auth`, `core`, `cli`, `docs`).

## Versioning

This repo publishes the `dfe-schemas` wheel to PyPI, and semantic-release owns
its version. Land correctly-typed conventional commits and the CI derives the
bump - never hand-edit `CHANGELOG.md` or the version in `pyproject.toml`. The
tag follows [Semantic Versioning 2.0.0](https://semver.org/):

- MAJOR (X.0.0): breaking changes that require users to modify their code
- MINOR (0.X.0): new features that are backwards-compatible
- PATCH (0.0.X): bug fixes and minor improvements

Schema YAML files carry their own independent version trees, unrelated to the
package version - see [docs/meta-schema.md](docs/meta-schema.md).

## License for Contributions

All contributions to this project are licensed under the Business Source
License 1.1 (BUSL-1.1), the same license that covers the project.

Each version of the software (including your contributions) will automatically
become available under the Apache License, Version 2.0 on the third
anniversary of its release.

## How to Contribute

1. Fork the repository and create your branch from `main`
2. Make your changes, following the commit message format above
3. Run the checks below
4. Open a pull request saying what the change does

### Pull Request Checklist

- [ ] Commits follow the conventional commit format
- [ ] Tests pass (if applicable)
- [ ] Documentation is updated (if applicable)

A question about contributing goes in a new issue.

## Run the checks locally before you push

CI runs lint, format, tests, secret scan and dependency audit on every pull
request, and on a push that changes code or carries a releasing commit. A push
that changes only docs skips them, so the local run is the only check it gets:

```bash
hyperi-ci check
```

`hyperi-ci` is the HyperI CI CLI (public on PyPI). If you do not have it, install
it once with `uv tool install hyperi-ci` (or `pipx install hyperi-ci`), or run it
ad hoc with `uvx hyperi-ci check`. It runs the quality and test stages CI runs
and reports what to fix. It does not run `validate-schemas.yml`: run
`make render` and `make validate` for that.

## CI/CD Workflow

Two workflows run on every push and pull request. `ci.yml` is the standard
hyperi-ci pipeline: quality, tests, and the build and publish on a release.
`validate-schemas.yml` renders the manifest for every platform and runs
`scripts/validate_schemas.py`. That checks the `current` version of each schema,
derived and table file with dfe-engine's loader, and builds each source and
catalogue file as an engine model, so it needs a cross-repo checkout hyperi-ci
has no stage for. It does not walk `dlq/`.

A push to main publishes only when the head commit's message carries a
`Release: true` trailer. To release without one, run the CI workflow by hand
(`workflow_dispatch`) with `from-head=true`: semantic-release picks the version
and tags it, and the release tail publishes the wheel to PyPI.

## Context

### What this is

The schema and DDL source of truth for the DFE suite -- every table, view, TTL,
ClickHouse role, grant and Kafka bootstrap topic, plus the renderer. It ships as
the PyPI package `dfe-schemas`. dfe-engine reads the trees out of the installed
wheel unless `DFE_ENGINE_SCHEMAS_DIR` points elsewhere. Nothing here touches a
database -- this repo supplies definitions and dfe-engine applies them.

### Where things live

| Path | What it holds |
|------|---------------|
| `manifest.yaml` | Every object a deployment creates, in dependency order |
| `dfe_schemas/` | The package: manifest reader, renderer, loader, platform probe, `sql/platform.sql` |
| `tables/`, `views/`, `roles/`, `topics/` | Definitions in exact ClickHouse types, SQL, the role catalogue, the Kafka policy |
| `common-header/`, `meta/`, `additional/`, `hunts/`, `dlq/` | Definitions in DFE primitives, mapped through `registries/types.yaml` |
| `derived/` | Select lists over a base meta schema, by package and data stream |
| `compat-profiles/` | Compatibility profiles, their JSON schema, and rows in the scalo.rules format |
| `sources/` | The core sources dfe-engine owns, such as `main` |
| `catalogue/` | Sources shipped ready to create, one `POST /sources` body per file |
| `registries/` | The allow-lists dfe-engine validates against, and the DDL catalogue every table renders from |
| `docs/architecture.md` | The parts, how they fit, and why it is built this way |

The package modules, by the part on [docs/architecture.md](docs/architecture.md) they belong to. Manifest is `manifest.py`. Renderer is `render.py`, `clickhouse.py`, `variants.py` and `ddl_catalogue.py`. Definitions are read by `loader.py`, `compat.py`, `topics.py`, `usage.py` and `deploy_defaults.py`.

| Module | What it owns |
|--------|--------------|
| `__init__.py` | `schemas_root()` and `manifest_path()`, the one path resolution |
| `manifest.py` | Reads the manifest and rejects a bad one |
| `loader.py` | Version trees, the two ways to declare a column, composition, the type registry, `quote_ident` |
| `compat.py` | The compatibility profiles, each version handed out as a scalo.rules rule set |
| `clickhouse.py` | The platform probe, the Keeper check and the one `ENGINE` form |
| `variants.py` | Picks one platform's block out of `sql/platform.sql`, knowing nothing of ClickHouse |
| `ddl_catalogue.py` | Reads `registries/ddl.yaml`: the table defaults and the index each use case renders |
| `render.py` | One object to statements, for one platform, with a checksum, and the one-DDL gate |
| `topics.py` | The Kafka naming rule, defaults and bootstrap set |
| `usage.py` | The names the usage views read and write, filled into their SQL |
| `deploy_defaults.py` | The declared deployment defaults |

### Commands that prove a change

```bash
make render      # every object, every platform -- needs only this package
make test        # the pytest suite
make validate PY=../dfe-engine/.venv/bin/python   # the meta schemas
hyperi-ci check  # the quality gate CI runs
```

`make render` needs nothing but this package, and prints the objects it rendered
by kind for both platforms. Coverage is gated by `fail_under` in `pyproject.toml`.

`make test` also starts ClickHouse in docker for `tests/test_usage_rollup.py`,
which applies the whole manifest and checks the usage numbers, and for
`tests/test_index_pruning.py`, which checks each documented query prunes.
Without docker those tests skip, and in CI they fail instead.

`make validate` is the only step needing another repo, which is why it runs from
`validate-schemas.yml` and not `ci.yml`
([docs/architecture.md](docs/architecture.md) has the reason).

### What tends to bite

| Don't | Do | Why |
|-------|----|-----|
| Trust `make render` to mean an index string is valid | Run `tests/test_tokenizers.py` | It checks every declared index tokenizer against the ones ClickHouse still accepts, which the renderer does not (`8934979`) |
| Edit a published version entry | Add a new entry, as a complete snapshot | Consumers pin versions independently, so an in-place edit changes what an already-pinned consumer resolves. `generate_meta_schemas.py` refuses it |
| Merge a table and expect a deployment to get it | Release the wheel, raise the floor in dfe-engine, relock, release the engine | Nothing reads this repo directly. The engine image ships the wheel its lock pins, so the engine release is what puts an object in front of a deployment |
| Wrap an `ORDER BY` key in `Nullable` | Leave it bare, or mark it `not_null` | `_sorting_key` refuses a Nullable key rather than dropping it. JSON inside Nullable is a different case: ClickHouse 24.8 refused it, code 43 (`2f28227`), but 26.3.32.14 creates and fills a `Nullable(JSON)` column |
| Point `PY` at a bare `python` | Leave it at `uv run python`, or name an interpreter with dfe-engine installed | A bare `python` is absent on a stock Linux box (`6c79570`) |

The invariants below are checks, not conventions. Without each one, the failure looks like success.

- A duplicate object id is rejected: two definitions claiming one name means whichever applies second wins.
- A dependency that is unknown or declared later is rejected: a view over a table that does not exist yet fails on a fresh cluster and succeeds on every re-run, the worst failure a bootstrap can have.
- A Nullable `ORDER BY` key is rejected rather than dropped. ClickHouse rejects one unless `allow_nullable_key` is on.
- A TTL over an absent column is rejected, because the table would keep every row forever. `ttl_only_drop_parts` is 1 when every TTL column appears in the partition expression, else 0: it waits for every row in a part to expire, so an unaligned table takes the row-level delete instead.
- A refreshable materialised view renders `APPEND` and an `EVERY` or `AFTER` schedule, and nothing else. Without `APPEND` each refresh swaps the target for its result and drops every day the source no longer holds. A `SETTINGS all_replicas = 1` would run every refresh on every replica, where the Replicated database hands each one to a single replica.
- A `${name}` placeholder in a view with no value in `dfe_schemas.usage.SQL_VALUES` is rejected, so the names the usage views read cannot drift from the ones the engine imports.
- A role is created before its quota, because ClickHouse rejects a quota naming a role that does not exist yet, and that shows up against a cluster where the role was not present.
- In a CREATE TABLE the database, table, column and index names go through `quote_ident`, which rejects a backtick, quote, parenthesis, semicolon, backslash or whitespace. Everything shipped here is committed, so this catches an edit rather than an attack, but such a name would end the statement early and start another one.

Every rendered statement converges. An `IF NOT EXISTS` creates and an `ALTER` re-asserts mutable state, so an edit reaches an object that exists.

### Where this sits

The edges below are the ones `dfe-infra/suite.yaml` declares
(`dfe-stack suite --producer dfe-schemas`). Nothing in the suite feeds this repo
-- it declares no inbound edges and the package has one runtime dependency,
`ruamel.yaml`.

| Repo | Kind | Mechanism, outbound |
|------|------|---------------------|
| dfe-engine | `python-dep`, potential | Declares `dfe-schemas` by range in `pyproject.toml`, below 1. The only applier |
| dfe-infra | `version-pin`, lockstep | `versions.yaml` records this wheel's version, tagged with the stack release |

dfe-deploy pins no dfe-schemas version: the wheel rides inside the engine image.
`make validate` needing dfe-engine is deliberately not an edge -- that cycle is
why `dfe_schemas/loader.py` exists. dfe-loader reads the deployed ClickHouse
schema from `system.columns`, never this YAML.
