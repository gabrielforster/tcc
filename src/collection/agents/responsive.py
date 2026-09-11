"""The responsive agent: answers a customer, grounded in retrieved documents.

Replaces the last stub. The flow is deliberately explicit, because each step is separately
measurable and the paper needs them to be:

1. **classify** the message (intent + confidence margin);
2. **decide whether to escalate** — before answering, not after;
3. **retrieve** the passages that bear on the question;
4. **compose** an answer from those passages, citing them.

Step 4 is behind a `Responder` interface. The default composes a templated answer that
quotes what was retrieved and cites the source document; it invents nothing, which makes it
a safe default and an honest baseline. An LLM responder drops into the same seam, and the
comparison between them is a result the paper can report -- the retrieval stays identical,
so any difference is attributable to generation alone.

Escalation is checked *before* answering because the cases that need a human -- a disputed
debt, a discount beyond the approval limit, a message nobody understood -- are exactly the
ones where a confident automated answer does the most damage.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from collection.agents.base import Agent
from collection.agents.events import Event, EventType
from collection.agents.intent import Classification, Intent, IntentClassifier
from collection.rag.index import Retrieved, VectorIndex

#: Intents that always go to a human, per knowledge/limites-de-alcada.md.
ALWAYS_ESCALATE = frozenset({Intent.DISPUTE})
#: Answered directly; retrieval would only return something irrelevant to quote back.
SMALL_TALK_REPLY = "Olá! Posso ajudar com a sua pendência, um parcelamento ou a segunda via."
#: Consecutive unclear messages before handing over.
UNCLEAR_LIMIT = 3
#: Retrieved passages below this similarity are not worth quoting back.
MIN_RELEVANCE = 0.05
TOP_K = 3


@dataclass
class Answer:
    text: str
    citations: list[str] = field(default_factory=list)
    grounded: bool = True


class Responder(ABC):
    name: str

    @abstractmethod
    def respond(self, message: str, intent: Intent, passages: list[Retrieved]) -> Answer:
        """Compose an answer from the retrieved passages."""


class TemplateResponder(Responder):
    """Quotes what was retrieved and cites it. Invents nothing.

    The honest baseline: it cannot hallucinate because it does not generate. What it can do
    is answer awkwardly, which is the trade the comparison against an LLM will quantify.
    """

    name = "template"

    OPENERS = {
        Intent.DEBT_INQUIRY: "Sobre a sua pendência:",
        Intent.SECOND_COPY: "Sobre a segunda via:",
        Intent.NEGOTIATION: "Sobre as condições de parcelamento:",
        Intent.PAYMENT_CLAIM: "Sobre o pagamento informado:",
        Intent.DISPUTE: "Sobre a contestação:",
        Intent.OPT_OUT: "Sobre o seu pedido:",
        Intent.UNKNOWN: "Encontrei estas informações:",
    }

    def respond(self, message: str, intent: Intent, passages: list[Retrieved]) -> Answer:
        if not passages:
            return Answer(
                text=(
                    "Não encontrei essa informação na nossa base. Vou encaminhar para um atendente."
                ),
                citations=[],
                grounded=False,
            )
        opener = self.OPENERS.get(intent, self.OPENERS[Intent.UNKNOWN])
        body = "\n\n".join(" ".join(p.text.split()) for p in passages[:2])
        citations = list(dict.fromkeys(p.document_id for p in passages[:2]))
        return Answer(
            text=f"{opener}\n\n{body}\n\nFonte: {', '.join(citations)}",
            citations=citations,
            grounded=True,
        )


@dataclass
class Reply:
    answer: Answer
    classification: Classification
    passages: list[Retrieved]
    escalated: bool
    escalation_reason: str | None
    latency_ms: float


class ResponsiveAgent(Agent):
    name = "responsive"
    handles = frozenset({EventType.MESSAGE_RECEIVED, EventType.AUDIO_RECEIVED})

    def __init__(
        self,
        index: VectorIndex,
        classifier: IntentClassifier | None = None,
        responder: Responder | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.index = index
        self.classifier = classifier or IntentClassifier()
        self.responder = responder or TemplateResponder()
        self.clock = clock
        #: Consecutive messages we failed to understand, per customer.
        self._unclear: dict[str, int] = {}

    # ----------------------------------------------------------------- answering
    def answer(self, message: str, customer_id: str = "anonymous") -> Reply:
        started = self.clock()
        classification = self.classifier.classify(message)

        reason = self._escalation_reason(classification, customer_id)
        if reason is not None:
            return Reply(
                answer=Answer(
                    text="Vou encaminhar o seu atendimento para uma pessoa da equipe.",
                    grounded=False,
                ),
                classification=classification,
                passages=[],
                escalated=True,
                escalation_reason=reason,
                latency_ms=(self.clock() - started) * 1000,
            )

        # Reset only when we actually understood the message. Resetting unconditionally
        # would clear the counter that an unclear message had just incremented, and the
        # "three unclear messages in a row" rule could never fire.
        if classification.is_confident:
            self._unclear[customer_id] = 0

        if classification.intent is Intent.SMALL_TALK:
            return Reply(
                answer=Answer(text=SMALL_TALK_REPLY, citations=[], grounded=True),
                classification=classification,
                passages=[],
                escalated=False,
                escalation_reason=None,
                latency_ms=(self.clock() - started) * 1000,
            )

        passages = [
            hit for hit in self.index.search(message, top_k=TOP_K) if hit.score >= MIN_RELEVANCE
        ]
        answer = self.responder.respond(message, classification.intent, passages)

        return Reply(
            answer=answer,
            classification=classification,
            passages=passages,
            # Nothing relevant retrieved means a human, not a guess.
            escalated=not answer.grounded,
            escalation_reason="no_grounding" if not answer.grounded else None,
            latency_ms=(self.clock() - started) * 1000,
        )

    def _escalation_reason(self, classification: Classification, customer_id: str) -> str | None:
        if classification.intent in ALWAYS_ESCALATE:
            return "dispute"
        if not classification.is_confident:
            self._unclear[customer_id] = self._unclear.get(customer_id, 0) + 1
            if self._unclear[customer_id] >= UNCLEAR_LIMIT:
                return "unclear_repeatedly"
        return None

    # -------------------------------------------------------------------- agent
    def handle(self, event: Event) -> list[Event]:
        message = str(event.payload.get("text", "")).strip()
        if not message:
            return [event.caused(EventType.ESCALATED_TO_HUMAN, reason="empty_message")]

        reply = self.answer(message, customer_id=event.customer_id)

        if reply.classification.intent is Intent.OPT_OUT:
            return [event.caused(EventType.OPT_OUT_REQUESTED, source="responsive_agent")]

        if reply.escalated:
            return [
                event.caused(
                    EventType.ESCALATED_TO_HUMAN,
                    reason=reply.escalation_reason,
                    intent=reply.classification.intent.value,
                    confidence=reply.classification.confidence,
                )
            ]

        return [
            event.caused(
                EventType.REPLY_SENT,
                text=reply.answer.text,
                intent=reply.classification.intent.value,
                confidence=reply.classification.confidence,
                citations=reply.answer.citations,
                latency_ms=round(reply.latency_ms, 2),
            )
        ]
