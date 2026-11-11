#  Project:      dfe-schemas
#  File:         dfe_schemas/render.py
#  Purpose:      Render every manifest object for a ClickHouse platform, checksummed
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Turn the manifest into statements, for the ClickHouse platform the caller names.

Every table renders one way: the argumentless ``Replicated<variant>`` engine and
no ``ON CLUSTER``, inside a database that replicates its own DDL. Only the
CREATE DATABASE and a JSON table's platform-only settings differ between Cloud
and self-hosted, and :mod:`dfe_schemas.clickhouse` supplies both, so the
platform is a PARAMETER here and never a literal in a definition.

Every rendered object carries a CHECKSUM. It is taken over the statement with
the platform token removed -- the database's ``ON CLUSTER`` and ``ENGINE``, a
table's ``Replicated`` prefix or empty argument list, and the platform-only
settings -- and whitespace collapsed, so one schema reads as one checksum on
either platform. That is what lets the applier's ledger answer "has this object
changed" rather than "was it applied somewhere else".
"""

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dfe_schemas.clickhouse import (
    Platform,
    create_database,
    engine_clause,
    parse_engine,
    platform_only_setting_names,
)
from dfe_schemas.ddl_catalogue import DDLCatalogue
from dfe_schemas.deploy_defaults import json_table_settings
from dfe_schemas.loader import (
    Column,
    ResolvedType,
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
from dfe_schemas.usage import SQL_VALUES

__all__ = [
    "RenderedObject",
    "Renderer",
    "TableDDL",
    "TableSpec",
    "checksum",
    "ddl_violations",
    "one_ddl_violations",
    "qualified_name",
    "referenced_objects",
    "render_table",
    "split_use_case",
]


def _index_name(column: str, suffix: str | None = None) -> str:
    """Quote an index name.

    A bare name on a column carrying a hyphen or a slash --
    `fortinet_firewall_app-type` -- parsed as `idx_fortinet_firewall_app` and
    then choked on the rest.
    """
    stem = f"idx_{column}" if suffix is None else f"idx_{column}_{suffix}"
    return f"`{stem}`"


_USE_CASE_RE = re.compile(r"^(?P<name>[a-z_]+)(?:\((?P<arg>\d+)\))?$")


def split_use_case(declared: str | None) -> tuple[str | None, int | None]:
    """Split a declared use case into its name and its optional integer argument.

    ``similarity_search(768)`` is the only shape that carries one, because
    ClickHouse cannot infer a vector's dimension count from the column.
    """
    if not declared:
        return None, None
    match = _USE_CASE_RE.match(declared.strip())
    if match is None:
        raise SchemaError(
            f"use case {declared!r} is not a name, optionally with an integer "
            f"argument -- for example 'word_search' or 'similarity_search(768)'"
        )
    arg = match.group("arg")
    return match.group("name"), int(arg) if arg else None


_ON_CLUSTER_RE = re.compile(r"\s+ON CLUSTER\s+\S+")
# A database's engine: the Replicated database a self-hosted server gets.
_DATABASE_ENGINE_RE = re.compile(r"\s+ENGINE\s*=\s*Replicated\([^)]*\)")
# A table's engine with the Replicated prefix and any empty argument list out,
# so the checksum names the variant alone.
_ENGINE_RE = re.compile(r"ENGINE = (?:Replicated)?([A-Za-z]*MergeTree)(?:\(\))?")


def _platform_settings_re() -> re.Pattern[str]:
    names = "|".join(re.escape(name) for name in sorted(platform_only_setting_names()))
    return re.compile(rf",\s*(?:{names})\s*=\s*[^,\n]+")


_QUOTED_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'")
# The spans whose whitespace is content: a string literal and a quoted name. A
# comment is matched first so an apostrophe inside one opens no literal.
_VERBATIM_RE = re.compile(
    r"--[^\n]*|/\*.*?\*/"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.|"")*"'
    r"|`(?:[^`\\]|\\.|``)*`",
    re.DOTALL,
)
_WHITESPACE_RE = re.compile(r"\s+")
_ON_CLUSTER_WORDS_RE = re.compile(r"\bON\s+CLUSTER\b", re.IGNORECASE)
_ENGINE_NAME_RE = re.compile(r"\bENGINE\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
# Two literal leading arguments are a Keeper path and a replica name, which a
# Replicated database refuses (code 36, or 80 with no macros). Read after literals are blanked.
_EXPLICIT_KEEPER_PATH_RE = re.compile(
    r"\bENGINE\s*=\s*(Replicated[A-Za-z]*MergeTree)\s*\(\s*''\s*,\s*''", re.IGNORECASE
)
_CREATE_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?P<kind>DATABASE|TABLE|MATERIALIZED\s+VIEW)\b",
    re.IGNORECASE,
)

# Engines that store no rows, so no replica can hold data another lacks. The
# Replicated database still replicates their DDL.
_DATALESS_ENGINES = frozenset({"Merge"})

# A refresh schedule a refreshable materialised view may take. Nothing else is
# accepted: `SETTINGS all_replicas = 1` would run the refresh on every replica.
_REFRESH_RE = re.compile(
    r"(?:EVERY|AFTER) [1-9]\d* (?:SECOND|MINUTE|HOUR|DAY|WEEK|MONTH|YEAR)"
    r"(?: OFFSET \d+ (?:SECOND|MINUTE|HOUR|DAY|WEEK|MONTH))?"
)

# A `${name}` token in a view's SQL, filled from dfe_schemas.usage.SQL_VALUES.
_PLACEHOLDER_RE = re.compile(r"\$\{(?P<name>[a-z][a-z0-9_]*)\}")

_DATE_TYPES = frozenset({"Date", "Date32"})


def ddl_violations(statement: str) -> list[str]:
    """Where one statement breaks the one-DDL shape, or an empty list.

    A table, or a materialised view's inner table, must take a ``Replicated*``
    engine with no Keeper path or replica name of its own, unless the engine
    stores no rows (``Merge``). Nothing but a CREATE DATABASE for a Replicated
    database may carry ``ON CLUSTER``. Comment lines and quoted literals are read
    past, so a column comment naming an engine is not mistaken for one.
    """
    lines = [line for line in statement.splitlines() if not line.lstrip().startswith("--")]
    text = _QUOTED_LITERAL_RE.sub("''", "\n".join(lines))
    head = text.strip().splitlines()[0] if text.strip() else ""
    create = _CREATE_RE.match(text)
    kind = " ".join(create["kind"].upper().split()) if create else ""
    engine = _ENGINE_NAME_RE.search(text)
    problems: list[str] = []

    if _ON_CLUSTER_WORDS_RE.search(text):
        replicated_database = (
            kind == "DATABASE" and engine is not None and engine[1] == "Replicated"
        )
        if not replicated_database:
            problems.append(f"carries ON CLUSTER: {head}")
    if kind == "TABLE":
        if engine is None:
            problems.append(f"declares no ENGINE: {head}")
        elif not engine[1].startswith("Replicated") and engine[1] not in _DATALESS_ENGINES:
            problems.append(f"engine {engine[1]} is not Replicated*: {head}")
    if (
        kind == "MATERIALIZED VIEW"
        and engine is not None
        and not engine[1].startswith("Replicated")
    ):
        problems.append(f"inner engine {engine[1]} is not Replicated*: {head}")
    explicit = _EXPLICIT_KEEPER_PATH_RE.search(text)
    if explicit is not None:
        problems.append(f"engine {explicit[1]} names its own Keeper path and replica: {head}")
    return problems


def _string_literal(text: str) -> str:
    """Single-quote *text* for ClickHouse: backslashes doubled before quotes are escaped.

    ClickHouse reads C-style escapes in a literal, so a lone backslash rewrites
    the comment the loader reads its directive from, and a trailing one ends
    the statement in a syntax error.
    """
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def one_ddl_violations(rendered: RenderedObject) -> list[str]:
    """Every :func:`ddl_violations` finding across one rendered object's statements."""
    return [problem for statement in rendered.statements for problem in ddl_violations(statement)]


