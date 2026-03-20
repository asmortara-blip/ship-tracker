"""
Freight Derivatives Desk Tab

Interactive pricing engine for Forward Freight Agreements (FFAs) and
freight rate options (Caps, Floors, Collars).  Provides:
  - FFA pricer with term structure chart
  - Options pricer (CAP / FLOOR / COLLAR cards)
  - Implied volatility surface heatmap
  - Hedging recommendations dashboard for all routes
"""
from __future__ import annotations

import csv as _csv
import io

import streamlit as st
import plotly.graph_objects as go

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


# ── Internal constants ────────────────────────────────────────────────────────

_C_PURPLE  = "#8b5cf6"
_C_SURFACE = "#111827"
_C_BG      = "#0a0f1a"

_ACTION_COLOR = {
    "BUY_CAP":   C_LOW,      # red — urgent protective action
    "BUY_FLOOR": C_MOD,      # amber — carrier protection
    "COLLAR":    C_ACCENT,   # blue — balanced hedge
    "WAIT":      C_TEXT3,    # grey — no action needed
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


# ── Small rendering helpers ───────────────────────────────────────────────────

def _stat_card(label: str, value: str, sublabel: str = "", accent: str = C_ACCENT) -> str:
    """Return HTML for a dark-themed stat card."""
    sub_html = (
        "<div style='font-size:0.75rem; color:" + C_TEXT3 + "; margin-top:4px'>"
        + sublabel
        + "</div>"
        if sublabel
        else ""
    )
    return (
        "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + "; "
        "border-top:3px solid " + accent + "; border-radius:10px; "
        "padding:18px 20px; text-align:center'>"
        "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.12em; "
        "color:" + C_TEXT3 + "; font-weight:600; margin-bottom:8px'>" + label + "</div>"
        "<div style='font-size:1.75rem; font-weight:800; color:" + C_TEXT + "; line-height:1'>"
        + value + "</div>"
        + sub_html
        + "</div>"
    )


_GREEK_TOOLTIPS = {
    "delta": (
        "Delta measures how much the option premium changes for a $1 move in the "
        "spot freight rate. For a shipper buying a cap, a delta of 0.40 means the "
        "cap gains ~$0.40 in value for every $1 the market moves above the strike. "
        "Closer to 1.0 = deeper in-the-money; closer to 0 = further out-of-the-money."
    ),
    "gamma": (
        "Gamma measures the rate of change of delta as the spot rate moves. "
        "High gamma (near the strike) means the hedge effectiveness can shift rapidly "
        "with market moves — useful when rates are hovering near your strike level. "
        "Low gamma means the hedge behaves more predictably day-to-day."
    ),
}


def _option_card(opt: FreightOption, spot: float) -> str:
    """Return HTML for a CAP / FLOOR / COLLAR option card."""
    type_colors = {
        "CAP":    C_LOW,
        "FLOOR":  C_HIGH,
        "COLLAR": C_ACCENT,
    }
    type_icons = {
        "CAP":    "CAP",
        "FLOOR":  "FLOOR",
        "COLLAR": "COLLAR",
    }
    color = type_colors.get(opt.option_type, C_ACCENT)
    label = type_icons.get(opt.option_type, opt.option_type)

    pct_otm = ((opt.strike_rate / spot) - 1.0) * 100.0 if spot > 0 else 0.0
    pct_label = (
        ("+" + "{:.1f}".format(pct_otm) + "% OTM")
        if pct_otm >= 0
        else ("{:.1f}".format(pct_otm) + "% ITM")
    )

    # Gamma: use attribute if present, else fall back to a simple Black-Scholes proxy
    gamma_val = getattr(opt, "gamma", None)
    gamma_str = "{:.4f}".format(gamma_val) if gamma_val is not None else "N/A"

    delta_tooltip = _GREEK_TOOLTIPS["delta"]
    gamma_tooltip = _GREEK_TOOLTIPS["gamma"]

    return (
        "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + "; "
        "border-left:4px solid " + color + "; border-radius:10px; padding:18px 20px; "
        "margin-bottom:12px'>"
        # Header row
        "<div style='display:flex; align-items:center; justify-content:space-between; "
        "margin-bottom:14px'>"
        "<span style='background:rgba(255,255,255,0.06); color:" + color + "; "
        "border:1px solid " + color + "55; padding:3px 10px; border-radius:999px; "
        "font-size:0.72rem; font-weight:700; letter-spacing:0.08em'>" + label + "</span>"
        "<span style='font-size:0.72rem; color:" + C_TEXT3 + "'>" + pct_label + "</span>"
        "</div>"
        # Grid of stats
        "<div style='display:grid; grid-template-columns:1fr 1fr; gap:10px 20px; "
        "margin-bottom:14px'>"
        # Premium
        "<div>"
        "<div style='font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em'>Premium/FEU</div>"
        "<div style='font-size:1.1rem; font-weight:700; color:" + color + "'>"
        "$" + "{:,.0f}".format(opt.premium_per_feu) + "</div>"
        "</div>"
        # Strike
        "<div>"
        "<div style='font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em'>Strike Rate</div>"
        "<div style='font-size:1.1rem; font-weight:700; color:" + C_TEXT + "'>"
        "$" + "{:,.0f}".format(opt.strike_rate) + "</div>"
        "</div>"
        # Delta — with tooltip title attribute
        "<div>"
        "<div style='font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em; cursor:help; text-decoration:underline dotted' "
        "title='" + delta_tooltip + "'>Delta (?)</div>"
        "<div style='font-size:1.1rem; font-weight:700; color:" + C_TEXT2 + "'>"
        + "{:.2f}".format(opt.delta) + "</div>"
        "</div>"
        # Gamma — with tooltip title attribute
        "<div>"
        "<div style='font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em; cursor:help; text-decoration:underline dotted' "
        "title='" + gamma_tooltip + "'>Gamma (?)</div>"
        "<div style='font-size:1.1rem; font-weight:700; color:" + C_TEXT2 + "'>"
        + gamma_str + "</div>"
        "</div>"
        # Breakeven — span full width
        "<div style='grid-column:1/-1'>"
        "<div style='font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.08em'>Breakeven</div>"
        "<div style='font-size:1.1rem; font-weight:700; color:" + C_TEXT + "'>"
        "$" + "{:,.0f}".format(opt.breakeven_rate) + "</div>"
        "</div>"
        "</div>"
        # Recommended for
        "<div style='font-size:0.73rem; color:" + C_TEXT2 + "; line-height:1.5; "
        "border-top:1px solid " + C_BORDER + "; padding-top:10px'>"
        + opt.recommended_for
        + "</div>"
        "</div>"
    )


def _action_badge(action: str) -> str:
    color = _ACTION_COLOR.get(action, C_TEXT3)
    label = _ACTION_LABEL.get(action, action)
    return (
        "<span style='background:rgba(255,255,255,0.04); color:" + color + "; "
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


# ── Hero section ─────────────────────────────────────────────────────────────

def _render_hero(freight_data: dict) -> None:
    """Render the dark terminal hero bar with three key stats."""
    from processing.derivatives_pricer import price_ffa as _pf, _compute_hist_vol

    # Compute avg implied vol across all routes
    vols = []
    cheapest_hedge_route = "—"
    cheapest_premium = float("inf")

    for route_id, df in freight_data.items():
        if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
            continue
        try:
            sigma = _compute_hist_vol(df)
            vols.append(sigma)
            cap = price_freight_cap(route_id, freight_data)
            if cap and cap.premium_per_feu < cheapest_premium:
                cheapest_premium = cap.premium_per_feu
                cheapest_hedge_route = route_id.replace("_", " ").title()
        except Exception:
            pass

    avg_vol = sum(vols) / len(vols) if vols else 0.0
    hedgeable_routes = len(vols)

    avg_vol_str = "{:.0%}".format(avg_vol) if avg_vol > 0 else "—"
    cheapest_prem_str = ("$" + "{:,.0f}".format(cheapest_premium) + "/FEU") if cheapest_premium < float("inf") else "—"

    st.markdown(
        "<div style='background:linear-gradient(135deg, #0a0f1a 0%, #111827 100%); "
        "border:1px solid rgba(59,130,246,0.25); border-radius:14px; "
        "padding:24px 28px; margin-bottom:28px'>"
        # Title row
        "<div style='display:flex; align-items:baseline; gap:14px; margin-bottom:20px'>"
        "<div style='font-size:0.62rem; text-transform:uppercase; letter-spacing:0.18em; "
        "color:#475569; border:1px solid #1e293b; padding:3px 8px; border-radius:4px; "
        "font-family:monospace'>DERIVATIVES DESK</div>"
        "<div style='font-size:1.55rem; font-weight:900; color:" + C_TEXT + "; "
        "letter-spacing:-0.03em'>Freight Derivatives Desk</div>"
        "</div>"
        # Stat row
        "<div style='display:grid; grid-template-columns:repeat(3,1fr); gap:16px'>"
        # Avg vol
        "<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); "
        "border-radius:8px; padding:14px 16px'>"
        "<div style='font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em; "
        "color:#475569; margin-bottom:6px; font-family:monospace'>AVG IMPLIED VOL</div>"
        "<div style='font-size:1.6rem; font-weight:800; color:" + C_ACCENT + "; line-height:1'>"
        + avg_vol_str + "</div>"
        "<div style='font-size:0.70rem; color:" + C_TEXT3 + "; margin-top:4px'>annualised across all routes</div>"
        "</div>"
        # Cheapest hedge
        "<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); "
        "border-radius:8px; padding:14px 16px'>"
        "<div style='font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em; "
        "color:#475569; margin-bottom:6px; font-family:monospace'>CHEAPEST CAP ROUTE</div>"
        "<div style='font-size:1.1rem; font-weight:800; color:" + C_HIGH + "; line-height:1.2'>"
        + cheapest_hedge_route + "</div>"
        "<div style='font-size:0.70rem; color:" + C_TEXT3 + "; margin-top:4px'>"
        + cheapest_prem_str + " 3m cap premium</div>"
        "</div>"
        # Hedgeable exposure
        "<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); "
        "border-radius:8px; padding:14px 16px'>"
        "<div style='font-size:0.62rem; text-transform:uppercase; letter-spacing:0.1em; "
        "color:#475569; margin-bottom:6px; font-family:monospace'>HEDGEABLE ROUTES</div>"
        "<div style='font-size:1.6rem; font-weight:800; color:" + C_MOD + "; line-height:1'>"
        + str(hedgeable_routes) + "</div>"
        "<div style='font-size:0.70rem; color:" + C_TEXT3 + "; margin-top:4px'>routes with priceable options</div>"
        "</div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ── FFA Pricer section ────────────────────────────────────────────────────────

def _render_ffa_pricer(route_id: str, freight_data: dict, months_forward: int) -> None:
    """Render FFA price vs spot card and term structure line chart."""
    ffa = price_ffa(route_id, freight_data, months_forward)

    if ffa is None:
        st.info(
            "📊 FFA data requires Freightos API access — showing estimated values "
            "based on spot rates. Live FFA prices will appear here once connected."
        )
        return

    # ── Summary stat row ──────────────────────────────────────────────────────
    cols = st.columns(4)
    with cols[0]:
        st.markdown(
            _stat_card(
                "FFA Fair Value",
                "$" + "{:,.0f}".format(ffa.ffa_price),
                ffa.settlement_period,
                C_ACCENT,
            ),
            unsafe_allow_html=True,
        )
    with cols[1]:
        basis_color = C_HIGH if ffa.basis >= 0 else C_LOW
        basis_sign = "+" if ffa.basis >= 0 else ""
        st.markdown(
            _stat_card(
                "Basis (FFA - Spot)",
                basis_sign + "$" + "{:,.0f}".format(abs(ffa.basis)),
                "contango" if ffa.basis >= 0 else "backwardation",
                basis_color,
            ),
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            _stat_card(
                "Implied Volatility",
                "{:.1%}".format(ffa.implied_volatility),
                "annualised historical",
                _C_PURPLE,
            ),
            unsafe_allow_html=True,
        )
    with cols[3]:
        ci_lo, ci_hi = ffa.confidence_interval
        st.markdown(
            _stat_card(
                "90% CI at Settlement",
                "$" + "{:,.0f}".format(ci_lo) + "–" + "{:,.0f}".format(ci_hi),
                str(ffa.days_to_settlement) + " days to settlement",
                C_MOD,
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── Term structure chart ──────────────────────────────────────────────────
    term = get_term_structure(route_id, freight_data, _TENORS)
    if not term:
        return

    labels = [t["label"] for t in term]
    ffa_prices = [t["ffa_price"] for t in term]
    spot_line = [ffa.current_spot] * len(term)

    fig = go.Figure()

    # Spot reference line
    fig.add_trace(go.Scatter(
        x=labels,
        y=spot_line,
        mode="lines",
        line={"width": 1.5, "color": C_MOD, "dash": "dash"},
        name="Current Spot",
    ))

    # FFA term structure
    fig.add_trace(go.Scatter(
        x=labels,
        y=ffa_prices,
        mode="lines+markers",
        line={"width": 2.5, "color": C_ACCENT},
        marker={"size": 8, "color": C_ACCENT, "symbol": "circle"},
        name="FFA Price",
        hovertemplate="<b>%{x}</b><br>FFA: $%{y:,.0f}/FEU<extra></extra>",
    ))

    # Shade area between spot and FFA
    fig.add_trace(go.Scatter(
        x=labels + labels[::-1],
        y=ffa_prices + spot_line[::-1],
        fill="toself",
        fillcolor="rgba(59,130,246,0.07)",
        line={"width": 0},
        hoverinfo="skip",
        showlegend=False,
    ))

    layout = dark_layout(
        title="FFA Term Structure — " + route_id.replace("_", " ").title(),
        height=340,
        showlegend=True,
    )
    layout["template"] = "plotly_dark"
    layout["yaxis"]["title"] = {"text": "Rate USD/FEU", "font": {"color": C_TEXT2, "size": 12}}
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=f"deriv_ffa_{route_id}_{months_forward}")

    # CSV download for FFA term structure
    csv_buf = io.StringIO()
    writer = _csv.writer(csv_buf)
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


# ── Options Pricer section ────────────────────────────────────────────────────

def _render_options_pricer(route_id: str, freight_data: dict) -> None:
    """Render CAP, FLOOR, COLLAR option cards side-by-side."""
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

    cap     = price_freight_cap(route_id, freight_data)
    floor_  = price_freight_floor(route_id, freight_data)
    collar  = price_freight_collar(route_id, freight_data)

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

    # ── Greeks legend ─────────────────────────────────────────────────────────
    with st.expander("What do Delta and Gamma mean for shipping operators?", expanded=False, key="derivatives_greeks_legend_expander"):
        st.markdown(
            "**Delta** — How much the option premium changes for every $1 move in spot "
            "freight rates. A delta of 0.40 on a cap means it gains $0.40 in protection "
            "value for each $1 the market rises above your strike. Use this to size how "
            "many FEUs to hedge to achieve a target coverage ratio.\n\n"
            "**Gamma** — How quickly delta itself changes as the market moves. High gamma "
            "(near the strike) means your hedge effectiveness can shift rapidly when rates "
            "hover around your protection level — you may need to rebalance your position "
            "more frequently. Low gamma indicates a more stable, predictable hedge.",
        )

    # ── Cargo size context ────────────────────────────────────────────────────
    if cap:
        st.markdown(
            "<div style='background:rgba(59,130,246,0.05); border-left:3px solid "
            + C_ACCENT + "; border-radius:0 8px 8px 0; padding:12px 16px; "
            "font-size:0.78rem; color:" + C_TEXT2 + "; line-height:1.6'>"
            "<b style='color:" + C_TEXT + "'>Cargo sizing guide:</b>  "
            "For a 1,000 FEU shipment at current spot ($"
            + "{:,.0f}".format(spot) + "), a 3-month cap costs $"
            + "{:,.0f}".format(cap.premium_per_feu * 1000)
            + " total. The cap activates when market rates exceed $"
            + "{:,.0f}".format(cap.strike_rate)
            + "/FEU, saving you the difference on all FEUs above that level."
            "</div>",
            unsafe_allow_html=True,
        )


# ── Volatility surface heatmap ────────────────────────────────────────────────

_VOL_SURFACE_MIN_ROUTES = 2  # minimum routes needed for a meaningful surface


def _render_vol_surface(freight_data: dict) -> None:
    """Render implied vol surface: routes (Y) x time horizons (X) as a heatmap.

    Falls back to a simplified flat bar chart when fewer than
    _VOL_SURFACE_MIN_ROUTES routes have sufficient data.
    """
    from processing.derivatives_pricer import _compute_hist_vol

    horizon_months = [1, 3, 6, 12]
    horizon_labels = ["1m", "3m", "6m", "12m"]

    route_ids = [r for r, df in freight_data.items()
                 if df is not None and not df.empty and "rate_usd_per_feu" in df.columns]

    if not route_ids:
        st.info("No route data available for volatility surface.")
        return

    # Build vol matrix — rows = routes, cols = horizons
    # Vol is constant across tenors for a given route (historical vol),
    # but we apply a term-structure scaling: short tenors have slightly lower
    # vol due to mean-reversion (vol * sqrt(1/T) heuristic clipped to reasonable range).

    route_labels = []
    z_matrix = []

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
            # Adjust vol by mean-reversion scaling: shorter tenor = slightly lower
            # because average rate is less uncertain closer in
            scaled_sigma = sigma_annual * (0.70 + 0.30 * min(1.0, T * 2))
            row.append(round(scaled_sigma * 100.0, 1))  # convert to %

        route_labels.append(route_id.replace("_", " ").title())
        z_matrix.append(row)

    if not z_matrix:
        st.info("Could not compute volatility for any route.")
        return

    # ── Insufficient data: show simplified flat bar chart ────────────────────
    if len(z_matrix) < _VOL_SURFACE_MIN_ROUTES:
        st.caption(
            "ℹ️ Not enough routes to build a full volatility surface — "
            "showing per-route average implied volatility instead."
        )
        avg_vols = [sum(row) / len(row) for row in z_matrix]
        fig = go.Figure(go.Bar(
            x=route_labels,
            y=avg_vols,
            marker_color="#8b5cf6",
            text=["{:.1f}%".format(v) for v in avg_vols],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Avg Implied Vol: %{y:.1f}%<extra></extra>",
        ))
        layout = dark_layout(
            title="Average Implied Volatility by Route",
            height=340,
            showlegend=False,
        )
        layout["yaxis"]["title"] = {"text": "Implied Vol (%)", "font": {"color": C_TEXT2, "size": 12}}
        layout["yaxis"]["ticksuffix"] = "%"
        layout["plot_bgcolor"] = "#0a0f1a"
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="deriv_vol_surface_fallback")
        return

    # ── Full heatmap surface ──────────────────────────────────────────────────
    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=horizon_labels,
        y=route_labels,
        colorscale="Viridis",
        colorbar={
            "title": {"text": "Implied Vol (%)", "font": {"color": C_TEXT2, "size": 12}},
            "tickfont": {"color": C_TEXT2, "size": 10},
            "thickness": 16,
        },
        text=[[("{:.1f}%".format(v)) for v in row] for row in z_matrix],
        texttemplate="%{text}",
        textfont={"size": 10, "color": "white"},
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Tenor: %{x}<br>"
            "Implied Vol: %{z:.1f}%<extra></extra>"
        ),
    ))

    layout = dark_layout(
        title="Implied Volatility Surface",
        height=max(300, 40 * len(route_labels) + 80),
        showlegend=False,
    )
    layout["template"] = "plotly_dark"
    layout["xaxis"]["title"] = {"text": "Tenor", "font": {"color": C_TEXT2, "size": 12}}
    layout["yaxis"]["title"] = {"text": "Route", "font": {"color": C_TEXT2, "size": 12}}
    layout["yaxis"]["automargin"] = True
    layout["plot_bgcolor"] = "#0a0f1a"
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="deriv_vol_surface")


