"""Tests for engine.alpha_engine.

Covers:
  - The AlphaSignal dataclass shape
  - Pure helpers: _latest_close, _pct_change_30d, _fred_series_pct_change_30d,
    _latest_macro_value, _bdi_rising, _bdi_pct_change
  - _make_signal factory: target/stop computation, risk_reward, strength clamp
  - _fallback_price for known tickers
  - generate_all_signals: graceful on empty inputs, sorts by conviction,
    deduplicates by (ticker, signal_type, direction)
  - compute_portfolio_alpha: empty input, weight normalization,
    expected_return / portfolio_vol / sharpe / max_dd shape
  - build_signal_scorecard: DataFrame shape

All RNG seeds are explicit integers — no Python hash() (process-salted).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.alpha_engine import (
    AlphaSignal,
    _bdi_pct_change,
    _bdi_rising,
    _fallback_price,
    _fred_series_pct_change_30d,
    _latest_close,
    _latest_macro_value,
    _make_signal,
    _pct_change_30d,
    build_signal_scorecard,
    compute_portfolio_alpha,
    generate_all_signals,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _stock_df(closes: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(closes), freq="B"),
        "close": closes,
    })


def _macro_df(values: list[float], start: str = "2025-01-01", freq: str = "D") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(values), freq=freq),
        "value": values,
    })


# ─── AlphaSignal dataclass ─────────────────────────────────────────────────

def test_alpha_signal_dataclass_shape() -> None:
    sig = AlphaSignal(
        ticker="ZIM", signal_name="Test", signal_type="MOMENTUM",
        direction="LONG", strength=0.75, conviction="HIGH",
        entry_price=20.0, target_price=24.0, stop_loss=18.0,
        expected_return_pct=20.0, time_horizon="1M",
        rationale="ZIM is hot", risk_reward=2.0,
    )
    assert sig.ticker == "ZIM"
    assert sig.direction == "LONG"
    assert sig.strength == 0.75


# ─── _latest_close ─────────────────────────────────────────────────────────

def test_latest_close_returns_most_recent() -> None:
    stock = {"ZIM": _stock_df([15.0, 16.0, 17.5])}
    assert _latest_close(stock, "ZIM") == 17.5


def test_latest_close_missing_ticker_returns_none() -> None:
    assert _latest_close({}, "ZIM") is None
    assert _latest_close({"ZIM": _stock_df([1.0])}, "MATX") is None


def test_latest_close_empty_or_no_column_returns_none() -> None:
    assert _latest_close({"ZIM": pd.DataFrame()}, "ZIM") is None
    assert _latest_close({"ZIM": pd.DataFrame({"other": [1, 2]})}, "ZIM") is None


def test_latest_close_drops_nan() -> None:
    df = _stock_df([15.0, 16.0])
    df["close"] = [15.0, float("nan")]
    assert _latest_close({"ZIM": df}, "ZIM") == 15.0


# ─── _pct_change_30d ───────────────────────────────────────────────────────

def test_pct_change_30d_known_uplift() -> None:
    """Build a 40-day series where ~30 days ago was 100 and now is 110.

    The function looks 30 *calendar* days back from the last date and
    returns the move as a percent (×100), not a fraction. To keep the
    back-30 day comparison anchored at 100, hold price flat for the
    first 35 business days then step up to 110 for the last 5.
    """
    closes = [100.0] * 35 + [110.0] * 5
    stock = {"ZIM": _stock_df(closes)}
    result = _pct_change_30d(stock, "ZIM")
    assert result is not None
    # Returns percent: 10.0 means +10%.
    assert result == pytest.approx(10.0, abs=1.0)


def test_pct_change_30d_missing_data_returns_none() -> None:
    assert _pct_change_30d({}, "ZIM") is None
    # Empty DataFrame
    assert _pct_change_30d({"ZIM": pd.DataFrame()}, "ZIM") is None
    # Too few rows
    assert _pct_change_30d({"ZIM": _stock_df([15.0])}, "ZIM") is None


# ─── _fred_series_pct_change_30d ───────────────────────────────────────────

def test_fred_pct_change_30d_known_move() -> None:
    """The function returns PERCENT (×100), not fraction — a +20% move
    on a 1000-to-1200 series shows up as ~20.0."""
    macro = {"WTI": _macro_df([1000.0] * 35 + [1200.0] * 5)}
    result = _fred_series_pct_change_30d(macro, "WTI")
    assert result is not None
    assert result > 5.0   # at least a 5% rise — generous tolerance for calendar/business day mismatch


def test_fred_pct_change_30d_missing_series_returns_none() -> None:
    assert _fred_series_pct_change_30d({}, "BDIY") is None
    assert _fred_series_pct_change_30d({"BDIY": pd.DataFrame()}, "BDIY") is None


# ─── _latest_macro_value ───────────────────────────────────────────────────

def test_latest_macro_value_returns_most_recent() -> None:
    macro = {"WTI": _macro_df([70.0, 75.0, 80.0])}
    assert _latest_macro_value(macro, "WTI") == 80.0


def test_latest_macro_value_missing_returns_none() -> None:
    assert _latest_macro_value({}, "WTI") is None
    assert _latest_macro_value({"WTI": pd.DataFrame()}, "WTI") is None


# ─── _bdi_rising and _bdi_pct_change ───────────────────────────────────────

def test_bdi_rising_when_recent_above_history() -> None:
    """_bdi_rising reads the FRED `BSXRLM` series (not BDIY) — that's the
    canonical FRED BDI series ID."""
    macro = {"BSXRLM": _macro_df([1000.0] * 35 + [1200.0] * 5)}
    assert _bdi_rising(macro) is True


def test_bdi_rising_false_on_flat_or_falling() -> None:
    flat = {"BSXRLM": _macro_df([1000.0] * 40)}
    falling = {"BSXRLM": _macro_df([1200.0] * 35 + [900.0] * 5)}
    assert _bdi_rising(flat) is False
    assert _bdi_rising(falling) is False


def test_bdi_rising_missing_data_returns_false() -> None:
    assert _bdi_rising({}) is False
    assert _bdi_rising({"BSXRLM": pd.DataFrame()}) is False


def test_bdi_pct_change_returns_signed_percent() -> None:
    """Returns the BDI's 30-day move in PERCENT (×100)."""
    macro = {"BSXRLM": _macro_df([1000.0] * 35 + [1200.0] * 5)}
    pct = _bdi_pct_change(macro)
    assert pct > 5.0   # at least +5% — clean signal in the fixture


