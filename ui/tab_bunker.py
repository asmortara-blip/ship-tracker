"""Bunker Fuel tab — fuel intelligence dashboard for shipping operators and traders.

Bunker fuel represents 40-60% of voyage operating cost for container ships.
This tab delivers a comprehensive bunker fuel intelligence suite:
  - Fuel cost dashboard: VLSFO, IFO 380, LNG, MDO metric cards with weekly change
  - 90-day bunker price time series: all fuel types on a single chart
  - Bunker cost impact on freight rates: correlation analysis
  - Port-specific bunker prices: major bunkering hubs comparison table
  - Route cost calculator: estimated bunker cost per major route
  - Fuel hedge indicator: hedge recommendation at current prices
  - Oil price vs shipping stock correlation chart
  - Alternative fuels tracker: LNG, methanol, ammonia adoption and pricing
  - Scrubber economics: scrubber-equipped vs non-scrubber vessel cost comparison
  - Global bunker price map
  - IMO 2020 / 2030 / 2050 compliance panel

Wire-up (add to app.py tabs list):
    import ui.tab_bunker as tab_bunker
    with tab_bunker_tab:
        tab_bunker.render(freight_data, macro_data, route_results)
"""
from __future__ import annotations

import datetime
import math
import random as _rand
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import streamlit as st
from loguru import logger

from processing.bunker_tracker import (
    BUNKER_HUB_PRICES,
    HUB_META,
    BunkerCostAnalysis,
    BunkerPrice,
    compute_voyage_fuel_cost,
    fetch_live_bunker_prices,
    get_optimal_bunkering_port,
    global_average_price,
    price_history_synthetic,
)
from routes.route_registry import ROUTES
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
    _hex_to_rgba,
    apply_dark_layout,
    section_header,
)


# ── Module-level colour aliases ────────────────────────────────────────────────
_GREEN  = C_HIGH    # "#10b981"
_RED    = C_LOW     # "#ef4444"
_AMBER  = C_MOD     # "#f59e0b"
_BLUE   = C_ACCENT  # "#3b82f6"
_PURPLE = "#8b5cf6"
_CYAN   = "#06b6d4"
_TEAL   = "#14b8a6"
_ROSE   = "#f43f5e"
_INDIGO = "#6366f1"

_FUEL_COLORS: dict[str, str] = {
    "VLSFO":    _BLUE,
    "IFO380":   _AMBER,
    "HFO":      _AMBER,
    "MDO":      _PURPLE,
    "LNG":      _GREEN,
    "METHANOL": _CYAN,
    "AMMONIA":  _TEAL,
}

_FUEL_LABELS: dict[str, str] = {
    "VLSFO":  "Very Low Sulphur Fuel Oil",
    "IFO380": "Intermediate Fuel Oil 380 cSt",
    "HFO":    "Heavy Fuel Oil (scrubber)",
    "MDO":    "Marine Diesel Oil",
    "LNG":    "Liquefied Natural Gas",
}

# ── Alternative fuel data (2026 estimates) ────────────────────────────────────
_ALT_FUELS: list[dict[str, Any]] = [
    {
        "name": "LNG",
        "price_per_mt": 520,
        "vlsfo_premium_pct": -15.4,   # cheaper than VLSFO on $/mt basis (energy-adjusted different)
        "fleet_adoption_pct": 5.2,
        "vessels_on_order": 380,
        "co2_reduction_pct": 20,
        "color": _GREEN,
        "status": "COMMERCIAL",
        "note": "Energy-adjusted cost ~15% cheaper; requires special tanks & port infrastructure",
    },
    {
        "name": "Methanol",
        "price_per_mt": 520,
        "vlsfo_premium_pct": -15.4,
        "fleet_adoption_pct": 0.6,
        "vessels_on_order": 112,
        "co2_reduction_pct": 15,
        "color": _CYAN,
        "status": "EMERGING",
        "note": "Maersk leading adoption; green methanol at ~$1,200/mt commands large premium",
    },
    {
        "name": "Ammonia",
        "price_per_mt": 680,
        "vlsfo_premium_pct": 10.6,
        "fleet_adoption_pct": 0.05,
        "vessels_on_order": 22,
        "co2_reduction_pct": 85,
        "color": _TEAL,
        "status": "PRE-COMMERCIAL",
        "note": "Highest decarbonisation potential; toxicity and storage challenges remain",
    },
    {
        "name": "Bio-VLSFO",
        "price_per_mt": 820,
        "vlsfo_premium_pct": 33.3,
        "fleet_adoption_pct": 0.8,
        "vessels_on_order": 0,
        "co2_reduction_pct": 40,
        "color": _INDIGO,
        "status": "NICHE",
        "note": "Drop-in replacement; supply constraints limit scale-up; 33% cost premium",
    },
]

# ── IMO regulatory timeline ───────────────────────────────────────────────────
_IMO_MILESTONES: list[dict[str, Any]] = [
    {
        "year": "IMO 2020",
        "label": "VLSFO / Scrubbers Mandate",
        "status": "IMPLEMENTED",
        "color": _GREEN,
        "detail": "Global 0.5% sulphur cap — fleet compliance ~97%",
        "compliance_pct": 97,
    },
    {
        "year": "IMO 2023",
        "label": "Carbon Intensity Indicator (CII)",
        "status": "ACTIVE",
        "color": _BLUE,
        "detail": "Annual CII ratings A–E required for vessels >5,000 GT. ~38% of fleet rated C or below.",
        "compliance_pct": 62,
    },
    {
        "year": "IMO 2030",
        "label": "40% Carbon Reduction Target",
        "status": "UPCOMING",
        "color": _AMBER,
        "detail": "40% reduction in CO2 intensity vs 2008 baseline. Current trajectory: ~28% achieved.",
        "compliance_pct": 28,
    },
    {
        "year": "IMO 2050",
        "label": "Net Zero Ambition",
        "status": "FUTURE",
        "color": _RED,
        "detail": "Near-zero GHG emissions — requires LNG/methanol/ammonia fleet transition. <5% LNG vessels today.",
        "compliance_pct": 5,
    },
]

# ── Scrubber economics constants (2026 estimates) ─────────────────────────────
_SCRUBBER_CAPEX_USD       = 3_500_000   # typical EGCS retrofit cost
_SCRUBBER_OPEX_USD_YR     = 120_000     # annual maintenance
_SCRUBBER_WASH_WATER_OPEX = 40_000      # additional compliance cost (port restrictions)
_VESSEL_VOYAGES_PER_YR    = 12          # representative voyages
_VESSEL_FUEL_MT_VOYAGE    = 1_200       # MT per voyage for large container vessel

# Major bunkering ports for the port prices table
_MAJOR_PORTS: list[tuple[str, str]] = [
    ("SGSIN", "Singapore"),
    ("NLRTM", "Rotterdam"),
    ("AEJEA", "Fujairah"),
    ("USNYC", "Houston"),
    ("CNSHA", "Shanghai"),
    ("DEHAM", "Hamburg"),
    ("KRPUS", "Busan"),
    ("GIXGI", "Gibraltar"),
]


# ── Helper: delta colour and arrow ────────────────────────────────────────────

def _delta_html(pct: float, label: str = "") -> str:
    """Return an HTML snippet showing a signed percentage with colour and arrow."""
    arrow = "▲" if pct >= 0 else "▼"
    color = _RED if pct >= 0 else _GREEN   # higher fuel price = bad = red
    sign = "+" if pct >= 0 else ""
    text = label + " " if label else ""
    return (
        '<span style="font-size:0.78rem; color:' + color + ';">'
        + arrow + " " + text + sign + str(round(pct, 1)) + "%"
        + "</span>"
    )


def _badge(text: str, color: str) -> str:
    """Inline pill badge."""
    bg = _hex_to_rgba(color, 0.15)
    border = _hex_to_rgba(color, 0.35)
    return (
        '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
        'font-size:0.68rem;font-weight:700;letter-spacing:0.06em;'
        'background:' + bg + ';color:' + color + ';border:1px solid ' + border + ';">'
        + text + "</span>"
    )


def _card_html(
    label: str,
    value: str,
    sub: str = "",
    accent: str = _BLUE,
    delta_html: str = "",
    badge_text: str = "",
    badge_color: str = "",
    footer: str = "",
) -> str:
    """Return an enhanced dark KPI card HTML string."""
    sub_block = (
        '<div style="color:' + C_TEXT3 + '; font-size:0.74rem; margin-top:4px; line-height:1.3;">'
        + sub + "</div>"
    ) if sub else ""
    delta_block = (
        '<div style="margin-top:8px; display:flex; align-items:center; gap:8px;">'
        + delta_html + "</div>"
    ) if delta_html else ""
    badge_block = (
        '<div style="margin-bottom:8px;">' + _badge(badge_text, badge_color) + "</div>"
    ) if badge_text and badge_color else ""
    footer_block = (
        '<div style="border-top:1px solid ' + C_BORDER + '; margin-top:10px; padding-top:8px; '
        'font-size:0.71rem; color:' + C_TEXT3 + ';">' + footer + "</div>"
    ) if footer else ""
    border_top = "border-top:3px solid " + accent + ";"
    glow = "box-shadow:0 0 20px " + _hex_to_rgba(accent, 0.08) + ", 0 4px 16px rgba(0,0,0,0.3);"
    return (
        '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + '; '
        + 'border-radius:12px; padding:18px 20px; height:100%; ' + border_top + glow + '">'
        + badge_block
        + '<div style="font-size:0.72rem; font-weight:700; color:' + C_TEXT3 + '; '
        + 'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">'
        + label + "</div>"
        + '<div style="font-size:1.85rem; font-weight:800; color:' + C_TEXT
        + '; line-height:1.1; font-variant-numeric:tabular-nums;">'
        + value + "</div>"
        + sub_block
        + delta_block
        + footer_block
        + "</div>"
    )


