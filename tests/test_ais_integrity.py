"""Tests for processing.ais_integrity — R049 AIS coverage-gap + spoof detection.

The DETECTION math is real (great-circle distance + vessel-type speed bands);
it runs over the SYNTHETIC voyage dataset, so anomalies are illustrative. These
tests pin the defining properties:

* a long ping gap INSIDE a high-risk zone -> a GAP anomaly;
* the same gap in open ocean -> none (suppressed as normal coverage sparsity);
* two pings implying > vessel-max speed -> a TELEPORT anomaly, with the
  implied-speed arithmetic verified against the great-circle distance;
* a normal-speed leg -> none;
* a single-point / coordless / garbage track -> empty (no crash);
* assess_voyage combines both; scan_fleet aggregates; never raises on garbage.
"""
from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest

import processing.ais_integrity as ai
from processing.ais_integrity import (
    AisAnomaly,
    AisPing,
    HighRiskZone,
    assess_voyage,
    detect_gaps,
    detect_teleports,
    great_circle_km,
    scan_fleet,
)

UTC = _dt.timezone.utc


def _t(hours: float) -> _dt.datetime:
    """A UTC timestamp ``hours`` after a fixed epoch."""
    return _dt.datetime(2026, 1, 1, tzinfo=UTC) + _dt.timedelta(hours=hours)


# A high-risk zone centred on the Strait of Hormuz (the real chokepoint coord).
_HORMUZ = HighRiskZone(name="Strait of Hormuz", lat=26.5, lon=56.3, radius_km=120.0)

# A minimal voyage stand-in (only the labeling fields the detectors read).
_VOYAGE = SimpleNamespace(
    voyage_id="VY-TEST-01",
    imo="9999990",
    vessel_name="TEST VESSEL",
    flag="Panama",
    owner_id="OWN-TEST",
    vessel_type="Container",  # band top 22 kts
)


# ── great_circle_km sanity ───────────────────────────────────────────────────

def test_great_circle_km_known_distance():
    # ~1 degree of latitude is ~111 km.
    d = great_circle_km(26.0, 56.3, 27.0, 56.3)
    assert 108.0 < d < 114.0


def test_great_circle_km_zero_for_same_point():
    assert great_circle_km(10.0, 20.0, 10.0, 20.0) == 0.0


def test_great_circle_km_non_finite_is_zero():
    assert great_circle_km(float("nan"), 0.0, 0.0, 0.0) == 0.0
    assert great_circle_km(0.0, float("inf"), 0.0, 0.0) == 0.0


# ── GAP detection ────────────────────────────────────────────────────────────

def test_gap_inside_high_risk_zone_flags():
    # Two pings straddling the Hormuz centre, 12h apart (> 6h threshold). The
    # midpoint sits inside the zone -> a GAP anomaly.
    track = [
        AisPing(ts=_t(0), lat=26.4, lon=56.2),
        AisPing(ts=_t(12), lat=26.6, lon=56.4),
    ]
    out = detect_gaps(_VOYAGE, high_risk_zones=[_HORMUZ], track=track)
    assert len(out) == 1
    a = out[0]
    assert a.kind == "GAP"
    assert a.zone_name == "Strait of Hormuz"
    assert a.gap_duration_hours == pytest.approx(12.0, abs=0.01)
    assert a.imo == "9999990"
    assert a.provenance == "MODELED"
    assert "MODELED" in a.reason


def test_gap_in_open_ocean_is_suppressed():
    # Same 12h gap, but mid-Pacific — far from any high-risk zone -> none.
    track = [
        AisPing(ts=_t(0), lat=0.0, lon=-150.0),
        AisPing(ts=_t(12), lat=1.0, lon=-150.0),
    ]
    out = detect_gaps(_VOYAGE, high_risk_zones=[_HORMUZ], track=track)
    assert out == []


