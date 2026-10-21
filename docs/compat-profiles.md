# Compatibility profiles

A compatibility profile reads the header fields another platform, or an older DFE, writes, and sets the DFE header from them. It is data: versioned rule rows that dfe-engine compiles into the first hop's ordinary rules, so dfe-receiver and dfe-fetcher never learn that profiles exist. Profiles live in `compat-profiles/`, one file per profile, and ship in the wheel under `dfe_schemas/data/compat-profiles/`. Two ship:

| Profile | Reads | Sets | Off until a source opts in |
|---------|-------|------|----------------------------|
| `dfe-legacy-2x` | `tags.event.category` through the `aliases` table | `_source` | |
| | `tags.event.org_id`, then `org_id`, skipping `unknown` and `dfesystem` | `_org_id` | |
| `elastic-agent` | `data_stream.dataset` through the `aliases` table | `_source` | |
| | `data_stream.namespace` | `_org_id` | `org-namespace` |

DFE 2.1 wrote `unknown` where it had no org and `dfesystem` for its DLQ. Neither is a tenant, so both fall through to the default org instead of counting as a clamp violation. A 2.x receive time needs no row: the first hop stamps its own `_timestamp_received`, and the header reads `timestamp_received` and `received_at` after it.

`data_stream.dataset` is the identifier Elastic gives each integration's data stream (`cisco_ios.log`), so one lookup covers every Elastic-shipped source. A namespace is free text the sender picks, which is why it names a tenant only on a source that says so. Elastic's `data_stream.namespace` commonly defaults to `default`, which maps to the DFE default org when the `org-namespace` row is opted in.

## The format

```yaml
id: elastic-agent              # the file name, and what a source binds as <id>@<version>
resource_type: core            # core ships here and refuses edits, custom is an operator's own
current: "1.0.0"
versions:
  "1.0.0":
    date: "2026-10-21"
    type: model                # SchemaVer: model, addition or revision
    summary: "..."
    title: Elastic Agent       # the UI name
    description: >-            # the UI tooltip
      ...
    covers:                    # the senders it reads, shown as a chip
      - platform: elastic-agent
    targets: [_source, _org_id]
    lookups:
      aliases: {}              # empty here, filled by the engine
    rules:
      - id: dataset
        set: {_source: {lookup: aliases, from: data_stream.dataset}}
      - id: org-namespace
        match: {field: data_stream.namespace, op: exists}
        set: {_org_id: {from: data_stream.namespace}}
    opt_in: [org-namespace]
```

Two checks own a profile between them. `compat-profiles/profile.schema.json` (JSON Schema 2020-12) owns the document: the version tree, the fields a row may set, ids and the `opt_in` list. scalo.rules owns what a row means, so `targets`, `lookups` and `rules` are exactly a scalo.rules rule set, and the tests compile every version with scalo's own checker. The row format, its tests and its actions are in scalo-py's `docs/api/RULES.md`.

A profile sets scalar header fields only: `_source`, `_org_id` and `_timestamp`. Moving a subtree or reshaping a payload is a transform's job. Each version is a complete snapshot and a published one never changes, so a fix is a new version entry.

## Compiling and adding one

`dfe_schemas.compat.rule_set()` hands one version out as the mapping `scalo.rules.RuleSet.compile` takes. Its `name` is the profile id, and a row listed under `opt_in` is left out unless the caller names it.

```python
from scalo.rules import Clamp, CompileOptions, RuleSet

from dfe_schemas.compat import rule_set

config = rule_set("elastic-agent", opt_in=["org-namespace"])
config["lookups"]["aliases"] = {"cisco_ios.log": "cisco-ios"}
rules = RuleSet.compile(config, CompileOptions())
outcome = rules.evaluate(record, Clamp("_org_id", {"default", "ep"}, "default"))
```

A shipped `aliases` table is empty. dfe-engine fills it from each registered source's aliases for that profile before it compiles, so a value no source claims misses the lookup and the next row decides. A profile row that copies a sender's field into `_org_id` is bounded the way a sender's own `_org_id` is: the first hop clamps the result to the orgs the sender's credential is bound to.

To add a profile:

1. Write `compat-profiles/<id>.yaml`. The file name is the id: lower-case words joined by hyphens.
2. List only header fields under `targets`, `_source` before `_org_id`, so an org row can test the source the same pass chose.
3. Add the id to `SHIPPED` in `tests/test_compat_profiles.py`, with a test that evaluates a real record from that sender.
4. `make test`, then release the wheel and raise the floor in dfe-engine, as for any other definition.
