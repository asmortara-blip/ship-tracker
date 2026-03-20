"""
Smart Booking Optimizer Tab

Helps importers/exporters find the optimal time and route to book
container shipping. Renders booking form, recommendation card, market
timing table, and logistics cost breakdown donut chart.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ports.port_registry import PORTS
from processing.booking_optimizer import (
    BookingScenario,
    BookingRecommendation,
    estimate_total_logistics_cost,
    get_market_timing_score,
    optimize_booking,
)
from routes.route_registry import ROUTES, ROUTES_BY_ID


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_C_BG     = "#0a0f1a"
_C_CARD   = "#1a2235"
_C_BORDER = "rgba(255,255,255,0.08)"
_C_HIGH   = "#10b981"
_C_MOD    = "#f59e0b"
_C_LOW    = "#ef4444"
_C_ACCENT = "#3b82f6"
_C_TEXT   = "#f1f5f9"
_C_TEXT2  = "#94a3b8"
_C_TEXT3  = "#64748b"

_CARGO_CATEGORIES = [
    "electronics",
    "machinery",
    "apparel",
    "food",
    "chemicals",
    "other",
]

_URGENCY_CONFIG: dict[str, dict] = {
    "BOOK_NOW":     {"color": "#ef4444", "label": "BOOK NOW",     "pulse": True},
    "WAIT_1_WEEK":  {"color": "#f59e0b", "label": "WAIT 1 WEEK",  "pulse": False},
    "WAIT_2_WEEKS": {"color": "#3b82f6", "label": "WAIT 2 WEEKS", "pulse": False},
    "FLEXIBLE":     {"color": "#10b981", "label": "FLEXIBLE",      "pulse": False},
}

_TIMING_CONFIG: dict[str, dict] = {
    "GREAT_TIME_TO_BOOK": {"color": "#10b981", "bg": "rgba(16,185,129,0.15)"},
    "GOOD":               {"color": "#3b82f6", "bg": "rgba(59,130,246,0.15)"},
    "NEUTRAL":            {"color": "#94a3b8", "bg": "rgba(148,163,184,0.10)"},
    "EXPENSIVE":          {"color": "#f59e0b", "bg": "rgba(245,158,11,0.15)"},
    "WAIT":               {"color": "#ef4444", "bg": "rgba(239,68,68,0.15)"},
}


# ---------------------------------------------------------------------------
# Helper renderers
# ---------------------------------------------------------------------------

def _port_name_map() -> dict[str, str]:
    """Return {locode: name} for all 25 tracked ports."""
    return {p.locode: p.name for p in PORTS}


def _divider(label: str = "") -> None:
    label_span = (
        '<span style="font-size:0.65rem; color:#475569; text-transform:uppercase;'
        ' letter-spacing:0.12em">' + label + "</span>"
        if label
        else ""
    )
    st.markdown(
        '<div style="display:flex; align-items:center; gap:12px; margin:28px 0">'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        + label_span
        + '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _badge(text: str, color: str, bg: str, pulse: bool = False) -> str:
    """Return an inline HTML badge string."""
    anim = "animation:pulse-badge 1.1s ease-in-out infinite;" if pulse else ""
    return (
        "<span style='"
        + anim
        + "background:"
        + bg
        + "; color:"
        + color
        + "; border:1px solid "
        + color
        + "55; padding:4px 14px; border-radius:999px;"
        " font-size:0.75rem; font-weight:700; letter-spacing:0.08em'>"
        + text
        + "</span>"
    )


def _timing_badge(signal: str) -> str:
    cfg = _TIMING_CONFIG.get(signal, _TIMING_CONFIG["NEUTRAL"])
    return _badge(signal.replace("_", " "), cfg["color"], cfg["bg"])


def _render_hero() -> None:
    st.markdown(
        """
<style>
@keyframes pulse-badge {
    0%   { opacity: 1; box-shadow: 0 0 0 0 rgba(239,68,68,0.4); }
    50%  { opacity: 0.85; box-shadow: 0 0 0 6px rgba(239,68,68,0); }
    100% { opacity: 1; box-shadow: 0 0 0 0 rgba(239,68,68,0); }
}
</style>
<div style="padding:16px 0 28px 0; border-bottom:1px solid rgba(255,255,255,0.06); margin-bottom:28px">
    <div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.15em;
                color:#475569; margin-bottom:6px">BOOKING INTELLIGENCE</div>
    <div style="font-size:1.65rem; font-weight:900; color:#f1f5f9;
                letter-spacing:-0.03em; line-height:1.1">
        Smart Booking Optimizer
    </div>
    <div style="font-size:0.88rem; color:#64748b; margin-top:6px">
        Find the best time and route to ship your cargo
    </div>
