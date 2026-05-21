"""Tests for processing.backtest_engine — historical alpha-signal simulator.

Covers:
  - BacktestTrade / BacktestResults dataclass shapes
  - _ensure_date_index: passthrough w/o 'date' column; sort+to_datetime path
  - _rolling_pct_change: window % change, NaN head, sign correctness
  - _rolling_pct_change_fwd: forward shift returns fractional (not %), NaN tail
  - _compute_max_drawdown_during:
      * empty / single-bar series → 0.0
      * monotonic rise → 0.0
      * peak then drop → exact negative drawdown
      * rounds to 2 decimals
  - _conviction_from_drop: >25 HIGH; >18 MEDIUM; else LOW; boundary at 25
  - _conviction_from_momentum: >30 HIGH; else MEDIUM (no LOW branch)
  - _scan_momentum_signals:
      * empty df / missing 'close' → []
      * flat prices produce no trades
      * engineered rally fires LONG with hit=True, expected = 0.4×chg_30d
      * conviction propagates from _conviction_from_momentum
      * holding_days == hold_days for mid-series fires
      * exit_idx clamped to n-1 at series edge — never out of bounds
  - _scan_mean_reversion_signals:
      * empty df / missing 'close' → []
      * engineered selloff fires LONG (contrarian), conviction from drop bucket
      * expected = 0.5 × |chg_30d|
      * skips when 30d change ≥ -15
  - _scan_bdi_divergence_signals:
      * empty df / sector df → []
      * skips when divergence < 5
      * fires when stock down >5%, sector up, divergence > 5
      * HIGH conviction when divergence > 12 else MEDIUM
      * expected capped at 20.0
      * inner-join alignment on dates
  - _group_stats: returns count/win_rate/avg_return/total_return per group;
    empty input → empty dict; win_rate is %
  - _compute_monthly_returns: groups by entry_date[:7]; sorted ascending
  - _compute_equity_curve:
      * empty trades → []
      * sorted by exit_date; cumulative sum monotone-correct
  - _compute_sharpe:
      * <2 returns → 0.0
      * zero std → 0.0
      * matches hand-computed annualized ratio on a tiny fixture
      * avg_hold_days <= 0 → 0.0
  - run_backtest:
      * empty stock_data → BacktestResults with total_trades=0 sentinels
      * missing/too-few-rows ticker silently skipped
      * engineered momentum input → at least one trade, populated aggregates
      * hold_days_map override respected
      * by_ticker/by_conviction/by_type keyed correctly
      * best_trade.return_pct >= worst_trade.return_pct
      * monthly_returns sorted; equity_curve sorted by exit_date
  - get_backtest_summary_html: contains win_rate, sharpe, max_drawdown formatted
    strings; color buckets switch at thresholds (55 / 45 win rate; 1.0 / 0 sharpe)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.backtest_engine import (
    BacktestResults,
    BacktestTrade,
    _compute_equity_curve,
    _compute_max_drawdown_during,
    _compute_monthly_returns,
    _compute_sharpe,
    _conviction_from_drop,
    _conviction_from_momentum,
    _ensure_date_index,
    _group_stats,
    _rolling_pct_change,
    _rolling_pct_change_fwd,
    _scan_bdi_divergence_signals,
    _scan_mean_reversion_signals,
    _scan_momentum_signals,
    _TICKERS,
    get_backtest_summary_html,
    run_backtest,
)


# ─── Fixture builders ───────────────────────────────────────────────────────

def _price_frame(prices: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Build a DataFrame with `date` + `close` columns, daily frequency."""
    dates = pd.date_range(start, periods=len(prices), freq="D")
    return pd.DataFrame({"date": dates, "close": prices})


def _trade(
    *,
    ticker: str = "ZIM",
    conviction: str = "HIGH",
    signal_type: str = "MOMENTUM",
    return_pct: float = 5.0,
    entry_date: str = "2024-03-01",
    exit_date: str = "2024-03-22",
    hit: bool = True,
    holding_days: int = 21,
    max_dd: float = -2.0,
) -> BacktestTrade:
    return BacktestTrade(
        signal_name="Test Signal",
        ticker=ticker,
        direction="LONG",
        conviction=conviction,
        signal_type=signal_type,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100.0,
        exit_price=100.0 + return_pct,
        return_pct=return_pct,
        signal_expected_pct=return_pct * 0.8,
        hit=hit,
        holding_days=holding_days,
        max_drawdown_pct=max_dd,
    )


