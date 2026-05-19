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

from typing import Any, Dict, List, Tuple

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
    compute_equipment_adjusted_rate,
    get_global_equipment_index,
    get_reefer_summary,
    get_trade_imbalance,
)
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_BORDER,
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
    RISK_COLORS,
    alert_banner,
    apply_dark_layout,
    badge,
    gradient_card,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Domain extensions to ui.styles palette ─────────────────────────────────
# Used for status/age/commodity differentiation beyond the core WSJ palette.
_TEAL    = "#14b8a6"   # prime-age fleet, seafood
_ROSE    = "#f43f5e"   # reefer commodity / decline marker
_ORANGE  = "#f97316"   # aging fleet, citrus

# Heatmap color scale: green (surplus) → amber → red (critical)
_UTIL_COLORSCALE = [
    [0.00, "#064e3b"],
    [0.40, "#2e9e6e"],
    [0.60, "#c9962b"],
    [0.80, "#c0392b"],
    [1.00, "#7f1d1d"],
]

_TYPE_LABELS: Dict[str, str] = {
    "20FT_DRY":    "20ft Dry",
    "40FT_DRY":    "40ft Dry",
    "40FT_HC":     "40ft HC",
    "40FT_REEFER": "40ft Reefer",
    "20FT_TANK":   "20ft Tank",
}

_REGION_COLORS: Dict[str, str] = {
    "Asia Pacific":  C_ACCENT,
    "North America": C_HIGH,
    "Europe":        C_CONV,
    "South America": C_MOD,
    "Middle East":   C_MACRO,
    "Africa":        C_LOW,
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
    {"bracket": "0–5 yrs",   "pct": 28.5, "status": "New",     "color": C_HIGH,  "note": "Post-2020 newbuild surge"},
    {"bracket": "5–10 yrs",  "pct": 22.0, "status": "Prime",   "color": _TEAL,   "note": "Peak productivity"},
    {"bracket": "10–15 yrs", "pct": 19.5, "status": "Mid-life","color": C_ACCENT,   "note": "Approaching major survey"},
    {"bracket": "15–20 yrs", "pct": 16.0, "status": "Aging",   "color": C_MOD,  "note": "Maintenance costs rising"},
    {"bracket": "20–25 yrs", "pct": 9.5,  "status": "Old",     "color": _ORANGE, "note": "Replacement candidates"},
    {"bracket": "25+ yrs",   "pct": 4.5,  "status": "EOL",     "color": C_LOW,    "note": "End-of-life / scrapping"},
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
    {"name": "Bananas",          "share_pct": 22, "peak_months": "Oct–Mar peak",    "key_origins": "Ecuador, Colombia, Costa Rica", "color": C_MOD},
    {"name": "Meat & Poultry",   "share_pct": 18, "peak_months": "Nov–Jan",         "key_origins": "Brazil, Australia, USA",        "color": C_LOW},
    {"name": "Avocados",         "share_pct": 10, "peak_months": "Mar–Aug",         "key_origins": "Mexico, Peru, South Africa",    "color": C_HIGH},
    {"name": "Pharmaceuticals",  "share_pct":  9, "peak_months": "Stable",          "key_origins": "Europe, India, USA",            "color": C_ACCENT},
    {"name": "Citrus Fruit",     "share_pct":  8, "peak_months": "Apr–Sep",         "key_origins": "South Africa, Spain, Argentina","color": _ORANGE},
    {"name": "Seafood",          "share_pct": 10, "peak_months": "Oct–Dec",         "key_origins": "Norway, Chile, Vietnam",        "color": C_MACRO},
    {"name": "Wine & Beer",      "share_pct":  6, "peak_months": "Sep–Dec",         "key_origins": "France, Australia, Chile",      "color": C_CONV},
    {"name": "Other Perishables","share_pct": 17, "peak_months": "Variable",        "key_origins": "Global",                        "color": C_TEXT3},
]


# ── Cell formatters for wsj_market_table() ────────────────────────────────
# wsj_market_table renders cell strings as raw HTML inside <td>. The table
# CSS already handles alignment, rule lines, and hover. These helpers only
# style content (font family + conditional color). Mirrors the pattern in
# ui/tab_rate_analytics.py.

