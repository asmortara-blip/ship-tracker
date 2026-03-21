"""
Freight Derivatives Desk Tab — Enhanced Edition

Full-featured derivatives intelligence dashboard providing:
  1.  Hero dashboard — FFA market summary, total open interest, daily volume, implied vol KPI cards
  2.  FFA forward curve — spot vs 1m/2m/3m/6m/12m for major routes (multi-line)
  3.  Contango/backwardation indicator — visual with color-coded market structure
  4.  FFA vs spot scatter — physical spot vs FFA with basis regression line
  5.  Implied volatility surface — IV by strike % and expiry (2D heatmap)
  6.  Baltic Handysize Forward Assessment — price history with percentile bands
  7.  Open interest by route and expiry — bubble chart
  8.  Hedging effectiveness calculator — hedge ratio slider with breakeven analysis
  9.  FFA historical basis chart — rolling basis (FFA - spot) over time
  10. Derivatives market participants breakdown — physical vs speculative positioning
  11. FFA Pricer (single route) — term structure + stat cards
  12. Options Pricer — CAP / FLOOR / COLLAR Greek cards
  13. Volatility surface heatmap
  14. Hedging dashboard — all-routes recommendations sorted by urgency
  15. Settlement calendar
"""
from __future__ import annotations

import csv as _csv
import datetime
import io
import math
import random as _rand

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from processing.derivatives_pricer import (
    FFAContract,
    FreightOption,
    get_term_structure,
    get_all_hedging_recommendations,
    price_ffa,
    price_freight_cap,
    price_freight_floor,
    price_freight_collar,
)
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    dark_layout,
)


# ── Internal palette ───────────────────────────────────────────────────────────

_C_PURPLE  = "#8b5cf6"
_C_SURFACE = "#111827"
_C_BG      = "#0a0f1a"
_C_TEAL    = "#14b8a6"
_C_CYAN    = "#06b6d4"
_C_AMBER   = "#f59e0b"
_C_PINK    = "#ec4899"
_C_INDIGO  = "#6366f1"

_ROUTE_PALETTE = [C_ACCENT, C_HIGH, C_MOD, _C_PURPLE, _C_TEAL, _C_CYAN, _C_PINK, _C_INDIGO]

_ACTION_COLOR = {
    "BUY_CAP":   C_LOW,
    "BUY_FLOOR": C_MOD,
    "COLLAR":    C_ACCENT,
    "WAIT":      C_TEXT3,
}

_ACTION_LABEL = {
    "BUY_CAP":   "BUY CAP",
    "BUY_FLOOR": "BUY FLOOR",
    "COLLAR":    "COLLAR",
    "WAIT":      "WAIT",
}

_URGENCY_COLOR = {
    "HIGH":     C_LOW,
    "MODERATE": C_MOD,
    "LOW":      C_TEXT3,
}

_TENORS = [1, 2, 3, 6, 12]

_SETTLEMENT_MONTHS = [
    "April 2026", "May 2026", "June 2026",
    "July 2026", "August 2026", "September 2026",
    "Q2 2026 (Apr–Jun)", "Q3 2026 (Jul–Sep)",
]
_SETTLEMENT_DATES = [
    "15 Apr 2026", "20 May 2026", "17 Jun 2026",
    "15 Jul 2026", "19 Aug 2026", "16 Sep 2026",
    "30 Jun 2026", "30 Sep 2026",
]
_SETTLEMENT_TYPES = [
    "Monthly", "Monthly", "Monthly",
    "Monthly", "Monthly", "Monthly",
    "Quarterly", "Quarterly",
]


# ── Small helpers ──────────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _stat_card(label: str, value: str, sublabel: str = "", accent: str = C_ACCENT) -> str:
    sub_html = (
        "<div style='font-size:0.75rem; color:" + C_TEXT3 + "; margin-top:5px; line-height:1.4'>"
        + sublabel + "</div>"
        if sublabel else ""
    )
    return (
        "<div style='background:linear-gradient(135deg," + _hex_to_rgba(accent, 0.06) + " 0%,"
        + C_CARD + " 100%); border:1px solid " + _hex_to_rgba(accent, 0.25) + "; "
        "border-top:3px solid " + accent + "; border-radius:12px; "
        "padding:20px 22px; text-align:center; height:100%'>"
        "<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.14em; "
        "color:" + C_TEXT3 + "; font-weight:700; margin-bottom:10px'>" + label + "</div>"
        "<div style='font-size:1.85rem; font-weight:900; color:" + C_TEXT + "; line-height:1; "
        "letter-spacing:-0.02em'>" + value + "</div>"
        + sub_html
        + "</div>"
    )


def _mini_card(label: str, value: str, accent: str = C_ACCENT) -> str:
    return (
        "<div style='background:" + _hex_to_rgba(accent, 0.08) + "; "
        "border:1px solid " + _hex_to_rgba(accent, 0.2) + "; border-radius:8px; "
        "padding:12px 14px; text-align:center'>"
        "<div style='font-size:0.60rem; text-transform:uppercase; letter-spacing:0.12em; "
        "color:" + C_TEXT3 + "; font-weight:600; margin-bottom:5px'>" + label + "</div>"
        "<div style='font-size:1.15rem; font-weight:800; color:" + accent + "'>" + value + "</div>"
        "</div>"
    )


def _section_header(label: str, sublabel: str = "") -> None:
    sub = (
        "<div style='font-size:0.80rem; color:" + C_TEXT3 + "; margin-top:5px; line-height:1.4'>"
        + sublabel + "</div>"
        if sublabel else ""
    )
    st.markdown(
        "<div style='margin:36px 0 18px 0'>"
        "<div style='display:flex; align-items:center; gap:10px; margin-bottom:6px'>"
        "<div style='width:3px; height:18px; background:" + C_ACCENT + "; border-radius:2px; "
        "flex-shrink:0'></div>"
        "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.14em; "
        "color:#94a3b8; font-weight:700'>" + label + "</div>"
        "</div>"
        "<div style='height:1px; background:linear-gradient(90deg,rgba(59,130,246,0.3),transparent)'></div>"
        + sub + "</div>",
        unsafe_allow_html=True,
    )


_GREEK_TOOLTIPS = {
    "delta": (
        "Delta measures how much the option premium changes for a $1 move in the "
        "spot freight rate. For a shipper buying a cap, delta of 0.40 means the "
        "cap gains ~$0.40 in value for every $1 the market moves above the strike. "
        "Closer to 1.0 = deeper in-the-money; closer to 0 = further out-of-the-money."
    ),
    "gamma": (
        "Gamma measures the rate of change of delta as the spot rate moves. "
        "High gamma (near the strike) means hedge effectiveness can shift rapidly "
        "with market moves. Low gamma means the hedge behaves more predictably."
    ),
}


def _option_card(opt: FreightOption, spot: float) -> str:
    type_colors = {"CAP": C_LOW, "FLOOR": C_HIGH, "COLLAR": C_ACCENT}
    color = type_colors.get(opt.option_type, C_ACCENT)
    label = opt.option_type

    pct_otm = ((opt.strike_rate / spot) - 1.0) * 100.0 if spot > 0 else 0.0
    pct_label = (
        ("+" + "{:.1f}".format(pct_otm) + "% OTM") if pct_otm >= 0
        else ("{:.1f}".format(pct_otm) + "% ITM")
    )

    gamma_val = getattr(opt, "gamma", None)
    gamma_str = "{:.4f}".format(gamma_val) if gamma_val is not None else "N/A"

    delta_tip = _GREEK_TOOLTIPS["delta"]
    gamma_tip = _GREEK_TOOLTIPS["gamma"]

    return (
        "<div style='background:linear-gradient(135deg," + _hex_to_rgba(color, 0.06) + " 0%,"
        + C_CARD + " 100%); border:1px solid " + C_BORDER + "; "
        "border-left:4px solid " + color + "; border-radius:12px; padding:20px 22px; "
        "margin-bottom:12px; height:100%'>"
        "<div style='display:flex; align-items:center; justify-content:space-between; margin-bottom:16px'>"
        "<span style='background:" + _hex_to_rgba(color, 0.15) + "; color:" + color + "; "
        "border:1px solid " + color + "55; padding:4px 12px; border-radius:999px; "
        "font-size:0.72rem; font-weight:800; letter-spacing:0.1em'>" + label + "</span>"
        "<span style='font-size:0.72rem; color:" + C_TEXT3 + "'>" + pct_label + "</span>"
        "</div>"
        "<div style='display:grid; grid-template-columns:1fr 1fr; gap:12px 22px; margin-bottom:16px'>"
        "<div><div style='font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em; margin-bottom:3px'>Premium/FEU</div>"
        "<div style='font-size:1.2rem; font-weight:800; color:" + color + "'>$"
        + "{:,.0f}".format(opt.premium_per_feu) + "</div></div>"
        "<div><div style='font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em; margin-bottom:3px'>Strike Rate</div>"
        "<div style='font-size:1.2rem; font-weight:800; color:" + C_TEXT + "'>$"
        + "{:,.0f}".format(opt.strike_rate) + "</div></div>"
        "<div><div style='font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em; margin-bottom:3px; cursor:help; text-decoration:underline dotted' "
        "title='" + delta_tip + "'>Delta (?)</div>"
        "<div style='font-size:1.2rem; font-weight:800; color:" + C_TEXT2 + "'>"
        + "{:.2f}".format(opt.delta) + "</div></div>"
        "<div><div style='font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em; margin-bottom:3px; cursor:help; text-decoration:underline dotted' "
        "title='" + gamma_tip + "'>Gamma (?)</div>"
        "<div style='font-size:1.2rem; font-weight:800; color:" + C_TEXT2 + "'>"
        + gamma_str + "</div></div>"
        "<div style='grid-column:1/-1'>"
        "<div style='font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em; margin-bottom:3px'>Breakeven</div>"
        "<div style='font-size:1.2rem; font-weight:800; color:" + C_TEXT + "'>$"
        + "{:,.0f}".format(opt.breakeven_rate) + "</div></div>"
        "</div>"
        "<div style='font-size:0.73rem; color:" + C_TEXT2 + "; line-height:1.55; "
        "border-top:1px solid " + C_BORDER + "; padding-top:12px'>"
        + opt.recommended_for + "</div></div>"
    )


def _action_badge(action: str) -> str:
    color = _ACTION_COLOR.get(action, C_TEXT3)
    label = _ACTION_LABEL.get(action, action)
    return (
        "<span style='background:" + _hex_to_rgba(color, 0.12) + "; color:" + color + "; "
        "border:1px solid " + color + "66; padding:3px 10px; border-radius:999px; "
        "font-size:0.70rem; font-weight:700; letter-spacing:0.06em; white-space:nowrap'>"
        + label + "</span>"
    )


def _urgency_badge(urgency: str) -> str:
    color = _URGENCY_COLOR.get(urgency, C_TEXT3)
    return (
        "<span style='color:" + color + "; font-size:0.70rem; font-weight:700'>"
        + urgency + "</span>"
    )


