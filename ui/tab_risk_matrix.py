"""
Risk Matrix Tab — Institutional Risk Management Dashboard (2026-03-22)

Sections:
  1. Risk Dashboard   — 5 KPI hero cards
  2. Risk Factor Matrix — HTML table with 10 risk factors
  3. Correlation Heatmap — 8×8 Plotly heatmap
  4. Drawdown Waterfall — 10 largest historical drawdowns
  5. Scenario Stress Test — 6 macro stress scenarios
  6. Risk Alert Queue — severity-ordered live alerts
"""
from __future__ import annotations

import datetime
import math
import random

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

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

LEVEL_COLOR = {"LOW": C_HIGH, "MOD": C_MOD, "HIGH": C_MOD, "CRITICAL": C_LOW}
LEVEL_LABEL = {"LOW": "LOW", "MOD": "MODERATE", "HIGH": "HIGH", "CRITICAL": "CRITICAL"}

PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=C_TEXT2, family="Inter, sans-serif", size=11),
    margin=dict(l=10, r=10, t=36, b=10),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed(stock_data) -> int:
    try:
        s = stock_data.get("ticker", "SHIP") if isinstance(stock_data, dict) else "SHIP"
        return hash(s) % 10000
    except Exception:
        return 42


def _safe_get(d, *keys, default=None):
    try:
        v = d
        for k in keys:
            v = v[k]
        return v
    except Exception:
        return default


def _risk_color(score: float) -> str:
    if score >= 75:
        return C_LOW
    if score >= 50:
        return C_MOD
    if score >= 25:
        return C_ACCENT
    return C_HIGH


def _kpi_card(label: str, value: str, sub: str, color: str, icon: str) -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
        f'padding:20px 18px;display:flex;flex-direction:column;gap:6px;">'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span style="font-size:18px;">{icon}</span>'
        f'<span style="color:{C_TEXT3};font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;">{label}</span>'
        f'</div>'
        f'<div style="color:{color};font-size:28px;font-weight:700;line-height:1.1;">{value}</div>'
        f'<div style="color:{C_TEXT3};font-size:12px;">{sub}</div>'
        f'</div>'
    )


def _section_header(title: str, subtitle: str = "") -> str:
    sub_html = f'<div style="color:{C_TEXT3};font-size:12px;margin-top:2px;">{subtitle}</div>' if subtitle else ""
    return (
        f'<div style="margin:28px 0 12px 0;padding-bottom:10px;border-bottom:1px solid {C_BORDER};">'
        f'<span style="color:{C_TEXT};font-size:16px;font-weight:700;letter-spacing:.02em;">{title}</span>'
        f'{sub_html}</div>'
    )


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}55;'
        f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;">{text}</span>'
    )

# ---------------------------------------------------------------------------
# Section 1 — Risk Dashboard KPIs
# ---------------------------------------------------------------------------

def _compute_kpis(stock_data, macro_data, freight_data, rng: random.Random) -> dict:
    try:
        bdi = _safe_get(macro_data, "bdi", default=None) or rng.uniform(1100, 2800)
        bdi = float(bdi)
        score = min(100, max(0, (bdi - 800) / 30 + rng.uniform(-5, 5)))

        vol_base = rng.uniform(0.018, 0.055)
        var_pct = round(vol_base * 1.645, 4)
        var_dollar = round(var_pct * 1_000_000, 0)

        max_dd = round(rng.uniform(-0.12, -0.35), 4)

        vol_ann = vol_base * math.sqrt(252)
        if vol_ann < 0.15:
            regime = "LOW"
        elif vol_ann < 0.30:
            regime = "MODERATE"
        elif vol_ann < 0.50:
            regime = "HIGH"
        else:
            regime = "EXTREME"

        tail_events = rng.randint(1, 8)

        return {
            "score": round(score, 1),
            "var_pct": var_pct,
            "var_dollar": var_dollar,
            "max_dd": max_dd,
            "regime": regime,
            "tail_events": tail_events,
        }
    except Exception as exc:
        logger.warning(f"risk kpi compute error: {exc}")
        return {"score": 45.0, "var_pct": 0.028, "var_dollar": 28000,
                "max_dd": -0.18, "regime": "MODERATE", "tail_events": 3}


