"""MercuryEyes tests. Invariants and provenance only — never this monitor's numbers."""
import contextlib
import json
import sys

import pytest

sys.path.insert(0, "/home/peterb/.hermes/hermes-agent")

from tools.mercury_eyes import geometry  # noqa: E402


def test_geometry_reports_its_source():
    g = geometry.probe()
    assert g["physical"][0] > 0 and g["physical"][1] > 0
    assert g["logical"][0] > 0 and g["logical"][1] > 0
    assert g["physical_source"] in {"portal", "drm", "kscreen", "assumed"}
    assert g["logical_source"] in {"atspi", "kscreen", "x11", "assumed"}
    assert g["confidence"] in {"measured", "assumed"}


def test_scale_is_derived_not_read():
    g = geometry.probe()
    assert abs(g["scale"] - g["physical"][0] / g["logical"][0]) < 1e-6
    assert 0.5 <= g["scale"] <= 4.0


def test_logical_never_exceeds_physical():
    g = geometry.probe()
    assert g["logical"][0] <= g["physical"][0]
    assert g["logical"][1] <= g["physical"][1]


def test_assumed_fallback_is_square_and_flagged(monkeypatch):
    monkeypatch.setattr(geometry, "_physical_sources", lambda: [])
    monkeypatch.setattr(geometry, "_logical_sources", lambda: [])
    g = geometry.probe(use_cache=False)
    assert g["scale"] == 1.0
    assert g["logical"] == g["physical"]
    assert g["confidence"] == "assumed"
    geometry.invalidate()  # do not leak the assumed geometry into later tests


def test_wayland_info_is_not_a_scale_source():
    # wl_output carries INTEGER scale only; it reported 2 where the real
    # fractional scale was 1.25. Guard against reintroduction.
    src = open(geometry.__file__).read()
    assert "wayland-info" not in src


# --- Task 1b: origin-space calibration -------------------------------------
# Synthetic geometry deliberately NOT this monitor: a 2500x1250 panel at
# scale 1.25 -> 2000x1000 logical. If the classifier only works at this
# host's numbers it fails here.

LOG = (2000, 1000)
PHYS = (2500, 1250)
SCALE = 1.25


def test_scale_one_origin_spaces_are_equivalent():
    r = geometry.classify_origin_space([(0, 940, 2000, 60)], (2000, 1000),
                                       (2000, 1000), 1.0)
    assert r["origin_space"] == "equivalent"


def test_panel_at_logical_origin_decodes_logical():
    # bottom panel: origin_y + height == LOGICAL height -> origins logical
    r = geometry.classify_origin_space([(0, 940, 2000, 60)], LOG, PHYS, SCALE)
    assert r["origin_space"] == "logical"
    assert r["evidence"]


def test_panel_at_physical_origin_decodes_physical():
    # origins physical, sizes logical: 1175 + 60*1.25 == 1250 == PHYS height
    r = geometry.classify_origin_space([(0, 1175, 2000, 60)], LOG, PHYS, SCALE)
    assert r["origin_space"] == "physical"
    assert r["evidence"]


def test_right_edge_anchor_also_discriminates():
    # right-side dock: origin_x + width == LOGICAL width -> origins logical
    r = geometry.classify_origin_space([(1952, 0, 48, 1000)], LOG, PHYS, SCALE)
    assert r["origin_space"] == "logical"


def test_conflicting_anchors_yield_unknown():
    # one frame reads logical, another reads physical -> refuse to decide
    frames = [(0, 940, 2000, 60), (0, 1175, 2000, 60)]
    r = geometry.classify_origin_space(frames, LOG, PHYS, SCALE)
    assert r["origin_space"] == "unknown"


def test_no_edge_anchored_frame_yields_unknown():
    # a floating window at neither edge carries no origin-space signal
    r = geometry.classify_origin_space([(500, 300, 800, 500)], LOG, PHYS, SCALE)
    assert r["origin_space"] == "unknown"


def test_probe_origin_space_is_a_decided_enum_value():
    g = geometry.probe()
    assert g["origin_space"] in {"logical", "physical", "equivalent", "unknown"}


# --- Task 2: coordinate conversion over derived geometry --------------------


def test_round_trip_is_stable_at_any_scale():
    from tools.mercury_eyes import coords
    for x, y in [(0, 0), (7, 13), (640, 480)]:
        px, py = coords.logical_to_physical(x, y)
        assert coords.physical_to_logical(px, py) == (x, y)


def test_clamp_uses_derived_bounds():
    from tools.mercury_eyes import coords
    w, h = geometry.probe()["logical"]
    assert coords.clamp_logical(w + 500, h + 500) == (w - 1, h - 1)
    assert coords.clamp_logical(-5, 10) == (0, 10)


def test_no_hardcoded_resolution_anywhere():
    import pathlib
    pkg = pathlib.Path("/home/peterb/.hermes/hermes-agent/tools/mercury_eyes")
    banned = ("2560", "1440", "2048", "1152", "1.25")
    for f in pkg.glob("*.py"):
        body = "\n".join(
            l for l in f.read_text().splitlines()
            if not l.strip().startswith("#") and "MEASURED" not in l
        )
        for tok in banned:
            assert tok not in body, f"{f.name} hardcodes {tok}"


# --- Task 3: degenerate-frame detector --------------------------------------


def test_blank_frame_is_degenerate(tmp_path):
    from PIL import Image
    from tools.mercury_eyes.capture import frame_stats, is_degenerate
    p = tmp_path / "blank.png"
    Image.new("RGB", (640, 480), (255, 255, 255)).save(p)
    s = frame_stats(str(p))
    assert s["unique_colours"] == 1
    assert s["lum_std"] < 1e-9  # float32 accumulation: ~3e-14, not exactly 0.0
    ok, reasons = is_degenerate(s)
    assert ok is True and reasons


def test_noise_frame_is_usable(tmp_path):
    import numpy as np
    from PIL import Image
    from tools.mercury_eyes.capture import frame_stats, is_degenerate
    p = tmp_path / "noise.png"
    a = (np.random.rand(480, 640, 3) * 255).astype("uint8")
    Image.fromarray(a).save(p)
    s = frame_stats(str(p))
    ok, _ = is_degenerate(s)
    assert ok is False


# --- Task 4: portal capture with sanity gate --------------------------------


