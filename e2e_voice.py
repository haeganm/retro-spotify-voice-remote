"""E2E recognition check (Windows-only): synthesizes spoken commands with the
built-in TTS voice, runs them through the real STT + wake word + intent parser.
Usage: python e2e_voice.py [--model small|medium] [--stt vosk|whisper]
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from retro.__main__ import MODELS, app_dir, ensure_model
from retro.voice import Listener, parse, strip_wake

PHRASES = {
    # name: (spoken text, expected action, expected arg or None to skip check)
    "play": ("hey retro play bohemian rhapsody", "play_track", "bohemian rhapsody"),
    "pause": ("hey retro pause", "pause", None),
    "volume": ("hey retro set volume to fifty", "set_volume", 50),
    "next": ("hey retro next song", "next_track", None),
    "nowplaying": ("hey retro what's playing", "now_playing", None),
    "standbyme": ("hey retro play stand by me", "play_track", "stand by me"),
    "liked": ("hey retro play my liked songs", "play_liked", None),
    # the hard ones: long titles + artist disambiguation
    "tameimpala": ("hey retro play the less i know the better by tame impala",
                   "play_track", "the less i know the better by tame impala"),
    # "tv girl" may decode as "t v girl" - fuzzy search absorbs it
    "tvgirl": ("hey retro play cigarettes out the window by tv girl",
               "play_track", None),
    # "mister" transcribes as "mr" - either is a fine search query
    "mrbrightside": ("hey retro play mister brightside by the killers",
                     "play_track", None),
    "brainstew": ("hey retro play brain stew by green day", "play_track", None),
    "queue": ("hey retro queue bohemian rhapsody", "queue_track", "bohemian rhapsody"),
    # niche artist name: expected to need --hotwords "Yeat" to transcribe right
    "yeat": ("hey retro play rockstar by yeat", "play_track", None),
    # short control commands: must take the Vosk fast path (0ms whisper)
    "skip": ("hey retro skip", "next_track", None),
    "shuffle": ("hey retro shuffle on", "shuffle_on", None),
}


def make_wavs(outdir):
    ps = ["Add-Type -AssemblyName System.Speech;"
          "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
          "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
          "[System.Speech.AudioFormat.AudioChannel]::Mono);"]
    for name, (text, *_rest) in PHRASES.items():
        wav = outdir / f"{name}.wav"
        if wav.exists():
            continue
        safe = text.replace("'", "''")
        ps.append("$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  f"$s.SetOutputToWaveFile('{wav}',$f);$s.Speak('{safe}');$s.Dispose();")
    if len(ps) > 1:
        subprocess.run(["powershell", "-NoProfile", "-Command", "".join(ps)], check=True)


def main():
    if sys.platform != "win32":
        sys.exit("Windows-only (uses the built-in SAPI voice for test audio).")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=list(MODELS), default="small")
    ap.add_argument("--stt", choices=["vosk", "whisper"], default="whisper")
    ap.add_argument("--hotwords", help="comma-separated artist names to bias whisper")
    args = ap.parse_args()

    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    model = Model(str(ensure_model(app_dir(), args.model)))

    transcriber = None
    if args.stt == "whisper":
        from retro.stt import make_transcriber
        transcriber = make_transcriber(hotwords=args.hotwords)
        transcriber(b"\x00" * 32000)  # warm up

    tts = Path(tempfile.gettempdir()) / "spotify-retro-e2e"
    tts.mkdir(exist_ok=True)
    make_wavs(tts)

    failures = 0
    for name, (_text, want_action, want_arg) in PHRASES.items():
        rec = KaldiRecognizer(model, 16000)
        with wave.open(str(tts / f"{name}.wav")) as w:
            pcm = w.readframes(w.getnframes())
        for i in range(0, len(pcm), 16000):
            rec.AcceptWaveform(pcm[i:i + 16000])
        vosk_heard = json.loads(rec.FinalResult()).get("text", "").strip()
        rest = strip_wake(vosk_heard, "hey retro")  # vosk always gates the wake
        ms = 0
        if rest and transcriber:  # the real production path
            lis = Listener("unused", "hey retro", on_command=None, transcriber=transcriber)
            t0 = time.perf_counter()
            rest = lis._better(rest, pcm, awaiting=False)
            ms = (time.perf_counter() - t0) * 1000
        intent = parse(rest) if rest else None
        ok = (rest is not None and intent is not None and intent[0] == want_action
              and (want_arg is None or intent[1] == want_arg))
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name:12} {ms:4.0f}ms cmd={rest!r} -> {intent}")

    print(f"[{args.model}/{args.stt}] {'PASS' if not failures else f'{failures} FAILURES'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