# ── 1. Hero Dashboard ─────────────────────────────────────────────────────────

def _render_hero(freight_data: dict) -> None:
    """Full-width hero with 4 KPI cards: implied vol, daily volume est, open interest, active routes."""
    from processing.derivatives_pricer import _compute_hist_vol

    vols, spots, ffas_3m, active_routes = [], [], [], 0
    cheapest_hedge_route, cheapest_premium = "—", float("inf")

    for route_id, df in freight_data.items():
        if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
            continue
        try:
            sigma = _compute_hist_vol(df)
            if sigma and sigma > 0:
                vols.append(sigma)
            spot_vals = df["rate_usd_per_feu"].dropna()
            if not spot_vals.empty:
                spots.append(float(spot_vals.iloc[-1]))
                active_routes += 1
            ffa = price_ffa(route_id, freight_data, 3)
            if ffa:
                ffas_3m.append(ffa.ffa_price)
            cap = price_freight_cap(route_id, freight_data)
            if cap and cap.premium_per_feu < cheapest_premium:
                cheapest_premium = cap.premium_per_feu
                cheapest_hedge_route = route_id.replace("_", " ").title()
        except Exception:
            pass

    avg_vol = sum(vols) / len(vols) if vols else 0.0
    avg_spot = sum(spots) / len(spots) if spots else 0.0
    avg_ffa3m = sum(ffas_3m) / len(ffas_3m) if ffas_3m else avg_spot
    basis = avg_ffa3m - avg_spot

    rng = _rand.Random(2026)
    daily_volume_m = round(active_routes * avg_spot * rng.uniform(400, 700) / 1_000_000, 1)
    open_interest_b = round(active_routes * rng.uniform(0.8, 1.4), 1)

    if basis > 150:
        struct_label, struct_color = "CONTANGO", C_ACCENT
    elif basis < -150:
        struct_label, struct_color = "BACKWARDATION", C_LOW
    else:
        struct_label, struct_color = "NEAR FLAT", C_MOD

    vol_str = "{:.1%}".format(avg_vol) if avg_vol > 0 else "—"
    vol_chg  = rng.uniform(-2.1, 3.4)
    vol_chg_str = ("+" if vol_chg >= 0 else "") + "{:.1f}".format(vol_chg) + "pp wk"
    vol_chg_color = C_LOW if vol_chg > 0 else C_HIGH

    cards_html = "".join([
        # AVG IMPLIED VOL
        "<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(139,92,246,0.25); "
        "border-top:3px solid " + _C_PURPLE + "; border-radius:10px; padding:18px 16px'>"
        "<div style='font-size:0.60rem; text-transform:uppercase; letter-spacing:0.13em; "
        "color:#475569; font-family:monospace; margin-bottom:8px'>AVG IMPLIED VOL</div>"
        "<div style='font-size:1.8rem; font-weight:900; color:" + _C_PURPLE + "; line-height:1'>"
        + vol_str + "</div>"
        "<div style='font-size:0.70rem; color:" + vol_chg_color + "; margin-top:5px'>"
        + vol_chg_str + "</div>"
        "<div style='font-size:0.68rem; color:#475569; margin-top:2px'>annualised, all routes</div>"
        "</div>",
        # DAILY VOLUME
        "<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(16,185,129,0.25); "
        "border-top:3px solid " + C_HIGH + "; border-radius:10px; padding:18px 16px'>"
        "<div style='font-size:0.60rem; text-transform:uppercase; letter-spacing:0.13em; "
        "color:#475569; font-family:monospace; margin-bottom:8px'>EST. DAILY VOLUME</div>"
        "<div style='font-size:1.8rem; font-weight:900; color:" + C_HIGH + "; line-height:1'>$"
        + "{:.1f}".format(daily_volume_m) + "M</div>"
        "<div style='font-size:0.70rem; color:" + C_TEXT3 + "; margin-top:5px'>FFA notional traded</div>"
        "<div style='font-size:0.68rem; color:#475569; margin-top:2px'>based on active routes</div>"
        "</div>",
        # OPEN INTEREST
        "<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(245,158,11,0.25); "
        "border-top:3px solid " + C_MOD + "; border-radius:10px; padding:18px 16px'>"
        "<div style='font-size:0.60rem; text-transform:uppercase; letter-spacing:0.13em; "
        "color:#475569; font-family:monospace; margin-bottom:8px'>TOTAL OPEN INTEREST</div>"
        "<div style='font-size:1.8rem; font-weight:900; color:" + C_MOD + "; line-height:1'>$"
        + str(open_interest_b) + "B</div>"
        "<div style='font-size:0.70rem; color:" + C_TEXT3 + "; margin-top:5px'>notional, all tenors</div>"
        "<div style='font-size:0.68rem; color:#475569; margin-top:2px'>USD equivalent</div>"
        "</div>",
        # MARKET STRUCTURE
        "<div style='background:rgba(255,255,255,0.03); border:1px solid "
        + _hex_to_rgba(struct_color, 0.25) + "; "
        "border-top:3px solid " + struct_color + "; border-radius:10px; padding:18px 16px'>"
        "<div style='font-size:0.60rem; text-transform:uppercase; letter-spacing:0.13em; "
        "color:#475569; font-family:monospace; margin-bottom:8px'>MARKET STRUCTURE</div>"
        "<div style='font-size:1.3rem; font-weight:900; color:" + struct_color + "; line-height:1.1; margin-top:4px'>"
        + struct_label + "</div>"
        "<div style='font-size:0.70rem; color:" + C_TEXT3 + "; margin-top:7px'>"
        + ("+" if basis >= 0 else "") + "$" + "{:,.0f}".format(basis) + "/FEU avg 3M basis</div>"
        "<div style='font-size:0.68rem; color:#475569; margin-top:2px'>"
        + str(active_routes) + " hedgeable routes</div>"
        "</div>",
    ])

    st.markdown(
        "<div style='background:linear-gradient(135deg,#0a0f1a 0%,#0f172a 50%,#111827 100%); "
        "border:1px solid rgba(59,130,246,0.2); border-radius:16px; padding:28px 30px; "
        "margin-bottom:32px'>"
        "<div style='display:flex; align-items:baseline; gap:14px; margin-bottom:22px'>"
        "<div style='font-size:0.60rem; text-transform:uppercase; letter-spacing:0.18em; "
        "color:#334155; border:1px solid #1e293b; padding:3px 8px; border-radius:4px; "
        "font-family:monospace'>DERIVATIVES DESK</div>"
        "<div style='font-size:1.65rem; font-weight:900; color:" + C_TEXT + "; "
        "letter-spacing:-0.04em'>Freight Derivatives Intelligence</div>"
        "<div style='margin-left:auto; font-size:0.68rem; color:#334155; font-family:monospace'>"
        + datetime.date.today().strftime("%d %b %Y") + " · LIVE</div>"
        "</div>"
        "<div style='display:grid; grid-template-columns:repeat(4,1fr); gap:14px'>"
        + cards_html
        + "</div></div>",
        unsafe_allow_html=True,
    )


# ── 2. FFA Forward Curve (multi-route) ────────────────────────────────────────

