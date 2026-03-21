"""Port Operations Monitor tab — comprehensive global port intelligence dashboard.

Sections
--------
A. Command Dashboard      — Critical alert banner + 4 headline KPI cards
B. Port Status Grid       — Enhanced 5-col grid with filter chips & trend arrows
C. Global Port Map        — Scattergeo with TEU-sized, status-coloured markers
D. Throughput Trends      — 7-day multi-line sparkline for top 8 ports
E. Arrival Forecast       — Grouped bar: vessel arrivals vs departures by region
F. Berth Heatmap          — Port × hour-of-day utilisation (go.Heatmap)
G. Efficiency Rankings    — Composite score bar chart + sortable table
H. Alert Log              — Timestamped severity cards (CRITICAL / WARNING / INFO)
1. Port Status Grid       — Original selector-driven grid (detail focus)
2. Live Port Detail Panel — Gauge, hourly bars, crane productivity
3. World Port Benchmark   — Ranked colour-coded table
4. Port Capacity by Region — Stacked area, Tuas projection
5. Anomaly Feed           — Real-time port warnings
"""
from __future__ import annotations

import math
import random as _rand
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from loguru import logger

from processing.port_monitor import (
    PORT_OPERATIONAL_DATA,
    PORT_OPERATIONAL_BY_LOCODE,
    PORT_PERFORMANCE_BENCHMARKS,
    PERF_BY_LOCODE,
    PortOperationalStatus,
    PortPerformanceMetric,
    get_all_live_stats,
    get_all_anomalies,
    detect_port_anomalies,
    simulate_live_throughput,
)
from ui.styles import (
    C_BG, C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_MOD, C_LOW, C_ACCENT,
    _hex_to_rgba, section_header, dark_layout,
)

# ── Module-level colour palette ───────────────────────────────────────────────

_C_NORMAL    = "#10b981"   # green
_C_DEGRADED  = "#f59e0b"   # amber
_C_DISRUPTED = "#ef4444"   # red
_C_BLUE      = "#3b82f6"
_C_PURPLE    = "#8b5cf6"
_C_CYAN      = "#06b6d4"
_C_GRAY      = "#475569"
_C_BG_SURF   = "#111827"
_C_ROSE      = "#f43f5e"
_C_LIME      = "#84cc16"
_C_INDIGO    = "#6366f1"
_C_ORANGE    = "#f97316"
_C_TEAL      = "#14b8a6"

_STATUS_COLOR = {
    "NORMAL":    _C_NORMAL,
    "DEGRADED":  _C_DEGRADED,
    "DISRUPTED": _C_DISRUPTED,
}

_REGION_COLORS = {
    "Asia East":          _C_BLUE,
    "Southeast Asia":     _C_CYAN,
    "Europe":             _C_PURPLE,
    "North America West": _C_NORMAL,
    "North America East": "#34d399",
    "Middle East":        _C_DEGRADED,
    "Africa":             _C_ORANGE,
    "South Asia":         "#a78bfa",
    "South America":      "#fb923c",
}

# Port lat/lon lookup
_PORT_COORDS: dict[str, tuple[float, float]] = {
    "CNSHA": (31.23,  121.47),
    "SGSIN": (1.29,   103.86),
    "CNNBO": (29.87,  121.55),
    "KRPUS": (35.10,  129.04),
    "HKHKG": (22.32,  114.17),
    "CNSZN": (22.54,  114.06),
    "NLRTM": (51.92,    4.48),
    "DEHAM": (53.55,    9.99),
    "BEANR": (51.23,    4.40),
    "USLAX": (33.74, -118.26),
    "USLGB": (33.76, -118.19),
    "AEJEA": (25.00,   55.17),
    "MYPKG": (3.00,   101.40),
    "MATNM": (35.77,   -5.80),
    "CNTAO": (36.07,  120.38),
    "CNTXG": (39.00,  117.72),
    "GRPIR": (37.94,   23.64),
    "LKCMB": (6.96,   79.85),
    "MYTPP": (1.42,  103.64),
    "USNYC": (40.70,  -74.02),
    "BRSAO": (-23.93, -46.32),
    "USSAV": (31.98,  -81.10),
    "GBFXT": (51.45,    0.37),
    "JPYOK": (35.45,  139.63),
    "TWKHH": (22.62,  120.27),
}

# Port region mapping for arrival forecast
_PORT_REGION: dict[str, str] = {
    "CNSHA": "Asia East",     "CNNBO": "Asia East",  "CNSZN": "Asia East",
    "CNTAO": "Asia East",     "CNTXG": "Asia East",  "HKHKG": "Asia East",
    "KRPUS": "Asia East",     "TWKHH": "Asia East",  "JPYOK": "Asia East",
    "SGSIN": "SE Asia",       "MYPKG": "SE Asia",    "MYTPP": "SE Asia",
    "NLRTM": "Europe",        "DEHAM": "Europe",     "BEANR": "Europe",
    "GRPIR": "Europe",        "GBFXT": "Europe",
    "USLAX": "N. America",    "USLGB": "N. America", "USNYC": "N. America",
    "USSAV": "N. America",
    "AEJEA": "Middle East",
    "MATNM": "Africa",        "LKCMB": "S. Asia",    "BRSAO": "S. America",
}


# ── Utility helpers ───────────────────────────────────────────────────────────

def _utc_ts() -> str:
    dt = datetime.now(timezone.utc)
    return (
        str(dt.hour).zfill(2) + ":"
        + str(dt.minute).zfill(2) + ":"
        + str(dt.second).zfill(2) + " UTC"
    )


def _mini_bar_html(pct: float, color: str, width: int = 100) -> str:
    filled = max(0.0, min(100.0, pct))
    bar_fill = str(int(filled)) + "%"
    return (
        "<div style='width:" + str(width) + "px;background:rgba(255,255,255,0.08);"
        + "border-radius:4px;height:5px;overflow:hidden;margin:2px 0'>"
        + "<div style='width:" + bar_fill + ";background:" + color + ";height:100%;'></div>"
        + "</div>"
    )


def _status_badge_html(status: str) -> str:
    color  = _STATUS_COLOR.get(status, _C_GRAY)
    bg     = _hex_to_rgba(color, 0.18)
    border = _hex_to_rgba(color, 0.35)
    return (
        "<span style='display:inline-block;padding:1px 8px;border-radius:999px;"
        + "font-size:0.66rem;font-weight:700;letter-spacing:0.05em;"
        + "background:" + bg + ";color:" + color + ";border:1px solid " + border + ";'>"
        + status + "</span>"
    )


def _teu_fmt(teu: int | float) -> str:
    teu = int(teu)
    if teu >= 1_000_000:
        return str(round(teu / 1_000_000, 2)) + "M"
    if teu >= 1000:
        return str(round(teu / 1000, 1)) + "k"
    return str(teu)


def _trend_arrow(current: float, previous: float) -> tuple[str, str]:
    """Return (arrow_char, color) for a trend indicator."""
    if current > previous * 1.01:
        return ("▲", _C_NORMAL)
    if current < previous * 0.99:
        return ("▼", _C_DISRUPTED)
    return ("▶", _C_GRAY)


def _severity_label(color: str) -> str:
    if color == _C_DISRUPTED or color == _C_ROSE:
        return "CRITICAL"
    if color == _C_DEGRADED:
        return "WARNING"
    return "INFO"


def _hr() -> None:
    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07);margin:24px 0'>",
        unsafe_allow_html=True,
    )


# ── Section A: Command Dashboard ─────────────────────────────────────────────

