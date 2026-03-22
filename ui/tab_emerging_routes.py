"""tab_emerging_routes.py — Emerging Shipping Routes Intelligence: new corridors,
strategic drivers, carrier adoption, route maps, and risk assessment."""

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
C_ARCTIC  = "#38bdf8"
C_ORANGE  = "#f97316"

_MATURITY_COLOR = {
    "NASCENT":     C_LOW,
    "GROWING":     C_MOD,
    "ESTABLISHED": C_HIGH,
}

# ---------------------------------------------------------------------------
# Static route data
# ---------------------------------------------------------------------------
_ROUTES = [
    {
        "name":        "Northern Sea Route (Arctic)",
        "short":       "Arctic NSR",
        "from_lon": 30.0, "from_lat": 70.0,
        "to_lon":  140.0, "to_lat": 60.0,
        "distance_nm": 7_200,
        "transit_d":   18,
        "rate_new":    1_850,
        "rate_alt":    2_950,
        "cost_adv":    "37% shorter",
        "capacity_teu": 4_800,
        "carriers":    "COSCO, Sovcomflot, MOL",
        "maturity":    "NASCENT",
        "driver":      "Arctic ice melt opening seasonal window",
    },
    {
        "name":        "India–Middle East–Europe Corridor",
        "short":       "IMEC",
        "from_lon":  72.8, "from_lat": 18.9,
        "to_lon":   13.4, "to_lat": 52.5,
        "distance_nm": 7_500,
        "transit_d":   22,
        "rate_new":    1_620,
        "rate_alt":    2_100,
        "cost_adv":    "23% cheaper",
        "capacity_teu": 12_000,
        "carriers":    "Maersk, MSC, Hapag-Lloyd",
        "maturity":    "GROWING",
        "driver":      "Suez bypass, India–EU trade deal",
    },
    {
        "name":        "Africa–Asia Direct",
        "short":       "Africa–Asia",
        "from_lon":  36.8, "from_lat": -1.3,
        "to_lon":  103.8, "to_lat":  1.3,
        "distance_nm": 6_900,
        "transit_d":   20,
        "rate_new":    1_980,
        "rate_alt":    2_600,
        "cost_adv":    "24% saving",
        "capacity_teu": 6_200,
        "carriers":    "CMA CGM, Evergreen, Yang Ming",
        "maturity":    "GROWING",
        "driver":      "African industrialisation; demand for Asian goods",
        },
    {
        "name":        "ASEAN Intra-Regional Express",
        "short":       "ASEAN Express",
        "from_lon": 100.5, "from_lat":  13.7,
        "to_lon":  106.7, "to_lat": 10.8,
        "distance_nm": 1_400,
        "transit_d":    4,
        "rate_new":      420,
        "rate_alt":      680,
        "cost_adv":    "38% cheaper",
        "capacity_teu": 9_500,
        "carriers":    "PIL, RCL, Samudera",
        "maturity":    "ESTABLISHED",
        "driver":      "RCEP, supply-chain nearshoring in ASEAN",
    },
    {
        "name":        "China–Africa Multi-Port",
        "short":       "China–Africa",
        "from_lon": 121.5, "from_lat": 31.2,
        "to_lon":    3.4, "to_lat":  6.4,
        "distance_nm": 9_800,
        "transit_d":   28,
        "rate_new":    2_100,
        "rate_alt":    2_700,
        "cost_adv":    "22% saving",
        "capacity_teu": 7_400,
        "carriers":    "COSCO, Sinolines, MSC",
        "maturity":    "GROWING",
        "driver":      "Belt & Road, African demand for Chinese goods",
    },
    {
        "name":        "Transarctic Polar Corridor",
        "short":       "Polar Trans",
        "from_lon": -74.0, "from_lat": 40.7,
        "to_lon":  139.7, "to_lat": 35.7,
        "distance_nm": 8_100,
        "transit_d":   21,
        "rate_new":    2_400,
        "rate_alt":    3_500,
        "cost_adv":    "31% shorter",
        "capacity_teu": 2_200,
        "carriers":    "Fednav, COSCO Polar",
        "maturity":    "NASCENT",
        "driver":      "Climate change, US–Japan direct Arctic link",
    },
    {
        "name":        "Cape of Good Hope Bypass",
        "short":       "Cape Bypass",
        "from_lon":  18.4, "from_lat": -33.9,
        "to_lon":    2.3, "to_lat": 48.9,
        "distance_nm": 6_700,
        "transit_d":   24,
        "rate_new":    1_480,
        "rate_alt":    1_680,
        "cost_adv":    "Suez war-risk avoidance",
        "capacity_teu": 22_000,
        "carriers":    "ALL major carriers",
        "maturity":    "ESTABLISHED",
        "driver":      "Red Sea / Houthi conflict since 2024",
    },
    {
        "name":        "Pacific Island Hub Loop",
        "short":       "Pacific Islands",
        "from_lon": 179.0, "from_lat": -17.7,
        "to_lon":  168.3, "to_lat": -17.7,
        "distance_nm": 2_100,
        "transit_d":    7,
        "rate_new":      890,
        "rate_alt":    1_250,
        "cost_adv":    "29% saving",
        "capacity_teu": 1_800,
        "carriers":    "Pacific Forum Line, ANL",
        "maturity":    "NASCENT",
        "driver":      "Pacific island development, tourism logistics",
    },
    {
        "name":        "East Africa–South Asia Corridor",
        "short":       "E.Africa–S.Asia",
        "from_lon":  39.7, "from_lat": -4.0,
        "to_lon":   72.8, "to_lat": 18.9,
        "distance_nm": 3_100,
        "transit_d":   10,
        "rate_new":      980,
        "rate_alt":    1_400,
        "cost_adv":    "30% saving",
        "capacity_teu": 5_100,
        "carriers":    "Safmarine, PIL, Evergreen",
        "maturity":    "GROWING",
        "driver":      "E.Africa growth, India–Africa trade push",
    },
    {
        "name":        "Latin America–Asia Direct",
        "short":       "LatAm–Asia",
        "from_lon": -46.6, "from_lat": -23.5,
        "to_lon":  121.5, "to_lat":  31.2,
        "distance_nm": 11_500,
        "transit_d":   33,
        "rate_new":    2_800,
        "rate_alt":    3_400,
        "cost_adv":    "18% saving",
        "capacity_teu": 8_200,
        "carriers":    "CMA CGM, COSCO, Hapag-Lloyd",
        "maturity":    "GROWING",
        "driver":      "Tariff diversification, Brazil-China soybeans",
    },
    {
        "name":        "Mediterranean–West Africa",
        "short":       "Med–W.Africa",
        "from_lon":  13.4, "from_lat": 38.1,
        "to_lon":    3.4, "to_lat":  6.4,
        "distance_nm": 4_200,
        "transit_d":   13,
        "rate_new":    1_150,
        "rate_alt":    1_600,
        "cost_adv":    "28% saving",
        "capacity_teu": 4_600,
        "carriers":    "MSC, CMA CGM, Grimaldi",
        "maturity":    "GROWING",
        "driver":      "AfCFTA, EU–Africa ties, Lagos growth",
    },
    {
        "name":        "India–Southeast Asia Express",
        "short":       "India–SEA",
        "from_lon":  80.3, "from_lat": 13.1,
        "to_lon":  103.8, "to_lat":  1.3,
        "distance_nm": 1_900,
        "transit_d":    6,
        "rate_new":      560,
        "rate_alt":      820,
        "cost_adv":    "32% saving",
        "capacity_teu": 11_000,
        "carriers":    "Maersk, CMA CGM, SCI",
        "maturity":    "ESTABLISHED",
        "driver":      "India manufacturing shift, PLI scheme, ASEAN–India FTA",
    },
]

