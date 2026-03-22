"""Port Operations Intelligence tab — comprehensive global port intelligence dashboard.

Sections
--------
1. Port Intelligence Header    — KPI cards: monitored, critical, elevated, normal, global TEU
2. Top 20 Ports Global Rankings — Full HTML table with all key metrics
3. Port Efficiency Benchmarks   — Crane moves/hour bar chart
4. Port Status Map              — Scatter geo sized by throughput, colored by congestion
5. Regional Port Dashboard      — st.tabs by region
6. Port Events Feed             — Upcoming strikes, maintenance, upgrades
7. Port-to-Port Rate Cards      — Top 10 lane spot rates + transit times
"""
from __future__ import annotations

import random as _rand
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Color palette ─────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

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
    # Additional ports for regional tabs
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

STATUS_COLOR = {"NORMAL": C_HIGH, "ELEVATED": C_MOD, "CRITICAL": C_LOW}
STATUS_BADGE = {
    "NORMAL":   f'<span style="background:{C_HIGH}20;color:{C_HIGH};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">NORMAL</span>',
    "ELEVATED": f'<span style="background:{C_MOD}20;color:{C_MOD};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">ELEVATED</span>',
    "CRITICAL": f'<span style="background:{C_LOW}20;color:{C_LOW};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">CRITICAL</span>',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _growth_cell(v: float) -> str:
    color = C_HIGH if v >= 0 else C_LOW
    arrow = "▲" if v >= 0 else "▼"
    return f'<span style="color:{color};font-weight:600;">{arrow} {abs(v):.1f}%</span>'


def _kpi_card(label: str, value: str, sub: str, color: str) -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
        f'padding:20px 24px;border-top:3px solid {color};">'
        f'<div style="color:{C_TEXT3};font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">{label}</div>'
        f'<div style="color:{color};font-size:32px;font-weight:800;line-height:1;">{value}</div>'
        f'<div style="color:{C_TEXT2};font-size:12px;margin-top:6px;">{sub}</div>'
        f'</div>'
    )


def _section_header(title: str, subtitle: str = "") -> str:
    sub_html = f'<div style="color:{C_TEXT2};font-size:13px;margin-top:4px;">{subtitle}</div>' if subtitle else ""
    return (
        f'<div style="margin:32px 0 16px 0;">'
        f'<div style="color:{C_TEXT};font-size:20px;font-weight:700;letter-spacing:-0.3px;">{title}</div>'
        f'{sub_html}'
        f'<div style="height:2px;background:linear-gradient(90deg,{C_ACCENT},transparent);margin-top:10px;border-radius:2px;"></div>'
        f'</div>'
    )


def _dark_table_style() -> str:
    return (
        "<style>"
        "table.portmon{width:100%;border-collapse:collapse;font-size:13px;}"
        "table.portmon th{background:#0d1525;color:#64748b;font-size:10px;font-weight:700;"
        "letter-spacing:1px;text-transform:uppercase;padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.08);text-align:left;}"
        "table.portmon td{padding:9px 12px;border-bottom:1px solid rgba(255,255,255,0.04);color:#f1f5f9;vertical-align:middle;}"
        "table.portmon tr:hover td{background:rgba(59,130,246,0.06);}"
        "</style>"
    )


# ── Section 1: KPI Header ─────────────────────────────────────────────────────

