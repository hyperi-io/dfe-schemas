"""The column COMMENT directive vocabulary, shared with dfe-loader and dfe-engine."""

import json
from importlib.resources import files

_VOCABULARY = json.loads(
    files("dfe_schemas").joinpath("directives.json").read_text(encoding="utf-8")
)

# The directives dfe-loader acts on (dfe-loader src/column_meta/mod.rs).
LOADER_DIRECTIVES: frozenset[str] = frozenset(_VOCABULARY["loader"])
# Directives that say how ClickHouse or the loader fills a column, which the loader deliberately skips.
DESCRIPTIVE_DIRECTIVES: frozenset[str] = frozenset(_VOCABULARY["descriptive"])
DIRECTIVES: frozenset[str] = LOADER_DIRECTIVES | DESCRIPTIVE_DIRECTIVES


def leading_directive(expr: str) -> str | None:
    """Return the name of the directive an expr starts with, or None when it starts with none.

    Args:
        expr: A column's expr, such as ``@source: host.name``.

    Returns:
        The directive name without its ``@``, or None.
    """
    text = expr.lstrip()
    if not text.startswith("@"):
        return None
    name = text[1:].split(":", 1)[0].split(None, 1)
    return name[0] if name else ""
