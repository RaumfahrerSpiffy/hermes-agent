#!/usr/bin/env python3
"""Cursor position via KWin scripting — runs under the SYSTEM python3.

The KWin script's print() never reaches the journal, so the value rides a
private DBus callback object registered on OUR OWN session-bus connection
(pattern borrowed from spike 004 / computer-use-linux): the KWin script calls
callDBus() back to us. No clipboard involved — the old klipper round-trip
clobbered the user's clipboard and needed a save/restore dance.

Prints one JSON object on stdout:
  {"ok": true,  "x": 412, "y": 388}
  {"ok": false, "error": "..."}
"""
import json
import os
import sys
import tempfile

SERVICE = f"dev.apollolabs.MercuryEyes.Cursor{os.getpid()}"
OBJ_PATH = "/CursorQuery"
IFACE = "dev.apollolabs.MercuryEyes.CursorQuery"
TIMEOUT_S = 5


def main():
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    result = {}
    loop = GLib.MainLoop()

    class CursorSink(dbus.service.Object):
        @dbus.service.method(IFACE, in_signature="ii")
        def report(self, x, y):
            result["pos"] = (int(x), int(y))
            loop.quit()

    name = dbus.service.BusName(SERVICE, bus)
    sink = CursorSink(bus, OBJ_PATH)

    script = (
        f'callDBus("{SERVICE}", "{OBJ_PATH}", "{IFACE}", "report", '
        f"workspace.cursorPos.x, workspace.cursorPos.y);\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        kwin = bus.get_object("org.kde.KWin", "/Scripting")
        scripting = dbus.Interface(kwin, "org.kde.kwin.Scripting")
        plugin = f"mercury_cursor_{os.getpid()}"
        # loadScript is OVERLOADED (s and ss); dbus-python resolves to the
        # single-arg form and dies with a signature TypeError (measured).
        # Force the two-string overload explicitly.
        script_id = int(scripting.loadScript(script_path, plugin,
                                             signature="ss"))
        if script_id < 0:
            print(json.dumps({"ok": False,
                              "error": "KWin refused the script (id < 0)"}))
            return 1

        # Plasma 6 exposes the loaded script at /Scripting/ScriptN; older
        # builds used /N. Try both, run(), and wait for the callback.
        ran = False
        for path in (f"/Scripting/Script{script_id}", f"/{script_id}"):
            try:
                sobj = bus.get_object("org.kde.KWin", path)
                dbus.Interface(sobj, "org.kde.kwin.Script").run()
                ran = True
                break
            except dbus.DBusException:
                continue
        if not ran:
            print(json.dumps({"ok": False,
                              "error": "loaded script object not found to run"}))
            return 1

        GLib.timeout_add_seconds(TIMEOUT_S, loop.quit)
        loop.run()

        try:
            scripting.unloadScript(plugin)
        except dbus.DBusException:
            pass
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    if "pos" in result:
        x, y = result["pos"]
        print(json.dumps({"ok": True, "x": x, "y": y}))
        return 0
    print(json.dumps({"ok": False,
                      "error": f"no callback within {TIMEOUT_S}s"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