def checksum(statements: tuple[str, ...] | list[str]) -> str:
    """sha256 over the statements with the platform token out and space collapsed.

    Whitespace inside a string literal or a quoted name is kept, because it
    changes what the statement does.
    """
    platform_settings = _platform_settings_re()
    normalised = []
    for statement in statements:
        text = _ON_CLUSTER_RE.sub("", statement)
        if text.lstrip().upper().startswith("CREATE DATABASE"):
            text = _DATABASE_ENGINE_RE.sub("", text)
        text = _ENGINE_RE.sub(r"ENGINE = \1", text)
        text = platform_settings.sub("", text)
        normalised.append(_collapse_whitespace(text))
    return hashlib.sha256("\n".join(normalised).encode("utf-8")).hexdigest()


def _collapse_whitespace(text: str) -> str:
    """Collapse each whitespace run to one space, except inside a literal or a quoted name."""
    pieces: list[str] = []
    end = 0
    for span in _VERBATIM_RE.finditer(text):
        verbatim = span.group()
        is_comment = verbatim.startswith(("--", "/*"))
        pieces.append(_WHITESPACE_RE.sub(" ", text[end : span.start()]))
        pieces.append(_WHITESPACE_RE.sub(" ", verbatim) if is_comment else verbatim)
        end = span.end()
    pieces.append(_WHITESPACE_RE.sub(" ", text[end:]))
    return "".join(pieces).strip()


