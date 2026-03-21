"""tab_intermodal.py — Multi-modal freight analysis tab (enhanced).

Sections:
  1.  Hero Header + KPI Strip
  2.  Mode Selection Optimizer (interactive form + recommendation card)
  3.  Intermodal Overview: mode share + cost comparison
  4.  Mode Cost Comparison: ocean+rail vs all-ocean by trade lane
  5.  Cost vs Time Bubble Matrix
  6.  Air Freight Monitor (rates, utilization, rate-spread chart)
  7.  Rail Capacity Utilization: US transcon, Silk Road, European rail
  8.  Air vs Ocean Rate Spread (absolute $/kg premium over time)
  9.  Inland Port Connectivity: rail/road scores for major ports
  10. Transshipment Hub Analysis: Singapore, HK, Rotterdam
  11. Last-Mile Cost Analysis: port-to-destination by mode
  12. Belt and Road Corridor Analyzer (map + detail table)
  13. Carbon Cost of Mode Choice (EU ETS $80/tonne)
"""
from __future__ import annotations

import csv as _csv
import io as _io

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.cargo_analyzer import HS_CATEGORIES
from processing.intermodal_analyzer import (
    AIR_KEY_ROUTES,
    AIR_OCEAN_RATIO_HISTORY,
    AIR_SURGE_EVENTS,
    TRANSPORT_MODES,
    BeltAndRoad,
    IntermodalComparison,
    compare_modes,
    compute_carbon_costs,
    _ROUTE_INTERMODAL,
)

# ---------------------------------------------------------------------------
# Design system — dark maritime palette
# ---------------------------------------------------------------------------
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_CARD2   = "#1e2a3a"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_BORDER2 = "rgba(255,255,255,0.12)"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"
_C_HIGH    = "#10b981"   # emerald
_C_ACCENT  = "#3b82f6"   # blue
_C_PURPLE  = "#8b5cf6"   # violet
_C_TEAL    = "#06b6d4"   # cyan
_C_WARN    = "#f59e0b"   # amber
_C_DANGER  = "#ef4444"   # red
_C_INDIGO  = "#6366f1"   # indigo

_MODE_COLORS: dict[str, str] = {
    "OCEAN":             "#3b82f6",
    "AIR":               "#ef4444",
    "RAIL_CHINA_EUROPE": "#10b981",
    "RAIL_US":           "#22c55e",
    "TRUCK_EU":          "#f59e0b",
}

_MODE_ICONS: dict[str, str] = {
    "OCEAN": "🚢",
    "AIR":   "✈️",
    "RAIL_CHINA_EUROPE": "🚂",
    "RAIL_US": "🛤️",
    "TRUCK_EU": "🚛",
}

_MODE_LABELS: dict[str, str] = {
    "OCEAN":             "Ocean",
    "AIR":               "Air",
    "RAIL_CHINA_EUROPE": "BRI Rail",
    "RAIL_US":           "US Rail",
    "TRUCK_EU":          "EU Truck",
}

_CARGO_LABELS: dict[str, str] = {k: v["label"] for k, v in HS_CATEGORIES.items()}
_URGENCY_OPTIONS: list[str] = ["Normal", "Urgent", "Critical"]
_URGENCY_MAP: dict[str, str] = {"Normal": "NORMAL", "Urgent": "URGENT", "Critical": "CRITICAL"}

_ALL_ROUTE_IDS: list[str] = [
    "transpacific_eb", "asia_europe", "transpacific_wb", "transatlantic",
    "sea_transpacific_eb", "ningbo_europe", "middle_east_to_europe",
    "middle_east_to_asia", "south_asia_to_europe", "intra_asia_china_sea",
    "intra_asia_china_japan", "china_south_america", "europe_south_america",
    "med_hub_to_asia", "north_africa_to_europe", "us_east_south_america",
    "longbeach_to_asia",
]

_ROUTE_LABELS_DISPLAY: dict[str, str] = {
    "transpacific_eb":        "Trans-Pacific Eastbound",
    "asia_europe":            "Asia-Europe",
    "transpacific_wb":        "Trans-Pacific Westbound",
    "transatlantic":          "Transatlantic",
    "sea_transpacific_eb":    "SE Asia Eastbound",
    "ningbo_europe":          "Ningbo-Europe via Suez",
    "middle_east_to_europe":  "Middle East to Europe",
    "middle_east_to_asia":    "Middle East to Asia",
    "south_asia_to_europe":   "South Asia to Europe",
    "intra_asia_china_sea":   "Intra-Asia: China to SE Asia",
    "intra_asia_china_japan": "Intra-Asia: China to Japan/Korea",
    "china_south_america":    "China to South America",
    "europe_south_america":   "Europe to South America",
    "med_hub_to_asia":        "Mediterranean Hub to Asia",
    "north_africa_to_europe": "North Africa to Europe",
    "us_east_south_america":  "US East Coast to South America",
    "longbeach_to_asia":      "Long Beach to Asia",
}

_EU_ETS_PER_TONNE: float = 80.0


# ---------------------------------------------------------------------------
# Shared static datasets for new sections
# ---------------------------------------------------------------------------

# Mode share % of global container trade volume (2026 estimates)
_GLOBAL_MODE_SHARE: dict[str, float] = {
    "OCEAN": 80.5,
    "TRUCK_EU": 11.2,
    "RAIL_CHINA_EUROPE": 4.8,
    "AIR": 3.5,
}

# Intermodal cost comparison: ocean+rail vs all-ocean savings ($/TEU) by lane
_INTERMODAL_SAVINGS: list[dict] = [
    {"lane": "Asia-Europe",         "all_ocean": 1850, "intermodal": 1420, "saving_pct": 23.2},
    {"lane": "Trans-Pacific EB",    "all_ocean": 2100, "intermodal": 1680, "saving_pct": 20.0},
    {"lane": "Trans-Atlantic",      "all_ocean": 1650, "intermodal": 1280, "saving_pct": 22.4},
    {"lane": "Intra-Asia",          "all_ocean":  820, "intermodal":  710, "saving_pct": 13.4},
    {"lane": "Middle East-Europe",  "all_ocean": 1420, "intermodal": 1190, "saving_pct": 16.2},
    {"lane": "South Asia-Europe",   "all_ocean": 1380, "intermodal": 1150, "saving_pct": 16.7},
]

# Rail capacity utilization % (Q1 2026)
_RAIL_UTILIZATION: list[dict] = [
    {
        "name": "US Transcontinental",
        "operator": "BNSF / UP",
        "utilization": 73,
        "capacity_teu_week": 12400,
        "yoy_change": +3.2,
        "status": "HEALTHY",
        "note": "Grain + intermodal containers; LA-Chicago 5-6 days",
    },
    {
        "name": "Silk Road (BRI) Rail",
        "operator": "China Railway / KTZ",
        "utilization": 58,
        "capacity_teu_week": 4800,
        "yoy_change": -8.1,
        "note": "Down from 2021 peak; Russia re-routing via Middle Corridor adds 5-7d",
        "status": "CONSTRAINED",
    },
    {
        "name": "European Rail (Rhine-Alpine)",
        "operator": "DB / SBB / RFI",
        "utilization": 88,
        "capacity_teu_week": 7200,
        "yoy_change": +1.4,
        "status": "STRAINED",
        "note": "Near-capacity; Gotthard Base Tunnel adds resilience; modal shift from road",
    },
]

# Air vs ocean rate spread — absolute $/kg premium (computed from ratio history)
_AIR_OCEAN_SPREAD_USD_KG: dict[int, float] = {
    yr: round(ratio * 0.06 - 0.06, 2)   # ocean baseline $0.06/kg
    for yr, ratio in AIR_OCEAN_RATIO_HISTORY.items()
}

# Inland port connectivity scores (0-100: higher = better rail/road access)
_PORT_CONNECTIVITY: list[dict] = [
    {
        "port": "Rotterdam",       "country": "NL",
        "rail_score": 94, "road_score": 96, "inland_ww": 91,
        "rail_connections": "Rhine-Alpine, Betuwe Line",
        "key_hinterland": "Germany, Switzerland, Italy",
    },
    {
        "port": "Los Angeles / Long Beach", "country": "US",
        "rail_score": 88, "road_score": 92, "inland_ww": 0,
        "rail_connections": "BNSF, UP intermodal ramps",
        "key_hinterland": "Chicago, Dallas, Kansas City",
    },
    {
        "port": "Hamburg",         "country": "DE",
        "rail_score": 89, "road_score": 88, "inland_ww": 78,
        "rail_connections": "DB Cargo, Scandinavia corridor",
        "key_hinterland": "Central/Eastern Europe, Scandinavia",
    },
    {
        "port": "Singapore",       "country": "SG",
        "rail_score": 42, "road_score": 85, "inland_ww": 0,
        "rail_connections": "Limited — no direct transcon rail",
        "key_hinterland": "SE Asia by feeder vessel",
    },
    {
        "port": "Antwerp-Bruges",  "country": "BE",
        "rail_score": 91, "road_score": 95, "inland_ww": 88,
        "rail_connections": "Iron Rhine, Rhine barge network",
        "key_hinterland": "France, Germany, UK (ferry)",
    },
    {
        "port": "Shanghai (Yangshan)", "country": "CN",
        "rail_score": 82, "road_score": 90, "inland_ww": 73,
        "rail_connections": "China Railway – Yiwu BRI services",
        "key_hinterland": "Yangtze River Delta, inland China",
    },
    {
        "port": "Busan",           "country": "KR",
        "rail_score": 71, "road_score": 87, "inland_ww": 0,
        "rail_connections": "KTX rail corridor to Seoul",
        "key_hinterland": "South Korea domestic",
    },
    {
        "port": "Savannah",        "country": "US",
        "rail_score": 79, "road_score": 91, "inland_ww": 0,
        "rail_connections": "CSX, Norfolk Southern",
        "key_hinterland": "Atlanta, Midwest, Southeast US",
    },
]

