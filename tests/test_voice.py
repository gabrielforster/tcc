"""Voice notes: what the system accepts, what it hears, and what it refuses to believe.

The theme is distrust, and it is narrower than it sounds. The agent trusts a transcript
enough to route on it -- intent survives a mangled sentence -- and refuses to trust it
enough to act on a figure in it, because a misheard amount reads exactly like a correct
one. The sharpest tests here are the two sides of that: `test_intent_survives_corruption_
that_destroys_figures` is the measurement, and
`test_a_spoken_figure_is_read_back_before_anything_is_done_with_it` is what the system
does about it.
"""

import random
import shutil
from pathlib import Path

import pytest

from collection.agents.events import Event, EventType
from collection.agents.responsive import CONFIRM_PREFIX, ResponsiveAgent
from collection.config import settings
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex
from collection.voice.audio import (
    MAX_DURATION_S,
    MIN_DURATION_S,
    AudioClip,
    AudioError,
    accept,
    ffmpeg_available,
    probe,
    to_wav,
)
from collection.voice.degrade import degrade
from collection.voice.evaluate import (
    RATES,
    cer,
    figure_survival,
    load_messages,
    run,
    sweep,
    wer,
)
from collection.voice.transcribe import (
    MIN_CONFIDENCE,
    ScriptedTranscriber,
    Transcription,
    TranscriptionError,
    get_transcriber,
    needs_confirmation,
    spoken_figures,
)

FIXTURE = Path(__file__).parent / "fixtures" / "voice-note.ogg"
needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def index() -> VectorIndex:
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()


@pytest.fixture(scope="module")
def messages():
    return load_messages()


def _clip(**kwargs) -> AudioClip:
    defaults = {
        "path": FIXTURE,
        "codec": "opus",
        "duration_s": 4.0,
        "sample_rate": 48_000,
        "channels": 1,
    }
    return AudioClip(**{**defaults, **kwargs})


def _audio_event(path: Path | str = FIXTURE, **payload) -> Event:
    return Event(
        type=EventType.AUDIO_RECEIVED,
        customer_id="cus_voice",
        payload={"audio_path": str(path), **payload},
    )


# ------------------------------------------------------------------ the file itself
@needs_ffmpeg
def test_an_ogg_opus_voice_note_is_read_without_being_decoded():
    clip = probe(FIXTURE)
    assert clip.codec == "opus"
    assert clip.channels == 1
    assert 1.0 < clip.duration_s < 2.0


@needs_ffmpeg
def test_decoding_produces_the_sixteen_kilohertz_mono_every_recogniser_wants(tmp_path):
    wav = to_wav(probe(FIXTURE), tmp_path / "clip.wav")
    decoded = probe(wav)
    assert decoded.sample_rate == 16_000
    assert decoded.channels == 1


def test_a_container_nobody_sends_is_refused_before_any_model_loads(tmp_path):
    fake = tmp_path / "note.aiff"
    fake.write_bytes(b"not audio")
    with pytest.raises(AudioError, match="unsupported container"):
        probe(fake)


def test_a_missing_file_is_an_audio_error_not_a_crash():
    with pytest.raises(AudioError, match="no such audio"):
        probe("/nonexistent/voice.ogg")


@needs_ffmpeg
def test_a_misfired_button_is_not_a_message(tmp_path, monkeypatch):
    import collection.voice.audio as audio_module

    monkeypatch.setattr(audio_module, "probe", lambda path: _clip(duration_s=0.1))
    with pytest.raises(AudioError, match="not a message"):
        audio_module.accept(FIXTURE)


def test_a_clip_longer_than_the_limit_goes_to_a_person(monkeypatch):
    import collection.voice.audio as audio_module

    monkeypatch.setattr(audio_module, "probe", lambda path: _clip(duration_s=MAX_DURATION_S + 1))
    with pytest.raises(AudioError, match="longer than"):
        audio_module.accept(FIXTURE)


@needs_ffmpeg
def test_accept_returns_the_clip_when_the_length_is_reasonable():
    assert accept(FIXTURE).codec == "opus"


def test_ffmpeg_is_probed_rather_than_imported():
    assert ffmpeg_available() == (
        shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
    )


# ----------------------------------------------------------------- the transcriber
def test_the_control_transcriber_returns_the_labelled_transcript():
    transcriber = ScriptedTranscriber(transcripts={"voice-note": "quero parcelar"})
    result = transcriber.transcribe(_clip())
    assert result.text == "quero parcelar"
    assert result.usable


def test_a_clip_with_no_scripted_transcript_is_an_error_not_an_empty_string():
    with pytest.raises(TranscriptionError, match="no scripted transcript"):
        ScriptedTranscriber().transcribe(_clip())


