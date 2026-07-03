"""Smallest check that fails if the intent parser breaks: python test_intents.py"""
import time

import spotipy

import retro.voice as voice_mod
from retro.__main__ import mic_family
from retro.player import NO_DEVICE, Player, _digits, _norm, _pl_score, query_variants, score
from retro.stt import normalize
from retro.voice import Listener, parse, strip_wake, words_to_int

CASES = {
    "play bohemian rhapsody": ("play_track", "bohemian rhapsody"),
    "play bohemian rhapsody by queen": ("play_track", "bohemian rhapsody by queen"),
    "play stand by me": ("play_track", "stand by me"),
    "turn it up": ("volume_up", None),
    "stop playing": ("pause", None),
    "play the artist queen": ("play_artist", "queen"),
    "play songs by queen": ("play_artist", "queen"),
    "play the album abbey road": ("play_album", "abbey road"),
    "play my playlist gym": ("play_playlist", "gym"),
    "pause": ("pause", None),
    "stop the music": ("pause", None),
    "play": ("resume", None),
    "resume": ("resume", None),
    "play the music": ("resume", None),
    "next": ("next_track", None),
    "skip this song": ("next_track", None),
    "skip it": ("next_track", None),
    "previous song": ("previous_track", None),
    "go back": ("previous_track", None),
    "volume up": ("volume_up", None),
    "turn the volume down": ("volume_down", None),
    "set volume to forty five": ("set_volume", 45),
    "set volume two fifty": ("set_volume", 50),  # vosk hears "to" as "two"
    "set volume 250": ("set_volume", 50),        # whisper writes "to fifty" as digits
    "volume two": ("set_volume", 2),
    "was playing": ("now_playing", None),  # vosk hears "what's" as "was"
    "volume to 30": ("set_volume", 30),
    "set the volume to one hundred": ("set_volume", 100),
    "what's playing": ("now_playing", None),
    "what song is this": ("now_playing", None),
    "shuffle on": ("shuffle_on", None),
    "shuffle": ("shuffle_on", None),
    "turn shuffle off": ("shuffle_off", None),
    "play my liked songs": ("play_liked", None),
    "play favorites": ("play_liked", None),
    # log-derived: real mishearings from retro.log
    "resumed": ("resume", None),
    "cause": ("pause", None),
    "play my light songs": ("play_liked", None),
    "playlist light songs": ("play_playlist", "light songs"),
    "play the playlist called gym": ("play_playlist", "gym"),
    "playlist seven point o": ("play_playlist", "seven point o"),
    "put it back": ("put_back", None),
    "go back to what was playing": ("put_back", None),
    "queue mr brightside": ("queue_track", "mr brightside"),
    # log-observed mishearings of "play" and "queue"
    "clay rock star by yeat": ("play_track", "rock star by yeat"),
    "played daft punk": ("play_track", "daft punk"),
    "you call my phone by lil mosey": ("queue_track", "call my phone by lil mosey"),
    "do call my phone": ("queue_track", "call my phone"),
    "queue up bohemian rhapsody": ("queue_track", "bohemian rhapsody"),
    "add stand by me to the queue": ("queue_track", "stand by me"),
    "play thriller next": ("queue_track", "thriller"),
    "play my gym playlist": ("play_playlist", "gym"),
    # noise decodes as filler words at the edges - stripped on second pass
    "the skip the": ("next_track", None),
    "uh play the less i know the better": ("play_track", "the less i know the better"),
    "the the pause": ("pause", None),
}

for text, want in CASES.items():
    got = parse(text)
    assert got == want, f"{text!r}: got {got}, want {want}"

# mic endpoint families: several Windows endpoints per physical device
assert mic_family("Headset (AirPods #4)") == "AirPods #4"
assert mic_family("Input (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\n;(AirPods #4))") == "AirPods #4"
assert mic_family("Microphone Array 3 ()") == "Microphone Array 3"
assert mic_family("Microphone (Realtek HD Audio Mic input)") == "Realtek HD Audio Mic input"

