"""Derived display geometry. NO MONITOR CONSTANTS.

Every value is probed from the host. Each probe records WHERE it came from,
so a caller can tell a measurement from a guess.

MEASURED TRAPS (2026-08-21, do not regress):
  * kscreen-doctor -j exposes `size` = PHYSICAL and has NO `geometry` key.
    Logical = size / scale.
  * The wl_output info tool reports integer scale (2) where the real
    fractional scale was 1.25 (MEASURED). Never a scale source.
  * Atspi.get_desktop(0).get_extents() returns a hardcoded stub.
    Use a real toplevel frame instead.
  * The portal returns a blank frame under the screen locker, but its
    DIMENSIONS are still correct -- so it calibrates even when it cannot see.
"""
import json
import os
import re
import struct
import subprocess
import time

_CACHE_TTL_S = 30.0
_cache = {"at": 0.0, "value": None}


def _png_size(data):
    """(width, height) from raw PNG bytes (IHDR), stdlib only."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        if w > 0 and h > 0:
            return (w, h)
    return None


def _portal_size():
    """PHYSICAL from a portal screenshot's dimensions.

    Empirical and compositor-agnostic: this IS the space captures come back
    in, so measuring it removes a whole class of disagreement. Dimensions are
    correct even when the frame is degenerate (dark/blank).

    Runs through portal_helper.py under the SYSTEM python3 — the agent venv
    has no gi/dbus. MEASURED 2026-08-22: this function previously did an
    in-process `import gi` + `import dbus`, so under the venv it raised and
    was silently skipped, dropping the most authoritative PHYSICAL source
    from the derivation ladder. It also duplicated capture.py's portal
    plumbing; that duplication is now removed in favour of the one helper.
    """
    import json
    import subprocess
    import tempfile

    helper = os.path.join(os.path.dirname(__file__), "portal_helper.py")
    out = os.path.join(tempfile.gettempdir(), f"mercury_geom_{os.getpid()}.png")
    try:
        r = subprocess.run(["/usr/bin/python3", helper, out],
                           capture_output=True, text=True, timeout=45)
        line = (r.stdout.strip().splitlines() or [""])[-1]
        payload = json.loads(line)
        if not payload.get("ok"):
            return None
        with open(payload["path"], "rb") as f:
            return _png_size(f.read())
    except Exception:
        return None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def _drm_size():
    """PHYSICAL from sysfs. No compositor needed, portable to any Linux."""
    base = "/sys/class/drm"
    for card in sorted(os.listdir(base)):
        status = os.path.join(base, card, "status")
        modes = os.path.join(base, card, "modes")
        try:
            if open(status).read().strip() != "connected":
                continue
            first = open(modes).read().split("\n")[0].strip()
            m = re.match(r"^(\d+)x(\d+)$", first)
            if m:
                return (int(m.group(1)), int(m.group(2)))
        except OSError:
            continue
    return None


def _kscreen_mode():
    """(physical_size, scale) from kscreen-doctor JSON. KDE only.

    TRAP: the JSON has NO `geometry` key; `size` and the current mode's
    `size` are PHYSICAL. Logical must be computed as size / scale.
    """
    r = subprocess.run(["kscreen-doctor", "-j"],
                       capture_output=True, text=True, timeout=15)
    outs = [o for o in json.loads(r.stdout).get("outputs", []) if o.get("enabled")]
    if not outs:
        return None
    o = outs[0]
    cur = o.get("currentModeId")
    mode = next((m for m in o.get("modes", []) if m.get("id") == cur), None)
    if not mode:
        return None
    phys = (int(mode["size"]["width"]), int(mode["size"]["height"]))
    scale = float(o.get("scale") or 1.0)
    return phys, scale


def _kscreen_phys():
    km = _kscreen_mode()
    return km[0] if km else None


def _atspi_toplevels():
    """Every AT-SPI toplevel frame as (x, y, w, h).

    Runs under the SYSTEM python3 via surfaces_helper.py — the agent venv has
    no gi. MEASURED 2026-08-22: this function previously did an in-process
    `import gi`, which raised ModuleNotFoundError under the venv. The caller
    caught it and returned origin_space="unknown" with the evidence string
    "AT-SPI unreachable from this interpreter" — silently undoing Task 1b's
    LOGICAL calibration on every probe. Same defect as surfaces.py had; this
    module was missed because its failure degraded quietly instead of raising.

    Raises on helper failure so the caller's existing except-branch still
    reports "unknown" honestly rather than inventing a decision.
    """
    import json
    import subprocess

    helper = os.path.join(os.path.dirname(__file__), "surfaces_helper.py")
    r = subprocess.run(["/usr/bin/python3", helper],
                       capture_output=True, text=True, timeout=30)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    payload = json.loads(line)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "surfaces helper failed")

    frames = []
    for f in payload.get("frames", []):
        x, y, w, h = f["geom"]
        if w <= 2 or h <= 2:
            continue
        frames.append((x, y, w, h))
    return frames


def _atspi_logical():
    """LOGICAL from the largest AT-SPI toplevel frame.

    TRAP: the AT-SPI root desktop reports a hardcoded stub, not the display.
    A real toplevel frame (shell desktop, locker greeter) reports true
    logical extents. Portable to any AT-SPI desktop including GNOME Shell.
    """
    frames = _atspi_toplevels()
    if not frames:
        return None
    return max(frames, key=lambda f: f[2] * f[3])[2:]


def classify_origin_space(frames, logical, physical, scale, tol=2):
    """Decide whether AT-SPI element ORIGINS are physical or logical pixels.

    The two hypotheses differ by the scale factor on any edge-anchored
    surface, so a single panel or dock discriminates them:

      origins logical:  panel bottom edge  y + h          == logical height
      origins physical: panel bottom edge  y + h * scale  == physical height
                        (sizes are logical under BOTH hypotheses -- that half
                        is settled; only the origin space is in question)

    frames: list of (x, y, w, h) toplevel extents.
    Returns {"origin_space": "logical"|"physical"|"equivalent"|"unknown",
             "evidence": [str, ...]}.

    "unknown" is a decision, not a failure: it means the visible frames
    carried no discriminating signal, or contradicted each other. Callers
    must treat it as refuse-to-guess.
    """
    if abs(scale - 1.0) < 1e-9:
        return {"origin_space": "equivalent",
                "evidence": ["scale == 1.0: physical and logical coincide"]}

    lw, lh = logical
    pw, ph = physical
    ptol = tol * scale  # rounding grows with the scale factor
    votes = set()
    evidence = []

    for (x, y, w, h) in frames:
        # Bottom-edge anchor (a top-anchored frame at y=0 carries no signal).
        if y > 0:
            if abs((y + h) - lh) <= tol:
                votes.add("logical")
                evidence.append(
                    f"frame ({x},{y} {w}x{h}): y+h={y + h} == logical_h")
            elif abs((y + h * scale) - ph) <= ptol:
                votes.add("physical")
                evidence.append(
                    f"frame ({x},{y} {w}x{h}): y+h*scale={y + h * scale:.1f}"
                    f" == physical_h")
        # Right-edge anchor.
        if x > 0:
            if abs((x + w) - lw) <= tol:
                votes.add("logical")
                evidence.append(
                    f"frame ({x},{y} {w}x{h}): x+w={x + w} == logical_w")
            elif abs((x + w * scale) - pw) <= ptol:
                votes.add("physical")
                evidence.append(
                    f"frame ({x},{y} {w}x{h}): x+w*scale={x + w * scale:.1f}"
                    f" == physical_w")

    if len(votes) == 1:
        return {"origin_space": votes.pop(), "evidence": evidence}
    if len(votes) > 1:
        return {"origin_space": "unknown",
                "evidence": ["conflicting anchors:"] + evidence}
    return {"origin_space": "unknown",
            "evidence": ["no edge-anchored frame carried a signal"]}


def _calibrated_origin_space(logical, physical, scale):
    """Classify from live AT-SPI frames; 'unknown' when the bus is out of reach."""
    try:
        frames = _atspi_toplevels()
    except Exception:
        return {"origin_space": "unknown",
                "evidence": ["AT-SPI unreachable from this interpreter"]}
    return classify_origin_space(frames, logical, physical, scale)


def _kscreen_logical():
    km = _kscreen_mode()
    if not km:
        return None
    phys, scale = km
    return (round(phys[0] / scale), round(phys[1] / scale))


def _x11_root():
    """LOGICAL == PHYSICAL on X11 (scale 1)."""
    if not os.environ.get("DISPLAY"):
        return None
    r = subprocess.run(["xdpyinfo"], capture_output=True, text=True, timeout=10)
    m = re.search(r"dimensions:\s+(\d+)x(\d+)", r.stdout)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def _physical_sources():
    return [("portal", _portal_size), ("drm", _drm_size), ("kscreen", _kscreen_phys)]


def _logical_sources():
    return [("atspi", _atspi_logical), ("kscreen", _kscreen_logical), ("x11", _x11_root)]


def _walk(ladder):
    """First source that answers wins. One failing source never breaks it."""
    for name, fn in ladder:
        try:
            v = fn()
        except Exception:
            continue
        if v:
            return name, v
    return "assumed", None


def probe(use_cache=True):
    """Derived display geometry.

    Returns {"physical", "logical", "scale", "physical_source",
             "logical_source", "confidence", "origin_space", "probed_at"}.

    SCALE is always computed as physical/logical, never read from a
    compositor field. When a ladder resolves nothing, the missing side is
    assumed equal to the known side and confidence is "assumed" -- a guess
    is never returned as a measurement.
    """
    if use_cache and _cache["value"] is not None \
            and time.monotonic() - _cache["at"] < _CACHE_TTL_S:
        return dict(_cache["value"])

    p_src, physical = _walk(_physical_sources())
    l_src, logical = _walk(_logical_sources())

    assumed = physical is None or logical is None
    if physical is None:
        physical = logical if logical else (1, 1)
    if logical is None:
        logical = physical
    scale = physical[0] / logical[0] if logical[0] else 1.0

    origin = _calibrated_origin_space(logical, physical, scale)

    value = {
        "physical": physical,
        "logical": logical,
        "scale": scale,
        "physical_source": p_src,
        "logical_source": l_src,
        "confidence": "assumed" if assumed else "measured",
        "origin_space": origin["origin_space"],
        "origin_evidence": origin["evidence"],
        "probed_at": time.time(),
    }
    _cache["at"] = time.monotonic()
    _cache["value"] = value
    return dict(value)


def invalidate():
    """Drop the cache. Call after any display-mode change."""
    _cache["at"] = 0.0
    _cache["value"] = None
