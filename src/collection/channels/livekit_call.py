"""Carrying a `VoiceCallChannel` call over a LiveKit room.

The agent joins the room as an ordinary participant, publishes one audio track (its voice)
and subscribes to the customer's microphone. Everything that decides anything is in
`voice_call.py`; this file only moves audio and keeps time:

* frames from the customer go through the `Endpointer`; a finished utterance is written to
  a WAV and handed to `channel.hear`, in a worker thread, because recognition is seconds of
  CPU and the room's event loop must keep reading audio meanwhile;
* the reply is synthesised sentence by sentence and played as it arrives, so the customer
  hears the first sentence while the second is still being made;
* a short JSON message on the room's data channel tells the browser what was heard and
  how long the turn took, which is what the call screen draws.

Why the low-level `livekit` SDK and not the `livekit-agents` framework the spec named. The
framework's voice pipeline is STT -> LLM -> TTS with the recogniser inside it, and it hands
the agent text. That is exactly the shape the channel contract forbids (docs/channels.md:
a channel must not decide what the customer said), and it would switch off the confidence
escalation and the figure read-back the voice-note work exists to defend. The cost of not
using it is the parts it does well -- Silero VAD, a learned end-of-turn model, and barge-in
-- which are named in `docs/voice-call.md` as this proof of concept's limitations.

**No barge-in.** While the agent speaks, the customer's audio is read and dropped. That is
a deliberate simplification, and it is also what makes the recording notice impossible to
talk over; when barge-in arrives, the notice must stay the one utterance it cannot cut.

`livekit` is imported lazily: the module loads, and the tests run, without the extra.
"""

import asyncio
import contextlib
import json
import secrets
import tempfile
import time
from pathlib import Path

from collection.channels.endpointing import FRAME_MS, Endpointer, Utterance
from collection.channels.voice_call import SILENCE_TIMEOUT_S, CallTurn, VoiceCallChannel
from collection.config import settings
from collection.voice.speak import SynthesisError, Synthesizer, resample, write_wav

#: What the endpointer and Whisper read. LiveKit resamples the customer's 48 kHz Opus to
#: this for us, so nothing here resamples on the way in.
IN_RATE = 16_000
#: What the agent publishes. WebRTC carries 48 kHz Opus; publishing at that rate means the
#: only resampling on the way out is ours, done once per sentence.
OUT_RATE = 48_000
#: How long the agent waits in an empty room for the customer to arrive.
JOIN_TIMEOUT_S = 60.0
#: Topic of the data messages the call page listens to.
DATA_TOPIC = "call"


class LiveKitUnavailable(RuntimeError):
    """The `call` extra is not installed."""


def _livekit():
    try:
        from livekit import api, rtc
    except ImportError as error:
        raise LiveKitUnavailable("The voice call needs livekit: uv sync --extra call") from error
    return api, rtc


def room_token(identity: str, room: str, name: str | None = None) -> str:
    """A join token for one room. Short-lived: a call is minutes, not a session."""
    api, _ = _livekit()
    from datetime import timedelta

    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name or identity)
        .with_ttl(timedelta(minutes=15))
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )


def new_room_name() -> str:
    return f"call-{secrets.token_hex(6)}"


