#!/usr/bin/env python3
"""AT-SPI surface enumeration — runs under the SYSTEM python3 (needs gi).

Invoked by surfaces.py as a subprocess because the agent venv lacks gi.
MEASURED 2026-08-22: surfaces.py imported gi in-process and raised
ModuleNotFoundError under the venv, which broke both the `surfaces` and
`wait` verbs. pointer.py and events.py already used this subprocess pattern;
this brings surfaces into line.

Prints one JSON object on stdout:
  {"ok": true,  "frames": [{app, name, role, geom, kids}, ...]}
  {"ok": false, "error": "..."}
"""
import json
import sys


def enumerate_frames():
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    out = []
    d = Atspi.get_desktop(0)
    for i in range(d.get_child_count()):
        a = d.get_child_at_index(i)
        if a is None:
            continue
        try:
            appn = a.get_name() or "?"
            n = a.get_child_count()
        except Exception:
            continue
        for j in range(n):
            try:
                f = a.get_child_at_index(j)
                if f is None:
                    continue
                e = f.get_extents(Atspi.CoordType.SCREEN)
                out.append({
                    "app": appn,
                    "name": (f.get_name() or ""),
                    "role": f.get_role_name(),
                    "geom": [e.x, e.y, e.width, e.height],
                    "kids": f.get_child_count(),
                })
            except Exception:
                continue
    return out


def main():
    try:
        print(json.dumps({"ok": True, "frames": enumerate_frames()}))
    except Exception as exc:
        print(json.dumps({"ok": False,
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
