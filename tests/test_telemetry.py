"""Telemetry: nothing without a collector, and one connected trace with one.

The first test runs in a subprocess, because OpenTelemetry's providers are process-global
and settable once: after this module turns telemetry on, no test in the same process could
observe it off. The rest share in-memory exporters installed once for the module, which
is the same SDK the servers run, minus the network.

The embedder is a deterministic bag of words, as in `test_web_chat_persistence.py`, so the
chat tests assert the shape of a trace and not a model download.
"""

import hashlib
import os
import re
import subprocess
import sys
import textwrap

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy.orm import Session

from collection import telemetry
from collection.agents.responsive import TemplateResponder
from collection.api.app import ModelRegistry, create_app
from collection.api.chat import ChatService
from collection.channels.delivery import ConversationDeliveries, DatabaseDeliveryLog
from collection.channels.session import DatabaseSessionStore
from collection.channels.web import ChatChannel
from collection.config import settings
from collection.db.models import Tenant, TenantState
from collection.db.repository import TenantScope
from collection.domain.intent import Intent
from collection.llm.client import Completion, ScriptedClient
from collection.llm.responder import LLMResponder
from collection.rag.documents import Document, load_customer_facing
from collection.rag.embeddings import Embedder
from collection.rag.index import VectorIndex
from collection.rag.retriever import PgVectorRetriever
from collection.rag.store import publish_document

QUESTION = "posso parcelar a divida"
BODY = "Posso parcelar a divida em ate doze vezes sem juros, com entrada de vinte por cento."
MESSAGES_ROUTE = "/chat/sessions/{session_id}/messages"


class BagOfWords(Embedder):
    name = "bag-of-words"

    def fit(self, texts):
        return self

    def encode(self, texts):
        matrix = np.zeros((len(texts), settings.embedding_dimensions))
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                matrix[row, int.from_bytes(digest[:4], "big") % settings.embedding_dimensions] += 1
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.where(norms == 0, 1.0, norms)


def test_without_an_endpoint_nothing_is_installed():
    """No collector named: no provider, no handler, no middleware, no patched client."""
    script = textwrap.dedent(
        """
        import logging

        handlers = list(logging.getLogger().handlers)
        factory = logging.getLogRecordFactory()

        import requests
        from opentelemetry import metrics, trace

        from collection import telemetry
        from collection.api.app import ModelRegistry, create_app
        from collection.api.call import create_call_app

        api = create_app(registry=ModelRegistry(loader=dict))
        call = create_call_app(channel=object(), synthesizer=object())

        assert not telemetry.enabled()
        assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"
        assert type(metrics.get_meter_provider()).__name__ == "_ProxyMeterProvider"
        assert logging.getLogger().handlers == handlers
        assert logging.getLogRecordFactory() is factory
        assert logging.getLogger("collection").level == logging.NOTSET
        for app in (api, call):
            names = [m.cls.__name__ for m in app.user_middleware]
            assert "OpenTelemetryMiddleware" not in names, names
        assert not hasattr(requests.Session.send, "__wrapped__")
        print("untouched")
        """
    )
    env = {**os.environ, "OTEL_EXPORTER_OTLP_ENDPOINT": ""}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("untouched")


@pytest.fixture(scope="module")
def recorded():
    if telemetry.enabled():
        pytest.skip("telemetry was already set up in this process with other exporters")
    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    assert telemetry.setup("collection-test", span_exporter=spans, metric_reader=reader)
    return spans, reader


@pytest.fixture
def spans(recorded):
    exporter, _ = recorded
    exporter.clear()
    return exporter


