"""tab_ecommerce.py — E-Commerce Impact dashboard for container shipping intelligence.

Sections:
  1. Overview Header          — live demand index, e-commerce share of containerized trade
  2. E-Commerce Demand Pulse  — 4-platform card grid with signals
  3. Peak Season Calendar     — 12-month heat strip + Q4/CNY/BFCM callouts
  4. Booking Window Alerts    — urgency callout cards
  5. DTC Shipping Growth      — Asia-US direct parcel flow chart
  6. E-Commerce vs Traditional — rate, route, vessel comparison
  7. Major Platform Volumes   — Alibaba/Temu/Shein route impact breakdown
  8. Air vs Ocean Split        — when e-commerce goes by air vs ocean + de minimis
  9. Last-Mile Port Congestion — parcel delivery creating inland congestion analysis
  10. Return Flows Analysis    — reverse logistics pattern visualization
  11. 90-Day Forecast          — trans-Pacific demand line chart
"""
from __future__ import annotations

import io
import csv as _csv_mod
import math
from datetime import date, timedelta

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.ecommerce_tracker import (
    ECOMMERCE_SIGNALS,
    RETAIL_CALENDAR,
    compute_ecommerce_demand_index,
    get_seasonal_booking_windows,
)

# ── Color palette ──────────────────────────────────────────────────────────────
C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_CARD2  = "#111827"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"
C_ORANGE = "#f97316"
C_PINK   = "#ec4899"
C_TEAL   = "#14b8a6"

# Platform brand colors
_PLATFORM_COLORS: dict[str, str] = {
    "AMAZON":  "#ff9900",
    "ALIBABA": "#ff6900",
    "SHEIN":   "#e91e8c",
    "TEMU":    "#e53935",
    "SHOPIFY": "#96bf48",
    "WAYFAIR": "#7f187f",
}

_PLATFORM_ICONS: dict[str, str] = {
    "AMAZON":  "A",
    "ALIBABA": "阿",
    "SHEIN":   "S",
    "TEMU":    "T",
    "SHOPIFY": "SH",
    "WAYFAIR": "W",
}

_PLATFORM_LABELS: dict[str, str] = {
    "AMAZON":  "Amazon",
    "ALIBABA": "Alibaba",
    "SHEIN":   "SHEIN",
    "TEMU":    "Temu",
    "SHOPIFY": "Shopify",
    "WAYFAIR": "Wayfair",
}

# Air vs ocean freight splits by platform (percent air)
_PLATFORM_AIR_PCT: dict[str, float] = {
    "AMAZON":  20.0,
    "ALIBABA":  5.0,
    "SHEIN":   80.0,
    "TEMU":    85.0,
    "SHOPIFY": 15.0,
    "WAYFAIR":  8.0,
}

# Estimated annual cross-border volumes (million packages/year)
_PLATFORM_VOLUMES: dict[str, float] = {
    "AMAZON":  450.0,
    "ALIBABA": 350.0,
    "SHEIN":   220.0,
    "TEMU":    150.0,
    "SHOPIFY":  80.0,
    "WAYFAIR":  18.0,
}

# Routes primarily impacted per platform
_PLATFORM_ROUTES: dict[str, list[str]] = {
    "AMAZON":  ["transpacific_eb", "us_mexico"],
    "ALIBABA": ["transpacific_eb", "asia_europe", "asia_latam"],
    "SHEIN":   ["transpacific_eb", "asia_europe"],
    "TEMU":    ["transpacific_eb", "intra_asia_sea"],
    "SHOPIFY": ["transpacific_eb", "us_mexico"],
    "WAYFAIR": ["transpacific_eb", "asia_europe"],
}

# Monthly demand index (trans-Pacific EB)
_TP_MONTHLY: dict[int, float] = {
    1: 0.85, 2: 0.70, 3: 0.80, 4: 0.90,
    5: 1.10, 6: 1.25, 7: 1.40, 8: 1.45,
    9: 1.35, 10: 1.20, 11: 1.00, 12: 0.90,
}

_MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

_MONTH_FULL = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_URGENCY_COLOR: dict[str, str] = {
    "CRITICAL": C_DANGER,
    "HIGH":     C_WARN,
    "MODERATE": C_ACCENT,
    "MONITOR":  C_HIGH,
}

_URGENCY_ICON: dict[str, str] = {
    "CRITICAL": "⚡",
    "HIGH":     "🔴",
    "MODERATE": "📅",
    "MONITOR":  "✅",
}

# Key e-commerce peak events with context
_PEAK_EVENTS = [
    {
        "name": "Chinese New Year",
        "months": [1, 2],
        "color": C_DANGER,
        "icon": "CNY",
        "impact": "Factory shutdowns 2-4 weeks; pre-CNY surge in Oct-Dec; Asia-US rates spike 20-40%",
        "pre_months": [-2, -1],
        "severity": "CRITICAL",
    },
    {
        "name": "Black Friday / Cyber Monday",
        "months": [11],
        "color": C_ORANGE,
        "icon": "BFCM",
        "impact": "Largest US retail event; ocean containers must be booked July-Aug for Nov arrival",
        "pre_months": [-3, -4],
        "severity": "CRITICAL",
    },
    {
        "name": "Singles Day (11.11)",
        "months": [11],
        "color": C_PINK,
        "icon": "1111",
        "impact": "Alibaba's annual sale — largest single shopping day globally; ~$140B GMV; drives massive Asia-Europe + Asia-US flows",
        "pre_months": [-3],
        "severity": "HIGH",
    },
    {
        "name": "Prime Day",
        "months": [7],
        "color": _PLATFORM_COLORS["AMAZON"],
        "icon": "PD",
        "impact": "Amazon FBA replenishment surge May-June; spot bookings up 40%; rates +15-25%",
        "pre_months": [-2, -1],
        "severity": "HIGH",
    },
    {
        "name": "Q4 Holiday Peak",
        "months": [8, 9, 10],
        "color": C_WARN,
        "icon": "Q4",
        "impact": "Peak ocean container season Aug-Oct as retailers stock for holiday; carriers impose peak season surcharges",
        "pre_months": [],
        "severity": "CRITICAL",
    },
    {
        "name": "Back to School",
        "months": [7, 8],
        "color": C_TEAL,
        "icon": "BTS",
        "impact": "Second-largest US retail seasonal event; electronics + apparel demand peak",
        "pre_months": [-2],
        "severity": "MODERATE",
    },
]

# Last-mile congestion data by port/gateway
_PORT_CONGESTION = [
    {"port": "Los Angeles / Long Beach", "parcel_pct": 42, "dwell_days": 3.8, "congestion_idx": 1.45, "route": "Trans-Pac EB"},
    {"port": "New York / New Jersey",    "parcel_pct": 28, "dwell_days": 2.9, "congestion_idx": 1.28, "route": "Trans-Atlantic"},
    {"port": "Seattle / Tacoma",          "parcel_pct": 18, "dwell_days": 2.1, "congestion_idx": 1.12, "route": "Trans-Pac EB"},
    {"port": "Savannah",                  "parcel_pct": 22, "dwell_days": 2.4, "congestion_idx": 1.18, "route": "Trans-Atlantic"},
    {"port": "Rotterdam",                 "parcel_pct": 35, "dwell_days": 1.8, "congestion_idx": 1.22, "route": "Asia-Europe"},
    {"port": "Felixstowe",                "parcel_pct": 31, "dwell_days": 2.2, "congestion_idx": 1.19, "route": "Asia-Europe"},
]

# Return logistics data
_RETURN_FLOWS = [
    {"category": "Apparel & Fashion",    "return_rate": 38, "reverse_teu_est": 180_000, "mode": "Ocean", "route": "US → Asia"},
    {"category": "Consumer Electronics", "return_rate": 22, "reverse_teu_est":  95_000, "mode": "Mixed",  "route": "US → Asia / Mexico"},
    {"category": "Home Furnishings",     "return_rate": 18, "reverse_teu_est":  62_000, "mode": "Ocean",  "route": "US → Asia"},
    {"category": "Beauty & Health",      "return_rate": 12, "reverse_teu_est":  28_000, "mode": "Air",    "route": "US → Asia"},
    {"category": "Shoes",                "return_rate": 35, "reverse_teu_est": 115_000, "mode": "Ocean",  "route": "US → Asia"},
    {"category": "Toys & Games",         "return_rate": 15, "reverse_teu_est":  44_000, "mode": "Ocean",  "route": "US → Asia"},
]

# DTC parcel flow data (Asia-US, million units/year, by quarter)
_DTC_QUARTERLY = {
    "labels": ["Q1 2023", "Q2 2023", "Q3 2023", "Q4 2023",
               "Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024",
               "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"],
    "air":   [210, 235, 285, 380, 260, 295, 355, 490, 310, 345, 415, 580],
    "ocean": [ 85,  92, 105, 135,  98, 110, 128, 162, 115, 135, 158, 200],
}


# ── HTML / component helpers ───────────────────────────────────────────────────

