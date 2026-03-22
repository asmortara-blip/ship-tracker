"""
Scenario Analysis & Stress Testing Tab
========================================
Institutional scenario analysis covering:

  Section 1 — Scenario Dashboard (hero metrics)
  Section 2 — Base / Bull / Bear 3-column comparison
  Section 3 — Scenario Comparison Table (6 scenarios × 8 metrics)
  Section 4 — Interactive Scenario Builder (st.form + sliders)
  Section 5 — Event Probability Tracker
  Section 6 — Monte Carlo Fan Chart (500 paths, 90-day horizon)
"""
from __future__ import annotations

import numpy as np
import streamlit as st
import plotly.graph_objects as go
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


# ── Helpers ────────────────────────────────────────────────────────────────────
def _val(data: dict | None, *keys, default=None):
    """Safe nested dict lookup."""
    try:
        v = data
        for k in keys:
            v = v[k]
        return v
    except Exception:
        return default


def _pct(v, decimals: int = 1) -> str:
    try:
        return f"{float(v):+.{decimals}f}%"
    except Exception:
        return "N/A"


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo


def _impact_color(val: float | None) -> str:
    if val is None:
        return C_TEXT3
    if val > 0:
        return C_HIGH
    if val < 0:
        return C_LOW
    return C_TEXT2


def _prob_bar_html(pct: int, color: str) -> str:
    filled = _clamp(pct, 0, 100)
    return (
        f'<div style="background:{C_SURFACE};border-radius:4px;height:6px;width:100%;margin-top:4px;">'
        f'<div style="background:{color};width:{filled}%;height:100%;border-radius:4px;"></div>'
        f'</div>'
    )


# ── Section 1: Scenario Dashboard ──────────────────────────────────────────────
def _render_dashboard(macro_data, freight_data, insights):
    logger.debug("rendering scenario dashboard hero")
    try:
        active = 6
        base_prob = 60
        upside_skew = 25
        downside_skew = 15

        bdi_val = _val(macro_data, "bdi", "value", default=1_850)
        wci_val = _val(freight_data, "wci", "current", default=3_200)

        skew_color = C_HIGH if upside_skew >= downside_skew else C_LOW
        skew_label = "Upside" if upside_skew >= downside_skew else "Downside"
        skew_pct   = upside_skew if upside_skew >= downside_skew else downside_skew

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:14px;padding:24px 28px 20px;margin-bottom:20px;">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;color:{C_TEXT3};text-transform:uppercase;margin-bottom:14px;">SCENARIO INTELLIGENCE DASHBOARD</div>'
            f'<div style="display:flex;gap:32px;flex-wrap:wrap;align-items:center;">'
            f'<div style="flex:1;min-width:140px;">'
            f'<div style="font-size:42px;font-weight:800;color:{C_ACCENT};line-height:1;">{active}</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};margin-top:4px;">Active Scenarios Tracked</div>'
            f'</div>'
            f'<div style="flex:1;min-width:140px;border-left:1px solid {C_BORDER};padding-left:28px;">'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">BASE CASE PROBABILITY</div>'
            f'<div style="font-size:36px;font-weight:700;color:{C_TEXT};">{base_prob}%</div>'
            f'<div style="background:{C_SURFACE};border-radius:4px;height:6px;margin-top:8px;">'
            f'<div style="background:{C_ACCENT};width:{base_prob}%;height:100%;border-radius:4px;"></div>'
            f'</div>'
            f'</div>'
            f'<div style="flex:1;min-width:140px;border-left:1px solid {C_BORDER};padding-left:28px;">'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">RISK SKEW</div>'
            f'<div style="font-size:36px;font-weight:700;color:{skew_color};">{skew_label}</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};">{skew_pct}% probability weight</div>'
            f'</div>'
            f'<div style="flex:1;min-width:140px;border-left:1px solid {C_BORDER};padding-left:28px;">'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">CURRENT BDI</div>'
            f'<div style="font-size:36px;font-weight:700;color:{C_TEXT};">{int(bdi_val):,}</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};">Baltic Dry Index</div>'
            f'</div>'
            f'<div style="flex:1;min-width:140px;border-left:1px solid {C_BORDER};padding-left:28px;">'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:4px;">CURRENT WCI</div>'
            f'<div style="font-size:36px;font-weight:700;color:{C_TEXT};">${int(wci_val):,}</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};">World Container Index</div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    except Exception as exc:
        logger.exception("dashboard hero error")
        st.error(f"Dashboard error: {exc}")


