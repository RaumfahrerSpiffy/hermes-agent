"""Surface inventory + geometry diff — the unnamed-dialog catcher.

Failure #3 (MEASURED 2026-08-21): the Steam redeem wizard opened as an
UNNAMED frame; a named-node diff missed it for 30 minutes. A human assistant
sees "a box appeared in the middle of the screen" regardless of whether it
has a name. This module keys surfaces on (app, geometry), so a new rectangle
IS a new surface, named or not.

Enumeration reads AT-SPI toplevels (works while the screen is LOCKED); the
diff is pure and needs no desktop at all.
"""
import time

from . import geometry

_MIN_DIM = 3  # Steam emits 3x1 stub frames; <=2px in either axis is noise


def _enumerate_frames():
    """Every AT-SPI toplevel as {app, name, role, geom, kids}. Needs gi."""
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


def surfaces():
    """Current surface inventory as an OBSERVATION.

    Returns {"frames": [...], "probed_at": epoch, "origin_space": ...}.
    Geometry is in the space geometry.probe()["origin_space"] names —
    measured LOGICAL on this host, re-decided per machine.

    Freshness contract (failure #6): probed_at rides every observation so a
    stale inventory can never be silently passed off as current.
    """
    frames = [f for f in _enumerate_frames()
              if f["geom"][2] >= _MIN_DIM and f["geom"][3] >= _MIN_DIM]
    return {
        "frames": frames,
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