def _fake_grab(tmp_path, w, h, noise=True):
    """Fabricate a portal-grab result for unit tests (no portal round-trip)."""
    import numpy as np
    from PIL import Image
    p = tmp_path / "grab.png"
    if noise:
        a = (np.random.rand(h, w, 3) * 255).astype("uint8")
        Image.fromarray(a).save(p)
    else:
        Image.new("RGB", (w, h), (0, 0, 0)).save(p)
    return {"path": str(p), "screensaver_active": False}


def test_capture_contract_on_usable_frame(tmp_path, monkeypatch):
    from tools.mercury_eyes import capture, geometry
    g = geometry.probe()
    pw, ph = g["physical"]
    monkeypatch.setattr(capture, "_portal_grab",
                        lambda out: _fake_grab(tmp_path, pw, ph))
    r = capture.capture(out=str(tmp_path / "out.png"))
    assert r["usable"] is True
    assert r["physical"] == (pw, ph)
    assert r["scale"] == g["scale"]
    assert r["stats"]["unique_colours"] > 50
    assert r["screensaver_active"] is False
    import os
    assert os.path.exists(r["path"])


def test_capture_refuses_blank_frame(tmp_path, monkeypatch):
    from tools.mercury_eyes import capture, geometry
    pw, ph = geometry.probe()["physical"]
    monkeypatch.setattr(capture, "_portal_grab",
                        lambda out: _fake_grab(tmp_path, pw, ph, noise=False))
    r = capture.capture(out=str(tmp_path / "out.png"))
    assert r["usable"] is False
    assert r["reasons"]  # human-readable, never silent


def test_capture_refuses_nonuniform_scale(tmp_path, monkeypatch):
    # Borrowed from spike 004: when capture/logical scale disagrees per axis
    # (rotation, panning), coordinate mapping is refused rather than wrong.
    from tools.mercury_eyes import capture, geometry
    lw, lh = geometry.probe()["logical"]
    # square capture over a non-square logical desktop -> axis scales differ
    side = max(lw, lh) + 40
    monkeypatch.setattr(capture, "_portal_grab",
                        lambda out: _fake_grab(tmp_path, side, side))
    r = capture.capture(out=str(tmp_path / "out.png"))
    assert r["usable"] is False
    assert any("scale" in reason for reason in r["reasons"])


def test_capture_reports_portal_failure_honestly(tmp_path, monkeypatch):
    from tools.mercury_eyes import capture

    def boom(out):
        raise RuntimeError("portal timeout")

    monkeypatch.setattr(capture, "_portal_grab", boom)
    r = capture.capture(out=str(tmp_path / "out.png"))
    assert r["usable"] is False
    assert any("portal" in reason for reason in r["reasons"])


# --- Task 5: surface inventory + geometry diff -------------------------------
# Failure #3: the redeem wizard opened as an UNNAMED frame and a named-node
# diff missed it for 30 minutes. The diff below keys on (app, geometry), so a
# new rectangle is a new surface whether or not it has a name.


def _fr(app, name, geom, role="frame", kids=1):
    return {"app": app, "name": name, "role": role, "geom": list(geom),
            "kids": kids}


def test_unnamed_dialog_appears_in_diff():
    from tools.mercury_eyes import surfaces
    before = [_fr("steam", "Steam", (10, 10, 800, 600))]
    after = before + [_fr("steam", "", (300, 200, 400, 300), role="dialog")]
    d = surfaces.diff_surfaces(before, after)
    assert len(d["new"]) == 1
    assert d["new"][0]["name"] == ""      # unnamed is still caught
    assert d["gone"] == []


def test_closed_surface_appears_as_gone():
    from tools.mercury_eyes import surfaces
    before = [_fr("kcalc", "KCalc", (0, 0, 400, 500)),
              _fr("kcalc", "", (100, 100, 200, 150), role="dialog")]
    after = [before[0]]
    d = surfaces.diff_surfaces(before, after)
    assert d["new"] == []
    assert len(d["gone"]) == 1 and d["gone"][0]["role"] == "dialog"


def test_moved_surface_is_new_plus_gone():
    # keying on (app, geom): a moved window reads as one new + one gone —
    # documented behavior, not a bug; callers correlate by app if needed.
    from tools.mercury_eyes import surfaces
    before = [_fr("dolphin", "Home", (0, 0, 640, 480))]
    after = [_fr("dolphin", "Home", (60, 40, 640, 480))]
    d = surfaces.diff_surfaces(before, after)
    assert len(d["new"]) == 1 and len(d["gone"]) == 1


def test_identical_inventories_diff_empty():
    from tools.mercury_eyes import surfaces
    frames = [_fr("plasmashell", "Desktop", (0, 0, 2000, 1000)),
              _fr("plasmashell", "", (0, 940, 2000, 60), role="panel")]
    d = surfaces.diff_surfaces(frames, frames)
    assert d == {"new": [], "gone": []}


def test_surfaces_returns_observation_with_timestamp(monkeypatch):
    # freshness contract (failure #6): every observation carries probed_at
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: ([_fr("kcalc", "KCalc", (0, 0, 400, 500))], None))
    obs = surfaces.surfaces()
    assert obs["frames"][0]["app"] == "kcalc"
    assert obs["probed_at"] > 0


def test_surfaces_stub_filter(monkeypatch):
    # Steam emits 3x1 stubs; frames <= 2px in either axis are noise
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: ([_fr("steam", "", (0, 0, 3, 1)),
                  _fr("steam", "Steam", (10, 10, 800, 600))], None))
    obs = surfaces.surfaces()
    assert len(obs["frames"]) == 1
    assert obs["frames"][0]["name"] == "Steam"


# --- Task 6: cursor readback -------------------------------------------------
# Compositor-truth pointer position. The KWin script's print() is unreachable;
# the value rides a private DBus callback object (spike 004 pattern) — no
# clipboard clobber. Failure #2/#4: without this readback, a mis-scaled click
# lands silently wrong and invites fabricated explanations.


def test_cursor_pos_is_an_observation(monkeypatch):
    from tools.mercury_eyes import pointer
    monkeypatch.setattr(pointer, "_query_cursor", lambda: (412, 388))
    obs = pointer.cursor_pos()
    assert obs["ok"] is True
    assert obs["pos"] == (412, 388)
    assert obs["probed_at"] > 0


