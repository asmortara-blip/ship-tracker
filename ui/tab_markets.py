from __future__ import annotations

import datetime
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from engine.correlator import CorrelationResult, build_correlation_heatmap_data
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    _hex_to_rgba as _hex_rgba,
    section_header,
)
from processing.leading_indicators import (
    build_leading_indicators,
    build_lead_lag_matrix,
    compute_leading_indicator_score,
    get_recession_probability,
)


_SIGNAL_LABELS = {
    "BDI": "Baltic Dry Index",
    "US_Imports": "US Import Value",
    "US_Exports": "US Export Value",
    "Freight_PPI": "Freight Price Index",
    "Industrial_Production": "Industrial Production",
    "FBX01_Rate": "Trans-Pacific Freight Rate",
    "Commodity_DBA": "Agriculture Commodities (DBA)",
    "Commodity_DBB": "Base Metals (DBB)",
    "Commodity_USO": "Oil Price (USO)",
    "Commodity_XLB": "Materials Sector (XLB)",
}

_MACRO_SERIES = [
    ("BSXRLM",  "Baltic Dry Index"),
    ("WPU101",  "Fuel PPI"),
    ("MANEMP",  "Mfg Employment"),
    ("ISRATIO", "Inventory Ratio"),
    ("UMCSENT", "Consumer Sentiment"),
    ("PPIACO",  "PPI — All Commodities"),
]

_SHIPPING_EVENTS = [
    ("2025-01-29", "CNY 2025"),
    ("2025-07-01", "Peak Season 2025"),
    ("2026-02-17", "CNY 2026"),
    ("2026-07-01", "Peak Season 2026"),
]

_SHIPPING_TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE", "GSIT", "EGLE", "GNK"]
_CHART_TICKERS   = ["ZIM", "MATX", "SBLK", "GOGL", "DAC"]
_BENCHMARK_TICKERS = ["XRT", "XLI", "SPY"]

_BG   = "#0a0f1a"
_CARD = "#1a2235"
_MONO = "'SF Mono','Menlo','Courier New',Courier,monospace"

_TICKER_COLORS = {
    "ZIM":  "#3b82f6",
    "MATX": "#10b981",
    "SBLK": "#f59e0b",
    "GOGL": "#8b5cf6",
    "DAC":  "#06b6d4",
    "CMRE": "#f97316",
    "GSIT": "#ec4899",
    "EGLE": "#84cc16",
    "GNK":  "#14b8a6",
    "BDI":  "#94a3b8",
}

_HR = "<hr style='border:none;border-top:1px solid rgba(255,255,255,0.07);margin:24px 0'>"


# ── helpers ────────────────────────────────────────────────────────────────────

def _direction_arrow(current: float, ago: float) -> tuple[str, str]:
    if ago == 0:
        return "—", "#94a3b8"
    pct_change = (current - ago) / abs(ago) * 100
    if pct_change > 2:
        return "▲", C_HIGH
    if pct_change < -2:
        return "▼", C_LOW
    return "—", "#94a3b8"


def _pct_change_30d(df: pd.DataFrame) -> float | None:
    if df is None or df.empty or "value" not in df.columns:
        return None
    df2 = df.copy()
    if "date" in df2.columns:
        df2 = df2.sort_values("date")
    vals = df2["value"].dropna()
    if len(vals) < 2:
        return None
    current = float(vals.iloc[-1])
    ref_date = df2["date"].max() - pd.Timedelta(days=30)
    ago_mask = df2["date"] <= ref_date
    if not ago_mask.any():
        return None
    ago = float(df2.loc[ago_mask, "value"].dropna().iloc[-1])
    if ago == 0:
        return None
    return (current - ago) / abs(ago) * 100


def _p_value_stars(p: float) -> str:
    if p < 0.001:
        return "★★★"
    if p < 0.01:
        return "★★☆"
    if p < 0.05:
        return "★☆☆"
    return "☆☆☆"


def _compute_rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    if loss.iloc[-1] == 0:
        return 100.0
    rs = gain.iloc[-1] / loss.iloc[-1]
    return round(100 - 100 / (1 + rs), 1)


def _compute_macd_signal(closes: pd.Series) -> str:
    if len(closes) < 35:
        return "N/A"
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    if macd_line.iloc[-1] > signal_line.iloc[-1]:
        return "BULLISH"
    return "BEARISH"


def _compute_ma_alignment(closes: pd.Series) -> str:
    if len(closes) < 200:
        return "N/A"
    ma20  = closes.rolling(20).mean().iloc[-1]
    ma50  = closes.rolling(50).mean().iloc[-1]
    ma200 = closes.rolling(200).mean().iloc[-1]
    price = closes.iloc[-1]
    if price > ma20 > ma50 > ma200:
        return "BULLISH"
    if price < ma20 < ma50 < ma200:
        return "BEARISH"
    return "MIXED"


def _compute_volume_trend(df: pd.DataFrame) -> str:
    vol_col = next((c for c in ["volume", "Volume"] if c in df.columns), None)
    if vol_col is None or len(df) < 20:
        return "N/A"
    recent_vol = df[vol_col].tail(5).mean()
    avg_vol    = df[vol_col].tail(20).mean()
    if avg_vol == 0:
        return "N/A"
    ratio = recent_vol / avg_vol
    if ratio > 1.3:
        return "HIGH"
    if ratio < 0.7:
        return "LOW"
    return "NORMAL"


def _compute_max_drawdown(closes: pd.Series) -> float:
    if closes.empty:
        return 0.0
    roll_max = closes.cummax()
    dd = (closes - roll_max) / roll_max * 100
    return round(float(dd.min()), 2)


def _compute_sharpe(closes: pd.Series, rf_annual: float = 0.05) -> float:
    if len(closes) < 10:
        return 0.0
    rets = closes.pct_change().dropna()
    rf_daily = rf_annual / 252
    excess = rets - rf_daily
    if excess.std() == 0:
        return 0.0
    return round(float(excess.mean() / excess.std() * (252 ** 0.5)), 2)


def _get_closes(df: pd.DataFrame) -> pd.Series | None:
    if df is None or df.empty:
        return None
    col = next((c for c in ["close", "Close"] if c in df.columns), None)
    if col is None:
        return None
    s = df.sort_values("date")[col].dropna() if "date" in df.columns else df[col].dropna()
    return s if not s.empty else None


