"""Traces, metrics and logs for the two servers, over OpenTelemetry. Off unless asked.

Everything here is a no-op until `setup` finds a collector to talk to
(`OTEL_EXPORTER_OTLP_ENDPOINT`, from the environment or `.env`). The instruments below are
created against the OpenTelemetry *API*, which hands out proxies that do nothing until an
SDK provider is installed -- so the code that records a retrieval or an escalation does it
unconditionally, and a test run, a pipeline step or a laptop without a collector pays one
attribute lookup for it and nothing else: no exporter, no thread, no handler on the root
logger, no change to what is printed.

What is recorded is chosen to be operational and anonymous. Attributes are bounded
vocabularies -- a task, a model, a responder, a reason, a stage -- never a message, a
transcript, a customer id or a session id. That is the LGPD habit the rest of the system
keeps (the data is synthetic in production, and the habit is the point), and it is also
what keeps the metric series countable: an attribute with an unbounded set of values is a
new time series per value.

Auto-instrumentation covers the edges: every HTTP request (FastAPI), every query
(SQLAlchemy), every call to the openai-protocol LLM server (requests). The Anthropic SDK
emits its own spans through the same API, metadata only. The custom spans are the steps
in between that only this code knows about: the orchestrator, retrieval, the responder,
a call turn.
"""

import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace

from collection.config import settings

logger = logging.getLogger(__name__)

tracer = trace.get_tracer("collection")
_meter = metrics.get_meter("collection")

# Bucket boundaries are advice to the SDK, stated here because its defaults are tuned for
# milliseconds and every duration below is in seconds (the OpenTelemetry convention, and
# what Prometheus' `_seconds` suffix then says out loud).
_SECONDS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60]

SCORING_BATCH = _meter.create_histogram(
    "collection.scoring.batch_size",
    unit="{receivable}",
    description="Receivables scored in one /score request.",
    explicit_bucket_boundaries_advisory=[1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000],
)
SCORING_DURATION = _meter.create_histogram(
    "collection.scoring.duration",
    unit="s",
    description="Time to score one batch, model prediction and ranking.",
    explicit_bucket_boundaries_advisory=_SECONDS,
)
RETRIEVAL_DURATION = _meter.create_histogram(
    "collection.retrieval.duration",
    unit="s",
    description="Time to retrieve passages for one customer message, every query included.",
    explicit_bucket_boundaries_advisory=_SECONDS,
)
RETRIEVAL_TOP_SCORE = _meter.create_histogram(
    "collection.retrieval.top_score",
    unit="1",
    description="Cosine similarity of the best passage retrieved for a message.",
    explicit_bucket_boundaries_advisory=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
RESPONDER_DURATION = _meter.create_histogram(
    "collection.responder.duration",
    unit="s",
    description="Time the responder took to write one answer, the LLM call included.",
    explicit_bucket_boundaries_advisory=_SECONDS,
)
RESPONDER_FALLBACKS = _meter.create_counter(
    "collection.responder.fallbacks",
    unit="{answer}",
    description="LLM answers replaced by the template: generation failed or overran its budget.",
)
LLM_TOKENS = _meter.create_counter(
    "collection.llm.tokens",
    unit="{token}",
    description="Tokens the LLM read and wrote, by direction.",
)
COMPLIANCE_CHECKS = _meter.create_counter(
    "collection.compliance.checks",
    unit="{message}",
    description="Outbound messages checked against their brief, by outcome.",
)
COMPLIANCE_VIOLATIONS = _meter.create_counter(
    "collection.compliance.violations",
    unit="{violation}",
    description="What the compliance check found wrong, by kind.",
)
ESCALATIONS = _meter.create_counter(
    "collection.escalations",
    unit="{event}",
    description="Conversations handed to a person, by reason.",
)
VOICE_TURN_DURATION = _meter.create_histogram(
    "collection.voice.turn.duration",
    unit="s",
    description="One call turn, split by stage: endpointing, stt, agent, tts_first_audio, total.",
    explicit_bucket_boundaries_advisory=_SECONDS,
)

_state: dict[str, Any] = {"enabled": False}


def enabled() -> bool:
    return bool(_state["enabled"])


def endpoint() -> str:
    """The collector's base URL, or empty when telemetry is off."""
    return (settings.otel_exporter_otlp_endpoint or "").strip().rstrip("/")


def setup(service_name: str, *, span_exporter=None, metric_reader=None) -> bool:
    """Install the SDK and export to the collector; True when telemetry is on.

    Idempotent, because both servers call it from their app factory and an app can be
    built more than once in a process (uvicorn's reload, the tests). The providers are
    process-global in OpenTelemetry and can be set exactly once.

    `span_exporter` and `metric_reader` are for the tests: given, they replace the OTLP
    exporters and turn telemetry on without a collector, and logs are left alone.
    """
    if _state["enabled"]:
        return True
    url = endpoint()
    testing = span_exporter is not None or metric_reader is not None
    if not url and not testing:
        return False
    try:
        _install(service_name, url, span_exporter, metric_reader)
    except ImportError as error:
        # The endpoint is set but the `otel` extra is not installed: say so once and run
        # without telemetry, rather than refusing to serve over a missing dashboard.
        logger.warning("telemetry is configured but unavailable (uv sync --extra otel): %s", error)
        return False
    _state["enabled"] = True
    return True


def _install(service_name: str, url: str, span_exporter, metric_reader) -> None:
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    # The stable HTTP conventions (`http.server.request.duration` in seconds, with
    # `http.route` and `http.response.status_code`) rather than the instrumentation's
    # older default; the dashboards are written against these names. Set before the first
    # instrumentor reads it, and only if the deployment has not chosen otherwise.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

    environment = settings.deployment_environment
    resource = Resource.create(
        {
            # OTEL_SERVICE_NAME wins, as everywhere in OpenTelemetry; the default is the
            # server's own name, so a process run by hand is still told apart.
            "service.name": os.environ.get("OTEL_SERVICE_NAME") or service_name,
            "service.version": settings.app_version,
            # Both spellings: `deployment.environment` is what the dashboards filter on,
            # `.name` is the current convention and costs nothing to send as well.
            "deployment.environment": environment,
            "deployment.environment.name": environment,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is not None:
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{url}/v1/traces"))
        )
    trace.set_tracer_provider(tracer_provider)

    if metric_reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        # 15 s: Prometheus' `rate()` over a 1-minute window needs a few samples in it.
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{url}/v1/metrics"), export_interval_millis=15_000
        )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    # The LLM's openai-protocol client. The exporters use requests too, and suppress their
    # own instrumentation, so the export does not trace itself.
    RequestsInstrumentor().instrument()

    if url and span_exporter is None:
        _install_logs(resource, url)


def _install_logs(resource, url: str) -> None:
    """Ship log records to the collector, each stamped with the trace it was written in.

    Handlers go on the root logger, not on any one module's, so every `collection.*`
    logger reaches the collector without being listed here -- including ones added later,
    such as the call server's network-check outcomes on `collection.call`. `collection` is
    lowered to INFO for the same reason; everything else stays at WARNING, so a library's
    chatter does not fill the log store.

    A handler on the root logger switches off Python's last-resort handler, which is what
    printed warnings to stderr until now. One is added back at the same level, so the
    container log shows what it showed before, now with the trace id.
    """
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{url}/v1/logs"))
    )
    set_logger_provider(provider)
    # No `inject_trace_context`: the exported record carries its trace id natively, and
    # injecting it as well would store it a second time as a log attribute.
    LoggingInstrumentor().instrument(log_handler_level=logging.INFO)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(_TraceFormatter())
    logging.getLogger().addHandler(stderr)
    app_logger = logging.getLogger("collection")
    if app_logger.level == logging.NOTSET or app_logger.level > logging.INFO:
        app_logger.setLevel(logging.INFO)