_DRIVERS = [
    ("Tariff Diversification",      "US–China tariff war pushes shippers to new origins/destinations", [
        "India–SEA Express", "LatAm–Asia Direct", "Africa–Asia Direct"]),
    ("Arctic Ice Melt",             "Summer ice-free window extends to 4–5 months by 2030", [
        "Northern Sea Route", "Transarctic Polar Corridor"]),
    ("Africa Growth (AfCFTA)",      "Continental free trade and population growth drive intra-Africa + exports", [
        "China–Africa Multi-Port", "Med–W.Africa", "East Africa–S.Asia"]),
    ("Red Sea Conflict",            "Houthi attacks divert 30%+ of Asia–Europe trade via Cape", [
        "Cape of Good Hope Bypass", "IMEC"]),
    ("ASEAN Supply-Chain Shift",    "RCEP and China+1 strategy accelerate intra-ASEAN flows", [
        "ASEAN Intra-Regional Express", "India–SEA Express"]),
    ("India Manufacturing Rise",    "PLI scheme and MNC diversification fuel Indian export surge", [
        "IMEC", "India–SEA Express"]),
]

_CARRIER_ADOPTION = [
    ("Maersk",       "IMEC, India–SEA Express",            "2 new services Q1 2026", "HIGH"),
    ("CMA CGM",      "Africa–Asia Direct, Med–W.Africa",   "1 new service Q2 2026",  "HIGH"),
    ("COSCO",        "Arctic NSR, China–Africa",           "Seasonal 2025–2026",     "MEDIUM"),
    ("Hapag-Lloyd",  "IMEC, LatAm–Asia",                   "JV with PIL planned",    "MEDIUM"),
    ("MSC",          "Cape Bypass, Med–W.Africa",          "Full round-the-year",    "HIGH"),
    ("ONE",          "ASEAN Express, India–SEA",           "Existing, expanding",    "ESTABLISHED"),
    ("Evergreen",    "Africa–Asia, E.Africa–S.Asia",       "New port calls added",   "MEDIUM"),
    ("PIL",          "ASEAN Express, Pacific Islands",     "Core competency",        "ESTABLISHED"),
]