def test_cursor_failure_is_honest_not_silent(monkeypatch):
    from tools.mercury_eyes import pointer

    def boom():
        raise RuntimeError("kwin scripting unavailable")

    monkeypatch.setattr(pointer, "_query_cursor", boom)
    obs = pointer.cursor_pos()
    assert obs["ok"] is False
    assert obs["pos"] is None
    assert "kwin" in obs["reason"].lower()


def test_cursor_outside_logical_bounds_is_flagged(monkeypatch):
    # a reading outside the derived desktop means the coordinate space is
    # wrong (the 0.8x bug's signature) — flag it, never pass it through
    from tools.mercury_eyes import pointer, geometry
    w, h = geometry.probe()["logical"]
    monkeypatch.setattr(pointer, "_query_cursor", lambda: (w + 100, h + 50))
    obs = pointer.cursor_pos()
    assert obs["ok"] is False
    assert obs["pos"] == (w + 100, h + 50)   # raw reading still reported
    assert "bounds" in obs["reason"]


# --- Task 7: verified click --------------------------------------------------
# The heart of the contract: clamp -> move -> ASSERT cursor within tolerance
# -> click -> return the observation. Refuses to click off-target (failures
# #2/#4: the 0.8x mis-scale plus the fabricated explanation it invited).


def _wire_act(monkeypatch, cursor_at=None, move_log=None, click_log=None):
    """Stub the device seam and cursor readback; return the act module."""
    from tools.mercury_eyes import act

    class FakeDevice:
        def move(self, x, y):
            if move_log is not None:
                move_log.append((x, y))

        def click_button(self, button, count):
            if click_log is not None:
                click_log.append((button, count))

        def close(self):
            pass

    monkeypatch.setattr(act, "_open_pointer", lambda: FakeDevice())
    if cursor_at is not None:
        monkeypatch.setattr(
            act.pointer, "cursor_pos",
            lambda: {"ok": True, "pos": cursor_at, "probed_at": 1.0})
    return act


def test_click_on_target_fires_and_reports_observation(monkeypatch):
    clicks = []
    act = _wire_act(monkeypatch, cursor_at=(400, 300), click_log=clicks)
    r = act.click(400, 300)
    assert r["clicked"] is True
    assert r["on_target"] is True
    assert r["commanded"] == (400, 300)
    assert r["observed"] == (400, 300)
    assert r["delta"] == (0, 0)
    assert clicks == [("left", 1)]
    assert r["probed_at"] > 0


def test_click_refuses_when_cursor_off_target(monkeypatch):
    # commanded (400,300), cursor lands 20px off -> REFUSE, report delta
    clicks = []
    act = _wire_act(monkeypatch, cursor_at=(420, 300), click_log=clicks)
    r = act.click(400, 300)
    assert r["clicked"] is False
    assert r["on_target"] is False
    assert r["delta"] == (20, 0)
    assert clicks == []                       # no blind click fired
    assert "refus" in r["reason"].lower()


def test_click_refuses_when_cursor_unreadable(monkeypatch):
    clicks = []
    act = _wire_act(monkeypatch, click_log=clicks)
    monkeypatch.setattr(
        act.pointer, "cursor_pos",
        lambda: {"ok": False, "pos": None, "probed_at": 1.0,
                 "reason": "kwin unavailable"})
    r = act.click(400, 300)
    assert r["clicked"] is False
    assert clicks == []
    assert "cursor" in r["reason"].lower()


def test_click_unverified_mode_reports_honestly(monkeypatch):
    # verify=False fires without the assert but the result SAYS so —
    # dispatched-not-verified, never dressed as a confirmed click
    clicks = []
    act = _wire_act(monkeypatch, click_log=clicks)
    monkeypatch.setattr(
        act.pointer, "cursor_pos",
        lambda: {"ok": False, "pos": None, "probed_at": 1.0, "reason": "x"})
    r = act.click(400, 300, verify=False)
    assert r["clicked"] is True
    assert r["on_target"] is None             # not asserted, not claimed
    assert clicks == [("left", 1)]


def test_click_clamps_offscreen_target(monkeypatch):
    from tools.mercury_eyes import geometry
    w, h = geometry.probe()["logical"]
    moves = []
    act = _wire_act(monkeypatch, cursor_at=(w - 1, h - 1), move_log=moves)
    r = act.click(w + 500, h + 500)
    assert r["commanded"] == (w - 1, h - 1)   # clamped, visibly
    assert moves == [(w - 1, h - 1)]
    assert r["clicked"] is True


# --- Task 8: wait_until with Chromium poll fallback --------------------------
# Replaces every blind sleep(N). Qt/GTK: AT-SPI push events (~117 ms measured,
# spike 002). Chromium/CEF: emits ZERO events (measured: 0 in 40 s during an
# active 30 GB download) — silent poll fallback. Callers must NOT need to know
# which app is Chromium.


def test_matcher_is_substring_and_case_insensitive():
    from tools.mercury_eyes import events
    assert events._matches("kcalc", "frame", "KCalc",
                           app_w="KCALC", name_w="kcal", role_w="frame")
    assert not events._matches("kcalc", "frame", "KCalc", app_w="steam")
    assert not events._matches("kcalc", "frame", "KCalc", role_w="dialog")
    assert events._matches("anything", "x", "y")   # no criteria = match all


def test_event_path_wins_when_events_fire(monkeypatch):
    from tools.mercury_eyes import events
    monkeypatch.setattr(
        events, "_event_wait",
        lambda app, name, role, timeout: {"ms": 117.0,
                                          "desc": "kcalc/frame 'KCalc'"})
    r = events.wait_until(name="KCalc", timeout=5.0)
    assert r["matched"] is True
    assert r["method"] == "event"
    assert r["element"]["ms"] == 117.0       # helper's own detection latency
    assert r["ms"] < 2000.0                  # wall clock: no poll-path stall
    assert r["probed_at"] > 0


def test_poll_fallback_catches_eventless_surface(monkeypatch):
    # event path returns nothing (Chromium), poll sees a matching new frame
    from tools.mercury_eyes import events
    monkeypatch.setattr(events, "_event_wait",
                        lambda app, name, role, timeout: None)
    frames = [{"app": "steamwebhelper", "name": "", "role": "frame",
               "geom": [300, 200, 400, 300], "kids": 2}]
    monkeypatch.setattr(events.surfaces, "surfaces",
                        lambda: {"frames": frames, "probed_at": 1.0,
                                 "origin_space": "logical"})
    r = events.wait_until(app="steamwebhelper", timeout=1.0,
                          poll_interval=0.1)
    assert r["matched"] is True
    assert r["method"] == "poll"
    assert r["element"]["app"] == "steamwebhelper"


