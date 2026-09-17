#  Project:      dfe-schemas
#  File:         dfe_schemas/render.py
#  Purpose:      Render every manifest object for a chosen topology, checksummed
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Turn the manifest into statements, for a topology the caller names.

The topology is a PARAMETER, never a literal in a definition. That is the whole
point of rendering here: the same table is rendered plain for a keeperless
node, ``Replicated`` for a Replicated database or Cloud, and ``Replicated`` plus
``ON CLUSTER`` for an Atomic database on a real cluster, from one definition. A
literal engine is correct on a single node and lands on one replica of a
cluster, where every query succeeds and the answer depends which node answered.

Every rendered object carries a CHECKSUM. It is taken over the statement with
the topology token removed -- the ``ON CLUSTER`` suffix, the ``Replicated``
prefix and the empty argument list that goes with it -- and whitespace
collapsed, so one schema reads as one checksum whether it was applied to a
single node or to a cluster. That is what lets the applier's ledger answer
"has this object changed" rather than "was it applied somewhere else".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dfe_schemas.clickhouse import EngineSpec, Topology, parse_engine, render_engine
from dfe_schemas.loader import (
    Column,
    SchemaError,
    TypeRegistry,
    compose,
    load_columns,
    load_profile,
    load_profile_exclude,
    load_version_entry,
    quote_ident,
    read_yaml,
    resolve,
)
from dfe_schemas.manifest import Manifest, ManifestObject
from dfe_schemas.topics import TopicPolicy, load_topic_policy

__all__ = [
    "RenderedObject",
    "Renderer",
    "checksum",
    "qualified_name",
    "referenced_objects",
]

# GA text index (26.2+): a deterministic inverted index with row-level
# filtering. A column's use_case picks one; a shape these cannot express is
# written out in the definition's own `indexes` list instead.
_INDEX_TEMPLATES: dict[str, str] = {
    "dimension": "INDEX {name} {col} TYPE set(0) GRANULARITY 4",
    "fulltext": "INDEX {name} {col} TYPE text(tokenizer=splitByNonAlpha) GRANULARITY 1",
    "text_search": "INDEX {name} {col} TYPE text(tokenizer=ngrams(3)) GRANULARITY 1",
    "range": "INDEX {name} {col} TYPE minmax GRANULARITY 4",
    "bloom": "INDEX {name} {col} TYPE bloom_filter GRANULARITY 4",
}

_PARTITION_FUNCS = {"day": "toYYYYMMDD", "month": "toYYYYMM"}

_DEFAULT_INDEX_GRANULARITY = 2048
_DEFAULT_PARTITION_COLUMN = "_timestamp_load"
_DEFAULT_PARTITION_GRANULARITY = "day"

_ON_CLUSTER_RE = re.compile(r"\s+ON CLUSTER\s+\S+")
# The topology token inside an ENGINE clause: the Replicated prefix, and the
# empty argument list the single form carries and the replicated form drops.
_ENGINE_RE = re.compile(r"ENGINE = (?:Replicated)?([A-Za-z]*MergeTree)(?:\(\))?")


def checksum(statements: tuple[str, ...] | list[str]) -> str:
    """sha256 over the statements with the topology token out and space collapsed."""
    normalised = []
    for statement in statements:
        text = _ON_CLUSTER_RE.sub("", statement)
        text = _ENGINE_RE.sub(r"ENGINE = \1", text)
        normalised.append(" ".join(text.split()))
    return hashlib.sha256("\n".join(normalised).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RenderedObject:
    """One manifest object, rendered for one topology."""

    id: str
    kind: str
    database: str | None
    name: str
    topology: str
    additive: bool
    optional: bool
    statements: tuple[str, ...]
    checksum: str
    # For a topic: the fields the admin client is handed. Empty otherwise.
    topic: dict[str, Any] | None = None


def _strip_leading_comments(body: str) -> str:
    """Drop a ``.sql`` file's opening comment block.

    The comment is for whoever opens the file; the statement is what gets
    applied and checksummed. Leaving it in would make a reworded comment read
    as a changed view on every deployment's next apply.
    """
    lines = list(body.splitlines())
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines)