def _render_command_dashboard(live_all: dict) -> None:
    """Critical alert banner + 4 headline KPI cards in premium style."""
    try:
        # Compute headline KPIs
        vessels_in_port  = 0
        global_teu_today = 0
        congested_count  = 0
        berth_occs       = []
        critical_alerts  = 0
        disrupted_count  = 0

        for port in PORT_OPERATIONAL_DATA:
            live = live_all.get(port.port_locode, {})
            berth_p = live.get("berth_occupancy_pct", 75.0)
            queue   = live.get("vessel_queue", 0)
            teu     = live.get("throughput_teu", port.throughput_today_teu)
            flags   = detect_port_anomalies(port.port_locode, live)

            vessels_in_port  += port.berth_count + queue
            global_teu_today += teu
            berth_occs.append(berth_p)
            critical_alerts  += len(flags)
            if port.operational_status == "DISRUPTED":
                disrupted_count += 1
            if berth_p >= 88 or port.operational_status in ("DEGRADED", "DISRUPTED"):
                congested_count += 1

        avg_berth_occ = sum(berth_occs) / len(berth_occs) if berth_occs else 0.0

        # ── Critical alert banner ──────────────────────────────────────────
        if disrupted_count > 0 or critical_alerts > 8:
            banner_color  = _C_DISRUPTED
            banner_icon   = "🔴"
            banner_msg    = (
                str(disrupted_count) + " port(s) in DISRUPTED state  ·  "
                + str(critical_alerts) + " anomaly flags active  ·  "
                + str(congested_count) + " ports congested or degraded"
            )
        elif congested_count > 4 or critical_alerts > 3:
            banner_color = _C_DEGRADED
            banner_icon  = "🟡"
            banner_msg   = (
                str(congested_count) + " ports congested or degraded  ·  "
                + str(critical_alerts) + " anomaly flags active  ·  "
                "Monitor throughput closely"
            )
        else:
            banner_color = _C_NORMAL
            banner_icon  = "🟢"
            banner_msg   = (
                "All ports operating within normal parameters  ·  "
                + str(critical_alerts) + " minor flags  ·  "
                + _utc_ts()
            )

        st.markdown(
            "<div style='background:" + _hex_to_rgba(banner_color, 0.10) + ";"
            + "border:1px solid " + _hex_to_rgba(banner_color, 0.40) + ";"
            + "border-left:4px solid " + banner_color + ";"
            + "border-radius:10px;padding:12px 18px;margin-bottom:18px;"
            + "display:flex;align-items:center;gap:12px'>"
            + "<span style='font-size:1.3rem'>" + banner_icon + "</span>"
            + "<div>"
            + "<div style='font-size:0.72rem;font-weight:800;color:" + banner_color + ";"
            + "text-transform:uppercase;letter-spacing:0.08em;margin-bottom:2px'>"
            + _severity_label(banner_color) + " — PORT COMMAND SUMMARY</div>"
            + "<div style='font-size:0.82rem;color:" + C_TEXT + ";line-height:1.4'>"
            + banner_msg + "</div>"
            + "</div></div>",
            unsafe_allow_html=True,
        )

        # ── 4 KPI cards ───────────────────────────────────────────────────
        kpi_data = [
            {
                "label":   "Vessels in Port",
                "value":   "{:,}".format(vessels_in_port),
                "sub":     "at-berth + queued across 25 ports",
                "color":   _C_BLUE,
                "icon":    "⚓",
                "detail":  str(disrupted_count) + " disrupted",
            },
            {
                "label":   "Global TEU Today",
                "value":   _teu_fmt(global_teu_today),
                "sub":     "combined throughput all ports",
                "color":   _C_CYAN,
                "icon":    "📦",
                "detail":  "25-port aggregate",
            },
            {
                "label":   "Congested Ports",
                "value":   str(congested_count),
                "sub":     "berth ≥88% or degraded status",
                "color":   _C_DEGRADED if congested_count > 3 else _C_NORMAL,
                "icon":    "⚠️",
                "detail":  str(disrupted_count) + " fully disrupted",
            },
            {
                "label":   "Avg Berth Occupancy",
                "value":   str(round(avg_berth_occ, 1)) + "%",
                "sub":     "fleet-wide berth utilisation",
                "color":   (
                    _C_DISRUPTED if avg_berth_occ >= 90 else
                    _C_DEGRADED  if avg_berth_occ >= 78 else
                    _C_NORMAL
                ),
                "icon":    "📊",
                "detail":  "Target < 85%",
            },
        ]

        cols = st.columns(4)
        for ci, kpi in enumerate(kpi_data):
            with cols[ci]:
                st.markdown(
                    "<div style='background:" + C_CARD + ";"
                    + "border:1px solid rgba(255,255,255,0.07);"
                    + "border-top:3px solid " + kpi["color"] + ";"
                    + "border-radius:12px;padding:18px 16px;position:relative;overflow:hidden'>"
                    # Background glow orb
                    + "<div style='position:absolute;top:-20px;right:-20px;width:80px;height:80px;"
                    + "border-radius:50%;background:" + _hex_to_rgba(kpi["color"], 0.08) + ";"
                    + "filter:blur(12px)'></div>"
                    # Icon
                    + "<div style='font-size:1.4rem;margin-bottom:8px'>" + kpi["icon"] + "</div>"
                    # Main value
                    + "<div style='font-size:2.1rem;font-weight:900;color:" + kpi["color"] + ";"
                    + "letter-spacing:-0.03em;line-height:1;margin-bottom:4px'>"
                    + kpi["value"] + "</div>"
                    # Label
                    + "<div style='font-size:0.73rem;font-weight:700;color:" + C_TEXT + ";"
                    + "text-transform:uppercase;letter-spacing:0.05em;margin-bottom:3px'>"
                    + kpi["label"] + "</div>"
                    # Sub label
                    + "<div style='font-size:0.65rem;color:" + C_TEXT3 + ";margin-bottom:6px'>"
                    + kpi["sub"] + "</div>"
                    # Detail chip
                    + "<div style='display:inline-block;background:" + _hex_to_rgba(kpi["color"], 0.12) + ";"
                    + "border:1px solid " + _hex_to_rgba(kpi["color"], 0.30) + ";"
                    + "border-radius:999px;padding:1px 8px;font-size:0.62rem;"
                    + "color:" + kpi["color"] + ";font-weight:600'>"
                    + kpi["detail"] + "</div>"
                    + "</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        st.warning("Command dashboard temporarily unavailable.")


# ── Section B: Enhanced Port Status Grid with Filter Chips ───────────────────

def _display_status(port: PortOperationalStatus, live: dict) -> tuple[str, str]:
    """Return (display_label, color) for a port."""
    berth_p = live.get("berth_occupancy_pct", 75.0)
    status  = port.operational_status
    if status == "DISRUPTED":
        return "DISRUPTED", _C_DISRUPTED
    if berth_p >= 88 or status == "DEGRADED":
        return "CONGESTED", _C_DEGRADED
    return "OPERATING", _C_NORMAL


def _port_status_card_v2(port: PortOperationalStatus, live: dict, prev_live: dict) -> str:
    """Enhanced card with status badge, trend arrow, wait time, TEU bar."""
    try:
        disp_status, disp_color = _display_status(port, live)

        teu      = live.get("throughput_teu", port.throughput_today_teu)
        prev_teu = prev_live.get("throughput_teu", teu * 0.97)
        berth_p  = live.get("berth_occupancy_pct", 75.0)
        queue    = live.get("vessel_queue", 0)
        wait_m   = live.get("gate_wait_minutes", 45.0)

        arrow, arrow_color = _trend_arrow(teu, prev_teu)

        badge_bg     = _hex_to_rgba(disp_color, 0.15)
        badge_border = _hex_to_rgba(disp_color, 0.40)

        wait_color = (
            _C_DISRUPTED if wait_m > 120 else
            _C_DEGRADED  if wait_m > 60  else
            _C_NORMAL
        )

        bar_pct  = min(100.0, berth_p)
        bar_html = (
            "<div style='background:rgba(255,255,255,0.07);border-radius:4px;"
            + "height:4px;margin:4px 0 0 0;overflow:hidden'>"
            + "<div style='width:" + str(int(bar_pct)) + "%;background:" + disp_color + ";"
            + "height:100%;border-radius:4px'></div>"
            + "</div>"
        )

        return (
            "<div style='background:" + _hex_to_rgba(disp_color, 0.05) + ";"
            + "border:1px solid " + _hex_to_rgba(disp_color, 0.25) + ";"
            + "border-radius:10px;padding:10px 11px;margin-bottom:8px;position:relative'>"
            # Top row: flag + badge
            + "<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:5px'>"
            + "<span style='font-size:1.05rem'>" + port.country_flag + "</span>"
            + "<span style='background:" + badge_bg + ";color:" + disp_color + ";"
            + "border:1px solid " + badge_border + ";padding:1px 6px;border-radius:999px;"
            + "font-size:0.58rem;font-weight:800;letter-spacing:0.05em'>" + disp_status + "</span>"
            + "</div>"
            # Port name + locode
            + "<div style='font-size:0.75rem;font-weight:700;color:" + C_TEXT + ";line-height:1.2;margin-bottom:1px'>"
            + port.port_name + "</div>"
            + "<div style='font-size:0.63rem;color:" + C_TEXT3 + ";margin-bottom:5px'>"
            + port.port_locode + "</div>"
            # TEU + trend arrow
            + "<div style='display:flex;align-items:center;gap:5px;margin-bottom:3px'>"
            + "<span style='font-size:0.88rem;font-weight:700;color:" + C_TEXT + "'>" + _teu_fmt(teu) + "</span>"
            + "<span style='font-size:0.62rem;color:" + C_TEXT3 + "'>TEU/d</span>"
            + "<span style='font-size:0.70rem;color:" + arrow_color + ";margin-left:2px'>" + arrow + "</span>"
            + "</div>"
            # Vessels + wait
            + "<div style='display:flex;align-items:center;justify-content:space-between'>"
            + "<span style='font-size:0.65rem;color:" + C_TEXT2 + "'>⚓ " + str(queue + port.berth_count) + " vessels</span>"
            + "<span style='font-size:0.65rem;color:" + wait_color + ";font-weight:700'>"
            + str(int(wait_m)) + "min wait</span>"
            + "</div>"
            # Berth utilisation bar
            + "<div style='font-size:0.60rem;color:" + C_TEXT3 + ";margin-top:4px'>Berth "
            + str(round(berth_p, 0))[:-2] + "%</div>"
            + bar_html
            + "</div>"
        )
    except Exception:
        return "<div style='padding:10px;color:#64748b;font-size:0.72rem'>" + port.port_name + "</div>"


def _render_enhanced_port_grid_v2(live_all: dict) -> None:
    """5-col grid with status filter chips and trend arrows."""
    try:
        section_header(
            "Port Status Grid",
            "All 25 tracked ports — live operational status · " + _utc_ts(),
        )

        # Filter chips
        filter_opts = ["ALL", "OPERATING", "CONGESTED", "DISRUPTED"]
        if "pm_status_filter" not in st.session_state:
            st.session_state["pm_status_filter"] = "ALL"

        chip_cols = st.columns(len(filter_opts) + 4)
        for ci, opt in enumerate(filter_opts):
            with chip_cols[ci]:
                chip_color = {
                    "ALL":       _C_BLUE,
                    "OPERATING": _C_NORMAL,
                    "CONGESTED": _C_DEGRADED,
                    "DISRUPTED": _C_DISRUPTED,
                }.get(opt, _C_GRAY)
                is_active = st.session_state["pm_status_filter"] == opt
                if st.button(
                    opt,
                    key="pm_chip_" + opt,
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state["pm_status_filter"] = opt
                    st.rerun()

        active_filter = st.session_state.get("pm_status_filter", "ALL")

        # Build previous-tick pseudo-data (seeded offset for trend arrows)
        rng_prev = _rand.Random(42)

        ports_to_show = []
        for port in PORT_OPERATIONAL_DATA:
            live = live_all.get(port.port_locode, {})
            disp_status, _ = _display_status(port, live)
            if active_filter == "ALL" or disp_status == active_filter:
                ports_to_show.append(port)

        if not ports_to_show:
            st.info("No ports match the selected filter.")
            return

        cols_per_row = 5
        num_rows = math.ceil(len(ports_to_show) / cols_per_row)

        for row in range(num_rows):
            cols = st.columns(cols_per_row)
            for ci in range(cols_per_row):
                idx = row * cols_per_row + ci
                if idx >= len(ports_to_show):
                    break
                port = ports_to_show[idx]
                live = live_all.get(port.port_locode, {})
                # Synthetic "previous" data for trend arrows
                prev_teu  = live.get("throughput_teu", port.throughput_today_teu)
                prev_live = {"throughput_teu": int(prev_teu * rng_prev.gauss(0.98, 0.025))}
                with cols[ci]:
                    st.markdown(
                        _port_status_card_v2(port, live, prev_live),
                        unsafe_allow_html=True,
                    )
    except Exception as exc:
        st.warning("Port status grid error: " + str(exc))


# ── Section C: Global Port Map ────────────────────────────────────────────────

def _render_global_port_map(live_all: dict) -> None:
    """Scattergeo map — TEU-sized markers, status-coloured, rich hover."""
    try:
        section_header(
            "Global Port Map",
            "Marker size = TEU volume  ·  colour = operational status  ·  hover for details",
        )

        lats, lons, texts, colors, sizes, symbols = [], [], [], [], [], []

        for port in PORT_OPERATIONAL_DATA:
            coords = _PORT_COORDS.get(port.port_locode)
            if not coords:
                continue
            live    = live_all.get(port.port_locode, {})
            berth_p = live.get("berth_occupancy_pct", 75.0)
            queue   = live.get("vessel_queue", 0)
            teu     = live.get("throughput_teu", port.throughput_today_teu)
            wait_m  = live.get("gate_wait_minutes", 45.0)
            perf    = PERF_BY_LOCODE.get(port.port_locode)

            disp_status, color = _display_status(port, live)

            # Size scaled by daily TEU (log scale, clamped 12-32)
            sz = max(12, min(32, 10 + int(math.log10(max(teu, 1)) * 3.8)))

            lats.append(coords[0])
            lons.append(coords[1])
            colors.append(color)
            sizes.append(sz)
            texts.append(
                "<b>" + port.country_flag + " " + port.port_name + "</b>"
                + "  <span style='color:#94a3b8'>(" + port.port_locode + ")</span><br>"
                + "<b>Status:</b> " + disp_status + "<br>"
                + "<b>TEU/day:</b> " + _teu_fmt(teu) + "<br>"
                + "<b>Berth:</b> " + str(round(berth_p, 1)) + "%"
                + "  <b>Queue:</b> " + str(queue) + " vessels<br>"
                + "<b>Gate wait:</b> " + str(int(wait_m)) + " min"
                + ("  <b>Crane:</b> " + str(perf.crane_productivity_moves_per_hour) + " mph" if perf else "")
            )

        fig = go.Figure()

        # Invisible legend helpers
        for lbl, col in [("OPERATING", _C_NORMAL), ("CONGESTED", _C_DEGRADED), ("DISRUPTED", _C_DISRUPTED)]:
            fig.add_trace(go.Scattergeo(
                lat=[None], lon=[None],
                mode="markers",
                marker=dict(size=10, color=col, symbol="circle"),
                name=lbl, showlegend=True,
            ))

        # Main data trace
        fig.add_trace(go.Scattergeo(
            lat=lats, lon=lons,
            mode="markers",
            marker=dict(
                size=sizes,
                color=colors,
                opacity=0.90,
                line=dict(color="rgba(255,255,255,0.40)", width=1.2),
                symbol="circle",
            ),
            hovertext=texts,
            hoverinfo="text",
            showlegend=False,
        ))

        fig.update_layout(
            paper_bgcolor=C_BG,
            height=500,
            margin=dict(l=0, r=0, t=0, b=0),
            geo=dict(
                projection_type="natural earth",
                showland=True,       landcolor="#1a2235",
                showocean=True,      oceancolor="#080e1c",
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.14)",
                showframe=False,
                bgcolor="#080e1c",
                showcountries=True,  countrycolor="rgba(255,255,255,0.06)",
                showlakes=False,
                showrivers=False,
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom", y=1.01,
                xanchor="right",  x=1,
                font=dict(size=11, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
                title=dict(text="Status  ", font=dict(color=C_TEXT3, size=10)),
            ),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=12),
                namelength=0,
            ),
            font=dict(color=C_TEXT),
        )

        st.plotly_chart(fig, use_container_width=True, key="pm_global_map_v2")

        # Quick stat row below map
        n_operating  = sum(1 for p in PORT_OPERATIONAL_DATA if p.operational_status == "NORMAL")
        n_degraded   = sum(1 for p in PORT_OPERATIONAL_DATA if p.operational_status == "DEGRADED")
        n_disrupted  = sum(1 for p in PORT_OPERATIONAL_DATA if p.operational_status == "DISRUPTED")
        total_teu    = sum(
            live_all.get(p.port_locode, {}).get("throughput_teu", p.throughput_today_teu)
            for p in PORT_OPERATIONAL_DATA
        )

        st.markdown(
            "<div style='display:flex;gap:20px;font-size:0.72rem;color:" + C_TEXT3 + ";margin-top:-8px'>"
            + "<span>🟢 <b style='color:" + _C_NORMAL + "'>" + str(n_operating) + "</b> OPERATING</span>"
            + "<span>🟡 <b style='color:" + _C_DEGRADED + "'>" + str(n_degraded) + "</b> DEGRADED</span>"
            + "<span>🔴 <b style='color:" + _C_DISRUPTED + "'>" + str(n_disrupted) + "</b> DISRUPTED</span>"
            + "<span style='margin-left:auto'>Fleet TEU today: <b style='color:" + _C_CYAN + "'>"
            + _teu_fmt(total_teu) + "</b></span>"
            + "</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning("Global port map error: " + str(exc))


# ── Section D: Throughput Trends — 7-day sparklines ──────────────────────────

def _render_throughput_trends_v2(live_all: dict) -> None:
    """7-day multi-line sparkline for top 8 ports by TEU volume."""
    try:
        section_header(
            "Throughput Trends — Top 8 Ports",
            "7-day rolling daily TEU throughput (synthetic from baseline + live variance)",
        )

        top8_locodes = ["CNSHA", "SGSIN", "CNNBO", "KRPUS", "HKHKG", "NLRTM", "AEJEA", "DEHAM"]
        top8_colors  = [_C_BLUE, _C_CYAN, _C_PURPLE, _C_NORMAL, _C_DEGRADED, _C_INDIGO, _C_ORANGE, _C_TEAL]
        days         = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        fig = go.Figure()

        for locode, color in zip(top8_locodes, top8_colors):
            port = PORT_OPERATIONAL_BY_LOCODE.get(locode)
            if not port:
                continue
            rng      = _rand.Random(hash(locode + "7day"))
            baseline = port.throughput_baseline_daily
            series   = [
                max(0, int(baseline * rng.gauss(1.0 + 0.006 * d, 0.035)))
                for d in range(7)
            ]
            if port.operational_status != "NORMAL":
                dip = rng.randint(1, 5)
                series[dip] = int(series[dip] * 0.70)

            live_teu = live_all.get(locode, {}).get("throughput_teu", baseline)
            series[-1] = int(live_teu)   # anchor last day to live value

            fig.add_trace(go.Scatter(
                x=days, y=series,
                name=port.port_name,
                mode="lines+markers",
                line=dict(color=color, width=2.2, shape="spline", smoothing=0.6),
                marker=dict(
                    size=6, color=color,
                    line=dict(color=C_BG, width=1.5),
                    symbol="circle",
                ),
                fill="tozeroy",
                fillcolor=_hex_to_rgba(color, 0.05),
                hovertemplate="<b>" + port.port_name + "</b> %{x}: %{y:,} TEU<extra></extra>",
            ))

        fig.update_layout(
            paper_bgcolor=C_BG,
            plot_bgcolor=C_CARD,
            height=330,
            margin=dict(l=20, r=20, t=20, b=30),
            xaxis=dict(
                tickfont=dict(color=C_TEXT3, size=11),
                gridcolor="rgba(255,255,255,0.04)",
                showline=False,
            ),
            yaxis=dict(
                title="TEU / day",
                titlefont=dict(color=C_TEXT3, size=11),
                tickfont=dict(color=C_TEXT3, size=10),
                gridcolor="rgba(255,255,255,0.04)",
                showline=False,
            ),
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                font=dict(color=C_TEXT2, size=10),
                orientation="h",
                yanchor="bottom", y=1.02, xanchor="right", x=1,
            ),
            font=dict(color=C_TEXT),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=12),
            ),
        )

        st.plotly_chart(fig, use_container_width=True, key="pm_throughput_trends_v2")
    except Exception as exc:
        st.warning("Throughput trends error: " + str(exc))


