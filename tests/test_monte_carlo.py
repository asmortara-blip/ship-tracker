"""Tests for processing.monte_carlo — freight-rate path simulation."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.monte_carlo import (
    MonteCarloResult,
    get_highest_upside_routes,
    get_risk_adjusted_opportunity,
    simulate_all_routes,
    simulate_freight_rates,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _rate_df(start: float, end: float, n: int = 180, seed: int = 11) -> pd.DataFrame:
    """A trending freight-rate series for MC parameter estimation."""
    rng = np.random.default_rng(seed)
    trend = np.linspace(start, end, n)
    noise = rng.normal(0, start * 0.02, n)
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="D"),
        "rate_usd_per_feu": np.clip(trend + noise, 1.0, None),
    })


# ─── MonteCarloResult dataclass ────────────────────────────────────────────

def test_monte_carlo_result_shape() -> None:
    r = MonteCarloResult(
        route_id="r", n_simulations=100, forecast_days=90,
        current_rate=2000.0,
        simulated_paths=[[2000.0, 2010.0]],
        percentiles={"p50": [2000.0, 2010.0]},
        prob_rate_increase=0.6, prob_rate_decrease=0.4,
        var_95=-0.10, expected_rate_90d=2050.0,
        bull_case_90d=2200.0, bear_case_90d=1900.0,
        confidence_interval_90d=(1850.0, 2250.0),
    )
    assert r.n_simulations == 100
    assert r.expected_rate_90d == 2050.0


# ─── simulate_freight_rates — empty / insufficient inputs ──────────────────

def test_simulate_missing_route_returns_none() -> None:
    assert simulate_freight_rates({}, "transpacific_eb") is None


def test_simulate_empty_df_returns_none() -> None:
    assert simulate_freight_rates({"r": pd.DataFrame()}, "r") is None


def test_simulate_missing_rate_column_returns_none() -> None:
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=20, freq="D")})
    assert simulate_freight_rates({"r": df}, "r") is None


def test_simulate_insufficient_history_returns_none() -> None:
    """< 10 rows of rate data → None."""
    df = _rate_df(2000.0, 2100.0, n=5)
    assert simulate_freight_rates({"r": df}, "r") is None


def test_simulate_zero_current_rate_returns_none() -> None:
    """Current rate ≤ 0 → None (can't simulate log-space process)."""
    df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=20, freq="D"),
        "rate_usd_per_feu": [0.0] * 20,
    })
    assert simulate_freight_rates({"r": df}, "r") is None


# ─── simulate_freight_rates — successful run ───────────────────────────────

def test_simulate_produces_well_formed_result() -> None:
    df = _rate_df(2000.0, 2200.0, n=120)
    out = simulate_freight_rates({"r1": df}, "r1",
                                  n_simulations=50, forecast_days=60)
    assert isinstance(out, MonteCarloResult)
    assert out.route_id == "r1"
    assert out.n_simulations == 50
    assert out.forecast_days == 60
    assert out.current_rate == pytest.approx(float(df["rate_usd_per_feu"].iloc[-1]))
    # Path matrix shape: n_sims × forecast_days
    assert len(out.simulated_paths) == 50
    assert len(out.simulated_paths[0]) == 60


def test_simulate_percentile_keys_present() -> None:
    df = _rate_df(2000.0, 2100.0, n=120)
    out = simulate_freight_rates({"r": df}, "r",
                                  n_simulations=30, forecast_days=30)
    assert out is not None
    for k in ("p5", "p25", "p50", "p75", "p95"):
        assert k in out.percentiles
        assert len(out.percentiles[k]) == 30


def test_simulate_percentiles_ordered() -> None:
    """At every horizon, p5 ≤ p25 ≤ p50 ≤ p75 ≤ p95."""
    df = _rate_df(2000.0, 2050.0, n=150)
    out = simulate_freight_rates({"r": df}, "r",
                                  n_simulations=100, forecast_days=30)
    assert out is not None
    for t in range(30):
        p = [out.percentiles[k][t] for k in ("p5", "p25", "p50", "p75", "p95")]
        assert p == sorted(p)


def test_simulate_probabilities_sum_to_one() -> None:
    df = _rate_df(2000.0, 2100.0, n=120)
    out = simulate_freight_rates({"r": df}, "r",
                                  n_simulations=100, forecast_days=30)
    assert out is not None
    assert out.prob_rate_increase + out.prob_rate_decrease == pytest.approx(1.0, abs=1e-6)
    assert 0.0 <= out.prob_rate_increase <= 1.0


