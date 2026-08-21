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