@dataclass(frozen=True)
class RenderedObject:
    """One manifest object, rendered for one ClickHouse platform."""

    id: str
    kind: str
    database: str | None
    name: str
    platform: str
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


def _fill_placeholders(body: str, source: Path) -> str:
    """Fill each ``${name}`` in a view's SQL from :data:`dfe_schemas.usage.SQL_VALUES`.

    An unknown name raises, and so does a ``${`` left over that names nothing,
    rather than rendering the token into SQL the server then refuses or, inside
    a literal, matches nothing.
    """

    def value(match: re.Match[str]) -> str:
        name = match["name"]
        if name not in SQL_VALUES:
            raise SchemaError(f"{source}: no value for ${{{name}}}, known: {', '.join(SQL_VALUES)}")
        return SQL_VALUES[name]

    filled = _PLACEHOLDER_RE.sub(value, body)
    if "${" in filled:
        raise SchemaError(f"{source}: a '${{' token is not a ${{name}} placeholder")
    return filled


def qualified_name(rendered: RenderedObject) -> str:
    """What a rendered object is known by: database-qualified, or bare for a topic."""
    if rendered.database is None:
        return f"topic:{rendered.name}"
    return f"{rendered.database}.{rendered.name}"


def referenced_objects(rendered: RenderedObject) -> set[str]:
    """Every DFE object a rendered statement names, other than itself.

    Only ``<database>.<object>`` counts. A bare name resolves against whichever
    database the connection is on and a quoted one is a string literal, so
    neither is a reference anything can check from the text. A grant's
    table-prefix wildcard (``dfe.otel_*``) names no one object either.
    """
    if rendered.database is None:
        return set()
    pattern = re.compile(
        rf"(?<!')\b{re.escape(rendered.database)}\.`?([A-Za-z_][A-Za-z0-9_]*)`?(?![A-Za-z0-9_*])"
    )
    found = {
        f"{rendered.database}.{name}"
        for statement in rendered.statements
        for name in pattern.findall(statement)
    }
    return found - {qualified_name(rendered)}


