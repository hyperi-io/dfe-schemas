#  Project:      dfe-schemas
#  File:         tests/test_catalogue.py
#  Purpose:      Every catalogue file is a source write body with a consistent catalogue block
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The catalogue format, checked without dfe-engine (validate_schemas.py builds each body)."""

import copy
from pathlib import Path

import pytest

import dfe_schemas
from dfe_schemas.loader import SchemaError, read_yaml, resolve

ROOT = dfe_schemas.schemas_root()
CATALOGUE = ROOT / "catalogue"

TOP_LEVEL_KEYS = (
    "source",
    "display_name",
    "description",
    "match",
    "fetcher",
    "schema",
    "transform",
    "catalogue",
)

CATALOGUE_KEYS = ("package", "data_stream", "intakes", "framing", "beats", "variants")

INTAKES = ("beats", "receiver", "fetcher")

FRAMINGS = ("line", "body")

# A source arrives by the first of these it lists, and its body carries that origin.
DEFAULT_ORIGIN_ORDER = ("fetcher", "receiver", "beats")

# The record fields a beats and a receiver match test: Beats stamps the integration's dataset, a pushed sender stamps the source name.
BEATS_DATASET_FIELD = "data_stream.dataset"
SOURCE_LABEL_FIELD = "_source"

SCHEMA_LAYERS = ("meta_schema", "derived_schema", "additional_fields")

WELL_FORMED_SCHEMA = {
    "meta_schema": "meta/elastic/ecs",
    "derived_schema": "derived/acme/log",
    "additional_fields": "additional/acme/log",
}

WELL_FORMED = {
    "source": "acme-log",
    "display_name": "acme.log",
    "description": "The log data stream of the Elastic acme integration.",
    "match": {"field": BEATS_DATASET_FIELD, "operator": "equals", "value": "acme.log"},
    "schema": WELL_FORMED_SCHEMA,
    "transform": {"engine": "elastic", "variant": "filebeat.acme_log.default"},
    "catalogue": {
        "package": "acme",
        "data_stream": "log",
        "intakes": ["beats"],
        "variants": ["filebeat.acme_log.default", "filebeat.acme_log.json"],
    },
}

_FETCHER = {"source_type": "acme", "topic": "own"}

_FETCHER_ORIGIN = {"match": None, "fetcher": _FETCHER}

WELL_FORMED_CASES = [
    pytest.param({}, id="beats"),
    pytest.param(
        {
            "catalogue.intakes": ["beats", "receiver"],
            "match.field": SOURCE_LABEL_FIELD,
            "match.value": "acme-log",
        },
        id="receiver",
    ),
    pytest.param({**_FETCHER_ORIGIN, "catalogue.intakes": ["beats", "fetcher"]}, id="fetcher"),
    pytest.param(
        {"catalogue.beats": {"module": "acme", "fileset": "log"}, "catalogue.framing": "line"},
        id="with-beats-and-framing",
    ),
]

MALFORMED_CASES = [
    pytest.param(
        {"source": "acme-other"}, "is not the file name", id="source-is-not-the-file-name"
    ),
    pytest.param({"catalogue.intakes": []}, "non-empty subset", id="no-intakes"),
    pytest.param(
        {"catalogue.intakes": ["beats", "email"]}, "non-empty subset", id="unknown-intake"
    ),
    pytest.param(
        {"catalogue.intakes": ["beats", "receiver"]},
        "default origin is 'receiver'",
        id="receiver-listed-but-beats-origin",
    ),
    pytest.param(
        {"catalogue.intakes": ["beats", "fetcher"]},
        "exactly when fetcher is an intake",
        id="fetcher-listed-but-no-fetcher-body",
    ),
    pytest.param(
        {"catalogue.intakes": ["beats", "fetcher"], "fetcher": _FETCHER},
        "default origin is 'fetcher'",
        id="fetcher-origin-also-carries-a-match",
    ),
    pytest.param(
        _FETCHER_ORIGIN, "exactly when fetcher is an intake", id="fetcher-body-but-not-listed"
    ),
    pytest.param(
        {**_FETCHER_ORIGIN, "catalogue.intakes": ["beats", "fetcher"], "schema": None},
        "only when the body has schema",
        id="fetcher-without-a-schema",
    ),
    pytest.param(
        {"match.value": "acme.other"},
        "default origin is 'beats'",
        id="beats-match-on-another-dataset",
    ),
    pytest.param(
        {"catalogue.intakes": ["beats", "receiver"], "match.field": SOURCE_LABEL_FIELD},
        "default origin is 'receiver'",
        id="receiver-match-on-the-dataset-not-the-source",
    ),
    pytest.param({"catalogue.variants": []}, "variants", id="no-variants"),
    pytest.param(
        {"transform.variant": "filebeat.acme_log.json"}, "variants", id="variant-is-not-the-first"
    ),
    pytest.param(
        {"schema.additional_fields": "additional/acme/none"},
        "does not resolve",
        id="schema-layer-file-missing",
    ),
    pytest.param(
        {"shcema": WELL_FORMED_SCHEMA}, "unknown top-level keys", id="misspelt-top-level-key"
    ),
    pytest.param(
        {"catalogue.fetcher_source_type": "acme"},
        "unknown catalogue keys",
        id="unknown-catalogue-key",
    ),
    pytest.param({"catalogue.package": ""}, "catalogue.package", id="empty-package"),
    pytest.param(
        {"catalogue.data_stream": 7}, "catalogue.data_stream", id="data-stream-not-a-string"
    ),
    pytest.param({"catalogue.beats": ["acme"]}, "catalogue.beats", id="beats-not-a-mapping"),
    pytest.param(
        {"catalogue.beats": {"module": "acme", "fileset": 1}},
        "catalogue.beats",
        id="beats-value-not-a-string",
    ),
    pytest.param({"catalogue.framing": "frame"}, "catalogue.framing", id="unknown-framing"),
    pytest.param(
        {"catalogue.variants": ["filebeat.acme_log.default", 3]},
        "catalogue.variants",
        id="variant-not-a-string",
    ),
]


def _mutated(*, changes: dict[str, object], doc: dict) -> dict:
    """A deep copy of *doc* with each dotted key set to its value, or removed where the value is None."""
    result = copy.deepcopy(doc)
    for dotted, value in changes.items():
        *parents, leaf = dotted.split(".")
        target = result
        for key in parents:
            target = target[key]
        if value is None:
            target.pop(leaf, None)
        else:
            target[leaf] = value
    return result


def _origin_violations(*, block: dict, doc: dict, intakes: list[str]) -> list[str]:
    """Why the body's origin is not the one its preferred listed intake derives."""
    default = next(intake for intake in DEFAULT_ORIGIN_ORDER if intake in intakes)
    found = []
    if ("fetcher" in intakes) != ("fetcher" in doc):
        found.append("the body carries fetcher exactly when fetcher is an intake")
    # dfe-engine refuses a fetcher source on its own topic with no schema.
    if ("fetcher" in intakes) and (not (doc.get("schema"))):
        found.append("fetcher is an intake only when the body has schema")
    if default == "fetcher":
        if ("match" in doc) or (not (doc.get("fetcher"))):
            found.append(f"default origin is {default!r}, so the body carries fetcher and no match")
        return found
    if default == "beats":
        field = BEATS_DATASET_FIELD
        value = f"{block.get('package')}.{block.get('data_stream')}"
    else:
        field = SOURCE_LABEL_FIELD
        value = doc.get("source")
    expected = {"field": field, "operator": "equals", "value": value}
    if ("fetcher" in doc) or (doc.get("match") != expected):
        found.append(f"default origin is {default!r}, so the body carries match {expected!r}")
    return found


