"""
Deep Dive Analysis Tab — Bloomberg-Terminal-level analysis for routes, ports, and stocks.

Sections
--------
0. Subject Selector  (Route / Port / Stock)
1. Hero Identity Card
2. Rate / Price History + Technical Indicators
3. Statistical Summary Panel
4. Port Deep Dive  (route mode) | Trade Flow Breakdown (port mode) | Fundamentals (stock mode)
5. Forecasts + Monte Carlo fan chart
6. Multi-factor Correlation Matrix
7. Scenario Analysis  (inline what-if)
8. Historical Comparison  (current vs 1-year vs 2-year ago)
9. AI Narrative  (auto-generated paragraph)
10. News & Sentiment

Function signature:
    render(route_results, freight_data, port_results, macro_data, stock_data, forecasts, insights)
"""
from __future__ import annotations

import datetime
import math
import hashlib
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from routes.optimizer import RouteOpportunity
from routes.route_registry import ROUTES


# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_CARD2   = "#162032"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_PINK    = "#ec4899"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_GOLD    = "#fbbf24"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _score_color(score: float) -> str:
    if score >= 0.65:
        return C_HIGH
    if score >= 0.45:
        return C_MOD
    return C_LOW


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;margin:32px 0 20px">'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,'
        f'rgba(255,255,255,0.0),rgba(255,255,255,0.08))"></div>'
        f'<span style="font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.14em;font-weight:700">{label}</span>'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,'
        f'rgba(255,255,255,0.08),rgba(255,255,255,0.0))"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _mini_bar(label: str, val: float, color: str) -> str:
    pct = max(0.0, min(val * 100, 100))
    return (
        f'<div style="margin-bottom:7px">'
        f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
        f'<span style="font-size:0.67rem;color:{C_TEXT3};font-weight:600">{label}</span>'
        f'<span style="font-size:0.67rem;color:{color};font-weight:800">{pct:.0f}%</span>'
        f'</div>'
        f'<div style="background:rgba(255,255,255,0.05);border-radius:4px;height:4px;overflow:hidden">'
        f'<div style="background:linear-gradient(90deg,{color},{_hex_to_rgba(color, 0.5)});'
        f'width:{pct:.0f}%;height:100%;border-radius:4px;'
        f'box-shadow:0 0 6px {_hex_to_rgba(color, 0.5)}"></div>'
        f'</div>'
        f'</div>'
    )


def _dark_layout(height: int = 400, margin: dict | None = None) -> dict:
    m = margin or dict(l=24, r=24, t=44, b=24)
    return dict(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
        height=height,
        margin=m,
        hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12)),
    )


def _axis_style() -> dict:
    return dict(
        gridcolor="rgba(255,255,255,0.04)",
        zerolinecolor="rgba(255,255,255,0.08)",
        tickfont=dict(color=C_TEXT3, size=10),
        linecolor="rgba(255,255,255,0.08)",
    )


def _legend_style() -> dict:
    return dict(
        bgcolor="rgba(0,0,0,0)",
        bordercolor="rgba(255,255,255,0.08)",
        font=dict(color=C_TEXT2, size=10),
        orientation="h",
        yanchor="bottom", y=1.02,
        xanchor="right", x=1,
    )


def _stat_card(label: str, value: str, sub: str = "", color: str = C_ACCENT,
               glow: bool = False) -> str:
    sub_html = (
        f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:4px">{sub}</div>'
        if sub else ""
    )
    shadow = f"box-shadow:0 0 24px {_hex_to_rgba(color, 0.18)};" if glow else ""
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-top:2px solid {color};border-radius:12px;padding:16px 18px;'
        f'text-align:center;{shadow}">'
        f'<div style="font-size:0.62rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">{label}</div>'
        f'<div style="font-size:1.4rem;font-weight:800;color:{C_TEXT};line-height:1">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{_hex_to_rgba(color, 0.15)};color:{color};'
        f'border:1px solid {_hex_to_rgba(color, 0.35)};'
        f'padding:3px 12px;border-radius:999px;font-size:0.72rem;font-weight:700;'
        f'white-space:nowrap">{text}</span>'
    )


def _trend_arrow(trend: str) -> tuple[str, str]:
    arrows = {"Rising": ("▲", C_HIGH), "Falling": ("▼", C_LOW), "Stable": ("→", C_MOD)}
    return arrows.get(trend, ("→", C_MOD))


def _uid(*parts) -> str:
    """Deterministic short key from string parts, safe for Streamlit widget keys."""
    raw = "_".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()[:10]


# ── Section 0: Universal Subject Selector ─────────────────────────────────────