def test_timeout_is_honest(monkeypatch):
    from tools.mercury_eyes import events
    monkeypatch.setattr(events, "_event_wait",
                        lambda app, name, role, timeout: None)
    monkeypatch.setattr(events.surfaces, "surfaces",
                        lambda: {"frames": [], "probed_at": 1.0,
                                 "origin_space": "logical"})
    r = events.wait_until(name="NeverAppears", timeout=0.3,
                          poll_interval=0.1)
    assert r["matched"] is False
    assert r["method"] == "timeout"
    assert r["ms"] >= 300.0
    assert "timeout" in r["reason"].lower()


def test_poll_matches_unnamed_frame_by_app(monkeypatch):
    # failure #3's shape: UNNAMED dialog — matchable by app + role alone
    from tools.mercury_eyes import events
    monkeypatch.setattr(events, "_event_wait",
                        lambda app, name, role, timeout: None)
    frames = [{"app": "steam", "name": "", "role": "dialog",
               "geom": [500, 300, 600, 400], "kids": 5}]
    monkeypatch.setattr(events.surfaces, "surfaces",
                        lambda: {"frames": frames, "probed_at": 1.0,
                                 "origin_space": "logical"})
    r = events.wait_until(app="steam", role="dialog", timeout=1.0,
                          poll_interval=0.1)
    assert r["matched"] is True
    assert r["element"]["name"] == ""


# --- Task 9: session guard ---------------------------------------------------
# Input needs an unlocked session; the screen must ALWAYS re-lock. The guard
# verifies the unlock actually took (loginctl alone leaves the KDE greeter up)
# and restores the prior state in a finally — a crash mid-task must not leave
# the machine unlocked.


class _FakeSession:
    """Scriptable stand-in for the real screen-state calls."""

    def __init__(self, active=True, unlock_works=True):
        self.active = active
        self.unlock_works = unlock_works
        self.calls = []

    def get_active(self):
        self.calls.append("get")
        return self.active

    def set_active(self, value):
        self.calls.append(f"set:{value}")
        if value is False and not self.unlock_works:
            return          # greeter stays up — the measured KDE trap
        self.active = value


def _wire_session(monkeypatch, fake):
    from tools.mercury_eyes import session
    monkeypatch.setattr(session, "_screensaver_active", fake.get_active)
    monkeypatch.setattr(session, "_set_screensaver", fake.set_active)
    return session


def test_unlocked_yields_only_after_verifying(monkeypatch):
    fake = _FakeSession(active=True)
    session = _wire_session(monkeypatch, fake)
    seen = {}
    with session.unlocked() as state:
        seen["inside"] = fake.active
        seen["state"] = state
    assert seen["inside"] is False          # genuinely unlocked before body ran
    assert seen["state"]["unlocked"] is True
    assert seen["state"]["was_active"] is True


def test_relock_happens_even_on_exception(monkeypatch):
    fake = _FakeSession(active=True)
    session = _wire_session(monkeypatch, fake)
    try:
        with session.unlocked():
            assert fake.active is False
            raise RuntimeError("task blew up mid-flight")
    except RuntimeError:
        pass
    assert fake.active is True              # restored despite the exception


def test_refuses_when_unlock_does_not_take(monkeypatch):
    # loginctl/SetActive can report success while the greeter stays up —
    # verify by read-back and REFUSE rather than act blind
    fake = _FakeSession(active=True, unlock_works=False)
    session = _wire_session(monkeypatch, fake)
    with pytest.raises(session.UnlockFailed) as exc:
        with session.unlocked():
            raise AssertionError("body must not run on a failed unlock")
    assert "verif" in str(exc.value).lower()


def test_already_unlocked_session_is_left_alone(monkeypatch):
    # reads work while locked and must NOT unlock; an already-unlocked
    # session must not be re-locked on exit (we did not lock it)
    fake = _FakeSession(active=False)
    session = _wire_session(monkeypatch, fake)
    with session.unlocked() as state:
        assert state["was_active"] is False
    assert fake.active is False             # left as we found it
    assert "set:True" not in fake.calls


