"""Spotify Connect control via the Web API. Premium required for playback endpoints."""
import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE

SCOPES = "user-modify-playback-state user-read-playback-state"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
NO_DEVICE = "No Spotify device found - open Spotify on any device first."


class Player:
    def __init__(self, client_id, cache_path):
        self.sp = spotipy.Spotify(auth_manager=SpotifyPKCE(
            client_id=client_id,
            redirect_uri=REDIRECT_URI,
            scope=SCOPES,
            cache_handler=CacheFileHandler(cache_path=str(cache_path)),
        ))

    def handle(self, action, arg=None):
        """Dispatch a parsed intent; always returns a human-readable message."""
        try:
            fn = getattr(self, action)
            return fn(arg) if arg is not None else fn()
        except spotipy.SpotifyException as e:
            return f"Spotify error: {getattr(e, 'msg', None) or e}"
        except Exception as e:  # ponytail: mic loop must never die on a bad API call
            return f"Error: {e}"

    # -- helpers --------------------------------------------------------
    def _device(self):
        """Active device id, transferring playback to the first available if none."""
        pb = self.sp.current_playback()
        if pb and pb.get("device"):
            return pb["device"]["id"]
        devices = self.sp.devices()["devices"]
        if not devices:
            return None
        dev = devices[0]["id"]
        self.sp.transfer_playback(dev, force_play=False)
        return dev

    def _search(self, query, kind):
        items = self.sp.search(q=query, type=kind, limit=1)[kind + "s"]["items"]
        return items[0] if items else None

    def _play_context(self, name, kind):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        item = self._search(name, kind)
        if not item:
            return f"No {kind} found for '{name}'"
        self.sp.start_playback(device_id=dev, context_uri=item["uri"])
        return f"Playing {kind} {item['name']}"

    def _volume(self):
        pb = self.sp.current_playback()
        if pb and pb.get("device"):
            return pb["device"]["volume_percent"] or 50
        return 50

    # -- commands ---------------------------------------------------------
    def play_track(self, query):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        t = self._search(query, "track")
        if not t and " by " in query:
            t = self._search(query.replace(" by ", " "), "track")
        if not t:
            return f"No results for '{query}'"
        self.sp.start_playback(device_id=dev, uris=[t["uri"]])
        return f"Playing {t['name']} by {t['artists'][0]['name']}"

    def play_artist(self, name):
        return self._play_context(name, "artist")

    def play_album(self, name):
        return self._play_context(name, "album")

    def play_playlist(self, name):
        return self._play_context(name, "playlist")

    def resume(self):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        self.sp.start_playback(device_id=dev)
        return "Resuming"

    def pause(self):
        self.sp.pause_playback()
        return "Paused"

    def next_track(self):
        self.sp.next_track()
        return "Skipped"

    def previous_track(self):
        self.sp.previous_track()
        return "Previous track"

    def set_volume(self, n):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        n = max(0, min(100, int(n)))
        self.sp.volume(n, device_id=dev)
        return f"Volume {n}"

    def volume_up(self):
        return self.set_volume(self._volume() + 10)

    def volume_down(self):
        return self.set_volume(self._volume() - 10)

    def now_playing(self):
        pb = self.sp.current_playback()
        if not pb or not pb.get("item"):
            return "Nothing playing"
        i = pb["item"]
        return f"{i['name']} by {', '.join(a['name'] for a in i['artists'])}"

    def _set_shuffle(self, state):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        self.sp.shuffle(state, device_id=dev)
        return f"Shuffle {'on' if state else 'off'}"

    def shuffle_on(self):
        return self._set_shuffle(True)

    def shuffle_off(self):
        return self._set_shuffle(False)
