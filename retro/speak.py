"""Offline voice feedback (SAPI via pyttsx3). One worker thread owns the
engine; speak() enqueues and returns immediately. Newest message wins - if
you fire three commands fast, only the latest result is worth hearing."""
import queue
import threading


def make_speaker():
    """Return speak(text) or None if pyttsx3 isn't available."""
    try:
        import pyttsx3
    except ImportError:
        return None
    q = queue.Queue()

    def worker():
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 190)
        except Exception:
            return
        while True:
            text = q.get()
            while not q.empty():  # drop stale messages, say the latest
                text = q.get()
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()
    return q.put
