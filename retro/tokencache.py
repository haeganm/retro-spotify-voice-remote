"""OAuth token cache encrypted at rest (Windows DPAPI, user-scoped).

The refresh token is the only secret this app holds; spotipy's default cache
writes it as plaintext JSON. This handler wraps it with CryptProtectData so
the file is unreadable to other Windows accounts and offline attackers -
via ctypes, no extra dependency. Legacy plaintext caches are read once and
re-saved encrypted. On any DPAPI failure (or off Windows) it degrades to
spotipy's plaintext behavior rather than breaking auth."""
import json
import sys

from spotipy.cache_handler import CacheHandler

_MAGIC = b"SRDP1"  # file prefix marking our encrypted format


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                               ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    fn = (ctypes.windll.crypt32.CryptProtectData if protect
          else ctypes.windll.crypt32.CryptUnprotectData)
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


class EncryptedCacheHandler(CacheHandler):
    def __init__(self, cache_path):
        self.cache_path = str(cache_path)

    def get_cached_token(self):
        try:
            with open(self.cache_path, "rb") as f:
                raw = f.read()
        except OSError:
            return None
        try:
            if raw.startswith(_MAGIC) and sys.platform == "win32":
                return json.loads(_dpapi(raw[len(_MAGIC):], protect=False))
            return json.loads(raw)  # legacy plaintext: re-encrypted on next save
        except Exception:
            return None  # corrupt cache -> re-auth, don't crash

    def save_token_to_cache(self, token_info):
        data = json.dumps(token_info).encode()
        if sys.platform == "win32":
            try:
                data = _MAGIC + _dpapi(data, protect=True)
            except Exception:
                pass  # plaintext fallback beats losing the session
        try:
            with open(self.cache_path, "wb") as f:
                f.write(data)
        except OSError:
            pass
