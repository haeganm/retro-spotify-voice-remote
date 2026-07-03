"""Whisper command transcription (faster-whisper), GPU when available.

Vosk stays as the always-on wake-word spotter; once an utterance ends, its
audio is re-transcribed here - Whisper is far better at song titles and
artist names than Kaldi-era models."""
import os
import re
import sys
from pathlib import Path


def normalize(text):
    """Whisper emits punctuation and case; commands want neither."""
    text = re.sub(r"[^a-z0-9' ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _register_cuda_dlls():
    """The nvidia-* pip wheels ship cuBLAS/cuDNN; ctranslate2 finds them only
    if their bin dirs are registered."""
    site = Path(sys.executable).parent / "Lib" / "site-packages"
    found = False
    for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
        d = site / sub
        if d.exists():
            os.add_dll_directory(str(d))
            found = True
    return found


def make_transcriber(model_name="auto", hotwords=None, device="auto"):
    """Return (transcribe(pcm16-bytes) -> normalized text, "<model>/<device>"),
    or (None, None) if faster-whisper isn't installed. `hotwords` (the user's
    artists/playlists/titles) biases decoding toward names Whisper would
    otherwise mangle. model "auto" = small.en on GPU (fast there), base.en on
    CPU; device "auto" tries cuda then cpu."""
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        return None, None

    model = None
    used = "cpu"
    if device in ("auto", "cuda") and _register_cuda_dlls():
        try:
            name = "small.en" if model_name == "auto" else model_name
            model = WhisperModel(name, device="cuda", compute_type="float16")
            used = f"{name}/cuda"
        except Exception:
            model = None
    if model is None:
        if device == "cuda":
            return None, None
        name = "base.en" if model_name == "auto" else model_name
        model = WhisperModel(name, device="cpu", compute_type="int8")
        used = f"{name}/cpu"

    pad = np.zeros(int(0.4 * 16000), dtype=np.float32)  # short clips hallucinate less with lead-in/out silence

    def transcribe(pcm16):
        audio = np.frombuffer(pcm16, np.int16).astype(np.float32) / 32768.0
        audio = np.concatenate([pad, audio, pad])
        segments, _ = model.transcribe(audio, language="en", beam_size=5,
                                       hotwords=hotwords)
        return normalize(" ".join(s.text for s in segments))

    return transcribe, used
