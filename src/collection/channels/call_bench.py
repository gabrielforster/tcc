"""The turn-time budget: where a call's silence between two speakers goes.

Measured, per spec decision 8, on synthesised speech rather than recordings: the customer's
lines in `knowledge/voice-messages.json` are spoken by a second Piper voice, so anyone with
the `call` and `voice` extras can reproduce the table and the repository holds no
customer-shaped audio. Each line is answered on a fresh call -- a dispute or an opt-out
ends the call it is said on, and the next line must not inherit that.

Two modes, and they answer different questions:

* **Pipeline** (default) runs the stages in this process, in order, and times each:
  endpointing (the hangover the endpointer waited, read off the audio clock), local
  Whisper, the agent through the orchestrator, and the synthesiser's first chunk. It says
  which stage to attack.
* **Live** (`--live`) puts the same lines through a real LiveKit room: a bot participant
  publishes the speech over WebRTC and listens for the agent's first audible frame. Its
  end-to-end number includes what the pipeline cannot see -- Opus encoding, the jitter
  buffer, the room hop, playout -- and is the one a caller would feel.

Synthesised speech is cleaner than a person on a headset, so the recognition term here is
a floor, not an estimate; `docs/voice-call.md` says so beside the table.
"""

import asyncio
import os
import platform
import re
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from collection.agents.spoken import sentences_of
from collection.channels.endpointing import FRAME_MS, THRESHOLD_DBFS, Endpointer, frame_dbfs
from collection.channels.voice_call import REPLY_MAX_WORDS, REPLY_TARGET_S, VoiceCallChannel
from collection.rag.index import Retrieved, VectorIndex
from collection.voice.evaluate import VoiceMessage, load_messages, wer
from collection.voice.speak import (
    SLOWEST_WORDS_PER_SECOND,
    Synthesizer,
    first_audio,
    resample,
    speaking_rates,
    spoken_words,
    write_wav,
)

RATE = 16_000
LEAD_SILENCE_S = 0.3
TAIL_SILENCE_S = 1.5
#: An agent frame louder than this, after the customer stopped, is the reply arriving.
AUDIBLE_DBFS = -50.0


@dataclass
class BenchRow:
    id: str
    reference: str
    expected_intent: str
    audio_s: float
    transcript: str = ""
    intent: str | None = None
    wer: float = 0.0
    end_reason: str | None = None
    endpointing_ms: float = 0.0
    stt_ms: float = 0.0
    agent_ms: float = 0.0
    tts_first_audio_ms: float = 0.0
    #: How long the spoken reply lasts. Pipeline mode only: it synthesises the whole reply.
    reply_s: float | None = None
    reply_words: int | None = None
    #: Live mode only: end of the bot's speech to the first audible agent frame.
    live_ms: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return self.endpointing_ms + self.stt_ms + self.agent_ms + self.tts_first_audio_ms


def speak_as_customer(voice: Synthesizer, text: str) -> bytes:
    """The line as 16 kHz mono PCM, with a little silence before and after it."""
    chunks = list(voice.synthesize(text))
    speech = b"".join(resample(c.pcm, c.sample_rate, RATE) for c in chunks)
    return (
        b"\x00\x00" * int(LEAD_SILENCE_S * RATE) + speech + b"\x00\x00" * int(TAIL_SILENCE_S * RATE)
    )


def spoken_wer(reference: str, transcript: str) -> float:
    """Word error rate with punctuation removed from the transcript first.

    Whisper punctuates and the labelled references do not, so "boleto." against "boleto"
    would count as a miss the classifier -- which tokenises on words -- never experiences.
    Digits are left alone on purpose: "6" for "seis" is a real difference downstream,
    because the figure read-back and the instalment parser see it.
    """
    if not transcript:
        return 1.0
    return wer(reference, re.sub(r"[^\w\s]", " ", transcript))


def trailing_silence_s(pcm: bytes, rate: int) -> float:
    """Seconds of below-threshold audio at the end of a clip."""
    step = rate * FRAME_MS // 1000 * 2
    quiet = 0
    for end in range(len(pcm), 0, -step):
        if frame_dbfs(pcm[max(0, end - step) : end]) >= THRESHOLD_DBFS:
            break
        quiet += 1
    return quiet * FRAME_MS / 1000


def endpoint(pcm: bytes) -> tuple[bytes, float]:
    """Run the endpointer over the audio; the utterance and the hangover it waited."""
    endpointer = Endpointer(sample_rate=RATE)
    step = RATE * FRAME_MS // 1000 * 2
    for offset in range(0, len(pcm), step):
        utterance = endpointer.push(pcm[offset : offset + step])
        if utterance is not None:
            return utterance.pcm, float(utterance.trailing_silence_ms)
    raise RuntimeError("the endpointer never closed the turn; is the tail silence too short?")


