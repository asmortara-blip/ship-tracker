"""tab_intermodal.py — Intermodal & Supply Chain Connectivity tab.

Sections:
  1.  Intermodal Network Dashboard (KPI strip)
  2.  Port-to-Inland Connection Table
  3.  Intermodal Network Map (Plotly scatter_geo + rail corridors)
  4.  Rail Dwell Time Tracker
  5.  Equipment Availability (chassis)
  6.  Inland Destination Analysis (pie + rail vs truck split)
  7.  Cost Comparison: all-water vs transshipment vs intermodal
  8.  Intermodal Market Signals (congestion vs rates correlation)
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    _hex_to_rgba,
    C_ACCENT,
    C_CARD,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    apply_dark_layout,
    badge,
    live_data_badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ---------------------------------------------------------------------------
# Data sources (provenance for every block)
# ---------------------------------------------------------------------------
_SRC_IANA      = DataSource.demo("IANA Intermodal Statistics")
_SRC_RAILROADS = DataSource.demo("BNSF / UP / CSX capacity bulletins")
_SRC_DRAYAGE   = DataSource.demo("POLA/POLB Drayage Report")
_SRC_CHASSIS   = DataSource.demo("Chassis Pool Operators")
_SRC_FREIGHTOS = DataSource.demo("Freightos Intermodal Index")
_SRC_MODEL     = DataSource.modeled("Proprietary Congestion Model")
_SRC_REROUTE   = DataSource.modeled(
    "Costed Reroute Recommender",
    notes=(
        "Ranks substitute corridors when a lane/chokepoint is stressed. "
        "Headroom from each port's MODELED mean-reversion baseline "
        "(processing.congestion_predictor) + regional supply deficits "
        "(processing.port_supply_lines); stressed chokepoints from "
        "processing.chokepoint_analyzer. Absolute congestion levels are "
        "baseline-seeded — only the headroom DIFFERENCES between corridors "
        "are comparative."
    ),
)

# ---------------------------------------------------------------------------
# Static reference data
# ---------------------------------------------------------------------------
_PORT_INLAND: list[dict] = [
    {"port": "Los Angeles / Long Beach", "region": "US West", "rail": "UP / BNSF (major hub)",
     "truck": "High", "dist_mi": 1745, "drayage": "$320", "rail_cost": "$1,100", "transit_d": 5, "bottleneck": "HIGH"},
    {"port": "New York / New Jersey", "region": "US East", "rail": "CSX / NS",
     "truck": "High", "dist_mi": 790, "drayage": "$410", "rail_cost": "$950", "transit_d": 4, "bottleneck": "HIGH"},
    {"port": "Seattle / Tacoma", "region": "US West", "rail": "BNSF / UP",
     "truck": "Moderate", "dist_mi": 1980, "drayage": "$290", "rail_cost": "$1,050", "transit_d": 5, "bottleneck": "LOW"},
    {"port": "Savannah", "region": "US East", "rail": "CSX / Norfolk Southern",
     "truck": "High", "dist_mi": 710, "drayage": "$280", "rail_cost": "$870", "transit_d": 4, "bottleneck": "MODERATE"},
    {"port": "Houston", "region": "US Gulf", "rail": "UP / BNSF",
     "truck": "High", "dist_mi": 500, "drayage": "$260", "rail_cost": "$780", "transit_d": 3, "bottleneck": "LOW"},
    {"port": "Baltimore", "region": "US East", "rail": "CSX",
     "truck": "Moderate", "dist_mi": 400, "drayage": "$350", "rail_cost": "$720", "transit_d": 3, "bottleneck": "LOW"},
    {"port": "Norfolk (Virginia)", "region": "US East", "rail": "Norfolk Southern",
     "truck": "Moderate", "dist_mi": 560, "drayage": "$310", "rail_cost": "$800", "transit_d": 3, "bottleneck": "LOW"},
    {"port": "Rotterdam", "region": "Europe", "rail": "DB Cargo / Europort",
     "truck": "Very High", "dist_mi": 620, "drayage": "\u20ac190", "rail_cost": "\u20ac480", "transit_d": 2, "bottleneck": "LOW"},
    {"port": "Hamburg", "region": "Europe", "rail": "DB Cargo",
     "truck": "High", "dist_mi": 530, "drayage": "\u20ac210", "rail_cost": "\u20ac520", "transit_d": 2, "bottleneck": "LOW"},
    {"port": "Felixstowe", "region": "Europe", "rail": "Freightliner",
     "truck": "High", "dist_mi": 120, "drayage": "\u00a3180", "rail_cost": "\u00a3290", "transit_d": 1, "bottleneck": "MODERATE"},
    {"port": "Antwerp", "region": "Europe", "rail": "SNCB / Rhine barge",
     "truck": "Very High", "dist_mi": 480, "drayage": "\u20ac170", "rail_cost": "\u20ac450", "transit_d": 2, "bottleneck": "LOW"},
    {"port": "Shenzhen (via HK)", "region": "Asia", "rail": "China Rail / MTR",
     "truck": "Very High", "dist_mi": 1240, "drayage": "$220", "rail_cost": "$680", "transit_d": 3, "bottleneck": "MODERATE"},
    {"port": "Shanghai", "region": "Asia", "rail": "China Rail / Yangtze barge",
     "truck": "Very High", "dist_mi": 1100, "drayage": "$200", "rail_cost": "$620", "transit_d": 3, "bottleneck": "LOW"},
    {"port": "Busan", "region": "Asia", "rail": "Korail",
     "truck": "High", "dist_mi": 320, "drayage": "$180", "rail_cost": "$410", "transit_d": 2, "bottleneck": "LOW"},
]

_DWELL: list[dict] = [
    {"port": "Los Angeles / Long Beach", "current": 8.2, "avg30": 7.4, "avg90": 5.9, "norm": 4.0},
    {"port": "New York / New Jersey",    "current": 5.8, "avg30": 5.2, "avg90": 4.7, "norm": 3.5},
    {"port": "Seattle / Tacoma",         "current": 4.1, "avg30": 3.9, "avg90": 3.6, "norm": 3.0},
    {"port": "Savannah",                 "current": 3.4, "avg30": 3.2, "avg90": 3.0, "norm": 2.8},
    {"port": "Houston",                  "current": 2.9, "avg30": 3.0, "avg90": 2.8, "norm": 2.5},
    {"port": "Rotterdam",                "current": 1.8, "avg30": 1.9, "avg90": 1.7, "norm": 1.5},
    {"port": "Shanghai",                 "current": 2.2, "avg30": 2.1, "avg90": 2.0, "norm": 1.8},
]

_CHASSIS: list[dict] = [
    {"port": "Los Angeles / Long Beach", "avail": 18_400, "demand": 24_600, "util": 75, "shortage": True,  "wait_h": 36},
    {"port": "New York / New Jersey",    "avail": 9_200,  "demand": 10_800, "util": 85, "shortage": True,  "wait_h": 18},
    {"port": "Seattle / Tacoma",         "avail": 4_800,  "demand": 5_100,  "util": 94, "shortage": True,  "wait_h": 12},
    {"port": "Savannah",                 "avail": 6_100,  "demand": 5_800,  "util": 95, "shortage": False, "wait_h":  4},
    {"port": "Houston",                  "avail": 5_700,  "demand": 5_200,  "util": 91, "shortage": False, "wait_h":  2},
    {"port": "Baltimore",                "avail": 2_900,  "demand": 2_700,  "util": 93, "shortage": False, "wait_h":  3},
    {"port": "Norfolk",                  "avail": 3_200,  "demand": 3_100,  "util": 97, "shortage": True,  "wait_h":  8},
]

_COST_COMPARE: list[dict] = [
    {
        "origin": "Shanghai",
        "dest": "Chicago",
        "options": [
            {"label": "Direct Call (all-water via Panama)", "days": 32, "cost_teu": 4_200, "mode": "Ocean"},
            {"label": "LA/LB + Transcontinental Rail",      "days": 22, "cost_teu": 4_800, "mode": "Intermodal"},
            {"label": "Houston + Inland Truck",             "days": 28, "cost_teu": 3_900, "mode": "Truck"},
        ],
    },
    {
        "origin": "Rotterdam",
        "dest": "Chicago",
        "options": [
            {"label": "All-water via NY/NJ",               "days": 18, "cost_teu": 2_800, "mode": "Ocean"},
            {"label": "NY/NJ + CSX Rail",                  "days": 14, "cost_teu": 3_100, "mode": "Intermodal"},
            {"label": "Baltimore + Truck",                  "days": 16, "cost_teu": 2_950, "mode": "Truck"},
        ],
    },
    {
        "origin": "Busan",
        "dest": "Dallas",
        "options": [
            {"label": "All-water via Gulf",                 "days": 28, "cost_teu": 3_600, "mode": "Ocean"},
            {"label": "LA/LB + BNSF Rail",                 "days": 20, "cost_teu": 4_100, "mode": "Intermodal"},
            {"label": "Seattle + UP Rail + Truck",          "days": 24, "cost_teu": 3_850, "mode": "Intermodal"},
        ],
    },
]

# Simulated weekly signals: intermodal congestion index (0-100) vs rate index
_WEEKS      = [f"W{i}" for i in range(1, 25)]
_CONGESTION = [42, 45, 48, 52, 61, 68, 72, 75, 71, 65, 60, 58,
               55, 57, 60, 64, 69, 73, 76, 74, 70, 66, 62, 59]
_RATES      = [100, 103, 107, 112, 121, 130, 136, 139, 134, 128, 122, 119,
               116, 118, 122, 127, 132, 138, 142, 139, 133, 127, 122, 118]


# ---------------------------------------------------------------------------
# Domain-specific color maps (kept local to this tab)
# ---------------------------------------------------------------------------
_BOTTLENECK_BADGE: dict[str, str] = {
    "HIGH":     C_LOW,
    "MODERATE": C_MOD,
    "LOW":      C_HIGH,
}
_MODE_COLOR: dict[str, str] = {
    "Ocean":      C_ACCENT,
    "Intermodal": C_HIGH,
    "Truck":      C_MOD,
}


def _bottleneck_badge(level: str) -> str:
    return badge(level, _BOTTLENECK_BADGE.get(level, C_ACCENT))


def _dwell_status_color(current: float) -> str:
    if current >= 7:
        return C_LOW
    if current >= 5:
        return C_MOD
    return C_HIGH


def _dwell_status_label(current: float) -> str:
    if current >= 7:
        return "CRITICAL"
    if current >= 5:
        return "ELEVATED"
    return "NORMAL"


def _dwell_vs_color(vs: float) -> str:
    if vs >= 3:
        return C_LOW
    if vs >= 1:
        return C_MOD
    return C_HIGH


def _util_color(util: int) -> str:
    if util >= 95:
        return C_LOW
    if util >= 88:
        return C_MOD
    return C_HIGH


def _wait_color(hours: int) -> str:
    if hours >= 24:
        return C_LOW
    if hours >= 8:
        return C_MOD
    return C_HIGH


# ---------------------------------------------------------------------------
# Cell formatters (tab-local)
# ---------------------------------------------------------------------------
def _sans(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">'
        f'{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sub_section(label: str) -> None:
    st.markdown(f'<div class="sub-section-header">{label}</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_kpi_strip() -> None:
    try:
        section_header(
            "Intermodal Network Dashboard",
            "Global port-to-inland connectivity metrics",
        )
        st.markdown(live_data_badge(_SRC_IANA), unsafe_allow_html=True)
        metric_card_row([
            {"label": "Active Intermodal Connections", "value": "247",
             "accent": C_HIGH, "sublabel": "\u25b2 12 MoM"},
            {"label": "Avg Port-to-Inland (days)", "value": "4.8",
             "accent": C_MOD, "sublabel": "+0.6 vs norm"},
            {"label": "Rail Capacity Utilization", "value": "83%",
             "accent": C_MOD, "sublabel": "Tight - watch LA/LB"},
            {"label": "Trucking Capacity Index", "value": "71",
             "accent": C_MOD, "sublabel": "\u25bc 5 pts MoM"},
            {"label": "Drayage Bottleneck Score", "value": "6.4 / 10",
             "accent": C_LOW, "sublabel": "Elevated at USWC"},
        ], columns=5)
    except Exception:
        logger.exception("KPI strip failed")
        st.error("KPI strip unavailable")


def _render_port_inland_table() -> None:
    try:
        section_header(
            "Port-to-Inland Connection Table",
            "Rail, truck, and drayage metrics for major global ports",
        )
        region_filter = st.selectbox(
            "Filter by region",
            ["All", "US West", "US East", "US Gulf", "Europe", "Asia"],
            key="intermodal_region_filter",
        )
        st.markdown(live_data_badge(_SRC_DRAYAGE), unsafe_allow_html=True)

        rows_src = (
            _PORT_INLAND if region_filter == "All"
            else [r for r in _PORT_INLAND if r["region"] == region_filter]
        )

        headers = [
            "Port", "Rail Connections", "Truck Capacity", "Inland Dist (mi)",
            "Drayage Cost", "Rail Cost", "Transit (days)", "Bottleneck",
        ]
        rows = [
            [
                _sans(r["port"], color=C_TEXT, weight=700),
                _sans(r["rail"], color=C_TEXT2, weight=500),
                _sans(r["truck"], color=C_TEXT2, weight=500),
                _mono(f'{r["dist_mi"]:,}', color=C_TEXT),
                _mono(r["drayage"], color=C_HIGH, weight=700),
                _mono(r["rail_cost"], color=C_ACCENT, weight=700),
                _mono(str(r["transit_d"]), color=C_TEXT),
                _bottleneck_badge(r["bottleneck"]),
            ]
            for r in rows_src
        ]
        wsj_market_table(headers, rows)
    except Exception:
        logger.exception("Port-to-inland table failed")
        st.error("Port-to-inland table unavailable")


def _render_network_map() -> None:
    try:
        section_header(
            "Intermodal Network Map",
            "Rail corridors colored by capacity utilization - green: available, amber: tight, red: constrained",
        )
        st.markdown(live_data_badge(_SRC_RAILROADS), unsafe_allow_html=True)

        nodes = [
            ("Los Angeles / Long Beach", 33.74, -118.27, "port"),
            ("Seattle / Tacoma",         47.60, -122.34, "port"),
            ("New York / NJ",            40.69,  -74.15, "port"),
            ("Savannah",                 32.08,  -81.10, "port"),
            ("Houston",                  29.73,  -95.27, "port"),
            ("Baltimore",                39.27,  -76.58, "port"),
            ("Norfolk",                  36.94,  -76.33, "port"),
            ("Chicago (Intermodal Hub)", 41.88,  -87.63, "inland"),
            ("Dallas (Inland Hub)",      32.78,  -96.80, "inland"),
            ("Kansas City",              39.10,  -94.58, "inland"),
            ("Denver",                   39.74, -104.98, "inland"),
            ("Memphis",                  35.15,  -90.05, "inland"),
            ("Atlanta",                  33.75,  -84.39, "inland"),
        ]

        corridors = [
            (0,  7,  "BNSF / UP Transcon",         C_LOW),
            (0,  9,  "UP Southwest Chief",         C_MOD),
            (0,  8,  "UP Sunset",                  C_MOD),
            (1,  7,  "BNSF Northern Transcon",     C_HIGH),
            (1,  9,  "UP/BNSF N. Route",           C_HIGH),
            (2,  7,  "CSX / NS Midwest",           C_MOD),
            (3, 11,  "CSX Southeast",              C_HIGH),
            (3,  7,  "NS / CSX Midwest",           C_HIGH),
            (4,  8,  "UP Texas Eagle",             C_HIGH),
            (4,  9,  "UP / BNSF Gulf",             C_HIGH),
            (5,  7,  "CSX Capitol",                C_HIGH),
            (6,  7,  "NS Heartland",               C_HIGH),
            (8,  7,  "UP / BNSF",                  C_HIGH),
            (9,  7,  "BNSF / UP",                  C_HIGH),
        ]

        fig = go.Figure()
        for from_i, to_i, carrier, color in corridors:
            _, flat, flon, _ = nodes[from_i]
            _, tlat, tlon, _ = nodes[to_i]
            fig.add_trace(go.Scattergeo(
                lon=[flon, tlon, None],
                lat=[flat, tlat, None],
                mode="lines",
                line={"width": 2, "color": color},
                name=carrier,
                showlegend=False,
                hoverinfo="skip",
            ))

        port_lats  = [n[1] for n in nodes if n[3] == "port"]
        port_lons  = [n[2] for n in nodes if n[3] == "port"]
        port_names = [n[0] for n in nodes if n[3] == "port"]
        in_lats    = [n[1] for n in nodes if n[3] == "inland"]
        in_lons    = [n[2] for n in nodes if n[3] == "inland"]
        in_names   = [n[0] for n in nodes if n[3] == "inland"]

        fig.add_trace(go.Scattergeo(
            lon=port_lons, lat=port_lats,
            mode="markers+text",
            marker={"size": 10, "color": C_ACCENT, "symbol": "circle"},
            text=port_names, textposition="top center",
            textfont={"color": C_TEXT, "size": 9},
            name="Seaport",
        ))
        fig.add_trace(go.Scattergeo(
            lon=in_lons, lat=in_lats,
            mode="markers+text",
            marker={"size": 8, "color": C_MOD, "symbol": "diamond"},
            text=in_names, textposition="top center",
            textfont={"color": C_TEXT2, "size": 9},
            name="Inland Hub",
        ))

        # Legend stubs for corridor coloring
        for label, color in [("Available", C_HIGH), ("Tight", C_MOD), ("Constrained", C_LOW)]:
            fig.add_trace(go.Scattergeo(
                lon=[None], lat=[None],
                mode="lines",
                line={"color": color, "width": 3},
                name=label,
            ))

        apply_dark_layout(
            fig,
            title="US Rail Corridors - Capacity Utilization",
            height=440,
            margin={"l": 0, "r": 0, "t": 40, "b": 0},
            showlegend=True,
            geo={
                "scope": "north america",
                "showland": True,
                "landcolor": C_CARD,
                "showocean": True,
                "oceancolor": "#0c0e14",
                "showcoastlines": True,
                "coastlinecolor": C_TEXT3,
                "showcountries": True,
                "countrycolor": C_TEXT3,
                "showlakes": False,
                "projection_type": "albers usa",
                "bgcolor": "rgba(0,0,0,0)",
            },
        )
        st.plotly_chart(fig, use_container_width=True, key="intermodal_network_map")
    except Exception:
        logger.exception("Network map failed")
        st.error("Intermodal network map unavailable")


def _render_dwell_tracker() -> None:
    try:
        section_header(
            "Rail Dwell Time Tracker",
            "Days containers sit at port awaiting rail pickup - >7 days flagged CRITICAL",
        )
        st.markdown(live_data_badge(_SRC_RAILROADS), unsafe_allow_html=True)

        headers = [
            "Port", "Current", "30-Day Avg", "90-Day Avg",
            "Normal", "vs Normal", "Status",
        ]
        rows = []
        for r in sorted(_DWELL, key=lambda x: -x["current"]):
            vs = r["current"] - r["norm"]
            vs_str = f"+{vs:.1f}" if vs >= 0 else f"{vs:.1f}"
            status_label = _dwell_status_label(r["current"])
            status_color = _dwell_status_color(r["current"])
            rows.append([
                _sans(r["port"], color=C_TEXT, weight=700),
                _mono(f'{r["current"]:.1f}', color=status_color, weight=700),
                _mono(f'{r["avg30"]:.1f}', color=C_TEXT2, weight=500),
                _mono(f'{r["avg90"]:.1f}', color=C_TEXT2, weight=500),
                _mono(f'{r["norm"]:.1f}', color=C_TEXT3, weight=500),
                _mono(vs_str, color=_dwell_vs_color(vs), weight=700),
                badge(status_label, _BOTTLENECK_BADGE.get(
                    "HIGH" if status_label == "CRITICAL"
                    else "MODERATE" if status_label == "ELEVATED"
                    else "LOW", C_ACCENT)),
            ])
        wsj_market_table(headers, rows)
        alert_banner(
            "LA/LB currently at <b>8.2 days</b> — 2.2 days above the critical "
            "threshold. Primary cause: BNSF slot allocation lag and chassis "
            "queue at ICTF.",
            level="warning",
        )
    except Exception:
        logger.exception("Dwell tracker failed")
        st.error("Rail dwell tracker unavailable")


def _render_equipment_availability() -> None:
    try:
        section_header(
            "Equipment Availability - Chassis by Port",
            "Chassis shortages are the hidden bottleneck in US intermodal logistics",
        )
        st.markdown(live_data_badge(_SRC_CHASSIS), unsafe_allow_html=True)

        headers = [
            "Port", "Available Chassis", "Demand", "Utilization",
            "Shortage", "Wait (hours)",
        ]
        rows = []
        for r in sorted(_CHASSIS, key=lambda x: -x["util"]):
            shortage_cell = (
                badge("YES", C_LOW) if r["shortage"]
                else badge("NO", C_HIGH)
            )
            rows.append([
                _sans(r["port"], color=C_TEXT, weight=700),
                _mono(f'{r["avail"]:,}', color=C_ACCENT, weight=700),
                _mono(f'{r["demand"]:,}', color=C_TEXT2, weight=500),
                _mono(f'{r["util"]}%', color=_util_color(r["util"]), weight=700),
                shortage_cell,
                _mono(f'{r["wait_h"]}h', color=_wait_color(r["wait_h"]), weight=700),
            ])
        wsj_market_table(headers, rows)
    except Exception:
        logger.exception("Equipment availability failed")
        st.error("Equipment availability table unavailable")


def _render_inland_destination() -> None:
    try:
        section_header(
            "Inland Destination Analysis",
            "Where do containers go after LA/LB? Asia \u2192 US West Coast trade flow breakdown",
        )
        st.markdown(live_data_badge(_SRC_IANA), unsafe_allow_html=True)

        c1, c2 = st.columns([1, 1])

        with c1:
            labels = ["Chicago", "Dallas", "Kansas City", "Denver", "Other Midwest", "Other"]
            values = [35, 12, 10, 8, 15, 20]
            colors = [C_ACCENT, C_HIGH, C_MOD, C_CONV, C_MACRO, C_TEXT3]

            fig_pie = go.Figure(go.Pie(
                labels=labels, values=values,
                hole=0.45,
                marker={"colors": colors, "line": {"color": C_SURFACE, "width": 2}},
                textfont={"color": C_TEXT, "size": 12},
                hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
            ))
            apply_dark_layout(
                fig_pie,
                title="Destination Share (%)",
                height=320,
                margin={"l": 10, "r": 10, "t": 40, "b": 10},
                showlegend=True,
                annotations=[{
                    "text": "LA/LB<br>Outflow",
                    "x": 0.5, "y": 0.5,
                    "font": {"size": 11, "color": C_TEXT2},
                    "showarrow": False,
                }],
            )
            st.plotly_chart(fig_pie, use_container_width=True, key="intermodal_dest_pie")

        with c2:
            dest_rows = [
                ("Chicago",       70, 30, 22, 1_100),
                ("Dallas",        40, 60, 18,   780),
                ("Kansas City",   65, 35, 20,   900),
                ("Denver",        55, 45, 17,   820),
                ("Other Midwest", 45, 55, 21,   950),
            ]
            _sub_section("Rail vs Truck Split by Destination")
            tbl_rows = [
                [
                    _sans(dest, color=C_TEXT, weight=700),
                    _mono(f"{rail_pct}%", color=C_ACCENT, weight=700),
                    _mono(f"{truck_pct}%", color=C_MOD, weight=700),
                    _mono(f"{days}d", color=C_TEXT2),
                    _mono(f"${cost:,}", color=C_TEXT2),
                ]
                for dest, rail_pct, truck_pct, days, cost in dest_rows
            ]
            wsj_market_table(
                ["Destination", "Rail %", "Truck %", "Transit", "Cost/TEU"],
                tbl_rows,
            )
    except Exception:
        logger.exception("Inland destination analysis failed")
        st.error("Inland destination analysis unavailable")


def _build_reroute_inputs() -> tuple[dict, dict, dict, list]:
    """Assemble REAL congestion + supply + rate inputs for the reroute ranker.

    Returns ``(routes, congestion, supply, active_disruptions)``:
      * ``routes``      — the live route registry (list[ShippingRoute]).
      * ``congestion``  — ``{locode: CongestionForecast}`` for every route's
        destination port, seeded from each port's real mean-reversion baseline
        (processing.congestion_predictor). Deterministic, no clock/RNG.
      * ``supply``      — ``{locode: PortSupplyState}`` from the real port
        supply-lines join (processing.port_supply_lines).
      * ``active``      — chokepoints with an active disruption
        (processing.chokepoint_analyzer).

    Any sub-failure degrades that channel to empty rather than raising, so the
    ranker still runs on whatever real signal is available.
    """
    from processing.chokepoint_analyzer import get_current_active_disruptions
    from processing.congestion_predictor import predict_congestion
    from processing.congestion_predictor import _port_baseline  # real per-port heuristic
    from processing.port_supply_lines import build_port_supply_chains
    from routes.route_registry import ROUTES

    # Supply states keyed by locode (real regional deficit join).
    supply: dict = {}
    try:
        chains = build_port_supply_chains()
        supply = {c.port.locode: c.port for c in chains}
    except Exception:
        logger.exception("reroute: supply-lines build failed")

    # Congestion forecasts for every destination port. Seed current congestion
    # from the port's MODELED mean-reversion baseline so the call is
    # deterministic (no live feed inside the tab). Absolute levels are
    # baseline-seeded; only the headroom DIFFERENCES between corridors are
    # comparative.
    congestion: dict = {}
    try:
        dest_locodes = {r.dest_locode for r in ROUTES}
        for locode in dest_locodes:
            seed = _port_baseline(locode)
            congestion[locode] = predict_congestion(locode, seed)
    except Exception:
        logger.exception("reroute: congestion forecast failed")

    active: list = []
    try:
        active = get_current_active_disruptions()
    except Exception:
        logger.exception("reroute: active-disruption lookup failed")

    return list(ROUTES), congestion, supply, active


def _render_reroute_recommender() -> None:
    """Costed failover: when a chokepoint is stressed, rank substitute corridors.

    Replaces the old hardcoded reroute prose with the real ranked detour —
    congestion headroom, transit-day delta, supply-deficit headroom and the
    $/FEU premium — for each active maritime disruption.
    """
    try:
        section_header(
            "Costed Reroute Recommender",
            "When a chokepoint is stressed, the ranked substitute corridors — "
            "congestion headroom, extra transit days, port supply headroom and "
            "the $/FEU premium of the detour",
        )
        st.markdown(live_data_badge(_SRC_REROUTE), unsafe_allow_html=True)

        from processing.reroute_recommender import recommend_reroutes

        routes, congestion, supply, active = _build_reroute_inputs()

        if not active:
            alert_banner(
                "No chokepoint currently flagged as disrupted — no reroute "
                "needed. Substitute corridors appear here when a passage is "
                "stressed.",
                level="info",
            )
            return

        # Map registry key (lower) for each active disruption so we can call the
        # ranker by its canonical key. chokepoint_analyzer keys the registry.
        from processing.chokepoint_analyzer import CHOKEPOINTS
        name_to_key = {cp.name: key for key, cp in CHOKEPOINTS.items()}

        any_rendered = False
        for cp in active:
            cp_key = name_to_key.get(cp.name)
            if not cp_key:
                continue
            options = recommend_reroutes(
                cp_key, routes, congestion, supply, top_n=3
            )
            if not options:
                continue

            any_rendered = True
            _sub_section(
                f'{cp.name} — {cp.current_risk_level} '
                f'({cp.current_disruption_type.replace("_", " ").title()})'
            )

            headers = [
                "Substitute Corridor", "Congestion Headroom", "Extra Transit",
                "Port Supply", "$/FEU Delta", "Composite", "Why This Detour",
            ]
            rows = []
            for o in options:
                hr = o.congestion_headroom
                hr_color = C_HIGH if hr > 0.05 else C_LOW if hr < -0.05 else C_MOD
                hr_str = f"{hr*100:+.0f}pp"

                ed = o.extra_transit_days
                ed_color = C_HIGH if ed <= 0 else C_MOD if ed <= 5 else C_LOW
                ed_str = f"+{ed}d" if ed > 0 else (f"{ed}d" if ed < 0 else "same")

                def_days = o.supply_deficit_days
                if def_days < -3:
                    sup_color, sup_str = C_LOW, f"{def_days:.0f}d short"
                elif def_days > 3:
                    sup_color, sup_str = C_HIGH, f"{def_days:+.0f}d surplus"
                else:
                    sup_color, sup_str = C_TEXT2, "balanced"

                cost = o.extra_cost_usd_feu
                if cost > 0:
                    cost_color, cost_str = C_LOW, f"+${cost:,.0f}"
                elif cost < 0:
                    cost_color, cost_str = C_HIGH, f"-${abs(cost):,.0f}"
                else:
                    cost_color, cost_str = C_TEXT3, "n/a"

                comp_color = (
                    C_HIGH if o.composite_score >= 0.66
                    else C_MOD if o.composite_score >= 0.45
                    else C_LOW
                )
                rows.append([
                    _sans(o.substitute_route_name, color=C_TEXT, weight=700),
                    _mono(hr_str, color=hr_color, weight=700),
                    _mono(ed_str, color=ed_color, weight=700),
                    _mono(sup_str, color=sup_color, weight=600),
                    _mono(cost_str, color=cost_color, weight=700),
                    badge(f"{o.composite_score:.0%}", comp_color),
                    _sans(o.rationale, color=C_TEXT2, weight=500),
                ])
            wsj_market_table(headers, rows)

        if not any_rendered:
            alert_banner(
                "No viable substitute corridor found for the currently-stressed "
                "chokepoints — every alternative lane either shares the same "
                "passage or serves a different origin→destination market.",
                level="warning",
            )
    except Exception:
        logger.exception("Reroute recommender failed")
        st.error("Costed reroute recommender unavailable")


def _render_cost_comparison() -> None:
    try:
        section_header(
            "Cost Comparison: Routing Options by Trade Lane",
            "All-water vs transshipment vs intermodal - cost and transit time per TEU",
        )
        st.markdown(live_data_badge(_SRC_FREIGHTOS), unsafe_allow_html=True)

        for pair in _COST_COMPARE:
            _sub_section(f'{pair["origin"]} \u2192 {pair["dest"]}')
            metric_card_row(
                [
                    {
                        "label":       opt["label"],
                        "value":       f'${opt["cost_teu"]:,}/TEU',
                        "accent":      _MODE_COLOR.get(opt["mode"], C_TEXT2),
                        "delta":       badge(opt["mode"].upper(),
                                            _MODE_COLOR.get(opt["mode"], C_ACCENT)),
                        "delta_color": _MODE_COLOR.get(opt["mode"], C_TEXT2),
                        "sublabel":    f'{opt["days"]} days transit',
                    }
                    for opt in pair["options"]
                ],
                columns=len(pair["options"]),
            )

        # Bar chart: cost vs days for all options
        all_labels, all_costs, all_days, all_colors = [], [], [], []
        for pair in _COST_COMPARE:
            for opt in pair["options"]:
                all_labels.append(f'{pair["origin"]}\u2192{pair["dest"]}\n{opt["mode"]}')
                all_costs.append(opt["cost_teu"])
                all_days.append(opt["days"])
                all_colors.append(_MODE_COLOR.get(opt["mode"], C_TEXT2))

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Cost ($/TEU)", x=all_labels, y=all_costs,
            marker_color=all_colors, yaxis="y",
            hovertemplate="<b>%{x}</b><br>Cost: $%{y:,}/TEU<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            name="Transit (days)", x=all_labels, y=all_days,
            mode="markers+lines",
            marker={"size": 9, "color": C_TEXT, "symbol": "diamond"},
            line={"color": C_TEXT2, "dash": "dot", "width": 1.5},
            yaxis="y2",
            hovertemplate="<b>%{x}</b><br>Transit: %{y} days<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            height=340,
            margin={"l": 50, "r": 60, "t": 30, "b": 80},
            yaxis={"title": "Cost ($/TEU)"},
            yaxis2={"title": "Transit (days)", "overlaying": "y",
                    "side": "right", "showgrid": False},
            bargap=0.3,
        )
        st.plotly_chart(fig, use_container_width=True, key="intermodal_cost_bar")
    except Exception:
        logger.exception("Cost comparison failed")
        st.error("Cost comparison unavailable")


def _render_market_signals() -> None:
    try:
        section_header(
            "Intermodal Market Signals",
            "Correlation between intermodal congestion index and freight rate index (24-week rolling)",
        )
        st.markdown(live_data_badge(_SRC_MODEL), unsafe_allow_html=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=_WEEKS, y=_CONGESTION,
            name="Congestion Index (0-100)",
            mode="lines+markers",
            line={"color": C_LOW, "width": 2},
            marker={"size": 5},
            fill="tozeroy",
            fillcolor=_hex_to_rgba(C_LOW, 0.09),
            hovertemplate="Week %{x}<br>Congestion: %{y}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=_WEEKS, y=_RATES,
            name="Freight Rate Index (base=100)",
            mode="lines+markers",
            line={"color": C_ACCENT, "width": 2},
            marker={"size": 5},
            yaxis="y2",
            hovertemplate="Week %{x}<br>Rate Index: %{y}<extra></extra>",
        ))

        peak_w = _WEEKS[_CONGESTION.index(max(_CONGESTION))]
        fig.add_annotation(
            x=peak_w, y=max(_CONGESTION),
            text="Congestion Peak", showarrow=True, arrowhead=2,
            arrowcolor=C_LOW, font={"color": C_LOW, "size": 10},
            ax=0, ay=-30,
        )
        apply_dark_layout(
            fig,
            height=320,
            margin={"l": 50, "r": 60, "t": 30, "b": 40},
            yaxis={"title": "Congestion Index", "range": [0, 100]},
            yaxis2={"title": "Rate Index", "overlaying": "y",
                    "side": "right", "showgrid": False},
        )
        st.plotly_chart(fig, use_container_width=True, key="intermodal_signals")

        # Correlation callout
        import statistics
        try:
            n = len(_CONGESTION)
            mean_c = statistics.mean(_CONGESTION)
            mean_r = statistics.mean(_RATES)
            num = sum((_CONGESTION[i] - mean_c) * (_RATES[i] - mean_r) for i in range(n))
            den = (sum((v - mean_c) ** 2 for v in _CONGESTION)
                   * sum((v - mean_r) ** 2 for v in _RATES)) ** 0.5
            corr = round(num / den, 3) if den else 0
        except Exception:
            corr = 0.87

        metric_card_row([
            {"label": "Pearson Correlation", "value": f"{corr}",
             "accent": C_HIGH,
             "sublabel": "Congestion vs freight rates (24-week rolling)"},
            {"label": "Leading Lag", "value": "2\u20133 wks",
             "accent": C_ACCENT,
             "sublabel": "Congestion leads rates"},
            {"label": "Hit Rate", "value": "4 / 5",
             "accent": C_MOD,
             "sublabel": "LA/LB dwell spikes preceding USWC rate surges"},
        ], columns=3)
    except Exception:
        logger.exception("Market signals failed")
        st.error("Market signals section unavailable")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def render(port_results=None, route_results=None, insights=None, *args, **kwargs) -> None:
    """Render the Intermodal & Supply Chain Connectivity tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('intermodal'):
        try:
            page_header(
                title="Intermodal & Supply Chain Connectivity",
                subtitle=("Port-to-inland rail corridors \u00b7 Chassis availability \u00b7 "
                          "Dwell times \u00b7 Multi-modal cost analysis \u00b7 Market signals"),
                badge_text="Demo Data",
                badge_color=C_MOD,
            )
        except Exception:
            logger.exception("Header render failed")

        _render_kpi_strip()
        _render_port_inland_table()
        _render_network_map()

        section_divider("Capacity & Equipment")

        c1, c2 = st.columns(2, gap="large")
        with c1:
            _render_dwell_tracker()
        with c2:
            _render_equipment_availability()

        section_divider("Inland Flows")
        _render_inland_destination()

        section_divider("Routing Economics")
        _render_reroute_recommender()
        _render_cost_comparison()

        section_divider("Market Signals")
        _render_market_signals()

        try:
            st.markdown(source_footer([
                _SRC_RAILROADS, _SRC_DRAYAGE, _SRC_IANA,
                _SRC_FREIGHTOS, _SRC_CHASSIS, _SRC_MODEL, _SRC_REROUTE,
            ], align="left"), unsafe_allow_html=True)
        except Exception:
            logger.exception("Footer failed")