@dataclass(frozen=True)
class TableSpec:
    """One table to render: its columns and every clause it declares.

    A clause left as None takes the DDL catalogue's default, so nothing is
    defaulted in code. TTL and the projection are off unless declared.

    Attributes:
        database: The database the table lands in, or the ``{db}`` placeholder.
        name: The table name.
        columns: The columns, in order.
        engine: The MergeTree-family variant, with its arguments if it takes any.
        ttl_days: Retention in days. None or 0 renders no TTL.
        ttl_columns: The columns the TTL reads.
        ttl_only_drop_parts: Emit the setting on a table with a TTL.
        partition_by: A raw PARTITION BY, which wins over the partition column.
        partition_column: The column PARTITION BY wraps, else the catalogue's.
        partition_granularity: The key into the catalogue's partition functions.
        order_by: A raw ORDER BY, which wins over the columns' ``order``.
        primary_key: A raw PRIMARY KEY, used only with *order_by*.
        sample_by: A SAMPLE BY expression.
        extra_indexes: Raw INDEX definitions, after the column indexes.
        index_granularity: The table's index_granularity, else the catalogue's.
        projection_order_by: A column for a ``SELECT * ORDER BY`` projection.
        comment: The table COMMENT.
    """

    database: str
    name: str
    columns: tuple[Column, ...]
    engine: str = "MergeTree"
    ttl_days: int | None = None
    ttl_columns: tuple[str, ...] = ()
    ttl_only_drop_parts: bool = True
    partition_by: str | None = None
    partition_column: str | None = None
    partition_granularity: str | None = None
    order_by: str | None = None
    primary_key: str | None = None
    sample_by: str | None = None
    extra_indexes: tuple[str, ...] = ()
    index_granularity: int | None = None
    projection_order_by: str | None = None
    comment: str | None = None


def _table_spec(
    block: dict[str, Any], *, database: str, columns: list[Column], comment: str | None
) -> TableSpec:
    """Read a definition's ``table`` block into a spec.

    TTL and the projection default to OFF rather than to the landing table's
    defaults: an engine-state table has none of the columns those clauses name,
    and a default nobody can see is how a table ends up keeping everything.
    """
    granularity = block.get("index_granularity")
    return TableSpec(
        database=database,
        name=block["name"],
        columns=tuple(columns),
        engine=block.get("engine", "MergeTree"),
        ttl_days=block.get("ttl_days"),
        ttl_columns=tuple(block.get("ttl_columns") or ()),
        projection_order_by=block.get("projection_order_by"),
        partition_by=block.get("partition_by"),
        partition_column=block.get("partition_column"),
        partition_granularity=block.get("partition_granularity"),
        order_by=block.get("order_by"),
        extra_indexes=tuple(block.get("indexes") or ()),
        index_granularity=None if granularity is None else int(granularity),
        comment=comment,
    )


