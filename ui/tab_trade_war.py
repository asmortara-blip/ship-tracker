"""tab_trade_war.py — Trade War Impact Intelligence Dashboard.

A comprehensive interactive UI covering:
  1.  Trade war status dashboard — active conflicts, affected trade value, escalation level
  2.  Global tariff heat map (choropleth)
  3.  Tariff impact matrix — countries vs. goods, colored by severity
  4.  Tariff scenario builder with live impact preview
  5.  Trade route diversion map (Sankey + Scattergeo)
  6.  Supply chain reshoring tracker
  7.  Affected sectors scorecard — electronics, agriculture, automotive, steel, chemicals
  8.  Trade volume charts — before/after tariff comparisons
  9.  Currency impact — how trade war affects relevant FX rates
  10. Winner/loser analysis — which routes/ports benefit vs. suffer
  11. Historical tariff event timeline
"""
from __future__ import annotations

import csv
import io

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.tariff_analyzer import (
    ROUTE_TARIFF_EXPOSURE,
    analyze_tariff_sensitivity,
)

# ── Color palette ──────────────────────────────────────────────────────────────
C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_CARD2  = "#141d2e"
C_BORDER = "rgba(255,255,255,0.08)"
C_BORDER2= "rgba(255,255,255,0.12)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"
C_PINK   = "#ec4899"
C_LIME   = "#84cc16"


# ── Active conflict data ───────────────────────────────────────────────────────
_ACTIVE_CONFLICTS: list[dict] = [
    {
        "pair": "US \u2194 China",
        "tariff_rate": "145%",
        "trade_value_bn": 582,
        "status": "CRITICAL",
        "status_color": C_DANGER,
        "since": "Apr 2025",
        "type": "Bilateral",
    },
    {
        "pair": "US \u2194 EU",
        "tariff_rate": "10\u201325%",
        "trade_value_bn": 810,
        "status": "ELEVATED",
        "status_color": C_WARN,
        "since": "Jan 2025",
        "type": "Broad-based",
    },
    {
        "pair": "US \u2194 Canada/Mexico",
        "tariff_rate": "25%",
        "trade_value_bn": 1100,
        "status": "ELEVATED",
        "status_color": C_WARN,
        "since": "Feb 2025",
        "type": "IEEPA",
    },
    {
        "pair": "US \u2194 Japan",
        "tariff_rate": "25% (autos)",
        "trade_value_bn": 215,
        "status": "MODERATE",
        "status_color": C_ACCENT,
        "since": "Mar 2025",
        "type": "Sectoral",
    },
]

# ── Sector impact data ─────────────────────────────────────────────────────────
_SECTOR_IMPACTS: list[dict] = [
    {
        "sector": "Electronics",
        "icon": "\u26a1",
        "impact_score": 9.2,
        "tariff_rate": "145%",
        "trade_vol_before": 420,
        "trade_vol_after": 195,
        "shipping_impact": -52,
        "reshoring_index": 78,
        "key_routes": ["Trans-Pacific EB", "SEA-Transpacific"],
        "description": "Consumer electronics, semiconductors, PCBs \u2014 hardest hit sector",
        "color": C_DANGER,
    },
    {
        "sector": "Agriculture",
        "icon": "\U0001f33e",
        "impact_score": 7.8,
        "tariff_rate": "34\u201380%",
        "trade_vol_before": 28,
        "trade_vol_after": 12,
        "shipping_impact": -57,
        "reshoring_index": 25,
        "key_routes": ["Trans-Pacific WB", "US Gulf-Asia"],
        "description": "Soybeans, pork, grains \u2014 retaliatory Chinese tariffs crushing US agri exports",
        "color": C_WARN,
    },
    {
        "sector": "Automotive",
        "icon": "\U0001f697",
        "impact_score": 7.1,
        "tariff_rate": "25%",
        "trade_vol_before": 185,
        "trade_vol_after": 130,
        "shipping_impact": -30,
        "reshoring_index": 62,
        "key_routes": ["Transatlantic", "Trans-Pacific EB"],
        "description": "Vehicles, EV batteries, auto parts \u2014 25% global tariff threatens supply chains",
        "color": C_WARN,
    },
    {
        "sector": "Steel & Metals",
        "icon": "\u2699\ufe0f",
        "impact_score": 6.5,
        "tariff_rate": "25%",
        "trade_vol_before": 145,
        "trade_vol_after": 108,
        "shipping_impact": -26,
        "reshoring_index": 55,
        "key_routes": ["Asia-Europe", "Trans-Pacific EB"],
        "description": "Steel, aluminum, rare earths \u2014 Section 232 and retaliatory countermeasures",
        "color": C_ACCENT,
    },
    {
        "sector": "Chemicals",
        "icon": "\U0001f9ea",
        "impact_score": 5.4,
        "tariff_rate": "10\u201325%",
        "trade_vol_before": 112,
        "trade_vol_after": 92,
        "shipping_impact": -18,
        "reshoring_index": 40,
        "key_routes": ["Asia-Europe", "Transatlantic"],
        "description": "Specialty chemicals, polymers \u2014 moderate but growing tariff exposure",
        "color": C_LIME,
    },
]

# ── Currency impact data ───────────────────────────────────────────────────────
_CURRENCY_IMPACTS: list[dict] = [
    {"pair": "USD/CNY", "before": 7.10,  "after": 7.42,  "change_pct": +4.5,  "direction": "CNY weakened", "color": C_DANGER, "note": "Yuan under pressure from tariff shock"},
    {"pair": "USD/JPY", "before": 148,   "after": 155,   "change_pct": +4.7,  "direction": "JPY weakened", "color": C_WARN,   "note": "Safe-haven demand vs. export headwinds"},
    {"pair": "USD/EUR", "before": 1.09,  "after": 1.05,  "change_pct": -3.7,  "direction": "EUR weakened", "color": C_WARN,   "note": "EU growth fears from US tariff threat"},
    {"pair": "USD/MXN", "before": 17.1,  "after": 18.9,  "change_pct": +10.5, "direction": "MXN weakened", "color": C_DANGER, "note": "25% IEEPA tariff shock on peso"},
    {"pair": "USD/KRW", "before": 1320,  "after": 1380,  "change_pct": +4.5,  "direction": "KRW weakened", "color": C_ACCENT, "note": "South Korea export economy pressure"},
    {"pair": "USD/VND", "before": 24500, "after": 25100, "change_pct": +2.4,  "direction": "VND weakened", "color": C_HIGH,   "note": "Vietnam: trade diversion beneficiary"},
]

# ── Winner / Loser port & route data ──────────────────────────────────────────
_WINNERS: list[dict] = [
    {"name": "Port of Ho Chi Minh City", "type": "Port",  "gain_pct": 42, "reason": "Primary China bypass \u2014 electronics/garments",       "color": C_HIGH},
    {"name": "Port of Manzanillo (MX)",  "type": "Port",  "gain_pct": 31, "reason": "USMCA nearshoring gateway \u2014 auto parts",             "color": C_HIGH},
    {"name": "SEA-Transpacific EB",      "type": "Route", "gain_pct": 28, "reason": "Vietnam/Thailand origin absorbing China share",            "color": C_HIGH},
    {"name": "Port of Chennai",          "type": "Port",  "gain_pct": 19, "reason": "India China+1 \u2014 pharma, IT hardware, textiles",       "color": C_HIGH},
    {"name": "Gulf of Mexico Ports",     "type": "Port",  "gain_pct": 16, "reason": "Mexico nearshoring agri/industrial flows",                 "color": C_HIGH},
    {"name": "Port of Sihanoukville",    "type": "Port",  "gain_pct": 14, "reason": "Cambodia garments/electronics diversion",                  "color": C_HIGH},
]

_LOSERS: list[dict] = [
    {"name": "Port of Shenzhen",          "type": "Port",  "loss_pct": -38, "reason": "Electronics exports collapse under 145% tariff",          "color": C_DANGER},
    {"name": "Port of Shanghai",          "type": "Port",  "loss_pct": -29, "reason": "Broad container throughput decline \u2014 US trade",      "color": C_DANGER},
    {"name": "Trans-Pacific EB (direct)", "type": "Route", "loss_pct": -31, "reason": "Direct China-US lane volume halved",                      "color": C_DANGER},
    {"name": "Port of Los Angeles",       "type": "Port",  "loss_pct": -22, "reason": "Import volumes declining as China goods diverted",         "color": C_WARN},
    {"name": "Port of Long Beach",        "type": "Port",  "loss_pct": -20, "reason": "Parallel LA decline \u2014 import substitution effect",   "color": C_WARN},
    {"name": "Trans-Pacific WB (US agri)","type": "Route", "loss_pct": -57, "reason": "US agri exports blocked by China retaliation",            "color": C_DANGER},
]

