"""Spotify Retro - tray app entry point. Run with: spotify-retro (or python -m retro)"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import speak, stt, voice
from .player import Player

MODELS = {  # name -> (folder, approx size) at https://alphacephei.com/vosk/models
    "small": ("vosk-model-small-en-us-0.15", "40 MB"),
    "medium": ("vosk-model-en-us-0.22-lgraph", "130 MB"),
}
# Vosk only spots the wake word (small is plenty); Whisper hears the command.
# notify: "smart" = subtle sound for successful actions, toasts only for
# errors and answers; "all" = toast everything.
DEFAULTS = {"wake_phrase": "hey retro", "model": "small",
            "input_device": None, "sound": True,
            "stt": "whisper", "whisper_model": "auto", "device": "auto",
            "notify": "smart", "speak": True, "duck": True}
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


def play_wav(name):
    """Soft async cue - quiet enough that the mic barely picks it up."""
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(str(Path(__file__).parent / "assets" / name),
                           winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)


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


def resolve_device(pref):
    """Config stores the mic by NAME (indexes shift between sessions);
    resolve to a current index, or None (default mic) if it's gone."""
    if pref is None:
        return None
    for i, name in input_devices():
        if isinstance(pref, int):  # legacy index configs
            if i == pref:
                return i
        elif pref.lower() in name.lower():
            return i
    return None


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


def make_logger(d):
    """Append-only transcript log (what each engine heard, what ran) so
    recognition misses can be diagnosed from ground truth. Trimmed on start."""
    logf = d / "retro.log"
    if logf.exists():
        lines = logf.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        logf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    lock = threading.Lock()

    def log(line):
        with lock, open(logf, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%m-%d %H:%M:%S')} {line}\n")
    return log


def main():
    ap = argparse.ArgumentParser(prog="spotify-retro")
    ap.add_argument("--say", help="run one command as text (no mic) and exit, e.g. --say 'play daft punk'")
    ap.add_argument("--debug", action="store_true", help="print everything the recognizer hears")
    ap.add_argument("--misses", action="store_true", help="show recent unrecognized commands from the log")
    args = ap.parse_args()

    d = app_dir()
    if args.misses:
        logf = d / "retro.log"
        lines = logf.read_text(encoding="utf-8", errors="replace").splitlines() if logf.exists() else []
        for line in lines:
            if "no intent" in line:
                print(line)
        return
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

    # fixed control confirmations get a subtle pop; search results, answers
    # and errors are spoken (or toasted when speech is off)
    POP_OK = ("Paused", "Resuming", "Skipped", "Previous", "Volume", "Shuffle")
    VOLUME_ACTIONS = {"set_volume", "volume_up", "volume_down"}

    log = make_logger(d)
    say = speak.make_speaker() if cfg["speak"] else None

    def announce(msg):
        print(msg)
        icon.title = f"Spotify Retro - {msg}"[:120]
        if say:
            say(msg)
        else:
            notify(msg)

    def on_command(text):
        def work():  # off the mic thread: API round trips must not deafen the app
            intent = voice.parse(text)
            if not intent:
                log(f"cmd: {text!r} -> no intent")
                player.unduck()
                if cfg["sound"] and not say:
                    play_wav("err.wav")
                announce(f"Didn't catch that: '{text}'")
                return
            msg = player.handle(*intent)
            log(f"cmd: {text!r} -> {intent} -> {msg!r}")
            if intent[0] in VOLUME_ACTIONS:
                player._ducked = None  # user set a volume: don't override it
            else:
                player.unduck()
            if (cfg["notify"] == "smart" and intent[0] != "now_playing"
                    and msg.startswith(POP_OK)):
                if cfg["sound"]:
                    play_wav("ok.wav")
                print(msg)
                icon.title = f"Spotify Retro - {msg}"[:120]
            else:
                announce(msg)
        threading.Thread(target=work, daemon=True).start()

    def wake_hint():
        if cfg["sound"]:
            play_wav("wake.wav")
        if cfg["duck"]:
            threading.Thread(target=player.duck, daemon=True).start()
            threading.Timer(10, player.unduck).start()  # safety restore

    listener = voice.Listener(
        model, cfg["wake_phrase"], on_command,
        on_wake=lambda: notify("Listening..."),
        on_wake_hint=wake_hint,
        device=resolve_device(cfg["input_device"]), debug=args.debug)
    listener.log = log
    listener.restart = threading.Event()

    def toggle(icon_, item):
        listening.clear() if listening.is_set() else listening.set()

    def pick_mic(name, index):  # (None, None) = system default
        def do(icon_, item):
            cfg["input_device"] = name  # stored by name: indexes shift between boots
            save_config(d, cfg)
            listener.device = index
            listener.restart.set()
        return do

    def mic_items():
        yield pystray.MenuItem("System default", pick_mic(None, None),
                               checked=lambda item: cfg["input_device"] is None,
                               radio=True)
        for i, name in input_devices():
            yield pystray.MenuItem(name, pick_mic(name, i),
                                   checked=lambda item, name=name: cfg["input_device"] == name,
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

    def load_whisper():
        """Whisper loads in ~5s; commands fall back to Vosk text until then.
        The user's top artists become Whisper hotwords, so names like 'Yeat'
        decode as themselves instead of a dictionary word."""
        if cfg["stt"] != "whisper":
            return
        try:  # capped: a long bias string swamps short clips
            words = player.user_artists()[:25]
            words += [p["name"] for p in player._my_playlists()]
            words += player.user_titles()
            hotwords = ", ".join(dict.fromkeys(words))[:500] or None
        except Exception:
            hotwords = None
        tr, used = stt.make_transcriber(cfg["whisper_model"], hotwords=hotwords,
                                        device=cfg["device"])
        if tr:
            tr(b"\x00" * 32000)  # warm up the compute graph
            listener.transcriber = tr
            print(f"Whisper ready ({used})"
                  + (f", biased to {hotwords.count(',') + 1} names" if hotwords else ""))

    threading.Thread(target=run_listener, daemon=True).start()
    threading.Thread(target=load_whisper, daemon=True).start()

    print(f'Running in the tray. Say "{cfg["wake_phrase"]}" then a command, '
          f'or "{cfg["wake_phrase"]}, play <song>" in one breath.')
    icon.run()


if __name__ == "__main__":
    main()
