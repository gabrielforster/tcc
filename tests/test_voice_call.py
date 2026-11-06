"""The voice call: the notice nobody can skip, the two records, and the ways a call ends.

No LiveKit, no Whisper, no voice model: the channel is transport-free, so these tests drive
it with WAV files and the scripted recogniser, the way the chat tests drive the chat. The
transport (`livekit_call.py`) is a thin loop around what is tested here, and what it adds
-- WebRTC, a microphone -- is not something a unit test can honestly stand in for.

The tests that route a clip need ffprobe, because the call reuses the chat's voice-note
path and that path refuses a clip it cannot measure. They skip where it is absent, as the
chat's audio tests do; CI installs it.
"""

import math
import struct
from pathlib import Path

import pytest

from collection.agents.responsive import TemplateResponder
from collection.channels.base import Channel
from collection.channels.endpointing import Endpointer, frame_dbfs
from collection.channels.voice_call import (
    MAX_SILENCE_PROMPTS,
    RECORDING_NOTICE,
    SILENCE_GOODBYE,
    SILENCE_PROMPT,
    CallEnded,
    CallState,
    VoiceCallChannel,
)
from collection.channels.web import GREETING, HANDOVER_NOTICE
from collection.voice.audio import ffmpeg_available
from collection.voice.speak import ToneSynthesizer, first_audio, resample, write_wav
from collection.voice.transcribe import ScriptedTranscriber

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not installed")

RATE = 16_000
FRAME = RATE // 50  # 20 ms


def tone(seconds: float, amplitude: int = 8000) -> bytes:
    samples = int(seconds * RATE)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * i / RATE)))
        for i in range(samples)
    )


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * RATE)


def frames(pcm: bytes) -> list[bytes]:
    step = FRAME * 2
    return [pcm[i : i + step] for i in range(0, len(pcm), step)]


def clip(tmp_path: Path, stem: str, seconds: float = 1.0) -> Path:
    return write_wav(tmp_path / f"{stem}.wav", tone(seconds), RATE)


TRANSCRIPTS = {
    "segunda_via": "preciso da segunda via do boleto",
    "parcelar": "quero parcelar a minha dívida em seis vezes",
    "contestar": "essa dívida não é minha eu nunca contratei esse serviço",
    "parar": "parem de me ligar não quero mais receber cobrança",
}


@pytest.fixture
def channel(index, tmp_path):
    return VoiceCallChannel(
        retriever=index,
        responder=TemplateResponder(),
        transcriber=ScriptedTranscriber(transcripts=TRANSCRIPTS),
        audio_dir=tmp_path / "calls",
    )


def listening(channel):
    call = channel.start()
    channel.opening_played(call.call_id)
    return call


# -------------------------------------------------------------------- contract
def test_the_voice_channel_satisfies_the_contract(channel):
    assert isinstance(channel, Channel)
    assert channel.name == "voice"
    assert channel.capabilities.realtime
    assert not channel.capabilities.accepts_text


# ---------------------------------------------------------------- the opening
def test_the_recording_notice_is_the_first_thing_said(channel):
    call = channel.start()
    assert call.opening[0] == RECORDING_NOTICE
    assert call.opening[1] == GREETING
    assert channel.record(call.call_id)["transcript"][0]["text"] == RECORDING_NOTICE


def test_speech_during_the_notice_is_not_heard(channel, tmp_path):
    call = channel.start()
    assert call.state is CallState.OPENING
    assert channel.hear(call.call_id, clip(tmp_path, "segunda_via")) is None
    # Nothing was routed, nothing was kept: the customer had not been told yet.
    assert channel.transcriber.inner.calls == []
    assert call.clips == []
    assert [e.kind for e in call.events][-1] == "speech_during_notice_ignored"


def test_silence_during_the_notice_does_not_count_either(channel):
    call = channel.start()
    assert channel.silence(call.call_id) is None
    assert call.silence_prompts == 0


def test_a_message_held_for_the_customer_follows_the_notice(channel):
    channel.deliver("cus_7", "Sua fatura de março venceu.")
    from collection.channels.session import issue_token

    call = channel.start(issue_token("cus_7"))
    assert call.opening == [RECORDING_NOTICE, GREETING, "Sua fatura de março venceu."]