def run_pipeline(
    channel: VoiceCallChannel,
    customer_voice: Synthesizer,
    agent_voice: Synthesizer,
    messages: list[VoiceMessage] | None = None,
    warmup: bool = True,
) -> list[BenchRow]:
    messages = messages if messages is not None else load_messages()
    rows: list[BenchRow] = []
    with tempfile.TemporaryDirectory(prefix="call-bench-") as workdir:
        if warmup and messages:
            # Whisper's first call pays for graph setup; a caller would not, because the
            # server loads the model at startup. One untimed turn takes that out.
            _turn(channel, agent_voice, customer_voice, messages[0], Path(workdir), "warmup")
        for message in messages:
            rows.append(
                _turn(channel, agent_voice, customer_voice, message, Path(workdir), message.id)
            )
    return rows


def _turn(
    channel: VoiceCallChannel,
    agent_voice: Synthesizer,
    customer_voice: Synthesizer,
    message: VoiceMessage,
    workdir: Path,
    stem: str,
) -> BenchRow:
    audio = speak_as_customer(customer_voice, message.text)
    utterance, endpointing_ms = endpoint(audio)
    clip = write_wav(workdir / f"{stem}.wav", utterance, RATE)
    row = BenchRow(
        id=message.id,
        reference=message.text,
        expected_intent=message.intent,
        audio_s=len(utterance) / 2 / RATE,
    )

    call = channel.start()
    channel.opening_played(call.call_id)
    turn = channel.hear(call.call_id, clip, endpointing_ms=endpointing_ms)
    if turn is None:
        row.notes.append("no turn")
        return row
    chunks, first_ms = first_audio(agent_voice, turn.say)
    row.reply_s = sum(chunk.duration_s for chunk in chunks)
    row.reply_words = spoken_words(turn.say)
    channel.spoke(call.call_id, turn.timing, first_ms)
    channel.hang_up(call.call_id)

    row.transcript = turn.transcript or ""
    row.intent = turn.intent
    row.wer = spoken_wer(message.text, row.transcript)
    row.end_reason = turn.end_reason
    row.endpointing_ms = turn.timing.endpointing_ms
    row.stt_ms = turn.timing.stt_ms
    row.agent_ms = turn.timing.agent_ms
    row.tts_first_audio_ms = first_ms
    return row


# ---------------------------------------------------------------------- live
def run_live(
    channel: VoiceCallChannel,
    customer_voice: Synthesizer,
    agent_voice: Synthesizer,
    messages: list[VoiceMessage] | None = None,
) -> list[BenchRow]:
    messages = messages if messages is not None else load_messages()
    return asyncio.run(_live_all(channel, customer_voice, agent_voice, messages))


async def _live_all(channel, customer_voice, agent_voice, messages) -> list[BenchRow]:
    rows = []
    for message in messages:
        rows.append(await _live_turn(channel, customer_voice, agent_voice, message))
    return rows


