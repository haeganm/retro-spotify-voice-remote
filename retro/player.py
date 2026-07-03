"""Spotify Connect control via the Web API. Premium required for playback endpoints."""
import difflib
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE

SCOPES = ("user-modify-playback-state user-read-playback-state "
          "playlist-read-private user-library-read user-top-read")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
NO_DEVICE = "No Spotify device found - open Spotify on any device first."
_POOL = ThreadPoolExecutor(max_workers=4)  # concurrent search variants


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def score(query, name, artists=(), popularity=0):
    """Similarity of a heard query to a candidate (0..~1.1). Compares against
    'title artists', title alone (the query may omit the artist), and every
    'title by artist' reading of the query - so 'brain stew by green day'
    scores the artist explicitly instead of hoping the concatenation matches.
    Popularity is only a small tiebreak."""
    q = _norm(query)
    title = _norm(name)
    art = _norm(" ".join(artists))
    cands = [_sim(q, f"{title} {art}".strip()), _sim(q, title)]
    parts = query.split(" by ")
    for i in range(1, len(parts)):
        t = _norm(" by ".join(parts[:i]))
        a = _norm(" by ".join(parts[i:]))
        cands.append(0.6 * _sim(t, title) + 0.4 * _sim(a, art))
    return max(cands) + popularity / 1000.0


def query_variants(query):
    """The raw query, plus a field-filtered variant for every possible
    ' by ' split — so 'stand by me by ben e king' tries both splits and
    'stand by me' alone still matches the title."""
    yield query
    parts = query.split(" by ")
    for i in range(1, len(parts)):
        title, artist = " by ".join(parts[:i]), " by ".join(parts[i:])
        yield f"track:{title} artist:{artist}"


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
        """Active device id (cached briefly - a lookup per command costs a
        round trip), transferring playback to the first available if none."""
        ts, dev = getattr(self, "_dev_cache", (0.0, None))
        if dev and time.monotonic() - ts < 10:
            return dev
        pb = self.sp.current_playback()
        if pb and pb.get("device"):
            dev = pb["device"]["id"]
        else:
            devices = self.sp.devices()["devices"]
            if not devices:
                return None
            dev = devices[0]["id"]
            self.sp.transfer_playback(dev, force_play=False)
        self._dev_cache = (time.monotonic(), dev)
        return dev

    def user_artists(self):
        """Names of artists this user actually listens to (top + liked),
        cached for the process lifetime. Used to bias search ranking and
        Whisper's vocabulary."""
        arts = getattr(self, "_arts_cache", None)
        if arts is not None:
            return arts
        names = set()
        try:
            for a in self.sp.current_user_top_artists(limit=50, time_range="medium_term")["items"]:
                names.add(a["name"])
        except Exception:
            pass
        try:
            for it in self.sp.current_user_saved_tracks(limit=50)["items"]:
                for a in it["track"].get("artists", []):
                    names.add(a["name"])
        except Exception:
            pass
        self._arts_cache = names
        return names

    def _artist_tracks(self, query):
        """When the query names an artist ('x by green day'), that artist's
        popular tracks rescue a garbled title STT couldn't spell. (The
        top-tracks endpoint is 403 for new dev-mode apps; artist-filtered
        search is allowed and equivalent here.)"""
        artist = query.rsplit(" by ", 1)[1]
        try:
            return self.sp.search(q=f"artist:{artist}", type="track",
                                  limit=10)["tracks"]["items"]  # >10 is 400 for dev-mode apps
        except Exception:
            return []

    def _best_track(self, query):
        """Rank up to 10 hits per query variant by fuzzy similarity instead of
        trusting Spotify's #1 (which favors chart-toppers over exact matches).
        All lookups run concurrently."""
        variants = list(query_variants(query))
        futures = [_POOL.submit(self.sp.search, q=q, type="track", limit=10)
                   for q in variants]
        top_f = _POOL.submit(self._artist_tracks, query) if " by " in query else None
        cands = {}
        for f in futures:
            for t in f.result()["tracks"]["items"]:
                cands[t["uri"]] = t
        for t in (top_f.result() if top_f else []):
            cands.setdefault(t["uri"], t)
        if not cands:
            return None
        fav = {_norm(n) for n in self.user_artists()}

        def key(t):
            s = score(query, t["name"], [a["name"] for a in t["artists"]],
                      t.get("popularity", 0))
            if any(_norm(a["name"]) in fav for a in t["artists"]):
                s += 0.12  # artists you listen to beat sound-alike strangers
            return s

        return max(cands.values(), key=key)

    def _search(self, query, kind):
        items = self.sp.search(q=query, type=kind, limit=5)[kind + "s"]["items"]
        items = [i for i in items if i]  # public playlist search can return nulls
        if not items:
            return None
        return max(items, key=lambda i: score(query, i["name"], (), i.get("popularity", 0)))

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
        t = self._best_track(query)
        if not t:
            return f"No results for '{query}'"
        self.sp.start_playback(device_id=dev, uris=[t["uri"]])
        return f"Playing {t['name']} by {t['artists'][0]['name']}"

    def play_artist(self, name):
        return self._play_context(name, "artist")

    def play_album(self, name):
        return self._play_context(name, "album")

    def queue_track(self, query):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        t = self._best_track(query)
        if not t:
            return f"No results for '{query}'"
        self.sp.add_to_queue(t["uri"], device_id=dev)
        return f"Queued {t['name']} by {t['artists'][0]['name']}"

    def _my_playlists(self):
        """All of the user's playlists (paginated), cached for 5 minutes."""
        ts, pls = getattr(self, "_pl_cache", (0.0, None))
        if pls is not None and time.monotonic() - ts < 300:
            return pls
        pls, res = [], self.sp.current_user_playlists(limit=50)
        while res:
            pls += [p for p in res["items"] if p]
            res = self.sp.next(res) if res.get("next") else None
        self._pl_cache = (time.monotonic(), pls)
        return pls

    def play_playlist(self, name):
        """Your own playlists first (public search can't see private ones),
        then public search. Playlist names carry emojis and decorations, so
        containment ('gym' in 'GYM PUMP mix') counts as a strong match."""
        dev = self._device()
        if not dev:
            return NO_DEVICE

        def pl_score(p):
            s, n = _norm(name), _norm(p["name"])
            return max(_sim(s, n), 0.95 if s and s in n else 0.0)

        mine = self._my_playlists()
        if mine:
            best = max(mine, key=pl_score)
            if pl_score(best) >= 0.55:
                self.sp.start_playback(device_id=dev, context_uri=best["uri"])
                return f"Playing playlist {best['name']}"
        return self._play_context(name, "playlist")

    def play_liked(self):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        items = self.sp.current_user_saved_tracks(limit=50)["items"]
        if not items:
            return "No liked songs found"
        uris = [i["track"]["uri"] for i in items]
        random.shuffle(uris)
        self.sp.start_playback(device_id=dev, uris=uris)
        return "Playing your liked songs"

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
