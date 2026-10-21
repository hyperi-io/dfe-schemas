#  Project:      dfe-schemas
#  File:         tests/test_compat_profiles.py
#  Purpose:      Every compatibility profile is a valid document and compiles under scalo.rules
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A compatibility profile is data the engine compiles into first-hop rule rows.

Two checks own a profile between them. The JSON schema owns what a profile
document is: its version tree, the header fields it may set, and the rows it
leaves off until a source opts in. scalo.rules owns what a row means, so every
version is compiled by the rule engine the receiver and the fetcher run, and a
row that would not compile there never ships here.
"""

import copy

import pytest
from jsonschema import Draft202012Validator
from scalo.rules import Clamp, ClampOutcome, CompileOptions, RuleSet

from dfe_schemas.compat import load_compat_profile, profile_ids, profile_schema, rule_set
from dfe_schemas.loader import SchemaError

SHIPPED = ("dfe-legacy-2x", "elastic-agent")
# An explicit profile, so no compile reads the scalo config cascade.
OPTIONS = CompileOptions()


def _versions() -> list[tuple[str, str]]:
    return [
        (profile_id, version)
        for profile_id in SHIPPED
        for version in load_compat_profile(profile_id)["versions"]
    ]


def _offered(profile_id: str, version: str) -> tuple[str, ...]:
    entry = load_compat_profile(profile_id)["versions"][version]
    return tuple(entry.get("opt_in") or ())


def _compiled(profile_id: str, aliases: dict[str, str], opt_in: tuple[str, ...] = ()) -> RuleSet:
    """The profile as the engine compiles it, with its aliases table filled."""
    config = rule_set(profile_id, opt_in=opt_in)
    config["lookups"]["aliases"] = aliases
    return RuleSet.compile(config, OPTIONS)


@pytest.fixture(scope="module")
def validator():
    return Draft202012Validator(profile_schema())


def test_the_shipped_profiles_are_the_two_at_ga():
    assert profile_ids() == list(SHIPPED)


def test_the_profile_schema_is_a_valid_json_schema():
    Draft202012Validator.check_schema(profile_schema())


@pytest.mark.parametrize("profile_id", SHIPPED)
def test_every_profile_validates_against_the_schema(validator, profile_id):
    problems = [error.message for error in validator.iter_errors(load_compat_profile(profile_id))]
    assert problems == []


@pytest.mark.parametrize(("profile_id", "version"), _versions())
def test_every_version_compiles_clean_under_the_rule_engine(profile_id, version):
    """No error and no warning, with and without the rows a source opts in to."""
    for opt_in in ((), _offered(profile_id, version)):
        config = rule_set(profile_id, version=version, opt_in=opt_in)
        assert RuleSet.check(config, OPTIONS) == []
        RuleSet.compile(config, OPTIONS)


def _set_host(doc):
    doc["versions"]["1.0.0"]["rules"][0]["set"] = {"host": {"value": "x"}}


def _set_two_fields(doc):
    doc["versions"]["1.0.0"]["rules"][0]["set"] = {
        "_source": {"value": "x"},
        "_org_id": {"value": "y"},
    }


def _target_outside_the_header(doc):
    doc["versions"]["1.0.0"]["targets"].append("message")


def _target_the_receive_time(doc):
    """The first hop stamps the receive time itself, and the header reads a sender's own."""
    doc["versions"]["1.0.0"]["targets"] = ["_source", "_org_id", "_timestamp_received"]


def _no_rules(doc):
    doc["versions"]["1.0.0"]["rules"] = []


def _unknown_entry_key(doc):
    doc["versions"]["1.0.0"]["enabled"] = True


def _binding_separator_in_the_id(doc):
    doc["id"] = "dfe-legacy-2x@1"


def _match_beside_when(doc):
    doc["versions"]["1.0.0"]["rules"][1]["when"] = "true"


def _no_current(doc):
    del doc["current"]


@pytest.mark.parametrize(
    "mutate",
    [
        _set_host,
        _set_two_fields,
        _target_outside_the_header,
        _target_the_receive_time,
        _no_rules,
        _unknown_entry_key,
        _binding_separator_in_the_id,
        _match_beside_when,
        _no_current,
    ],
)
def test_the_schema_refuses_a_malformed_profile(validator, mutate):
    doc = copy.deepcopy(load_compat_profile("dfe-legacy-2x"))
    mutate(doc)
    assert not validator.is_valid(doc)


