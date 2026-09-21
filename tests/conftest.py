#  Purpose:      Shared test helpers for the schema-tree sweeps
#  License:      BUSL-1.1

"""Find the schema YAML a sweep should actually check.

`schemas_root()` is the repo root, and `rglob` from there walks into `.venv`
and `.worktrees` -- so a sweep picks up the installed wheel and every other
checkout on the machine. Those pass on clean CI and fail on a developer's box,
which makes them look like the change under test.
"""

from __future__ import annotations

from pathlib import Path

# A directory name here prunes the whole subtree. `site-packages` catches an
# installed copy of this same package; a leading dot catches venvs, worktrees
# and tool caches.
_PRUNED = {"site-packages", "node_modules", "__pycache__", "target", "build", "dist"}


def _is_pruned(path: Path, root: Path) -> bool:
    return any(
        part in _PRUNED or part.startswith(".") for part in path.relative_to(root).parts[:-1]
    )


def schema_yaml(root: Path | str) -> list[Path]:
    """Every committed schema YAML under ``root``, nothing borrowed."""
    root = Path(root)
    return sorted(p for p in root.rglob("*.yaml") if not _is_pruned(p, root))
