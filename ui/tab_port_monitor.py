"""Port Operations Monitor tab — live-feeling port dashboard for all 25 tracked ports.

Sections
--------
1. Port Status Grid        — 5x5 card grid; click to focus detail panel
2. Live Port Detail Panel  — gauge, 24-h throughput bars, crane productivity
3. World Port Benchmark    — ranked table (crane productivity, turn time, dwell, TEU)
4. Port Capacity by Region — stacked area chart; Tuas expansion projection
5. Anomaly Feed            — real-time-style list of current port warnings
"""
from __future__ import annotations

import math
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

_STATUS_COLOR = {
    "NORMAL":    _C_NORMAL,
    "DEGRADED":  _C_DEGRADED,
    "DISRUPTED": _C_DISRUPTED,
}

# Region grouping for capacity area chart
_REGION_COLORS = {
    "Asia East":          _C_BLUE,
    "Southeast Asia":     _C_CYAN,
    "Europe":             _C_PURPLE,
    "North America West": _C_NORMAL,
    "North America East": "#34d399",
    "Middle East":        _C_DEGRADED,
    "Africa":             "#f97316",
    "South Asia":         "#a78bfa",
    "South America":      "#fb923c",
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
    """Inline progress bar as HTML, no f-string backslash issues."""
    filled = max(0.0, min(100.0, pct))
    bar_fill = str(int(filled)) + "%"
    pct_label = str(round(filled, 0))[:-2] if str(round(filled, 0)).endswith(".0") else str(round(filled, 1))
    return (
        "<div style='width:" + str(width) + "px;background:rgba(255,255,255,0.08);"
        + "border-radius:4px;height:5px;overflow:hidden;margin:2px 0'>"
        + "<div style='width:" + bar_fill + ";background:" + color + ";height:100%;'></div>"
        + "</div>"
    )


def _status_badge_html(status: str) -> str:
    color = _STATUS_COLOR.get(status, _C_GRAY)
    bg    = _hex_to_rgba(color, 0.18)
    border = _hex_to_rgba(color, 0.35)
    return (
        "<span style='display:inline-block;padding:1px 8px;border-radius:999px;"
        + "font-size:0.66rem;font-weight:700;letter-spacing:0.05em;"
        + "background:" + bg + ";color:" + color + ";border:1px solid " + border + ";'>"
        + status + "</span>"
    )


def _teu_fmt(teu: int) -> str:
    """Format TEU as e.g. '134.8k'."""
    if teu >= 1_000_000:
        return str(round(teu / 1_000_000, 2)) + "M"
    if teu >= 1000:
        return str(round(teu / 1000, 1)) + "k"
    return str(teu)


# ── Section 1: Port Status Grid ───────────────────────────────────────────────

def _port_card_html(
    port: PortOperationalStatus,
    live: dict,
    selected: bool,
) -> str:
    color   = _STATUS_COLOR.get(port.operational_status, _C_GRAY)
    border  = "2px solid " + color if selected else "1px solid " + _hex_to_rgba(color, 0.35)
    bg      = _hex_to_rgba(color, 0.07) if selected else C_CARD
    glow    = "box-shadow:0 0 14px " + _hex_to_rgba(color, 0.25) + ";" if selected else ""

    teu         = live.get("throughput_teu", port.throughput_today_teu)
    crane_pct   = live.get("crane_utilization_pct", 80.0)
    berth_pct   = live.get("berth_occupancy_pct", 75.0)

    crane_color = _C_NORMAL if crane_pct >= 80 else (_C_DEGRADED if crane_pct >= 60 else _C_DISRUPTED)
    berth_color = _C_DISRUPTED if berth_pct >= 90 else (_C_DEGRADED if berth_pct >= 75 else _C_NORMAL)

    bar_crane = _mini_bar_html(crane_pct, crane_color, 90)
    bar_berth = _mini_bar_html(berth_pct, berth_color, 90)

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
        + "<div style='font-size:0.65rem;color:" + C_TEXT2 + ";margin-bottom:1px'>Cranes " + str(round(crane_pct, 0))[:-2] + "%</div>"
        + bar_crane
        + "<div style='font-size:0.65rem;color:" + C_TEXT2 + ";margin-top:3px;margin-bottom:1px'>Berths " + str(round(berth_pct, 0))[:-2] + "%</div>"
        + bar_berth
        + "</div>"
    )


