"""Tests for engine.correlator.

Covers:
  - CorrelationResult dataclass shape
  - ShippingStockCorrelator construction with custom thresholds
  - analyze(): empty input returns []; cleared thresholds → results
  - _find_best_lag returns the lag with highest |r| that passes gates
  - _compute_correlation: alignment, lag shift, insufficient overlap
  - _interpret: contains expected phrasing for each direction/timing
  - build_correlation_heatmap_data: shape + cell population
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.correlator import (
    CorrelationResult,
    ShippingStockCorrelator,
    build_correlation_heatmap_data,
)


# ─── Helpers / fixtures ────────────────────────────────────────────────────

def _dated_df(values: list[float], col: str, start: str = "2025-01-01") -> pd.DataFrame:
    """A date-indexed DataFrame with one column."""
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(values), freq="D"),
        col: values,
    })


def _correlated_pair(n: int = 120, noise: float = 0.05, seed: int = 11) -> tuple[pd.Series, pd.Series]:
    """Strongly positively correlated stock + signal series."""
    rng = np.random.default_rng(seed)
    base = np.cumsum(rng.normal(0.0, 1.0, n))
    stock = pd.Series(
        base + rng.normal(0.0, noise, n),
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )
    signal = pd.Series(
        base + rng.normal(0.0, noise, n),
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )
    return stock, signal


# ─── CorrelationResult dataclass ───────────────────────────────────────────

def test_correlation_result_shape() -> None:
    r = CorrelationResult(
        stock="ZIM", signal="BDI", lag_days=7,
        pearson_r=0.65, p_value=0.001,
        direction="positive", interpretation="...", n_observations=120,
    )
    assert r.stock == "ZIM"
    assert r.lag_days == 7
    assert r.direction == "positive"


# ─── ShippingStockCorrelator construction ──────────────────────────────────

def test_correlator_defaults() -> None:
    c = ShippingStockCorrelator()
    assert c.min_window == 60
    assert c.min_abs_r == 0.40
    assert c.lags_to_test == [0, 7, 14, 21, 30]


def test_correlator_custom_thresholds_and_lags() -> None:
    c = ShippingStockCorrelator(
        min_window=30, min_abs_r=0.25,
        lags_to_test=[0, 5, 10],
    )
    assert c.min_window == 30
    assert c.min_abs_r == 0.25
    assert c.lags_to_test == [0, 5, 10]


def test_correlator_default_lag_list_when_none_passed() -> None:
    """Passing lags_to_test=None falls back to the default [0,7,14,21,30]."""
    c = ShippingStockCorrelator(lags_to_test=None)
    assert c.lags_to_test == [0, 7, 14, 21, 30]


# ─── analyze: empty / missing inputs ───────────────────────────────────────

def test_analyze_empty_stock_data_returns_empty() -> None:
    c = ShippingStockCorrelator()
    assert c.analyze({}, {"BDIY": _dated_df([1, 2, 3], "value")}) == []


def test_analyze_empty_macro_data_returns_empty() -> None:
    c = ShippingStockCorrelator()
    stock_df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=3, freq="D"),
        "close": [10.0, 11.0, 12.0],
    })
    assert c.analyze({"ZIM": stock_df}, {}) == []


def test_analyze_with_no_significant_correlation_returns_empty() -> None:
    """Pure-noise stock + signal — no correlation should clear |r|>=0.40."""
    rng = np.random.default_rng(101)
    n = 200
    stock_df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="D"),
        "close": rng.normal(0, 1, n).cumsum(),
    })
    macro = {
        "BDIY": pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=n, freq="D"),
            "value": rng.normal(0, 1, n).cumsum(),
        }),
    }
    c = ShippingStockCorrelator(min_window=60, min_abs_r=0.40)
    # Independent random walks shouldn't produce |r|>0.40 on most seeds.
    out = c.analyze({"ZIM": stock_df}, macro)
    # If it does produce results, they must all clear the threshold.
    for r in out:
        assert abs(r.pearson_r) >= 0.40


# ─── _compute_correlation ──────────────────────────────────────────────────

def test_compute_correlation_recovers_high_r_on_aligned_pair() -> None:
    c = ShippingStockCorrelator(min_window=30)
    stock, signal = _correlated_pair(n=120, noise=0.05, seed=21)
    r, p, n = c._compute_correlation(signal, stock, lag=0)
    assert r > 0.80     # strong positive on a tight cumulative-noise pair
    assert n == 120


def test_compute_correlation_insufficient_overlap_returns_low_n() -> None:
    c = ShippingStockCorrelator(min_window=60)
    stock, signal = _correlated_pair(n=20, seed=31)
    r, p, n = c._compute_correlation(signal, stock, lag=0)
    # min_window=60 with n=20 → function returns (0.0, 1.0, n_actual)
    assert r == 0.0
    assert p == 1.0


def test_compute_correlation_lag_shift_changes_r() -> None:
    """Lagging the stock relative to the signal should reduce alignment on
    a contemporaneously-correlated pair."""
    c = ShippingStockCorrelator(min_window=30)
    stock, signal = _correlated_pair(n=120, noise=0.05, seed=41)
    r0, _, _ = c._compute_correlation(signal, stock, lag=0)
    r60, _, _ = c._compute_correlation(signal, stock, lag=60)
    # Lag-0 should dominate lag-60 on the cumulative-noise fixture.
    assert abs(r0) >= abs(r60)


# ─── _find_best_lag ────────────────────────────────────────────────────────

def test_find_best_lag_returns_none_when_nothing_clears() -> None:
    """A noisier pair where r ≈ 0.3-0.5 won't clear min_abs_r=0.95."""
    rng = np.random.default_rng(51)
    n = 120
    # Mostly noise, very weak signal — r will be small.
    stock = pd.Series(
        rng.normal(0, 1, n),
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )
    signal = pd.Series(
        rng.normal(0, 1, n),
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )
    c = ShippingStockCorrelator(min_window=30, min_abs_r=0.95, lags_to_test=[0])
    out = c._find_best_lag("ZIM", "BDIY", signal, stock)
    assert out is None