assert parse("banana hammock") is None
assert words_to_int("seventeen") == 17
assert words_to_int("") is None
assert strip_wake("hey retro play thriller", "hey retro") == "play thriller"
assert strip_wake("retro pause", "hey retro") == "pause"
assert strip_wake("hey retro", "hey retro") == ""
assert strip_wake("play thriller", "hey retro") is None
assert strip_wake("a retro pause", "hey retro") == "pause"       # STT mangles "hey"
assert strip_wake("hey metro next song", "hey retro") == "next song"  # fuzzy wake word
assert strip_wake("metra pause", "hey retro") == "pause"         # botched first consonant
assert strip_wake("random chatter here", "hey retro") is None
assert parse("place stand by me") == ("play_track", "stand by me")  # "play s..." -> "place s..."

# Listener routing: same-breath, two-step within 6s, expired window, no wake.
heard = []
lis = Listener("unused", "hey retro", on_command=heard.append, on_wake=lambda: heard.append("<wake>"))
lis.feed_text("hey retro play thriller", now=100)
lis.feed_text("hey retro", now=110)          # bare wake -> awaiting
lis.feed_text("pause", now=112)              # within window -> command
lis.feed_text("hey retro", now=200)
lis.feed_text("next song", now=210)          # window expired -> ignored
lis.feed_text("random chatter", now=300)     # no wake -> ignored
lis.feed_text("hey retro", now=400)
lis.feed_text("the", now=401)                # noise must NOT eat the window...
lis.feed_text("skip", now=403)               # ...so the real command still lands
lis.feed_text("hey retro", now=500)
lis.feed_text("hey retro", now=502)          # repeated wake re-arms the window
lis.feed_text("pause", now=507)              # (original window would've expired at 506)
assert heard == ["play thriller", "<wake>", "pause", "<wake>", "<wake>", "skip",
                 "<wake>", "<wake>", "pause"], heard

# window junk must not be promoted to "play ..." ("no" -> NOKIA incident)
heard.clear()
lis.feed_text("hey retro", now=600)
lis.feed_text("no", now=601)                 # 1 word: dispatched raw, not "play no"
lis.feed_text("hey retro", now=610)
lis.feed_text("money to work", now=611)      # plausible: promoted to "play ..."
assert heard == ["<wake>", "no", "<wake>", "play money to work"], heard

# Whisper routing: search commands (free text) go through the transcriber;
# control commands take the Vosk fast path and must NEVER touch Whisper.
def no_whisper(a):
    raise AssertionError("control command must not call whisper")

heard2 = []
lis2 = Listener("unused", "hey retro", on_command=heard2.append)
lis2.transcriber = lambda a: "hey retro play brain stew by green day"
lis2.feed_text("hey retro play brainstew", now=1, audio=b"pcm")   # search -> whisper
lis2.transcriber = no_whisper
lis2.feed_text("hey retro pause", now=2, audio=b"pcm")            # control -> fast path
lis2.feed_text("hey retro the skip the", now=3, audio=b"pcm")     # defilled control
lis2.feed_text("hey retro", now=4)
lis2.feed_text("skip", now=5, audio=b"pcm")                       # window control
lis2.transcriber = lambda a: (_ for _ in ()).throw(RuntimeError)  # whisper crash
lis2.feed_text("hey retro play thriller", now=6, audio=b"pcm")    # -> vosk fallback
lis2.transcriber = lambda a: "unrelated mumble"  # whisper text has no wake, no parse
lis2.feed_text("hey retro play daft punk", now=7, audio=b"pcm")   # -> vosk fallback
# verb grafting (the money-twerk bug): whisper drops the verb but hears the
# title; keep Vosk's intent, send BOTH hearings to search
lis2.transcriber = lambda a: "money twerk by yeat"
lis2.feed_text("hey retro play money toward my eat", now=8, audio=b"pcm")
assert heard2 == ["play brain stew by green day", "pause", "the skip the",
                  "skip", "play thriller", "play daft punk",
                  "play money twerk by yeat|money toward my eat"], heard2