# ── Hardcoded 2025-2026 tariff risk data (25+ countries) ──────────────────────
# tariff_risk: 0 = low risk, 1 = high risk
_COUNTRY_TARIFF_RISK: list[dict] = [
    {"iso": "USA", "name": "United States",      "tariff_risk": 0.85, "note": "Major tariff imposer — Section 301, reciprocal tariffs"},
    {"iso": "CHN", "name": "China",              "tariff_risk": 0.90, "note": "Primary tariff target — 145% US tariffs in force"},
    {"iso": "DEU", "name": "Germany",            "tariff_risk": 0.40, "note": "EU member — moderate exposure via US-EU 10% baseline"},
    {"iso": "FRA", "name": "France",             "tariff_risk": 0.38, "note": "EU member — auto/luxury goods tariff risk"},
    {"iso": "GBR", "name": "United Kingdom",     "tariff_risk": 0.35, "note": "Post-Brexit — negotiating bilateral deal with US"},
    {"iso": "JPN", "name": "Japan",              "tariff_risk": 0.55, "note": "25% auto tariffs; ongoing bilateral negotiations"},
    {"iso": "KOR", "name": "South Korea",        "tariff_risk": 0.50, "note": "Steel/auto tariffs; KORUS under review"},
    {"iso": "VNM", "name": "Vietnam",            "tariff_risk": 0.60, "note": "Trade diversion hub — elevated US scrutiny"},
    {"iso": "MEX", "name": "Mexico",             "tariff_risk": 0.55, "note": "25% IEEPA tariffs — nearshoring destination"},
    {"iso": "CAN", "name": "Canada",             "tariff_risk": 0.50, "note": "25% tariffs outside USMCA — CUSMA review"},
    {"iso": "IND", "name": "India",              "tariff_risk": 0.30, "note": "Beneficiary of China+1 — low direct exposure"},
    {"iso": "BRA", "name": "Brazil",             "tariff_risk": 0.25, "note": "Limited direct US-China tariff impact"},
    {"iso": "AUS", "name": "Australia",          "tariff_risk": 0.20, "note": "Allied nation — minimal tariff risk"},
    {"iso": "IDN", "name": "Indonesia",          "tariff_risk": 0.35, "note": "Moderate — some transshipment exposure"},
    {"iso": "THA", "name": "Thailand",           "tariff_risk": 0.45, "note": "Regional manufacturing — moderate US scrutiny"},
    {"iso": "MYS", "name": "Malaysia",           "tariff_risk": 0.42, "note": "Semiconductor hub — elevated trade war risk"},
    {"iso": "BGD", "name": "Bangladesh",         "tariff_risk": 0.20, "note": "Apparel exporter — GSP beneficiary"},
    {"iso": "SAU", "name": "Saudi Arabia",       "tariff_risk": 0.18, "note": "Energy corridor — low manufactured goods exposure"},
    {"iso": "ARE", "name": "UAE",                "tariff_risk": 0.22, "note": "Re-export hub — some transshipment risk"},
    {"iso": "SGP", "name": "Singapore",          "tariff_risk": 0.28, "note": "Transshipment hub — indirect exposure"},
    {"iso": "ZAF", "name": "South Africa",       "tariff_risk": 0.18, "note": "Commodities focus — low manufactured goods risk"},
    {"iso": "MAR", "name": "Morocco",            "tariff_risk": 0.15, "note": "EU-adjacent nearshoring — low US tariff risk"},
    {"iso": "POL", "name": "Poland",             "tariff_risk": 0.35, "note": "EU eastern manufacturing hub — moderate exposure"},
    {"iso": "TUR", "name": "Turkey",             "tariff_risk": 0.30, "note": "Steel tariffs — moderate bilateral exposure"},
    {"iso": "RUS", "name": "Russia",             "tariff_risk": 0.10, "note": "Sanctioned — de facto excluded from major trade flows"},
]


# ── Shared HTML helpers ────────────────────────────────────────────────────────

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        '<div style="color:' + C_TEXT2 + '; font-size:0.83rem; margin-top:4px; line-height:1.45">'
        + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        '<div style="margin-bottom:18px; margin-top:10px">'
        '<div style="display:flex; align-items:center; gap:10px">'
        '<div style="width:3px; height:22px; background:linear-gradient(180deg,'
        + C_ACCENT + ',' + C_PURPLE + '); border-radius:2px"></div>'
        '<div style="font-size:1.08rem; font-weight:800; color:' + C_TEXT + '; letter-spacing:-0.01em">'
        + text + "</div></div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _badge(label: str, color: str, size: str = "0.68rem") -> str:
    return (
        '<span style="font-size:' + size + '; font-weight:700; color:' + color + ';'
        ' background:' + color + '1a; border:1px solid ' + color + '44;'
        ' border-radius:4px; padding:2px 8px; letter-spacing:0.06em">'
        + label + "</span>"
    )


def _progress_bar(pct: float, color: str, height: str = "5px") -> str:
    w = min(100, max(0, pct))
    return (
        '<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:' + height + '; overflow:hidden">'
        '<div style="width:' + str(w) + '%; height:100%; background:' + color + '; border-radius:4px;'
        ' transition:width 0.4s ease"></div>'
        "</div>"
    )


def _metric_pill(label: str, value: str, color: str) -> str:
    return (
        '<div style="background:rgba(0,0,0,0.25); border:1px solid '
        + color + '44; border-radius:8px; padding:10px 14px; margin-bottom:8px">'
        '<div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.09em; color:'
        + C_TEXT3 + '; margin-bottom:4px">' + label + "</div>"
        '<div style="font-size:1.18rem; font-weight:800; color:' + color + '">' + value + "</div>"
        "</div>"
    )


