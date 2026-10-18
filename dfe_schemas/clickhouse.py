#  Project:      dfe-schemas
#  File:         dfe_schemas/clickhouse.py
#  Purpose:      ClickHouse platform probe and the one DDL shape DFE renders
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Which ClickHouse platform a connection reaches, and the one DDL shape for both.

Every DFE table is created the same way everywhere: the argumentless
``Replicated<variant>`` engine, no ``ON CLUSTER``, inside a database that
replicates its own DDL. ClickHouse Cloud rewrites that engine to
``SharedMergeTree`` on CREATE. Self-hosted ClickHouse needs Keeper and the
``{cluster}``, ``{shard}`` and ``{replica}`` macros for it, and a server without
them is refused rather than handed a second engine form.

What does differ by platform -- the CREATE DATABASE, and the settings a JSON
table carries only on self-hosted -- is SQL in ``sql/platform.sql``, one variant
per platform. ClickHouse cannot branch its own DDL, so one probe per connection
names the platform and the variant follows.

The platform resolves down a cascade, first match wins:

1. an explicit override, for a test or a call site that pins it
2. the live probe: ``SharedMergeTree`` in ``system.table_engines`` is Cloud.
   ``cloud_mode`` is not the signal, because it can be set on any server
3. the hostname, when there is no client: ``*.clickhouse.cloud`` or
   ``*.clickhouse-byoc.com`` is Cloud
4. self-hosted
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from dfe_schemas.loader import SchemaError
from dfe_schemas.variants import VariantBlocks, load_variant_blocks

logger = logging.getLogger("dfe_schemas.clickhouse")

__all__ = [
    "CLOUD_HOST_SUFFIXES",
    "PLATFORM_DOCS",
    "EngineSpec",
    "KeeperMissingError",
    "Platform",
    "PlatformProbe",
    "QueryClient",
    "SettingsRealign",
    "create_database",
    "engine_clause",
    "parse_engine",
    "platform_from_host",
    "platform_json_settings",
    "platform_only_setting_names",
    "realign_settings",
]

CLOUD_HOST_SUFFIXES = (".clickhouse.cloud", ".clickhouse-byoc.com")

PLATFORM_DOCS = "https://github.com/hyperi-io/dfe-schemas/blob/main/docs/clickhouse-platform.md"

# Keeper's own connection table: present only when the server is configured for Keeper.
_KEEPER_TABLE = "zookeeper_connection"
# The macros the Replicated database statement names.
_REQUIRED_MACROS = ("cluster", "shard", "replica")
_BARE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOPOLOGY_PREFIXES = ("Replicated", "Shared")


class Platform(StrEnum):
    """The ClickHouse platform a connection reaches."""

    CLOUD = "cloud"
    SELF_HOSTED = "self_hosted"


class KeeperMissingError(Exception):
    """A self-hosted server lacks the Keeper or the macros every DFE table needs."""


@dataclass(frozen=True)
class EngineSpec:
    """The engine a call site asks for: a MergeTree-family variant and its arguments.

    Attributes:
        variant: the MergeTree-family variant, e.g. ``ReplacingMergeTree``.
        params: the arguments inside the engine parentheses, e.g. a version
            column. Empty when the variant takes none.
    """

    variant: str = "MergeTree"
    params: str = ""


def parse_engine(engine: str) -> EngineSpec:
    """Split an engine string into its variant and parameters.

    Accepts the bare variant and the parameterised form, so any MergeTree-family
    engine renders through one function::

        parse_engine("MergeTree")                         # MergeTree, ""
        parse_engine("ReplacingMergeTree(updated_at)")    # ReplacingMergeTree, "updated_at"
        parse_engine("SummingMergeTree(a, b)")            # SummingMergeTree, "a, b"

    A trailing empty ``()`` counts as no parameters, and whitespace is ignored.
    """
    text = engine.strip()
    if "(" not in text:
        return EngineSpec(variant=text, params="")
    variant, _, rest = text.partition("(")
    return EngineSpec(variant=variant.strip(), params=rest.rsplit(")", 1)[0].strip())