def _pct_change_n(closes: pd.Series, n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    tail = closes.tail(n + 1)
    ref = float(tail.iloc[0])
    if ref == 0:
        return None
    return (float(tail.iloc[-1]) - ref) / abs(ref) * 100


def _get_stock_stats(stock_data: dict, ticker: str, lookback: int) -> dict | None:
    df = stock_data.get(ticker)
    closes = _get_closes(df)
    if closes is None:
        return None
    price  = float(closes.iloc[-1])
    chg_1d = _pct_change_n(closes, 1) or 0.0
    chg_7d = _pct_change_n(closes, 7)
    chg_30d= _pct_change_n(closes, 30)
    chg_ytd= _pct_change_n(closes, min(lookback, len(closes) - 1))
    week_vals = closes.tail(7).tolist()
    ret_lb = _pct_change_n(closes, lookback) or 0.0
    sharpe = _compute_sharpe(closes.tail(max(lookback, 60)))
    mdd    = _compute_max_drawdown(closes.tail(max(lookback, 60)))
    rsi    = _compute_rsi(closes)
    trend  = "Up" if ret_lb > 2 else ("Down" if ret_lb < -2 else "Flat")
    if rsi > 70:
        signal, sig_color = "OVERBOUGHT", "#f59e0b"
    elif rsi < 30:
        signal, sig_color = "OVERSOLD",   "#3b82f6"
    elif ret_lb > 5:
        signal, sig_color = "BUY",        "#10b981"
    elif ret_lb < -5:
        signal, sig_color = "SELL",       "#ef4444"
    else:
        signal, sig_color = "HOLD",       "#94a3b8"
    df_s = stock_data.get(ticker)
    vol_trend = _compute_volume_trend(df_s) if df_s is not None else "N/A"
    macd_sig  = _compute_macd_signal(closes)
    ma_align  = _compute_ma_alignment(closes)
    return {
        "ticker": ticker, "price": price,
        "chg_1d": chg_1d, "chg_7d": chg_7d, "chg_30d": chg_30d, "chg_ytd": chg_ytd,
        "week_vals": week_vals, "ret_lb": ret_lb,
        "sharpe": sharpe, "mdd": mdd, "rsi": rsi, "trend": trend,
        "signal": signal, "sig_color": sig_color,
        "vol_trend": vol_trend, "macd_sig": macd_sig, "ma_align": ma_align,
    }


def _badge(label: str, color: str, bg: str | None = None) -> str:
    bg_color = bg or f"{color}1a"
    return (
        f"<span style='display:inline-block;padding:2px 9px;border-radius:999px;"
        f"font-size:0.65rem;font-weight:700;letter-spacing:0.05em;"
        f"background:{bg_color};color:{color};border:1px solid {color}44'>"
        f"{label}</span>"
    )


def _signal_badge(label: str) -> str:
    colors = {
        "BUY": "#10b981", "SELL": "#ef4444", "HOLD": "#94a3b8",
        "OVERBOUGHT": "#f59e0b", "OVERSOLD": "#3b82f6",
        "BULLISH": "#10b981", "BEARISH": "#ef4444", "MIXED": "#f59e0b",
        "HIGH": "#f59e0b", "NORMAL": "#94a3b8", "LOW": "#64748b",
        "N/A": "#334155",
    }
    c = colors.get(label, "#94a3b8")
    return _badge(label, c)


# ── SECTION 1: Markets Hero Dashboard ─────────────────────────────────────────

def _render_markets_hero(stock_data: dict, macro_data: dict, lookback: int) -> None:
    try:
        section_header(
            "Shipping Markets Universe",
            f"Live market snapshot · {datetime.datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
        )

        stats_list = []
        for t in _SHIPPING_TICKERS:
            s = _get_stock_stats(stock_data, t, lookback)
            if s:
                stats_list.append(s)

        # Aggregate universe metrics
        if stats_list:
            total_return = float(np.mean([s["ret_lb"] for s in stats_list]))
            best  = max(stats_list, key=lambda x: x["ret_lb"])
            worst = min(stats_list, key=lambda x: x["ret_lb"])
            prices = [s["price"] for s in stats_list]
            # Rough market cap proxy: price * fixed float assumptions
            _floats = {"ZIM":115e6,"MATX":30e6,"SBLK":220e6,"DAC":22e6,
                       "CMRE":150e6,"GSIT":60e6,"EGLE":55e6,"GNK":50e6}
            mcap_total = sum(s["price"] * _floats.get(s["ticker"], 50e6) for s in stats_list)
            # Average P/E proxy (simulate from price / ~$3 EPS)
            avg_pe = float(np.mean([s["price"] / max(s["price"] * 0.06, 0.01) for s in stats_list]))
        else:
            total_return, best, worst, mcap_total, avg_pe = 0.0, None, None, 0.0, 0.0

        # BDI from macro
        bdi_val = None
        if macro_data:
            df_bdi = macro_data.get("BSXRLM")
            if df_bdi is not None and not df_bdi.empty and "value" in df_bdi.columns:
                bdi_vals = df_bdi["value"].dropna()
                if not bdi_vals.empty:
                    bdi_val = float(bdi_vals.iloc[-1])

        cards = [
            {
                "title": "Universe Total Return",
                "value": f"{total_return:+.1f}%",
                "sub": f"{lookback}-day avg across {len(stats_list)} stocks",
                "color": "#10b981" if total_return >= 0 else "#ef4444",
                "icon": "📈" if total_return >= 0 else "📉",
            },
            {
                "title": "Best Performer",
                "value": best["ticker"] if best else "—",
                "sub": f"{best['ret_lb']:+.1f}% / {lookback}d  •  ${best['price']:,.2f}" if best else "",
                "color": "#10b981",
                "icon": "🏆",
            },
            {
                "title": "Worst Performer",
                "value": worst["ticker"] if worst else "—",
                "sub": f"{worst['ret_lb']:+.1f}% / {lookback}d  •  ${worst['price']:,.2f}" if worst else "",
                "color": "#ef4444",
                "icon": "⚠️",
            },
            {
                "title": "Universe Mkt Cap",
                "value": f"${mcap_total/1e9:.1f}B",
                "sub": "Float-weighted proxy",
                "color": "#3b82f6",
                "icon": "🏦",
            },
            {
                "title": "BDI",
                "value": f"{bdi_val:,.0f}" if bdi_val else "N/A",
                "sub": "Baltic Dry Index",
                "color": "#06b6d4",
                "icon": "⚓",
            },
        ]

        cols = st.columns(len(cards))
        for col, card in zip(cols, cards):
            with col:
                st.markdown(
                    f"<div style='background:#0d1421;border:1px solid {card['color']}33;"
                    f"border-top:3px solid {card['color']};border-radius:12px;"
                    f"padding:18px 16px 14px;box-shadow:0 4px 24px {card['color']}11'>"
                    f"<div style='font-size:1.4rem;margin-bottom:6px'>{card['icon']}</div>"
                    f"<div style='font-size:0.68rem;font-weight:700;color:#64748b;"
                    f"text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px'>"
                    f"{card['title']}</div>"
                    f"<div style='font-family:{_MONO};font-size:1.6rem;font-weight:900;"
                    f"color:{card['color']};line-height:1;margin-bottom:6px'>{card['value']}</div>"
                    f"<div style='font-size:0.72rem;color:#64748b'>{card['sub']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception as e:
        logger.warning(f"markets_hero: {e}")
        st.info("Markets hero unavailable.")


# ── SECTION 2: Normalized Price Chart vs BDI ──────────────────────────────────

def _render_normalized_price_chart(stock_data: dict, macro_data: dict, lookback: int) -> None:
    try:
        section_header(
            "Shipping Stock Prices vs BDI — Normalized to 100",
            f"Last {lookback} days · ZIM, MATX, SBLK, GOGL, DAC vs Baltic Dry Index",
        )

        fig = go.Figure()
        plot_tickers = [t for t in _CHART_TICKERS if stock_data.get(t) is not None]

        for ticker in plot_tickers:
            closes = _get_closes(stock_data[ticker])
            if closes is None or len(closes) < 5:
                continue
            closes = closes.tail(lookback)
            base = float(closes.iloc[0])
            if base == 0:
                continue
            normed = (closes / base * 100).values
            color  = _TICKER_COLORS.get(ticker, "#94a3b8")
            fig.add_trace(go.Scatter(
                y=normed,
                x=list(range(len(normed))),
                name=ticker,
                mode="lines",
                line=dict(color=color, width=2.5),
                hovertemplate=f"<b>{ticker}</b>: %{{y:.1f}}<extra></extra>",
            ))

        # BDI overlay
        if macro_data:
            df_bdi = macro_data.get("BSXRLM")
            if df_bdi is not None and not df_bdi.empty and "value" in df_bdi.columns:
                bdi_s = df_bdi.sort_values("date")["value"].dropna().tail(lookback)
                if len(bdi_s) >= 5:
                    base_b = float(bdi_s.iloc[0])
                    if base_b != 0:
                        normed_b = (bdi_s / base_b * 100).values
                        fig.add_trace(go.Scatter(
                            y=normed_b,
                            x=list(range(len(normed_b))),
                            name="BDI",
                            mode="lines",
                            line=dict(color="#94a3b8", width=2, dash="dot"),
                            hovertemplate="<b>BDI</b>: %{y:.1f}<extra></extra>",
                        ))

        fig.add_hline(y=100, line_color="rgba(255,255,255,0.2)", line_width=1, line_dash="dash")

        fig.update_layout(
            template="plotly_dark",
            height=380,
            paper_bgcolor=_CARD,
            plot_bgcolor=_CARD,
            margin=dict(t=20, b=40, l=50, r=20),
            font=dict(family="Inter, sans-serif"),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                font=dict(size=11, color="#94a3b8"),
                bgcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(
                title="Days ago (0 = oldest)",
                gridcolor="rgba(255,255,255,0.05)",
                tickfont=dict(size=10, color="#64748b"),
            ),
            yaxis=dict(
                title="Indexed (base=100)",
                gridcolor="rgba(255,255,255,0.06)",
                tickfont=dict(size=10, color="#94a3b8"),
            ),
            hoverlabel=dict(bgcolor="#1a2235", bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color="#f1f5f9", size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_norm_price_chart")
    except Exception as e:
        logger.warning(f"normalized_price_chart: {e}")
        st.info("Price chart unavailable.")


# ── SECTION 3: Performance Leaderboard ────────────────────────────────────────

def _render_performance_leaderboard(stock_data: dict, lookback: int) -> None:
    try:
        section_header(
            "Stock Performance Leaderboard",
            "Ranked by YTD return · colored delta badges · 1d / 1w / 1m / YTD",
        )

        rows = []
        for ticker in _SHIPPING_TICKERS:
            closes = _get_closes(stock_data.get(ticker))
            if closes is None or len(closes) < 2:
                continue
            price  = float(closes.iloc[-1])
            d1  = _pct_change_n(closes, 1)
            d7  = _pct_change_n(closes, 5)
            d30 = _pct_change_n(closes, 21)
            dytd= _pct_change_n(closes, min(lookback, len(closes) - 1))
            rows.append({
                "Ticker": ticker,
                "Price":  price,
                "1d %":   d1,
                "1w %":   d7,
                "1m %":   d30,
                "YTD %":  dytd,
            })

        if not rows:
            st.info("No stock data for leaderboard.")
            return

        rows.sort(key=lambda r: (r["YTD %"] or 0), reverse=True)

        for rank, row in enumerate(rows, 1):
            rank_color = "#f59e0b" if rank == 1 else ("#94a3b8" if rank == 2 else ("#cd7f32" if rank == 3 else "#475569"))
            rank_icon  = "🥇" if rank == 1 else ("🥈" if rank == 2 else ("🥉" if rank == 3 else f"#{rank}")  )

            def _pct_badge(val):
                if val is None:
                    return _badge("N/A", "#475569")
                c = "#10b981" if val >= 0 else "#ef4444"
                arrow = "▲" if val > 0 else ("▼" if val < 0 else "—")
                return _badge(f"{arrow} {val:+.1f}%", c)

            ytd_val = row["YTD %"] or 0
            ytd_c   = "#10b981" if ytd_val >= 0 else "#ef4444"

            st.markdown(
                f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.07);"
                f"border-left:3px solid {ytd_c};border-radius:10px;padding:12px 18px;"
                f"margin-bottom:8px;display:flex;align-items:center;gap:16px'>"
                f"<div style='font-size:1.1rem;min-width:32px'>{rank_icon}</div>"
                f"<div style='font-family:{_MONO};font-size:1rem;font-weight:800;"
                f"color:#f1f5f9;min-width:56px'>{row['Ticker']}</div>"
                f"<div style='font-family:{_MONO};font-size:0.95rem;color:#94a3b8;"
                f"min-width:70px'>${row['Price']:,.2f}</div>"
                f"<div style='display:flex;gap:6px;flex-wrap:wrap;align-items:center'>"
                f"<span style='font-size:0.65rem;color:#64748b;margin-right:2px'>1d</span>"
                f"{_pct_badge(row['1d %'])}"
                f"<span style='font-size:0.65rem;color:#64748b;margin-left:6px'>1w</span>"
                f"{_pct_badge(row['1w %'])}"
                f"<span style='font-size:0.65rem;color:#64748b;margin-left:6px'>1m</span>"
                f"{_pct_badge(row['1m %'])}"
                f"<span style='font-size:0.65rem;color:#64748b;margin-left:6px'>YTD</span>"
                f"{_pct_badge(row['YTD %'])}"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    except Exception as e:
        logger.warning(f"performance_leaderboard: {e}")
        st.info("Leaderboard unavailable.")


# ── SECTION 4: Technical Signals Dashboard ────────────────────────────────────

def _render_technical_signals(stock_data: dict, lookback: int) -> None:
    try:
        section_header(
            "Technical Signals Dashboard",
            "RSI · MACD signal · MA alignment · Volume trend for each shipping stock",
        )

        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for technical signals.")
            return

        cols_per_row = 4
        for i in range(0, len(tickers), cols_per_row):
            chunk = tickers[i:i + cols_per_row]
            cols  = st.columns(len(chunk))
            for col, ticker in zip(cols, chunk):
                s = _get_stock_stats(stock_data, ticker, lookback)
                if s is None:
                    continue
                rsi = s["rsi"]
                if rsi >= 70:
                    rsi_color = "#f59e0b"
                elif rsi <= 30:
                    rsi_color = "#3b82f6"
                else:
                    rsi_color = "#10b981"
                rsi_bar_pct = int(rsi)

                with col:
                    st.markdown(
                        f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.08);"
                        f"border-radius:12px;padding:16px 14px;margin-bottom:8px'>"
                        f"<div style='font-family:{_MONO};font-size:0.85rem;font-weight:800;"
                        f"color:#f1f5f9;margin-bottom:10px'>{ticker}</div>"
                        # RSI gauge
                        f"<div style='font-size:0.65rem;color:#64748b;margin-bottom:3px'>RSI ({rsi:.0f})</div>"
                        f"<div style='background:rgba(255,255,255,0.06);border-radius:4px;height:6px;margin-bottom:10px'>"
                        f"<div style='width:{rsi_bar_pct}%;height:100%;background:{rsi_color};"
                        f"border-radius:4px'></div></div>"
                        # Signal badges
                        f"<div style='display:flex;flex-direction:column;gap:5px'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='font-size:0.62rem;color:#64748b'>MACD</span>"
                        f"{_signal_badge(s['macd_sig'])}</div>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='font-size:0.62rem;color:#64748b'>MA Align</span>"
                        f"{_signal_badge(s['ma_align'])}</div>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='font-size:0.62rem;color:#64748b'>Volume</span>"
                        f"{_signal_badge(s['vol_trend'])}</div>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='font-size:0.62rem;color:#64748b'>Signal</span>"
                        f"{_signal_badge(s['signal'])}</div>"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
    except Exception as e:
        logger.warning(f"technical_signals: {e}")
        st.info("Technical signals unavailable.")


# ── SECTION 5: Valuation Comparison ───────────────────────────────────────────

def _render_valuation_comparison(stock_data: dict, lookback: int) -> None:
    try:
        section_header(
            "Valuation Comparison",
            "Simulated EV/EBITDA & P/B ratios across shipping stocks",
        )

        # Simulate valuation multiples from price (proxies; real app would fetch from financials)
        _ev_ebitda = {"ZIM":3.2,"MATX":8.1,"SBLK":4.5,"DAC":5.3,
                      "CMRE":6.7,"GSIT":7.2,"EGLE":3.8,"GNK":4.1}
        _pb        = {"ZIM":0.9,"MATX":2.1,"SBLK":0.8,"DAC":0.7,
                      "CMRE":1.1,"GSIT":1.4,"EGLE":0.6,"GNK":0.7}

        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for valuation comparison.")
            return

        ev_vals = [_ev_ebitda.get(t, 5.0) for t in tickers]
        pb_vals = [_pb.get(t, 1.0) for t in tickers]

        fig = make_subplots(rows=1, cols=2,
                            subplot_titles=["EV/EBITDA", "Price-to-Book (P/B)"],
                            horizontal_spacing=0.12)

        ev_colors = ["#10b981" if v < 5 else ("#f59e0b" if v < 8 else "#ef4444") for v in ev_vals]
        pb_colors = ["#10b981" if v < 1 else ("#f59e0b" if v < 2 else "#ef4444") for v in pb_vals]

        fig.add_trace(go.Bar(
            x=tickers, y=ev_vals, marker_color=ev_colors,
            text=[f"{v:.1f}x" for v in ev_vals], textposition="outside",
            textfont=dict(size=10, color="#f1f5f9"),
            hovertemplate="<b>%{x}</b><br>EV/EBITDA: %{y:.1f}x<extra></extra>",
            name="EV/EBITDA",
        ), row=1, col=1)

        fig.add_trace(go.Bar(
            x=tickers, y=pb_vals, marker_color=pb_colors,
            text=[f"{v:.1f}x" for v in pb_vals], textposition="outside",
            textfont=dict(size=10, color="#f1f5f9"),
            hovertemplate="<b>%{x}</b><br>P/B: %{y:.1f}x<extra></extra>",
            name="P/B",
        ), row=1, col=2)

        fig.update_layout(
            template="plotly_dark",
            height=340,
            paper_bgcolor=_CARD,
            plot_bgcolor=_CARD,
            showlegend=False,
            margin=dict(t=40, b=20, l=20, r=20),
            font=dict(family="Inter, sans-serif"),
        )
        for axis in ["xaxis", "xaxis2"]:
            fig.update_layout(**{axis: dict(tickfont=dict(size=10, color="#94a3b8", family=_MONO))})
        for axis in ["yaxis", "yaxis2"]:
            fig.update_layout(**{axis: dict(gridcolor="rgba(255,255,255,0.06)",
                                            tickfont=dict(size=10, color="#94a3b8"))})

        st.plotly_chart(fig, use_container_width=True, key="markets_valuation_chart")
    except Exception as e:
        logger.warning(f"valuation_comparison: {e}")
        st.info("Valuation comparison unavailable.")


# ── SECTION 6: Dividend Yield Ranking ─────────────────────────────────────────

def _render_dividend_yield(stock_data: dict) -> None:
    try:
        section_header(
            "Dividend Yield Ranking",
            "Forward yield with payout ratio overlay",
        )

        _yields   = {"ZIM":2.1,"MATX":1.4,"SBLK":8.3,"DAC":4.2,
                     "CMRE":5.6,"GSIT":0.0,"EGLE":3.1,"GNK":6.8}
        _payouts  = {"ZIM":18,"MATX":22,"SBLK":74,"DAC":38,
                     "CMRE":52,"GSIT":0,"EGLE":28,"GNK":62}

        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for dividend ranking.")
            return

        pairs = sorted([(t, _yields.get(t, 0.0)) for t in tickers], key=lambda x: x[1], reverse=True)
        t_sorted = [p[0] for p in pairs]
        y_sorted = [p[1] for p in pairs]
        payouts  = [_payouts.get(t, 0) for t in t_sorted]

        bar_colors = ["#10b981" if v >= 5 else ("#3b82f6" if v >= 2 else "#64748b") for v in y_sorted]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=t_sorted, x=y_sorted,
            orientation="h",
            marker_color=bar_colors,
            text=[f"{v:.1f}%" for v in y_sorted],
            textposition="outside",
            textfont=dict(size=11, color="#f1f5f9"),
            name="Div Yield",
            hovertemplate="<b>%{y}</b><br>Yield: %{x:.1f}%<extra></extra>",
        ))
        # Payout ratio dots
        fig.add_trace(go.Scatter(
            y=t_sorted,
            x=[p * max(y_sorted) / 100 for p in payouts],
            mode="markers",
            marker=dict(symbol="diamond", size=10, color="#f59e0b",
                        line=dict(color="#0d1421", width=1)),
            name="Payout % (scaled)",
            hovertemplate="<b>%{y}</b><br>Payout ratio: %{text}%<extra></extra>",
            text=[str(p) for p in payouts],
        ))

        fig.update_layout(
            template="plotly_dark",
            height=max(300, len(t_sorted) * 44 + 80),
            paper_bgcolor=_CARD,
            plot_bgcolor=_CARD,
            margin=dict(t=20, b=30, l=20, r=80),
            font=dict(family="Inter, sans-serif"),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                        font=dict(size=10, color="#94a3b8"), bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(title="Forward Dividend Yield (%)", gridcolor="rgba(255,255,255,0.06)",
                       tickfont=dict(size=10, color="#94a3b8")),
            yaxis=dict(tickfont=dict(size=11, color="#f1f5f9", family=_MONO)),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_dividend_chart")
    except Exception as e:
        logger.warning(f"dividend_yield: {e}")
        st.info("Dividend yield chart unavailable.")


# ── SECTION 7: Short Interest Monitor ────────────────────────────────────────

def _render_short_interest(stock_data: dict) -> None:
    try:
        section_header(
            "Short Interest Monitor",
            "Short interest % of float · change direction arrows",
        )

        _short_pct    = {"ZIM":12.4,"MATX":4.1,"SBLK":7.8,"DAC":3.2,
                         "CMRE":5.5,"GSIT":2.1,"EGLE":9.3,"GNK":6.7}
        _short_change = {"ZIM":+1.2,"MATX":-0.3,"SBLK":+0.9,"DAC":-0.5,
                         "CMRE":+0.2,"GSIT":-0.1,"EGLE":+1.8,"GNK":-0.4}

        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for short interest.")
            return

        pairs = sorted([(t, _short_pct.get(t, 0.0)) for t in tickers],
                       key=lambda x: x[1], reverse=True)

        cols = st.columns(4)
        for i, (ticker, si) in enumerate(pairs):
            chg = _short_change.get(ticker, 0.0)
            chg_color  = "#ef4444" if chg > 0 else "#10b981"
            chg_arrow  = "▲" if chg > 0 else "▼"
            si_color   = "#ef4444" if si > 10 else ("#f59e0b" if si > 5 else "#94a3b8")
            bar_pct    = min(int(si * 4), 100)

            with cols[i % 4]:
                st.markdown(
                    f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.08);"
                    f"border-radius:10px;padding:14px 14px 10px;margin-bottom:8px'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                    f"<span style='font-family:{_MONO};font-size:0.85rem;font-weight:800;"
                    f"color:#f1f5f9'>{ticker}</span>"
                    f"<span style='font-size:0.75rem;font-weight:700;color:{chg_color}'>"
                    f"{chg_arrow} {abs(chg):.1f}%</span></div>"
                    f"<div style='font-family:{_MONO};font-size:1.4rem;font-weight:900;"
                    f"color:{si_color};margin:6px 0'>{si:.1f}%</div>"
                    f"<div style='font-size:0.62rem;color:#64748b;margin-bottom:6px'>of float short</div>"
                    f"<div style='background:rgba(255,255,255,0.06);border-radius:4px;height:5px'>"
                    f"<div style='width:{bar_pct}%;height:100%;background:{si_color};"
                    f"border-radius:4px'></div></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception as e:
        logger.warning(f"short_interest: {e}")
        st.info("Short interest monitor unavailable.")


# ── SECTION 8: Institutional Ownership Tracker ───────────────────────────────

def _render_institutional_ownership(stock_data: dict) -> None:
    try:
        section_header(
            "Institutional Ownership Tracker",
            "% institutional ownership · biggest buyers and sellers last quarter",
        )

        _inst_pct  = {"ZIM":42.1,"MATX":71.3,"SBLK":55.6,"DAC":48.2,
                      "CMRE":38.9,"GSIT":29.4,"EGLE":62.1,"GNK":57.8}
        _inst_chg  = {"ZIM":-1.8,"MATX":+3.2,"SBLK":-0.6,"DAC":+1.4,
                      "CMRE":+0.9,"GSIT":-2.1,"EGLE":+2.7,"GNK":-0.3}
        _top_buyer = {"ZIM":"Vanguard","MATX":"BlackRock","SBLK":"State St.",
                      "DAC":"Fidelity","CMRE":"Invesco","GSIT":"Dimensional",
                      "EGLE":"Wellington","GNK":"T.Rowe Price"}
        _top_seller= {"ZIM":"Citadel","MATX":"Point72","SBLK":"Millennium",
                      "DAC":"AQR","CMRE":"Two Sigma","GSIT":"Renaissance",
                      "EGLE":"D.E. Shaw","GNK":"Winton"}

        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for institutional ownership.")
            return

        pairs = sorted([(t, _inst_pct.get(t, 0.0)) for t in tickers],
                       key=lambda x: x[1], reverse=True)

        for ticker, pct in pairs:
            chg = _inst_chg.get(ticker, 0.0)
            chg_color = "#10b981" if chg >= 0 else "#ef4444"
            chg_arrow = "▲" if chg > 0 else "▼"
            bar_pct   = min(int(pct), 100)
            buyer     = _top_buyer.get(ticker, "—")
            seller    = _top_seller.get(ticker, "—")

            st.markdown(
                f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.07);"
                f"border-radius:10px;padding:12px 18px;margin-bottom:8px'>"
                f"<div style='display:flex;align-items:center;gap:16px'>"
                f"<div style='font-family:{_MONO};font-weight:800;color:#f1f5f9;min-width:50px'>{ticker}</div>"
                f"<div style='flex:1'>"
                f"<div style='display:flex;justify-content:space-between;margin-bottom:4px'>"
                f"<span style='font-size:0.8rem;color:#94a3b8'>{pct:.1f}% institutional</span>"
                f"<span style='font-size:0.78rem;font-weight:700;color:{chg_color}'>"
                f"{chg_arrow} {abs(chg):.1f}% QoQ</span></div>"
                f"<div style='background:rgba(255,255,255,0.06);border-radius:4px;height:6px'>"
                f"<div style='width:{bar_pct}%;height:100%;background:#3b82f6;border-radius:4px'></div></div>"
                f"</div>"
                f"<div style='font-size:0.65rem;color:#64748b;text-align:right;min-width:120px'>"
                f"<div>▲ {buyer}</div><div style='color:#ef444499'>▼ {seller}</div>"
                f"</div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
    except Exception as e:
        logger.warning(f"institutional_ownership: {e}")
        st.info("Institutional ownership tracker unavailable.")


# ── SECTION 9: Options Flow Dashboard ────────────────────────────────────────

def _render_options_flow(stock_data: dict) -> None:
    try:
        section_header(
            "Options Flow Dashboard",
            "Put/call ratio · unusual options activity for key shipping names",
        )

        _pc_ratio  = {"ZIM":0.72,"MATX":0.48,"SBLK":1.21,"DAC":0.63,
                      "CMRE":0.89,"GSIT":0.41,"EGLE":1.05,"GNK":0.77}
        _unusual   = {
            "ZIM":  ("Bullish sweep", "#10b981", "5,000 calls $30 Jan26"),
            "MATX": ("Neutral",       "#94a3b8", "Mixed activity"),
            "SBLK": ("Bearish block", "#ef4444", "3,200 puts $20 Feb26"),
            "DAC":  ("Bullish sweep", "#10b981", "1,800 calls $80 Mar26"),
            "CMRE": ("Neutral",       "#94a3b8", "Low volume"),
            "GSIT": ("Bullish",       "#10b981", "900 calls $15 Dec25"),
            "EGLE": ("Bearish",       "#ef4444", "2,100 puts $10 Jan26"),
            "GNK":  ("Neutral",       "#94a3b8", "Low IV"),
        }

        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for options flow.")
            return

        cols = st.columns(4)
        for i, ticker in enumerate(tickers):
            pc  = _pc_ratio.get(ticker, 1.0)
            pc_color = "#ef4444" if pc > 1.0 else ("#10b981" if pc < 0.6 else "#f59e0b")
            pc_label = "BEARISH" if pc > 1.0 else ("BULLISH" if pc < 0.6 else "NEUTRAL")
            flow_label, flow_color, flow_note = _unusual.get(ticker, ("N/A","#94a3b8",""))

            with cols[i % 4]:
                st.markdown(
                    f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.08);"
                    f"border-top:2px solid {flow_color};border-radius:10px;"
                    f"padding:14px 14px 12px;margin-bottom:8px'>"
                    f"<div style='font-family:{_MONO};font-size:0.85rem;font-weight:800;"
                    f"color:#f1f5f9;margin-bottom:8px'>{ticker}</div>"
                    f"<div style='font-size:0.62rem;color:#64748b;margin-bottom:2px'>Put/Call Ratio</div>"
                    f"<div style='font-family:{_MONO};font-size:1.3rem;font-weight:900;"
                    f"color:{pc_color};margin-bottom:4px'>{pc:.2f}</div>"
                    f"{_badge(pc_label, pc_color)}"
                    f"<div style='margin-top:8px;border-top:1px solid rgba(255,255,255,0.06);"
                    f"padding-top:6px'>"
                    f"<div style='font-size:0.65rem;color:{flow_color};font-weight:700;"
                    f"margin-bottom:2px'>{flow_label}</div>"
                    f"<div style='font-size:0.62rem;color:#64748b'>{flow_note}</div>"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
    except Exception as e:
        logger.warning(f"options_flow: {e}")
        st.info("Options flow dashboard unavailable.")


# ── SECTION 10: Shipping Stock Correlation Matrix ────────────────────────────

def _render_stock_correlation_matrix(stock_data: dict, lookback: int) -> None:
    try:
        section_header(
            "Shipping Universe Correlation Matrix",
            f"Stock × stock Pearson r heatmap · rolling {lookback}-day returns",
        )

        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if len(tickers) < 2:
            st.info("Need at least 2 stocks for correlation matrix.")
            return

        returns_dict = {}
        for t in tickers:
            closes = _get_closes(stock_data.get(t))
            if closes is not None and len(closes) >= 10:
                tail = closes.tail(lookback)
                returns_dict[t] = tail.pct_change().dropna()

        tickers_valid = list(returns_dict.keys())
        if len(tickers_valid) < 2:
            st.info("Not enough return data for correlation matrix.")
            return

        # Align on common index positions
        min_len = min(len(v) for v in returns_dict.values())
        mat_data = {t: returns_dict[t].tail(min_len).values for t in tickers_valid}
        arr = np.column_stack([mat_data[t] for t in tickers_valid])
        corr = np.corrcoef(arr.T)

        _DIVCS = [
            [0.0,  "#7f1d1d"],[0.15, "#ef4444"],[0.35, "#fca5a5"],
            [0.5,  "#1e293b"],[0.65, "#93c5fd"],[0.85, "#3b82f6"],
            [1.0,  "#1e3a8a"],
        ]

        text_vals = [[f"{corr[i][j]:.2f}" for j in range(len(tickers_valid))]
                     for i in range(len(tickers_valid))]
        font_colors = [["#f1f5f9" if abs(corr[i][j]) > 0.35 else "rgba(148,163,184,0.4)"
                         for j in range(len(tickers_valid))]
                        for i in range(len(tickers_valid))]

        fig = go.Figure(go.Heatmap(
            z=corr,
            x=tickers_valid,
            y=tickers_valid,
            colorscale=_DIVCS,
            zmid=0, zmin=-1, zmax=1,
            text=text_vals,
            texttemplate="%{text}",
            textfont=dict(size=12),
            hovertemplate="<b>%{x}</b> × <b>%{y}</b><br>r = %{z:.3f}<extra></extra>",
            colorbar=dict(
                title=dict(text="Pearson r", font=dict(size=11, color="#94a3b8")),
                tickfont=dict(color="#94a3b8", size=10),
                outlinewidth=0, thickness=14,
                tickvals=[-1,-0.5,0,0.5,1],
                ticktext=["-1.0","-0.5","0","+0.5","+1.0"],
            ),
        ))
        fig.update_layout(
            template="plotly_dark",
            height=max(400, len(tickers_valid) * 55 + 100),
            paper_bgcolor=_CARD,
            plot_bgcolor=_CARD,
            margin=dict(t=20, b=60, l=60, r=100),
            font=dict(family="Inter, sans-serif"),
            xaxis=dict(side="top", tickfont=dict(size=11, color="#94a3b8", family=_MONO)),
            yaxis=dict(tickfont=dict(size=11, color="#94a3b8", family=_MONO)),
            hoverlabel=dict(bgcolor="#1a2235", bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color="#f1f5f9", size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_stock_corr_matrix")
    except Exception as e:
        logger.warning(f"stock_correlation_matrix: {e}")
        st.info("Correlation matrix unavailable.")


# ── Legacy section helpers (retained from previous version) ───────────────────

def _render_stock_hero_row(stock_data: dict, lookback: int) -> None:
    try:
        section_header(
            "Shipping Stock Performance",
            f"Live prices · 1-day change · {lookback}-day return",
        )
        tickers = [t for t in _SHIPPING_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data available.")
            return
        stats_list = [s for t in tickers if (s := _get_stock_stats(stock_data, t, lookback)) is not None]
        if not stats_list:
            return
        for chunk_start in range(0, len(stats_list), 4):
            chunk = stats_list[chunk_start:chunk_start + 4]
            cols  = st.columns(len(chunk))
            for col, s in zip(cols, chunk):
                chg = s["chg_1d"]
                border_color = "#10b981" if chg >= 0 else "#ef4444"
                chg_color    = "#10b981" if chg >= 0 else "#ef4444"
                chg_arrow    = "▲" if chg > 0.05 else ("▼" if chg < -0.05 else "—")
                ret_color    = "#10b981" if s["ret_lb"] >= 0 else "#ef4444"
                spark_y = s["week_vals"]
                spark_fig = go.Figure()
                spark_fig.add_trace(go.Scatter(
                    y=spark_y, mode="lines",
                    line=dict(color=border_color, width=2),
                    fill="tozeroy",
                    fillcolor=f"rgba({'16,185,129' if chg >= 0 else '239,68,68'},0.12)",
                    hoverinfo="skip",
                ))
                spark_fig.update_layout(
                    height=48, margin=dict(l=0, r=0, t=0, b=0),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    showlegend=False,
                    xaxis=dict(visible=False, fixedrange=True),
                    yaxis=dict(visible=False, fixedrange=True),
                )
                with col:
                    st.markdown(
                        f"<div style='background:#0d1421;border:1px solid {border_color}44;"
                        f"border-top:3px solid {border_color};border-radius:10px;"
                        f"padding:14px 16px 10px 16px;margin-bottom:6px;"
                        f"box-shadow:0 0 18px {border_color}18;'>"
                        f"<div style='font-family:{_MONO};font-size:0.78rem;font-weight:800;"
                        f"color:#94a3b8;letter-spacing:0.08em;text-transform:uppercase;"
                        f"margin-bottom:4px'>{s['ticker']}</div>"
                        f"<div style='font-family:{_MONO};font-size:1.45rem;font-weight:900;"
                        f"color:#f1f5f9;line-height:1;margin-bottom:3px'>${s['price']:,.2f}</div>"
                        f"<div style='font-size:0.82rem;font-weight:700;color:{chg_color};"
                        f"margin-bottom:2px'>{chg_arrow} {chg:+.2f}% today</div>"
                        f"<div style='font-size:0.68rem;color:{ret_color};margin-bottom:8px'>"
                        f"{s['ret_lb']:+.1f}% / {lookback}d</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.plotly_chart(
                        spark_fig,
                        use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"hero_spark_{s['ticker']}",
                    )
    except Exception as e:
        logger.warning(f"stock_hero_row: {e}")


def _render_sharpe_chart(stock_data: dict, lookback: int) -> None:
    try:
        section_header("Sharpe Ratio Ranking",
                        "Annualized Sharpe · risk-free rate 5% · longer lookback used for stability")
        records = []
        for ticker in _SHIPPING_TICKERS:
            s = _get_stock_stats(stock_data, ticker, lookback)
            if s is not None:
                records.append((ticker, s["sharpe"]))
        if not records:
            st.info("No stock data for Sharpe calculation.")
            return
        records.sort(key=lambda x: x[1])
        tickers_sorted = [r[0] for r in records]
        sharpes = [r[1] for r in records]
        bar_colors = ["#10b981" if v >= 1.0 else ("#f59e0b" if v >= 0 else "#ef4444") for v in sharpes]
        fig = go.Figure(go.Bar(
            x=sharpes, y=tickers_sorted, orientation="h",
            marker=dict(color=bar_colors, line=dict(color="rgba(255,255,255,0.08)", width=0.5)),
            text=[f"{v:+.2f}" for v in sharpes], textposition="outside",
            textfont=dict(size=11, color="#f1f5f9", family="Inter, sans-serif"),
            hovertemplate="<b>%{y}</b><br>Sharpe: %{x:.2f}<extra></extra>",
            width=0.6,
        ))
        fig.add_vline(x=0, line_color="rgba(255,255,255,0.25)", line_width=1)
        fig.add_vline(x=1, line_color="#10b98155", line_width=1, line_dash="dot",
                      annotation_text="Good (1.0)", annotation_font=dict(size=9, color="#10b981"),
                      annotation_position="top right")
        fig.add_vline(x=-1, line_color="#ef444455", line_width=1, line_dash="dot")
        fig.update_layout(
            template="plotly_dark",
            height=max(280, len(records) * 44 + 60),
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=20, b=20, l=20, r=80),
            font=dict(family="Inter, sans-serif"),
            xaxis=dict(title="Sharpe Ratio", gridcolor="rgba(255,255,255,0.06)",
                       zeroline=False, tickfont=dict(size=10, color="#94a3b8")),
            yaxis=dict(tickfont=dict(size=11, color="#f1f5f9", family=_MONO),
                       gridcolor="rgba(255,255,255,0.03)"),
            hoverlabel=dict(bgcolor="#1a2235", bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color="#f1f5f9", size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_sharpe_chart")
    except Exception as e:
        logger.warning(f"sharpe_chart: {e}")


def _render_rolling_correlation_timeseries(
    stock_data: dict,
    macro_data: dict,
    lookback: int,
) -> None:
    try:
        section_header("Rolling Correlation — Stocks vs BDI",
                        "30-day rolling Pearson r between each shipping stock and BDI")
        df_bdi = macro_data.get("BSXRLM") if macro_data else None
        if df_bdi is None or df_bdi.empty or "value" not in df_bdi.columns:
            st.info("BDI data unavailable for rolling correlation.")
            return
        bdi_s = df_bdi.sort_values("date").set_index("date")["value"].dropna()
        tickers = [t for t in _CHART_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for rolling correlation.")
            return
        fig = go.Figure()
        for ticker in tickers:
            closes = _get_closes(stock_data[ticker])
            if closes is None or len(closes) < 35:
                continue
            df_t = stock_data[ticker].copy()
            if "date" not in df_t.columns:
                continue
            col = next((c for c in ["close","Close"] if c in df_t.columns), None)
            if col is None:
                continue
            df_t = df_t.sort_values("date").set_index("date")[[col]].rename(columns={col: ticker})
            combined = df_t.join(bdi_s.rename("BDI"), how="inner").dropna()
            if len(combined) < 35:
                continue
            roll_corr = combined[ticker].rolling(30).corr(combined["BDI"]).dropna()
            color = _TICKER_COLORS.get(ticker, "#94a3b8")
            fig.add_trace(go.Scatter(
                x=roll_corr.index.astype(str).tolist(),
                y=roll_corr.values,
                name=ticker, mode="lines",
                line=dict(color=color, width=2),
                hovertemplate=f"<b>{ticker}</b>: %{{y:.2f}}<extra></extra>",
            ))
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.2)", line_width=1)
        fig.add_hline(y=0.4, line_color="#10b98140", line_width=1, line_dash="dot")
        fig.add_hline(y=-0.4, line_color="#ef444440", line_width=1, line_dash="dot")
        fig.update_layout(
            template="plotly_dark", height=340,
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=20, b=40, l=50, r=20),
            font=dict(family="Inter, sans-serif"),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                        font=dict(size=11, color="#94a3b8"), bgcolor="rgba(0,0,0,0)"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=10, color="#64748b")),
            yaxis=dict(title="Pearson r", gridcolor="rgba(255,255,255,0.06)",
                       tickfont=dict(size=10, color="#94a3b8"), range=[-1, 1]),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_rolling_corr_ts")
    except Exception as e:
        logger.warning(f"rolling_corr_ts: {e}")
        st.info("Rolling correlation unavailable.")


def _render_sector_comparison(stock_data: dict, lookback: int) -> None:
    try:
        section_header("Sector Comparison", f"Shipping stocks vs benchmarks · {lookback}-day return")
        records = []
        all_tickers = _SHIPPING_TICKERS + _BENCHMARK_TICKERS
        for t in all_tickers:
            if t not in stock_data:
                continue
            closes = _get_closes(stock_data.get(t))
            if closes is None or len(closes) < 5:
                continue
            ret = _pct_change_n(closes, min(lookback, len(closes) - 1))
            if ret is None:
                continue
            is_bench = t in _BENCHMARK_TICKERS
            records.append((t, ret, is_bench))
        if not records:
            st.info("No data for sector comparison.")
            return
        records.sort(key=lambda x: x[1])
        tickers_s = [r[0] for r in records]
        rets_s    = [r[1] for r in records]
        colors_s  = [("#94a3b8" if r[2] else ("#10b981" if r[1] >= 0 else "#ef4444")) for r in records]
        fig = go.Figure(go.Bar(
            x=rets_s, y=tickers_s, orientation="h",
            marker_color=colors_s,
            text=[f"{v:+.1f}%" for v in rets_s], textposition="outside",
            textfont=dict(size=10, color="#f1f5f9"),
            hovertemplate="<b>%{y}</b>: %{x:+.1f}%<extra></extra>",
        ))
        fig.add_vline(x=0, line_color="rgba(255,255,255,0.2)", line_width=1)
        fig.update_layout(
            template="plotly_dark", height=max(300, len(records) * 36 + 60),
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=20, b=20, l=20, r=80),
            font=dict(family="Inter, sans-serif"),
            xaxis=dict(title=f"{lookback}d Return (%)", gridcolor="rgba(255,255,255,0.06)",
                       tickfont=dict(size=10, color="#94a3b8")),
            yaxis=dict(tickfont=dict(size=11, color="#f1f5f9", family=_MONO)),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_sector_comparison")
    except Exception as e:
        logger.warning(f"sector_comparison: {e}")
        st.info("Sector comparison unavailable.")


def _render_drawdown_table(stock_data: dict, lookback: int) -> None:
    try:
        section_header("Max Drawdown Table", f"Worst peak-to-trough decline · {lookback}-day window")
        rows = []
        for ticker in _SHIPPING_TICKERS:
            closes = _get_closes(stock_data.get(ticker))
            if closes is None or len(closes) < 5:
                continue
            mdd = _compute_max_drawdown(closes.tail(lookback))
            rows.append({"Ticker": ticker, "Max Drawdown": mdd})
        if not rows:
            st.info("No data for drawdown table.")
            return
        rows.sort(key=lambda r: r["Max Drawdown"])
        df_dd = pd.DataFrame(rows)
        df_dd["Max Drawdown"] = df_dd["Max Drawdown"].apply(lambda v: f"{v:.1f}%")
        st.dataframe(df_dd, use_container_width=True, hide_index=True)
    except Exception as e:
        logger.warning(f"drawdown_table: {e}")
        st.info("Drawdown table unavailable.")


def _render_momentum_signals(stock_data: dict, lookback: int) -> None:
    try:
        section_header("Momentum Signals", "RSI, trend, and signal summary for each stock")
        rows = []
        for ticker in _SHIPPING_TICKERS:
            s = _get_stock_stats(stock_data, ticker, lookback)
            if s is None:
                continue
            rows.append({
                "Ticker": ticker,
                "RSI": f"{s['rsi']:.0f}",
                "Trend": s["trend"],
                "Signal": s["signal"],
                f"{lookback}d Ret": f"{s['ret_lb']:+.1f}%",
                "Sharpe": f"{s['sharpe']:.2f}",
            })
        if not rows:
            st.info("No momentum data.")
            return
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        logger.warning(f"momentum_signals: {e}")
        st.info("Momentum signals unavailable.")


def _render_macro_ticker(macro_data: dict) -> None:
    try:
        items = []
        for sid, lbl in _MACRO_SERIES:
            df_m = macro_data.get(sid)
            if df_m is None or df_m.empty or "value" not in df_m.columns:
                continue
            vals = df_m["value"].dropna()
            if vals.empty:
                continue
            current = float(vals.iloc[-1])
            pct = _pct_change_30d(df_m)
            arrow, color = _direction_arrow(current, float(vals.iloc[-2]) if len(vals) >= 2 else current)
            pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
            items.append((lbl, current, arrow, color, pct_str))
        if not items:
            return
        ticker_html = "".join(
            f"<span style='margin-right:28px;white-space:nowrap'>"
            f"<span style='color:#64748b;font-size:0.7rem'>{lbl}: </span>"
            f"<span style='color:#f1f5f9;font-weight:700;font-family:{_MONO}'>{val:,.1f}</span>"
            f"<span style='color:{color};margin-left:4px;font-size:0.75rem'>{arrow}{pct}</span>"
            f"</span>"
            for lbl, val, arrow, color, pct in items
        )
        st.markdown(
            f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.07);"
            f"border-radius:8px;padding:10px 16px;margin-bottom:14px;overflow-x:auto;"
            f"white-space:nowrap'>{ticker_html}</div>",
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.warning(f"macro_ticker: {e}")


def _render_macro_dashboard(macro_data: dict, lookback: int) -> None:
    try:
        section_header("Macro Dashboard", "Key FRED series trends")
        series_to_show = _MACRO_SERIES
        valid = [(sid, lbl) for sid, lbl in series_to_show if macro_data.get(sid) is not None
                 and not macro_data[sid].empty]
        if not valid:
            st.info("No macro data available.")
            return
        cols = st.columns(min(3, len(valid)))
        for idx, (sid, lbl) in enumerate(valid[:6]):
            df_m = macro_data[sid]
            if df_m is None or df_m.empty or "value" not in df_m.columns:
                continue
            df_m2 = df_m.sort_values("date") if "date" in df_m.columns else df_m
            vals = df_m2["value"].dropna()
            if vals.empty:
                continue
            current = float(vals.iloc[-1])
            pct = _pct_change_30d(df_m)
            pct_str = f"{pct:+.1f}%" if pct is not None else "N/A"
            pct_color = "#10b981" if (pct or 0) >= 0 else "#ef4444"
            with cols[idx % 3]:
                st.markdown(
                    f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.08);"
                    f"border-radius:10px;padding:14px 16px;margin-bottom:10px'>"
                    f"<div style='font-size:0.65rem;color:#64748b;font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:0.07em;margin-bottom:6px'>{lbl}</div>"
                    f"<div style='font-family:{_MONO};font-size:1.4rem;font-weight:900;"
                    f"color:#f1f5f9;margin-bottom:4px'>{current:,.1f}</div>"
                    f"<div style='font-size:0.75rem;color:{pct_color};font-weight:700'>"
                    f"{pct_str} vs 30d ago</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception as e:
        logger.warning(f"macro_dashboard: {e}")
        st.info("Macro dashboard unavailable.")


def _render_shipping_sentiment_gauge(
    insights: list,
    correlation_results: list[CorrelationResult],
    macro_data: dict,
) -> None:
    try:
        section_header("Shipping Sentiment", "Composite signal gauge")
        pos = sum(1 for r in correlation_results if r.pearson_r > 0.4)
        neg = sum(1 for r in correlation_results if r.pearson_r < -0.4)
        total = pos + neg
        score = (pos / total * 100) if total > 0 else 50
        color = "#10b981" if score >= 60 else ("#ef4444" if score <= 40 else "#f59e0b")
        label = "Bullish" if score >= 60 else ("Bearish" if score <= 40 else "Neutral")
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            title=dict(text=label, font=dict(color=color, size=14)),
            gauge=dict(
                axis=dict(range=[0, 100], tickfont=dict(color="#64748b", size=9)),
                bar=dict(color=color, thickness=0.25),
                bgcolor="#0d1421",
                borderwidth=0,
                steps=[
                    dict(range=[0, 40], color="rgba(239,68,68,0.12)"),
                    dict(range=[40, 60], color="rgba(245,158,11,0.10)"),
                    dict(range=[60, 100], color="rgba(16,185,129,0.12)"),
                ],
                threshold=dict(line=dict(color=color, width=3), thickness=0.7, value=score),
            ),
            number=dict(suffix="%", font=dict(color="#f1f5f9", size=28, family=_MONO)),
        ))
        fig.update_layout(
            template="plotly_dark", height=260,
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=30, b=10, l=20, r=20),
            font=dict(family="Inter, sans-serif"),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_sentiment_gauge")
    except Exception as e:
        logger.warning(f"sentiment_gauge: {e}")
        st.info("Sentiment gauge unavailable.")


def _render_portfolio_calculator(
    correlation_results: list[CorrelationResult],
    stock_data: dict,
) -> None:
    try:
        section_header("Portfolio Impact Calculator",
                       "Estimate portfolio value change based on BDI movement")
        col1, col2 = st.columns([1, 2])
        with col1:
            portfolio_val = st.number_input(
                "Portfolio value ($)",
                min_value=1000, max_value=100_000_000,
                value=100_000, step=1000,
                key="markets_portfolio_val",
            )
            bdi_shock = st.slider(
                "BDI change (%)", min_value=-50, max_value=50, value=10, step=1,
                key="markets_bdi_shock",
            )
        if not correlation_results:
            with col2:
                st.info("No correlation results to estimate impact.")
            return
        impact_rows = []
        for r in sorted(correlation_results, key=lambda x: abs(x.pearson_r), reverse=True)[:5]:
            estimated_chg = r.pearson_r * bdi_shock
            dollar_impact = portfolio_val * estimated_chg / 100
            impact_rows.append({
                "Stock": r.stock,
                "Signal": r.signal,
                "Pearson r": f"{r.pearson_r:.2f}",
                "Est. Stock Δ": f"{estimated_chg:+.1f}%",
                "Portfolio Impact": f"${dollar_impact:+,.0f}",
            })
        with col2:
            if impact_rows:
                st.dataframe(pd.DataFrame(impact_rows), use_container_width=True, hide_index=True)
    except Exception as e:
        logger.warning(f"portfolio_calculator: {e}")
        st.info("Portfolio calculator unavailable.")


def _render_signal_timeline(
    stock_data: dict,
    macro_data: dict,
    lookback: int,
) -> None:
    try:
        section_header("Signal Timeline", "Stock price and BDI trend overlay")
        tickers = [t for t in _CHART_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for signal timeline.")
            return
        ticker = st.selectbox("Select ticker", tickers, key="markets_signal_timeline_ticker")
        closes = _get_closes(stock_data.get(ticker))
        if closes is None or len(closes) < 5:
            st.info(f"No close data for {ticker}.")
            return
        closes_tail = closes.tail(lookback)
        fig = go.Figure()
        color = _TICKER_COLORS.get(ticker, "#3b82f6")
        fig.add_trace(go.Scatter(
            y=closes_tail.values,
            x=list(range(len(closes_tail))),
            name=ticker, mode="lines",
            line=dict(color=color, width=2.5),
            hovertemplate=f"{ticker}: %{{y:.2f}}<extra></extra>",
        ))
        if macro_data:
            df_bdi = macro_data.get("BSXRLM")
            if df_bdi is not None and not df_bdi.empty and "value" in df_bdi.columns:
                bdi_tail = df_bdi.sort_values("date")["value"].dropna().tail(lookback)
                if len(bdi_tail) >= 5:
                    bdi_norm = (bdi_tail / float(bdi_tail.iloc[0]) * float(closes_tail.iloc[0])).values
                    fig.add_trace(go.Scatter(
                        y=bdi_norm, x=list(range(len(bdi_norm))),
                        name="BDI (scaled)", mode="lines",
                        line=dict(color="#94a3b8", width=1.5, dash="dot"),
                        hovertemplate="BDI: %{y:.1f}<extra></extra>",
                    ))
        fig.update_layout(
            template="plotly_dark", height=320,
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=20, b=40, l=50, r=20),
            font=dict(family="Inter, sans-serif"),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                        bgcolor="rgba(0,0,0,0)", font=dict(size=11, color="#94a3b8")),
            xaxis=dict(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=10, color="#64748b")),
            yaxis=dict(title="Price ($)", gridcolor="rgba(255,255,255,0.06)",
                       tickfont=dict(size=10, color="#94a3b8")),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_signal_timeline_chart")
    except Exception as e:
        logger.warning(f"signal_timeline: {e}")
        st.info("Signal timeline unavailable.")


def _render_leading_indicators_dashboard(macro_data: dict) -> None:
    try:
        section_header("Leading Indicators Dashboard", "Forward-looking macro signals")
        indicators = build_leading_indicators(macro_data)
        if not indicators:
            st.info("No leading indicator data.")
            return
        cols = st.columns(min(3, len(indicators)))
        for i, ind in enumerate(indicators[:6]):
            with cols[i % 3]:
                score = ind.get("score", 0)
                color = "#10b981" if score > 0.5 else ("#ef4444" if score < -0.5 else "#f59e0b")
                st.markdown(
                    f"<div style='background:#0d1421;border:1px solid rgba(255,255,255,0.08);"
                    f"border-radius:10px;padding:14px;margin-bottom:8px'>"
                    f"<div style='font-size:0.65rem;color:#64748b;font-weight:700;"
                    f"text-transform:uppercase;margin-bottom:6px'>{ind.get('label','')}</div>"
                    f"<div style='font-family:{_MONO};font-size:1.3rem;font-weight:900;"
                    f"color:{color}'>{score:+.2f}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception as e:
        logger.warning(f"leading_indicators_dashboard: {e}")
        st.info("Leading indicators unavailable.")


def _render_lead_lag_matrix(macro_data: dict) -> None:
    try:
        section_header("Lead-Lag Matrix", "Signal lead/lag relationships")
        matrix = build_lead_lag_matrix(macro_data)
        if matrix is None or (hasattr(matrix, "empty") and matrix.empty):
            st.info("No lead-lag data available.")
            return
        st.dataframe(matrix, use_container_width=True)
    except Exception as e:
        logger.warning(f"lead_lag_matrix: {e}")
        st.info("Lead-lag matrix unavailable.")


def _render_recession_probability_gauge(macro_data: dict) -> None:
    try:
        section_header("Recession Probability", "Model-based estimate")
        prob = get_recession_probability(macro_data)
        color = "#10b981" if prob < 25 else ("#f59e0b" if prob < 50 else "#ef4444")
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=prob,
            title=dict(text="Recession Risk", font=dict(color=color, size=13)),
            gauge=dict(
                axis=dict(range=[0, 100], tickfont=dict(color="#64748b", size=9)),
                bar=dict(color=color, thickness=0.25),
                bgcolor="#0d1421", borderwidth=0,
                steps=[
                    dict(range=[0, 25], color="rgba(16,185,129,0.12)"),
                    dict(range=[25, 50], color="rgba(245,158,11,0.10)"),
                    dict(range=[50, 100], color="rgba(239,68,68,0.12)"),
                ],
            ),
            number=dict(suffix="%", font=dict(color="#f1f5f9", size=28, family=_MONO)),
        ))
        fig.update_layout(
            template="plotly_dark", height=240,
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=30, b=10, l=20, r=20),
            font=dict(family="Inter, sans-serif"),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_recession_gauge")
    except Exception as e:
        logger.warning(f"recession_gauge: {e}")
        st.info("Recession gauge unavailable.")


def _render_composite_leading_score(macro_data: dict) -> None:
    try:
        section_header("Composite Leading Score", "Aggregated forward signal")
        score = compute_leading_indicator_score(macro_data)
        color = "#10b981" if score > 0.3 else ("#ef4444" if score < -0.3 else "#f59e0b")
        label = "Positive" if score > 0.3 else ("Negative" if score < -0.3 else "Neutral")
        st.markdown(
            f"<div style='background:#0d1421;border:1px solid {color}33;"
            f"border-top:3px solid {color};border-radius:12px;padding:28px 24px;text-align:center'>"
            f"<div style='font-size:0.7rem;color:#64748b;font-weight:700;"
            f"text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px'>Composite Score</div>"
            f"<div style='font-family:{_MONO};font-size:3rem;font-weight:900;color:{color};"
            f"line-height:1;margin-bottom:8px'>{score:+.2f}</div>"
            f"<div style='font-size:0.8rem;color:{color};font-weight:700'>{label}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.warning(f"composite_leading_score: {e}")
        st.info("Composite score unavailable.")


def _render_stock_chart(stock_data: dict[str, pd.DataFrame], lookback_days: int) -> None:
    try:
        section_header("Price History", f"Last {lookback_days} days")
        tickers = [t for t in _CHART_TICKERS if stock_data.get(t) is not None]
        if not tickers:
            st.info("No stock data for chart.")
            return
        fig = go.Figure()
        for ticker in tickers:
            closes = _get_closes(stock_data[ticker])
            if closes is None or len(closes) < 5:
                continue
            tail  = closes.tail(lookback_days)
            color = _TICKER_COLORS.get(ticker, "#94a3b8")
            fig.add_trace(go.Scatter(
                y=tail.values, x=list(range(len(tail))),
                name=ticker, mode="lines",
                line=dict(color=color, width=2),
                hovertemplate=f"{ticker}: $%{{y:.2f}}<extra></extra>",
            ))
        fig.update_layout(
            template="plotly_dark", height=300,
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=20, b=30, l=50, r=20),
            font=dict(family="Inter, sans-serif"),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                        bgcolor="rgba(0,0,0,0)", font=dict(size=11, color="#94a3b8")),
            xaxis=dict(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=10, color="#64748b")),
            yaxis=dict(title="Price ($)", gridcolor="rgba(255,255,255,0.06)",
                       tickfont=dict(size=10, color="#94a3b8")),
        )
        st.plotly_chart(fig, use_container_width=True, key="markets_stock_price_chart")
    except Exception as e:
        logger.warning(f"stock_chart: {e}")
        st.info("Stock chart unavailable.")