def _section_header(title: str, subtitle: str = "", icon: str = "") -> None:
    icon_html = (
        '<span style="display:inline-flex; align-items:center; justify-content:center;'
        ' width:32px; height:32px; border-radius:8px;'
        ' background:rgba(59,130,246,0.15); border:1px solid rgba(59,130,246,0.3);'
        ' font-size:0.85rem; margin-right:10px; flex-shrink:0">'
        + icon + "</span>"
        if icon else ""
    )
    sub_html = (
        '<div style="color:' + C_TEXT2 + '; font-size:0.82rem; margin-top:4px; margin-left:'
        + ("42px" if icon else "0") + '">'
        + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        '<div style="margin-bottom:16px; margin-top:12px; padding-bottom:10px;'
        ' border-bottom:1px solid ' + C_BORDER + '">'
        '<div style="display:flex; align-items:center">'
        + icon_html
        + '<span style="font-size:1.05rem; font-weight:700; color:' + C_TEXT + '">'
        + title + "</span>"
        + "</div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _divider(color: str = C_BORDER) -> None:
    st.markdown(
        '<hr style="border:none; border-top:1px solid ' + color + '; margin:24px 0">',
        unsafe_allow_html=True,
    )


def _card(content_html: str, border_color: str = C_BORDER, padding: str = "16px 18px") -> str:
    return (
        '<div style="background:' + C_CARD + '; border:1px solid ' + border_color + ';'
        ' border-radius:12px; padding:' + padding + '; margin-bottom:10px; height:100%">'
        + content_html + "</div>"
    )


def _stat_card(label: str, value: str, sub: str = "", color: str = C_ACCENT,
               border_color: str = C_BORDER) -> str:
    return _card(
        '<div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.09em;'
        ' color:' + C_TEXT3 + '; margin-bottom:8px">' + label + "</div>"
        '<div style="font-size:1.85rem; font-weight:900; color:' + color + '; line-height:1; margin-bottom:4px">'
        + value + "</div>"
        + ('<div style="font-size:0.74rem; color:' + C_TEXT2 + '">' + sub + "</div>" if sub else ""),
        border_color=border_color,
        padding="16px 18px",
    )


def _badge(text: str, color: str) -> str:
    return (
        '<span style="background:' + color + '22; color:' + color + '; border:1px solid '
        + color + '55; border-radius:6px; padding:2px 9px; font-size:0.70rem;'
        ' font-weight:700; letter-spacing:0.04em; white-space:nowrap">' + text + "</span>"
    )


def _platform_logo(platform: str, size: int = 44) -> str:
    color = _PLATFORM_COLORS.get(platform, C_ACCENT)
    icon  = _PLATFORM_ICONS.get(platform, platform[:1])
    sz    = str(size)
    fsz   = str(max(10, int(size * 0.36)))
    return (
        '<div style="width:' + sz + 'px; height:' + sz + 'px; border-radius:10px;'
        ' background:' + color + '1a; border:2px solid ' + color + '55;'
        ' display:inline-flex; align-items:center; justify-content:center;'
        ' font-size:' + fsz + 'px; font-weight:900; color:' + color + ';'
        ' flex-shrink:0">' + icon + "</div>"
    )


def _route_pill(route: str) -> str:
    labels = {
        "transpacific_eb":    "Trans-Pac EB",
        "transpacific_wb":    "Trans-Pac WB",
        "asia_europe":        "Asia-Europe",
        "intra_asia_sea":     "Intra-Asia",
        "us_mexico":          "US-Mexico",
        "gulf_coast_inbound": "Gulf Coast",
        "asia_latam":         "Asia-LATAM",
    }
    label = labels.get(route, route)
    return (
        '<span style="background:rgba(59,130,246,0.10); color:' + C_ACCENT + ';'
        ' border:1px solid rgba(59,130,246,0.28); border-radius:5px;'
        ' padding:1px 7px; font-size:0.67rem; margin-right:4px; white-space:nowrap">'
        + label + "</span>"
    )


def _demand_color(idx: float) -> str:
    if idx >= 1.40:
        return C_DANGER
    if idx >= 1.25:
        return C_ORANGE
    if idx >= 1.10:
        return C_WARN
    if idx >= 0.95:
        return "#84cc16"
    return C_HIGH


def _demand_label(idx: float) -> str:
    if idx >= 1.40:
        return "PEAK"
    if idx >= 1.25:
        return "HIGH"
    if idx >= 1.10:
        return "MODERATE"
    if idx >= 0.95:
        return "NORMAL"
    return "LOW"


# ── NEW: Enhanced E-Commerce Overview (hero cards + calendar + comparison + DTC + air/ocean) ──

# Monthly e-commerce demand heatmap data (index: 100 = annual avg)
_ECOM_MONTHLY_HEATMAP: dict[str, list[float]] = {
    "Trans-Pac EB": [85, 70, 80, 90, 110, 125, 140, 145, 135, 120, 100, 90],
    "Asia-Europe":  [88, 72, 82, 92, 108, 118, 128, 132, 125, 115, 108, 95],
    "Air (total)":  [95, 80, 85, 96, 114, 122, 142, 150, 138, 128, 118, 102],
    "DTC Parcels":  [78, 65, 75, 85, 108, 120, 145, 155, 140, 125, 110, 92],
}

# Monthly e-com vs traditional rate comparison
_ECOM_TRAD_RATES_MONTHLY: dict[str, list[int]] = {
    "months":       [1,  2,  3,  4,  5,  6,  7,  8,  9,  10, 11, 12],
    "ecom_spot":    [2100, 1800, 2000, 2200, 2600, 3100, 3800, 4200, 3600, 3200, 2800, 2400],
    "traditional":  [2400, 2300, 2300, 2400, 2400, 2500, 2600, 2700, 2700, 2800, 2700, 2500],
    "ecom_volume_idx": [78, 65, 75, 85, 108, 120, 145, 155, 140, 125, 110, 92],
    "trad_volume_idx": [92, 88, 92, 96, 100, 100, 98,  96,  98,  102, 104, 100],
}

# Direct-to-consumer Asia→US annual parcel volumes (millions)
_DTC_ANNUAL: list[dict] = [
    {"year": 2020, "air_m": 480,  "ocean_m": 180},
    {"year": 2021, "air_m": 680,  "ocean_m": 245},
    {"year": 2022, "air_m": 820,  "ocean_m": 295},
    {"year": 2023, "air_m": 1110, "ocean_m": 415},
    {"year": 2024, "air_m": 1400, "ocean_m": 535},
    {"year": 2025, "air_m": 1650, "ocean_m": 708},
]

# Air vs ocean split by product category for e-commerce
_ECOM_AIR_OCEAN_CATEGORIES: list[dict] = [
    {"category": "Fast Fashion",       "air_pct": 88, "color": "#ec4899"},
    {"category": "Electronics",        "air_pct": 52, "color": "#3b82f6"},
    {"category": "Beauty / Health",    "air_pct": 72, "color": "#f59e0b"},
    {"category": "Home Goods",         "air_pct": 14, "color": "#14b8a6"},
    {"category": "Furniture",          "air_pct": 5,  "color": "#8b5cf6"},
    {"category": "Toys / Games",       "air_pct": 38, "color": "#f97316"},
    {"category": "Footwear",           "air_pct": 45, "color": "#84cc16"},
    {"category": "Sports / Outdoor",   "air_pct": 28, "color": "#06b6d4"},
]

_MONTH_ABBR_LIST = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _render_enhanced_ecommerce_overview() -> None:
    """New enhanced sections rendered before the existing hero."""

    # ── A: E-commerce impact hero metric cards ────────────────────────────
    _section_header(
        "E-Commerce Global Impact Overview",
        "Key metrics: market share, growth rate, modal shift, and value at stake",
        icon="🌐",
    )

    today = __import__("datetime").date.today()

    ecom_share_pct   = 22.4
    yoy_growth_pct   = 14.2
    dtc_parcels_b    = 1.65
    de_minimis_risk_b = 38.0  # estimated USD B at risk from policy reform

    hero_cols = st.columns(4)
    for col, label, value, sub, color in [
        (hero_cols[0], "E-COMMERCE SHARE", f"{ecom_share_pct}%",  "of global containerized trade",          C_CYAN),
        (hero_cols[1], "YOY GROWTH",        f"+{yoy_growth_pct}%", "cross-border parcel volume 2025",        C_HIGH),
        (hero_cols[2], "DTC PARCELS / YR",  f"{dtc_parcels_b}B",   "Asia → US direct-to-consumer 2025",     C_ACCENT),
        (hero_cols[3], "DE MINIMIS RISK",   f"${de_minimis_risk_b:.0f}B",
                                             "trade value exposed to policy reform",  C_WARN),
    ]:
        with col:
            st.markdown(_stat_card(label, value, sub, color=color, border_color=color + "44"),
                        unsafe_allow_html=True)

    # Context banner
    st.markdown(
        '<div style="background:linear-gradient(135deg,rgba(6,182,212,0.06) 0%,'
        'rgba(139,92,246,0.06) 100%);border:1px solid rgba(6,182,212,0.20);'
        'border-radius:12px;padding:14px 20px;margin-top:6px">'
        '<span style="font-size:0.80rem;color:' + C_TEXT2 + '">'
        "E-commerce now accounts for <b style='color:" + C_TEXT + "'>22.4%</b> of all containerized "
        "trade — up from 12% in 2020. The structural shift is permanent: platforms like SHEIN, Temu, "
        "and Alibaba have rewritten the demand calendar, creating discrete event-driven peaks that "
        "require container procurement <b style='color:" + C_WARN + "'>8–16 weeks ahead</b>. "
        "De minimis policy reform is the single largest wildcard for ocean rate dynamics in 2026."
        "</span></div>",
        unsafe_allow_html=True,
    )

    _divider()

    # ── B: Peak season demand heatmap calendar ────────────────────────────
    _section_header(
        "Peak Season Demand Calendar — Monthly Heatmap",
        "Monthly demand index across key e-commerce corridors. "
        "Q4 holiday, CNY, Black Friday/Cyber Monday, and Prime Day create sharp spikes.",
        icon="🗓️",
    )

    # Build heatmap: rows = corridors, cols = months
    heatmap_rows   = list(_ECOM_MONTHLY_HEATMAP.keys())
    heatmap_matrix = [_ECOM_MONTHLY_HEATMAP[k] for k in heatmap_rows]

    # Annotation highlights for peak events
    peak_events_ann = [
        {"month_idx": 1,  "label": "CNY",  "color": C_DANGER},   # Feb (0-indexed)
        {"month_idx": 6,  "label": "Prime Day", "color": "#ff9900"},  # Jul
        {"month_idx": 7,  "label": "Q4 Prep",   "color": C_WARN},     # Aug
        {"month_idx": 10, "label": "BFCM",       "color": C_ORANGE},   # Nov
    ]

    fig_heat = go.Figure(go.Heatmap(
        z=heatmap_matrix,
        x=_MONTH_ABBR_LIST,
        y=heatmap_rows,
        colorscale=[
            [0.0,  "#1e293b"],
            [0.3,  "#1e3a5f"],
            [0.55, "#1d4ed8"],
            [0.80, "#10b981"],
            [1.0,  "#f59e0b"],
        ],
        showscale=True,
        colorbar=dict(
            title="Demand Index", titleside="right",
            tickfont=dict(color=C_TEXT2, size=9),
            titlefont=dict(color=C_TEXT2, size=9),
            len=0.8, thickness=12,
            bgcolor="rgba(0,0,0,0)", bordercolor="rgba(255,255,255,0.08)",
        ),
        hovertemplate="<b>%{y}</b><br>%{x}: index %{z}<extra></extra>",
        xgap=3, ygap=3,
    ))

    # Mark current month
    cur_m = today.month
    fig_heat.add_shape(
        type="rect",
        x0=cur_m - 1.5, x1=cur_m - 0.5,
        y0=-0.5, y1=len(heatmap_rows) - 0.5,
        line=dict(color=C_WARN, width=2),
        fillcolor="rgba(0,0,0,0)", layer="above",
    )

    # Annotate key events
    for ann in peak_events_ann:
        m_label = _MONTH_ABBR_LIST[ann["month_idx"]]
        fig_heat.add_annotation(
            x=m_label, y=-0.7,
            text=ann["label"],
            showarrow=False,
            font=dict(color=ann["color"], size=9, family="Inter, sans-serif"),
            bgcolor=ann["color"] + "22",
            bordercolor=ann["color"] + "55",
            borderwidth=1, borderpad=2,
            yanchor="top",
        )

    fig_heat.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG, plot_bgcolor=C_BG,
        height=260,
        margin=dict(l=120, r=60, t=30, b=60),
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
        xaxis=dict(side="top", gridcolor="rgba(0,0,0,0)", color=C_TEXT2),
        yaxis=dict(gridcolor="rgba(0,0,0,0)", color=C_TEXT2),
    )
    st.plotly_chart(fig_heat, use_container_width=True, key="new_ecom_demand_heatmap_cal")
    st.caption(
        "Demand index: 100 = annual average. Gold = peak season (>130). "
        "Current month highlighted amber. Book containers for Q4 by July–August."
    )

    _divider()

    # ── C: E-commerce vs traditional — side-by-side bar comparison ───────
    _section_header(
        "E-Commerce vs. Traditional: Rates & Volumes",
        "Monthly spot rate comparison (USD/FEU) and relative demand index — ocean Trans-Pacific EB",
        icon="⚖️",
    )

    months_l    = [_MONTH_ABBR_LIST[m - 1] for m in _ECOM_TRAD_RATES_MONTHLY["months"]]
    ecom_rates  = _ECOM_TRAD_RATES_MONTHLY["ecom_spot"]
    trad_rates  = _ECOM_TRAD_RATES_MONTHLY["traditional"]
    ecom_vol    = _ECOM_TRAD_RATES_MONTHLY["ecom_volume_idx"]
    trad_vol    = _ECOM_TRAD_RATES_MONTHLY["trad_volume_idx"]

    col_rates, col_vols = st.columns(2)

    with col_rates:
        fig_rates = go.Figure()
        fig_rates.add_trace(go.Bar(
            name="E-Commerce Spot",
            x=months_l, y=ecom_rates,
            marker_color=C_CYAN, marker_opacity=0.82,
            hovertemplate="<b>%{x}</b><br>E-Com Spot: $%{y:,}/FEU<extra></extra>",
        ))
        fig_rates.add_trace(go.Bar(
            name="Traditional Contract",
            x=months_l, y=trad_rates,
            marker_color=C_ACCENT, marker_opacity=0.72,
            hovertemplate="<b>%{x}</b><br>Traditional: $%{y:,}/FEU<extra></extra>",
        ))
        fig_rates.update_layout(
            barmode="group",
            template="plotly_dark", paper_bgcolor=C_BG, plot_bgcolor=C_BG,
            height=300, margin=dict(l=50, r=20, t=30, b=40),
            title=dict(text="Freight Rate: E-Com Spot vs Traditional ($/FEU)",
                       font=dict(color=C_TEXT2, size=11), x=0),
            font=dict(color=C_TEXT, family="monospace"),
            legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                        x=0.01, y=0.99),
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=10),
                       gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(title="$/FEU", tickfont=dict(color=C_TEXT2, size=10),
                       gridcolor="rgba(255,255,255,0.05)", tickprefix="$", tickformat=","),
        )
        st.plotly_chart(fig_rates, use_container_width=True, key="new_ecom_rate_compare_bars")

    with col_vols:
        fig_vols = go.Figure()
        fig_vols.add_trace(go.Bar(
            name="E-Commerce Volume Idx",
            x=months_l, y=ecom_vol,
            marker_color=C_CYAN, marker_opacity=0.82,
            hovertemplate="<b>%{x}</b><br>E-Com Volume: %{y}<extra></extra>",
        ))
        fig_vols.add_trace(go.Bar(
            name="Traditional Volume Idx",
            x=months_l, y=trad_vol,
            marker_color=C_ACCENT, marker_opacity=0.72,
            hovertemplate="<b>%{x}</b><br>Traditional Volume: %{y}<extra></extra>",
        ))
        fig_vols.add_hline(
            y=100, line_dash="dot", line_color="rgba(255,255,255,0.2)", line_width=1,
            annotation_text="Avg", annotation_font=dict(color=C_TEXT3, size=9),
            annotation_position="right",
        )
        fig_vols.update_layout(
            barmode="group",
            template="plotly_dark", paper_bgcolor=C_BG, plot_bgcolor=C_BG,
            height=300, margin=dict(l=50, r=20, t=30, b=40),
            title=dict(text="Volume Demand Index (100 = annual avg)",
                       font=dict(color=C_TEXT2, size=11), x=0),
            font=dict(color=C_TEXT, family="monospace"),
            legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                        x=0.01, y=0.99),
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=10),
                       gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(title="Index", tickfont=dict(color=C_TEXT2, size=10),
                       gridcolor="rgba(255,255,255,0.05)"),
        )
        st.plotly_chart(fig_vols, use_container_width=True, key="new_ecom_vol_compare_bars")

    _divider()

    # ── D: Direct-to-consumer Asia→US parcel flow chart ──────────────────
    _section_header(
        "Asia → US Direct-to-Consumer Parcel Flow",
        "Annual parcel volumes (millions) by mode: air vs ocean — showing rapid DTC growth 2020–2025",
        icon="📦",
    )

    years_dtc = [d["year"] for d in _DTC_ANNUAL]
    air_vals  = [d["air_m"] for d in _DTC_ANNUAL]
    ocean_vals = [d["ocean_m"] for d in _DTC_ANNUAL]
    total_vals = [a + o for a, o in zip(air_vals, ocean_vals)]

    fig_dtc = go.Figure()
    fig_dtc.add_trace(go.Bar(
        name="Air (M parcels)",
        x=years_dtc, y=air_vals,
        marker_color=C_CYAN, marker_opacity=0.85,
        text=[f"{v}M" for v in air_vals],
        textposition="inside", textfont=dict(color="white", size=10),
        hovertemplate="<b>%{x}</b><br>Air: %{y}M parcels<extra></extra>",
    ))
    fig_dtc.add_trace(go.Bar(
        name="Ocean (M parcels)",
        x=years_dtc, y=ocean_vals,
        marker_color=C_ACCENT, marker_opacity=0.85,
        text=[f"{v}M" for v in ocean_vals],
        textposition="inside", textfont=dict(color="white", size=10),
        hovertemplate="<b>%{x}</b><br>Ocean: %{y}M parcels<extra></extra>",
    ))
    fig_dtc.add_trace(go.Scatter(
        name="Total",
        x=years_dtc, y=total_vals,
        mode="lines+markers",
        line=dict(color=C_WARN, width=2.5, dash="dot"),
        marker=dict(color=C_WARN, size=8, line=dict(color=C_BG, width=1.5)),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Total: %{y}M parcels<extra></extra>",
    ))

    # CAGR annotation
    cagr = ((total_vals[-1] / total_vals[0]) ** (1 / (len(total_vals) - 1)) - 1) * 100
    fig_dtc.add_annotation(
        x=2025, y=total_vals[-1] + 80,
        text=f"CAGR {cagr:.1f}%",
        showarrow=False,
        font=dict(color=C_HIGH, size=11, family="Inter, sans-serif"),
        bgcolor="rgba(16,185,129,0.12)",
        bordercolor="rgba(16,185,129,0.35)", borderwidth=1, borderpad=4,
    )

    fig_dtc.update_layout(
        barmode="stack",
        template="plotly_dark", paper_bgcolor=C_BG, plot_bgcolor=C_BG,
        height=360, margin=dict(l=50, r=80, t=20, b=40),
        font=dict(color=C_TEXT, family="monospace"),
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(10,15,26,0.6)",
                    bordercolor=C_BORDER, borderwidth=1, x=0.01, y=0.99),
        xaxis=dict(tickfont=dict(color=C_TEXT2, size=11),
                   gridcolor="rgba(255,255,255,0.04)"),
        yaxis=dict(title="Million Parcels", tickfont=dict(color=C_TEXT2, size=10),
                   gridcolor="rgba(255,255,255,0.05)"),
        yaxis2=dict(title="Total (M parcels)", tickfont=dict(color=C_WARN, size=10),
                    overlaying="y", side="right", showgrid=False,
                    titlefont=dict(color=C_WARN)),
    )
    st.plotly_chart(fig_dtc, use_container_width=True, key="new_ecom_dtc_annual_flow")

    st.markdown(
        '<div style="background:rgba(6,182,212,0.07);border:1px solid rgba(6,182,212,0.25);'
        'border-radius:10px;padding:12px 16px;margin-top:2px">'
        '<span style="font-size:0.77rem;color:' + C_CYAN + ';font-weight:700">DTC INSIGHT: </span>'
        '<span style="font-size:0.77rem;color:' + C_TEXT2 + '">'
        f"Total Asia-US DTC parcels grew from {total_vals[0]}M (2020) to {total_vals[-1]}M (2025) — "
        f"a {cagr:.0f}% CAGR. Air maintains 68-73% modal share; ocean share is growing as platforms "
        "pre-position bonded warehouse inventory inside the US. De minimis reform could accelerate "
        "the ocean shift by +200K TEU/year."
        "</span></div>",
        unsafe_allow_html=True,
    )

    _divider()

    # ── E: Air vs ocean split — stacked bars by product category ─────────
    _section_header(
        "Air vs. Ocean Split by E-Commerce Category",
        "When e-commerce goes by air vs. ocean — driven by product value, transit time, and de minimis",
        icon="✈️",
    )

    cats_sorted = sorted(_ECOM_AIR_OCEAN_CATEGORIES, key=lambda c: c["air_pct"], reverse=True)
    cat_names   = [c["category"] for c in cats_sorted]
    air_pcts    = [c["air_pct"] for c in cats_sorted]
    ocean_pcts  = [100 - a for a in air_pcts]
    cat_colors  = [c["color"] for c in cats_sorted]

    fig_split = go.Figure()
    fig_split.add_trace(go.Bar(
        name="Air %",
        x=cat_names, y=air_pcts,
        marker_color=cat_colors, marker_opacity=0.88,
        text=[f"{v}%" for v in air_pcts],
        textposition="inside", textfont=dict(color="white", size=11, family="monospace"),
        hovertemplate="<b>%{x}</b><br>Air: %{y}%<extra></extra>",
    ))
    fig_split.add_trace(go.Bar(
        name="Ocean %",
        x=cat_names, y=ocean_pcts,
        marker_color="rgba(255,255,255,0.08)",
        marker_line_color="rgba(255,255,255,0.18)", marker_line_width=1,
        text=[f"{v}%" for v in ocean_pcts],
        textposition="inside", textfont=dict(color=C_TEXT3, size=10, family="monospace"),
        hovertemplate="<b>%{x}</b><br>Ocean: %{y}%<extra></extra>",
    ))

    # Highlight fast fashion as de minimis driver
    fig_split.add_annotation(
        x="Fast Fashion", y=96,
        text="De minimis<br>driven",
        showarrow=True, arrowhead=2, arrowcolor=C_WARN, arrowwidth=1.5,
        ax=60, ay=-30,
        font=dict(color=C_WARN, size=9),
        bgcolor="rgba(10,15,26,0.88)",
        bordercolor=C_WARN, borderwidth=1, borderpad=4,
    )

    fig_split.update_layout(
        barmode="stack",
        template="plotly_dark", paper_bgcolor=C_BG, plot_bgcolor=C_BG,
        height=340, margin=dict(l=40, r=20, t=20, b=60),
        font=dict(color=C_TEXT, family="monospace"),
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(10,15,26,0.6)",
                    bordercolor=C_BORDER, borderwidth=1, x=0.01, y=0.99),
        xaxis=dict(tickfont=dict(color=C_TEXT2, size=10),
                   gridcolor="rgba(255,255,255,0.04)", tickangle=-25),
        yaxis=dict(title="Modal Split (%)", tickfont=dict(color=C_TEXT2, size=10),
                   gridcolor="rgba(255,255,255,0.06)", ticksuffix="%", range=[0, 112]),
    )
    st.plotly_chart(fig_split, use_container_width=True, key="new_ecom_air_ocean_split_bars")

    st.markdown(
        '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:4px">'
        + "".join(
            f'<div style="background:{c["color"]}11;border:1px solid {c["color"]}33;'
            f'border-radius:6px;padding:5px 10px;font-size:0.70rem;color:{C_TEXT2}">'
            f'<b style="color:{c["color"]}">{c["category"]}</b>: '
            f'{c["air_pct"]}% air / {100-c["air_pct"]}% ocean</div>'
            for c in cats_sorted
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    _divider()


# ── Section 0: Hero Overview Bar ──────────────────────────────────────────────

def _render_hero(trade_data: dict | None, freight_data: dict | None) -> None:
    today = date.today()
    demand_idx = compute_ecommerce_demand_index(today.month)
    tp_val  = demand_idx["transpacific_eb"]["index"]
    tp_lbl  = demand_idx["transpacific_eb"]["label"]
    tp_color = _demand_color(tp_val)

    # Derive supplemental metrics (gracefully from data or fallback)
    ecom_share_pct = 22.4  # ~22% of containerized trade is e-commerce driven (2025 est.)
    yoy_growth     = 14.2  # % YoY growth in e-commerce shipping volumes
    dtc_parcels_b  = 1.1   # billion direct-to-consumer parcels annually
    air_shift_pp   = 6.5   # percentage points air modal shift YoY

    # Top KPI strip
    kpis = [
        ("E-COMMERCE SHARE", f"{ecom_share_pct}%", "of containerized trade", tp_color),
        ("DEMAND INDEX NOW", f"{tp_val}x", tp_lbl + " — Trans-Pac EB", tp_color),
        ("YOY VOLUME GROWTH", f"+{yoy_growth}%", "cross-border e-commerce parcels", C_HIGH),
        ("DTC PARCELS/YEAR", f"{dtc_parcels_b}B", "Asia→US direct consumer shipments", C_CYAN),
        ("AIR MODAL SHIFT", f"+{air_shift_pp}pp", "YoY air share gain vs ocean", C_WARN),
    ]

    cols = st.columns(5)
    for i, (lbl, val, sub, color) in enumerate(kpis):
        with cols[i]:
            st.markdown(
                _stat_card(lbl, val, sub, color=color,
                           border_color=color + "44"),
                unsafe_allow_html=True,
            )

    # Narrative context banner
    st.markdown(
        '<div style="background:linear-gradient(135deg, rgba(59,130,246,0.06) 0%,'
        ' rgba(139,92,246,0.06) 100%); border:1px solid rgba(59,130,246,0.18);'
        ' border-radius:12px; padding:14px 20px; margin-top:4px">'
        '<span style="font-size:0.80rem; color:' + C_TEXT2 + '">  '
        "E-commerce now drives <b style='color:" + C_TEXT + "'>22%</b> of all containerized trade — "
        "up from 12% in 2020. Platforms like SHEIN, Temu, and Alibaba have fundamentally "
        "reshaped trans-Pacific demand, creating discrete calendar-driven spikes that require "
        "container procurement <b style='color:" + C_WARN + "'>8-16 weeks ahead</b> of peak events. "
        "De minimis policy reform remains the single largest structural wildcard for ocean carriers."
        "</span></div>",
        unsafe_allow_html=True,
    )


# ── Section 1: E-commerce Demand Pulse ────────────────────────────────────────

def _render_platform_cards() -> None:
    logger.debug("Rendering e-commerce platform demand pulse cards")
    _section_header(
        "E-Commerce Demand Pulse",
        "Real-time shipping signals from major platforms — 2025/2026 data",
        icon="📡",
    )

    primary_platforms = ["AMAZON", "ALIBABA", "SHEIN", "SHOPIFY"]
    available_platforms = [p for p in primary_platforms if ECOMMERCE_SIGNALS.get(p)]
    if not available_platforms:
        st.info("E-commerce demand signal data is currently unavailable. Check back later.")
        return

    cols = st.columns(4)
    for i, platform in enumerate(primary_platforms):
        signals = ECOMMERCE_SIGNALS.get(platform, [])
        if not signals:
            continue

        lead  = signals[0]
        color = _PLATFORM_COLORS.get(platform, C_ACCENT)
        label = _PLATFORM_LABELS.get(platform, platform)
        vol   = _PLATFORM_VOLUMES.get(platform, 0)
        air_p = _PLATFORM_AIR_PCT.get(platform, 50)

        growth_str = (
            "+" + str(round(lead.yoy_growth_pct, 0)).rstrip("0").rstrip(".") + "% YoY"
            if lead.yoy_growth_pct >= 0
            else str(round(lead.yoy_growth_pct, 0)).rstrip("0").rstrip(".") + "% YoY"
        )
        growth_color = C_HIGH if lead.yoy_growth_pct >= 0 else C_DANGER
        routes_html  = "".join(_route_pill(r) for r in lead.affected_routes[:2])
        signal_preview = (
            lead.shipping_implication[:110] + "…"
            if len(lead.shipping_implication) > 110
            else lead.shipping_implication
        )

        # Mini air/ocean bar
        ocean_p = 100 - air_p
        mode_bar = (
            '<div style="margin:8px 0 4px">'
            '<div style="display:flex; justify-content:space-between; font-size:0.63rem;'
            ' color:' + C_TEXT3 + '; margin-bottom:3px">'
            '<span>Ocean ' + str(int(ocean_p)) + '%</span>'
            '<span>Air ' + str(int(air_p)) + '%</span>'
            "</div>"
            '<div style="height:5px; border-radius:3px; background:rgba(255,255,255,0.07);'
            ' overflow:hidden">'
            '<div style="height:100%; width:' + str(int(ocean_p)) + '%; background:'
            + C_ACCENT + '88; border-radius:3px 0 0 3px; float:left"></div>'
            '<div style="height:100%; width:' + str(int(air_p)) + '%; background:'
            + C_CYAN + '88; border-radius:0 3px 3px 0; float:left"></div>'
            "</div></div>"
        )

        content = (
            '<div style="display:flex; align-items:flex-start; gap:10px; margin-bottom:10px">'
            + _platform_logo(platform, 38)
            + '<div><div style="font-size:1.0rem; font-weight:800; color:' + C_TEXT + '">'
            + label + "</div>"
            + '<div style="font-size:0.68rem; color:' + C_TEXT3 + '">'
            + str(int(vol)) + "M parcels/yr</div></div></div>"
            + '<div style="margin-bottom:8px; display:flex; gap:4px; flex-wrap:wrap">'
            + _badge(growth_str, growth_color)
            + "&nbsp;"
            + _badge("Conf " + str(int(lead.confidence * 100)) + "%", C_TEXT3)
            + "</div>"
            + '<div style="font-size:0.74rem; color:' + C_TEXT2 + '; margin-bottom:8px; line-height:1.48">'
            + signal_preview + "</div>"
            + mode_bar
            + '<div style="font-size:0.67rem; color:' + C_TEXT3 + '; margin-top:8px; margin-bottom:5px">ROUTES</div>'
            + routes_html
            + '<div style="font-size:0.68rem; color:' + C_TEXT3 + '; margin-top:10px">'
            + "Lead time: <b style='color:" + color + "'>" + str(lead.lead_time_weeks) + " wks</b>"
            + "</div>"
        )
        with cols[i]:
            st.markdown(_card(content, border_color=color + "44"), unsafe_allow_html=True)

    # Expanded signals table
    with st.expander("View all platform signals", expanded=False,
                     key="ecommerce_platform_signals_expander"):
        for platform, signals in ECOMMERCE_SIGNALS.items():
            color = _PLATFORM_COLORS.get(platform, C_ACCENT)
            label = _PLATFORM_LABELS.get(platform, platform)
            st.markdown(
                '<div style="font-size:0.90rem; font-weight:700; color:' + color
                + '; margin:12px 0 6px">' + label + "</div>",
                unsafe_allow_html=True,
            )
            for sig in signals:
                g_str = (
                    "+" + str(round(sig.yoy_growth_pct, 1)) + "% YoY"
                    if sig.yoy_growth_pct >= 0
                    else str(round(sig.yoy_growth_pct, 1)) + "% YoY"
                )
                routes_html = "".join(_route_pill(r) for r in sig.affected_routes)
                st.markdown(
                    '<div style="background:rgba(26,34,53,0.7); border-left:3px solid '
                    + color + '44; padding:10px 14px; margin-bottom:8px;'
                    ' border-radius:0 8px 8px 0">'
                    '<div style="font-size:0.78rem; font-weight:700; color:' + C_TEXT
                    + '; margin-bottom:4px">'
                    + sig.metric_name + "  "
                    + _badge(g_str, C_HIGH if sig.yoy_growth_pct >= 0 else C_DANGER)
                    + "</div>"
                    '<div style="font-size:0.74rem; color:' + C_TEXT2
                    + '; margin-bottom:8px; line-height:1.45">'
                    + sig.shipping_implication + "</div>"
                    + routes_html + "</div>",
                    unsafe_allow_html=True,
                )

    # Export
    def _platform_signals_csv() -> bytes:
        buf = io.StringIO()
        w = _csv_mod.writer(buf)
        w.writerow(["Platform", "Metric", "YoY Growth %", "Lead Time (wks)",
                    "Confidence", "Affected Routes", "Shipping Implication"])
        for platform, signals in ECOMMERCE_SIGNALS.items():
            lbl = _PLATFORM_LABELS.get(platform, platform)
            for sig in signals:
                w.writerow([lbl, sig.metric_name, round(sig.yoy_growth_pct, 1),
                            sig.lead_time_weeks, round(sig.confidence, 2),
                            "; ".join(sig.affected_routes), sig.shipping_implication])
        return buf.getvalue().encode()

    st.download_button(
        label="Download platform signals CSV",
        data=_platform_signals_csv(),
        file_name="ecommerce_platform_signals.csv",
        mime="text/csv",
        key="dl_platform_signals",
    )


# ── Section 2: Peak Season Demand Calendar ────────────────────────────────────

def _render_peak_calendar() -> None:
    logger.debug("Rendering peak season demand calendar")
    _section_header(
        "Peak Season Demand Calendar",
        "12-month trans-Pacific demand heat strip with key e-commerce event overlays",
        icon="📅",
    )

    today = date.today()

    # Peak event spotlight cards
    spotlight_events = [e for e in _PEAK_EVENTS if e["severity"] in ("CRITICAL", "HIGH")]
    if spotlight_events:
        spot_cols = st.columns(min(len(spotlight_events), 3))
        for i, ev in enumerate(spotlight_events[:3]):
            color = ev["color"]
            months_str = " / ".join(_MONTH_NAMES[m] for m in ev["months"] if 1 <= m <= 12)
            with spot_cols[i]:
                st.markdown(
                    '<div style="background:' + color + '0f; border:1px solid '
                    + color + '44; border-radius:12px; padding:14px 16px; height:100%">'
                    '<div style="display:flex; justify-content:space-between;'
                    ' align-items:flex-start; margin-bottom:8px">'
                    '<span style="font-size:0.65rem; font-weight:900; color:' + color
                    + '; letter-spacing:0.1em; background:' + color + '22;'
                    ' padding:3px 8px; border-radius:5px">'
                    + ev["icon"] + "</span>"
                    + _badge(ev["severity"], color) + "</div>"
                    '<div style="font-size:0.92rem; font-weight:800; color:' + C_TEXT
                    + '; margin-bottom:4px">' + ev["name"] + "</div>"
                    '<div style="font-size:0.70rem; color:' + color
                    + '; margin-bottom:8px; font-weight:600">' + months_str + "</div>"
                    '<div style="font-size:0.73rem; color:' + C_TEXT2
                    + '; line-height:1.5">' + ev["impact"] + "</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )
        st.markdown("<br>", unsafe_allow_html=True)

    # 12-month calendar heat strip
    if not RETAIL_CALENDAR:
        st.info("Retail calendar data is currently unavailable.")
        return

    event_by_month: dict[int, list[str]] = {}
    for cal in RETAIL_CALENDAR:
        event_by_month.setdefault(cal.month, []).append(cal.event_name)

    booking_by_month: dict[int, list[str]] = {}
    for cal in RETAIL_CALENDAR:
        bw_month = cal.month - int(math.ceil(cal.typical_order_window_weeks_before / 4.33))
        bw_month = ((bw_month - 1) % 12) + 1
        booking_by_month.setdefault(bw_month, []).append("Book: " + cal.event_name)

    peak_months = {10, 11}   # Q4 holiday
    cny_months  = {1, 2}     # Chinese New Year
    bfcm_months = {11}       # Black Friday / Cyber Monday

    months_html = '<div style="display:flex; gap:5px; overflow-x:auto; padding-bottom:8px">'
    for offset in range(12):
        m = ((today.month - 1 + offset) % 12) + 1
        year_offset = (today.month - 1 + offset) // 12
        yr = today.year + year_offset

        idx = _TP_MONTHLY[m]
        bg  = _demand_color(idx)
        dlbl = _demand_label(idx)
        is_current = (m == today.month and yr == today.year)

        events   = event_by_month.get(m, [])
        bookings = booking_by_month.get(m, [])

        # Special event badges
        event_badges = ""
        if m in peak_months:
            event_badges += '<div style="font-size:0.55rem; font-weight:700; color:' + C_DANGER + '; background:' + C_DANGER + '1a; border-radius:3px; padding:1px 4px; margin:1px 0; text-align:center">Q4 PEAK</div>'
        if m in cny_months:
            event_badges += '<div style="font-size:0.55rem; font-weight:700; color:' + C_DANGER + '; background:' + C_DANGER + '1a; border-radius:3px; padding:1px 4px; margin:1px 0; text-align:center">CNY</div>'
        if m in bfcm_months:
            event_badges += '<div style="font-size:0.55rem; font-weight:700; color:' + C_ORANGE + '; background:' + C_ORANGE + '1a; border-radius:3px; padding:1px 4px; margin:1px 0; text-align:center">BFCM</div>'

        dot_events   = "".join(
            '<div style="width:5px; height:5px; border-radius:50%; background:' + C_TEXT
            + '; margin:2px auto" title="' + e + '"></div>'
            for e in events
        )
        dot_bookings = "".join(
            '<div style="width:5px; height:5px; border-radius:50%; background:' + C_WARN
            + '; margin:2px auto" title="' + b + '"></div>'
            for b in bookings
        )

        border = "2px solid " + C_TEXT if is_current else "1px solid " + bg + "44"
        now_lbl = (
            '<div style="font-size:0.55rem; color:' + C_TEXT + '; text-align:center;'
            ' font-weight:900; margin-bottom:2px">NOW</div>'
            if is_current else ""
        )

        # Demand bar fill
        fill_w = int((idx / 1.5) * 100)
        fill_w = min(fill_w, 100)

        cell = (
            '<div style="min-width:68px; background:' + bg + '0d; border:' + border + ';'
            ' border-radius:10px; padding:9px 6px; text-align:center; flex:1">'
            + now_lbl
            + '<div style="font-size:0.78rem; font-weight:700; color:' + C_TEXT + '">'
            + _MONTH_NAMES[m] + "</div>"
            + '<div style="font-size:0.62rem; color:' + C_TEXT3 + '; margin-bottom:5px">'
            + str(yr) + "</div>"
            + '<div style="font-size:1.05rem; font-weight:900; color:' + bg
            + '; line-height:1; margin-bottom:2px">' + str(round(idx, 2)) + "x</div>"
            + '<div style="font-size:0.55rem; color:' + bg + '; font-weight:700;'
            ' margin-bottom:6px">' + dlbl + "</div>"
            + '<div style="height:3px; background:rgba(255,255,255,0.07); border-radius:2px;'
            ' margin:0 4px 6px; overflow:hidden">'
            '<div style="height:100%; width:' + str(fill_w) + '%; background:' + bg
            + '; border-radius:2px"></div></div>'
            + event_badges
            + dot_events
            + dot_bookings
            + "</div>"
        )
        months_html += cell

    months_html += "</div>"

    legend_html = (
        '<div style="display:flex; gap:14px; margin-top:10px; flex-wrap:wrap;'
        ' align-items:center">'
        '<span style="font-size:0.68rem; color:' + C_TEXT3 + '; font-weight:600;'
        ' text-transform:uppercase; letter-spacing:0.06em">Legend:</span>'
        + "".join(
            '<div style="display:flex; align-items:center; gap:5px">'
            '<div style="width:8px; height:8px; border-radius:2px; background:' + col + '"></div>'
            '<span style="font-size:0.70rem; color:' + C_TEXT2 + '">' + lbl + "</span></div>"
            for col, lbl in [
                (C_DANGER, "PEAK 1.4x+"),
                (C_ORANGE, "HIGH 1.25x+"),
                (C_WARN, "MOD 1.10x+"),
                (C_HIGH, "LOW <0.95x"),
            ]
        )
        + '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:8px; height:8px; border-radius:50%; background:' + C_TEXT + '"></div>'
        '<span style="font-size:0.70rem; color:' + C_TEXT2 + '">Retail event</span></div>'
        '<div style="display:flex; align-items:center; gap:5px">'
        '<div style="width:8px; height:8px; border-radius:50%; background:' + C_WARN + '"></div>'
        '<span style="font-size:0.70rem; color:' + C_TEXT2 + '">Book window</span></div>'
        "</div>"
    )

    st.markdown(months_html + legend_html, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    with st.expander("Key event details & book-by dates", expanded=False,
                     key="ecommerce_event_details_expander"):
        for cal in sorted(RETAIL_CALENDAR or [], key=lambda c: (c.month, c.day)):
            bw_month_raw = cal.month - int(math.ceil(cal.typical_order_window_weeks_before / 4.33))
            bw_month = ((bw_month_raw - 1) % 12) + 1
            bw_label = _MONTH_NAMES[bw_month]
            mult_str = str(round(cal.container_demand_multiplier, 2)) + "x"
            routes_html = "".join(_route_pill(r) for r in cal.affected_routes)
            m_color = _demand_color(cal.container_demand_multiplier)
            st.markdown(
                '<div style="border-left:3px solid ' + m_color + '66; padding:10px 14px;'
                ' margin-bottom:8px; border-radius:0 8px 8px 0; background:rgba(26,34,53,0.5)">'
                '<div style="display:flex; justify-content:space-between; align-items:center;'
                ' margin-bottom:4px">'
                '<span style="font-weight:700; color:' + C_TEXT + '; font-size:0.84rem">'
                + cal.event_name + "</span>"
                + _badge(mult_str + " demand", m_color)
                + "</div>"
                + '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin:4px 0">'
                + cal.description + "</div>"
                + '<div style="font-size:0.69rem; color:' + C_TEXT3 + '; margin-top:6px">'
                + "Book containers in: <b style='color:" + C_WARN + "'>" + bw_label + "</b>"
                + "  |  Lead time: <b style='color:" + C_ACCENT + "'>"
                + str(cal.typical_order_window_weeks_before) + " wks</b>"
                + "</div>"
                + '<div style="margin-top:7px">' + routes_html + "</div>"
                + "</div>",
                unsafe_allow_html=True,
            )


# ── Section 3: Booking Window Alerts ──────────────────────────────────────────

def _render_booking_alerts() -> None:
    logger.debug("Rendering booking window alert cards")
    _section_header(
        "Booking Window Alerts",
        "Act now — container procurement lead times are unforgiving",
        icon="⚡",
    )

    windows = get_seasonal_booking_windows()
    if not windows:
        st.info("No upcoming booking windows in the next 52 weeks.")
        return

    for w in windows:
        urgency   = w["urgency_level"]
        color     = _URGENCY_COLOR.get(urgency, C_TEXT3)
        icon      = _URGENCY_ICON.get(urgency, "")
        wk_book   = w["weeks_until_book_by"]
        wk_event  = w["weeks_until_event"]
        event     = w["event_name"]
        mult      = w["demand_multiplier"]
        book_date = w["book_by_date"]

        if wk_book <= 0:
            time_label = "Booking window OPEN NOW"
        elif wk_book == 1:
            time_label = "Book within 1 week"
        else:
            time_label = f"Book within {wk_book} weeks"

        routes_html = "".join(_route_pill(r) for r in w["affected_routes"])
        mult_str    = str(round(mult, 2)) + "x demand"

        # Urgency progress bar (weeks remaining as visual)
        urgency_width = max(5, min(100, int((1 - (wk_book / 16)) * 100))) if wk_book > 0 else 100

        content = (
            '<div style="display:flex; align-items:flex-start; gap:14px">'
            '<div style="font-size:1.5rem; line-height:1; flex-shrink:0; margin-top:2px">'
            + icon + "</div>"
            '<div style="flex:1">'
            '<div style="display:flex; justify-content:space-between; align-items:center;'
            ' margin-bottom:6px">'
            '<span style="font-size:0.90rem; font-weight:800; color:' + color + '">'
            + urgency + ": " + event.upper() + "</span>"
            + _badge(mult_str, C_WARN)
            + "</div>"
            + '<div style="font-size:0.80rem; color:' + C_TEXT + '; font-weight:600;'
            ' margin-bottom:4px">'
            + time_label + " — book by " + book_date.strftime("%B %d, %Y") + "</div>"
            + '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-bottom:10px">'
            + f"Event in {wk_event} weeks" + "</div>"
            + '<div style="height:4px; background:rgba(255,255,255,0.07); border-radius:2px;'
            ' margin-bottom:10px; overflow:hidden">'
            '<div style="height:100%; width:' + str(urgency_width) + '%; background:'
            + color + '; border-radius:2px; transition:width 0.3s"></div></div>'
            + routes_html
            + "</div></div>"
        )

        st.markdown(_card(content, border_color=color + "66"), unsafe_allow_html=True)


# ── Section 4: DTC Shipping Growth ────────────────────────────────────────────

def _render_dtc_growth() -> None:
    logger.debug("Rendering DTC shipping growth chart")
    _section_header(
        "Direct-to-Consumer Shipping Growth",
        "Asia-US direct parcel flows (millions of units) — air vs ocean split by quarter",
        icon="📦",
    )

    labels = _DTC_QUARTERLY["labels"]
    air    = _DTC_QUARTERLY["air"]
    ocean  = _DTC_QUARTERLY["ocean"]

    fig = go.Figure()

    # Ocean bars
    fig.add_trace(go.Bar(
        name="Ocean Freight (M units)",
        x=labels, y=ocean,
        marker_color=C_ACCENT,
        marker_opacity=0.85,
        marker_line_color="rgba(255,255,255,0.15)",
        marker_line_width=0.5,
        text=[str(v) + "M" for v in ocean],
        textposition="inside",
        textfont=dict(color="white", size=10),
        hovertemplate="<b>%{x}</b><br>Ocean: %{y}M units<extra></extra>",
    ))

    # Air bars
    fig.add_trace(go.Bar(
        name="Air Freight (M units)",
        x=labels, y=air,
        marker_color=C_CYAN,
        marker_opacity=0.85,
        marker_line_color="rgba(255,255,255,0.15)",
        marker_line_width=0.5,
        text=[str(v) + "M" for v in air],
        textposition="inside",
        textfont=dict(color="white", size=10),
        hovertemplate="<b>%{x}</b><br>Air: %{y}M units<extra></extra>",
    ))

    # Trend line — total
    total = [a + o for a, o in zip(air, ocean)]
    fig.add_trace(go.Scatter(
        name="Total (M units)",
        x=labels, y=total,
        mode="lines+markers",
        line=dict(color=C_WARN, width=2, dash="dot"),
        marker=dict(color=C_WARN, size=7, line=dict(color=C_BG, width=1.5)),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Total: %{y}M units<extra></extra>",
    ))

    # Q4 annotations
    q4_labels = [l for l in labels if l.startswith("Q4")]
    for ql in q4_labels:
        if ql in labels:
            idx = labels.index(ql)
            fig.add_annotation(
                x=ql, y=total[idx] + 20,
                text="Q4 Peak",
                showarrow=True, arrowhead=1,
                arrowcolor=C_WARN, arrowwidth=1,
                font=dict(color=C_WARN, size=9),
                ax=0, ay=-28,
                bgcolor="rgba(10,15,26,0.8)",
                bordercolor=C_WARN, borderwidth=1, borderpad=4,
            )

    fig.update_layout(
        barmode="stack",
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, family="monospace"),
        height=380,
        margin=dict(l=50, r=80, t=20, b=50),
        legend=dict(
            font=dict(color=C_TEXT2, size=11),
            bgcolor="rgba(10,15,26,0.6)",
            bordercolor=C_BORDER, borderwidth=1,
            x=0.01, y=0.99,
        ),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.04)",
            tickangle=-30,
        ),
        yaxis=dict(
            title=dict(text="Million Units", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(255,255,255,0.06)",
        ),
        yaxis2=dict(
            title=dict(text="Total (M units)", font=dict(color=C_WARN, size=11)),
            tickfont=dict(color=C_WARN, size=10),
            overlaying="y", side="right",
            showgrid=False,
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="ecommerce_dtc_growth_chart")

    # Context callout
    st.markdown(
        '<div style="background:rgba(6,182,212,0.07); border:1px solid rgba(6,182,212,0.25);'
        ' border-radius:10px; padding:12px 16px">'
        '<span style="font-size:0.77rem; color:' + C_CYAN + '; font-weight:700">DTC INSIGHT: </span>'
        '<span style="font-size:0.77rem; color:' + C_TEXT2 + '">'
        "Asia-US direct-to-consumer parcel flows grew 176% from Q1 2023 to Q4 2025. "
        "Air maintains 68-75% modal share driven by SHEIN/Temu de minimis strategy; "
        "ocean growth is accelerating as platforms pre-position bonded US inventory. "
        "Q4 peaks consistently run 40-55% above Q1 baselines — book by August for holiday."
        "</span></div>",
        unsafe_allow_html=True,
    )


# ── Section 5: E-Commerce vs Traditional Retail ────────────────────────────────

def _render_ecom_vs_traditional() -> None:
    logger.debug("Rendering e-commerce vs traditional retail shipping comparison")
    _section_header(
        "E-Commerce vs. Traditional Retail Shipping",
        "Structural differences in rates, routes, vessel requirements, and booking patterns",
        icon="⚖️",
    )

    col1, col2 = st.columns(2)

    # Comparison table rows
    comparisons = [
        ("Freight Rate Sensitivity", "High — spikes 30-60% during peaks",     "Moderate — annual contract rates with stability"),
        ("Booking Lead Time",        "2-4 weeks (just-in-time replenishment)", "8-16 weeks (seasonal buying cycles)"),
        ("Vessel Type Preference",   "LCL & expedited services preferred",     "Full container loads (FCL), 20K+ TEU vessels"),
        ("Primary Route",            "Trans-Pacific EB (Asia→US/EU DTC)",      "Trans-Pacific EB + Asia-Europe both balanced"),
        ("Peak Season",              "Year-round events; Q4, Prime Day, 11.11","Traditional Q4 holiday + early spring"),
        ("Return Rate",              "15-38% depending on category",           "2-8% average retail return rate"),
        ("Port of Choice",           "LA/LB, Chicago air gateway",             "Spread across major US/EU gateway ports"),
        ("Container Type",           "LCL, parcel, air freight dominant",      "FCL 40' standard + high cube containers"),
    ]

    rows_html = ""
    for i, (attr, ecom, trad) in enumerate(comparisons):
        row_bg = "rgba(26,34,53,0.4)" if i % 2 == 0 else "rgba(26,34,53,0.1)"
        rows_html += (
            '<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px;'
            ' padding:10px 12px; background:' + row_bg + '; border-radius:8px; margin-bottom:4px">'
            '<div style="font-size:0.74rem; font-weight:700; color:' + C_TEXT2 + '">'
            + attr + "</div>"
            '<div style="font-size:0.73rem; color:' + C_CYAN + '; line-height:1.45">'
            + ecom + "</div>"
            '<div style="font-size:0.73rem; color:' + C_ACCENT + '; line-height:1.45">'
            + trad + "</div>"
            "</div>"
        )

    with col1:
        header_html = (
            '<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px;'
            ' padding:8px 12px; margin-bottom:6px">'
            '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em;'
            ' color:' + C_TEXT3 + '">Attribute</div>'
            '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em;'
            ' color:' + C_CYAN + '">E-Commerce</div>'
            '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em;'
            ' color:' + C_ACCENT + '">Traditional</div>'
            "</div>"
        )
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-radius:12px; padding:16px; overflow:hidden">'
            + header_html + rows_html + "</div>",
            unsafe_allow_html=True,
        )

    with col2:
        # Rate premium chart: e-com peak vs traditional contract
        categories = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        ecom_rates  = [2100, 1800, 2000, 2200, 2600, 3100, 3800, 4200, 3600, 3200, 2800, 2400]
        trad_rates  = [2400, 2300, 2300, 2400, 2400, 2500, 2600, 2700, 2700, 2800, 2700, 2500]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            name="E-Commerce Spot",
            x=categories, y=ecom_rates,
            mode="lines+markers",
            fill="tozeroy",
            fillcolor="rgba(6,182,212,0.08)",
            line=dict(color=C_CYAN, width=2.5),
            marker=dict(color=C_CYAN, size=7, line=dict(color=C_BG, width=1.5)),
            hovertemplate="<b>%{x}</b><br>E-Com Spot: $%{y:,}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            name="Traditional Contract",
            x=categories, y=trad_rates,
            mode="lines+markers",
            line=dict(color=C_ACCENT, width=2, dash="dash"),
            marker=dict(color=C_ACCENT, size=6, line=dict(color=C_BG, width=1.5)),
            hovertemplate="<b>%{x}</b><br>Trad Contract: $%{y:,}<extra></extra>",
        ))
        fig.update_layout(
            title=dict(text="Trans-Pac EB Rate: E-Com Spot vs Traditional ($USD/FEU)",
                       font=dict(color=C_TEXT2, size=11), x=0),
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            font=dict(color=C_TEXT, family="monospace"),
            height=310,
            margin=dict(l=50, r=20, t=40, b=40),
            legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(10,15,26,0.6)",
                        bordercolor=C_BORDER, borderwidth=1, x=0.01, y=0.99),
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=10),
                       gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(title=dict(text="$/FEU", font=dict(color=C_TEXT2, size=10)),
                       tickfont=dict(color=C_TEXT2, size=10),
                       gridcolor="rgba(255,255,255,0.05)",
                       tickprefix="$", tickformat=","),
        )
        st.plotly_chart(fig, use_container_width=True, key="ecommerce_rate_comparison_chart")