def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
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
    KPI hero strip, scattergeo balance map, repositioning cost bar, port dwell
    table, and shortage alert panel — all rendered through the shared design
    system (metric_card_row, apply_dark_layout, wsj_market_table, insight_card_html).
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
    crit_high_count = sum(
        1 for a in _SHORTAGE_ALERT_ROUTES if a["risk"] in ("CRITICAL", "HIGH")
    )

    metric_card_row([
        {"label": "Global TEU Pool",       "value": f"{pool['total_teu_m']}M TEU",
         "accent": C_ACCENT, "sublabel": "world fleet all types"},
        {"label": "Active Utilization",    "value": f"{global_util:.1f}%",
         "accent": C_HIGH if global_util < 80 else C_MOD,
         "sublabel": "loaded + in-service containers"},
        {"label": "Repositioning (Empty)", "value": f"{reposition_count_k:,}K TEU",
         "accent": C_MOD, "sublabel": "currently in empty transit"},
        {"label": "Shortage Risk Routes",  "value": str(crit_high_count),
         "accent": C_LOW, "sublabel": "CRITICAL or HIGH shortage"},
    ], columns=4)

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
            geo_colors.append(C_HIGH)
        elif g["balance"] > 0:
            geo_colors.append(_TEAL)
        elif g["balance"] > -20:
            geo_colors.append(C_MOD)
        else:
            geo_colors.append(C_LOW)
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
        textfont=dict(color=C_TEXT, size=11, family="Libre Franklin, sans-serif"),
        marker=dict(
            size=geo_sizes,
            color=geo_colors,
            opacity=0.82,
            line=dict(color="rgba(255,255,255,0.3)", width=1.5),
        ),
        customdata=hover_texts,
        hovertemplate="%{customdata}<extra></extra>",
    ))
    apply_dark_layout(fig_geo, height=380, showlegend=False)
    fig_geo.update_layout(
        geo=dict(
            showland=True, landcolor="#12151e",
            showocean=True, oceancolor="#0c0e14",
            showlakes=False,
            showcountries=True, countrycolor="rgba(232,230,225,0.05)",
            showframe=False,
            bgcolor=C_BG,
            projection_type="natural earth",
        ),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig_geo, use_container_width=True, key="new_equip_geo_map")

    # Color legend — dot markers with class-based layout, only color is inline
    legend_items = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px;">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
        f'background:{col};flex-shrink:0;"></span>'
        f'<span class="wsj-byline" style="text-transform:none;letter-spacing:0;">{lbl}</span>'
        f'</span>'
        for col, lbl in [
            (C_HIGH, "Large surplus (>+20)"),
            (_TEAL,  "Slight surplus"),
            (C_MOD, "Slight deficit"),
            (C_LOW,   "Large deficit (<-20)"),
        ]
    )
    st.markdown(
        f'<div class="wsj-body">{legend_items}</div>',
        unsafe_allow_html=True,
    )

    st.markdown(source_footer([
        {"name": "Drewry Container Equipment Index", "kind": "modeled", "quality": "demo"},
        {"name": "Internal regional balance model",  "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)

    section_divider()

    # ── REPOSITIONING COST BAR CHART ──────────────────────────────────────
    section_header(
        "Repositioning Cost by Route",
        "Cost to move empty containers back to cargo origin. "
        "Export-heavy routes carry the highest hidden repositioning surcharge.",
    )

    repo_sorted = sorted(_REPO_COST_ROUTES, key=lambda r: r["cost_feu"], reverse=True)
    repo_colors = [
        C_LOW if r["risk"] == "HIGH" else (C_MOD if r["risk"] == "MODERATE" else C_HIGH)
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
    apply_dark_layout(fig_repo2, height=320, showlegend=False)
    fig_repo2.update_layout(
        xaxis={"title": "Repositioning Cost (USD/FEU)",
               "tickfont": {"color": C_TEXT3, "size": 10}},
        yaxis={"tickfont": {"color": C_TEXT2, "size": 10}},
        margin={"l": 230, "r": 80, "t": 25, "b": 30},
    )
    st.plotly_chart(fig_repo2, use_container_width=True, key="new_equip_repo_cost_bar")

    st.markdown(source_footer([
        {"name": "Drewry Container Equipment Index",            "kind": "modeled", "quality": "demo"},
        {"name": "Carrier-disclosed repositioning surcharges",  "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)

    section_divider()

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
                tt_colors.append(C_LOW)
            elif p["dwell"] >= 6.5:
                tt_colors.append(C_MOD)
            elif p["dwell"] >= 4.5:
                tt_colors.append(C_ACCENT)
            else:
                tt_colors.append(C_HIGH)

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
        apply_dark_layout(fig_tt, height=340, showlegend=False)
        fig_tt.update_layout(
            xaxis={"title": "Dwell Days",
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis={"tickfont": {"color": C_TEXT2, "size": 10}},
            margin={"l": 110, "r": 70, "t": 20, "b": 30},
        )
        st.plotly_chart(fig_tt, use_container_width=True, key="new_equip_turntime_bar")

    with col_tt_cards:
        st.markdown('<div class="sub-section-header">Port Detail</div>',
                    unsafe_allow_html=True)
        port_rows = []
        for p in _TURN_TIME_HIGHLIGHT:
            rc = RISK_COLORS.get(p["risk"], C_TEXT2)
            port_rows.append([
                _sans(p["port"], color=C_TEXT, weight=700),
                _sans(p["region"], color=C_TEXT3),
                _mono(f"{p['dwell']}d", color=rc),
                badge(p["risk"], color=rc),
            ])
        wsj_market_table(["Port", "Region", "Dwell", "Risk"], port_rows)

    st.markdown(source_footer([
        {"name": "S&P Global Port Performance",  "kind": "modeled", "quality": "demo"},
        {"name": "Internal dwell tracking",      "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)

    section_divider()

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
        alert_banner(f"CRITICAL: {desc} — immediate sourcing action required.", level="critical")
    if high_alerts:
        desc = ", ".join(a["route"] for a in high_alerts)
        st.warning(f"HIGH risk: {desc} — book within 48 hours to secure equipment.", icon="⚠️")

    risk_to_action = {"CRITICAL": "Avoid", "HIGH": "Caution", "MODERATE": "Monitor", "LOW": "Watch"}

    alert_cols = st.columns(2)
    for i, alert in enumerate(_SHORTAGE_ALERT_ROUTES):
        action = risk_to_action.get(alert["risk"], "Monitor")
        score = max(0.0, min(1.0, alert["util"] / 100.0))
        rationale = (
            f"{alert['util']}% util · {alert['shortfall_teu']:,} TEU shortfall · "
            f"+{alert['rate_premium_pct']}% rate premium. {alert['detail']}"
        )
        with alert_cols[i % 2]:
            st.markdown(insight_card_html(
                title=alert["route"],
                score=score,
                action=action,
                rationale=rationale,
                category="ROUTE",
            ), unsafe_allow_html=True)

    st.markdown(source_footer([
        {"name": "Drewry Container Equipment Index", "kind": "modeled", "quality": "demo"},
        {"name": "Internal route shortage model",    "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)

    section_divider("Global TEU Pool")


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
        idx_label, idx_color = "TIGHT", C_LOW
    elif global_idx >= 70:
        idx_label, idx_color = "NORMAL", C_MOD
    else:
        idx_label, idx_color = "SURPLUS", C_HIGH

    # ── Row 1: KPI cards ─────────────────────────────────────────────────
    metric_card_row([
        {"label": "Total World Fleet",    "value": f"{pool['total_teu_m']}M TEU",
         "accent": C_ACCENT,  "sublabel": "all container types"},
        {"label": "Active in Service",    "value": f"{pool['active_pct']}%",
         "accent": C_HIGH,
         "sublabel": f"{pool['total_teu_m']*pool['active_pct']/100:.1f}M TEU loaded/moving"},
        {"label": "Empty Repositioning",  "value": f"{pool['repositioning_pct']}%",
         "accent": C_MOD,     "sublabel": "TEU currently in empty transit"},
        {"label": "Idle / Awaiting",      "value": f"{pool['idle_pct']}%",
         "accent": C_LOW,     "sublabel": "parked, not yet deployed"},
        {"label": "Weighted Utilization", "value": f"{global_idx}%",
         "accent": idx_color, "sublabel": idx_label},
    ], columns=5)

    st.write("")

    # ── Row 2: Fleet composition donut + YoY metrics ─────────────────────
    col_donut, col_fleet, col_reposition = st.columns([2, 2, 3])

    with col_donut:
        # Fleet ownership donut
        fig_own = go.Figure(go.Pie(
            labels=["Carrier-Owned", "Leased from Lessors"],
            values=[pool["owned_pct"], pool["leased_pct"]],
            hole=0.62,
            marker_colors=[C_ACCENT, C_CONV],
            textinfo="label+percent",
            textfont={"color": C_TEXT, "size": 11},
            hovertemplate="%{label}: %{value}%<extra></extra>",
        ))
        fig_own.add_annotation(
            text="<b>Fleet<br>Ownership</b>",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT2, "size": 11},
        )
        apply_dark_layout(fig_own, height=230, showlegend=False)
        fig_own.update_layout(
            margin={"l": 10, "r": 10, "t": 20, "b": 10},
            paper_bgcolor=C_CARD,
        )
        st.plotly_chart(fig_own, use_container_width=True, key="equip_own_donut")
        st.markdown(source_footer([
            {"name": "Alphaliner Fleet Database",      "kind": "modeled", "quality": "demo"},
            {"name": "Drewry Container Forecast",      "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)

    with col_fleet:
        # Fleet status donut
        fig_status = go.Figure(go.Pie(
            labels=["Active", "Repositioning Empty", "Idle"],
            values=[pool["active_pct"], pool["repositioning_pct"], pool["idle_pct"]],
            hole=0.62,
            marker_colors=[C_HIGH, C_MOD, C_LOW],
            textinfo="label+percent",
            textfont={"color": C_TEXT, "size": 11},
            hovertemplate="%{label}: %{value}%<extra></extra>",
        ))
        fig_status.add_annotation(
            text="<b>Fleet<br>Status</b>",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT2, "size": 11},
        )
        apply_dark_layout(fig_status, height=230, showlegend=False)
        fig_status.update_layout(
            margin={"l": 10, "r": 10, "t": 20, "b": 10},
            paper_bgcolor=C_CARD,
        )
        st.plotly_chart(fig_status, use_container_width=True, key="equip_status_donut")
        st.markdown(source_footer([
            {"name": "Alphaliner Fleet Database",      "kind": "modeled", "quality": "demo"},
            {"name": "Drewry Container Forecast",      "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)

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
            marker_color=C_HIGH,
            marker_opacity=0.85,
            hovertemplate="%{y}: %{x:.1f}K TEU surplus-days<extra></extra>",
        ))
        fig_repo.add_trace(go.Bar(
            y=[d["region"] for d in reposition_data],
            x=[-d["deficit"] for d in reposition_data],
            name="Deficit (days short)",
            orientation="h",
            marker_color=C_LOW,
            marker_opacity=0.85,
            hovertemplate="%{y}: %{x:.1f}K TEU deficit-days<extra></extra>",
        ))
        apply_dark_layout(
            fig_repo,
            title="Surplus / Deficit by Region (TEU-days index)",
            height=230,
        )
        fig_repo.update_layout(
            barmode="overlay",
            xaxis={"title": "← Deficit  |  Surplus →",
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
            margin={"l": 100, "r": 20, "t": 35, "b": 30},
            legend={"orientation": "h", "y": -0.22,
                    "font": {"color": C_TEXT3, "size": 10}},
            shapes=[{"type": "line", "x0": 0, "x1": 0, "y0": -0.5,
                      "y1": len(REGIONS) - 0.5,
                      "line": {"color": "rgba(255,255,255,0.3)", "width": 1}}],
        )
        st.plotly_chart(fig_repo, use_container_width=True, key="equip_repo_bar")
        st.markdown(source_footer([
            {"name": "Alphaliner Fleet Database",      "kind": "modeled", "quality": "demo"},
            {"name": "Drewry Container Forecast",      "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)

    # ── Fleet growth strip ─────────────────────────────────────────────
    yoy_growth = pool["yoy_fleet_growth"]
    net_add    = pool["newbuild_delivery_m"] - pool["scrapping_m"]
    metric_card_row([
        {"label": "YoY Fleet Growth",      "value": f"+{yoy_growth}%",
         "accent": C_HIGH if yoy_growth >= 0 else C_LOW},
        {"label": "Newbuild Deliveries",   "value": f"{pool['newbuild_delivery_m']}M TEU",
         "accent": C_ACCENT},
        {"label": "Scrappings",            "value": f"{pool['scrapping_m']}M TEU",
         "accent": C_MOD},
        {"label": "Net Fleet Addition",    "value": f"+{net_add:.1f}M TEU",
         "accent": C_MACRO if net_add >= 0 else C_LOW},
        {"label": "Global Utilization Idx","value": f"{global_idx}%",
         "accent": idx_color, "sublabel": idx_label},
        {"label": "Empty Repositioning %", "value": f"{pool['repositioning_pct']}%",
         "accent": C_MOD if pool["repositioning_pct"] >= 18 else C_HIGH},
    ], columns=6)


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
        alert_banner(f"CRITICAL shortage: {crit_desc} — expect significant rate premiums and booking delays.", level="critical")

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
            textfont={"size": 11, "color": "#e8e6e1"},
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
                "bgcolor": C_SURFACE,
                "bordercolor": C_BORDER,
                "borderwidth": 1,
                "len": 0.85,
            },
            xgap=3,
            ygap=3,
        ))
        apply_dark_layout(fig, height=320, showlegend=False)
        fig.update_layout(
            xaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
            yaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
            margin={"l": 110, "r": 20, "t": 20, "b": 40},
        )
        st.plotly_chart(fig, use_container_width=True, key="equip_heatmap")

        # Risk legend
        legend_html = " &nbsp; ".join(badge(r, color=RISK_COLORS[r]) for r in ["LOW","MODERATE","HIGH","CRITICAL"])
        st.markdown(
            f'<div class="wsj-byline">Shortage Risk: {legend_html}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(source_footer([
            {"name": "Drewry Container Equipment Index", "kind": "modeled", "quality": "demo"},
            {"name": "Alphaliner Fleet Database",        "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)

    with col_detail:
        # Per-region summary cards
        for region in REGIONS:
            region_equip = [e for e in REGIONAL_EQUIPMENT_STATUS if e.region == region]
            avg_util = (sum(e.utilization_pct for e in region_equip) / len(region_equip)
                        if region_equip else 0.0)
            total_k  = sum(e.available_units_k for e in region_equip)
            worst    = max(region_equip, key=lambda e: e.utilization_pct, default=None)
            color    = _REGION_COLORS.get(region, C_TEXT2)
            worst_risk = worst.shortage_risk if worst else "LOW"
            risk_tag   = badge(worst_risk, color=RISK_COLORS.get(worst_risk, C_TEXT2))

            region_content = (
                f'<span class="port-name" style="font-size:0.82rem;">{region}</span>'
                f'<span style="float:right;font-family:var(--sans);font-size:0.78rem;'
                f'font-weight:700;color:{color};">{avg_util:.0f}% avg util</span>'
                f'<span class="port-detail" style="display:block;margin-top:5px;">'
                f'<span>{total_k:.0f}K TEU tracked</span>'
                f'<span style="float:right;">{risk_tag}</span></span>'
            )
            st.markdown(
                gradient_card(region_content, border_color=color),
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
            C_LOW if c >= 400 else (C_MOD if c >= 250 else C_HIGH)
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
        apply_dark_layout(
            fig_bar,
            title="Repositioning Cost / FEU (USD)",
            height=480,
            showlegend=False,
        )
        fig_bar.update_layout(
            xaxis={"title": "USD per FEU",
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis={"tickfont": {"color": C_TEXT2, "size": 10}},
            margin={"l": 160, "r": 60, "t": 40, "b": 30},
        )
        st.plotly_chart(fig_bar, use_container_width=True, key="equip_reposition_bar")
        st.markdown(source_footer([
            {"name": "Drewry Container Equipment Index", "kind": "modeled", "quality": "demo"},
            {"name": "Carrier reposition cost surveys",   "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)

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
            "rgba(53,114,176,0.85)",
            "rgba(46,158,110,0.85)",
            "rgba(124,110,175,0.85)",
            "rgba(201,150,43,0.85)",
            "rgba(6,182,212,0.85)",
            "rgba(192,57,43,0.85)",
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
                color = "rgba(53,114,176,0.55)"
                label = (f"Loaded — ${m.empty_container_repositioning_cost_per_feu:,}/FEU "
                         f"reposition cost | IR: {m.imbalance_ratio:.2f}")
            else:
                vol   = max((2.0 - m.imbalance_ratio) * 8, 3)
                color = "rgba(100,116,139,0.28)"
                label = (f"Empty repositioning — {m.repositioning_days}d | "
                         f"${m.empty_container_repositioning_cost_per_feu:,}/FEU")
            sources.append(src)
            targets.append(tgt)
            values.append(vol)
            link_colors.append(color)
            link_labels.append(label)

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
            apply_dark_layout(fig_sk, height=480, showlegend=False)
            fig_sk.update_layout(
                margin={"l": 10, "r": 10, "t": 35, "b": 20},
                title={"text": "Trade Flow: Loaded (blue) vs Empty Repositioning (gray)",
                       "font": {"size": 12, "color": C_TEXT2}, "x": 0.01},
            )
            st.plotly_chart(fig_sk, use_container_width=True, key="equip_sankey")

    # Repositioning stats strip
    avg_cost = sum(m.empty_container_repositioning_cost_per_feu for m in TRADE_IMBALANCE_DATA) / len(TRADE_IMBALANCE_DATA)
    max_route = max(TRADE_IMBALANCE_DATA, key=lambda m: m.empty_container_repositioning_cost_per_feu)
    avg_days  = sum(m.repositioning_days for m in TRADE_IMBALANCE_DATA) / len(TRADE_IMBALANCE_DATA)

    metric_card_row([
        {"label": "Avg Repositioning Cost",
         "value": f"${avg_cost:,.0f}/FEU",
         "accent": C_MOD},
        {"label": "Highest Cost Route",
         "value": f"${max_route.empty_container_repositioning_cost_per_feu:,}",
         "accent": C_LOW,
         "sublabel": max_route.route_id.replace("_", " ").title()},
        {"label": "Avg Reposition Days",
         "value": f"{avg_days:.0f} days",
         "accent": C_ACCENT},
        {"label": "Routes Tracked",
         "value": str(len(TRADE_IMBALANCE_DATA)),
         "accent": C_TEXT},
    ], columns=4)

    # Per-route table
    table_rows = []
    for m in sorted_routes:
        cost = m.empty_container_repositioning_cost_per_feu
        if m.imbalance_ratio > 1.3:
            risk_level = "HIGH"
        elif m.imbalance_ratio < 0.8:
            risk_level = "MODERATE"
        else:
            risk_level = "LOW"
        cost_color = C_LOW if cost >= 400 else (C_MOD if cost >= 250 else C_HIGH)
        table_rows.append([
            _sans(m.route_id.replace("_", " ").title(), color=C_TEXT, weight=600),
            _mono(f"${cost:,}", color=cost_color),
            _mono(f"{m.repositioning_days}", color=C_TEXT2),
            badge(risk_level, color=RISK_COLORS.get(risk_level, C_TEXT2)),
        ])
    wsj_market_table(
        headers=["Route", "Cost / FEU", "Days", "Risk"],
        rows=table_rows,
    )
    st.markdown(source_footer([
        {"name": "Drewry Container Equipment Index", "kind": "modeled", "quality": "demo"},
        {"name": "Carrier reposition cost surveys",   "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)

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
                colors.append(C_LOW)
            elif d >= 6:
                colors.append(C_MOD)
            elif d >= 4:
                colors.append(C_ACCENT)
            else:
                colors.append(C_HIGH)

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
        apply_dark_layout(
            fig,
            title="Average Container Dwell Time (days) — Major Ports",
            height=max(320, len(filtered_sorted) * 26 + 60),
            showlegend=False,
        )
        fig.update_layout(
            xaxis={"title": "Dwell Days", "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis={"tickfont": {"color": C_TEXT2, "size": 10}},
            margin={"l": 110, "r": 70, "t": 40, "b": 30},
        )
        st.plotly_chart(fig, use_container_width=True, key="equip_dwell_bar")

    with col_cards:
        st.markdown('<div class="sub-section-header">Port Detail</div>',
                    unsafe_allow_html=True)
        _trend_color_map = {"improving": C_HIGH, "stable": C_MOD, "worsening": C_LOW}
        rows: List[List[str]] = []
        for p in filtered_sorted[:10]:  # show top 10
            d = p["dwell_days"]
            if d >= 8:
                dcolor = C_LOW
            elif d >= 6:
                dcolor = C_MOD
            elif d >= 4:
                dcolor = C_ACCENT
            else:
                dcolor = C_HIGH

            vs_avg = p["vs_avg"]
            vs_sign = "+" if vs_avg >= 0 else ""
            vs_color = C_LOW if vs_avg > 20 else (C_MOD if vs_avg > 0 else C_HIGH)
            r_color = _REGION_COLORS.get(p["region"], C_TEXT2)
            trend_text = p["trend"]
            trend_color = _trend_color_map.get(trend_text, C_TEXT2)

            rows.append([
                _sans(p["port"], color=C_TEXT, weight=700),
                _sans(p["region"], color=r_color),
                _mono(f"{d}d", color=dcolor),
                _mono(f"{vs_sign}{vs_avg}%", color=vs_color),
                badge(trend_text, color=trend_color),
            ])
        wsj_market_table(
            headers=["Port", "Region", "Dwell Days", "vs Avg", "Trend"],
            rows=rows,
        )

    # Dwell summary stats
    all_dwell = [p["dwell_days"] for p in filtered]
    if all_dwell:
        avg_d  = sum(all_dwell) / len(all_dwell)
        worst_p = max(filtered, key=lambda p: p["dwell_days"])
        score = max(0.0, min(1.0, worst_p["dwell_days"] / 12.0))
        st.markdown(
            insight_card_html(
                title="Worst Port Dwell Times",
                score=score,
                action="Caution",
                rationale=(
                    f"{worst_p['port']} leads at {worst_p['dwell_days']}d "
                    f"vs selection average {avg_d:.1f}d across {len(filtered)} ports."
                ),
                category="PORT_DEMAND",
            ),
            unsafe_allow_html=True,
        )

    st.markdown(
        source_footer([
            {"name": "UNCTAD Port Productivity",   "kind": "modeled", "quality": "demo"},
            {"name": "Drewry Port Tariff Monitor", "kind": "modeled", "quality": "demo"},
        ]),
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

    metric_card_row([
        {"label": "Avg Reefer Utilization", "value": f"{avg_util}%",
         "accent": C_LOW,    "sublabel": "capacity-weighted"},
        {"label": "Total Reefer Units",     "value": f"{total_k:.0f}K",
         "accent": C_ACCENT, "sublabel": "units tracked"},
        {"label": "Avg Daily Lease Rate",   "value": f"${avg_rate:.2f}/day",
         "accent": C_MOD,    "sublabel": "per 40ft reefer unit"},
        {"label": "Premium vs Dry Box",     "value": f"{premium_x}×",
         "accent": C_CONV,   "sublabel": "daily lease rate multiple"},
        {"label": "Critical Regions",       "value": str(len(crit_regions)),
         "accent": _ROSE,    "sublabel": "CRITICAL shortage"},
    ], columns=5)

    st.write("")

    col_left, col_right = st.columns([3, 2])

    with col_left:
        # Regional reefer utilization + lease rate dual-axis
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        reg_names  = [e.region for e in reefers]
        util_vals  = [e.utilization_pct for e in reefers]
        rate_vals  = [e.daily_lease_rate_usd for e in reefers]
        bar_colors = [
            RISK_COLORS.get(e.shortage_risk, C_TEXT2) for e in reefers
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
            line={"color": C_MOD, "width": 2.5},
            marker={"size": 10, "color": C_MOD,
                    "line": {"color": C_BG, "width": 2}},
            text=[f"${r:.2f}" for r in rate_vals],
            textposition="top center",
            textfont={"color": C_MOD, "size": 10},
            hovertemplate="%{x}: $%{y:.2f}/day<extra></extra>",
        ), secondary_y=True)

        # Danger threshold line
        fig.add_hline(
            y=90, line={"color": "rgba(192,57,43,0.5)", "dash": "dash", "width": 1.5},
            annotation_text="90% danger zone",
            annotation_font={"color": C_LOW, "size": 10},
            secondary_y=False,
        )

        apply_dark_layout(
            fig,
            title="Reefer Utilization (bars) & Daily Lease Rate (line) by Region",
            height=320,
        )
        fig.update_layout(
            yaxis={"title": "Utilization %", "range": [70, 100],
                   "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis2={"title": {"text": "USD/day", "font": {"color": C_MOD}},
                    "range": [2.5, 5.0],
                    "tickfont": {"color": C_MOD, "size": 10}},
            margin={"l": 50, "r": 60, "t": 45, "b": 30},
            legend={"orientation": "h", "y": -0.22, "font": {"color": C_TEXT3, "size": 10}},
        )
        st.plotly_chart(fig, use_container_width=True, key="equip_reefer_util")

        # Seasonal demand chart
        months = _REEFER_SEASONAL["labels"]
        fig2 = go.Figure()
        seasonal_colors = {
            "Global": C_TEXT2,
            "South America": C_MOD,
            "Europe": C_CONV,
            "Asia Pacific": C_ACCENT,
            "North America": C_HIGH,
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
        apply_dark_layout(
            fig2,
            title="Reefer Seasonal Demand Index (100 = annual avg)",
            height=270,
        )
        fig2.update_layout(
            yaxis={"range": [60, 145]},
            margin={"l": 40, "r": 60, "t": 40, "b": 20},
            legend={"orientation": "h", "y": -0.28, "font": {"color": C_TEXT3, "size": 10}},
        )
        st.plotly_chart(fig2, use_container_width=True, key="equip_reefer_seasonal")

        st.markdown(source_footer([
            {"name": "Drewry Reefer Container Forecast", "kind": "modeled", "quality": "demo"},
            {"name": "USDA / IFPRI seasonal trade flows", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)

    with col_right:
        # Reefer commodity breakdown
        st.markdown('<div class="sub-section-header">Top Reefer Commodities</div>',
                    unsafe_allow_html=True)
        for comm in _REEFER_COMMODITIES:
            score = min(comm["share_pct"] / 25.0, 1.0)
            st.markdown(insight_card_html(
                title=f"{comm['name']} — {comm['share_pct']}%",
                score=score,
                action="Watch",
                rationale=f"Peak: {comm['peak_months']} · Origins: {comm['key_origins']}",
                category="REEFER",
            ), unsafe_allow_html=True)

        # Deficit days by region summary
        st.markdown('<div class="sub-section-header">Reefer Deficit Days by Region</div>',
                    unsafe_allow_html=True)
        deficit_rows = []
        for e in reefers:
            d = e.days_surplus_deficit
            label = f"{abs(d)}d deficit" if d < 0 else f"{d}d surplus"
            sign_color = C_LOW if d < 0 else C_HIGH
            deficit_rows.append([
                _sans(e.region, color=C_TEXT, weight=600),
                _mono(label, color=sign_color),
            ])
        wsj_market_table(["Region", "Reefer Status"], deficit_rows)

    st.markdown(source_footer([
        {"name": "Drewry Reefer Container Forecast",  "kind": "modeled", "quality": "demo"},
        {"name": "Internal regional shortage tracker", "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)


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

    metric_card_row([
        {"label": "Active Alerts", "value": str(len(alerts)), "accent": C_LOW},
        {"label": "Critical",      "value": str(crit_count),  "accent": C_LOW},
        {"label": "High Risk",     "value": str(high_count),  "accent": C_MOD},
    ], columns=3)
    st.markdown(
        '<div class="wsj-body">'
        'Alerts represent region × container-type combinations where '
        'utilization and deficit days indicate shortage risk to booked cargo. '
        'Rate premiums of 15–45% above baseline are typical in CRITICAL conditions.'
        '</div>',
        unsafe_allow_html=True,
    )

    risk_to_action = {"CRITICAL": "Avoid", "HIGH": "Caution", "MODERATE": "Monitor", "LOW": "Watch"}

    col_a, col_b = st.columns(2)
    cols = [col_a, col_b]
    for i, alert in enumerate(alerts):
        action = risk_to_action.get(alert["risk"], "Monitor")
        score = max(0.0, min(1.0, alert["util"] / 100.0))
        yoy_sign = "+" if alert["yoy"] >= 0 else ""
        deficit_label = (
            f"{abs(alert['deficit_d'])}d deficit" if alert["deficit_d"] < 0
            else f"{alert['deficit_d']}d surplus"
        )
        rationale = (
            f"Primary exposure: {alert['route']} — "
            f"{alert['util']:.0f}% util, {deficit_label}, "
            f"${alert['rate']:.2f}/day lease ({yoy_sign}{alert['yoy']:.1f}pp YoY)"
        )
        with cols[i % 2]:
            st.markdown(insight_card_html(
                title=f"{alert['region']} — {alert['type']}",
                score=score,
                action=action,
                rationale=rationale,
                category="ROUTE",
            ), unsafe_allow_html=True)

    st.markdown(source_footer([
        {"name": "Drewry Container Equipment Index", "kind": "modeled", "quality": "demo"},
        {"name": "Internal route congestion model",  "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)


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
            marker_line={"color": C_BG, "width": 2},
            textinfo="percent",
            textfont={"color": "#e8e6e1", "size": 11},
            hovertemplate="%{label}<br>%{value}% of fleet<extra></extra>",
        ))
        fig.add_annotation(
            text="<b>Fleet<br>Age Mix</b>",
            x=0.5, y=0.5, showarrow=False,
            font={"color": C_TEXT2, "size": 12},
        )
        apply_dark_layout(fig, height=300, showlegend=False)
        fig.update_layout(
            margin={"l": 10, "r": 10, "t": 30, "b": 10},
            title={"text": "Global Fleet Age Profile", "font": {"size": 12, "color": C_TEXT2}, "x": 0.01},
        )
        st.plotly_chart(fig, use_container_width=True, key="equip_age_donut")
        st.markdown(
            source_footer([{"name": "BRS Alphaliner Fleet Database", "kind": "modeled", "quality": "demo"}]),
            unsafe_allow_html=True,
        )

    with col_table:
        st.markdown('<div class="sub-section-header">Age Bracket Details</div>',
                    unsafe_allow_html=True)
        global_fleet = _GLOBAL_TEU_POOL["total_teu_m"]
        age_metrics = [
            {
                "label":    b["bracket"],
                "value":    f"{b['pct']}%",
                "accent":   b["color"],
                "delta":    f"{global_fleet * b['pct'] / 100:.2f}M TEU",
                "sublabel": f"{b['status']} · {b['note']}",
            }
            for b in _FLEET_AGE_DIST
        ]
        metric_card_row(age_metrics, columns=6)

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
            line={"color": _ROSE, "width": 2.5},
            marker={"size": 9, "color": _ROSE, "line": {"color": C_BG, "width": 2}},
            hovertemplate="%{x}: urgency score %{y}<extra></extra>",
        ), secondary_y=True)

        apply_dark_layout(
            fig2,
            title="Fleet Volume (bars) & Replacement Urgency Score (line)",
            height=300,
        )
        fig2.update_layout(
            yaxis={"title": "M TEU", "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis2={"title": {"text": "Urgency (0–100)", "font": {"color": _ROSE}},
                    "range": [0, 120],
                    "tickfont": {"color": _ROSE, "size": 10}},
            margin={"l": 50, "r": 60, "t": 45, "b": 50},
            xaxis={"tickfont": {"color": C_TEXT2, "size": 10}},
            legend={"orientation": "h", "y": -0.28, "font": {"color": C_TEXT3, "size": 10}},
        )
        st.plotly_chart(fig2, use_container_width=True, key="equip_age_bars")
        st.markdown(
            source_footer([{"name": "BRS Alphaliner Fleet Database", "kind": "modeled", "quality": "demo"}]),
            unsafe_allow_html=True,
        )

        # Replacement need callout
        eol_pct = _FLEET_AGE_DIST[-1]["pct"] + _FLEET_AGE_DIST[-2]["pct"]
        eol_teu = _GLOBAL_TEU_POOL["total_teu_m"] * eol_pct / 100
        urgency_score = max(0.0, min(1.0, eol_pct / 30.0))
        st.markdown(
            insight_card_html(
                title="Fleet Replacement Urgency",
                score=urgency_score,
                action="Caution",
                rationale=(
                    f"{eol_pct:.1f}% of global fleet ({eol_teu:.2f}M TEU) is 20+ years old "
                    f"and represents near-term scrapping/replacement demand. "
                    f"At current newbuild pricing ($3,800–$28,000/unit), total replacement "
                    f"capex across the aging bracket is estimated at $80–120B over 5 years."
                ),
                category="MACRO",
            ),
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
            marker_color=C_ACCENT,
            marker_opacity=0.85,
            hovertemplate="%{x}<br>Own: $%{y:.2f}/day<extra></extra>",
        ), secondary_y=False)
        fig.add_trace(go.Bar(
            x=types, y=lease_d,
            name="Lease Rate (USD/day)",
            marker_color=C_CONV,
            marker_opacity=0.85,
            hovertemplate="%{x}<br>Lease: $%{y:.2f}/day<extra></extra>",
        ), secondary_y=False)
        prem_colors = [
            C_HIGH if p < 0 else (C_MOD if p < 30 else C_LOW)
            for p in premium
        ]
        fig.add_trace(go.Scatter(
            x=types, y=premium,
            name="Lease Premium vs Own (%)",
            mode="lines+markers+text",
            line={"color": C_MOD, "width": 2.5},
            marker={"size": 10, "color": prem_colors,
                    "line": {"color": C_BG, "width": 2}},
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

        apply_dark_layout(
            fig,
            title="Daily Cost: Own vs Lease (bars) + Premium % (line)",
            height=340,
        )
        fig.update_layout(
            barmode="group",
            yaxis={"title": "USD/day", "tickfont": {"color": C_TEXT3, "size": 10}},
            yaxis2={"title": {"text": "Lease Premium %", "font": {"color": C_MOD}},
                    "tickfont": {"color": C_MOD, "size": 10},
                    "zeroline": False},
            margin={"l": 50, "r": 60, "t": 45, "b": 50},
            xaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
            legend={"orientation": "h", "y": -0.28, "font": {"color": C_TEXT3, "size": 10}},
        )
        st.plotly_chart(fig, use_container_width=True, key="equip_lease_own_chart")

        # Breakeven years chart
        fig_be = go.Figure(go.Bar(
            x=types,
            y=[r["breakeven_yrs"] for r in _LEASE_VS_OWN],
            marker_color=[C_HIGH if r["breakeven_yrs"] <= 5 else C_MOD
                          for r in _LEASE_VS_OWN],
            marker_opacity=0.85,
            text=[f"{r['breakeven_yrs']}y" for r in _LEASE_VS_OWN],
            textposition="outside",
            textfont={"color": C_TEXT2, "size": 10},
            hovertemplate="%{x}: break-even in %{y}yr<extra></extra>",
        ))
        fig_be.add_hline(
            y=5, line={"color": "rgba(46,158,110,0.4)", "dash": "dash", "width": 1.5},
            annotation_text="5yr threshold",
            annotation_font={"color": C_HIGH, "size": 10},
        )
        apply_dark_layout(
            fig_be,
            title="Ownership Break-Even vs Leasing (years)",
            height=210,
            showlegend=False,
        )
        fig_be.update_layout(
            yaxis={"title": "Years", "tickfont": {"color": C_TEXT3, "size": 10}},
            xaxis={"tickfont": {"color": C_TEXT2, "size": 11}},
            margin={"l": 40, "r": 20, "t": 40, "b": 30},
        )
        st.plotly_chart(fig_be, use_container_width=True, key="equip_breakeven_chart")

        st.markdown(source_footer([
            {"name": "Drewry Container Census",            "kind": "modeled", "quality": "demo"},
            {"name": "Triton / Textainer fleet disclosures", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)

    with col_table:
        st.markdown('<div class="sub-section-header">Lease/Own Detail by Type</div>',
                    unsafe_allow_html=True)
        for r in _LEASE_VS_OWN:
            prem = r["lease_premium"]
            pref = (
                "Leasing preferred" if prem < 0
                else ("Ownership preferred" if prem > 35 else "Market-dependent")
            )
            score = max(0.0, min(1.0, (prem + 50) / 100.0))
            rationale = (
                f"Capex ${r['own_capex_usd']:,} · Own ${r['own_daily_usd']:.2f}/d · "
                f"Lease ${r['lease_daily']:.2f}/d · Break-even {r['breakeven_yrs']}y · "
                f"{r['market_trend']}"
            )
            st.markdown(insight_card_html(
                title=f"{r['type']} — lease premium {'+' if prem >= 0 else ''}{prem}%",
                score=score,
                action=pref,
                rationale=rationale,
                category="EQUIP",
            ), unsafe_allow_html=True)

        # Fleet strategy callout
        st.markdown(insight_card_html(
            title="Fleet Strategy Note",
            score=0.5,
            action="Watch",
            rationale=(
                "Major carriers (MSC, Maersk, CMA CGM) own 45–60% of their fleets "
                "for cost control. Lessors (Triton, Textainer, CAI) provide market "
                "flexibility. Post-2022 oversupply has pushed dry box lease rates to "
                "multi-year lows — favouring short-term lease strategies for shippers "
                "and carriers seeking to avoid overcapitalization."
            ),
            category="MACRO",
        ), unsafe_allow_html=True)

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
        imb_label, imb_color = "Export-heavy — empties flow back at cost", C_LOW
    elif metrics.imbalance_ratio < 0.8:
        imb_label, imb_color = "Import-heavy — carrier absorbs empty return", C_MOD
    else:
        imb_label, imb_color = "Near-balanced trade flow", C_HIGH

    st.write("")

    # KPI output row
    metric_card_row([
        {"label": "Base Freight Cost",    "value": f"${total_base:,.0f}",
         "accent": C_ACCENT, "sublabel": f"{feu_count:,.0f} FEU × ${base_rate_per_feu:,}"},
        {"label": "Repositioning Charge", "value": f"${total_reposition:,.0f}",
         "accent": C_LOW,    "sublabel": f"${reposition_per_feu:,}/FEU embedded surcharge"},
        {"label": "Equipment-Adj. Total", "value": f"${total_adjusted:,.0f}",
         "accent": C_MOD,    "sublabel": f"full cost for {feu_count:,.0f} FEU"},
        {"label": "Rate Uplift",          "value": f"{uplift_pct:.1f}%",
         "accent": C_CONV,   "sublabel": "repositioning as % of base rate"},
    ], columns=4)

    st.write("")

    # Detail strip
    metric_card_row([
        {"label": "Trade Imbalance Ratio", "value": f"{metrics.imbalance_ratio:.2f}:1",
         "accent": imb_color, "sublabel": imb_label},
        {"label": "Repositioning Days",    "value": f"{metrics.repositioning_days} days",
         "accent": C_TEXT,    "sublabel": "empty transit back to origin"},
        {"label": "Reposition per FEU",    "value": f"${reposition_per_feu:,.0f}",
         "accent": C_MOD,     "sublabel": "adds to eastbound rate"},
        {"label": "Adjusted Rate / FEU",   "value": f"${adjusted_rate:,.0f}",
         "accent": C_LOW,     "sublabel": f"vs base ${base_rate_per_feu:,}/FEU"},
    ], columns=4)

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
        increasing={"marker": {"color": C_LOW}},
        totals={"marker": {"color": C_MOD}},
        decreasing={"marker": {"color": C_HIGH}},
        hovertemplate="%{x}: $%{y:,}/FEU<extra></extra>",
    ))
    apply_dark_layout(
        fig_wf,
        title="Rate Build-Up Waterfall (USD/FEU)",
        height=280,
        showlegend=False,
    )
    fig_wf.update_layout(
        yaxis={"title": "USD/FEU"},
        margin={"l": 60, "r": 40, "t": 45, "b": 30},
    )
    st.plotly_chart(fig_wf, use_container_width=True, key="equip_waterfall")

    st.markdown(source_footer([
        {"name": "Drewry Container Equipment Index",        "kind": "modeled", "quality": "demo"},
        {"name": "Internal trade-imbalance / route model",  "kind": "modeled", "quality": "demo"},
    ]), unsafe_allow_html=True)

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
            marker={"size": 8, "color": color, "line": {"color": C_BG, "width": 1.5}},
            hovertemplate=f"{region} %{{x}}: %{{y}}<extra></extra>",
        ))

    # Zone bands
    fig.add_hrect(y0=0,   y1=35,  fillcolor="rgba(192,57,43,0.05)",   line_width=0,
                  annotation_text="Shortage zone",  annotation_position="left",
                  annotation_font={"color": C_LOW,   "size": 10})
    fig.add_hrect(y0=35,  y1=65,  fillcolor="rgba(201,150,43,0.04)",  line_width=0,
                  annotation_text="Transition",     annotation_position="left",
                  annotation_font={"color": C_MOD, "size": 10})
    fig.add_hrect(y0=65,  y1=100, fillcolor="rgba(46,158,110,0.04)",  line_width=0,
                  annotation_text="Surplus zone",  annotation_position="left",
                  annotation_font={"color": C_HIGH, "size": 10})

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

    apply_dark_layout(
        fig,
        title="Equipment Balance Index by Region (100 = well-supplied)",
        height=380,
    )
    fig.update_layout(
        xaxis={"title": "Year", "tickvals": years, "ticktext": [str(y) for y in years]},
        yaxis={"title": "Balance Index", "range": [0, 108]},
        margin={"l": 90, "r": 20, "t": 45, "b": 50},
        legend={"orientation": "h", "y": -0.22, "font": {"color": C_TEXT3, "size": 10}},
    )
    st.plotly_chart(fig, use_container_width=True, key="equip_balance_timeline")

    st.markdown(
        source_footer([
            {"name": "Drewry Container Equipment Index", "kind": "modeled", "quality": "demo"},
            {"name": "BRS Alphaliner Fleet Database",     "kind": "modeled", "quality": "demo"},
        ]),
        unsafe_allow_html=True,
    )

    # Global index callout
    global_idx = get_global_equipment_index()
    if global_idx >= 85:
        idx_label, idx_action = "TIGHT", "Caution"
    elif global_idx >= 70:
        idx_label, idx_action = "NORMAL", "Monitor"
    else:
        idx_label, idx_action = "SURPLUS", "Prioritize"

    # Map utilization (0–100%) to a 0.0–1.0 score
    idx_score = max(0.0, min(1.0, global_idx / 100.0))

    st.markdown(
        insight_card_html(
            title=f"Current Global Equipment Index: {global_idx:.1f}% ({idx_label})",
            score=idx_score,
            action=idx_action,
            rationale=(
                "Weighted-average utilization across all 6 regions and 5 container types. "
                "Above 85% = tight market with rate pressure; below 70% = surplus conditions."
            ),
            category="MACRO",
        ),
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

    page_header(
        title="Equipment & Container Pools",
        subtitle="Global TEU pool, regional shortage map, repositioning economics, "
                 "fleet age, and lease-vs-own analytics.",
        badge_text="OPERATIONS",
        badge_color=C_ACCENT,
    )

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

    section_divider("Shortage & Surplus")

    try:
        _render_shortage_surplus_map()
    except Exception:
        logger.exception("tab_equipment: error in shortage/surplus map")
        st.error("Error rendering Shortage/Surplus Map section.", icon="⚠️")

    section_divider("Repositioning")

    try:
        _render_repositioning_costs()
    except Exception:
        logger.exception("tab_equipment: error in repositioning costs")
        st.error("Error rendering Repositioning Cost section.", icon="⚠️")

    section_divider("Turn Time")

    try:
        _render_dwell_times()
    except Exception:
        logger.exception("tab_equipment: error in dwell times")
        st.error("Error rendering Equipment Turn Time section.", icon="⚠️")

    section_divider("Reefer Equipment")

    try:
        _render_reefer_section()
    except Exception:
        logger.exception("tab_equipment: error in reefer section")
        st.error("Error rendering Reefer Availability section.", icon="⚠️")

    section_divider("Shortage Alerts")

    try:
        _render_shortage_alerts()
    except Exception:
        logger.exception("tab_equipment: error in shortage alerts")
        st.error("Error rendering Shortage Alert System section.", icon="⚠️")

    section_divider("Fleet Age")

    try:
        _render_age_distribution()
    except Exception:
        logger.exception("tab_equipment: error in age distribution")
        st.error("Error rendering Fleet Age Distribution section.", icon="⚠️")

    section_divider("Lease vs Own")

    try:
        _render_lease_vs_own()
    except Exception:
        logger.exception("tab_equipment: error in lease vs own")
        st.error("Error rendering Leasing vs Owned Economics section.", icon="⚠️")

    section_divider("Cost Calculator")

    try:
        _render_cost_calculator(route_results)
    except Exception:
        logger.exception("tab_equipment: error in cost calculator")
        st.error("Error rendering Equipment Cost Calculator section.", icon="⚠️")

    section_divider("Balance Timeline")

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
