"""The voice call: a real-time channel over the same contract as the chat.

What is new on a call is not what the agent decides -- it is the same `ResponsiveAgent`,
reached through the same orchestrator, deciding the same things -- but four obligations a
chat does not have:

* **A legal opening that cannot be skipped.** The call opens by saying it is being
  transcribed and recorded, and nothing the customer says counts until that sentence has
  finished playing. Speech heard during the notice is logged and discarded, not queued:
  a turn that slipped in under the notice would be a recording the customer was not yet
  told about. This lives here, in the channel's state, rather than in the browser page,
  because a page is code the customer controls.
* **Silence is an event.** A chat customer who stops typing has simply left. A caller who
  stops talking is still on the line, so the call asks once and then hangs up -- a
  collection call that sits open on a silent line is a call nobody is accountable for.
* **The thread ends on the call.** A handover or an opt-out ends a chat thread by stopping
  input; on a call it ends the call, after the sentence that says so.
* **Two records with two lifetimes** (spec decision 7). The transcript goes where the chat's
  goes -- the session store, which is rows for a tenant -- and is the audit record. The
  audio is kept apart under `data/calls/`, because a voice is biometric personal data and
  must be erasable on request without destroying the account of why the customer was
  contacted. `erase_audio` is that path.

What it deliberately does not do is recognise speech. Each finished utterance is written to
a clip and handed to the orchestrator as `audio_received` -- exactly what the chat channel
does with a voice note, and by the same code (`ChatChannel.send_audio`), so the
`MIN_CONFIDENCE` escalation and the read-back of a spoken figure apply on a call without
being re-implemented. Reusing the chat channel's routing is the cheapest way to make the
cross-channel divergence zero by construction rather than by care.

Transport-free: `livekit_call.py` drives this from a WebRTC room, the tests drive it from
WAV files, and the turn-time benchmark drives it from synthesised speech. None of the
three can skip the notice, because none of them owns the state that enforces it.
"""

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Lock

from collection.agents.responsive import Responder
from collection.channels.base import ChannelCapabilities
from collection.channels.session import (
    ConversationEnded,
    RateLimited,
    SessionNotFound,
    SessionStore,
)
from collection.channels.web import GREETING, HANDOVER_NOTICE, ChatChannel, Retriever
from collection.config import settings
from collection.voice.audio import AudioClip, AudioError
from collection.voice.speak import SLOWEST_WORDS_PER_SECOND
from collection.voice.transcribe import Transcriber, Transcription

#: Spoken first, on every call, before anything else. The wording matches the notice on
#: the approved call screen ("está sendo transcrita e registrada"), which is true of both
#: records the call keeps.
RECORDING_NOTICE = "Esta ligação está sendo transcrita e registrada."
#: Asked once when the line goes quiet.
SILENCE_PROMPT = "Você ainda está na linha? Pode falar, estou ouvindo."
#: Said before hanging up on a line that stayed quiet after the prompt.
SILENCE_GOODBYE = (
    "Como não ouvi resposta, vou encerrar a ligação. Você pode retomar o atendimento quando quiser."
)
#: Seconds of silence, after the agent stops speaking, before the line counts as quiet.
SILENCE_TIMEOUT_S = 8.0
#: Prompts on a quiet line before the call is ended.
MAX_SILENCE_PROMPTS = 1
#: How long one reply may run before the customer gets the line back. A choice, not a
#: measurement: twenty seconds is about five of the customer's own turns (median 4.1 s in
#: the call benchmark), and at that length a ten-minute call still holds some twenty
#: exchanges. The first benchmark's replies ran a median 102 s -- the customer listened
#: for a minute and a half to answer one question.
REPLY_TARGET_S = 20.0
#: The target in words, at the slowest rate Piper speaks the corpus. Derived rather than
#: chosen: at any faster rate the reply finishes sooner, so the target holds for all of it.
REPLY_MAX_WORDS = int(REPLY_TARGET_S * SLOWEST_WORDS_PER_SECOND)
#: A call is cut here regardless. Collection calls are short; one that runs for ten
#: minutes is a conversation a person should be having.
MAX_CALL_S = 600.0


class CallState(StrEnum):
    #: The recording notice and greeting are playing; nothing the customer says counts.
    OPENING = "opening"
    LISTENING = "listening"
    ENDED = "ended"


class CallEnded(RuntimeError):
    """The call is over; nothing more can be heard on it."""