def test_short_gap_below_threshold_not_flagged():
    # A 3h gap inside the zone is below the 6h floor -> none.
    track = [
        AisPing(ts=_t(0), lat=26.4, lon=56.2),
        AisPing(ts=_t(3), lat=26.6, lon=56.4),
    ]
    out = detect_gaps(_VOYAGE, high_risk_zones=[_HORMUZ], track=track)
    assert out == []


def test_gap_severity_scales_with_duration():
    # >= 3x threshold (18h) -> CRITICAL.
    track = [
        AisPing(ts=_t(0), lat=26.4, lon=56.2),
        AisPing(ts=_t(20), lat=26.6, lon=56.4),
    ]
    out = detect_gaps(_VOYAGE, high_risk_zones=[_HORMUZ], track=track)
    assert out and out[0].severity == "CRITICAL"


# ── TELEPORT detection ───────────────────────────────────────────────────────

def test_teleport_flags_impossible_speed():
    # ~111 km covered in 1h => ~60 kts implied, far above a container's ~22 kts
    # max * 1.8 margin (= 39.6 kts) -> a TELEPORT anomaly.
    p0 = AisPing(ts=_t(0), lat=26.0, lon=56.3)
    p1 = AisPing(ts=_t(1), lat=27.0, lon=56.3)
    out = detect_teleports(_VOYAGE, track=[p0, p1])
    assert len(out) == 1
    a = out[0]
    assert a.kind == "TELEPORT"
    assert a.provenance == "MODELED"
    assert a.max_speed_kts == pytest.approx(22.0, abs=0.01)

    # Verify the implied-speed arithmetic against the great-circle distance:
    # implied_kts = (km / hours) / 1.852.
    dist_km = great_circle_km(26.0, 56.3, 27.0, 56.3)
    expected_kts = (dist_km / 1.0) / 1.852
    assert a.implied_speed_kts == pytest.approx(round(expected_kts, 1), abs=0.1)
    # And it must actually exceed the band-top * margin gate.
    assert a.implied_speed_kts > 22.0 * 1.8


def test_normal_speed_leg_not_flagged():
    # ~18.5 km in 1h => ~10 kts, well within a container ship's band -> none.
    # 0.1667 deg lat ~= 18.5 km.
    p0 = AisPing(ts=_t(0), lat=26.0, lon=56.3)
    p1 = AisPing(ts=_t(1), lat=26.0 + 1.0 / 6.0, lon=56.3)
    out = detect_teleports(_VOYAGE, track=[p0, p1])
    assert out == []


def test_teleport_skips_sub_threshold_leg_duration():
    # A huge jump over a 5-minute leg is below _TELEPORT_MIN_LEG_HOURS (15min)
    # -> skipped (too noisy to trust), no anomaly.
    p0 = AisPing(ts=_t(0), lat=26.0, lon=56.3)
    p1 = AisPing(ts=_t(5.0 / 60.0), lat=30.0, lon=56.3)
    out = detect_teleports(_VOYAGE, track=[p0, p1])
    assert out == []


def test_unknown_vessel_type_uses_generous_default_band():
    v = SimpleNamespace(voyage_id="X", imo="1", vessel_name="Y", vessel_type="UFO")
    p0 = AisPing(ts=_t(0), lat=26.0, lon=56.3)
    p1 = AisPing(ts=_t(1), lat=27.0, lon=56.3)  # ~60 kts implied
    out = detect_teleports(v, track=[p0, p1])
    # 60 kts > default band top (25) * 1.8 (= 45) -> still flagged.
    assert out and out[0].max_speed_kts == pytest.approx(25.0, abs=0.01)


# ── Degenerate / garbage tracks ──────────────────────────────────────────────

def test_single_point_track_is_empty():
    track = [AisPing(ts=_t(0), lat=26.5, lon=56.3)]
    assert detect_gaps(_VOYAGE, high_risk_zones=[_HORMUZ], track=track) == []
    assert detect_teleports(_VOYAGE, track=track) == []