def _render_subject_selector(
    route_results: list[RouteOpportunity],
    port_results: list,
    stock_data: dict,
) -> tuple[str, Any]:
    """
    Returns (subject_type, subject_object).
    subject_type in {"route", "port", "stock"}
    """
    st.markdown(
        f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
        f'border:1px solid {C_BORDER};border-radius:16px;padding:24px 28px;margin-bottom:8px">'
        f'<div style="font-size:1.1rem;font-weight:800;color:{C_TEXT};margin-bottom:4px;'
        f'letter-spacing:-0.01em">Deep Dive Analysis</div>'
        f'<div style="font-size:0.8rem;color:{C_TEXT3}">Select any route, port, or stock ticker '
        f'for Bloomberg-level analysis with full history, correlations, and scenario modeling.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    t_col, s_col = st.columns([1, 4])

    with t_col:
        subject_type = st.selectbox(
            "Type",
            options=["Route", "Port", "Stock"],
            key="dd_subject_type",
            label_visibility="collapsed",
        ).lower()

    with s_col:
        if subject_type == "route":
            opts = {}
            for r in route_results:
                pct = f"{r.opportunity_score * 100:.0f}%"
                label = f"{r.route_name}  ({r.origin_locode} → {r.dest_locode})  [{pct}]"
                opts[label] = r
            if not opts:
                st.warning("No route data available.")
                return "route", None
            keys = list(opts.keys())
            saved = st.session_state.get("dd_route_select")
            idx = keys.index(saved) if saved in keys else 0
            chosen = st.selectbox("Route", keys, index=idx,
                                  label_visibility="collapsed", key="dd_route_select")
            return "route", opts.get(chosen, list(opts.values())[0])

        elif subject_type == "port":
            port_opts = {}
            for p in (port_results or []):
                label = f"{p.port_name}  ({p.locode})  — {p.region}"
                port_opts[label] = p
            if not port_opts:
                st.warning("No port data available.")
                return "port", None
            keys = list(port_opts.keys())
            saved = st.session_state.get("dd_port_select")
            idx = keys.index(saved) if saved in keys else 0
            chosen = st.selectbox("Port", keys, index=idx,
                                  label_visibility="collapsed", key="dd_port_select")
            return "port", port_opts.get(chosen, list(port_opts.values())[0])

        else:  # stock
            tickers = sorted(stock_data.keys()) if stock_data else []
            if not tickers:
                st.warning("No stock data available.")
                return "stock", None
            saved = st.session_state.get("dd_stock_select")
            idx = tickers.index(saved) if saved in tickers else 0
            chosen = st.selectbox("Ticker", tickers, index=idx,
                                  label_visibility="collapsed", key="dd_stock_select")
            return "stock", chosen

    return "route", None


# ── Route sections ─────────────────────────────────────────────────────────────

def _render_route_hero(r: RouteOpportunity) -> None:
    score_color = _score_color(r.opportunity_score)
    score_pct   = f"{r.opportunity_score * 100:.0f}"
    arr, t_col  = _trend_arrow(r.rate_trend)
    rate_str    = f"${r.current_rate_usd_feu:,.0f}" if r.current_rate_usd_feu > 0 else "N/A"
    pct_30      = f"{r.rate_pct_change_30d * 100:+.1f}% (30d)" if r.current_rate_usd_feu > 0 else ""

    updated_mins = 0
    try:
        gen = datetime.datetime.fromisoformat(r.generated_at.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        updated_mins = max(0, int((now - gen).total_seconds() / 60))
    except Exception:
        pass

    sub_bars = (
        _mini_bar("Rate Momentum",      r.rate_momentum_component,         C_ACCENT)
        + _mini_bar("Demand Imbalance", r.demand_imbalance_component,      C_HIGH)
        + _mini_bar("Congestion Clear.",r.congestion_clearance_component,   C_MOD)
        + _mini_bar("Macro Tailwind",   r.macro_tailwind_component,         C_CONV)
    )

    html = (
        f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
        f'border:1px solid {_hex_to_rgba(score_color, 0.4)};'
        f'border-top:3px solid {score_color};border-radius:16px;'
        f'padding:28px 32px;margin-bottom:4px;'
        f'box-shadow:0 4px 40px {_hex_to_rgba(score_color, 0.12)}">'

        # Header row
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
        f'margin-bottom:22px">'
        f'<div>'
        f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.12em;margin-bottom:6px">Route Deep Dive</div>'
        f'<div style="font-size:1.9rem;font-weight:800;color:{C_TEXT};line-height:1.05;'
        f'letter-spacing:-0.025em">{r.route_name}</div>'
        f'<div style="display:flex;align-items:center;gap:10px;margin-top:8px">'
        f'<span style="font-size:1rem;color:{C_TEXT2}">{r.origin_locode}</span>'
        f'<span style="font-size:1.1rem;color:{score_color};font-weight:700">&#8594;</span>'
        f'<span style="font-size:1rem;color:{C_TEXT2}">{r.dest_locode}</span>'
        f'<span style="font-size:0.72rem;color:{C_TEXT3};background:rgba(255,255,255,0.04);'
        f'padding:2px 9px;border-radius:6px;border:1px solid {C_BORDER}">'
        f'{r.transit_days}d transit</span>'
        f'</div>'
        f'</div>'
        f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">'
        + _badge(r.fbx_index, C_ACCENT)
        + _badge(r.opportunity_label, score_color)
        + f'</div>'
        f'</div>'

        # Metrics grid
        f'<div style="display:grid;grid-template-columns:auto 1fr 1fr;gap:28px;align-items:start">'

        # Score orb
        f'<div style="text-align:center;min-width:100px">'
        f'<div style="font-size:3.2rem;font-weight:900;color:{score_color};line-height:1;'
        f'text-shadow:0 0 40px {_hex_to_rgba(score_color, 0.5)}">{score_pct}%</div>'
        f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.1em;margin-top:5px">Opportunity Score</div>'
        f'</div>'

        # Rate block
        f'<div style="border-left:1px solid {C_BORDER};padding-left:24px">'
        f'<div style="font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.1em;margin-bottom:4px">Current Rate</div>'
        f'<div style="font-size:1.8rem;font-weight:800;color:{C_TEXT};line-height:1">{rate_str}</div>'
        f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-top:2px">USD / FEU</div>'
        f'<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">'
        + _badge(f"{arr} {r.rate_trend}  {pct_30}", t_col)
        + f'</div>'
        f'</div>'

        # Sub-score bars
        f'<div style="border-left:1px solid {C_BORDER};padding-left:24px">'
        f'<div style="font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.1em;margin-bottom:10px">Signal Breakdown</div>'
        f'{sub_bars}'
        f'</div>'
        f'</div>'

        # Footer
        f'<div style="margin-top:18px;padding-top:12px;border-top:1px solid {C_BORDER};'
        f'font-size:0.68rem;color:{C_TEXT3};display:flex;gap:20px;flex-wrap:wrap">'
        f'<span>Updated {updated_mins} min ago</span>'
        f'<span>Origin: {r.origin_region}</span>'
        f'<span>Destination: {r.dest_region}</span>'
        f'</div>'

        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _compute_ohlc_weekly(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    rate = df["rate_usd_per_feu"]
    ohlc = rate.resample("W").agg(open="first", high="max", low="min", close="last")
    return ohlc.dropna()


def _render_route_rate_chart(route_id: str, freight_data: dict,
                              current_rate: float) -> None:
    df_raw = freight_data.get(route_id)
    if df_raw is None or df_raw.empty or len(df_raw) < 5:
        st.info("Insufficient rate history for this route.")
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    rates = df["rate_usd_per_feu"]

    ma7  = rates.rolling(7,  min_periods=1).mean()
    ma30 = rates.rolling(30, min_periods=1).mean()
    ma90 = rates.rolling(90, min_periods=1).mean()

    bb_mid   = rates.rolling(20, min_periods=1).mean()
    bb_std   = rates.rolling(20, min_periods=1).std(ddof=0).fillna(0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # RSI-14
    delta    = rates.diff()
    gain     = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss     = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs       = gain / loss.replace(0, np.nan)
    rsi      = 100 - (100 / (1 + rs))
    rsi      = rsi.fillna(50)

    vol_7d   = rates.rolling(7, min_periods=1).std(ddof=0).fillna(0)
    vol_color = [C_HIGH if r >= m else C_LOW for r, m in zip(rates, ma30)]

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.20, 0.20],
        vertical_spacing=0.03,
        subplot_titles=["Freight Rate (USD/FEU)", "RSI-14", "Volatility (7d σ)"],
    )

    # Bollinger fill
    fig.add_trace(go.Scatter(
        x=pd.concat([df["date"], df["date"][::-1]]),
        y=pd.concat([bb_upper, bb_lower[::-1]]),
        fill="toself", fillcolor=_hex_to_rgba(C_ACCENT, 0.06),
        line=dict(color="rgba(0,0,0,0)"),
        name="Bollinger Band (20d,2σ)", hoverinfo="skip", showlegend=True,
    ), row=1, col=1)

    n_weeks = len(df) // 7
    if n_weeks >= 4:
        ohlc = _compute_ohlc_weekly(df)
        fig.add_trace(go.Candlestick(
            x=ohlc.index, open=ohlc["open"], high=ohlc["high"],
            low=ohlc["low"], close=ohlc["close"],
            name="Weekly OHLC",
            increasing=dict(line=dict(color=C_HIGH), fillcolor=_hex_to_rgba(C_HIGH, 0.4)),
            decreasing=dict(line=dict(color=C_LOW),  fillcolor=_hex_to_rgba(C_LOW,  0.4)),
        ), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(
            x=df["date"], y=rates, mode="lines",
            line=dict(color=C_ACCENT, width=2), name="Rate (daily)",
            hovertemplate="%{x|%Y-%m-%d}: $%{y:,.0f}/FEU<extra></extra>",
        ), row=1, col=1)

    for ma_y, ma_name, ma_col in [(ma7, "7d MA", "white"), (ma30, "30d MA", C_ACCENT), (ma90, "90d MA", C_MOD)]:
        fig.add_trace(go.Scatter(
            x=df["date"], y=ma_y, mode="lines",
            line=dict(color=ma_col, width=1.2, dash="dash"),
            name=ma_name, opacity=0.8,
            hovertemplate=f"{ma_name}: $%{{y:,.0f}}<extra></extra>",
        ), row=1, col=1)

    if current_rate > 0:
        fig.add_hline(y=current_rate, line_dash="dot", line_color=C_HIGH, line_width=1.2,
                      annotation_text=f"  Now ${current_rate:,.0f}",
                      annotation_position="right",
                      annotation_font=dict(color=C_HIGH, size=10), row=1, col=1)

    # RSI
    fig.add_trace(go.Scatter(
        x=df["date"], y=rsi, mode="lines",
        line=dict(color=C_CONV, width=1.5), name="RSI-14",
        hovertemplate="RSI: %{y:.1f}<extra></extra>",
    ), row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color=C_LOW,   line_width=0.8, row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color=C_HIGH,  line_width=0.8, row=2, col=1)

    # Volatility bars
    fig.add_trace(go.Bar(
        x=df["date"], y=vol_7d, marker_color=vol_color,
        name="Volatility (7d σ)", opacity=0.75,
        hovertemplate="%{x|%Y-%m-%d}: σ=%{y:,.0f}<extra></extra>",
    ), row=3, col=1)

    ax = _axis_style()
    fig.update_layout(
        template="plotly_dark", **_dark_layout(560, dict(l=20, r=20, t=40, b=20)),
        showlegend=True, legend=_legend_style(),
        xaxis_rangeslider_visible=False,
    )
    for k in ["xaxis", "xaxis2", "xaxis3", "yaxis", "yaxis2", "yaxis3"]:
        fig.update_layout(**{k: ax})
    fig.update_layout(
        yaxis_title="USD/FEU", yaxis2_title="RSI", yaxis3_title="σ",
        yaxis2=dict(**ax, range=[0, 100]),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"dd_rate_{_uid(route_id)}")


def _render_route_stats(route_id: str, freight_data: dict) -> None:
    df_raw = freight_data.get(route_id)
    if df_raw is None or df_raw.empty or len(df_raw) < 5:
        st.info("Insufficient data for statistical summary.")
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    rates = df["rate_usd_per_feu"].dropna()
    current = float(rates.iloc[-1])

    cutoff_52w = df["date"].max() - pd.Timedelta(weeks=52)
    r52 = rates[df["date"] >= cutoff_52w]
    hi_52 = float(r52.max()) if len(r52) else current
    lo_52 = float(r52.min()) if len(r52) else current

    cutoff_90d = df["date"].max() - pd.Timedelta(days=90)
    r90 = rates[df["date"] >= cutoff_90d]
    avg_90d   = float(r90.mean()) if len(r90) else current
    vs_90d    = (current / avg_90d - 1) * 100 if avg_90d > 0 else 0.0

    log_ret  = np.log(rates / rates.shift(1)).dropna()
    ann_vol  = float(log_ret.std(ddof=1)) * math.sqrt(252) * 100 if len(log_ret) > 1 else 0.0

    hist_mean = float(rates.mean())
    hist_std  = float(rates.std(ddof=1)) if len(rates) > 1 else 1.0
    z_score   = (current - hist_mean) / hist_std if hist_std > 0 else 0.0

    sharpe_proxy = (float(log_ret.mean()) / float(log_ret.std(ddof=1)) * math.sqrt(252)
                    if len(log_ret) > 1 and log_ret.std() > 0 else 0.0)

    pct_pos   = float((log_ret > 0).sum()) / len(log_ret) * 100 if len(log_ret) else 50.0

    hi_color  = C_LOW  if current >= hi_52 * 0.95 else C_TEXT
    lo_color  = C_HIGH if current <= lo_52 * 1.05 else C_TEXT
    pct_color = C_HIGH if vs_90d > 0 else C_LOW
    z_color   = C_LOW  if z_score > 1.5 else (C_HIGH if z_score < -1.5 else C_MOD)
    sh_color  = C_HIGH if sharpe_proxy > 0.5 else (C_LOW if sharpe_proxy < -0.5 else C_MOD)

    cols = st.columns(6)
    cards = [
        ("52-Wk High",    f"${hi_52:,.0f}",       "USD/FEU",            hi_color),
        ("52-Wk Low",     f"${lo_52:,.0f}",        "USD/FEU",            lo_color),
        ("vs 90d Avg",    f"{vs_90d:+.1f}%",       f"Avg ${avg_90d:,.0f}", pct_color),
        ("Ann. Volatility",f"{ann_vol:.1f}%",      "log-return basis",   C_CONV),
        ("Z-Score",       f"{z_score:+.2f}σ",      "from hist mean",     z_color),
        ("Sharpe Proxy",  f"{sharpe_proxy:+.2f}",  f"{pct_pos:.0f}% up-days", sh_color),
    ]
    for col, (lbl, val, sub, col_) in zip(cols, cards):
        col.markdown(_stat_card(lbl, val, sub, col_), unsafe_allow_html=True)

    st.markdown(
        f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-top:8px">'
        f'Z-Score = standard deviations from all-time mean. '
        f'Sharpe Proxy = annualised mean log-return ÷ std dev (no risk-free rate).'
        f'</div>', unsafe_allow_html=True,
    )


def _render_route_port_cards(r: RouteOpportunity, port_results: list) -> None:
    col_orig, col_dest = st.columns(2)
    for col, locode, side in [(col_orig, r.origin_locode, "Origin"),
                               (col_dest, r.dest_locode, "Destination")]:
        with col:
            result = next((p for p in (port_results or []) if p.locode == locode), None)
            if result is None:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                    f'border-radius:12px;padding:24px;color:{C_TEXT3};text-align:center;'
                    f'font-size:0.82rem">{side}: {locode}<br>No port data available.</div>',
                    unsafe_allow_html=True,
                )
                continue

            d_score  = result.demand_score
            d_color  = _score_color(d_score)
            c_idx    = result.congestion_index
            c_color  = C_HIGH if c_idx < 0.35 else (C_LOW if c_idx >= 0.65 else C_MOD)
            c_label  = "Low" if c_idx < 0.35 else ("High" if c_idx >= 0.65 else "Moderate")
            arr, t_c = _trend_arrow(result.demand_trend)
            products = (result.top_products or [])[:4]

            pills = "".join(
                f'<span style="background:{_hex_to_rgba(p.get("color", C_ACCENT), 0.15)};'
                f'color:{p.get("color", C_ACCENT)};'
                f'border:1px solid {_hex_to_rgba(p.get("color", C_ACCENT), 0.3)};'
                f'padding:2px 9px;border-radius:999px;font-size:0.65rem;font-weight:600;'
                f'margin:2px;display:inline-block">{p.get("category","")}</span>'
                for p in products
            ) or f'<span style="color:{C_TEXT3};font-size:0.75rem">No product data</span>'

            # Gauge figure
            gauge = go.Figure(go.Pie(
                values=[d_score, max(0.0, 1.0 - d_score)],
                hole=0.74,
                marker=dict(colors=[d_color, "rgba(255,255,255,0.04)"]),
                showlegend=False, textinfo="none", hoverinfo="none",
            ))
            gauge.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", height=100,
                margin=dict(l=0, r=0, t=0, b=0),
                annotations=[dict(
                    text=f"<b>{d_score * 100:.0f}%</b>",
                    x=0.5, y=0.5,
                    font=dict(size=15, color=d_color, family="Inter"),
                    showarrow=False,
                )],
            )

            # Trade flow bar
            total_trade = (result.import_value_usd or 0) + (result.export_value_usd or 0)
            imp_pct = (result.import_value_usd / total_trade * 100) if total_trade > 0 else 50
            exp_pct = 100 - imp_pct

            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-top:2px solid {d_color};border-radius:14px;padding:20px 22px">'
                f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">{side}</div>'
                f'<div style="font-size:1.1rem;font-weight:700;color:{C_TEXT};margin-bottom:2px">'
                f'{result.port_name}</div>'
                f'<div style="font-size:0.75rem;color:{C_TEXT3};margin-bottom:14px">'
                f'{locode} &bull; {result.region}</div>'
                f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">Demand Score</div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(gauge, use_container_width=True,
                            key=f"dd_port_gauge_{locode}_{side}_{_uid(locode, side)}")
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">'
                f'<span style="font-size:0.78rem;color:{t_c}">{arr} {result.demand_trend}</span>'
                f'</div>'

                # Trade flow split
                f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">Trade Flow</div>'
                f'<div style="display:flex;border-radius:4px;overflow:hidden;height:8px;margin-bottom:4px">'
                f'<div style="width:{imp_pct:.0f}%;background:{C_ACCENT};"></div>'
                f'<div style="width:{exp_pct:.0f}%;background:{C_HIGH};"></div>'
                f'</div>'
                f'<div style="display:flex;justify-content:space-between;font-size:0.65rem;'
                f'color:{C_TEXT3};margin-bottom:12px">'
                f'<span style="color:{C_ACCENT}">Imports {imp_pct:.0f}%</span>'
                f'<span style="color:{C_HIGH}">Exports {exp_pct:.0f}%</span>'
                f'</div>'

                f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">Top Products</div>'
                f'<div style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:14px">{pills}</div>'

                f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">'
                f'<div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px;'
                f'text-align:center">'
                f'<div style="font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;'
                f'margin-bottom:3px">Congestion</div>'
                f'<div style="font-size:0.9rem;font-weight:700;color:{c_color}">{c_label}</div>'
                f'<div style="font-size:0.62rem;color:{C_TEXT3}">{c_idx * 100:.0f}%</div>'
                f'</div>'
                f'<div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px;'
                f'text-align:center">'
                f'<div style="font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;'
                f'margin-bottom:3px">AIS Vessels</div>'
                f'<div style="font-size:0.9rem;font-weight:700;color:{C_TEXT}">'
                f'{result.vessel_count}</div>'
                f'<div style="font-size:0.62rem;color:{C_TEXT3}">in zone</div>'
                f'</div>'
                f'<div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px;'
                f'text-align:center">'
                f'<div style="font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;'
                f'margin-bottom:3px">Throughput</div>'
                f'<div style="font-size:0.9rem;font-weight:700;color:{C_TEXT}">'
                f'{result.throughput_teu_m:.1f}M</div>'
                f'<div style="font-size:0.62rem;color:{C_TEXT3}">TEU/yr</div>'
                f'</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── Port sections ──────────────────────────────────────────────────────────────

