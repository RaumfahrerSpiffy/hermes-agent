"""Coordinate conversion over DERIVED geometry.

All internal APIs speak LOGICAL; only screenshots are PHYSICAL. Every value
comes from geometry.probe() at call time, so a resolution change followed by
invalidate() is picked up without a code edit.

MEASURED 2026-08-21: uinput ABS axes declared over the PHYSICAL panel made
every click land at 0.8x. The compositor maps a declared ABS range onto the
LOGICAL desktop. That class of bug is why there are no constants here.
"""
from . import geometry


def physical_to_logical(x, y):
    """Convert a pixel seen in a screenshot into a click target."""
    s = geometry.probe()["scale"]
    return (int(round(x / s)), int(round(y / s)))


def logical_to_physical(x, y):
    """Convert a click target into screenshot pixel space."""
    s = geometry.probe()["scale"]
    return (int(round(x * s)), int(round(y * s)))


def clamp_logical(x, y):
    """Clamp to the derived logical desktop. The compositor clamps silently;
    we do it visibly, and re-probe when a target falls outside the cached
    bounds so a display-mode change cannot silently misplace a click."""
    g = geometry.probe()
    w, h = g["logical"]
    if not (0 <= x < w and 0 <= y < h):
        g = geometry.probe(use_cache=False)
        w, h = g["logical"]
    return (max(0, min(int(x), w - 1)),
            max(0, min(int(y), h - 1)))
