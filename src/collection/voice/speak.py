"""Speaking the system's own words, behind an interface so the engine stays replaceable.

The same split as recognition, for the same reasons:

* `PiperSynthesizer` is the real one: Piper, a VITS model exported to ONNX, running on
  CPU in this process. `piper-tts` is imported lazily and the extra is optional, so a fresh
  clone, CI and the test suite run without a voice downloaded.
* `ToneSynthesizer` returns a fixed-pitch tone whose length follows the text. Tests need
  audio of a plausible shape -- a call must wait for the notice to finish *playing* -- and
  no model; a tone also cannot be mistaken for speech in a recording someone opens later.

Why local, when the words are the system's and not the customer's. The asymmetry the spec
names is real: a cloud TTS would only ever receive text this system wrote, which is not the
customer's personal data in the way their voice is. But the text is not anonymous either --
a collection reply reads back the amount, the instalments and the due date of one named
person's debt -- so a vendor would still be processing a debtor's financial situation, and
a third-party processor is a disclosure the operation would owe. The other reason is the
measurement: Piper's first audio on this CPU is tens of milliseconds, so the latency
argument that usually justifies a cloud engine does not arise. Local is cheaper to defend
and no slower. `docs/voice-call.md` has the numbers.

Every engine failure is `SynthesisError`, so the call can end on a known outcome rather
than on whichever exception a backend happens to raise.
"""

import math
import struct
import time
import wave
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from collection.config import settings

#: The agent's voice. `faber` is a clear, moderately paced male pt-BR voice at medium
#: quality; `cadu` is a second speaker the turn-time benchmark uses for the customer, so
#: the recogniser is not hearing the agent's own voice back.
AGENT_VOICE = "pt_BR-faber-medium"
CUSTOMER_VOICE = "pt_BR-cadu-medium"


class SynthesisError(RuntimeError):
    """Speech could not be produced: no engine, no voice file, engine failure."""


@dataclass(frozen=True)
class SpeechChunk:
    """A run of mono 16-bit PCM. Engines yield one per sentence, which is what makes
    "time to first audio" a smaller number than "time to synthesise the reply"."""

    pcm: bytes
    sample_rate: int

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate


class Synthesizer(ABC):
    name: str

    @abstractmethod
    def synthesize(self, text: str) -> Iterator[SpeechChunk]:
        """Speech for `text`, sentence by sentence, or `SynthesisError`."""

    def warm(self) -> None:
        """Load whatever the engine loads lazily, so the first call does not pay for it."""
        return None


class PiperSynthesizer(Synthesizer):
    """Piper on CPU. The voice is an `.onnx` file plus its `.onnx.json` beside it."""

    name = "piper"

    def __init__(self, voice: str = AGENT_VOICE, voices_dir: Path | None = None) -> None:
        self.voice = voice
        self.voices_dir = voices_dir or settings.dir_voices
        self._voice = None

    @property
    def model_path(self) -> Path:
        return self.voices_dir / f"{self.voice}.onnx"

    def _load(self):
        if self._voice is None:
            try:
                from piper import PiperVoice
            except ImportError as error:
                raise SynthesisError(
                    "The Piper synthesizer needs piper-tts: uv sync --extra call"
                ) from error
            if not self.model_path.exists():
                raise SynthesisError(
                    f"No voice at {self.model_path}. Download it with `make voices`."
                )
            try:
                self._voice = PiperVoice.load(self.model_path)
            except Exception as error:
                raise SynthesisError(f"{type(error).__name__}: {error}") from error
        return self._voice

    def warm(self) -> None:
        self._load()

    def synthesize(self, text: str) -> Iterator[SpeechChunk]:
        voice = self._load()
        try:
            for chunk in voice.synthesize(text):
                yield SpeechChunk(pcm=chunk.audio_int16_bytes, sample_rate=chunk.sample_rate)
        except Exception as error:
            raise SynthesisError(f"{type(error).__name__}: {error}") from error


@dataclass
class ToneSynthesizer(Synthesizer):
    """A 440 Hz tone, `seconds_per_char` long per character, one chunk per sentence.

    The stand-in for tests and for a machine with no voice. It records what it was asked
    to say, which is how a test proves the recording notice was spoken first.
    """

    sample_rate: int = 16_000
    seconds_per_char: float = 0.01
    name: str = "tone"
    spoken: list[str] = field(default_factory=list)

    def synthesize(self, text: str) -> Iterator[SpeechChunk]:
        self.spoken.append(text)
        for sentence in [part for part in text.replace("!", ".").split(".") if part.strip()]:
            samples = max(1, int(len(sentence) * self.seconds_per_char * self.sample_rate))
            pcm = b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / self.sample_rate)))
                for i in range(samples)
            )
            yield SpeechChunk(pcm=pcm, sample_rate=self.sample_rate)


def first_audio(synthesizer: Synthesizer, text: str) -> tuple[list[SpeechChunk], float]:
    """Synthesise `text`, returning the chunks and the milliseconds to the first one."""
    started = time.perf_counter()
    chunks: list[SpeechChunk] = []
    first_ms = 0.0
    for chunk in synthesizer.synthesize(text):
        if not chunks:
            first_ms = (time.perf_counter() - started) * 1000
        chunks.append(chunk)
    return chunks, first_ms


def write_wav(path: Path | str, pcm: bytes, sample_rate: int) -> Path:
    """Mono 16-bit PCM to a WAV file -- the format the call keeps and Whisper reads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return path


def resample(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linear-interpolation resampling of mono 16-bit PCM.

    Good enough for moving synthesised speech between 22.05 kHz (Piper) and 16 kHz
    (Whisper) or 48 kHz (WebRTC); it is not a studio resampler, and nothing here needs one.
    """
    if source_rate == target_rate or not pcm:
        return pcm
    import numpy as np

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    count = max(1, int(round(len(samples) * target_rate / source_rate)))
    positions = np.linspace(0, len(samples) - 1, count)
    out = np.interp(positions, np.arange(len(samples)), samples)
    return np.clip(out, -32768, 32767).astype("<i2").tobytes()