def _render_port_hero(p: Any) -> None:
    d_color  = _score_color(p.demand_score)
    c_idx    = p.congestion_index
    c_color  = C_HIGH if c_idx < 0.35 else (C_LOW if c_idx >= 0.65 else C_MOD)
    c_label  = "Low" if c_idx < 0.35 else ("High" if c_idx >= 0.65 else "Moderate")
    arr, t_c = _trend_arrow(p.demand_trend)

    html = (
        f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
        f'border:1px solid {_hex_to_rgba(d_color, 0.4)};'
        f'border-top:3px solid {d_color};border-radius:16px;'
        f'padding:28px 32px;margin-bottom:4px;'
        f'box-shadow:0 4px 40px {_hex_to_rgba(d_color, 0.12)}">'

        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
        f'margin-bottom:22px">'
        f'<div>'
        f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.12em;margin-bottom:6px">Port Deep Dive</div>'
        f'<div style="font-size:1.9rem;font-weight:800;color:{C_TEXT};line-height:1.05;'
        f'letter-spacing:-0.025em">{p.port_name}</div>'
        f'<div style="font-size:0.95rem;color:{C_TEXT3};margin-top:6px">'
        f'{p.locode} &bull; {p.region} &bull; {p.country_iso3}</div>'
        f'</div>'
        f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">'
        + _badge(p.demand_label, d_color)
        + _badge(f"Congestion: {c_label}", c_color)
        + f'</div>'
        f'</div>'

        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px">'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">Demand Score</div>'
        f'<div style="font-size:1.8rem;font-weight:900;color:{d_color};'
        f'text-shadow:0 0 20px {_hex_to_rgba(d_color, 0.4)}">'
        f'{p.demand_score * 100:.0f}%</div>'
        f'<div style="font-size:0.7rem;color:{t_c};margin-top:2px">{arr} {p.demand_trend}</div>'
        f'</div>'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">Vessels (AIS)</div>'
        f'<div style="font-size:1.8rem;font-weight:900;color:{C_TEXT}">{p.vessel_count}</div>'
        f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:2px">in port zone</div>'
        f'</div>'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">Throughput</div>'
        f'<div style="font-size:1.8rem;font-weight:900;color:{C_TEXT}">{p.throughput_teu_m:.1f}M</div>'
        f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:2px">TEU / year</div>'
        f'</div>'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">Congestion</div>'
        f'<div style="font-size:1.8rem;font-weight:900;color:{c_color}">'
        f'{c_idx * 100:.0f}%</div>'
        f'<div style="font-size:0.7rem;color:{c_color};margin-top:2px">{c_label}</div>'
        f'</div>'
        f'</div>'

        # Sub-score bars
        f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px;'
        f'padding-top:18px;border-top:1px solid {C_BORDER}">'
        + f'<div><div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:8px">Trade Flow</div>'
        + _mini_bar("Score", p.trade_flow_component, C_ACCENT) + f'</div>'
        + f'<div><div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:8px">Congestion</div>'
        + _mini_bar("Score", p.congestion_component, C_MOD) + f'</div>'
        + f'<div><div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:8px">Throughput</div>'
        + _mini_bar("Score", p.throughput_component, C_CONV) + f'</div>'
        + f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _render_port_trade_breakdown(p: Any) -> None:
    products = (p.top_products or [])[:8]
    if not products:
        st.info("No product breakdown available for this port.")
        return

    names  = [pr.get("category", "Unknown") for pr in products]
    values = [pr.get("value_usd", 0)         for pr in products]
    colors = [pr.get("color", C_ACCENT)       for pr in products]

    col_pie, col_bars = st.columns([1, 1])

    with col_pie:
        fig = go.Figure(go.Pie(
            labels=names, values=values,
            hole=0.55,
            marker=dict(colors=colors, line=dict(color=C_BG, width=2)),
            textfont=dict(color=C_TEXT, size=11),
            hovertemplate="%{label}: $%{value:,.0f}<extra></extra>",
        ))
        fig.update_layout(
            **_dark_layout(300, dict(l=10, r=10, t=30, b=10)),
            title=dict(text="Trade Composition", font=dict(size=12, color=C_TEXT2), x=0.0),
            legend=dict(font=dict(color=C_TEXT2, size=9), bgcolor="rgba(0,0,0,0)"),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"dd_port_pie_{_uid(p.locode)}")

    with col_bars:
        bar_fig = go.Figure(go.Bar(
            x=values, y=names,
            orientation="h",
            marker=dict(color=colors, line=dict(color="rgba(0,0,0,0)")),
            hovertemplate="%{y}: $%{x:,.0f}<extra></extra>",
        ))
        bar_fig.update_layout(
            **_dark_layout(300, dict(l=10, r=20, t=30, b=10)),
            title=dict(text="Value by Category (USD)", font=dict(size=12, color=C_TEXT2), x=0.0),
            xaxis=dict(**_axis_style(), tickformat="$,.0f"),
            yaxis=dict(**_axis_style()),
        )
        st.plotly_chart(bar_fig, use_container_width=True,
                        key=f"dd_port_bars_{_uid(p.locode)}")

    # Import / export split
    total = (p.import_value_usd or 0) + (p.export_value_usd or 0)
    if total > 0:
        _divider("Import / Export Balance")
        imp_pct = p.import_value_usd / total * 100
        exp_pct = p.export_value_usd / total * 100
        c1, c2, c3 = st.columns(3)
        c1.markdown(_stat_card("Total Trade", f"${total / 1e9:.2f}B", "USD", C_ACCENT), unsafe_allow_html=True)
        c2.markdown(_stat_card("Imports", f"${p.import_value_usd / 1e9:.2f}B",
                                f"{imp_pct:.1f}% of total", C_CONV), unsafe_allow_html=True)
        c3.markdown(_stat_card("Exports", f"${p.export_value_usd / 1e9:.2f}B",
                                f"{exp_pct:.1f}% of total", C_HIGH), unsafe_allow_html=True)


def _render_port_connections(p: Any, route_results: list[RouteOpportunity]) -> None:
    connected = [r for r in (route_results or [])
                 if r.origin_locode == p.locode or r.dest_locode == p.locode]
    if not connected:
        st.info("No tracked routes connected to this port.")
        return

    rows = []
    for r in connected:
        role    = "Origin" if r.origin_locode == p.locode else "Destination"
        partner = r.dest_locode if r.origin_locode == p.locode else r.origin_locode
        rows.append({
            "Route": r.route_name,
            "Role": role,
            "Partner": partner,
            "Rate (USD/FEU)": f"${r.current_rate_usd_feu:,.0f}" if r.current_rate_usd_feu > 0 else "N/A",
            "Trend": r.rate_trend,
            "Score": f"{r.opportunity_score * 100:.0f}%",
            "Transit (d)": r.transit_days,
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                       format="%d%%"),
        },
    )