def test_an_unknown_transcriber_is_refused_by_name():
    with pytest.raises(ValueError, match="Unknown transcriber"):
        get_transcriber("vosk")


def test_the_whisper_backend_is_imported_lazily_so_a_fresh_clone_still_runs():
    # Constructing it must not import or download anything; only transcribing may.
    transcriber = get_transcriber("whisper")
    assert transcriber.name == "whisper"
    assert transcriber._model is None


def test_confidence_below_the_threshold_makes_a_transcript_unusable():
    assert not Transcription(
        text="quero parcelar", confidence=MIN_CONFIDENCE - 0.01, transcriber="x"
    ).usable
    assert Transcription(text="quero parcelar", confidence=1.0, transcriber="x").usable


def test_an_empty_transcript_is_unusable_however_confident_the_model_is():
    assert not Transcription(text="   ", confidence=1.0, transcriber="x").usable


def test_real_time_factor_is_compute_over_audio_length():
    heard = Transcription(
        text="oi", confidence=1.0, transcriber="x", latency_ms=2_000, duration_s=4.0
    )
    assert heard.real_time_factor == pytest.approx(0.5)


# --------------------------------------------------------------- figures in speech
@pytest.mark.parametrize(
    "text",
    [
        "quero parcelar em seis vezes",
        "consigo pagar mil duzentos e cinquenta reais",
        "posso pagar R$ 1.250,00 hoje",
        "da pra fazer em 12x",
    ],
)
def test_a_spoken_amount_is_detected_in_either_notation(text):
    assert needs_confirmation(text)
    assert spoken_figures(text)


def test_a_message_with_no_figure_needs_no_read_back():
    assert not needs_confirmation("quero a segunda via do boleto por favor")


# ------------------------------------------------------------------- the error model
def test_a_perfect_recogniser_changes_nothing():
    reference = "quero parcelar a minha divida em seis vezes"
    assert degrade(reference, 0.0) == reference


def test_the_error_model_is_deterministic_for_a_seed():
    reference = "consigo pagar mil duzentos e cinquenta reais a vista"
    first = degrade(reference, 0.4, random.Random(7))
    second = degrade(reference, 0.4, random.Random(7))
    assert first == second


def test_corruption_actually_costs_something_the_measures_can_see():
    # The trap this guards: most corruptions are accent loss, and every measure here
    # folds accents. If the rest of the error model degenerated into accent-stripping,
    # the sweep would report a word error rate near zero and flatter the system.
    reference = " ".join(["quero parcelar a minha divida em seis vezes"] * 4)
    heard = degrade(reference, 0.6, random.Random(3))
    assert wer(reference, heard) > 0.1


# --------------------------------------------------------------------- the measures
def test_word_error_rate_folds_accents_because_everything_downstream_does():
    assert wer("minha dívida é alta", "minha divida e alta") == 0.0
    assert cer("dívida", "divida") == 0.0


def test_word_error_rate_counts_a_real_substitution():
    assert wer("quero parcelar em seis vezes", "quero parcelar em dez vezes") == pytest.approx(0.2)


def test_a_figure_only_survives_as_a_whole_token():
    assert figure_survival(["dez"], "paguei no dia dez") == 1.0
    # `dez` inside `dezoito` is exactly the confusion the measure must not reward.
    assert figure_survival(["dez"], "paguei no dia dezoito") == 0.0


def test_a_message_with_no_figures_cannot_lose_any():
    assert figure_survival([], "quero a segunda via") == 1.0


# ----------------------------------------------------------------------- the sweep
def test_the_control_arm_reproduces_every_reference_transcript(messages):
    control = run(messages, rate=0.0)
    assert control.wer == 0.0
    assert control.intent_agreement == 1.0
    assert control.figure_survival == 1.0


def test_intent_survives_corruption_that_destroys_figures(messages):
    """The finding the module exists for.

    Intent is said several ways in one sentence and survives; an amount is said once and
    does not. Asserting the *ordering* rather than a threshold keeps the test meaningful
    when the labelled set grows -- the gap is the result, not the particular numbers.
    """
    heavy = run(messages, rate=0.40)
    assert heavy.figure_survival < 1.0
    assert heavy.intent_agreement > heavy.figure_survival


def test_more_corruption_never_reads_as_fewer_errors(messages):
    scores = [run(messages, rate=rate) for rate in (0.0, 0.2, 0.4)]
    assert scores[0].wer < scores[1].wer < scores[2].wer


def test_the_sweep_reports_one_row_per_rate(messages):
    table, results = sweep(messages, repeats=2)
    assert len(results) == len(RATES)
    assert list(table["corruption"]) == list(RATES)


