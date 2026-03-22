"""
Deep Dive Tab — Research Analyst view: route + commodity analysis.

Sections
--------
1. Deep Dive Selector       (route + commodity dropdowns)
2. Route Analysis Card      (rates, carriers, history chart, capacity changes)
3. Commodity Flow Analysis  (production map, trade flows, seasonality, correlation)
4. Supply Chain Pressure    (congestion, inland, labor, equipment — rated LOW/MOD/HIGH)
5. Shipper Intelligence     (top BCOs, contract vs spot, rate strategies)
6. Analyst Commentary       (bull/bear/base/watchpoints)
7. Similar Route Comparison (mini table)

Function signature:
    render(route_results=None, freight_data=None, port_results=None, insights=None)
"""
from __future__ import annotations

import datetime
import random
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Palette ────────────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

# ── Static reference data ──────────────────────────────────────────────────────
ROUTES = {
    "Asia-Europe": {
        "origin": "Shanghai",
        "dest": "Rotterdam",
        "nm": 11_800,
        "transit_days": 28,
        "weekly_services": 14,
        "carriers_active": 9,
        "base_rate": 2_450,
        "rate_pct": 62,
        "carriers": [
            ("MSC", 28.4), ("Maersk", 22.1), ("CMA CGM", 18.7),
            ("COSCO", 14.2), ("Evergreen", 8.6),
        ],
        "capacity_changes": [
            "MSC blank sailing: W14 (Apr 3)",
            "Maersk AE-1 newbuild deployment: May 2026",
            "Gemini alliance capacity uplift +8% Q2",
        ],
        "similar": [
            ("Asia-MED", 2_180, 13_200, -11),
            ("Asia-UKCI", 2_310, 12_400, -5),
            ("NE Passage (summer)", 1_900, 10_200, -22),
        ],
    },
    "Transpacific EB": {
        "origin": "Yantian",
        "dest": "Los Angeles",
        "nm": 6_470,
        "transit_days": 14,
        "weekly_services": 18,
        "carriers_active": 11,
        "base_rate": 3_210,
        "rate_pct": 74,
        "carriers": [
            ("COSCO", 24.5), ("Evergreen", 18.3), ("Yang Ming", 16.1),
            ("ONE", 14.8), ("MSC", 11.2),
        ],
        "capacity_changes": [
            "Peak season surge program: Jun–Sep",
            "Yang Ming TP-6 newbuild: Aug 2026",
            "ILWU contract renewal uncertainty — Q3 risk",
        ],
        "similar": [
            ("TP via Panama (Alteration)", 3_050, 7_200, -5),
            ("Transpacific WB", 2_800, 6_470, -13),
            ("Asia-USGC via Panama", 3_800, 9_100, +18),
        ],
    },
    "Transpacific WB": {
        "origin": "Los Angeles",
        "dest": "Yantian",
        "nm": 6_470,
        "transit_days": 14,
        "weekly_services": 14,
        "carriers_active": 9,
        "base_rate": 780,
        "rate_pct": 28,
        "carriers": [
            ("Evergreen", 26.0), ("COSCO", 22.4), ("ONE", 18.2),
            ("Yang Ming", 17.6), ("Maersk", 8.8),
        ],
        "capacity_changes": [
            "Repositioning empties — demand soft",
            "Westbound rates near floor; minimal blanking pressure",
            "No major newbuilds on WB rotation Q2",
        ],
        "similar": [
            ("Transpacific EB", 3_210, 6_470, +312),
            ("USGC-Asia via Panama", 850, 9_100, +9),
            ("Europe-Asia backhaul", 620, 11_800, -21),
        ],
    },
    "Asia-USGC": {
        "origin": "Busan",
        "dest": "Houston",
        "nm": 9_100,
        "transit_days": 22,
        "weekly_services": 8,
        "carriers_active": 7,
        "base_rate": 3_800,
        "rate_pct": 68,
        "carriers": [
            ("CMA CGM", 29.1), ("MSC", 24.3), ("COSCO", 18.5),
            ("Hapag-Lloyd", 15.2), ("Evergreen", 7.6),
        ],
        "capacity_changes": [
            "Panama Canal water levels stable — no surcharges",
            "Hapag-Lloyd joining new Asia-Gulf loop Q3",
            "Suez risk re-routing adding ~7 days for some strings",
        ],
        "similar": [
            ("Asia-USEC via Suez", 4_100, 10_800, +8),
            ("Asia-USEC via Panama", 3_950, 9_600, +4),
            ("Asia-Europe", 2_450, 11_800, -36),
        ],
    },
    "Intra-Asia": {
        "origin": "Shanghai",
        "dest": "Singapore",
        "nm": 2_300,
        "transit_days": 6,
        "weekly_services": 32,
        "carriers_active": 18,
        "base_rate": 480,
        "rate_pct": 41,
        "carriers": [
            ("PIL", 22.5), ("RCL", 18.3), ("SITC", 16.7),
            ("IRISL", 12.1), ("CMA CGM", 9.4),
        ],
        "capacity_changes": [
            "New PIL loop commencing Apr 2026",
            "SITC capacity expansion +15% H2",
            "Intra-Asia demand soft — NE Asian manufacturing slowdown",
        ],
        "similar": [
            ("Intra-Asia North", 520, 1_800, +8),
            ("SE Asia Feeder", 380, 1_400, -21),
            ("China-Japan", 410, 1_100, -15),
        ],
    },
    "Transatlantic": {
        "origin": "Hamburg",
        "dest": "New York",
        "nm": 3_800,
        "transit_days": 10,
        "weekly_services": 10,
        "carriers_active": 7,
        "base_rate": 1_650,
        "rate_pct": 55,
        "carriers": [
            ("Hapag-Lloyd", 31.2), ("MSC", 24.5), ("Maersk", 20.1),
            ("CMA CGM", 14.8), ("ZIM", 6.3),
        ],
        "capacity_changes": [
            "Transatlantic trade buoyed by US import front-running",
            "Hapag-Lloyd TA-1 frequency increase Q2",
            "Tariff uncertainty driving erratic bookings patterns",
        ],
        "similar": [
            ("USEC-Europe backhaul", 820, 3_800, -50),
            ("N. Europe-Canada", 1_480, 3_400, -10),
            ("Med-USEC", 1_720, 4_900, +4),
        ],
    },
}

