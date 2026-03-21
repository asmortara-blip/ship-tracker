"""Deep stock analysis and comparison for shipping stocks.

Provides StockMetrics and StockComparison dataclasses populated by
analyze_shipping_stocks(), plus HTML formatting helpers for the
investor report's stock analysis section.

Supported tickers: ZIM, MATX, SBLK, DAC, CMRE
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

TICKER_META = {
    "ZIM":  {"name": "ZIM Integrated Shipping",  "type": "Container",         "domicile": "Israel", "exchange": "NYSE"},
    "MATX": {"name": "Matson Inc.",               "type": "Container",         "domicile": "USA",    "exchange": "NYSE"},
    "SBLK": {"name": "Star Bulk Carriers",        "type": "Dry Bulk",          "domicile": "Greece", "exchange": "NASDAQ"},
    "DAC":  {"name": "Danaos Corporation",        "type": "Container Lessor",  "domicile": "Greece", "exchange": "NYSE"},
    "CMRE": {"name": "Costamare Inc.",            "type": "Container Lessor",  "domicile": "Greece", "exchange": "NYSE"},
}

_CONVICTION_SCORES = {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.35}
_MIN_ROWS_FULL = 20  # minimum rows for full computation


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class StockMetrics:
    ticker: str
    name: str
    type: str                       # Container | Dry Bulk | Container Lessor
    latest_price: float
    change_1d_pct: float
    change_7d_pct: float
    change_30d_pct: float
    change_90d_pct: float
    high_52w: float
    low_52w: float
    pct_from_52w_high: float        # negative if below high
    pct_from_52w_low: float         # positive if above low
    avg_volume_30d: float
    volatility_30d: float           # annualized std dev
    sharpe_30d: float               # 30d return / 30d vol
    momentum_score: float           # composite [0, 1]
    trend: str                      # "Uptrend" | "Downtrend" | "Sideways"
    rsi_14: float                   # RSI [0, 100]
    ma_20: float                    # 20-day moving average
    ma_50: float                    # 50-day moving average
    price_vs_ma20: str              # "Above" | "Below"
    price_vs_ma50: str              # "Above" | "Below"
    signal_count: int               # from alpha signals
    top_signal_direction: str       # LONG | SHORT | NEUTRAL
    top_signal_conviction: str      # HIGH | MEDIUM | LOW


@dataclass
class StockComparison:
    metrics: dict                   # ticker → StockMetrics
    best_momentum: str              # ticker with highest momentum_score
    worst_performer_30d: str        # ticker with most negative 30d change
    best_performer_30d: str         # ticker with most positive 30d change
    most_volatile: str              # highest volatility_30d
    sector_avg_change_30d: float    # average of all 30d changes
    correlation_matrix: dict        # {(t1, t2): correlation_coeff}
    bdi_correlations: dict          # {ticker: correlation with BDI last 90d}
    ranking: list                   # [{rank, ticker, score, label}] desc by momentum_score


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default: float = float("nan")) -> float:
    """Convert a value to float, returning default on failure."""
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _pct_change_n_days(prices: pd.Series, n: int) -> float:
    """Percentage change over last N days (or as many rows as available)."""
    tail = prices.dropna().tail(n + 1)
    if len(tail) < 2:
        return float("nan")
    start = tail.iloc[0]
    end = tail.iloc[-1]
    if start == 0:
        return float("nan")
    return (end - start) / abs(start) * 100.0


def _compute_rsi(prices: pd.Series, period: int = 14) -> float:
    """Wilder RSI.  Returns 50.0 when data is insufficient."""
    try:
        delta = prices.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1] if not rsi.empty else 50.0
        return _safe_float(val, 50.0)
    except Exception as exc:
        logger.warning(f"RSI computation failed: {exc}")
        return 50.0


def _compute_volatility_30d(prices: pd.Series) -> float:
    """Annualised 30-day realised volatility from log returns."""
    try:
        tail = prices.dropna().tail(31)
        if len(tail) < 5:
            return float("nan")
        log_rets = np.log(tail / tail.shift(1)).dropna()
        if log_rets.empty:
            return float("nan")
        return float(log_rets.std() * math.sqrt(252))
    except Exception as exc:
        logger.warning(f"Volatility computation failed: {exc}")
        return float("nan")


def _compute_sharpe_30d(prices: pd.Series) -> float:
    """Simple 30-day Sharpe: (30d return / annualised vol).  Zero risk-free rate."""
    try:
        ret = _pct_change_n_days(prices, 30) / 100.0
        vol = _compute_volatility_30d(prices)
        if math.isnan(ret) or math.isnan(vol) or vol == 0:
            return float("nan")
        return ret / vol
    except Exception as exc:
        logger.warning(f"Sharpe computation failed: {exc}")
        return float("nan")


def _compute_moving_average(prices: pd.Series, window: int) -> float:
    """Rolling simple moving average ending at the latest row."""
    try:
        tail = prices.dropna().tail(window)
        if len(tail) < 1:
            return float("nan")
        return float(tail.mean())
    except Exception as exc:
        logger.warning(f"MA-{window} computation failed: {exc}")
        return float("nan")


def _classify_trend(price: float, ma20: float, ma50: float) -> str:
    """Uptrend / Downtrend / Sideways classification."""
    try:
        if math.isnan(price) or math.isnan(ma20) or math.isnan(ma50):
            return "Sideways"
        if price > ma20 > ma50:
            return "Uptrend"
        if price < ma20 < ma50:
            return "Downtrend"
        return "Sideways"
    except Exception:
        return "Sideways"


def _extract_top_signal(ticker: str, signals: list | None) -> tuple[int, str, str]:
    """Return (count, direction, conviction) from alpha signals for *ticker*."""
    if not signals:
        return 0, "NEUTRAL", "LOW"
    ticker_sigs = [s for s in signals if getattr(s, "ticker", None) == ticker]
    if not ticker_sigs:
        return 0, "NEUTRAL", "LOW"
    # Sort by strength desc, pick the top signal
    try:
        top = sorted(ticker_sigs, key=lambda s: getattr(s, "strength", 0), reverse=True)[0]
        return (
            len(ticker_sigs),
            getattr(top, "direction", "NEUTRAL"),
            getattr(top, "conviction", "LOW"),
        )
    except Exception as exc:
        logger.warning(f"Signal extraction failed for {ticker}: {exc}")
        return len(ticker_sigs), "NEUTRAL", "LOW"


def _momentum_score(
    change_30d: float,
    rsi: float,
    price_vs_ma20: str,
    price_vs_ma50: str,
    conviction: str,
    change_30d_peers: list[float],
) -> float:
    """Composite momentum score normalised to [0, 1]."""
    try:
        # 1. Normalised 30d return contribution (cross-sectional)
        peers_valid = [c for c in change_30d_peers if not math.isnan(c)]
        if len(peers_valid) > 1 and not math.isnan(change_30d):
            mn = min(peers_valid)
            mx = max(peers_valid)
            span = mx - mn
            norm_ret = (change_30d - mn) / span if span > 0 else 0.5
        elif not math.isnan(change_30d):
            # Single valid peer — centre at zero
            norm_ret = 0.5 + min(max(change_30d / 100.0, -0.5), 0.5)
        else:
            norm_ret = 0.5

        # 2. RSI normalised
        rsi_safe = rsi if not math.isnan(rsi) else 50.0
        norm_rsi = rsi_safe / 100.0

        # 3. Price vs MA20
        ma20_flag = 1.0 if price_vs_ma20 == "Above" else 0.0

        # 4. Price vs MA50
        ma50_flag = 1.0 if price_vs_ma50 == "Above" else 0.0

        # 5. Signal conviction
        conv_score = _CONVICTION_SCORES.get(conviction, 0.5)

        score = (
            0.35 * norm_ret
            + 0.20 * norm_rsi
            + 0.15 * ma20_flag
            + 0.15 * ma50_flag
            + 0.15 * conv_score
        )
        return round(min(max(score, 0.0), 1.0), 4)
    except Exception as exc:
        logger.warning(f"Momentum score computation failed: {exc}")
        return 0.5


def _pearson_correlation(s1: pd.Series, s2: pd.Series) -> float:
    """Pearson correlation of two series aligned on their index."""
    try:
        aligned = pd.concat([s1, s2], axis=1).dropna()
        if len(aligned) < 5:
            return float("nan")
        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        return _safe_float(corr)
    except Exception as exc:
        logger.warning(f"Pearson correlation failed: {exc}")
        return float("nan")


def _price_series(df: pd.DataFrame) -> pd.Series:
    """Return a date-indexed closing price series from a normalised stock DataFrame."""
    if df is None or df.empty or "close" not in df.columns:
        return pd.Series(dtype=float)
    work = df.copy()
    if "date" in work.columns:
        work = work.set_index("date")
    return work["close"].sort_index().dropna()


def _bdi_series(macro_data: dict) -> pd.Series:
    """Extract a date-indexed BDI value series from macro_data."""
    try:
        bdi_df = macro_data.get("BDIY")
        if bdi_df is None or (hasattr(bdi_df, "empty") and bdi_df.empty):
            return pd.Series(dtype=float)
        if isinstance(bdi_df, pd.Series):
            return bdi_df.dropna().sort_index()
        if "date" in bdi_df.columns and "value" in bdi_df.columns:
            s = bdi_df.set_index("date")["value"].sort_index().dropna()
            return s
        # Fallback: last numeric column
        num_cols = bdi_df.select_dtypes(include=[np.number]).columns
        if len(num_cols) == 0:
            return pd.Series(dtype=float)
        idx_col = bdi_df.index if not isinstance(bdi_df.index, pd.RangeIndex) else None
        if idx_col is not None:
            return bdi_df[num_cols[-1]].sort_index().dropna()
        return pd.Series(dtype=float)
    except Exception as exc:
        logger.warning(f"BDI series extraction failed: {exc}")
        return pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------

def analyze_shipping_stocks(
    stock_data: dict,
    macro_data: dict,
    signals: list | None = None,
) -> StockComparison:
    """Analyse all shipping stocks and return a StockComparison.

    Args:
        stock_data: dict mapping ticker → pd.DataFrame (STOCK_COLS schema)
        macro_data: dict mapping series_id → pd.DataFrame (MACRO_COLS schema)
        signals:    optional list of AlphaSignal objects

    Returns:
        StockComparison populated with per-ticker StockMetrics plus
        sector-level summaries, correlations, and ranking.
    """
    metrics: dict[str, StockMetrics] = {}
    change_30d_map: dict[str, float] = {}

    # ---- Pass 1: collect 30d changes for cross-sectional normalisation ----
    for ticker in TICKERS:
        df = stock_data.get(ticker)
        if df is None or df.empty:
            continue
        prices = _price_series(df)
        if len(prices) >= 2:
            change_30d_map[ticker] = _pct_change_n_days(prices, 30)

    change_30d_peers = list(change_30d_map.values())

    # ---- Pass 2: compute full metrics per ticker ----
    for ticker in TICKERS:
        meta = TICKER_META.get(ticker, {})
        df = stock_data.get(ticker)

        if df is None or df.empty:
            logger.warning(f"stock_comparator: no data for {ticker}, skipping")
            continue

        try:
            prices = _price_series(df)
            n_rows = len(prices)
            has_full = n_rows >= _MIN_ROWS_FULL

            latest_price = _safe_float(prices.iloc[-1]) if n_rows >= 1 else float("nan")

            # Period returns
            change_1d  = _pct_change_n_days(prices, 1)
            change_7d  = _pct_change_n_days(prices, 7)
            change_30d = change_30d_map.get(ticker, float("nan"))
            change_90d = _pct_change_n_days(prices, 90)

            # 52-week high/low (use full available data, up to 252 bars)
            try:
                prices_52w = prices.tail(252)
                high_52w = _safe_float(prices_52w.max())
                low_52w  = _safe_float(prices_52w.min())
                pct_from_52w_high = ((latest_price - high_52w) / high_52w * 100) if high_52w else float("nan")
                pct_from_52w_low  = ((latest_price - low_52w)  / low_52w  * 100) if low_52w  else float("nan")
            except Exception as exc:
                logger.warning(f"{ticker} 52w range failed: {exc}")
                high_52w = low_52w = pct_from_52w_high = pct_from_52w_low = float("nan")

            # Volume
            try:
                if "volume" in df.columns:
                    avg_volume_30d = _safe_float(df["volume"].tail(30).mean())
                else:
                    avg_volume_30d = float("nan")
            except Exception as exc:
                logger.warning(f"{ticker} volume failed: {exc}")
                avg_volume_30d = float("nan")

            # Volatility and Sharpe
            volatility_30d = _compute_volatility_30d(prices) if has_full else float("nan")
            sharpe_30d     = _compute_sharpe_30d(prices)     if has_full else float("nan")

            # Technical indicators
            rsi_14 = _compute_rsi(prices)            if has_full else 50.0
            ma_20  = _compute_moving_average(prices, 20) if n_rows >= 20 else float("nan")
            ma_50  = _compute_moving_average(prices, 50) if n_rows >= 50 else float("nan")

            price_vs_ma20 = "Above" if (not math.isnan(ma_20)  and latest_price > ma_20)  else "Below"
            price_vs_ma50 = "Above" if (not math.isnan(ma_50)  and latest_price > ma_50)  else "Below"

            trend = _classify_trend(latest_price, ma_20, ma_50)

            # Signals
            sig_count, sig_direction, sig_conviction = _extract_top_signal(ticker, signals)

            # Momentum score
            mom_score = _momentum_score(
                change_30d, rsi_14, price_vs_ma20, price_vs_ma50,
                sig_conviction, change_30d_peers,
            ) if has_full else 0.5

            metrics[ticker] = StockMetrics(
                ticker=ticker,
                name=meta.get("name", ticker),
                type=meta.get("type", "Unknown"),
                latest_price=latest_price,
                change_1d_pct=change_1d,
                change_7d_pct=change_7d,
                change_30d_pct=change_30d,
                change_90d_pct=change_90d,
                high_52w=high_52w,
                low_52w=low_52w,
                pct_from_52w_high=pct_from_52w_high,
                pct_from_52w_low=pct_from_52w_low,
                avg_volume_30d=avg_volume_30d,
                volatility_30d=volatility_30d,
                sharpe_30d=sharpe_30d,
                momentum_score=mom_score,
                trend=trend,
                rsi_14=rsi_14,
                ma_20=ma_20,
                ma_50=ma_50,
                price_vs_ma20=price_vs_ma20,
                price_vs_ma50=price_vs_ma50,
                signal_count=sig_count,
                top_signal_direction=sig_direction,
                top_signal_conviction=sig_conviction,
            )

        except Exception as exc:
            logger.warning(f"stock_comparator: failed to compute metrics for {ticker}: {exc}")
            continue

    # ---- Sector summaries ----
    valid_tickers = list(metrics.keys())

    def _best_by(key: str, maximize: bool = True) -> str:
        if not valid_tickers:
            return ""
        vals = {t: getattr(metrics[t], key) for t in valid_tickers}
        valid = {t: v for t, v in vals.items() if not (isinstance(v, float) and math.isnan(v))}
        if not valid:
            return valid_tickers[0]
        return max(valid, key=lambda t: valid[t]) if maximize else min(valid, key=lambda t: valid[t])

    best_momentum       = _best_by("momentum_score", maximize=True)
    best_performer_30d  = _best_by("change_30d_pct", maximize=True)
    worst_performer_30d = _best_by("change_30d_pct", maximize=False)
    most_volatile       = _best_by("volatility_30d", maximize=True)

    changes_30d_valid = [
        metrics[t].change_30d_pct for t in valid_tickers
        if not math.isnan(metrics[t].change_30d_pct)
    ]
    sector_avg_change_30d = float(np.mean(changes_30d_valid)) if changes_30d_valid else float("nan")

    # ---- Pairwise correlation matrix (90d price returns) ----
    correlation_matrix: dict = {}
    try:
        price_series_map: dict[str, pd.Series] = {}
        for ticker in valid_tickers:
            df = stock_data.get(ticker)
            if df is not None and not df.empty:
                ps = _price_series(df).tail(91)
                ret = ps.pct_change().dropna()
                price_series_map[ticker] = ret

        for i, t1 in enumerate(valid_tickers):
            for t2 in valid_tickers[i + 1:]:
                if t1 in price_series_map and t2 in price_series_map:
                    corr = _pearson_correlation(price_series_map[t1], price_series_map[t2])
                    correlation_matrix[(t1, t2)] = corr
                    correlation_matrix[(t2, t1)] = corr
    except Exception as exc:
        logger.warning(f"Correlation matrix failed: {exc}")

    # ---- BDI correlations ----
    bdi_correlations: dict = {}
    try:
        bdi = _bdi_series(macro_data)
        if not bdi.empty:
            bdi_ret = bdi.tail(91).pct_change().dropna()
            for ticker in valid_tickers:
                df = stock_data.get(ticker)
                if df is None or df.empty:
                    bdi_correlations[ticker] = float("nan")
                    continue
                ps = _price_series(df).tail(91)
                stock_ret = ps.pct_change().dropna()
                bdi_correlations[ticker] = _pearson_correlation(stock_ret, bdi_ret)
        else:
            logger.warning("stock_comparator: BDI series (BDIY) not available; skipping BDI correlations")
            bdi_correlations = {t: float("nan") for t in valid_tickers}
    except Exception as exc:
        logger.warning(f"BDI correlation failed: {exc}")
        bdi_correlations = {t: float("nan") for t in valid_tickers}

    # ---- Ranking ----
    def _momentum_label(score: float) -> str:
        if math.isnan(score):
            return "N/A"
        if score >= 0.75:
            return "Strong Buy"
        if score >= 0.60:
            return "Buy"
        if score >= 0.45:
            return "Neutral"
        if score >= 0.30:
            return "Sell"
        return "Strong Sell"

    ranking = []
    sorted_tickers = sorted(
        valid_tickers,
        key=lambda t: metrics[t].momentum_score if not math.isnan(metrics[t].momentum_score) else -1,
        reverse=True,
    )
    for rank, ticker in enumerate(sorted_tickers, start=1):
        score = metrics[ticker].momentum_score
        ranking.append({
            "rank": rank,
            "ticker": ticker,
            "score": score,
            "label": _momentum_label(score),
        })

    return StockComparison(
        metrics=metrics,
        best_momentum=best_momentum,
        worst_performer_30d=worst_performer_30d,
        best_performer_30d=best_performer_30d,
        most_volatile=most_volatile,
        sector_avg_change_30d=sector_avg_change_30d,
        correlation_matrix=correlation_matrix,
        bdi_correlations=bdi_correlations,
        ranking=ranking,
    )


# ---------------------------------------------------------------------------
# HTML formatters
# ---------------------------------------------------------------------------

def _pct_color(val: float, positive_good: bool = True) -> str:
    """Return a CSS colour string for a percentage value."""
    if math.isnan(val):
        return "#6b7280"  # gray
    if positive_good:
        return "#16a34a" if val >= 0 else "#dc2626"
    return "#dc2626" if val >= 0 else "#16a34a"


def _fmt_pct(val: float, decimals: int = 2) -> str:
    """Format a percentage value with sign."""
    if math.isnan(val):
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.{decimals}f}%"


def _fmt_price(val: float) -> str:
    if math.isnan(val):
        return "N/A"
    return f"${val:,.2f}"


def _momentum_bar_html(score: float, width_px: int = 120) -> str:
    """Render a small inline momentum bar."""
    if math.isnan(score):
        return "<span style='color:#6b7280'>N/A</span>"
    pct = int(score * 100)
    # Colour gradient: red → yellow → green
    if score >= 0.6:
        bar_color = "#16a34a"
    elif score >= 0.4:
        bar_color = "#ca8a04"
    else:
        bar_color = "#dc2626"
    filled = int(score * width_px)
    return (
        f"<div style='display:inline-block;background:#e5e7eb;border-radius:4px;"
        f"width:{width_px}px;height:10px;vertical-align:middle;'>"
        f"<div style='background:{bar_color};border-radius:4px;"
        f"width:{filled}px;height:10px;'></div></div>"
        f" <span style='font-size:0.75rem;color:#374151'>{pct}%</span>"
    )


def _rsi_gauge_html(rsi: float) -> str:
    """Inline RSI value with colour coding."""
    if math.isnan(rsi):
        return "<span style='color:#6b7280'>N/A</span>"
    if rsi >= 70:
        color = "#dc2626"
        label = "Overbought"
    elif rsi <= 30:
        color = "#2563eb"
        label = "Oversold"
    else:
        color = "#374151"
        label = "Neutral"
    return (
        f"<span style='color:{color};font-weight:600'>{rsi:.1f}</span>"
        f" <span style='color:#6b7280;font-size:0.75rem'>({label})</span>"
    )


def _trend_badge_html(trend: str) -> str:
    colors = {
        "Uptrend":   ("#dcfce7", "#15803d"),
        "Downtrend": ("#fee2e2", "#b91c1c"),
        "Sideways":  ("#f3f4f6", "#374151"),
    }
    bg, fg = colors.get(trend, ("#f3f4f6", "#374151"))
    return (
        f"<span style='background:{bg};color:{fg};padding:2px 8px;"
        f"border-radius:12px;font-size:0.75rem;font-weight:600'>{trend}</span>"
    )


def _type_badge_html(ship_type: str) -> str:
    colors = {
        "Container":        ("#dbeafe", "#1d4ed8"),
        "Dry Bulk":         ("#fef9c3", "#92400e"),
        "Container Lessor": ("#ede9fe", "#6d28d9"),
    }
    bg, fg = colors.get(ship_type, ("#f3f4f6", "#374151"))
    return (
        f"<span style='background:{bg};color:{fg};padding:2px 8px;"
        f"border-radius:12px;font-size:0.7rem;font-weight:500'>{ship_type}</span>"
    )


def _direction_badge_html(direction: str, conviction: str) -> str:
    colors = {
        "LONG":    ("#dcfce7", "#15803d"),
        "SHORT":   ("#fee2e2", "#b91c1c"),
        "NEUTRAL": ("#f3f4f6", "#374151"),
    }
    bg, fg = colors.get(direction, ("#f3f4f6", "#374151"))
    return (
        f"<span style='background:{bg};color:{fg};padding:2px 8px;"
        f"border-radius:12px;font-size:0.75rem;font-weight:600'>"
        f"{direction} ({conviction})</span>"
    )


def format_stock_card_html(metrics: StockMetrics, signals: list | None = None) -> str:
    """Return HTML for a styled stock card.

    Displays:
    - Ticker + name + type badge
    - Price + 1d change + 30d change (coloured)
    - Momentum score bar
    - Trend label
    - RSI gauge
    - Top signal (if signals provided)
    """
    # Resolve top signal for this card from raw signals list if supplied
    sig_direction  = metrics.top_signal_direction
    sig_conviction = metrics.top_signal_conviction
    sig_count      = metrics.signal_count
    sig_name       = ""
    if signals:
        ticker_sigs = [s for s in signals if getattr(s, "ticker", None) == metrics.ticker]
        if ticker_sigs:
            top = sorted(ticker_sigs, key=lambda s: getattr(s, "strength", 0), reverse=True)[0]
            sig_direction  = getattr(top, "direction", sig_direction)
            sig_conviction = getattr(top, "conviction", sig_conviction)
            sig_count      = len(ticker_sigs)
            sig_name       = getattr(top, "signal_name", "")

    price_str    = _fmt_price(metrics.latest_price)
    chg_1d_str   = _fmt_pct(metrics.change_1d_pct)
    chg_30d_str  = _fmt_pct(metrics.change_30d_pct)
    chg_1d_col   = _pct_color(metrics.change_1d_pct)
    chg_30d_col  = _pct_color(metrics.change_30d_pct)
    type_badge   = _type_badge_html(metrics.type)
    trend_badge  = _trend_badge_html(metrics.trend)
    mom_bar      = _momentum_bar_html(metrics.momentum_score)
    rsi_gauge    = _rsi_gauge_html(metrics.rsi_14)

    signal_html = ""
    if sig_count > 0:
        dir_badge   = _direction_badge_html(sig_direction, sig_conviction)
        signal_name_html = f"<div style='font-size:0.75rem;color:#6b7280;margin-top:2px'>{sig_name}</div>" if sig_name else ""
        signal_html = f"""
        <div style='margin-top:10px;padding-top:8px;border-top:1px solid #e5e7eb'>
          <div style='font-size:0.75rem;color:#6b7280;margin-bottom:3px'>
            Top Signal ({sig_count} total)
          </div>
          {dir_badge}
          {signal_name_html}
        </div>"""

    ma20_note = f"MA20: {_fmt_price(metrics.ma_20)} ({metrics.price_vs_ma20})"
    ma50_note = f"MA50: {_fmt_price(metrics.ma_50)} ({metrics.price_vs_ma50})"
    vol_note  = f"Vol (30d ann.): {metrics.volatility_30d:.1%}" if not math.isnan(metrics.volatility_30d) else "Vol: N/A"

    html = f"""