# ── Stock sections ─────────────────────────────────────────────────────────────

def _render_stock_hero(ticker: str, stock_data: dict) -> None:
    df_raw = stock_data.get(ticker)
    if df_raw is None or df_raw.empty:
        st.warning(f"No data available for {ticker}.")
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"].dropna()
    current = float(close.iloc[-1])
    prev    = float(close.iloc[-2]) if len(close) >= 2 else current
    day_chg = (current / prev - 1) * 100 if prev != 0 else 0.0

    hi_52 = float(close.tail(252).max())
    lo_52 = float(close.tail(252).min())
    vs_hi = (current / hi_52 - 1) * 100 if hi_52 > 0 else 0.0

    # Simple 30d avg vol
    avg_vol_30 = float(df["volume"].tail(30).mean()) if "volume" in df.columns else 0.0

    chg_color = C_HIGH if day_chg >= 0 else C_LOW
    chg_arrow = "▲" if day_chg >= 0 else "▼"

    html = (
        f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
        f'border:1px solid {_hex_to_rgba(chg_color, 0.4)};'
        f'border-top:3px solid {chg_color};border-radius:16px;'
        f'padding:28px 32px;margin-bottom:4px;'
        f'box-shadow:0 4px 40px {_hex_to_rgba(chg_color, 0.12)}">'

        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
        f'margin-bottom:22px">'
        f'<div>'
        f'<div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.12em;margin-bottom:6px">Stock Deep Dive</div>'
        f'<div style="font-size:2.2rem;font-weight:900;color:{C_TEXT};letter-spacing:-0.03em;'
        f'line-height:1">{ticker}</div>'
        f'<div style="font-size:0.9rem;color:{C_TEXT3};margin-top:6px">Shipping-correlated equity</div>'
        f'</div>'
        f'<div style="text-align:right">'
        f'<div style="font-size:2rem;font-weight:900;color:{C_TEXT}">${current:,.2f}</div>'
        f'<div style="font-size:1rem;color:{chg_color};font-weight:700;margin-top:4px">'
        f'{chg_arrow} {day_chg:+.2f}%</div>'
        f'</div>'
        f'</div>'

        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px">'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">52-Wk High</div>'
        f'<div style="font-size:1.3rem;font-weight:800;color:{C_TEXT}">${hi_52:,.2f}</div>'
        f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-top:2px">'
        f'{vs_hi:+.1f}% from here</div>'
        f'</div>'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">52-Wk Low</div>'
        f'<div style="font-size:1.3rem;font-weight:800;color:{C_TEXT}">${lo_52:,.2f}</div>'
        f'<div style="font-size:0.68rem;color:{C_HIGH};margin-top:2px">'
        f'{(current/lo_52-1)*100:+.1f}% above low</div>'
        f'</div>'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">Avg Vol (30d)</div>'
        f'<div style="font-size:1.3rem;font-weight:800;color:{C_TEXT}">'
        f'{avg_vol_30/1e6:.1f}M</div>'
        f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-top:2px">shares / day</div>'
        f'</div>'

        f'<div style="background:rgba(255,255,255,0.03);border-radius:10px;padding:14px;'
        f'text-align:center">'
        f'<div style="font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;'
        f'margin-bottom:4px">Data Points</div>'
        f'<div style="font-size:1.3rem;font-weight:800;color:{C_TEXT}">{len(df)}</div>'
        f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-top:2px">trading days</div>'
        f'</div>'

        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _render_stock_price_chart(ticker: str, stock_data: dict) -> None:
    df_raw = stock_data.get(ticker)
    if df_raw is None or df_raw.empty:
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].fillna(method="ffill")

    ma20  = close.rolling(20,  min_periods=1).mean()
    ma50  = close.rolling(50,  min_periods=1).mean()
    ma200 = close.rolling(200, min_periods=1).mean()
    bb_mid  = ma20
    bb_std  = close.rolling(20, min_periods=1).std(ddof=0).fillna(0)
    bb_up   = bb_mid + 2 * bb_std
    bb_dn   = bb_mid - 2 * bb_std

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rsi   = (100 - (100 / (1 + gain / loss.replace(0, np.nan)))).fillna(50)

    has_vol = "volume" in df.columns and df["volume"].sum() > 0
    rows_n  = 3 if has_vol else 2
    heights = [0.55, 0.20, 0.25] if has_vol else [0.70, 0.30]
    titles  = (["Price (USD)", "RSI-14", "Volume"] if has_vol
               else ["Price (USD)", "RSI-14"])

    fig = make_subplots(
        rows=rows_n, cols=1, shared_xaxes=True,
        row_heights=heights, vertical_spacing=0.03,
        subplot_titles=titles,
    )

    # BB fill
    fig.add_trace(go.Scatter(
        x=pd.concat([df["date"], df["date"][::-1]]),
        y=pd.concat([bb_up, bb_dn[::-1]]),
        fill="toself", fillcolor=_hex_to_rgba(C_CONV, 0.07),
        line=dict(color="rgba(0,0,0,0)"),
        name="Bollinger (20d,2σ)", hoverinfo="skip",
    ), row=1, col=1)

    # Candles if OHLC available
    has_ohlc = all(c in df.columns for c in ["open", "high", "low", "close"])
    if has_ohlc and len(df) >= 14:
        fig.add_trace(go.Candlestick(
            x=df["date"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name=ticker,
            increasing=dict(line=dict(color=C_HIGH), fillcolor=_hex_to_rgba(C_HIGH, 0.4)),
            decreasing=dict(line=dict(color=C_LOW),  fillcolor=_hex_to_rgba(C_LOW,  0.4)),
        ), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(
            x=df["date"], y=close, mode="lines",
            line=dict(color=C_CONV, width=2), name=ticker,
            hovertemplate="%{x|%Y-%m-%d}: $%{y:,.2f}<extra></extra>",
        ), row=1, col=1)

    for ma_y, ma_n, ma_c in [(ma20, "20d MA", "white"),
                               (ma50, "50d MA", C_CONV),
                               (ma200, "200d MA", C_GOLD)]:
        fig.add_trace(go.Scatter(
            x=df["date"], y=ma_y, mode="lines",
            line=dict(color=ma_c, width=1.2, dash="dash"),
            name=ma_n, opacity=0.8,
        ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["date"], y=rsi, mode="lines",
        line=dict(color=C_PINK, width=1.5), name="RSI-14",
    ), row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color=C_LOW,  line_width=0.8, row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color=C_HIGH, line_width=0.8, row=2, col=1)

    if has_vol:
        vol_colors = [C_HIGH if (df["close"].iloc[i] >= df["close"].iloc[i - 1]) else C_LOW
                      for i in range(len(df))]
        vol_colors[0] = C_TEXT3
        fig.add_trace(go.Bar(
            x=df["date"], y=df["volume"], marker_color=vol_colors,
            name="Volume", opacity=0.7,
            hovertemplate="%{x|%Y-%m-%d}: %{y:,.0f}<extra></extra>",
        ), row=rows_n, col=1)

    ax = _axis_style()
    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(560, dict(l=20, r=20, t=40, b=20)),
        showlegend=True, legend=_legend_style(),
        xaxis_rangeslider_visible=False,
    )
    for k in [f"xaxis{'' if i == 1 else i}" for i in range(1, rows_n + 1)] + \
             [f"yaxis{'' if i == 1 else i}" for i in range(1, rows_n + 1)]:
        fig.update_layout(**{k: ax})
    fig.update_layout(yaxis2=dict(**ax, range=[0, 100]))

    st.plotly_chart(fig, use_container_width=True, key=f"dd_stock_chart_{_uid(ticker)}")