def _schema_violations(*, doc: dict, root: Path) -> list[str]:
    """Every schema layer the body references that names no file under *root*."""
    found = []
    schema = doc.get("schema") or {}
    for layer in SCHEMA_LAYERS:
        reference = schema.get(layer)
        if reference is None:
            continue
        try:
            resolve(ref=reference, root=root)
        except SchemaError as exc:
            found.append(f"schema.{layer}: {exc}")
    return found


def _shape_violations(*, block: dict, doc: dict) -> list[str]:
    """Every key outside the format, and every catalogue value of the wrong type."""
    found = []
    unknown = sorted(set(doc) - set(TOP_LEVEL_KEYS))
    if unknown:
        found.append(f"unknown top-level keys {unknown!r}, allowed {TOP_LEVEL_KEYS!r}")
    unknown = sorted(set(block) - set(CATALOGUE_KEYS))
    if unknown:
        found.append(f"unknown catalogue keys {unknown!r}, allowed {CATALOGUE_KEYS!r}")
    for key in ("package", "data_stream"):
        value = block.get(key)
        if not (isinstance(value, str)) or not (value):
            found.append(f"catalogue.{key} {value!r} must be a non-empty string")
    beats = block.get("beats", {})
    beats_is_text = isinstance(beats, dict) and all(
        isinstance(name, str) and isinstance(text, str) for name, text in beats.items()
    )
    if not (beats_is_text):
        found.append(f"catalogue.beats {beats!r} must map strings to strings")
    framing = block.get("framing", FRAMINGS[0])
    if framing not in FRAMINGS:
        found.append(f"catalogue.framing {framing!r} must be one of {FRAMINGS!r}")
    variants = block.get("variants")
    variants_are_text = isinstance(variants, list) and all(
        isinstance(variant, str) for variant in variants
    )
    if not (variants_are_text) or not (variants):
        found.append(f"catalogue.variants {variants!r} must be a non-empty list of strings")
    return found


