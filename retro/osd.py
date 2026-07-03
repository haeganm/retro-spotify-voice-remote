"""Instant on-screen feedback. Windows toast balloons queue up and animate in
late; a tiny always-on-top tkinter overlay appears in milliseconds. One worker
thread owns the Tk loop; show() just enqueues."""
import queue
import threading

HIDE_MS = 2500


def make_osd():
    """Return show(text) or None if tkinter isn't available."""
    try:
        import tkinter as tk
    except ImportError:
        return None
    q = queue.Queue()

    def worker():
        try:
            root = tk.Tk()
        except Exception:  # no display
            return
        root.withdraw()
        root.overrideredirect(True)  # no title bar
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        label = tk.Label(root, text="", font=("Segoe UI", 11),
                         fg="#1ed760", bg="#121212", padx=16, pady=9)
        label.pack()
        hide_job = [None]

        def poll():
            try:
                msg = q.get_nowait()
            except queue.Empty:
                root.after(80, poll)
                return
            while not q.empty():  # newest message wins
                msg = q.get_nowait()
            label.config(text=msg)
            root.update_idletasks()
            w, h = label.winfo_reqwidth(), label.winfo_reqheight()
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 90}")
            root.deiconify()
            if hide_job[0]:
                root.after_cancel(hide_job[0])
            hide_job[0] = root.after(HIDE_MS, root.withdraw)
            root.after(80, poll)

        root.after(80, poll)
        root.mainloop()

    threading.Thread(target=worker, daemon=True).start()
    return q.put