def test_empty_track_is_empty():
    assert detect_gaps(_VOYAGE, high_risk_zones=[_HORMUZ], track=[]) == []
    assert detect_teleports(_VOYAGE, track=[]) == []


def test_coordless_track_does_not_crash():
    bad = [
        SimpleNamespace(ts=_t(0), lat=None, lon=None),
        SimpleNamespace(ts=_t(12), lat=float("nan"), lon=56.3),
    ]
    assert detect_gaps(_VOYAGE, high_risk_zones=[_HORMUZ], track=bad) == []
    assert detect_teleports(_VOYAGE, track=bad) == []


def test_garbage_track_never_raises():
    for junk in (None, [None, 42, object()], "nonsense"):
        # detectors must not raise on garbage tracks.
        assert isinstance(detect_teleports(_VOYAGE, track=junk), list)


# ── assess_voyage combines both ──────────────────────────────────────────────

def test_assess_voyage_combines_gap_and_teleport_on_real_voyage():
    # Use the real synthetic fleet: assess_voyage on each voyage must never
    # raise and must return AisAnomaly objects of the two known kinds only.
    from data.voyage_dataset import build_voyage_fleet

    fleet = build_voyage_fleet(seed=20260606)
    kinds = set()
    for v in fleet:
        out = assess_voyage(v)
        assert isinstance(out, list)
        for a in out:
            assert isinstance(a, AisAnomaly)
            assert a.kind in {"GAP", "TELEPORT"}
            assert a.provenance == "MODELED"
            kinds.add(a.kind)
    # The modeled injection guarantees the fleet surfaces both kinds.
    assert kinds == {"GAP", "TELEPORT"}


def test_assess_voyage_garbage_returns_empty():
    assert assess_voyage(None) == []
    assert assess_voyage(object()) == []
    assert assess_voyage(SimpleNamespace()) == []


# ── scan_fleet aggregation + determinism ─────────────────────────────────────

def test_scan_fleet_aggregates_and_is_severity_sorted():
    from data.voyage_dataset import build_voyage_fleet

    fleet = build_voyage_fleet(seed=20260606)
    anomalies = scan_fleet(fleet)
    assert len(anomalies) > 0
    # Severity-sorted: CRITICAL before HIGH before MEDIUM before LOW.
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    sev_ranks = [order[a.severity] for a in anomalies]
    assert sev_ranks == sorted(sev_ranks)


def test_scan_fleet_is_deterministic():
    from data.voyage_dataset import build_voyage_fleet

    a1 = scan_fleet(build_voyage_fleet(seed=4242))
    a2 = scan_fleet(build_voyage_fleet(seed=4242))
    assert [(a.voyage_id, a.kind, a.severity) for a in a1] == [
        (a.voyage_id, a.kind, a.severity) for a in a2
    ]


def test_scan_fleet_never_raises_on_garbage():
    assert scan_fleet([None, 42, object(), "x"]) == []
    assert scan_fleet([]) == []
    assert scan_fleet(None) == []


# ── alert builder integration (R049 AIS_ANOMALY) ─────────────────────────────

def test_check_ais_anomaly_alerts_emits_modeled_alerts():
    from engine.alert_engine_v2 import check_ais_anomaly_alerts
    from data.voyage_dataset import build_voyage_fleet

    alerts = check_ais_anomaly_alerts(voyages=build_voyage_fleet(seed=20260606), max_alerts=5)
    assert 0 < len(alerts) <= 5
    for a in alerts:
        assert a.alert_type == "AIS_ANOMALY"
        assert "MODELED" in a.title
        assert "ILLUSTRATIVE" in a.body


def test_check_ais_anomaly_alerts_never_raises_on_garbage():
    from engine.alert_engine_v2 import check_ais_anomaly_alerts

    assert check_ais_anomaly_alerts(voyages=[None, 42]) == []
    assert check_ais_anomaly_alerts(voyages=[]) == []