def _render_port_grid(live_all: dict) -> Optional[str]:
    """Render 5x5 card grid. Returns the locode of the selected port (via selectbox)."""
    section_header(
        "Port Status Grid",
        "All 25 tracked ports — live operational status  \u25cf  " + _utc_ts(),
    )

    # Port selector above the grid (drives detail panel)
    port_names    = [p.port_name + "  (" + p.port_locode + ")" for p in PORT_OPERATIONAL_DATA]
    default_idx   = 0

    # Session state persistence
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

    # Render 5 columns of cards
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
                # Invisible button to allow click-to-select (below card)
                if st.button(
                    port.port_locode,
                    key="btn_" + port.port_locode,
                    help="Focus " + port.port_name,
                    use_container_width=True,
                ):
                    st.session_state["pm_selected_locode"] = port.port_locode
                    st.rerun()

    return selected_locode


# ── Section 2: Live Port Detail Panel ────────────────────────────────────────

def _hourly_throughput_bars(port: PortOperationalStatus) -> go.Figure:
    """24-h bar chart showing synthetic hourly throughput around the daily baseline."""
    baseline_hourly = port.throughput_baseline_daily / 24.0
    hours           = list(range(24))
    values          = []

    # Determine peak-hour integer ranges
    peak_ranges: list = []
    for window in port.peak_hours:
        parts = window.split("-")
        if len(parts) == 2:
            try:
                s = int(parts[0].split(":")[0])
                e = int(parts[1].split(":")[0])
                peak_ranges.append((s, e))
            except ValueError:
                pass

    import random as _rand
    rng = _rand.Random(hash(port.port_locode + "hourly"))
    for h in hours:
        in_peak = any(s <= h < e for s, e in peak_ranges)
        factor  = rng.gauss(1.12 if in_peak else 0.88, 0.06)
        values.append(max(0, int(baseline_hourly * factor)))

    colors = []
    current_h = datetime.now(timezone.utc).hour
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
    layout["template"] = "plotly_dark"
    layout["xaxis"]["title"] = ""
    layout["yaxis"]["title"] = "TEU / hr"
    layout["margin"] = {"l": 30, "r": 50, "t": 35, "b": 30}
    fig.update_layout(**layout)
    return fig


def _crane_productivity_chart(port: PortOperationalStatus, perf: Optional[PortPerformanceMetric]) -> go.Figure:
    """Horizontal bar comparing this port's crane productivity vs peer benchmarks."""
    # Build top-10 list of benchmarks for context
    top_peers = sorted(PORT_PERFORMANCE_BENCHMARKS, key=lambda m: -m.crane_productivity_moves_per_hour)[:10]
    locodes   = [m.port_locode for m in top_peers]
    values    = [m.crane_productivity_moves_per_hour for m in top_peers]
    port_locodes_in_top = [lc for lc in locodes if lc == port.port_locode]

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
    # Industry benchmark reference line
    fig.add_vline(
        x=25,
        line={"color": _C_DEGRADED, "dash": "dash", "width": 1.5},
        annotation_text="25 (industry avg)",
        annotation_font={"color": _C_DEGRADED, "size": 9},
        annotation_position="top",
    )
    layout = dark_layout(title="Crane Productivity — Moves/Hour (Top 10)", height=260)
    layout["template"] = "plotly_dark"
    layout["xaxis"]["title"] = "Moves per crane-hour"
    layout["yaxis"]["autorange"] = "reversed"
    layout["margin"] = {"l": 60, "r": 70, "t": 35, "b": 25}
    fig.update_layout(**layout)
    return fig


