"""tab_cargo.py — Cargo Analysis: deep-dive into commodity flows, specialised cargo,
route intelligence, and import/export imbalances.

Sections
--------
1.  Hero banner  — key aggregate stats
2.  Cargo mix overview  — containerised trade donut + breakdown
3.  Top-10 HS code categories  — volume & value league table
4.  Cargo seasonality  — 12-month heat-stripe calendar per category
5.  High-value cargo routes  — which routes carry the richest cargo?
6.  Dangerous goods tracker  — DG volumes, classifications, route restrictions
7.  Reefer cargo analysis  — cold-chain volumes & specialised carrier utilisation
8.  Cargo loss / damage rates  — by route with industry benchmarks
9.  Import / export imbalance  — regional surplus vs. deficit map
10. Route cargo-mix selector  — per-route donut + characteristics table
11. Cargo value trend  — time-series or benchmark bar chart
"""

from __future__ import annotations

import calendar as _cal
import datetime
import logging

import plotly.graph_objects as go
import streamlit as st

logger = logging.getLogger(__name__)

from processing.cargo_analyzer import (
    CARGO_CHARACTERISTICS,
    HS_CATEGORIES,
    CargoFlowAnalysis,
    analyze_cargo_flows,
    get_route_cargo_mix,
    get_seasonal_cargo_calendar,
)
from utils.helpers import format_usd

# ---------------------------------------------------------------------------
# Colour palette (mirrors styles.py)
# ---------------------------------------------------------------------------
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_CARD2   = "#141d2e"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_BORDER2 = "rgba(255,255,255,0.04)"
_C_HIGH    = "#10b981"
_C_GROW    = "#3b82f6"
_C_STABLE  = "#64748b"
_C_DECLINE = "#ef4444"
_C_WARN    = "#f59e0b"
_C_ACCENT  = "#3b82f6"
_C_PURPLE  = "#8b5cf6"
_C_PINK    = "#ec4899"
_C_CYAN    = "#14b8a6"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"

# Category icons & colours
_ICONS: dict[str, str] = {
    "electronics": "🖥️",
    "machinery":   "⚙️",
    "automotive":  "🚗",
    "apparel":     "👕",
    "chemicals":   "🧪",
    "agriculture": "🌾",
    "metals":      "🔩",
    "other":       "📦",
}

_CAT_COLORS: dict[str, str] = {
    "electronics": "#3b82f6",
    "machinery":   "#f59e0b",
    "automotive":  "#8b5cf6",
    "apparel":     "#ec4899",
    "chemicals":   "#14b8a6",
    "agriculture": "#84cc16",
    "metals":      "#94a3b8",
    "other":       "#64748b",
}

_SIGNAL_COLORS: dict[str, str] = {
    "SURGING":   _C_HIGH,
    "GROWING":   _C_GROW,
    "STABLE":    _C_STABLE,
    "DECLINING": _C_DECLINE,
}

_ALL_ROUTES: list[str] = [
    "transpacific_eb",
    "asia_europe",
    "transpacific_wb",
    "transatlantic",
    "sea_transpacific_eb",
    "ningbo_europe",
    "middle_east_to_europe",
    "middle_east_to_asia",
    "south_asia_to_europe",
    "intra_asia_china_sea",
    "intra_asia_china_japan",
    "china_south_america",
    "europe_south_america",
    "med_hub_to_asia",
    "north_africa_to_europe",
    "us_east_south_america",
    "longbeach_to_asia",
]

_ROUTE_LABELS: dict[str, str] = {
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

# ---------------------------------------------------------------------------
# Dangerous goods data (IMDG-class breakdown + route restrictions)
# ---------------------------------------------------------------------------
_DG_CLASSES: list[dict] = [
    {"class": "Class 1 — Explosives",       "icon": "💥", "annual_teu": 420_000,  "color": "#ef4444", "restricted_routes": ["transpacific_eb", "asia_europe"]},
    {"class": "Class 2 — Gases",            "icon": "🫧", "annual_teu": 1_850_000, "color": "#f59e0b", "restricted_routes": ["transatlantic"]},
    {"class": "Class 3 — Flammable Liquids","icon": "🔥", "annual_teu": 3_200_000, "color": "#f97316", "restricted_routes": []},
    {"class": "Class 4 — Flammable Solids", "icon": "🟧", "annual_teu": 980_000,  "color": "#eab308", "restricted_routes": ["south_asia_to_europe"]},
    {"class": "Class 5 — Oxidizers",        "icon": "⚗️",  "annual_teu": 740_000,  "color": "#84cc16", "restricted_routes": []},
    {"class": "Class 6 — Toxic Substances", "icon": "☠️",  "annual_teu": 1_100_000,"color": "#8b5cf6", "restricted_routes": ["middle_east_to_europe", "middle_east_to_asia"]},
    {"class": "Class 8 — Corrosives",       "icon": "🧴", "annual_teu": 2_400_000, "color": "#14b8a6", "restricted_routes": []},
    {"class": "Class 9 — Miscellaneous DG", "icon": "🔵", "annual_teu": 1_600_000, "color": "#64748b", "restricted_routes": []},
]

# ---------------------------------------------------------------------------
# Reefer cargo data
# ---------------------------------------------------------------------------
_REEFER_COMMODITIES: list[dict] = [
    {"name": "Fresh Fruit & Vegetables", "icon": "🍎", "annual_teu": 8_200_000,  "temp_c": "2–8",   "color": "#84cc16"},
    {"name": "Frozen Meat & Seafood",    "icon": "🥩", "annual_teu": 6_400_000,  "temp_c": "-18",   "color": "#3b82f6"},
    {"name": "Dairy Products",           "icon": "🧀", "annual_teu": 1_900_000,  "temp_c": "2–6",   "color": "#f59e0b"},
    {"name": "Pharmaceuticals",          "icon": "💊", "annual_teu": 1_100_000,  "temp_c": "2–8",   "color": "#ec4899"},
    {"name": "Cut Flowers",              "icon": "🌸", "annual_teu": 620_000,    "temp_c": "2–4",   "color": "#f43f5e"},
    {"name": "Chemicals (temp-ctrl)",    "icon": "🧪", "annual_teu": 840_000,    "temp_c": "15–25", "color": "#14b8a6"},
]

_REEFER_CARRIERS: list[dict] = [
    {"carrier": "Maersk",       "reefer_share_pct": 21.4, "fleet_reefer_slots": 382_000},
    {"carrier": "MSC",          "reefer_share_pct": 19.8, "fleet_reefer_slots": 354_000},
    {"carrier": "CMA CGM",      "reefer_share_pct": 14.2, "fleet_reefer_slots": 253_000},
    {"carrier": "Evergreen",    "reefer_share_pct": 8.6,  "fleet_reefer_slots": 153_000},
    {"carrier": "ONE",          "reefer_share_pct": 7.9,  "fleet_reefer_slots": 141_000},
    {"carrier": "Hapag-Lloyd",  "reefer_share_pct": 10.1, "fleet_reefer_slots": 180_000},
]

# ---------------------------------------------------------------------------
# Cargo loss / damage data
# ---------------------------------------------------------------------------
_CARGO_LOSS_RATES: list[dict] = [
    {"route": "transpacific_eb",        "loss_pct": 0.18, "primary_cause": "Weather / moisture"},
    {"route": "asia_europe",            "loss_pct": 0.22, "primary_cause": "Pilferage at transit hubs"},
    {"route": "transatlantic",          "loss_pct": 0.14, "primary_cause": "Rough weather / roll"},
    {"route": "sea_transpacific_eb",    "loss_pct": 0.26, "primary_cause": "Multi-port handling"},
    {"route": "south_asia_to_europe",   "loss_pct": 0.31, "primary_cause": "Pilferage / inadequate packing"},
    {"route": "china_south_america",    "loss_pct": 0.35, "primary_cause": "Long transit / humidity"},
    {"route": "middle_east_to_europe",  "loss_pct": 0.19, "primary_cause": "Heat damage (chemicals)"},
    {"route": "north_africa_to_europe", "loss_pct": 0.28, "primary_cause": "Reefer failures (agriculture)"},
    {"route": "europe_south_america",   "loss_pct": 0.17, "primary_cause": "Port handling damage"},
    {"route": "intra_asia_china_sea",   "loss_pct": 0.21, "primary_cause": "Frequent port calls"},
]

# ---------------------------------------------------------------------------
# Regional import/export imbalance
# ---------------------------------------------------------------------------
_REGION_IMBALANCE: list[dict] = [
    {"region": "East Asia",       "exports_b": 1_240, "imports_b": 680,  "icon": "🌏"},
    {"region": "Southeast Asia",  "exports_b": 620,   "imports_b": 510,  "icon": "🌏"},
    {"region": "South Asia",      "exports_b": 340,   "imports_b": 480,  "icon": "🌏"},
    {"region": "North America W", "exports_b": 420,   "imports_b": 890,  "icon": "🌎"},
    {"region": "North America E", "exports_b": 310,   "imports_b": 640,  "icon": "🌎"},
    {"region": "Europe",          "exports_b": 780,   "imports_b": 850,  "icon": "🌍"},
    {"region": "Middle East",     "exports_b": 520,   "imports_b": 310,  "icon": "🌍"},
    {"region": "South America",   "exports_b": 290,   "imports_b": 350,  "icon": "🌎"},
    {"region": "Africa",          "exports_b": 180,   "imports_b": 260,  "icon": "🌍"},
]

# ---------------------------------------------------------------------------
# High-value route data ($/kg cargo value density)
# ---------------------------------------------------------------------------
_HIGH_VALUE_ROUTES: list[dict] = [
    {"route": "transpacific_eb",        "value_density_usd_kg": 42.3,  "top_commodity": "Semiconductors / Electronics"},
    {"route": "transatlantic",          "value_density_usd_kg": 38.7,  "top_commodity": "Machinery & Medical Equipment"},
    {"route": "asia_europe",            "value_density_usd_kg": 35.1,  "top_commodity": "Electronics & Automotive"},
    {"route": "intra_asia_china_japan", "value_density_usd_kg": 31.8,  "top_commodity": "Auto Parts & Precision Machinery"},
    {"route": "ningbo_europe",          "value_density_usd_kg": 29.4,  "top_commodity": "Electronics & Industrial Goods"},
    {"route": "europe_south_america",   "value_density_usd_kg": 24.6,  "top_commodity": "Machinery & Pharmaceuticals"},
    {"route": "sea_transpacific_eb",    "value_density_usd_kg": 22.8,  "top_commodity": "Electronics & Consumer Goods"},
    {"route": "china_south_america",    "value_density_usd_kg": 20.1,  "top_commodity": "Machinery & Electronics"},
    {"route": "middle_east_to_europe",  "value_density_usd_kg": 14.6,  "top_commodity": "Chemicals & Petrochemicals"},
    {"route": "north_africa_to_europe", "value_density_usd_kg": 8.3,   "top_commodity": "Agriculture & Metals"},
]

# ---------------------------------------------------------------------------
# HS Code top-10 detailed breakdown
# ---------------------------------------------------------------------------
_HS_TOP10: list[dict] = [
    {"rank": 1,  "hs_prefix": "8471/8517",  "desc": "Computers & Telecom Equipment",  "category": "electronics", "volume_mteu": 14.2, "value_b": 780, "yoy": +8.1},
    {"rank": 2,  "hs_prefix": "8703",       "desc": "Passenger Motor Vehicles",       "category": "automotive",  "volume_mteu": 11.8, "value_b": 620, "yoy": +2.4},
    {"rank": 3,  "hs_prefix": "8479/8413",  "desc": "Industrial Machinery",           "category": "machinery",   "volume_mteu": 9.6,  "value_b": 540, "yoy": +4.1},
    {"rank": 4,  "hs_prefix": "2902/3901",  "desc": "Petrochemicals & Plastics",      "category": "chemicals",   "volume_mteu": 8.4,  "value_b": 390, "yoy": +3.8},
    {"rank": 5,  "hs_prefix": "6109/6110",  "desc": "Knitted Apparel & Garments",     "category": "apparel",     "volume_mteu": 7.9,  "value_b": 310, "yoy": +1.2},
    {"rank": 6,  "hs_prefix": "8542/8541",  "desc": "Semiconductors & ICs",           "category": "electronics", "volume_mteu": 6.7,  "value_b": 870, "yoy": +11.4},
    {"rank": 7,  "hs_prefix": "1001/1201",  "desc": "Wheat, Soybeans & Grains",       "category": "agriculture", "volume_mteu": 6.2,  "value_b": 180, "yoy": +5.9},
    {"rank": 8,  "hs_prefix": "7208/7209",  "desc": "Flat-Rolled Steel Products",     "category": "metals",      "volume_mteu": 5.8,  "value_b": 210, "yoy": -1.8},
    {"rank": 9,  "hs_prefix": "8708",       "desc": "Auto Parts & Accessories",       "category": "automotive",  "volume_mteu": 5.1,  "value_b": 290, "yoy": +3.2},
    {"rank": 10, "hs_prefix": "0901/0902",  "desc": "Coffee, Tea & Spices",           "category": "agriculture", "volume_mteu": 4.4,  "value_b": 95,  "yoy": +6.3},
]


# ---------------------------------------------------------------------------
# Shared HTML helpers
# ---------------------------------------------------------------------------

def _hex_to_rgba(hex_color: str, alpha: float = 0.35) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return f"rgba(100,116,139,{alpha})"


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:14px;margin:36px 0 20px">'
        f'<div style="flex:1;height:1px;background:linear-gradient(to right,rgba(255,255,255,0),rgba(255,255,255,0.08))"></div>'
        f'<span style="font-size:0.62rem;color:#475569;text-transform:uppercase;'
        f'letter-spacing:0.14em;font-weight:600;white-space:nowrap">{label}</span>'
        f'<div style="flex:1;height:1px;background:linear-gradient(to left,rgba(255,255,255,0),rgba(255,255,255,0.08))"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _badge(text: str, color: str, text_color: str = "#fff") -> str:
    bg = _hex_to_rgba(color, 0.18)
    return (
        f'<span style="background:{bg};color:{color};border:1px solid {_hex_to_rgba(color,0.35)};'
        f'font-size:0.65rem;font-weight:700;padding:2px 8px;border-radius:4px;'
        f'letter-spacing:0.05em;display:inline-block">{text}</span>'
    )


def _pill(text: str, color: str) -> str:
    bg = _hex_to_rgba(color, 0.15)
    return (
        f'<span style="background:{bg};color:{color};border:1px solid {_hex_to_rgba(color,0.3)};'
        f'font-size:0.62rem;font-weight:700;padding:1px 7px;border-radius:999px;'
        f'letter-spacing:0.04em;display:inline-block;white-space:nowrap">{text}</span>'
    )


def _progress_bar(fraction: float, color: str, height: int = 4) -> str:
    pct = min(100, max(0, fraction * 100))
    return (
        f'<div style="background:rgba(255,255,255,0.06);border-radius:999px;height:{height}px;overflow:hidden;margin-top:4px">'
        f'<div style="width:{pct:.1f}%;height:100%;background:{color};border-radius:999px"></div>'
        f'</div>'
    )


def _stat_card(label: str, value: str, sub: str = "", color: str = _C_ACCENT, icon: str = "") -> str:
    return (
        f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};border-top:2px solid {color};'
        f'border-radius:12px;padding:16px 18px;display:flex;flex-direction:column;gap:4px">'
        f'<div style="font-size:0.65rem;color:{_C_TEXT3};text-transform:uppercase;letter-spacing:0.1em;font-weight:600">'
        f'{icon + " " if icon else ""}{label}</div>'
        f'<div style="font-size:1.5rem;font-weight:800;color:{_C_TEXT};letter-spacing:-0.02em">{value}</div>'
        f'{"<div style=font-size:0.72rem;color:" + _C_TEXT2 + ">" + sub + "</div>" if sub else ""}'
        f'</div>'
    )


