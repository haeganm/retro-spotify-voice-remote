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


_NUM_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2",
              "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
              "eight": "8", "nine": "9", "ten": "10", "point": "."}


def _pl_score(said, name):
    """Playlist-name match. Real names carry emojis/decorations (containment
    counts) and version numbers ('7.0' is spoken 'seven point o' - digitize
    the speech and compare stripped)."""
    s, n = _norm(said), _norm(name)
    cands = [_sim(s, n), 0.95 if s and s in n else 0.0]
    ds = re.sub(r"[^a-z0-9]", "", "".join(_NUM_WORDS.get(w, w) for w in said.lower().split()))
    dn = re.sub(r"[^a-z0-9]", "", name.lower())
    if ds and dn:
        cands.append(_sim(ds, dn))
        if ds in dn:
            cands.append(0.9)
    return max(cands)


def _queries(query):
    """A command arg may carry both engines' hearings joined by '|'."""
    return [q.strip() for q in query.split("|") if q.strip()]


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
        """Artists this user actually listens to, most-relevant first (top
        artists ranked, then liked-track artists), cached for the process
        lifetime. Used to bias search ranking and Whisper's vocabulary."""
        arts = getattr(self, "_arts_cache", None)
        if arts is not None:
            return arts
        names = {}  # insertion-ordered set
        try:
            for a in self.sp.current_user_top_artists(limit=50, time_range="medium_term")["items"]:
                names[a["name"]] = None
        except Exception:
            pass
        try:
            for it in self.sp.current_user_saved_tracks(limit=50)["items"]:
                for a in it["track"].get("artists", []):
                    names[a["name"]] = None
        except Exception:
            pass
        self._arts_cache = list(names)
        return self._arts_cache

    def user_titles(self):
        """Titles of the user's 50 most recent liked tracks (hotword fodder)."""
        titles = getattr(self, "_titles_cache", None)
        if titles is not None:
            return titles
        try:
            titles = [it["track"]["name"]
                      for it in self.sp.current_user_saved_tracks(limit=50)["items"]]
        except Exception:
            titles = []
        self._titles_cache = titles
        return titles

    def _snapshot(self):
        """Remember what's playing so 'put it back' can restore it."""
        try:
            pb = self.sp.current_playback()
            if pb and pb.get("item"):
                self._undo = {
                    "context": (pb.get("context") or {}).get("uri"),
                    "track": pb["item"]["uri"],
                    "name": pb["item"]["name"],
                    "pos": pb.get("progress_ms") or 0,
                }
        except Exception:
            pass

    def put_back(self):
        undo = getattr(self, "_undo", None)
        if not undo:
            return "Nothing to go back to"
        dev = self._device()
        if not dev:
            return NO_DEVICE
        if undo["context"]:
            self.sp.start_playback(device_id=dev, context_uri=undo["context"],
                                   offset={"uri": undo["track"]},
                                   position_ms=undo["pos"])
        else:
            self.sp.start_playback(device_id=dev, uris=[undo["track"]],
                                   position_ms=undo["pos"])
        return f"Back to {undo['name']}"

    def duck(self):
        """Quiet the music while the user speaks a command (speaker setups
        drown the mic otherwise). Remembers the level for unduck()."""
        try:
            pb = self.sp.current_playback()
            vol = pb["device"]["volume_percent"] if pb and pb.get("device") else None
            if vol is not None and vol >= 30 and pb["device"]["id"]:
                self._ducked = (vol, pb["device"]["id"])
                self.sp.volume(15, device_id=pb["device"]["id"])
        except Exception:
            pass

    def unduck(self):
        ducked = getattr(self, "_ducked", None)
        self._ducked = None
        if ducked:
            try:
                self.sp.volume(ducked[0], device_id=ducked[1])
            except Exception:
                pass

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
        The query may be several '|'-joined hearings; candidates from all of
        them compete and each is scored against its best-matching hearing.
        All lookups run concurrently."""
        queries = _queries(query)
        variants = list(dict.fromkeys(
            v for q in queries for v in query_variants(q)))
        futures = [_POOL.submit(self.sp.search, q=q, type="track", limit=10)
                   for q in variants]
        top_fs = [_POOL.submit(self._artist_tracks, q)
                  for q in queries if " by " in q]
        cands = {}
        for f in futures:
            for t in f.result()["tracks"]["items"]:
                cands[t["uri"]] = t
        for f in top_fs:
            for t in f.result():
                cands.setdefault(t["uri"], t)
        if not cands:
            return None
        fav = {_norm(n) for n in self.user_artists()}

        def key(t):
            s = max(score(q, t["name"], [a["name"] for a in t["artists"]],
                          t.get("popularity", 0)) for q in queries)
            if any(_norm(a["name"]) in fav for a in t["artists"]):
                s += 0.12  # artists you listen to beat sound-alike strangers
            return s

        return max(cands.values(), key=key)

    def _search(self, query, kind):
        items = []
        for q in _queries(query):
            items += [i for i in self.sp.search(q=q, type=kind, limit=5)[kind + "s"]["items"]
                      if i]  # public playlist search can return nulls
        if not items:
            return None
        return max(items, key=lambda i: max(
            score(q, i["name"], (), i.get("popularity", 0)) for q in _queries(query)))

    def _play_context(self, name, kind):
        self._snapshot()
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
        self._snapshot()
        dev = self._device()
        if not dev:
            return NO_DEVICE
        t = self._best_track(query)
        if not t:
            return f"No results for '{query.replace('|', ' / ')}'"
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
        then public search. 'Liked songs' feels like a playlist to users but
        isn't one to Spotify - route it."""
        self._snapshot()
        names = _queries(name)
        if any(_sim(_norm(n), "liked songs") >= 0.7 for n in names):
            return self.play_liked()
        dev = self._device()
        if not dev:
            return NO_DEVICE

        def best_score(p):
            return max(_pl_score(n, p["name"]) for n in names)

        mine = self._my_playlists()
        if mine:
            best = max(mine, key=best_score)
            if best_score(best) >= 0.55:
                self.sp.start_playback(device_id=dev, context_uri=best["uri"])
                return f"Playing playlist {best['name']}"
        res = self._play_context(name, "playlist")
        if res.startswith("No playlist") and mine:
            ranked = sorted(mine, key=best_score, reverse=True)
            closest = ", ".join(p["name"] for p in ranked[:3])
            return f"No playlist like '{names[0]}'. Closest: {closest}"
        return res

    def play_liked(self):
        self._snapshot()
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
        dev = self._device()
        if not dev:
            return NO_DEVICE
        self.sp.pause_playback(device_id=dev)
        return "Paused"

    def next_track(self):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        self.sp.next_track(device_id=dev)
        return "Skipped"

    def previous_track(self):
        dev = self._device()
        if not dev:
            return NO_DEVICE
        self.sp.previous_track(device_id=dev)
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
