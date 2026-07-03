"""Default-output switching (Windows only, best-effort).

Bluetooth headsets can't run hi-fi audio and their mic at once: engaging the
mic kills the hi-fi (A2DP) output and only the phone-quality 'Headset' output
stays alive - but apps don't migrate to it on their own, so the system goes
silent. While a Bluetooth mic is selected we point the system default output
at that headset endpoint (audio continues, degraded), and restore the previous
default when the mic is released.

Uses the undocumented-but-stable IPolicyConfig COM interface (same as
SoundSwitch and friends). Everything is wrapped: on any failure we just don't
switch, which reproduces the old behavior."""
import sys

_prev_default = None


def _policy_config():
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT

    class IPolicyConfig(comtypes.IUnknown):
        _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
        _methods_ = (
            COMMETHOD([], HRESULT, "GetMixFormat",
                      (["in"], comtypes.c_wchar_p), (["out"], comtypes.POINTER(comtypes.c_void_p))),
            COMMETHOD([], HRESULT, "GetDeviceFormat",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_int),
                      (["out"], comtypes.POINTER(comtypes.c_void_p))),
            COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], comtypes.c_wchar_p)),
            COMMETHOD([], HRESULT, "SetDeviceFormat",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_void_p),
                      (["in"], comtypes.c_void_p)),
            COMMETHOD([], HRESULT, "GetProcessingPeriod",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_int),
                      (["out"], comtypes.POINTER(comtypes.c_longlong)),
                      (["out"], comtypes.POINTER(comtypes.c_longlong))),
            COMMETHOD([], HRESULT, "SetProcessingPeriod",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.POINTER(comtypes.c_longlong))),
            COMMETHOD([], HRESULT, "GetShareMode",
                      (["in"], comtypes.c_wchar_p), (["out"], comtypes.POINTER(comtypes.c_void_p))),
            COMMETHOD([], HRESULT, "SetShareMode",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_void_p)),
            COMMETHOD([], HRESULT, "GetPropertyValue",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_int),
                      (["in"], comtypes.c_void_p), (["out"], comtypes.POINTER(comtypes.c_void_p))),
            COMMETHOD([], HRESULT, "SetPropertyValue",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_int),
                      (["in"], comtypes.c_void_p), (["in"], comtypes.c_void_p)),
            COMMETHOD([], HRESULT, "SetDefaultEndpoint",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_int)),
            COMMETHOD([], HRESULT, "SetEndpointVisibility",
                      (["in"], comtypes.c_wchar_p), (["in"], comtypes.c_int)),
        )

    clsid = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")
    return comtypes.CoCreateInstance(clsid, IPolicyConfig, comtypes.CLSCTX_ALL)


def _render_endpoints():
    """[(id, friendly_name, state)] of playback endpoints, including inactive
    ones - a Bluetooth 'Headset' output shows as unplugged until the profile
    switches, and that's exactly the one we need to target."""
    from pycaw.pycaw import AudioUtilities
    out = []
    for dev in AudioUtilities.GetAllDevices():
        try:
            if dev.id.startswith("{0.0.0.00000000}"):
                out.append((dev.id, dev.FriendlyName or "", int(dev.state.value)))
        except Exception:
            continue
    return out


def _default_render_id():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().id


def _set_default(dev_id):
    pc = _policy_config()
    for role in (0, 1, 2):  # console, multimedia, communications
        pc.SetDefaultEndpoint(dev_id, role)


def route_output_to_headset(family, wait_s=8, marker=None):
    """Point the default output at `family`'s phone-quality 'Headset' render
    endpoint so audio keeps playing while its mic is engaged. The endpoint
    only materializes once the mic opens, so poll briefly. Returns the
    endpoint name, or None if not applicable/failed."""
    global _prev_default
    if sys.platform != "win32":
        return None
    import time
    deadline = time.monotonic() + wait_s
    try:
        while time.monotonic() < deadline:
            for dev_id, name, state in _render_endpoints():
                if (family.lower() in name.lower() and "headset" in name.lower()
                        and state in (1, 8)):  # active or unplugged-but-present
                    current = _default_render_id()
                    if current != dev_id:
                        _prev_default = _prev_default or current
                        if marker is not None:  # survives a crash: see restore_output
                            try:
                                marker.write_text(_prev_default, encoding="utf-8")
                            except Exception:
                                pass
                        _set_default(dev_id)
                    return name
            time.sleep(1)
    except Exception:
        pass
    return None


def restore_output(marker=None):
    """Put the default output back to what it was before we touched it. The
    marker file covers the crash case - if the app died while routed, the next
    start finds the marker and undoes the switch."""
    global _prev_default
    prev = _prev_default
    if prev is None and marker is not None:
        try:
            prev = marker.read_text(encoding="utf-8").strip() or None
        except Exception:
            prev = None
    if prev is not None:
        try:
            _set_default(prev)
        except Exception:
            pass
    _prev_default = None
    if marker is not None:
        try:
            marker.unlink(missing_ok=True)
        except Exception:
            pass