def test_a_held_message_is_dropped_for_a_customer_who_opted_out(channel):
    from collection.channels.session import issue_token

    channel.deliver("cus_8", "Sua fatura venceu.")
    channel.opted_out.add("cus_8")
    assert channel.start(issue_token("cus_8")).opening == [RECORDING_NOTICE, GREETING]


# -------------------------------------------------------------------- turns
@needs_ffmpeg
def test_a_turn_is_answered_through_the_agent(channel, tmp_path, monkeypatch):
    from collection.agents.orchestrator import Orchestrator

    submitted = []
    original = Orchestrator.submit

    def spy(self, event):
        submitted.append(event)
        return original(self, event)

    monkeypatch.setattr(Orchestrator, "submit", spy)
    call = listening(channel)
    turn = channel.hear(call.call_id, clip(tmp_path, "segunda_via"), endpointing_ms=700)
    assert turn is not None and not turn.ends_call
    assert turn.transcript == TRANSCRIPTS["segunda_via"]
    assert turn.intent == "second_copy"
    assert turn.say
    assert turn.timing.endpointing_ms == 700
    assert turn.timing.stt_ms is not None
    # The turn went through an orchestrator, as audio, carrying the call's own label.
    assert [e.type.value for e in submitted] == ["audio_received"]
    assert submitted[0].payload["channel"] == "voice"


@needs_ffmpeg
def test_a_spoken_figure_is_read_back_on_a_call_too(channel, tmp_path):
    call = listening(channel)
    turn = channel.hear(call.call_id, clip(tmp_path, "parcelar"))
    assert turn.needs_confirmation
    assert turn.say.startswith("Entendi:")


@needs_ffmpeg
def test_a_dispute_ends_the_call_with_a_handover(channel, tmp_path):
    call = listening(channel)
    turn = channel.hear(call.call_id, clip(tmp_path, "contestar"))
    assert turn.ends_call and turn.end_reason == "handover"
    assert turn.say == HANDOVER_NOTICE
    assert call.state is CallState.ENDED
    with pytest.raises(CallEnded):
        channel.hear(call.call_id, clip(tmp_path, "segunda_via"))


@needs_ffmpeg
def test_an_opt_out_ends_the_call_and_sticks(channel, tmp_path):
    call = listening(channel)
    turn = channel.hear(call.call_id, clip(tmp_path, "parar"))
    assert turn.ends_call and turn.end_reason == "opt_out"
    assert turn.intent == "opt_out"
    assert call.customer_id in channel.opted_out
    # The record says what the customer said, not that they sent some audio.
    transcript = channel.record(call.call_id)["transcript"]
    said = [turn["text"] for turn in transcript if turn["role"] == "customer"]
    assert said == [TRANSCRIPTS["parar"]]


@needs_ffmpeg
def test_unintelligible_audio_is_handed_to_a_person(channel, tmp_path):
    call = listening(channel)
    # The scripted recogniser has no transcript for this clip: recognition failed.
    turn = channel.hear(call.call_id, clip(tmp_path, "ruido"))
    assert turn.ends_call and turn.end_reason == "handover"
    assert turn.escalation_reason == "audio_unintelligible"


@needs_ffmpeg
def test_a_click_is_not_a_turn(channel, tmp_path):
    call = listening(channel)
    short = clip(tmp_path, "segunda_via", seconds=0.1)
    assert channel.hear(call.call_id, short) is None
    assert call.state is CallState.LISTENING
    assert [e.kind for e in call.events][-1] == "utterance_rejected"


# ------------------------------------------------------------------ silence
def test_a_quiet_line_is_asked_once_then_hung_up(channel):
    call = listening(channel)
    prompts = [channel.silence(call.call_id) for _ in range(MAX_SILENCE_PROMPTS)]
    assert all(turn.say == SILENCE_PROMPT and not turn.ends_call for turn in prompts)
    final = channel.silence(call.call_id)
    assert final.ends_call and final.end_reason == "silence_timeout"
    assert final.say == SILENCE_GOODBYE
    assert channel.record(call.call_id)["transcript"][-1]["text"] == SILENCE_GOODBYE


def test_a_call_past_its_limit_ends_on_the_next_silence(index, tmp_path):
    now = [1000.0]
    channel = VoiceCallChannel(
        retriever=index, responder=TemplateResponder(), audio_dir=tmp_path, clock=lambda: now[0]
    )
    call = listening(channel)
    now[0] += 3600
    assert channel.silence(call.call_id).end_reason == "max_duration"