# ── Section E: Arrival Forecast — Grouped Bar Chart ──────────────────────────

def _render_arrival_forecast(live_all: dict) -> None:
    """Grouped bar chart: expected vessel arrivals vs departures by region."""
    try:
        section_header(
            "Vessel Arrival Forecast — Next 7 Days",
            "Expected arrivals vs departures by region (synthetic from queue + baseline)",
        )

        regions = ["Asia East", "SE Asia", "Europe", "N. America", "Middle East", "Africa", "S. Asia", "S. America"]
        region_colors_arr = [_C_BLUE, _C_CYAN, _C_PURPLE, _C_NORMAL, _C_DEGRADED, _C_ORANGE, "#a78bfa", "#fb923c"]

        # Aggregate arrivals by region from port data
        region_arrivals: dict[str, float] = {r: 0.0 for r in regions}
        region_departures: dict[str, float] = {r: 0.0 for r in regions}

        rng_fc = _rand.Random(77)
        for port in PORT_OPERATIONAL_DATA:
            region = _PORT_REGION.get(port.port_locode, "Other")
            if region not in region_arrivals:
                continue
            live  = live_all.get(port.port_locode, {})
            queue = live.get("vessel_queue", 0)
            base  = port.berth_count * 1.6

            arrivals   = max(1, int((queue + base) * 7 * rng_fc.gauss(1.0, 0.08)))
            departures = max(1, int(arrivals * rng_fc.gauss(0.93, 0.05)))
            region_arrivals[region]   += arrivals
            region_departures[region] += departures

        arr_vals  = [region_arrivals.get(r, 0)   for r in regions]
        dep_vals  = [region_departures.get(r, 0) for r in regions]

        fig = go.Figure()

        fig.add_trace(go.Bar(
            name="Arrivals",
            x=regions,
            y=arr_vals,
            marker=dict(
                color=[_hex_to_rgba(c, 0.80) for c in region_colors_arr],
                line=dict(color=[c for c in region_colors_arr], width=1.5),
            ),
            hovertemplate="%{x}<br>Arrivals: <b>%{y}</b> vessels<extra></extra>",
        ))

        fig.add_trace(go.Bar(
            name="Departures",
            x=regions,
            y=dep_vals,
            marker=dict(
                color=[_hex_to_rgba(c, 0.35) for c in region_colors_arr],
                line=dict(color=[c for c in region_colors_arr], width=1.5),
            ),
            hovertemplate="%{x}<br>Departures: <b>%{y}</b> vessels<extra></extra>",
        ))

        fig.update_layout(
            paper_bgcolor=C_BG,
            plot_bgcolor=C_CARD,
            barmode="group",
            bargap=0.20,
            bargroupgap=0.06,
            height=340,
            margin=dict(l=20, r=20, t=20, b=50),
            xaxis=dict(
                tickfont=dict(color=C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.03)",
            ),
            yaxis=dict(
                title="Vessel calls",
                titlefont=dict(color=C_TEXT3, size=11),
                tickfont=dict(color=C_TEXT3, size=10),
                gridcolor="rgba(255,255,255,0.04)",
            ),
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                font=dict(color=C_TEXT2, size=11),
                orientation="h",
                yanchor="bottom", y=1.02, xanchor="right", x=1,
            ),
            font=dict(color=C_TEXT),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=12),
            ),
        )

        st.plotly_chart(fig, use_container_width=True, key="pm_arrival_forecast")
    except Exception as exc:
        st.warning("Arrival forecast error: " + str(exc))


