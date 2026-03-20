"""
Deep Dive Tab — Bloomberg-Terminal-level single-route analysis.

Sections
--------
0. Route Selector
1. Route Identity Card  (hero)
2. Rate History + Technical Indicators  (Plotly make_subplots)
3. Statistical Summary Panel  (5 columns)
4. Port Deep Dive  (2 columns)
5. Forecasts + Monte Carlo fan chart
6. Correlated Assets
7. News & Sentiment

Integration in app.py
----------------------
    # After all other tab definitions:
    with tab_deep_dive:
        from ui import tab_deep_dive as _dd
        _dd.render(
            route_results=route_results,
            freight_data=freight_data,
            port_results=port_results,
            macro_data=macro_data,
            stock_data=stock_data,
            forecasts=forecasts,
            insights=insights,
        )
"""
from __future__ import annotations

import datetime
import math
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from routes.optimizer import RouteOpportunity
from routes.route_registry import ROUTES


# ── Colour palette (mirrors ui/styles.py) ─────────────────────────────────────

C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _score_color(score: float) -> str:
    if score >= 0.65:
        return C_HIGH
    if score >= 0.45:
        return C_MOD
    return C_LOW


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;margin:28px 0">'
        f'<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        f'<span style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.12em">{label}</span>'
        f'<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _mini_bar(label: str, val: float, color: str) -> str:
    pct = val * 100
    return (
        f'<div style="margin-bottom:6px">'
        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
        f'<span style="font-size:0.68rem;color:{C_TEXT3};font-weight:500">{label}</span>'
        f'<span style="font-size:0.68rem;color:{color};font-weight:700">{pct:.0f}%</span>'
        f'</div>'
        f'<div style="background:rgba(255,255,255,0.06);border-radius:3px;height:5px;overflow:hidden">'
        f'<div style="background:{color};width:{min(pct, 100):.0f}%;height:100%;border-radius:3px"></div>'
        f'</div>'
        f'</div>'
    )


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _dark_layout_base(height: int = 400) -> dict:
    return dict(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=12),
        height=height,
        margin=dict(l=20, r=20, t=40, b=20),
        hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12)),
    )


# ── Section 0: Route Selector ─────────────────────────────────────────────────

def _render_route_selector(
    route_results: list[RouteOpportunity],
) -> RouteOpportunity:
    """Prominent selectbox grouped by region; defaults to highest-opportunity route."""

    # Build option list sorted by score descending (already sorted in route_results)
    option_labels: list[str] = []
    label_to_route: dict[str, RouteOpportunity] = {}

    for r in route_results:
        score_pct = f"{r.opportunity_score * 100:.0f}%"
        label = f"{r.route_name}  ({r.origin_locode} → {r.dest_locode})  [{score_pct}]"
        option_labels.append(label)
        label_to_route[label] = r

    st.markdown(
        f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">'
        f'Select Route for Deep Dive</div>',
        unsafe_allow_html=True,
    )
    # Clamp the default index so it always points to a valid entry (handles
    # session-state drift when the route list changes between reruns).
    default_index = 0
    saved = st.session_state.get("deep_dive_route_select")
    if saved in option_labels:
        default_index = option_labels.index(saved)

    selected_label = st.selectbox(
        "Route",
        options=option_labels,
        index=default_index,
        label_visibility="collapsed",
        key="deep_dive_route_select",
    )
    # Fall back to the first route if the selected label is somehow missing
    return label_to_route.get(selected_label, label_to_route[option_labels[0]])


# ── Section 1: Route Identity Card ────────────────────────────────────────────