def _apply_dark_chart(fig: go.Figure, height: int = 380) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=height,
        margin=dict(l=16, r=16, t=40, b=40),
        font=dict(color=_C_TEXT, family="Inter, sans-serif", size=11),
        legend=dict(
            font=dict(color=_C_TEXT2, size=10),
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(255,255,255,0.06)",
        ),
    )
    return fig


# ---------------------------------------------------------------------------
# Section 0 — Hero Banner
# ---------------------------------------------------------------------------

def _render_hero(flows: list[CargoFlowAnalysis]) -> None:
    total_value = sum(f.total_value_usd for f in flows)
    surging = sum(1 for f in flows if f.demand_signal == "SURGING")
    declining = sum(1 for f in flows if f.demand_signal == "DECLINING")
    avg_growth = (
        sum(f.yoy_growth_pct for f in flows) / len(flows) if flows else 0.0
    )
    top_cat = max(flows, key=lambda f: f.total_value_usd, default=None)

    st.markdown(
        f'<h2 style="font-size:1.5rem;font-weight:800;color:{_C_TEXT};margin-bottom:2px;'
        f'letter-spacing:-0.02em">Cargo & Commodity Intelligence</h2>'
        f'<p style="font-size:0.82rem;color:{_C_TEXT2};margin-bottom:0;max-width:680px">'
        f'Deep-dive into HS-code categories, dangerous goods, cold chain, cargo loss rates, '
        f'regional imbalances, and route-level composition.</p>',
        unsafe_allow_html=True,
    )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    cols = st.columns(4)
    cards = [
        ("Total Tracked Value",   format_usd(total_value),        f"{len(flows)} categories",           _C_ACCENT, ""),
        ("Avg YoY Growth",        f"{avg_growth:+.1f}%",          "across all cargo categories",         _C_HIGH if avg_growth > 0 else _C_DECLINE, ""),
        ("Surging Categories",    str(surging),                   f"{declining} declining",              _C_HIGH, ""),
        ("Dominant Category",     top_cat.category_label if top_cat else "—",
                                  format_usd(top_cat.total_value_usd) if top_cat else "",              _C_WARN, _ICONS.get(top_cat.hs_category, "") if top_cat else ""),
    ]
    for col, (label, value, sub, color, icon) in zip(cols, cards):
        with col:
            st.markdown(_stat_card(label, value, sub, color, icon), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 1 — Cargo Mix Overview (Donut)
# ---------------------------------------------------------------------------

def _render_cargo_mix_overview(flows: list[CargoFlowAnalysis]) -> None:
    _divider("CARGO MIX OVERVIEW — CONTAINERISED TRADE BY COMMODITY")

    if not flows:
        st.info("No cargo flow data available.")
        return

    labels = [f.category_label for f in flows]
    values = [f.total_value_usd / 1e9 for f in flows]
    colors = [_CAT_COLORS.get(f.hs_category, "#64748b") for f in flows]
    icons  = [_ICONS.get(f.hs_category, "📦") for f in flows]
    display_labels = [f"{icons[i]} {labels[i]}" for i in range(len(labels))]

    col_donut, col_detail = st.columns([1, 1])

    with col_donut:
        total_b = sum(values)
        fig = go.Figure(
            go.Pie(
                labels=display_labels,
                values=values,
                hole=0.60,
                marker=dict(
                    colors=colors,
                    line=dict(color=_C_BG, width=3),
                ),
                textinfo="label+percent",
                textfont=dict(size=10.5, color=_C_TEXT),
                hovertemplate="<b>%{label}</b><br>Value: $%{value:.1f}B<br>Share: %{percent}<extra></extra>",
                pull=[0.04 if i == 0 else 0 for i in range(len(values))],
            )
        )
        fig.update_layout(
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_BG,
            height=400,
            margin=dict(l=0, r=0, t=40, b=0),
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.02,
                xanchor="center",
                x=0.5,
                font=dict(color=_C_TEXT2, size=9.5),
                bgcolor="rgba(0,0,0,0)",
            ),
            annotations=[
                dict(
                    text=f"<b>${total_b:.0f}B</b><br><span style='font-size:9px'>Total Trade</span>",
                    x=0.5, y=0.5,
                    font=dict(size=14, color=_C_TEXT),
                    showarrow=False,
                    align="center",
                )
            ],
        )
        st.plotly_chart(fig, use_container_width=True, key="cargo_mix_overview_donut")

    with col_detail:
        st.markdown(
            f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
            f'border-radius:12px;padding:18px 20px">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.7rem;font-weight:700;color:{_C_TEXT2};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:14px">Category Breakdown</div>',
            unsafe_allow_html=True,
        )
        max_val = max(values) if values else 1
        for f, val_b in sorted(zip(flows, values), key=lambda x: x[1], reverse=True):
            icon   = _ICONS.get(f.hs_category, "📦")
            color  = _CAT_COLORS.get(f.hs_category, "#64748b")
            yoy_s  = f"+{f.yoy_growth_pct:.1f}%" if f.yoy_growth_pct >= 0 else f"{f.yoy_growth_pct:.1f}%"
            yoy_c  = _C_HIGH if f.yoy_growth_pct >= 0 else _C_DECLINE
            sig_c  = _SIGNAL_COLORS.get(f.demand_signal, _C_STABLE)
            bar    = _progress_bar(val_b / max_val, color, 5)
            st.markdown(
                f'<div style="margin-bottom:14px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{_C_TEXT}">{icon} {f.category_label}</span>'
                f'<div style="display:flex;gap:6px;align-items:center">'
                f'<span style="font-size:0.72rem;color:{yoy_c};font-weight:700">{yoy_s}</span>'
                f'{_pill(f.demand_signal, sig_c)}'
                f'</div></div>'
                f'<div style="font-size:0.7rem;color:{_C_TEXT3};margin-top:1px">'
                f'${val_b:.1f}B · {val_b/sum(values)*100:.1f}% share</div>'
                f'{bar}</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 2 — Top-10 HS Code Categories
# ---------------------------------------------------------------------------

def _render_top_hs_codes() -> None:
    _divider("TOP 10 TRADED HS CODE CATEGORIES — VOLUME & VALUE")

    # Header row
    st.markdown(
        f'<div style="display:grid;grid-template-columns:32px 1fr 1fr 120px 120px 80px 80px;'
        f'gap:0 12px;padding:6px 16px;margin-bottom:2px;'
        f'font-size:0.62rem;font-weight:700;color:{_C_TEXT3};text-transform:uppercase;letter-spacing:0.08em">'
        f'<div>#</div><div>HS Prefix</div><div>Description</div>'
        f'<div style="text-align:right">Volume (M TEU)</div>'
        f'<div style="text-align:right">Value (USD B)</div>'
        f'<div style="text-align:right">YoY</div>'
        f'<div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    max_vol = max(r["volume_mteu"] for r in _HS_TOP10)
    max_val = max(r["value_b"] for r in _HS_TOP10)

    for i, row in enumerate(_HS_TOP10):
        cat    = row["category"]
        color  = _CAT_COLORS.get(cat, "#64748b")
        icon   = _ICONS.get(cat, "📦")
        yoy    = row["yoy"]
        yoy_s  = f"+{yoy:.1f}%" if yoy >= 0 else f"{yoy:.1f}%"
        yoy_c  = _C_HIGH if yoy >= 0 else _C_DECLINE
        bg     = _C_CARD if i % 2 == 0 else _C_CARD2
        vol_pct = row["volume_mteu"] / max_vol * 100
        val_pct = row["value_b"] / max_val * 100

        st.markdown(
            f'<div style="display:grid;grid-template-columns:32px 1fr 1fr 120px 120px 80px 80px;'
            f'gap:0 12px;padding:10px 16px;background:{bg};'
            f'border:1px solid {_C_BORDER2};border-radius:8px;margin-bottom:4px;align-items:center">'
            # rank
            f'<div style="font-size:0.75rem;font-weight:800;color:{color}">{row["rank"]}</div>'
            # hs prefix + icon
            f'<div style="font-size:0.75rem;color:{_C_TEXT2};font-family:monospace">'
            f'{icon} {row["hs_prefix"]}</div>'
            # description
            f'<div style="font-size:0.8rem;font-weight:600;color:{_C_TEXT}">{row["desc"]}</div>'
            # volume bar + number
            f'<div style="text-align:right">'
            f'<div style="font-size:0.8rem;font-weight:700;color:{_C_TEXT}">{row["volume_mteu"]:.1f}</div>'
            f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:3px;margin-top:3px">'
            f'<div style="width:{vol_pct:.0f}%;height:100%;background:{color};border-radius:2px"></div>'
            f'</div></div>'
            # value
            f'<div style="font-size:0.8rem;font-weight:700;color:{_C_TEXT};text-align:right">'
            f'${row["value_b"]}B'
            f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:3px;margin-top:3px">'
            f'<div style="width:{val_pct:.0f}%;height:100%;background:{color};border-radius:2px"></div>'
            f'</div></div>'
            # yoy
            f'<div style="font-size:0.8rem;font-weight:700;color:{yoy_c};text-align:right">{yoy_s}</div>'
            # category pill
            f'<div style="text-align:right">{_pill(HS_CATEGORIES.get(cat,{}).get("label",cat.title()), color)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 3 — Cargo Seasonality Heat Chart
# ---------------------------------------------------------------------------

def _render_seasonality() -> None:
    _divider("CARGO SEASONALITY — MONTHLY INTENSITY BY CATEGORY")

    current_month = datetime.date.today().month

    # Build a 7-category × 12-month matrix of relative intensity (0-1)
    # Peak month = 1.0, adjacent months taper off via a Gaussian-like curve
    import math
    categories = list(HS_CATEGORIES.keys())
    months = list(range(1, 13))

    def _intensity(cat: str, month: int) -> float:
        chars = CARGO_CHARACTERISTICS.get(cat, {})
        peak = chars.get("seasonal_peak", 6)
        dist = min(abs(month - peak), 12 - abs(month - peak))
        return math.exp(-0.5 * (dist / 2.5) ** 2)

    z = [[_intensity(cat, m) for m in months] for cat in categories]
    cat_labels = [f"{_ICONS.get(c,'📦')} {HS_CATEGORIES[c]['label']}" for c in categories]
    month_labels = [_cal.month_abbr[m] for m in months]

    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=month_labels,
            y=cat_labels,
            colorscale=[
                [0.0,  "#1e293b"],
                [0.3,  "#1e3a5f"],
                [0.6,  "#1d4ed8"],
                [0.85, "#10b981"],
                [1.0,  "#f59e0b"],
            ],
            showscale=True,
            colorbar=dict(
                title="Intensity",
                titleside="right",
                tickfont=dict(color=_C_TEXT2, size=9),
                titlefont=dict(color=_C_TEXT2, size=9),
                len=0.8,
                thickness=12,
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(255,255,255,0.08)",
            ),
            hovertemplate="<b>%{y}</b><br>%{x}: intensity %{z:.2f}<extra></extra>",
            xgap=2,
            ygap=2,
        )
    )

    # Current-month annotation
    fig.add_shape(
        type="rect",
        x0=current_month - 1.5,
        x1=current_month - 0.5,
        y0=-0.5,
        y1=len(categories) - 0.5,
        line=dict(color=_C_WARN, width=2),
        fillcolor="rgba(0,0,0,0)",
        layer="above",
    )
    fig.add_annotation(
        x=_cal.month_abbr[current_month],
        y=len(categories) - 0.5,
        text="NOW",
        showarrow=False,
        font=dict(color=_C_WARN, size=8, family="Inter, sans-serif"),
        bgcolor=_hex_to_rgba(_C_WARN, 0.15),
        bordercolor=_hex_to_rgba(_C_WARN, 0.4),
        borderpad=2,
        borderwidth=1,
        yanchor="bottom",
    )

    fig = _apply_dark_chart(fig, height=320)
    fig.update_layout(
        xaxis=dict(side="top", gridcolor="rgba(0,0,0,0)", color=_C_TEXT2),
        yaxis=dict(gridcolor="rgba(0,0,0,0)", color=_C_TEXT2),
        margin=dict(l=160, r=60, t=60, b=20),
    )
    st.plotly_chart(fig, use_container_width=True, key="cargo_seasonality_heat")
    st.caption(
        "Intensity is modelled as a Gaussian decay from each category's seasonal peak month. "
        "Gold = peak, dark blue = off-season. Current month highlighted in amber."
    )


# ---------------------------------------------------------------------------
# Section 4 — High-Value Cargo Routes
# ---------------------------------------------------------------------------

def _render_high_value_routes() -> None:
    _divider("HIGH-VALUE CARGO ROUTES — VALUE DENSITY (USD/KG)")

    routes_sorted = sorted(_HIGH_VALUE_ROUTES, key=lambda r: r["value_density_usd_kg"], reverse=True)
    max_density = routes_sorted[0]["value_density_usd_kg"]

    # Horizontal bar chart
    route_labels = [_ROUTE_LABELS.get(r["route"], r["route"]) for r in routes_sorted]
    densities    = [r["value_density_usd_kg"] for r in routes_sorted]
    bar_colors   = [
        _C_HIGH if d > 35 else (_C_ACCENT if d > 20 else _C_STABLE)
        for d in densities
    ]
    hover_texts  = [
        f"<b>{_ROUTE_LABELS.get(r['route'], r['route'])}</b><br>"
        f"Value density: ${r['value_density_usd_kg']:.1f}/kg<br>"
        f"Top commodity: {r['top_commodity']}"
        for r in routes_sorted
    ]

    fig = go.Figure(
        go.Bar(
            x=densities,
            y=route_labels,
            orientation="h",
            marker=dict(
                color=bar_colors,
                line=dict(color="rgba(0,0,0,0)", width=0),
                opacity=0.85,
            ),
            text=[f"${d:.1f}/kg" for d in densities],
            textposition="outside",
            textfont=dict(color=_C_TEXT2, size=10),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover_texts,
        )
    )
    fig = _apply_dark_chart(fig, height=380)
    fig.update_layout(
        xaxis=dict(
            title="Value Density (USD/kg)",
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.05)",
        ),
        yaxis=dict(color=_C_TEXT2, autorange="reversed"),
        showlegend=False,
        margin=dict(l=200, r=80, t=30, b=40),
    )
    st.plotly_chart(fig, use_container_width=True, key="high_value_routes_bar")

    # Annotation cards for top 3
    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    cols = st.columns(3)
    for col, r in zip(cols, routes_sorted[:3]):
        color = _C_HIGH if r["value_density_usd_kg"] > 35 else _C_ACCENT
        with col:
            st.markdown(
                f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
                f'border-left:3px solid {color};border-radius:10px;padding:12px 14px">'
                f'<div style="font-size:0.65rem;color:{_C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.1em;margin-bottom:4px">'
                f'#{routes_sorted.index(r)+1} Highest Value</div>'
                f'<div style="font-size:0.85rem;font-weight:700;color:{_C_TEXT};margin-bottom:4px">'
                f'{_ROUTE_LABELS.get(r["route"], r["route"])}</div>'
                f'<div style="font-size:1.2rem;font-weight:800;color:{color}">'
                f'${r["value_density_usd_kg"]:.1f}<span style="font-size:0.75rem;color:{_C_TEXT2}">/kg</span></div>'
                f'<div style="font-size:0.7rem;color:{_C_TEXT2};margin-top:4px">{r["top_commodity"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Section 5 — Dangerous Goods Tracker
# ---------------------------------------------------------------------------

def _render_dangerous_goods() -> None:
    _divider("DANGEROUS GOODS TRACKER — IMDG CLASS VOLUMES & ROUTE RESTRICTIONS")

    total_dg_teu = sum(d["annual_teu"] for d in _DG_CLASSES)
    max_teu = max(d["annual_teu"] for d in _DG_CLASSES)

    col_chart, col_info = st.columns([3, 2])

    with col_chart:
        dg_labels = [d["class"] for d in _DG_CLASSES]
        dg_values = [d["annual_teu"] / 1e6 for d in _DG_CLASSES]
        dg_colors = [d["color"] for d in _DG_CLASSES]
        dg_icons  = [d["icon"] for d in _DG_CLASSES]

        fig = go.Figure(
            go.Bar(
                x=[f'{dg_icons[i]} {dg_labels[i]}' for i in range(len(dg_labels))],
                y=dg_values,
                marker=dict(
                    color=dg_colors,
                    opacity=0.80,
                    line=dict(color="rgba(0,0,0,0)", width=0),
                ),
                text=[f"{v:.2f}M" for v in dg_values],
                textposition="outside",
                textfont=dict(color=_C_TEXT2, size=9.5),
                hovertemplate="<b>%{x}</b><br>Volume: %{y:.2f}M TEU/year<extra></extra>",
            )
        )
        fig = _apply_dark_chart(fig, height=320)
        fig.update_layout(
            xaxis=dict(
                color=_C_TEXT2,
                tickangle=-25,
                tickfont=dict(size=9),
                gridcolor="rgba(0,0,0,0)",
            ),
            yaxis=dict(
                title="Annual Volume (M TEU)",
                color=_C_TEXT2,
                gridcolor="rgba(255,255,255,0.05)",
            ),
            showlegend=False,
            margin=dict(l=50, r=20, t=40, b=100),
        )
        st.plotly_chart(fig, use_container_width=True, key="dg_volume_bar")

    with col_info:
        st.markdown(
            f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
            f'border-radius:12px;padding:16px 18px;height:100%">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:700;color:{_C_TEXT2};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">'
            f'Route Restrictions Summary</div>',
            unsafe_allow_html=True,
        )

        # Total DG stat
        st.markdown(
            f'<div style="margin-bottom:14px">'
            f'<div style="font-size:1.4rem;font-weight:800;color:{_C_TEXT}">'
            f'{total_dg_teu/1e6:.1f}M <span style="font-size:0.75rem;color:{_C_TEXT2};font-weight:400">TEU/year</span></div>'
            f'<div style="font-size:0.7rem;color:{_C_TEXT2}">Total DG cargo across all IMDG classes</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for d in _DG_CLASSES:
            if not d["restricted_routes"]:
                continue
            color = d["color"]
            restricted_labels = [_ROUTE_LABELS.get(r, r) for r in d["restricted_routes"]]
            st.markdown(
                f'<div style="margin-bottom:10px;padding:8px 10px;'
                f'background:{_hex_to_rgba(color,0.08)};border:1px solid {_hex_to_rgba(color,0.2)};'
                f'border-radius:8px">'
                f'<div style="font-size:0.73rem;font-weight:700;color:{color}">'
                f'{d["icon"]} {d["class"]}</div>'
                f'<div style="font-size:0.65rem;color:{_C_TEXT2};margin-top:3px">'
                f'Restrictions: {", ".join(restricted_labels)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    st.caption(
        "DG volumes are IMDG-class annual TEU estimates. Route restrictions indicate partial "
        "or conditional carriage limitations — consult carrier DG policies for definitive guidance."
    )


# ---------------------------------------------------------------------------
# Section 6 — Reefer Cargo Analysis
# ---------------------------------------------------------------------------

def _render_reefer_cargo() -> None:
    _divider("REEFER CARGO ANALYSIS — COLD CHAIN VOLUMES & CARRIER UTILISATION")

    total_reefer = sum(r["annual_teu"] for r in _REEFER_COMMODITIES)

    col_a, col_b = st.columns([1, 1])

    with col_a:
        # Commodity donut
        labels = [r["name"] for r in _REEFER_COMMODITIES]
        values = [r["annual_teu"] / 1e6 for r in _REEFER_COMMODITIES]
        colors = [r["color"] for r in _REEFER_COMMODITIES]
        icons  = [r["icon"] for r in _REEFER_COMMODITIES]

        fig = go.Figure(
            go.Pie(
                labels=[f'{icons[i]} {labels[i]}' for i in range(len(labels))],
                values=values,
                hole=0.55,
                marker=dict(colors=colors, line=dict(color=_C_BG, width=3)),
                textinfo="label+percent",
                textfont=dict(size=9.5, color=_C_TEXT),
                hovertemplate="<b>%{label}</b><br>Volume: %{value:.2f}M TEU<br>Share: %{percent}<extra></extra>",
            )
        )
        fig.update_layout(
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_BG,
            height=350,
            margin=dict(l=0, r=0, t=30, b=0),
            legend=dict(
                font=dict(color=_C_TEXT2, size=9),
                bgcolor="rgba(0,0,0,0)",
                orientation="h",
                y=-0.05,
                xanchor="center",
                x=0.5,
            ),
            annotations=[
                dict(
                    text=f"<b>{total_reefer/1e6:.1f}M</b><br><span style='font-size:9px'>TEU/yr</span>",
                    x=0.5, y=0.5,
                    font=dict(size=13, color=_C_TEXT),
                    showarrow=False,
                )
            ],
        )
        st.plotly_chart(fig, use_container_width=True, key="reefer_commodity_donut")

        # Temperature requirements table
        st.markdown(
            f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
            f'border-radius:10px;padding:12px 14px;margin-top:4px">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.65rem;font-weight:700;color:{_C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">Temperature Requirements</div>',
            unsafe_allow_html=True,
        )
        for r in _REEFER_COMMODITIES:
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'margin-bottom:5px;font-size:0.75rem">'
                f'<span style="color:{_C_TEXT2}">{r["icon"]} {r["name"]}</span>'
                f'<span style="color:{r["color"]};font-weight:700;font-family:monospace">{r["temp_c"]}°C</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with col_b:
        # Carrier utilisation
        carrier_names = [c["carrier"] for c in _REEFER_CARRIERS]
        carrier_shares = [c["reefer_share_pct"] for c in _REEFER_CARRIERS]
        carrier_slots  = [c["fleet_reefer_slots"] for c in _REEFER_CARRIERS]
        carrier_colors = ["#3b82f6", "#14b8a6", "#f59e0b", "#10b981", "#8b5cf6", "#ec4899"]

        fig2 = go.Figure()
        fig2.add_trace(
            go.Bar(
                name="Market Share %",
                x=carrier_names,
                y=carrier_shares,
                marker=dict(color=carrier_colors, opacity=0.85),
                hovertemplate="<b>%{x}</b><br>Reefer share: %{y:.1f}%<extra></extra>",
                yaxis="y",
            )
        )
        fig2.add_trace(
            go.Scatter(
                name="Reefer Slots",
                x=carrier_names,
                y=carrier_slots,
                mode="lines+markers",
                marker=dict(size=8, color=_C_TEXT, symbol="diamond"),
                line=dict(color=_C_TEXT, width=1.5, dash="dot"),
                hovertemplate="<b>%{x}</b><br>Fleet reefer slots: %{y:,}<extra></extra>",
                yaxis="y2",
            )
        )
        fig2.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_SURFACE,
            height=350,
            margin=dict(l=50, r=60, t=40, b=40),
            font=dict(color=_C_TEXT, family="Inter, sans-serif", size=10),
            legend=dict(font=dict(color=_C_TEXT2, size=9), bgcolor="rgba(0,0,0,0)", x=0.01, y=0.99),
            barmode="group",
            yaxis=dict(
                title="Market Share (%)",
                color=_C_TEXT2,
                gridcolor="rgba(255,255,255,0.05)",
            ),
            yaxis2=dict(
                title="Fleet Reefer Slots",
                overlaying="y",
                side="right",
                color=_C_TEXT2,
                gridcolor="rgba(0,0,0,0)",
                showgrid=False,
            ),
            xaxis=dict(color=_C_TEXT2, gridcolor="rgba(0,0,0,0)"),
        )
        st.plotly_chart(fig2, use_container_width=True, key="reefer_carrier_chart")

        # Insight blurb
        st.markdown(
            f'<div style="background:{_hex_to_rgba(_C_CYAN,0.08)};border:1px solid {_hex_to_rgba(_C_CYAN,0.2)};'
            f'border-radius:10px;padding:14px 16px;margin-top:6px">'
            f'<div style="font-size:0.78rem;font-weight:700;color:{_C_CYAN};margin-bottom:6px">Cold Chain Insight</div>'
            f'<div style="font-size:0.73rem;color:{_C_TEXT2};line-height:1.55">'
            f'Reefer capacity is tightening as pharmaceutical cold-chain volumes surged +18% YoY, '
            f'compressing slot availability on peak banana/berry routes (Apr–Jun). Maersk and MSC '
            f'account for >40% of global reefer capacity. Controlled-atmosphere containers are '
            f'increasingly deployed for high-value produce, extending shelf life by 40–60 days.'
            f'</div></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 7 — Cargo Loss / Damage Rates
# ---------------------------------------------------------------------------

def _render_loss_rates() -> None:
    _divider("CARGO LOSS & DAMAGE RATES — BY ROUTE vs. INDUSTRY BENCHMARK")

    BENCHMARK_PCT = 0.20  # industry average loss/damage rate

    loss_sorted = sorted(_CARGO_LOSS_RATES, key=lambda r: r["loss_pct"], reverse=True)
    route_labels = [_ROUTE_LABELS.get(r["route"], r["route"]) for r in loss_sorted]
    loss_vals    = [r["loss_pct"] for r in loss_sorted]
    causes       = [r["primary_cause"] for r in loss_sorted]

    bar_colors = [
        _C_DECLINE if v > BENCHMARK_PCT * 1.2
        else (_C_WARN if v > BENCHMARK_PCT
              else _C_HIGH)
        for v in loss_vals
    ]

    hover_texts = [
        f"<b>{route_labels[i]}</b><br>"
        f"Loss rate: {loss_vals[i]:.2f}%<br>"
        f"vs benchmark: {BENCHMARK_PCT:.2f}%<br>"
        f"Primary cause: {causes[i]}"
        for i in range(len(loss_sorted))
    ]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=loss_vals,
            y=route_labels,
            orientation="h",
            marker=dict(color=bar_colors, opacity=0.82),
            text=[f"{v:.2f}%" for v in loss_vals],
            textposition="outside",
            textfont=dict(color=_C_TEXT2, size=10),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover_texts,
        )
    )
    # Industry benchmark line
    fig.add_vline(
        x=BENCHMARK_PCT,
        line=dict(color=_C_WARN, width=1.5, dash="dash"),
        annotation_text="Industry Avg",
        annotation_font=dict(color=_C_WARN, size=9),
        annotation_position="top right",
    )

    fig = _apply_dark_chart(fig, height=360)
    fig.update_layout(
        xaxis=dict(
            title="Loss / Damage Rate (%)",
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.05)",
            tickformat=".2f",
        ),
        yaxis=dict(color=_C_TEXT2, autorange="reversed"),
        showlegend=False,
        margin=dict(l=220, r=60, t=30, b=40),
    )
    st.plotly_chart(fig, use_container_width=True, key="cargo_loss_bar")

    # Cause breakdown pills
    col_a, col_b, col_c = st.columns(3)
    cause_groups: dict[str, list[str]] = {}
    for r in loss_sorted:
        cause_groups.setdefault(r["primary_cause"], []).append(
            _ROUTE_LABELS.get(r["route"], r["route"])
        )

    for idx, (cause, affected) in enumerate(cause_groups.items()):
        target_col = [col_a, col_b, col_c][idx % 3]
        with target_col:
            st.markdown(
                f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
                f'border-radius:8px;padding:10px 12px;margin-bottom:8px">'
                f'<div style="font-size:0.7rem;font-weight:700;color:{_C_WARN};margin-bottom:4px">{cause}</div>'
                f'<div style="font-size:0.65rem;color:{_C_TEXT3}">{" · ".join(affected)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Section 8 — Import / Export Regional Imbalance
# ---------------------------------------------------------------------------

def _render_imbalance() -> None:
    _divider("IMPORT / EXPORT REGIONAL IMBALANCE — TRADE SURPLUS vs. DEFICIT")

    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        regions = [r["region"] for r in _REGION_IMBALANCE]
        exports = [r["exports_b"] for r in _REGION_IMBALANCE]
        imports = [r["imports_b"] for r in _REGION_IMBALANCE]
        balances = [e - i for e, i in zip(exports, imports)]

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                name="Exports (USD B)",
                x=regions,
                y=exports,
                marker=dict(color=_C_HIGH, opacity=0.80),
                hovertemplate="<b>%{x}</b><br>Exports: $%{y}B<extra></extra>",
            )
        )
        fig.add_trace(
            go.Bar(
                name="Imports (USD B)",
                x=regions,
                y=[-v for v in imports],
                marker=dict(color=_C_DECLINE, opacity=0.80),
                hovertemplate="<b>%{x}</b><br>Imports: $%{customdata}B<extra></extra>",
                customdata=imports,
            )
        )
        fig = _apply_dark_chart(fig, height=380)
        fig.update_layout(
            barmode="relative",
            xaxis=dict(color=_C_TEXT2, tickangle=-25, gridcolor="rgba(0,0,0,0)"),
            yaxis=dict(
                title="USD Billion",
                color=_C_TEXT2,
                gridcolor="rgba(255,255,255,0.05)",
                tickformat=".0f",
            ),
            legend=dict(
                font=dict(color=_C_TEXT2, size=10),
                bgcolor="rgba(0,0,0,0)",
            ),
            margin=dict(l=50, r=20, t=40, b=80),
        )
        fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.15)", width=1))
        st.plotly_chart(fig, use_container_width=True, key="imbalance_bar")

    with col_table:
        st.markdown(
            f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
            f'border-radius:12px;padding:16px 18px">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:700;color:{_C_TEXT2};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">Trade Balance Summary</div>',
            unsafe_allow_html=True,
        )

        max_abs_balance = max(abs(e - i) for e, i in zip(exports, imports))

        for r in sorted(_REGION_IMBALANCE, key=lambda x: x["exports_b"] - x["imports_b"], reverse=True):
            balance = r["exports_b"] - r["imports_b"]
            is_surplus = balance >= 0
            color = _C_HIGH if is_surplus else _C_DECLINE
            label = f"+${balance}B surplus" if is_surplus else f"-${abs(balance)}B deficit"
            bar_w = abs(balance) / max_abs_balance

            st.markdown(
                f'<div style="margin-bottom:11px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">'
                f'<span style="font-size:0.8rem;font-weight:600;color:{_C_TEXT}">'
                f'{r["icon"]} {r["region"]}</span>'
                f'<span style="font-size:0.73rem;font-weight:700;color:{color}">{label}</span>'
                f'</div>'
                f'{_progress_bar(bar_w, color, 4)}'
                f'<div style="font-size:0.65rem;color:{_C_TEXT3};margin-top:2px">'
                f'Ex: ${r["exports_b"]}B  ·  Im: ${r["imports_b"]}B</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

    st.caption(
        "Regional trade balances in USD billion (estimated annualised containerised trade). "
        "Positive = net exporter region; negative = net importer. Data excludes bulk and liquid bulk cargo."
    )


# ---------------------------------------------------------------------------
# Section 9 — Cargo Flow Sankey
# ---------------------------------------------------------------------------

def _render_sankey(flows: list[CargoFlowAnalysis]) -> None:
    _divider("CARGO FLOW — CATEGORY TO REGION TO DESTINATION")

    if not flows:
        st.info("Sankey diagram unavailable — no cargo flow data to display.")
        return

    categories   = list(HS_CATEGORIES.keys())
    orig_regions = ["Asia East", "Europe", "N. America West", "N. America East", "SE Asia", "Middle East", "South America", "South Asia", "Africa"]
    dest_regions = ["N. America West", "N. America East", "Europe", "Asia East", "SE Asia", "South America", "Middle East"]

    nodes: list[str] = (
        categories
        + ["Orig: " + r for r in orig_regions]
        + ["Dest: " + r for r in dest_regions]
    )
    node_idx = {n: i for i, n in enumerate(nodes)}

    node_colors = (
        [_CAT_COLORS.get(c, "#64748b") for c in categories]
        + [_hex_to_rgba(_C_ACCENT, 0.65)] * len(orig_regions)
        + [_hex_to_rgba(_C_HIGH, 0.65)] * len(dest_regions)
    )

    flow_map = {f.hs_category: f for f in flows}

    _CAT_REGION_FLOWS: list[tuple[str, str, str, float]] = [
        ("electronics",  "Asia East",          "N. America West", 0.38),
        ("electronics",  "Asia East",          "Europe",             0.28),
        ("electronics",  "SE Asia",     "N. America West", 0.18),
        ("machinery",    "Asia East",          "Europe",             0.30),
        ("machinery",    "Europe",             "N. America East", 0.28),
        ("machinery",    "Asia East",          "N. America West", 0.20),
        ("automotive",   "Asia East",          "N. America West", 0.35),
        ("automotive",   "Europe",             "N. America East", 0.30),
        ("apparel",      "Asia East",          "N. America West", 0.30),
        ("apparel",      "Asia East",          "Europe",             0.25),
        ("apparel",      "South Asia",         "Europe",             0.22),
        ("chemicals",    "Europe",             "Asia East",          0.28),
        ("chemicals",    "Middle East",        "Europe",             0.26),
        ("chemicals",    "N. America West", "Asia East",          0.22),
        ("agriculture",  "N. America West", "Asia East",          0.38),
        ("agriculture",  "South America",      "Asia East",          0.28),
        ("agriculture",  "N. America East", "Europe",             0.18),
        ("metals",       "Asia East",          "N. America West", 0.30),
        ("metals",       "Asia East",          "Europe",             0.26),
        ("metals",       "Middle East",        "Europe",             0.20),
    ]

    sources: list[int] = []
    targets: list[int] = []
    values_: list[float] = []
    link_colors_clean: list[str] = []

    for cat, orig, dest, weight in _CAT_REGION_FLOWS:
        cat_node  = cat
        orig_node = "Orig: " + orig
        dest_node = "Dest: " + dest

        if cat_node not in node_idx or orig_node not in node_idx or dest_node not in node_idx:
            continue

        scale = flow_map[cat].total_value_usd if cat in flow_map else 1_000_000_000
        flow_val = scale * weight / 1e8

        sources.append(node_idx[cat_node])
        targets.append(node_idx[orig_node])
        values_.append(flow_val)
        link_colors_clean.append(_hex_to_rgba(_CAT_COLORS.get(cat, "#64748b"), 0.38))

        sources.append(node_idx[orig_node])
        targets.append(node_idx[dest_node])
        values_.append(flow_val)
        link_colors_clean.append("rgba(148,163,184,0.16)")

    if not sources:
        st.info("Sankey diagram unavailable — no valid flow links could be constructed.")
        return

    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            node=dict(
                pad=20,
                thickness=20,
                line=dict(color="rgba(255,255,255,0.1)", width=0.5),
                label=nodes,
                color=node_colors,
                hovertemplate="<b>%{label}</b><extra></extra>",
            ),
            link=dict(
                source=sources,
                target=targets,
                value=values_,
                color=link_colors_clean,
                hovertemplate="Flow: %{value:.1f} units<extra></extra>",
            ),
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=420,
        margin=dict(l=40, r=20, t=20, b=20),
        font=dict(color=_C_TEXT, family="Inter, sans-serif", size=11),
    )
    st.plotly_chart(fig, use_container_width=True, key="cargo_sankey")
    st.caption(
        "Flow widths proportional to estimated trade value. "
        "Blue nodes = origin regions; green nodes = destination regions."
    )