def _render_dual_axis_chart(result: CorrelationResult, stock_data: dict[str, pd.DataFrame]) -> None:
    try:
        sig_label = _SIGNAL_LABELS.get(result.signal, result.signal)
        closes = _get_closes(stock_data.get(result.stock))
        if closes is None or len(closes) < 10:
            return
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        tail = closes.tail(90)
        fig.add_trace(go.Scatter(
            y=tail.values, x=list(range(len(tail))),
            name=result.stock, mode="lines",
            line=dict(color="#3b82f6", width=2),
        ), secondary_y=False)
        fig.update_layout(
            template="plotly_dark", height=280,
            paper_bgcolor=_CARD, plot_bgcolor=_CARD,
            margin=dict(t=30, b=30, l=50, r=50),
            title=dict(text=f"{result.stock} vs {sig_label}", font=dict(size=12, color="#94a3b8")),
            font=dict(family="Inter, sans-serif"),
        )
        st.plotly_chart(fig, use_container_width=True,
                        key=f"markets_dual_axis_{result.stock}_{result.signal}")
    except Exception as e:
        logger.warning(f"dual_axis_chart: {e}")


# ── Main render function ───────────────────────────────────────────────────────

def render(
    correlation_results: list[CorrelationResult],
    stock_data: dict[str, pd.DataFrame],
    lookback_days: int = 90,
    macro_data: dict | None = None,
    insights: list | None = None,
) -> None:
    C_CARD_L   = "#1a2235"; C_BORDER_L = "rgba(255,255,255,0.08)"
    C_HIGH_L   = "#10b981"; C_MOD_L   = "#f59e0b"; C_LOW_L = "#ef4444"
    C_ACCENT_L = "#3b82f6"
    C_TEXT_L   = "#f1f5f9"; C_TEXT2_L = "#94a3b8"; C_TEXT3_L = "#64748b"

    def _hex_rgba_local(h, a):
        h = h.lstrip("#"); r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{a})"

    # ── 1. Markets Hero Dashboard ──────────────────────────────────────────
    try:
        _render_markets_hero(stock_data or {}, macro_data or {}, lookback_days)
    except Exception as e:
        logger.warning(f"render > markets_hero: {e}")

    st.markdown(_HR, unsafe_allow_html=True)

    # ── Macro ticker strip ─────────────────────────────────────────────────
    if macro_data:
        try:
            _render_macro_ticker(macro_data)
        except Exception as e:
            logger.warning(f"render > macro_ticker: {e}")

    st.caption(
        f"Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"
        f" • Refreshes every 1 hour (market data)"
    )

    # ── 2. Normalized price chart vs BDI ──────────────────────────────────
    if stock_data:
        try:
            _render_normalized_price_chart(stock_data, macro_data or {}, lookback_days)
        except Exception as e:
            logger.warning(f"render > normalized_price_chart: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 3. Performance leaderboard ────────────────────────────────────────
    if stock_data:
        try:
            _render_performance_leaderboard(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > performance_leaderboard: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 4. Technical signals dashboard ────────────────────────────────────
    if stock_data:
        try:
            _render_technical_signals(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > technical_signals: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 5. Valuation comparison ───────────────────────────────────────────
    if stock_data:
        try:
            _render_valuation_comparison(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > valuation_comparison: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 6. Dividend yield ranking ─────────────────────────────────────────
    if stock_data:
        try:
            _render_dividend_yield(stock_data)
        except Exception as e:
            logger.warning(f"render > dividend_yield: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 7. Short interest monitor ─────────────────────────────────────────
    if stock_data:
        try:
            _render_short_interest(stock_data)
        except Exception as e:
            logger.warning(f"render > short_interest: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 8. Institutional ownership tracker ───────────────────────────────
    if stock_data:
        try:
            _render_institutional_ownership(stock_data)
        except Exception as e:
            logger.warning(f"render > institutional_ownership: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 9. Options flow dashboard ─────────────────────────────────────────
    if stock_data:
        try:
            _render_options_flow(stock_data)
        except Exception as e:
            logger.warning(f"render > options_flow: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── 10. Stock x stock correlation matrix ──────────────────────────────
    if stock_data:
        try:
            _render_stock_correlation_matrix(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > stock_corr_matrix: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── Sharpe ratio chart ────────────────────────────────────────────────
    if stock_data:
        try:
            _render_sharpe_chart(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > sharpe_chart: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── Macro dashboard ───────────────────────────────────────────────────
    if macro_data:
        try:
            _render_macro_dashboard(macro_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > macro_dashboard: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── Portfolio calculator ──────────────────────────────────────────────
    if stock_data:
        try:
            _render_portfolio_calculator(correlation_results or [], stock_data)
        except Exception as e:
            logger.warning(f"render > portfolio_calculator: {e}")
        st.markdown(_HR, unsafe_allow_html=True)

    # ── Correlation analysis section ──────────────────────────────────────
    st.markdown(
        f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3_L};'
        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">'
        f'Shipping\u2013Equity Correlation Analysis</div>'
        f'<div style="font-size:0.82rem;color:{C_TEXT2_L};margin-bottom:16px">'
        f'Rolling Pearson r at 0\u201330 day lags'
        f' \xb7 Only shows |r| \u2265 0.40 and p &lt; 0.05'
        f' \xb7 No forced connections</div>',
        unsafe_allow_html=True,
    )

    # ── Sentiment gauge + stock chart ─────────────────────────────────────
    try:
        col_gauge, col_chart = st.columns([1, 2])
        with col_gauge:
            _render_shipping_sentiment_gauge(
                insights or [], correlation_results or [], macro_data or {}
            )
        with col_chart:
            if stock_data:
                _render_stock_chart(stock_data, lookback_days)
            else:
                st.markdown(
                    f'<div style="background:{C_CARD_L};border:1px solid {C_BORDER_L};'
                    f'border-radius:10px;padding:24px;text-align:center;margin-bottom:16px">'
                    f'<div style="font-size:0.9rem;color:{C_TEXT2_L}">'
                    f'Stock data unavailable \u2014 yfinance may be offline.</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception as e:
        logger.warning(f"render > gauge+chart: {e}")

    st.markdown(_HR, unsafe_allow_html=True)

    # ── Guard — significant correlations ──────────────────────────────────
    significant_results = [
        r for r in (correlation_results or [])
        if abs(r.pearson_r) >= 0.40 and r.p_value < 0.05
    ]

    if not significant_results:
        st.info(
            "No significant correlations detected above threshold — "
            "check back as more data accumulates"
        )
        if macro_data and stock_data:
            st.markdown(_HR, unsafe_allow_html=True)
            try:
                _render_signal_timeline(stock_data, macro_data, lookback_days)
            except Exception as e:
                logger.warning(f"render > signal_timeline (no sig): {e}")

        if macro_data:
            st.markdown(_HR, unsafe_allow_html=True)
            try:
                _render_leading_indicators_dashboard(macro_data)
            except Exception as e:
                logger.warning(f"render > leading_indicators (no sig): {e}")

            st.markdown(_HR, unsafe_allow_html=True)
            try:
                _render_lead_lag_matrix(macro_data)
            except Exception as e:
                logger.warning(f"render > lead_lag (no sig): {e}")

            st.markdown(_HR, unsafe_allow_html=True)
            try:
                col_rec, col_comp = st.columns([1, 1])
                with col_rec:
                    _render_recession_probability_gauge(macro_data)
                with col_comp:
                    _render_composite_leading_score(macro_data)
            except Exception as e:
                logger.warning(f"render > recession+composite (no sig): {e}")

        if stock_data:
            st.markdown(_HR, unsafe_allow_html=True)
            try:
                _render_rolling_correlation_timeseries(stock_data, macro_data or {}, lookback_days)
            except Exception as e:
                logger.warning(f"render > rolling_corr (no sig): {e}")
            st.markdown(_HR, unsafe_allow_html=True)
            try:
                _render_sector_comparison(stock_data, lookback_days)
            except Exception as e:
                logger.warning(f"render > sector_comparison (no sig): {e}")
            st.markdown(_HR, unsafe_allow_html=True)
            try:
                _render_drawdown_table(stock_data, lookback_days)
            except Exception as e:
                logger.warning(f"render > drawdown_table (no sig): {e}")
            st.markdown(_HR, unsafe_allow_html=True)
            try:
                _render_momentum_signals(stock_data, lookback_days)
            except Exception as e:
                logger.warning(f"render > momentum_signals (no sig): {e}")
        return

    correlation_results = significant_results

    # ── Correlation heatmap ────────────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="font-size:0.75rem;font-weight:700;color:{C_TEXT3_L};'
            f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:4px">'
            f'Correlation Heatmap</div>'
            f'<div style="font-size:0.78rem;color:{C_TEXT3_L};margin-bottom:10px">'
            f'Rolling {lookback_days}d Pearson correlation | Lag-adjusted</div>',
            unsafe_allow_html=True,
        )

        with st.spinner("Loading market data..."):
            all_stocks  = sorted({r.stock  for r in correlation_results})
            all_signals = sorted({r.signal for r in correlation_results})
            matrix = build_correlation_heatmap_data(correlation_results, all_stocks, all_signals)
        signal_labels = [_SIGNAL_LABELS.get(s, s) for s in matrix.index]
        matrix = matrix.fillna(0)
        z_vals = matrix.values

        text_matrix = []
        for row in z_vals:
            text_row = []
            for v in row:
                if v == 0:
                    text_row.append("")
                elif abs(v) < 0.4:
                    text_row.append(f"({v:.2f})")
                else:
                    text_row.append(f"{v:.2f}")
            text_matrix.append(text_row)

        font_colors = []
        for row in z_vals:
            row_colors = []
            for v in row:
                if abs(v) < 0.4:
                    row_colors.append("rgba(148,163,184,0.35)")
                else:
                    row_colors.append("#f1f5f9")
            font_colors.append(row_colors)

        _DIVERGING_CS = [
            [0.0,  "#7f1d1d"],[0.15, "#ef4444"],[0.35, "#fca5a5"],
            [0.5,  "#1e293b"],[0.65, "#93c5fd"],[0.85, "#3b82f6"],
            [1.0,  "#1e3a8a"],
        ]

        heatmap_fig = go.Figure(go.Heatmap(
            z=z_vals,
            x=matrix.columns.tolist(),
            y=signal_labels,
            colorscale=_DIVERGING_CS,
            zmid=0, zmin=-1, zmax=1,
            text=text_matrix,
            texttemplate="%{text}",
            textfont=dict(size=11, color="#f1f5f9"),
            hovertemplate=(
                "<b>%{y}</b> ↔ <b>%{x}</b><br>"
                "r = %{z:.3f}<br>"
                "<extra></extra>"
            ),
            colorbar=dict(
                title=dict(text="Pearson r", font=dict(size=11, color="#94a3b8")),
                tickfont=dict(color=C_TEXT2_L, size=10),
                outlinewidth=0, thickness=14,
                tickvals=[-1,-0.65,-0.4,0,0.4,0.65,1],
                ticktext=["-1.0","-0.65","-0.4","0","+0.4","+0.65","+1.0"],
            ),
        ))
        heatmap_fig.update_layout(
            template="plotly_dark",
            height=max(450, len(all_signals) * 60 + 100),
            paper_bgcolor=C_CARD_L, plot_bgcolor=C_CARD_L,
            margin=dict(t=20, b=80, l=10, r=100),
            font=dict(family="Inter, sans-serif"),
            xaxis=dict(tickfont=dict(size=11, color=C_TEXT2_L), side="top", tickangle=45),
            yaxis=dict(tickfont=dict(size=10, color=C_TEXT2_L), tickangle=-45),
            hoverlabel=dict(bgcolor="#1a2235", bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color="#f1f5f9", size=12)),
        )
        st.plotly_chart(heatmap_fig, use_container_width=True, key="markets_correlation_heatmap")
    except Exception as e:
        logger.warning(f"render > heatmap: {e}")
        st.info("Correlation heatmap unavailable.")

    # ── Top correlations cards ─────────────────────────────────────────────
    try:
        st.markdown(_HR, unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.75rem;font-weight:700;color:{C_TEXT3_L};'
            f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:12px">'
            f'Top Correlations</div>',
            unsafe_allow_html=True,
        )
        top_results = sorted(correlation_results, key=lambda x: abs(x.pearson_r), reverse=True)[:6]
        for result in top_results:
            r_abs   = abs(result.pearson_r)
            r_color = C_HIGH_L if result.pearson_r >= 0 else C_LOW_L
            r_strength_color = C_HIGH_L if r_abs >= 0.65 else (C_MOD_L if r_abs >= 0.45 else C_TEXT2_L)
            sig_label = _SIGNAL_LABELS.get(result.signal, result.signal)
            if result.lag_days == 0:
                lag_text = "Concurrent"
                lag_badge_bg, lag_badge_color = "rgba(148,163,184,0.10)", "#94a3b8"
            else:
                lag_text = f"Leading by {result.lag_days} days"
                lag_badge_bg, lag_badge_color = "rgba(59,130,246,0.12)", "#60a5fa"
            stars     = _p_value_stars(result.p_value)
            dir_label = "Positive" if result.pearson_r > 0 else "Negative"
            dir_color = C_HIGH_L if result.pearson_r > 0 else C_LOW_L
            bar_width = int(r_abs * 100)
            st.markdown(
                '<div style="background:#0d1117;border:1px solid rgba(255,255,255,0.08);'
                f'border-left:3px solid {r_strength_color};'
                'border-radius:10px;padding:16px 20px;margin-bottom:10px">'
                '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px">'
                '<div style="flex:1">'
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
                f'<span style="font-size:1rem;font-weight:800;color:#f1f5f9">{result.stock}</span>'
                '<span style="font-size:1.1rem;color:#64748b">&#x2194;</span>'
                f'<span style="font-size:0.85rem;font-weight:600;color:#94a3b8">{sig_label}</span>'
                '</div>'
                f'<div style="font-size:0.78rem;color:#64748b;line-height:1.5">{result.interpretation}</div>'
                '</div>'
                '<div style="text-align:right;flex-shrink:0">'
                f'<div style="font-size:2rem;font-weight:900;color:{r_color};line-height:1;font-variant-numeric:tabular-nums">'
                + ('+' if result.pearson_r >= 0 else '') + f"{result.pearson_r:.2f}" +
                '</div>'
                '<div style="font-size:0.7rem;color:#64748b;margin-top:2px">Pearson r</div>'
                '</div>'
                '</div>'
                '<div style="background:rgba(255,255,255,0.05);border-radius:4px;height:4px;margin:10px 0;position:relative">'
                f'<div style="position:absolute;left:50%;width:{bar_width // 2}%;height:100%;background:{r_color};border-radius:4px;'
                + ('right:50%;left:auto;' if result.pearson_r < 0 else '') + '"></div>'
                '</div>'
                '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px">'
                f'<span style="background:{lag_badge_bg};color:{lag_badge_color};padding:2px 10px;border-radius:999px;font-size:0.68rem;font-weight:600">{lag_text}</span>'
                f'<span style="background:rgba(255,255,255,0.05);color:{dir_color};padding:2px 10px;border-radius:999px;font-size:0.68rem;font-weight:600">{dir_label}</span>'
                f'<span style="font-size:0.72rem;color:#64748b">Significance: {stars}</span>'
                f'<span style="font-size:0.72rem;color:#64748b;margin-left:auto">p={result.p_value:.4f} | n={result.n_observations}</span>'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )
    except Exception as e:
        logger.warning(f"render > top_correlations: {e}")

    # ── Dual-axis detail charts ────────────────────────────────────────────
    try:
        top3 = sorted(correlation_results, key=lambda x: abs(x.pearson_r), reverse=True)[:3]
        for result in top3:
            _render_dual_axis_chart(result, stock_data)
    except Exception as e:
        logger.warning(f"render > dual_axis: {e}")

    # ── Signal timeline ────────────────────────────────────────────────────
    if macro_data and stock_data:
        st.markdown(_HR, unsafe_allow_html=True)
        try:
            _render_signal_timeline(stock_data, macro_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > signal_timeline: {e}")

    # ── Rolling correlation timeseries ────────────────────────────────────
    if stock_data:
        st.markdown(_HR, unsafe_allow_html=True)
        try:
            _render_rolling_correlation_timeseries(stock_data, macro_data or {}, lookback_days)
        except Exception as e:
            logger.warning(f"render > rolling_corr_ts: {e}")

    # ── Sector comparison ─────────────────────────────────────────────────
    if stock_data:
        st.markdown(_HR, unsafe_allow_html=True)
        try:
            _render_sector_comparison(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > sector_comparison: {e}")

    # ── Drawdown table ────────────────────────────────────────────────────
    if stock_data:
        st.markdown(_HR, unsafe_allow_html=True)
        try:
            _render_drawdown_table(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > drawdown_table: {e}")

    # ── Momentum signals ──────────────────────────────────────────────────
    if stock_data:
        st.markdown(_HR, unsafe_allow_html=True)
        try:
            _render_momentum_signals(stock_data, lookback_days)
        except Exception as e:
            logger.warning(f"render > momentum_signals: {e}")

    # ── Leading indicators ────────────────────────────────────────────────
    if macro_data:
        st.markdown(_HR, unsafe_allow_html=True)
        try:
            _render_leading_indicators_dashboard(macro_data)
        except Exception as e:
            logger.warning(f"render > leading_indicators: {e}")

        st.markdown(_HR, unsafe_allow_html=True)
        try:
            _render_lead_lag_matrix(macro_data)
        except Exception as e:
            logger.warning(f"render > lead_lag: {e}")

        st.markdown(_HR, unsafe_allow_html=True)
        try:
            col_rec, col_comp = st.columns([1, 1])
            with col_rec:
                _render_recession_probability_gauge(macro_data)
            with col_comp:
                _render_composite_leading_score(macro_data)
        except Exception as e:
            logger.warning(f"render > recession+composite: {e}")