<div style='
  border:1px solid #e5e7eb;
  border-radius:12px;
  padding:16px;
  background:#ffffff;
  box-shadow:0 1px 3px rgba(0,0,0,0.06);
  font-family:sans-serif;
  min-width:220px;
'>
  <!-- Header -->
  <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:6px'>
    <span style='font-size:1.2rem;font-weight:700;color:#111827'>{metrics.ticker}</span>
    {type_badge}
  </div>
  <div style='font-size:0.8rem;color:#6b7280;margin-bottom:12px'>{metrics.name}</div>

  <!-- Price row -->
  <div style='display:flex;align-items:baseline;gap:10px;margin-bottom:4px'>
    <span style='font-size:1.5rem;font-weight:700;color:#111827'>{price_str}</span>
    <span style='color:{chg_1d_col};font-weight:600'>{chg_1d_str} 1d</span>
    <span style='color:{chg_30d_col};font-weight:600'>{chg_30d_str} 30d</span>
  </div>

  <!-- Technicals row -->
  <div style='font-size:0.75rem;color:#6b7280;margin-bottom:8px'>
    {ma20_note} &nbsp;|&nbsp; {ma50_note} &nbsp;|&nbsp; {vol_note}
  </div>

  <!-- Trend + RSI -->
  <div style='display:flex;align-items:center;gap:10px;margin-bottom:8px'>
    {trend_badge}
    <span style='font-size:0.75rem;color:#6b7280'>RSI {rsi_gauge}</span>
  </div>

  <!-- Momentum bar -->
  <div style='margin-bottom:4px'>
    <span style='font-size:0.75rem;color:#6b7280'>Momentum&nbsp;</span>
    {mom_bar}
  </div>

  {signal_html}
