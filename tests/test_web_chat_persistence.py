"""The chat channel on the durable record: the same conversation, kept in rows.

`tests/test_web_chat.py` pins what the customer sees, against the in-memory store. These
are the properties only rows can have: a transcript that outlives the process that wrote
it, the evidence behind each answer written at the moment it was retrieved, and the
tenant gate -- a tenant that is not `ACTIVE` gets no conversation and no scheduled
contact (spec 6.2).

The embedder is replaced with a deterministic bag of words, as in `test_retriever.py`, so
a test asserts retrieval and not a download.
"""

import hashlib
import re
import time
import uuid

import numpy as np
import pytest
from sqlalchemy.orm import Session

from collection.agents.events import Event, EventType
from collection.agents.responsive import TemplateResponder
from collection.channels.delivery import (
    ConversationDeliveries,
    DatabaseDeliveryLog,
    DeliveryStatus,
)
from collection.channels.session import (
    SESSION_TTL_SECONDS,
    DatabaseSessionStore,
    TenantNotServable,
    UnknownTenant,
    issue_token,
    resolve_tenant,
)
from collection.channels.web import GREETING, ChatChannel
from collection.config import settings
from collection.db.models import (
    Conversation,
    ConversationState,
    Customer,
    Delivery,
    Tenant,
    TenantState,
    Turn,
    TurnRetrieval,
)
from collection.db.repository import TenantScope
from collection.domain.intent import Intent
from collection.rag.embeddings import Embedder
from collection.rag.retriever import PgVectorRetriever
from collection.rag.store import publish_document

#: Matches the negotiation cues strongly enough to classify, and shares its words with the
#: document published below so retrieval has something to find.
QUESTION = "posso parcelar a divida"
BODY = "Posso parcelar a divida em ate doze vezes sem juros, com entrada de vinte por cento."


class BagOfWords(Embedder):
    """Deterministic and offline: each word hashes to one dimension, so overlap is cosine."""

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


@pytest.fixture(autouse=True)
def offline_embedder(monkeypatch):
    monkeypatch.setattr("collection.rag.store.get_embedder", lambda *a: BagOfWords())
    monkeypatch.setattr("collection.rag.retriever.get_embedder", lambda *a: BagOfWords())


def _tenant(db: Session, slug: str, state: TenantState = TenantState.ACTIVE) -> Tenant:
    tenant = Tenant(name=slug, slug=slug, state=state)
    db.add(tenant)
    db.flush()
    return tenant


def _scope(db: Session, tenant: Tenant) -> TenantScope:
    return TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant.id
    )


def _channel(scope: TenantScope) -> ChatChannel:
    """The channel as `ChatService` builds it for a tenant: every part on the record."""
    store = DatabaseSessionStore(scope)
    return ChatChannel(
        retriever=PgVectorRetriever(scope),
        responder=TemplateResponder(),
        store=store,
        delivery=ConversationDeliveries(DatabaseDeliveryLog(scope), store),
    )


def _serving(db: Session, slug: str = "t", state: TenantState = TenantState.ACTIVE):
    scope = _scope(db, _tenant(db, slug, state))
    return _channel(scope), scope


@pytest.mark.db
def test_a_transcript_survives_a_new_store_over_the_same_database(db):
    channel, scope = _serving(db)
    session, greeting = channel.open()
    reply = channel.send(session.session_id, QUESTION)

    # A second stack over the same rows, which is all a restarted process is.
    second = _channel(_scope(db, scope.session.get(Tenant, scope.tenant_id)))
    turns = second.transcript(session.session_id)

    assert [turn["role"] for turn in turns] == ["agent", "customer", "agent"]
    assert turns[0]["text"] == greeting == GREETING
    assert turns[1]["text"] == QUESTION
    assert turns[2]["text"] == reply.text
    # The delivery ids come back too: a widget redrawing the thread must still be able to
    # report on every agent bubble it draws.
    assert turns[0]["delivery_id"] == session.greeting_delivery_id
    assert turns[2]["delivery_id"] is not None


@pytest.mark.db
def test_opening_a_session_for_a_non_active_tenant_raises_tenant_not_servable(db):
    for state in (
        TenantState.PROVISIONED,
        TenantState.INGESTING,
        TenantState.READY,
        TenantState.SUSPENDED,
    ):
        channel, scope = _serving(db, slug=f"t-{state.value}", state=state)

        with pytest.raises(TenantNotServable):
            channel.open()

        # Refused before anything was written: no half-open conversation to find later.
        assert scope.all(Conversation) == []
        assert scope.all(Delivery) == []


@pytest.mark.db
def test_an_answer_writes_a_customer_turn_an_agent_turn_and_its_retrieval_rows(db):
    channel, scope = _serving(db)
    version = publish_document(scope, title="Parcelamento", path="faq/parcelar.md", body=BODY)
    session, _ = channel.open()

    reply = channel.send(session.session_id, QUESTION)

    assert not reply.escalated
    turns = sorted(scope.all(Turn), key=lambda row: row.created_at)
    assert [(row.role, row.text) for row in turns] == [
        ("agent", GREETING),
        ("customer", QUESTION),
        ("agent", reply.text),
    ]
    customer, agent = turns[1], turns[2]
    # The intent is only known once the agent has run; the customer's turn carries it too,
    # exactly as the in-memory transcript does.
    assert customer.intent is Intent.NEGOTIATION is agent.intent
    assert agent.responder == TemplateResponder.name
    assert agent.latency_ms is not None

    rows = sorted(scope.all(TurnRetrieval), key=lambda row: row.rank)
    assert rows, "the answer quoted a passage, so the evidence must be on the record"
    assert all(row.turn_id == agent.id for row in rows)
    assert [row.rank for row in rows] == list(range(1, len(rows) + 1))
    assert {row.version_id for row in rows} == {version.id}
    assert all(0.0 < row.score <= 1.0 for row in rows)