def _render_detail_panel(locode: str, live_all: dict) -> None:
    section_header(
        "Live Port Detail",
        "Selected port — real-time operational snapshot",
    )

    port = PORT_OPERATIONAL_BY_LOCODE.get(locode)
    if port is None:
        st.warning("No data for locode: " + locode)
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
        # ── Row 1: Gauge + KPI strip ──────────────────────────────────────
        col_gauge, col_kpi = st.columns([1, 2])

        with col_gauge:
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

        with col_kpi:
            teu_val   = live.get("throughput_teu", port.throughput_today_teu)
            queue_val = live.get("vessel_queue", 0)
            dwell_val = live.get("avg_dwell_time_days", port.avg_dwell_time_days)
            crane_val = live.get("crane_utilization_pct", 80.0)
            gate_val  = live.get("gate_wait_minutes", 45.0)

            turn_val  = perf.ship_turn_hours if perf else "—"

            kpi_items = [
                {"label": "TEU Processed Today", "val": _teu_fmt(teu_val), "color": _C_BLUE},
                {"label": "Vessel Queue",         "val": str(queue_val),    "color": (_C_DISRUPTED if queue_val >= 5 else _C_NORMAL)},
                {"label": "Avg Dwell (days)",     "val": str(round(dwell_val, 1)), "color": (_C_DEGRADED if dwell_val > 3.5 else _C_NORMAL)},
                {"label": "Crane Util %",         "val": str(round(crane_val, 1)), "color": crane_val > 95 and _C_DISRUPTED or _C_NORMAL},
                {"label": "Gate Wait (min)",      "val": str(int(gate_val)),  "color": (_C_DISRUPTED if gate_val > 120 else (_C_DEGRADED if gate_val > 60 else _C_NORMAL))},
                {"label": "Ship Turn (hrs)",      "val": str(turn_val),       "color": C_TEXT},
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

            # Infrastructure badges
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

        # AIS unavailability notice
        if not ais_live:
            st.markdown(
                "<div style='background:rgba(71,85,105,0.12);border:1px solid rgba(71,85,105,0.30);"
                + "border-radius:8px;padding:8px 14px;margin-top:4px;"
                + "font-size:0.78rem;color:" + C_TEXT2 + ";display:flex;align-items:center;gap:8px'>"
                + "<span style='font-size:1.05rem'>&#128674;</span>"
                + "<span>AIS data temporarily unavailable \u2014 operational metrics are simulated from baseline.</span>"
                + "</div>",
                unsafe_allow_html=True,
            )

        # Incident banner
        if port.incident_description:
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

        st.markdown("<div style='margin:10px 0'></div>", unsafe_allow_html=True)

        # ── Row 2: Hourly bars | Crane productivity ────────────────────────
        col_hourly, col_crane = st.columns([3, 2])
        with col_hourly:
            st.plotly_chart(_hourly_throughput_bars(port), use_container_width=True, key="pm_hourly_" + locode)
        with col_crane:
            st.plotly_chart(_crane_productivity_chart(port, perf), use_container_width=True, key="pm_crane_" + locode)

        # ── Export port report ─────────────────────────────────────────────
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
            _report_df  = pd.DataFrame([_report_rows])
            _csv_bytes   = _report_df.to_csv(index=False).encode("utf-8")
            _fname        = "port_report_" + locode + "_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M") + ".csv"
            st.download_button(
                label="&#128229; Export port report",
                data=_csv_bytes,
                file_name=_fname,
                mime="text/csv",
                key="pm_export_" + locode,
                use_container_width=True,
            )


# ── Section 3: World Port Benchmark Table ─────────────────────────────────────

def _quartile_color(value: float, values: list, higher_is_better: bool) -> str:
    """Return a hex color based on quartile rank."""
    sorted_vals = sorted(values)
    n           = len(sorted_vals)
    rank        = sorted_vals.index(
        min(sorted_vals, key=lambda v: abs(v - value))
    )
    quartile = rank / max(n - 1, 1)   # 0 = lowest, 1 = highest

    if higher_is_better:
        if quartile >= 0.75:
            return _C_NORMAL
        if quartile >= 0.25:
            return _C_BLUE
        return _C_DISRUPTED
    else:
        if quartile <= 0.25:
            return _C_NORMAL
        if quartile <= 0.75:
            return _C_BLUE
        return _C_DISRUPTED


