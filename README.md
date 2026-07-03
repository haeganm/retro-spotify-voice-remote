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
(base.en, CPU int8, ~0.5s), which is far better at song titles and artist
names. Whichever transcription parses as a valid command wins.

It also learns you: your top Spotify artists become Whisper *hotwords* (so
"Yeat" decodes as Yeat, not "beat") and get a ranking boost in search — an
artist you actually listen to beats a sound-alike stranger.

Song lookup doesn't trust Spotify's #1 hit: candidates from several search
strategies are ranked by fuzzy similarity to what you actually said, so long
titles and non-chart-toppers resolve correctly ("play cigarettes out the window
by tv girl" finds TV Girl, not whatever is popular this week).

## Requirements

- Spotify **Premium** (the playback-control API requires it)
- Python 3.10+
- A microphone
- Spotify open on at least one device (the API commands a device; it doesn't produce audio)

## Setup (5 minutes, $0)

1. Create a free app at <https://developer.spotify.com/dashboard>
   - Redirect URI: `http://127.0.0.1:8888/callback`
   - API: Web API
2. Install and run:

   ```sh
   git clone https://github.com/YOURNAME/spotify-retro
   cd spotify-retro
   pip install .
   spotify-retro
   ```

3. First run: paste your app's **Client ID**, approve the browser sign-in once
   (PKCE — no client secret involved), and let it download the speech models
   (~40 MB Vosk + ~75 MB Whisper). After that it just runs.

Tray menu → **Start with Windows** makes it launch silently at login.

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
| `switch to my phone` / `play it on the speaker` | move playback to another device |
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

`%APPDATA%\SpotifyRetro\config.json` (Windows) or `~/.config/SpotifyRetro/config.json`:

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
- `speak`: `true` (default) - results are spoken aloud (offline SAPI voice);
  `false` for toasts only
- `duck`: `true` (default) - music volume dips while you speak a command,
  restored right after (big win for speaker setups)
- `notify`: `"smart"` (default - subtle sound for control commands, voice/toast
  for results and errors; the tray tooltip always shows the last action) or
  `"all"` to toast everything

## Tray menu

Listening on/off · Microphone picker · Start with Windows · Re-authenticate · Quit

## Tests

```sh
python test_intents.py   # parser + player logic (offline, instant)
python e2e_voice.py      # synthesized speech through the real model (Windows)
```

## Cross-platform

Vosk, sounddevice, spotipy, and pystray all support Windows/macOS/Linux.
The Startup shortcut and TTS-based e2e test are Windows-only. Built and
tested on Windows 11.

## License

MIT
