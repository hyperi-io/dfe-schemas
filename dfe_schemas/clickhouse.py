#  Project:      dfe-schemas
#  File:         dfe_schemas/clickhouse.py
#  Purpose:      ClickHouse table-engine selection by sensing the target server
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Pick the right ClickHouse table engine for the server you are talking to.

Moved here from scalo's ``ddl`` module: dfe-schemas is the home for the
generic, column-agnostic DDL mechanics, so every schema caller (the engine,
the ArgoCD Job, the docker one-shot) consumes ONE implementation.

The most capable engine differs per topology, and the DDL alone cannot tell
you which you are on:

===========================  ==========================================
keeperless single node       plain ``MergeTree`` (Replicated is REJECTED)
Replicated/Shared database   ``ReplicatedMergeTree``, no ``ON CLUSTER``
Atomic database + cluster    ``ReplicatedMergeTree ON CLUSTER``
ClickHouse Cloud             ``SharedMergeTree`` (the server substitutes)
===========================  ==========================================

Sending the wrong one either hard-fails with code 36 or silently splits data
across replicas. The replica path and name are never emitted: those come from
the server's ``default_replica_path`` / ``default_replica_name`` macros, which
are the operator's to set.

Resolution is a cascade, first match wins:

1. an explicit override, when a call site pins the topology itself
2. live sensing, when a client is supplied
3. a deployment setting, for a gitops path with no client to sense with
4. ``single``, safe everywhere -- Cloud still substitutes Shared

**Only sensing emits ``ON CLUSTER``.** A topology named in config says which
engine form to use and nothing about fanning DDL across nodes, so a hand-set
value cannot accidentally broadcast.

The variant and its parameters always survive:
``ReplacingMergeTree(ver)`` becomes ``ReplicatedReplacingMergeTree(ver)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger("dfe_schemas.clickhouse")

__all__ = [
    "EngineResolver",
    "EngineSpec",
    "ResolvedEngine",
    "Topology",
    "parse_engine",
]