def qualified_name(rendered: RenderedObject) -> str:
    """What a rendered object is known by: database-qualified, or bare for a topic."""
    if rendered.database is None:
        return f"topic:{rendered.name}"
    return f"{rendered.database}.{rendered.name}"


def referenced_objects(rendered: RenderedObject) -> set[str]:
    """Every DFE object a rendered statement names, other than itself.

    Only ``<database>.<object>`` counts. A bare name resolves against whichever
    database the connection is on and a quoted one is a string literal, so
    neither is a reference anything can check from the text.
    """
    if rendered.database is None:
        return set()
    pattern = re.compile(rf"(?<!')\b{re.escape(rendered.database)}\.`?([A-Za-z_][A-Za-z0-9_]*)`?")
    found = {
        f"{rendered.database}.{name}"
        for statement in rendered.statements
        for name in pattern.findall(statement)
    }
    return found - {qualified_name(rendered)}


@dataclass(frozen=True)
class _TableConfig:
    """The DDL clauses a definition declares, with nothing defaulted silently."""

    engine: str = "MergeTree"
    ttl_days: int | None = None
    ttl_columns: tuple[str, ...] = ()
    projection_order_by: str | None = None
    partition_by: str | None = None
    partition_column: str = _DEFAULT_PARTITION_COLUMN
    partition_granularity: str = _DEFAULT_PARTITION_GRANULARITY
    order_by: str | None = None
    extra_indexes: tuple[str, ...] = ()
    index_granularity: int = _DEFAULT_INDEX_GRANULARITY


def _table_config(block: dict[str, Any]) -> _TableConfig:
    """Read a definition's ``table`` block.

    TTL and the projection default to OFF rather than to the landing table's
    defaults: an engine-state table has none of the columns those clauses name,
    and a default nobody can see is how a table ends up keeping everything.
    """
    return _TableConfig(
        engine=block.get("engine", "MergeTree"),
        ttl_days=block.get("ttl_days"),
        ttl_columns=tuple(block.get("ttl_columns") or ()),
        projection_order_by=block.get("projection_order_by"),
        partition_by=block.get("partition_by"),
        partition_column=block.get("partition_column", _DEFAULT_PARTITION_COLUMN),
        partition_granularity=block.get("partition_granularity", _DEFAULT_PARTITION_GRANULARITY),
        order_by=block.get("order_by"),
        extra_indexes=tuple(block.get("indexes") or ()),
        index_granularity=int(block.get("index_granularity", _DEFAULT_INDEX_GRANULARITY)),
    )


