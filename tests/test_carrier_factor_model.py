"""Tests for engine.carrier_factor_model.

Strategy: build synthetic panels where true β loadings are known, then
assert recovery within tolerance. Keeps tests deterministic (seeded) and
independent of any live feed.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.carrier_factor_model import (
    DEFAULT_CARRIERS,
    DEFAULT_FACTORS,
    build_factor_frame,
    fit_carrier_factors,
    residual_signal_backtest,
    residual_zscore,
)


RNG = np.random.default_rng(20260422)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _synthetic_panel(
    n: int = 260,
    carriers: tuple[str, ...] = ("ZIM", "MATX"),
    factors: tuple[str, ...] = ("dBDI", "dSCFI", "dBrent", "dDXY"),
    true_betas: dict[str, dict[str, float]] | None = None,
    noise: float = 0.01,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, float]]]:
    """Return (returns_df, factors_df, true_betas) on a weekly index."""
    if true_betas is None:
        # ZIM: classic cyclical, big on rates
        # MATX: muted, negative oil beta
        true_betas = {
            "ZIM":  {"dBDI": 0.6, "dSCFI": 0.8, "dBrent":  0.1, "dDXY": -0.4},
            "MATX": {"dBDI": 0.3, "dSCFI": 0.2, "dBrent": -0.3, "dDXY": -0.2},
        }
    idx = pd.date_range("2020-01-03", periods=n, freq="W-FRI")
    F = {c: RNG.normal(0, 0.03, size=n) for c in factors}
    factors_df = pd.DataFrame(F, index=idx)

    R: dict[str, np.ndarray] = {}
    for ticker in carriers:
        beta_vec = np.array([true_betas[ticker].get(f, 0.0) for f in factors])
        signal = factors_df.values @ beta_vec
        eps = RNG.normal(0, noise, size=n)
        R[ticker] = signal + eps
    returns_df = pd.DataFrame(R, index=idx)
    return returns_df, factors_df, true_betas


# ── fit_carrier_factors ─────────────────────────────────────────────────────


def test_fit_recovers_known_betas() -> None:
    returns_df, factors_df, truth = _synthetic_panel(n=260, noise=0.005)
    fits = fit_carrier_factors(returns_df, factors_df)
    assert set(fits.keys()) == set(returns_df.columns)

    for ticker, fit in fits.items():
        for factor, true_b in truth[ticker].items():
            est = fit.betas[factor]
            assert abs(est - true_b) < 0.1, (
                f"{ticker}:{factor} est={est:.3f} true={true_b:.3f}"
            )
        # R² should be very high since noise is small
        assert fit.r_squared > 0.8
        # Alpha should be ~0 (no intercept in data-gen)
        assert abs(fit.alpha) < 0.005


def test_fit_r_squared_drops_with_noise() -> None:
    _, factors_df_low, _ = _synthetic_panel(n=260, noise=0.001)
    returns_low, _, _ = _synthetic_panel(n=260, noise=0.001)
    returns_high, _, _ = _synthetic_panel(n=260, noise=0.05)
    fit_low  = fit_carrier_factors(returns_low,  factors_df_low)["ZIM"]
    fit_high = fit_carrier_factors(returns_high, factors_df_low)["ZIM"]
    assert fit_low.r_squared > fit_high.r_squared


def test_fit_skips_insufficient_obs() -> None:
    factors_df = pd.DataFrame({
        "dBDI":   RNG.normal(0, 0.03, size=5),
        "dSCFI":  RNG.normal(0, 0.03, size=5),
    }, index=pd.date_range("2026-01-02", periods=5, freq="W-FRI"))
    returns_df = pd.DataFrame({"ZIM": RNG.normal(0, 0.02, size=5)},
                              index=factors_df.index)
    fits = fit_carrier_factors(returns_df, factors_df)
    # Too few obs — should be silently skipped
    assert fits == {}


def test_fit_empty_returns() -> None:
    fits = fit_carrier_factors(pd.DataFrame(), pd.DataFrame())
    assert fits == {}


def test_fit_hac_tstats_finite() -> None:
    returns_df, factors_df, _ = _synthetic_panel(n=260)
    fits = fit_carrier_factors(returns_df, factors_df, hac_lags=6)
    for fit in fits.values():
        for tv in fit.tvalues.values():
            assert math.isfinite(tv)


# ── residual_zscore ─────────────────────────────────────────────────────────


def test_residual_zscore_centered_and_finite() -> None:
    returns_df, factors_df, _ = _synthetic_panel(n=260)
    fit = fit_carrier_factors(returns_df, factors_df)["ZIM"]
    z = residual_zscore(fit, returns_df["ZIM"], factors_df, window=52)
    # After the burn-in, z should be roughly centered, finite, and have
    # unit-ish standard deviation on the tail window.
    tail = z.dropna().iloc[-52:]
    assert len(tail) == 52
    assert abs(tail.mean()) < 0.5
    assert 0.5 < tail.std(ddof=0) < 2.0


# ── residual_signal_backtest ────────────────────────────────────────────────


def test_backtest_returns_finite_metrics() -> None:
    returns_df, factors_df, _ = _synthetic_panel(n=200, noise=0.02)
    bt = residual_signal_backtest(
        returns_df["ZIM"], factors_df, lookback=52,
    )
    assert bt.name == "ZIM"
    assert bt.n_trades >= 0
    assert 0.0 <= bt.hit_rate <= 1.0
    assert math.isfinite(bt.sharpe)
    assert math.isfinite(bt.information_ratio)
    assert len(bt.equity_curve) == len(returns_df)


def test_backtest_requires_enough_obs() -> None:
    idx = pd.date_range("2026-01-02", periods=30, freq="W-FRI")
    returns = pd.Series(RNG.normal(0, 0.02, size=30), index=idx, name="ZIM")
    factors = pd.DataFrame({
        "dBDI": RNG.normal(0, 0.03, size=30),
        "dSCFI": RNG.normal(0, 0.03, size=30),
    }, index=idx)
    with pytest.raises(ValueError):
        residual_signal_backtest(returns, factors, lookback=52)


# ── build_factor_frame ──────────────────────────────────────────────────────


def test_build_factor_frame_log_diff_and_spread() -> None:
    idx_daily = pd.date_range("2024-01-01", "2025-12-31", freq="B")
    bdi  = pd.Series(1500 + np.cumsum(RNG.normal(0, 5, size=len(idx_daily))), index=idx_daily, name="BDI").abs() + 500
    scfi = pd.Series(1000 + np.cumsum(RNG.normal(0, 3, size=len(idx_daily))), index=idx_daily, name="SCFI").abs() + 500
    brent = pd.Series(75 + np.cumsum(RNG.normal(0, 0.5, size=len(idx_daily))), index=idx_daily, name="Brent").abs() + 10
    wti   = pd.Series(72 + np.cumsum(RNG.normal(0, 0.5, size=len(idx_daily))), index=idx_daily, name="WTI").abs() + 10
    dxy   = pd.Series(102 + np.cumsum(RNG.normal(0, 0.2, size=len(idx_daily))), index=idx_daily, name="DXY").abs() + 10
    vix   = pd.Series(15 + RNG.normal(0, 0.5, size=len(idx_daily)).cumsum(), index=idx_daily, name="VIX").abs() + 5

    factors = build_factor_frame(
        {"BDI": bdi, "SCFI": scfi, "Brent": brent, "WTI": wti, "DXY": dxy, "VIX": vix}
    )
    assert not factors.empty
    # Expected column names given default config
    assert "dBDI" in factors.columns
    assert "dSCFI" in factors.columns
    assert "dBrent" in factors.columns
    assert "dDXY" in factors.columns
    assert "VIX" in factors.columns
    assert "WTI_Brent_spread" in factors.columns
    # Resampled to weekly → should have ≤ 110 rows across 2 years
    assert len(factors) <= 110


def test_build_factor_frame_empty_input() -> None:
    assert build_factor_frame({}).empty


# ── Module-level constants sanity check ─────────────────────────────────────


def test_default_carriers_and_factors_are_tuples() -> None:
    assert isinstance(DEFAULT_CARRIERS, tuple)
    assert isinstance(DEFAULT_FACTORS, tuple)
    assert len(DEFAULT_CARRIERS) >= 5
    assert len(DEFAULT_FACTORS) >= 5
