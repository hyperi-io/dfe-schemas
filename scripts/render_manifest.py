#!/usr/bin/env python3
#  Project:      dfe-schemas
#  File:         scripts/render_manifest.py
#  Purpose:      Render every manifest object for every topology, and check it
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""Render the apply manifest for every topology, and fail on what must not ship.

Needs nothing but this package, which is the point: the source of truth checks
its own content rather than borrowing its consumer's loader.

What it fails on:

* a duplicate object id, or a dependency that is unknown or declared later
  (both from the manifest's own check)
* two objects rendering to the same qualified name, which one id per object
  does not on its own prevent
* a definition that does not render at all under some topology
* a checksum that differs between topologies, which would mean the topology
  token leaked into it and every cluster would read as drifted

Usage::

    python3 scripts/render_manifest.py
    python3 scripts/render_manifest.py --topology replicated_on_cluster --print
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dfe_schemas.clickhouse import Topology  # noqa: E402
from dfe_schemas.manifest import ManifestError, load_manifest  # noqa: E402
from dfe_schemas.render import Renderer, qualified_name, referenced_objects  # noqa: E402

TOPOLOGIES = (Topology.SINGLE, Topology.REPLICATED, Topology.REPLICATED_ON_CLUSTER)


def main() -> int:
    """Render every object for every topology; return 1 on any problem."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topology",
        choices=[topology.value for topology in TOPOLOGIES],
        help="render one topology instead of all three",
    )
    parser.add_argument(
        "--print", action="store_true", dest="show", help="print every rendered statement"
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(root=REPO_ROOT)
    except ManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    wanted = [Topology(args.topology)] if args.topology else list(TOPOLOGIES)
    problems: list[str] = []
    checksums: dict[str, dict[str, str]] = {}
    kinds: Counter[str] = Counter()

    for topology in wanted:
        renderer = Renderer(manifest, topology=topology, cluster="dfe_cluster")
        names: dict[str, str] = {}
        references: list[tuple[str, set[str]]] = []
        for obj in manifest.objects:
            try:
                rendered = renderer.render(obj)
            except Exception as exc:
                problems.append(f"{obj.id} [{topology.value}]: {exc}")
                continue
            qualified = qualified_name(rendered)
            if qualified in names:
                problems.append(
                    f"{obj.id} [{topology.value}]: renders {qualified}, "
                    f"already rendered by {names[qualified]}"
                )
            names[qualified] = obj.id
            references.append((obj.id, referenced_objects(rendered)))
            checksums.setdefault(obj.id, {})[topology.value] = rendered.checksum
            if topology is wanted[0]:
                kinds[rendered.kind] += 1
            if args.show:
                print(f"-- {obj.id} [{topology.value}] {rendered.checksum[:12]}")
                for statement in rendered.statements:
                    print(statement + ";\n")

        # Every object a statement names must itself be in the manifest. This is
        # what catches a view still pointing at a table that was renamed.
        for object_id, referenced in references:
            for target in sorted(referenced - set(names)):
                problems.append(
                    f"{object_id} [{topology.value}]: names {target}, "
                    "which the manifest does not declare"
                )

    for object_id, per_topology in checksums.items():
        distinct = set(per_topology.values())
        if len(distinct) > 1:
            rendered_as = ", ".join(f"{k}={v[:12]}" for k, v in sorted(per_topology.items()))
            problems.append(
                f"{object_id}: checksum differs by topology ({rendered_as}); the topology "
                "token must normalise out"
            )

    if problems:
        print("The apply manifest does not render clean:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    summary = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    print(
        f"Rendered {len(manifest.objects)} objects ({summary}) "
        f"for {len(wanted)} topolog{'y' if len(wanted) == 1 else 'ies'}: OK"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