def _render_identity_card(r: RouteOpportunity) -> None:
    score_color = _score_color(r.opportunity_score)
    score_pct   = f"{r.opportunity_score * 100:.0f}"

    trend_arrow = {"Rising": "▲", "Falling": "▼", "Stable": "→"}.get(r.rate_trend, "→")
    trend_color = {"Rising": C_HIGH, "Falling": C_LOW, "Stable": C_MOD}.get(r.rate_trend, C_MOD)

    rate_str  = f"${r.current_rate_usd_feu:,.0f}" if r.current_rate_usd_feu > 0 else "N/A"
    pct_30_str = f"{r.rate_pct_change_30d * 100:+.1f}% (30d)" if r.current_rate_usd_feu > 0 else ""

    updated_mins = 0  # placeholder — generated_at is ISO; compute if parseable
    try:
        gen = datetime.datetime.fromisoformat(r.generated_at.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        updated_mins = max(0, int((now - gen).total_seconds() / 60))
    except Exception:
        updated_mins = 0

    sub_bars = (
        _mini_bar("Rate Momentum",     r.rate_momentum_component,          C_ACCENT)
        + _mini_bar("Demand Imbalance",  r.demand_imbalance_component,     C_HIGH)
        + _mini_bar("Congestion Clear.", r.congestion_clearance_component,  C_MOD)
        + _mini_bar("Macro Tailwind",    r.macro_tailwind_component,        C_CONV)
    )

    card = (
        f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
        f'border:1px solid {_hex_to_rgba(score_color, 0.4)};'
        f'border-top:3px solid {score_color};'
        f'border-radius:14px;padding:28px 32px;margin-bottom:20px;'
        f'box-shadow:0 0 40px {_hex_to_rgba(score_color, 0.1)}">'

        # Row 1: route name + FBX badge
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:18px">'
        f'  <div>'
        f'    <div style="font-size:1.8rem;font-weight:800;color:{C_TEXT};line-height:1.1;letter-spacing:-0.02em">'
        f'      {r.route_name}'
        f'    </div>'
        f'    <div style="display:flex;align-items:center;gap:10px;margin-top:8px">'
        f'      <span style="font-size:1.05rem;color:{C_TEXT2}">'
        f'        {r.origin_locode}'
        f'      </span>'
        f'      <span style="font-size:1.2rem;color:{score_color};font-weight:700">&#8594;</span>'
        f'      <span style="font-size:1.05rem;color:{C_TEXT2}">'
        f'        {r.dest_locode}'
        f'      </span>'
        f'      <span style="font-size:0.78rem;color:{C_TEXT3};'
        f'        background:rgba(255,255,255,0.04);padding:2px 8px;border-radius:6px;'
        f'        border:1px solid {C_BORDER}">'
        f'        {r.transit_days}d transit'
        f'      </span>'
        f'    </div>'
        f'  </div>'
        f'  <div style="text-align:right">'
        f'    <span style="background:{_hex_to_rgba(C_ACCENT, 0.15)};color:{C_ACCENT};'
        f'      border:1px solid {_hex_to_rgba(C_ACCENT, 0.35)};'
        f'      padding:4px 14px;border-radius:999px;font-size:0.78rem;font-weight:700">'
        f'      {r.fbx_index}'
        f'    </span>'
        f'  </div>'
        f'</div>'

        # Row 2: Big score + rate + trend badge + sub-bars
        f'<div style="display:grid;grid-template-columns:auto 1fr 1fr;gap:32px;align-items:start">'

        # Opportunity score
        f'<div style="text-align:center">'
        f'  <div style="font-size:3rem;font-weight:900;color:{score_color};line-height:1;'
        f'    text-shadow:0 0 30px {_hex_to_rgba(score_color, 0.4)}">{score_pct}%</div>'
        f'  <div style="font-size:0.68rem;color:{C_TEXT3};text-transform:uppercase;'
        f'    letter-spacing:0.1em;margin-top:4px">Opportunity Score</div>'
        f'</div>'

        # Rate block
        f'<div style="border-left:1px solid {C_BORDER};padding-left:24px">'
        f'  <div style="font-size:0.68rem;color:{C_TEXT3};text-transform:uppercase;'
        f'    letter-spacing:0.08em;margin-bottom:4px">Current Rate</div>'
        f'  <div style="font-size:1.7rem;font-weight:800;color:{C_TEXT};line-height:1">{rate_str}</div>'
        f'  <div style="font-size:0.78rem;color:{C_TEXT3};margin-top:2px">USD / FEU</div>'
        f'  <div style="margin-top:10px">'
        f'    <span style="background:{_hex_to_rgba(trend_color, 0.12)};color:{trend_color};'
        f'      border:1px solid {_hex_to_rgba(trend_color, 0.3)};'
        f'      padding:3px 12px;border-radius:999px;font-size:0.75rem;font-weight:700">'
        f'      {trend_arrow} {r.rate_trend}  {pct_30_str}'
        f'    </span>'
        f'  </div>'
        f'</div>'

        # Sub-score bars
        f'<div style="border-left:1px solid {C_BORDER};padding-left:24px">'
        f'  <div style="font-size:0.68rem;color:{C_TEXT3};text-transform:uppercase;'
        f'    letter-spacing:0.08em;margin-bottom:8px">Signal Breakdown</div>'
        f'  {sub_bars}'
        f'</div>'
        f'</div>'  # end grid

        # Footer: updated timestamp
        f'<div style="margin-top:16px;padding-top:12px;border-top:1px solid {C_BORDER};'
        f'font-size:0.72rem;color:{C_TEXT3}">'
        f'  Updated: {updated_mins} min ago'
        f'</div>'

        f'</div>'  # end card
    )

    st.markdown(card, unsafe_allow_html=True)


# ── Section 2: Rate History with Technical Indicators ────────────────────────

def _compute_ohlc_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily freight rate data to weekly OHLC."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    rate = df["rate_usd_per_feu"]
    ohlc = rate.resample("W").agg(open="first", high="max", low="min", close="last")
    return ohlc.dropna()


def _render_rate_chart(route_id: str, freight_data: dict, current_rate: float) -> None:
    df_raw = freight_data.get(route_id)
    if df_raw is None or df_raw.empty or len(df_raw) < 5:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:32px;text-align:center;color:{C_TEXT3};font-size:0.88rem">'
            f'Insufficient rate history for this route.</div>',
            unsafe_allow_html=True,
        )
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    rates = df["rate_usd_per_feu"]

    # Moving averages
    ma7  = rates.rolling(7,  min_periods=1).mean()
    ma30 = rates.rolling(30, min_periods=1).mean()
    ma90 = rates.rolling(90, min_periods=1).mean()

    # Bollinger bands (20-day, 2 sigma)
    bb_mid   = rates.rolling(20, min_periods=1).mean()
    bb_std   = rates.rolling(20, min_periods=1).std(ddof=0).fillna(0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # Volatility proxy: rolling 7d std dev
    vol_7d   = rates.rolling(7, min_periods=1).std(ddof=0).fillna(0)
    vol_color = [C_HIGH if r >= m else C_LOW for r, m in zip(rates, ma30)]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.04,
        subplot_titles=["Freight Rate (USD/FEU)", "Volatility Proxy (7d Std Dev)"],
    )

    # ── Row 1: rate traces ──

    # Bollinger band fill
    fig.add_trace(go.Scatter(
        x=pd.concat([df["date"], df["date"][::-1]]),
        y=pd.concat([bb_upper, bb_lower[::-1]]),
        fill="toself",
        fillcolor="rgba(59,130,246,0.07)",
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=True,
        name="Bollinger Band (20d, 2σ)",
        hoverinfo="skip",
    ), row=1, col=1)

    # Determine if we have enough data for OHLC
    n_weeks = len(df) // 7
    use_ohlc = n_weeks >= 4

    if use_ohlc:
        ohlc = _compute_ohlc_weekly(df)
        fig.add_trace(go.Candlestick(
            x=ohlc.index,
            open=ohlc["open"],
            high=ohlc["high"],
            low=ohlc["low"],
            close=ohlc["close"],
            name="Rate (Weekly OHLC)",
            increasing=dict(line=dict(color=C_HIGH), fillcolor=_hex_to_rgba(C_HIGH, 0.4)),
            decreasing=dict(line=dict(color=C_LOW),  fillcolor=_hex_to_rgba(C_LOW,  0.4)),
            showlegend=True,
        ), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(
            x=df["date"], y=rates,
            mode="lines",
            line=dict(color=C_ACCENT, width=2),
            name="Rate (daily)",
            hovertemplate="%{x|%Y-%m-%d}: $%{y:,.0f}/FEU<extra></extra>",
        ), row=1, col=1)

    # MA lines
    fig.add_trace(go.Scatter(
        x=df["date"], y=ma7,
        mode="lines", line=dict(color="white", width=1.2, dash="dash"),
        name="7d MA", opacity=0.7,
        hovertemplate="7d MA: $%{y:,.0f}<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["date"], y=ma30,
        mode="lines", line=dict(color=C_ACCENT, width=1.2, dash="dash"),
        name="30d MA", opacity=0.8,
        hovertemplate="30d MA: $%{y:,.0f}<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df["date"], y=ma90,
        mode="lines", line=dict(color=C_MOD, width=1.2, dash="dash"),
        name="90d MA", opacity=0.8,
        hovertemplate="90d MA: $%{y:,.0f}<extra></extra>",
    ), row=1, col=1)

    # Current rate horizontal line
    if current_rate > 0:
        fig.add_hline(
            y=current_rate,
            line_dash="dot", line_color=C_HIGH, line_width=1.2,
            annotation_text=f"  Current ${current_rate:,.0f}",
            annotation_position="right",
            annotation_font=dict(color=C_HIGH, size=10),
            row=1, col=1,
        )

    # ── Row 2: volatility bars ──
    fig.add_trace(go.Bar(
        x=df["date"], y=vol_7d,
        marker_color=vol_color,
        name="Volatility (7d σ)",
        opacity=0.75,
        hovertemplate="%{x|%Y-%m-%d}: σ=%{y:,.0f}<extra></extra>",
    ), row=2, col=1)

    # Layout
    shared_axis = dict(
        gridcolor="rgba(255,255,255,0.05)",
        zerolinecolor="rgba(255,255,255,0.08)",
        tickfont=dict(color=C_TEXT3, size=10),
        linecolor="rgba(255,255,255,0.1)",
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
        height=500,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(255,255,255,0.08)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right",  x=1,
        ),
        hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12)),
        xaxis_rangeslider_visible=False,
    )

    for axis_key in ["xaxis", "xaxis2", "yaxis", "yaxis2"]:
        fig.update_layout(**{axis_key: shared_axis})

    fig.update_layout(
        yaxis_title="USD / FEU",
        yaxis2_title="Std Dev",
    )

    st.plotly_chart(fig, use_container_width=True, key=f"deep_dive_rate_chart_{route_id}")


