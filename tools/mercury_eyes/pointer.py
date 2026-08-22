"""Compositor-truth cursor position — the anti-blind-click instrument.

Failures #2/#4 (MEASURED 2026-08-21): a mis-declared uinput ABS range put
every click 0.8x off target, and without a cursor readback the miss was
invisible — which invited a fabricated explanation ("stale duplicate
button"). This module exists so a pointer move can be ASSERTED, not assumed.

The KWin script value rides a private DBus callback object (cursor_helper.py,
system python3 — gi/dbus live there, not in the agent venv). The old klipper
round-trip is retired: no clipboard clobber.

Position is reported in the compositor's coordinate space — LOGICAL on this
host per the calibrated origin_space; a reading outside the derived desktop
bounds is flagged as a coordinate-space fault, never passed through.
"""
import json
import os
import subprocess
import time

from . import geometry

_HELPER = os.path.join(os.path.dirname(__file__), "cursor_helper.py")
_SYSTEM_PYTHON = "/usr/bin/python3"  # has gi/dbus; the agent venv does not


def _query_cursor():
    """(x, y) from the compositor via the helper. Raises on failure.
    Monkeypatched in unit tests; exercised live in the acceptance run."""
    r = subprocess.run([_SYSTEM_PYTHON, _HELPER],
                       capture_output=True, text=True, timeout=15)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"cursor helper spoke no JSON (exit {r.returncode}): "
            f"{r.stderr.strip()[:200]}")
    if not payload.get("ok"):
        raise RuntimeError(f"kwin cursor query failed: {payload.get('error')}")
    return (int(payload["x"]), int(payload["y"]))


def cursor_pos():
    """Pointer position as an OBSERVATION.

    Returns {"ok", "pos", "probed_at", "reason"?}:
      ok=True   pos is the compositor's cursor position, in bounds
      ok=False  pos is None (query failed) or the raw out-of-bounds reading
                (coordinate-space fault — the 0.8x bug's signature); reason
                says which. Never silent, never a guess.
    """
    obs = {"ok": False, "pos": None, "probed_at": time.time()}
    try:
        pos = _query_cursor()
    except Exception as e:
        obs["reason"] = str(e)
        return obs

    w, h = geometry.probe()["logical"]
    obs["pos"] = pos
    if not (0 <= pos[0] < w and 0 <= pos[1] < h):
        obs["reason"] = (
            f"reading {pos} outside derived logical bounds {w}x{h} — "
            f"coordinate-space fault, refusing to treat as valid")
        return obs

    obs["ok"] = True
    return obs