class _TraceFormatter(logging.Formatter):
    """`LEVEL logger [trace_id=...] message`, the id read from the span the record is in.

    Read at format time, which is still the thread that logged, rather than written onto
    the record, which every other handler would then see and export.
    """

    def __init__(self) -> None:
        super().__init__("%(levelname)s %(name)s [trace_id=%(trace_id)s] %(message)s")

    def formatMessage(self, record: logging.LogRecord) -> str:
        context = trace.get_current_span().get_span_context()
        trace_id = format(context.trace_id, "032x") if context.is_valid else "-"
        return self._style._fmt % {**record.__dict__, "trace_id": trace_id}


def instrument_app(app, service_name: str) -> None:
    """Trace and measure every request to a FastAPI app, when telemetry is on.

    `/health` is left out: the container health check calls it every fifteen seconds, and
    those requests would be most of the traces and none of the interest.
    """
    if not setup(service_name):
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health$")


def instrument_engine(engine) -> None:
    """Trace the queries of one SQLAlchemy engine, when telemetry is on.

    Per engine rather than by patching `create_engine`: `db.session` binds that name at
    import, before any server has called `setup`, so a patch would never be seen.
    """
    if not enabled():
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """A span named for a step, with attributes that are never None."""
    with tracer.start_as_current_span(name, attributes=_clean(attributes)) as current:
        yield current


@contextmanager
def timed(histogram, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Record how long the block took on `histogram`, in seconds.

    Yields the attribute dict so the block can add what it only learns at the end -- the
    outcome, the model that answered.
    """
    started = time.perf_counter()
    try:
        yield attributes
    finally:
        histogram.record(time.perf_counter() - started, _clean(attributes))


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attributes.items() if value is not None}


def describe_responder(responder: object) -> dict[str, str]:
    """Provider and model behind a responder, for attributes; empty for the template."""
    client = getattr(responder, "client", None)
    if client is None:
        return {}
    return {
        "gen_ai.provider.name": str(getattr(client, "name", "unknown")),
        "gen_ai.request.model": str(getattr(client, "model", "unknown")),
    }
