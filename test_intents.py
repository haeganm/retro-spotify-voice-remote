"""Smallest check that fails if the intent parser breaks: python test_intents.py"""
from retro.voice import parse, strip_wake, words_to_int

CASES = {
    "play bohemian rhapsody": ("play_track", "bohemian rhapsody"),
    "play bohemian rhapsody by queen": ("play_track", "bohemian rhapsody queen"),
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
assert strip_wake("hey retro play thriller", "hey retro") == "play thriller"
assert strip_wake("retro pause", "hey retro") == "pause"
assert strip_wake("hey retro", "hey retro") == ""
assert strip_wake("play thriller", "hey retro") is None

print(f"OK - {len(CASES) + 6} checks passed")
