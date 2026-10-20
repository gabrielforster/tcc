"""The web chat channel.

An embeddable widget talks to this. It is a *channel adapter*: it owns transport and
identity, and it hands every message to the orchestrator as a `message_received` event
rather than calling the responsive agent directly.

That indirection is the point. Routing through the orchestrator means the web chat
inherits the global rules for free -- priority, the cascade guard, the audit trail, and an
opt-out that is enforced before any agent sees the event. A channel that called the agent
directly would quietly opt out of all of them, and the compliance story would be true of
the demo and false of the product. It also means the next channel (WhatsApp, a phone call)
is another adapter over the same contract, not a second copy of the conversation logic.

What the channel does own:

* **Identity**, because only the channel knows whether the browser proved who it is.
* **The thread**, because HTTP does not have one. Sessions live in `session.py`.
* **Translating events back into a reply**, because the orchestrator speaks in events and
  a widget speaks in messages.

Opt-out state lives on the channel and persists across messages, so a customer who types
"parem de me mandar mensagem" is recorded as opted out for every later event -- including
the proactive ones that arrive through a different channel entirely.
"""

from dataclasses import dataclass, field

from collection.agents.conversation import Conversation
from collection.agents.events import Event, EventType
from collection.agents.orchestrator import Orchestrator
from collection.agents.responsive import Responder, ResponsiveAgent
from collection.channels.session import Session, SessionStore, clean_message
from collection.rag.index import VectorIndex

#: Shown when the conversation is handed over. The widget stops accepting input after it.
HANDOVER_NOTICE = (
    "Vou encaminhar o seu atendimento para uma pessoa da equipe. "
    "Em breve alguém entra em contato por aqui."
)
#: Shown once, at the top of the thread. Transparency about who the customer is talking
#: to is not decoration: an automated collection contact that passes itself off as a
#: person is the kind of thing CDC art. 42 exists to prevent.
GREETING = (
    "Olá! Sou o atendimento automático de cobrança. "
    "Posso ajudar com a sua pendência, parcelamento ou segunda via. "
    "Se precisar, transfiro para uma pessoa da equipe."
)


@dataclass
class ChatReply:
    """One turn, as the widget needs to render it."""

    session_id: str
    text: str
    intent: str | None = None
    confidence: float = 0.0
    citations: list[str] = field(default_factory=list)
    escalated: bool = False
    escalation_reason: str | None = None
    opted_out: bool = False
    latency_ms: float = 0.0
    responder: str = ""
    #: Event types produced by this turn, for the audit trail the paper describes.
    events: list[str] = field(default_factory=list)


class WebChatChannel:
    def __init__(
        self,
        index: VectorIndex,
        responder: Responder | None = None,
        store: SessionStore | None = None,
        opted_out: set[str] | None = None,
    ) -> None:
        self.index = index
        self.store = store or SessionStore()
        self.opted_out = opted_out if opted_out is not None else set()
        # One agent across all sessions: it counts consecutive unclear messages per
        # customer, and a fresh agent per message would reset that counter every turn,
        # so the "three unclear messages in a row" handover could never fire.
        self.agent = ResponsiveAgent(index=index, responder=responder)

    # ------------------------------------------------------------------ sessions
    def open(self, token: str | None = None) -> tuple[Session, str]:
        """Start a conversation and return it with the greeting to display."""
        session = self.store.open(token)
        session.record("agent", GREETING)
        return session, GREETING

    def transcript(self, session_id: str) -> list[dict[str, object]]:
        session = self.store.get(session_id)
        return [
            {"role": turn.role, "text": turn.text, "at": turn.at, "intent": turn.intent}
            for turn in session.transcript
        ]

    # ------------------------------------------------------------------ messages
    def send(self, session_id: str, raw: str) -> ChatReply:
        """Route one customer message through the orchestrator and render the result."""
        session = self.store.get(session_id)
        session.check_rate()
        text = clean_message(raw)
        history = self._history(session)
        customer_turn = session.record("customer", text)

        orchestrator = Orchestrator(agents=[self.agent], opted_out=self.opted_out)
        orchestrator.submit(
            Event(
                type=EventType.MESSAGE_RECEIVED,
                customer_id=session.customer_id,
                payload={
                    "text": text,
                    "channel": "web",
                    "session_id": session_id,
                    # The thread so far, excluding the message being answered: the agent
                    # needs it to resolve "e em 6 vezes?" into a question it can retrieve
                    # on. It rides on the event rather than being handed to the agent
                    # directly, so the channel keeps talking to the orchestrator only.
                    "history": history,
                },
            )
        )
        trace = orchestrator.run()
        # The orchestrator records opt-outs on the set it was handed, so the channel's
        # own set is already updated; this keeps that dependency explicit.
        self.opted_out |= orchestrator.opted_out

        reply = self._render(session_id, trace.produced)
        # The intent is only known after the agent has run, so the customer's turn is
        # labelled retroactively. Without this the transcript carries no intent on the
        # side that has one, and the next follow-up would have nothing to inherit.
        customer_turn.intent = reply.intent
        session.record("agent", reply.text, intent=reply.intent, escalated=reply.escalated)
        if reply.escalated:
            session.escalated = True
        return reply

    def _history(self, session: Session) -> Conversation:
        """The session transcript as the agent's view of the thread.

        A translation rather than a shared object: the session records what was displayed
        (timestamps, escalation flags, the greeting), the agent needs what was said. A
        greeting nobody typed is not a turn the agent should resolve a follow-up against,
        so it is dropped here.
        """
        conversation = Conversation()
        for turn in session.transcript:
            if turn.role == "agent" and turn.text == GREETING:
                continue
            conversation.add(turn.role, turn.text, turn.intent)
        return conversation

    def _render(self, session_id: str, produced: list[Event]) -> ChatReply:
        """Turn the events a run produced into the one reply the widget shows.

        A turn can produce more than one event -- an opt-out request and an escalation,
        say -- but the customer sees a single message, so the terminal outcomes are
        checked in the order that matters to them.
        """
        reply = ChatReply(
            session_id=session_id,
            text=HANDOVER_NOTICE,
            events=[event.type.value for event in produced],
        )

        for event in produced:
            if event.type is EventType.OPT_OUT_REQUESTED:
                reply.opted_out = True
                reply.text = (
                    "Pronto, registrei o seu pedido: não enviaremos mais mensagens de "
                    "cobrança por este canal."
                )
                return reply

        for event in produced:
            if event.type is EventType.REPLY_SENT:
                reply.text = str(event.payload.get("text", ""))
                reply.intent = event.payload.get("intent")
                reply.confidence = float(event.payload.get("confidence", 0.0))
                reply.citations = list(event.payload.get("citations", []))
                reply.latency_ms = float(event.payload.get("latency_ms", 0.0))
                return reply

        for event in produced:
            if event.type is EventType.ESCALATED_TO_HUMAN:
                reply.escalated = True
                reply.escalation_reason = event.payload.get("reason")
                reply.intent = event.payload.get("intent")
                reply.confidence = float(event.payload.get("confidence", 0.0))
                return reply

        # Nothing produced: no agent handled the event. Handing over is the safe reading,
        # and the empty `events` list on the reply says why.
        reply.escalated = True
        reply.escalation_reason = "unrouted"
        return reply
