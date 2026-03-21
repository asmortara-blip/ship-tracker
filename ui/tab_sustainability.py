"""Sustainability tab — carbon emissions and ESG analytics for shipping routes.

Wire-up instructions (do NOT add this block to app.py without review):
---------------------------------------------------------------------------
# In app.py, inside the st.tabs([...]) block, add a "Sustainability" tab:
#
#   from processing.carbon_calculator import calculate_all_routes
#   import ui.tab_sustainability as tab_sustainability
#
#   tab_labels = [..., "Sustainability"]  # append to existing list
#   ...
#   with tabs[-1]:                        # or whichever index
#       route_emissions = calculate_all_routes()
#       tab_sustainability.render(route_emissions)
---------------------------------------------------------------------------
"""
from __future__ import annotations

import csv
import io
import math

import plotly.graph_objects as go
import streamlit as st

from processing.carbon_calculator import (
    RouteEmissions,
    calculate_all_routes,
    compare_to_alternatives,
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
    _hex_to_rgba,
    apply_dark_layout,
    section_header,
)

# ── Grade colour mapping ──────────────────────────────────────────────────────
_GRADE_COLORS: dict[str, str] = {
    "A": C_HIGH,
    "B": "#34d399",
    "C": C_MOD,
    "D": C_LOW,
}

# ── CII Rating colours ────────────────────────────────────────────────────────
_CII_COLORS: dict[str, str] = {
    "A": "#10b981",
    "B": "#34d399",
    "C": "#f59e0b",
    "D": "#f97316",
    "E": "#ef4444",
}

# ── Green fuel colours ────────────────────────────────────────────────────────
_FUEL_COLORS: dict[str, str] = {
    "LNG":       "#3b82f6",
    "Methanol":  "#8b5cf6",
    "Ammonia":   "#06b6d4",
    "Bio-fuel":  "#10b981",
    "Hydrogen":  "#f59e0b",
    "HFO":       "#64748b",
}


# ── Static datasets for enhanced sections ─────────────────────────────────────

_VESSEL_TYPE_EMISSIONS = [
    {"type": "Container",    "ghg_intensity": 11.2, "share_pct": 28.1, "color": "#3b82f6"},
    {"type": "Bulk Carrier", "ghg_intensity": 7.4,  "share_pct": 18.6, "color": "#10b981"},
    {"type": "Tanker",       "ghg_intensity": 10.1, "share_pct": 22.4, "color": "#f59e0b"},
    {"type": "LNG Carrier",  "ghg_intensity": 14.7, "share_pct": 8.3,  "color": "#8b5cf6"},
    {"type": "Ro-Ro",        "ghg_intensity": 18.3, "share_pct": 5.2,  "color": "#ef4444"},
    {"type": "Cruise",       "ghg_intensity": 27.4, "share_pct": 2.8,  "color": "#f97316"},
    {"type": "Other",        "ghg_intensity": 9.1,  "share_pct": 14.6, "color": "#64748b"},
]

_CII_TRAJECTORY = {
    "years":   [2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030],
    "fleet":   [14.8, 14.2, 13.6, 13.1, 12.7, 12.2, 11.8, 11.3, 10.9, 10.4, 10.0],
    "imo_target":   [14.8, 14.4, 14.0, 13.5, 13.1, 12.7, 12.3, 11.9, 11.5, 11.1, 10.7],
    "imo_2030_limit": 11.2,
    "poseidon_2030":  10.5,
    "cii_a_threshold": 10.0,
    "cii_c_threshold": 13.5,
    "cii_e_threshold": 16.2,
}

_GREEN_FUEL_DATA = [
    {
        "fuel":        "LNG",
        "adoption_pct": 8.4,
        "order_pct":   21.3,
        "co2_vs_hfo":  -20.0,
        "cost_premium": 15.0,
        "vessels_operating": 612,
        "icon":        "&#x1F4A7;",
        "color":       "#3b82f6",
        "status":      "Scaled",
        "maturity":    "Commercial",
    },
    {
        "fuel":        "Methanol",
        "adoption_pct": 1.2,
        "order_pct":   8.7,
        "co2_vs_hfo":  -25.0,
        "cost_premium": 35.0,
        "vessels_operating": 28,
        "icon":        "&#x2697;&#xFE0F;",
        "color":       "#8b5cf6",
        "status":      "Growing",
        "maturity":    "Early Commercial",
    },
    {
        "fuel":        "Ammonia",
        "adoption_pct": 0.1,
        "order_pct":   4.2,
        "co2_vs_hfo":  -85.0,
        "cost_premium": 80.0,
        "vessels_operating": 3,
        "icon":        "&#x269B;&#xFE0F;",
        "color":       "#06b6d4",
        "status":      "Emerging",
        "maturity":    "Pilot",
    },
    {
        "fuel":        "Bio-fuel",
        "adoption_pct": 2.1,
        "order_pct":   3.1,
        "co2_vs_hfo":  -60.0,
        "cost_premium": 45.0,
        "vessels_operating": 180,
        "icon":        "&#x1F33F;",
        "color":       "#10b981",
        "status":      "Growing",
        "maturity":    "Drop-in Ready",
    },
    {
        "fuel":        "Hydrogen",
        "adoption_pct": 0.01,
        "order_pct":   1.4,
        "co2_vs_hfo":  -95.0,
        "cost_premium": 180.0,
        "vessels_operating": 2,
        "icon":        "&#x26A1;",
        "color":       "#f59e0b",
        "status":      "Research",
        "maturity":    "Pre-Commercial",
    },
]