@dataclass
class CallEvent:
    kind: str
    at: float
    detail: dict[str, object] = field(default_factory=dict)


@dataclass
class TurnTiming:
    """Where one turn's latency went, from the end of speech to the first reply audio.

    `endpointing_ms` is the wait before the system decided the customer had finished;
    `stt_ms` the local recogniser; `agent_ms` everything else the orchestrator ran --
    intent, retrieval, the responder; `tts_first_audio_ms` the synthesiser's first chunk.
    The transport fills the last term, because only it plays audio.
    """

    endpointing_ms: float = 0.0
    stt_ms: float = 0.0
    agent_ms: float = 0.0
    tts_first_audio_ms: float | None = None

    @property
    def total_ms(self) -> float:
        return self.endpointing_ms + self.stt_ms + self.agent_ms + (self.tts_first_audio_ms or 0.0)

    def as_dict(self) -> dict[str, float | None]:
        return {
            "endpointing_ms": round(self.endpointing_ms, 1),
            "stt_ms": round(self.stt_ms, 1),
            "agent_ms": round(self.agent_ms, 1),
            "tts_first_audio_ms": (
                None if self.tts_first_audio_ms is None else round(self.tts_first_audio_ms, 1)
            ),
            "total_ms": round(self.total_ms, 1),
        }


@dataclass
class CallTurn:
    """What the transport must do after a turn: say `say`, then hang up if `ends_call`."""

    say: str
    ends_call: bool = False
    end_reason: str | None = None
    transcript: str | None = None
    intent: str | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    needs_confirmation: bool = False
    timing: TurnTiming = field(default_factory=TurnTiming)


@dataclass
class Call:
    call_id: str
    customer_id: str
    started_at: float
    #: What the transport must play, in order, before the call listens.
    opening: list[str]
    state: CallState = CallState.OPENING
    events: list[CallEvent] = field(default_factory=list)
    clips: list[Path] = field(default_factory=list)
    timings: list[TurnTiming] = field(default_factory=list)
    end_reason: str | None = None
    #: The thread as it stood when the call ended; the session may be swept afterwards.
    transcript: list[dict[str, object]] = field(default_factory=list)
    silence_prompts: int = 0

    def log(self, kind: str, **detail: object) -> CallEvent:
        event = CallEvent(kind=kind, at=time.time(), detail=detail)
        self.events.append(event)
        return event


class _TimedTranscriber(Transcriber):
    """Passes recognition through unchanged and remembers how long it took.

    Measurement, not a decision: the agent still calls the recogniser and still owns what
    it does with the result. The wall clock is taken around the whole call, decoding
    included, because that is the time the customer waits.
    """

    def __init__(self, inner: Transcriber) -> None:
        self.inner = inner
        self.name = inner.name
        self.last_ms: float | None = None

    def transcribe(self, clip: AudioClip) -> Transcription:
        return self.inner.transcribe(clip)

    def transcribe_path(self, path: Path | str) -> Transcription:
        started = time.perf_counter()
        try:
            return self.inner.transcribe_path(path)
        finally:
            self.last_ms = (time.perf_counter() - started) * 1000


