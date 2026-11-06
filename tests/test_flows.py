"""The three flows, end to end, through the orchestrator.

The module tests pin each piece where it lives: the rules engine in `test_rules.py`, the
composer and its compliance check in `test_outreach.py`, seeding in `test_channels.py`,
the handover in `test_handover.py`. None of them shows that the pieces still meet. These
do, one flow per section, each starting from the event that opens it and ending at the
state a customer or an operator would see:

1. **Proactive.** A detected receivable goes through the rules engine, is written by the
   composer, passes the compliance check and waits in the customer's thread -- or is
   stopped, with a reason, by the rule that applies: the time window, an opt-out at
   scheduling time, an opt-out between scheduling and delivery, a message that fails
   compliance.
2. **Responsive.** A customer message is classified, answered from the knowledge base and
   tracked, or handed to a person when the agent should not answer it.
3. **Escalation handoff.** A thread the agent handed over reaches the operators' queue on
   the durable record, is claimed by a named person and is resolved, which is the only
   exit an escalated thread has.

The last section is the external-service seams. The language model and the speech
recogniser are the two services a turn can depend on, and what the customer gets when
either fails is a decision, not an accident: a template answer when generation fails, a
person when recognition does.
"""

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from collection.agents.events import EventType
from collection.agents.responsive import TemplateResponder
from collection.channels import outbound
from collection.channels.delivery import (
    ConversationDeliveries,
    DatabaseDeliveryLog,
    DeliveryStatus,
)
from collection.channels.handover import AGENT, claim, queued, resolve
from collection.channels.session import DatabaseSessionStore, SessionNotFound, issue_token
from collection.channels.web import ChatChannel
from collection.db.models import (
    Conversation,
    ConversationState,
    InternalUser,
    Tenant,
    TenantState,
)
from collection.db.repository import TenantScope
from collection.llm.client import Completion, LLMClient, LLMError
from collection.llm.responder import LLMResponder
from collection.outreach.brief import Brief
from collection.outreach.compliance import check
from collection.outreach.compose import Composer, OutboundMessage
from collection.voice.transcribe import ScriptedTranscriber, Transcriber, TranscriptionError

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
VOICE_NOTE = Path(__file__).parent / "fixtures" / "voice-note.ogg"
QUESTION = "quais os juros por atraso?"
DISPUTE = "esse valor ta errado, nao e isso que eu devo"
OPT_OUT = "parem de me mandar mensagem"


@pytest.fixture
def channel(index):
    return ChatChannel(retriever=index, responder=TemplateResponder())


def _scheduled_for(result: outbound.SeedResult, customer_id: str):
    return next(event for event, _ in result.seeded if event.customer_id == customer_id)


def open_as(channel: ChatChannel, customer_id: str):
    return channel.open(token=issue_token(customer_id))


# ======================================================================== 1. proactive
def test_a_detected_receivable_becomes_a_compliant_message_waiting_in_the_thread(channel):
    result = outbound.run(channel)

    scheduled = _scheduled_for(result, outbound.SEED_DEMO_CUSTOMER)
    delivery_id = dict((e.customer_id, d) for e, d in result.seeded)[outbound.SEED_DEMO_CUSTOMER]
    # The rules engine allowed it, the composer wrote it, and what was scheduled passes the
    # same check the agent applied before scheduling it.
    brief = Brief(customer_id="cus_a", amount=1250.0, days_late=12, customer_name="Marina")
    assert check(scheduled.payload["message"], brief).sendable
    assert scheduled.payload["delinquency_stage"] == "8-30"
    assert scheduled.payload["channel"] == "chat"

    session, _ = open_as(channel, outbound.SEED_DEMO_CUSTOMER)
    thread = channel.transcript(session.session_id)
    assert thread[-1]["text"] == scheduled.payload["message"]
    assert thread[-1]["delivery_id"] == delivery_id
    assert channel.delivery.of(delivery_id).status is DeliveryStatus.ENVIADA


def test_a_customer_replying_to_a_contact_is_answered_by_the_responsive_agent(channel):
    """The proactive flow hands over to the responsive one at the customer's first reply."""
    result = outbound.run(channel)
    delivery_id = dict((e.customer_id, d) for e, d in result.seeded)["cus_a"]
    session, _ = open_as(channel, "cus_a")

    reply = channel.send(session.session_id, QUESTION)

    assert "reply_sent" in reply.events
    assert reply.citations, "answered from the knowledge base"
    assert channel.delivery.of(delivery_id).status is DeliveryStatus.RESPONDIDA


def test_the_rules_engine_stops_a_contact_outside_the_legal_window(channel, monkeypatch):
    # 05:00 local: inside no permitted window, whatever the day.
    monkeypatch.setattr(outbound, "DEMO_CLOCK", datetime(2026, 9, 10, 5, 0, tzinfo=SAO_PAULO))

    result = outbound.run(channel)

    assert result.seeded == []
    reasons = {e.payload.get("reason") for e in result.suppressed if e.customer_id != "cus_optout"}
    assert reasons == {"outside_window"}
    retry = [e.payload["retry_at"] for e in result.suppressed if e.payload.get("retry_at")]
    assert retry, "a blocked attempt says when it would become allowed"
    session, _ = open_as(channel, "cus_a")
    assert len(channel.transcript(session.session_id)) == 1, "only the greeting"