# ---------------------------------------------------------------------- the agent
def test_the_agent_answers_a_voice_note_it_can_hear(index):
    agent = ResponsiveAgent(
        index,
        transcriber=ScriptedTranscriber(
            transcripts={"voice-note": "quero a segunda via do boleto"}
        ),
    )
    produced = agent.handle(_audio_event())
    assert produced[0].type is EventType.REPLY_SENT
    assert produced[0].payload["transcript"] == "quero a segunda via do boleto"
    assert produced[0].payload["transcriber"] == "scripted"


def test_a_spoken_figure_is_read_back_before_anything_is_done_with_it(index):
    transcript = "consigo pagar mil duzentos e cinquenta reais a vista"
    agent = ResponsiveAgent(
        index, transcriber=ScriptedTranscriber(transcripts={"voice-note": transcript})
    )
    reply = agent.handle(_audio_event())[0]
    assert reply.payload["needs_confirmation"] is True
    assert reply.payload["text"].startswith(CONFIRM_PREFIX.format(transcript=transcript))


def test_the_same_words_typed_are_not_read_back(index):
    """The read-back is a response to recognition risk, not to the word `reais`."""
    agent = ResponsiveAgent(index)
    typed = Event(
        type=EventType.MESSAGE_RECEIVED,
        customer_id="cus_voice",
        payload={"text": "consigo pagar mil duzentos e cinquenta reais a vista"},
    )
    reply = agent.handle(typed)[0]
    assert "needs_confirmation" not in reply.payload
    assert not reply.payload["text"].startswith("Entendi:")


def test_audio_the_model_cannot_make_out_goes_to_a_person(index):
    agent = ResponsiveAgent(
        index,
        transcriber=ScriptedTranscriber(
            transcripts={"voice-note": "quero parcelar"}, confidence=0.1
        ),
    )
    produced = agent.handle(_audio_event())
    assert produced[0].type is EventType.ESCALATED_TO_HUMAN
    assert produced[0].payload["reason"] == "audio_unintelligible"
    # The transcript still travels, so the person sees what was guessed at.
    assert produced[0].payload["transcript"] == "quero parcelar"


def test_a_deployment_with_no_recogniser_escalates_instead_of_guessing(index):
    produced = ResponsiveAgent(index).handle(_audio_event())
    assert produced[0].type is EventType.ESCALATED_TO_HUMAN
    assert produced[0].payload["reason"] == "audio_unintelligible"
    assert "no transcriber" in produced[0].payload["detail"]


def test_an_audio_event_without_a_file_is_escalated_not_crashed(index):
    event = Event(type=EventType.AUDIO_RECEIVED, customer_id="cus_voice", payload={})
    produced = ResponsiveAgent(index, transcriber=ScriptedTranscriber()).handle(event)
    assert produced[0].type is EventType.ESCALATED_TO_HUMAN
    assert "no audio_path" in produced[0].payload["detail"]


def test_an_unreadable_clip_is_escalated_with_the_reason(index):
    agent = ResponsiveAgent(index, transcriber=ScriptedTranscriber())
    produced = agent.handle(_audio_event(path="/nonexistent/voice.ogg"))
    assert produced[0].type is EventType.ESCALATED_TO_HUMAN
    assert produced[0].payload["reason"] == "audio_unintelligible"


def test_an_opt_out_spoken_aloud_is_honoured_like_one_typed(index):
    agent = ResponsiveAgent(
        index,
        transcriber=ScriptedTranscriber(
            transcripts={"voice-note": "nao quero mais receber essas mensagens me tira da lista"}
        ),
    )
    produced = agent.handle(_audio_event())
    assert produced[0].type is EventType.OPT_OUT_REQUESTED


def _live_webm(path, seconds):
    """A WebM streamed the way MediaRecorder writes it: no duration in the header."""
    import subprocess

    with path.open("wb") as handle:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=d={seconds}", "-c:a", "libopus"]
            + ["-f", "webm", "-live", "1", "pipe:1"],
            stdout=handle,
            check=True,
        )
    return path


@needs_ffmpeg
def test_a_streamed_webm_without_a_header_duration_is_measured(tmp_path):
    """Chrome's MediaRecorder writes WebM with no duration; it must not read as 0 seconds."""
    path = _live_webm(tmp_path / "live.webm", 2)
    clip = probe(path)
    assert 1.5 < clip.duration_s < 2.5


@needs_ffmpeg
def test_a_streamed_webm_over_the_maximum_is_still_refused(tmp_path):
    path = _live_webm(tmp_path / "long.webm", MAX_DURATION_S + 10)
    with pytest.raises(AudioError, match="longer than"):
        accept(path)


@needs_ffmpeg
def test_a_streamed_webm_under_the_minimum_is_still_refused(tmp_path):
    path = _live_webm(tmp_path / "short.webm", MIN_DURATION_S / 2)
    with pytest.raises(AudioError, match="not a message"):
        accept(path)