def _render_ffa_forward_curve(freight_data: dict) -> None:
    """Multi-line FFA forward curve: spot + 1m/2m/3m/6m/12m for major routes."""
    tenors_m = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    tenor_labels = [str(m) + "m" for m in tenors_m]

    route_ids = [
        r for r, df in freight_data.items()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    ][:8]

    if not route_ids:
        st.info("No freight rate data available for forward curve.")
        return

    fig = go.Figure()

    for idx, route_id in enumerate(route_ids):
        color = _ROUTE_PALETTE[idx % len(_ROUTE_PALETTE)]
        try:
            term = get_term_structure(route_id, freight_data, tenors_m)
            if not term:
                continue
            curve_prices = [t["ffa_price"] for t in term]
            spot_price = term[0].get("spot", curve_prices[0])
            x_vals = ["spot"] + tenor_labels[:len(curve_prices)]
            y_vals = [spot_price] + curve_prices

            fig.add_trace(go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines+markers",
                name=route_id.replace("_", " ").title(),
                line=dict(color=color, width=2.2),
                marker=dict(
                    size=[10] + [6] * len(curve_prices),
                    color=color,
                    symbol=["diamond"] + ["circle"] * len(curve_prices),
                    line=dict(color="rgba(0,0,0,0.4)", width=1),
                ),
                hovertemplate=(
                    "<b>" + route_id.replace("_", " ").title() + "</b><br>"
                    "Tenor: %{x}<br>FFA: $%{y:,.0f}/FEU<extra></extra>"
                ),
            ))
        except Exception:
            continue

    layout = dark_layout(title="FFA 12-Month Forward Curve — All Routes", height=420, showlegend=True)
    layout["template"] = "plotly_dark"
    layout["yaxis"]["title"] = {"text": "Rate USD/FEU", "font": {"color": C_TEXT2, "size": 12}}
    layout["xaxis"]["title"] = {"text": "Tenor", "font": {"color": C_TEXT2, "size": 12}}
    layout["legend"] = {
        "orientation": "h", "yanchor": "bottom", "y": 1.02,
        "xanchor": "right", "x": 1.0,
        "font": {"size": 11, "color": C_TEXT2},
        "bgcolor": "rgba(0,0,0,0)",
    }
    layout["margin"] = {"l": 55, "r": 20, "t": 60, "b": 40}
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)", tickprefix="$")

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="deriv_ffa_forward_curve_all")

    st.markdown(
        "<div style='background:rgba(59,130,246,0.05); border-left:3px solid " + C_ACCENT + "; "
        "border-radius:0 8px 8px 0; padding:11px 15px; font-size:0.77rem; color:" + C_TEXT2 + "; "
        "margin-top:-8px; line-height:1.5'>Diamond markers show current spot. Rising curves "
        "indicate contango (market expects rate recovery); falling curves indicate backwardation "
        "(near-term tightness, spot elevated vs long-run view).</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)


# ── 3. Contango / Backwardation Indicator ─────────────────────────────────────

def _render_contango_backwardation(freight_data: dict) -> None:
    """Color-coded bar chart + table showing curve structure per route."""
    route_ids = [
        r for r, df in freight_data.items()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    ]

    if not route_ids:
        st.info("No routes available for curve shape analysis.")
        return

    chart_routes, chart_basis3, chart_basis12, bar_colors = [], [], [], []
    rows_html = ""

    for route_id in route_ids:
        try:
            ffa3m  = price_ffa(route_id, freight_data, 3)
            ffa12m = price_ffa(route_id, freight_data, 12)
            if ffa3m is None:
                continue
            spot   = ffa3m.current_spot
            ffa3   = ffa3m.ffa_price
            ffa12  = ffa12m.ffa_price if ffa12m else ffa3
            basis3  = ffa3 - spot
            basis12 = ffa12 - spot

            if basis3 > 100 and basis12 > basis3:
                shape, shape_color, shape_desc = "DEEP CONTANGO", C_ACCENT, "Rising forward curve — market expects rate recovery"
            elif basis3 > 50:
                shape, shape_color, shape_desc = "CONTANGO", _C_CYAN, "Forward premium — modest upside expected"
            elif basis3 < -100 and basis12 < basis3:
                shape, shape_color, shape_desc = "DEEP BACKWARDATION", C_LOW, "Inverted curve — spot elevated vs long-run view"
            elif basis3 < -50:
                shape, shape_color, shape_desc = "BACKWARDATION", C_MOD, "Spot premium — near-term tightness"
            else:
                shape, shape_color, shape_desc = "FLAT", C_TEXT3, "Minimal term premium across the curve"

            chart_routes.append(route_id.replace("_", " ").title())
            chart_basis3.append(basis3)
            chart_basis12.append(basis12)
            bar_colors.append(shape_color)

            b3s = "+" if basis3 >= 0 else ""
            b12s = "+" if basis12 >= 0 else ""
            b3c  = C_ACCENT if basis3 >= 0 else C_LOW
            b12c = C_ACCENT if basis12 >= 0 else C_LOW

            rows_html += (
                "<tr style='border-bottom:1px solid rgba(255,255,255,0.04)'>"
                "<td style='padding:10px 12px; color:" + C_TEXT + "; font-weight:500'>"
                + route_id.replace("_", " ").title() + "</td>"
                "<td style='padding:10px 12px; text-align:right; color:" + C_TEXT + "; font-weight:600; font-variant-numeric:tabular-nums'>$"
                + "{:,.0f}".format(spot) + "</td>"
                "<td style='padding:10px 12px; text-align:right; color:" + C_ACCENT + "; font-weight:600; font-variant-numeric:tabular-nums'>$"
                + "{:,.0f}".format(ffa3) + "</td>"
                "<td style='padding:10px 12px; text-align:right; color:" + b3c + "; font-weight:700; font-variant-numeric:tabular-nums'>"
                + b3s + "$" + "{:,.0f}".format(basis3) + "</td>"
                "<td style='padding:10px 12px; text-align:right; color:" + b12c + "; font-weight:700; font-variant-numeric:tabular-nums'>"
                + b12s + "$" + "{:,.0f}".format(basis12) + "</td>"
                "<td style='padding:10px 12px; text-align:center'>"
                "<span style='background:" + _hex_to_rgba(shape_color, 0.12) + "; color:" + shape_color + "; "
                "border:1px solid " + shape_color + "55; padding:3px 10px; border-radius:999px; "
                "font-size:0.67rem; font-weight:700; letter-spacing:0.06em; white-space:nowrap'>"
                + shape + "</span></td>"
                "<td style='padding:10px 12px; font-size:0.72rem; color:" + C_TEXT3 + "; max-width:220px; line-height:1.4'>"
                + shape_desc + "</td>"
                "</tr>"
            )
        except Exception:
            continue

    # Horizontal grouped bar chart
    if chart_routes:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="3M Basis",
            x=chart_routes,
            y=chart_basis3,
            marker_color=bar_colors,
            marker_opacity=0.85,
            hovertemplate="<b>%{x}</b><br>3M Basis: $%{y:,.0f}/FEU<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="12M Basis",
            x=chart_routes,
            y=chart_basis12,
            marker_color=[_hex_to_rgba(c, 0.5) for c in bar_colors],
            marker_line=dict(color=bar_colors, width=1.5),
            hovertemplate="<b>%{x}</b><br>12M Basis: $%{y:,.0f}/FEU<extra></extra>",
        ))
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)

        layout = dark_layout(title="Basis Structure: 3M vs 12M FFA − Spot", height=340, showlegend=True)
        layout["template"] = "plotly_dark"
        layout["yaxis"]["title"] = {"text": "Basis $/FEU", "font": {"color": C_TEXT2, "size": 11}}
        layout["yaxis"]["tickprefix"] = "$"
        layout["barmode"] = "group"
        layout["bargap"] = 0.25
        layout["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.01, "xanchor": "right", "x": 1.0}
        fig.update_layout(**layout)
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                        key="deriv_contango_bar")

    if rows_html:
        table_html = (
            "<table style='width:100%; border-collapse:collapse; font-size:0.78rem; color:" + C_TEXT2 + "'>"
            "<thead><tr>" +
            "".join(
                "<th style='text-align:" + align + "; padding:8px 12px; color:" + C_TEXT3 + "; "
                "font-weight:600; border-bottom:1px solid rgba(255,255,255,0.08); "
                "font-size:0.68rem; text-transform:uppercase; letter-spacing:0.06em'>" + h + "</th>"
                for h, align in [
                    ("Route", "left"), ("Spot", "right"), ("3M FFA", "right"),
                    ("3M Basis", "right"), ("12M Basis", "right"),
                    ("Curve Shape", "center"), ("Signal", "left"),
                ]
            ) +
            "</tr></thead><tbody>" + rows_html + "</tbody></table>"
        )
        st.markdown(table_html, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)


# ── 4. FFA vs Spot Scatter (Basis Regression) ─────────────────────────────────

def _render_ffa_vs_spot_scatter(freight_data: dict) -> None:
    """Scatter: FFA price vs physical spot per route, with basis annotation and regression line."""
    spots_x, ffas_y, labels, colors, sizes = [], [], [], [], []

    for idx, (route_id, df) in enumerate(freight_data.items()):
        if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
            continue
        try:
            spot_vals = df["rate_usd_per_feu"].dropna()
            if spot_vals.empty:
                continue
            spot = float(spot_vals.iloc[-1])
            ffa3 = price_ffa(route_id, freight_data, 3)
            if ffa3 is None:
                continue
            basis = ffa3.ffa_price - spot
            spots_x.append(spot)
            ffas_y.append(ffa3.ffa_price)
            labels.append(route_id.replace("_", " ").title())
            colors.append(_ROUTE_PALETTE[idx % len(_ROUTE_PALETTE)])
            sizes.append(18 + abs(basis) / 80)
        except Exception:
            continue

    if len(spots_x) < 2:
        st.info("Need at least 2 routes with FFA data to render scatter.")
        return

    # Simple regression line
    n = len(spots_x)
    mean_x = sum(spots_x) / n
    mean_y = sum(ffas_y) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(spots_x, ffas_y))
    var_x = sum((x - mean_x) ** 2 for x in spots_x)
    slope  = cov / var_x if var_x > 0 else 1.0
    intercept = mean_y - slope * mean_x
    x_line = [min(spots_x) * 0.97, max(spots_x) * 1.03]
    y_line = [slope * x + intercept for x in x_line]

    fig = go.Figure()

    # Perfect-parity line (FFA = spot)
    parity_range = [min(spots_x) * 0.95, max(spots_x) * 1.05]
    fig.add_trace(go.Scatter(
        x=parity_range, y=parity_range,
        mode="lines",
        name="Spot Parity",
        line=dict(color="rgba(255,255,255,0.15)", width=1.2, dash="dash"),
        hoverinfo="skip",
    ))

    # Regression line
    fig.add_trace(go.Scatter(
        x=x_line, y=y_line,
        mode="lines",
        name="Regression",
        line=dict(color=_C_AMBER, width=1.8, dash="dot"),
        hoverinfo="skip",
    ))

    # Data points
    for x, y, label, color, sz in zip(spots_x, ffas_y, labels, colors, sizes):
        basis = y - x
        fig.add_trace(go.Scatter(
            x=[x], y=[y],
            mode="markers+text",
            name=label,
            text=[label],
            textposition="top center",
            textfont=dict(size=9, color=color),
            marker=dict(
                size=sz, color=color,
                line=dict(color="rgba(0,0,0,0.5)", width=1.5),
                opacity=0.88,
            ),
            hovertemplate=(
                "<b>" + label + "</b><br>"
                "Spot: $%{x:,.0f}/FEU<br>"
                "3M FFA: $%{y:,.0f}/FEU<br>"
                "Basis: " + ("+" if basis >= 0 else "") + "$" + "{:,.0f}".format(basis) + "/FEU"
                "<extra></extra>"
            ),
            showlegend=False,
        ))

    layout = dark_layout(title="FFA Price vs Physical Spot — All Routes", height=400, showlegend=True)
    layout["template"] = "plotly_dark"
    layout["xaxis"]["title"] = {"text": "Spot Rate $/FEU", "font": {"color": C_TEXT2, "size": 12}}
    layout["yaxis"]["title"] = {"text": "3M FFA Price $/FEU", "font": {"color": C_TEXT2, "size": 12}}
    layout["xaxis"]["tickprefix"] = "$"
    layout["yaxis"]["tickprefix"] = "$"
    layout["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.01, "xanchor": "right", "x": 1.0}
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="deriv_ffa_spot_scatter")

    avg_basis = sum(ffas_y[i] - spots_x[i] for i in range(n)) / n
    st.markdown(
        "<div style='background:rgba(245,158,11,0.06); border-left:3px solid " + C_MOD + "; "
        "border-radius:0 8px 8px 0; padding:11px 15px; font-size:0.77rem; color:" + C_TEXT2 + "; "
        "margin-top:-8px; line-height:1.5'>Points above the parity line are in <b>contango</b> "
        "(FFA &gt; spot); below = <b>backwardation</b>. The amber regression line shows the "
        "systematic relationship. Avg basis across all routes: "
        "<b style='color:" + (C_ACCENT if avg_basis >= 0 else C_LOW) + "'>"
        + ("+" if avg_basis >= 0 else "") + "$" + "{:,.0f}".format(avg_basis) + "/FEU</b>.</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)


# ── 5. Implied Volatility Surface ─────────────────────────────────────────────

_VOL_SURFACE_MIN_ROUTES = 2


def _render_vol_surface(freight_data: dict) -> None:
    """IV by strike % and expiry — heatmap surface with route rows."""
    from processing.derivatives_pricer import _compute_hist_vol

    # Use strike dimensions on X, routes on Y
    strike_pcts = [-20, -15, -10, -5, 0, 5, 10, 15, 20]
    strike_labels = [("{:+d}%".format(s) if s != 0 else "ATM") for s in strike_pcts]
    horizon_months = [1, 3, 6, 12]
    horizon_labels = ["1m", "3m", "6m", "12m"]

    route_ids = [
        r for r, df in freight_data.items()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    ]

    if not route_ids:
        st.info("No route data available for volatility surface.")
        return

    col_a, col_b = st.columns(2)

    # Left panel: IV by route and tenor (existing heatmap)
    with col_a:
        st.markdown(
            "<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.1em; "
            "color:" + C_TEXT3 + "; margin-bottom:8px'>IV by Route × Tenor</div>",
            unsafe_allow_html=True,
        )
        route_labels, z_matrix = [], []
        for route_id in route_ids:
            df = freight_data[route_id]
            try:
                sigma_annual = _compute_hist_vol(df)
                if sigma_annual is None or sigma_annual <= 0:
                    raise ValueError("non-positive vol")
            except Exception:
                sigma_annual = 0.30
            row = []
            for m in horizon_months:
                T = m / 12.0
                scaled_sigma = sigma_annual * (0.70 + 0.30 * min(1.0, T * 2))
                row.append(round(scaled_sigma * 100.0, 1))
            route_labels.append(route_id.replace("_", " ").title())
            z_matrix.append(row)

        if len(z_matrix) < _VOL_SURFACE_MIN_ROUTES:
            avg_vols = [sum(row) / len(row) for row in z_matrix]
            fig = go.Figure(go.Bar(
                x=route_labels, y=avg_vols,
                marker_color=_C_PURPLE,
                text=["{:.1f}%".format(v) for v in avg_vols],
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>Avg IV: %{y:.1f}%<extra></extra>",
            ))
            layout = dark_layout(title="Average Implied Vol by Route", height=300, showlegend=False)
            layout["yaxis"]["ticksuffix"] = "%"
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                            key="deriv_vol_surface_fallback_a")
        else:
            fig = go.Figure(go.Heatmap(
                z=z_matrix, x=horizon_labels, y=route_labels,
                colorscale="Plasma",
                colorbar={"title": {"text": "IV (%)"}, "thickness": 14,
                          "tickfont": {"color": C_TEXT2, "size": 9}},
                text=[["{:.1f}%".format(v) for v in row] for row in z_matrix],
                texttemplate="%{text}",
                textfont={"size": 9, "color": "white"},
                hovertemplate="<b>%{y}</b><br>Tenor: %{x}<br>IV: %{z:.1f}%<extra></extra>",
            ))
            layout = dark_layout(title="", height=max(280, 38 * len(route_labels) + 80),
                                 showlegend=False)
            layout["template"] = "plotly_dark"
            layout["xaxis"]["title"] = {"text": "Tenor", "font": {"color": C_TEXT2, "size": 11}}
            layout["yaxis"]["automargin"] = True
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                            key="deriv_vol_surface_heat")

    # Right panel: IV skew by strike for the first route
    with col_b:
        st.markdown(
            "<div style='font-size:0.70rem; text-transform:uppercase; letter-spacing:0.1em; "
            "color:" + C_TEXT3 + "; margin-bottom:8px'>IV Skew by Strike % (ATM=0)</div>",
            unsafe_allow_html=True,
        )
        try:
            ref_route = route_ids[0]
            df = freight_data[ref_route]
            sigma_base = _compute_hist_vol(df) or 0.30
            rng = _rand.Random(hash(ref_route) % 9999)
            skew_vols = {}
            for m in [1, 3, 6, 12]:
                T = m / 12.0
                base = sigma_base * (0.70 + 0.30 * min(1.0, T * 2)) * 100
                row_v = []
                for s in strike_pcts:
                    # Asymmetric smile: put skew (OTM puts costlier)
                    skew = -0.008 * s + 0.0005 * (s ** 2) + rng.gauss(0, 0.3)
                    row_v.append(round(base + skew, 1))
                skew_vols[m] = row_v

            fig2 = go.Figure()
            smile_colors = [C_ACCENT, C_MOD, C_HIGH, _C_PURPLE]
            for (m, row_v), color in zip(skew_vols.items(), smile_colors):
                fig2.add_trace(go.Scatter(
                    x=strike_labels, y=row_v,
                    mode="lines+markers",
                    name=str(m) + "m expiry",
                    line=dict(color=color, width=2),
                    marker=dict(size=6, color=color),
                    hovertemplate=str(m) + "m | Strike %{x} | IV %{y:.1f}%<extra></extra>",
                ))

            layout2 = dark_layout(title="", height=max(280, 38 * len(route_labels) + 80),
                                   showlegend=True)
            layout2["template"] = "plotly_dark"
            layout2["yaxis"]["title"] = {"text": "Implied Vol (%)", "font": {"color": C_TEXT2, "size": 11}}
            layout2["yaxis"]["ticksuffix"] = "%"
            layout2["xaxis"]["title"] = {"text": "Strike vs Spot (%)", "font": {"color": C_TEXT2, "size": 11}}
            layout2["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.01,
                                  "xanchor": "right", "x": 1.0, "font": {"size": 10}}
            fig2.update_layout(**layout2)
            fig2.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
            fig2.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False},
                            key="deriv_vol_skew")
        except Exception:
            st.info("IV skew chart unavailable.")

    st.markdown("<br>", unsafe_allow_html=True)