def test_simulate_var_95_is_non_negative_loss() -> None:
    """VaR-95 is reported as a POSITIVE dollar-loss amount
    (max(0, current − 5th-percentile final rate)). Always ≥ 0; > 0 when the
    downside scenario falls below current."""
    df = _rate_df(2000.0, 2100.0, n=120)
    out = simulate_freight_rates({"r": df}, "r",
                                  n_simulations=100, forecast_days=60)
    assert out is not None
    assert out.var_95 >= 0.0


def test_simulate_bull_bear_bracketing() -> None:
    """bull_case_90d ≥ expected_rate_90d ≥ bear_case_90d."""
    df = _rate_df(2000.0, 2200.0, n=150)
    out = simulate_freight_rates({"r": df}, "r",
                                  n_simulations=100, forecast_days=90)
    assert out is not None
    assert out.bull_case_90d >= out.expected_rate_90d
    assert out.expected_rate_90d >= out.bear_case_90d


def test_simulate_volatility_override_changes_paths() -> None:
    """A higher volatility_override should produce a wider confidence band."""
    df = _rate_df(2000.0, 2050.0, n=120)
    low_vol = simulate_freight_rates({"r": df}, "r",
                                      n_simulations=100, forecast_days=60,
                                      volatility_override=0.10)
    high_vol = simulate_freight_rates({"r": df}, "r",
                                       n_simulations=100, forecast_days=60,
                                       volatility_override=1.50)
    assert low_vol is not None and high_vol is not None
    low_band = low_vol.confidence_interval_90d[1] - low_vol.confidence_interval_90d[0]
    high_band = high_vol.confidence_interval_90d[1] - high_vol.confidence_interval_90d[0]
    assert high_band > low_band


# ─── simulate_all_routes ───────────────────────────────────────────────────

def test_simulate_all_routes_empty_input() -> None:
    assert simulate_all_routes({}) == {}


def test_simulate_all_routes_runs_per_route() -> None:
    freight = {
        "r1": _rate_df(2000.0, 2100.0, seed=21),
        "r2": _rate_df(1500.0, 1700.0, seed=22),
    }
    out = simulate_all_routes(freight, n_simulations=30)
    assert set(out.keys()) == {"r1", "r2"}
    for r in out.values():
        assert isinstance(r, MonteCarloResult)


def test_simulate_all_routes_skips_failures() -> None:
    """A bad route (insufficient data) is silently skipped."""
    freight = {
        "good": _rate_df(2000.0, 2100.0, seed=31),
        "bad": pd.DataFrame({"date": [], "rate_usd_per_feu": []}),
    }
    out = simulate_all_routes(freight, n_simulations=30)
    assert "good" in out
    assert "bad" not in out


# ─── get_highest_upside_routes ─────────────────────────────────────────────

def _mk_mc_result(route_id: str, current: float, bull: float) -> MonteCarloResult:
    return MonteCarloResult(
        route_id=route_id, n_simulations=10, forecast_days=90,
        current_rate=current, simulated_paths=[], percentiles={},
        prob_rate_increase=0.5, prob_rate_decrease=0.5,
        var_95=-0.10, expected_rate_90d=current,
        bull_case_90d=bull, bear_case_90d=current * 0.9,
        confidence_interval_90d=(current * 0.8, bull),
    )


def test_get_highest_upside_routes_sorted_by_upside_desc() -> None:
    results = {
        "low": _mk_mc_result("low", 2000.0, 2050.0),   # +2.5%
        "high": _mk_mc_result("high", 2000.0, 2400.0), # +20%
        "mid": _mk_mc_result("mid", 2000.0, 2200.0),   # +10%
    }
    out = get_highest_upside_routes(results, top_n=3)
    assert [r.route_id for r in out] == ["high", "mid", "low"]


def test_get_highest_upside_routes_top_n() -> None:
    results = {f"r{i}": _mk_mc_result(f"r{i}", 2000.0, 2000.0 + i * 10) for i in range(10)}
    out = get_highest_upside_routes(results, top_n=3)
    assert len(out) == 3


def test_get_highest_upside_handles_zero_current_rate() -> None:
    """Routes with current_rate=0 are sorted to upside=0 (no divide-by-zero)."""
    results = {
        "good": _mk_mc_result("good", 2000.0, 2200.0),
        "bad": _mk_mc_result("bad", 0.0, 100.0),
    }
    out = get_highest_upside_routes(results, top_n=5)
    # Both included, no crash.
    assert len(out) == 2


# ─── get_risk_adjusted_opportunity ─────────────────────────────────────────

def test_risk_adjusted_returns_zero_on_invalid_current() -> None:
    bad = _mk_mc_result("bad", 0.0, 100.0)
    assert get_risk_adjusted_opportunity(bad) == 0.0