async def _live_turn(channel, customer_voice, agent_voice, message: VoiceMessage) -> BenchRow:
    import json

    from livekit import rtc

    from collection.channels.livekit_call import (
        DATA_TOPIC,
        OUT_RATE,
        CallAgent,
        new_room_name,
        room_token,
    )

    room_name = new_room_name()
    agent = CallAgent(channel, agent_voice, room_name)
    agent_task = asyncio.create_task(agent.run())

    bot = rtc.Room()
    listening = asyncio.Event()
    timed: asyncio.Future = asyncio.get_running_loop().create_future()
    heard: dict[str, object] = {}
    spoke_at: list[float] = []
    speech_done_at: list[float] = []

    def on_data(packet) -> None:
        if packet.topic != DATA_TOPIC:
            return
        payload = json.loads(packet.data.decode())
        if payload.get("type") == "listening":
            listening.set()
        elif payload.get("type") == "heard":
            heard.update(payload)
        elif payload.get("type") == "timing" and not timed.done():
            timed.set_result(payload)
        elif payload.get("type") == "ended" and not timed.done():
            timed.set_result({})

    async def watch(track) -> None:
        stream = rtc.AudioStream(track, sample_rate=RATE, num_channels=1, frame_size_ms=FRAME_MS)
        async for event in stream:
            if not speech_done_at or spoke_at:
                continue
            if frame_dbfs(bytes(event.frame.data)) > AUDIBLE_DBFS:
                spoke_at.append(time.perf_counter())

    watchers: list[asyncio.Task] = []

    def on_track(track, publication, participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            watchers.append(asyncio.create_task(watch(track)))

    bot.on("data_received", on_data)
    bot.on("track_subscribed", on_track)
    await bot.connect(agent.url, room_token(f"bench-{room_name}", room_name))
    source = rtc.AudioSource(OUT_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("bench-voice", source)
    await bot.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )

    samples = OUT_RATE // 100
    silence_frame = rtc.AudioFrame(b"\x00\x00" * samples, OUT_RATE, 1, samples)
    stop_silence = asyncio.Event()

    async def keep_line_open() -> None:
        # A microphone does not go quiet by stopping; it keeps sending silence, and the
        # endpointer needs those frames to time the hangover.
        while not stop_silence.is_set():
            await source.capture_frame(silence_frame)

    row = BenchRow(
        id=message.id, reference=message.text, expected_intent=message.intent, audio_s=0.0
    )
    filler = asyncio.create_task(keep_line_open())
    try:
        await asyncio.wait_for(listening.wait(), 120)
        stop_silence.set()
        await filler
        chunks = customer_voice.synthesize(message.text)
        speech = b"".join(resample(c.pcm, c.sample_rate, OUT_RATE) for c in chunks)
        row.audio_s = len(speech) / 2 / OUT_RATE
        for offset in range(0, len(speech), samples * 2):
            piece = speech[offset : offset + samples * 2].ljust(samples * 2, b"\x00")
            await source.capture_frame(rtc.AudioFrame(piece, OUT_RATE, 1, samples))
        await source.wait_for_playout()
        # The clock starts at the last *voiced* frame, as the endpointer's does: Piper pads
        # its output with silence, and counting that pad would credit the network with
        # part of the hangover.
        speech_done_at.append(time.perf_counter() - trailing_silence_s(speech, OUT_RATE))
        stop_silence.clear()
        filler = asyncio.create_task(keep_line_open())
        timing = await asyncio.wait_for(timed, 120)
        # The reply's first audible frame can land a moment after the timing message.
        for _ in range(100):
            if spoke_at:
                break
            await asyncio.sleep(0.05)
    except TimeoutError:
        row.notes.append("timed out")
        timing = {}
    finally:
        stop_silence.set()
        filler.cancel()
        await bot.disconnect()
        for watcher in watchers:
            watcher.cancel()
        await asyncio.wait_for(agent_task, 60)

    row.transcript = str(heard.get("transcript") or "")
    row.intent = heard.get("intent")  # type: ignore[assignment]
    row.wer = spoken_wer(message.text, row.transcript)
    row.endpointing_ms = float(timing.get("endpointing_ms") or 0.0)
    row.stt_ms = float(timing.get("stt_ms") or 0.0)
    row.agent_ms = float(timing.get("agent_ms") or 0.0)
    row.tts_first_audio_ms = float(timing.get("tts_first_audio_ms") or 0.0)
    if spoke_at and speech_done_at:
        row.live_ms = (spoke_at[0] - speech_done_at[0]) * 1000
    if agent.call_id:
        row.end_reason = channel.calls[agent.call_id].end_reason
    return row


# -------------------------------------------------------------------- report
STAGES = [
    ("endpointing_ms", "End-of-turn wait (hangover)"),
    ("stt_ms", "Local Whisper"),
    ("agent_ms", "Agent (intent, retrieval, responder)"),
    ("tts_first_audio_ms", "Piper, first audio"),
    ("total_ms", "**Sum of stages**"),
]


@dataclass(frozen=True)
class SpeakingRate:
    """How fast the agent's voice says the knowledge base, which the word budget rests on."""

    sentences: int
    pooled: float
    slowest: float


def corpus_speaking_rate(voice: Synthesizer, index: VectorIndex) -> SpeakingRate:
    """Re-measure the rate `SLOWEST_WORDS_PER_SECOND` was set from, on every sentence the
    responder could speak, so the report shows whether the constant still holds."""
    seen: dict[str, None] = {}
    for chunk in index.chunks:
        for sentence in sentences_of(Retrieved(chunk=chunk, score=1.0)):
            if len(sentence.text.split()) > 3:
                seen.setdefault(sentence.text, None)
    sentences = list(seen)
    rates = speaking_rates(voice, sentences)
    seconds = sum(spoken_words(s) / r for s, r in zip(sentences, rates, strict=True))
    words = sum(spoken_words(s) for s in sentences)
    return SpeakingRate(sentences=len(rates), pooled=words / seconds, slowest=min(rates))


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def summarise(rows: list[BenchRow]) -> list[tuple[str, float, float, float]]:
    out = []
    for key, label in STAGES:
        values = [getattr(row, key) for row in rows]
        out.append((label, statistics.median(values), _quantile(values, 0.9), max(values)))
    live = [row.live_ms for row in rows if row.live_ms is not None]
    if live:
        out.append(
            (
                "**Measured over WebRTC** (end of speech to first audible reply)",
                statistics.median(live),
                _quantile(live, 0.9),
                max(live),
            )
        )
    return out


def _cpu() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def write_report(
    rows: list[BenchRow],
    path: Path | str,
    whisper_model: str,
    mode: str,
    rate: SpeakingRate | None = None,
) -> str:
    audio = [row.audio_s for row in rows]
    stt_rtf = [row.stt_ms / 1000 / row.audio_s for row in rows if row.audio_s]
    intents_ok = sum(1 for row in rows if row.intent == row.expected_intent)
    lines = [
        "# Voice call: the turn-time budget",
        "",
        "Generated by `make call-bench"
        + (' ARGS="--live"`' if mode == "live" else "`")
        + f" on {date.today().isoformat()}. Do not edit by hand.",
        "",
        f"- **Mode:** {mode}. "
        + (
            "Each line spoken by a bot participant into a self-hosted LiveKit room over WebRTC."
            if mode == "live"
            else "Stages run in-process, in order; no network."
        ),
        f"- **Machine:** {_cpu()}, {os.cpu_count()} threads, CPU only.",
        f"- **Recogniser:** faster-whisper `{whisper_model}`, int8, beam 5, local.",
        "- **Voices:** Piper `pt_BR-cadu-medium` speaks the customer, "
        "`pt_BR-faber-medium` the agent.",
        "- **Responder:** template (no API key), so the agent term carries no generation.",
        f"- **Lines:** {len(rows)} from `knowledge/voice-messages.json`, one fresh call each; "
        f"median {statistics.median(audio):.1f} s of speech.",
        "",
        "## Where the time goes (milliseconds, from the end of the customer's speech)",
        "",
        "| Stage | Median | p90 | Max |",
        "|---|---:|---:|---:|",
    ]
    for label, median, p90, worst in summarise(rows):
        lines.append(f"| {label} | {median:,.0f} | {p90:,.0f} | {worst:,.0f} |")
    lines += [
        "",
        f"Whisper real-time factor: median {statistics.median(stt_rtf):.2f} "
        "(seconds of compute per second of speech).",
        f"Intent on the transcript matched the label on {intents_ok}/{len(rows)} lines; "
        f"median WER {statistics.median([r.wer for r in rows]):.3f}.",
    ]
    replies = [row.reply_s for row in rows if row.reply_s is not None]
    if replies:
        within = sum(1 for reply in replies if reply <= REPLY_TARGET_S)
        words = [row.reply_words for row in rows if row.reply_words is not None]
        lines += [
            f"Spoken reply length: median {statistics.median(replies):.1f} s, "
            f"max {max(replies):.1f} s -- the time the customer then spends listening. "
            f"{within}/{len(replies)} within the {REPLY_TARGET_S:.0f} s target; "
            f"median {statistics.median(words):.0f} spoken words against a budget of "
            f"{REPLY_MAX_WORDS}.",
        ]
    if rate is not None:
        drift = "holds" if rate.slowest >= SLOWEST_WORDS_PER_SECOND else "**is now too fast**"
        lines.append(
            f"Agent voice on the corpus ({rate.sentences} sentences): {rate.pooled:.2f} words/s "
            f"pooled, {rate.slowest:.2f} slowest; the budget's constant "
            f"({SLOWEST_WORDS_PER_SECOND:.2f}) {drift}."
        )
    lines += [
        "",
        "## Per line",
        "",
        "| Line | Speech s | Reply s | Endpoint | STT | Agent | TTS first | Sum"
        + (" | WebRTC" if mode == "live" else "")
        + " | WER | Intent (expected) | Ended |",
        "|---|---:|---:|---:|---:|---:|---:|---:"
        + ("|---:" if mode == "live" else "")
        + "|---:|---|---|",
    ]
    for row in rows:
        live = (
            f" | {row.live_ms:,.0f}"
            if mode == "live" and row.live_ms is not None
            else (" | --" if mode == "live" else "")
        )
        intent = row.intent or "--"
        if row.intent != row.expected_intent:
            intent = f"**{intent}** ({row.expected_intent})"
        lines.append(
            f"| {row.id} | {row.audio_s:.1f} "
            f"| {'--' if row.reply_s is None else f'{row.reply_s:.0f}'} "
            f"| {row.endpointing_ms:,.0f} | {row.stt_ms:,.0f} "
            f"| {row.agent_ms:,.0f} | {row.tts_first_audio_ms:,.0f} | {row.total_ms:,.0f}{live} "
            f"| {row.wer:.2f} | {intent} | {row.end_reason or '--'} |"
        )
    lines += ["", "## What the recogniser heard", "", "| Line | Said | Heard |", "|---|---|---|"]
    for row in rows:
        lines.append(f"| {row.id} | {row.reference} | {row.transcript or '--'} |")
    text = "\n".join(lines) + "\n"
    Path(path).write_text(text, encoding="utf-8")
    return text