@pytest.mark.db
def test_an_unknown_tenant_key_is_refused(db):
    tenant = _tenant(db, "known")

    assert resolve_tenant(tenant.tenant_key, session=db).id == tenant.id
    with pytest.raises(UnknownTenant):
        resolve_tenant("nao-existe", session=db)
    # An empty key is not a wildcard that resolves to the first tenant.
    with pytest.raises(UnknownTenant):
        resolve_tenant("", session=db)


@pytest.mark.db
def test_the_proactive_agent_schedules_nothing_for_a_non_active_tenant(db):
    contact = Event(
        type=EventType.CONTACT_SCHEDULED,
        customer_id="C1",
        payload={"message": "Sua fatura venceu ontem.", "channel": "chat"},
    )
    refused, refused_scope = _serving(db, slug="asleep", state=TenantState.READY)

    assert refused.seed(contact) is None
    assert refused.skipped == [("tenant_not_servable", contact)]
    assert refused_scope.all(Delivery) == []
    assert refused_scope.all(Conversation) == []
    assert refused._mailbox == {}

    # The same contact, for a tenant that is servable: the gate is the state, not the event.
    serving, scope = _serving(db, slug="awake")
    assert serving.seed(contact) is not None
    assert len(scope.all(Delivery)) == 1


@pytest.mark.db
def test_reopening_the_widget_starts_a_new_thread_rather_than_resuming_the_old_one(db):
    channel, scope = _serving(db)
    first, _ = channel.open(issue_token("C7"))
    channel.send(first.session_id, QUESTION)

    second, _ = channel.open(issue_token("C7"))

    assert second.session_id != first.session_id
    # The greeting is shown once, at the top of a thread. Resuming would put it in the
    # middle of a restored transcript, feed the old thread back to the agent, and count
    # the turn cap across reopenings.
    assert [turn["text"] for turn in channel.transcript(second.session_id)] == [GREETING]
    assert len(channel.transcript(first.session_id)) == 3
    assert len(scope.all(Conversation)) == 2


@pytest.mark.db
def test_an_abandoned_session_is_swept_and_its_thread_closed(db):
    channel, scope = _serving(db)
    stale, _ = channel.open()
    stale.last_seen_at = time.time() - SESSION_TTL_SECONDS - 1

    channel.open()  # a write sweeps

    assert len(channel.store) == 1
    assert scope.get(Conversation, uuid.UUID(stale.session_id)).state is ConversationState.CLOSED


@pytest.mark.db
def test_a_seeded_contact_is_shown_in_the_conversation_it_is_recorded_against(db):
    message = "Sua fatura venceu ontem."
    channel, scope = _serving(db)
    contact = Event(
        type=EventType.CONTACT_SCHEDULED,
        customer_id="C9",
        payload={"message": message, "channel": "chat"},
    )

    delivery_id = channel.seed(contact)
    session, _ = channel.open(issue_token("C9"))

    # Seeded before any session existed, and drained into the first one that opens.
    assert [turn["text"] for turn in channel.transcript(session.session_id)] == [GREETING, message]
    row = scope.get(Delivery, uuid.UUID(delivery_id))
    assert str(row.conversation_id) == session.session_id
    assert row.owner == session.session_id
    # The thread opened only to hold the contact is closed once the message has left it,
    # rather than staying open for a conversation that will never happen there.
    seeded = next(t for t in scope.all(Conversation) if str(t.id) != session.session_id)
    assert seeded.state is ConversationState.CLOSED


@pytest.mark.db
def test_a_customers_reply_advances_the_greeting_delivery_to_respondida(db):
    channel, scope = _serving(db)
    session, _ = channel.open()
    assert channel.delivery.of(session.greeting_delivery_id).status is DeliveryStatus.ENVIADA

    channel.send(session.session_id, QUESTION)

    greeting = scope.get(Delivery, uuid.UUID(session.greeting_delivery_id))
    assert greeting.status is DeliveryStatus.RESPONDIDA
    assert greeting.replied_at is not None
    # Every delivery this turn produced belongs to the conversation it was shown in.
    assert {row.conversation_id for row in scope.all(Delivery)} == {session.conversation.id}


@pytest.mark.db
def test_an_anonymous_visitor_and_an_identified_one_each_get_their_own_conversation(db):
    channel, scope = _serving(db)

    anonymous, _ = channel.open()
    known, _ = channel.open(issue_token("C7"))

    assert anonymous.session_id != known.session_id
    assert anonymous.identified is False
    assert known.identified is True
    assert known.customer_id == "C7"
    assert len(scope.all(Conversation)) == 2
    assert {row.external_id for row in scope.all(Customer)} == {anonymous.customer_id, "C7"}
    assert all(row.channel == "chat" for row in scope.all(Conversation))
    assert all(row.state is ConversationState.OPEN for row in scope.all(Conversation))

    # Read back by a store that never opened them, identity is derived from the customer
    # the thread belongs to. Getting that wrong would quietly de-identify a real customer
    # or, worse, promote a visitor.
    restored = _channel(_scope(db, scope.session.get(Tenant, scope.tenant_id))).store
    assert restored.get(known.session_id).identified is True
    assert restored.get(known.session_id).customer_id == "C7"
    assert restored.get(anonymous.session_id).identified is False