def test_sigterm_during_guard_still_relocks(tmp_path):
    # MEASURED 2026-08-21: default SIGTERM handling terminates the process
    # WITHOUT running `finally` — a systemctl restart mid-task left the
    # screen unlocked. The guard traps kill signals; this pins that shut.
    import signal
    import subprocess
    import sys as _sys
    import textwrap

    state_file = tmp_path / "lockstate"
    state_file.write_text("locked")
    script = tmp_path / "guarded.py"
    script.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, "/home/peterb/.hermes/hermes-agent")
        from tools.mercury_eyes import session
        P = {str(state_file)!r}
        session._screensaver_active = lambda: open(P).read().strip() == "locked"
        session._set_screensaver = lambda v: open(P, "w").write(
            "locked" if v else "unlocked")
        session._loginctl = lambda verb: None
        with session.unlocked():
            print("BODY", flush=True)
            time.sleep(30)
    """))

    proc = subprocess.Popen([_sys.executable, str(script)],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "BODY"
        assert state_file.read_text().strip() == "unlocked"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert state_file.read_text().strip() == "locked"


# --- Task 10: the `screen` tool ---------------------------------------------
# One tool, seven verbs, service-gated by check_fn. Contract: the handler
# ALWAYS returns a JSON string (never raises, never returns a dict), input
# verbs are approval-gated and run under the session guard, read verbs are
# ungated and never unlock.


def _screen_tool():
    from tools import screen_tool
    return screen_tool


def _call(monkeypatch, args, approve=True, **stubs):
    """Invoke the handler with the desktop layer and approval gate stubbed."""
    st = _screen_tool()
    calls = {"approval": [], "unlocked": 0}

    def _approval(tool_name, reason, **kw):
        calls["approval"].append((tool_name, reason))
        return {"approved": approve,
                "message": None if approve else "denied by user"}

    monkeypatch.setattr(st, "request_tool_approval", _approval)

    @contextlib.contextmanager
    def _guard():
        calls["unlocked"] += 1
        yield {"unlocked": True, "was_active": True}

    monkeypatch.setattr(st.session, "unlocked", _guard)
    for attr, value in stubs.items():
        target, _, fn = attr.partition("__")
        monkeypatch.setattr(getattr(st, target), fn, value)
    return st.handle_screen(args), calls


def test_handler_always_returns_a_json_string(monkeypatch):
    st = _screen_tool()
    # even a verb whose backend explodes must come back as parseable JSON
    monkeypatch.setattr(st.surfaces, "surfaces",
                        lambda: (_ for _ in ()).throw(RuntimeError("bus down")))
    out = st.handle_screen({"action": "surfaces"})
    assert isinstance(out, str)
    parsed = json.loads(out)
    assert "error" in parsed
    assert "bus down" in parsed["error"]


def test_unknown_verb_is_an_honest_error(monkeypatch):
    st = _screen_tool()
    parsed = json.loads(st.handle_screen({"action": "teleport"}))
    assert "error" in parsed
    assert "teleport" in parsed["error"]


def test_read_verbs_are_not_approval_gated_and_never_unlock(monkeypatch):
    for verb, stub in (
        ("surfaces", {"surfaces__surfaces": lambda: {"frames": [], "probed_at": 1.0}}),
        ("cursor", {"pointer__cursor_pos": lambda: {"ok": True, "pos": [10, 20]}}),
    ):
        out, calls = _call(monkeypatch, {"action": verb}, **stub)
        json.loads(out)
        assert calls["approval"] == [], f"{verb} must not prompt for approval"
        assert calls["unlocked"] == 0, f"{verb} must not unlock the session"


def test_input_verbs_are_approval_gated(monkeypatch):
    out, calls = _call(
        monkeypatch, {"action": "click", "x": 100, "y": 200},
        act__click=lambda x, y, **kw: {"ok": True, "clicked": [x, y]},
    )
    assert json.loads(out)["ok"] is True
    assert len(calls["approval"]) == 1
    assert calls["approval"][0][0] == "screen"
    assert "100" in calls["approval"][0][1]      # coordinates shown to the human


def test_denied_approval_performs_no_input(monkeypatch):
    fired = []
    out, calls = _call(
        monkeypatch, {"action": "click", "x": 5, "y": 5}, approve=False,
        act__click=lambda x, y, **kw: fired.append((x, y)) or {"ok": True},
    )
    parsed = json.loads(out)
    assert fired == [], "input fired despite denied approval"
    assert parsed.get("ok") is not True
    assert "denied" in json.dumps(parsed).lower()
    assert calls["unlocked"] == 0, "must not unlock when approval is denied"


def test_input_verbs_run_under_the_session_guard(monkeypatch):
    out, calls = _call(
        monkeypatch, {"action": "type", "text": "hello"},
        act__type_text=lambda text, **kw: {"ok": True, "typed": text},
    )
    assert json.loads(out)["ok"] is True
    assert calls["unlocked"] == 1, "input must be wrapped in session.unlocked()"


def test_check_fn_gates_on_the_real_desktop_requirements(monkeypatch):
    st = _screen_tool()
    monkeypatch.setattr(st.os.path, "exists", lambda p: True)
    monkeypatch.setattr(st.os, "access", lambda p, m: True)
    monkeypatch.setattr(st.shutil, "which", lambda c: "/usr/bin/qdbus6")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert st._check() is True
    # a host without the uinput write bit cannot drive input — stay hidden
    monkeypatch.setattr(st.os, "access", lambda p, m: False)
    assert st._check() is False
    # a non-Wayland session is out of scope for this tool
    monkeypatch.setattr(st.os, "access", lambda p, m: True)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert st._check() is False


def test_registration_is_top_level_and_discoverable():
    # the AST scanner in discover_builtin_tools() only sees module-body
    # registry.register() calls — a nested one is silently never loaded
    import ast
    import pathlib
    src = pathlib.Path(
        "/home/peterb/.hermes/hermes-agent/tools/screen_tool.py").read_text()
    tree = ast.parse(src)
    top_level_register = any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "attr", "") == "register"
        for node in tree.body
    )
    assert top_level_register, "registry.register() must be at module top level"


def test_tool_is_registered_with_the_expected_surface():
    """Assert the EMITTED schema, not the registered dict.

    The earlier version of this test read ``entry.schema["function"]``, which
    only passes when the schema is doubly-wrapped — so it certified the very
    defect it should have caught (MEASURED 2026-08-23: the tool dispatched
    fine while the model saw an empty description and empty parameters).
    ``get_definitions()`` is what the model actually receives, so that is what
    gets asserted.
    """
    from tools.registry import registry
    import tools.screen_tool  # noqa: F401  — import triggers registration
    entry = registry.get_entry("screen")
    assert entry is not None
    assert entry.toolset == "screen"

    defs = registry.get_definitions({"screen"})
    assert defs, "screen produced no definition (check_fn false on this host?)"
    fn = defs[0]["function"]
    assert defs[0]["type"] == "function"
    assert fn["name"] == "screen"
    assert len(fn["description"]) > 50, "description reached the model empty"
    verbs = set(fn["parameters"]["properties"]["action"]["enum"])
    assert verbs == {"look", "surfaces", "wait", "click", "type", "key",
                     "cursor"}
    assert fn["parameters"]["required"] == ["action"]


def test_no_registered_tool_has_a_doubly_wrapped_schema():
    """Whole-class guard: register() takes the BARE function object.

    ``get_definitions()`` adds the {"type": "function", "function": ...}
    envelope itself. A schema that already carries it registers and dispatches
    without complaint while the model receives an empty description and empty
    parameters. This asserts the invariant across every registered tool, so a
    future tool cannot reintroduce the shape silently.
    """
    from tools.registry import registry, discover_builtin_tools
    discover_builtin_tools()
    offenders = [
        name for name, entry in registry._tools.items()
        if isinstance(entry.schema, dict)
        and "function" in entry.schema
        and "type" in entry.schema
    ]
    assert not offenders, (
        f"doubly-wrapped tool schemas (model sees empty description and "
        f"parameters): {sorted(offenders)}"
    )


# --- DPMS: the blank frame that was NOT a lock ------------------------------
# MEASURED 2026-08-22, calibrated against the commander's on-site observation
# ("dark sleeping monitor, no prompt"): the session was UNLOCKED the whole
# time. GetActive=false, LockedHint=no and no kscreenlocker_greet were all
# CORRECT readings. The blank frame was the output in DPMS power-save.
#
# `kscreen-doctor --dpms show` -> "dpms mode for screen DP-1: off" is the
# probe that agrees with his eyes. The reference implementation
# (agent-sh/computer-use-linux) has no DPMS handling at all — its only blank
# check is `bytes.is_empty()`, which a valid 15KB black PNG passes.


def test_dpms_state_is_read_from_kscreen_doctor(monkeypatch):
    from tools.mercury_eyes import capture

    def _fake_run(cmd, **kw):
        assert "kscreen-doctor" in cmd[0]
        assert "--dpms" in cmd and "show" in cmd
        return type("R", (), {"returncode": 0, "stdout": "dpms mode for screen DP-1: off\n", "stderr": ""})()

    monkeypatch.setattr(capture.subprocess, "run", _fake_run)
    state = capture.dpms_state()
    assert state["asleep"] is True
    assert "DP-1" in str(state["outputs"])


def test_dpms_state_reports_awake_when_on(monkeypatch):
    from tools.mercury_eyes import capture
    monkeypatch.setattr(capture.subprocess, "run",
                        lambda cmd, **kw: type("R", (), {
                            "returncode": 0,
                            "stdout": "dpms mode for screen DP-1: on\n",
                            "stderr": ""})())
    assert capture.dpms_state()["asleep"] is False


def test_dpms_state_unreadable_is_none_not_false(monkeypatch):
    # absence of signal is NOT evidence of an awake display
    from tools.mercury_eyes import capture
    monkeypatch.setattr(capture.subprocess, "run",
                        lambda cmd, **kw: (_ for _ in ()).throw(OSError("no kscreen-doctor")))
    state = capture.dpms_state()
    assert state["asleep"] is None
    assert state["reason"]


def test_wake_verifies_by_reading_dpms_back(monkeypatch):
    from tools.mercury_eyes import capture
    seen = []

    def _fake_run(cmd, **kw):
        seen.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(capture.subprocess, "run", _fake_run)
    # after the wake command, the state read must come back awake
    states = iter([{"asleep": False, "outputs": {"DP-1": "on"}, "reason": None}])
    monkeypatch.setattr(capture, "dpms_state", lambda: next(states))
    obs = capture.wake()
    assert obs["ok"] is True
    assert obs["verified"] is True
    assert any("--dpms" in c and "on" in c for c in seen)


def test_wake_that_does_not_take_is_reported_as_failure(monkeypatch):
    from tools.mercury_eyes import capture
    monkeypatch.setattr(capture.subprocess, "run",
                        lambda cmd, **kw: type("R", (), {
                            "returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(capture, "dpms_state",
                        lambda: {"asleep": True, "outputs": {"DP-1": "off"},
                                 "reason": None})
    obs = capture.wake()
    assert obs["ok"] is False
    assert obs["verified"] is False
    assert "still" in obs["reason"].lower() or "asleep" in obs["reason"].lower()


def test_degenerate_frame_triggers_wake_and_recapture(monkeypatch):
    """A dark frame is diagnosed, not just refused."""
    from tools.mercury_eyes import capture

    grabs = iter([{"path": "/tmp/dark.png", "screensaver_active": False},
                  {"path": "/tmp/real.png", "screensaver_active": False}])
    monkeypatch.setattr(capture, "_portal_grab", lambda out: next(grabs))
    stats = iter([
        {"path": "/tmp/dark.png", "width": 2560, "height": 1440, "bytes": 15090,
         "bytes_per_mp": 4093, "unique_colours": 4, "lum_std": 0.34,
         "dominant_frac": 0.9999},
        {"path": "/tmp/real.png", "width": 2560, "height": 1440,
         "bytes": 900000, "bytes_per_mp": 244140, "unique_colours": 22000,
         "lum_std": 61.2, "dominant_frac": 0.11},
    ])
    monkeypatch.setattr(capture, "frame_stats", lambda p: next(stats))
    monkeypatch.setattr(capture, "dpms_state",
                        lambda: {"asleep": True, "outputs": {"DP-1": "off"},
                                 "reason": None})
    monkeypatch.setattr(capture, "wake",
                        lambda: {"ok": True, "verified": True, "reason": None})
    monkeypatch.setattr(capture.geometry, "probe",
                        lambda: {"logical": (2048, 1152),
                                 "origin_space": "logical"})

    r = capture.capture(out="/tmp/x.png", wake_if_dark=True)
    assert r["usable"] is True
    assert r["woke"] is True
    assert "power save" in json.dumps(r).lower() or "dpms" in json.dumps(r).lower()


def test_wake_is_not_attempted_on_a_healthy_frame(monkeypatch):
    from tools.mercury_eyes import capture
    monkeypatch.setattr(capture, "_portal_grab",
                        lambda out: {"path": "/tmp/real.png",
                                     "screensaver_active": False})
    monkeypatch.setattr(capture, "frame_stats", lambda p: {
        "path": "/tmp/real.png", "width": 2560, "height": 1440,
        "bytes": 900000, "bytes_per_mp": 244140, "unique_colours": 22000,
        "lum_std": 61.2, "dominant_frac": 0.11})
    monkeypatch.setattr(capture.geometry, "probe",
                        lambda: {"logical": (2048, 1152),
                                 "origin_space": "logical"})
    fired = []
    monkeypatch.setattr(capture, "wake", lambda: fired.append(1))
    r = capture.capture(out="/tmp/x.png", wake_if_dark=True)
    assert r["usable"] is True
    assert fired == [], "must not touch the display when the frame is fine"
    assert r["woke"] is False


def test_wake_can_be_declined(monkeypatch):
    """wake_if_dark=False diagnoses without touching the user's display."""
    from tools.mercury_eyes import capture
    monkeypatch.setattr(capture, "_portal_grab",
                        lambda out: {"path": "/tmp/dark.png",
                                     "screensaver_active": False})
    monkeypatch.setattr(capture, "frame_stats", lambda p: {
        "path": "/tmp/dark.png", "width": 2560, "height": 1440, "bytes": 15090,
        "bytes_per_mp": 4093, "unique_colours": 4, "lum_std": 0.34,
        "dominant_frac": 0.9999})
    monkeypatch.setattr(capture, "dpms_state",
                        lambda: {"asleep": True, "outputs": {"DP-1": "off"},
                                 "reason": None})
    fired = []
    monkeypatch.setattr(capture, "wake", lambda: fired.append(1))
    monkeypatch.setattr(capture.geometry, "probe",
                        lambda: {"logical": (2048, 1152),
                                 "origin_space": "logical"})
    r = capture.capture(out="/tmp/x.png", wake_if_dark=False)
    assert r["usable"] is False
    assert fired == []
    assert r["woke"] is False
    assert any("power save" in s.lower() or "dpms" in s.lower()
               for s in r["reasons"]), r["reasons"]