# ── Section 6: Major E-Commerce Player Impact ─────────────────────────────────

def _render_platform_volumes() -> None:
    logger.debug("Rendering major e-commerce player volume impact")
    _section_header(
        "Major E-Commerce Player Impact",
        "Alibaba / Temu / SHEIN cross-border volumes affecting specific shipping routes",
        icon="🌐",
    )

    # Volume bubble overview
    platforms = ["AMAZON", "ALIBABA", "SHEIN", "TEMU", "SHOPIFY", "WAYFAIR"]
    platform_labels = [_PLATFORM_LABELS[p] for p in platforms]
    volumes  = [_PLATFORM_VOLUMES[p] for p in platforms]
    colors   = [_PLATFORM_COLORS[p] for p in platforms]
    air_pcts = [_PLATFORM_AIR_PCT[p] for p in platforms]
    ocean_pcts = [100 - a for a in air_pcts]

    # Horizontal volume bar chart
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Volume (M parcels/yr)",
        x=volumes,
        y=platform_labels,
        orientation="h",
        marker_color=colors,
        marker_opacity=0.85,
        text=[f"{int(v)}M parcels/yr" for v in volumes],
        textposition="outside",
        textfont=dict(color=C_TEXT2, size=11),
        hovertemplate="<b>%{y}</b><br>%{x}M parcels/year<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, family="monospace"),
        height=300,
        margin=dict(l=80, r=120, t=20, b=40),
        xaxis=dict(
            title=dict(text="Million Parcels / Year", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.06)",
        ),
        yaxis=dict(tickfont=dict(color=C_TEXT, size=12)),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key="ecommerce_platform_volume_chart")

    # Platform deep dives in 3-column layout
    deep_dive_data = [
        {
            "platform": "ALIBABA",
            "headline": "Singles Day generates ~$140B GMV; pre-event ocean booking surge Aug-Sep drives 35-50% rate spike on Asia-Europe and Trans-Pac routes.",
            "key_routes": ["transpacific_eb", "asia_europe", "asia_latam"],
            "teu_impact": "+380K TEU",
            "teu_sub": "annual incremental demand",
            "policy": "Under scrutiny for circumventing US tariffs via multiple entities",
        },
        {
            "platform": "TEMU",
            "headline": "85% air mode share driven by de minimis strategy; ~400K packages/day to US. De minimis reform could redirect 120K+ TEU/yr to ocean.",
            "key_routes": ["transpacific_eb", "intra_asia_sea"],
            "teu_impact": "+120K TEU",
            "teu_sub": "potential ocean shift if de minimis closed",
            "policy": "Most exposed to US de minimis reform — no bonded US warehouse infrastructure",
        },
        {
            "platform": "SHEIN",
            "headline": "600K+ packages/day to US; growing Asia-Europe DTC model. EU customs reform targeting SHEIN's structure — similar exposure to Temu.",
            "key_routes": ["transpacific_eb", "asia_europe"],
            "teu_impact": "+80K TEU",
            "teu_sub": "potential ocean shift on de minimis closure",
            "policy": "EU Package Regulation targeting low-value parcel exemptions — dual regulatory risk",
        },
    ]

    dd_cols = st.columns(3)
    for i, dd in enumerate(deep_dive_data):
        plat  = dd["platform"]
        color = _PLATFORM_COLORS.get(plat, C_ACCENT)
        label = _PLATFORM_LABELS.get(plat, plat)
        routes_html = "".join(_route_pill(r) for r in dd["key_routes"])

        with dd_cols[i]:
            st.markdown(
                '<div style="background:' + color + '0a; border:1px solid ' + color
                + '33; border-radius:12px; padding:14px 16px; height:100%">'
                '<div style="display:flex; align-items:center; gap:10px; margin-bottom:10px">'
                + _platform_logo(plat, 34)
                + '<span style="font-size:0.95rem; font-weight:800; color:' + color + '">'
                + label + "</span></div>"
                '<div style="font-size:0.73rem; color:' + C_TEXT2 + '; line-height:1.5;'
                ' margin-bottom:10px">' + dd["headline"] + "</div>"
                '<div style="margin-bottom:10px">' + routes_html + "</div>"
                '<div style="background:' + color + '12; border-radius:8px; padding:10px;'
                ' text-align:center; margin-bottom:10px">'
                '<div style="font-size:1.3rem; font-weight:900; color:' + color + '">'
                + dd["teu_impact"] + "</div>"
                '<div style="font-size:0.65rem; color:' + C_TEXT3 + '">'
                + dd["teu_sub"] + "</div></div>"
                '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; font-style:italic;'
                ' border-top:1px solid ' + color + '22; padding-top:8px">'
                + dd["policy"] + "</div>"
                "</div>",
                unsafe_allow_html=True,
            )


