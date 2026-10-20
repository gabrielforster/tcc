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
from collection.agents.conversation import (
    CONTEXT_WINDOW,
    Conversation,
    QueryRewriter,
    build_vocabulary,
    get_rewriter,
    resolve_intent,
)
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
    #: Which responder wrote this. Carried on the answer so an evaluation run cannot
    #: report a templated fallback under the LLM's name.
    responder: str = "template"


class Responder(ABC):
    name: str

    @abstractmethod
    def respond(
        self,
        message: str,
        intent: Intent,
        passages: list[Retrieved],
        history: Conversation | None = None,
    ) -> Answer:
        """Compose an answer from the retrieved passages, in the context of the thread."""


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

    def respond(
        self,
        message: str,
        intent: Intent,
        passages: list[Retrieved],
        history: Conversation | None = None,
    ) -> Answer:
        # The template quotes passages, so the thread changes nothing for it: history is
        # accepted to satisfy the interface and deliberately unused. That is also what
        # makes it the control arm for the multi-turn comparison.
        if not passages:
            return Answer(
                text=(
                    "Não encontrei essa informação na nossa base. Vou encaminhar para um atendente."
                ),
                citations=[],
                grounded=False,
                responder=self.name,
            )
        opener = self.OPENERS.get(intent, self.OPENERS[Intent.UNKNOWN])
        body = "\n\n".join(" ".join(p.text.split()) for p in passages[:2])
        citations = list(dict.fromkeys(p.document_id for p in passages[:2]))
        return Answer(
            text=f"{opener}\n\n{body}\n\nFonte: {', '.join(citations)}",
            citations=citations,
            grounded=True,
            responder=self.name,
        )


@dataclass
class Reply:
    answer: Answer
    classification: Classification
    passages: list[Retrieved]
    escalated: bool
    escalation_reason: str | None
    latency_ms: float
    #: The most context-aware query retrieval ran on. Differs from the message for a
    #: follow-up, and the evaluation reports it so a bad answer can be traced to a bad
    #: rewrite.
    query: str = ""
    #: Every query retrieval ran on, the raw message always among them.
    queries: list[str] = field(default_factory=list)


class ResponsiveAgent(Agent):
    name = "responsive"
    handles = frozenset({EventType.MESSAGE_RECEIVED, EventType.AUDIO_RECEIVED})

    def __init__(
        self,
        index: VectorIndex,
        classifier: IntentClassifier | None = None,
        responder: Responder | None = None,
        rewriter: QueryRewriter | None = None,
        context_window: int = CONTEXT_WINDOW,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.index = index
        self.classifier = classifier or IntentClassifier()
        self.responder = responder or TemplateResponder()
        # Concatenation by default: it needs no key, so multi-turn works on a fresh
        # clone, and it is the baseline the LLM rewriter is measured against.
        self.rewriter = rewriter or get_rewriter("concat")
        #: Turns of thread the agent may look back over. Derived in docs/multi-turn.md.
        self.context_window = context_window
        self.clock = clock
        #: Consecutive messages we failed to understand, per customer.
        self._unclear: dict[str, int] = {}
        #: Words the corpus and the cue phrases use, for deciding whether an
        #: unclassifiable message is a follow-up or nonsense. Built once.
        # `getattr`: a test double standing in for the index is a legitimate thing to
        # hand this agent, and the vocabulary is an aid to intent inheritance, not a
        # requirement for answering.
        self.vocabulary = build_vocabulary(
            [chunk.text for chunk in getattr(index, "chunks", [])], self.classifier
        )

    # ----------------------------------------------------------------- answering
    def answer(
        self,
        message: str,
        customer_id: str = "anonymous",
        history: Conversation | None = None,
    ) -> Reply:
        started = self.clock()
        classification = resolve_intent(
            self.classifier.classify(message),
            message,
            history,
            self.vocabulary,
            self.context_window,
        )

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
                query=message,
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
                query=message,
            )

        # Retrieval runs on every query the strategy offers -- always including the raw
        # message -- and keeps the best passages across all of them. Replacing the
        # message with a context-laden query measurably lost passages the message alone
        # would have found; adding a query cannot.
        queries = self.rewriter.queries(message, history)
        passages = self._retrieve(queries)
        answer = self.responder.respond(message, classification.intent, passages, history)

        return Reply(
            answer=answer,
            classification=classification,
            passages=passages,
            # Nothing relevant retrieved means a human, not a guess.
            escalated=not answer.grounded,
            escalation_reason="no_grounding" if not answer.grounded else None,
            latency_ms=(self.clock() - started) * 1000,
            query=queries[-1],
            queries=queries,
        )

    def _retrieve(self, queries: list[str]) -> list[Retrieved]:
        """Best passages across every query, interleaved by rank rather than by score.

        Sorting the union by score looks obvious and is wrong. A longer query produces
        systematically higher cosines against long passages, so the contextual query wins
        every comparison and crowds out the hits the customer's own words found -- which
        is the failure the multi-query design was meant to remove, reintroduced one layer
        down. Measured: merging by score still lost `e a entrada?`, a follow-up the bare
        message retrieves correctly.

        Interleaving by rank compares each query only against itself. Every query's best
        passage is taken before any query's second, so the message's top hit is always
        present no matter how the context scored.
        """
        ranked = [
            [hit for hit in self.index.search(query, top_k=TOP_K) if hit.score >= MIN_RELEVANCE]
            for query in queries
        ]
        merged: dict[str, Retrieved] = {}
        for rank in range(TOP_K):
            for hits in ranked:
                if rank >= len(hits) or len(merged) >= TOP_K:
                    continue
                hit = hits[rank]
                if hit.chunk.chunk_id not in merged:
                    merged[hit.chunk.chunk_id] = hit
        return list(merged.values())

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

        # The channel puts the thread on the event; a channel that does not have one
        # (or does not keep it) simply omits it and the agent answers single-turn.
        history = event.payload.get("history")
        reply = self.answer(
            message,
            customer_id=event.customer_id,
            history=history if isinstance(history, Conversation) else None,
        )

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


def get_responder(name: str = "template") -> Responder:
    """Pick a responder by name.

    The LLM import is deferred: `collection.llm.responder` imports `Responder` from this
    module, and naming it at the top would close the cycle. Deferring it also keeps the
    optional SDK out of the import path for anyone running the template arm.
    """
    if name == "template":
        return TemplateResponder()
    if name == "llm":
        from collection.llm.responder import LLMResponder

        return LLMResponder()
    raise ValueError(f"Unknown responder: {name!r}. Available: template, llm")
