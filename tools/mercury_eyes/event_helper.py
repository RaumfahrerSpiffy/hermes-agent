#!/usr/bin/env python3
"""AT-SPI event wait — runs under the SYSTEM python3 (needs gi).

Ported from spike 002b (validated: ~117 ms launch-to-detect on Qt).
Blocks on AT-SPI push events until one matches, then prints one JSON line:
  {"ok": true,  "ms": 117.3, "desc": "kcalc/frame 'KCalc'",
   "app": "kcalc", "role": "frame", "name": "KCalc"}
  {"ok": false} on timeout — the CALLER decides what timeout means (the
  venv side falls back to polling; Chromium emits zero events, measured).

argv: app name role timeout_s   ("" = no criterion)
"""
import json
import sys
import time
import warnings

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

warnings.simplefilter("ignore")

EVENT_TYPES = ["window:create",
               "object:state-changed:showing",
               "object:children-changed:add"]


def main():
    want_app = sys.argv[1] if len(sys.argv) > 1 else ""
    want_name = sys.argv[2] if len(sys.argv) > 2 else ""
    want_role = sys.argv[3] if len(sys.argv) > 3 else ""
    timeout = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0

    result = {}
    t0 = time.perf_counter()
    loop = GLib.MainLoop()

    def on_event(e):
        if result:
            return
        try:
            src = e.source
            nm = (src.get_name() or "").strip()
            role = src.get_role_name()
            app = src.get_application()
            appn = (app.get_name() or "?") if app else "?"
        except Exception:
            return
        if want_app and want_app.lower() not in appn.lower():
            return
        if want_role and want_role != role:
            return
        if want_name and want_name.lower() not in nm.lower():
            return
        result["ms"] = (time.perf_counter() - t0) * 1000
        result["app"], result["role"], result["name"] = appn, role, nm
        loop.quit()

    listeners = []
    for t in EVENT_TYPES:
        listener = Atspi.EventListener.new(on_event)
        listener.register(t)
        listeners.append(listener)

    GLib.timeout_add(int(timeout * 1000), lambda: (loop.quit(), False)[1])
    loop.run()

    for listener in listeners:
        try:
            listener.deregister_all()
        except Exception:
            pass

    if result:
        result["ok"] = True
        result["desc"] = (f"{result['app']}/{result['role']} "
                          f"'{result['name'][:45]}'")
        print(json.dumps(result))
        return 0
    print(json.dumps({"ok": False}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