# ── Section 7: Air vs Ocean Split ────────────────────────────────────────────

def _render_air_ocean_split() -> None:
    logger.debug("Rendering air vs ocean split + de minimis")
    _section_header(
        "Air vs. Ocean Split — When E-Commerce Goes by Air",
        "Mode choice drivers: speed, cost, de minimis threshold, product value, and route economics",
        icon="✈️",
    )

    col1, col2 = st.columns([1.2, 1])

    with col1:
        platforms = ["Amazon", "Alibaba", "SHEIN", "Temu", "Shopify", "Wayfair"]
        keys      = ["AMAZON", "ALIBABA", "SHEIN", "TEMU", "SHOPIFY", "WAYFAIR"]
        air_pcts  = [_PLATFORM_AIR_PCT[k] for k in keys]
        ocean_pcts = [100.0 - a for a in air_pcts]
        colors    = [_PLATFORM_COLORS[k] for k in keys]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Air %",
            x=platforms, y=air_pcts,
            marker_color=[_PLATFORM_COLORS[k] for k in keys],
            marker_opacity=0.9,
            text=[str(int(v)) + "%" for v in air_pcts],
            textposition="inside",
            textfont=dict(color="white", size=12, family="monospace"),
            hovertemplate="<b>%{x}</b><br>Air: %{y:.0f}%<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Ocean %",
            x=platforms, y=ocean_pcts,
            marker_color="rgba(255,255,255,0.08)",
            marker_line_color="rgba(255,255,255,0.18)",
            marker_line_width=1,
            text=[str(int(v)) + "%" for v in ocean_pcts],
            textposition="inside",
            textfont=dict(color=C_TEXT3, size=11, family="monospace"),
            hovertemplate="<b>%{x}</b><br>Ocean: %{y:.0f}%<extra></extra>",
        ))
        fig.add_annotation(
            x=2, y=92,
            text="SHEIN/TEMU:<br>de minimis driven",
            showarrow=True, arrowhead=2, arrowcolor=C_WARN, arrowwidth=1.5,
            ax=55, ay=-35,
            font=dict(color=C_WARN, size=10),
            bgcolor="rgba(10,15,26,0.88)",
            bordercolor=C_WARN, borderwidth=1, borderpad=5,
        )
        fig.update_layout(
            barmode="stack",
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            font=dict(color=C_TEXT, family="monospace"),
            height=340,
            margin=dict(l=40, r=20, t=20, b=40),
            legend=dict(font=dict(color=C_TEXT2, size=11), bgcolor="rgba(10,15,26,0.6)",
                        bordercolor=C_BORDER, borderwidth=1, x=0.01, y=0.99),
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=11),
                       gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(title=dict(text="Share (%)", font=dict(color=C_TEXT2, size=11)),
                       tickfont=dict(color=C_TEXT2, size=11),
                       gridcolor="rgba(255,255,255,0.06)",
                       ticksuffix="%", range=[0, 112]),
        )
        st.plotly_chart(fig, use_container_width=True, key="ecommerce_mode_split_chart")

    with col2:
        # De minimis explainer + mode decision tree
        st.markdown(
            _card(
                '<div style="font-size:0.67rem; text-transform:uppercase; letter-spacing:0.09em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">DE MINIMIS — $800 THRESHOLD</div>'
                '<div style="font-size:2.0rem; font-weight:900; color:' + C_ACCENT
                + '; line-height:1; margin-bottom:6px">$800</div>'
                '<div style="font-size:0.78rem; font-weight:700; color:' + C_TEXT
                + '; margin-bottom:10px">Duty-Free per Package (Section 321)</div>'
                '<div style="font-size:0.73rem; color:' + C_TEXT2 + '; line-height:1.55;'
                ' margin-bottom:12px">'
                "Packages under $800 enter the US duty-free. SHEIN and Temu ship each individual "
                "order as a separate air parcel from China, avoiding 7.5–145% tariffs. "
                "~1 billion de minimis packages entered the US in 2024; 70%+ from China."
                "</div>"
                + _badge("HIGH POLICY RISK", C_DANGER)
                + '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-top:10px;'
                ' line-height:1.5">'
                "Bipartisan legislation (STOP Act) proposes eliminating de minimis for Chinese goods. "
                "If passed: 10-15% of SHEIN/TEMU volume shifts to ocean containers = "
                "<b style='color:" + C_HIGH + "'>+200K TEU/yr</b> incremental Trans-Pac EB demand, "
                "<b style='color:" + C_HIGH + "'>+8-12%</b> rate uplift."
                "</div>",
                border_color=C_ACCENT + "55",
            ),
            unsafe_allow_html=True,
        )

        # Mode decision criteria
        criteria = [
            ("Product value/weight", "High value → Air preferred",    "Low value → Ocean wins"),
            ("Transit time required", "< 2 weeks → Air",              "2-6 weeks → Ocean"),
            ("Order size",           "Single item (DTC) → Air",       "Bulk (B2B) → Ocean FCL"),
            ("Tariff exposure",      "Under $800 → Air (de minimis)", "Over $800 → Ocean + duty"),
            ("Carbon targets",       "Green goals → Ocean preferred", "Speed priority → Air"),
        ]
        rows = ""
        for criterion, air_case, ocean_case in criteria:
            rows += (
                '<div style="display:grid; grid-template-columns:0.9fr 1fr 1fr; gap:6px;'
                ' padding:7px 10px; border-bottom:1px solid ' + C_BORDER + '; font-size:0.70rem">'
                '<span style="color:' + C_TEXT3 + '; font-weight:600">' + criterion + "</span>"
                '<span style="color:' + C_CYAN + '">' + air_case + "</span>"
                '<span style="color:' + C_ACCENT + '">' + ocean_case + "</span>"
                "</div>"
            )
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER
            + '; border-radius:12px; overflow:hidden; margin-top:8px">'
            '<div style="display:grid; grid-template-columns:0.9fr 1fr 1fr; gap:6px;'
            ' padding:8px 10px; background:rgba(255,255,255,0.04)">'
            '<span style="font-size:0.63rem; text-transform:uppercase; letter-spacing:0.07em;'
            ' color:' + C_TEXT3 + '">Factor</span>'
            '<span style="font-size:0.63rem; text-transform:uppercase; letter-spacing:0.07em;'
            ' color:' + C_CYAN + '">Air Case</span>'
            '<span style="font-size:0.63rem; text-transform:uppercase; letter-spacing:0.07em;'
            ' color:' + C_ACCENT + '">Ocean Case</span>'
            "</div>"
            + rows + "</div>",
            unsafe_allow_html=True,
        )


