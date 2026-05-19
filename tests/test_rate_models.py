"""Tests for the rate-forecasting models — Monte Carlo and the linear forecaster.

The design uses known-property synthetic fixtures:

  * ``monte_carlo.simulate_freight_rates`` runs an Ornstein-Uhlenbeck
    mean-reverting process — a rate starting *far above* its long-run level
    must be pulled back *down*, and one starting *far below* must be pulled
    *up*. A pure random walk would do neither.
  * ``forecaster.forecast_all_routes`` widens its confidence band with
    horizon — the 90-day band must be strictly wider than the 30-day band.

No Streamlit, no live feed.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from processing.forecaster import RateForecast, forecast_all_routes
from processing.monte_carlo import MonteCarloResult, simulate_freight_rates


RNG = np.random.default_rng(20260519)


def _oscillating_history(n: int, equilibrium: float, last_rate: float) -> pd.DataFrame:
    """A rate series oscillating around ``equilibrium`` with a forced final value.

    The trailing mean/median sit near ``equilibrium``; the final observation is
    overridden to ``last_rate`` so the simulation's starting point can be placed
    deliberately above or below the long-run level.
    """
    base = date.today() - timedelta(days=n)
    dates = pd.to_datetime([base + timedelta(days=i) for i in range(n)])
    rates = equilibrium + 0.08 * equilibrium * np.sin(np.linspace(0, 6, n))
    rates = rates + RNG.normal(0, 0.02 * equilibrium, size=n)
    rates[-1] = last_rate
    return pd.DataFrame({"date": dates, "rate_usd_per_feu": rates})


# ── Monte Carlo: percentile ordering ────────────────────────────────────────


def test_monte_carlo_percentiles_strictly_ordered() -> None:
    """P5 < P25 < P50 < P75 < P95 must hold at every simulated day."""
    fd = {"r": _oscillating_history(180, equilibrium=2000.0, last_rate=2000.0)}
    res = simulate_freight_rates(fd, "r", n_simulations=1500, forecast_days=90)
    assert isinstance(res, MonteCarloResult)
    p = res.percentiles
    for t in range(res.forecast_days):
        assert p["p5"][t] < p["p25"][t] < p["p50"][t] < p["p75"][t] < p["p95"][t]


# ── Monte Carlo: Ornstein-Uhlenbeck mean reversion ──────────────────────────


def test_monte_carlo_reverts_a_high_starting_rate_downward() -> None:
    """A rate starting far ABOVE equilibrium is pulled back down by the OU drift."""
    fd = {"r": _oscillating_history(180, equilibrium=2000.0, last_rate=3600.0)}
    res = simulate_freight_rates(fd, "r", n_simulations=2000, forecast_days=90)
    assert res is not None
    # The estimated long-run level sits below the spiked current rate ...
    assert res.long_run_rate < res.current_rate
    # ... and the median 90-day path reverts toward it (well below current).
    assert res.expected_rate_90d < res.current_rate


def test_monte_carlo_reverts_a_low_starting_rate_upward() -> None:
    """A rate starting far BELOW equilibrium is pulled back up by the OU drift."""
    fd = {"r": _oscillating_history(180, equilibrium=2000.0, last_rate=900.0)}
    res = simulate_freight_rates(fd, "r", n_simulations=2000, forecast_days=90)
    assert res is not None
    assert res.long_run_rate > res.current_rate
    assert res.expected_rate_90d > res.current_rate


def test_monte_carlo_exposes_process_metadata() -> None:
    """The result reports the OU process and its estimated parameters."""
    fd = {"r": _oscillating_history(180, equilibrium=2000.0, last_rate=2000.0)}
    res = simulate_freight_rates(fd, "r", n_simulations=500, forecast_days=90)
    assert res is not None
    assert res.process == "ornstein_uhlenbeck_jump"
    assert res.reversion_speed > 0.0
    assert res.long_run_rate > 0.0
    assert res.daily_volatility > 0.0


# ── Forecaster: horizon-dependent uncertainty ───────────────────────────────


def _trending_freight_data() -> dict[str, pd.DataFrame]:
    """Freight history for every registry route — a gentle, noisy uptrend."""
    from routes.route_registry import ROUTES

    n = 120
    base = date.today() - timedelta(days=n)
    dates = pd.to_datetime([base + timedelta(days=i) for i in range(n)])
    out: dict[str, pd.DataFrame] = {}
    for route in ROUTES:
        rates = 2500.0 + 3.0 * np.arange(n) + RNG.normal(0, 30, size=n)
        out[route.id] = pd.DataFrame(
            {"date": dates, "rate_usd_per_feu": rates, "source": "fixture"}
        )
    return out


def test_forecaster_produces_30_60_90_day_numbers() -> None:
    """Every forecast carries finite, positive 30/60/90-day rates."""
    forecasts = forecast_all_routes(_trending_freight_data())
    assert len(forecasts) > 0
    for fc in forecasts:
        assert isinstance(fc, RateForecast)
        for value in (fc.forecast_30d, fc.forecast_60d, fc.forecast_90d):
            assert isinstance(value, float)
            assert np.isfinite(value)
            assert value > 0.0


def test_forecaster_90d_band_is_wider_than_30d_band() -> None:
    """Forecast uncertainty grows with horizon: the 90d band > the 30d band."""
    forecasts = forecast_all_routes(_trending_freight_data())
    assert len(forecasts) > 0
    for fc in forecasts:
        band_30 = fc.upper_30d - fc.lower_30d
        band_90 = fc.upper_90d - fc.lower_90d
        assert band_90 > band_30, fc.route_id


def test_forecaster_respects_hard_sanity_caps() -> None:
    """Forecasts stay within [0.30x, 3.0x] of the current rate."""
    forecasts = forecast_all_routes(_trending_freight_data())
    assert len(forecasts) > 0
    for fc in forecasts:
        for value in (fc.forecast_30d, fc.forecast_60d, fc.forecast_90d):
            assert fc.current_rate * 0.30 <= value <= fc.current_rate * 3.0


def test_forecaster_surfaced_30d_seasonal_factor_matches_seasonal_module() -> None:
    """The forecast's 30d seasonal factor equals 1 + the seasonal-module adjustment.

    This pins the deseasonalize → re-apply contract: the factor stamped onto
    the forecast for the 30-day horizon must be exactly the calendar-aware
    factor that ``processing.seasonal`` reports for that date — seasonality is
    genuinely re-applied, not dropped or hard-coded to neutral.
    """
    from processing.seasonal import get_seasonal_adjustment

    freight = _trending_freight_data()
    forecasts = forecast_all_routes(freight)
    tpeb = next((f for f in forecasts if f.route_id == "transpacific_eb"), None)
    assert tpeb is not None

    # The forecaster launches the 30d horizon from the last observation date.
    last_obs = pd.to_datetime(freight["transpacific_eb"]["date"]).dt.date.max()
    expected_factor = 1.0 + get_seasonal_adjustment(
        "transpacific_eb", last_obs + timedelta(days=30)
    )
    assert tpeb.seasonal_factor_30d == pytest.approx(expected_factor, abs=1e-9)