class VoiceCallChannel:
    """The voice channel; satisfies `collection.channels.base.Channel` structurally."""

    name = "voice"
    capabilities = ChannelCapabilities(
        accepts_audio=True,
        accepts_text=False,
        # There is nothing to report: the customer either heard it on the line or the
        # call record says the line was dropped.
        shows_delivery_status=False,
        realtime=True,
        max_reply_words=REPLY_MAX_WORDS,
    )

    def __init__(
        self,
        retriever: Retriever,
        responder: Responder | None = None,
        store: SessionStore | None = None,
        opted_out: set[str] | None = None,
        transcriber: Transcriber | None = None,
        audio_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.transcriber = _TimedTranscriber(transcriber) if transcriber is not None else None
        self._core = ChatChannel(
            retriever=retriever,
            responder=responder,
            store=store,
            opted_out=opted_out,
            transcriber=self.transcriber,
        )
        self._core.event_channel = self.name
        self._core.reply_max_words = self.capabilities.max_reply_words
        self.audio_dir = audio_dir or settings.dir_calls
        self.clock = clock
        self.calls: dict[str, Call] = {}
        #: Messages waiting to be said on the customer's next call, after the notice.
        self._pending: dict[str, list[str]] = {}
        self._lock = Lock()

    @property
    def opted_out(self) -> set[str]:
        return self._core.opted_out

    @property
    def audit(self) -> list:
        return self._core.audit

    # ------------------------------------------------------------------ contract
    def deliver(self, customer_id: str, text: str, **meta: object) -> str:
        """Hold `text` to be said on the customer's next call, after the notice.

        There is no dialer: this system does not place calls, for the same reason the
        chat has no push (docs/channels.md). The message waits for the customer to call
        in, and is dropped then if they have opted out in the meantime.
        """
        delivery_id = self._core.delivery.record(text, owner=customer_id).id
        with self._lock:
            self._pending.setdefault(customer_id, []).append(text)
        return delivery_id

    # --------------------------------------------------------------------- calls
    def start(self, token: str | None = None) -> Call:
        """Answer a call. The transport must play `call.opening` and then call
        `opening_played`; until it does, nothing the customer says is heard."""
        session = self._core.store.open(token)
        with self._lock:
            pending = self._pending.pop(session.customer_id, [])
        if session.customer_id in self.opted_out:
            pending = []
        opening = [RECORDING_NOTICE, GREETING, *pending]
        for line in opening:
            session.record("agent", line)
        call = Call(
            call_id=session.session_id,
            customer_id=session.customer_id,
            started_at=self.clock(),
            opening=opening,
        )
        call.log("call_started", identified=session.identified)
        call.log("notice_playing", text=RECORDING_NOTICE)
        with self._lock:
            self.calls[call.call_id] = call
        return call

    def opening_played(self, call_id: str) -> None:
        """The notice and greeting have finished playing; the call may listen now."""
        call = self._live(call_id)
        if call.state is CallState.OPENING:
            call.state = CallState.LISTENING
            call.log("notice_played")

    def hear(self, call_id: str, clip: Path, endpointing_ms: float = 0.0) -> CallTurn | None:
        """Route one finished utterance; `None` when there is nothing to say back.

        `None` covers the two cases where the customer is not owed a reply: speech during
        the notice, which does not count, and a clip the decoder refused (a click, a
        breath), which is not a turn. Everything else -- including audio the agent could
        not make out -- comes back as a `CallTurn`, because the agent's answer to that is a
        handover and the customer must hear it.
        """
        call = self._live(call_id)
        if call.state is CallState.OPENING:
            call.log("speech_during_notice_ignored")
            return None
        if self._overrun(call):
            return self._end(call, "max_duration", SILENCE_GOODBYE)
        call.silence_prompts = 0

        kept = self._keep(call, clip)
        if self.transcriber is not None:
            self.transcriber.last_ms = None
        started = time.perf_counter()
        try:
            # The clip as the transport wrote it, not the kept copy: the copy is named for
            # the record, and a recogniser keyed on the file (the scripted one) must see
            # the same file the chat's voice-note path would have handed it.
            reply = self._core.send_audio(call_id, Path(clip))
        except AudioError as error:
            call.log("utterance_rejected", detail=str(error))
            return None
        except ConversationEnded:
            return self._end(call, "turn_cap", HANDOVER_NOTICE)
        except RateLimited:
            # Twenty finished utterances in a minute is not a person talking.
            return self._end(call, "rate_limited", HANDOVER_NOTICE)
        elapsed_ms = (time.perf_counter() - started) * 1000
        stt_ms = (self.transcriber.last_ms if self.transcriber is not None else None) or 0.0
        timing = TurnTiming(
            endpointing_ms=endpointing_ms, stt_ms=stt_ms, agent_ms=max(0.0, elapsed_ms - stt_ms)
        )
        call.timings.append(timing)

        turn = CallTurn(
            say=reply.text,
            transcript=reply.transcript,
            intent=reply.intent,
            escalated=reply.escalated,
            escalation_reason=reply.escalation_reason,
            needs_confirmation=reply.needs_confirmation,
            timing=timing,
        )
        call.log(
            "turn",
            clip=kept.name,
            transcript=reply.transcript,
            intent=reply.intent,
            escalated=reply.escalated,
            escalation_reason=reply.escalation_reason,
            opted_out=reply.opted_out,
            needs_confirmation=reply.needs_confirmation,
            events=reply.events,
        )
        if reply.opted_out:
            self._end(call, "opt_out")
            turn.ends_call, turn.end_reason = True, "opt_out"
        elif reply.escalated:
            self._end(call, "handover")
            turn.ends_call, turn.end_reason = True, "handover"
        return turn

    def spoke(self, call_id: str, timing: TurnTiming, first_audio_ms: float) -> None:
        """The transport's report that the reply started playing, completing the timing."""
        call = self.calls[call_id]
        timing.tts_first_audio_ms = first_audio_ms
        call.log("turn_timed", **timing.as_dict())

    def silence(self, call_id: str) -> CallTurn | None:
        """The line has been quiet for `SILENCE_TIMEOUT_S`. Ask once, then hang up."""
        call = self._live(call_id)
        if call.state is CallState.OPENING:
            return None
        if call.silence_prompts < MAX_SILENCE_PROMPTS and not self._overrun(call):
            call.silence_prompts += 1
            call.log("silence_prompt")
            self._record_agent(call, SILENCE_PROMPT)
            return CallTurn(say=SILENCE_PROMPT)
        reason = "max_duration" if self._overrun(call) else "silence_timeout"
        return self._end(call, reason, SILENCE_GOODBYE)

    def hang_up(self, call_id: str) -> None:
        """The customer left. Not an error: it is how most calls end."""
        call = self.calls.get(call_id)
        if call is not None and call.state is not CallState.ENDED:
            self._end(call, "customer_hung_up")

    def fail(self, call_id: str, detail: str) -> None:
        """The transport broke -- the synthesiser, the room. Recorded, never raised past."""
        call = self.calls.get(call_id)
        if call is not None and call.state is not CallState.ENDED:
            self._end(call, "transport_error", detail=detail)

    # ------------------------------------------------------------------- records
    def record(self, call_id: str) -> dict[str, object]:
        """The full call record: what was said, what happened, and how long it took."""
        call = self.calls[call_id]
        transcript = call.transcript if call.state is CallState.ENDED else self._transcript(call)
        return {
            "call_id": call.call_id,
            "customer_id": call.customer_id,
            "channel": self.name,
            "state": call.state.value,
            "end_reason": call.end_reason,
            "started_at": call.started_at,
            "transcript": transcript,
            "events": [
                {"kind": event.kind, "at": event.at, **event.detail} for event in call.events
            ],
            "timings": [timing.as_dict() for timing in call.timings],
            "clips": [str(path) for path in call.clips],
        }

    def erase_audio(self, customer_id: str) -> int:
        """Delete every recording of a customer, keeping the transcripts and the events.

        The LGPD erasure path for the biometric half of the record. The transcript is the
        audit of why the customer was contacted and what they were told, and erasing the
        voice must not erase that; the event log gains an `audio_erased` entry so the
        record says the recordings existed and were removed, rather than going quiet.
        """
        erased = 0
        folder = self.audio_dir / customer_id
        if folder.exists():
            erased = sum(1 for path in folder.rglob("*") if path.is_file())
            shutil.rmtree(folder)
        for call in self.calls.values():
            if call.customer_id == customer_id:
                call.clips = []
                call.log("audio_erased", clips=erased)
        return erased

    # ------------------------------------------------------------------ internals
    def _live(self, call_id: str) -> Call:
        call = self.calls.get(call_id)
        if call is None:
            raise SessionNotFound("Unknown call.")
        if call.state is CallState.ENDED:
            raise CallEnded(f"call {call_id} ended: {call.end_reason}")
        return call

    def _overrun(self, call: Call) -> bool:
        return self.clock() - call.started_at > MAX_CALL_S

    def _keep(self, call: Call, clip: Path) -> Path:
        """Move the utterance into the call's recordings, beside the anonymised data."""
        folder = self.audio_dir / call.customer_id
        folder.mkdir(parents=True, exist_ok=True)
        kept = folder / f"{call.call_id}-{len(call.clips) + 1:03d}{Path(clip).suffix or '.wav'}"
        shutil.copyfile(clip, kept)
        call.clips.append(kept)
        return kept

    def _record_agent(self, call: Call, text: str) -> None:
        try:
            session = self._core.store.get(call.call_id)
        except (SessionNotFound, ConversationEnded):
            return
        session.record("agent", text)

    def _transcript(self, call: Call) -> list[dict[str, object]]:
        try:
            return self._core.transcript(call.call_id)
        except (SessionNotFound, ConversationEnded):
            return call.transcript

    def _end(
        self, call: Call, reason: str, closing: str | None = None, **detail: object
    ) -> CallTurn:
        if closing:
            self._record_agent(call, closing)
        call.transcript = self._transcript(call)
        call.state = CallState.ENDED
        call.end_reason = reason
        call.log("call_ended", reason=reason, **detail)
        return CallTurn(say=closing or "", ends_call=True, end_reason=reason)
