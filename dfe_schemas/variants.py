#  Project:      dfe-schemas
#  File:         dfe_schemas/variants.py
#  Purpose:      Pick one variant of a named block out of a file of variant blocks
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A file of named blocks, each written once per variant, and the selector over it.

The file is plain SQL with a marker line opening each block::

    -- @block create_database self_hosted
    CREATE DATABASE IF NOT EXISTS ${database} ...

    -- @block create_database cloud
    CREATE DATABASE IF NOT EXISTS ${database}

The selector knows nothing about what the blocks say. It returns the body of one
block for one variant with ``${name}`` placeholders filled in, and refuses rather
than guesses: a block that names a variant another block does not, a duplicate
block, a placeholder left unfilled, and an unknown block or variant all raise.

Comment lines inside a body are dropped, so rewording one never changes the
statement or its checksum.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

from dfe_schemas.loader import SchemaError

__all__ = ["VariantBlocks", "VariantError", "load_variant_blocks"]

_MARKER = re.compile(
    r"^--\s*@block\s+(?P<block>[a-z][a-z0-9_]*)\s+(?P<variant>[a-z][a-z0-9_]*)\s*$"
)


class VariantError(SchemaError):
    """A variant file is malformed, or a block or variant asked for is not in it."""


@dataclass(frozen=True, slots=True)
class VariantBlocks:
    """Every block in one file, by block name and then by variant.

    Attributes:
        source: Where the blocks were read from, for error messages.
        blocks: Block name to variant name to the block's body, comments removed.
    """

    source: str
    blocks: dict[str, dict[str, str]]

    def select(self, block: str, variant: str, **values: str) -> str:
        """The body of *block* for *variant*, with every ``${name}`` filled from *values*.

        Raises:
            VariantError: The block or the variant is unknown, or a placeholder in the
                body has no value.
        """
        bodies = self._block(block)
        if variant not in bodies:
            raise VariantError(
                f"{self.source}: block {block!r} has no {variant!r} variant; "
                f"it has {', '.join(sorted(bodies))}"
            )
        try:
            return Template(bodies[variant]).substitute(values)
        except (KeyError, ValueError) as exc:
            raise VariantError(
                f"{self.source}: block {block!r} variant {variant!r} needs a value for {exc}"
            ) from exc

    def _block(self, block: str) -> dict[str, str]:
        if block not in self.blocks:
            raise VariantError(
                f"{self.source}: no block {block!r}; it has {', '.join(sorted(self.blocks))}"
            )
        return self.blocks[block]


def _body(lines: list[str]) -> str:
    kept = [line.rstrip() for line in lines if not line.lstrip().startswith("--")]
    return "\n".join(kept).strip()


def load_variant_blocks(path: Path) -> VariantBlocks:
    """Read *path* into its blocks, checking that every block names every variant.

    Raises:
        VariantError: The file is unreadable, holds a statement before the first
            marker, repeats a block and variant, or leaves a variant out of a block.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VariantError(f"could not read {path}: {exc}") from exc

    source = str(path)
    blocks: dict[str, dict[str, str]] = {}
    current: tuple[str, str] | None = None
    pending: list[str] = []

    def close() -> None:
        if current is None:
            if _body(pending):
                raise VariantError(f"{source}: text before the first '-- @block' marker")
            return
        block, variant = current
        if variant in blocks.setdefault(block, {}):
            raise VariantError(f"{source}: block {block!r} variant {variant!r} appears twice")
        blocks[block][variant] = _body(pending)

    for line in text.splitlines():
        marker = _MARKER.match(line.strip())
        if marker is None:
            pending.append(line)
            continue
        close()
        current = (marker["block"], marker["variant"])
        pending = []
    close()

    if not blocks:
        raise VariantError(f"{source}: no '-- @block' markers")
    every = {variant for bodies in blocks.values() for variant in bodies}
    for block, bodies in sorted(blocks.items()):
        missing = sorted(every - set(bodies))
        if missing:
            raise VariantError(f"{source}: block {block!r} has no {', '.join(missing)} variant")
    return VariantBlocks(source=source, blocks=blocks)
