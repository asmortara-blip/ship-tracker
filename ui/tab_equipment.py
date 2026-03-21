"""tab_equipment.py — Container Equipment Tracking tab.

Renders a comprehensive, professional view of global container equipment:
  • Global TEU pool KPIs and utilization overview
  • Container shortage/surplus map by region
  • Repositioning cost by route
  • Equipment turn time (dwell time) by port
  • Reefer availability by region
  • Equipment shortage alert system
  • Container age distribution and fleet replacement needs
  • Leasing vs owned equipment economics

Function signature: render(route_results, freight_data, macro_data) -> None
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from processing.equipment_tracker import (
    CONTAINER_TYPES,
    REGIONS,
    REGIONAL_EQUIPMENT_STATUS,
    TRADE_IMBALANCE_DATA,
    EquipmentStatus,
    compute_equipment_adjusted_rate,
    get_global_equipment_index,
    get_reefer_summary,
    get_trade_imbalance,
)
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    RISK_COLORS,
    dark_layout,
    section_header,
)

# ── Palette ────────────────────────────────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_GREEN   = "#10b981"
_C_RED     = "#ef4444"
_C_AMBER   = "#f59e0b"
_C_BLUE    = "#3b82f6"
_C_PURPLE  = "#8b5cf6"
_C_CYAN    = "#06b6d4"
_C_TEAL    = "#14b8a6"
_C_INDIGO  = "#6366f1"
_C_ROSE    = "#f43f5e"
_C_ORANGE  = "#f97316"

# Heatmap color scale: green (surplus) → amber → red (critical)
_UTIL_COLORSCALE = [
    [0.00, "#064e3b"],
    [0.40, "#10b981"],
    [0.60, "#f59e0b"],
    [0.80, "#ef4444"],
    [1.00, "#7f1d1d"],
]

_RISK_COLOR: Dict[str, str] = {
    "CRITICAL": "#b91c1c",
    "HIGH":     "#ef4444",
    "MODERATE": "#f59e0b",
    "LOW":      "#10b981",
}

_TYPE_LABELS: Dict[str, str] = {
    "20FT_DRY":    "20ft Dry",
    "40FT_DRY":    "40ft Dry",
    "40FT_HC":     "40ft HC",
    "40FT_REEFER": "40ft Reefer",
    "20FT_TANK":   "20ft Tank",
}

_REGION_COLORS: Dict[str, str] = {
    "Asia Pacific":  _C_BLUE,
    "North America": _C_GREEN,
    "Europe":        _C_PURPLE,
    "South America": _C_AMBER,
    "Middle East":   _C_CYAN,
    "Africa":        _C_RED,
}

# ── Static data sets ───────────────────────────────────────────────────────

# Global TEU pool totals (millions TEU, 2026 estimates; Alphaliner / Drewry)
_GLOBAL_TEU_POOL: Dict[str, Any] = {
    "total_teu_m":       25.2,   # million TEU in world fleet
    "owned_pct":         52.0,   # % fleet owned by carriers
    "leased_pct":        48.0,   # % fleet leased from lessors
    "active_pct":        74.5,   # % of fleet in active service
    "idle_pct":          8.2,    # % idle / awaiting deployment
    "repositioning_pct": 17.3,   # % in empty repositioning transit
    "yoy_fleet_growth":  3.8,    # fleet TEU growth YoY %
    "newbuild_delivery_m": 1.8,  # newbuild deliveries this year (million TEU)
    "scrapping_m":       0.4,    # units scrapped this year (million TEU)
}

# Equipment turn time (average dwell days) at major ports — 2025/2026 baseline
# Source: Port productivity benchmarks — UNCTAD, Drewry Port Tariff Monitor
_PORT_DWELL_TIMES: List[Dict[str, Any]] = [
    {"port": "Shanghai",        "region": "Asia Pacific",  "dwell_days": 3.2,  "trend": "stable",    "vs_avg": -22},
    {"port": "Singapore",       "region": "Asia Pacific",  "dwell_days": 2.8,  "trend": "improving", "vs_avg": -32},
    {"port": "Busan",           "region": "Asia Pacific",  "dwell_days": 3.5,  "trend": "stable",    "vs_avg": -15},
    {"port": "Ningbo",          "region": "Asia Pacific",  "dwell_days": 3.8,  "trend": "stable",    "vs_avg": -8},
    {"port": "Hong Kong",       "region": "Asia Pacific",  "dwell_days": 4.1,  "trend": "worsening", "vs_avg": -1},
    {"port": "Rotterdam",       "region": "Europe",        "dwell_days": 4.6,  "trend": "stable",    "vs_avg": +11},
    {"port": "Antwerp",         "region": "Europe",        "dwell_days": 5.2,  "trend": "worsening", "vs_avg": +25},
    {"port": "Hamburg",         "region": "Europe",        "dwell_days": 5.8,  "trend": "worsening", "vs_avg": +40},
    {"port": "Felixstowe",      "region": "Europe",        "dwell_days": 6.1,  "trend": "worsening", "vs_avg": +47},
    {"port": "Los Angeles",     "region": "North America", "dwell_days": 5.4,  "trend": "improving", "vs_avg": +30},
    {"port": "Long Beach",      "region": "North America", "dwell_days": 5.1,  "trend": "improving", "vs_avg": +24},
    {"port": "New York",        "region": "North America", "dwell_days": 6.8,  "trend": "worsening", "vs_avg": +64},
    {"port": "Savannah",        "region": "North America", "dwell_days": 5.9,  "trend": "stable",    "vs_avg": +43},
    {"port": "Santos",          "region": "South America", "dwell_days": 7.4,  "trend": "worsening", "vs_avg": +79},
    {"port": "Buenos Aires",    "region": "South America", "dwell_days": 8.2,  "trend": "worsening", "vs_avg": +98},
    {"port": "Jebel Ali",       "region": "Middle East",   "dwell_days": 3.0,  "trend": "improving", "vs_avg": -27},
    {"port": "King Abdullah",   "region": "Middle East",   "dwell_days": 3.4,  "trend": "stable",    "vs_avg": -18},
    {"port": "Durban",          "region": "Africa",        "dwell_days": 9.6,  "trend": "worsening", "vs_avg": +132},
    {"port": "Mombasa",         "region": "Africa",        "dwell_days": 11.2, "trend": "worsening", "vs_avg": +171},
    {"port": "Tanger Med",      "region": "Africa",        "dwell_days": 4.2,  "trend": "stable",    "vs_avg": +1},
]
_PORT_GLOBAL_AVG_DWELL = 4.15  # global simple average dwell days

# Container fleet age distribution (% of global fleet by age bracket)
# Source: BRS Alphaliner Fleet Database 2025
_FLEET_AGE_DIST: List[Dict[str, Any]] = [
    {"bracket": "0–5 yrs",   "pct": 28.5, "status": "New",     "color": _C_GREEN,  "note": "Post-2020 newbuild surge"},
    {"bracket": "5–10 yrs",  "pct": 22.0, "status": "Prime",   "color": _C_TEAL,   "note": "Peak productivity"},
    {"bracket": "10–15 yrs", "pct": 19.5, "status": "Mid-life","color": _C_BLUE,   "note": "Approaching major survey"},
    {"bracket": "15–20 yrs", "pct": 16.0, "status": "Aging",   "color": _C_AMBER,  "note": "Maintenance costs rising"},
    {"bracket": "20–25 yrs", "pct": 9.5,  "status": "Old",     "color": _C_ORANGE, "note": "Replacement candidates"},
    {"bracket": "25+ yrs",   "pct": 4.5,  "status": "EOL",     "color": _C_RED,    "note": "End-of-life / scrapping"},
]

# Leasing vs owned economics by container type (2026 market rates)
# Owned: capex-based; Leased: operating cost-based
_LEASE_VS_OWN: List[Dict[str, Any]] = [
    {
        "type":          "20ft Dry",
        "own_capex_usd": 2_200,   # new unit purchase price
        "own_daily_usd": 0.55,    # implied daily cost (15yr depreciation + maintenance)
        "lease_daily":   0.78,    # current market daily hire
        "lease_premium": 42,      # % premium to lease vs own
        "breakeven_yrs": 4.0,     # years to break even owning vs leasing
        "market_trend":  "Lease rates softening — post-surge oversupply",
    },
    {
        "type":          "40ft Dry",
        "own_capex_usd": 3_800,
        "own_daily_usd": 0.72,
        "lease_daily":   1.05,
        "lease_premium": 46,
        "breakeven_yrs": 4.2,
        "market_trend":  "Lease rates near 3-year low",
    },
    {
        "type":          "40ft HC",
        "own_capex_usd": 4_100,
        "own_daily_usd": 0.78,
        "lease_daily":   1.15,
        "lease_premium": 47,
        "breakeven_yrs": 4.3,
        "market_trend":  "HC preferred for e-commerce — lease demand firm",
    },
    {
        "type":          "40ft Reefer",
        "own_capex_usd": 28_000,
        "own_daily_usd": 4.20,    # includes power/maintenance
        "lease_daily":   3.55,    # current market daily hire
        "lease_premium": -15,     # leasing is CHEAPER due to structural oversupply risk
        "breakeven_yrs": 7.5,
        "market_trend":  "Leasing preferred — reefer build costs high, utilisation volatile",
    },
    {
        "type":          "20ft Tank",
        "own_capex_usd": 18_000,
        "own_daily_usd": 2.85,
        "lease_daily":   2.20,
        "lease_premium": -23,
        "breakeven_yrs": 9.0,
        "market_trend":  "Specialised units favour leasing — fleet flexibility key",
    },
]

# Historical equipment balance index (0 = severe shortage, 100 = large surplus)
_BALANCE_TIMELINE: Dict[str, Any] = {
    "years": [2020, 2021, 2022, 2023, 2024, 2025, 2026],
    "Asia Pacific":  [65, 28, 42, 58, 72, 75, 76],
    "North America": [60, 22, 35, 48, 55, 52, 50],
    "Europe":        [62, 25, 38, 52, 60, 62, 63],
    "South America": [58, 20, 32, 45, 52, 54, 55],
    "Middle East":   [62, 30, 44, 56, 64, 66, 67],
    "Africa":        [55, 18, 30, 42, 50, 52, 53],
}

# Reefer seasonal demand index (100 = annual average)
_REEFER_SEASONAL: Dict[str, Any] = {
    "labels":        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"],
    "Global":        [92, 88, 95, 105, 118, 120, 115, 110, 108, 102, 95, 90],
    "South America": [130, 125, 120, 110, 95, 85, 80, 85, 95, 110, 125, 135],
    "Europe":        [85, 82, 90, 102, 118, 125, 130, 128, 112, 95, 85, 82],
    "Asia Pacific":  [88, 110, 95, 92, 95, 100, 108, 115, 118, 112, 100, 92],
    "North America": [88, 85, 90, 95, 102, 105, 108, 118, 125, 130, 115, 95],
}

# Top reefer commodities
_REEFER_COMMODITIES: List[Dict[str, Any]] = [
    {"name": "Bananas",          "share_pct": 22, "peak_months": "Oct–Mar peak",    "key_origins": "Ecuador, Colombia, Costa Rica", "color": _C_AMBER},
    {"name": "Meat & Poultry",   "share_pct": 18, "peak_months": "Nov–Jan",         "key_origins": "Brazil, Australia, USA",        "color": _C_RED},
    {"name": "Avocados",         "share_pct": 10, "peak_months": "Mar–Aug",         "key_origins": "Mexico, Peru, South Africa",    "color": _C_GREEN},
    {"name": "Pharmaceuticals",  "share_pct":  9, "peak_months": "Stable",          "key_origins": "Europe, India, USA",            "color": _C_BLUE},
    {"name": "Citrus Fruit",     "share_pct":  8, "peak_months": "Apr–Sep",         "key_origins": "South Africa, Spain, Argentina","color": _C_ORANGE},
    {"name": "Seafood",          "share_pct": 10, "peak_months": "Oct–Dec",         "key_origins": "Norway, Chile, Vietnam",        "color": _C_CYAN},
    {"name": "Wine & Beer",      "share_pct":  6, "peak_months": "Sep–Dec",         "key_origins": "France, Australia, Chile",      "color": _C_PURPLE},
    {"name": "Other Perishables","share_pct": 17, "peak_months": "Variable",        "key_origins": "Global",                        "color": C_TEXT3},
]


# ── Utility helpers ────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> str:
    """Convert #rrggbb to 'r,g,b' string."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"{r},{g},{b}"


def _risk_badge(risk: str) -> str:
    color = _RISK_COLOR.get(risk, C_TEXT2)
    rgb = _hex_to_rgb(color)
    return (
        f"<span style='display:inline-block;padding:2px 9px;border-radius:999px;"
        f"font-size:0.65rem;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:0.05em;"
        f"background:rgba({rgb},0.18);color:{color};"
        f"border:1px solid rgba({rgb},0.40);'>{risk}</span>"
    )