def _divider(margin: str = "28px 0") -> None:
    st.markdown(
        '<div style="margin:' + margin + '; height:1px; background:linear-gradient(90deg,'
        'transparent, rgba(255,255,255,0.08), transparent)"></div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Alert Banner — Trade War Status Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def _render_alert_banner() -> None:
    """Top-of-page alert banner: active conflicts count, affected trade value, tariff escalation level."""
    total_trade_bn = sum(c["trade_value_bn"] for c in _ACTIVE_CONFLICTS)
    critical_count = sum(1 for c in _ACTIVE_CONFLICTS if c["status"] == "CRITICAL")
    elevated_count = sum(1 for c in _ACTIVE_CONFLICTS if c["status"] == "ELEVATED")
    escalation_level = "CRITICAL" if critical_count >= 1 else "ELEVATED"
    banner_color = C_DANGER if escalation_level == "CRITICAL" else C_WARN
    banner_bg = "rgba(239,68,68,0.10)" if escalation_level == "CRITICAL" else "rgba(245,158,11,0.10)"
    banner_border = "rgba(239,68,68,0.40)" if escalation_level == "CRITICAL" else "rgba(245,158,11,0.40)"

    conflict_pills = ""
    for c in _ACTIVE_CONFLICTS:
        sc = c["status_color"]
        conflict_pills += (
            '<span style="display:inline-flex; align-items:center; gap:6px;'
            ' background:' + sc + '18; border:1px solid ' + sc + '44;'
            ' border-radius:6px; padding:4px 10px; font-size:0.72rem; color:' + sc + ';'
            ' font-weight:700; margin-right:6px; margin-bottom:6px;">'
            '<span style="width:6px; height:6px; border-radius:50%; background:' + sc + '; display:inline-block;'
            ' box-shadow:0 0 6px ' + sc + '"></span>'
            + c["pair"] + " \u2014 " + c["tariff_rate"]
            + '</span>'
        )

    st.markdown(
        '<div style="background:' + banner_bg + '; border:1px solid ' + banner_border + ';'
        ' border-left:4px solid ' + banner_color + '; border-radius:12px; padding:18px 24px;'
        ' margin-bottom:24px;">'
        '<div style="display:flex; align-items:center; gap:12px; margin-bottom:10px; flex-wrap:wrap;">'
        '<span style="font-size:1.4rem;">&#x26A0;&#xFE0F;</span>'
        '<span style="font-size:0.72rem; font-weight:800; letter-spacing:0.12em; color:' + banner_color + ';'
        ' text-transform:uppercase;">TRADE WAR ALERT \u2014 ESCALATION STATUS: ' + escalation_level + '</span>'
        '<span style="margin-left:auto; font-size:0.72rem; color:' + C_TEXT3 + ';">Last updated: Mar 2026</span>'
        '</div>'
        '<div style="display:flex; gap:28px; flex-wrap:wrap; margin-bottom:14px;">'

        '<div>'
        '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.09em; color:' + C_TEXT3 + '; margin-bottom:2px;">Active Conflicts</div>'
        '<div style="font-size:1.6rem; font-weight:900; color:' + banner_color + '; line-height:1;">' + str(len(_ACTIVE_CONFLICTS)) + '</div>'
        '</div>'

        '<div style="width:1px; background:rgba(255,255,255,0.08);"></div>'

        '<div>'
        '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.09em; color:' + C_TEXT3 + '; margin-bottom:2px;">Affected Trade Value</div>'
        '<div style="font-size:1.6rem; font-weight:900; color:' + C_WARN + '; line-height:1;">$' + f"{total_trade_bn / 1000:.1f}" + 'T</div>'
        '</div>'

        '<div style="width:1px; background:rgba(255,255,255,0.08);"></div>'

        '<div>'
        '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.09em; color:' + C_TEXT3 + '; margin-bottom:2px;">Tariff Escalation Level</div>'
        '<div style="font-size:1.6rem; font-weight:900; color:' + banner_color + '; line-height:1;">' + escalation_level + '</div>'
        '</div>'

        '<div style="width:1px; background:rgba(255,255,255,0.08);"></div>'

        '<div>'
        '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.09em; color:' + C_TEXT3 + '; margin-bottom:2px;">Critical / Elevated</div>'
        '<div style="font-size:1.6rem; font-weight:900; color:' + C_TEXT + '; line-height:1;">' + str(critical_count) + ' / ' + str(elevated_count) + '</div>'
        '</div>'

        '</div>'
        '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-bottom:8px;">Active disputes:</div>'
        '<div style="display:flex; flex-wrap:wrap; gap:4px;">' + conflict_pills + '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Affected Sectors Scorecard
# ══════════════════════════════════════════════════════════════════════════════

def _render_sectors_scorecard() -> None:
    """Electronics, Agriculture, Automotive, Steel, Chemicals — each with impact score badge."""
    logger.debug("Rendering sectors scorecard")

    cols = st.columns(len(_SECTOR_IMPACTS))
    for col, sector in zip(cols, _SECTOR_IMPACTS):
        score = sector["impact_score"]
        color = sector["color"]
        score_bg = color + "1a"
        score_border = color + "44"
        with col:
            st.markdown(
                '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
                ' border-top:3px solid ' + color + '; border-radius:12px; padding:16px 14px;'
                ' text-align:center; height:100%;">'

                '<div style="font-size:1.6rem; margin-bottom:6px;">' + sector["icon"] + '</div>'
                '<div style="font-size:0.78rem; font-weight:800; color:' + C_TEXT + '; margin-bottom:10px;'
                ' letter-spacing:-0.01em;">' + sector["sector"] + '</div>'

                '<div style="display:inline-block; background:' + score_bg + '; border:1px solid ' + score_border + ';'
                ' border-radius:999px; padding:4px 14px; margin-bottom:10px;">'
                '<span style="font-size:1.2rem; font-weight:900; color:' + color + ';">' + f"{score:.1f}" + '</span>'
                '<span style="font-size:0.65rem; color:' + C_TEXT3 + '; margin-left:4px;">/10</span>'
                '</div>'

                '<div style="font-size:0.7rem; color:' + C_TEXT3 + '; margin-bottom:8px;">'
                'Tariff: <b style="color:' + color + ';">' + sector["tariff_rate"] + '</b>'
                '</div>'

                '<div style="font-size:0.65rem; color:' + C_TEXT3 + '; text-align:left; margin-bottom:4px;">'
                'Shipping vol. impact'
                '</div>'
                + _progress_bar(abs(sector["shipping_impact"]), color, "5px") +
                '<div style="font-size:0.72rem; font-weight:700; color:' + color + '; margin-top:4px;">'
                + str(sector["shipping_impact"]) + '%'
                '</div>'

                '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; margin-top:8px; line-height:1.4;'
                ' text-align:left;">' + sector["description"] + '</div>'

                '</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Trade Volume Before/After Comparison
# ══════════════════════════════════════════════════════════════════════════════

def _render_trade_volume_comparison() -> None:
    """Side-by-side bar chart: before vs after tariff trade volumes per sector."""
    logger.debug("Rendering trade volume before/after comparison")

    sectors = [s["sector"] for s in _SECTOR_IMPACTS]
    vol_before = [s["trade_vol_before"] for s in _SECTOR_IMPACTS]
    vol_after = [s["trade_vol_after"] for s in _SECTOR_IMPACTS]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Before Tariffs",
        x=sectors,
        y=vol_before,
        marker_color="rgba(59,130,246,0.75)",
        marker_line_color=C_ACCENT,
        marker_line_width=1,
        text=[f"${v}B" for v in vol_before],
        textposition="outside",
        textfont=dict(color=C_TEXT2, size=10),
        hovertemplate="<b>%{x}</b><br>Before: $%{y}B/yr<extra></extra>",
    ))

    fig.add_trace(go.Bar(
        name="After Tariffs",
        x=sectors,
        y=vol_after,
        marker_color="rgba(239,68,68,0.75)",
        marker_line_color=C_DANGER,
        marker_line_width=1,
        text=[f"${v}B" for v in vol_after],
        textposition="outside",
        textfont=dict(color=C_TEXT2, size=10),
        hovertemplate="<b>%{x}</b><br>After: $%{y}B/yr<extra></extra>",
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        font=dict(color=C_TEXT),
        height=380,
        barmode="group",
        margin=dict(l=50, r=20, t=30, b=60),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            color=C_TEXT2,
            tickfont=dict(size=10),
        ),
        yaxis=dict(
            title="Trade Volume ($B / yr)",
            gridcolor="rgba(255,255,255,0.05)",
            color=C_TEXT2,
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="tw_trade_volume_comparison")


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Trade Route Diversion Scattergeo Map
# ══════════════════════════════════════════════════════════════════════════════

def _render_diversion_scattergeo() -> None:
    """Scattergeo showing how tariffs redirect trade flows between key ports."""
    logger.debug("Rendering trade diversion scattergeo map")

    # Node definitions: (name, lat, lon, role, color)
    _NODES = [
        ("Shanghai",          31.23,  121.47, "origin",       C_DANGER),
        ("Shenzhen",          22.54,  114.06, "origin",       C_DANGER),
        ("Los Angeles",       33.73, -118.26, "destination",  C_ACCENT),
        ("New York/NJ",       40.67,  -74.09, "destination",  C_ACCENT),
        ("Ho Chi Minh City",  10.82,  106.63, "diversion",    C_HIGH),
        ("Manzanillo (MX)",   19.05, -104.32, "diversion",    C_HIGH),
        ("Singapore",          1.35,  103.82, "hub",          C_CYAN),
        ("Rotterdam",         51.90,    4.48, "destination",  C_PURPLE),
        ("Chennai",           13.09,   80.27, "diversion",    C_HIGH),
    ]

    # Route arcs: (from_idx, to_idx, label, color, width)
    _ARCS = [
        (0, 2, "China \u2192 LA (pre-tariff baseline)",  "rgba(100,116,139,0.30)", 2),
        (1, 3, "Shenzhen \u2192 NY (pre-tariff)",        "rgba(100,116,139,0.25)", 2),
        (0, 7, "China \u2192 Rotterdam (direct)",         "rgba(139,92,246,0.35)", 2),
        (4, 2, "Vietnam \u2192 LA (diversion)",           "rgba(16,185,129,0.80)", 3),
        (5, 3, "Mexico \u2192 NY (nearshoring)",          "rgba(16,185,129,0.75)", 3),
        (6, 2, "Singapore hub \u2192 LA",                 "rgba(6,182,212,0.65)",  2),
        (8, 7, "India \u2192 Rotterdam (China+1)",        "rgba(16,185,129,0.60)", 2),
        (0, 2, "Residual China \u2192 LA (145% tariff)", "rgba(239,68,68,0.50)",  2),
    ]

    fig = go.Figure()

    for arc in _ARCS:
        from_node = _NODES[arc[0]]
        to_node   = _NODES[arc[1]]
        label     = arc[2]
        arc_color = arc[3]
        arc_width = arc[4]
        mid_lat = (from_node[1] + to_node[1]) / 2
        mid_lon = (from_node[2] + to_node[2]) / 2
        fig.add_trace(go.Scattergeo(
            lat=[from_node[1], mid_lat, to_node[1]],
            lon=[from_node[2], mid_lon, to_node[2]],
            mode="lines",
            line=dict(width=arc_width, color=arc_color),
            name=label,
            showlegend=True,
            hoverinfo="name",
        ))

    for role_filter, marker_symbol, size in [
        ("origin",      "circle",   14),
        ("destination", "square",   13),
        ("diversion",   "star",     16),
        ("hub",         "diamond",  13),
    ]:
        role_nodes = [n for n in _NODES if n[3] == role_filter]
        if not role_nodes:
            continue
        fig.add_trace(go.Scattergeo(
            lat=[n[1] for n in role_nodes],
            lon=[n[2] for n in role_nodes],
            mode="markers+text",
            marker=dict(
                size=size,
                color=[n[4] for n in role_nodes],
                symbol=marker_symbol,
                line=dict(color="rgba(255,255,255,0.40)", width=1.5),
                opacity=0.92,
            ),
            text=[n[0] for n in role_nodes],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            name=role_filter.capitalize() + " ports",
            hovertemplate="<b>%{text}</b><extra></extra>",
            showlegend=True,
        ))

    fig.update_layout(
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.10)",
            showland=True,
            landcolor="#111827",
            showocean=True,
            oceancolor=C_BG,
            showlakes=False,
            showcountries=True,
            countrycolor="rgba(255,255,255,0.08)",
            bgcolor=C_BG,
            projection_type="natural earth",
            lataxis=dict(range=[-15, 65]),
            lonaxis=dict(range=[-140, 155]),
        ),
        paper_bgcolor=C_BG,
        font=dict(color=C_TEXT, size=10),
        height=440,
        margin=dict(l=0, r=0, t=20, b=0),
        legend=dict(
            bgcolor="rgba(10,15,26,0.85)",
            bordercolor=C_BORDER,
            borderwidth=1,
            font=dict(size=9, color=C_TEXT2),
            x=0.01,
            y=0.01,
            xanchor="left",
            yanchor="bottom",
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="tw_diversion_scattergeo")

    st.markdown(
        '<div style="font-size:0.76rem; color:' + C_TEXT3 + '; margin-top:-4px; padding:0 4px;">'
        'Circles = Chinese origin ports (under tariff). Stars = diversion hubs (Vietnam, Mexico, India). '
        'Dotted/faded lines = pre-tariff baselines. Solid colored lines = active diverted flows.'
        '</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# NEW: Winner / Loser Analysis — two-column card layout
# ══════════════════════════════════════════════════════════════════════════════

def _render_winner_loser_analysis() -> None:
    """Two-column card layout: winners (green) on left, losers (red) on right."""
    logger.debug("Rendering winner/loser analysis")

    col_win, col_lose = st.columns(2)

    with col_win:
        st.markdown(
            '<div style="font-size:0.72rem; font-weight:800; text-transform:uppercase;'
            ' letter-spacing:0.10em; color:' + C_HIGH + '; margin-bottom:12px;">'
            '&#x2B06; WINNERS \u2014 Routes &amp; Ports Benefiting</div>',
            unsafe_allow_html=True,
        )
        for w in _WINNERS:
            st.markdown(
                '<div style="background:' + C_CARD + '; border:1px solid rgba(16,185,129,0.20);'
                ' border-left:3px solid ' + C_HIGH + '; border-radius:10px; padding:12px 16px;'
                ' margin-bottom:8px;">'
                '<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">'
                '<div>'
                '<span style="font-size:0.82rem; font-weight:700; color:' + C_TEXT + ';">' + w["name"] + '</span>'
                '<span style="margin-left:8px; font-size:0.65rem; color:' + C_TEXT3 + ';">' + w["type"] + '</span>'
                '</div>'
                '<span style="font-size:1.0rem; font-weight:900; color:' + C_HIGH + ';">+'
                + str(w["gain_pct"]) + '%</span>'
                '</div>'
                + _progress_bar(w["gain_pct"], C_HIGH, "4px") +
                '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-top:6px; line-height:1.4;">'
                + w["reason"] + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    with col_lose:
        st.markdown(
            '<div style="font-size:0.72rem; font-weight:800; text-transform:uppercase;'
            ' letter-spacing:0.10em; color:' + C_DANGER + '; margin-bottom:12px;">'
            '&#x2B07; LOSERS \u2014 Routes &amp; Ports Under Pressure</div>',
            unsafe_allow_html=True,
        )
        for lo in _LOSERS:
            pct_abs = abs(lo["loss_pct"])
            lcolor = lo["color"]
            st.markdown(
                '<div style="background:' + C_CARD + '; border:1px solid rgba(239,68,68,0.18);'
                ' border-left:3px solid ' + lcolor + '; border-radius:10px; padding:12px 16px;'
                ' margin-bottom:8px;">'
                '<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px;">'
                '<div>'
                '<span style="font-size:0.82rem; font-weight:700; color:' + C_TEXT + ';">' + lo["name"] + '</span>'
                '<span style="margin-left:8px; font-size:0.65rem; color:' + C_TEXT3 + ';">' + lo["type"] + '</span>'
                '</div>'
                '<span style="font-size:1.0rem; font-weight:900; color:' + lcolor + ';">'
                + str(lo["loss_pct"]) + '%</span>'
                '</div>'
                + _progress_bar(pct_abs, lcolor, "4px") +
                '<div style="font-size:0.72rem; color:' + C_TEXT2 + '; margin-top:6px; line-height:1.4;">'
                + lo["reason"] + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Trade War Status Dashboard (existing)
# ══════════════════════════════════════════════════════════════════════════════

def _render_status_dashboard() -> None:
    logger.debug("Rendering trade war status dashboard")

    total_trade = sum(c["trade_value_bn"] for c in _ACTIVE_CONFLICTS)
    escalation_pct = 87

    kpi_cols = st.columns(4)
    kpis = [
        ("Active Conflicts",   str(len(_ACTIVE_CONFLICTS)), C_DANGER, "Bilateral trade disputes"),
        ("Trade Under Tariff", f"${total_trade / 1000:.1f}T", C_WARN,   "Annualized bilateral trade value"),
        ("Escalation Index",   f"{escalation_pct}/100",       C_DANGER, "Composite trade war intensity score"),
        ("Shipping Impact",    "\u2212$8.4B/yr",              C_WARN,   "Estimated annual freight revenue delta"),
    ]
    for col, (label, value, color, sublabel) in zip(kpi_cols, kpis):
        with col:
            st.markdown(
                '<div style="background:' + C_CARD + '; border:1px solid ' + color + '33;'
                ' border-radius:14px; padding:20px 18px; border-top:3px solid ' + color + '">'
                '<div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.10em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">' + label + '</div>'
                '<div style="font-size:2rem; font-weight:900; color:' + color + ';'
                ' letter-spacing:-0.03em; line-height:1">' + value + '</div>'
                '<div style="font-size:0.72rem; color:' + C_TEXT3 + '; margin-top:6px">'
                + sublabel + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)

    gauge_col, conflicts_col = st.columns([1, 2])

    with gauge_col:
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=escalation_pct,
            title=dict(text="Trade War<br>Escalation Index", font=dict(color=C_TEXT2, size=12)),
            number=dict(font=dict(color=C_TEXT, size=36), suffix="/100"),
            gauge=dict(
                axis=dict(
                    range=[0, 100],
                    tickwidth=1,
                    tickcolor=C_TEXT3,
                    tickfont=dict(color=C_TEXT3, size=9),
                    nticks=6,
                ),
                bar=dict(color=C_DANGER, thickness=0.28),
                bgcolor="rgba(0,0,0,0)",
                borderwidth=0,
                steps=[
                    dict(range=[0,  33], color="rgba(16,185,129,0.15)"),
                    dict(range=[33, 66], color="rgba(245,158,11,0.15)"),
                    dict(range=[66, 100], color="rgba(239,68,68,0.15)"),
                ],
                threshold=dict(
                    line=dict(color=C_WARN, width=3),
                    thickness=0.85,
                    value=70,
                ),
            ),
        ))
        fig_gauge.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT),
            height=200,
            margin=dict(l=20, r=20, t=20, b=10),
        )
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:14px; padding:10px 4px">',
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig_gauge, use_container_width=True, key="tw_escalation_gauge")
        st.markdown('</div>', unsafe_allow_html=True)

    with conflicts_col:
        st.markdown(
            '<div style="font-size:0.70rem; text-transform:uppercase; letter-spacing:0.10em;'
            ' color:' + C_TEXT3 + '; margin-bottom:12px">Active Trade Conflicts</div>',
            unsafe_allow_html=True,
        )
        for conflict in _ACTIVE_CONFLICTS:
            sc = conflict["status_color"]
            badge_html = (
                '<span style="font-size:0.65rem; font-weight:700; color:' + sc + ';'
                ' background:' + sc + '1a; border:1px solid ' + sc + '44;'
                ' border-radius:4px; padding:2px 7px; letter-spacing:0.06em">'
                + conflict["status"] + "</span>"
            )
            st.markdown(
                '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
                ' border-left:3px solid ' + sc + '; border-radius:10px; padding:12px 16px;'
                ' margin-bottom:10px">'
                '<div style="display:flex; align-items:center; gap:10px; margin-bottom:4px">'
                '<span style="font-size:0.90rem; font-weight:800; color:' + C_TEXT + '">'
                + conflict["pair"] + "</span>" + badge_html
                + '</div>'
                '<div style="display:flex; gap:20px">'
                '<span style="font-size:0.75rem; color:' + C_TEXT2 + '">Tariff: <b style="color:' + sc + '">'
                + conflict["tariff_rate"] + "</b></span>"
                '<span style="font-size:0.75rem; color:' + C_TEXT2 + '">Trade Value: <b style="color:' + C_TEXT + '">$'
                + str(conflict["trade_value_bn"]) + "B</b></span>"
                '<span style="font-size:0.75rem; color:' + C_TEXT3 + '">Since ' + conflict["since"] + "</span>"
                '<span style="font-size:0.68rem; color:' + C_TEXT3 + '">' + conflict["type"] + "</span>"
                "</div></div>",
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Global Tariff Heat Map
# ══════════════════════════════════════════════════════════════════════════════

def _render_tariff_heatmap() -> None:
    logger.debug("Rendering tariff heat map choropleth")

    iso_codes   = [c["iso"]         for c in _COUNTRY_TARIFF_RISK]
    risk_values = [c["tariff_risk"] for c in _COUNTRY_TARIFF_RISK]
    names       = [c["name"]        for c in _COUNTRY_TARIFF_RISK]
    notes       = [c["note"]        for c in _COUNTRY_TARIFF_RISK]

    hover_text = [
        "<b>" + names[i] + "</b> (" + iso_codes[i] + ")<br>"
        "Tariff Risk: " + str(int(risk_values[i] * 100)) + "%<br>"
        "<i>" + notes[i] + "</i>"
        for i in range(len(iso_codes))
    ]

    fig = go.Figure(go.Choropleth(
        locations=iso_codes,
        z=risk_values,
        zmin=0.0,
        zmax=1.0,
        colorscale=[
            [0.00, "#10b981"],
            [0.33, "#84cc16"],
            [0.55, "#f59e0b"],
            [0.75, "#f97316"],
            [1.00, "#ef4444"],
        ],
        colorbar=dict(
            title=dict(text="Tariff Risk", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=10),
            tickformat=".0%",
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["Low 0%", "25%", "Moderate 50%", "75%", "Critical 100%"],
            bgcolor="rgba(10,15,26,0.90)",
            bordercolor=C_BORDER,
            borderwidth=1,
            len=0.80,
            thickness=14,
        ),
        hovertext=hover_text,
        hoverinfo="text",
        marker_line_color="rgba(255,255,255,0.12)",
        marker_line_width=0.6,
    ))

    fig.update_layout(
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.12)",
            showland=True,
            landcolor="#111827",
            showocean=True,
            oceancolor="#0a0f1a",
            showlakes=False,
            showrivers=False,
            showcountries=True,
            countrycolor="rgba(255,255,255,0.10)",
            bgcolor=C_BG,
            projection_type="natural earth",
        ),
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT),
        height=420,
        margin=dict(l=0, r=0, t=10, b=0),
    )

    st.plotly_chart(fig, use_container_width=True, key="trade_war_tariff_choropleth")


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Tariff Impact Matrix (Countries x Goods)
# ══════════════════════════════════════════════════════════════════════════════

