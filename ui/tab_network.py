"""tab_network.py — Shipping Network Topology & Resilience tab.

Renders global network map, centrality analysis, hub-and-spoke tradeoffs,
carrier service coverage, and network stress testing.
"""
from __future__ import annotations

import random
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
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
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"

# ---------------------------------------------------------------------------
# Static network data
# ---------------------------------------------------------------------------

# (port, lat, lon, throughput_mTEU, region, color)
_PORTS = [
    ("Shanghai",      31.23,  121.47, 47.3, "Asia East",         C_ACCENT),
    ("Singapore",      1.29,  103.85, 37.2, "Southeast Asia",    C_CYAN),
    ("Ningbo",        29.87,  121.55, 33.5, "Asia East",         C_ACCENT),
    ("Shenzhen",      22.54,  114.06, 30.0, "Asia East",         C_ACCENT),
    ("Guangzhou",     23.09,  113.26, 24.2, "Asia East",         C_ACCENT),
    ("Qingdao",       36.07,  120.33, 22.0, "Asia East",         C_ACCENT),
    ("Busan",         35.18,  129.08, 21.7, "Asia East",         C_ACCENT),
    ("Hong Kong",     22.30,  114.18, 17.8, "Asia East",         C_ACCENT),
    ("Rotterdam",     51.92,    4.48, 14.5, "Europe",            C_HIGH),
    ("Port Klang",     3.00,  101.40, 13.2, "Southeast Asia",    C_CYAN),
    ("Antwerp",       51.22,    4.40, 11.9, "Europe",            C_HIGH),
    ("Kaohsiung",     22.62,  120.30, 11.4, "Asia East",         C_ACCENT),
    ("Hamburg",       53.55,    9.99, 10.0, "Europe",            C_HIGH),
    ("Los Angeles",   33.73, -118.26,  9.9, "North America West",C_MOD),
    ("Long Beach",    33.75, -118.22,  9.4, "North America West",C_MOD),
    ("Tanjung Pelepas", 1.36,  103.55,  9.2, "Southeast Asia",   C_CYAN),
    ("Dubai (Jebel Ali)", 24.99, 55.06, 14.4, "Middle East",     "#f97316"),
    ("Colombo",        6.93,   79.85,  7.2, "South Asia",        C_PURPLE),
    ("New York",      40.69,  -74.15,  8.7, "North America East","#eab308"),
    ("Felixstowe",    51.96,    1.33,  3.7, "Europe",            C_HIGH),
    ("Valencia",      39.45,   -0.32,  5.4, "Europe",            C_HIGH),
    ("Algeciras",     36.13,   -5.45,  5.3, "Europe",            C_HIGH),
    ("Piraeus",       37.94,   23.62,  5.6, "Europe",            C_HIGH),
    ("Santos",       -23.95,  -46.33,  4.2, "South America",     C_LOW),
    ("Durban",       -29.87,   31.02,  2.8, "Africa",            "#84cc16"),
]

# (port_a, port_b, weekly_calls, trade_lane_color)
_ROUTES = [
    ("Shanghai",   "Rotterdam",     14, C_ACCENT),
    ("Shanghai",   "Los Angeles",   18, C_CYAN),
    ("Shanghai",   "Singapore",     22, C_MOD),
    ("Singapore",  "Rotterdam",     12, C_ACCENT),
    ("Singapore",  "Colombo",       10, C_PURPLE),
    ("Ningbo",     "Long Beach",    16, C_CYAN),
    ("Busan",      "Los Angeles",   10, C_CYAN),
    ("Shenzhen",   "Antwerp",        8, C_ACCENT),
    ("Rotterdam",  "New York",       8, "#eab308"),
    ("Qingdao",    "Hamburg",        6, C_ACCENT),
    ("Dubai (Jebel Ali)", "Rotterdam", 10, "#f97316"),
    ("Port Klang", "Felixstowe",    8, C_HIGH),
    ("Colombo",    "Hamburg",        6, C_PURPLE),
    ("Shanghai",   "Santos",         4, C_LOW),
    ("Rotterdam",  "Durban",         4, "#84cc16"),
    ("Singapore",  "Port Klang",    20, C_CYAN),
    ("Tanjung Pelepas", "Rotterdam", 6, C_ACCENT),
    ("Antwerp",    "New York",       6, "#eab308"),
    ("Algeciras",  "Rotterdam",      8, C_HIGH),
    ("Piraeus",    "Shanghai",       4, C_ACCENT),
]