class Renderer:
    """Renders manifest objects for one topology.

    Construct one per topology. ``cluster`` is only read under
    ``replicated_on_cluster`` -- every other topology fans nothing out.
    """

    def __init__(
        self,
        manifest: Manifest,
        *,
        topology: Topology = Topology.SINGLE,
        data_database: str = "dfe",
        default_ttl_days: int | None = 90,
        cluster: str = "dfe_cluster",
        broker_count: int = 1,
        kafka_tiered_storage: bool = False,
    ) -> None:
        self._manifest = manifest
        self._topology = topology
        self._data_database = data_database
        self._default_ttl_days = default_ttl_days
        self._cluster = cluster
        self._broker_count = broker_count
        self._kafka_tiered_storage = kafka_tiered_storage
        self._root = manifest.root
        self._registry = TypeRegistry.load(root=self._root)
        self._topics: TopicPolicy | None = None

    # -- entry points --------------------------------------------------------

    def render(self, obj: ManifestObject) -> RenderedObject:
        """One object."""
        handler = {
            "database": self._render_database,
            "table": self._render_table,
            "materialized_view": self._render_materialized_view,
            "view": self._render_view,
            "role": self._render_role,
            "topic": self._render_topic,
        }.get(obj.kind)
        if handler is None:
            raise SchemaError(f"{obj.id}: nothing renders a {obj.kind!r}")
        return handler(obj)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _required(obj: ManifestObject, field: str) -> str:
        """A manifest field this kind cannot render without.

        The manifest's own check makes these present per kind; this is what a
        hand-built object hits instead of an attribute error deeper down.
        """
        value = getattr(obj, field)
        if value is None:
            raise SchemaError(f"{obj.id}: a {obj.kind} needs {field!r}")
        return str(value)

    def _database(self, obj: ManifestObject) -> str:
        return self._manifest.database_name(
            self._required(obj, "database"), data_database=self._data_database
        )

    def _engine(self, engine: str) -> tuple[str, str]:
        """The ENGINE clause and the ON CLUSTER suffix for this topology."""
        rendered = render_engine(parse_engine(engine), self._topology, cluster=self._cluster)
        return rendered.clause, rendered.on_cluster

    @property
    def _on_cluster(self) -> str:
        """The ON CLUSTER suffix for a statement that carries no engine."""
        return render_engine(
            EngineSpec("MergeTree"), self._topology, cluster=self._cluster
        ).on_cluster

    def _finish(
        self,
        obj: ManifestObject,
        name: str,
        statements: list[str],
        *,
        database: str | None,
        topic: dict[str, Any] | None = None,
    ) -> RenderedObject:
        frozen = tuple(statements)
        return RenderedObject(
            id=obj.id,
            kind=obj.kind,
            database=database,
            name=name,
            topology=self._topology.value,
            additive=obj.additive,
            optional=obj.optional,
            statements=frozen,
            checksum=checksum(frozen),
            topic=topic,
        )

    # -- databases -----------------------------------------------------------

    def _render_database(self, obj: ManifestObject) -> RenderedObject:
        """A database, cluster-wide when the topology fans out.

        Without the suffix here, ON CLUSTER table DDL lands on nodes that have
        no database to put it in.
        """
        database = self._database(obj)
        statement = f"CREATE DATABASE IF NOT EXISTS {database}{self._on_cluster}"
        return self._finish(obj, database, [statement], database=database)

    # -- tables --------------------------------------------------------------

    def _columns(self, obj: ManifestObject, entry: dict[str, Any], path: Path) -> list[Column]:
        """The table's columns: its own, or a header composed with an extra file."""
        if obj.header:
            header = load_profile(obj.header, root=self._root)
            if not obj.compose:
                return header
            extra_path = resolve(obj.compose, root=self._root)
            return compose(
                header,
                load_columns(extra_path),
                exclude=load_profile_exclude(extra_path),
            )
        if obj.compose:
            return load_columns(resolve(obj.compose, root=self._root))
        if "columns" not in entry:
            raise SchemaError(
                f"{obj.id}: {path} declares no columns and the manifest names no header or compose"
            )
        return load_columns(path)

    def _profile_comment(self, obj: ManifestObject) -> str | None:
        """``@profile`` tags for a header-composed table, so the DDL records its version."""
        if not obj.header:
            return None
        data = read_yaml(self._root / "common-header" / f"{obj.header}.yaml") or {}
        version = data.get("current")
        tags = [f"@profile: {obj.header}"]
        if version:
            tags.append(f"@profile_version: {version}")
        return " | ".join(tags)

    def _render_table(self, obj: ManifestObject) -> RenderedObject:
        database = self._database(obj)
        path = resolve(self._required(obj, "defines"), root=self._root)
        entry = load_version_entry(path, require_columns=False)
        block = entry.get("table") or {}
        if "name" not in block:
            raise SchemaError(f"{obj.id}: {path} does not name its table")
        name = block["name"]
        columns = self._columns(obj, entry, path)
        config = self._with_default_ttl(_table_config(block))
        statement = self._create_table(database, name, columns, config, self._profile_comment(obj))
        return self._finish(obj, name, [statement], database=database)

    def _with_default_ttl(self, config: _TableConfig) -> _TableConfig:
        """Give a time-series table the deployment's retention when it declares none.

        A declared ``ttl_days`` always wins, and a table with no ``ttl_columns``
        cannot carry a TTL at all -- a default over none fails the CREATE.
        """
        if self._default_ttl_days is None or config.ttl_days is not None or not config.ttl_columns:
            return config
        return replace(config, ttl_days=self._default_ttl_days)

    def _create_table(
        self,
        database: str,
        name: str,
        columns: list[Column],
        config: _TableConfig,
        table_comment: str | None,
    ) -> str:
        clause, on_cluster = self._engine(config.engine)
        target = f"{quote_ident(database, what='database')}.{quote_ident(name, what='table name')}"
        lines = [f"CREATE TABLE IF NOT EXISTS {target}{on_cluster}\n("]
        lines.append(",\n".join(self._body(columns, config)))
        lines.append(f")\nENGINE = {clause}")

        partition = self._partition(columns, config)
        if partition:
            lines.append(f"PARTITION BY {partition}")

        # A raw ORDER BY sets no PRIMARY KEY of its own: ClickHouse then takes
        # the sorting key as the primary key, which is what such a table wants.
        if config.order_by:
            lines.append(f"ORDER BY ({config.order_by})")
        else:
            key = self._sorting_key(columns, name)
            if key:
                joined = ", ".join(key)
                lines.append(f"PRIMARY KEY ({joined})")
                lines.append(f"ORDER BY ({joined})")
            else:
                lines.append("ORDER BY tuple()")

        ttl = self._ttl(columns, config, name)
        if ttl:
            lines.append(ttl)

        settings = [f"index_granularity = {config.index_granularity}"]
        if ttl:
            # ttl_only_drop_parts waits for EVERY row in a part to expire, so a
            # table whose TTL is not aligned to its partition would silently
            # retain data forever; those need the row-level delete instead.
            aligned = bool(partition) and all(col in partition for col in config.ttl_columns)
            settings.append(f"ttl_only_drop_parts = {int(aligned)}")
        lines.append("SETTINGS\n    " + ",\n    ".join(settings))

        if table_comment:
            lines.append(f"COMMENT '{table_comment.replace(chr(39), chr(92) + chr(39))}'")

        return "\n".join(lines)

    def _body(self, columns: list[Column], config: _TableConfig) -> list[str]:
        body = [f"    {self._column_def(column)}" for column in columns]
        for column in columns:
            index = self._index_def(column)
            if index:
                body.append(f"    {index}")
        body.extend(f"    {index}" for index in config.extra_indexes)
        if config.projection_order_by and config.projection_order_by in {c.name for c in columns}:
            body.append(
                f"    PROJECTION {config.projection_order_by}_optimized "
                f"(SELECT * ORDER BY `{config.projection_order_by}`)"
            )
        return body

    def _column_def(self, column: Column) -> str:
        resolved = self._registry.resolve(column)
        parts = [quote_ident(column.name, what="column name"), resolved.ch_type]

        if column.default:
            if "materialized" in column.attribute:
                parts.append(f"MATERIALIZED {column.default}")
            elif "alias" in column.attribute:
                parts.append(f"ALIAS {column.default}")
            else:
                parts.append(f"DEFAULT {column.default}")

        # The expr comes first when both are present: the loader parses the
        # directive off the front of the comment.
        if column.expr and column.comment:
            comment = f"{column.expr} - {column.comment}"
        else:
            comment = column.expr or column.comment
        if comment:
            parts.append("COMMENT '" + comment.replace("'", "\\'") + "'")

        if resolved.codec:
            parts.append(f"CODEC({resolved.codec})")
        return " ".join(parts)

    def _index_def(self, column: Column) -> str | None:
        template = _INDEX_TEMPLATES.get(column.use_case or "")
        if template is None:
            return None
        return template.format(name=f"idx_{column.name}", col=f"`{column.name}`")

    def _partition(self, columns: list[Column], config: _TableConfig) -> str | None:
        """PARTITION BY: a raw expression wins over the column plus granularity."""
        if config.partition_by:
            return config.partition_by
        if not any(column.name == config.partition_column for column in columns):
            return None
        func = _PARTITION_FUNCS.get(config.partition_granularity)
        if func is None:
            raise SchemaError(
                f"unknown partition_granularity {config.partition_granularity!r}; "
                f"valid: {', '.join(sorted(_PARTITION_FUNCS))}"
            )
        return f"{func}({config.partition_column})"

    def _sorting_key(self, columns: list[Column], table: str) -> list[str]:
        """The ORDER BY built from the columns' own ``order`` positions.

        A Nullable key is refused rather than dropped: ClickHouse rejects one
        unless allow_nullable_key is on, and even then a null in the sort key
        cripples the index and doubles storage. Mark the column ``not_null`` or
        give it a DEFAULT.
        """
        positioned = [(column.order, column) for column in columns if column.order is not None]
        ordered = [column for _, column in sorted(positioned, key=lambda pair: pair[0])]
        key: list[str] = []
        for column in ordered:
            ch_type = self._registry.resolve(column).ch_type
            if "Nullable" in ch_type:
                raise SchemaError(
                    f"{table}.{column.name} resolves to {ch_type} and is an ORDER BY key; "
                    "add the 'not_null' attribute or a DEFAULT"
                )
            key.append(quote_ident(column.name, what="column name"))
        return key

    def _ttl(self, columns: list[Column], config: _TableConfig, table: str) -> str | None:
        if not config.ttl_days:
            return None
        if not config.ttl_columns:
            raise SchemaError(f"{table}: ttl_days={config.ttl_days} declared with no ttl_columns")
        present = {column.name for column in columns}
        missing = [col for col in config.ttl_columns if col not in present]
        if missing:
            raise SchemaError(
                f"{table}: ttl_days={config.ttl_days} declared over absent column(s) "
                f"{', '.join(missing)}; the table would keep every row forever"
            )
        parts = [
            f"{col} + INTERVAL {config.ttl_days} DAY DELETE WHERE {col} >= 0"
            for col in config.ttl_columns
        ]
        return "TTL " + ",\n    ".join(parts)

    # -- views ---------------------------------------------------------------

    def _render_materialized_view(self, obj: ManifestObject) -> RenderedObject:
        database = self._database(obj)
        path = resolve(self._required(obj, "defines"), root=self._root)
        view = load_version_entry(path, require_columns=False).get("materialized_view")
        if not view:
            raise SchemaError(f"{obj.id}: {path} declares no materialized_view")
        name = view["name"]
        select = view["select"].format(db=database)
        statement = (
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.{name}{self._on_cluster} "
            f"TO {database}.{view['to']} AS\n{select}"
        )
        return self._finish(obj, name, [statement], database=database)

    def _render_view(self, obj: ManifestObject) -> RenderedObject:
        """A plain view, plus the SELECT grant its reader role needs.

        The header line must be exactly ``CREATE OR REPLACE VIEW {db}.<name>``:
        that is where the ON CLUSTER suffix goes, and without it a view is
        created on the ONE node the connection landed on while the siblings
        behind a headless Service silently diverge.
        """
        database = self._database(obj)
        defines = self._required(obj, "defines")
        name = Path(defines).name
        path = resolve(defines, root=self._root, suffix=".sql")
        body = _strip_leading_comments(path.read_text(encoding="utf-8"))
        header = f"CREATE OR REPLACE VIEW {{db}}.{name}"
        if header not in body:
            raise SchemaError(f"{obj.id}: {path} does not open with {header!r}")
        statement = body.replace(
            header, f"CREATE OR REPLACE VIEW {database}.{name}{self._on_cluster}", 1
        ).replace("{db}", database)
        reader = self._view_reader_role()
        grant = f"GRANT SELECT ON {database}.{name} TO {reader}"
        return self._finish(obj, name, [statement.strip(), grant], database=database)

    def _view_reader_role(self) -> str:
        catalogue = load_version_entry(
            self._root / "roles" / "clickhouse.yaml", require_columns=False
        )
        return catalogue["view_reader_role"]

    # -- roles ---------------------------------------------------------------

    def _render_role(self, obj: ManifestObject) -> RenderedObject:
        database = self._database(obj)
        catalogue = load_version_entry(
            self._root / "roles" / "clickhouse.yaml", require_columns=False
        )
        section = self._required(obj, "section")
        if section == "tenant":
            name, statements = self._render_tenant(catalogue)
        else:
            entries = catalogue.get(section)
            if entries is None:
                raise SchemaError(f"{obj.id}: roles/clickhouse.yaml has no section {section!r}")
            key = self._required(obj, "key")
            entry = next((item for item in entries if item.get("name") == key), None)
            if entry is None:
                raise SchemaError(f"{obj.id}: no {section} entry named {key!r}")
            name, statements = self._render_role_entry(catalogue, entry, database)
        return self._finish(obj, name, statements, database=database)

    @staticmethod
    def _settings_kv(settings: dict[str, int]) -> str:
        """``k = v`` pairs, sorted, so a diff is stable."""
        return ", ".join(f"{key} = {value}" for key, value in sorted(settings.items()))

    def _render_role_entry(
        self, catalogue: dict[str, Any], entry: dict[str, Any], database: str
    ) -> tuple[str, list[str]]:
        """A tier or a service role: role, profile, quota, grants, then attach.

        The role comes FIRST because the quota is assigned TO it, and ClickHouse
        rejects a quota naming a role that does not exist yet -- which only
        shows up against a cluster where the role was not already present. Every
        statement converges: an IF NOT EXISTS creates, an ALTER re-asserts the
        mutable state, so an edit reaches an object that already exists.
        """
        naming = catalogue["naming"]
        name = entry["name"]
        role = f"`{naming['role'].format(name=name)}`"
        profile = f"`{naming['settings_profile'].format(name=name)}`"
        settings = entry.get("settings") or {}
        statements: list[str] = []

        if entry.get("quota"):
            statements.append(f"CREATE ROLE IF NOT EXISTS {role}")
        if settings:
            statements.append(f"CREATE SETTINGS PROFILE IF NOT EXISTS {profile}")
            statements.append(
                f"ALTER SETTINGS PROFILE {profile} SETTINGS {self._settings_kv(settings)}"
            )
        if not entry.get("quota"):
            statements.append(f"CREATE ROLE IF NOT EXISTS {role}")

        quota = entry.get("quota") or {}
        if quota:
            quota_name = f"`{naming['quota'].format(name=name)}`"
            maxima = self._settings_kv(
                {key: value for key, value in quota.items() if key != "interval"}
            )
            interval = quota.get("interval", "1 hour")
            statements.append(f"CREATE QUOTA IF NOT EXISTS {quota_name} TO {role}")
            statements.append(
                f"ALTER QUOTA {quota_name} FOR INTERVAL {interval} MAX {maxima} TO {role}"
            )

        grants = list(entry.get("grants") or [])
        if entry.get("system_introspection"):
            grants += [
                f"SELECT ON system.{table}" for table in catalogue["system_introspection_tables"]
            ]
        placeholder = catalogue["database_placeholder"]
        statements += [
            f"GRANT {grant.replace(placeholder, database)} TO {role}" for grant in grants
        ]

        if settings:
            statements.append(f"ALTER ROLE {role} SETTINGS PROFILE {profile}")
        return name, statements

    def _render_tenant(self, catalogue: dict[str, Any]) -> tuple[str, list[str]]:
        """The shared tenant role and the system grants a fenced org still needs.

        The RESTRICTIVE row policies are not rendered here: they are written per
        table against the ones that actually carry ``_org_id``, which is read
        off the live server. What is declared here is the predicate they use and
        the name they get, so the reconciler writes no policy of its own
        invention.
        """
        tenant = catalogue["tenant"]
        role = f"`{tenant['role']}`"
        statements = [f"CREATE ROLE IF NOT EXISTS {role}"]
        statements += [
            f"GRANT SELECT ON system.{table} TO {role}" for table in tenant["system_tables"]
        ]
        return tenant["role"], statements

    # -- topics --------------------------------------------------------------

    def _render_topic(self, obj: ManifestObject) -> RenderedObject:
        if self._topics is None:
            self._topics = load_topic_policy(root=self._root)
        spec = self._topics.spec(
            self._required(obj, "section"),
            self._required(obj, "key"),
            broker_count=self._broker_count,
            kafka_tiered_storage=self._kafka_tiered_storage,
        )
        return self._finish(
            obj,
            spec.name,
            [spec.describe()],
            database=None,
            topic={
                "name": spec.name,
                "partitions": spec.partitions,
                "replication_factor": spec.replication_factor,
                "config": spec.config,
                "kind": spec.kind,
            },
        )