def _render_benchmark_table(live_all: dict) -> None:
    section_header(
        "World Port Benchmark",
        "All 25 ports ranked by crane productivity — color coded by quartile",
    )

    rows = []
    crane_vals = [m.crane_productivity_moves_per_hour for m in PORT_PERFORMANCE_BENCHMARKS]
    turn_vals  = [m.ship_turn_hours for m in PORT_PERFORMANCE_BENCHMARKS]
    dwell_vals = [m.rail_dwell_days for m in PORT_PERFORMANCE_BENCHMARKS]
    teu_vals   = [m.annual_teu_volume_m for m in PORT_PERFORMANCE_BENCHMARKS]

    sorted_metrics = sorted(
        PORT_PERFORMANCE_BENCHMARKS,
        key=lambda m: -m.crane_productivity_moves_per_hour,
    )

    for rank, m in enumerate(sorted_metrics, 1):
        port = PORT_OPERATIONAL_BY_LOCODE.get(m.port_locode)
        name = (port.country_flag + "  " + port.port_name) if port else m.port_locode
        status = port.operational_status if port else "NORMAL"
        live   = live_all.get(m.port_locode, {})
        gate_w = live.get("gate_wait_minutes", m.gate_truck_wait_minutes)

        rows.append({
            "Rank":       rank,
            "Port":       name,
            "LOCODE":     m.port_locode,
            "Status":     status,
            "Crane mph":  round(m.crane_productivity_moves_per_hour, 1),
            "Turn h":     round(m.ship_turn_hours, 0),
            "Dwell d":    round(m.rail_dwell_days, 1),
            "Gate min":   round(gate_w, 0),
            "TEU M/yr":   round(m.annual_teu_volume_m, 1),
        })

    df = pd.DataFrame(rows)

    # Style function
    def _style_row(row: pd.Series) -> list:
        styles = [""] * len(row)
        col_names = list(row.index)

        crane_c = _quartile_color(row["Crane mph"], crane_vals, True)
        turn_c  = _quartile_color(row["Turn h"],   turn_vals, False)
        dwell_c = _quartile_color(row["Dwell d"],  dwell_vals, False)
        teu_c   = _quartile_color(row["TEU M/yr"], teu_vals, True)
        stat_c  = _STATUS_COLOR.get(row["Status"], _C_GRAY)

        color_map = {
            "Crane mph": crane_c,
            "Turn h":    turn_c,
            "Dwell d":   dwell_c,
            "TEU M/yr":  teu_c,
            "Status":    stat_c,
        }
        for i, col in enumerate(col_names):
            if col in color_map:
                styles[i] = "color: " + color_map[col] + "; font-weight: 600"
        return styles

    styled = (
        df.style
        .apply(_style_row, axis=1)
        .set_table_styles([
            {"selector": "th", "props": [
                ("background-color", C_CARD),
                ("color", C_TEXT2),
                ("font-size", "0.75rem"),
                ("text-transform", "uppercase"),
                ("letter-spacing", "0.05em"),
            ]},
            {"selector": "td", "props": [
                ("background-color", C_BG),
                ("color", C_TEXT),
                ("font-size", "0.82rem"),
                ("border-bottom", "1px solid rgba(255,255,255,0.05)"),
            ]},
        ])
        .hide(axis="index")
    )

    st.dataframe(
        df[["Rank", "Port", "LOCODE", "Status", "Crane mph", "Turn h", "Dwell d", "Gate min", "TEU M/yr"]],
        use_container_width=True,
        height=520,
    )

    # Legend
    st.markdown(
        "<div style='font-size:0.73rem;color:" + C_TEXT3 + ";margin-top:4px'>"
        + "<span style='color:" + _C_NORMAL + ";font-weight:600'>Green</span> = top quartile  |  "
        + "<span style='color:" + _C_BLUE + ";font-weight:600'>Blue</span> = middle  |  "
        + "<span style='color:" + _C_DISRUPTED + ";font-weight:600'>Red</span> = bottom quartile"
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 4: Port Capacity by Region (Stacked Area) ─────────────────────────

# Regional capacity data (M TEU, current utilisation, 2030/2040 projections)
_REGION_CAPACITY = [
    {
        "region":        "Asia East",
        "ports":         ["CNSHA", "CNNBO", "CNSZN", "CNTAO", "CNTXG", "HKHKG", "KRPUS", "TWKHH", "JPYOK"],
        "current_cap_m": 248.0,
        "utilisation":   0.79,
        "proj_2030_m":   285.0,
        "proj_2040_m":   320.0,
    },
    {
        "region":        "Southeast Asia",
        "ports":         ["SGSIN", "MYPKG", "MYTPP"],
        "current_cap_m": 77.0,
        "utilisation":   0.75,
        "proj_2030_m":   108.0,   # Tuas Phase 1-2 adds significant capacity
        "proj_2040_m":   142.0,   # Tuas full build-out: +65 M TEU
    },
    {
        "region":        "Europe",
        "ports":         ["NLRTM", "BEANR", "DEHAM", "GRPIR", "GBFXT"],
        "current_cap_m": 55.0,
        "utilisation":   0.72,
        "proj_2030_m":   62.0,
        "proj_2040_m":   68.0,
    },
    {
        "region":        "North America",
        "ports":         ["USLAX", "USLGB", "USNYC", "USSAV"],
        "current_cap_m": 48.0,
        "utilisation":   0.80,
        "proj_2030_m":   56.0,   # LA/LB zero-emission terminal expansion
        "proj_2040_m":   65.0,
    },
    {
        "region":        "Middle East",
        "ports":         ["AEJEA"],
        "current_cap_m": 22.0,
        "utilisation":   0.76,
        "proj_2030_m":   26.0,
        "proj_2040_m":   30.0,
    },
    {
        "region":        "Africa & Other",
        "ports":         ["MATNM", "LKCMB", "BRSAO"],
        "current_cap_m": 32.0,
        "utilisation":   0.71,
        "proj_2030_m":   42.0,
        "proj_2040_m":   55.0,
    },
]

_YEARS = [2020, 2023, 2025, 2027, 2030, 2035, 2040]


def _region_series(rc: dict) -> list:
    """Interpolate a simple growth curve for a region across _YEARS."""
    c0   = rc["current_cap_m"] * 0.88    # approximate 2020 baseline
    c25  = rc["current_cap_m"]
    c30  = rc["proj_2030_m"]
    c40  = rc["proj_2040_m"]

    def _lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    result = []
    for yr in _YEARS:
        if yr <= 2020:
            result.append(round(c0, 1))
        elif yr <= 2025:
            t = (yr - 2020) / 5.0
            result.append(round(_lerp(c0, c25, t), 1))
        elif yr <= 2030:
            t = (yr - 2025) / 5.0
            result.append(round(_lerp(c25, c30, t), 1))
        else:
            t = (yr - 2030) / 10.0
            result.append(round(_lerp(c30, c40, t), 1))
    return result


def _render_capacity_chart() -> None:
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
        "Africa & Other":  "#f97316",
    }

    fig = go.Figure()
    for rc in _REGION_CAPACITY:
        series = _region_series(rc)
        color  = region_colors.get(rc["region"], _C_GRAY)
        fig.add_trace(go.Scatter(
            x=_YEARS,
            y=series,
            name=rc["region"],
            mode="lines",
            stackgroup="cap",
            line={"width": 1.5, "color": color},
            fillcolor=_hex_to_rgba(color, 0.35),
            hovertemplate=rc["region"] + " %{x}: %{y:.0f}M TEU<extra></extra>",
        ))

    # Tuas expansion annotation
    fig.add_annotation(
        x=2040, y=142,
        text="Tuas full build-out<br>+65M TEU",
        showarrow=True,
        arrowhead=2,
        arrowcolor=_C_CYAN,
        arrowwidth=1.5,
        font={"color": _C_CYAN, "size": 10},
        bgcolor=_hex_to_rgba(_C_CYAN, 0.1),
        bordercolor=_C_CYAN,
    )

    # LA/LB expansion
    fig.add_annotation(
        x=2035, y=57,
        text="LA/LB zero-emission<br>terminal expansion",
        showarrow=True,
        arrowhead=2,
        arrowcolor=_C_NORMAL,
        arrowwidth=1.5,
        font={"color": _C_NORMAL, "size": 10},
        bgcolor=_hex_to_rgba(_C_NORMAL, 0.1),
        bordercolor=_C_NORMAL,
    )

    layout = dark_layout(title="Global Container Port Capacity (M TEU) — 2020-2040 Projection", height=380)
    layout["template"] = "plotly_dark"
    layout["xaxis"]["title"] = "Year"
    layout["yaxis"]["title"] = "Capacity (M TEU)"
    layout["legend"]["orientation"] = "h"
    layout["legend"]["y"] = -0.15
    layout["legend"]["x"] = 0
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, key="pm_capacity_chart")

    # Current utilisation table
    util_rows = []
    for rc in _REGION_CAPACITY:
        util_rows.append({
            "Region":         rc["region"],
            "Capacity (M TEU)": rc["current_cap_m"],
            "Utilisation":    str(round(rc["utilisation"] * 100, 0))[:-2] + "%",
            "2030 Proj":      rc["proj_2030_m"],
            "2040 Proj":      rc["proj_2040_m"],
        })

    st.dataframe(
        pd.DataFrame(util_rows),
        use_container_width=True,
        hide_index=True,
        height=240,
    )


