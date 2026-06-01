"""Regression tests for ui/tab_event_study price-frame coercion robustness.

Bug-hunt 2026-06-01: `_close_series_from_frame` raised `TypeError` on
MultiIndex-column or duplicate-`close`-column frames (because `frame[col]`
returned a DataFrame, not a Series), and the `_prices_by_ticker` call site in
`render()` was outside any try/except — so one malformed frame took down the
whole tab. The helper promises "return None, never raise"; these lock that in.

Pure helpers, fully offline (no Streamlit render, no network).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ui.tab_event_study import _close_series_from_frame, _prices_by_ticker


def _good_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=10)
    return pd.DataFrame({"close": np.linspace(10.0, 12.0, 10)}, index=dates)


def test_close_series_from_frame_handles_duplicate_close_columns() -> None:
    """Duplicate 'close' labels → frame['close'] is a DataFrame; must not raise."""
    dates = pd.bdate_range("2024-01-01", periods=10)
    dup = pd.DataFrame(
        np.c_[np.linspace(9, 11, 10), np.linspace(9, 11, 10)],
        index=dates, columns=["close", "close"],
    )
    out = _close_series_from_frame(dup)        # must not raise
    assert out is None or isinstance(out, pd.Series)


def test_close_series_from_frame_handles_multiindex_columns() -> None:
    """A MultiIndex with a 'close' level-0 must not raise."""
    dates = pd.bdate_range("2024-01-01", periods=10)
    mi = pd.DataFrame(
        np.c_[np.linspace(8, 10, 10), np.linspace(1, 2, 10)],
        index=dates,
        columns=pd.MultiIndex.from_tuples([("close", "X"), ("vol", "X")]),
    )
    out = _close_series_from_frame(mi)         # must not raise
    assert out is None or isinstance(out, pd.Series)


def test_prices_by_ticker_skips_bad_frames_and_keeps_good_ones() -> None:
    """One malformed frame among good ones must never crash the build; the good
    ticker is still returned."""
    dates = pd.bdate_range("2024-01-01", periods=10)
    dup = pd.DataFrame(
        np.c_[np.linspace(9, 11, 10), np.linspace(9, 11, 10)],
        index=dates, columns=["close", "close"],
    )
    mi = pd.DataFrame(
        np.c_[np.linspace(8, 10, 10), np.linspace(1, 2, 10)],
        index=dates,
        columns=pd.MultiIndex.from_tuples([("close", "X"), ("vol", "X")]),
    )
    out = _prices_by_ticker({"GOOD": _good_frame(), "DUP": dup, "MI": mi, "NONE": None})
    assert isinstance(out, dict)
    assert "GOOD" in out and isinstance(out["GOOD"], pd.Series)


def test_prices_by_ticker_empty_or_non_dict_returns_empty() -> None:
    assert _prices_by_ticker(None) == {}
    assert _prices_by_ticker({}) == {}
    assert _prices_by_ticker([1, 2, 3]) == {}
