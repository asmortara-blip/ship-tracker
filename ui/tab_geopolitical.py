"""
Geopolitical Risk Monitor Tab — Enhanced Edition

Sections:
  0.  Hero Dashboard          — Global risk score gauge, conflict count, sanctions exposure, shipping impact
  1.  Risk Heatmap Map        — Plotly choropleth with country risk scores and hover details
  2.  Active Conflict Tracker — Cards for each active conflict with risk level, shipping impact, affected routes
  3.  Sanctions Monitor       — Jurisdiction tracker with coverage %, active sanctions, vessel screening
  4.  Political Risk by Lane  — Horizontal bar chart of geopolitical risk score per trade corridor
  5.  Conflict Escalation TL  — Historical chart of risk score with conflict event annotations
  6.  Supply Chain Vuln. Map  — Key sourcing countries ranked by political stability score
  7.  Piracy & Security       — Incident map with monthly frequency chart
  8.  Diplomatic Tension      — Bilateral tension matrix (US, China, EU, Russia, key partners)
  9.  Scenario Analysis       — 3 scenario cards (base/bull/bear) with freight rate implications
  10. World Risk Globe        — Orthographic scattergeo with chokepoints and shipping lanes
  11. Route Risk Matrix       — All routes ranked by composite geopolitical score
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.geopolitical_monitor import (
    CURRENT_RISK_EVENTS,
    GeopoliticalEvent,
    get_chokepoint_exposure,
    compute_geopolitical_score,
    compute_expected_rate_impact,
    get_route_risk_events,
    get_all_route_scores,
    get_risk_color,
)

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

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
C_ORANGE = "#f97316"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"

_LEVEL_COLOR: dict[str, str] = {
    "CRITICAL": C_DANGER,
    "HIGH":     C_ORANGE,
    "MODERATE": C_WARN,
    "LOW":      C_HIGH,
}

# ---------------------------------------------------------------------------
# Shared CSS
# ---------------------------------------------------------------------------

_PULSE_CSS = """
<style>
@keyframes geo-pulse {
    0%   { opacity: 1; box-shadow: 0 0 0 0 rgba(239,68,68,0.6); }
    70%  { opacity: 0.85; box-shadow: 0 0 0 12px rgba(239,68,68,0.0); }
    100% { opacity: 1; box-shadow: 0 0 0 0 rgba(239,68,68,0.0); }
}
@keyframes card-glow {
    0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.0); }
    50%     { box-shadow: 0 0 18px 2px rgba(239,68,68,0.18); }
}
@keyframes fade-in-up {
    from { opacity:0; transform:translateY(10px); }
    to   { opacity:1; transform:translateY(0); }
}
</style>
"""

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _card(content: str, border_color: str = C_BORDER, extra_style: str = "") -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {border_color};'
        f'border-radius:14px;padding:20px 22px;margin-bottom:14px;{extra_style}">'
        + content + "</div>"
    )

def _section_title(text: str, subtitle: str = "") -> None:
    sub = (f'<div style="color:{C_TEXT2};font-size:0.83rem;margin-top:3px">{subtitle}</div>'
           if subtitle else "")
    st.markdown(
        f'<div style="margin:6px 0 16px">'
        f'<div style="font-size:1.08rem;font-weight:700;color:{C_TEXT};letter-spacing:-0.01em">{text}</div>'
        + sub + "</div>",
        unsafe_allow_html=True,
    )

def _kpi_card(value: str, label: str, color: str, sublabel: str = "") -> str:
    sub = (f'<div style="font-size:0.65rem;color:{C_TEXT3};margin-top:2px">{sublabel}</div>'
           if sublabel else "")
    return (
        f'<div style="background:{C_CARD2};border:1px solid {color}22;border-radius:12px;'
        f'padding:16px 18px;text-align:center">'
        f'<div style="font-size:1.9rem;font-weight:800;color:{color};line-height:1">{value}</div>'
        f'<div style="font-size:0.72rem;color:{C_TEXT2};margin-top:5px;font-weight:600">{label}</div>'
        + sub + "</div>"
    )

def _risk_badge(level: str, pulse: bool = False) -> str:
    color = _LEVEL_COLOR.get(level, C_TEXT2)
    anim = "animation:geo-pulse 1.6s ease-in-out infinite;" if pulse else ""
    return (
        f'<span style="background:rgba(0,0,0,0.35);color:{color};border:1px solid {color};'
        f'padding:2px 11px;border-radius:999px;font-size:0.69rem;font-weight:700;'
        f'white-space:nowrap;{anim}">{level}</span>'
    )

def _pill(text: str, color: str = C_ACCENT) -> str:
    return (
        f'<span style="display:inline-block;background:{color}18;color:{color};'
        f'border:1px solid {color}44;padding:1px 9px;border-radius:999px;'
        f'font-size:0.68rem;font-weight:600;margin:2px 3px 2px 0;white-space:nowrap">{text}</span>'
    )

def _bar_h(pct: float, color: str, label: str, max_pct: float = 100.0) -> str:
    w = min(100.0, abs(pct) / max(max_pct, 1) * 100)
    sign = "+" if pct >= 0 else ""
    return (
        f'<div style="margin-bottom:7px">'
        f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
        f'<span style="font-size:0.73rem;color:{C_TEXT2}">{label}</span>'
        f'<span style="font-size:0.73rem;font-weight:700;color:{color}">{sign}{pct:.1f}%</span>'
        f'</div>'
        f'<div style="background:rgba(255,255,255,0.07);border-radius:4px;height:7px">'
        f'<div style="width:{w:.1f}%;background:{color};border-radius:4px;height:7px"></div>'
        f'</div></div>'
    )

def _divider() -> None:
    st.markdown(
        f'<div style="border-top:1px solid {C_BORDER};margin:28px 0"></div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

_CHOKEPOINTS: list[dict] = [
    {"name": "Strait of Hormuz",   "lat": 26.6,  "lon": 56.3,   "risk": "MODERATE", "traffic_pct": 20, "note": "20% global oil transit; elevated Iran tension risk"},
    {"name": "Suez Canal",         "lat": 30.0,  "lon": 32.5,   "risk": "HIGH",     "traffic_pct": 12, "note": "12% world trade; Red Sea crisis diverting traffic"},
    {"name": "Strait of Malacca",  "lat": 2.5,   "lon": 101.0,  "risk": "MODERATE", "traffic_pct": 40, "note": "40% world trade; South China Sea spillover risk"},
    {"name": "Panama Canal",       "lat": 9.0,   "lon": -79.5,  "risk": "MODERATE", "traffic_pct": 5,  "note": "5% world trade; drought capacity constraints"},
    {"name": "Taiwan Strait",      "lat": 24.0,  "lon": 120.0,  "risk": "HIGH",     "traffic_pct": 26, "note": "26% container trade; military exercise disruption risk"},
    {"name": "Bab-el-Mandeb",      "lat": 12.5,  "lon": 43.5,   "risk": "CRITICAL", "traffic_pct": 10, "note": "Houthi attacks; major carriers fully rerouting"},
    {"name": "Danish Straits",     "lat": 55.5,  "lon": 11.0,   "risk": "LOW",      "traffic_pct": 3,  "note": "North Europe access; Russian shadow fleet monitoring"},
    {"name": "Cape of Good Hope",  "lat": -34.4, "lon": 18.5,   "risk": "LOW",      "traffic_pct": 8,  "note": "Alternate Asia-Europe route; surging post-Red Sea"},
]

_SHIPPING_LANES: list[dict] = [
    {"name": "Asia-Europe (Red Sea/Suez)",
     "lats": [31.2, 29.9, 20.0, 12.5, 5.0, -5.0, -25.0, -34.4, -30.0, 0.0, 20.0, 51.5],
     "lons": [121.5, 121.0, 55.0, 43.5, 43.0, 40.0, 20.0, 18.5, -5.0, -5.0, -10.0, 1.0], "risk": "CRITICAL"},
    {"name": "Trans-Pacific Eastbound",
     "lats": [31.2, 35.0, 40.0, 45.0, 47.0, 37.8],
     "lons": [121.5, 150.0, 170.0, -175.0, -160.0, -122.4], "risk": "HIGH"},
    {"name": "Transatlantic",
     "lats": [51.5, 50.0, 48.0, 44.0, 40.7],
     "lons": [1.0, -15.0, -30.0, -50.0, -74.0], "risk": "LOW"},
    {"name": "Middle East to Europe",
     "lats": [25.3, 26.6, 20.0, 12.5, 5.0, 0.0, 20.0, 51.5],
     "lons": [55.4, 56.3, 55.0, 43.5, 43.0, 40.0, -10.0, 1.0], "risk": "HIGH"},
    {"name": "Intra-Asia: China-SE Asia",
     "lats": [22.3, 15.0, 5.0, 1.3],
     "lons": [114.2, 110.0, 105.0, 103.8], "risk": "MODERATE"},
    {"name": "Trans-Pacific via Panama",
     "lats": [37.8, 35.0, 25.0, 15.0, 9.0, 8.0, 10.0, 25.0, 40.7],
     "lons": [-122.4, -130.0, -120.0, -100.0, -79.5, -79.5, -75.0, -70.0, -74.0], "risk": "MODERATE"},
]

# Country risk scores (ISO-3, numeric 0-100, higher = more risk)
_COUNTRY_RISK: list[dict] = [
    {"iso": "YEM", "name": "Yemen",          "score": 97, "level": "CRITICAL", "note": "Houthi conflict, shipping attacks"},
    {"iso": "RUS", "name": "Russia",          "score": 93, "level": "CRITICAL", "note": "Ukraine war, full Western sanctions"},
    {"iso": "UKR", "name": "Ukraine",         "score": 91, "level": "CRITICAL", "note": "Active war, Black Sea blockade"},
    {"iso": "IRN", "name": "Iran",            "score": 88, "level": "CRITICAL", "note": "OFAC sanctions, Hormuz tension"},
    {"iso": "PRK", "name": "North Korea",     "score": 86, "level": "CRITICAL", "note": "Full UN sanctions regime"},
    {"iso": "SYR", "name": "Syria",           "score": 84, "level": "CRITICAL", "note": "Conflict, comprehensive sanctions"},
    {"iso": "AFG", "name": "Afghanistan",     "score": 79, "level": "HIGH",     "note": "Taliban governance, sanctions"},
    {"iso": "SDN", "name": "Sudan",           "score": 77, "level": "HIGH",     "note": "Civil war, sanctions"},
    {"iso": "MMR", "name": "Myanmar",         "score": 74, "level": "HIGH",     "note": "Military junta, EU/US sanctions"},
    {"iso": "LBY", "name": "Libya",           "score": 72, "level": "HIGH",     "note": "Ongoing conflict, divided government"},
    {"iso": "SOM", "name": "Somalia",         "score": 71, "level": "HIGH",     "note": "Piracy hotspot, instability"},
    {"iso": "VEN", "name": "Venezuela",       "score": 68, "level": "HIGH",     "note": "US sanctions, oil sector"},
    {"iso": "CUB", "name": "Cuba",            "score": 65, "level": "HIGH",     "note": "US embargo, sanctions"},
    {"iso": "BLR", "name": "Belarus",         "score": 64, "level": "HIGH",     "note": "EU/US sanctions, Russia aligned"},
    {"iso": "NIC", "name": "Nicaragua",       "score": 58, "level": "MODERATE", "note": "Authoritarian, partial sanctions"},
    {"iso": "ETH", "name": "Ethiopia",        "score": 55, "level": "MODERATE", "note": "Tigray conflict aftermath"},
    {"iso": "PAK", "name": "Pakistan",        "score": 52, "level": "MODERATE", "note": "Political instability"},
    {"iso": "CHN", "name": "China",           "score": 50, "level": "MODERATE", "note": "Taiwan tensions, tech restrictions"},
    {"iso": "IRQ", "name": "Iraq",            "score": 49, "level": "MODERATE", "note": "Political fragility, militia activity"},
    {"iso": "GTM", "name": "Guatemala",       "score": 40, "level": "MODERATE", "note": "Governance challenges"},
    {"iso": "SAU", "name": "Saudi Arabia",    "score": 38, "level": "MODERATE", "note": "Yemen war exposure, Vision 2030"},
    {"iso": "TUR", "name": "Turkey",          "score": 35, "level": "MODERATE", "note": "Regional tensions, inflation"},
    {"iso": "IND", "name": "India",           "score": 28, "level": "LOW",      "note": "Stable; border tensions with China"},
    {"iso": "EGY", "name": "Egypt",           "score": 26, "level": "LOW",      "note": "Suez Canal operator; fiscal pressure"},
    {"iso": "USA", "name": "United States",   "score": 18, "level": "LOW",      "note": "Primary sanctions authority"},
    {"iso": "DEU", "name": "Germany",         "score": 12, "level": "LOW",      "note": "Stable; energy transition"},
    {"iso": "SGP", "name": "Singapore",       "score": 8,  "level": "LOW",      "note": "Hub port; strong governance"},
    {"iso": "AUS", "name": "Australia",       "score": 7,  "level": "LOW",      "note": "Stable; China trade tension easing"},
]

# Active conflicts
_ACTIVE_CONFLICTS: list[dict] = [
    {
        "name": "Houthi Red Sea Campaign",
        "region": "Bab-el-Mandeb / Gulf of Aden",
        "type": "Armed Conflict",
        "risk": "CRITICAL",
        "started": "Nov 2023",
        "status": "Active / Escalating",
        "shipping_impact": (
            "Major carriers (Maersk, MSC, Hapag-Lloyd, OOCL) fully rerouting via Cape of Good Hope. "
            "+14 days transit time, +$750/FEU fuel surcharge. ~90% of Asia-Europe traffic diverted."
        ),
        "freight_delta": "+38%",
        "vessels_attacked": 74,
        "affected_routes": ["Asia-Europe", "Middle East-Europe", "South Asia-Europe"],
        "lat": 12.5, "lon": 43.5,
    },
    {
        "name": "Russia-Ukraine War",
        "region": "Black Sea / Baltic Sea",
        "type": "Armed Conflict",
        "risk": "HIGH",
        "started": "Feb 2022",
        "status": "Active / Stalemated",
        "shipping_impact": (
            "Black Sea grain corridor suspended. Shadow fleet operating Danish/Baltic straits. "
            "Ukrainian port Odesa capacity at 35%. Grain, steel, fertilizer trade severely disrupted."
        ),
        "freight_delta": "+12%",
        "vessels_attacked": 18,
        "affected_routes": ["Transatlantic", "Mediterranean", "Black Sea routes"],
        "lat": 47.0, "lon": 32.0,
    },
    {
        "name": "Taiwan Strait Military Tensions",
        "region": "Taiwan Strait / South China Sea",
        "type": "Geopolitical",
        "risk": "HIGH",
        "started": "Aug 2022",
        "status": "Elevated / Periodic Exercises",
        "shipping_impact": (
            "26% of global container trade transits the Taiwan Strait. Naval exercises causing AIS "
            "blackouts and temporary routing deviations. Insurance premiums elevated by ~15%."
        ),
        "freight_delta": "+8%",
        "vessels_attacked": 0,
        "affected_routes": ["Trans-Pacific", "Intra-Asia", "Asia-Europe"],
        "lat": 24.0, "lon": 120.0,
    },
    {
        "name": "Gaza Conflict — Regional Spillover",
        "region": "Eastern Mediterranean / Levant",
        "type": "Armed Conflict",
        "risk": "HIGH",
        "started": "Oct 2023",
        "status": "Active / Ceasefire Talks",
        "shipping_impact": (
            "Israeli port Ashdod traffic down 40%. Eastern Mediterranean routing risk elevated. "
            "Houthi attacks directly linked to this conflict. Regional escalation risk remains HIGH."
        ),
        "freight_delta": "+6%",
        "vessels_attacked": 3,
        "affected_routes": ["Mediterranean", "Asia-Europe", "Middle East-Europe"],
        "lat": 31.5, "lon": 34.5,
    },
    {
        "name": "Sudan Civil War",
        "region": "Horn of Africa / Red Sea Coast",
        "type": "Armed Conflict",
        "risk": "MODERATE",
        "started": "Apr 2023",
        "status": "Active / Humanitarian Crisis",
        "shipping_impact": (
            "Port Sudan operations intermittent. Red Sea coast instability compounds Houthi risk. "
            "Grain and humanitarian aid shipping disrupted."
        ),
        "freight_delta": "+3%",
        "vessels_attacked": 1,
        "affected_routes": ["Red Sea routes", "East Africa routes"],
        "lat": 15.6, "lon": 32.5,
    },
]

# Sanctions monitor data
_SANCTIONS_DATA: list[dict] = [
    {"jurisdiction": "Russia",      "authority": "OFAC/EU/UK/UN", "vessels": 623, "entities": 4820, "coverage_pct": 94, "trade_bn": 112.4, "status": "ACTIVE",   "screening": "MANDATORY"},
    {"jurisdiction": "Iran",        "authority": "OFAC/EU/UK",    "vessels": 412, "entities": 2140, "coverage_pct": 91, "trade_bn": 68.2,  "status": "ACTIVE",   "screening": "MANDATORY"},
    {"jurisdiction": "North Korea", "authority": "OFAC/UN",       "vessels": 87,  "entities": 310,  "coverage_pct": 99, "trade_bn": 3.1,   "status": "ACTIVE",   "screening": "MANDATORY"},
    {"jurisdiction": "Venezuela",   "authority": "OFAC",          "vessels": 58,  "entities": 480,  "coverage_pct": 72, "trade_bn": 12.8,  "status": "ACTIVE",   "screening": "MANDATORY"},
    {"jurisdiction": "Syria",       "authority": "OFAC/EU/UK",    "vessels": 44,  "entities": 620,  "coverage_pct": 88, "trade_bn": 4.2,   "status": "ACTIVE",   "screening": "MANDATORY"},
    {"jurisdiction": "Belarus",     "authority": "EU/UK/US",      "vessels": 12,  "entities": 280,  "coverage_pct": 68, "trade_bn": 8.6,   "status": "ACTIVE",   "screening": "ENHANCED"},
    {"jurisdiction": "Myanmar",     "authority": "OFAC/EU/UK",    "vessels": 9,   "entities": 145,  "coverage_pct": 55, "trade_bn": 2.4,   "status": "ACTIVE",   "screening": "ENHANCED"},
    {"jurisdiction": "Cuba",        "authority": "OFAC",          "vessels": 31,  "entities": 210,  "coverage_pct": 80, "trade_bn": 1.8,   "status": "ACTIVE",   "screening": "STANDARD"},
    {"jurisdiction": "Nicaragua",   "authority": "OFAC",          "vessels": 4,   "entities": 88,   "coverage_pct": 40, "trade_bn": 0.9,   "status": "PARTIAL",  "screening": "STANDARD"},
    {"jurisdiction": "Ethiopia",    "authority": "OFAC",          "vessels": 0,   "entities": 32,   "coverage_pct": 25, "trade_bn": 0.4,   "status": "PARTIAL",  "screening": "STANDARD"},
]

# Trade lane risk scores
_LANE_RISK: list[dict] = [
    {"lane": "Asia–Europe (via Red Sea/Suez)", "score": 9.2, "delta": +2.1, "primary_risk": "Houthi attacks"},
    {"lane": "Middle East–Europe",             "score": 8.7, "delta": +1.8, "primary_risk": "Red Sea + sanctions"},
    {"lane": "Trans-Pacific (Westbound)",      "score": 7.4, "delta": +0.9, "primary_risk": "Taiwan Strait tensions"},
    {"lane": "Trans-Pacific (Eastbound)",      "score": 7.1, "delta": +0.7, "primary_risk": "Taiwan Strait tensions"},
    {"lane": "Middle East–Asia",               "score": 6.8, "delta": +0.6, "primary_risk": "Hormuz risk + Iran"},
    {"lane": "Asia–Europe (via Cape)",         "score": 5.2, "delta": -1.4, "primary_risk": "Diversion route; piracy"},
    {"lane": "Mediterranean",                  "score": 5.0, "delta": +0.4, "primary_risk": "Gaza spillover"},
    {"lane": "Black Sea",                      "score": 4.9, "delta": +0.2, "primary_risk": "Russia-Ukraine"},
    {"lane": "Trans-Pacific via Panama",       "score": 4.3, "delta": -0.2, "primary_risk": "Panama drought"},
    {"lane": "South America–Europe",           "score": 3.8, "delta": +0.1, "primary_risk": "Low direct exposure"},
    {"lane": "Intra-Asia (SE Asia)",           "score": 3.5, "delta": +0.3, "primary_risk": "SCS tensions, minor"},
    {"lane": "Transatlantic",                  "score": 2.9, "delta": -0.1, "primary_risk": "Minimal geopolitical"},
    {"lane": "Australia–Asia",                 "score": 2.4, "delta":  0.0, "primary_risk": "Stable"},
    {"lane": "Intra-Europe",                   "score": 2.1, "delta": +0.1, "primary_risk": "Minimal"},
]

# Escalation timeline (monthly global risk index, 0-10)
_ESCALATION_TIMELINE: list[dict] = [
    {"date": "2021-01", "score": 3.2}, {"date": "2021-03", "score": 3.8, "event": "Ever Given Suez Grounding"},
    {"date": "2021-06", "score": 3.4}, {"date": "2021-09", "score": 4.1, "event": "Container Rate Peak"},
    {"date": "2021-12", "score": 3.9},
    {"date": "2022-02", "score": 6.8, "event": "Russia Invades Ukraine"},
    {"date": "2022-04", "score": 6.5}, {"date": "2022-06", "score": 6.9, "event": "Shanghai COVID Lockdown"},
    {"date": "2022-08", "score": 6.2}, {"date": "2022-10", "score": 5.8, "event": "Black Sea Grain Deal"},
    {"date": "2022-12", "score": 5.5},
    {"date": "2023-02", "score": 5.3}, {"date": "2023-05", "score": 5.6, "event": "Panama Drought Warning"},
    {"date": "2023-08", "score": 5.4}, {"date": "2023-10", "score": 6.7, "event": "Gaza Conflict Begins"},
    {"date": "2023-11", "score": 7.4, "event": "Houthi Campaign Starts"},
    {"date": "2024-01", "score": 8.6, "event": "Carriers Exit Red Sea"},
    {"date": "2024-03", "score": 8.2, "event": "Baltimore Bridge Collapse"},
    {"date": "2024-06", "score": 7.9, "event": "Taiwan Military Exercises"},
    {"date": "2024-08", "score": 8.8, "event": "Red Sea Rate Spike +200%"},
    {"date": "2024-10", "score": 8.3}, {"date": "2024-12", "score": 7.8},
    {"date": "2025-01", "score": 7.6, "event": "2M Alliance Dissolves"},
    {"date": "2025-03", "score": 7.9, "event": "Iran Sanctions Tightened"},
    {"date": "2025-06", "score": 7.4, "event": "SCS Standoff"},
    {"date": "2025-09", "score": 7.1}, {"date": "2025-12", "score": 6.9},
    {"date": "2026-01", "score": 7.0}, {"date": "2026-02", "score": 6.7, "event": "Houthi Ceasefire Talks"},
    {"date": "2026-03", "score": 7.2},
]

# Supply chain vulnerability — political stability score (0=unstable, 100=stable)
_SOURCING_COUNTRIES: list[dict] = [
    {"country": "China",         "iso": "CHN", "stability": 52, "key_exports": "Electronics, Machinery, Textiles",   "trade_share_pct": 31.2},
    {"country": "USA",           "iso": "USA", "stability": 82, "key_exports": "Energy, Agriculture, Tech",           "trade_share_pct": 14.8},
    {"country": "Germany",       "iso": "DEU", "stability": 88, "key_exports": "Machinery, Vehicles, Chemicals",      "trade_share_pct": 8.4},
    {"country": "Japan",         "iso": "JPN", "stability": 85, "key_exports": "Electronics, Vehicles, Machinery",    "trade_share_pct": 6.2},
    {"country": "South Korea",   "iso": "KOR", "stability": 80, "key_exports": "Semiconductors, Ships, Electronics",  "trade_share_pct": 5.7},
    {"country": "Taiwan",        "iso": "TWN", "stability": 70, "key_exports": "Semiconductors, Electronics",         "trade_share_pct": 5.1},
    {"country": "India",         "iso": "IND", "stability": 72, "key_exports": "Pharma, Textiles, IT Services",       "trade_share_pct": 4.8},
    {"country": "Vietnam",       "iso": "VNM", "stability": 68, "key_exports": "Electronics, Footwear, Garments",     "trade_share_pct": 3.9},
    {"country": "Brazil",        "iso": "BRA", "stability": 62, "key_exports": "Soybeans, Iron Ore, Oil",             "trade_share_pct": 3.2},
    {"country": "Saudi Arabia",  "iso": "SAU", "stability": 62, "key_exports": "Crude Oil, Petrochemicals",           "trade_share_pct": 2.8},
    {"country": "Russia",        "iso": "RUS", "stability": 28, "key_exports": "Energy, Metals, Grain (sanctioned)",  "trade_share_pct": 2.1},
    {"country": "Iran",          "iso": "IRN", "stability": 22, "key_exports": "Oil (sanctioned), Petrochemicals",    "trade_share_pct": 0.4},
]

# Piracy incidents (lat, lon, severity, date, description)
_PIRACY_INCIDENTS: list[dict] = [
    {"lat": 12.5,  "lon": 43.5,  "severity": "HIGH",     "month": "2026-02", "type": "Missile Attack",  "vessel": "Bulk carrier, crew safe"},
    {"lat": 11.0,  "lon": 45.0,  "severity": "HIGH",     "month": "2026-02", "type": "Drone Attack",    "vessel": "Container ship, minor damage"},
    {"lat": 13.0,  "lon": 42.5,  "severity": "CRITICAL", "month": "2026-01", "type": "Hijack Attempt",  "vessel": "Oil tanker, repelled by navy"},
    {"lat": 10.5,  "lon": 44.5,  "severity": "HIGH",     "month": "2026-01", "type": "Missile Strike",  "vessel": "Bulk carrier, 2 crew injured"},
    {"lat": 11.8,  "lon": 43.8,  "severity": "HIGH",     "month": "2025-12", "type": "Boat Boarding",   "vessel": "Chemical tanker, cargo looted"},
    {"lat": 3.5,   "lon": 7.0,   "severity": "MODERATE", "month": "2026-01", "type": "Armed Robbery",   "vessel": "Product tanker, Gulf of Guinea"},
    {"lat": 2.0,   "lon": 6.5,   "severity": "MODERATE", "month": "2025-12", "type": "Piracy Attack",   "vessel": "Bulk carrier, GoG"},
    {"lat": 4.0,   "lon": 7.5,   "severity": "LOW",      "month": "2025-11", "type": "Suspicious Approach", "vessel": "Container ship, no boarding"},
    {"lat": 1.5,   "lon": 103.8, "severity": "LOW",      "month": "2026-02", "type": "Armed Robbery",   "vessel": "Anchored tanker, Malacca"},
    {"lat": 14.5,  "lon": 42.0,  "severity": "HIGH",     "month": "2026-03", "type": "Missile Attack",  "vessel": "Ro-Ro vessel, fire damage"},
    {"lat": 12.0,  "lon": 44.0,  "severity": "CRITICAL", "month": "2026-03", "type": "Drone Swarm",     "vessel": "LNG carrier, diverted"},
    {"lat": 11.5,  "lon": 43.2,  "severity": "HIGH",     "month": "2025-11", "type": "Mine Threat",     "vessel": "Container ship, rerouted"},
]

_PIRACY_MONTHLY: list[dict] = [
    {"month": "Oct 2025", "incidents": 12}, {"month": "Nov 2025", "incidents": 18},
    {"month": "Dec 2025", "incidents": 21}, {"month": "Jan 2026", "incidents": 27},
    {"month": "Feb 2026", "incidents": 31}, {"month": "Mar 2026", "incidents": 34},
]

# Diplomatic tension matrix (0=no tension, 10=hostile)
_DIPLO_PARTIES = ["USA", "China", "EU", "Russia", "India", "Saudi Arabia", "Iran", "Turkey"]
_DIPLO_MATRIX = [
    # USA  CHN   EU   RUS  IND  SAU  IRN  TUR
    [  0,   7.8,  1.2,  9.5,  3.2,  4.5,  9.2,  5.1],  # USA
    [  7.8,  0,   6.5,  2.1,  4.8,  3.8,  1.8,  3.2],  # CHN
    [  1.2, 6.5,   0,   9.2,  2.5,  3.1,  7.8,  4.8],  # EU
    [  9.5, 2.1,  9.2,   0,   4.2,  5.2,  2.0,  4.5],  # RUS
    [  3.2, 4.8,  2.5,  4.2,   0,   2.8,  5.5,  3.8],  # IND
    [  4.5, 3.8,  3.1,  5.2,  2.8,   0,   6.8,  4.2],  # SAU
    [  9.2, 1.8,  7.8,  2.0,  5.5,  6.8,   0,   3.5],  # IRN
    [  5.1, 3.2,  4.8,  4.5,  3.8,  4.2,  3.5,   0 ],  # TUR
]

# Scenario analysis
_SCENARIOS: list[dict] = [
    {
        "name": "Bear Case",
        "subtitle": "Full Regional Escalation",
        "icon": "↓",
        "color": C_DANGER,
        "probability": 18,
        "triggers": [
            "Iran enters Red Sea conflict directly",
            "Taiwan Strait blockade by PLAN",
            "Russian Baltic Sea mining",
            "Panama Canal extended closure",
        ],
        "rate_impact": "+65–90%",
        "volume_impact": "-18%",
        "transit_days": "+21 days (Asia-EU)",
        "fuel_cost": "+$1,200/FEU",
        "description": (
            "Full multi-theatre escalation triggers simultaneous closure of Red Sea, Strait of Hormuz, "
            "and Taiwan Strait. Global container capacity drops 22%. SCFI could hit 8,000+ pts. "
            "Energy tanker rates spike 3x. Insurance war risk premiums make certain routes uneconomical."
        ),
        "freight_corridors": {"Asia-Europe": "+90%", "Trans-Pacific": "+55%", "Transatlantic": "+30%"},
    },
    {
        "name": "Base Case",
        "subtitle": "Current Elevated Risk Persists",
        "icon": "→",
        "color": C_WARN,
        "probability": 58,
        "triggers": [
            "Houthi attacks continue at current rate",
            "Taiwan tensions periodic but no blockade",
            "Russia-Ukraine war remains stalemated",
            "Partial Panama Canal restrictions",
        ],
        "rate_impact": "+25–40%",
        "volume_impact": "-6%",
        "transit_days": "+14 days (Asia-EU)",
        "fuel_cost": "+$750/FEU",
        "description": (
            "Red Sea diversion persists through H2 2026. Carriers maintain Cape routing. "
            "SCFI stabilizes in 2,800–3,400 range. Trans-Pacific holds with moderate Taiwan premium. "
            "Supply chains adapt with buffer inventory. Multi-year carrier capacity absorption underway."
        ),
        "freight_corridors": {"Asia-Europe": "+35%", "Trans-Pacific": "+18%", "Transatlantic": "+8%"},
    },
    {
        "name": "Bull Case",
        "subtitle": "De-escalation & Normalization",
        "icon": "↑",
        "color": C_HIGH,
        "probability": 24,
        "triggers": [
            "Houthi ceasefire agreement reached",
            "US-Iran nuclear deal resumes",
            "Taiwan tensions managed diplomatically",
            "Panama Canal rainfall normalizes",
        ],
        "rate_impact": "-15–25%",
        "volume_impact": "+4%",
        "transit_days": "-10 days (Asia-EU)",
        "fuel_cost": "-$400/FEU",
        "description": (
            "Red Sea reopens to commercial traffic by Q3 2026. SCFI drops sharply to 1,400–1,800 "
            "as vessels return to Suez routing and capacity floods market. Spot rates compress; "
            "shippers benefit. Alliance restructuring adds additional efficiency. Positive volume "
            "rebound in H2 2026 as supply chains normalise and inventory restocking begins."
        ),
        "freight_corridors": {"Asia-Europe": "-22%", "Trans-Pacific": "-10%", "Transatlantic": "-5%"},
    },
]

# ============================================================================
# Section 0: Hero Dashboard
# ============================================================================

def _render_hero_dashboard() -> None:
    """Render geopolitical risk hero dashboard with gauge, KPI cards."""
    logger.debug("Rendering geopolitical hero dashboard")
    try:
        st.markdown(_PULSE_CSS, unsafe_allow_html=True)

        # Compute summary stats
        critical_count = sum(1 for e in _ACTIVE_CONFLICTS if e["risk"] == "CRITICAL")
        high_count     = sum(1 for e in _ACTIVE_CONFLICTS if e["risk"] == "HIGH")
        total_conflicts = len(_ACTIVE_CONFLICTS)
        total_vessels_sanctioned = sum(s["vessels"] for s in _SANCTIONS_DATA)
        active_sanctions = sum(1 for s in _SANCTIONS_DATA if s["status"] == "ACTIVE")
        trade_at_risk = sum(s["trade_bn"] for s in _SANCTIONS_DATA)

        # Overall risk index (0-10 scale, derived from escalation timeline latest)
        global_risk_score = _ESCALATION_TIMELINE[-1]["score"]

        # Risk gauge
        gauge_color = (
            C_DANGER  if global_risk_score >= 8.0 else
            C_ORANGE  if global_risk_score >= 6.5 else
            C_WARN    if global_risk_score >= 4.5 else
            C_HIGH
        )
        risk_label = (
            "CRITICAL" if global_risk_score >= 8.0 else
            "HIGH"     if global_risk_score >= 6.5 else
            "MODERATE" if global_risk_score >= 4.5 else
            "LOW"
        )

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=global_risk_score,
            number={"font": {"size": 42, "color": gauge_color, "family": "Inter, sans-serif"}, "suffix": "/10"},
            title={"text": f"<b>Global Geopolitical Risk Index</b><br><span style='font-size:0.8em;color:{gauge_color}'>{risk_label}</span>",
                   "font": {"size": 14, "color": C_TEXT2}},
            gauge={
                "axis": {"range": [0, 10], "tickwidth": 1, "tickcolor": C_TEXT3,
                         "tickfont": {"size": 10, "color": C_TEXT3}},
                "bar": {"color": gauge_color, "thickness": 0.3},
                "bgcolor": "rgba(0,0,0,0)",
                "borderwidth": 0,
                "steps": [
                    {"range": [0, 4.5],  "color": "rgba(16,185,129,0.12)"},
                    {"range": [4.5, 6.5], "color": "rgba(245,158,11,0.12)"},
                    {"range": [6.5, 8.0], "color": "rgba(249,115,22,0.14)"},
                    {"range": [8.0, 10],  "color": "rgba(239,68,68,0.16)"},
                ],
                "threshold": {"line": {"color": gauge_color, "width": 3}, "thickness": 0.8, "value": global_risk_score},
            },
        ))
        fig_gauge.update_layout(
            height=260,
            margin=dict(l=30, r=30, t=40, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            font={"color": C_TEXT},
        )

        # Layout: gauge left, KPI cards right
        col_gauge, col_kpis = st.columns([1, 1.6])
        with col_gauge:
            st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar": False})

        with col_kpis:
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            r1c1, r1c2 = st.columns(2)
            r2c1, r2c2 = st.columns(2)
            with r1c1:
                st.markdown(
                    _kpi_card(str(total_conflicts), "Active Conflicts", C_DANGER,
                              f"{critical_count} CRITICAL · {high_count} HIGH"),
                    unsafe_allow_html=True,
                )
            with r1c2:
                st.markdown(
                    _kpi_card(f"{total_vessels_sanctioned:,}", "Sanctioned Vessels", C_ORANGE,
                              f"{active_sanctions} active regimes"),
                    unsafe_allow_html=True,
                )
            with r2c1:
                st.markdown(
                    _kpi_card(f"${trade_at_risk:.0f}B", "Trade Value at Risk", C_WARN,
                              "Sanctions-exposed trade"),
                    unsafe_allow_html=True,
                )
            with r2c2:
                st.markdown(
                    _kpi_card("+38%", "Max Route Rate Impact", C_PURPLE,
                              "Asia-EU via Red Sea"),
                    unsafe_allow_html=True,
                )

        # Alert banner
        pulse_anim = "animation:geo-pulse 2s ease-in-out infinite;"
        st.markdown(
            f'<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.35);'
            f'border-radius:12px;padding:14px 20px;margin-top:4px;{pulse_anim}'
            f'display:flex;align-items:center;gap:14px">'
            f'<span style="font-size:1.4rem">⚠</span>'
            f'<div>'
            f'<div style="font-size:0.85rem;font-weight:700;color:{C_DANGER}">ELEVATED GLOBAL SHIPPING RISK — LEVEL {risk_label}</div>'
            f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-top:3px">'
            f'Bab-el-Mandeb CRITICAL · Taiwan Strait HIGH · Black Sea HIGH · '
            f'Global risk index {global_risk_score:.1f}/10 as of Mar 2026'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning("Hero dashboard render error: {}", exc)
        st.warning("Hero dashboard unavailable.")


# ============================================================================
# Section 1: Risk Heatmap Choropleth
# ============================================================================

def _render_choropleth_map() -> None:
    """Choropleth world map with country risk scores."""
    logger.debug("Rendering risk choropleth map")
    try:
        isos    = [c["iso"]   for c in _COUNTRY_RISK]
        scores  = [c["score"] for c in _COUNTRY_RISK]
        names   = [c["name"]  for c in _COUNTRY_RISK]
        levels  = [c["level"] for c in _COUNTRY_RISK]
        notes   = [c["note"]  for c in _COUNTRY_RISK]

        hover = [
            f"<b>{n}</b><br>Risk Level: <b>{lv}</b><br>Risk Score: {sc}/100<br>{nt}"
            for n, lv, sc, nt in zip(names, levels, scores, notes)
        ]

        fig = go.Figure(go.Choropleth(
            locations=isos,
            z=scores,
            text=hover,
            hovertemplate="%{text}<extra></extra>",
            colorscale=[
                [0.0,  "rgba(16,185,129,0.25)"],
                [0.35, "rgba(245,158,11,0.35)"],
                [0.65, "rgba(249,115,22,0.50)"],
                [0.85, "rgba(239,68,68,0.65)"],
                [1.0,  "rgba(239,68,68,0.90)"],
            ],
            zmin=0, zmax=100,
            showscale=True,
            colorbar=dict(
                title=dict(text="Risk Score", font=dict(size=11, color=C_TEXT2)),
                tickfont=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(26,34,53,0.85)",
                bordercolor=C_BORDER,
                borderwidth=1,
                len=0.7, thickness=14,
                x=1.01,
            ),
            marker=dict(line=dict(color="rgba(255,255,255,0.10)", width=0.5)),
        ))

        # Add chokepoint scatter overlay
        cp_colors = [_LEVEL_COLOR.get(c["risk"], C_TEXT2) for c in _CHOKEPOINTS]
        cp_sizes  = [16 if c["risk"] == "CRITICAL" else 12 if c["risk"] == "HIGH" else 9 for c in _CHOKEPOINTS]
        fig.add_trace(go.Scattergeo(
            lat=[c["lat"] for c in _CHOKEPOINTS],
            lon=[c["lon"] for c in _CHOKEPOINTS],
            mode="markers",
            marker=dict(size=cp_sizes, color=cp_colors, opacity=0.92,
                        line=dict(color="white", width=1.2), symbol="circle"),
            hovertemplate=[
                f"<b>{c['name']}</b><br>Risk: {c['risk']}<br>{c['note']}<extra></extra>"
                for c in _CHOKEPOINTS
            ],
            showlegend=False,
            name="Chokepoints",
        ))

        fig.update_geos(
            projection_type="natural earth",
            showland=True, landcolor="#1e293b",
            showocean=True, oceancolor="#0f172a",
            showlakes=False,
            showcountries=True, countrycolor="rgba(255,255,255,0.08)",
            showframe=False,
            bgcolor="rgba(0,0,0,0)",
            lataxis_range=[-60, 85],
        )
        fig.update_layout(
            height=440,
            margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            geo_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        # Legend row
        legend_html = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:6px">'
        for level, color in _LEVEL_COLOR.items():
            legend_html += (
                f'<div style="display:flex;align-items:center;gap:5px">'
                f'<div style="width:10px;height:10px;border-radius:50%;background:{color}"></div>'
                f'<span style="font-size:0.71rem;color:{C_TEXT2}">{level}</span></div>'
            )
        legend_html += (
            '<div style="display:flex;align-items:center;gap:5px">'
            f'<div style="width:10px;height:10px;border-radius:50%;background:white;border:1px solid #64748b"></div>'
            f'<span style="font-size:0.71rem;color:{C_TEXT2}">Chokepoint</span></div></div>'
        )
        st.markdown(legend_html, unsafe_allow_html=True)

    except Exception as exc:
        logger.warning("Choropleth render error: {}", exc)
        st.warning("Risk heatmap unavailable.")


# ============================================================================
# Section 2: Active Conflict Tracker
# ============================================================================

def _render_active_conflict_tracker() -> None:
    """Cards for each active conflict with details."""
    logger.debug("Rendering active conflict tracker")
    try:
        order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
        sorted_conflicts = sorted(_ACTIVE_CONFLICTS, key=lambda x: order.get(x["risk"], 9))

        for i in range(0, len(sorted_conflicts), 2):
            cols = st.columns(2)
            for j, conflict in enumerate(sorted_conflicts[i:i+2]):
                with cols[j]:
                    color = _LEVEL_COLOR.get(conflict["risk"], C_TEXT2)
                    is_critical = conflict["risk"] == "CRITICAL"
                    pulse = "animation:card-glow 2.5s ease-in-out infinite;" if is_critical else ""
                    badge = _risk_badge(conflict["risk"], pulse=is_critical)
                    route_pills = "".join(_pill(r) for r in conflict["affected_routes"])
                    type_pill = _pill(conflict["type"], color=C_PURPLE)

                    # Vessels attacked indicator
                    attacked = conflict.get("vessels_attacked", 0)
                    attacked_html = (
                        f'<span style="color:{C_DANGER};font-weight:700">{attacked}</span>'
                        f'<span style="color:{C_TEXT3};font-size:0.70rem"> vessels attacked</span>'
                        if attacked > 0 else
                        f'<span style="color:{C_TEXT3};font-size:0.70rem">No vessel incidents</span>'
                    )

                    card_html = (
                        f'<div style="background:{C_CARD};border:1px solid {color}33;'
                        f'border-radius:14px;padding:18px 20px;margin-bottom:14px;{pulse}">'
                        # Header
                        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;flex-wrap:wrap;gap:6px">'
                        f'<div>{badge} {type_pill}</div>'
                        f'<span style="font-size:0.68rem;color:{C_TEXT3};white-space:nowrap">{conflict["started"]}</span>'
                        f'</div>'
                        # Title
                        f'<div style="font-size:1.0rem;font-weight:700;color:{C_TEXT};margin-bottom:3px">{conflict["name"]}</div>'
                        f'<div style="font-size:0.75rem;color:{C_TEXT3};margin-bottom:10px">{conflict["region"]}</div>'
                        # Status
                        f'<div style="background:rgba(0,0,0,0.25);border-radius:8px;padding:8px 12px;margin-bottom:10px">'
                        f'<span style="font-size:0.67rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em">Status</span><br>'
                        f'<span style="font-size:0.80rem;color:{color};font-weight:600">{conflict["status"]}</span>'
                        f'</div>'
                        # Shipping impact
                        f'<div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.5;margin-bottom:10px">'
                        f'{conflict["shipping_impact"]}</div>'
                        # Metrics row
                        f'<div style="display:flex;gap:14px;margin-bottom:10px">'
                        f'<div style="flex:1;background:rgba(0,0,0,0.2);border-radius:8px;padding:8px;text-align:center">'
                        f'<div style="font-size:1.1rem;font-weight:800;color:{color}">{conflict["freight_delta"]}</div>'
                        f'<div style="font-size:0.65rem;color:{C_TEXT3}">Rate Impact</div></div>'
                        f'<div style="flex:1;background:rgba(0,0,0,0.2);border-radius:8px;padding:8px;text-align:center">'
                        f'<div style="font-size:0.82rem;font-weight:600">{attacked_html}</div></div>'
                        f'</div>'
                        # Routes
                        f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;margin-bottom:5px">Affected Routes</div>'
                        + route_pills
                        + "</div>"
                    )
                    st.markdown(card_html, unsafe_allow_html=True)

    except Exception as exc:
        logger.warning("Active conflict tracker render error: {}", exc)
        st.warning("Active conflict tracker unavailable.")


# ============================================================================
# Section 3: Sanctions Monitor
# ============================================================================

def _render_sanctions_monitor() -> None:
    """Jurisdiction tracker with coverage, vessels, and vessel screening status."""
    logger.debug("Rendering sanctions monitor")
    try:
        total_vessels   = sum(s["vessels"]   for s in _SANCTIONS_DATA)
        total_entities  = sum(s["entities"]  for s in _SANCTIONS_DATA)
        total_trade_bn  = sum(s["trade_bn"]  for s in _SANCTIONS_DATA)
        active_count    = sum(1 for s in _SANCTIONS_DATA if s["status"] == "ACTIVE")

        # KPI row
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(_kpi_card(f"{total_vessels:,}", "Sanctioned Vessels", C_DANGER, "Global fleet exposure"), unsafe_allow_html=True)
        with k2:
            st.markdown(_kpi_card(f"{total_entities:,}", "Sanctioned Entities", C_ORANGE, "Persons, ships & corps"), unsafe_allow_html=True)
        with k3:
            st.markdown(_kpi_card(f"${total_trade_bn:.0f}B", "Trade at Risk", C_WARN, "Annual exposure"), unsafe_allow_html=True)
        with k4:
            st.markdown(_kpi_card(str(active_count), "Active Regimes", C_PURPLE, f"of {len(_SANCTIONS_DATA)} tracked"), unsafe_allow_html=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # Coverage bars
        _section_title("Sanctions Coverage by Jurisdiction", "Coverage = % of sanctioned entities with confirmed vessel links screened")
        sorted_sanc = sorted(_SANCTIONS_DATA, key=lambda x: -x["coverage_pct"])
        bar_cols = st.columns(2)
        for i, s in enumerate(sorted_sanc):
            with bar_cols[i % 2]:
                col = _LEVEL_COLOR.get("CRITICAL" if s["coverage_pct"] >= 85 else "HIGH" if s["coverage_pct"] >= 65 else "MODERATE", C_WARN)
                screening_color = C_DANGER if s["screening"] == "MANDATORY" else C_WARN if s["screening"] == "ENHANCED" else C_TEXT3
                st.markdown(
                    f'<div style="background:{C_CARD2};border:1px solid {C_BORDER};border-radius:10px;padding:12px 16px;margin-bottom:10px">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                    f'<span style="font-size:0.83rem;font-weight:700;color:{C_TEXT}">{s["jurisdiction"]}</span>'
                    f'<div style="display:flex;gap:8px;align-items:center">'
                    f'<span style="font-size:0.65rem;color:{screening_color};border:1px solid {screening_color}44;padding:1px 7px;border-radius:999px">{s["screening"]}</span>'
                    f'<span style="font-size:0.75rem;font-weight:700;color:{col}">{s["coverage_pct"]}%</span>'
                    f'</div></div>'
                    f'<div style="background:rgba(255,255,255,0.07);border-radius:4px;height:6px">'
                    f'<div style="width:{s["coverage_pct"]}%;background:{col};border-radius:4px;height:6px"></div></div>'
                    f'<div style="display:flex;gap:14px;margin-top:7px">'
                    f'<span style="font-size:0.68rem;color:{C_TEXT3}">{s["vessels"]} vessels · {s["entities"]} entities · ${s["trade_bn"]}B trade</span>'
                    f'<span style="font-size:0.68rem;color:{C_TEXT3};margin-left:auto">{s["authority"]}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

    except Exception as exc:
        logger.warning("Sanctions monitor render error: {}", exc)
        st.warning("Sanctions monitor unavailable.")


# ============================================================================
# Section 4: Political Risk by Trade Lane
# ============================================================================

def _render_lane_risk_chart() -> None:
    """Horizontal bar chart of geopolitical risk score per trade corridor."""
    logger.debug("Rendering lane risk chart")
    try:
        sorted_lanes = sorted(_LANE_RISK, key=lambda x: x["score"])
        names   = [l["lane"]         for l in sorted_lanes]
        scores  = [l["score"]        for l in sorted_lanes]
        deltas  = [l["delta"]        for l in sorted_lanes]
        primary = [l["primary_risk"] for l in sorted_lanes]

        bar_colors = [
            C_DANGER  if s >= 8.5 else
            C_ORANGE  if s >= 7.0 else
            C_WARN    if s >= 5.0 else
            C_ACCENT  if s >= 3.5 else
            C_HIGH
            for s in scores
        ]

        hover = [
            f"<b>{n}</b><br>Risk Score: {s:.1f}/10<br>Primary Risk: {p}<br>QoQ Change: {'+' if d >= 0 else ''}{d:.1f}"
            for n, s, p, d in zip(names, scores, primary, deltas)
        ]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=scores, y=names,
            orientation="h",
            marker_color=bar_colors,
            marker_line_width=0,
            opacity=0.88,
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover,
        ))

        # Delta annotations
        for i, (s, d) in enumerate(zip(scores, deltas)):
            delta_str = f"+{d:.1f}" if d > 0 else f"{d:.1f}"
            delta_color = C_DANGER if d > 0 else C_HIGH if d < 0 else C_TEXT3
            fig.add_annotation(
                x=s + 0.15, y=i,
                text=f"<b>{delta_str}</b>",
                showarrow=False,
                font=dict(size=10, color=delta_color),
                xanchor="left",
            )

        fig.add_vline(x=7.0, line_dash="dot", line_color=C_ORANGE, line_width=1.5,
                      annotation_text="HIGH threshold", annotation_font_size=10,
                      annotation_font_color=C_ORANGE, annotation_position="top right")
        fig.add_vline(x=8.5, line_dash="dot", line_color=C_DANGER, line_width=1.5,
                      annotation_text="CRITICAL", annotation_font_size=10,
                      annotation_font_color=C_DANGER, annotation_position="top left")

        fig.update_layout(
            height=420,
            margin=dict(l=0, r=60, t=20, b=40),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(range=[0, 11], gridcolor="rgba(255,255,255,0.06)", color=C_TEXT2,
                       title="Geopolitical Risk Score (0–10)", title_font_size=11),
            yaxis=dict(color=C_TEXT2, tickfont=dict(size=11)),
            font=dict(color=C_TEXT),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    except Exception as exc:
        logger.warning("Lane risk chart render error: {}", exc)
        st.warning("Lane risk chart unavailable.")


# ============================================================================
# Section 5: Conflict Escalation Timeline
# ============================================================================

def _render_escalation_timeline() -> None:
    """Historical risk score line chart with conflict event annotations."""
    logger.debug("Rendering escalation timeline")
    try:
        dates  = [e["date"]  for e in _ESCALATION_TIMELINE]
        scores = [e["score"] for e in _ESCALATION_TIMELINE]

        # Color gradient per score
        line_colors = [
            C_DANGER  if s >= 8.0 else
            C_ORANGE  if s >= 6.5 else
            C_WARN    if s >= 4.5 else
            C_HIGH
            for s in scores
        ]

        fig = go.Figure()

        # Shaded risk zones
        fig.add_hrect(y0=8.0, y1=10.5, fillcolor=f"{C_DANGER}14", line_width=0)
        fig.add_hrect(y0=6.5, y1=8.0,  fillcolor=f"{C_ORANGE}10", line_width=0)
        fig.add_hrect(y0=4.5, y1=6.5,  fillcolor=f"{C_WARN}0C",   line_width=0)
        fig.add_hrect(y0=0,   y1=4.5,  fillcolor=f"{C_HIGH}08",   line_width=0)

        # Area fill
        fig.add_trace(go.Scatter(
            x=dates, y=scores,
            fill="tozeroy", fillcolor="rgba(59,130,246,0.07)",
            line=dict(color=C_ACCENT, width=2.5),
            mode="lines",
            hovertemplate="<b>%{x}</b><br>Risk Score: %{y:.1f}/10<extra></extra>",
            showlegend=False,
        ))

        # Colored dots for each data point
        fig.add_trace(go.Scatter(
            x=dates, y=scores,
            mode="markers",
            marker=dict(size=6, color=line_colors, line=dict(color="rgba(0,0,0,0.4)", width=1)),
            hoverinfo="skip",
            showlegend=False,
        ))

        # Event annotations
        for e in _ESCALATION_TIMELINE:
            if "event" in e:
                score = e["score"]
                color = (C_DANGER if score >= 8.0 else C_ORANGE if score >= 6.5 else C_WARN if score >= 4.5 else C_HIGH)
                fig.add_annotation(
                    x=e["date"], y=score,
                    text=f"<b>{e['event']}</b>",
                    showarrow=True, arrowhead=2, arrowcolor=color,
                    arrowwidth=1.5, arrowsize=0.8,
                    ax=0, ay=-38,
                    font=dict(size=8.5, color=color),
                    bgcolor="rgba(10,15,26,0.82)",
                    bordercolor=color + "66",
                    borderwidth=1,
                    borderpad=3,
                )

        # Current date line
        fig.add_vline(x="2026-03", line_dash="dash", line_color=C_CYAN, line_width=1.5,
                      annotation_text="NOW", annotation_font_color=C_CYAN, annotation_font_size=10)

        # Zone labels (right axis annotations)
        for y_mid, label, color in [
            (9.0, "CRITICAL", C_DANGER), (7.25, "HIGH", C_ORANGE),
            (5.5, "MODERATE", C_WARN),  (2.25, "LOW", C_HIGH),
        ]:
            fig.add_annotation(x="2026-03", y=y_mid, text=label, showarrow=False,
                                font=dict(size=9, color=color), xanchor="right", opacity=0.6)

        fig.update_layout(
            height=420,
            margin=dict(l=10, r=20, t=20, b=40),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="rgba(255,255,255,0.05)", color=C_TEXT2, tickangle=-35,
                       tickfont=dict(size=9)),
            yaxis=dict(range=[0, 10.5], gridcolor="rgba(255,255,255,0.05)", color=C_TEXT2,
                       title="Global Risk Index", title_font_size=11),
            font=dict(color=C_TEXT),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    except Exception as exc:
        logger.warning("Escalation timeline render error: {}", exc)
        st.warning("Escalation timeline unavailable.")


# ============================================================================
# Section 6: Supply Chain Vulnerability Map
# ============================================================================

def _render_supply_chain_vulnerability() -> None:
    """Key sourcing countries ranked by political stability score."""
    logger.debug("Rendering supply chain vulnerability map")
    try:
        sorted_sc = sorted(_SOURCING_COUNTRIES, key=lambda x: x["stability"])

        fig = go.Figure()
        colors = [
            C_DANGER  if c["stability"] < 35 else
            C_ORANGE  if c["stability"] < 55 else
            C_WARN    if c["stability"] < 70 else
            C_HIGH
            for c in sorted_sc
        ]
        hover = [
            f"<b>{c['country']}</b><br>Stability Score: {c['stability']}/100<br>"
            f"Key Exports: {c['key_exports']}<br>Global Trade Share: {c['trade_share_pct']}%"
            for c in sorted_sc
        ]

        fig.add_trace(go.Bar(
            x=[c["stability"] for c in sorted_sc],
            y=[c["country"]   for c in sorted_sc],
            orientation="h",
            marker_color=colors,
            marker_line_width=0,
            opacity=0.87,
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover,
        ))

        # Trade share as bubble text overlay
        for i, c in enumerate(sorted_sc):
            fig.add_annotation(
                x=c["stability"] + 1.5, y=i,
                text=f"{c['trade_share_pct']}% share",
                showarrow=False,
                font=dict(size=9, color=C_TEXT3),
                xanchor="left",
            )

        fig.add_vline(x=50, line_dash="dot", line_color=C_WARN, line_width=1.2,
                      annotation_text="Stability threshold", annotation_font_size=9,
                      annotation_font_color=C_WARN)

        fig.update_layout(
            height=380,
            margin=dict(l=0, r=80, t=20, b=40),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(range=[0, 110], gridcolor="rgba(255,255,255,0.06)", color=C_TEXT2,
                       title="Political Stability Score (0=unstable · 100=very stable)",
                       title_font_size=11),
            yaxis=dict(color=C_TEXT2, tickfont=dict(size=11)),
            font=dict(color=C_TEXT),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        # Vulnerability call-outs
        high_risk_sources = [c for c in _SOURCING_COUNTRIES if c["stability"] < 50]
        if high_risk_sources:
            st.markdown(
                f'<div style="background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.25);'
                f'border-radius:10px;padding:12px 16px;margin-top:4px">'
                f'<div style="font-size:0.78rem;font-weight:700;color:{C_DANGER};margin-bottom:5px">'
                f'High-Vulnerability Sourcing Countries ({len(high_risk_sources)})</div>'
                f'<div style="font-size:0.74rem;color:{C_TEXT2}">'
                + " · ".join(f'<b style="color:{C_TEXT}">{c["country"]}</b> ({c["stability"]}/100)' for c in high_risk_sources)
                + f'</div><div style="font-size:0.70rem;color:{C_TEXT3};margin-top:5px">'
                f'These sourcing nations have elevated political instability that may disrupt supply chain continuity. '
                f'Combined trade share: {sum(c["trade_share_pct"] for c in high_risk_sources):.1f}%</div></div>',
                unsafe_allow_html=True,
            )

    except Exception as exc:
        logger.warning("Supply chain vulnerability render error: {}", exc)
        st.warning("Supply chain vulnerability map unavailable.")


# ============================================================================
# Section 7: Piracy & Security Risk Tracker
# ============================================================================

def _render_piracy_tracker() -> None:
    """Incident map with monthly frequency chart."""
    logger.debug("Rendering piracy tracker")
    try:
        col_map, col_chart = st.columns([1.5, 1])

        with col_map:
            inc_colors = [_LEVEL_COLOR.get(i["severity"], C_TEXT2) for i in _PIRACY_INCIDENTS]
            inc_sizes  = [18 if i["severity"] == "CRITICAL" else 13 if i["severity"] == "HIGH" else 9 for i in _PIRACY_INCIDENTS]

            fig_map = go.Figure()

            # Glow layer
            fig_map.add_trace(go.Scattergeo(
                lat=[i["lat"] for i in _PIRACY_INCIDENTS],
                lon=[i["lon"] for i in _PIRACY_INCIDENTS],
                mode="markers",
                marker=dict(size=[s * 2.5 for s in inc_sizes], color=inc_colors, opacity=0.12, line_width=0),
                hoverinfo="skip", showlegend=False,
            ))
            fig_map.add_trace(go.Scattergeo(
                lat=[i["lat"] for i in _PIRACY_INCIDENTS],
                lon=[i["lon"] for i in _PIRACY_INCIDENTS],
                mode="markers",
                marker=dict(size=inc_sizes, color=inc_colors, opacity=0.90,
                            line=dict(color="rgba(255,255,255,0.4)", width=1), symbol="circle"),
                hovertemplate=[
                    f"<b>{i['type']}</b><br>{i['month']}<br>Severity: {i['severity']}<br>{i['vessel']}<extra></extra>"
                    for i in _PIRACY_INCIDENTS
                ],
                showlegend=False,
            ))

            fig_map.update_geos(
                projection_type="natural earth",
                showland=True, landcolor="#1e293b",
                showocean=True, oceancolor="#0f172a",
                showcountries=True, countrycolor="rgba(255,255,255,0.07)",
                showframe=False, bgcolor="rgba(0,0,0,0)",
                center=dict(lat=10, lon=35),
                lataxis_range=[-20, 70],
                lonaxis_range=[-20, 130],
            )
            fig_map.update_layout(
                height=360,
                margin=dict(l=0, r=0, t=0, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_map, use_container_width=True, config={"displayModeBar": False})

        with col_chart:
            months   = [m["month"]     for m in _PIRACY_MONTHLY]
            inc_cnts = [m["incidents"] for m in _PIRACY_MONTHLY]
            bar_cols = [C_DANGER if c >= 30 else C_ORANGE if c >= 20 else C_WARN for c in inc_cnts]

            fig_bar = go.Figure(go.Bar(
                x=months, y=inc_cnts,
                marker_color=bar_cols,
                marker_line_width=0,
                opacity=0.87,
                hovertemplate="<b>%{x}</b><br>Incidents: %{y}<extra></extra>",
            ))
            fig_bar.update_layout(
                title=dict(text="Monthly Piracy & Attack Incidents", font=dict(size=12, color=C_TEXT2)),
                height=240,
                margin=dict(l=10, r=10, t=40, b=40),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(gridcolor="rgba(255,255,255,0.05)", color=C_TEXT2, tickangle=-35, tickfont=dict(size=9)),
                yaxis=dict(gridcolor="rgba(255,255,255,0.05)", color=C_TEXT2, title="Incidents"),
                font=dict(color=C_TEXT),
            )
            st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})

            # Summary stats
            total_inc  = sum(m["incidents"] for m in _PIRACY_MONTHLY)
            trend_pct  = round((inc_cnts[-1] - inc_cnts[0]) / max(inc_cnts[0], 1) * 100, 0)
            trend_str  = f"+{trend_pct:.0f}%" if trend_pct > 0 else f"{trend_pct:.0f}%"
            trend_col  = C_DANGER if trend_pct > 20 else C_WARN if trend_pct > 0 else C_HIGH

            critical_inc = sum(1 for i in _PIRACY_INCIDENTS if i["severity"] == "CRITICAL")
            hotspot = "Bab-el-Mandeb / Gulf of Aden"

            st.markdown(
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">'
                f'{_kpi_card(str(total_inc), "6-Month Total", C_ORANGE)}'
                f'{_kpi_card(trend_str, "YoY Trend", trend_col)}'
                f'{_kpi_card(str(critical_inc), "Critical Incidents", C_DANGER)}'
                f'{_kpi_card(hotspot[:14]+"…", "Primary Hotspot", C_WARN)}'
                f'</div>',
                unsafe_allow_html=True,
            )

    except Exception as exc:
        logger.warning("Piracy tracker render error: {}", exc)
        st.warning("Piracy tracker unavailable.")


# ============================================================================
# Section 8: Diplomatic Tension Scorecard
# ============================================================================

def _render_diplomatic_tension() -> None:
    """Bilateral tension heatmap matrix."""
    logger.debug("Rendering diplomatic tension matrix")
    try:
        parties = _DIPLO_PARTIES
        matrix  = _DIPLO_MATRIX

        # Heatmap
        hover_text = []
        for i, row in enumerate(matrix):
            hover_row = []
            for j, val in enumerate(row):
                if i == j:
                    hover_row.append(f"<b>{parties[i]}</b><br>Self")
                else:
                    tension = (
                        "Hostile"    if val >= 8.5 else
                        "Very High"  if val >= 7.0 else
                        "High"       if val >= 5.5 else
                        "Moderate"   if val >= 3.5 else
                        "Low"        if val >= 1.5 else
                        "Friendly"
                    )
                    hover_row.append(f"<b>{parties[i]} ↔ {parties[j]}</b><br>Tension: {tension}<br>Score: {val:.1f}/10")
            hover_text.append(hover_row)

        fig = go.Figure(go.Heatmap(
            z=matrix,
            x=parties, y=parties,
            colorscale=[
                [0.0,  "rgba(16,185,129,0.8)"],
                [0.25, "rgba(59,130,246,0.6)"],
                [0.50, "rgba(245,158,11,0.7)"],
                [0.75, "rgba(249,115,22,0.8)"],
                [1.0,  "rgba(239,68,68,0.95)"],
            ],
            zmin=0, zmax=10,
            text=[[f"{v:.1f}" if v > 0 else "—" for v in row] for row in matrix],
            texttemplate="%{text}",
            textfont=dict(size=11, color="white"),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover_text,
            showscale=True,
            colorbar=dict(
                title=dict(text="Tension Score", font=dict(size=11, color=C_TEXT2)),
                tickfont=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(26,34,53,0.85)",
                bordercolor=C_BORDER,
                borderwidth=1,
                len=0.85, thickness=14,
            ),
        ))
        fig.update_layout(
            height=390,
            margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(color=C_TEXT2, tickfont=dict(size=11)),
            yaxis=dict(color=C_TEXT2, tickfont=dict(size=11), autorange="reversed"),
            font=dict(color=C_TEXT),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        # Hottest bilateral pairs
        pairs = []
        for i in range(len(parties)):
            for j in range(i + 1, len(parties)):
                pairs.append((parties[i], parties[j], matrix[i][j]))
        pairs.sort(key=lambda x: -x[2])
        top_pairs = pairs[:5]

        st.markdown(
            f'<div style="background:{C_CARD2};border:1px solid {C_BORDER};border-radius:10px;padding:14px 18px">'
            f'<div style="font-size:0.78rem;font-weight:700;color:{C_TEXT};margin-bottom:10px">Highest Bilateral Tensions</div>'
            + "".join(
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
                f'<span style="font-size:0.80rem;color:{C_TEXT2}">{a} ↔ {b}</span>'
                f'<div style="display:flex;align-items:center;gap:8px">'
                f'<div style="width:80px;background:rgba(255,255,255,0.07);border-radius:3px;height:5px">'
                f'<div style="width:{score*10:.0f}%;background:{C_DANGER if score >= 8 else C_ORANGE if score >= 7 else C_WARN};height:5px;border-radius:3px"></div></div>'
                f'<span style="font-size:0.78rem;font-weight:700;color:{C_DANGER if score >= 8 else C_ORANGE if score >= 7 else C_WARN}">{score:.1f}</span>'
                f'</div></div>'
                for a, b, score in top_pairs
            )
            + "</div>",
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning("Diplomatic tension render error: {}", exc)
        st.warning("Diplomatic tension scorecard unavailable.")


# ============================================================================
# Section 9: Scenario Analysis
# ============================================================================

def _render_scenario_analysis() -> None:
    """3-card scenario analysis (bear/base/bull) with freight implications."""
    logger.debug("Rendering scenario analysis")
    try:
        cols = st.columns(3)
        for col, sc in zip(cols, _SCENARIOS):
            with col:
                color = sc["color"]
                prob  = sc["probability"]

                triggers_html = "".join(
                    f'<div style="display:flex;align-items:flex-start;gap:6px;margin-bottom:5px">'
                    f'<span style="color:{color};font-size:0.75rem;margin-top:1px">▸</span>'
                    f'<span style="font-size:0.75rem;color:{C_TEXT2}">{t}</span></div>'
                    for t in sc["triggers"]
                )

                corridor_html = "".join(
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
                    f'<span style="font-size:0.72rem;color:{C_TEXT3}">{lane}</span>'
                    f'<span style="font-size:0.72rem;font-weight:700;color:{color}">{impact}</span></div>'
                    for lane, impact in sc["freight_corridors"].items()
                )

                card_html = (
                    f'<div style="background:{C_CARD};border:1px solid {color}44;border-radius:14px;'
                    f'padding:20px 18px;height:100%">'
                    # Header
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px">'
                    f'<div>'
                    f'<div style="font-size:1.3rem;font-weight:800;color:{color}">{sc["icon"]} {sc["name"]}</div>'
                    f'<div style="font-size:0.74rem;color:{C_TEXT3};margin-top:1px">{sc["subtitle"]}</div>'
                    f'</div>'
                    f'<div style="background:{color}22;border:1px solid {color}55;border-radius:8px;'
                    f'padding:6px 10px;text-align:center">'
                    f'<div style="font-size:1.1rem;font-weight:800;color:{color}">{prob}%</div>'
                    f'<div style="font-size:0.60rem;color:{C_TEXT3}">Prob.</div></div></div>'
                    # Probability bar
                    f'<div style="background:rgba(255,255,255,0.07);border-radius:4px;height:5px;margin-bottom:14px">'
                    f'<div style="width:{prob}%;background:{color};border-radius:4px;height:5px"></div></div>'
                    # Key metrics
                    f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">'
                    f'<div style="background:{color}14;border-radius:8px;padding:8px;text-align:center">'
                    f'<div style="font-size:1.0rem;font-weight:800;color:{color}">{sc["rate_impact"]}</div>'
                    f'<div style="font-size:0.62rem;color:{C_TEXT3}">Rate Impact</div></div>'
                    f'<div style="background:{color}14;border-radius:8px;padding:8px;text-align:center">'
                    f'<div style="font-size:1.0rem;font-weight:800;color:{color}">{sc["transit_days"]}</div>'
                    f'<div style="font-size:0.62rem;color:{C_TEXT3}">Transit</div></div>'
                    f'<div style="background:{color}14;border-radius:8px;padding:8px;text-align:center">'
                    f'<div style="font-size:1.0rem;font-weight:800;color:{color}">{sc["volume_impact"]}</div>'
                    f'<div style="font-size:0.62rem;color:{C_TEXT3}">Volume</div></div>'
                    f'<div style="background:{color}14;border-radius:8px;padding:8px;text-align:center">'
                    f'<div style="font-size:1.0rem;font-weight:800;color:{color}">{sc["fuel_cost"]}</div>'
                    f'<div style="font-size:0.62rem;color:{C_TEXT3}">Fuel/FEU</div></div>'
                    f'</div>'
                    # Description
                    f'<div style="font-size:0.76rem;color:{C_TEXT2};line-height:1.5;margin-bottom:14px">{sc["description"]}</div>'
                    # Triggers
                    f'<div style="font-size:0.67rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;margin-bottom:7px">Key Triggers</div>'
                    + triggers_html
                    + f'<div style="border-top:1px solid {C_BORDER};margin:12px 0 10px"></div>'
                    # Corridor impacts
                    f'<div style="font-size:0.67rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;margin-bottom:7px">Corridor Rate Impact</div>'
                    + corridor_html
                    + "</div>"
                )
                st.markdown(card_html, unsafe_allow_html=True)

    except Exception as exc:
        logger.warning("Scenario analysis render error: {}", exc)
        st.warning("Scenario analysis unavailable.")


# ============================================================================
# Section 10: World Risk Globe (orthographic)
# ============================================================================

def _render_world_map() -> None:
    """Orthographic dark globe with chokepoint markers and shipping lanes."""
    logger.debug("Rendering geopolitical world risk map")
    try:
        fig = go.Figure()

        for lane in _SHIPPING_LANES:
            lane_color = _LEVEL_COLOR.get(lane["risk"], C_TEXT2)
            opacity = 0.60 if lane["risk"] in ("CRITICAL", "HIGH") else 0.32
            lw = 2.5 if lane["risk"] == "CRITICAL" else 1.8 if lane["risk"] == "HIGH" else 1.2
            fig.add_trace(go.Scattergeo(
                lat=lane["lats"], lon=lane["lons"],
                mode="lines",
                line=dict(color=lane_color, width=lw),
                opacity=opacity,
                hoverinfo="text",
                hovertext=f"{lane['name']} — Risk: {lane['risk']}",
                showlegend=False,
            ))

        lats_cp   = [c["lat"]  for c in _CHOKEPOINTS]
        lons_cp   = [c["lon"]  for c in _CHOKEPOINTS]
        colors_cp = [_LEVEL_COLOR.get(c["risk"], C_TEXT2) for c in _CHOKEPOINTS]
        sizes_cp  = [34 if c["risk"] == "CRITICAL" else 26 if c["risk"] == "HIGH" else 20 for c in _CHOKEPOINTS]
        hover_cp  = [
            f"<b>{c['name']}</b><br>Risk: {c['risk']}<br>{c['note']}<br>Traffic: {c['traffic_pct']}% of world trade"
            for c in _CHOKEPOINTS
        ]

        # Glow ring
        fig.add_trace(go.Scattergeo(
            lat=lats_cp, lon=lons_cp, mode="markers",
            marker=dict(size=[s * 2.2 for s in sizes_cp], color=colors_cp, opacity=0.14, line_width=0),
            hoverinfo="skip", showlegend=False,
        ))
        # Main markers
        fig.add_trace(go.Scattergeo(
            lat=lats_cp, lon=lons_cp, mode="markers+text",
            marker=dict(size=sizes_cp, color=colors_cp, opacity=0.93, symbol="circle",
                        line=dict(color="rgba(255,255,255,0.55)", width=1.5)),
            text=["  " + c["name"] for c in _CHOKEPOINTS],
            textposition="middle right",
            textfont=dict(size=9, color=C_TEXT2),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover_cp,
            showlegend=False,
        ))
        # Warning symbols
        crit_high_cp = [c for c in _CHOKEPOINTS if c["risk"] in ("CRITICAL", "HIGH")]
        if crit_high_cp:
            fig.add_trace(go.Scattergeo(
                lat=[c["lat"] for c in crit_high_cp],
                lon=[c["lon"] for c in crit_high_cp],
                mode="text",
                text=["!" for _ in crit_high_cp],
                textfont=dict(size=10, color="white"),
                hoverinfo="skip", showlegend=False,
            ))

        fig.update_geos(
            projection_type="orthographic",
            showland=True, landcolor="#1e293b",
            showocean=True, oceancolor="#0f172a",
            showlakes=False, showrivers=False,
            showcountries=True, countrycolor="rgba(255,255,255,0.08)",
            showframe=False, bgcolor="rgba(0,0,0,0)",
            projection_rotation=dict(lon=30, lat=20),
        )
        fig.update_layout(
            height=520,
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    except Exception as exc:
        logger.warning("World map render error: {}", exc)
        st.warning("World risk map unavailable.")


# ============================================================================
# Section 11: Route Risk Matrix
# ============================================================================

def _recommendation(score: float, rate_impact: float) -> str:
    if score >= 0.70:
        return "AVOID / Reroute immediately"
    if score >= 0.55 and rate_impact >= 20:
        return "HIGH CAUTION — price in premium"
    if score >= 0.40:
        return "Monitor closely — hedging advised"
    if score >= 0.25:
        return "Moderate caution — standard protocol"
    return "No special action required"


def _render_route_matrix(route_results) -> None:
    """All routes ranked by composite geopolitical score."""
    logger.debug("Rendering route risk matrix")
    try:
        all_scores = get_all_route_scores()
        if not all_scores:
            st.info("No route score data available.")
            return

        order = {"CRITICAL": C_DANGER, "HIGH": C_ORANGE, "MODERATE": C_WARN, "LOW": C_HIGH}
        sorted_routes = sorted(all_scores.items(), key=lambda x: -x[1])

        header_style = (
            f"color:{C_TEXT3};font-size:0.66rem;text-transform:uppercase;"
            f"letter-spacing:0.07em;padding:7px 12px;border-bottom:1px solid rgba(255,255,255,0.10)"
        )
        rows_html = ""
        for route_id, score in sorted_routes:
            level = ("CRITICAL" if score >= 0.75 else "HIGH" if score >= 0.55
                     else "MODERATE" if score >= 0.35 else "LOW")
            color = order[level]
            rate_impact = compute_expected_rate_impact(route_id)
            rec = _recommendation(score, rate_impact)
            ri_str = f"+{rate_impact:.1f}%" if rate_impact >= 0 else f"{rate_impact:.1f}%"
            ri_color = C_DANGER if rate_impact >= 20 else C_ORANGE if rate_impact >= 10 else C_WARN if rate_impact >= 5 else C_TEXT2

            rows_html += (
                f"<tr style='border-bottom:1px solid rgba(255,255,255,0.04)'>"
                f"<td style='color:{C_TEXT};font-size:0.79rem;padding:9px 12px;font-weight:600'>"
                + route_id.replace("_", " ").title()
                + f"</td>"
                f"<td style='padding:9px 12px'>"
                f"<span style='color:{color};border:1px solid {color}44;padding:2px 10px;"
                f"border-radius:999px;font-size:0.67rem;font-weight:700'>{level}</span></td>"
                f"<td style='padding:9px 12px'>"
                f"<div style='display:flex;align-items:center;gap:8px'>"
                f"<div style='width:70px;background:rgba(255,255,255,0.07);border-radius:3px;height:6px'>"
                f"<div style='width:{min(100,score*100):.0f}%;background:{color};border-radius:3px;height:6px'></div></div>"
                f"<span style='font-size:0.77rem;font-weight:700;color:{color}'>{score:.2f}</span></div></td>"
                f"<td style='color:{ri_color};font-size:0.79rem;font-weight:700;padding:9px 12px'>{ri_str}</td>"
                f"<td style='color:{C_TEXT3};font-size:0.73rem;padding:9px 12px'>{rec}</td>"
                f"</tr>"
            )

        table_html = (
            f"<div style='overflow-x:auto'>"
            f"<table style='width:100%;border-collapse:collapse'>"
            f"<thead><tr>"
            f"<th style='{header_style}'>Route</th>"
            f"<th style='{header_style}'>Risk Level</th>"
            f"<th style='{header_style}'>Geo Score</th>"
            f"<th style='{header_style}'>Exp. Rate Impact</th>"
            f"<th style='{header_style}'>Recommendation</th>"
            f"</tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            f"</table></div>"
        )
        st.markdown(
            f"<div style='background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:18px 20px'>"
            + table_html + "</div>",
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning("Route matrix render error: {}", exc)
        st.warning("Route risk matrix unavailable.")


# ============================================================================
# Main render function — EXACT SIGNATURE PRESERVED
# ============================================================================

def render(route_results, port_results, freight_data, macro_data) -> None:
    """Render the Geopolitical Risk Monitor tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer.
    port_results : list[PortDemandResult]
        Current port demand results.
    freight_data : dict
        Freight rate data dict (passed through).
    macro_data : dict
        Global macro indicators dict (passed through).
    """
    logger.info("Rendering Geopolitical Risk Monitor tab")

    st.header("Geopolitical Risk Monitor")
    st.caption(
        "Real-time geopolitical risk intelligence for global shipping. "
        "Risk levels (CRITICAL / HIGH / MODERATE / LOW) are assigned using a probability-weighted "
        "composite of active conflict, sanctions, chokepoint status, and historical disruption frequency. "
        "Scores are updated each app rerun against the curated event database."
    )

    # ── Section 0: Hero Dashboard ────────────────────────────────────────────
    _render_hero_dashboard()
    _divider()

    # ── Section 1: Risk Heatmap ──────────────────────────────────────────────
    _section_title(
        "Global Risk Heatmap",
        "Country-level geopolitical risk scores — natural earth projection with chokepoint overlay.",
    )
    _render_choropleth_map()
    _divider()

    # ── Section 2: Active Conflict Tracker ───────────────────────────────────
    _section_title(
        "Active Conflict Tracker",
        f"{len(_ACTIVE_CONFLICTS)} active conflicts — ranked by severity with shipping impact details.",
    )
    _render_active_conflict_tracker()
    _divider()

    # ── Section 3: Sanctions Monitor ─────────────────────────────────────────
    _section_title(
        "Sanctions Monitor",
        "Jurisdiction coverage tracker — vessel screening status, entity counts, trade exposure.",
    )
    _render_sanctions_monitor()
    _divider()

    # ── Section 4: Political Risk by Trade Lane ───────────────────────────────
    _section_title(
        "Political Risk by Trade Lane",
        "Geopolitical risk score per corridor (0–10) with quarter-on-quarter change.",
    )
    _render_lane_risk_chart()
    _divider()

    # ── Section 5: Conflict Escalation Timeline ───────────────────────────────
    _section_title(
        "Conflict Escalation Timeline (2021–2026)",
        "Global shipping risk index with annotated conflict events — dashed line marks today.",
    )
    _render_escalation_timeline()
    _divider()

    # ── Section 6: Supply Chain Vulnerability ────────────────────────────────
    _section_title(
        "Supply Chain Vulnerability Map",
        "Key sourcing countries ranked by political stability — sized by global trade share.",
    )
    _render_supply_chain_vulnerability()
    _divider()

    # ── Section 7: Piracy & Security ─────────────────────────────────────────
    _section_title(
        "Piracy & Maritime Security Tracker",
        "Incident map (Oct 2025–Mar 2026) with monthly frequency trend.",
    )
    _render_piracy_tracker()
    _divider()

    # ── Section 8: Diplomatic Tension Scorecard ───────────────────────────────
    _section_title(
        "Diplomatic Tension Scorecard",
        "Bilateral tension matrix (0=friendly · 10=hostile) across major shipping powers.",
    )
    _render_diplomatic_tension()
    _divider()

    # ── Section 9: Scenario Analysis ─────────────────────────────────────────
    _section_title(
        "Geopolitical Scenario Analysis",
        "Three forward scenarios with probability, freight rate implications, and key triggers.",
    )
    _render_scenario_analysis()
    _divider()

    # ── Section 10: World Risk Globe ─────────────────────────────────────────
    _section_title(
        "World Risk Map",
        "Orthographic globe — chokepoints and shipping lanes colour-coded by risk severity. Rotate to explore.",
    )
    _render_world_map()
    _divider()

    # ── Section 11: Route Risk Matrix ────────────────────────────────────────
    _section_title(
        "Route Geopolitical Risk Matrix",
        "All trade lanes ranked by composite geopolitical score with expected rate impact and recommendations.",
    )
    _render_route_matrix(route_results)
