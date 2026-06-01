"""Tests for processing.eta_predictor — shipment ETA + delay drivers.

Covers:
  - ShipmentETA dataclass shape
  - _clamp: bounds + identity
  - _get_congestion_for_locode: matches by locode on dicts or objects;
    falls back to 0.5 when not found
  - _origin_delay / _dest_delay: threshold maps; dest is scaled to 0.7×
  - _season_delay: Jul-Sep peak; Feb CNY slowdown; otherwise 0
  - _rate_momentum_delay: requires >= 30 obs; +0.5 when 30d rate change > 20%
  - _current_rate / _projected_rate_in_2w: returns 0 on missing / empty;
    projects forward via linear trend
  - _congestion_risk_label: SEVERE / HIGH / MODERATE / LOW
  - _compute_confidence: base 0.70 + 0.10 per data source; volatility penalty
  - _build_delay_drivers: surfaces a driver for each active factor;
    'no significant delay' when nothing fires
  - predict_eta: unknown route → fallback (UNKNOWN locodes, 14 nominal days);
    known route → full ShipmentETA with valid fields
  - predict_all_routes: returns one per ROUTES entry, sorted by delay desc
  - get_best_departure_windows: only routes with positive savings,
    capped at 3, sorted desc by savings
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import pytest

from processing.eta_predictor import (
    ShipmentETA,
    _build_delay_drivers,
    _clamp,
    _compute_confidence,
    _congestion_risk_label,
    _current_rate,
    _dest_delay,
    _get_congestion_for_locode,
    _origin_delay,
    _projected_rate_in_2w,
    _rate_momentum_delay,
    _season_delay,
    get_best_departure_windows,
    predict_all_routes,
    predict_eta,
)


@dataclass
class _FakePort:
    locode: str
    congestion_index: float = 0.5


def _rate_df(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(values), freq="D"),
        "rate_usd_per_feu": values,
    })


# ─── Dataclass + _clamp ────────────────────────────────────────────────────

def test_shipment_eta_shape() -> None:
    eta = ShipmentETA(
        route_id="r", origin_port="CNSHA", dest_port="USLAX",
        nominal_transit_days=14, predicted_delay_days=2.0, total_eta_days=16.0,
        confidence=0.85, delay_drivers=["A"], optimal_departure_week="Week of Apr 1",
        rate_at_optimal=2000.0, congestion_risk="MODERATE", cost_savings_vs_now=100.0,
    )
    assert eta.total_eta_days == 16.0


def test_clamp_within_bounds() -> None:
    assert _clamp(0.5) == 0.5
    assert _clamp(-0.5) == 0.0
    assert _clamp(1.5) == 1.0


def test_clamp_custom_bounds() -> None:
    assert _clamp(50.0, lo=10, hi=100) == 50.0
    assert _clamp(5.0, lo=10, hi=100) == 10.0


# ─── _get_congestion_for_locode ────────────────────────────────────────────

def test_get_congestion_for_locode_finds_dict_entry() -> None:
    pr = [{"locode": "USLAX", "congestion_index": 0.85}]
    assert _get_congestion_for_locode(pr, "USLAX") == 0.85


def test_get_congestion_for_locode_uses_port_locode_alias() -> None:
    pr = [{"port_locode": "USLAX", "congestion_index": 0.85}]
    assert _get_congestion_for_locode(pr, "USLAX") == 0.85


def test_get_congestion_for_locode_finds_object_entry() -> None:
    assert _get_congestion_for_locode([_FakePort("USLAX", 0.90)], "USLAX") == 0.90


def test_get_congestion_for_locode_returns_half_when_missing() -> None:
    assert _get_congestion_for_locode([], "USLAX") == 0.5


# ─── _origin_delay / _dest_delay ───────────────────────────────────────────

def test_origin_delay_severe_at_85pct() -> None:
    assert _origin_delay(0.90) == 3.0


def test_origin_delay_elevated_at_70pct() -> None:
    assert _origin_delay(0.75) == 1.5


def test_origin_delay_zero_when_low() -> None:
    assert _origin_delay(0.50) == 0.0


def test_dest_delay_scaled_to_0_7x() -> None:
    """Dest delay is 70% of origin delay at same congestion."""
    assert _dest_delay(0.90) == pytest.approx(3.0 * 0.7)
    assert _dest_delay(0.75) == pytest.approx(1.5 * 0.7)
    assert _dest_delay(0.50) == 0.0


# ─── _season_delay ─────────────────────────────────────────────────────────

def test_season_delay_peak_july_through_september() -> None:
    assert _season_delay(date(2024, 7, 15)) == 0.5
    assert _season_delay(date(2024, 8, 15)) == 0.5
    assert _season_delay(date(2024, 9, 15)) == 0.5


def test_season_delay_cny_february() -> None:
    assert _season_delay(date(2024, 2, 15)) == 1.0


def test_season_delay_zero_in_off_season() -> None:
    assert _season_delay(date(2024, 4, 15)) == 0.0


def test_season_delay_default_uses_today() -> None:
    out = _season_delay()
    assert out in {0.0, 0.5, 1.0}


# ─── _rate_momentum_delay ──────────────────────────────────────────────────

def test_rate_momentum_delay_zero_when_no_data() -> None:
    assert _rate_momentum_delay("r", {}) == 0.0


def test_rate_momentum_delay_zero_when_under_30_obs() -> None:
    assert _rate_momentum_delay("r", {"r": _rate_df([1000.0] * 20)}) == 0.0


def test_rate_momentum_delay_fires_on_big_30d_rise() -> None:
    """+25% over 30d → returns +0.5."""
    rates = [1000.0] + [1010.0] * 28 + [1250.0]   # +25%
    assert _rate_momentum_delay("r", {"r": _rate_df(rates)}) == 0.5


def test_rate_momentum_delay_zero_on_modest_rise() -> None:
    rates = [1000.0] + [1010.0] * 28 + [1100.0]   # +10%
    assert _rate_momentum_delay("r", {"r": _rate_df(rates)}) == 0.0


# ─── _current_rate / _projected_rate_in_2w ─────────────────────────────────

def test_current_rate_returns_last_value() -> None:
    assert _current_rate("r", {"r": _rate_df([1000.0, 1100.0, 1200.0])}) == 1200.0


def test_current_rate_zero_on_missing() -> None:
    assert _current_rate("r", {}) == 0.0


def test_projected_rate_in_2w_extrapolates_trend() -> None:
    """Steady linear rise → projection is greater than current."""
    rates = [1000.0 + i * 10 for i in range(30)]
    proj = _projected_rate_in_2w("r", {"r": _rate_df(rates)})
    assert proj > rates[-1]


def test_projected_rate_in_2w_returns_zero_on_short() -> None:
    assert _projected_rate_in_2w("r", {"r": _rate_df([1000.0] * 5)}) == 0.0


# ─── _congestion_risk_label ────────────────────────────────────────────────

def test_congestion_risk_label_severe_when_combined_above_0_80() -> None:
    assert _congestion_risk_label(0.95, 0.95) == "SEVERE"


def test_congestion_risk_label_high_above_0_65() -> None:
    assert _congestion_risk_label(0.75, 0.70) == "HIGH"


def test_congestion_risk_label_moderate_above_0_45() -> None:
    assert _congestion_risk_label(0.55, 0.50) == "MODERATE"


def test_congestion_risk_label_low_when_both_low() -> None:
    assert _congestion_risk_label(0.30, 0.30) == "LOW"


# ─── _compute_confidence ───────────────────────────────────────────────────

def test_compute_confidence_base_no_data() -> None:
    """Base 0.70 with no data sources."""
    assert _compute_confidence(0.5, 0.5, False, False) == 0.70


def test_compute_confidence_boosted_by_data_availability() -> None:
    assert _compute_confidence(0.5, 0.5, True, False) == pytest.approx(0.80)
    assert _compute_confidence(0.5, 0.5, True, True) == pytest.approx(0.90)


def test_compute_confidence_penalized_at_high_congestion() -> None:
    """When (origin+dest)/2 > 0.70, penalty kicks in."""
    high = _compute_confidence(0.90, 0.90, True, True)
    low = _compute_confidence(0.50, 0.50, True, True)
    assert high < low


def test_compute_confidence_in_unit_interval() -> None:
    assert 0.0 <= _compute_confidence(0.99, 0.99, True, True) <= 1.0


# ─── _build_delay_drivers ─────────────────────────────────────────────────

def test_build_delay_drivers_says_no_delay_when_all_zero() -> None:
    drivers = _build_delay_drivers(0.3, 0.3, 0.0, 0.0, "CNSHA", "USLAX")
    assert len(drivers) == 1
    assert "No significant" in drivers[0]


def test_build_delay_drivers_surfaces_origin_severe() -> None:
    drivers = _build_delay_drivers(0.90, 0.30, 0.0, 0.0, "CNSHA", "USLAX")
    assert any("Severe origin port" in d for d in drivers)
    assert any("CNSHA" in d for d in drivers)


def test_build_delay_drivers_surfaces_cny_when_season_delay_1() -> None:
    drivers = _build_delay_drivers(0.3, 0.3, 1.0, 0.0, "CNSHA", "USLAX")
    assert any("Chinese New Year" in d for d in drivers)


def test_build_delay_drivers_surfaces_peak_when_season_delay_half() -> None:
    drivers = _build_delay_drivers(0.3, 0.3, 0.5, 0.0, "CNSHA", "USLAX")
    assert any("Peak season" in d for d in drivers)


def test_build_delay_drivers_surfaces_rate_momentum() -> None:
    drivers = _build_delay_drivers(0.3, 0.3, 0.0, 0.5, "CNSHA", "USLAX")
    assert any("Rate momentum" in d for d in drivers)


# ─── predict_eta ───────────────────────────────────────────────────────────

def test_predict_eta_unknown_route_uses_fallback() -> None:
    eta = predict_eta("totally_made_up_route", [], {}, {})
    assert eta.origin_port == "UNKNOWN"
    assert eta.dest_port == "UNKNOWN"
    assert eta.nominal_transit_days == 14


def test_predict_eta_known_route_returns_well_formed_dataclass() -> None:
    from routes.route_registry import ROUTES
    route = ROUTES[0]
    eta = predict_eta(route.id, [], {}, {})
    assert isinstance(eta, ShipmentETA)
    assert eta.route_id == route.id
    assert eta.origin_port == route.origin_locode
    assert eta.dest_port == route.dest_locode
    assert eta.nominal_transit_days == route.transit_days
    assert 0.0 <= eta.confidence <= 1.0
    assert eta.congestion_risk in {"LOW", "MODERATE", "HIGH", "SEVERE"}
    assert eta.delay_drivers


def test_predict_eta_total_eta_is_nominal_plus_delay() -> None:
    from routes.route_registry import ROUTES
    eta = predict_eta(ROUTES[0].id, [], {}, {})
    assert eta.total_eta_days == pytest.approx(
        eta.nominal_transit_days + eta.predicted_delay_days, abs=0.01,
    )


def test_predict_eta_congestion_drives_delay() -> None:
    """With severe congestion at the origin port, predicted delay rises."""
    from routes.route_registry import ROUTES
    route = ROUTES[0]
    high_pr = [_FakePort(route.origin_locode, congestion_index=0.95)]
    no_pr: list = []
    eta_high = predict_eta(route.id, high_pr, {}, {})
    eta_none = predict_eta(route.id, no_pr, {}, {})
    assert eta_high.predicted_delay_days > eta_none.predicted_delay_days


# ─── predict_all_routes ────────────────────────────────────────────────────

def test_predict_all_routes_returns_one_per_route_sorted_desc() -> None:
    from routes.route_registry import ROUTES
    out = predict_all_routes([], {}, {})
    assert len(out) == len(ROUTES)
    delays = [e.predicted_delay_days for e in out]
    assert delays == sorted(delays, reverse=True)


# ─── get_best_departure_windows ────────────────────────────────────────────

def _mk_eta(route_id: str, savings: float) -> ShipmentETA:
    return ShipmentETA(
        route_id=route_id, origin_port="o", dest_port="d",
        nominal_transit_days=14, predicted_delay_days=0.0, total_eta_days=14.0,
        confidence=0.8, delay_drivers=[], optimal_departure_week="w",
        rate_at_optimal=1000.0, congestion_risk="LOW",
        cost_savings_vs_now=savings,
    )


def test_get_best_departure_windows_only_positive_savings() -> None:
    out = get_best_departure_windows([
        _mk_eta("a", -50.0), _mk_eta("b", 0.0), _mk_eta("c", 100.0),
    ])
    assert {w["route_id"] for w in out} == {"c"}


def test_get_best_departure_windows_capped_at_3() -> None:
    etas = [_mk_eta(f"r{i}", float(i + 1)) for i in range(10)]
    out = get_best_departure_windows(etas)
    assert len(out) == 3


def test_get_best_departure_windows_sorted_desc_by_savings() -> None:
    etas = [_mk_eta("a", 10.0), _mk_eta("b", 50.0), _mk_eta("c", 30.0)]
    out = get_best_departure_windows(etas)
    assert [w["route_id"] for w in out] == ["b", "c", "a"]


def test_get_best_departure_windows_empty_when_no_positive() -> None:
    out = get_best_departure_windows([_mk_eta("a", -5.0)])
    assert out == []