# Transshipment hub volumes (million TEU, 2025 est.)
_TRANSSHIPMENT_HUBS: list[dict] = [
    {
        "hub": "Singapore",
        "total_teu_m": 39.2,
        "transship_pct": 82,
        "anchorage_days": 1.8,
        "cranes": 72,
        "depth_m": 16.0,
        "key_routes": "Asia-Europe, Intra-Asia, Asia-Australia",
        "risk": "LOW",
        "note": "World's #2 container port; exceptional turnaround; PSA International ops.",
    },
    {
        "hub": "Hong Kong",
        "total_teu_m": 14.3,
        "transship_pct": 74,
        "anchorage_days": 2.4,
        "cranes": 38,
        "depth_m": 15.5,
        "key_routes": "South China, Trans-Pacific, Asia-Europe",
        "risk": "MODERATE",
        "note": "Volumes declining post-2020; Shenzhen competition; regulatory uncertainty.",
    },
    {
        "hub": "Rotterdam",
        "total_teu_m": 14.8,
        "transship_pct": 38,
        "anchorage_days": 1.2,
        "cranes": 54,
        "depth_m": 20.0,
        "key_routes": "Asia-Europe, Transatlantic, North Sea",
        "risk": "LOW",
        "note": "Europe's largest port; Maasvlakte 2 expansion; 20m depth enables ULCVs.",
    },
    {
        "hub": "Port Klang",
        "total_teu_m": 14.1,
        "transship_pct": 61,
        "anchorage_days": 2.1,
        "cranes": 44,
        "depth_m": 14.5,
        "key_routes": "Intra-Asia, Asia-Europe feeder, Strait of Malacca",
        "risk": "LOW",
        "note": "Malaysia's primary hub; Westports expansion adds 5M TEU capacity by 2027.",
    },
    {
        "hub": "Colombo",
        "total_teu_m": 7.2,
        "transship_pct": 76,
        "anchorage_days": 2.9,
        "cranes": 26,
        "depth_m": 18.0,
        "key_routes": "South Asia, Asia-Europe, Africa feeder",
        "risk": "MODERATE",
        "note": "ECT expansion underway; strategic location between Asia-Europe main lanes.",
    },
]

# Last-mile costs: port to final destination (USD per TEU by mode)
_LAST_MILE_DATA: list[dict] = [
    {
        "segment":    "Port → Urban WH (50 km)",
        "truck":      380,
        "rail":       210,
        "barge":      140,
        "days_truck": 0.3,
        "days_rail":  0.8,
        "days_barge": 1.5,
    },
    {
        "segment":    "Port → Regional DC (200 km)",
        "truck":      720,
        "rail":       490,
        "barge":      310,
        "days_truck": 0.8,
        "days_rail":  1.5,
        "days_barge": 3.0,
    },
    {
        "segment":    "Port → Inland Hub (500 km)",
        "truck":      1_450,
        "rail":       870,
        "barge":      520,
        "days_truck": 1.5,
        "days_rail":  2.5,
        "days_barge": 6.0,
    },
    {
        "segment":    "Port → Far Interior (1,200 km)",
        "truck":      3_100,
        "rail":       1_680,
        "barge":      None,
        "days_truck": 3.0,
        "days_rail":  4.5,
        "days_barge": None,
    },
    {
        "segment":    "Port → Cross-Country (2,500 km)",
        "truck":      5_800,
        "rail":       2_950,
        "barge":      None,
        "days_truck": 5.5,
        "days_rail":  7.0,
        "days_barge": None,
    },
]


# ---------------------------------------------------------------------------
# Shared layout primitives
# ---------------------------------------------------------------------------

def _section_header(title: str, subtitle: str = "", icon: str = "") -> None:
    icon_html = (
        f'<span style="font-size:1.3rem;margin-right:10px;vertical-align:middle">{icon}</span>'
        if icon else ""
    )
    sub_html = (
        f'<div style="color:{_C_TEXT2};font-size:0.82rem;margin-top:4px;font-weight:400">'
        f'{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:28px 0 16px">'
        f'<div style="font-size:1.08rem;font-weight:800;color:{_C_TEXT};'
        f'letter-spacing:-0.01em">{icon_html}{title}</div>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;margin:32px 0 4px">'
        f'<div style="flex:1;height:1px;background:rgba(255,255,255,0.07)"></div>'
        f'<span style="font-size:0.6rem;color:{_C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.14em;white-space:nowrap">{label}</span>'
        f'<div style="flex:1;height:1px;background:rgba(255,255,255,0.07)"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:#fff;font-size:0.65rem;font-weight:700;'
        f'padding:2px 8px;border-radius:4px;letter-spacing:0.06em;'
        f'text-transform:uppercase">{text}</span>'
    )


def _metric_card(label: str, value: str, sub: str = "", color: str = _C_TEXT,
                 border_color: str = _C_BORDER) -> str:
    sub_html = (
        f'<div style="font-size:0.68rem;color:{_C_TEXT3};margin-top:3px">{sub}</div>'
        if sub else ""
    )
    return (
        f'<div style="background:{_C_CARD};border:1px solid {border_color};'
        f'border-radius:10px;padding:14px 16px;height:100%">'
        f'<div style="font-size:0.65rem;color:{_C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:6px">{label}</div>'
        f'<div style="font-size:1.45rem;font-weight:800;color:{color};'
        f'line-height:1.1">{value}</div>'
        f'{sub_html}</div>'
    )


def _trunc(s: str, n: int) -> str:
    if not s:
        return ""
    return s[:n] + ("..." if len(s) > n else "")


def _fig_layout(fig: go.Figure, height: int = 360, margin: dict | None = None) -> go.Figure:
    m = margin or dict(t=30, b=50, l=64, r=24)
    fig.update_layout(
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=height,
        margin=m,
        font=dict(color=_C_TEXT, family="Inter, sans-serif", size=11),
        legend=dict(
            font=dict(color=_C_TEXT2, size=10),
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(255,255,255,0.06)",
            borderwidth=1,
        ),
        xaxis=dict(
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            showgrid=True,
        ),
        yaxis=dict(
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            showgrid=True,
        ),
    )
    return fig


# ---------------------------------------------------------------------------
# Section 1 — Hero Header + KPI Strip
# ---------------------------------------------------------------------------

