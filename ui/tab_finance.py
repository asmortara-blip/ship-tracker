"""
tab_finance.py
==============
Trade Finance Dashboard tab for the Ship Tracker application.

render(freight_data, macro_data, route_results, stock_data) is the public
entry point.

Sections
--------
0.  NEW — Trade Finance Intelligence Banner  — L/C volumes, financing costs,
    credit availability KPI cards + quick-glance summary bar
1.  Trade Finance Overview            — L/C volumes, trade credit growth, financing costs
2.  Financing Cost by Route           — cost of trade financing per O/D pair
3.  Interest Rate Impact Model        — rate suppression + interactive what-if slider
4.  Bank Trade Finance Availability   — lender activity, credit availability trends
5.  Documentary Credit vs Open Account — shift toward open-account terms (2015-2026)
6.  Trade Finance Gap Analysis        — SME vs large-corp financing access
7.  FX Hedging Costs                  — forward rates vs spot for major trade currencies
8.  Supply Chain Finance Programs     — SCF adoption rates and cost savings
9.  Credit Availability Map           — choropleth: green = easy, red = tight
10. De-dollarization Monitor          — USD trade share decline + CNY growth
11. Sanctions Impact Tracker          — route-level sanctions card summary
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from loguru import logger
import streamlit as st

from processing.trade_finance import (
    TradeFinanceIndicator,
    TradeFinanceRiskScore,
    build_trade_finance_indicators,
    compute_trade_finance_composite,
    compute_regional_finance_risk,
    compute_interest_rate_impact_on_shipping,
)
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_MACRO,
    _hex_to_rgba as _rgba,
    section_header,
)

# ---------------------------------------------------------------------------
# Local colour helpers
# ---------------------------------------------------------------------------

C_WARN   = C_MOD
C_DANGER = C_LOW
C_BULL   = C_HIGH
C_PURPLE = "#8b5cf6"
C_CYAN   = C_MACRO

_SIGNAL_COLOR: Dict[str, str] = {
    "BULLISH":  C_HIGH,
    "BEARISH":  C_LOW,
    "NEUTRAL":  C_TEXT3,
}

_SIGNAL_ARROW: Dict[str, str] = {
    "BULLISH":  "▲",
    "BEARISH":  "▼",
    "NEUTRAL":  "—",
}

_SEVERITY_COLOR: dict[str, str] = {
    "CRITICAL": "#ef4444",
    "HIGH":     "#f97316",
    "MODERATE": "#f59e0b",
    "LOW":      "#64748b",
}


# ---------------------------------------------------------------------------
# Shared layout helpers
# ---------------------------------------------------------------------------

def _hr() -> None:
    st.markdown(
        "<hr style='border:none; border-top:1px solid rgba(255,255,255,0.07);"
        " margin:32px 0'>",
        unsafe_allow_html=True,
    )


def _subheading(text: str, color: str = "") -> None:
    c = color or C_TEXT2
    st.markdown(
        f'<div style="font-size:0.72rem; font-weight:700; color:{c};'
        f' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px">'
        f'{text}</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(
    label: str,
    value: str,
    sub: str = "",
    accent: str = "",
    full_width: bool = False,
) -> None:
    acc = accent or C_ACCENT
    width_style = "width:100%;" if full_width else ""
    st.markdown(
        f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
        f' border-top:2px solid {acc}; border-radius:10px; padding:16px 18px;'
        f' text-align:center; {width_style}">'
        f'<div style="font-size:0.62rem; font-weight:700; color:{C_TEXT3};'
        f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px">{label}</div>'
        f'<div style="font-size:1.95rem; font-weight:800; color:{C_TEXT};'
        f' font-variant-numeric:tabular-nums; line-height:1.1">{value}</div>'
        f'<div style="font-size:0.70rem; color:{C_TEXT3}; margin-top:3px">{sub}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _insight_box(text: str, color: str = "", label: str = "Key Insight") -> None:
    c = color or C_ACCENT
    st.markdown(
        f'<div style="background:{_rgba(c, 0.06)}; border:1px solid {_rgba(c, 0.22)};'
        f' border-left:3px solid {c}; border-radius:8px; padding:12px 16px;'
        f' font-size:0.82rem; color:{C_TEXT2}; margin-top:10px">'
        f'<strong style="color:{c}">{label}:</strong>&nbsp;{text}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _latest_macro_value(macro_data: dict, series_id: str) -> float | None:
    """Extract the most recent float value from a FRED dataframe."""
    df = macro_data.get(series_id)
    if df is None or df.empty or "value" not in df.columns:
        return None
    v = df["value"].dropna()
    return float(v.iloc[-1]) if not v.empty else None


def _plotly_defaults(fig: go.Figure, height: int = 340) -> go.Figure:
    """Apply consistent dark theme layout to a plotly figure."""
    fig.update_layout(
        template="plotly_dark",
        height=height,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=24, b=24, l=10, r=10),
        font=dict(family="Inter, sans-serif", color=C_TEXT2),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=11),
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(color=C_TEXT2, size=11))
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(color=C_TEXT2, size=11))
    return fig


# ---------------------------------------------------------------------------
# Static data tables
# ---------------------------------------------------------------------------

_LC_OA_DATA: dict = {
    "year":     [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026],
    "lc_pct":   [43,   41,   38,   36,   34,   33,   31,   29,   27,   26,   25,   23],
    "doc_coll": [14,   13,   13,   12,   12,   11,   11,   10,   10,    9,    9,    8],
    "open_acc": [43,   46,   49,   52,   54,   56,   58,   61,   63,   65,   66,   69],
}

_DEDOLLAR_DATA: dict = {
    "year":      [2015, 2017, 2019, 2021, 2022, 2023, 2024, 2025, 2026],
    "usd_pct":   [85.4, 84.9, 83.8, 82.5, 81.2, 80.6, 80.1, 79.8, 79.2],
    "eur_pct":   [ 6.0,  6.1,  6.4,  6.8,  6.9,  7.0,  7.1,  7.2,  7.2],
    "cny_pct":   [ 1.5,  1.8,  2.1,  2.7,  3.1,  3.7,  4.2,  4.6,  5.1],
    "other_pct": [ 7.1,  7.2,  7.7,  8.0,  8.8,  8.7,  8.6,  8.4,  8.5],
}

_SANCTIONS_DATA: list[dict] = [
    {
        "jurisdiction": "Russia",
        "mechanism": "SWIFT exclusion · SDN asset freeze · EU import bans",
        "shipping_impact": (
            "Major Russian ports (Novorossiysk, St. Petersburg, Vladivostok) face"
            " vessel withdrawal by western carriers. Trans-Atlantic and Baltic routes"
            " have absorbed ~5% diverted commodity volumes rerouted via Turkey/India."
        ),
        "diverted_vol_pct": 5.0,
        "affected_routes": ["BSEA_TRANSIT", "EUROPE_RUSSIA", "ARCTIC_ROUTE"],
        "severity": "CRITICAL",
        "in_force_since": "2022-02-28",
    },
    {
        "jurisdiction": "Iran",
        "mechanism": "US OFAC secondary sanctions · SWIFT exclusion (Iranian banks)",
        "shipping_impact": (
            "Iranian oil tankers use shadow fleet / flag-of-convenience vessels."
            " Strait of Hormuz insurance premiums elevated. Gulf carriers avoid"
            " Iranian port calls. Estimate 0.8M bbl/day oil trade rerouted."
        ),
        "diverted_vol_pct": 2.5,
        "affected_routes": ["HORMUZ_TRANSIT", "MIDEAST_GULF", "INDIA_WEST"],
        "severity": "HIGH",
        "in_force_since": "2018-11-05",
    },
    {
        "jurisdiction": "Cuba",
        "mechanism": "US embargo · OFAC vessel/port restrictions",
        "shipping_impact": (
            "Minimal direct shipping impact — Cuba trades primarily with China,"
            " Russia, and EU via non-US carriers. US-flagged vessels and those"
            " calling Cuban ports face OFAC 180-day bar on US port entry."
        ),
        "diverted_vol_pct": 0.2,
        "affected_routes": ["CARIB_WEST"],
        "severity": "LOW",
        "in_force_since": "1962-02-07",
    },
    {
        "jurisdiction": "Venezuela",
        "mechanism": "US OFAC oil sector sanctions · secondary sanctions on financiers",
        "shipping_impact": (
            "Venezuelan crude export volumes suppressed; tanker operators face"
            " secondary sanction risk. PDVSA cargoes handled via shadow fleet."
            " Minimal mainstream container shipping impact."
        ),
        "diverted_vol_pct": 0.4,
        "affected_routes": ["CARIB_WEST", "LATAM_NORTH"],
        "severity": "MODERATE",
        "in_force_since": "2019-01-28",
    },
    {
        "jurisdiction": "Belarus",
        "mechanism": "EU/US/UK sanctions following 2020 election · partial SWIFT",
        "shipping_impact": (
            "Belarusian potash and fertiliser export routes through Lithuanian/Latvian"
            " ports blocked; rerouted via Russian ports. Rail and road freight impacted."
        ),
        "diverted_vol_pct": 1.2,
        "affected_routes": ["EUROPE_RUSSIA", "BSEA_TRANSIT"],
        "severity": "MODERATE",
        "in_force_since": "2021-06-21",
    },
]

# Route financing cost data (basis points over SOFR)
_ROUTE_FINANCE_DATA: list[dict] = [
    {"route": "China → USA (Trans-Pacific)", "origin": "CN", "dest": "US",
     "cost_bps": 85,  "change_bps": +12, "risk": "MODERATE", "lc_share_pct": 24,
     "cargo_value_pct": 1.42},
    {"route": "China → Europe (Asia-Europe)", "origin": "CN", "dest": "DE",
     "cost_bps": 78,  "change_bps": +9,  "risk": "MODERATE", "lc_share_pct": 22,
     "cargo_value_pct": 1.30},
    {"route": "Europe → USA (Trans-Atlantic)", "origin": "DE", "dest": "US",
     "cost_bps": 42,  "change_bps": +5,  "risk": "LOW",      "lc_share_pct": 12,
     "cargo_value_pct": 0.70},
    {"route": "India → Europe (Suez)", "origin": "IN", "dest": "DE",
     "cost_bps": 110, "change_bps": +28, "risk": "HIGH",     "lc_share_pct": 38,
     "cargo_value_pct": 1.83},
    {"route": "Brazil → China (LATAM-Asia)", "origin": "BR", "dest": "CN",
     "cost_bps": 135, "change_bps": +18, "risk": "HIGH",     "lc_share_pct": 45,
     "cargo_value_pct": 2.25},
    {"route": "SE Asia → USA (ASEAN-US)", "origin": "VN", "dest": "US",
     "cost_bps": 92,  "change_bps": +15, "risk": "MODERATE", "lc_share_pct": 28,
     "cargo_value_pct": 1.53},
    {"route": "Middle East → Asia (Hormuz)", "origin": "SA", "dest": "CN",
     "cost_bps": 148, "change_bps": +35, "risk": "CRITICAL", "lc_share_pct": 62,
     "cargo_value_pct": 2.47},
    {"route": "Africa → Europe (Intra-Africa)", "origin": "NG", "dest": "DE",
     "cost_bps": 195, "change_bps": +22, "risk": "CRITICAL", "lc_share_pct": 71,
     "cargo_value_pct": 3.25},
    {"route": "Intra-Asia (Short sea)", "origin": "SG", "dest": "JP",
     "cost_bps": 55,  "change_bps": +6,  "risk": "LOW",      "lc_share_pct": 18,
     "cargo_value_pct": 0.92},
    {"route": "USA → Latin America", "origin": "US", "dest": "MX",
     "cost_bps": 68,  "change_bps": +8,  "risk": "MODERATE", "lc_share_pct": 20,
     "cargo_value_pct": 1.13},
]

# Bank trade finance activity
_BANK_TF_DATA: list[dict] = [
    {"bank": "HSBC",             "region": "Global",      "tf_vol_bn": 285, "yoy_chg": +4.2,  "credit_avail": "HIGH",     "specialty": "Asia Trade Finance"},
    {"bank": "Citibank",         "region": "Global",      "tf_vol_bn": 242, "yoy_chg": +2.8,  "credit_avail": "HIGH",     "specialty": "Transaction Banking"},
    {"bank": "Deutsche Bank",    "region": "Europe/Asia", "tf_vol_bn": 198, "yoy_chg": -1.2,  "credit_avail": "MODERATE", "specialty": "European Trade"},
    {"bank": "BNP Paribas",      "region": "Europe/MENA", "tf_vol_bn": 187, "yoy_chg": +3.5,  "credit_avail": "HIGH",     "specialty": "Commodity Finance"},
    {"bank": "Standard Chartered","region": "Asia/Africa", "tf_vol_bn": 156, "yoy_chg": +6.1,  "credit_avail": "HIGH",     "specialty": "Emerging Markets"},
    {"bank": "JPMorgan",         "region": "Americas",    "tf_vol_bn": 143, "yoy_chg": +1.9,  "credit_avail": "MODERATE", "specialty": "Supply Chain Finance"},
    {"bank": "Societe Generale", "region": "Europe/Africa","tf_vol_bn": 112, "yoy_chg": -3.4,  "credit_avail": "LOW",      "specialty": "Commodity Trade"},
    {"bank": "ING",              "region": "Europe",      "tf_vol_bn": 108, "yoy_chg": +0.8,  "credit_avail": "MODERATE", "specialty": "Structured Trade"},
    {"bank": "DBS",              "region": "Asia-Pacific", "tf_vol_bn": 94,  "yoy_chg": +8.3,  "credit_avail": "HIGH",     "specialty": "ASEAN Trade"},
    {"bank": "Mizuho",           "region": "Asia",        "tf_vol_bn": 88,  "yoy_chg": +2.1,  "credit_avail": "MODERATE", "specialty": "Japan Supply Chain"},
]

# SME vs large corp financing access
_FINANCE_GAP_DATA: dict = {
    "year":         [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026],
    "sme_gap_bn":   [1500, 1520, 1680, 1590, 1720, 1810, 1850, 1900, 1950],
    "corp_access":  [88,   89,   84,   91,   87,   86,   85,   85,   84],   # % fully funded
    "sme_access":   [58,   57,   48,   54,   51,   49,   48,   47,   45],   # % fully funded
    "rejection_rate_sme": [28, 29, 38, 31, 35, 37, 38, 39, 41],            # %
}

# FX hedging cost data (annual cost of hedge as % of notional)
_FX_HEDGE_DATA: list[dict] = [
    {"pair": "USD/CNY", "spot": 7.24,  "fwd_3m": 7.19,  "hedge_cost_pct": 2.8,  "vol_30d": 4.1,  "trend": "STABLE"},
    {"pair": "USD/EUR", "spot": 0.922, "fwd_3m": 0.918, "hedge_cost_pct": 1.1,  "vol_30d": 5.8,  "trend": "TIGHTENING"},
    {"pair": "USD/JPY", "spot": 149.2, "fwd_3m": 147.8, "hedge_cost_pct": 3.7,  "vol_30d": 7.2,  "trend": "WIDENING"},
    {"pair": "USD/KRW", "spot": 1328,  "fwd_3m": 1319,  "hedge_cost_pct": 2.4,  "vol_30d": 5.5,  "trend": "STABLE"},
    {"pair": "USD/INR", "spot": 83.4,  "fwd_3m": 82.9,  "hedge_cost_pct": 1.9,  "vol_30d": 3.2,  "trend": "STABLE"},
    {"pair": "USD/BRL", "spot": 5.08,  "fwd_3m": 5.21,  "hedge_cost_pct": 9.8,  "vol_30d": 12.4, "trend": "WIDENING"},
    {"pair": "USD/SGD", "spot": 1.344, "fwd_3m": 1.341, "hedge_cost_pct": 0.9,  "vol_30d": 3.8,  "trend": "STABLE"},
    {"pair": "USD/AED", "spot": 3.673, "fwd_3m": 3.673, "hedge_cost_pct": 0.05, "vol_30d": 0.1,  "trend": "PEGGED"},
]

# Supply chain finance adoption data
_SCF_DATA: dict = {
    "year":          [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026],
    "adoption_pct":  [18,   22,   27,   34,   39,   44,   49,   54,   58],
    "vol_outstanding_bn": [520, 640, 780, 950, 1120, 1290, 1480, 1680, 1850],
    "cost_saving_bps": [42,  45,  48,  52,  55,  58,  62,  65,  68],
    "supplier_onboard_pct": [12, 16, 21, 28, 33, 38, 43, 48, 52],
}

_SCF_PROGRAMS: list[dict] = [
    {"program": "Walmart Supplier Finance",  "vol_bn": 12.4, "suppliers": 4200, "saving_bps": 85, "sector": "Retail"},
    {"program": "Amazon Supply Chain",       "vol_bn": 9.8,  "suppliers": 3100, "saving_bps": 72, "sector": "E-Commerce"},
    {"program": "Apple Supplier Financing",  "vol_bn": 8.2,  "suppliers": 1800, "saving_bps": 68, "sector": "Technology"},
    {"program": "Toyota Production Finance", "vol_bn": 6.5,  "suppliers": 2900, "saving_bps": 91, "sector": "Automotive"},
    {"program": "IKEA Supply Chain Finance", "vol_bn": 3.8,  "suppliers": 1600, "saving_bps": 78, "sector": "Retail"},
    {"program": "Unilever Supplier Program", "vol_bn": 3.2,  "suppliers": 5400, "saving_bps": 64, "sector": "FMCG"},
]


# ---------------------------------------------------------------------------
# Section 0 (NEW) — Trade Finance Intelligence Banner
# ---------------------------------------------------------------------------

def _render_finance_banner(macro_data: dict) -> None:
    """Top-of-tab banner: L/C volumes, financing costs, credit availability as KPI cards,
    then a financing-cost-per-route bar chart, interest-rate impact time series,
    documentary credit vs open account trend, and FX hedging cost table — all as a
    compact overview before the full detail sections below."""

    # ── Page header ──────────────────────────────────────────────────────────
    st.markdown("""
    <div style="padding: 16px 0 24px 0; border-bottom: 1px solid rgba(255,255,255,0.06); margin-bottom: 24px">
        <div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.15em;
                    color:#475569; margin-bottom:6px">TRADE FINANCE INTELLIGENCE</div>
        <div style="font-size:1.6rem; font-weight:900; color:#f1f5f9; letter-spacing:-0.03em; line-height:1.1">
            Trade Finance Dashboard
        </div>
        <div style="font-size:0.85rem; color:#64748b; margin-top:6px">
            Letter of credit volumes, financing costs, FX hedging, credit availability — and their impact on global shipping demand
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── KPI Row 1: headline metrics ───────────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
        " color:#64748b; margin-bottom:10px; font-weight:700'>Key Trade Finance Metrics</div>",
        unsafe_allow_html=True,
    )

    dgs10_val = _latest_macro_value(macro_data, "DGS10")
    current_rate = dgs10_val if dgs10_val is not None else 4.45
    delta_rate = max(0, current_rate - 2.0)
    added_lc_cost = int(round(delta_rate * 28, 0))

    # Credit availability index (latest from static quarterly series)
    avail_idx_latest = 66  # Q1-26
    avail_color = C_HIGH if avail_idx_latest >= 60 else C_MOD if avail_idx_latest >= 45 else C_LOW

    # Global LC volume trend
    lc_vol_latest = 3.55  # $T projected 2026
    lc_yoy = "+4.4%"

    # Blended financing cost
    avg_bps = round(sum(r["cost_bps"] for r in _ROUTE_FINANCE_DATA) / len(_ROUTE_FINANCE_DATA))

    # Highest-cost route
    worst_route = max(_ROUTE_FINANCE_DATA, key=lambda x: x["cost_bps"])

    b1, b2, b3, b4, b5, b6 = st.columns(6)
    with b1:
        _kpi_card("Global LC Volume", f"${lc_vol_latest}T", f"2026 proj. · {lc_yoy} YoY", C_ACCENT)
    with b2:
        _kpi_card("Avg Financing Cost", f"{avg_bps} bps", "blended, over SOFR", C_MOD)
    with b3:
        rate_src = "FRED live" if dgs10_val else "estimate"
        _kpi_card("10Y Treasury", f"{current_rate:.2f}%", rate_src, C_ACCENT)
    with b4:
        _kpi_card("Added LC Cost", f"+${added_lc_cost}B", "vs 2.0% neutral rate/yr", C_LOW)
    with b5:
        _kpi_card("Credit Availability", f"{avail_idx_latest}/100", "Q1-2026 bank index", avail_color)
    with b6:
        _kpi_card("Finance Gap (SME)", "$1.95T", "unmet global demand", C_LOW)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── Financing cost by route — compact bar chart ───────────────────────────
    st.markdown(
        "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
        " color:#64748b; margin-bottom:10px; font-weight:700'>"
        "Financing Cost by Route (bps over SOFR) vs Cargo Value Cost (%)</div>",
        unsafe_allow_html=True,
    )

    routes_sorted = sorted(_ROUTE_FINANCE_DATA, key=lambda x: x["cost_bps"])
    route_labels  = [r["route"] for r in routes_sorted]
    cost_vals     = [r["cost_bps"] for r in routes_sorted]
    cargo_pct     = [r["cargo_value_pct"] for r in routes_sorted]
    bar_colors    = [_SEVERITY_COLOR.get(r["risk"], C_TEXT3) for r in routes_sorted]

    fig_route = make_subplots(
        rows=1, cols=2,
        column_widths=[0.6, 0.4],
        subplot_titles=["All-In Financing Cost (bps over SOFR)", "As % of Cargo Value"],
    )
    fig_route.add_trace(go.Bar(
        x=cost_vals,
        y=route_labels,
        orientation="h",
        marker_color=bar_colors,
        marker_line_width=0,
        text=[f"{v} bps" for v in cost_vals],
        textposition="outside",
        textfont=dict(size=9, color="#94a3b8"),
        hovertemplate="<b>%{y}</b><br>Cost: %{x} bps<extra></extra>",
        showlegend=False,
    ), row=1, col=1)
    cargo_colors = [C_LOW if v > 2.5 else C_MOD if v > 1.5 else C_HIGH for v in cargo_pct]
    fig_route.add_trace(go.Bar(
        x=cargo_pct,
        y=route_labels,
        orientation="h",
        marker_color=cargo_colors,
        marker_line_width=0,
        text=[f"{v:.2f}%" for v in cargo_pct],
        textposition="outside",
        textfont=dict(size=9, color="#94a3b8"),
        hovertemplate="<b>%{y}</b><br>Cost: %{x:.2f}% of cargo value<extra></extra>",
        showlegend=False,
    ), row=1, col=2)
    fig_route = _plotly_defaults(fig_route, height=380)
    fig_route.update_xaxes(title="bps over SOFR", row=1, col=1, ticksuffix=" bps")
    fig_route.update_xaxes(title="% of cargo value", row=1, col=2, ticksuffix="%")
    fig_route.update_yaxes(tickfont=dict(size=10))
    fig_route.update_layout(margin=dict(t=36, b=10, l=10, r=80))
    st.plotly_chart(fig_route, use_container_width=True, key="banner_route_cost_chart")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── Interest rate impact over time ────────────────────────────────────────
    st.markdown(
        "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
        " color:#64748b; margin-bottom:10px; font-weight:700'>"
        "Interest Rate Impact on Trade Finance Costs (2019–2026)</div>",
        unsafe_allow_html=True,
    )

    hist_years  = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
    hist_rates  = [2.09, 0.91, 1.45, 2.97, 3.96, 4.23, 4.38, 4.45]
    hist_lc_add = [max(0.0, (r - 2.0) * 28) for r in hist_rates]
    # blended financing cost proxy: SOFR (≈rate) + avg_bps spread
    hist_all_in = [r * 100 + avg_bps for r in hist_rates]  # bps total

    fig_rate_ts = make_subplots(specs=[[{"secondary_y": True}]])
    fig_rate_ts.add_trace(go.Scatter(
        x=hist_years, y=hist_rates,
        name="10Y Treasury (%)",
        mode="lines+markers",
        line=dict(color=C_ACCENT, width=2.5),
        marker=dict(size=7),
        hovertemplate="Year: %{x}<br>Rate: %{y:.2f}%<extra></extra>",
    ), secondary_y=False)
    fig_rate_ts.add_trace(go.Bar(
        x=hist_years, y=hist_lc_add,
        name="Added Annual LC Cost ($B)",
        marker_color=[_rgba(C_LOW, 0.55) if v > 0 else _rgba(C_HIGH, 0.4) for v in hist_lc_add],
        hovertemplate="Year: %{x}<br>Added LC Cost: $%{y:.0f}B<extra></extra>",
    ), secondary_y=True)
    fig_rate_ts.add_trace(go.Scatter(
        x=hist_years, y=hist_all_in,
        name="All-In Trade Finance Cost (bps)",
        mode="lines",
        line=dict(color=C_MOD, width=1.8, dash="dot"),
        hovertemplate="Year: %{x}<br>All-In Cost: %{y:.0f} bps<extra></extra>",
    ), secondary_y=True)
    fig_rate_ts = _plotly_defaults(fig_rate_ts, height=300)
    fig_rate_ts.update_yaxes(
        title_text="10Y Treasury (%)", secondary_y=False,
        ticksuffix="%", gridcolor="rgba(255,255,255,0.05)",
    )
    fig_rate_ts.update_yaxes(
        title_text="Added LC Cost ($B) / All-In (bps)", secondary_y=True,
        showgrid=False,
    )
    fig_rate_ts.update_layout(margin=dict(t=16, b=24, l=10, r=10))
    st.plotly_chart(fig_rate_ts, use_container_width=True, key="banner_rate_impact_ts")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── Documentary credit vs open account trend ──────────────────────────────
    col_lc, col_fx = st.columns([3, 2])

    with col_lc:
        st.markdown(
            "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:#64748b; margin-bottom:10px; font-weight:700'>"
            "Documentary Credit vs Open Account (2015–2026)</div>",
            unsafe_allow_html=True,
        )
        years_lc = _LC_OA_DATA["year"]
        lc_pct   = _LC_OA_DATA["lc_pct"]
        oa_pct   = _LC_OA_DATA["open_acc"]
        dc_pct   = _LC_OA_DATA["doc_coll"]

        fig_lc_oa = go.Figure()
        fig_lc_oa.add_trace(go.Scatter(
            x=years_lc, y=oa_pct, name="Open Account",
            mode="lines+markers",
            line=dict(color=C_HIGH, width=2.5),
            marker=dict(size=6),
            fill="tozeroy", fillcolor=_rgba(C_HIGH, 0.08),
            hovertemplate="Year: %{x}<br>Open Account: %{y}%<extra></extra>",
        ))
        fig_lc_oa.add_trace(go.Scatter(
            x=years_lc, y=dc_pct, name="Doc. Collections",
            mode="lines+markers",
            line=dict(color=C_MOD, width=1.8),
            marker=dict(size=5),
            hovertemplate="Year: %{x}<br>Doc. Collections: %{y}%<extra></extra>",
        ))
        fig_lc_oa.add_trace(go.Scatter(
            x=years_lc, y=lc_pct, name="Letter of Credit",
            mode="lines+markers",
            line=dict(color=C_ACCENT, width=2.5),
            marker=dict(size=6),
            hovertemplate="Year: %{x}<br>Letter of Credit: %{y}%<extra></extra>",
        ))
        fig_lc_oa = _plotly_defaults(fig_lc_oa, height=280)
        fig_lc_oa.update_yaxes(title="Share (%)", ticksuffix="%", range=[0, 80])
        fig_lc_oa.update_xaxes(title="Year", dtick=2)
        fig_lc_oa.update_layout(margin=dict(t=16, b=24, l=10, r=10))
        st.plotly_chart(fig_lc_oa, use_container_width=True, key="banner_lc_oa_trend")

    with col_fx:
        st.markdown(
            "<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            " color:#64748b; margin-bottom:10px; font-weight:700'>"
            "FX Hedging Cost — Forward vs Spot (Major Trade Currencies)</div>",
            unsafe_allow_html=True,
        )
        trend_color = {"STABLE": C_HIGH, "TIGHTENING": C_CYAN, "WIDENING": C_LOW, "PEGGED": C_TEXT3}
        trend_icon  = {"STABLE": "→", "TIGHTENING": "↘", "WIDENING": "↗", "PEGGED": "="}
        # Table header
        st.markdown(
            "<div style='display:grid; grid-template-columns:80px 70px 70px 60px 80px;"
            " gap:4px; padding:4px 6px; background:rgba(255,255,255,0.04);"
            " border-radius:6px; margin-bottom:6px'>"
            "<span style='font-size:0.60rem; color:#64748b; font-weight:700'>PAIR</span>"
            "<span style='font-size:0.60rem; color:#64748b; font-weight:700'>SPOT</span>"
            "<span style='font-size:0.60rem; color:#64748b; font-weight:700'>3M FWD</span>"
            "<span style='font-size:0.60rem; color:#64748b; font-weight:700'>COST%</span>"
            "<span style='font-size:0.60rem; color:#64748b; font-weight:700'>TREND</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        for h in sorted(_FX_HEDGE_DATA, key=lambda x: x["hedge_cost_pct"], reverse=True):
            tc = trend_color.get(h["trend"], C_TEXT3)
            ti = trend_icon.get(h["trend"], "?")
            cost_c = C_LOW if h["hedge_cost_pct"] > 3 else C_MOD if h["hedge_cost_pct"] > 1.5 else C_HIGH
            # format spot/fwd nicely
            spot_str = f"{h['spot']}" if isinstance(h["spot"], float) and h["spot"] < 10 else f"{h['spot']:.1f}" if isinstance(h["spot"], float) and h["spot"] < 200 else f"{h['spot']:.0f}"
            fwd_str  = f"{h['fwd_3m']}" if isinstance(h["fwd_3m"], float) and h["fwd_3m"] < 10 else f"{h['fwd_3m']:.1f}" if isinstance(h["fwd_3m"], float) and h["fwd_3m"] < 200 else f"{h['fwd_3m']:.0f}"
            st.markdown(
                f"<div style='display:grid; grid-template-columns:80px 70px 70px 60px 80px;"
                f" gap:4px; padding:6px 6px; border-bottom:1px solid rgba(255,255,255,0.04)'>"
                f"<span style='font-size:0.73rem; font-weight:700; color:{C_TEXT}'>{h['pair']}</span>"
                f"<span style='font-size:0.72rem; color:{C_TEXT2}'>{spot_str}</span>"
                f"<span style='font-size:0.72rem; color:{C_TEXT2}'>{fwd_str}</span>"
                f"<span style='font-size:0.72rem; font-weight:700; color:{cost_c}'>{h['hedge_cost_pct']:.2f}%</span>"
                f"<span style='font-size:0.68rem; color:{tc}; font-weight:600'>{ti} {h['trend']}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    _insight_box(
        f"With the 10Y Treasury at <strong style='color:{C_ACCENT}'>{current_rate:.2f}%</strong>,"
        f" global L/C costs are running <strong style='color:{C_LOW}'>+${added_lc_cost}B/yr</strong>"
        f" above neutral (2.0%) — pressuring emerging-market routes most, where financing costs"
        f" already exceed <strong>2.5%</strong> of cargo value (Africa-Europe, Hormuz corridor)."
        f" The 20 pp shift from L/C to open-account since 2015 amplifies cancellation risk"
        f" when credit tightens.",
        color=C_MOD, label="Intelligence Summary",
    )


# ---------------------------------------------------------------------------
# Section 1 — Trade Finance Overview
# ---------------------------------------------------------------------------

def _render_finance_overview(indicators: List[TradeFinanceIndicator]) -> None:
    section_header(
        "Trade Finance Overview",
        "Global letter of credit volumes, trade credit growth rates, and all-in"
        " financing costs — key leading indicators for shipping demand (6-12 wk lag)",
    )

    # Top-line KPI row
    c1, c2, c3, c4, c5 = st.columns(5)
    kpis = [
        (c1, "Global LC Volume", "$3.4T", "annualised, 2026 proj.", C_ACCENT),
        (c2, "Trade Credit Growth", "+4.8%", "YoY, March 2026", C_HIGH),
        (c3, "All-In Finance Cost", "SOFR+112bps", "blended average", C_MOD),
        (c4, "Finance Gap (SME)", "$1.95T", "unmet demand globally", C_LOW),
        (c5, "SCF Adoption", "58%", "of Fortune 500 cos.", C_CYAN),
    ]
    for col, label, val, sub, accent in kpis:
        with col:
            _kpi_card(label, val, sub, accent)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # If live indicators available, render composite score bar
    if indicators:
        composite = compute_trade_finance_composite(indicators)
        cs = composite["composite_score"]
        dom = composite["dominant_signal"]
        dom_color = _SIGNAL_COLOR.get(dom, C_TEXT3)
        pct_bar = round(cs * 100)

        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
            f' border-radius:10px; padding:14px 20px; display:flex;'
            f' align-items:center; gap:24px; margin-bottom:4px">'
            f'<div style="flex:0 0 auto; min-width:140px">'
            f'<div style="font-size:0.60rem; font-weight:700; color:{C_TEXT3};'
            f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:2px">'
            f'Composite Credit Score</div>'
            f'<div style="font-size:2.1rem; font-weight:800; color:{dom_color};'
            f' font-variant-numeric:tabular-nums; line-height:1.1">{pct_bar}/100</div>'
            f'<div style="font-size:0.70rem; font-weight:600; color:{dom_color}">{dom}</div>'
            f'</div>'
            f'<div style="flex:1 1 auto">'
            f'<div style="font-size:0.70rem; color:{C_TEXT2}; margin-bottom:8px">'
            f'Bullish: <strong style="color:{C_HIGH}">{composite["bullish_count"]}</strong>'
            f'&nbsp;&nbsp;Bearish: <strong style="color:{C_LOW}">{composite["bearish_count"]}</strong>'
            f'&nbsp;&nbsp;Neutral: <strong style="color:{C_TEXT3}">{composite["neutral_count"]}</strong>'
            f'</div>'
            f'<div style="background:rgba(255,255,255,0.07); border-radius:6px;'
            f' height:10px; overflow:hidden">'
            f'<div style="width:{pct_bar}%; height:100%; background:linear-gradient(90deg,'
            f' {_rgba(dom_color,0.5)},{dom_color}); border-radius:6px"></div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # LC volume trend mini chart
    years_lc = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
    lc_vol   = [2.61, 2.18, 2.75, 3.02, 3.15, 3.28, 3.40, 3.55]   # $T outstanding

    col_chart, col_info = st.columns([2, 1])
    with col_chart:
        fig_lc = go.Figure()
        fig_lc.add_trace(go.Scatter(
            x=years_lc, y=lc_vol,
            name="LC Volume ($T)",
            mode="lines+markers",
            line=dict(color=C_ACCENT, width=2.5),
            marker=dict(size=7, color=C_ACCENT, line=dict(color="white", width=1)),
            fill="tozeroy",
            fillcolor=_rgba(C_ACCENT, 0.10),
            hovertemplate="Year: %{x}<br>LC Volume: $%{y:.2f}T<extra></extra>",
        ))
        fig_lc.add_annotation(
            x=2020, y=2.18,
            text="COVID dip",
            showarrow=True, arrowhead=2, arrowcolor=C_MOD,
            ax=40, ay=-30,
            font=dict(size=9, color=C_MOD),
            bgcolor=_rgba(C_CARD, 0.9), borderpad=3,
        )
        fig_lc = _plotly_defaults(fig_lc, height=280)
        fig_lc.update_yaxes(title="Volume ($T)", tickprefix="$", ticksuffix="T")
        fig_lc.update_xaxes(title="Year", dtick=1)
        fig_lc.update_layout(showlegend=False, margin=dict(t=16, b=24, l=10, r=10))
        st.plotly_chart(fig_lc, use_container_width=True, key="finance_lc_volume_trend")
        _subheading("Global Letter of Credit Outstanding Volume ($T)")

    with col_info:
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        items = [
            ("Documentary LC", "$1.82T", C_ACCENT),
            ("Standby LC", "$1.12T", C_CYAN),
            ("Guarantees", "$0.41T", C_MOD),
            ("ICC Uniform Rules", "UCP 600", C_TEXT3),
            ("Avg LC Tenor", "90 days", C_TEXT3),
            ("Top Issuer", "HSBC / Citi", C_TEXT3),
        ]
        for lbl, val, c in items:
            st.markdown(
                f'<div style="display:flex; justify-content:space-between;'
                f' align-items:center; padding:7px 0;'
                f' border-bottom:1px solid rgba(255,255,255,0.05)">'
                f'<span style="font-size:0.73rem; color:{C_TEXT2}">{lbl}</span>'
                f'<span style="font-size:0.73rem; font-weight:700; color:{c}">{val}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Indicator detail expander
    if indicators:
        with st.expander("Live Indicator Details & Sources", expanded=False,
                         key="finance_overview_indicators"):
            rows_data = [{
                "Indicator":   ind.indicator_name,
                "Value":       ind.current_value,
                "YoY %":       ind.yoy_change_pct,
                "Signal":      ind.signal,
                "Lead (wks)":  ind.shipping_lead_weeks,
                "Source":      ind.data_source,
            } for ind in indicators]
            detail_df = pd.DataFrame(rows_data)
            st.dataframe(detail_df, use_container_width=True, hide_index=True)
            st.download_button(
                label="Download CSV",
                data=detail_df.to_csv(index=False).encode("utf-8"),
                file_name="trade_finance_indicators.csv",
                mime="text/csv",
                key="finance_overview_dl",
            )


# ---------------------------------------------------------------------------
# Section 2 — Financing Cost by Route
# ---------------------------------------------------------------------------

def _render_route_financing() -> None:
    section_header(
        "Financing Cost by Trade Route",
        "All-in trade financing cost (basis points over SOFR) and L/C usage share"
        " for major origin-destination pairs — higher cost = tighter credit = freight risk",
    )

    # Horizontal bar chart — cost by route
    routes_sorted = sorted(_ROUTE_FINANCE_DATA, key=lambda x: x["cost_bps"])
    route_labels = [r["route"] for r in routes_sorted]
    cost_vals    = [r["cost_bps"] for r in routes_sorted]
    bar_colors   = [_SEVERITY_COLOR.get(r["risk"], C_TEXT3) for r in routes_sorted]
    change_vals  = [r["change_bps"] for r in routes_sorted]

    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.62, 0.38],
        subplot_titles=["All-In Financing Cost (bps over SOFR)", "YoY Change (bps)"],
    )

    fig.add_trace(go.Bar(
        x=cost_vals,
        y=route_labels,
        orientation="h",
        marker_color=bar_colors,
        marker_line_width=0,
        hovertemplate="<b>%{y}</b><br>Cost: %{x} bps over SOFR<extra></extra>",
        showlegend=False,
    ), row=1, col=1)

    chg_colors = [C_LOW if v > 0 else C_HIGH for v in change_vals]
    fig.add_trace(go.Bar(
        x=change_vals,
        y=route_labels,
        orientation="h",
        marker_color=chg_colors,
        marker_line_width=0,
        hovertemplate="<b>%{y}</b><br>YoY Change: %{x:+d} bps<extra></extra>",
        showlegend=False,
    ), row=1, col=2)

    fig = _plotly_defaults(fig, height=380)
    fig.update_xaxes(title="bps over SOFR", row=1, col=1, ticksuffix=" bps")
    fig.update_xaxes(title="YoY change (bps)", row=1, col=2)
    fig.update_yaxes(tickfont=dict(size=10))
    fig.update_layout(margin=dict(t=36, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True, key="finance_route_cost_chart")

    # Route detail table with styled risk badges
    _subheading("Route Financing Detail")
    cols = st.columns([3, 1, 1, 1, 1])
    for hdr, col in zip(["Route", "Cost (bps)", "YoY Change", "Risk Level", "LC Share"], cols):
        col.markdown(
            f'<div style="font-size:0.62rem; font-weight:700; color:{C_TEXT3};'
            f' text-transform:uppercase; letter-spacing:0.06em">{hdr}</div>',
            unsafe_allow_html=True,
        )

    for r in sorted(_ROUTE_FINANCE_DATA, key=lambda x: x["cost_bps"], reverse=True):
        rc = _SEVERITY_COLOR.get(r["risk"], C_TEXT3)
        chg_str = f'+{r["change_bps"]}' if r["change_bps"] >= 0 else str(r["change_bps"])
        chg_color = C_LOW if r["change_bps"] > 0 else C_HIGH
        c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
        c1.markdown(
            f'<div style="font-size:0.75rem; color:{C_TEXT}; padding:4px 0">'
            f'{r["route"]}</div>', unsafe_allow_html=True)
        c2.markdown(
            f'<div style="font-size:0.75rem; color:{rc}; font-weight:700; padding:4px 0">'
            f'{r["cost_bps"]} bps</div>', unsafe_allow_html=True)
        c3.markdown(
            f'<div style="font-size:0.75rem; color:{chg_color}; font-weight:600;'
            f' padding:4px 0">{chg_str} bps</div>', unsafe_allow_html=True)
        c4.markdown(
            f'<div style="padding:4px 0"><span style="background:{_rgba(rc,0.15)};'
            f' color:{rc}; border:1px solid {_rgba(rc,0.3)}; padding:2px 8px;'
            f' border-radius:999px; font-size:0.60rem; font-weight:700">'
            f'{r["risk"]}</span></div>', unsafe_allow_html=True)
        c5.markdown(
            f'<div style="font-size:0.75rem; color:{C_TEXT2}; padding:4px 0">'
            f'{r["lc_share_pct"]}%</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="border-bottom:1px solid rgba(255,255,255,0.04)"></div>',
            unsafe_allow_html=True)

    _insight_box(
        "Emerging-market routes (Africa-Europe, Middle East-Asia) carry financing premiums"
        " 3-4x higher than intra-developed-market lanes. Rising financing costs on the"
        " Middle East Hormuz corridor (+35 bps YoY) are already suppressing spot"
        " booking volumes on Asia-Gulf tanker routes.",
        color=C_MOD, label="Shipping Impact",
    )


# ---------------------------------------------------------------------------
# Section 3 — Interest Rate Impact Model
# ---------------------------------------------------------------------------

def _render_rate_impact(macro_data: dict) -> None:
    section_header(
        "Interest Rate Impact on Trade Finance",
        "Higher rates compound trade financing costs — each 100 bps rate rise adds"
        " ~$28B to annual global L/C costs and suppresses container demand 6-12 months later",
    )

    dgs10_val = _latest_macro_value(macro_data, "DGS10")
    static_rate = 4.45
    current_rate = dgs10_val if dgs10_val is not None else static_rate
    rate_source = "DGS10 (FRED live)" if dgs10_val is not None else "static estimate (4.45%)"
    logger.info("tab_finance rate model: using rate={r:.2f}% source={s}", r=current_rate, s=rate_source)

    live_impact = compute_interest_rate_impact_on_shipping(current_rate)
    impact_val  = live_impact["estimated_demand_impact_pct"]
    cum_val     = live_impact["cumulative_impact_since_2022_pct"]

    impact_color = C_LOW if impact_val < 0 else C_HIGH
    cum_color    = C_LOW if cum_val < 0 else C_HIGH

    c1, c2, c3, c4 = st.columns(4)
    with c1: _kpi_card("10Y Treasury (DGS10)", f"{round(current_rate, 2)}%", rate_source, C_ACCENT)
    with c2: _kpi_card("Demand Suppression", f"{impact_val}%", "vs neutral rate baseline", impact_color)
    with c3: _kpi_card("Cumulative (since Mar 2022)", f"{cum_val}%", "vs 0.08% pre-hike", cum_color)
    with c4:
        # Derived: incremental LC cost at current rate vs 2% neutral
        delta_rate = max(0, current_rate - 2.0)
        lc_cost_add = round(delta_rate * 28, 0)
        _kpi_card("Added Annual LC Cost", f"+${int(lc_cost_add)}B", "vs 2.0% neutral rate", C_LOW)

    _insight_box(
        f"Fed rate hikes since March 2022 have suppressed container demand by an estimated"
        f" <strong style='color:{C_LOW}'>{abs(cum_val)}%</strong>, acting through elevated"
        f" inventory carrying costs, reduced import order frequency, and tighter trade credit."
        f" {live_impact['scenario_label']}.",
        color=C_LOW, label="Rate Cycle Impact",
    )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Dual-axis chart: rate history + demand impact ────────────────────────
    hist_years  = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
    hist_rates  = [2.09, 0.91, 1.45, 2.97, 3.96, 4.23, 4.38, 4.45]
    hist_impact = [r - 2.0 for r in hist_rates]
    hist_lc_add = [max(0, (r - 2.0) * 28) for r in hist_rates]

    fig_hist = make_subplots(specs=[[{"secondary_y": True}]])
    fig_hist.add_trace(go.Scatter(
        x=hist_years, y=hist_rates,
        name="10Y Treasury (%)",
        mode="lines+markers",
        line=dict(color=C_ACCENT, width=2.5),
        marker=dict(size=7),
        hovertemplate="Year: %{x}<br>Rate: %{y:.2f}%<extra></extra>",
    ), secondary_y=False)
    fig_hist.add_trace(go.Bar(
        x=hist_years, y=hist_lc_add,
        name="Added LC Cost ($B)",
        marker_color=[_rgba(C_LOW, 0.5) if v > 0 else _rgba(C_HIGH, 0.4) for v in hist_lc_add],
        hovertemplate="Year: %{x}<br>Added LC Cost: $%{y:.0f}B<extra></extra>",
    ), secondary_y=True)
    fig_hist = _plotly_defaults(fig_hist, height=300)
    fig_hist.update_yaxes(
        title_text="10Y Treasury (%)", secondary_y=False,
        ticksuffix="%", gridcolor="rgba(255,255,255,0.05)",
    )
    fig_hist.update_yaxes(
        title_text="Added Annual LC Cost ($B)", secondary_y=True,
        tickprefix="$", ticksuffix="B", showgrid=False,
    )
    fig_hist.update_layout(margin=dict(t=16, b=24, l=10, r=10))
    st.plotly_chart(fig_hist, use_container_width=True, key="finance_rate_history_chart")

    # ── Interactive what-if slider ────────────────────────────────────────────
    st.markdown(
        f'<div style="margin-top:4px; margin-bottom:8px; font-size:0.78rem;'
        f' font-weight:600; color:{C_TEXT2}">What-If Rate Scenario Modeller</div>',
        unsafe_allow_html=True,
    )
    scenario_rate = st.slider(
        "Model rate cut/hike to (%)",
        min_value=0.5, max_value=7.0,
        value=float(round(current_rate, 1)), step=0.25,
        help="Drag to model how a rate change affects estimated container demand and LC costs",
        key="finance_rate_slider",
    )
    scenario_rate = max(scenario_rate, 0.25)

    if abs(scenario_rate - current_rate) > 0.05:
        scenario_impact = compute_interest_rate_impact_on_shipping(scenario_rate)
        delta_demand    = round(
            scenario_impact["estimated_demand_impact_pct"] - impact_val, 2)
        delta_lc        = round((scenario_rate - current_rate) * 28, 1)
        delta_color     = C_HIGH if delta_demand >= 0 else C_LOW

        sc1, sc2 = st.columns(2)
        with sc1:
            _kpi_card(
                f"Demand Impact at {scenario_rate:.2f}%",
                f"{scenario_impact['estimated_demand_impact_pct']}%",
                f"Delta vs current: {'+'if delta_demand>=0 else''}{delta_demand} pp",
                delta_color,
            )
        with sc2:
            _kpi_card(
                f"LC Cost Change",
                f"{'+'if delta_lc>=0 else''}{delta_lc}B/yr",
                scenario_impact["scenario_label"],
                C_LOW if delta_lc > 0 else C_HIGH,
            )

    # Rate sensitivity bar chart
    rate_points = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]
    demand_vals = [
        compute_interest_rate_impact_on_shipping(r)["estimated_demand_impact_pct"]
        for r in rate_points
    ]
    bar_colors_r = [C_HIGH if v >= 0 else C_LOW for v in demand_vals]
    fig_rate = go.Figure(go.Bar(
        x=rate_points, y=demand_vals,
        marker_color=bar_colors_r,
        hovertemplate="Rate: %{x:.2f}%<br>Demand impact: %{y:.1f}%<extra></extra>",
    ))
    fig_rate.add_vline(x=current_rate, line_dash="dot", line_color=C_ACCENT, line_width=2,
                       annotation_text="Current", annotation_font=dict(color=C_ACCENT, size=10),
                       annotation_position="top")
    if abs(scenario_rate - current_rate) > 0.05:
        fig_rate.add_vline(x=scenario_rate, line_dash="dash", line_color=C_MOD, line_width=2,
                           annotation_text="Scenario", annotation_font=dict(color=C_MOD, size=10),
                           annotation_position="top right")
    fig_rate = _plotly_defaults(fig_rate, height=260)
    fig_rate.update_xaxes(title="Benchmark Rate (%)", ticksuffix="%")
    fig_rate.update_yaxes(title="Container Demand Impact (%)", ticksuffix="%",
                           zeroline=True, zerolinecolor="rgba(255,255,255,0.18)")
    fig_rate.update_layout(showlegend=False, margin=dict(t=16, b=24, l=10, r=10))
    st.plotly_chart(fig_rate, use_container_width=True, key="finance_rate_sensitivity_chart")


