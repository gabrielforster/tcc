"""Turning a voice note into text, behind an interface so the recogniser stays replaceable.

Two implementations, the same split every other provider seam in this project uses:

* `WhisperTranscriber` is the real one. `faster-whisper` is imported lazily and the extra
  is optional, so a fresh clone, CI and the whole test suite still run on a machine with
  no model downloaded -- the discipline `AnthropicClient` and `SentenceTransformerEmbedder`
  already follow.
* `ScriptedTranscriber` returns the labelled transcript for a clip. It is the control arm
  and it cannot be wrong, which is the point: it isolates everything downstream of
  recognition, so a drop in answer quality can be attributed to the recogniser or to the
  agent, never ambiguously to both.

Every provider failure becomes `TranscriptionError`. The responsive agent escalates to a
human when transcription fails, and it cannot do that uniformly if each backend raises its
own exception type.
"""

import math
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from collection.voice.audio import AudioClip, accept, to_wav

#: Transcripts below this confidence are not answered: the agent hands over rather than
#: act on a guess. The figure is provisional and it is the one number in this module that
#: no measurement here supports -- calibrating a confidence threshold needs real audio
#: with known transcripts, which this repository does not contain. It is set where
#: Whisper's duration-weighted likelihood sits for a clean short pt-BR clip, and
#: docs/voice.md records it as owed work. The threshold is not what protects the customer
#: in any case: the sweep shows spoken figures corrupting at word error rates where intent
#: still classifies correctly, so the protection is reading the figure back, not refusing
#: the transcript.
MIN_CONFIDENCE = 0.55

#: Default model size. `small` is the smallest Whisper checkpoint that handles pt-BR
#: numerals at all; `tiny` and `base` mangle them badly enough to be useless here.
DEFAULT_MODEL = "small"

#: Values a customer may speak but the system must never act on without reading them back.
#: Both notations, because a recogniser emits either: "1.250,00" and "mil duzentos".
_MONEY = re.compile(
    r"(?:r\$\s*)?\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\b\d+\s*(?:reais|x|vezes|parcelas)\b",
    re.IGNORECASE,
)
_SPOKEN_NUMBER = re.compile(
    r"\b(?:um|dois|duas|tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|"
    r"quatorze|catorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta|"
    r"cinquenta|sessenta|setenta|oitenta|noventa|cem|cento|duzentos|trezentos|quatrocentos|"
    r"quinhentos|seiscentos|setecentos|oitocentos|novecentos|mil)\b",
    re.IGNORECASE,
)


class TranscriptionError(RuntimeError):
    """Recognition failed: no model, unreadable clip, backend error."""


@dataclass
class Transcription:
    text: str
    confidence: float
    transcriber: str
    latency_ms: float = 0.0
    duration_s: float = 0.0
    language: str = "pt"

    @property
    def usable(self) -> bool:
        return bool(self.text.strip()) and self.confidence >= MIN_CONFIDENCE

    @property
    def real_time_factor(self) -> float:
        """Seconds of compute per second of audio. Under 1.0 is faster than listening."""
        if self.duration_s <= 0:
            return 0.0
        return (self.latency_ms / 1000) / self.duration_s


def spoken_figures(text: str) -> list[str]:
    """The amounts and instalment counts a transcript claims the customer said."""
    found = [match.group(0).strip() for match in _MONEY.finditer(text)]
    found += [match.group(0).strip() for match in _SPOKEN_NUMBER.finditer(text)]
    return found


def needs_confirmation(text: str) -> bool:
    """Whether a spoken message carries a figure that must be read back before acting.

    This is the rule the error sweep paid for. Recognition degrades gracefully on intent
    -- "quero parcelar" survives a mangled sentence -- and catastrophically on numbers: a
    digit lost or transposed turns R$ 1.250,00 into a different debt, and nothing in the
    transcript marks it as suspect. So an amount heard in audio is treated as a proposal
    to confirm, never as an instruction to execute.
    """
    return bool(spoken_figures(text))


