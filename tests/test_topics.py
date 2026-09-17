#  Project:      dfe-schemas
#  File:         tests/test_topics.py
#  Purpose:      The topic naming rule and the bootstrap set are one definition
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""scalo-rs derives the same names on the consumer side.

Two implementations of the naming rule is two answers about which topic a
message is on, and the failure shows up as a loader that reads an empty topic
while the receiver writes a full one.
"""

from __future__ import annotations

import pytest

import dfe_schemas
from dfe_schemas.loader import SchemaError
from dfe_schemas.topics import load_topic_policy

ROOT = dfe_schemas.schemas_root()


@pytest.fixture(scope="module")
def policy():
    return load_topic_policy(root=ROOT)


def test_a_source_lands_on_its_own_topic(policy):
    assert policy.landing_topic("okta") == "okta_land"


def test_a_transformed_source_writes_a_second_topic(policy):
    assert policy.transformed_topic("okta") == "okta_load"


def test_the_default_landing_label_matches_the_landing_table(policy):
    """The topic derives from the table name, so they cannot be two names."""
    assert policy.default_landing_label == "main"
    assert policy.landing_topic(policy.default_landing_label) == "main_land"


def test_the_bootstrap_set_is_the_landing_topic_plus_five_dlqs(policy):
    specs = policy.bootstrap_specs(broker_count=3)
    assert [spec.name for spec in specs] == [
        "main_land",
        "dfe_receiver_dlq",
        "dfe_loader_dlq",
        "dfe_archiver_dlq",
        "dfe_fetcher_dlq",
        "dfe_transform_dlq",
    ]


def test_a_dlq_gets_one_partition(policy):
    """Poison messages are read by a human, not by a consumer group."""
    dlq = policy.spec("dlq", "dfe_loader_dlq", broker_count=3)
    assert dlq.partitions == 1


def test_the_engine_may_create_and_alter_but_never_delete(policy):
    """A compromised engine principal must not be able to drop the bus."""
    assert policy.permissions == {"create": True, "alter": True, "delete": False}


def test_an_unknown_topic_is_an_error(policy):
    with pytest.raises(SchemaError, match="no topic"):
        policy.spec("dlq", "dfe_nope_dlq")


def test_the_describe_form_is_deterministic(policy):
    landing = policy.spec("landing", "main_land", broker_count=3)
    assert landing.describe() == (
        "kafka-topic main_land partitions=12 replication_factor=3 "
        "max.message.bytes=16777216 retention.ms=86400000"
    )


def test_the_landing_topic_tiers_where_the_deployment_does(policy):
    """Without the per-topic key the broker holds the plugin and moves nothing."""
    landing = policy.spec("landing", "main_land", broker_count=3, kafka_tiered_storage=True)
    assert landing.config["remote.storage.enable"] == "true"


def test_no_tiering_key_is_rendered_where_the_deployment_does_not_tier(policy):
    """Absent, not "false" -- a false would fight a broker that does tier."""
    landing = policy.spec("landing", "main_land", broker_count=3, kafka_tiered_storage=False)
    assert "remote.storage.enable" not in landing.config


def test_a_dlq_is_never_tiered(policy):
    """A DLQ is small and read by a human, so tiering costs a remote fetch."""
    dlq = policy.spec("dlq", "dfe_loader_dlq", broker_count=3, kafka_tiered_storage=True)
    assert "remote.storage.enable" not in dlq.config


def test_tiering_rides_the_landing_topic_and_not_every_topic(policy):
    tiered = {
        spec.name
        for spec in policy.bootstrap_specs(broker_count=3, kafka_tiered_storage=True)
        if "remote.storage.enable" in spec.config
    }
    assert tiered == {"main_land"}