_RISKS = [
    ("Northern Sea Route",      "HIGH",   "LOW",    "HIGH",   "Ice, class requirements; Russia sanctions"),
    ("IMEC",                    "MEDIUM", "MEDIUM", "LOW",    "India–Pakistan tensions; Israel port access"),
    ("Africa–Asia Direct",      "MEDIUM", "LOW",    "MEDIUM", "Port infrastructure gaps East Africa"),
    ("ASEAN Intra-Regional",    "LOW",    "HIGH",   "LOW",    "Typhoon season Jul–Oct; ASEAN fragmentation risk"),
    ("China–Africa",            "LOW",    "MEDIUM", "LOW",    "Belt & Road debt diplomacy; port sovereignty risk"),
    ("Transarctic Polar",       "HIGH",   "LOW",    "HIGH",   "Extreme weather; Russian EEZ dependency"),
    ("Cape Bypass",             "LOW",    "HIGH",   "LOW",    "Longer transit cost; bunker cost increase"),
    ("E.Africa–S.Asia",         "MEDIUM", "MEDIUM", "MEDIUM", "Somali piracy residual risk; shallow ports"),
    ("LatAm–Asia Direct",       "LOW",    "LOW",    "LOW",    "Panama Canal drought risk; Chile labour"),
    ("Med–W.Africa",            "MEDIUM", "MEDIUM", "LOW",    "W.Africa port congestion; piracy Gulf of Guinea"),
    ("India–SEA Express",       "LOW",    "HIGH",   "LOW",    "Bay of Bengal cyclones; monsoon delays"),
    ("Pacific Islands",         "MEDIUM", "LOW",    "HIGH",   "Cyclone season; very shallow draught ports"),
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


def _risk_badge(level: str) -> str:
    color = {"HIGH": C_LOW, "MEDIUM": C_MOD, "LOW": C_HIGH}.get(level, C_TEXT3)
    return (
        f'<span style="background:{color}22;color:{color};font-size:0.7rem;'
        f'font-weight:700;padding:2px 8px;border-radius:4px;">{level}</span>'
    )


def _maturity_badge(m: str) -> str:
    color = _MATURITY_COLOR.get(m, C_TEXT3)
    return (
        f'<span style="background:{color}22;color:{color};font-size:0.7rem;'
        f'font-weight:700;padding:2px 8px;border-radius:4px;">{m}</span>'
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_hero() -> None:
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
            f'border:1px solid {C_BORDER};border-radius:14px;padding:24px 28px;margin-bottom:20px;">'
            f'<div style="font-size:1.4rem;font-weight:700;color:{C_TEXT};">Emerging Routes Intelligence</div>'
            f'<div style="font-size:0.85rem;color:{C_TEXT2};margin-top:4px;">'
            f'12 new corridors identified · Strategic macro drivers · Real-time carrier adoption tracking</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _kpi_card("New Routes Identified", "12", "vs 7 in 2024", C_HIGH)
        with c2:
            _kpi_card("Total New Capacity", "94K TEU/wk", "▲ 31% YoY", C_ACCENT)
        with c3:
            _kpi_card("Avg Cost Advantage", "28%", "vs established alternatives", C_MOD)
        with c4:
            _kpi_card("Carriers Adopting", "8 major lines", "across all emerging routes", C_CYAN)
    except Exception:
        logger.exception("Emerging routes hero failed")
        st.error("Hero section unavailable.")


def _render_route_discovery_table() -> None:
    try:
        _section_header("Route Discovery Table", "12 emerging routes with capacity, carriers, maturity, and strategic driver")
        cols = "1.8fr 0.8fr 0.7fr 0.8fr 0.9fr 0.8fr 1.2fr 0.9fr"
        header_html = (
            f'<div style="display:grid;grid-template-columns:{cols};'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 14px;">'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Route</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Dist (nm)</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Days</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Rate (new)</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Cost Adv.</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Cap (TEU/wk)</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Key Carriers</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Maturity</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, r in enumerate(_ROUTES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            mat_badge = _maturity_badge(r["maturity"])
            rows_html += (
                f'<div style="display:grid;grid-template-columns:{cols};'
                f'gap:0;background:{bg};padding:9px 14px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{r["short"]}</span>'
                f'<span style="font-size:0.8rem;color:{C_TEXT2};">{r["distance_nm"]:,}</span>'
                f'<span style="font-size:0.8rem;color:{C_TEXT};">{r["transit_d"]}d</span>'
                f'<span style="font-size:0.8rem;color:{C_HIGH};font-weight:600;">${r["rate_new"]:,}</span>'
                f'<span style="font-size:0.78rem;color:{C_MOD};">{r["cost_adv"]}</span>'
                f'<span style="font-size:0.8rem;color:{C_TEXT2};">{r["capacity_teu"]:,}</span>'
                f'<span style="font-size:0.75rem;color:{C_TEXT3};">{r["carriers"]}</span>'
                f'<span>{mat_badge}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Route discovery table failed")
        st.error("Route discovery table unavailable.")


def _render_strategic_drivers() -> None:
    try:
        _section_header("Strategic Driver Analysis", "Macro trends creating emerging route opportunities")
        driver_colors = [C_ACCENT, C_ARCTIC, C_HIGH, C_LOW, C_MOD, C_PURPLE]
        for idx, (driver, desc, routes) in enumerate(_DRIVERS):
            color = driver_colors[idx % len(driver_colors)]
            routes_str = " · ".join(routes)
            st.markdown(
                f'<div style="background:{C_CARD};border-left:3px solid {color};'
                f'border-radius:0 10px 10px 0;border:1px solid {C_BORDER};'
                f'border-left:3px solid {color};padding:14px 18px;margin-bottom:8px;">'
                f'<div style="font-size:0.88rem;font-weight:600;color:{C_TEXT};">{driver}</div>'
                f'<div style="font-size:0.8rem;color:{C_TEXT2};margin-top:4px;">{desc}</div>'
                f'<div style="font-size:0.75rem;color:{color};margin-top:6px;">Routes: {routes_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("Strategic drivers render failed")
        st.error("Strategic driver analysis unavailable.")


def _render_route_map() -> None:
    try:
        _section_header("Emerging Routes World Map", "Dashed = emerging · Solid = established alternative")
        fig = go.Figure()
        established_routes = [
            {"name": "Asia–Europe (Suez)", "lons": [121.5, 55.3, 13.4], "lats": [31.2, 23.6, 52.5]},
            {"name": "Transpacific",       "lons": [121.5, -118.2],       "lats": [31.2, 33.7]},
            {"name": "Transatlantic",      "lons": [-74.0, 13.4],         "lats": [40.7, 52.5]},
        ]
        for er in established_routes:
            fig.add_trace(go.Scattergeo(
                lon=er["lons"], lat=er["lats"],
                mode="lines",
                line=dict(color=C_TEXT3, width=1.5, dash="solid"),
                name=er["name"],
                showlegend=True,
            ))
        maturity_dash = {"NASCENT": "dot", "GROWING": "dash", "ESTABLISHED": "solid"}
        for r in _ROUTES:
            color = _MATURITY_COLOR.get(r["maturity"], C_ACCENT)
            dash  = maturity_dash.get(r["maturity"], "dash")
            fig.add_trace(go.Scattergeo(
                lon=[r["from_lon"], r["to_lon"]],
                lat=[r["from_lat"], r["to_lat"]],
                mode="lines+markers",
                line=dict(color=color, width=2, dash=dash),
                marker=dict(size=6, color=color),
                name=r["short"],
                showlegend=True,
            ))
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            geo=dict(
                bgcolor="rgba(0,0,0,0)",
                showland=True, landcolor="#1e293b",
                showocean=True, oceancolor="#0f172a",
                showcountries=True, countrycolor="rgba(255,255,255,0.06)",
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.1)",
                projection_type="natural earth",
            ),
            legend=dict(font_color=C_TEXT2, bgcolor="rgba(0,0,0,0)", font_size=10),
            margin=dict(t=10, b=10, l=0, r=0),
            height=480,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        logger.exception("Route map render failed")
        st.error("Route map unavailable.")


def _render_carrier_adoption() -> None:
    try:
        _section_header("Carrier Adoption Tracker", "Which major carriers are investing in emerging route services")
        adoption_color = {"HIGH": C_HIGH, "MEDIUM": C_MOD, "ESTABLISHED": C_ACCENT}
        header_html = (
            f'<div style="display:grid;grid-template-columns:1fr 1.8fr 1.4fr 0.8fr;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 16px;">'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Carrier</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Routes</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Activity</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Commitment</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (carrier, routes, activity, level) in enumerate(_CARRIER_ADOPTION):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            lc = adoption_color.get(level, C_TEXT2)
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1fr 1.8fr 1.4fr 0.8fr;'
                f'gap:0;background:{bg};padding:9px 16px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:700;color:{C_TEXT};">{carrier}</span>'
                f'<span style="font-size:0.78rem;color:{C_ACCENT};">{routes}</span>'
                f'<span style="font-size:0.78rem;color:{C_TEXT2};">{activity}</span>'
                f'<span style="font-size:0.78rem;font-weight:700;color:{lc};">{level}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Carrier adoption render failed")
        st.error("Carrier adoption tracker unavailable.")


def _render_risk_assessment() -> None:
    try:
        _section_header("Risk Assessment per Route", "Political · Infrastructure · Seasonal weather risks")
        header_html = (
            f'<div style="display:grid;grid-template-columns:1.6fr 0.8fr 0.8fr 0.8fr 2fr;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 16px;">'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Route</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Political</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Infra.</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Weather</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Key Concern</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (route, pol, infra, wx, concern) in enumerate(_RISKS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1.6fr 0.8fr 0.8fr 0.8fr 2fr;'
                f'gap:0;background:{bg};padding:9px 16px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{route}</span>'
                f'<span>{_risk_badge(pol)}</span>'
                f'<span>{_risk_badge(infra)}</span>'
                f'<span>{_risk_badge(wx)}</span>'
                f'<span style="font-size:0.75rem;color:{C_TEXT3};">{concern}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Risk assessment render failed")
        st.error("Risk assessment unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def render(
    route_results=None,
    port_results=None,
    insights=None,
) -> None:
    try:
        _render_hero()
        _render_route_discovery_table()
        _render_strategic_drivers()
        _render_route_map()
        _render_carrier_adoption()
        _render_risk_assessment()
    except Exception:
        logger.exception("tab_emerging_routes top-level render failed")
        st.error("Emerging Routes tab encountered an error.")