def _trend_badge(trend: str) -> str:
    cfg = {
        "improving": ("↗", _C_GREEN),
        "stable":    ("→", _C_AMBER),
        "worsening": ("↘", _C_RED),
    }
    arrow, color = cfg.get(trend, ("–", C_TEXT3))
    rgb = _hex_to_rgb(color)
    return (
        f"<span style='display:inline-block;padding:2px 8px;border-radius:999px;"
        f"font-size:0.65rem;font-weight:700;"
        f"background:rgba({rgb},0.15);color:{color};"
        f"border:1px solid rgba({rgb},0.35);'>{arrow} {trend.title()}</span>"
    )


def _kpi_card(label: str, value: str, subtitle: str, color: str, icon: str = "") -> str:
    rgb = _hex_to_rgb(color)
    icon_html = f"<div style='font-size:1.4rem;margin-bottom:6px;'>{icon}</div>" if icon else ""
    return (
        f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-top:3px solid {color};border-radius:12px;"
        f"padding:20px 16px;text-align:center;height:100%;'>"
        f"{icon_html}"
        f"<div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.08em;margin-bottom:8px;'>{label}</div>"
        f"<div style='font-size:1.9rem;font-weight:800;color:{C_TEXT};"
        f"line-height:1.1;'>{value}</div>"
        f"<div style='font-size:0.72rem;color:{C_TEXT3};margin-top:6px;'>{subtitle}</div>"
        f"</div>"
    )


def _section_divider() -> None:
    st.markdown(
        "<div style='margin:28px 0;border-top:1px solid rgba(255,255,255,0.06);'></div>",
        unsafe_allow_html=True,
    )


def _build_equip_matrix() -> Tuple[List, List, List]:
    z_util: List[List[float]] = []
    z_text: List[List[str]] = []
    z_risk: List[List[str]] = []
    idx = {(e.region, e.container_type): e for e in REGIONAL_EQUIPMENT_STATUS}
    for region in REGIONS:
        row_u, row_t, row_r = [], [], []
        for ctype in CONTAINER_TYPES:
            equip = idx.get((region, ctype))
            if equip:
                row_u.append(equip.utilization_pct)
                row_t.append(f"{int(equip.utilization_pct)}%\n{equip.shortage_risk}")
                row_r.append(equip.shortage_risk)
            else:
                row_u.append(0.0)
                row_t.append("N/A")
                row_r.append("LOW")
        z_util.append(row_u)
        z_text.append(row_t)
        z_risk.append(row_r)
    return z_util, z_text, z_risk


# ══════════════════════════════════════════════════════════════════════════════
#  NEW SECTION 0A — Enhanced Equipment Overview (KPI hero + geo map + alerts)
# ══════════════════════════════════════════════════════════════════════════════

# Geo scatter data: equipment balance by region (positive = surplus, negative = shortage)
_GEO_BALANCE: List[Dict[str, Any]] = [
    {"region": "Asia Pacific",  "lat": 25.0,  "lon": 115.0, "balance": +42,  "util": 76, "risk": "LOW"},
    {"region": "North America", "lat": 40.0,  "lon": -100.0,"balance": -18,  "util": 81, "risk": "MODERATE"},
    {"region": "Europe",        "lat": 51.0,  "lon": 10.0,  "balance": +12,  "util": 78, "risk": "LOW"},
    {"region": "South America", "lat": -20.0, "lon": -60.0, "balance": -8,   "util": 83, "risk": "MODERATE"},
    {"region": "Middle East",   "lat": 25.0,  "lon": 50.0,  "balance": +28,  "util": 72, "risk": "LOW"},
    {"region": "Africa",        "lat": 5.0,   "lon": 22.0,  "balance": -35,  "util": 91, "risk": "HIGH"},
]

# Repositioning cost by route (static enhanced dataset for the bar chart)
_REPO_COST_ROUTES: List[Dict[str, Any]] = [
    {"route": "Trans-Pacific WB (US→Asia)",      "cost_feu": 620, "days": 22, "risk": "HIGH"},
    {"route": "Africa Inbound (Eu→Africa)",       "cost_feu": 540, "days": 28, "risk": "HIGH"},
    {"route": "South Am → Asia",                  "cost_feu": 480, "days": 32, "risk": "HIGH"},
    {"route": "Trans-Atlantic WB (US→Europe)",    "cost_feu": 380, "days": 18, "risk": "MODERATE"},
    {"route": "Asia-Europe WB (Eu→Asia)",         "cost_feu": 290, "days": 28, "risk": "MODERATE"},
    {"route": "Intra-Asia Rebalancing",           "cost_feu": 195, "days": 8,  "risk": "LOW"},
    {"route": "Middle East → Asia",               "cost_feu": 160, "days": 12, "risk": "LOW"},
]

# Port dwell time (turn time) static highlights
_TURN_TIME_HIGHLIGHT: List[Dict[str, Any]] = [
    {"port": "Mombasa",      "dwell": 11.2, "risk": "CRITICAL", "region": "Africa"},
    {"port": "Buenos Aires", "dwell": 8.2,  "risk": "HIGH",     "region": "South America"},
    {"port": "Santos",       "dwell": 7.4,  "risk": "HIGH",     "region": "South America"},
    {"port": "New York",     "dwell": 6.8,  "risk": "MODERATE", "region": "North America"},
    {"port": "Felixstowe",   "dwell": 6.1,  "risk": "MODERATE", "region": "Europe"},
    {"port": "Los Angeles",  "dwell": 5.4,  "risk": "MODERATE", "region": "North America"},
    {"port": "Rotterdam",    "dwell": 4.6,  "risk": "LOW",      "region": "Europe"},
    {"port": "Singapore",    "dwell": 2.8,  "risk": "LOW",      "region": "Asia Pacific"},
]

# Shortage alert routes
_SHORTAGE_ALERT_ROUTES: List[Dict[str, Any]] = [
    {
        "route": "Africa Inbound",
        "risk": "CRITICAL",
        "util": 91,
        "shortfall_teu": 38_000,
        "rate_premium_pct": 42,
        "detail": "Structural deficit — insufficient carrier-owned fleet; lessors not expanding",
    },
    {
        "route": "South America N/B",
        "risk": "HIGH",
        "util": 87,
        "shortfall_teu": 22_000,
        "rate_premium_pct": 28,
        "detail": "Seasonal reefer competition displacing dry box availability Q1",
    },
    {
        "route": "Trans-Pacific WB",
        "risk": "HIGH",
        "util": 83,
        "shortfall_teu": 15_000,
        "rate_premium_pct": 18,
        "detail": "Imbalance empties backlog delays reposition cycle; US inland dwell elevated",
    },
    {
        "route": "North America East",
        "risk": "MODERATE",
        "util": 79,
        "shortfall_teu": 8_000,
        "rate_premium_pct": 12,
        "detail": "Port congestion at NY/NJ slowing container release cycle",
    },
]


