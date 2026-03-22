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

import random
from datetime import date

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Color palette ──────────────────────────────────────────────────────────────
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

def _card_style(border_color: str = C_BORDER, pad: str = "18px 20px") -> str:
    return (
        f"background:{C_CARD};border:1px solid {border_color};"
        f"border-radius:10px;padding:{pad};margin-bottom:16px;"
    )


def _signal_badge(signal: str) -> str:
    colors = {"BULLISH": C_HIGH, "BEARISH": C_LOW, "NEUTRAL": C_MOD, "RISK": C_LOW, "AT RISK": C_LOW}
    c = colors.get(signal.upper(), C_TEXT3)
    return (
        f'<span style="background:{c}22;color:{c};border:1px solid {c}44;'
        f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;'
        f'letter-spacing:0.05em;">{signal}</span>'
    )


def _pct_bar(pct: float, color: str, bg: str = "#1e293b", height: str = "8px") -> str:
    return (
        f'<div style="background:{bg};border-radius:4px;height:{height};width:100%;margin-top:4px;">'
        f'<div style="background:{color};width:{min(pct,100):.1f}%;height:100%;border-radius:4px;"></div>'
        f'</div>'
    )


def _month_color(idx: float) -> str:
    if idx >= 95:
        return C_LOW
    if idx >= 85:
        return C_MOD
    if idx >= 70:
        return C_HIGH
    return C_ACCENT


# ── Section renderers ──────────────────────────────────────────────────────────

