"""Tests for processing.risk_lab.

Three test groups matching the three modules:
  1. VaR / CVaR helpers — historical, parametric, portfolio
  2. Scenario stress test — against the catalog
  3. Regime detection — Bull / Bear / Crisis / Sideways defining properties

All synthetic inputs are deterministic (explicit int seeds, never
Python's salted hash()).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.risk_lab import (
    TRADING_DAYS_PER_YEAR,
    MarketRegime,
    ScenarioStressResult,
    VaRResult,
    _z_alpha,
    detect_regime,
    historical_var,
    parametric_var,
    portfolio_var,
    stress_test_all_scenarios,
    stress_test_scenario,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _normal_returns(n: int = 252, mu: float = 0.0, sigma: float = 0.02,
                    seed: int = 11) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(
        rng.normal(mu, sigma, n),
        index=pd.date_range("2025-01-01", periods=n, freq="B"),
    )


def _crisis_returns(n: int = 252, seed: int = 21) -> pd.Series:
    """A return series with both a clear drawdown AND a vol spike in the tail.

    Crisis-regime threshold needs vol_ratio > 1.5 (recent vol > 1.5×
    long-run vol) AND short-window drawdown ≤ -0.15. The 20-day crash
    here uses σ=0.055 — large enough that the recent 63-day window's
    vol clearly exceeds the 252-day baseline.
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0005, 0.012, n)
    # Inject a 20-day crash at the end: large negative jumps AND high vol.
    base[-20:] = rng.normal(-0.025, 0.055, 20)
    return pd.Series(
        base, index=pd.date_range("2025-01-01", periods=n, freq="B"),
    )


def _bull_returns(n: int = 252, seed: int = 31) -> pd.Series:
    """Steady-state bull market — drift dominates sample noise on 63 obs.

    Drift = +0.002/period with σ = 0.005 → std-of-mean(63) = 0.0006,
    so drift is ~3.2× the noise std on the 63-day window. Trailing
    sample mean stays positive across any seed.
    """
    rng = np.random.default_rng(seed)
    return pd.Series(
        rng.normal(0.0, 0.005, n) + 0.002,
        index=pd.date_range("2025-01-01", periods=n, freq="B"),
    )


def _bear_returns(n: int = 252, seed: int = 41) -> pd.Series:
    """Steady-state bear market — drift dominates sample noise on 63 obs.

    Drift = -0.002/period with σ = 0.005 — symmetric to the bull fixture
    but with the sign flipped. Ann_return reliably ≤ -50%, well below
    the Bear threshold (-0.08).
    """
    rng = np.random.default_rng(seed)
    return pd.Series(
        rng.normal(0.0, 0.005, n) - 0.002,
        index=pd.date_range("2025-01-01", periods=n, freq="B"),
    )


# ─── _z_alpha ───────────────────────────────────────────────────────────────

def test_z_alpha_known_values() -> None:
    # Inverse normal at standard confidence levels.
    assert _z_alpha(0.05) == pytest.approx(-1.6449, abs=1e-3)
    assert _z_alpha(0.01) == pytest.approx(-2.3263, abs=1e-3)
    assert _z_alpha(0.50) == pytest.approx(0.0, abs=1e-3)


def test_z_alpha_left_tail_is_negative() -> None:
    for alpha in (0.001, 0.05, 0.10, 0.25):
        assert _z_alpha(alpha) < 0


# ─── historical_var ─────────────────────────────────────────────────────────

def test_historical_var_returns_well_formed() -> None:
    r = _normal_returns(n=252, mu=0.0, sigma=0.02, seed=51)
    out = historical_var(r, confidence=0.95, portfolio_value=1_000_000)
    assert isinstance(out, VaRResult)
    assert out.method == "historical"
    assert out.confidence == 0.95
    assert out.var_pct <= 0.0
    assert out.cvar_pct <= out.var_pct       # CVaR is worse than VaR
    assert out.n_observations == 252
    assert out.var_dollar == pytest.approx(abs(out.var_pct) * 1_000_000, abs=1.0)