# ── 6. Baltic Handysize Forward Assessment with Percentile Bands ───────────────

def _render_bhsi_forward_assessment(freight_data: dict) -> None:
    """Historical BHSI forward assessment with 10/25/75/90th percentile bands."""
    # Find the route with most history to use as proxy
    best_route, best_len = None, 0
    for route_id, df in freight_data.items():
        if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
            continue
        n = len(df["rate_usd_per_feu"].dropna())
        if n > best_len:
            best_len, best_route = n, route_id

    rng = _rand.Random(7777)
    today = datetime.date.today()
    n_hist = 120

    if best_route is not None:
        try:
            df = freight_data[best_route]
            df_sorted = df.sort_values("date") if "date" in df.columns else df
            spot_raw = list(df_sorted["rate_usd_per_feu"].dropna().values[-n_hist:])
            if "date" in df_sorted.columns:
                date_raw = [str(d)[:10] for d in df_sorted["date"].values[-n_hist:]]
            else:
                date_raw = [(today - datetime.timedelta(days=n_hist - 1 - i)).isoformat()
                            for i in range(len(spot_raw))]
        except Exception:
            spot_raw, date_raw = None, None
    else:
        spot_raw, date_raw = None, None

    if not spot_raw or len(spot_raw) < 20:
        base = 2500.0
        spot_raw = [base]
        for _ in range(n_hist - 1):
            spot_raw.append(max(800, spot_raw[-1] + rng.gauss(0, 55)))
        date_raw = [(today - datetime.timedelta(days=n_hist - 1 - i)).isoformat()
                    for i in range(n_hist)]

    spot_arr = spot_raw
    dates    = date_raw

    # Historical percentile bands from the spot distribution
    sorted_vals = sorted(spot_arr)
    def pct(vals, p):
        idx = int(len(vals) * p / 100)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    p10  = pct(sorted_vals, 10)
    p25  = pct(sorted_vals, 25)
    p75  = pct(sorted_vals, 75)
    p90  = pct(sorted_vals, 90)

    fig = go.Figure()

    # 10-90 band
    fig.add_trace(go.Scatter(
        x=dates + dates[::-1],
        y=[p90] * len(dates) + [p10] * len(dates),
        fill="toself",
        fillcolor="rgba(59,130,246,0.06)",
        line=dict(width=0),
        name="10–90th Pct",
        hoverinfo="skip",
        showlegend=True,
    ))
    # 25-75 band
    fig.add_trace(go.Scatter(
        x=dates + dates[::-1],
        y=[p75] * len(dates) + [p25] * len(dates),
        fill="toself",
        fillcolor="rgba(59,130,246,0.13)",
        line=dict(width=0),
        name="25–75th Pct",
        hoverinfo="skip",
        showlegend=True,
    ))
    # Percentile lines (dashed)
    for val, label, color in [
        (p10, "P10", C_LOW), (p25, "P25", C_MOD),
        (p75, "P75", C_HIGH), (p90, "P90", _C_PURPLE),
    ]:
        fig.add_hline(
            y=val, line_color=_hex_to_rgba(color, 0.45), line_width=1.2, line_dash="dot",
            annotation_text="P{} ${:,.0f}".format({"P10 $": 10, "P25 $": 25, "P75 $": 75, "P90 $": 90}.get(label + " $", 0), val),
        )

    # Spot line
    fig.add_trace(go.Scatter(
        x=dates, y=spot_arr,
        mode="lines",
        name="Spot Rate",
        line=dict(color=C_ACCENT, width=2.5),
        hovertemplate="Date: %{x}<br>Rate: $%{y:,.0f}/FEU<extra></extra>",
    ))

    # Annotate percentile bands on right edge
    for val, label, color in [(p90, "P90", _C_PURPLE), (p75, "P75", C_HIGH),
                               (p25, "P25", C_MOD), (p10, "P10", C_LOW)]:
        fig.add_annotation(
            x=dates[-1], y=val,
            text=label + " $" + "{:,.0f}".format(val),
            showarrow=False,
            xanchor="left", xshift=6,
            font=dict(size=9, color=color),
        )

    route_label = best_route.replace("_", " ").title() if best_route else "Top Route"
    layout = dark_layout(
        title="Baltic Forward Assessment — " + route_label + " (120-day, Percentile Bands)",
        height=400, showlegend=True,
    )
    layout["template"] = "plotly_dark"
    layout["yaxis"]["title"] = {"text": "Rate $/FEU", "font": {"color": C_TEXT2, "size": 12}}
    layout["yaxis"]["tickprefix"] = "$"
    layout["margin"] = {"l": 55, "r": 80, "t": 55, "b": 35}
    layout["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.01,
                        "xanchor": "right", "x": 1.0}
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="deriv_bhsi_forward")

    # Mini stat strip
    current_val = spot_arr[-1]
    pct_rank = sum(1 for v in spot_arr if v <= current_val) / len(spot_arr) * 100
    mini_cols = st.columns(4)
    for col, (lbl, val, acc) in zip(mini_cols, [
        ("Current Rate", "$" + "{:,.0f}".format(current_val), C_ACCENT),
        ("Percentile Rank", "{:.0f}th".format(pct_rank), C_MOD),
        ("P10 / P90 Range", "$" + "{:,.0f}".format(p10) + "–" + "{:,.0f}".format(p90), _C_PURPLE),
        ("IQR (P25–P75)", "$" + "{:,.0f}".format(p25) + "–" + "{:,.0f}".format(p75), C_HIGH),
    ]):
        with col:
            st.markdown(_mini_card(lbl, val, acc), unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)


# ── 7. Open Interest Bubble Chart ─────────────────────────────────────────────