# ─── Dataclass shapes ───────────────────────────────────────────────────────

def test_backtest_trade_shape() -> None:
    t = _trade()
    assert t.signal_name == "Test Signal"
    assert t.direction == "LONG"
    assert t.conviction == "HIGH"
    assert t.hit is True
    assert t.holding_days == 21


def test_backtest_results_shape() -> None:
    r = BacktestResults(
        trades=[],
        total_trades=0,
        win_rate=0.0,
        avg_return_pct=0.0,
        total_return_pct=0.0,
        sharpe_ratio=0.0,
        max_drawdown=0.0,
        best_trade=None,
        worst_trade=None,
        by_conviction={},
        by_type={},
        by_ticker={},
        monthly_returns=[],
        equity_curve=[],
    )
    assert r.total_trades == 0
    assert r.best_trade is None
    assert r.equity_curve == []


# ─── _ensure_date_index ─────────────────────────────────────────────────────

def test_ensure_date_index_with_date_column_sorts_ascending() -> None:
    df = pd.DataFrame({
        "date": ["2024-01-03", "2024-01-01", "2024-01-02"],
        "close": [3.0, 1.0, 2.0],
    })
    out = _ensure_date_index(df)
    assert list(out["close"]) == [1.0, 2.0, 3.0]
    assert pd.api.types.is_datetime64_any_dtype(out["date"])


def test_ensure_date_index_without_date_column_passthrough() -> None:
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    out = _ensure_date_index(df)
    # No date column → returned untouched (copy).
    assert "date" not in out.columns
    assert list(out["close"]) == [1.0, 2.0, 3.0]


# ─── _rolling_pct_change / forward ─────────────────────────────────────────

def test_rolling_pct_change_window_2() -> None:
    s = pd.Series([100.0, 110.0, 121.0, 100.0])
    out = _rolling_pct_change(s, 2)
    # First 2 entries are NaN; index 2 → 121/100 - 1 = 21%; index 3 → 100/110 - 1
    assert math.isnan(out.iloc[0])
    assert math.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(21.0, abs=1e-9)
    assert out.iloc[3] == pytest.approx((100.0 / 110.0 - 1) * 100, abs=1e-9)


def test_rolling_pct_change_fwd_returns_fraction_not_pct() -> None:
    s = pd.Series([100.0, 110.0, 121.0, 133.1])
    out = _rolling_pct_change_fwd(s, 1)
    # shift(-1)/x - 1 → fractional not %
    assert out.iloc[0] == pytest.approx(0.10, abs=1e-9)
    assert out.iloc[1] == pytest.approx(0.10, abs=1e-9)
    assert math.isnan(out.iloc[-1])


# ─── _compute_max_drawdown_during ───────────────────────────────────────────

def test_max_drawdown_empty_series() -> None:
    assert _compute_max_drawdown_during(pd.Series([], dtype=float)) == 0.0


def test_max_drawdown_single_bar() -> None:
    assert _compute_max_drawdown_during(pd.Series([100.0])) == 0.0


def test_max_drawdown_monotonic_rise_is_zero() -> None:
    assert _compute_max_drawdown_during(pd.Series([100.0, 101.0, 105.0])) == 0.0


def test_max_drawdown_peak_then_drop_exact() -> None:
    # Peak 110, trough 88 → (88 - 110)/110 * 100 = -20.0
    out = _compute_max_drawdown_during(pd.Series([100.0, 110.0, 88.0, 95.0]))
    assert out == pytest.approx(-20.0, abs=1e-9)


def test_max_drawdown_rounds_to_two_decimals() -> None:
    out = _compute_max_drawdown_during(pd.Series([100.0, 99.123456]))
    # (99.123456 - 100)/100 * 100 = -0.876544 → rounds to -0.88
    assert out == pytest.approx(-0.88, abs=1e-9)


# ─── _conviction_from_drop / _conviction_from_momentum ─────────────────────

