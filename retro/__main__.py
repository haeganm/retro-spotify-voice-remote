"""Spotify Retro - tray app entry point. Run with: spotify-retro (or python -m retro)"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import osd, stt, voice, winaudio
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
            "notify": "smart", "duck": True, "osd": True}
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


def ask_client_id():
    """First-run Client ID prompt. Console when there is one; a tkinter dialog
    when launched from a shortcut (pythonw has no stdin - input() would kill
    the app silently on a fresh machine)."""
    instructions = ("Create a free app at https://developer.spotify.com/dashboard\n"
                    "with Redirect URI http://127.0.0.1:8888/callback")
    if sys.stdin and sys.stdin.isatty():
        print(f"First run - {instructions}")
        return input("Paste your Client ID: ").strip()
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    val = simpledialog.askstring("Spotify Retro - first run",
                                 instructions + "\n\nPaste your Client ID:")
    root.destroy()
    return (val or "").strip()


def load_config(d):
    f = d / "config.json"
    cfg = json.loads(f.read_text()) if f.exists() else {}
    if not cfg.get("client_id"):
        cfg["client_id"] = ask_client_id()
        if not cfg["client_id"]:
            sys.exit("No Client ID provided.")
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
        # belt-and-braces zip-slip guard (extract() also sanitizes paths)
        for n in z.namelist():
            if ".." in Path(n).parts:
                raise ValueError(f"unsafe path in model zip: {n}")
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


def mic_family(name):
    """The user-meaningful device name: Windows exposes several endpoints per
    physical mic ('Headset (AirPods #4)', 'Input (...;(AirPods #4))') and some
    are silent traps - we store the family and probe all its endpoints."""
    groups = re.findall(r"\(([^()]*)\)", name)
    for g in reversed(groups):
        if g.strip():
            return g.strip()
    return re.sub(r"\s*\(\s*\)\s*$", "", name).strip() or name.strip()


def _probe_mic(index, seconds=0.5):
    """RMS of a short capture; -1 if the endpoint won't open."""
    import numpy as np
    import sounddevice as sd
    try:
        rec = sd.rec(int(seconds * 16000), samplerate=16000, channels=1,
                     dtype="int16", device=index)
        sd.wait()
    except Exception:
        return -1.0
    return float(np.sqrt(np.mean(rec.astype(np.float64) ** 2)))


