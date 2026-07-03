"""Spotify Retro - tray app entry point. Run with: python -m retro"""
import argparse
import json
import os
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from . import voice
from .player import Player

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_NAME = "vosk-model-small-en-us-0.15"


def app_dir():
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "SpotifyRetro"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_config(d):
    f = d / "config.json"
    cfg = json.loads(f.read_text()) if f.exists() else {}
    if not cfg.get("client_id"):
        print("First run - create a free app at https://developer.spotify.com/dashboard")
        print("  (set its Redirect URI to http://127.0.0.1:8888/callback)")
        cfg["client_id"] = input("Paste your Client ID: ").strip()
    cfg.setdefault("wake_phrase", "hey retro")
    f.write_text(json.dumps(cfg, indent=2))
    return cfg


def ensure_model(d):
    m = d / MODEL_NAME
    if m.exists():
        return m
    print("Downloading Vosk speech model (~40 MB, one time)...")
    zpath = d / "model.zip"
    urllib.request.urlretrieve(MODEL_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(d)
    zpath.unlink()
    return m


def make_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (64, 64), (18, 18, 18))
    dr = ImageDraw.Draw(img)
    dr.ellipse((6, 6, 58, 58), fill=(30, 215, 96))   # spotify green record
    dr.ellipse((26, 26, 38, 38), fill=(18, 18, 18))  # spindle hole
    return img


def main():
    ap = argparse.ArgumentParser(prog="spotify-retro")
    ap.add_argument("--say", help="run one command as text (no mic) and exit, e.g. --say 'play daft punk'")
    args = ap.parse_args()

    d = app_dir()
    cfg = load_config(d)
    player = Player(cfg["client_id"], d / "token.json")

    if args.say:
        intent = voice.parse(args.say)
        print(player.handle(*intent) if intent else f"Didn't understand: {args.say}")
        return

    model = ensure_model(d)
    import pystray

    listening = threading.Event()
    listening.set()
    stop = threading.Event()

    icon = pystray.Icon("SpotifyRetro", make_image(), "Spotify Retro")

    def notify(msg):
        print(msg)
        try:
            icon.notify(msg, "Spotify Retro")
        except Exception:
            pass

    def on_command(text):
        intent = voice.parse(text)
        notify(player.handle(*intent) if intent else f"Didn't catch that: '{text}'")

    def toggle(icon_, item):
        listening.clear() if listening.is_set() else listening.set()

    def reauth(icon_, item):
        (d / "token.json").unlink(missing_ok=True)
        notify("Token cleared - restart Spotify Retro to sign in again.")

    def quit_(icon_, item):
        stop.set()
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Listening", toggle, checked=lambda item: listening.is_set()),
        pystray.MenuItem("Re-authenticate", reauth),
        pystray.MenuItem("Quit", quit_),
    )

    listener = voice.Listener(model, cfg["wake_phrase"], on_command,
                              on_wake=lambda: notify("Listening..."))

    def run_listener():
        try:
            listener.run(listening, stop)
        except Exception as e:  # e.g. no microphone: stay in tray but say why
            notify(f"Voice listener stopped: {e}")

    threading.Thread(target=run_listener, daemon=True).start()

    print(f'Running in the tray. Say "{cfg["wake_phrase"]}" then a command, '
          f'or "{cfg["wake_phrase"]}, play <song>" in one breath.')
    icon.run()


if __name__ == "__main__":
    main()
