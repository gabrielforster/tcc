"""Responsive agent: intent classification, escalation and grounded answering.

The property that matters most here is that the agent **escalates rather than guesses**.
A collection system that answers a disputed debt confidently, or invents an instalment
plan, does more damage than one that hands over to a person — so the escalation paths get
more tests than the happy path.
"""

import json

import pytest

from collection.agents.events import Event, EventType
from collection.agents.intent import Intent, IntentClassifier
from collection.agents.intent import evaluate as evaluate_intents
from collection.agents.responsive import (
    UNCLEAR_LIMIT,
    Answer,
    Responder,
    ResponsiveAgent,
    TemplateResponder,
)
from collection.config import settings
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex


@pytest.fixture(scope="module")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()


@pytest.fixture
def agent(index):
    return ResponsiveAgent(index=index)


@pytest.fixture(scope="module")
def classifier():
    return IntentClassifier()


def message_event(text: str, customer: str = "cus_1") -> Event:
    return Event(type=EventType.MESSAGE_RECEIVED, customer_id=customer, payload={"text": text})


# --------------------------------------------------------------------- intents
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("quanto eu to devendo?", Intent.DEBT_INQUIRY),
        ("me manda a segunda via por favor", Intent.SECOND_COPY),
        ("consigo parcelar em 6 vezes?", Intent.NEGOTIATION),
        ("esse valor ta errado, nao e isso que eu devo", Intent.DISPUTE),
        ("ja paguei esse boleto semana passada", Intent.PAYMENT_CLAIM),
        ("parem de me mandar mensagem", Intent.OPT_OUT),
    ],
)
def test_each_intent_is_recognised(classifier, text, expected):
    assert classifier.classify(text).intent is expected


def test_an_empty_message_is_unknown_rather_than_guessed(classifier):
    assert classifier.classify("   ").intent is Intent.UNKNOWN


def test_a_greeting_is_small_talk_not_a_negotiation(classifier):
    """Without a small-talk intent, "bom dia, tudo bem?" matched the negotiation cue
    "nao tenho como pagar tudo agora" on the shared word."""
    assert classifier.classify("bom dia, tudo bem com voce?").intent is Intent.SMALL_TALK
    assert classifier.classify("obrigado!").intent is Intent.SMALL_TALK


def test_nonsense_gets_no_confident_intent(classifier):
    result = classifier.classify("asdf qwer zxcv")
    assert not result.is_confident


def test_confidence_is_a_margin_between_the_top_two_intents(classifier):
    """It must not be read as a probability, so it is tested as a margin."""
    result = classifier.classify("consigo parcelar em 6 vezes?")
    ranked = sorted(result.scores.values(), reverse=True)
    assert result.confidence == pytest.approx(ranked[0] - ranked[1], abs=1e-3)


def test_the_lexical_baseline_is_measured_not_asserted():
    """Records the baseline the LLM classifier will be compared against."""
    data = json.loads((settings.dir_knowledge / "messages.json").read_text(encoding="utf-8"))
    labelled = [(item["text"], Intent(item["intent"])) for item in data]
    result = evaluate_intents(IntentClassifier(), labelled)
    assert result["n"] >= 20
    # Measured at 0.913 when written; the floor guards against a regression, and the
    # number itself belongs in the paper as the baseline, not as a good result.
    assert result["accuracy"] >= 0.85


# ------------------------------------------------------------------ escalation
def test_a_disputed_debt_always_goes_to_a_human(agent):
    reply = agent.answer("eu nunca contratei esse servico")
    assert reply.escalated
    assert reply.escalation_reason == "dispute"
    assert reply.answer.citations == []


def test_a_dispute_escalates_before_any_answer_is_composed(agent):
    """The damage from a confident wrong answer is worst exactly here."""
    reply = agent.answer("esse valor ta errado")
    assert reply.passages == []


class _AlwaysRetrieves:
    """Index that always returns something relevant.

    Isolates the unclear-message counter: with the real index an unclear message usually
    retrieves nothing and escalates on `no_grounding` first, which would hide whether the
    counter works at all.
    """

    def __init__(self, index):
        self._hit = index.search("politica de cobranca", top_k=1)

    def search(self, query, top_k=3):
        return self._hit


@pytest.fixture
def counting_agent(index):
    return ResponsiveAgent(index=_AlwaysRetrieves(index))


def test_repeated_unclear_messages_hand_over_to_a_person(counting_agent):
    gibberish = "asdf qwer zxcv"
    for _ in range(UNCLEAR_LIMIT - 1):
        assert not counting_agent.answer(gibberish, customer_id="cus_9").escalated
    escalated = counting_agent.answer(gibberish, customer_id="cus_9")
    assert escalated.escalated
    assert escalated.escalation_reason == "unclear_repeatedly"