# ── Section F: Berth Heatmap — Port × Hour-of-Day ────────────────────────────

def _render_berth_heatmap_hourly(live_all: dict) -> None:
    """go.Heatmap of port × hour-of-day berth utilisation for top 15 ports."""
    try:
        section_header(
            "Berth Utilisation Heatmap — Hour of Day",
            "Top 15 ports (Y) vs hour of day 0-23 (X) — red = constrained, green = available",
        )

        hours    = list(range(24))
        top15    = PORT_OPERATIONAL_DATA[:15]
        z_data   = []
        y_labels = []
        hover    = []
        rng_hm   = _rand.Random(hash("heatmap_hourly"))

        for port in top15:
            live   = live_all.get(port.port_locode, {})
            base_p = live.get("berth_occupancy_pct", 75.0)

            # Parse peak hours from port data
            peak_ranges: list[tuple[int, int]] = []
            for window in port.peak_hours:
                parts = window.split("-")
                if len(parts) == 2:
                    try:
                        s = int(parts[0].split(":")[0])
                        e = int(parts[1].split(":")[0])
                        peak_ranges.append((s, e))
                    except ValueError:
                        pass

            row, row_txt = [], []
            for h in hours:
                in_peak = any(s <= h < e for s, e in peak_ranges)
                night   = h < 5 or h >= 22
                factor  = (
                    rng_hm.gauss(1.12, 0.06) if in_peak else
                    rng_hm.gauss(0.78, 0.06) if night  else
                    rng_hm.gauss(0.93, 0.05)
                )
                pct = max(15.0, min(100.0, base_p * factor))
                row.append(round(pct, 1))
                row_txt.append(
                    port.port_name + " " + str(h).zfill(2) + ":00 — "
                    + str(round(pct, 0))[:-2] + "% berth"
                )
            z_data.append(row)
            y_labels.append(port.country_flag + " " + port.port_name)
            hover.append(row_txt)

        current_h = datetime.now(timezone.utc).hour

        fig = go.Figure(go.Heatmap(
            z=z_data,
            x=[str(h).zfill(2) + ":00" for h in hours],
            y=y_labels,
            text=[[str(round(v, 0))[:-2] + "%" for v in row] for row in z_data],
            texttemplate="%{text}",
            textfont=dict(size=8, color="rgba(255,255,255,0.65)"),
            colorscale=[
                [0.00, "#064e3b"],
                [0.40, "#10b981"],
                [0.62, "#f59e0b"],
                [0.82, "#ef4444"],
                [1.00, "#7f1d1d"],
            ],
            zmin=15, zmax=100,
            showscale=True,
            colorbar=dict(
                title=dict(text="Berth %", font=dict(color=C_TEXT2, size=10)),
                tickfont=dict(color=C_TEXT2, size=9),
                outlinecolor="rgba(0,0,0,0)",
                bgcolor="rgba(0,0,0,0)",
                len=0.85,
                thickness=14,
            ),
            hovertext=hover,
            hoverinfo="text",
        ))

        # Current-hour vertical line annotation
        fig.add_vline(
            x=str(current_h).zfill(2) + ":00",
            line=dict(color=_C_CYAN, width=1.8, dash="dot"),
            annotation_text="Now",
            annotation_font=dict(color=_C_CYAN, size=9),
            annotation_position="top",
        )

        fig.update_layout(
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            height=440,
            margin=dict(l=20, r=90, t=24, b=30),
            xaxis=dict(
                tickfont=dict(color=C_TEXT3, size=9),
                side="top",
                gridcolor="rgba(255,255,255,0.02)",
            ),
            yaxis=dict(
                tickfont=dict(color=C_TEXT2, size=9),
                autorange="reversed",
            ),
            font=dict(color=C_TEXT),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=11),
            ),
        )

        st.plotly_chart(fig, use_container_width=True, key="pm_berth_heatmap_hourly")
        st.markdown(
            "<div style='font-size:0.70rem;color:" + C_TEXT3 + ";margin-top:-8px'>"
            + "🟩 Capacity available &nbsp;·&nbsp; 🟨 Approaching constraint &nbsp;·&nbsp; 🟥 Constrained"
            + " &nbsp;·&nbsp; Cyan dotted line = current UTC hour"
            + "</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning("Berth heatmap error: " + str(exc))


# ── Section G: Efficiency Rankings ───────────────────────────────────────────

def _composite_efficiency(m: PortPerformanceMetric) -> float:
    """Score 0-100: higher = more efficient (crane, turn, dwell combined)."""
    try:
        crane_norm = min(m.crane_productivity_moves_per_hour / 35.0, 1.0)
        turn_norm  = max(0.0, 1.0 - (m.ship_turn_hours - 10) / 50.0)
        dwell_norm = max(0.0, 1.0 - (m.rail_dwell_days - 1) / 6.0)
        return round((crane_norm * 0.45 + turn_norm * 0.30 + dwell_norm * 0.25) * 100, 1)
    except Exception:
        return 50.0


def _render_efficiency_rankings(live_all: dict) -> None:
    """Composite efficiency score horizontal bar chart + sortable dataframe."""
    try:
        section_header(
            "Port Efficiency Rankings",
            "Composite score = 45% crane productivity + 30% ship turn time + 25% dwell time",
        )

        scored = []
        for m in PORT_PERFORMANCE_BENCHMARKS:
            port  = PORT_OPERATIONAL_BY_LOCODE.get(m.port_locode)
            score = _composite_efficiency(m)
            live  = live_all.get(m.port_locode, {})
            gate  = live.get("gate_wait_minutes", m.gate_truck_wait_minutes)
            scored.append({
                "locode":       m.port_locode,
                "name":         (port.country_flag + " " + port.port_name) if port else m.port_locode,
                "score":        score,
                "crane_mph":    m.crane_productivity_moves_per_hour,
                "turn_h":       m.ship_turn_hours,
                "dwell_d":      m.rail_dwell_days,
                "gate_min":     gate,
                "annual_teu_m": m.annual_teu_volume_m,
            })

        scored.sort(key=lambda x: -x["score"])

        # Top 12 horizontal bar chart
        top12 = scored[:12]
        names  = [s["name"]  for s in top12]
        scores = [s["score"] for s in top12]
        colors = [
            _C_NORMAL    if s >= 75 else
            _C_BLUE      if s >= 60 else
            _C_DEGRADED  if s >= 45 else
            _C_DISRUPTED
            for s in scores
        ]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=scores,
            y=names,
            orientation="h",
            marker=dict(
                color=[_hex_to_rgba(c, 0.75) for c in colors],
                line=dict(color=colors, width=1.5),
            ),
            text=[str(s) for s in scores],
            textposition="outside",
            textfont=dict(color=C_TEXT, size=10),
            hovertemplate="%{y}<br>Efficiency Score: <b>%{x:.1f}/100</b><extra></extra>",
        ))

        # Benchmark reference lines
        fig.add_vline(x=75, line=dict(color=_C_NORMAL, dash="dot", width=1.2),
                      annotation_text="Top tier (75)", annotation_font=dict(color=_C_NORMAL, size=9))
        fig.add_vline(x=60, line=dict(color=_C_BLUE, dash="dot", width=1.2),
                      annotation_text="Good (60)", annotation_font=dict(color=_C_BLUE, size=9),
                      annotation_position="bottom right")

        fig.update_layout(
            paper_bgcolor=C_BG,
            plot_bgcolor=C_CARD,
            height=400,
            margin=dict(l=20, r=80, t=20, b=20),
            xaxis=dict(
                title="Composite Efficiency Score (0-100)",
                range=[0, 105],
                titlefont=dict(color=C_TEXT3, size=11),
                tickfont=dict(color=C_TEXT3, size=10),
                gridcolor="rgba(255,255,255,0.04)",
            ),
            yaxis=dict(
                tickfont=dict(color=C_TEXT2, size=10),
                autorange="reversed",
            ),
            font=dict(color=C_TEXT),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=12),
            ),
            showlegend=False,
        )

        st.plotly_chart(fig, use_container_width=True, key="pm_efficiency_bar")

        # Sortable dataframe
        df_eff = pd.DataFrame([{
            "Rank":        i + 1,
            "Port":        s["name"],
            "LOCODE":      s["locode"],
            "Eff. Score":  s["score"],
            "Crane mph":   round(s["crane_mph"], 1),
            "Turn h":      round(s["turn_h"], 0),
            "Dwell d":     round(s["dwell_d"], 1),
            "Gate min":    round(s["gate_min"], 0),
            "TEU M/yr":    round(s["annual_teu_m"], 1),
        } for i, s in enumerate(scored)])

        st.dataframe(df_eff, use_container_width=True, height=440, hide_index=True)
    except Exception as exc:
        st.warning("Efficiency rankings error: " + str(exc))


# ── Section H: Alert Log — Timestamped Severity Cards ────────────────────────

_ALERT_LOG: list[dict] = [
    {"ts": "2026-03-20 07:42 UTC", "locode": "CNSHA", "severity": "WARNING",
     "change": "NORMAL → DEGRADED",
     "note": "Yangshan T4 crane utilisation crossed 98%; gate wait spiked to 134 min"},
    {"ts": "2026-03-20 05:17 UTC", "locode": "AEJEA", "severity": "INFO",
     "change": "CONGESTED → OPERATING",
     "note": "Jebel Ali throughput normalised after 3-day congestion spike from Red Sea diversions"},
    {"ts": "2026-03-19 22:08 UTC", "locode": "USLAX", "severity": "WARNING",
     "change": "NORMAL → DEGRADED",
     "note": "San Pedro Bay vessel queue reached 14; POLA anchor zone near capacity"},
    {"ts": "2026-03-19 18:55 UTC", "locode": "DEHAM", "severity": "INFO",
     "change": "DEGRADED → NORMAL",
     "note": "Hamburg Elbe tide window improved; ULCV backlog cleared after 48h delay"},
    {"ts": "2026-03-19 14:30 UTC", "locode": "BRSAO", "severity": "WARNING",
     "change": "NORMAL → DEGRADED",
     "note": "Santos customs IT migration causing intermittent delays; 4-6h processing slowdown"},
    {"ts": "2026-03-19 11:12 UTC", "locode": "MATNM", "severity": "INFO",
     "change": "OPERATING → OPERATING",
     "note": "Tanger Med Phase III fully operational; 5M TEU incremental capacity online"},
    {"ts": "2026-03-18 23:44 UTC", "locode": "KRPUS", "severity": "INFO",
     "change": "NORMAL → NORMAL",
     "note": "New Alliance restructuring adds 12% call frequency at Busan New Port"},
    {"ts": "2026-03-18 19:05 UTC", "locode": "GRPIR", "severity": "INFO",
     "change": "DEGRADED → NORMAL",
     "note": "Piraeus berth 4 back online after maintenance; COSCO terminal at full capacity"},
    {"ts": "2026-03-18 10:22 UTC", "locode": "NLRTM", "severity": "WARNING",
     "change": "NORMAL → DEGRADED",
     "note": "Rotterdam Maasvlakte II Phase 2 expansion delayed to Q3 2026 per PoR update"},
    {"ts": "2026-03-17 15:48 UTC", "locode": "LKCMB", "severity": "CRITICAL",
     "change": "DEGRADED → DISRUPTED",
     "note": "Colombo — labour dispute at UCT terminal; 40% throughput reduction; ETA resolution 48h"},
    {"ts": "2026-03-17 09:33 UTC", "locode": "SGSIN", "severity": "INFO",
     "change": "OPERATING → OPERATING",
     "note": "Tuas Phase 2 berths 9-16 commissioning on schedule; 8M TEU incremental capacity 2027"},
    {"ts": "2026-03-16 21:10 UTC", "locode": "HKHKG", "severity": "WARNING",
     "change": "NORMAL → DEGRADED",
     "note": "Typhoon Haikui track shifted; Terminal 9 precautionary closure; operations resume 06:00"},
]