def _render_stock_freight_correlation(ticker: str, stock_data: dict,
                                       freight_data: dict) -> None:
    s_raw = stock_data.get(ticker)
    if s_raw is None or s_raw.empty:
        return

    sdf = s_raw.copy()
    sdf["date"] = pd.to_datetime(sdf["date"])
    sdf = sdf.sort_values("date").set_index("date")["close"]

    correlations = []
    for rid, rdf_raw in (freight_data or {}).items():
        if rdf_raw is None or rdf_raw.empty:
            continue
        rdf = rdf_raw.copy()
        rdf["date"] = pd.to_datetime(rdf["date"])
        rdf = rdf.sort_values("date").set_index("date")["rate_usd_per_feu"]
        combined = pd.DataFrame({"s": sdf, "r": rdf}).dropna()
        if len(combined) < 20:
            continue
        try:
            corr_val = float(combined["s"].corr(combined["r"]))
            correlations.append((rid, corr_val, combined))
        except Exception:
            pass

    if not correlations:
        st.info("Insufficient overlapping data for freight correlation analysis.")
        return

    correlations.sort(key=lambda x: abs(x[1]), reverse=True)
    best_rid, best_corr, best_combined = correlations[0]

    # Show top correlations table
    rows = [{"Route": r, "Pearson r": f"{c:+.3f}",
             "Strength": "Strong" if abs(c) >= 0.6 else ("Moderate" if abs(c) >= 0.35 else "Weak"),
             "Direction": "Positive" if c > 0 else "Negative"}
            for r, c, _ in correlations[:8]]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Dual-axis chart
    s_base = float(best_combined["s"].iloc[0])
    r_base = float(best_combined["r"].iloc[0])
    best_combined = best_combined.copy()
    best_combined["s_pct"] = (best_combined["s"] / s_base - 1) * 100 if s_base != 0 else 0.0
    best_combined["r_pct"] = (best_combined["r"] / r_base - 1) * 100 if r_base != 0 else 0.0

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=best_combined.index, y=best_combined["r_pct"],
        name="Freight Rate % Chg", yaxis="y",
        mode="lines", line=dict(color=C_ACCENT, width=2),
        hovertemplate="%{x|%Y-%m-%d}: %{y:+.1f}%<extra>Rate</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=best_combined.index, y=best_combined["s_pct"],
        name=f"{ticker} % Chg", yaxis="y2",
        mode="lines", line=dict(color=C_CONV, width=2, dash="dot"),
        hovertemplate=f"%{{x|%Y-%m-%d}}: %{{y:+.1f}}%<extra>{ticker}</extra>",
    ))
    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(300, dict(l=20, r=60, t=40, b=20)),
        title=dict(text=f"{ticker} vs Best-Correlated Route ({best_rid}) — r={best_corr:+.3f}",
                   font=dict(size=12, color=C_TEXT2), x=0.0),
        xaxis=dict(**_axis_style()),
        yaxis=dict(**_axis_style(), title="Rate % Chg",
                   titlefont=dict(color=C_ACCENT, size=10), ticksuffix="%"),
        yaxis2=dict(**_axis_style(), title=f"{ticker} % Chg",
                    titlefont=dict(color=C_CONV, size=10),
                    overlaying="y", side="right",
                    gridcolor="rgba(0,0,0,0)", ticksuffix="%"),
        legend=_legend_style(),
        hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12)),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"dd_stock_corr_{_uid(ticker)}")


def _render_stock_signal_analysis(ticker: str, stock_data: dict) -> None:
    df_raw = stock_data.get(ticker)
    if df_raw is None or df_raw.empty or len(df_raw) < 30:
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].fillna(method="ffill")

    current = float(close.iloc[-1])
    ma20    = float(close.rolling(20, min_periods=1).mean().iloc[-1])
    ma50    = float(close.rolling(50, min_periods=1).mean().iloc[-1])
    ma200   = float(close.rolling(200, min_periods=1).mean().iloc[-1])

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rsi   = float((100 - (100 / (1 + gain / loss.replace(0, np.nan)))).fillna(50).iloc[-1])

    log_ret = np.log(close / close.shift(1)).dropna()
    ann_vol = float(log_ret.std(ddof=1)) * math.sqrt(252) * 100 if len(log_ret) > 1 else 0.0
    momentum_30 = (current / float(close.iloc[-31]) - 1) * 100 if len(close) > 31 else 0.0

    signals = []
    # Golden / death cross
    if ma50 > ma200:
        signals.append(("Golden Cross (50 > 200 MA)", "Bullish", C_HIGH))
    else:
        signals.append(("Death Cross (50 < 200 MA)", "Bearish", C_LOW))
    # Price vs MA
    if current > ma50:
        signals.append(("Price above 50d MA", "Bullish", C_HIGH))
    else:
        signals.append(("Price below 50d MA", "Bearish", C_LOW))
    # RSI
    if rsi > 70:
        signals.append((f"RSI overbought ({rsi:.0f})", "Caution", C_MOD))
    elif rsi < 30:
        signals.append((f"RSI oversold ({rsi:.0f})", "Opportunity", C_HIGH))
    else:
        signals.append((f"RSI neutral ({rsi:.0f})", "Neutral", C_TEXT2))
    # Momentum
    m_label = "Bullish" if momentum_30 > 5 else ("Bearish" if momentum_30 < -5 else "Neutral")
    m_color = C_HIGH if momentum_30 > 5 else (C_LOW if momentum_30 < -5 else C_TEXT2)
    signals.append((f"30d Momentum ({momentum_30:+.1f}%)", m_label, m_color))

    signals_html = "".join(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:10px 14px;background:rgba(255,255,255,0.03);border-radius:8px;'
        f'margin-bottom:6px;border-left:3px solid {c}">'
        f'<span style="font-size:0.8rem;color:{C_TEXT2}">{sig}</span>'
        f'<span style="font-size:0.75rem;font-weight:700;color:{c}">{lbl}</span>'
        f'</div>'
        for sig, lbl, c in signals
    )

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown(
            f'<div style="font-size:0.62rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">Signal Dashboard</div>'
            + signals_html,
            unsafe_allow_html=True,
        )
    with c2:
        ma_fig = go.Figure()
        for ma_val, ma_name, ma_col in [
            (ma20, "20d MA", "white"), (ma50, "50d MA", C_CONV), (ma200, "200d MA", C_GOLD)
        ]:
            ma_fig.add_trace(go.Scatter(
                x=["Current"], y=[current], mode="markers",
                marker=dict(color=C_TEXT, size=12, symbol="diamond"),
                name="Price", showlegend=False,
            ))
        # Horizontal gauge bars for key levels
        levels = [
            ("Current", current, C_TEXT),
            ("20d MA",  ma20,    "white"),
            ("50d MA",  ma50,    C_CONV),
            ("200d MA", ma200,   C_GOLD),
        ]
        all_vals = [v for _, v, _ in levels]
        lo_v, hi_v = min(all_vals) * 0.97, max(all_vals) * 1.03
        ma_fig2 = go.Figure()
        for lv_name, lv_val, lv_col in levels:
            ma_fig2.add_shape(
                type="line", x0=0, x1=1, y0=lv_val, y1=lv_val,
                line=dict(color=lv_col, width=2, dash="dash" if lv_name != "Current" else "solid"),
                xref="paper",
            )
            ma_fig2.add_annotation(
                x=1.02, y=lv_val, text=f"  {lv_name}: ${lv_val:,.2f}",
                showarrow=False, xref="paper",
                font=dict(color=lv_col, size=10),
            )
        ma_fig2.update_layout(
            **_dark_layout(220, dict(l=20, r=130, t=30, b=20)),
            title=dict(text="Key Price Levels", font=dict(size=12, color=C_TEXT2), x=0.0),
            yaxis=dict(**_axis_style(), range=[lo_v, hi_v], tickformat="$,.2f"),
            xaxis=dict(visible=False),
            showlegend=False,
        )
        st.plotly_chart(ma_fig2, use_container_width=True,
                        key=f"dd_stock_levels_{_uid(ticker)}")


# ── Shared sections ────────────────────────────────────────────────────────────

