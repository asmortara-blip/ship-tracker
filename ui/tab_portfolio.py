"""Portfolio Tracker tab — shipping sector position management, P&L, risk metrics."""
from __future__ import annotations

import datetime
import random

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_TEXT, C_TEXT2, C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    live_data_badge,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)

from engine.carrier_factor_model import (
    build_factor_frame,
    fit_carrier_factors,
    residual_signal_backtest,
    residual_zscore,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_C_LONG    = C_HIGH
_C_SHORT   = C_LOW
_C_NEUTRAL = C_TEXT2

_SECTOR_COLORS = {
    "Container":      "#3572b0",
    "Dry Bulk":       "#2e9e6e",
    "Tanker":         "#c9962b",
    "LNG":            "#7c6eaf",
    "Port Operator":  "#4a90a4",
}

_SECTORS = list(_SECTOR_COLORS.keys())

# Default pre-populated positions: (ticker, sector, shares, avg_cost, beta)
_DEFAULT_POSITIONS = [
    {"ticker": "ZIM",   "sector": "Container",     "shares": 500,   "avg_cost": 18.40,  "beta": 1.85},
    {"ticker": "MATX",  "sector": "Container",     "shares": 200,   "avg_cost": 121.50, "beta": 0.92},
    {"ticker": "DAC",   "sector": "Container",     "shares": 300,   "avg_cost": 74.20,  "beta": 1.12},
    {"ticker": "SBLK",  "sector": "Dry Bulk",      "shares": 800,   "avg_cost": 15.80,  "beta": 1.65},
    {"ticker": "GOGL",  "sector": "Dry Bulk",      "shares": 600,   "avg_cost": 11.25,  "beta": 1.48},
    {"ticker": "STNG",  "sector": "Tanker",        "shares": 400,   "avg_cost": 52.30,  "beta": 1.32},
    {"ticker": "GSL",   "sector": "Container",     "shares": 700,   "avg_cost": 22.10,  "beta": 1.18},
    {"ticker": "HAFNI", "sector": "Tanker",        "shares": 1000,  "avg_cost": 7.85,   "beta": 1.55},
]

# Mock current prices (realistic for 2026 shipping names)
_MOCK_PRICES = {
    "ZIM":   19.82,
    "MATX":  128.45,
    "DAC":   81.60,
    "SBLK":  14.35,
    "GOGL":  12.80,
    "STNG":  55.90,
    "GSL":   24.75,
    "HAFNI": 8.42,
}

# Mock day change pcts
_MOCK_DAY_CHANGE = {
    "ZIM":   +2.14,
    "MATX":  +0.78,
    "DAC":   +1.35,
    "SBLK":  -1.82,
    "GOGL":  +3.21,
    "STNG":  +0.45,
    "GSL":   -0.62,
    "HAFNI": +2.88,
}

_HR = "<hr style='border:none;border-top:1px solid rgba(232,230,225,0.05);margin:20px 0'>"


# ── Cell formatters for wsj_market_table() ────────────────────────────────

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_dollar(v: float, decimals: int = 0) -> str:
    sign = "+" if v > 0 else ""
    if abs(v) >= 1_000_000:
        return f"{sign}${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{sign}${v/1_000:.1f}K"
    return f"{sign}${v:,.{decimals}f}"


def _fmt_dollar_abs(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:,.2f}"


def _fmt_pct(v: float, decimals: int = 2) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def _color(v: float) -> str:
    return _C_LONG if v >= 0 else _C_SHORT


def _get_price(ticker: str, stock_data) -> float:
    """Return current price from stock_data or fall back to mock."""
    try:
        if stock_data is not None:
            if isinstance(stock_data, dict) and ticker in stock_data:
                row = stock_data[ticker]
                if hasattr(row, "get"):
                    price = row.get("price") or row.get("close") or row.get("last")
                    if price:
                        return float(price)
            if isinstance(stock_data, pd.DataFrame) and ticker in stock_data.columns:
                val = stock_data[ticker].dropna().iloc[-1]
                return float(val)
    except Exception:
        pass
    return _MOCK_PRICES.get(ticker, 20.0 + random.uniform(-2, 2))


def _get_day_change_pct(ticker: str) -> float:
    return _MOCK_DAY_CHANGE.get(ticker, random.uniform(-3.5, 3.5))


def _init_positions() -> None:
    if "portfolio_positions" not in st.session_state:
        st.session_state["portfolio_positions"] = [dict(p) for p in _DEFAULT_POSITIONS]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _render_hero() -> None:
    page_header(
        title="Portfolio Tracker",
        subtitle="Shipping sector position management — P&L, risk metrics, and factor attribution.",
        badge_text="PORTFOLIO",
        badge_color=C_ACCENT,
    )


def _build_snapshot(positions: list[dict], stock_data) -> pd.DataFrame:
    """Build holdings DataFrame with live/mock prices."""
    rows = []
    for pos in positions:
        ticker  = pos.get("ticker", "")
        sector  = pos.get("sector", "Unknown")
        shares  = float(pos.get("shares", 0))
        avg_cost = float(pos.get("avg_cost", 0))
        beta    = float(pos.get("beta", 1.0))
        price   = _get_price(ticker, stock_data)
        mkt_val = shares * price
        cost_basis = shares * avg_cost
        pnl_dollar = mkt_val - cost_basis
        pnl_pct    = (pnl_dollar / cost_basis * 100) if cost_basis > 0 else 0.0
        day_chg    = _get_day_change_pct(ticker)
        day_pnl    = mkt_val * day_chg / 100
        rows.append({
            "Ticker":       ticker,
            "Sector":       sector,
            "Shares":       shares,
            "Avg Cost":     avg_cost,
            "Price":        price,
            "Market Value": mkt_val,
            "P&L $":        pnl_dollar,
            "P&L %":        pnl_pct,
            "Day Chg %":    day_chg,
            "Day P&L $":    day_pnl,
            "Beta":         beta,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        total_val = df["Market Value"].sum()
        df["Weight %"] = (df["Market Value"] / total_val * 100).round(2) if total_val > 0 else 0.0
    return df


def _render_summary_metrics(df: pd.DataFrame) -> None:
    """Hero KPI row."""
    try:
        total_val   = df["Market Value"].sum() if not df.empty else 0.0
        day_pnl     = df["Day P&L $"].sum()    if not df.empty else 0.0
        total_pnl   = df["P&L $"].sum()         if not df.empty else 0.0
        cost_total  = (df["Shares"] * df["Avg Cost"]).sum() if not df.empty else 1.0
        total_ret   = (total_pnl / cost_total * 100) if cost_total > 0 else 0.0
        port_beta   = (df["Beta"] * df["Weight %"] / 100).sum() if not df.empty else 1.0

        metric_card_row([
            {"label": "Total Portfolio Value", "value": _fmt_dollar_abs(total_val),
             "accent": C_TEXT,         "sublabel": "Shipping Sector Exposure"},
            {"label": "Day P&L",               "value": _fmt_dollar(day_pnl),
             "accent": _color(day_pnl), "sublabel": "Today's unrealized change"},
            {"label": "Total Return",          "value": _fmt_pct(total_ret),
             "accent": _color(total_ret), "sublabel": f"{_fmt_dollar(total_pnl)} unrealized P&L"},
            {"label": "Portfolio Beta",        "value": f"{port_beta:.2f}",
             "accent": C_ACCENT,        "sublabel": "Weighted avg vs. SPY"},
        ], columns=4)
    except Exception as e:
        logger.warning(f"summary metrics error: {e}")


def _render_add_position_form() -> None:
    """Expander form to add a new position."""
    with st.expander("➕  Add / Edit Position", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            ticker_in = st.text_input("Ticker Symbol", placeholder="e.g. ZIM", key="add_ticker").upper().strip()
        with c2:
            shares_in = st.number_input("Shares", min_value=1, value=100, step=10, key="add_shares")
        with c3:
            cost_in = st.number_input("Avg Cost ($)", min_value=0.01, value=20.00, step=0.01,
                                       format="%.2f", key="add_cost")

        c4, c5, c6 = st.columns(3)
        with c4:
            sector_in = st.selectbox("Sector", _SECTORS, key="add_sector")
        with c5:
            beta_in = st.number_input("Beta", min_value=0.1, max_value=5.0, value=1.2,
                                       step=0.05, format="%.2f", key="add_beta")
        with c6:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            add_btn = st.button("Add Position", type="primary", use_container_width=True)

        if add_btn:
            try:
                if not ticker_in:
                    st.error("Please enter a ticker symbol.")
                else:
                    positions = st.session_state["portfolio_positions"]
                    # Update if exists, else append
                    existing = next((p for p in positions if p["ticker"] == ticker_in), None)
                    if existing:
                        existing["shares"]   = float(shares_in)
                        existing["avg_cost"] = float(cost_in)
                        existing["sector"]   = sector_in
                        existing["beta"]     = float(beta_in)
                        st.success(f"Updated position: {ticker_in}")
                    else:
                        positions.append({
                            "ticker":   ticker_in,
                            "sector":   sector_in,
                            "shares":   float(shares_in),
                            "avg_cost": float(cost_in),
                            "beta":     float(beta_in),
                        })
                        st.success(f"Added {ticker_in} — {shares_in} shares @ ${cost_in:.2f}")
                    st.session_state["portfolio_positions"] = positions
                    st.rerun()
            except Exception as e:
                st.error(f"Error adding position: {e}")

        # Remove position
        st.markdown("<div style='margin-top:12px'></div>", unsafe_allow_html=True)
        positions = st.session_state.get("portfolio_positions", [])
        if positions:
            tickers_list = [p["ticker"] for p in positions]
            rem_ticker = st.selectbox("Remove position", ["— select —"] + tickers_list, key="rem_ticker")
            if st.button("Remove", key="rem_btn") and rem_ticker != "— select —":
                st.session_state["portfolio_positions"] = [
                    p for p in positions if p["ticker"] != rem_ticker
                ]
                st.success(f"Removed {rem_ticker}")
                st.rerun()


def _render_holdings_table(df: pd.DataFrame) -> None:
    """Sector-coded holdings table."""
    section_header("Holdings", "Sector-coded position table with P&L and weight allocation")
    if df.empty:
        st.info("No positions in portfolio. Add one above.")
        return

    try:
        rows = []
        for _, row in df.iterrows():
            pnl_color  = _color(row["P&L $"])
            day_color  = _color(row["Day Chg %"])
            sector_col = _SECTOR_COLORS.get(row["Sector"], C_TEXT2)
            rows.append([
                _sans(row["Ticker"], color=C_TEXT, weight=700),
                badge(row["Sector"], color=sector_col),
                _mono(f"{int(row['Shares']):,}"),
                _mono(f"${row['Avg Cost']:.2f}"),
                _mono(f"${row['Price']:.2f}", color=C_TEXT),
                _mono(_fmt_dollar_abs(row["Market Value"]), color=C_TEXT),
                _mono(_fmt_dollar(row["P&L $"]), color=pnl_color),
                _mono(_fmt_pct(row["P&L %"]), color=pnl_color),
                _mono(_fmt_pct(row["Day Chg %"]), color=day_color),
                _mono(f"{row['Weight %']:.1f}%"),
            ])

        # Footer totals row (rendered as a final row)
        total_val  = df["Market Value"].sum()
        total_pnl  = df["P&L $"].sum()
        total_day  = df["Day P&L $"].sum()
        tot_color  = _color(total_pnl)
        day_color2 = _color(total_day)
        rows.append([
            _sans("TOTAL", color=C_TEXT, weight=800),
            "", "", "", "",
            _mono(_fmt_dollar_abs(total_val), color=C_TEXT),
            _mono(_fmt_dollar(total_pnl), color=tot_color),
            "",
            _mono(f"Day: {_fmt_dollar(total_day)}", color=day_color2),
            _mono("100%"),
        ])

        wsj_market_table(
            ["Ticker", "Sector", "Shares", "Avg Cost", "Price",
             "Mkt Value", "P&L $", "P&L %", "Day Chg", "Weight"],
            rows,
        )
        st.markdown(source_footer([
            {"name": "Live quote feed (yfinance / IEX fallback)", "kind": "live", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception as e:
        logger.warning(f"holdings table error: {e}")
        st.error(f"Holdings table error: {e}")


def _render_composition_chart(df: pd.DataFrame) -> None:
    """Sector allocation donut chart."""
    try:
        if df.empty:
            return
        sector_grp = df.groupby("Sector")["Market Value"].sum().reset_index()
        colors = [_SECTOR_COLORS.get(s, C_TEXT2) for s in sector_grp["Sector"]]

        fig = go.Figure(go.Pie(
            labels=sector_grp["Sector"],
            values=sector_grp["Market Value"],
            hole=0.6,
            marker=dict(colors=colors, line=dict(color="#0c0e14", width=2)),
            textinfo="label+percent",
            textfont=dict(color="#e8e6e1", size=12),
            hovertemplate="<b>%{label}</b><br>Value: $%{value:,.0f}<br>Share: %{percent}<extra></extra>",
        ))

        total_val = df["Market Value"].sum()
        fig.add_annotation(
            text=f"<b>{_fmt_dollar_abs(total_val)}</b><br><span style='font-size:10px'>AUM</span>",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=15, color="#e8e6e1"),
        )

        apply_dark_layout(fig, title="Sector Allocation", height=360)
        st.plotly_chart(fig, use_container_width=True, key="portfolio_donut")
        st.markdown(source_footer([
            {"name": "Live quote feed (yfinance / IEX fallback)", "kind": "live", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception as e:
        logger.warning(f"composition chart error: {e}")


def _render_performance_chart(df: pd.DataFrame) -> None:
    """90-day simulated portfolio NAV vs shipping index."""
    try:
        np.random.seed(42)
        days = 90
        dates = pd.date_range(end=datetime.date.today(), periods=days, freq="B")

        # Simulate correlated returns
        port_ret   = np.random.normal(0.0008, 0.018, days)
        index_ret  = np.random.normal(0.0003, 0.020, days)

        # Add some correlation + trending
        port_ret   = port_ret + 0.0005
        index_ret  = index_ret - 0.0002

        nav_port   = 100 * np.cumprod(1 + port_ret)
        nav_index  = 100 * np.cumprod(1 + index_ret)

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=dates, y=nav_port,
            name="Portfolio",
            line=dict(color=C_ACCENT, width=2.5),
            fill="tozeroy",
            fillcolor="rgba(53,114,176,0.06)",
            hovertemplate="<b>Portfolio</b><br>%{x|%b %d}<br>NAV: %{y:.1f}<extra></extra>",
        ))

        fig.add_trace(go.Scatter(
            x=dates, y=nav_index,
            name="Shipping Index (BDI proxy)",
            line=dict(color=C_MOD, width=1.8, dash="dot"),
            hovertemplate="<b>Index</b><br>%{x|%b %d}<br>NAV: %{y:.1f}<extra></extra>",
        ))

        apply_dark_layout(fig, title="Portfolio NAV vs. Shipping Index (90-Day)", height=360)
        fig.update_layout(yaxis={"title": dict(text="Indexed (Base=100)", font=dict(size=11, color=C_TEXT3))})

        st.plotly_chart(fig, use_container_width=True, key="portfolio_nav")
        st.markdown(source_footer([
            {"name": "Simulated 90-day NAV path", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception as e:
        logger.warning(f"performance chart error: {e}")


def _render_risk_metrics(df: pd.DataFrame) -> None:
    """VaR, Sharpe, Max Drawdown, BDI correlation panel."""
    try:
        np.random.seed(7)
        n = 252
        port_ret = np.random.normal(0.0008, 0.018, n)

        # VaR 95% 1-day
        var_95 = float(np.percentile(port_ret, 5))
        total_val = df["Market Value"].sum() if not df.empty else 500_000
        var_dollar = abs(var_95) * total_val

        # Sharpe (annualised, rf=4.5%)
        rf_daily = 0.045 / 252
        sharpe = (port_ret.mean() - rf_daily) / port_ret.std() * np.sqrt(252)

        # Max drawdown
        nav = np.cumprod(1 + port_ret)
        peak = np.maximum.accumulate(nav)
        drawdown = (nav - peak) / peak
        max_dd = float(drawdown.min()) * 100

        # BDI correlation (simulated)
        bdi_ret = 0.6 * port_ret + np.random.normal(0, 0.012, n)
        bdi_corr = float(np.corrcoef(port_ret, bdi_ret)[0, 1])

        sharpe_color = C_HIGH if sharpe > 1.0 else (C_MOD if sharpe > 0 else C_LOW)
        dd_color     = C_LOW if max_dd < -15 else (C_MOD if max_dd < -8 else C_HIGH)

        section_header("Risk Metrics", "VaR, Sharpe, drawdown, and BDI correlation — trailing 252 days")
        metric_card_row([
            {"label": "VaR (95%, 1-Day)", "value": _fmt_dollar_abs(var_dollar),
             "accent": C_LOW,         "sublabel": f"{abs(var_95)*100:.2f}% of portfolio"},
            {"label": "Sharpe Ratio",    "value": f"{sharpe:.2f}",
             "accent": sharpe_color,   "sublabel": "Annualised, rf=4.5%"},
            {"label": "Max Drawdown",    "value": f"{max_dd:.1f}%",
             "accent": dd_color,       "sublabel": "Trailing 252 days"},
            {"label": "Corr. to BDI",    "value": f"{bdi_corr:.2f}",
             "accent": C_ACCENT,       "sublabel": "Baltic Dry Index"},
        ], columns=4)
        st.markdown(source_footer([
            {"name": "Simulated daily P&L (252-day Monte Carlo)", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception as e:
        logger.warning(f"risk metrics error: {e}")


def _render_top_movers(df: pd.DataFrame) -> None:
    """Best and worst performers today."""
    try:
        if df.empty or len(df) < 2:
            return
        sorted_df = df.sort_values("Day Chg %", ascending=False)
        best = sorted_df.iloc[0]
        worst = sorted_df.iloc[-1]

        section_header("Top Movers", "Best and worst single-day performers across the portfolio")

        col_best, col_worst = st.columns(2)
        with col_best:
            st.markdown(insight_card_html(
                title=f"{best['Ticker']} — {best['Sector']}",
                score=max(0.0, min(1.0, (best['Day Chg %'] + 5) / 10)),
                action="Watch",
                rationale=(
                    f"Best Performer Today · {_fmt_pct(best['Day Chg %'])} "
                    f"({_fmt_dollar(best['Day P&L $'])}). "
                    f"Price ${best['Price']:.2f} · Mkt Value {_fmt_dollar_abs(best['Market Value'])} · "
                    f"Weight {best['Weight %']:.1f}%."
                ),
                category="GAINER",
            ), unsafe_allow_html=True)
        with col_worst:
            st.markdown(insight_card_html(
                title=f"{worst['Ticker']} — {worst['Sector']}",
                score=max(0.0, min(1.0, (worst['Day Chg %'] + 5) / 10)),
                action="Caution",
                rationale=(
                    f"Worst Performer Today · {_fmt_pct(worst['Day Chg %'])} "
                    f"({_fmt_dollar(worst['Day P&L $'])}). "
                    f"Price ${worst['Price']:.2f} · Mkt Value {_fmt_dollar_abs(worst['Market Value'])} · "
                    f"Weight {worst['Weight %']:.1f}%."
                ),
                category="LOSER",
            ), unsafe_allow_html=True)

        # Bar chart for all positions
        bar_colors = [_color(v) for v in df["Day Chg %"]]
        fig = go.Figure(go.Bar(
            x=df["Ticker"],
            y=df["Day Chg %"],
            marker_color=bar_colors,
            text=[f"{v:+.2f}%" for v in df["Day Chg %"]],
            textposition="outside",
            textfont=dict(size=11, color="#e8e6e1"),
            hovertemplate="<b>%{x}</b><br>Day Change: %{y:+.2f}%<extra></extra>",
        ))
        apply_dark_layout(fig, title="Today's Returns by Position", height=280, showlegend=False)
        fig.update_layout(yaxis={"ticksuffix": "%"})
        st.plotly_chart(fig, use_container_width=True, key="top_movers_bar")
        st.markdown(source_footer([
            {"name": "Live quote feed (yfinance / IEX fallback)", "kind": "live", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception as e:
        logger.warning(f"top movers error: {e}")


def _render_position_details(df: pd.DataFrame) -> None:
    """Expander per position with mini chart + key stats."""
    try:
        if df.empty:
            return
        section_header("Position Detail", "Per-position price chart, key stats, and sector context")

        np.random.seed(0)
        dates = pd.date_range(end=datetime.date.today(), periods=60, freq="B")

        for _, row in df.iterrows():
            ticker = row["Ticker"]
            pnl_color = _color(row["P&L %"])
            sector_col = _SECTOR_COLORS.get(row["Sector"], C_TEXT2)

            with st.expander(
                f"{ticker}  —  {_fmt_pct(row['P&L %'])}  |  {_fmt_dollar_abs(row['Market Value'])}",
                expanded=False
            ):
                # Mini price chart (simulated)
                seed_offset = hash(ticker) % 999
                np.random.seed(seed_offset)
                daily_ret  = np.random.normal(0.0005, 0.022, 60)
                price_path = row["Price"] / np.cumprod(1 + daily_ret)[-1] * np.cumprod(1 + daily_ret)

                mini_fig = go.Figure()
                line_color = C_HIGH if row["P&L %"] >= 0 else C_LOW
                mini_fig.add_trace(go.Scatter(
                    x=dates, y=price_path,
                    mode="lines",
                    line=dict(color=line_color, width=2),
                    fill="tozeroy",
                    fillcolor=f"{line_color}18",
                    hovertemplate=f"<b>{ticker}</b><br>%{{x|%b %d}}<br>${{y:.2f}}<extra></extra>",
                    showlegend=False,
                ))

                # Avg cost line
                mini_fig.add_hline(
                    y=row["Avg Cost"],
                    line=dict(color=C_MOD, width=1.2, dash="dash"),
                    annotation_text=f"Avg ${row['Avg Cost']:.2f}",
                    annotation_font=dict(color=C_MOD, size=10),
                )

                apply_dark_layout(mini_fig, title=f"{ticker} — 60-Day Price", height=220, showlegend=False)
                mini_fig.update_layout(margin={"l": 10, "r": 10, "t": 36, "b": 20})
                st.plotly_chart(mini_fig, use_container_width=True, key=f"detail_{ticker}")

                cost_basis = row["Shares"] * row["Avg Cost"]
                day_pnl_row = row["Day P&L $"]

                metric_card_row([
                    {"label": "Shares",     "value": f"{int(row['Shares']):,}",
                     "accent": C_TEXT,    "sublabel": ""},
                    {"label": "Cost Basis", "value": _fmt_dollar_abs(cost_basis),
                     "accent": C_TEXT,    "sublabel": f"avg ${row['Avg Cost']:.2f}"},
                    {"label": "Total P&L",  "value": _fmt_dollar(row['P&L $']),
                     "accent": pnl_color, "sublabel": _fmt_pct(row['P&L %'])},
                    {"label": "Day P&L",    "value": _fmt_dollar(day_pnl_row),
                     "accent": _color(day_pnl_row), "sublabel": _fmt_pct(row['Day Chg %'])},
                    {"label": "Beta",       "value": f"{row['Beta']:.2f}",
                     "accent": C_ACCENT,  "sublabel": "vs SPY"},
                ], columns=5)
                metric_card_row([
                    {"label": "Sector",            "value": row['Sector'],
                     "accent": sector_col, "sublabel": "shipping sub-sector"},
                    {"label": "Portfolio Weight",  "value": f"{row['Weight %']:.1f}%",
                     "accent": C_TEXT,    "sublabel": "of total AUM"},
                    {"label": "Return %",          "value": _fmt_pct(row['P&L %']),
                     "accent": pnl_color, "sublabel": "since entry"},
                ], columns=3)
    except Exception as e:
        logger.warning(f"position detail error: {e}")


_FACTOR_CARRIERS: tuple[str, ...] = ("ZIM", "MATX", "SBLK", "DAC", "CMRE", "GSL")
_FACTOR_LEVEL_KEYS: tuple[str, ...] = ("BDI", "SCFI", "Brent", "WTI", "DXY", "VIX")


def _weekly_log_returns(stock_data) -> pd.DataFrame:
    """Build a weekly log-returns DataFrame from the stock_data dict."""
    if not isinstance(stock_data, dict):
        return pd.DataFrame()
    frames: dict[str, pd.Series] = {}
    for ticker in _FACTOR_CARRIERS:
        frame = stock_data.get(ticker)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        col = None
        for cand in ("close", "Close", "adj_close", "price"):
            if cand in frame.columns:
                col = cand
                break
        if col is None:
            continue
        series = pd.to_numeric(frame[col], errors="coerce").dropna()
        if series.empty or not isinstance(series.index, pd.DatetimeIndex):
            continue
        weekly = series.resample("W-FRI").last().dropna()
        rets = np.log(weekly.where(weekly > 0)).diff().dropna()
        if len(rets) >= 60:
            frames[ticker] = rets
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).dropna(how="all")


def _extract_level(series_or_frame) -> pd.Series | None:
    """Coerce a macro item (Series or single-col DataFrame) into a level Series."""
    if series_or_frame is None:
        return None
    if isinstance(series_or_frame, pd.Series):
        s = pd.to_numeric(series_or_frame, errors="coerce").dropna()
        return s if not s.empty else None
    if isinstance(series_or_frame, pd.DataFrame):
        if series_or_frame.empty:
            return None
        for cand in ("value", "close", "Close", "level"):
            if cand in series_or_frame.columns:
                s = pd.to_numeric(series_or_frame[cand], errors="coerce").dropna()
                return s if not s.empty else None
        first = series_or_frame.iloc[:, 0]
        s = pd.to_numeric(first, errors="coerce").dropna()
        return s if not s.empty else None
    return None


def _factors_from_macro(macro_data) -> pd.DataFrame:
    """Build a factor frame from macro_data; empty DataFrame if unavailable."""
    if not isinstance(macro_data, dict) or not macro_data:
        return pd.DataFrame()
    levels: dict[str, pd.Series] = {}
    for key in _FACTOR_LEVEL_KEYS:
        for candidate in (key, key.lower(), key.upper()):
            if candidate in macro_data:
                s = _extract_level(macro_data[candidate])
                if s is not None:
                    levels[key] = s
                    break
    if len(levels) < 2:
        return pd.DataFrame()
    return build_factor_frame(levels)


def _synthetic_factor_frame(returns_df: pd.DataFrame) -> pd.DataFrame:
    """Deterministic demo factor panel aligned to the returns index."""
    if returns_df.empty:
        return pd.DataFrame()
    idx = returns_df.index
    n = len(idx)
    rng = np.random.default_rng(20260422)
    data = {
        "dBDI":             rng.normal(0, 0.04, n),
        "dSCFI":            rng.normal(0, 0.03, n),
        "dBrent":           rng.normal(0, 0.035, n),
        "WTI_Brent_spread": rng.normal(0, 0.5, n),
        "dDXY":             rng.normal(0, 0.008, n),
        "VIX":              15 + rng.normal(0, 3.5, n).cumsum() * 0.02,
    }
    return pd.DataFrame(data, index=idx)


def _t_color(tval: float) -> str:
    a = abs(tval)
    if a >= 2.0:
        return C_HIGH
    if a >= 1.0:
        return C_MOD
    return C_TEXT3


def _render_carrier_factor_lens(stock_data, macro_data) -> None:
    """Quant artifact: OLS betas + residual mean-reversion backtest."""
    try:
        returns_df = _weekly_log_returns(stock_data)
        if returns_df.empty or returns_df.shape[1] < 2:
            return

        factors_df = _factors_from_macro(macro_data)
        if factors_df.empty or len(factors_df) < 80:
            factors_df = _synthetic_factor_frame(returns_df)
            data_quality = "demo"
            source_name = "Synthetic Factors"
            notes = "Demo factors — wire BDI/SCFI/Brent/DXY/VIX in macro_data for real fit"
        else:
            data_quality = "modeled"
            source_name = "FRED + Baltic (modeled)"
            notes = "Factor panel: Δlog BDI/SCFI/Brent/DXY, WTI-Brent spread, VIX"

        combined_idx = returns_df.index.intersection(factors_df.index)
        if len(combined_idx) < 60:
            return
        returns_df = returns_df.loc[combined_idx]
        factors_df = factors_df.loc[combined_idx]

        fits = fit_carrier_factors(returns_df, factors_df, hac_lags=4)
        if not fits:
            return

        section_header("Carrier Factor Lens", "Ridge-fit factor exposures and residual signal back-test")
        st.markdown(
            live_data_badge(
                source=source_name,
                as_of=combined_idx.max(),
                quality=data_quality,
                kind="modeled",
                notes=notes,
            ),
            unsafe_allow_html=True,
        )

        factor_cols = list(factors_df.columns)
        headers = ["Carrier", "α (bps)", "R²", "n"] + factor_cols
        rows: list[list[str]] = []
        for ticker, fit in fits.items():
            alpha_bps = fit.alpha * 1e4
            alpha_cell = (
                f'<span style="color:{_t_color(fit.alpha_tstat)};'
                f'font-family:var(--mono);font-weight:600;">'
                f'{alpha_bps:+.1f}'
                f'<span style="color:{C_TEXT3};font-size:10px;margin-left:4px;">'
                f't={fit.alpha_tstat:+.2f}</span></span>'
            )
            r2_cell = (
                f'<span style="font-family:var(--mono);color:{C_TEXT};font-weight:600;">'
                f'{fit.r_squared:.2f}</span>'
            )
            n_cell = (
                f'<span style="font-family:var(--mono);color:{C_TEXT2};">{fit.n_obs}</span>'
            )
            beta_cells: list[str] = []
            for f in factor_cols:
                b = fit.betas.get(f, 0.0)
                t = fit.tvalues.get(f, 0.0)
                col = _t_color(t)
                beta_cells.append(
                    f'<span style="font-family:var(--mono);color:{col};font-weight:600;">'
                    f'{b:+.2f}'
                    f'<span style="color:{C_TEXT3};font-size:10px;margin-left:4px;">'
                    f'{t:+.1f}</span></span>'
                )
            ticker_cell = (
                f'<span style="font-family:var(--sans);color:{C_TEXT};font-weight:700;">'
                f'{ticker}</span>'
            )
            rows.append([ticker_cell, alpha_cell, r2_cell, n_cell] + beta_cells)
        wsj_market_table(headers, rows)

        # Residual trajectory + backtest for the highest-R² carrier
        focus_ticker = max(fits.keys(), key=lambda k: fits[k].r_squared)
        focus_fit = fits[focus_ticker]
        z = residual_zscore(focus_fit, returns_df[focus_ticker], factors_df, window=52)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=z.index, y=z.values,
            mode="lines",
            line=dict(color=C_ACCENT, width=1.8),
            name="Residual z-score",
            hovertemplate="<b>%{x|%b %d, %Y}</b><br>z = %{y:.2f}<extra></extra>",
        ))
        for level, color, dash in ((1.0, C_MOD, "dot"), (-1.0, C_MOD, "dot"),
                                    (2.0, C_LOW, "dash"), (-2.0, C_LOW, "dash")):
            fig.add_hline(y=level, line=dict(color=color, width=1, dash=dash))
        apply_dark_layout(
            fig,
            title=f"{focus_ticker} — Residual Z-Score (52W rolling)",
            height=300,
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="carrier_factor_resid")

        try:
            bt = residual_signal_backtest(
                returns_df[focus_ticker], factors_df,
                name=focus_ticker, lookback=52,
            )
        except ValueError:
            return

        cum_color = C_HIGH if bt.cumulative_return >= 0 else C_LOW
        sharpe_color = (
            C_HIGH if bt.sharpe >= 0.5 else (C_MOD if bt.sharpe >= 0 else C_LOW)
        )
        ir_color = (
            C_HIGH if bt.information_ratio >= 0 else C_LOW
        )
        hit_color = (
            C_HIGH if bt.hit_rate >= 0.55 else (C_MOD if bt.hit_rate >= 0.48 else C_LOW)
        )
        metric_card_row([
            {
                "label":    "Signal Sharpe",
                "value":    f"{bt.sharpe:+.2f}",
                "accent":   sharpe_color,
                "sublabel": f"{focus_ticker} · walk-forward",
            },
            {
                "label":    "Info Ratio vs. B&H",
                "value":    f"{bt.information_ratio:+.2f}",
                "accent":   ir_color,
                "sublabel": "Sharpe − buy-and-hold",
            },
            {
                "label":    "Hit Rate",
                "value":    f"{bt.hit_rate * 100:.1f}%",
                "accent":   hit_color,
                "sublabel": f"{bt.n_trades} active weeks",
            },
            {
                "label":    "Cumulative",
                "value":    f"{bt.cumulative_return * 100:+.1f}%",
                "accent":   cum_color,
                "sublabel": f"{bt.mean_return_bps:+.1f} bps / wk",
            },
        ])
    except Exception as e:
        logger.warning(f"carrier factor lens error: {e}")


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(stock_data, macro_data, insights) -> None:
    """Render the Portfolio Tracker tab."""
    try:
        _init_positions()

        _render_hero()

        positions = st.session_state.get("portfolio_positions", [])
        df = _build_snapshot(positions, stock_data)

        _render_summary_metrics(df)

        _render_add_position_form()

        st.markdown(_HR, unsafe_allow_html=True)

        _render_holdings_table(df)

        # Charts row: donut + performance
        if not df.empty:
            col_left, col_right = st.columns([1, 1.6])
            with col_left:
                section_header("Sector Allocation", "Donut: market-value share by shipping sub-sector")
                _render_composition_chart(df)
            with col_right:
                section_header("Performance", "Portfolio NAV vs shipping benchmark — 90-day base=100")
                _render_performance_chart(df)

        st.markdown(_HR, unsafe_allow_html=True)

        _render_risk_metrics(df)

        st.markdown(_HR, unsafe_allow_html=True)

        _render_carrier_factor_lens(stock_data, macro_data)

        st.markdown(_HR, unsafe_allow_html=True)

        _render_top_movers(df)

        st.markdown(_HR, unsafe_allow_html=True)

        _render_position_details(df)

    except Exception as e:
        logger.exception(f"Portfolio tab crash: {e}")
        st.error(f"Portfolio tracker encountered an error: {e}")