def _render_enhanced_equipment_overview() -> None:
    """
    New pre-existing sections: KPI hero cards, scattergeo map, repositioning
    cost bar chart, turn-time table, and shortage alert panel.
    """
    # ── KPI HERO CARDS ───────────────────────────────────────────────────
    section_header(
        "Equipment Overview — Global TEU Pool Snapshot",
        "Real-time summary of worldwide container equipment: pool size, utilization, "
        "repositioning pressure, and shortage signals.",
    )

    pool = _GLOBAL_TEU_POOL

    try:
        from processing.equipment_tracker import get_global_equipment_index as _gei
        global_util = _gei()
    except Exception:
        global_util = 74.5

    reposition_count_k = round(pool["total_teu_m"] * pool["repositioning_pct"] / 100 * 1000)

    c1, c2, c3, c4 = st.columns(4)
    for col, label, value, subtitle, color, icon in [
        (c1, "Global TEU Pool",       f"{pool['total_teu_m']}M TEU", "world fleet all types",           _C_BLUE,   ""),
        (c2, "Active Utilization",    f"{global_util:.1f}%",         "loaded + in-service containers",  _C_GREEN if global_util < 80 else _C_AMBER, ""),
        (c3, "Repositioning (Empty)", f"{reposition_count_k:,}K TEU","currently in empty transit",       _C_AMBER, ""),
        (c4, "Shortage Risk Routes",  str(sum(1 for a in _SHORTAGE_ALERT_ROUTES if a["risk"] in ("CRITICAL","HIGH"))),
                                      "CRITICAL or HIGH shortage",    _C_RED,    ""),
    ]:
        with col:
            st.markdown(_kpi_card(label, value, subtitle, color, icon), unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── SCATTERGEO — equipment balance by region ──────────────────────────
    section_header(
        "Container Equipment Balance Map",
        "Regions colored by equipment surplus (green) or deficit (red). "
        "Bubble size = magnitude of imbalance. Hover for utilization and risk level.",
    )

    geo_colors = []
    geo_sizes  = []
    for g in _GEO_BALANCE:
        if g["balance"] > 20:
            geo_colors.append(_C_GREEN)
        elif g["balance"] > 0:
            geo_colors.append(_C_TEAL)
        elif g["balance"] > -20:
            geo_colors.append(_C_AMBER)
        else:
            geo_colors.append(_C_RED)
        geo_sizes.append(max(16, min(55, abs(g["balance"]) * 1.2 + 14)))

    hover_texts = [
        f"<b>{g['region']}</b><br>"
        f"Balance: {'+' if g['balance']>0 else ''}{g['balance']} (index)<br>"
        f"Utilization: {g['util']}%<br>"
        f"Risk: {g['risk']}"
        for g in _GEO_BALANCE
    ]

    fig_geo = go.Figure(go.Scattergeo(
        lat=[g["lat"] for g in _GEO_BALANCE],
        lon=[g["lon"] for g in _GEO_BALANCE],
        text=[g["region"] for g in _GEO_BALANCE],
        mode="markers+text",
        textposition="top center",
        textfont=dict(color=C_TEXT, size=11, family="Inter, sans-serif"),
        marker=dict(
            size=geo_sizes,
            color=geo_colors,
            opacity=0.82,
            line=dict(color="rgba(255,255,255,0.3)", width=1.5),
        ),
        customdata=hover_texts,
        hovertemplate="%{customdata}<extra></extra>",
    ))
    fig_geo.update_layout(
        geo=dict(
            showland=True, landcolor="#111827",
            showocean=True, oceancolor="#0a0f1a",
            showlakes=False,
            showcountries=True, countrycolor="rgba(255,255,255,0.06)",
            showframe=False,
            bgcolor=_C_BG,
            projection_type="natural earth",
        ),
        paper_bgcolor=_C_BG,
        font=dict(color=C_TEXT, family="Inter, sans-serif"),
        height=380,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig_geo, use_container_width=True, key="new_equip_geo_map")

    # Color legend
    st.markdown(
        '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:-4px;margin-bottom:8px">'
        + "".join(
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<div style="width:10px;height:10px;border-radius:50%;background:{col}"></div>'
            f'<span style="font-size:0.70rem;color:{C_TEXT2}">{lbl}</span></div>'
            for col, lbl in [
                (_C_GREEN, "Large surplus (>+20)"),
                (_C_TEAL,  "Slight surplus"),
                (_C_AMBER, "Slight deficit"),
                (_C_RED,   "Large deficit (<-20)"),
            ]
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    _section_divider()

    # ── REPOSITIONING COST BAR CHART ──────────────────────────────────────
    section_header(
        "Repositioning Cost by Route",
        "Cost to move empty containers back to cargo origin. "
        "Export-heavy routes carry the highest hidden repositioning surcharge.",
    )

    repo_sorted = sorted(_REPO_COST_ROUTES, key=lambda r: r["cost_feu"], reverse=True)
    repo_colors = [
        _C_RED if r["risk"] == "HIGH" else (_C_AMBER if r["risk"] == "MODERATE" else _C_GREEN)
        for r in repo_sorted
    ]

    fig_repo2 = go.Figure(go.Bar(
        x=[r["cost_feu"] for r in repo_sorted],
        y=[r["route"] for r in repo_sorted],
        orientation="h",
        marker=dict(color=repo_colors, opacity=0.85),
        text=[f"${r['cost_feu']:,}  ({r['days']}d)" for r in repo_sorted],
        textposition="outside",
        textfont=dict(color=C_TEXT2, size=10),
        hovertemplate="<b>%{y}</b><br>Cost: $%{x:,}/FEU<extra></extra>",
    ))
    layout_rb = dark_layout(height=320, showlegend=False)
    layout_rb["xaxis"]["title"] = "Repositioning Cost (USD/FEU)"
    layout_rb["xaxis"]["tickfont"] = {"color": C_TEXT3, "size": 10}
    layout_rb["yaxis"]["tickfont"] = {"color": C_TEXT2, "size": 10}
    layout_rb["margin"] = {"l": 230, "r": 80, "t": 25, "b": 30}
    fig_repo2.update_layout(**layout_rb)
    st.plotly_chart(fig_repo2, use_container_width=True, key="new_equip_repo_cost_bar")

    _section_divider()

    # ── EQUIPMENT TURN TIME TABLE ─────────────────────────────────────────
    section_header(
        "Equipment Turn Time — Dwell Days at Major Ports",
        "Average container dwell time drives effective utilization. "
        "High dwell locks up TEUs, shrinking the available pool globally.",
    )

    col_tt_chart, col_tt_cards = st.columns([3, 2])

    with col_tt_chart:
        tt_sorted = sorted(_TURN_TIME_HIGHLIGHT, key=lambda p: p["dwell"], reverse=True)
        tt_colors = []
        for p in tt_sorted:
            if p["dwell"] >= 9:
                tt_colors.append(_C_RED)
            elif p["dwell"] >= 6.5:
                tt_colors.append(_C_AMBER)
            elif p["dwell"] >= 4.5:
                tt_colors.append(_C_BLUE)
            else:
                tt_colors.append(_C_GREEN)

        fig_tt = go.Figure(go.Bar(
            y=[p["port"] for p in tt_sorted],
            x=[p["dwell"] for p in tt_sorted],
            orientation="h",
            marker_color=tt_colors,
            marker_opacity=0.88,
            text=[f"{p['dwell']}d" for p in tt_sorted],
            textposition="outside",
            textfont={"color": C_TEXT2, "size": 10},
            hovertemplate="<b>%{y}</b><br>Dwell: %{x}d<extra></extra>",
        ))
        fig_tt.add_vline(
            x=4.15, line={"color": "rgba(255,255,255,0.3)", "dash": "dash", "width": 1.5},
            annotation_text="Global avg 4.15d",
            annotation_font={"color": C_TEXT3, "size": 10},
        )
        layout_tt = dark_layout(height=340, showlegend=False)
        layout_tt["xaxis"]["title"] = "Dwell Days"
        layout_tt["xaxis"]["tickfont"] = {"color": C_TEXT3, "size": 10}
        layout_tt["yaxis"]["tickfont"] = {"color": C_TEXT2, "size": 10}
        layout_tt["margin"] = {"l": 110, "r": 70, "t": 20, "b": 30}
        fig_tt.update_layout(**layout_tt)
        st.plotly_chart(fig_tt, use_container_width=True, key="new_equip_turntime_bar")

    with col_tt_cards:
        st.markdown(
            f"<div style='font-size:0.70rem;font-weight:700;color:{C_TEXT2};"
            f"text-transform:uppercase;letter-spacing:0.07em;margin-bottom:8px'>Port Detail</div>",
            unsafe_allow_html=True,
        )
        for p in _TURN_TIME_HIGHLIGHT:
            rc = _RISK_COLOR.get(p["risk"], C_TEXT2)
            rgb = _hex_to_rgb(rc)
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-left:3px solid {rc};border-radius:8px;"
                f"padding:8px 12px;margin-bottom:5px;'>"
                f"<div style='display:flex;justify-content:space-between;'>"
                f"<span style='font-size:0.80rem;font-weight:700;color:{C_TEXT}'>{p['port']}</span>"
                f"<span style='font-size:0.85rem;font-weight:800;color:{rc}'>{p['dwell']}d</span></div>"
                f"<div style='display:flex;justify-content:space-between;margin-top:3px;'>"
                f"<span style='font-size:0.68rem;color:{C_TEXT3}'>{p['region']}</span>"
                f"{_risk_badge(p['risk'])}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    _section_divider()

    # ── SHORTAGE ALERT PANEL ──────────────────────────────────────────────
    section_header(
        "Equipment Shortage Alert — Routes at Risk",
        "Routes highlighted in red where equipment shortages are impacting "
        "booking lead times and driving rate premiums. Act immediately on CRITICAL alerts.",
    )

    crit_alerts = [a for a in _SHORTAGE_ALERT_ROUTES if a["risk"] == "CRITICAL"]
    high_alerts = [a for a in _SHORTAGE_ALERT_ROUTES if a["risk"] == "HIGH"]

    if crit_alerts:
        desc = " · ".join(
            f"{a['route']} ({a['util']}% util, -{a['shortfall_teu']//1000}K TEU short)"
            for a in crit_alerts
        )
        st.error(f"CRITICAL: {desc} — immediate sourcing action required.", icon="🚨")
    if high_alerts:
        desc = ", ".join(a["route"] for a in high_alerts)
        st.warning(f"HIGH risk: {desc} — book within 48 hours to secure equipment.", icon="⚠️")

    alert_cols = st.columns(2)
    for i, alert in enumerate(_SHORTAGE_ALERT_ROUTES):
        col = alert_cols[i % 2]
        rc = _RISK_COLOR.get(alert["risk"], C_TEXT2)
        rgb = _hex_to_rgb(rc)
        with col:
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-left:4px solid {rc};border-radius:10px;"
                f"padding:14px 16px;margin-bottom:10px;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
                f"<span style='font-size:0.88rem;font-weight:700;color:{C_TEXT}'>{alert['route']}</span>"
                f"{_risk_badge(alert['risk'])}</div>"
                f"<div style='display:flex;gap:20px;flex-wrap:wrap;margin-bottom:8px'>"
                f"<div><div style='font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em'>Utilization</div>"
                f"<div style='font-size:1.0rem;font-weight:700;color:{rc};margin-top:2px'>{alert['util']}%</div></div>"
                f"<div><div style='font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em'>Shortfall</div>"
                f"<div style='font-size:1.0rem;font-weight:700;color:{_C_RED};margin-top:2px'>{alert['shortfall_teu']:,} TEU</div></div>"
                f"<div><div style='font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em'>Rate Premium</div>"
                f"<div style='font-size:1.0rem;font-weight:700;color:{_C_AMBER};margin-top:2px'>+{alert['rate_premium_pct']}%</div></div>"
                f"</div>"
                f"<div style='font-size:0.72rem;color:{C_TEXT2};line-height:1.45'>{alert['detail']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    _section_divider()


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — Global TEU Equipment Pool Overview
# ══════════════════════════════════════════════════════════════════════════════

def _render_global_pool_overview() -> None:
    section_header(
        "Global TEU Equipment Pool",
        "World fleet snapshot: 25.2M TEU across all container types. "
        "Active utilization, idle stock, and repositioning flows define rate pressure.",
    )

    pool = _GLOBAL_TEU_POOL
    global_idx = get_global_equipment_index()

    if global_idx >= 85:
        idx_label, idx_color = "TIGHT", _C_RED
    elif global_idx >= 70:
        idx_label, idx_color = "NORMAL", _C_AMBER
    else:
        idx_label, idx_color = "SURPLUS", _C_GREEN

    # ── Row 1: KPI cards ─────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    kpis = [
        (c1, "Total World Fleet",     f"{pool['total_teu_m']}M TEU",  "all container types",           _C_BLUE,   ""),
        (c2, "Active in Service",     f"{pool['active_pct']}%",       f"{pool['total_teu_m']*pool['active_pct']/100:.1f}M TEU loaded/moving", _C_GREEN,  ""),
        (c3, "Empty Repositioning",   f"{pool['repositioning_pct']}%","TEU currently in empty transit", _C_AMBER,  ""),
        (c4, "Idle / Awaiting",       f"{pool['idle_pct']}%",         "parked, not yet deployed",       _C_RED,    ""),
        (c5, "Weighted Utilization",  f"{global_idx}%",               idx_label,                        idx_color, ""),
    ]
    for col, label, value, subtitle, color, icon in kpis:
        with col:
            st.markdown(_kpi_card(label, value, subtitle, color, icon), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:18px;'></div>", unsafe_allow_html=True)

    # ── Row 2: Fleet composition donut + YoY metrics ─────────────────────
    col_donut, col_fleet, col_reposition = st.columns([2, 2, 3])

    with col_donut:
        # Fleet ownership donut
        fig_own = go.Figure(go.Pie(
            labels=["Carrier-Owned", "Leased from Lessors"],
            values=[pool["owned_pct"], pool["leased_pct"]],
            hole=0.62,
            marker_colors=[_C_BLUE, _C_PURPLE],
            textinfo="label+percent",
            textfont={"color": C_TEXT, "size": 11},
            hovertemplate="%{label}: %{value}%<extra></extra>",
        ))
        fig_own.add_annotation(
            text=f"<b>Fleet<br>Ownership</b>",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT2, "size": 11},
        )
        layout = dark_layout(height=230, showlegend=False)
        layout["margin"] = {"l": 10, "r": 10, "t": 20, "b": 10}
        layout["paper_bgcolor"] = C_CARD
        fig_own.update_layout(**layout)
        st.plotly_chart(fig_own, use_container_width=True, key="equip_own_donut")

    with col_fleet:
        # Fleet status donut
        fig_status = go.Figure(go.Pie(
            labels=["Active", "Repositioning Empty", "Idle"],
            values=[pool["active_pct"], pool["repositioning_pct"], pool["idle_pct"]],
            hole=0.62,
            marker_colors=[_C_GREEN, _C_AMBER, _C_RED],
            textinfo="label+percent",
            textfont={"color": C_TEXT, "size": 11},
            hovertemplate="%{label}: %{value}%<extra></extra>",
        ))
        fig_status.add_annotation(
            text="<b>Fleet<br>Status</b>",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT2, "size": 11},
        )
        layout2 = dark_layout(height=230, showlegend=False)
        layout2["margin"] = {"l": 10, "r": 10, "t": 20, "b": 10}
        layout2["paper_bgcolor"] = C_CARD
        fig_status.update_layout(**layout2)
        st.plotly_chart(fig_status, use_container_width=True, key="equip_status_donut")

    with col_reposition:
        # Repositioning need by region — bar chart using available_units_k and utilization
        reposition_data = []
        idx_map = {(e.region, e.container_type): e for e in REGIONAL_EQUIPMENT_STATUS}
        for region in REGIONS:
            surplus_k = 0.0
            deficit_k = 0.0
            for ctype in CONTAINER_TYPES:
                e = idx_map.get((region, ctype))
                if e:
                    d = e.days_surplus_deficit
                    if d > 0:
                        surplus_k += e.available_units_k * 0.01 * d
                    else:
                        deficit_k += e.available_units_k * 0.01 * abs(d)
            reposition_data.append({
                "region": region,
                "surplus": round(surplus_k, 1),
                "deficit": round(deficit_k, 1),
            })

        fig_repo = go.Figure()
        fig_repo.add_trace(go.Bar(
            y=[d["region"] for d in reposition_data],
            x=[d["surplus"] for d in reposition_data],
            name="Surplus (days supply)",
            orientation="h",
            marker_color=_C_GREEN,
            marker_opacity=0.85,
            hovertemplate="%{y}: %{x:.1f}K TEU surplus-days<extra></extra>",
        ))
        fig_repo.add_trace(go.Bar(
            y=[d["region"] for d in reposition_data],
            x=[-d["deficit"] for d in reposition_data],
            name="Deficit (days short)",
            orientation="h",
            marker_color=_C_RED,
            marker_opacity=0.85,
            hovertemplate="%{y}: %{x:.1f}K TEU deficit-days<extra></extra>",
        ))
        layout3 = dark_layout(
            title="Surplus / Deficit by Region (TEU-days index)",
            height=230,
        )
        layout3["barmode"] = "overlay"
        layout3["xaxis"]["title"] = "← Deficit  |  Surplus →"
        layout3["xaxis"]["tickfont"] = {"color": C_TEXT3, "size": 10}
        layout3["yaxis"]["tickfont"] = {"color": C_TEXT2, "size": 11}
        layout3["margin"] = {"l": 100, "r": 20, "t": 35, "b": 30}
        layout3["legend"] = {"orientation": "h", "y": -0.22, "font": {"color": C_TEXT3, "size": 10}}
        layout3["shapes"] = [{"type": "line", "x0": 0, "x1": 0, "y0": -0.5,
                               "y1": len(REGIONS) - 0.5,
                               "line": {"color": "rgba(255,255,255,0.3)", "width": 1}}]
        fig_repo.update_layout(**layout3)
        st.plotly_chart(fig_repo, use_container_width=True, key="equip_repo_bar")

    # ── Fleet growth strip ─────────────────────────────────────────────
    st.markdown(
        f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:10px;padding:14px 20px;display:flex;gap:48px;flex-wrap:wrap;'>"
        f"<div style='font-size:0.70rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>YoY Fleet Growth"
        f"<div style='font-size:1.15rem;font-weight:700;color:{_C_GREEN};margin-top:4px;'>"
        f"+{pool['yoy_fleet_growth']}%</div></div>"
        f"<div style='font-size:0.70rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Newbuild Deliveries"
        f"<div style='font-size:1.15rem;font-weight:700;color:{_C_BLUE};margin-top:4px;'>"
        f"{pool['newbuild_delivery_m']}M TEU</div></div>"
        f"<div style='font-size:0.70rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Scrappings"
        f"<div style='font-size:1.15rem;font-weight:700;color:{_C_AMBER};margin-top:4px;'>"
        f"{pool['scrapping_m']}M TEU</div></div>"
        f"<div style='font-size:0.70rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Net Fleet Addition"
        f"<div style='font-size:1.15rem;font-weight:700;color:{_C_CYAN};margin-top:4px;'>"
        f"+{pool['newbuild_delivery_m']-pool['scrapping_m']:.1f}M TEU</div></div>"
        f"<div style='font-size:0.70rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Global Utilization Index"
        f"<div style='font-size:1.15rem;font-weight:700;color:{idx_color};margin-top:4px;'>"
        f"{global_idx}% &nbsp;"
        f"<span style='display:inline-block;padding:1px 8px;border-radius:999px;"
        f"font-size:0.65rem;font-weight:700;"
        f"background:rgba({_hex_to_rgb(idx_color)},0.18);color:{idx_color};"
        f"border:1px solid rgba({_hex_to_rgb(idx_color)},0.38);'>{idx_label}</span>"
        f"</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — Container Shortage / Surplus Map  (Heatmap + Alert Panel)
# ══════════════════════════════════════════════════════════════════════════════

def _render_shortage_surplus_map() -> None:
    section_header(
        "Container Shortage / Surplus Map",
        "6 regions × 5 container types. "
        "Color = utilization % — red = tight/critical, green = surplus. "
        "Critical cells trigger route-level rate premiums.",
    )

    if not REGIONAL_EQUIPMENT_STATUS:
        st.warning("No regional equipment data available.")
        return

    z_util, z_text, z_risk = _build_equip_matrix()
    x_labels = [_TYPE_LABELS.get(ct, ct) for ct in CONTAINER_TYPES]

    # ── Critical alert banner ─────────────────────────────────────────────
    critical_cells = [e for e in REGIONAL_EQUIPMENT_STATUS if e.shortage_risk == "CRITICAL"]
    high_cells     = [e for e in REGIONAL_EQUIPMENT_STATUS if e.shortage_risk == "HIGH"]

    if critical_cells:
        crit_desc = " • ".join(
            f"{e.region} / {_TYPE_LABELS.get(e.container_type, e.container_type)} "
            f"({int(e.utilization_pct)}% utilized, {abs(e.days_surplus_deficit)}d short)"
            for e in critical_cells
        )
        st.error(f"CRITICAL shortage: {crit_desc} — expect significant rate premiums and booking delays.", icon="🚨")

    if high_cells:
        high_desc = ", ".join(
            f"{e.region} {_TYPE_LABELS.get(e.container_type, e.container_type)}"
            for e in high_cells
        )
        st.warning(f"HIGH shortage risk: {high_desc}", icon="⚠️")

    col_heat, col_detail = st.columns([3, 2])

    with col_heat:
        fig = go.Figure(go.Heatmap(
            z=z_util,
            x=x_labels,
            y=REGIONS,
            colorscale=_UTIL_COLORSCALE,
            zmin=50,
            zmax=100,
            text=z_text,
            texttemplate="%{text}",
            textfont={"size": 11, "color": "#f1f5f9"},
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Type: %{x}<br>"
                "Utilization: %{z:.1f}%<br>"
                "<extra></extra>"
            ),
            showscale=True,
            colorbar={
                "title": {"text": "Utilization %", "font": {"color": C_TEXT2, "size": 10}},
                "tickfont": {"color": C_TEXT3, "size": 10},
                "bgcolor": _C_SURFACE,
                "bordercolor": C_BORDER,
                "borderwidth": 1,
                "len": 0.85,
            },
            xgap=3,
            ygap=3,
        ))
        layout = dark_layout(height=320, showlegend=False)
        layout["xaxis"]["tickfont"] = {"color": C_TEXT2, "size": 11}
        layout["yaxis"]["tickfont"] = {"color": C_TEXT2, "size": 11}
        layout["margin"] = {"l": 110, "r": 20, "t": 20, "b": 40}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="equip_heatmap")

        # Risk legend
        legend_html = " &nbsp; ".join(_risk_badge(r) for r in ["LOW","MODERATE","HIGH","CRITICAL"])
        st.markdown(
            f"<div style='font-size:0.76rem;color:{C_TEXT3};margin-top:-6px;'>"
            f"Shortage Risk: {legend_html}</div>",
            unsafe_allow_html=True,
        )

    with col_detail:
        # Per-region summary cards
        idx_map = {(e.region, e.container_type): e for e in REGIONAL_EQUIPMENT_STATUS}
        for region in REGIONS:
            region_equip = [e for e in REGIONAL_EQUIPMENT_STATUS if e.region == region]
            avg_util = (sum(e.utilization_pct for e in region_equip) / len(region_equip)
                        if region_equip else 0.0)
            total_k  = sum(e.available_units_k for e in region_equip)
            worst    = max(region_equip, key=lambda e: e.utilization_pct, default=None)
            color    = _REGION_COLORS.get(region, C_TEXT2)
            risk_tag = _risk_badge(worst.shortage_risk if worst else "LOW")
            rgb      = _hex_to_rgb(color)

            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-left:3px solid {color};border-radius:8px;"
                f"padding:10px 14px;margin-bottom:6px;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                f"<span style='font-size:0.82rem;font-weight:700;color:{C_TEXT};'>{region}</span>"
                f"<span style='font-size:0.78rem;font-weight:700;color:{color};'>"
                f"{avg_util:.0f}% avg util</span></div>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;margin-top:5px;'>"
                f"<span style='font-size:0.72rem;color:{C_TEXT3};'>{total_k:.0f}K TEU tracked</span>"
                f"{risk_tag}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # CSV export
    rows = [{"Region": e.region,
             "Container Type": _TYPE_LABELS.get(e.container_type, e.container_type),
             "Utilization %": e.utilization_pct,
             "Available Units (K)": e.available_units_k,
             "Shortage Risk": e.shortage_risk,
             "Days Surplus/Deficit": e.days_surplus_deficit,
             "Daily Lease Rate USD": e.daily_lease_rate_usd}
            for e in REGIONAL_EQUIPMENT_STATUS]
    if rows:
        st.download_button(
            label="Download Equipment Status CSV",
            data=pd.DataFrame(rows).to_csv(index=False),
            file_name="equipment_status.csv",
            mime="text/csv",
            key="equip_status_csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — Repositioning Cost by Route + Sankey
# ══════════════════════════════════════════════════════════════════════════════

def _render_repositioning_costs() -> None:
    section_header(
        "Container Repositioning Cost by Route",
        "Empty container repositioning is a hidden freight surcharge embedded in spot rates. "
        "Export-heavy origin routes carry the highest repositioning premiums.",
    )

    if not TRADE_IMBALANCE_DATA:
        st.warning("No trade imbalance data available.")
        return

    col_bar, col_sankey = st.columns([2, 3])

    with col_bar:
        # Horizontal bar chart: repositioning cost per FEU by route
        sorted_routes = sorted(
            TRADE_IMBALANCE_DATA,
            key=lambda m: m.empty_container_repositioning_cost_per_feu,
            reverse=True,
        )
        route_labels = [
            m.route_id.replace("_", " ").title()[:28]
            for m in sorted_routes
        ]
        costs = [m.empty_container_repositioning_cost_per_feu for m in sorted_routes]
        bar_colors = [
            _C_RED if c >= 400 else (_C_AMBER if c >= 250 else _C_GREEN)
            for c in costs
        ]

        fig_bar = go.Figure(go.Bar(
            y=route_labels,
            x=costs,
            orientation="h",
            marker_color=bar_colors,
            marker_opacity=0.88,
            text=[f"${c:,}" for c in costs],
            textposition="outside",
            textfont={"color": C_TEXT2, "size": 10},
            hovertemplate="%{y}<br>Repositioning: $%{x:,}/FEU<extra></extra>",
        ))
        layout = dark_layout(
            title="Repositioning Cost / FEU (USD)",
            height=480,
            showlegend=False,
        )
        layout["xaxis"]["title"] = "USD per FEU"
        layout["xaxis"]["tickfont"] = {"color": C_TEXT3, "size": 10}
        layout["yaxis"]["tickfont"] = {"color": C_TEXT2, "size": 10}
        layout["margin"] = {"l": 160, "r": 60, "t": 40, "b": 30}
        fig_bar.update_layout(**layout)
        st.plotly_chart(fig_bar, use_container_width=True, key="equip_reposition_bar")

    with col_sankey:
        # Sankey: loaded vs empty flows across key corridors
        selected_routes = [
            ("transpacific_eb",     "Asia Pacific",  "North America", True),
            ("transpacific_wb",     "North America", "Asia Pacific",  False),
            ("asia_europe",         "Asia Pacific",  "Europe",        True),
            ("med_hub_to_asia",     "Europe",        "Asia Pacific",  False),
            ("ningbo_europe",       "Asia Pacific",  "Europe",        True),
            ("transatlantic",       "Europe",        "North America", True),
            ("china_south_america", "Asia Pacific",  "South America", True),
            ("europe_south_america","Europe",        "South America", True),
        ]
        node_labels = ["Asia Pacific","North America","Europe","South America","Middle East","Africa"]
        node_idx    = {lbl: i for i, lbl in enumerate(node_labels)}
        node_colors = [
            "rgba(59,130,246,0.85)",
            "rgba(16,185,129,0.85)",
            "rgba(139,92,246,0.85)",
            "rgba(245,158,11,0.85)",
            "rgba(6,182,212,0.85)",
            "rgba(239,68,68,0.85)",
        ]
        imb_idx = {m.route_id: m for m in TRADE_IMBALANCE_DATA}
        sources, targets, values, link_colors, link_labels = [], [], [], [], []

        for route_id, origin, dest, is_loaded in selected_routes:
            m = imb_idx.get(route_id)
            if not m:
                continue
            src = node_idx.get(origin)
            tgt = node_idx.get(dest)
            if src is None or tgt is None:
                continue
            if is_loaded:
                vol   = max(m.imbalance_ratio * 10, 5)
                color = "rgba(59,130,246,0.55)"
                label = (f"Loaded — ${m.empty_container_repositioning_cost_per_feu:,}/FEU "
                         f"reposition cost | IR: {m.imbalance_ratio:.2f}")
            else:
                vol   = max((2.0 - m.imbalance_ratio) * 8, 3)
                color = "rgba(100,116,139,0.28)"
                label = (f"Empty repositioning — {m.repositioning_days}d | "
                         f"${m.empty_container_repositioning_cost_per_feu:,}/FEU")
            sources.append(src); targets.append(tgt)
            values.append(vol); link_colors.append(color); link_labels.append(label)

        if sources:
            fig_sk = go.Figure(go.Sankey(
                arrangement="snap",
                node={
                    "pad": 16, "thickness": 20,
                    "line": {"color": "rgba(255,255,255,0.12)", "width": 0.8},
                    "label": node_labels, "color": node_colors,
                    "hovertemplate": "%{label}<extra></extra>",
                },
                link={
                    "source": sources, "target": targets, "value": values,
                    "color": link_colors, "label": link_labels,
                    "hovertemplate": "%{label}<extra></extra>",
                },
            ))
            layout_sk = dark_layout(height=480, showlegend=False)
            layout_sk["margin"] = {"l": 10, "r": 10, "t": 35, "b": 20}
            layout_sk["title"] = {"text": "Trade Flow: Loaded (blue) vs Empty Repositioning (gray)",
                                   "font": {"size": 12, "color": C_TEXT2}, "x": 0.01}
            fig_sk.update_layout(**layout_sk)
            st.plotly_chart(fig_sk, use_container_width=True, key="equip_sankey")

    # Repositioning stats strip
    avg_cost = sum(m.empty_container_repositioning_cost_per_feu for m in TRADE_IMBALANCE_DATA) / len(TRADE_IMBALANCE_DATA)
    max_route = max(TRADE_IMBALANCE_DATA, key=lambda m: m.empty_container_repositioning_cost_per_feu)
    avg_days  = sum(m.repositioning_days for m in TRADE_IMBALANCE_DATA) / len(TRADE_IMBALANCE_DATA)

    st.markdown(
        f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:10px;padding:14px 20px;display:flex;gap:40px;flex-wrap:wrap;margin-top:8px;'>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
        f"Avg Repositioning Cost</div>"
        f"<div style='font-size:1.1rem;font-weight:700;color:{_C_AMBER};margin-top:4px;'>${avg_cost:,.0f}/FEU</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
        f"Highest Cost Route</div>"
        f"<div style='font-size:1.1rem;font-weight:700;color:{_C_RED};margin-top:4px;'>"
        f"{max_route.route_id.replace('_',' ').title()} — ${max_route.empty_container_repositioning_cost_per_feu:,}</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
        f"Avg Reposition Days</div>"
        f"<div style='font-size:1.1rem;font-weight:700;color:{_C_BLUE};margin-top:4px;'>{avg_days:.0f} days</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
        f"Routes Tracked</div>"
        f"<div style='font-size:1.1rem;font-weight:700;color:{C_TEXT};margin-top:4px;'>{len(TRADE_IMBALANCE_DATA)}</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # CSV
    imb_rows = [{
        "Route ID": m.route_id,
        "Origin": m.origin_region, "Destination": m.dest_region,
        "Repositioning Cost (USD/FEU)": m.empty_container_repositioning_cost_per_feu,
        "Imbalance Ratio": m.imbalance_ratio,
        "Repositioning Days": m.repositioning_days,
    } for m in TRADE_IMBALANCE_DATA]
    st.download_button(
        label="Download Repositioning Cost CSV",
        data=pd.DataFrame(imb_rows).to_csv(index=False),
        file_name="repositioning_costs.csv",
        mime="text/csv",
        key="equip_reposition_csv",
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — Equipment Turn Time (Dwell) by Port
# ══════════════════════════════════════════════════════════════════════════════

def _render_dwell_times() -> None:
    section_header(
        "Equipment Turn Time by Port",
        "Container dwell time is a primary driver of effective equipment utilization. "
        "High dwell = containers tied up at port, reducing available pool. "
        f"Global average: {_PORT_GLOBAL_AVG_DWELL} days.",
    )

    col_filter, _ = st.columns([2, 5])
    with col_filter:
        region_filter = st.selectbox(
            "Filter by Region",
            options=["All Regions"] + REGIONS,
            index=0,
            key="dwell_region_filter",
        )

    filtered = (
        _PORT_DWELL_TIMES if region_filter == "All Regions"
        else [p for p in _PORT_DWELL_TIMES if p["region"] == region_filter]
    )
    filtered_sorted = sorted(filtered, key=lambda p: p["dwell_days"], reverse=True)

    col_chart, col_cards = st.columns([3, 2])

    with col_chart:
        colors = []
        for p in filtered_sorted:
            d = p["dwell_days"]
            if d >= 8:
                colors.append(_C_RED)
            elif d >= 6:
                colors.append(_C_AMBER)
            elif d >= 4:
                colors.append(_C_BLUE)
            else:
                colors.append(_C_GREEN)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=[p["port"] for p in filtered_sorted],
            x=[p["dwell_days"] for p in filtered_sorted],
            orientation="h",
            marker_color=colors,
            marker_opacity=0.88,
            text=[f"{p['dwell_days']}d" for p in filtered_sorted],
            textposition="outside",
            textfont={"color": C_TEXT2, "size": 10},
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Dwell: %{x} days<br>"
                "<extra></extra>"
            ),
        ))
        # Global average reference line
        fig.add_vline(
            x=_PORT_GLOBAL_AVG_DWELL,
            line={"color": "rgba(255,255,255,0.35)", "dash": "dash", "width": 1.5},
            annotation_text=f"Global avg {_PORT_GLOBAL_AVG_DWELL}d",
            annotation_position="top",
            annotation_font={"color": C_TEXT3, "size": 10},
        )
        layout = dark_layout(
            title="Average Container Dwell Time (days) — Major Ports",
            height=max(320, len(filtered_sorted) * 26 + 60),
            showlegend=False,
        )
        layout["xaxis"]["title"] = "Dwell Days"
        layout["xaxis"]["tickfont"] = {"color": C_TEXT3, "size": 10}
        layout["yaxis"]["tickfont"] = {"color": C_TEXT2, "size": 10}
        layout["margin"] = {"l": 110, "r": 70, "t": 40, "b": 30}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="equip_dwell_bar")

    with col_cards:
        st.markdown(
            f"<div style='font-size:0.72rem;font-weight:700;color:{C_TEXT2};"
            f"text-transform:uppercase;letter-spacing:0.07em;margin-bottom:10px;'>"
            f"Port Detail</div>",
            unsafe_allow_html=True,
        )
        for p in filtered_sorted[:10]:  # show top 10
            d = p["dwell_days"]
            if d >= 8:
                dcolor = _C_RED
            elif d >= 6:
                dcolor = _C_AMBER
            elif d >= 4:
                dcolor = _C_BLUE
            else:
                dcolor = _C_GREEN

            vs_avg = p["vs_avg"]
            vs_sign = "+" if vs_avg >= 0 else ""
            vs_color = _C_RED if vs_avg > 20 else (_C_AMBER if vs_avg > 0 else _C_GREEN)
            r_color = _REGION_COLORS.get(p["region"], C_TEXT2)

            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-left:3px solid {dcolor};border-radius:8px;"
                f"padding:9px 13px;margin-bottom:5px;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                f"<span style='font-size:0.82rem;font-weight:700;color:{C_TEXT};'>{p['port']}</span>"
                f"<span style='font-size:0.85rem;font-weight:800;color:{dcolor};'>{d}d</span></div>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;margin-top:4px;'>"
                f"<span style='font-size:0.70rem;color:{r_color};'>{p['region']}</span>"
                f"<span style='font-size:0.70rem;color:{vs_color};'>{vs_sign}{vs_avg}% vs avg</span></div>"
                f"<div style='margin-top:4px;'>{_trend_badge(p['trend'])}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Dwell summary stats
    all_dwell = [p["dwell_days"] for p in filtered]
    if all_dwell:
        avg_d  = sum(all_dwell) / len(all_dwell)
        worst_p = max(filtered, key=lambda p: p["dwell_days"])
        best_p  = min(filtered, key=lambda p: p["dwell_days"])
        st.markdown(
            f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
            f"border-radius:10px;padding:12px 20px;display:flex;gap:36px;flex-wrap:wrap;margin-top:6px;'>"
            f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
            f"Selection Average</div><div style='font-size:1.05rem;font-weight:700;color:{_C_BLUE};margin-top:3px;'>"
            f"{avg_d:.1f} days</div></div>"
            f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
            f"Worst Port</div><div style='font-size:1.05rem;font-weight:700;color:{_C_RED};margin-top:3px;'>"
            f"{worst_p['port']} ({worst_p['dwell_days']}d)</div></div>"
            f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
            f"Best Port</div><div style='font-size:1.05rem;font-weight:700;color:{_C_GREEN};margin-top:3px;'>"
            f"{best_p['port']} ({best_p['dwell_days']}d)</div></div>"
            f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;letter-spacing:0.07em;'>"
            f"Ports Tracked</div><div style='font-size:1.05rem;font-weight:700;color:{C_TEXT};margin-top:3px;'>"
            f"{len(filtered)}</div></div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — Reefer Equipment Availability by Region
# ══════════════════════════════════════════════════════════════════════════════

def _render_reefer_section() -> None:
    section_header(
        "Reefer Equipment Availability by Region",
        "Refrigerated containers are the tightest equipment segment globally. "
        "86–91% utilization across regions. Structural deficit driven by pharma, "
        "perishables and e-commerce growth outpacing fleet investment.",
    )

    reefer_data = get_reefer_summary()
    reefers = [e for e in REGIONAL_EQUIPMENT_STATUS if e.container_type == "40FT_REEFER"]

    if not reefer_data or not reefers:
        st.warning("Reefer data unavailable.")
        return

    # ── KPI strip ─────────────────────────────────────────────────────────
    avg_util = reefer_data.get("avg_utilization_pct") or 0.0
    avg_rate = reefer_data.get("avg_lease_rate_usd")  or 0.0
    total_k  = reefer_data.get("total_units_k")       or 0.0
    dry_avg  = 0.88
    premium_x = round(avg_rate / dry_avg, 1) if dry_avg > 0 and avg_rate > 0 else 0.0
    crit_regions = reefer_data.get("regions_critical", [])
    high_regions = reefer_data.get("regions_high",     [])

    c1, c2, c3, c4, c5 = st.columns(5)
    kpis = [
        (c1, "Avg Reefer Utilization",  f"{avg_util}%",          "capacity-weighted",         _C_RED),
        (c2, "Total Reefer Units",      f"{total_k:.0f}K",       "units tracked",              _C_BLUE),
        (c3, "Avg Daily Lease Rate",    f"${avg_rate:.2f}/day",  "per 40ft reefer unit",       _C_AMBER),
        (c4, "Premium vs Dry Box",      f"{premium_x}×",         "daily lease rate multiple",  _C_PURPLE),
        (c5, "Critical Regions",        str(len(crit_regions)),  "CRITICAL shortage",          _C_ROSE),
    ]
    for col, label, value, subtitle, color in kpis:
        with col:
            st.markdown(_kpi_card(label, value, subtitle, color), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)

    col_left, col_right = st.columns([3, 2])

    with col_left:
        # Regional reefer utilization + lease rate dual-axis
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        reg_names  = [e.region for e in reefers]
        util_vals  = [e.utilization_pct for e in reefers]
        rate_vals  = [e.daily_lease_rate_usd for e in reefers]
        deficit_vals = [abs(e.days_surplus_deficit) if e.days_surplus_deficit < 0 else 0 for e in reefers]
        bar_colors = [
            _RISK_COLOR.get(e.shortage_risk, C_TEXT2) for e in reefers
        ]

        fig.add_trace(go.Bar(
            x=reg_names, y=util_vals,
            name="Utilization %",
            marker_color=bar_colors,
            marker_opacity=0.85,
            hovertemplate="%{x}: %{y:.1f}% utilized<extra></extra>",
        ), secondary_y=False)

        fig.add_trace(go.Scatter(
            x=reg_names, y=rate_vals,
            name="Daily Lease Rate (USD)",
            mode="lines+markers+text",
            line={"color": _C_AMBER, "width": 2.5},
            marker={"size": 10, "color": _C_AMBER,
                    "line": {"color": _C_BG, "width": 2}},
            text=[f"${r:.2f}" for r in rate_vals],
            textposition="top center",
            textfont={"color": _C_AMBER, "size": 10},
            hovertemplate="%{x}: $%{y:.2f}/day<extra></extra>",
        ), secondary_y=True)

        # Danger threshold line
        fig.add_hline(
            y=90, line={"color": "rgba(239,68,68,0.5)", "dash": "dash", "width": 1.5},
            annotation_text="90% danger zone",
            annotation_font={"color": _C_RED, "size": 10},
            secondary_y=False,
        )

        layout = dark_layout(
            title="Reefer Utilization (bars) & Daily Lease Rate (line) by Region",
            height=320,
        )
        layout["yaxis"]  = {**layout.get("yaxis", {}),
                             "title": "Utilization %", "range": [70, 100],
                             "tickfont": {"color": C_TEXT3, "size": 10}}
        layout["yaxis2"] = {"title": "USD/day", "range": [2.5, 5.0],
                             "tickfont": {"color": _C_AMBER, "size": 10},
                             "titlefont": {"color": _C_AMBER}}
        layout["margin"] = {"l": 50, "r": 60, "t": 45, "b": 30}
        layout["legend"] = {"orientation": "h", "y": -0.22, "font": {"color": C_TEXT3, "size": 10}}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="equip_reefer_util")

        # Seasonal demand chart
        months = _REEFER_SEASONAL["labels"]
        fig2 = go.Figure()
        seasonal_colors = {
            "Global": C_TEXT2,
            "South America": _C_AMBER,
            "Europe": _C_PURPLE,
            "Asia Pacific": _C_BLUE,
            "North America": _C_GREEN,
        }
        for region, color in seasonal_colors.items():
            y_vals = _REEFER_SEASONAL.get(region, [])
            if not y_vals:
                continue
            is_global = region == "Global"
            fig2.add_trace(go.Scatter(
                x=months, y=y_vals, name=region,
                mode="lines+markers",
                line={"color": color, "width": 2.5 if is_global else 1.5,
                      "dash": "solid" if is_global else "dot"},
                marker={"size": 5 if is_global else 4, "color": color},
                hovertemplate=f"{region} — %{{x}}: %{{y}}<extra></extra>",
            ))
        fig2.add_hline(y=100,
                       line={"color": "rgba(255,255,255,0.18)", "dash": "dash", "width": 1},
                       annotation_text="Annual avg", annotation_position="right",
                       annotation_font={"color": C_TEXT3, "size": 10})
        layout2 = dark_layout(title="Reefer Seasonal Demand Index (100 = annual avg)", height=270)
        layout2["yaxis"]["range"] = [60, 145]
        layout2["margin"] = {"l": 40, "r": 60, "t": 40, "b": 20}
        layout2["legend"] = {"orientation": "h", "y": -0.28, "font": {"color": C_TEXT3, "size": 10}}
        fig2.update_layout(**layout2)
        st.plotly_chart(fig2, use_container_width=True, key="equip_reefer_seasonal")

    with col_right:
        # Reefer commodity breakdown
        st.markdown(
            f"<div style='font-size:0.72rem;font-weight:700;color:{C_TEXT2};"
            f"text-transform:uppercase;letter-spacing:0.07em;margin-bottom:10px;'>"
            f"Top Reefer Commodities</div>",
            unsafe_allow_html=True,
        )
        for comm in _REEFER_COMMODITIES:
            color = comm["color"]
            rgb   = _hex_to_rgb(color)
            bar_w = min(int(comm["share_pct"] * 4.2), 100)
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-left:3px solid {color};border-radius:8px;"
                f"padding:10px 13px;margin-bottom:6px;'>"
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:center;margin-bottom:5px;'>"
                f"<span style='font-size:0.82rem;font-weight:600;color:{C_TEXT};'>"
                f"{comm['name']}</span>"
                f"<span style='font-size:0.80rem;font-weight:800;color:{color};'>"
                f"{comm['share_pct']}%</span></div>"
                f"<div style='background:rgba({rgb},0.12);border-radius:3px;height:4px;margin-bottom:6px;'>"
                f"<div style='background:{color};width:{bar_w}%;height:4px;border-radius:3px;'></div></div>"
                f"<div style='font-size:0.70rem;color:{C_TEXT3};'>"
                f"{comm['peak_months']} &nbsp;|&nbsp; {comm['key_origins']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Deficit days by region summary
        st.markdown(
            f"<div style='font-size:0.72rem;font-weight:700;color:{C_TEXT2};"
            f"text-transform:uppercase;letter-spacing:0.07em;margin-top:14px;margin-bottom:8px;'>"
            f"Reefer Deficit Days by Region</div>",
            unsafe_allow_html=True,
        )
        for e in reefers:
            d = e.days_surplus_deficit
            color = _RISK_COLOR.get(e.shortage_risk, C_TEXT2)
            rgb   = _hex_to_rgb(color)
            label = f"{abs(d)}d deficit" if d < 0 else f"{d}d surplus"
            sign_color = _C_RED if d < 0 else _C_GREEN
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:center;padding:6px 12px;"
                f"background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-radius:6px;margin-bottom:4px;'>"
                f"<span style='font-size:0.78rem;color:{C_TEXT};'>{e.region}</span>"
                f"<span style='font-size:0.78rem;font-weight:700;color:{sign_color};'>{label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — Equipment Shortage Alert System