def _render_forecasts(route_id: str, forecasts: list, freight_data: dict) -> None:
    fc = next((f for f in (forecasts or []) if f.route_id == route_id), None)
    if fc is None:
        st.info("No forecast available for this route (insufficient history).")
        return

    c1, c2, c3 = st.columns(3)
    for col, label, rate, color in [
        (c1, "30-Day Forecast", fc.forecast_30d, C_ACCENT),
        (c2, "60-Day Forecast", fc.forecast_60d, C_CONV),
        (c3, "90-Day Forecast", fc.forecast_90d, C_MOD),
    ]:
        pct = (rate / fc.current_rate - 1) * 100 if fc.current_rate > 0 else 0.0
        pct_color = C_HIGH if pct > 0 else C_LOW
        col.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-top:3px solid {color};border-radius:12px;padding:18px 20px;text-align:center">'
            f'<div style="font-size:0.62rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">{label}</div>'
            f'<div style="font-size:1.7rem;font-weight:800;color:{C_TEXT}">${rate:,.0f}</div>'
            f'<div style="font-size:0.78rem;color:{pct_color};margin-top:4px">'
            f'{pct:+.1f}% vs current</div>'
            f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-top:2px">USD / FEU</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:8px;margin-bottom:14px">'
        f'Methodology: {fc.methodology} &bull; '
        f'Confidence: <b style="color:{C_TEXT2}">{fc.confidence}</b> &bull; '
        f'R&#178;={fc.r_squared:.2f} &bull; {fc.data_points} data points'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Monte Carlo fan chart
    try:
        from processing.monte_carlo import simulate_freight_rates
        mc = simulate_freight_rates(freight_data, route_id, n_simulations=300, forecast_days=90)
    except Exception:
        mc = None

    if mc and mc.percentiles:
        days = list(range(len(mc.percentiles.get("p50", []))))
        p5, p25, p50 = mc.percentiles.get("p5", []), mc.percentiles.get("p25", []), mc.percentiles.get("p50", [])
        p75, p95     = mc.percentiles.get("p75", []), mc.percentiles.get("p95", [])

        if days and p50:
            fan = go.Figure()
            fan.add_trace(go.Scatter(
                x=days + days[::-1], y=p95 + p5[::-1],
                fill="toself", fillcolor=_hex_to_rgba(C_ACCENT, 0.06),
                line=dict(color="rgba(0,0,0,0)"),
                name="p5–p95 (90% CI)", hoverinfo="skip",
            ))
            fan.add_trace(go.Scatter(
                x=days + days[::-1], y=p75 + p25[::-1],
                fill="toself", fillcolor=_hex_to_rgba(C_ACCENT, 0.15),
                line=dict(color="rgba(0,0,0,0)"),
                name="p25–p75 (50% CI)", hoverinfo="skip",
            ))
            fan.add_trace(go.Scatter(
                x=days, y=p50, mode="lines",
                line=dict(color=C_ACCENT, width=2.5), name="Median (p50)",
                hovertemplate="Day %{x}: $%{y:,.0f}/FEU<extra></extra>",
            ))
            if mc.bull_case_90d and mc.bear_case_90d:
                fan.add_trace(go.Scatter(
                    x=[0, len(days) - 1], y=[mc.current_rate, mc.bull_case_90d],
                    mode="lines", line=dict(color=C_HIGH, width=1.2, dash="dot"),
                    name=f"Bull ${mc.bull_case_90d:,.0f}", hoverinfo="skip",
                ))
                fan.add_trace(go.Scatter(
                    x=[0, len(days) - 1], y=[mc.current_rate, mc.bear_case_90d],
                    mode="lines", line=dict(color=C_LOW, width=1.2, dash="dot"),
                    name=f"Bear ${mc.bear_case_90d:,.0f}", hoverinfo="skip",
                ))
            fan.update_layout(
                template="plotly_dark",
                **_dark_layout(330, dict(l=20, r=20, t=36, b=20)),
                title=dict(text=f"Monte Carlo ({mc.n_simulations} sims, GBM)",
                           font=dict(size=12, color=C_TEXT2), x=0.0),
                xaxis=dict(**_axis_style(), title="Days Forward"),
                yaxis=dict(**_axis_style(), title="Rate USD/FEU", tickformat="$,.0f"),
                legend=_legend_style(),
                hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                                font=dict(color=C_TEXT, size=12)),
            )
            st.plotly_chart(fan, use_container_width=True,
                            key=f"dd_mc_fan_{_uid(route_id)}")

            ci_lo, ci_hi = mc.confidence_interval_90d
            st.markdown(
                f'<div style="display:flex;gap:24px;font-size:0.76rem;color:{C_TEXT3};'
                f'margin-top:-6px;flex-wrap:wrap;padding-bottom:4px">'
                f'<span><b style="color:{C_HIGH}">{mc.prob_rate_increase * 100:.0f}%</b> prob rise</span>'
                f'<span><b style="color:{C_LOW}">{mc.prob_rate_decrease * 100:.0f}%</b> prob fall</span>'
                f'<span>90d 90% CI: <b style="color:{C_TEXT2}">${ci_lo:,.0f} – ${ci_hi:,.0f}</b></span>'
                f'<span>VaR 95%: <b style="color:{C_MOD}">${mc.var_95:,.0f}</b></span>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _render_correlation_matrix(
    subject_type: str,
    subject_id: str,
    freight_data: dict,
    stock_data: dict,
    macro_data: dict,
) -> None:
    """Build a cross-asset correlation matrix for the selected subject."""
    series: dict[str, pd.Series] = {}

    # Add freight routes (daily rates)
    for rid, df_raw in (freight_data or {}).items():
        if df_raw is None or df_raw.empty:
            continue
        try:
            df = df_raw.copy()
            df["date"] = pd.to_datetime(df["date"])
            s = df.sort_values("date").set_index("date")["rate_usd_per_feu"]
            if len(s) >= 20:
                series[f"Route:{rid[:12]}"] = s
        except Exception:
            pass

    # Add stocks
    for ticker, df_raw in (stock_data or {}).items():
        if df_raw is None or df_raw.empty:
            continue
        try:
            df = df_raw.copy()
            df["date"] = pd.to_datetime(df["date"])
            s = df.sort_values("date").set_index("date")["close"]
            if len(s) >= 20:
                series[f"Stock:{ticker}"] = s
        except Exception:
            pass

    # Add select macro indicators
    for mid, df_raw in (macro_data or {}).items():
        if df_raw is None or df_raw.empty:
            continue
        try:
            df = df_raw.copy()
            df["date"] = pd.to_datetime(df["date"])
            val_col = [c for c in df.columns if c not in ("date", "series_id", "source")]
            if not val_col:
                continue
            s = df.sort_values("date").set_index("date")[val_col[0]].dropna()
            if len(s) >= 20:
                series[f"Macro:{mid[:10]}"] = s
        except Exception:
            pass

    if len(series) < 2:
        st.info("Insufficient data for correlation matrix (need ≥ 2 series with ≥ 20 overlapping days).")
        return

    # Align all series to common date index and compute pct changes
    combined = pd.DataFrame(series).dropna(how="all")
    if len(combined) < 20:
        st.info("Not enough overlapping dates across all series for correlation matrix.")
        return

    pct_chg = combined.pct_change().dropna(how="all")
    corr_matrix = pct_chg.corr()
    labels = list(corr_matrix.columns)
    z = corr_matrix.values

    # Colorscale: red = -1, white = 0, green = +1
    colorscale = [
        [0.0,  "#ef4444"],
        [0.25, "#f87171"],
        [0.5,  "#1a2235"],
        [0.75, "#34d399"],
        [1.0,  "#10b981"],
    ]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=labels, y=labels,
        zmin=-1, zmax=1,
        colorscale=colorscale,
        text=[[f"{v:.2f}" for v in row] for row in z],
        texttemplate="%{text}",
        textfont=dict(size=9, color="white"),
        hovertemplate="%{y} vs %{x}: r=%{z:.3f}<extra></extra>",
        colorbar=dict(
            title=dict(text="Pearson r", font=dict(color=C_TEXT2, size=10)),
            tickfont=dict(color=C_TEXT3, size=9),
            thickness=12,
        ),
    ))
    n = len(labels)
    height = max(320, min(n * 38, 700))
    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(height, dict(l=10, r=10, t=40, b=10)),
        title=dict(text="Cross-Asset Correlation Matrix (pct-change basis)",
                   font=dict(size=12, color=C_TEXT2), x=0.0),
        xaxis=dict(tickfont=dict(color=C_TEXT3, size=9), tickangle=-35),
        yaxis=dict(tickfont=dict(color=C_TEXT3, size=9)),
    )
    st.plotly_chart(fig, use_container_width=True,
                    key=f"dd_corr_matrix_{_uid(subject_id)}")


def _render_scenario_analysis(
    subject_type: str,
    route: RouteOpportunity | None,
    freight_data: dict,
) -> None:
    """Inline what-if scenario sliders for route rate changes and congestion spikes."""
    if subject_type != "route" or route is None:
        st.info("Scenario analysis is available for Route mode only.")
        return

    current_rate = route.current_rate_usd_feu
    if current_rate <= 0:
        st.info("No current rate available for scenario modeling.")
        return

    st.markdown(
        f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-bottom:12px">'
        f'Adjust the sliders to model hypothetical market conditions and see '
        f'estimated opportunity score and rate impact.</div>',
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        rate_delta_pct = st.slider(
            "Freight Rate Change (%)", min_value=-50, max_value=100, value=0, step=5,
            key=f"dd_scenario_rate_{_uid(route.route_id)}",
        )
        congestion_delta = st.slider(
            "Congestion Spike (index +/-)", min_value=-0.5, max_value=0.5, value=0.0, step=0.05,
            key=f"dd_scenario_cong_{_uid(route.route_id)}",
        )
    with c2:
        demand_delta = st.slider(
            "Demand Shift (score +/-)", min_value=-0.3, max_value=0.3, value=0.0, step=0.05,
            key=f"dd_scenario_demand_{_uid(route.route_id)}",
        )
        macro_delta = st.slider(
            "Macro Tailwind Shift (+/-)", min_value=-0.3, max_value=0.3, value=0.0, step=0.05,
            key=f"dd_scenario_macro_{_uid(route.route_id)}",
        )

    # Scenario calculations
    scen_rate = current_rate * (1 + rate_delta_pct / 100)
    # Approximate new opportunity score components
    scen_rate_mom  = min(1.0, max(0.0, route.rate_momentum_component + rate_delta_pct / 200))
    scen_demand    = min(1.0, max(0.0, route.demand_imbalance_component + demand_delta))
    scen_cong      = min(1.0, max(0.0, route.congestion_clearance_component - congestion_delta))
    scen_macro     = min(1.0, max(0.0, route.macro_tailwind_component + macro_delta))

    w = {"rate_momentum": 0.35, "demand_imbalance": 0.30,
         "congestion_clearance": 0.20, "macro_tailwind": 0.15}
    scen_score = (
        scen_rate_mom  * w["rate_momentum"]
        + scen_demand  * w["demand_imbalance"]
        + scen_cong    * w["congestion_clearance"]
        + scen_macro   * w["macro_tailwind"]
    )
    score_delta = scen_score - route.opportunity_score
    rate_impact = scen_rate - current_rate

    scen_color   = _score_color(scen_score)
    delta_color  = C_HIGH if score_delta >= 0 else C_LOW
    impact_color = C_HIGH if rate_impact >= 0 else C_LOW

    c_base, c_scen, c_delta = st.columns(3)
    c_base.markdown(
        _stat_card("Base Score",    f"{route.opportunity_score * 100:.0f}%",
                   f"${current_rate:,.0f}/FEU", _score_color(route.opportunity_score)),
        unsafe_allow_html=True,
    )
    c_scen.markdown(
        _stat_card("Scenario Score", f"{scen_score * 100:.0f}%",
                   f"${scen_rate:,.0f}/FEU", scen_color, glow=True),
        unsafe_allow_html=True,
    )
    c_delta.markdown(
        _stat_card("Score Impact",  f"{score_delta * 100:+.1f}pp",
                   f"Rate impact ${rate_impact:+,.0f}", delta_color),
        unsafe_allow_html=True,
    )

    # Scenario interpretation
    if abs(rate_delta_pct) > 0 or abs(congestion_delta) > 0.01 or abs(demand_delta) > 0.01:
        interp_parts = []
        if rate_delta_pct > 0:
            interp_parts.append(f"a {rate_delta_pct}% freight rate increase")
        elif rate_delta_pct < 0:
            interp_parts.append(f"a {abs(rate_delta_pct)}% freight rate decline")
        if congestion_delta > 0:
            interp_parts.append(f"a congestion spike of +{congestion_delta:.2f}")
        elif congestion_delta < 0:
            interp_parts.append(f"congestion easing of {congestion_delta:.2f}")
        if demand_delta > 0:
            interp_parts.append(f"demand strengthening by +{demand_delta:.2f}")
        elif demand_delta < 0:
            interp_parts.append(f"demand softening by {demand_delta:.2f}")

        scenario_desc = ", ".join(interp_parts) if interp_parts else "these conditions"
        direction = "improve" if score_delta > 0 else "deteriorate"
        st.markdown(
            f'<div style="background:{_hex_to_rgba(scen_color, 0.08)};'
            f'border:1px solid {_hex_to_rgba(scen_color, 0.25)};'
            f'border-radius:10px;padding:14px 18px;margin-top:10px;'
            f'font-size:0.8rem;color:{C_TEXT2};line-height:1.5">'
            f'Under a scenario of {scenario_desc}, the opportunity score would '
            f'<b style="color:{scen_color}">{direction} by '
            f'{abs(score_delta * 100):.1f} percentage points</b> to '
            f'<b style="color:{scen_color}">{scen_score * 100:.0f}%</b>, '
            f'with the implied rate moving to '
            f'<b style="color:{impact_color}">${scen_rate:,.0f}/FEU</b>.'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_historical_comparison(
    subject_type: str,
    subject_id: str,
    freight_data: dict,
    stock_data: dict,
) -> None:
    """Compare current metrics vs 1-year ago and 2-years ago."""
    if subject_type == "route":
        df_raw = freight_data.get(subject_id)
        col_name = "rate_usd_per_feu"
        unit = "USD/FEU"
        fmt  = lambda v: f"${v:,.0f}"
    elif subject_type == "stock":
        df_raw = stock_data.get(subject_id)
        col_name = "close"
        unit = "USD"
        fmt  = lambda v: f"${v:,.2f}"
    else:
        st.info("Historical comparison is available for Route and Stock modes.")
        return

    if df_raw is None or df_raw.empty:
        st.info("No data available for historical comparison.")
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if col_name not in df.columns:
        st.info(f"Column '{col_name}' not found in data.")
        return

    series = df.set_index("date")[col_name].dropna()
    if series.empty:
        return

    now_val = float(series.iloc[-1])
    latest_date = series.index[-1]

    def _val_at(delta_days: int) -> float | None:
        target = latest_date - pd.Timedelta(days=delta_days)
        nearest = series[series.index <= target]
        return float(nearest.iloc[-1]) if not nearest.empty else None

    val_1y = _val_at(365)
    val_2y = _val_at(730)

    cols = st.columns(4)
    cols[0].markdown(_stat_card("Current", fmt(now_val), unit, C_ACCENT, glow=True),
                     unsafe_allow_html=True)
    if val_1y is not None:
        chg1 = (now_val / val_1y - 1) * 100
        c1 = C_HIGH if chg1 > 0 else C_LOW
        cols[1].markdown(_stat_card("1 Year Ago", fmt(val_1y), f"{chg1:+.1f}% change", c1),
                         unsafe_allow_html=True)
    else:
        cols[1].markdown(_stat_card("1 Year Ago", "N/A", "Insufficient history", C_TEXT3),
                         unsafe_allow_html=True)
    if val_2y is not None:
        chg2 = (now_val / val_2y - 1) * 100
        c2 = C_HIGH if chg2 > 0 else C_LOW
        cols[2].markdown(_stat_card("2 Years Ago", fmt(val_2y), f"{chg2:+.1f}% change", c2),
                         unsafe_allow_html=True)
    else:
        cols[2].markdown(_stat_card("2 Years Ago", "N/A", "Insufficient history", C_TEXT3),
                         unsafe_allow_html=True)

    # All-time stats
    at_mean = float(series.mean())
    at_std  = float(series.std(ddof=1)) if len(series) > 1 else 0.0
    z_score = (now_val - at_mean) / at_std if at_std > 0 else 0.0
    z_col   = C_LOW if z_score > 1.5 else (C_HIGH if z_score < -1.5 else C_MOD)
    cols[3].markdown(_stat_card("Z-Score vs History", f"{z_score:+.2f}σ",
                                f"Hist avg {fmt(at_mean)}", z_col),
                     unsafe_allow_html=True)

    # Timeline chart
    if val_1y is not None or val_2y is not None:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=series.index, y=series,
            mode="lines", line=dict(color=C_ACCENT, width=1.8),
            name=subject_id,
            hovertemplate="%{x|%Y-%m-%d}: " + ("$%{y:,.0f}" if subject_type == "route"
                                                 else "$%{y:,.2f}") + "<extra></extra>",
        ))
        # Vertical marker lines for 1y and 2y ago
        for delta, label, col_marker in [(365, "1Y ago", C_MOD), (730, "2Y ago", C_CONV)]:
            target = latest_date - pd.Timedelta(days=delta)
            if not series[series.index <= target].empty:
                fig.add_vline(x=str(target.date()), line_dash="dot",
                              line_color=col_marker, line_width=1.2,
                              annotation_text=f"  {label}",
                              annotation_position="top right",
                              annotation_font=dict(color=col_marker, size=9))
        fig.update_layout(
            template="plotly_dark",
            **_dark_layout(280, dict(l=20, r=20, t=36, b=20)),
            title=dict(text="Full Price History with Year-Ago Anchors",
                       font=dict(size=12, color=C_TEXT2), x=0.0),
            xaxis=dict(**_axis_style()),
            yaxis=dict(**_axis_style(), tickformat=("$,.0f" if subject_type == "route"
                                                    else "$,.2f")),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True,
                        key=f"dd_hist_comp_{_uid(subject_id)}")