# ── Section 5: Anomaly Feed ───────────────────────────────────────────────────

# Synthetic additional context items that add realism regardless of live stats
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
    """Return (icon, color, border_color) based on keywords."""
    upper = text.upper()
    if "DISRUPTED" in upper or "CRITICAL" in upper or "STRIKE" in upper:
        return ("!", _C_DISRUPTED, _hex_to_rgba(_C_DISRUPTED, 0.35))
    if "DEGRADED" in upper or "DEVIATION" in upper or "CONGESTION" in upper or "DELAY" in upper:
        return ("!", _C_DEGRADED, _hex_to_rgba(_C_DEGRADED, 0.30))
    return ("i", _C_BLUE, _hex_to_rgba(_C_BLUE, 0.25))


def _anomaly_card_html(text: str, idx: int) -> str:
    icon, color, border = _anomaly_severity(text)
    bg = _hex_to_rgba(color, 0.07)
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


def _render_anomaly_feed(live_all: dict) -> None:
    section_header(
        "Anomaly Feed",
        "Real-time port warnings across all 25 ports — refreshes with page",
    )

    # Live anomalies from detection
    live_flags: list = []
    for port in PORT_OPERATIONAL_DATA:
        stats = live_all.get(port.port_locode, {})
        flags = detect_port_anomalies(port.port_locode, stats)
        live_flags.extend(flags)

    all_items = live_flags + _STATIC_ANOMALY_CONTEXT

    if not all_items:
        st.success("No anomalies detected across 25 monitored ports.")
        return

    # Split into two columns
    half = math.ceil(len(all_items) / 2)
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


