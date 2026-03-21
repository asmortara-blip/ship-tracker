"""
Smart Booking Optimizer Tab

Helps importers/exporters find the optimal time and route to book container
shipping. Renders booking form, recommendation engine, rate timing matrix,
spot vs contract comparison, lead-time optimizer, carrier selection guide,
volume commitment advisor, seasonal booking calendar, and capacity forecast.
"""
from __future__ import annotations

import csv
import io
import math
import random
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from ports.port_registry import PORTS
from processing.booking_optimizer import (
    BookingScenario,
    BookingRecommendation,
    estimate_total_logistics_cost,
    get_market_timing_score,
    optimize_booking,
    _get_current_rate,
    _get_6m_avg_rate,
    _BASE_RATES,
    _CARRIER_MAP,
)
from routes.route_registry import ROUTES, ROUTES_BY_ID


# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

_C_BG     = "#0a0f1a"
_C_CARD   = "#111827"
_C_CARD2  = "#1a2235"
_C_BORDER = "rgba(255,255,255,0.07)"
_C_HIGH   = "#10b981"
_C_MOD    = "#f59e0b"
_C_LOW    = "#ef4444"
_C_ACCENT = "#3b82f6"
_C_PURPLE = "#8b5cf6"
_C_CYAN   = "#06b6d4"
_C_TEXT   = "#f1f5f9"
_C_TEXT2  = "#94a3b8"
_C_TEXT3  = "#475569"

_CARGO_CATEGORIES = [
    "electronics",
    "machinery",
    "apparel",
    "food",
    "chemicals",
    "other",
]

_URGENCY_CONFIG: dict[str, dict] = {
    "BOOK_NOW":     {"color": "#ef4444", "label": "BOOK NOW",     "pulse": True,  "icon": "🔴"},
    "WAIT_1_WEEK":  {"color": "#f59e0b", "label": "WAIT 1 WEEK",  "pulse": False, "icon": "🟡"},
    "WAIT_2_WEEKS": {"color": "#3b82f6", "label": "WAIT 2 WEEKS", "pulse": False, "icon": "🔵"},
    "FLEXIBLE":     {"color": "#10b981", "label": "FLEXIBLE",      "pulse": False, "icon": "🟢"},
}

_TIMING_CONFIG: dict[str, dict] = {
    "GREAT_TIME_TO_BOOK": {"color": "#10b981", "bg": "rgba(16,185,129,0.15)",  "rank": 0},
    "GOOD":               {"color": "#3b82f6", "bg": "rgba(59,130,246,0.15)",  "rank": 1},
    "NEUTRAL":            {"color": "#94a3b8", "bg": "rgba(148,163,184,0.10)", "rank": 2},
    "EXPENSIVE":          {"color": "#f59e0b", "bg": "rgba(245,158,11,0.15)",  "rank": 3},
    "WAIT":               {"color": "#ef4444", "bg": "rgba(239,68,68,0.15)",   "rank": 4},
}

# Carrier data: reliability, cost tier, capacity score
_CARRIER_PROFILES: dict[str, dict] = {
    "Maersk":    {"reliability": 0.88, "cost_tier": "premium",  "on_time": 72, "color": "#1d4ed8", "routes": 4},
    "MSC":       {"reliability": 0.84, "cost_tier": "mid",      "on_time": 67, "color": "#7c3aed", "routes": 5},
    "COSCO":     {"reliability": 0.81, "cost_tier": "value",    "on_time": 63, "color": "#dc2626", "routes": 4},
    "Evergreen": {"reliability": 0.79, "cost_tier": "value",    "on_time": 60, "color": "#15803d", "routes": 2},
    "ONE":       {"reliability": 0.83, "cost_tier": "mid",      "on_time": 65, "color": "#ea580c", "routes": 3},
}

_MONTH_LABELS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# Seasonal quality score per month (1=best, 5=worst booking conditions)
# Rows = routes (subset for heatmap), Cols = months
_SEASONAL_SCORES: dict[str, list[int]] = {
    "Trans-Pacific EB":    [2, 1, 2, 3, 4, 5, 5, 5, 4, 4, 3, 2],
    "Asia-Europe":         [3, 2, 2, 3, 4, 5, 5, 4, 4, 3, 2, 2],
    "Transatlantic":       [2, 2, 3, 3, 4, 4, 5, 4, 3, 3, 2, 2],
    "SE Asia Eastbound":   [2, 1, 2, 3, 4, 5, 5, 5, 4, 3, 2, 2],
    "China–South America": [3, 2, 3, 3, 4, 4, 5, 5, 4, 4, 3, 3],
    "Europe–South America":[2, 2, 3, 3, 3, 4, 4, 4, 3, 3, 2, 2],
    "ME Hub to Europe":    [3, 2, 2, 3, 3, 4, 5, 5, 4, 3, 2, 2],
    "Ningbo–Europe":       [3, 1, 2, 3, 4, 5, 5, 4, 4, 3, 2, 2],
}


# ---------------------------------------------------------------------------
# CSS injection (once)
# ---------------------------------------------------------------------------

_CSS = """
<style>
@keyframes pulse-badge {
    0%   { opacity:1;    box-shadow:0 0 0 0   rgba(239,68,68,0.5); }
    50%  { opacity:0.82; box-shadow:0 0 0 8px rgba(239,68,68,0); }
    100% { opacity:1;    box-shadow:0 0 0 0   rgba(239,68,68,0); }
}
@keyframes slide-in {
    from { opacity:0; transform:translateY(10px); }
    to   { opacity:1; transform:translateY(0); }
}
.bk-section-title {
    font-size:0.65rem;
    text-transform:uppercase;
    letter-spacing:0.14em;
    color:#475569;
    font-weight:700;
    margin-bottom:14px;
}
.bk-card {
    background:#111827;
    border:1px solid rgba(255,255,255,0.07);
    border-radius:16px;
    padding:22px 24px;
    margin-bottom:18px;
    animation:slide-in 0.25s ease;
}
.bk-metric-box {
    background:rgba(255,255,255,0.03);
    border:1px solid rgba(255,255,255,0.06);
    border-radius:12px;
    padding:16px 14px;
    text-align:center;
}
.bk-metric-val {
    font-size:1.6rem;
    font-weight:900;
    color:#f1f5f9;
    line-height:1;
}
.bk-metric-label {
    font-size:0.63rem;
    text-transform:uppercase;
    letter-spacing:0.08em;
    color:#64748b;
    margin-top:5px;
}
.bk-row-label {
    font-size:0.73rem;
    color:#cbd5e1;
    font-weight:500;
}
.bk-pill {
    display:inline-block;
    padding:3px 11px;
    border-radius:999px;
    font-size:0.68rem;
    font-weight:700;
    letter-spacing:0.06em;
}
.bk-rec-rank {
    width:28px; height:28px;
    border-radius:50%;
    display:inline-flex;
    align-items:center;
    justify-content:center;
    font-size:0.75rem;
    font-weight:900;
    flex-shrink:0;
}
.bk-progress-bar-track {
    background:rgba(255,255,255,0.06);
    border-radius:999px;
    height:6px;
    overflow:hidden;
}
.bk-divider {
    height:1px;
    background:linear-gradient(90deg, transparent, rgba(255,255,255,0.08), transparent);
    margin:28px 0;
}
</style>
"""


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _port_name_map() -> dict[str, str]:
    return {p.locode: p.name for p in PORTS}