# ── Section 2: Base / Bull / Bear Comparison ───────────────────────────────────
def _render_three_scenarios(macro_data, freight_data):
    logger.debug("rendering three-scenario comparison")
    try:
        bdi_base = int(_val(macro_data, "bdi", "value", default=1_850))
        bdi_bull = int(bdi_base * 1.45)
        bdi_bear = int(bdi_base * 0.62)

        wci_base = int(_val(freight_data, "wci", "current", default=3_200))
        wci_bull = int(wci_base * 1.60)
        wci_bear = int(wci_base * 0.55)

        scenarios = [
            {
                "label": "BASE CASE",
                "prob": 60,
                "color": C_ACCENT,
                "bdi": bdi_base,
                "wci": wci_base,
                "util": 84,
                "fr_impact": "+0%",
                "fr_color": C_TEXT2,
                "headline": "Moderate growth, stable rates",
                "equity": "Neutral — sector inline with market",
                "equity_color": C_TEXT2,
                "assumptions": [
                    "Global GDP growth ~2.4%",
                    "Fleet growth 3.2% YoY",
                    "Red Sea disruptions persist H1",
                    "China PMI stabilizes 50–52",
                    "Oil price $75–$85/bbl range",
                ],
            },
            {
                "label": "BULL CASE",
                "prob": 25,
                "color": C_HIGH,
                "bdi": bdi_bull,
                "wci": wci_bull,
                "util": 92,
                "fr_impact": "+38–55%",
                "fr_color": C_HIGH,
                "headline": "Supply disruptions + demand recovery",
                "equity": "Strongly bullish — shipping equities +30–60%",
                "equity_color": C_HIGH,
                "assumptions": [
                    "Chokepoint disruptions extend 12+ mo",
                    "China stimulus drives import surge",
                    "New orderbook delays slip further",
                    "Port congestion re-emerges globally",
                    "VLCC demand spikes on rerouting",
                ],
            },
            {
                "label": "BEAR CASE",
                "prob": 15,
                "color": C_LOW,
                "bdi": bdi_bear,
                "wci": wci_bear,
                "util": 72,
                "fr_impact": "-30–45%",
                "fr_color": C_LOW,
                "headline": "Demand slowdown + capacity glut",
                "equity": "Bearish — shipping equities -20–40%",
                "equity_color": C_LOW,
                "assumptions": [
                    "US recession reduces import demand",
                    "Newbuild deliveries accelerate",
                    "China property sector re-deteriorates",
                    "Red Sea normalizes, routes shorten",
                    "Consumer spending contracts",
                ],
            },
        ]

        cols = st.columns(3)
        for col, sc in zip(cols, scenarios):
            with col:
                assumptions_html = "".join(
                    f'<div style="display:flex;gap:8px;margin-bottom:5px;">'
                    f'<span style="color:{sc["color"]};font-size:10px;margin-top:2px;">▸</span>'
                    f'<span style="font-size:12px;color:{C_TEXT2};">{a}</span>'
                    f'</div>'
                    for a in sc["assumptions"]
                )
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {sc["color"]}33;border-top:3px solid {sc["color"]};border-radius:12px;padding:20px;height:100%;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
                    f'<span style="font-size:11px;font-weight:700;letter-spacing:2px;color:{sc["color"]};">{sc["label"]}</span>'
                    f'<span style="background:{sc["color"]}22;color:{sc["color"]};font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;">{sc["prob"]}%</span>'
                    f'</div>'
                    f'<div style="font-size:13px;color:{C_TEXT2};margin-bottom:16px;font-style:italic;">{sc["headline"]}</div>'
                    f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px;">'
                    f'<div style="background:{C_SURFACE};border-radius:8px;padding:10px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:2px;">BDI</div>'
                    f'<div style="font-size:20px;font-weight:700;color:{C_TEXT};">{sc["bdi"]:,}</div>'
                    f'</div>'
                    f'<div style="background:{C_SURFACE};border-radius:8px;padding:10px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:2px;">WCI ($/FEU)</div>'
                    f'<div style="font-size:20px;font-weight:700;color:{C_TEXT};">${sc["wci"]:,}</div>'
                    f'</div>'
                    f'<div style="background:{C_SURFACE};border-radius:8px;padding:10px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:2px;">Fleet Utilization</div>'
                    f'<div style="font-size:20px;font-weight:700;color:{C_TEXT};">{sc["util"]}%</div>'
                    f'</div>'
                    f'<div style="background:{C_SURFACE};border-radius:8px;padding:10px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:2px;">Rate Impact</div>'
                    f'<div style="font-size:18px;font-weight:700;color:{sc["fr_color"]};">{sc["fr_impact"]}</div>'
                    f'</div>'
                    f'</div>'
                    f'<div style="font-size:10px;font-weight:700;letter-spacing:1px;color:{C_TEXT3};margin-bottom:8px;">KEY ASSUMPTIONS</div>'
                    f'{assumptions_html}'
                    f'<div style="margin-top:14px;padding:10px;background:{sc["color"]}11;border-radius:8px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:3px;">EQUITY SECTOR IMPACT</div>'
                    f'<div style="font-size:12px;font-weight:600;color:{sc["equity_color"]};">{sc["equity"]}</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
    except Exception as exc:
        logger.exception("three-scenario render error")
        st.error(f"Three-scenario error: {exc}")


