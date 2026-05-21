"""Tests for processing.forecaster — seasonally-adjusted rate forecaster.

Covers:
  - RateForecast dataclass shape & default 90d band fields
  - _seasonal_factor: override path passes through; clamped to [-0.15, +0.15];
    no override falls through to processing.seasonal (or 1.0 on failure)
  - _forecast_route:
      * returns None when fewer than 3 valid obs
      * returns None when every row is source='fallback'
      * normal series → RateForecast with band fields, slope, R², confidence,
        and the seasonal_factor_30d we set via override
      * 90d band wider than 30d band (sqrt-horizon widening)
      * forecast clamped to [floor_mult, ceil_mult] × current_rate
      * methodology string always references confidence/sample size
  - forecast_all_routes: iterates the real ROUTES catalog, returns a list
    sorted by |forecast_30d - current_rate| descending
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from processing.forecaster import (
    _FORECAST_CEIL_MULT,
    _FORECAST_FLOOR_MULT,
    RateForecast,
    _forecast_route,
    _seasonal_factor,
    forecast_all_routes,
)


def _hist_df(rates: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(rates), freq="D"),
        "rate_usd_per_feu": rates,
    })


# ─── RateForecast dataclass ─────────────────────────────────────────────────

def test_rate_forecast_shape_with_defaults() -> None:
    f = RateForecast(
        route_id="r", route_name="R", current_rate=2000.0,
        forecast_30d=2100.0, forecast_60d=2200.0, forecast_90d=2300.0,
        trend_slope=3.0, r_squared=0.5, confidence="Medium",
        methodology="m", upper_30d=2200.0, lower_30d=2000.0, data_points=60,
    )
    # Defaults
    assert f.upper_90d == 0.0
    assert f.lower_90d == 0.0
    assert f.seasonal_factor_30d == 1.0


# ─── _seasonal_factor ───────────────────────────────────────────────────────

def test_seasonal_factor_override_passes_through() -> None:
    # Override 0.10 → factor = 1.10
    assert _seasonal_factor("r", date(2024, 6, 1), 0.10) == pytest.approx(1.10)


def test_seasonal_factor_override_clamped_above() -> None:
    # Override 0.50 → clamped to 0.15 → factor = 1.15
    assert _seasonal_factor("r", date(2024, 6, 1), 0.50) == pytest.approx(1.15)


def test_seasonal_factor_override_clamped_below() -> None:
    # Override -0.50 → clamped to -0.15 → factor = 0.85
    assert _seasonal_factor("r", date(2024, 6, 1), -0.50) == pytest.approx(0.85)


def test_seasonal_factor_no_override_returns_finite_factor() -> None:
    """Without an override, the function asks processing.seasonal — we just
    confirm it returns a finite, clamped factor and never raises."""
    f = _seasonal_factor("transpacific_eb", date(2024, 9, 15), None)
    assert 0.85 <= f <= 1.15


# ─── _forecast_route — input guards ─────────────────────────────────────────

def test_forecast_route_returns_none_when_too_few_obs() -> None:
    """< 3 positive-rate rows → None."""
    df = _hist_df([1000.0, 0.0])  # 0 gets filtered → 1 obs
    assert _forecast_route("r", "R", df) is None


def test_forecast_route_returns_none_when_all_rows_are_fallback() -> None:
    df = _hist_df([1000.0, 1100.0, 1200.0])
    df["source"] = "fallback"
    assert _forecast_route("r", "R", df) is None


def test_forecast_route_returns_forecast_when_source_is_real_data() -> None:
    df = _hist_df([1000.0, 1100.0, 1200.0])
    df["source"] = "live"
    assert _forecast_route("r", "R", df) is not None


# ─── _forecast_route — happy path ───────────────────────────────────────────

def test_forecast_route_returns_well_formed_dataclass() -> None:
    """Slight sinusoidal noise so residual std > 0 — otherwise the bands
    collapse to zero width which would obscure the upper > lower invariant."""
    rates = [1000.0 + i * 10 + 20 * np.sin(i * 0.4) for i in range(60)]
    df = _hist_df(rates)
    f = _forecast_route("r", "R", df, seasonal_override=0.0)
    assert f is not None
    assert isinstance(f, RateForecast)
    assert f.route_id == "r"
    assert f.route_name == "R"
    assert f.data_points == 60
    assert 0.0 <= f.r_squared <= 1.0
    assert f.confidence in {"High", "Medium", "Low"}
    assert f.methodology  # non-empty
    assert f.lower_30d >= 0.0
    assert f.upper_30d > f.lower_30d


def test_forecast_route_seasonal_override_propagated() -> None:
    """Override 0.10 → seasonal_factor_30d ≈ 1.10."""
    df = _hist_df([1000.0] * 30)
    f = _forecast_route("r", "R", df, seasonal_override=0.10)
    assert f is not None
    assert f.seasonal_factor_30d == pytest.approx(1.10)


def test_forecast_route_90d_band_wider_than_30d() -> None:
    """sqrt(90/30) = √3 ≈ 1.73× wider — by construction."""
    df = _hist_df([1000.0 + np.sin(i * 0.3) * 50 for i in range(60)])
    f = _forecast_route("r", "R", df, seasonal_override=0.0)
    assert f is not None
    band_30 = f.upper_30d - f.lower_30d
    band_90 = f.upper_90d - f.lower_90d
    assert band_90 > band_30


def test_forecast_route_forecast_clamped_to_floor_mult() -> None:
    """A precipitous trend would project negative — must floor at
    current_rate * _FORECAST_FLOOR_MULT."""
    # Long history dropping by 50/day → 30 days = -1500 → negative without cap
    rates = [3000.0 - i * 50 for i in range(50)]
    df = _hist_df(rates)
    f = _forecast_route("r", "R", df, seasonal_override=0.0)
    assert f is not None
    assert f.forecast_30d >= f.current_rate * _FORECAST_FLOOR_MULT


def test_forecast_route_forecast_clamped_to_ceil_mult() -> None:
    """A vertical trend must cap at current_rate * _FORECAST_CEIL_MULT."""
    rates = [100.0 + i * 200 for i in range(50)]
    df = _hist_df(rates)
    f = _forecast_route("r", "R", df, seasonal_override=0.0)
    assert f is not None
    assert f.forecast_30d <= f.current_rate * _FORECAST_CEIL_MULT


def test_forecast_route_methodology_mentions_data_points_when_high_confidence() -> None:
    """≥ 30 obs + R² ≥ 0.60 → 'High' confidence → methodology mentions the count."""
    rates = [1000.0 + i * 10 for i in range(50)]  # near-perfect linear
    df = _hist_df(rates)
    f = _forecast_route("r", "R", df, seasonal_override=0.0)
    assert f is not None
    if f.confidence == "High":
        assert str(f.data_points) in f.methodology


def test_forecast_route_returns_none_when_date_column_missing() -> None:
    """sort_values("date") runs unconditionally before the column-existence
    check, so a missing date column gets caught by the top-level try/except
    and the function returns None rather than raising."""
    df = pd.DataFrame({"rate_usd_per_feu": [1000.0 + i for i in range(40)]})
    f = _forecast_route("r", "R", df, seasonal_override=0.0)
    assert f is None


def test_forecast_route_filters_non_positive_rates() -> None:
    """Rows with rate <= 0 are dropped before fitting."""
    rates = [1000.0, -100.0, 0.0, 1100.0, 1200.0, 1300.0]
    df = _hist_df(rates)
    f = _forecast_route("r", "R", df, seasonal_override=0.0)
    assert f is not None
    # 4 positive rows survive
    assert f.data_points == 4


# ─── forecast_all_routes ─────────────────────────────────────────────────

def test_forecast_all_routes_returns_empty_when_no_freight_data() -> None:
    out = forecast_all_routes({})
    assert out == []


def test_forecast_all_routes_returns_list_of_rate_forecasts() -> None:
    from routes.route_registry import ROUTES
    freight = {
        r.id: _hist_df([1000.0 + i * 5 for i in range(40)]) for r in ROUTES[:3]
    }
    out = forecast_all_routes(freight)
    assert len(out) == 3
    for f in out:
        assert isinstance(f, RateForecast)


def test_forecast_all_routes_sorted_by_expected_move_desc() -> None:
    """The route with the largest |forecast - current| comes first."""
    from routes.route_registry import ROUTES
    # Build two routes: one with a strong trend, one nearly flat.
    big_move = [1000.0 + i * 50 for i in range(40)]  # steep slope
    flat = [1000.0] * 40
    freight = {ROUTES[0].id: _hist_df(big_move), ROUTES[1].id: _hist_df(flat)}
    out = forecast_all_routes(freight, seasonal_adjustments={r.id: 0.0 for r in ROUTES[:2]})
    assert len(out) == 2
    moves = [abs(f.forecast_30d - f.current_rate) for f in out]
    assert moves == sorted(moves, reverse=True)


def test_forecast_all_routes_skips_routes_with_under_5_obs() -> None:
    from routes.route_registry import ROUTES
    freight = {
        ROUTES[0].id: _hist_df([1000.0] * 3),       # too few → skipped
        ROUTES[1].id: _hist_df([1000.0] * 40),      # included
    }
    out = forecast_all_routes(freight)
    assert len(out) == 1
    assert out[0].route_id == ROUTES[1].id


def test_forecast_all_routes_uses_seasonal_overrides_when_supplied() -> None:
    """When a seasonal override is given for a route, the resulting forecast's
    seasonal_factor_30d matches 1 + clamped(override)."""
    from routes.route_registry import ROUTES
    target = ROUTES[0]
    freight = {target.id: _hist_df([1000.0] * 40)}
    out = forecast_all_routes(freight, seasonal_adjustments={target.id: 0.08})
    assert out
    f = next(x for x in out if x.route_id == target.id)
    assert f.seasonal_factor_30d == pytest.approx(1.08)