class CallAgent:
    """One call: join, play the opening, then turn after turn until the call ends."""

    def __init__(
        self,
        channel: VoiceCallChannel,
        synthesizer: Synthesizer,
        room: str,
        customer_token: str | None = None,
        url: str | None = None,
        records_dir: Path | None = None,
    ) -> None:
        self.channel = channel
        self.synthesizer = synthesizer
        self.room_name = room
        self.customer_token = customer_token
        self.url = url or settings.livekit_url
        self.records_dir = records_dir or settings.dir_calls / "records"
        self.call_id: str | None = None
        #: True while the agent's audio is playing; the customer's frames are dropped.
        self.speaking = False
        #: True while a turn is being answered; the customer's frames are dropped.
        self.busy = False
        self.endpointer = Endpointer(sample_rate=IN_RATE)
        #: The customer has left the room.
        self.gone = False

    async def run(self) -> str | None:
        """Serve the call to the end and return its id, or `None` if nobody joined."""
        _, rtc = _livekit()
        loop = asyncio.get_running_loop()
        room = rtc.Room()
        joined: asyncio.Future = loop.create_future()
        queue: asyncio.Queue = asyncio.Queue()

        def on_track(track, publication, participant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO and not joined.done():
                joined.set_result(track)

        def on_left(participant) -> None:
            self.gone = True
            queue.put_nowait(("hangup", None, 0.0))

        room.on("track_subscribed", on_track)
        room.on("participant_disconnected", on_left)
        await room.connect(self.url, room_token(f"agent-{self.room_name}", self.room_name))
        source = rtc.AudioSource(OUT_RATE, 1)
        voice = rtc.LocalAudioTrack.create_audio_track("agent-voice", source)
        await room.local_participant.publish_track(
            voice, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        try:
            customer = await asyncio.wait_for(joined, JOIN_TIMEOUT_S)
        except TimeoutError:
            await room.disconnect()
            return None

        call = await asyncio.to_thread(self.channel.start, self.customer_token)
        self.call_id = call.call_id
        stream = rtc.AudioStream(
            customer, sample_rate=IN_RATE, num_channels=1, frame_size_ms=FRAME_MS
        )
        reader = asyncio.create_task(self._read(stream, queue))
        workdir = tempfile.TemporaryDirectory(prefix="call-")
        try:
            await self._send(room, type="call_started", call_id=call.call_id)
            # The opening plays to the end before anything is heard. `speaking` drops the
            # customer's frames meanwhile, and the channel would ignore them anyway.
            for line in call.opening:
                await self._send(room, type="agent", text=line)
                await self._say(rtc, source, line)
            self.channel.opening_played(call.call_id)
            await self._send(room, type="listening")
            self.endpointer.reset()
            self.busy = False

            turns = 0
            while True:
                kind, utterance, decided_at = await queue.get()
                if kind == "hangup":
                    self.channel.hang_up(call.call_id)
                    break
                if kind == "silence":
                    turn = self.channel.silence(call.call_id)
                else:
                    turns += 1
                    turn = await self._answer(room, utterance, workdir.name, turns)
                said = turn is not None and turn.say
                if said and await self._reply(rtc, room, source, turn, decided_at):
                    break
                self.endpointer.reset()
                self.busy = False
        except SynthesisError as error:
            self.channel.fail(call.call_id, str(error))
        finally:
            reader.cancel()
            workdir.cleanup()
            self._save(call.call_id)
            await self._send(room, type="ended", reason=self.channel.calls[call.call_id].end_reason)
            await room.disconnect()
        return call.call_id

    async def _read(self, stream, queue: asyncio.Queue) -> None:
        async for event in stream:
            if self.speaking or self.busy:
                continue
            pcm = bytes(event.frame.data)
            utterance = self.endpointer.push(pcm)
            if utterance is not None:
                self.busy = True
                queue.put_nowait(("utterance", utterance, time.perf_counter()))
            elif self.endpointer.idle_ms >= SILENCE_TIMEOUT_S * 1000:
                self.busy = True
                queue.put_nowait(("silence", None, time.perf_counter()))
        queue.put_nowait(("hangup", None, 0.0))

    async def _answer(self, room, utterance: Utterance, workdir: str, n: int) -> CallTurn | None:
        clip = write_wav(Path(workdir) / f"utterance-{n:03d}.wav", utterance.pcm, IN_RATE)
        turn = await asyncio.to_thread(
            self.channel.hear, self.call_id, clip, float(utterance.trailing_silence_ms)
        )
        if turn is not None:
            await self._send(
                room,
                type="heard",
                transcript=turn.transcript,
                intent=turn.intent,
                escalated=turn.escalated,
            )
        return turn

    async def _reply(self, rtc, room, source, turn: CallTurn, decided_at: float) -> bool:
        """Say the turn's reply; True when the call is over."""
        await self._send(room, type="agent", text=turn.say)
        timed = turn.transcript is not None or turn.intent is not None

        async def started(first_ms: float) -> None:
            # Reported the moment the reply is audible, not after it has finished: the
            # turn time ends where the customer starts hearing an answer.
            if timed:
                self.channel.spoke(self.call_id, turn.timing, first_ms)
                await self._send(
                    room,
                    type="timing",
                    **turn.timing.as_dict(),
                    wall_ms=round((time.perf_counter() - decided_at) * 1000, 1),
                )

        await self._say(rtc, source, turn.say, on_first_audio=started)
        return turn.ends_call or self.gone

    async def _say(self, rtc, source, text: str, on_first_audio=None) -> float:
        """Play `text`, sentence by sentence; return milliseconds to the first audio.

        Stops early if the customer has left: speaking a long answer into an empty room
        is time the agent cannot spend on the next call.
        """
        self.speaking = True
        started = time.perf_counter()
        first_ms: float | None = None
        chunks = iter(self.synthesizer.synthesize(text))
        samples_per_frame = OUT_RATE // 100  # 10 ms
        step = samples_per_frame * 2
        try:
            while not self.gone:
                chunk = await asyncio.to_thread(next, chunks, None)
                if chunk is None:
                    break
                pcm = resample(chunk.pcm, chunk.sample_rate, OUT_RATE)
                for offset in range(0, len(pcm), step):
                    if self.gone:
                        break
                    piece = pcm[offset : offset + step].ljust(step, b"\x00")
                    await source.capture_frame(
                        rtc.AudioFrame(piece, OUT_RATE, 1, samples_per_frame)
                    )
                    if first_ms is None:
                        first_ms = (time.perf_counter() - started) * 1000
                        if on_first_audio is not None:
                            await on_first_audio(first_ms)
            if not self.gone:
                await source.wait_for_playout()
        finally:
            self.speaking = False
        return first_ms or 0.0

    async def _send(self, room, **message: object) -> None:
        # The data channel is the call screen's convenience, not the record. A lost
        # message must not end a call the customer is still on.
        with contextlib.suppress(Exception):
            await room.local_participant.publish_data(
                json.dumps(message, ensure_ascii=False).encode(), topic=DATA_TOPIC
            )

    def _save(self, call_id: str) -> Path:
        self.records_dir.mkdir(parents=True, exist_ok=True)
        path = self.records_dir / f"{call_id}.json"
        path.write_text(
            json.dumps(self.channel.record(call_id), ensure_ascii=False, indent=2, default=str)
        )
        return path