def _render_hero() -> None:
    st.markdown(
        f'<div style="background:linear-gradient(135deg,{_C_SURFACE} 0%,{_C_CARD} 100%);'
        f'border:1px solid {_C_BORDER2};border-radius:16px;padding:28px 32px;margin-bottom:24px">'
        f'<div style="display:flex;align-items:flex-start;justify-content:space-between;'
        f'flex-wrap:wrap;gap:16px">'
        f'<div>'
        f'<div style="font-size:0.65rem;color:{_C_TEAL};text-transform:uppercase;'
        f'letter-spacing:0.16em;margin-bottom:8px;font-weight:700">Shipping Intelligence Platform</div>'
        f'<h2 style="font-size:1.7rem;font-weight:900;color:{_C_TEXT};margin:0 0 8px;'
        f'letter-spacing:-0.02em">Intermodal Transport Intelligence</h2>'
        f'<p style="font-size:0.85rem;color:{_C_TEXT2};margin:0;max-width:620px;line-height:1.6">'
        f'Compare ocean, air, rail, and road freight across cost, transit time, carbon footprint, '
        f'and reliability. Analyze mode share, corridor risk, transshipment hubs, and last-mile '
        f'economics to optimize your supply chain strategy.'
        f'</p></div>'
        f'<div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">'
        f'{_badge("Live Intelligence", _C_HIGH)}'
        f'<span style="font-size:0.7rem;color:{_C_TEXT3}">Q1 2026 benchmarks</span>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )

    # KPI strip
    kpis = [
        ("Ocean Mode Share", "80.5%",   "of global container trade volume", _C_ACCENT),
        ("Air vs Ocean Premium", "75x",  "cost ratio $/kg (2026 avg)",      _C_DANGER),
        ("BRI Rail Growth", "+340%",    "volume index vs 2015 baseline",    _C_HIGH),
        ("EU Rail Utilization", "88%",  "Rhine-Alpine corridor — strained", _C_WARN),
        ("Avg Last-Mile Cost", "$870",  "rail, 200 km inland per TEU",     _C_PURPLE),
    ]
    cols = st.columns(len(kpis))
    for col, (label, val, sub, color) in zip(cols, kpis):
        col.markdown(_metric_card(label, val, sub, color, color + "33"),
                     unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 2 — Mode Selection Optimizer
# ---------------------------------------------------------------------------

def _render_mode_optimizer(route_results: list) -> None:
    _divider("SECTION 1 — MODE SELECTION OPTIMIZER")
    _section_header(
        "Mode Selection Optimizer",
        "Given cargo type, urgency, and shipment weight — find your optimal transport mix.",
        "🧭",
    )

    cargo_keys    = list(HS_CATEGORIES.keys())
    cargo_display = [_CARGO_LABELS.get(k, k.title()) for k in cargo_keys]
    route_ids     = _ALL_ROUTE_IDS
    route_display = [_ROUTE_LABELS_DISPLAY.get(r, r) for r in route_ids]

    default_route_idx = 0
    if route_results:
        try:
            first_id = getattr(route_results[0], "route_id", None)
            if first_id in route_ids:
                default_route_idx = route_ids.index(first_id)
        except (AttributeError, IndexError):
            pass

    col1, col2, col3, col4 = st.columns([1.5, 1, 1, 1.8])
    with col1:
        sel_cargo_display = st.selectbox(
            "Cargo Category", options=cargo_display, index=0, key="im_cargo_cat"
        )
        sel_cargo = cargo_keys[cargo_display.index(sel_cargo_display)]
    with col2:
        weight_kg = st.number_input(
            "Weight (kg)", min_value=1.0, max_value=500_000.0,
            value=1_000.0, step=100.0, key="im_weight",
        )
    with col3:
        urgency_label = st.selectbox(
            "Urgency", options=_URGENCY_OPTIONS, index=0, key="im_urgency"
        )
        urgency = _URGENCY_MAP[urgency_label]
    with col4:
        sel_route_display = st.selectbox(
            "Trade Route", options=route_display, index=default_route_idx, key="im_route"
        )
        sel_route = route_ids[route_display.index(sel_route_display)]

    try:
        result: IntermodalComparison = compare_modes(sel_cargo, weight_kg, sel_route, urgency)
        logger.debug("im optimizer: recommended={}", result.recommended_mode)
    except Exception as exc:
        st.warning(f"Could not compute mode comparison: {exc}")
        return

    has_rail  = result.rail_cost is not None and result.rail_days is not None
    has_truck = result.truck_cost is not None and result.truck_days is not None

    # Recommendation banner
    rec_color = _MODE_COLORS.get(result.recommended_mode, _C_ACCENT)
    rec_label = _MODE_LABELS.get(result.recommended_mode, result.recommended_mode)
    rec_icon  = _MODE_ICONS.get(result.recommended_mode, "")

    st.markdown(
        f'<div style="background:linear-gradient(135deg,{_C_CARD} 0%,{_C_CARD2} 100%);'
        f'border:1.5px solid {rec_color}44;border-left:4px solid {rec_color};'
        f'border-radius:12px;padding:18px 22px;margin:18px 0">'
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
        f'<span style="font-size:1.5rem">{rec_icon}</span>'
        f'<div>'
        f'<div style="font-size:0.62rem;color:{_C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.1em">Recommended Mode</div>'
        f'<div style="font-size:1.05rem;font-weight:800;color:{rec_color}">{rec_label}</div>'
        f'</div>'
        f'<div style="margin-left:auto">'
        f'{_badge("OPTIMAL CHOICE", rec_color)}'
        f'</div></div>'
        f'<div style="font-size:0.83rem;color:{_C_TEXT2};line-height:1.65;'
        f'border-top:1px solid rgba(255,255,255,0.06);padding-top:10px">'
        f'{result.recommendation_rationale}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Build comparison rows
    def _row(mode_key: str, cost_usd: float, days: float) -> dict:
        mode = TRANSPORT_MODES.get(mode_key)
        if mode is None:
            return {}
        is_rec = mode_key == result.recommended_mode
        co2    = mode.co2_kg_per_kg_cargo * weight_kg
        return {
            "mode_key": mode_key,
            "label":    _MODE_LABELS.get(mode_key, mode_key),
            "icon":     _MODE_ICONS.get(mode_key, ""),
            "cost":     cost_usd,
            "cost_kg":  cost_usd / weight_kg if weight_kg > 0 else 0,
            "days":     days,
            "co2":      co2,
            "rel":      mode.reliability_pct,
            "is_rec":   is_rec,
        }

    rows = [_row("OCEAN", result.ocean_cost, result.ocean_days),
            _row("AIR",   result.air_cost,   result.air_days)]
    if has_rail:
        ri = _ROUTE_INTERMODAL.get(sel_route)
        rail_key = "RAIL_US" if (ri and "RAIL_US" in ri[2]) else "RAIL_CHINA_EUROPE"
        rows.append(_row(rail_key, result.rail_cost, result.rail_days))
    if has_truck:
        rows.append(_row("TRUCK_EU", result.truck_cost, result.truck_days))
    rows = [r for r in rows if r]

    # Visual comparison cards
    cols = st.columns(len(rows))
    for col, row in zip(cols, rows):
        color   = _MODE_COLORS.get(row["mode_key"], _C_TEXT3)
        bg      = f"linear-gradient(160deg,{_C_CARD} 0%,{_C_CARD2} 100%)"
        border  = f"1.5px solid {color}" if row["is_rec"] else f"1px solid {_C_BORDER}"
        badge   = _badge("BEST", color) if row["is_rec"] else ""
        col.markdown(
            f'<div style="background:{bg};border:{border};border-radius:12px;'
            f'padding:16px 14px;position:relative">'
            f'<div style="position:absolute;top:10px;right:10px">{badge}</div>'
            f'<div style="font-size:1.3rem;margin-bottom:4px">{row["icon"]}</div>'
            f'<div style="font-size:0.82rem;font-weight:700;color:{color};'
            f'margin-bottom:12px">{row["label"]}</div>'
            f'<div style="font-size:0.65rem;color:{_C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.06em">Total Cost</div>'
            f'<div style="font-size:1.1rem;font-weight:800;color:{_C_TEXT};margin-bottom:8px">'
            f'${row["cost"]:,.0f}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;'
            f'font-size:0.72rem;color:{_C_TEXT2}">'
            f'<div><div style="color:{_C_TEXT3}">$/kg</div>'
            f'<div style="font-weight:600">${row["cost_kg"]:.3f}</div></div>'
            f'<div><div style="color:{_C_TEXT3}">Days</div>'
            f'<div style="font-weight:600">{row["days"]:.1f}</div></div>'
            f'<div><div style="color:{_C_TEXT3}">CO₂ (kg)</div>'
            f'<div style="font-weight:600">{row["co2"]:.1f}</div></div>'
            f'<div><div style="color:{_C_TEXT3}">Reliability</div>'
            f'<div style="font-weight:600">{row["rel"]}%</div></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    prem = result.cost_premium_air_vs_ocean_pct
    st.caption(
        f"Air vs Ocean cost premium for this shipment: +{prem:.0f}%.  "
        "CO₂ figures are per-shipment totals.  Reliability = on-time delivery %."
    )

    # Download
    def _csv_str() -> str:
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["Mode", "Total Cost USD", "Cost/kg USD", "Transit Days", "CO2 kg", "Reliability %"])
        for row in rows:
            w.writerow([
                row["label"], round(row["cost"], 2), round(row["cost_kg"], 4),
                round(row["days"], 1), round(row["co2"], 2), row["rel"],
            ])
        return buf.getvalue()

    st.download_button(
        "Download mode comparison (CSV)", data=_csv_str(),
        file_name="intermodal_mode_comparison.csv", mime="text/csv",
        key="im_optimizer_dl",
    )


# ---------------------------------------------------------------------------
# Section 3 — Intermodal Overview: Mode Share + Cost Landscape
# ---------------------------------------------------------------------------

def _render_intermodal_overview() -> None:
    _divider("SECTION 2 — INTERMODAL OVERVIEW")
    _section_header(
        "Global Mode Share & Cost Landscape",
        "Ocean dominates volume but air, rail, and truck each serve distinct niches.",
        "🌐",
    )

    col_pie, col_cost = st.columns([1, 1.6])

    with col_pie:
        # Pie chart: mode share
        labels = [_MODE_LABELS.get(k, k) for k in _GLOBAL_MODE_SHARE]
        values = list(_GLOBAL_MODE_SHARE.values())
        colors = [_MODE_COLORS.get(k, "#64748b") for k in _GLOBAL_MODE_SHARE]

        fig_pie = go.Figure(go.Pie(
            labels=labels,
            values=values,
            hole=0.55,
            marker=dict(colors=colors, line=dict(color=_C_BG, width=3)),
            textinfo="label+percent",
            textfont=dict(size=10, color=_C_TEXT),
            hovertemplate="<b>%{label}</b><br>Share: %{value:.1f}%<extra></extra>",
        ))
        fig_pie.add_annotation(
            text="Global<br>Volume<br>Share",
            x=0.5, y=0.5, font=dict(size=10, color=_C_TEXT2, family="Inter"),
            showarrow=False,
        )
        fig_pie.update_layout(
            paper_bgcolor=_C_BG,
            height=320,
            margin=dict(t=20, b=20, l=10, r=10),
            font=dict(color=_C_TEXT, family="Inter, sans-serif"),
            legend=dict(font=dict(color=_C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)"),
            showlegend=True,
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="im_mode_share_pie")

    with col_cost:
        # Bar: cost per kg by mode with capacity dots
        modes_ordered = ["OCEAN", "RAIL_US", "RAIL_CHINA_EUROPE", "TRUCK_EU", "AIR"]
        mode_labels_o = [_MODE_LABELS.get(m, m) for m in modes_ordered]
        costs_o = [TRANSPORT_MODES[m].cost_per_kg_usd for m in modes_ordered]
        colors_o = [_MODE_COLORS.get(m, "#64748b") for m in modes_ordered]
        co2_o = [TRANSPORT_MODES[m].co2_kg_per_kg_cargo for m in modes_ordered]
        rel_o = [TRANSPORT_MODES[m].reliability_pct for m in modes_ordered]

        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=mode_labels_o,
            y=costs_o,
            marker_color=colors_o,
            marker_opacity=0.85,
            marker_line=dict(color="rgba(255,255,255,0.15)", width=1),
            customdata=list(zip(co2_o, rel_o)),
            hovertemplate=(
                "<b>%{x}</b><br>"
                "Cost: $%{y:.2f}/kg<br>"
                "CO₂: %{customdata[0]:.3f} kg/kg<br>"
                "Reliability: %{customdata[1]:.0f}%"
                "<extra></extra>"
            ),
            name="Cost/kg (USD)",
        ))

        # Overlay reliability as scatter
        fig_bar.add_trace(go.Scatter(
            x=mode_labels_o,
            y=[r / 100 * max(costs_o) * 1.1 for r in rel_o],
            mode="markers+text",
            marker=dict(size=10, color=_C_TEAL, symbol="diamond",
                        line=dict(color=_C_BG, width=2)),
            text=[f"{r:.0f}%" for r in rel_o],
            textposition="top center",
            textfont=dict(color=_C_TEAL, size=9),
            name="Reliability (scaled)",
            hovertemplate="<b>%{x}</b><br>Reliability: %{text}<extra></extra>",
            yaxis="y",
        ))

        fig_bar = _fig_layout(fig_bar, height=320)
        fig_bar.update_layout(
            yaxis=dict(title="Cost per kg (USD)", type="log", color=_C_TEXT2,
                       gridcolor="rgba(255,255,255,0.04)", zeroline=False),
            barmode="group",
            showlegend=True,
        )
        st.plotly_chart(fig_bar, use_container_width=True, key="im_cost_landscape")

    st.caption(
        "Mode share by global container trade volume (2026 est.).  "
        "Cost per kg on log scale — air is ~75x more expensive than ocean.  "
        "Teal diamonds show reliability %; right axis scaled for visibility."
    )


# ---------------------------------------------------------------------------
# Section 4 — Mode Cost Comparison: intermodal vs all-ocean by trade lane
# ---------------------------------------------------------------------------

def _render_mode_cost_comparison() -> None:
    _divider("SECTION 3 — OCEAN + RAIL VS ALL-OCEAN BY TRADE LANE")
    _section_header(
        "Intermodal vs All-Ocean Cost Comparison",
        "Ocean + rail combinations save 13–23% vs all-ocean on major trade lanes.",
        "📊",
    )

    lanes     = [d["lane"] for d in _INTERMODAL_SAVINGS]
    all_ocean = [d["all_ocean"] for d in _INTERMODAL_SAVINGS]
    intermod  = [d["intermodal"] for d in _INTERMODAL_SAVINGS]
    savings_p = [d["saving_pct"] for d in _INTERMODAL_SAVINGS]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="All-Ocean ($/TEU)",
        x=lanes,
        y=all_ocean,
        marker_color=_C_ACCENT,
        marker_opacity=0.70,
        hovertemplate="<b>%{x}</b><br>All-ocean: $%{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Ocean + Rail ($/TEU)",
        x=lanes,
        y=intermod,
        marker_color=_C_HIGH,
        marker_opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Intermodal: $%{y:,}<extra></extra>",
    ))

    # Savings annotations
    for lane, ao, im, sp in zip(lanes, all_ocean, intermod, savings_p):
        fig.add_annotation(
            x=lane,
            y=ao + 40,
            text=f"−{sp:.1f}%",
            showarrow=False,
            font=dict(color=_C_HIGH, size=9, family="Inter"),
            yshift=4,
        )

    fig = _fig_layout(fig, height=360)
    fig.update_layout(
        barmode="group",
        xaxis=dict(color=_C_TEXT2, gridcolor="rgba(0,0,0,0)", tickangle=-20),
        yaxis=dict(title="Cost per TEU (USD)", color=_C_TEXT2,
                   gridcolor="rgba(255,255,255,0.04)"),
    )
    st.plotly_chart(fig, use_container_width=True, key="im_intermodal_savings")

    # Summary strip
    avg_saving = sum(savings_p) / len(savings_p)
    max_saving = max(savings_p)
    max_lane   = lanes[savings_p.index(max_saving)]

    kpis = [
        ("Avg Cost Saving", f"{avg_saving:.1f}%",     "intermodal vs all-ocean",  _C_HIGH),
        ("Best Route",      max_lane,                  f"−{max_saving:.1f}% saving", _C_ACCENT),
        ("Mode Mix",        "Ocean + Rail",             "pre-carriage rail segment", _C_TEAL),
        ("Data Basis",      "2026 Q1",                 "benchmark $/TEU estimates", _C_TEXT3),
    ]
    cols = st.columns(4)
    for col, (lbl, val, sub, clr) in zip(cols, kpis):
        col.markdown(_metric_card(lbl, val, sub, clr), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 5 — Cost vs Time Bubble Matrix
# ---------------------------------------------------------------------------

def _render_cost_time_scatter() -> None:
    _divider("SECTION 4 — COST vs TRANSIT TIME MATRIX")
    _section_header(
        "Transport Mode Matrix",
        "Bubble size = CO₂ intensity (kg CO₂/kg cargo).  Y-axis log-scale.",
        "⚖️",
    )

    fig = go.Figure()
    all_modes = list(TRANSPORT_MODES.keys())
    mode_symbols = {
        "OCEAN": "circle", "AIR": "circle",
        "RAIL_CHINA_EUROPE": "diamond", "TRUCK_EU": "square", "RAIL_US": "diamond",
    }

    for mk in all_modes:
        mode = TRANSPORT_MODES[mk]
        color = _MODE_COLORS.get(mk, "#64748b")
        label = _MODE_LABELS.get(mk, mk)
        bsize = mode.co2_kg_per_kg_cargo * 55 + 14

        # Transit range bar
        fig.add_trace(go.Scatter(
            x=[mode.transit_days_min, mode.transit_days_max],
            y=[mode.cost_per_kg_usd, mode.cost_per_kg_usd],
            mode="lines",
            line=dict(color=color, width=2, dash="dot"),
            showlegend=False,
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=[mode.transit_days_mid],
            y=[mode.cost_per_kg_usd],
            mode="markers+text",
            name=label,
            marker=dict(
                size=bsize, color=color, opacity=0.82,
                symbol=mode_symbols.get(mk, "circle"),
                line=dict(color="rgba(255,255,255,0.25)", width=1.5),
            ),
            text=[label],
            textposition="top center",
            textfont=dict(color=_C_TEXT, size=10, family="Inter, sans-serif"),
            hovertemplate=(
                f"<b>{label}</b><br>"
                f"Transit: {mode.transit_days_min}–{mode.transit_days_max} days<br>"
                f"Midpoint: {mode.transit_days_mid:.0f} days<br>"
                f"Cost: ${mode.cost_per_kg_usd}/kg<br>"
                f"CO₂: {mode.co2_kg_per_kg_cargo} kg/kg cargo<br>"
                f"Reliability: {mode.reliability_pct}%<br>"
                f"Advantage: {mode.key_advantage[:70]}"
                "<extra></extra>"
            ),
        ))

    # Zone annotations
    for txt, x, y, clr in [
        ("Fastest & Priciest", 2, 6.0, _C_DANGER),
        ("Sweet Spot", 13, 0.5, _C_HIGH),
        ("Cheapest & Slowest", 27, 0.07, _C_ACCENT),
    ]:
        fig.add_annotation(
            x=x, y=y, text=txt, showarrow=False,
            font=dict(color=clr, size=9),
            bgcolor="rgba(0,0,0,0.35)",
            borderpad=4,
        )

    fig = _fig_layout(fig, height=440)
    fig.update_layout(
        xaxis=dict(title="Transit Days (midpoint estimate)"),
        yaxis=dict(title="Cost per kg (USD)", type="log"),
    )
    st.plotly_chart(fig, use_container_width=True, key="im_cost_time_scatter")
    st.caption(
        "Dashed horizontal lines show min–max transit range per mode.  "
        "Bubble size proportional to CO₂ intensity.  Air is ~120x more carbon-intensive than ocean."
    )


# ---------------------------------------------------------------------------
# Section 6 — Air Freight Monitor
# ---------------------------------------------------------------------------

def _render_air_freight_monitor() -> None:
    _divider("SECTION 5 — AIR FREIGHT MONITOR")
    _section_header(
        "Air Cargo Market Intelligence",
        "Key route rates, utilization, and historical surge events.",
        "✈️",
    )

    cols = st.columns(len(AIR_KEY_ROUTES))
    for i, route_info in enumerate(AIR_KEY_ROUTES):
        with cols[i]:
            normal = route_info["normal_rate_usd_kg"]
            peak   = route_info["peak_rate_usd_kg"]
            util   = route_info["utilization_pct"]
            util_color = _C_DANGER if util >= 90 else _C_WARN if util >= 80 else _C_HIGH
            status = "CRITICAL" if util >= 90 else "HIGH" if util >= 80 else "NORMAL"

            st.markdown(
                f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER2};'
                f'border-top:3px solid {_C_DANGER};border-radius:10px;padding:14px 16px">'
                f'<div style="font-size:0.62rem;color:{_C_DANGER};text-transform:uppercase;'
                f'letter-spacing:0.08em;margin-bottom:6px">Air Cargo Route</div>'
                f'<div style="font-size:0.83rem;font-weight:700;color:{_C_TEXT};'
                f'margin-bottom:12px">{route_info["route"]}</div>'
                f'<div style="display:flex;gap:10px;margin-bottom:10px">'
                f'<div><div style="font-size:0.62rem;color:{_C_TEXT3}">Normal</div>'
                f'<div style="font-size:1.0rem;font-weight:800;color:{_C_TEXT}">${normal}/kg</div></div>'
                f'<div><div style="font-size:0.62rem;color:{_C_TEXT3}">Peak</div>'
                f'<div style="font-size:1.0rem;font-weight:800;color:{_C_WARN}">${peak}/kg</div></div>'
                f'<div><div style="font-size:0.62rem;color:{_C_TEXT3}">Util.</div>'
                f'<div style="font-size:1.0rem;font-weight:800;color:{util_color}">{util}%</div></div>'
                f'</div>'
                f'<div style="margin-bottom:8px">{_badge(status, util_color)}</div>'
                f'<div style="font-size:0.7rem;color:{_C_TEXT3}">{route_info["dominant_cargo"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # Historical surge events
    st.markdown(
        f'<div style="font-size:0.72rem;font-weight:700;color:{_C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">'
        f'Historical Air Cargo Surge Events</div>',
        unsafe_allow_html=True,
    )
    for event in AIR_SURGE_EVENTS:
        mult = event["peak_rate_multiplier"]
        mult_color = _C_DANGER if mult >= 2.0 else _C_WARN
        st.markdown(
            f'<div style="background:{_C_CARD};border-left:3px solid {mult_color};'
            f'border-radius:0 8px 8px 0;padding:10px 16px;margin-bottom:8px;'
            f'display:flex;align-items:center;gap:16px">'
            f'<div style="min-width:44px;font-size:0.9rem;font-weight:800;color:{mult_color}">'
            f'{event["year"]}</div>'
            f'<div style="flex:1">'
            f'<div style="font-size:0.82rem;font-weight:700;color:{_C_TEXT};margin-bottom:2px">'
            f'{event["label"]}</div>'
            f'<div style="font-size:0.74rem;color:{_C_TEXT2}">{event["description"]}</div>'
            f'</div>'
            f'<div style="text-align:right;white-space:nowrap">'
            f'<div style="font-size:0.62rem;color:{_C_TEXT3}">Rate Multiplier</div>'
            f'<div style="font-size:1.05rem;font-weight:800;color:{mult_color}">{mult}x</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 7 — Rail Capacity Utilization
# ---------------------------------------------------------------------------

def _render_rail_utilization() -> None:
    _divider("SECTION 6 — RAIL CAPACITY UTILIZATION")
    _section_header(
        "Rail Corridor Capacity Utilization",
        "US Transcontinental, Silk Road (BRI), and European Rhine-Alpine corridor status.",
        "🚂",
    )

    status_colors = {"HEALTHY": _C_HIGH, "CONSTRAINED": _C_WARN, "STRAINED": _C_DANGER}

    cols = st.columns(len(_RAIL_UTILIZATION))
    for col, rail in zip(cols, _RAIL_UTILIZATION):
        util   = rail["utilization"]
        scolor = status_colors.get(rail["status"], _C_TEXT3)
        yoy    = rail["yoy_change"]
        yoy_sign = "+" if yoy >= 0 else ""
        yoy_color = _C_HIGH if yoy >= 0 else _C_DANGER

        # Utilization bar visual
        bar_fill = f'width:{util}%;background:{scolor};'
        with col:
            st.markdown(
                f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER2};'
                f'border-radius:12px;padding:18px 16px">'
                f'<div style="font-size:0.62rem;color:{_C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.08em;margin-bottom:4px">{rail["operator"]}</div>'
                f'<div style="font-size:0.88rem;font-weight:800;color:{_C_TEXT};'
                f'margin-bottom:12px">{rail["name"]}</div>'
                f'<div style="margin-bottom:6px">'
                f'<div style="display:flex;justify-content:space-between;'
                f'font-size:0.7rem;color:{_C_TEXT3};margin-bottom:4px">'
                f'<span>Utilization</span><span style="color:{scolor};font-weight:700">'
                f'{util}%</span></div>'
                f'<div style="background:rgba(255,255,255,0.07);border-radius:4px;height:8px;'
                f'overflow:hidden">'
                f'<div style="{bar_fill}height:100%;border-radius:4px;'
                f'transition:width 0.4s ease"></div></div></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:12px">'
                f'<div><div style="font-size:0.62rem;color:{_C_TEXT3}">Capacity</div>'
                f'<div style="font-size:0.82rem;font-weight:700;color:{_C_TEXT}">'
                f'{rail["capacity_teu_week"]:,} TEU/wk</div></div>'
                f'<div style="text-align:right"><div style="font-size:0.62rem;color:{_C_TEXT3}">'
                f'YoY Change</div>'
                f'<div style="font-size:0.82rem;font-weight:700;color:{yoy_color}">'
                f'{yoy_sign}{yoy:.1f}%</div></div></div>'
                f'{_badge(rail["status"], scolor)}'
                f'<div style="font-size:0.71rem;color:{_C_TEXT3};margin-top:10px;'
                f'line-height:1.5">{rail["note"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # Utilization bar chart comparison
    fig = go.Figure()
    names  = [r["name"] for r in _RAIL_UTILIZATION]
    utils  = [r["utilization"] for r in _RAIL_UTILIZATION]
    scolors = [status_colors.get(r["status"], _C_TEXT3) for r in _RAIL_UTILIZATION]
    caps   = [r["capacity_teu_week"] for r in _RAIL_UTILIZATION]

    fig.add_trace(go.Bar(
        x=names, y=utils,
        marker_color=scolors,
        marker_opacity=0.85,
        customdata=caps,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Utilization: %{y}%<br>"
            "Capacity: %{customdata:,} TEU/wk"
            "<extra></extra>"
        ),
        name="Utilization %",
        text=[f"{u}%" for u in utils],
        textposition="inside",
        textfont=dict(color=_C_TEXT, size=12, family="Inter"),
    ))

    # 90% warning threshold
    fig.add_hline(
        y=90, line_width=1.5, line_dash="dash", line_color=_C_DANGER,
        annotation_text="90% — Severe Constraint",
        annotation_font=dict(color=_C_DANGER, size=9),
        annotation_position="right",
    )
    fig.add_hline(
        y=80, line_width=1, line_dash="dot", line_color=_C_WARN,
        annotation_text="80% — Elevated Pressure",
        annotation_font=dict(color=_C_WARN, size=9),
        annotation_position="right",
    )

    fig = _fig_layout(fig, height=280)
    fig.update_layout(
        yaxis=dict(title="Utilization (%)", range=[0, 105]),
        xaxis=dict(gridcolor="rgba(0,0,0,0)"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key="im_rail_utilization")


# ---------------------------------------------------------------------------
# Section 8 — Air vs Ocean Rate Spread
# ---------------------------------------------------------------------------

def _render_air_ocean_spread() -> None:
    _divider("SECTION 7 — AIR vs OCEAN RATE SPREAD")
    _section_header(
        "Air Freight vs Ocean Rate Spread",
        "Absolute $/kg premium of air over ocean freight — historical trend and surge events.",
        "📈",
    )

    years   = sorted(AIR_OCEAN_RATIO_HISTORY.keys())
    ratios  = [AIR_OCEAN_RATIO_HISTORY[y] for y in years]
    spreads = [_AIR_OCEAN_SPREAD_USD_KG.get(y, 0.0) for y in years]

    col_ratio, col_spread = st.columns(2)

    with col_ratio:
        fig_r = go.Figure()
        fig_r.add_trace(go.Scatter(
            x=years, y=ratios,
            mode="lines+markers",
            line=dict(color=_C_DANGER, width=2.5),
            marker=dict(size=6, color=_C_DANGER),
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.08)",
            name="Air/Ocean ratio",
            hovertemplate="Year: %{x}<br>Ratio: %{y:.0f}x<extra></extra>",
        ))

        for event in AIR_SURGE_EVENTS:
            yr = event["year"]
            if yr in AIR_OCEAN_RATIO_HISTORY:
                fig_r.add_annotation(
                    x=yr, y=AIR_OCEAN_RATIO_HISTORY[yr],
                    text=event["label"][:25],
                    showarrow=True, arrowhead=2,
                    arrowcolor=_C_WARN,
                    font=dict(color=_C_WARN, size=8),
                    ax=0, ay=-34,
                )

        fig_r.add_hrect(
            y0=50, y1=100,
            fillcolor="rgba(59,130,246,0.05)", line_width=0,
            annotation_text="Normal 50-100x",
            annotation_position="right",
            annotation_font=dict(color=_C_TEXT3, size=8),
        )

        fig_r = _fig_layout(fig_r, height=300)
        fig_r.update_layout(
            title=dict(text="Air/Ocean Rate Ratio (×)", font=dict(size=11, color=_C_TEXT2),
                       x=0, pad=dict(l=0)),
            yaxis=dict(title="Ratio (×)"),
            xaxis=dict(title="Year"),
            showlegend=False,
        )
        st.plotly_chart(fig_r, use_container_width=True, key="im_air_ocean_ratio")

    with col_spread:
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(
            x=years, y=spreads,
            mode="lines+markers",
            line=dict(color=_C_WARN, width=2.5),
            marker=dict(size=6, color=_C_WARN),
            fill="tozeroy",
            fillcolor="rgba(245,158,11,0.08)",
            name="Spread $/kg",
            hovertemplate="Year: %{x}<br>Spread: $%{y:.2f}/kg<extra></extra>",
        ))

        fig_s = _fig_layout(fig_s, height=300)
        fig_s.update_layout(
            title=dict(text="Absolute Air Premium ($/kg over ocean)", font=dict(size=11, color=_C_TEXT2),
                       x=0, pad=dict(l=0)),
            yaxis=dict(title="Spread ($/kg)"),
            xaxis=dict(title="Year"),
            showlegend=False,
        )
        st.plotly_chart(fig_s, use_container_width=True, key="im_air_ocean_spread")

    st.caption(
        "Ocean baseline: $0.06/kg.  Rate spread = (air/ocean ratio × $0.06) − $0.06.  "
        "COVID-19 peak (2021): air reached ~$9/kg, a spread of ~$8.94/kg over ocean."
    )