def _render_kpi_dashboard(macro_data: dict | None) -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin-bottom:16px;">'
            f'&#128200; E-Commerce Demand Dashboard</div>',
            unsafe_allow_html=True,
        )

        kpis = [
            {"label": "Global E-Commerce Market", "value": "$6.8T", "sub": "2024 total market size", "color": C_HIGH, "delta": "+9.8% YoY"},
            {"label": "YoY E-Commerce Growth", "value": "+9.8%", "sub": "Global retail e-commerce", "color": C_MOD, "delta": "vs +8.1% prior year"},
            {"label": "E-Commerce Share of Retail", "value": "20.1%", "sub": "Share of total global retail", "color": C_ACCENT, "delta": "+1.3pp YoY"},
            {"label": "Cross-Border Parcel Volume", "value": "7.1B", "sub": "Parcels shipped/year globally", "color": C_PURPLE, "delta": "+22% YoY"},
            {"label": "Chinese E-Com Exports", "value": "~$300B", "sub": "Temu + Shein + AliExpress GMV", "color": C_LOW, "delta": "Regulatory risk ↑"},
        ]

        cols = st.columns(5)
        for col, kpi in zip(cols, kpis):
            with col:
                st.markdown(
                    f'<div style="{_card_style(kpi["color"] + "44")}">'
                    f'<div style="font-size:11px;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">{kpi["label"]}</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{kpi["color"]};line-height:1.1;">{kpi["value"]}</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">{kpi["sub"]}</div>'
                    f'<div style="font-size:11px;color:{C_TEXT3};margin-top:4px;">{kpi["delta"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown(
            f'<div style="{_card_style(C_ACCENT + "44")}">'
            f'<div style="font-size:13px;font-weight:600;color:{C_ACCENT};margin-bottom:8px;">Market Context</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};line-height:1.7;">'
            f'Global e-commerce reached <span style="color:{C_HIGH};font-weight:600;">$6.8 trillion</span> in 2024, '
            f'representing <span style="color:{C_HIGH};font-weight:600;">20.1%</span> of all retail sales. '
            f'Cross-border e-commerce is growing at <span style="color:{C_MOD};font-weight:600;">2x</span> the rate of domestic e-commerce, '
            f'driven overwhelmingly by Chinese platforms — Temu, Shein, and AliExpress — which collectively account for '
            f'<span style="color:{C_LOW};font-weight:600;">~40%</span> of global cross-border parcel volume. '
            f'This structural shift is fundamentally reshaping transpacific freight demand, air cargo pricing, and '
            f'last-mile infrastructure across North America and Europe.'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("KPI dashboard render failed")
        st.error("Failed to render KPI dashboard.")


def _render_platform_table() -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin:24px 0 16px;">'
            f'&#128666; E-Commerce Giants: Shipping Impact</div>',
            unsafe_allow_html=True,
        )

        header_style = (
            f"background:{C_SURFACE};padding:10px 12px;font-size:11px;font-weight:700;"
            f"color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;"
        )
        cell_style = (
            f"padding:12px;font-size:12px;color:{C_TEXT2};"
            f"border-top:1px solid {C_BORDER};vertical-align:top;"
        )

        rows_html = ""
        for p in _PLATFORM_DATA:
            rate_color = C_MOD if "Suppresses" in p["rate_impact"] else (C_LOW if "surge" in p["rate_impact"].lower() or "driving" in p["rate_impact"].lower() else C_TEXT2)
            rows_html += (
                f'<tr>'
                f'<td style="{cell_style}">'
                f'<span style="color:{p["color"]};font-weight:700;">{p["flag"]} {p["company"]}</span>'
                f'</td>'
                f'<td style="{cell_style};color:{C_HIGH};font-weight:600;">${p["gmv"]}B</td>'
                f'<td style="{cell_style};color:{C_ACCENT};font-weight:600;">{p["shipping_vol"]}B</td>'
                f'<td style="{cell_style}">{p["routes"]}</td>'
                f'<td style="{cell_style}">{p["carrier_strategy"]}</td>'
                f'<td style="{cell_style};color:{rate_color};">{p["rate_impact"]}</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="{_card_style()};padding:0;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th style="{header_style}">Company</th>'
            f'<th style="{header_style}">GMV ($B)</th>'
            f'<th style="{header_style}">Ship Vol (B parcels)</th>'
            f'<th style="{header_style}">Primary Routes</th>'
            f'<th style="{header_style}">Carrier Strategy</th>'
            f'<th style="{header_style}">Rate Impact</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Platform table render failed")
        st.error("Failed to render platform table.")


def _render_de_minimis() -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin:24px 0 16px;">'
            f'&#127464;&#127475; Chinese E-Commerce Export Effect: De Minimis Risk</div>',
            unsafe_allow_html=True,
        )

        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown(
                f'<div style="{_card_style(C_ACCENT + "55")}">'
                f'<div style="font-size:13px;font-weight:700;color:{C_ACCENT};margin-bottom:12px;">What is De Minimis?</div>'
                f'<div style="font-size:13px;color:{C_TEXT2};line-height:1.7;margin-bottom:12px;">'
                f'De minimis thresholds allow imports below a set value to enter duty-free and with minimal customs scrutiny. '
                f'This rule is the legal backbone of the Temu/Shein business model.'
                f'</div>'
                f'<div style="display:flex;gap:16px;margin-top:8px;">'
                f'<div style="flex:1;background:{C_SURFACE};border-radius:8px;padding:12px;text-align:center;">'
                f'<div style="font-size:24px;font-weight:800;color:{C_HIGH};">$800</div>'
                f'<div style="font-size:11px;color:{C_TEXT3};">US threshold per shipment</div>'
                f'</div>'
                f'<div style="flex:1;background:{C_SURFACE};border-radius:8px;padding:12px;text-align:center;">'
                f'<div style="font-size:24px;font-weight:800;color:{C_MOD};">€150</div>'
                f'<div style="font-size:11px;color:{C_TEXT3};">EU threshold per shipment</div>'
                f'</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            st.markdown(
                f'<div style="{_card_style(C_LOW + "44")}">'
                f'<div style="font-size:13px;font-weight:700;color:{C_LOW};margin-bottom:10px;">&#9888; Trump Admin Proposal: Eliminate De Minimis</div>'
                f'<div style="font-size:13px;color:{C_TEXT2};line-height:1.7;">'
                f'The Trump administration proposed <strong style="color:{C_LOW};">eliminating the $800 de minimis exemption</strong> for Chinese-origin goods. '
                f'If enacted, every Temu/Shein parcel would face tariffs, duties, and full customs scrutiny — effectively breaking the '
                f'direct-to-consumer China model.'
                f'</div>'
                f'<div style="margin-top:12px;padding:8px;background:{C_SURFACE};border-radius:6px;">'
                f'<div style="font-size:11px;color:{C_TEXT3};">Current status (Mar 2026)</div>'
                f'<div style="font-size:13px;color:{C_MOD};font-weight:600;">Executive order signed; legal challenges ongoing</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with col2:
            st.markdown(
                f'<div style="{_card_style(C_BORDER)}">'
                f'<div style="font-size:13px;font-weight:700;color:{C_TEXT};margin-bottom:12px;">Current Volume</div>'
                f'<div style="margin-bottom:14px;">'
                f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">Parcels entering US under de minimis/year</div>'
                f'<div style="font-size:28px;font-weight:800;color:{C_HIGH};">~1.4B</div>'
                f'<div style="font-size:11px;color:{C_TEXT3};">+35% YoY; majority from China</div>'
                f'</div>'
                f'<div style="margin-bottom:14px;">'
                f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">Share from Chinese platforms</div>'
                f'<div style="font-size:28px;font-weight:800;color:{C_MOD};">~60%</div>'
                f'{_pct_bar(60, C_MOD)}'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            impact_rows = [
                ("Projected volume decline if eliminated", "−60 to −80%", C_LOW),
                ("Transpacific air cargo rate impact", "−15 to −25% kg rates", C_LOW),
                ("Ocean LCL container demand effect", "−5 to −10% demand", C_MOD),
                ("Re-routing via Mexico/Canada risk", "Significant", C_MOD),
                ("EU similar measures (2025 reform)", "€150 threshold removed", C_LOW),
            ]
            rows_html = ""
            for label, val, color in impact_rows:
                rows_html += (
                    f'<tr>'
                    f'<td style="padding:8px 10px;font-size:12px;color:{C_TEXT2};border-top:1px solid {C_BORDER};">{label}</td>'
                    f'<td style="padding:8px 10px;font-size:12px;color:{color};font-weight:600;border-top:1px solid {C_BORDER};text-align:right;">{val}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<div style="{_card_style()};padding:0;overflow:hidden;">'
                f'<div style="padding:10px 12px;background:{C_SURFACE};font-size:11px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;">'
                f'If De Minimis is Eliminated — Projected Impacts</div>'
                f'<table style="width:100%;border-collapse:collapse;">'
                f'<tbody>{rows_html}</tbody>'
                f'</table>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("De minimis section render failed")
        st.error("Failed to render de minimis analysis.")


def _render_peak_calendar() -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin:24px 0 16px;">'
            f'&#128197; Peak Season Calendar — E-Commerce Shipping Demand Index</div>',
            unsafe_allow_html=True,
        )

        bars_html = ""
        for m in _PEAK_MONTHS:
            c = _month_color(m["idx"])
            h = max(20, int(m["idx"] * 0.9))
            events_str = " · ".join(m["events"])
            bars_html += (
                f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;" title="{events_str}">'
                f'<div style="font-size:11px;color:{c};font-weight:700;">{m["idx"]}</div>'
                f'<div style="width:100%;background:{C_SURFACE};border-radius:4px 4px 0 0;height:90px;display:flex;align-items:flex-end;">'
                f'<div style="width:100%;height:{h}%;background:{c};border-radius:4px 4px 0 0;opacity:0.85;"></div>'
                f'</div>'
                f'<div style="font-size:11px;color:{C_TEXT3};">{m["month"]}</div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="{_card_style()}">'
            f'<div style="display:flex;gap:6px;align-items:flex-end;margin-bottom:16px;">{bars_html}</div>'
            f'<div style="display:flex;gap:16px;font-size:11px;">'
            f'<span><span style="color:{C_LOW};">&#9632;</span> Peak (&ge;95)</span>'
            f'<span><span style="color:{C_MOD};">&#9632;</span> High (85–94)</span>'
            f'<span><span style="color:{C_HIGH};">&#9632;</span> Elevated (70–84)</span>'
            f'<span><span style="color:{C_ACCENT};">&#9632;</span> Normal (&lt;70)</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        key_events = [
            ("🧨 Chinese New Year (Feb)", "Supply disruption — factory shutdowns cause 2–4 week shipping delays. Pre-CNY surge in Jan.", C_MOD),
            ("🔥 Amazon Prime Day (Jul)", "Single-day demand spike; pre-positioning drives June–July container bookings premium.", C_LOW),
            ("🛍 Singles Day 11/11 (Nov)", "World's largest shopping event. $150B+ GMV. Massive transpacific and air cargo surge.", C_LOW),
            ("🦃 Black Friday / Cyber Monday", "US demand peak. Combined with Singles Day aftermath creates Nov container shortage.", C_LOW),
            ("🎁 Holiday Peak (Nov–Dec)", "Sustained high demand. Last-mile capacity exhaustion, rate premiums of 20–40%.", C_MOD),
        ]
        cols = st.columns(len(key_events))
        for col, (title, desc, color) in zip(cols, key_events):
            with col:
                st.markdown(
                    f'<div style="{_card_style(color + "44")};padding:12px;">'
                    f'<div style="font-size:12px;font-weight:700;color:{color};margin-bottom:6px;">{title}</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};line-height:1.5;">{desc}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception:
        logger.exception("Peak calendar render failed")
        st.error("Failed to render peak calendar.")


def _render_b2c_b2b_split() -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin:24px 0 16px;">'
            f'&#128230; B2C vs B2B Freight Split by Route</div>',
            unsafe_allow_html=True,
        )

        header_style = f"background:{C_SURFACE};padding:10px 12px;font-size:11px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;"
        cell_style = f"padding:11px 12px;font-size:12px;color:{C_TEXT2};border-top:1px solid {C_BORDER};vertical-align:middle;"

        rows_html = ""
        for r in _ROUTE_SPLIT:
            b2c_bar = _pct_bar(r["b2c"], C_ACCENT, C_SURFACE, "6px")
            trend_color = C_HIGH if "↑" in r["trend"] else (C_MOD if "stable" in r["trend"].lower() else C_TEXT2)
            rows_html += (
                f'<tr>'
                f'<td style="{cell_style};font-weight:600;color:{C_TEXT};">{r["route"]}</td>'
                f'<td style="{cell_style};">'
                f'<div style="display:flex;align-items:center;gap:8px;">'
                f'<span style="color:{C_ACCENT};font-weight:700;min-width:32px;">{r["b2c"]}%</span>'
                f'<div style="flex:1;">{b2c_bar}</div>'
                f'</div>'
                f'</td>'
                f'<td style="{cell_style};color:{C_MOD};font-weight:600;">{r["b2b"]}%</td>'
                f'<td style="{cell_style};">{r["avg_size"]}</td>'
                f'<td style="{cell_style};">{r["mode"]}</td>'
                f'<td style="{cell_style};color:{trend_color};">{r["trend"]}</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="{_card_style()};padding:0;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th style="{header_style}">Route</th>'
            f'<th style="{header_style}">B2C Share %</th>'
            f'<th style="{header_style}">B2B Share %</th>'
            f'<th style="{header_style}">Avg Shipment Size</th>'
            f'<th style="{header_style}">Parcel vs Container</th>'
            f'<th style="{header_style}">Trend</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<div style="{_card_style(C_ACCENT + "33")};margin-top:4px;">'
            f'<div style="font-size:12px;color:{C_TEXT2};line-height:1.6;">'
            f'<strong style="color:{C_ACCENT};">Key Structural Shift:</strong> The transpacific route has seen B2C share grow from '
            f'<span style="color:{C_MOD};font-weight:600;">~15% in 2020</span> to '
            f'<span style="color:{C_HIGH};font-weight:600;">35% in 2025</span>, driven entirely by Chinese platform exports. '
            f'This B2C growth favors <strong>LCL consolidation, air freight, and smaller, more frequent ocean bookings</strong> '
            f'over traditional full-container-load (FCL) B2B flows.'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("B2C/B2B split render failed")
        st.error("Failed to render B2C/B2B split.")


def _render_returns() -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin:24px 0 16px;">'
            f'&#128260; Last Mile & Returns — Reverse Logistics Analysis</div>',
            unsafe_allow_html=True,
        )

        col1, col2 = st.columns([3, 2])
        with col1:
            header_style = f"background:{C_SURFACE};padding:10px 12px;font-size:11px;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.07em;"
            cell_style = f"padding:10px 12px;font-size:12px;color:{C_TEXT2};border-top:1px solid {C_BORDER};vertical-align:middle;"

            rows_html = ""
            for r in _RETURN_RATES:
                bar = _pct_bar(r["return_rate"], C_LOW, C_SURFACE, "6px")
                rows_html += (
                    f'<tr>'
                    f'<td style="{cell_style};color:{C_TEXT};font-weight:600;">{r["category"]}</td>'
                    f'<td style="{cell_style};">'
                    f'<div style="display:flex;align-items:center;gap:8px;">'
                    f'<span style="color:{C_LOW};font-weight:700;min-width:30px;">{r["return_rate"]}%</span>'
                    f'<div style="flex:1;">{bar}</div>'
                    f'</div>'
                    f'</td>'
                    f'<td style="{cell_style};color:{C_MOD};">{r["shipped_back"]}</td>'
                    f'<td style="{cell_style};">{r["note"]}</td>'
                    f'</tr>'
                )

            st.markdown(
                f'<div style="{_card_style()};padding:0;overflow:hidden;">'
                f'<table style="width:100%;border-collapse:collapse;">'
                f'<thead><tr>'
                f'<th style="{header_style}">Category</th>'
                f'<th style="{header_style}">Return Rate</th>'
                f'<th style="{header_style}">Shipped Back to Origin</th>'
                f'<th style="{header_style}">Notes</th>'
                f'</tr></thead>'
                f'<tbody>{rows_html}</tbody>'
                f'</table>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with col2:
            metrics = [
                ("Global E-Commerce Return Cost", "$816B/yr", C_LOW, "Retailers bear ~10–15% of GMV in returns"),
                ("Container Utilization Hit", "−3 to −5%", C_MOD, "Returns reduce effective container capacity"),
                ("Last-Mile Cost Inflation", "+22% since 2020", C_LOW, "Labor, fuel, failed delivery attempts"),
                ("Apparel Return Rate (Temu/Shein)", "~25–35%", C_LOW, "Quality mismatch drives high returns"),
                ("Returns going back to China", "<5% of volume", C_HIGH, "Most landfilled, donated, or liquidated locally"),
            ]
            for label, val, color, note in metrics:
                st.markdown(
                    f'<div style="{_card_style()};padding:12px 16px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
                    f'<div style="font-size:12px;color:{C_TEXT2};flex:1;padding-right:8px;">{label}</div>'
                    f'<div style="font-size:16px;font-weight:800;color:{color};white-space:nowrap;">{val}</div>'
                    f'</div>'
                    f'<div style="font-size:11px;color:{C_TEXT3};margin-top:4px;">{note}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception:
        logger.exception("Returns section render failed")
        st.error("Failed to render returns analysis.")


def _render_rate_impact_chart() -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin:24px 0 16px;">'
            f'&#128200; Rate Impact of E-Commerce Growth</div>',
            unsafe_allow_html=True,
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
            fig.update_layout(
                paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                font={"color": C_TEXT2, "size": 11},
                margin={"t": 24, "b": 24, "l": 8, "r": 8},
                height=300,
                legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
                xaxis={"showgrid": False, "color": C_TEXT3, "dtick": 1},
                yaxis={"showgrid": True, "gridcolor": C_BORDER, "color": C_TEXT3, "title": "Index (2019=100)"},
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            insights = [
                ("Smaller, More Frequent Orders", "E-commerce drives JIT inventory replenishment. Average order size down 18% since 2019. Favors LCL, air, and express.", C_ACCENT),
                ("Temu/Shein Air Cargo Boom", "Ultra-fast fashion model requires air freight. Transpacific air demand up 40%+ YoY from Chinese platforms alone.", C_LOW),
                ("Inventory Strategy Shift", "B2C e-commerce breaks traditional quarterly bulk ordering. Importers now book 4–6x per year vs 1–2x previously.", C_MOD),
                ("LCL vs FCL Rebalancing", "LCL market growing 2x FCL growth rate. Parcel consolidation hubs in Yiwu, Guangzhou becoming critical nodes.", C_HIGH),
            ]
            for title, text, color in insights:
                st.markdown(
                    f'<div style="{_card_style(color + "33")};padding:12px;">'
                    f'<div style="font-size:12px;font-weight:700;color:{color};margin-bottom:5px;">{title}</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};line-height:1.5;">{text}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception:
        logger.exception("Rate impact chart render failed")
        st.error("Failed to render rate impact chart.")


def _render_leading_indicators() -> None:
    try:
        st.markdown(
            f'<div style="font-size:18px;font-weight:700;color:{C_TEXT};margin:24px 0 16px;">'
            f'&#128270; Key Metrics to Watch — Quarterly Leading Indicators</div>',
            unsafe_allow_html=True,
        )

        for i, ind in enumerate(_LEADING_INDICATORS, start=1):
            signal_color = {
                "BULLISH": C_HIGH, "BEARISH": C_LOW, "NEUTRAL": C_MOD, "RISK": C_LOW, "AT RISK": C_LOW
            }.get(ind["signal"].upper(), C_TEXT3)

            st.markdown(
                f'<div style="{_card_style(signal_color + "33")}">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">'
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<div style="background:{signal_color}22;color:{signal_color};width:26px;height:26px;border-radius:50%;'
                f'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:800;">{i}</div>'
                f'<div style="font-size:14px;font-weight:700;color:{C_TEXT};">{ind["metric"]}</div>'
                f'</div>'
                f'{_signal_badge(ind["signal"])}'
                f'</div>'
                f'<div style="display:flex;gap:24px;margin-bottom:8px;">'
                f'<div>'
                f'<div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em;">Source</div>'
                f'<div style="font-size:12px;color:{C_TEXT2};">{ind["source"]}</div>'
                f'</div>'
                f'<div>'
                f'<div style="font-size:10px;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em;">Current Reading</div>'
                f'<div style="font-size:13px;font-weight:700;color:{signal_color};">{ind["current"]}</div>'
                f'</div>'
                f'</div>'
                f'<div style="font-size:12px;color:{C_TEXT2};line-height:1.5;padding-left:36px;">'
                f'<strong style="color:{C_TEXT3};">Why it matters:</strong> {ind["why"]}'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("Leading indicators render failed")
        st.error("Failed to render leading indicators.")


# ── Main entry point ───────────────────────────────────────────────────────────

def render(macro_data=None, freight_data=None, insights=None) -> None:
    """Render the E-Commerce Driven Shipping Demand Intelligence tab."""
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_CARD} 0%,{C_SURFACE} 100%);'
            f'border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px;margin-bottom:20px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
            f'<div>'
            f'<div style="font-size:22px;font-weight:800;color:{C_TEXT};margin-bottom:6px;">'
            f'E-Commerce Driven Shipping Demand</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};max-width:680px;line-height:1.6;">'
            f'How global e-commerce platforms — Amazon, Temu, Shein, AliExpress — are reshaping '
            f'freight demand, air cargo pricing, container routes, and last-mile logistics worldwide.'
            f'</div>'
            f'</div>'
            f'<div style="text-align:right;min-width:120px;">'
            f'<div style="font-size:11px;color:{C_TEXT3};">Data as of</div>'
            f'<div style="font-size:13px;font-weight:700;color:{C_ACCENT};">Mar 2026</div>'
            f'<div style="margin-top:6px;">{_signal_badge("BULLISH")}</div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Header render failed")

    _render_kpi_dashboard(macro_data)
    _render_platform_table()
    _render_de_minimis()
    _render_peak_calendar()
    _render_b2c_b2b_split()
    _render_returns()
    _render_rate_impact_chart()
    _render_leading_indicators()

    try:
        st.markdown(
            f'<div style="{_card_style(C_BORDER)};margin-top:8px;">'
            f'<div style="font-size:11px;color:{C_TEXT3};line-height:1.6;">'
            f'<strong style="color:{C_TEXT2};">Sources & Methodology:</strong> '
            f'GMV and volume estimates from company filings, Bloomberg, eMarketer, and industry reports. '
            f'De minimis data from US CBP and USITC. Peak season indices are proprietary composite indicators. '
            f'Rate indices normalized to 2019=100 baseline. All figures approximate; verify before trading decisions.'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Footer render failed")