def engine_clause(spec: EngineSpec) -> str:
    """The token after ``ENGINE =``, the same on every platform.

    Argumentless, because the Replicated database supplies the replica path and
    name. A variant's own parameters, such as a version column, are kept.

    Raises:
        ValueError: *spec* already carries a ``Replicated`` or ``Shared`` prefix,
            which the platform adds and a definition never declares.
    """
    if spec.variant.startswith(_TOPOLOGY_PREFIXES):
        raise ValueError(
            f"engine {spec.variant!r}: declare the MergeTree-family variant alone, "
            "because the Replicated form is added at render time"
        )
    if spec.params:
        return f"Replicated{spec.variant}({spec.params})"
    return f"Replicated{spec.variant}"


def platform_from_host(host: str) -> Platform:
    """The platform a hostname names: Cloud for a Cloud or BYOC endpoint, else self-hosted."""
    name = host.strip().rstrip(".").lower()
    return Platform.CLOUD if name.endswith(CLOUD_HOST_SUFFIXES) else Platform.SELF_HOSTED


@cache
def _platform_blocks() -> VariantBlocks:
    """The packaged platform SQL, read once per process."""
    source = resources.files("dfe_schemas") / "sql" / "platform.sql"
    with resources.as_file(source) as path:
        return load_variant_blocks(Path(path))


def create_database(database: str, platform: Platform) -> str:
    """The CREATE DATABASE for *database* on *platform*.

    Raises:
        SchemaError: *database* is not a bare name. It is spliced into the Keeper
            path as well as the statement, where a quote or a slash would change
            what the statement means.
    """
    if not _BARE_NAME.fullmatch(database):
        raise SchemaError(f"database name {database!r} is not a bare identifier")
    return _platform_blocks().select("create_database", platform.value, database=database)


def _settings_block(platform: Platform) -> dict[str, int]:
    body = _platform_blocks().select("json_table_settings", platform.value)
    settings: dict[str, int] = {}
    for line in body.splitlines():
        name, eq, value = line.partition("=")
        if not eq or not name.strip() or not value.strip().isdigit():
            raise SchemaError(
                f"json_table_settings {platform.value}: {line.strip()!r} is not 'name = <integer>'"
            )
        settings[name.strip()] = int(value.strip())
    return settings


def platform_json_settings(platform: Platform) -> dict[str, int]:
    """The settings a JSON table carries on *platform* alone, as a fresh copy."""
    return _settings_block(platform)


def platform_only_setting_names() -> frozenset[str]:
    """Every setting some platform gives a JSON table and another does not."""
    names: set[str] = set()
    for platform in Platform:
        names.update(_settings_block(platform))
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class SettingsRealign:
    """What brings a live table's settings to the ones it should carry.

    Attributes:
        modify: ``(name, value)`` for each setting to set, sorted by name.
        reset: Each platform-only setting to return to the server default, sorted.
    """

    modify: tuple[tuple[str, str], ...] = ()
    reset: tuple[str, ...] = ()

    def statements(self, target: str) -> tuple[str, ...]:
        """The ALTER TABLE statements for *target* (a quoted ``database.table``)."""
        out: list[str] = []
        if self.modify:
            assignments = ", ".join(f"{name} = {value}" for name, value in self.modify)
            out.append(f"ALTER TABLE {target} MODIFY SETTING {assignments}")
        if self.reset:
            out.append(f"ALTER TABLE {target} RESET SETTING {', '.join(self.reset)}")
        return tuple(out)

    def describe(self) -> tuple[str, ...]:
        """``name = value`` for each setting set, then ``reset name`` for each reset."""
        return tuple(f"{name} = {value}" for name, value in self.modify) + tuple(
            f"reset {name}" for name in self.reset
        )


def realign_settings(wanted: Mapping[str, object], live: Mapping[str, str]) -> SettingsRealign:
    """Bring *live* to *wanted*, and reset a platform-only setting *wanted* omits.

    Only names in *wanted* are set, and one already at its value is left. A live
    setting *wanted* omits is reset only when some platform sets it, so moving a
    deployment between Cloud and self-hosted converges either way, and a setting
    an operator added by hand stays.

    Args:
        wanted: Setting name to the value the table should carry on this platform.
        live: The table's settings now, by name, as the server prints them.
    """
    modify = tuple(
        (name, str(value)) for name, value in sorted(wanted.items()) if live.get(name) != str(value)
    )
    reset = tuple(
        sorted(
            name for name in platform_only_setting_names() if name in live and name not in wanted
        )
    )
    return SettingsRealign(modify=modify, reset=reset)


