"""Offline speech: mic -> Vosk -> intents. Nothing here touches the network."""
import difflib
import json
import queue
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

# One decode at a time on its own thread: the log showed CUDA occasionally
# stalling 15-25s, which must never block the mic loop.
_DECODER = ThreadPoolExecutor(max_workers=1)
WHISPER_TIMEOUT = 2.5  # seconds before we give up and use Vosk's text

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
    _i(r"(?:resumed?|continue|keep playing|play)(?: (?:the )?(?:music|song))?", "resume"),
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
    _i(r"(?:play )?(?:the |my )?playlist (?:called |named )?(.+)", "play_playlist", lambda m: m.group(1)),
    _i(r"play (?:the |my )?(.+) playlist", "play_playlist", lambda m: m.group(1)),
    # "liked" is heard as "like"/"light" constantly
    _i(r"play (?:my )?(?:(?:liked?|light|favou?rite|saved) (?:songs?|tracks?|music)|favou?rites?|likes)", "play_liked"),
    _i(r"put it back|go back to what was playing|play what was (?:playing |on )?before|undo(?: that)?", "put_back"),
    # "queue" decodes as its homophones constantly ("you"/"do" are log-observed;
    # safe because intents only parse right after the wake word)
    _i(r"(?:(?:queue|que|cue|q|you|do)(?: up)?|add) (.+?)(?: to (?:the |my )?(?:queue|que|cue|q))?", "queue_track", lambda m: m.group(1)),
    _i(r"play (.+) next", "queue_track", lambda m: m.group(1)),
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


# Last-resort net for near-homophones of control words ("cause" -> pause).
# Only consulted for short unparsed text so titles are never hijacked.
_CONTROLS = {"pause": ("pause", None), "stop": ("pause", None),
             "skip": ("next_track", None), "next": ("next_track", None),
             "resume": ("resume", None), "previous": ("previous_track", None),
             "go back": ("previous_track", None), "shuffle": ("shuffle_on", None)}


def parse(text):
    """Return (action, arg) or None if the utterance isn't a known command."""
    text = text.lower().strip()
    # "play" decodes as these constantly (log-observed)
    text = re.sub(r"^(?:place|clay|played)\b", "play", text)
    intent = _match(text)
    if intent is None and defill(text) != text:
        intent = _match(defill(text))  # second chance without edge noise
    if intent is None:
        short = defill(text)
        if short and len(short.split()) <= 2:
            best_r = 0.749
            for phrase, it in _CONTROLS.items():
                r = difflib.SequenceMatcher(None, short, phrase).ratio()
                if r > best_r:
                    best_r, intent = r, it
    return intent


