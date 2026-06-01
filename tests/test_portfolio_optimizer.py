"""Tests for engine.portfolio_optimizer.

Strategy: synthetic return panels with known correlation + return profiles,
then assert each optimization method recovers its defining property:

  max_sharpe    : Sharpe(w*) ≥ Sharpe(equal_weight) on the training panel
  min_variance  : vol(w*)    ≤ vol(equal_weight)
  mean_variance : utility(w*) ≥ utility(equal_weight) for the given λ
  risk_parity   : per-asset risk contributions equal within tolerance

Plus shape / constraint / edge-case coverage and a backtest sanity check.
RNG seeded with explicit integers — no `hash()` (process-salted).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.portfolio_optimizer import (
    TRADING_DAYS_PER_YEAR,
    VALID_METHODS,
    BacktestResult,
    OptimizedPortfolio,
    _risk_contributions,
    estimate_cov,
    estimate_mu,
    optimize_portfolio,
    walk_forward_backtest,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _synth_returns(
    tickers: tuple[str, ...] = ("ZIM", "MATX", "SBLK", "DAC"),
    n: int = 504,
    means: tuple[float, ...] | None = None,
    vols:  tuple[float, ...] | None = None,
    correlation: float = 0.30,
    seed: int = 11,
) -> pd.DataFrame:
    """Generate synthetic daily returns with controllable per-asset mean/vol
    and a uniform pairwise correlation.

    Returns a date-indexed DataFrame with one column per ticker.
    """
    k = len(tickers)
    if means is None:
        means = (0.0006, 0.0004, 0.0008, 0.0005)[:k]
    if vols is None:
        vols  = (0.022, 0.014, 0.030, 0.020)[:k]

    rng = np.random.default_rng(seed)
    # Build a k×k correlation matrix and the corresponding cov matrix.
    corr = np.full((k, k), correlation)
    np.fill_diagonal(corr, 1.0)
    vol_arr = np.array(vols)
    cov = np.outer(vol_arr, vol_arr) * corr
    # Sample from a multivariate normal with the supplied mean vector.
    samples = rng.multivariate_normal(mean=np.array(means), cov=cov, size=n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(samples, index=dates, columns=list(tickers))


def _equal_weight(tickers) -> np.ndarray:
    return np.full(len(tickers), 1.0 / len(tickers))


# ─── estimate_mu / estimate_cov ─────────────────────────────────────────────

def test_estimate_mu_is_annualized() -> None:
    rf = _synth_returns(seed=1)
    mu = estimate_mu(rf)
    # The daily mean × 252 should match the function output exactly.
    expected = rf.mean() * TRADING_DAYS_PER_YEAR
    assert np.allclose(mu.to_numpy(), expected.to_numpy())


def test_estimate_cov_is_annualized_and_psd() -> None:
    rf = _synth_returns(seed=2)
    cov = estimate_cov(rf)
    # Symmetric, PSD, annualized.
    assert np.allclose(cov.to_numpy(), cov.to_numpy().T)
    eigvals = np.linalg.eigvalsh(cov.to_numpy())
    assert (eigvals > -1e-10).all()
    # Diagonal entries should match per-asset annualized variance.
    daily_vars = rf.var()
    expected_diag = daily_vars * TRADING_DAYS_PER_YEAR
    assert np.allclose(np.diag(cov.to_numpy()), expected_diag.to_numpy())


# ─── Universal shape / constraint tests across all methods ──────────────────

@pytest.mark.parametrize("method", VALID_METHODS)
def test_weights_sum_to_one_and_respect_bounds(method: str) -> None:
    rf = _synth_returns(seed=21)
    opt = optimize_portfolio(rf, method=method, weight_cap=0.40)
    weights = np.array(list(opt.weights.values()))
    assert math.isclose(float(weights.sum()), 1.0, abs_tol=1e-4)
    # Long-only by default.
    assert (weights >= -1e-6).all(), f"{method} produced negative weight: {weights}"
    assert (weights <= 0.40 + 1e-4).all(), f"{method} exceeded weight cap: {weights}"


@pytest.mark.parametrize("method", VALID_METHODS)
def test_result_has_required_fields(method: str) -> None:
    rf = _synth_returns(seed=22)
    opt = optimize_portfolio(rf, method=method)
    assert isinstance(opt, OptimizedPortfolio)
    assert opt.method == method
    assert set(opt.weights.keys()) == set(rf.columns)
    assert set(opt.risk_contributions.keys()) == set(rf.columns)
    assert math.isfinite(opt.expected_return)
    assert math.isfinite(opt.expected_vol)
    assert math.isfinite(opt.sharpe)
    # risk_contributions sum to 1.0 (they're fractions of portfolio variance).
    rc_sum = sum(opt.risk_contributions.values())
    assert math.isclose(rc_sum, 1.0, abs_tol=1e-3)


# ─── Method-specific defining properties ────────────────────────────────────

def test_max_sharpe_beats_equal_weight_sharpe() -> None:
    rf = _synth_returns(
        means=(0.0010, 0.0002, 0.0008, 0.0003), vols=(0.022, 0.014, 0.030, 0.020),
        seed=31,
    )
    mu = estimate_mu(rf).to_numpy()
    cov = estimate_cov(rf).to_numpy()
    risk_free = 0.045

    opt = optimize_portfolio(rf, method="max_sharpe", rf=risk_free)
    w_opt = np.array([opt.weights[c] for c in rf.columns])
    w_eq = _equal_weight(rf.columns)

    def sharpe_of(w: np.ndarray) -> float:
        ret = float(w @ mu)
        vol = math.sqrt(max(float(w @ cov @ w), 0.0))
        return (ret - risk_free) / vol if vol > 0 else 0.0

    assert sharpe_of(w_opt) >= sharpe_of(w_eq) - 1e-6


def test_min_variance_beats_equal_weight_vol() -> None:
    rf = _synth_returns(seed=32)
    cov = estimate_cov(rf).to_numpy()

    opt = optimize_portfolio(rf, method="min_variance")
    w_opt = np.array([opt.weights[c] for c in rf.columns])
    w_eq = _equal_weight(rf.columns)
    vol_opt = math.sqrt(float(w_opt @ cov @ w_opt))
    vol_eq = math.sqrt(float(w_eq @ cov @ w_eq))
    assert vol_opt <= vol_eq + 1e-6


def test_mean_variance_beats_equal_weight_utility() -> None:
    rf = _synth_returns(seed=33)
    mu = estimate_mu(rf).to_numpy()
    cov = estimate_cov(rf).to_numpy()
    risk_aversion = 2.0

    opt = optimize_portfolio(rf, method="mean_variance", risk_aversion=risk_aversion)
    w_opt = np.array([opt.weights[c] for c in rf.columns])
    w_eq = _equal_weight(rf.columns)

    def utility(w: np.ndarray) -> float:
        return float(w @ mu) - 0.5 * risk_aversion * float(w @ cov @ w)

    assert utility(w_opt) >= utility(w_eq) - 1e-6


def test_risk_parity_equalises_risk_contributions() -> None:
    rf = _synth_returns(seed=34)
    cov = estimate_cov(rf).to_numpy()
    opt = optimize_portfolio(rf, method="risk_parity")
    w_opt = np.array([opt.weights[c] for c in rf.columns])
    rc = _risk_contributions(w_opt, cov)
    # Every asset's risk contribution should be within ~3pp of 1/N (loose
    # because SLSQP is iterative and bounded).
    target = 1.0 / len(rf.columns)
    assert np.all(np.abs(rc - target) < 0.05), f"risk contributions: {rc}"


# ─── Constraint behavior ─────────────────────────────────────────────────────

def test_weight_cap_binds_when_signal_is_concentrated() -> None:
    """When one asset has a dominant mean return, max_sharpe wants to put
    everything in it. The weight cap should bind."""
    # ZIM has dramatically higher mean than the rest; everything else is noise.
    rf = _synth_returns(
        means=(0.0030, 0.0001, 0.0001, 0.0001), vols=(0.022, 0.014, 0.030, 0.020),
        seed=41,
    )
    opt = optimize_portfolio(rf, method="max_sharpe", weight_cap=0.40)
    # The dominant asset should hit (or be very close to) the cap.
    assert opt.weights["ZIM"] >= 0.39


def test_long_only_disabled_allows_negative_weights() -> None:
    rf = _synth_returns(seed=42)
    opt = optimize_portfolio(
        rf, method="min_variance",
        long_only=False, weight_cap=0.50,
    )
    weights = np.array(list(opt.weights.values()))
    # No assertion that there ARE negatives — just that they're permitted
    # and the optimizer didn't crash. Verify magnitude bounds.
    assert (weights >= -0.50 - 1e-6).all()
    assert (weights <= 0.50 + 1e-6).all()


# ─── Edge cases ──────────────────────────────────────────────────────────────

def test_empty_returns_raises() -> None:
    with pytest.raises(ValueError):
        optimize_portfolio(pd.DataFrame())


def test_single_asset_raises() -> None:
    rf = _synth_returns(tickers=("ZIM",), seed=51)
    with pytest.raises(ValueError):
        optimize_portfolio(rf)


def test_unknown_method_raises() -> None:
    rf = _synth_returns(seed=52)
    with pytest.raises(ValueError):
        optimize_portfolio(rf, method="market_cap_weight")


def test_insufficient_observations_raises() -> None:
    rf = _synth_returns(n=15, seed=53)
    with pytest.raises(ValueError):
        optimize_portfolio(rf)


# ─── walk_forward_backtest ──────────────────────────────────────────────────

def test_backtest_returns_finite_metrics_on_realistic_panel() -> None:
    rf = _synth_returns(n=750, seed=61)
    bt = walk_forward_backtest(rf, method="max_sharpe", train_window=252, rebal_freq=21)
    assert isinstance(bt, BacktestResult)
    assert bt.n_rebalances > 0
    assert math.isfinite(bt.annualized_return)
    assert math.isfinite(bt.annualized_vol)
    assert math.isfinite(bt.sharpe)
    assert bt.max_drawdown <= 0.0   # by definition non-positive
    assert bt.final_equity > 0.0
    assert len(bt.equity_curve) > 0


def test_backtest_min_variance_has_lower_vol_than_max_sharpe() -> None:
    """On the same data, the min_variance portfolio should backtest with
    lower realized volatility than the max_sharpe portfolio."""
    rf = _synth_returns(n=600, seed=62)
    bt_mv = walk_forward_backtest(rf, method="min_variance",
                                  train_window=252, rebal_freq=21)
    bt_ms = walk_forward_backtest(rf, method="max_sharpe",
                                  train_window=252, rebal_freq=21)
    # If both produced enough rebalances to be statistically meaningful.
    if bt_mv.n_rebalances >= 5 and bt_ms.n_rebalances >= 5:
        assert bt_mv.annualized_vol <= bt_ms.annualized_vol + 0.01


def test_backtest_handles_empty_and_short_inputs() -> None:
    empty = walk_forward_backtest(pd.DataFrame())
    assert empty.n_rebalances == 0
    assert empty.final_equity == 1.0

    short = _synth_returns(n=100, seed=63)  # below 252 + 21
    short_bt = walk_forward_backtest(short, train_window=252, rebal_freq=21)
    assert short_bt.n_rebalances == 0
