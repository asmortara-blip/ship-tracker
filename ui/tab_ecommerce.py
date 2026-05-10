"""tab_ecommerce.py — E-Commerce Driven Shipping Demand Intelligence.

Sections:
  1. E-Commerce Demand Dashboard    — Global KPIs and market metrics
  2. E-Commerce Giants Shipping Impact — Platform comparison table
  3. Chinese E-Commerce Export Effect — De minimis analysis
  4. Peak Season Calendar           — Monthly demand timeline
  5. B2C vs B2B Freight Split       — By route breakdown
  6. Last Mile & Returns            — Reverse logistics analysis
  7. Rate Impact of E-Commerce      — Volume vs air freight correlation
  8. Key Metrics to Watch           — 5 leading indicators
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    apply_dark_layout,
    insight_card_html,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)

# Single tab-local color — semantic (e-commerce branding), not a palette alias.
C_PURPLE = "#7c6eaf"


# ── Provenance ────────────────────────────────────────────────────────────────

_SOURCES = [
    {"name": "Company filings · Bloomberg · eMarketer", "kind": "modeled", "quality": "good"},
    {"name": "US CBP · USITC (de minimis)", "kind": "static", "quality": "good"},
    {"name": "Composite peak-season indices", "kind": "modeled", "quality": "demo"},
]


# ── Static data ────────────────────────────────────────────────────────────────

_PLATFORM_DATA = [
    {
        "company": "Amazon",
        "gmv": 600,
        "shipping_vol": 5.9,
        "routes": "US domestic, US-EU",
        "carrier_strategy": "Own logistics (Amazon Air, delivery vans, DSPs)",
        "rate_impact": "Suppresses spot rates; builds parallel network",
        "color": "#ff9900",
        "flag": "🇺🇸",
    },
    {
        "company": "Temu",
        "gmv": 15,
        "shipping_vol": 1.2,
        "routes": "China → US, China → EU",
        "carrier_strategy": "Heavy air freight for fast delivery; growing ocean LCL",
        "rate_impact": "Driving up transpacific air cargo; 40%+ YoY air demand surge",
        "color": "#e53935",
        "flag": "🇨🇳",
    },
    {
        "company": "Shein",
        "gmv": 30,
        "shipping_vol": 2.2,
        "routes": "China FTZ → US, China → EU, China → LatAm",
        "carrier_strategy": "Air freight parcels via China FTZ; ultra-fast fashion model",
        "rate_impact": "Significant uplift to air cargo; de minimis dependent",
        "color": "#e91e8c",
        "flag": "🇨🇳",
    },
    {
        "company": "AliExpress",
        "gmv": 45,
        "shipping_vol": 3.1,
        "routes": "China → Global (150+ countries)",
        "carrier_strategy": "Mix of ePacket, ocean parcels, air; Cainiao network",
        "rate_impact": "Adds low-value high-volume parcel density to ocean containers",
        "color": "#ff6900",
        "flag": "🇨🇳",
    },
    {
        "company": "Shopify Merchants",
        "gmv": 200,
        "shipping_vol": 1.8,
        "routes": "US domestic; varies by merchant",
        "carrier_strategy": "Fragmented: UPS, FedEx, USPS, regional carriers",
        "rate_impact": "Neutral to slightly bullish; adds spot demand volatility",
        "color": "#96bf48",
        "flag": "🌐",
    },
    {
        "company": "Wayfair / Zalando",
        "gmv": 55,
        "shipping_vol": 0.3,
        "routes": "US domestic; EU-US; Asia → EU",
        "carrier_strategy": "B2C furniture/home; larger containers, LTL, white-glove",
        "rate_impact": "Bullish on FCL and container demand; high cubic weight",
        "color": "#7f187f",
        "flag": "🇺🇸🇪🇺",
    },
]

_ROUTE_SPLIT = [
    {"route": "Transpacific (Asia→US)", "b2c": 35, "b2b": 65, "avg_size": "8.2 kg", "mode": "Parcel + Container", "trend": "B2C ↑ rapidly (+20pp since 2020)"},
    {"route": "Asia → Europe", "b2c": 28, "b2b": 72, "avg_size": "12.4 kg", "mode": "Container + Air parcel", "trend": "B2C ↑ (+12pp since 2020)"},
    {"route": "Intra-Europe", "b2c": 52, "b2b": 48, "avg_size": "3.1 kg", "mode": "Parcel dominant", "trend": "B2C stable, high share"},
    {"route": "Transatlantic (US→EU)", "b2c": 18, "b2b": 82, "avg_size": "22.0 kg", "mode": "Container dominant", "trend": "B2C slowly ↑"},
    {"route": "China → LatAm", "b2c": 40, "b2b": 60, "avg_size": "6.5 kg", "mode": "Air + Ocean LCL", "trend": "B2C ↑ rapidly (+25pp since 2020)"},
    {"route": "Asia → Middle East", "b2c": 30, "b2b": 70, "avg_size": "9.8 kg", "mode": "Air parcel + Container", "trend": "B2C ↑ (+15pp since 2020)"},
]

_RETURN_RATES = [
    {"category": "Apparel & Footwear", "return_rate": 30, "shipped_back": "< 5%", "note": "Most returns landfilled or donated locally"},
    {"category": "Electronics", "return_rate": 15, "shipped_back": "20%", "note": "High-value items refurbished and re-exported"},
    {"category": "Furniture / Home", "return_rate": 10, "shipped_back": "< 2%", "note": "Too costly to return; often liquidated"},
    {"category": "Beauty / Personal Care", "return_rate": 8, "shipped_back": "0%", "note": "Hygiene regulations prevent re-import"},
    {"category": "Books / Media", "return_rate": 5, "shipped_back": "40%", "note": "Compact, economical to ship back"},
    {"category": "Sporting Goods", "return_rate": 12, "shipped_back": "10%", "note": "Partial return to origin for defect analysis"},
]

_PEAK_MONTHS = [
    {"month": "Jan", "idx": 72, "events": ["Post-holiday returns peak", "Inventory replenishment orders"]},
    {"month": "Feb", "idx": 55, "events": ["Chinese New Year (supply disruption)", "Factory restarts mid-month"]},
    {"month": "Mar", "idx": 68, "events": ["CNY freight surge", "Spring inventory buildup"]},
    {"month": "Apr", "idx": 75, "events": ["Easter promotions (EU)", "Pre-summer stock movement"]},
    {"month": "May", "idx": 80, "events": ["Mother's Day surge (US)", "Summer goods shipping"]},
    {"month": "Jun", "idx": 78, "events": ["Father's Day (US)", "Back-to-school early orders"]},
    {"month": "Jul", "idx": 95, "events": ["Amazon Prime Day 🔥", "Back-to-school peak begins"]},
    {"month": "Aug", "idx": 88, "events": ["Back-to-school peak", "Holiday inventory pre-positioning"]},
    {"month": "Sep", "idx": 92, "events": ["Holiday inventory buildup", "Q4 peak season onset"]},
    {"month": "Oct", "idx": 98, "events": ["Pre-holiday surge", "Container bookings premium"]},
    {"month": "Nov", "idx": 100, "events": ["Singles Day 11/11 🔥 (largest global)", "Black Friday", "Cyber Monday"]},
    {"month": "Dec", "idx": 90, "events": ["Holiday peak (US/EU)", "Last-mile capacity crunch"]},
]

_LEADING_INDICATORS = [
    {
        "metric": "US Retail E-Commerce Sales",
        "source": "US Census Bureau (quarterly)",
        "current": "$1.19T annualized",
        "signal": "BULLISH",
        "why": "Sustained 10%+ YoY growth drives container and air freight demand from Asia",
    },
    {
        "metric": "Chinese Cross-Border Parcel Volume",
        "source": "China Post / CAAC monthly",
        "current": "~7B parcels/yr",
        "signal": "BULLISH",
        "why": "Temu/Shein growth pushing record parcel volumes; direct indicator of transpacific air demand",
    },
    {
        "metric": "De Minimis Exemption Status",
        "source": "US CBP / EU Customs policy",
        "current": "AT RISK",
        "signal": "RISK",
        "why": "Elimination of $800 US threshold would collapse Temu/Shein model; major demand shock",
    },
    {
        "metric": "Amazon Logistics Expansion",
        "source": "Amazon press releases / SEC filings",
        "current": "Accelerating",
        "signal": "NEUTRAL",
        "why": "Amazon internalizing freight suppresses spot rates but adds air cargo demand for Prime",
    },
    {
        "metric": "Temu / Shein Order Volumes",
        "source": "SimilarWeb / App Annie / Bloomberg",
        "current": "~5M orders/day combined",
        "signal": "BULLISH",
        "why": "Direct driver of transpacific air freight; each 10% volume change = ~$0.15/kg rate move",
    },
]


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _signal_action(signal: str) -> str:
    """Map e-commerce signal labels to insight_card_html ACTION_COLORS keys."""
    s = signal.upper()
    return {
        "BULLISH":  "Prioritize",
        "BEARISH":  "Avoid",
        "NEUTRAL":  "Monitor",
        "RISK":     "Caution",
        "AT RISK":  "Caution",
    }.get(s, "Watch")


def _signal_score(signal: str) -> float:
    """Map signal label to a 0-1 score for insight_card_html progress bar."""
    s = signal.upper()
    return {
        "BULLISH":  0.85,
        "BEARISH":  0.15,
        "NEUTRAL":  0.50,
        "RISK":     0.30,
        "AT RISK":  0.30,
    }.get(s, 0.50)


def _month_color(idx: float) -> str:
    if idx >= 95:
        return C_LOW
    if idx >= 85:
        return C_MOD
    if idx >= 70:
        return C_HIGH
    return C_ACCENT


# ── Section renderers ──────────────────────────────────────────────────────────

def _render_kpi_dashboard() -> None:
    try:
        section_header(
            "E-Commerce Demand Dashboard",
            subtitle="Global market metrics and structural drivers",
        )

        metric_card_row(
            [
                {"label": "Global E-Commerce Market", "value": "$6.8T", "accent": C_HIGH, "delta": "+9.8% YoY", "delta_color": C_HIGH},
                {"label": "YoY E-Commerce Growth", "value": "+9.8%", "accent": C_MOD, "delta": "vs +8.1% prior year", "delta_color": C_TEXT2},
                {"label": "E-Com Share of Retail", "value": "20.1%", "accent": C_ACCENT, "delta": "+1.3pp YoY", "delta_color": C_HIGH},
                {"label": "Cross-Border Parcels", "value": "7.1B", "accent": C_PURPLE, "delta": "+22% YoY", "delta_color": C_HIGH},
                {"label": "Chinese E-Com Exports", "value": "~$300B", "accent": C_LOW, "delta": "Regulatory risk ↑", "delta_color": C_LOW},
            ],
            columns=5,
        )

        st.markdown(insight_card_html(
            title="Market Context — $6.8T global e-commerce",
            score=0.80,
            action="Prioritize",
            rationale=(
                "Global e-commerce reached $6.8 trillion in 2024 — 20.1% of all retail sales. "
                "Cross-border e-commerce is growing at 2x the rate of domestic e-commerce, driven "
                "overwhelmingly by Chinese platforms (Temu, Shein, AliExpress) which account for "
                "~40% of global cross-border parcel volume. This structural shift is reshaping "
                "transpacific freight demand, air cargo pricing, and last-mile infrastructure."
            ),
            category="MACRO",
        ), unsafe_allow_html=True)

        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("KPI dashboard render failed")
        st.error("Failed to render KPI dashboard.")


def _render_platform_table() -> None:
    try:
        section_header("E-Commerce Giants: Shipping Impact")

        rows = []
        for p in _PLATFORM_DATA:
            impact = p["rate_impact"]
            rate_color = (
                C_MOD if "Suppresses" in impact
                else C_LOW if ("surge" in impact.lower() or "driving" in impact.lower())
                else C_TEXT2
            )
            rows.append([
                _sans(f'{p["flag"]} {p["company"]}', color=p["color"], weight=700),
                _mono(f'${p["gmv"]}B', color=C_HIGH),
                _mono(f'{p["shipping_vol"]}B', color=C_ACCENT),
                _sans(p["routes"], color=C_TEXT2),
                _sans(p["carrier_strategy"], color=C_TEXT2),
                _sans(p["rate_impact"], color=rate_color),
            ])

        wsj_market_table(
            headers=["Company", "GMV ($B)", "Ship Vol (B parcels)", "Primary Routes", "Carrier Strategy", "Rate Impact"],
            rows=rows,
        )
        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Platform table render failed")
        st.error("Failed to render platform table.")


def _render_de_minimis() -> None:
    try:
        section_header(
            "Chinese E-Commerce Export Effect",
            subtitle="De Minimis Risk Analysis",
        )

        col1, col2 = st.columns([1, 1])
        with col1:
            metric_card_row(
                [
                    {"label": "US Threshold per Shipment", "value": "$800", "accent": C_HIGH,
                     "delta": "Duty-free ceiling", "delta_color": C_TEXT2},
                    {"label": "EU Threshold per Shipment", "value": "€150", "accent": C_MOD,
                     "delta": "Duty-free ceiling", "delta_color": C_TEXT2},
                ],
                columns=2,
            )

            st.markdown(insight_card_html(
                title="What is De Minimis?",
                score=0.70,
                action="Monitor",
                rationale=(
                    "De minimis thresholds allow imports below a set value to enter duty-free and "
                    "with minimal customs scrutiny. This rule is the legal backbone of the Temu/Shein "
                    "business model."
                ),
                category="MACRO",
            ), unsafe_allow_html=True)

            st.markdown(insight_card_html(
                title="Trump Admin Proposal — Eliminate De Minimis",
                score=0.85,
                action="Caution",
                rationale=(
                    "The Trump administration proposed eliminating the $800 de minimis exemption for "
                    "Chinese-origin goods. If enacted, every Temu/Shein parcel would face tariffs, "
                    "duties, and full customs scrutiny — effectively breaking the direct-to-consumer "
                    "China model. Current status (Mar 2026): executive order signed; legal challenges "
                    "ongoing."
                ),
                category="MACRO",
            ), unsafe_allow_html=True)

        with col2:
            metric_card_row(
                [
                    {"label": "Parcels entering US /yr", "value": "~1.4B", "accent": C_HIGH,
                     "delta": "+35% YoY; majority China-origin", "delta_color": C_TEXT2},
                    {"label": "Share from Chinese platforms", "value": "~60%", "accent": C_MOD,
                     "delta": "Temu, Shein, AliExpress", "delta_color": C_TEXT2},
                ],
                columns=1,
            )

            impact_rows = [
                ("Projected volume decline if eliminated", "−60 to −80%", C_LOW),
                ("Transpacific air cargo rate impact", "−15 to −25% kg rates", C_LOW),
                ("Ocean LCL container demand effect", "−5 to −10% demand", C_MOD),
                ("Re-routing via Mexico/Canada risk", "Significant", C_MOD),
                ("EU similar measures (2025 reform)", "€150 threshold removed", C_LOW),
            ]
            rows = [
                [_sans(label, color=C_TEXT2), _mono(val, color=color)]
                for label, val, color in impact_rows
            ]
            wsj_market_table(
                headers=["If De Minimis Eliminated — Projected Impact", "Estimate"],
                rows=rows,
            )
        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("De minimis section render failed")
        st.error("Failed to render de minimis analysis.")


def _render_peak_calendar() -> None:
    try:
        section_header(
            "Peak Season Calendar",
            subtitle="E-Commerce Shipping Demand Index (Nov=100)",
        )

        months = [m["month"] for m in _PEAK_MONTHS]
        idxs = [m["idx"] for m in _PEAK_MONTHS]
        colors = [_month_color(v) for v in idxs]
        hover_text = [" · ".join(m["events"]) for m in _PEAK_MONTHS]

        fig = go.Figure(go.Bar(
            x=months, y=idxs,
            marker=dict(color=colors, opacity=0.88),
            text=[str(v) for v in idxs], textposition="outside",
            textfont=dict(size=10),
            customdata=hover_text,
            hovertemplate="<b>%{x}</b>: %{y}<br>%{customdata}<extra></extra>",
        ))
        apply_dark_layout(fig, height=260)
        fig.update_layout(yaxis=dict(range=[0, 115], title="Demand Index"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        key_events = [
            ("Chinese New Year (Feb)",
             "Supply disruption — factory shutdowns cause 2–4 week shipping delays. Pre-CNY surge in Jan.",
             "Caution", 0.65),
            ("Amazon Prime Day (Jul)",
             "Single-day demand spike; pre-positioning drives June–July container bookings premium.",
             "Prioritize", 0.85),
            ("Singles Day 11/11 (Nov)",
             "World's largest shopping event. $150B+ GMV. Massive transpacific and air cargo surge.",
             "Prioritize", 0.95),
            ("Black Friday / Cyber Monday",
             "US demand peak. Combined with Singles Day aftermath creates Nov container shortage.",
             "Prioritize", 0.90),
            ("Holiday Peak (Nov–Dec)",
             "Sustained high demand. Last-mile capacity exhaustion, rate premiums of 20–40%.",
             "Caution", 0.80),
        ]
        cols = st.columns(len(key_events))
        for col, (title, desc, action, score) in zip(cols, key_events):
            with col:
                st.markdown(insight_card_html(
                    title=title,
                    score=score,
                    action=action,
                    rationale=desc,
                    category="ROUTE",
                ), unsafe_allow_html=True)
        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Peak calendar render failed")
        st.error("Failed to render peak calendar.")


def _render_b2c_b2b_split() -> None:
    try:
        section_header(
            "B2C vs B2B Freight Split",
            subtitle="By route — showing secular rise of B2C share",
        )

        rows = []
        for r in _ROUTE_SPLIT:
            trend_color = (
                C_HIGH if "↑" in r["trend"]
                else C_MOD if "stable" in r["trend"].lower()
                else C_TEXT2
            )
            rows.append([
                _sans(r["route"], color=C_TEXT, weight=600),
                _mono(f'{r["b2c"]}%', color=C_ACCENT),
                _mono(f'{r["b2b"]}%', color=C_MOD),
                _sans(r["avg_size"], color=C_TEXT2),
                _sans(r["mode"], color=C_TEXT2),
                _sans(r["trend"], color=trend_color),
            ])

        wsj_market_table(
            headers=["Route", "B2C Share %", "B2B Share %", "Avg Shipment Size", "Parcel vs Container", "Trend"],
            rows=rows,
        )

        st.markdown(insight_card_html(
            title="Key Structural Shift — Transpacific B2C share",
            score=0.80,
            action="Prioritize",
            rationale=(
                "Transpacific B2C share grew from ~15% in 2020 to 35% in 2025, driven entirely by "
                "Chinese platform exports. This favors LCL consolidation, air freight, and smaller, "
                "more frequent ocean bookings over traditional FCL B2B flows."
            ),
            category="ROUTE",
        ), unsafe_allow_html=True)

        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("B2C/B2B split render failed")
        st.error("Failed to render B2C/B2B split.")


def _render_returns() -> None:
    try:
        section_header(
            "Last Mile & Returns",
            subtitle="Reverse logistics and the cost of e-commerce returns",
        )

        col1, col2 = st.columns([3, 2])
        with col1:
            rows = []
            for r in _RETURN_RATES:
                rows.append([
                    _sans(r["category"], color=C_TEXT, weight=600),
                    _mono(f'{r["return_rate"]}%', color=C_LOW),
                    _mono(r["shipped_back"], color=C_MOD),
                    _sans(r["note"], color=C_TEXT2),
                ])
            wsj_market_table(
                headers=["Category", "Return Rate", "Shipped Back to Origin", "Notes"],
                rows=rows,
            )

        with col2:
            metrics = [
                {"label": "Global Return Cost /yr", "value": "$816B", "accent": C_LOW,
                 "delta": "~10–15% of retail GMV", "delta_color": C_TEXT2},
                {"label": "Container Utilization Hit", "value": "−3 to −5%", "accent": C_MOD,
                 "delta": "Returns reduce effective capacity", "delta_color": C_TEXT2},
                {"label": "Last-Mile Cost Inflation", "value": "+22%", "accent": C_LOW,
                 "delta": "Since 2020 — labor, fuel, failures", "delta_color": C_TEXT2},
                {"label": "Apparel Returns (Temu/Shein)", "value": "25–35%", "accent": C_LOW,
                 "delta": "Quality mismatch drives high returns", "delta_color": C_TEXT2},
                {"label": "Returns going back to China", "value": "<5%", "accent": C_HIGH,
                 "delta": "Most landfilled/donated locally", "delta_color": C_TEXT2},
            ]
            metric_card_row(metrics, columns=1)
        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Returns section render failed")
        st.error("Failed to render returns analysis.")


def _render_rate_impact_chart() -> None:
    try:
        section_header(
            "Rate Impact of E-Commerce Growth",
            subtitle="Volume vs air/ocean rate indices (2019=100)",
        )

        col1, col2 = st.columns([2, 1])
        with col1:
            years = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
            ecom_growth = [100, 128, 156, 168, 182, 200, 222]
            air_rates   = [100, 185, 310, 280, 195, 230, 265]
            ocean_rates = [100, 120, 380, 290, 140, 160, 185]

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=years, y=ecom_growth, name="E-Commerce Volume Index",
                line={"color": C_HIGH, "width": 2},
                fill="tozeroy", fillcolor=C_HIGH + "15",
            ))
            fig.add_trace(go.Scatter(
                x=years, y=air_rates, name="Air Cargo Rate Index",
                line={"color": C_MOD, "width": 2, "dash": "dot"},
            ))
            fig.add_trace(go.Scatter(
                x=years, y=ocean_rates, name="Ocean Spot Rate Index",
                line={"color": C_ACCENT, "width": 2, "dash": "dash"},
            ))
            apply_dark_layout(fig, height=300)
            fig.update_layout(
                legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
                xaxis=dict(dtick=1),
                yaxis=dict(title="Index (2019=100)"),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        with col2:
            insights = [
                ("Smaller, More Frequent Orders",
                 "E-commerce drives JIT inventory replenishment. Average order size down 18% since 2019. Favors LCL, air, and express.",
                 "Monitor", 0.65),
                ("Temu/Shein Air Cargo Boom",
                 "Ultra-fast fashion model requires air freight. Transpacific air demand up 40%+ YoY from Chinese platforms alone.",
                 "Prioritize", 0.90),
                ("Inventory Strategy Shift",
                 "B2C e-commerce breaks traditional quarterly bulk ordering. Importers now book 4–6x per year vs 1–2x previously.",
                 "Caution", 0.70),
                ("LCL vs FCL Rebalancing",
                 "LCL market growing 2x FCL growth rate. Parcel consolidation hubs in Yiwu, Guangzhou becoming critical nodes.",
                 "Prioritize", 0.80),
            ]
            for title, text, action, score in insights:
                st.markdown(insight_card_html(
                    title=title,
                    score=score,
                    action=action,
                    rationale=text,
                    category="ROUTE",
                ), unsafe_allow_html=True)
        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Rate impact chart render failed")
        st.error("Failed to render rate impact chart.")


def _render_leading_indicators() -> None:
    try:
        section_header(
            "Key Metrics to Watch",
            subtitle="Quarterly leading indicators for e-commerce shipping demand",
        )

        for ind in _LEADING_INDICATORS:
            rationale = (
                f"Source: {ind['source']}. Current reading: {ind['current']}. "
                f"Why it matters: {ind['why']}"
            )
            st.markdown(insight_card_html(
                title=ind["metric"],
                score=_signal_score(ind["signal"]),
                action=_signal_action(ind["signal"]),
                rationale=rationale,
                category="MACRO",
            ), unsafe_allow_html=True)

        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Leading indicators render failed")
        st.error("Failed to render leading indicators.")


# ── Main entry point ───────────────────────────────────────────────────────────

def render(macro_data=None, freight_data=None, insights=None) -> None:
    """Render the E-Commerce Driven Shipping Demand Intelligence tab."""
    try:
        page_header(
            title="E-Commerce Driven Shipping Demand",
            subtitle=(
                "How global e-commerce platforms — Amazon, Temu, Shein, AliExpress — are reshaping "
                "freight demand, air cargo pricing, container routes, and last-mile logistics worldwide."
            ),
            badge_text="E-COMMERCE",
            badge_color=C_ACCENT,
        )
    except Exception:
        logger.exception("Header render failed")

    _render_kpi_dashboard()
    _render_platform_table()
    _render_de_minimis()
    _render_peak_calendar()
    _render_b2c_b2b_split()
    _render_returns()
    _render_rate_impact_chart()
    _render_leading_indicators()

    try:
        st.markdown(source_footer(_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Footer render failed")
