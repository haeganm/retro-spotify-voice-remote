<p align="center"><img src="retro/assets/icon.png" width="128" alt="Spotify Retro"></p>

# Spotify Retro

A free, local voice remote for Spotify Premium. Runs in your system tray, listens
offline (speech never leaves your machine), and controls whatever device Spotify
is already playing on — desktop, phone, speaker.

> "hey retro, play bohemian rhapsody"

## How it works

There's no music player in here. Spotify Connect's Web API *is* the remote:
**mic → offline speech recognition → intent → one HTTPS call to
api.spotify.com**. The only network traffic is the Spotify API itself.

Speech is a two-stage hybrid, all offline: [Vosk](https://alphacephei.com/vosk/)
streams the mic and spots the wake phrase instantly; the command utterance is
then re-transcribed by [Whisper](https://github.com/SYSTRAN/faster-whisper)
(GPU when available, ~0.15s; CPU otherwise, ~0.6s), which is far better at
song titles and artist names. Whichever transcription parses as a valid
command wins.

It also learns you: your top Spotify artists become Whisper *hotwords* (so
"Yeat" decodes as Yeat, not "beat") and get a ranking boost in search — an
artist you actually listen to beats a sound-alike stranger.

Song lookup doesn't trust Spotify's #1 hit: candidates from several search
strategies are ranked by fuzzy similarity to what you actually said, so long
titles and non-chart-toppers resolve correctly ("play cigarettes out the window
by tv girl" finds TV Girl, not whatever is popular this week).

## Requirements

- Windows 10/11 (this app is Windows-only)
- Spotify **Premium** (the playback-control API requires it)
- Python 3.10–3.12 (`winget install Python.Python.3.12` if you don't have it)
- A microphone
- Spotify open on at least one device (the API commands a device; it doesn't produce audio)
- An NVIDIA GPU is optional — the installer detects one and enables faster,
  more accurate recognition automatically

## Install (one command, $0)

```powershell
git clone https://github.com/YOURNAME/spotify-retro
cd spotify-retro
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer creates an isolated environment, installs the tested dependency
set (`requirements.lock`), enables GPU acceleration if you have an NVIDIA
card, and puts a **Spotify Retro** icon on your desktop.

Then the one-time human part (5 minutes):

1. Create a free app at <https://developer.spotify.com/dashboard>
   - Redirect URI (exactly): `http://127.0.0.1:8888/callback`
   - API: Web API
2. Double-click the desktop icon, paste your app's **Client ID** into the
   dialog, and approve the browser sign-in once (PKCE — no client secret
   involved). The speech models (~120 MB) download on first run.

After that it just runs. Tray menu → **Start with Windows** makes it launch
silently at login.

<details><summary>Developer install (no installer)</summary>

```sh
pip install .        # or pip install -e . for hacking
spotify-retro        # console entry point
```
</details>

## Commands

Say the wake phrase (**"hey retro"** by default — a short beep confirms it heard
you), then:

| Say | Does |
|---|---|
| `play <song>` / `play <song> by <artist>` | search and play a track |
| `queue <song>` / `play <song> next` / `add <song> to the queue` | add to queue |
| `play the artist <name>` / `play songs by <name>` | play an artist |
| `play the album <name>` / `play my playlist <name>` | album / your playlists |
| `play my liked songs` | shuffle your liked songs |
| `pause` / `stop` | pause |
| `play` / `resume` | resume |
| `next` / `skip` | next track |
| `previous` / `go back` | previous track |
| `volume up` / `turn it up` / `set volume to fifty` | Spotify volume (never system volume) |
| `what's playing` | show current track |
| `shuffle on` / `shuffle off` | shuffle |
| `put it back` / `go back to what was playing` | undo - restore what was on before |

Say it in one breath ("hey retro play thriller") or wait for the beep /
*Listening...* notification after the wake phrase.

Test without a mic: `spotify-retro --say "play daft punk"` ·
Debug what it hears: `spotify-retro --debug`, or check the transcript log at
`%APPDATA%\SpotifyRetro\retro.log` (every recognition + command outcome) ·
List everything it failed to understand: `spotify-retro --misses`.

Control commands (skip/pause/volume/...) dispatch instantly from the wake-word
engine; only title-carrying commands (play/queue/...) take the ~0.6s Whisper pass.

## Config

`%APPDATA%\SpotifyRetro\config.json`:

```json
{
  "client_id": "...",
  "wake_phrase": "hey retro",
  "model": "small",
  "input_device": null,
  "sound": true,
  "stt": "whisper",
  "whisper_model": "base.en"
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
  secret. Everything lives in `%APPDATA%\SpotifyRetro`, never in the repo.
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
| It never hears the wake word | `spotify-retro --mic-test` — speak while it runs; pick the mic with the biggest bar (tray → Microphone), or leave it on Automatic |
| It mishears commands | `spotify-retro --misses` lists everything it failed to parse; `%APPDATA%\SpotifyRetro\retro.log` shows exactly what each engine heard |
| "No Spotify device found" | Open Spotify on any device — this is a remote, not a player |
| Music goes quiet/mono on a Bluetooth headset mic | Windows can't do hi-fi audio + headset mic at once; the app keeps sound flowing on the headset channel and restores hi-fi when you switch mics. Use Automatic (a wired/built-in mic) for full quality |
| Commands need Premium | The Spotify playback API rejects free accounts |

Uninstall: delete the repo folder, `%APPDATA%\SpotifyRetro`, and the desktop
shortcut (plus tray → "Start with Windows" off, or delete the Startup shortcut).

## License

MIT — see [LICENSE](LICENSE). Speech models: Vosk (Apache-2.0),
faster-whisper/Whisper (MIT).