# Search scoring: exact-but-obscure must beat popular-but-partial.
tv_girl = score("cigarettes out the window", "Cigarettes out the Window", ["TV Girl"], 65)
juice = score("cigarettes out the window", "Cigarettes", ["Juice WRLD"], 95)
assert tv_girl > juice, (tv_girl, juice)
# artist in query disambiguates covers
orig = score("hurt by johnny cash", "Hurt", ["Johnny Cash"], 70)
cover = score("hurt by johnny cash", "Hurt", ["Nine Inch Nails"], 80)
assert orig > cover, (orig, cover)
# artist term weighted explicitly: same title, wrong artist, higher popularity loses
right = score("brain stew by green day", "Brain Stew", ["Green Day"], 70)
karaoke = score("brain stew by green day", "Brain Stew", ["Karaoke Legends"], 95)
assert right > karaoke, (right, karaoke)

assert list(query_variants("stand by me by ben e king")) == [
    "stand by me by ben e king",
    "track:stand artist:me by ben e king",
    "track:stand by me artist:ben e king",
]

# stylized/diacritic titles fold to ASCII instead of losing letters
assert _norm("Wôa...! Beyoncé") == "woa beyonce"
assert normalize("Wôa...!") == "woa"
# spoken numbers digitize for matching numeric titles
assert _digits("nine oh two one oh") == "90210"
assert _digits("ninety two ten") == "90210"
assert "90210 by travis scott" in list(query_variants("nine oh two one oh by travis scott"))
# ranking: run-collapse rescues onomatopoeia, digits rescue numeric titles,
# fold rescues stylized spellings - each beating a more popular wrong hit
assert score("shh by yeat", "Shhh!", ["Yeat"], 55) > score("shh by yeat", "Sh Boom", ["Stranger"], 95)
assert score("woa by yeat", "Wôa", ["Yeat"], 50) > score("woa by yeat", "Woah Woah Woah", ["Stranger"], 90)
assert score("nine oh two one oh by travis scott", "90210", ["Travis Scott"], 70) \
    > score("nine oh two one oh by travis scott", "Nine", ["Stranger"], 95)

# log-derived (07-03): the real candidate sets that picked the wrong track.
# decorated title + feat-diluted artists must not lose to a clean wrong title
assert score("90210 travis scott", "90210 (feat. Kacy Hill)", ["Travis Scott", "Kacy Hill"]) \
    > score("90210 travis scott", "SICKO MODE", ["Travis Scott"])
assert score("90210 by travis scott", "90210 (feat. Kacy Hill)", ["Travis Scott", "Kacy Hill"]) \
    > score("90210 by travis scott", "90210", ["sativa"])
# unrequested remix loses to the original; an ASKED-for remix still wins
assert score("california love tupac", "California Love - Original Version", ["2Pac", "Roger", "Dr. Dre"]) \
    > score("california love tupac", "California Love (Remix)", ["2Pac", "Roger Troutman", "Dr. Dre"])
assert score("california love remix tupac", "California Love (Remix)", ["2Pac", "Roger Troutman", "Dr. Dre"]) \
    > score("california love remix tupac", "California Love - Original Version", ["2Pac", "Roger", "Dr. Dre"])
# whisper glue: 'play9210' splits at the letter-digit seam
assert normalize("play9210 travis scott") == "play 9210 travis scott"

# Watchdog: a stuck decode must not block commands (falls back to Vosk text),
# and a busy decoder means the next command skips Whisper entirely.
voice_mod.WHISPER_TIMEOUT = 0.2
heard3 = []
lis3 = Listener("unused", "hey retro", on_command=heard3.append)
lis3.transcriber = lambda a: (time.sleep(1.5), "too late")[1]
t0 = time.time()
lis3.feed_text("hey retro play thriller", now=1, audio=b"pcm")
took = time.time() - t0
assert heard3 == ["play thriller"] and took < 1.0, (heard3, took)
lis3.feed_text("hey retro play daft punk", now=2, audio=b"pcm")  # decoder busy -> skip
assert heard3[-1] == "play daft punk"
time.sleep(1.6)  # let the stuck decode drain so the pool is clean for later tests
voice_mod.WHISPER_TIMEOUT = 2.5

# Player logic with a stubbed Spotify client (no network).


