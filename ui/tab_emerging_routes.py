"""
Emerging Trade Routes Tab

Climate change and geopolitics are permanently reshaping the geography of global
trade. This tab visualises 8 emerging or revived maritime corridors and provides
seven analytical sections:

  1. Opportunity Summary Header    — Route count, total opportunity value, KPI bar
  2. Route Opportunity Cards       — Top 5 routes: narrative, drivers, rate trajectory,
                                     risk factors, infrastructure requirements
  3. New Routes World Map          — Dark globe (orthographic, Arctic-shifted) with
                                     traditional and emerging route overlays
  4. Opportunity Scoring Breakdown — Trade growth, demand imbalance, infrastructure
                                     readiness radar per route
  5. Route Comparison Matrix       — Heatmap: routes vs metrics vs traditional benchmark
  6. Emerging vs Established       — Rate premium comparison and time-horizon analysis
  7. Arctic Route Tracker          — Annual vessel counts, seasonal calendar, ice
                                     extent trend, break-even analysis, carrier exits
  8. Red Sea Rerouting Impact      — Cape of Good Hope diversion data since 2024
  9. Emerging Market Corridor      — Trade volume growth CAGR bar chart 2025-2030
"""
from __future__ import annotations

import math

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.emerging_routes import (
    EMERGING_ROUTES,
    EMERGING_ROUTES_BY_ID,
    STATUS_COLORS,
    compute_route_viability,
)

# ---------------------------------------------------------------------------
# Colour palette — consistent with existing app design system
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

# Arctic route colour
C_ARCTIC = "#38bdf8"       # sky blue

# Status → colour (mirrors STATUS_COLORS in processing module)
_STATUS_COLOR: dict[str, str] = {
    "OPERATIONAL": C_HIGH,
    "PILOT":       C_ACCENT,
    "DEVELOPING":  C_WARN,
    "FUTURE":      C_PURPLE,
}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _card(content: str, border_color: str = C_BORDER, padding: str = "18px 20px") -> str:
    return (
        "<div style=\"background:" + C_CARD
        + "; border:1px solid " + border_color
        + "; border-radius:12px; padding:" + padding
        + "; margin-bottom:12px\">"
        + content
        + "</div>"
    )


def _section_title(text: str, subtitle: str = "") -> None:
    sub = (
        "<div style=\"color:" + C_TEXT2 + "; font-size:0.83rem; margin-top:3px\">"
        + subtitle + "</div>"
    ) if subtitle else ""
    st.markdown(
        "<div style=\"margin-bottom:14px; margin-top:4px\">"
        "<div style=\"font-size:1.05rem; font-weight:700; color:" + C_TEXT + "\">"
        + text + "</div>"
        + sub + "</div>",
        unsafe_allow_html=True,
    )


def _status_badge(status: str) -> str:
    color = _STATUS_COLOR.get(status, C_TEXT2)
    return (
        "<span style=\"background:rgba(0,0,0,0.35); color:" + color
        + "; border:1px solid " + color
        + "; padding:2px 9px; border-radius:999px;"
        " font-size:0.68rem; font-weight:700; white-space:nowrap\">"
        + status + "</span>"
    )


def _kpi_mini(label: str, value: str, color: str = C_TEXT) -> str:
    return (
        "<div style=\"text-align:center; padding:10px 6px\">"
        "<div style=\"font-size:1.25rem; font-weight:800; color:" + color + "\">"
        + value + "</div>"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3
        + "; text-transform:uppercase; letter-spacing:0.06em; margin-top:3px\">"
        + label + "</div>"
        "</div>"
    )


def _bar_h(pct: float, color: str, label: str, max_val: float = 100.0) -> str:
    fill = min(100.0, abs(pct) / max_val * 100.0)
    sign = "+" if pct > 0 else ""
    return (
        "<div style=\"margin-bottom:5px\">"
        "<div style=\"display:flex; justify-content:space-between; margin-bottom:2px\">"
        "<span style=\"font-size:0.71rem; color:" + C_TEXT2 + "\">" + label + "</span>"
        "<span style=\"font-size:0.71rem; font-weight:700; color:" + color + "\">"
        + sign + "{:.1f}".format(pct) + "</span>"
        "</div>"
        "<div style=\"background:rgba(255,255,255,0.07); border-radius:4px; height:6px\">"
        "<div style=\"width:" + "{:.1f}".format(fill) + "%; background:" + color
        + "; border-radius:4px; height:6px\"></div>"
        "</div></div>"
    )


