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

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    wsj_market_table,
)


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
        "LOW":  ("Origin Port", "LOW", C_HIGH, "Avg wait: 0.4 days"),
        "MOD":  ("Origin Port", "MODERATE", C_MOD,  "Avg wait: 1.8 days"),
        "HIGH": ("Origin Port", "HIGH", C_LOW,  "Avg wait: 4.2 days"),
    },
    "dest_port": {
        "LOW":  ("Dest Port", "LOW", C_HIGH, "Anchor: 3"),
        "MOD":  ("Dest Port", "MODERATE", C_MOD,  "Anchor: 14"),
        "HIGH": ("Dest Port", "HIGH", C_LOW,  "Anchor: 31"),
    },
    "inland": {
        "LOW":  ("Inland", "LOW RISK", C_HIGH, "Rail/truck OK"),
        "MOD":  ("Inland", "MODERATE", C_MOD,  "Backlog 2–5d"),
        "HIGH": ("Inland", "HIGH RISK", C_LOW,  "Dwell 9d"),
    },
    "labor": {
        "LOW":  ("Labor", "STABLE", C_HIGH, "Contracts current"),
        "MOD":  ("Labor", "WATCH", C_MOD,  "Negotiations"),
        "HIGH": ("Labor", "RISK", C_LOW,  "Work-to-rule"),
    },
    "equipment": {
        "LOW":  ("Equipment", "ADEQUATE", C_HIGH, "Box surplus"),
        "MOD":  ("Equipment", "TIGHT", C_MOD,  "40HC: 5d lead"),
        "HIGH": ("Equipment", "CRITICAL", C_LOW,  "Acute shortage"),
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


def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _pressure_level(route_name: str, commodity: str) -> dict[str, str]:
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
    section_header("Deep Dive Selector", subtitle="Choose a trade lane and commodity")
    c1, c2 = st.columns(2)
    with c1:
        route = st.selectbox("Route", list(ROUTES.keys()), key="dd_route")
    with c2:
        commodity = st.selectbox("Commodity", list(COMMODITIES.keys()), key="dd_commodity")
    return route, commodity


def _render_route_card(route_name: str) -> None:
    try:
        rd = ROUTES[route_name]
        pct = rd["rate_pct"]
        pct_color = C_HIGH if pct < 40 else (C_MOD if pct < 70 else C_LOW)
        df = _seeded_rate_history(rd["base_rate"], route_name)

        section_divider("Route Analysis")
        section_header(
            f'{rd["origin"]} → {rd["dest"]}',
            subtitle=f'{rd["nm"]:,} nm · {rd["transit_days"]} day transit · '
                     f'{rd["weekly_services"]} weekly services · {rd["carriers_active"]} active carriers',
        )

        metric_card_row(
            [
                {"label": "Current Rate", "value": f"${rd['base_rate']:,}", "accent": C_ACCENT},
                {"label": "12-mo Percentile", "value": f"{pct}th", "accent": pct_color},
                {"label": "Transit Days", "value": str(rd["transit_days"]), "accent": C_TEXT2},
                {"label": "Weekly Services", "value": str(rd["weekly_services"]), "accent": C_TEXT2},
            ],
            columns=4,
        )

        section_header("Top 5 Carriers by Capacity Share")
        carrier_rows = []
        for carrier, share in rd["carriers"]:
            bar_w = int(share * 2.5)
            bar_html = (
                f'<div style="display:inline-block;width:110px;background:{C_BORDER};'
                f'border-radius:3px;height:6px;vertical-align:middle;margin-right:8px">'
                f'<div style="background:{C_ACCENT};width:{bar_w}%;height:100%;border-radius:3px"></div>'
                f'</div>'
            )
            carrier_rows.append([
                _sans(carrier, color=C_TEXT, weight=700),
                bar_html + _mono(f"{share}%", color=C_ACCENT),
            ])
        wsj_market_table(headers=["Carrier", "Share"], rows=carrier_rows)

        section_header("Upcoming Capacity Changes")
        cap_rows = [[_sans(c, color=C_TEXT2)] for c in rd["capacity_changes"]]
        wsj_market_table(headers=["Note"], rows=cap_rows)

        # Rate history chart
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["date"], y=df["rate"],
            mode="lines", name="Freight Rate",
            line=dict(color=C_ACCENT, width=2),
            fill="tozeroy",
            fillcolor=_hex_rgba(C_ACCENT, 0.12),
        ))
        fig.add_hline(
            y=df["rate"].mean(), line_dash="dot",
            line_color=_hex_rgba(C_MOD, 0.6), line_width=1,
            annotation_text="12-mo avg", annotation_font_color=C_MOD,
        )
        apply_dark_layout(fig, height=240, title="Rate History — 52 Weeks")
        fig.update_yaxes(tickprefix="$")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    except Exception:
        logger.exception("_render_route_card failed")
        st.warning("Route analysis unavailable.")