def _variant_violations(*, block: dict, doc: dict) -> list[str]:
    """Why the body's transform variant is not the first of the entry's variants."""
    variants = block.get("variants") or []
    variant = (doc.get("transform") or {}).get("variant")
    if (variants) and (variant == variants[0]):
        return []
    return [f"transform.variant {variant!r} must be the first of variants {variants!r}"]


def _violations(*, doc: dict, root: Path, stem: str) -> list[str]:
    """Every way one catalogue file breaks the catalogue format, empty when it follows it."""
    found = []
    if doc.get("source") != stem:
        found.append(f"source {doc.get('source')!r} is not the file name {stem!r}")
    block = doc.get("catalogue")
    if not (isinstance(block, dict)):
        found.append(f"catalogue {block!r} must be a mapping")
        block = {}
    found.extend(_shape_violations(block=block, doc=doc))
    intakes = block.get("intakes")
    if isinstance(intakes, list) and (intakes) and (set(intakes) <= set(INTAKES)):
        found.extend(_origin_violations(block=block, doc=doc, intakes=intakes))
    else:
        found.append(f"intakes {intakes!r} must be a non-empty subset of {INTAKES!r}")
    found.extend(_schema_violations(doc=doc, root=root))
    found.extend(_variant_violations(block=block, doc=doc))
    return found


@pytest.fixture(scope="module")
def catalogue() -> dict[str, dict]:
    """Every shipped catalogue file, parsed, by file name stem."""
    return {path.stem: read_yaml(path=path) for path in sorted(CATALOGUE.glob("*.yaml"))}


@pytest.fixture
def schema_tree(tmp_path: Path) -> Path:
    """A schemas root holding a file for each layer the well-formed body names."""
    for reference in WELL_FORMED_SCHEMA.values():
        path = tmp_path / f"{reference}.yaml"
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text("current: '1.0.0'\n", encoding="utf-8")
    return tmp_path


def test_the_catalogue_ships_files(catalogue):
    assert catalogue, f"no catalogue file under {str(CATALOGUE)!r}"


def test_every_file_follows_the_catalogue_format(catalogue):
    found = []
    for stem, doc in catalogue.items():
        violations = _violations(doc=doc, root=ROOT, stem=stem)
        found.extend(f"catalogue/{stem}.yaml: {violation}" for violation in violations)
    assert not (found), "\n".join(found)


@pytest.mark.parametrize("changes", WELL_FORMED_CASES)
def test_a_well_formed_file_has_no_violations(schema_tree, changes):
    doc = _mutated(changes=changes, doc=WELL_FORMED)
    assert _violations(doc=doc, root=schema_tree, stem="acme-log") == []


@pytest.mark.parametrize(("changes", "expected"), MALFORMED_CASES)
def test_a_malformed_file_is_refused(schema_tree, changes, expected):
    doc = _mutated(changes=changes, doc=WELL_FORMED)
    violations = _violations(doc=doc, root=schema_tree, stem="acme-log")
    assert any(expected in violation for violation in violations), violations
