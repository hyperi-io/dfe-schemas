#  Project:      dfe-schemas
#  File:         dfe_schemas/topics.py
#  Purpose:      The Kafka topic naming rule and the bootstrap topic set
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Topic names and the bootstrap set, from ``topics/kafka.yaml``.

The naming rule is shared with scalo-rs, which derives the same names on the
consumer side: a source's records arrive on ``<source>_land``, and a source
with a transform has the transform write ``<source>_load`` for the loader to
read. Two implementations of that rule is two answers about which topic a
message is on.

Replication factor is CLAMPED to the broker count here rather than at each call
site: asking for more replicas than there are brokers leaves a topic un-Ready
forever, and the single-broker tiers are the common case in dev.

Tiered storage is opted into per TOPIC as well as per broker, so a deployment
whose broker tiers renders ``remote.storage.enable`` on the landing topic. With
the broker-wide switch on and the topic key absent the broker moves nothing and
reports nothing, and the first symptom is a full volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dfe_schemas import schemas_root
from dfe_schemas.loader import SchemaError, load_version_entry

__all__ = [
    "TopicPolicy",
    "TopicSpec",
    "load_topic_policy",
]

TOPICS_REF = "topics/kafka"
_MS_PER_HOUR = 3_600_000


@dataclass(frozen=True)
class TopicSpec:
    """One topic as the admin client wants it."""

    name: str
    partitions: int
    replication_factor: int
    config: dict[str, str]
    kind: str

    def describe(self) -> str:
        """The canonical one-line rendering, which is what gets checksummed.

        Not SQL and not Kafka wire syntax -- a topic has neither. It is a
        deterministic text form of the same fields the admin client is handed,
        so a topic's definition can be checksummed and recorded beside every
        other object.
        """
        settings = " ".join(f"{key}={value}" for key, value in sorted(self.config.items()))
        return (
            f"kafka-topic {self.name} partitions={self.partitions} "
            f"replication_factor={self.replication_factor} {settings}".rstrip()
        )


@dataclass(frozen=True)
class TopicPolicy:
    """The declared naming rule, defaults and bootstrap set."""

    land_suffix: str
    load_suffix: str
    default_landing_label: str
    defaults: dict[str, Any]
    tiered: dict[str, Any]
    bootstrap: dict[str, list[dict[str, Any]]]
    derivation: dict[str, Any]
    permissions: dict[str, bool]

    def landing_topic(self, source: str) -> str:
        """The topic a source's records arrive on, before any transform."""
        return f"{source}{self.land_suffix}"

    def transformed_topic(self, source: str) -> str:
        """The topic a source's transform writes, and the loader then reads."""
        return f"{source}{self.load_suffix}"

    def spec(
        self,
        section: str,
        key: str,
        *,
        broker_count: int = 1,
        kafka_tiered_storage: bool = False,
    ) -> TopicSpec:
        """One bootstrap topic, by the section and name the manifest gives it."""
        entries = self.bootstrap.get(section)
        if entries is None:
            raise SchemaError(f"topics/kafka.yaml declares no bootstrap section {section!r}")
        for entry in entries:
            if entry.get("name") == key:
                return self._spec(
                    entry,
                    section,
                    broker_count=broker_count,
                    kafka_tiered_storage=kafka_tiered_storage,
                )
        known = ", ".join(str(entry.get("name")) for entry in entries)
        raise SchemaError(f"no topic {key!r} in bootstrap.{section}; declared: {known}")

    def bootstrap_specs(
        self, *, broker_count: int = 1, kafka_tiered_storage: bool = False
    ) -> list[TopicSpec]:
        """Every bootstrap topic, landing first then the dead-letter set."""
        specs: list[TopicSpec] = []
        for section in ("landing", "dlq"):
            for entry in self.bootstrap.get(section) or ():
                specs.append(
                    self._spec(
                        entry,
                        section,
                        broker_count=broker_count,
                        kafka_tiered_storage=kafka_tiered_storage,
                    )
                )
        return specs

    def _tiered_config(self, section: str, *, kafka_tiered_storage: bool) -> dict[str, str]:
        """The tiering keys this section carries, or nothing.

        Nothing is emitted where the deployment does not tier: an explicit
        "false" would override a broker-level setting on a cluster that does.
        """
        if not kafka_tiered_storage:
            return {}
        if section not in (self.tiered.get("sections") or ()):
            return {}
        return {str(k): str(v) for k, v in (self.tiered.get("config") or {}).items()}

    def _spec(
        self,
        entry: dict[str, Any],
        section: str,
        *,
        broker_count: int,
        kafka_tiered_storage: bool = False,
    ) -> TopicSpec:
        defaults = self.defaults.get(section) or {}
        replication = min(int(defaults["replication_factor"]), max(broker_count, 1))
        config = {
            "max.message.bytes": str(int(self.defaults["max_message_bytes"])),
            "retention.ms": str(int(defaults["retention_hours"]) * _MS_PER_HOUR),
        }
        config.update(self._tiered_config(section, kafka_tiered_storage=kafka_tiered_storage))
        return TopicSpec(
            name=str(entry["name"]),
            partitions=int(entry.get("partitions", defaults["partitions"])),
            replication_factor=replication,
            config=config,
            kind=str(entry.get("kind", section)),
        )


def load_topic_policy(*, root: Path | None = None) -> TopicPolicy:
    """Read the declared topic policy."""
    base = root or schemas_root()
    entry = load_version_entry(base / f"{TOPICS_REF}.yaml", require_columns=False)
    naming = entry.get("naming") or {}
    return TopicPolicy(
        land_suffix=naming["land_suffix"],
        load_suffix=naming["load_suffix"],
        default_landing_label=naming["default_landing_label"],
        defaults=entry.get("defaults") or {},
        tiered=entry.get("tiered") or {},
        bootstrap=entry.get("bootstrap") or {},
        derivation=entry.get("derivation") or {},
        permissions=entry.get("permissions") or {},
    )
