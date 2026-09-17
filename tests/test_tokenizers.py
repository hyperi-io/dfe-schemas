#  Project:      dfe-schemas
#  File:         tests/test_tokenizers.py
#  Purpose:      Every declared text-index tokenizer is one ClickHouse accepts
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""ClickHouse renamed every text-index tokenizer, and a stale name is fatal.

The rename landed as a backward-incompatible change: `default` became
`splitByNonAlpha`, `ngram` became `ngrams`, `split` became `splitByString` and
`no_op` became `array`. A retired name does not degrade -- the server refuses
the CREATE with `Unknown tokenizer`, so the table never exists and the source
that needed it never lands a row.

The whole tree was migrated except two `index:` strings in the timeseries common
header, and those survived because the renderer dropped a column's declared
index and rendered the use_case template instead. The moment the engine started
honouring the declared string, every new source on the default profile failed to
deploy.

So this asserts the names rather than the rendering: a tokenizer is only ever
read by the server, and the server is the thing that refuses it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import dfe_schemas

ROOT = Path(dfe_schemas.schemas_root())

# Accepted across the ClickHouse versions the tree supports; `SELECT name FROM
# system.tokenizers` is the authority for one server.
VALID = frozenset(
    {
        "splitByNonAlpha",
        "splitByString",
        "splitByRegexp",
        "ngrams",
        "sparseGrams",
        "array",
        "asciiCJK",
        "chinese",
        "icu",
        "japanese",
        "keyValuePairs",
    }
)

# Retired, and what each one became.
RETIRED = {
    "default": "splitByNonAlpha",
    "ngram": "ngrams",
    "split": "splitByString",
    "no_op": "array",
}

# `tokenizer = 'name'` or `tokenizer=name`, with or without an argument list.
_TOKENIZER = re.compile(r"tokenizer\s*=\s*'?(?P<name>\w+)'?")


def _declarations() -> list[tuple[Path, int, str]]:
    """Every tokenizer name declared anywhere in the schema tree, with its line."""
    found: list[tuple[Path, int, str]] = []
    for path in sorted(ROOT.rglob("*.yaml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _TOKENIZER.finditer(line):
                found.append((path, number, match.group("name")))
    return found


def test_the_tree_declares_at_least_one_tokenizer():
    """A regex that matches nothing would pass every assertion below."""
    assert _declarations(), "no tokenizer declaration found; the pattern has gone stale"


@pytest.mark.parametrize("path, number, name", _declarations())
def test_every_declared_tokenizer_is_one_the_server_accepts(path, number, name):
    """A retired name is refused at CREATE, so the table never exists."""
    replacement = RETIRED.get(name)
    assert replacement is None, (
        f"{path.relative_to(ROOT)}:{number} declares the retired tokenizer "
        f"{name!r}; ClickHouse renamed it to {replacement!r}"
    )
    assert name in VALID, (
        f"{path.relative_to(ROOT)}:{number} declares tokenizer {name!r}, which is "
        f"not one ClickHouse accepts; valid: {', '.join(sorted(VALID))}"
    )
