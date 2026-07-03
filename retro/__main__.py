"""Spotify Retro - tray app entry point. Run with: spotify-retro (or python -m retro)"""
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

MODELS = {  # name -> (folder, approx size) at https://alphacephei.com/vosk/models
    "small": ("vosk-model-small-en-us-0.15", "40 MB"),
    "medium": ("vosk-model-en-us-0.22-lgraph", "130 MB"),
}
DEFAULTS = {"wake_phrase": "hey retro", "model": "medium",
            "input_device": None, "sound": True}
ICON = Path(__file__).parent / "assets" / "icon.png"
ICON_ICO = Path(__file__).parent / "assets" / "icon.ico"


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


def save_config(d, cfg):
    (d / "config.json").write_text(json.dumps(cfg, indent=2))


def load_config(d):
    f = d / "config.json"
    cfg = json.loads(f.read_text()) if f.exists() else {}
    if not cfg.get("client_id"):
        print("First run - create a free app at https://developer.spotify.com/dashboard")
        print("  (set its Redirect URI to http://127.0.0.1:8888/callback)")
        cfg["client_id"] = input("Paste your Client ID: ").strip()
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    save_config(d, cfg)
    return cfg


def ensure_model(d, size):
    folder, mb = MODELS.get(size) or MODELS["medium"]
    m = d / folder
    if m.exists():
        return m
    print(f"Downloading Vosk speech model ({mb}, one time)...")
    zpath = d / "model.zip"
    urllib.request.urlretrieve(f"https://alphacephei.com/vosk/models/{folder}.zip", zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(d)
    zpath.unlink()
    return m


def make_image():
    from PIL import Image, ImageDraw
    if ICON.exists():
        return Image.open(ICON)
    img = Image.new("RGB", (64, 64), (18, 18, 18))  # fallback: drawn record
    dr = ImageDraw.Draw(img)
    dr.ellipse((6, 6, 58, 58), fill=(30, 215, 96))
    dr.ellipse((26, 26, 38, 38), fill=(18, 18, 18))
    return img


def beep():
    if sys.platform == "win32":
        import winsound
        winsound.Beep(880, 90)


def input_devices():
    """(index, name) of unique input devices."""
    import sounddevice as sd
    seen, out = set(), []
    for i, dv in enumerate(sd.query_devices()):
        name = dv["name"].strip()
        if dv["max_input_channels"] > 0 and name not in seen:
            seen.add(name)
            out.append((i, name))
    return out


def startup_lnk():
    return (Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / "Spotify Retro.lnk")


def set_startup(enable):
    """Create/remove a Startup shortcut running pythonw (silent, no console)."""
    lnk = startup_lnk()
    if not enable:
        lnk.unlink(missing_ok=True)
        return
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    import subprocess
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{pythonw}';$s.Arguments='-m retro';"
          f"$s.IconLocation='{ICON_ICO}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   check=True, creationflags=0x08000000)  # CREATE_NO_WINDOW


def main():
    ap = argparse.ArgumentParser(prog="spotify-retro")
    ap.add_argument("--say", help="run one command as text (no mic) and exit, e.g. --say 'play daft punk'")
    ap.add_argument("--debug", action="store_true", help="print everything the recognizer hears")
    args = ap.parse_args()

    d = app_dir()
    cfg = load_config(d)
    player = Player(cfg["client_id"], d / "token.json")

    if args.say:
        intent = voice.parse(args.say)
        print(player.handle(*intent) if intent else f"Didn't understand: {args.say}")
        return

    # First API call triggers the one-time browser OAuth; do it at startup,
    # not mid-first-command.
    print(f"Connected. Now playing: {player.now_playing()}")

    model = ensure_model(d, cfg["model"])
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

    listener = voice.Listener(
        model, cfg["wake_phrase"], on_command,
        on_wake=lambda: notify("Listening..."),
        on_wake_hint=beep if cfg["sound"] else lambda: None,
        device=cfg["input_device"], debug=args.debug)
    listener.restart = threading.Event()

    def toggle(icon_, item):
        listening.clear() if listening.is_set() else listening.set()

    def pick_mic(index):  # None = system default
        def do(icon_, item):
            cfg["input_device"] = index
            save_config(d, cfg)
            listener.device = index
            listener.restart.set()
        return do

    def mic_items():
        yield pystray.MenuItem("System default", pick_mic(None),
                               checked=lambda item: cfg["input_device"] is None,
                               radio=True)
        for i, name in input_devices():
            yield pystray.MenuItem(name, pick_mic(i),
                                   checked=lambda item, i=i: cfg["input_device"] == i,
                                   radio=True)

    def toggle_startup(icon_, item):
        set_startup(not startup_lnk().exists())

    def reauth(icon_, item):
        (d / "token.json").unlink(missing_ok=True)
        notify("Token cleared - restart Spotify Retro to sign in again.")

    def quit_(icon_, item):
        stop.set()
        icon.stop()

    menu_items = [
        pystray.MenuItem("Listening", toggle, checked=lambda item: listening.is_set()),
        pystray.MenuItem("Microphone", pystray.Menu(mic_items)),
    ]
    if sys.platform == "win32":
        menu_items.append(pystray.MenuItem("Start with Windows", toggle_startup,
                                           checked=lambda item: startup_lnk().exists()))
    menu_items += [
        pystray.MenuItem("Re-authenticate", reauth),
        pystray.MenuItem("Quit", quit_),
    ]
    icon.menu = pystray.Menu(*menu_items)

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