def test_hanging_up_is_recorded_and_not_an_error(channel):
    call = listening(channel)
    channel.hang_up(call.call_id)
    channel.hang_up(call.call_id)  # the transport may report it twice
    record = channel.record(call.call_id)
    assert record["end_reason"] == "customer_hung_up"
    assert [e["kind"] for e in record["events"]].count("call_ended") == 1


# ------------------------------------------------------------------ records
@needs_ffmpeg
def test_the_call_record_is_complete(channel, tmp_path):
    call = listening(channel)
    turn = channel.hear(call.call_id, clip(tmp_path, "segunda_via"), endpointing_ms=700)
    channel.spoke(call.call_id, turn.timing, first_audio_ms=80.0)
    channel.hang_up(call.call_id)

    record = channel.record(call.call_id)
    kinds = [event["kind"] for event in record["events"]]
    assert kinds == [
        "call_started",
        "notice_playing",
        "notice_played",
        "turn",
        "turn_timed",
        "call_ended",
    ]
    roles = [turn["role"] for turn in record["transcript"]]
    assert roles == ["agent", "agent", "customer", "agent"]
    assert record["transcript"][2]["text"] == TRANSCRIPTS["segunda_via"]
    assert record["timings"][0]["tts_first_audio_ms"] == 80.0
    assert len(record["clips"]) == 1 and Path(record["clips"][0]).exists()


@needs_ffmpeg
def test_erasing_a_customers_audio_keeps_the_transcript(channel, tmp_path):
    call = listening(channel)
    channel.hear(call.call_id, clip(tmp_path, "segunda_via"))
    channel.hang_up(call.call_id)
    before = channel.record(call.call_id)["transcript"]

    assert channel.erase_audio(call.customer_id) == 1
    record = channel.record(call.call_id)
    assert record["clips"] == []
    assert not (channel.audio_dir / call.customer_id).exists()
    assert record["transcript"] == before
    assert record["events"][-1]["kind"] == "audio_erased"


# ---------------------------------------------------------------- endpointing
def test_silence_is_quieter_than_the_threshold_and_speech_is_not():
    assert frame_dbfs(silence(0.02)) < -90
    assert frame_dbfs(tone(0.02)) > -20


def test_a_turn_ends_after_the_hangover_and_not_before():
    endpointer = Endpointer(hangover_ms=700)
    results = [endpointer.push(f) for f in frames(tone(1.0) + silence(0.68))]
    assert all(result is None for result in results)
    utterance = None
    for f in frames(silence(0.1)):
        utterance = utterance or endpointer.push(f)
    assert utterance is not None
    assert utterance.ended_by == "silence"
    assert utterance.trailing_silence_ms == 700
    assert 990 <= utterance.speech_ms <= 1010


def test_a_click_is_not_an_utterance():
    endpointer = Endpointer()
    pushed = [endpointer.push(f) for f in frames(tone(0.06) + silence(1.0))]
    assert all(result is None for result in pushed)


def test_a_monologue_is_cut_at_the_limit():
    endpointer = Endpointer(max_utterance_s=2.0)
    cut = [u for u in (endpointer.push(f) for f in frames(tone(3.0))) if u]
    assert len(cut) == 1 and cut[0].ended_by == "max_length"


def test_idle_time_counts_only_silence_without_speech():
    endpointer = Endpointer()
    for f in frames(silence(1.0)):
        endpointer.push(f)
    assert endpointer.idle_ms == 1000
    endpointer.push(frames(tone(0.02))[0])
    endpointer.reset()
    assert endpointer.idle_ms == 0


# --------------------------------------------------------------- synthesis
def test_the_tone_synthesizer_speaks_sentence_by_sentence():
    synthesizer = ToneSynthesizer()
    chunks, first_ms = first_audio(synthesizer, "Primeira frase. Segunda frase.")
    assert len(chunks) == 2 and first_ms >= 0
    assert synthesizer.spoken == ["Primeira frase. Segunda frase."]


def test_resampling_keeps_the_duration():
    pcm = tone(1.0)
    out = resample(pcm, 16_000, 48_000)
    assert len(out) // 2 == 48_000