def test_historical_var_empty_input_safe() -> None:
    out = historical_var(None)
    assert out.var_pct == 0.0
    assert out.n_observations == 0
    out2 = historical_var(pd.Series(dtype=float))
    assert out2.var_pct == 0.0


def test_historical_var_too_few_obs_safe() -> None:
    out = historical_var(pd.Series([0.01, -0.01, 0.02]))
    assert out.var_pct == 0.0
    assert out.n_observations == 0


def test_historical_var_horizon_scaling() -> None:
    """Horizon-scaled VaR is sqrt(horizon) times the 1-day VaR."""
    r = _normal_returns(n=252, seed=53)
    var_1d = historical_var(r, horizon_days=1)
    var_5d = historical_var(r, horizon_days=5)
    assert var_5d.var_pct == pytest.approx(var_1d.var_pct * math.sqrt(5), abs=1e-6)


# ─── parametric_var ─────────────────────────────────────────────────────────

def test_parametric_var_returns_well_formed() -> None:
    r = _normal_returns(n=252, mu=0.0005, sigma=0.018, seed=61)
    out = parametric_var(r, confidence=0.95, portfolio_value=500_000)
    assert isinstance(out, VaRResult)
    assert out.method == "parametric"
    assert out.var_pct <= 0.0
    assert out.cvar_pct <= out.var_pct
    assert out.var_dollar == pytest.approx(abs(out.var_pct) * 500_000, abs=1.0)


def test_parametric_var_matches_gaussian_formula() -> None:
    """For a clean Gaussian fixture, parametric VaR = μ + z_α × σ."""
    r = _normal_returns(n=2000, mu=0.0, sigma=0.02, seed=62)
    out = parametric_var(r, confidence=0.95)
    z = _z_alpha(0.05)
    expected = 0.0 + z * float(r.std(ddof=0))
    # Tolerance loose: sample mean / std aren't exactly μ / σ.
    assert out.var_pct == pytest.approx(expected, abs=0.005)


def test_parametric_var_zero_sigma_safe() -> None:
    r = pd.Series([0.01] * 100)
    out = parametric_var(r)
    assert out.var_pct == 0.0


# ─── portfolio_var ──────────────────────────────────────────────────────────

def test_portfolio_var_aggregates_weighted_returns() -> None:
    """Portfolio VaR on a weights vector matches running VaR on the
    weighted-return series."""
    rng = np.random.default_rng(71)
    n = 252
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    returns_df = pd.DataFrame({
        "ZIM":  rng.normal(0, 0.02, n),
        "MATX": rng.normal(0, 0.015, n),
    }, index=dates)
    weights = {"ZIM": 0.6, "MATX": 0.4}

    out = portfolio_var(returns_df, weights, confidence=0.95)
    # Compute the weighted series directly and check it matches.
    direct = 0.6 * returns_df["ZIM"] + 0.4 * returns_df["MATX"]
    expected = historical_var(direct, confidence=0.95)
    assert out.var_pct == pytest.approx(expected.var_pct, abs=1e-6)


def test_portfolio_var_empty_inputs_safe() -> None:
    out = portfolio_var(pd.DataFrame(), {})
    assert out.var_pct == 0.0
    out2 = portfolio_var(
        pd.DataFrame({"ZIM": [0.01, 0.02, 0.03]}), {"DAC": 1.0},  # no overlap
    )
    assert out2.var_pct == 0.0


# ─── Scenario stress test ──────────────────────────────────────────────────

def test_stress_one_scenario_against_catalog() -> None:
    from state.scenarios import SCENARIO_CATALOG
    suez = SCENARIO_CATALOG["suez_closure"]
    weights = {"ZIM": 0.30, "MATX": 0.20, "SBLK": 0.20, "DAC": 0.30}
    out = stress_test_scenario(weights, suez, portfolio_value=1_000_000)
    assert isinstance(out, ScenarioStressResult)
    assert out.scenario_id == "suez_closure"
    assert out.category == "Geopolitical"
    # Suez closure shocks ZIM +18% → contribution to pnl_pct = 0.30 × 0.18 = 0.054.
    # Other tickers don't appear in the catalog's shocks list, so they
    # contribute zero. So pnl_pct ≈ +0.054.
    assert out.pnl_pct == pytest.approx(0.30 * 0.18, abs=1e-4)
    # Per-ticker P&L: ZIM gets 0.30 × 0.18 × 1M = 54k.
    assert out.per_ticker_pnl["ZIM"] == pytest.approx(54_000, abs=10.0)
    # Tickers without a matching shock get zero contribution.
    assert out.per_ticker_pnl["MATX"] == pytest.approx(0.0, abs=1e-6)