class QueryClient(Protocol):
    """The one call the probe makes. A clickhouse-connect ``Client`` satisfies it."""

    def query(self, query: str, /, parameters: dict[str, str] | None = None) -> Any:
        """Run one SELECT, binding ``parameters`` server-side, and return its result."""


class PlatformProbe:
    """Names the platform behind one connection, probing it at most once.

    Construct one per connection with whatever the caller has::

        probe = PlatformProbe(client, host=settings.host)
        probe.require_replication()          # self-hosted without Keeper refuses here
        sql = create_database("dfe", probe.platform)
    """

    def __init__(
        self,
        client: QueryClient | None = None,
        *,
        host: str | None = None,
        override: Platform | str | None = None,
    ) -> None:
        """Take whichever cascade inputs the caller has.

        Args:
            client: a live client, or None. Supplying one enables the probe.
            host: the server's hostname, read when there is no client or the
                probe fails.
            override: a platform pinned by the caller, ``cloud`` or ``self_hosted``.

        Raises:
            ValueError: *override* names no platform.
        """
        self._client = client
        self._host = host
        self._override = Platform(override) if override is not None else None
        self._resolved: tuple[Platform, str] | None = None

    @property
    def platform(self) -> Platform:
        """The platform, resolved once and then cached."""
        return self._resolve()[0]

    @property  # noqa: V106 - public API
    def origin(self) -> str:
        """Which cascade layer decided: override, probe, hostname or default."""
        return self._resolve()[1]

    def _resolve(self) -> tuple[Platform, str]:
        if self._resolved is None:
            self._resolved = self._cascade()
            logger.debug(f"clickhouse platform: {self._resolved[0]} (via {self._resolved[1]})")
        return self._resolved

    def _cascade(self) -> tuple[Platform, str]:
        if self._override is not None:
            return self._override, "override"
        if self._client is not None:
            try:
                count = self._scalar(
                    "SELECT count() FROM system.table_engines WHERE name = 'SharedMergeTree'"
                )
                return (Platform.CLOUD if count > 0 else Platform.SELF_HOSTED), "probe"
            except Exception as exc:
                logger.warning(f"clickhouse platform probe failed: {exc}; using the hostname")
        if self._host:
            return platform_from_host(self._host), "hostname"
        return Platform.SELF_HOSTED, "default"

    def require_replication(self) -> None:  # noqa: V105 - public API
        """Refuse a self-hosted server that cannot hold a Replicated database.

        Cloud supplies its own coordination and passes without a query.

        Raises:
            KeeperMissingError: Keeper is not configured, or a macro the database
                statement names is not defined.
            RuntimeError: There is no client to ask.
        """
        if self.platform is Platform.CLOUD:
            return
        if self._client is None:
            raise RuntimeError("checking for Keeper needs a client")
        missing: list[str] = []
        keeper = self._scalar(
            "SELECT count() FROM system.tables WHERE database = 'system' AND name = {name:String}",
            {"name": _KEEPER_TABLE},
        )
        if keeper == 0:
            missing.append("Keeper (no Keeper or ZooKeeper connection is configured)")
        defined = {str(row[0]) for row in self._rows("SELECT macro FROM system.macros")}
        missing.extend(
            "the {" + macro + "} macro" for macro in _REQUIRED_MACROS if macro not in defined
        )
        if missing:
            # Names no host: the engine serves this text on its status route.
            raise KeeperMissingError(
                f"self-hosted ClickHouse is missing {', '.join(missing)}. DFE creates "
                "every table as an argumentless ReplicatedMergeTree inside a Replicated "
                "database, which needs Keeper and the {cluster}, {shard} and {replica} "
                f"macros. A single node runs Keeper embedded. See {PLATFORM_DOCS}"
            )

    def _rows(self, sql: str, parameters: dict[str, str] | None = None) -> list:
        if self._client is None:
            raise RuntimeError("the platform probe needs a client")
        return list(self._client.query(sql, parameters=parameters).result_rows)

    def _scalar(self, sql: str, parameters: dict[str, str] | None = None) -> int:
        rows = self._rows(sql, parameters)
        return int(rows[0][0]) if rows and rows[0][0] is not None else 0