# --- surfaces must not import gi in-process ---------------------------------
# MEASURED 2026-08-22: `screen surfaces` raised ModuleNotFoundError: No module
# named 'gi' under the agent venv. pointer/events shell out to system python3;
# surfaces did not. Task 10's tests stubbed surfaces.surfaces() and never
# exercised the real path, so both `surfaces` and `wait` shipped broken.


def test_surfaces_does_not_import_gi_in_the_agent_venv():
    import ast
    import pathlib
    src = pathlib.Path(
        "/home/peterb/.hermes/hermes-agent/tools/mercury_eyes/surfaces.py"
    ).read_text()
    # parse rather than grep: the docstring legitimately mentions the fix
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "gi" not in imported, (
        "surfaces.py must shell out to system python3 like pointer/events; "
        "the agent venv has no gi")


def test_surfaces_enumerates_through_the_system_interpreter(monkeypatch):
    from tools.mercury_eyes import surfaces
    seen = {}

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        payload = {"ok": True, "frames": [
            {"app": "kcalc", "name": "KCalc", "role": "frame",
             "geom": [10, 20, 400, 500], "kids": 3}]}
        return type("R", (), {"returncode": 0, "stdout": json.dumps(payload),
                              "stderr": ""})()

    monkeypatch.setattr(surfaces.subprocess, "run", _fake_run)
    monkeypatch.setattr(surfaces.geometry, "probe",
                        lambda: {"origin_space": "logical"})
    obs = surfaces.surfaces()
    assert seen["cmd"][0] == surfaces._SYSTEM_PYTHON
    assert len(obs["frames"]) == 1
    assert obs["frames"][0]["app"] == "kcalc"
    assert "probed_at" in obs


