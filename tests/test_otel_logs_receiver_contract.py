#  Project:      dfe-schemas
#  File:         tests/test_otel_logs_receiver_contract.py
#  Purpose:      meta/otel/logs reads what dfe-receiver emits in each OTLP mode
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The OTel logs columns must find their values in the receiver's own output.

The fixtures are dfe-receiver's records for the same two OTLP log records sent
by the OpenTelemetry Python SDK (see scripts/otlp_logs_fixture.py). A hand-typed
fixture would carry whatever key spelling the schema author expected, which is
how the schema came to read camelCase keys neither receiver mode writes.

`resolve` follows dfe-loader's source lookup: the candidates of `first(a/b/c)` in
order, a dotted candidate descending nested objects.
"""

import json
import re
from pathlib import Path

import pytest

import dfe_schemas
from dfe_schemas.loader import read_yaml

ROOT = dfe_schemas.schemas_root()
FIXTURES = Path(__file__).parent / "fixtures"

# (receiver mode, column, the key that mode writes the value under)
MODE_KEYS = [
    ("hyperdx", "timestamp", "Timestamp"),
    ("hyperdx", "severity_text", "SeverityText"),
    ("hyperdx", "severity_number", "SeverityNumber"),
    ("hyperdx", "service_name", "ServiceName"),
    ("hyperdx", "trace_id", "TraceId"),
    ("hyperdx", "span_id", "SpanId"),
    ("hyperdx", "body", "Body"),
    ("generic", "timestamp", "_timestamp"),
    ("generic", "severity_text", "severity_text"),
    ("generic", "severity_number", "severity_number"),
    ("generic", "trace_id", "trace_id"),
    ("generic", "span_id", "span_id"),
    ("generic", "body", "body"),
]


def _records(mode: str) -> list[dict]:
    path = FIXTURES / f"otlp_logs_receiver_{mode}.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _columns() -> dict[str, str]:
    schema = read_yaml(ROOT / "meta" / "otel" / "logs.yaml")
    version = schema["versions"][schema["current"]]
    return {column["name"]: column["expr"] for column in version["columns"]}


def _candidates(expr: str) -> list[str]:
    directive = expr.removeprefix("@source:").split("|")[0].strip()
    wrapped = re.fullmatch(r"first\((.*)\)", directive)
    return wrapped.group(1).split("/") if wrapped else [directive]


def _lookup(node: dict, path: str):
    """Find ``path`` in ``node``: the whole path as one key, else each dot as the split
    between an object key and the rest, left to right (dfe-loader's source lookup)."""
    if path in node:
        return node[path]
    for index, char in enumerate(path):
        child = node.get(path[:index]) if char == "." else None
        if isinstance(child, dict):
            found = _lookup(child, path[index + 1 :])
            if found is not None:
                return found
    return None


def resolve(expr: str, record: dict):
    """Return the value a loader would take for ``expr`` from ``record``, or None."""
    for path in _candidates(expr):
        found = _lookup(record, path)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize(("mode", "column", "key"), MODE_KEYS)
def test_a_column_takes_its_value_from_the_receiver_output(mode, column, key):
    expr = _columns()[column]
    for record in _records(mode):
        assert key in record, f"the {mode} fixture has no {key}"
        assert resolve(expr, record) == record[key]


# (receiver mode, column, the attribute map that mode writes, the dotted key in it)
RESOURCE_KEYS = [
    ("hyperdx", "service_name", "ResourceAttributes", "service.name"),
    ("hyperdx", "host_name", "ResourceAttributes", "host.name"),
    ("generic", "service_name", "resource", "service.name"),
    ("generic", "host_name", "resource", "host.name"),
]


@pytest.mark.parametrize(("mode", "column", "attributes", "key"), RESOURCE_KEYS)
def test_a_column_takes_its_value_from_a_resource_attribute(mode, column, attributes, key):
    """The attribute maps are objects keyed by literal dotted names, such as "service.name"."""
    expr = _columns()[column]
    for record in _records(mode):
        assert key in record[attributes], f"the {mode} fixture has no {attributes}[{key!r}]"
        assert resolve(expr, record) == record[attributes][key]


@pytest.mark.parametrize("mode", ["hyperdx", "generic"])
def test_the_traced_record_lands_severity_trace_and_body(mode):
    """The first fixture record is a WARN inside a trace, so none of these may come back empty."""
    record = _records(mode)[0]
    landed = {name: resolve(expr, record) for name, expr in _columns().items()}
    assert landed["severity_text"] == "WARN"
    assert landed["severity_number"] == 13
    assert len(landed["trace_id"]) == 32
    assert len(landed["span_id"]) == 16
    assert landed["body"].startswith("request complete")


def test_a_record_with_none_of_the_keys_leaves_the_column_absent():
    assert all(resolve(expr, {"unrelated": 1}) is None for expr in _columns().values())