</div>"""
    return html


def format_comparison_table_html(comparison: StockComparison) -> str:
    """Return HTML for a comparison table across all shipping stocks.

    Columns: Ticker | Price | 30d | 90d | Volatility | RSI | Trend | Momentum | Signals
    Cells are colour-coded.
    """
    if not comparison.metrics:
        return "<p style='color:#6b7280'>No stock data available.</p>"

    # Sort rows by momentum_score desc (same as ranking)
    sorted_tickers = [r["ticker"] for r in comparison.ranking if r["ticker"] in comparison.metrics]
    # Include any tickers in metrics not in ranking (shouldn't happen, but safety)
    for t in comparison.metrics:
        if t not in sorted_tickers:
            sorted_tickers.append(t)

    header_style = (
        "padding:8px 12px;text-align:left;font-size:0.78rem;font-weight:600;"
        "color:#374151;background:#f9fafb;border-bottom:2px solid #e5e7eb;"
    )
    cell_style = (
        "padding:8px 12px;font-size:0.82rem;color:#111827;"
        "border-bottom:1px solid #f3f4f6;white-space:nowrap;"
    )

    headers = ["Ticker", "Price", "30d", "90d", "Volatility", "RSI", "Trend", "Momentum", "Signals"]
    header_row = "".join(f"<th style='{header_style}'>{h}</th>" for h in headers)

    rows_html = ""
    for ticker in sorted_tickers:
        m = comparison.metrics[ticker]

        price_cell  = f"<td style='{cell_style}'><b>{_fmt_price(m.latest_price)}</b></td>"
        chg30_col   = _pct_color(m.change_30d_pct)
        chg90_col   = _pct_color(m.change_90d_pct)
        chg30_cell  = (
            f"<td style='{cell_style}color:{chg30_col};font-weight:600'>"
            f"{_fmt_pct(m.change_30d_pct)}</td>"
        )
        chg90_cell  = (
            f"<td style='{cell_style}color:{chg90_col};font-weight:600'>"
            f"{_fmt_pct(m.change_90d_pct)}</td>"
        )
        vol_str     = f"{m.volatility_30d:.1%}" if not math.isnan(m.volatility_30d) else "N/A"
        vol_cell    = f"<td style='{cell_style}'>{vol_str}</td>"
        rsi_cell    = f"<td style='{cell_style}'>{_rsi_gauge_html(m.rsi_14)}</td>"
        trend_cell  = f"<td style='{cell_style}'>{_trend_badge_html(m.trend)}</td>"
        mom_cell    = f"<td style='{cell_style}'>{_momentum_bar_html(m.momentum_score, width_px=80)}</td>"

        # Signals cell
        if m.signal_count > 0:
            dir_badge  = _direction_badge_html(m.top_signal_direction, m.top_signal_conviction)
            sig_cell   = f"<td style='{cell_style}'>{dir_badge} <span style='color:#6b7280;font-size:0.72rem'>×{m.signal_count}</span></td>"
        else:
            sig_cell   = f"<td style='{cell_style};color:#9ca3af'>—</td>"

        # Ticker cell with type badge
        type_badge  = _type_badge_html(m.type)
        ticker_cell = (
            f"<td style='{cell_style}'>"
            f"<b style='color:#111827'>{ticker}</b><br>"
            f"{type_badge}"
            f"</td>"
        )

        rows_html += f"<tr>{ticker_cell}{price_cell}{chg30_cell}{chg90_cell}{vol_cell}{rsi_cell}{trend_cell}{mom_cell}{sig_cell}</tr>"

    # Sector summary footer
    avg_30d_str   = _fmt_pct(comparison.sector_avg_change_30d)
    avg_30d_color = _pct_color(comparison.sector_avg_change_30d)
    footer_html   = (
        f"<tr style='background:#f9fafb'>"
        f"<td style='{cell_style}font-weight:600;color:#374151'>Sector Avg</td>"
        f"<td style='{cell_style}'>—</td>"
        f"<td style='{cell_style}color:{avg_30d_color};font-weight:600'>{avg_30d_str}</td>"
        f"<td colspan='6' style='{cell_style}color:#6b7280;font-size:0.75rem'>"
        f"Best: <b>{comparison.best_performer_30d}</b> &nbsp;|&nbsp; "
        f"Worst: <b>{comparison.worst_performer_30d}</b> &nbsp;|&nbsp; "
        f"Most Vol: <b>{comparison.most_volatile}</b> &nbsp;|&nbsp; "
        f"Top Momentum: <b>{comparison.best_momentum}</b>"
        f"</td>"
        f"</tr>"
    )

    html = f"""
<div style='overflow-x:auto;'>
<table style='
  border-collapse:collapse;
  width:100%;
  font-family:sans-serif;
  background:#ffffff;
  border-radius:10px;
  box-shadow:0 1px 3px rgba(0,0,0,0.07);
  overflow:hidden;
'>
  <thead><tr>{header_row}</tr></thead>
  <tbody>
    {rows_html}
    {footer_html}
  </tbody>
</table>
</div>"""
    return html