def test_surfaces_helper_failure_is_honest_not_an_empty_list(monkeypatch):
    # an empty inventory and a broken bus must NOT look identical
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(surfaces.subprocess, "run",
                        lambda cmd, **kw: type("R", (), {
                            "returncode": 1, "stdout": "",
                            "stderr": "bus unreachable"})())
    monkeypatch.setattr(surfaces.geometry, "probe",
                        lambda: {"origin_space": "logical"})
    obs = surfaces.surfaces()
    assert obs.get("ok") is False
    assert obs.get("error")
    assert obs["frames"] == []


def test_no_module_imports_gi_in_the_agent_venv():
    """The whole class, not one file at a time.

    MEASURED 2026-08-22: surfaces.py imported gi in-process and broke the
    `surfaces`/`wait` verbs loudly. geometry.py had the SAME defect and was
    missed, because its failure degraded QUIETLY — origin_space fell back to
    "unknown", silently undoing Task 1b's calibration. Per-file checks let
    the sibling through; this sweeps the package.
    """
    import ast
    import pathlib
    pkg = pathlib.Path(
        "/home/peterb/.hermes/hermes-agent/tools/mercury_eyes")
    offenders = []
    for path in sorted(pkg.glob("*.py")):
        if path.name.endswith("_helper.py"):
            continue  # helpers RUN under system python3 — gi is correct there
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            if "gi" in names:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"venv-side modules import gi: {offenders}. Shell out to "
        f"/usr/bin/python3 via a *_helper.py instead.")


def test_origin_space_is_decided_not_unknown_on_this_host(monkeypatch):
    """Task 1b's calibration must survive running under the agent venv.

    Regression: geometry._atspi_toplevels() raised ModuleNotFoundError in the
    venv, the caller swallowed it, and every probe returned "unknown" — a
    refuse-to-guess that looked like a considered decision.
    """
    from tools.mercury_eyes import geometry
    # a bottom-anchored panel: y+h == logical height discriminates the spaces
    monkeypatch.setattr(geometry, "_atspi_toplevels",
                        lambda: [(0, 1090, 2048, 62)])
    verdict = geometry.classify_origin_space(
        geometry._atspi_toplevels(), (2048, 1152), (2560, 1440), 1.25)
    assert verdict["origin_space"] == "logical"
    assert verdict["evidence"]


def test_origin_space_unknown_still_says_why(monkeypatch):
    from tools.mercury_eyes import geometry
    monkeypatch.setattr(geometry, "_atspi_toplevels",
                        lambda: (_ for _ in ()).throw(RuntimeError("no bus")))
    out = geometry._calibrated_origin_space((2048, 1152), (2560, 1440), 1.25)
    assert out["origin_space"] == "unknown"
    assert out["evidence"], "unknown must always carry its reason"


def test_capture_carries_probed_at(monkeypatch):
    """Freshness contract (failure #6) applies to `look` too.

    MEASURED 2026-08-22 during Task 11 acceptance: cursor and surfaces both
    carry probed_at; capture() did not. Every observation rides a timestamp
    or none of the contract means anything.
    """
    from tools.mercury_eyes import capture
    monkeypatch.setattr(capture, "_portal_grab",
                        lambda out: {"path": "/tmp/real.png",
                                     "screensaver_active": False})
    monkeypatch.setattr(capture, "frame_stats", lambda p: {
        "path": "/tmp/real.png", "width": 2560, "height": 1440,
        "bytes": 900000, "bytes_per_mp": 244140, "unique_colours": 22000,
        "lum_std": 61.2, "dominant_frac": 0.11})
    monkeypatch.setattr(capture.geometry, "probe",
                        lambda: {"logical": (2048, 1152),
                                 "origin_space": "logical"})
    import time
    before = time.time()
    r = capture.capture(out="/tmp/x.png", wake_if_dark=False)
    assert "probed_at" in r
    assert before <= r["probed_at"] <= time.time()


