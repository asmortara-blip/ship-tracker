"""Portfolio Tracker tab — shipping sector position management, P&L, risk metrics."""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from utils.helpers import stable_hash
from ui.styles import (
    _hex_to_rgba,
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_TEXT, C_TEXT2, C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    live_data_badge,
    metric_card_row,
    page_header,
    section_divider,
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

# Prices and day-changes come from REAL stock_feed closes (processing.book_pnl);
# unpriced tickers render NaN / 0.0 rather than a fabricated quote.


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
    """Latest REAL close from stock_data, or NaN when unavailable (no mock)."""
    try:
        from processing.book_pnl import _latest_close
        last = _latest_close(stock_data, ticker)
        if last is not None:
            return float(last)
        # Wide-frame shape (some callers pass a columns-are-tickers DataFrame).
        if isinstance(stock_data, pd.DataFrame) and ticker in stock_data.columns:
            val = stock_data[ticker].dropna().iloc[-1]
            return float(val)
    except Exception:
        pass
    return float("nan")


def _get_day_change_pct(ticker: str, stock_data) -> float:
    """Real 1-day % change from the last two closes; 0.0 when unavailable."""
    try:
        from processing.book_pnl import day_change_pct
        d = day_change_pct(ticker, stock_data)
        return float(d) if d is not None else 0.0
    except Exception:
        return 0.0


def _init_positions() -> None:
    """Seed the session book from the durable per-user ledger (schema v29).

    A logged-in user's saved positions load from the DB; a brand-new user (or
    the session-less CLI/demo path) falls back to the illustrative default
    book, which is NOT persisted until they actually edit it.
    """
    if "portfolio_positions" in st.session_state:
        return
    loaded: list[dict] = []
    try:
        from state.user_scope import current_user_id
        from state import positions as pos_store
        uid = current_user_id()
        if uid:
            loaded = pos_store.load_positions(uid)
    except Exception:
        loaded = []
    st.session_state["portfolio_positions"] = (
        loaded if loaded else [dict(p) for p in _DEFAULT_POSITIONS]
    )


def _persist_positions() -> None:
    """Persist the current session book to the durable per-user ledger.

    No-op without a logged-in user (the session-less CLI/demo path keeps the
    legacy in-memory behaviour). Failures are swallowed so a storage blip
    never breaks the tab.
    """
    try:
        from state.user_scope import current_user_id
        from state import positions as pos_store
        uid = current_user_id()
        if not uid:
            return
        pos_store.replace_positions(uid, st.session_state.get("portfolio_positions", []))
    except Exception:
        from loguru import logger
        logger.exception("tab_portfolio: persist_positions failed")


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
        beta    = float(pos.get("beta") or 1.0)
        price   = _get_price(ticker, stock_data)
        mkt_val = shares * price
        cost_basis = shares * avg_cost
        pnl_dollar = mkt_val - cost_basis
        pnl_pct    = (pnl_dollar / cost_basis * 100) if cost_basis > 0 else 0.0
        day_chg    = _get_day_change_pct(ticker, stock_data)
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
                    _persist_positions()
                    st.rerun()
            except Exception as e:
                st.error(f"Error adding position: {e}")

        # Remove position
        positions = st.session_state.get("portfolio_positions", [])
        if positions:
            tickers_list = [p["ticker"] for p in positions]
            rem_ticker = st.selectbox("Remove position", ["— select —"] + tickers_list, key="rem_ticker")
            if st.button("Remove", key="rem_btn") and rem_ticker != "— select —":
                st.session_state["portfolio_positions"] = [
                    p for p in positions if p["ticker"] != rem_ticker
                ]
                _persist_positions()
                st.success(f"Removed {rem_ticker}")
                st.rerun()


def _render_editorial_commentary(df: pd.DataFrame) -> None:
    """1-2 paragraph editorial read on the current portfolio snapshot.

    Sits between the summary KPI strip and the holdings table. Wraps the
    engine call in try/except — template fallback is safe; the only failure
    mode we guard against here is import / DB errors.
    """
    try:
        from engine.tab_commentary import build_commentary

        if df is None or df.empty:
            return  # No holdings → no editorial commentary to render.

        total_val = float(df["Market Value"].sum())
        total_pnl = float(df["P&L $"].sum())
        day_pnl = float(df["Day P&L $"].sum())
        cost_basis = float((df["Shares"] * df["Avg Cost"]).sum())
        total_ret_pct = (
            (total_pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
        )
        port_beta = float(
            (df["Beta"] * df["Weight %"] / 100.0).sum()
        ) if "Weight %" in df.columns else 1.0

        # Top holding by market value + best/worst single-day mover. Guard the
        # all-unpriced case (every Market Value NaN) — idxmax would raise.
        _mv = df["Market Value"]
        top = df.loc[_mv.idxmax()] if _mv.notna().any() else df.iloc[0]
        sorted_day = df.sort_values("Day Chg %", ascending=False)
        best = sorted_day.iloc[0]
        worst = sorted_day.iloc[-1]

        # Sector exposure as a simple dict, rounded to whole pct points.
        sector_exposure: dict[str, float] = {}
        try:
            sector_grp = (
                df.groupby("Sector")["Market Value"].sum() / total_val * 100.0
            )
            sector_exposure = {
                str(k): round(float(v), 1)
                for k, v in sector_grp.sort_values(ascending=False).items()
            }
        except Exception:
            pass

        context: dict[str, object] = {
            "n_positions": int(len(df)),
            "total_value_usd": round(total_val, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "total_return_pct": round(total_ret_pct, 2),
            "day_pnl_usd": round(day_pnl, 2),
            "portfolio_beta": round(port_beta, 2),
            "top_holding": (
                f"{top['Ticker']} ({top['Sector']}, "
                f"{float(top['Weight %']):.1f}% of book)"
            ),
            "best_today": f"{best['Ticker']} ({float(best['Day Chg %']):+.2f}%)",
            "worst_today": f"{worst['Ticker']} ({float(worst['Day Chg %']):+.2f}%)",
            "sector_exposure_pct": sector_exposure,
        }

        commentary = build_commentary("Portfolio", context)

        section_header(
            "Editorial",
            subtitle=(
                "LLM-narrated read on the current portfolio snapshot. "
                "Falls back to a deterministic template when no API key is "
                "configured."
            ),
        )

        source_label, source_color = (
            ("LLM", C_HIGH) if commentary.source == "llm"
            else ("Template", C_MOD)
        )
        meta_bits = [f"<span style='color:{source_color}'>{source_label}</span>"]
        if commentary.source == "llm" and commentary.model:
            meta_bits.append(
                f"<code style='font-size:0.66rem;color:{C_TEXT3}'>{commentary.model}</code>"
            )
        if commentary.tokens_in or commentary.tokens_out:
            meta_bits.append(
                f"<span style='font-size:0.66rem;color:{C_TEXT3}'>"
                f"{commentary.tokens_in}→{commentary.tokens_out} tok</span>"
            )

        body_html = "".join(
            f'<p style="margin:0 0 10px 0;font-size:0.86rem;line-height:1.55;'
            f'color:{C_TEXT2}">{para.strip()}</p>'
            for para in commentary.body.split("\n\n") if para.strip()
        )
        st.markdown(
            f'<div style="background:rgba(53,114,176,0.06);'
            f'border-left:3px solid {C_ACCENT};padding:14px 18px;border-radius:3px;'
            f'margin-bottom:14px">'
            f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
            f'color:{C_TEXT3};font-weight:600;margin-bottom:6px">'
            f'Source: {" · ".join(meta_bits)}'
            f'</div>'
            f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1.05rem;'
            f'line-height:1.4;color:{C_TEXT};font-weight:600;margin-bottom:10px">'
            f'{commentary.headline}</div>'
            f'{body_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Portfolio — editorial commentary render failed")


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


def _render_performance_chart(positions: list[dict], stock_data) -> None:
    """Portfolio NAV from REAL closes — current holdings marked to history.

    Values today's book at each past real close (base=100). This is a
    mark-to-history curve, NOT a replayed trading path (entries/exits are not
    reconstructed). Empty-states honestly when no priced holdings exist.
    """
    try:
        from processing.book_pnl import nav_series
        nav = nav_series(positions, stock_data, days=90, base=100.0)
        if nav is None or nav.empty:
            st.info(
                "Portfolio NAV needs live price history for the held tickers — "
                "unavailable right now."
            )
            return

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=nav.index, y=nav.values,
            name="Portfolio (mark-to-history)",
            line=dict(color=C_ACCENT, width=2.5),
            fill="tozeroy",
            fillcolor="rgba(53,114,176,0.06)",
            hovertemplate="<b>Portfolio</b><br>%{x|%b %d}<br>NAV: %{y:.1f}<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            title="Portfolio NAV — current holdings marked to historical closes",
            height=360,
        )
        fig.update_layout(yaxis={"title": dict(text="Indexed (Base=100)", font=dict(size=11, color=C_TEXT3))})

        st.plotly_chart(fig, use_container_width=True, key="portfolio_nav")
        st.markdown(source_footer([
            {"name": "Holdings marked to real historical closes (yfinance)", "kind": "real", "quality": "live"},
        ]), unsafe_allow_html=True)
        st.caption(
            "Mark-to-history: today's holdings valued at each past close — "
            "not a realized trading path."
        )
    except Exception as e:
        logger.warning(f"performance chart error: {e}")


def _build_risk_return_scatter(df: pd.DataFrame) -> go.Figure:
    """Per-position risk × return scatter — Beta on x, total P&L % on y.

    Marker size scales with portfolio weight; colour follows P&L direction
    (gain/loss/flat). Reference lines at Beta=1 (market) and P&L=0 split
    the plane into the canonical four quadrants, so the reader sees at a
    glance which holdings carry the most cycle exposure and how the bets
    have actually played.

    Pure builder — no ``st.*`` calls — so the lock-in tests exercise it
    directly. Returns an annotated-empty figure when the snapshot is empty.
    """
    fig = go.Figure()
    if df is None or df.empty:
        fig.add_annotation(
            text="No positions to plot",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Risk × Return", height=300)
        return fig

    betas    = df["Beta"].to_list()
    pnl_pcts = df["P&L %"].to_list()
    weights  = df["Weight %"].to_list()
    tickers  = df["Ticker"].to_list()
    market_vals = df["Market Value"].to_list()

    # Marker size: scale weight (0-100%) into a 12-44px range so the smallest
    # position is still visible and the largest doesn't dominate.
    max_w = max(weights) if weights else 1.0
    sizes = [
        12 + (32 * (w / max_w if max_w > 0 else 0.0))
        for w in weights
    ]
    colors = [_color(p) for p in pnl_pcts]

    fig.add_trace(go.Scatter(
        x=betas,
        y=pnl_pcts,
        mode="markers+text",
        marker={
            "size": sizes,
            "color": colors,
            "line": {"color": "#0c0e14", "width": 1.5},
            "opacity": 0.88,
        },
        text=tickers,
        textposition="top center",
        textfont={"color": C_TEXT3, "size": 10},
        customdata=list(zip(weights, market_vals)),
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Beta: %{x:.2f}<br>"
            "P&L: %{y:+.1f}%<br>"
            "Weight: %{customdata[0]:.1f}%<br>"
            "Market value: $%{customdata[1]:,.0f}<extra></extra>"
        ),
        showlegend=False,
    ))

    # Reference lines: market beta = 1, breakeven P&L = 0
    fig.add_vline(
        x=1.0,
        line={"color": "rgba(255,255,255,0.10)", "width": 1, "dash": "dot"},
        annotation_text="Market β",
        annotation_position="top",
        annotation_font={"color": C_TEXT3, "size": 9},
    )
    fig.add_hline(
        y=0.0,
        line={"color": "rgba(255,255,255,0.10)", "width": 1, "dash": "dot"},
    )

    apply_dark_layout(
        fig,
        title="Risk × Return — per-position decomposition",
        height=340,
    )
    fig.update_layout(
        xaxis={"title": "Beta (systematic risk vs. SPY)",
               "gridcolor": "rgba(255,255,255,0.04)"},
        yaxis={"title": "Total P&L (%)",
               "gridcolor": "rgba(255,255,255,0.04)",
               "zeroline": False},
        margin={"l": 70, "r": 30, "t": 48, "b": 50},
    )
    return fig


def _render_risk_return_scatter(df: pd.DataFrame) -> None:
    """Render the risk-return scatter with a one-line caption underneath."""
    if df is None or df.empty:
        return
    st.plotly_chart(
        _build_risk_return_scatter(df),
        use_container_width=True,
        key="portfolio_risk_return_scatter",
    )
    st.caption(
        "Marker size = portfolio weight; colour = P&L direction. Reference "
        "lines split the plane at Beta=1 (market) and P&L=0. Top-right is "
        "cycle-on winners; top-left is defensive winners; bottom-right is "
        "cycle-down losers; bottom-left is defensive losers (where something "
        "is probably wrong with the thesis)."
    )


def _render_risk_metrics(df: pd.DataFrame, stock_data=None, macro_data=None) -> None:
    """VaR / Sharpe / Max Drawdown / BDI correlation from the REAL weighted book.

    R008 — replaces the former ``rng.normal`` 252-day Monte-Carlo panel (which
    fabricated the whole risk read, including a ~0.6 BDI correlation). Now built
    from ``book_pnl.returns_panel`` over the book's tickers, weighted by real
    marked weights, with a LABELED synthetic fallback only when prices are dark.
    The BDI correlation is REAL (from macro_data) or honestly ``n/a`` — never
    fabricated.
    """
    try:
        if df is None or df.empty or "Ticker" not in df.columns:
            return
        from processing import risk_lab
        from processing.book_exposure import book_weights
        from processing.book_pnl import returns_panel

        positions = st.session_state.get("portfolio_positions", [])
        tickers = [str(t) for t in df["Ticker"].tolist()]
        book_mv = float(df["Market Value"].sum()) or 500_000.0

        real = returns_panel(stock_data or {}, tickers)
        panel_is_real = not real.empty
        if panel_is_real:
            returns_df = real
            w = book_weights(positions, stock_data or {})
            # Restrict to surviving columns (returns_panel drops thin names) and
            # renormalise so the weights sum to 1 over the panel.
            w = {t: w[t] for t in returns_df.columns if t in w}
            s = sum(w.values())
            w = ({t: v / s for t, v in w.items()} if s > 0
                 else {t: 1.0 / len(returns_df.columns) for t in returns_df.columns})
        else:
            returns_df = _synth_returns_panel(tickers or ["ZIM"])
            w = {t: 1.0 / len(returns_df.columns) for t in returns_df.columns}
        if returns_df.empty:
            return

        hist = risk_lab.portfolio_var(
            returns_df, w, confidence=0.95,
            portfolio_value=book_mv, method="historical")

        # Weighted book-return series for Sharpe / MaxDD / BDI correlation.
        cols = [t for t in returns_df.columns if t in w]
        w_vec = np.array([w[t] for t in cols])
        port_ret = pd.Series(
            returns_df[cols].to_numpy() @ w_vec, index=returns_df.index)
        rf_daily = 0.045 / 252
        std = float(port_ret.std())
        sharpe = (((float(port_ret.mean()) - rf_daily) / std) * np.sqrt(252)
                  if std > 0 else 0.0)
        nav = (1.0 + port_ret).cumprod()
        max_dd = (float(((nav - nav.cummax()) / nav.cummax()).min()) * 100
                  if len(nav) else 0.0)

        # REAL BDI correlation, or an honest "n/a" — never the old 0.6 fabrication.
        # The BDI level is date-indexed and reindexed onto the (daily) return
        # dates before correlating changes, so a coarser BDI cadence still aligns
        # instead of silently producing an empty join (-> "n/a forever").
        bdi_corr_txt, bdi_sub = "n/a", "BDI unavailable"
        try:
            bdi_level = _bdi_level_series(macro_data)
            if (bdi_level is not None and len(bdi_level) > 5
                    and isinstance(port_ret.index, pd.DatetimeIndex)):
                bdi_ret = bdi_level.reindex(port_ret.index, method="ffill").pct_change()
                aligned = pd.concat([port_ret, bdi_ret], axis=1).dropna()
                if len(aligned) >= 10:
                    c = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
                    if np.isfinite(c):
                        bdi_corr_txt, bdi_sub = f"{c:.2f}", "Baltic Dry Index (real)"
        except Exception:
            pass

        var_pct = abs(hist.var_pct) * 100
        sharpe_color = C_HIGH if sharpe > 1.0 else (C_MOD if sharpe > 0 else C_LOW)
        dd_color = C_LOW if max_dd < -15 else (C_MOD if max_dd < -8 else C_HIGH)
        window = len(returns_df)
        sub = (f"real weighted book returns, {window}d" if panel_is_real
               else f"synthetic panel — prices dark, {window}d")
        section_header("Risk Metrics", f"VaR, Sharpe, drawdown, BDI correlation — {sub}")
        metric_card_row([
            {"label": "VaR (95%, 1-Day)", "value": _fmt_dollar_abs(hist.var_dollar),
             "accent": C_LOW,        "sublabel": f"{var_pct:.2f}% of book"},
            {"label": "Sharpe Ratio",    "value": f"{sharpe:.2f}",
             "accent": sharpe_color,  "sublabel": "Annualised, rf=4.5%"},
            {"label": "Max Drawdown",    "value": f"{max_dd:.1f}%",
             "accent": dd_color,      "sublabel": f"over {window}-day window"},
            {"label": "Corr. to BDI",    "value": bdi_corr_txt,
             "accent": C_ACCENT,      "sublabel": bdi_sub},
        ], columns=4)
        st.markdown(source_footer([
            DataSource.live("Real weighted book returns from cached closes; BDI from FRED")
            if panel_is_real else
            DataSource.demo("Synthetic returns panel (prices dark — labeled demo)")
        ]), unsafe_allow_html=True)
    except Exception as e:
        logger.warning(f"risk metrics error: {e}")


def _render_book_cascade(positions, stock_data, macro_data, insights) -> None:
    """Overlay the book onto the disruption cascade (R008) — the research-to-PM
    bridge that the Portfolio tab never had.

    NOTE: ``render`` here has no live freight/port/route feeds, so the cascade
    runs macro-only and most route-stress terms are muted — this is a book TILT
    against the macro-driven cascade, labeled honestly as such.
    """
    try:
        if not positions:
            return
        from processing.book_exposure import (
            book_cascade_overlay, book_commodity_exposure, book_concentration)

        ideas = []
        try:
            from processing.disruption_cascade import score_equity_ideas
            from processing.exposure_matrix import build_exposure_matrix
            from processing.shipping_stress_index import compute_shipping_stress
            stress = compute_shipping_stress({}, macro_data or {}, [], [])
            exposure = build_exposure_matrix(stock_data or {})
            ideas = score_equity_ideas(stress, exposure, stock_data or {}, insights)
        except Exception as exc:
            logger.debug(f"book cascade ideas unavailable: {exc}")

        overlay = book_cascade_overlay(positions, ideas, stock_data or {})
        conc = book_concentration(positions, stock_data or {})

        section_header(
            "Book vs Cascade",
            "Your book's tilt against the macro-driven disruption cascade, "
            "plus concentration")
        tilt_color = (C_HIGH if overlay.net_tilt == "Bullish"
                      else (C_LOW if overlay.net_tilt == "Bearish" else C_TEXT3))
        metric_card_row([
            {"label": "Net Cascade Tilt", "value": overlay.net_tilt,
             "accent": tilt_color,
             "sublabel": f"{overlay.coverage*100:.0f}% of book weight covered"},
            {"label": "Weighted Conviction", "value": f"{overlay.weighted_conviction:.2f}",
             "accent": C_MOD,
             "sublabel": f"{overlay.n_covered} covered · {overlay.n_uncovered} uncovered"},
            {"label": "Concentration (HHI)", "value": f"{conc['hhi']:.2f}",
             "accent": C_ACCENT,
             "sublabel": f"~{conc['effective_n']:.1f} effective names · top {conc['top_name_pct']:.0f}%"},
        ], columns=3)

        if overlay.top_contributors:
            headers = ["Instrument", "Weight", "Cascade Idea", "Conviction",
                       "Weighted Cascade"]
            rows = []
            for n in overlay.top_contributors:
                dl = n.direction.lower()
                d_col = (C_HIGH if dl.startswith("bull")
                         else (C_LOW if dl.startswith("bear") else C_TEXT3))
                rows.append([
                    _mono(n.ticker, color=C_TEXT),
                    _mono(f"{n.weight*100:.0f}%"),
                    _sans(n.direction, color=d_col, weight=600),
                    _mono(f"{n.conviction_score:.2f}"),
                    _mono(f"{n.weighted_cascade:.3f}", color=C_MOD),
                ])
            wsj_market_table(headers, rows)

        ce = book_commodity_exposure(positions, stock_data or {})
        if ce:
            top3 = sorted(ce.items(), key=lambda x: -x[1])[:3]
            st.caption("Book commodity tilt — "
                       + " · ".join(f"{k}: {v*100:.0f}%" for k, v in top3))
        st.caption(
            "⚠ Macro-only cascade (this tab has no live freight/port/route feeds) "
            "— route-stress terms are muted, so read this as a book tilt, not a "
            "full cascade. The Risk Lab tab runs the cascade on the full inputs.")
    except Exception as exc:
        logger.warning(f"book cascade error: {exc}")


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

        # Note: the outer np.random.seed(0) here was dead — pd.date_range uses
        # no randomness and the loop seeds a per-ticker RNG below.
        dates = pd.date_range(end=datetime.date.today(), periods=60, freq="B")

        for _, row in df.iterrows():
            ticker = row["Ticker"]
            pnl_color = _color(row["P&L %"])
            sector_col = _SECTOR_COLORS.get(row["Sector"], C_TEXT2)

            with st.expander(
                f"{ticker}  —  {_fmt_pct(row['P&L %'])}  |  {_fmt_dollar_abs(row['Market Value'])}",
                expanded=False
            ):
                # Mini price chart (simulated) — instance-scoped RNG so we
                # don't perturb numpy's global state mid-render.
                rng = np.random.default_rng(stable_hash(ticker) % 999)
                daily_ret  = rng.normal(0.0005, 0.022, 60)
                price_path = row["Price"] / np.cumprod(1 + daily_ret)[-1] * np.cumprod(1 + daily_ret)

                mini_fig = go.Figure()
                line_color = C_HIGH if row["P&L %"] >= 0 else C_LOW
                mini_fig.add_trace(go.Scatter(
                    x=dates, y=price_path,
                    mode="lines",
                    line=dict(color=line_color, width=2),
                    fill="tozeroy",
                    fillcolor=_hex_to_rgba(line_color, 0.09),
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
    """Build a weekly log-returns DataFrame from the stock_data dict.

    Weekly LOG-RETURNS for the carrier factor model → look-ahead-free
    total-return basis ``close * adj_factor`` (R127) so a split/large dividend
    in the window doesn't inject a spurious ~-50% weekly return. adj_factor
    defaults to 1.0 when absent (fixtures / legacy frames), so those are
    unchanged.
    """
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
        series = pd.to_numeric(frame[col], errors="coerce")
        # Apply the forward adj_factor (aligned on the frame's index) before the
        # dropna so it stays row-aligned to its close. Missing → 1.0 (raw).
        if "adj_factor" in frame.columns:
            adj = pd.to_numeric(frame["adj_factor"], errors="coerce").fillna(1.0)
            series = series * adj
        series = series.dropna()
        if series.empty or not isinstance(series.index, pd.DatetimeIndex):
            continue
        weekly = series.resample("W-FRI").last().dropna()
        rets = np.log(weekly.where(weekly > 0)).diff().dropna()
        if len(rets) >= 60:
            frames[ticker] = rets
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).dropna(how="all")


def _bdi_level_series(macro_data) -> "pd.Series | None":
    """A DATE-INDEXED Baltic Dry Index level series from ``macro_data``.

    Tries the friendly name and the FRED series id, and promotes a ``date``
    column (the real-cache shape) to the index — without this the BDI series
    carries a RangeIndex and never aligns with the DatetimeIndex return panel,
    so the correlation would be "n/a" forever. Returns None when absent.
    """
    if not macro_data:
        return None
    for key in ("BDI", "BDIY", "BSXRLM", "bdi"):
        v = macro_data.get(key)
        if v is None:
            continue
        try:
            if isinstance(v, pd.Series):
                s = pd.to_numeric(v, errors="coerce").dropna()
                if isinstance(s.index, pd.DatetimeIndex) and not s.empty:
                    return s.sort_index()
                continue
            if isinstance(v, pd.DataFrame) and not v.empty:
                df = v.copy()
                if "date" in df.columns:
                    df = df.set_index(pd.to_datetime(df["date"], errors="coerce"))
                elif not isinstance(df.index, pd.DatetimeIndex):
                    df.index = pd.to_datetime(df.index, errors="coerce")
                col = next((c for c in ("value", "close", "Close", "level")
                            if c in df.columns), None)
                ser = df[col] if col is not None else df.iloc[:, 0]
                s = pd.to_numeric(ser, errors="coerce").dropna()
                s = s[s.index.notna()]
                if isinstance(s.index, pd.DatetimeIndex) and not s.empty:
                    return s.sort_index()
        except Exception:
            continue
    return None


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


def _synth_returns_panel(tickers: list[str], n: int = 504) -> pd.DataFrame:
    """Synthetic daily-returns panel keyed on the supplied tickers.

    The portfolio tab doesn't yet ingest a persisted per-ticker daily-return
    history (``stock_data`` is a per-ticker snapshot). This synth gives the
    optimizer a 504-day (~2y) panel with per-ticker mean/vol drawn from
    stable seeds + a uniform pairwise correlation of 0.30.

    Determinism: every per-ticker mean / vol seed flows through
    ``utils.helpers.stable_hash`` so the same ticker → same return profile
    across processes (Python's built-in hash is salted; see the existing
    audit in commit 789518f).
    """
    from utils.helpers import stable_hash

    if not tickers:
        return pd.DataFrame()
    k = len(tickers)
    means = np.array([
        # Per-ticker daily mean drawn deterministically from a small range.
        0.0002 + (stable_hash(t + "mu") % 10) / 10_000.0
        for t in tickers
    ])
    vols = np.array([
        # Per-ticker daily vol — shipping stocks are 1.5–3% daily σ.
        0.014 + (stable_hash(t + "vol") % 100) / 5_000.0
        for t in tickers
    ])
    corr = np.full((k, k), 0.30)
    np.fill_diagonal(corr, 1.0)
    cov = np.outer(vols, vols) * corr
    rng = np.random.default_rng(stable_hash("_panel" + "".join(tickers)) % (2**31))
    samples = rng.multivariate_normal(mean=means, cov=cov, size=n)
    dates = pd.date_range(end=datetime.date.today(), periods=n, freq="B")
    return pd.DataFrame(samples, index=dates, columns=tickers)


def _render_optimization_lab(df: pd.DataFrame, stock_data=None) -> None:
    """Run portfolio_optimizer's four methods on the current holdings and
    surface the comparison.

    Sections:
      1. Per-method comparison table: expected return / vol / Sharpe and the
         top-2 weighted positions.
      2. Side-by-side weight table: each ticker × each method.
      3. Walk-forward backtest equity curve for max_sharpe vs min_variance.

    The pure-function engine lives in ``engine.portfolio_optimizer``; this
    function only handles UI rendering + synthetic data plumbing.
    """
    try:
        from engine.portfolio_optimizer import (
            VALID_METHODS,
            optimize_portfolio,
            walk_forward_backtest,
        )

        if df is None or df.empty:
            st.info("Add positions above to run the optimization lab.")
            return

        tickers = [str(t) for t in df["Ticker"].tolist() if t]
        if len(tickers) < 2:
            st.info("Need at least 2 tickers in the portfolio to optimize.")
            return

        # Prefer REAL returns from cached closes (so the optimizer runs on the
        # book's actual covariance + tails); fall back to the synthetic panel —
        # labeled demo in the footer — only when prices are dark.
        from processing.book_pnl import returns_panel
        real_panel = returns_panel(stock_data, tickers)
        opt_panel_is_real = not real_panel.empty
        if opt_panel_is_real:
            returns_df = real_panel
            tickers = [t for t in tickers if t in returns_df.columns]
        else:
            returns_df = _synth_returns_panel(tickers)
        if returns_df.empty or returns_df.shape[1] < 2:
            st.info("Could not assemble a returns panel.")
            return

        section_header(
            "Portfolio Optimization Lab",
            "Compare four canonical methods against the current weighting — "
            "max-Sharpe, min-variance, mean-variance, and risk-parity. "
            "Walk-forward backtest sits below.",
        )

        # ── 1. Run all four methods ────────────────────────────────────────
        results = {}
        for method in VALID_METHODS:
            try:
                results[method] = optimize_portfolio(
                    returns_df, method=method, weight_cap=0.40, rf=0.045,
                )
            except Exception as exc:
                logger.debug(f"optimization_lab: {method} failed: {exc}")

        if not results:
            st.warning("All optimization methods failed on this panel.")
            return

        # ── 2. Per-method comparison table ────────────────────────────────
        method_labels = {
            "max_sharpe":    "Max Sharpe",
            "min_variance":  "Min Variance",
            "mean_variance": "Mean-Variance (λ=2)",
            "risk_parity":   "Risk Parity",
        }
        headers = ["Method", "Exp. Return", "Exp. Vol", "Sharpe", "Top Holdings"]
        rows: list[list[str]] = []
        for method in VALID_METHODS:
            if method not in results:
                continue
            opt = results[method]
            top2 = sorted(opt.weights.items(), key=lambda kv: kv[1], reverse=True)[:2]
            top_str = ", ".join(f"{t} {w*100:.0f}%" for t, w in top2)
            ret_color = C_HIGH if opt.expected_return > 0.15 else (
                C_MOD if opt.expected_return > 0.05 else C_TEXT2
            )
            sharpe_color = (
                C_HIGH if opt.sharpe > 1.0 else
                (C_MOD if opt.sharpe > 0.4 else C_LOW)
            )
            rows.append([
                _sans(method_labels[method], color=C_TEXT),
                _mono(f"{opt.expected_return*100:+6.1f}%", color=ret_color),
                _mono(f"{opt.expected_vol*100:6.1f}%", color=C_TEXT2),
                _mono(f"{opt.sharpe:5.2f}", color=sharpe_color),
                _sans(top_str, color=C_TEXT2),
            ])
        wsj_market_table(headers, rows)

        # ── 3. Side-by-side weight table ──────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        weight_headers = ["Ticker"] + [method_labels[m] for m in VALID_METHODS if m in results]
        weight_rows: list[list[str]] = []
        for ticker in tickers:
            row = [_mono(ticker, color=C_TEXT)]
            for method in VALID_METHODS:
                if method not in results:
                    continue
                w = results[method].weights.get(ticker, 0.0)
                if w >= 0.40 - 0.005:
                    color = C_LOW  # at cap
                elif w >= 0.20:
                    color = C_HIGH
                elif w >= 0.05:
                    color = C_TEXT
                else:
                    color = C_TEXT3
                row.append(_mono(f"{w*100:5.1f}%", color=color))
            weight_rows.append(row)
        wsj_market_table(weight_headers, weight_rows)
        st.markdown(
            f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-top:4px">'
            f'Cells highlighted in red are at the per-position cap (40%).</div>',
            unsafe_allow_html=True,
        )

        # ── 4. Walk-forward backtest equity curves ─────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        section_header(
            "Walk-Forward Backtest",
            "Train 252 days, rebalance every 21 days. Comparison of "
            "max-Sharpe vs min-variance equity curves (real returns where "
            "available; see footer).",
        )

        bt_results: dict[str, "BacktestResult"] = {}  # type: ignore[name-defined]
        for method in ("max_sharpe", "min_variance"):
            try:
                bt_results[method] = walk_forward_backtest(
                    returns_df, method=method,
                    train_window=252, rebal_freq=21,
                    weight_cap=0.40, rf=0.045,
                )
            except Exception as exc:
                logger.debug(f"optimization_lab: backtest {method} failed: {exc}")

        if any(bt.n_rebalances > 0 for bt in bt_results.values()):
            fig = go.Figure()
            method_color = {"max_sharpe": C_ACCENT, "min_variance": C_HIGH}
            for method, bt in bt_results.items():
                if bt.n_rebalances == 0 or bt.equity_curve.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=bt.equity_curve.index, y=bt.equity_curve.values,
                    mode="lines", name=method_labels[method],
                    line=dict(color=method_color[method], width=2),
                    hovertemplate=(
                        f"<b>{method_labels[method]}</b><br>%{{x|%b %Y}}<br>"
                        f"Equity: %{{y:.3f}}<extra></extra>"
                    ),
                ))
            apply_dark_layout(
                fig,
                title="Backtest Equity Curve — $1 invested",
                height=320,
                margin=dict(l=12, r=12, t=46, b=30),
                yaxis=dict(title=dict(text="Cumulative growth of $1", font=dict(color=C_TEXT2, size=11))),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            # Backtest metrics summary
            metric_cards = []
            for method in ("max_sharpe", "min_variance"):
                if method not in bt_results or bt_results[method].n_rebalances == 0:
                    continue
                bt = bt_results[method]
                metric_cards.append({
                    "label": f"{method_labels[method]} — Ann. Return",
                    "value": f"{bt.annualized_return*100:+5.1f}%",
                    "accent": C_HIGH if bt.annualized_return > 0 else C_LOW,
                    "sublabel": f"Final equity: {bt.final_equity:.2f}×",
                })
                metric_cards.append({
                    "label": f"{method_labels[method]} — Sharpe / MaxDD",
                    "value": f"{bt.sharpe:.2f} / {bt.max_drawdown*100:.1f}%",
                    "accent": (
                        C_HIGH if bt.sharpe > 1.0 else (
                            C_MOD if bt.sharpe > 0.3 else C_LOW
                        )
                    ),
                    # R103: net-of-assumed-cost Sharpe + the turnover it pays for.
                    "sublabel": (
                        f"net Sharpe {bt.net_sharpe:.2f} after "
                        f"~{bt.turnover_per_year:.1f}×/yr turnover · "
                        f"{bt.n_rebalances} rebal"
                    ),
                })
            if metric_cards:
                metric_card_row(metric_cards, columns=2)

        # ── 5. Provenance footer ──────────────────────────────────────────
        st.markdown(
            source_footer([
                DataSource.live(
                    "Real per-ticker daily returns from cached yfinance closes."
                ) if opt_panel_is_real else DataSource.demo(
                    "Synthetic 2-year returns panel — per-ticker mean/vol drawn "
                    "deterministically via utils.helpers.stable_hash; prices dark."
                ),
            ]),
            unsafe_allow_html=True,
        )

    except Exception:
        logger.exception("Portfolio — optimization lab render failed")


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

        # ── Book factor risk: R106 exposure vector + R107 factor-vs-specific
        # split, computed on the SAME real fits + factor panel as the table
        # below (so it inherits this lens's provenance badge). Equal-weights
        # the fitted carriers as a representative book.
        try:
            from engine.carrier_factor_model import (
                factor_covariance,
                factor_risk_decomposition,
                portfolio_factor_exposures,
            )
            _names = list(fits.keys())
            _w = {n: 1.0 / len(_names) for n in _names}
            _decomp = factor_risk_decomposition(_w, fits, factor_covariance(factors_df))
            _expo = portfolio_factor_exposures(_w, fits)
            if _decomp.total_vol > 0:
                metric_card_row([
                    {"label": "Book Vol (ann)", "value": f"{_decomp.total_vol * 100:.1f}%",
                     "accent": C_ACCENT, "sublabel": f"equal-wt {_decomp.n_names} carriers"},
                    {"label": "Factor Risk", "value": f"{_decomp.pct_factor * 100:.0f}%",
                     "accent": C_MOD, "sublabel": "systematic"},
                    {"label": "Specific Risk", "value": f"{_decomp.pct_specific * 100:.0f}%",
                     "accent": C_TEXT2, "sublabel": "idiosyncratic"},
                ], columns=3)
                _top = sorted(_expo.exposures.items(), key=lambda kv: -abs(kv[1]))[:3]
                st.caption(
                    "Net factor tilt (Σ wᵢβᵢ): "
                    + " · ".join(f"{f}: {v:+.2f}" for f, v in _top)
                )
        except Exception as _exc:  # noqa: BLE001
            logger.debug(f"carrier factor-risk panel skipped: {_exc}")

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
                # R103: net of an assumed per-trade turnover cost.
                "sublabel": f"net {bt.net_sharpe:+.2f} · {focus_ticker} walk-fwd",
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

        # R114 (follow-on): the focus carrier is the BEST-R² of N — its raw PSR
        # is selection-inflated. Run the backtest across all fitted carriers and
        # apply a Benjamini-Hochberg FDR haircut so the displayed edge is honest
        # about the multiple-comparisons surface.
        try:
            from processing.stat_significance import TrialsLedger
            ledger = TrialsLedger("carrier-residual")
            for tkr in fits:
                try:
                    cbt = bt if tkr == focus_ticker else residual_signal_backtest(
                        returns_df[tkr], factors_df, name=tkr, lookback=52)
                    ledger.add_sharpe(tkr, cbt.psr)
                except ValueError:
                    continue
            if ledger.n_trials >= 2:
                verdicts = {v.name: v for v in ledger.corrected(alpha=0.05)}
                n_surv = sum(1 for v in verdicts.values() if v.survives_fdr)
                fv = verdicts.get(focus_ticker)
                focus_ok = "survives" if (fv and fv.survives_fdr) else "does NOT survive"
                st.caption(
                    f"Multiple-testing (R114): {n_surv}/{ledger.n_trials} carriers "
                    f"clear the Benjamini-Hochberg FDR across the selection set; "
                    f"{focus_ticker} (best-R²) {focus_ok} the haircut — a single "
                    f"carrier's raw PSR is selection-inflated.")
        except Exception as exc:
            logger.debug(f"carrier FDR correction skipped: {exc}")
    except Exception as e:
        logger.warning(f"carrier factor lens error: {e}")


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(stock_data, macro_data, insights) -> None:
    """Render the Portfolio Tracker tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('portfolio'):
        try:
            _init_positions()

            _render_hero()

            positions = st.session_state.get("portfolio_positions", [])
            df = _build_snapshot(positions, stock_data)

            _render_summary_metrics(df)

            _render_add_position_form()

            # Editorial commentary (per-tab LLM + template fallback)
            _render_editorial_commentary(df)

            section_divider("Holdings")

            _render_holdings_table(df)

            # Charts row: donut + performance
            if not df.empty:
                col_left, col_right = st.columns([1, 1.6])
                with col_left:
                    section_header("Sector Allocation", "Donut: market-value share by shipping sub-sector")
                    _render_composition_chart(df)
                with col_right:
                    section_header("Performance", "Portfolio NAV vs shipping benchmark — 90-day base=100")
                    _render_performance_chart(positions, stock_data)

            section_divider("Risk")

            _render_risk_metrics(df, stock_data, macro_data)
            _render_book_cascade(positions, stock_data, macro_data, insights)

            # Per-position risk-return scatter — complements the aggregate
            # risk cards with a "where is risk concentrated?" cross-section.
            _render_risk_return_scatter(df)

            section_divider("Optimization Lab")

            _render_optimization_lab(df, stock_data)

            section_divider("Factor Attribution")

            _render_carrier_factor_lens(stock_data, macro_data)

            section_divider("Position Detail")

            _render_top_movers(df)

            _render_position_details(df)

        except Exception as e:
            logger.exception(f"Portfolio tab crash: {e}")
            st.error(f"Portfolio tracker encountered an error: {e}")