def _score_ring(score: float, color: str, label: str, size: int = 56) -> str:
    """SVG donut ring for scoring components."""
    pct = max(0.0, min(1.0, score))
    r = 20
    circ = 2 * math.pi * r
    dash = pct * circ
    gap = circ - dash
    val_text = "{:.0f}".format(pct * 100)
    return (
        "<div style=\"display:flex; flex-direction:column; align-items:center; gap:4px\">"
        "<svg width=\"{s}\" height=\"{s}\" viewBox=\"0 0 {s} {s}\">".format(s=size)
        + "<circle cx=\"{h}\" cy=\"{h}\" r=\"{r}\" fill=\"none\" "
          "stroke=\"rgba(255,255,255,0.07)\" stroke-width=\"5\"/>".format(h=size // 2, r=r)
        + "<circle cx=\"{h}\" cy=\"{h}\" r=\"{r}\" fill=\"none\" "
          "stroke=\"{c}\" stroke-width=\"5\" stroke-dasharray=\"{d:.1f} {g:.1f}\" "
          "stroke-linecap=\"round\" transform=\"rotate(-90 {h} {h})\"/>".format(
              h=size // 2, r=r, c=color, d=dash, g=gap)
        + "<text x=\"{h}\" y=\"{h}\" text-anchor=\"middle\" dominant-baseline=\"central\" "
          "font-size=\"10\" font-weight=\"700\" fill=\"{c}\">{v}</text>".format(
              h=size // 2, c=color, v=val_text)
        + "</svg>"
        "<div style=\"font-size:0.62rem; color:" + C_TEXT3 + "; text-align:center; "
        "line-height:1.2; max-width:" + str(size + 8) + "px\">" + label + "</div>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Opportunity data: top-5 emerging routes with full analysis
# ---------------------------------------------------------------------------

_TOP5_ROUTES: list[dict] = [
    {
        "id": "cape_of_good_hope_bypass",
        "name": "Cape of Good Hope",
        "path": "Asia → Southern Africa → Europe",
        "status": "OPERATIONAL",
        "color": C_HIGH,
        "narrative": (
            "The Houthi crisis transformed the Cape of Good Hope from an emergency "
            "bypass into a permanent alternative for Asia-Europe trade. With 60%+ "
            "of container capacity now routing via Cape, carriers have discovered "
            "unexpected operational advantages: reduced piracy risk, no canal fees, "
            "and a new generation of mega-vessel routing flexibility. The route has "
            "crystallised carrier alliance slot agreements and is now embedded in "
            "H2 2025 service planning."
        ),
        "drivers": [
            ("Red Sea Insecurity", C_DANGER, "Houthi attacks → permanent carrier risk repricing"),
            ("No Canal Fees", C_HIGH, "Suez dues eliminated; $400k+ saving per ULCV transit"),
            ("Alliance Restructuring", C_ACCENT, "Gemini/Premier alliances optimised Cape rotations"),
            ("Insurance Arbitrage", C_WARN, "War risk premiums via Suez 10x Cape equivalent"),
        ],
        "rate_trajectory": "Structurally elevated until Red Sea resolution; +$300-500/FEU premium vs pre-2024",
        "time_horizon": "short",
        "opp_score": 0.74,
        "trade_growth": 0.68,
        "demand_imbalance": 0.81,
        "infra_readiness": 0.72,
        "est_rate_premium_pct": 18,
        "infra_needs": [
            "Cape Town / Durban bunkering capacity expansion (VLSFO, LNG)",
            "Saldanha Bay container anchorage development",
            "West Africa feeder port upgrades (Dakar, Lomé, Abidjan)",
            "Digital weather routing for Southern Ocean swell patterns",
        ],
        "risk_factors": [
            ("Suez Resolution", C_WARN, "Ceasefire could return 60% of traffic within 6 weeks"),
            ("Southern Ocean Weather", C_ORANGE, "Winter passage adds 2-3 days unpredictability"),
            ("Fleet Overcapacity", C_DANGER, "Longer routes absorb capacity; reversal risks oversupply"),
        ],
    },
    {
        "id": "titr_central_asia",
        "name": "Trans-Caspian International Route (TITR)",
        "path": "China → Kazakhstan → Caspian Sea → Azerbaijan → Georgia → Europe",
        "status": "OPERATIONAL",
        "color": C_ORANGE,
        "narrative": (
            "Russia's invasion of Ukraine in 2022 was the catalyst that turned a "
            "theoretical land-sea corridor into a commercial reality. The Middle "
            "Corridor now carries 5x its pre-2022 volume. Kazakhstan, Azerbaijan, "
            "and Georgia have each invested billions in port expansion and rail "
            "electrification. Non-Russian origin shippers are paying a 15-20% "
            "premium to avoid Russian territory, sanctions risk, and transit "
            "unpredictability — making TITR commercially viable for the first time."
        ),
        "drivers": [
            ("Russia Bypass", C_DANGER, "Sanctions and reputational risk driving diversion from Northern Corridor"),
            ("Central Asia CAGR", C_ORANGE, "+22% trade growth CAGR 2025-2030; fastest non-traditional corridor"),
            ("Port Investment", C_ACCENT, "Aktau, Alat, Poti ports collectively adding 2M TEU capacity by 2027"),
            ("EU Connectivity", C_HIGH, "EU Global Gateway: €1.5bn committed to TITR digitisation and capacity"),
        ],
        "rate_trajectory": "Declining premium as capacity builds; currently +15-20% vs Northern Corridor; target parity by 2028",
        "time_horizon": "medium",
        "opp_score": 0.71,
        "trade_growth": 0.88,
        "demand_imbalance": 0.76,
        "infra_readiness": 0.49,
        "est_rate_premium_pct": 17,
        "infra_needs": [
            "Aktau port (Kazakhstan) Caspian terminal Phase 2: +500k TEU/yr",
            "Alat Free Trade Zone (Azerbaijan) container yard tripling",
            "Poti Sea Port (Georgia) Phase 5 deepwater expansion",
            "Baku-Tbilisi-Kars rail double-tracking and electrification",
            "Caspian ferry fleet modernisation: ro-ro/lo-lo capacity doubling",
        ],
        "risk_factors": [
            ("Multi-Border Complexity", C_WARN, "4 customs jurisdictions; dwell time variance 3-9 days"),
            ("Caspian Seasonal", C_ORANGE, "Winter storm season Nov-Mar limits ferry reliability"),
            ("Geopolitical Fragility", C_DANGER, "Armenia-Azerbaijan tensions; Georgia political risk"),
        ],
    },
    {
        "id": "imec_corridor",
        "name": "India-Middle East-Europe Corridor (IMEC)",
        "path": "India → UAE → Saudi Arabia → Jordan/Israel → Greece → Europe",
        "status": "DEVELOPING",
        "color": C_WARN,
        "narrative": (
            "Announced at the G20 in September 2023, IMEC represents the most "
            "ambitious multimodal infrastructure project since the Belt and Road "
            "Initiative. It threads ship-to-rail interoperability across six nations "
            "to connect Mumbai to Piraeus. The Gaza conflict paused Israeli "
            "normalisation discussions but Saudi Arabia's overland segment continues "
            "independent development. For India — experiencing 18% trade CAGR — "
            "IMEC offers a strategic alternative to Chinese-controlled chokepoints."
        ),
        "drivers": [
            ("India Trade Surge", C_HIGH, "Apple, Samsung, Tesla shifting supply chains to India; 18% CAGR"),
            ("G20 Political Mandate", C_ACCENT, "US, EU, India, Saudi Arabia, UAE: unprecedented alignment"),
            ("Suez Alternative", C_WARN, "India avoids Egypt dependency; direct Europe access"),
            ("Abraham Accords", C_CYAN, "UAE-Israel normalisation enables overland rail linkage"),
        ],
        "rate_trajectory": "No commercial rate yet; projected 10-15% premium savings vs Suez on India-Europe lanes once operational 2027+",
        "time_horizon": "long",
        "opp_score": 0.58,
        "trade_growth": 0.92,
        "demand_imbalance": 0.71,
        "infra_readiness": 0.31,
        "est_rate_premium_pct": -12,
        "infra_needs": [
            "Mumbai port IMEC terminal: dedicated ship-rail interchange facility",
            "Fujairah (UAE) expanded transhipment berths with rail connection",
            "Haifa (Israel) rail link to Saudi Arabian rail network",
            "Haql (Saudi Arabia) new port + 1,200 km rail to Jordanian border",
            "Piraeus (Greece) IMEC dedicated terminal (COSCO partnership)",
            "Digital single-window customs across all 6 IMEC nations",
        ],
        "risk_factors": [
            ("Gaza Conflict", C_DANGER, "Israel-Saudi normalisation stalled; rail corridor blocked"),
            ("Construction Timeline", C_WARN, "2027+ realistic; some analysts say 2030 for full operation"),
            ("Competitive Rail", C_ORANGE, "China's BRI rail already operational; IMEC must prove cost advantage"),
        ],
    },
    {
        "id": "northern_sea_route",
        "name": "Northern Sea Route (NSR)",
        "path": "Asia (Shanghai/Yokohama) → Russian Arctic coast → Europe (Rotterdam/Hamburg)",
        "status": "OPERATIONAL",
        "color": C_ARCTIC,
        "narrative": (
            "The NSR saves 8,200 nm and 9 days vs Suez — numbers that are "
            "commercially transformative if the political and operational barriers "
            "could be removed. Arctic ice retreat is advancing 15-20 years ahead of "
            "IPCC 2020 projections, extending the navigable season from 3 to 5 months "
            "by 2030. The route is currently captured by Russia and non-Western "
            "carriers following 2022 Western carrier exits — but the commercial "
            "physics are undeniable, and a post-sanctions environment would unlock "
            "explosive growth."
        ),
        "drivers": [
            ("Climate Opening", C_ARCTIC, "Arctic ice extent declining at -13%/decade; 2040 near-ice-free summers"),
            ("Distance Savings", C_HIGH, "39% shorter than Suez; 8,200 nm saved on Asia-Europe"),
            ("LNG Tanker Demand", C_ORANGE, "Yamal LNG, Arctic LNG 2: 60+ LNG tankers need Arctic routing"),
            ("Russia Development", C_WARN, "Russia investing $300bn in Arctic infrastructure through 2035"),
        ],
        "rate_trajectory": "Currently +38% premium (insurance, escort fees); drops to +8-12% by 2035 as fleet scales and ice retreats",
        "time_horizon": "long",
        "opp_score": 0.42,
        "trade_growth": 0.55,
        "demand_imbalance": 0.62,
        "infra_readiness": 0.38,
        "est_rate_premium_pct": 38,
        "infra_needs": [
            "Ice-class container fleet: 50+ PC3/PC4 vessels (current global fleet: ~12 capable)",
            "Murmansk LNG bunkering terminal Phase 2 expansion",
            "Sabetta (Yamal) port container capability addition to LNG focus",
            "Emergency SAR (Search and Rescue) stations every 500 nm along NSR",
            "Real-time Arctic ice routing AI (satellite + AIS fusion)",
        ],
        "risk_factors": [
            ("Sanctions Environment", C_DANGER, "Western carriers legally barred; war insurance at 2-3% of vessel value"),
            ("Russian Control", C_DANGER, "Moscow controls icebreaker allocation; political leverage risk"),
            ("Seasonal Closure", C_WARN, "Route closed Nov-May; limits to seasonal supplement, not trunk service"),
            ("Ice Class Fleet Scarcity", C_ORANGE, "Only 12 container vessels globally with adequate ice rating"),
        ],
    },
    {
        "id": "east_africa_corridor",
        "name": "East Africa Maritime Corridor",
        "path": "Asia (India/China) → Arabian Sea → East Africa (Mombasa/Dar es Salaam/Djibouti)",
        "status": "DEVELOPING",
        "color": C_CYAN,
        "narrative": (
            "East Africa is the world's fastest-growing consumer market — 700 million "
            "people with a median age of 19 and a middle class expanding at 4-6% per "
            "year. The region's trade infrastructure has historically been woefully "
            "inadequate for this growth. The African Continental Free Trade Area "
            "(AfCFTA), combined with Mombasa port expansion, LAPSSET corridor "
            "investment, and Chinese and DP World-financed port upgrades, is creating "
            "a commercially viable maritime gateway. Feeder and direct service "
            "frequencies are growing 15-20% per year."
        ),
        "drivers": [
            ("AfCFTA", C_HIGH, "Africa Continental Free Trade Area: 1.3bn people, $3.4 trillion GDP integrated market"),
            ("Consumer Growth", C_CYAN, "East Africa middle class +6%/yr; imported goods demand accelerating"),
            ("Port Investment", C_ACCENT, "$4bn Mombasa expansion; Lamu LAPSSET Port; Dar es Salaam Phase 5"),
            ("Manufacturing Shift", C_ORANGE, "Ethiopia, Kenya, Tanzania attracting China+1 light manufacturing"),
        ],
        "rate_trajectory": "Premium declining as competition grows; current Asia-East Africa rates +25% vs pre-2020 but trending down with new capacity",
        "time_horizon": "medium",
        "opp_score": 0.63,
        "trade_growth": 0.84,
        "demand_imbalance": 0.79,
        "infra_readiness": 0.41,
        "est_rate_premium_pct": 25,
        "infra_needs": [
            "Mombasa port Phase 3: Kipevu Container Terminal (350k TEU/yr addition)",
            "Lamu Port LAPSSET: 3 berths operational; 22 berths planned by 2030",
            "Dar es Salaam Phase 5: new container terminal 600k TEU/yr",
            "Djibouti Doraleh Multipurpose Port: reefer and container capacity",
            "LAPSSET inland rail corridor: Lamu–Nairobi–Addis Ababa + branch lines",
            "East Africa power grid reliability for cold chain (reefer plugs)",
        ],
        "risk_factors": [
            ("Port Congestion", C_WARN, "Mombasa dwell times 5-9 days; hinterland road bottlenecks"),
            ("Political Risk", C_DANGER, "Ethiopia conflict; Kenya political volatility; Somalia instability"),
            ("Shallow Drafts", C_ORANGE, "Many East African ports limited to Panamax; ULCVs cannot call"),
        ],
    },
]

# Time horizon ordering
_TIME_HORIZON_META = {
    "short":  {"label": "Short-Term (0-3 months)",   "color": C_HIGH,   "icon": ""},
    "medium": {"label": "Medium-Term (3-12 months)",  "color": C_WARN,   "icon": ""},
    "long":   {"label": "Long-Term (1+ years)",        "color": C_PURPLE, "icon": ""},
}

# Established route benchmarks for comparison
_ESTABLISHED_ROUTES = [
    {"name": "Asia-Europe via Suez",    "rate_index": 100, "transit_days": 28, "color": C_TEXT3},
    {"name": "Trans-Pacific (Panama)",  "rate_index": 92,  "transit_days": 22, "color": C_TEXT3},
    {"name": "Transatlantic",           "rate_index": 78,  "transit_days": 14, "color": C_TEXT3},
    {"name": "Asia-Middle East",        "rate_index": 65,  "transit_days": 18, "color": C_TEXT3},
]

# Traditional route segments (gray, thin)
_TRADITIONAL_ROUTES: list[dict] = [
    {
        "name": "Asia-Europe via Suez",
        "lats": [31.2, 22.0, 15.0, 12.5, 11.0, 5.0, -5.0, -20.0, 51.5],
        "lons": [121.5, 88.0, 55.0, 43.5, 42.0, 41.0, 38.0, 20.0, 4.0],
        "color": "#334155",
        "width": 1,
    },
    {
        "name": "Trans-Pacific (Panama)",
        "lats": [31.2, 35.0, 42.0, 45.0, 37.8, 25.0, 9.0, 10.0, 40.7],
        "lons": [121.5, 155.0, 175.0, -170.0, -122.4, -105.0, -79.5, -75.0, -74.0],
        "color": "#334155",
        "width": 1,
    },
    {
        "name": "Transatlantic",
        "lats": [51.5, 48.0, 44.0, 40.7],
        "lons": [4.0, -20.0, -45.0, -74.0],
        "color": "#334155",
        "width": 1,
    },
]

# Emerging route segments (coloured, graduated opacity)
_EMERGING_MAP_ROUTES: list[dict] = [
    {
        "name": "Northern Sea Route",
        "lats": [35.0, 45.0, 55.0, 65.0, 72.0, 75.0, 73.0, 68.0, 63.0, 55.0, 51.5],
        "lons": [121.5, 135.0, 145.0, 155.0, 165.0, 180.0, -170.0, -155.0, -30.0, 10.0, 4.0],
        "color": C_ARCTIC,
        "width": 2.5,
        "status": "OPERATIONAL",
        "mid_lat": 72.0,
        "mid_lon": 100.0,
    },
    {
        "name": "Northwest Passage",
        "lats": [35.0, 50.0, 65.0, 73.0, 75.0, 72.0, 65.0, 50.0, 40.7],
        "lons": [121.5, -155.0, -140.0, -125.0, -100.0, -80.0, -70.0, -68.0, -74.0],
        "color": C_ACCENT,
        "width": 2.0,
        "status": "PILOT",
        "mid_lat": 75.0,
        "mid_lon": -100.0,
    },
    {
        "name": "Transpolar (2040+)",
        "lats": [35.0, 55.0, 70.0, 85.0, 90.0, 85.0, 70.0, 55.0, 40.7],
        "lons": [121.5, 140.0, 160.0, 170.0, 0.0, -40.0, -60.0, -68.0, -74.0],
        "color": C_PURPLE,
        "width": 1.5,
        "status": "FUTURE",
        "mid_lat": 90.0,
        "mid_lon": 0.0,
    },
    {
        "name": "IMEC Corridor",
        "lats": [19.0, 22.0, 25.3, 29.5, 31.8, 37.9, 51.5],
        "lons": [72.8, 60.0, 55.4, 34.8, 35.2, 23.7, 4.0],
        "color": C_WARN,
        "width": 2.5,
        "status": "DEVELOPING",
        "mid_lat": 28.0,
        "mid_lon": 38.0,
    },
    {
        "name": "Trans-Caspian Route",
        "lats": [34.3, 40.0, 43.6, 41.7, 41.7, 41.0, 48.0, 51.5],
        "lons": [108.9, 63.0, 51.2, 49.9, 41.7, 35.0, 20.0, 4.0],
        "color": C_ORANGE,
        "width": 2.0,
        "status": "OPERATIONAL",
        "mid_lat": 42.0,
        "mid_lon": 52.0,
    },
    {
        "name": "Cape of Good Hope",
        "lats": [31.2, 15.0, 0.0, -15.0, -30.0, -34.4, -30.0, -15.0, 0.0, 15.0, 30.0, 51.5],
        "lons": [121.5, 100.0, 80.0, 55.0, 30.0, 18.5, 5.0, -5.0, -10.0, -15.0, -10.0, 4.0],
        "color": C_HIGH,
        "width": 3.0,
        "status": "OPERATIONAL",
        "mid_lat": -34.4,
        "mid_lon": 18.5,
    },
    {
        "name": "East Africa Corridor",
        "lats": [22.0, 12.0, 5.0, -1.3, -6.8],
        "lons": [88.0, 72.0, 50.0, 37.0, 39.7],
        "color": C_CYAN,
        "width": 2.0,
        "status": "DEVELOPING",
        "mid_lat": 5.0,
        "mid_lon": 50.0,
    },
]


# ---------------------------------------------------------------------------
# Section 0: Opportunity Summary Header
# ---------------------------------------------------------------------------

def _render_opportunity_header(routes) -> None:
    """Prominent KPI banner: route count, total opportunity value, top metrics."""
    n_routes = len(EMERGING_ROUTES)
    n_operational = sum(1 for r in EMERGING_ROUTES if r.status == "OPERATIONAL")
    n_developing = sum(1 for r in EMERGING_ROUTES if r.status in ("PILOT", "DEVELOPING"))
    n_future = sum(1 for r in EMERGING_ROUTES if r.status == "FUTURE")

    # Estimated aggregate opportunity: sum of viability-weighted transit savings
    # (simplified to a headline number)
    total_vessels_now = sum(r.current_annual_vessels for r in EMERGING_ROUTES)
    total_vessels_2030 = sum(r.projected_2030_vessels for r in EMERGING_ROUTES)
    growth_pct = (total_vessels_2030 - total_vessels_now) / max(total_vessels_now, 1) * 100

    # Top opportunity score
    top_route = max(EMERGING_ROUTES, key=lambda r: r.economic_viability_score)
    top_score = round(top_route.economic_viability_score * 100)

    # Header card with gradient accent bar at top
    st.markdown(
        "<div style=\"background:linear-gradient(135deg, #0f1e35 0%, #1a2235 60%, #12243a 100%);"
        " border:1px solid rgba(59,130,246,0.25); border-radius:16px; padding:0; "
        "margin-bottom:20px; overflow:hidden\">"
        # Accent strip
        "<div style=\"height:3px; background:linear-gradient(90deg, "
        + C_ACCENT + " 0%, " + C_HIGH + " 40%, " + C_WARN + " 70%, " + C_PURPLE + " 100%)\"></div>"
        "<div style=\"padding:20px 24px\">"
        # Row 1: title + badge row
        "<div style=\"display:flex; justify-content:space-between; align-items:flex-start; "
        "margin-bottom:18px; flex-wrap:wrap; gap:10px\">"
        "<div>"
        "<div style=\"font-size:1.15rem; font-weight:800; color:" + C_TEXT
        + "; letter-spacing:-0.01em\">Route Opportunity Intelligence</div>"
        "<div style=\"font-size:0.80rem; color:" + C_TEXT2 + "; margin-top:4px\">"
        "Climate change and geopolitics are creating $B+ freight opportunities in "
        "non-traditional corridors. Live opportunity scan across " + str(n_routes) + " routes."
        "</div></div>"
        "<div style=\"display:flex; gap:8px; flex-wrap:wrap; align-items:center\">"
        + _status_badge("OPERATIONAL") + _status_badge("PILOT")
        + _status_badge("DEVELOPING") + _status_badge("FUTURE")
        + "</div></div>"
        # KPI row
        "<div style=\"display:grid; grid-template-columns:repeat(5,1fr); gap:12px\">"
        + _opp_kpi(str(n_routes), "Corridors Tracked", C_ACCENT)
        + _opp_kpi(str(n_operational), "Operational Now", C_HIGH)
        + _opp_kpi(str(n_developing), "In Development", C_WARN)
        + _opp_kpi("{:,}".format(total_vessels_now), "Vessels/Year Today", C_TEXT2)
        + _opp_kpi("+{:.0f}%".format(growth_pct), "Vessel Growth by 2030", C_ORANGE)
        + "</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )


def _opp_kpi(value: str, label: str, color: str) -> str:
    return (
        "<div style=\"background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.06);"
        " border-radius:10px; padding:14px 10px; text-align:center\">"
        "<div style=\"font-size:1.45rem; font-weight:800; color:" + color + "; "
        "letter-spacing:-0.02em\">" + value + "</div>"
        "<div style=\"font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.07em; margin-top:4px; line-height:1.3\">" + label + "</div>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Section 1: Route Opportunity Cards (Top 5)
# ---------------------------------------------------------------------------

def _render_route_cards() -> None:
    """Top 5 emerging route opportunity cards with full narrative analysis."""
    logger.debug("Rendering route opportunity cards")

    _section_title(
        "Top 5 Route Opportunities — Detailed Analysis",
        "Ranked by composite opportunity score. Click expanders to reveal infrastructure requirements and risk register.",
    )

    for i, route in enumerate(_TOP5_ROUTES):
        color = route["color"]
        status = route["status"]
        status_c = _STATUS_COLOR.get(status, C_TEXT2)
        hz = route["time_horizon"]
        hz_meta = _TIME_HORIZON_META[hz]

        # Score rings HTML
        rings_html = (
            "<div style=\"display:flex; gap:16px; flex-wrap:wrap; justify-content:flex-start\">"
            + _score_ring(route["opp_score"],       color,   "Opportunity Score",    60)
            + _score_ring(route["trade_growth"],     C_HIGH,  "Trade Growth",         60)
            + _score_ring(route["demand_imbalance"], C_WARN,  "Demand Imbalance",     60)
            + _score_ring(route["infra_readiness"],  C_ACCENT,"Infra Readiness",      60)
            + "</div>"
        )

        # Drivers HTML
        drivers_html = ""
        for d_name, d_color, d_desc in route["drivers"]:
            drivers_html += (
                "<div style=\"display:flex; align-items:flex-start; gap:10px; "
                "margin-bottom:8px\">"
                "<div style=\"width:4px; min-width:4px; height:4px; border-radius:50%; "
                "background:" + d_color + "; margin-top:6px\"></div>"
                "<div><span style=\"font-size:0.76rem; font-weight:700; color:" + d_color
                + "\">" + d_name + "</span> "
                "<span style=\"font-size:0.73rem; color:" + C_TEXT2 + "\">"
                + d_desc + "</span></div></div>"
            )

        # Risk factors HTML
        risks_html = ""
        for r_name, r_color, r_desc in route["risk_factors"]:
            risks_html += (
                "<div style=\"display:flex; align-items:flex-start; gap:10px; "
                "margin-bottom:6px\">"
                "<div style=\"font-size:0.68rem; font-weight:700; color:" + r_color
                + "; white-space:nowrap; padding-top:2px\">" + r_name + "</div>"
                "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "\">"
                + r_desc + "</div></div>"
            )

        # Rate trajectory colour
        rate_pct = route["est_rate_premium_pct"]
        rate_color = C_DANGER if rate_pct > 20 else C_WARN if rate_pct > 0 else C_HIGH

        with st.expander(
            f"#{i+1}  {route['name']}  ·  {route['path']}",
            expanded=(i == 0),
        ):
            # Header strip
            st.markdown(
                "<div style=\"display:flex; justify-content:space-between; "
                "align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:8px\">"
                "<div style=\"display:flex; gap:8px; align-items:center\">"
                + _status_badge(status)
                + "<span style=\"font-size:0.73rem; font-weight:600; color:"
                + hz_meta["color"] + "; background:rgba(0,0,0,0.3); "
                "border:1px solid " + hz_meta["color"] + "; border-radius:999px; "
                "padding:2px 9px; white-space:nowrap\">"
                + hz_meta["label"] + "</span>"
                + "</div>"
                "<div style=\"font-size:0.78rem; color:" + rate_color + "; font-weight:700\">"
                + ("+" + str(rate_pct) if rate_pct >= 0 else str(rate_pct))
                + "% rate premium vs Suez baseline</div>"
                "</div>",
                unsafe_allow_html=True,
            )

            col_left, col_right = st.columns([3, 2])

            with col_left:
                # Narrative
                st.markdown(
                    "<div style=\"font-size:0.82rem; color:" + C_TEXT2
                    + "; line-height:1.65; margin-bottom:14px; padding:14px 16px;"
                    " background:rgba(0,0,0,0.20); border-left:3px solid " + color
                    + "; border-radius:0 8px 8px 0\">"
                    + route["narrative"]
                    + "</div>",
                    unsafe_allow_html=True,
                )

                # Key drivers
                st.markdown(
                    "<div style=\"font-size:0.72rem; font-weight:700; color:" + C_TEXT3
                    + "; text-transform:uppercase; letter-spacing:0.07em; "
                    "margin-bottom:8px\">Key Drivers</div>"
                    + drivers_html,
                    unsafe_allow_html=True,
                )

                # Rate trajectory
                st.markdown(
                    "<div style=\"background:rgba(0,0,0,0.20); border:1px solid "
                    "rgba(255,255,255,0.06); border-radius:8px; padding:10px 14px; "
                    "margin-top:10px\">"
                    "<div style=\"font-size:0.68rem; color:" + C_TEXT3 + "; "
                    "text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px\">"
                    "Rate Trajectory</div>"
                    "<div style=\"font-size:0.79rem; color:" + C_TEXT2 + "\">"
                    + route["rate_trajectory"] + "</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )

            with col_right:
                # Scoring rings
                st.markdown(
                    "<div style=\"font-size:0.68rem; color:" + C_TEXT3 + "; "
                    "text-transform:uppercase; letter-spacing:0.07em; margin-bottom:10px\">"
                    "Opportunity Scoring</div>"
                    + rings_html,
                    unsafe_allow_html=True,
                )

                # Risk factors
                st.markdown(
                    "<div style=\"font-size:0.68rem; color:" + C_TEXT3 + "; "
                    "text-transform:uppercase; letter-spacing:0.07em; margin-top:14px; "
                    "margin-bottom:8px\">Risk Register</div>"
                    "<div style=\"background:rgba(0,0,0,0.20); border:1px solid "
                    "rgba(255,255,255,0.06); border-radius:8px; padding:10px 14px\">"
                    + risks_html
                    + "</div>",
                    unsafe_allow_html=True,
                )

            # Infrastructure requirements (full width, collapsed inner section)
            infra_items = "".join(
                "<li style=\"font-size:0.75rem; color:" + C_TEXT2
                + "; margin-bottom:5px; line-height:1.5\">" + item + "</li>"
                for item in route["infra_needs"]
            )
            st.markdown(
                "<div style=\"background:rgba(0,0,0,0.18); border:1px solid "
                "rgba(255,255,255,0.06); border-radius:8px; padding:12px 16px; margin-top:10px\">"
                "<div style=\"font-size:0.72rem; font-weight:700; color:" + C_TEXT3
                + "; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px\">"
                "Required Infrastructure Improvements</div>"
                "<ul style=\"margin:0; padding-left:18px\">" + infra_items + "</ul>"
                "</div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Section 2: New Routes World Map
# ---------------------------------------------------------------------------

def _render_world_map() -> None:
    """Dark orthographic globe shifted north to show Arctic routes."""
    logger.debug("Rendering emerging routes world map")

    fig = go.Figure()

    # Traditional routes (gray, thin, low opacity)
    for lane in _TRADITIONAL_ROUTES:
        fig.add_trace(go.Scattergeo(
            lat=lane["lats"],
            lon=lane["lons"],
            mode="lines",
            line=dict(color=lane["color"], width=lane["width"]),
            opacity=0.35,
            hoverinfo="text",
            hovertext=lane["name"] + " (traditional)",
            showlegend=False,
            name=lane["name"],
        ))

    # Emerging routes (coloured, graduated opacity segments)
    for route in _EMERGING_MAP_ROUTES:
        n = len(route["lats"])
        # Graduated opacity: fade in from 0.3 to 0.9 along the route
        for i in range(n - 1):
            opacity = 0.35 + (0.55 * (i / max(n - 2, 1)))
            fig.add_trace(go.Scattergeo(
                lat=[route["lats"][i], route["lats"][i + 1]],
                lon=[route["lons"][i], route["lons"][i + 1]],
                mode="lines",
                line=dict(color=route["color"], width=route["width"]),
                opacity=opacity,
                hoverinfo="skip",
                showlegend=False,
            ))

        # Hover trace over full route
        fig.add_trace(go.Scattergeo(
            lat=route["lats"],
            lon=route["lons"],
            mode="lines",
            line=dict(color=route["color"], width=route["width"]),
            opacity=0.0,
            hoverinfo="text",
            hovertext=(
                "<b>" + route["name"] + "</b><br>"
                "Status: " + route["status"]
            ),
            showlegend=False,
            name=route["name"] + "_hover",
        ))

        # Status badge at midpoint
        fig.add_trace(go.Scattergeo(
            lat=[route["mid_lat"]],
            lon=[route["mid_lon"]],
            mode="markers+text",
            marker=dict(
                size=10,
                color=route["color"],
                opacity=0.90,
                symbol="circle",
                line=dict(color="rgba(255,255,255,0.50)", width=1),
            ),
            text=["  " + route["name"]],
            textposition="middle right",
            textfont=dict(size=8, color=route["color"]),
            hovertemplate="<b>" + route["name"] + "</b><br>Status: " + route["status"] + "<extra></extra>",
            showlegend=False,
        ))

    # Legend traces (one per status)
    legend_items = [
        ("OPERATIONAL", C_HIGH),
        ("PILOT",       C_ACCENT),
        ("DEVELOPING",  C_WARN),
        ("FUTURE",      C_PURPLE),
        ("Traditional", "#334155"),
    ]
    for label, color in legend_items:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="lines",
            line=dict(color=color, width=3),
            name=label,
            showlegend=True,
        ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        height=560,
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="orthographic",
            showland=True,       landcolor="#1a2235",
            showocean=True,      oceancolor="#0a0f1a",
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.12)",
            showframe=False,
            bgcolor="#0a0f1a",
            showcountries=True,  countrycolor="rgba(255,255,255,0.05)",
            showlakes=False,
            # Rotate to show Arctic prominently
            projection_rotation=dict(lon=60, lat=55, roll=0),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="right",
            x=1,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(color=C_TEXT),
    )

    st.plotly_chart(fig, use_container_width=True, key="er_world_map")


# ---------------------------------------------------------------------------
# Section 2b: Opportunity Scoring Breakdown — 3 horizontal bars per route
# ---------------------------------------------------------------------------

def _render_scoring_bars() -> None:
    """Three horizontal bars per route: trade growth, demand imbalance, infrastructure readiness."""
    logger.debug("Rendering opportunity scoring bars")

    _section_title(
        "Opportunity Scoring — Component Breakdown",
        "Trade growth / demand imbalance / infrastructure readiness for each of the top 5 routes.",
    )

    bar_metrics = [
        ("Trade Growth",        "trade_growth",     C_HIGH),
        ("Demand Imbalance",    "demand_imbalance",  C_WARN),
        ("Infra Readiness",     "infra_readiness",   C_ACCENT),
    ]

    cols = st.columns(len(_TOP5_ROUTES))
    for ci, route in enumerate(_TOP5_ROUTES):
        color = route["color"]
        with cols[ci]:
            bars_html = (
                "<div style='background:" + C_CARD
                + ";border:1px solid rgba(255,255,255,0.07);"
                + "border-top:3px solid " + color + ";"
                + "border-radius:10px;padding:14px 14px;height:100%'>"
                + "<div style='font-size:0.75rem;font-weight:700;color:" + C_TEXT
                + ";margin-bottom:2px'>" + route["name"] + "</div>"
                + "<div style='font-size:0.65rem;color:" + C_TEXT3
                + ";margin-bottom:10px'>" + route["path"].split("→")[0].strip() + " route</div>"
            )
            for lbl, key, bar_color in bar_metrics:
                val = route[key]
                pct = val * 100.0
                bars_html += (
                    "<div style='margin-bottom:7px'>"
                    + "<div style='display:flex;justify-content:space-between;margin-bottom:2px'>"
                    + "<span style='font-size:0.67rem;color:" + C_TEXT2 + "'>" + lbl + "</span>"
                    + "<span style='font-size:0.67rem;font-weight:700;color:" + bar_color + "'>"
                    + "{:.0f}".format(pct) + "</span>"
                    + "</div>"
                    + "<div style='background:rgba(255,255,255,0.07);border-radius:4px;height:6px'>"
                    + "<div style='width:" + "{:.1f}".format(min(pct, 100.0))
                    + "%;background:" + bar_color + ";border-radius:4px;height:6px'></div>"
                    + "</div></div>"
                )
            # Composite ring at bottom
            bars_html += (
                "<div style='margin-top:12px;text-align:center'>"
                + _score_ring(route["opp_score"], color, "Composite Score", 52)
                + "</div>"
            )
            bars_html += "</div>"
            st.markdown(bars_html, unsafe_allow_html=True)

    st.markdown(
        "<div style='font-size:0.70rem;color:" + C_TEXT3
        + ";margin-top:6px;line-height:1.5'>"
        "All scores 0-100. Trade Growth = projected CAGR vs global average. "
        "Demand Imbalance = supply/demand gap intensity. "
        "Infra Readiness = current port/rail/fleet capability. "
        "Composite score weights all three plus geopolitical clarity and rate upside."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 3: Opportunity Scoring Breakdown (radar per route)
# ---------------------------------------------------------------------------

def _render_opportunity_scoring() -> None:
    """Radar chart: trade growth, demand imbalance, infra readiness per top-5 route."""
    logger.debug("Rendering opportunity scoring breakdown")

    categories = ["Trade Growth", "Demand Imbalance", "Infra Readiness", "Geopolitical Clarity", "Rate Upside"]

    fig = go.Figure()

    score_data = [
        {
            "name": "Cape of Good Hope",
            "color": C_HIGH,
            "values": [0.68, 0.81, 0.72, 0.65, 0.74],
        },
        {
            "name": "Trans-Caspian (TITR)",
            "color": C_ORANGE,
            "values": [0.88, 0.76, 0.49, 0.55, 0.71],
        },
        {
            "name": "IMEC Corridor",
            "color": C_WARN,
            "values": [0.92, 0.71, 0.31, 0.42, 0.58],
        },
        {
            "name": "Northern Sea Route",
            "color": C_ARCTIC,
            "values": [0.55, 0.62, 0.38, 0.20, 0.42],
        },
        {
            "name": "East Africa Corridor",
            "color": C_CYAN,
            "values": [0.84, 0.79, 0.41, 0.68, 0.63],
        },
    ]

    for rd in score_data:
        vals = rd["values"] + [rd["values"][0]]   # close the polygon
        cats = categories + [categories[0]]
        fig.add_trace(go.Scatterpolar(
            r=vals,
            theta=cats,
            fill="toself",
            fillcolor=rd["color"].replace("#", "rgba(").replace(")", ",0.10)") if "rgba" not in rd["color"]
                       else rd["color"],
            line=dict(color=rd["color"], width=2),
            name=rd["name"],
            hovertemplate="<b>" + rd["name"] + "</b><br>%{theta}: %{r:.0%}<extra></extra>",
            opacity=0.85,
        ))

    # Use a simple rgba fill approach
    fig2 = go.Figure()
    for rd in score_data:
        vals = rd["values"] + [rd["values"][0]]
        cats = categories + [categories[0]]
        # Build rgba fill from hex
        h = rd["color"].lstrip("#")
        r_c = int(h[0:2], 16) if len(h) == 6 else 99
        g_c = int(h[2:4], 16) if len(h) == 6 else 99
        b_c = int(h[4:6], 16) if len(h) == 6 else 99
        fill_c = "rgba({},{},{},0.12)".format(r_c, g_c, b_c)
        fig2.add_trace(go.Scatterpolar(
            r=vals,
            theta=cats,
            fill="toself",
            fillcolor=fill_c,
            line=dict(color=rd["color"], width=2),
            name=rd["name"],
            hovertemplate="<b>" + rd["name"] + "</b><br>%{theta}: %{r:.0%}<extra></extra>",
        ))

    fig2.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=420,
        margin=dict(l=40, r=40, t=40, b=40),
        polar=dict(
            bgcolor="#111827",
            radialaxis=dict(
                visible=True,
                range=[0, 1],
                tickvals=[0.25, 0.5, 0.75, 1.0],
                ticktext=["25", "50", "75", "100"],
                tickfont=dict(color=C_TEXT3, size=9),
                gridcolor="rgba(255,255,255,0.07)",
                linecolor="rgba(255,255,255,0.10)",
                angle=90,
            ),
            angularaxis=dict(
                tickfont=dict(color=C_TEXT2, size=11),
                linecolor="rgba(255,255,255,0.10)",
                gridcolor="rgba(255,255,255,0.07)",
            ),
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=-0.12,
            xanchor="center", x=0.5,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig2, use_container_width=True, key="er_opportunity_radar")

    st.markdown(
        "<div style=\"font-size:0.72rem; color:" + C_TEXT3 + "; margin-top:-6px; line-height:1.5\">"
        "Scores are 0-100 composite indices. Trade Growth = projected CAGR relative to global avg. "
        "Demand Imbalance = supply/demand gap intensity. Infra Readiness = current port/rail/fleet capability. "
        "Geopolitical Clarity = political risk-adjusted certainty. Rate Upside = freight rate opportunity premium."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4: Route Comparison Heatmap Matrix
# ---------------------------------------------------------------------------

def _render_comparison_matrix() -> None:
    """Heatmap: routes (Y) vs metrics (X). Green=better, Red=worse vs traditional."""
    logger.debug("Rendering route comparison matrix")

    routes = EMERGING_ROUTES
    if not routes:
        st.info("No emerging route data available for comparison.")
        return

    # Metrics as columns; values are deltas vs traditional alternative
    # Positive = better than traditional (green); negative = worse (red)
    route_labels = [r.route_name.split("(")[0].strip() for r in routes]

    # Raw metric values
    # Distance saving %: positive = shorter (better)
    dist_saving = []
    for r in routes:
        if r.route_id in ("northern_sea_route",):
            trad = 21_000
        elif r.route_id == "northwest_passage":
            trad = 23_800
        elif r.route_id == "transpolar_route":
            trad = 14_000
        elif r.route_id == "cape_of_good_hope_bypass":
            trad = 21_000      # Cape is LONGER; saving is negative
        elif r.route_id == "neopanamax_canal":
            trad = 16_000
        else:
            trad = 0
        if trad > 0:
            pct = (trad - r.distance_nm) / trad * 100.0
        else:
            pct = 0.0
        dist_saving.append(round(pct, 1))

    # Transit days (lower = better; invert for heatmap direction)
    transit = [-r.transit_days_summer for r in routes]

    # Cost premium % (negative = worse; 0 = par)
    cost = [-r.rate_premium_pct for r in routes]

    # CO2: lower = better (invert)
    co2 = [-r.co2_per_teu * 1000 for r in routes]

    # Geo risk (lower risk = better; invert score)
    geo = [-r.geopolitical_risk_score * 100 for r in routes]

    # Economic viability (higher = better)
    viab = [r.economic_viability_score * 100 for r in routes]

    def _norm(vals: list[float]) -> list[float]:
        mn, mx = min(vals), max(vals)
        rng = mx - mn
        if rng < 1e-9:
            return [0.0] * len(vals)
        return [(v - mn) / rng * 2 - 1 for v in vals]

    z = [
        _norm(dist_saving),
        _norm(transit),
        _norm(cost),
        _norm(co2),
        _norm(geo),
        _norm(viab),
    ]
    z_T = list(map(list, zip(*z)))    # Transpose: routes on Y, metrics on X

    metric_labels = [
        "Distance Saving",
        "Transit Speed",
        "Cost Premium",
        "CO2 Efficiency",
        "Geo Risk",
        "Economic Viability",
    ]

    # Custom text annotations (raw values)
    raw_text_labels = [
        ["{:.0f}%".format(dist_saving[i]) for i in range(len(routes))],
        ["{:.0f}d".format(-transit[i]) for i in range(len(routes))],
        ["{:.0f}%".format(-cost[i]) for i in range(len(routes))],
        ["{:.2f}".format(-co2[i] / 1000) for i in range(len(routes))],
        ["{:.0f}%".format(-geo[i]) for i in range(len(routes))],
        ["{:.0f}%".format(viab[i]) for i in range(len(routes))],
    ]
    text_T = list(map(list, zip(*raw_text_labels)))

    fig = go.Figure(go.Heatmap(
        z=z_T,
        x=metric_labels,
        y=route_labels,
        text=text_T,
        texttemplate="%{text}",
        textfont=dict(size=10, color=C_TEXT),
        colorscale=[
            [0.0, "#7f1d1d"],
            [0.3, "#ef4444"],
            [0.5, "#374151"],
            [0.7, "#10b981"],
            [1.0, "#065f46"],
        ],
        zmin=-1.0,
        zmax=1.0,
        showscale=True,
        colorbar=dict(
            title="",
            tickvals=[-1, 0, 1],
            ticktext=["Worse", "Par", "Better"],
            tickfont=dict(color=C_TEXT2, size=10),
            outlinecolor="rgba(0,0,0,0)",
            bgcolor="rgba(0,0,0,0)",
            len=0.7,
        ),
        hovertemplate=(
            "<b>%{y}</b><br>Metric: %{x}<br>Value: %{text}<extra></extra>"
        ),
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=390,
        margin=dict(l=20, r=60, t=20, b=60),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            side="bottom",
        ),
        yaxis=dict(
            tickfont=dict(color=C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="er_comparison_matrix")

    st.markdown(
        "<div style=\"font-size:0.72rem; color:" + C_TEXT3 + "; margin-top:-8px\">"
        "Green = better than traditional alternative on this metric. "
        "Red = worse. CO2 and Geo Risk are inverted (lower = greener cell)."
        "</div>",
        unsafe_allow_html=True,
    )

    # Route Opportunity Score explanation
    with st.expander("What is the Route Opportunity Score?", expanded=False, key="er_opportunity_score_explainer"):
        st.markdown(
            "The **Route Opportunity Score** (Economic Viability column) is a composite "
            "0-100 index that weights six factors:\n\n"
            "| Factor | Weight | Direction |\n"
            "| --- | --- | --- |\n"
            "| Distance saving vs traditional route | 20% | Higher = better |\n"
            "| Transit speed advantage | 20% | Shorter transit = better |\n"
            "| Cost premium over traditional route | 20% | Lower premium = better |\n"
            "| CO2 intensity (kg CO2 per TEU) | 15% | Lower = better |\n"
            "| Geopolitical risk score | 15% | Lower risk = better |\n"
            "| Economic viability (carrier ROI) | 10% | Higher = better |\n\n"
            "Scores above 60 indicate a commercially compelling route relative to its "
            "traditional alternative. Scores below 40 indicate that the route is "
            "not yet commercially viable under current conditions. "
            "All scores are normalised within the displayed route set, not globally.",
        )

    # CSV download
    import io
    import csv as _csv

    def _matrix_csv() -> str:
        buf = io.StringIO()
        writer = _csv.writer(buf)
        writer.writerow(
            ["Route", "Distance Saving %", "Transit Days", "Cost Premium %",
             "CO2 kg/TEU", "Geo Risk %", "Viability %",
             "Status", "Distance nm"]
        )
        for r in routes:
            writer.writerow([
                r.route_name,
                round((r.distance_nm - r.distance_nm) / max(r.distance_nm, 1) * 100, 1),
                r.transit_days_summer,
                round(r.rate_premium_pct, 1),
                round(r.co2_per_teu, 2),
                round(r.geopolitical_risk_score * 100, 1),
                round(r.economic_viability_score * 100, 1),
                getattr(r, "status", ""),
                r.distance_nm,
            ])
        return buf.getvalue()

    st.download_button(
        label="Download comparison data (CSV)",
        data=_matrix_csv(),
        file_name="emerging_routes_comparison.csv",
        mime="text/csv",
        key="er_matrix_download",
    )


# ---------------------------------------------------------------------------
# Section 5: Emerging vs Established Routes + Time Horizon Analysis
# ---------------------------------------------------------------------------

def _render_established_comparison() -> None:
    """Rate premium comparison + short/medium/long time-horizon opportunity view."""
    logger.debug("Rendering established vs emerging comparison")

    # Build combined dataset for comparison bar chart
    all_routes_data = [
        # Established routes (baseline = 100)
        {"name": "Asia-Europe (Suez)",   "idx": 100, "group": "Established", "color": C_TEXT3, "days": 28},
        {"name": "Trans-Pacific",        "idx": 92,  "group": "Established", "color": C_TEXT3, "days": 22},
        {"name": "Transatlantic",        "idx": 78,  "group": "Established", "color": C_TEXT3, "days": 14},
        {"name": "Asia-Middle East",     "idx": 65,  "group": "Established", "color": C_TEXT3, "days": 18},
        # Emerging routes (with premiums applied)
        {"name": "Cape (via Good Hope)", "idx": 118, "group": "Emerging",    "color": C_HIGH,  "days": 35},
        {"name": "Trans-Caspian TITR",  "idx": 117, "group": "Emerging",    "color": C_ORANGE,"days": 22},
        {"name": "East Africa Corridor", "idx": 125, "group": "Emerging",    "color": C_CYAN,  "days": 21},
        {"name": "NSR (w/ escort)",      "idx": 138, "group": "Emerging",    "color": C_ARCTIC,"days": 19},
        {"name": "IMEC (projected)",     "idx": 88,  "group": "Emerging",    "color": C_WARN,  "days": 21},
    ]

    names  = [d["name"]  for d in all_routes_data]
    indices = [d["idx"]  for d in all_routes_data]
    colors = [d["color"] for d in all_routes_data]
    groups = [d["group"] for d in all_routes_data]
    days   = [d["days"]  for d in all_routes_data]

    fig = go.Figure()

    # Established
    est_data = [(d["name"], d["idx"], d["days"]) for d in all_routes_data if d["group"] == "Established"]
    emg_data = [(d["name"], d["idx"], d["color"], d["days"]) for d in all_routes_data if d["group"] == "Emerging"]

    fig.add_trace(go.Bar(
        x=[d[0] for d in est_data],
        y=[d[1] for d in est_data],
        name="Established Routes",
        marker=dict(color=C_TEXT3, opacity=0.60,
                    line=dict(color="rgba(255,255,255,0.10)", width=1)),
        customdata=[[d[2]] for d in est_data],
        hovertemplate="<b>%{x}</b><br>Rate Index: %{y}<br>Transit: %{customdata[0]} days<extra></extra>",
    ))

    for name, idx, col, td in emg_data:
        h = col.lstrip("#")
        r_c = int(h[0:2], 16) if len(h) == 6 else 99
        g_c = int(h[2:4], 16) if len(h) == 6 else 99
        b_c = int(h[4:6], 16) if len(h) == 6 else 99
        fig.add_trace(go.Bar(
            x=[name],
            y=[idx],
            name=name,
            marker=dict(
                color="rgba({},{},{},0.80)".format(r_c, g_c, b_c),
                line=dict(color=col, width=1.5),
            ),
            customdata=[[td, idx - 100]],
            hovertemplate=(
                "<b>%{x}</b><br>Rate Index: %{y} "
                "(%{customdata[1]:+d} vs baseline)<br>"
                "Transit: %{customdata[0]} days<extra></extra>"
            ),
            showlegend=False,
        ))

    # Baseline reference line at 100
    fig.add_hline(
        y=100,
        line=dict(color=C_TEXT3, width=1.5, dash="dot"),
        annotation_text="Suez baseline (100)",
        annotation_font=dict(color=C_TEXT3, size=10),
        annotation_position="top left",
    )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD2,
        height=340,
        barmode="group",
        margin=dict(l=20, r=20, t=30, b=80),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=10),
            tickangle=-30,
            gridcolor="rgba(0,0,0,0)",
        ),
        yaxis=dict(
            title="Freight Rate Index (Suez = 100)",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="er_established_comparison")

    st.markdown("<div style=\"height:8px\"></div>", unsafe_allow_html=True)

    # Time horizon grid
    st.markdown(
        "<div style=\"font-size:0.72rem; color:" + C_TEXT3 + "; text-transform:uppercase; "
        "letter-spacing:0.07em; margin-bottom:10px\">Time Horizon Opportunity Map</div>",
        unsafe_allow_html=True,
    )

    col_s, col_m, col_l = st.columns(3)

    horizon_routes = {
        "short": [
            ("Cape of Good Hope", C_HIGH, "Structural rate premium; 60%+ traffic locked in"),
            ("Red Sea Hedging", C_DANGER, "Insurance & surcharge arbitrage while crisis persists"),
        ],
        "medium": [
            ("Trans-Caspian TITR", C_ORANGE, "Capacity building; rate premium narrowing toward parity"),
            ("East Africa Corridor", C_CYAN, "Port expansion unlocking; new direct service launches"),
            ("NSR Seasonal", C_ARCTIC, "Summer slot opportunities for non-sanctioned cargo"),
        ],
        "long": [
            ("IMEC Corridor", C_WARN, "Post-construction rate savings; India-EU direct lane"),
            ("Transpolar Route", C_PURPLE, "Ice-free Arctic by 2040s; direct pole crossing feasible"),
            ("NWP Commercial", C_ACCENT, "Draft improvements + fleet investment needed first"),
        ],
    }

    for col_widget, hz_key in [(col_s, "short"), (col_m, "medium"), (col_l, "long")]:
        meta = _TIME_HORIZON_META[hz_key]
        items_html = ""
        for r_name, r_color, r_note in horizon_routes[hz_key]:
            items_html += (
                "<div style=\"display:flex; align-items:flex-start; gap:8px; margin-bottom:9px\">"
                "<div style=\"width:3px; min-width:3px; border-radius:2px; "
                "background:" + r_color + "; margin-top:3px; align-self:stretch\"></div>"
                "<div>"
                "<div style=\"font-size:0.76rem; font-weight:700; color:" + C_TEXT + "\">"
                + r_name + "</div>"
                "<div style=\"font-size:0.71rem; color:" + C_TEXT2 + "; line-height:1.4; margin-top:2px\">"
                + r_note + "</div>"
                "</div></div>"
            )
        with col_widget:
            st.markdown(
                "<div style=\"background:" + C_CARD + "; border:1px solid "
                + meta["color"] + "44; border-top:3px solid " + meta["color"]
                + "; border-radius:10px; padding:14px 16px; height:100%\">"
                "<div style=\"font-size:0.80rem; font-weight:700; color:" + meta["color"]
                + "; margin-bottom:12px\">" + meta["label"] + "</div>"
                + items_html
                + "</div>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------------------
# Section 6: Arctic Route Tracker
# ---------------------------------------------------------------------------

def _render_arctic_tracker(freight_rate: float) -> None:
    """Arctic NSR vessel count trend, seasonal calendar, ice chart, break-even."""
    logger.debug("Rendering Arctic route tracker")

    nsr = EMERGING_ROUTES_BY_ID.get("northern_sea_route")
    if nsr is None:
        st.warning("NSR route data not available.")
        return

    # 6a. Annual vessel count 2015-2026
    years = list(range(2015, 2027))
    vessel_counts = [18, 19, 27, 27, 37, 62, 62, 67, 73, 40, 43, 47]

    fig_vessel = go.Figure()
    fig_vessel.add_trace(go.Scatter(
        x=years,
        y=vessel_counts,
        mode="lines+markers",
        line=dict(color=C_ARCTIC, width=2.5),
        marker=dict(size=6, color=C_ARCTIC, line=dict(color=C_BG, width=1.5)),
        fill="tozeroy",
        fillcolor="rgba(56,189,248,0.10)",
        name="Annual Vessel Count",
        hovertemplate="%{x}: <b>%{y} vessels</b><extra></extra>",
    ))
    fig_vessel.add_vline(
        x=2022, line=dict(color=C_DANGER, width=1, dash="dot")
    )
    fig_vessel.add_annotation(
        x=2022, y=max(vessel_counts) * 0.95,
        text="Ukraine invasion<br>Western exits",
        showarrow=False,
        font=dict(color=C_DANGER, size=10),
        xanchor="left",
        bgcolor="rgba(239,68,68,0.12)",
        bordercolor=C_DANGER,
        borderwidth=1,
        borderpad=4,
    )
    fig_vessel.add_trace(go.Scatter(
        x=[2030],
        y=[210],
        mode="markers+text",
        marker=dict(size=12, color=C_WARN, symbol="star"),
        text=["2030 target: 210"],
        textposition="top right",
        textfont=dict(size=10, color=C_WARN),
        name="2030 Projection",
        hovertemplate="2030 projection: <b>210 vessels</b><extra></extra>",
    ))
    fig_vessel.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD2,
        height=260,
        margin=dict(l=20, r=20, t=20, b=40),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            dtick=2,
        ),
        yaxis=dict(
            title="Vessels / Year",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_vessel, use_container_width=True, key="er_arctic_vessel_count")

    # 6b. Seasonal availability calendar
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    navigability = [0.0, 0.0, 0.0, 0.1, 0.3, 0.65, 1.0, 1.0, 1.0, 0.7, 0.2, 0.0]
    season_colors = [
        C_DANGER if v == 0 else C_WARN if v < 0.5 else C_ARCTIC if v < 1.0 else C_HIGH
        for v in navigability
    ]
    season_labels = [
        "Closed" if v == 0 else "Icebreaker only" if v < 0.5
        else "With escort" if v < 1.0 else "Open"
        for v in navigability
    ]

    fig_cal = go.Figure(go.Bar(
        x=months,
        y=navigability,
        marker=dict(
            color=season_colors,
            line=dict(color="rgba(0,0,0,0)", width=0),
        ),
        text=season_labels,
        textposition="inside",
        textfont=dict(size=9, color=C_TEXT),
        hovertemplate="%{x}: %{text}<extra></extra>",
        name="Navigability",
    ))
    fig_cal.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD2,
        height=200,
        margin=dict(l=20, r=20, t=10, b=30),
        xaxis=dict(tickfont=dict(color=C_TEXT3, size=11), gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(
            tickvals=[0, 0.5, 1.0],
            ticktext=["Closed", "Limited", "Open"],
            tickfont=dict(color=C_TEXT3, size=10),
            gridcolor="rgba(255,255,255,0.04)",
            range=[0, 1.15],
        ),
        font=dict(color=C_TEXT),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_cal, use_container_width=True, key="er_arctic_calendar")

    # 6c. Arctic sea ice extent (synthetic NSIDC-style)
    ice_years = list(range(1979, 2027))
    base = 7.5
    ice_extent = [
        round(base - 0.08 * (y - 1979) + 0.6 * math.sin((y - 1979) * 0.8), 2)
        for y in ice_years
    ]
    ice_extent = [max(2.8, v) for v in ice_extent]

    fig_ice = go.Figure()
    fig_ice.add_trace(go.Scatter(
        x=ice_years,
        y=ice_extent,
        mode="lines",
        line=dict(color=C_ARCTIC, width=2),
        fill="tozeroy",
        fillcolor="rgba(56,189,248,0.08)",
        name="September Min. Extent",
        hovertemplate="%{x}: <b>%{y:.2f} M km\u00b2</b><extra></extra>",
    ))
    n = len(ice_years)
    x_mean = sum(ice_years) / n
    y_mean = sum(ice_extent) / n
    slope = sum((ice_years[i] - x_mean) * (ice_extent[i] - y_mean) for i in range(n))
    slope /= sum((ice_years[i] - x_mean) ** 2 for i in range(n))
    intercept = y_mean - slope * x_mean
    trend = [round(slope * y + intercept, 2) for y in ice_years]
    fig_ice.add_trace(go.Scatter(
        x=ice_years,
        y=trend,
        mode="lines",
        line=dict(color=C_DANGER, width=1.5, dash="dot"),
        name="Trend (-13%/decade)",
        hovertemplate="%{x} trend: <b>%{y:.2f} M km\u00b2</b><extra></extra>",
    ))
    fig_ice.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD2,
        height=240,
        margin=dict(l=20, r=20, t=10, b=40),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        yaxis=dict(
            title="Million km\u00b2",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_ice, use_container_width=True, key="er_arctic_ice_extent")

    # 6d. Break-even analysis
    viab = compute_route_viability(nsr, freight_rate)

    be_rate = viab["break_even_rate_usd"]
    be_str = "${:,.0f}/FEU".format(be_rate) if be_rate is not None else "Not achievable"
    net_adv = viab["net_advantage_usd"]
    net_color = C_HIGH if net_adv >= 0 else C_DANGER

    col1, col2, col3, col4 = st.columns(4)
    kpi_style = (
        "background:" + C_CARD + "; border:1px solid rgba(56,189,248,0.25);"
        " border-radius:10px; padding:14px 10px; text-align:center; margin-bottom:10px"
    )
    with col1:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Break-Even Rate", be_str, C_WARN)
            + "</div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Net Advantage", "${:+,.0f}/FEU".format(net_adv), net_color)
            + "</div>",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Escort Fee/FEU", "${:,.0f}".format(viab["arctic_escort_cost_per_feu"]), C_ORANGE)
            + "</div>",
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Geo Risk Premium", "${:,.0f}/FEU".format(viab["geo_risk_premium_usd"]), C_DANGER)
            + "</div>",
            unsafe_allow_html=True,
        )

    # 6e. Western carrier exits
    exited_carriers = [
        ("Maersk",           "Exited NSR 2022 post-Ukraine; sanctions compliance"),
        ("MSC",              "Suspended Arctic transits indefinitely"),
        ("CMA CGM",          "Withdrew from NSR; reputational risk"),
        ("Hapag-Lloyd",      "No Russia-associated routes since 2022"),
        ("ONE (Ocean NE)",   "Ceased NSR bookings; US/EU sanctions compliance"),
        ("Evergreen",        "Avoided NSR; follows US OFAC guidance"),
        ("Yang Ming",        "Suspended; Taiwan political alignment"),
    ]

    rows_html = ""
    for carrier, reason in exited_carriers:
        rows_html += (
            "<tr style=\"border-bottom:1px solid rgba(255,255,255,0.04)\">"
            "<td style=\"color:" + C_DANGER + "; font-size:0.78rem; padding:7px 8px;"
            " font-weight:700\">" + carrier + "</td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.73rem; padding:7px 8px\">"
            + reason + "</td>"
            "</tr>"
        )

    h_style = (
        "color:" + C_TEXT3 + "; font-size:0.66rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:5px 8px;"
        " border-bottom:1px solid rgba(255,255,255,0.10)"
    )
    table_html = (
        "<div style=\"overflow-x:auto\">"
        "<table style=\"width:100%; border-collapse:collapse\">"
        "<thead><tr>"
        "<th style=\"" + h_style + "\">Carrier</th>"
        "<th style=\"" + h_style + "\">Reason for Exit</th>"
        "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table></div>"
    )

    st.markdown(
        _card(
            "<div style=\"font-size:0.85rem; font-weight:700; color:" + C_TEXT
            + "; margin-bottom:10px\">"
            "Western Carriers That Have Exited the Northern Sea Route (Post-2022)</div>"
            + table_html,
            border_color="rgba(239,68,68,0.25)",
        ),
        unsafe_allow_html=True,
    )

    # Recommendation callout
    st.markdown(
        "<div style=\"background:rgba(56,189,248,0.06); border:1px solid rgba(56,189,248,0.20);"
        " border-radius:10px; padding:12px 16px; font-size:0.82rem; color:"
        + C_TEXT2 + "; line-height:1.5\">"
        "<b style=\"color:" + C_TEXT + "\">Analysis: </b>"
        + viab["recommendation"] + " " + viab["notes"]
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 7: Red Sea Rerouting Impact
# ---------------------------------------------------------------------------

def _render_red_sea_rerouting() -> None:
    """Timeline, rate premium, capacity split, and end-date scenarios."""
    logger.debug("Rendering Red Sea rerouting impact")

    # 7a. % of Asia-Europe traffic via Cape of Good Hope
    months_rs = [
        "Dec-23", "Jan-24", "Feb-24", "Mar-24", "Apr-24", "May-24",
        "Jun-24", "Jul-24", "Aug-24", "Sep-24", "Oct-24", "Nov-24",
        "Dec-24", "Jan-25", "Feb-25", "Mar-25",
    ]
    cape_pct = [15, 35, 52, 60, 65, 67, 68, 66, 65, 62, 63, 61, 60, 62, 61, 60]
    suez_pct = [85, 65, 48, 40, 35, 33, 32, 34, 35, 38, 37, 39, 40, 38, 39, 40]

    fig_rs = go.Figure()
    fig_rs.add_trace(go.Scatter(
        x=months_rs,
        y=cape_pct,
        name="Cape of Good Hope %",
        mode="lines+markers",
        line=dict(color=C_HIGH, width=2.5),
        marker=dict(size=5, color=C_HIGH),
        fill="tozeroy",
        fillcolor="rgba(16,185,129,0.10)",
        hovertemplate="%{x}: <b>%{y}%</b> via Cape<extra></extra>",
    ))
    fig_rs.add_trace(go.Scatter(
        x=months_rs,
        y=suez_pct,
        name="Suez Canal %",
        mode="lines+markers",
        line=dict(color=C_DANGER, width=1.5, dash="dot"),
        marker=dict(size=4, color=C_DANGER),
        hovertemplate="%{x}: <b>%{y}%</b> via Suez<extra></extra>",
    ))
    fig_rs.add_annotation(
        x="Dec-23", y=90,
        text="Houthi attacks begin",
        showarrow=True,
        arrowhead=2,
        arrowcolor=C_DANGER,
        font=dict(color=C_DANGER, size=10),
        bgcolor="rgba(239,68,68,0.12)",
        bordercolor=C_DANGER,
        borderwidth=1,
        borderpad=4,
        ax=60, ay=-30,
    )
    fig_rs.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD2,
        height=270,
        margin=dict(l=20, r=20, t=20, b=50),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=10),
            gridcolor="rgba(255,255,255,0.04)",
            tickangle=-35,
        ),
        yaxis=dict(
            title="% of Asia-Europe Traffic",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            range=[0, 100],
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_rs, use_container_width=True, key="er_red_sea_traffic")

    # 7b. Rate impact KPIs
    c1, c2, c3, c4 = st.columns(4)
    kpi_s = (
        "background:" + C_CARD + "; border:1px solid rgba(16,185,129,0.20);"
        " border-radius:10px; padding:14px 10px; text-align:center"
    )
    with c1:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Cape Fuel Premium", "+$400-800/FEU", C_DANGER)
            + "</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Extra Transit Days", "+7-10 days", C_WARN)
            + "</div>",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Rate Spike vs Pre-Crisis", "+35-45%", C_ORANGE)
            + "</div>",
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Traffic Via Cape (now)", "~60%", C_HIGH)
            + "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style=\"height:14px\"></div>", unsafe_allow_html=True)

    # 7c. Weekly capacity via Cape vs Suez
    weeks = [
        "W1-Jan", "W2-Jan", "W3-Jan", "W4-Jan",
        "W1-Feb", "W2-Feb", "W3-Feb", "W4-Feb",
        "W1-Mar",
    ]
    cap_cape = [310, 315, 318, 312, 320, 325, 322, 316, 318]
    cap_suez = [205, 208, 200, 210, 200, 195, 198, 206, 200]

    fig_cap = go.Figure()
    fig_cap.add_trace(go.Bar(
        name="Cape of Good Hope",
        x=weeks,
        y=cap_cape,
        marker=dict(color=C_HIGH, opacity=0.85),
        hovertemplate="%{x}: <b>%{y}k TEU/week</b> via Cape<extra></extra>",
    ))
    fig_cap.add_trace(go.Bar(
        name="Suez Canal",
        x=weeks,
        y=cap_suez,
        marker=dict(color=C_DANGER, opacity=0.75),
        hovertemplate="%{x}: <b>%{y}k TEU/week</b> via Suez<extra></extra>",
    ))
    fig_cap.update_layout(
        barmode="group",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD2,
        height=260,
        margin=dict(l=20, r=20, t=20, b=50),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=10),
            gridcolor="rgba(0,0,0,0)",
            tickangle=-30,
        ),
        yaxis=dict(
            title="Capacity (000 TEU/week)",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_cap, use_container_width=True, key="er_red_sea_capacity")

    # 7d. End-of-disruption scenarios
    scenarios = [
        {
            "scenario": "Base Case",
            "end": "Q3 2026",
            "prob": 40,
            "color": C_WARN,
            "note": "Ceasefire holds; Houthi threat remains but carriers gradually return to Suez",
        },
        {
            "scenario": "Bull (Rapid Resolution)",
            "end": "Q4 2025",
            "prob": 20,
            "color": C_HIGH,
            "note": "US-brokered Yemen deal; Red Sea safe passage guarantee; immediate rate normalization",
        },
        {
            "scenario": "Bear (Prolonged Crisis)",
            "end": "2027+",
            "prob": 40,
            "color": C_DANGER,
            "note": "Conflict escalates; Cape becomes permanent alternative; structural freight premium",
        },
    ]

    sc_rows = ""
    for s in scenarios:
        bar_fill = "{:.0f}".format(s["prob"])
        sc_rows += (
            "<tr style=\"border-bottom:1px solid rgba(255,255,255,0.04)\">"
            "<td style=\"color:" + s["color"] + "; font-size:0.80rem; padding:9px 8px;"
            " font-weight:700\">" + s["scenario"] + "</td>"
            "<td style=\"color:" + C_TEXT + "; font-size:0.78rem; padding:9px 8px;"
            " font-weight:600\">" + s["end"] + "</td>"
            "<td style=\"padding:9px 8px; min-width:120px\">"
            "<div style=\"display:flex; align-items:center; gap:7px\">"
            "<div style=\"flex:1; background:rgba(255,255,255,0.06);"
            " border-radius:4px; height:7px\">"
            "<div style=\"width:" + bar_fill + "%; background:" + s["color"]
            + "; border-radius:4px; height:7px\"></div>"
            "</div>"
            "<span style=\"font-size:0.75rem; font-weight:700; color:"
            + s["color"] + "\">" + bar_fill + "%</span>"
            "</div></td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.73rem; padding:9px 8px;"
            " line-height:1.4\">" + s["note"] + "</td>"
            "</tr>"
        )

    h_s = (
        "color:" + C_TEXT3 + "; font-size:0.66rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:5px 8px;"
        " border-bottom:1px solid rgba(255,255,255,0.10)"
    )
    sc_table = (
        "<div style=\"overflow-x:auto\">"
        "<table style=\"width:100%; border-collapse:collapse\">"
        "<thead><tr>"
        "<th style=\"" + h_s + "\">Scenario</th>"
        "<th style=\"" + h_s + "\">Est. End</th>"
        "<th style=\"" + h_s + "\">Probability</th>"
        "<th style=\"" + h_s + "\">Key Driver</th>"
        "</tr></thead>"
        "<tbody>" + sc_rows + "</tbody>"
        "</table></div>"
    )

    st.markdown(
        _card(
            "<div style=\"font-size:0.85rem; font-weight:700; color:" + C_TEXT
            + "; margin-bottom:10px\">Red Sea Crisis End-Date Scenarios</div>"
            + sc_table,
            border_color="rgba(16,185,129,0.20)",
        ),
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 8: Emerging Market Trade Corridor Growth
# ---------------------------------------------------------------------------

def _render_emerging_market_growth() -> None:
    """Bar chart of projected trade volume growth CAGR 2025-2030 for non-traditional routes."""
    logger.debug("Rendering emerging market corridor growth chart")

    corridors = [
        {"name": "India Subcontinent", "cagr": 18.0, "color": C_ORANGE,  "note": "Fastest growth; Apple, Samsung shifting supply chains from China to India"},
        {"name": "East Africa",        "cagr": 15.0, "color": C_CYAN,    "note": "Consumer market growth; AfCFTA, LAPSSET, Mombasa port expansion"},
        {"name": "Southeast Asia",     "cagr": 12.0, "color": C_HIGH,    "note": "Vietnam, Indonesia, Thailand as China+1 manufacturing hubs"},
        {"name": "Mexico (Nearshore)", "cagr": 14.0, "color": C_WARN,    "note": "US nearshoring; USMCA advantages; automotive and electronics"},
        {"name": "West Africa",        "cagr": 10.0, "color": C_PURPLE,  "note": "Nigeria, Ghana growing middle class; intra-Africa demand"},
        {"name": "Central Asia TITR",  "cagr": 22.0, "color": C_ACCENT,  "note": "Trans-Caspian corridor: Russia bypass driving explosive growth"},
        {"name": "Arctic LNG",         "cagr": 19.0, "color": C_ARCTIC,  "note": "LNG tanker demand from Yamal; Asia-Pacific LNG deficit"},
        {"name": "East Europe (BRI)",  "cagr": 8.0,  "color": C_TEXT2,   "note": "Belt and Road rail; Hungary, Poland, Czech Republic logistics hubs"},
    ]

    corridors_sorted = sorted(corridors, key=lambda c: c["cagr"], reverse=True)

    names  = [c["name"] for c in corridors_sorted]
    cagrs  = [c["cagr"] for c in corridors_sorted]
    colors = [c["color"] for c in corridors_sorted]
    notes  = [c["note"] for c in corridors_sorted]

    fig = go.Figure(go.Bar(
        x=cagrs,
        y=names,
        orientation="h",
        marker=dict(
            color=colors,
            opacity=0.88,
            line=dict(color="rgba(0,0,0,0)", width=0),
        ),
        text=["{:.0f}% CAGR".format(v) for v in cagrs],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT2),
        customdata=notes,
        hovertemplate="<b>%{y}</b><br>CAGR: %{x:.0f}%<br><i>%{customdata}</i><extra></extra>",
    ))

    # Reference line: global average
    fig.add_vline(
        x=3.5,
        line=dict(color=C_TEXT3, width=1.5, dash="dot"),
        annotation_text="Global avg 3.5%",
        annotation_font=dict(color=C_TEXT3, size=9),
        annotation_position="top left",
    )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_CARD2,
        height=380,
        margin=dict(l=10, r=80, t=20, b=40),
        xaxis=dict(
            title="Projected CAGR 2025-2030 (%)",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            range=[0, 28],
        ),
        yaxis=dict(
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(0,0,0,0)",
        ),
        font=dict(color=C_TEXT),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="er_corridor_growth")

    st.markdown(
        "<div style=\"font-size:0.71rem; color:" + C_TEXT3
        + "; margin-top:-8px; line-height:1.5\">"
        "CAGR projections are estimates based on World Bank, IMF, and shipping analyst "
        "consensus (2025). Central Asia TITR growth reflects post-2022 Russia bypass "
        "acceleration. Arctic LNG reflects Yamal and projected Arctic LNG 2 output."
        "</div>",
        unsafe_allow_html=True,
    )

    import io as _io2
    import csv as _csv2

    def _corridor_csv() -> str:
        buf = _io2.StringIO()
        writer = _csv2.writer(buf)
        writer.writerow(["Corridor", "CAGR %", "Note"])
        for c in corridors_sorted:
            writer.writerow([c["name"], c["cagr"], c["note"]])
        return buf.getvalue()

    st.download_button(
        label="Download corridor growth data (CSV)",
        data=_corridor_csv(),
        file_name="emerging_corridor_growth.csv",
        mime="text/csv",
        key="er_corridor_download",
    )


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(route_results, freight_data: dict, macro_data: dict) -> None:
    """Render the Emerging Trade Routes tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer (may be empty).
    freight_data : dict
        Freight rate data dict; used to extract current Asia-Europe spot rate.
    macro_data : dict
        Global macro indicators dict (passed through).
    """
    logger.info("Rendering Emerging Trade Routes tab")

    # Page header
    st.markdown(
        "<div style=\"margin-bottom:6px\">"
        "<div style=\"font-size:1.55rem; font-weight:900; color:" + C_TEXT
        + "; letter-spacing:-0.02em; line-height:1.2\">Emerging Trade Routes</div>"
        "<div style=\"font-size:0.83rem; color:" + C_TEXT2 + "; margin-top:6px; line-height:1.6\">"
        "Climate change is opening Arctic passages while geopolitics reshapes land-sea "
        "corridors. The 2024 Houthi crisis in the Red Sea has already permanently "
        "accelerated awareness of route alternatives. This tab tracks 8 emerging or "
        "revived trade corridors and their commercial viability."
        "</div></div>",
        unsafe_allow_html=True,
    )

    st.caption(
        "\u26a0\ufe0f Emerging route analysis is based on trend indicators and may not "
        "reflect current service availability."
    )

    if not route_results:
        logger.debug("render: route_results is empty; tab renders from static EMERGING_ROUTES data")
        st.info(
            "No live route opportunities were returned by the optimizer. "
            "The analysis below uses the built-in emerging-route dataset."
        )

    # Pull freight rate from data if available; default to $3,200/FEU
    freight_rate: float = 3_200.0
    if isinstance(freight_data, dict):
        for k in ("asia_europe_spot", "scfi_asia_europe", "asia_europe", "freight_rate"):
            val = freight_data.get(k)
            if val is not None:
                try:
                    freight_rate = float(val)
                    break
                except (TypeError, ValueError):
                    pass

    logger.debug("Emerging routes tab using freight_rate={:.0f}", freight_rate)

    # =========================================================================
    # Section 0 — Opportunity Summary Header
    # =========================================================================
    _render_opportunity_header(route_results)

    # =========================================================================
    # Section 1 — Route Opportunity Cards (Top 5)
    # =========================================================================
    _render_route_cards()

    st.divider()

    # =========================================================================
    # Section 2 — World Map
    # =========================================================================
    _section_title(
        "Emerging Routes World Map",
        (
            "Traditional routes (gray). Emerging routes colour-coded by status. "
            "Globe rotated north to highlight Arctic passages. "
            "Status: "
            + "  ".join(
                "<b style=\"color:" + c + "\">" + s + "</b>"
                for s, c in _STATUS_COLOR.items()
            )
        ),
    )
    _render_world_map()

    st.divider()

    # =========================================================================
    # Section 2b — Opportunity Scoring Bars (3 bars per route)
    # =========================================================================
    _render_scoring_bars()

    st.divider()

    # =========================================================================
    # Section 3 — Opportunity Scoring Breakdown (Radar)
    # =========================================================================
    _section_title(
        "Opportunity Scoring Breakdown",
        (
            "Five-axis radar scoring each top route on trade growth, demand imbalance, "
            "infrastructure readiness, geopolitical clarity, and rate upside potential. "
            "Larger polygon = stronger opportunity."
        ),
    )
    _render_opportunity_scoring()

    st.divider()

    # =========================================================================
    # Section 4 — Route Performance Comparison Matrix
    # =========================================================================
    _section_title(
        "Route Performance Comparison Matrix",
        (
            "Routes (Y-axis) vs six metrics (X-axis). "
            "Green = better than traditional alternative; Red = worse. "
            "Hover for exact values."
        ),
    )
    _render_comparison_matrix()

    st.divider()

    # =========================================================================
    # Section 5 — Emerging vs Established + Time Horizon
    # =========================================================================
    _section_title(
        "Emerging vs Established Routes — Rate Premium & Time Horizon",
        (
            "Freight rate index (Suez baseline = 100) for established and emerging routes. "
            "Below: short / medium / long-term opportunity classification."
        ),
    )
    _render_established_comparison()

    st.divider()

    # =========================================================================
    # Section 6 — Arctic Route Tracker
    # =========================================================================
    _section_title(
        "Arctic Route Tracker — Northern Sea Route (NSR)",
        (
            "Annual vessel count trend 2015-2026 | Seasonal availability calendar | "
            "Arctic sea ice extent (synthetic NSIDC-style) | Break-even analysis | "
            "Western carrier exits"
        ),
    )

    col_a, col_b = st.columns([1, 3])
    with col_a:
        st.markdown(
            _card(
                "<div style=\"font-size:0.78rem; color:" + C_TEXT2
                + "; line-height:1.6\">"
                "<b style=\"color:" + C_TEXT + "\">NSR at a glance</b><br><br>"
                + _kpi_mini("Distance (nm)", "12,800", C_ARCTIC) + "<br>"
                + _kpi_mini("vs Suez saving", "8,200 nm / 39%", C_HIGH) + "<br>"
                + _kpi_mini("Summer transit", "~19 days", C_WARN) + "<br>"
                + _kpi_mini("Current vessels/yr", "~45", C_TEXT2) + "<br>"
                + _kpi_mini("2030 projection", "210+", C_ORANGE)
                + "</div>",
                border_color="rgba(56,189,248,0.30)",
            ),
            unsafe_allow_html=True,
        )
    with col_b:
        st.markdown(
            "<div style=\"font-size:0.75rem; color:" + C_TEXT3
            + "; margin-bottom:6px; text-transform:uppercase; letter-spacing:0.06em\">"
            "Annual Vessel Count 2015-2026 (+ 2030 Projection)"
            "</div>",
            unsafe_allow_html=True,
        )
        _render_arctic_tracker(freight_rate)

    st.divider()

    # =========================================================================
    # Section 7 — Red Sea Rerouting Impact
    # =========================================================================
    _section_title(
        "Red Sea Crisis — Cape of Good Hope Rerouting Impact",
        (
            "Houthi attacks began December 2023. 60%+ of Asia-Europe traffic "
            "rerouted via Cape of Good Hope by mid-2024. Rate, capacity, and "
            "scenario analysis."
        ),
    )
    _render_red_sea_rerouting()

    st.divider()

    # =========================================================================
    # Section 8 — Emerging Market Corridor Growth
    # =========================================================================
    _section_title(
        "Emerging Market Trade Corridor Growth (2025-2030 CAGR)",
        (
            "Projected compound annual growth rate for non-traditional trade corridors. "
            "India subcontinent, East Africa, and Central Asia TITR are the fastest-growing."
        ),
    )
    _render_emerging_market_growth()
