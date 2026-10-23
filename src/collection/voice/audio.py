"""Reading the file a customer actually sends, before anyone tries to understand it.

WhatsApp records voice notes as OGG/OPUS at 48 kHz; every speech recogniser in reach
wants 16 kHz mono PCM. That conversion is the whole of this module, and it is kept apart
from transcription on purpose: a clip can be rejected -- wrong container, silent, eleven
minutes long -- without a model being loaded, and the rejection is then a cheap, testable
decision rather than a failure inside a provider.

`ffmpeg` is the dependency. It is not a Python package, so it is probed rather than
imported, and everything that needs it says so: `probe` and `to_wav` raise `AudioError`
when it is missing, and the evaluation in `evaluate.py` deliberately needs neither.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Containers worth accepting. OGG/OPUS is what WhatsApp sends; the rest are what a web
#: client or an operator uploading a recording will produce.
ACCEPTED_CODECS = frozenset({"opus", "vorbis", "mp3", "aac", "pcm_s16le", "pcm_f32le"})
ACCEPTED_SUFFIXES = frozenset({".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".webm"})

#: Every speech model in use expects mono at this rate; sending it anything else makes
#: the provider resample internally, which is the same work done less visibly.
TARGET_SAMPLE_RATE = 16_000

#: A voice note longer than this is not a question, it is a call, and it goes to a person.
#: Two minutes is above the 95th percentile of WhatsApp voice notes in the operation's own
#: history and well under the point where transcription cost stops being negligible.
MAX_DURATION_S = 120.0

#: Below this there is no speech, only a misfired button.
MIN_DURATION_S = 0.4


class AudioError(RuntimeError):
    """The clip cannot be used: missing, unreadable, wrong format, wrong length."""


@dataclass(frozen=True)
class AudioClip:
    path: Path
    codec: str
    duration_s: float
    sample_rate: int
    channels: int

    @property
    def too_long(self) -> bool:
        return self.duration_s > MAX_DURATION_S

    @property
    def too_short(self) -> bool:
        return self.duration_s < MIN_DURATION_S


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as error:
        raise AudioError("ffmpeg/ffprobe is not installed; audio cannot be decoded") from error
    except subprocess.TimeoutExpired as error:
        raise AudioError(f"{command[0]} timed out") from error
    if completed.returncode != 0:
        raise AudioError(f"{command[0]} failed: {completed.stderr.strip()[:200]}")
    return completed.stdout


def _packet_duration(path: Path) -> float:
    """Length from the last packet's timestamp, for containers that carry no duration."""
    out = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=pts_time,duration_time",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    end = 0.0
    for line in out.splitlines():
        start, _, length = line.partition(",")
        try:
            end = max(end, float(start) + float(length or 0))
        except ValueError:
            continue
    return end


def probe(path: Path | str) -> AudioClip:
    """Read the clip's shape without decoding it.

    Raises rather than returning a flag: a clip that cannot be probed is one the agent
    must not try to answer, and the caller has to deal with that either way.
    """
    path = Path(path)
    if not path.exists():
        raise AudioError(f"no such audio file: {path}")
    if path.suffix.lower() not in ACCEPTED_SUFFIXES:
        raise AudioError(f"unsupported container {path.suffix!r}")

    payload = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-select_streams",
            "a:0",
            str(path),
        ]
    )
    try:
        parsed = json.loads(payload)
        stream = parsed["streams"][0]
    except (json.JSONDecodeError, KeyError, IndexError) as error:
        raise AudioError(f"no audio stream in {path.name}") from error

    codec = str(stream.get("codec_name", ""))
    if codec not in ACCEPTED_CODECS:
        raise AudioError(f"unsupported codec {codec!r} in {path.name}")

    duration = stream.get("duration") or parsed.get("format", {}).get("duration") or 0.0
    if not float(duration):
        # A browser's MediaRecorder streams WebM without a duration in the header. Reading
        # the packet timestamps is slower than the header but is the only honest length.
        duration = _packet_duration(path)
    return AudioClip(
        path=path,
        codec=codec,
        duration_s=float(duration),
        sample_rate=int(stream.get("sample_rate", 0) or 0),
        channels=int(stream.get("channels", 0) or 0),
    )


def accept(path: Path | str) -> AudioClip:
    """Probe the clip and refuse the lengths that are not worth transcribing."""
    clip = probe(path)
    if clip.too_short:
        raise AudioError(f"{clip.duration_s:.2f}s of audio is not a message")
    if clip.too_long:
        raise AudioError(f"{clip.duration_s:.0f}s is longer than the {MAX_DURATION_S:.0f}s limit")
    return clip


def to_wav(clip: AudioClip, destination: Path | str) -> Path:
    """Decode to 16 kHz mono PCM, the format every recogniser here expects."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(clip.path),
            "-ac",
            "1",
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-f",
            "wav",
            str(destination),
        ]
    )
    return destination