def _divider() -> None:
    st.markdown(
        '<div style="height:1px; background:linear-gradient(90deg, transparent, '
        + C_BORDER + ', transparent); margin:28px 0;"></div>',
        unsafe_allow_html=True,
    )


# ── Section 0: Bunker Market Intelligence Overview (new top-level summary) ────

def _render_market_overview(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    """Compact at-a-glance intelligence panel: key signals across all dashboard sections."""
    avg_vlsfo = global_average_price("VLSFO", bunker_prices) or 615.0
    avg_hfo   = global_average_price("HFO",   bunker_prices) or 435.0
    avg_lng   = global_average_price("LNG",   bunker_prices) or 520.0
    avg_mdo   = global_average_price("MDO",   bunker_prices) or 780.0

    vlsfo_7d  = [bp.change_7d_pct for (_, ft), bp in bunker_prices.items() if ft == "VLSFO"]
    avg_7d    = (sum(vlsfo_7d) / len(vlsfo_7d)) if vlsfo_7d else 0.0
    spread    = avg_vlsfo - avg_hfo

    # Trend signal
    if avg_7d > 2.0:
        trend_label, trend_color = "RISING", _RED
    elif avg_7d < -2.0:
        trend_label, trend_color = "FALLING", _GREEN
    else:
        trend_label, trend_color = "STABLE", _AMBER

    # Scrubber quick verdict
    annual_fuel_mt = _VESSEL_VOYAGES_PER_YR * _VESSEL_FUEL_MT_VOYAGE
    net_save = spread * annual_fuel_mt - _SCRUBBER_OPEX_USD_YR - _SCRUBBER_WASH_WATER_OPEX
    pb_yrs   = (_SCRUBBER_CAPEX_USD / net_save if net_save > 0 else float("inf"))
    scrubber_signal = (
        "ATTRACTIVE" if pb_yrs < 2.5 else
        ("BORDERLINE" if pb_yrs < 5.0 else "UNATTRACTIVE")
    )
    scrubber_color = _GREEN if pb_yrs < 2.5 else (_AMBER if pb_yrs < 5.0 else _RED)

    # Hedge momentum
    vlsfo_30d = [bp.change_30d_pct for (_, ft), bp in bunker_prices.items() if ft == "VLSFO"]
    avg_30d   = (sum(vlsfo_30d) / len(vlsfo_30d)) if vlsfo_30d else 0.0
    momentum  = avg_7d * 0.6 + avg_30d * 0.4
    if momentum > 2.5:
        hedge_signal, hedge_color = "HEDGE NOW", _RED
    elif momentum > 0.8:
        hedge_signal, hedge_color = "PARTIAL HEDGE", _AMBER
    elif momentum < -2.0:
        hedge_signal, hedge_color = "STAY UNHEDGED", _GREEN
    else:
        hedge_signal, hedge_color = "MONITOR", _BLUE

    # Alt fuel leader by adoption
    top_alt = max(_ALT_FUELS, key=lambda f: f["fleet_adoption_pct"])

    overview_items = [
        ("VLSFO Spot", "$" + "{:,.0f}".format(avg_vlsfo) + "/mt",
         trend_label, trend_color, "7d: " + ("+" if avg_7d >= 0 else "") + str(round(avg_7d, 1)) + "%"),
        ("HFO / Scrubber Spread", "$" + str(int(spread)) + "/mt",
         scrubber_signal, scrubber_color, "Scrubber payback " + (str(round(pb_yrs, 1)) + "y" if pb_yrs < 50 else "N/A")),
        ("Hedge Signal", hedge_signal,
         hedge_signal, hedge_color, "30d momentum " + ("+" if avg_30d >= 0 else "") + str(round(avg_30d, 1)) + "%"),
        ("Leading Alt Fuel", top_alt["name"] + " " + str(top_alt["fleet_adoption_pct"]) + "%",
         top_alt["status"], top_alt["color"], str(top_alt["co2_reduction_pct"]) + "% CO2 reduction"),
    ]

    grid_html = "<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:4px;'>"
    for title, value, badge_t, badge_c, sub_t in overview_items:
        bg  = _hex_to_rgba(badge_c, 0.06)
        brd = _hex_to_rgba(badge_c, 0.22)
        grid_html += (
            "<div style='background:" + bg + ";border:1px solid " + brd + ";"
            "border-top:3px solid " + badge_c + ";border-radius:10px;padding:14px 16px;'>"
            "<div style='font-size:0.65rem;text-transform:uppercase;letter-spacing:0.09em;"
            "color:" + C_TEXT3 + ";font-weight:600;margin-bottom:6px;'>" + title + "</div>"
            "<div style='font-size:1.3rem;font-weight:800;color:" + C_TEXT + ";"
            "font-variant-numeric:tabular-nums;line-height:1.1;'>" + value + "</div>"
            "<div style='margin-top:6px;'>"
            "<span style='background:" + _hex_to_rgba(badge_c, 0.12) + ";color:" + badge_c + ";"
            "border:1px solid " + _hex_to_rgba(badge_c, 0.3) + ";padding:2px 8px;"
            "border-radius:999px;font-size:0.63rem;font-weight:700;letter-spacing:0.07em;'>"
            + badge_t + "</span>"
            "</div>"
            "<div style='font-size:0.72rem;color:" + C_TEXT3 + ";margin-top:6px;'>"
            + sub_t + "</div>"
            "</div>"
        )
    grid_html += "</div>"

    st.markdown(grid_html, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)


# ── Section 1: Hero Banner ─────────────────────────────────────────────────────

def _render_hero(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    avg_vlsfo = global_average_price("VLSFO", bunker_prices)
    avg_hfo   = global_average_price("HFO",   bunker_prices)
    avg_mdo   = global_average_price("MDO",   bunker_prices)
    avg_lng   = global_average_price("LNG",   bunker_prices)
    n_hubs    = len(HUB_META)

    # Average 7d change for VLSFO
    vlsfo_changes = [
        bp.change_7d_pct for (_, ft), bp in bunker_prices.items() if ft == "VLSFO"
    ]
    avg_vlsfo_7d = sum(vlsfo_changes) / len(vlsfo_changes) if vlsfo_changes else 0.0
    chg_arrow    = "▲" if avg_vlsfo_7d >= 0 else "▼"
    chg_color    = _RED if avg_vlsfo_7d >= 0 else _GREEN
    chg_sign     = "+" if avg_vlsfo_7d >= 0 else ""
    chg_str      = chg_arrow + " " + chg_sign + str(round(avg_vlsfo_7d, 1)) + "% 7d"

    pills = [
        ("VLSFO", "$" + str(int(avg_vlsfo)) + "/mt", _BLUE),
        ("IFO 380", "$" + str(int(avg_hfo)) + "/mt", _AMBER),
        ("MDO", "$" + str(int(avg_mdo)) + "/mt", _PURPLE),
        ("LNG", "$" + str(int(avg_lng)) + "/mt", _GREEN),
    ]

    pill_html = ""
    for fuel_label, fuel_val, fuel_color in pills:
        pill_html += (
            '<span style="display:inline-flex;align-items:center;gap:6px;'
            'background:' + _hex_to_rgba(fuel_color, 0.12) + ';'
            'border:1px solid ' + _hex_to_rgba(fuel_color, 0.3) + ';'
            'border-radius:999px;padding:4px 14px;font-size:0.82rem;'
            'font-weight:600;color:' + C_TEXT + ';margin:0 4px 4px 0;">'
            '<span style="width:8px;height:8px;border-radius:50%;background:' + fuel_color + ';'
            'display:inline-block;flex-shrink:0;box-shadow:0 0 6px ' + fuel_color + ';"></span>'
            + fuel_label + '&nbsp;<span style="color:' + fuel_color + ';">' + fuel_val + '</span>'
            '</span>'
        )

    st.markdown(
        '<div style="background:linear-gradient(135deg,rgba(59,130,246,0.12) 0%,'
        'rgba(16,185,129,0.06) 50%,rgba(26,34,53,0.98) 100%);'
        'border:1px solid rgba(59,130,246,0.25);border-radius:16px;'
        'padding:28px 32px;margin-bottom:28px;'
        'box-shadow:0 0 60px rgba(59,130,246,0.08),0 4px 24px rgba(0,0,0,0.4);">'

        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
        '<div style="width:8px;height:8px;border-radius:50%;background:' + _BLUE + ';'
        'box-shadow:0 0 8px ' + _BLUE + ';animation:pulse 2s infinite;"></div>'
        '<span style="font-size:0.70rem;font-weight:700;letter-spacing:0.12em;'
        'color:' + _BLUE + ';text-transform:uppercase;">Bunker Fuel Intelligence Dashboard</span>'
        '</div>'

        '<div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;">'
        '<span style="font-size:2.6rem;font-weight:800;color:' + C_TEXT
        + ';line-height:1;font-variant-numeric:tabular-nums;">'
        'VLSFO $' + str(int(avg_vlsfo)) + '<span style="font-size:1.2rem;color:' + C_TEXT2
        + ';font-weight:500;">/mt</span></span>'
        '<span style="font-size:1.1rem;font-weight:700;color:' + chg_color + ';">'
        + chg_str + '</span>'
        '</div>'

        '<div style="margin-top:14px;display:flex;flex-wrap:wrap;gap:2px;">'
        + pill_html +
        '</div>'

        '<div style="margin-top:14px;display:flex;align-items:center;gap:20px;'
        'flex-wrap:wrap;font-size:0.80rem;color:' + C_TEXT2 + ';">'
        '<span><b style="color:' + C_TEXT + ';">' + str(n_hubs) + '</b> hubs tracked</span>'
        '<span style="color:' + C_TEXT3 + ';">|</span>'
        '<span>Fuel = <b style="color:' + _AMBER + ';">40-60%</b> of voyage cost</span>'
        '<span style="color:' + C_TEXT3 + ';">|</span>'
        '<span>IMO 2020 in force &mdash; CII active &mdash; 2030 target: -40% CO2</span>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Section 2: Fuel Cost Dashboard (metric cards) ─────────────────────────────

def _render_fuel_dashboard(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Fuel Cost Dashboard",
        "Global average spot prices with 7-day and 30-day change — Q1 2026 baseline",
    )

    # Map IFO380 -> HFO since we store HFO
    display_fuels = [
        ("VLSFO",  "VLSFO", "Very Low Sulphur Fuel Oil",    _BLUE),
        ("IFO 380", "HFO",  "Intermediate Fuel Oil 380 cSt", _AMBER),
        ("LNG",    "LNG",   "Liquefied Natural Gas",         _GREEN),
        ("MDO",    "MDO",   "Marine Diesel Oil",              _PURPLE),
    ]

    cols = st.columns(4)
    for col, (display_name, internal_key, full_label, color) in zip(cols, display_fuels):
        avg = global_average_price(internal_key, bunker_prices)
        changes_7d  = [bp.change_7d_pct  for (_, ft), bp in bunker_prices.items() if ft == internal_key]
        changes_30d = [bp.change_30d_pct for (_, ft), bp in bunker_prices.items() if ft == internal_key]
        avg_7d  = sum(changes_7d)  / len(changes_7d)  if changes_7d  else 0.0
        avg_30d = sum(changes_30d) / len(changes_30d) if changes_30d else 0.0

        if avg == 0.0:
            with col:
                st.markdown(
                    _card_html(display_name, "N/A", sub="No data available", accent=color),
                    unsafe_allow_html=True,
                )
            continue

        # Hedge signal based on 30d trend
        if avg_30d > 3.0:
            badge_t, badge_c = "RISING", _RED
        elif avg_30d < -3.0:
            badge_t, badge_c = "FALLING", _GREEN
        else:
            badge_t, badge_c = "STABLE", _AMBER

        with col:
            st.markdown(
                _card_html(
                    label=display_name,
                    value="$" + "{:,.0f}".format(avg) + "/mt",
                    sub=full_label,
                    accent=color,
                    delta_html=_delta_html(avg_7d, "7d") + "&nbsp;&nbsp;" + _delta_html(avg_30d, "30d"),
                    badge_text=badge_t,
                    badge_color=badge_c,
                    footer="Global avg across " + str(len(changes_7d)) + " hub" + ("s" if len(changes_7d) != 1 else ""),
                ),
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)


# ── Section 3: 90-Day Bunker Price Time Series ────────────────────────────────

def _render_price_timeseries(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Bunker Price Time Series — 90-Day History",
        "All major fuel types on a single chart — WTI-correlated synthetic history",
    )

    fuel_series: dict[str, tuple[list, list]] = {}
    rng = _rand.Random(99)

    for ft, color in [("VLSFO", _BLUE), ("HFO", _AMBER), ("MDO", _PURPLE), ("LNG", _GREEN)]:
        try:
            history = price_history_synthetic(ft, weeks=13)  # ~91 days
        except Exception as exc:
            logger.warning("90d history unavailable for %s: %s", ft, exc)
            history = []

        if not history:
            # Generate 90-day synthetic fallback
            base = global_average_price(ft, bunker_prices) or 600.0
            today = datetime.date.today()
            dates_fb = [(today - datetime.timedelta(days=89 - i)).isoformat() for i in range(90)]
            prices_fb = [base + rng.gauss(0, base * 0.02) for _ in range(90)]
            # Smooth with a running average
            smooth = prices_fb[:]
            for i in range(2, 88):
                smooth[i] = (prices_fb[i-2] + prices_fb[i-1] + prices_fb[i] + prices_fb[i+1] + prices_fb[i+2]) / 5
            fuel_series[ft] = (dates_fb, smooth)
        else:
            dates_h  = [h[0].isoformat() for h in history]
            prices_h = [h[1] for h in history]
            fuel_series[ft] = (dates_h, prices_h)

    fig = go.Figure()

    for ft, color in [("VLSFO", _BLUE), ("HFO", _AMBER), ("MDO", _PURPLE), ("LNG", _GREEN)]:
        if ft not in fuel_series:
            continue
        dates_s, prices_s = fuel_series[ft]
        display_ft = "IFO 380" if ft == "HFO" else ft

        fig.add_trace(
            go.Scatter(
                x=dates_s,
                y=prices_s,
                mode="lines",
                name=display_ft,
                line=dict(color=color, width=2.5),
                fill="tozeroy",
                fillcolor=_hex_to_rgba(color, 0.05),
                hovertemplate="<b>" + display_ft + "</b><br>%{x}<br>$%{y:.0f}/mt<extra></extra>",
            )
        )

    apply_dark_layout(fig, title="", height=380, showlegend=True)
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Price ($/mt)",
        margin=dict(l=50, r=20, t=20, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="right",
            x=1.0,
            font=dict(size=12),
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.04)", tickprefix="$")

    st.plotly_chart(fig, use_container_width=True, key="bunker_ts_90d")

    # Spread callout
    avg_vlsfo = global_average_price("VLSFO", bunker_prices)
    avg_hfo   = global_average_price("HFO",   bunker_prices)
    spread    = avg_vlsfo - avg_hfo if avg_vlsfo > 0 and avg_hfo > 0 else 0.0
    spread_color = _GREEN if spread < 100 else (_AMBER if spread < 180 else _RED)
    st.markdown(
        '<div style="background:' + _hex_to_rgba(_BLUE, 0.05) + '; border:1px solid '
        + _hex_to_rgba(_BLUE, 0.15) + '; border-radius:10px; padding:12px 18px; '
        + 'font-size:0.83rem; color:' + C_TEXT2 + ';">'
        + "VLSFO / IFO 380 compliance spread: "
        + "<b style='color:" + spread_color + ";'>$" + str(int(spread)) + "/mt</b>"
        + " &nbsp;|&nbsp; Wide spreads favour scrubber-equipped vessels."
        + " Narrow spreads erode the scrubber payback case."
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 4: Port-Specific Bunker Prices Table ─────────────────────────────

def _render_port_prices_table(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Port-Specific Bunker Prices",
        "Spot prices at major bunkering hubs — Singapore, Rotterdam, Fujairah, Houston and more",
    )

    rows = []
    for locode, port_name in _MAJOR_PORTS:
        row: dict[str, Any] = {"Port": port_name, "Region": HUB_META.get(locode, {}).get("region", "—")}
        for ft in ("VLSFO", "HFO", "MDO", "LNG"):
            bp = bunker_prices.get((locode, ft))
            if bp and bp.price_per_mt and bp.price_per_mt > 0:
                chg_sign = "+" if bp.change_7d_pct >= 0 else ""
                row[ft + " ($/mt)"] = "$" + str(int(bp.price_per_mt))
                row[ft + " 7d"] = chg_sign + str(round(bp.change_7d_pct, 1)) + "%"
            else:
                row[ft + " ($/mt)"] = "—"
                row[ft + " 7d"] = "—"
        rows.append(row)

    if not rows:
        st.info("Port price data unavailable.")
        return

    df = pd.DataFrame(rows)

    # Style: colour the 7d change columns
    def _style_change(val: str) -> str:
        if val.startswith("+"):
            return "color: " + _RED + "; font-weight:600;"
        if val.startswith("-"):
            return "color: " + _GREEN + "; font-weight:600;"
        return ""

    change_cols = [c for c in df.columns if "7d" in c]
    price_cols  = [c for c in df.columns if "$/mt" in c]

    styled = (
        df.style
        .applymap(_style_change, subset=change_cols)
        .set_properties(subset=price_cols, **{"font-weight": "600", "color": C_TEXT})
        .set_table_styles([
            {"selector": "th", "props": [
                ("background-color", "#1a2235"),
                ("color", C_TEXT2),
                ("font-size", "0.73rem"),
                ("font-weight", "700"),
                ("text-transform", "uppercase"),
                ("letter-spacing", "0.06em"),
                ("border-bottom", "1px solid rgba(255,255,255,0.1)"),
            ]},
            {"selector": "td", "props": [
                ("font-size", "0.84rem"),
                ("border-bottom", "1px solid rgba(255,255,255,0.05)"),
            ]},
        ])
    )

    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Export button
    csv = df.to_csv(index=False)
    st.download_button(
        label="Download port prices CSV",
        data=csv,
        file_name="bunker_port_prices.csv",
        mime="text/csv",
        key="download_port_prices_csv",
    )


# ── Section 5: Route Cost Calculator ─────────────────────────────────────────

def _render_cost_calculator(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Route Bunker Cost Calculator",
        "Estimated bunker spend and cost-per-FEU for major routes at current spot prices",
    )

    route_options = {r.name: r.id for r in ROUTES}
    col_a, col_b, col_c = st.columns([2, 1, 1])

    with col_a:
        selected_route_name = st.selectbox(
            "Route",
            options=list(route_options.keys()),
            index=0,
            key="bunker_calc_route",
        )
    with col_b:
        fuel_type = st.radio(
            "Fuel type",
            options=["VLSFO", "HFO", "MDO"],
            index=0,
            key="bunker_calc_fuel",
        )
    with col_c:
        feu_count = st.number_input(
            "FEU count",
            min_value=1,
            max_value=4000,
            value=100,
            step=50,
            key="bunker_calc_feu",
        )

    route_id       = route_options[selected_route_name]
    analysis       = compute_voyage_fuel_cost(route_id, fuel_type, bunker_prices)
    total_feu_cap  = (8000 * 0.85) / 2.0   # ~3400 FEU
    feu_share      = min(feu_count / total_feu_cap, 1.0) if feu_count > 0 else 0.0
    optimal_cost   = analysis.optimal_fuel_cost or 0.0
    user_fuel_cost = optimal_cost * feu_share
    cost_per_feu   = analysis.cost_per_feu or 0.0

    typical_freight_total = feu_count * 2_400.0
    fuel_pct              = (user_fuel_cost / typical_freight_total * 100.0
                             if typical_freight_total > 0 else 0.0)

    m1, m2, m3, m4 = st.columns(4)

    with m1:
        st.markdown(
            _card_html(
                "Total Fuel Cost",
                "$" + "{:,.0f}".format(user_fuel_cost),
                sub="for " + str(feu_count) + " FEU via " + analysis.optimal_fuel_type,
                accent=_FUEL_COLORS.get(analysis.optimal_fuel_type, _BLUE),
            ),
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            _card_html(
                "Cost per FEU",
                "$" + "{:,.0f}".format(cost_per_feu),
                sub="fuel only, optimal port",
                accent=_CYAN,
            ),
            unsafe_allow_html=True,
        )
    with m3:
        pct_color = _RED if fuel_pct > 50 else (_AMBER if fuel_pct > 40 else _BLUE)
        st.markdown(
            _card_html(
                "% of Freight Revenue",
                str(round(fuel_pct, 1)) + "%",
                sub="vs $2,400/FEU avg rate",
                accent=pct_color,
            ),
            unsafe_allow_html=True,
        )
    with m4:
        st.markdown(
            _card_html(
                "Breakeven Rate",
                "$" + "{:,.0f}".format(analysis.breakeven_rate) + "/FEU",
                sub="fuel at 50% of voyage cost",
                accent=_PURPLE,
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # Fuel comparison table
    st.markdown(
        '<div style="font-size:0.85rem; font-weight:600; color:' + C_TEXT2
        + '; margin-bottom:10px;">Fuel Comparison — ' + selected_route_name + '</div>',
        unsafe_allow_html=True,
    )

    rows = []
    for ft in ("VLSFO", "HFO", "LNG"):
        a = compute_voyage_fuel_cost(route_id, ft, bunker_prices)
        if ft == "LNG" and not a.lng_available:
            rows.append({
                "Fuel": "LNG",
                "Consumption (MT)": round(a.fuel_consumption_mt, 0),
                "Voyage Cost ($)": "N/A — no hub",
                "Cost / FEU ($)": "N/A",
                "Optimal Port": get_optimal_bunkering_port(route_id, ft, bunker_prices),
                "vs VLSFO": "—",
            })
            continue
        voyage_cost = (
            a.vlsfo_cost if ft == "VLSFO"
            else (a.hfo_cost if ft == "HFO" else a.lng_cost)
        ) or 0.0
        vlsfo_ref = compute_voyage_fuel_cost(route_id, "VLSFO", bunker_prices).vlsfo_cost or 0.0
        vs_vlsfo  = ((voyage_cost - vlsfo_ref) / vlsfo_ref * 100.0
                     if vlsfo_ref > 0 and ft != "VLSFO" else 0.0)
        vs_str = ("—" if ft == "VLSFO"
                  else (("+" if vs_vlsfo >= 0 else "") + str(round(vs_vlsfo, 1)) + "%"))
        cpf = "{:,.0f}".format(voyage_cost / total_feu_cap) if total_feu_cap > 0 and voyage_cost > 0 else "N/A"
        rows.append({
            "Fuel": "IFO 380" if ft == "HFO" else ft,
            "Consumption (MT)": round(a.fuel_consumption_mt or 0, 0),
            "Voyage Cost ($)": "{:,.0f}".format(voyage_cost),
            "Cost / FEU ($)": cpf,
            "Optimal Port": HUB_META.get(a.bunkering_port, {}).get("name", a.bunkering_port or "—"),
            "vs VLSFO": vs_str,
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    opt_port_name = HUB_META.get(analysis.bunkering_port, {}).get("name", analysis.bunkering_port)
    st.markdown(
        '<div style="background:' + _hex_to_rgba(_BLUE, 0.07) + '; border:1px solid '
        + _hex_to_rgba(_BLUE, 0.2) + '; border-radius:10px; padding:12px 16px; '
        + 'font-size:0.83rem; color:' + C_TEXT2 + '; margin-top:8px;">'
        + "Optimal bunkering port for <b style='color:" + C_TEXT + ";'>"
        + selected_route_name + "</b>: "
        + "<b style='color:" + _BLUE + ";'>" + str(opt_port_name) + "</b>"
        + " &nbsp;|&nbsp; Distance: <b>" + "{:,.0f}".format(analysis.voyage_distance_nm) + " nm</b>"
        + " &nbsp;|&nbsp; Transit: <b>" + str(int(analysis.transit_days)) + " days</b>"
        + " &nbsp;|&nbsp; Consumption: <b>" + str(int(analysis.fuel_consumption_mt)) + " MT</b>"
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 6: Fuel Hedge Indicator ──────────────────────────────────────────

def _render_hedge_indicator(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Fuel Hedge Indicator",
        "Should carriers be hedging at current bunker prices? Signal based on price trends and macro context.",
    )

    avg_vlsfo    = global_average_price("VLSFO", bunker_prices)
    vlsfo_7d     = [bp.change_7d_pct  for (_, ft), bp in bunker_prices.items() if ft == "VLSFO"]
    vlsfo_30d    = [bp.change_30d_pct for (_, ft), bp in bunker_prices.items() if ft == "VLSFO"]
    avg_7d       = sum(vlsfo_7d)  / len(vlsfo_7d)  if vlsfo_7d  else 0.0
    avg_30d      = sum(vlsfo_30d) / len(vlsfo_30d) if vlsfo_30d else 0.0

    # Signal logic: rising price + multi-week trend = hedge; falling = wait
    momentum_score = avg_7d * 0.6 + avg_30d * 0.4   # weighted momentum

    if momentum_score > 2.5:
        signal       = "HEDGE NOW"
        signal_color = _RED
        signal_icon  = "BULLISH"
        rationale    = (
            "Prices are rising across 7-day and 30-day windows. "
            "Forward purchasing or FFA hedges can lock in current rates before further upside. "
            "Consider 30-90 day forward contracts on VLSFO."
        )
    elif momentum_score > 0.8:
        signal       = "PARTIAL HEDGE"
        signal_color = _AMBER
        signal_icon  = "CAUTIOUS"
        rationale    = (
            "Prices are drifting higher but momentum is modest. "
            "A 30-50% hedge of anticipated fuel consumption reduces downside risk "
            "while preserving upside if prices retreat."
        )
    elif momentum_score < -2.0:
        signal       = "STAY UNHEDGED"
        signal_color = _GREEN
        signal_icon  = "BEARISH"
        rationale    = (
            "Prices are in a declining trend. Purchasing spot fuel may be optimal. "
            "If already hedged, consider restructuring forwards at lower levels."
        )
    else:
        signal       = "MONITOR"
        signal_color = _BLUE
        signal_icon  = "NEUTRAL"
        rationale    = (
            "Price momentum is flat. Market conditions do not strongly favour "
            "hedging or deferral. Maintain a balanced position and review weekly."
        )

    # Gauge-style visual using a horizontal progress bar
    # Normalise momentum_score to 0-100 (range -5 to +5)
    gauge_pct  = max(0, min(100, int((momentum_score + 5.0) / 10.0 * 100)))
    gauge_color = signal_color

    col_signal, col_detail = st.columns([1, 2])

    with col_signal:
        st.markdown(
            '<div style="background:' + _hex_to_rgba(signal_color, 0.08) + ';'
            'border:1px solid ' + _hex_to_rgba(signal_color, 0.25) + ';'
            'border-radius:14px;padding:24px 20px;text-align:center;">'

            + _badge(signal_icon, signal_color) +

            '<div style="font-size:1.6rem;font-weight:800;color:' + signal_color
            + ';margin:14px 0 6px;">' + signal + '</div>'

            '<div style="font-size:0.80rem;color:' + C_TEXT2
            + ';margin-bottom:16px;">VLSFO Momentum Score</div>'

            '<div style="background:rgba(255,255,255,0.07);border-radius:6px;'
            'height:10px;width:100%;overflow:hidden;">'
            '<div style="width:' + str(gauge_pct) + '%;height:10px;border-radius:6px;'
            'background:linear-gradient(90deg,' + _GREEN + ',' + _AMBER + ',' + _RED + ');'
            'opacity:0.9;"></div></div>'

            '<div style="display:flex;justify-content:space-between;'
            'font-size:0.68rem;color:' + C_TEXT3 + ';margin-top:4px;">'
            '<span>Bearish</span><span>Neutral</span><span>Bullish</span></div>'

            '<div style="margin-top:16px;font-size:0.78rem;color:' + C_TEXT3 + ';">'
            '7d: <b style="color:' + (_RED if avg_7d >= 0 else _GREEN) + ';">'
            + ("+" if avg_7d >= 0 else "") + str(round(avg_7d, 1)) + '%</b>'
            '&nbsp;&nbsp;30d: <b style="color:' + (_RED if avg_30d >= 0 else _GREEN) + ';">'
            + ("+" if avg_30d >= 0 else "") + str(round(avg_30d, 1)) + '%</b>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    with col_detail:
        # Hedging context cards
        hedge_metrics = [
            ("Current VLSFO", "$" + str(int(avg_vlsfo)) + "/mt", _BLUE, "Global average spot"),
            ("1-Yr Historical Avg", "$610/mt",  _CYAN,  "2025 average reference"),
            ("52-Week Range",      "$580–$660/mt", _AMBER, "Low to high"),
            ("Breakeven Fuel Cost","$540/mt",   _GREEN, "Avg breakeven across routes"),
        ]

        inner_cols = st.columns(2)
        for i, (lbl, val, clr, sub_t) in enumerate(hedge_metrics):
            with inner_cols[i % 2]:
                st.markdown(
                    _card_html(lbl, val, sub=sub_t, accent=clr),
                    unsafe_allow_html=True,
                )

        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
        st.markdown(
            '<div style="background:' + _hex_to_rgba(_BLUE, 0.05) + ';border:1px solid '
            + _hex_to_rgba(_BLUE, 0.15) + ';border-radius:10px;padding:14px 16px;'
            'font-size:0.83rem;color:' + C_TEXT2 + ';line-height:1.55;">'
            '<b style="color:' + C_TEXT + ';">Rationale:</b> ' + rationale
            + '</div>',
            unsafe_allow_html=True,
        )


# ── Section 7: Bunker Cost Impact on Freight Rates ───────────────────────────

def _render_correlation(macro_data: dict, freight_data: dict) -> None:
    section_header(
        "Bunker Cost Impact on Freight Rates",
        "WTI crude oil vs container freight rates — Pearson correlation with regression band",
    )

    wti_df = macro_data.get("WTISPLC") or macro_data.get("WTI") or pd.DataFrame()
    if isinstance(wti_df, pd.DataFrame) and not wti_df.empty and "value" in wti_df.columns:
        wti_series = (
            wti_df.set_index("date")["value"].dropna()
            if "date" in wti_df.columns
            else wti_df["value"].dropna()
        )
    else:
        wti_series = pd.Series(dtype=float)

    freight_series = pd.Series(dtype=float)
    for key in ("transpacific_eb", "FBX01", "fbx01"):
        df = freight_data.get(key, pd.DataFrame())
        if isinstance(df, pd.DataFrame) and not df.empty:
            col = "rate" if "rate" in df.columns else ("value" if "value" in df.columns else None)
            if col:
                idx_col = "date" if "date" in df.columns else None
                freight_series = df.set_index(idx_col)[col].dropna() if idx_col else df[col].dropna()
                break

    rng_corr = _rand.Random(42)
    if len(wti_series) < 10 or len(freight_series) < 10:
        n = 90
        wti_vals     = [70.0 + rng_corr.gauss(0, 8) for _ in range(n)]
        freight_vals = [2000.0 + w * 22 + rng_corr.gauss(0, 300) for w in wti_vals]
        data_label   = "Synthetic (90-day simulated)"
    else:
        try:
            common = wti_series.index.intersection(freight_series.index)
            if len(common) < 5:
                raise ValueError("insufficient overlap")
            wti_vals     = [float(wti_series[d]) for d in common]
            freight_vals = [float(freight_series[d]) for d in common]
            data_label   = "Live (FRED WTI + freight index)"
        except Exception:
            n = 90
            wti_vals     = [70.0 + rng_corr.gauss(0, 8) for _ in range(n)]
            freight_vals = [2000.0 + w * 22 + rng_corr.gauss(0, 300) for w in wti_vals]
            data_label   = "Synthetic fallback"

    n_pts = len(wti_vals)
    if n_pts == 0 or len(freight_vals) != n_pts:
        st.info("Insufficient data to render correlation scatter.")
        return

    mean_w  = sum(wti_vals) / n_pts
    mean_f  = sum(freight_vals) / n_pts
    cov     = sum((w - mean_w) * (f - mean_f) for w, f in zip(wti_vals, freight_vals)) / n_pts
    std_w   = (sum((w - mean_w) ** 2 for w in wti_vals) / n_pts) ** 0.5
    std_f   = (sum((f - mean_f) ** 2 for f in freight_vals) / n_pts) ** 0.5
    corr    = cov / (std_w * std_f) if (std_w > 0 and std_f > 0) else 0.0
    slope   = cov / (std_w ** 2) if std_w > 0 else 0.0
    intercept = mean_f - slope * mean_w

    x_min, x_max = min(wti_vals), max(wti_vals)
    reg_x = [x_min, x_max]
    reg_y = [slope * x + intercept for x in reg_x]
    residuals = [f - (slope * w + intercept) for w, f in zip(wti_vals, freight_vals)]
    resid_std = (sum(r ** 2 for r in residuals) / max(n_pts - 2, 1)) ** 0.5
    ci_upper  = [y + 1.96 * resid_std for y in reg_y]
    ci_lower  = [y - 1.96 * resid_std for y in reg_y]

    fig = go.Figure()

    # Scatter points
    fig.add_trace(go.Scatter(
        x=wti_vals, y=freight_vals,
        mode="markers",
        name="Weekly observations",
        marker=dict(color=_BLUE, size=7, opacity=0.6, line=dict(color="rgba(255,255,255,0.2)", width=0.5)),
        hovertemplate="WTI: $%{x:.1f}/bbl<br>Freight: $%{y:,.0f}/FEU<extra></extra>",
    ))

    # Regression line
    fig.add_trace(go.Scatter(
        x=reg_x, y=reg_y,
        mode="lines",
        name="Regression",
        line=dict(color=_GREEN, width=2.5, dash="dash"),
        hoverinfo="skip",
    ))

    # CI band
    fig.add_trace(go.Scatter(
        x=reg_x, y=ci_upper, mode="lines",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=reg_x, y=ci_lower, mode="lines",
        line=dict(width=0), fill="tonexty",
        fillcolor=_hex_to_rgba(_GREEN, 0.10),
        name="95% CI",
        hovertemplate="95% CI: $%{y:,.0f}/FEU<extra></extra>",
    ))

    apply_dark_layout(fig, title="WTI Crude vs Trans-Pacific Freight Rate", height=400)
    fig.update_layout(
        xaxis_title="WTI Crude Oil ($/bbl)",
        yaxis_title="Freight Rate ($/FEU)",
        margin=dict(l=50, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0, font=dict(size=11)),
    )

    st.plotly_chart(fig, use_container_width=True, key="bunker_wti_correlation")

    corr_pct   = round(abs(corr) * 100, 1)
    direction  = "positively" if corr >= 0 else "negatively"
    corr_color = _GREEN if corr_pct >= 50 else (_AMBER if corr_pct >= 30 else C_TEXT2)
    st.markdown(
        '<div style="background:' + _hex_to_rgba(_BLUE, 0.06) + '; border:1px solid '
        + _hex_to_rgba(_BLUE, 0.18) + '; border-radius:10px; padding:12px 16px; '
        + 'font-size:0.84rem; color:' + C_TEXT2 + '; margin-top:-8px;">'
        + "Fuel costs are <b style='color:" + corr_color + ";'>"
        + str(corr_pct) + "% " + direction + " correlated</b> with freight rates"
        + " (Pearson r = " + str(round(corr, 3)) + "). Source: " + data_label + "."
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 8: Oil Price vs Shipping Stock Correlation ────────────────────────

def _render_oil_stock_correlation() -> None:
    section_header(
        "Oil Price vs Shipping Stocks",
        "Synthetic correlation between WTI crude oil and major shipping equities — 90-day window",
    )

    rng_s = _rand.Random(7)
    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=89 - i)).isoformat() for i in range(90)]

    # Synthetic WTI base
    wti_base = [70.0]
    for _ in range(89):
        wti_base.append(wti_base[-1] + rng_s.gauss(0, 1.2))

    # Shipping stocks: some positively correlated (fuel cost drag), some negatively
    stocks = {
        "ZIM":   {"beta": -0.8,  "base": 18.0,  "noise": 0.8,  "color": _RED},
        "DAC":   {"beta": -0.6,  "base": 82.0,  "noise": 1.5,  "color": _AMBER},
        "MATX":  {"beta": -0.5,  "base": 105.0, "noise": 1.2,  "color": _BLUE},
        "DANAOS":{"beta": -0.55, "base": 88.0,  "noise": 1.3,  "color": _PURPLE},
    }

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.4, 0.6],
        vertical_spacing=0.06,
        subplot_titles=["WTI Crude Oil ($/bbl)", "Shipping Equity Prices (rebased to 100)"],
    )

    # WTI panel
    fig.add_trace(
        go.Scatter(
            x=dates, y=wti_base,
            mode="lines", name="WTI Crude",
            line=dict(color=_AMBER, width=2.2),
            fill="tozeroy", fillcolor=_hex_to_rgba(_AMBER, 0.06),
            hovertemplate="WTI: $%{y:.1f}/bbl<extra></extra>",
        ),
        row=1, col=1,
    )

    # Equities panel — rebased to 100
    for ticker, cfg in stocks.items():
        prices = [cfg["base"]]
        for i in range(1, 90):
            wti_move = wti_base[i] - wti_base[i - 1]
            prices.append(prices[-1] + cfg["beta"] * wti_move + rng_s.gauss(0, cfg["noise"]))
        rebased = [p / prices[0] * 100 for p in prices]
        fig.add_trace(
            go.Scatter(
                x=dates, y=rebased,
                mode="lines", name=ticker,
                line=dict(color=cfg["color"], width=1.8),
                hovertemplate=ticker + ": %{y:.1f} (rebased)<extra></extra>",
            ),
            row=2, col=1,
        )

    apply_dark_layout(fig, title="", height=480, showlegend=True)
    fig.update_layout(
        margin=dict(l=50, r=20, t=40, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1.0, font=dict(size=11)),
    )
    for ax in ["xaxis", "xaxis2", "yaxis", "yaxis2"]:
        if hasattr(fig.layout, ax):
            fig.layout[ax].update(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=10))
    fig.update_annotations(font=dict(color=C_TEXT2, size=12))
    fig.update_yaxes(tickprefix="$", row=1, col=1)

    st.plotly_chart(fig, use_container_width=True, key="bunker_oil_stock_corr")

    st.markdown(
        '<div style="background:' + _hex_to_rgba(_AMBER, 0.05) + '; border:1px solid '
        + _hex_to_rgba(_AMBER, 0.15) + '; border-radius:10px; padding:12px 16px; '
        + 'font-size:0.82rem; color:' + C_TEXT2 + ';">'
        + "<b style='color:" + C_TEXT + ";'>Interpretation:</b> "
        + "Shipping equities typically exhibit a <b>negative beta</b> to crude oil — rising fuel costs "
        + "compress margins, weighing on share prices. The effect is strongest for spot-market operators "
        + "(ZIM, DAC) and muted for long-term charter holders with contractual fuel clauses. "
        + "Data shown is synthetic for illustrative purposes."
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 9: Alternative Fuels Tracker ─────────────────────────────────────

def _render_alt_fuels_tracker(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Alternative Fuels Tracker",
        "LNG, methanol, ammonia and bio-VLSFO — adoption rates, price premium/discount vs VLSFO",
    )

    avg_vlsfo = global_average_price("VLSFO", bunker_prices) or 615.0

    cols = st.columns(len(_ALT_FUELS))
    for col, fuel in zip(cols, _ALT_FUELS):
        color    = fuel["color"]
        prem_pct = fuel["vlsfo_premium_pct"]
        prem_str = ("+" if prem_pct >= 0 else "") + str(round(prem_pct, 1)) + "%"
        prem_color = _RED if prem_pct > 0 else _GREEN
        adpt_pct = fuel["fleet_adoption_pct"]
        adpt_bar = int(min(adpt_pct * 5, 100))   # scale: 20% adoption = full bar

        with col:
            st.markdown(
                '<div style="background:' + _hex_to_rgba(color, 0.07) + ';'
                'border:1px solid ' + _hex_to_rgba(color, 0.22) + ';'
                'border-radius:12px;padding:18px 16px;height:100%;'
                'box-shadow:0 4px 16px rgba(0,0,0,0.25);">'

                + _badge(fuel["status"], color) +

                '<div style="font-size:1.15rem;font-weight:800;color:' + color
                + ';margin:10px 0 4px;">' + fuel["name"] + '</div>'

                '<div style="font-size:1.6rem;font-weight:700;color:' + C_TEXT
                + ';font-variant-numeric:tabular-nums;">$' + str(fuel["price_per_mt"]) + '/mt</div>'

                '<div style="font-size:0.80rem;color:' + prem_color
                + ';font-weight:600;margin-top:4px;">' + prem_str + ' vs VLSFO</div>'

                '<div style="margin-top:12px;">'
                '<div style="font-size:0.72rem;color:' + C_TEXT3
                + ';text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">'
                'Fleet Adoption</div>'
                '<div style="background:rgba(255,255,255,0.07);border-radius:4px;height:5px;">'
                '<div style="width:' + str(adpt_bar) + '%;height:5px;border-radius:4px;background:'
                + color + ';"></div></div>'
                '<div style="font-size:0.78rem;color:' + C_TEXT2
                + ';margin-top:4px;">' + str(adpt_pct) + '% of fleet</div>'
                '</div>'

                '<div style="margin-top:10px;">'
                '<div style="font-size:0.72rem;color:' + C_TEXT3
                + ';text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">'
                'CO2 Reduction</div>'
                '<div style="font-size:0.85rem;font-weight:600;color:' + _GREEN + ';">'
                + str(fuel["co2_reduction_pct"]) + '% vs HFO</div>'
                '</div>'

                '<div style="margin-top:10px;">'
                '<div style="font-size:0.72rem;color:' + C_TEXT3
                + ';text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">'
                'On Order</div>'
                '<div style="font-size:0.85rem;font-weight:600;color:' + C_TEXT2 + ';">'
                + str(fuel["vessels_on_order"]) + " vessels" + '</div>'
                '</div>'

                '<div style="border-top:1px solid ' + _hex_to_rgba(color, 0.2)
                + ';margin-top:12px;padding-top:10px;font-size:0.73rem;color:'
                + C_TEXT3 + ';line-height:1.45;">' + fuel["note"] + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # Price comparison chart: alt fuels vs VLSFO
    alt_names  = ["VLSFO (baseline)"] + [f["name"] for f in _ALT_FUELS]
    alt_prices = [avg_vlsfo] + [f["price_per_mt"] for f in _ALT_FUELS]
    alt_colors = [_BLUE] + [f["color"] for f in _ALT_FUELS]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=alt_names,
        y=alt_prices,
        marker=dict(
            color=alt_colors,
            line=dict(color="rgba(255,255,255,0.1)", width=1),
        ),
        text=["$" + str(int(p)) + "/mt" for p in alt_prices],
        textposition="outside",
        textfont=dict(color=C_TEXT2, size=11),
        hovertemplate="<b>%{x}</b><br>$%{y:,.0f}/mt<extra></extra>",
        name="Price per MT",
    ))

    # Reference line at VLSFO
    fig.add_hline(
        y=avg_vlsfo,
        line=dict(color=_BLUE, width=1.5, dash="dot"),
        annotation_text="VLSFO baseline $" + str(int(avg_vlsfo)) + "/mt",
        annotation_position="right",
        annotation_font=dict(color=_BLUE, size=10),
    )

    apply_dark_layout(fig, title="Alternative Fuel Price Comparison vs VLSFO", height=320, showlegend=False)
    fig.update_layout(
        margin=dict(l=20, r=80, t=50, b=20),
        yaxis_title="Price ($/mt)",
        yaxis_tickprefix="$",
        bargap=0.35,
    )
    fig.update_xaxes(tickfont=dict(color=C_TEXT2, size=11))
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.04)")

    st.plotly_chart(fig, use_container_width=True, key="bunker_alt_fuels_chart")


# ── Section 10: Scrubber Economics ───────────────────────────────────────────

def _render_scrubber_economics(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Scrubber Economics",
        "Scrubber-equipped (HFO + EGCS) vs non-scrubber (VLSFO) vessel cost comparison and payback period",
    )

    avg_vlsfo = global_average_price("VLSFO", bunker_prices) or 615.0
    avg_hfo   = global_average_price("HFO",   bunker_prices) or 435.0
    spread    = avg_vlsfo - avg_hfo

    # Annual savings from scrubber
    annual_fuel_mt        = _VESSEL_VOYAGES_PER_YR * _VESSEL_FUEL_MT_VOYAGE
    annual_spread_saving  = spread * annual_fuel_mt
    net_annual_saving     = annual_spread_saving - _SCRUBBER_OPEX_USD_YR - _SCRUBBER_WASH_WATER_OPEX
    payback_yrs           = (_SCRUBBER_CAPEX_USD / net_annual_saving
                             if net_annual_saving > 0 else float("inf"))

    payback_color = (
        _GREEN if payback_yrs < 2.5 else
        (_AMBER if payback_yrs < 5.0 else _RED)
    )
    payback_str = (str(round(payback_yrs, 1)) + " yrs") if payback_yrs < 50 else "Not viable"
    verdict = (
        "ATTRACTIVE" if payback_yrs < 2.5 else
        ("BORDERLINE" if payback_yrs < 5.0 else "UNATTRACTIVE")
    )

    # ── Metric cards ──────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(
            _card_html(
                "VLSFO / HFO Spread",
                "$" + str(int(spread)) + "/mt",
                sub="Wider = better scrubber case",
                accent=_AMBER,
                footer="VLSFO $" + str(int(avg_vlsfo)) + " vs HFO $" + str(int(avg_hfo)),
            ),
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            _card_html(
                "Annual Spread Saving",
                "$" + "{:,.0f}".format(annual_spread_saving),
                sub=str(annual_fuel_mt) + " MT/yr at current spread",
                accent=_GREEN,
            ),
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            _card_html(
                "Net Annual Saving",
                "$" + "{:,.0f}".format(max(net_annual_saving, 0)),
                sub="After OPEX $" + "{:,.0f}".format(_SCRUBBER_OPEX_USD_YR + _SCRUBBER_WASH_WATER_OPEX),
                accent=_GREEN if net_annual_saving > 0 else _RED,
            ),
            unsafe_allow_html=True,
        )
    with m4:
        st.markdown(
            _card_html(
                "Payback Period",
                payback_str,
                sub="On $" + "{:,.0f}".format(_SCRUBBER_CAPEX_USD) + " capex",
                accent=payback_color,
                badge_text=verdict,
                badge_color=payback_color,
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Sensitivity chart: payback vs spread ─────────────────────────────────
    spreads_range = list(range(50, 301, 10))
    paybacks = []
    for sp in spreads_range:
        ann_saving  = sp * annual_fuel_mt - _SCRUBBER_OPEX_USD_YR - _SCRUBBER_WASH_WATER_OPEX
        pb = _SCRUBBER_CAPEX_USD / ann_saving if ann_saving > 0 else 10.0
        paybacks.append(min(pb, 10.0))

    fig = go.Figure()

    # Background zones
    fig.add_hrect(y0=0, y1=2.5, fillcolor=_hex_to_rgba(_GREEN, 0.08),
                  line_width=0, annotation_text="Attractive", annotation_position="right",
                  annotation_font=dict(color=_GREEN, size=10))
    fig.add_hrect(y0=2.5, y1=5.0, fillcolor=_hex_to_rgba(_AMBER, 0.06),
                  line_width=0, annotation_text="Borderline", annotation_position="right",
                  annotation_font=dict(color=_AMBER, size=10))
    fig.add_hrect(y0=5.0, y1=10.0, fillcolor=_hex_to_rgba(_RED, 0.05),
                  line_width=0, annotation_text="Unattractive", annotation_position="right",
                  annotation_font=dict(color=_RED, size=10))

    fig.add_trace(go.Scatter(
        x=spreads_range, y=paybacks,
        mode="lines+markers",
        name="Scrubber Payback",
        line=dict(color=_BLUE, width=2.5),
        marker=dict(size=5, color=_BLUE),
        hovertemplate="Spread: $%{x}/mt<br>Payback: %{y:.1f} yrs<extra></extra>",
    ))

    # Current spread marker
    if 50 <= int(spread) <= 300:
        pb_now = min(_SCRUBBER_CAPEX_USD / max(spread * annual_fuel_mt - _SCRUBBER_OPEX_USD_YR - _SCRUBBER_WASH_WATER_OPEX, 1), 10.0)
        fig.add_trace(go.Scatter(
            x=[int(spread)], y=[round(pb_now, 2)],
            mode="markers",
            name="Current spread",
            marker=dict(size=14, color=_AMBER, symbol="star",
                        line=dict(color="white", width=1.5)),
            hovertemplate="Current<br>Spread: $" + str(int(spread)) + "/mt<br>Payback: " + str(round(pb_now, 1)) + " yrs<extra></extra>",
        ))

    apply_dark_layout(fig, title="Scrubber Payback Period vs Fuel Spread Sensitivity", height=360, showlegend=True)
    fig.update_layout(
        xaxis_title="VLSFO / HFO Spread ($/mt)",
        yaxis_title="Payback Period (years)",
        margin=dict(l=50, r=100, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0, font=dict(size=11)),
    )
    fig.update_yaxes(range=[0, 10.5], gridcolor="rgba(255,255,255,0.04)")
    fig.update_xaxes(tickprefix="$", gridcolor="rgba(255,255,255,0.04)")

    st.plotly_chart(fig, use_container_width=True, key="bunker_scrubber_sensitivity")

    st.markdown(
        '<div style="background:' + _hex_to_rgba(_AMBER, 0.06) + '; border:1px solid '
        + _hex_to_rgba(_AMBER, 0.2) + '; border-radius:10px; padding:14px 18px; '
        + 'font-size:0.82rem; color:' + C_TEXT2 + '; line-height:1.55;">'
        + "<b style='color:" + C_TEXT + ";'>Scrubber economics in 2026:</b> "
        + "The current VLSFO/HFO spread of <b style='color:" + _AMBER + ";'>$" + str(int(spread)) + "/mt</b> "
        + "implies a payback of <b style='color:" + payback_color + ";'>" + payback_str + "</b> "
        + "on a $3.5M EGCS retrofit. Spreads above $150/mt are typically required for sub-3-year payback. "
        + "Environmental port restrictions on open-loop scrubbers in certain jurisdictions (California, China, Singapore anchorages) "
        + "add operational complexity and reduce annual savings."
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 11: Global Bunker Price Map ───────────────────────────────────────

def _render_price_map(bunker_prices: dict[tuple[str, str], BunkerPrice]) -> None:
    section_header(
        "Global Bunker Price Map",
        "VLSFO spot price at major bunkering hubs — marker size proportional to price premium vs global average",
    )

    lats, lons, names, vlsfo_prices, texts = [], [], [], [], []

    for locode, meta in HUB_META.items():
        if "lat" not in meta or "lon" not in meta:
            continue
        bp_vlsfo = bunker_prices.get((locode, "VLSFO"))
        if bp_vlsfo is None:
            continue
        price = bp_vlsfo.price_per_mt
        if not price or price <= 0:
            continue
        lats.append(meta["lat"])
        lons.append(meta["lon"])
        names.append(meta.get("name", locode))
        vlsfo_prices.append(price)

        parts = ["<b>" + meta.get("name", locode) + "</b> (" + locode + ")<br>"]
        for ft in ("VLSFO", "HFO", "MDO", "LNG"):
            bp = bunker_prices.get((locode, ft))
            if bp:
                chg = ("+" if bp.change_7d_pct >= 0 else "") + str(bp.change_7d_pct) + "% 7d"
                disp = "IFO 380" if ft == "HFO" else ft
                parts.append(disp + ": $" + str(int(bp.price_per_mt)) + "/mt  " + chg + "<br>")
        texts.append("".join(parts) + "<extra></extra>")

    if not lats:
        st.info("Bunker price map unavailable — no hub price data with valid coordinates.")
        return

    min_p   = min(vlsfo_prices)
    max_p   = max(vlsfo_prices)
    spread  = max(max_p - min_p, 1.0)
    marker_sizes = [16 + 26 * ((p - min_p) / spread) for p in vlsfo_prices]

    fig = go.Figure()
    fig.add_trace(go.Scattergeo(
        lat=lats, lon=lons,
        text=names,
        hovertemplate=texts,
        mode="markers+text",
        textposition="top center",
        textfont=dict(color=C_TEXT2, size=10),
        marker=dict(
            size=marker_sizes,
            color=vlsfo_prices,
            colorscale=[[0.0, _GREEN], [0.5, _AMBER], [1.0, _RED]],
            showscale=True,
            colorbar=dict(
                title="VLSFO $/mt",
                thickness=12,
                len=0.6,
                bgcolor="rgba(10,15,26,0.8)",
                tickfont=dict(color=C_TEXT2, size=10),
                titlefont=dict(color=C_TEXT2, size=11),
            ),
            line=dict(color="rgba(255,255,255,0.3)", width=1),
            opacity=0.9,
        ),
    ))

    fig.update_layout(
        template="plotly_dark",
        height=460,
        paper_bgcolor="#0a0f1a",
        margin=dict(l=20, r=20, t=30, b=20),
        geo=dict(
            projection_type="natural earth",
            bgcolor="#0a0f1a",
            showland=True,
            landcolor="#111827",
            showocean=True,
            oceancolor="#080e1a",
            showcountries=True,
            countrycolor="rgba(255,255,255,0.06)",
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.08)",
            showframe=False,
            showlakes=False,
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="bunker_price_map")


# ── Section 12: IMO Compliance Panel ─────────────────────────────────────────

def _render_imo_compliance() -> None:
    section_header(
        "IMO Regulatory Compliance Panel",
        "2020 → 2050 regulatory timeline with fleet compliance estimates and transition outlook",
    )

    cols = st.columns(4)
    for col, milestone in zip(cols, _IMO_MILESTONES):
        pct    = milestone["compliance_pct"]
        color  = milestone["color"]
        status = milestone["status"]
        bg     = _hex_to_rgba(color, 0.07)
        border = _hex_to_rgba(color, 0.22)

        bar_color = _GREEN if pct >= 80 else (_AMBER if pct >= 40 else _RED)
        bar_html  = (
            '<div style="background:rgba(255,255,255,0.07);border-radius:4px;'
            'height:6px;width:100%;margin-top:8px;">'
            '<div style="width:' + str(pct) + '%;height:6px;border-radius:4px;'
            'background:' + bar_color + ';box-shadow:0 0 6px ' + bar_color + ';"></div></div>'
        )

        with col:
            st.markdown(
                '<div style="background:' + bg + '; border:1px solid ' + border + '; '
                'border-radius:12px; padding:20px 16px; height:100%; '
                'box-shadow:0 4px 16px rgba(0,0,0,0.2);">'

                + _badge(status, color) +

                '<div style="font-size:1.1rem;font-weight:800;color:' + color
                + ';margin:10px 0 4px;">' + milestone["year"] + '</div>'
                '<div style="font-size:0.82rem;font-weight:600;color:' + C_TEXT
                + ';margin-bottom:8px;line-height:1.35;">' + milestone["label"] + '</div>'

                + bar_html +

                '<div style="font-size:0.75rem;color:' + C_TEXT3
                + ';margin-top:6px;">' + str(pct) + '% fleet compliant</div>'
                '<div style="font-size:0.77rem;color:' + C_TEXT2
                + ';margin-top:10px;line-height:1.45;">' + milestone["detail"] + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown(
        '<div style="background:' + _hex_to_rgba(_AMBER, 0.06) + '; border:1px solid '
        + _hex_to_rgba(_AMBER, 0.2) + '; border-radius:10px; padding:14px 18px; '
        + 'font-size:0.83rem; color:' + C_TEXT2 + '; line-height:1.55;">'
        + "<b style='color:" + C_TEXT + ";'>Fleet transition outlook:</b> "
        + "Reaching IMO 2050 net-zero requires ~$1–1.5 trillion in new vessel orders and "
        + "fuel infrastructure. Today only ~5% of the global container fleet is LNG-capable. "
        + "Methanol and ammonia dual-fuel vessels are the next frontier, with 130+ on order "
        + "as of Q1 2026. The $150–250/mt price premium for green fuels vs VLSFO represents "
        + "the core commercial hurdle for accelerated transition."
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Main render entry point ───────────────────────────────────────────────────

def render(
    freight_data: dict | None = None,
    macro_data: dict | None = None,
    route_results: list | None = None,
) -> None:
    """Render the full Bunker Fuel Intelligence tab.

    Parameters
    ----------
    freight_data:
        Dict of route_id -> DataFrame with freight rate history.
    macro_data:
        Dict of FRED series_id -> DataFrame (must include "WTISPLC" for WTI).
    route_results:
        Pre-computed RouteEmissions list (unused here but accepted for
        consistency with other tab signatures).
    """
    if freight_data is None:
        freight_data = {}
    if macro_data is None:
        macro_data = {}

    logger.info("Rendering bunker tab")

    bunker_prices: dict[tuple[str, str], BunkerPrice] = BUNKER_HUB_PRICES

    # ── Market Intelligence Overview (new top-level summary panel) ────────────
    try:
        _render_market_overview(bunker_prices)
    except Exception as exc:
        logger.warning("Market overview render failed: %s", exc)

    # ── Hero banner ───────────────────────────────────────────────────────────
    try:
        _render_hero(bunker_prices)
    except Exception as exc:
        logger.warning("Bunker hero render failed: %s", exc)
        st.info("Bunker fuel dashboard loading...")

    _divider()

    # ── Fuel cost dashboard (metric cards) ────────────────────────────────────
    try:
        _render_fuel_dashboard(bunker_prices)
    except Exception as exc:
        logger.warning("Fuel dashboard render failed: %s", exc)
        st.warning("Fuel dashboard unavailable.")

    _divider()

    # ── 90-day price time series ──────────────────────────────────────────────
    try:
        _render_price_timeseries(bunker_prices)
    except Exception as exc:
        logger.warning("Price timeseries render failed: %s", exc)
        st.warning("Price time series unavailable.")

    _divider()

    # ── Port-specific bunker prices table ─────────────────────────────────────
    try:
        _render_port_prices_table(bunker_prices)
    except Exception as exc:
        logger.warning("Port prices table render failed: %s", exc)
        st.warning("Port prices table unavailable.")

    _divider()

    # ── Route cost calculator ─────────────────────────────────────────────────
    try:
        _render_cost_calculator(bunker_prices)
    except Exception as exc:
        logger.warning("Cost calculator render failed: %s", exc)
        st.warning("Route cost calculator unavailable.")

    _divider()

    # ── Fuel hedge indicator ──────────────────────────────────────────────────
    try:
        _render_hedge_indicator(bunker_prices)
    except Exception as exc:
        logger.warning("Hedge indicator render failed: %s", exc)
        st.warning("Hedge indicator unavailable.")

    _divider()

    # ── Bunker cost impact on freight rates ───────────────────────────────────
    try:
        _render_correlation(macro_data, freight_data)
    except Exception as exc:
        logger.warning("Correlation render failed: %s", exc)
        st.warning("Freight rate correlation unavailable.")

    _divider()

    # ── Oil price vs shipping stock correlation ───────────────────────────────
    try:
        _render_oil_stock_correlation()
    except Exception as exc:
        logger.warning("Oil-stock correlation render failed: %s", exc)
        st.warning("Oil vs shipping stock chart unavailable.")

    _divider()

    # ── Alternative fuels tracker ─────────────────────────────────────────────
    try:
        _render_alt_fuels_tracker(bunker_prices)
    except Exception as exc:
        logger.warning("Alt fuels tracker render failed: %s", exc)
        st.warning("Alternative fuels tracker unavailable.")

    _divider()

    # ── Scrubber economics ────────────────────────────────────────────────────
    try:
        _render_scrubber_economics(bunker_prices)
    except Exception as exc:
        logger.warning("Scrubber economics render failed: %s", exc)
        st.warning("Scrubber economics panel unavailable.")

    _divider()

    # ── Global bunker price map ───────────────────────────────────────────────
    try:
        _render_price_map(bunker_prices)
    except Exception as exc:
        logger.warning("Price map render failed: %s", exc)
        st.warning("Price map unavailable.")

    _divider()

    # ── IMO compliance panel ──────────────────────────────────────────────────
    try:
        _render_imo_compliance()
    except Exception as exc:
        logger.warning("IMO compliance render failed: %s", exc)
        st.warning("IMO compliance panel unavailable.")

    # ── CSV export ────────────────────────────────────────────────────────────
    try:
        export_rows = []
        for (locode, ft), bp in bunker_prices.items():
            hub_name = HUB_META.get(locode, {}).get("name", locode)
            export_rows.append({
                "Port (LOCODE)": locode,
                "Port Name": hub_name,
                "Fuel Type": "IFO 380" if ft == "HFO" else ft,
                "Price ($/mt)": bp.price_per_mt,
                "7d Change (%)": bp.change_7d_pct,
                "30d Change (%)": bp.change_30d_pct,
                "vs Global Avg (%)": bp.vs_global_avg_pct,
                "Source": bp.source,
            })
        if export_rows:
            bunker_csv = pd.DataFrame(export_rows).to_csv(index=False)
            st.markdown("<br>", unsafe_allow_html=True)
            st.download_button(
                label="Download full bunker prices CSV",
                data=bunker_csv,
                file_name="bunker_prices_full.csv",
                mime="text/csv",
                key="download_bunker_prices_csv",
            )
    except Exception as exc:
        logger.warning("Bunker CSV export failed: %s", exc)

    logger.info("Bunker tab render complete")