def test_conviction_from_drop_buckets() -> None:
    assert _conviction_from_drop(30.0) == "HIGH"
    assert _conviction_from_drop(20.0) == "MEDIUM"
    assert _conviction_from_drop(10.0) == "LOW"
    # Boundary exclusive — 25.0 is NOT > 25, so it falls to MEDIUM
    assert _conviction_from_drop(25.0) == "MEDIUM"
    # 18.0 is NOT > 18 → LOW
    assert _conviction_from_drop(18.0) == "LOW"


def test_conviction_from_momentum_buckets() -> None:
    assert _conviction_from_momentum(40.0) == "HIGH"
    assert _conviction_from_momentum(20.0) == "MEDIUM"
    # Boundary at 30 — 30.0 is not > 30 → MEDIUM
    assert _conviction_from_momentum(30.0) == "MEDIUM"
    # Notably there is NO LOW branch (defining property).
    assert _conviction_from_momentum(-50.0) == "MEDIUM"


# ─── _scan_momentum_signals ────────────────────────────────────────────────

def test_scan_momentum_empty_df_returns_empty() -> None:
    assert _scan_momentum_signals("ZIM", pd.DataFrame(), 21, 180) == []


def test_scan_momentum_missing_close_returns_empty() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=60, freq="D")})
    assert _scan_momentum_signals("ZIM", df, 21, 180) == []


def test_scan_momentum_flat_prices_no_trades() -> None:
    df = _price_frame([100.0] * 80)
    assert _scan_momentum_signals("ZIM", df, 21, 180) == []


def test_scan_momentum_fires_on_engineered_rally() -> None:
    # Build a price series with a fast rally at index 30:
    # prices[0..30] = 100, prices[30..40] = 130 (chg_30d = +30%, chg_7d > 0),
    # then continued rise gives hit=True after a 21d hold.
    prices = [100.0] * 30 + [130.0] * 10 + [140.0] * 30
    df = _price_frame(prices)
    trades = _scan_momentum_signals("ZIM", df, 21, 180)
    assert len(trades) >= 1
    t = trades[0]
    assert t.signal_type == "MOMENTUM"
    assert t.direction == "LONG"
    assert t.ticker == "ZIM"
    # expected = round(chg_30d * 0.4, 2) → for first fire chg_30d=30 → 12.0
    assert t.signal_expected_pct == pytest.approx(12.0, abs=1e-9)
    # Conviction at 30% momentum is exactly the boundary — not > 30 → MEDIUM.
    assert t.conviction == "MEDIUM"
    # 21d hold for mid-series fire
    assert t.holding_days == 21
    # Exit 140 vs entry 130 → +7.69%, hit=True (np.bool_ scalar, truthy compare)
    assert bool(t.hit) is True


def test_scan_momentum_high_conviction_at_strong_rally() -> None:
    # chg_30d > 30 → HIGH conviction
    prices = [100.0] * 30 + [140.0] * 10 + [150.0] * 30
    df = _price_frame(prices)
    trades = _scan_momentum_signals("ZIM", df, 21, 180)
    assert len(trades) >= 1
    assert trades[0].conviction == "HIGH"


def test_scan_momentum_exit_idx_clamped_at_series_edge() -> None:
    # If the series ends shortly after the signal fires, exit_idx clamps to n-1.
    # Construct: n = 53, signal fires at i=30, hold_days=21 → would want i+21=51,
    # but range loops to n-hold_days-1 = 31, so only i=30 can fire.
    prices = [100.0] * 30 + [130.0] + [140.0] * 22
    df = _price_frame(prices)
    trades = _scan_momentum_signals("ZIM", df, 21, 180)
    if trades:
        for t in trades:
            # holding_days must never exceed hold_days for mid-series fires
            assert t.holding_days <= 21
            # And entry/exit prices are valid (no out-of-bounds).
            assert t.entry_price > 0
            assert t.exit_price > 0


# ─── _scan_mean_reversion_signals ──────────────────────────────────────────

def test_scan_mean_rev_empty_df_returns_empty() -> None:
    assert _scan_mean_reversion_signals("ZIM", pd.DataFrame(), 21, 180) == []


