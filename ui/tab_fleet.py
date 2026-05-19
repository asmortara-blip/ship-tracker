"""Global Fleet Analytics tab — comprehensive fleet supply, composition, and demand dynamics.

Renders global fleet analytics across 8 major sections:
  1. Global Fleet KPIs          — hero row: TEU capacity, active vessels, orders, scrapping rate, net growth
  2. Fleet Composition          — donut by vessel type + bar by age bracket
  3. Newbuild Order Book        — table with vessel type, orders, DWT/TEU, delivery, % of fleet, shipyards
  4. Scrapping Analysis         — YTD scrapped table + bar chart by type/age + avg scrapping age insight
  5. Fleet Utilization Map      — scatter_geo vessel density by region (dark ocean)
  6. Capacity vs Demand         — 5-year line chart supply vs demand growth with shaded oversupply zones
  7. Age Profile Risk           — oldest fleets, eco-compliance, IMO 2030 readiness table
  8. Key Fleet Metrics by Route — Asia-Europe, Transpacific, Transatlantic deployed capacity + utilization
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    insight_card_html,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
)


# ── Cell formatters for wsj_market_table() ────────────────────────────────
# wsj_market_table renders cell strings as raw HTML inside <td>. These helpers
# only style content (font + conditional color); table CSS handles alignment
# and rule lines. Mirrors the pattern in ui/tab_results.py.

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


# ── Section 1: Global Fleet KPIs ──────────────────────────────────────────────

def _render_kpis(insights: Optional[dict]) -> None:
    try:
        section_header("Global Fleet KPIs", "As of Q1 2026 — global merchant fleet snapshot")
        metric_card_row([
            {"label": "Total Fleet TEU Capacity", "value": "30.4M TEU",
             "accent": C_HIGH,   "sublabel": "+3.2% YoY"},
            {"label": "Active Vessels",           "value": "6,842",
             "accent": C_ACCENT, "sublabel": "+124 net adds"},
            {"label": "Vessels on Order",         "value": "1,207",
             "accent": C_MOD,    "sublabel": "Newbuild pipeline"},
            {"label": "Scrapping Rate",           "value": "38 / month",
             "accent": C_LOW,    "sublabel": "Avg YTD 2026"},
            {"label": "Net Fleet Growth % YoY",   "value": "+3.2%",
             "accent": C_MOD,    "sublabel": "Supply growth above demand"},
        ], columns=5)
        st.markdown(source_footer([
            {"name": "Clarksons Research Fleet Database", "kind": "modeled", "quality": "demo"},
            {"name": "Alphaliner Monthly Monitor",        "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Fleet KPIs render failed")


# ── Section 2: Fleet Composition Breakdown ────────────────────────────────────

def _render_composition() -> None:
    try:
        section_header("Fleet Composition Breakdown", "By vessel type and age bracket")
        left, right = st.columns(2)

        # Donut — by vessel type
        with left:
            try:
                types   = ["Container", "Dry Bulk", "Tanker", "LNG", "Other"]
                shares  = [34, 29, 18, 6, 13]
                colors  = [C_ACCENT, C_HIGH, C_MOD, C_CONV, C_TEXT3]
                fig = go.Figure(go.Pie(
                    labels=types,
                    values=shares,
                    hole=0.52,
                    marker=dict(colors=colors, line=dict(color=C_BG, width=2)),
                    textinfo="label+percent",
                    textfont=dict(color=C_TEXT, size=11),
                    hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
                ))
                apply_dark_layout(fig, height=340, showlegend=True)
                fig.update_layout(
                    margin=dict(l=10, r=10, t=30, b=10),
                    title=dict(text="Fleet by Vessel Type", font=dict(color=C_TEXT, size=13), x=0.5),
                    legend=dict(font=dict(color=C_TEXT2, size=10), x=0.7, y=0.5),
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                logger.exception("Composition donut failed")

        # Bar — by age bracket
        with right:
            try:
                brackets = ["0–5y", "6–10y", "11–15y", "16–20y", "21+y"]
                counts   = [1820, 2140, 1650, 980, 652]
                bar_colors = [C_HIGH, C_ACCENT, C_MOD, C_LOW, "#c0392b"]
                fig2 = go.Figure(go.Bar(
                    x=brackets,
                    y=counts,
                    marker=dict(color=bar_colors, line=dict(color=C_BG, width=1)),
                    text=[f"{v:,}" for v in counts],
                    textposition="outside",
                    textfont=dict(color=C_TEXT2, size=10),
                    hovertemplate="<b>%{x}</b><br>Vessels: %{y:,}<extra></extra>",
                ))
                apply_dark_layout(fig2, height=340, showlegend=False)
                fig2.update_layout(
                    margin=dict(l=52, r=16, t=30, b=40),
                    title=dict(text="Fleet by Age Bracket — Ageing Fleet Narrative", font=dict(color=C_TEXT, size=13), x=0.5),
                    xaxis=dict(title="Age Bracket", color=C_TEXT3),
                    yaxis=dict(title="Vessel Count", color=C_TEXT3),
                )
                # annotation for "ageing fleet" note
                fig2.add_annotation(
                    x="21+y", y=652, text="Scrapping<br>risk zone",
                    showarrow=True, arrowhead=2, arrowcolor=C_LOW,
                    font=dict(color=C_LOW, size=9), ax=40, ay=-30,
                )
                st.plotly_chart(fig2, use_container_width=True)
            except Exception:
                logger.exception("Age bracket bar failed")

        st.markdown(source_footer([
            {"name": "Clarksons Research Fleet Database", "kind": "modeled", "quality": "demo"},
            {"name": "BRS Alphaliner Vessel Census",      "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Fleet composition section failed")


# ── Section 3: Newbuild Order Book ────────────────────────────────────────────

def _render_orderbook() -> None:
    try:
        section_header("Newbuild Order Book", "Active orders — LNG dual-fuel trend dominant")
        data = [
            {
                "Vessel Type": "Ultra Large Container (24k+ TEU)",
                "Orders on Book": 48,
                "DWT / TEU": "24,000–25,400 TEU",
                "Expected Delivery": "2026–2028",
                "% of Existing Fleet": "12.4%",
                "Key Shipyards": "HHI, DSME, Samsung HI",
            },
            {
                "Vessel Type": "Panamax Container (4.4k–5.1k TEU)",
                "Orders on Book": 124,
                "DWT / TEU": "4,400–5,100 TEU",
                "Expected Delivery": "2025–2027",
                "% of Existing Fleet": "8.7%",
                "Key Shipyards": "COSCO Shipyard, Jiangnan",
            },
            {
                "Vessel Type": "Capesize Bulk Carrier",
                "Orders on Book": 87,
                "DWT / TEU": "180,000 DWT",
                "Expected Delivery": "2025–2027",
                "% of Existing Fleet": "6.2%",
                "Key Shipyards": "HHI, HHIC-Phil",
            },
            {
                "Vessel Type": "VLCC Tanker",
                "Orders on Book": 62,
                "DWT / TEU": "300,000 DWT",
                "Expected Delivery": "2026–2028",
                "% of Existing Fleet": "9.1%",
                "Key Shipyards": "HD Korea, Hyundai Mipo",
            },
            {
                "Vessel Type": "LNG Carrier",
                "Orders on Book": 201,
                "DWT / TEU": "174,000 m³",
                "Expected Delivery": "2025–2030",
                "% of Existing Fleet": "42.3%",
                "Key Shipyards": "Samsung HI, HHI, GTT",
            },
            {
                "Vessel Type": "Ammonia / LNG Dual-Fuel",
                "Orders on Book": 318,
                "DWT / TEU": "Various",
                "Expected Delivery": "2025–2029",
                "% of Existing Fleet": "—",
                "Key Shipyards": "MAN ES, Wartsila, HHI",
            },
        ]
        df = pd.DataFrame(data)

        # Highlight dual-fuel row
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown(insight_card_html(
            title="LNG Dual-Fuel Trend",
            score=0.75,
            action="Watch",
            rationale=(
                "Dual-fuel vessels now represent the largest single category in the global "
                "orderbook, driven by IMO 2030 carbon-intensity targets and EU ETS compliance "
                "pressure."
            ),
            category="MACRO",
        ), unsafe_allow_html=True)
        st.markdown(source_footer([
            {"name": "Clarksons Newbuilding Orderbook", "kind": "modeled", "quality": "demo"},
            {"name": "MAN Energy Solutions / GTT trend reports", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Order book section failed")


# ── Section 4: Scrapping Analysis ─────────────────────────────────────────────

def _render_scrapping() -> None:
    try:
        section_header("Scrapping Analysis", "YTD 2026 scrapped vessels + 12-month forecast")
        left, right = st.columns([1, 1.5])

        with left:
            try:
                scrap_data = [
                    {"Vessel Type": "Container", "YTD Scrapped": 42, "Forecast Full Year": 180, "Avg Age at Scrap": 26},
                    {"Vessel Type": "Dry Bulk",   "YTD Scrapped": 61, "Forecast Full Year": 260, "Avg Age at Scrap": 24},
                    {"Vessel Type": "Tanker",     "YTD Scrapped": 28, "Forecast Full Year": 115, "Avg Age at Scrap": 25},
                    {"Vessel Type": "LNG",        "YTD Scrapped":  3, "Forecast Full Year":  12, "Avg Age at Scrap": 32},
                    {"Vessel Type": "Other",      "YTD Scrapped": 18, "Forecast Full Year":  75, "Avg Age at Scrap": 28},
                ]
                df_s = pd.DataFrame(scrap_data)
                st.dataframe(df_s, use_container_width=True, hide_index=True)
                st.markdown(insight_card_html(
                    title="Avg Scrapping Age — 26 years",
                    score=0.85,
                    action="Caution",
                    rationale=(
                        "Vessels 20+ years face accelerating scrapping pressure under CII "
                        "ratings and EU ETS compliance costs."
                    ),
                    category="SCRAP",
                ), unsafe_allow_html=True)
            except Exception:
                logger.exception("Scrapping table failed")

        with right:
            try:
                types    = ["Container", "Dry Bulk", "Tanker", "LNG", "Other"]
                ytd      = [42, 61, 28, 3, 18]
                forecast = [180, 260, 115, 12, 75]
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    name="YTD 2026",
                    x=types, y=ytd,
                    marker_color=C_LOW,
                    hovertemplate="<b>%{x}</b><br>YTD: %{y}<extra></extra>",
                ))
                fig.add_trace(go.Bar(
                    name="Full Year Forecast",
                    x=types, y=forecast,
                    marker_color="rgba(192,57,43,0.35)",
                    hovertemplate="<b>%{x}</b><br>Forecast: %{y}<extra></extra>",
                ))
                apply_dark_layout(fig, height=300, showlegend=True)
                fig.update_layout(
                    margin=dict(l=44, r=16, t=30, b=44),
                    title=dict(text="Scrapping by Vessel Type — YTD vs Forecast", font=dict(color=C_TEXT, size=12), x=0.5),
                    barmode="group",
                    xaxis=dict(color=C_TEXT3),
                    yaxis=dict(title="Vessels Scrapped", color=C_TEXT3),
                    legend=dict(font=dict(color=C_TEXT2, size=10)),
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                logger.exception("Scrapping bar chart failed")

        st.markdown(source_footer([
            {"name": "VesselsValue Scrapping Tracker",  "kind": "modeled", "quality": "demo"},
            {"name": "Internal CII / EU ETS forecasts", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Scrapping section failed")


# ── Section 5: Fleet Utilization Map ──────────────────────────────────────────

def _render_utilization_map() -> None:
    try:
        section_header("Fleet Utilization Map", "Vessel density by region — darker = higher concentration")
        vessel_hubs = [
            ("South China Sea",      115.0,  20.0, 1840, "South China Sea: 1,840 vessels"),
            ("Singapore Strait",     104.0,   1.3, 1420, "Singapore Strait: 1,420 vessels"),
            ("English Channel",        1.5,  51.0,  980, "English Channel: 980 vessels"),
            ("Gulf of Aden",          45.0,  12.0,  420, "Gulf of Aden: 420 vessels"),
            ("Strait of Malacca",    100.0,   4.0, 1100, "Strait of Malacca: 1,100 vessels"),
            ("Red Sea",               38.0,  20.0,  190, "Red Sea: 190 vessels (disrupted)"),
            ("Panama Canal",         -79.9,   9.1,  610, "Panama Canal: 610 vessels"),
            ("Suez Canal",            32.5,  30.0,  340, "Suez Canal: 340 vessels"),
            ("North Atlantic",       -40.0,  45.0,  720, "North Atlantic: 720 vessels"),
            ("East Coast US",        -74.0,  40.0,  530, "East Coast US: 530 vessels"),
            ("NW European Ports",      8.0,  54.0,  870, "NW Europe Ports: 870 vessels"),
            ("Persian Gulf",          52.0,  26.0,  460, "Persian Gulf: 460 vessels"),
            ("East Japan",           140.0,  35.0,  580, "East Japan: 580 vessels"),
            ("Australia East",       153.0, -27.0,  310, "Australia East: 310 vessels"),
            ("Caribbean",            -70.0,  15.0,  290, "Caribbean: 290 vessels"),
        ]
        lons   = [h[1] for h in vessel_hubs]
        lats   = [h[2] for h in vessel_hubs]
        sizes  = [h[3] for h in vessel_hubs]
        labels = [h[4] for h in vessel_hubs]

        fig = go.Figure(go.Scattergeo(
            lon=lons,
            lat=lats,
            text=labels,
            hoverinfo="text",
            mode="markers",
            marker=dict(
                size=[s / 60 for s in sizes],
                color=sizes,
                colorscale=[[0, "rgba(53,114,176,0.3)"], [0.5, C_ACCENT], [1, C_HIGH]],
                cmin=100,
                cmax=1900,
                colorbar=dict(
                    title=dict(text="Vessel Density", font=dict(color=C_TEXT2, size=10)),
                    tickfont=dict(color=C_TEXT3, size=9),
                    bgcolor=C_CARD,
                    bordercolor=C_BORDER,
                    len=0.6,
                ),
                line=dict(color=C_ACCENT, width=0.5),
                opacity=0.85,
            ),
            name="",
        ))
        fig.update_geos(
            projection_type="natural earth",
            bgcolor=C_BG,
            landcolor="#1a2535",
            oceancolor="#0a1220",
            lakecolor="#0a1220",
            coastlinecolor="rgba(100,116,139,0.4)",
            showland=True,
            showocean=True,
            showcoastlines=True,
            showframe=False,
            showcountries=True,
            countrycolor="rgba(100,116,139,0.2)",
        )
        apply_dark_layout(fig, height=440, showlegend=False)
        fig.update_layout(
            margin=dict(l=0, r=0, t=20, b=0),
            geo=dict(bgcolor=C_BG),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(insight_card_html(
            title="Red Sea Disruption — Capacity Tightening",
            score=0.7,
            action="Caution",
            rationale=(
                "Red Sea (38°E, 20°N) shows sharp vessel density drop due to Houthi "
                "disruptions rerouting traffic via Cape of Good Hope. This has added "
                "~10–14 days to Asia-Europe voyages and effectively tightened global "
                "capacity 8–12%."
            ),
            category="ROUTE",
        ), unsafe_allow_html=True)
        st.markdown(source_footer([
            {"name": "MarineTraffic AIS density",          "kind": "scraped", "quality": "demo"},
            {"name": "Internal disruption-to-capacity model", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Utilization map failed")


# ── Section 6: Capacity vs Demand ─────────────────────────────────────────────

def _render_capacity_vs_demand() -> None:
    try:
        section_header("Capacity vs Demand", "Fleet supply growth % vs trade volume growth % — 2020–2025")
        years    = [2020, 2021, 2022, 2023, 2024, 2025]
        supply   = [2.1,  4.3,  3.8,  8.2,  6.4,  4.1]   # fleet capacity growth %
        demand   = [1.2,  6.8,  4.1,  3.1,  5.9,  3.2]   # trade volume growth %

        fig = go.Figure()

        # Fill oversupply (supply > demand)
        supply_over = [s if s >= d else d for s, d in zip(supply, demand)]
        demand_under = [d if s >= d else s for s, d in zip(supply, demand)]
        fig.add_trace(go.Scatter(
            x=years + years[::-1],
            y=supply_over + demand_under[::-1],
            fill="toself",
            fillcolor="rgba(192,57,43,0.10)",
            line=dict(color="rgba(0,0,0,0)"),
            hoverinfo="skip",
            name="Oversupply zone",
            showlegend=True,
        ))

        # Fill demand tightness (demand > supply)
        demand_over = [d if d >= s else s for s, d in zip(supply, demand)]
        supply_under = [s if d >= s else d for s, d in zip(supply, demand)]
        fig.add_trace(go.Scatter(
            x=years + years[::-1],
            y=demand_over + supply_under[::-1],
            fill="toself",
            fillcolor="rgba(46,158,110,0.10)",
            line=dict(color="rgba(0,0,0,0)"),
            hoverinfo="skip",
            name="Tight market zone",
            showlegend=True,
        ))

        fig.add_trace(go.Scatter(
            x=years, y=supply,
            name="Fleet Capacity Growth %",
            line=dict(color=C_LOW, width=2.5),
            mode="lines+markers",
            marker=dict(size=6, color=C_LOW),
            hovertemplate="<b>%{x}</b><br>Supply growth: %{y:.1f}%<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=years, y=demand,
            name="Trade Volume Growth %",
            line=dict(color=C_HIGH, width=2.5),
            mode="lines+markers",
            marker=dict(size=6, color=C_HIGH),
            hovertemplate="<b>%{x}</b><br>Demand growth: %{y:.1f}%<extra></extra>",
        ))

        # Annotation: Red Sea disruption
        fig.add_annotation(
            x=2024, y=5.9,
            text="Red Sea disruptions<br>boost effective demand",
            showarrow=True, arrowhead=2, arrowcolor=C_MOD,
            font=dict(color=C_MOD, size=9),
            ax=-80, ay=-36,
        )
        # Annotation: 2023 oversupply peak
        fig.add_annotation(
            x=2023, y=8.2,
            text="Orderbook deliveries<br>oversupply peak",
            showarrow=True, arrowhead=2, arrowcolor=C_LOW,
            font=dict(color=C_LOW, size=9),
            ax=60, ay=-30,
        )
        apply_dark_layout(fig, height=360, showlegend=True)
        fig.update_layout(
            margin=dict(l=52, r=24, t=36, b=48),
            title=dict(text="Fleet Capacity vs Trade Volume Growth (2020–2025)", font=dict(color=C_TEXT, size=13), x=0.5),
            xaxis=dict(title="Year", color=C_TEXT3, tickvals=years),
            yaxis=dict(title="YoY Growth %", color=C_TEXT3),
            legend=dict(font=dict(color=C_TEXT2, size=10), x=0.01, y=0.99),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer([
            {"name": "Clarksons Supply / IMF WEO Trade",    "kind": "modeled", "quality": "demo"},
            {"name": "Internal supply-demand growth model", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Capacity vs demand section failed")


# ── Section 7: Age Profile Risk ───────────────────────────────────────────────

def _render_age_risk() -> None:
    try:
        section_header("Age Profile Risk", "Vessel types with oldest fleets — renewal and IMO 2030 readiness")
        data = [
            {
                "Vessel Type": "General Cargo",
                "Avg Fleet Age (yrs)": 23.4,
                "Vessels 20+y (%)": "51%",
                "Eco-Compliant (%)": "18%",
                "IMO 2030 Ready": "No",
                "CII Rating Risk": "High",
            },
            {
                "Vessel Type": "Tanker (Aframax)",
                "Avg Fleet Age (yrs)": 18.7,
                "Vessels 20+y (%)": "38%",
                "Eco-Compliant (%)": "34%",
                "IMO 2030 Ready": "Partial",
                "CII Rating Risk": "Medium",
            },
            {
                "Vessel Type": "Dry Bulk (Handysize)",
                "Avg Fleet Age (yrs)": 17.9,
                "Vessels 20+y (%)": "32%",
                "Eco-Compliant (%)": "29%",
                "IMO 2030 Ready": "Partial",
                "CII Rating Risk": "Medium",
            },
            {
                "Vessel Type": "VLCC Tanker",
                "Avg Fleet Age (yrs)": 13.2,
                "Vessels 20+y (%)": "18%",
                "Eco-Compliant (%)": "51%",
                "IMO 2030 Ready": "Partial",
                "CII Rating Risk": "Low",
            },
            {
                "Vessel Type": "Container (Panamax)",
                "Avg Fleet Age (yrs)": 12.8,
                "Vessels 20+y (%)": "15%",
                "Eco-Compliant (%)": "58%",
                "IMO 2030 Ready": "Partial",
                "CII Rating Risk": "Low",
            },
            {
                "Vessel Type": "Container (ULCS 18k+)",
                "Avg Fleet Age (yrs)": 5.1,
                "Vessels 20+y (%)": "0%",
                "Eco-Compliant (%)": "92%",
                "IMO 2030 Ready": "Yes",
                "CII Rating Risk": "Minimal",
            },
            {
                "Vessel Type": "LNG Carrier",
                "Avg Fleet Age (yrs)": 9.4,
                "Vessels 20+y (%)": "8%",
                "Eco-Compliant (%)": "81%",
                "IMO 2030 Ready": "Yes",
                "CII Rating Risk": "Minimal",
            },
        ]
        df = pd.DataFrame(data).sort_values("Avg Fleet Age (yrs)", ascending=False)

        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown(insight_card_html(
            title="IMO 2030 / CII Compliance",
            score=0.65,
            action="Caution",
            rationale=(
                "IMO 2030 targets require 40% carbon intensity reduction vs 2008 baseline. "
                "Vessels rated CII D/E for two consecutive years face trading restrictions from 2026."
            ),
            category="MACRO",
        ), unsafe_allow_html=True)
        st.markdown(source_footer([
            {"name": "Clarksons Fleet Census",            "kind": "modeled", "quality": "demo"},
            {"name": "IMO MEPC 80 / 81 final reports",    "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Age profile risk section failed")


# ── Section 8: Key Fleet Metrics by Route ────────────────────────────────────

def _render_route_metrics(route_results: Optional[dict]) -> None:
    try:
        section_header("Key Fleet Metrics by Route", "Deployed capacity, utilization, and vessel count by major trade lane")
        routes = [
            {
                "Trade Lane": "Asia–Europe",
                "Deployed Capacity (TEU)": "5,820,000",
                "Utilization Rate": "91%",
                "Deployed Vessels": 412,
                "Avg Vessel Size (TEU)": "14,126",
                "Trend": "+4.2% YoY",
                "Disruption Note": "Rerouting via Cape +12 days",
            },
            {
                "Trade Lane": "Transpacific (Asia–USWC)",
                "Deployed Capacity (TEU)": "4,340,000",
                "Utilization Rate": "87%",
                "Deployed Vessels": 388,
                "Avg Vessel Size (TEU)": "11,186",
                "Trend": "+2.8% YoY",
                "Disruption Note": "Panama drought partial recovery",
            },
            {
                "Trade Lane": "Transatlantic (Europe–USEC)",
                "Deployed Capacity (TEU)": "1,680,000",
                "Utilization Rate": "84%",
                "Deployed Vessels": 218,
                "Avg Vessel Size (TEU)": "7,706",
                "Trend": "+1.1% YoY",
                "Disruption Note": "Stable, slight slack capacity",
            },
        ]

        for r in routes:
            util_val = int(r["Utilization Rate"].replace("%", ""))
            util_color = C_HIGH if util_val >= 90 else (C_MOD if util_val >= 80 else C_LOW)
            section_header(r["Trade Lane"], f"Trend: {r['Trend']} · {r['Disruption Note']}")
            metric_card_row([
                {"label": "Deployed Capacity", "value": f"{r['Deployed Capacity (TEU)']} TEU",
                 "accent": C_TEXT,    "sublabel": "active deployment"},
                {"label": "Utilization",       "value": r["Utilization Rate"],
                 "accent": util_color, "sublabel": "lane fill rate"},
                {"label": "Vessels Deployed",  "value": str(r["Deployed Vessels"]),
                 "accent": C_ACCENT,  "sublabel": "active service"},
                {"label": "Avg Vessel Size",   "value": f"{r['Avg Vessel Size (TEU)']} TEU",
                 "accent": C_MOD,     "sublabel": "per deployment"},
            ], columns=4)

        st.markdown(source_footer([
            {"name": "Alphaliner Trade Lane Monitor",    "kind": "modeled", "quality": "demo"},
            {"name": "Drewry Container Forecaster",      "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Route metrics section failed")


# ── Main render ───────────────────────────────────────────────────────────────

def render(port_results=None, route_results=None, insights=None, *args, **kwargs) -> None:
    """Render the Global Fleet Analytics tab."""
    try:
        page_header(
            title="Global Fleet Analytics",
            subtitle=(
                "Comprehensive supply-side analysis — fleet composition, orderbook, "
                "scrapping dynamics, capacity vs demand, and trade lane deployment. "
                "Data as of Q1 2026."
            ),
            badge_text="FLEET",
            badge_color=C_ACCENT,
        )
    except Exception:
        logger.exception("Fleet header failed")

    _render_kpis(insights)
    st.divider()
    _render_composition()
    st.divider()
    _render_orderbook()
    st.divider()
    _render_scrapping()
    st.divider()
    _render_utilization_map()
    st.divider()
    _render_capacity_vs_demand()
    st.divider()
    _render_age_risk()
    st.divider()
    _render_route_metrics(route_results)