def _render_commodity_flow(commodity: str) -> None:
    try:
        cd = COMMODITIES[commodity]
        section_divider("Commodity Flow Analysis")

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
        apply_dark_layout(fig_map, height=320, title=f"{commodity} — Global Production (Top Exporters)")
        fig_map.update_layout(
            geo=dict(
                bgcolor=C_CARD,
                showframe=False, showcoastlines=True,
                coastlinecolor=C_BORDER, landcolor=C_SURFACE,
                showocean=True, oceancolor=C_CARD,
                showlakes=False, showcountries=True,
                countrycolor=C_BORDER,
            ),
        )
        st.plotly_chart(fig_map, use_container_width=True, config={"displayModeBar": False})

        c1, c2 = st.columns([3, 2])
        with c1:
            section_header("Top 5 Trade Flows")
            flow_rows = [
                [
                    _sans(f"{tf[0]} → {tf[1]}", color=C_TEXT, weight=600),
                    _mono(f"{tf[2]:.1f}M MT/yr", color=C_ACCENT),
                ]
                for tf in cd["trade_flows"]
            ]
            wsj_market_table(headers=["Route", "Volume"], rows=flow_rows)
        with c2:
            el = cd["elasticity"]
            el_color = C_HIGH if el < 0.5 else (C_MOD if el < 0.8 else C_LOW)
            corr = cd["price_corr"]
            corr_color = C_HIGH if corr > 0.7 else (C_MOD if corr > 0.4 else C_TEXT3)
            section_header("Shipping Metrics")
            metric_card_row(
                [
                    {"label": "Demand Elasticity", "value": f"{el:.2f}", "accent": el_color},
                    {"label": "Price × Freight r", "value": f"{corr:.2f}", "accent": corr_color},
                    {"label": "Avg Price", "value": f"${cd['avg_price']:,}", "accent": C_TEXT2},
                ],
                columns=1,
            )

        # Seasonality bar chart
        sea = cd["seasonality"]
        fig_sea = go.Figure(go.Bar(
            x=MONTHS, y=sea,
            marker=dict(color=C_ACCENT, opacity=0.85),
            text=[str(v) for v in sea], textposition="outside",
            textfont=dict(color=C_ACCENT, size=10),
            hovertemplate="%{x}: %{y}<extra></extra>",
        ))
        apply_dark_layout(fig_sea, height=240, title="Seasonal Volume Index")
        fig_sea.update_yaxes(range=[0, max(sea) * 1.15])
        st.plotly_chart(fig_sea, use_container_width=True, config={"displayModeBar": False})

    except Exception:
        logger.exception("_render_commodity_flow failed")
        st.warning("Commodity flow analysis unavailable.")


def _render_pressure_points(route_name: str, commodity: str) -> None:
    try:
        section_divider("Supply Chain Pressure Points")
        levels = _pressure_level(route_name, commodity)

        metrics = []
        for key in ["origin_port", "dest_port", "inland", "labor", "equipment"]:
            lvl = levels[key]
            label, rating, color, metric = PRESSURE_TEMPLATES[key][lvl]
            metrics.append({
                "label": label,
                "value": rating,
                "accent": color,
                "delta": metric,
                "delta_color": C_TEXT3,
            })
        metric_card_row(metrics, columns=5)
    except Exception:
        logger.exception("_render_pressure_points failed")
        st.warning("Pressure points unavailable.")


def _render_shipper_intel(route_name: str, commodity: str) -> None:
    try:
        section_divider("Shipper Intelligence")
        bcos = _seeded_bcos(route_name, commodity)

        section_header(
            f"Top 10 Beneficial Cargo Owners — {commodity} on {route_name}",
        )

        rows = []
        for b in bcos:
            spot = b["spot_pct"]
            spot_color = C_LOW if spot > 60 else (C_MOD if spot > 35 else C_HIGH)
            rows.append([
                _mono(str(b["rank"]), color=C_TEXT3),
                _sans(b["name"], color=C_TEXT, weight=700),
                _mono(f"{b['volume_teu']:,}", color=C_TEXT2),
                badge(f"{spot}%", color=spot_color),
                _mono(f"{b['contract_months']}mo", color=C_TEXT2),
                _sans(b["strategy"], color=C_TEXT3),
            ])

        wsj_market_table(
            headers=["#", "BCO", "Vol (TEU/yr)", "Spot %", "Contract", "Strategy"],
            rows=rows,
        )
    except Exception:
        logger.exception("_render_shipper_intel failed")
        st.warning("Shipper intelligence unavailable.")


