"""tab_cargo.py — Cargo Intelligence: comprehensive commodity flows, equipment
balance, dangerous goods, reefer monitoring, and LCL/FCL optimization."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT, C_BORDER, C_CARD, C_CONV, C_HIGH, C_LOW, C_MACRO, C_MOD,
    C_SURFACE, C_TEXT, C_TEXT2, C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_header,
    wsj_market_table,
)


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
            icon="📦",
            badge_text="Demo Data",
            badge_color=C_MOD,
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
            apply_dark_layout(
                fig,
                title="Volume Share",
                height=300,
                margin=dict(t=40, b=10, l=10, r=10),
                showlegend=True,
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
            apply_dark_layout(
                fig2,
                title="Value Share",
                height=300,
                margin=dict(t=40, b=10, l=10, r=10),
                showlegend=True,
                legend=dict(font_color=C_TEXT2, bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig2, use_container_width=True, key="cargo_val_pie")
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
        st.html("<div style='height:12px;'></div>")
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
            rec_color = C_ACCENT
            rec_reason = f"At {cbm} CBM, LCL saves you ${fcl20_cost - lcl_cost:,} vs a 20ft FCL."
        elif cbm <= 28:
            rec = "20ft FCL"
            rec_color = C_HIGH
            rec_reason = f"At {cbm} CBM, a 20ft FCL (${fcl20_cost:,}) is more efficient than LCL (${lcl_cost:,})."
        else:
            rec = "40ft FCL"
            rec_color = C_MOD
            rec_reason = f"At {cbm} CBM, a 40ft FCL gives best per-CBM rate at ${fcl40_cost/67:.0f}/CBM."

        with c2:
            st.html(
                f'<div style="background:{C_CARD};border:1px solid {rec_color};'
                f'border-radius:6px;padding:20px 24px;">'
                f'<div style="font-size:0.75rem;color:{C_TEXT3};'
                f'text-transform:uppercase;margin-bottom:6px;">Recommendation</div>'
                f'<div style="font-size:1.8rem;font-weight:700;color:{rec_color};">{rec}</div>'
                f'<div style="font-size:0.85rem;color:{C_TEXT2};margin-top:8px;">{rec_reason}</div>'
                f'<div style="margin-top:14px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">'
                f'<div style="background:{C_SURFACE};border-radius:8px;padding:10px;text-align:center;">'
                f'<div style="font-size:0.7rem;color:{C_TEXT3};">LCL</div>'
                f'<div style="font-size:1.1rem;font-weight:600;color:{C_TEXT};">${lcl_cost:,}</div></div>'
                f'<div style="background:{C_SURFACE};border-radius:8px;padding:10px;text-align:center;">'
                f'<div style="font-size:0.7rem;color:{C_TEXT3};">20ft FCL</div>'
                f'<div style="font-size:1.1rem;font-weight:600;color:{C_TEXT};">${fcl20_cost:,}</div></div>'
                f'<div style="background:{C_SURFACE};border-radius:8px;padding:10px;text-align:center;">'
                f'<div style="font-size:0.7rem;color:{C_TEXT3};">40ft FCL</div>'
                f'<div style="font-size:1.1rem;font-weight:600;color:{C_TEXT};">${fcl40_cost:,}</div></div>'
                f'</div></div>'
            )
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
        st.html("<div style='height:12px;'></div>")
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
        apply_dark_layout(
            fig,
            height=320,
            margin=dict(t=20, b=10, l=10, r=10),
            yaxis=dict(
                title="TEU Surplus / Deficit",
                zeroline=True,
                zerolinecolor=C_BORDER,
                zerolinewidth=1,
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="cargo_equipment_bar")
        st.html(
            f'<div style="display:flex;gap:20px;margin-top:4px;padding:0 4px;">'
            f'<span style="font-size:0.78rem;color:{C_HIGH};">&#9646; Surplus — excess empty boxes available for export</span>'
            f'<span style="font-size:0.78rem;color:{C_LOW};">&#9646; Deficit — repositioning cost pressure on importers</span>'
            f'<span style="font-size:0.78rem;color:{C_MOD};">&#9646; Balanced — within ±2,500 TEU tolerance</span>'
            f'</div>'
        )
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
