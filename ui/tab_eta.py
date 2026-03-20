"""
tab_eta.py — Cargo ETA Intelligence tab.

Renders:
  1. Hero KPI cards: avg delay, HIGH-risk routes, total potential savings
  2. Route ETA Cards grid: per-route transit, delay, congestion badge, drivers
  3. Departure Calendar: 4-week forward-looking grid (green/amber/red)
  4. Cost Savings Calculator: selectbox route + FEU count input
  5. Congestion Timeline: 30-day sinusoidal + baseline chart for selected port
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.eta_predictor import (
    ShipmentETA,
    predict_all_routes,
    get_best_departure_windows,
)
from routes.route_registry import ROUTES_BY_ID

# ---------------------------------------------------------------------------
# Color palette (mirrors styles.py)
# ---------------------------------------------------------------------------
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_HIGH    = "#10b981"   # green
_C_MOD     = "#f59e0b"   # amber
_C_LOW     = "#ef4444"   # red
_C_ACCENT  = "#3b82f6"   # blue
_C_CONV    = "#8b5cf6"   # purple
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _risk_color(risk: str) -> str:
    return {
        "LOW":      _C_HIGH,
        "MODERATE": _C_MOD,
        "HIGH":     _C_LOW,
        "SEVERE":   "#dc2626",
    }.get(risk, _C_MOD)


def _delay_color(delay_days: float) -> str:
    if delay_days <= 0.5:
        return _C_HIGH
    if delay_days <= 2.0:
        return _C_MOD
    return _C_LOW


def _divider(label: str) -> None:
    st.markdown(
        '<div style="display:flex; align-items:center; gap:12px; margin:28px 0">'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        '<span style="font-size:0.65rem; color:#475569; text-transform:uppercase;'
        ' letter-spacing:0.12em">' + label + '</span>'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(label: str, value: str, sub: str = "", color: str = _C_ACCENT) -> str:
    sub_html = (
        "" if not sub
        else '<div style="font-size:0.78rem; color:' + _C_TEXT2 + '">' + sub + "</div>"
    )
    return (
        '<div style="background:' + _C_CARD + '; border:1px solid ' + _C_BORDER + ';'
        ' border-top:3px solid ' + color + '; border-radius:10px;'
        ' padding:16px 18px; text-align:center">'
        '<div style="font-size:0.68rem; font-weight:700; color:' + _C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.07em">' + label + '</div>'
        '<div style="font-size:1.7rem; font-weight:800; color:' + _C_TEXT + ';'
        ' line-height:1.1; margin:5px 0">' + value + '</div>'
        + sub_html
        + '</div>'
    )


# ---------------------------------------------------------------------------
# Section 1 – Hero KPIs
# ---------------------------------------------------------------------------

def _render_hero(etas: list[ShipmentETA]) -> None:
    if not etas:
        st.info("No ETA data available.")
        return

    avg_delay = sum(e.predicted_delay_days for e in etas) / len(etas)
    high_risk_count = sum(1 for e in etas if e.congestion_risk in ("HIGH", "SEVERE"))
    total_savings = sum(e.cost_savings_vs_now for e in etas if e.cost_savings_vs_now > 0)

    delay_color = _delay_color(avg_delay)
    risk_color = _C_LOW if high_risk_count >= 3 else (_C_MOD if high_risk_count >= 1 else _C_HIGH)
    savings_color = _C_HIGH if total_savings > 0 else _C_TEXT3

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            _kpi_card(
                "Avg Predicted Delay",
                "+" + str(round(avg_delay, 1)) + "d",
                "across all routes",
                delay_color,
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            _kpi_card(
                "Routes w/ HIGH Congestion",
                str(high_risk_count),
                "of " + str(len(etas)) + " routes tracked",
                risk_color,
            ),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            _kpi_card(
                "Total Potential Savings",
                "$" + "{:,.0f}".format(total_savings),
                "per FEU from optimal timing",
                savings_color,
            ),
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 2 – Route ETA Cards grid
# ---------------------------------------------------------------------------

def _eta_card_html(eta: ShipmentETA) -> str:
    risk_color = _risk_color(eta.congestion_risk)
    delay_color = _delay_color(eta.predicted_delay_days)

    route = ROUTES_BY_ID.get(eta.route_id)
    route_name = route.name if route else eta.route_id

    drivers_html = "".join(
        '<li style="margin:2px 0; color:' + _C_TEXT2 + '; font-size:0.76rem">'
        + d + "</li>"
        for d in eta.delay_drivers
    )

    savings_str = ""
    if eta.cost_savings_vs_now > 0:
        savings_str = (
            '<div style="margin-top:8px; padding:6px 10px;'
            ' background:rgba(16,185,129,0.10); border-radius:6px;'
            ' color:' + _C_HIGH + '; font-size:0.76rem; font-weight:600">'
            "Optimal: " + eta.optimal_departure_week
            + " — save $" + "{:,.0f}".format(eta.cost_savings_vs_now) + "/FEU"
            "</div>"
        )
    else:
        savings_str = (
            '<div style="margin-top:8px; padding:6px 10px;'
            ' background:rgba(255,255,255,0.04); border-radius:6px;'
            ' color:' + _C_TEXT3 + '; font-size:0.76rem">'
            "Optimal: " + eta.optimal_departure_week
            + "</div>"
        )

    conf_pct = str(round(eta.confidence * 100)) + "%"

    return (
        '<div style="background:' + _C_CARD + '; border:1px solid ' + _C_BORDER + ';'
        ' border-left:4px solid ' + risk_color + '; border-radius:12px;'
        ' padding:16px 18px; margin-bottom:14px">'

        # Header row
        '<div style="display:flex; justify-content:space-between; align-items:flex-start;'
        ' margin-bottom:10px">'
        '  <div>'
        '    <div style="font-size:0.9rem; font-weight:700; color:' + _C_TEXT + '">'
        + route_name + "</div>"
        '    <div style="font-size:0.78rem; color:' + _C_TEXT3 + '; margin-top:2px">'
        + eta.origin_port + " &#8594; " + eta.dest_port + "</div>"
        "  </div>"
        '  <span style="background:' + risk_color + '22; color:' + risk_color + ';'
        ' border:1px solid ' + risk_color + '; border-radius:999px;'
        ' padding:2px 10px; font-size:0.7rem; font-weight:700">'
        + eta.congestion_risk + "</span>"
        "</div>"

        # Transit + delay row
        '<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:10px">'
        '<div style="text-align:center">'
        '<div style="font-size:0.63rem; color:' + _C_TEXT3 + '; text-transform:uppercase">Transit</div>'
        '<div style="font-size:1.2rem; font-weight:700; color:' + _C_TEXT + '">'
        + str(eta.nominal_transit_days) + "d</div>"
        "</div>"
        '<div style="text-align:center">'
        '<div style="font-size:0.63rem; color:' + _C_TEXT3 + '; text-transform:uppercase">Delay</div>'
        '<div style="font-size:1.2rem; font-weight:700; color:' + delay_color + '">'
        "+" + str(eta.predicted_delay_days) + "d</div>"
        "</div>"
        '<div style="text-align:center">'
        '<div style="font-size:0.63rem; color:' + _C_TEXT3 + '; text-transform:uppercase">Total ETA</div>'
        '<div style="font-size:1.2rem; font-weight:700; color:' + _C_ACCENT + '">'
        + str(eta.total_eta_days) + "d</div>"
        "</div>"
        "</div>"

        # Delay drivers
        '<div style="font-size:0.68rem; font-weight:700; color:' + _C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px">Delay Drivers</div>'
        '<ul style="margin:0; padding-left:16px">' + drivers_html + "</ul>"

        # Optimal departure
        + savings_str

        # Confidence footer
        + '<div style="margin-top:8px; font-size:0.68rem; color:' + _C_TEXT3 + '">'
        + "Confidence: " + conf_pct
        + "</div>"
        + "</div>"
    )


def _render_eta_cards(etas: list[ShipmentETA]) -> None:
    n = len(etas)
    rows = (n + 1) // 2
    for row_i in range(rows):
        left_idx = row_i * 2
        right_idx = row_i * 2 + 1
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown(_eta_card_html(etas[left_idx]), unsafe_allow_html=True)
        if right_idx < n:
            with col_r:
                st.markdown(_eta_card_html(etas[right_idx]), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 3 – Departure Calendar (4-week forward)
# ---------------------------------------------------------------------------

def _week_congestion_level(base_congestion: float, week_offset: int) -> float:
    """Project congestion level for a given week offset using mean reversion."""
    today = date.today()
    future = today + timedelta(weeks=week_offset)
    day_of_year = future.timetuple().tm_yday
    seasonal_wave = 0.05 * math.sin(2 * math.pi * (day_of_year - 60) / 365)
    projected = base_congestion * (0.92 ** week_offset) + 0.5 * (1 - 0.92 ** week_offset) + seasonal_wave
    return max(0.0, min(1.0, projected))


def _cell_color(cong: float) -> str:
    if cong <= 0.45:
        return "rgba(16,185,129,0.25)"
    if cong <= 0.65:
        return "rgba(245,158,11,0.25)"
    return "rgba(239,68,68,0.25)"


def _cell_text_color(cong: float) -> str:
    if cong <= 0.45:
        return _C_HIGH
    if cong <= 0.65:
        return _C_MOD
    return _C_LOW


def _render_departure_calendar(etas: list[ShipmentETA], port_results: list) -> None:
    today = date.today()
    week_labels = []
    for w in range(4):
        wdate = today + timedelta(weeks=w)
        week_labels.append("Week of " + wdate.strftime("%b %-d"))

    # Build header
    header_cols = ["Route"] + week_labels
    header_html = "".join(
        '<th style="padding:8px 10px; font-size:0.68rem; font-weight:700;'
        ' color:' + _C_TEXT3 + '; text-transform:uppercase;'
        ' letter-spacing:0.06em; text-align:center; border-bottom:1px solid rgba(255,255,255,0.08)">'
        + col + "</th>"
        for col in header_cols
    )

    rows_html = ""
    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        short_name = route.name[:22] + "..." if route and len(route.name) > 25 else (route.name if route else eta.route_id)

        # Get origin congestion for projection
        origin_cong = 0.5
        for result in port_results:
            if isinstance(result, dict):
                pl = result.get("locode") or result.get("port_locode")
            else:
                pl = getattr(result, "locode", None) or getattr(result, "port_locode", None)
            if pl == eta.origin_port:
                origin_cong = float(
                    result.get("congestion_index", 0.5)
                    if isinstance(result, dict)
                    else getattr(result, "congestion_index", 0.5)
                )
                break

        cells_html = (
            '<td style="padding:8px 10px; font-size:0.78rem; font-weight:600;'
            ' color:' + _C_TEXT + '">' + short_name + "</td>"
        )
        for w in range(4):
            cong = _week_congestion_level(origin_cong, w)
            bg = _cell_color(cong)
            tc = _cell_text_color(cong)
            wdate = today + timedelta(weeks=w)
            is_optimal = (eta.optimal_departure_week == "Week of " + wdate.strftime("%b %-d"))
            border_extra = (
                " border:2px solid " + _C_HIGH + ";"
                if is_optimal
                else " border:1px solid rgba(255,255,255,0.05);"
            )
            label = "OPTIMAL" if is_optimal else ("GO" if cong <= 0.45 else ("OK" if cong <= 0.65 else "AVOID"))
            cells_html += (
                '<td style="padding:8px 10px; text-align:center;'
                ' background:' + bg + ';' + border_extra
                + ' border-radius:4px">'
                '<div style="font-size:0.7rem; font-weight:700; color:' + tc + '">'
                + label + "</div>"
                '<div style="font-size:0.6rem; color:' + _C_TEXT3 + ';">'
                + str(round(cong, 2)) + " idx</div>"
                "</td>"
            )

        rows_html += "<tr>" + cells_html + "</tr>"

    st.markdown(
        '<div style="overflow-x:auto">'
        '<table style="width:100%; border-collapse:separate; border-spacing:4px">'
        "<thead><tr>" + header_html + "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table>"
        '<div style="font-size:0.68rem; color:' + _C_TEXT3 + '; margin-top:8px">'
        "Green = low congestion (GO), Amber = moderate (OK), Red = high congestion (AVOID). "
        "Gold border = optimal departure week for that route."
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4 – Cost Savings Calculator
# ---------------------------------------------------------------------------

def _render_savings_calculator(etas: list[ShipmentETA]) -> None:
    route_options = []
    eta_by_id: dict[str, ShipmentETA] = {}
    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        label = (route.name if route else eta.route_id) + " (" + eta.origin_port + " -> " + eta.dest_port + ")"
        route_options.append(label)
        eta_by_id[label] = eta

    if not route_options:
        st.info("No routes available for calculator.")
        return

    col_sel, col_feu = st.columns([2, 1])
    with col_sel:
        selected_label = st.selectbox(
            "Select route",
            route_options,
            key="eta_calc_route",
        )
    with col_feu:
        feu_count = st.number_input(
            "Number of FEUs",
            min_value=1,
            max_value=500,
            value=10,
            step=1,
            key="eta_calc_feu",
        )

    selected_eta = eta_by_id.get(selected_label)
    if selected_eta is None:
        return

    savings_per_feu = selected_eta.cost_savings_vs_now
    total_savings = savings_per_feu * feu_count

    if savings_per_feu > 0:
        st.markdown(
            '<div style="background:rgba(16,185,129,0.10); border:1px solid ' + _C_HIGH + ';'
            ' border-radius:10px; padding:18px 22px; margin-top:12px">'
            '<div style="font-size:0.75rem; font-weight:700; color:' + _C_HIGH + ';'
            ' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px">'
            "Savings from Waiting for Optimal Departure</div>"
            '<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px">'
            '<div><div style="font-size:0.65rem; color:' + _C_TEXT3 + '">Per FEU Savings</div>'
            '<div style="font-size:1.4rem; font-weight:800; color:' + _C_HIGH + '">'
            "$" + "{:,.0f}".format(savings_per_feu) + "</div></div>"
            '<div><div style="font-size:0.65rem; color:' + _C_TEXT3 + '">FEU Count</div>'
            '<div style="font-size:1.4rem; font-weight:800; color:' + _C_TEXT + '">'
            + str(feu_count) + "</div></div>"
            '<div><div style="font-size:0.65rem; color:' + _C_TEXT3 + '">Total Savings</div>'
            '<div style="font-size:1.4rem; font-weight:800; color:' + _C_HIGH + '">'
            "$" + "{:,.0f}".format(total_savings) + "</div></div>"
            "</div>"
            '<div style="margin-top:10px; font-size:0.78rem; color:' + _C_TEXT2 + '">'
            "Optimal departure: <strong>" + selected_eta.optimal_departure_week + "</strong>"
            " — rate at optimal: $" + "{:,.0f}".format(selected_eta.rate_at_optimal) + "/FEU"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        extra_cost = abs(total_savings)
        st.markdown(
            '<div style="background:rgba(245,158,11,0.08); border:1px solid ' + _C_MOD + ';'
            ' border-radius:10px; padding:18px 22px; margin-top:12px">'
            '<div style="font-size:0.75rem; font-weight:700; color:' + _C_MOD + ';'
            ' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px">'
            "Ship Now for Best Rate</div>"
            '<div style="font-size:0.88rem; color:' + _C_TEXT2 + '">'
            "Rates are rising — waiting until "
            + selected_eta.optimal_departure_week
            + " will cost an additional <strong>$"
            + "{:,.0f}".format(extra_cost)
            + "</strong> for "
            + str(feu_count)
            + " FEUs. Ship now to lock in current rate."
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 5 – Congestion Timeline (30-day sinusoidal model)
# ---------------------------------------------------------------------------

def _render_congestion_timeline(port_results: list) -> None:
    # Collect unique port locodes
    port_locodes: list[str] = []
    port_names: dict[str, str] = {}
    for result in port_results:
        if isinstance(result, dict):
            locode = result.get("locode") or result.get("port_locode", "")
            name = result.get("port_name", locode)
        else:
            locode = getattr(result, "locode", None) or getattr(result, "port_locode", "")
            name = getattr(result, "port_name", locode)
        if locode and locode not in port_names:
            port_locodes.append(locode)
            port_names[locode] = name

    if not port_locodes:
        st.info("No port data available for congestion timeline.")
        return

    port_options = [port_names[lc] + " (" + lc + ")" for lc in port_locodes]
    locode_by_option = {port_names[lc] + " (" + lc + ")": lc for lc in port_locodes}

    selected_option = st.selectbox(
        "Select port for congestion timeline",
        port_options,
        key="eta_timeline_port",
    )
    selected_locode = locode_by_option.get(selected_option, port_locodes[0])

    # Get baseline congestion
    baseline_cong = 0.5
    for result in port_results:
        if isinstance(result, dict):
            pl = result.get("locode") or result.get("port_locode")
        else:
            pl = getattr(result, "locode", None) or getattr(result, "port_locode", None)
        if pl == selected_locode:
            baseline_cong = float(
                result.get("congestion_index", 0.5)
                if isinstance(result, dict)
                else getattr(result, "congestion_index", 0.5)
            )
            break

    # Generate 30-day forward congestion with sinusoidal seasonal model + baseline
    today = date.today()
    dates: list[str] = []
    values: list[float] = []
    for day in range(31):
        future = today + timedelta(days=day)
        day_of_year = future.timetuple().tm_yday
        seasonal_wave = 0.08 * math.sin(2 * math.pi * (day_of_year - 60) / 365)
        # Mean reversion with decay
        alpha = 0.03 * day
        projected = baseline_cong * math.exp(-alpha * 0.05) + 0.5 * (1 - math.exp(-alpha * 0.05)) + seasonal_wave
        projected = max(0.0, min(1.0, projected))
        dates.append(future.isoformat())
        values.append(round(projected, 4))

    # Threshold bands
    fig = go.Figure()

    # Fill bands for risk zones
    fig.add_hrect(
        y0=0.85, y1=1.0,
        fillcolor="rgba(220,38,38,0.12)",
        line_width=0,
        annotation_text="SEVERE",
        annotation_position="right",
        annotation_font=dict(color="#dc2626", size=9),
    )
    fig.add_hrect(
        y0=0.70, y1=0.85,
        fillcolor="rgba(239,68,68,0.10)",
        line_width=0,
        annotation_text="HIGH",
        annotation_position="right",
        annotation_font=dict(color=_C_LOW, size=9),
    )
    fig.add_hrect(
        y0=0.45, y1=0.70,
        fillcolor="rgba(245,158,11,0.08)",
        line_width=0,
        annotation_text="MODERATE",
        annotation_position="right",
        annotation_font=dict(color=_C_MOD, size=9),
    )
    fig.add_hrect(
        y0=0.0, y1=0.45,
        fillcolor="rgba(16,185,129,0.06)",
        line_width=0,
        annotation_text="LOW",
        annotation_position="right",
        annotation_font=dict(color=_C_HIGH, size=9),
    )

    # Congestion line
    fig.add_trace(go.Scatter(
        x=dates,
        y=values,
        mode="lines+markers",
        line=dict(color=_C_ACCENT, width=2.5),
        marker=dict(size=5, color=_C_ACCENT),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.08)",
        name="Projected Congestion",
        hovertemplate="%{x}: %{y:.3f}<extra></extra>",
    ))

    # Mark today
    fig.add_vline(
        x=today.isoformat(),
        line_dash="dot",
        line_color="rgba(148,163,184,0.5)",
        annotation_text="Today",
        annotation_position="top",
        annotation_font=dict(color=_C_TEXT3, size=10),
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=320,
        margin=dict(t=20, b=20, l=48, r=80),
        xaxis=dict(
            title="Date",
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(size=10, color=_C_TEXT3),
        ),
        yaxis=dict(
            title="Congestion Index",
            range=[0, 1.05],
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color=_C_TEXT3),
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=_C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Sinusoidal seasonal model + mean reversion from current baseline ("
        + str(round(baseline_cong, 3))
        + ") for "
        + selected_option
    )


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(
    port_results: list,
    route_results: list,
    freight_data: dict,
    macro_data: dict,
) -> None:
    """Render the Cargo ETA Intelligence tab."""
    logger.info("Rendering ETA Intelligence tab")

    st.header("Cargo ETA Intelligence")
    st.caption(
        "Predicted transit delays, optimal departure windows, "
        "and cost savings based on live congestion, seasonality, and rate momentum."
    )

    # Compute ETAs
    try:
        etas = predict_all_routes(port_results, freight_data, macro_data)
    except Exception as exc:
        logger.error("ETA prediction failed: {}", exc)
        st.error("ETA prediction encountered an error: " + str(exc))
        return

    if not etas:
        st.info("No ETA data computed. Ensure port and freight data are loaded.")
        return

    # ── Hero KPIs ─────────────────────────────────────────────────────────────
    _render_hero(etas)

    # ── Route ETA Cards ───────────────────────────────────────────────────────
    _divider("Route ETA Cards")
    _render_eta_cards(etas)

    # ── Departure Calendar ────────────────────────────────────────────────────
    _divider("4-Week Departure Calendar")
    st.markdown(
        '<div style="font-size:0.82rem; color:' + _C_TEXT2 + '; margin-bottom:12px">'
        "Forward-looking 4-week congestion calendar. "
        "Ship during green weeks for lowest delay risk."
        "</div>",
        unsafe_allow_html=True,
    )
    _render_departure_calendar(etas, port_results)

    # ── Best Departure Windows ────────────────────────────────────────────────
    best_windows = get_best_departure_windows(etas)
    if best_windows:
        _divider("Top Departure Opportunities")
        cols = st.columns(min(len(best_windows), 3))
        for i, window in enumerate(best_windows[:3]):
            with cols[i]:
                route = ROUTES_BY_ID.get(window["route_id"])
                rname = route.name if route else window["route_id"]
                st.markdown(
                    '<div style="background:rgba(16,185,129,0.08); border:1px solid ' + _C_HIGH + ';'
                    ' border-radius:10px; padding:14px 16px; text-align:center">'
                    '<div style="font-size:0.68rem; font-weight:700; color:' + _C_HIGH + ';'
                    ' text-transform:uppercase; letter-spacing:0.06em; margin-bottom:6px">'
                    "Best Window</div>"
                    '<div style="font-size:0.85rem; font-weight:700; color:' + _C_TEXT + '; margin-bottom:4px">'
                    + rname + "</div>"
                    '<div style="font-size:0.75rem; color:' + _C_TEXT3 + '; margin-bottom:8px">'
                    + window["origin_port"] + " -> " + window["dest_port"] + "</div>"
                    '<div style="font-size:1.2rem; font-weight:800; color:' + _C_HIGH + '">'
                    + window["optimal_departure_week"] + "</div>"
                    '<div style="font-size:0.8rem; color:' + _C_TEXT2 + '; margin-top:6px">'
                    "Save $" + "{:,.0f}".format(window["cost_savings_vs_now"]) + "/FEU"
                    "</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )

    # ── Cost Savings Calculator ───────────────────────────────────────────────
    _divider("Cost Savings Calculator")
    _render_savings_calculator(etas)

    # ── Congestion Timeline ───────────────────────────────────────────────────
    _divider("Port Congestion Timeline — 30-Day Outlook")
    if port_results:
        _render_congestion_timeline(port_results)
    else:
        st.info("No port data available for congestion timeline.")