# ── Section 8: Last-Mile Port Congestion ─────────────────────────────────────

def _render_last_mile_congestion() -> None:
    logger.debug("Rendering last-mile port congestion analysis")
    _section_header(
        "Last-Mile Impact on Port Congestion",
        "E-commerce parcel delivery creating inland distribution congestion at key gateways",
        icon="🚛",
    )

    col1, col2 = st.columns([1.2, 1])

    with col1:
        # Port congestion bubble chart
        port_names  = [p["port"] for p in _PORT_CONGESTION]
        parcel_pcts = [p["parcel_pct"] for p in _PORT_CONGESTION]
        dwell_days  = [p["dwell_days"] for p in _PORT_CONGESTION]
        cong_idxes  = [p["congestion_idx"] for p in _PORT_CONGESTION]
        routes      = [p["route"] for p in _PORT_CONGESTION]

        bubble_colors = [_demand_color(c) for c in cong_idxes]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=parcel_pcts,
            y=dwell_days,
            mode="markers+text",
            marker=dict(
                size=[c * 40 for c in cong_idxes],
                color=bubble_colors,
                opacity=0.75,
                line=dict(color="rgba(255,255,255,0.25)", width=1.5),
            ),
            text=["<b>" + p.split("/")[0].strip()[:12] + "</b>" for p in port_names],
            textposition="top center",
            textfont=dict(color=C_TEXT, size=10),
            customdata=list(zip(port_names, cong_idxes, routes)),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "E-com parcel share: %{x}%<br>"
                "Avg container dwell: %{y} days<br>"
                "Congestion index: %{customdata[1]}x<br>"
                "Route: %{customdata[2]}<extra></extra>"
            ),
        ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            font=dict(color=C_TEXT, family="monospace"),
            height=340,
            margin=dict(l=60, r=20, t=20, b=50),
            xaxis=dict(
                title=dict(text="E-Commerce Parcel Share of Port Volume (%)",
                           font=dict(color=C_TEXT2, size=11)),
                tickfont=dict(color=C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.06)",
                ticksuffix="%",
            ),
            yaxis=dict(
                title=dict(text="Avg Container Dwell Time (days)",
                           font=dict(color=C_TEXT2, size=11)),
                tickfont=dict(color=C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.06)",
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="ecommerce_congestion_bubble")

    with col2:
        # Congestion index cards per port
        st.markdown(
            '<div style="font-size:0.70rem; text-transform:uppercase; letter-spacing:0.08em;'
            ' color:' + C_TEXT3 + '; margin-bottom:10px">PORT CONGESTION INDEX (1.0 = baseline)</div>',
            unsafe_allow_html=True,
        )
        for p in sorted(_PORT_CONGESTION, key=lambda x: x["congestion_idx"], reverse=True):
            color = _demand_color(p["congestion_idx"])
            bar_w = min(100, int((p["congestion_idx"] - 1.0) / 0.5 * 100))
            st.markdown(
                '<div style="padding:9px 12px; margin-bottom:6px; background:rgba(26,34,53,0.5);'
                ' border:1px solid ' + C_BORDER + '; border-radius:8px">'
                '<div style="display:flex; justify-content:space-between; margin-bottom:5px">'
                '<span style="font-size:0.78rem; font-weight:700; color:' + C_TEXT + '">'
                + p["port"] + "</span>"
                '<span style="font-size:0.82rem; font-weight:900; color:' + color + '">'
                + str(p["congestion_idx"]) + "x</span></div>"
                '<div style="display:flex; gap:10px; margin-bottom:6px">'
                + _badge(str(p["parcel_pct"]) + "% e-com", C_CYAN)
                + _badge(str(p["dwell_days"]) + "d dwell", C_WARN)
                + "</div>"
                '<div style="height:4px; background:rgba(255,255,255,0.07); border-radius:2px;'
                ' overflow:hidden">'
                '<div style="height:100%; width:' + str(bar_w) + '%; background:' + color
                + '; border-radius:2px"></div></div>'
                "</div>",
                unsafe_allow_html=True,
            )

        st.markdown(
            '<div style="font-size:0.72rem; color:' + C_TEXT3 + '; margin-top:4px;'
            ' line-height:1.5">'
            "Bubble size = congestion index magnitude. High e-commerce parcel share correlates "
            "with extended container dwell times as inland distribution networks saturate "
            "during peak parcel periods (Q4, Prime Day)."
            "</div>",
            unsafe_allow_html=True,
        )


