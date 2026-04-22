"""Tests for engine.cointegration.

The design uses known-statistical-properties fixtures: a random walk pair
that should FAIL cointegration, and an engineered cointegrated pair where
y_t = β·x_t + ε_t with ε stationary, which SHOULD pass.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.cointegration import (
    engle_granger,
    fit_ecm,
    half_life,
    half_life_from_lambda,
    johansen_test,
    pair_report,
    walk_forward_backtest,
)


RNG = np.random.default_rng(1729)


def _random_walk(n: int, scale: float = 1.0, start: float = 100.0) -> pd.Series:
    steps = RNG.normal(0, scale, size=n)
    return pd.Series(start + np.cumsum(steps), name="rw")


def _cointegrated_pair(n: int = 500, beta: float = 1.5) -> tuple[pd.Series, pd.Series]:
    """y_t = β·x_t + ε_t where ε is AR(1) stationary; x is a random walk."""
    x = _random_walk(n, scale=1.0, start=100.0)
    eps = np.zeros(n)
    phi = 0.85
    for i in range(1, n):
        eps[i] = phi * eps[i - 1] + RNG.normal(0, 1.0)
    y = pd.Series(beta * x.values + eps, name="y")
    x.name = "x"
    return y, x


def _independent_random_walks(n: int = 500) -> tuple[pd.Series, pd.Series]:
    a = _random_walk(n, scale=1.0, start=100.0)
    b = _random_walk(n, scale=1.0, start=100.0)
    a.name, b.name = "a", "b"
    return a, b


# ── half_life_from_lambda ───────────────────────────────────────────────────


def test_half_life_from_lambda_basic() -> None:
    # λ = -0.5 → 1+λ = 0.5 → half-life = 1 step (-ln2/ln0.5 = 1)
    assert math.isclose(half_life_from_lambda(-0.5), 1.0, rel_tol=1e-9)


def test_half_life_from_lambda_non_reverting() -> None:
    assert math.isinf(half_life_from_lambda(0.1))
    assert math.isinf(half_life_from_lambda(-1.5))


# ── Engle-Granger ───────────────────────────────────────────────────────────


def test_engle_granger_detects_cointegration() -> None:
    y, x = _cointegrated_pair(n=500, beta=1.5)
    res = engle_granger(y, x)
    # Should recover β ≈ 1.5 and reject unit-root in residuals.
    assert abs(res.beta - 1.5) < 0.1
    assert res.is_cointegrated
    assert res.coint_pvalue < 0.05


def test_engle_granger_rejects_independent_walks() -> None:
    a, b = _independent_random_walks(n=500)
    res = engle_granger(a, b)
    # Two independent I(1) series should not cointegrate reliably.
    assert not res.is_cointegrated or res.coint_pvalue > 0.01


# ── ECM ─────────────────────────────────────────────────────────────────────


def test_fit_ecm_adjustment_speed_negative() -> None:
    y, x = _cointegrated_pair(n=500, beta=1.5)
    ecm = fit_ecm(y, x)
    # Pull-back coefficient must be negative and significant.
    assert ecm.lambda_y < 0
    assert ecm.lambda_y_tstat < -2.0
    # Half-life should be a finite positive number of days.
    assert math.isfinite(ecm.half_life_days)
    assert ecm.half_life_days > 0


# ── half_life on spread directly ────────────────────────────────────────────


def test_half_life_ar1() -> None:
    # Construct φ = 0.5 AR(1) — expected half-life = 1.0
    n = 1000
    phi = 0.5
    eps = np.zeros(n)
    for i in range(1, n):
        eps[i] = phi * eps[i - 1] + RNG.normal(0, 1.0)
    hl = half_life(pd.Series(eps))
    assert abs(hl - 1.0) < 0.15


# ── Johansen ────────────────────────────────────────────────────────────────


def test_johansen_on_cointegrated_pair() -> None:
    y, x = _cointegrated_pair(n=500, beta=1.5)
    df = pd.concat([y, x], axis=1)
    res = johansen_test(df)
    assert res.rank >= 1
    assert res.is_cointegrated
    # Shapes: for a 2-series system trace has 2 hypotheses.
    assert res.trace_stat.shape == (2,)
    assert res.trace_crit_95.shape == (2,)


def test_johansen_rejects_noise() -> None:
    df = pd.DataFrame({
        "a": RNG.normal(0, 1, size=500).cumsum(),
        "b": RNG.normal(0, 1, size=500).cumsum(),
    })
    res = johansen_test(df)
    # Two independent random walks: rank should typically be 0.
    # Occasionally the test finds spurious rank — allow up to 1.
    assert res.rank <= 1


# ── pair_report integration ─────────────────────────────────────────────────


def test_pair_report_happy_path() -> None:
    y, x = _cointegrated_pair(n=500, beta=1.5)
    rpt = pair_report(y, x)
    assert rpt.n_obs == 500
    assert rpt.engle_granger.is_cointegrated
    assert rpt.ecm.lambda_y < 0
    assert isinstance(rpt.spread, pd.Series)
    assert len(rpt.spread) == 500
    assert math.isfinite(rpt.spread_zscore)


def test_pair_report_insufficient_obs_raises() -> None:
    y = pd.Series([1, 2, 3], name="y")
    x = pd.Series([1, 2, 3], name="x")
    with pytest.raises(ValueError):
        pair_report(y, x)


# ── Walk-forward backtest ───────────────────────────────────────────────────


def test_walk_forward_backtest_shape() -> None:
    # Use strictly positive series (log returns require >0).
    x = _random_walk(400, scale=0.3, start=100.0).clip(lower=1.0)
    eps = np.zeros(400)
    for i in range(1, 400):
        eps[i] = 0.8 * eps[i - 1] + RNG.normal(0, 0.5)
    y = pd.Series(1.2 * x.values + eps, name="y").clip(lower=1.0)
    x.name = "x"
    res = walk_forward_backtest(y, x, lookback=120)
    assert res.pair == ("y", "x")
    assert res.n_trades >= 0
    assert 0.0 <= res.hit_rate <= 1.0
    assert len(res.equity_curve) == 400
    assert math.isfinite(res.sharpe)
    assert math.isfinite(res.information_ratio)


def test_walk_forward_backtest_requires_enough_obs() -> None:
    y = pd.Series(np.arange(50) + 100.0, name="y")
    x = pd.Series(np.arange(50) + 100.0, name="x")
    with pytest.raises(ValueError):
        walk_forward_backtest(y, x, lookback=120)