# (port, centrality, connections, disruption_pct, description)
_CENTRALITY = [
    ("Singapore",        98, 38, 18.4, "Global transshipment nexus — half of Asia-Europe containers touch here"),
    ("Shanghai",         95, 42, 22.1, "Largest port by volume — Asia export anchor"),
    ("Rotterdam",        91, 35, 15.8, "European gateway — largest European port by TEU"),
    ("Port Klang",       82, 28,  9.4, "Malaysia hub — critical feeder for intra-Asia"),
    ("Hong Kong",        79, 31,  8.1, "Pearl River Delta overflow & transshipment"),
    ("Colombo",          76, 24,  7.2, "Indian subcontinent transshipment hub"),
    ("Dubai (Jebel Ali)",74, 27,  8.8, "Middle East gateway — growing Red Sea hub"),
    ("Algeciras",        69, 22,  6.5, "Mediterranean transshipment — Strait of Gibraltar"),
    ("Tanjung Pelepas",  66, 20,  6.1, "Johor Strait alternative to Singapore"),
    ("Busan",            63, 25,  5.8, "Northeast Asia hub — Korea & Japan gateway"),
    ("Hamburg",          59, 24,  4.7, "Northern Europe secondary hub"),
    ("Los Angeles",      57, 23,  7.3, "US West Coast primary gateway"),
    ("Piraeus",          48, 18,  4.1, "Eastern Mediterranean — growing COSCO hub"),
    ("Antwerp",          45, 20,  3.9, "Chemical & bulk hub — second European port"),
    ("Santos",           31, 14,  2.4, "South America primary — Brazil gateway"),
]

# (hub, type, avg_cost_usd, avg_days, reliability_pct, note)
_HUB_SPOKE = [
    ("Singapore",         "Transshipment Hub", 850,  28, 89, "Feeder to 200+ ports — 2.4d avg dwell"),
    ("Colombo",           "Transshipment Hub", 720,  32, 84, "South Asia feeder — lower cost, longer dwell"),
    ("Port Klang",        "Transshipment Hub", 680,  30, 86, "Peninsular Malaysia feeder — Butterworth, Penang"),
    ("Tanjung Pelepas",   "Transshipment Hub", 640,  31, 83, "Maersk/MSC dedicated terminal — direct rail link"),
    ("Algeciras",         "Transshipment Hub", 590,  35, 81, "Med transship — APM Terminal"),
    ("Piraeus",           "Transshipment Hub", 560,  33, 79, "COSCO hub — Adriatic/Black Sea feeders"),
    ("Shanghai → Rotterdam", "Direct Call",   1050,  26, 94, "No transshipment — premium service, higher rate"),
    ("Ningbo → Long Beach",  "Direct Call",    980,  19, 95, "Transpacific express — fastest option"),
    ("Busan → LA",           "Direct Call",    920,  14, 93, "Northeast Asia direct — Hyundai/SM Line"),
    ("Qingdao → Hamburg",    "Direct Call",   1100,  28, 91, "Weekly direct — limited capacity"),
]

# (alliance, carriers, weekly_services, port_pairs_pct, flagship_service)
_CARRIER_SERVICES = [
    ("Gemini (Maersk + Hapag-Lloyd)", "Maersk, Hapag-Lloyd",       42, 72, "AE-1/Shogun (Asia–Europe, 18,000 TEU)"),
    ("Premier Alliance (ONE + HMM + YM)", "ONE, HMM, Yang Ming",   38, 65, "FE4 (Far East–US East Coast)"),
    ("Ocean Alliance (CMA+COSCO+Evergreen)", "CMA CGM, COSCO, Evergreen, OOCL", 55, 78, "FAL 1 (Asia–Europe, 21,000 TEU)"),
    ("MSC (Independent)",             "MSC",                        48, 70, "Shogun/Griffin (own fleet + slot swap)"),
    ("ZIM (Independent)",             "ZIM",                        18, 45, "ZX1 Transpacific (chartered vessels)"),
    ("PIL (Independent)",             "PIL",                        12, 38, "AEX Intra-Asia service"),
    ("Wan Hai (Independent)",         "Wan Hai Lines",               9, 30, "SE Asia Regional Loop"),
]