@dataclass(frozen=True)
class EngineSpec:
    """The engine a call site asks for, before the topology renders it.

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
    engine flows through one resolver::

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


class Topology(StrEnum):
    """The deployment shape that decides the engine form."""

    SINGLE = "single"
    """Standalone or keeperless: plain ``<variant>()``."""

    REPLICATED = "replicated"
    """A Replicated/Shared database or Cloud: ``Replicated<variant>``, no ``ON CLUSTER``."""

    REPLICATED_ON_CLUSTER = "replicated_on_cluster"
    """An Atomic database on a real cluster: ``Replicated<variant> ON CLUSTER``."""


@dataclass(frozen=True)
class ResolvedEngine:
    """What to splice into a ``CREATE TABLE``.

    Attributes:
        clause: the full token after ``ENGINE =``.
        on_cluster: ``" ON CLUSTER <name>"`` to follow the table name, or empty.
        topology: ``single`` or ``replicated``, for a generator that wants the
            coarse form rather than the enum.
        origin: which cascade layer decided, for logs.
    """

    clause: str
    on_cluster: str
    topology: str
    origin: str


def _engine_clause(spec: EngineSpec, topology: Topology) -> str:
    """Render the ``ENGINE`` clause for a variant under a topology.

    The replicated forms are argumentless unless the variant itself takes
    parameters, because the server supplies the path and replica name.
    """
    if topology is Topology.SINGLE:
        return f"{spec.variant}({spec.params})"
    if spec.params:
        return f"Replicated{spec.variant}({spec.params})"
    return f"Replicated{spec.variant}"


class EngineResolver:
    """Resolves a table's engine down the cascade, caching what it senses.

    Construct one per apply pass with whatever the caller has: a live client
    enables sensing, and ``override`` / ``topology_setting`` feed the other
    layers::

        resolver = EngineResolver(client=ch, topology_setting=cfg.topology)
        resolved = resolver.resolve(parse_engine("ReplacingMergeTree(ver)"), "analytics")
        sql = f"CREATE TABLE analytics.events{resolved.on_cluster} (...) ENGINE = {resolved.clause}"
    """

    def __init__(
        self,
        client=None,
        *,
        override: str | None = None,
        topology_setting: str | None = None,
    ) -> None:
        """
        Args:
            client: a live clickhouse-connect client exposing ``query``, or None.
                Supplying one enables the sensing layer.
            override: an explicit topology pin, ``single`` or ``replicated``.
                Highest priority, and never emits ``ON CLUSTER``.
            topology_setting: the deployment's configured topology, used when
                there is no client to sense with.
        """
        self._client = client
        self._override = override
        self._topology_setting = topology_setting
        self._sensed: dict[str, Topology] = {}

    def resolve(self, spec: EngineSpec, database: str) -> ResolvedEngine:
        """Resolve ``spec`` against ``database``."""
        topology, origin = self._cascade(database)
        on_cluster = ""
        if topology is Topology.REPLICATED_ON_CLUSTER:
            cluster = self._cluster_name(database)
            on_cluster = f" ON CLUSTER {cluster}" if cluster else ""
        clause = _engine_clause(spec, topology)
        logger.debug(
            f"engine resolved: db={database} variant={spec.variant} "
            f"-> {clause}{on_cluster} (topology={topology.value}, via {origin})"
        )
        return ResolvedEngine(
            clause=clause,
            on_cluster=on_cluster,
            topology="single" if topology is Topology.SINGLE else "replicated",
            origin=origin,
        )

    def _cascade(self, database: str) -> tuple[Topology, str]:
        if self._override:
            return self._topology_from_name(self._override), "config-override"
        if self._client is not None:
            sensed = self._sense(database)
            if sensed is not None:
                return sensed, "sensed"
        if self._topology_setting:
            return self._topology_from_name(self._topology_setting), "topology-setting"
        return Topology.SINGLE, "default"

    @staticmethod
    def _topology_from_name(name: str) -> Topology:
        # A named topology carries no ON CLUSTER intent; only sensing an Atomic
        # cluster does.
        return Topology.SINGLE if name.strip().lower() == "single" else Topology.REPLICATED

    def _sense(self, database: str) -> Topology | None:
        """Classify the live server, or return None so the cascade continues."""
        if database in self._sensed:
            return self._sensed[database]
        try:
            # Cloud substitutes SharedMergeTree for any MergeTree-family CREATE.
            if self._scalar("SELECT value FROM system.settings WHERE name = 'cloud_mode'") == "1":
                return self._cache(database, Topology.REPLICATED)

            # A Replicated/Shared database propagates DDL and replicates data on
            # its own, so the argumentless form is right and ON CLUSTER is not.
            db_engine = self._scalar(
                f"SELECT engine FROM system.databases WHERE name = '{database}'"
            )
            if db_engine in ("Replicated", "Shared"):
                return self._cache(database, Topology.REPLICATED)

            # On a plain Atomic database, replication needs the server to be
            # cluster-configured, and then the DDL has to fan out explicitly.
            macros = {row[0] for row in self._rows("SELECT macro FROM system.macros")}
            if {"shard", "replica"} <= macros:
                return self._cache(database, Topology.REPLICATED_ON_CLUSTER)

            return self._cache(database, Topology.SINGLE)
        except Exception as exc:
            logger.warning(
                f"engine sensing failed for db={database}: {exc}; falling through cascade"
            )
            return None

    def _cluster_name(self, database: str) -> str | None:
        """The cluster to fan DDL over: the macro-declared one, else the first
        multi-host cluster the server reports."""
        try:
            macro_cluster = self._scalar(
                "SELECT substitution FROM system.macros WHERE macro = 'cluster'"
            )
            if macro_cluster:
                return macro_cluster
            rows = self._rows(
                "SELECT cluster FROM system.clusters GROUP BY cluster HAVING count() > 1 ORDER BY cluster LIMIT 1"
            )
            return rows[0][0] if rows else None
        except Exception as exc:
            logger.warning(f"cluster-name sensing failed for db={database}: {exc}")
            return None

    def _cache(self, database: str, topology: Topology) -> Topology:
        self._sensed[database] = topology
        return topology

    def _rows(self, sql: str) -> list:
        return self._client.query(sql).result_rows

    def _scalar(self, sql: str) -> str | None:
        rows = self._rows(sql)
        if not rows:
            return None
        value = rows[0][0]
        return str(value) if value is not None else None