def test_an_opted_out_customer_is_suppressed_before_any_message_is_written(channel):
    result = outbound.run(channel)

    blocked = [e for e in result.suppressed if e.customer_id == "cus_optout"]
    # The overdue event itself, not a composed contact: the orchestrator suppressed it
    # before the proactive agent ran, so no message for this customer was ever written.
    assert [e.type for e in blocked] == [EventType.OVERDUE_DETECTED]
    assert "cus_optout" not in {e.customer_id for e, _ in result.seeded}
    assert result.total == len(outbound.overdue())


def test_an_opt_out_between_scheduling_and_delivery_stops_the_waiting_message(channel):
    # The customer already has a thread open when the job runs, and opts out in it.
    earlier, _ = open_as(channel, "cus_a")
    result = outbound.run(channel)
    delivery_id = dict((e.customer_id, d) for e, d in result.seeded)["cus_a"]

    channel.send(earlier.session_id, OPT_OUT)
    fresh, _ = open_as(channel, "cus_a")

    assert "cus_a" in channel.opted_out
    assert len(channel.transcript(fresh.session_id)) == 1, "only the greeting"
    assert channel.delivery.of(delivery_id).status is DeliveryStatus.ENVIADA, "never shown"
    # And the next run of the job does not schedule them again.
    again = outbound.run(channel)
    assert "cus_a" not in {e.customer_id for e, _ in again.seeded}


class _Overreaching(Composer):
    """A composer that threatens and invents a figure: what the compliance gate is for."""

    name = "overreaching"

    def compose(self, brief: Brief) -> OutboundMessage:
        return OutboundMessage(
            text="Pague R$ 9.999,00 hoje ou seu nome vai para a justiça.",
            composer=self.name,
            stage=brief.stage,
            channel=brief.channel,
        )


def test_a_message_that_fails_compliance_goes_to_a_person_not_the_customer(channel, monkeypatch):
    monkeypatch.setattr("collection.agents.proactive.TemplateComposer", _Overreaching)

    result = outbound.run(channel)

    assert result.seeded == []
    assert {e.payload["reason"] for e in result.escalated} == {"message_not_compliant"}
    assert all(e.payload["composer"] == "overreaching" for e in result.escalated)
    session, _ = open_as(channel, "cus_a")
    assert len(channel.transcript(session.session_id)) == 1


# ======================================================================= 2. responsive
def test_a_question_is_classified_answered_from_the_knowledge_base_and_tracked(channel):
    session, _ = channel.open()

    reply = channel.send(session.session_id, QUESTION)

    assert not reply.escalated
    assert reply.intent is not None
    assert reply.citations
    assert reply.events == ["reply_sent"]
    # The schedule's latency requirement for a text turn, on the arm that needs no network.
    assert reply.latency_ms < 5000
    assert channel.delivery.of(reply.delivery_id).status is DeliveryStatus.ENVIADA
    roles = [turn["role"] for turn in channel.transcript(session.session_id)]
    assert roles == ["agent", "customer", "agent"]


def test_a_dispute_is_handed_to_a_person_and_the_thread_is_marked(channel):
    session, _ = channel.open()

    reply = channel.send(session.session_id, DISPUTE)

    assert reply.escalated
    assert reply.escalation_reason == "dispute"
    assert channel.store.get(session.session_id).escalated


def test_a_message_nothing_answers_is_never_guessed_at(channel):
    """No passage, no answer: each one goes to a person, and the third says it is a pattern."""
    session, _ = channel.open()

    replies = [channel.send(session.session_id, "asdfgh qwerty zxcvb") for _ in range(3)]

    assert all(r.escalated for r in replies)
    assert [r.escalation_reason for r in replies] == [
        "no_grounding",
        "no_grounding",
        "unclear_repeatedly",
    ]


def test_an_opt_out_typed_in_chat_suppresses_the_next_proactive_contact(channel):
    """The two flows share one opt-out set: what the customer says in one binds the other."""
    session, _ = open_as(channel, "cus_a")
    reply = channel.send(session.session_id, OPT_OUT)
    assert reply.opted_out

    result = outbound.run(channel)

    assert "cus_a" not in {e.customer_id for e, _ in result.seeded}
    assert "cus_a" in {e.customer_id for e in result.suppressed}


# =============================================================== 3. escalation handoff
OPERATOR = "ana@cobranca.example"