# ── Section 3: Statistical Summary ───────────────────────────────────────────

def _render_stats_panel(route_id: str, freight_data: dict) -> None:
    df_raw = freight_data.get(route_id)
    if df_raw is None or df_raw.empty or len(df_raw) < 5:
        st.info("Insufficient data for statistical summary.")
        return

    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    rates = df["rate_usd_per_feu"].dropna()

    current = float(rates.iloc[-1])

    # 52-week window
    cutoff_52w = df["date"].max() - pd.Timedelta(weeks=52)
    rates_52w  = rates[df["date"] >= cutoff_52w]
    hi_52  = float(rates_52w.max()) if len(rates_52w) else current
    lo_52  = float(rates_52w.min()) if len(rates_52w) else current

    # 90-day average
    cutoff_90d = df["date"].max() - pd.Timedelta(days=90)
    rates_90d  = rates[df["date"] >= cutoff_90d]
    avg_90d    = float(rates_90d.mean()) if len(rates_90d) else current
    vs_90d_pct = (current / avg_90d - 1) * 100 if avg_90d > 0 else 0.0

    # Annualised volatility (daily log returns, *sqrt(252))
    log_ret   = np.log(rates / rates.shift(1)).dropna()
    daily_vol = float(log_ret.std(ddof=1)) if len(log_ret) > 1 else 0.0
    ann_vol   = daily_vol * math.sqrt(252) * 100  # percent

    # Z-score from historical mean
    hist_mean = float(rates.mean())
    hist_std  = float(rates.std(ddof=1)) if len(rates) > 1 else 1.0
    z_score   = (current - hist_mean) / hist_std if hist_std > 0 else 0.0

    # Days since local high / low (last 90d)
    idx_high = int(rates_90d.idxmax()) if len(rates_90d) else len(df) - 1
    idx_low  = int(rates_90d.idxmin()) if len(rates_90d) else len(df) - 1
    days_since_high = (df["date"].max() - df.loc[idx_high, "date"]).days
    days_since_low  = (df["date"].max() - df.loc[idx_low,  "date"]).days

    def _stat_card(label: str, value: str, sub: str = "", color: str = C_ACCENT) -> str:
        sub_html = (
            f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-top:3px">{sub}</div>'
            if sub else ""
        )
        return (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-top:2px solid {color};border-radius:10px;padding:14px 16px;text-align:center">'
            f'<div style="font-size:0.65rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:1.35rem;font-weight:800;color:{C_TEXT};line-height:1">{value}</div>'
            f'{sub_html}'
            f'</div>'
        )

    hi_color  = C_LOW   if current >= hi_52 * 0.95 else C_TEXT
    lo_color  = C_HIGH  if current <= lo_52 * 1.05 else C_TEXT
    pct_color = C_HIGH  if vs_90d_pct > 0 else C_LOW
    z_color   = C_LOW   if z_score > 1.5 else (C_HIGH if z_score < -1.5 else C_MOD)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_stat_card("52-Wk High", f"${hi_52:,.0f}", "USD/FEU", hi_color),  unsafe_allow_html=True)
    c2.markdown(_stat_card("52-Wk Low",  f"${lo_52:,.0f}", "USD/FEU", lo_color),  unsafe_allow_html=True)
    c3.markdown(_stat_card("vs 90d Avg", f"{vs_90d_pct:+.1f}%", f"Avg ${avg_90d:,.0f}", pct_color), unsafe_allow_html=True)
    c4.markdown(_stat_card("Ann. Volatility", f"{ann_vol:.1f}%", "log-return basis", C_CONV),   unsafe_allow_html=True)
    c5.markdown(_stat_card("Z-Score", f"{z_score:+.2f}", f"{days_since_high}d since 90d high", z_color), unsafe_allow_html=True)


