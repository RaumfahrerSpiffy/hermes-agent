#!/usr/bin/env python3
"""KWin window geometry — runs under the SYSTEM python3 (needs gi + dbus).

MEASURED 2026-08-22: on Wayland, AT-SPI reports origin (0,0) for every
application window — clients cannot see their own absolute position, only the
compositor can. A click aimed at an AT-SPI rectangle struck bare desktop and
desktop-focused typing opened KRunner. KWin's frameGeometry is the
authoritative source for where windows actually are.

Same DBus-callback pattern as cursor_helper.py: the KWin script calls
callDBus() back to a private object on our own bus connection.

Prints one JSON object on stdout:
  {"ok": true,  "windows": [{app, caption, x, y, w, h, minimized}, ...]}
  {"ok": false, "error": "..."}
"""
import json
import os
import sys
import tempfile

SERVICE = f"dev.apollolabs.MercuryEyes.Windows{os.getpid()}"
OBJ_PATH = "/WindowsQuery"
IFACE = "dev.apollolabs.MercuryEyes.WindowsQuery"
TIMEOUT_S = 8

KWIN_SCRIPT = """
var wins = workspace.windowList();
var out = [];
for (var i = 0; i < wins.length; i++) {
  var w = wins[i];
  var g = w.frameGeometry;
  out.push({
    app: String(w.resourceClass || ""),
    caption: String(w.caption || ""),
    x: g.x, y: g.y, w: g.width, h: g.height,
    minimized: !!w.minimized,
    on_all_desktops: !!w.onAllDesktops
  });
}
callDBus("%(service)s", "%(path)s", "%(iface)s", "report",
         JSON.stringify(out));
"""


def main():
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    result = {}
    loop = GLib.MainLoop()

    class WindowsSink(dbus.service.Object):
        @dbus.service.method(IFACE, in_signature="s")
        def report(self, payload):
            result["windows"] = json.loads(str(payload))
            loop.quit()

    name = dbus.service.BusName(SERVICE, bus)
    sink = WindowsSink(bus, OBJ_PATH)

    script = KWIN_SCRIPT % {"service": SERVICE, "path": OBJ_PATH,
                            "iface": IFACE}
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        kwin = bus.get_object("org.kde.KWin", "/Scripting")
        scripting = dbus.Interface(kwin, "org.kde.kwin.Scripting")
        plugin = f"mercury_windows_{os.getpid()}"
        # loadScript is OVERLOADED (s and ss); force the two-string form.
        script_id = int(scripting.loadScript(script_path, plugin,
                                             signature="ss"))
        if script_id < 0:
            print(json.dumps({"ok": False,
                              "error": "KWin refused the script (id < 0)"}))
            return 1

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

    if "windows" in result:
        print(json.dumps({"ok": True, "windows": result["windows"]}))
        return 0
    print(json.dumps({"ok": False,
                      "error": f"no callback within {TIMEOUT_S}s"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