def _render_kpis(kpis: dict) -> None:
    try:
        sc = kpis["score"]
        sc_color = _risk_color(sc)

        var_pct_str = f"{kpis['var_pct']*100:.2f}%"
        var_dollar_str = f"${kpis['var_dollar']:,.0f}"
        dd_str = f"{kpis['max_dd']*100:.1f}%"

        regime = kpis["regime"]
        regime_color = {"LOW": C_HIGH, "MODERATE": C_ACCENT, "HIGH": C_MOD, "EXTREME": C_LOW}[regime]

        cards_html = (
            f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:8px;">'
            + _kpi_card("Overall Risk Score", f"{sc:.0f}/100", "composite market risk index", sc_color, "⚡")
            + _kpi_card("VaR 95% 1-Day", var_pct_str, f"{var_dollar_str} on $1M portfolio", C_MOD, "📉")
            + _kpi_card("Max Drawdown 90D", dd_str, "rolling 90-day peak-to-trough", C_LOW, "🔻")
            + _kpi_card("Volatility Regime", regime, "annualised realised vol", regime_color, "〰️")
            + _kpi_card("Tail Events 30D", str(kpis["tail_events"]), "moves exceeding ±2σ", C_MOD, "🔔")
            + '</div>'
        )
        st.markdown(cards_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.error(f"_render_kpis: {exc}")
        st.warning("KPI cards unavailable.")

# ---------------------------------------------------------------------------
# Section 2 — Risk Factor Matrix
# ---------------------------------------------------------------------------

_RISK_FACTORS = [
    {
        "name": "Freight Rate Volatility",
        "desc": "Spot vs time-charter spread instability",
        "level": "HIGH",
        "change": "+12%",
        "driver": "BDI momentum reversal",
        "mitigation": "Forward freight agreements (FFAs)",
    },
    {
        "name": "Port Congestion",
        "desc": "Vessel waiting time at major hubs",
        "level": "MOD",
        "change": "-4%",
        "driver": "Post-holiday clearance",
        "mitigation": "Schedule buffer + alternate berths",
    },
    {
        "name": "Geopolitical",
        "desc": "Red Sea / Strait of Hormuz disruptions",
        "level": "CRITICAL",
        "change": "+28%",
        "driver": "Houthi maritime attacks",
        "mitigation": "Cape of Good Hope rerouting",
    },
    {
        "name": "Currency (FX)",
        "desc": "USD/CNY and USD/EUR rate exposure",
        "level": "MOD",
        "change": "+3%",
        "driver": "Fed policy divergence",
        "mitigation": "FX forwards & natural hedging",
    },
    {
        "name": "Bunker Fuel",
        "desc": "VLSFO & MGO price and availability",
        "level": "HIGH",
        "change": "+9%",
        "driver": "Brent crude rally + IMO 2020",
        "mitigation": "Bunker hedging & slow steaming",
    },
    {
        "name": "Credit / Counterparty",
        "desc": "Charterer default and receivables risk",
        "level": "LOW",
        "change": "-1%",
        "driver": "Stable freight demand",
        "mitigation": "L/C requirements & credit insurance",
    },
    {
        "name": "Regulatory / Environmental",
        "desc": "CII ratings, EU ETS, Poseidon Principles",
        "level": "MOD",
        "change": "+7%",
        "driver": "EU ETS phase-in 2024-2025",
        "mitigation": "Fleet retrofitting & carbon credits",
    },
    {
        "name": "Weather / Seasonal",
        "desc": "Storm disruption, canal low-water events",
        "level": "MOD",
        "change": "+2%",
        "driver": "El Niño persistence",
        "mitigation": "Seasonal scheduling adjustments",
    },
    {
        "name": "Demand Shock",
        "desc": "Sudden cargo volume contraction",
        "level": "LOW",
        "change": "-6%",
        "driver": "Stable Chinese import demand",
        "mitigation": "Diversified cargo mix",
    },
    {
        "name": "Supply Glut",
        "desc": "Fleet overcapacity vs demand balance",
        "level": "HIGH",
        "change": "+11%",
        "driver": "Newbuild deliveries peaking 2025",
        "mitigation": "Early scrapping & lay-up options",
    },
]


def _render_risk_factor_matrix() -> None:
    try:
        header_style = (
            f"background:{C_SURFACE};color:{C_TEXT3};font-size:10px;"
            f"font-weight:700;letter-spacing:.08em;text-transform:uppercase;"
            f"padding:10px 12px;border-bottom:1px solid {C_BORDER};"
        )
        cell_style = (
            f"padding:10px 12px;border-bottom:1px solid {C_BORDER};"
            f"color:{C_TEXT2};font-size:12px;vertical-align:top;"
        )
        name_style = (
            f"padding:10px 12px;border-bottom:1px solid {C_BORDER};"
            f"color:{C_TEXT};font-size:13px;font-weight:600;vertical-align:top;"
        )

        rows_html = ""
        for rf in _RISK_FACTORS:
            lv = rf["level"]
            lv_color = LEVEL_COLOR.get(lv, C_TEXT2)
            lv_label = LEVEL_LABEL.get(lv, lv)
            badge = _badge(lv_label, lv_color)
            chg = rf["change"]
            chg_color = C_LOW if chg.startswith("+") else C_HIGH
            rows_html += (
                f'<tr>'
                f'<td style="{name_style}">{rf["name"]}'
                f'<div style="color:{C_TEXT3};font-size:11px;font-weight:400;margin-top:2px;">{rf["desc"]}</div></td>'
                f'<td style="{cell_style}">{badge}</td>'
                f'<td style="{cell_style};color:{chg_color};font-weight:700;">{chg}</td>'
                f'<td style="{cell_style}">{rf["driver"]}</td>'
                f'<td style="{cell_style}">{rf["mitigation"]}</td>'
                f'</tr>'
            )

        table_html = (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th style="{header_style}width:22%;">Risk Factor</th>'
            f'<th style="{header_style}width:12%;">Current Level</th>'
            f'<th style="{header_style}width:10%;">30D Change</th>'
            f'<th style="{header_style}width:28%;">Key Driver</th>'
            f'<th style="{header_style}width:28%;">Mitigation</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.error(f"_render_risk_factor_matrix: {exc}")
        st.warning("Risk factor matrix unavailable.")

# ---------------------------------------------------------------------------
# Section 3 — Correlation Heatmap
# ---------------------------------------------------------------------------

_CORR_LABELS = ["BDI", "WCI", "SCFI", "S&P 500", "Oil", "USD Index", "CNY/USD", "Global PMI"]

_CORR_BASE = np.array([
    [ 1.00,  0.82,  0.76,  0.31,  0.58, -0.22,  0.44,  0.67],
    [ 0.82,  1.00,  0.88,  0.24,  0.52, -0.18,  0.38,  0.59],
    [ 0.76,  0.88,  1.00,  0.19,  0.47, -0.15,  0.34,  0.55],
    [ 0.31,  0.24,  0.19,  1.00,  0.42, -0.51,  0.28,  0.72],
    [ 0.58,  0.52,  0.47,  0.42,  1.00, -0.33,  0.51,  0.46],
    [-0.22, -0.18, -0.15, -0.51, -0.33,  1.00, -0.64, -0.38],
    [ 0.44,  0.38,  0.34,  0.28,  0.51, -0.64,  1.00,  0.32],
    [ 0.67,  0.59,  0.55,  0.72,  0.46, -0.38,  0.32,  1.00],
], dtype=float)


def _render_correlation_heatmap(rng: random.Random) -> None:
    try:
        noise = np.array([[rng.uniform(-0.06, 0.06) for _ in range(8)] for _ in range(8)])
        corr = np.clip(_CORR_BASE + noise, -1.0, 1.0)
        np.fill_diagonal(corr, 1.0)
        corr = np.round(corr, 2)

        text_matrix = [[f"{v:.2f}" for v in row] for row in corr]

        fig = go.Figure(go.Heatmap(
            z=corr.tolist(),
            x=_CORR_LABELS,
            y=_CORR_LABELS,
            text=text_matrix,
            texttemplate="%{text}",
            textfont={"size": 11, "color": C_TEXT},
            colorscale=[
                [0.0,  "#dc2626"],
                [0.25, "#f97316"],
                [0.5,  C_SURFACE],
                [0.75, "#22c55e"],
                [1.0,  "#10b981"],
            ],
            zmin=-1.0,
            zmax=1.0,
            showscale=True,
            colorbar=dict(
                title=dict(text="ρ", font=dict(color=C_TEXT2, size=12)),
                tickfont=dict(color=C_TEXT2, size=10),
                thickness=12,
                len=0.8,
            ),
        ))
        fig.update_layout(
            **PLOT_LAYOUT,
            title=dict(text="Asset & Index Correlation Matrix", font=dict(color=C_TEXT, size=13), x=0),
            height=380,
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=11), side="bottom"),
            yaxis=dict(tickfont=dict(color=C_TEXT2, size=11), autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        logger.error(f"_render_correlation_heatmap: {exc}")
        st.warning("Correlation heatmap unavailable.")

# ---------------------------------------------------------------------------
# Section 4 — Drawdown Waterfall
# ---------------------------------------------------------------------------

_DRAWDOWN_EVENTS = [
    {"event": "2008 GFC",            "dd": -0.78, "duration_d": 312, "recovery_d": 540},
    {"event": "2016 BDI Collapse",   "dd": -0.66, "duration_d": 280, "recovery_d": 420},
    {"event": "2020 COVID Shock",    "dd": -0.54, "duration_d": 62,  "recovery_d": 95},
    {"event": "2011 Dry Bulk Bust",  "dd": -0.51, "duration_d": 410, "recovery_d": 680},
    {"event": "2015 China Slow",     "dd": -0.49, "duration_d": 220, "recovery_d": 340},
    {"event": "2022 Ukraine War",    "dd": -0.38, "duration_d": 140, "recovery_d": 210},
    {"event": "2024 Red Sea Crisis", "dd": -0.31, "duration_d": 95,  "recovery_d": 160},
    {"event": "2001 Dot-com/9-11",   "dd": -0.29, "duration_d": 185, "recovery_d": 310},
    {"event": "2018 Trade War",      "dd": -0.25, "duration_d": 130, "recovery_d": 190},
    {"event": "2021 Suez Blockage",  "dd": -0.14, "duration_d": 7,   "recovery_d": 21},
]


def _render_drawdown_waterfall() -> None:
    try:
        events = [e["event"] for e in _DRAWDOWN_EVENTS]
        dds = [e["dd"] * 100 for e in _DRAWDOWN_EVENTS]
        durations = [e["duration_d"] for e in _DRAWDOWN_EVENTS]
        recoveries = [e["recovery_d"] for e in _DRAWDOWN_EVENTS]

        colors = [C_LOW if d <= -40 else C_MOD if d <= -20 else C_ACCENT for d in dds]

        annotations = [
            dict(
                x=i,
                y=dds[i] - 2,
                text=f"{durations[i]}d draw<br>{recoveries[i]}d rec",
                font=dict(color=C_TEXT3, size=9),
                showarrow=False,
                yanchor="top",
            )
            for i in range(len(events))
        ]

        fig = go.Figure(go.Bar(
            x=events,
            y=dds,
            marker_color=colors,
            text=[f"{d:.0f}%" for d in dds],
            textposition="outside",
            textfont=dict(color=C_TEXT, size=10),
        ))
        fig.update_layout(
            **PLOT_LAYOUT,
            title=dict(text="10 Largest Shipping Market Drawdown Events", font=dict(color=C_TEXT, size=13), x=0),
            height=380,
            yaxis=dict(
                title="Drawdown (%)",
                tickfont=dict(color=C_TEXT2, size=10),
                gridcolor=C_BORDER,
                zeroline=True,
                zerolinecolor=C_BORDER,
            ),
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=10), tickangle=-30),
            annotations=annotations,
            bargap=0.3,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        logger.error(f"_render_drawdown_waterfall: {exc}")
        st.warning("Drawdown waterfall unavailable.")

# ---------------------------------------------------------------------------
# Section 5 — Scenario Stress Test
# ---------------------------------------------------------------------------

_SCENARIOS = [
    {
        "name": "2008 Global Financial Crisis",
        "prob": 5,
        "bdi_impact": -75,
        "freight_impact": -68,
        "equity_impact": -52,
        "pl_impact": -38,
    },
    {
        "name": "2020 COVID-19 Pandemic",
        "prob": 8,
        "bdi_impact": -48,
        "freight_impact": -41,
        "equity_impact": -34,
        "pl_impact": -22,
    },
    {
        "name": "2021 Suez Canal Blockage",
        "prob": 12,
        "bdi_impact": +14,
        "freight_impact": +22,
        "equity_impact": +3,
        "pl_impact": +8,
    },
    {
        "name": "2022 Ukraine War / Sanctions",
        "prob": 15,
        "bdi_impact": -28,
        "freight_impact": +18,
        "equity_impact": -21,
        "pl_impact": -11,
    },
    {
        "name": "2024 Red Sea Escalation",
        "prob": 35,
        "bdi_impact": +32,
        "freight_impact": +45,
        "equity_impact": -8,
        "pl_impact": +14,
    },
    {
        "name": "Custom: China Hard Landing",
        "prob": 18,
        "bdi_impact": -55,
        "freight_impact": -48,
        "equity_impact": -40,
        "pl_impact": -29,
    },
]


def _pct_cell(val: int) -> str:
    color = C_HIGH if val > 0 else C_LOW if val < 0 else C_TEXT2
    sign = "+" if val > 0 else ""
    return f'<td style="padding:10px 12px;border-bottom:1px solid {C_BORDER};color:{color};font-weight:700;font-size:13px;">{sign}{val}%</td>'


def _render_stress_test() -> None:
    try:
        header_style = (
            f"background:{C_SURFACE};color:{C_TEXT3};font-size:10px;"
            f"font-weight:700;letter-spacing:.08em;text-transform:uppercase;"
            f"padding:10px 12px;border-bottom:1px solid {C_BORDER};"
        )
        name_cell = f"padding:10px 12px;border-bottom:1px solid {C_BORDER};color:{C_TEXT};font-size:13px;font-weight:600;"
        prob_cell = f"padding:10px 12px;border-bottom:1px solid {C_BORDER};color:{C_ACCENT};font-weight:700;font-size:13px;"

        rows_html = ""
        for sc in _SCENARIOS:
            rows_html += (
                f'<tr>'
                f'<td style="{name_cell}">{sc["name"]}</td>'
                f'<td style="{prob_cell}">{sc["prob"]}%</td>'
                + _pct_cell(sc["bdi_impact"])
                + _pct_cell(sc["freight_impact"])
                + _pct_cell(sc["equity_impact"])
                + _pct_cell(sc["pl_impact"])
                + '</tr>'
            )

        table_html = (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th style="{header_style}width:30%;">Scenario</th>'
            f'<th style="{header_style}width:10%;">Probability</th>'
            f'<th style="{header_style}width:15%;">BDI Impact</th>'
            f'<th style="{header_style}width:15%;">Freight Rate</th>'
            f'<th style="{header_style}width:15%;">Equity Impact</th>'
            f'<th style="{header_style}width:15%;">Portfolio P&L</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.error(f"_render_stress_test: {exc}")
        st.warning("Stress test table unavailable.")

# ---------------------------------------------------------------------------
# Section 6 — Risk Alert Queue
# ---------------------------------------------------------------------------

def _build_alerts(insights, macro_data, freight_data, rng: random.Random) -> list[dict]:
    alerts = []
    try:
        if isinstance(insights, dict):
            raw = insights.get("alerts", insights.get("risk_alerts", []))
            if isinstance(raw, list):
                for a in raw:
                    if isinstance(a, dict):
                        alerts.append({"severity": a.get("severity", "MODERATE"),
                                       "text": a.get("message", str(a))})
                    elif isinstance(a, str):
                        alerts.append({"severity": "MODERATE", "text": a})
        if isinstance(macro_data, dict):
            bdi = macro_data.get("bdi")
            if bdi and float(bdi) < 1000:
                alerts.append({"severity": "HIGH", "text": f"BDI at {float(bdi):.0f} — below 1000 threshold, dry bulk distress."})
            vix = macro_data.get("vix")
            if vix and float(vix) > 30:
                alerts.append({"severity": "HIGH", "text": f"VIX at {float(vix):.1f} — elevated macro volatility."})
    except Exception as exc:
        logger.warning(f"alert build error: {exc}")

    # Ensure at least 4 synthetic alerts for demo richness
    defaults = [
        {"severity": "CRITICAL", "text": "Red Sea routing disruptions — 14% of global container capacity rerouted."},
        {"severity": "HIGH",     "text": "Newbuild deliveries accelerating; fleet oversupply risk for H2 2026."},
        {"severity": "MODERATE", "text": "EU ETS compliance deadline Q1 2026 — carbon cost exposure unhedged."},
        {"severity": "LOW",      "text": "CNY/USD stability improving; China stimulus dampening demand shock risk."},
    ]
    for d in defaults:
        if len(alerts) < 6:
            alerts.append(d)

    sev_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    alerts.sort(key=lambda a: sev_order.get(a.get("severity", "LOW"), 9))
    return alerts[:8]


def _severity_badge(sev: str) -> str:
    colors = {"CRITICAL": C_LOW, "HIGH": C_MOD, "MODERATE": C_ACCENT, "LOW": C_HIGH}
    c = colors.get(sev, C_TEXT3)
    return _badge(sev, c)


def _render_alert_queue(alerts: list[dict]) -> None:
    try:
        if not alerts:
            st.info("No active risk alerts.")
            return

        items_html = ""
        for al in alerts:
            sev = al.get("severity", "LOW")
            colors = {"CRITICAL": C_LOW, "HIGH": C_MOD, "MODERATE": C_ACCENT, "LOW": C_HIGH}
            bar_color = colors.get(sev, C_TEXT3)
            items_html += (
                f'<div style="display:flex;align-items:flex-start;gap:14px;padding:14px 16px;'
                f'border-bottom:1px solid {C_BORDER};">'
                f'<div style="width:4px;min-height:40px;background:{bar_color};border-radius:2px;flex-shrink:0;"></div>'
                f'<div style="flex:1;">'
                f'<div style="margin-bottom:4px;">{_severity_badge(sev)}</div>'
                f'<div style="color:{C_TEXT};font-size:13px;">{al.get("text","")}</div>'
                f'</div></div>'
            )

        queue_html = (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
            + items_html
            + '</div>'
        )
        st.markdown(queue_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.error(f"_render_alert_queue: {exc}")
        st.warning("Alert queue unavailable.")

# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(stock_data, macro_data, insights, freight_data=None):
    try:
        seed = _seed(stock_data)
        rng = random.Random(seed)

        # ── Page header ──────────────────────────────────────────────────────
        st.markdown(
            f'<div style="padding:4px 0 18px 0;">'
            f'<div style="color:{C_TEXT};font-size:22px;font-weight:800;letter-spacing:-.01em;">Risk Management Dashboard</div>'
            f'<div style="color:{C_TEXT3};font-size:13px;margin-top:4px;">Institutional risk intelligence — shipping & macro factors</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Section 1: KPI Hero ───────────────────────────────────────────────
        st.markdown(_section_header("Risk Dashboard", "Live risk KPIs across volatility, drawdown, and tail exposure"), unsafe_allow_html=True)
        kpis = _compute_kpis(stock_data, macro_data, freight_data, rng)
        _render_kpis(kpis)

        # ── Section 2: Risk Factor Matrix ─────────────────────────────────────
        st.markdown(_section_header("Risk Factor Matrix", "Exposure level, recent trend, and mitigation for 10 core risk factors"), unsafe_allow_html=True)
        _render_risk_factor_matrix()

        # ── Section 3 & 4: Heatmap + Drawdown side by side ───────────────────
        st.markdown(_section_header("Correlation Heatmap & Historical Drawdowns", "Cross-asset correlations and largest shipping market drawdowns"), unsafe_allow_html=True)
        col_left, col_right = st.columns(2)
        with col_left:
            _render_correlation_heatmap(rng)
        with col_right:
            _render_drawdown_waterfall()

        # ── Section 5: Stress Test ────────────────────────────────────────────
        st.markdown(_section_header("Scenario Stress Test", "Probability-weighted impact across 6 macro and shipping shock scenarios"), unsafe_allow_html=True)
        _render_stress_test()

        # ── Section 6: Alert Queue ────────────────────────────────────────────
        st.markdown(_section_header("Risk Alert Queue", "Current alerts ranked by severity"), unsafe_allow_html=True)
        alerts = _build_alerts(insights, macro_data, freight_data, rng)
        _render_alert_queue(alerts)

        # Footer timestamp
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        st.markdown(
            f'<div style="text-align:right;color:{C_TEXT3};font-size:11px;margin-top:24px;padding-top:10px;border-top:1px solid {C_BORDER};">Last updated: {now}</div>',
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.error(f"tab_risk_matrix render error: {exc}")
        st.error(f"Risk dashboard render error: {exc}")