def _render_ai_narrative(
    subject_type: str,
    route: RouteOpportunity | None,
    port: Any,
    ticker: str | None,
    freight_data: dict,
    stock_data: dict,
    insights: list,
) -> None:
    """Generate an auto-constructed analytical narrative for the selected subject."""

    paragraphs: list[str] = []

    if subject_type == "route" and route is not None:
        r = route
        score_label = r.opportunity_label
        score_color = _score_color(r.opportunity_score)
        rate_str    = f"${r.current_rate_usd_feu:,.0f}/FEU" if r.current_rate_usd_feu > 0 else "unknown rate"
        arr, _      = _trend_arrow(r.rate_trend)

        trend_stmt = (
            f"Rates are currently {r.rate_trend.lower()} {arr} with a 30-day change of "
            f"{r.rate_pct_change_30d * 100:+.1f}%."
            if r.current_rate_usd_feu > 0 else
            "Rate history is limited for this lane."
        )

        demand_level = ("strong" if r.demand_imbalance_component >= 0.65
                        else "moderate" if r.demand_imbalance_component >= 0.45 else "weak")
        cong_stmt = (
            "with origin congestion creating headwinds for capacity"
            if r.origin_congestion >= 0.65 else
            "with origin ports operating at manageable congestion levels"
        )
        macro_stmt = (
            "Macro tailwinds are supportive." if r.macro_tailwind_component >= 0.55
            else "Macro conditions are neutral to slightly negative."
        )

        # Pull in relevant insight if available
        rel_insights = [i for i in (insights or [])
                        if hasattr(i, "route_id") and i.route_id == r.route_id]
        insight_snippet = (f' The scoring engine flags: "{rel_insights[0].text}"'
                           if rel_insights else "")

        paragraphs.append(
            f"The <b>{r.route_name}</b> corridor ({r.origin_locode} → {r.dest_locode}) "
            f"currently shows a <b style='color:{score_color}'>{score_label}</b> opportunity "
            f"with a composite score of <b style='color:{score_color}'>"
            f"{r.opportunity_score * 100:.0f}%</b>. "
            f"The spot rate stands at <b>{rate_str}</b>. "
            f"{trend_stmt}"
        )
        paragraphs.append(
            f"Demand dynamics on this lane are <b>{demand_level}</b>, "
            f"{cong_stmt}. "
            f"The demand imbalance component scores "
            f"<b>{r.demand_imbalance_component * 100:.0f}%</b> while the congestion clearance "
            f"component scores <b>{r.congestion_clearance_component * 100:.0f}%</b>. "
            f"{macro_stmt}{insight_snippet}"
        )
        paragraphs.append(
            f"Rate momentum contributes <b>{r.rate_momentum_component * 100:.0f}%</b> to the "
            f"overall signal. The transit time of <b>{r.transit_days} days</b> and the FBX "
            f"index classification of <b>{r.fbx_index}</b> define the benchmark universe for "
            f"this trade lane. Operators should monitor congestion at {r.origin_locode} and "
            f"demand evolution at {r.dest_locode} for leading signals."
        )

    elif subject_type == "port" and port is not None:
        p = port
        d_color = _score_color(p.demand_score)
        c_level = "elevated" if p.congestion_index >= 0.65 else ("low" if p.congestion_index < 0.35 else "moderate")
        arr, _  = _trend_arrow(p.demand_trend)
        top_cats = ", ".join(pr.get("category", "") for pr in (p.top_products or [])[:3]) or "general cargo"

        paragraphs.append(
            f"<b>{p.port_name}</b> ({p.locode}, {p.region}) registers a "
            f"<b style='color:{d_color}'>{p.demand_label}</b> demand signal at "
            f"<b style='color:{d_color}'>{p.demand_score * 100:.0f}%</b>. "
            f"The demand trend is <b>{p.demand_trend.lower()}</b> {arr}, "
            f"driven by trade flows in {top_cats}."
        )
        paragraphs.append(
            f"Port congestion is currently <b>{c_level}</b> at an index of "
            f"<b>{p.congestion_index * 100:.0f}%</b>, with approximately "
            f"<b>{p.vessel_count}</b> cargo vessels in the port zone according to AIS data. "
            f"Annual throughput is estimated at <b>{p.throughput_teu_m:.1f}M TEU</b>. "
            f"The trade flow component scores <b>{p.trade_flow_component * 100:.0f}%</b>, "
            f"the congestion component <b>{p.congestion_component * 100:.0f}%</b>, "
            f"and the throughput component <b>{p.throughput_component * 100:.0f}%</b>."
        )
        paragraphs.append(
            f"Import value stands at approximately "
            f"<b>${p.import_value_usd / 1e9:.2f}B</b> and export value at "
            f"<b>${p.export_value_usd / 1e9:.2f}B</b> for the latest measured period. "
            f"Watch for shifts in {top_cats} trade volumes and AIS vessel counts as "
            f"leading indicators of demand turning points at this port."
        )

    elif subject_type == "stock" and ticker:
        df_raw = stock_data.get(ticker)
        if df_raw is not None and not df_raw.empty:
            df = df_raw.copy()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            close = df["close"].dropna()
            current = float(close.iloc[-1])
            ma50    = float(close.rolling(50, min_periods=1).mean().iloc[-1])
            ma200   = float(close.rolling(200, min_periods=1).mean().iloc[-1])
            log_ret = np.log(close / close.shift(1)).dropna()
            ann_vol = float(log_ret.std(ddof=1)) * math.sqrt(252) * 100 if len(log_ret) > 1 else 0.0
            trend_short = "above" if current > ma50 else "below"
            trend_long  = "above" if current > ma200 else "below"
            cross_stmt = "a bullish golden cross alignment" if ma50 > ma200 else "a bearish death cross alignment"

            paragraphs.append(
                f"<b>{ticker}</b> is currently trading at <b>${current:,.2f}</b>, "
                f"{trend_short} its 50-day moving average (${ma50:,.2f}) and "
                f"{trend_long} its 200-day moving average (${ma200:,.2f}), "
                f"indicating {cross_stmt} across key trend timeframes."
            )
            paragraphs.append(
                f"Annualised volatility stands at <b>{ann_vol:.1f}%</b>, which is "
                + ("elevated, indicating elevated market uncertainty for this shipping equity."
                   if ann_vol > 40 else
                   "moderate, consistent with typical shipping sector beta."
                   if ann_vol > 20 else
                   "low, suggesting relatively stable price action.") +
                f" With {len(df)} trading days of history, the statistical base is "
                + ("robust." if len(df) >= 252 else "developing — treat metrics with caution.")
            )
            paragraphs.append(
                f"Correlation analysis against tracked freight routes provides insight into "
                f"how closely {ticker}'s price moves with physical shipping market dynamics. "
                f"A high positive correlation suggests the equity is a reliable proxy for "
                f"freight rate exposure, useful for portfolio hedging or directional trades "
                f"when rate data lags are a concern."
            )

    if not paragraphs:
        st.info("AI narrative not available for the current selection.")
        return

    html_paras = "".join(
        f'<p style="margin-bottom:12px;line-height:1.65;color:{C_TEXT2};font-size:0.85rem">'
        f'{p}</p>'
        for p in paragraphs
    )
    st.markdown(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-left:3px solid {C_ACCENT};border-radius:12px;padding:22px 26px;'
        f'margin-top:4px">'
        f'<div style="font-size:0.62rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:14px">'
        f'AI Narrative — Auto-Generated Analysis</div>'
        + html_paras
        + f'<div style="font-size:0.65rem;color:{C_TEXT3};margin-top:8px;'
        f'padding-top:10px;border-top:1px solid {C_BORDER}">'
        f'Narrative generated from live data signals. Not financial advice.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_news_for_route(r: RouteOpportunity) -> None:
    try:
        from processing.news_feed import get_cached_news
        all_news = get_cached_news()
    except Exception:
        all_news = []

    if not all_news:
        try:
            from processing.news_feed import fetch_shipping_news
            all_news = fetch_shipping_news(max_items=60)
        except Exception:
            all_news = []

    keywords = {
        r.origin_locode.lower(), r.dest_locode.lower(),
        r.origin_region.lower(), r.dest_region.lower(),
        r.route_name.lower(),
        r.origin_locode[:2].lower(), r.dest_locode[:2].lower(),
    }

    def _relevant(item) -> bool:
        combined = (item.title + " ".join(item.keywords)).lower()
        return any(kw in combined for kw in keywords)

    relevant = [i for i in all_news if _relevant(i)]
    display  = relevant[:8] if relevant else list(all_news)[:5]

    if not display:
        st.info("No recent news available. Configure RSS feeds in processing/news_feed.py.")
        return

    if not relevant and all_news:
        st.caption(f"No news specifically matched {r.route_name} — showing latest shipping headlines.")

    for item in display:
        sentiment  = getattr(item, "sentiment_score", 0.0)
        sent_color = C_HIGH if sentiment > 0.1 else (C_LOW if sentiment < -0.1 else C_MOD)
        sent_label = "Positive" if sentiment > 0.1 else ("Negative" if sentiment < -0.1 else "Neutral")
        sent_arrow = "▲" if sentiment > 0.1 else ("▼" if sentiment < -0.1 else "→")

        try:
            pub_dt  = item.published_dt
            age_h   = int((datetime.datetime.utcnow() - pub_dt.replace(tzinfo=None)).total_seconds() / 3600)
            age_str = f"{age_h}h ago" if age_h < 48 else f"{age_h // 24}d ago"
        except Exception:
            age_str = ""

        source = getattr(item, "source", "Unknown")
        url    = getattr(item, "url", "#")
        title  = item.title

        kw_html = "".join(
            f'<span style="background:rgba(255,255,255,0.05);color:{C_TEXT3};'
            f'padding:1px 7px;border-radius:4px;font-size:0.63rem;margin-right:3px">{kw}</span>'
            for kw in (getattr(item, "keywords", []) or [])[:4]
        )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-left:3px solid {sent_color};border-radius:10px;'
            f'padding:12px 16px;margin-bottom:8px">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
            f'margin-bottom:6px">'
            f'<a href="{url}" target="_blank" style="font-size:0.85rem;font-weight:600;'
            f'color:{C_TEXT};text-decoration:none;flex:1;margin-right:12px;line-height:1.35">'
            f'{title}</a>'
            f'<div style="text-align:right;flex-shrink:0">'
            f'<span style="font-size:0.7rem;color:{sent_color};font-weight:700">'
            f'{sent_arrow} {sent_label}</span>'
            f'<div style="font-size:0.63rem;color:{C_TEXT3}">{sentiment:+.2f}</div>'
            f'</div>'
            f'</div>'
            f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
            f'<span style="font-size:0.67rem;color:{C_TEXT3};font-weight:600">{source}</span>'
            f'<span style="font-size:0.63rem;color:{C_TEXT3}">{age_str}</span>'
            f'{kw_html}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Main render ────────────────────────────────────────────────────────────────

def render(
    route_results: list[RouteOpportunity],
    freight_data: dict,
    port_results: list,
    macro_data: dict,
    stock_data: dict,
    forecasts: list,
    insights: list,
) -> None:
    """Render the Deep Dive Analysis tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Scored routes from routes.optimizer (sorted by score desc).
    freight_data : dict
        route_id -> DataFrame[date, rate_usd_per_feu, source].
    port_results : list
        PortDemandResult objects from ports.demand_analyzer.
    macro_data : dict
        series_id -> DataFrame from data.fred_feed.
    stock_data : dict
        ticker -> DataFrame[date, open, high, low, close, volume].
    forecasts : list
        RateForecast objects from processing.forecaster.
    insights : list
        Insight objects from engine.scorer.
    """
    if not route_results and not port_results and not stock_data:
        st.info("No data available. Check API credentials and click Refresh.")
        return

    # ── Section 0: Subject Selector ───────────────────────────────────────────
    subject_type, subject_obj = _render_subject_selector(
        route_results or [], port_results or [], stock_data or {}
    )

    if subject_obj is None:
        return

    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)

    # Resolve IDs for each mode
    route_obj  = subject_obj if subject_type == "route" else None
    port_obj   = subject_obj if subject_type == "port"  else None
    ticker_str = subject_obj if subject_type == "stock" else None

    subject_id = (
        route_obj.route_id if route_obj else
        port_obj.locode    if port_obj  else
        ticker_str         if ticker_str else "unknown"
    )

    # ── Section 1: Hero Card ──────────────────────────────────────────────────
    if subject_type == "route" and route_obj:
        _render_route_hero(route_obj)
    elif subject_type == "port" and port_obj:
        _render_port_hero(port_obj)
    elif subject_type == "stock" and ticker_str:
        _render_stock_hero(ticker_str, stock_data)

    # ── Section 2: Price / Rate Chart + Technical Indicators ─────────────────
    _divider("Price History — Technical Analysis")
    if subject_type == "route" and route_obj:
        _render_route_rate_chart(route_obj.route_id, freight_data, route_obj.current_rate_usd_feu)
    elif subject_type == "stock" and ticker_str:
        _render_stock_price_chart(ticker_str, stock_data)
    elif subject_type == "port":
        st.info("Price chart not applicable for port mode — see trade flow breakdown below.")

    # ── Section 3: Statistical Summary ───────────────────────────────────────
    if subject_type == "route" and route_obj:
        _divider("Statistical Summary")
        _render_route_stats(route_obj.route_id, freight_data)
    elif subject_type == "stock" and ticker_str:
        _divider("Signal Analysis")
        _render_stock_signal_analysis(ticker_str, stock_data)

    # ── Section 4: Port / Trade / Fundamentals ───────────────────────────────
    if subject_type == "route" and route_obj:
        _divider("Port Deep Dive")
        _render_route_port_cards(route_obj, port_results)
    elif subject_type == "port" and port_obj:
        _divider("Trade Flow Breakdown")
        _render_port_trade_breakdown(port_obj)
        _divider("Connected Routes")
        _render_port_connections(port_obj, route_results)
    elif subject_type == "stock" and ticker_str:
        _divider("Freight Correlation Analysis")
        _render_stock_freight_correlation(ticker_str, stock_data, freight_data)

    # ── Section 5: Forecasts (route mode only) ────────────────────────────────
    if subject_type == "route" and route_obj:
        _divider("Rate Forecasts + Monte Carlo")
        _render_forecasts(route_obj.route_id, forecasts, freight_data)

    # ── Section 6: Multi-Factor Correlation Matrix ────────────────────────────
    _divider("Multi-Factor Correlation Matrix")
    _render_correlation_matrix(subject_type, subject_id, freight_data, stock_data, macro_data)

    # ── Section 7: Scenario Analysis ─────────────────────────────────────────
    _divider("Scenario Analysis — What-If Modeling")
    _render_scenario_analysis(subject_type, route_obj, freight_data)

    # ── Section 8: Historical Comparison ─────────────────────────────────────
    _divider("Historical Comparison — Current vs 1Y vs 2Y Ago")
    _render_historical_comparison(subject_type, subject_id, freight_data, stock_data)

    # ── Section 9: AI Narrative ───────────────────────────────────────────────
    _divider("AI Narrative")
    _render_ai_narrative(
        subject_type, route_obj, port_obj, ticker_str,
        freight_data, stock_data, insights,
    )

    # ── Section 10: News & Sentiment (route mode) ─────────────────────────────
    if subject_type == "route" and route_obj:
        _divider("News & Sentiment")
        _render_news_for_route(route_obj)

    # ── Footer ────────────────────────────────────────────────────────────────
    label = (
        route_obj.route_name if route_obj else
        (port_obj.port_name  if port_obj  else ticker_str)
    )
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(
        f'<div style="margin-top:36px;padding-top:16px;border-top:1px solid {C_BORDER};'
        f'font-size:0.68rem;color:{C_TEXT3};text-align:center;'
        f'display:flex;justify-content:center;gap:20px;flex-wrap:wrap">'
        f'<span>Deep Dive Analysis</span>'
        f'<span>&bull;</span>'
        f'<span>{label}</span>'
        f'<span>&bull;</span>'
        f'<span>{ts}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
