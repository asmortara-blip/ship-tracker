"""Pure-function tests for processing.index_tracker.

These tests pin the by-name column selection in ``_safe_series``: a recent
audit fix replaced ``iloc[:, 0]`` (which silently grabbed the *date* column
from canonical freight / FRED frames) with explicit lookups for
``rate_usd_per_feu`` (freight) and ``value`` (FRED). The tests below
verify that the rate / value column is returned for both schemas, that
Series inputs survive numeric coercion, and that None / empty inputs
yield None without raising.

A handful of higher-level ``compute_index_dashboard`` invariants are also
pinned — the dashboard must produce one entry per ``INDEX_DEFINITIONS`` key,
unchanged schema, no crashes on missing data, and BDI resolution via the
FRED ``BDIY`` macro key.

No Streamlit, no network. Synthetic frames are built inline with
``pd.date_range`` + numpy ranges, mirroring the realistic feed shape.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing.index_tracker import (
    INDEX_DEFINITIONS,
    _safe_series,
    compute_index_dashboard,
)


# ── Synthetic frame builders ────────────────────────────────────────────────


def _freight_frame(n: int = 30, start: float = 2_000.0, slope: float = 5.0) -> pd.DataFrame:
    """A freight-shaped DataFrame: [date, route_id, rate_usd_per_feu, source]."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "route_id": "transpacific_eb",
        "rate_usd_per_feu": np.arange(n, dtype=float) * slope + start,
        "source": "fixture",
    })


def _fred_frame(n: int = 30, start: float = 1_500.0, slope: float = 2.0) -> pd.DataFrame:
    """A FRED-shaped DataFrame: [date, value]."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "value": np.arange(n, dtype=float) * slope + start,
    })


# ── _safe_series: by-name column selection ──────────────────────────────────


def test_safe_series_freight_returns_rate_not_date() -> None:
    """A freight frame must yield the rate_usd_per_feu column, never date."""
    df = _freight_frame(n=10, start=1_000.0, slope=10.0)
    series = _safe_series({"k": df}, "k")
    assert series is not None
    assert len(series) == 10
    # First value should be the rate start (1000.0), not a pandas Timestamp.
    assert float(series.iloc[0]) == pytest.approx(1_000.0)
    assert float(series.iloc[-1]) == pytest.approx(1_090.0)
    # The result must be a numeric series, not datetime / object.
    assert pd.api.types.is_numeric_dtype(series)


def test_safe_series_fred_returns_value_not_date() -> None:
    """A FRED frame must yield the value column, never date (the column 0 bug)."""
    df = _fred_frame(n=15, start=500.0, slope=1.0)
    series = _safe_series({"BDIY": df}, "BDIY")
    assert series is not None
    assert len(series) == 15
    assert float(series.iloc[0]) == pytest.approx(500.0)
    assert float(series.iloc[-1]) == pytest.approx(514.0)
    assert pd.api.types.is_numeric_dtype(series)


def test_safe_series_swapped_column_order_still_picks_value() -> None:
    """Column order is irrelevant — by-name selection survives swapped layouts."""
    n = 8
    dates = pd.date_range("2026-02-01", periods=n, freq="D")
    df = pd.DataFrame({"value": np.arange(n, dtype=float) + 100.0, "date": dates})
    series = _safe_series({"x": df}, "x")
    assert series is not None
    # Must be numeric values starting at 100.0, not Timestamps.
    assert float(series.iloc[0]) == pytest.approx(100.0)
    assert pd.api.types.is_numeric_dtype(series)


def test_safe_series_series_input_is_coerced_numeric() -> None:
    """A pd.Series input is accepted and coerced to numeric, NaNs dropped."""
    raw = pd.Series([1.0, 2.0, "3.5", "garbage", 5.0])
    series = _safe_series({"s": raw}, "s")
    assert series is not None
    # 'garbage' is dropped, '3.5' is parsed → 4 numeric values.
    assert len(series) == 4
    assert pd.api.types.is_numeric_dtype(series)
    assert float(series.iloc[-1]) == pytest.approx(5.0)


def test_safe_series_numeric_fallback_for_unnamed_frames() -> None:
    """A frame missing both expected columns falls back to first numeric column."""
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5, freq="D"),
        "weird_col": [10.0, 11.0, 12.0, 13.0, 14.0],
    })
    series = _safe_series({"x": df}, "x")
    assert series is not None
    assert float(series.iloc[0]) == pytest.approx(10.0)
    assert float(series.iloc[-1]) == pytest.approx(14.0)


def test_safe_series_none_input_returns_none() -> None:
    assert _safe_series({}, "missing") is None
    assert _safe_series({"x": None}, "x") is None


def test_safe_series_empty_frame_returns_none() -> None:
    empty_freight = pd.DataFrame(columns=["date", "rate_usd_per_feu"])
    assert _safe_series({"x": empty_freight}, "x") is None
    empty_fred = pd.DataFrame(columns=["date", "value"])
    assert _safe_series({"x": empty_fred}, "x") is None


def test_safe_series_no_numeric_column_returns_none() -> None:
    """A frame with no numeric columns at all yields None, not a crash."""
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3, freq="D"),
        "label": ["a", "b", "c"],
    })
    assert _safe_series({"x": df}, "x") is None


def test_safe_series_drops_nan_values() -> None:
    """NaN values must be dropped to keep downstream stats safe."""
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=4, freq="D"),
        "value": [1.0, float("nan"), 3.0, float("nan")],
    })
    series = _safe_series({"x": df}, "x")
    assert series is not None
    assert len(series) == 2
    assert list(series) == [1.0, 3.0]


# ── compute_index_dashboard: integration with _safe_series ──────────────────


def test_dashboard_returns_one_entry_per_definition() -> None:
    """The dashboard always emits one row per INDEX_DEFINITIONS key."""
    results = compute_index_dashboard({}, {})
    assert isinstance(results, list)
    assert {r["key"] for r in results} == set(INDEX_DEFINITIONS.keys())


def test_dashboard_marks_no_data_when_empty() -> None:
    """Empty data sets `has_data=False` and `momentum='N/A'`, no crash."""
    results = compute_index_dashboard({}, {})
    for row in results:
        assert row["has_data"] is False
        assert row["momentum"] == "N/A"
        assert row["current"] is None


def test_dashboard_resolves_bdi_via_bdiy_macro_key() -> None:
    """BDI is sourced from the FRED ``BDIY`` macro frame when no freight key exists."""
    macro = {"BDIY": _fred_frame(n=60, start=1_500.0, slope=5.0)}
    results = compute_index_dashboard({}, macro)
    bdi_row = next(r for r in results if r["key"] == "BDI")
    assert bdi_row["has_data"] is True
    # Current is the LAST numeric value, never a Timestamp.
    assert isinstance(bdi_row["current"], float)
    assert bdi_row["current"] == pytest.approx(1_500.0 + 5.0 * 59)
    assert bdi_row["momentum"] in {
        "Strong Rally", "Uptrend", "Consolidating", "Downtrend", "Sharp Decline",
    }


def test_dashboard_picks_rate_column_from_freight_frame() -> None:
    """SCFI sourced from a freight frame uses rate_usd_per_feu, not date."""
    freight = {"SCFI": _freight_frame(n=60, start=2_000.0, slope=1.0)}
    results = compute_index_dashboard(freight, {})
    scfi_row = next(r for r in results if r["key"] == "SCFI")
    assert scfi_row["has_data"] is True
    assert isinstance(scfi_row["current"], float)
    assert scfi_row["current"] == pytest.approx(2_059.0)