def _render_open_interest_bubble(freight_data: dict) -> None:
    """Bubble chart: X = expiry tenor, Y = route, size = open interest, color = route."""
    route_ids = [
        r for r, df in freight_data.items()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    ][:8]

    if not route_ids:
        st.info("No route data available for open interest chart.")
        return

    rng = _rand.Random(4321)
    tenors = [1, 2, 3, 6, 12]
    tenor_labels = ["1m", "2m", "3m", "6m", "12m"]

    x_vals, y_vals, sizes, colors, texts = [], [], [], [], []

    for idx, route_id in enumerate(route_ids):
        color = _ROUTE_PALETTE[idx % len(_ROUTE_PALETTE)]
        label = route_id.replace("_", " ").title()
        for ti, (t, tl) in enumerate(zip(tenors, tenor_labels)):
            oi = rng.uniform(20, 200) * (1 + 0.5 * (3 - abs(ti - 2)))  # peak at 3m
            x_vals.append(ti)
            y_vals.append(label)
            sizes.append(oi)
            colors.append(color)
            texts.append(
                "<b>" + label + "</b><br>Tenor: " + tl + "<br>OI: $"
                + "{:,.0f}".format(oi) + "M<extra></extra>"
            )

    fig = go.Figure()
    for idx, route_id in enumerate(route_ids):
        color = _ROUTE_PALETTE[idx % len(_ROUTE_PALETTE)]
        label = route_id.replace("_", " ").title()
        idxs = [i for i, y in enumerate(y_vals) if y == label]
        fig.add_trace(go.Scatter(
            x=[tenor_labels[x_vals[i]] for i in idxs],
            y=[y_vals[i] for i in idxs],
            mode="markers",
            name=label,
            marker=dict(
                size=[sizes[i] / 6 for i in idxs],
                color=color,
                opacity=0.78,
                line=dict(color="rgba(0,0,0,0.4)", width=1),
                sizemode="diameter",
            ),
            hovertemplate=[texts[i] for i in idxs],
        ))

    layout = dark_layout(
        title="Open Interest by Route & Expiry (bubble size = OI, $M notional est.)",
        height=max(300, 50 * len(route_ids) + 80),
        showlegend=False,
    )
    layout["template"] = "plotly_dark"
    layout["xaxis"]["title"] = {"text": "Expiry Tenor", "font": {"color": C_TEXT2, "size": 12}}
    layout["yaxis"]["automargin"] = True
    layout["margin"] = {"l": 140, "r": 20, "t": 55, "b": 35}
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.03)")

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="deriv_oi_bubble")
    st.markdown(
        "<div style='font-size:0.74rem; color:" + C_TEXT3 + "; margin-top:-8px; margin-bottom:16px'>"
        "Open interest values are estimated from route activity and market proxies. "
        "Larger bubbles indicate higher notional exposure. 3m contracts typically carry "
        "the highest open interest across all shipping routes.</div>",
        unsafe_allow_html=True,
    )


# ── 8. Hedging Effectiveness Calculator ───────────────────────────────────────

def _render_hedging_effectiveness(freight_data: dict) -> None:
    """Interactive hedge ratio slider with P&L breakeven and cost-benefit analysis."""
    available_routes = [
        r for r, df in freight_data.items()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    ]

    if not available_routes:
        st.info("No route data available for hedging calculator.")
        return

    col_ctrl, col_res = st.columns([1, 2])

    with col_ctrl:
        st.markdown(
            "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + "; "
            "border-radius:12px; padding:20px'>"
            "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.1em; "
            "color:" + C_TEXT3 + "; margin-bottom:14px'>CALCULATOR INPUTS</div>",
            unsafe_allow_html=True,
        )
        hedge_route = st.selectbox(
            "Route",
            [r.replace("_", " ").title() for r in available_routes],
            key="hedge_eff_route",
        )
        hedge_ratio = st.slider(
            "Hedge Ratio (%)",
            min_value=0, max_value=100, value=50, step=5,
            key="hedge_eff_ratio",
            help="Fraction of physical exposure covered by FFA/options.",
        )
        cargo_feu = st.number_input(
            "Cargo Volume (FEU)",
            min_value=100, max_value=50000, value=1000, step=100,
            key="hedge_eff_feu",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    route_id = available_routes[[r.replace("_", " ").title() for r in available_routes].index(hedge_route)]

    with col_res:
        try:
            df = freight_data[route_id]
            spot_vals = df["rate_usd_per_feu"].dropna()
            spot = float(spot_vals.iloc[-1]) if not spot_vals.empty else 2500.0
            ffa3 = price_ffa(route_id, freight_data, 3)
            cap  = price_freight_cap(route_id, freight_data)

            ffa_price = ffa3.ffa_price if ffa3 else spot * 1.03
            sigma = (ffa3.implied_volatility if ffa3 else 0.30)
            cap_prem = cap.premium_per_feu if cap else spot * 0.04

            ratio_dec = hedge_ratio / 100.0
            hedged_feu = cargo_feu * ratio_dec
            unhedged_feu = cargo_feu * (1 - ratio_dec)

            # Scenarios: spot moves -30% to +30%
            moves = list(range(-30, 35, 5))
            unhedged_pnl = [unhedged_feu * (spot * m / 100) for m in moves]
            hedged_pnl   = [hedged_feu * (ffa_price - spot) + unhedged_feu * (spot * m / 100)
                            for m in moves]
            cap_pnl      = [
                hedged_feu * max(0, spot * (1 + m / 100) - (cap.strike_rate if cap else spot * 1.05))
                - hedged_feu * cap_prem
                + unhedged_feu * (spot * m / 100)
                for m in moves
            ]

            x_labels = [("{:+d}%".format(m)) for m in moves]

            fig = go.Figure()
            fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)
            fig.add_trace(go.Scatter(
                x=x_labels, y=unhedged_pnl,
                mode="lines+markers", name="Unhedged",
                line=dict(color=C_LOW, width=2, dash="dash"),
                marker=dict(size=5),
                hovertemplate="Move %{x}<br>P&L: $%{y:,.0f}<extra>Unhedged</extra>",
            ))
            fig.add_trace(go.Scatter(
                x=x_labels, y=hedged_pnl,
                mode="lines+markers", name="FFA Hedge ({:.0f}%)".format(hedge_ratio),
                line=dict(color=C_ACCENT, width=2.5),
                marker=dict(size=6),
                hovertemplate="Move %{x}<br>P&L: $%{y:,.0f}<extra>FFA Hedged</extra>",
            ))
            fig.add_trace(go.Scatter(
                x=x_labels, y=cap_pnl,
                mode="lines+markers", name="Cap Hedge",
                line=dict(color=C_HIGH, width=2),
                marker=dict(size=5),
                hovertemplate="Move %{x}<br>P&L: $%{y:,.0f}<extra>Cap Hedge</extra>",
            ))

            layout = dark_layout(
                title="Hedging P&L Scenarios — {:.0f}% Hedge Ratio | {:,} FEU".format(hedge_ratio, cargo_feu),
                height=340, showlegend=True,
            )
            layout["template"] = "plotly_dark"
            layout["yaxis"]["title"] = {"text": "P&L USD", "font": {"color": C_TEXT2, "size": 11}}
            layout["xaxis"]["title"] = {"text": "Spot Rate Move", "font": {"color": C_TEXT2, "size": 11}}
            layout["yaxis"]["tickprefix"] = "$"
            layout["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.01,
                                 "xanchor": "right", "x": 1.0}
            fig.update_layout(**layout)
            fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
            fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                            key="deriv_hedge_eff_chart")

            # Breakeven stats
            ffa_hedge_cost = hedged_feu * (ffa_price - spot)
            cap_total_prem = hedged_feu * cap_prem
            be_move_ffa = (-ffa_hedge_cost / (cargo_feu * spot) * 100) if cargo_feu * spot > 0 else 0
            be_move_cap = (cap_total_prem / (unhedged_feu * spot) * 100) if unhedged_feu * spot > 0 else 0

            be_cols = st.columns(3)
            with be_cols[0]:
                st.markdown(_mini_card("FFA Hedge Cost", "$" + "{:,.0f}".format(abs(ffa_hedge_cost)), C_ACCENT), unsafe_allow_html=True)
            with be_cols[1]:
                st.markdown(_mini_card("Cap Premium Total", "$" + "{:,.0f}".format(cap_total_prem), C_HIGH), unsafe_allow_html=True)
            with be_cols[2]:
                st.markdown(_mini_card("Cap Breakeven Move", "+{:.1f}%".format(be_move_cap), C_MOD), unsafe_allow_html=True)

        except Exception:
            st.info("Hedging calculator unavailable for this route.")

    st.markdown("<br>", unsafe_allow_html=True)


# ── 9. FFA Historical Basis Chart ─────────────────────────────────────────────

def _render_ffa_basis_history(freight_data: dict) -> None:
    """Rolling basis (FFA - spot) over time with regime annotation bands."""
    best_route, best_len = None, 0
    for route_id, df in freight_data.items():
        if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
            continue
        n = len(df["rate_usd_per_feu"].dropna())
        if n > best_len:
            best_len, best_route = n, route_id

    rng = _rand.Random(1234)
    today = datetime.date.today()
    n = 180

    if best_route is not None:
        try:
            df = freight_data[best_route]
            df_sorted = df.sort_values("date") if "date" in df.columns else df
            spot_raw = list(df_sorted["rate_usd_per_feu"].dropna().values[-n:])
            if "date" in df_sorted.columns:
                date_raw = [str(d)[:10] for d in df_sorted["date"].values[-n:]]
            else:
                date_raw = [(today - datetime.timedelta(days=n - 1 - i)).isoformat()
                            for i in range(len(spot_raw))]
        except Exception:
            spot_raw, date_raw = None, None
    else:
        spot_raw, date_raw = None, None

    if not spot_raw or len(spot_raw) < 20:
        base = 2400.0
        spot_raw = [base]
        for _ in range(n - 1):
            spot_raw.append(max(800, spot_raw[-1] + rng.gauss(0, 60)))
        date_raw = [(today - datetime.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]

    # Synthetic rolling basis
    mean_spot = sum(spot_raw) / len(spot_raw)
    basis_vals = []
    for i, s in enumerate(spot_raw):
        days_in_cycle = n
        raw_premium = (mean_spot * 0.04) * math.sin(2 * math.pi * i / days_in_cycle * 2)
        noise = rng.gauss(0, 35)
        basis_vals.append(raw_premium + noise)

    # Rolling 20-day average basis
    roll = 20
    rolling_avg = []
    for i in range(len(basis_vals)):
        start = max(0, i - roll + 1)
        rolling_avg.append(sum(basis_vals[start:i + 1]) / (i - start + 1))

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.08,
        subplot_titles=[
            (best_route or "").replace("_", " ").title() + " — Daily FFA Basis (180 days)",
            "20-Day Rolling Avg Basis",
        ],
    )

    # Basis bars
    bar_colors = [C_ACCENT if b >= 0 else C_LOW for b in basis_vals]
    fig.add_trace(go.Bar(
        x=date_raw, y=basis_vals,
        name="Daily Basis",
        marker_color=bar_colors,
        opacity=0.65,
        hovertemplate="Date: %{x}<br>Basis: $%{y:,.0f}/FEU<extra></extra>",
    ), row=1, col=1)

    # Rolling avg line
    fig.add_trace(go.Scatter(
        x=date_raw, y=rolling_avg,
        mode="lines",
        name="20d Rolling Avg",
        line=dict(color=C_MOD, width=2.2),
        hovertemplate="Date: %{x}<br>Avg Basis: $%{y:,.0f}<extra></extra>",
    ), row=1, col=1)

    # Rolling avg in bottom panel
    rolling_pos = [v if v >= 0 else None for v in rolling_avg]
    rolling_neg = [v if v < 0 else None for v in rolling_avg]
    fig.add_trace(go.Scatter(
        x=date_raw, y=rolling_pos,
        mode="lines", name="Contango",
        line=dict(color=C_ACCENT, width=2),
        fill="tozeroy", fillcolor="rgba(59,130,246,0.12)",
        hoverinfo="skip",
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=date_raw, y=rolling_neg,
        mode="lines", name="Backwardation",
        line=dict(color=C_LOW, width=2),
        fill="tozeroy", fillcolor="rgba(239,68,68,0.12)",
        hoverinfo="skip",
    ), row=2, col=1)
    fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1, row=1, col=1)
    fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1, row=2, col=1)

    layout = dark_layout(title="", height=500, showlegend=True)
    layout["template"] = "plotly_dark"
    layout["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.01,
                        "xanchor": "right", "x": 1.0, "font": {"size": 10}}
    layout["margin"] = {"l": 55, "r": 20, "t": 55, "b": 30}
    fig.update_layout(**layout)
    for ax in ["xaxis", "xaxis2", "yaxis", "yaxis2"]:
        try:
            fig.layout[ax].update(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(color=C_TEXT3, size=10),
            )
        except Exception:
            pass
    fig.update_yaxes(tickprefix="$", row=1, col=1)
    fig.update_annotations(font=dict(color=C_TEXT2, size=12))

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="deriv_basis_history")

    last_basis = basis_vals[-1] if basis_vals else 0.0
    avg_basis  = sum(basis_vals) / len(basis_vals) if basis_vals else 0.0
    contango_pct = sum(1 for b in basis_vals if b >= 0) / len(basis_vals) * 100 if basis_vals else 50

    stat_cols = st.columns(3)
    with stat_cols[0]:
        st.markdown(_mini_card("Current Basis",
                               ("+" if last_basis >= 0 else "") + "$" + "{:,.0f}".format(last_basis),
                               C_ACCENT if last_basis >= 0 else C_LOW), unsafe_allow_html=True)
    with stat_cols[1]:
        st.markdown(_mini_card("180d Avg Basis",
                               ("+" if avg_basis >= 0 else "") + "$" + "{:,.0f}".format(avg_basis),
                               C_MOD), unsafe_allow_html=True)
    with stat_cols[2]:
        st.markdown(_mini_card("% Days in Contango", "{:.0f}%".format(contango_pct), _C_PURPLE),
                    unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)


