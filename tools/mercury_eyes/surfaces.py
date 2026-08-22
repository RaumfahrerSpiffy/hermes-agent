"""Surface inventory + geometry diff — the unnamed-dialog catcher.

Failure #3 (MEASURED 2026-08-21): the Steam redeem wizard opened as an
UNNAMED frame; a named-node diff missed it for 30 minutes. A human assistant
sees "a box appeared in the middle of the screen" regardless of whether it
has a name. This module keys surfaces on (app, geometry), so a new rectangle
IS a new surface, named or not.

Enumeration runs under the SYSTEM python3 via surfaces_helper.py — the agent
venv has no gi (MEASURED 2026-08-22: an in-process `import gi` raised
ModuleNotFoundError and broke both the `surfaces` and `wait` verbs). The diff
is pure and needs no desktop at all.

Whether enumeration works while the screen is LOCKED is UNMEASURED. An
earlier version of this docstring asserted it did; that claim was never
tested and has been withdrawn.
"""
import json
import os
import subprocess
import time

from . import geometry

_MIN_DIM = 3  # Steam emits 3x1 stub frames; <=2px in either axis is noise
_HELPER = os.path.join(os.path.dirname(__file__), "surfaces_helper.py")
_SYSTEM_PYTHON = "/usr/bin/python3"  # has gi; the agent venv does not


def _enumerate_frames():
    """Every AT-SPI toplevel as {app, name, role, geom, kids}.

    Returns (frames, error). error is None on success; on failure frames is
    empty AND error is set, so a broken bus never looks like an empty desktop.
    """
    try:
        r = subprocess.run([_SYSTEM_PYTHON, _HELPER],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"

    line = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return [], (f"surfaces helper spoke no JSON (exit {r.returncode}): "
                    f"{r.stderr.strip()[:200]}")
    if not payload.get("ok"):
        return [], str(payload.get("error") or "helper reported failure")
    return payload.get("frames", []), None


def surfaces():
    """Current surface inventory as an OBSERVATION.

    Returns {"frames": [...], "probed_at": epoch, "origin_space": ...,
             "ok": bool, "error": str|None}.

    Geometry is in the space geometry.probe()["origin_space"] names —
    measured LOGICAL on this host, re-decided per machine.

    Freshness contract (failure #6): probed_at rides every observation so a
    stale inventory can never be silently passed off as current.

    Honesty contract: a helper failure returns ok=False with the reason. An
    empty desktop and an unreachable bus must never look identical.
    """
    raw, error = _enumerate_frames()
    frames = [f for f in raw
              if f["geom"][2] >= _MIN_DIM and f["geom"][3] >= _MIN_DIM]
    return {
        "frames": frames,
        "ok": error is None,
        "error": error,
        "probed_at": time.time(),
        "origin_space": geometry.probe()["origin_space"],
    }


def _key(fr):
    return (fr["app"], tuple(fr["geom"]))


def diff_surfaces(before, after):
    """{"new": [...], "gone": [...]} keyed on (app, geometry).

    A moved window reads as one new + one gone — documented, not a bug;
    callers correlate by app when they care. Accepts raw frame lists or
    surfaces() observations.
    """
    if isinstance(before, dict):
        before = before["frames"]
    if isinstance(after, dict):
        after = after["frames"]
    bk = {_key(f) for f in before}
    ak = {_key(f) for f in after}
    return {
        "new": [f for f in after if _key(f) not in bk],
        "gone": [f for f in before if _key(f) not in ak],
    }