def test_one_understood_message_resets_the_unclear_counter(counting_agent):
    counting_agent.answer("asdf qwer zxcv", customer_id="cus_7")
    counting_agent.answer("quero a segunda via do boleto", customer_id="cus_7")
    assert not counting_agent.answer("asdf qwer zxcv", customer_id="cus_7").escalated


def test_the_unclear_counter_is_per_customer(counting_agent):
    for _ in range(UNCLEAR_LIMIT):
        counting_agent.answer("asdf qwer zxcv", customer_id="cus_a")
    assert not counting_agent.answer("asdf qwer zxcv", customer_id="cus_b").escalated


def test_an_answer_with_no_grounding_escalates_rather_than_guessing(index):
    class EmptyIndex:
        def search(self, query, top_k=3):
            return []

    agent = ResponsiveAgent(index=EmptyIndex())
    reply = agent.answer("posso parcelar?")
    assert reply.escalated
    assert reply.escalation_reason == "no_grounding"
    assert not reply.answer.grounded


# ------------------------------------------------------------------- answering
def test_an_answer_cites_the_documents_it_came_from(agent):
    reply = agent.answer("quero a segunda via do boleto")
    assert reply.answer.citations
    assert "Fonte:" in reply.answer.text
    assert all(citation in reply.answer.text for citation in reply.answer.citations)


def test_the_template_responder_only_repeats_retrieved_text(index):
    """It cannot hallucinate, because it does not generate."""
    responder = TemplateResponder()
    passages = index.search("parcelamento", top_k=2)
    answer = responder.respond("posso parcelar?", Intent.NEGOTIATION, passages)
    for passage in passages[:2]:
        assert " ".join(passage.text.split())[:60] in answer.text


def test_the_responder_is_replaceable_without_touching_retrieval(index):
    """The seam an LLM drops into: retrieval identical, generation swapped."""

    class ShoutingResponder(Responder):
        name = "shouting"

        def respond(self, message, intent, passages):
            return Answer(text="RESPOSTA", citations=[p.document_id for p in passages])

    agent = ResponsiveAgent(index=index, responder=ShoutingResponder())
    reply = agent.answer("quero a segunda via")
    assert reply.answer.text == "RESPOSTA"
    assert reply.passages  # retrieval still ran


def test_latency_is_measured_on_every_reply(agent):
    reply = agent.answer("quero a segunda via do boleto")
    assert reply.latency_ms > 0
    # The 5s target applies to the full LLM path; the lexical baseline is far under it.
    assert reply.latency_ms < 5000


# ----------------------------------------------------------------- as an agent
def test_a_message_produces_a_reply_event_carrying_its_citations(agent):
    produced = agent.handle(message_event("quero a segunda via do boleto"))[0]
    assert produced.type is EventType.REPLY_SENT
    assert produced.payload["citations"]
    assert produced.payload["intent"] == Intent.SECOND_COPY.value
    assert produced.payload["latency_ms"] > 0


def test_an_opt_out_message_becomes_an_opt_out_event(agent):
    produced = agent.handle(message_event("nao quero mais receber mensagens"))[0]
    assert produced.type is EventType.OPT_OUT_REQUESTED


def test_a_dispute_becomes_an_escalation_event(agent):
    produced = agent.handle(message_event("eu nunca contratei esse servico"))[0]
    assert produced.type is EventType.ESCALATED_TO_HUMAN
    assert produced.payload["reason"] == "dispute"


def test_an_empty_message_escalates_rather_than_being_answered(agent):
    produced = agent.handle(message_event("   "))[0]
    assert produced.type is EventType.ESCALATED_TO_HUMAN
    assert produced.payload["reason"] == "empty_message"


def test_the_agent_only_claims_the_inbound_event_types(agent):
    assert agent.accepts(message_event("oi"))
    assert not agent.accepts(Event(type=EventType.OVERDUE_DETECTED, customer_id="cus_1"))


def test_an_opt_out_through_the_orchestrator_stops_later_outbound_contact(index):
    """End to end: the customer asks to stop, and the campaign for them is suppressed."""
    from collection.agents.orchestrator import Orchestrator
    from collection.agents.proactive import ProactiveAgent

    orchestrator = Orchestrator([ResponsiveAgent(index=index), ProactiveAgent()])
    orchestrator.submit(message_event("parem de me mandar mensagem", customer="cus_1"))
    orchestrator.submit(
        Event(
            type=EventType.OVERDUE_DETECTED,
            customer_id="cus_1",
            payload={"days_late": 10, "amount": 100.0},
        )
    )
    trace = orchestrator.run()
    assert "cus_1" in orchestrator.opted_out
    assert any(event.type is EventType.OVERDUE_DETECTED for event in trace.suppressed)
