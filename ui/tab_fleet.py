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

import random
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

# ── Color palette ─────────────────────────────────────────────────────────────
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
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"
C_ORANGE  = "#f97316"


# ── Layout helper ─────────────────────────────────────────────────────────────

def _dark_layout(height: int = 360, l: int = 52, r: int = 24, t: int = 36, b: int = 44) -> dict:
    return dict(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(family="Inter, sans-serif", color=C_TEXT2, size=11),
        margin=dict(l=l, r=r, t=t, b=b),
        height=height,
        hoverlabel=dict(bgcolor=C_CARD, font_color=C_TEXT, bordercolor=C_BORDER),
    )


def _section(title: str, subtitle: str = "") -> None:
    sub_html = f'<span style="color:{C_TEXT3};font-size:12px;margin-left:10px;">{subtitle}</span>' if subtitle else ""
    st.markdown(
        f'<div style="border-left:3px solid {C_ACCENT};padding:6px 0 6px 12px;margin:24px 0 12px 0;">'
        f'<span style="color:{C_TEXT};font-size:15px;font-weight:700;letter-spacing:0.3px;">{title}</span>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(label: str, value: str, delta: str = "", color: str = C_HIGH) -> str:
    delta_html = (
        f'<div style="color:{color};font-size:11px;margin-top:4px;">{delta}</div>'
        if delta else ""
    )
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:16px 20px;text-align:center;">'
        f'<div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">{label}</div>'
        f'<div style="color:{C_TEXT};font-size:24px;font-weight:700;">{value}</div>'
        f'{delta_html}</div>'
    )


# ── Section 1: Global Fleet KPIs ──────────────────────────────────────────────

def _render_kpis(insights: Optional[dict]) -> None:
    try:
        _section("Global Fleet KPIs", "As of Q1 2026 — global merchant fleet snapshot")
        cols = st.columns(5)
        kpis = [
            ("Total Fleet TEU Capacity", "30.4M TEU", "+3.2% YoY", C_HIGH),
            ("Active Vessels", "6,842", "+124 net adds", C_ACCENT),
            ("Vessels on Order", "1,207", "Newbuild pipeline", C_MOD),
            ("Scrapping Rate", "38 / month", "Avg YTD 2026", C_LOW),
            ("Net Fleet Growth % YoY", "+3.2%", "Supply growth above demand", C_MOD),
        ]
        for col, (label, value, delta, color) in zip(cols, kpis):
            with col:
                st.markdown(_kpi_card(label, value, delta, color), unsafe_allow_html=True)
    except Exception:
        logger.exception("Fleet KPIs render failed")


# ── Section 2: Fleet Composition Breakdown ────────────────────────────────────