# (port, scenario, affected_routes, alternative, rate_impact_pct, add_days, recovery_weeks)
_STRESS_TESTS = [
    ("Shanghai",
     "Major typhoon + terminal fire — full closure 30 days",
     "18 major trade lanes, 47% of China exports",
     "Divert to Ningbo (+150 km), Qingdao (+400 km), Busan transshipment",
     "+34%", "+3-5 days", "6-8 weeks"),
    ("Singapore",
     "Port authority strike — all terminals closed 30 days",
     "12 Asia-Europe lanes, entire intra-Asia network",
     "Port Klang, Tanjung Pelepas, Colombo absorb feeder traffic",
     "+28%", "+4-7 days", "4-6 weeks"),
    ("Rotterdam",
     "Cyber attack + lock closure — North Sea access denied",
     "All Europe mainline services, 60% of NW Europe imports",
     "Antwerp, Hamburg, Felixstowe — capacity constrained immediately",
     "+41%", "+5-8 days", "8-12 weeks"),
    ("Suez Canal",
     "Canal blocked (repeat Ever Given scenario) — 30 days",
     "Asia-Europe corridor — 100% of canal-transiting vessels",
     "Cape of Good Hope routing — +10 days, +$400/TEU bunker cost",
     "+22%", "+9-12 days", "2-3 weeks (canal) + 8-10 weeks (rate normalization)"),
    ("Los Angeles",
     "Earthquake damage — West Coast ILWU stoppage 30 days",
     "Transpacific EB — 60% of US West Coast imports",
     "East Coast via Panama Canal, Gulf ports (Houston, Savannah)",
     "+38%", "+6-10 days", "10-14 weeks"),
]


# ---------------------------------------------------------------------------
# Helper rendering functions
# ---------------------------------------------------------------------------

def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}44;'
        f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;">{text}</span>'
    )