# ---------------------------------------------------------------------------
# Section 9 — Inland Port Connectivity
# ---------------------------------------------------------------------------

def _render_inland_port_connectivity() -> None:
    _divider("SECTION 8 — INLAND PORT CONNECTIVITY")
    _section_header(
        "Port Rail & Road Connectivity Scores",
        "Which major ports have the best inland transport networks for multimodal access?",
        "🔗",
    )

    # Radar-style grouped bar
    ports   = [p["port"] for p in _PORT_CONNECTIVITY]
    rail_sc = [p["rail_score"] for p in _PORT_CONNECTIVITY]
    road_sc = [p["road_score"] for p in _PORT_CONNECTIVITY]
    ww_sc   = [p["inland_ww"] for p in _PORT_CONNECTIVITY]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Rail Score",
        x=ports, y=rail_sc,
        marker_color=_C_HIGH, marker_opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Rail: %{y}/100<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Road Score",
        x=ports, y=road_sc,
        marker_color=_C_ACCENT, marker_opacity=0.75,
        hovertemplate="<b>%{x}</b><br>Road: %{y}/100<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Inland Waterway",
        x=ports, y=ww_sc,
        marker_color=_C_TEAL, marker_opacity=0.65,
        hovertemplate="<b>%{x}</b><br>Inland WW: %{y}/100<extra></extra>",
    ))

    fig = _fig_layout(fig, height=360)
    fig.update_layout(
        barmode="group",
        xaxis=dict(tickangle=-25, gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(title="Connectivity Score (0–100)", range=[0, 105]),
    )
    st.plotly_chart(fig, use_container_width=True, key="im_port_connectivity")

    # Detail table
    st.markdown(
        f'<div style="font-size:0.68rem;font-weight:700;color:{_C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px">'
        f'Port Detail</div>',
        unsafe_allow_html=True,
    )

    hdr_cols = st.columns([1.8, 0.5, 0.5, 0.5, 1.8, 2.0])
    for col, h in zip(hdr_cols, ["Port", "Rail", "Road", "Inland WW", "Rail Connections", "Key Hinterland"]):
        col.markdown(
            f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.06em">{h}</div>',
            unsafe_allow_html=True,
        )

    for p in _PORT_CONNECTIVITY:
        rc_color = _C_HIGH if p["rail_score"] >= 85 else _C_WARN if p["rail_score"] >= 70 else _C_DANGER
        cols_r = st.columns([1.8, 0.5, 0.5, 0.5, 1.8, 2.0])
        cols_r[0].markdown(
            f'<div style="font-size:0.8rem;font-weight:700;color:{_C_TEXT};padding:5px 0">'
            f'{p["port"]} <span style="color:{_C_TEXT3};font-weight:400">({p["country"]})</span>'
            f'</div>', unsafe_allow_html=True)
        cols_r[1].markdown(
            f'<div style="font-size:0.8rem;font-weight:700;color:{rc_color};padding:5px 0">'
            f'{p["rail_score"]}</div>', unsafe_allow_html=True)
        cols_r[2].markdown(
            f'<div style="font-size:0.8rem;color:{_C_ACCENT};padding:5px 0">'
            f'{p["road_score"]}</div>', unsafe_allow_html=True)
        cols_r[3].markdown(
            f'<div style="font-size:0.8rem;color:{_C_TEAL};padding:5px 0">'
            f'{p["inland_ww"] if p["inland_ww"] else "—"}</div>', unsafe_allow_html=True)
        cols_r[4].markdown(
            f'<div style="font-size:0.74rem;color:{_C_TEXT2};padding:5px 0">'
            f'{p["rail_connections"]}</div>', unsafe_allow_html=True)
        cols_r[5].markdown(
            f'<div style="font-size:0.74rem;color:{_C_TEXT3};padding:5px 0">'
            f'{p["key_hinterland"]}</div>', unsafe_allow_html=True)

    st.caption("Scores 0–100: composite index of route density, frequency, and capacity. "
               "Inland WW = inland waterway score (0 = not applicable).")


# ---------------------------------------------------------------------------
# Section 10 — Transshipment Hub Analysis
# ---------------------------------------------------------------------------

def _render_transshipment_hubs() -> None:
    _divider("SECTION 9 — TRANSSHIPMENT HUB ANALYSIS")
    _section_header(
        "Transshipment Hub Intelligence",
        "Singapore, Hong Kong, Rotterdam, Port Klang, and Colombo — volume, efficiency, and risk.",
        "⚓",
    )

    # Volume + transshipment % chart
    hubs   = [h["hub"] for h in _TRANSSHIPMENT_HUBS]
    totals = [h["total_teu_m"] for h in _TRANSSHIPMENT_HUBS]
    ts_pct = [h["transship_pct"] for h in _TRANSSHIPMENT_HUBS]
    ts_vol = [round(t * p / 100, 2) for t, p in zip(totals, ts_pct)]
    orig_v = [round(t - tv, 2) for t, tv in zip(totals, ts_vol)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Origin/Destination",
        x=hubs, y=orig_v,
        marker_color=_C_ACCENT, marker_opacity=0.80,
        hovertemplate="<b>%{x}</b><br>O/D: %{y:.2f}M TEU<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Transshipment",
        x=hubs, y=ts_vol,
        marker_color=_C_PURPLE, marker_opacity=0.80,
        hovertemplate="<b>%{x}</b><br>Transshipment: %{y:.2f}M TEU (%{customdata}%)<extra></extra>",
        customdata=ts_pct,
    ))

    # Annotation: total TEU
    for hub, total in zip(hubs, totals):
        fig.add_annotation(
            x=hub, y=total + 0.4,
            text=f"{total:.1f}M TEU",
            showarrow=False,
            font=dict(color=_C_TEXT, size=9),
        )

    fig = _fig_layout(fig, height=360)
    fig.update_layout(
        barmode="stack",
        xaxis=dict(gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(title="Volume (Million TEU)"),
    )
    st.plotly_chart(fig, use_container_width=True, key="im_transship_hubs")

    # Hub detail cards
    risk_colors = {"LOW": _C_HIGH, "MODERATE": _C_WARN, "HIGH": _C_DANGER}
    cols = st.columns(len(_TRANSSHIPMENT_HUBS))

    for col, hub in zip(cols, _TRANSSHIPMENT_HUBS):
        rcolor = risk_colors.get(hub["risk"], _C_TEXT3)
        with col:
            st.markdown(
                f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
                f'border-top:2px solid {rcolor};border-radius:10px;padding:14px 12px">'
                f'<div style="font-size:0.85rem;font-weight:800;color:{_C_TEXT};'
                f'margin-bottom:8px">{hub["hub"]}</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;'
                f'font-size:0.72rem;margin-bottom:10px">'
                f'<div><div style="color:{_C_TEXT3}">Total</div>'
                f'<div style="font-weight:700;color:{_C_TEXT}">{hub["total_teu_m"]}M TEU</div></div>'
                f'<div><div style="color:{_C_TEXT3}">T/S %</div>'
                f'<div style="font-weight:700;color:{_C_PURPLE}">{hub["transship_pct"]}%</div></div>'
                f'<div><div style="color:{_C_TEXT3}">Turnaround</div>'
                f'<div style="font-weight:700;color:{_C_TEXT}">{hub["anchorage_days"]}d</div></div>'
                f'<div><div style="color:{_C_TEXT3}">Depth</div>'
                f'<div style="font-weight:700;color:{_C_TEAL}">{hub["depth_m"]}m</div></div>'
                f'</div>'
                f'{_badge(hub["risk"], rcolor)}'
                f'<div style="font-size:0.68rem;color:{_C_TEXT3};margin-top:8px;line-height:1.45">'
                f'{_trunc(hub["note"], 110)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Section 11 — Last-Mile Cost Analysis
# ---------------------------------------------------------------------------

def _render_last_mile_cost() -> None:
    _divider("SECTION 10 — LAST-MILE COST ANALYSIS")
    _section_header(
        "Port to Final Destination — Last-Mile Economics",
        "Cost per TEU and transit days by mode and distance band.",
        "🏭",
    )

    segs   = [d["segment"] for d in _LAST_MILE_DATA]
    trucks = [d["truck"] for d in _LAST_MILE_DATA]
    rails  = [d["rail"] for d in _LAST_MILE_DATA]
    barges = [d["barge"] if d["barge"] is not None else 0 for d in _LAST_MILE_DATA]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Truck ($/TEU)",
        x=segs, y=trucks,
        marker_color=_C_WARN, marker_opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Truck: $%{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Rail ($/TEU)",
        x=segs, y=rails,
        marker_color=_C_HIGH, marker_opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Rail: $%{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Barge ($/TEU)",
        x=segs, y=barges,
        marker_color=_C_TEAL, marker_opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Barge: $%{y:,}<extra></extra>",
        text=["N/A" if d["barge"] is None else "" for d in _LAST_MILE_DATA],
    ))

    fig = _fig_layout(fig, height=360)
    fig.update_layout(
        barmode="group",
        xaxis=dict(tickangle=-15, gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(title="Cost per TEU (USD)"),
    )
    st.plotly_chart(fig, use_container_width=True, key="im_last_mile_cost")

    # Days comparison table
    st.markdown(
        f'<div style="font-size:0.68rem;font-weight:700;color:{_C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px">'
        f'Transit Days by Mode & Segment</div>',
        unsafe_allow_html=True,
    )

    hdr_cols = st.columns([2.2, 1, 1, 1, 1, 1, 1])
    for col, h in zip(hdr_cols, ["Segment", "Truck $", "Rail $", "Barge $",
                                  "Truck Days", "Rail Days", "Barge Days"]):
        col.markdown(
            f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.06em">{h}</div>',
            unsafe_allow_html=True,
        )

    for d in _LAST_MILE_DATA:
        rc = st.columns([2.2, 1, 1, 1, 1, 1, 1])
        rc[0].markdown(
            f'<div style="font-size:0.78rem;color:{_C_TEXT};padding:5px 0">'
            f'{d["segment"]}</div>', unsafe_allow_html=True)
        rc[1].markdown(
            f'<div style="font-size:0.78rem;color:{_C_WARN};padding:5px 0">'
            f'${d["truck"]:,}</div>', unsafe_allow_html=True)
        rc[2].markdown(
            f'<div style="font-size:0.78rem;color:{_C_HIGH};padding:5px 0">'
            f'${d["rail"]:,}</div>', unsafe_allow_html=True)
        rc[3].markdown(
            f'<div style="font-size:0.78rem;color:{_C_TEAL};padding:5px 0">'
            f'{"$" + str(d["barge"]) if d["barge"] else "—"}</div>',
            unsafe_allow_html=True)
        rc[4].markdown(
            f'<div style="font-size:0.78rem;color:{_C_TEXT2};padding:5px 0">'
            f'{d["days_truck"]}d</div>', unsafe_allow_html=True)
        rc[5].markdown(
            f'<div style="font-size:0.78rem;color:{_C_TEXT2};padding:5px 0">'
            f'{d["days_rail"]}d</div>', unsafe_allow_html=True)
        rc[6].markdown(
            f'<div style="font-size:0.78rem;color:{_C_TEXT3};padding:5px 0">'
            f'{"" + str(d["days_barge"]) + "d" if d["days_barge"] else "—"}</div>',
            unsafe_allow_html=True)

    st.caption(
        "Costs in USD per TEU (20-ft equivalent unit).  Barge not available for long inland hauls.  "
        "Rail savings increase with distance — at 1,200 km, rail is 46% cheaper than truck."
    )


# ---------------------------------------------------------------------------
# Section 12 — Belt and Road Corridor Analyzer
# ---------------------------------------------------------------------------

def _render_bri_analyzer() -> None:
    _divider("SECTION 11 — BELT AND ROAD INITIATIVE ANALYZER")
    _section_header(
        "China-Europe Rail Corridor Intelligence",
        "Seven main BRI corridors: transit times, cost vs ocean, and post-2022 disruption risk.",
        "🌏",
    )

    bri = BeltAndRoad()
    if not bri.CORRIDORS:
        st.info("No BRI corridor data is available.")
        return

    risk_colors: dict[str, str] = {
        "LOW": _C_HIGH, "MODERATE": _C_WARN, "HIGH": _C_DANGER
    }

    def _trunc_local(s: str, n: int) -> str:
        if not s:
            return ""
        return s[:n] + ("..." if len(s) > n else "")

    # --- BRI Scattergeo map ---
    fig_map = go.Figure()
    for corridor in bri.CORRIDORS:
        lats = corridor.lat_waypoints or []
        lons = corridor.lon_waypoints or []
        if not lats or not lons:
            logger.warning("BRI corridor '{}' has no waypoints; skipping", corridor.name)
            continue
        color = risk_colors.get(corridor.disruption_risk, _C_TEXT3)
        hover_txt = (
            f"<b>{corridor.name}</b><br>"
            f"{corridor.origin_city} → {corridor.dest_city}<br>"
            f"Transit: {corridor.transit_days} days<br>"
            f"Cost vs Ocean: {corridor.cost_vs_ocean_ratio}x<br>"
            f"Risk: {corridor.disruption_risk}<br>"
            f"<i>{_trunc_local(corridor.disruption_note, 80)}</i>"
            "<extra></extra>"
        )
        fig_map.add_trace(go.Scattergeo(
            lon=lons, lat=lats, mode="lines",
            line=dict(width=2.5, color=color),
            name=corridor.name, hoverinfo="skip", showlegend=False,
        ))
        fig_map.add_trace(go.Scattergeo(
            lon=[lons[0]], lat=[lats[0]], mode="markers+text",
            marker=dict(size=9, color=color, symbol="circle"),
            text=[corridor.origin_city],
            textposition="top right",
            textfont=dict(color=_C_TEXT, size=8),
            name=corridor.name,
            hovertemplate=hover_txt,
            showlegend=True,
        ))
        fig_map.add_trace(go.Scattergeo(
            lon=[lons[-1]], lat=[lats[-1]], mode="markers",
            marker=dict(size=9, color=color, symbol="square"),
            name=corridor.name + " (dest)",
            hovertemplate=hover_txt, showlegend=False,
        ))

    fig_map.update_layout(
        geo=dict(
            showland=True, landcolor="#1a2235",
            showocean=True, oceancolor=_C_BG,
            showlakes=False, showcountries=True,
            countrycolor="rgba(255,255,255,0.07)",
            bgcolor=_C_BG, projection_type="natural earth",
            center=dict(lon=60, lat=45),
            lonaxis=dict(range=[60, 180]),
            lataxis=dict(range=[15, 65]),
        ),
        paper_bgcolor=_C_BG, height=390,
        margin=dict(t=10, b=10, l=0, r=0),
        legend=dict(font=dict(color=_C_TEXT2, size=9), bgcolor="rgba(0,0,0,0)", x=0.01, y=0.99),
        font=dict(color=_C_TEXT, family="Inter, sans-serif"),
    )
    fig_map.add_annotation(
        x=0.01, y=0.06, xref="paper", yref="paper",
        text="Green = Low risk   Amber = Moderate   Red = High (Russia route disrupted post-2022)",
        showarrow=False, font=dict(color=_C_TEXT3, size=9), align="left",
    )
    st.plotly_chart(fig_map, use_container_width=True, key="im_bri_map")

    # Corridor detail table
    st.markdown(
        f'<div style="font-size:0.68rem;font-weight:700;color:{_C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px">'
        f'Corridor Detail Comparison</div>',
        unsafe_allow_html=True,
    )
    ocean_mid = bri.ocean_transit_days()
    col_hdrs = st.columns([1.5, 0.6, 0.7, 0.7, 2.2])
    for col, h in zip(col_hdrs, ["Corridor", "Days", "vs Ocean Cost", "Risk", "Disruption Note"]):
        col.markdown(
            f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.06em">{h}</div>',
            unsafe_allow_html=True,
        )

    for corridor in bri.CORRIDORS:
        risk_color = risk_colors.get(corridor.disruption_risk, _C_TEXT3)
        days_saved = round(ocean_mid - corridor.transit_days, 0)
        days_txt   = f"{corridor.transit_days}d"
        if days_saved > 0:
            days_txt += f" (−{int(days_saved)}d)"

        row_cols = st.columns([1.5, 0.6, 0.7, 0.7, 2.2])
        row_cols[0].markdown(
            f'<div style="font-size:0.8rem;font-weight:700;color:{_C_TEXT};padding:5px 0">'
            f'{corridor.name}</div>', unsafe_allow_html=True)
        row_cols[1].markdown(
            f'<div style="font-size:0.8rem;color:{_C_TEXT};padding:5px 0">'
            f'{days_txt}</div>', unsafe_allow_html=True)
        row_cols[2].markdown(
            f'<div style="font-size:0.8rem;color:{_C_WARN};padding:5px 0">'
            f'{corridor.cost_vs_ocean_ratio}x ocean</div>', unsafe_allow_html=True)
        row_cols[3].markdown(
            f'<div style="padding:5px 0">{_badge(corridor.disruption_risk, risk_color)}</div>',
            unsafe_allow_html=True)
        row_cols[4].markdown(
            f'<div style="font-size:0.72rem;color:{_C_TEXT2};padding:5px 0;line-height:1.4">'
            f'{_trunc_local(corridor.disruption_note, 120)}</div>', unsafe_allow_html=True)

    # BRI growth chart
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    years_bri, growth_vals = bri.get_growth_series()
    fig_growth = go.Figure()
    fig_growth.add_trace(go.Scatter(
        x=years_bri, y=growth_vals,
        mode="lines+markers",
        fill="tozeroy",
        fillcolor="rgba(16,185,129,0.08)",
        line=dict(color=_C_HIGH, width=2.5),
        marker=dict(size=6, color=_C_HIGH),
        name="BRI Volume Index (2015=100)",
        hovertemplate="Year: %{x}<br>Index: %{y:.0f}<extra></extra>",
    ))
    fig_growth.add_vline(
        x=2022, line_width=1.5, line_dash="dash", line_color=_C_DANGER,
        annotation_text="Russia sanctions",
        annotation_font=dict(color=_C_DANGER, size=9),
        annotation_position="top right",
    )

    fig_growth = _fig_layout(fig_growth, height=260)
    fig_growth.update_layout(
        xaxis=dict(title="Year"),
        yaxis=dict(title="Volume Index (2015 = 100)"),
        showlegend=False,
    )
    st.plotly_chart(fig_growth, use_container_width=True, key="im_bri_growth")

    # BRI market share callout
    st.markdown(
        f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER2};'
        f'border-radius:10px;padding:16px 20px;display:flex;gap:20px;'
        f'align-items:center;margin-top:4px">'
        f'<div><div style="font-size:0.65rem;color:{_C_TEXT3}">BRI Market Share</div>'
        f'<div style="font-size:1.8rem;font-weight:900;color:{_C_HIGH}">'
        f'{bri.MARKET_SHARE_PCT}%</div>'
        f'<div style="font-size:0.68rem;color:{_C_TEXT3}">of Asia-Europe trade (2026)</div></div>'
        f'<div style="font-size:0.8rem;color:{_C_TEXT2};flex:1;line-height:1.6">'
        f'BRI rail holds ~5% of Asia-Europe trade by volume, up from near-zero in 2015. '
        f'Rail excels when cargo is time-sensitive but too heavy for air (100 kg–5 t), '
        f'or when Suez Canal disruptions add ocean delays. '
        f'The Russia route suspension post-2022 forced re-routing via the Middle Corridor '
        f'(Kazakhstan-Caspian ferry-Georgia/Turkey), adding cost and 5-7 transit days.'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # CSV download
    def _bri_csv() -> str:
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["Corridor", "Origin", "Destination", "Transit Days",
                    "Cost vs Ocean (x)", "Disruption Risk", "Note"])
        for corridor in bri.CORRIDORS:
            w.writerow([
                corridor.name, corridor.origin_city, corridor.dest_city,
                corridor.transit_days, corridor.cost_vs_ocean_ratio,
                corridor.disruption_risk, corridor.disruption_note,
            ])
        return buf.getvalue()

    st.download_button(
        "Download BRI corridor data (CSV)", data=_bri_csv(),
        file_name="bri_corridors.csv", mime="text/csv", key="im_bri_download",
    )


# ---------------------------------------------------------------------------
# Section 13 — Carbon Cost of Mode Choice
# ---------------------------------------------------------------------------

def _render_carbon_cost(freight_data: dict) -> None:
    _divider("SECTION 12 — CARBON COST OF MODE CHOICE")
    _section_header(
        "Total Cost Including Carbon Offset",
        "Freight cost + EU ETS carbon offset ($80/tonne CO₂) stacked by mode.",
        "🌿",
    )

    weight_kg = st.slider(
        "Shipment weight for carbon comparison (kg)",
        min_value=100, max_value=50_000, value=5_000, step=100,
        key="im_carbon_weight",
    )

    try:
        carbon_data = compute_carbon_costs(float(weight_kg))
    except Exception as exc:
        st.warning(f"Carbon cost data unavailable: {exc}")
        return

    mode_order = ["OCEAN", "AIR", "RAIL_CHINA_EUROPE", "TRUCK_EU", "RAIL_US"]
    mode_keys  = [m for m in mode_order if m in carbon_data]
    if not mode_keys:
        st.info("Carbon cost data is not available for the selected parameters.")
        return

    mode_names   = [_MODE_LABELS.get(m, m) for m in mode_keys]
    freight_vals = [carbon_data[m].get("freight_cost_usd", 0.0) for m in mode_keys]
    carbon_vals  = [carbon_data[m].get("carbon_offset_usd", 0.0) for m in mode_keys]
    co2_kg_vals  = [carbon_data[m].get("co2_kg", 0.0) for m in mode_keys]
    total_vals   = [carbon_data[m].get("total_cost_usd", 0.0) for m in mode_keys]
    bar_colors   = [_MODE_COLORS.get(m, "#64748b") for m in mode_keys]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Freight Cost (USD)",
        x=mode_names, y=freight_vals,
        marker_color=bar_colors, marker_opacity=0.85,
        hovertemplate="<b>%{x} — Freight</b><br>$%{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Carbon Offset (EU ETS $80/t)",
        x=mode_names, y=carbon_vals,
        marker_color="rgba(239,68,68,0.55)",
        marker_line=dict(color="rgba(239,68,68,0.8)", width=1),
        hovertemplate="<b>%{x} — Carbon</b><br>$%{y:,.2f}<extra></extra>",
    ))

    for name, total, co2 in zip(mode_names, total_vals, co2_kg_vals):
        fig.add_annotation(
            x=name, y=total,
            text=f"${total:,.0f}<br>{co2:.0f} kg CO₂",
            showarrow=False, yshift=12,
            font=dict(color=_C_TEXT, size=8),
        )

    fig = _fig_layout(fig, height=420)
    fig.update_layout(
        barmode="stack",
        xaxis=dict(gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(title="Cost (USD)", type="log"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True, key="im_carbon_cost_chart")

    # Summary table
    col_w = [1.2, 1, 1, 1, 1]
    hdr_cols = st.columns(col_w)
    for col, h in zip(hdr_cols, ["Mode", "Freight Cost", "Carbon Offset", "Total Cost", "CO₂ (kg)"]):
        col.markdown(
            f'<div style="font-size:0.62rem;font-weight:700;color:{_C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.06em">{h}</div>',
            unsafe_allow_html=True,
        )

    for mk, name, fc, cc, tot, co2 in zip(
        mode_keys, mode_names, freight_vals, carbon_vals, total_vals, co2_kg_vals
    ):
        color = _MODE_COLORS.get(mk, _C_TEXT)
        rc = st.columns(col_w)
        rc[0].markdown(
            f'<div style="font-size:0.82rem;font-weight:700;color:{color};padding:5px 0">'
            f'{name}</div>', unsafe_allow_html=True)
        rc[1].markdown(
            f'<div style="font-size:0.82rem;color:{_C_TEXT};padding:5px 0">'
            f'${fc:,.0f}</div>', unsafe_allow_html=True)
        rc[2].markdown(
            f'<div style="font-size:0.82rem;color:{_C_DANGER};padding:5px 0">'
            f'${cc:,.0f}</div>', unsafe_allow_html=True)
        rc[3].markdown(
            f'<div style="font-size:0.82rem;font-weight:700;color:{_C_TEXT};padding:5px 0">'
            f'${tot:,.0f}</div>', unsafe_allow_html=True)
        rc[4].markdown(
            f'<div style="font-size:0.82rem;color:{_C_TEXT2};padding:5px 0">'
            f'{co2:.1f} kg</div>', unsafe_allow_html=True)

    st.caption(
        "Carbon offset cost at EU ETS $80/tonne CO₂.  "
        "For air freight, the carbon offset often exceeds the freight cost itself.  "
        "Y-axis log-scale to show ocean/rail alongside air on the same chart."
    )

    def _carbon_csv() -> str:
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["Mode", "Freight Cost USD", "Carbon Offset USD",
                    "Total Cost USD", "CO2 kg", f"Weight kg: {weight_kg}"])
        for mk, name, fc, cc, tot, co2 in zip(
            mode_keys, mode_names, freight_vals, carbon_vals, total_vals, co2_kg_vals
        ):
            w.writerow([name, round(fc, 2), round(cc, 2), round(tot, 2), round(co2, 2)])
        return buf.getvalue()

    st.download_button(
        "Download carbon cost data (CSV)", data=_carbon_csv(),
        file_name="carbon_cost_comparison.csv", mime="text/csv",
        key="im_carbon_download",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    route_results: list,
    freight_data: dict,
    macro_data: dict,
    port_results: list,
) -> None:
    """Render the Intermodal Transport tab.

    Parameters
    ----------
    route_results:
        List of route objects with ``route_id`` attribute; used to pre-select
        the default route in the Mode Optimizer form.
    freight_data:
        Freight rate data dict (may be empty).  Passed through for future
        rate-calibration enrichment.
    macro_data:
        Macro data dict (may be empty).  Reserved for elasticity adjustment.
    port_results:
        List of port objects (may be empty).  Reserved for dynamic port
        connectivity enrichment.
    """
    logger.info("tab_intermodal: render() called")

    if not isinstance(route_results, list):
        logger.warning("tab_intermodal: route_results is not a list; defaulting to []")
        route_results = []
    if not isinstance(port_results, list):
        logger.warning("tab_intermodal: port_results is not a list; defaulting to []")
        port_results = []

    try:
        _render_hero()
    except Exception as exc:
        logger.warning("tab_intermodal: _render_hero failed: {}", exc)

    try:
        _render_mode_optimizer(route_results)
    except Exception as exc:
        logger.error("tab_intermodal: _render_mode_optimizer failed: {}", exc)
        st.error(f"Mode optimizer unavailable: {exc}")

    try:
        _render_intermodal_overview()
    except Exception as exc:
        logger.error("tab_intermodal: _render_intermodal_overview failed: {}", exc)

    try:
        _render_mode_cost_comparison()
    except Exception as exc:
        logger.error("tab_intermodal: _render_mode_cost_comparison failed: {}", exc)

    try:
        _render_cost_time_scatter()
    except Exception as exc:
        logger.error("tab_intermodal: _render_cost_time_scatter failed: {}", exc)

    try:
        _render_air_freight_monitor()
    except Exception as exc:
        logger.error("tab_intermodal: _render_air_freight_monitor failed: {}", exc)

    try:
        _render_rail_utilization()
    except Exception as exc:
        logger.error("tab_intermodal: _render_rail_utilization failed: {}", exc)

    try:
        _render_air_ocean_spread()
    except Exception as exc:
        logger.error("tab_intermodal: _render_air_ocean_spread failed: {}", exc)

    try:
        _render_inland_port_connectivity()
    except Exception as exc:
        logger.error("tab_intermodal: _render_inland_port_connectivity failed: {}", exc)

    try:
        _render_transshipment_hubs()
    except Exception as exc:
        logger.error("tab_intermodal: _render_transshipment_hubs failed: {}", exc)

    try:
        _render_last_mile_cost()
    except Exception as exc:
        logger.error("tab_intermodal: _render_last_mile_cost failed: {}", exc)

    try:
        _render_bri_analyzer()
    except Exception as exc:
        logger.error("tab_intermodal: _render_bri_analyzer failed: {}", exc)

    try:
        _render_carbon_cost(freight_data)
    except Exception as exc:
        logger.error("tab_intermodal: _render_carbon_cost failed: {}", exc)

    logger.info("tab_intermodal: render() complete")


# ---------------------------------------------------------------------------
# Integration note (for app.py maintainer)
# ---------------------------------------------------------------------------
# from ui import tab_intermodal
# with tab_intermodal_ui:
#     tab_intermodal.render(route_results, freight_data, macro_data, port_results)
#
# Signature: render(route_results, freight_data, macro_data, port_results) -> None
