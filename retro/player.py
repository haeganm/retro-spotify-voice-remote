"""Spotify Connect control via the Web API. Premium required for playback endpoints."""
import difflib
import random
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE

SCOPES = ("user-modify-playback-state user-read-playback-state "
          "playlist-read-private user-library-read user-top-read")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
NO_DEVICE = "No Spotify device found - open Spotify on any device first."
_POOL = ThreadPoolExecutor(max_workers=8)  # searches + device prep in parallel


def _fold(s):
    """Stylized titles ('Wôa', 'Monëy Twërk') fold to ASCII instead of losing
    the accented letters when non-alphanumerics are stripped."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", _fold(s).lower()).strip()


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _squash(s):
    """Collapse letter runs so onomatopoeia matches at any length STT picked:
    'sh'/'shh'/'shhh' all become 'sh'."""
    return re.sub(r"(.)\1+", r"\1", s)


# Titles carrying one of these that the user didn't ask for are nudged down -
# 'california love' should get the original, not 'California Love (Remix)'.
_VERSION_WORDS = frozenset(("remix live cover karaoke instrumental acoustic sped "
                            "slowed acapella nightcore mashup medley remake reverb "
                            "8d tribute parody").split())


def _base_title(name):
    """Search titles carry decorations nobody speaks: '90210 (feat. Kacy
    Hill)', 'California Love - Original Version'. Strip bracketed chunks and
    ' - ...' suffixes for an undecorated comparison candidate."""
    return _norm(re.sub(r"[(\[].*?[)\]]", " ", name).split(" - ")[0]) or _norm(name)


def score(query, name, artists=(), popularity=0):
    """Similarity of a heard query to a candidate (0..~1.1). Compares against
    decorated AND undecorated titles ('90210 (feat. Kacy Hill)' is spoken
    '90210'), all-artists and primary-artist forms (feat credits dilute
    otherwise), every ' by ' split, and every word split (people drop the
    'by': '90210 travis scott'). Letter-run-collapsed and spoken-digit
    readings join the same max() - rescues that never demote a match.
    Unasked-for version markers (remix/live/...) are nudged down. Popularity
    is only a small tiebreak (and absent from search results these days -
    _best_track's rank bonus covers that job)."""
    q = _norm(query)
    title = _norm(name)
    base = _base_title(name)
    art = _norm(" ".join(artists))
    prim = _norm(artists[0]) if artists else art
    dq = _digits(query)
    spoken_nums = dq != re.sub(r"[^a-z0-9]", "", q)  # query had number words

    def t_sim(t):  # best reading of a title guess, squashed too
        return max(_sim(t, title), _sim(t, base),
                   _sim(_squash(t), _squash(title)), _sim(_squash(t), _squash(base)))

    def a_sim(a):
        return max(_sim(a, art), _sim(a, prim))

    cands = [t_sim(q)]
    for a in {art, prim}:
        full = f"{title} {a}".strip()
        cands += [_sim(q, full), _sim(q, f"{base} {a}".strip()),
                  _sim(_squash(q), _squash(full))]
    if spoken_nums:
        cands += [_sim(dq, _digits(name)),
                  _sim(dq, _digits(f"{name} {' '.join(artists)}"))]
    parts = query.split(" by ")
    splits = [(" by ".join(parts[:i]), " by ".join(parts[i:]))
              for i in range(1, len(parts))]
    words = q.split()
    splits += [(" ".join(words[:i]), " ".join(words[i:]))
               for i in range(1, len(words))]
    for t, a in splits:
        t, a = _norm(t), _norm(a)
        s = 0.6 * t_sim(t) + 0.4 * a_sim(a)
        if spoken_nums:
            s = max(s, 0.6 * _sim(_digits(t), _digits(name)) + 0.4 * a_sim(a))
        cands.append(s)
    best = max(cands)
    if (_VERSION_WORDS & set(title.split())) - set(q.split()):
        best -= 0.08  # a remix/live/... the user didn't ask for
    return best + popularity / 1000.0


_NUM_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2",
              "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
              "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
              "twelve": "12", "thirteen": "13", "fourteen": "14",
              "fifteen": "15", "sixteen": "16", "seventeen": "17",
              "eighteen": "18", "nineteen": "19", "twenty": "20",
              "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
              "seventy": "70", "eighty": "80", "ninety": "90", "point": "."}


