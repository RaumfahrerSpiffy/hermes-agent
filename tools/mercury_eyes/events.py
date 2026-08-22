"""wait_until — block until a matching surface/element appears.

Replaces every blind sleep(N) in a control workflow. Two paths, auto-selected
so callers never need to know which app is Chromium:

  event   AT-SPI push events (system helper). Qt/GTK measure ~117 ms
          launch-to-detect (spike 002). Works while the screen is LOCKED.
  poll    surfaces() re-read every poll_interval. The Chromium/CEF fallback:
          MEASURED 2026-08-21 — Chromium emitted ZERO AT-SPI events in 40 s
          during an active 30 GB download.

Design: the event path gets the first `event_grace` seconds alone (fast path,
no poll churn); if it has not matched by then the poller takes over while the
event helper keeps running to the full timeout. Whichever matches first wins.
Timeout is reported as an observation, method="timeout", never as silence.
"""
import os
import subprocess
import time

from . import surfaces

_HELPER = os.path.join(os.path.dirname(__file__), "event_helper.py")
_SYSTEM_PYTHON = "/usr/bin/python3"  # has gi; the agent venv does not
EVENT_GRACE_S = 0.5     # events alone get this head start before polling joins
DEFAULT_POLL_S = 0.5    # spike 002: Chromium poll cadence


def _matches(app, role, name, app_w=None, name_w=None, role_w=None):
    """Case-insensitive substring match; no criteria = match all."""
    if app_w and app_w.lower() not in (app or "").lower():
        return False
    if role_w and role_w != role:
        return False
    if name_w and name_w.lower() not in (name or "").lower():
        return False
    return True


def _event_wait(app, name, role, timeout):
    """System-side AT-SPI event wait. Returns the helper's JSON payload or
    None on timeout/failure. Monkeypatched in unit tests."""
    import json
    try:
        r = subprocess.run(
            [_SYSTEM_PYTHON, _HELPER, app or "", name or "", role or "",
             str(timeout)], capture_output=True, text=True,
            timeout=timeout + 10)
    except (subprocess.TimeoutExpired, OSError):
        return None
    line = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if payload.get("ok") else None


def wait_until(app=None, name=None, role=None, timeout=30.0,
               poll_interval=DEFAULT_POLL_S):
    """Block until a matching surface appears. Returns an OBSERVATION:

      matched   True/False
      method    "event" | "poll" | "timeout"
      ms        wall-clock to the answer
      element   the matched frame dict (poll path) or desc (event path)
      reason    present on timeout
      probed_at timestamp

    The event path and the poll path race; callers must NOT need to know
    which app is Chromium (measured: zero events) — the fallback is silent.
    """
    import threading

    t0 = time.monotonic()
    holder = {}

    def run_events():
        holder["event"] = _event_wait(app, name, role, timeout)

    thread = threading.Thread(target=run_events, daemon=True)
    thread.start()

    # Poll path joins after the event grace window.
    poll_at = t0 + EVENT_GRACE_S
    element = None
    while time.monotonic() - t0 < timeout:
        now = time.monotonic()
        if "event" in holder and holder["event"]:
            ms = (now - t0) * 1000
            return {"matched": True, "method": "event", "ms": ms,
                    "element": holder["event"], "probed_at": time.time()}
        if now >= poll_at:
            obs = surfaces.surfaces()
            for f in obs["frames"]:
                if _matches(f["app"], f["role"], f["name"],
                            app_w=app, name_w=name, role_w=role):
                    element = f
                    break
            if element is not None:
                # surface exists — if the event path later claims it, the
                # event thread already lost the race; poll reports now.
                return {"matched": True, "method": "poll",
                        "ms": (time.monotonic() - t0) * 1000,
                        "element": element, "probed_at": time.time()}
            poll_at = now + poll_interval
        time.sleep(min(0.05, max(timeout - (time.monotonic() - t0), 0)))

    # one last event-path read before declaring timeout
    thread.join(timeout=2.0)
    if holder.get("event"):
        ms = (time.monotonic() - t0) * 1000
        return {"matched": True, "method": "event", "ms": ms,
                "element": holder["event"], "probed_at": time.time()}

    return {"matched": False, "method": "timeout",
            "ms": (time.monotonic() - t0) * 1000, "element": None,
            "reason": (f"timeout after {timeout:.1f}s waiting for "
                       f"app={app!r} name={name!r} role={role!r}"),
            "probed_at": time.time()}