def _inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def _divider(label: str = "") -> None:
    if label:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;margin:30px 0">'
            '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
            '<span style="font-size:0.63rem;text-transform:uppercase;letter-spacing:0.14em;'
            'color:#475569;white-space:nowrap;font-weight:700">' + label + "</span>"
            '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown('<div class="bk-divider"></div>', unsafe_allow_html=True)


def _badge(text: str, color: str, bg: str, pulse: bool = False) -> str:
    anim = "animation:pulse-badge 1.1s ease-in-out infinite;" if pulse else ""
    return (
        f"<span style='{anim}background:{bg};color:{color};"
        f"border:1px solid {color}55;padding:4px 13px;border-radius:999px;"
        f"font-size:0.72rem;font-weight:700;letter-spacing:0.07em'>{text}</span>"
    )


def _timing_badge(signal: str) -> str:
    cfg = _TIMING_CONFIG.get(signal, _TIMING_CONFIG["NEUTRAL"])
    return _badge(signal.replace("_", " "), cfg["color"], cfg["bg"])


def _pill(text: str, color: str, bg: str) -> str:
    return (
        f"<span class='bk-pill' style='background:{bg};color:{color};"
        f"border:1px solid {color}44'>{text}</span>"
    )


def _progress_bar(pct: float, color: str, height: int = 6) -> str:
    """Return an HTML progress bar (pct is 0-100)."""
    w = max(0, min(100, pct))
    return (
        f"<div style='background:rgba(255,255,255,0.06);border-radius:999px;"
        f"height:{height}px;overflow:hidden;margin-top:6px'>"
        f"<div style='width:{w:.0f}%;height:100%;border-radius:999px;"
        f"background:{color};transition:width 0.4s ease'></div></div>"
    )


def _timing_confidence(ts: dict) -> tuple[int, str]:
    score, max_score = 0, 3
    vs6m_avg = ts.get("current_vs_6m_avg")
    pct_52w = ts.get("percentile_52w")
    dip_days = ts.get("days_until_expected_dip")
    if vs6m_avg is not None:
        score += 1
    if pct_52w is not None:
        score += 1
        if pct_52w <= 0.15 or pct_52w >= 0.85:
            score += 1
    if dip_days is not None:
        score += 1
    pct = max(20, min(95, int(round(score / max_score * 100))))
    color = _C_HIGH if pct >= 70 else (_C_MOD if pct >= 50 else _C_LOW)
    return pct, color


# ---------------------------------------------------------------------------
# Section: Hero header
# ---------------------------------------------------------------------------

