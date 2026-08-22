#!/usr/bin/env python3
"""Portal Screenshot helper — runs under the SYSTEM python3 (needs gi + dbus).

Invoked by capture.py as a subprocess because the agent venv lacks gi/dbus.
Prints one JSON object on stdout:
  {"ok": true,  "path": "...", "screensaver_active": false}
  {"ok": false, "error": "..."}

The portal drops its own copy under ~/Pictures/; this helper copies the bytes
to the requested output path and DELETES the portal's original so repeated
captures do not litter the user's Pictures directory (a real run once left 6).
"""
import json
import os
import sys


def screensaver_active(bus):
    """ScreenSaver.GetActive — the probe that predicts a blank frame.

    MEASURED 2026-08-21: LockedHint=no while GetActive=true and the portal
    returned a blank frame. LockedHint is NOT a sufficient probe.
    """
    try:
        import dbus
        obj = bus.get_object("org.freedesktop.ScreenSaver", "/ScreenSaver")
        return bool(dbus.Interface(obj, "org.freedesktop.ScreenSaver").GetActive())
    except Exception:
        return None  # unknown, not false


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mercury_eye.png"

    from gi.repository import GLib
    import dbus
    import dbus.mainloop.glib

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    saver = screensaver_active(bus)

    obj = bus.get_object("org.freedesktop.portal.Desktop",
                         "/org/freedesktop/portal/desktop")
    shot = dbus.Interface(obj, "org.freedesktop.portal.Screenshot")
    sender = bus.get_unique_name()[1:].replace(".", "_")
    token = f"mercury_eye_{os.getpid()}"
    path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    loop = GLib.MainLoop()
    result = {}

    def on_response(code, results):
        result["code"] = int(code)
        result["results"] = dict(results)
        loop.quit()

    bus.add_signal_receiver(on_response,
                            signal_name="Response",
                            dbus_interface="org.freedesktop.portal.Request",
                            path=path)
    shot.Screenshot("", {"handle_token": token,
                         "interactive": dbus.Boolean(False),
                         "modal": dbus.Boolean(False)})

    def bail():
        result.setdefault("code", -1)
        loop.quit()
        return False

    GLib.timeout_add_seconds(30, bail)
    loop.run()

    code = result.get("code", -1)
    uri = str(result.get("results", {}).get("uri", ""))
    if code != 0 or not uri:
        print(json.dumps({"ok": False,
                          "error": f"portal response code {code}, uri={bool(uri)}"}))
        return 1

    src = uri.replace("file://", "")
    try:
        with open(src, "rb") as f:
            data = f.read()
        with open(out, "wb") as f:
            f.write(data)
    finally:
        try:
            os.unlink(src)
        except OSError:
            pass

    print(json.dumps({"ok": True, "path": out, "screensaver_active": saver}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