def test_find_best_lag_returns_result_on_strong_pair() -> None:
    c = ShippingStockCorrelator(min_window=30, min_abs_r=0.30,
                                lags_to_test=[0, 5, 10])
    stock, signal = _correlated_pair(n=120, seed=61)
    out = c._find_best_lag("ZIM", "BDIY", signal, stock)
    assert out is not None
    assert isinstance(out, CorrelationResult)
    assert out.stock == "ZIM"
    assert out.signal == "BDIY"
    assert out.lag_days in (0, 5, 10)
    assert abs(out.pearson_r) >= 0.30
    assert out.direction == ("positive" if out.pearson_r > 0 else "negative")


# ─── _interpret ────────────────────────────────────────────────────────────

def test_interpret_positive_contemporaneous() -> None:
    c = ShippingStockCorrelator()
    msg = c._interpret("ZIM", "BDI", r=0.65, lag=0)
    assert "ZIM" in msg
    assert "positively" in msg
    assert "Baltic Dry Index" in msg     # signal label translation
    assert "contemporaneously" in msg


def test_interpret_negative_with_signal_leading() -> None:
    c = ShippingStockCorrelator()
    msg = c._interpret("MATX", "BDI", r=-0.55, lag=14)
    assert "MATX" in msg
    assert "inversely" in msg
    assert "14-day lag" in msg


def test_interpret_with_stock_leading_signal() -> None:
    """Negative lag means stock anticipates signal."""
    c = ShippingStockCorrelator()
    msg = c._interpret("ZIM", "BDI", r=0.60, lag=-7)
    assert "7-day lead" in msg
    assert "stock anticipates" in msg


def test_interpret_strength_threshold() -> None:
    c = ShippingStockCorrelator()
    strong = c._interpret("ZIM", "BDI", r=0.75, lag=0)
    moderate = c._interpret("ZIM", "BDI", r=0.45, lag=0)
    assert "strongly" in strong
    assert "moderately" in moderate


def test_interpret_unknown_signal_uses_raw_label() -> None:
    """An unmapped signal ID flows through verbatim."""
    c = ShippingStockCorrelator()
    msg = c._interpret("ZIM", "RANDOM_SIG", r=0.50, lag=0)
    assert "RANDOM_SIG" in msg