def pick_input(pref=None, exclude=()):
    """Choose the input endpoint that actually delivers audio. Names lie;
    RMS doesn't - but gated mics (Bluetooth headsets suppress their noise
    floor) probe silent while being perfectly fine, so a user-chosen family
    always wins with its best OPENABLE endpoint, quiet or not."""
    devs = [(i, n) for i, n in input_devices() if i not in exclude]
    if isinstance(pref, int):  # legacy index configs
        match = [n for i, n in devs if i == pref]
        pref = mic_family(match[0]) if match else None
    fam = [(i, n) for i, n in devs if pref and pref.lower() in n.lower()]
    if fam:
        scored = [(rms, i, n) for i, n in fam if (rms := _probe_mic(i)) >= 0]
        if scored:
            _, i, n = max(scored)  # loudest, or best openable if all gated-quiet
            return i, n
    scored = [(rms, i, n) for i, n in devs if (rms := _probe_mic(i)) >= 0]
    live = [s for s in scored if s[0] >= 25]
    if live:
        _, i, n = max(live)
        return i, n
    if scored:
        _, i, n = max(scored)
        return i, n
    return None, "system default"


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

    def q(p):  # PowerShell single-quote escaping (usernames can contain ')
        return str(p).replace("'", "''")

    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
          f"$s.TargetPath='{q(pythonw)}';$s.Arguments='-m retro';"
          f"$s.IconLocation='{q(ICON_ICO)}';$s.Save()")
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
    if sys.platform == "win32":
        # own app identity: otherwise Windows matches ANY pythonw window to
        # our shortcuts and paints the Retro icon on unrelated Python apps
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SpotifyRetro.App")

    ap = argparse.ArgumentParser(prog="spotify-retro")
    ap.add_argument("--say", help="run one command as text (no mic) and exit, e.g. --say 'play daft punk'")
    ap.add_argument("--debug", action="store_true", help="print everything the recognizer hears")
    ap.add_argument("--misses", action="store_true", help="show recent unrecognized commands from the log")
    ap.add_argument("--mic-test", action="store_true", help="show live input level per microphone (speak while it runs)")
    args = ap.parse_args()

    if args.mic_test:
        import numpy as np
        import sounddevice as sd
        print("Speak normally... measuring each mic for 2s:")
        for i, name in input_devices():
            try:
                rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1,
                             dtype="int16", device=i)
                sd.wait()
                rms = float(np.sqrt(np.mean(rec.astype(np.float64) ** 2)))
                bar = "#" * min(40, int(rms / 50))
                print(f"{rms:7.0f} {bar:40} {name}")
            except Exception as e:
                print(f"   dead {'':40} {name} ({e})")
        return

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

    if not args.say:
        import socket
        guard = socket.socket()  # held for process lifetime
        try:
            guard.bind(("127.0.0.1", 48765))  # ponytail: port-bind single-instance lock
        except OSError:
            print("Spotify Retro is already running (check the tray).")
            return

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
    show_osd = osd.make_osd() if cfg["osd"] else None

    def notify(msg):
        print(msg)
        if show_osd:  # instant overlay; toasts queue up and arrive late
            show_osd(msg)
            return
        try:
            icon.notify(msg, "Spotify Retro")
        except Exception:
            pass

    # successes get a subtle sound + tooltip update; toasts only carry news
    # you must read (errors, "what's playing")
    QUIET_OK = ("Playing", "Queued", "Back to", "Paused", "Resuming", "Skipped",
                "Previous", "Volume", "Shuffle")
    VOLUME_ACTIONS = {"set_volume", "volume_up", "volume_down"}

    log = make_logger(d)

    def on_command(text):
        def work():  # off the mic thread: API round trips must not deafen the app
            intent = voice.parse(text)
            if not intent:
                log(f"cmd: {text!r} -> no intent")
                player.unduck()
                if cfg["sound"]:
                    play_wav("err.wav")
                notify(f"Didn't catch that: '{text}'")
                return
            msg = player.handle(*intent)
            log(f"cmd: {text!r} -> {intent} -> {msg!r}")
            if intent[0] in VOLUME_ACTIONS:
                player.commit_volume()  # user set a volume: don't restore over it
            else:
                player.unduck()
            if (cfg["notify"] == "smart" and intent[0] != "now_playing"
                    and msg.startswith(QUIET_OK)):
                if cfg["sound"]:
                    play_wav("ok.wav")
                print(msg)
                icon.title = f"Spotify Retro - {msg}"[:120]  # hover shows last action
            else:
                if cfg["sound"] and not msg.startswith(QUIET_OK) and intent[0] != "now_playing":
                    play_wav("err.wav")
                notify(msg)
        threading.Thread(target=work, daemon=True).start()

    def wake_hint():
        if cfg["sound"]:
            play_wav("wake.wav")
        if cfg["duck"]:
            threading.Thread(target=player.duck, daemon=True).start()

    mic_index, mic_name = pick_input(cfg["input_device"])
    print(f"Microphone: {mic_name}")
    log(f"mic: {mic_name!r} (index {mic_index})")
    bt_mic = any(k in mic_name.lower() for k in ("headset", "hands-free", "bthhfenum"))

    listener = voice.Listener(
        model, cfg["wake_phrase"], on_command,
        on_wake=lambda: notify("Listening..."),
        on_wake_hint=wake_hint,
        device=mic_index, debug=args.debug)
    listener.log = log
    listener.restart = threading.Event()

    switches = [0]

    def on_dead_mic():
        """Stream never produced audio: find an endpoint that's alive. Only in
        Automatic mode - an explicitly chosen mic (e.g. a gated Bluetooth
        headset that is silent until spoken into) is respected, always."""
        if cfg["input_device"] is not None or switches[0] >= 3:
            return
        switches[0] += 1
        exclude = (listener.device,) if isinstance(listener.device, int) else ()
        idx, name = pick_input(cfg["input_device"], exclude=exclude)
        if idx is not None and idx != listener.device:
            log(f"mic: dead air, switching to {name!r} (index {idx})")
            notify(f"Mic was silent - switched to {name}")
            listener.device = idx
            listener.restart.set()

    listener.on_dead_mic = on_dead_mic

    def toggle(icon_, item):
        listening.clear() if listening.is_set() else listening.set()

    def route_headset_output(family):
        """Bluetooth mics kill the hi-fi output; keep sound flowing by moving
        the system default output to the headset-quality endpoint (and back)."""
        def work():
            name = winaudio.route_output_to_headset(family)
            if name:
                log(f"audio: default output -> {name!r}")
                notify(f"Audio continues via {name} (phone quality while mic is on)")
        threading.Thread(target=work, daemon=True).start()

    def pick_mic(family):  # None = auto (probe everything)
        def do(icon_, item):
            cfg["input_device"] = family  # family name: we probe its endpoints
            save_config(d, cfg)
            switches[0] = 0
            winaudio.restore_output()  # leaving a previous Bluetooth selection
            idx, name = pick_input(family)
            log(f"mic: user picked {family!r} -> {name!r} (index {idx})")
            listener.device = idx
            listener.restart.set()
            if family and ("headset" in name.lower() or "hands-free" in name.lower()
                           or "bthhfenum" in name.lower()):
                route_headset_output(family)
        return do

    def mic_items():
        yield pystray.MenuItem("Automatic (recommended)", pick_mic(None),
                               checked=lambda item: cfg["input_device"] is None,
                               radio=True)
        seen, bt = set(), set()
        for _, name in input_devices():
            fam = mic_family(name)
            low = name.lower()
            if "hands-free" in low or "bthhfenum" in low or low.startswith("headset"):
                bt.add(fam)  # Bluetooth mic: using it silences A2DP music on Windows
            seen.add(fam)
        for fam in sorted(seen):
            label = (f"{fam}  (Windows limitation: headset audio degrades while its mic is on)"
                     if fam in bt else fam)
            yield pystray.MenuItem(label, pick_mic(fam),
                                   checked=lambda item, fam=fam: cfg["input_device"] == fam,
                                   radio=True)

    def toggle_startup(icon_, item):
        set_startup(not startup_lnk().exists())

    def reauth(icon_, item):
        (d / "token.json").unlink(missing_ok=True)
        notify("Token cleared - restart Spotify Retro to sign in again.")

    def quit_(icon_, item):
        winaudio.restore_output()  # never leave the system on the phone-quality output
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
    if cfg["input_device"] and bt_mic:  # resumed on a Bluetooth mic: keep audio flowing
        route_headset_output(cfg["input_device"])

    print(f'Running in the tray. Say "{cfg["wake_phrase"]}" then a command, '
          f'or "{cfg["wake_phrase"]}, play <song>" in one breath.')
    icon.run()


if __name__ == "__main__":
    main()
