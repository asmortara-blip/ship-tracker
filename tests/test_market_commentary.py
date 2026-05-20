"""Pure-function tests for processing.market_commentary.

The ``_safe_last`` / ``_safe_pct_change`` helpers here handle BOTH freight
(``rate_usd_per_feu``) and FRED (``value``) schemas. Before the audit fix,
they reached for ``iloc[:, -1]`` immediately (which still missed the
freight schema, since `source` is the last column on those frames). The
patched helpers select by name first, falling back to the last column.

Tests pin:
  * freight frame returns rate (not source / not date),
  * FRED frame returns value,
  * pct-change is numeric and directional in both schemas,
  * None / empty inputs return None,
  * ``generate_daily_wrap`` and ``generate_forward_outlook`` integrate
    these helpers safely on empty input.

No Streamlit, no live network.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing.market_commentary import (
    _safe_last,
    _safe_pct_change,
    generate_daily_wrap,
    generate_forward_outlook,
)


# ── Frame builders ──────────────────────────────────────────────────────────


def _freight(n: int = 10, start: float = 2_000.0, slope: float = 10.0) -> pd.DataFrame:
    """Freight-shaped frame: [date, route_id, rate_usd_per_feu, source]."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "route_id": "transpacific_eb",
        "rate_usd_per_feu": np.arange(n, dtype=float) * slope + start,
        "source": "fixture",
    })


def _fred(n: int = 10, start: float = 100.0, slope: float = 1.0) -> pd.DataFrame:
    """FRED-shaped frame: [date, value]."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "value": np.arange(n, dtype=float) * slope + start,
    })


# ── _safe_last on both schemas ──────────────────────────────────────────────


def test_safe_last_freight_returns_rate() -> None:
    """A freight frame yields the rate column, not date and not 'fixture'."""
    df = _freight(n=8, start=1_500.0, slope=5.0)
    result = _safe_last(df)
    assert result is not None
    assert isinstance(result, float)
    # last = 1500 + 5*7 = 1535
    assert result == pytest.approx(1_535.0)


def test_safe_last_fred_returns_value() -> None:
    df = _fred(n=8, start=200.0, slope=2.0)
    result = _safe_last(df)
    assert result == pytest.approx(214.0)


def test_safe_last_picks_value_over_unrelated_last_col() -> None:
    """If the frame has both 'value' and a trailing string column, value wins."""
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=4, freq="D"),
        "value": [10.0, 20.0, 30.0, 40.0],
        "source": ["a", "b", "c", "d"],
    })
    # Without by-name selection, last col 'source' would crash on float().
    assert _safe_last(df) == pytest.approx(40.0)


def test_safe_last_falls_back_to_last_column_when_unnamed() -> None:
    """Without rate / value columns, the last column is used."""
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3, freq="D"),
        "metric": [7.0, 8.0, 9.0],
    })
    assert _safe_last(df) == pytest.approx(9.0)


def test_safe_last_series_and_empty() -> None:
    assert _safe_last(pd.Series([1.0, 2.0, 3.0])) == pytest.approx(3.0)
    assert _safe_last(None) is None
    assert _safe_last(pd.DataFrame()) is None


# ── _safe_pct_change on both schemas ────────────────────────────────────────


def test_safe_pct_change_freight_positive() -> None:
    """An ascending freight series → positive pct-change (in %)."""
    df = _freight(n=5, start=1_000.0, slope=100.0)
    pct = _safe_pct_change(df, periods=4)
    assert pct is not None
    # last=1400, prev=1000 → (1400-1000)/1000*100 = 40.0
    assert pct == pytest.approx(40.0)


def test_safe_pct_change_fred_negative() -> None:
    df = _fred(n=10, start=100.0, slope=-2.0)
    pct = _safe_pct_change(df, periods=5)
    assert pct is not None
    assert pct < 0


def test_safe_pct_change_zero_prev_returns_none() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3, freq="D"),
        "value": [0.0, 5.0, 10.0],
    })
    assert _safe_pct_change(df, periods=2) is None


def test_safe_pct_change_too_short_or_empty() -> None:
    assert _safe_pct_change(None) is None
    assert _safe_pct_change(pd.DataFrame()) is None
    assert _safe_pct_change(_fred(n=1), periods=2) is None


def test_safe_pct_change_series_input() -> None:
    pct = _safe_pct_change(pd.Series([100.0, 105.0, 110.0]), periods=2)
    assert pct == pytest.approx(10.0)


# ── generate_daily_wrap / generate_forward_outlook smoke tests ──────────────


def test_daily_wrap_empty_returns_well_formed_skeleton() -> None:
    """No inputs → a valid wrap with neutral tone, never a crash."""
    out = generate_daily_wrap({}, {}, {}, [], [])
    assert isinstance(out, dict)
    assert {"headline", "subhead", "body", "key_movers", "market_tone",
            "date", "timestamp", "stats"}.issubset(out)
    assert out["market_tone"] in {"BULLISH", "NEUTRAL", "BEARISH"}
    assert isinstance(out["body"], list)
    assert isinstance(out["key_movers"], list)


def test_daily_wrap_reads_freight_rate_columns_safely() -> None:
    """A freight dict triggers _safe_pct_change against rate_usd_per_feu.

    A positive slope must be summarised positively (no crash, no negative
    bias from picking the wrong column).
    """
    freight = {"transpacific_eb": _freight(n=5, start=1_000.0, slope=20.0)}
    out = generate_daily_wrap({}, freight, {}, [], [])
    assert out["stats"]["avg_freight_chg"] > 0


def test_daily_wrap_reads_fred_value_columns_safely() -> None:
    """Macro reads (VIX, crude, DXY) pass through _safe_last / _safe_pct_change."""
    macro = {
        "VIXCLS": _fred(n=5, start=15.0, slope=0.0),
        "DCOILWTICO": _fred(n=5, start=70.0, slope=0.0),
        "DEXCHUS": _fred(n=5, start=7.2, slope=0.01),
    }
    out = generate_daily_wrap({}, {}, macro, [], [])
    # Body may include macro paragraph; tone must be valid; no crash is the point.
    assert out["market_tone"] in {"BULLISH", "NEUTRAL", "BEARISH"}


def test_forward_outlook_empty_inputs() -> None:
    out = generate_forward_outlook(None, None, None)
    assert out["opportunities"] == []
    assert out["risks"] == []
    assert out["narrative"]


def test_forward_outlook_inverted_yield_curve_adds_macro_risk() -> None:
    """A negative T10Y2Y reading is surfaced as a macro risk."""
    macro = {"T10Y2Y": _fred(n=5, start=-0.5, slope=0.0)}
    out = generate_forward_outlook([], macro, {})
    assert any("Inverted yield curve" in r["title"] for r in out["risks"])


def test_forward_outlook_high_vix_adds_macro_risk() -> None:
    """A VIX reading above 25 surfaces as a volatility risk."""
    macro = {"VIXCLS": _fred(n=5, start=30.0, slope=0.0)}
    out = generate_forward_outlook([], macro, {})
    assert any("Elevated market volatility" in r["title"] for r in out["risks"])