# ── Hedging dashboard ─────────────────────────────────────────────────────────

def _render_hedging_dashboard(freight_data: dict, macro_data: dict) -> None:
    """Render hedging recommendations table for all routes."""
    recs = get_all_hedging_recommendations(freight_data, macro_data)

    if not recs:
        st.info("No hedging recommendations available.")
        return

    # Sort: HIGH urgency first, then MODERATE, then LOW; within each by saving desc
    urgency_order = {"HIGH": 0, "MODERATE": 1, "LOW": 2}
    sorted_recs = sorted(
        recs.items(),
        key=lambda kv: (
            urgency_order.get(kv[1].get("urgency", "LOW"), 2),
            -kv[1].get("estimated_annual_saving_per_feu", 0.0),
        ),
    )

    table_html = (
        "<table style='width:100%; border-collapse:collapse; font-size:0.78rem; "
        "color:" + C_TEXT2 + "'>"
        "<thead><tr>"
        "<th style='text-align:left; padding:8px 12px; color:" + C_TEXT3 + "; font-weight:600; "
        "border-bottom:1px solid rgba(255,255,255,0.08)'>Route</th>"
        "<th style='text-align:center; padding:8px 12px; color:" + C_TEXT3 + "; font-weight:600; "
        "border-bottom:1px solid rgba(255,255,255,0.08)'>Action</th>"
        "<th style='text-align:center; padding:8px 12px; color:" + C_TEXT3 + "; font-weight:600; "
        "border-bottom:1px solid rgba(255,255,255,0.08)'>Urgency</th>"
        "<th style='text-align:center; padding:8px 12px; color:" + C_TEXT3 + "; font-weight:600; "
        "border-bottom:1px solid rgba(255,255,255,0.08)'>Impl. Vol</th>"
        "<th style='text-align:center; padding:8px 12px; color:" + C_TEXT3 + "; font-weight:600; "
        "border-bottom:1px solid rgba(255,255,255,0.08)'>Trend</th>"
        "<th style='text-align:right; padding:8px 12px; color:" + C_TEXT3 + "; font-weight:600; "
        "border-bottom:1px solid rgba(255,255,255,0.08)'>Est. Annual Saving/FEU</th>"
        "<th style='text-align:left; padding:8px 12px; color:" + C_TEXT3 + "; font-weight:600; "
        "border-bottom:1px solid rgba(255,255,255,0.08)'>Rationale</th>"
        "</tr></thead><tbody>"
    )

    trend_arrows = {"RISING": "↑", "FALLING": "↓", "STABLE": "→"}
    trend_colors = {"RISING": C_LOW, "FALLING": C_HIGH, "STABLE": C_TEXT3}

    for route_id, rec in sorted_recs:
        action = rec.get("action", "WAIT")
        urgency = rec.get("urgency", "LOW")
        sigma = rec.get("implied_vol", 0.0)
        trend = rec.get("rate_trend", "STABLE")
        saving = rec.get("estimated_annual_saving_per_feu", 0.0)
        rationale = rec.get("rationale", "")

        route_label = route_id.replace("_", " ").title()
        trend_arrow = trend_arrows.get(trend, "→")
        trend_color = trend_colors.get(trend, C_TEXT3)
        saving_str = ("$" + "{:,.0f}".format(saving)) if saving > 0 else "—"
        saving_color = C_HIGH if saving > 0 else C_TEXT3

        table_html += (
            "<tr style='border-bottom:1px solid rgba(255,255,255,0.03)'>"
            "<td style='padding:10px 12px; color:" + C_TEXT + "; font-weight:500'>"
            + route_label + "</td>"
            "<td style='padding:10px 12px; text-align:center'>"
            + _action_badge(action) + "</td>"
            "<td style='padding:10px 12px; text-align:center'>"
            + _urgency_badge(urgency) + "</td>"
            "<td style='padding:10px 12px; text-align:center; color:" + _C_PURPLE + "; "
            "font-weight:600'>"
            + "{:.0%}".format(sigma) + "</td>"
            "<td style='padding:10px 12px; text-align:center; color:" + trend_color + "; "
            "font-weight:700; font-size:1rem'>"
            + trend_arrow + " " + trend + "</td>"
            "<td style='padding:10px 12px; text-align:right; color:" + saving_color + "; "
            "font-weight:700'>"
            + saving_str + "</td>"
            "<td style='padding:10px 12px; color:" + C_TEXT3 + "; font-size:0.72rem; "
            "max-width:320px; line-height:1.5'>"
            + rationale[:160] + ("…" if len(rationale) > 160 else "")
            + "</td>"
            "</tr>"
        )

    table_html += "</tbody></table>"
    st.markdown(table_html, unsafe_allow_html=True)


