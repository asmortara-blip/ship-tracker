"""Alpha signal backtesting engine — simulates historical signal performance."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BacktestTrade:
    signal_name: str
    ticker: str
    direction: str           # LONG | SHORT
    conviction: str          # HIGH | MEDIUM | LOW
    signal_type: str
    entry_date: str          # ISO date
    exit_date: str           # ISO date
    entry_price: float
    exit_price: float
    return_pct: float        # actual return
    signal_expected_pct: float   # what signal predicted
    hit: bool                # did price move in predicted direction?
    holding_days: int
    max_drawdown_pct: float  # worst intraday during hold


@dataclass
class BacktestResults:
    trades: list[BacktestTrade]
    total_trades: int
    win_rate: float          # % trades that were correct direction
    avg_return_pct: float
    total_return_pct: float  # sum of all returns (equal weight)
    sharpe_ratio: float
    max_drawdown: float
    best_trade: Optional[BacktestTrade]
    worst_trade: Optional[BacktestTrade]
    by_conviction: dict      # {HIGH: {win_rate, avg_return, count}, ...}
    by_type: dict            # {MOMENTUM: {...}, ...}
    by_ticker: dict          # {ZIM: {...}, ...}
    monthly_returns: list[dict]   # [{month, return_pct, trade_count}]
    equity_curve: list[dict]      # [{date, cumulative_return}]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HOLD_DAYS_DEFAULT = {"1W": 5, "1M": 21, "3M": 63}

_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

# Signal definitions for historical scanning
# Each entry: (signal_name, signal_type, direction, conviction_fn, expected_pct_fn, horizon)
# conviction_fn and expected_pct_fn take (chg_30d, aux) and return values.

def _ensure_date_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return df sorted with a proper DatetimeIndex on 'date' column."""
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def _rolling_pct_change(series: pd.Series, window: int) -> pd.Series:
    """Percent change over `window` bars for each element in series."""
    return series.pct_change(periods=window) * 100


def _rolling_pct_change_fwd(series: pd.Series, window: int) -> pd.Series:
    """Forward percent change: from index i to i+window."""
    return series.shift(-window) / series - 1


def _compute_max_drawdown_during(prices: pd.Series) -> float:
    """Given a price series for a holding period, compute worst drawdown %."""
    if prices.empty or len(prices) < 2:
        return 0.0
    peak = prices.iloc[0]
    worst = 0.0
    for p in prices:
        if p > peak:
            peak = p
        dd = (p - peak) / peak * 100
        if dd < worst:
            worst = dd
    return round(worst, 2)


def _conviction_from_drop(drop_pct: float) -> str:
    if drop_pct > 25:
        return "HIGH"
    if drop_pct > 18:
        return "MEDIUM"
    return "LOW"


def _conviction_from_momentum(chg: float) -> str:
    return "HIGH" if chg > 30 else "MEDIUM"


# ---------------------------------------------------------------------------
# Signal scanning on historical data
# ---------------------------------------------------------------------------

def _scan_momentum_signals(
    ticker: str,
    df: pd.DataFrame,
    hold_days: int,
    lookback_days: int,
) -> list[BacktestTrade]:
    """Momentum: 30d return > 15% AND recent 7d positive → LONG."""
    trades: list[BacktestTrade] = []
    if df.empty or "close" not in df.columns:
        return trades

    prices = df["close"].values
    dates = df["date"].values if "date" in df.columns else np.arange(len(prices))
    n = len(prices)

    for i in range(30, n - hold_days - 1):
        p_now = prices[i]
        p_30ago = prices[i - 30]
        p_7ago = prices[i - 7]

        if p_30ago <= 0 or p_7ago <= 0:
            continue

        chg_30d = (p_now - p_30ago) / p_30ago * 100
        chg_7d = (p_now - p_7ago) / p_7ago * 100

        if chg_30d > 15 and chg_7d > 0:
            exit_idx = min(i + hold_days, n - 1)
            p_exit = prices[exit_idx]
            ret = (p_exit - p_now) / p_now * 100
            holding_prices = prices[i:exit_idx + 1]
            max_dd = _compute_max_drawdown_during(pd.Series(holding_prices))
            conviction = _conviction_from_momentum(chg_30d)
            expected = round(chg_30d * 0.4, 2)

            entry_date = str(dates[i])[:10]
            exit_date = str(dates[exit_idx])[:10]

            trades.append(BacktestTrade(
                signal_name="Price Momentum",
                ticker=ticker,
                direction="LONG",
                conviction=conviction,
                signal_type="MOMENTUM",
                entry_date=entry_date,
                exit_date=exit_date,
                entry_price=round(float(p_now), 2),
                exit_price=round(float(p_exit), 2),
                return_pct=round(float(ret), 2),
                signal_expected_pct=expected,
                hit=ret > 0,
                holding_days=exit_idx - i,
                max_drawdown_pct=max_dd,
            ))
    return trades


