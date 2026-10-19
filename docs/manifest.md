# The apply manifest (`manifest.yaml`)

One list of every object a DFE deployment creates, in the order it creates
them. dfe-engine iterates it at boot and applies nothing that is not in it.

Before it, the order lived in three Python tuples in dfe-engine, a Helm helper
in dfe-infra and a resolver in dfe-docker, and the only way to know what a
deployment would create was to read all five.

## Top level

| Key | Meaning |
|---|---|
| `manifest_version` | The format this file is written in. The reader refuses a version it does not know rather than guessing at the fields. |
| `parameters` | The deployment values the renderer substitutes: the data database, the default retention, Kafka tiered storage. Each names the setting that supplies it. The ClickHouse platform is not one of them: it is probed from the server. |
| `databases` | The databases objects land in, by KEY. A key carries either `parameter` (the deployment supplies the name) or `name` (fixed). |
| `objects` | The list. |

A database is addressed by key rather than by name because a deployment can
rename its data database. A literal would create the object, grant on it and
query it in a database that deployment does not have.

## An object

| Key | Meaning |
|---|---|
| `id` | Unique across the manifest. Two definitions claiming one name means whichever applies second silently wins, so this is checked. |
| `kind` | `database`, `table`, `materialized_view`, `view`, `role` or `topic`. |
| `database` | The declared database key. A topic names none. |
| `defines` | The definition file, relative to the schemas root, without a suffix. `.yaml` for everything but a view, which is `.sql`. |
| `section` / `key` | Where inside `defines` the entry is, for the files that carry several: a role's section in `roles/clickhouse.yaml`, a topic's section in `topics/kafka.yaml`, a materialised view's name in a table file's `materialized_views`. |
| `header` | The common-header profile to compose, for a table whose columns come from one. |
| `compose` | A second definition composed over the header (or used alone), e.g. `hunts/results` for the detection table. |
| `topology` | `resolved` renders per ClickHouse platform, and `fixed` renders the same everywhere. ClickHouse access objects and Kafka topics are `fixed`. |
| `additive` | Whether an additive change may be applied automatically. A changed type, ORDER BY, PARTITION BY, TTL or codec is drift either way and is refused. |
| `optional` | The apply may skip this object without failing the pass. |
| `depends_on` | Ids declared EARLIER in the list. |

`depends_on` is checked against what precedes it, not merely against what
exists. A view over a table declared after it is an apply that fails on a fresh
cluster and succeeds on every re-run afterwards -- it only breaks for whoever
stands one up.

## Platform and checksums

Every table renders `ENGINE = Replicated<variant>` with no `ON CLUSTER`, on
ClickHouse Cloud and self-hosted ClickHouse alike. The platform the caller names
picks only the CREATE DATABASE and a JSON table's platform-only settings.
`docs/clickhouse-platform.md` has the probe, the SQL variants and what a
self-hosted server needs.

Every rendered object carries a sha256 over its statements with the platform
token removed -- the database's `ON CLUSTER` and `ENGINE`, a table's
`Replicated` prefix or empty argument list, and the platform-only settings --
and whitespace collapsed.

That is what makes one schema read as one checksum on either platform. With the
platform in it, moving a deployment between Cloud and self-hosted would read as
drift on every object and the migration ledger would report a change on a
deploy that changed nothing.

## What the renderer refuses

`make render` (`scripts/render_manifest.py`) fails on:

* a duplicate `id`, or a `depends_on` that is unknown or declared later
* two objects rendering to the same qualified name
* a definition that will not render on some platform
* a table that stores rows and whose engine is not `Replicated*`, or a
  statement other than the self-hosted CREATE DATABASE that carries `ON CLUSTER`
* a refreshable materialised view whose `refresh` is not an `EVERY` or `AFTER`
  schedule, which is what keeps `APPEND` and one replica per refresh
* a statement naming a `<database>.<object>` the manifest does not declare
* a checksum that differs between platforms

## Known gaps

**`dfe_v_analytics_event_counts`** selects `FROM events`, an unqualified table
no DFE object declares and no bootstrap creates. It resolves against whichever
database the connection is on, so the reference check above does not see it.
The body was carried across from dfe-engine unchanged rather than guessed at;
the view needs either a declared `events` table or a rewrite onto one that
exists.

**Row policies** are not manifest objects. They are written per table against
the ones that actually carry `_org_id`, which is read off the live server;
`roles/clickhouse.yaml` declares the predicate they use and the name they get,
so the reconciler writes no policy of its own invention.

**Per-source tables** are not manifest objects either. The manifest is the set
a deployment creates before anything streams; a source's own table is created
when that source is deployed, from the same renderer.
