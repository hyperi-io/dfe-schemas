#  Project:      dfe-schemas
#  File:         scripts/render_ddl.py
#  Purpose:      Render reference ClickHouse DDL from the schema YAML using
#                dfe-engine's DDLFileWriter, into the committed ddl/ tree.
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED
"""Render argocd/ddl/ from the dfe-schemas YAML via dfe-engine's DDLFileWriter.

The writer emits a version-nested tree, which is not the flat layout the
Argo migration Job mounts from ``argocd/ddl/``, and the committed flat files
have been hand-edited since. Reconciling the two is tracked in issue #9, so
CI does not gate on this render. Requires dfe-engine importable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    """Render all reference DDL files into ``<repo>/ddl``."""
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

    written = DDLFileWriter().write_all(repo_root / "argocd" / "ddl")
    for path in written:
        print(path.relative_to(repo_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