class Transcriber(ABC):
    name: str

    @abstractmethod
    def transcribe(self, clip: AudioClip) -> Transcription:
        """Text for this clip, or `TranscriptionError`."""

    def transcribe_path(self, path: Path | str) -> Transcription:
        """Accept the file first, then transcribe it. The usual entry point."""
        return self.transcribe(accept(path))


@dataclass
class ScriptedTranscriber(Transcriber):
    """Returns the labelled transcript for a clip, keyed by file stem.

    The control arm. It never mishears, so every number it reports downstream is the
    number that was spoken, and any failure after it belongs to the agent.
    """

    transcripts: dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    name: str = "scripted"
    calls: list[str] = field(default_factory=list)

    def transcribe(self, clip: AudioClip) -> Transcription:
        self.calls.append(clip.path.name)
        key = clip.path.stem
        if key not in self.transcripts:
            raise TranscriptionError(f"no scripted transcript for {key!r}")
        return Transcription(
            text=self.transcripts[key],
            confidence=self.confidence,
            transcriber=self.name,
            duration_s=clip.duration_s,
        )


class WhisperTranscriber(Transcriber):
    """Whisper via `faster-whisper`, running locally on CPU.

    Local on purpose. A voice note is personal data under the LGPD, and sending customer
    audio to a third-party recognition API is a transfer the thesis would have to justify
    and the operation would have to disclose. Running the model in-process makes that
    question disappear, at the cost of latency the evaluation reports honestly.
    """

    name = "whisper"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        language: str = "pt",
        beam_size: int = 5,
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model
        self.language = language
        self.beam_size = beam_size
        self.compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise TranscriptionError(
                    "The whisper transcriber needs faster-whisper: uv sync --extra voice"
                ) from error
            try:
                self._model = WhisperModel(self.model_size, compute_type=self.compute_type)
            except Exception as error:
                raise TranscriptionError(f"{type(error).__name__}: {error}") from error
        return self._model

    def transcribe(self, clip: AudioClip) -> Transcription:
        import tempfile

        model = self._load()
        started = time.perf_counter()
        with tempfile.TemporaryDirectory() as workdir:
            wav = to_wav(clip, Path(workdir) / "clip.wav")
            try:
                segments, info = model.transcribe(
                    str(wav), language=self.language, beam_size=self.beam_size
                )
                segments = list(segments)
            except Exception as error:
                raise TranscriptionError(f"{type(error).__name__}: {error}") from error
        latency_ms = (time.perf_counter() - started) * 1000

        text = " ".join(segment.text.strip() for segment in segments).strip()
        return Transcription(
            text=text,
            confidence=_confidence(segments),
            transcriber=self.name,
            latency_ms=latency_ms,
            duration_s=clip.duration_s or getattr(info, "duration", 0.0),
            language=getattr(info, "language", self.language),
        )


def _confidence(segments: list) -> float:
    """One number from Whisper's per-segment log probabilities.

    Whisper reports an average log probability per segment and a probability that the
    segment is not speech at all. Exponentiating the first gives a per-segment likelihood;
    weighting by duration keeps a half-second "ahn" from outvoting the sentence that
    matters. An empty result is zero confidence, not an error, because silence is a
    legitimate thing for a customer to send by accident.
    """
    if not segments:
        return 0.0
    total = 0.0
    weighted = 0.0
    for segment in segments:
        span = max(1e-3, float(getattr(segment, "end", 0.0) - getattr(segment, "start", 0.0)))
        likelihood = math.exp(float(getattr(segment, "avg_logprob", -1.0)))
        speech = 1.0 - float(getattr(segment, "no_speech_prob", 0.0))
        weighted += span * likelihood * speech
        total += span
    return max(0.0, min(1.0, weighted / total if total else 0.0))


TRANSCRIBERS: dict[str, type[Transcriber]] = {"whisper": WhisperTranscriber}


def get_transcriber(name: str = "whisper") -> Transcriber:
    if name not in TRANSCRIBERS:
        raise ValueError(f"Unknown transcriber: {name!r}. Available: {', '.join(TRANSCRIBERS)}")
    return TRANSCRIBERS[name]()