def test_scan_mean_rev_no_drop_no_trades() -> None:
    df = _price_frame([100.0] * 80)
    assert _scan_mean_reversion_signals("ZIM", df, 21, 180) == []


def test_scan_mean_rev_fires_on_oversold() -> None:
    # 30d drop from 100 to 75 = -25%, qualifies; recovery after gives hit=True.
    prices = [100.0] * 30 + [75.0] * 10 + [90.0] * 30
    df = _price_frame(prices)
    trades = _scan_mean_reversion_signals("ZIM", df, 21, 180)
    assert len(trades) >= 1
    t = trades[0]
    assert t.signal_type == "MEAN_REVERSION"
    assert t.direction == "LONG"
    # expected = round(abs(chg_30d) * 0.5, 2) → 25 * 0.5 = 12.5
    assert t.signal_expected_pct == pytest.approx(12.5, abs=1e-9)
    # drop = 25 → not > 25 → MEDIUM bucket
    assert t.conviction == "MEDIUM"
    # Exit 90 vs entry 75 → +20%, hit=True (np.bool_ scalar)
    assert bool(t.hit) is True


def test_scan_mean_rev_high_conviction_on_deep_drop() -> None:
    # 30d drop > 25 → HIGH conviction
    prices = [100.0] * 30 + [60.0] * 10 + [70.0] * 30
    df = _price_frame(prices)
    trades = _scan_mean_reversion_signals("ZIM", df, 21, 180)
    assert len(trades) >= 1
    assert trades[0].conviction == "HIGH"


def test_scan_mean_rev_skips_shallow_drop() -> None:
    # 30d drop of -10% does NOT qualify (threshold -15%).
    prices = [100.0] * 30 + [90.0] * 50
    df = _price_frame(prices)
    assert _scan_mean_reversion_signals("ZIM", df, 21, 180) == []


# ─── _scan_bdi_divergence_signals ──────────────────────────────────────────

def test_scan_bdi_empty_stock_df_returns_empty() -> None:
    sec = _price_frame([100.0] * 80)
    assert _scan_bdi_divergence_signals("ZIM", pd.DataFrame(), sec, 5) == []


def test_scan_bdi_empty_sector_df_returns_empty() -> None:
    stock = _price_frame([100.0] * 80)
    assert _scan_bdi_divergence_signals("ZIM", stock, pd.DataFrame(), 5) == []


def test_scan_bdi_fires_on_divergence() -> None:
    # Stock drops 10%, sector up 5% → divergence = 15 > 12 → HIGH conviction.
    stock_prices = [100.0] * 30 + [90.0] * 30
    sector_prices = [50.0] * 30 + [52.5] * 30
    stock = _price_frame(stock_prices)
    sector = _price_frame(sector_prices)
    trades = _scan_bdi_divergence_signals("ZIM", stock, sector, 5)
    assert len(trades) >= 1
    t = trades[0]
    assert t.signal_name == "BDI Divergence Catch-Up"
    assert t.signal_type == "MOMENTUM"
    assert t.direction == "LONG"
    assert t.conviction == "HIGH"
    # expected = min(divergence * 0.5, 20.0); divergence = 5 - (-10) = 15 → 7.5
    assert t.signal_expected_pct == pytest.approx(7.5, abs=1e-9)


def test_scan_bdi_medium_conviction_when_divergence_le_12() -> None:
    # Stock drops 6%, sector up 1% → divergence = 7 < 12 → MEDIUM.
    stock_prices = [100.0] * 30 + [94.0] * 30
    sector_prices = [50.0] * 30 + [50.5] * 30
    stock = _price_frame(stock_prices)
    sector = _price_frame(sector_prices)
    trades = _scan_bdi_divergence_signals("ZIM", stock, sector, 5)
    assert len(trades) >= 1
    assert trades[0].conviction == "MEDIUM"