class TableDDL:
    """Renders tables, columns and indexes from the type registry and the DDL catalogue.

    The manifest :class:`Renderer` and dfe-engine's source tables both render
    through it.
    """

    def __init__(
        self,
        *,
        registry: TypeRegistry,
        catalogue: DDLCatalogue,
        platform: Platform = Platform.SELF_HOSTED,
    ) -> None:
        """Bind the registry, the catalogue and the platform a JSON table's settings follow."""
        self._registry = registry
        self._ddl = catalogue
        self._platform = platform

    @classmethod
    def load(
        cls, *, platform: Platform = Platform.SELF_HOSTED, root: Path | None = None
    ) -> TableDDL:
        """Read the registry and the catalogue under *root*, or the ones the package ships."""
        return cls(
            registry=TypeRegistry.load(root=root),
            catalogue=DDLCatalogue.load(root=root),
            platform=platform,
        )

    @property
    def catalogue(self) -> DDLCatalogue:
        """The DDL catalogue the tables render from."""
        return self._ddl

    def resolve(self, column: Column) -> ResolvedType:
        """The full ClickHouse type and codec for one column."""
        return self._registry.resolve(column)

    def create_table(self, spec: TableSpec) -> str:
        """The CREATE TABLE IF NOT EXISTS for *spec*, with no trailing semicolon.

        Raises:
            SchemaError: A clause or column cannot render.
        """
        if parse_engine(spec.engine).variant in _DATALESS_ENGINES:
            return self._create_dataless_table(spec)
        columns = list(spec.columns)
        lines = [f"CREATE TABLE IF NOT EXISTS {_target(spec.database, spec.name)}\n("]
        lines.append(",\n".join(self._body(spec)))
        lines.append(f")\nENGINE = {engine_clause(parse_engine(spec.engine))}")

        partition = self.partition(spec)
        if partition:
            lines.append(f"PARTITION BY {partition}")

        # A raw ORDER BY sets no PRIMARY KEY of its own: ClickHouse then takes
        # the sorting key as the primary key, which is what such a table wants.
        if spec.order_by:
            if spec.primary_key:
                lines.append(f"PRIMARY KEY ({spec.primary_key})")
            lines.append(f"ORDER BY ({spec.order_by})")
        else:
            key = self._sorting_key(columns, spec.name)
            if key:
                joined = ", ".join(key)
                lines.append(f"PRIMARY KEY ({joined})")
                lines.append(f"ORDER BY ({joined})")
            else:
                lines.append("ORDER BY tuple()")

        if spec.sample_by:
            lines.append(f"SAMPLE BY {spec.sample_by}")

        ttl = self.ttl_clause(spec)
        if ttl:
            lines.append(ttl)

        granularity = spec.index_granularity or self._ddl.table.index_granularity
        settings = [f"index_granularity = {granularity}"]
        if ttl and spec.ttl_only_drop_parts:
            # ttl_only_drop_parts waits for EVERY row in a part to expire, so a
            # table whose TTL is not aligned to its partition would keep data
            # forever. Those take the row-level delete instead.
            aligned = bool(partition) and all(col in partition for col in spec.ttl_columns)
            settings.append(f"ttl_only_drop_parts = {int(aligned)}")
        settings.extend(f"{key} = {value}" for key, value in self.json_settings(columns).items())
        lines.append("SETTINGS\n    " + ",\n    ".join(settings))

        if spec.comment:
            lines.append(f"COMMENT {_string_literal(spec.comment)}")

        return "\n".join(lines)

    def json_settings(self, columns: list[Column]) -> dict[str, int]:
        """The table SETTINGS a JSON column needs on this platform, empty without one."""
        column_types = [self._registry.resolve(column).ch_type for column in columns]
        return json_table_settings(column_types, platform=self._platform)

    def _create_dataless_table(self, spec: TableSpec) -> str:
        """A table whose engine stores no rows: columns and the engine, nothing else.

        ``{db}`` in the engine arguments is the deployment's data database, so a
        Merge table reads the database it lands in. A clause only a MergeTree
        takes is refused rather than dropped.
        """
        declared = {
            "ttl_days": spec.ttl_days,
            "ttl_columns": spec.ttl_columns,
            "partition_by": spec.partition_by,
            "order_by": spec.order_by,
            "indexes": spec.extra_indexes,
            "projection_order_by": spec.projection_order_by,
        }
        stray = sorted(key for key, value in declared.items() if value)
        if stray:
            raise SchemaError(
                f"{spec.name}: engine {spec.engine!r} stores no rows and takes none of {stray}"
            )
        engine = spec.engine.replace("{db}", quote_ident(spec.database, what="database"))
        body = ",\n".join(self._body(spec))
        target = _target(spec.database, spec.name)
        return f"CREATE TABLE IF NOT EXISTS {target}\n(\n{body}\n)\nENGINE = {engine}"

    def _body(self, spec: TableSpec) -> list[str]:
        body = [f"    {self.column_def(column)}" for column in spec.columns]
        for column in spec.columns:
            body.extend(f"    {index}" for index in self.index_defs(column))
        body.extend(f"    {index}" for index in spec.extra_indexes)
        names = {c.name for c in spec.columns}
        if spec.projection_order_by and spec.projection_order_by in names:
            body.append(
                f"    PROJECTION {spec.projection_order_by}_optimized "
                f"(SELECT * ORDER BY `{spec.projection_order_by}`)"
            )
        return body

    def column_def(self, column: Column) -> str:
        """One column's definition: name, type, default, comment and codec."""
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
            parts.append(f"COMMENT {_string_literal(comment)}")

        if resolved.codec:
            parts.append(f"CODEC({resolved.codec})")
        return " ".join(parts)

    def index_defs(self, column: Column) -> list[str]:
        """The INDEX lines a column asks for, from the catalogue.

        A column carrying its own ``index`` TYPE renders that verbatim. An
        unknown use case is refused rather than rendered with no index, so a
        retired or misspelled name never reads as a column that wants none.
        """
        if column.index:
            return [f"INDEX {_index_name(column.name)} `{column.name}` TYPE {column.index}"]
        use_case, dims = split_use_case(column.use_case)
        if use_case is None:
            return []
        try:
            return self._ddl.index_defs(
                column=column.name,
                use_case=use_case,
                dims=dims,
                cardinality=column.cardinality,
                name=lambda suffix: _index_name(column.name, suffix),
            )
        except SchemaError as exc:
            raise SchemaError(f"column {column.name!r}: {exc}") from exc

    def partition(self, spec: TableSpec) -> str | None:
        """PARTITION BY: a raw expression wins over the column plus granularity."""
        if spec.partition_by:
            return spec.partition_by
        column = spec.partition_column or self._ddl.table.partition_column
        if not any(c.name == column for c in spec.columns):
            return None
        granularity = spec.partition_granularity or self._ddl.table.partition_granularity
        return self._ddl.table.partition_expr(column, granularity)

    def nullable_key_columns(self, columns: list[Column]) -> list[str]:
        """The columns given an ORDER BY position that resolve to a Nullable type."""
        return [
            column.name
            for column in columns
            if column.order is not None and "Nullable" in self._registry.resolve(column).ch_type
        ]

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

    def ttl_clause(self, spec: TableSpec) -> str | None:
        """The TTL clause, or None when *spec* declares no retention.

        Raises:
            SchemaError: Retention is declared over no column, or over a column
                the table lacks, which would keep every row forever.
        """
        if not spec.ttl_days:
            return None
        if not spec.ttl_columns:
            raise SchemaError(f"{spec.name}: ttl_days={spec.ttl_days} declared with no ttl_columns")
        types = {column.name: self._registry.resolve(column).ch_type for column in spec.columns}
        missing = [col for col in spec.ttl_columns if col not in types]
        if missing:
            raise SchemaError(
                f"{spec.name}: ttl_days={spec.ttl_days} declared over absent column(s) "
                f"{', '.join(missing)}; the table would keep every row forever"
            )
        parts = [_ttl_expression(col, types[col], spec.ttl_days) for col in spec.ttl_columns]
        return "TTL " + ",\n    ".join(parts)


