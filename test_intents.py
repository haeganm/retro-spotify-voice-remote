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
    "volume two": ("set_volume", 2),
    "was playing": ("now_playing", None),  # vosk hears "what's" as "was"
    "volume to 30": ("set_volume", 30),
    "set the volume to one hundred": ("set_volume", 100),
    "what's playing": ("now_playing", None),
    "what song is this": ("now_playing", None),
    "shuffle on": ("shuffle_on", None),
    "shuffle": ("shuffle_on", None),
    "turn shuffle off": ("shuffle_off", None),
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

# Listener routing: same-breath, two-step within 6s, expired window, no wake.
heard = []
lis = Listener("unused", "hey retro", on_command=heard.append, on_wake=lambda: heard.append("<wake>"))
lis.feed_text("hey retro play thriller", now=100)
lis.feed_text("hey retro", now=110)          # bare wake -> awaiting
lis.feed_text("pause", now=112)              # within window -> command
lis.feed_text("hey retro", now=200)
lis.feed_text("next song", now=210)          # window expired -> ignored
lis.feed_text("random chatter", now=300)     # no wake -> ignored
assert heard == ["play thriller", "<wake>", "pause", "<wake>"], heard

# Player logic with a stubbed Spotify client (no network).
from retro.player import NO_DEVICE, Player


class FakeSp:
    def __init__(self, tracks=(), device=True):
        self.tracks = dict(tracks)  # query -> track name
        self.device = device
        self.played = None

    def current_playback(self):
        return {"device": {"id": "d1", "volume_percent": 50}} if self.device else None

    def devices(self):
        return {"devices": []}

    def search(self, q, type, limit):
        hit = self.tracks.get(q)
        items = [{"uri": "uri:" + hit, "name": hit, "artists": [{"name": "x"}]}] if hit else []
        return {type + "s": {"items": items}}

    def start_playback(self, device_id=None, uris=None, context_uri=None):
        self.played = uris[0] if uris else context_uri

    def volume(self, n, device_id=None):
        self.vol = n


def fake_player(**kw):
    p = Player.__new__(Player)  # skip OAuth
    p.sp = FakeSp(**kw)
    return p


p = fake_player(tracks={"stand by me": "Stand by Me"})
assert p.handle("play_track", "stand by me") == "Playing Stand by Me by x"

# raw query misses, fallback without "by" hits
p = fake_player(tracks={"thriller michael jackson": "Thriller"})
assert p.handle("play_track", "thriller by michael jackson") == "Playing Thriller by x"
assert p.sp.played == "uri:Thriller"

assert fake_player().handle("play_track", "anything") == "No results for 'anything'"
assert fake_player(device=False).handle("play_track", "x") == NO_DEVICE
assert fake_player().handle("set_volume", 150) == "Volume 100"
assert "Error" in fake_player().handle("pause")  # FakeSp lacks pause_playback -> caught, not raised

print(f"OK - {len(CASES)} intent cases + helper checks passed")