def _durable(db: Session, index) -> tuple[ChatChannel, TenantScope]:
    tenant = Tenant(name="flows", slug=f"flows-{uuid.uuid4().hex[:6]}", state=TenantState.ACTIVE)
    db.add(tenant)
    db.flush()
    scope = TenantScope(
        Session(bind=db.connection(), join_transaction_mode="create_savepoint"), tenant.id
    )
    store = DatabaseSessionStore(scope)
    channel = ChatChannel(
        retriever=index,
        responder=TemplateResponder(),
        store=store,
        delivery=ConversationDeliveries(DatabaseDeliveryLog(scope), store),
    )
    scope.add(InternalUser(email=OPERATOR, password_hash="x", role="operator", active=True))
    scope.session.flush()
    return channel, scope


@pytest.mark.db
def test_an_escalated_chat_reaches_the_queue_is_claimed_and_is_resolved(db, index):
    channel, scope = _durable(db, index)
    session, _ = channel.open(token=issue_token("cus_dispute"))

    reply = channel.send(session.session_id, DISPUTE)
    assert reply.escalated
    thread = uuid.UUID(session.session_id)

    # The agent let go of it: it waits for a person.
    assert [c.id for c in queued(scope)] == [thread]
    conversation = scope.get(Conversation, thread)
    assert conversation.state is ConversationState.ESCALATED
    assert conversation.escalation_reason == "dispute"

    # A named operator takes it, and it leaves the queue.
    assert claim(scope, thread, email=OPERATOR).holder == OPERATOR
    assert queued(scope) == []

    # Resolving is the exit: the thread closes, and keeps who handled it.
    closed = resolve(scope, thread)
    assert closed.state is ConversationState.CLOSED
    assert closed.holder == OPERATOR
    assert closed.closed_at is not None

    # Another process cannot resume a resolved thread; the customer starts a new one, and
    # the agent answers it again.
    with pytest.raises(SessionNotFound):
        DatabaseSessionStore(scope).get(session.session_id)
    fresh, _ = channel.open(token=issue_token("cus_dispute"))
    assert fresh.session_id != session.session_id
    assert scope.get(Conversation, uuid.UUID(fresh.session_id)).holder == AGENT


# ============================================================== external-service seams
class _Down(LLMClient):
    """The provider is unreachable: expired key, rate limit, timeout -- all one error."""

    name = "down"
    model = "down"

    def complete(self, system: str, user: str) -> Completion:
        raise LLMError("APITimeoutError: request timed out")


class _Answers(LLMClient):
    name = "answers"
    model = "answers"

    def complete(self, system: str, user: str) -> Completion:
        return Completion(text="Os juros são de 1% ao mês.", model=self.model, latency_ms=1.0)


def test_a_model_outage_still_answers_the_customer_from_the_template(index):
    responder = LLMResponder(client=_Down())
    channel = ChatChannel(retriever=index, responder=responder)
    session, _ = channel.open()

    reply = channel.send(session.session_id, QUESTION)

    assert not reply.escalated, "an outage is not the customer's problem to wait out"
    assert reply.citations
    assert responder.failures == ["APITimeoutError: request timed out"]


def test_a_working_model_writes_the_answer_the_customer_sees(index):
    channel = ChatChannel(retriever=index, responder=LLMResponder(client=_Answers()))
    session, _ = channel.open()

    reply = channel.send(session.session_id, QUESTION)

    assert reply.text == "Os juros são de 1% ao mês."
    assert reply.citations, "credited to the passages the model was shown"


class _Crashing(Transcriber):
    name = "crashing"

    def transcribe(self, clip):
        raise TranscriptionError("RuntimeError: model weights missing")

    def transcribe_path(self, path):
        raise TranscriptionError("RuntimeError: model weights missing")


def test_a_recogniser_that_fails_hands_the_voice_note_to_a_person(index):
    channel = ChatChannel(retriever=index, transcriber=_Crashing())
    session, _ = channel.open()

    reply = channel.send_audio(session.session_id, VOICE_NOTE)

    assert reply.escalated
    assert reply.escalation_reason == "audio_unintelligible"


def test_a_recogniser_that_is_unsure_hands_the_voice_note_to_a_person(index):
    unsure = ScriptedTranscriber(transcripts={"voice-note": QUESTION}, confidence=0.2)
    channel = ChatChannel(retriever=index, transcriber=unsure)
    session, _ = channel.open()

    reply = channel.send_audio(session.session_id, VOICE_NOTE)

    assert reply.escalated
    assert reply.escalation_reason == "audio_unintelligible"
    # The guess travels to the person, but the thread keeps what is known: a voice note.
    assert reply.transcript == QUESTION
    assert channel.transcript(session.session_id)[1]["text"] != QUESTION


def test_a_voice_note_the_recogniser_hears_is_answered_like_typed_text(index):
    heard = ScriptedTranscriber(transcripts={"voice-note": QUESTION})
    channel = ChatChannel(retriever=index, transcriber=heard)
    session, _ = channel.open()

    reply = channel.send_audio(session.session_id, VOICE_NOTE)

    assert not reply.escalated
    assert reply.transcript == QUESTION
    assert reply.citations