# ── Section 3: Scenario Comparison Table ──────────────────────────────────────
def _render_comparison_table():
    logger.debug("rendering scenario comparison table")
    try:
        scenarios = [
            ("Base Case",               C_ACCENT, "+0%",    "+0%",   "+0%",   "8–12%",  60, "12 mo", "PMI inflection",   "Recession shock"),
            ("Bull — Supply Shock",     C_HIGH,   "+42%",   "+55%",  "+38%",  "28–40%", 25, "6–9 mo","Chokepoint event",  "Normalization"),
            ("Bull — Demand Surge",     "#34d399", "+28%",  "+35%",  "+18%",  "18–28%", 15, "9–15mo","China stimulus",    "Overcapacity"),
            ("Bear — Recession",        C_LOW,    "-38%",   "-42%",  "-25%",  "-15–25%",12, "12–18mo","US GDP<0",         "Prolonged slump"),
            ("Bear — Oversupply Glut",  "#f87171", "-22%",  "-30%",  "-15%",  "-10–18%",18, "18–24mo","Delivery surge",   "Fleet scrapping lag"),
            ("Tail — Geopolitical",     "#a855f7", "+65%",  "+80%",  "+55%",  "35–55%",  5, "3–6 mo","Military conflict", "Rapid resolution"),
        ]

        headers = ["Scenario", "BDI Δ", "WCI Δ", "VLCC Δ", "Portfolio Return", "Probability", "Time Horizon", "Key Trigger", "Key Risk"]

        header_cells = "".join(
            f'<th style="padding:10px 14px;font-size:10px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;text-align:left;border-bottom:1px solid {C_BORDER};white-space:nowrap;">{h}</th>'
            for h in headers
        )

        rows_html = ""
        for i, row in enumerate(scenarios):
            name, color, bdi_d, wci_d, vlcc_d, port_r, prob, horizon, trigger, risk = row
            bg = C_CARD if i % 2 == 0 else C_SURFACE

            def _cell(val, is_pct=False):
                try:
                    v = float(val.replace("%", "").replace("+", ""))
                    c = C_HIGH if v > 0 else (C_LOW if v < 0 else C_TEXT2)
                except Exception:
                    c = C_TEXT2
                return f'<td style="padding:10px 14px;font-size:12px;font-weight:600;color:{c};white-space:nowrap;">{val}</td>'

            rows_html += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:10px 14px;white-space:nowrap;">'
                f'<span style="display:inline-block;width:8px;height:8px;background:{color};border-radius:50%;margin-right:8px;"></span>'
                f'<span style="font-size:12px;font-weight:600;color:{C_TEXT};">{name}</span>'
                f'</td>'
                + _cell(bdi_d, True)
                + _cell(wci_d, True)
                + _cell(vlcc_d, True)
                + _cell(port_r, True)
                + f'<td style="padding:10px 14px;font-size:12px;font-weight:700;color:{color};">{prob}%</td>'
                + f'<td style="padding:10px 14px;font-size:12px;color:{C_TEXT2};">{horizon}</td>'
                + f'<td style="padding:10px 14px;font-size:12px;color:{C_TEXT2};">{trigger}</td>'
                + f'<td style="padding:10px 14px;font-size:12px;color:{C_TEXT3};font-style:italic;">{risk}</td>'
                + f'</tr>'
            )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;margin-bottom:4px;">'
            f'<div style="padding:16px 20px 0;font-size:11px;font-weight:700;letter-spacing:2px;color:{C_TEXT3};text-transform:uppercase;">SCENARIO COMPARISON MATRIX</div>'
            f'<div style="overflow-x:auto;padding:12px 0 4px;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>{header_cells}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    except Exception as exc:
        logger.exception("comparison table error")
        st.error(f"Comparison table error: {exc}")


