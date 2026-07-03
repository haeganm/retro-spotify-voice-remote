<p align="center"><img src="docs/banner.png" alt="Retro - personalized voice music remote" width="100%"></p>

<p align="center">
  <a href="https://github.com/haeganm/retro/actions/workflows/ci.yml"><img src="https://github.com/haeganm/retro/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="License: GPL-3.0">
  <img src="https://img.shields.io/badge/python-3.10%E2%80%933.12-blue" alt="Python 3.10-3.12">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6" alt="Windows 10/11">
  <img src="https://img.shields.io/badge/cost-%240-1ed760" alt="$0">
</p>

# Retro

A free, local voice remote **for Spotify Premium**. Runs in your system tray,
listens offline (speech never leaves your machine), and controls whatever
device Spotify is already playing on — desktop, phone, speaker.

> **"hey retro, play bohemian rhapsody"**

<!-- demo GIF goes here: docs/demo.gif -->

## Why it's different

- **It knows *your* music.** Your top artists, playlists, and liked songs are
  injected into the recognizer's vocabulary and boosted in search ranking — so
  niche artist names and personal playlist names ("7.0", spoken as
  "seven point o") resolve correctly when generic assistants fail.
- **It forgives how people actually talk.** Two speech engines hear every
  command and their transcriptions *compete* in a fuzzy-ranked search — a
  garbled "play money to work by eat" still finds *Money Twërk by Yeat*.
  Control words ("skip", "pause") dispatch instantly with zero transcription
  latency.
- **Zero cloud, zero cost, zero secrets.** There is no server and no account:
  Spotify Connect is the player, auth is PKCE against your own free developer
  app, speech is processed entirely on-device (GPU-accelerated Whisper when
  you have an NVIDIA card), and your session token is encrypted at rest.

## Architecture

```mermaid
flowchart LR
    MIC([Microphone]) --> VOSK["Vosk (streaming)<br/>wake-word spotting"]
    VOSK -- "hey retro..." --> CUT[wake-phrase<br/>audio slicing]
    CUT --> WHISPER["Whisper (GPU/CPU)<br/>command transcription<br/>+ personal hotwords"]
    VOSK -. "control words<br/>(skip/pause/...)" .-> INTENT
    WHISPER --> INTENT[intent parser<br/>+ homophone aliases]
    INTENT --> SEARCH["dual-hearing fuzzy search<br/>artist affinity + ranking"]
    SEARCH --> API[Spotify Connect<br/>Web API]
    API --> DEV([Your Spotify device])
    INTENT -. feedback .-> OSD[instant OSD +<br/>sound cues]
```

Everything left of the Spotify API runs offline on your machine.

## Requirements