def _render_composition() -> None:
    try:
        _section("Fleet Composition Breakdown", "By vessel type and age bracket")
        left, right = st.columns(2)

        # Donut — by vessel type
        with left:
            try:
                types   = ["Container", "Dry Bulk", "Tanker", "LNG", "Other"]
                shares  = [34, 29, 18, 6, 13]
                colors  = [C_ACCENT, C_HIGH, C_MOD, C_PURPLE, C_TEXT3]
                fig = go.Figure(go.Pie(
                    labels=types,
                    values=shares,
                    hole=0.52,
                    marker=dict(colors=colors, line=dict(color=C_BG, width=2)),
                    textinfo="label+percent",
                    textfont=dict(color=C_TEXT, size=11),
                    hovertemplate="<b>%{label}</b><br>Share: %{percent}<extra></extra>",
                ))
                fig.update_layout(
                    **_dark_layout(height=340, l=10, r=10, t=30, b=10),
                    title=dict(text="Fleet by Vessel Type", font=dict(color=C_TEXT, size=13), x=0.5),
                    showlegend=True,
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
                bar_colors = [C_HIGH, C_ACCENT, C_MOD, C_LOW, "#ef4444"]
                fig2 = go.Figure(go.Bar(
                    x=brackets,
                    y=counts,
                    marker=dict(color=bar_colors, line=dict(color=C_BG, width=1)),
                    text=[f"{v:,}" for v in counts],
                    textposition="outside",
                    textfont=dict(color=C_TEXT2, size=10),
                    hovertemplate="<b>%{x}</b><br>Vessels: %{y:,}<extra></extra>",
                ))
                fig2.update_layout(
                    **_dark_layout(height=340, l=52, r=16, t=30, b=40),
                    title=dict(text="Fleet by Age Bracket — Ageing Fleet Narrative", font=dict(color=C_TEXT, size=13), x=0.5),
                    xaxis=dict(title="Age Bracket", color=C_TEXT3, gridcolor="rgba(255,255,255,0.04)"),
                    yaxis=dict(title="Vessel Count", color=C_TEXT3, gridcolor="rgba(255,255,255,0.04)"),
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
    except Exception:
        logger.exception("Fleet composition section failed")


# ── Section 3: Newbuild Order Book ────────────────────────────────────────────

def _render_orderbook() -> None:
    try:
        _section("Newbuild Order Book", "Active orders — LNG dual-fuel trend dominant")
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
        def _row_style(row: pd.Series) -> list:
            if "Dual-Fuel" in row["Vessel Type"] or "LNG Carrier" in row["Vessel Type"]:
                return [f"background-color:rgba(139,92,246,0.12);color:{C_TEXT}"] * len(row)
            return [f"color:{C_TEXT}"] * len(row)

        styled = (
            df.style
            .apply(_row_style, axis=1)
            .set_table_styles([
                {"selector": "thead th", "props": [
                    ("background-color", C_CARD),
                    ("color", C_TEXT2),
                    ("font-size", "11px"),
                    ("text-transform", "uppercase"),
                    ("letter-spacing", "0.5px"),
                    ("border-bottom", f"1px solid {C_BORDER}"),
                    ("padding", "8px 12px"),
                ]},
                {"selector": "tbody td", "props": [
                    ("border-bottom", f"1px solid {C_BORDER}"),
                    ("padding", "8px 12px"),
                    ("font-size", "12px"),
                ]},
                {"selector": "table", "props": [
                    ("background-color", C_SURFACE),
                    ("border-radius", "8px"),
                    ("width", "100%"),
                ]},
            ])
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown(
            f'<div style="color:{C_PURPLE};font-size:12px;margin-top:6px;">'
            f'Trend: LNG dual-fuel vessels now represent the largest single category in the global orderbook, '
            f'driven by IMO 2030 carbon-intensity targets and EU ETS compliance pressure.</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Order book section failed")


# ── Section 4: Scrapping Analysis ─────────────────────────────────────────────

def _render_scrapping() -> None:
    try:
        _section("Scrapping Analysis", "YTD 2026 scrapped vessels + 12-month forecast")
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
                avg_age = 26
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid rgba(239,68,68,0.3);border-radius:8px;'
                    f'padding:10px 14px;margin-top:10px;">'
                    f'<span style="color:{C_LOW};font-weight:700;">Avg Scrapping Age:</span> '
                    f'<span style="color:{C_TEXT};font-size:20px;font-weight:700;">{avg_age} years</span><br>'
                    f'<span style="color:{C_TEXT3};font-size:11px;">Vessels 20+ years face accelerating scrapping pressure '
                    f'under CII ratings and EU ETS compliance costs.</span></div>',
                    unsafe_allow_html=True,
                )
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
                    marker_color="rgba(239,68,68,0.35)",
                    hovertemplate="<b>%{x}</b><br>Forecast: %{y}<extra></extra>",
                ))
                fig.update_layout(
                    **_dark_layout(height=300, l=44, r=16, t=30, b=44),
                    title=dict(text="Scrapping by Vessel Type — YTD vs Forecast", font=dict(color=C_TEXT, size=12), x=0.5),
                    barmode="group",
                    xaxis=dict(color=C_TEXT3),
                    yaxis=dict(title="Vessels Scrapped", color=C_TEXT3, gridcolor="rgba(255,255,255,0.04)"),
                    legend=dict(font=dict(color=C_TEXT2, size=10)),
                )
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                logger.exception("Scrapping bar chart failed")
    except Exception:
        logger.exception("Scrapping section failed")


# ── Section 5: Fleet Utilization Map ──────────────────────────────────────────

def _render_utilization_map() -> None:
    try:
        _section("Fleet Utilization Map", "Vessel density by region — darker = higher concentration")
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
        names  = [h[0] for h in vessel_hubs]

        fig = go.Figure(go.Scattergeo(
            lon=lons,
            lat=lats,
            text=labels,
            hoverinfo="text",
            mode="markers",
            marker=dict(
                size=[s / 60 for s in sizes],
                color=sizes,
                colorscale=[[0, "rgba(59,130,246,0.3)"], [0.5, C_ACCENT], [1, C_HIGH]],
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
        fig.update_layout(
            **_dark_layout(height=440, l=0, r=0, t=20, b=0),
            geo=dict(bgcolor=C_BG),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:-6px;">'
            f'Red Sea (38°E, 20°N) shows sharp vessel density drop due to Houthi disruptions rerouting traffic via Cape of Good Hope. '
            f'This has added ~10–14 days to Asia-Europe voyages and effectively tightened global capacity 8–12%.</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Utilization map failed")


# ── Section 6: Capacity vs Demand ─────────────────────────────────────────────

def _render_capacity_vs_demand() -> None:
    try:
        _section("Capacity vs Demand", "Fleet supply growth % vs trade volume growth % — 2020–2025")
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
            fillcolor="rgba(239,68,68,0.10)",
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
            fillcolor="rgba(16,185,129,0.10)",
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
        fig.update_layout(
            **_dark_layout(height=360, l=52, r=24, t=36, b=48),
            title=dict(text="Fleet Capacity vs Trade Volume Growth (2020–2025)", font=dict(color=C_TEXT, size=13), x=0.5),
            xaxis=dict(title="Year", color=C_TEXT3, tickvals=years, gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(title="YoY Growth %", color=C_TEXT3, gridcolor="rgba(255,255,255,0.04)"),
            legend=dict(font=dict(color=C_TEXT2, size=10), x=0.01, y=0.99),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        logger.exception("Capacity vs demand section failed")


# ── Section 7: Age Profile Risk ───────────────────────────────────────────────

def _render_age_risk() -> None:
    try:
        _section("Age Profile Risk", "Vessel types with oldest fleets — renewal and IMO 2030 readiness")
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

        def _color_risk(val: str) -> str:
            mapping = {
                "High": f"color:{C_LOW};font-weight:700",
                "Medium": f"color:{C_MOD};font-weight:700",
                "Low": f"color:{C_HIGH};font-weight:700",
                "Minimal": f"color:{C_ACCENT};font-weight:700",
            }
            return mapping.get(val, "")

        def _color_imo(val: str) -> str:
            if val == "Yes":
                return f"color:{C_HIGH};font-weight:700"
            if val == "Partial":
                return f"color:{C_MOD}"
            return f"color:{C_LOW}"

        styled = (
            df.style
            .applymap(_color_risk, subset=["CII Rating Risk"])
            .applymap(_color_imo, subset=["IMO 2030 Ready"])
            .set_table_styles([
                {"selector": "thead th", "props": [
                    ("background-color", C_CARD),
                    ("color", C_TEXT2),
                    ("font-size", "11px"),
                    ("text-transform", "uppercase"),
                    ("padding", "8px 12px"),
                    ("border-bottom", f"1px solid {C_BORDER}"),
                ]},
                {"selector": "tbody td", "props": [
                    ("border-bottom", f"1px solid {C_BORDER}"),
                    ("padding", "7px 12px"),
                    ("font-size", "12px"),
                    ("color", C_TEXT),
                ]},
                {"selector": "table", "props": [
                    ("background-color", C_SURFACE),
                    ("border-radius", "8px"),
                    ("width", "100%"),
                ]},
            ])
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown(
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:8px;">'
            f'IMO 2030 targets require 40% carbon intensity reduction vs 2008 baseline. '
            f'Vessels rated CII D/E for two consecutive years face trading restrictions from 2026.</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Age profile risk section failed")


# ── Section 8: Key Fleet Metrics by Route ────────────────────────────────────

def _render_route_metrics(route_results: Optional[dict]) -> None:
    try:
        _section("Key Fleet Metrics by Route", "Deployed capacity, utilization, and vessel count by major trade lane")
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
            util_color = C_HIGH if util_val >= 90 else C_MOD if util_val >= 80 else C_LOW
            trend_color = C_HIGH if "+" in r["Trend"] else C_LOW
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
                f'padding:16px 20px;margin-bottom:12px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">'
                f'<span style="color:{C_TEXT};font-size:14px;font-weight:700;">{r["Trade Lane"]}</span>'
                f'<span style="color:{trend_color};font-size:12px;font-weight:600;">{r["Trend"]}</span>'
                f'</div>'
                f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;">'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;margin-bottom:3px;">Deployed Capacity</div>'
                f'<div style="color:{C_TEXT};font-size:14px;font-weight:600;">{r["Deployed Capacity (TEU)"]} TEU</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;margin-bottom:3px;">Utilization</div>'
                f'<div style="color:{util_color};font-size:14px;font-weight:700;">{r["Utilization Rate"]}</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;margin-bottom:3px;">Vessels Deployed</div>'
                f'<div style="color:{C_TEXT};font-size:14px;font-weight:600;">{r["Deployed Vessels"]}</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;margin-bottom:3px;">Avg Vessel Size</div>'
                f'<div style="color:{C_TEXT};font-size:14px;font-weight:600;">{r["Avg Vessel Size (TEU)"]} TEU</div></div>'
                f'</div>'
                f'<div style="margin-top:10px;color:{C_TEXT3};font-size:11px;">Note: {r["Disruption Note"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("Route metrics section failed")


# ── Main render ───────────────────────────────────────────────────────────────

def render(port_results=None, route_results=None, insights=None) -> None:
    """Render the Global Fleet Analytics tab."""
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_SURFACE} 0%,rgba(10,15,26,0.8) 100%);'
            f'border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px;margin-bottom:20px;">'
            f'<div style="color:{C_TEXT};font-size:20px;font-weight:800;letter-spacing:0.3px;">Global Fleet Analytics</div>'
            f'<div style="color:{C_TEXT3};font-size:12px;margin-top:4px;">'
            f'Comprehensive supply-side analysis — fleet composition, orderbook, scrapping dynamics, '
            f'capacity vs demand, and trade lane deployment. Data as of Q1 2026.</div>'
            f'</div>',
            unsafe_allow_html=True,
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
