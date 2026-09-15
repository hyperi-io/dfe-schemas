#  Project:      dfe-schemas
#  File:         tests/test_registries.py
#  Purpose:      The registries tree resolves from the package root
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

# Entry contents are checked by scripts/validate_schemas.py, which loads them through dfe-engine.

from __future__ import annotations

import pytest

import dfe_schemas

_REGISTRIES = dfe_schemas.schemas_root() / "registries"


@pytest.mark.parametrize(
    "relative",
    [
        "engines.yaml",
        "types.yaml",
        "field-maps/cim/_default.yaml",
        "field-maps/ecs/_default.yaml",
        "field-maps/ocsf/_default.yaml",
        "field-maps/sigma/_default.yaml",
    ],
)
def test_registry_file_ships(relative: str):
    assert (_REGISTRIES / relative).is_file()