def _render_analyst_commentary(route_name: str, commodity: str) -> None:
    try:
        section_divider("Analyst Commentary")
        rd = ROUTES[route_name]
        pct = rd["rate_pct"]
        rate = rd["base_rate"]
        cd = COMMODITIES[commodity]

        high_pct = pct >= 65

        bull_items = [
            (
                f"Peak season demand surge expected to push {route_name} rates +18–25% by Q3"
                if high_pct else
                f"Rates at {pct}th percentile leave significant upside; "
                f"any demand shock could add $400–600/TEU"
            ),
            (
                f"{commodity} production growth of {cd['top_exporters'][0][3]:+.1f}% "
                f"in top exporter drives incremental shipping demand through H2"
            ),
            (
                "Vessel supply growth decelerating as orderbook deliveries pushed to 2027; "
                "effective capacity may tighten 3–5% YoY"
            ),
        ]

        bear_items = [
            "Macroeconomic slowdown in key consumer markets risks demand contraction of 5–8%",
            (
                f"New {rd['carriers'][0][0]} loop deployment adds ~{rd['weekly_services'] // 3} "
                f"weekly sailings — capacity pressure on rates"
            ),
            (
                f"{commodity} import substitution trends reducing long-haul shipment volumes; "
                f"nearshoring accelerating"
            ),
        ]

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
            bg = _hex_rgba(color, 0.10)
            if isinstance(items, list):
                content = "".join(
                    f'<div style="display:flex;gap:8px;margin-bottom:6px">'
                    f'<div style="color:{color};font-weight:900;margin-top:1px">▸</div>'
                    f'<div style="font-family:var(--sans);font-size:0.82rem;color:{C_TEXT2};line-height:1.5">{it}</div>'
                    f'</div>'
                    for it in items
                )
            else:
                content = f'<div style="font-family:var(--sans);font-size:0.82rem;color:{C_TEXT2};line-height:1.6">{items}</div>'
            return (
                f'<div style="background:{bg};border-left:3px solid {color};'
                f'border-radius:0 3px 3px 0;padding:14px 16px;margin-bottom:12px">'
                f'<div style="font-family:var(--sans);font-size:0.7rem;color:{color};font-weight:700;'
                f'letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">{title}</div>'
                f'{content}</div>'
            )

        html = (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:3px;padding:16px 20px">'
            f'{_case_block("Bull Case", C_HIGH, bull_items)}'
            f'{_case_block("Bear Case", C_LOW, bear_items)}'
            f'{_case_block("Base Case", C_ACCENT, base_case)}'
            f'{_case_block("Key Watchpoints", C_MOD, watchpoints)}'
            f'</div>'
        )
        st.html(html)

    except Exception:
        logger.exception("_render_analyst_commentary failed")
        st.warning("Analyst commentary unavailable.")


def _render_similar_routes(route_name: str) -> None:
    try:
        section_divider("Similar Route Comparisons")
        rd = ROUTES[route_name]
        similar = rd.get("similar", [])

        if not similar:
            st.info("No comparable routes configured.")
            return

        sel_rate = rd["base_rate"]
        rows = [[
            _sans(f"{route_name} (selected)", color=C_ACCENT, weight=800),
            _mono(f"${sel_rate:,}", color=C_TEXT),
            _mono(f"{rd['nm']:,}", color=C_TEXT2),
            _sans("—", color=C_TEXT3),
        ]]
        for name, rate, nm, diff in similar:
            diff_color = C_HIGH if diff < 0 else C_LOW
            diff_sign = "+" if diff > 0 else ""
            diff_label = f"{diff_sign}{diff}%" if isinstance(diff, (int, float)) else str(diff)
            rows.append([
                _sans(name, color=C_TEXT2),
                _mono(f"${rate:,}", color=C_TEXT2),
                _mono(f"{nm:,}", color=C_TEXT3),
                badge(diff_label, color=diff_color),
            ])

        wsj_market_table(
            headers=["Route", "Rate ($/TEU)", "Distance (nm)", "vs Selected"],
            rows=rows,
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
        page_header(
            title="Deep Dive — Research Analyst View",
            subtitle="Select a route and commodity to generate comprehensive trade lane intelligence.",
            icon="🔍",
            badge_text="Demo Data",
            badge_color=C_MOD,
        )

        route, commodity = _render_selector()
        _render_route_card(route)
        _render_commodity_flow(commodity)
        _render_pressure_points(route, commodity)
        _render_shipper_intel(route, commodity)
        _render_analyst_commentary(route, commodity)
        _render_similar_routes(route)

    except Exception:
        logger.exception("tab_deep_dive render failed")
        st.error("Deep Dive tab encountered an unexpected error. Check logs.")