# ── Section 4: Scenario Builder ────────────────────────────────────────────────
def _render_scenario_builder():
    logger.debug("rendering scenario builder")
    try:
        if "custom_scenarios" not in st.session_state:
            st.session_state.custom_scenarios = []

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:20px 24px 4px;margin-bottom:8px;">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;color:{C_TEXT3};text-transform:uppercase;margin-bottom:4px;">INTERACTIVE SCENARIO BUILDER</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};margin-bottom:16px;">Adjust macro parameters to estimate freight rate and BDI impact.</div>'
            f'</div>',
            unsafe_allow_html=True
        )

        with st.form("scenario_builder_form"):
            c1, c2 = st.columns(2)
            with c1:
                gdp = st.slider("GDP Growth (%)", min_value=-2.0, max_value=4.0, value=2.4, step=0.1, help="Global real GDP growth rate")
                oil = st.slider("Oil Price ($/bbl)", min_value=40, max_value=140, value=80, step=5, help="Brent crude oil price assumption")
            with c2:
                fleet_growth = st.slider("Fleet Growth (%)", min_value=-2.0, max_value=6.0, value=3.2, step=0.1, help="Net fleet capacity growth YoY")
                demand_growth = st.slider("Trade Demand Growth (%)", min_value=-3.0, max_value=5.0, value=2.8, step=0.1, help="Global seaborne trade volume growth")

            scenario_name = st.text_input("Scenario Name", value="Custom Scenario", max_chars=40)
            submitted = st.form_submit_button("Calculate & Save Scenario", use_container_width=True)

        if submitted:
            try:
                supply_demand_gap = demand_growth - fleet_growth
                gdp_factor = (gdp - 2.0) * 8.0
                oil_factor = (oil - 80) * 0.15
                fr_impact = supply_demand_gap * 12.0 + gdp_factor + oil_factor
                bdi_impact = fr_impact * 0.85

                impact_color = C_HIGH if fr_impact > 0 else (C_LOW if fr_impact < 0 else C_TEXT2)
                bdi_color = C_HIGH if bdi_impact > 0 else (C_LOW if bdi_impact < 0 else C_TEXT2)

                new_sc = {
                    "name": scenario_name,
                    "gdp": gdp,
                    "oil": oil,
                    "fleet": fleet_growth,
                    "demand": demand_growth,
                    "fr_impact": fr_impact,
                    "bdi_impact": bdi_impact,
                }
                st.session_state.custom_scenarios.append(new_sc)

                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {impact_color}44;border-radius:12px;padding:20px 24px;margin-top:12px;">'
                    f'<div style="font-size:13px;font-weight:700;color:{C_TEXT};margin-bottom:14px;">Results: {scenario_name}</div>'
                    f'<div style="display:flex;gap:24px;flex-wrap:wrap;">'
                    f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;padding:14px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:4px;">FREIGHT RATE IMPACT</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{impact_color};">{fr_impact:+.1f}%</div>'
                    f'</div>'
                    f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;padding:14px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:4px;">BDI IMPACT</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{bdi_color};">{bdi_impact:+.1f}%</div>'
                    f'</div>'
                    f'<div style="flex:1;min-width:120px;background:{C_SURFACE};border-radius:8px;padding:14px;">'
                    f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:4px;">SUPPLY/DEMAND GAP</div>'
                    f'<div style="font-size:28px;font-weight:800;color:{C_TEXT};">{supply_demand_gap:+.1f}pp</div>'
                    f'</div>'
                    f'</div>'
                    f'<div style="font-size:11px;color:{C_TEXT3};margin-top:12px;">Scenario saved. {len(st.session_state.custom_scenarios)} custom scenario(s) stored this session.</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
            except Exception as calc_exc:
                logger.exception("scenario builder calculation error")
                st.error(f"Calculation error: {calc_exc}")

        saved = st.session_state.get("custom_scenarios", [])
        if len(saved) > 1:
            st.markdown(
                f'<div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;margin:16px 0 8px;">SAVED CUSTOM SCENARIOS</div>',
                unsafe_allow_html=True
            )
            rows = "".join(
                f'<tr style="background:{C_CARD if i%2==0 else C_SURFACE};">'
                f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT};">{s["name"]}</td>'
                f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT2};">{s["gdp"]:+.1f}%</td>'
                f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT2};">${s["oil"]}</td>'
                f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT2};">{s["fleet"]:+.1f}%</td>'
                f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT2};">{s["demand"]:+.1f}%</td>'
                f'<td style="padding:8px 12px;font-size:12px;font-weight:700;color:{C_HIGH if s["fr_impact"]>0 else C_LOW};">{s["fr_impact"]:+.1f}%</td>'
                f'<td style="padding:8px 12px;font-size:12px;font-weight:700;color:{C_HIGH if s["bdi_impact"]>0 else C_LOW};">{s["bdi_impact"]:+.1f}%</td>'
                f'</tr>'
                for i, s in enumerate(saved[-5:])
            )
            hdr = "".join(
                f'<th style="padding:8px 12px;font-size:10px;font-weight:700;letter-spacing:1.2px;color:{C_TEXT3};text-transform:uppercase;text-align:left;border-bottom:1px solid {C_BORDER};">{h}</th>'
                for h in ["Name", "GDP", "Oil", "Fleet Δ", "Demand Δ", "Rate Impact", "BDI Impact"]
            )
            st.markdown(
                f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow-x:auto;">'
                f'<table style="width:100%;border-collapse:collapse;">'
                f'<thead><tr>{hdr}</tr></thead><tbody>{rows}</tbody>'
                f'</table></div>',
                unsafe_allow_html=True
            )
    except Exception as exc:
        logger.exception("scenario builder error")
        st.error(f"Scenario builder error: {exc}")