def _render_tariff_matrix() -> None:
    logger.debug("Rendering tariff impact matrix")

    countries = ["China", "EU", "Japan", "Canada", "Mexico", "S. Korea", "Vietnam"]
    goods     = ["Electronics", "Autos", "Steel", "Agri", "Chemicals", "Pharma", "Textiles", "Machinery"]

    # Tariff rate (%) — rows=countries, cols=goods
    tariff_matrix = [
        # Elec  Auto  Steel  Agri  Chem  Pharma  Text  Mach
        [145,   25,   25,   34,   145,  0,      145,  145],  # China
        [10,    25,   25,   0,    10,   0,      10,   10 ],  # EU
        [25,    25,   25,   0,    10,   0,      10,   10 ],  # Japan
        [10,    25,   25,   0,    25,   0,      25,   25 ],  # Canada
        [10,    25,   25,   0,    25,   0,      25,   25 ],  # Mexico
        [10,    25,   25,   0,    10,   0,      10,   10 ],  # S. Korea
        [0,     0,    0,    0,    0,    0,      0,    0  ],  # Vietnam (recipient)
    ]

    def _tariff_cell_color(rate: int) -> str:
        if rate == 0:
            return "rgba(16,185,129,0.25)"
        elif rate <= 15:
            return "rgba(132,204,22,0.35)"
        elif rate <= 25:
            return "rgba(245,158,11,0.40)"
        elif rate <= 50:
            return "rgba(249,115,22,0.50)"
        else:
            return "rgba(239,68,68,0.65)"

    def _tariff_text_color(rate: int) -> str:
        if rate == 0:
            return C_HIGH
        elif rate <= 15:
            return C_LIME
        elif rate <= 25:
            return C_WARN
        elif rate <= 50:
            return "#f97316"
        else:
            return C_DANGER

    th_style = (
        'style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.07em;'
        ' color:' + C_TEXT3 + '; padding:8px 10px; text-align:center;'
        ' border-bottom:1px solid rgba(255,255,255,0.08)"'
    )
    matrix_html = (
        '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
        ' border-radius:14px; padding:18px; overflow-x:auto">'
        '<table style="width:100%; border-collapse:collapse; min-width:560px">'
        "<thead><tr>"
        '<th ' + th_style + ' style="text-align:left">Country / Goods</th>'
    )
    for g in goods:
        matrix_html += "<th " + th_style + ">" + g + "</th>"
    matrix_html += "</tr></thead><tbody>"

    for r_idx, (country, row) in enumerate(zip(countries, tariff_matrix)):
        row_bg = "rgba(255,255,255,0.02)" if r_idx % 2 == 0 else "rgba(0,0,0,0)"
        matrix_html += (
            '<tr style="background:' + row_bg + '">'
            '<td style="font-size:0.80rem; font-weight:700; color:' + C_TEXT + ';'
            ' padding:9px 10px; border-bottom:1px solid rgba(255,255,255,0.05)">'
            + country + "</td>"
        )
        for rate in row:
            cell_bg  = _tariff_cell_color(rate)
            cell_col = _tariff_text_color(rate)
            label = str(rate) + "%" if rate > 0 else "\u2014"
            matrix_html += (
                '<td style="text-align:center; padding:9px 6px;'
                ' border-bottom:1px solid rgba(255,255,255,0.05)">'
                '<div style="background:' + cell_bg + '; border-radius:6px;'
                ' padding:5px 4px; font-size:0.80rem; font-weight:700; color:' + cell_col + '">'
                + label + "</div></td>"
            )
        matrix_html += "</tr>"

    matrix_html += (
        "</tbody></table>"
        '<div style="display:flex; gap:14px; margin-top:14px; flex-wrap:wrap; align-items:center">'
        '<span style="font-size:0.68rem; color:' + C_TEXT3 + '">Legend:</span>'
        '<span style="font-size:0.68rem; color:' + C_HIGH   + '">\u2014 No tariff (0%)</span>'
        '<span style="font-size:0.68rem; color:' + C_LIME   + '">\u25a0 Low (&le;15%)</span>'
        '<span style="font-size:0.68rem; color:' + C_WARN   + '">\u25a0 Moderate (&le;25%)</span>'
        '<span style="font-size:0.68rem; color:#f97316">\u25a0 High (&le;50%)</span>'
        '<span style="font-size:0.68rem; color:' + C_DANGER + '">\u25a0 Critical (&gt;50%)</span>'
        "</div></div>"
    )

    st.markdown(matrix_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Tariff Scenario Builder
# ══════════════════════════════════════════════════════════════════════════════

def _compute_scenario_impacts(
    us_china_pct: float,
    us_eu_pct: float,
    china_retaliation_pct: float,
    pmi_impact_pp: float,
    trade_diversion: bool,
) -> dict:
    """
    Compute estimated volume and rate impacts for key shipping lanes
    based on user-supplied tariff scenario parameters.

    All inputs are as percentages (0-100 for tariffs, -10 to 0 for PMI).
    Returns a dict with computed impact strings.
    """
    # Convert to decimal fractions for elasticity math
    us_china_frac        = us_china_pct / 100.0
    us_eu_frac           = us_eu_pct / 100.0
    china_retal_frac     = china_retaliation_pct / 100.0

    # Volume elasticity: -0.8 per unit of tariff shock (from tariff_analyzer)
    _elast = -0.8
    _rate_follow = 0.6

    # Baseline tariff on transpacific EB = 14.5% — incremental shock above baseline
    baseline_tp = 0.145
    shock_tp = max(0.0, us_china_frac - baseline_tp)
    tp_eb_vol_chg = shock_tp * 0.85 * _elast  # exposure_score=0.85
    tp_eb_rate_chg = tp_eb_vol_chg * _rate_follow

    # Trade diversion bonus: if diverting, SE Asia lanes gain ~40% of lost US-China volume
    diversion_bonus_tp = 0.0
    if trade_diversion and shock_tp > 0:
        diversion_bonus_tp = shock_tp * 0.85 * 0.40  # 40% recapture via SEA

    # Asia-Europe: exposure_score=0.40, influenced by US-EU tariff + china retaliation
    baseline_ae = 0.065
    shock_ae = max(0.0, (us_eu_frac - baseline_ae) * 0.40 + china_retal_frac * 0.20)
    ae_vol_chg = shock_ae * _elast
    ae_rate_chg = ae_vol_chg * _rate_follow

    # PMI multiplier: every 1pp drop in global PMI reduces trade volumes ~0.5%
    pmi_vol_effect = pmi_impact_pp * 0.005  # negative already

    # Apply PMI to both lanes
    tp_eb_vol_chg  += pmi_vol_effect
    ae_vol_chg     += pmi_vol_effect

    # Rate shock estimate on key shipping stocks (ZIM most exposed at ~90% TP/AE)
    zim_rate_impact  = tp_eb_rate_chg * 0.65 + ae_rate_chg * 0.25
    matx_rate_impact = tp_eb_rate_chg * 0.75  # MATX = Matson, Hawaii/Pacific focus

    # Format helper
    def _fmt_pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return sign + str(round(v * 100, 1)) + "%"

    def _color(v: float) -> str:
        return C_HIGH if v >= 0 else C_DANGER

    return {
        "tp_eb_vol_chg":          tp_eb_vol_chg,
        "tp_eb_rate_chg":         tp_eb_rate_chg,
        "ae_vol_chg":             ae_vol_chg,
        "ae_rate_chg":            ae_rate_chg,
        "zim_rate_impact":        zim_rate_impact,
        "matx_rate_impact":       matx_rate_impact,
        "diversion_bonus_tp":     diversion_bonus_tp,
        "tp_eb_vol_str":          _fmt_pct(tp_eb_vol_chg + diversion_bonus_tp),
        "tp_eb_rate_str":         _fmt_pct(tp_eb_rate_chg),
        "ae_vol_str":             _fmt_pct(ae_vol_chg),
        "ae_rate_str":            _fmt_pct(ae_rate_chg),
        "zim_str":                _fmt_pct(zim_rate_impact),
        "matx_str":               _fmt_pct(matx_rate_impact),
        "tp_eb_vol_color":        _color(tp_eb_vol_chg + diversion_bonus_tp),
        "tp_eb_rate_color":       _color(tp_eb_rate_chg),
        "ae_vol_color":           _color(ae_vol_chg),
        "ae_rate_color":          _color(ae_rate_chg),
        "zim_color":              _color(zim_rate_impact),
        "matx_color":             _color(matx_rate_impact),
    }


def _render_scenario_builder(route_results: list) -> None:
    logger.debug("Rendering tariff scenario builder")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(
            '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.10em;'
            ' color:' + C_TEXT3 + '; margin-bottom:12px">Scenario Controls</div>',
            unsafe_allow_html=True,
        )

        us_china_pct = st.slider(
            "US \u2192 China tariff rate %",
            min_value=0,
            max_value=200,
            value=145,
            step=5,
            help="Total US tariff rate applied to Chinese goods imports",
            key="tw_slider_us_china_pct",
        )
        us_eu_pct = st.slider(
            "US \u2192 EU tariff rate %",
            min_value=0,
            max_value=50,
            value=10,
            step=1,
            help="US tariff rate applied to EU goods imports",
            key="tw_slider_us_eu_pct",
        )
        china_retaliation_pct = st.slider(
            "China retaliation %",
            min_value=0,
            max_value=125,
            value=84,
            step=5,
            help="Chinese retaliatory tariff rate on US exports",
            key="tw_slider_china_retaliation_pct",
        )
        pmi_impact_pp = st.slider(
            "Global PMI drag (pp)",
            min_value=-10,
            max_value=0,
            value=-3,
            step=1,
            help="Estimated drag on global PMI from trade war uncertainty (percentage points)",
            key="tw_slider_pmi_impact_pp",
        )
        trade_diversion = st.checkbox(
            "Model trade diversion (Vietnam / Mexico)",
            value=True,
            help="Model trade flow diversion through alternative manufacturing hubs",
            key="tw_checkbox_trade_diversion",
        )

    impacts = _compute_scenario_impacts(
        us_china_pct=float(us_china_pct),
        us_eu_pct=float(us_eu_pct),
        china_retaliation_pct=float(china_retaliation_pct),
        pmi_impact_pp=float(pmi_impact_pp),
        trade_diversion=trade_diversion,
    )

    with col_right:
        st.markdown(
            '<div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.10em;'
            ' color:' + C_TEXT3 + '; margin-bottom:12px">Live Impact Preview</div>',
            unsafe_allow_html=True,
        )

        preview_html = (
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:14px; padding:20px 22px">'

            '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT2 + ';'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:12px;'
            ' padding-bottom:8px; border-bottom:1px solid ' + C_BORDER + '">'
            "Trans-Pacific EB Lane</div>"
            + _metric_pill("Volume Change", impacts["tp_eb_vol_str"], impacts["tp_eb_vol_color"])
            + _metric_pill("Rate Change Estimate", impacts["tp_eb_rate_str"], impacts["tp_eb_rate_color"])

            + '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT2 + ';'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-top:16px; margin-bottom:12px;'
            ' padding-top:12px; padding-bottom:8px;'
            ' border-top:1px solid ' + C_BORDER + '; border-bottom:1px solid ' + C_BORDER + '">'
            "Asia-Europe Lane</div>"
            + _metric_pill("Volume Change", impacts["ae_vol_str"], impacts["ae_vol_color"])
            + _metric_pill("Rate Change Estimate", impacts["ae_rate_str"], impacts["ae_rate_color"])

            + '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT2 + ';'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-top:16px; margin-bottom:12px;'
            ' padding-top:12px; padding-bottom:8px;'
            ' border-top:1px solid ' + C_BORDER + '; border-bottom:1px solid ' + C_BORDER + '">'
            "Carrier Stock Exposure (Rate Impact)</div>"
            + _metric_pill("ZIM (Most Exposed)",   impacts["zim_str"],  impacts["zim_color"])
            + _metric_pill("MATX (Pacific Focus)", impacts["matx_str"], impacts["matx_color"])

            + "</div>"
        )

        st.markdown(preview_html, unsafe_allow_html=True)

        if trade_diversion and float(us_china_pct) > 14:
            st.markdown(
                '<div style="margin-top:10px; padding:12px 16px; background:rgba(16,185,129,0.08);'
                ' border:1px solid rgba(16,185,129,0.28); border-radius:10px;'
                ' font-size:0.80rem; color:' + C_HIGH + '">'
                "<b>Trade diversion active</b> \u2014 Vietnam/Mexico absorbing ~"
                + str(round(impacts["diversion_bonus_tp"] * 100, 1))
                + "pp of diverted Trans-Pacific volume"
                "</div>",
                unsafe_allow_html=True,
            )

    # Also run tariff_analyzer if we have real route_results
    if route_results:
        shock_frac = max(0.0, float(us_china_pct) / 100.0 - 0.145)
        try:
            tariff_impacts = analyze_tariff_sensitivity(route_results, tariff_shock_pct=shock_frac)
            logger.debug(
                "tariff_analyzer returned {} route impacts for scenario shock={:.1%}",
                len(tariff_impacts),
                shock_frac,
            )
        except Exception as exc:
            logger.warning("tariff_analyzer call failed: {}", exc)
            tariff_impacts = []

        if tariff_impacts:
            top_hits = sorted(
                tariff_impacts,
                key=lambda x: abs(x.net_opportunity_delta),
                reverse=True,
            )[:5]

            rows_html = ""
            for ti in top_hits:
                delta_color = C_HIGH if ti.net_opportunity_delta >= 0 else C_DANGER
                sign = "+" if ti.net_opportunity_delta >= 0 else ""
                rows_html += (
                    "<tr>"
                    '<td style="color:' + C_TEXT + '; font-size:0.82rem; padding:8px 6px">'
                    + ti.route_name + "</td>"
                    '<td style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:8px 6px">'
                    + str(round(ti.volume_impact_pct * 100, 1)) + "%" + "</td>"
                    '<td style="color:' + C_TEXT2 + '; font-size:0.82rem; padding:8px 6px">'
                    + str(round(ti.rate_impact_pct * 100, 1)) + "%" + "</td>"
                    '<td style="color:' + delta_color + '; font-weight:700; font-size:0.82rem; padding:8px 6px">'
                    + sign + str(round(ti.net_opportunity_delta * 100, 1)) + "%" + "</td>"
                    "</tr>"
                )

            table_html = (
                '<div style="margin-top:18px">'
                '<div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:0.08em;'
                ' color:' + C_TEXT3 + '; margin-bottom:8px">Top 5 Affected Routes (Tariff Analyzer)</div>'
                '<table style="width:100%; border-collapse:collapse">'
                "<thead><tr>"
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Route</th>'
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Vol Chg</th>'
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Rate Chg</th>'
                '<th style="color:' + C_TEXT3 + '; font-size:0.70rem; text-transform:uppercase;'
                ' padding:6px 6px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Net Delta</th>'
                "</tr></thead>"
                "<tbody>" + rows_html + "</tbody>"
                "</table></div>"
            )
            st.markdown(table_html, unsafe_allow_html=True)

            # ── CSV export for route tariff impacts ──────────────────────────
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Route", "Volume Change %", "Rate Change %", "Net Opportunity Delta %"])
            for ti in tariff_impacts:
                writer.writerow([
                    ti.route_name,
                    round(ti.volume_impact_pct * 100, 1),
                    round(ti.rate_impact_pct * 100, 1),
                    round(ti.net_opportunity_delta * 100, 1),
                ])
            st.download_button(
                label="Export Tariff Impact CSV",
                data=buf.getvalue().encode(),
                file_name="tariff_route_impacts.csv",
                mime="text/csv",
                key="tw_tariff_impacts_csv_download",
            )

    st.caption("Tariff impacts estimated based on 2018-2024 trade data and announced rates")


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Trade Diversion Sankey
# ══════════════════════════════════════════════════════════════════════════════

def _render_trade_diversion_sankey() -> None:
    logger.debug("Rendering trade diversion Sankey diagram")

    # ── Guard: show informative message if tariff level implies no significant diversion ──
    # Read scenario slider values from session_state (keys set in scenario builder).
    # Diversion is only meaningful when US-China tariff meaningfully exceeds baseline (~14.5%).
    _tw_us_china = st.session_state.get("tw_slider_us_china_pct", 25)
    _tw_diversion = st.session_state.get("tw_checkbox_trade_diversion", True)
    _DIVERSION_THRESHOLD = 20  # % — below this, no significant diversion modelled

    if not _tw_diversion or int(_tw_us_china) < _DIVERSION_THRESHOLD:
        st.info(
            "No significant trade diversion detected under current scenario settings "
            "(US-China tariff below {thr}% or diversion not enabled). "
            "Increase the US-China tariff rate above {thr}% and enable "
            "\"Trade diversion to Vietnam/Mexico\" in the Scenario Builder to see diversion flows.".format(
                thr=_DIVERSION_THRESHOLD
            )
        )
        return

    # Nodes
    # 0: China (origin)
    # 1: USA (destination)
    # 2: Vietnam (intermediate)
    # 3: Mexico (intermediate)
    # 4: USA-direct (same destination, split trace for clarity)
    # 5: EU (origin for second triangle)
    # 6: EU-bound (destination)

    node_labels = [
        "China",          # 0
        "USA (Direct)",   # 1
        "Vietnam",        # 2
        "Mexico",         # 3
        "USA (Diverted)", # 4
        "China (AE)",     # 5
        "Europe",         # 6
        "SE Asia Hub",    # 7
    ]

    node_colors = [
        "#ef4444",   # China — red
        "#f59e0b",   # USA direct — amber
        "#10b981",   # Vietnam — green
        "#10b981",   # Mexico — green
        "#3b82f6",   # USA diverted — blue
        "#ef4444",   # China AE — red
        "#8b5cf6",   # Europe — purple
        "#06b6d4",   # SE Asia Hub — cyan
    ]

    # source, target, value (TEU thousands/year), color, label
    # Original direct US-China flow (gray, faded) — pre-tariff baseline
    # Diverted flows (colored) — post 145% tariff scenario
    flow_defs = [
        # Original US-China direct (faded — historical baseline)
        (0, 1, 8000,  "rgba(100,116,139,0.35)", "Pre-tariff: China \u2192 USA (8.0M TEU)"),
        # Diverted: China → Vietnam → USA
        (0, 2, 2200,  "rgba(16,185,129,0.70)",  "China \u2192 Vietnam transship (2.2M TEU)"),
        (2, 4, 2200,  "rgba(16,185,129,0.70)",  "Vietnam \u2192 USA (2.2M TEU)"),
        # Diverted: China → Mexico → USA (assembly)
        (0, 3, 1400,  "rgba(16,185,129,0.60)",  "China components \u2192 Mexico assembly (1.4M TEU)"),
        (3, 4, 1400,  "rgba(16,185,129,0.60)",  "Mexico nearshoring \u2192 USA (1.4M TEU)"),
        # Residual direct US-China (what remains at 145% tariff)
        (0, 1, 2800,  "rgba(239,68,68,0.55)",   "Residual China \u2192 USA direct (2.8M TEU)"),
        # Asia-Europe diversion via SE Asia
        (5, 7, 1800,  "rgba(6,182,212,0.65)",   "China \u2192 SE Asia hub (1.8M TEU)"),
        (7, 6, 1800,  "rgba(6,182,212,0.65)",   "SE Asia hub \u2192 Europe (1.8M TEU)"),
        # China direct to Europe (residual)
        (5, 6, 3200,  "rgba(139,92,246,0.45)",  "China \u2192 Europe direct (3.2M TEU)"),
    ]

    sources  = [f[0] for f in flow_defs]
    targets  = [f[1] for f in flow_defs]
    values   = [f[2] for f in flow_defs]
    link_col = [f[3] for f in flow_defs]
    link_lbl = [f[4] for f in flow_defs]

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=18,
            thickness=22,
            line=dict(color="rgba(255,255,255,0.12)", width=0.8),
            label=node_labels,
            color=node_colors,
            hovertemplate="<b>%{label}</b><extra></extra>",
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_col,
            label=link_lbl,
            hovertemplate=(
                "<b>%{label}</b><br>"
                "Volume: %{value:,} TEU/yr<extra></extra>"
            ),
        ),
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, size=11),
        height=400,
        margin=dict(l=20, r=20, t=20, b=20),
    )

    st.plotly_chart(fig, use_container_width=True, key="trade_war_diversion_sankey")

    # Legend note
    st.markdown(
        '<div style="font-size:0.78rem; color:' + C_TEXT3 + '; margin-top:-6px; padding:0 4px">'
        "Gray flows = pre-tariff baseline. Colored flows = diverted/residual volumes at 145% US-China tariff. "
        "TEU/yr figures are modeled estimates based on 2024 trade data and elasticity assumptions."
        "</div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Historical Tariff Impact Events Timeline
# ══════════════════════════════════════════════════════════════════════════════

def _render_historical_timeline() -> None:
    logger.debug("Rendering historical tariff event timeline")

    # Key tariff events: (date_str, label, y_annot, description)
    tariff_events = [
        ("2018-07-06", "Section 301\n$34B",   1, "US Section 301 tariffs on $34B Chinese goods at 25%"),
        ("2018-08-23", "+$16B",                2, "US tariffs on additional $16B Chinese goods at 25%"),
        ("2018-09-24", "$200B @ 10%",          1, "US tariffs on $200B Chinese goods at 10%"),
        ("2019-05-10", "Raised to 25%",        2, "US raises $200B tariff tranche from 10% to 25%"),
        ("2020-01-15", "Phase 1 Deal",         1, "US-China Phase 1 trade deal — partial rollback"),
        ("2021-06-01", "Pause",                2, "Trade war pause — rates stabilize under Biden"),
        ("2025-01-20", "Trump 2.0\n10% base", 1, "Trump tariffs 2.0 — 10% universal baseline"),
        ("2025-04-09", "Escalation\n145%",    2, "US-China tariffs escalate to 145%"),
    ]

    # Approximate Trans-Pacific EB spot rate index (illustrative, USD/FEU)
    # Key: rate series overlaid to show correlation with tariff events
    rate_dates = [
        "2018-01-01", "2018-07-01", "2018-10-01", "2019-01-01", "2019-05-01",
        "2019-10-01", "2020-01-01", "2020-06-01", "2020-10-01", "2021-01-01",
        "2021-06-01", "2021-12-01", "2022-03-01", "2022-09-01", "2023-01-01",
        "2023-06-01", "2024-01-01", "2024-06-01", "2025-01-01", "2025-04-01",
        "2025-07-01", "2025-10-01",
    ]
    rate_values = [
        1800, 2100, 2400, 2200, 2600,
        2100, 1900, 2400, 3800, 5500,
        7200, 10800, 9500, 6200, 2800,
        1600, 2200, 2800, 3400, 5100,
        4200, 3800,
    ]

    fig = go.Figure()

    # Rate series (left y-axis)
    fig.add_trace(go.Scatter(
        x=rate_dates,
        y=rate_values,
        name="Trans-Pacific EB Rate (USD/FEU)",
        mode="lines",
        line=dict(color=C_ACCENT, width=2.5),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.08)",
        yaxis="y1",
        hovertemplate="<b>Trans-Pacific EB</b><br>%{x}<br>$%{y:,}/FEU<extra></extra>",
    ))

    # Tariff event markers
    event_x = [ev[0] for ev in tariff_events]
    event_labels = [ev[1] for ev in tariff_events]
    event_desc   = [ev[3] for ev in tariff_events]

    # Map event dates to approximate rate values for marker placement
    rate_lookup = dict(zip(rate_dates, rate_values))

    def _nearest_rate(date_str: str) -> float:
        yr, mo = date_str[:7].split("-")
        key_mo = date_str[:7] + "-01"
        if key_mo in rate_lookup:
            return rate_lookup[key_mo]
        # fallback: median
        return 3500.0

    event_y = [_nearest_rate(ev[0]) for ev in tariff_events]

    fig.add_trace(go.Scatter(
        x=event_x,
        y=event_y,
        name="Tariff Events",
        mode="markers+text",
        marker=dict(
            size=16,
            color=C_WARN,
            symbol="diamond",
            line=dict(color="rgba(255,255,255,0.35)", width=1.5),
        ),
        text=event_labels,
        textposition=[
            "top center", "top right", "top center", "top right",
            "top center", "top right", "top center", "top right",
        ],
        textfont=dict(size=9, color=C_WARN),
        hovertext=[
            "<b>" + ev[1].replace("\n", " ") + "</b><br>" + ev[3]
            for ev in tariff_events
        ],
        hoverinfo="text",
        yaxis="y1",
    ))

    # Vertical annotation lines for tariff events
    for ev in tariff_events:
        fig.add_vline(
            x=ev[0],
            line_color="rgba(245,158,11,0.25)",
            line_dash="dot",
            line_width=1,
        )

    # Phase 1 shading
    fig.add_vrect(
        x0="2020-01-15", x1="2025-01-20",
        fillcolor="rgba(16,185,129,0.05)",
        line_width=0,
        annotation_text="Trade War Pause",
        annotation_position="top left",
        annotation_font=dict(size=9, color=C_TEXT3),
    )

    # 2021 peak shading
    fig.add_vrect(
        x0="2021-06-01", x1="2022-06-01",
        fillcolor="rgba(239,68,68,0.06)",
        line_width=0,
        annotation_text="COVID Surge Peak",
        annotation_position="top right",
        annotation_font=dict(size=9, color=C_TEXT3),
    )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        font=dict(color=C_TEXT),
        height=420,
        margin=dict(l=60, r=40, t=30, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(
            title="Date",
            gridcolor="rgba(255,255,255,0.05)",
            color=C_TEXT2,
            showspikes=True,
            spikecolor="rgba(255,255,255,0.20)",
            spikethickness=1,
        ),
        yaxis=dict(
            title="Trans-Pacific EB Rate (USD/FEU)",
            gridcolor="rgba(255,255,255,0.05)",
            color=C_TEXT2,
            tickformat="$,.0f",
        ),
        hovermode="x unified",
    )

    st.plotly_chart(fig, use_container_width=True, key="trade_war_tariff_timeline")


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Supply Chain Reshoring Tracker
# ══════════════════════════════════════════════════════════════════════════════