def _render_kpi_header(ports: list[dict]) -> None:
    try:
        total = len(ports)
        critical = sum(1 for p in ports if p["status"] == "CRITICAL")
        elevated = sum(1 for p in ports if p["status"] == "ELEVATED")
        normal   = sum(1 for p in ports if p["status"] == "NORMAL")
        global_teu = sum(p["teu_m"] for p in ports)

        st.markdown(_section_header(
            "Port Operations Intelligence",
            f"Real-time monitoring across {total} major global ports · Updated {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        ), unsafe_allow_html=True)

        cols = st.columns(5)
        cards = [
            ("Ports Monitored",       str(total),      "global coverage",         C_ACCENT),
            ("Critical Congestion",   str(critical),   "immediate action needed", C_LOW),
            ("Elevated Status",       str(elevated),   "monitoring closely",      C_MOD),
            ("Normal Operations",     str(normal),     "within parameters",       C_HIGH),
            ("Global Throughput",     f"{global_teu:.0f}M", "TEU annual capacity",C_TEXT2),
        ]
        for col, (label, val, sub, color) in zip(cols, cards):
            col.markdown(_kpi_card(label, val, sub, color), unsafe_allow_html=True)
    except Exception:
        logger.exception("KPI header render failed")
        st.error("KPI header unavailable")


# ── Section 2: Top 20 Rankings Table ─────────────────────────────────────────

def _render_rankings_table(ports: list[dict]) -> None:
    try:
        st.markdown(_section_header(
            "Top 20 Ports — Global Rankings",
            "Annual throughput, efficiency metrics, and operational status"
        ), unsafe_allow_html=True)

        headers = [
            "Rank", "Port", "Country", "TEU M/yr", "Growth",
            "Calls/Day", "Berths", "Max Vessel", "Crane Mvs/hr", "Dwell Days", "Status"
        ]
        header_row = "".join(f"<th>{h}</th>" for h in headers)

        rows_html = ""
        for p in sorted(ports, key=lambda x: x["rank"]):
            if p["rank"] > 20:
                continue
            rank_badge = (
                f'<span style="background:{C_ACCENT};color:#fff;border-radius:50%;'
                f'width:24px;height:24px;display:inline-flex;align-items:center;'
                f'justify-content:center;font-size:11px;font-weight:700;">{p["rank"]}</span>'
            )
            dwell_color = C_LOW if p["dwell"] > 4 else (C_MOD if p["dwell"] > 3 else C_HIGH)
            crane_color = C_HIGH if p["crane_moves"] >= 30 else (C_MOD if p["crane_moves"] >= 24 else C_LOW)
            rows_html += (
                f'<tr>'
                f'<td style="text-align:center;">{rank_badge}</td>'
                f'<td style="font-weight:600;color:{C_TEXT};">{p["port"]}</td>'
                f'<td style="color:{C_TEXT2};">{p["country"]}</td>'
                f'<td style="font-weight:700;color:{C_ACCENT};">{p["teu_m"]:.0f}M</td>'
                f'<td>{_growth_cell(p["growth"])}</td>'
                f'<td style="color:{C_TEXT2};">{p["calls_day"]}</td>'
                f'<td style="color:{C_TEXT2};">{p["berths"]}</td>'
                f'<td style="color:{C_TEXT2};">{p["max_vessel"]:,}</td>'
                f'<td style="color:{crane_color};font-weight:600;">{p["crane_moves"]}</td>'
                f'<td style="color:{dwell_color};font-weight:600;">{p["dwell"]:.1f}</td>'
                f'<td>{STATUS_BADGE[p["status"]]}</td>'
                f'</tr>'
            )

        html = (
            _dark_table_style()
            + f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;padding:0;">'
            + f'<table class="portmon"><thead><tr>{header_row}</tr></thead><tbody>{rows_html}</tbody></table>'
            + '</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Rankings table render failed")
        st.error("Rankings table unavailable")


# ── Section 3: Efficiency Benchmarks Chart ────────────────────────────────────

def _render_efficiency_chart(ports: list[dict]) -> None:
    try:
        st.markdown(_section_header(
            "Port Efficiency Benchmarks",
            "Crane moves per hour — world leaders vs. laggards"
        ), unsafe_allow_html=True)

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
        fig.add_hline(y=world_avg, line_dash="dot", line_color=C_TEXT3, line_width=1.5,
                      annotation_text=f"Avg {world_avg:.1f}", annotation_font_color=C_TEXT3,
                      annotation_position="top right")
        fig.update_layout(
            plot_bgcolor=C_CARD, paper_bgcolor=C_CARD,
            font=dict(color=C_TEXT, family="Inter, sans-serif"),
            xaxis=dict(showgrid=False, tickfont=dict(size=11), tickangle=-30),
            yaxis=dict(showgrid=True, gridcolor=C_BORDER, title="Crane Moves / Hour", range=[0, max(moves) + 5]),
            margin=dict(l=20, r=20, t=20, b=60),
            height=350,
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        logger.exception("Efficiency chart render failed")
        st.error("Efficiency chart unavailable")


# ── Section 4: Port Status Map ────────────────────────────────────────────────

def _render_port_map(ports: list[dict]) -> None:
    try:
        st.markdown(_section_header(
            "Global Port Status Map",
            "Bubble size = annual throughput · Color = congestion status"
        ), unsafe_allow_html=True)

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
                    color=STATUS_COLOR[status],
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
        fig.update_layout(
            geo=dict(
                bgcolor=C_BG,
                showland=True, landcolor="#1a2235",
                showocean=True, oceancolor=C_BG,
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.12)",
                showframe=False,
                projection_type="natural earth",
            ),
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            font=dict(color=C_TEXT),
            margin=dict(l=0, r=0, t=0, b=0),
            height=480,
            legend=dict(
                orientation="h", yanchor="bottom", y=0.02, xanchor="right", x=1,
                bgcolor="rgba(17,24,39,0.8)", bordercolor=C_BORDER, borderwidth=1,
                font=dict(color=C_TEXT, size=12),
            ),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        logger.exception("Port map render failed")
        st.error("Port map unavailable")


# ── Section 5: Regional Port Dashboard ───────────────────────────────────────

def _regional_table(region_ports: list[dict]) -> str:
    headers = ["Port", "Country", "TEU M/yr", "Growth", "Berths", "Crane Mvs/hr", "Dwell", "Status"]
    header_row = "".join(f"<th>{h}</th>" for h in headers)
    rows_html = ""
    for p in sorted(region_ports, key=lambda x: x["teu_m"], reverse=True):
        dwell_color = C_LOW if p["dwell"] > 4 else (C_MOD if p["dwell"] > 3 else C_HIGH)
        rows_html += (
            f'<tr>'
            f'<td style="font-weight:600;">{p["port"]}</td>'
            f'<td style="color:{C_TEXT2};">{p["country"]}</td>'
            f'<td style="color:{C_ACCENT};font-weight:700;">{p["teu_m"]:.0f}M</td>'
            f'<td>{_growth_cell(p["growth"])}</td>'
            f'<td style="color:{C_TEXT2};">{p["berths"]}</td>'
            f'<td style="color:{C_HIGH};font-weight:600;">{p["crane_moves"]}</td>'
            f'<td style="color:{dwell_color};">{p["dwell"]:.1f} d</td>'
            f'<td>{STATUS_BADGE[p["status"]]}</td>'
            f'</tr>'
        )
    return (
        _dark_table_style()
        + f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
        + f'<table class="portmon"><thead><tr>{header_row}</tr></thead><tbody>{rows_html}</tbody></table>'
        + '</div>'
    )


def _regional_highlight(region: str, region_ports: list[dict]) -> str:
    if not region_ports:
        return ""
    top = max(region_ports, key=lambda x: x["teu_m"])
    fastest = max(region_ports, key=lambda x: x["crane_moves"])
    total_teu = sum(p["teu_m"] for p in region_ports)
    critical_count = sum(1 for p in region_ports if p["status"] == "CRITICAL")
    crit_color = C_LOW if critical_count > 0 else C_HIGH
    return (
        f'<div style="display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap;">'
        f'<div style="flex:1;min-width:140px;background:{C_BG};border:1px solid {C_BORDER};border-radius:8px;padding:14px;">'
        f'<div style="color:{C_TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Largest Port</div>'
        f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;margin-top:4px;">{top["port"]}</div>'
        f'<div style="color:{C_ACCENT};font-size:13px;">{top["teu_m"]:.0f}M TEU/yr</div>'
        f'</div>'
        f'<div style="flex:1;min-width:140px;background:{C_BG};border:1px solid {C_BORDER};border-radius:8px;padding:14px;">'
        f'<div style="color:{C_TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Most Efficient</div>'
        f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;margin-top:4px;">{fastest["port"]}</div>'
        f'<div style="color:{C_HIGH};font-size:13px;">{fastest["crane_moves"]} crane mvs/hr</div>'
        f'</div>'
        f'<div style="flex:1;min-width:140px;background:{C_BG};border:1px solid {C_BORDER};border-radius:8px;padding:14px;">'
        f'<div style="color:{C_TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Regional TEU</div>'
        f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;margin-top:4px;">{total_teu:.0f}M</div>'
        f'<div style="color:{C_TEXT2};font-size:13px;">{len(region_ports)} ports</div>'
        f'</div>'
        f'<div style="flex:1;min-width:140px;background:{C_BG};border:1px solid {C_BORDER};border-radius:8px;padding:14px;">'
        f'<div style="color:{C_TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Critical Ports</div>'
        f'<div style="color:{crit_color};font-size:24px;font-weight:800;margin-top:4px;">{critical_count}</div>'
        f'<div style="color:{C_TEXT2};font-size:13px;">require attention</div>'
        f'</div>'
        f'</div>'
    )


def _render_regional_dashboard(ports: list[dict]) -> None:
    try:
        st.markdown(_section_header(
            "Regional Port Dashboard",
            "Performance breakdown by geographic region"
        ), unsafe_allow_html=True)

        regions = ["Asia-Pacific", "Europe", "Americas", "Middle East/Africa"]
        tabs = st.tabs(regions)
        for tab, region in zip(tabs, regions):
            with tab:
                try:
                    region_ports = [p for p in ports if p["region"] == region]
                    if not region_ports:
                        st.info(f"No ports data for {region}")
                        continue
                    st.markdown(_regional_highlight(region, region_ports), unsafe_allow_html=True)
                    st.markdown(_regional_table(region_ports), unsafe_allow_html=True)
                except Exception:
                    logger.exception(f"Regional tab render failed: {region}")
                    st.error(f"{region} data unavailable")
    except Exception:
        logger.exception("Regional dashboard render failed")
        st.error("Regional dashboard unavailable")


# ── Section 6: Port Events Feed ───────────────────────────────────────────────

def _render_events_feed(events: list[dict]) -> None:
    try:
        st.markdown(_section_header(
            "Port Events Feed",
            "Upcoming events affecting port operations — strikes, maintenance, expansions"
        ), unsafe_allow_html=True)

        EVENT_COLOR = {
            "Labor Strike":      C_LOW,
            "Terminal Upgrade":  C_ACCENT,
            "Berth Maintenance": C_MOD,
            "Infrastructure":    C_LOW,
            "Dredging Works":    C_MOD,
            "New Berth Opening": C_HIGH,
            "Terminal Expansion":C_HIGH,
            "Weather Delay":     C_MOD,
            "IT System Upgrade": C_TEXT3,
        }
        EVENT_ICON = {
            "Labor Strike":      "🚫",
            "Terminal Upgrade":  "🔧",
            "Berth Maintenance": "⚙️",
            "Infrastructure":    "🏗️",
            "Dredging Works":    "⛏️",
            "New Berth Opening": "✅",
            "Terminal Expansion":"✅",
            "Weather Delay":     "⚠️",
            "IT System Upgrade": "💻",
        }

        headers = ["Port", "Event Type", "Date", "Duration", "Capacity Impact"]
        header_row = "".join(f"<th>{h}</th>" for h in headers)
        rows_html = ""
        for e in events:
            ev_color = EVENT_COLOR.get(e["type"], C_TEXT2)
            icon = EVENT_ICON.get(e["type"], "•")
            impact_color = C_LOW if "-" in e["impact"] else C_HIGH
            rows_html += (
                f'<tr>'
                f'<td style="font-weight:600;">{e["port"]}</td>'
                f'<td><span style="color:{ev_color};font-weight:600;">{icon} {e["type"]}</span></td>'
                f'<td style="color:{C_TEXT2};">{e["date"]}</td>'
                f'<td style="color:{C_TEXT2};">{e["duration"]}</td>'
                f'<td style="color:{impact_color};font-weight:600;">{e["impact"]}</td>'
                f'</tr>'
            )

        html = (
            _dark_table_style()
            + f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
            + f'<table class="portmon"><thead><tr>{header_row}</tr></thead><tbody>{rows_html}</tbody></table>'
            + '</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Events feed render failed")
        st.error("Events feed unavailable")


# ── Section 7: Port-to-Port Rate Cards ───────────────────────────────────────

def _render_rate_cards(lanes: list[dict]) -> None:
    try:
        st.markdown(_section_header(
            "Port-to-Port Rate Cards",
            "Top 10 trade lanes — spot rates, transit times, weekly services"
        ), unsafe_allow_html=True)

        cards_html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;">'
        for lane in lanes:
            rate = lane["spot_rate"]
            rate_color = C_LOW if rate > 4000 else (C_MOD if rate > 2500 else C_HIGH)
            cards_html += (
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:18px;">'
                f'<div style="font-weight:700;font-size:14px;color:{C_TEXT};margin-bottom:12px;">'
                f'{lane["from_port"]} <span style="color:{C_TEXT3};">→</span> {lane["to_port"]}'
                f'</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;letter-spacing:1px;">Spot Rate</div>'
                f'<div style="color:{rate_color};font-size:22px;font-weight:800;">${rate:,}</div>'
                f'<div style="color:{C_TEXT3};font-size:11px;">per TEU</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;letter-spacing:1px;">Transit</div>'
                f'<div style="color:{C_TEXT};font-size:22px;font-weight:800;">{lane["transit_days"]}</div>'
                f'<div style="color:{C_TEXT3};font-size:11px;">days</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;letter-spacing:1px;">Weekly Services</div>'
                f'<div style="color:{C_ACCENT};font-size:18px;font-weight:700;">{lane["weekly_svcs"]}</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;letter-spacing:1px;">Weekly Capacity</div>'
                f'<div style="color:{C_TEXT};font-size:18px;font-weight:700;">{lane["cap_teu"]:,}</div>'
                f'<div style="color:{C_TEXT3};font-size:11px;">TEU</div></div>'
                f'</div>'
                f'</div>'
            )
        cards_html += '</div>'
        st.markdown(cards_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Rate cards render failed")
        st.error("Rate cards unavailable")


# ── Main entry point ──────────────────────────────────────────────────────────

def render(port_results: Any = None, freight_data: Optional[Any] = None) -> None:
    """Render the Port Operations Intelligence tab."""
    try:
        logger.info("Rendering port monitor tab")

        # Merge live port_results into mock data where available
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

        col_chart, col_gap = st.columns([3, 1])
        with col_chart:
            _render_efficiency_chart(ports)

        _render_port_map(ports)
        _render_regional_dashboard(ports)
        _render_events_feed(PORT_EVENTS)
        _render_rate_cards(LANE_RATES)

        st.markdown(
            f'<div style="text-align:center;color:{C_TEXT3};font-size:11px;padding:24px 0 8px 0;">'
            f'Port Operations Intelligence · {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'
            f'</div>',
            unsafe_allow_html=True
        )
        logger.success("Port monitor tab rendered successfully")
    except Exception:
        logger.exception("Port monitor tab render failed")
        st.error("Port monitor dashboard encountered an error. Check logs for details.")