# ── Section 4: Port Deep Dive ─────────────────────────────────────────────────

def _port_card(locode: str, side: str, port_results: list) -> None:
    """Render one port detail card (origin or destination)."""
    result = next((p for p in (port_results or []) if p.locode == locode), None)

    no_data = result is None
    demand_score   = result.demand_score   if result else 0.5
    vessel_count   = result.vessel_count   if result else 0
    congestion_idx = result.congestion_index if result else 0.5
    port_name      = result.port_name      if result else locode
    region         = result.region         if result else "Unknown"
    products       = (result.top_products or [])[:3] if result else []
    demand_trend   = result.demand_trend   if result else "Unknown"

    demand_color = C_HIGH if demand_score >= 0.65 else (C_LOW if demand_score < 0.40 else C_MOD)
    cong_label = (
        "Low"      if congestion_idx < 0.35 else
        "Moderate" if congestion_idx < 0.65 else
        "High"
    )
    cong_color = C_HIGH if congestion_idx < 0.35 else (C_LOW if congestion_idx >= 0.65 else C_MOD)
    trend_arrow = {"Rising": "▲", "Falling": "▼", "Stable": "→"}.get(demand_trend, "→")
    trend_color = {"Rising": C_HIGH, "Falling": C_LOW, "Stable": C_MOD}.get(demand_trend, C_MOD)

    # Small donut gauge for demand score
    gauge_fig = go.Figure(go.Pie(
        values=[demand_score, max(0.0, 1.0 - demand_score)],
        hole=0.72,
        marker=dict(colors=[demand_color, "rgba(255,255,255,0.04)"]),
        showlegend=False,
        textinfo="none",
        hoverinfo="none",
    ))
    gauge_fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        height=110,
        margin=dict(l=0, r=0, t=0, b=0),
        annotations=[dict(
            text=f"<b>{demand_score * 100:.0f}%</b>",
            x=0.5, y=0.5,
            font=dict(size=16, color=demand_color, family="Inter"),
            showarrow=False,
        )],
    )

    # Product pills
    if products:
        pills_html = "".join(
            f'<span style="background:{_hex_to_rgba(p.get("color", C_ACCENT), 0.15)};'
            f'color:{p.get("color", C_ACCENT)};'
            f'border:1px solid {_hex_to_rgba(p.get("color", C_ACCENT), 0.3)};'
            f'padding:2px 9px;border-radius:999px;font-size:0.68rem;font-weight:600;'
            f'margin:2px">{p.get("category", "")}</span>'
            for p in products
        )
    else:
        pills_html = f'<span style="color:{C_TEXT3};font-size:0.78rem">No product data</span>'

    st.markdown(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
        f'padding:20px 22px;height:100%">'
        f'<div style="font-size:0.65rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">{side} Port</div>'
        f'<div style="font-size:1.1rem;font-weight:700;color:{C_TEXT};margin-bottom:2px">{port_name}</div>'
        f'<div style="font-size:0.78rem;color:{C_TEXT3};margin-bottom:12px">'
        f'  {locode} &bull; {region}'
        f'</div>'
        f'<div style="font-size:0.68rem;font-weight:600;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:4px">Demand Score</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(gauge_fig, use_container_width=True, key=f"port_gauge_{locode}_{side}")

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">'
        f'  <span style="font-size:0.78rem;color:{trend_color}">'
        f'    {trend_arrow} {demand_trend}</span>'
        f'</div>'
        f'<div style="font-size:0.68rem;font-weight:600;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:6px">Top Products</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:14px">'
        f'  {pills_html}'
        f'</div>'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">'
        f'  <div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px">'
        f'    <div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
        f'      letter-spacing:0.07em;margin-bottom:2px">Congestion</div>'
        f'    <div style="font-size:0.95rem;font-weight:700;color:{cong_color}">{cong_label}</div>'
        f'    <div style="font-size:0.68rem;color:{C_TEXT3}">{congestion_idx * 100:.0f}% index</div>'
        f'  </div>'
        f'  <div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px">'
        f'    <div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
        f'      letter-spacing:0.07em;margin-bottom:2px">AIS Vessels</div>'
        f'    <div style="font-size:0.95rem;font-weight:700;color:{C_TEXT}">{vessel_count}</div>'
        f'    <div style="font-size:0.68rem;color:{C_TEXT3}">cargo in zone</div>'
        f'  </div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if no_data:
        st.markdown(
            f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:6px;'
            f'padding:6px 10px;background:rgba(255,255,255,0.03);'
            f'border-radius:6px;border:1px solid {C_BORDER}">'
            f'No deep-dive data available for {locode}. '
            f'Values shown are defaults only.</div>',
            unsafe_allow_html=True,
        )


def _render_port_deep_dive(r: RouteOpportunity, port_results: list) -> None:
    col_orig, col_dest = st.columns(2)
    with col_orig:
        _port_card(r.origin_locode, "Origin", port_results)
    with col_dest:
        _port_card(r.dest_locode, "Destination", port_results)


# ── Section 5: Forecasts ─────────────────────────────────────────────────────

def _render_forecasts(route_id: str, forecasts: list, freight_data: dict) -> None:
    fc = next((f for f in (forecasts or []) if f.route_id == route_id), None)

    if fc is None:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:24px;text-align:center;color:{C_TEXT3};font-size:0.88rem">'
            f'No forecast available for this route (insufficient history).</div>',
            unsafe_allow_html=True,
        )
        return

    # Three metric cards: 30d / 60d / 90d
    def _fc_card(label: str, rate: float, ref: float, color: str) -> str:
        pct  = (rate / ref - 1) * 100 if ref > 0 else 0.0
        sign = "+" if pct >= 0 else ""
        pct_color = C_HIGH if pct > 0 else C_LOW
        return (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-top:3px solid {color};border-radius:10px;padding:18px 20px;text-align:center">'
            f'<div style="font-size:0.68rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">{label}</div>'
            f'<div style="font-size:1.6rem;font-weight:800;color:{C_TEXT}">${rate:,.0f}</div>'
            f'<div style="font-size:0.78rem;color:{pct_color};margin-top:3px">'
            f'{sign}{pct:.1f}% vs current</div>'
            f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-top:2px">USD / FEU</div>'
            f'</div>'
        )

    c1, c2, c3 = st.columns(3)
    c1.markdown(_fc_card("30-Day Forecast",  fc.forecast_30d, fc.current_rate, C_ACCENT), unsafe_allow_html=True)
    c2.markdown(_fc_card("60-Day Forecast",  fc.forecast_60d, fc.current_rate, C_CONV),  unsafe_allow_html=True)
    c3.markdown(_fc_card("90-Day Forecast",  fc.forecast_90d, fc.current_rate, C_MOD),   unsafe_allow_html=True)

    st.markdown(
        f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-top:8px;margin-bottom:16px">'
        f'Methodology: {fc.methodology} &bull; '
        f'Confidence: <b style="color:{C_TEXT2}">{fc.confidence}</b> &bull; '
        f'R&#178;={fc.r_squared:.2f} &bull; {fc.data_points} data points'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Monte Carlo fan chart if available
    try:
        from processing.monte_carlo import simulate_freight_rates
        mc = simulate_freight_rates(freight_data, route_id, n_simulations=300, forecast_days=90)
    except Exception:
        mc = None

    if mc and mc.percentiles:
        days = list(range(len(mc.percentiles.get("p50", []))))
        p5   = mc.percentiles.get("p5",  [])
        p25  = mc.percentiles.get("p25", [])
        p50  = mc.percentiles.get("p50", [])
        p75  = mc.percentiles.get("p75", [])
        p95  = mc.percentiles.get("p95", [])

        if days and p50:
            fan_fig = go.Figure()

            # p5–p95 outer band
            fan_fig.add_trace(go.Scatter(
                x=days + days[::-1],
                y=p95 + p5[::-1],
                fill="toself",
                fillcolor=_hex_to_rgba(C_ACCENT, 0.06),
                line=dict(color="rgba(0,0,0,0)"),
                name="p5–p95 (90% CI)",
                showlegend=True,
                hoverinfo="skip",
            ))

            # p25–p75 inner band
            fan_fig.add_trace(go.Scatter(
                x=days + days[::-1],
                y=p75 + p25[::-1],
                fill="toself",
                fillcolor=_hex_to_rgba(C_ACCENT, 0.14),
                line=dict(color="rgba(0,0,0,0)"),
                name="p25–p75 (50% CI)",
                showlegend=True,
                hoverinfo="skip",
            ))

            # Median path
            fan_fig.add_trace(go.Scatter(
                x=days, y=p50,
                mode="lines",
                line=dict(color=C_ACCENT, width=2),
                name="Median (p50)",
                hovertemplate="Day %{x}: $%{y:,.0f}/FEU<extra></extra>",
            ))

            # Bull / bear lines
            if mc.bull_case_90d and mc.bear_case_90d:
                fan_fig.add_trace(go.Scatter(
                    x=[0, len(days) - 1], y=[mc.current_rate, mc.bull_case_90d],
                    mode="lines",
                    line=dict(color=C_HIGH, width=1, dash="dot"),
                    name=f"Bull (p90) ${mc.bull_case_90d:,.0f}",
                    hoverinfo="skip",
                ))
                fan_fig.add_trace(go.Scatter(
                    x=[0, len(days) - 1], y=[mc.current_rate, mc.bear_case_90d],
                    mode="lines",
                    line=dict(color=C_LOW, width=1, dash="dot"),
                    name=f"Bear (p10) ${mc.bear_case_90d:,.0f}",
                    hoverinfo="skip",
                ))

            fan_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor=C_BG,
                plot_bgcolor=C_SURFACE,
                font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
                height=320,
                margin=dict(l=20, r=20, t=30, b=20),
                title=dict(
                    text=f"Monte Carlo Fan Chart ({mc.n_simulations} simulations, GBM)",
                    font=dict(size=13, color=C_TEXT2),
                    x=0.01,
                ),
                xaxis=dict(
                    title="Days Forward",
                    gridcolor="rgba(255,255,255,0.05)",
                    tickfont=dict(color=C_TEXT3, size=10),
                ),
                yaxis=dict(
                    title="Rate USD/FEU",
                    gridcolor="rgba(255,255,255,0.05)",
                    tickfont=dict(color=C_TEXT3, size=10),
                    tickformat="$,.0f",
                ),
                legend=dict(
                    font=dict(color=C_TEXT2, size=10),
                    bgcolor="rgba(0,0,0,0)",
                    orientation="h",
                    yanchor="bottom", y=1.02,
                    xanchor="right",  x=1,
                ),
                hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                                font=dict(color=C_TEXT, size=12)),
            )

            st.plotly_chart(fan_fig, use_container_width=True, key=f"deep_dive_mc_fan_{route_id}")

            # Probability stats below chart
            prob_up = mc.prob_rate_increase * 100
            prob_dn = mc.prob_rate_decrease * 100
            ci_lo, ci_hi = mc.confidence_interval_90d
            st.markdown(
                f'<div style="display:flex;gap:24px;font-size:0.78rem;color:{C_TEXT3};'
                f'margin-top:-8px;flex-wrap:wrap">'
                f'  <span><b style="color:{C_HIGH}">{prob_up:.0f}%</b> prob. rate rises</span>'
                f'  <span><b style="color:{C_LOW}">{prob_dn:.0f}%</b> prob. rate falls</span>'
                f'  <span>90d 90% CI: '
                f'    <b style="color:{C_TEXT2}">${ci_lo:,.0f} – ${ci_hi:,.0f}</b>'
                f'  </span>'
                f'  <span>VaR 95%: <b style="color:{C_MOD}">${mc.var_95:,.0f}</b></span>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── Section 6: Correlated Assets ─────────────────────────────────────────────

def _render_correlated_assets(
    r: RouteOpportunity,
    insights: list,
    stock_data: dict,
    freight_data: dict,
) -> None:
    # Extract correlation results from insights (CorrelationResult objects
    # are stored separately; we fall back to checking insights for stock mentions)
    corr_items: list[Any] = []

    # Try to get correlation_results from session state (set by app.py)
    raw_corr = st.session_state.get("correlation_results", [])
    if raw_corr:
        # Filter to correlations that plausibly relate to this route
        route_locodes = {r.origin_locode.lower(), r.dest_locode.lower()}
        for cr in raw_corr:
            signal_lower = cr.signal.lower()
            relevant = (
                r.route_id.lower() in signal_lower
                or any(loc in signal_lower for loc in route_locodes)
                or "fbx" in signal_lower
                or "bdi" in signal_lower
                or "rate" in signal_lower
            )
            if relevant:
                corr_items.append(cr)
        # Fallback: if none matched, show top-5 overall
        if not corr_items:
            corr_items = raw_corr[:5]

    # If still nothing, show info card
    if not corr_items:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:20px;color:{C_TEXT3};font-size:0.88rem;text-align:center">'
            f'No significant stock correlations found for this route. '
            f'Run with scipy installed and at least 60 days of overlapping data.</div>',
            unsafe_allow_html=True,
        )
        return

    # Show up to 4 correlation cards
    for cr in corr_items[:4]:
        r_val  = cr.pearson_r
        r_color = C_HIGH if r_val > 0 else C_LOW
        r_abs  = abs(r_val)
        strength = "Strong" if r_abs >= 0.65 else ("Moderate" if r_abs >= 0.45 else "Weak")
        lag_str  = f"lag {cr.lag_days}d" if cr.lag_days > 0 else "contemporaneous"

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-left:3px solid {r_color};border-radius:10px;'
            f'padding:14px 18px;margin-bottom:8px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'margin-bottom:4px">'
            f'  <div>'
            f'    <span style="font-size:0.95rem;font-weight:700;color:{C_TEXT}">{cr.stock}</span>'
            f'    <span style="font-size:0.75rem;color:{C_TEXT3};margin-left:8px">vs {cr.signal}</span>'
            f'  </div>'
            f'  <div style="text-align:right">'
            f'    <span style="font-size:1.2rem;font-weight:800;color:{r_color}">r={r_val:+.2f}</span>'
            f'    <span style="display:block;font-size:0.68rem;color:{C_TEXT3}">'
            f'      {strength} &bull; {lag_str}'
            f'    </span>'
            f'  </div>'
            f'</div>'
            f'<div style="font-size:0.75rem;color:{C_TEXT3}">{cr.interpretation}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Mini dual-axis chart: route rate vs most correlated stock
    best_cr = corr_items[0]
    stock_df = stock_data.get(best_cr.stock)
    route_df = freight_data.get(r.route_id)

    if stock_df is not None and not stock_df.empty and route_df is not None and not route_df.empty:
        sdf = stock_df.copy()
        sdf["date"] = pd.to_datetime(sdf["date"])
        sdf = sdf.sort_values("date").set_index("date")["close"]

        rdf = route_df.copy()
        rdf["date"] = pd.to_datetime(rdf["date"])
        rdf = rdf.sort_values("date").set_index("date")["rate_usd_per_feu"]

        # Align on date and normalise to % change from first observation
        combined = pd.DataFrame({"stock": sdf, "rate": rdf}).dropna()
        if len(combined) >= 5:
            stock_base = combined["stock"].iloc[0]
            rate_base  = combined["rate"].iloc[0]
            combined["stock_pct"] = (
                (combined["stock"] / stock_base - 1) * 100 if stock_base != 0 else 0.0
            )
            combined["rate_pct"]  = (
                (combined["rate"]  / rate_base  - 1) * 100 if rate_base  != 0 else 0.0
            )

            dual_fig = go.Figure()
            dual_fig.add_trace(go.Scatter(
                x=combined.index, y=combined["rate_pct"],
                name="Rate % Chg", yaxis="y",
                mode="lines", line=dict(color=C_ACCENT, width=2),
                hovertemplate="%{x|%Y-%m-%d}: %{y:+.1f}%<extra>Rate</extra>",
            ))
            dual_fig.add_trace(go.Scatter(
                x=combined.index, y=combined["stock_pct"],
                name=f"{best_cr.stock} % Chg", yaxis="y2",
                mode="lines", line=dict(color=C_HIGH, width=2, dash="dot"),
                hovertemplate="%{x|%Y-%m-%d}: %{y:+.1f}%<extra>" + best_cr.stock + "</extra>",
            ))
            dual_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor=C_BG,
                plot_bgcolor=C_SURFACE,
                font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
                height=260,
                margin=dict(l=20, r=60, t=30, b=20),
                title=dict(
                    text=f"Route Rate vs {best_cr.stock} (normalised % change)",
                    font=dict(size=12, color=C_TEXT2), x=0.01,
                ),
                xaxis=dict(gridcolor="rgba(255,255,255,0.05)",
                           tickfont=dict(color=C_TEXT3, size=10)),
                yaxis=dict(title="Rate % Chg", titlefont=dict(color=C_ACCENT, size=10),
                           tickfont=dict(color=C_TEXT3, size=10),
                           gridcolor="rgba(255,255,255,0.05)",
                           ticksuffix="%"),
                yaxis2=dict(title=f"{best_cr.stock} % Chg",
                            titlefont=dict(color=C_HIGH, size=10),
                            tickfont=dict(color=C_TEXT3, size=10),
                            overlaying="y", side="right",
                            gridcolor="rgba(0,0,0,0)",
                            ticksuffix="%"),
                legend=dict(font=dict(color=C_TEXT2, size=10),
                            bgcolor="rgba(0,0,0,0)",
                            orientation="h", yanchor="bottom", y=1.02,
                            xanchor="right", x=1),
                hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                                font=dict(color=C_TEXT, size=12)),
            )
            st.plotly_chart(dual_fig, use_container_width=True, key=f"deep_dive_dual_corr_{r.route_id}_{best_cr.stock}")