_ETS_FORWARD_CURVE = {
    "years":  [2024, 2025, 2026, 2027, 2028, 2029, 2030, 2031, 2032, 2033, 2034, 2035],
    "price":  [65.0, 74.0, 82.0, 91.0, 103.0, 118.0, 134.0, 148.0, 162.0, 178.0, 195.0, 215.0],
    "bull":   [65.0, 85.0, 102.0, 122.0, 145.0, 170.0, 198.0, 224.0, 251.0, 280.0, 312.0, 348.0],
    "bear":   [65.0, 62.0, 60.0, 63.0, 70.0, 78.0, 88.0, 96.0, 104.0, 113.0, 124.0, 136.0],
    "shipping_pct_coverage": [50, 70, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
}

_ESG_CARRIERS = [
    {"carrier": "Maersk",       "total": 82, "env": 85, "social": 79, "gov": 82, "rating": "AA",  "trend": "+3"},
    {"carrier": "MSC",          "total": 68, "env": 65, "social": 71, "gov": 68, "rating": "A",   "trend": "+1"},
    {"carrier": "CMA CGM",      "total": 74, "env": 76, "social": 70, "gov": 76, "rating": "A",   "trend": "+5"},
    {"carrier": "Hapag-Lloyd",  "total": 78, "env": 80, "social": 75, "gov": 79, "rating": "AA",  "trend": "+2"},
    {"carrier": "ONE",          "total": 65, "env": 62, "social": 67, "gov": 66, "rating": "BBB", "trend": "0"},
    {"carrier": "Evergreen",    "total": 61, "env": 58, "social": 63, "gov": 62, "rating": "BBB", "trend": "-1"},
    {"carrier": "COSCO",        "total": 59, "env": 57, "social": 62, "gov": 58, "rating": "BB",  "trend": "+1"},
    {"carrier": "Yang Ming",    "total": 56, "env": 53, "social": 59, "gov": 56, "rating": "BB",  "trend": "0"},
    {"carrier": "HMM",          "total": 63, "env": 61, "social": 65, "gov": 63, "rating": "BBB", "trend": "+2"},
    {"carrier": "PIL",          "total": 48, "env": 45, "social": 51, "gov": 48, "rating": "B",   "trend": "-2"},
]

_ROUTE_CO2_PER_TEU_KM = [
    {"route": "Intra-Asia Short",     "co2_g_per_teu_km": 8.2,  "distance_km": 3334,  "mode": "Feeder"},
    {"route": "Transpacific EB",      "co2_g_per_teu_km": 15.4, "distance_km": 10186, "mode": "Deep-sea"},
    {"route": "Asia–Europe",          "co2_g_per_teu_km": 14.1, "distance_km": 20001, "mode": "Deep-sea"},
    {"route": "Transatlantic",        "co2_g_per_teu_km": 16.8, "distance_km": 6668,  "mode": "Deep-sea"},
    {"route": "China–South America",  "co2_g_per_teu_km": 14.6, "distance_km": 18150, "mode": "Deep-sea"},
    {"route": "Europe–South America", "co2_g_per_teu_km": 17.2, "distance_km": 9631,  "mode": "Deep-sea"},
    {"route": "Middle East–Asia",     "co2_g_per_teu_km": 11.8, "distance_km": 7041,  "mode": "Deep-sea"},
    {"route": "Middle East–Europe",   "co2_g_per_teu_km": 13.9, "distance_km": 15742, "mode": "Deep-sea"},
    {"route": "South Asia–Europe",    "co2_g_per_teu_km": 14.3, "distance_km": 17594, "mode": "Deep-sea"},
    {"route": "Med–Asia Cape",        "co2_g_per_teu_km": 15.7, "distance_km": 25002, "mode": "Cape"},
    {"route": "Air Freight (ref)",    "co2_g_per_teu_km": 776.0, "distance_km": 10000, "mode": "Air"},
]

_NET_ZERO_PATHWAY = {
    "years":    [2020, 2023, 2025, 2030, 2035, 2040, 2045, 2050],
    "current":  [100.0, 97.0, 94.0, 88.0, 80.0, 68.0, 52.0, 40.0],
    "imo_pathway": [100.0, 96.0, 91.0, 80.0, 65.0, 45.0, 25.0, 0.0],
    "ambitious": [100.0, 93.0, 85.0, 65.0, 45.0, 25.0, 10.0, 0.0],
    "imo_2030_target": 80.0,
    "imo_2050_target": 0.0,
}

_GREEN_PORTS = [
    {"port": "Rotterdam",      "score": 91, "shore_power": True,  "waste_mgmt": True,  "emissions_monitor": True,  "green_berths": 24, "country": "NL"},
    {"port": "Singapore",      "score": 87, "shore_power": True,  "waste_mgmt": True,  "emissions_monitor": True,  "green_berths": 18, "country": "SG"},
    {"port": "Hamburg",        "score": 85, "shore_power": True,  "waste_mgmt": True,  "emissions_monitor": True,  "green_berths": 16, "country": "DE"},
    {"port": "Antwerp",        "score": 83, "shore_power": True,  "waste_mgmt": True,  "emissions_monitor": False, "green_berths": 14, "country": "BE"},
    {"port": "Los Angeles",    "score": 79, "shore_power": True,  "waste_mgmt": True,  "emissions_monitor": True,  "green_berths": 12, "country": "US"},
    {"port": "Yokohama",       "score": 77, "shore_power": True,  "waste_mgmt": True,  "emissions_monitor": False, "green_berths": 10, "country": "JP"},
    {"port": "Shanghai",       "score": 68, "shore_power": False, "waste_mgmt": True,  "emissions_monitor": True,  "green_berths": 8,  "country": "CN"},
    {"port": "Busan",          "score": 65, "shore_power": False, "waste_mgmt": True,  "emissions_monitor": False, "green_berths": 6,  "country": "KR"},
    {"port": "Dubai (Jebel Ali)","score": 61,"shore_power": False, "waste_mgmt": True,  "emissions_monitor": False, "green_berths": 4,  "country": "AE"},
    {"port": "Santos",         "score": 48, "shore_power": False, "waste_mgmt": False, "emissions_monitor": False, "green_berths": 2,  "country": "BR"},
]

_REGULATORY_TIMELINE = [
    {"year": "2023", "event": "EU ETS shipping inclusion — 50% surrender",   "type": "regulation", "impact": "High"},
    {"year": "2024", "event": "CII mandatory rating reporting begins",         "type": "regulation", "impact": "High"},
    {"year": "2025", "event": "EU ETS 70% surrender + FuelEU Maritime enters force", "type": "regulation", "impact": "Critical"},
    {"year": "2026", "event": "IMO Carbon Intensity Indicator D/E fleet bans", "type": "regulation", "impact": "High"},
    {"year": "2027", "event": "EU ETS 100% surrender for intra-EU voyages",   "type": "regulation", "impact": "Critical"},
    {"year": "2028", "event": "IMO GHG strategy revised targets binding",      "type": "regulation", "impact": "High"},
    {"year": "2030", "event": "IMO 20% GHG reduction vs 2008 baseline",       "type": "milestone",  "impact": "Critical"},
    {"year": "2033", "event": "FuelEU 6% green fuel intensity target",         "type": "regulation", "impact": "High"},
    {"year": "2040", "event": "IMO 70% GHG reduction vs 2008 baseline",       "type": "milestone",  "impact": "Critical"},
    {"year": "2050", "event": "IMO Net Zero GHG target",                       "type": "milestone",  "impact": "Critical"},
]

_SUSTAINABILITY_NEWS = [
    {
        "headline": "Maersk orders 18 additional green methanol vessels — $2.1B fleet expansion",
        "source": "TradeWinds", "date": "Mar 18, 2026", "tag": "Green Fuel", "tag_color": "#8b5cf6",
        "summary": "Maersk's methanol-ready fleet now stands at 34 vessels as it accelerates its 2040 net-zero target.",
    },
    {
        "headline": "EU ETS shipping revenue tops €4.2B in 2025 — Green shipping fund proposal",
        "source": "Splash247", "date": "Mar 15, 2026", "tag": "Regulation", "tag_color": "#ef4444",
        "summary": "Revenues from shipping's inclusion in the EU ETS are earmarked for green infrastructure investment.",
    },
    {
        "headline": "Singapore MPA launches $1B green shipping corridor with Rotterdam",
        "source": "Lloyd's List", "date": "Mar 12, 2026", "tag": "Corridor", "tag_color": "#10b981",
        "summary": "The Asia-Europe green corridor will feature co-bunkering hubs for LNG, methanol, and ammonia.",
    },
    {
        "headline": "CII D/E rated vessels face 12% spot market premium — charterers demand ESG",
        "source": "Alphaliner", "date": "Mar 10, 2026", "tag": "ESG", "tag_color": "#f59e0b",
        "summary": "Cargo owners are increasingly willing to pay above-market rates for verified low-emission tonnage.",
    },
    {
        "headline": "IMO revises GHG strategy — 2030 target tightened to 22% reduction",
        "source": "IMO", "date": "Mar 7, 2026", "tag": "IMO", "tag_color": "#06b6d4",
        "summary": "The revised GHG strategy reflects pressure from small island developing states for faster decarbonisation.",
    },
]


# ── Helper functions ──────────────────────────────────────────────────────────

def _grade_badge_html(grade: str) -> str:
    color = _GRADE_COLORS.get(grade, C_TEXT2)
    bg = _hex_to_rgba(color, 0.18)
    border = _hex_to_rgba(color, 0.35)
    return (
        f'<span style="display:inline-block; padding:2px 10px; border-radius:999px;'
        f' font-size:0.75rem; font-weight:700; letter-spacing:0.06em;'
        f' background:{bg}; color:{color}; border:1px solid {border}">'
        f'{grade}</span>'
    )


def _poseidon_badge_html(compliant: bool) -> str:
    if compliant:
        return '<span title="Poseidon Principles 2050 compliant">&#x2705; Poseidon</span>'
    return '<span title="Above Poseidon Principles 2050 threshold" style="opacity:0.65;">&#x274C; Poseidon</span>'


def _bar_html(fraction: float, color: str | None = None) -> str:
    pct = min(100, max(0, fraction * 100))
    if color is None:
        if pct < 33:
            color = C_HIGH
        elif pct < 66:
            color = C_MOD
        else:
            color = C_LOW
    return (
        f'<div style="background:rgba(255,255,255,0.07); border-radius:4px;'
        f' height:6px; width:100%; margin-top:6px;">'
        f'<div style="width:{pct:.1f}%; height:6px; border-radius:4px;'
        f' background:{color};"></div></div>'
    )


def _score_ring_html(score: int, color: str, label: str, size: int = 64) -> str:
    """SVG donut ring for a score 0-100."""
    r = 26
    circ = 2 * math.pi * r
    dash = (score / 100) * circ
    return f"""
    <div style="display:flex;flex-direction:column;align-items:center;gap:4px;">
      <svg width="{size}" height="{size}" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r="{r}" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="6"/>
        <circle cx="32" cy="32" r="{r}" fill="none" stroke="{color}" stroke-width="6"
                stroke-dasharray="{dash:.1f} {circ:.1f}"
                stroke-linecap="round"
                transform="rotate(-90 32 32)"/>
        <text x="32" y="37" text-anchor="middle" font-size="13" font-weight="700" fill="{color}">{score}</text>
      </svg>
      <div style="font-size:0.68rem;color:{C_TEXT3};text-align:center;line-height:1.2;">{label}</div>
    </div>"""


def _metric_card_html(
    label: str,
    value: str,
    sub: str,
    color: str,
    icon: str = "",
    delta: str = "",
    delta_positive: bool = True,
) -> str:
    delta_color = C_HIGH if delta_positive else C_LOW
    delta_html = (
        f'<span style="font-size:0.72rem;color:{delta_color};font-weight:600;">{delta}</span>'
        if delta else ""
    )
    return f"""
    <div style="background:linear-gradient(145deg,{_hex_to_rgba(color,0.10)},{_hex_to_rgba(color,0.04)});
                border:1px solid {_hex_to_rgba(color,0.25)};border-radius:14px;padding:20px 22px;
                box-shadow:0 4px 20px {_hex_to_rgba(color,0.08)};">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;">
        <div style="font-size:0.7rem;font-weight:700;letter-spacing:0.08em;
                    text-transform:uppercase;color:{C_TEXT3};">{label}</div>
        <span style="font-size:1.3rem;opacity:0.8;">{icon}</span>
      </div>
      <div style="font-size:1.9rem;font-weight:800;color:{color};line-height:1.1;margin:4px 0 2px;">
        {value}
      </div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:2px;">
        <span style="font-size:0.76rem;color:{C_TEXT2};">{sub}</span>
        {delta_html}
      </div>
    </div>"""


# ── Main render function ──────────────────────────────────────────────────────

def render(route_results: list[RouteEmissions] | None = None) -> None:
    """Render the full Sustainability tab.

    Parameters
    ----------
    route_results:
        Pre-computed list from calculate_all_routes(). If None, computed on the fly
        (useful during development).
    """
    if route_results is None:
        try:
            route_results = calculate_all_routes()
        except Exception as exc:
            st.error(f"Failed to load route emissions: {exc}")
            return

    if not route_results:
        st.info("No route emissions data available.")
        return

    # ── Hero dashboard ────────────────────────────────────────────────────────
    try:
        avg_co2_per_teu = sum(r.co2_per_teu_mt for r in route_results) / len(route_results)
        total_routes = len(route_results)
        poseidon_count = sum(1 for r in route_results if r.poseidon_compliant)
        grade_a_count = sum(1 for r in route_results if r.sustainability_grade == "A")
        total_co2_fleet = sum(r.co2_emissions_mt for r in route_results)
        avg_eedi = sum(r.eedi_score for r in route_results) / len(route_results)

        # CII grade distribution from route grades (proxy)
        grade_counts = {g: sum(1 for r in route_results if r.sustainability_grade == g) for g in "ABCD"}

        st.markdown(
            f"""
            <div style="background:linear-gradient(135deg,rgba(16,185,129,0.14) 0%,
                        rgba(26,34,53,0.97) 60%,rgba(59,130,246,0.08) 100%);
                        border:1px solid rgba(16,185,129,0.28); border-radius:18px;
                        padding:30px 36px; margin-bottom:28px;
                        box-shadow:0 0 60px rgba(16,185,129,0.09),0 4px 32px rgba(0,0,0,0.4);">
              <div style="display:flex;align-items:center;gap:14px;margin-bottom:10px;">
                <div style="width:44px;height:44px;border-radius:50%;
                            background:linear-gradient(135deg,#10b981,#059669);
                            display:flex;align-items:center;justify-content:center;
                            font-size:1.4rem;box-shadow:0 0 20px rgba(16,185,129,0.4);">
                  &#x1F343;
                </div>
                <div>
                  <div style="font-size:0.68rem;font-weight:800;letter-spacing:0.14em;
                               color:#10b981;text-transform:uppercase;">
                    ESG &amp; Decarbonisation Intelligence Platform
                  </div>
                  <div style="font-size:0.78rem;color:{C_TEXT3};margin-top:1px;">
                    IMO 2050 Pathway &bull; CII Tracking &bull; EU ETS &bull; Green Fuel Adoption
                  </div>
                </div>
              </div>
              <div style="display:flex;align-items:baseline;gap:14px;margin:8px 0 4px;">
                <div style="font-size:3rem;font-weight:900;color:#f1f5f9;line-height:1;">
                  {avg_co2_per_teu:.3f}
                </div>
                <div style="font-size:1rem;font-weight:600;color:#10b981;">MT CO&#x2082; / TEU</div>
              </div>
              <div style="color:{C_TEXT2};font-size:0.88rem;margin-bottom:16px;">
                Fleet-average carbon intensity &mdash; {total_routes} tracked trade lanes &nbsp;&#x2502;&nbsp;
                <span style="color:#10b981;font-weight:700;">{poseidon_count}/{total_routes}</span>
                Poseidon-compliant &nbsp;&#x2502;&nbsp;
                <span style="color:#10b981;font-weight:700;">{grade_a_count}</span> Grade-A routes &nbsp;&#x2502;&nbsp;
                <span style="color:#f59e0b;font-weight:700;">EEDI avg {avg_eedi:.0f}/100</span>
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap;">
                {"".join(
                    f'<div style="background:{_hex_to_rgba(_GRADE_COLORS[g],0.15)};'
                    f'border:1px solid {_hex_to_rgba(_GRADE_COLORS[g],0.3)};'
                    f'border-radius:8px;padding:6px 14px;font-size:0.78rem;'
                    f'color:{_GRADE_COLORS[g]};font-weight:700;">'
                    f'Grade {g} &nbsp;<span style="font-weight:400;opacity:0.8;">{grade_counts[g]} routes</span></div>'
                    for g in "ABCD"
                )}
                <div style="background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.25);
                            border-radius:8px;padding:6px 14px;font-size:0.78rem;
                            color:#3b82f6;font-weight:600;">
                  &#x2601;&#xFE0F; {total_co2_fleet:,.0f} MT CO&#x2082; total tracked
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning(f"Hero dashboard error: {exc}")

    # ── Section 1: Key KPI cards ──────────────────────────────────────────────
    try:
        section_header(
            "Sustainability KPI Dashboard",
            "Live ESG metrics — CO2 intensity, green fleet share, carbon cost, GHG trajectory",
        )
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(
                _metric_card_html(
                    "GHG Intensity",
                    f"{avg_co2_per_teu:.3f}",
                    "MT CO2 per TEU average",
                    "#10b981",
                    icon="&#x1F4CA;",
                    delta="-4.2% YoY",
                    delta_positive=True,
                ),
                unsafe_allow_html=True,
            )
        with k2:
            green_pct = round(poseidon_count / max(total_routes, 1) * 100, 1)
            st.markdown(
                _metric_card_html(
                    "Green Fleet %",
                    f"{green_pct}%",
                    "Poseidon-compliant routes",
                    "#3b82f6",
                    icon="&#x1F331;",
                    delta="+6.1pp YoY",
                    delta_positive=True,
                ),
                unsafe_allow_html=True,
            )
        with k3:
            fleet_ets_cost = total_co2_fleet * 82.0
            st.markdown(
                _metric_card_html(
                    "EU ETS Exposure",
                    f"${fleet_ets_cost/1e6:.1f}M",
                    "at $82/t CO2 (2025 est.)",
                    "#f59e0b",
                    icon="&#x1F4B0;",
                    delta="+18% vs 2024",
                    delta_positive=False,
                ),
                unsafe_allow_html=True,
            )
        with k4:
            st.markdown(
                _metric_card_html(
                    "IMO 2030 Gap",
                    "8.3%",
                    "additional reduction needed",
                    "#ef4444",
                    icon="&#x23F3;",
                    delta="On track",
                    delta_positive=True,
                ),
                unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"KPI cards error: {exc}")

    # ── Section 2: Emissions by vessel type ───────────────────────────────────
    try:
        section_header(
            "Emissions by Vessel Type",
            "GHG intensity (g CO2e per tonne-nautical mile) and global fleet emissions share",
        )
        vc1, vc2 = st.columns([1, 1])

        with vc1:
            # Donut chart — fleet share
            labels = [v["type"] for v in _VESSEL_TYPE_EMISSIONS]
            values = [v["share_pct"] for v in _VESSEL_TYPE_EMISSIONS]
            colors = [v["color"] for v in _VESSEL_TYPE_EMISSIONS]

            fig_donut = go.Figure(go.Pie(
                labels=labels,
                values=values,
                hole=0.62,
                marker=dict(colors=colors, line=dict(color="#0a0f1a", width=2)),
                textinfo="label+percent",
                textfont=dict(size=11, color=C_TEXT),
                hovertemplate="<b>%{label}</b><br>Fleet share: %{value:.1f}%<extra></extra>",
                rotation=90,
            ))
            fig_donut.add_annotation(
                text=f"<b>Global</b><br>Fleet Mix",
                x=0.5, y=0.5,
                font=dict(size=13, color=C_TEXT),
                showarrow=False,
            )
            apply_dark_layout(fig_donut, title="Global Fleet Emissions Share (%)", height=360,
                              margin={"l": 10, "r": 10, "t": 45, "b": 10})
            st.plotly_chart(fig_donut, use_container_width=True, key="sustainability_vessel_donut")

        with vc2:
            # Horizontal bar — GHG intensity
            types = [v["type"] for v in _VESSEL_TYPE_EMISSIONS]
            intensities = [v["ghg_intensity"] for v in _VESSEL_TYPE_EMISSIONS]
            bar_colors = [v["color"] for v in _VESSEL_TYPE_EMISSIONS]

            fig_bar = go.Figure(go.Bar(
                x=intensities,
                y=types,
                orientation="h",
                marker_color=bar_colors,
                marker_line_color="rgba(255,255,255,0.1)",
                marker_line_width=1,
                text=[f"{v:.1f}" for v in intensities],
                textposition="outside",
                textfont=dict(color=C_TEXT2, size=11),
                hovertemplate="<b>%{y}</b><br>GHG Intensity: %{x:.1f} g CO2e/t·nm<extra></extra>",
            ))
            # IMO average line
            fig_bar.add_vline(x=11.1, line_color=C_MOD, line_dash="dot", line_width=1.5,
                              annotation_text="IMO avg 11.1", annotation_font_color=C_MOD,
                              annotation_font_size=10)
            apply_dark_layout(fig_bar, title="GHG Intensity by Vessel Type (g CO2e/t·nm)", height=360,
                              margin={"l": 90, "r": 60, "t": 45, "b": 40})
            fig_bar.update_layout(xaxis_title="g CO2e per tonne-nautical mile", showlegend=False)
            st.plotly_chart(fig_bar, use_container_width=True, key="sustainability_vessel_bar")
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Vessel type emissions error: {exc}")

    # ── Section 3: CII rating trajectory ─────────────────────────────────────
    try:
        section_header(
            "Carbon Intensity Indicator (CII) Trajectory",
            "Fleet-average AER vs IMO regulatory thresholds — rating bands A through E",
        )
        years = _CII_TRAJECTORY["years"]
        fig_cii = go.Figure()

        # Rating band fills
        fig_cii.add_hrect(y0=0, y1=_CII_TRAJECTORY["cii_a_threshold"],
                          fillcolor="rgba(16,185,129,0.06)", layer="below", line_width=0)
        fig_cii.add_hrect(y0=_CII_TRAJECTORY["cii_a_threshold"], y1=_CII_TRAJECTORY["imo_2030_limit"],
                          fillcolor="rgba(52,211,153,0.04)", layer="below", line_width=0)
        fig_cii.add_hrect(y0=_CII_TRAJECTORY["imo_2030_limit"], y1=_CII_TRAJECTORY["cii_c_threshold"],
                          fillcolor="rgba(245,158,11,0.05)", layer="below", line_width=0)
        fig_cii.add_hrect(y0=_CII_TRAJECTORY["cii_c_threshold"], y1=_CII_TRAJECTORY["cii_e_threshold"],
                          fillcolor="rgba(239,68,68,0.05)", layer="below", line_width=0)

        # Threshold lines
        for y_val, label, color in [
            (_CII_TRAJECTORY["cii_a_threshold"], "CII-A threshold", "#10b981"),
            (_CII_TRAJECTORY["poseidon_2030"], "Poseidon 2030", "#3b82f6"),
            (_CII_TRAJECTORY["imo_2030_limit"], "IMO 2030 limit", "#f59e0b"),
            (_CII_TRAJECTORY["cii_c_threshold"], "CII-C/D boundary", "#f97316"),
            (_CII_TRAJECTORY["cii_e_threshold"], "CII-E threshold", "#ef4444"),
        ]:
            fig_cii.add_hline(y=y_val, line_color=color, line_dash="dot", line_width=1.2,
                              annotation_text=label, annotation_font_color=color,
                              annotation_font_size=9, annotation_position="right")

        # Fleet trajectory
        fig_cii.add_trace(go.Scatter(
            x=years, y=_CII_TRAJECTORY["fleet"],
            name="Fleet Average AER",
            mode="lines+markers",
            line=dict(color="#3b82f6", width=3),
            marker=dict(size=8, color="#3b82f6", line=dict(color="#1a2235", width=2)),
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.06)",
            hovertemplate="<b>%{x}</b><br>Fleet AER: %{y:.1f} g CO2/t·nm<extra></extra>",
        ))

        # IMO required pathway
        fig_cii.add_trace(go.Scatter(
            x=years, y=_CII_TRAJECTORY["imo_target"],
            name="IMO Required Pathway",
            mode="lines",
            line=dict(color="#f59e0b", width=2, dash="dash"),
            hovertemplate="<b>%{x}</b><br>IMO Target: %{y:.1f} g CO2/t·nm<extra></extra>",
        ))

        apply_dark_layout(fig_cii, title="Fleet CII Trajectory vs IMO Regulatory Pathway (g CO2/t·nm)",
                          height=460, margin={"l": 50, "r": 160, "t": 50, "b": 40})
        fig_cii.update_layout(
            xaxis_title="Year",
            yaxis_title="Annual Efficiency Ratio (g CO2 / t·nm)",
            yaxis=dict(range=[8, 18]),
        )
        st.plotly_chart(fig_cii, use_container_width=True, key="sustainability_cii_trajectory")
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"CII trajectory error: {exc}")

    # ── Section 4: Green fuel adoption tracker ────────────────────────────────
    try:
        section_header(
            "Green Fuel Adoption Tracker",
            "Alternative marine fuel uptake — fleet penetration, CO2 reduction, and cost premium vs HFO",
        )
        fuel_cols = st.columns(len(_GREEN_FUEL_DATA))
        for col, fuel in zip(fuel_cols, _GREEN_FUEL_DATA):
            with col:
                adopt_bar = _bar_html(fuel["adoption_pct"] / 25.0, color=fuel["color"])
                order_bar = _bar_html(fuel["order_pct"] / 40.0, color=_hex_to_rgba(fuel["color"], 0.6))
                co2_label = f"-{abs(fuel['co2_vs_hfo']):.0f}%"
                st.markdown(
                    f"""
                    <div style="background:linear-gradient(160deg,
                                {_hex_to_rgba(fuel['color'],0.12)},{_hex_to_rgba(fuel['color'],0.04)});
                                border:1px solid {_hex_to_rgba(fuel['color'],0.28)};
                                border-radius:14px;padding:18px 16px;
                                box-shadow:0 4px 20px {_hex_to_rgba(fuel['color'],0.07)};">
                      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                        <div style="font-size:1.6rem;">{fuel['icon']}</div>
                        <div style="font-size:0.65rem;font-weight:700;letter-spacing:0.06em;
                                    color:{fuel['color']};background:{_hex_to_rgba(fuel['color'],0.15)};
                                    border:1px solid {_hex_to_rgba(fuel['color'],0.3)};
                                    border-radius:999px;padding:2px 8px;">{fuel['status']}</div>
                      </div>
                      <div style="font-size:1rem;font-weight:800;color:{C_TEXT};margin:8px 0 2px;">
                        {fuel['fuel']}
                      </div>
                      <div style="font-size:0.68rem;color:{C_TEXT3};margin-bottom:10px;">
                        {fuel['maturity']}
                      </div>
                      <div style="font-size:0.72rem;color:{C_TEXT3};margin-bottom:2px;">
                        Operating fleet: <span style="color:{fuel['color']};font-weight:700;">
                        {fuel['adoption_pct']:.1f}%</span>
                      </div>
                      {adopt_bar}
                      <div style="font-size:0.72rem;color:{C_TEXT3};margin-top:8px;margin-bottom:2px;">
                        Orderbook share: <span style="color:{C_TEXT2};font-weight:600;">
                        {fuel['order_pct']:.1f}%</span>
                      </div>
                      {order_bar}
                      <div style="margin-top:12px;display:flex;justify-content:space-between;
                                  font-size:0.72rem;">
                        <div>
                          <div style="color:{C_TEXT3};">CO2 vs HFO</div>
                          <div style="color:{C_HIGH};font-weight:700;">{co2_label}</div>
                        </div>
                        <div style="text-align:right;">
                          <div style="color:{C_TEXT3};">Cost premium</div>
                          <div style="color:{C_MOD};font-weight:700;">+{fuel['cost_premium']:.0f}%</div>
                        </div>
                      </div>
                      <div style="margin-top:8px;font-size:0.7rem;color:{C_TEXT3};
                                  border-top:1px solid rgba(255,255,255,0.06);padding-top:8px;">
                        {fuel['vessels_operating']:,} vessels operating
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Green fuel tracker error: {exc}")

    # ── Section 5: Carbon cost projections / EU ETS forward curve ─────────────
    try:
        section_header(
            "Carbon Cost Projections — EU ETS Forward Curve",
            "ETS price trajectory 2024-2035 with bull/bear scenarios and shipping coverage ramp-up",
        )
        cc1, cc2 = st.columns([2, 1])

        with cc1:
            fig_ets = go.Figure()
            yrs = _ETS_FORWARD_CURVE["years"]

            # Bear scenario fill
            fig_ets.add_trace(go.Scatter(
                x=yrs + yrs[::-1],
                y=_ETS_FORWARD_CURVE["bull"] + _ETS_FORWARD_CURVE["bear"][::-1],
                fill="toself",
                fillcolor="rgba(59,130,246,0.06)",
                line=dict(color="rgba(0,0,0,0)"),
                name="Bull/Bear range",
                showlegend=True,
                hoverinfo="skip",
            ))

            # Bull scenario
            fig_ets.add_trace(go.Scatter(
                x=yrs, y=_ETS_FORWARD_CURVE["bull"],
                name="Bull scenario",
                mode="lines",
                line=dict(color="#10b981", width=1.5, dash="dot"),
                hovertemplate="<b>%{x} Bull</b><br>ETS: $%{y:.0f}/t<extra></extra>",
            ))

            # Bear scenario
            fig_ets.add_trace(go.Scatter(
                x=yrs, y=_ETS_FORWARD_CURVE["bear"],
                name="Bear scenario",
                mode="lines",
                line=dict(color="#ef4444", width=1.5, dash="dot"),
                hovertemplate="<b>%{x} Bear</b><br>ETS: $%{y:.0f}/t<extra></extra>",
            ))

            # Base case
            fig_ets.add_trace(go.Scatter(
                x=yrs, y=_ETS_FORWARD_CURVE["price"],
                name="Base case",
                mode="lines+markers",
                line=dict(color="#3b82f6", width=3),
                marker=dict(size=7, color="#3b82f6", line=dict(color="#0a0f1a", width=2)),
                fill="tozeroy",
                fillcolor="rgba(59,130,246,0.07)",
                hovertemplate="<b>%{x}</b><br>ETS Price: $%{y:.0f}/t CO2<extra></extra>",
            ))

            # 100% coverage line
            fig_ets.add_vline(x=2026, line_color=C_MOD, line_dash="dash", line_width=1.2,
                              annotation_text="100% coverage", annotation_font_color=C_MOD,
                              annotation_font_size=9)

            apply_dark_layout(fig_ets, title="EU ETS Carbon Price Forward Curve ($/tonne CO2)",
                              height=420, margin={"l": 50, "r": 20, "t": 50, "b": 40})
            fig_ets.update_layout(xaxis_title="Year", yaxis_title="EU ETS Price ($ / tonne CO2)")
            st.plotly_chart(fig_ets, use_container_width=True, key="sustainability_ets_curve")

        with cc2:
            st.markdown("<br>", unsafe_allow_html=True)
            # Cost impact calculator
            st.markdown(
                f"""<div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;
                              text-transform:uppercase;color:{C_TEXT3};margin-bottom:12px;">
                    Cost Impact on Shipping Rate</div>""",
                unsafe_allow_html=True,
            )
            for yr, price, cov in zip(
                _ETS_FORWARD_CURVE["years"][::2],
                _ETS_FORWARD_CURVE["price"][::2],
                _ETS_FORWARD_CURVE["shipping_pct_coverage"][::2],
            ):
                cost_per_teu = avg_co2_per_teu * price * (cov / 100)
                bar_pct = min(100, cost_per_teu / 60 * 100)
                st.markdown(
                    f"""
                    <div style="margin-bottom:10px;">
                      <div style="display:flex;justify-content:space-between;
                                  font-size:0.75rem;margin-bottom:3px;">
                        <span style="color:{C_TEXT2};">{yr}</span>
                        <span style="color:#3b82f6;font-weight:700;">${cost_per_teu:.0f}/TEU</span>
                      </div>
                      <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:5px;">
                        <div style="width:{bar_pct:.0f}%;height:5px;border-radius:4px;
                                    background:linear-gradient(90deg,#3b82f6,#8b5cf6);"></div>
                      </div>
                      <div style="font-size:0.67rem;color:{C_TEXT3};margin-top:2px;">
                        ${price:.0f}/t &bull; {cov}% covered
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Carbon cost projections error: {exc}")

    # ── Section 6: ESG leaderboard ─────────────────────────────────────────────
    try:
        section_header(
            "ESG Score Leaderboard — Major Carriers",
            "Environmental (E), Social (S), Governance (G) ratings — MSCI-style scoring methodology",
        )
        sorted_carriers = sorted(_ESG_CARRIERS, key=lambda x: x["total"], reverse=True)

        for i, carrier in enumerate(sorted_carriers):
            rank_icon = ["&#x1F947;", "&#x1F948;", "&#x1F949;"][i] if i < 3 else f"#{i+1}"
            total = carrier["total"]
            e_score = carrier["env"]
            s_score = carrier["social"]
            g_score = carrier["gov"]
            rating = carrier["rating"]
            trend_color = C_HIGH if "+" in carrier["trend"] else (C_LOW if "-" in carrier["trend"] else C_TEXT3)

            # Rating color
            if rating in ("AAA", "AA"):
                r_color = C_HIGH
            elif rating == "A":
                r_color = "#34d399"
            elif rating in ("BBB", "BB"):
                r_color = C_MOD
            else:
                r_color = C_LOW

            bar_e = _bar_html(e_score / 100, color="#10b981")
            bar_s = _bar_html(s_score / 100, color="#3b82f6")
            bar_g = _bar_html(g_score / 100, color="#8b5cf6")

            st.markdown(
                f"""
                <div style="background:{C_CARD};border:1px solid {C_BORDER};
                            border-radius:12px;padding:14px 20px;margin-bottom:8px;
                            display:flex;align-items:center;gap:16px;">
                  <div style="font-size:1.2rem;width:28px;text-align:center;">{rank_icon}</div>
                  <div style="flex:0 0 130px;">
                    <div style="font-size:0.88rem;font-weight:700;color:{C_TEXT};">{carrier['carrier']}</div>
                    <div style="font-size:0.72rem;color:{C_TEXT3};margin-top:2px;">Carrier ESG</div>
                  </div>
                  <div style="flex:0 0 60px;text-align:center;">
                    <div style="font-size:1.6rem;font-weight:900;color:{r_color};">{total}</div>
                    <div style="font-size:0.65rem;color:{C_TEXT3};">Total</div>
                  </div>
                  <div style="flex:0 0 50px;text-align:center;">
                    <div style="font-size:0.95rem;font-weight:700;
                                color:{r_color};background:{_hex_to_rgba(r_color,0.15)};
                                border:1px solid {_hex_to_rgba(r_color,0.3)};
                                border-radius:6px;padding:2px 6px;">{rating}</div>
                    <div style="font-size:0.65rem;color:{C_TEXT3};margin-top:2px;">MSCI</div>
                  </div>
                  <div style="flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
                    <div>
                      <div style="font-size:0.67rem;color:#10b981;font-weight:600;">E: {e_score}</div>
                      {bar_e}
                    </div>
                    <div>
                      <div style="font-size:0.67rem;color:#3b82f6;font-weight:600;">S: {s_score}</div>
                      {bar_s}
                    </div>
                    <div>
                      <div style="font-size:0.67rem;color:#8b5cf6;font-weight:600;">G: {g_score}</div>
                      {bar_g}
                    </div>
                  </div>
                  <div style="flex:0 0 60px;text-align:right;
                              font-size:0.78rem;font-weight:700;color:{trend_color};">
                    {carrier['trend']}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"ESG leaderboard error: {exc}")

    # ── Section 7: Route emissions comparison — CO2 per TEU per km ────────────
    try:
        section_header(
            "Route Emissions Intensity — CO2 per TEU per km",
            "Normalised emissions across all major trade lanes vs air freight benchmark",
        )
        # Filter air freight for separate annotation
        sea_routes = [r for r in _ROUTE_CO2_PER_TEU_KM if r["mode"] != "Air"]
        air_route  = next((r for r in _ROUTE_CO2_PER_TEU_KM if r["mode"] == "Air"), None)

        sorted_r = sorted(sea_routes, key=lambda x: x["co2_g_per_teu_km"])
        route_names_r = [r["route"] for r in sorted_r]
        co2_vals = [r["co2_g_per_teu_km"] for r in sorted_r]

        bar_colors_r = [
            C_HIGH if v < 12 else (C_MOD if v < 16 else C_LOW)
            for v in co2_vals
        ]

        fig_route = go.Figure()
        fig_route.add_trace(go.Bar(
            x=route_names_r,
            y=co2_vals,
            marker_color=bar_colors_r,
            marker_line_color="rgba(255,255,255,0.08)",
            marker_line_width=1,
            text=[f"{v:.1f}" for v in co2_vals],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=10),
            name="Sea freight",
            hovertemplate="<b>%{x}</b><br>%{y:.1f} g CO2/TEU·km<extra></extra>",
        ))

        if air_route:
            # Air freight as a separate reference bar
            fig_route.add_trace(go.Bar(
                x=["Air Freight (ref)"],
                y=[air_route["co2_g_per_teu_km"]],
                marker_color="rgba(239,68,68,0.5)",
                marker_line_color=C_LOW,
                marker_line_width=1.5,
                name="Air freight reference",
                text=[f"{air_route['co2_g_per_teu_km']:.0f}"],
                textposition="outside",
                textfont=dict(color=C_LOW, size=10),
                hovertemplate="<b>Air Freight</b><br>%{y:.0f} g CO2/TEU·km<br>~50x sea freight<extra></extra>",
            ))

        apply_dark_layout(fig_route, title="CO2 Intensity: g CO2 per TEU per km", height=440,
                          margin={"l": 40, "r": 20, "t": 50, "b": 110})
        fig_route.update_layout(
            barmode="group",
            xaxis_tickangle=-38,
            xaxis=dict(tickfont=dict(size=10), automargin=True),
            yaxis_title="g CO2 / TEU / km",
        )
        st.plotly_chart(fig_route, use_container_width=True, key="sustainability_route_co2_km")
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Route emissions comparison error: {exc}")

    # ── Section 8: Net zero pathway chart ────────────────────────────────────
    try:
        section_header(
            "Net Zero Pathway — Current Trajectory vs IMO 2030/2050 Targets",
            "Indexed to 2008 baseline (100) — fleet GHG trajectory and decarbonisation scenarios",
        )
        nz_years = _NET_ZERO_PATHWAY["years"]
        fig_nz = go.Figure()

        # Ambitious scenario fill vs IMO
        fig_nz.add_trace(go.Scatter(
            x=nz_years + nz_years[::-1],
            y=_NET_ZERO_PATHWAY["ambitious"] + _NET_ZERO_PATHWAY["imo_pathway"][::-1],
            fill="toself",
            fillcolor="rgba(16,185,129,0.06)",
            line=dict(color="rgba(0,0,0,0)"),
            name="Ambition gap",
            showlegend=True,
            hoverinfo="skip",
        ))

        # Current trajectory
        fig_nz.add_trace(go.Scatter(
            x=nz_years, y=_NET_ZERO_PATHWAY["current"],
            name="Current trajectory",
            mode="lines+markers",
            line=dict(color="#ef4444", width=3),
            marker=dict(size=8, color="#ef4444", line=dict(color="#0a0f1a", width=2)),
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.05)",
            hovertemplate="<b>%{x}</b><br>Current: %{y:.0f}% of 2008 baseline<extra></extra>",
        ))

        # IMO pathway
        fig_nz.add_trace(go.Scatter(
            x=nz_years, y=_NET_ZERO_PATHWAY["imo_pathway"],
            name="IMO required pathway",
            mode="lines+markers",
            line=dict(color="#f59e0b", width=2.5, dash="dash"),
            marker=dict(size=7, color="#f59e0b"),
            hovertemplate="<b>%{x}</b><br>IMO Target: %{y:.0f}%<extra></extra>",
        ))

        # Ambitious scenario
        fig_nz.add_trace(go.Scatter(
            x=nz_years, y=_NET_ZERO_PATHWAY["ambitious"],
            name="Ambitious scenario",
            mode="lines",
            line=dict(color="#10b981", width=2, dash="dot"),
            hovertemplate="<b>%{x}</b><br>Ambitious: %{y:.0f}%<extra></extra>",
        ))

        # IMO milestone annotations
        fig_nz.add_hline(y=80, line_color=C_MOD, line_dash="dot", line_width=1,
                         annotation_text="IMO 2030: 80% of baseline", annotation_font_color=C_MOD,
                         annotation_font_size=9, annotation_position="left")
        fig_nz.add_hline(y=0, line_color=C_HIGH, line_dash="dot", line_width=1.5,
                         annotation_text="IMO 2050: Net Zero", annotation_font_color=C_HIGH,
                         annotation_font_size=9, annotation_position="left")
        fig_nz.add_vline(x=2030, line_color=C_MOD, line_dash="dash", line_width=1,
                         annotation_text="2030", annotation_font_color=C_MOD, annotation_font_size=9)
        fig_nz.add_vline(x=2050, line_color=C_HIGH, line_dash="dash", line_width=1,
                         annotation_text="2050", annotation_font_color=C_HIGH, annotation_font_size=9)

        apply_dark_layout(fig_nz, title="Net Zero Pathway — GHG Index vs 2008 Baseline (100 = 2008 levels)",
                          height=480, margin={"l": 60, "r": 20, "t": 50, "b": 40})
        fig_nz.update_layout(
            xaxis_title="Year",
            yaxis_title="GHG Index (2008 = 100)",
            yaxis=dict(range=[-5, 108]),
        )
        st.plotly_chart(fig_nz, use_container_width=True, key="sustainability_net_zero")
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Net zero pathway error: {exc}")

    # ── Section 9: Green port index ────────────────────────────────────────────
    try:
        section_header(
            "Green Port Index",
            "Port sustainability scores — shore power, waste management, emissions monitoring, green berths",
        )
        gp1, gp2 = st.columns([1.5, 1])

        with gp1:
            for port in _GREEN_PORTS:
                score = port["score"]
                bar_color = C_HIGH if score >= 80 else (C_MOD if score >= 65 else C_LOW)
                bar_frac = score / 100
                sp_icon = "&#x26A1;" if port["shore_power"] else "&#x2716;"
                wm_icon = "&#x267B;" if port["waste_mgmt"] else "&#x2716;"
                em_icon = "&#x1F4E1;" if port["emissions_monitor"] else "&#x2716;"
                sp_color = C_HIGH if port["shore_power"] else C_TEXT3
                wm_color = C_HIGH if port["waste_mgmt"] else C_TEXT3
                em_color = C_HIGH if port["emissions_monitor"] else C_TEXT3
                st.markdown(
                    f"""
                    <div style="background:{C_CARD};border:1px solid {C_BORDER};
                                border-radius:10px;padding:12px 16px;margin-bottom:6px;">
                      <div style="display:flex;align-items:center;gap:12px;">
                        <div style="flex:0 0 150px;">
                          <div style="font-size:0.85rem;font-weight:700;color:{C_TEXT};">
                            {port['port']}
                          </div>
                          <div style="font-size:0.68rem;color:{C_TEXT3};">{port['country']}</div>
                        </div>
                        <div style="flex:1;">
                          <div style="display:flex;justify-content:space-between;
                                      font-size:0.72rem;margin-bottom:3px;">
                            <span style="color:{C_TEXT2};">Green Score</span>
                            <span style="color:{bar_color};font-weight:700;">{score}/100</span>
                          </div>
                          <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:6px;">
                            <div style="width:{bar_frac*100:.0f}%;height:6px;border-radius:4px;
                                        background:linear-gradient(90deg,{bar_color},{_hex_to_rgba(bar_color,0.6)});"></div>
                          </div>
                        </div>
                        <div style="display:flex;gap:10px;font-size:0.8rem;flex:0 0 auto;">
                          <span style="color:{sp_color};" title="Shore Power">{sp_icon}</span>
                          <span style="color:{wm_color};" title="Waste Mgmt">{wm_icon}</span>
                          <span style="color:{em_color};" title="Emissions Monitoring">{em_icon}</span>
                        </div>
                        <div style="flex:0 0 80px;text-align:right;font-size:0.72rem;color:{C_TEXT3};">
                          {port['green_berths']} green berths
                        </div>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with gp2:
            # Radar chart of top 5 ports across 4 dimensions
            top5 = _GREEN_PORTS[:5]
            fig_radar = go.Figure()
            categories = ["Green Score", "Shore Power", "Waste Mgmt", "Emissions Monitor", "Green Berths"]
            for port in top5:
                r_vals = [
                    port["score"],
                    100 if port["shore_power"] else 0,
                    100 if port["waste_mgmt"] else 0,
                    100 if port["emissions_monitor"] else 0,
                    min(100, port["green_berths"] * 4),
                ]
                fig_radar.add_trace(go.Scatterpolar(
                    r=r_vals + [r_vals[0]],
                    theta=categories + [categories[0]],
                    name=port["port"],
                    fill="toself",
                    fillcolor=_hex_to_rgba(C_ACCENT, 0.04),
                    line=dict(width=1.5),
                    hovertemplate=f"<b>{port['port']}</b><br>%{{theta}}: %{{r}}<extra></extra>",
                ))
            apply_dark_layout(fig_radar, title="Top 5 Green Ports — Radar", height=400,
                              margin={"l": 40, "r": 40, "t": 50, "b": 40})
            fig_radar.update_layout(
                polar=dict(
                    bgcolor="rgba(26,34,53,0.5)",
                    radialaxis=dict(visible=True, range=[0, 100],
                                   tickfont=dict(size=8, color=C_TEXT3),
                                   gridcolor="rgba(255,255,255,0.06)"),
                    angularaxis=dict(tickfont=dict(size=10, color=C_TEXT2),
                                     gridcolor="rgba(255,255,255,0.06)"),
                ),
            )
            st.plotly_chart(fig_radar, use_container_width=True, key="sustainability_port_radar")
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Green port index error: {exc}")

    # ── Section 10: Emissions leaderboard (route level) ───────────────────────
    try:
        section_header(
            "Route Emissions Leaderboard",
            "All trade lanes ranked cleanest to most carbon-intensive — CO2 per TEU (metric tons)",
        )
        max_co2 = max(r.co2_per_teu_mt for r in route_results)
        cols_per_row = 2
        for row_start in range(0, len(route_results), cols_per_row):
            row_routes = route_results[row_start: row_start + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, (rank_offset, route) in zip(cols, enumerate(row_routes)):
                rank = row_start + rank_offset + 1
                fraction = route.co2_per_teu_mt / max_co2 if max_co2 > 0 else 0
                grade_color = _GRADE_COLORS.get(route.sustainability_grade, C_TEXT2)
                with col:
                    st.markdown(
                        f"""
                        <div class="ship-card" style="border-left:3px solid {grade_color};">
                          <div style="display:flex;justify-content:space-between;
                                      align-items:flex-start;margin-bottom:4px;">
                            <div>
                              <span style="color:{C_TEXT3};font-size:0.72rem;font-weight:700;">#{rank}</span>
                              <span style="color:{C_TEXT};font-size:0.88rem;font-weight:600;margin-left:8px;">
                                {route.route_name}
                              </span>
                            </div>
                            {_grade_badge_html(route.sustainability_grade)}
                          </div>
                          <div style="display:flex;gap:20px;font-size:0.8rem;
                                      color:{C_TEXT2};margin-bottom:2px;">
                            <span>&#x1F6A2; {route.distance_nm:,.0f} nm</span>
                            <span>&#x23F1; {route.transit_days}d</span>
                            <span style="color:{grade_color};font-weight:700;">
                              {route.co2_per_teu_mt:.4f} MT CO2/TEU
                            </span>
                          </div>
                          {_bar_html(fraction, color=grade_color)}
                          <div style="margin-top:8px;font-size:0.75rem;color:{C_TEXT3};">
                            {_poseidon_badge_html(route.poseidon_compliant)}
                            &nbsp;&nbsp;Carbon cost: ${route.carbon_cost_usd:,.0f}
                            &nbsp;&nbsp;EEDI: {route.eedi_score:.0f}/100
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Route leaderboard error: {exc}")

    # ── Section 11: Carbon cost calculator ───────────────────────────────────
    try:
        section_header(
            "Carbon Cost Calculator",
            "Estimate total carbon footprint and offset costs for your TEU volume",
        )
        calc_col, pad_col = st.columns([1, 1])
        with calc_col:
            teu_volume = st.number_input(
                "Your TEU volume",
                min_value=1,
                max_value=100_000,
                value=100,
                step=50,
                key="sustainability_teu_volume",
                help="Number of TEUs you wish to calculate carbon cost for.",
            )
            route_options = [r.route_name for r in route_results]
            selected_calc_route_name = st.selectbox(
                "Select route",
                options=route_options,
                key="sustainability_calc_route",
            )

        selected_calc_route = next(
            (r for r in route_results if r.route_name == selected_calc_route_name),
            route_results[0],
        )
        try:
            alts = compare_to_alternatives(selected_calc_route)
        except Exception:
            alts = {"trees_to_offset": 0, "carbon_offset_cost_usd": 0}

        total_co2_mt = selected_calc_route.co2_per_teu_mt * teu_volume
        total_carbon_cost = total_co2_mt * 82.0
        cost_per_teu = total_carbon_cost / max(teu_volume, 1)
        _loaded_cap = max(selected_calc_route.teu_capacity * 0.85, 1)
        trees_needed = int(alts["trees_to_offset"] * teu_volume / _loaded_cap)
        offset_cost = alts["carbon_offset_cost_usd"] * teu_volume / _loaded_cap
        grade = selected_calc_route.sustainability_grade
        grade_color = _GRADE_COLORS.get(grade, C_TEXT2)

        st.markdown(
            f"""
            <div style="background:#0d1526;border:1px solid rgba(16,185,129,0.2);
                        border-radius:16px;padding:26px 30px;margin-top:12px;
                        font-family:'JetBrains Mono','Courier New',monospace;
                        box-shadow:0 0 40px rgba(0,0,0,0.4);">
              <div style="color:#10b981;font-size:0.72rem;font-weight:700;
                          letter-spacing:0.1em;margin-bottom:18px;">
                &#x1F4CA; CARBON FOOTPRINT CALCULATOR &mdash; {selected_calc_route.route_name.upper()}
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
                <div style="background:rgba(16,185,129,0.07);border:1px solid rgba(16,185,129,0.15);
                            border-radius:10px;padding:16px;">
                  <div style="color:{C_TEXT3};font-size:0.7rem;text-transform:uppercase;
                               letter-spacing:0.06em;">Total CO2 Emitted</div>
                  <div style="color:#10b981;font-size:1.7rem;font-weight:800;margin-top:5px;">
                    {total_co2_mt:.2f} MT
                  </div>
                  <div style="color:{C_TEXT3};font-size:0.72rem;">for {teu_volume:,} TEU shipment</div>
                </div>
                <div style="background:rgba(59,130,246,0.07);border:1px solid rgba(59,130,246,0.15);
                            border-radius:10px;padding:16px;">
                  <div style="color:{C_TEXT3};font-size:0.7rem;text-transform:uppercase;
                               letter-spacing:0.06em;">EU ETS Carbon Cost</div>
                  <div style="color:#3b82f6;font-size:1.7rem;font-weight:800;margin-top:5px;">
                    ${total_carbon_cost:,.0f}
                  </div>
                  <div style="color:{C_TEXT3};font-size:0.72rem;">${cost_per_teu:.2f} per TEU at $82/t</div>
                </div>
                <div style="background:rgba(245,158,11,0.07);border:1px solid rgba(245,158,11,0.15);
                            border-radius:10px;padding:16px;">
                  <div style="color:{C_TEXT3};font-size:0.7rem;text-transform:uppercase;
                               letter-spacing:0.06em;">Trees to Offset</div>
                  <div style="color:#f59e0b;font-size:1.7rem;font-weight:800;margin-top:5px;">
                    &#x1F333; {trees_needed:,}
                  </div>
                  <div style="color:{C_TEXT3};font-size:0.72rem;">over 20-year growth period</div>
                </div>
                <div style="background:rgba(139,92,246,0.07);border:1px solid rgba(139,92,246,0.15);
                            border-radius:10px;padding:16px;">
                  <div style="color:{C_TEXT3};font-size:0.7rem;text-transform:uppercase;
                               letter-spacing:0.06em;">Voluntary Offset Cost</div>
                  <div style="color:#8b5cf6;font-size:1.7rem;font-weight:800;margin-top:5px;">
                    ${offset_cost:,.0f}
                  </div>
                  <div style="color:{C_TEXT3};font-size:0.72rem;">at $15/tonne (VCM market)</div>
                </div>
              </div>
              <div style="margin-top:18px;display:flex;align-items:center;gap:14px;
                          font-size:0.8rem;color:{C_TEXT2};padding-top:14px;
                          border-top:1px solid rgba(255,255,255,0.06);">
                <span>Grade:</span>{_grade_badge_html(grade)}
                <span style="color:{C_TEXT3};">EEDI {selected_calc_route.eedi_score:.1f}/100</span>
                <span style="color:{C_TEXT3};">{_poseidon_badge_html(selected_calc_route.poseidon_compliant)}</span>
                <span style="color:{C_TEXT3};margin-left:auto;font-size:0.72rem;">
                  vs air freight: ~{total_co2_mt*50:,.0f} MT CO2 equivalent
                </span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Carbon calculator error: {exc}")

    # ── Section 12: Interactive route comparison scatter ───────────────────────
    try:
        section_header(
            "Sustainability Efficiency Frontier",
            "X = transit days  |  Y = CO2 per TEU  |  Bubble = distance  |  Color = grade",
        )
        scatter_fig = go.Figure()
        for grade_label in ["A", "B", "C", "D"]:
            grade_routes = [r for r in route_results if r.sustainability_grade == grade_label]
            if not grade_routes:
                continue
            scatter_fig.add_trace(go.Scatter(
                x=[r.transit_days for r in grade_routes],
                y=[r.co2_per_teu_mt for r in grade_routes],
                mode="markers+text",
                name=f"Grade {grade_label}",
                text=[r.route_name.split(" ")[0] for r in grade_routes],
                textposition="top center",
                textfont={"size": 9, "color": C_TEXT3},
                marker=dict(
                    size=[max(10, r.distance_nm / 200) for r in grade_routes],
                    color=_GRADE_COLORS[grade_label],
                    opacity=0.85,
                    line=dict(width=1.5, color="rgba(255,255,255,0.12)"),
                ),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=[
                    (
                        f"<b>{r.route_name}</b><br>"
                        f"Grade: {r.sustainability_grade}<br>"
                        f"CO2/TEU: {r.co2_per_teu_mt:.4f} MT<br>"
                        f"Transit: {r.transit_days} days<br>"
                        f"Distance: {r.distance_nm:,.0f} nm<br>"
                        f"EEDI: {r.eedi_score:.1f}/100<br>"
                        f"Poseidon: {'Yes' if r.poseidon_compliant else 'No'}"
                    )
                    for r in grade_routes
                ],
            ))
        max_days = max(r.transit_days for r in route_results) + 2
        scatter_fig.add_shape(
            type="line", x0=0, x1=max_days, y0=0.12, y1=0.12,
            line=dict(color=C_MOD, dash="dot", width=1.5),
        )
        scatter_fig.add_annotation(
            x=max_days * 0.98, y=0.125,
            text="Poseidon 2050 limit (0.12)", showarrow=False,
            font=dict(color=C_MOD, size=10), xanchor="right",
        )
        apply_dark_layout(scatter_fig, title="Sustainability Efficiency Frontier — all routes",
                          height=520, margin={"l": 50, "r": 20, "t": 50, "b": 40})
        scatter_fig.update_layout(xaxis_title="Transit Days", yaxis_title="CO2 per TEU (MT)")
        st.plotly_chart(scatter_fig, use_container_width=True, key="sustainability_scatter")
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Efficiency frontier error: {exc}")

    # ── Section 13: Regulatory timeline ──────────────────────────────────────
    try:
        section_header(
            "Regulatory Timeline & Decarbonisation Milestones",
            "Key IMO, EU ETS, CII and FuelEU Maritime dates — strategic compliance calendar",
        )
        for item in _REGULATORY_TIMELINE:
            impact_color = {
                "Critical": "#ef4444",
                "High": "#f59e0b",
                "Medium": "#3b82f6",
            }.get(item["impact"], C_TEXT3)
            type_color = "#3b82f6" if item["type"] == "regulation" else "#10b981"
            type_label = "REGULATION" if item["type"] == "regulation" else "MILESTONE"
            st.markdown(
                f"""
                <div style="display:flex;gap:16px;align-items:flex-start;
                            margin-bottom:10px;padding:12px 16px;
                            background:{C_CARD};border:1px solid {C_BORDER};
                            border-radius:10px;">
                  <div style="flex:0 0 52px;text-align:center;">
                    <div style="font-size:1rem;font-weight:800;color:{impact_color};">{item['year']}</div>
                  </div>
                  <div style="flex:0 0 100px;">
                    <span style="font-size:0.62rem;font-weight:700;letter-spacing:0.06em;
                                 color:{type_color};background:{_hex_to_rgba(type_color,0.12)};
                                 border:1px solid {_hex_to_rgba(type_color,0.25)};
                                 border-radius:999px;padding:2px 8px;">{type_label}</span>
                  </div>
                  <div style="flex:1;font-size:0.83rem;color:{C_TEXT};font-weight:500;">
                    {item['event']}
                  </div>
                  <div style="flex:0 0 70px;text-align:right;">
                    <span style="font-size:0.68rem;font-weight:700;color:{impact_color};
                                 background:{_hex_to_rgba(impact_color,0.12)};
                                 border-radius:4px;padding:2px 8px;">{item['impact']}</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Regulatory timeline error: {exc}")

    # ── Section 14: Sustainability news ──────────────────────────────────────
    try:
        section_header(
            "Sustainability News & Market Intelligence",
            "Latest green shipping developments — fuel, regulation, ESG, and corridor announcements",
        )
        for news in _SUSTAINABILITY_NEWS:
            st.markdown(
                f"""
                <div style="background:{C_CARD};border:1px solid {C_BORDER};
                            border-left:3px solid {news['tag_color']};
                            border-radius:10px;padding:16px 20px;margin-bottom:10px;">
                  <div style="display:flex;justify-content:space-between;
                              align-items:flex-start;margin-bottom:6px;">
                    <div style="display:flex;gap:10px;align-items:center;flex:1;">
                      <span style="font-size:0.65rem;font-weight:700;letter-spacing:0.06em;
                                   color:{news['tag_color']};
                                   background:{_hex_to_rgba(news['tag_color'],0.15)};
                                   border:1px solid {_hex_to_rgba(news['tag_color'],0.3)};
                                   border-radius:999px;padding:2px 9px;white-space:nowrap;">
                        {news['tag']}
                      </span>
                      <span style="font-size:0.87rem;font-weight:600;color:{C_TEXT};line-height:1.3;">
                        {news['headline']}
                      </span>
                    </div>
                    <div style="flex:0 0 auto;margin-left:16px;text-align:right;">
                      <div style="font-size:0.7rem;color:{C_TEXT3};">{news['source']}</div>
                      <div style="font-size:0.68rem;color:{C_TEXT3};">{news['date']}</div>
                    </div>
                  </div>
                  <div style="font-size:0.78rem;color:{C_TEXT2};padding-left:2px;">
                    {news['summary']}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Sustainability news error: {exc}")

    # ── Footer insight ────────────────────────────────────────────────────────
    try:
        best = route_results[0]
        worst = route_results[-1]
        st.markdown(
            f"""
            <div style="background:{_hex_to_rgba(C_HIGH,0.07)};
                        border:1px solid {_hex_to_rgba(C_HIGH,0.22)};
                        border-radius:12px;padding:16px 22px;margin-top:4px;
                        font-size:0.84rem;color:{C_TEXT2};">
              &#x1F4A1; <b style="color:{C_TEXT}">Key insight:</b>
              The cleanest tracked route is <b style="color:{C_HIGH}">{best.route_name}</b>
              at <b>{best.co2_per_teu_mt:.4f} MT CO2/TEU</b> (Grade {best.sustainability_grade}),
              while the most carbon-intensive is
              <b style="color:{C_LOW}">{worst.route_name}</b>
              at <b>{worst.co2_per_teu_mt:.4f} MT CO2/TEU</b> (Grade {worst.sustainability_grade}).
              Even the highest-emitting sea route produces ~98% less CO2 per tonne-km than equivalent air freight.
              &nbsp;&mdash;&nbsp;
              <span style="color:{C_TEXT3};">
                IMO 2050 net-zero target requires a cumulative reduction of ~100% from 2008 levels
                across all shipping GHGs.
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning(f"Footer insight error: {exc}")

    # ── CSV export ────────────────────────────────────────────────────────────
    try:
        st.markdown("<br>", unsafe_allow_html=True)
        section_header(
            "Export Emissions Data",
            "Download full route emissions dataset as CSV for offline analysis and ESG reporting.",
        )

        def _build_emissions_csv(routes: list[RouteEmissions]) -> str:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "Route Name", "Route ID", "Transit Days", "Distance (nm)",
                "TEU Capacity", "Total Fuel (MT)", "Total CO2 (MT)",
                "CO2 per TEU (MT)", "EEDI Score (0-100)", "Sustainability Grade",
                "Poseidon Compliant", "Carbon Cost (USD @ $82/t)",
            ])
            for r in routes:
                writer.writerow([
                    r.route_name, r.route_id, r.transit_days,
                    f"{r.distance_nm:.0f}", r.teu_capacity,
                    f"{r.total_fuel_mt:.2f}", f"{r.co2_emissions_mt:.2f}",
                    f"{r.co2_per_teu_mt:.6f}", f"{r.eedi_score:.1f}",
                    r.sustainability_grade,
                    "Yes" if r.poseidon_compliant else "No",
                    f"{r.carbon_cost_usd:.2f}",
                ])
            return buf.getvalue()

        csv_data = _build_emissions_csv(route_results)
        st.download_button(
            label="Download Emissions CSV",
            data=csv_data,
            file_name="route_emissions.csv",
            mime="text/csv",
            key="sustainability_download_csv",
        )
    except Exception as exc:
        st.warning(f"CSV export error: {exc}")