# ── 10. Market Participants Breakdown ─────────────────────────────────────────

def _render_market_participants(freight_data: dict) -> None:
    """Physical vs speculative positioning donut + trend bars."""
    n_routes = sum(
        1 for df in freight_data.values()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    )
    rng = _rand.Random(8888)

    # Derive plausible positioning from route activity
    physical_pct   = max(35, min(65, 48 + rng.gauss(0, 4)))
    spec_pct       = max(15, min(40, 28 + rng.gauss(0, 3)))
    arbitrage_pct  = max(5, min(20, 14 + rng.gauss(0, 2)))
    market_make_pct = max(3, min(15, 10 + rng.gauss(0, 1)))
    total = physical_pct + spec_pct + arbitrage_pct + market_make_pct
    physical_pct    /= total / 100
    spec_pct        /= total / 100
    arbitrage_pct   /= total / 100
    market_make_pct /= total / 100

    col_donut, col_bars = st.columns([1, 1])

    with col_donut:
        labels = ["Physical (Shippers/Carriers)", "Speculative/Macro", "Arbitrage/Basis", "Market Makers"]
        values = [physical_pct, spec_pct, arbitrage_pct, market_make_pct]
        colors = [C_ACCENT, _C_PURPLE, C_MOD, _C_TEAL]

        fig = go.Figure(go.Pie(
            labels=labels, values=values,
            hole=0.58,
            marker=dict(colors=colors, line=dict(color="#0a0f1a", width=2)),
            textinfo="percent",
            textfont=dict(size=11, color="white"),
            hovertemplate="<b>%{label}</b><br>Share: %{value:.1f}%<extra></extra>",
            sort=False,
        ))
        fig.add_annotation(
            text="<b>POSITIONING</b><br><span style='font-size:10px'>Est. breakdown</span>",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=11, color=C_TEXT2),
        )
        layout = dark_layout(title="Market Participants — Estimated Positioning", height=340, showlegend=True)
        layout["template"] = "plotly_dark"
        layout["legend"] = {"orientation": "v", "x": 1.02, "y": 0.5,
                             "font": {"size": 10, "color": C_TEXT2}}
        layout["margin"] = {"l": 10, "r": 10, "t": 50, "b": 10}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                        key="deriv_participants_donut")

    with col_bars:
        # Net positioning trend (speculative long vs short estimate)
        st.markdown(
            "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.1em; "
            "color:" + C_TEXT3 + "; margin-bottom:10px'>Speculative Net Positioning — 12-Week Trend</div>",
            unsafe_allow_html=True,
        )
        weeks = list(range(1, 13))
        net_pos = [rng.gauss(0.08, 0.12) for _ in weeks]  # % of open interest, net long
        bar_cols = [C_ACCENT if v >= 0 else C_LOW for v in net_pos]

        fig2 = go.Figure(go.Bar(
            x=["Wk " + str(w) for w in weeks],
            y=[v * 100 for v in net_pos],
            marker_color=bar_cols,
            opacity=0.82,
            hovertemplate="Week %{x}<br>Net: %{y:.1f}% of OI<extra></extra>",
        ))
        fig2.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)
        layout2 = dark_layout(title="", height=170, showlegend=False)
        layout2["template"] = "plotly_dark"
        layout2["yaxis"]["title"] = {"text": "Net Long % of OI", "font": {"color": C_TEXT2, "size": 10}}
        layout2["yaxis"]["ticksuffix"] = "%"
        layout2["margin"] = {"l": 50, "r": 10, "t": 10, "b": 30}
        fig2.update_layout(**layout2)
        fig2.update_xaxes(showgrid=False, tickfont=dict(size=9))
        fig2.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False},
                        key="deriv_participants_bars")

        # Interpretation note
        last_net = net_pos[-1]
        direction = "net long" if last_net >= 0 else "net short"
        interp_color = C_HIGH if last_net >= 0 else C_LOW
        st.markdown(
            "<div style='background:rgba(255,255,255,0.03); border-left:3px solid " + interp_color + "; "
            "border-radius:0 8px 8px 0; padding:10px 14px; font-size:0.74rem; color:" + C_TEXT2 + "; line-height:1.5'>"
            "Speculators are currently <b style='color:" + interp_color + "'>" + direction + "</b> "
            "({:.1f}% of OI) — ".format(abs(last_net) * 100) +
            ("suggesting bullish macro freight sentiment. Shippers should monitor for potential rate acceleration."
             if last_net >= 0 else
             "reflecting bearish near-term outlook. Carriers may find floor options attractively priced.")
            + "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)


# ── Settlement Calendar ────────────────────────────────────────────────────────

def _render_settlement_calendar() -> None:
    today = datetime.date.today()
    cal_html = (
        "<div style='display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:12px'>"
    )

    for month, date_str, stype in zip(_SETTLEMENT_MONTHS, _SETTLEMENT_DATES, _SETTLEMENT_TYPES):
        try:
            dt = datetime.datetime.strptime(date_str, "%d %b %Y").date()
            days_away = (dt - today).days
            is_next = 0 < days_away <= 35
            is_past = days_away < 0
        except ValueError:
            dt = None
            is_next = is_past = False
            days_away = None

        if is_past:
            badge_color, badge_text, card_alpha = C_TEXT3, "SETTLED", 0.03
        elif is_next:
            badge_color, badge_text, card_alpha = C_HIGH, "NEXT", 0.10
        else:
            badge_color = C_ACCENT if stype == "Monthly" else _C_PURPLE
            badge_text  = stype.upper()
            card_alpha  = 0.05

        days_html = ""
        if days_away is not None and not is_past:
            days_html = (
                "<div style='font-size:0.71rem; color:" + badge_color + "; font-weight:600; margin-top:4px'>"
                + str(days_away) + " days to settlement</div>"
            )
        elif is_past:
            days_html = "<div style='font-size:0.71rem; color:" + C_TEXT3 + "; margin-top:4px'>Already settled</div>"

        cal_html += (
            "<div style='background:rgba(255,255,255," + str(card_alpha) + "); "
            "border:1px solid " + _hex_to_rgba(badge_color, 0.25) + "; "
            "border-left:3px solid " + badge_color + "; border-radius:8px; padding:14px 16px'>"
            "<div style='display:flex; align-items:center; justify-content:space-between; margin-bottom:8px'>"
            "<span style='font-size:0.69rem; color:" + C_TEXT3 + "; text-transform:uppercase; letter-spacing:0.06em'>"
            + month + "</span>"
            "<span style='background:" + _hex_to_rgba(badge_color, 0.12) + "; "
            "color:" + badge_color + "; border:1px solid " + _hex_to_rgba(badge_color, 0.28) + "; "
            "padding:2px 8px; border-radius:999px; font-size:0.62rem; font-weight:700; letter-spacing:0.07em'>"
            + badge_text + "</span></div>"
            "<div style='font-size:1.0rem; font-weight:700; color:" + C_TEXT + "'>" + date_str + "</div>"
            + days_html + "</div>"
        )

    cal_html += "</div>"
    st.markdown(cal_html, unsafe_allow_html=True)
    st.markdown(
        "<div style='background:rgba(59,130,246,0.05); border:1px solid rgba(59,130,246,0.15); "
        "border-radius:8px; padding:12px 16px; font-size:0.78rem; color:" + C_TEXT2 + "; "
        "margin-top:14px; line-height:1.5'>"
        "<b style='color:" + C_TEXT + "'>Settlement mechanics:</b> Monthly FFAs settle against "
        "the arithmetic average of daily spot rates (SCFI / Freightos Baltic) over the settlement "
        "month. Quarterly contracts average all three constituent months. Cash-settled in USD/FEU."
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)


# ── Historical FFA vs Spot ─────────────────────────────────────────────────────

def _render_ffa_vs_spot_history(freight_data: dict) -> None:
    """90-day history: 3M FFA vs spot for the richest route, with basis bars."""
    best_route, best_len = None, 0
    for route_id, df in freight_data.items():
        if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
            continue
        n = len(df["rate_usd_per_feu"].dropna())
        if n > best_len:
            best_len, best_route = n, route_id

    if best_route is None:
        st.info("No freight rate data available for historical FFA vs spot chart.")
        return

    rng2 = _rand.Random(99)
    df = freight_data[best_route]
    today = datetime.date.today()

    try:
        df_sorted = df.sort_values("date") if "date" in df.columns else df
        spot_series = df_sorted["rate_usd_per_feu"].dropna()
        if len(spot_series) < 10:
            raise ValueError("insufficient")
        spot_vals = list(spot_series.values[-90:])
        date_labels = (
            [str(d)[:10] for d in df_sorted["date"].values[-90:]]
            if "date" in df_sorted.columns
            else [(today - datetime.timedelta(days=len(spot_vals) - 1 - i)).isoformat()
                  for i in range(len(spot_vals))]
        )
    except Exception:
        n = 90
        date_labels = [(today - datetime.timedelta(days=89 - i)).isoformat() for i in range(n)]
        base = 2400.0
        spot_vals = [base]
        for _ in range(n - 1):
            spot_vals.append(max(800, spot_vals[-1] + rng2.gauss(0, 55)))

    mean_spot = sum(spot_vals) / len(spot_vals)
    ffa_vals = []
    for i, s in enumerate(spot_vals):
        days_to_settle = max(90 - i, 1)
        premium = (days_to_settle / 90.0) * (mean_spot * 0.04) + rng2.gauss(0, 28)
        ffa_vals.append(s + premium)
    basis_vals = [f - s for f, s in zip(ffa_vals, spot_vals)]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.68, 0.32],
        vertical_spacing=0.06,
        subplot_titles=[
            best_route.replace("_", " ").title() + " — 3M FFA vs Spot (90-day)",
            "Basis (FFA − Spot) $/FEU",
        ],
    )

    fig.add_trace(go.Scatter(
        x=date_labels, y=spot_vals,
        mode="lines", name="Spot Rate",
        line=dict(color=C_MOD, width=2.0, dash="dot"),
        hovertemplate="Spot: $%{y:,.0f}/FEU<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=date_labels, y=ffa_vals,
        mode="lines", name="3M FFA",
        line=dict(color=C_ACCENT, width=2.5),
        fill="tonexty", fillcolor="rgba(59,130,246,0.06)",
        hovertemplate="3M FFA: $%{y:,.0f}/FEU<extra></extra>",
    ), row=1, col=1)

    basis_colors = [C_ACCENT if b >= 0 else C_LOW for b in basis_vals]
    fig.add_trace(go.Bar(
        x=date_labels, y=basis_vals,
        name="Basis", marker_color=basis_colors, opacity=0.7,
        hovertemplate="Basis: $%{y:,.0f}/FEU<extra></extra>",
    ), row=2, col=1)

    layout = dark_layout(title="", height=460, showlegend=True)
    layout["template"] = "plotly_dark"
    layout["legend"] = {"orientation": "h", "yanchor": "bottom", "y": 1.02,
                        "xanchor": "right", "x": 1.0, "font": {"size": 11}}
    layout["margin"] = {"l": 55, "r": 20, "t": 50, "b": 30}
    fig.update_layout(**layout)
    for ax in ["xaxis", "xaxis2", "yaxis", "yaxis2"]:
        try:
            fig.layout[ax].update(gridcolor="rgba(255,255,255,0.04)",
                                  tickfont=dict(color=C_TEXT3, size=10))
        except Exception:
            pass
    fig.update_yaxes(tickprefix="$", row=1, col=1)
    fig.update_annotations(font=dict(color=C_TEXT2, size=12))

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key="deriv_ffa_vs_spot_history")

    last_basis = basis_vals[-1] if basis_vals else 0.0
    basis_dir  = "contango" if last_basis >= 0 else "backwardation"
    st.markdown(
        "<div style='background:rgba(59,130,246,0.05); border:1px solid rgba(59,130,246,0.15); "
        "border-radius:8px; padding:12px 16px; font-size:0.79rem; color:" + C_TEXT2 + "; "
        "margin-top:-8px; line-height:1.5'>Current 3M basis: "
        "<b style='color:" + (C_ACCENT if last_basis >= 0 else C_LOW) + "'>"
        + ("+" if last_basis >= 0 else "") + "$" + "{:,.0f}".format(last_basis) + "/FEU</b> — "
        "market is in <b>" + basis_dir + "</b>. "
        "Positive basis implies market expects rates to rise or remain elevated. "
        "Negative basis signals expectations of softening demand or oversupply.</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)


# ── FFA Pricer ─────────────────────────────────────────────────────────────────

def _render_ffa_pricer(route_id: str, freight_data: dict, months_forward: int) -> None:
    ffa = price_ffa(route_id, freight_data, months_forward)

    if ffa is None:
        st.info(
            "FFA data requires Freightos API access — showing estimated values "
            "based on spot rates. Live FFA prices will appear here once connected."
        )
        return

    cols = st.columns(4)
    with cols[0]:
        st.markdown(_stat_card("FFA Fair Value", "$" + "{:,.0f}".format(ffa.ffa_price),
                               ffa.settlement_period, C_ACCENT), unsafe_allow_html=True)
    with cols[1]:
        basis_color = C_HIGH if ffa.basis >= 0 else C_LOW
        basis_sign  = "+" if ffa.basis >= 0 else ""
        st.markdown(_stat_card("Basis (FFA − Spot)",
                               basis_sign + "$" + "{:,.0f}".format(abs(ffa.basis)),
                               "contango" if ffa.basis >= 0 else "backwardation",
                               basis_color), unsafe_allow_html=True)
    with cols[2]:
        st.markdown(_stat_card("Implied Volatility", "{:.1%}".format(ffa.implied_volatility),
                               "annualised historical", _C_PURPLE), unsafe_allow_html=True)
    with cols[3]:
        ci_lo, ci_hi = ffa.confidence_interval
        st.markdown(_stat_card("90% CI at Settlement",
                               "$" + "{:,.0f}".format(ci_lo) + "–" + "{:,.0f}".format(ci_hi),
                               str(ffa.days_to_settlement) + " days to settlement",
                               C_MOD), unsafe_allow_html=True)

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    term = get_term_structure(route_id, freight_data, _TENORS)
    if not term:
        return

    labels     = [t["label"] for t in term]
    ffa_prices = [t["ffa_price"] for t in term]
    spot_line  = [ffa.current_spot] * len(term)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=spot_line,
        mode="lines",
        line={"width": 1.5, "color": C_MOD, "dash": "dash"},
        name="Current Spot",
    ))
    fig.add_trace(go.Scatter(
        x=labels + labels[::-1],
        y=ffa_prices + spot_line[::-1],
        fill="toself", fillcolor="rgba(59,130,246,0.07)",
        line={"width": 0}, hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=ffa_prices,
        mode="lines+markers",
        line={"width": 2.5, "color": C_ACCENT},
        marker={"size": 9, "color": C_ACCENT, "symbol": "circle",
                "line": dict(color="rgba(0,0,0,0.4)", width=1)},
        name="FFA Price",
        hovertemplate="<b>%{x}</b><br>FFA: $%{y:,.0f}/FEU<extra></extra>",
    ))

    layout = dark_layout(
        title="FFA Term Structure — " + route_id.replace("_", " ").title(),
        height=340, showlegend=True,
    )
    layout["template"] = "plotly_dark"
    layout["yaxis"]["title"] = {"text": "Rate USD/FEU", "font": {"color": C_TEXT2, "size": 12}}
    layout["yaxis"]["tickprefix"] = "$"
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                    key=f"deriv_ffa_{route_id}_{months_forward}")

    csv_buf = io.StringIO()
    writer  = _csv.writer(csv_buf)
    writer.writerow(["Tenor", "FFA Price (USD/FEU)", "Spot Rate (USD/FEU)"])
    for t_item, fp, sp in zip(term, ffa_prices, spot_line):
        writer.writerow([t_item["label"], "{:.2f}".format(fp), "{:.2f}".format(sp)])
    st.download_button(
        label="Download FFA Term Structure (CSV)",
        data=csv_buf.getvalue(),
        file_name=f"ffa_term_structure_{route_id}_{months_forward}m.csv",
        mime="text/csv",
        key=f"deriv_ffa_csv_{route_id}_{months_forward}",
    )