def test_a_row_left_off_by_default_is_compiled_only_when_opted_in():
    default = [row["id"] for row in rule_set("elastic-agent")["rules"]]
    opted = [row["id"] for row in rule_set("elastic-agent", opt_in=("org-namespace",))["rules"]]
    assert "org-namespace" not in default
    assert "org-namespace" in opted


def test_opting_in_to_a_row_the_profile_does_not_offer_is_refused():
    with pytest.raises(SchemaError, match="does not offer"):
        rule_set("dfe-legacy-2x", opt_in=("org-tags",))


@pytest.mark.parametrize("profile_id", ["nope", "../tables/core/main"])
def test_an_unknown_profile_is_refused(profile_id):
    with pytest.raises(SchemaError, match="no compatibility profile"):
        rule_set(profile_id)


def test_an_unknown_version_is_refused():
    with pytest.raises(SchemaError, match="not in"):
        rule_set("dfe-legacy-2x", version="9.9.9")


LEGACY_RECORD = {
    "org_id": "flat-org",
    "tags": {"event": {"org_id": "acme", "category": "logs_syslog"}},
    "timestamp_received": "2026-01-01T00:00:00.250Z",
    "message": "<34>Oct 11 22:14:15 host su: 'su root' failed",
}


def test_a_2x_record_takes_its_org_and_category():
    outcome = _compiled("dfe-legacy-2x", {"logs_syslog": "syslog"}).evaluate(LEGACY_RECORD)
    assert outcome.value("_source") == "syslog"
    assert outcome.value("_org_id") == "acme"


def test_a_2x_record_without_tags_takes_the_flat_org():
    outcome = _compiled("dfe-legacy-2x", {}).evaluate({"org_id": "flat-org"})
    assert outcome.value("_org_id") == "flat-org"


@pytest.mark.parametrize("placeholder", ["unknown", "dfesystem"])
def test_a_2x_placeholder_org_takes_the_default_and_is_not_counted(placeholder):
    """2.1 wrote these where it had no tenant, so they are no sender's claim on one."""
    rules = _compiled("dfe-legacy-2x", {})
    record = {"org_id": placeholder, "tags": {"event": {"org_id": placeholder}}}
    clamp = Clamp("_org_id", frozenset({"default", "acme"}), "default")
    outcome = rules.evaluate(record, clamp)
    assert outcome.value("_org_id") == "default"
    assert outcome.clamp is not None
    assert outcome.clamp.outcome is ClampOutcome.DEFAULTED
    assert sum(row.clamped for row in rules.usage()) == 0
    assert rules.record_clamped() == 0


def test_a_category_no_source_aliases_sets_no_source():
    """A lookup miss falls through, so the catch-all after the profile decides."""
    outcome = _compiled("dfe-legacy-2x", {"logs_beats_winlog": "windows"}).evaluate(LEGACY_RECORD)
    assert outcome.value("_source") is None


def test_the_clamp_bounds_a_2x_org_to_the_credential():
    clamp = Clamp("_org_id", frozenset({"default", "beta"}), "default")
    outcome = _compiled("dfe-legacy-2x", {}).evaluate(LEGACY_RECORD, clamp)
    assert outcome.value("_org_id") == "default"
    assert outcome.clamp is not None
    assert outcome.clamp.outcome is ClampOutcome.REPLACED
    assert outcome.clamp.rule == "org-tags"


def test_a_2x_record_with_no_org_takes_the_default():
    clamp = Clamp("_org_id", frozenset({"default"}), "default")
    outcome = _compiled("dfe-legacy-2x", {}).evaluate({"message": "x"}, clamp)
    assert outcome.value("_org_id") == "default"
    assert outcome.clamp is not None
    assert outcome.clamp.outcome is ClampOutcome.DEFAULTED


ELASTIC_RECORD = {
    "agent": {"type": "filebeat", "name": "edge-01", "version": "8.0.0"},
    "data_stream": {"dataset": "cisco_ios.log", "namespace": "ep", "type": "logs"},
    "input": {"type": "tcp"},
    "message": "%SYS-5-CONFIG_I: Configured from console by vty0",
}


def test_an_elastic_event_routes_on_its_dataset():
    outcome = _compiled("elastic-agent", {"cisco_ios.log": "cisco-ios"}).evaluate(ELASTIC_RECORD)
    assert outcome.value("_source") == "cisco-ios"


def test_the_namespace_is_not_the_tenant_unless_a_source_opts_in():
    default = _compiled("elastic-agent", {}).evaluate(ELASTIC_RECORD)
    opted = _compiled("elastic-agent", {}, opt_in=("org-namespace",)).evaluate(ELASTIC_RECORD)
    assert default.value("_org_id") is None
    assert opted.value("_org_id") == "ep"