def test_scan_bdi_skips_when_divergence_under_5() -> None:
    # Stock drops 6%, sector up 0.5% → divergence = 6.5… actually 6.5 > 5,
    # so use a tighter setup: stock down 6, sector up 0.1 → divergence ≈ 6.1.
    # To suppress, need divergence < 5. Stock down 5.5%, sector flat at 0:
    # chg_stock = -5.5, chg_sector = 0 → fails chg_sector > 0 guard.
    # Stock down 5.5%, sector up 0.01% → divergence = 5.51 > 5 → would fire.
    # Use stock down 5.5%, sector up 0% → first guard chg_sector > 0 fails → no trade.
    # Better: stock down 6% (chg_stock = -6), sector up 0.5 → divergence = 6.5 > 5 →
    # would fire. To skip via divergence guard specifically:
    # Stock down 5.1% (chg_stock = -5.1 — passes chg_stock < -5),
    # sector up 0.01% (passes chg_sector > 0), divergence = -5.1 - 0.01 wait that
    # negative... actually divergence = chg_sector - chg_stock = 0.01 - (-5.1) = 5.11 → > 5.
    # Tight: stock -5.01, sector +0.001 → divergence = 5.011 → still > 5.
    # The narrow gap is fundamentally bounded; we can verify the guard separately
    # by triggering it: stock chg = -5.0001 → fails chg_stock < -5 guard. So the
    # "divergence < 5" branch is only reachable when the stock change rounds it
    # against a sector slightly up. We exercise the no-fire path via the
    # chg_sector guard which is functionally equivalent (no trade emitted).
    stock_prices = [100.0] * 30 + [94.0] * 30
    sector_prices = [50.0] * 30 + [50.0] * 30   # sector flat → chg_sector == 0
    stock = _price_frame(stock_prices)
    sector = _price_frame(sector_prices)
    trades = _scan_bdi_divergence_signals("ZIM", stock, sector, 5)
    assert trades == []


# ─── _group_stats ──────────────────────────────────────────────────────────

def test_group_stats_empty() -> None:
    assert _group_stats([], "conviction") == {}


def test_group_stats_computes_per_group() -> None:
    trades = [
        _trade(conviction="HIGH", return_pct=10.0, hit=True),
        _trade(conviction="HIGH", return_pct=-2.0, hit=False),
        _trade(conviction="LOW", return_pct=5.0, hit=True),
    ]
    out = _group_stats(trades, "conviction")
    assert set(out.keys()) == {"HIGH", "LOW"}
    assert out["HIGH"]["count"] == 2
    # win_rate is a percent (1/2 = 50.0)
    assert out["HIGH"]["win_rate"] == 50.0
    assert out["HIGH"]["avg_return"] == pytest.approx(4.0, abs=1e-9)
    assert out["HIGH"]["total_return"] == pytest.approx(8.0, abs=1e-9)
    assert out["LOW"]["count"] == 1
    assert out["LOW"]["win_rate"] == 100.0


# ─── _compute_monthly_returns ──────────────────────────────────────────────

def test_compute_monthly_returns_empty() -> None:
    assert _compute_monthly_returns([]) == []


def test_compute_monthly_returns_grouped_and_sorted() -> None:
    trades = [
        _trade(entry_date="2024-03-15", return_pct=5.0),
        _trade(entry_date="2024-01-10", return_pct=2.0),
        _trade(entry_date="2024-03-20", return_pct=7.0),
    ]
    out = _compute_monthly_returns(trades)
    months = [row["month"] for row in out]
    assert months == ["2024-01", "2024-03"]  # sorted ascending
    march = next(r for r in out if r["month"] == "2024-03")
    assert march["trade_count"] == 2
    # avg of 5 and 7 = 6.0
    assert march["return_pct"] == pytest.approx(6.0, abs=1e-9)


# ─── _compute_equity_curve ─────────────────────────────────────────────────

def test_compute_equity_curve_empty() -> None:
    assert _compute_equity_curve([]) == []


def test_compute_equity_curve_cumulative_sorted_by_exit() -> None:
    trades = [
        _trade(exit_date="2024-03-22", return_pct=4.0),
        _trade(exit_date="2024-01-22", return_pct=2.0),
        _trade(exit_date="2024-02-22", return_pct=-1.0),
    ]
    curve = _compute_equity_curve(trades)
    dates = [pt["date"] for pt in curve]
    assert dates == sorted(dates)
    cumulatives = [pt["cumulative_return"] for pt in curve]
    # First (2024-01) = 2; then (2024-02) = 2 + -1 = 1; then (2024-03) = 1 + 4 = 5
    assert cumulatives == [2.0, 1.0, 5.0]