# ── Section 9: Return Flows Analysis ──────────────────────────────────────────

def _render_return_flows() -> None:
    logger.debug("Rendering return flows analysis")
    _section_header(
        "Return Flows Analysis",
        "Reverse logistics creating unique eastbound Asia shipping patterns and rate dynamics",
        icon="↩️",
    )

    col1, col2 = st.columns([1, 1.1])

    with col1:
        categories   = [r["category"] for r in _RETURN_FLOWS]
        return_rates = [r["return_rate"] for r in _RETURN_FLOWS]
        teu_ests     = [r["reverse_teu_est"] / 1_000 for r in _RETURN_FLOWS]  # in thousands
        modes        = [r["mode"] for r in _RETURN_FLOWS]
        mode_colors  = {
            "Ocean": C_ACCENT,
            "Air":   C_CYAN,
            "Mixed": C_PURPLE,
        }
        bar_colors = [mode_colors.get(m, C_TEXT3) for m in modes]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Return Rate (%)",
            x=return_rates,
            y=categories,
            orientation="h",
            marker_color=bar_colors,
            marker_opacity=0.85,
            text=[f"{r}%" for r in return_rates],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=10),
            hovertemplate=(
                "<b>%{y}</b><br>Return rate: %{x}%<extra></extra>"
            ),
        ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            font=dict(color=C_TEXT, family="monospace"),
            height=310,
            margin=dict(l=130, r=60, t=20, b=40),
            xaxis=dict(
                title=dict(text="Return Rate (%)", font=dict(color=C_TEXT2, size=11)),
                tickfont=dict(color=C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.06)",
                ticksuffix="%",
            ),
            yaxis=dict(tickfont=dict(color=C_TEXT, size=11)),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="ecommerce_return_rate_chart")

        # Mode legend
        st.markdown(
            '<div style="display:flex; gap:12px; flex-wrap:wrap">'
            + "".join(
                '<div style="display:flex; align-items:center; gap:5px">'
                '<div style="width:10px; height:10px; border-radius:2px; background:' + c + '"></div>'
                '<span style="font-size:0.70rem; color:' + C_TEXT2 + '">' + m + "</span></div>"
                for m, c in mode_colors.items()
            )
            + "</div>",
            unsafe_allow_html=True,
        )

    with col2:
        # TEU impact table + narrative
        total_reverse_teu = sum(r["reverse_teu_est"] for r in _RETURN_FLOWS)
        st.markdown(
            _card(
                '<div style="font-size:0.67rem; text-transform:uppercase; letter-spacing:0.09em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">TOTAL REVERSE LOGISTICS VOLUME</div>'
                '<div style="font-size:2.0rem; font-weight:900; color:' + C_PURPLE
                + '; line-height:1; margin-bottom:4px">'
                + f"{total_reverse_teu:,}" + "</div>"
                '<div style="font-size:0.75rem; color:' + C_TEXT2
                + '; margin-bottom:14px">estimated TEU/year (US→Asia returns)</div>'
                '<div style="font-size:0.73rem; color:' + C_TEXT2 + '; line-height:1.55">'
                "E-commerce return rates of 15-38% — vs 2-8% for traditional retail — generate "
                "substantial <b style='color:" + C_TEXT + "'>westbound (US→Asia) cargo flows</b> "
                "that partially backfill the structural imbalance on Trans-Pac westbound lanes. "
                "Apparel (38%) and shoes (35%) are highest-return categories, driven by fit/style mismatches "
                "in online-only purchasing."
                "</div>",
                border_color=C_PURPLE + "55",
            ),
            unsafe_allow_html=True,
        )

        # Detailed return table
        st.markdown(
            '<div style="font-size:0.70rem; text-transform:uppercase; letter-spacing:0.08em;'
            ' color:' + C_TEXT3 + '; margin-top:10px; margin-bottom:8px">'
            "Category Breakdown</div>",
            unsafe_allow_html=True,
        )
        for r in sorted(_RETURN_FLOWS, key=lambda x: x["return_rate"], reverse=True):
            mode_color = {"Ocean": C_ACCENT, "Air": C_CYAN, "Mixed": C_PURPLE}.get(r["mode"], C_TEXT3)
            teu_k = r["reverse_teu_est"] // 1_000
            teu_r = r["reverse_teu_est"] % 1_000 // 100
            teu_str = f"{teu_k},{teu_r}00K TEU/yr" if teu_r else f"{teu_k}K TEU/yr"
            bar_w = int((r["return_rate"] / 40) * 100)
            st.markdown(
                '<div style="padding:8px 10px; margin-bottom:5px; background:rgba(26,34,53,0.4);'
                ' border:1px solid ' + C_BORDER + '; border-radius:8px">'
                '<div style="display:flex; justify-content:space-between; margin-bottom:4px">'
                '<span style="font-size:0.76rem; font-weight:700; color:' + C_TEXT + '">'
                + r["category"] + "</span>"
                + _badge(r["mode"], mode_color)
                + "</div>"
                '<div style="display:flex; gap:8px; margin-bottom:5px">'
                + _badge(str(r["return_rate"]) + "% returns", C_DANGER)
                + _badge(teu_str, C_PURPLE)
                + "</div>"
                '<div style="height:3px; background:rgba(255,255,255,0.07); border-radius:2px;'
                ' overflow:hidden">'
                '<div style="height:100%; width:' + str(bar_w) + '%; background:' + C_PURPLE
                + '88; border-radius:2px"></div></div>'
                "</div>",
                unsafe_allow_html=True,
            )