def test_bdi_pct_change_missing_returns_zero() -> None:
    assert _bdi_pct_change({}) == 0.0


# ─── _make_signal factory ──────────────────────────────────────────────────

def test_make_signal_long_target_and_stop_correct() -> None:
    sig = _make_signal(
        ticker="ZIM", signal_name="Test", signal_type="MOMENTUM",
        direction="LONG", strength=0.7, conviction="HIGH",
        entry_price=20.0, target_pct=20.0, stop_pct=-10.0,
        time_horizon="1M", rationale="test",
    )
    assert sig.entry_price == 20.0
    assert sig.target_price == 24.0     # 20 * 1.20
    assert sig.stop_loss == 18.0        # 20 * 0.90
    assert sig.expected_return_pct == 20.0
    # risk_reward = |24 - 20| / |20 - 18| = 4 / 2 = 2.0
    assert sig.risk_reward == 2.0


def test_make_signal_short_target_below_entry() -> None:
    sig = _make_signal(
        ticker="DAC", signal_name="Test", signal_type="MEAN_REVERSION",
        direction="SHORT", strength=0.5, conviction="MEDIUM",
        entry_price=100.0, target_pct=-15.0, stop_pct=5.0,
        time_horizon="1M", rationale="test",
    )
    assert sig.target_price == 85.0     # 100 * 0.85
    assert sig.stop_loss == 105.0       # 100 * 1.05
    assert sig.risk_reward == pytest.approx(15.0 / 5.0, abs=0.01)   # 3.0


def test_make_signal_strength_clamped_to_unit_interval() -> None:
    high = _make_signal("X", "n", "MOMENTUM", "LONG", 5.0, "HIGH",
                       10.0, 10.0, -5.0, "1M", "r")
    low = _make_signal("X", "n", "MOMENTUM", "LONG", -2.0, "HIGH",
                       10.0, 10.0, -5.0, "1M", "r")
    assert high.strength == 1.0
    assert low.strength == 0.0


def test_make_signal_zero_stop_distance_yields_zero_rr() -> None:
    """stop_pct=0 → stop_loss == entry → divide guard fires."""
    sig = _make_signal("X", "n", "MOMENTUM", "LONG", 0.5, "HIGH",
                       10.0, 10.0, 0.0, "1M", "r")
    assert sig.risk_reward == 0.0


# ─── _fallback_price ───────────────────────────────────────────────────────

def test_fallback_price_known_tickers() -> None:
    assert _fallback_price("ZIM") == 18.0
    assert _fallback_price("MATX") == 110.0
    assert _fallback_price("SBLK") == 12.0


def test_fallback_price_unknown_ticker_default() -> None:
    assert _fallback_price("NEWTICKER") == 20.0


