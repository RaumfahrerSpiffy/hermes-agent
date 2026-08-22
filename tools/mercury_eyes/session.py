"""Session guard — unlock for input, ALWAYS re-lock.

Reads (AT-SPI, capture, cursor) work while the screen is locked and must NOT
unlock. Only input verbs need this guard.

Two measured facts shape the design:

  * `loginctl unlock-session` alone leaves the KDE greeter up — the session
    reports unlocked while the greeter still owns the screen and input lands
    on the lock screen. Pair it with ScreenSaver.SetActive(false) and VERIFY
    by read-back before yielding.
  * ScreenSaver.GetActive is the probe that predicts a blank frame;
    LockedHint is NOT (measured: greeter showing at LockedHint=no).

Unlocking is a real security action. The contract here is narrow: verify
before yielding, restore in a `finally` so a crash cannot leave the machine
unlocked, and never re-lock a session we did not unlock.
"""
import contextlib
import signal
import subprocess
import time

BUS_NAME = "org.freedesktop.ScreenSaver"
BUS_PATH = "/ScreenSaver"
VERIFY_TIMEOUT_S = 5.0
VERIFY_POLL_S = 0.25

# Signals that would otherwise kill the process WITHOUT running `finally`.
# MEASURED 2026-08-21: a SIGTERM during a guarded body left the screen
# unlocked — Python's default SIGTERM handler terminates immediately, so the
# contextmanager's finally never ran. systemctl restart / kill / gateway
# restart all deliver SIGTERM. Trapping them is load-bearing for the promise
# "the screen always re-locks", not a nicety.
_TRAPPED_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class UnlockFailed(RuntimeError):
    """The unlock did not verify — refuse to act rather than act blind."""


def _qdbus(*args):
    r = subprocess.run(["qdbus6", BUS_NAME, BUS_PATH, *args],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(f"qdbus6 {' '.join(args)} failed: "
                           f"{r.stderr.strip()[:200]}")
    return r.stdout.strip()


def _screensaver_active():
    """True when the screensaver/greeter owns the screen. Seam for tests."""
    return _qdbus(f"{BUS_NAME}.GetActive").lower() == "true"


def _set_screensaver(value):
    """Ask the screensaver to activate/deactivate. Seam for tests."""
    _qdbus(f"{BUS_NAME}.SetActive", "true" if value else "false")


def _loginctl(verb):
    """Best-effort session verb; never fatal on its own (SetActive is the
    load-bearing call, this is belt-and-braces for the greeter)."""
    try:
        subprocess.run(["loginctl", verb], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _wait_until_active_is(want, timeout=VERIFY_TIMEOUT_S):
    """Poll GetActive until it reads `want`. Returns True when verified."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _screensaver_active() is want:
                return True
        except Exception:
            pass
        time.sleep(VERIFY_POLL_S)
    return False


@contextlib.contextmanager
def unlocked():
    """Unlock the session for input; guarantee restoration on the way out.

    Yields {"unlocked": bool, "was_active": bool, "entered_at": float}.

    Raises UnlockFailed when the unlock cannot be VERIFIED — the body never
    runs, because input on a greeter-owned screen lands nowhere (measured).
    If the session was already unlocked, it is left exactly as found: we do
    not lock a screen the user left open.
    """
    was_active = _screensaver_active()
    state = {"unlocked": not was_active, "was_active": was_active,
             "entered_at": time.time()}

    if not was_active:
        # already unlocked — touch nothing, restore nothing
        yield state
        return

    _loginctl("unlock-session")
    _set_screensaver(False)
    if not _wait_until_active_is(False):
        # leave the screen as we found it, then refuse
        try:
            _set_screensaver(True)
        except Exception:
            pass
        raise UnlockFailed(
            "unlock did not verify: ScreenSaver.GetActive still reports "
            "active after SetActive(false) — refusing to drive input at a "
            "greeter-owned screen")
    state["unlocked"] = True

    def _restore():
        try:
            _set_screensaver(True)
        except Exception:
            _loginctl("lock-session")

    # Trap kill signals so the restore runs even when the process is being
    # terminated (measured: default SIGTERM skips `finally` entirely).
    previous = {}
    for sig in _TRAPPED_SIGNALS:
        def _handler(signum, frame, _sig=sig):
            _restore()
            prior = previous.get(_sig)
            if callable(prior):
                prior(signum, frame)
            else:
                signal.signal(_sig, signal.SIG_DFL)
                signal.raise_signal(signum)
        try:
            previous[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):
            # not the main thread, or signal unavailable — the finally below
            # still covers the ordinary and exception paths
            pass

    try:
        yield state
    finally:
        for sig, prior in previous.items():
            try:
                signal.signal(sig, prior if prior is not None else signal.SIG_DFL)
            except (ValueError, OSError):
                pass
        _restore()
