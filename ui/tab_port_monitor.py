"""Port Operations Intelligence tab — comprehensive global port intelligence dashboard.

Sections
--------
1. Port Intelligence Header    — KPI cards: monitored, critical, elevated, normal, global TEU
2. Top 20 Ports Global Rankings — wsj_market_table with all key metrics
3. Port Efficiency Benchmarks   — Crane moves/hour bar chart
4. Port Status Map              — Scatter geo sized by throughput, colored by congestion
5. Regional Port Dashboard      — st.tabs by region
6. Port Events Feed             — Upcoming strikes, maintenance, upgrades
7. Port-to-Port Rate Cards      — Top 10 lane spot rates + transit times
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Data provenance ──────────────────────────────────────────────────────────
_PORT_SRC   = DataSource.demo("Top-25 Port Master List")
_EVENTS_SRC = DataSource.demo("Port Events Feed")
_LANES_SRC  = DataSource.demo("Lane Spot Rate Reference")

# ── Master port dataset ───────────────────────────────────────────────────────
TOP_PORTS = [
    {"rank": 1,  "port": "Shanghai",         "country": "China",       "region": "Asia-Pacific",     "lat": 31.23,  "lon": 121.47, "teu_m": 47.0, "growth": 4.2,  "calls_day": 210, "berths": 125, "max_vessel": 24000, "crane_moves": 32, "dwell": 2.1, "status": "NORMAL"},
    {"rank": 2,  "port": "Singapore",         "country": "Singapore",   "region": "Asia-Pacific",     "lat": 1.29,   "lon": 103.85, "teu_m": 38.0, "growth": 3.8,  "calls_day": 185, "berths": 98,  "max_vessel": 24000, "crane_moves": 35, "dwell": 1.8, "status": "NORMAL"},
    {"rank": 3,  "port": "Ningbo-Zhoushan",   "country": "China",       "region": "Asia-Pacific",     "lat": 29.87,  "lon": 121.55, "teu_m": 35.0, "growth": 6.1,  "calls_day": 160, "berths": 105, "max_vessel": 24000, "crane_moves": 30, "dwell": 2.3, "status": "ELEVATED"},
    {"rank": 4,  "port": "Shenzhen",          "country": "China",       "region": "Asia-Pacific",     "lat": 22.54,  "lon": 114.06, "teu_m": 29.0, "growth": 2.9,  "calls_day": 145, "berths": 90,  "max_vessel": 22000, "crane_moves": 29, "dwell": 2.5, "status": "NORMAL"},
    {"rank": 5,  "port": "Qingdao",           "country": "China",       "region": "Asia-Pacific",     "lat": 36.07,  "lon": 120.38, "teu_m": 26.0, "growth": 5.5,  "calls_day": 130, "berths": 80,  "max_vessel": 22000, "crane_moves": 28, "dwell": 2.2, "status": "NORMAL"},
    {"rank": 6,  "port": "Guangzhou",         "country": "China",       "region": "Asia-Pacific",     "lat": 23.13,  "lon": 113.26, "teu_m": 24.0, "growth": 3.3,  "calls_day": 120, "berths": 75,  "max_vessel": 20000, "crane_moves": 27, "dwell": 2.6, "status": "ELEVATED"},
    {"rank": 7,  "port": "Busan",             "country": "South Korea", "region": "Asia-Pacific",     "lat": 35.10,  "lon": 129.03, "teu_m": 22.0, "growth": 2.1,  "calls_day": 110, "berths": 72,  "max_vessel": 24000, "crane_moves": 31, "dwell": 1.9, "status": "NORMAL"},
    {"rank": 8,  "port": "Tianjin",           "country": "China",       "region": "Asia-Pacific",     "lat": 38.99,  "lon": 117.72, "teu_m": 21.0, "growth": 4.8,  "calls_day": 105, "berths": 68,  "max_vessel": 20000, "crane_moves": 26, "dwell": 2.7, "status": "NORMAL"},
    {"rank": 9,  "port": "Hong Kong",         "country": "Hong Kong",   "region": "Asia-Pacific",     "lat": 22.33,  "lon": 114.19, "teu_m": 16.0, "growth": -1.2, "calls_day": 95,  "berths": 60,  "max_vessel": 24000, "crane_moves": 28, "dwell": 2.0, "status": "NORMAL"},
    {"rank": 10, "port": "Rotterdam",         "country": "Netherlands", "region": "Europe",           "lat": 51.92,  "lon": 4.48,   "teu_m": 15.0, "growth": 1.8,  "calls_day": 88,  "berths": 58,  "max_vessel": 24000, "crane_moves": 26, "dwell": 3.1, "status": "ELEVATED"},
    {"rank": 11, "port": "Dubai",             "country": "UAE",         "region": "Middle East/Africa","lat": 25.20,  "lon": 55.27,  "teu_m": 15.0, "growth": 5.2,  "calls_day": 82,  "berths": 55,  "max_vessel": 22000, "crane_moves": 27, "dwell": 2.4, "status": "NORMAL"},
    {"rank": 12, "port": "Antwerp",           "country": "Belgium",     "region": "Europe",           "lat": 51.23,  "lon": 4.40,   "teu_m": 12.0, "growth": 2.4,  "calls_day": 75,  "berths": 50,  "max_vessel": 24000, "crane_moves": 25, "dwell": 3.4, "status": "CRITICAL"},
    {"rank": 13, "port": "Port Klang",        "country": "Malaysia",    "region": "Asia-Pacific",     "lat": 3.00,   "lon": 101.39, "teu_m": 12.0, "growth": 6.8,  "calls_day": 70,  "berths": 45,  "max_vessel": 20000, "crane_moves": 24, "dwell": 2.8, "status": "ELEVATED"},
    {"rank": 14, "port": "Los Angeles",       "country": "USA",         "region": "Americas",         "lat": 33.73,  "lon": -118.27,"teu_m": 10.0, "growth": 3.1,  "calls_day": 65,  "berths": 42,  "max_vessel": 24000, "crane_moves": 22, "dwell": 4.2, "status": "CRITICAL"},
    {"rank": 15, "port": "Tanjung Pelepas",   "country": "Malaysia",    "region": "Asia-Pacific",     "lat": 1.37,   "lon": 103.55, "teu_m": 9.0,  "growth": 7.3,  "calls_day": 60,  "berths": 40,  "max_vessel": 24000, "crane_moves": 33, "dwell": 1.6, "status": "NORMAL"},
    {"rank": 16, "port": "Hamburg",           "country": "Germany",     "region": "Europe",           "lat": 53.55,  "lon": 10.00,  "teu_m": 9.0,  "growth": -0.5, "calls_day": 58,  "berths": 38,  "max_vessel": 20000, "crane_moves": 24, "dwell": 3.8, "status": "ELEVATED"},
    {"rank": 17, "port": "Long Beach",        "country": "USA",         "region": "Americas",         "lat": 33.76,  "lon": -118.20,"teu_m": 9.0,  "growth": 2.8,  "calls_day": 55,  "berths": 36,  "max_vessel": 24000, "crane_moves": 21, "dwell": 4.5, "status": "CRITICAL"},
    {"rank": 18, "port": "New York",          "country": "USA",         "region": "Americas",         "lat": 40.66,  "lon": -74.04, "teu_m": 9.0,  "growth": 1.6,  "calls_day": 52,  "berths": 35,  "max_vessel": 18000, "crane_moves": 20, "dwell": 4.8, "status": "ELEVATED"},
    {"rank": 19, "port": "Colombo",           "country": "Sri Lanka",   "region": "Asia-Pacific",     "lat": 6.93,   "lon": 79.85,  "teu_m": 7.0,  "growth": 8.4,  "calls_day": 48,  "berths": 32,  "max_vessel": 20000, "crane_moves": 23, "dwell": 2.1, "status": "NORMAL"},
    {"rank": 20, "port": "Felixstowe",        "country": "UK",          "region": "Europe",           "lat": 51.96,  "lon": 1.35,   "teu_m": 4.0,  "growth": -1.8, "calls_day": 35,  "berths": 22,  "max_vessel": 20000, "crane_moves": 23, "dwell": 3.2, "status": "NORMAL"},
    {"rank": 21, "port": "Kaohsiung",         "country": "Taiwan",      "region": "Asia-Pacific",     "lat": 22.62,  "lon": 120.28, "teu_m": 9.8,  "growth": 1.4,  "calls_day": 62,  "berths": 44,  "max_vessel": 20000, "crane_moves": 26, "dwell": 2.3, "status": "NORMAL"},
    {"rank": 22, "port": "Valencia",          "country": "Spain",       "region": "Europe",           "lat": 39.44,  "lon": -0.33,  "teu_m": 5.8,  "growth": 3.9,  "calls_day": 40,  "berths": 28,  "max_vessel": 20000, "crane_moves": 22, "dwell": 2.9, "status": "NORMAL"},
    {"rank": 23, "port": "Santos",            "country": "Brazil",      "region": "Americas",         "lat": -23.95, "lon": -46.33, "teu_m": 4.9,  "growth": 4.6,  "calls_day": 38,  "berths": 26,  "max_vessel": 14000, "crane_moves": 19, "dwell": 5.1, "status": "ELEVATED"},
    {"rank": 24, "port": "Durban",            "country": "South Africa","region": "Middle East/Africa","lat": -29.87, "lon": 31.02,  "teu_m": 3.1,  "growth": 2.2,  "calls_day": 25,  "berths": 18,  "max_vessel": 12000, "crane_moves": 17, "dwell": 5.8, "status": "CRITICAL"},
    {"rank": 25, "port": "Abu Dhabi",         "country": "UAE",         "region": "Middle East/Africa","lat": 24.45,  "lon": 54.60,  "teu_m": 3.6,  "growth": 9.1,  "calls_day": 28,  "berths": 20,  "max_vessel": 18000, "crane_moves": 24, "dwell": 2.6, "status": "NORMAL"},
]

PORT_EVENTS = [
    {"port": "Antwerp",     "type": "Labor Strike",     "date": "2026-04-02", "duration": "5 days",   "impact": "-35% capacity"},
    {"port": "Los Angeles", "type": "Terminal Upgrade", "date": "2026-04-10", "duration": "14 days",  "impact": "-15% throughput"},
    {"port": "Rotterdam",   "type": "Berth Maintenance","date": "2026-04-18", "duration": "7 days",   "impact": "-10% capacity"},
    {"port": "Durban",      "type": "Infrastructure",   "date": "2026-04-05", "duration": "21 days",  "impact": "-25% capacity"},
    {"port": "Hamburg",     "type": "Dredging Works",   "date": "2026-04-22", "duration": "10 days",  "impact": "Draft limit -2m"},
    {"port": "Long Beach",  "type": "New Berth Opening","date": "2026-05-01", "duration": "Permanent","impact": "+12% capacity"},
    {"port": "Colombo",     "type": "Terminal Expansion","date": "2026-05-15","duration": "Permanent","impact": "+20% capacity"},
    {"port": "Singapore",   "type": "Tuas Phase 3",     "date": "2026-06-01", "duration": "Permanent","impact": "+18% capacity"},
    {"port": "New York",    "type": "Weather Delay",    "date": "2026-04-08", "duration": "3 days",   "impact": "-20% vessel calls"},
    {"port": "Felixstowe",  "type": "IT System Upgrade","date": "2026-04-14", "duration": "2 days",   "impact": "Minor delays"},
]

LANE_RATES = [
    {"lane": "Shanghai → Rotterdam",     "from_port": "Shanghai",   "to_port": "Rotterdam",  "spot_rate": 3850, "transit_days": 28, "weekly_svcs": 12, "cap_teu": 180000},
    {"lane": "Shanghai → Los Angeles",   "from_port": "Shanghai",   "to_port": "Los Angeles","spot_rate": 4200, "transit_days": 16, "weekly_svcs": 15, "cap_teu": 210000},
    {"lane": "Singapore → Rotterdam",    "from_port": "Singapore",  "to_port": "Rotterdam",  "spot_rate": 3500, "transit_days": 22, "weekly_svcs": 10, "cap_teu": 150000},
    {"lane": "Ningbo → Long Beach",      "from_port": "Ningbo-Zhoushan","to_port": "Long Beach","spot_rate": 4350,"transit_days": 17,"weekly_svcs": 11,"cap_teu": 160000},
    {"lane": "Busan → Hamburg",          "from_port": "Busan",      "to_port": "Hamburg",    "spot_rate": 3200, "transit_days": 25, "weekly_svcs": 8,  "cap_teu": 110000},
    {"lane": "Dubai → Rotterdam",        "from_port": "Dubai",      "to_port": "Rotterdam",  "spot_rate": 2100, "transit_days": 18, "weekly_svcs": 7,  "cap_teu": 95000},
    {"lane": "Shanghai → New York",      "from_port": "Shanghai",   "to_port": "New York",   "spot_rate": 5100, "transit_days": 31, "weekly_svcs": 9,  "cap_teu": 130000},
    {"lane": "Singapore → Los Angeles",  "from_port": "Singapore",  "to_port": "Los Angeles","spot_rate": 3900, "transit_days": 18, "weekly_svcs": 8,  "cap_teu": 115000},
    {"lane": "Antwerp → New York",       "from_port": "Antwerp",    "to_port": "New York",   "spot_rate": 1800, "transit_days": 9,  "weekly_svcs": 6,  "cap_teu": 75000},
    {"lane": "Port Klang → Rotterdam",   "from_port": "Port Klang", "to_port": "Rotterdam",  "spot_rate": 3300, "transit_days": 21, "weekly_svcs": 7,  "cap_teu": 90000},
]

_STATUS_COLOR = {"NORMAL": C_HIGH, "ELEVATED": C_MOD, "CRITICAL": C_LOW}
_STATUS_BADGE = {"NORMAL": "green", "ELEVATED": "yellow", "CRITICAL": "red"}

_EVENT_COLOR = {
    "Labor Strike":      C_LOW,
    "Terminal Upgrade":  C_ACCENT,
    "Berth Maintenance": C_MOD,
    "Infrastructure":    C_LOW,
    "Dredging Works":    C_MOD,
    "New Berth Opening": C_HIGH,
    "Terminal Expansion":C_HIGH,
    "Tuas Phase 3":      C_HIGH,
    "Weather Delay":     C_MOD,
    "IT System Upgrade": C_TEXT3,
}
_EVENT_ICON = {
    "Labor Strike":      "🚫",
    "Terminal Upgrade":  "🔧",
    "Berth Maintenance": "⚙️",
    "Infrastructure":    "🏗️",
    "Dredging Works":    "⛏️",
    "New Berth Opening": "✅",
    "Terminal Expansion":"✅",
    "Tuas Phase 3":      "✅",
    "Weather Delay":     "⚠️",
    "IT System Upgrade": "💻",
}


# ── Cell formatters ──────────────────────────────────────────────────────────

def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};">{value}</span>'


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">{value}</span>'


def _growth_cell(v: float) -> str:
    color = C_HIGH if v >= 0 else C_LOW
    arrow = "▲" if v >= 0 else "▼"
    return _mono(f"{arrow} {abs(v):.1f}%", color=color, weight=600)


# ── Section 1: KPI Header ─────────────────────────────────────────────────────

def _render_kpi_header(ports: list[dict]) -> None:
    try:
        total = len(ports)
        critical = sum(1 for p in ports if p["status"] == "CRITICAL")
        elevated = sum(1 for p in ports if p["status"] == "ELEVATED")
        normal   = sum(1 for p in ports if p["status"] == "NORMAL")
        global_teu = sum(p["teu_m"] for p in ports)
        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

        page_header(
            title="Port Operations Intelligence",
            subtitle=f"Real-time monitoring across {total} major global ports · Updated {now_str}",
            badge_text="PORT MONITOR",
            badge_color=C_ACCENT,
        )

        metric_card_row(
            [
                {"label": "Ports Monitored",     "value": str(total),           "accent": C_ACCENT, "sublabel": "global coverage"},
                {"label": "Critical Congestion", "value": str(critical),        "accent": C_LOW,    "sublabel": "immediate action needed"},
                {"label": "Elevated Status",     "value": str(elevated),        "accent": C_MOD,    "sublabel": "monitoring closely"},
                {"label": "Normal Operations",   "value": str(normal),          "accent": C_HIGH,   "sublabel": "within parameters"},
                {"label": "Global Throughput",   "value": f"{global_teu:.0f}M", "accent": C_TEXT2,  "sublabel": "TEU annual capacity"},
            ],
            columns=5,
        )
        st.markdown(source_footer([_PORT_SRC]), unsafe_allow_html=True)
    except Exception:
        logger.exception("KPI header render failed")
        st.error("KPI header unavailable")


# ── Section 2: Top 20 Rankings Table ─────────────────────────────────────────

def _render_rankings_table(ports: list[dict]) -> None:
    try:
        section_header(
            "Top 20 Ports — Global Rankings",
            "Annual throughput, efficiency metrics, and operational status",
        )

        headers = [
            "Rank", "Port", "Country", "TEU M/yr", "Growth",
            "Calls/Day", "Berths", "Max Vessel", "Crane Mvs/hr", "Dwell Days", "Status"
        ]
        rows = []
        for p in sorted(ports, key=lambda x: x["rank"]):
            if p["rank"] > 20:
                continue
            dwell_color = C_LOW if p["dwell"] > 4 else (C_MOD if p["dwell"] > 3 else C_HIGH)
            crane_color = C_HIGH if p["crane_moves"] >= 30 else (C_MOD if p["crane_moves"] >= 24 else C_LOW)
            rows.append([
                _mono(f"#{p['rank']}", color=C_ACCENT, weight=700),
                _sans(p["port"], weight=600),
                _sans(p["country"], color=C_TEXT2),
                _mono(f"{p['teu_m']:.0f}M", color=C_ACCENT, weight=700),
                _growth_cell(p["growth"]),
                _mono(str(p["calls_day"]), color=C_TEXT2),
                _mono(str(p["berths"]), color=C_TEXT2),
                _mono(f"{p['max_vessel']:,}", color=C_TEXT2),
                _mono(str(p["crane_moves"]), color=crane_color, weight=600),
                _mono(f"{p['dwell']:.1f}", color=dwell_color, weight=600),
                badge(p["status"], _STATUS_BADGE[p["status"]]),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer([_PORT_SRC]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Rankings table render failed")
        st.error("Rankings table unavailable")


# ── Section 3: Efficiency Benchmarks Chart ────────────────────────────────────

def _render_efficiency_chart(ports: list[dict]) -> None:
    try:
        section_header(
            "Port Efficiency Benchmarks",
            "Crane moves per hour — world leaders vs. laggards",
        )

        data = sorted([p for p in ports if p["rank"] <= 20], key=lambda x: x["crane_moves"], reverse=True)
        names  = [p["port"] for p in data]
        moves  = [p["crane_moves"] for p in data]
        colors = [C_HIGH if m >= 30 else (C_MOD if m >= 24 else C_LOW) for m in moves]

        fig = go.Figure(go.Bar(
            x=names, y=moves,
            marker_color=colors,
            marker_line_width=0,
            text=[str(m) for m in moves],
            textposition="outside",
            textfont=dict(color=C_TEXT, size=11),
        ))
        world_avg = sum(moves) / len(moves)
        fig.add_hline(
            y=world_avg, line_dash="dot", line_color=C_TEXT3, line_width=1.5,
            annotation_text=f"Avg {world_avg:.1f}", annotation_font_color=C_TEXT3,
            annotation_position="top right",
        )
        apply_dark_layout(
            fig,
            height=350,
            showlegend=False,
            margin=dict(l=20, r=20, t=20, b=60),
            xaxis=dict(showgrid=False, tickfont=dict(size=11), tickangle=-30),
            yaxis=dict(title="Crane Moves / Hour", range=[0, max(moves) + 5]),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer([_PORT_SRC]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Efficiency chart render failed")
        st.error("Efficiency chart unavailable")


# ── Section 4: Port Status Map ────────────────────────────────────────────────

def _render_port_map(ports: list[dict]) -> None:
    try:
        section_header(
            "Global Port Status Map",
            "Bubble size = annual throughput · Color = congestion status",
        )

        traces = []
        for status in ["NORMAL", "ELEVATED", "CRITICAL"]:
            subset = [p for p in ports if p["status"] == status]
            if not subset:
                continue
            traces.append(go.Scattergeo(
                lat=[p["lat"] for p in subset],
                lon=[p["lon"] for p in subset],
                mode="markers",
                name=status,
                marker=dict(
                    size=[max(8, p["teu_m"] * 0.9) for p in subset],
                    color=_STATUS_COLOR[status],
                    opacity=0.85,
                    line=dict(color="#ffffff", width=0.8),
                ),
                text=[
                    f"<b>{p['port']}</b><br>"
                    f"Status: {p['status']}<br>"
                    f"Throughput: {p['teu_m']:.0f}M TEU/yr<br>"
                    f"Dwell: {p['dwell']:.1f} days<br>"
                    f"Crane Moves: {p['crane_moves']}/hr"
                    for p in subset
                ],
                hovertemplate="%{text}<extra></extra>",
            ))

        fig = go.Figure(traces)
        apply_dark_layout(
            fig,
            height=480,
            margin=dict(l=0, r=0, t=0, b=0),
            geo=dict(
                bgcolor=C_BG,
                showland=True, landcolor=C_CARD,
                showocean=True, oceancolor=C_BG,
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.12)",
                showframe=False,
                projection_type="natural earth",
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=0.02, xanchor="right", x=1,
                bgcolor="rgba(17,24,39,0.8)", bordercolor=C_BORDER, borderwidth=1,
                font=dict(color=C_TEXT, size=12),
            ),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer([_PORT_SRC]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Port map render failed")
        st.error("Port map unavailable")


# ── Section 5: Regional Port Dashboard ───────────────────────────────────────

def _render_regional_table(region_ports: list[dict]) -> None:
    headers = ["Port", "Country", "TEU M/yr", "Growth", "Berths", "Crane Mvs/hr", "Dwell", "Status"]
    rows = []
    for p in sorted(region_ports, key=lambda x: x["teu_m"], reverse=True):
        dwell_color = C_LOW if p["dwell"] > 4 else (C_MOD if p["dwell"] > 3 else C_HIGH)
        rows.append([
            _sans(p["port"], weight=600),
            _sans(p["country"], color=C_TEXT2),
            _mono(f"{p['teu_m']:.0f}M", color=C_ACCENT, weight=700),
            _growth_cell(p["growth"]),
            _mono(str(p["berths"]), color=C_TEXT2),
            _mono(str(p["crane_moves"]), color=C_HIGH, weight=600),
            _mono(f"{p['dwell']:.1f} d", color=dwell_color),
            badge(p["status"], _STATUS_BADGE[p["status"]]),
        ])
    wsj_market_table(headers, rows)


def _render_regional_highlight(region_ports: list[dict]) -> None:
    if not region_ports:
        return
    top = max(region_ports, key=lambda x: x["teu_m"])
    fastest = max(region_ports, key=lambda x: x["crane_moves"])
    total_teu = sum(p["teu_m"] for p in region_ports)
    critical_count = sum(1 for p in region_ports if p["status"] == "CRITICAL")
    crit_color = C_LOW if critical_count > 0 else C_HIGH
    metric_card_row(
        [
            {"label": "Largest Port",    "value": top["port"],                 "accent": C_ACCENT, "sublabel": f"{top['teu_m']:.0f}M TEU/yr"},
            {"label": "Most Efficient",  "value": fastest["port"],             "accent": C_HIGH,   "sublabel": f"{fastest['crane_moves']} crane mvs/hr"},
            {"label": "Regional TEU",    "value": f"{total_teu:.0f}M",         "accent": C_TEXT,   "sublabel": f"{len(region_ports)} ports"},
            {"label": "Critical Ports",  "value": str(critical_count),         "accent": crit_color, "sublabel": "require attention"},
        ],
        columns=4,
    )


def _render_regional_dashboard(ports: list[dict]) -> None:
    try:
        section_header(
            "Regional Port Dashboard",
            "Performance breakdown by geographic region",
        )

        regions = ["Asia-Pacific", "Europe", "Americas", "Middle East/Africa"]
        tabs = st.tabs(regions)
        for tab, region in zip(tabs, regions):
            with tab:
                try:
                    region_ports = [p for p in ports if p["region"] == region]
                    if not region_ports:
                        st.info(f"No ports data for {region}")
                        continue
                    _render_regional_highlight(region_ports)
                    _render_regional_table(region_ports)
                    st.markdown(source_footer([_PORT_SRC]), unsafe_allow_html=True)
                except Exception:
                    logger.exception(f"Regional tab render failed: {region}")
                    st.error(f"{region} data unavailable")
    except Exception:
        logger.exception("Regional dashboard render failed")
        st.error("Regional dashboard unavailable")


# ── Section 6: Port Events Feed ───────────────────────────────────────────────

def _render_events_feed(events: list[dict]) -> None:
    try:
        section_header(
            "Port Events Feed",
            "Upcoming events affecting port operations — strikes, maintenance, expansions",
        )

        headers = ["Port", "Event Type", "Date", "Duration", "Capacity Impact"]
        rows = []
        for e in events:
            ev_color = _EVENT_COLOR.get(e["type"], C_TEXT2)
            icon = _EVENT_ICON.get(e["type"], "•")
            impact_color = C_LOW if "-" in e["impact"] else C_HIGH
            rows.append([
                _sans(e["port"], weight=600),
                _sans(f"{icon} {e['type']}", color=ev_color, weight=600),
                _mono(e["date"], color=C_TEXT2),
                _sans(e["duration"], color=C_TEXT2),
                _sans(e["impact"], color=impact_color, weight=600),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer([_EVENTS_SRC]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Events feed render failed")
        st.error("Events feed unavailable")


# ── Section 7: Port-to-Port Rate Cards ───────────────────────────────────────

def _render_rate_cards(lanes: list[dict]) -> None:
    try:
        section_header(
            "Port-to-Port Rate Cards",
            "Top 10 trade lanes — spot rates, transit times, weekly services",
        )

        headers = [
            "Lane", "Spot Rate ($/TEU)", "Transit (days)",
            "Weekly Services", "Weekly Capacity (TEU)",
        ]
        rows = []
        for lane in lanes:
            rate = lane["spot_rate"]
            rate_color = C_LOW if rate > 4000 else (C_MOD if rate > 2500 else C_HIGH)
            transit_color = C_LOW if lane["transit_days"] > 25 else (
                C_MOD if lane["transit_days"] > 18 else C_HIGH
            )
            rows.append([
                _sans(
                    f"{lane['from_port']} → {lane['to_port']}",
                    color=C_TEXT, weight=700,
                ),
                _mono(f"${rate:,}", color=rate_color, weight=700),
                _mono(f"{lane['transit_days']}", color=transit_color, weight=600),
                _mono(str(lane["weekly_svcs"]), color=C_ACCENT, weight=600),
                _mono(f"{lane['cap_teu']:,}", color=C_TEXT2),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer([_LANES_SRC]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Rate cards render failed")
        st.error("Rate cards unavailable")


# ── Main entry point ──────────────────────────────────────────────────────────

def render(port_results: Any = None, freight_data: Optional[Any] = None) -> None:
    """Render the Port Operations Intelligence tab."""
    try:
        logger.info("Rendering port monitor tab")

        ports = list(TOP_PORTS)
        if port_results:
            try:
                live_map: dict = {}
                if hasattr(port_results, "__iter__"):
                    for item in port_results:
                        name = getattr(item, "port_name", None) or (item.get("port_name") if isinstance(item, dict) else None)
                        if name:
                            live_map[name] = item
                for p in ports:
                    if p["port"] in live_map:
                        live = live_map[p["port"]]
                        if isinstance(live, dict):
                            if "status" in live:
                                p["status"] = live["status"]
                            if "teu_m" in live:
                                p["teu_m"] = float(live["teu_m"])
                        elif hasattr(live, "status"):
                            p["status"] = str(live.status)
            except Exception:
                logger.warning("Could not merge live port_results; using mock data")

        _render_kpi_header(ports)
        _render_rankings_table(ports)

        col_chart, _col_gap = st.columns([3, 1])
        with col_chart:
            _render_efficiency_chart(ports)

        _render_port_map(ports)
        _render_regional_dashboard(ports)
        _render_events_feed(PORT_EVENTS)
        _render_rate_cards(LANE_RATES)

        logger.success("Port monitor tab rendered successfully")
    except Exception:
        logger.exception("Port monitor tab render failed")
        st.error("Port monitor dashboard encountered an error. Check logs for details.")