_SEV_COLOR = {
    "CRITICAL": _C_DISRUPTED,
    "WARNING":  _C_DEGRADED,
    "INFO":     _C_BLUE,
}

_SEV_ICON = {
    "CRITICAL": "🔴",
    "WARNING":  "🟡",
    "INFO":     "🔵",
}


def _render_alert_log_v2() -> None:
    """Timestamped alert cards with severity chips, filter, and status transitions."""
    try:
        section_header(
            "Alert Log — Port Status Events",
            "Last 12 status change events across 25 monitored ports (newest first)",
        )

        # Severity filter
        sev_opts = ["ALL", "CRITICAL", "WARNING", "INFO"]
        if "pm_alert_filter" not in st.session_state:
            st.session_state["pm_alert_filter"] = "ALL"

        sev_cols = st.columns(len(sev_opts) + 5)
        for ci, opt in enumerate(sev_opts):
            with sev_cols[ci]:
                sev_color = _SEV_COLOR.get(opt, _C_BLUE)
                is_active = st.session_state["pm_alert_filter"] == opt
                if st.button(
                    opt,
                    key="pm_alrt_chip_" + opt,
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state["pm_alert_filter"] = opt
                    st.rerun()

        active_sev = st.session_state.get("pm_alert_filter", "ALL")
        alerts = [a for a in _ALERT_LOG if active_sev == "ALL" or a["severity"] == active_sev]

        if not alerts:
            st.info("No alerts match the selected severity filter.")
            return

        col_a, col_b = st.columns(2)
        half = math.ceil(len(alerts) / 2)

        for col, subset in [(col_a, alerts[:half]), (col_b, alerts[half:])]:
            with col:
                for alert in subset:
                    try:
                        sev     = alert["severity"]
                        color   = _SEV_COLOR.get(sev, _C_BLUE)
                        icon    = _SEV_ICON.get(sev, "🔵")
                        bg      = _hex_to_rgba(color, 0.07)
                        border  = _hex_to_rgba(color, 0.28)
                        port    = PORT_OPERATIONAL_BY_LOCODE.get(alert["locode"])
                        flag    = port.country_flag if port else ""
                        name    = port.port_name if port else alert["locode"]

                        sev_badge = (
                            "<span style='background:" + _hex_to_rgba(color, 0.18) + ";"
                            + "color:" + color + ";border:1px solid " + _hex_to_rgba(color, 0.40) + ";"
                            + "padding:1px 7px;border-radius:999px;"
                            + "font-size:0.60rem;font-weight:800;letter-spacing:0.06em'>"
                            + sev + "</span>"
                        )

                        # Change arrows
                        chg_parts = alert["change"].split("→")
                        if len(chg_parts) == 2:
                            from_s = chg_parts[0].strip()
                            to_s   = chg_parts[1].strip()
                            from_c = _STATUS_COLOR.get(from_s, _C_GRAY)
                            to_c   = _STATUS_COLOR.get(to_s, _C_GRAY)
                            chg_html = (
                                "<span style='color:" + from_c + ";font-weight:700;font-size:0.68rem'>" + from_s + "</span>"
                                + "<span style='color:" + C_TEXT3 + ";margin:0 4px'>→</span>"
                                + "<span style='color:" + to_c + ";font-weight:700;font-size:0.68rem'>" + to_s + "</span>"
                            )
                        else:
                            chg_html = "<span style='font-size:0.68rem;color:" + C_TEXT2 + "'>" + alert["change"] + "</span>"

                        st.markdown(
                            "<div style='background:" + bg + ";border:1px solid " + border + ";"
                            + "border-left:3px solid " + color + ";"
                            + "border-radius:8px;padding:10px 14px;margin-bottom:8px'>"
                            # Top row: icon + timestamp + severity badge
                            + "<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'>"
                            + "<span style='font-size:0.95rem'>" + icon + "</span>"
                            + "<span style='font-size:0.65rem;color:" + C_TEXT3 + ";flex:1'>" + alert["ts"] + "</span>"
                            + sev_badge
                            + "</div>"
                            # Port name + change
                            + "<div style='display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:4px'>"
                            + "<span style='font-size:0.85rem'>" + flag + "</span>"
                            + "<span style='font-size:0.76rem;font-weight:700;color:" + C_TEXT + "'>" + name + "</span>"
                            + "<span style='font-size:0.63rem;color:" + C_TEXT3 + "'>(" + alert["locode"] + ")</span>"
                            + "<span style='margin-left:auto'>" + chg_html + "</span>"
                            + "</div>"
                            # Note text
                            + "<div style='font-size:0.73rem;color:" + C_TEXT2 + ";line-height:1.45'>"
                            + alert["note"] + "</div>"
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                    except Exception:
                        pass

        st.markdown(
            "<div style='font-size:0.70rem;color:" + C_TEXT3 + ";margin-top:6px;text-align:right'>"
            + str(len(alerts)) + " events shown  ·  Alert log updated " + _utc_ts()
            + "</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning("Alert log error: " + str(exc))


# ── Original Section 1: Port Status Grid (selector + detail focus) ────────────

def _mini_bar_html_orig(pct: float, color: str, width: int = 100) -> str:
    filled  = max(0.0, min(100.0, pct))
    bar_fill = str(int(filled)) + "%"
    return (
        "<div style='width:" + str(width) + "px;background:rgba(255,255,255,0.08);"
        + "border-radius:4px;height:5px;overflow:hidden;margin:2px 0'>"
        + "<div style='width:" + bar_fill + ";background:" + color + ";height:100%;'></div>"
        + "</div>"
    )


def _port_card_html(
    port: PortOperationalStatus,
    live: dict,
    selected: bool,
) -> str:
    try:
        color  = _STATUS_COLOR.get(port.operational_status, _C_GRAY)
        border = "2px solid " + color if selected else "1px solid " + _hex_to_rgba(color, 0.35)
        bg     = _hex_to_rgba(color, 0.07) if selected else C_CARD
        glow   = "box-shadow:0 0 14px " + _hex_to_rgba(color, 0.25) + ";" if selected else ""

        teu       = live.get("throughput_teu", port.throughput_today_teu)
        crane_pct = live.get("crane_utilization_pct", 80.0)
        berth_pct = live.get("berth_occupancy_pct", 75.0)

        crane_color = _C_NORMAL if crane_pct >= 80 else (_C_DEGRADED if crane_pct >= 60 else _C_DISRUPTED)
        berth_color = _C_DISRUPTED if berth_pct >= 90 else (_C_DEGRADED if berth_pct >= 75 else _C_NORMAL)

        bar_crane = _mini_bar_html_orig(crane_pct, crane_color, 90)
        bar_berth = _mini_bar_html_orig(berth_pct, berth_color, 90)

        return (
            "<div style='background:" + bg + ";border:" + border + ";"
            + "border-radius:10px;padding:10px 12px;cursor:pointer;" + glow + "'>"
            + "<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:4px'>"
            + "<span style='font-size:1.0rem;'>" + port.country_flag + "</span>"
            + _status_badge_html(port.operational_status)
            + "</div>"
            + "<div style='font-size:0.77rem;font-weight:700;color:" + C_TEXT + ";line-height:1.2;margin-bottom:2px'>"
            + port.port_name + "</div>"
            + "<div style='font-size:0.68rem;color:" + C_TEXT3 + ";margin-bottom:6px'>" + port.port_locode + "</div>"
            + "<div style='font-size:1.05rem;font-weight:700;color:" + C_TEXT + ";margin-bottom:1px'>"
            + _teu_fmt(teu) + "<span style='font-size:0.65rem;color:" + C_TEXT3 + ";font-weight:400'> TEU/day</span></div>"
            + "<div style='font-size:0.65rem;color:" + C_TEXT2 + ";margin-bottom:1px'>Cranes "
            + str(round(crane_pct, 0))[:-2] + "%</div>"
            + bar_crane
            + "<div style='font-size:0.65rem;color:" + C_TEXT2 + ";margin-top:3px;margin-bottom:1px'>Berths "
            + str(round(berth_pct, 0))[:-2] + "%</div>"
            + bar_berth
            + "</div>"
        )
    except Exception:
        return "<div style='padding:10px;color:#64748b;font-size:0.72rem'>" + port.port_name + "</div>"


def _render_port_grid(live_all: dict) -> Optional[str]:
    """Render 5x5 card grid. Returns the locode of the selected port."""
    try:
        section_header(
            "Port Status Grid",
            "All 25 tracked ports — live operational status  \u25cf  " + _utc_ts(),
        )

        if "pm_selected_locode" not in st.session_state:
            st.session_state["pm_selected_locode"] = PORT_OPERATIONAL_DATA[0].port_locode

        sel_label = st.selectbox(
            "Focus port",
            options=[p.port_locode for p in PORT_OPERATIONAL_DATA],
            format_func=lambda lc: PORT_OPERATIONAL_BY_LOCODE[lc].country_flag
                                    + "  " + PORT_OPERATIONAL_BY_LOCODE[lc].port_name
                                    + "  (" + lc + ")",
            index=[p.port_locode for p in PORT_OPERATIONAL_DATA].index(
                st.session_state["pm_selected_locode"]
            ),
            key="pm_port_selector",
            label_visibility="collapsed",
        )
        st.session_state["pm_selected_locode"] = sel_label
        selected_locode = sel_label

        cols_per_row = 5
        num_ports    = len(PORT_OPERATIONAL_DATA)
        num_rows     = math.ceil(num_ports / cols_per_row)

        for row in range(num_rows):
            cols = st.columns(cols_per_row)
            for ci in range(cols_per_row):
                idx = row * cols_per_row + ci
                if idx >= num_ports:
                    break
                port     = PORT_OPERATIONAL_DATA[idx]
                live     = live_all.get(port.port_locode, {})
                selected = port.port_locode == selected_locode
                with cols[ci]:
                    st.markdown(_port_card_html(port, live, selected), unsafe_allow_html=True)
                    if st.button(
                        port.port_locode,
                        key="btn_" + port.port_locode,
                        help="Focus " + port.port_name,
                        use_container_width=True,
                    ):
                        st.session_state["pm_selected_locode"] = port.port_locode
                        st.rerun()

        return selected_locode
    except Exception as exc:
        st.warning("Port grid error: " + str(exc))
        return PORT_OPERATIONAL_DATA[0].port_locode if PORT_OPERATIONAL_DATA else None


# ── Original Section 2: Live Port Detail Panel ────────────────────────────────

def _hourly_throughput_bars(port: PortOperationalStatus) -> go.Figure:
    try:
        baseline_hourly = port.throughput_baseline_daily / 24.0
        hours = list(range(24))
        values: list[int] = []

        peak_ranges: list[tuple[int, int]] = []
        for window in port.peak_hours:
            parts = window.split("-")
            if len(parts) == 2:
                try:
                    s = int(parts[0].split(":")[0])
                    e = int(parts[1].split(":")[0])
                    peak_ranges.append((s, e))
                except ValueError:
                    pass

        rng = _rand.Random(hash(port.port_locode + "hourly"))
        for h in hours:
            in_peak = any(s <= h < e for s, e in peak_ranges)
            factor  = rng.gauss(1.12 if in_peak else 0.88, 0.06)
            values.append(max(0, int(baseline_hourly * factor)))

        current_h = datetime.now(timezone.utc).hour
        colors = []
        for h in hours:
            if h == current_h:
                colors.append(_C_CYAN)
            elif any(s <= h < e for s, e in peak_ranges):
                colors.append(_C_BLUE)
            else:
                colors.append(_C_GRAY)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=[str(h).zfill(2) + ":00" for h in hours],
            y=values,
            marker={"color": colors, "line": {"color": "rgba(0,0,0,0)", "width": 0}},
            hovertemplate="%{x} — %{y:,} TEU<extra></extra>",
        ))
        fig.add_hline(
            y=baseline_hourly,
            line={"color": C_TEXT3, "dash": "dot", "width": 1.5},
            annotation_text="Baseline",
            annotation_font={"color": C_TEXT3, "size": 10},
            annotation_position="right",
        )
        layout = dark_layout(title="24-Hour Throughput (TEU/hour)", height=220)
        layout["template"]       = "plotly_dark"
        layout["xaxis"]["title"] = ""
        layout["yaxis"]["title"] = "TEU / hr"
        layout["margin"]         = {"l": 30, "r": 50, "t": 35, "b": 30}
        fig.update_layout(**layout)
        return fig
    except Exception:
        return go.Figure()


def _crane_productivity_chart(port: PortOperationalStatus, perf: Optional[PortPerformanceMetric]) -> go.Figure:
    try:
        top_peers = sorted(
            PORT_PERFORMANCE_BENCHMARKS,
            key=lambda m: -m.crane_productivity_moves_per_hour,
        )[:10]
        locodes = [m.port_locode for m in top_peers]
        values  = [m.crane_productivity_moves_per_hour for m in top_peers]

        colors = []
        for lc in locodes:
            if lc == port.port_locode:
                colors.append(_C_CYAN)
            elif values[locodes.index(lc)] >= 30:
                colors.append(_C_NORMAL)
            elif values[locodes.index(lc)] >= 27:
                colors.append(_C_BLUE)
            else:
                colors.append(_C_DISRUPTED)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=values,
            y=locodes,
            orientation="h",
            marker={"color": colors},
            text=[str(v) + " mph" for v in values],
            textposition="outside",
            textfont={"color": C_TEXT, "size": 10},
            hovertemplate="%{y} — %{x:.1f} moves/hr<extra></extra>",
        ))
        fig.add_vline(
            x=25,
            line={"color": _C_DEGRADED, "dash": "dash", "width": 1.5},
            annotation_text="25 (industry avg)",
            annotation_font={"color": _C_DEGRADED, "size": 9},
            annotation_position="top",
        )
        layout = dark_layout(title="Crane Productivity — Moves/Hour (Top 10)", height=260)
        layout["template"]            = "plotly_dark"
        layout["xaxis"]["title"]      = "Moves per crane-hour"
        layout["yaxis"]["autorange"]  = "reversed"
        layout["margin"]              = {"l": 60, "r": 70, "t": 35, "b": 25}
        fig.update_layout(**layout)
        return fig
    except Exception:
        return go.Figure()