COMMODITIES = {
    "Container (general)": {
        "description": "General containerized merchandise",
        "top_exporters": [
            ("China", 35.2, 121.4, 31.2),
            ("South Korea", 8.4, 37.5, -5.1),
            ("Germany", 7.9, 9.8, 2.3),
            ("Vietnam", 6.2, 16.7, 18.4),
            ("USA", 5.8, 37.2, -3.6),
        ],
        "trade_flows": [
            ("China", "USA", 22.4),
            ("China", "Germany", 8.7),
            ("Germany", "USA", 5.3),
            ("South Korea", "USA", 4.8),
            ("Vietnam", "USA", 6.1),
        ],
        "elasticity": 0.82,
        "seasonality": [72, 68, 75, 82, 88, 91, 94, 97, 100, 88, 76, 65],
        "price_corr": 0.61,
        "avg_price": 2_100,
    },
    "Electronics": {
        "description": "Consumer electronics, semiconductors, components",
        "top_exporters": [
            ("Taiwan", 28.6, 68.3, 12.4),
            ("South Korea", 22.1, 51.2, 6.7),
            ("China", 19.4, 88.5, -2.1),
            ("Japan", 11.2, 31.5, -4.3),
            ("Vietnam", 8.4, 22.1, 24.6),
        ],
        "trade_flows": [
            ("Taiwan", "USA", 14.2),
            ("South Korea", "USA", 11.8),
            ("China", "Europe", 9.4),
            ("Japan", "Europe", 5.7),
            ("Vietnam", "USA", 7.3),
        ],
        "elasticity": 0.94,
        "seasonality": [65, 62, 70, 78, 85, 82, 88, 95, 100, 92, 98, 80],
        "price_corr": 0.73,
        "avg_price": 3_800,
    },
    "Automotive": {
        "description": "Finished vehicles and auto parts",
        "top_exporters": [
            ("Germany", 24.5, 4.2, 2.1),
            ("Japan", 19.8, 3.8, -3.4),
            ("South Korea", 14.3, 2.9, 8.6),
            ("China", 12.7, 2.5, 42.1),
            ("Mexico", 8.4, 1.8, 5.3),
        ],
        "trade_flows": [
            ("Germany", "USA", 8.4),
            ("Japan", "USA", 7.2),
            ("South Korea", "USA", 5.8),
            ("China", "Europe", 4.1),
            ("Mexico", "USA", 6.3),
        ],
        "elasticity": 0.68,
        "seasonality": [70, 72, 80, 85, 88, 82, 75, 78, 88, 92, 85, 60],
        "price_corr": 0.44,
        "avg_price": 28_000,
    },
    "Chemicals": {
        "description": "Bulk and specialty chemicals",
        "top_exporters": [
            ("USA", 18.4, 142.3, 3.2),
            ("Germany", 16.2, 118.5, 1.8),
            ("China", 14.8, 126.7, 6.4),
            ("Saudi Arabia", 11.3, 85.2, 4.1),
            ("Belgium", 8.7, 72.4, 0.9),
        ],
        "trade_flows": [
            ("USA", "China", 12.3),
            ("Germany", "USA", 9.8),
            ("Saudi Arabia", "Asia", 8.4),
            ("China", "SE Asia", 7.2),
            ("Belgium", "Asia", 5.9),
        ],
        "elasticity": 0.52,
        "seasonality": [88, 84, 90, 92, 95, 90, 88, 86, 92, 96, 94, 82],
        "price_corr": 0.38,
        "avg_price": 1_250,
    },
    "Iron Ore": {
        "description": "Iron ore for steel production",
        "top_exporters": [
            ("Australia", 58.4, 920.1, 1.2),
            ("Brazil", 24.8, 390.4, 3.8),
            ("South Africa", 4.2, 66.3, -1.4),
            ("Canada", 3.1, 48.7, 0.6),
            ("India", 2.8, 44.2, -8.3),
        ],
        "trade_flows": [
            ("Australia", "China", 48.2),
            ("Brazil", "China", 20.4),
            ("Australia", "Japan", 7.8),
            ("Brazil", "Europe", 5.4),
            ("South Africa", "China", 3.8),
        ],
        "elasticity": 0.91,
        "seasonality": [90, 88, 95, 100, 98, 95, 88, 86, 94, 96, 92, 85],
        "price_corr": 0.88,
        "avg_price": 108,
    },
    "Coking Coal": {
        "description": "Metallurgical coal for steel making",
        "top_exporters": [
            ("Australia", 54.2, 182.4, -2.1),
            ("USA", 18.6, 62.5, 4.3),
            ("Canada", 12.4, 41.7, 1.8),
            ("Russia", 8.3, 27.9, -12.6),
            ("Mongolia", 4.1, 13.8, 18.4),
        ],
        "trade_flows": [
            ("Australia", "China", 28.4),
            ("Australia", "India", 18.6),
            ("USA", "Europe", 9.2),
            ("Canada", "Japan", 7.8),
            ("Mongolia", "China", 5.4),
        ],
        "elasticity": 0.78,
        "seasonality": [95, 88, 92, 86, 84, 80, 82, 88, 94, 98, 100, 96],
        "price_corr": 0.82,
        "avg_price": 245,
    },
    "Thermal Coal": {
        "description": "Steam coal for power generation",
        "top_exporters": [
            ("Indonesia", 42.8, 580.3, 6.2),
            ("Australia", 22.4, 303.5, -4.8),
            ("Russia", 14.6, 197.8, -8.4),
            ("South Africa", 9.2, 124.6, 2.1),
            ("Colombia", 6.8, 92.1, 1.4),
        ],
        "trade_flows": [
            ("Indonesia", "China", 28.4),
            ("Indonesia", "India", 18.6),
            ("Australia", "Japan", 14.2),
            ("Russia", "Europe", 8.4),
            ("Colombia", "Europe", 6.1),
        ],
        "elasticity": 0.65,
        "seasonality": [100, 92, 82, 72, 68, 70, 74, 78, 84, 88, 95, 98],
        "price_corr": 0.71,
        "avg_price": 128,
    },
    "Grain": {
        "description": "Wheat, corn, soybeans, and other grains",
        "top_exporters": [
            ("USA", 24.8, 142.6, -3.4),
            ("Brazil", 22.4, 128.8, 12.6),
            ("Argentina", 14.2, 81.5, 8.4),
            ("Australia", 11.8, 67.8, -6.2),
            ("Ukraine", 8.4, 48.2, -22.4),
        ],
        "trade_flows": [
            ("USA", "Asia", 32.4),
            ("Brazil", "China", 28.6),
            ("Argentina", "Asia", 16.4),
            ("Australia", "SE Asia", 12.8),
            ("Ukraine", "MENA", 8.2),
        ],
        "elasticity": 0.44,
        "seasonality": [60, 55, 65, 72, 80, 95, 100, 98, 88, 78, 68, 62],
        "price_corr": 0.58,
        "avg_price": 210,
    },
    "Crude Oil": {
        "description": "Crude petroleum for refining",
        "top_exporters": [
            ("Saudi Arabia", 18.4, 2_840.6, 1.2),
            ("Russia", 14.8, 2_284.4, -6.4),
            ("Iraq", 9.2, 1_419.6, 3.8),
            ("UAE", 7.6, 1_172.8, 2.4),
            ("USA", 6.8, 1_048.4, 18.6),
        ],
        "trade_flows": [
            ("Saudi Arabia", "China", 8.4),
            ("Russia", "China", 7.2),
            ("Saudi Arabia", "India", 5.8),
            ("Iraq", "India", 4.6),
            ("USA", "Europe", 4.1),
        ],
        "elasticity": 0.32,
        "seasonality": [88, 82, 86, 90, 92, 88, 84, 86, 90, 94, 96, 92],
        "price_corr": 0.79,
        "avg_price": 78,
    },
    "LNG": {
        "description": "Liquefied natural gas",
        "top_exporters": [
            ("Australia", 22.4, 82.4, 4.2),
            ("Qatar", 20.8, 76.5, 1.8),
            ("USA", 18.6, 68.4, 28.4),
            ("Russia", 9.2, 33.8, -4.6),
            ("Malaysia", 7.4, 27.2, -2.1),
        ],
        "trade_flows": [
            ("Australia", "Japan", 18.4),
            ("Qatar", "Europe", 14.2),
            ("USA", "Europe", 12.8),
            ("Australia", "China", 10.6),
            ("Qatar", "India", 8.4),
        ],
        "elasticity": 0.58,
        "seasonality": [100, 94, 82, 68, 58, 54, 56, 62, 72, 84, 92, 98],
        "price_corr": 0.84,
        "avg_price": 14.2,
    },
}

