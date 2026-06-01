"""Pure-function tests for data.freight_scraper's synthetic fallback.

When every live FBX strategy fails, ``_synthetic_fallback`` must hand the
forecasting modules a genuine multi-row TIME SERIES per route — not a single
point-in-time rate — while keeping the exact schema of the real scrape path.
These tests pin the row count, the canonical column set, determinism and the
provenance marker. No Streamlit, no live HTTP.
"""
from __future__ import annotations

import pandas as pd

from data.freight_scraper import (
    _single_fallback,
    _synthetic_fallback,
    _synthetic_series,
)
from data.normalizer import FREIGHT_COLS


# ── Shape: the fallback is now a time series ─────────────────────────────────


def test_synthetic_fallback_returns_a_dataframe_per_route() -> None:
    """The fallback maps every route_id to a non-empty DataFrame."""
    out = _synthetic_fallback()
    assert isinstance(out, dict)
    assert len(out) > 0
    assert all(isinstance(df, pd.DataFrame) for df in out.values())


def test_synthetic_fallback_is_a_multi_row_time_series() -> None:
    """Each route frame carries ~120 rows of history, not a single point.

    Forecasters need history to fit; a one-row stub gave them nothing.
    """
    out = _synthetic_fallback()
    for route_id, df in out.items():
        assert len(df) > 100, f"{route_id} has only {len(df)} rows"
        # Dates are distinct and strictly increasing — a real series.
        assert df["date"].is_monotonic_increasing
        assert df["date"].nunique() == len(df)


def test_synthetic_fallback_keeps_the_canonical_schema() -> None:
    """Columns match FREIGHT_COLS exactly so downstream consumers are unchanged."""
    out = _synthetic_fallback()
    for route_id, df in out.items():
        assert list(df.columns) == FREIGHT_COLS, (
            f"{route_id} schema drifted: {list(df.columns)}"
        )


def test_synthetic_fallback_rates_are_positive_and_vary() -> None:
    """A mean-reverting walk produces positive, non-constant rates."""
    out = _synthetic_fallback()
    for route_id, df in out.items():
        rates = df["rate_usd_per_feu"]
        assert (rates > 0).all(), f"{route_id} has non-positive rates"
        assert rates.nunique() > 5, f"{route_id} rates are nearly constant"


def test_synthetic_fallback_carries_synthetic_provenance() -> None:
    """The ``source`` column stays a synthetic marker for DEMO labelling."""
    out = _synthetic_fallback()
    for route_id, df in out.items():
        sources = set(df["source"].astype(str).str.lower().unique())
        assert sources <= {"synthetic", "mock", "fallback"}, (
            f"{route_id} lost its synthetic provenance: {sources}"
        )


# ── Determinism ──────────────────────────────────────────────────────────────


def test_synthetic_series_is_deterministic() -> None:
    """Same route_id → identical series within a session (stability contract)."""
    a = _synthetic_series("transpacific_eb", "FBX01")
    b = _synthetic_series("transpacific_eb", "FBX01")
    pd.testing.assert_frame_equal(a, b)


def test_synthetic_fallback_is_deterministic() -> None:
    """The whole fallback payload reproduces byte-for-byte."""
    a = _synthetic_fallback()
    b = _synthetic_fallback()
    assert a.keys() == b.keys()
    for route_id in a:
        pd.testing.assert_frame_equal(a[route_id], b[route_id])


def test_distinct_routes_get_distinct_series() -> None:
    """Different routes draw different walks (not one frozen shape)."""
    a = _synthetic_series("transpacific_eb", "FBX01")
    b = _synthetic_series("asia_europe", "FBX03")
    assert not a["rate_usd_per_feu"].equals(b["rate_usd_per_feu"])


# ── _single_fallback gap-fill is also a series ───────────────────────────────


def test_single_fallback_is_also_a_time_series() -> None:
    """A gap-filled route still gets full history, not a lone point."""
    df = _single_fallback("transpacific_eb", "FBX01")
    assert list(df.columns) == FREIGHT_COLS
    assert len(df) > 100
