"""Tests for processing.rate_analytics — rate regime / spread / seasonal helpers.

Covers:
  - _safe_series: None, empty df, DataFrame with rate_usd_per_feu column,
    DataFrame fallback to col 0, Series passthrough
  - compute_rate_regime:
      * skips routes with < 30 obs
      * Boom (z>1.5), Bust (z<-1.5), Normal (-0.5<z<0.5) thresholds
      * range_position formula (current vs min/max)
      * market_regime: 'Bull Market' (avg_z>1), 'Bear Market' (avg_z<-1),
        moderates between
      * empty input → 'Insufficient Data'
  - compute_rate_spreads:
      * filters by >= 10 obs
      * 'Wide' when z_spread > 1.5, 'Narrow' when < -1.5, else 'Normal'
      * sorted by |z_score| desc, capped at 15
      * correlation field present
  - compute_seasonal_factors:
      * skips routes with < 60 obs
      * returns 12-month factors normalized so the mean ≈ 100
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing.rate_analytics import (
    _safe_series,
    compute_rate_regime,
    compute_rate_spreads,
    compute_seasonal_factors,
)


def _freight_df(values: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(values), freq="D"),
        "rate_usd_per_feu": values,
    })


def _dated_series(values: list[float], start: str = "2024-01-01") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"))


# ─── _safe_series ───────────────────────────────────────────────────────────

def test_safe_series_returns_none_for_missing_key() -> None:
    assert _safe_series({}, "r") is None


def test_safe_series_returns_none_for_empty_df() -> None:
    assert _safe_series({"r": pd.DataFrame()}, "r") is None


def test_safe_series_picks_rate_column_by_name() -> None:
    """Regression: must not positionally pick `date`."""
    s = _safe_series({"r": _freight_df([1000.0, 1100.0])}, "r")
    assert s is not None
    assert list(s) == [1000.0, 1100.0]


def test_safe_series_falls_back_to_first_col_when_rate_missing() -> None:
    df = pd.DataFrame({"value": [1.5, 2.5, 3.5]})
    s = _safe_series({"r": df}, "r")
    assert s is not None
    assert list(s) == [1.5, 2.5, 3.5]


def test_safe_series_passes_through_series() -> None:
    raw = pd.Series([100.0, 200.0])
    out = _safe_series({"r": raw}, "r")
    assert out is not None
    assert list(out) == [100.0, 200.0]


def test_safe_series_drops_na() -> None:
    raw = pd.Series([100.0, float("nan"), 200.0])
    out = _safe_series({"r": raw}, "r")
    assert out is not None
    assert list(out) == [100.0, 200.0]


# ─── compute_rate_regime ────────────────────────────────────────────────────

def test_compute_rate_regime_skips_routes_under_30_obs() -> None:
    out = compute_rate_regime({"r": _freight_df([1000.0] * 20)})
    assert out["routes"] == {}
    assert out["market_regime"] == "Insufficient Data"


def test_compute_rate_regime_normal_when_current_near_mean() -> None:
    """Flat series → z ≈ 0 → 'Normal'."""
    out = compute_rate_regime({"r": _freight_df([1000.0] * 60)})
    assert out["routes"]["r"]["regime"] == "Normal"


def test_compute_rate_regime_boom_when_current_far_above_mean() -> None:
    """Mostly low rates with a final spike → z > 1.5 → 'Boom'."""
    rates = [1000.0] * 50 + [1010.0] * 9 + [3000.0]
    out = compute_rate_regime({"r": _freight_df(rates)})
    assert out["routes"]["r"]["regime"] == "Boom"


def test_compute_rate_regime_bust_when_current_far_below_mean() -> None:
    rates = [3000.0] * 50 + [2990.0] * 9 + [1000.0]
    out = compute_rate_regime({"r": _freight_df(rates)})
    assert out["routes"]["r"]["regime"] == "Bust"


def test_compute_rate_regime_range_position_formula() -> None:
    """current=1500, min=1000, max=2000 → (1500-1000)/(2000-1000) = 50%."""
    rates = [1000.0, 2000.0] + [1500.0] * 60
    out = compute_rate_regime({"r": _freight_df(rates)})
    assert out["routes"]["r"]["range_position"] == pytest.approx(50.0, abs=1.0)


def test_compute_rate_regime_market_regime_bull_when_avg_z_above_1() -> None:
    """Two routes both in Boom → market_regime='Bull Market'."""
    boom_a = [1000.0] * 50 + [1010.0] * 9 + [3000.0]
    boom_b = [500.0] * 50 + [510.0] * 9 + [1500.0]
    out = compute_rate_regime({"a": _freight_df(boom_a), "b": _freight_df(boom_b)})
    assert out["avg_z_score"] > 1.0
    assert out["market_regime"] == "Bull Market"


def test_compute_rate_regime_market_regime_bear_when_avg_z_below_neg_1() -> None:
    bust_a = [3000.0] * 50 + [2990.0] * 9 + [1000.0]
    bust_b = [1500.0] * 50 + [1490.0] * 9 + [500.0]
    out = compute_rate_regime({"a": _freight_df(bust_a), "b": _freight_df(bust_b)})
    assert out["market_regime"] == "Bear Market"


def test_compute_rate_regime_returns_n_routes_analyzed() -> None:
    out = compute_rate_regime({"r": _freight_df([1000.0] * 60)})
    assert out["n_routes_analyzed"] == 1


def test_compute_rate_regime_skips_non_dataframe_entries() -> None:
    out = compute_rate_regime({"junk": "not a df", "r": _freight_df([1000.0] * 60)})
    assert "junk" not in out["routes"]
    assert "r" in out["routes"]


# ─── compute_rate_spreads ──────────────────────────────────────────────────

def test_compute_rate_spreads_pairs_routes() -> None:
    a = _dated_series([1000.0] * 20)
    b = _dated_series([800.0] * 20)
    out = compute_rate_spreads({"a": a, "b": b})
    # One pair (a, b)
    assert len(out) == 1
    pair = out[0]
    assert {pair["route1"], pair["route2"]} == {"a", "b"}
    assert pair["current_spread"] == pytest.approx(200.0)


def test_compute_rate_spreads_skips_routes_under_10_obs() -> None:
    short = _dated_series([1000.0] * 5)
    long = _dated_series([800.0] * 20)
    out = compute_rate_spreads({"short": short, "long": long})
    # Only one route eligible → no pairs
    assert out == []


def test_compute_rate_spreads_signal_wide_when_z_above_1_5() -> None:
    """Series where spread surges at the end → z > 1.5 → 'Wide'."""
    flat_b = _dated_series([800.0] * 60)
    # Spread = a - b. Hold a constant for 50 days, then jump it.
    a_vals = [1000.0] * 50 + [1010.0] * 9 + [3000.0]
    a = _dated_series(a_vals)
    out = compute_rate_spreads({"a": a, "b": flat_b})
    assert out[0]["signal"] == "Wide"


def test_compute_rate_spreads_signal_narrow_when_z_below_neg_1_5() -> None:
    flat_b = _dated_series([800.0] * 60)
    a_vals = [1000.0] * 50 + [990.0] * 9 + [-1000.0]
    a = _dated_series(a_vals)
    out = compute_rate_spreads({"a": a, "b": flat_b})
    assert out[0]["signal"] == "Narrow"


def test_compute_rate_spreads_correlation_is_finite() -> None:
    a = _dated_series([1000.0 + i for i in range(20)])
    b = _dated_series([800.0 + i for i in range(20)])
    out = compute_rate_spreads({"a": a, "b": b})
    # Perfectly correlated linear series → corr ≈ 1.0
    assert out[0]["correlation"] == pytest.approx(1.0, abs=0.01)


def test_compute_rate_spreads_sorted_by_abs_z_desc() -> None:
    flat_c = _dated_series([500.0] * 60)
    # Pair 1: small spike, Pair 2: big spike, Pair 3: medium
    a = _dated_series([1000.0] * 50 + [1005.0] * 9 + [1100.0])   # small
    b = _dated_series([2000.0] * 50 + [2010.0] * 9 + [5000.0])   # huge
    out = compute_rate_spreads({"a": a, "b": b, "c": flat_c})
    z_scores = [abs(s["z_score"]) for s in out]
    assert z_scores == sorted(z_scores, reverse=True)


def test_compute_rate_spreads_capped_at_15() -> None:
    series_dict = {f"r{i}": _dated_series([100.0 * (i + 1)] * 20) for i in range(8)}
    # 8 routes → C(8,2) = 28 pairs → should cap at 15
    out = compute_rate_spreads(series_dict)
    assert len(out) <= 15


# ─── compute_seasonal_factors ───────────────────────────────────────────────

def test_compute_seasonal_factors_skips_short_series() -> None:
    """compute_seasonal_factors needs >= 60 obs AND a DatetimeIndex."""
    out = compute_seasonal_factors({"r": _dated_series([1000.0] * 30)})
    assert out == {}


def test_compute_seasonal_factors_returns_12_month_factors() -> None:
    """365 days of data with DatetimeIndex → all 12 months populated."""
    rates = [1000.0 + 100 * np.sin(i * 2 * np.pi / 365) for i in range(365)]
    out = compute_seasonal_factors({"r": _dated_series(rates)})
    assert "r" in out
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    assert set(out["r"].keys()) == set(months)


def test_compute_seasonal_factors_factors_average_near_100() -> None:
    """The 12 monthly factors, weighted by obs, should average near 100."""
    rates = [1000.0 + 100 * np.sin(i * 2 * np.pi / 365) for i in range(365)]
    out = compute_seasonal_factors({"r": _dated_series(rates)})
    avg = np.mean(list(out["r"].values()))
    assert avg == pytest.approx(100.0, abs=5.0)


def test_compute_seasonal_factors_skips_series_without_datetimeindex() -> None:
    """Frames with a column-style `date` (RangeIndex) are skipped — the
    function's documented requirement is index-as-dates."""
    out = compute_seasonal_factors({"r": _freight_df([1000.0] * 100)})
    assert "r" not in out


def test_compute_seasonal_factors_empty_input() -> None:
    assert compute_seasonal_factors({}) == {}