def _spoken_digits(text):
    """Rewrite spoken number words as digits, gluing adjacent ones together:
    'nine oh two one oh by travis scott' -> '90210 by travis scott'."""
    toks, run = [], []
    for w in text.split():
        d = _NUM_WORDS.get(w)
        if d is None:
            if run:
                toks.append("".join(run))
                run = []
            toks.append(w)
        else:
            run.append(d)
    if run:
        toks.append("".join(run))
    return " ".join(toks)


def _digits(text):
    """Digitized squash for matching numeric titles: 'nine oh two one oh'
    and '90210' both become '90210'."""
    return re.sub(r"[^a-z0-9]", "", _spoken_digits(_fold(text).lower()))


def _pl_score(said, name):
    """Playlist-name match. Real names carry emojis/decorations (containment
    counts) and version numbers ('7.0' is spoken 'seven point o' - digitize
    the speech and compare stripped)."""
    s, n = _norm(said), _norm(name)
    cands = [_sim(s, n), 0.95 if s and s in n else 0.0]
    ds, dn = _digits(said), _digits(name)
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
    'stand by me' alone still matches the title. Spoken numbers also get a
    digit-collapsed reading ('nine oh two one oh ...' -> '90210 ...') so
    Spotify's own search sees the real title."""
    yield query
    parts = query.split(" by ")
    for i in range(1, len(parts)):
        title, artist = " by ".join(parts[:i]), " by ".join(parts[i:])
        yield f"track:{title} artist:{artist}"
    dq = _spoken_digits(query)
    if dq != query:
        yield from query_variants(dq)


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
        """Titles the user actually plays (top tracks first - the ones voice
        commands ask for most) plus the 50 most recent likes. Hotword fodder,
        so Whisper can spell titles like 'Shhh!' and 'Woa' at all."""
        titles = getattr(self, "_titles_cache", None)
        if titles is not None:
            return titles
        names = {}  # insertion-ordered set
        try:
            for t in self.sp.current_user_top_tracks(limit=50, time_range="medium_term")["items"]:
                names[t["name"]] = None
        except Exception:
            pass
        try:
            for it in self.sp.current_user_saved_tracks(limit=50)["items"]:
                names[it["track"]["name"]] = None
        except Exception:
            pass
        self._titles_cache = list(names)
        return self._titles_cache

    def _prep(self):
        """ONE round trip that both snapshots what's playing (for put_back)
        and resolves the target device - run in parallel with the search so
        neither adds to command latency."""
        pb = None
        try:
            pb = self.sp.current_playback()
        except Exception:
            pass
        if pb and pb.get("item"):
            self._undo = {
                "context": (pb.get("context") or {}).get("uri"),
                "track": pb["item"]["uri"],
                "name": pb["item"]["name"],
                "pos": pb.get("progress_ms") or 0,
            }
        if pb and pb.get("device"):
            dev = pb["device"]["id"]
        else:
            try:
                devices = self.sp.devices()["devices"]
            except Exception:
                devices = []
            if not devices:
                return None
            dev = devices[0]["id"]
            self.sp.transfer_playback(dev, force_play=False)
        self._dev_cache = (time.monotonic(), dev)
        return dev

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
        drown the mic otherwise). Owns its own safety timer: cancel-and-replace
        so a stale timer from a previous wake can never restore volume while
        the current command is still being spoken."""
        t = getattr(self, "_duck_timer", None)
        if t:
            t.cancel()
        try:
            pb = self.sp.current_playback()
            vol = pb["device"]["volume_percent"] if pb and pb.get("device") else None
            if vol is not None and vol >= 30 and pb["device"]["id"]:
                self._ducked = (vol, pb["device"]["id"])
                self.sp.volume(15, device_id=pb["device"]["id"])
                timer = threading.Timer(15, self.unduck)
                timer.daemon = True
                self._duck_timer = timer
                timer.start()
        except Exception:
            pass

    def unduck(self):
        t = getattr(self, "_duck_timer", None)
        if t:
            t.cancel()
            self._duck_timer = None
        ducked = getattr(self, "_ducked", None)
        self._ducked = None
        if ducked:
            try:
                self.sp.volume(ducked[0], device_id=ducked[1])
            except Exception:
                pass

    def commit_volume(self):
        """After an explicit volume command: forget the pre-duck level so
        nothing restores over the user's choice."""
        t = getattr(self, "_duck_timer", None)
        if t:
            t.cancel()
            self._duck_timer = None
        self._ducked = None

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
        cands = {}  # uri -> (track, best rank across variants' result lists)
        for f in futures:
            for i, t in enumerate(f.result()["tracks"]["items"]):
                old = cands.get(t["uri"])
                if old is None or i < old[1]:
                    cands[t["uri"]] = (t, i)
        for f in top_fs:
            for t in f.result():
                cands.setdefault(t["uri"], (t, 10))  # rescue pool: no rank bonus
        if not cands:
            return None
        fav = {_norm(n) for n in self.user_artists()}

        def key(cand):
            t, rank = cand
            s = max(score(q, t["name"], [a["name"] for a in t["artists"]],
                          t.get("popularity", 0)) for q in queries)
            if any(_norm(a["name"]) in fav for a in t["artists"]):
                s += 0.12  # artists you listen to beat sound-alike strangers
            # search results no longer carry popularity; Spotify's own result
            # order is the popularity signal - a small nudge that breaks
            # near-ties toward the version everyone means
            return s + max(0, 5 - rank) * 0.01

        return max(cands.values(), key=key)[0]

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
        prep = _POOL.submit(self._prep)
        item = self._search(name, kind)
        dev = prep.result()
        if not dev:
            return NO_DEVICE
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
        prep = _POOL.submit(self._prep)
        t = self._best_track(query)
        dev = prep.result()
        if not dev:
            return NO_DEVICE
        if not t:
            return f"No results for '{query.replace('|', ' / ')}'"
        self.sp.start_playback(device_id=dev, uris=[t["uri"]])
        return f"Playing {t['name']} by {t['artists'][0]['name']}"

    def play_artist(self, name):
        return self._play_context(name, "artist")

    def play_album(self, name):
        return self._play_context(name, "album")

    def queue_track(self, query):
        dev_f = _POOL.submit(self._device)  # no snapshot: the queue isn't a context change
        t = self._best_track(query)
        dev = dev_f.result()
        if not dev:
            return NO_DEVICE
        if not t:
            return f"No results for '{query.replace('|', ' / ')}'"
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
        names = _queries(name)
        if any(_sim(_norm(n), "liked songs") >= 0.7 for n in names):
            return self.play_liked()
        prep = _POOL.submit(self._prep)

        def best_score(p):
            return max(_pl_score(n, p["name"]) for n in names)

        mine = self._my_playlists()
        dev = prep.result()
        if not dev:
            return NO_DEVICE
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
        prep = _POOL.submit(self._prep)
        items = self.sp.current_user_saved_tracks(limit=50)["items"]
        dev = prep.result()
        if not dev:
            return NO_DEVICE
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
        try:
            self.sp.start_playback(device_id=dev)
        except spotipy.SpotifyException as e:
            if "Restriction violated" in (getattr(e, "msg", None) or str(e)):
                return "Already playing"  # 'play' while playing is not an error
            raise
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
        try:
            self.sp.previous_track(device_id=dev)
        except spotipy.SpotifyException as e:
            if "Restriction violated" in (getattr(e, "msg", None) or str(e)):
                self.sp.seek_track(0, device_id=dev)  # first track: restart it
                return "Restarted track"
            raise
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