def _render_hero() -> None:
    st.markdown(
        """
<div style="padding:20px 0 32px 0;border-bottom:1px solid rgba(255,255,255,0.06);margin-bottom:32px">
    <div style="font-size:0.62rem;text-transform:uppercase;letter-spacing:0.18em;
                color:#475569;margin-bottom:8px;font-weight:700">
        BOOKING INTELLIGENCE &nbsp;·&nbsp; SMART OPTIMIZER
    </div>
    <div style="font-size:2rem;font-weight:900;color:#f1f5f9;
                letter-spacing:-0.04em;line-height:1.1;margin-bottom:8px">
        Booking Optimization Suite
    </div>
    <div style="font-size:0.88rem;color:#64748b;max-width:680px;line-height:1.6">
        AI-driven booking intelligence across all major trade lanes. Find the optimal
        booking window, compare spot vs. contract economics, and select the right carrier
        and commitment level for your cargo.
    </div>
</div>
""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section: Booking form
# ---------------------------------------------------------------------------

def _render_booking_form(port_names: dict[str, str]) -> BookingScenario | None:
    port_labels = [name + " (" + lc + ")" for lc, name in port_names.items()]
    locode_by_label = {name + " (" + lc + ")": lc for lc, name in port_names.items()}

    st.markdown(
        "<div class='bk-section-title'>Configure Shipment</div>",
        unsafe_allow_html=True,
    )

    with st.form("booking_form", clear_on_submit=False):
        col1, col2 = st.columns(2)

        with col1:
            origin_label = st.selectbox("Origin Port", options=port_labels, index=0, key="bk_origin")
            cargo_feu = st.number_input(
                "Cargo Volume (FEU)", min_value=1, max_value=500, value=10, step=1, key="bk_feu"
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
                "Cargo Category", options=_CARGO_CATEGORIES, index=0, key="bk_cat"
            )
            flexibility_days = st.slider(
                "Arrival Flexibility (days)",
                min_value=0, max_value=21, value=7, step=1,
                key="bk_flex",
                help="Number of days +/- on your desired arrival date",
            )
            contract_pct = st.slider(
                "Contract Volume Target (%)",
                min_value=0, max_value=100, value=40, step=5,
                key="bk_contract_pct",
                help="Target share of volume on long-term contract vs. spot",
            )

        submitted = st.form_submit_button(
            "Run Booking Optimization", use_container_width=True, type="primary"
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

    st.session_state["bk_contract_pct"] = int(contract_pct)
    logger.info("Booking form: {} -> {} {} FEU {}".format(
        origin_locode, dest_locode, cargo_feu, priority
    ))
    return scenario


# ---------------------------------------------------------------------------
# Section: Top-3 booking recommendations engine
# ---------------------------------------------------------------------------

def _render_top3_recommendations(
    rec: BookingRecommendation,
    port_names: dict[str, str],
    freight_data: dict[str, pd.DataFrame],
    macro_data: dict,
) -> None:
    _divider("TOP BOOKING RECOMMENDATIONS")

    urgency_cfg = _URGENCY_CONFIG.get(rec.booking_urgency, _URGENCY_CONFIG["FLEXIBLE"])
    urgency_color = urgency_cfg["color"]
    urgency_label = urgency_cfg["label"]
    urgency_pulse = urgency_cfg["pulse"]

    route = ROUTES_BY_ID.get(rec.recommended_route_id)
    route_name = route.name if route else rec.recommended_route_id
    origin_name = port_names.get(rec.scenario.origin_locode, rec.scenario.origin_locode)
    dest_name   = port_names.get(rec.scenario.dest_locode,   rec.scenario.dest_locode)

    savings_color = _C_HIGH if rec.savings_vs_spot >= 0 else _C_LOW
    savings_sign  = "+" if rec.savings_vs_spot >= 0 else ""
    savings_label = "saving" if rec.savings_vs_spot >= 0 else "overpaying"

    conf_pct = int(rec.confidence * 100)
    conf_color = _C_HIGH if rec.confidence >= 0.75 else (_C_MOD if rec.confidence >= 0.55 else _C_LOW)

    # ── Primary recommendation card ──
    urgency_badge_html = _badge(urgency_label, urgency_color, urgency_color + "22", urgency_pulse)

    st.markdown(
        f"<div style='background:#111827;border:1px solid rgba(255,255,255,0.07);"
        f"border-left:4px solid {urgency_color};border-radius:16px;"
        f"padding:24px 28px;margin-bottom:16px;animation:slide-in 0.25s ease'>",
        unsafe_allow_html=True,
    )

    hcol1, hcol2 = st.columns([3, 1])
    with hcol1:
        st.markdown(
            "<div style='font-size:0.62rem;text-transform:uppercase;letter-spacing:0.12em;"
            "color:#64748b;margin-bottom:4px'>PRIMARY RECOMMENDATION</div>"
            f"<div style='font-size:1.3rem;font-weight:800;color:#f1f5f9'>"
            f"{origin_name} &rarr; {dest_name}</div>"
            f"<div style='font-size:0.82rem;color:#94a3b8;margin-top:3px'>via {route_name}</div>",
            unsafe_allow_html=True,
        )
    with hcol2:
        st.markdown(
            f"<div style='text-align:right;padding-top:4px'>{urgency_badge_html}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

    m1, m2, m3, m4 = st.columns(4)
    metric_data = [
        (m1, f"${rec.estimated_rate_per_feu:,.0f}", "Rate / FEU", None),
        (m2, f"${rec.total_cost_usd:,.0f}", "Total Ocean Cost", None),
        (m3, f"{rec.transit_days}d", "Transit Time", None),
        (m4, f"{conf_pct}%", "Confidence", conf_color),
    ]
    for col, val, label, vc in metric_data:
        with col:
            color = vc or "#f1f5f9"
            st.markdown(
                f"<div class='bk-metric-box'>"
                f"<div class='bk-metric-val' style='color:{color}'>{val}</div>"
                f"<div class='bk-metric-label'>{label}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

    d1, d2, d3, d4 = st.columns(4)
    with d1:
        st.markdown(
            "<div style='font-size:0.68rem;color:#64748b;text-transform:uppercase;"
            "letter-spacing:0.06em'>Recommended Departure</div>"
            f"<div style='font-size:1rem;font-weight:700;color:#f1f5f9;margin-top:4px'>"
            f"{rec.recommended_departure}</div>",
            unsafe_allow_html=True,
        )
    with d2:
        st.markdown(
            "<div style='font-size:0.68rem;color:#64748b;text-transform:uppercase;"
            "letter-spacing:0.06em'>Estimated Arrival</div>"
            f"<div style='font-size:1rem;font-weight:700;color:#f1f5f9;margin-top:4px'>"
            f"{rec.estimated_arrival}</div>",
            unsafe_allow_html=True,
        )
    with d3:
        carrier_color = _CARRIER_PROFILES.get(rec.carrier_recommendation, {}).get("color", _C_ACCENT)
        st.markdown(
            "<div style='font-size:0.68rem;color:#64748b;text-transform:uppercase;"
            "letter-spacing:0.06em'>Recommended Carrier</div>"
            f"<div style='font-size:1rem;font-weight:700;color:{carrier_color};margin-top:4px'>"
            f"{rec.carrier_recommendation}</div>",
            unsafe_allow_html=True,
        )
    with d4:
        st.markdown(
            "<div style='font-size:0.68rem;color:#64748b;text-transform:uppercase;"
            "letter-spacing:0.06em'>vs Spot Today</div>"
            f"<div style='font-size:1rem;font-weight:700;color:{savings_color};margin-top:4px'>"
            f"{savings_sign}${abs(rec.savings_vs_spot):,.0f} ({savings_label})</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Alternative recommendations ──
    if rec.alternative_routes:
        st.markdown(
            "<div style='font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;"
            "color:#64748b;font-weight:700;margin:10px 0 12px'>ALTERNATIVE ROUTES</div>",
            unsafe_allow_html=True,
        )
        rank_colors = ["#f59e0b", "#94a3b8"]
        for idx, alt in enumerate(rec.alternative_routes[:2]):
            rank = idx + 2
            rc = rank_colors[idx]
            carrier = alt.get("carrier", "MSC")
            carrier_color = _CARRIER_PROFILES.get(carrier, {}).get("color", _C_ACCENT)
            savings_alt = (rec.estimated_rate_per_feu - alt.get("rate_per_feu", 0)) * rec.scenario.cargo_feu
            sa_color = _C_HIGH if savings_alt >= 0 else _C_LOW
            sa_sign = "+" if savings_alt >= 0 else ""

            st.markdown(
                f"<div style='background:#111827;border:1px solid rgba(255,255,255,0.06);"
                f"border-radius:12px;padding:16px 20px;margin-bottom:10px;"
                f"display:flex;align-items:center;gap:16px'>"
                f"<div class='bk-rec-rank' style='background:{rc}22;color:{rc};border:1px solid {rc}44'>#{rank}</div>"
                f"<div style='flex:1'>"
                f"<div style='font-size:0.88rem;font-weight:700;color:#f1f5f9'>"
                f"{alt.get('route_name','—')}</div>"
                f"<div style='font-size:0.75rem;color:#64748b;margin-top:2px'>"
                f"via <span style='color:{carrier_color}'>{carrier}</span> &nbsp;·&nbsp; "
                f"{alt.get('transit_days','?')}d transit</div>"
                f"</div>"
                f"<div style='text-align:right'>"
                f"<div style='font-size:1.05rem;font-weight:800;color:#f1f5f9'>"
                f"${alt.get('rate_per_feu',0):,.0f}/FEU</div>"
                f"<div style='font-size:0.72rem;color:{sa_color}'>"
                f"{sa_sign}${abs(savings_alt):,.0f} vs primary</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        csv_rows = [["Rank", "Route", "Rate/FEU", "Transit (days)", "Total Cost", "Carrier"]]
        csv_rows.append([
            "#1 (Primary)", route_name,
            f"{rec.estimated_rate_per_feu:.0f}",
            str(rec.transit_days),
            f"{rec.total_cost_usd:.0f}",
            rec.carrier_recommendation,
        ])
        for i, alt in enumerate(rec.alternative_routes[:2]):
            csv_rows.append([
                f"#{i+2}",
                alt.get("route_name",""),
                f"{alt.get('rate_per_feu',0):.0f}",
                str(alt.get("transit_days","?")),
                f"{alt.get('total_cost_usd',0):.0f}",
                alt.get("carrier",""),
            ])
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerows(csv_rows)
        st.download_button(
            "Download Recommendation Comparison (CSV)",
            data=buf.getvalue(),
            file_name="booking_recommendations.csv",
            mime="text/csv",
            key="bk_rec_csv",
        )

    # ── Key risks ──
    if rec.key_risks:
        st.markdown(
            "<div style='font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;"
            "color:#64748b;font-weight:700;margin:18px 0 10px'>KEY RISKS</div>",
            unsafe_allow_html=True,
        )
        risks_html = (
            "<ul style='margin:0;padding-left:18px;color:#94a3b8;"
            "font-size:0.82rem;line-height:2'>"
        )
        for risk in rec.key_risks:
            risks_html += f"<li>{risk}</li>"
        risks_html += "</ul>"
        st.markdown(risks_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section: Rate timing matrix (routes x timing signal)
# ---------------------------------------------------------------------------

def _render_rate_timing_matrix(freight_data: dict[str, pd.DataFrame]) -> None:
    _divider("RATE TIMING MATRIX")

    st.markdown(
        "<div class='bk-section-title'>Book Now vs. Wait — All Major Lanes</div>",
        unsafe_allow_html=True,
    )

    rows: list[dict] = []
    for route in ROUTES:
        ts = get_market_timing_score(route.id, freight_data)
        current = _get_current_rate(route.id, freight_data)
        avg_6m  = _get_6m_avg_rate(route.id, freight_data)
        current_str = f"${current:,.0f}" if current is not None else "N/A"
        vs6m = ts.get("current_vs_6m_avg")
        pct_52w = ts.get("percentile_52w")
        signal = ts.get("timing_signal", "NEUTRAL")
        dip_days = ts.get("days_until_expected_dip")
        vs6m_str   = f"{vs6m:+.1%}" if vs6m is not None else "N/A"
        pct_52w_str = f"{pct_52w:.0%}" if pct_52w is not None else "N/A"
        dip_str = f"{dip_days}d" if dip_days is not None else "N/A"
        conf_pct, conf_color = _timing_confidence(ts)
        rows.append({
            "Route": route.name,
            "Current Rate": current_str,
            "vs 6m Avg": vs6m_str,
            "52w Pct": pct_52w_str,
            "Signal": signal,
            "Confidence": conf_pct,
            "_conf_color": conf_color,
            "Dip in ~": dip_str,
            "_signal": signal,
            "_rank": _TIMING_CONFIG.get(signal, _TIMING_CONFIG["NEUTRAL"])["rank"],
            "_vs6m_raw": vs6m if vs6m is not None else 0.0,
        })

    # Sort: best opportunities first
    rows.sort(key=lambda r: r["_rank"])

    headers = ["Route", "Current Rate", "vs 6m Avg", "52w %ile", "Timing Signal", "Confidence", "Dip Expected"]
    th_style = (
        "text-align:left;padding:8px 12px;color:#475569;font-size:0.67rem;"
        "text-transform:uppercase;letter-spacing:0.1em;font-weight:700;"
        "border-bottom:1px solid rgba(255,255,255,0.08)"
    )
    table = (
        "<table style='width:100%;border-collapse:collapse;font-size:0.78rem'>"
        "<thead><tr>"
        + "".join(f"<th style='{th_style}'>{h}</th>" for h in headers)
        + "</tr></thead><tbody>"
    )

    for i, row in enumerate(rows):
        signal = row["_signal"]
        cfg = _TIMING_CONFIG.get(signal, _TIMING_CONFIG["NEUTRAL"])
        bg_row = "rgba(255,255,255,0.015)" if i % 2 else "transparent"

        badge_html = (
            f"<span style='background:{cfg['bg']};color:{cfg['color']};"
            f"border:1px solid {cfg['color']}55;padding:3px 10px;border-radius:999px;"
            f"font-size:0.66rem;font-weight:700;letter-spacing:0.07em;white-space:nowrap'>"
            f"{signal.replace('_',' ')}</span>"
        )
        vs6m_color = _C_HIGH if row["_vs6m_raw"] < -0.02 else (_C_LOW if row["_vs6m_raw"] > 0.05 else _C_TEXT2)
        conf_pct = row["Confidence"]
        conf_color = row["_conf_color"]
        conf_bar = _progress_bar(conf_pct, conf_color, height=4)
        conf_html = (
            f"<div style='min-width:80px'>"
            f"<span style='color:{conf_color};font-weight:700'>{conf_pct}%</span>"
            f"{conf_bar}</div>"
        )

        td = "padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.04)"
        table += (
            f"<tr style='background:{bg_row}'>"
            f"<td style='{td};color:#f1f5f9;font-weight:600'>{row['Route']}</td>"
            f"<td style='{td};color:#cbd5e1;font-family:monospace'>{row['Current Rate']}</td>"
            f"<td style='{td};color:{vs6m_color};font-weight:700'>{row['vs 6m Avg']}</td>"
            f"<td style='{td};color:#94a3b8'>{row['52w Pct']}</td>"
            f"<td style='{td}'>{badge_html}</td>"
            f"<td style='{td}'>{conf_html}</td>"
            f"<td style='{td};color:#64748b'>{row['Dip in ~']}</td>"
            f"</tr>"
        )

    table += "</tbody></table>"
    st.markdown(table, unsafe_allow_html=True)
    st.caption("Booking signals are algorithmic estimates based on historical rate patterns.")


# ---------------------------------------------------------------------------
# Section: Spot vs contract rate comparison
# ---------------------------------------------------------------------------

def _render_spot_vs_contract(freight_data: dict[str, pd.DataFrame]) -> None:
    _divider("SPOT vs. CONTRACT RATE COMPARISON")

    st.markdown(
        "<div class='bk-section-title'>Current Spot vs. Estimated Contract Rate — by Lane</div>",
        unsafe_allow_html=True,
    )

    routes_display = [r for r in ROUTES if r.id in _BASE_RATES]

    route_names, spot_rates, contract_rates, savings_pcts = [], [], [], []
    for route in routes_display[:12]:
        spot = _get_current_rate(route.id, freight_data)
        avg_6m = _get_6m_avg_rate(route.id, freight_data)
        ts = get_market_timing_score(route.id, freight_data)
        pct_52w = ts.get("percentile_52w", 0.5) or 0.5

        if pct_52w >= 0.70:
            contract_discount = -0.08
        elif pct_52w >= 0.45:
            contract_discount = 0.06
        else:
            contract_discount = 0.12

        contract = spot * (1.0 - contract_discount)
        saving_pct = contract_discount * 100

        route_names.append(route.name[:28] + ("..." if len(route.name) > 28 else ""))
        spot_rates.append(spot)
        contract_rates.append(contract)
        savings_pcts.append(saving_pct)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Spot Rate",
        x=route_names,
        y=spot_rates,
        marker_color="#3b82f6",
        marker_line_width=0,
        text=[f"${r:,.0f}" for r in spot_rates],
        textposition="outside",
        textfont=dict(size=10, color="#94a3b8"),
    ))
    fig.add_trace(go.Bar(
        name="Contract Rate (est.)",
        x=route_names,
        y=contract_rates,
        marker_color="#10b981",
        marker_line_width=0,
        text=[f"${r:,.0f}" for r in contract_rates],
        textposition="outside",
        textfont=dict(size=10, color="#94a3b8"),
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        barmode="group",
        height=380,
        margin=dict(l=0, r=0, t=10, b=120),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8", size=11),
        ),
        xaxis=dict(
            tickangle=-35, tickfont=dict(size=10, color="#64748b"),
            gridcolor="rgba(255,255,255,0.04)", showgrid=False,
        ),
        yaxis=dict(
            tickprefix="$", tickfont=dict(size=10, color="#64748b"),
            gridcolor="rgba(255,255,255,0.05)",
            title=dict(text="Rate (USD/FEU)", font=dict(color="#475569", size=11)),
        ),
        font=dict(color="#94a3b8"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="bk_spot_contract_bar")

    sc_html = (
        "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));"
        "gap:10px;margin-top:4px'>"
    )
    for name, spot, contract, saving in zip(route_names, spot_rates, contract_rates, savings_pcts):
        s_color = _C_HIGH if saving > 0 else _C_LOW
        s_label = f"Contract saves {saving:.1f}%" if saving > 0 else f"Spot cheaper by {abs(saving):.1f}%"
        sc_html += (
            f"<div style='background:#111827;border:1px solid rgba(255,255,255,0.06);"
            f"border-radius:10px;padding:12px 14px'>"
            f"<div style='font-size:0.72rem;color:#64748b;margin-bottom:4px'>{name}</div>"
            f"<div style='font-size:0.8rem;color:{s_color};font-weight:700'>{s_label}</div>"
            f"</div>"
        )
    sc_html += "</div>"
    st.markdown(sc_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section: Booking lead time optimizer
# ---------------------------------------------------------------------------

def _render_lead_time_optimizer(freight_data: dict[str, pd.DataFrame]) -> None:
    _divider("BOOKING LEAD TIME OPTIMIZER")

    st.markdown(
        "<div class='bk-section-title'>Optimal Booking Windows — by Lane & Priority</div>",
        unsafe_allow_html=True,
    )

    lead_time_data: list[dict] = []
    for route in ROUTES[:12]:
        ts = get_market_timing_score(route.id, freight_data)
        pct_52w = ts.get("percentile_52w", 0.5) or 0.5
        signal  = ts.get("timing_signal", "NEUTRAL")

        if pct_52w >= 0.70:
            cost_window   = (21, 28)
            speed_window  = (14, 21)
            rel_window    = (28, 35)
        elif pct_52w >= 0.40:
            cost_window   = (14, 21)
            speed_window  = (7, 14)
            rel_window    = (21, 28)
        else:
            cost_window   = (7, 14)
            speed_window  = (7, 14)
            rel_window    = (14, 21)

        lead_time_data.append({
            "route": route,
            "signal": signal,
            "pct_52w": pct_52w,
            "cost_window": cost_window,
            "speed_window": speed_window,
            "rel_window": rel_window,
        })

    th_style = (
        "text-align:left;padding:8px 12px;color:#475569;font-size:0.64rem;"
        "text-transform:uppercase;letter-spacing:0.1em;font-weight:700;"
        "border-bottom:1px solid rgba(255,255,255,0.08)"
    )
    table = (
        "<table style='width:100%;border-collapse:collapse;font-size:0.78rem'>"
        "<thead><tr>"
        + "".join(
            f"<th style='{th_style}'>{h}</th>"
            for h in ["Lane", "Rate Level", "Book for Cost", "Book for Speed", "Book for Reliability"]
        )
        + "</tr></thead><tbody>"
    )

    def _window_badge(lo: int, hi: int, color: str) -> str:
        return (
            f"<span style='background:{color}18;color:{color};"
            f"border:1px solid {color}33;padding:3px 10px;border-radius:999px;"
            f"font-size:0.69rem;font-weight:700'>{lo}-{hi}d before sailing</span>"
        )

    for i, d in enumerate(lead_time_data):
        bg_row = "rgba(255,255,255,0.015)" if i % 2 else "transparent"
        cfg = _TIMING_CONFIG.get(d["signal"], _TIMING_CONFIG["NEUTRAL"])
        pct_bar = _progress_bar(d["pct_52w"] * 100, cfg["color"], height=4)
        rate_cell = (
            f"<div style='font-size:0.73rem;color:{cfg['color']};font-weight:700'>"
            f"{d['signal'].replace('_',' ')}</div>"
            f"<div style='font-size:0.66rem;color:#64748b'>{d['pct_52w']:.0%} of 52w range</div>"
            f"{pct_bar}"
        )
        td = "padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.04)"
        table += (
            f"<tr style='background:{bg_row}'>"
            f"<td style='{td};color:#f1f5f9;font-weight:600'>{d['route'].name}</td>"
            f"<td style='{td};min-width:160px'>{rate_cell}</td>"
            f"<td style='{td}'>{_window_badge(*d['cost_window'],   _C_HIGH)}</td>"
            f"<td style='{td}'>{_window_badge(*d['speed_window'],  _C_ACCENT)}</td>"
            f"<td style='{td}'>{_window_badge(*d['rel_window'],    _C_PURPLE)}</td>"
            f"</tr>"
        )

    table += "</tbody></table>"
    st.markdown(table, unsafe_allow_html=True)
    st.caption("Lead times represent optimal booking window (days before vessel departure date).")


# ---------------------------------------------------------------------------
# Section: Carrier selection guide
# ---------------------------------------------------------------------------

def _render_carrier_guide(freight_data: dict[str, pd.DataFrame]) -> None:
    _divider("CARRIER SELECTION GUIDE")

    st.markdown(
        "<div class='bk-section-title'>Best Carrier by Route — Reliability · Cost · Capacity</div>",
        unsafe_allow_html=True,
    )

    carrier_route_data: list[dict] = []
    for route in ROUTES[:12]:
        primary = _CARRIER_MAP.get(route.id, "MSC")
        profile = _CARRIER_PROFILES.get(primary, {})
        ts = get_market_timing_score(route.id, freight_data)
        pct_52w = ts.get("percentile_52w", 0.5) or 0.5

        capacity_pct = int((1.0 - pct_52w) * 100)
        reliability = profile.get("reliability", 0.75)
        on_time = profile.get("on_time", 65)
        cost_tier = profile.get("cost_tier", "mid")
        cost_color = {"premium": _C_LOW, "mid": _C_MOD, "value": _C_HIGH}.get(cost_tier, _C_MOD)
        cost_label = {"premium": "Premium", "mid": "Mid-Range", "value": "Value"}.get(cost_tier, "Mid")

        carrier_route_data.append({
            "route": route,
            "carrier": primary,
            "color": profile.get("color", _C_ACCENT),
            "reliability": reliability,
            "on_time": on_time,
            "cost_tier": cost_label,
            "cost_color": cost_color,
            "capacity_pct": capacity_pct,
        })

    col_a, col_b = st.columns(2)
    for idx, d in enumerate(carrier_route_data):
        col = col_a if idx % 2 == 0 else col_b
        rel_bar  = _progress_bar(d["reliability"] * 100, d["color"])
        cap_bar  = _progress_bar(d["capacity_pct"], _C_CYAN)
        with col:
            st.markdown(
                f"<div style='background:#111827;border:1px solid rgba(255,255,255,0.06);"
                f"border-radius:12px;padding:16px 18px;margin-bottom:10px'>"
                f"<div style='display:flex;align-items:flex-start;justify-content:space-between;"
                f"margin-bottom:10px'>"
                f"<div>"
                f"<div style='font-size:0.82rem;font-weight:700;color:#f1f5f9'>{d['route'].name}</div>"
                f"<div style='font-size:0.72rem;color:#64748b;margin-top:2px'>"
                f"{d['route'].transit_days}d transit</div>"
                f"</div>"
                f"<span style='background:{d['color']}22;color:{d['color']};"
                f"border:1px solid {d['color']}44;padding:4px 12px;border-radius:999px;"
                f"font-size:0.72rem;font-weight:800'>{d['carrier']}</span>"
                f"</div>"
                f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px'>"
                f"<div>"
                f"<div style='font-size:0.62rem;color:#64748b;text-transform:uppercase;"
                f"letter-spacing:0.08em'>Reliability</div>"
                f"<div style='font-size:0.82rem;font-weight:700;color:{d['color']}'>"
                f"{d['reliability']*100:.0f}%</div>"
                f"{rel_bar}"
                f"</div>"
                f"<div>"
                f"<div style='font-size:0.62rem;color:#64748b;text-transform:uppercase;"
                f"letter-spacing:0.08em'>On-Time</div>"
                f"<div style='font-size:0.82rem;font-weight:700;color:#94a3b8'>{d['on_time']}%</div>"
                f"{_progress_bar(d['on_time'], '#94a3b8')}"
                f"</div>"
                f"<div>"
                f"<div style='font-size:0.62rem;color:#64748b;text-transform:uppercase;"
                f"letter-spacing:0.08em'>Cost Tier</div>"
                f"<div style='font-size:0.82rem;font-weight:700;color:{d['cost_color']}'>"
                f"{d['cost_tier']}</div>"
                f"</div>"
                f"</div>"
                f"<div style='margin-top:10px'>"
                f"<div style='font-size:0.62rem;color:#64748b;text-transform:uppercase;"
                f"letter-spacing:0.08em'>Capacity Availability</div>"
                f"<div style='font-size:0.72rem;color:#06b6d4;font-weight:600;margin-top:2px'>"
                f"{d['capacity_pct']}% available</div>"
                f"{cap_bar}"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown(
        "<div style='font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;"
        "color:#64748b;font-weight:700;margin:18px 0 12px'>CARRIER SCORECARD</div>",
        unsafe_allow_html=True,
    )
    score_cols = st.columns(len(_CARRIER_PROFILES))
    for col, (carrier, prof) in zip(score_cols, _CARRIER_PROFILES.items()):
        with col:
            c = prof["color"]
            tier_color = {"premium": _C_LOW, "mid": _C_MOD, "value": _C_HIGH}.get(prof["cost_tier"], _C_MOD)
            tier_label = {"premium": "Premium", "mid": "Mid", "value": "Value"}.get(prof["cost_tier"], "Mid")
            st.markdown(
                f"<div style='background:#111827;border:1px solid {c}33;"
                f"border-top:3px solid {c};border-radius:12px;padding:16px 14px;text-align:center'>"
                f"<div style='font-size:0.9rem;font-weight:800;color:{c}'>{carrier}</div>"
                f"<div style='font-size:1.3rem;font-weight:900;color:#f1f5f9;margin:8px 0 2px'>"
                f"{prof['reliability']*100:.0f}%</div>"
                f"<div style='font-size:0.62rem;color:#64748b;text-transform:uppercase;"
                f"letter-spacing:0.08em'>Reliability</div>"
                f"<div style='margin-top:8px;font-size:0.75rem;font-weight:700;color:{tier_color}'>"
                f"{tier_label}</div>"
                f"<div style='font-size:0.62rem;color:#64748b'>Cost Tier</div>"
                f"<div style='margin-top:6px;font-size:0.72rem;color:#94a3b8'>{prof['routes']} lanes</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Section: Volume commitment optimizer
# ---------------------------------------------------------------------------

def _render_volume_optimizer(
    rec: BookingRecommendation | None,
    freight_data: dict[str, pd.DataFrame],
) -> None:
    _divider("VOLUME COMMITMENT OPTIMIZER")

    st.markdown(
        "<div class='bk-section-title'>Spot vs. Long-Term Contract Balance Recommendation</div>",
        unsafe_allow_html=True,
    )

    signals = []
    for route in ROUTES[:8]:
        ts = get_market_timing_score(route.id, freight_data)
        signals.append(ts.get("percentile_52w", 0.5) or 0.5)
    avg_pct = sum(signals) / len(signals) if signals else 0.5

    if avg_pct >= 0.72:
        rec_contract = 70
        regime = "ELEVATED RATES"
        regime_color = _C_LOW
        regime_desc = (
            "Rates are near 12-month highs across major lanes. "
            "Locking in long-term contracts now protects against further increases. "
            "Recommended contract allocation: 65-75%."
        )
    elif avg_pct >= 0.45:
        rec_contract = 50
        regime = "BALANCED MARKET"
        regime_color = _C_MOD
        regime_desc = (
            "Rates are near mid-range on a 12-month basis. "
            "A balanced split gives flexibility while managing downside cost risk. "
            "Recommended contract allocation: 45-55%."
        )
    else:
        rec_contract = 30
        regime = "SOFT RATES"
        regime_color = _C_HIGH
        regime_desc = (
            "Rates are near 12-month lows. Spot markets offer the best value. "
            "Keep contract commitments minimal to benefit from continued rate softness. "
            "Recommended contract allocation: 25-35%."
        )

    rec_spot = 100 - rec_contract
    user_pct = st.session_state.get("bk_contract_pct", 40)

    fig = go.Figure(go.Pie(
        labels=["Long-Term Contract", "Spot Market"],
        values=[rec_contract, rec_spot],
        hole=0.65,
        marker=dict(
            colors=[_C_ACCENT, _C_PURPLE],
            line=dict(color=_C_BG, width=3),
        ),
        textinfo="label+percent",
        textfont=dict(size=11, color="#f1f5f9"),
        hovertemplate="<b>%{label}</b><br>%{percent}<extra></extra>",
        direction="clockwise",
        sort=False,
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=260,
        margin=dict(l=0, r=0, t=10, b=10),
        showlegend=False,
        annotations=[dict(
            text=f"<b>{rec_contract}%</b><br><span style='font-size:0.8em;color:#64748b'>Contract</span>",
            x=0.5, y=0.5,
            font=dict(color="#f1f5f9", size=16),
            showarrow=False,
        )],
    )

    vcol1, vcol2 = st.columns([1, 1.5])
    with vcol1:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="bk_volume_donut")

    with vcol2:
        diff = user_pct - rec_contract
        if abs(diff) <= 5:
            cmp_color = _C_HIGH
            cmp_text = f"Your target ({user_pct}%) aligns with the recommendation."
        elif diff > 5:
            cmp_color = _C_MOD
            cmp_text = (
                f"Your target ({user_pct}%) is {diff}pp above recommendation. "
                f"Consider reducing contract exposure in current market conditions."
            )
        else:
            cmp_color = _C_MOD
            cmp_text = (
                f"Your target ({user_pct}%) is {abs(diff)}pp below recommendation. "
                f"Consider increasing contract coverage to protect against rate rises."
            )

        st.markdown(
            f"<div style='padding-top:10px'>"
            f"<div style='font-size:0.62rem;text-transform:uppercase;letter-spacing:0.12em;"
            f"color:#64748b;margin-bottom:6px'>MARKET REGIME</div>"
            f"<div style='font-size:1.1rem;font-weight:800;color:{regime_color};margin-bottom:12px'>"
            f"{regime}</div>"
            f"<div style='font-size:0.82rem;color:#94a3b8;line-height:1.65;margin-bottom:16px'>"
            f"{regime_desc}</div>"
            f"<div style='background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);"
            f"border-left:3px solid {cmp_color};border-radius:10px;padding:14px 16px'>"
            f"<div style='font-size:0.72rem;color:#64748b;margin-bottom:4px'>YOUR TARGET vs RECOMMENDATION</div>"
            f"<div style='font-size:0.82rem;color:{cmp_color};font-weight:600'>{cmp_text}</div>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    if rec is not None:
        spot_rate = rec.estimated_rate_per_feu
        contract_rate = spot_rate * (1.0 - (0.08 if avg_pct >= 0.72 else 0.05))
        be_html = (
            "<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:4px'>"
            f"<div class='bk-metric-box'>"
            f"<div class='bk-metric-val' style='font-size:1.2rem'>${spot_rate:,.0f}</div>"
            f"<div class='bk-metric-label'>Current Spot / FEU</div>"
            f"</div>"
            f"<div class='bk-metric-box'>"
            f"<div class='bk-metric-val' style='font-size:1.2rem;color:{_C_HIGH}'>${contract_rate:,.0f}</div>"
            f"<div class='bk-metric-label'>Est. Contract / FEU</div>"
            f"</div>"
            f"<div class='bk-metric-box'>"
            f"<div class='bk-metric-val' style='font-size:1.2rem;color:{_C_ACCENT}'>${spot_rate - contract_rate:,.0f}</div>"
            f"<div class='bk-metric-label'>Annual Saving / FEU</div>"
            f"</div>"
            f"</div>"
        )
        st.markdown(be_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section: Seasonal booking calendar (heatmap)
# ---------------------------------------------------------------------------

def _render_seasonal_calendar() -> None:
    _divider("SEASONAL BOOKING CALENDAR")

    st.markdown(
        "<div class='bk-section-title'>"
        "Best &amp; Worst Booking Months — by Lane (1=Best, 5=Worst)</div>",
        unsafe_allow_html=True,
    )

    route_labels = list(_SEASONAL_SCORES.keys())
    z_data = []
    for route_name, scores in _SEASONAL_SCORES.items():
        row = [int(s) for s in scores]
        z_data.append(row)

    colorscale = [
        [0.00, "#10b981"],
        [0.25, "#34d399"],
        [0.50, "#f59e0b"],
        [0.75, "#f97316"],
        [1.00, "#ef4444"],
    ]

    z_norm = [[(v - 1) / 4 for v in row] for row in z_data]

    fig = go.Figure(go.Heatmap(
        z=z_norm,
        x=_MONTH_LABELS,
        y=route_labels,
        colorscale=colorscale,
        showscale=True,
        colorbar=dict(
            title=dict(text="Booking Quality", side="right", font=dict(color="#64748b", size=11)),
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["Best", "Good", "Neutral", "Poor", "Worst"],
            tickfont=dict(color="#64748b", size=10),
            thickness=12,
            len=0.8,
        ),
        text=[[str(v) for v in row] for row in z_data],
        texttemplate="%{text}",
        textfont=dict(size=12, color="#f1f5f9"),
        hovertemplate="<b>%{y}</b><br>%{x}: Score %{text}<extra></extra>",
        zmin=0, zmax=1,
        xgap=3, ygap=3,
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=330,
        margin=dict(l=180, r=80, t=10, b=30),
        xaxis=dict(
            side="top",
            tickfont=dict(size=11, color="#94a3b8"),
            showgrid=False,
        ),
        yaxis=dict(
            tickfont=dict(size=11, color="#94a3b8"),
            showgrid=False,
            autorange="reversed",
        ),
        font=dict(color="#94a3b8"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="bk_seasonal_heatmap")

    st.markdown(
        "<div style='display:flex;gap:18px;margin-top:2px;flex-wrap:wrap'>"
        + "".join(
            f"<div style='display:flex;align-items:center;gap:6px;font-size:0.72rem;color:#64748b'>"
            f"<div style='width:12px;height:12px;border-radius:3px;background:{c}'></div>"
            f"{label}</div>"
            for label, c in [
                ("1 - Best rates / book freely", "#10b981"),
                ("2 - Good window", "#34d399"),
                ("3 - Neutral", "#f59e0b"),
                ("4 - Elevated / plan ahead", "#f97316"),
                ("5 - Peak / book early", "#ef4444"),
            ]
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    current_month_idx = date.today().month - 1
    current_month = _MONTH_LABELS[current_month_idx]
    good_now = [r for r, scores in _SEASONAL_SCORES.items() if int(scores[current_month_idx]) <= 2]
    avoid_now = [r for r, scores in _SEASONAL_SCORES.items() if int(scores[current_month_idx]) >= 4]

    if good_now or avoid_now:
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        if good_now:
            with c1:
                st.markdown(
                    f"<div style='background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);"
                    f"border-radius:12px;padding:14px 16px'>"
                    f"<div style='font-size:0.65rem;text-transform:uppercase;letter-spacing:0.1em;"
                    f"color:#10b981;font-weight:700;margin-bottom:8px'>GOOD TO BOOK IN {current_month.upper()}</div>"
                    + "".join(f"<div style='font-size:0.78rem;color:#94a3b8;line-height:1.8'>+ {r}</div>" for r in good_now)
                    + "</div>",
                    unsafe_allow_html=True,
                )
        if avoid_now:
            with c2:
                st.markdown(
                    f"<div style='background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);"
                    f"border-radius:12px;padding:14px 16px'>"
                    f"<div style='font-size:0.65rem;text-transform:uppercase;letter-spacing:0.1em;"
                    f"color:#ef4444;font-weight:700;margin-bottom:8px'>AVOID / PLAN EARLY IN {current_month.upper()}</div>"
                    + "".join(f"<div style='font-size:0.78rem;color:#94a3b8;line-height:1.8'>! {r}</div>" for r in avoid_now)
                    + "</div>",
                    unsafe_allow_html=True,
                )


# ---------------------------------------------------------------------------
# Section: Rolling capacity vs demand forecast
# ---------------------------------------------------------------------------

def _render_capacity_forecast(freight_data: dict[str, pd.DataFrame]) -> None:
    _divider("CAPACITY vs. DEMAND FORECAST - 16-WEEK ROLLING OUTLOOK")

    st.markdown(
        "<div class='bk-section-title'>Available Capacity vs. Projected Demand — Major Lanes</div>",
        unsafe_allow_html=True,
    )

    focus_routes = [
        r for r in ROUTES
        if r.id in ("transpacific_eb", "asia_europe", "transatlantic", "china_south_america")
    ]

    weeks = list(range(1, 17))
    week_labels = [(date.today() + timedelta(weeks=w)).strftime("W%V %b %d") for w in weeks]

    fig = go.Figure()
    palette = [_C_ACCENT, _C_HIGH, _C_MOD, _C_PURPLE]

    for ridx, route in enumerate(focus_routes):
        ts = get_market_timing_score(route.id, freight_data)
        pct_52w = ts.get("percentile_52w", 0.5) or 0.5
        signal  = ts.get("timing_signal", "NEUTRAL")

        base_util = 0.55 + pct_52w * 0.35

        month = date.today().month
        if month in (7, 8, 9, 10):
            season_push = 0.06
        elif month in (1, 2):
            season_push = -0.04
        else:
            season_push = 0.01

        color = palette[ridx % len(palette)]
        seed = hash(route.id) % 1000

        rng = np.random.default_rng(seed)
        demand_vals = []
        for w in weeks:
            noise = rng.uniform(-3, 3)
            trend = min(w * 0.3, 4.0) if signal in ("EXPENSIVE", "WAIT") else 0.0
            demand_vals.append(
                max(30, min(105, (base_util + season_push) * 100 + trend + noise))
            )

        fig.add_trace(go.Scatter(
            x=week_labels,
            y=demand_vals,
            name=route.name[:22] + ("..." if len(route.name) > 22 else ""),
            line=dict(color=color, width=2),
            mode="lines+markers",
            marker=dict(size=5, color=color),
            hovertemplate=f"<b>{route.name}</b><br>Week: %{{x}}<br>Demand: %{{y:.1f}}%<extra></extra>",
        ))

    fig.add_hline(
        y=100, line_dash="dot", line_color="rgba(239,68,68,0.5)",
        annotation_text="Full Capacity", annotation_position="right",
        annotation_font=dict(color="#ef4444", size=10),
    )
    fig.add_hline(
        y=80, line_dash="dash", line_color="rgba(245,158,11,0.4)",
        annotation_text="Tight Market (80%)", annotation_position="right",
        annotation_font=dict(color="#f59e0b", size=10),
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=360,
        margin=dict(l=10, r=100, t=10, b=80),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)", font=dict(color="#94a3b8", size=10),
        ),
        xaxis=dict(
            tickangle=-30, tickfont=dict(size=9, color="#64748b"),
            gridcolor="rgba(255,255,255,0.04)", showgrid=True,
        ),
        yaxis=dict(
            range=[30, 115],
            ticksuffix="%",
            tickfont=dict(size=10, color="#64748b"),
            gridcolor="rgba(255,255,255,0.05)",
            title=dict(text="Capacity Utilisation (%)", font=dict(color="#475569", size=11)),
        ),
        font=dict(color="#94a3b8"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="bk_capacity_forecast")

    alert_html = "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-top:4px'>"
    for route in focus_routes:
        ts = get_market_timing_score(route.id, freight_data)
        pct_52w = ts.get("percentile_52w", 0.5) or 0.5
        util = int((0.55 + pct_52w * 0.35) * 100)
        if util >= 85:
            tile_color = _C_LOW
            tile_label = "Tight - Book Now"
        elif util >= 70:
            tile_color = _C_MOD
            tile_label = "Moderate - Monitor"
        else:
            tile_color = _C_HIGH
            tile_label = "Available - Flexible"
        alert_html += (
            f"<div style='background:#111827;border:1px solid {tile_color}33;"
            f"border-top:3px solid {tile_color};border-radius:12px;padding:14px 16px'>"
            f"<div style='font-size:0.72rem;color:#64748b;margin-bottom:4px'>{route.name}</div>"
            f"<div style='font-size:1.1rem;font-weight:800;color:{tile_color}'>{util}%</div>"
            f"<div style='font-size:0.7rem;font-weight:600;color:{tile_color}'>{tile_label}</div>"
            f"{_progress_bar(util, tile_color)}"
            f"</div>"
        )
    alert_html += "</div>"
    st.markdown(alert_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section: Full logistics cost breakdown (donut)
# ---------------------------------------------------------------------------

def _render_cost_breakdown(recommendation: BookingRecommendation) -> None:
    _divider("FULL LOGISTICS COST BREAKDOWN")

    st.markdown(
        "<div class='bk-section-title'>Door-to-Door Cost Estimate — All Components</div>",
        unsafe_allow_html=True,
    )

    breakdown = estimate_total_logistics_cost(recommendation)
    if not breakdown:
        st.info("Cost breakdown is not available for this route.")
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
        "ocean_freight":          _C_ACCENT,
        "port_handling":          _C_PURPLE,
        "inland_drayage_origin":  _C_CYAN,
        "inland_drayage_dest":    "#0891b2",
        "documentation":          _C_MOD,
        "insurance":              _C_HIGH,
        "carbon_offset":          "#6b7280",
    }

    present = [k for k in labels_map if k in breakdown and breakdown.get(k, 0) > 0]
    if not present:
        st.info("No cost components found.")
        return

    labels  = [labels_map[k] for k in present]
    values  = [breakdown[k] for k in present]
    colors  = [color_map[k] for k in present]
    total   = breakdown.get("total") or sum(values)

    if total <= 0:
        st.info("Total cost is zero — cannot render breakdown.")
        return

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.62,
        marker=dict(colors=colors, line=dict(color=_C_BG, width=2)),
        textinfo="label+percent",
        textfont=dict(size=11, color="#f1f5f9"),
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>",
        direction="clockwise",
        sort=False,
        showlegend=False,
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=320,
        margin=dict(l=10, r=10, t=10, b=10),
        annotations=[dict(
            text=f"<b>${total:,.0f}</b><br><span style='font-size:0.8em;color:#64748b'>Total</span>",
            x=0.5, y=0.5,
            font=dict(color="#f1f5f9", size=14),
            showarrow=False,
        )],
    )

    chart_col, detail_col = st.columns([1.3, 1])
    with chart_col:
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="bk_cost_donut")

    with detail_col:
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        tbl = "<table style='width:100%;font-size:0.78rem;border-collapse:collapse'>"
        for k in present:
            val = breakdown[k]
            pct = val / total * 100 if total > 0 else 0
            c = color_map[k]
            tbl += (
                f"<tr style='border-bottom:1px solid rgba(255,255,255,0.04)'>"
                f"<td style='padding:7px 6px'>"
                f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;"
                f"background:{c};margin-right:8px'></span>"
                f"<span style='color:#94a3b8'>{labels_map[k]}</span></td>"
                f"<td style='padding:7px 6px;text-align:right;color:#f1f5f9;font-weight:600'>"
                f"${val:,.0f}</td>"
                f"<td style='padding:7px 6px;text-align:right;color:#64748b'>{pct:.1f}%</td>"
                f"</tr>"
            )
        tbl += (
            "<tr style='border-top:2px solid rgba(255,255,255,0.10)'>"
            "<td style='padding:8px 6px;color:#f1f5f9;font-weight:700'>Total</td>"
            f"<td style='padding:8px 6px;text-align:right;color:{_C_ACCENT};font-weight:800'>"
            f"${total:,.0f}</td>"
            "<td style='padding:8px 6px;text-align:right;color:#64748b'>100%</td>"
            "</tr></table>"
        )
        st.markdown(tbl, unsafe_allow_html=True)

    csv_buf = io.StringIO()
    wr = csv.writer(csv_buf)
    wr.writerow(["Cost Component", "Amount (USD)", "Share (%)"])
    for k in present:
        val = breakdown[k]
        pct = val / total * 100 if total > 0 else 0
        wr.writerow([labels_map[k], f"{val:.2f}", f"{pct:.1f}"])
    wr.writerow(["Total", f"{total:.2f}", "100.0"])
    st.download_button(
        "Download Cost Breakdown (CSV)",
        data=csv_buf.getvalue(),
        file_name="booking_cost_breakdown.csv",
        mime="text/csv",
        key="bk_cost_csv",
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
    _inject_css()
    _render_hero()

    port_names = _port_name_map()

    # -- Booking form --
    scenario = _render_booking_form(port_names)

    if scenario is not None:
        with st.spinner("Running booking optimization..."):
            try:
                rec = optimize_booking(scenario, route_results, freight_data, macro_data)
                st.session_state["booking_rec"] = rec
            except Exception as exc:
                logger.error(f"Booking optimization failed: {exc}")
                st.error(f"Optimization failed: {exc}")
                st.session_state.pop("booking_rec", None)

    rec: BookingRecommendation | None = st.session_state.get("booking_rec")

    # -- Top-3 recommendations (only after form submission) --
    if rec is not None:
        _render_top3_recommendations(rec, port_names, freight_data, macro_data)

    # -- Always-visible sections --
    _render_rate_timing_matrix(freight_data)
    _render_spot_vs_contract(freight_data)
    _render_lead_time_optimizer(freight_data)
    _render_carrier_guide(freight_data)
    _render_volume_optimizer(rec, freight_data)
    _render_seasonal_calendar()
    _render_capacity_forecast(freight_data)

    # -- Cost breakdown (only after form submission) --
    if rec is not None:
        _render_cost_breakdown(rec)
