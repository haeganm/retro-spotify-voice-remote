"""Offline speech: mic -> Vosk -> intents. Nothing here touches the network."""
import json
import queue
import re
import time

# Vosk emits numbers as words ("forty five"), so a tiny words->int map.
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}


def words_to_int(text):
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in ("one hundred", "a hundred", "hundred"):
        return 100
    n = 0
    for w in text.split():
        if w in _TENS:
            n += _TENS[w]
        elif w in _UNITS:
            n += _UNITS[w]
        else:
            return None
    return n


def _i(pattern, action, arg=lambda m: None):
    return (re.compile(pattern), action, arg)


# Order matters: exact phrases before greedy "play (.+)".
INTENTS = [
    _i(r"(?:pause|stop)(?: (?:the )?(?:music|song|playback|playing))?", "pause"),
    _i(r"(?:resume|continue|keep playing|play)(?: (?:the )?(?:music|song))?", "resume"),
    _i(r"(?:next|skip)(?: (?:this |the )?(?:song|track))?|skip it", "next_track"),
    _i(r"(?:previous|go back|back|last)(?: (?:song|track))?", "previous_track"),
    _i(r"(?:turn (?:the )?)?volume up|turn it up|louder", "volume_up"),
    _i(r"(?:turn (?:the )?)?volume down|turn it down|quieter|softer", "volume_down"),
    # "to" is often transcribed as its homophone "two" ("set volume two fifty"),
    # so both are treated as the preposition; backtracking still allows "volume two" -> 2.
    _i(r"(?:set (?:the )?)?volume(?: to| two)? (.+)", "set_volume", lambda m: words_to_int(m.group(1))),
    # "what's" is often heard as "was"
    _i(r"(?:what(?:'s| is|s)?|was)(?: currently)? playing|what (?:song|track) is this|what is this(?: song)?", "now_playing"),
    _i(r"(?:turn )?shuffle off", "shuffle_off"),
    _i(r"(?:turn )?shuffle(?: on)?", "shuffle_on"),
    _i(r"play (?:the )?artist (.+)", "play_artist", lambda m: m.group(1)),
    _i(r"play (?:some|songs by|music by) (.+)", "play_artist", lambda m: m.group(1)),
    _i(r"play (?:the )?album (.+)", "play_album", lambda m: m.group(1)),
    _i(r"play (?:the |my )?playlist (.+)", "play_playlist", lambda m: m.group(1)),
    # "play X by Y" goes to Spotify search as-is: splitting on "by" would break
    # titles like "stand by me"; player retries without "by" if search misses.
    _i(r"play (.+)", "play_track", lambda m: m.group(1)),
]


def parse(text):
    """Return (action, arg) or None if the utterance isn't a known command."""
    text = text.lower().strip()
    for pat, action, argf in INTENTS:
        m = pat.fullmatch(text)
        if m:
            arg = argf(m)
            if action == "set_volume" and arg is None:
                continue  # "volume banana" -> keep trying other patterns
            return (action, arg)
    return None


def strip_wake(text, wake):
    """If the utterance contains the wake phrase, return what follows it ('' if
    nothing). Return None when the wake phrase isn't present. Also accepts the
    last wake word alone ('retro play x') since Vosk often drops the 'hey'."""
    if wake in text:
        return text.split(wake, 1)[1].strip()
    word = wake.split()[-1]
    if text == word or text.startswith(word + " "):
        return text[len(word):].strip()
    return None


class Listener:
    """Streams the mic into Vosk; calls on_command(text) with the utterance
    following the wake phrase (same breath or within the next 6 seconds)."""

    def __init__(self, model_path, wake_phrase, on_command, on_wake=lambda: None):
        self.model_path = str(model_path)
        self.wake = wake_phrase.lower()
        self.on_command = on_command
        self.on_wake = on_wake
        self._awaiting_until = 0.0

    def feed_text(self, text, now=None):
        """Route one recognized utterance: command after wake (same breath or
        within 6s of the bare wake phrase)."""
        now = time.time() if now is None else now
        if now < self._awaiting_until:
            self._awaiting_until = 0.0
            self.on_command(text)
            return
        rest = strip_wake(text, self.wake)
        if rest:
            self.on_command(rest)
        elif rest == "":  # wake phrase alone: next utterance is the command
            self._awaiting_until = now + 6
            self.on_wake()

    def run(self, listening, stop):
        import sounddevice as sd
        from vosk import Model, KaldiRecognizer, SetLogLevel

        SetLogLevel(-1)
        rec = KaldiRecognizer(Model(self.model_path), 16000)
        q = queue.Queue()

        def cb(indata, frames, t, status):
            q.put(bytes(indata))

        with sd.RawInputStream(samplerate=16000, blocksize=8000,
                               dtype="int16", channels=1, callback=cb):
            while not stop.is_set():
                try:
                    data = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if not listening.is_set():
                    continue  # drop audio while muted
                if not rec.AcceptWaveform(data):
                    continue
                text = json.loads(rec.Result()).get("text", "").strip()
                if text:
                    self.feed_text(text)
