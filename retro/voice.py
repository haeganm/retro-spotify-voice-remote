"""Offline speech: mic -> Vosk -> intents. Nothing here touches the network."""
import difflib
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


def _vol(text):
    n = words_to_int(text)
    if n is not None and n > 100 and 0 <= n - 200 <= 100:
        return n - 200  # whisper writes "to fifty" as digits: "250"
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
    _i(r"(?:set (?:the )?)?volume(?: to| two)? (.+)", "set_volume", lambda m: _vol(m.group(1))),
    # "what's" is often heard as "was"
    _i(r"(?:what(?:'s| is|s)?|was)(?: currently)? playing|what (?:song|track) is this|what is this(?: song)?", "now_playing"),
    _i(r"(?:turn )?shuffle off", "shuffle_off"),
    _i(r"(?:turn )?shuffle(?: on)?", "shuffle_on"),
    _i(r"play (?:the )?artist (.+)", "play_artist", lambda m: m.group(1)),
    _i(r"play (?:some|songs by|music by) (.+)", "play_artist", lambda m: m.group(1)),
    _i(r"play (?:the )?album (.+)", "play_album", lambda m: m.group(1)),
    _i(r"play (?:the |my )?playlist (.+)", "play_playlist", lambda m: m.group(1)),
    _i(r"play (?:my )?(?:(?:liked?|favou?rite|saved) (?:songs?|tracks?|music)|favou?rites?|likes)", "play_liked"),
    # "play X by Y" goes to Spotify search as-is: splitting on "by" would break
    # titles like "stand by me"; player retries without "by" if search misses.
    _i(r"play (.+)", "play_track", lambda m: m.group(1)),
]


# Noise/breath decodes as short junk words at utterance edges ("the skip the").
_FILLERS = {"the", "a", "uh", "um", "and", "huh", "hey"}


def defill(text):
    """Strip filler tokens from the edges only - never from inside a title."""
    words = text.split()
    while words and words[0] in _FILLERS:
        words.pop(0)
    while words and words[-1] in _FILLERS:
        words.pop()
    return " ".join(words)


def _match(text):
    for pat, action, argf in INTENTS:
        m = pat.fullmatch(text)
        if m:
            arg = argf(m)
            if action == "set_volume" and arg is None:
                continue  # "volume banana" -> keep trying other patterns
            return (action, arg)
    return None


def parse(text):
    """Return (action, arg) or None if the utterance isn't a known command."""
    text = text.lower().strip()
    text = re.sub(r"^place\b", "play", text)  # "play s..." often decodes as "place s..."
    intent = _match(text)
    if intent is None and defill(text) != text:
        intent = _match(defill(text))  # second chance without edge noise
    return intent


def strip_wake(text, wake):
    """If the utterance starts with the wake phrase, return what follows it
    ('' if nothing); None when it doesn't. Tolerant of how STT actually mangles
    wake words: 'hey retro' decodes as 'a retro', 'the retro', or drops the
    'hey' entirely, so the key wake word may sit at token 0 or 1 and only
    needs to be a close fuzzy match ('metro' counts, 'random' doesn't)."""
    if wake in text:
        return text.split(wake, 1)[1].strip()
    word = wake.split()[-1]
    tokens = text.split()
    for i in (0, 1):
        if len(tokens) > i and difflib.SequenceMatcher(None, tokens[i], word).ratio() >= 0.75:
            return " ".join(tokens[i + 1:])
    return None


class Listener:
    """Streams the mic into Vosk; calls on_command(text) with the utterance
    following the wake phrase (same breath or within the next 6 seconds)."""

    def __init__(self, model_path, wake_phrase, on_command, on_wake=lambda: None,
                 on_wake_hint=lambda: None, device=None, debug=False, transcriber=None):
        self.model_path = str(model_path)
        self.wake = wake_phrase.lower()
        self.on_command = on_command
        self.on_wake = on_wake
        self.on_wake_hint = on_wake_hint  # fires the instant the wake phrase shows in a partial
        self.device = device  # sounddevice index/name substring, None = default
        self.debug = debug
        self.transcriber = transcriber  # optional fn(pcm16) -> text (see stt.py)
        self.restart = None  # threading.Event set by the tray to reopen the mic
        self._awaiting_until = 0.0

    def _better(self, fallback, audio, awaiting):
        """Re-transcribe the utterance with Whisper when available. Whisper wins
        on titles/artists but garbles short commands sometimes ("pause"->"poss"),
        so prefer whichever transcription actually parses as a command."""
        if not self.transcriber or audio is None:
            return fallback
        try:
            w = self.transcriber(audio)
        except Exception:
            return fallback
        if self.debug and w:
            print(f"[whisper] {w}")
        if not w:
            return fallback
        cand = w if awaiting else strip_wake(w, self.wake)
        if cand and parse(cand):
            return cand
        if parse(fallback):
            return fallback
        return cand or fallback

    def feed_text(self, text, now=None, audio=None):
        """Route one recognized utterance: command after wake (same breath or
        within 6s of the bare wake phrase)."""
        now = time.time() if now is None else now
        if now < self._awaiting_until:
            if not defill(text):
                return  # pure noise ("the") must not eat the command window
            self._awaiting_until = 0.0
            self.on_command(self._better(text, audio, awaiting=True))
            return
        rest = strip_wake(text, self.wake)
        if rest:
            self.on_command(self._better(rest, audio, awaiting=False))
        elif rest == "":  # wake phrase alone: next utterance is the command
            self._awaiting_until = now + 6
            self.on_wake()

    def run(self, listening, stop):
        import threading

        import sounddevice as sd
        from vosk import Model, KaldiRecognizer, SetLogLevel

        SetLogLevel(-1)
        model = Model(self.model_path)
        self.restart = self.restart or threading.Event()

        while not stop.is_set():  # outer loop: one pass per mic device
            self.restart.clear()
            rec = KaldiRecognizer(model, 16000)
            try:
                # trailing-silence window before an utterance finalizes:
                # (max unfinished, silence after speech, max utterance length)
                # 0.5s: snappy but doesn't split words on natural pauses
                rec.SetEndpointerDelays(1.6, 0.5, 12.0)
            except AttributeError:
                pass  # older vosk: default endpointing
            q = queue.Queue()

            def cb(indata, frames, t, status):
                q.put(bytes(indata))

            hinted = False  # one wake hint per utterance
            utt = bytearray()  # raw audio of the current utterance, for Whisper
            with sd.RawInputStream(samplerate=16000, blocksize=4000,
                                   dtype="int16", channels=1, callback=cb,
                                   device=self.device):
                while not (stop.is_set() or self.restart.is_set()):
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if not listening.is_set():
                        utt.clear()
                        continue  # drop audio while muted
                    utt.extend(data)
                    del utt[:-16000 * 2 * 20]  # cap at 20s
                    if rec.AcceptWaveform(data):
                        hinted = False
                        text = json.loads(rec.Result()).get("text", "").strip()
                        audio = bytes(utt)
                        utt.clear()
                        if self.debug and text:
                            print(f"[heard] {text}")
                        if text:
                            self.feed_text(text, audio=audio)
                    elif not hinted:
                        partial = json.loads(rec.PartialResult()).get("partial", "")
                        if strip_wake(partial, self.wake) is not None:
                            hinted = True
                            self.on_wake_hint()
