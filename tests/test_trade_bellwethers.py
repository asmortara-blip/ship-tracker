"""Pure-function tests for processing.trade_bellwethers.

The recently patched helpers ``_safe_last`` and ``_safe_pct_change`` now
select the FRED ``value`` column **by name**, with a last-column fallback
for atypical frames. Previously they reached for whatever sat in column 0
(typically `date`), which silently produced nonsense values when the frame
was sorted with date first. These tests pin:

  * the ``value`` column is read (never a Timestamp / not column 0),
  * Series inputs still work,
  * pct-change is directionally sensible & numeric,
  * None / empty / too-short inputs return None,
  * ``compute_bellwether_score`` integrates the helpers and produces a
    well-formed composite.

No Streamlit, no live feed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing.trade_bellwethers import (
    INDICATOR_WEIGHTS,
    _safe_last,
    _safe_pct_change,
    compute_bellwether_score,
)


# ── Synthetic frame builders ────────────────────────────────────────────────


def _fred(n: int, start: float, slope: float) -> pd.DataFrame:
    """Canonical FRED frame: [date, value]."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "value": np.arange(n, dtype=float) * slope + start,
    })


def _fred_swapped(n: int, start: float, slope: float) -> pd.DataFrame:
    """A FRED frame with value first, date last — by-name selection still works."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "value": np.arange(n, dtype=float) * slope + start,
        "date": dates,
    })


# ── _safe_last ──────────────────────────────────────────────────────────────


def test_safe_last_fred_returns_last_value_not_date() -> None:
    """A FRED frame yields the last value column — not a Timestamp from column 0."""
    df = _fred(n=10, start=100.0, slope=1.5)
    result = _safe_last(df)
    assert result is not None
    assert isinstance(result, float)
    # n=10, start=100, slope=1.5 → last = 100 + 1.5*9 = 113.5
    assert result == pytest.approx(113.5)


def test_safe_last_works_regardless_of_column_order() -> None:
    """By-name selection — the column order has no effect on the result."""
    df = _fred_swapped(n=5, start=50.0, slope=2.0)
    result = _safe_last(df)
    assert result == pytest.approx(58.0)


def test_safe_last_series_input() -> None:
    raw = pd.Series([1.0, 2.0, 3.0, float("nan")])
    result = _safe_last(raw)
    # NaN is dropped, so the "last" is 3.0.
    assert result == pytest.approx(3.0)


def test_safe_last_none_and_empty() -> None:
    assert _safe_last(None) is None
    assert _safe_last(pd.DataFrame()) is None
    assert _safe_last(pd.DataFrame({"value": []})) is None
    assert _safe_last(pd.Series([], dtype=float)) is None


def test_safe_last_no_value_column_falls_back_to_last_col() -> None:
    """Without 'value', the helper falls back to df.columns[-1]."""
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3, freq="D"),
        "alt_field": [10.0, 20.0, 30.0],
    })
    assert _safe_last(df) == pytest.approx(30.0)


def test_safe_last_all_nan_returns_none() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3, freq="D"),
        "value": [float("nan")] * 3,
    })
    assert _safe_last(df) is None


# ── _safe_pct_change ────────────────────────────────────────────────────────


def test_safe_pct_change_fred_directional_increase() -> None:
    """An ascending FRED series → positive pct-change."""
    df = _fred(n=20, start=100.0, slope=1.0)
    pct = _safe_pct_change(df, periods=10)
    assert pct is not None
    assert isinstance(pct, float)
    # last=119, prev=109 → (119-109)/109 ≈ 0.0917
    assert pct == pytest.approx((119.0 - 109.0) / 109.0)
    assert pct > 0


def test_safe_pct_change_fred_directional_decrease() -> None:
    df = _fred(n=20, start=200.0, slope=-2.0)
    pct = _safe_pct_change(df, periods=5)
    assert pct is not None
    assert pct < 0


def test_safe_pct_change_zero_prev_returns_none() -> None:
    """Division-by-zero guard: prev=0 yields None, not inf / NaN."""
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5, freq="D"),
        "value": [0.0, 1.0, 2.0, 3.0, 4.0],
    })
    pct = _safe_pct_change(df, periods=4)  # prev_index = 0, value = 0.0
    assert pct is None


def test_safe_pct_change_too_short_returns_none() -> None:
    df = _fred(n=2, start=100.0, slope=1.0)
    assert _safe_pct_change(df, periods=5) is None


def test_safe_pct_change_none_and_empty() -> None:
    assert _safe_pct_change(None) is None
    assert _safe_pct_change(pd.DataFrame()) is None


def test_safe_pct_change_series_input() -> None:
    raw = pd.Series([100.0, 105.0, 110.0, 115.0])
    pct = _safe_pct_change(raw, periods=3)
    assert pct == pytest.approx((115.0 - 100.0) / 100.0)


# ── compute_bellwether_score integration ────────────────────────────────────


def test_bellwether_empty_macro_returns_no_data() -> None:
    """No macro data → a 'No Data' shape with neutral composite, never a crash."""
    out = compute_bellwether_score({})
    assert out["composite_label"] == "No Data"
    assert out["composite_score"] == pytest.approx(0.5)
    assert out["narrative"]
    assert out["timestamp"]


def test_bellwether_indicator_weights_sum_to_one() -> None:
    """The composite weight table must sum to exactly 1.0."""
    assert abs(sum(INDICATOR_WEIGHTS.values()) - 1.0) < 1e-9


def test_bellwether_score_uses_value_not_date() -> None:
    """When macro frames are present, the indicators read the value column.

    With PMI=55 (>50) and a positive yield curve, the bellwether must not
    fire 'Contraction'. If _safe_last accidentally returned a Timestamp, the
    score would either crash or land near 0.5 (the neutral fallback).
    """
    pmi_frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=10, freq="D"),
        "value": [55.0] * 10,  # firmly in expansion territory
    })
    yc_frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=10, freq="D"),
        "value": [1.5] * 10,  # normal positive spread
    })
    macro = {"NAPMPI": pmi_frame, "T10Y2Y": yc_frame}

    out = compute_bellwether_score(macro)
    pmi_raw = out["indicators"]["pmi"]["raw"]
    yc_raw = out["indicators"]["yield_curve"]["raw"]
    assert pmi_raw == pytest.approx(55.0)
    assert yc_raw == pytest.approx(1.5)
    assert out["indicators"]["pmi"]["interpretation"] == "Expansion"
    assert out["composite_label"] in {"Expansion", "Neutral", "Contraction"}


def test_bellwether_score_in_unit_interval() -> None:
    """Composite score is always in [0, 1]."""
    out = compute_bellwether_score({"NAPMPI": _fred(n=5, start=52.0, slope=0.0)})
    assert 0.0 <= out["composite_score"] <= 1.0


def test_bellwether_handles_swapped_columns_correctly() -> None:
    """By-name selection: a value-first / date-last frame still reads value."""
    macro = {"NAPMPI": _fred_swapped(n=5, start=52.0, slope=0.0)}
    out = compute_bellwether_score(macro)
    assert out["indicators"]["pmi"]["raw"] == pytest.approx(52.0)
