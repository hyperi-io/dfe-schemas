#  Project:      dfe-schemas
#  File:         tests/test_common_header.py
#  Purpose:      The header names the wire fields each row's tenant, tags and receive time come from
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The ``_org_id`` directive is the tenancy switch.

dfe-loader reads a column's ``@source`` directive back off its ClickHouse COMMENT,
and the directive beats the loader's own ``org_id_field``. So the field the header
names here is the field every row's tenant is taken from: name one the first hop
does not stamp and every org's rows land in the default tenant. These tests read
the rendered DDL, because the COMMENT is the only thing the loader sees.
"""

import dataclasses

import pytest

import dfe_schemas
from dfe_schemas.loader import read_yaml
from dfe_schemas.manifest import load_manifest
from dfe_schemas.render import Renderer

ROOT = dfe_schemas.schemas_root()
PROFILES = ("timeseries", "minimal", "passthrough")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(root=ROOT)


def _column_lines(manifest, header: str) -> dict[str, str]:
    """Each column's DDL line in the landing table rendered on *header*, by column name."""
    landing = next(obj for obj in manifest.objects if obj.id == "data.main")
    rendered = Renderer(manifest).render(dataclasses.replace(landing, header=header))
    lines: dict[str, str] = {}
    for line in rendered.statements[0].splitlines():
        stripped = line.strip()
        if stripped.startswith("`"):
            lines[stripped.split("`")[1]] = stripped
    return lines


@pytest.mark.parametrize("header", PROFILES)
def test_the_tenant_is_read_from_the_stamped_org_id(manifest, header):
    """The first hop stamps ``_org_id``. A sender's bare ``org_id`` is not the tenant."""
    line = _column_lines(manifest, header)["_org_id"]
    assert "COMMENT '@source: _org_id - " in line, line


def test_tags_take_the_prefixed_field_first(manifest):
    line = _column_lines(manifest, "timeseries")["_tags"]
    assert "COMMENT '@source: first(_tags/tags/meta/metadata.tags) - " in line, line


def test_the_received_time_drops_the_retired_receiver_stamp(manifest):
    """``_timestamp_received`` is the one name the receiver and the fetcher both stamp."""
    line = _column_lines(manifest, "timeseries")["_timestamp_received"]
    assert "COMMENT '@source: first(_timestamp_received/" in line, line
    assert "_timestamp_receiver" not in line, line


def test_every_profile_is_current_at_one_version():
    """dfe-engine applies one deployment-wide header version to whichever profile a source names."""
    current = {
        header: read_yaml(ROOT / "common-header" / f"{header}.yaml")["current"]
        for header in PROFILES
    }
    assert len(set(current.values())) == 1, current


def test_every_core_source_pins_its_header_at_current():
    """The engine builds main and detection from these, so a lagging pin rebuilds them on an old header."""
    sources = sorted((ROOT / "sources").glob("*.yaml"))
    assert sources
    for path in sources:
        source = read_yaml(path)
        header = source["versions"][source["current"]]["header"]
        profile = read_yaml(ROOT / "common-header" / f"{header['type']}.yaml")
        assert header["version"] == profile["current"], f"{path.name} pins {header}"