def test_keyboard_capability_excludes_reserved_codes():
    """MEASURED 2026-08-22: declaring ALL KEY_* names from ecodes made UInput
    creation fail EINVAL — reserved codes are rejected by the kernel. The cap
    set must be curated."""
    from tools.mercury_eyes import act
    from evdev import ecodes
    cap = act._keyboard_cap(ecodes)
    codes = cap[ecodes.EV_KEY]
    assert 0 not in codes, "code 0 (KEY_RESERVED) is rejected by uinput"
    assert all(c <= 0x2FF for c in codes), "codes beyond KEY_MAX are invalid"
    assert ecodes.KEY_A in codes and ecodes.KEY_1 in codes
    assert ecodes.KEY_LEFTCTRL in codes and ecodes.KEY_ESC in codes


def test_key_combo_aliases_evdev_names():
    """'Escape' must resolve to KEY_ESC, not the nonexistent KEY_ESCAPE."""
    from tools.mercury_eyes import act
    assert act._KEY_ALIASES["ESCAPE"] == "KEY_ESC"
    assert act._KEY_ALIASES["RETURN"] == "KEY_ENTER"


def test_type_and_key_report_dispatched_not_typed(monkeypatch):
    from tools.mercury_eyes import act

    class _FakeKbd:
        def __init__(self):
            self.taps = []

        def tap(self, key_name, modifiers=()):
            self.taps.append((key_name, modifiers))

        def close(self):
            pass

    kbd = _FakeKbd()
    monkeypatch.setattr(act, "_open_keyboard", lambda: kbd)
    r = act.type_text("aB!")
    assert r["dispatched"] == 3
    assert r["verified"] is False
    assert ("KEY_A", ()) in kbd.taps
    assert ("KEY_B", ("KEY_LEFTSHIFT",)) in kbd.taps
    assert ("KEY_1", ("KEY_LEFTSHIFT",)) in kbd.taps

    kbd.taps.clear()
    r = act.key_combo("ctrl+s")
    assert r["dispatched"] is True
    assert ("KEY_S", ("KEY_LEFTCTRL",)) in kbd.taps


# --- KWin-authoritative window geometry -------------------------------------
# MEASURED 2026-08-22 during Task 11 acceptance: EVERY application window on
# this host reports AT-SPI origin (0,0) — kcalc [0,0,640,480] while visibly
# center-screen, Thorium [0,0,...], Discover [0,0,...]. On Wayland, clients
# cannot see their own absolute geometry; only the compositor can. A click
# aimed at the AT-SPI rectangle struck bare desktop and the desktop-focused
# typing opened KRunner. AT-SPI keeps names/roles/children; KWin supplies
# position.


def test_surfaces_includes_kwin_windows(monkeypatch):
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: ([_fr("kcalc", "KCalc", (0, 0, 640, 480))], None))
    monkeypatch.setattr(
        surfaces, "_kwin_windows",
        lambda: ([{"app": "kcalc", "caption": "KCalc",
                   "x": 704, "y": 336, "w": 640, "h": 480,
                   "minimized": False}], None))
    monkeypatch.setattr(surfaces.geometry, "probe",
                        lambda: {"origin_space": "logical"})
    obs = surfaces.surfaces()
    assert "windows" in obs
    assert obs["windows"][0]["x"] == 704
    assert obs["windows_ok"] is True


def test_kwin_geometry_attaches_to_matching_frames(monkeypatch):
    """A frame whose app matches a KWin window gets authoritative geometry."""
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: ([_fr("kcalc", "KCalc", (0, 0, 640, 480))], None))
    monkeypatch.setattr(
        surfaces, "_kwin_windows",
        lambda: ([{"app": "kcalc", "caption": "KCalc",
                   "x": 704, "y": 336, "w": 640, "h": 480,
                   "minimized": False}], None))
    monkeypatch.setattr(surfaces.geometry, "probe",
                        lambda: {"origin_space": "logical"})
    obs = surfaces.surfaces()
    fr = obs["frames"][0]
    assert fr["geom"] == [704, 336, 640, 480], (
        "KWin geometry must replace the untrustworthy AT-SPI origin")
    assert fr["geom_source"] == "kwin"


def test_unmatched_frames_are_flagged_not_faked(monkeypatch):
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: ([_fr("plasmashell", "", (0, 1090, 2048, 62))], None))
    monkeypatch.setattr(surfaces, "_kwin_windows", lambda: ([], None))
    monkeypatch.setattr(surfaces.geometry, "probe",
                        lambda: {"origin_space": "logical"})
    obs = surfaces.surfaces()
    fr = obs["frames"][0]
    assert fr["geom"] == [0, 1090, 2048, 62]   # keep AT-SPI value
    assert fr["geom_source"] == "atspi"        # but SAY so


def test_kwin_failure_does_not_break_surfaces(monkeypatch):
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: ([_fr("kcalc", "KCalc", (0, 0, 640, 480))], None))
    monkeypatch.setattr(surfaces, "_kwin_windows",
                        lambda: ([], "kwin scripting unavailable"))
    monkeypatch.setattr(surfaces.geometry, "probe",
                        lambda: {"origin_space": "logical"})
    obs = surfaces.surfaces()
    assert obs["windows_ok"] is False
    assert obs["windows_error"] == "kwin scripting unavailable"
    assert obs["frames"][0]["geom_source"] == "atspi"


def test_reverse_domain_app_names_match(monkeypatch):
    """AT-SPI 'kcalc' must match KWin 'org.kde.kcalc'.

    MEASURED 2026-08-22: exact app-name matching missed both live kcalc
    windows; the merge left (0,0) origins while reporting success.
    KWin also returns float coordinates — merged geometry must be int.
    """
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: ([_fr("kcalc", "KCalc", (0, 0, 640, 480)),
                  _fr("kcalc", "KCalc", (0, 0, 640, 480))], None))
    monkeypatch.setattr(
        surfaces, "_kwin_windows",
        lambda: ([{"app": "org.kde.kcalc", "caption": "KCalc",
                   "x": 704.0, "y": 299.0, "w": 640.0, "h": 508.0,
                   "minimized": False},
                  {"app": "org.kde.kcalc", "caption": "KCalc",
                   "x": 746.67, "y": 322.04, "w": 640.0, "h": 508.0,
                   "minimized": False}], None))
    monkeypatch.setattr(surfaces.geometry, "probe",
                        lambda: {"origin_space": "logical"})
    obs = surfaces.surfaces()
    first, second = obs["frames"]
    assert first["geom"] == [704, 299, 640, 508]
    assert first["geom_source"] == "kwin"
    assert second["geom"] == [747, 322, 640, 508]  # floats rounded
    assert second["geom_source"] == "kwin"
