"""Verified actions — the heart of the contract.

click() sequence: clamp -> open device -> settle -> move -> ASSERT the
compositor cursor within tolerance -> click -> return the OBSERVATION.
It refuses to click when the pointer is off target.

Failures #2/#4 (MEASURED 2026-08-21): a uinput ABS range declared over the
PHYSICAL panel put every click at 0.8x, and with no cursor readback the miss
was invisible — which invited a fabricated explanation. The assert here makes
that class of failure structurally impossible: a mis-mapped move is caught
BEFORE the button fires, and reported as a refusal with the measured delta.

Device notes, all measured (spike 004 corroborates both):
  * settle after device creation — kwin/libinput must enumerate the new
    device or events silently vanish. Our measurement: ~2.0 s required
    (spike 004 uses 500 ms; keep ours).
  * INPUT_PROP_DIRECT so libinput maps the axes to screen coordinates.
  * ABS range declared over the DERIVED logical desktop; targets pass
    through unscaled. Never the physical panel.
"""
import time

from . import coords, geometry, pointer

SETTLE_S = 2.0          # measured: events vanish below ~2 s enumeration
CLICK_TOLERANCE = 3     # px; |delta| beyond this refuses the click


class _UinputPointer:
    """Absolute uinput pointer over the derived logical desktop."""

    def __init__(self, width, height):
        from evdev import (AbsInfo, UInput, ecodes)
        cap = {
            ecodes.EV_ABS: [
                (ecodes.ABS_X, AbsInfo(0, 0, max(width - 1, 1), 0, 0, 1)),
                (ecodes.ABS_Y, AbsInfo(0, 0, max(height - 1, 1), 0, 0, 1)),
            ],
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT,
                            ecodes.BTN_MIDDLE],
        }
        self._ecodes = ecodes
        self._dev = UInput(cap, name="mercury-eyes absolute pointer",
                           input_props=[ecodes.INPUT_PROP_DIRECT])
        time.sleep(SETTLE_S)

    def move(self, x, y):
        e = self._ecodes
        self._dev.write(e.EV_ABS, e.ABS_X, x)
        self._dev.write(e.EV_ABS, e.ABS_Y, y)
        self._dev.syn()
        time.sleep(0.05)

    def click_button(self, button, count):
        e = self._ecodes
        code = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT,
                "middle": e.BTN_MIDDLE}[button]
        for _ in range(count):
            self._dev.write(e.EV_KEY, code, 1)
            self._dev.syn()
            time.sleep(0.02)
            self._dev.write(e.EV_KEY, code, 0)
            self._dev.syn()
            time.sleep(0.05)

    def close(self):
        self._dev.close()


def _open_pointer():
    """Device seam — monkeypatched in unit tests, real uinput live."""
    w, h = geometry.probe()["logical"]
    return _UinputPointer(w, h)


def click(x, y, button="left", count=1, verify=True):
    """Click at LOGICAL (x, y), asserting the pointer landed first.

    Returns an OBSERVATION, always:
      commanded   (x, y) actually targeted (after visible clamping)
      observed    compositor cursor after the move (None if unreadable)
      delta       observed - commanded (None if unreadable)
      on_target   True/False when asserted; None when verify=False
      clicked     whether the button actually fired
      reason      present whenever clicked is False, or verify was skipped
      probed_at   timestamp of the observation

    verify=True (default): cursor unreadable or |delta| > CLICK_TOLERANCE
    means NO click fires — a refusal with the evidence, not a guess.
    verify=False fires blind but says so: on_target=None, reason set.
    Never reports a click it did not observe the preconditions for.
    """
    cx, cy = coords.clamp_logical(x, y)
    obs = {"commanded": (cx, cy), "observed": None, "delta": None,
           "on_target": None, "clicked": False, "probed_at": time.time()}

    dev = _open_pointer()
    try:
        dev.move(cx, cy)

        if verify:
            cur = pointer.cursor_pos()
            if not cur["ok"]:
                obs["observed"] = cur.get("pos")
                obs["reason"] = (
                    f"cursor unreadable after move — refusing to click "
                    f"({cur.get('reason', 'no reason given')})")
                return obs
            ox, oy = cur["pos"]
            obs["observed"] = (ox, oy)
            obs["delta"] = (ox - cx, oy - cy)
            obs["on_target"] = (abs(ox - cx) <= CLICK_TOLERANCE
                                and abs(oy - cy) <= CLICK_TOLERANCE)
            if not obs["on_target"]:
                obs["reason"] = (
                    f"refusing to click: cursor at {obs['observed']}, "
                    f"commanded {obs['commanded']}, delta {obs['delta']} "
                    f"exceeds {CLICK_TOLERANCE}px tolerance")
                return obs
        else:
            obs["reason"] = "dispatched without verification (verify=False)"

        dev.click_button(button, count)
        obs["clicked"] = True
        obs["probed_at"] = time.time()
        return obs
    finally:
        dev.close()