</div>
""",
        unsafe_allow_html=True,
    )


def _render_booking_form(port_names: dict[str, str]) -> BookingScenario | None:
    """Render the booking form and return a BookingScenario if submitted."""
    port_labels = [name + " (" + locode + ")" for locode, name in port_names.items()]
    locode_by_label = {
        name + " (" + locode + ")": locode for locode, name in port_names.items()
    }

    with st.form("booking_form", clear_on_submit=False):
        st.markdown(
            "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:#64748b; margin-bottom:14px; font-weight:700'>Configure Shipment</div>",
            unsafe_allow_html=True,
        )

        col1, col2 = st.columns(2)

        with col1:
            origin_label = st.selectbox(
                "Origin Port",
                options=port_labels,
                index=0,
                key="bk_origin",
            )
            cargo_feu = st.number_input(
                "Cargo Volume (FEU)",
                min_value=1,
                max_value=500,
                value=10,
                step=1,
                key="bk_feu",
            )
            desired_arrival = st.date_input(
                "Desired Arrival Date",
                value=date.today() + timedelta(days=45),
                min_value=date.today() + timedelta(days=14),
                max_value=date.today() + timedelta(days=365),
                key="bk_arrival",
            )
            priority = st.radio(
                "Optimization Priority",
                options=["Cost", "Speed", "Reliability"],
                horizontal=True,
                key="bk_priority",
            )

        with col2:
            dest_label = st.selectbox(
                "Destination Port",
                options=port_labels,
                index=min(9, len(port_labels) - 1),
                key="bk_dest",
            )
            cargo_category = st.selectbox(
                "Cargo Category",
                options=_CARGO_CATEGORIES,
                index=0,
                key="bk_cat",
            )
            flexibility_days = st.slider(
                "Arrival Flexibility (days)",
                min_value=0,
                max_value=21,
                value=7,
                step=1,
                key="bk_flex",
                help="Number of days +/- on your desired arrival date",
            )

        submitted = st.form_submit_button(
            "Optimize Booking",
            use_container_width=True,
            type="primary",
        )

    if not submitted:
        return None

    origin_locode = locode_by_label.get(origin_label, "CNSHA")
    dest_locode = locode_by_label.get(dest_label, "USLAX")

    if origin_locode == dest_locode:
        st.warning("Origin and destination cannot be the same port.")
        return None

    priority_map = {"Cost": "COST", "Speed": "SPEED", "Reliability": "RELIABILITY"}

    scenario = BookingScenario(
        origin_locode=origin_locode,
        dest_locode=dest_locode,
        cargo_feu=int(cargo_feu),
        cargo_category=cargo_category,
        desired_arrival=desired_arrival.strftime("%Y-%m-%d"),
        flexibility_days=int(flexibility_days),
        priority=priority_map.get(priority, "COST"),
    )

    logger.info(
        "Booking form submitted: {} -> {} {} FEU {}".format(
            origin_locode, dest_locode, cargo_feu, priority
        )
    )
    return scenario


def _render_recommendation_card(
    rec: BookingRecommendation,
    port_names: dict[str, str],
) -> None:
    """Render the main recommendation result card."""
    urgency_cfg = _URGENCY_CONFIG.get(rec.booking_urgency, _URGENCY_CONFIG["FLEXIBLE"])
    urgency_color = urgency_cfg["color"]
    urgency_label = urgency_cfg["label"]
    urgency_pulse = urgency_cfg["pulse"]

    route = ROUTES_BY_ID.get(rec.recommended_route_id)
    route_name = route.name if route else rec.recommended_route_id

    origin_name = port_names.get(rec.scenario.origin_locode, rec.scenario.origin_locode)
    dest_name = port_names.get(rec.scenario.dest_locode, rec.scenario.dest_locode)

    savings_color = _C_HIGH if rec.savings_vs_spot >= 0 else _C_LOW
    savings_arrow = "saving" if rec.savings_vs_spot >= 0 else "overpaying"
    savings_sign = "+" if rec.savings_vs_spot >= 0 else ""

    confidence_pct = int(rec.confidence * 100)
    conf_color = _C_HIGH if rec.confidence >= 0.75 else (_C_MOD if rec.confidence >= 0.55 else _C_LOW)

    urgency_badge = _badge(urgency_label, urgency_color, urgency_color + "22", urgency_pulse)

    st.markdown(
        "<div style='background:#1a2235; border:1px solid rgba(255,255,255,0.08);"
        " border-left:4px solid "
        + urgency_color
        + "; border-radius:14px; padding:24px 28px; margin-bottom:20px'>",
        unsafe_allow_html=True,
    )

    # Header row
    hcol1, hcol2 = st.columns([3, 1])
    with hcol1:
        st.markdown(
            "<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.12em;"
            " color:#64748b; margin-bottom:4px'>RECOMMENDATION</div>"
            "<div style='font-size:1.25rem; font-weight:800; color:#f1f5f9'>"
            + origin_name
            + " \u2192 "
            + dest_name
            + "</div>"
            "<div style='font-size:0.82rem; color:#94a3b8; margin-top:2px'>via "
            + route_name
            + "</div>",
            unsafe_allow_html=True,
        )
    with hcol2:
        st.markdown(
            "<div style='text-align:right'>"
            + urgency_badge
            + "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # Key metrics row
    m1, m2, m3, m4 = st.columns(4)

    with m1:
        st.markdown(
            "<div style='background:rgba(255,255,255,0.03); border-radius:10px;"
            " padding:14px 16px; text-align:center'>"
            "<div style='font-size:1.5rem; font-weight:900; color:#f1f5f9'>"
            "$"
            + "{:,.0f}".format(rec.estimated_rate_per_feu)
            + "</div>"
            "<div style='font-size:0.68rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em; margin-top:4px'>Rate / FEU</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    with m2:
        st.markdown(
            "<div style='background:rgba(255,255,255,0.03); border-radius:10px;"
            " padding:14px 16px; text-align:center'>"
            "<div style='font-size:1.5rem; font-weight:900; color:#f1f5f9'>"
            "$"
            + "{:,.0f}".format(rec.total_cost_usd)
            + "</div>"
            "<div style='font-size:0.68rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em; margin-top:4px'>Total Ocean Cost</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    with m3:
        st.markdown(
            "<div style='background:rgba(255,255,255,0.03); border-radius:10px;"
            " padding:14px 16px; text-align:center'>"
            "<div style='font-size:1.5rem; font-weight:900; color:#f1f5f9'>"
            + str(rec.transit_days)
            + " days</div>"
            "<div style='font-size:0.68rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em; margin-top:4px'>Transit Time</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    with m4:
        st.markdown(
            "<div style='background:rgba(255,255,255,0.03); border-radius:10px;"
            " padding:14px 16px; text-align:center'>"
            "<div style='font-size:1.5rem; font-weight:900; color:"
            + conf_color
            + "'>"
            + str(confidence_pct)
            + "%</div>"
            "<div style='font-size:0.68rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em; margin-top:4px'>Confidence</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # Departure / arrival / carrier / savings row
    d1, d2, d3, d4 = st.columns(4)

    with d1:
        st.markdown(
            "<div style='font-size:0.72rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em'>Recommended Departure</div>"
            "<div style='font-size:1rem; font-weight:700; color:#f1f5f9; margin-top:4px'>"
            + rec.recommended_departure
            + "</div>",
            unsafe_allow_html=True,
        )

    with d2:
        st.markdown(
            "<div style='font-size:0.72rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em'>Estimated Arrival</div>"
            "<div style='font-size:1rem; font-weight:700; color:#f1f5f9; margin-top:4px'>"
            + rec.estimated_arrival
            + "</div>",
            unsafe_allow_html=True,
        )

    with d3:
        st.markdown(
            "<div style='font-size:0.72rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em'>Recommended Carrier</div>"
            "<div style='font-size:1rem; font-weight:700; color:#3b82f6; margin-top:4px'>"
            + rec.carrier_recommendation
            + "</div>",
            unsafe_allow_html=True,
        )

    with d4:
        st.markdown(
            "<div style='font-size:0.72rem; color:#64748b; text-transform:uppercase;"
            " letter-spacing:0.06em'>vs Spot Today</div>"
            "<div style='font-size:1rem; font-weight:700; color:"
            + savings_color
            + "; margin-top:4px'>"
            + savings_sign
            + "$"
            + "{:,.0f}".format(abs(rec.savings_vs_spot))
            + " ("
            + savings_arrow
            + ")</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)

    # Key risks
    if rec.key_risks:
        st.markdown(
            "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:#64748b; margin-bottom:10px; margin-top:4px; font-weight:700'>Key Risks</div>",
            unsafe_allow_html=True,
        )
        risks_html = "<ul style='margin:0; padding-left:20px; color:#94a3b8; font-size:0.82rem; line-height:1.8'>"
        for risk in rec.key_risks:
            risks_html += "<li>" + risk + "</li>"
        risks_html += "</ul>"
        st.markdown(risks_html, unsafe_allow_html=True)

    # Alternative routes table
    if rec.alternative_routes:
        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:#64748b; margin-bottom:10px; font-weight:700'>Alternative Routes</div>",
            unsafe_allow_html=True,
        )
        alt_df = pd.DataFrame(rec.alternative_routes).rename(
            columns={
                "route_name":    "Route",
                "rate_per_feu":  "Rate/FEU (USD)",
                "transit_days":  "Transit (days)",
                "total_cost_usd":"Total Cost (USD)",
                "carrier":       "Carrier",
            }
        )
        display_cols = [c for c in ["Route", "Rate/FEU (USD)", "Transit (days)", "Total Cost (USD)", "Carrier"] if c in alt_df.columns]
        st.dataframe(
            alt_df[display_cols],
            use_container_width=True,
            hide_index=True,
            key="bk_alt_routes_table",
        )
        # CSV download for booking comparison
        st.download_button(
            label="Download Route Comparison (CSV)",
            data=alt_df[display_cols].to_csv(index=False),
            file_name="booking_route_comparison.csv",
            mime="text/csv",
            key="bk_routes_csv_download",
        )


def _timing_confidence(ts: dict) -> tuple[int, str]:
    """Derive a confidence level from the timing score dict.

    Returns (pct: int, color: str).  Confidence is estimated from how many
    data signals are present and how extreme the 52-week percentile is.
    """
    score = 0
    max_score = 0

    vs6m_avg = ts.get("current_vs_6m_avg")
    pct_52w = ts.get("percentile_52w")
    dip_days = ts.get("days_until_expected_dip")

    max_score += 3
    if vs6m_avg is not None:
        score += 1
    if pct_52w is not None:
        score += 1
        # Extreme percentiles (very low or very high) are more decisive
        if pct_52w <= 0.15 or pct_52w >= 0.85:
            score += 1
    if dip_days is not None:
        score += 1

    pct = int(round(score / max_score * 100)) if max_score > 0 else 50
    pct = max(20, min(95, pct))  # clamp to [20, 95] — never claim 100 % or 0 %
    color = _C_HIGH if pct >= 70 else (_C_MOD if pct >= 50 else _C_LOW)
    return pct, color


def _render_market_timing(freight_data: dict[str, pd.DataFrame]) -> None:
    """Render the always-visible market timing table for all routes."""
    _divider("Market Timing")

    st.markdown(
        "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em;"
        " color:#64748b; margin-bottom:14px; font-weight:700'>"
        "Rate Timing by Lane — Best Time to Book</div>",
        unsafe_allow_html=True,
    )

    rows: list[dict] = []
    from processing.booking_optimizer import _get_current_rate
    for route in ROUTES:
        ts = get_market_timing_score(route.id, freight_data)
        current = _get_current_rate(route.id, freight_data)
        # Guard against None/missing current rate
        current_str = ("$" + "{:,.0f}".format(current) + "/FEU") if current is not None else "N/A"
        # Guard against missing keys in the timing score dict
        vs6m_avg = ts.get("current_vs_6m_avg")
        pct_52w = ts.get("percentile_52w")
        timing_signal = ts.get("timing_signal", "NEUTRAL")
        dip_days = ts.get("days_until_expected_dip")
        vs6m_str = ("{:+.1%}".format(vs6m_avg)) if vs6m_avg is not None else "N/A"
        pct_52w_str = "{:.0%}".format(pct_52w) if pct_52w is not None else "N/A"
        dip_str = str(dip_days) + "d" if dip_days is not None else "N/A"
        conf_pct, conf_color = _timing_confidence(ts)
        rows.append(
            {
                "Route": route.name,
                "Current Rate": current_str,
                "vs 6m Avg": vs6m_str,
                "52w Pct": pct_52w_str,
                "Signal": timing_signal,
                "Dip in ~": dip_str,
                "_signal": timing_signal,
                "_conf_pct": conf_pct,
                "_conf_color": conf_color,
            }
        )

    table_html = (
        "<table style='width:100%; border-collapse:collapse; font-size:0.77rem; color:#cbd5e1'>"
        "<thead><tr>"
    )
    headers = ["Route", "Current Rate", "vs 6m Avg", "52w Pct", "Signal", "Confidence", "Dip in ~"]
    for h in headers:
        table_html += (
            "<th style='text-align:left; padding:7px 10px; color:#64748b; font-weight:600;"
            " border-bottom:1px solid rgba(255,255,255,0.08)'>"
            + h
            + "</th>"
        )
    table_html += "</tr></thead><tbody>"

    for row in rows:
        signal = row["_signal"]
        cfg = _TIMING_CONFIG.get(signal, _TIMING_CONFIG["NEUTRAL"])
        badge_html = (
            "<span style='background:"
            + cfg["bg"]
            + "; color:"
            + cfg["color"]
            + "; border:1px solid "
            + cfg["color"]
            + "55; padding:2px 10px; border-radius:999px;"
            " font-size:0.68rem; font-weight:700; letter-spacing:0.06em; white-space:nowrap'>"
            + signal.replace("_", " ")
            + "</span>"
        )
        vs6m_color = _C_HIGH if row["vs 6m Avg"].startswith("-") else _C_LOW
        conf_pct = row["_conf_pct"]
        conf_color = row["_conf_color"]
        conf_html = (
            "<span style='color:" + conf_color + "; font-weight:700'>"
            + str(conf_pct) + "%</span>"
        )
        table_html += (
            "<tr style='border-bottom:1px solid rgba(255,255,255,0.04)'>"
            "<td style='padding:8px 10px; color:#f1f5f9; font-weight:500'>"
            + row["Route"]
            + "</td>"
            "<td style='padding:8px 10px'>"
            + row["Current Rate"]
            + "</td>"
            "<td style='padding:8px 10px; color:"
            + vs6m_color
            + "; font-weight:600'>"
            + row["vs 6m Avg"]
            + "</td>"
            "<td style='padding:8px 10px'>"
            + row["52w Pct"]
            + "</td>"
            "<td style='padding:8px 10px'>"
            + badge_html
            + "</td>"
            "<td style='padding:8px 10px'>"
            + conf_html
            + "</td>"
            "<td style='padding:8px 10px; color:#64748b'>"
            + row["Dip in ~"]
            + "</td>"
            "</tr>"
        )

    table_html += "</tbody></table>"
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption("⚠️ Booking recommendations are algorithmic signals, not financial advice")


def _render_cost_breakdown(recommendation: BookingRecommendation) -> None:
    """Render a donut chart of logistics cost components."""
    _divider("Cost Breakdown")

    st.markdown(
        "<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em;"
        " color:#64748b; margin-bottom:14px; font-weight:700'>"
        "Full Logistics Cost Breakdown</div>",
        unsafe_allow_html=True,
    )

    breakdown = estimate_total_logistics_cost(recommendation)

    if not breakdown:
        st.info("Cost breakdown data is not available for this route.")
        return

    labels_map = {
        "ocean_freight":          "Ocean Freight",
        "port_handling":          "Port Handling",
        "inland_drayage_origin":  "Drayage (Origin)",
        "inland_drayage_dest":    "Drayage (Dest)",
        "documentation":          "Documentation",
        "insurance":              "Insurance",
        "carbon_offset":          "Carbon Offset",
    }
    color_map = {
        "ocean_freight":         "#3b82f6",  # blue
        "port_handling":         "#8b5cf6",  # purple
        "inland_drayage_origin": "#06b6d4",  # cyan
        "inland_drayage_dest":   "#0891b2",  # darker cyan
        "documentation":         "#f59e0b",  # amber
        "insurance":             "#10b981",  # green
        "carbon_offset":         "#6b7280",  # gray
    }

    # Only include components that exist in the breakdown and have a positive value
    present_keys = [
        k for k in labels_map
        if k in breakdown and breakdown[k] is not None and breakdown.get(k, 0) > 0
    ]

    if not present_keys:
        st.info("No cost components found for this route.")
        return

    labels = [labels_map[k] for k in present_keys]
    values = [breakdown[k] for k in present_keys]
    colors = [color_map[k] for k in present_keys]
    total = breakdown.get("total") or sum(values)

    if total <= 0:
        st.info("Total logistics cost is zero — cannot render cost breakdown.")
        return

    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.62,
            marker=dict(colors=colors, line=dict(color="#0a0f1a", width=2)),
            textinfo="label+percent",
            textfont=dict(size=11, color="#f1f5f9"),
            hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>",
            direction="clockwise",
            sort=False,
            showlegend=False,
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=340,
        margin=dict(l=10, r=10, t=10, b=10),
        annotations=[
            dict(
                text="<b style='font-size:1.2em'>${:,.0f}</b><br><span style='font-size:0.8em; color:#64748b'>Total</span>".format(
                    total
                ),
                x=0.5,
                y=0.5,
                font=dict(color="#f1f5f9", size=14),
                showarrow=False,
            )
        ],
    )

    chart_col, detail_col = st.columns([1.3, 1])

    with chart_col:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="booking_cost_donut")

    with detail_col:
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        detail_html = "<table style='width:100%; font-size:0.78rem; border-collapse:collapse'>"
        for k in present_keys:
            label = labels_map[k]
            val = breakdown[k]
            pct = val / total * 100 if total > 0 else 0
            detail_html += (
                "<tr style='border-bottom:1px solid rgba(255,255,255,0.04)'>"
                "<td style='padding:7px 6px; color:#94a3b8'>"
                + label
                + "</td>"
                "<td style='padding:7px 6px; text-align:right; color:#f1f5f9; font-weight:600'>"
                "$"
                + "{:,.0f}".format(val)
                + "</td>"
                "<td style='padding:7px 6px; text-align:right; color:#64748b'>"
                "{:.1f}%".format(pct)
                + "</td>"
                "</tr>"
            )
        detail_html += (
            "<tr style='border-top:2px solid rgba(255,255,255,0.10)'>"
            "<td style='padding:8px 6px; color:#f1f5f9; font-weight:700'>Total</td>"
            "<td style='padding:8px 6px; text-align:right; color:#3b82f6; font-weight:800'>"
            "$"
            + "{:,.0f}".format(total)
            + "</td>"
            "<td style='padding:8px 6px; text-align:right; color:#64748b'>100%</td>"
            "</tr>"
        )
        detail_html += "</table>"
        st.markdown(detail_html, unsafe_allow_html=True)

    # CSV download for cost breakdown
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["Cost Component", "Amount (USD)", "Share (%)"])
    for k in present_keys:
        val = breakdown[k]
        pct = val / total * 100 if total > 0 else 0
        writer.writerow([labels_map[k], "{:.2f}".format(val), "{:.1f}".format(pct)])
    writer.writerow(["Total", "{:.2f}".format(total), "100.0"])
    st.download_button(
        label="Download Cost Breakdown (CSV)",
        data=csv_buf.getvalue(),
        file_name="booking_cost_breakdown.csv",
        mime="text/csv",
        key="bk_cost_csv_download",
    )


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(
    port_results: list,
    route_results: list,
    freight_data: dict[str, pd.DataFrame],
    macro_data: dict,
) -> None:
    """Render the Smart Booking Optimizer tab."""

    _render_hero()

    port_names = _port_name_map()

    # --- Booking form ---
    scenario = _render_booking_form(port_names)

    if scenario is not None:
        with st.spinner("Optimizing booking..."):
            try:
                rec = optimize_booking(scenario, route_results, freight_data, macro_data)
                st.session_state["booking_rec"] = rec
            except Exception as exc:
                logger.error("Booking optimization failed: {}".format(exc))
                st.error("Optimization failed: " + str(exc))
                st.session_state.pop("booking_rec", None)

    # --- Recommendation card (persists across reruns via session_state) ---
    rec: BookingRecommendation | None = st.session_state.get("booking_rec")
    if rec is not None:
        _divider("Recommendation")
        _render_recommendation_card(rec, port_names)
        _render_cost_breakdown(rec)

    # --- Market timing (always visible) ---
    _render_market_timing(freight_data)