class FakeSp:
    def __init__(self, tracks=(), device=True, playlists=(), liked=(), fav=(),
                 device_list=()):
        self.tracks = dict(tracks)  # query -> [(name, artist, popularity)]
        self.device = device
        self.device_list = list(device_list)
        self.playlists = list(playlists)
        self.liked = list(liked)
        self.fav = list(fav)        # user's top artists
        self.played = None
        self.queued = None
        self.transferred = None

    def current_user_top_artists(self, limit, time_range):
        return {"items": [{"name": n} for n in self.fav]}

    def current_user_top_tracks(self, limit, time_range):
        return {"items": [{"name": n} for n in getattr(self, "top", [])]}

    def add_to_queue(self, uri, device_id=None):
        self.queued = uri

    def current_playback(self):
        if not self.device:
            return None
        return {"device": {"id": "d1", "volume_percent": 50},
                "item": {"uri": "uri:old", "name": "Old Song"},
                "context": {"uri": "ctx:oldplaylist"}, "progress_ms": 1234}

    def devices(self):
        return {"devices": self.device_list}

    def transfer_playback(self, device_id, force_play=False):
        self.transferred = device_id

    def volume(self, n, device_id=None):
        self.vol = n

    def search(self, q, type, limit):
        items = [{"uri": "uri:" + n, "name": n, "popularity": pop,
                  "artists": [{"name": a}]} for n, a, pop in self.tracks.get(q, [])]
        return {type + "s": {"items": items}}

    def current_user_playlists(self, limit):
        return {"items": [{"uri": "uri:pl:" + n, "name": n} for n in self.playlists]}

    def current_user_saved_tracks(self, limit):
        return {"items": [{"track": {"uri": "uri:" + n}} for n in self.liked]}

    def start_playback(self, device_id=None, uris=None, context_uri=None, **kw):
        self.played = uris if uris else context_uri
        self.play_kw = kw

    def previous_track(self, device_id=None):
        raise spotipy.SpotifyException(404, -1, "Player command failed: Restriction violated")

    def seek_track(self, pos, device_id=None):
        self.seeked = pos


def fake_player(**kw):
    p = Player.__new__(Player)  # skip OAuth
    p.sp = FakeSp(**kw)
    return p


p = fake_player(tracks={"stand by me": [("Stand by Me", "Ben E. King", 80)]})
assert p.handle("play_track", "stand by me") == "Playing Stand by Me by Ben E. King"

# raw query misses; the field-filtered "by" variant hits
p = fake_player(tracks={"track:thriller artist:michael jackson": [("Thriller", "Michael Jackson", 90)]})
assert p.handle("play_track", "thriller by michael jackson") == "Playing Thriller by Michael Jackson"

# candidates from all variants ranked together: exact title wins over popular partial
p = fake_player(tracks={"the less i know the better by tame impala": [
    ("The Less I Know The Better", "Tame Impala", 80),
    ("The Less", "Somebody Big", 99)]})
assert "Tame Impala" in p.handle("play_track", "the less i know the better by tame impala")

# multi-hearing search: candidates from both texts compete, best match wins
p = fake_player(tracks={"bohemian rhapsody": [("Bohemian Rhapsody", "Queen", 90)]})
assert p.handle("queue_track", "cubo hemian rhapsody|bohemian rhapsody") \
    == "Queued Bohemian Rhapsody by Queen"
p = fake_player(tracks={"money twerk by yeat": [("Money Twërk", "Yeat", 60)],
                        "money toward my eat": [("Money - Mazza Remix", "Klaas", 80)]})
assert p.handle("play_track", "money twerk by yeat|money toward my eat") \
    == "Playing Money Twërk by Yeat"

# garbled title + clear artist: rescued from artist-filtered search
p = fake_player(tracks={"artist:green day": [
    ("Brain Stew", "Green Day", 70), ("Basket Case", "Green Day", 80)]})
assert p.handle("play_track", "brain stu by green day") == "Playing Brain Stew by Green Day"

# spoken numbers reach Spotify search as digits ("90210 by travis scott" variant)
p = fake_player(tracks={"90210 by travis scott": [("90210", "Travis Scott", 80)]})
assert p.handle("play_track", "nine oh two one oh by travis scott") \
    == "Playing 90210 by Travis Scott"

# onomatopoeic title: right artist + run-collapse beats a popular exact-ish stranger
p = fake_player(fav=["Yeat"], tracks={"shh by yeat": [
    ("Shhh!", "Yeat", 40), ("Shh", "Imposter", 90)]})
assert p.handle("play_track", "shh by yeat") == "Playing Shhh! by Yeat"