@pytest.fixture
def reader(recorded):
    return recorded[1]


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    return [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _message_trace(exporter: InMemorySpanExporter) -> tuple[object, list]:
    """The server span of the message request, and every span in its trace."""
    finished = exporter.get_finished_spans()
    server = next(
        s
        for s in finished
        if s.kind is SpanKind.SERVER and s.attributes.get("http.route") == MESSAGES_ROUTE
    )
    return server, [s for s in finished if s.context.trace_id == server.context.trace_id]


def _ancestry(span, by_id: dict) -> list[str]:
    names = []
    while span.parent is not None and span.parent.span_id in by_id:
        span = by_id[span.parent.span_id]
        names.append(span.name)
    return names


def _send(app) -> None:
    with TestClient(app) as client:
        session = client.post("/chat/sessions", json={}).json()["session_id"]
        response = client.post(f"/chat/sessions/{session}/messages", json={"text": QUESTION})
        assert response.status_code == 200, response.text


def test_a_chat_message_is_one_trace_from_the_request_to_the_responder(spans):
    index = VectorIndex(embedder=BagOfWords())
    index.add_documents(load_customer_facing(settings.dir_knowledge)).build()
    channel = ChatChannel(retriever=index, responder=TemplateResponder())
    _send(create_app(registry=ModelRegistry(loader=dict), chat=ChatService(channel=channel)))

    server, trace = _message_trace(spans)
    by_id = {s.context.span_id: s for s in trace}
    named = {s.name: s for s in trace}
    for step in ("orchestrator.run", "agent.responsive", "retrieval", "responder"):
        assert step in named, sorted(named)
        assert server.name in _ancestry(named[step], by_id), step
    # Each step inside the one before it: the trace reads as the pipeline does.
    assert _ancestry(named["retrieval"], by_id)[:2] == ["agent.responsive", "orchestrator.run"]
    assert named["responder"].attributes["responder"] == "template"
    assert named["retrieval"].attributes["retriever"] == "VectorIndex"


def test_no_span_carries_the_message(spans):
    index = VectorIndex(embedder=BagOfWords())
    index.add_documents([Document(document_id="faq.md", text=BODY, source="faq.md")]).build()
    channel = ChatChannel(retriever=index, responder=TemplateResponder())
    _send(create_app(registry=ModelRegistry(loader=dict), chat=ChatService(channel=channel)))

    for span in spans.get_finished_spans():
        for value in span.attributes.values():
            assert QUESTION not in str(value) and BODY not in str(value), span.name


@pytest.fixture
def traced_engine(recorded, db_engine):
    """The suite's engine with query spans on, for one test, then off again."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    instrumentor = SQLAlchemyInstrumentor()
    instrumentor.instrument(engine=db_engine)
    yield db_engine
    instrumentor.uninstrument()


@pytest.mark.db
def test_a_chat_message_on_the_record_reaches_the_database_in_the_same_trace(
    traced_engine, db, spans, monkeypatch
):
    monkeypatch.setattr("collection.rag.store.get_embedder", lambda *a: BagOfWords())
    monkeypatch.setattr("collection.rag.retriever.get_embedder", lambda *a: BagOfWords())
    tenant = Tenant(name="otel", slug="otel", state=TenantState.ACTIVE)
    db.add(tenant)
    db.flush()
    scope = TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant.id
    )
    publish_document(scope, title="Parcelamento", path="faq.md", body=BODY)
    store = DatabaseSessionStore(scope)
    channel = ChatChannel(
        retriever=PgVectorRetriever(scope),
        responder=TemplateResponder(),
        store=store,
        delivery=ConversationDeliveries(DatabaseDeliveryLog(scope), store),
    )
    _send(create_app(registry=ModelRegistry(loader=dict), chat=ChatService(channel=channel)))

    server, trace = _message_trace(spans)
    by_id = {s.context.span_id: s for s in trace}
    queries = [s for s in trace if "db.system" in s.attributes or "db.system.name" in s.attributes]
    assert queries, sorted({s.name for s in trace})
    # The pgvector search is a query inside the retrieval span, inside the request.
    inside_retrieval = [q for q in queries if "retrieval" in _ancestry(q, by_id)]
    assert inside_retrieval
    assert server.name in _ancestry(inside_retrieval[0], by_id)


def _stub_bundle() -> dict:
    class Model:
        def predict_proba(self, frame):
            positive = np.asarray(frame["amount"], dtype=float) / 1000.0
            return np.column_stack([1 - positive, positive])

    return {
        "pipeline": Model(),
        "threshold": 0.3,
        "target": "target_payment_propensity",
        "task": "propensity",
        "source": "test",
        "champion": "telemetry_stub",
        "features": ["amount"],
    }


def test_scoring_records_the_batch_its_latency_and_the_model(spans, reader):
    registry = ModelRegistry(loader=lambda: {"propensity": _stub_bundle()})
    channel = ChatChannel(retriever=VectorIndex(embedder=BagOfWords()))
    with TestClient(create_app(registry=registry, chat=ChatService(channel=channel))) as client:
        rows = pd.DataFrame({"amount": [100.0, 500.0, 900.0]}).to_dict("records")
        response = client.post("/score", json={"task": "propensity", "receivables": rows})
        assert response.status_code == 200, response.text

    labels = {"task": "propensity", "model": "telemetry_stub"}
    batch = [p for p in _points(reader, "collection.scoring.batch_size") if p.attributes == labels]
    assert len(batch) == 1 and batch[0].count == 1 and batch[0].sum == 3
    latency = [p for p in _points(reader, "collection.scoring.duration") if p.attributes == labels]
    assert len(latency) == 1 and latency[0].count == 1 and latency[0].sum > 0
    predict = next(s for s in spans.get_finished_spans() if s.name == "scoring.predict")
    assert predict.attributes["batch_size"] == 3
    assert predict.attributes["model"] == "telemetry_stub"


def test_an_escalation_is_counted_by_reason(reader):
    index = VectorIndex(embedder=BagOfWords())
    index.add_documents([Document(document_id="faq.md", text=BODY, source="faq.md")]).build()
    channel = ChatChannel(retriever=index, responder=TemplateResponder())
    session, _ = channel.open()

    def disputes() -> float:
        return sum(
            p.value
            for p in _points(reader, "collection.escalations")
            if p.attributes.get("reason") == "dispute"
        )

    before = disputes()
    reply = channel.send(session.session_id, "nao reconheco essa divida, quero contestar")
    assert reply.escalation_reason == "dispute"
    assert disputes() == before + 1


def test_an_llm_answer_is_a_span_with_token_counts_and_a_failure_counts_a_fallback(spans, reader):
    class Counted(ScriptedClient):
        def complete(self, system, user):
            done = super().complete(system, user)
            return Completion(done.text, done.model, 0.0, input_tokens=120, output_tokens=30)

    index = VectorIndex(embedder=BagOfWords())
    index.add_documents([Document(document_id="faq.md", text=BODY, source="faq.md")]).build()
    passages = index.search(QUESTION)
    responder = LLMResponder(client=Counted(replies=["Pode sim. Fonte: (faq.md)"]))

    def fallbacks() -> float:
        return sum(
            p.value
            for p in _points(reader, "collection.responder.fallbacks")
            if p.attributes.get("reason") == "fallback"
        )

    responder.respond(QUESTION, Intent.NEGOTIATION, passages)
    call = next(s for s in spans.get_finished_spans() if s.name == "llm.complete")
    assert call.attributes["gen_ai.provider.name"] == "scripted"
    assert call.attributes["gen_ai.usage.input_tokens"] == 120
    tokens = {
        p.attributes["gen_ai.token.type"]: p.value for p in _points(reader, "collection.llm.tokens")
    }
    assert tokens["input"] >= 120 and tokens["output"] >= 30

    before = fallbacks()
    answer = responder.respond(QUESTION, Intent.NEGOTIATION, passages)  # out of replies
    assert answer.responder == "llm:fallback"
    assert fallbacks() == before + 1
