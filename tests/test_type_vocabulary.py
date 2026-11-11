#  Project:      dfe-schemas
#  File:         tests/test_type_vocabulary.py
#  Purpose:      types.yaml carries what a schema form shows, and every override option is valid
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Labels and override options in ``registries/types.yaml``.

dfe-engine serves them to the console, so an entry without a label would show
as its bare name, and an override option the validator refuses would let a user
pick a type the engine then rejects.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

import dfe_schemas
from dfe_schemas.loader import read_yaml

_TYPES: dict[str, Any] = read_yaml(dfe_schemas.schemas_root() / "registries" / "types.yaml")
_OVERRIDES = _TYPES["ch_overrides"]
_PATTERNS = [re.compile(f"^{pattern}$") for pattern in _OVERRIDES["parameterised"]]
_OPTIONS = _OVERRIDES["options"]


@pytest.mark.parametrize("section", ["primitives", "use_cases", "attributes"])
def test_every_entry_has_a_label(section: str):
    unlabelled = [name for name, entry in _TYPES[section].items() if not entry.get("label")]

    assert unlabelled == []


def test_every_use_case_names_the_question_it_answers():
    assert [
        name for name, entry in _TYPES["use_cases"].items() if not entry.get("description")
    ] == []


def _filled(option: dict[str, Any]) -> str:
    template = option.get("template") or option["value"]
    return template.format(**{param["name"]: 8 for param in option.get("parameters", [])})


@pytest.mark.parametrize("option", _OPTIONS, ids=lambda option: option["value"])
def test_an_override_option_is_a_type_the_catalogue_accepts(option: dict[str, Any]):
    filled = _filled(option)

    assert filled in _OVERRIDES["exact"] or any(p.match(filled) for p in _PATTERNS)


@pytest.mark.parametrize("option", _OPTIONS, ids=lambda option: option["value"])
def test_an_override_option_refines_a_known_primitive(option: dict[str, Any]):
    assert option["primitive"] in _TYPES["primitives"]
    assert bool(option.get("parameters")) == bool(option.get("template"))


def test_override_values_are_unique():
    values = [option["value"] for option in _OPTIONS]

    assert len(values) == len(set(values))


@pytest.mark.parametrize("option", _OPTIONS, ids=lambda option: option["value"])
def test_every_parameter_is_named_in_the_template_and_typed(option: dict[str, Any]):
    for param in option.get("parameters", []):
        assert param["label"]
        assert param["kind"] in {"integer", "text"}
        assert "{" + param["name"] + "}" in option["template"]
