#  Project:      dfe-schemas
#  File:         tests/test_generate_meta_schemas.py
#  Purpose:      The meta-schema generator never rewrites a version it already wrote
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A written version entry is immutable, because consumers pin versions independently."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_meta_schemas.py"


def _dump(directory: Path, rows: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(row) for row in rows)
    (directory / "assets.jsonl").write_text(lines + "\n", encoding="utf-8", newline="\n")
    return directory


def _generate(dump: Path, out_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(SCRIPT),
            "--dump",
            str(dump),
            "--provider",
            "acme",
            "--out-root",
            str(out_root),
            "--date",
            "2026-11-01",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


FIRST = [{"id": "a1", "name": "web"}, {"id": "a2", "name": "db"}]
CHANGED = [{"id": "a1", "name": "web", "port": 443}, {"id": "a2", "name": "db", "port": 5432}]


@pytest.mark.parametrize(
    ("second", "exit_code", "message"),
    [
        pytest.param(FIRST, 0, "unchanged", id="same_dump_is_a_no_op"),
        pytest.param(CHANGED, 1, "already holds version 1.0.0", id="changed_dump_is_refused"),
    ],
)
def test_a_written_version_is_never_rewritten(tmp_path, second, exit_code, message):
    out_root = tmp_path / "meta"
    assert _generate(_dump(tmp_path / "one", FIRST), out_root).returncode == 0
    written = (out_root / "acme" / "assets.yaml").read_text(encoding="utf-8")

    result = _generate(_dump(tmp_path / "two", second), out_root)

    assert result.returncode == exit_code, result.stdout + result.stderr
    assert message in result.stdout + result.stderr
    assert (out_root / "acme" / "assets.yaml").read_text(encoding="utf-8") == written


def _generator():
    """The script as a module, so a test can replace what it reads from dfe_schemas."""
    spec = importlib.util.spec_from_file_location("generate_meta_schemas", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_low_cardinality_string_declares_cardinality_not_the_retired_attribute(tmp_path):
    rows = [{"id": f"a{i}", "state": "up" if i % 2 else "down", "note": f"n{i}"} for i in range(8)]
    out_root = tmp_path / "meta"
    assert _generate(_dump(tmp_path / "one", rows), out_root).returncode == 0
    written = (out_root / "acme" / "assets.yaml").read_text(encoding="utf-8")
    state = written.split("- name: state")[1].split("- name:")[0]
    assert "cardinality: low" in state
    assert "lowcardinality" not in written


@pytest.mark.parametrize(
    ("values", "primitive"),
    [
        pytest.param([["a"]], "string", id="strings"),
        pytest.param([[1]], "integer", id="integers"),
        pytest.param([[1.5]], "float", id="floats"),
        pytest.param([[True]], "boolean", id="booleans"),
        pytest.param([[{"k": 1}]], "json", id="objects"),
    ],
)
def test_an_array_column_takes_its_type_and_codec_from_the_registry(values, primitive, monkeypatch):
    generator = _generator()
    registry = {
        "string": {"ch_type": "S1", "codec": "C1"},
        "integer": {"ch_type": "S2", "codec": "C2"},
        "float": {"ch_type": "S3", "codec": "C3"},
        "boolean": {"ch_type": "S4", "codec": "C4"},
        "json": {"ch_type": "S5", "codec": "C5"},
    }
    monkeypatch.setattr(generator, "_primitives", lambda: registry)
    stats = generator.profile([{"id": "x", "tags": value} for value in values])["tags"]

    column = generator.derive_column(
        "tags", stats, rows=1, row_key="id", record_path="", text_min_length=256, notes=[]
    )

    assert column.ch_override == f"Array({registry[primitive]['ch_type']})"
    assert column.codec == registry[primitive]["codec"]


def test_the_reserved_names_are_the_headers_columns_and_the_envelopes(monkeypatch):
    generator = _generator()
    monkeypatch.setattr(generator, "load_profile", lambda name: [SimpleNamespace(name="_alpha")])
    monkeypatch.setattr(generator, "load_columns", lambda path: [SimpleNamespace(name="frame")])
    assert generator._reserved_names().split(",") == ["_alpha", "frame", "timestamp"]


def test_the_shipped_reserved_names_cover_the_header_and_the_snapshot_envelope():
    reserved = set(_generator().DEFAULT_RESERVED.split(","))
    assert {"_org_id", "_uuid", "_raw", "_json", "_tags", "_source", "_timestamp"} <= reserved
    assert {"kind", "snapshot_id", "snapshot_at", "row_key", "timestamp"} <= reserved