# ── Main render entry point ───────────────────────────────────────────────────

def render(port_results: Any = None, ais_data: Any = None) -> None:
    """Render the Port Operations Monitor tab.

    Parameters
    ----------
    port_results:
        Optional pre-computed port data from the main app pipeline.
        Not required — all data sourced from processing.port_monitor.
    ais_data:
        Optional AIS vessel position data. Reserved for future vessel-queue
        enrichment; not currently used in this tab.
    """
    logger.debug("tab_port_monitor.render() called")

    # ── Pull all live stats once (reused across all sections) ─────────────────
    live_all: dict = get_all_live_stats()

    # ── 1. Port Status Grid ────────────────────────────────────────────────────
    selected_locode = _render_port_grid(live_all)
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:22px 0'>", unsafe_allow_html=True)

    # ── 2. Live Port Detail Panel ──────────────────────────────────────────────
    _render_detail_panel(selected_locode, live_all)
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:22px 0'>", unsafe_allow_html=True)

    # ── 3. World Port Benchmark Table ─────────────────────────────────────────
    _render_benchmark_table(live_all)
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:22px 0'>", unsafe_allow_html=True)

    # ── 4. Port Capacity by Region ────────────────────────────────────────────
    _render_capacity_chart()
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:22px 0'>", unsafe_allow_html=True)

    # ── 5. Anomaly Feed ───────────────────────────────────────────────────────
    _render_anomaly_feed(live_all)
