#  Project:      dfe-schemas
#  File:         scripts/render_ddl.py
#  Purpose:      Render reference ClickHouse DDL from the schema YAML using
#                dfe-engine's DDLFileWriter, into the committed ddl/ tree.
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED
"""Render ddl/ from the dfe-schemas YAML via dfe-engine's DDLFileWriter.

This is the ONLY producer of the committed ``ddl/`` tree. CI renders and
diff-checks it (see .github/workflows/ci.yml); the Argo migration Job
(argocd/) applies it to ClickHouse independently of dfe-engine. Requires
dfe-engine importable in the environment (CI installs it).
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

    written = DDLFileWriter().write_all(repo_root / "ddl")
    for path in written:
        print(path.relative_to(repo_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
