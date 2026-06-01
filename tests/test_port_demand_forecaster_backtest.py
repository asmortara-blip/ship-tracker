"""Tests for port_demand_forecaster's walk-forward backtest path.

Only covers the new functions:
  - naive_history_forecast
  - walk_forward_backtest
  - backtest_all_ports
  - PortDemandBacktestResult

The existing signal-based forecaster (forecast_port_demand,
forecast_all_ports, etc.) is covered elsewhere.

Strategy: build synthetic per-port histories with KNOWN properties
(trend, mean-reverting, flat, noisy) and assert the backtest's metrics
match what those properties imply. RNG seeds are explicit integers.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.port_demand_forecaster import (
    PortDemandBacktestResult,
    backtest_all_ports,
    naive_history_forecast,
    walk_forward_backtest,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _flat_series(value: float = 0.50, n: int = 180) -> pd.Series:
    """Constant series — drift = 0, mean = value."""
    return pd.Series([value] * n, index=pd.date_range("2025-01-01", periods=n, freq="D"))


def _trending_series(start: float = 0.30, end: float = 0.70, n: int = 180) -> pd.Series:
    """Linear trend with no noise — drift dominates the forecast."""
    return pd.Series(
        np.linspace(start, end, n),
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )


def _mean_reverting_series(level: float = 0.50, vol: float = 0.05,
                            n: int = 180, seed: int = 41) -> pd.Series:
    """OU-like process oscillating around `level` — drift ≈ 0, mean ≈ level."""
    rng = np.random.default_rng(seed)
    vals = [level]
    for _ in range(n - 1):
        prev = vals[-1]
        shock = rng.normal(0, vol)
        new = 0.85 * prev + 0.15 * level + shock
        vals.append(max(0.05, min(0.95, new)))
    return pd.Series(vals, index=pd.date_range("2025-01-01", periods=n, freq="D"))


# ─── naive_history_forecast — basic behavior ────────────────────────────────

def test_naive_forecast_empty_or_none_returns_neutral() -> None:
    assert naive_history_forecast(None) == 0.5
    assert naive_history_forecast(pd.Series(dtype=float)) == 0.5


def test_naive_forecast_single_point_returns_clamped_value() -> None:
    s = pd.Series([0.42])
    assert naive_history_forecast(s) == pytest.approx(0.42, abs=1e-6)


def test_naive_forecast_flat_series_returns_the_level() -> None:
    s = _flat_series(0.40, n=90)
    assert naive_history_forecast(s, horizon=30) == pytest.approx(0.40, abs=1e-6)


def test_naive_forecast_clamps_to_bounds() -> None:
    # Extreme positive drift extrapolated through horizon would shoot past 1.0.
    s = pd.Series(np.linspace(0.50, 0.94, 60))
    out = naive_history_forecast(s, horizon=180, drift_weight=1.0, mean_weight=0.0)
    assert 0.05 <= out <= 0.95


def test_naive_forecast_drift_weight_extrapolates_trend() -> None:
    s = _trending_series(0.30, 0.70, n=60)
    # With full drift weight and zero mean weight, the forecast is exactly
    # last + drift_per_day × horizon (then clamped).
    drift_per_day = (s.iloc[-1] - s.iloc[0]) / (len(s) - 1)
    expected = s.iloc[-1] + drift_per_day * 30
    out = naive_history_forecast(s, horizon=30, drift_weight=1.0, mean_weight=0.0)
    assert out == pytest.approx(expected, abs=1e-6)


def test_naive_forecast_mean_weight_returns_trailing_mean() -> None:
    s = _trending_series(0.30, 0.70, n=60)
    out = naive_history_forecast(s, horizon=30, drift_weight=0.0, mean_weight=1.0)
    assert out == pytest.approx(float(s.mean()), abs=1e-6)


# ─── walk_forward_backtest — structure / edge cases ─────────────────────────

def test_walk_forward_returns_well_formed_result() -> None:
    s = _mean_reverting_series(seed=11, n=200)
    bt = walk_forward_backtest(s, port_locode="USLAX", train_window=60, horizon=30, step=15)
    assert isinstance(bt, PortDemandBacktestResult)
    assert bt.port_locode == "USLAX"
    assert bt.n_predictions > 0
    assert math.isfinite(bt.mae)
    assert math.isfinite(bt.rmse)
    assert 0.0 <= bt.direction_hit_rate <= 1.0
    assert bt.avg_horizon_days == 30


def test_walk_forward_empty_input() -> None:
    bt = walk_forward_backtest(None, port_locode="X")
    assert bt.n_predictions == 0
    assert bt.mae == 0.0
    assert bt.port_locode == "X"


def test_walk_forward_too_short_history() -> None:
    s = _flat_series(0.5, n=50)
    bt = walk_forward_backtest(s, train_window=60, horizon=30)
    assert bt.n_predictions == 0


# ─── walk_forward_backtest — defining properties ────────────────────────────

def test_flat_series_has_zero_mae_and_bias() -> None:
    """On a perfectly flat series, predict = anchor for every step → MAE = 0."""
    s = _flat_series(0.42, n=200)
    bt = walk_forward_backtest(s, train_window=60, horizon=30, step=15)
    assert bt.n_predictions > 0
    assert bt.mae == pytest.approx(0.0, abs=1e-9)
    assert bt.bias == pytest.approx(0.0, abs=1e-9)
    # Direction hit rate is 0 when there's no movement (no direction signals).
    assert bt.direction_hit_rate == 0.0


def test_trending_series_direction_hit_rate_is_high() -> None:
    """Linear up-trend → forecast direction matches realized direction every time."""
    s = _trending_series(0.30, 0.70, n=200)
    bt = walk_forward_backtest(s, train_window=60, horizon=30, step=15,
                                drift_weight=1.0, mean_weight=0.0)
    assert bt.direction_hit_rate >= 0.95  # near-perfect on a clean trend


def test_trending_series_mae_smaller_with_drift_weight() -> None:
    """With drift_weight=1 (extrapolate), MAE should be much lower on a
    deterministic linear trend than with mean_weight=1 (which underestimates
    every forward value)."""
    s = _trending_series(0.30, 0.70, n=200)
    bt_drift = walk_forward_backtest(s, train_window=60, horizon=30, step=15,
                                      drift_weight=1.0, mean_weight=0.0)
    bt_mean = walk_forward_backtest(s, train_window=60, horizon=30, step=15,
                                     drift_weight=0.0, mean_weight=1.0)
    assert bt_drift.mae < bt_mean.mae


def test_mean_reverting_series_low_bias() -> None:
    """A mean-reverting series with mean ≈ 0.5 should give small bias when
    forecast leans on the trailing mean."""
    s = _mean_reverting_series(level=0.50, vol=0.04, n=200, seed=21)
    bt = walk_forward_backtest(s, train_window=60, horizon=30, step=15,
                                drift_weight=0.2, mean_weight=0.8)
    assert abs(bt.bias) < 0.05


# ─── backtest_all_ports — multi-port batch ──────────────────────────────────

def test_backtest_all_ports_returns_one_result_per_port() -> None:
    histories = {
        "USLAX": _flat_series(0.50, n=200),
        "CNSHA": _trending_series(0.40, 0.70, n=200),
        "NLRTM": _mean_reverting_series(level=0.55, n=200, seed=33),
    }
    results = backtest_all_ports(histories, train_window=60, horizon=30, step=15)
    assert len(results) == 3
    assert {r.port_locode for r in results} == {"USLAX", "CNSHA", "NLRTM"}
    for r in results:
        assert isinstance(r, PortDemandBacktestResult)


def test_backtest_all_ports_sorted_by_mae_ascending() -> None:
    """Most-accurate port should appear first."""
    histories = {
        "FLAT":  _flat_series(0.50, n=200),                # MAE ≈ 0
        "NOISY": _mean_reverting_series(vol=0.10, n=200, seed=55),  # bigger MAE
    }
    results = backtest_all_ports(histories, train_window=60, horizon=30, step=15)
    assert len(results) == 2
    assert results[0].mae <= results[1].mae
    assert results[0].port_locode == "FLAT"


def test_backtest_all_ports_empty_input() -> None:
    assert backtest_all_ports({}) == []
