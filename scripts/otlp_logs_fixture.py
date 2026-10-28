# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "opentelemetry-sdk==1.45.1",
#     "opentelemetry-exporter-otlp-proto-http==1.45.1",
# ]
# ///
"""Send two fixed OTLP log records, through the OpenTelemetry SDK, to a dfe-receiver.

Regenerates tests/fixtures/otlp_logs_receiver_*.ndjson. Run a dfe-receiver on the
Kafka transport with the OTLP HTTP listener on, once with `otlp.mode` unset
(hyperdx) and once with `otlp.mode: generic`. For each, run this script against
its listener, then read the receiver's records off the topic it wrote:

    uv run scripts/otlp_logs_fixture.py http://127.0.0.1:4318
"""

import sys

from opentelemetry._logs import LogRecord, SeverityNumber  # ty: ignore[unresolved-import]
from opentelemetry.exporter.otlp.proto.http._log_exporter import (  # ty: ignore[unresolved-import]
    OTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider  # ty: ignore[unresolved-import]
from opentelemetry.sdk._logs.export import (  # ty: ignore[unresolved-import]
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.resources import Resource  # ty: ignore[unresolved-import]
from opentelemetry.trace import TraceFlags  # ty: ignore[unresolved-import]

endpoint = sys.argv[1]
resource = Resource(
    {
        "service.name": "checkout-api",
        "service.instance.id": "instance-1",
        "host.name": "app-01",
        "telemetry.sdk.language": "python",
    }
)
provider = LoggerProvider(resource=resource)
provider.add_log_record_processor(
    SimpleLogRecordProcessor(OTLPLogExporter(endpoint=endpoint + "/v1/logs"))
)
logger = provider.get_logger("probe")

# A WARN inside a trace, with the SDK's own severity text.
logger.emit(
    LogRecord(
        timestamp=1_793_153_465_000_000_000,
        observed_timestamp=1_793_153_465_000_000_000,
        trace_id=0xCEF7083DB6EA01B762AD95BFBEF5CAE8,
        span_id=0x458E193CC6B0B179,
        trace_flags=TraceFlags(1),
        severity_text="WARN",
        severity_number=SeverityNumber.WARN,
        body="request complete method=GET path=/api/v1/health status=200",
        attributes={"http.status": 200},
    )
)
# An ERROR with a severity number only and no trace context.
logger.emit(
    LogRecord(
        timestamp=1_793_153_466_000_000_000,
        observed_timestamp=1_793_153_466_000_000_000,
        severity_number=SeverityNumber.ERROR,
        body="upstream returned status=502 target=billing.example.com",
    )
)
provider.shutdown()