# ── Section 7: News & Sentiment ───────────────────────────────────────────────

def _render_news(r: RouteOpportunity) -> None:
    try:
        from processing.news_feed import get_cached_news
        all_news = get_cached_news()
    except Exception:
        all_news = []

    if not all_news:
        # Try fetching fresh
        try:
            from processing.news_feed import fetch_shipping_news
            all_news = fetch_shipping_news(max_items=60)
        except Exception:
            all_news = []

    # Filter news relevant to this route's ports / regions
    keywords = {
        r.origin_locode.lower(),
        r.dest_locode.lower(),
        r.origin_region.lower(),
        r.dest_region.lower(),
        r.route_name.lower(),
    }
    # Add country/city hints from locode prefixes
    locode_hints = {r.origin_locode[:2].lower(), r.dest_locode[:2].lower()}
    keywords |= locode_hints

    def _is_relevant(item) -> bool:
        combined = (item.title + " ".join(item.keywords)).lower()
        return any(kw in combined for kw in keywords)

    relevant = [item for item in all_news if _is_relevant(item)]
    display  = relevant[:8] if relevant else list(all_news)[:5]

    if not display:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:20px;text-align:center;color:{C_TEXT3};font-size:0.85rem">'
            f'No recent news available. Configure RSS feeds in processing/news_feed.py.</div>',
            unsafe_allow_html=True,
        )
        return

    if not relevant and all_news:
        st.caption(
            f"No news specifically matched {r.route_name} — showing latest shipping headlines."
        )

    for item in display:
        sentiment  = getattr(item, "sentiment_score", 0.0)
        relevance  = getattr(item, "relevance_score", 0.5)
        sent_color = C_HIGH if sentiment > 0.1 else (C_LOW if sentiment < -0.1 else C_MOD)
        sent_label = "Positive" if sentiment > 0.1 else ("Negative" if sentiment < -0.1 else "Neutral")
        sent_arrow = "▲" if sentiment > 0.1 else ("▼" if sentiment < -0.1 else "→")

        try:
            pub_dt = item.published_dt
            age_h  = int((datetime.datetime.utcnow() - pub_dt.replace(tzinfo=None)).total_seconds() / 3600)
            age_str = f"{age_h}h ago" if age_h < 48 else f"{age_h // 24}d ago"
        except Exception:
            age_str = ""

        source = getattr(item, "source", "Unknown")
        url    = getattr(item, "url", "#")
        title  = item.title

        keywords_html = ""
        for kw in (getattr(item, "keywords", []) or [])[:4]:
            keywords_html += (
                f'<span style="background:rgba(255,255,255,0.05);color:{C_TEXT3};'
                f'padding:1px 7px;border-radius:4px;font-size:0.65rem;margin-right:4px">{kw}</span>'
            )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-left:3px solid {sent_color};border-radius:10px;'
            f'padding:12px 16px;margin-bottom:8px">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
            f'margin-bottom:6px">'
            f'  <a href="{url}" target="_blank" style="font-size:0.88rem;font-weight:600;'
            f'    color:{C_TEXT};text-decoration:none;flex:1;margin-right:12px;line-height:1.35">'
            f'    {title}</a>'
            f'  <div style="text-align:right;flex-shrink:0">'
            f'    <span style="font-size:0.7rem;color:{sent_color};font-weight:700">'
            f'      {sent_arrow} {sent_label}</span>'
            f'    <div style="font-size:0.65rem;color:{C_TEXT3}">{sentiment:+.2f}</div>'
            f'  </div>'
            f'</div>'
            f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            f'  <span style="font-size:0.68rem;color:{C_TEXT3};font-weight:600">{source}</span>'
            f'  <span style="font-size:0.65rem;color:{C_TEXT3}">{age_str}</span>'
            f'  {keywords_html}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Main render ───────────────────────────────────────────────────────────────

