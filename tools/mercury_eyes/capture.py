"""Frame sanity: never return a blank capture as if it were real.

The spectacle blank-capture fooled a whole morning once: a dimensionally
correct PNG, entirely one colour, reported as a successful capture. A human
assistant would say "the screen is blank." These statistics say the same
thing locally, with no model call.

Thresholds MEASURED 2026-08-21 against real captures (spike 003b):
blank lock-screen frame = 1 colour / 0.0 stddev / 100% dominant / ~4 kB per
megapixel; real desktop frames = 22k+ colours. The margins are wide.
"""
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

from . import geometry

MAX_UNIQUE_COLOURS = 50      # below this the frame is near-monochrome
MIN_LUM_STD = 8.0            # below this luminance is flat
MAX_DOMINANT_FRAC = 0.92     # above this one colour owns the frame
MIN_BYTES_PER_MP = 20000     # below this the PNG compressed to nothing

SCALE_TOLERANCE = 0.02       # spike 004: per-axis scale disagreement allowed
_HELPER = os.path.join(os.path.dirname(__file__), "portal_helper.py")
_SYSTEM_PYTHON = "/usr/bin/python3"  # has gi/dbus; the agent venv does not


def frame_stats(path):
    """Cheap local statistics for a capture. No model call."""
    sz = os.path.getsize(path)
    im = Image.open(path).convert("RGB")
    a = np.asarray(im)
    h, w, _ = a.shape
    mp = (w * h) / 1e6

    small = a[::4, ::4]
    flat = small.reshape(-1, 3)
    lum = (0.2126 * small[:, :, 0] + 0.7152 * small[:, :, 1]
           + 0.0722 * small[:, :, 2])
    colours, counts = np.unique(flat, axis=0, return_counts=True)

    return {
        "path": path,
        "width": w,
        "height": h,
        "bytes": sz,
        "bytes_per_mp": int(sz / mp) if mp else 0,
        "unique_colours": len(colours),
        "lum_std": float(lum.std()),
        "dominant_frac": float(counts.max() / counts.sum()),
    }


def is_degenerate(stats):
    """(is_degenerate, reasons). Reasons are human-readable, never silent."""
    reasons = []
    if stats["unique_colours"] < MAX_UNIQUE_COLOURS:
        reasons.append(f"only {stats['unique_colours']} unique colours")
    if stats["lum_std"] < MIN_LUM_STD:
        reasons.append(f"luminance stddev {stats['lum_std']:.1f} (flat)")
    if stats["dominant_frac"] > MAX_DOMINANT_FRAC:
        reasons.append(f"{stats['dominant_frac'] * 100:.0f}% one colour")
    if stats["bytes_per_mp"] < MIN_BYTES_PER_MP:
        reasons.append(f"{stats['bytes_per_mp']} bytes/MP (too compressible)")
    return (bool(reasons), reasons)


def _portal_grab(out):
    """One portal screenshot via the system interpreter (gi/dbus live there).

    Returns {"path": ..., "screensaver_active": bool|None}. Raises on failure.
    Monkeypatched in unit tests; exercised for real in the acceptance run.
    """
    r = subprocess.run([_SYSTEM_PYTHON, _HELPER, out],
                       capture_output=True, text=True, timeout=45)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"portal helper spoke no JSON (exit {r.returncode}): "
            f"{r.stderr.strip()[:200]}")
    if not payload.get("ok"):
        raise RuntimeError(f"portal grab failed: {payload.get('error')}")
    return {"path": payload["path"],
            "screensaver_active": payload.get("screensaver_active")}


def capture(out="/tmp/mercury_eye.png"):
    """Full-frame portal capture that refuses to return a lie.

    Returns a dict, always:
      usable            True only when the frame passed every gate
      path              the PNG on disk (present even when unusable, for triage)
      physical          (w, h) of the capture in PHYSICAL pixels
      logical           (w, h) of the logical desktop (derived)
      scale             capture_w / logical_w when uniform
      stats             frame_stats() output
      screensaver_active  ScreenSaver.GetActive at grab time (None = unknown)
      reasons           why usable is False (never silent)

    Gates, in order: portal grab succeeded -> per-axis scale uniform
    (spike 004: rotation/panning otherwise maps clicks silently wrong) ->
    frame not degenerate (failure #5: a blank frame is not an observation).
    """
    result = {"usable": False, "path": out, "physical": None, "logical": None,
              "scale": None, "stats": None, "screensaver_active": None,
              "reasons": []}

    try:
        grab = _portal_grab(out)
    except Exception as e:
        result["reasons"].append(f"portal capture failed: {e}")
        return result
    result["path"] = grab["path"]
    result["screensaver_active"] = grab.get("screensaver_active")

    stats = frame_stats(grab["path"])
    result["stats"] = stats
    result["physical"] = (stats["width"], stats["height"])

    g = geometry.probe()
    lw, lh = g["logical"]
    result["logical"] = (lw, lh)
    sx = stats["width"] / lw if lw else 0.0
    sy = stats["height"] / lh if lh else 0.0
    if abs(sx - sy) > SCALE_TOLERANCE * max(sx, sy):
        result["reasons"].append(
            f"non-uniform scale: x={sx:.4f} vs y={sy:.4f} against logical "
            f"{lw}x{lh} — refusing coordinate mapping (rotation/panning?)")
    else:
        result["scale"] = sx

    degenerate, why = is_degenerate(stats)
    if degenerate:
        result["reasons"].extend(why)

    result["usable"] = not result["reasons"]
    return result
