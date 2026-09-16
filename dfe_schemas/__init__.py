#  Project:      dfe-schemas
#  File:         dfe_schemas/__init__.py
#  Purpose:      Package entry: locate the schema trees, in a wheel or a checkout
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""DFE table schemas plus the ClickHouse DDL mechanics that render them.

This package is the only place a DFE table, view, TTL, role, grant or Kafka
bootstrap topic is defined. ``manifest.yaml`` lists every one of them in the
order they are created; :mod:`dfe_schemas.manifest` reads and checks it, and
:mod:`dfe_schemas.render` turns it into checksummed statements for a topology
the caller names.

Installed as a wheel, the schema trees (``tables/``, ``hunts/``,
``common-header/``, ``meta/``, ``additional/``, ``sources/``, ``registries/``,
``views/``, ``roles/``, ``topics/``) ship as package data alongside
``manifest.yaml``; in a git checkout the same trees sit at the repository root.
:func:`schemas_root` resolves either.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("dfe-schemas")
except PackageNotFoundError:  # a checkout used as a submodule, not installed
    __version__ = "0.0.0"

__all__ = ["__version__", "manifest_path", "schemas_root"]


def schemas_root() -> Path:
    """The directory holding the schema trees.

    Package data when installed, the repository root when this file is read
    out of a checkout.
    """
    package_dir = Path(__file__).resolve().parent
    packaged = package_dir / "data"
    if (packaged / "tables").is_dir():
        return packaged
    return package_dir.parent


def manifest_path() -> Path:
    """The apply manifest, wherever the trees resolved from."""
    return schemas_root() / "manifest.yaml"