PRESSURE_TEMPLATES = {
    "origin_port": {
        "LOW":  ("Origin Port Congestion", "LOW", C_HIGH, "Avg wait: 0.4 days"),
        "MOD":  ("Origin Port Congestion", "MODERATE", C_MOD,  "Avg wait: 1.8 days"),
        "HIGH": ("Origin Port Congestion", "HIGH", C_LOW,  "Avg wait: 4.2 days"),
    },
    "dest_port": {
        "LOW":  ("Destination Port Congestion", "LOW", C_HIGH, "Vessels at anchor: 3"),
        "MOD":  ("Destination Port Congestion", "MODERATE", C_MOD,  "Vessels at anchor: 14"),
        "HIGH": ("Destination Port Congestion", "HIGH", C_LOW,  "Vessels at anchor: 31"),
    },
    "inland": {
        "LOW":  ("Inland Connectivity", "LOW RISK", C_HIGH, "Rail + truck capacity adequate"),
        "MOD":  ("Inland Connectivity", "MODERATE", C_MOD,  "Intermodal backlogs 2–5 days"),
        "HIGH": ("Inland Connectivity", "HIGH RISK", C_LOW,  "Severe inland dwell; avg 9 days"),
    },
    "labor": {
        "LOW":  ("Labor Situation", "STABLE", C_HIGH, "No disputes; contracts current"),
        "MOD":  ("Labor Situation", "WATCH", C_MOD,  "Negotiations ongoing; slowdowns possible"),
        "HIGH": ("Labor Situation", "RISK", C_LOW,  "Active dispute; work-to-rule in effect"),
    },
    "equipment": {
        "LOW":  ("Equipment Availability", "ADEQUATE", C_HIGH, "Box surplus on route"),
        "MOD":  ("Equipment Availability", "TIGHT", C_MOD,  "Lead time for 40HC: 5 days"),
        "HIGH": ("Equipment Availability", "CRITICAL", C_LOW,  "Acute shortage; 14-day lead time"),
    },
}