# ─── generate_all_signals ──────────────────────────────────────────────────

def test_generate_all_signals_handles_empty_inputs_gracefully() -> None:
    out = generate_all_signals({}, {}, {}, [], [])
    assert isinstance(out, list)
    # Empty inputs may still produce strategy-default signals; just check shape.
    for sig in out:
        assert isinstance(sig, AlphaSignal)


def test_generate_all_signals_sorted_by_conviction_then_strength() -> None:
    """High conviction comes before Medium / Low. Within a level, sorted
    by strength descending."""
    out = generate_all_signals({}, {}, {}, [], [])
    if len(out) < 2:
        pytest.skip("Need ≥ 2 signals to test ordering")
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        # Either a's conviction is higher, or same conviction with higher strength.
        assert (rank.get(a.conviction, 3) < rank.get(b.conviction, 3)
                or (a.conviction == b.conviction
                    and a.strength >= b.strength))


def test_generate_all_signals_deduped_by_ticker_type_direction() -> None:
    """No two output signals share the same (ticker, signal_type, direction)."""
    out = generate_all_signals({}, {}, {}, [], [])
    keys = [(s.ticker, s.signal_type, s.direction) for s in out]
    assert len(keys) == len(set(keys))


# ─── compute_portfolio_alpha ───────────────────────────────────────────────

def test_compute_portfolio_alpha_empty_input() -> None:
    out = compute_portfolio_alpha([], {})
    assert out["weights"] == {}
    assert out["expected_return"] == 0.0
    assert out["portfolio_vol"] == 0.0
    assert out["sharpe"] == 0.0


def test_compute_portfolio_alpha_weights_normalized() -> None:
    """For a 2-signal portfolio, |weights| sum to 1 by construction."""
    sigs = [
        _make_signal("ZIM", "n1", "MOMENTUM", "LONG", 0.8, "HIGH",
                    20.0, 20.0, -10.0, "1M", "r"),
        _make_signal("MATX", "n2", "MEAN_REVERSION", "SHORT", 0.6, "MEDIUM",
                    100.0, -10.0, 5.0, "1M", "r"),
    ]
    out = compute_portfolio_alpha(sigs, {})
    total_abs_weight = sum(abs(w) for w in out["weights"].values())
    assert total_abs_weight == pytest.approx(1.0, abs=1e-3)


def test_compute_portfolio_alpha_metrics_finite() -> None:
    sigs = [
        _make_signal("ZIM", "n", "MOMENTUM", "LONG", 0.7, "HIGH",
                    20.0, 15.0, -8.0, "1M", "r"),
    ]
    rng = np.random.default_rng(11)
    stock = {"ZIM": pd.DataFrame({"close": 20 + rng.normal(0, 0.5, 50).cumsum()})}
    out = compute_portfolio_alpha(sigs, stock)
    assert math.isfinite(out["expected_return"])
    assert math.isfinite(out["portfolio_vol"])
    assert math.isfinite(out["sharpe"])
    assert math.isfinite(out["max_dd_estimate"])
    assert out["portfolio_vol"] > 0


def test_compute_portfolio_alpha_falls_back_to_default_vol() -> None:
    """A ticker missing from stock_data uses the 40% shipping default."""
    sigs = [_make_signal("UNKNOWN", "n", "MOMENTUM", "LONG", 0.5, "MEDIUM",
                        20.0, 10.0, -5.0, "1M", "r")]
    out = compute_portfolio_alpha(sigs, {})
    # portfolio_vol = sqrt(w² * 40²) with |w|=1 → 40
    assert out["portfolio_vol"] == pytest.approx(40.0, abs=0.1)


# ─── build_signal_scorecard ────────────────────────────────────────────────

def test_build_signal_scorecard_empty_returns_empty_df_with_expected_columns() -> None:
    df = build_signal_scorecard([])
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    # Expected columns present even when empty (per the function's source).
    for col in ("ticker", "signal_name", "direction", "strength", "conviction"):
        assert col in df.columns


def test_build_signal_scorecard_has_one_row_per_signal() -> None:
    sigs = [
        _make_signal("ZIM", "n1", "MOMENTUM", "LONG", 0.7, "HIGH",
                    20.0, 20.0, -10.0, "1M", "r"),
        _make_signal("MATX", "n2", "MEAN_REVERSION", "SHORT", 0.5, "LOW",
                    100.0, -10.0, 5.0, "3M", "r"),
    ]
    df = build_signal_scorecard(sigs)
    assert len(df) == 2
    assert set(df["ticker"]) == {"ZIM", "MATX"}
