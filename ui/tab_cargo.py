"""tab_cargo.py — Cargo Intelligence: comprehensive commodity flows, equipment
balance, dangerous goods, reefer monitoring, and LCL/FCL optimization."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT, C_BORDER, C_CONV, C_HIGH, C_LOW, C_MACRO, C_MOD,
    C_TEXT, C_TEXT2, C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)


# ---------------------------------------------------------------------------
# Data provenance — demo/modeled signals for this tab
# ---------------------------------------------------------------------------
_CARGO_SOURCES = [
    DataSource.demo("Cargo intelligence demo dataset"),
    DataSource.modeled("Commodity routing model"),
]


# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------
_CARGO_TYPE_VOL = {"Container": 55, "Dry Bulk": 28, "Liquid Bulk": 17}
_CARGO_TYPE_VAL = {
    "Electronics": 35, "Chemicals": 18, "Automotive": 12,
    "Machinery": 11, "Food & Agri": 10, "Textiles": 8, "Other": 6,
}

_COMMODITIES = [
    ("Soybeans",        "Brazil",         "China",        "Capesize",     21, 18.50),
    ("Iron Ore",        "Australia",      "China",        "VLOC",         12, 8.20),
    ("Coal",            "Indonesia",      "India",        "Panamax",      14, 10.40),
    ("LNG",             "Qatar",          "Japan",        "LNG Carrier",  18, 62.00),
    ("Crude Oil",       "Saudi Arabia",   "South Korea",  "VLCC",         20, 4.80),
    ("Electronics",     "China",          "USA",          "Container",    28, 1850.00),
    ("Automobiles",     "Germany",        "USA",          "RORO",         16, 480.00),
    ("Wheat",           "USA",            "Egypt",        "Handymax",     18, 22.10),
    ("Coffee",          "Colombia",       "Germany",      "Container",    22, 135.00),
    ("Fertilizers",     "Russia",         "Brazil",       "Handymax",     24, 28.40),
    ("Chemicals",       "Netherlands",    "Singapore",    "Chemical Tkr", 26, 95.00),
    ("Copper Ore",      "Chile",          "China",        "Panamax",      32, 14.70),
    ("Palm Oil",        "Malaysia",       "India",        "Chemical Tkr", 10, 45.00),
    ("Timber",          "Canada",         "Japan",        "Handymax",     19, 32.00),
    ("Cotton",          "USA",            "Bangladesh",   "Container",    25, 88.00),
    ("Rice",            "Thailand",       "West Africa",  "Handymax",     22, 38.50),
    ("Pharmaceuticals", "India",          "USA",          "Container",    24, 2400.00),
    ("Steel Coils",     "South Korea",    "Vietnam",      "Handymax",      7, 52.00),
    ("Plastics",        "China",          "Europe",       "Container",    30, 420.00),
    ("Sugar",           "Brazil",         "Middle East",  "Panamax",      20, 24.80),
]

_HAZMAT = [
    ("Ammonium Nitrate",  "Class 5.1",  "USLAX", "High",   "MSC, CMA CGM",  "Blanket ban cargo decks"),
    ("Lithium Batteries", "Class 9",    "DEHAM", "Medium", "Hapag-Lloyd",   "Restricted to below-deck"),
    ("Chlorine Gas",      "Class 2.3",  "SGSIN", "High",   "ALL carriers",  "Special documentation req"),
    ("Crude Explosives",  "Class 1",    "CNSHA", "High",   "ALL carriers",  "Prohibited – port ban"),
    ("Hydrofluoric Acid", "Class 8",    "JPYOK", "High",   "MOL, K-Line",   "Dedicated vessel only"),
    ("Radioactive Mat.",  "Class 7",    "GBFXT", "High",   "ALL carriers",  "IMO permit mandatory"),
    ("Ethanol",           "Class 3",    "AEDXB", "Low",    "Maersk, ONE",   "Flashpoint >23°C variant"),
    ("Aerosols",          "Class 2.1",  "HKHKG", "Medium", "COSCO",         "Quantity limits enforced"),
]

_REEFER_ROUTES = [
    ("Shanghai → Los Angeles",    "Pharmaceuticals",  "-18°C", "28d", 4200, "+38%"),
    ("Rotterdam → New York",      "Fresh Produce",    "+4°C",  "12d", 3100, "+22%"),
    ("Auckland → Shanghai",       "Dairy / Meat",     "-20°C", "24d", 2800, "+41%"),
    ("Mombasa → Hamburg",         "Flowers",          "+2°C",  "18d", 1950, "+29%"),
    ("Santos → Barcelona",        "Citrus Fruit",     "+6°C",  "16d", 2400, "+25%"),
    ("Ho Chi Minh → Dubai",       "Seafood",          "-25°C", "10d", 1700, "+33%"),
]

_THEFT_ROUTES = [
    ("Santos → Europe",        "Coffee, Electronics", "Very High", 4.2,  "$12,000–18,000 / TEU"),
    ("West Africa ← Europe",   "Pharmaceuticals",     "High",      3.8,  "$9,500–14,000 / TEU"),
    ("USMEX Landbridge",       "Automotive Parts",    "High",      3.1,  "$7,000–11,000 / TEU"),
    ("India → Middle East",    "Textiles, Mobile",    "Medium",    2.4,  "$5,000–8,000 / TEU"),
    ("Philippines → China",    "Electronics",         "Medium",    2.1,  "$6,500–9,500 / TEU"),
    ("Colombia → USA",         "Clothing, Footwear",  "Low",       1.4,  "$3,000–5,000 / TEU"),
]

_EQUIPMENT_BALANCE = [
    ("East Asia",        35_000, "Surplus",  C_HIGH),
    ("South-East Asia",  8_000,  "Surplus",  C_HIGH),
    ("South Asia",      -4_000,  "Deficit",  C_LOW),
    ("North America",  -22_000,  "Deficit",  C_LOW),
    ("Europe",          -9_000,  "Deficit",  C_LOW),
    ("Latin America",   -6_000,  "Deficit",  C_LOW),
    ("Middle East",      2_000,  "Balanced", C_MOD),
    ("Africa",          -3_500,  "Deficit",  C_LOW),
]

_RISK_BADGE = {
    "Very High": "red",
    "High":      "red",
    "Medium":    "yellow",
    "Low":       "green",
}


# ---------------------------------------------------------------------------
# Cell formatters
# ---------------------------------------------------------------------------
def _sans(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">'
        f'{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};">'
        f'{value}</span>'
    )


def _risk_badge(risk: str) -> str:
    return badge(risk, _RISK_BADGE.get(risk, "blue"))


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_hero() -> None:
    try:
        page_header(
            title="Cargo Intelligence Hub",
            subtitle="Global commodity flows · Equipment balance · Specialised cargo monitoring",
            badge_text="CARGO",
            badge_color=C_ACCENT,
        )
        metric_card_row([
            {"label": "Global Container Throughput", "value": "842M TEU",
             "accent": C_HIGH, "sublabel": "▲ 3.1% YoY"},
            {"label": "TEU Demand Index", "value": "108.4",
             "accent": C_ACCENT, "sublabel": "▲ 2.7 pts MoM"},
            {"label": "LCL Share of Bookings", "value": "23%",
             "accent": C_MOD, "sublabel": "▼ 1.2 pts YoY"},
            {"label": "Reefer Volume", "value": "51.2M TEU",
             "accent": C_MACRO, "sublabel": "▲ 4.8% YoY"},
        ])
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Cargo hero render failed")
        st.error("Hero section unavailable.")


def _render_cargo_breakdown() -> None:
    try:
        section_header("Cargo Type Breakdown",
                       "Volume share and trade value by commodity class")
        c1, c2 = st.columns(2)
        with c1:
            labels = list(_CARGO_TYPE_VOL.keys())
            values = list(_CARGO_TYPE_VOL.values())
            fig = go.Figure(go.Pie(
                labels=labels, values=values,
                hole=0.55,
                marker_colors=[C_ACCENT, C_MOD, C_CONV],
                textfont_color=C_TEXT,
                textfont_size=12,
            ))
            apply_dark_layout(fig, title="Volume Share", height=300, showlegend=True)
            fig.update_layout(
                margin=dict(t=40, b=10, l=10, r=10),
                legend=dict(font_color=C_TEXT2, bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig, use_container_width=True, key="cargo_vol_pie")
        with c2:
            labels2 = list(_CARGO_TYPE_VAL.keys())
            values2 = list(_CARGO_TYPE_VAL.values())
            colors2 = [C_HIGH, C_MACRO, C_MOD, C_ACCENT, C_CONV, "#f97316", C_TEXT3]
            fig2 = go.Figure(go.Pie(
                labels=labels2, values=values2,
                hole=0.55,
                marker_colors=colors2,
                textfont_color=C_TEXT,
                textfont_size=12,
            ))
            apply_dark_layout(fig2, title="Value Share", height=300, showlegend=True)
            fig2.update_layout(
                margin=dict(t=40, b=10, l=10, r=10),
                legend=dict(font_color=C_TEXT2, bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig2, use_container_width=True, key="cargo_val_pie")
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Cargo breakdown render failed")
        st.error("Cargo breakdown unavailable.")


def _render_commodity_table() -> None:
    try:
        section_header("Commodity-to-Shipping Routing",
                       "20 key commodities with vessel type, transit time, and freight rate")
        headers = ["Commodity", "Origin", "Destination", "Vessel Type", "Days", "Rate $/MT"]
        rows = [
            [
                _sans(comm),
                _sans(origin, color=C_TEXT2, weight=500),
                _sans(dest, color=C_TEXT2, weight=500),
                _sans(vessel, color=C_ACCENT, weight=600),
                _mono(f"{days}d", color=C_TEXT, weight=500),
                _mono(f"${rate:,.2f}", color=C_HIGH, weight=700),
            ]
            for (comm, origin, dest, vessel, days, rate) in _COMMODITIES
        ]
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Commodity table render failed")
        st.error("Commodity routing table unavailable.")


def _render_hazmat() -> None:
    try:
        section_header("Dangerous Goods Tracker",
                       "Hazmat cargo restrictions by port and carrier")
        headers = ["Cargo", "Class", "Port", "Risk", "Carriers", "Restriction"]
        rows = [
            [
                _sans(cargo),
                _mono(cls, color=C_MOD, weight=600),
                _mono(port, color=C_TEXT2, weight=500),
                _risk_badge(risk),
                _sans(carriers, color=C_TEXT2, weight=500),
                _sans(restriction, color=C_TEXT3, weight=400),
            ]
            for (cargo, cls, port, risk, carriers, restriction) in _HAZMAT
        ]
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Hazmat tracker render failed")
        st.error("Dangerous goods tracker unavailable.")


def _render_reefer() -> None:
    try:
        section_header("Reefer Cargo Monitor",
                       "Temperature-sensitive cargo stats, rate premiums, and top routes")
        metric_card_row([
            {"label": "Active Reefer Units", "value": "1.84M TEU",
             "accent": C_MACRO, "sublabel": "▲ 6.2% YoY"},
            {"label": "Avg Reefer Rate Premium", "value": "+31%",
             "accent": C_MOD, "sublabel": "vs standard dry rate"},
            {"label": "Reefer Fleet Utilisation", "value": "87%",
             "accent": C_HIGH, "sublabel": "▲ 3 pts vs LY"},
        ], columns=3)
        headers = ["Route", "Cargo", "Temp", "Transit", "Rate $/FEU", "Premium"]
        rows = [
            [
                _sans(route),
                _sans(cargo, color=C_TEXT2, weight=500),
                _mono(temp, color=C_MACRO, weight=600),
                _mono(transit, color=C_TEXT, weight=500),
                _mono(f"${rate:,}", color=C_HIGH, weight=700),
                _mono(prem, color=C_MOD, weight=700),
            ]
            for (route, cargo, temp, transit, rate, prem) in _REEFER_ROUTES
        ]
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Reefer monitor render failed")
        st.error("Reefer monitor unavailable.")


def _render_lcl_fcl_optimizer() -> None:
    try:
        section_header("LCL / FCL Optimizer",
                       "Enter your cargo volume to get a cost recommendation")
        c1, c2 = st.columns([1, 2])
        with c1:
            cbm = st.number_input("Cargo Volume (CBM)", min_value=1, max_value=120,
                                   value=18, step=1)
            st.number_input("Cargo Weight (tonnes)", min_value=0.1, max_value=30.0,
                            value=8.0, step=0.5)
            route_sel = st.selectbox("Route",
                                      ["Asia → Europe", "Asia → USA",
                                       "Europe → USA", "Intra-Asia"])

        lcl_rate_per_cbm = {"Asia → Europe": 68, "Asia → USA": 82,
                             "Europe → USA": 55, "Intra-Asia": 38}
        fcl_20ft_rate   = {"Asia → Europe": 1650, "Asia → USA": 2100,
                             "Europe → USA": 1400, "Intra-Asia": 950}
        fcl_40ft_rate   = {"Asia → Europe": 2400, "Asia → USA": 3200,
                             "Europe → USA": 1900, "Intra-Asia": 1350}

        lcl_cost  = cbm * lcl_rate_per_cbm.get(route_sel, 68)
        fcl20_cost = fcl_20ft_rate.get(route_sel, 1650)
        fcl40_cost = fcl_40ft_rate.get(route_sel, 2400)

        if cbm <= 15:
            rec = "LCL"
            rec_score = 0.85
            rec_reason = f"At {cbm} CBM, LCL saves ${fcl20_cost - lcl_cost:,} vs a 20ft FCL."
        elif cbm <= 28:
            rec = "20ft FCL"
            rec_score = 0.72
            rec_reason = f"At {cbm} CBM, a 20ft FCL (${fcl20_cost:,}) beats LCL (${lcl_cost:,})."
        else:
            rec = "40ft FCL"
            rec_score = 0.58
            rec_reason = f"At {cbm} CBM, a 40ft FCL gives best per-CBM rate at ${fcl40_cost/67:.0f}/CBM."

        with c2:
            st.markdown(
                insight_card_html(
                    title=f"Recommendation: {rec}",
                    score=rec_score,
                    action="BUY",
                    rationale=rec_reason,
                    category="LCL/FCL",
                ),
                unsafe_allow_html=True,
            )
            cost_rows = [
                [
                    _sans("LCL",       color=C_TEXT,  weight=700),
                    _mono(f"${lcl_cost:,}",  color=(C_HIGH if rec == "LCL" else C_TEXT)),
                ],
                [
                    _sans("20ft FCL",  color=C_TEXT,  weight=700),
                    _mono(f"${fcl20_cost:,}", color=(C_HIGH if rec == "20ft FCL" else C_TEXT)),
                ],
                [
                    _sans("40ft FCL",  color=C_TEXT,  weight=700),
                    _mono(f"${fcl40_cost:,}", color=(C_HIGH if rec == "40ft FCL" else C_TEXT)),
                ],
            ]
            wsj_market_table(["Option", "Cost"], cost_rows)
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("LCL/FCL optimizer render failed")
        st.error("LCL/FCL optimizer unavailable.")


def _render_theft_tracker() -> None:
    try:
        section_header("Cargo Theft & Loss Tracker",
                       "High-risk routes, stolen cargo categories, and insurance implications")
        metric_card_row([
            {"label": "Annual Cargo Losses", "value": "$22.4B",
             "accent": C_LOW, "sublabel": "Global estimate 2025"},
            {"label": "Avg Loss per Incident", "value": "$148K",
             "accent": C_MOD, "sublabel": "▲ 12% vs 2024"},
            {"label": "Insurance Rate Impact", "value": "+0.3–0.8%",
             "accent": C_TEXT2, "sublabel": "High-risk route surcharge"},
        ], columns=3)
        headers = ["Route", "Cargo at Risk", "Risk Level", "Incidents/Mo", "Insurance Add-on"]
        rows = [
            [
                _sans(route),
                _sans(cargo, color=C_TEXT2, weight=500),
                _risk_badge(risk),
                _mono(f"{incidents}", color=C_TEXT, weight=600),
                _sans(insur, color=C_TEXT3, weight=400),
            ]
            for (route, cargo, risk, incidents, insur) in _THEFT_ROUTES
        ]
        wsj_market_table(headers, rows)
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Theft tracker render failed")
        st.error("Cargo theft tracker unavailable.")


def _render_equipment_balance() -> None:
    try:
        section_header("Container Equipment Balance",
                       "Regional surplus / deficit of empty containers (TEU units)")
        regions  = [r[0] for r in _EQUIPMENT_BALANCE]
        balances = [r[1] for r in _EQUIPMENT_BALANCE]
        colors   = [r[3] for r in _EQUIPMENT_BALANCE]
        fig = go.Figure(go.Bar(
            x=regions,
            y=balances,
            marker_color=colors,
            text=[f"{'+' if b > 0 else ''}{b:,}" for b in balances],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=11),
        ))
        apply_dark_layout(fig, height=320)
        fig.update_layout(
            margin=dict(t=20, b=10, l=10, r=10),
            yaxis=dict(
                title="TEU Surplus / Deficit",
                zeroline=True,
                zerolinecolor=C_BORDER,
                zerolinewidth=1,
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="cargo_equipment_bar")
        legend_rows = [
            [
                badge("Surplus", color=C_HIGH),
                _sans("Excess empty boxes available for export",
                      color=C_TEXT2, weight=500),
            ],
            [
                badge("Deficit", color=C_LOW),
                _sans("Repositioning cost pressure on importers",
                      color=C_TEXT2, weight=500),
            ],
            [
                badge("Balanced", color=C_MOD),
                _sans("Within ±2,500 TEU tolerance",
                      color=C_TEXT2, weight=500),
            ],
        ]
        wsj_market_table(["State", "Interpretation"], legend_rows)
        st.markdown(source_footer(_CARGO_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Equipment balance render failed")
        st.error("Equipment balance chart unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def render(
    port_results=None,
    route_results=None,
    freight_data=None,
    insights=None,
) -> None:
    try:
        _render_hero()
        _render_cargo_breakdown()
        _render_commodity_table()
        _render_hazmat()
        _render_reefer()
        _render_lcl_fcl_optimizer()
        _render_theft_tracker()
        _render_equipment_balance()
    except Exception:
        logger.exception("tab_cargo top-level render failed")
        st.error("Cargo Intelligence tab encountered an error.")
