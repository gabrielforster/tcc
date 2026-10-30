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
from pathlib import Path
from threading import Lock
from typing import Protocol

from collection.agents.base import Agent
from collection.agents.conversation import Conversation
from collection.agents.events import Event, EventType
from collection.agents.orchestrator import Orchestrator
from collection.agents.responsive import Responder, ResponsiveAgent
from collection.channels.base import ChannelCapabilities
from collection.channels.delivery import DeliveryLog, DeliveryStatus
from collection.channels.session import Session, SessionStore, clean_message
from collection.rag.index import Retrieved
from collection.voice import audio
from collection.voice.transcribe import Transcriber


class Retriever(Protocol):
    """What the channel needs of retrieval, and nothing more.

    Structural on purpose: the in-memory `VectorIndex` fitted at startup and the
    `PgVectorRetriever` reading one tenant's rows are the same thing to a channel, and
    making the channel name either of them would make it choose.
    """

    def search(self, query: str, top_k: int = ...) -> list[Retrieved]: ...


#: Shown when the conversation is handed over. The widget stops accepting input after it.
HANDOVER_NOTICE = (
    "Vou encaminhar o seu atendimento para uma pessoa da equipe. "
    "Em breve alguém entra em contato por aqui."
)
#: Stands in for the customer's turn until the agent has heard the clip. The channel does
#: not transcribe, so at the moment the turn is recorded nobody knows what was said; the
#: turn is rewritten with the transcript once the agent has produced one.
VOICE_NOTE_PLACEHOLDER = "[mensagem de voz]"
#: Shown once, at the top of the thread. Transparency about who the customer is talking
#: to is not decoration: an automated collection contact that passes itself off as a
#: person is the kind of thing CDC art. 42 exists to prevent.
GREETING = (
    "Olá! Sou o atendimento automático de cobrança. "
    "Posso ajudar com a sua pendência, parcelamento ou segunda via. "
    "Se precisar, transfiro para uma pessoa da equipe."
)


@dataclass(frozen=True)
class PendingMessage:
    """An outbound message waiting in a customer's mailbox for the widget to open."""

    text: str
    delivery_id: str


class _Mailer(Agent):
    """Puts a scheduled contact into the mailbox and says that it did.

    A channel-side agent, not one of `collection.agents`: it exists so `seed` can go
    through the orchestrator (which owns opt-out suppression) instead of testing
    `opted_out` itself. The orchestrator only routes events to agents, so delivery has to
    be an agent's job. It lives here because it needs the mailbox, and the agents package
    must not import a channel.
    """

    name = "chat_mailer"
    handles = frozenset({EventType.CONTACT_SCHEDULED})

    def __init__(self, channel: "ChatChannel") -> None:
        self.channel = channel

    def accepts(self, event: Event) -> bool:
        # Another channel's contact is not ours to put in a chat mailbox.
        return super().accepts(event) and event.payload.get("channel", "chat") == "chat"

    def handle(self, event: Event) -> list[Event]:
        text = str(event.payload.get("message", "")).strip()
        if not text:
            # A producer bug, not a skip: swallowing it would leave a contact_scheduled
            # in the audit with no trace of why nothing was ever sent.
            raise ValueError(f"contact_scheduled {event.event_id} carries no message text")
        delivery_id = self.channel.deliver(event.customer_id, text)
        self.channel._enqueue(event.customer_id, PendingMessage(text, delivery_id))
        return [event.caused(EventType.CONTACT_SENT, channel="chat", delivery_id=delivery_id)]


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
    #: What the agent heard, for a voice note; `None` for typed text. Carried out of the
    #: `reply_sent` payload because the agent -- not the channel -- owns recognition.
    transcript: str | None = None
    transcription_confidence: float = 0.0
    #: True when the reply opens by reading a spoken figure back for the customer to confirm.
    needs_confirmation: bool = False
    #: Tracks the agent message in this reply; the widget reports `entregue`/`lida` against it.
    delivery_id: str | None = None


