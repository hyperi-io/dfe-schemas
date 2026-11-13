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
bump - never hand-edit `VERSION`, `CHANGELOG.md`, or the version in
`pyproject.toml`. The tag follows
[Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR** (X.0.0): Breaking changes that require users to modify their code
- **MINOR** (0.X.0): New features that are backwards-compatible
- **PATCH** (0.0.X): Bug fixes and minor improvements

Schema YAML files carry their own independent version trees, unrelated to the
package version - see [docs/meta-schema.md](docs/meta-schema.md).

## Developer Certificate of Origin

This project uses the Developer Certificate of Origin (DCO) to ensure that
contributors have the right to submit their contributions.

By making a contribution to this project, you certify that:

1. The contribution was created in whole or in part by you and you have the
   right to submit it under the license indicated in the file; or

2. The contribution is based upon previous work that, to the best of your
   knowledge, is covered under an appropriate open source license and you
   have the right under that license to submit that work with modifications;
   or

3. The contribution was provided directly to me by some other person who
   certified (1), (2) or (3) and you have not modified it.

4. You understand and agree that this project and the contribution are public
   and that a record of the contribution (including all personal information
   you submit with it, including your sign-off) is maintained indefinitely
   and may be redistributed consistent with this project or the license(s)
   involved.

## How to Sign Off Your Commits

You must sign off each commit to indicate your acceptance of the DCO. Combine
the signoff with your conventional commit message:

```bash
git commit --signoff -m "feat(auth): add two-factor authentication"
```

This produces:

```text
feat(auth): add two-factor authentication

Signed-off-by: Your Name <your.email@example.com>
```

Make sure your Git configuration has your correct name and email:

```bash
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

## License for Contributions

All contributions to this project are licensed under the Business Source
License 1.1 (BUSL-1.1), the same license that covers the project.

Each version of the software (including your contributions) will automatically
become available under the Apache License, Version 2.0 on the third
anniversary of its release.

## How to Contribute

1. **Fork the repository** and create your branch from `main`
2. **Make your changes** following the commit message format above
3. **Sign off your commits** with the DCO
4. **Test your changes** to ensure they work as expected
5. **Submit a pull request** with a clear description of what you've done

### Pull Request Checklist

- [ ] Commits follow the conventional commit format
- [ ] All commits are signed off (DCO)
- [ ] Tests pass (if applicable)
- [ ] Documentation is updated (if applicable)

## Run the checks locally before you push

HyperI projects gate every push through CI (lint, format, tests, secret scan,
dependency audit, build). Run the SAME checks locally first so your change lands
green instead of bouncing:

```bash
hyperi-ci check
```

`hyperi-ci` is the HyperI CI CLI (public on PyPI). If you do not have it, install
it once with `uv tool install hyperi-ci` (or `pipx install hyperi-ci`), or run it
ad hoc with `uvx hyperi-ci check`. It runs the project's full local validation --
the same suite CI runs -- and reports what to fix. Run it before every push and before opening a
pull request. It is the single best way to give your change the best chance of
surviving CI.

## CI/CD Workflow

Two workflows run on every push and pull request. `ci.yml` is the standard
hyperi-ci pipeline - quality, tests, build. `validate-schemas.yml` validates
every schema YAML against the dfe-engine meta-schema, which needs a cross-repo
checkout hyperi-ci has no stage for.

A push never publishes. To cut a release, run the CI workflow by hand
(`workflow_dispatch`) with `from-head=true`, and semantic-release picks the
version, tags it, and the release tail publishes the wheel to PyPI.

## Questions

If you have questions about contributing, please open an issue or contact us.

## Context

### What this is

The schema and DDL source of truth for the DFE suite -- every table, view, TTL,
ClickHouse role, grant and Kafka bootstrap topic, plus the renderer. It ships as
the PyPI package `dfe-schemas`, NOT a git submodule: it was one, and the older
half of `docs/meta-schema.md` still describes one. dfe-engine reads the trees out
of the installed wheel unless `DFE_ENGINE_SCHEMAS_DIR` points elsewhere. Nothing here
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
