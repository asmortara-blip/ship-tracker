"""Pure-function tests for processing.port_demand_forecaster.

The forecaster produces 30/60/90-day demand-score projections per port via
signal-based extrapolation. These tests pin:

  * PortDemandForecast dataclass shape;
  * the documented clamp window [0.05, 0.95] on every horizon;
  * confidence in [0, 1] and the documented 0.4 baseline floor;
  * trend-direction classification consistency with the 90-day delta;
  * directional invariants — a positive monthly delta moves the forecast up,
    a negative one moves it down;
  * the 30/60/90 horizon-dampening ordering when the delta is signed;
  * graceful degradation on empty input and ports missing optional sub-scores;
  * ``forecast_all_ports`` sorting by forecast_30d descending;
  * determinism within a session.

Port inputs are duck-typed stubs (the module reads attributes via getattr).
Empty macro/wb dicts keep the global macro adjustment at its neutral default.
No Streamlit, no live feed.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from processing.port_demand_forecaster import (
    PortDemandForecast,
    forecast_all_ports,
    forecast_port_demand,
)


# ── Duck-typed port stub ────────────────────────────────────────────────────


@dataclass
class _PortStub:
    """Minimal stand-in for a PortDemandResult."""
    locode: str
    port_name: str
    demand_score: float
    trade_flow_component: float = 0.5
    congestion_component: float = 0.5


def _neutral_ports() -> list[_PortStub]:
    """Ports with neutral sub-scores → near-flat forecasts."""
    return [
        _PortStub("USLAX", "Los Angeles", 0.60),
        _PortStub("NLRTM", "Rotterdam", 0.45),
    ]


# ── Dataclass shape ─────────────────────────────────────────────────────────


def test_forecast_field_presence() -> None:
    forecasts = forecast_port_demand(_neutral_ports(), {}, {})
    assert len(forecasts) == 2
    for fc in forecasts:
        assert isinstance(fc, PortDemandForecast)
        assert isinstance(fc.port_locode, str) and fc.port_locode
        assert isinstance(fc.port_name, str)
        assert isinstance(fc.current_score, float)
        assert isinstance(fc.forecast_30d, float)
        assert isinstance(fc.forecast_60d, float)
        assert isinstance(fc.forecast_90d, float)
        assert isinstance(fc.confidence, float)
        assert isinstance(fc.key_drivers, list) and len(fc.key_drivers) >= 1
        assert isinstance(fc.seasonal_adjustment, float)
        assert fc.trend_direction in {"Accelerating", "Stable", "Decelerating"}


def test_one_forecast_per_input_port() -> None:
    ports = _neutral_ports()
    assert len(forecast_port_demand(ports, {}, {})) == len(ports)


# ── Clamp window ────────────────────────────────────────────────────────────


def test_forecasts_clamped_to_documented_window() -> None:
    """Every horizon must land within the documented [0.05, 0.95] band.

    Includes ports pinned near both extremes so the clamp is genuinely
    exercised, not just the comfortable mid-range.
    """
    edge_ports = [
        _PortStub("HI", "Near-Ceiling", 0.94, trade_flow_component=0.95,
                  congestion_component=0.10),
        _PortStub("LO", "Near-Floor", 0.06, trade_flow_component=0.05,
                  congestion_component=0.95),
    ] + _neutral_ports()
    for fc in forecast_port_demand(edge_ports, {}, {}):
        for value in (fc.forecast_30d, fc.forecast_60d, fc.forecast_90d):
            assert 0.05 <= value <= 0.95, fc.port_locode


def test_current_score_echoes_input_demand_score() -> None:
    forecasts = forecast_port_demand(_neutral_ports(), {}, {})
    by_locode = {fc.port_locode: fc for fc in forecasts}
    assert by_locode["USLAX"].current_score == pytest.approx(0.60, abs=1e-6)
    assert by_locode["NLRTM"].current_score == pytest.approx(0.45, abs=1e-6)


# ── Confidence ──────────────────────────────────────────────────────────────


def test_confidence_in_unit_interval() -> None:
    for fc in forecast_port_demand(_neutral_ports(), {}, {}):
        assert 0.0 <= fc.confidence <= 1.0, fc.port_locode


def test_confidence_baseline_floor_with_no_data() -> None:
    """With no macro/wb data and neutral congestion, confidence is the 0.4 baseline."""
    for fc in forecast_port_demand(_neutral_ports(), {}, {}):
        assert fc.confidence == pytest.approx(0.4, abs=1e-6)


def test_informative_congestion_lifts_confidence() -> None:
    """A non-default congestion sub-score adds the documented +0.1 credit."""
    informative = [_PortStub("XX", "Informative", 0.5, congestion_component=0.80)]
    fc = forecast_port_demand(informative, {}, {})[0]
    assert fc.confidence == pytest.approx(0.5, abs=1e-6)


# ── Trend-direction classification ──────────────────────────────────────────


def test_trend_direction_consistent_with_90d_delta() -> None:
    """Accelerating ⇔ 90d > current+0.05; Decelerating ⇔ 90d < current-0.05."""
    # Strong positive trade momentum + low congestion → upward push.
    rising = [_PortStub("UP", "Rising", 0.40, trade_flow_component=0.90,
                        congestion_component=0.10)]
    falling = [_PortStub("DN", "Falling", 0.70, trade_flow_component=0.10,
                         congestion_component=0.90)]
    for fc in forecast_port_demand(rising, {}, {}):
        if fc.forecast_90d > fc.current_score + 0.05:
            assert fc.trend_direction == "Accelerating"
        elif fc.forecast_90d < fc.current_score - 0.05:
            assert fc.trend_direction == "Decelerating"
        else:
            assert fc.trend_direction == "Stable"
    for fc in forecast_port_demand(falling, {}, {}):
        if fc.forecast_90d > fc.current_score + 0.05:
            assert fc.trend_direction == "Accelerating"
        elif fc.forecast_90d < fc.current_score - 0.05:
            assert fc.trend_direction == "Decelerating"
        else:
            assert fc.trend_direction == "Stable"


# ── Directional invariants ──────────────────────────────────────────────────


def test_strong_trade_momentum_pushes_forecast_above_current() -> None:
    """Strong trade flow + low congestion → positive monthly delta → 30d > current."""
    bullish = [_PortStub("UP", "Bullish", 0.40, trade_flow_component=0.90,
                         congestion_component=0.10)]
    fc = forecast_port_demand(bullish, {}, {})[0]
    assert fc.forecast_30d > fc.current_score


def test_weak_trade_flow_pushes_forecast_below_current() -> None:
    """Weak trade flow + high congestion → negative monthly delta → 30d < current."""
    bearish = [_PortStub("DN", "Bearish", 0.70, trade_flow_component=0.10,
                         congestion_component=0.90)]
    fc = forecast_port_demand(bearish, {}, {})[0]
    assert fc.forecast_30d < fc.current_score


def test_horizon_dampening_for_signed_delta() -> None:
    """When the monthly delta is positive, longer horizons are progressively damped.

    forecast_30d uses multiplier 30/30 = 1.0; forecast_60d uses (60/30)*0.85 =
    1.70; forecast_90d uses (90/30)*0.70 = 2.10. With a positive delta and no
    clamping in play, 30d < 60d < 90d. With strong trade momentum and a mid
    current score, the clamp ceiling stays clear, so the ordering must hold.
    """
    bullish = [_PortStub("UP", "Bullish", 0.45, trade_flow_component=0.90,
                         congestion_component=0.10)]
    fc = forecast_port_demand(bullish, {}, {})[0]
    assert fc.current_score < fc.forecast_30d < fc.forecast_60d < fc.forecast_90d


def test_neutral_port_forecast_is_near_flat() -> None:
    """Neutral sub-scores → ~zero monthly delta → forecasts hug the current score."""
    fc = forecast_port_demand([_PortStub("N", "Neutral", 0.55)], {}, {})[0]
    for value in (fc.forecast_30d, fc.forecast_60d, fc.forecast_90d):
        assert abs(value - fc.current_score) < 0.05


# ── Graceful degradation ────────────────────────────────────────────────────


def test_empty_port_list_returns_empty() -> None:
    assert forecast_port_demand([], {}, {}) == []
    assert forecast_all_ports([], {}, {}) == []


def test_port_missing_sub_scores_uses_neutral_defaults() -> None:
    """A port lacking trade_flow_component / congestion_component still forecasts."""

    @dataclass
    class _Bare:
        locode: str
        port_name: str
        demand_score: float

    forecasts = forecast_port_demand([_Bare("BARE", "Bare Port", 0.50)], {}, {})
    assert len(forecasts) == 1
    fc = forecasts[0]
    assert 0.05 <= fc.forecast_30d <= 0.95
    # Neutral sub-scores → near-flat forecast.
    assert abs(fc.forecast_30d - fc.current_score) < 0.05


def test_port_with_bad_demand_score_is_skipped_not_crashed() -> None:
    """A port whose demand_score cannot be coerced to float is dropped, not fatal.

    The single bad port yields no forecast; the good port still does.
    """

    @dataclass
    class _Bad:
        locode: str
        port_name: str
        demand_score: object

    mixed = [
        _Bad("BAD", "Bad Port", "not-a-number"),
        _PortStub("GOOD", "Good Port", 0.55),
    ]
    forecasts = forecast_port_demand(mixed, {}, {})
    locodes = {fc.port_locode for fc in forecasts}
    assert "GOOD" in locodes
    assert "BAD" not in locodes


# ── forecast_all_ports ──────────────────────────────────────────────────────


def test_forecast_all_ports_sorted_by_30d_descending() -> None:
    ports = [
        _PortStub("A", "A", 0.30),
        _PortStub("B", "B", 0.80),
        _PortStub("C", "C", 0.55),
    ]
    forecasts = forecast_all_ports(ports, {}, {})
    thirty_day = [fc.forecast_30d for fc in forecasts]
    assert thirty_day == sorted(thirty_day, reverse=True)


# ── Determinism ─────────────────────────────────────────────────────────────


def test_forecast_is_repeatable_for_same_inputs() -> None:
    """The forecaster has no randomness — identical inputs give identical output."""
    ports = _neutral_ports()
    a = forecast_port_demand(ports, {}, {})
    b = forecast_port_demand(ports, {}, {})
    assert [
        (fc.port_locode, fc.forecast_30d, fc.forecast_60d, fc.forecast_90d, fc.confidence)
        for fc in a
    ] == [
        (fc.port_locode, fc.forecast_30d, fc.forecast_60d, fc.forecast_90d, fc.confidence)
        for fc in b
    ]
