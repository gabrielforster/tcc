"""Deciding that the customer has finished a sentence.

On a voice note the customer decides where the message ends: they let go of the button.
On a call nobody presses anything, so the system has to decide, and the decision is a
trade the turn-time table makes visible. Wait too little and a customer who pauses to
look for an invoice number is cut off mid-sentence; wait too long and every turn starts
with dead air. `HANGOVER_MS` is that wait, and it is a floor under every turn's latency
that no amount of faster hardware removes.

The detector is frame energy against a fixed threshold, with a hangover. That is the
simplest thing that works on a headset in a quiet room, which is the demo; it is not what
a production line wants. LiveKit's agent framework ships Silero VAD and a learned
end-of-turn model, and the reason they are not used here is in `docs/voice-call.md`: the
framework's pipeline owns recognition, which this design keeps in the agent. Swapping in a
learned detector changes this module and nothing else.

Pure on purpose -- bytes in, utterances out, no clock and no I/O -- so every decision here
is a unit test rather than something to be heard on a call.
"""

import math
from array import array
from dataclasses import dataclass, field

#: Frames are 20 ms: the WebRTC packet size, and fine enough that the hangover is not
#: quantised into something coarser than it claims to be.
FRAME_MS = 20
#: Speech is anything louder than this. -45 dBFS sits well above a quiet room through a
#: laptop microphone with the browser's noise suppression on, and well below speech.
THRESHOLD_DBFS = -45.0
#: Silence that ends a turn. 700 ms tolerates the pause a pt-BR speaker leaves before a
#: number ("são... trezentos reais") and is within the 500-800 ms range voice products
#: commonly ship; it is the single biggest term in the turn-time budget after recognition.
HANGOVER_MS = 700
#: Voiced audio shorter than this is a cough or a click, not a turn.
MIN_SPEECH_MS = 200
#: Audio kept from before speech onset, so a soft first syllable is not clipped.
PREROLL_MS = 200
#: A monologue is cut here and answered, rather than buffered without limit.
MAX_UTTERANCE_S = 30.0


def frame_dbfs(frame: bytes) -> float:
    """Loudness of one frame of mono 16-bit PCM, in dB relative to full scale."""
    samples = array("h", frame)
    if not samples:
        return -math.inf
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 0:
        return -math.inf
    return 10 * math.log10(mean_square / (32768.0 * 32768.0))


@dataclass(frozen=True)
class Utterance:
    """One finished turn: the audio, and how the end of it was decided."""

    pcm: bytes
    sample_rate: int
    speech_ms: int
    #: Silence between the last voiced frame and the decision: the endpointing wait.
    trailing_silence_ms: int
    #: "silence" when the hangover elapsed, "max_length" when the monologue was cut.
    ended_by: str

    @property
    def duration_s(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate


@dataclass
class Endpointer:
    """Feed it 20 ms frames; it returns an `Utterance` when a turn ends, else `None`."""

    sample_rate: int = 16_000
    threshold_dbfs: float = THRESHOLD_DBFS
    hangover_ms: int = HANGOVER_MS
    min_speech_ms: int = MIN_SPEECH_MS
    preroll_ms: int = PREROLL_MS
    max_utterance_s: float = MAX_UTTERANCE_S
    _buffer: bytearray = field(default_factory=bytearray, repr=False)
    _preroll: list[bytes] = field(default_factory=list, repr=False)
    _speech_ms: int = 0
    _silence_ms: int = 0
    _in_speech: bool = False
    #: Milliseconds of audio heard since the last turn ended without any speech in it.
    #: The call reads this to decide the customer has gone quiet.
    idle_ms: int = 0

    def reset(self) -> None:
        """Forget the current turn -- after the agent has spoken, say."""
        self._buffer.clear()
        self._preroll.clear()
        self._speech_ms = self._silence_ms = self.idle_ms = 0
        self._in_speech = False

    def push(self, frame: bytes) -> Utterance | None:
        frame_ms = round(len(frame) / 2 / self.sample_rate * 1000)
        voiced = frame_dbfs(frame) >= self.threshold_dbfs

        if not self._in_speech:
            if not voiced:
                self.idle_ms += frame_ms
                self._preroll.append(frame)
                while len(self._preroll) * frame_ms > self.preroll_ms:
                    self._preroll.pop(0)
                return None
            self._in_speech = True
            self._buffer.extend(b"".join(self._preroll))
            self._preroll.clear()

        self._buffer.extend(frame)
        if voiced:
            self._speech_ms += frame_ms
            self._silence_ms = 0
        else:
            self._silence_ms += frame_ms

        if self._silence_ms >= self.hangover_ms:
            return self._finish("silence")
        if len(self._buffer) / 2 / self.sample_rate >= self.max_utterance_s:
            return self._finish("max_length")
        return None

    def _finish(self, ended_by: str) -> Utterance | None:
        utterance = Utterance(
            pcm=bytes(self._buffer),
            sample_rate=self.sample_rate,
            speech_ms=self._speech_ms,
            trailing_silence_ms=self._silence_ms,
            ended_by=ended_by,
        )
        speech_ms = self._speech_ms
        self.reset()
        if speech_ms < self.min_speech_ms:
            # A click is not a turn, and it is not the customer going quiet either.
            return None
        return utterance