BCO_NAMES = [
    "Walmart", "Amazon", "IKEA", "Home Depot", "Target",
    "Apple", "Samsung", "Nike", "Ford Motor", "Tyson Foods",
    "Procter & Gamble", "Unilever", "3M", "Caterpillar", "John Deere",
]

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;margin:32px 0 20px">'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,rgba(255,255,255,0),{C_BORDER})"></div>'
        f'<span style="font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.14em;font-weight:700">{label}</span>'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,{C_BORDER},rgba(255,255,255,0))"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _card_open(extra_style: str = "") -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
        f'padding:20px 24px;margin-bottom:16px;{extra_style}">'
    )


def _badge(text: str, color: str) -> str:
    bg = _hex_rgba(color, 0.15)
    return (
        f'<span style="background:{bg};color:{color};border:1px solid {_hex_rgba(color,0.35)};'
        f'border-radius:6px;padding:2px 9px;font-size:0.67rem;font-weight:700;letter-spacing:0.06em">'
        f'{text}</span>'
    )


def _pressure_level(route_name: str, commodity: str, rng: random.Random) -> dict[str, str]:
    seed_val = hash(route_name + commodity) % 1000
    rng2 = random.Random(seed_val)
    levels = ["LOW", "MOD", "HIGH"]
    weights = [0.35, 0.40, 0.25]
    return {
        "origin_port": rng2.choices(levels, weights)[0],
        "dest_port":   rng2.choices(levels, weights)[0],
        "inland":      rng2.choices(levels, weights)[0],
        "labor":       rng2.choices(levels, weights)[0],
        "equipment":   rng2.choices(levels, weights)[0],
    }


def _seeded_rate_history(base_rate: float, route_name: str) -> pd.DataFrame:
    rng = random.Random(hash(route_name) % 99999)
    today = datetime.date.today()
    dates = pd.date_range(end=today, periods=52, freq="W")
    rates = [base_rate]
    for _ in range(51):
        delta = rng.gauss(0, base_rate * 0.04)
        rates.append(max(base_rate * 0.3, rates[-1] + delta))
    return pd.DataFrame({"date": dates, "rate": rates})


def _seeded_bcos(route_name: str, commodity: str, n: int = 10) -> list[dict]:
    rng = random.Random(hash(route_name + commodity) % 77777)
    pool = BCO_NAMES[:]
    rng.shuffle(pool)
    out = []
    for i, name in enumerate(pool[:n]):
        vol = rng.randint(2_000, 80_000)
        spot_pct = rng.randint(15, 75)
        contract_months = rng.choice([6, 12, 12, 24, 24, 36])
        strategy = rng.choice([
            "Leans spot — opportunistic buyer",
            "Mostly contracted — rate stability priority",
            "Hybrid — 60/40 contract/spot blend",
            "Annual tender — awards Q4",
            "Spot preferred — flexible supply chain",
        ])
        out.append({
            "rank": i + 1,
            "name": name,
            "volume_teu": vol,
            "spot_pct": spot_pct,
            "contract_months": contract_months,
            "strategy": strategy,
        })
    return out


# ── Section renderers ──────────────────────────────────────────────────────────

