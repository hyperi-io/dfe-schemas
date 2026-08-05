#  Project:      dfe-schemas
#  File:         scripts/render_ddl.py
#  Purpose:      Render the deployed ClickHouse DDL from the schema YAML using
#                dfe-engine's DDLFileWriter, into the committed argocd/ddl/.
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED
"""Render argocd/ddl/ from the dfe-schemas YAML via dfe-engine's DDLFileWriter.

This is the only producer of the committed ``argocd/ddl/`` tree; CI renders and
checks it for drift. The Argo migration Job (argocd/) applies it to ClickHouse
independently of dfe-engine, deriving each database from the ``<db>.<table>``
filename and substituting the ``{db}`` placeholder.

``DDLFileWriter.write_all`` is deliberately not used: it emits a version-nested
reference tree, and the deploy unit needs the flat one kustomize mounts.

Requires dfe-engine importable in the environment (CI installs it).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Profiles rendered as reference SQL under ddl/profiles/. Documentation only -
# argocd/kustomization.yaml mounts the core tables and not these.
_PROFILES = ("timeseries", "minimal", "passthrough")


def main() -> int:
    """Render the deployed DDL files into ``<repo>/argocd/ddl``."""
    repo_root = Path(__file__).resolve().parent.parent
    # Point the engine's schema loader at THIS repo, not a submodule.
    os.environ["DFE_SCHEMAS_DIR"] = str(repo_root)

    try:
        from dfe_engine.schema.ddl_writer import DDLFileWriter
    except ImportError as exc:  # pragma: no cover - environment guard
        print(
            "dfe-engine is not importable. Install it first "
            "(CI checks out + uv-syncs dfe-engine). Error: " + str(exc),
            file=sys.stderr,
        )
        return 2

    writer = DDLFileWriter()
    ddl_dir = repo_root / "argocd" / "ddl"

    targets: list[tuple[Path, str]] = [
        (ddl_dir / "dfe.default.sql", writer.generate_default_table()),
        (
            ddl_dir / "dfe_audit.detection_checkpoint.sql",
            writer.generate_detection_checkpoint_table(),
        ),
        (ddl_dir / "dfe_hunts.detection.sql", writer.generate_detection_table()),
    ]
    targets += [
        (ddl_dir / "profiles" / f"{name}.sql", writer.generate_profile_table(name))
        for name in _PROFILES
    ]

    for path, sql in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql, encoding="utf-8", newline="\n")
        print(path.relative_to(repo_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