_RESHORING_TRENDS: list[dict] = [
    {
        "flag": "VN",
        "country": "Vietnam",
        "industry": "Electronics & Manufacturing",
        "growth_pct": 40,
        "routes": ["Trans-Pacific EB", "SEA-Transpacific EB"],
        "note": "US-China alternative hub for consumer electronics, semiconductors, apparel",
        "rating": "HIGH",
        "rating_color": C_HIGH,
        "investment_bn": 38,
        "companies": "Samsung, Apple, Intel, Nike",
    },
    {
        "flag": "MX",
        "country": "Mexico",
        "industry": "Automotive & Assembly",
        "growth_pct": 25,
        "routes": ["US East-South America", "Trans-Pacific WB"],
        "note": "USMCA-driven nearshoring \u2014 auto parts, EV assembly, white goods",
        "rating": "HIGH",
        "rating_color": C_HIGH,
        "investment_bn": 52,
        "companies": "Tesla, GM, BMW, Whirlpool",
    },
    {
        "flag": "IN",
        "country": "India",
        "industry": "Pharma, IT & Textiles",
        "growth_pct": 15,
        "routes": ["South Asia-Europe", "Asia-Europe"],
        "note": "China+1 beneficiary \u2014 Apple supply chain, generic pharma, IT services",
        "rating": "MODERATE",
        "rating_color": C_ACCENT,
        "investment_bn": 24,
        "companies": "Apple, Foxconn, Tata, Cipla",
    },
    {
        "flag": "PL",
        "country": "Eastern Europe (Poland/Romania)",
        "industry": "EU Supply Chain",
        "growth_pct": 20,
        "routes": ["Transatlantic", "Med Hub-Asia"],
        "note": "EU manufacturing reshoring \u2014 auto components, electronics, logistics hubs",
        "rating": "MODERATE",
        "rating_color": C_ACCENT,
        "investment_bn": 18,
        "companies": "LG, Volkswagen, Amazon, IKEA",
    },
    {
        "flag": "MA",
        "country": "Morocco / North Africa",
        "industry": "EU Textile & Automotive",
        "growth_pct": 30,
        "routes": ["North Africa-Europe", "Med Hub-Asia"],
        "note": "Proximity to EU markets \u2014 Renault/Stellantis production, textile OEM",
        "rating": "MODERATE",
        "rating_color": C_ACCENT,
        "investment_bn": 12,
        "companies": "Renault, Stellantis, H&M, Inditex",
    },
    {
        "flag": "BD",
        "country": "Bangladesh",
        "industry": "Apparel & RMG",
        "growth_pct": 10,
        "routes": ["South Asia-Europe", "Asia-Europe"],
        "note": "Low-cost apparel manufacturing \u2014 H&M, Zara, PVH supply chains",
        "rating": "LOW-MOD",
        "rating_color": C_WARN,
        "investment_bn": 6,
        "companies": "H&M, Zara, PVH, Gap",
    },
]