def _scan_mean_reversion_signals(
    ticker: str,
    df: pd.DataFrame,
    hold_days: int,
    lookback_days: int,
) -> list[BacktestTrade]:
    """Mean reversion: 30d return < -15% → contrarian LONG."""
    trades: list[BacktestTrade] = []
    if df.empty or "close" not in df.columns:
        return trades

    prices = df["close"].values
    dates = df["date"].values if "date" in df.columns else np.arange(len(prices))
    n = len(prices)

    for i in range(30, n - hold_days - 1):
        p_now = prices[i]
        p_30ago = prices[i - 30]

        if p_30ago <= 0:
            continue

        chg_30d = (p_now - p_30ago) / p_30ago * 100

        if chg_30d < -15:
            drop = abs(chg_30d)
            exit_idx = min(i + hold_days, n - 1)
            p_exit = prices[exit_idx]
            ret = (p_exit - p_now) / p_now * 100
            holding_prices = prices[i:exit_idx + 1]
            max_dd = _compute_max_drawdown_during(pd.Series(holding_prices))
            conviction = _conviction_from_drop(drop)
            expected = round(drop * 0.5, 2)

            entry_date = str(dates[i])[:10]
            exit_date = str(dates[exit_idx])[:10]

            trades.append(BacktestTrade(
                signal_name="Oversold Mean Reversion",
                ticker=ticker,
                direction="LONG",
                conviction=conviction,
                signal_type="MEAN_REVERSION",
                entry_date=entry_date,
                exit_date=exit_date,
                entry_price=round(float(p_now), 2),
                exit_price=round(float(p_exit), 2),
                return_pct=round(float(ret), 2),
                signal_expected_pct=expected,
                hit=ret > 0,
                holding_days=exit_idx - i,
                max_drawdown_pct=max_dd,
            ))
    return trades


def _scan_bdi_divergence_signals(
    ticker: str,
    df: pd.DataFrame,
    sector_df: pd.DataFrame,
    hold_days: int,
) -> list[BacktestTrade]:
    """BDI divergence: stock down >5% but sector proxy up → LONG."""
    trades: list[BacktestTrade] = []
    if df.empty or "close" not in df.columns:
        return trades
    if sector_df.empty or "close" not in sector_df.columns:
        return trades

    # Align on dates
    df2 = df.set_index("date")["close"] if "date" in df.columns else df["close"]
    sec2 = sector_df.set_index("date")["close"] if "date" in sector_df.columns else sector_df["close"]

    try:
        df2.index = pd.to_datetime(df2.index)
        sec2.index = pd.to_datetime(sec2.index)
        aligned = pd.concat([df2, sec2], axis=1, join="inner")
        aligned.columns = ["stock", "sector"]
        aligned = aligned.dropna().sort_index()
    except Exception:
        return trades

    prices_stock = aligned["stock"].values
    prices_sector = aligned["sector"].values
    dates = aligned.index
    n = len(aligned)

    for i in range(30, n - hold_days - 1):
        p_s_now = prices_stock[i]
        p_s_30 = prices_stock[i - 30]
        p_sec_now = prices_sector[i]
        p_sec_30 = prices_sector[i - 30]

        if p_s_30 <= 0 or p_sec_30 <= 0:
            continue

        chg_stock = (p_s_now - p_s_30) / p_s_30 * 100
        chg_sector = (p_sec_now - p_sec_30) / p_sec_30 * 100

        if chg_stock < -5 and chg_sector > 0:
            divergence = chg_sector - chg_stock
            if divergence < 5:
                continue

            exit_idx = min(i + hold_days, n - 1)
            p_exit = prices_stock[exit_idx]
            ret = (p_exit - p_s_now) / p_s_now * 100
            holding_prices = prices_stock[i:exit_idx + 1]
            max_dd = _compute_max_drawdown_during(pd.Series(holding_prices))
            conviction = "HIGH" if divergence > 12 else "MEDIUM"
            expected = round(min(divergence * 0.5, 20.0), 2)

            entry_date = str(dates[i])[:10]
            exit_date = str(dates[exit_idx])[:10]

            trades.append(BacktestTrade(
                signal_name="BDI Divergence Catch-Up",
                ticker=ticker,
                direction="LONG",
                conviction=conviction,
                signal_type="MOMENTUM",
                entry_date=entry_date,
                exit_date=exit_date,
                entry_price=round(float(p_s_now), 2),
                exit_price=round(float(p_exit), 2),
                return_pct=round(float(ret), 2),
                signal_expected_pct=expected,
                hit=ret > 0,
                holding_days=exit_idx - i,
                max_drawdown_pct=max_dd,
            ))
    return trades


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _group_stats(trades: list[BacktestTrade], key: str) -> dict:
    """Group trades by a BacktestTrade attribute and compute stats per group."""
    groups: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        val = getattr(t, key, "UNKNOWN")
        groups.setdefault(val, []).append(t)

    result = {}
    for grp, grp_trades in groups.items():
        returns = [t.return_pct for t in grp_trades]
        hits = [t.hit for t in grp_trades]
        result[grp] = {
            "count": len(grp_trades),
            "win_rate": round(sum(hits) / len(hits) * 100, 1) if hits else 0.0,
            "avg_return": round(sum(returns) / len(returns), 2) if returns else 0.0,
            "total_return": round(sum(returns), 2),
        }
    return result