def _render_detail_panel(locode: str, live_all: dict) -> None:
    try:
        section_header(
            "Live Port Detail",
            "Selected port — real-time operational snapshot",
        )

        port = PORT_OPERATIONAL_BY_LOCODE.get(locode)
        if port is None:
            st.warning("No data for locode: " + str(locode))
            return

        live_real = live_all.get(locode)
        ais_live  = live_real is not None and bool(live_real)
        live      = live_real or simulate_live_throughput(locode)
        perf      = PERF_BY_LOCODE.get(locode)

        color = _STATUS_COLOR.get(port.operational_status, _C_GRAY)

        with st.expander(
            port.country_flag + "  " + port.port_name + "  —  " + port.port_locode
            + "  " + _status_badge_html(port.operational_status),
            expanded=True,
            key="pm_detail_expander_" + locode,
        ):
            col_gauge, col_kpi = st.columns([1, 2])

            with col_gauge:
                try:
                    berth_pct = live.get("berth_occupancy_pct", 75.0)
                    occ_color = _C_DISRUPTED if berth_pct >= 90 else (
                        _C_DEGRADED if berth_pct >= 75 else _C_NORMAL
                    )
                    fig_gauge = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=berth_pct,
                        number={"suffix": "%", "font": {"color": C_TEXT, "size": 32}},
                        title={"text": "Berth Occupancy", "font": {"color": C_TEXT2, "size": 12}},
                        gauge={
                            "axis": {
                                "range": [0, 100],
                                "tickfont": {"color": C_TEXT3, "size": 9},
                                "tickwidth": 1,
                            },
                            "bar": {"color": occ_color, "thickness": 0.28},
                            "bgcolor": _C_BG_SURF,
                            "bordercolor": C_BORDER,
                            "borderwidth": 1,
                            "steps": [
                                {"range": [0,  60],  "color": "rgba(16,185,129,0.14)"},
                                {"range": [60, 80],  "color": "rgba(59,130,246,0.12)"},
                                {"range": [80, 92],  "color": "rgba(245,158,11,0.16)"},
                                {"range": [92, 100], "color": "rgba(239,68,68,0.20)"},
                            ],
                        },
                    ))
                    layout_g = dark_layout(height=220, showlegend=False)
                    layout_g["template"]      = "plotly_dark"
                    layout_g["paper_bgcolor"] = C_BG
                    layout_g["margin"]        = {"l": 15, "r": 15, "t": 30, "b": 10}
                    fig_gauge.update_layout(**layout_g)
                    st.plotly_chart(fig_gauge, use_container_width=True, key="pm_gauge_" + locode)
                except Exception:
                    st.metric("Berth Occupancy", str(round(live.get("berth_occupancy_pct", 75.0), 1)) + "%")

            with col_kpi:
                try:
                    teu_val   = live.get("throughput_teu", port.throughput_today_teu)
                    queue_val = live.get("vessel_queue", 0)
                    dwell_val = live.get("avg_dwell_time_days", port.avg_dwell_time_days)
                    crane_val = live.get("crane_utilization_pct", 80.0)
                    gate_val  = live.get("gate_wait_minutes", 45.0)
                    turn_val  = perf.ship_turn_hours if perf else "—"

                    kpi_items = [
                        {"label": "TEU Processed Today", "val": _teu_fmt(teu_val),           "color": _C_BLUE},
                        {"label": "Vessel Queue",         "val": str(queue_val),              "color": (_C_DISRUPTED if queue_val >= 5 else _C_NORMAL)},
                        {"label": "Avg Dwell (days)",     "val": str(round(dwell_val, 1)),    "color": (_C_DEGRADED if dwell_val > 3.5 else _C_NORMAL)},
                        {"label": "Crane Util %",         "val": str(round(crane_val, 1)),    "color": _C_DISRUPTED if crane_val > 95 else _C_NORMAL},
                        {"label": "Gate Wait (min)",      "val": str(int(gate_val)),          "color": (_C_DISRUPTED if gate_val > 120 else (_C_DEGRADED if gate_val > 60 else _C_NORMAL))},
                        {"label": "Ship Turn (hrs)",      "val": str(turn_val),               "color": C_TEXT},
                    ]

                    kpi_cols = st.columns(3)
                    for idx, item in enumerate(kpi_items):
                        with kpi_cols[idx % 3]:
                            st.markdown(
                                "<div style='background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
                                + "border-top:3px solid " + item["color"] + ";"
                                + "border-radius:8px;padding:12px 14px;text-align:center;margin-bottom:8px'>"
                                + "<div style='font-size:0.68rem;color:" + C_TEXT2 + ";text-transform:uppercase;"
                                + "letter-spacing:0.06em;margin-bottom:4px'>" + item["label"] + "</div>"
                                + "<div style='font-size:1.35rem;font-weight:700;color:" + item["color"] + "'>"
                                + item["val"] + "</div>"
                                + "</div>",
                                unsafe_allow_html=True,
                            )
                except Exception:
                    pass

                # Infrastructure badges
                try:
                    infra_parts = []
                    if port.rail_connection:
                        infra_parts.append(
                            "<span style='background:rgba(59,130,246,0.15);color:" + _C_BLUE + ";"
                            + "border:1px solid rgba(59,130,246,0.3);padding:2px 8px;border-radius:999px;"
                            + "font-size:0.68rem;font-weight:600;margin-right:4px'>Rail</span>"
                        )
                    if port.deepwater_berths_count > 0:
                        infra_parts.append(
                            "<span style='background:rgba(6,182,212,0.15);color:" + _C_CYAN + ";"
                            + "border:1px solid rgba(6,182,212,0.3);padding:2px 8px;border-radius:999px;"
                            + "font-size:0.68rem;font-weight:600;margin-right:4px'>Deepwater x"
                            + str(port.deepwater_berths_count) + "</span>"
                        )
                    infra_parts.append(
                        "<span style='background:rgba(139,92,246,0.15);color:" + _C_PURPLE + ";"
                        + "border:1px solid rgba(139,92,246,0.3);padding:2px 8px;border-radius:999px;"
                        + "font-size:0.68rem;font-weight:600'>Max " + str(port.max_vessel_teu) + " TEU</span>"
                    )
                    st.markdown(
                        "<div style='margin-top:4px'>" + "".join(infra_parts) + "</div>",
                        unsafe_allow_html=True,
                    )
                except Exception:
                    pass

            # AIS notice
            if not ais_live:
                st.markdown(
                    "<div style='background:rgba(71,85,105,0.12);border:1px solid rgba(71,85,105,0.30);"
                    + "border-radius:8px;padding:8px 14px;margin-top:4px;"
                    + "font-size:0.78rem;color:" + C_TEXT2 + ";display:flex;align-items:center;gap:8px'>"
                    + "<span style='font-size:1.05rem'>&#128674;</span>"
                    + "<span>AIS data temporarily unavailable — operational metrics are simulated from baseline.</span>"
                    + "</div>",
                    unsafe_allow_html=True,
                )

            # Incident banner
            if port.incident_description:
                try:
                    inc_color = _STATUS_COLOR.get(port.operational_status, _C_GRAY)
                    st.markdown(
                        "<div style='background:" + _hex_to_rgba(inc_color, 0.1) + ";"
                        + "border:1px solid " + _hex_to_rgba(inc_color, 0.35) + ";"
                        + "border-radius:8px;padding:10px 14px;margin-top:6px;"
                        + "font-size:0.8rem;color:" + C_TEXT + ";line-height:1.5'>"
                        + "<span style='font-weight:700;color:" + inc_color + "'>Latest Incident</span>"
                        + "  " + port.last_incident_date + " — "
                        + port.incident_description
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                except Exception:
                    pass

            st.markdown("<div style='margin:10px 0'></div>", unsafe_allow_html=True)

            col_hourly, col_crane = st.columns([3, 2])
            with col_hourly:
                try:
                    st.plotly_chart(_hourly_throughput_bars(port), use_container_width=True, key="pm_hourly_" + locode)
                except Exception:
                    pass
            with col_crane:
                try:
                    st.plotly_chart(_crane_productivity_chart(port, perf), use_container_width=True, key="pm_crane_" + locode)
                except Exception:
                    pass

            # Export
            try:
                st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
                _col_exp, _col_pad = st.columns([1, 4])
                with _col_exp:
                    _report_rows = {
                        "port_name":               port.port_name,
                        "port_locode":             port.port_locode,
                        "operational_status":      port.operational_status,
                        "throughput_teu_today":    live.get("throughput_teu", port.throughput_today_teu),
                        "berth_occupancy_pct":     live.get("berth_occupancy_pct", 75.0),
                        "crane_utilization_pct":   live.get("crane_utilization_pct", 80.0),
                        "vessel_queue":            live.get("vessel_queue", 0),
                        "avg_dwell_time_days":     live.get("avg_dwell_time_days", port.avg_dwell_time_days),
                        "gate_wait_minutes":       live.get("gate_wait_minutes", 45.0),
                        "ship_turn_hours":         perf.ship_turn_hours if perf else "",
                        "crane_productivity_mph":  perf.crane_productivity_moves_per_hour if perf else "",
                        "annual_teu_volume_m":     perf.annual_teu_volume_m if perf else "",
                        "rail_connection":         port.rail_connection,
                        "deepwater_berths":        port.deepwater_berths_count,
                        "max_vessel_teu":          port.max_vessel_teu,
                        "ais_data_live":           ais_live,
                        "report_generated_utc":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    }
                    _report_df = pd.DataFrame([_report_rows])
                    _csv_bytes = _report_df.to_csv(index=False).encode("utf-8")
                    _fname     = "port_report_" + locode + "_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M") + ".csv"
                    st.download_button(
                        label="&#128229; Export port report",
                        data=_csv_bytes,
                        file_name=_fname,
                        mime="text/csv",
                        key="pm_export_" + locode,
                        use_container_width=True,
                    )
            except Exception:
                pass
    except Exception as exc:
        st.warning("Detail panel error: " + str(exc))