# ---------------------------------------------------------------------------
# Section 10 — Route Cargo Mix Selector
# ---------------------------------------------------------------------------

def _render_route_cargo_mix(trade_data: dict, route_results: list) -> None:
    _divider("ROUTE CARGO MIX — SELECT A LANE TO INSPECT")

    route_display = [_ROUTE_LABELS.get(r, r) for r in _ALL_ROUTES]

    default_idx = 0
    if route_results:
        try:
            first_route = route_results[0]
            route_id = getattr(first_route, "route_id", None) or getattr(first_route, "id", None)
            if route_id and route_id in _ALL_ROUTES:
                default_idx = _ALL_ROUTES.index(route_id)
        except (IndexError, AttributeError):
            pass

    selected_label = st.selectbox(
        "Select a shipping lane",
        options=route_display,
        index=default_idx,
        key="cargo_route_selectbox",
    )
    selected_route = _ALL_ROUTES[route_display.index(selected_label)]

    mix = get_route_cargo_mix(selected_route, trade_data)

    if not mix:
        st.info(f"No cargo mix data available for route: {selected_label}.")
        return

    known_items = {k: v for k, v in mix.items() if k != "other"}
    other_share = mix.get("other", 0.0)
    total_known = sum(known_items.values())

    if total_known == 0 and other_share == 0:
        st.warning(f"All cargo shares are zero for route: {selected_label}.")
        return

    display_items = sorted(known_items.items(), key=lambda x: x[1], reverse=True)
    if other_share > 0.001:
        display_items.append(("other", other_share))

    labels  = [HS_CATEGORIES.get(k, {}).get("label", k.title()) if k != "other" else "Other" for k, _ in display_items]
    values  = [v * 100 for _, v in display_items]
    colors  = [_CAT_COLORS.get(k, "#64748b") for k, _ in display_items]
    icons   = [_ICONS.get(k, "📦") for k, _ in display_items]
    display = [f"{icons[i]} {labels[i]}" for i in range(len(labels))]

    col_chart, col_table = st.columns([1, 1])

    with col_chart:
        fig = go.Figure(
            go.Pie(
                labels=display,
                values=values,
                hole=0.58,
                marker=dict(colors=colors, line=dict(color=_C_BG, width=3)),
                textinfo="label+percent",
                textfont=dict(size=10, color=_C_TEXT),
                hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
                pull=[0.05 if i == 0 else 0 for i in range(len(values))],
            )
        )
        fig.update_layout(
            paper_bgcolor=_C_BG,
            plot_bgcolor=_C_BG,
            height=340,
            margin=dict(l=0, r=0, t=20, b=0),
            legend=dict(
                font=dict(color=_C_TEXT2, size=9.5),
                bgcolor="rgba(0,0,0,0)",
                orientation="h",
                y=-0.08,
                xanchor="center",
                x=0.5,
            ),
            annotations=[
                dict(
                    text=f"<b>{selected_label.split()[0]}</b><br><span style='font-size:9px'>Cargo Mix</span>",
                    x=0.5, y=0.5,
                    font=dict(size=12, color=_C_TEXT),
                    showarrow=False,
                    align="center",
                )
            ],
        )
        st.plotly_chart(fig, use_container_width=True, key="cargo_route_mix_donut")

    with col_table:
        st.markdown(
            f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
            f'border-radius:12px;padding:16px 18px">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.68rem;font-weight:700;color:{_C_TEXT2};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">'
            f'Cargo Characteristics</div>',
            unsafe_allow_html=True,
        )

        for cat_key, share in display_items:
            if cat_key == "other":
                icon = "📦"; label = "Other"; color = _CAT_COLORS.get("other", "#64748b")
                shipping = "mixed"; sensitivity = "—"
            else:
                chars = CARGO_CHARACTERISTICS.get(cat_key, {})
                shipping    = chars.get("shipping", "standard container")
                sensitivity = chars.get("sensitivity", "—")
                icon  = _ICONS.get(cat_key, "📦")
                label = HS_CATEGORIES.get(cat_key, {}).get("label", cat_key.title())
                color = _CAT_COLORS.get(cat_key, "#64748b")

            pct_str = f"{share * 100:.1f}%"
            bar     = _progress_bar(share, color, 3)

            st.markdown(
                f'<div style="margin-bottom:10px">'
                f'<div style="display:flex;align-items:center;justify-content:space-between">'
                f'<span style="font-size:0.82rem;font-weight:700;color:{color}">{icon} {label}</span>'
                f'<span style="font-size:0.82rem;font-weight:800;color:{_C_TEXT}">{pct_str}</span>'
                f'</div>'
                f'<div style="font-size:0.66rem;color:{_C_TEXT3};margin-top:1px">'
                f'{shipping} · sensitivity: {sensitivity}</div>'
                f'{bar}'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    # CSV export
    try:
        import pandas as pd
        csv_rows = [
            {
                "category_key":   k,
                "category_label": HS_CATEGORIES.get(k, {}).get("label", k.title()) if k != "other" else "Other",
                "share_pct":      round(v * 100, 2),
            }
            for k, v in display_items
        ]
        if csv_rows:
            df_export = pd.DataFrame(csv_rows)
            st.download_button(
                label="Download cargo mix CSV",
                data=df_export.to_csv(index=False).encode("utf-8"),
                file_name=f"cargo_mix_{selected_route}.csv",
                mime="text/csv",
                key="cargo_mix_download_btn",
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Section 11 — Cargo Value Trend
# ---------------------------------------------------------------------------

def _render_value_trend(trade_data: dict, flows: list[CargoFlowAnalysis]) -> None:
    _divider("CARGO VALUE TREND — BY CATEGORY")

    # Detect time-series
    has_time_series = False
    time_col = None
    if trade_data:
        for locode, df in trade_data.items():
            if df is not None and hasattr(df, "columns") and not df.empty:
                for candidate in ("period", "date", "year"):
                    if candidate in df.columns:
                        has_time_series = True
                        time_col = candidate
                        break
            if has_time_series:
                break

    if has_time_series and time_col:
        import pandas as pd
        cat_series: dict[str, dict] = {}
        for locode, df in trade_data.items():
            if df is None or df.empty or "hs_category" not in df.columns:
                continue
            if time_col not in df.columns or "value_usd" not in df.columns:
                continue
            for cat_key in HS_CATEGORIES:
                cat_df = df[df["hs_category"] == cat_key]
                if cat_df.empty:
                    continue
                grouped = cat_df.groupby(time_col)["value_usd"].sum()
                for period, val in grouped.items():
                    cat_series.setdefault(cat_key, {})[period] = (
                        cat_series.get(cat_key, {}).get(period, 0) + val
                    )
        if cat_series:
            fig = go.Figure()
            for cat_key, ts in cat_series.items():
                if not ts:
                    continue
                periods = sorted(ts.keys())
                values  = [ts[p] / 1e9 for p in periods]
                fig.add_trace(
                    go.Scatter(
                        x=periods,
                        y=values,
                        mode="lines+markers",
                        name=f"{_ICONS.get(cat_key,'')} {HS_CATEGORIES[cat_key]['label']}",
                        line=dict(color=_CAT_COLORS.get(cat_key, "#64748b"), width=2.5),
                        marker=dict(size=6),
                        hovertemplate="<b>%{fullData.name}</b><br>%{x}: $%{y:.2f}B<extra></extra>",
                    )
                )
            fig = _apply_dark_chart(fig, height=380)
            fig.update_layout(
                xaxis=dict(title="Period", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(title="Trade Value (USD B)", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
            )
            st.plotly_chart(fig, use_container_width=True, key="cargo_value_trend_line")
            return

    # Fallback bar chart from flows
    if not flows:
        st.info("No cargo value data available.")
        return

    flow_map = {f.hs_category: f for f in flows}
    rows = []
    for cat_key in HS_CATEGORIES:
        f = flow_map.get(cat_key)
        if not f:
            continue
        rows.append({
            "label":  f"{_ICONS.get(cat_key, '')} {f.category_label}",
            "value":  f.total_value_usd / 1e9,
            "color":  _CAT_COLORS.get(cat_key, "#64748b"),
            "yoy":    f.yoy_growth_pct,
            "signal": f.demand_signal,
        })

    if not rows:
        st.info("No cargo value data available.")
        return

    rows_sorted = sorted(rows, key=lambda r: r["value"], reverse=True)

    fig = go.Figure(
        go.Bar(
            x=[r["label"] for r in rows_sorted],
            y=[r["value"] for r in rows_sorted],
            marker=dict(color=[r["color"] for r in rows_sorted], opacity=0.83),
            text=[f"${r['value']:.1f}B" for r in rows_sorted],
            textposition="outside",
            textfont=dict(color=_C_TEXT2, size=10),
            hovertemplate=(
                "<b>%{x}</b><br>Value: $%{y:.2f}B<extra></extra>"
            ),
        )
    )
    fig = _apply_dark_chart(fig, height=380)
    fig.update_layout(
        xaxis=dict(color=_C_TEXT2, gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(title="Estimated Value (USD B)", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key="cargo_value_trend_bar")
    st.caption(
        "Values are benchmark estimates when live Comtrade data is unavailable. "
        "Bar colour indicates commodity category."
    )

    try:
        import pandas as pd
        df_export = pd.DataFrame([
            {"category": r["label"], "estimated_value_usd_billion": round(r["value"], 3), "yoy_growth_pct": r["yoy"]}
            for r in rows_sorted
        ])
        st.download_button(
            label="Download value data CSV",
            data=df_export.to_csv(index=False).encode("utf-8"),
            file_name="cargo_value_trend.csv",
            mime="text/csv",
            key="cargo_value_download_btn",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NEW SECTION A — Cargo Mix Overview Hero (enhanced donut + commodity table)
# ---------------------------------------------------------------------------

_COMMODITY_OVERVIEW: list[dict] = [
    {"name": "Electronics",  "icon": "🖥️",  "color": "#3b82f6", "share_pct": 26.2, "volume_m_teu": 20.9, "value_b": 1_650, "yoy": +8.1},
    {"name": "Machinery",    "icon": "⚙️",  "color": "#f59e0b", "share_pct": 18.4, "volume_m_teu": 14.6, "value_b": 980,   "yoy": +4.1},
    {"name": "Automotive",   "icon": "🚗",  "color": "#8b5cf6", "share_pct": 14.8, "volume_m_teu": 11.7, "value_b": 820,   "yoy": +2.7},
    {"name": "Apparel",      "icon": "👕",  "color": "#ec4899", "share_pct": 10.5, "volume_m_teu": 8.3,  "value_b": 420,   "yoy": +1.3},
    {"name": "Chemicals",    "icon": "🧪",  "color": "#14b8a6", "share_pct": 9.8,  "volume_m_teu": 7.8,  "value_b": 390,   "yoy": +3.8},
    {"name": "Agriculture",  "icon": "🌾",  "color": "#84cc16", "share_pct": 8.6,  "volume_m_teu": 6.8,  "value_b": 275,   "yoy": +5.9},
    {"name": "Metals",       "icon": "🔩",  "color": "#94a3b8", "share_pct": 6.4,  "volume_m_teu": 5.1,  "value_b": 215,   "yoy": -1.8},
    {"name": "Other",        "icon": "📦",  "color": "#64748b", "share_pct": 5.3,  "volume_m_teu": 4.2,  "value_b": 170,   "yoy": +1.0},
]

_COMMODITY_SEASONALITY: dict[str, list[float]] = {
    "Electronics":  [78, 72, 80, 88, 95, 100, 108, 115, 118, 112, 105, 98],
    "Machinery":    [90, 85, 92, 98, 102, 100, 96, 94, 100, 104, 98, 88],
    "Automotive":   [88, 82, 95, 105, 110, 100, 92, 90, 102, 108, 98, 85],
    "Apparel":      [70, 65, 80, 95, 110, 115, 120, 130, 125, 105, 80, 72],
    "Chemicals":    [95, 92, 98, 102, 105, 108, 110, 108, 100, 95, 90, 88],
    "Agriculture":  [112, 118, 115, 102, 88, 80, 78, 82, 90, 100, 108, 115],
    "Metals":       [92, 88, 95, 100, 105, 102, 98, 95, 98, 102, 100, 95],
}

_REGION_DIVERGE: list[dict] = [
    {"region": "East Asia",       "surplus_b": 560,  "color": "#3b82f6"},
    {"region": "Southeast Asia",  "surplus_b": 110,  "color": "#3b82f6"},
    {"region": "Middle East",     "surplus_b": 210,  "color": "#f59e0b"},
    {"region": "South America",   "surplus_b": -60,  "color": "#ef4444"},
    {"region": "Africa",          "surplus_b": -80,  "color": "#ef4444"},
    {"region": "South Asia",      "surplus_b": -140, "color": "#ef4444"},
    {"region": "North America W", "surplus_b": -470, "color": "#ef4444"},
    {"region": "North America E", "surplus_b": -330, "color": "#ef4444"},
    {"region": "Europe",          "surplus_b": -70,  "color": "#f59e0b"},
]

_REEFER_ROUTES: list[dict] = [
    {"route": "South America → North America", "volume_k_teu": 2_850, "primary": "Fresh Fruit / Bananas",  "color": "#84cc16"},
    {"route": "Europe → East Asia",            "volume_k_teu": 1_420, "primary": "Pharmaceuticals / Dairy","color": "#ec4899"},
    {"route": "South Africa → Europe",         "volume_k_teu": 1_180, "primary": "Citrus / Grapes",        "color": "#f59e0b"},
    {"route": "South Asia → Middle East",      "volume_k_teu": 780,   "primary": "Seafood",                "color": "#06b6d4"},
    {"route": "New Zealand → East Asia",       "volume_k_teu": 640,   "primary": "Dairy / Meat",           "color": "#3b82f6"},
    {"route": "Ecuador → Europe",              "volume_k_teu": 590,   "primary": "Bananas",                "color": "#84cc16"},
]


def _render_enhanced_cargo_mix_overview() -> None:
    """New hero section: commodity donut + top-10 table + seasonality + imbalance + reefer."""
    import calendar as _cal2

    # ── Sub-section A: Commodity mix donut + KPI row ────────────────────
    _divider("GLOBAL CARGO MIX — COMMODITY BREAKDOWN (2026 ESTIMATE)")

    # Hero KPIs
    total_vol   = sum(c["volume_m_teu"] for c in _COMMODITY_OVERVIEW)
    total_val   = sum(c["value_b"] for c in _COMMODITY_OVERVIEW)
    top_growth  = max(_COMMODITY_OVERVIEW, key=lambda c: c["yoy"])
    declining   = [c for c in _COMMODITY_OVERVIEW if c["yoy"] < 0]

    c1, c2, c3, c4 = st.columns(4)
    hero_cards = [
        (c1, "Total Containerised Volume", f"{total_vol:.1f}M TEU", "all commodity categories",             _C_ACCENT),
        (c2, "Total Trade Value",           f"${total_val:,}B",     "estimated annual USD value",           _C_HIGH),
        (c3, "Fastest Growing Commodity",   top_growth["name"],     f"+{top_growth['yoy']:.1f}% YoY",       _C_HIGH),
        (c4, "Declining Categories",        str(len(declining)),    ", ".join(c["name"] for c in declining), _C_DECLINE),
    ]
    for col, label, value, sub, color in hero_cards:
        with col:
            st.markdown(_stat_card(label, value, sub, color), unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    col_donut, col_legend = st.columns([1, 1])

    with col_donut:
        labels = [c["name"] for c in _COMMODITY_OVERVIEW]
        values = [c["share_pct"] for c in _COMMODITY_OVERVIEW]
        colors = [c["color"] for c in _COMMODITY_OVERVIEW]
        icons  = [c["icon"] for c in _COMMODITY_OVERVIEW]

        fig = go.Figure(go.Pie(
            labels=[f"{icons[i]} {labels[i]}" for i in range(len(labels))],
            values=values,
            hole=0.62,
            marker=dict(colors=colors, line=dict(color=_C_BG, width=3)),
            textinfo="label+percent",
            textfont=dict(size=10, color=_C_TEXT),
            hovertemplate="<b>%{label}</b><br>Share: %{percent}<br>Volume: see table<extra></extra>",
            pull=[0.05 if i == 0 else 0 for i in range(len(values))],
        ))
        fig.update_layout(
            paper_bgcolor=_C_BG, plot_bgcolor=_C_BG,
            height=400, margin=dict(l=0, r=0, t=30, b=0),
            legend=dict(font=dict(color=_C_TEXT2, size=9), bgcolor="rgba(0,0,0,0)",
                        orientation="h", y=-0.05, xanchor="center", x=0.5),
            annotations=[dict(
                text=f"<b>{total_vol:.0f}M</b><br><span style='font-size:9px'>Total TEU</span>",
                x=0.5, y=0.5, font=dict(size=14, color=_C_TEXT), showarrow=False, align="center",
            )],
        )
        st.plotly_chart(fig, use_container_width=True, key="new_cargo_mix_donut_hero")

    with col_legend:
        st.markdown(
            f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
            f'border-radius:12px;padding:16px 18px;height:100%">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.65rem;font-weight:700;color:{_C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">Commodity Snapshot</div>',
            unsafe_allow_html=True,
        )
        max_vol_c = max(c["volume_m_teu"] for c in _COMMODITY_OVERVIEW)
        for c in _COMMODITY_OVERVIEW:
            yoy_s = f"+{c['yoy']:.1f}%" if c["yoy"] >= 0 else f"{c['yoy']:.1f}%"
            yoy_c = _C_HIGH if c["yoy"] >= 0 else _C_DECLINE
            bar = _progress_bar(c["volume_m_teu"] / max_vol_c, c["color"], 4)
            st.markdown(
                f'<div style="margin-bottom:11px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center">'
                f'<span style="font-size:0.8rem;font-weight:600;color:{_C_TEXT}">'
                f'{c["icon"]} {c["name"]}</span>'
                f'<div style="display:flex;gap:8px">'
                f'<span style="font-size:0.72rem;color:{yoy_c};font-weight:700">{yoy_s}</span>'
                f'<span style="font-size:0.68rem;color:{_C_TEXT3}">· ${c["value_b"]:,}B</span>'
                f'</div></div>'
                f'<div style="font-size:0.68rem;color:{_C_TEXT3};margin-top:1px">'
                f'{c["volume_m_teu"]:.1f}M TEU &nbsp;·&nbsp; {c["share_pct"]:.1f}% share</div>'
                f'{bar}</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Sub-section B: Top-10 commodity table (volume + value + growth) ──
    _divider("TOP TRADED COMMODITY CATEGORIES — VOLUME · VALUE · GROWTH")

    header = (
        f'<div style="display:grid;grid-template-columns:36px 40px 1fr 130px 120px 90px;'
        f'gap:0 12px;padding:6px 16px;margin-bottom:3px;'
        f'font-size:0.6rem;font-weight:700;color:{_C_TEXT3};text-transform:uppercase;letter-spacing:0.09em">'
        f'<div>#</div><div>Icon</div><div>Commodity</div>'
        f'<div style="text-align:right">Volume (M TEU)</div>'
        f'<div style="text-align:right">Value (USD B)</div>'
        f'<div style="text-align:right">YoY</div>'
        f'</div>'
    )
    st.markdown(header, unsafe_allow_html=True)

    max_vol_t = max(c["volume_m_teu"] for c in _COMMODITY_OVERVIEW)
    max_val_t = max(c["value_b"] for c in _COMMODITY_OVERVIEW)

    for idx, c in enumerate(sorted(_COMMODITY_OVERVIEW, key=lambda x: x["volume_m_teu"], reverse=True)):
        yoy_s = f"+{c['yoy']:.1f}%" if c["yoy"] >= 0 else f"{c['yoy']:.1f}%"
        yoy_c = _C_HIGH if c["yoy"] >= 0 else _C_DECLINE
        bg    = _C_CARD if idx % 2 == 0 else _C_CARD2
        vol_pct = c["volume_m_teu"] / max_vol_t * 100
        val_pct = c["value_b"] / max_val_t * 100
        st.markdown(
            f'<div style="display:grid;grid-template-columns:36px 40px 1fr 130px 120px 90px;'
            f'gap:0 12px;padding:10px 16px;background:{bg};'
            f'border:1px solid {_C_BORDER2};border-radius:8px;margin-bottom:4px;align-items:center">'
            f'<div style="font-size:0.75rem;font-weight:800;color:{c["color"]}">{idx+1}</div>'
            f'<div style="font-size:1.1rem">{c["icon"]}</div>'
            f'<div style="font-size:0.82rem;font-weight:600;color:{_C_TEXT}">{c["name"]}</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:0.8rem;font-weight:700;color:{_C_TEXT}">{c["volume_m_teu"]:.1f}</div>'
            f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:3px;margin-top:3px">'
            f'<div style="width:{vol_pct:.0f}%;height:100%;background:{c["color"]};border-radius:2px"></div>'
            f'</div></div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:0.8rem;font-weight:700;color:{_C_TEXT}">${c["value_b"]:,}B</div>'
            f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:3px;margin-top:3px">'
            f'<div style="width:{val_pct:.0f}%;height:100%;background:{c["color"]};border-radius:2px"></div>'
            f'</div></div>'
            f'<div style="font-size:0.8rem;font-weight:700;color:{yoy_c};text-align:right">{yoy_s}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Sub-section C: Cargo seasonality chart ───────────────────────────
    _divider("CARGO SEASONALITY — MONTHLY DEMAND PATTERNS BY COMMODITY TYPE")

    months_abbr = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    current_month = __import__("datetime").date.today().month

    fig_seas = go.Figure()
    for c in _COMMODITY_OVERVIEW[:7]:  # skip "Other"
        y_vals = _COMMODITY_SEASONALITY.get(c["name"], [100] * 12)
        fig_seas.add_trace(go.Scatter(
            x=months_abbr,
            y=y_vals,
            name=f'{c["icon"]} {c["name"]}',
            mode="lines+markers",
            line=dict(color=c["color"], width=2),
            marker=dict(size=5, color=c["color"]),
            hovertemplate=f'<b>{c["name"]}</b> — %{{x}}: %{{y}} index<extra></extra>',
        ))

    # Highlight current month
    fig_seas.add_vline(
        x=months_abbr[current_month - 1],
        line=dict(color=_C_WARN, width=2, dash="dash"),
        annotation_text="NOW",
        annotation_font=dict(color=_C_WARN, size=9),
        annotation_position="top",
    )
    fig_seas.add_hline(
        y=100, line=dict(color="rgba(255,255,255,0.15)", dash="dot", width=1),
        annotation_text="Avg", annotation_font=dict(color=_C_TEXT3, size=9),
        annotation_position="right",
    )

    fig_seas = _apply_dark_chart(fig_seas, height=360)
    fig_seas.update_layout(
        xaxis=dict(color=_C_TEXT2, gridcolor="rgba(255,255,255,0.04)"),
        yaxis=dict(title="Seasonal Demand Index (100 = annual avg)",
                   color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
        legend=dict(font=dict(color=_C_TEXT2, size=9), bgcolor="rgba(0,0,0,0)",
                    orientation="h", y=-0.18, xanchor="center", x=0.5),
        margin=dict(l=60, r=40, t=30, b=50),
    )
    st.plotly_chart(fig_seas, use_container_width=True, key="new_cargo_seasonality_chart")
    st.caption("Seasonal index: 100 = annual average. Values above 100 indicate above-average demand months. "
               "Agriculture peaks in northern-hemisphere winter (southern-hemisphere harvest). "
               "Electronics peaks Q3–Q4 driven by consumer tech launches and holiday pre-positioning.")

    # ── Sub-section D: Import/Export imbalance diverging bar chart ───────
    _divider("IMPORT / EXPORT IMBALANCE — REGIONAL TRADE SURPLUS vs. DEFICIT (DIVERGING BARS)")

    regions_div  = [r["region"] for r in _REGION_DIVERGE]
    surplus_vals = [r["surplus_b"] for r in _REGION_DIVERGE]
    bar_colors_d = [_C_HIGH if s > 0 else _C_DECLINE for s in surplus_vals]
    bar_text_d   = [f"+${s}B" if s > 0 else f"-${abs(s)}B" for s in surplus_vals]

    fig_div = go.Figure(go.Bar(
        x=surplus_vals,
        y=regions_div,
        orientation="h",
        marker=dict(color=bar_colors_d, opacity=0.85,
                    line=dict(color="rgba(0,0,0,0)", width=0)),
        text=bar_text_d,
        textposition="outside",
        textfont=dict(color=_C_TEXT2, size=10),
        hovertemplate="<b>%{y}</b><br>Net trade balance: $%{x}B<extra></extra>",
    ))
    fig_div.add_vline(x=0, line=dict(color="rgba(255,255,255,0.25)", width=1.5))
    fig_div = _apply_dark_chart(fig_div, height=340)
    fig_div.update_layout(
        xaxis=dict(title="Net Trade Balance (Exports − Imports, USD B)",
                   color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(color=_C_TEXT2),
        showlegend=False,
        margin=dict(l=160, r=80, t=20, b=40),
    )
    st.plotly_chart(fig_div, use_container_width=True, key="new_imbalance_diverge_bar")

    col_info1, col_info2, col_info3 = st.columns(3)
    for col, label, val, color in [
        (col_info1, "Largest Surplus Region",  "East Asia  +$560B",       _C_HIGH),
        (col_info2, "Largest Deficit Region",  "North America W  -$470B", _C_DECLINE),
        (col_info3, "Near-Balanced",           "Europe  -$70B",           _C_STABLE),
    ]:
        with col:
            st.markdown(
                f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
                f'border-left:3px solid {color};border-radius:10px;padding:12px 14px">'
                f'<div style="font-size:0.62rem;color:{_C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.1em;margin-bottom:3px">{label}</div>'
                f'<div style="font-size:0.92rem;font-weight:700;color:{color}">{val}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Sub-section E: Reefer / cold-chain section ───────────────────────
    _divider("REEFER CARGO — COLD CHAIN VOLUMES & SPECIALISED ROUTES")

    total_reefer_vol = sum(r["volume_k_teu"] for r in _REEFER_ROUTES)

    col_reefer_kpis = st.columns(3)
    for col, label, val, sub, color in [
        (col_reefer_kpis[0], "Total Cold Chain Volume", f"{total_reefer_vol/1000:.1f}M TEU/yr",   "across all reefer corridors",           "#14b8a6"),
        (col_reefer_kpis[1], "Global Reefer Fleet",    "1.79M TEU",                               "dedicated refrigerated slots",          "#3b82f6"),
        (col_reefer_kpis[2], "Utilization Rate",       "89%",                                     "structural near-shortage globally",     "#ef4444"),
    ]:
        with col:
            st.markdown(_stat_card(label, val, sub, color), unsafe_allow_html=True)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    col_rr_chart, col_rr_detail = st.columns([3, 2])

    with col_rr_chart:
        rr_names  = [r["route"] for r in _REEFER_ROUTES]
        rr_vols   = [r["volume_k_teu"] / 1000 for r in _REEFER_ROUTES]
        rr_colors = [r["color"] for r in _REEFER_ROUTES]

        fig_rr = go.Figure(go.Bar(
            x=rr_vols,
            y=rr_names,
            orientation="h",
            marker=dict(color=rr_colors, opacity=0.85),
            text=[f"{v:.2f}M TEU" for v in rr_vols],
            textposition="outside",
            textfont=dict(color=_C_TEXT2, size=10),
            hovertemplate="<b>%{y}</b><br>Volume: %{x:.2f}M TEU/yr<extra></extra>",
        ))
        fig_rr = _apply_dark_chart(fig_rr, height=300)
        fig_rr.update_layout(
            xaxis=dict(title="Volume (M TEU/yr)", color=_C_TEXT2,
                       gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(color=_C_TEXT2),
            showlegend=False,
            margin=dict(l=240, r=80, t=20, b=40),
        )
        st.plotly_chart(fig_rr, use_container_width=True, key="new_reefer_routes_bar")

    with col_rr_detail:
        st.markdown(
            f'<div style="background:{_C_CARD};border:1px solid {_C_BORDER};'
            f'border-radius:12px;padding:14px 16px">',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:0.64rem;font-weight:700;color:{_C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">'
            f'Primary Cold Chain Cargo by Route</div>',
            unsafe_allow_html=True,
        )
        for r in _REEFER_ROUTES:
            st.markdown(
                f'<div style="margin-bottom:9px;padding:8px 10px;'
                f'background:{_hex_to_rgba(r["color"],0.07)};'
                f'border:1px solid {_hex_to_rgba(r["color"],0.22)};border-radius:8px">'
                f'<div style="font-size:0.73rem;font-weight:700;color:{r["color"]};margin-bottom:2px">'
                f'{r["route"]}</div>'
                f'<div style="font-size:0.68rem;color:{_C_TEXT2}">{r["primary"]}</div>'
                f'<div style="font-size:0.65rem;color:{_C_TEXT3};margin-top:2px">'
                f'{r["volume_k_teu"]:,}K TEU/yr</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    st.caption(
        "Cold-chain volumes growing +6.8% YoY driven by pharmaceutical expansion and rising "
        "demand for fresh produce across all regions. Reefer slot availability is critically tight "
        "on South America northbound lanes during Q1 banana season."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(trade_data: dict, wb_data: dict, route_results: list) -> None:
    """Render the Cargo Analysis tab.

    Parameters
    ----------
    trade_data:
        Mapping of port_locode -> DataFrame (may be empty).
    wb_data:
        World Bank supplemental data (may be None or empty).
    route_results:
        List of RouteOpportunity objects from the route optimizer.
    """
    try:
        flows = analyze_cargo_flows(trade_data, wb_data)
    except Exception:
        flows = []

    try:
        _render_enhanced_cargo_mix_overview()
    except Exception:
        pass

    try:
        _render_hero(flows)
    except Exception:
        logger.exception("tab_cargo: error in hero")
        st.error("Error rendering Cargo Overview header.", icon="⚠️")

    try:
        _render_cargo_mix_overview(flows)
    except Exception:
        logger.exception("tab_cargo: error in cargo mix overview")
        st.error("Error rendering Cargo Mix Overview section.", icon="⚠️")

    try:
        _render_top_hs_codes()
    except Exception:
        logger.exception("tab_cargo: error in top hs codes")
        st.error("Error rendering Top HS Codes section.", icon="⚠️")

    try:
        _render_seasonality()
    except Exception:
        logger.exception("tab_cargo: error in seasonality")
        st.error("Error rendering Seasonality section.", icon="⚠️")

    try:
        _render_high_value_routes()
    except Exception:
        logger.exception("tab_cargo: error in high value routes")
        st.error("Error rendering High-Value Routes section.", icon="⚠️")

    try:
        _render_dangerous_goods()
    except Exception:
        logger.exception("tab_cargo: error in dangerous goods")
        st.error("Error rendering Dangerous Goods section.", icon="⚠️")

    try:
        _render_reefer_cargo()
    except Exception:
        logger.exception("tab_cargo: error in reefer cargo")
        st.error("Error rendering Reefer Cargo section.", icon="⚠️")

    try:
        _render_loss_rates()
    except Exception:
        logger.exception("tab_cargo: error in loss rates")
        st.error("Error rendering Cargo Loss Rates section.", icon="⚠️")

    try:
        _render_imbalance()
    except Exception:
        logger.exception("tab_cargo: error in imbalance")
        st.error("Error rendering Trade Imbalance section.", icon="⚠️")

    try:
        _render_sankey(flows)
    except Exception:
        logger.exception("tab_cargo: error in sankey")
        st.error("Error rendering Trade Flow Sankey section.", icon="⚠️")

    try:
        _render_route_cargo_mix(trade_data, route_results)
    except Exception:
        logger.exception("tab_cargo: error in route cargo mix")
        st.error("Error rendering Route Cargo Mix section.", icon="⚠️")

    try:
        _render_value_trend(trade_data, flows)
    except Exception:
        logger.exception("tab_cargo: error in value trend")
        st.error("Error rendering Cargo Value Trend section.", icon="⚠️")