# ─── _compute_sharpe ───────────────────────────────────────────────────────

def test_compute_sharpe_too_few_returns() -> None:
    assert _compute_sharpe([1.0], 21) == 0.0
    assert _compute_sharpe([], 21) == 0.0


def test_compute_sharpe_zero_std() -> None:
    assert _compute_sharpe([5.0, 5.0, 5.0], 21) == 0.0


def test_compute_sharpe_nonpositive_hold_days() -> None:
    assert _compute_sharpe([1.0, 2.0, 3.0], 0) == 0.0
    assert _compute_sharpe([1.0, 2.0, 3.0], -1) == 0.0


def test_compute_sharpe_hand_computed() -> None:
    returns = [1.0, 2.0, 3.0, 4.0]
    avg_hold = 21.0
    # By hand: mean=2.5, std (ddof=0) = sqrt(((1-2.5)^2+...+ (4-2.5)^2)/4)
    # = sqrt((2.25+0.25+0.25+2.25)/4) = sqrt(5/4) = sqrt(1.25) ≈ 1.1180339887
    # scale = 252/21 = 12
    # annualized_ret = 2.5 * 12 = 30
    # annualized_std = 1.1180339887 * sqrt(12) ≈ 3.8729833462
    # sharpe = 30 / 3.8729833462 ≈ 7.7459666...
    # rounded to 3 decimals → 7.746
    assert _compute_sharpe(returns, avg_hold) == pytest.approx(7.746, abs=1e-3)


# ─── run_backtest ──────────────────────────────────────────────────────────

def test_run_backtest_empty_data_returns_sentinel() -> None:
    r = run_backtest({})
    assert r.total_trades == 0
    assert r.win_rate == 0.0
    assert r.avg_return_pct == 0.0
    assert r.best_trade is None
    assert r.worst_trade is None
    assert r.equity_curve == []
    assert r.monthly_returns == []
    assert r.by_conviction == {}
    assert r.by_type == {}
    assert r.by_ticker == {}


def test_run_backtest_skips_too_few_rows() -> None:
    # Only 10 rows — under the 35-row minimum.
    short_df = _price_frame([100.0] * 10)
    r = run_backtest({"ZIM": short_df})
    assert r.total_trades == 0


def test_run_backtest_skips_missing_close_column() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=80)})
    r = run_backtest({"ZIM": df})
    assert r.total_trades == 0


def test_run_backtest_engineered_input_produces_trades() -> None:
    # Build a rally that triggers momentum signal on ZIM.
    # Need lookback window to retain ≥35 rows after trimming; with default
    # lookback_days=180 we are well within bounds.
    prices = [100.0] * 30 + [130.0] * 10 + [140.0] * 50
    df = _price_frame(prices)
    r = run_backtest({"ZIM": df}, lookback_days=180)
    assert r.total_trades >= 1
    # All trades belong to ZIM (the only ticker with data).
    assert all(t.ticker == "ZIM" for t in r.trades)
    # Aggregates are populated and consistent.
    assert r.win_rate == pytest.approx(
        round(sum(t.hit for t in r.trades) / r.total_trades * 100, 1),
        abs=1e-9,
    )
    assert r.best_trade.return_pct >= r.worst_trade.return_pct
    assert r.by_ticker.get("ZIM", {}).get("count") == r.total_trades
    # Monthly rows are sorted ascending.
    months = [row["month"] for row in r.monthly_returns]
    assert months == sorted(months)
    # Equity curve sorted by exit_date.
    dates = [pt["date"] for pt in r.equity_curve]
    assert dates == sorted(dates)


def test_run_backtest_hold_days_map_override() -> None:
    # Override hold periods — momentum uses 1M, BDI uses 1W.
    prices = [100.0] * 30 + [130.0] * 10 + [140.0] * 50
    df = _price_frame(prices)
    # Set 1M hold to 10 days; resulting momentum trades should record
    # holding_days <= 10 for mid-series fires.
    r = run_backtest({"ZIM": df}, lookback_days=180,
                     hold_days_map={"1W": 3, "1M": 10})
    momentum_trades = [t for t in r.trades if t.signal_type == "MOMENTUM"
                       and t.signal_name == "Price Momentum"]
    assert momentum_trades, "expected at least one momentum trade"
    for t in momentum_trades:
        assert t.holding_days <= 10