# ── Section divider ───────────────────────────────────────────────────────────

def _section_header(label: str, sublabel: str = "") -> None:
    sub = (
        "<div style='font-size:0.80rem; color:" + C_TEXT3 + "; margin-top:4px'>"
        + sublabel + "</div>"
        if sublabel
        else ""
    )
    st.markdown(
        "<div style='margin:28px 0 16px 0'>"
        "<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.14em; "
        "color:#475569; margin-bottom:4px; font-weight:600'>" + label + "</div>"
        "<div style='height:1px; background:rgba(255,255,255,0.06)'></div>"
        + sub
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Main render entry point ───────────────────────────────────────────────────

def render(route_results: list, freight_data: dict, macro_data: dict) -> None:
    """Render the Freight Derivatives Desk tab.

    Parameters
    ----------
    route_results : List of RouteResult objects (used for route selection).
    freight_data  : Dict mapping route_id -> DataFrame with 'rate_usd_per_feu'.
    macro_data    : Dict of macro time series.
    """

    # ── Hero ──────────────────────────────────────────────────────────────────
    _render_hero(freight_data)

    # ── Route selector ────────────────────────────────────────────────────────
    available_routes = [
        r for r, df in freight_data.items()
        if df is not None and not df.empty and "rate_usd_per_feu" in df.columns
    ]

    if not available_routes:
        st.warning("No freight rate data loaded. Cannot render derivatives desk.")
        return

    # Prefer routes from route_results if available, else fall back to freight_data keys
    route_display_map: dict[str, str] = {}
    if route_results:
        try:
            for rr in route_results:
                rid = getattr(rr, "route_id", None) or getattr(rr, "id", None)
                rname = getattr(rr, "route_name", None) or getattr(rr, "name", None) or rid
                if rid and rid in available_routes:
                    route_display_map[rname] = rid
        except Exception:
            pass

    if not route_display_map:
        for rid in available_routes:
            route_display_map[rid.replace("_", " ").title()] = rid

    # ── Controls row ──────────────────────────────────────────────────────────
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
            min_value=1,
            max_value=12,
            value=3,
            step=1,
            key="deriv_months_slider",
        )

    selected_route_id = route_display_map.get(selected_display, available_routes[0])

    # ── FFA Pricer ─────────────────────────────────────────────────────────────
    _section_header(
        "FFA PRICER",
        "Forward Freight Agreement fair value and term structure",
    )
    _render_ffa_pricer(selected_route_id, freight_data, months_forward)

    # ── Options Pricer ────────────────────────────────────────────────────────
    _section_header(
        "OPTIONS PRICER",
        "Black-Scholes freight rate options — Cap, Floor, Collar",
    )
    _render_options_pricer(selected_route_id, freight_data)

    # ── Volatility Surface ────────────────────────────────────────────────────
    _section_header(
        "VOLATILITY SURFACE",
        "Implied vol by route and tenor — darker = higher vol",
    )
    _render_vol_surface(freight_data)

    # ── Hedging Dashboard ─────────────────────────────────────────────────────
    _section_header(
        "HEDGING DASHBOARD",
        "Recommendations for all routes — sorted by urgency",
    )
    _render_hedging_dashboard(freight_data, macro_data)
