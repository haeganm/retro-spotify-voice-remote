"""Smallest check that fails if the intent parser breaks: python test_intents.py"""
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
    # noise decodes as filler words at the edges - stripped on second pass
    "the skip the": ("next_track", None),
    "uh play the less i know the better": ("play_track", "the less i know the better"),
    "the the pause": ("pause", None),
}

for text, want in CASES.items():
    got = parse(text)
    assert got == want, f"{text!r}: got {got}, want {want}"

assert parse("banana hammock") is None
assert words_to_int("seventeen") == 17
assert words_to_int("") is None
assert strip_wake("hey retro play thriller", "hey retro") == "play thriller"
assert strip_wake("retro pause", "hey retro") == "pause"
assert strip_wake("hey retro", "hey retro") == ""
assert strip_wake("play thriller", "hey retro") is None
assert strip_wake("a retro pause", "hey retro") == "pause"       # STT mangles "hey"
assert strip_wake("hey metro next song", "hey retro") == "next song"  # fuzzy wake word
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
assert heard == ["play thriller", "<wake>", "pause", "<wake>", "<wake>", "skip"], heard

# Whisper re-transcription: replaces Vosk's garble when audio is available,
# falls back to Vosk text when whisper fails or drops the wake phrase.
heard2 = []
lis2 = Listener("unused", "hey retro", on_command=heard2.append)
lis2.transcriber = lambda a: "hey retro play brain stew by green day"
lis2.feed_text("hey retro play brainstew", now=1, audio=b"pcm")
lis2.transcriber = lambda a: (_ for _ in ()).throw(RuntimeError)  # whisper crash
lis2.feed_text("hey retro pause", now=2, audio=b"pcm")
lis2.transcriber = lambda a: "unrelated mumble"  # no wake in whisper text
lis2.feed_text("hey retro next song", now=3, audio=b"pcm")
lis2.transcriber = lambda a: "skip"  # window utterance: no wake expected
lis2.feed_text("hey retro", now=4)
lis2.feed_text("the skip", now=5, audio=b"pcm")
assert heard2 == ["play brain stew by green day", "pause", "next song", "skip"], heard2

# Search scoring: exact-but-obscure must beat popular-but-partial.
from retro.player import NO_DEVICE, Player, query_variants, score

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

# Player logic with a stubbed Spotify client (no network).


class FakeSp:
    def __init__(self, tracks=(), device=True, playlists=(), liked=()):
        self.tracks = dict(tracks)  # query -> [(name, artist, popularity)]
        self.device = device
        self.playlists = list(playlists)
        self.liked = list(liked)
        self.played = None

    def current_playback(self):
        return {"device": {"id": "d1", "volume_percent": 50}} if self.device else None

    def devices(self):
        return {"devices": []}

    def search(self, q, type, limit):
        items = [{"uri": "uri:" + n, "name": n, "popularity": pop,
                  "artists": [{"name": a}]} for n, a, pop in self.tracks.get(q, [])]
        return {type + "s": {"items": items}}

    def current_user_playlists(self, limit):
        return {"items": [{"uri": "uri:pl:" + n, "name": n} for n in self.playlists]}

    def current_user_saved_tracks(self, limit):
        return {"items": [{"track": {"uri": "uri:" + n}} for n in self.liked]}

    def start_playback(self, device_id=None, uris=None, context_uri=None):
        self.played = uris if uris else context_uri

    def volume(self, n, device_id=None):
        self.vol = n


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

# garbled title + clear artist: rescued from artist-filtered search
p = fake_player(tracks={"artist:green day": [
    ("Brain Stew", "Green Day", 70), ("Basket Case", "Green Day", 80)]})
assert p.handle("play_track", "brain stu by green day") == "Playing Brain Stew by Green Day"

# own playlists beat public search; fuzzy match on name
p = fake_player(playlists=["Gym Pump", "chill vibes"])
assert p.handle("play_playlist", "gym pump") == "Playing playlist Gym Pump"
assert p.sp.played == "uri:pl:Gym Pump"

p = fake_player(liked=["a", "b", "c"])
assert p.handle("play_liked") == "Playing your liked songs"
assert sorted(p.sp.played) == ["uri:a", "uri:b", "uri:c"]

assert fake_player().handle("play_track", "anything") == "No results for 'anything'"
assert fake_player(device=False).handle("play_track", "x") == NO_DEVICE
assert fake_player().handle("set_volume", 150) == "Volume 100"
assert "Error" in fake_player().handle("pause")  # FakeSp lacks pause_playback -> caught, not raised

print(f"OK - {len(CASES)} intent cases + helper checks passed")