def render(
    route_results: list[RouteOpportunity],
    freight_data: dict,
    port_results: list,
    macro_data: dict,
    stock_data: dict,
    forecasts: list,
    insights: list,
) -> None:
    """Render the Deep Dive tab.

    Parameters
    ----------
    route_results:
        List of RouteOpportunity from routes.optimizer (sorted by score).
    freight_data:
        Dict route_id -> DataFrame with columns [date, rate_usd_per_feu, source].
    port_results:
        List of PortDemandResult from ports.demand_analyzer.
    macro_data:
        Dict series_id -> DataFrame from data.fred_feed.
    stock_data:
        Dict ticker -> DataFrame from data.stock_feed.
    forecasts:
        List of RateForecast from processing.forecaster.
    insights:
        List of Insight from engine.scorer.
    """
    if not route_results:
        st.info("No route data available. Check API credentials and click Refresh.")
        return

    # Stash correlation_results into session state so Section 6 can read it
    # (app.py computes correlation_results outside this render call)
    # We don't overwrite if already present from a previous run in this session.

    # ── Section 0: Route Selector ─────────────────────────────────────────────
    selected = _render_route_selector(route_results)

    st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)

    # ── Section 1: Hero Identity Card ────────────────────────────────────────
    _render_identity_card(selected)

    # ── Section 2: Rate History + Technical Indicators ────────────────────────
    _divider("Rate History — Technical Analysis")
    _render_rate_chart(selected.route_id, freight_data, selected.current_rate_usd_feu)

    # ── Section 3: Statistical Summary ───────────────────────────────────────
    _divider("Statistical Summary")
    _render_stats_panel(selected.route_id, freight_data)

    st.markdown(
        f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-top:6px">'
        f'Z-Score measures standard deviations from all-time mean. '
        f'Volatility annualised from daily log-return standard deviation.'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Section 4: Port Deep Dive ─────────────────────────────────────────────
    _divider("Port Deep Dive")
    _render_port_deep_dive(selected, port_results)

    # ── Section 5: Forecasts ──────────────────────────────────────────────────
    _divider("Rate Forecasts")
    _render_forecasts(selected.route_id, forecasts, freight_data)

    # ── Section 6: Correlated Assets ─────────────────────────────────────────
    _divider("Correlated Assets")
    _render_correlated_assets(selected, insights, stock_data, freight_data)

    # ── Section 7: News & Sentiment ───────────────────────────────────────────
    _divider("News & Sentiment")
    _render_news(selected)

    # ── Footer ────────────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="margin-top:32px;padding-top:16px;border-top:1px solid {C_BORDER};'
        f'font-size:0.7rem;color:{C_TEXT3};text-align:center">'
        f'Deep Dive &bull; {selected.route_name} &bull; '
        f'Generated: {selected.generated_at}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── app.py integration ────────────────────────────────────────────────────────
#
#   Add to app.py after the existing tab definitions:
#
#       tab_labels = [..., "Deep Dive"]  # add to tab list
#       tab_deep_dive = st.tabs(tab_labels)[-1]   # or unpack appropriately
#
#       # Also store correlation_results in session state so the Deep Dive
#       # correlated assets section can access them:
#       st.session_state["correlation_results"] = correlation_results
#
#       with tab_deep_dive:
#           from ui import tab_deep_dive as _dd
#           _dd.render(
#               route_results=route_results,
#               freight_data=freight_data,
#               port_results=port_results,
#               macro_data=macro_data,
#               stock_data=stock_data,
#               forecasts=forecasts,
#               insights=insights,
#           )