# ── Section 5: Event Probability Tracker ──────────────────────────────────────
def _render_event_tracker():
    logger.debug("rendering event probability tracker")
    try:
        events = [
            {
                "event": "Red Sea Normalization",
                "timing": "Q3 2026",
                "prob": 35,
                "impact": "HIGH",
                "direction": "Bearish",
                "dir_color": C_LOW,
                "indicator": "Houthi ceasefire reports, transit volume data",
            },
            {
                "event": "US Recession",
                "timing": "Q4 2026",
                "prob": 20,
                "impact": "HIGH",
                "direction": "Bearish",
                "dir_color": C_LOW,
                "indicator": "GDP prints, unemployment claims, yield curve",
            },
            {
                "event": "China Demand Surge",
                "timing": "Q2–Q3 2026",
                "prob": 25,
                "impact": "HIGH",
                "direction": "Bullish",
                "dir_color": C_HIGH,
                "indicator": "PBoC stimulus, PMI >52, import growth >8%",
            },
            {
                "event": "Major Newbuild Oversupply",
                "timing": "2026–2027",
                "prob": 30,
                "impact": "MOD",
                "direction": "Bearish",
                "dir_color": C_LOW,
                "indicator": "Orderbook delivery schedule, scrapping rates",
            },
            {
                "event": "Panama Drought Persists",
                "timing": "Q2–Q4 2026",
                "prob": 40,
                "impact": "MOD",
                "direction": "Bullish",
                "dir_color": C_HIGH,
                "indicator": "Gatun Lake water levels, canal authority bulletins",
            },
            {
                "event": "New Chokepoint Disruption",
                "timing": "2026",
                "prob": 15,
                "impact": "HIGH",
                "direction": "Bullish",
                "dir_color": C_HIGH,
                "indicator": "Geopolitical risk indices, naval incident reports",
            },
        ]

        impact_colors = {"HIGH": C_LOW, "MOD": C_MOD, "LOW": C_HIGH}

        header_cells = "".join(
            f'<th style="padding:10px 14px;font-size:10px;font-weight:700;letter-spacing:1.5px;color:{C_TEXT3};text-transform:uppercase;text-align:left;border-bottom:1px solid {C_BORDER};white-space:nowrap;">{h}</th>'
            for h in ["Event", "Timing", "Probability", "Impact", "Direction", "Key Indicator to Watch"]
        )

        rows_html = ""
        for i, ev in enumerate(events):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            ic = impact_colors.get(ev["impact"], C_TEXT2)
            bar = _prob_bar_html(ev["prob"], ev["dir_color"])
            rows_html += (
                f'<tr style="background:{bg};">'
                f'<td style="padding:10px 14px;font-size:12px;font-weight:600;color:{C_TEXT};white-space:nowrap;">{ev["event"]}</td>'
                f'<td style="padding:10px 14px;font-size:12px;color:{C_TEXT2};white-space:nowrap;">{ev["timing"]}</td>'
                f'<td style="padding:10px 14px;min-width:100px;">'
                f'<div style="font-size:13px;font-weight:700;color:{ev["dir_color"]};">{ev["prob"]}%</div>'
                f'{bar}'
                f'</td>'
                f'<td style="padding:10px 14px;">'
                f'<span style="background:{ic}22;color:{ic};font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;letter-spacing:1px;">{ev["impact"]}</span>'
                f'</td>'
                f'<td style="padding:10px 14px;">'
                f'<span style="background:{ev["dir_color"]}22;color:{ev["dir_color"]};font-size:11px;font-weight:600;padding:3px 10px;border-radius:10px;">{ev["direction"]}</span>'
                f'</td>'
                f'<td style="padding:10px 14px;font-size:11px;color:{C_TEXT3};max-width:220px;">{ev["indicator"]}</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
            f'<div style="padding:16px 20px 0;">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;color:{C_TEXT3};text-transform:uppercase;margin-bottom:4px;">EVENT PROBABILITY TRACKER</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};margin-bottom:12px;">Estimated probability of key market-moving events with impact assessment and monitoring indicators.</div>'
            f'</div>'
            f'<div style="overflow-x:auto;padding-bottom:8px;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>{header_cells}</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    except Exception as exc:
        logger.exception("event tracker error")
        st.error(f"Event tracker error: {exc}")


# ── Section 6: Monte Carlo Fan Chart ──────────────────────────────────────────
def _render_monte_carlo(macro_data):
    logger.debug("rendering Monte Carlo fan chart")
    try:
        bdi_start = float(_val(macro_data, "bdi", "value", default=1_850))
        n_paths = 500
        horizon  = 90
        dt       = 1 / 252
        mu       = 0.0003
        sigma    = 0.028

        rng = np.random.default_rng(seed=42)
        shocks = rng.normal(mu, sigma, size=(n_paths, horizon))
        log_returns = np.cumsum(shocks, axis=1)
        paths = bdi_start * np.exp(log_returns)

        days = np.arange(1, horizon + 1)
        pcts = [5, 25, 50, 75, 95]
        bands = {p: np.percentile(paths, p, axis=0) for p in pcts}

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[95]]),
            mode="lines", line=dict(width=0),
            showlegend=False, name="95th",
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[5]]),
            mode="lines", line=dict(width=0),
            fill="tonexty",
            fillcolor="rgba(59,130,246,0.08)",
            name="90% CI",
            hovertemplate="Day %{x}<br>5–95th: %{y:.0f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[75]]),
            mode="lines", line=dict(width=0),
            showlegend=False, name="75th",
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[25]]),
            mode="lines", line=dict(width=0),
            fill="tonexty",
            fillcolor="rgba(59,130,246,0.15)",
            name="50% CI",
            hovertemplate="Day %{x}<br>25–75th: %{y:.0f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=np.concatenate([[0], days]),
            y=np.concatenate([[bdi_start], bands[50]]),
            mode="lines",
            line=dict(color=C_ACCENT, width=2.5),
            name="Median",
            hovertemplate="Day %{x}<br>Median BDI: %{y:.0f}<extra></extra>",
        ))

        sample_paths = paths[rng.integers(0, n_paths, size=30)]
        for path in sample_paths:
            fig.add_trace(go.Scatter(
                x=days, y=path,
                mode="lines",
                line=dict(color="rgba(59,130,246,0.06)", width=1),
                showlegend=False,
                hoverinfo="skip",
            ))

        fig.add_hline(
            y=bdi_start,
            line_dash="dash",
            line_color=C_TEXT3,
            annotation_text=f"Current BDI: {int(bdi_start):,}",
            annotation_font_color=C_TEXT3,
            annotation_font_size=11,
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_SURFACE,
            font=dict(family="Inter, sans-serif", color=C_TEXT2),
            title=dict(
                text=f"BDI Monte Carlo Simulation — 500 Paths, 90-Day Horizon",
                font=dict(size=14, color=C_TEXT),
                x=0.02,
            ),
            xaxis=dict(
                title="Trading Days",
                gridcolor=C_BORDER,
                showgrid=True,
                zeroline=False,
                tickfont=dict(size=11),
            ),
            yaxis=dict(
                title="Baltic Dry Index",
                gridcolor=C_BORDER,
                showgrid=True,
                zeroline=False,
                tickfont=dict(size=11),
                tickformat=",d",
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="left",
                x=0,
                font=dict(size=11),
                bgcolor="rgba(0,0,0,0)",
            ),
            hovermode="x unified",
            height=420,
            margin=dict(l=16, r=16, t=60, b=16),
        )

        st.plotly_chart(fig, use_container_width=True)

        final_median = bands[50][-1]
        final_5th    = bands[5][-1]
        final_95th   = bands[95][-1]
        med_chg = (final_median / bdi_start - 1) * 100
        lo_chg  = (final_5th / bdi_start - 1) * 100
        hi_chg  = (final_95th / bdi_start - 1) * 100

        st.markdown(
            f'<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:4px;">'
            f'<div style="flex:1;min-width:130px;background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px;">'
            f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:4px;">90-DAY MEDIAN</div>'
            f'<div style="font-size:22px;font-weight:700;color:{C_TEXT};">{int(final_median):,}</div>'
            f'<div style="font-size:12px;color:{C_HIGH if med_chg>=0 else C_LOW};">{med_chg:+.1f}% vs today</div>'
            f'</div>'
            f'<div style="flex:1;min-width:130px;background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px;">'
            f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:4px;">5TH PERCENTILE (BEAR)</div>'
            f'<div style="font-size:22px;font-weight:700;color:{C_LOW};">{int(final_5th):,}</div>'
            f'<div style="font-size:12px;color:{C_LOW};">{lo_chg:+.1f}% vs today</div>'
            f'</div>'
            f'<div style="flex:1;min-width:130px;background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px;">'
            f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:4px;">95TH PERCENTILE (BULL)</div>'
            f'<div style="font-size:22px;font-weight:700;color:{C_HIGH};">{int(final_95th):,}</div>'
            f'<div style="font-size:12px;color:{C_HIGH};">{hi_chg:+.1f}% vs today</div>'
            f'</div>'
            f'<div style="flex:1;min-width:130px;background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:14px;">'
            f'<div style="font-size:10px;color:{C_TEXT3};margin-bottom:4px;">SIMULATED PATHS</div>'
            f'<div style="font-size:22px;font-weight:700;color:{C_TEXT};">500</div>'
            f'<div style="font-size:12px;color:{C_TEXT2};">GBM, σ={sigma:.1%}/day</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    except Exception as exc:
        logger.exception("Monte Carlo fan chart error")
        st.error(f"Monte Carlo error: {exc}")


# ── Main Entry Point ───────────────────────────────────────────────────────────
def render(macro_data=None, freight_data=None, insights=None):
    """Render the Scenario Analysis & Stress Testing tab."""
    logger.info("tab_scenarios.render() called")
    try:
        st.markdown(
            f'<style>div[data-testid="stVerticalBlock"]>div{{gap:0rem;}}</style>',
            unsafe_allow_html=True
        )

        # ── Section 1: Dashboard Hero
        _render_dashboard(macro_data, freight_data, insights)

        # ── Section 2: Three-Scenario Comparison
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;color:{C_TEXT3};text-transform:uppercase;margin:20px 0 10px;">BASE / BULL / BEAR SCENARIO COMPARISON</div>',
            unsafe_allow_html=True
        )
        _render_three_scenarios(macro_data, freight_data)

        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)

        # ── Section 3: Comparison Table
        _render_comparison_table()

        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)

        # ── Section 4: Scenario Builder
        _render_scenario_builder()

        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)

        # ── Section 5: Event Probability Tracker
        _render_event_tracker()

        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)

        # ── Section 6: Monte Carlo Fan Chart
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:2px;color:{C_TEXT3};text-transform:uppercase;margin-bottom:10px;">MONTE CARLO BDI SIMULATION</div>',
            unsafe_allow_html=True
        )
        _render_monte_carlo(macro_data)

    except Exception as exc:
        logger.exception("tab_scenarios.render() top-level error")
        st.error(f"Scenario tab error: {exc}")