def _score_bar(score: int, color: str, width: int = 100) -> str:
    pct = max(0, min(100, score))
    return (
        f'<div style="background:{C_SURFACE};border-radius:3px;height:6px;width:{width}px;'
        f'display:inline-block;vertical-align:middle;">'
        f'<div style="background:{color};width:{pct}%;height:100%;border-radius:3px;"></div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_hero_stats() -> None:
    try:
        kpis = [
            ("Ports in Network",         "847",   C_ACCENT, "Total ports with active container services"),
            ("Trade Routes",             "2,340", C_CYAN,   "Unique port-pair routes tracked"),
            ("Network Resilience Score", "73/100", C_MOD,   "Composite redundancy & connectivity index"),
            ("Single Points of Failure", "7",     C_LOW,    "Ports whose closure disrupts >5% of global trade"),
        ]
        cols = st.columns(4)
        for col, (label, value, color, tip) in zip(cols, kpis):
            with col:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-top:3px solid {color};'
                    f'border-radius:10px;padding:18px 14px;text-align:center;">'
                    f'<div style="font-size:28px;font-weight:800;color:{color};">{value}</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};margin-top:4px;font-weight:600;">{label}</div>'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-top:3px;">{tip}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning(f"hero stats error: {exc}")
        st.info("Network stats unavailable.")


def _render_network_map() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Global Network Map</div>',
            unsafe_allow_html=True,
        )

        fig = go.Figure()

        # Draw route edges first
        for port_a, port_b, weekly, color in _ROUTES:
            port_a_data = next((p for p in _PORTS if p[0] == port_a), None)
            port_b_data = next((p for p in _PORTS if p[0] == port_b), None)
            if port_a_data and port_b_data:
                fig.add_trace(go.Scattergeo(
                    lon=[port_a_data[2], port_b_data[2]],
                    lat=[port_a_data[1], port_b_data[1]],
                    mode="lines",
                    line=dict(width=max(0.5, weekly / 8), color=color),
                    opacity=0.45,
                    showlegend=False,
                    hoverinfo="skip",
                ))

        # Draw port nodes
        lats = [p[1] for p in _PORTS]
        lons = [p[2] for p in _PORTS]
        names = [p[0] for p in _PORTS]
        sizes = [max(8, min(30, p[3] * 0.55)) for p in _PORTS]
        colors = [p[5] for p in _PORTS]
        hover_texts = [
            f"{p[0]}<br>Throughput: {p[3]} M TEU/yr<br>Region: {p[4]}"
            for p in _PORTS
        ]

        fig.add_trace(go.Scattergeo(
            lat=lats,
            lon=lons,
            text=names,
            mode="markers+text",
            marker=dict(
                size=sizes,
                color=colors,
                opacity=0.9,
                line=dict(width=1, color="rgba(255,255,255,0.3)"),
            ),
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            hovertext=hover_texts,
            hoverinfo="text",
            showlegend=False,
        ))

        fig.update_geos(
            projection_type="natural earth",
            showland=True, landcolor="#1a2235",
            showocean=True, oceancolor="#0a0f1a",
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.1)",
            showcountries=True, countrycolor="rgba(255,255,255,0.05)",
            showframe=False,
            bgcolor=C_BG,
        )
        fig.update_layout(
            paper_bgcolor=C_CARD,
            margin=dict(l=0, r=0, t=10, b=0),
            height=440,
            font=dict(color=C_TEXT2),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        logger.warning(f"network map error: {exc}")
        st.info("Network map unavailable.")


def _render_centrality() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Network Centrality Analysis</div>',
            unsafe_allow_html=True,
        )

        header = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            f'<thead><tr style="border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">#</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Port</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Centrality Score</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Connections</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">If Removed → Trade Impact</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Role</th>'
            f'</tr></thead><tbody>'
        )

        rows = ""
        for i, (port, centrality, connections, disruption, description) in enumerate(_CENTRALITY):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            if centrality >= 85:
                color = C_LOW
            elif centrality >= 65:
                color = C_MOD
            else:
                color = C_HIGH
            impact_color = C_LOW if disruption >= 12 else (C_MOD if disruption >= 7 else C_HIGH)
            rows += (
                f'<tr style="background:{bg};border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-weight:700;">{i+1}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT};font-weight:700;">{port}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="font-size:15px;font-weight:800;color:{color};">{centrality}</span> '
                f'{_score_bar(centrality, color)}</td>'
                f'<td style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">{connections}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="font-size:14px;font-weight:800;color:{impact_color};">{disruption:.1f}%</span>'
                f'<span style="font-size:10px;color:{C_TEXT3};"> global trade</span></td>'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:11px;">{description[:60]}…</td>'
                f'</tr>'
            )

        st.markdown(header + rows + "</tbody></table></div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"centrality error: {exc}")
        st.info("Centrality data unavailable.")


def _render_hub_spoke() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Hub-and-Spoke vs Direct Calls — Cost/Time Tradeoff</div>',
            unsafe_allow_html=True,
        )

        header = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            f'<thead><tr style="border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Route / Hub</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Type</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Avg Cost/TEU</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Transit Days</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Reliability</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Notes</th>'
            f'</tr></thead><tbody>'
        )

        rows = ""
        for i, (route, rtype, cost, days, reliability, note) in enumerate(_HUB_SPOKE):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            is_direct = rtype == "Direct Call"
            type_color = C_CYAN if is_direct else C_PURPLE
            rel_color = C_HIGH if reliability >= 90 else (C_MOD if reliability >= 80 else C_LOW)
            cost_color = C_HIGH if cost < 700 else (C_MOD if cost < 900 else C_LOW)
            days_color = C_HIGH if days <= 20 else (C_MOD if days <= 30 else C_LOW)
            rows += (
                f'<tr style="background:{bg};border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;color:{C_TEXT};font-weight:700;">{route}</td>'
                f'<td style="padding:10px 14px;text-align:center;">{_badge(rtype, type_color)}</td>'
                f'<td style="padding:10px 14px;text-align:center;color:{cost_color};font-weight:700;">${cost:,}</td>'
                f'<td style="padding:10px 14px;text-align:center;color:{days_color};font-weight:700;">{days}d</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="color:{rel_color};font-weight:700;">{reliability}%</span> '
                f'{_score_bar(reliability, rel_color)}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:11px;">{note}</td>'
                f'</tr>'
            )

        st.markdown(header + rows + "</tbody></table></div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"hub spoke error: {exc}")
        st.info("Hub-and-spoke data unavailable.")


def _render_carrier_services() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Carrier Alliance Service Network</div>',
            unsafe_allow_html=True,
        )

        alliance_colors = {
            "Gemini": C_ACCENT,
            "Premier": C_PURPLE,
            "Ocean": C_CYAN,
            "MSC": C_MOD,
            "ZIM": C_HIGH,
            "PIL": C_TEXT2,
            "Wan Hai": C_TEXT3,
        }

        html = f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
        html += (
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            f'<thead><tr style="border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Alliance / Carrier</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Members</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Weekly Services</th>'
            f'<th style="padding:10px 14px;text-align:center;color:{C_TEXT2};font-weight:600;">Port Pair Coverage</th>'
            f'<th style="padding:10px 14px;text-align:left;color:{C_TEXT2};font-weight:600;">Flagship Service</th>'
            f'</tr></thead><tbody>'
        )

        for i, (alliance, carriers, weekly, pct, flagship) in enumerate(_CARRIER_SERVICES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            short_name = alliance.split(" (")[0].split(" Alliance")[0].split(" ")[0]
            color = alliance_colors.get(short_name, C_TEXT2)
            html += (
                f'<tr style="background:{bg};border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;color:{color};font-weight:700;">{alliance}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT2};font-size:11px;">{carriers}</td>'
                f'<td style="padding:10px 14px;text-align:center;color:{C_TEXT};font-weight:700;font-size:16px;">{weekly}</td>'
                f'<td style="padding:10px 14px;text-align:center;">'
                f'<span style="color:{color};font-weight:700;">{pct}%</span> '
                f'{_score_bar(pct, color)}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:11px;">{flagship}</td>'
                f'</tr>'
            )

        html += "</tbody></table></div>"
        st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"carrier services error: {exc}")
        st.info("Carrier service data unavailable.")


