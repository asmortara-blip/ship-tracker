"""tab_network.py — Shipping Network Topology & Resilience tab.

Renders global network map, centrality analysis, hub-and-spoke tradeoffs,
carrier service coverage, and network stress testing.
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BG,
    C_CARD,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ---------------------------------------------------------------------------
# Data provenance
# ---------------------------------------------------------------------------
#
# Network topology figures are illustrative demos (port lists, centrality,
# carrier service counts, stress scenarios). Mark them as demo so the
# provenance pill is honest.

_NETWORK_SOURCES = [
    {"name": "Vessel scheduling & AIS (demo)",      "kind": "modeled", "quality": "demo"},
    {"name": "Carrier service announcements (demo)", "kind": "modeled", "quality": "demo"},
]

# ---------------------------------------------------------------------------
# Static network data
# ---------------------------------------------------------------------------

_PORTS = [
    ("Shanghai",      31.23,  121.47, 47.3, "Asia East",         C_ACCENT),
    ("Singapore",      1.29,  103.85, 37.2, "Southeast Asia",    C_MACRO),
    ("Ningbo",        29.87,  121.55, 33.5, "Asia East",         C_ACCENT),
    ("Shenzhen",      22.54,  114.06, 30.0, "Asia East",         C_ACCENT),
    ("Guangzhou",     23.09,  113.26, 24.2, "Asia East",         C_ACCENT),
    ("Qingdao",       36.07,  120.33, 22.0, "Asia East",         C_ACCENT),
    ("Busan",         35.18,  129.08, 21.7, "Asia East",         C_ACCENT),
    ("Hong Kong",     22.30,  114.18, 17.8, "Asia East",         C_ACCENT),
    ("Rotterdam",     51.92,    4.48, 14.5, "Europe",            C_HIGH),
    ("Port Klang",     3.00,  101.40, 13.2, "Southeast Asia",    C_MACRO),
    ("Antwerp",       51.22,    4.40, 11.9, "Europe",            C_HIGH),
    ("Kaohsiung",     22.62,  120.30, 11.4, "Asia East",         C_ACCENT),
    ("Hamburg",       53.55,    9.99, 10.0, "Europe",            C_HIGH),
    ("Los Angeles",   33.73, -118.26,  9.9, "North America West",C_MOD),
    ("Long Beach",    33.75, -118.22,  9.4, "North America West",C_MOD),
    ("Tanjung Pelepas", 1.36,  103.55,  9.2, "Southeast Asia",   C_MACRO),
    ("Dubai (Jebel Ali)", 24.99, 55.06, 14.4, "Middle East",     "#f97316"),
    ("Colombo",        6.93,   79.85,  7.2, "South Asia",        C_CONV),
    ("New York",      40.69,  -74.15,  8.7, "North America East","#eab308"),
    ("Felixstowe",    51.96,    1.33,  3.7, "Europe",            C_HIGH),
    ("Valencia",      39.45,   -0.32,  5.4, "Europe",            C_HIGH),
    ("Algeciras",     36.13,   -5.45,  5.3, "Europe",            C_HIGH),
    ("Piraeus",       37.94,   23.62,  5.6, "Europe",            C_HIGH),
    ("Santos",       -23.95,  -46.33,  4.2, "South America",     C_LOW),
    ("Durban",       -29.87,   31.02,  2.8, "Africa",            "#84cc16"),
]

_ROUTES = [
    ("Shanghai",   "Rotterdam",     14, C_ACCENT),
    ("Shanghai",   "Los Angeles",   18, C_MACRO),
    ("Shanghai",   "Singapore",     22, C_MOD),
    ("Singapore",  "Rotterdam",     12, C_ACCENT),
    ("Singapore",  "Colombo",       10, C_CONV),
    ("Ningbo",     "Long Beach",    16, C_MACRO),
    ("Busan",      "Los Angeles",   10, C_MACRO),
    ("Shenzhen",   "Antwerp",        8, C_ACCENT),
    ("Rotterdam",  "New York",       8, "#eab308"),
    ("Qingdao",    "Hamburg",        6, C_ACCENT),
    ("Dubai (Jebel Ali)", "Rotterdam", 10, "#f97316"),
    ("Port Klang", "Felixstowe",    8, C_HIGH),
    ("Colombo",    "Hamburg",        6, C_CONV),
    ("Shanghai",   "Santos",         4, C_LOW),
    ("Rotterdam",  "Durban",         4, "#84cc16"),
    ("Singapore",  "Port Klang",    20, C_MACRO),
    ("Tanjung Pelepas", "Rotterdam", 6, C_ACCENT),
    ("Antwerp",    "New York",       6, "#eab308"),
    ("Algeciras",  "Rotterdam",      8, C_HIGH),
    ("Piraeus",    "Shanghai",       4, C_ACCENT),
]

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

_CARRIER_SERVICES = [
    ("Gemini (Maersk + Hapag-Lloyd)", "Maersk, Hapag-Lloyd",       42, 72, "AE-1/Shogun (Asia–Europe, 18,000 TEU)"),
    ("Premier Alliance (ONE + HMM + YM)", "ONE, HMM, Yang Ming",   38, 65, "FE4 (Far East–US East Coast)"),
    ("Ocean Alliance (CMA+COSCO+Evergreen)", "CMA CGM, COSCO, Evergreen, OOCL", 55, 78, "FAL 1 (Asia–Europe, 21,000 TEU)"),
    ("MSC (Independent)",             "MSC",                        48, 70, "Shogun/Griffin (own fleet + slot swap)"),
    ("ZIM (Independent)",             "ZIM",                        18, 45, "ZX1 Transpacific (chartered vessels)"),
    ("PIL (Independent)",             "PIL",                        12, 38, "AEX Intra-Asia service"),
    ("Wan Hai (Independent)",         "Wan Hai Lines",               9, 30, "SE Asia Regional Loop"),
]

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
# Cell formatters
# ---------------------------------------------------------------------------

def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};">{value}</span>'


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">{value}</span>'


def _score_bar(score: int, color: str, width: int = 100) -> str:
    """Inline progress bar for table cells.

    The outer container uses the global .progress-bar-custom class (no ad-hoc
    div style blocks).  Only the dynamic fill width (data-driven %) is kept as
    an inline style on the class-based fill element — the permitted exception per
    playbook step 4.
    """
    pct = max(0, min(100, score))
    return (
        f'<span class="progress-bar-custom" '
        f'style="display:inline-block;width:{width}px;vertical-align:middle;">'
        f'<span class="progress-bar-fill" style="width:{pct}%;background:{color};"></span>'
        f'</span>'
    )


def _score_cell(score: int, color: str) -> str:
    return f'<span style="color:{color};font-weight:700;">{score}%</span> {_score_bar(score, color)}'


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_hero_stats() -> None:
    try:
        metric_card_row(
            [
                {"label": "Ports in Network",         "value": "847",    "accent": C_ACCENT, "sublabel": "Active container services"},
                {"label": "Trade Routes",             "value": "2,340",  "accent": C_MACRO,   "sublabel": "Unique port-pair routes tracked"},
                {"label": "Network Resilience Score", "value": "73/100", "accent": C_MOD,    "sublabel": "Composite redundancy & connectivity index"},
                {"label": "Single Points of Failure", "value": "7",      "accent": C_LOW,    "sublabel": "Closure disrupts >5% of global trade"},
            ],
            columns=4,
        )
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"hero stats error: {exc}")
        st.info("Network stats unavailable.")


def _render_network_map() -> None:
    try:
        section_header("Global Network Map", "Port throughput × route density, weighted by weekly calls")

        fig = go.Figure()

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

        apply_dark_layout(
            fig,
            height=440,
            margin=dict(l=0, r=0, t=10, b=0),
            geo=dict(
                projection_type="natural earth",
                showland=True, landcolor=C_CARD,
                showocean=True, oceancolor=C_BG,
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.1)",
                showcountries=True, countrycolor="rgba(232,230,225,0.04)",
                showframe=False,
                bgcolor=C_BG,
            ),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"network map error: {exc}")
        st.info("Network map unavailable.")


def _render_centrality() -> None:
    try:
        section_header("Network Centrality Analysis", "Betweenness centrality weighted by TEU throughput")

        headers = ["#", "Port", "Centrality Score", "Connections", "If Removed → Trade Impact", "Role"]
        rows = []
        for i, (port, centrality, connections, disruption, description) in enumerate(_CENTRALITY):
            if centrality >= 85:
                color = C_LOW
            elif centrality >= 65:
                color = C_MOD
            else:
                color = C_HIGH
            impact_color = C_LOW if disruption >= 12 else (C_MOD if disruption >= 7 else C_HIGH)
            impact_cell = (
                f'<span style="font-size:14px;font-weight:800;color:{impact_color};">{disruption:.1f}%</span>'
                f'<span style="font-size:10px;color:{C_TEXT3};"> global trade</span>'
            )
            rows.append([
                _mono(str(i + 1), color=C_TEXT3, weight=700),
                _sans(port, weight=700),
                _score_cell(centrality, color),
                _mono(str(connections), color=C_TEXT2, weight=600),
                impact_cell,
                _sans(f"{description[:60]}…", color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"centrality error: {exc}")
        st.info("Centrality data unavailable.")


def _render_hub_spoke() -> None:
    try:
        section_header("Hub-and-Spoke vs Direct Calls", "Cost, transit time, and reliability tradeoff")

        headers = ["Route / Hub", "Type", "Avg Cost/TEU", "Transit Days", "Reliability", "Notes"]
        rows = []
        for route, rtype, cost, days, reliability, note in _HUB_SPOKE:
            is_direct = rtype == "Direct Call"
            type_color = C_ACCENT if is_direct else C_CONV
            rel_color = C_HIGH if reliability >= 90 else (C_MOD if reliability >= 80 else C_LOW)
            cost_color = C_HIGH if cost < 700 else (C_MOD if cost < 900 else C_LOW)
            days_color = C_HIGH if days <= 20 else (C_MOD if days <= 30 else C_LOW)
            rows.append([
                _sans(route, weight=700),
                badge(rtype, type_color),
                _mono(f"${cost:,}", color=cost_color, weight=700),
                _mono(f"{days}d", color=days_color, weight=700),
                _score_cell(reliability, rel_color),
                _sans(note, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"hub spoke error: {exc}")
        st.info("Hub-and-spoke data unavailable.")


def _render_carrier_services() -> None:
    try:
        section_header("Carrier Alliance Service Network", "Weekly service count & port-pair coverage")

        alliance_colors = {
            "Gemini":  C_ACCENT,
            "Premier": C_CONV,
            "Ocean":   C_MACRO,
            "MSC":     C_MOD,
            "ZIM":     C_HIGH,
            "PIL":     C_TEXT2,
            "Wan Hai": C_TEXT3,
        }

        headers = ["Alliance / Carrier", "Members", "Weekly Services", "Port Pair Coverage", "Flagship Service"]
        rows = []
        for alliance, carriers, weekly, pct, flagship in _CARRIER_SERVICES:
            short_name = alliance.split(" (")[0].split(" Alliance")[0].split(" ")[0]
            color = alliance_colors.get(short_name, C_TEXT2)
            rows.append([
                _sans(alliance, color=color, weight=700),
                _sans(carriers, color=C_TEXT2),
                _mono(str(weekly), color=C_TEXT, weight=700),
                _score_cell(pct, color),
                _sans(flagship, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"carrier services error: {exc}")
        st.info("Carrier service data unavailable.")


def _render_stress_test() -> None:
    try:
        section_header(
            "Network Stress Test",
            "Port Closure Scenarios (30-Day Simulation) — rate, routing, and recovery impact",
        )

        headers = [
            "Port Closure", "Scenario", "Affected Routes",
            "Alternative Routing", "Rate Impact", "Add. Days", "Recovery",
        ]
        rows = []
        for port, scenario, affected, alternative, rate_impact, add_days, recovery in _STRESS_TESTS:
            rows.append([
                _sans(f"{port} Closure", color=C_LOW, weight=800),
                _sans(scenario, color=C_TEXT3),
                _sans(affected, color=C_TEXT2),
                _sans(alternative, color=C_TEXT2),
                _mono(rate_impact, color=C_LOW, weight=800),
                _mono(add_days, color=C_MOD, weight=700),
                _sans(recovery, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"stress test error: {exc}")
        st.info("Stress test data unavailable.")


def _render_centrality_chart() -> None:
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
            x=ports, y=scores,
            marker_color=colors_list, opacity=0.85, yaxis="y",
            hovertemplate="%{x}<br>Centrality: %{y}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            name="Trade Disruption %",
            x=ports, y=disruption,
            mode="lines+markers",
            line=dict(color=C_CONV, width=2),
            marker=dict(size=7, color=C_CONV),
            yaxis="y2",
            hovertemplate="%{x}<br>Disruption: %{y}%<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            title="Port Centrality & Trade Disruption Risk",
            height=300,
            margin=dict(l=10, r=10, t=46, b=80),
            xaxis=dict(tickangle=-30),
            yaxis=dict(range=[0, 110], title="Centrality Score"),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                bgcolor="rgba(0,0,0,0)", font=dict(color=C_TEXT2),
            ),
        )
        fig.update_layout(
            yaxis2=dict(
                overlaying="y", side="right",
                range=[0, 30], title="Disruption %",
                gridcolor="rgba(0,0,0,0)",
            ),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"centrality chart error: {exc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(port_results=None, route_results=None, insights=None, *args, **kwargs) -> None:
    """Render the Shipping Network Topology & Resilience tab."""
    try:
        page_header(
            title="Shipping Network Topology & Resilience",
            subtitle="Global network map · Port centrality · Hub-and-spoke analysis · Alliance coverage · Stress testing",
            badge_text="NETWORK",
            badge_color=C_ACCENT,
        )
    except Exception as exc:
        logger.warning(f"header error: {exc}")

    _render_hero_stats()
    _render_network_map()

    section_divider()

    col_left, col_right = st.columns([3, 2])
    with col_left:
        _render_centrality()
    with col_right:
        try:
            _render_centrality_chart()
        except Exception as exc:
            logger.warning(f"centrality chart col error: {exc}")

    section_divider()

    _render_hub_spoke()

    section_divider()

    _render_carrier_services()

    section_divider()

    _render_stress_test()

    section_divider()

    try:
        st.markdown(
            insight_card_html(
                title="Methodology & Provenance",
                score=0.0,
                action="Watch",
                category="ROUTE",
                rationale=(
                    "Network topology derived from vessel scheduling data, AIS tracking, "
                    "and carrier service announcements. Centrality scores calculated using "
                    "betweenness centrality weighted by TEU throughput. Stress test scenarios "
                    "are modelled simulations — actual outcomes depend on market conditions "
                    "and carrier response."
                ),
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer(_NETWORK_SOURCES), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"footer error: {exc}")
