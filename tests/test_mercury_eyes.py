"""MercuryEyes tests. Invariants and provenance only — never this monitor's numbers."""
import sys

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
        lambda: [_fr("kcalc", "KCalc", (0, 0, 400, 500))])
    obs = surfaces.surfaces()
    assert obs["frames"][0]["app"] == "kcalc"
    assert obs["probed_at"] > 0


def test_surfaces_stub_filter(monkeypatch):
    # Steam emits 3x1 stubs; frames <= 2px in either axis are noise
    from tools.mercury_eyes import surfaces
    monkeypatch.setattr(
        surfaces, "_enumerate_frames",
        lambda: [_fr("steam", "", (0, 0, 3, 1)),
                 _fr("steam", "Steam", (10, 10, 800, 600))])
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