def _render_selector() -> tuple[str, str]:
    """Section 1 — route + commodity dropdowns."""
    st.markdown(
        f'<div style="{_card_open()[5:]}">'
        f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.12em;font-weight:700;margin-bottom:14px">Deep Dive Selector</div>',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    with c1:
        route = st.selectbox("Route", list(ROUTES.keys()), key="dd_route")
    with c2:
        commodity = st.selectbox("Commodity", list(COMMODITIES.keys()), key="dd_commodity")
    st.markdown("</div>", unsafe_allow_html=True)
    return route, commodity


def _render_route_card(route_name: str) -> None:
    """Section 2 — Route Analysis Card."""
    try:
        rd = ROUTES[route_name]
        pct = rd["rate_pct"]
        pct_color = C_HIGH if pct < 40 else (C_MOD if pct < 70 else C_LOW)
        df = _seeded_rate_history(rd["base_rate"], route_name)

        _divider("Route Analysis")

        # Header row
        st.markdown(
            f'{_card_open()}'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">'
            f'<div>'
            f'<div style="font-size:1.05rem;font-weight:800;color:{C_TEXT}">'
            f'{rd["origin"]} → {rd["dest"]}</div>'
            f'<div style="font-size:0.75rem;color:{C_TEXT3};margin-top:4px">'
            f'{rd["nm"]:,} nm &nbsp;·&nbsp; {rd["transit_days"]} day transit'
            f'&nbsp;·&nbsp; {rd["weekly_services"]} weekly services'
            f'&nbsp;·&nbsp; {rd["carriers_active"]} active carriers</div>'
            f'</div>'
            f'<div style="text-align:right">'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.1em">Current Rate</div>'
            f'<div style="font-size:1.5rem;font-weight:900;color:{C_TEXT}">${rd["base_rate"]:,}</div>'
            f'<div style="font-size:0.7rem;color:{pct_color};font-weight:700">'
            f'{pct}th percentile (12-mo)</div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Carrier table
        header = (
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.1em;font-weight:700;margin-bottom:12px">Top 5 Carriers by Capacity Share</div>'
        )
        rows = ""
        for carrier, share in rd["carriers"]:
            bar_w = int(share * 2.5)
            rows += (
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
                f'<div style="width:110px;font-size:0.75rem;color:{C_TEXT};font-weight:600">{carrier}</div>'
                f'<div style="flex:1;background:rgba(255,255,255,0.05);border-radius:4px;height:6px">'
                f'<div style="background:{C_ACCENT};width:{bar_w}%;height:100%;border-radius:4px"></div>'
                f'</div>'
                f'<div style="width:42px;text-align:right;font-size:0.75rem;color:{C_ACCENT};font-weight:700">'
                f'{share}%</div>'
                f'</div>'
            )
        st.markdown(
            f'{_card_open()}{header}{rows}</div>',
            unsafe_allow_html=True,
        )

        # Capacity changes
        changes_html = "".join(
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
            f'<div style="width:6px;height:6px;border-radius:50%;background:{C_MOD};flex-shrink:0"></div>'
            f'<div style="font-size:0.78rem;color:{C_TEXT2}">{c}</div>'
            f'</div>'
            for c in rd["capacity_changes"]
        )
        st.markdown(
            f'{_card_open()}'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.1em;font-weight:700;margin-bottom:12px">Upcoming Capacity Changes</div>'
            f'{changes_html}</div>',
            unsafe_allow_html=True,
        )

        # Rate history chart
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["rate"],
            mode="lines", name="Freight Rate",
            line=dict(color=C_ACCENT, width=2),
            fill="tozeroy",
            fillcolor=_hex_rgba(C_ACCENT, 0.08),
        ))
        fig.add_hline(
            y=df["rate"].mean(), line_dash="dot",
            line_color=_hex_rgba(C_MOD, 0.6), line_width=1,
            annotation_text="12-mo avg", annotation_font_color=C_MOD,
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=28, b=0), height=220,
            font=dict(color=C_TEXT2, size=11),
            title=dict(text="Rate History — 52 Weeks", font=dict(color=C_TEXT2, size=12), x=0),
            xaxis=dict(showgrid=False, color=C_TEXT3),
            yaxis=dict(showgrid=True, gridcolor=C_BORDER, color=C_TEXT3, tickprefix="$"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    except Exception:
        logger.exception("_render_route_card failed")
        st.warning("Route analysis unavailable.")


def _render_commodity_flow(commodity: str) -> None:
    """Section 3 — Commodity Flow Analysis."""
    try:
        cd = COMMODITIES[commodity]
        _divider("Commodity Flow Analysis")

        # Production map
        exporters = cd["top_exporters"]
        country_names = [e[0] for e in exporters]
        volumes = [e[2] for e in exporters]
        yoy = [e[3] for e in exporters]

        fig_map = go.Figure(go.Scattergeo(
            locationmode="country names",
            locations=country_names,
            mode="markers+text",
            text=country_names,
            textposition="top center",
            marker=dict(
                size=[max(10, v / max(volumes) * 60) for v in volumes],
                color=[C_HIGH if y >= 0 else C_LOW for y in yoy],
                opacity=0.85,
                line=dict(width=1, color=C_BORDER),
            ),
            hovertemplate="<b>%{location}</b><br>Volume: %{customdata[0]:,.0f} MT<br>YoY: %{customdata[1]:+.1f}%<extra></extra>",
            customdata=list(zip(volumes, yoy)),
        ))
        fig_map.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=36, b=0), height=280,
            title=dict(text=f"{commodity} — Global Production (Top Exporters)", font=dict(color=C_TEXT2, size=12), x=0),
            geo=dict(
                bgcolor="rgba(0,0,0,0)",
                showframe=False, showcoastlines=True,
                coastlinecolor=C_BORDER, landcolor="#1a2235",
                showocean=True, oceancolor=C_SURFACE,
                showlakes=False, showcountries=True,
                countrycolor=C_BORDER,
            ),
            font=dict(color=C_TEXT2),
        )
        st.plotly_chart(fig_map, use_container_width=True, config={"displayModeBar": False})

        # Trade flows table + elasticity
        c1, c2 = st.columns([3, 2])
        with c1:
            rows_html = "".join(
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:8px 0;border-bottom:1px solid {C_BORDER}">'
                f'<div style="font-size:0.77rem;color:{C_TEXT};font-weight:600">'
                f'{tf[0]} → {tf[1]}</div>'
                f'<div style="font-size:0.77rem;color:{C_ACCENT};font-weight:700">'
                f'{tf[2]:.1f}M MT/yr</div>'
                f'</div>'
                for tf in cd["trade_flows"]
            )
            st.markdown(
                f'{_card_open()}'
                f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.1em;font-weight:700;margin-bottom:12px">Top 5 Trade Flows</div>'
                f'{rows_html}</div>',
                unsafe_allow_html=True,
            )
        with c2:
            el = cd["elasticity"]
            el_color = C_HIGH if el < 0.5 else (C_MOD if el < 0.8 else C_LOW)
            corr = cd["price_corr"]
            corr_color = C_HIGH if corr > 0.7 else (C_MOD if corr > 0.4 else C_TEXT3)
            st.markdown(
                f'{_card_open()}'
                f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.1em;font-weight:700;margin-bottom:16px">Shipping Metrics</div>'
                f'<div style="margin-bottom:14px">'
                f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-bottom:4px">Demand Elasticity</div>'
                f'<div style="font-size:1.3rem;font-weight:900;color:{el_color}">{el:.2f}</div>'
                f'<div style="font-size:0.67rem;color:{C_TEXT3}">vs production change</div>'
                f'</div>'
                f'<div style="margin-bottom:14px">'
                f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-bottom:4px">Price × Freight Corr.</div>'
                f'<div style="font-size:1.3rem;font-weight:900;color:{corr_color}">{corr:.2f}</div>'
                f'<div style="font-size:0.67rem;color:{C_TEXT3}">Pearson r (24-mo)</div>'
                f'</div>'
                f'<div>'
                f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-bottom:4px">Avg Commodity Price</div>'
                f'<div style="font-size:1.1rem;font-weight:800;color:{C_TEXT}">${cd["avg_price"]:,}</div>'
                f'<div style="font-size:0.67rem;color:{C_TEXT3}">/MT or unit</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Seasonal bar chart (HTML)
        sea = cd["seasonality"]
        max_s = max(sea)
        bars_html = "".join(
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px;flex:1">'
            f'<div style="font-size:0.62rem;color:{C_ACCENT};font-weight:700">{v}</div>'
            f'<div style="width:100%;background:rgba(255,255,255,0.05);border-radius:3px 3px 0 0;'
            f'height:80px;display:flex;align-items:flex-end">'
            f'<div style="width:100%;background:linear-gradient(180deg,{C_ACCENT},{_hex_rgba(C_ACCENT,0.4)});'
            f'height:{int(v/max_s*100)}%;border-radius:3px 3px 0 0"></div>'
            f'</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3}">{MONTHS[i]}</div>'
            f'</div>'
            for i, v in enumerate(sea)
        )
        st.markdown(
            f'{_card_open()}'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.1em;font-weight:700;margin-bottom:16px">Seasonal Volume Index</div>'
            f'<div style="display:flex;gap:6px;align-items:flex-end">{bars_html}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    except Exception:
        logger.exception("_render_commodity_flow failed")
        st.warning("Commodity flow analysis unavailable.")


def _render_pressure_points(route_name: str, commodity: str) -> None:
    """Section 4 — Supply Chain Pressure Points."""
    try:
        _divider("Supply Chain Pressure Points")
        rng = random.Random(hash(route_name + commodity) % 55555)
        levels = _pressure_level(route_name, commodity, rng)

        cols = st.columns(5)
        keys = ["origin_port", "dest_port", "inland", "labor", "equipment"]
        for col, key in zip(cols, keys):
            lvl = levels[key]
            label, rating, color, metric = PRESSURE_TEMPLATES[key][lvl]
            bg = _hex_rgba(color, 0.10)
            border = _hex_rgba(color, 0.30)
            with col:
                st.markdown(
                    f'<div style="background:{bg};border:1px solid {border};'
                    f'border-radius:10px;padding:14px 12px;text-align:center">'
                    f'<div style="font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;'
                    f'letter-spacing:0.1em;font-weight:700;margin-bottom:8px">{label}</div>'
                    f'<div style="font-size:1rem;font-weight:900;color:{color};margin-bottom:6px">{rating}</div>'
                    f'<div style="font-size:0.68rem;color:{C_TEXT2}">{metric}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception:
        logger.exception("_render_pressure_points failed")
        st.warning("Pressure points unavailable.")


def _render_shipper_intel(route_name: str, commodity: str) -> None:
    """Section 5 — Shipper Intelligence."""
    try:
        _divider("Shipper Intelligence")
        bcos = _seeded_bcos(route_name, commodity)

        header_html = (
            f'<div style="display:grid;grid-template-columns:28px 1fr 90px 80px 90px 1fr;'
            f'gap:8px;padding:6px 0;border-bottom:1px solid {C_BORDER};margin-bottom:8px">'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700">#</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700">BCO</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700;text-align:right">Vol (TEU/yr)</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700;text-align:center">Spot %</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700;text-align:center">Contract</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700">Strategy</div>'
            f'</div>'
        )
        rows_html = ""
        for b in bcos:
            spot = b["spot_pct"]
            spot_color = C_LOW if spot > 60 else (C_MOD if spot > 35 else C_HIGH)
            rows_html += (
                f'<div style="display:grid;grid-template-columns:28px 1fr 90px 80px 90px 1fr;'
                f'gap:8px;padding:7px 0;border-bottom:1px solid {_hex_rgba(C_BORDER,0.4)}'
                f';align-items:center">'
                f'<div style="font-size:0.72rem;color:{C_TEXT3}">{b["rank"]}</div>'
                f'<div style="font-size:0.75rem;color:{C_TEXT};font-weight:700">{b["name"]}</div>'
                f'<div style="font-size:0.75rem;color:{C_TEXT2};text-align:right">'
                f'{b["volume_teu"]:,}</div>'
                f'<div style="text-align:center">{_badge(f"{spot}%", spot_color)}</div>'
                f'<div style="font-size:0.72rem;color:{C_TEXT2};text-align:center">'
                f'{b["contract_months"]}mo</div>'
                f'<div style="font-size:0.68rem;color:{C_TEXT3}">{b["strategy"]}</div>'
                f'</div>'
            )
        st.markdown(
            f'{_card_open()}'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.1em;font-weight:700;margin-bottom:12px">'
            f'Top 10 Beneficial Cargo Owners — {commodity} on {route_name}</div>'
            f'{header_html}{rows_html}</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("_render_shipper_intel failed")
        st.warning("Shipper intelligence unavailable.")


def _render_analyst_commentary(route_name: str, commodity: str) -> None:
    """Section 6 — Analyst Commentary."""
    try:
        _divider("Analyst Commentary")
        rd = ROUTES[route_name]
        pct = rd["rate_pct"]
        rate = rd["base_rate"]
        cd = COMMODITIES[commodity]

        # Generate bull/bear/base from data signals
        high_pct = pct >= 65
        low_pct = pct <= 35

        bull_1 = (
            f"Peak season demand surge expected to push {route_name} rates +18–25% by Q3"
            if high_pct else
            f"Rates at {pct}th percentile leave significant upside; any demand shock could add $400–600/TEU"
        )
        bull_2 = (
            f"{commodity} production growth of {cd['top_exporters'][0][3]:+.1f}% in top exporter "
            f"drives incremental shipping demand through H2"
        )
        bull_3 = (
            f"Vessel supply growth decelerating as orderbook deliveries pushed to 2027; "
            f"effective capacity may tighten 3–5% YoY"
        )

        bear_1 = (
            f"Macroeconomic slowdown in key consumer markets risks demand contraction of 5–8%"
        )
        bear_2 = (
            f"New {rd['carriers'][0][0]} loop deployment adds ~{rd['weekly_services'] // 3} "
            f"weekly sailings — capacity pressure on rates"
        )
        bear_3 = (
            f"{commodity} import substitution trends reducing long-haul shipment volumes; "
            f"nearshoring accelerating"
        )

        base_case = (
            f"Rates expected to consolidate near current levels (${rate:,}/TEU) through Q2, "
            f"with modest seasonal uplift of 8–12% in Q3. "
            f"{commodity} flows remain resilient but below 2024 peaks. "
            f"Watch carrier discipline on blank sailings as the key swing factor."
        )

        watchpoints = [
            f"{route_name} spot rate vs 4-week moving average (threshold: ±15%)",
            f"{commodity} PMI in key origin markets (current signal: {cd['elasticity']:.2f} elasticity)",
            f"Blank sailing announcements from {rd['carriers'][0][0]} and {rd['carriers'][1][0]}",
        ]

        def _case_block(title: str, color: str, items: list[str] | str) -> str:
            bg = _hex_rgba(color, 0.08)
            border = _hex_rgba(color, 0.25)
            if isinstance(items, list):
                content = "".join(
                    f'<div style="display:flex;gap:8px;margin-bottom:6px">'
                    f'<div style="color:{color};font-weight:900;margin-top:1px">▸</div>'
                    f'<div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.5">{it}</div>'
                    f'</div>'
                    for it in items
                )
            else:
                content = f'<div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.6">{items}</div>'
            return (
                f'<div style="background:{bg};border-left:3px solid {color};'
                f'border-radius:0 8px 8px 0;padding:14px 16px;margin-bottom:12px">'
                f'<div style="font-size:0.67rem;color:{color};font-weight:800;'
                f'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">{title}</div>'
                f'{content}</div>'
            )

        html = (
            f'{_card_open()}'
            f'{_case_block("Bull Case", C_HIGH, [bull_1, bull_2, bull_3])}'
            f'{_case_block("Bear Case", C_LOW, [bear_1, bear_2, bear_3])}'
            f'{_case_block("Base Case", C_ACCENT, base_case)}'
            f'{_case_block("Key Watchpoints", C_MOD, watchpoints)}'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)

    except Exception:
        logger.exception("_render_analyst_commentary failed")
        st.warning("Analyst commentary unavailable.")


def _render_similar_routes(route_name: str) -> None:
    """Section 7 — Similar Route Comparisons."""
    try:
        _divider("Similar Route Comparisons")
        rd = ROUTES[route_name]
        similar = rd.get("similar", [])

        if not similar:
            st.info("No comparable routes configured.")
            return

        header_html = (
            f'<div style="display:grid;grid-template-columns:1fr 90px 90px 100px;'
            f'gap:8px;padding:6px 0;border-bottom:1px solid {C_BORDER};margin-bottom:8px">'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700">Route</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700;text-align:right">Rate ($/TEU)</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700;text-align:right">Distance (nm)</div>'
            f'<div style="font-size:0.62rem;color:{C_TEXT3};font-weight:700;text-align:center">vs Selected</div>'
            f'</div>'
        )

        # Selected route row
        sel_rate = rd["base_rate"]
        rows_html = (
            f'<div style="display:grid;grid-template-columns:1fr 90px 90px 100px;'
            f'gap:8px;padding:8px 0;border-bottom:1px solid {C_BORDER};'
            f'background:{_hex_rgba(C_ACCENT,0.07)};border-radius:6px;'
            f'padding-left:8px;align-items:center;margin-bottom:2px">'
            f'<div style="font-size:0.78rem;color:{C_TEXT};font-weight:800">'
            f'{route_name} <span style="color:{C_ACCENT};font-size:0.62rem">(selected)</span></div>'
            f'<div style="font-size:0.78rem;color:{C_TEXT};font-weight:700;text-align:right">${sel_rate:,}</div>'
            f'<div style="font-size:0.78rem;color:{C_TEXT2};text-align:right">{rd["nm"]:,}</div>'
            f'<div style="text-align:center">—</div>'
            f'</div>'
        )
        for name, rate, nm, diff in similar:
            diff_color = C_HIGH if diff < 0 else C_LOW
            diff_sign = "+" if diff > 0 else ""
            diff_label = f"{diff_sign}{diff}%" if isinstance(diff, (int, float)) else str(diff)
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1fr 90px 90px 100px;'
                f'gap:8px;padding:7px 0;padding-left:8px;border-bottom:1px solid '
                f'{_hex_rgba(C_BORDER,0.4)};align-items:center">'
                f'<div style="font-size:0.76rem;color:{C_TEXT2}">{name}</div>'
                f'<div style="font-size:0.76rem;color:{C_TEXT2};text-align:right">${rate:,}</div>'
                f'<div style="font-size:0.76rem;color:{C_TEXT3};text-align:right">{nm:,}</div>'
                f'<div style="text-align:center">{_badge(diff_label, diff_color)}</div>'
                f'</div>'
            )

        st.markdown(
            f'{_card_open()}'
            f'<div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;'
            f'letter-spacing:0.1em;font-weight:700;margin-bottom:12px">Route Benchmarking</div>'
            f'{header_html}{rows_html}</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("_render_similar_routes failed")
        st.warning("Similar routes comparison unavailable.")


# ── Entry point ────────────────────────────────────────────────────────────────

def render(
    route_results: Any = None,
    freight_data: Any = None,
    port_results: Any = None,
    insights: Any = None,
) -> None:
    """Render the Deep Dive research analyst tab."""
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{_hex_rgba(C_ACCENT,0.12)},rgba(0,0,0,0));'
            f'border:1px solid {_hex_rgba(C_ACCENT,0.25)};border-radius:14px;'
            f'padding:20px 26px;margin-bottom:24px">'
            f'<div style="display:flex;align-items:center;gap:14px">'
            f'<div style="font-size:1.5rem">🔬</div>'
            f'<div>'
            f'<div style="font-size:1.1rem;font-weight:900;color:{C_TEXT}">Deep Dive — Research Analyst View</div>'
            f'<div style="font-size:0.78rem;color:{C_TEXT3};margin-top:3px">'
            f'Select a route and commodity to generate comprehensive trade lane intelligence.</div>'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )

        # Section 1: selector
        route, commodity = _render_selector()

        # Section 2: route analysis
        _render_route_card(route)

        # Section 3: commodity flow
        _render_commodity_flow(commodity)

        # Section 4: pressure points
        _render_pressure_points(route, commodity)

        # Section 5: shipper intelligence
        _render_shipper_intel(route, commodity)

        # Section 6: analyst commentary
        _render_analyst_commentary(route, commodity)

        # Section 7: similar routes
        _render_similar_routes(route)

    except Exception:
        logger.exception("tab_deep_dive render failed")
        st.error("Deep Dive tab encountered an unexpected error. Check logs.")
