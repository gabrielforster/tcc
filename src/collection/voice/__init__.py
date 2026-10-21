"""Voice: accepting a customer's audio, turning it into text, and knowing what to distrust."""

from collection.voice.audio import AudioClip, AudioError, accept, probe, to_wav
from collection.voice.degrade import degrade
from collection.voice.transcribe import (
    MIN_CONFIDENCE,
    ScriptedTranscriber,
    Transcriber,
    Transcription,
    TranscriptionError,
    WhisperTranscriber,
    get_transcriber,
    needs_confirmation,
    spoken_figures,
)

__all__ = [
    "MIN_CONFIDENCE",
    "AudioClip",
    "AudioError",
    "ScriptedTranscriber",
    "Transcriber",
    "Transcription",
    "TranscriptionError",
    "WhisperTranscriber",
    "accept",
    "degrade",
    "get_transcriber",
    "needs_confirmation",
    "probe",
    "spoken_figures",
    "to_wav",
]
