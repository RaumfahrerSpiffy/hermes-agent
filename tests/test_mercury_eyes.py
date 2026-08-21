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
