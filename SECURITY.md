# Security

## Reporting

Found a vulnerability? Open a GitHub issue (nothing here warrants private
disclosure channels: the app holds no server, no user database, and no secrets
beyond your own Spotify session on your own machine). If you believe you've
found something sensitive anyway, open an issue asking for a contact.

## Threat model (what this app can and cannot protect against)

**Attack surface:**

- **Network, inbound:** none. Two loopback-only sockets exist: a bind-only
  single-instance lock on `127.0.0.1:48765` (never listens or accepts), and
  spotipy's OAuth redirect catcher on `127.0.0.1:8888` that exists only for
  the seconds of the one-time browser sign-in.
- **Network, outbound (all HTTPS):** `api.spotify.com` / `accounts.spotify.com`
  (playback control + auth), `alphacephei.com` (one-time Vosk model download,
  SHA-256-verified against pinned hashes), `huggingface.co` (one-time Whisper
  model download, hash-verified by huggingface_hub). Nothing else - no
  telemetry, no update checks.
- **Secrets:** your Spotify OAuth token is the only secret. It is encrypted at
  rest with Windows DPAPI (user-scoped), so other Windows accounts and offline
  disk access can't read it. There is no client secret anywhere - the app uses
  OAuth PKCE by design. Your Client ID is not a secret.
- **Local files:** everything lives in `%APPDATA%\Retro` - config,
  encrypted token, speech models, and (unless disabled with `"log": false`)
  a transcript log of recognized speech for debugging.
- **System-touching behaviors, by design and user-triggered:** creating a
  Startup shortcut ("Start with Windows"), and switching the Windows default
  audio output while a Bluetooth headset mic is explicitly selected (restored
  afterwards, crash-safe via a marker file).

**Out of scope:** malware running as your user defeats every control above
(it can read DPAPI-protected data, your mic, and your keystrokes). Protecting
a compromised machine is an operating-system problem, not an app problem.

## Supply chain

- `requirements.lock` pins exact tested versions; `pip-audit` runs against it
  (clean as of the last release).
- CI actions are pinned to commit SHAs.
- `bandit` static analysis runs clean under the policy in `pyproject.toml`
  (each skip is justified there).