# ---------------------------------------------------------------------------
# Section 4 — Bank Trade Finance Availability
# ---------------------------------------------------------------------------

def _render_bank_availability() -> None:
    section_header(
        "Bank Trade Finance Availability",
        "Active lender volumes, credit availability by institution, and year-on-year"
        " lending trends — key barometer for trade credit tightening or easing",
    )

    total_vol  = sum(b["tf_vol_bn"] for b in _BANK_TF_DATA)
    high_avail = sum(1 for b in _BANK_TF_DATA if b["credit_avail"] == "HIGH")
    avg_yoy    = round(sum(b["yoy_chg"] for b in _BANK_TF_DATA) / len(_BANK_TF_DATA), 1)

    k1, k2, k3, k4 = st.columns(4)
    with k1: _kpi_card("Total TF Volume (Top 10)", f"${total_vol:,}B", "annualised", C_ACCENT)
    with k2: _kpi_card("High Availability Banks", str(high_avail), "of top 10 lenders", C_HIGH)
    with k3: _kpi_card("Avg YoY Volume Growth", f"{avg_yoy:+.1f}%", "across top 10", C_HIGH if avg_yoy > 0 else C_LOW)
    with k4: _kpi_card("Market Concentration", "Top 5 = 68%", "of global TF volumes", C_MOD)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # Chart: horizontal bar — TF volume, coloured by credit availability
    sorted_banks = sorted(_BANK_TF_DATA, key=lambda b: b["tf_vol_bn"])
    avail_color_map = {"HIGH": C_HIGH, "MODERATE": C_MOD, "LOW": C_LOW}
    b_colors = [avail_color_map.get(b["credit_avail"], C_TEXT3) for b in sorted_banks]

    col_chart, col_table = st.columns([3, 2])
    with col_chart:
        fig_banks = go.Figure()
        fig_banks.add_trace(go.Bar(
            x=[b["tf_vol_bn"] for b in sorted_banks],
            y=[b["bank"] for b in sorted_banks],
            orientation="h",
            marker_color=b_colors,
            marker_line_width=0,
            hovertemplate="<b>%{y}</b><br>Volume: $%{x}B<extra></extra>",
            showlegend=False,
        ))
        # YoY change scatter on secondary axis
        yoy_vals = [b["yoy_chg"] for b in sorted_banks]
        yoy_cols = [C_HIGH if v > 0 else C_LOW for v in yoy_vals]
        fig_banks.add_trace(go.Scatter(
            x=[b["tf_vol_bn"] + (b["tf_vol_bn"] * 0.02) for b in sorted_banks],
            y=[b["bank"] for b in sorted_banks],
            mode="text",
            text=[f" {v:+.1f}%" for v in yoy_vals],
            textfont=dict(
                size=10,
                color=yoy_cols,
            ),
            showlegend=False,
            hoverinfo="skip",
        ))

        # Legend items
        for label, color in [("High Avail.", C_HIGH), ("Moderate Avail.", C_MOD), ("Low Avail.", C_LOW)]:
            fig_banks.add_trace(go.Bar(
                x=[None], y=[None],
                orientation="h",
                name=label,
                marker_color=color,
                showlegend=True,
            ))

        fig_banks = _plotly_defaults(fig_banks, height=360)
        fig_banks.update_xaxes(title="TF Volume ($B)", tickprefix="$", ticksuffix="B")
        fig_banks.update_layout(margin=dict(t=16, b=24, l=10, r=10))
        st.plotly_chart(fig_banks, use_container_width=True, key="finance_bank_volume_chart")

    with col_table:
        _subheading("Lender Detail")
        for b in sorted(_BANK_TF_DATA, key=lambda x: x["tf_vol_bn"], reverse=True):
            ac = avail_color_map.get(b["credit_avail"], C_TEXT3)
            yoy_c = C_HIGH if b["yoy_chg"] > 0 else C_LOW
            st.markdown(
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
                f' border-left:3px solid {ac}; border-radius:8px;'
                f' padding:9px 12px; margin-bottom:7px">'
                f'<div style="display:flex; justify-content:space-between; align-items:center">'
                f'<span style="font-size:0.78rem; font-weight:700; color:{C_TEXT}">{b["bank"]}</span>'
                f'<span style="font-size:0.68rem; font-weight:600; color:{yoy_c}">{b["yoy_chg"]:+.1f}%</span>'
                f'</div>'
                f'<div style="font-size:0.67rem; color:{C_TEXT2}; margin-top:2px">'
                f'${b["tf_vol_bn"]}B · {b["region"]}'
                f'</div>'
                f'<div style="font-size:0.65rem; color:{C_TEXT3}; margin-top:1px">'
                f'{b["specialty"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Credit availability trend mini chart (simulated quarterly)
    quarters = ["Q1-24", "Q2-24", "Q3-24", "Q4-24", "Q1-25", "Q2-25", "Q3-25", "Q4-25", "Q1-26"]
    avail_idx = [62, 58, 55, 57, 60, 63, 61, 64, 66]   # index 0-100

    fig_avail = go.Figure()
    fig_avail.add_trace(go.Scatter(
        x=quarters, y=avail_idx,
        name="Credit Availability Index",
        mode="lines+markers",
        line=dict(color=C_CYAN, width=2.5),
        marker=dict(size=7),
        fill="tozeroy",
        fillcolor=_rgba(C_CYAN, 0.09),
        hovertemplate="%{x}<br>Availability: %{y}/100<extra></extra>",
    ))
    fig_avail.add_hline(y=50, line_dash="dot", line_color=C_TEXT3, line_width=1,
                        annotation_text="Neutral (50)", annotation_position="right",
                        annotation_font=dict(color=C_TEXT3, size=9))
    fig_avail = _plotly_defaults(fig_avail, height=220)
    fig_avail.update_layout(showlegend=False, margin=dict(t=16, b=24, l=10, r=10))
    fig_avail.update_yaxes(title="Availability Index", range=[30, 100])
    st.plotly_chart(fig_avail, use_container_width=True, key="finance_credit_avail_trend")
    _subheading("Quarterly Bank Credit Availability Index (0 = Frozen, 100 = Open)")

    _insight_box(
        "Emerging-market specialist lenders (Standard Chartered, DBS) are growing"
        " TF volumes at 6-8% YoY versus contraction at European universals (Societe"
        " Generale -3.4%, Deutsche Bank -1.2%). This bifurcation signals tightening"
        " credit for commodity/European trade corridors while Asia lanes remain well-funded.",
        color=C_CYAN, label="Market Dynamics",
    )


# ---------------------------------------------------------------------------
# Section 5 — Documentary Credit vs Open Account
# ---------------------------------------------------------------------------

def _render_lc_oa_trend() -> None:
    section_header(
        "Documentary Credit vs Open Account Trend (2015-2026)",
        "L/C used for new/riskier counterparties — accelerating shift to open account"
        " signals trust deepening but exposes exporters to abrupt cancellation risk",
    )

    years = _LC_OA_DATA["year"]
    lc    = _LC_OA_DATA["lc_pct"]
    dc    = _LC_OA_DATA["doc_coll"]
    oa    = _LC_OA_DATA["open_acc"]

    col_chart, col_stats = st.columns([3, 1])
    with col_chart:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=years, y=oa, name="Open Account",
            mode="lines+markers",
            line=dict(color=C_HIGH, width=2.5),
            marker=dict(size=6, color=C_HIGH, line=dict(color="white", width=1)),
            fill="tozeroy",
            fillcolor=_rgba(C_HIGH, 0.09),
            hovertemplate="Year: %{x}<br>Open Account: %{y}%<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=years, y=dc, name="Documentary Collections",
            mode="lines+markers",
            line=dict(color=C_MOD, width=2),
            marker=dict(size=6, color=C_MOD),
            fill="tonexty",
            fillcolor=_rgba(C_MOD, 0.06),
            hovertemplate="Year: %{x}<br>Doc. Collections: %{y}%<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=years, y=lc, name="Letter of Credit",
            mode="lines+markers",
            line=dict(color=C_ACCENT, width=2.5),
            marker=dict(size=6, color=C_ACCENT),
            hovertemplate="Year: %{x}<br>Letter of Credit: %{y}%<extra></extra>",
        ))
        fig.add_annotation(x=2015, y=43, text="L/C 43%", showarrow=True,
                           arrowhead=2, arrowcolor=C_ACCENT, ax=35, ay=-25,
                           font=dict(size=9, color=C_ACCENT),
                           bgcolor=_rgba(C_CARD, 0.9), borderpad=3)
        fig.add_annotation(x=2026, y=69, text="Open acct 69%", showarrow=True,
                           arrowhead=2, arrowcolor=C_HIGH, ax=-55, ay=-20,
                           font=dict(size=9, color=C_HIGH),
                           bgcolor=_rgba(C_CARD, 0.9), borderpad=3)
        fig.add_annotation(x=2026, y=23, text="L/C 23%", showarrow=True,
                           arrowhead=2, arrowcolor=C_ACCENT, ax=30, ay=25,
                           font=dict(size=9, color=C_ACCENT),
                           bgcolor=_rgba(C_CARD, 0.9), borderpad=3)

        fig = _plotly_defaults(fig, height=360)
        fig.update_xaxes(title="Year", dtick=2)
        fig.update_yaxes(title="Share of Global Trade Financing (%)", ticksuffix="%", range=[0, 82])
        fig.update_layout(margin=dict(t=24, b=24, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True, key="finance_lc_oa_trend")

    with col_stats:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        lc_change  = lc[-1] - lc[0]
        oa_change  = oa[-1] - oa[0]
        rate_str   = f"{abs(lc_change)/len(years):.1f} pp/yr"
        items = [
            ("LC 2015", f"{lc[0]}%", C_ACCENT),
            ("LC 2026", f"{lc[-1]}%", C_ACCENT),
            ("LC Change", f"{lc_change:+d} pp", C_LOW),
            ("Open Acct 2026", f"{oa[-1]}%", C_HIGH),
            ("OA Change", f"{oa_change:+d} pp", C_HIGH),
            ("Shift Rate", rate_str, C_TEXT2),
            ("Doc. Coll. 2026", f"{dc[-1]}%", C_MOD),
        ]
        for lbl, val, c in items:
            st.markdown(
                f'<div style="display:flex; justify-content:space-between;'
                f' align-items:center; padding:8px 0;'
                f' border-bottom:1px solid rgba(255,255,255,0.05)">'
                f'<span style="font-size:0.72rem; color:{C_TEXT2}">{lbl}</span>'
                f'<span style="font-size:0.72rem; font-weight:700; color:{c}">{val}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.caption(
        "Sources: ICC Banking Commission · BIS Payment Statistics · McKinsey Global"
        " Payments Report · SWIFT Trade Finance Activity.  2025-2026 = projections."
    )
    _insight_box(
        "Rising open-account share signals maturing trade relationships — but"
        " shifts risk from banks to exporters. When credit conditions tighten,"
        " open-account deals cancel abruptly, creating sharper short-term shipping"
        " demand volatility vs. the stability of L/C-backed shipments. The 20 pp"
        " LC decline since 2015 represents a structural regime shift in trade risk.",
        color=C_MOD, label="Shipping Implication",
    )


# ---------------------------------------------------------------------------
# Section 6 — Trade Finance Gap Analysis
# ---------------------------------------------------------------------------

def _render_finance_gap() -> None:
    section_header(
        "Trade Finance Gap — SME vs Large Corporation Access",
        "The $1.95T global trade finance gap falls disproportionately on SMEs —"
        " small exporters face 3x higher rejection rates, suppressing emerging-market"
        " shipping volumes on feeder and regional routes",
    )

    years = _FINANCE_GAP_DATA["year"]
    gap   = _FINANCE_GAP_DATA["sme_gap_bn"]
    corp  = _FINANCE_GAP_DATA["corp_access"]
    sme   = _FINANCE_GAP_DATA["sme_access"]
    rej   = _FINANCE_GAP_DATA["rejection_rate_sme"]

    # Gap + Access dual panel
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["Global Trade Finance Gap ($B, SME Unmet)", "Finance Access Rate (% of Need Met)"],
        column_widths=[0.45, 0.55],
    )

    fig.add_trace(go.Bar(
        x=years, y=gap,
        name="SME Finance Gap ($B)",
        marker_color=[_rgba(C_LOW, 0.6 + 0.04 * i) for i in range(len(years))],
        hovertemplate="Year: %{x}<br>Gap: $%{y}B<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=years, y=corp,
        name="Large Corp Access",
        mode="lines+markers",
        line=dict(color=C_HIGH, width=2.5),
        marker=dict(size=7),
        hovertemplate="Year: %{x}<br>Corp Access: %{y}%<extra></extra>",
    ), row=1, col=2)
    fig.add_trace(go.Scatter(
        x=years, y=sme,
        name="SME Access",
        mode="lines+markers",
        line=dict(color=C_LOW, width=2.5),
        marker=dict(size=7),
        fill="tonexty",
        fillcolor=_rgba(C_LOW, 0.06),
        hovertemplate="Year: %{x}<br>SME Access: %{y}%<extra></extra>",
    ), row=1, col=2)
    fig.add_trace(go.Scatter(
        x=years, y=rej,
        name="SME Rejection Rate",
        mode="lines",
        line=dict(color=C_MOD, width=1.5, dash="dash"),
        hovertemplate="Year: %{x}<br>Rejection Rate: %{y}%<extra></extra>",
    ), row=1, col=2)

    fig = _plotly_defaults(fig, height=340)
    fig.update_yaxes(title="$B Unmet", tickprefix="$", ticksuffix="B", row=1, col=1)
    fig.update_yaxes(title="Access / Rejection Rate (%)", ticksuffix="%", range=[30, 100], row=1, col=2)
    fig.update_layout(margin=dict(t=36, b=24, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True, key="finance_gap_chart")

    # Access gap KPI row
    gap_now     = sme[-1]
    corp_now    = corp[-1]
    rej_now     = rej[-1]
    access_delta = corp_now - gap_now

    k1, k2, k3, k4 = st.columns(4)
    with k1: _kpi_card("SME Finance Access Rate", f"{gap_now}%", "of financing need met", C_LOW)
    with k2: _kpi_card("Large Corp Access Rate", f"{corp_now}%", "of financing need met", C_HIGH)
    with k3: _kpi_card("Access Gap", f"{access_delta:.0f} pp", "Corp minus SME", C_MOD)
    with k4: _kpi_card("SME Rejection Rate", f"{rej_now}%", "of applicants rejected", C_LOW)

    # Breakdown by region
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    _subheading("Regional SME Finance Access Rates (2026 estimate)")

    region_access = [
        ("North America",    72, 18, C_HIGH),
        ("Europe",           68, 21, C_HIGH),
        ("East Asia",        61, 28, C_MOD),
        ("Southeast Asia",   51, 36, C_MOD),
        ("South Asia",       41, 44, C_MOD),
        ("Latin America",    35, 51, C_LOW),
        ("Middle East/N.Af.", 38, 48, C_LOW),
        ("Sub-Saharan Africa",28, 59, C_LOW),
    ]
    cols_reg = st.columns(4)
    for i, (region, access_pct, rej_pct, col_c) in enumerate(region_access):
        with cols_reg[i % 4]:
            fill_pct = access_pct
            st.markdown(
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
                f' border-radius:8px; padding:10px 12px; margin-bottom:8px">'
                f'<div style="font-size:0.65rem; font-weight:700; color:{C_TEXT3};'
                f' text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px">'
                f'{region}</div>'
                f'<div style="font-size:1.2rem; font-weight:800; color:{col_c}">{access_pct}%</div>'
                f'<div style="font-size:0.62rem; color:{C_TEXT3}; margin-bottom:5px">'
                f'access · {rej_pct}% rejected</div>'
                f'<div style="background:rgba(255,255,255,0.06); border-radius:4px;'
                f' height:4px; overflow:hidden">'
                f'<div style="width:{fill_pct}%; height:100%; background:{col_c};'
                f' border-radius:4px"></div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    _insight_box(
        "Sub-Saharan Africa's 28% SME access rate and 59% rejection rate are the"
        " primary drivers of the $420B Africa trade finance gap — a major structural"
        " constraint on intra-Africa shipping growth despite rising commodity volumes."
        " IFC and MDB programs address less than 12% of the gap.",
        color=C_LOW, label="Structural Barrier",
    )


# ---------------------------------------------------------------------------
# Section 7 — FX Hedging Costs
# ---------------------------------------------------------------------------

def _render_fx_hedging() -> None:
    section_header(
        "FX Hedging Costs — Forward vs Spot for Major Trade Currencies",
        "The cost of currency hedging adds directly to trade finance costs — high FX"
        " volatility and wide forward premiums reduce trade competitiveness on affected routes",
    )

    # Hedging cost chart — horizontal bar
    pairs_sorted = sorted(_FX_HEDGE_DATA, key=lambda x: x["hedge_cost_pct"], reverse=True)
    hedge_colors = [C_LOW if h["hedge_cost_pct"] > 3 else C_MOD if h["hedge_cost_pct"] > 1.5 else C_HIGH
                    for h in pairs_sorted]

    col_chart, col_detail = st.columns([3, 2])
    with col_chart:
        fig_fx = go.Figure()
        fig_fx.add_trace(go.Bar(
            x=[h["hedge_cost_pct"] for h in pairs_sorted],
            y=[h["pair"] for h in pairs_sorted],
            orientation="h",
            marker_color=hedge_colors,
            marker_line_width=0,
            hovertemplate="<b>%{y}</b><br>Hedge Cost: %{x:.2f}% p.a.<extra></extra>",
            showlegend=False,
        ))
        # vol scatter
        fig_fx.add_trace(go.Scatter(
            x=[h["hedge_cost_pct"] for h in pairs_sorted],
            y=[h["pair"] for h in pairs_sorted],
            mode="markers",
            marker=dict(
                size=[h["vol_30d"] * 1.2 for h in pairs_sorted],
                color=[_rgba(C_CYAN, 0.5) for _ in pairs_sorted],
                line=dict(color=C_CYAN, width=1),
            ),
            name="30d Vol",
            hovertemplate="<b>%{y}</b><br>30d Vol: %{text}<extra></extra>",
            text=[f"{h['vol_30d']:.1f}%" for h in pairs_sorted],
        ))
        fig_fx = _plotly_defaults(fig_fx, height=340)
        fig_fx.update_xaxes(title="Annual Hedge Cost (% of notional)", ticksuffix="%")
        fig_fx.update_layout(margin=dict(t=16, b=24, l=10, r=10))
        st.plotly_chart(fig_fx, use_container_width=True, key="finance_fx_hedge_chart")

    with col_detail:
        _subheading("Currency Pair Detail")
        trend_color = {"STABLE": C_HIGH, "TIGHTENING": C_CYAN, "WIDENING": C_LOW, "PEGGED": C_TEXT3}
        trend_icon  = {"STABLE": "→", "TIGHTENING": "↘", "WIDENING": "↗", "PEGGED": "="}
        for h in sorted(_FX_HEDGE_DATA, key=lambda x: x["hedge_cost_pct"], reverse=True):
            tc = trend_color.get(h["trend"], C_TEXT3)
            ti = trend_icon.get(h["trend"], "?")
            cost_c = C_LOW if h["hedge_cost_pct"] > 3 else C_MOD if h["hedge_cost_pct"] > 1.5 else C_HIGH
            st.markdown(
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
                f' border-radius:8px; padding:9px 12px; margin-bottom:6px">'
                f'<div style="display:flex; justify-content:space-between; align-items:center">'
                f'<span style="font-size:0.78rem; font-weight:700; color:{C_TEXT}">{h["pair"]}</span>'
                f'<span style="font-size:0.78rem; font-weight:700; color:{cost_c}">'
                f'{h["hedge_cost_pct"]:.2f}% p.a.</span>'
                f'</div>'
                f'<div style="display:flex; justify-content:space-between; margin-top:3px">'
                f'<span style="font-size:0.67rem; color:{C_TEXT2}">'
                f'Spot: {h["spot"]} · 3M Fwd: {h["fwd_3m"]}</span>'
                f'<span style="font-size:0.67rem; color:{tc}; font-weight:600">'
                f'{ti} {h["trend"]}</span>'
                f'</div>'
                f'<div style="font-size:0.62rem; color:{C_TEXT3}; margin-top:2px">'
                f'30d Vol: {h["vol_30d"]:.1f}%</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Hedging cost impact on trade margin
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    _subheading("Hedging Cost Impact on Trade Finance Margins")

    # Simulate: for a $10M shipment, what's the cost of hedging?
    notional_m = st.slider(
        "Shipment notional value ($M)",
        min_value=1, max_value=100, value=10, step=1,
        key="finance_fx_notional_slider",
        help="Drag to see absolute hedging cost for a shipment of this value",
    )
    hedge_cols = st.columns(4)
    for i, h in enumerate(sorted(_FX_HEDGE_DATA, key=lambda x: x["hedge_cost_pct"], reverse=True)[:4]):
        abs_cost = round(notional_m * 1_000_000 * h["hedge_cost_pct"] / 100 / 1000, 1)  # in $K
        cost_c = C_LOW if h["hedge_cost_pct"] > 3 else C_MOD if h["hedge_cost_pct"] > 1.5 else C_HIGH
        with hedge_cols[i]:
            _kpi_card(h["pair"], f"${abs_cost}K", f"annual hedge cost on ${notional_m}M", cost_c)

    _insight_box(
        f"USD/BRL hedging at 9.8% p.a. adds $980K to the annual cost of hedging a"
        f" $10M Brazil-China commodity shipment — often exceeding the shipper's profit margin."
        f" This structural barrier explains Brazil's preference for CNY invoicing on"
        f" agricultural exports and contributes to de-dollarization trends.",
        color=C_MOD, label="FX Margin Squeeze",
    )


# ---------------------------------------------------------------------------
# Section 8 — Supply Chain Finance Programs
# ---------------------------------------------------------------------------

def _render_scf_programs() -> None:
    section_header(
        "Supply Chain Finance (SCF) Programs",
        "SCF allows buyers to extend payment terms while suppliers receive early"
        " payment at buyer-grade credit costs — reducing trade finance gap and"
        " supporting shipping demand stability via committed order flows",
    )

    years  = _SCF_DATA["year"]
    adopt  = _SCF_DATA["adoption_pct"]
    vol    = _SCF_DATA["vol_outstanding_bn"]
    saving = _SCF_DATA["cost_saving_bps"]
    supp   = _SCF_DATA["supplier_onboard_pct"]

    # SCF growth dual chart
    fig_scf = make_subplots(
        rows=1, cols=2,
        subplot_titles=["SCF Adoption & Supplier Onboarding (%)", "Outstanding Volume ($B) & Cost Savings (bps)"],
        specs=[[{}, {"secondary_y": True}]],
    )
    fig_scf.add_trace(go.Scatter(
        x=years, y=adopt, name="Corp Adoption %",
        mode="lines+markers",
        line=dict(color=C_HIGH, width=2.5),
        marker=dict(size=7),
        fill="tozeroy",
        fillcolor=_rgba(C_HIGH, 0.09),
        hovertemplate="Year: %{x}<br>Adoption: %{y}%<extra></extra>",
    ), row=1, col=1)
    fig_scf.add_trace(go.Scatter(
        x=years, y=supp, name="Supplier Onboard %",
        mode="lines+markers",
        line=dict(color=C_CYAN, width=2, dash="dot"),
        marker=dict(size=6),
        hovertemplate="Year: %{x}<br>Suppliers Onboarded: %{y}%<extra></extra>",
    ), row=1, col=1)
    fig_scf.add_trace(go.Bar(
        x=years, y=vol,
        name="SCF Volume ($B)",
        marker_color=_rgba(C_ACCENT, 0.5),
        hovertemplate="Year: %{x}<br>Volume: $%{y}B<extra></extra>",
    ), row=1, col=2, secondary_y=False)
    fig_scf.add_trace(go.Scatter(
        x=years, y=saving, name="Cost Saving (bps)",
        mode="lines+markers",
        line=dict(color=C_MOD, width=2),
        marker=dict(size=6),
        hovertemplate="Year: %{x}<br>Saving: %{y} bps<extra></extra>",
    ), row=1, col=2, secondary_y=True)

    fig_scf = _plotly_defaults(fig_scf, height=320)
    fig_scf.update_yaxes(title="Rate (%)", ticksuffix="%", row=1, col=1)
    fig_scf.update_yaxes(title="SCF Volume ($B)", tickprefix="$", ticksuffix="B",
                          secondary_y=False, row=1, col=2)
    fig_scf.update_yaxes(title="Cost Saving (bps)", ticksuffix=" bps",
                          showgrid=False, secondary_y=True, row=1, col=2)
    fig_scf.update_layout(margin=dict(t=36, b=24, l=10, r=10))
    st.plotly_chart(fig_scf, use_container_width=True, key="finance_scf_trend_chart")

    # KPI bar
    k1, k2, k3, k4 = st.columns(4)
    with k1: _kpi_card("SCF Adoption (Fortune 500)", f"{adopt[-1]}%", "2026 estimate", C_HIGH)
    with k2: _kpi_card("Outstanding Volume", f"${vol[-1]:,}B", "global SCF programs", C_ACCENT)
    with k3: _kpi_card("Avg Cost Saving", f"{saving[-1]} bps", "vs traditional credit", C_HIGH)
    with k4: _kpi_card("Supplier Onboarding", f"{supp[-1]}%", "of eligible suppliers", C_CYAN)

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
    _subheading("Major SCF Programs — Volume & Savings")

    prog_cols = st.columns(3)
    for i, prog in enumerate(_SCF_PROGRAMS):
        with prog_cols[i % 3]:
            saving_c = C_HIGH if prog["saving_bps"] > 70 else C_MOD
            st.markdown(
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
                f' border-top:2px solid {saving_c}; border-radius:10px;'
                f' padding:12px 14px; margin-bottom:10px">'
                f'<div style="font-size:0.78rem; font-weight:700; color:{C_TEXT};'
                f' margin-bottom:4px">{prog["program"]}</div>'
                f'<div style="font-size:0.65rem; color:{C_TEXT3}; margin-bottom:8px">'
                f'{prog["sector"]}</div>'
                f'<div style="display:flex; justify-content:space-between; margin-bottom:4px">'
                f'<span style="font-size:0.70rem; color:{C_TEXT2}">Volume</span>'
                f'<span style="font-size:0.70rem; font-weight:700; color:{C_ACCENT}">'
                f'${prog["vol_bn"]}B</span>'
                f'</div>'
                f'<div style="display:flex; justify-content:space-between; margin-bottom:4px">'
                f'<span style="font-size:0.70rem; color:{C_TEXT2}">Suppliers</span>'
                f'<span style="font-size:0.70rem; font-weight:700; color:{C_TEXT}">'
                f'{prog["suppliers"]:,}</span>'
                f'</div>'
                f'<div style="display:flex; justify-content:space-between">'
                f'<span style="font-size:0.70rem; color:{C_TEXT2}">Cost Saving</span>'
                f'<span style="font-size:0.70rem; font-weight:700; color:{saving_c}">'
                f'{prog["saving_bps"]} bps</span>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    _insight_box(
        "Toyota's SCF program delivers 91 bps cost savings — effectively cutting"
        " supplier financing costs by ~40% vs unsupported credit. This translates"
        " directly into shipping stability: SCF-backed orders are 3-4x less likely"
        " to be cancelled vs open-account, supporting consistent container booking"
        " volumes on Japan-ASEAN auto parts routes.",
        color=C_HIGH, label="Shipping Stability Impact",
    )


# ---------------------------------------------------------------------------
# Section 9 — Credit Availability Map (was Section 3)
# ---------------------------------------------------------------------------

def _render_credit_map(risk_scores: List[TradeFinanceRiskScore]) -> None:
    section_header(
        "Trade Credit Availability — Global Risk Map",
        "Green = easy credit access · Red = tight / restricted · scores reflect"
        " sanctions, FX controls, banking system depth, and sovereign risk",
    )

    if not risk_scores:
        st.info(
            "Regional credit risk data is currently unavailable. "
            "The map and risk table will populate once the data feed refreshes."
        )
        return

    _COUNTRY_SCORES: list[tuple[str, float]] = [
        ("RUS", 0.95), ("IRN", 0.90), ("VEN", 0.82), ("ARG", 0.78),
        ("BLR", 0.80), ("SYR", 0.85), ("PRK", 0.92), ("CUB", 0.70),
        ("NGA", 0.62), ("ETH", 0.60), ("KEN", 0.55), ("GHA", 0.58),
        ("PAK", 0.52), ("BGD", 0.48), ("EGY", 0.50), ("TUR", 0.44),
        ("CHN", 0.38),
        ("IDN", 0.30), ("PHL", 0.28), ("VNM", 0.26), ("MMR", 0.40),
        ("USA", 0.12), ("DEU", 0.10), ("GBR", 0.11), ("FRA", 0.11),
        ("JPN", 0.12), ("KOR", 0.14), ("AUS", 0.13), ("CAN", 0.12),
        ("NLD", 0.10), ("SGP", 0.13), ("CHE", 0.09), ("SWE", 0.10),
        ("NOR", 0.10), ("DNK", 0.11), ("FIN", 0.10), ("BEL", 0.11),
        ("IND", 0.32), ("BRA", 0.38), ("MEX", 0.34), ("ZAF", 0.30),
        ("SAU", 0.20), ("ARE", 0.18), ("TWN", 0.15), ("HKG", 0.14),
    ]

    iso_codes = [c[0] for c in _COUNTRY_SCORES]
    scores    = [c[1] for c in _COUNTRY_SCORES]

    fig_map = go.Figure(go.Choropleth(
        locations=iso_codes,
        z=scores,
        colorscale=[
            [0.0,  "#10b981"],
            [0.4,  "#f59e0b"],
            [0.7,  "#f97316"],
            [1.0,  "#ef4444"],
        ],
        zmin=0.0, zmax=1.0,
        colorbar=dict(
            title=dict(text="Credit Risk", font=dict(color=C_TEXT2, size=11)),
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["Easy", "Low", "Moderate", "High", "Restricted"],
            tickfont=dict(color=C_TEXT2, size=10),
            bgcolor="rgba(26,34,53,0.8)",
            bordercolor="rgba(255,255,255,0.1)",
            len=0.7, y=0.5,
        ),
        marker_line_color="rgba(255,255,255,0.08)",
        marker_line_width=0.5,
        hovertemplate="<b>%{location}</b><br>Credit Risk Score: %{z:.2f}<extra></extra>",
    ))

    for ann in [
        dict(lat=61.5, lon=105.3, text="Russia (SWIFT exc.)", color="#ef4444"),
        dict(lat=32.4, lon=53.7,  text="Iran (sanctions)",    color="#ef4444"),
        dict(lat=-34.0, lon=-64.0, text="Argentina (FX ctrl)", color="#f97316"),
        dict(lat=6.4, lon=-66.6,  text="Venezuela (OFAC)",    color="#ef4444"),
    ]:
        fig_map.add_trace(go.Scattergeo(
            lat=[ann["lat"]], lon=[ann["lon"]],
            mode="markers+text",
            marker=dict(size=10, color=ann["color"], symbol="circle",
                        line=dict(color="white", width=1)),
            text=[ann["text"]],
            textfont=dict(color=ann["color"], size=9),
            textposition="top center",
            showlegend=False, hoverinfo="skip",
        ))

    fig_map.update_geos(
        showcoastlines=True, coastlinecolor="rgba(255,255,255,0.15)",
        showland=True, landcolor="#111827",
        showocean=True, oceancolor="#0a0f1a",
        showlakes=False,
        showcountries=True, countrycolor="rgba(255,255,255,0.06)",
        projection_type="natural earth", bgcolor="#0a0f1a",
    )
    fig_map.update_layout(
        template="plotly_dark", height=420,
        paper_bgcolor=C_CARD,
        margin=dict(t=10, b=10, l=0, r=0),
        geo_bgcolor="#0a0f1a",
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(fig_map, use_container_width=True, key="finance_credit_map")

    _subheading("Regional Credit Risk Detail")
    risk_rows = [{
        "Region":       rs.region,
        "Risk Score":   round(rs.score * 100),
        "Primary Risk": rs.primary_risk[:60] + ("…" if len(rs.primary_risk) > 60 else ""),
        "Rate Impact":  ("+" if rs.rate_impact_pct > 0 else "") + str(rs.rate_impact_pct) + "%",
    } for rs in risk_scores]
    risk_df = pd.DataFrame(risk_rows)
    st.dataframe(risk_df, use_container_width=True, hide_index=True)
    st.download_button(
        label="Download CSV",
        data=risk_df.to_csv(index=False).encode("utf-8"),
        file_name="regional_credit_risk.csv",
        mime="text/csv",
        key="finance_credit_risk_download",
    )


# ---------------------------------------------------------------------------
# Section 10 — De-dollarization Monitor (was Section 5)
# ---------------------------------------------------------------------------

def _render_dedollarization() -> None:
    section_header(
        "De-dollarization Monitor",
        "USD trade settlement share declining from 85% — CNY growing via China"
        " bilateral CIPS agreements — impacts freight pricing benchmarks (BDI, FBX)",
    )

    years = _DEDOLLAR_DATA["year"]
    usd   = _DEDOLLAR_DATA["usd_pct"]
    eur   = _DEDOLLAR_DATA["eur_pct"]
    cny   = _DEDOLLAR_DATA["cny_pct"]
    other = _DEDOLLAR_DATA["other_pct"]

    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.6, 0.4],
        subplot_titles=["Trade Settlement Currency Share (% global)", "2026 Currency Share"],
    )

    for y_vals, name, color in [
        (usd,   "USD",   "#3b82f6"),
        (eur,   "EUR",   "#10b981"),
        (cny,   "CNY",   "#f59e0b"),
        (other, "Other", "#64748b"),
    ]:
        fig.add_trace(go.Scatter(
            x=years, y=y_vals, name=name,
            mode="lines",
            line=dict(color=color, width=2),
            stackgroup="one",
            hovertemplate=name + ": %{y:.1f}%<extra></extra>",
        ), row=1, col=1)

    latest    = [usd[-1], eur[-1], cny[-1], other[-1]]
    colors_pie = ["#3b82f6", "#10b981", "#f59e0b", "#64748b"]
    fig.add_trace(go.Pie(
        values=latest,
        labels=["USD", "EUR", "CNY", "Other"],
        marker=dict(colors=colors_pie, line=dict(color="rgba(0,0,0,0.4)", width=1)),
        hole=0.55,
        textfont=dict(color=C_TEXT, size=11),
        hovertemplate="%{label}: %{value:.1f}%<extra></extra>",
        showlegend=False,
    ), row=1, col=2)

    fig.add_annotation(
        x=2026, y=cny[-1],
        text=f"CNY {cny[-1]}%",
        showarrow=True, arrowhead=2, arrowcolor="#f59e0b",
        ax=-55, ay=-25,
        font=dict(size=10, color="#f59e0b"),
        bgcolor=_rgba(C_CARD, 0.9), borderpad=4,
        row=1, col=1,
    )

    fig = _plotly_defaults(fig, height=360)
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.05)", row=1, col=1)
    fig.update_yaxes(title="Share (%)", ticksuffix="%", row=1, col=1)
    fig.update_layout(margin=dict(t=40, b=20, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True, key="finance_dedollarization")

    usd_drop = round(usd[0] - usd[-1], 1)
    cny_rise = round(cny[-1] - cny[0], 1)

    k1, k2, k3 = st.columns(3)
    with k1: _kpi_card("USD Share 2026", f"{usd[-1]}%", f"Down {usd_drop} pp since 2015", C_LOW)
    with k2: _kpi_card("CNY Share 2026", f"{cny[-1]}%", f"Up {cny_rise} pp since 2015", C_HIGH)
    with k3: _kpi_card("Non-USD Share", f"{round(100-usd[-1],1)}%", "2026 estimate", C_MOD)

    _insight_box(
        f"USD trade-settlement share has fallen ~{usd_drop} pp since 2015. CNY has gained"
        f" {cny_rise} pp primarily through China bilateral payment agreements (CIPS network,"
        f" petroyuan pricing, Belt and Road settlements). If USD weakens as the dominant"
        f" trade currency, USD-denominated freight benchmarks (BDI, FBX) may decouple from"
        f" actual trade volumes — complicating rate forecasting for trans-Pacific and Asia-Europe routes.",
        color=C_MOD, label="De-dollarization Watch",
    )


# ---------------------------------------------------------------------------
# Section 11 — Sanctions Impact Tracker (was Section 6)
# ---------------------------------------------------------------------------

def _render_sanctions_tracker() -> None:
    section_header(
        "Financial Sanctions Impact Tracker",
        "Active sanctions regimes affecting shipping routes via financial channel"
        " restrictions — SWIFT cutoffs, OFAC/SDN lists, and correspondent banking blocks",
    )

    total_diverted = sum(s["diverted_vol_pct"] for s in _SANCTIONS_DATA)
    critical_count = sum(1 for s in _SANCTIONS_DATA if s["severity"] == "CRITICAL")
    high_count     = sum(1 for s in _SANCTIONS_DATA if s["severity"] == "HIGH")

    k1, k2, k3 = st.columns(3)
    with k1: _kpi_card("Active Regimes", str(len(_SANCTIONS_DATA)),
                        f"{critical_count} Critical · {high_count} High", C_LOW)
    with k2: _kpi_card("Diverted Trade Volume", f"{round(total_diverted, 1)}%",
                        "of affected route volumes rerouted", C_MOD)
    with k3: _kpi_card("Longest Running", "64y", "Cuba (since 1962)", C_ACCENT)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    for sanction in _SANCTIONS_DATA:
        sev       = sanction["severity"]
        sev_color = _SEVERITY_COLOR.get(sev, C_TEXT3)
        routes_str = " · ".join(sanction["affected_routes"])
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {_rgba(sev_color, 0.25)};'
            f' border-left:4px solid {sev_color}; border-radius:10px;'
            f' padding:14px 16px; margin-bottom:10px">'
            f'<div style="display:flex; justify-content:space-between;'
            f' align-items:flex-start; margin-bottom:8px">'
            f'<div>'
            f'<span style="font-size:1rem; font-weight:700; color:{C_TEXT}">'
            f'{sanction["jurisdiction"]}</span>&nbsp;&nbsp;'
            f'<span style="background:{_rgba(sev_color, 0.15)}; color:{sev_color};'
            f' border:1px solid {_rgba(sev_color, 0.3)}; padding:2px 8px;'
            f' border-radius:999px; font-size:0.65rem; font-weight:700">{sev}</span>'
            f'</div>'
            f'<div style="font-size:0.68rem; color:{C_TEXT3}">Since {sanction["in_force_since"]}</div>'
            f'</div>'
            f'<div style="font-size:0.73rem; color:{C_TEXT2}; margin-bottom:6px">'
            f'<strong>Mechanism:</strong> {sanction["mechanism"]}</div>'
            f'<div style="font-size:0.73rem; color:{C_TEXT2}; margin-bottom:8px">'
            f'{sanction["shipping_impact"]}</div>'
            f'<div style="display:flex; gap:16px; flex-wrap:wrap">'
            f'<div style="font-size:0.65rem; color:{C_TEXT3}">'
            f'<strong>Routes:</strong> {routes_str}</div>'
            f'<div style="font-size:0.65rem; color:{sev_color}">'
            f'<strong>Diverted vol:</strong> {sanction["diverted_vol_pct"]}%</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    freight_data: dict | None = None,
    macro_data: dict | None = None,
    route_results: list | None = None,
    stock_data: dict | None = None,
) -> None:
    """Render the Trade Finance Dashboard tab.

    Parameters
    ----------
    freight_data:
        Optional dict of freight-rate DataFrames (accepted for API consistency).
    macro_data:
        Dict mapping FRED series_id -> pd.DataFrame with 'date' and 'value'
        columns. DGS10 is used for the rate impact model if present.
    route_results:
        Optional list of route analysis result dicts (accepted for API consistency).
    stock_data:
        Optional dict of equity/stock DataFrames (accepted for API consistency).
    """
    macro_data = macro_data or {}
    n_loaded   = len(macro_data)
    logger.info(
        "tab_finance: rendering with {n} FRED series, freight_data={fd},"
        " route_results={rr}, stock_data={sd}",
        n=n_loaded,
        fd=freight_data is not None,
        rr=route_results is not None,
        sd=stock_data is not None,
    )

    # ── Load processing layer ────────────────────────────────────────────────
    try:
        indicators = build_trade_finance_indicators()
    except Exception as exc:
        logger.warning("tab_finance: build_trade_finance_indicators failed: {}", exc)
        indicators = []

    try:
        risk_scores = compute_regional_finance_risk()
    except Exception as exc:
        logger.warning("tab_finance: compute_regional_finance_risk failed: {}", exc)
        risk_scores = []

    # ── Section 0: Intelligence Banner (NEW) ─────────────────────────────────
    _render_finance_banner(macro_data)
    _hr()

    # ── Section 1: Trade Finance Overview ───────────────────────────────────
    _render_finance_overview(indicators)
    _hr()

    # ── Section 2: Financing Cost by Route ──────────────────────────────────
    _render_route_financing()
    _hr()

    # ── Section 3: Interest Rate Impact Model ────────────────────────────────
    _render_rate_impact(macro_data)
    _hr()

    # ── Section 4: Bank Trade Finance Availability ───────────────────────────
    _render_bank_availability()
    _hr()

    # ── Section 5: Documentary Credit vs Open Account ────────────────────────
    _render_lc_oa_trend()
    _hr()

    # ── Section 6: Trade Finance Gap Analysis ────────────────────────────────
    _render_finance_gap()
    _hr()

    # ── Section 7: FX Hedging Costs ──────────────────────────────────────────
    _render_fx_hedging()
    _hr()

    # ── Section 8: Supply Chain Finance Programs ──────────────────────────────
    _render_scf_programs()
    _hr()

    # ── Section 9: Credit Availability Map ──────────────────────────────────
    _render_credit_map(risk_scores)
    _hr()

    # ── Section 10: De-dollarization Monitor ─────────────────────────────────
    _render_dedollarization()
    _hr()

    # ── Section 11: Sanctions Impact Tracker ─────────────────────────────────
    _render_sanctions_tracker()