# hotword vocabulary: top tracks come first, deduped against likes
p = fake_player()
p.sp.top = ["Shhh!", "Wôa"]
assert p.user_titles()[:2] == ["Shhh!", "Wôa"]

# familiar artist beats a sound-alike stranger with a more popular track
p = fake_player(fav=["Yeat"], tracks={"rockstar by yeat": [
    ("rockstar", "<3BEAT", 85), ("Rockstar", "Yeat", 55)]})
assert p.handle("play_track", "rockstar by yeat") == "Playing Rockstar by Yeat"

# queueing
p = fake_player(tracks={"thriller": [("Thriller", "Michael Jackson", 90)]})
assert p.handle("queue_track", "thriller") == "Queued Thriller by Michael Jackson"
assert p.sp.queued == "uri:Thriller"

# own playlists beat public search; decorated names match by containment
p = fake_player(playlists=["Gym Pump", "chill vibes"])
assert p.handle("play_playlist", "gym pump") == "Playing playlist Gym Pump"
assert p.sp.played == "uri:pl:Gym Pump"
p = fake_player(playlists=["\U0001F525 GYM PUMP mix 2024 \U0001F525", "chill vibes"])
assert "GYM PUMP" in p.handle("play_playlist", "gym pump")

# spoken numbers match versioned names ("seven point o" -> "7.0")
assert _pl_score("seven point o", "7.0 \U0001F3AF") >= 0.9
assert _pl_score("seven point zero", "7.0 \U0001F3AF") >= 0.9
p = fake_player(playlists=["7.0 \U0001F3AF", "boiii"])
assert p.handle("play_playlist", "seven point o") == "Playing playlist 7.0 \U0001F3AF"

# 'liked songs' asked as a playlist routes to liked songs
p = fake_player(liked=["a"])
assert p.handle("play_playlist", "light songs") == "Playing your liked songs"

# no match: error names the closest candidates
p = fake_player(playlists=["7.0 \U0001F3AF", "boiii", "haeglin"])
msg = p.handle("play_playlist", "quantum flimflam")
assert msg.startswith("No playlist like") and "Closest:" in msg, msg

p = fake_player(liked=["a", "b", "c"])
assert p.handle("play_liked") == "Playing your liked songs"
assert sorted(p.sp.played) == ["uri:a", "uri:b", "uri:c"]

# put it back: snapshot on play, restore with context + position
p = fake_player(tracks={"thriller": [("Thriller", "MJ", 90)]})
p.handle("play_track", "thriller")
assert p.handle("put_back") == "Back to Old Song"
assert p.sp.played == "ctx:oldplaylist" and p.sp.play_kw["position_ms"] == 1234
assert fake_player().handle("put_back") == "Nothing to go back to"

# duck/unduck bookkeeping: safety timer is cancel-and-replace, and an explicit
# volume command commits (nothing may restore over it)
p = fake_player()
p.duck()
assert p.sp.vol == 15 and p._duck_timer is not None
old_timer = p._duck_timer
p.duck()  # second wake: stale timer must be cancelled, not left to fire
assert old_timer.finished.is_set() or not old_timer.is_alive()
p.unduck()
assert p.sp.vol == 50 and p._duck_timer is None
p.unduck()  # idempotent
p.duck()
p.commit_volume()
assert p._ducked is None and p._duck_timer is None
p.unduck()
assert p.sp.vol == 15  # nothing restored: user's volume choice stands

# "previous" on the first track: Spotify raises Restriction violated -> restart it
p = fake_player()
assert p.handle("previous_track") == "Restarted track"
assert p.sp.seeked == 0

# "play" while already playing: Restriction violated is not an error
p = fake_player()


def _restricted(**kw):
    raise spotipy.SpotifyException(403, -1, "Player command failed: Restriction violated")


p.sp.start_playback = _restricted
assert p.handle("resume") == "Already playing"

assert fake_player().handle("play_track", "anything") == "No results for 'anything'"
assert fake_player(device=False).handle("play_track", "x") == NO_DEVICE
assert fake_player().handle("set_volume", 150) == "Volume 100"
assert "Error" in fake_player().handle("pause")  # FakeSp lacks pause_playback -> caught, not raised

print(f"OK - {len(CASES)} intent cases + helper checks passed")