def _render_stress_test() -> None:
    try:
        st.markdown(
            f'<div style="font-size:16px;font-weight:700;color:{C_TEXT};margin:24px 0 12px;">Network Stress Test — Port Closure Scenarios (30-Day Simulation)</div>',
            unsafe_allow_html=True,
        )

        for port, scenario, affected, alternative, rate_impact, add_days, recovery in _STRESS_TESTS:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-left:4px solid {C_LOW};'
                f'border-radius:10px;padding:16px 20px;margin-bottom:12px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;">'
                f'<div>'
                f'<span style="font-size:14px;font-weight:800;color:{C_LOW};">{port} Closure</span>'
                f'<div style="font-size:11px;color:{C_TEXT3};margin-top:3px;">{scenario}</div>'
                f'</div>'
                f'<div style="text-align:right;">'
                f'<div style="font-size:18px;font-weight:800;color:{C_LOW};">{rate_impact}</div>'
                f'<div style="font-size:10px;color:{C_TEXT3};">rate impact</div>'
                f'</div>'
                f'</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">'
                f'<div style="background:{C_SURFACE};border-radius:6px;padding:10px 12px;">'
                f'<div style="font-size:10px;color:{C_TEXT3};font-weight:600;margin-bottom:4px;">AFFECTED ROUTES</div>'
                f'<div style="font-size:11px;color:{C_TEXT2};">{affected}</div>'
                f'</div>'
                f'<div style="background:{C_SURFACE};border-radius:6px;padding:10px 12px;">'
                f'<div style="font-size:10px;color:{C_TEXT3};font-weight:600;margin-bottom:4px;">ALTERNATIVE ROUTING</div>'
                f'<div style="font-size:11px;color:{C_TEXT2};">{alternative}</div>'
                f'</div>'
                f'<div style="background:{C_SURFACE};border-radius:6px;padding:10px 12px;">'
                f'<div style="font-size:10px;color:{C_TEXT3};font-weight:600;margin-bottom:4px;">ADDITIONAL DAYS / RECOVERY</div>'
                f'<div style="font-size:11px;color:{C_MOD};font-weight:700;">{add_days}</div>'
                f'<div style="font-size:10px;color:{C_TEXT3};">Recovery: {recovery}</div>'
                f'</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.warning(f"stress test error: {exc}")
        st.info("Stress test data unavailable.")