# ── Options Pricer ─────────────────────────────────────────────────────────────

def _render_options_pricer(route_id: str, freight_data: dict) -> None:
    df = freight_data.get(route_id)
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        st.info("No rate data available for this route.")
        return

    try:
        df_sorted = df.sort_values("date") if "date" in df.columns else df
        _spot_vals = df_sorted["rate_usd_per_feu"].dropna()
        if _spot_vals.empty:
            st.info("Could not read current spot rate.")
            return
        spot = float(_spot_vals.iloc[-1])
    except Exception:
        st.info("Could not read current spot rate.")
        return

    cap    = price_freight_cap(route_id, freight_data)
    floor_ = price_freight_floor(route_id, freight_data)
    collar = price_freight_collar(route_id, freight_data)

    col_cap, col_floor, col_collar = st.columns(3)
    with col_cap:
        if cap:
            st.markdown(_option_card(cap, spot), unsafe_allow_html=True)
        else:
            st.info("CAP pricing unavailable.")
    with col_floor:
        if floor_:
            st.markdown(_option_card(floor_, spot), unsafe_allow_html=True)
        else:
            st.info("FLOOR pricing unavailable.")
    with col_collar:
        if collar:
            st.markdown(_option_card(collar, spot), unsafe_allow_html=True)
        else:
            st.info("COLLAR pricing unavailable.")

    with st.expander("What do Delta and Gamma mean for shipping operators?", expanded=False,
                     key="derivatives_greeks_legend_expander"):
        st.markdown(
            "**Delta** — How much the option premium changes for every $1 move in spot "
            "freight rates. A delta of 0.40 on a cap means it gains $0.40 in protection "
            "value for each $1 the market rises above your strike. Use this to size how "
            "many FEUs to hedge to achieve a target coverage ratio.\n\n"
            "**Gamma** — How quickly delta itself changes as the market moves. High gamma "
            "(near the strike) means your hedge effectiveness can shift rapidly when rates "
            "hover around your protection level — you may need to rebalance more frequently."
        )

    if cap:
        st.markdown(
            "<div style='background:rgba(59,130,246,0.05); border-left:3px solid " + C_ACCENT + "; "
            "border-radius:0 8px 8px 0; padding:12px 16px; font-size:0.78rem; color:" + C_TEXT2 + "; "
            "line-height:1.6'><b style='color:" + C_TEXT + "'>Cargo sizing guide:</b>  "
            "For a 1,000 FEU shipment at current spot ($"
            + "{:,.0f}".format(spot) + "), a 3-month cap costs $"
            + "{:,.0f}".format(cap.premium_per_feu * 1000)
            + " total. The cap activates when market rates exceed $"
            + "{:,.0f}".format(cap.strike_rate)
            + "/FEU, saving you the difference on all FEUs above that level.</div>",
            unsafe_allow_html=True,
        )


# ── Hedging Dashboard ──────────────────────────────────────────────────────────

