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


# --- keyboard ---------------------------------------------------------------
# HONESTY NOTE: there is no compositor readback for keystrokes the way there is
# for the cursor. We can assert the device was created and the events were
# written, and nothing more. So these verbs report DISPATCHED, never "typed
# successfully" — the caller is expected to confirm the effect by looking
# (screen look / surfaces) if the outcome matters. Reporting a keystroke as
# landed without evidence would be exactly the fabrication click() exists to
# prevent.

_SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "MINUS", "+": "EQUAL", "{": "LEFTBRACE",
    "}": "RIGHTBRACE", "|": "BACKSLASH", ":": "SEMICOLON", '"': "APOSTROPHE",
    "<": "COMMA", ">": "DOT", "?": "SLASH", "~": "GRAVE",
}
_PUNCT = {
    "-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE",
    "\\": "BACKSLASH", ";": "SEMICOLON", "'": "APOSTROPHE", ",": "COMMA",
    ".": "DOT", "/": "SLASH", "`": "GRAVE", " ": "SPACE", "\n": "ENTER",
    "\t": "TAB",
}
_MODIFIERS = {"ctrl": "KEY_LEFTCTRL", "control": "KEY_LEFTCTRL",
              "alt": "KEY_LEFTALT", "shift": "KEY_LEFTSHIFT",
              "meta": "KEY_LEFTMETA", "super": "KEY_LEFTMETA",
              "win": "KEY_LEFTMETA"}


def _keycode(ch):
    """Map one character to (KEY_ name, needs_shift). None when unmappable."""
    if ch in _SHIFTED:
        base = _SHIFTED[ch]
        return (f"KEY_{base}", True)
    if ch in _PUNCT:
        return (f"KEY_{_PUNCT[ch]}", False)
    if ch.isdigit():
        return (f"KEY_{ch}", False)
    if ch.isalpha() and ch.isascii():
        return (f"KEY_{ch.upper()}", ch.isupper())
    return None


class _UinputKeyboard:
    def __init__(self):
        from evdev import UInput, ecodes
        self._ecodes = ecodes
        keys = [c for c in ecodes.ecodes
                if c.startswith("KEY_")]
        cap = {ecodes.EV_KEY: [ecodes.ecodes[k] for k in keys]}
        self._dev = UInput(cap, name="mercury-eyes keyboard")
        time.sleep(SETTLE_S)

    def tap(self, key_name, modifiers=()):
        e = self._ecodes
        code = e.ecodes.get(key_name)
        if code is None:
            raise KeyError(key_name)
        mods = [e.ecodes[m] for m in modifiers]
        for m in mods:
            self._dev.write(e.EV_KEY, m, 1)
        self._dev.syn()
        self._dev.write(e.EV_KEY, code, 1)
        self._dev.syn()
        time.sleep(0.012)
        self._dev.write(e.EV_KEY, code, 0)
        for m in reversed(mods):
            self._dev.write(e.EV_KEY, m, 0)
        self._dev.syn()
        time.sleep(0.018)

    def close(self):
        self._dev.close()


def _open_keyboard():
    """Device seam — monkeypatched in unit tests, real uinput live."""
    return _UinputKeyboard()


def type_text(text, **_):
    """Dispatch `text` as keystrokes. Returns an OBSERVATION.

    Reports `dispatched` (count of characters actually written) and
    `unmapped` (characters this layout could not express). Never claims the
    text arrived anywhere — no readback channel exists.
    """
    obs = {"requested": len(text), "dispatched": 0, "unmapped": [],
           "verified": False,
           "reason": "keystrokes have no readback channel; dispatched only",
           "probed_at": time.time()}
    dev = _open_keyboard()
    try:
        for ch in text:
            mapped = _keycode(ch)
            if mapped is None:
                obs["unmapped"].append(ch)
                continue
            key_name, shifted = mapped
            dev.tap(key_name, ("KEY_LEFTSHIFT",) if shifted else ())
            obs["dispatched"] += 1
        obs["probed_at"] = time.time()
        return obs
    finally:
        dev.close()


def key_combo(keys, **_):
    """Dispatch one chord such as "ctrl+s" or "alt+F4". Returns an OBSERVATION."""
    obs = {"requested": keys, "dispatched": False, "verified": False,
           "reason": "keystrokes have no readback channel; dispatched only",
           "probed_at": time.time()}
    parts = [p.strip() for p in str(keys).split("+") if p.strip()]
    if not parts:
        obs["reason"] = "empty key specification"
        return obs
    *mod_names, final = parts
    mods = []
    for m in mod_names:
        code = _MODIFIERS.get(m.lower())
        if code is None:
            obs["reason"] = f"unknown modifier {m!r}"
            return obs
        mods.append(code)

    if len(final) == 1:
        mapped = _keycode(final)
        if mapped is None:
            obs["reason"] = f"unmappable key {final!r}"
            return obs
        key_name, shifted = mapped
        if shifted:
            mods.append("KEY_LEFTSHIFT")
    else:
        key_name = f"KEY_{final.upper()}"

    dev = _open_keyboard()
    try:
        dev.tap(key_name, tuple(mods))
        obs["dispatched"] = True
        obs["probed_at"] = time.time()
        return obs
    except KeyError:
        obs["reason"] = f"unknown key name {key_name!r}"
        return obs
    finally:
        dev.close()
