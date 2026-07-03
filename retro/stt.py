"""Whisper command transcription (faster-whisper, offline, CPU int8).

Vosk stays as the always-on wake-word spotter; once an utterance ends, its
audio is re-transcribed here - Whisper is far better at song titles and
artist names than Kaldi-era models."""
import re


def normalize(text):
    """Whisper emits punctuation and case; commands want neither."""
    text = re.sub(r"[^a-z0-9' ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def make_transcriber(model_name="base.en"):
    """Return transcribe(pcm16-bytes) -> normalized text, or None if
    faster-whisper isn't installed."""
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(pcm16):
        audio = np.frombuffer(pcm16, np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(audio, language="en", beam_size=5)
        return normalize(" ".join(s.text for s in segments))

    return transcribe
