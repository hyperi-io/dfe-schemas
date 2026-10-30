#  Project:      dfe-schemas
#  File:         tests/test_producer_contracts.py
#  Purpose:      A meta schema's columns read the keys its producer writes
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A meta schema's columns must find their values in the producer's own output.

The OTLP fixtures are dfe-receiver's records for the same two OTLP log records
sent by the OpenTelemetry Python SDK (see scripts/otlp_logs_fixture.py). The
CloudWatch fixture is the row dfe-fetcher's cloudwatch_logs row builder writes
in its own test (crates/rest/src/hooks/cloudwatch_logs.rs). A fixture typed
from the schema would carry whatever key spelling the schema author expected,
which is how meta/otel/logs came to read camelCase keys neither receiver mode
writes.

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

OTEL_LOGS = "otel/logs"
CLOUDWATCH_LOGS = "aws/cloudwatch_logs"
CLOUDWATCH_FIXTURE = "cloudwatch_logs_fetcher"

# (meta schema, producer fixture, column, the key the producer writes the value under)
PRODUCER_KEYS = [
    (OTEL_LOGS, "otlp_logs_receiver_hyperdx", "timestamp", "Timestamp"),
    (OTEL_LOGS, "otlp_logs_receiver_hyperdx", "severity_text", "SeverityText"),
    (OTEL_LOGS, "otlp_logs_receiver_hyperdx", "severity_number", "SeverityNumber"),
    (OTEL_LOGS, "otlp_logs_receiver_hyperdx", "service_name", "ServiceName"),
    (OTEL_LOGS, "otlp_logs_receiver_hyperdx", "trace_id", "TraceId"),
    (OTEL_LOGS, "otlp_logs_receiver_hyperdx", "span_id", "SpanId"),
    (OTEL_LOGS, "otlp_logs_receiver_hyperdx", "body", "Body"),
    (OTEL_LOGS, "otlp_logs_receiver_generic", "timestamp", "_timestamp"),
    (OTEL_LOGS, "otlp_logs_receiver_generic", "severity_text", "severity_text"),
    (OTEL_LOGS, "otlp_logs_receiver_generic", "severity_number", "severity_number"),
    (OTEL_LOGS, "otlp_logs_receiver_generic", "trace_id", "trace_id"),
    (OTEL_LOGS, "otlp_logs_receiver_generic", "span_id", "span_id"),
    (OTEL_LOGS, "otlp_logs_receiver_generic", "body", "body"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "event_id", "eventId"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "log_group_name", "logGroupName"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "log_stream_name", "logStreamName"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "timestamp", "timestamp"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "ingestion_time", "ingestionTime"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "message", "message"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "aws_account_id", "awsAccountId"),
    (CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE, "aws_region", "awsRegion"),
]

# Schemas whose producer writes a key for every column, so a column missing
# from the table above is a column that always lands empty.
FULLY_PRODUCED = [(CLOUDWATCH_LOGS, CLOUDWATCH_FIXTURE)]


def _records(fixture: str) -> list[dict]:
    path = FIXTURES / f"{fixture}.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _columns(schema_ref: str = OTEL_LOGS) -> dict[str, str]:
    schema = read_yaml(ROOT / "meta" / f"{schema_ref}.yaml")
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


@pytest.mark.parametrize(("schema_ref", "fixture", "column", "key"), PRODUCER_KEYS)
def test_a_column_takes_its_value_from_the_producer_output(schema_ref, fixture, column, key):
    columns = _columns(schema_ref)
    assert column in columns, f"meta/{schema_ref} has no {column} column"
    for record in _records(fixture):
        assert key in record, f"the {fixture} fixture has no {key}"
        assert resolve(columns[column], record) == record[key]


@pytest.mark.parametrize(("schema_ref", "fixture"), FULLY_PRODUCED)
def test_every_key_the_producer_writes_has_a_column(schema_ref, fixture):
    """Both directions: no column reads a key the producer never writes, and no key is dropped."""
    rows = [row[2:] for row in PRODUCER_KEYS if row[:2] == (schema_ref, fixture)]
    assert {column for column, _key in rows} == set(_columns(schema_ref))
    for record in _records(fixture):
        assert set(record) == {key for _column, key in rows}


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
    for record in _records(f"otlp_logs_receiver_{mode}"):
        assert key in record[attributes], f"the {mode} fixture has no {attributes}[{key!r}]"
        assert resolve(expr, record) == record[attributes][key]


@pytest.mark.parametrize("mode", ["hyperdx", "generic"])
def test_the_traced_record_lands_severity_trace_and_body(mode):
    """The first fixture record is a WARN inside a trace, so none of these may come back empty."""
    record = _records(f"otlp_logs_receiver_{mode}")[0]
    landed = {name: resolve(expr, record) for name, expr in _columns().items()}
    assert landed["severity_text"] == "WARN"
    assert landed["severity_number"] == 13
    assert len(landed["trace_id"]) == 32
    assert len(landed["span_id"]) == 16
    assert landed["body"].startswith("request complete")


def test_a_record_with_none_of_the_keys_leaves_the_column_absent():
    assert all(resolve(expr, {"unrelated": 1}) is None for expr in _columns().values())