# ══════════════════════════════════════════════════════════════════════════════

def _render_shortage_alerts() -> None:
    section_header(
        "Equipment Shortage Alert System",
        "Routes and regions at risk of equipment shortages impacting shipment timelines. "
        "Alerts ranked by severity — CRITICAL requires immediate alternative sourcing.",
    )

    # Build alert list from REGIONAL_EQUIPMENT_STATUS
    alerts = []
    for e in REGIONAL_EQUIPMENT_STATUS:
        if e.shortage_risk in ("CRITICAL", "HIGH"):
            # Find relevant routes
            related = [
                m for m in TRADE_IMBALANCE_DATA
                if m.origin_region == e.region or m.dest_region == e.region
            ]
            top_route = sorted(related, key=lambda m: m.empty_container_repositioning_cost_per_feu, reverse=True)
            route_str = top_route[0].route_id.replace("_", " ").title() if top_route else "Various routes"

            severity_score = (
                (e.utilization_pct / 100) * 60
                + (abs(e.days_surplus_deficit) / 30) * 25
                + (15 if e.shortage_risk == "CRITICAL" else 0)
            )
            alerts.append({
                "region":     e.region,
                "type":       _TYPE_LABELS.get(e.container_type, e.container_type),
                "risk":       e.shortage_risk,
                "util":       e.utilization_pct,
                "deficit_d":  e.days_surplus_deficit,
                "rate":       e.daily_lease_rate_usd,
                "yoy":        e.vs_year_ago_pct,
                "route":      route_str,
                "score":      severity_score,
            })

    alerts.sort(key=lambda a: a["score"], reverse=True)

    if not alerts:
        st.success("No active equipment shortage alerts — all regions within normal utilization ranges.", icon="✅")
        return

    # Summary bar
    crit_count = sum(1 for a in alerts if a["risk"] == "CRITICAL")
    high_count = sum(1 for a in alerts if a["risk"] == "HIGH")

    st.markdown(
        f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-left:4px solid {_C_RED};border-radius:10px;"
        f"padding:14px 20px;display:flex;gap:32px;flex-wrap:wrap;margin-bottom:16px;'>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Active Alerts</div>"
        f"<div style='font-size:1.4rem;font-weight:800;color:{_C_RED};margin-top:2px;'>"
        f"{len(alerts)}</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Critical</div>"
        f"<div style='font-size:1.4rem;font-weight:800;color:#b91c1c;margin-top:2px;'>"
        f"{crit_count}</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>High Risk</div>"
        f"<div style='font-size:1.4rem;font-weight:800;color:{_C_RED};margin-top:2px;'>"
        f"{high_count}</div></div>"
        f"<div style='flex:1;display:flex;align-items:center;'>"
        f"<div style='font-size:0.80rem;color:{C_TEXT3};'>"
        f"Alerts represent region × container-type combinations where "
        f"utilization and deficit days indicate shortage risk to booked cargo. "
        f"Rate premiums of 15–45% above baseline are typical in CRITICAL conditions.</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    col_a, col_b = st.columns(2)
    for i, alert in enumerate(alerts):
        col = col_a if i % 2 == 0 else col_b
        risk_color = _RISK_COLOR.get(alert["risk"], C_TEXT2)
        rgb = _hex_to_rgb(risk_color)
        yoy_sign  = "+" if alert["yoy"] >= 0 else ""
        yoy_color = _C_RED if alert["yoy"] > 0 else _C_GREEN
        deficit_label = (
            f"{abs(alert['deficit_d'])}d DEFICIT" if alert["deficit_d"] < 0
            else f"{alert['deficit_d']}d surplus"
        )
        deficit_color = _C_RED if alert["deficit_d"] < 0 else _C_GREEN

        with col:
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-left:4px solid {risk_color};"
                f"border-radius:10px;padding:14px 16px;margin-bottom:10px;'>"
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:flex-start;margin-bottom:8px;'>"
                f"<div>"
                f"<div style='font-size:0.88rem;font-weight:700;color:{C_TEXT};'>"
                f"{alert['region']} — {alert['type']}</div>"
                f"<div style='font-size:0.72rem;color:{C_TEXT3};margin-top:2px;'>"
                f"Primary exposure: {alert['route']}</div>"
                f"</div>"
                f"{_risk_badge(alert['risk'])}"
                f"</div>"
                f"<div style='display:flex;gap:24px;flex-wrap:wrap;'>"
                f"<div><div style='font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>Utilization</div>"
                f"<div style='font-size:1.05rem;font-weight:700;color:{risk_color};margin-top:2px;'>"
                f"{alert['util']:.0f}%</div></div>"
                f"<div><div style='font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>Supply Status</div>"
                f"<div style='font-size:1.05rem;font-weight:700;color:{deficit_color};margin-top:2px;'>"
                f"{deficit_label}</div></div>"
                f"<div><div style='font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>Lease Rate</div>"
                f"<div style='font-size:1.05rem;font-weight:700;color:{_C_AMBER};margin-top:2px;'>"
                f"${alert['rate']:.2f}/day</div></div>"
                f"<div><div style='font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>YoY Change</div>"
                f"<div style='font-size:1.05rem;font-weight:700;color:{yoy_color};margin-top:2px;'>"
                f"{yoy_sign}{alert['yoy']:.1f}pp</div></div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — Container Age Distribution
# ══════════════════════════════════════════════════════════════════════════════

def _render_age_distribution() -> None:
    section_header(
        "Container Fleet Age Distribution",
        "Fleet age profile and replacement needs. "
        "14% of global TEU pool is 20+ years old — approaching end-of-life. "
        "Post-COVID 2020–2023 newbuild surge created a young fleet bulge.",
    )

    col_donut, col_table, col_timeline = st.columns([2, 2, 3])

    with col_donut:
        pcts   = [b["pct"] for b in _FLEET_AGE_DIST]
        labels = [f"{b['bracket']} ({b['status']})" for b in _FLEET_AGE_DIST]
        colors = [b["color"] for b in _FLEET_AGE_DIST]

        fig = go.Figure(go.Pie(
            labels=labels,
            values=pcts,
            hole=0.58,
            marker_colors=colors,
            marker_line={"color": _C_BG, "width": 2},
            textinfo="percent",
            textfont={"color": "#f1f5f9", "size": 11},
            hovertemplate="%{label}<br>%{value}% of fleet<extra></extra>",
        ))
        fig.add_annotation(
            text="<b>Fleet<br>Age Mix</b>",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT2, "size": 12},
        )
        layout = dark_layout(height=300, showlegend=False)
        layout["margin"] = {"l": 10, "r": 10, "t": 30, "b": 10}
        layout["title"] = {"text": "Global Fleet Age Profile", "font": {"size": 12, "color": C_TEXT2}, "x": 0.01}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="equip_age_donut")

    with col_table:
        st.markdown(
            f"<div style='font-size:0.72rem;font-weight:700;color:{C_TEXT2};"
            f"text-transform:uppercase;letter-spacing:0.07em;margin-bottom:10px;'>"
            f"Age Bracket Details</div>",
            unsafe_allow_html=True,
        )
        global_fleet = _GLOBAL_TEU_POOL["total_teu_m"]
        for b in _FLEET_AGE_DIST:
            color = b["color"]
            rgb   = _hex_to_rgb(color)
            teu_m = global_fleet * b["pct"] / 100
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-left:3px solid {color};border-radius:8px;"
                f"padding:10px 14px;margin-bottom:6px;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                f"<span style='font-size:0.82rem;font-weight:700;color:{C_TEXT};'>{b['bracket']}</span>"
                f"<span style='font-size:0.82rem;font-weight:800;color:{color};'>{b['pct']}%</span></div>"
                f"<div style='background:rgba({rgb},0.12);border-radius:3px;height:3px;margin:6px 0;'>"
                f"<div style='background:{color};width:{min(b['pct']*3, 100)}%;height:3px;border-radius:3px;'>"
                f"</div></div>"
                f"<div style='display:flex;justify-content:space-between;'>"
                f"<span style='font-size:0.70rem;color:{C_TEXT3};'>{b['status']}</span>"
                f"<span style='font-size:0.70rem;color:{C_TEXT3};'>{teu_m:.2f}M TEU</span></div>"
                f"<div style='font-size:0.70rem;color:{C_TEXT3};margin-top:3px;'>{b['note']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    with col_timeline:
        # Scrapping and renewal demand bar chart
        age_brackets = [b["bracket"] for b in _FLEET_AGE_DIST]
        pcts_vals    = [b["pct"] for b in _FLEET_AGE_DIST]
        teu_vals     = [_GLOBAL_TEU_POOL["total_teu_m"] * p / 100 for p in pcts_vals]
        bar_cols     = [b["color"] for b in _FLEET_AGE_DIST]

        # Replacement urgency (qualitative score: 0–100)
        urgency = [5, 10, 30, 60, 85, 100]

        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_trace(go.Bar(
            x=age_brackets, y=teu_vals,
            name="Fleet Size (M TEU)",
            marker_color=bar_cols,
            marker_opacity=0.85,
            hovertemplate="%{x}: %{y:.2f}M TEU<extra></extra>",
        ), secondary_y=False)
        fig2.add_trace(go.Scatter(
            x=age_brackets, y=urgency,
            name="Replacement Urgency (0–100)",
            mode="lines+markers",
            line={"color": _C_ROSE, "width": 2.5},
            marker={"size": 9, "color": _C_ROSE, "line": {"color": _C_BG, "width": 2}},
            hovertemplate="%{x}: urgency score %{y}<extra></extra>",
        ), secondary_y=True)

        layout2 = dark_layout(
            title="Fleet Volume (bars) & Replacement Urgency Score (line)",
            height=300,
        )
        layout2["yaxis"]  = {"title": "M TEU", "tickfont": {"color": C_TEXT3, "size": 10}}
        layout2["yaxis2"] = {"title": "Urgency (0–100)", "range": [0, 120],
                              "tickfont": {"color": _C_ROSE, "size": 10},
                              "titlefont": {"color": _C_ROSE}}
        layout2["margin"] = {"l": 50, "r": 60, "t": 45, "b": 50}
        layout2["xaxis"]["tickfont"] = {"color": C_TEXT2, "size": 10}
        layout2["legend"] = {"orientation": "h", "y": -0.28, "font": {"color": C_TEXT3, "size": 10}}
        fig2.update_layout(**layout2)
        st.plotly_chart(fig2, use_container_width=True, key="equip_age_bars")

        # Replacement need callout
        eol_pct = _FLEET_AGE_DIST[-1]["pct"] + _FLEET_AGE_DIST[-2]["pct"]
        eol_teu = _GLOBAL_TEU_POOL["total_teu_m"] * eol_pct / 100
        st.markdown(
            f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
            f"border-left:4px solid {_C_ORANGE};border-radius:8px;"
            f"padding:12px 16px;margin-top:8px;'>"
            f"<span style='font-size:0.80rem;font-weight:700;color:{_C_ORANGE};'>"
            f"Fleet Replacement Pipeline: </span>"
            f"<span style='font-size:0.80rem;color:{C_TEXT2};'>"
            f"{eol_pct:.1f}% of global fleet ({eol_teu:.2f}M TEU) is 20+ years old "
            f"and represents near-term scrapping/replacement demand. "
            f"At current newbuild pricing ($3,800–$28,000/unit), total replacement "
            f"capex across the aging bracket is estimated at $80–120B over 5 years."
            f"</span></div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — Leasing vs Owned Equipment Economics
# ══════════════════════════════════════════════════════════════════════════════

def _render_lease_vs_own() -> None:
    section_header(
        "Leasing vs Owned Equipment Economics",
        "Build-or-lease decision framework by container type. "
        "Dry box leasing premium averages 44% over implied ownership cost. "
        "Specialised units (reefer, tank) favour leasing due to high capex and utilisation volatility.",
    )

    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        types   = [r["type"] for r in _LEASE_VS_OWN]
        own_d   = [r["own_daily_usd"] for r in _LEASE_VS_OWN]
        lease_d = [r["lease_daily"] for r in _LEASE_VS_OWN]
        premium = [r["lease_premium"] for r in _LEASE_VS_OWN]

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Bar(
            x=types, y=own_d,
            name="Implied Own Cost (USD/day)",
            marker_color=_C_BLUE,
            marker_opacity=0.85,
            hovertemplate="%{x}<br>Own: $%{y:.2f}/day<extra></extra>",
        ), secondary_y=False)
        fig.add_trace(go.Bar(
            x=types, y=lease_d,
            name="Lease Rate (USD/day)",
            marker_color=_C_PURPLE,
            marker_opacity=0.85,
            hovertemplate="%{x}<br>Lease: $%{y:.2f}/day<extra></extra>",
        ), secondary_y=False)
        prem_colors = [
            _C_GREEN if p < 0 else (_C_AMBER if p < 30 else _C_RED)
            for p in premium
        ]
        fig.add_trace(go.Scatter(
            x=types, y=premium,
            name="Lease Premium vs Own (%)",
            mode="lines+markers+text",
            line={"color": _C_AMBER, "width": 2.5},
            marker={"size": 10, "color": prem_colors,
                    "line": {"color": _C_BG, "width": 2}},
            text=[f"{p:+}%" for p in premium],
            textposition="top center",
            textfont={"size": 10},
            hovertemplate="%{x}: %{y:+}% lease premium<extra></extra>",
        ), secondary_y=True)

        fig.add_hline(
            y=0, line={"color": "rgba(255,255,255,0.2)", "dash": "dash", "width": 1},
            secondary_y=True,
            annotation_text="Break-even",
            annotation_font={"color": C_TEXT3, "size": 10},
        )

        layout = dark_layout(
            title="Daily Cost: Own vs Lease (bars) + Premium % (line)",
            height=340,
        )
        layout["barmode"] = "group"
        layout["yaxis"]   = {"title": "USD/day", "tickfont": {"color": C_TEXT3, "size": 10}}
        layout["yaxis2"]  = {"title": "Lease Premium %",
                              "tickfont": {"color": _C_AMBER, "size": 10},
                              "titlefont": {"color": _C_AMBER},
                              "zeroline": False}
        layout["margin"]  = {"l": 50, "r": 60, "t": 45, "b": 50}
        layout["xaxis"]["tickfont"] = {"color": C_TEXT2, "size": 11}
        layout["legend"]  = {"orientation": "h", "y": -0.28, "font": {"color": C_TEXT3, "size": 10}}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="equip_lease_own_chart")

        # Breakeven years chart
        fig_be = go.Figure(go.Bar(
            x=types,
            y=[r["breakeven_yrs"] for r in _LEASE_VS_OWN],
            marker_color=[_C_GREEN if r["breakeven_yrs"] <= 5 else _C_AMBER
                          for r in _LEASE_VS_OWN],
            marker_opacity=0.85,
            text=[f"{r['breakeven_yrs']}y" for r in _LEASE_VS_OWN],
            textposition="outside",
            textfont={"color": C_TEXT2, "size": 10},
            hovertemplate="%{x}: break-even in %{y}yr<extra></extra>",
        ))
        fig_be.add_hline(
            y=5, line={"color": "rgba(16,185,129,0.4)", "dash": "dash", "width": 1.5},
            annotation_text="5yr threshold",
            annotation_font={"color": _C_GREEN, "size": 10},
        )
        layout_be = dark_layout(
            title="Ownership Break-Even vs Leasing (years)",
            height=210, showlegend=False,
        )
        layout_be["yaxis"]["title"] = "Years"
        layout_be["yaxis"]["tickfont"] = {"color": C_TEXT3, "size": 10}
        layout_be["xaxis"]["tickfont"] = {"color": C_TEXT2, "size": 11}
        layout_be["margin"] = {"l": 40, "r": 20, "t": 40, "b": 30}
        fig_be.update_layout(**layout_be)
        st.plotly_chart(fig_be, use_container_width=True, key="equip_breakeven_chart")

    with col_table:
        st.markdown(
            f"<div style='font-size:0.72rem;font-weight:700;color:{C_TEXT2};"
            f"text-transform:uppercase;letter-spacing:0.07em;margin-bottom:12px;'>"
            f"Lease/Own Detail by Type</div>",
            unsafe_allow_html=True,
        )
        for r in _LEASE_VS_OWN:
            prem = r["lease_premium"]
            prem_color = _C_RED if prem > 30 else (_C_AMBER if prem > 0 else _C_GREEN)
            own_c = r["own_capex_usd"]
            pref  = "Leasing preferred" if prem < 0 else ("Ownership preferred" if prem > 35 else "Market-dependent")
            pref_color = _C_GREEN if prem < 0 else (_C_RED if prem > 35 else _C_AMBER)

            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-radius:10px;padding:13px 15px;margin-bottom:8px;'>"
                f"<div style='font-size:0.88rem;font-weight:700;color:{C_TEXT};"
                f"margin-bottom:8px;'>{r['type']}</div>"
                f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;'>"
                f"<div><div style='font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>New Unit Cost</div>"
                f"<div style='font-size:0.88rem;font-weight:700;color:{C_TEXT};margin-top:2px;'>"
                f"${own_c:,}</div></div>"
                f"<div><div style='font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>Break-Even</div>"
                f"<div style='font-size:0.88rem;font-weight:700;color:{_C_BLUE};margin-top:2px;'>"
                f"{r['breakeven_yrs']}y</div></div>"
                f"<div><div style='font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>Own Daily</div>"
                f"<div style='font-size:0.88rem;font-weight:700;color:{_C_BLUE};margin-top:2px;'>"
                f"${r['own_daily_usd']:.2f}</div></div>"
                f"<div><div style='font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;"
                f"letter-spacing:0.06em;'>Lease Daily</div>"
                f"<div style='font-size:0.88rem;font-weight:700;color:{_C_PURPLE};margin-top:2px;'>"
                f"${r['lease_daily']:.2f}</div></div>"
                f"</div>"
                f"<div style='margin-top:8px;border-top:1px solid {C_BORDER};padding-top:7px;'>"
                f"<span style='font-size:0.70rem;font-weight:700;color:{prem_color};'>"
                f"Lease premium: {'+' if prem >= 0 else ''}{prem}%</span>"
                f"<span style='font-size:0.68rem;color:{C_TEXT3};'> · </span>"
                f"<span style='font-size:0.70rem;font-weight:700;color:{pref_color};'>{pref}</span>"
                f"<div style='font-size:0.68rem;color:{C_TEXT3};margin-top:4px;'>{r['market_trend']}</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Fleet strategy callout
        st.markdown(
            f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
            f"border-left:4px solid {_C_CYAN};border-radius:8px;"
            f"padding:12px 14px;margin-top:6px;'>"
            f"<div style='font-size:0.72rem;font-weight:700;color:{_C_CYAN};"
            f"text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px;'>"
            f"Fleet Strategy Note</div>"
            f"<div style='font-size:0.76rem;color:{C_TEXT2};line-height:1.55;'>"
            f"Major carriers (MSC, Maersk, CMA CGM) own 45–60% of their fleets "
            f"for cost control. Lessors (Triton, Textainer, CAI) provide market "
            f"flexibility. Post-2022 oversupply has pushed dry box lease rates to "
            f"multi-year lows — favouring short-term lease strategies for shippers "
            f"and carriers seeking to avoid overcapitalization."
            f"</div></div>",
            unsafe_allow_html=True,
        )

    # Export
    lease_rows = [{
        "Container Type": r["type"],
        "New Unit Capex (USD)": r["own_capex_usd"],
        "Implied Own Daily (USD)": r["own_daily_usd"],
        "Market Lease Daily (USD)": r["lease_daily"],
        "Lease Premium (%)": r["lease_premium"],
        "Break-Even (years)": r["breakeven_yrs"],
        "Market Trend": r["market_trend"],
    } for r in _LEASE_VS_OWN]
    st.download_button(
        label="Download Lease vs Own Economics CSV",
        data=pd.DataFrame(lease_rows).to_csv(index=False),
        file_name="lease_vs_own.csv",
        mime="text/csv",
        key="equip_lease_csv",
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 9 — Equipment Cost Calculator (interactive)
# ══════════════════════════════════════════════════════════════════════════════

def _render_cost_calculator(route_results: Any) -> None:
    section_header(
        "Equipment Cost Calculator",
        "Select a route and shipment size to see the full equipment-adjusted "
        "freight cost — including repositioning surcharges embedded in spot rates.",
    )

    if not TRADE_IMBALANCE_DATA:
        st.warning("Trade imbalance data unavailable — cannot compute equipment cost.")
        return

    route_options = {
        m.route_id: (
            m.route_id.replace("_", " ").title()
            + f"  ({m.origin_region} → {m.dest_region})"
        )
        for m in TRADE_IMBALANCE_DATA
    }
    route_display_list = list(route_options.values())
    route_id_list      = list(route_options.keys())

    col_sel, col_teu, col_base = st.columns([3, 2, 2])
    with col_sel:
        selected_display = st.selectbox(
            "Route", options=route_display_list, index=0, key="equip_calc_route",
        )
    with col_teu:
        teu_count = st.number_input(
            "TEU Count", min_value=1, max_value=15000, value=500, step=100, key="equip_calc_teu",
        )
    with col_base:
        base_rate_per_feu = st.number_input(
            "Base Rate (USD/FEU)", min_value=100, max_value=25000, value=2500, step=100, key="equip_calc_base",
        )

    selected_idx      = route_display_list.index(selected_display)
    selected_route_id = route_id_list[selected_idx]
    metrics           = get_trade_imbalance(selected_route_id)

    if metrics is None:
        st.error("Route data unavailable — cannot compute equipment-adjusted rate.")
        return

    feu_count          = max(teu_count / 2.0, 0.5)
    reposition_per_feu = metrics.empty_container_repositioning_cost_per_feu or 0.0
    adjusted_rate      = compute_equipment_adjusted_rate(selected_route_id, base_rate_per_feu) or 0.0
    total_base         = base_rate_per_feu * feu_count
    total_reposition   = reposition_per_feu * feu_count
    total_adjusted     = adjusted_rate * feu_count
    uplift_pct         = (reposition_per_feu / base_rate_per_feu * 100) if base_rate_per_feu > 0 else 0.0

    if metrics.imbalance_ratio > 1.3:
        imb_label, imb_color = "Export-heavy — empties flow back at cost", _C_RED
    elif metrics.imbalance_ratio < 0.8:
        imb_label, imb_color = "Import-heavy — carrier absorbs empty return", _C_AMBER
    else:
        imb_label, imb_color = "Near-balanced trade flow", _C_GREEN

    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

    # KPI output row
    c1, c2, c3, c4 = st.columns(4)
    for col, label, value, subtitle, color in [
        (c1, "Base Freight Cost",     f"${total_base:,.0f}",       f"{feu_count:,.0f} FEU × ${base_rate_per_feu:,}",       _C_BLUE),
        (c2, "Repositioning Charge",  f"${total_reposition:,.0f}", f"${reposition_per_feu:,}/FEU embedded surcharge",       _C_RED),
        (c3, "Equipment-Adj. Total",  f"${total_adjusted:,.0f}",   f"full cost for {feu_count:,.0f} FEU",                   _C_AMBER),
        (c4, "Rate Uplift",           f"{uplift_pct:.1f}%",        "repositioning as % of base rate",                       _C_PURPLE),
    ]:
        with col:
            st.markdown(_kpi_card(label, value, subtitle, color), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)

    # Detail card
    st.markdown(
        f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:10px;padding:18px 22px;'>"
        f"<div style='display:flex;gap:36px;flex-wrap:wrap;'>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Trade Imbalance Ratio</div>"
        f"<div style='font-size:1.15rem;font-weight:700;color:{imb_color};margin-top:4px;'>"
        f"{metrics.imbalance_ratio:.2f}:1</div>"
        f"<div style='font-size:0.75rem;color:{imb_color};margin-top:2px;'>{imb_label}</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Repositioning Days</div>"
        f"<div style='font-size:1.15rem;font-weight:700;color:{C_TEXT};margin-top:4px;'>"
        f"{metrics.repositioning_days} days</div>"
        f"<div style='font-size:0.75rem;color:{C_TEXT2};margin-top:2px;'>"
        f"empty transit back to origin</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Reposition per FEU</div>"
        f"<div style='font-size:1.15rem;font-weight:700;color:{_C_AMBER};margin-top:4px;'>"
        f"${reposition_per_feu:,.0f}</div>"
        f"<div style='font-size:0.75rem;color:{C_TEXT2};margin-top:2px;'>"
        f"adds to eastbound rate</div></div>"
        f"<div><div style='font-size:0.68rem;color:{C_TEXT2};text-transform:uppercase;"
        f"letter-spacing:0.07em;'>Adjusted Rate / FEU</div>"
        f"<div style='font-size:1.15rem;font-weight:700;color:{_C_RED};margin-top:4px;'>"
        f"${adjusted_rate:,.0f}</div>"
        f"<div style='font-size:0.75rem;color:{C_TEXT2};margin-top:2px;'>"
        f"vs base ${base_rate_per_feu:,}/FEU</div></div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )

    # Cost waterfall chart
    fig_wf = go.Figure(go.Waterfall(
        orientation="v",
        measure=["relative", "relative", "total"],
        x=["Base Freight Rate", "Repositioning Surcharge", "Equipment-Adj. Total"],
        y=[base_rate_per_feu, reposition_per_feu, 0],
        text=[f"${base_rate_per_feu:,}", f"+${reposition_per_feu:,}", f"${adjusted_rate:,.0f}"],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 11},
        connector={"line": {"color": "rgba(255,255,255,0.15)"}},
        increasing={"marker": {"color": _C_RED}},
        totals={"marker": {"color": _C_AMBER}},
        decreasing={"marker": {"color": _C_GREEN}},
        hovertemplate="%{x}: $%{y:,}/FEU<extra></extra>",
    ))
    layout_wf = dark_layout(
        title="Rate Build-Up Waterfall (USD/FEU)",
        height=280, showlegend=False,
    )
    layout_wf["yaxis"]["title"] = "USD/FEU"
    layout_wf["margin"] = {"l": 60, "r": 40, "t": 45, "b": 30}
    fig_wf.update_layout(**layout_wf)
    st.plotly_chart(fig_wf, use_container_width=True, key="equip_waterfall")

    # CSV export
    calc_csv = pd.DataFrame([{
        "Route": selected_route_id,
        "Base Rate (USD/FEU)": base_rate_per_feu,
        "TEU Count": teu_count,
        "FEU Count": feu_count,
        "Repositioning Cost (USD/FEU)": reposition_per_feu,
        "Equipment-Adjusted Rate (USD/FEU)": adjusted_rate,
        "Total Base Cost (USD)": total_base,
        "Total Repositioning Cost (USD)": total_reposition,
        "Total Adjusted Cost (USD)": total_adjusted,
        "Rate Uplift (%)": round(uplift_pct, 2),
        "Imbalance Ratio": metrics.imbalance_ratio,
        "Repositioning Days": metrics.repositioning_days,
    }]).to_csv(index=False)
    st.download_button(
        label="Download Calculation CSV",
        data=calc_csv,
        file_name="equipment_cost_calc.csv",
        mime="text/csv",
        key="equip_calc_csv",
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 10 — Regional Balance Timeline
# ══════════════════════════════════════════════════════════════════════════════

def _render_balance_timeline() -> None:
    section_header(
        "Regional Equipment Balance Timeline (2020–2026)",
        "Balance index: 100 = well-supplied, 0 = severe shortage. "
        "2021 COVID surge drove historic shortages; recovery has been uneven. "
        "Asia holds structural surplus; North America remains deficit-prone.",
    )

    years = _BALANCE_TIMELINE.get("years", [])
    if not years:
        st.warning("Balance timeline data unavailable.")
        return

    fig = go.Figure()
    for region, color in _REGION_COLORS.items():
        y_vals = _BALANCE_TIMELINE.get(region, [])
        if not y_vals:
            continue
        fig.add_trace(go.Scatter(
            x=years, y=y_vals,
            name=region,
            mode="lines+markers",
            line={"color": color, "width": 2.2},
            marker={"size": 8, "color": color, "line": {"color": _C_BG, "width": 1.5}},
            hovertemplate=f"{region} %{{x}}: %{{y}}<extra></extra>",
        ))

    # Zone bands
    fig.add_hrect(y0=0,   y1=35,  fillcolor="rgba(239,68,68,0.05)",   line_width=0,
                  annotation_text="Shortage zone",  annotation_position="left",
                  annotation_font={"color": _C_RED,   "size": 10})
    fig.add_hrect(y0=35,  y1=65,  fillcolor="rgba(245,158,11,0.04)",  line_width=0,
                  annotation_text="Transition",     annotation_position="left",
                  annotation_font={"color": _C_AMBER, "size": 10})
    fig.add_hrect(y0=65,  y1=100, fillcolor="rgba(16,185,129,0.04)",  line_width=0,
                  annotation_text="Surplus zone",  annotation_position="left",
                  annotation_font={"color": _C_GREEN, "size": 10})

    # Key event annotations
    for ann in [
        {"x": 2021, "y": 20, "text": "COVID surge\npeak shortage", "ax": 0, "ay": -55},
        {"x": 2022.5, "y": 40, "text": "Gradual\nrecovery",        "ax": 35, "ay": -40},
        {"x": 2025,   "y": 76, "text": "Asia\nsurplus",            "ax": 30, "ay": -30},
    ]:
        fig.add_annotation(
            x=ann["x"], y=ann["y"],
            text=ann["text"],
            showarrow=True, arrowhead=2,
            arrowcolor="rgba(255,255,255,0.3)", arrowwidth=1.5,
            ax=ann["ax"], ay=ann["ay"],
            font={"color": C_TEXT3, "size": 10},
            bgcolor="rgba(17,24,39,0.88)",
            bordercolor="rgba(255,255,255,0.1)",
            borderwidth=1, borderpad=4,
        )

    layout = dark_layout(
        title="Equipment Balance Index by Region (100 = well-supplied)",
        height=380,
    )
    layout["xaxis"]["title"] = "Year"
    layout["xaxis"]["tickvals"] = years
    layout["xaxis"]["ticktext"] = [str(y) for y in years]
    layout["yaxis"]["title"] = "Balance Index"
    layout["yaxis"]["range"] = [0, 108]
    layout["margin"] = {"l": 90, "r": 20, "t": 45, "b": 50}
    layout["legend"] = {"orientation": "h", "y": -0.22, "font": {"color": C_TEXT3, "size": 10}}
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="equip_balance_timeline")

    # Global index callout
    global_idx = get_global_equipment_index()
    if global_idx >= 85:
        idx_label, idx_color = "TIGHT", _C_RED
    elif global_idx >= 70:
        idx_label, idx_color = "NORMAL", _C_AMBER
    else:
        idx_label, idx_color = "SURPLUS", _C_GREEN

    st.markdown(
        f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-left:4px solid {idx_color};"
        f"border-radius:8px;padding:14px 18px;margin-top:8px;'>"
        f"<span style='font-size:0.80rem;font-weight:700;color:{C_TEXT2};"
        f"text-transform:uppercase;letter-spacing:0.06em;'>"
        f"Current Global Equipment Index: </span>"
        f"<span style='font-size:1.15rem;font-weight:800;color:{idx_color};'>"
        f"{global_idx:.1f}% &nbsp;</span>"
        f"<span style='display:inline-block;padding:2px 10px;border-radius:999px;"
        f"font-size:0.72rem;font-weight:700;"
        f"background:rgba({_hex_to_rgb(idx_color)},0.15);color:{idx_color};"
        f"border:1px solid rgba({_hex_to_rgb(idx_color)},0.38);'>{idx_label}</span>"
        f"<div style='font-size:0.80rem;color:{C_TEXT2};margin-top:6px;'>"
        f"Weighted-average utilization across all 6 regions and 5 container types. "
        f"Above 85% = tight market with rate pressure; below 70% = surplus conditions."
        f"</div></div>",
        unsafe_allow_html=True,
    )

    # CSV export
    timeline_rows = []
    for region in _REGION_COLORS:
        vals = _BALANCE_TIMELINE.get(region, [])
        for year, val in zip(years, vals):
            timeline_rows.append({"Region": region, "Year": year, "Balance Index": val})
    if timeline_rows:
        st.download_button(
            label="Download Balance Timeline CSV",
            data=pd.DataFrame(timeline_rows).to_csv(index=False),
            file_name="equipment_balance_timeline.csv",
            mime="text/csv",
            key="equip_timeline_csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN RENDER ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def render(
    route_results: Any = None,
    freight_data: Any = None,
    macro_data: Any = None,
) -> None:
    """Render the Container Equipment Tracking tab.

    Parameters
    ----------
    route_results:
        List of ShippingRoute objects from route_registry.  May be None.
    freight_data:
        Freight market data dict from the main app.  May be None.
    macro_data:
        Macro data dict from FRED/World Bank feeds.  May be None.
    """
    logger.debug("tab_equipment.render() called.")

    try:
        _render_enhanced_equipment_overview()
    except Exception:
        logger.exception("tab_equipment: error in enhanced overview")
        st.error("Error rendering Equipment Overview section.", icon="⚠️")

    try:
        _render_global_pool_overview()
    except Exception:
        logger.exception("tab_equipment: error in global pool overview")
        st.error("Error rendering Global Equipment Pool section.", icon="⚠️")

    _section_divider()

    try:
        _render_shortage_surplus_map()
    except Exception:
        logger.exception("tab_equipment: error in shortage/surplus map")
        st.error("Error rendering Shortage/Surplus Map section.", icon="⚠️")

    _section_divider()

    try:
        _render_repositioning_costs()
    except Exception:
        logger.exception("tab_equipment: error in repositioning costs")
        st.error("Error rendering Repositioning Cost section.", icon="⚠️")

    _section_divider()

    try:
        _render_dwell_times()
    except Exception:
        logger.exception("tab_equipment: error in dwell times")
        st.error("Error rendering Equipment Turn Time section.", icon="⚠️")

    _section_divider()

    try:
        _render_reefer_section()
    except Exception:
        logger.exception("tab_equipment: error in reefer section")
        st.error("Error rendering Reefer Availability section.", icon="⚠️")

    _section_divider()

    try:
        _render_shortage_alerts()
    except Exception:
        logger.exception("tab_equipment: error in shortage alerts")
        st.error("Error rendering Shortage Alert System section.", icon="⚠️")

    _section_divider()

    try:
        _render_age_distribution()
    except Exception:
        logger.exception("tab_equipment: error in age distribution")
        st.error("Error rendering Fleet Age Distribution section.", icon="⚠️")

    _section_divider()

    try:
        _render_lease_vs_own()
    except Exception:
        logger.exception("tab_equipment: error in lease vs own")
        st.error("Error rendering Leasing vs Owned Economics section.", icon="⚠️")

    _section_divider()

    try:
        _render_cost_calculator(route_results)
    except Exception:
        logger.exception("tab_equipment: error in cost calculator")
        st.error("Error rendering Equipment Cost Calculator section.", icon="⚠️")

    _section_divider()

    try:
        _render_balance_timeline()
    except Exception:
        logger.exception("tab_equipment: error in balance timeline")
        st.error("Error rendering Equipment Balance Timeline section.", icon="⚠️")


# ── Integration notes ─────────────────────────────────────────────────────
# Wire into app.py:
#
#   from ui import tab_equipment
#
#   ..., tab_equip = st.tabs([..., "Equipment"])
#   with tab_equip:
#       tab_equipment.render(
#           route_results=route_results,
#           freight_data=freight_data,
#           macro_data=macro_data,
#       )
