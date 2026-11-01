#  Project:      dfe-schemas
#  File:         tests/test_generate_meta_schemas.py
#  Purpose:      The meta-schema generator never rewrites a version it already wrote
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A written version entry is immutable, because consumers pin versions independently."""

import json
import subprocess
import sys
from pathlib import Path

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