# Unicode flag emojis by 2-letter country code
_FLAG_MAP: dict[str, str] = {
    "VN": "\U0001f1fb\U0001f1f3",
    "MX": "\U0001f1f2\U0001f1fd",
    "IN": "\U0001f1ee\U0001f1f3",
    "PL": "\U0001f1f5\U0001f1f1",
    "MA": "\U0001f1f2\U0001f1e6",
    "BD": "\U0001f1e7\U0001f1e9",
}


def _render_reshoring_tracker() -> None:
    logger.debug("Rendering reshoring tracker cards")

    cols = st.columns(3)

    for idx, trend in enumerate(_RESHORING_TRENDS):
        col = cols[idx % 3]
        flag_emoji = _FLAG_MAP.get(trend["flag"], "")
        routes_str = " | ".join(trend["routes"])
        growth_bar_pct = min(100, trend["growth_pct"] * 2)  # scale 50% = full bar
        rating_color = trend["rating_color"]

        card_html = (
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:12px; padding:16px 18px; margin-bottom:12px; height:100%">'

            # Flag + Country header
            '<div style="display:flex; align-items:center; gap:10px; margin-bottom:10px">'
            '<span style="font-size:1.8rem; line-height:1">' + flag_emoji + "</span>"
            '<div>'
            '<div style="font-size:0.90rem; font-weight:700; color:' + C_TEXT + '">'
            + trend["country"] + "</div>"
            '<div style="font-size:0.75rem; color:' + C_TEXT2 + '">' + trend["industry"] + "</div>"
            "</div></div>"

            # Growth metric
            '<div style="margin-bottom:8px">'
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px">'
            '<span style="font-size:0.72rem; color:' + C_TEXT3 + '; text-transform:uppercase; letter-spacing:0.07em">YoY Capacity Growth</span>'
            '<span style="font-size:1.0rem; font-weight:800; color:' + rating_color + '">+'
            + str(trend["growth_pct"]) + "%</span>"
            "</div>"
            '<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:5px">'
            '<div style="width:' + str(growth_bar_pct) + '%; height:100%; background:' + rating_color + '; border-radius:4px"></div>'
            "</div></div>"

            # Opportunity rating
            '<div style="margin-bottom:8px">'
            '<span style="font-size:0.68rem; font-weight:700; color:' + rating_color + ';'
            ' background:' + rating_color + '18; border:1px solid ' + rating_color + '44;'
            ' border-radius:4px; padding:2px 7px; letter-spacing:0.06em">'
            + trend["rating"] + " OPPORTUNITY"
            + "</span></div>"

            # Note
            '<div style="font-size:0.78rem; color:' + C_TEXT2 + '; line-height:1.45; margin-bottom:10px">'
            + trend["note"] + "</div>"

            # Impacted routes
            '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; border-top:1px solid '
            + C_BORDER + '; padding-top:8px; margin-top:4px">'
            "Routes: " + routes_str + "</div>"

            "</div>"
        )

        with col:
            st.markdown(card_html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Main render function
# ══════════════════════════════════════════════════════════════════════════════

def render(
    route_results: list,
    port_results: list,
    freight_data: dict,
    macro_data: dict,
    trade_data: dict,
) -> None:
    """Render the Trade War & Tariff Impact Simulator tab."""
    logger.info("Rendering Trade War tab")

    # ── Tab header ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="padding:16px 0 24px 0; border-bottom:1px solid rgba(255,255,255,0.06);'
        ' margin-bottom:24px">'
        '<div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.15em;'
        ' color:#475569; margin-bottom:6px">GEOPOLITICAL ANALYSIS</div>'
        '<div style="font-size:1.6rem; font-weight:900; color:#f1f5f9;'
        ' letter-spacing:-0.03em; line-height:1.1">Trade War & Tariff Impact Simulator</div>'
        '<div style="font-size:0.85rem; color:#64748b; margin-top:6px">'
        "Model the shipping market impact of US-China tariffs, trade diversion, and reshoring trends"
        "</div></div>",
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Alert Banner — active conflicts, affected trade value, escalation level
    # ══════════════════════════════════════════════════════════════════════════
    _render_alert_banner()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Affected Sectors Scorecard
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Affected Sectors Scorecard",
        "Impact scores for key goods categories — tariff rate, shipping volume change, reshoring pressure",
    )
    _render_sectors_scorecard()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Tariff Impact Matrix Heatmap
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Tariff Impact Matrix \u2014 Countries vs. Goods Sectors",
        "Heatmap of US tariff rates applied to key goods by origin country \u2014 colored by severity",
    )
    _render_tariff_matrix()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Trade Volume Before/After
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Trade Volume: Before vs. After Tariffs",
        "Annualized bilateral trade flow ($B/yr) per sector \u2014 pre- and post-tariff side-by-side bars",
    )
    _render_trade_volume_comparison()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Trade Route Diversion Scattergeo Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Trade Route Diversion Map",
        "Geographic Scattergeo view of how tariffs redirect cargo flows \u2014 origin ports, diversion hubs, destinations",
    )
    _render_diversion_scattergeo()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Winner / Loser Analysis
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Winner / Loser Analysis",
        "Which routes and ports benefit from trade diversion vs. suffer from volume collapse",
    )
    _render_winner_loser_analysis()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Global Tariff Heat Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Global Tariff Risk Heat Map",
        "2025-2026 tariff exposure by country \u2014 color scale: green (low) to red (high risk)",
    )
    _render_tariff_heatmap()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Scenario Builder
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Tariff Scenario Builder",
        "Adjust tariff parameters to preview live shipping market impacts",
    )
    _render_scenario_builder(route_results)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Trade Diversion Sankey
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Trade Diversion Analysis",
        "How elevated US-China tariffs redirect cargo flows through Vietnam and Mexico",
    )
    _render_trade_diversion_sankey()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Historical Timeline
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Historical Tariff Impact Events",
        "Key tariff escalation events overlaid with Trans-Pacific EB spot rate series",
    )
    _render_historical_timeline()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Reshoring Tracker
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Supply Chain Reshoring Tracker",
        "Major manufacturing relocation trends and their impact on shipping lanes",
    )
    _render_reshoring_tracker()

    logger.info("Trade War tab render complete")