def _compute_monthly_returns(trades: list[BacktestTrade]) -> list[dict]:
    """Aggregate trades by entry month."""
    monthly: dict[str, list[float]] = {}
    for t in trades:
        month_key = t.entry_date[:7]  # YYYY-MM
        monthly.setdefault(month_key, []).append(t.return_pct)

    rows = []
    for month in sorted(monthly):
        rets = monthly[month]
        rows.append({
            "month": month,
            "return_pct": round(sum(rets) / len(rets), 2),
            "trade_count": len(rets),
        })
    return rows


def _compute_equity_curve(trades: list[BacktestTrade]) -> list[dict]:
    """Build cumulative equal-weight return curve sorted by exit date."""
    if not trades:
        return []

    sorted_trades = sorted(trades, key=lambda t: t.exit_date)
    cumulative = 0.0
    curve = []
    for t in sorted_trades:
        cumulative += t.return_pct
        curve.append({
            "date": t.exit_date,
            "cumulative_return": round(cumulative, 2),
        })
    return curve


def _compute_sharpe(returns: list[float], avg_hold_days: float) -> float:
    """Annualized Sharpe ratio."""
    if len(returns) < 2 or avg_hold_days <= 0:
        return 0.0
    arr = np.array(returns)
    avg_ret = float(arr.mean())
    std_ret = float(arr.std())
    if std_ret == 0:
        return 0.0
    scale = 252 / avg_hold_days
    annualized_ret = avg_ret * scale
    annualized_std = std_ret * math.sqrt(scale)
    return round(annualized_ret / annualized_std, 3)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backtest(
    stock_data: dict,
    lookback_days: int = 180,
    hold_days_map: dict = None,
) -> BacktestResults:
    """Simulate historical alpha signal performance across all tickers.

    Parameters
    ----------
    stock_data:    dict[ticker → DataFrame with 'close' and optionally 'date']
    lookback_days: how many days of history to scan (controls how far back we go)
    hold_days_map: override default hold periods per horizon key

    Returns
    -------
    BacktestResults populated with all trade records and aggregate statistics.
    """
    if hold_days_map is None:
        hold_days_map = _HOLD_DAYS_DEFAULT.copy()

    all_trades: list[BacktestTrade] = []

    # Build a sector proxy: average close across all tickers available
    sector_frames = []
    for ticker in _TICKERS:
        df = stock_data.get(ticker)
        if df is not None and not df.empty and "close" in df.columns:
            df2 = _ensure_date_index(df)
            if "date" in df2.columns:
                sector_frames.append(df2.set_index("date")["close"])

    sector_proxy = pd.DataFrame()
    if sector_frames:
        try:
            combined = pd.concat(sector_frames, axis=1)
            combined.index = pd.to_datetime(combined.index)
            sector_mean = combined.mean(axis=1).dropna()
            sector_proxy = sector_mean.reset_index()
            sector_proxy.columns = ["date", "close"]
        except Exception as e:
            logger.warning(f"Backtest: sector proxy build failed: {e}")

    for ticker in _TICKERS:
        df_raw = stock_data.get(ticker)
        if df_raw is None or df_raw.empty or "close" not in df_raw.columns:
            logger.warning(f"Backtest: no data for {ticker}, skipping")
            continue

        df = _ensure_date_index(df_raw)

        # Trim to lookback window
        if "date" in df.columns:
            cutoff = df["date"].max() - pd.Timedelta(days=lookback_days)
            df = df[df["date"] >= cutoff].reset_index(drop=True)

        if len(df) < 35:
            logger.warning(f"Backtest: too few rows for {ticker} ({len(df)}), skipping")
            continue

        hold_1w = hold_days_map.get("1W", 5)
        hold_1m = hold_days_map.get("1M", 21)

        # Momentum signals — 1M hold
        try:
            trades = _scan_momentum_signals(ticker, df, hold_1m, lookback_days)
            all_trades.extend(trades)
            logger.debug(f"Backtest momentum {ticker}: {len(trades)} trades")
        except Exception as e:
            logger.warning(f"Backtest momentum scan error for {ticker}: {e}")

        # Mean reversion signals — 1M hold
        try:
            trades = _scan_mean_reversion_signals(ticker, df, hold_1m, lookback_days)
            all_trades.extend(trades)
            logger.debug(f"Backtest mean-rev {ticker}: {len(trades)} trades")
        except Exception as e:
            logger.warning(f"Backtest mean-rev scan error for {ticker}: {e}")

        # BDI divergence — 1W hold (short-term catch-up)
        if not sector_proxy.empty:
            try:
                trades = _scan_bdi_divergence_signals(ticker, df, sector_proxy, hold_1w)
                all_trades.extend(trades)
                logger.debug(f"Backtest BDI-div {ticker}: {len(trades)} trades")
            except Exception as e:
                logger.warning(f"Backtest BDI-div scan error for {ticker}: {e}")

    logger.info(f"Backtest complete: {len(all_trades)} total simulated trades")

    if not all_trades:
        return BacktestResults(
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

    returns = [t.return_pct for t in all_trades]
    hits = [t.hit for t in all_trades]
    holding_days_list = [t.holding_days for t in all_trades]

    win_rate = round(sum(hits) / len(hits) * 100, 1)
    avg_return = round(sum(returns) / len(returns), 2)
    total_return = round(sum(returns), 2)
    avg_hold = sum(holding_days_list) / len(holding_days_list) if holding_days_list else 21
    sharpe = _compute_sharpe(returns, avg_hold)

    sorted_by_ret = sorted(all_trades, key=lambda t: t.return_pct)
    worst_trade = sorted_by_ret[0]
    best_trade = sorted_by_ret[-1]

    # Max portfolio drawdown: worst single trade drawdown (equal weight proxy)
    max_drawdown = round(min(t.max_drawdown_pct for t in all_trades), 2)

    by_conviction = _group_stats(all_trades, "conviction")
    by_type = _group_stats(all_trades, "signal_type")
    by_ticker = _group_stats(all_trades, "ticker")
    monthly_returns = _compute_monthly_returns(all_trades)
    equity_curve = _compute_equity_curve(all_trades)

    return BacktestResults(
        trades=all_trades,
        total_trades=len(all_trades),
        win_rate=win_rate,
        avg_return_pct=avg_return,
        total_return_pct=total_return,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        best_trade=best_trade,
        worst_trade=worst_trade,
        by_conviction=by_conviction,
        by_type=by_type,
        by_ticker=by_ticker,
        monthly_returns=monthly_returns,
        equity_curve=equity_curve,
    )


def get_backtest_summary_html(results: BacktestResults) -> str:
    """Return a styled HTML summary card for embedding in Streamlit."""
    wr_color = "#10b981" if results.win_rate >= 55 else ("#f59e0b" if results.win_rate >= 45 else "#ef4444")
    ret_color = "#10b981" if results.avg_return_pct >= 0 else "#ef4444"
    sharpe_color = "#10b981" if results.sharpe_ratio >= 1.0 else ("#f59e0b" if results.sharpe_ratio >= 0 else "#ef4444")

    return f"""
    <div style="
        background: #1a2235;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 10px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 1rem;
    ">
        <div style="font-size:0.7rem;color:#64748b;text-transform:uppercase;
                    letter-spacing:0.1em;margin-bottom:0.8rem;">
            Backtest Summary — {results.total_trades} Simulated Trades
        </div>
        <div style="display:flex;gap:2rem;flex-wrap:wrap;">
            <div>
                <div style="font-size:1.5rem;font-weight:700;color:{wr_color};">
                    {results.win_rate:.1f}%
                </div>
                <div style="font-size:0.72rem;color:#94a3b8;">Win Rate</div>
            </div>
            <div>
                <div style="font-size:1.5rem;font-weight:700;color:{ret_color};">
                    {results.avg_return_pct:+.2f}%
                </div>
                <div style="font-size:0.72rem;color:#94a3b8;">Avg Return</div>
            </div>
            <div>
                <div style="font-size:1.5rem;font-weight:700;color:{sharpe_color};">
                    {results.sharpe_ratio:.2f}
                </div>
                <div style="font-size:0.72rem;color:#94a3b8;">Sharpe</div>
            </div>
            <div>
                <div style="font-size:1.5rem;font-weight:700;color:#ef4444;">
                    {results.max_drawdown:.1f}%
                </div>
                <div style="font-size:0.72rem;color:#94a3b8;">Max Drawdown</div>
            </div>
        </div>
    </div>
    """