# ── Original Section 3: World Port Benchmark Table ────────────────────────────

def _quartile_color(value: float, values: list, higher_is_better: bool) -> str:
    try:
        sorted_vals = sorted(values)
        n           = len(sorted_vals)
        rank        = sorted_vals.index(min(sorted_vals, key=lambda v: abs(v - value)))
        quartile    = rank / max(n - 1, 1)

        if higher_is_better:
            if quartile >= 0.75: return _C_NORMAL
            if quartile >= 0.25: return _C_BLUE
            return _C_DISRUPTED
        else:
            if quartile <= 0.25: return _C_NORMAL
            if quartile <= 0.75: return _C_BLUE
            return _C_DISRUPTED
    except Exception:
        return _C_GRAY


def _render_benchmark_table(live_all: dict) -> None:
    try:
        section_header(
            "World Port Benchmark",
            "All 25 ports ranked by crane productivity — color coded by quartile",
        )

        crane_vals = [m.crane_productivity_moves_per_hour for m in PORT_PERFORMANCE_BENCHMARKS]
        turn_vals  = [m.ship_turn_hours for m in PORT_PERFORMANCE_BENCHMARKS]
        dwell_vals = [m.rail_dwell_days for m in PORT_PERFORMANCE_BENCHMARKS]
        teu_vals   = [m.annual_teu_volume_m for m in PORT_PERFORMANCE_BENCHMARKS]

        sorted_metrics = sorted(
            PORT_PERFORMANCE_BENCHMARKS,
            key=lambda m: -m.crane_productivity_moves_per_hour,
        )

        rows = []
        for rank, m in enumerate(sorted_metrics, 1):
            port   = PORT_OPERATIONAL_BY_LOCODE.get(m.port_locode)
            name   = (port.country_flag + "  " + port.port_name) if port else m.port_locode
            status = port.operational_status if port else "NORMAL"
            live   = live_all.get(m.port_locode, {})
            gate_w = live.get("gate_wait_minutes", m.gate_truck_wait_minutes)
            rows.append({
                "Rank":      rank,
                "Port":      name,
                "LOCODE":    m.port_locode,
                "Status":    status,
                "Crane mph": round(m.crane_productivity_moves_per_hour, 1),
                "Turn h":    round(m.ship_turn_hours, 0),
                "Dwell d":   round(m.rail_dwell_days, 1),
                "Gate min":  round(gate_w, 0),
                "TEU M/yr":  round(m.annual_teu_volume_m, 1),
            })

        df = pd.DataFrame(rows)
        st.dataframe(
            df[["Rank", "Port", "LOCODE", "Status", "Crane mph", "Turn h", "Dwell d", "Gate min", "TEU M/yr"]],
            use_container_width=True,
            height=520,
            hide_index=True,
        )

        st.markdown(
            "<div style='font-size:0.73rem;color:" + C_TEXT3 + ";margin-top:4px'>"
            + "<span style='color:" + _C_NORMAL + ";font-weight:600'>Green</span> = top quartile  |  "
            + "<span style='color:" + _C_BLUE + ";font-weight:600'>Blue</span> = middle  |  "
            + "<span style='color:" + _C_DISRUPTED + ";font-weight:600'>Red</span> = bottom quartile"
            + "</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning("Benchmark table error: " + str(exc))


# ── Original Section 4: Port Capacity by Region ───────────────────────────────

_REGION_CAPACITY = [
    {
        "region": "Asia East",
        "ports": ["CNSHA", "CNNBO", "CNSZN", "CNTAO", "CNTXG", "HKHKG", "KRPUS", "TWKHH", "JPYOK"],
        "current_cap_m": 248.0, "utilisation": 0.79, "proj_2030_m": 285.0, "proj_2040_m": 320.0,
    },
    {
        "region": "Southeast Asia",
        "ports": ["SGSIN", "MYPKG", "MYTPP"],
        "current_cap_m": 77.0, "utilisation": 0.75, "proj_2030_m": 108.0, "proj_2040_m": 142.0,
    },
    {
        "region": "Europe",
        "ports": ["NLRTM", "BEANR", "DEHAM", "GRPIR", "GBFXT"],
        "current_cap_m": 55.0, "utilisation": 0.72, "proj_2030_m": 62.0, "proj_2040_m": 68.0,
    },
    {
        "region": "North America",
        "ports": ["USLAX", "USLGB", "USNYC", "USSAV"],
        "current_cap_m": 48.0, "utilisation": 0.80, "proj_2030_m": 56.0, "proj_2040_m": 65.0,
    },
    {
        "region": "Middle East",
        "ports": ["AEJEA"],
        "current_cap_m": 22.0, "utilisation": 0.76, "proj_2030_m": 26.0, "proj_2040_m": 30.0,
    },
    {
        "region": "Africa & Other",
        "ports": ["MATNM", "LKCMB", "BRSAO"],
        "current_cap_m": 32.0, "utilisation": 0.71, "proj_2030_m": 42.0, "proj_2040_m": 55.0,
    },
]

_YEARS = [2020, 2023, 2025, 2027, 2030, 2035, 2040]


def _region_series(rc: dict) -> list:
    c0  = rc["current_cap_m"] * 0.88
    c25 = rc["current_cap_m"]
    c30 = rc["proj_2030_m"]
    c40 = rc["proj_2040_m"]

    def _lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    result = []
    for yr in _YEARS:
        if yr <= 2020:
            result.append(round(c0, 1))
        elif yr <= 2025:
            result.append(round(_lerp(c0, c25, (yr - 2020) / 5.0), 1))
        elif yr <= 2030:
            result.append(round(_lerp(c25, c30, (yr - 2025) / 5.0), 1))
        else:
            result.append(round(_lerp(c30, c40, (yr - 2030) / 10.0), 1))
    return result


def _render_capacity_chart() -> None:
    try:
        section_header(
            "Port Infrastructure Capacity by Region",
            "Current capacity vs utilisation — Tuas (Singapore) mega-port expansion adds 65M TEU by 2040",
        )

        region_colors = {
            "Asia East":       _C_BLUE,
            "Southeast Asia":  _C_CYAN,
            "Europe":          _C_PURPLE,
            "North America":   _C_NORMAL,
            "Middle East":     _C_DEGRADED,
            "Africa & Other":  _C_ORANGE,
        }

        fig = go.Figure()
        for rc in _REGION_CAPACITY:
            series = _region_series(rc)
            color  = region_colors.get(rc["region"], _C_GRAY)
            fig.add_trace(go.Scatter(
                x=_YEARS, y=series,
                name=rc["region"],
                mode="lines",
                stackgroup="cap",
                line={"width": 1.5, "color": color},
                fillcolor=_hex_to_rgba(color, 0.35),
                hovertemplate=rc["region"] + " %{x}: %{y:.0f}M TEU<extra></extra>",
            ))

        fig.add_annotation(
            x=2040, y=142,
            text="Tuas full build-out<br>+65M TEU",
            showarrow=True, arrowhead=2,
            arrowcolor=_C_CYAN, arrowwidth=1.5,
            font={"color": _C_CYAN, "size": 10},
            bgcolor=_hex_to_rgba(_C_CYAN, 0.1), bordercolor=_C_CYAN,
        )
        fig.add_annotation(
            x=2035, y=57,
            text="LA/LB zero-emission<br>terminal expansion",
            showarrow=True, arrowhead=2,
            arrowcolor=_C_NORMAL, arrowwidth=1.5,
            font={"color": _C_NORMAL, "size": 10},
            bgcolor=_hex_to_rgba(_C_NORMAL, 0.1), bordercolor=_C_NORMAL,
        )

        layout = dark_layout(title="Global Container Port Capacity (M TEU) — 2020-2040 Projection", height=380)
        layout["template"]               = "plotly_dark"
        layout["xaxis"]["title"]         = "Year"
        layout["yaxis"]["title"]         = "Capacity (M TEU)"
        layout["legend"]["orientation"]  = "h"
        layout["legend"]["y"]            = -0.15
        layout["legend"]["x"]            = 0
        fig.update_layout(**layout)

        st.plotly_chart(fig, use_container_width=True, key="pm_capacity_chart")

        util_rows = []
        for rc in _REGION_CAPACITY:
            util_rows.append({
                "Region":           rc["region"],
                "Capacity (M TEU)": rc["current_cap_m"],
                "Utilisation":      str(round(rc["utilisation"] * 100, 0))[:-2] + "%",
                "2030 Proj":        rc["proj_2030_m"],
                "2040 Proj":        rc["proj_2040_m"],
            })
        st.dataframe(pd.DataFrame(util_rows), use_container_width=True, hide_index=True, height=240)
    except Exception as exc:
        st.warning("Capacity chart error: " + str(exc))


# ── Original Section 5: Anomaly Feed ─────────────────────────────────────────

_STATIC_ANOMALY_CONTEXT = [
    "CNSHA — Yangshan T4 automated terminal running at 99.2% crane utilisation; no buffer for surge",
    "USLAX + USLGB — Combined San Pedro Bay vessel queue: 14 ships at anchor; POLA anchor zone near capacity",
    "AEJEA — Throughput 18% above prior-year baseline; Red Sea diversions inflating transshipment volumes",
    "NLRTM — Maasvlakte II Phase 2 expansion ETA delayed to Q3 2026 per PoR Q1 update",
    "SGSIN — Tuas Phase 2 berths 9-16 commissioning on schedule; 8M TEU incremental capacity expected 2027",
    "BRSAO — Customs IT migration ongoing; expect intermittent processing slowdowns through April 2026",
    "USNYC — Bayonne Bridge air draft 65.5 m; ULCV 24000 TEU class now fully accessible to all terminals",
    "KRPUS — New Alliance restructuring increasing call frequency +12% at Busan New Port",
    "MATNM — Phase III expansion (Tanger Med 2) adds 5M TEU; fully operational since 2024",
    "DEHAM — Elbe fairway depth 13.5 m limits ULCV access; dredging plan pending federal approval",
]


def _anomaly_severity(text: str) -> tuple:
    upper = text.upper()
    if "DISRUPTED" in upper or "CRITICAL" in upper or "STRIKE" in upper:
        return ("!", _C_DISRUPTED, _hex_to_rgba(_C_DISRUPTED, 0.35))
    if "DEGRADED" in upper or "DEVIATION" in upper or "CONGESTION" in upper or "DELAY" in upper:
        return ("!", _C_DEGRADED, _hex_to_rgba(_C_DEGRADED, 0.30))
    return ("i", _C_BLUE, _hex_to_rgba(_C_BLUE, 0.25))


def _anomaly_card_html(text: str, idx: int) -> str:
    try:
        icon, color, border = _anomaly_severity(text)
        bg    = _hex_to_rgba(color, 0.07)
        delay = str(idx * 35) + "ms"
        return (
            "<div style='background:" + bg + ";border:1px solid " + border + ";"
            + "border-radius:8px;padding:9px 14px;margin-bottom:6px;"
            + "animation:slide-in-up 0.35s ease-out both;animation-delay:" + delay + ";'>"
            + "<div style='display:flex;align-items:flex-start;gap:10px'>"
            + "<div style='width:20px;height:20px;border-radius:50%;background:" + _hex_to_rgba(color, 0.2) + ";"
            + "border:1px solid " + border + ";display:flex;align-items:center;justify-content:center;"
            + "flex-shrink:0;font-size:0.72rem;font-weight:700;color:" + color + "'>" + icon + "</div>"
            + "<div style='font-size:0.80rem;color:" + C_TEXT + ";line-height:1.5'>" + text + "</div>"
            + "</div></div>"
        )
    except Exception:
        return "<div style='padding:8px;font-size:0.78rem;color:" + C_TEXT2 + "'>" + text + "</div>"


def _render_anomaly_feed(live_all: dict) -> None:
    try:
        section_header(
            "Anomaly Feed",
            "Real-time port warnings across all 25 ports — refreshes with page",
        )

        live_flags: list = []
        for port in PORT_OPERATIONAL_DATA:
            stats = live_all.get(port.port_locode, {})
            try:
                flags = detect_port_anomalies(port.port_locode, stats)
                live_flags.extend(flags)
            except Exception:
                pass

        all_items = live_flags + _STATIC_ANOMALY_CONTEXT

        if not all_items:
            st.success("No anomalies detected across 25 monitored ports.")
            return

        half   = math.ceil(len(all_items) / 2)
        col_a, col_b = st.columns(2)

        with col_a:
            for idx, item in enumerate(all_items[:half]):
                st.markdown(_anomaly_card_html(item, idx), unsafe_allow_html=True)

        with col_b:
            for idx, item in enumerate(all_items[half:]):
                st.markdown(_anomaly_card_html(item, idx + half), unsafe_allow_html=True)

        st.markdown(
            "<div style='font-size:0.72rem;color:" + C_TEXT3 + ";margin-top:8px;text-align:right'>"
            + str(len(live_flags)) + " live anomaly flags  |  "
            + str(len(_STATIC_ANOMALY_CONTEXT)) + " contextual alerts  |  Updated " + _utc_ts()
            + "</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning("Anomaly feed error: " + str(exc))


# ── Main render entry point ───────────────────────────────────────────────────

def render(port_results: Any = None, ais_data: Any = None, freight_data: Any = None) -> None:
    """Render the Port Operations Monitor tab.

    Parameters
    ----------
    port_results:
        Optional pre-computed port data from the main app pipeline.
        Not required — all data sourced from processing.port_monitor.
    ais_data:
        Optional AIS vessel position data. Reserved for future vessel-queue
        enrichment; not currently used in this tab.
    freight_data:
        Optional freight rate data dict (passed through from main pipeline).
        Not currently used in port monitor sections but accepted for API
        consistency with other tabs.
    """
    logger.debug("tab_port_monitor.render() called")

    # Pull all live stats once — reused across all sections
    try:
        live_all: dict = get_all_live_stats()
    except Exception:
        live_all = {}

    # ── A. Command Dashboard ──────────────────────────────────────────────────
    try:
        section_header(
            "Port Operations Command Center",
            "Global port intelligence — 25 ports monitored · " + _utc_ts(),
        )
        _render_command_dashboard(live_all)
    except Exception as exc:
        st.warning("Command dashboard error: " + str(exc))
    _hr()

    # ── B. Enhanced Port Status Grid (filter chips + trend arrows) ────────────
    try:
        _render_enhanced_port_grid_v2(live_all)
    except Exception as exc:
        st.warning("Enhanced port grid error: " + str(exc))
    _hr()

    # ── C. Global Port Map ────────────────────────────────────────────────────
    try:
        _render_global_port_map(live_all)
    except Exception as exc:
        st.warning("Global map error: " + str(exc))
    _hr()

    # ── D. Throughput Trends — 7-day sparklines ───────────────────────────────
    try:
        _render_throughput_trends_v2(live_all)
    except Exception as exc:
        st.warning("Throughput trends error: " + str(exc))
    _hr()

    # ── E. Arrival Forecast ───────────────────────────────────────────────────
    try:
        _render_arrival_forecast(live_all)
    except Exception as exc:
        st.warning("Arrival forecast error: " + str(exc))
    _hr()

    # ── F. Berth Heatmap (port × hour-of-day) ────────────────────────────────
    try:
        _render_berth_heatmap_hourly(live_all)
    except Exception as exc:
        st.warning("Berth heatmap error: " + str(exc))
    _hr()

    # ── G. Efficiency Rankings ────────────────────────────────────────────────
    try:
        _render_efficiency_rankings(live_all)
    except Exception as exc:
        st.warning("Efficiency rankings error: " + str(exc))
    _hr()

    # ── H. Alert Log (severity cards) ────────────────────────────────────────
    try:
        _render_alert_log_v2()
    except Exception as exc:
        st.warning("Alert log error: " + str(exc))
    _hr()

    # ── 1. Port Status Grid (original selector + card grid) ───────────────────
    try:
        selected_locode = _render_port_grid(live_all)
    except Exception as exc:
        st.warning("Port grid error: " + str(exc))
        selected_locode = PORT_OPERATIONAL_DATA[0].port_locode if PORT_OPERATIONAL_DATA else None
    _hr()

    # ── 2. Live Port Detail Panel ─────────────────────────────────────────────
    try:
        if selected_locode:
            _render_detail_panel(selected_locode, live_all)
    except Exception as exc:
        st.warning("Detail panel error: " + str(exc))
    _hr()

    # ── 3. World Port Benchmark Table ─────────────────────────────────────────
    try:
        _render_benchmark_table(live_all)
    except Exception as exc:
        st.warning("Benchmark table error: " + str(exc))
    _hr()

    # ── 4. Port Capacity by Region ────────────────────────────────────────────
    try:
        _render_capacity_chart()
    except Exception as exc:
        st.warning("Capacity chart error: " + str(exc))
    _hr()

    # ── 5. Anomaly Feed ───────────────────────────────────────────────────────
    try:
        _render_anomaly_feed(live_all)
    except Exception as exc:
        st.warning("Anomaly feed error: " + str(exc))