- Windows 10/11 (this app is Windows-only)
- Spotify **Premium** (the playback-control API requires it)
- Python 3.10–3.12 (`winget install Python.Python.3.12` if you don't have it)
- A microphone
- Spotify open on at least one device (the API commands a device; it doesn't produce audio)
- An NVIDIA GPU is optional — the installer detects one and enables faster,
  more accurate recognition automatically

## Install ($0, ~5 minutes)

**Step 1 — get Python** (skip if you have 3.10-3.12):

```powershell
winget install Python.Python.3.12
```

**Step 2 — get Retro** (either way works):

```powershell
git clone https://github.com/haeganm/retro
cd retro
powershell -ExecutionPolicy Bypass -File install.ps1
```

&nbsp;&nbsp;&nbsp;&nbsp;*No git? Click **Code → Download ZIP** on this page, extract it
anywhere permanent (not Downloads), then right-click `install.ps1` → **Run with
PowerShell**.*

The installer creates an isolated environment (nothing touches your system
Python), installs the tested dependency set, enables GPU acceleration if you
have an NVIDIA card, and puts a **Retro** icon on your desktop.

**Step 3 — connect your Spotify** (one time):

1. Go to <https://developer.spotify.com/dashboard>, log in with your Spotify
   account, and click **Create app**:
   - App name / description: anything ("Retro" works)
   - **Redirect URI** (must be exact): `http://127.0.0.1:8888/callback` — click **Add**
   - API: **Web API** → Save
2. On the app's **Settings** page, copy the **Client ID**.
3. Double-click the **Retro** icon on your desktop, paste the Client ID into
   the dialog, and approve the browser sign-in once. The speech models
   (~120 MB) download automatically on first run.

Done. Retro now lives in your system tray. Tray menu → **Start with Windows**
makes it launch silently at login.

<details><summary>Developer install (no installer)</summary>

```sh
pip install .        # or pip install -e . for hacking
retro                # console entry point
```
</details>

## Using the remote — read this first

> **Retro is a remote control, not a chatbot.** It matches *specific command
> patterns* — it cannot chat, answer questions, or figure out "play something
> chill for studying". Stick to the phrases below and it's fast and reliable;
> freestyle and it will say *"didn't catch that"*.

**The rhythm:** say the wake phrase, then the command —

- **One breath** (fastest): *"hey retro, play bohemian rhapsody"*
- **Two-step**: say *"hey retro"*, wait for the **chime**, then speak the
  command within ~6 seconds. Use this when the room is noisy.

**What the sounds mean:** chime = it heard the wake word · soft pop = command
done · low "uh-oh" = it heard you but couldn't match a command · the
corner overlay and tray-icon tooltip show exactly what it did.

### The commands

| Say | Does |
|---|---|
| `play <song>` / `play <song> by <artist>` | search and play a track |
| `queue <song>` / `play <song> next` / `add <song> to the queue` | add to queue |
| `play the artist <name>` / `play songs by <name>` | play an artist |
| `play the album <name>` | play an album |
| `playlist <name>` / `play my playlist <name>` | your playlists (names with numbers work: say "seven point o" for "7.0") |
| `play my liked songs` | shuffle your Liked Songs |
| `pause` / `stop` | pause |
| `play` / `resume` | resume |
| `next` / `skip` | next track |
| `previous` / `go back` | previous track (restarts the track if there's no previous) |
| `volume up` / `volume down` / `turn it up` | volume ±10 (Spotify volume, never system volume) |
| `set volume to fifty` | volume 0-100 |
| `what's playing` | show the current track |
| `shuffle on` / `shuffle off` | shuffle |
| `put it back` / `go back to what was playing` | undo — restore what was playing before |

### Getting the best recognition

- **Include the artist for anything that isn't famous**: *"play brain stew by
  green day"* beats *"play brain stew"*. Title + artist is the single biggest
  accuracy win.
- **Speak at a normal pace** and let the sentence end — Retro processes when
  you stop talking (about half a second of silence).
- Say **the actual title**, not a description. "play that one from the gym
  playlist" won't work; "playlist gym" will.
- Control words ("skip", "pause") respond instantly; song searches take a
  moment (speech transcription + Spotify search).
- If it keeps mishearing a phrase, run `retro --misses` to see what it
  actually heard — the transcript log (`%APPDATA%\Retro\retro.log`) shows
  both recognition engines' hearings for every command.
- Test any command without speaking: `retro --say "play daft punk"`.

## Config

`%APPDATA%\Retro\config.json`:

```json
{
  "client_id": "...",
  "wake_phrase": "hey retro",
  "model": "small",
  "input_device": null,
  "sound": true,
  "stt": "whisper",
  "whisper_model": "auto",
  "device": "auto",
  "notify": "smart",
  "duck": true,
  "osd": true,
  "log": true
}
```

- `model`: the Vosk wake-word model - `"small"` (default) or `"medium"`
- `stt`: `"whisper"` (default) or `"vosk"` to skip Whisper on very weak machines
- `whisper_model`: `"auto"` (default: small.en on GPU, base.en on CPU) or any
  faster-whisper model name
- `device`: `"auto"` (default: NVIDIA GPU when present - install
  `nvidia-cublas-cu12 nvidia-cudnn-cu12` - else CPU), `"cuda"`, or `"cpu"`
- `input_device`: pick a microphone from the tray menu instead of editing this
- `sound`: wake chime + success/error cues; `false` to disable
- `duck`: `true` (default) - music volume dips while you speak a command,
  restored right after (big win for speaker setups)
- `notify`: `"smart"` (default - subtle sound for control commands, voice/toast
  for results and errors; the tray tooltip always shows the last action) or
  `"all"` to toast everything
- `log`: `true` (default) - keep the local speech transcript log for
  debugging; `false` disables it

## Tray menu

Listening on/off · Microphone picker · Start with Windows · Re-authenticate · Quit

## Tests

```sh
python test_intents.py   # parser + player logic (offline, instant)
python e2e_voice.py      # synthesized speech through the real model (Windows)
```

## Privacy & security

- **Speech never leaves your machine** - wake-word and transcription are fully
  offline. Outbound traffic is exactly: `api.spotify.com`/`accounts.spotify.com`
  (HTTPS, playback + auth) and two one-time model downloads
  (`alphacephei.com`, SHA-256-verified; `huggingface.co`, hash-verified). No
  telemetry, no update checks.
- **Auth is OAuth PKCE** - no client secret exists anywhere. Your Spotify
  token is encrypted at rest with Windows DPAPI; your Client ID is not a
  secret. Everything lives in `%APPDATA%\Retro`, never in the repo.
- **Transcript log**: recognized speech is kept locally in `retro.log` (last
  ~500 lines) for debugging. Set `"log": false` in config to disable, or
  delete the file anytime.
- **System-touching behaviors** (both user-triggered, both reversible):
  "Start with Windows" creates a Startup shortcut; selecting a Bluetooth
  headset mic temporarily switches the Windows default audio output so your
  music keeps playing, restored when you switch back - crash-safe.

See [SECURITY.md](SECURITY.md) for the full threat model.

## Troubleshooting

| Symptom | Do this |
|---|---|
| It never hears the wake word | `retro --mic-test` — speak while it runs; pick the mic with the biggest bar (tray → Microphone), or leave it on Automatic |
| It mishears commands | `retro --misses` lists everything it failed to parse; `%APPDATA%\Retro\retro.log` shows exactly what each engine heard |
| "No Spotify device found" | Open Spotify on any device — this is a remote, not a player |
| Music goes quiet/mono on a Bluetooth headset mic | Windows can't do hi-fi audio + headset mic at once; the app keeps sound flowing on the headset channel and restores hi-fi when you switch mics. Use Automatic (a wired/built-in mic) for full quality |
| Commands need Premium | The Spotify playback API rejects free accounts |

Uninstall: delete the repo folder, `%APPDATA%\Retro`, and the desktop
shortcut (plus tray → "Start with Windows" off, or delete the Startup shortcut).

## License & credits

**GPL-3.0** — see [LICENSE](LICENSE). Use it, learn from it, fork it — but
derivatives must stay open source with attribution. Copyright (C) 2026
Haegan McGarry.

Built on [Vosk](https://alphacephei.com/vosk/) (Apache-2.0),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) / OpenAI Whisper
(MIT), and [spotipy](https://github.com/spotipy-dev/spotipy) (MIT).

---

*Retro is an independent project — not affiliated with, endorsed by, or
sponsored by Spotify AB. Spotify is a trademark of Spotify AB. Requires a
user-created Spotify developer application and a Spotify Premium account.*
