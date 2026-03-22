"""tab_cargo.py — Cargo Intelligence: comprehensive commodity flows, equipment
balance, dangerous goods, reefer monitoring, and LCL/FCL optimization."""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _kpi_card(label: str, value: str, delta: str = "", color: str = C_HIGH) -> None:
    delta_html = (
        f'<div style="font-size:0.72rem;color:{color};margin-top:2px;">{delta}</div>'
        if delta else ""
    )
    st.markdown(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:16px 18px;text-align:center;">'
        f'<div style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">{label}</div>'
        f'<div style="font-size:1.6rem;font-weight:700;color:{C_TEXT};">{value}</div>'
        f'{delta_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub = (
        f'<div style="font-size:0.82rem;color:{C_TEXT3};margin-top:2px;">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:28px 0 12px;">'
        f'<div style="font-size:1.05rem;font-weight:600;color:{C_TEXT};">{title}</div>'
        f'{sub}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_hero() -> None:
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
            f'border:1px solid {C_BORDER};border-radius:14px;padding:24px 28px;margin-bottom:20px;">'
            f'<div style="font-size:1.4rem;font-weight:700;color:{C_TEXT};">Cargo Intelligence Hub</div>'
            f'<div style="font-size:0.85rem;color:{C_TEXT2};margin-top:4px;">'
            f'Global commodity flows · Equipment balance · Specialised cargo monitoring</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _kpi_card("Global Container Throughput", "842M TEU", "▲ 3.1% YoY", C_HIGH)
        with c2:
            _kpi_card("TEU Demand Index", "108.4", "▲ 2.7 pts MoM", C_ACCENT)
        with c3:
            _kpi_card("LCL Share of Bookings", "23%", "▼ 1.2 pts YoY", C_MOD)
        with c4:
            _kpi_card("Reefer Volume", "51.2M TEU", "▲ 4.8% YoY", C_CYAN)
    except Exception:
        logger.exception("Cargo hero render failed")
        st.error("Hero section unavailable.")


def _render_cargo_breakdown() -> None:
    try:
        _section_header("Cargo Type Breakdown", "Volume share and trade value by commodity class")
        c1, c2 = st.columns(2)
        with c1:
            labels = list(_CARGO_TYPE_VOL.keys())
            values = list(_CARGO_TYPE_VOL.values())
            fig = go.Figure(go.Pie(
                labels=labels, values=values,
                hole=0.55,
                marker_colors=[C_ACCENT, C_MOD, C_PURPLE],
                textfont_color=C_TEXT,
                textfont_size=12,
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color=C_TEXT,
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=True,
                legend=dict(font_color=C_TEXT2, bgcolor="rgba(0,0,0,0)"),
                title=dict(text="Volume Share", font_color=C_TEXT2, font_size=13, x=0.5),
            )
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            labels2 = list(_CARGO_TYPE_VAL.keys())
            values2 = list(_CARGO_TYPE_VAL.values())
            colors2 = [C_HIGH, C_CYAN, C_MOD, C_ACCENT, C_PURPLE, "#f97316", C_TEXT3]
            fig2 = go.Figure(go.Pie(
                labels=labels2, values=values2,
                hole=0.55,
                marker_colors=colors2,
                textfont_color=C_TEXT,
                textfont_size=12,
            ))
            fig2.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color=C_TEXT,
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=True,
                legend=dict(font_color=C_TEXT2, bgcolor="rgba(0,0,0,0)"),
                title=dict(text="Value Share", font_color=C_TEXT2, font_size=13, x=0.5),
            )
            st.plotly_chart(fig2, use_container_width=True)
    except Exception:
        logger.exception("Cargo breakdown render failed")
        st.error("Cargo breakdown unavailable.")


def _render_commodity_table() -> None:
    try:
        _section_header("Commodity-to-Shipping Routing", "20 key commodities with vessel type, transit time, and freight rate")
        header_html = (
            f'<div style="display:grid;grid-template-columns:1.4fr 1.2fr 1.2fr 1fr 0.7fr 0.8fr;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 16px;">'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;">Commodity</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;">Origin</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;">Destination</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;">Vessel Type</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;">Days</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;">Rate $/MT</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = (
            f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        )
        for i, (comm, origin, dest, vessel, days, rate) in enumerate(_COMMODITIES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1.4fr 1.2fr 1.2fr 1fr 0.7fr 0.8fr;'
                f'gap:0;background:{bg};padding:9px 16px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{comm}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT2};">{origin}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT2};">{dest}</span>'
                f'<span style="font-size:0.78rem;color:{C_ACCENT};">{vessel}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT};">{days}d</span>'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_HIGH};">${rate:,.2f}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Commodity table render failed")
        st.error("Commodity routing table unavailable.")


def _render_hazmat() -> None:
    try:
        _section_header("Dangerous Goods Tracker", "Hazmat cargo restrictions by port and carrier")
        header_html = (
            f'<div style="display:grid;grid-template-columns:1.4fr 0.8fr 0.7fr 0.7fr 1.1fr 1.4fr;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 16px;">'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Cargo</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Class</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Port</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Risk</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Carriers</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Restriction</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        risk_color = {"High": C_LOW, "Medium": C_MOD, "Low": C_HIGH}
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (cargo, cls, port, risk, carriers, restriction) in enumerate(_HAZMAT):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rc = risk_color.get(risk, C_TEXT2)
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1.4fr 0.8fr 0.7fr 0.7fr 1.1fr 1.4fr;'
                f'gap:0;background:{bg};padding:9px 16px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{cargo}</span>'
                f'<span style="font-size:0.78rem;color:{C_MOD};">{cls}</span>'
                f'<span style="font-size:0.78rem;color:{C_TEXT2};">{port}</span>'
                f'<span style="font-size:0.78rem;font-weight:700;color:{rc};">{risk}</span>'
                f'<span style="font-size:0.75rem;color:{C_TEXT2};">{carriers}</span>'
                f'<span style="font-size:0.75rem;color:{C_TEXT3};">{restriction}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Hazmat tracker render failed")
        st.error("Dangerous goods tracker unavailable.")


def _render_reefer() -> None:
    try:
        _section_header("Reefer Cargo Monitor", "Temperature-sensitive cargo stats, rate premiums, and top routes")
        c1, c2, c3 = st.columns(3)
        with c1:
            _kpi_card("Active Reefer Units", "1.84M TEU", "▲ 6.2% YoY", C_CYAN)
        with c2:
            _kpi_card("Avg Reefer Rate Premium", "+31%", "vs standard dry rate", C_MOD)
        with c3:
            _kpi_card("Reefer Fleet Utilisation", "87%", "▲ 3 pts vs LY", C_HIGH)
        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        header_html = (
            f'<div style="display:grid;grid-template-columns:1.8fr 1.2fr 0.8fr 0.7fr 0.8fr 0.7fr;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 16px;">'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Route</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Cargo</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Temp</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Transit</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Rate $/FEU</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Premium</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (route, cargo, temp, transit, rate, prem) in enumerate(_REEFER_ROUTES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1.8fr 1.2fr 0.8fr 0.7fr 0.8fr 0.7fr;'
                f'gap:0;background:{bg};padding:9px 16px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{route}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT2};">{cargo}</span>'
                f'<span style="font-size:0.78rem;color:{C_CYAN};font-family:monospace;">{temp}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT};">{transit}</span>'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_HIGH};">${rate:,}</span>'
                f'<span style="font-size:0.82rem;font-weight:700;color:{C_MOD};">{prem}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Reefer monitor render failed")
        st.error("Reefer monitor unavailable.")


def _render_lcl_fcl_optimizer() -> None:
    try:
        _section_header("LCL / FCL Optimizer", "Enter your cargo volume to get a cost recommendation")
        with st.container():
            c1, c2 = st.columns([1, 2])
            with c1:
                cbm = st.number_input("Cargo Volume (CBM)", min_value=1, max_value=120, value=18, step=1)
                weight_t = st.number_input("Cargo Weight (tonnes)", min_value=0.1, max_value=30.0, value=8.0, step=0.5)
                route_sel = st.selectbox("Route", ["Asia → Europe", "Asia → USA", "Europe → USA", "Intra-Asia"])

            lcl_rate_per_cbm = {"Asia → Europe": 68, "Asia → USA": 82, "Europe → USA": 55, "Intra-Asia": 38}
            fcl_20ft_rate   = {"Asia → Europe": 1650, "Asia → USA": 2100, "Europe → USA": 1400, "Intra-Asia": 950}
            fcl_40ft_rate   = {"Asia → Europe": 2400, "Asia → USA": 3200, "Europe → USA": 1900, "Intra-Asia": 1350}

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
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {rec_color};border-radius:12px;padding:20px 24px;">'
                    f'<div style="font-size:0.75rem;color:{C_TEXT3};text-transform:uppercase;margin-bottom:6px;">Recommendation</div>'
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
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception:
        logger.exception("LCL/FCL optimizer render failed")
        st.error("LCL/FCL optimizer unavailable.")


def _render_theft_tracker() -> None:
    try:
        _section_header("Cargo Theft & Loss Tracker", "High-risk routes, stolen cargo categories, and insurance implications")
        c1, c2, c3 = st.columns(3)
        with c1:
            _kpi_card("Annual Cargo Losses", "$22.4B", "Global estimate 2025", C_LOW)
        with c2:
            _kpi_card("Avg Loss per Incident", "$148K", "▲ 12% vs 2024", C_MOD)
        with c3:
            _kpi_card("Insurance Rate Impact", "+0.3–0.8%", "High-risk route surcharge", C_TEXT2)
        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
        header_html = (
            f'<div style="display:grid;grid-template-columns:1.4fr 1.2fr 0.8fr 0.6fr 1.2fr;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 16px;">'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Route</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Cargo at Risk</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Risk Level</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Incidents/Mo</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Insurance Add-on</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        risk_color = {"Very High": C_LOW, "High": "#f97316", "Medium": C_MOD, "Low": C_HIGH}
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (route, cargo, risk, incidents, insur) in enumerate(_THEFT_ROUTES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rc = risk_color.get(risk, C_TEXT2)
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1.4fr 1.2fr 0.8fr 0.6fr 1.2fr;'
                f'gap:0;background:{bg};padding:9px 16px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{route}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT2};">{cargo}</span>'
                f'<span style="font-size:0.78rem;font-weight:700;color:{rc};">{risk}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT};">{incidents}</span>'
                f'<span style="font-size:0.78rem;color:{C_TEXT3};">{insur}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Theft tracker render failed")
        st.error("Cargo theft tracker unavailable.")


def _render_equipment_balance() -> None:
    try:
        _section_header("Container Equipment Balance", "Regional surplus / deficit of empty containers (TEU units)")
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
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color=C_TEXT,
            xaxis=dict(tickfont_color=C_TEXT2, gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(
                tickfont_color=C_TEXT2,
                gridcolor="rgba(255,255,255,0.04)",
                title="TEU Surplus / Deficit",
                title_font_color=C_TEXT3,
                zeroline=True,
                zerolinecolor=C_BORDER,
                zerolinewidth=1,
            ),
            margin=dict(t=20, b=10, l=10, r=10),
            height=320,
        )
        st.plotly_chart(fig, use_container_width=True)
        legend_html = (
            f'<div style="display:flex;gap:20px;margin-top:4px;padding:0 4px;">'
            f'<span style="font-size:0.78rem;color:{C_HIGH};">&#9646; Surplus — excess empty boxes available for export</span>'
            f'<span style="font-size:0.78rem;color:{C_LOW};">&#9646; Deficit — repositioning cost pressure on importers</span>'
            f'<span style="font-size:0.78rem;color:{C_MOD};">&#9646; Balanced — within ±2,500 TEU tolerance</span>'
            f'</div>'
        )
        st.markdown(legend_html, unsafe_allow_html=True)
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