def _ttl_expression(column: str, ch_type: str, days: int) -> str:
    """One column's TTL. A Date takes no ``>= 0`` guard: 26.3 refuses Date >= UInt8 (code 43)."""
    if ch_type in _DATE_TYPES:
        return f"{column} + INTERVAL {days} DAY DELETE"
    return f"{column} + INTERVAL {days} DAY DELETE WHERE {column} >= 0"


def _target(database: str, name: str) -> str:
    return f"{quote_ident(database, what='database')}.{quote_ident(name, what='table name')}"


def render_table(
    spec: TableSpec, *, platform: Platform = Platform.SELF_HOSTED, root: Path | None = None
) -> str:
    """Render one table's CREATE TABLE IF NOT EXISTS, without a trailing semicolon.

    For a table that is not a manifest object, such as a dfe-engine source
    table. It renders through the same :class:`TableDDL` the manifest uses.

    Raises:
        SchemaError: A clause or column cannot render.
    """
    return TableDDL.load(platform=platform, root=root).create_table(spec)


class Renderer:
    """Renders manifest objects for one ClickHouse platform.

    Construct one per platform, which :class:`~dfe_schemas.clickhouse.PlatformProbe`
    names for a live connection.
    """

    def __init__(
        self,
        manifest: Manifest,
        *,
        platform: Platform = Platform.SELF_HOSTED,
        data_database: str = "dfe",
        default_ttl_days: int | None = 90,
        broker_count: int = 1,
        kafka_tiered_storage: bool = False,
    ) -> None:
        """Bind the manifest to one deployment's settings.

        Args:
            manifest: the checked manifest whose objects this renders.
            platform: the ClickHouse platform, which picks the CREATE DATABASE
                and a JSON table's platform-only settings.
            data_database: the name a parameter-bound database key resolves to.
            default_ttl_days: retention for a time-series table that declares
                none. None leaves such a table without a TTL.
            broker_count: the Kafka broker count topic replication is clamped to.
            kafka_tiered_storage: whether the deployment's brokers tier, so
                topics in a tiered section render the tiering keys.
        """
        self._manifest = manifest
        self._platform = platform
        self._data_database = data_database
        self._default_ttl_days = default_ttl_days
        self._broker_count = broker_count
        self._kafka_tiered_storage = kafka_tiered_storage
        self._root = manifest.root
        self.tables = TableDDL(
            registry=TypeRegistry.load(root=self._root),
            catalogue=DDLCatalogue.load(root=self._root),
            platform=platform,
        )
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
            platform=self._platform.value,
            additive=obj.additive,
            optional=obj.optional,
            statements=frozen,
            checksum=checksum(frozen),
            topic=topic,
        )

    # -- databases -----------------------------------------------------------

    def _render_database(self, obj: ManifestObject) -> RenderedObject:
        """A database that replicates every table DDL issued inside it."""
        database = self._database(obj)
        statement = create_database(database, self._platform)
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
                load_columns(extra_path, root=self._root),
                exclude=load_profile_exclude(extra_path),
            )
        if obj.compose:
            return load_columns(resolve(obj.compose, root=self._root), root=self._root)
        if "columns" not in entry:
            raise SchemaError(
                f"{obj.id}: {path} declares no columns and the manifest names no header or compose"
            )
        return load_columns(path, root=self._root)

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
        spec = _table_spec(
            block,
            database=database,
            columns=self._columns(obj, entry, path),
            comment=self._profile_comment(obj),
        )
        if parse_engine(spec.engine).variant not in _DATALESS_ENGINES:
            spec = self._with_default_ttl(spec)
        statements = [self.tables.create_table(spec)]
        # As a view does. ClickHouse folds it into the role's SELECT ON <db>.* where that exists.
        if block.get("reader_grant", False):
            statements.append(
                f"GRANT SELECT ON {database}.{spec.name} TO {self._view_reader_role()}"
            )
        return self._finish(obj, spec.name, statements, database=database)

    def _with_default_ttl(self, spec: TableSpec) -> TableSpec:
        """Give a time-series table the deployment's retention when it declares none.

        A declared ``ttl_days`` always wins, and a table with no ``ttl_columns``
        cannot carry a TTL at all -- a default over none fails the CREATE.
        """
        if self._default_ttl_days is None or spec.ttl_days is not None or not spec.ttl_columns:
            return spec
        return replace(spec, ttl_days=self._default_ttl_days)

    # -- views ---------------------------------------------------------------

    def _render_materialized_view(self, obj: ManifestObject) -> RenderedObject:
        """An incremental view, or a refreshable one when it declares ``refresh``.

        A refreshable view always APPENDs. Without it ClickHouse swaps the target
        for each refresh's result, which would drop every row older than what the
        source still holds. Inside the Replicated database Keeper hands each
        scheduled refresh to one replica.
        """
        database = self._database(obj)
        path = resolve(self._required(obj, "defines"), root=self._root)
        view = self._materialized_view_entry(obj, load_version_entry(path, require_columns=False))
        name = view["name"]
        select = view["select"].format(db=database)
        refresh = view.get("refresh")
        if refresh is None:
            schedule = ""
        elif _REFRESH_RE.fullmatch(str(refresh)):
            schedule = f"REFRESH {refresh} APPEND "
        else:
            raise SchemaError(
                f"{obj.id}: refresh {refresh!r} is not EVERY|AFTER <n> <unit> [OFFSET <n> <unit>]"
            )
        statement = (
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.{name} "
            f"{schedule}TO {database}.{view['to']} AS\n{select}"
        )
        return self._finish(obj, name, [statement], database=database)

    @staticmethod
    def _materialized_view_entry(obj: ManifestObject, entry: dict[str, Any]) -> dict[str, Any]:
        """The view a manifest object names: by ``key`` from ``materialized_views``, else the one."""
        if obj.key is None:
            view = entry.get("materialized_view")
            if not view:
                raise SchemaError(f"{obj.id}: {obj.defines} declares no materialized_view")
            return view
        for view in entry.get("materialized_views") or ():
            if view.get("name") == obj.key:
                return view
        raise SchemaError(f"{obj.id}: {obj.defines} has no materialized_views entry {obj.key!r}")

    def _render_view(self, obj: ManifestObject) -> RenderedObject:
        """A plain view, plus the SELECT grant its reader role needs.

        The header line must be exactly ``CREATE OR REPLACE VIEW {db}.<name>``,
        so the rendered name is the one the manifest declares.
        """
        database = self._database(obj)
        defines = self._required(obj, "defines")
        name = Path(defines).name
        path = resolve(defines, root=self._root, suffix=".sql")
        body = _fill_placeholders(_strip_leading_comments(path.read_text(encoding="utf-8")), path)
        header = f"CREATE OR REPLACE VIEW {{db}}.{name}"
        if header not in body:
            raise SchemaError(f"{obj.id}: {path} does not open with {header!r}")
        statement = body.replace(header, f"CREATE OR REPLACE VIEW {database}.{name}", 1).replace(
            "{db}", database
        )
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

    @staticmethod
    def profile_settings(settings: dict[str, Any]) -> str:
        """A SETTINGS PROFILE's settings list, sorted, each with its constraint.

        A setting is a plain value, or a mapping of ``value`` plus ``min`` and
        ``max``, or ``value`` plus ``const: true``. The engine's reconciler
        renders profiles through this too, so both write the same text.

        Raises:
            SchemaError: A mapping has an unknown key, a non-numeric bound, a
                bound beside ``const``, or a value outside its bounds.
        """
        return ", ".join(
            f"{key} = {Renderer._profile_setting(key, setting)}"
            for key, setting in sorted(settings.items())
        )

    @staticmethod
    def _profile_setting(name: str, setting: Any) -> str:
        """One setting's ``value [MIN a] [MAX b] [CONST]`` text."""
        if not isinstance(setting, dict):
            return str(setting)
        unknown = set(setting) - {"value", "min", "max", "const"}
        if unknown:
            raise SchemaError(f"setting {name}: unknown keys {sorted(unknown)}")
        bounds = {key: setting[key] for key in ("value", "min", "max") if key in setting}
        if "value" not in bounds:
            raise SchemaError(f"setting {name}: a constrained setting needs a value")
        for key, number in bounds.items():
            if isinstance(number, bool) or not isinstance(number, int | float):
                raise SchemaError(f"setting {name}: {key} must be a number, not {number!r}")
        const = setting.get("const", False)
        if not isinstance(const, bool):
            raise SchemaError(f"setting {name}: const must be true or false")
        if const and ("min" in bounds or "max" in bounds):
            raise SchemaError(f"setting {name}: a const setting takes no min or max")
        value = bounds["value"]
        low, high = bounds.get("min", value), bounds.get("max", value)
        if not low <= value <= high:
            raise SchemaError(f"setting {name}: value {value} is outside min {low}, max {high}")
        parts = [str(value)]
        parts += [f"{key.upper()} {bounds[key]}" for key in ("min", "max") if key in bounds]
        if const:
            parts.append("CONST")
        return " ".join(parts)

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
                f"ALTER SETTINGS PROFILE {profile} SETTINGS {self.profile_settings(settings)}"
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
        statements.append(
            self._replace_grants([grant.replace(placeholder, database) for grant in grants], role)
        )

        if settings:
            statements.append(f"ALTER ROLE {role} SETTINGS PROFILE {profile}")
        return name, statements

    @staticmethod
    def _replace_grants(grants: list[str], role: str) -> str:
        """One statement leaving *role* holding *grants* and nothing else.

        ``WITH REPLACE OPTION`` revokes whatever privilege the list does not name
        in the same statement, so a grant dropped from the catalogue leaves the
        role and the role never holds less than the list. Roles granted to the
        role are not privileges and stay.
        """
        if not grants:
            return f"REVOKE ALL ON *.* FROM {role}"
        return f"GRANT {', '.join(grants)} TO {role} WITH REPLACE OPTION"

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
        grants = [f"SELECT ON system.{table}" for table in tenant["system_tables"]]
        statements = [f"CREATE ROLE IF NOT EXISTS {role}", self._replace_grants(grants, role)]
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