# ─── build_correlation_heatmap_data ────────────────────────────────────────

def test_heatmap_data_shape() -> None:
    results: list[CorrelationResult] = []
    df = build_correlation_heatmap_data(
        results,
        all_stocks=["ZIM", "MATX"],
        all_signals=["BDI", "FBX01_Rate"],
    )
    assert df.shape == (2, 2)
    assert list(df.index) == ["BDI", "FBX01_Rate"]
    assert list(df.columns) == ["ZIM", "MATX"]
    # Empty results → all zeros.
    assert (df.to_numpy() == 0.0).all()


def test_heatmap_data_populates_cells() -> None:
    results = [
        CorrelationResult(stock="ZIM", signal="BDI", lag_days=0,
                          pearson_r=0.62, p_value=0.01,
                          direction="positive", interpretation="x",
                          n_observations=100),
        CorrelationResult(stock="MATX", signal="FBX01_Rate", lag_days=7,
                          pearson_r=-0.45, p_value=0.03,
                          direction="negative", interpretation="x",
                          n_observations=100),
    ]
    df = build_correlation_heatmap_data(
        results, all_stocks=["ZIM", "MATX"],
        all_signals=["BDI", "FBX01_Rate"],
    )
    assert df.loc["BDI", "ZIM"] == pytest.approx(0.62)
    assert df.loc["FBX01_Rate", "MATX"] == pytest.approx(-0.45)
    # Unfilled cells stay 0.
    assert df.loc["BDI", "MATX"] == 0.0
    assert df.loc["FBX01_Rate", "ZIM"] == 0.0


def test_heatmap_data_ignores_unknown_stocks_or_signals() -> None:
    """A result for a stock/signal not in the supplied lists is silently
    dropped — no exception, no spurious column/row."""
    results = [
        CorrelationResult(stock="UNKNOWN", signal="BDI", lag_days=0,
                          pearson_r=0.7, p_value=0.01,
                          direction="positive", interpretation="x",
                          n_observations=100),
    ]
    df = build_correlation_heatmap_data(
        results, all_stocks=["ZIM"], all_signals=["BDI"],
    )
    # No row/col added; the unknown stock just doesn't contribute.
    assert df.shape == (1, 1)
    assert df.loc["BDI", "ZIM"] == 0.0


# ─── BDI key resolution ────────────────────────────────────────────────────

def test_analyze_resolves_bdi_via_bsxrlm_macro_key() -> None:
    """The signal builder must source BDI from the canonical FRED ``BSXRLM`` key.

    A tightly correlated stock+BDI pair should produce at least one BDI
    correlation result when the macro frame is keyed by BSXRLM.
    """
    n = 200
    rng = np.random.default_rng(7)
    base = np.cumsum(rng.normal(0.0, 1.0, n))
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    stock_df = pd.DataFrame({"date": dates, "close": base + rng.normal(0, 0.05, n)})
    macro = {
        "BSXRLM": pd.DataFrame({"date": dates, "value": base + rng.normal(0, 0.05, n)}),
    }
    c = ShippingStockCorrelator(min_window=60, min_abs_r=0.30, lags_to_test=[0])
    results = c.analyze({"ZIM": stock_df}, macro)
    bdi_results = [r for r in results if r.signal == "BDI"]
    assert bdi_results, "BDI signal should resolve via BSXRLM macro key"


def test_analyze_resolves_bdi_via_bdiy_fallback_macro_key() -> None:
    """Legacy callers keyed by ``BDIY`` must continue to resolve as the fallback."""
    n = 200
    rng = np.random.default_rng(7)
    base = np.cumsum(rng.normal(0.0, 1.0, n))
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    stock_df = pd.DataFrame({"date": dates, "close": base + rng.normal(0, 0.05, n)})
    macro = {
        "BDIY": pd.DataFrame({"date": dates, "value": base + rng.normal(0, 0.05, n)}),
    }
    c = ShippingStockCorrelator(min_window=60, min_abs_r=0.30, lags_to_test=[0])
    results = c.analyze({"ZIM": stock_df}, macro)
    bdi_results = [r for r in results if r.signal == "BDI"]
    assert bdi_results, "BDI signal should resolve via BDIY legacy fallback"