# ── Section 10: 90-Day Forecast ────────────────────────────────────────────────

def _render_90day_forecast() -> None:
    logger.debug("Rendering 90-day trans-Pacific demand forecast chart")
    _section_header(
        "Forecast: Next 90 Days — Trans-Pacific Demand",
        "Weekly predicted demand index based on retail calendar + e-commerce seasonal patterns",
        icon="📈",
    )

    today  = date.today()
    weeks  = 13
    dates  = [today + timedelta(weeks=i) for i in range(weeks)]
    labels = [d.strftime("%b %d") for d in dates]

    demand_vals: list[float] = []
    for d in dates:
        m    = d.month
        base = _TP_MONTHLY.get(m, 1.0)
        boost = 0.0
        for cal in (RETAIL_CALENDAR or []):
            for yr_off in (0, 1):
                try:
                    ev_date = date(d.year + yr_off, cal.month, cal.day)
                except ValueError:
                    ev_date = date(d.year + yr_off, cal.month, 28)
                bw_start = ev_date - timedelta(weeks=cal.typical_order_window_weeks_before)
                bw_end   = ev_date - timedelta(weeks=max(0, cal.typical_order_window_weeks_before - 4))
                if bw_start <= d <= bw_end:
                    boost = max(boost, (cal.container_demand_multiplier - 1.0) * 0.5)
                    break
        demand_vals.append(round(base + boost, 3))

    peak_weeks = [(labels[i], demand_vals[i]) for i in range(len(demand_vals)) if demand_vals[i] >= 1.35]
    high_weeks = [(labels[i], demand_vals[i]) for i in range(len(demand_vals)) if 1.15 <= demand_vals[i] < 1.35]

    if peak_weeks:
        peak_list = ", ".join(lbl for lbl, _ in peak_weeks)
        st.warning(
            f"**PEAK DEMAND** weeks in the next 90 days — book containers immediately: "
            f"{peak_list}. Demand index ≥ 1.35x baseline; space is constrained."
        )
    elif high_weeks:
        high_list = ", ".join(lbl for lbl, _ in high_weeks)
        st.warning(
            f"**HIGH DEMAND** weeks approaching — book within 2 weeks: "
            f"{high_list}. Demand index 1.15–1.35x baseline."
        )

    fig = go.Figure()

    # Urgency bands
    for y0, y1, fc, lbl, fnt_color in [
        (1.35, 1.60, "rgba(239,68,68,0.07)",  "PEAK", C_DANGER),
        (1.15, 1.35, "rgba(249,115,22,0.06)", "HIGH", C_ORANGE),
        (0.95, 1.15, "rgba(245,158,11,0.05)", "MOD",  C_WARN),
        (0.60, 0.95, "rgba(16,185,129,0.04)", "LOW",  C_HIGH),
    ]:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=fc, line_width=0,
                      annotation_text=lbl, annotation_position="right",
                      annotation_font=dict(color=fnt_color, size=10))

    fig.add_hline(y=1.0, line_dash="dot", line_color="rgba(255,255,255,0.20)",
                  line_width=1, annotation_text="Baseline",
                  annotation_font=dict(color=C_TEXT3, size=10),
                  annotation_position="right")

    # Fill area
    fig.add_trace(go.Scatter(
        x=labels, y=demand_vals,
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.07)",
        line=dict(color="transparent"),
        showlegend=False, hoverinfo="skip",
    ))

    # Main line
    fig.add_trace(go.Scatter(
        x=labels, y=demand_vals,
        mode="lines+markers",
        name="Demand Index",
        line=dict(color=C_ACCENT, width=2.5),
        marker=dict(
            color=[_demand_color(v) for v in demand_vals],
            size=9,
            line=dict(color=C_BG, width=1.8),
        ),
        hovertemplate="<b>%{x}</b><br>Demand Index: %{y:.3f}x<extra></extra>",
    ))

    # Event annotations
    for cal in (RETAIL_CALENDAR or []):
        for yr_off in (0, 1):
            try:
                ev_date = date(today.year + yr_off, cal.month, cal.day)
            except ValueError:
                ev_date = date(today.year + yr_off, cal.month, 28)
            if today <= ev_date <= dates[-1]:
                ev_label = ev_date.strftime("%b %d")
                if ev_label in labels:
                    idx_pos = labels.index(ev_label)
                    fig.add_annotation(
                        x=ev_label, y=demand_vals[idx_pos] + 0.05,
                        text=cal.event_name[:12],
                        showarrow=True, arrowhead=1,
                        arrowcolor=C_WARN, arrowwidth=1,
                        font=dict(color=C_WARN, size=9),
                        ax=0, ay=-30,
                    )

    fig.add_vline(
        x=labels[0], line_dash="dash",
        line_color=C_HIGH, line_width=1.5,
        annotation_text="Today",
        annotation_font=dict(color=C_HIGH, size=10),
        annotation_position="top left",
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, family="monospace"),
        height=400,
        margin=dict(l=50, r=90, t=20, b=50),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.04)",
            tickangle=-30,
        ),
        yaxis=dict(
            title=dict(text="Demand Index (1.0 = baseline)", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(255,255,255,0.06)",
            range=[0.50, 1.65],
        ),
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True, key="ecommerce_90day_forecast_chart")

    # Legend + export
    st.markdown(
        '<div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:-4px">'
        + "".join(
            '<div style="display:flex; align-items:center; gap:5px">'
            '<div style="width:11px; height:11px; border-radius:3px; background:'
            + col + '; opacity:0.8"></div>'
            '<span style="font-size:0.70rem; color:' + C_TEXT2 + '">' + lbl + "</span></div>"
            for col, lbl in [
                (C_DANGER, "Peak — book immediately"),
                (C_ORANGE, "High — book within 2 wks"),
                (C_WARN,   "Moderate — monitor closely"),
                (C_HIGH,   "Low — normal procurement"),
            ]
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    def _forecast_csv() -> bytes:
        buf = io.StringIO()
        w = _csv_mod.writer(buf)
        w.writerow(["Week Starting", "Demand Index", "Zone"])
        for lbl, val in zip(labels, demand_vals):
            if val >= 1.35:
                zone = "PEAK"
            elif val >= 1.15:
                zone = "HIGH"
            elif val >= 1.00:
                zone = "MODERATE"
            else:
                zone = "LOW"
            w.writerow([lbl, val, zone])
        return buf.getvalue().encode()

    st.download_button(
        label="Download 90-day forecast CSV",
        data=_forecast_csv(),
        file_name="ecommerce_90day_demand_forecast.csv",
        mime="text/csv",
        key="dl_90day_forecast",
    )


# ── Public render entry point ──────────────────────────────────────────────────

def render(
    trade_data: dict | None = None,
    freight_data: dict | None = None,
    macro_data: dict | None = None,
    route_results: dict | None = None,
) -> None:
    """Render the E-Commerce Impact tab for the shipping intelligence platform.

    Args:
        trade_data:    Trade volume and e-commerce share data.
        freight_data:  Current freight rate data across routes.
        macro_data:    Macroeconomic context (GDP, consumer spending, etc.).
        route_results: Route analysis output from the main engine.
    """
    logger.info("Rendering tab_ecommerce")

    try:
        # ── Enhanced overview (new sections) ────────────────────────────────────
        _render_enhanced_ecommerce_overview()

    except Exception as _enh_exc:
        logger.exception("tab_ecommerce enhanced overview error: %s", _enh_exc)
        st.warning("Enhanced overview sections encountered an error — continuing with standard view.")

    try:
        # ── Hero overview ───────────────────────────────────────────────────────
        _render_hero(trade_data, freight_data)

        _divider()

        # ── Section 1: E-Commerce Demand Pulse ─────────────────────────────────
        _render_platform_cards()

        _divider()

        # ── Section 2: Peak Season Calendar ────────────────────────────────────
        _render_peak_calendar()

        _divider()

        # ── Section 3: Booking Window Alerts ───────────────────────────────────
        _render_booking_alerts()

        _divider()

        # ── Section 4: DTC Shipping Growth ─────────────────────────────────────
        _render_dtc_growth()

        _divider()

        # ── Section 5: E-Commerce vs Traditional ───────────────────────────────
        _render_ecom_vs_traditional()

        _divider()

        # ── Section 6: Major Platform Volumes ──────────────────────────────────
        _render_platform_volumes()

        _divider()

        # ── Section 7: Air vs Ocean Split ──────────────────────────────────────
        _render_air_ocean_split()

        _divider()

        # ── Section 8: Last-Mile Congestion ────────────────────────────────────
        _render_last_mile_congestion()

        _divider()

        # ── Section 9: Return Flows ─────────────────────────────────────────────
        _render_return_flows()

        _divider()

        # ── Section 10: 90-Day Forecast ─────────────────────────────────────────
        _render_90day_forecast()

    except Exception as exc:
        logger.exception("tab_ecommerce render error: %s", exc)
        st.error(f"E-Commerce tab encountered a rendering error: {exc}")

    logger.info("tab_ecommerce render complete")