class ChatChannel:
    """The chat channel; satisfies `collection.channels.base.Channel` structurally."""

    name = "chat"
    capabilities = ChannelCapabilities(
        accepts_audio=True,
        accepts_text=True,
        shows_delivery_status=True,
        realtime=False,
    )

    def __init__(
        self,
        retriever: Retriever,
        responder: Responder | None = None,
        store: SessionStore | None = None,
        delivery: DeliveryLog | None = None,
        opted_out: set[str] | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.retriever = retriever
        # `is not None`, not `or`: a store counts the conversations it holds, so an empty
        # one is falsy and `or` would quietly swap a real store for an in-memory one.
        self.store = store if store is not None else SessionStore()
        self.opted_out = opted_out if opted_out is not None else set()
        self.delivery = delivery if delivery is not None else DeliveryLog()
        # Outbound messages waiting for a customer to open the widget, keyed by customer.
        # Deliberately not on a `Session`: sessions expire after 30 minutes and are swept,
        # and a scheduled collection contact must not vanish because nobody opened a tab.
        self._mailbox: dict[str, list[PendingMessage]] = {}
        self._mailbox_lock = Lock()
        #: Events seed() declined on purpose, as (reason, event): "suppressed" for an opt-out,
        #: "other_channel" for a contact that belongs to a different channel.
        self.skipped: list[tuple[str, Event]] = []
        #: Every event the channel's own orchestrator runs saw, in order -- including
        #: `contact_sent`, which is how a scheduled contact is shown to have been sent.
        self.audit: list[Event] = []
        # The agent messages each session is waiting for an answer to: the greeting plus anything
        # seeded, all answered by the customer's next turn.
        self._awaiting_reply: dict[str, list[str]] = {}
        # One agent across all sessions: it counts consecutive unclear messages per
        # customer, and a fresh agent per message would reset that counter every turn,
        # so the "three unclear messages in a row" handover could never fire.
        self.agent = ResponsiveAgent(index=retriever, responder=responder, transcriber=transcriber)

    def deliver(self, customer_id: str, text: str, **meta: object) -> str:
        """Record an outbound message and return the delivery id to track it by.

        Chat is store-and-forward: the widget picks the message up when it next polls, so
        this records `ENVIADA` and nothing more. Later rungs come from the widget reporting
        back (`ENTREGUE`, `LIDA`) or the customer replying (`RESPONDIDA`); see `delivery.py`
        for what each does and does not prove. A proactive message sent here is not tied
        to a session, so a reply in some later thread does not mark it `RESPONDIDA`.
        """
        # Owned by the customer id, not a session: a seeded message exists before any
        # session does. `open()` re-owns it to the session that picks it up.
        return self.delivery.record(text, owner=customer_id).id

    def seed(self, event: Event) -> str | None:
        """Leave a scheduled contact waiting for the customer; return its delivery id.

        No push transport: the message sits in the mailbox and the widget finds it on
        `open()`. Returns `None` when nothing was delivered on purpose -- the customer is
        opted out or the contact is for another channel (see `skipped` for which). A contact
        with no text is a producer bug and raises `ValueError`.

        The event goes through an `Orchestrator` rather than a check of `opted_out` here,
        because suppression is the orchestrator's rule and a channel that re-implements it
        can come to disagree with it. A suppressed event is recorded on the orchestrator's
        trace, which is copied to `audit`.

        A tenant that is not `ACTIVE` is the one refusal taken before the orchestrator:
        the proactive agent schedules nothing for it (spec 6.2), and it is recorded as a
        skip with a reason rather than raised, because the agent is running over a whole
        portfolio and a closed tenant is an expected state, not an error.
        """
        if not self.store.servable:
            self.skipped.append(("tenant_not_servable", event))
            return None
        orchestrator = Orchestrator(agents=[_Mailer(self)], opted_out=self.opted_out)
        orchestrator.submit(event)
        trace = orchestrator.run()
        # `contact_sent` is already in `produced`; it also lands in `unrouted` because no agent
        # here handles it, so only the submitted event itself can be a wrong-channel skip.
        declined = [e for e in trace.unrouted if e.event_id == event.event_id]
        self.audit.extend(trace.processed + trace.produced + trace.suppressed + declined)
        self.skipped += [("suppressed", e) for e in trace.suppressed]
        self.skipped += [("other_channel", e) for e in declined]
        for produced in trace.produced:
            if produced.type is EventType.CONTACT_SENT:
                return str(produced.payload["delivery_id"])
        return None

    def _enqueue(self, customer_id: str, message: PendingMessage) -> None:
        with self._mailbox_lock:
            self._mailbox.setdefault(customer_id, []).append(message)

    def _drain(self, customer_id: str) -> list[PendingMessage]:
        with self._mailbox_lock:
            waiting = self._mailbox.pop(customer_id, [])
        # Delivery-time opt-out check, deliberately outside the orchestrator. The
        # orchestrator suppresses at *routing* time, but a customer can opt out after the
        # seed and before the widget opens, by any path that adds to the shared
        # `opted_out` set. Only the moment of delivery sees all of them. Do not "tidy" this
        # away as a duplicate of the orchestrator's rule.
        if customer_id in self.opted_out:
            return []
        return waiting

    def _purge_opted_out(self) -> None:
        """Drop waiting messages for anyone who has opted out since they were seeded."""
        with self._mailbox_lock:
            for customer_id in self.opted_out & self._mailbox.keys():
                del self._mailbox[customer_id]

    # ------------------------------------------------------------------ sessions
    def open(self, token: str | None = None) -> tuple[Session, str]:
        """Start a conversation and return it with the greeting to display.

        Anything seeded for this customer follows the greeting in the thread, oldest
        first. The customer's next turn answers all of them.
        """
        session = self.store.open(token)
        # Tracked like any agent message, and its id is handed to the widget so it can
        # report the first message of the thread too.
        session.greeting_delivery_id = self.delivery.record(GREETING, owner=session.session_id).id
        session.record("agent", GREETING, delivery_id=session.greeting_delivery_id)
        self._awaiting_reply[session.session_id] = [session.greeting_delivery_id]
        for pending in self._drain(session.customer_id):
            session.record("agent", pending.text, delivery_id=pending.delivery_id)
            self.delivery.reassign(pending.delivery_id, session.session_id)
            self._awaiting_reply[session.session_id].append(pending.delivery_id)
        return session, GREETING

    def transcript(self, session_id: str) -> list[dict[str, object]]:
        session = self.store.get(session_id)
        return [
            {
                "role": turn.role,
                "text": turn.text,
                "at": turn.at,
                "intent": turn.intent,
                "delivery_id": turn.delivery_id,
            }
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
        self._mark_answered(session)

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
        self._purge_opted_out()

        reply = self._render(session_id, trace.produced)
        # The intent is only known after the agent has run, so the customer's turn is
        # labelled retroactively. Without this the transcript carries no intent on the
        # side that has one, and the next follow-up would have nothing to inherit.
        customer_turn.intent = reply.intent
        session.amend(customer_turn)
        self._record_answer(session, reply, trace.produced)
        if reply.escalated:
            session.escalated = True
        self._track_reply(session, reply)
        return reply

    def send_audio(self, session_id: str, path: Path, *, rate_checked: bool = False) -> ChatReply:
        """Route one voice note through the orchestrator, exactly as `send` routes text.

        The channel does not transcribe. It hands the orchestrator an `audio_received`
        event that points at the file, and `ResponsiveAgent` does the recognition. That is
        deliberate: three decisions hang off the `Transcription` object -- the
        `MIN_CONFIDENCE` escalation, the read-back of a spoken figure, and the transcript
        fields on `reply_sent`. A channel that recognised speech and emitted plain text
        would silently switch all three off.

        What the channel *does* own is refusing a clip before anything is spent on it.
        `audio.accept` raises `AudioError` for a file that is not audio, too short or too
        long, and it is called here rather than left to the agent because the agent turns
        an `AudioError` into an escalation. "The decoder refused the file" is a bad
        request the caller can fix; "the agent could not understand it" is a conversation
        a person should pick up. They are different outcomes and must not look alike.

        `rate_checked` is for a caller that already spent the rate-limit hit before
        reading the upload (the HTTP route does, so an unknown or limited session never
        costs a read); without it the hit is spent here and counted once either way.
        """
        session = self.store.get(session_id)
        if not rate_checked:
            session.check_rate()
        audio.accept(path)
        history = self._history(session)
        customer_turn = session.record("customer", VOICE_NOTE_PLACEHOLDER)
        self._mark_answered(session)

        orchestrator = Orchestrator(agents=[self.agent], opted_out=self.opted_out)
        orchestrator.submit(
            Event(
                type=EventType.AUDIO_RECEIVED,
                customer_id=session.customer_id,
                payload={
                    "audio_path": str(path),
                    "channel": "web",
                    "session_id": session_id,
                    "history": history,
                },
            )
        )
        trace = orchestrator.run()
        self.opted_out |= orchestrator.opted_out
        self._purge_opted_out()

        reply = self._render(session_id, trace.produced)
        # The session record is what was said, not what was displayed: once the agent has
        # heard the clip, the transcript replaces the placeholder, so the thread a later
        # turn resolves a follow-up against reads like a conversation. The exception is a
        # clip the agent rejected as unintelligible: its transcript is a low-confidence
        # guess, and writing it into the session record would make a guess read as
        # something the customer said. That record is the audit trail of what they said,
        # so the placeholder stays; the guess is still on the reply and the escalation.
        if reply.transcript and reply.escalation_reason != "audio_unintelligible":
            customer_turn.text = reply.transcript
        customer_turn.intent = reply.intent
        session.amend(customer_turn)
        self._record_answer(session, reply, trace.produced)
        if reply.escalated:
            session.escalated = True
        self._track_reply(session, reply)
        return reply

    def _record_answer(self, session: Session, reply: ChatReply, produced: list[Event]) -> None:
        """Record the agent's turn with the evidence behind it.

        The transcript keeps what was displayed; a store that keeps rows also keeps what
        the answer was made from, so the passages travel with the turn rather than being
        looked up again later -- by then the current version of a document may not be the
        one that was read. They come off the `reply_sent` payload because the channel
        talks to the orchestrator, not to the agent.

        The responder is read from the same payload rather than from the channel's
        configured one, because an LLM responder that fell back to the template says so on
        its answer and a record that named the model would be a lie. It is deliberately
        not put on the `ChatReply`: that is what the widget is sent, and what the customer
        is sent does not change in this stage.
        """
        answered = next(
            (event.payload for event in produced if event.type is EventType.REPLY_SENT), {}
        )
        session.record(
            "agent",
            reply.text,
            intent=reply.intent,
            escalated=reply.escalated,
            passages=answered.get("passages", []),
            confidence=reply.confidence,
            responder=answered.get("responder", ""),
            latency_ms=reply.latency_ms,
            escalation_reason=reply.escalation_reason,
        )

    def _mark_answered(self, session: Session) -> None:
        """The customer's turn arrived: the agent message before it was answered.

        There is deliberately no `escalated` check here. Handover ends the thread, and the
        mechanism is that an escalated reply is never registered as awaiting (see
        `_track_reply`), so a message that still reaches `send` afterwards finds nothing
        to advance. One mechanism, pinned by `test_an_escalated_thread_stops_advancing`.
        """
        for delivery_id in self._awaiting_reply.pop(session.session_id, []):
            self.delivery.advance(delivery_id, DeliveryStatus.RESPONDIDA)

    def _track_reply(self, session: Session, reply: ChatReply) -> None:
        """Record the agent's message and, unless it ends the thread, await an answer.

        The id goes on the transcript turn too: a widget that draws the thread from the
        transcript must be able to report on every agent bubble it draws, not only the
        ones it saw arrive.
        """
        record = self.delivery.record(reply.text, owner=session.session_id)
        reply.delivery_id = record.id
        for turn in reversed(session.transcript):
            if turn.role == "agent":
                turn.delivery_id = record.id
                session.amend(turn)
                break
        if not reply.escalated:
            self._awaiting_reply[session.session_id] = [record.id]

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
                transcript = event.payload.get("transcript")
                if transcript is not None:
                    reply.transcript = str(transcript)
                    reply.transcription_confidence = float(
                        event.payload.get("transcription_confidence", 0.0)
                    )
                    reply.needs_confirmation = bool(event.payload.get("needs_confirmation", False))
                return reply

        for event in produced:
            if event.type is EventType.ESCALATED_TO_HUMAN:
                reply.escalated = True
                reply.escalation_reason = event.payload.get("reason")
                reply.intent = event.payload.get("intent")
                reply.confidence = float(event.payload.get("confidence", 0.0))
                # An escalated voice note still carries what the system thought it heard.
                transcript = event.payload.get("transcript")
                if transcript:
                    reply.transcript = str(transcript)
                return reply

        # Nothing produced: no agent handled the event. Handing over is the safe reading,
        # and the empty `events` list on the reply says why.
        reply.escalated = True
        reply.escalation_reason = "unrouted"
        return reply
