# The source catalogue (`catalogue/`)

`catalogue/` holds the sources DFE ships ready to create, one file per source, with the source's name. Each file is a `POST /api/v1/sources` write body, plus a `catalogue` block the write model ignores. dfe-engine reads the catalogue out of the installed wheel and compiles a body for the intake a create names.

```yaml
source: cisco-ios
display_name: cisco_ios.log
description: The log data stream of the Elastic cisco_ios integration.
match:
  field: _source
  operator: equals
  value: cisco-ios
schema:
  meta_schema: meta/elastic/ecs
  derived_schema: derived/cisco_ios/log
  additional_fields: additional/cisco_ios/log
transform:
  engine: elastic
  variant: filebeat.cisco_ios.default
catalogue:
  package: cisco_ios
  data_stream: log
  intakes: [beats, receiver]
  framing: line
  variants:
    - filebeat.cisco_ios.default
  detect:
    order: 100
    markers:
      - priority: 10
        any: ["%"]
        verify: ios_mnemonic
        note: "%FAC-n-MNEM: catch-all, below every other %-style source"
```

A file sits in the folder for the quick-add tag the console offers it under, `uncategorised` for a source with none. `registries/catalogue-tags.yaml` gives each folder the name and description the console shows, and dfe-engine serves them at `GET /api/v1/sources/catalogue-tags`. `tests/test_catalogue.py` rejects a file outside a listed folder, a listed folder with no file and a source name in two folders.

## The `catalogue` block

| Key | What it holds |
|-----|---------------|
| `package`, `data_stream` | The integration package and data stream the source reads |
| `intakes` | Every way the data can arrive: `beats`, `receiver`, `fetcher` |
| `framing` | Receiver intakes: `line` or `body`, whether the pipeline wants the syslog line or the body |
| `beats` | The Beats module and fileset carrying the same source, where one does |
| `variants` | Every compiled transform for the source, with the body's `transform.variant` first |
| `detect` | An elastic entry with the receiver intake: the line markers dfe-transform-elastic's agentless mode identifies the source by |

`detect.markers` is a list. Each marker has a `priority` from 0 to 255 and exactly one of `any` (literals, any one on the line fires it), `all` (up to 8 literals that must all appear) or `cef` (a CEF or LEEF header's `vendor` and `product`, compared without regard to case). An `any` marker can name a `verify` check that dfe-transform-elastic runs on the bytes around the hit, and `note` says why the marker is there.

The highest priority wins. A tie between literal markers goes to the source with the lower `detect.order`, which every entry with an `any` or `all` marker declares and no two entries share. A CEF marker loses a tie to any literal marker, and CEF ties go by transform variant. dfe-transform-elastic generates its marker table from these blocks in the dfe-schemas release it pins.

`registries/elastic-variants.yaml` lists every variant dfe-transform-elastic registers, with the intakes each accepts. That repo's CI generates it and opens a `chore(elastic-variants)` PR here when it changes. `tests/test_catalogue.py` rejects an elastic entry with a variant not listed there, or one listing an intake that one of its variants does not accept.

The body is written for the first of `fetcher`, `receiver` and `beats` the entry lists, so it has a `fetcher` block exactly when `fetcher` is an intake. dfe-engine replaces the match for the intake a create names: `beats` matches `data_stream.dataset` equal to `<package>.<data_stream>`, and `receiver` matches `_source` equal to the source name. A create that names no intake takes the first of `fetcher`, `beats` and `receiver` the entry lists.

An entry whose sole intake is `receiver` keeps the `match` it is written with, which is one `equals` test on a field the shipper sends, such as `@edgestream.event.src_type` for `edgestream/edgestream-hub-syslog.yaml`. A `_source` match there must still name the source. An entry may carry no `transform`, and then lists no `variants`.

Schema layers name no version pin. dfe-engine binds no schema for a body without `schema`. `fetcher` is an intake on a body with a schema and never on one without, because dfe-engine rejects a fetcher source that has a dedicated topic and no schema.

`tests/test_catalogue.py` checks the format, and `make validate` builds every body as dfe-engine's `SourceWriteRequest`.