def test_stress_recession_drags_returns_negative() -> None:
    """demand_recession applies -18% to ticker:*.return via wildcard;
    every position should contribute negatively."""
    from state.scenarios import SCENARIO_CATALOG
    recession = SCENARIO_CATALOG["demand_recession"]
    weights = {"ZIM": 0.5, "MATX": 0.5}
    out = stress_test_scenario(weights, recession, portfolio_value=100_000)
    assert out.pnl_pct < 0
    # Both tickers contribute negative dollars.
    assert out.per_ticker_pnl["ZIM"] < 0
    assert out.per_ticker_pnl["MATX"] < 0


def test_stress_all_scenarios_returns_sorted_by_pnl_pct_ascending() -> None:
    """Worst-loss-first ordering — important for the UI."""
    weights = {"ZIM": 0.4, "MATX": 0.3, "SBLK": 0.3}
    results = stress_test_all_scenarios(weights, portfolio_value=1_000_000)
    assert len(results) >= 5     # catalog has 6 scenarios
    pnls = [r.pnl_pct for r in results]
    assert pnls == sorted(pnls)


def test_stress_empty_weights() -> None:
    from state.scenarios import SCENARIO_CATALOG
    out = stress_test_scenario({}, SCENARIO_CATALOG["suez_closure"])
    assert out.pnl_pct == 0.0
    assert out.per_ticker_pnl == {}


# ─── Regime detection ──────────────────────────────────────────────────────

def test_detect_regime_returns_well_formed() -> None:
    r = _normal_returns(n=200, seed=81)
    regime = detect_regime(r)
    assert isinstance(regime, MarketRegime)
    assert regime.label in {"Bull", "Bear", "Sideways", "Crisis", "Unknown"}
    assert 0.0 <= regime.confidence <= 1.0
    assert regime.interpretation


def test_detect_regime_insufficient_data_returns_unknown() -> None:
    regime = detect_regime(pd.Series([0.01, 0.02, -0.01]))
    assert regime.label == "Unknown"
    assert regime.confidence == 0.0


def test_detect_regime_recognises_crisis() -> None:
    r = _crisis_returns(n=252, seed=91)
    regime = detect_regime(r)
    assert regime.label == "Crisis"
    # Indicators should reflect both the drawdown and elevated vol.
    assert regime.indicators["short_drawdown"] < -0.05
    assert regime.indicators["vol_ratio"] > 1.0


def test_detect_regime_recognises_bull() -> None:
    r = _bull_returns(n=252, seed=101)
    regime = detect_regime(r)
    # With μ=0.001/day, σ=0.008/day → ann_return ≈ 25%, vol ≈ 13% → Bull.
    assert regime.label == "Bull"
    assert regime.indicators["ann_return_long"] > 0.10


def test_detect_regime_recognises_bear() -> None:
    r = _bear_returns(n=252, seed=111)
    regime = detect_regime(r)
    assert regime.label == "Bear"
    assert regime.indicators["ann_return_long"] < 0


def test_detect_regime_sideways_for_small_drift_low_vol() -> None:
    """Tiny drift, modest vol, no drawdown → Sideways."""
    rng = np.random.default_rng(121)
    # μ=0.0001/day → annualized 2.5% (below bull_return=0.10);
    # σ=0.005/day → annualized 8% (low vol)
    r = pd.Series(rng.normal(0.0001, 0.005, 252))
    regime = detect_regime(r)
    assert regime.label == "Sideways"