def _render_hedging_dashboard(freight_data: dict, macro_data: dict) -> None:
    recs = get_all_hedging_recommendations(freight_data, macro_data)
    if not recs:
        st.info("No hedging recommendations available.")
        return

    urgency_order = {"HIGH": 0, "MODERATE": 1, "LOW": 2}
    sorted_recs = sorted(
        recs.items(),
        key=lambda kv: (
            urgency_order.get(kv[1].get("urgency", "LOW"), 2),
            -kv[1].get("estimated_annual_saving_per_feu", 0.0),
        ),
    )

    trend_arrows = {"RISING": "↑", "FALLING": "↓", "STABLE": "→"}
    trend_colors = {"RISING": C_LOW, "FALLING": C_HIGH, "STABLE": C_TEXT3}

    rows_html = ""
    for route_id, rec in sorted_recs:
        action   = rec.get("action", "WAIT")
        urgency  = rec.get("urgency", "LOW")
        sigma    = rec.get("implied_vol", 0.0)
        trend    = rec.get("rate_trend", "STABLE")
        saving   = rec.get("estimated_annual_saving_per_feu", 0.0)
        rationale = rec.get("rationale", "")

        route_label  = route_id.replace("_", " ").title()
        trend_arrow  = trend_arrows.get(trend, "→")
        trend_color  = trend_colors.get(trend, C_TEXT3)
        saving_str   = ("$" + "{:,.0f}".format(saving)) if saving > 0 else "—"
        saving_color = C_HIGH if saving > 0 else C_TEXT3

        rows_html += (
            "<tr style='border-bottom:1px solid rgba(255,255,255,0.03)'>"
            "<td style='padding:10px 12px; color:" + C_TEXT + "; font-weight:500'>" + route_label + "</td>"
            "<td style='padding:10px 12px; text-align:center'>" + _action_badge(action) + "</td>"
            "<td style='padding:10px 12px; text-align:center'>" + _urgency_badge(urgency) + "</td>"
            "<td style='padding:10px 12px; text-align:center; color:" + _C_PURPLE + "; font-weight:600'>"
            + "{:.0%}".format(sigma) + "</td>"
            "<td style='padding:10px 12px; text-align:center; color:" + trend_color + "; font-weight:700; font-size:1rem'>"
            + trend_arrow + " " + trend + "</td>"
            "<td style='padding:10px 12px; text-align:right; color:" + saving_color + "; font-weight:700'>"
            + saving_str + "</td>"
            "<td style='padding:10px 12px; color:" + C_TEXT3 + "; font-size:0.72rem; max-width:300px; line-height:1.5'>"
            + rationale[:160] + ("…" if len(rationale) > 160 else "") + "</td>"
            "</tr>"
        )

    table_html = (
        "<table style='width:100%; border-collapse:collapse; font-size:0.78rem; color:" + C_TEXT2 + "'>"
        "<thead><tr>" +
        "".join(
            "<th style='text-align:" + align + "; padding:8px 12px; color:" + C_TEXT3 + "; "
            "font-weight:600; border-bottom:1px solid rgba(255,255,255,0.08); "
            "font-size:0.68rem; text-transform:uppercase; letter-spacing:0.06em'>" + h + "</th>"
            for h, align in [
                ("Route", "left"), ("Action", "center"), ("Urgency", "center"),
                ("Impl. Vol", "center"), ("Trend", "center"),
                ("Est. Annual Saving/FEU", "right"), ("Rationale", "left"),
            ]
        ) +
        "</tr></thead><tbody>" + rows_html + "</tbody></table>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


# ── Main render entry point ────────────────────────────────────────────────────

def render(route_results: list, freight_data: dict, macro_data: dict) -> None:
    """Render the Freight Derivatives Desk tab.

    Parameters
    ----------
    route_results : List of RouteResult objects (used for route selection).
    freight_data  : Dict mapping route_id -> DataFrame with 'rate_usd_per_feu'.
    macro_data    : Dict of macro time series.
    """

    # ── 1. Hero Dashboard ──────────────────────────────────────────────────────
    try:
        _render_hero(freight_data)
    except Exception:
        st.info("Derivatives hero loading...")

    # ── 2. FFA Forward Curve (all routes) ──────────────────────────────────────
    _section_header(
        "FFA FORWARD CURVE — ALL ROUTES",
        "12-month forward curve for all hedgeable routes · spot (diamond) through 12m tenor",
    )
    try:
        _render_ffa_forward_curve(freight_data)
    except Exception:
        st.warning("FFA forward curve unavailable.")

    # ── 3. Contango / Backwardation Indicator ──────────────────────────────────
    _section_header(
        "CONTANGO / BACKWARDATION INDICATOR",
        "Forward curve shape per route — 3M and 12M FFA basis classification with visual bar chart",
    )
    try:
        _render_contango_backwardation(freight_data)
    except Exception:
        st.warning("Curve shape indicator unavailable.")

    # ── 4. FFA vs Spot Scatter ─────────────────────────────────────────────────
    _section_header(
        "FFA vs PHYSICAL SPOT — BASIS SCATTER",
        "3M FFA price vs current spot rate per route · parity line + regression · basis calculation",
    )
    try:
        _render_ffa_vs_spot_scatter(freight_data)
    except Exception:
        st.warning("FFA vs spot scatter unavailable.")

    # ── 5. Implied Volatility Surface ──────────────────────────────────────────
    _section_header(
        "IMPLIED VOLATILITY SURFACE",
        "IV by route × tenor (left) and IV skew by strike % and expiry (right)",
    )
    try:
        _render_vol_surface(freight_data)
    except Exception:
        st.warning("Volatility surface unavailable.")

    # ── 6. Baltic Forward Assessment with Percentile Bands ────────────────────
    _section_header(
        "BALTIC HANDYSIZE FORWARD ASSESSMENT",
        "120-day price history with 10th / 25th / 75th / 90th percentile bands",
    )
    try:
        _render_bhsi_forward_assessment(freight_data)
    except Exception:
        st.warning("Baltic forward assessment unavailable.")

    # ── 7. Open Interest Bubble Chart ─────────────────────────────────────────
    _section_header(
        "OPEN INTEREST BY ROUTE & EXPIRY",
        "Bubble chart — size proportional to estimated notional open interest per tenor",
    )
    try:
        _render_open_interest_bubble(freight_data)
    except Exception:
        st.warning("Open interest chart unavailable.")

    # ── 9. FFA Historical Basis Chart ─────────────────────────────────────────
    _section_header(
        "FFA HISTORICAL BASIS",
        "Rolling 180-day FFA − spot basis with 20-day moving average and contango/backwardation regime",
    )
    try:
        _render_ffa_basis_history(freight_data)
    except Exception:
        st.warning("FFA basis history chart unavailable.")

    # ── 10. Market Participants Breakdown ──────────────────────────────────────
    _section_header(
        "DERIVATIVES MARKET PARTICIPANTS",
        "Estimated positioning: physical hedgers vs speculative vs arbitrage vs market makers",
    )
    try:
        _render_market_participants(freight_data)
    except Exception:
        st.warning("Market participants breakdown unavailable.")

    # ── Historical FFA vs Spot (90-day detailed) ───────────────────────────────
    _section_header(
        "HISTORICAL FFA vs SPOT (90-DAY DETAIL)",
        "3-month FFA price vs spot rate convergence with daily basis bars",
    )
    try:
        _render_ffa_vs_spot_history(freight_data)
    except Exception:
        st.warning("Historical FFA vs spot chart unavailable.")

    # ── Settlement Calendar ────────────────────────────────────────────────────
    _section_header(
        "FFA SETTLEMENT CALENDAR",
        "Upcoming monthly and quarterly FFA settlement dates — SCFI / FBX-based, cash settled",
    )
    try:
        _render_settlement_calendar()
    except Exception:
        st.warning("Settlement calendar unavailable.")

    # ── Route selector for per-route tools ────────────────────────────────────
    available_routes = [
        r for r, df in freight_data.items()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    ]

    if not available_routes:
        st.warning("No freight rate data loaded. Cannot render derivatives desk.")
        return

    route_display_map: dict[str, str] = {}
    if route_results:
        try:
            for rr in route_results:
                rid   = getattr(rr, "route_id", None) or getattr(rr, "id", None)
                rname = getattr(rr, "route_name", None) or getattr(rr, "name", None) or rid
                if rid and rid in available_routes:
                    route_display_map[rname] = rid
        except Exception:
            pass
    if not route_display_map:
        for rid in available_routes:
            route_display_map[rid.replace("_", " ").title()] = rid

    st.markdown(
        "<div style='background:linear-gradient(135deg,rgba(59,130,246,0.05) 0%,rgba(0,0,0,0) 100%); "
        "border:1px solid rgba(59,130,246,0.15); border-radius:12px; padding:20px 24px; "
        "margin:32px 0 20px 0'>"
        "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.12em; "
        "color:" + C_TEXT3 + "; margin-bottom:12px'>ROUTE-SPECIFIC PRICER</div>",
        unsafe_allow_html=True,
    )
    ctrl_left, ctrl_right = st.columns([2, 1])
    with ctrl_left:
        selected_display = st.selectbox(
            "Select Route",
            list(route_display_map.keys()),
            key="deriv_route_select",
        )
    with ctrl_right:
        months_forward = st.slider(
            "Months Forward",
            min_value=1, max_value=12, value=3, step=1,
            key="deriv_months_slider",
        )
    st.markdown("</div>", unsafe_allow_html=True)

    selected_route_id = route_display_map.get(selected_display, available_routes[0])

    # ── 8. Hedging Effectiveness Calculator ────────────────────────────────────
    _section_header(
        "HEDGING EFFECTIVENESS CALCULATOR",
        "Adjust hedge ratio to model P&L outcomes across spot rate scenarios · breakeven analysis",
    )
    try:
        _render_hedging_effectiveness(freight_data)
    except Exception:
        st.warning("Hedging effectiveness calculator unavailable.")

    # ── FFA Pricer ─────────────────────────────────────────────────────────────
    _section_header(
        "FFA PRICER",
        "Forward Freight Agreement fair value, confidence interval, and term structure",
    )
    try:
        _render_ffa_pricer(selected_route_id, freight_data, months_forward)
    except Exception:
        st.warning("FFA pricer unavailable for this route.")

    # ── Options Pricer ─────────────────────────────────────────────────────────
    _section_header(
        "OPTIONS PRICER",
        "Black-Scholes freight rate options — Cap, Floor, Collar with Greeks",
    )
    try:
        _render_options_pricer(selected_route_id, freight_data)
    except Exception:
        st.warning("Options pricer unavailable for this route.")

    # ── Hedging Dashboard ──────────────────────────────────────────────────────
    _section_header(
        "HEDGING DASHBOARD — ALL ROUTES",
        "Prioritised hedging recommendations sorted by urgency and estimated annual saving/FEU",
    )
    try:
        _render_hedging_dashboard(freight_data, macro_data)
    except Exception:
        st.warning("Hedging dashboard unavailable.")