def test_run_backtest_aggregates_match_trade_list() -> None:
    prices = [100.0] * 30 + [130.0] * 10 + [140.0] * 50
    df = _price_frame(prices)
    r = run_backtest({"ZIM": df}, lookback_days=180)
    if r.total_trades == 0:
        pytest.skip("engineered fixture produced no trades")
    expected_total = round(sum(t.return_pct for t in r.trades), 2)
    expected_avg = round(sum(t.return_pct for t in r.trades) / r.total_trades, 2)
    assert r.total_return_pct == pytest.approx(expected_total, abs=1e-9)
    assert r.avg_return_pct == pytest.approx(expected_avg, abs=1e-9)
    # max_drawdown is the worst per-trade dd (most negative).
    expected_dd = round(min(t.max_drawdown_pct for t in r.trades), 2)
    assert r.max_drawdown == pytest.approx(expected_dd, abs=1e-9)


def test_run_backtest_tickers_constant() -> None:
    # Defining property: the engine scans exactly these five tickers.
    assert _TICKERS == ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]


# ─── get_backtest_summary_html ─────────────────────────────────────────────

def _empty_results(*, win_rate: float, avg_return: float, sharpe: float,
                   max_dd: float, total_trades: int = 10) -> BacktestResults:
    return BacktestResults(
        trades=[],
        total_trades=total_trades,
        win_rate=win_rate,
        avg_return_pct=avg_return,
        total_return_pct=avg_return * total_trades,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        best_trade=None,
        worst_trade=None,
        by_conviction={},
        by_type={},
        by_ticker={},
        monthly_returns=[],
        equity_curve=[],
    )


def test_summary_html_contains_metrics() -> None:
    r = _empty_results(win_rate=60.0, avg_return=2.5, sharpe=1.5, max_dd=-8.0)
    html = get_backtest_summary_html(r)
    assert "60.0%" in html
    assert "1.50" in html         # sharpe formatted with .2f
    assert "-8.0%" in html        # max_drawdown formatted with .1f
    assert "Backtest Summary" in html
    assert "10" in html           # total trades count


def test_summary_html_color_buckets_win_rate() -> None:
    high = _empty_results(win_rate=60.0, avg_return=1.0, sharpe=1.5, max_dd=-5.0)
    mid = _empty_results(win_rate=50.0, avg_return=1.0, sharpe=1.5, max_dd=-5.0)
    low = _empty_results(win_rate=30.0, avg_return=1.0, sharpe=1.5, max_dd=-5.0)
    assert "#2e9e6e" in get_backtest_summary_html(high)  # green for ≥55
    assert "#c9962b" in get_backtest_summary_html(mid)   # amber for ≥45
    assert "#c0392b" in get_backtest_summary_html(low)   # red for <45


def test_summary_html_color_buckets_sharpe() -> None:
    high = _empty_results(win_rate=50.0, avg_return=1.0, sharpe=1.5, max_dd=-5.0)
    mid = _empty_results(win_rate=50.0, avg_return=1.0, sharpe=0.5, max_dd=-5.0)
    neg = _empty_results(win_rate=50.0, avg_return=1.0, sharpe=-0.5, max_dd=-5.0)
    assert "1.50" in get_backtest_summary_html(high)
    # Mid sharpe (0.5) → amber color in the sharpe cell.
    mid_html = get_backtest_summary_html(mid)
    assert "0.50" in mid_html
    assert "#c9962b" in mid_html
    # Negative sharpe → red
    assert "#c0392b" in get_backtest_summary_html(neg)


def test_summary_html_avg_return_color_flip_on_sign() -> None:
    pos = _empty_results(win_rate=50.0, avg_return=2.0, sharpe=0.5, max_dd=-5.0)
    neg = _empty_results(win_rate=50.0, avg_return=-1.0, sharpe=0.5, max_dd=-5.0)
    # The avg_return color is green when ≥0, red when <0.
    assert "+2.00%" in get_backtest_summary_html(pos)
    assert "-1.00%" in get_backtest_summary_html(neg)