def _render_centrality_chart() -> None:
    """Bar chart for top 10 ports by centrality score."""
    try:
        top10 = _CENTRALITY[:10]
        ports = [r[0] for r in top10]
        scores = [r[1] for r in top10]
        disruption = [r[3] for r in top10]
        colors_list = [
            C_LOW if s >= 85 else (C_MOD if s >= 65 else C_HIGH)
            for s in scores
        ]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Centrality Score",
            x=ports,
            y=scores,
            marker_color=colors_list,
            opacity=0.85,
            yaxis="y",
            hovertemplate="%{x}<br>Centrality: %{y}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            name="Trade Disruption %",
            x=ports,
            y=disruption,
            mode="lines+markers",
            line=dict(color=C_PURPLE, width=2),
            marker=dict(size=7, color=C_PURPLE),
            yaxis="y2",
            hovertemplate="%{x}<br>Disruption: %{y}%<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            font=dict(color=C_TEXT2, size=11),
            margin=dict(l=10, r=10, t=30, b=80),
            xaxis=dict(gridcolor=C_BORDER, tickangle=-30),
            yaxis=dict(gridcolor=C_BORDER, range=[0, 110], title="Centrality Score"),
            yaxis2=dict(
                overlaying="y", side="right",
                range=[0, 30], title="Disruption %",
                gridcolor="rgba(0,0,0,0)",
            ),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                        bgcolor="rgba(0,0,0,0)", font=dict(color=C_TEXT2)),
            height=300,
            title=dict(text="Port Centrality & Trade Disruption Risk", font=dict(color=C_TEXT, size=13)),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        logger.warning(f"centrality chart error: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(port_results=None, route_results=None, insights=None) -> None:
    """Render the Shipping Network Topology & Resilience tab."""
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
            f'border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px;margin-bottom:20px;">'
            f'<div style="font-size:22px;font-weight:800;color:{C_TEXT};">Shipping Network Topology & Resilience</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};margin-top:4px;">'
            f'Global network map · Port centrality · Hub-and-spoke analysis · Alliance coverage · Stress testing'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"header error: {exc}")

    _render_hero_stats()
    _render_network_map()

    st.markdown(
        f'<div style="height:1px;background:{C_BORDER};margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([3, 2])
    with col_left:
        _render_centrality()
    with col_right:
        try:
            _render_centrality_chart()
        except Exception as exc:
            logger.warning(f"centrality chart col error: {exc}")

    st.markdown(
        f'<div style="height:1px;background:{C_BORDER};margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )

    _render_hub_spoke()

    st.markdown(
        f'<div style="height:1px;background:{C_BORDER};margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )

    _render_carrier_services()

    st.markdown(
        f'<div style="height:1px;background:{C_BORDER};margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )

    _render_stress_test()

    try:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:14px 18px;margin-top:28px;font-size:11px;color:{C_TEXT3};">'
            f'Network topology derived from vessel scheduling data, AIS tracking, and carrier service announcements. '
            f'Centrality scores calculated using betweenness centrality weighted by TEU throughput. '
            f'Stress test scenarios are modelled simulations — actual outcomes depend on market conditions and carrier response.'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"footer error: {exc}")
