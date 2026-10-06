#  Project:      dfe-schemas
#  File:         tests/test_field_maps.py
#  Purpose:      Every field-map target is a column the schema it maps onto defines
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A field map names real columns, or its view does not exist.

dfe-engine renders a map as ``<target> AS <standard field>`` in one ``CREATE VIEW``,
and ClickHouse refuses the whole view when a single target is not a column
(code 47, UNKNOWN_IDENTIFIER). A map that also points at the wrong real column
creates fine and silently matches nothing, which is why the Sigma map has to name
``process_executable`` for ``Image`` rather than the basename in ``process_name``.
"""

from pathlib import Path

import pytest

import dfe_schemas
from dfe_schemas.loader import compose, load_columns, load_profile, read_yaml

ROOT = dfe_schemas.schemas_root()
FIELD_MAPS = ROOT / "registries" / "field-maps"

# Every shipped map targets the ECS physical vocabulary under the default header.
HEADER_PROFILE = "timeseries"
VOCABULARY = {
    "cim": "meta/elastic/ecs",
    "ecs": "meta/elastic/ecs",
    "ocsf": "meta/elastic/ecs",
    "sigma": "meta/elastic/ecs",
}


def _columns(ref: str) -> frozenset[str]:
    header = load_profile(HEADER_PROFILE, root=ROOT)
    meta = load_columns(ROOT / f"{ref}.yaml")
    return frozenset(column.name for column in compose(header, meta))


def _field_maps() -> list[Path]:
    return sorted(FIELD_MAPS.glob("*/*.yaml"))


def test_every_standard_with_a_field_map_declares_its_vocabulary():
    standards = {path.parent.name for path in _field_maps()}
    assert standards, f"no field maps found under {FIELD_MAPS}"
    assert standards <= VOCABULARY.keys(), (
        f"no vocabulary declared for {sorted(standards - VOCABULARY.keys())}"
    )


@pytest.mark.parametrize("path", _field_maps(), ids=lambda path: f"{path.parent.name}/{path.name}")
def test_every_target_is_a_column_of_the_schema_it_maps_onto(path: Path):
    doc = read_yaml(path) or {}
    mappings = doc.get("mappings") or {}
    assert mappings, f"{path.relative_to(ROOT)} maps nothing"

    ref = VOCABULARY[path.parent.name]
    columns = _columns(ref)
    missing = {field: target for field, target in mappings.items() if target not in columns}
    assert not missing, (
        f"{path.relative_to(ROOT)} targets columns {HEADER_PROFILE} + {ref} does not "
        f"define: {missing}"
    )


@pytest.mark.parametrize(
    ("field", "column"),
    [
        # Sigma compares these against a full path, which a basename never equals.
        ("Image", "process_executable"),
        ("ParentImage", "process_parent_executable"),
        ("TargetFilename", "file_path"),
        ("ImageLoaded", "file_path"),
        # The Windows event number is event.code; event.id is a per-event identifier.
        ("EventID", "event_code"),
        # TargetObject carries the hive; registry.key drops it.
        ("TargetObject", "registry_path"),
    ],
)
def test_a_sigma_field_lands_on_the_column_pysigma_names_not_a_neighbour(field, column):
    """A neighbouring column exists, so the column check above cannot catch these."""
    doc = read_yaml(FIELD_MAPS / "sigma" / "_default.yaml")
    assert doc["mappings"][field] == column