# Only these intents carry free text (titles/names) that Whisper hears better;
# everything else is a fixed control phrase Vosk handles faster and safer.
# The value is a canonical verb prefix used to graft Whisper's text (which
# often drops the verb) onto the intent Vosk identified.
NEEDS_WHISPER = {"play_track": "play", "queue_track": "queue",
                 "play_playlist": "play playlist", "play_album": "play the album",
                 "play_artist": "play the artist"}


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
        if len(tokens) <= i:
            break
        tok = tokens[i]
        # fuzzy match, or same middle chars ('metra'/'metro' for 'retro' -
        # STT reliably keeps the vowel-r core and botches the first consonant)
        if (difflib.SequenceMatcher(None, tok, word).ratio() >= 0.75
                or (len(tok) >= 4 and len(word) >= 4 and tok[1:4] == word[1:4])):
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
        self.log = lambda line: None  # set by the app to append to retro.log
        self.restart = None  # threading.Event set by the tray to reopen the mic
        self.on_dead_mic = lambda: None  # fired after ~20s of dead air
        self._awaiting_until = 0.0
        self._decode_busy = False

    def _wake_cut(self, words, utt_start_sample):
        """Byte offset just past the wake keyword, from Vosk word timings -
        Whisper then sees only the command audio."""
        key = self.wake.split()[-1]
        for wd in words[:3]:
            if difflib.SequenceMatcher(None, wd["word"], key).ratio() >= 0.75:
                return (int(wd["end"] * 16000) - utt_start_sample) * 2
        return 0

    def _better(self, fallback, audio, awaiting):
        """Decide which transcription to dispatch.

        Fast path: control commands (skip/pause/volume/...) have no free text;
        Vosk nails those fixed words and Whisper hallucinates on sub-second
        clips - so if Vosk's text already parses to one, use it, instantly.

        Quality path: search commands carry a title, which Vosk garbles -
        re-transcribe the audio with Whisper and prefer whichever text parses."""
        fb_intent = parse(fallback)
        if fb_intent and fb_intent[0] not in NEEDS_WHISPER:
            return fallback
        if not self.transcriber or audio is None:
            return fallback
        if self._decode_busy:
            self.log("whisper: skipped (previous decode still stuck)")
            return fallback
        self._decode_busy = True
        t0 = time.time()
        fut = _DECODER.submit(self.transcriber, audio)
        try:
            w = fut.result(timeout=WHISPER_TIMEOUT)
            self._decode_busy = False
        except FutureTimeout:
            self.log(f"whisper: TIMEOUT >{WHISPER_TIMEOUT}s, using vosk text")
            # release the flag (and swallow any exception) whenever it finishes
            fut.add_done_callback(
                lambda f: (setattr(self, "_decode_busy", False), f.exception()))
            return fallback
        except Exception:
            self._decode_busy = False
            return fallback
        if self.debug and w:
            print(f"[whisper] {w}")
        self.log(f"whisper: {w!r} ({(time.time() - t0) * 1000:.0f}ms)")
        if not w:
            return fallback
        rest = strip_wake(w, self.wake)  # audio may or may not include the wake
        cand = rest if rest else w
        if cand and parse(cand):
            return cand
        if cand and fb_intent:
            # Whisper often hears the title but drops the verb ("money twerk by
            # yeat"): keep Vosk's intent and send BOTH engines' words to the
            # search ('a|b'), letting candidate ranking pick the real track.
            # Gated on similarity so a Whisper hallucination can't join in.
            similar = difflib.SequenceMatcher(None, cand, fb_intent[1] or "").ratio() >= 0.35
            combo = f"{NEEDS_WHISPER[fb_intent[0]]} {cand}|{fb_intent[1] or ''}".rstrip("|")
            if similar and parse(combo):
                return combo
        if fb_intent:
            return fallback
        return cand or fallback

    def feed_text(self, text, now=None, audio=None):
        """Route one recognized utterance: command after wake (same breath or
        within 6s of the bare wake phrase)."""
        now = time.time() if now is None else now
        if now < self._awaiting_until:
            if not defill(text):
                return  # pure noise ("the") must not eat the command window
            rest = strip_wake(text, self.wake)
            if rest == "":  # they repeated the wake because we didn't respond
                self._awaiting_until = now + 6
                self.on_wake()
                return
            self._awaiting_until = 0.0
            if rest:  # wake + command inside the window, in one breath
                cmd = self._better(rest, audio, awaiting=False)
            else:
                cmd = self._better(text, audio, awaiting=True)
            # a window utterance IS a command; if the verb got eaten
            # ("7 0 kanye west bangers"), assume "play" - but only for
            # plausible titles, never junk like "no" or "s"
            if (parse(cmd) is None and len(cmd.split()) >= 2
                    and strip_wake(cmd, self.wake) is None and parse(f"play {cmd}")):
                cmd = f"play {cmd}"
            self.on_command(cmd)
            return
        rest = strip_wake(text, self.wake)
        if rest:
            self.on_command(self._better(rest, audio, awaiting=False))
        elif rest == "":  # wake phrase alone: next utterance is the command
            self._awaiting_until = now + 6
            self.on_wake()

    def run(self, listening, stop):
        import threading

        import numpy as np
        import sounddevice as sd
        from vosk import Model, KaldiRecognizer, SetLogLevel

        SetLogLevel(-1)
        model = Model(self.model_path)
        self.restart = self.restart or threading.Event()

        while not stop.is_set():  # outer loop: one pass per mic device
            self.restart.clear()
            rec = KaldiRecognizer(model, 16000)
            rec.SetWords(True)  # word timings, used to slice off the wake phrase
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
            fed = 0  # samples fed to this recognizer (maps word times to utt)
            warm = time.time()  # last Whisper use, for the keep-warm pulse
            stream_start = time.time()
            heard_any = False  # trap endpoints NEVER produce sound; gated
            # headset mics do the moment the user speaks - so "dead" means
            # nothing heard since the stream opened, not a quiet stretch
            try:
                stream = sd.RawInputStream(samplerate=16000, blocksize=4000,
                                           dtype="int16", channels=1, callback=cb,
                                           device=self.device)
            except Exception as e:
                if self.device is None:
                    raise
                # chosen mic vanished or changed identity: default mic > deaf app
                self.log(f"mic '{self.device}' failed ({e}); using default mic")
                self.device = None
                continue
            with stream:
                while not (stop.is_set() or self.restart.is_set()):
                    # laptop GPUs sleep when idle and take seconds (log showed
                    # 14-24s stalls) to wake a cold CUDA context: a tiny decode
                    # every minute keeps it hot (on the decoder thread, so it
                    # can never block the mic or collide with a real decode)
                    if self.transcriber and not self._decode_busy and time.time() - warm > 60:
                        warm = time.time()
                        _DECODER.submit(self.transcriber, b"\x00" * 6400) \
                            .add_done_callback(lambda f: f.exception())
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if not listening.is_set():
                        utt.clear()
                        continue  # drop audio while muted
                    if not heard_any:
                        if np.abs(np.frombuffer(data, np.int16)).mean() > 25:
                            heard_any = True
                        elif time.time() - stream_start > 20:
                            heard_any = True  # fire once, then stop judging
                            self.log("mic: no audio at all since stream open")
                            self.on_dead_mic()
                    utt.extend(data)
                    del utt[:-16000 * 2 * 20]  # cap at 20s
                    fed += len(data) // 2
                    if rec.AcceptWaveform(data):
                        hinted = False
                        res = json.loads(rec.Result())
                        text = res.get("text", "").strip()
                        audio = bytes(utt)
                        utt_start = fed - len(utt) // 2
                        utt.clear()
                        if not text:
                            continue
                        if self.debug:
                            print(f"[heard] {text}")
                        self.log(f"vosk: {text!r}")
                        cut = self._wake_cut(res.get("result", []), utt_start)
                        if 0 < cut < len(audio):
                            audio = audio[cut:]
                        self.feed_text(text, audio=audio)
                    elif not hinted:
                        partial = json.loads(rec.PartialResult()).get("partial", "")
                        if strip_wake(partial, self.wake) is not None:
                            hinted = True
                            self.on_wake_hint()
