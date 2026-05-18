"""Risk Matrix Tab — Institutional Risk Management Dashboard.

Sections:
  1. Risk Dashboard KPI hero (composite score, VaR, drawdown, regime, tail)
  2. Risk Factor Matrix — 10 factors with level, trend, driver, mitigation
  3. Correlation Heatmap — cross-asset correlation matrix
  4. Drawdown Waterfall — 10 largest historical shipping drawdowns
  5. Scenario Stress Test — probability-weighted shock impact table
  6. Risk Alert Queue — severity-ordered live alerts

Canonical design-system migration: palette is imported from ``ui.styles``,
every figure/table carries a ``live_data_badge`` pill, and synthetic figures
are tagged ``quality="demo"`` so viewers know not to trust the numbers.
"""
from __future__ import annotations

import datetime
import math
import random

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_RULE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    live_data_badge,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Domain-specific semantic mappings (kept local to the tab)
# ─────────────────────────────────────────────────────────────────────────────

# Risk level → palette color. "HIGH" gets amber not red because "CRITICAL"
# reserves the red slot — keeps the matrix readable at a glance.
_LEVEL_COLOR: dict[str, str] = {
    "LOW":      C_HIGH,
    "MOD":      C_MOD,
    "HIGH":     C_MOD,
    "CRITICAL": C_LOW,
}

_LEVEL_LABEL: dict[str, str] = {
    "LOW":      "LOW",
    "MOD":      "MODERATE",
    "HIGH":     "HIGH",
    "CRITICAL": "CRITICAL",
}

# Badge color tokens accepted by `badge(text, color)` helper — hex required.
_LEVEL_BADGE_COLOR: dict[str, str] = {
    "LOW":      C_HIGH,
    "MOD":      C_MOD,
    "HIGH":     C_MOD,
    "CRITICAL": C_LOW,
}

_VOL_REGIME_COLOR: dict[str, str] = {
    "LOW":      C_HIGH,
    "MODERATE": C_ACCENT,
    "HIGH":     C_MOD,
    "EXTREME":  C_LOW,
}

_SEVERITY_COLOR: dict[str, str] = {
    "CRITICAL": C_LOW,
    "HIGH":     C_MOD,
    "MODERATE": C_ACCENT,
    "LOW":      C_HIGH,
}

_SEVERITY_BADGE_COLOR: dict[str, str] = {
    "CRITICAL": C_LOW,
    "HIGH":     C_MOD,
    "MODERATE": C_ACCENT,
    "LOW":      C_HIGH,
}


# ─────────────────────────────────────────────────────────────────────────────
#  Data-provenance sources — demo pills make it clear the numbers are synthetic
# ─────────────────────────────────────────────────────────────────────────────

def _risk_demo_source(name: str) -> DataSource:
    return DataSource.demo(name)


# ─────────────────────────────────────────────────────────────────────────────
#  Cell formatters for wsj_market_table
# ─────────────────────────────────────────────────────────────────────────────

def _sans(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">'
        f'{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT, weight: int = 600) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans_with_sub(value: str, sub: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};font-weight:600;">{value}</span>'
        f'<span style="display:block;font-family:var(--sans);color:{C_TEXT3};font-size:0.74rem;'
        f'font-weight:400;margin-top:2px;">{sub}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def _risk_score_color(score: float) -> str:
    if score >= 75:
        return C_LOW
    if score >= 50:
        return C_MOD
    if score >= 25:
        return C_ACCENT
    return C_HIGH


def _render_badge_row(source: DataSource) -> None:
    """Render a single provenance pill right-aligned via source_footer."""
    st.markdown(source_footer([source]), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Section 1 — Risk Dashboard KPIs
# ─────────────────────────────────────────────────────────────────────────────

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
            "score":       round(score, 1),
            "var_pct":     var_pct,
            "var_dollar":  var_dollar,
            "max_dd":      max_dd,
            "regime":      regime,
            "tail_events": tail_events,
        }
    except Exception as exc:
        logger.warning(f"risk kpi compute error: {exc}")
        return {"score": 45.0, "var_pct": 0.028, "var_dollar": 28000,
                "max_dd": -0.18, "regime": "MODERATE", "tail_events": 3}


def _render_kpis(kpis: dict, source: DataSource) -> None:
    try:
        sc      = kpis["score"]
        regime  = kpis["regime"]
        metrics = [
            {
                "label":   "Overall Risk Score",
                "value":   f"{sc:.0f}/100",
                "accent":  _risk_score_color(sc),
                "sublabel": "Composite market risk index",
            },
            {
                "label":   "VaR 95% 1-Day",
                "value":   f"{kpis['var_pct']*100:.2f}%",
                "accent":  C_MOD,
                "sublabel": f"${kpis['var_dollar']:,.0f} on $1M portfolio",
            },
            {
                "label":   "Max Drawdown 90D",
                "value":   f"{kpis['max_dd']*100:.1f}%",
                "accent":  C_LOW,
                "sublabel": "Rolling 90-day peak-to-trough",
            },
            {
                "label":   "Volatility Regime",
                "value":   regime,
                "accent":  _VOL_REGIME_COLOR.get(regime, C_ACCENT),
                "sublabel": "Annualised realised vol",
            },
            {
                "label":   "Tail Events 30D",
                "value":   str(kpis["tail_events"]),
                "accent":  C_MOD,
                "sublabel": "Moves exceeding \u00b12\u03c3",
            },
        ]
        _render_badge_row(source)
        metric_card_row(metrics, columns=5)
    except Exception as exc:
        logger.error(f"_render_kpis: {exc}")
        st.warning("KPI cards unavailable.")


# ─────────────────────────────────────────────────────────────────────────────
#  Section 2 — Risk Factor Matrix
# ─────────────────────────────────────────────────────────────────────────────

_RISK_FACTORS: list[dict] = [
    {
        "name":       "Freight Rate Volatility",
        "desc":       "Spot vs time-charter spread instability",
        "level":      "HIGH",
        "change":     "+12%",
        "driver":     "BDI momentum reversal",
        "mitigation": "Forward freight agreements (FFAs)",
    },
    {
        "name":       "Port Congestion",
        "desc":       "Vessel waiting time at major hubs",
        "level":      "MOD",
        "change":     "-4%",
        "driver":     "Post-holiday clearance",
        "mitigation": "Schedule buffer + alternate berths",
    },
    {
        "name":       "Geopolitical",
        "desc":       "Red Sea / Strait of Hormuz disruptions",
        "level":      "CRITICAL",
        "change":     "+28%",
        "driver":     "Houthi maritime attacks",
        "mitigation": "Cape of Good Hope rerouting",
    },
    {
        "name":       "Currency (FX)",
        "desc":       "USD/CNY and USD/EUR rate exposure",
        "level":      "MOD",
        "change":     "+3%",
        "driver":     "Fed policy divergence",
        "mitigation": "FX forwards & natural hedging",
    },
    {
        "name":       "Bunker Fuel",
        "desc":       "VLSFO & MGO price and availability",
        "level":      "HIGH",
        "change":     "+9%",
        "driver":     "Brent crude rally + IMO 2020",
        "mitigation": "Bunker hedging & slow steaming",
    },
    {
        "name":       "Credit / Counterparty",
        "desc":       "Charterer default and receivables risk",
        "level":      "LOW",
        "change":     "-1%",
        "driver":     "Stable freight demand",
        "mitigation": "L/C requirements & credit insurance",
    },
    {
        "name":       "Regulatory / Environmental",
        "desc":       "CII ratings, EU ETS, Poseidon Principles",
        "level":      "MOD",
        "change":     "+7%",
        "driver":     "EU ETS phase-in 2024-2025",
        "mitigation": "Fleet retrofitting & carbon credits",
    },
    {
        "name":       "Weather / Seasonal",
        "desc":       "Storm disruption, canal low-water events",
        "level":      "MOD",
        "change":     "+2%",
        "driver":     "El Ni\u00f1o persistence",
        "mitigation": "Seasonal scheduling adjustments",
    },
    {
        "name":       "Demand Shock",
        "desc":       "Sudden cargo volume contraction",
        "level":      "LOW",
        "change":     "-6%",
        "driver":     "Stable Chinese import demand",
        "mitigation": "Diversified cargo mix",
    },
    {
        "name":       "Supply Glut",
        "desc":       "Fleet overcapacity vs demand balance",
        "level":      "HIGH",
        "change":     "+11%",
        "driver":     "Newbuild deliveries peaking 2025",
        "mitigation": "Early scrapping & lay-up options",
    },
]


def _render_risk_factor_matrix(source: DataSource) -> None:
    try:
        _render_badge_row(source)
        headers = [
            "Risk Factor", "Current Level", "30D Change", "Key Driver", "Mitigation",
        ]
        rows: list[list[str]] = []
        for rf in _RISK_FACTORS:
            lv      = rf["level"]
            lv_lbl  = _LEVEL_LABEL.get(lv, lv)
            lv_bdg  = _LEVEL_BADGE_COLOR.get(lv, C_ACCENT)
            chg     = rf["change"]
            chg_clr = C_LOW if chg.startswith("+") else C_HIGH

            rows.append([
                _sans_with_sub(rf["name"], rf["desc"], color=C_TEXT),
                badge(lv_lbl, color=lv_bdg),
                _mono(chg, color=chg_clr, weight=700),
                _sans(rf["driver"], color=C_TEXT2, weight=500),
                _sans(rf["mitigation"], color=C_TEXT2, weight=500),
            ])
        wsj_market_table(headers, rows)
    except Exception as exc:
        logger.error(f"_render_risk_factor_matrix: {exc}")
        st.warning("Risk factor matrix unavailable.")


# ─────────────────────────────────────────────────────────────────────────────
#  Section 3 — Correlation Heatmap
# ─────────────────────────────────────────────────────────────────────────────

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


def _render_correlation_heatmap(rng: random.Random, source: DataSource) -> None:
    try:
        noise = np.array([[rng.uniform(-0.06, 0.06) for _ in range(8)] for _ in range(8)])
        corr = np.clip(_CORR_BASE + noise, -1.0, 1.0)
        np.fill_diagonal(corr, 1.0)
        corr = np.round(corr, 2)

        text_matrix = [[f"{v:.2f}" for v in row] for row in corr]

        # Semantic scale: red = negative, palette-neutral mid, green = positive.
        fig = go.Figure(go.Heatmap(
            z=corr.tolist(),
            x=_CORR_LABELS,
            y=_CORR_LABELS,
            text=text_matrix,
            texttemplate="%{text}",
            textfont={"size": 11, "color": C_TEXT},
            colorscale=[
                [0.0,  C_LOW],       # strongly negative
                [0.35, C_MOD],       # mildly negative
                [0.5,  "#12151e"],   # neutral — matches surface
                [0.65, "#5fa884"],   # mildly positive
                [1.0,  C_HIGH],      # strongly positive
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
        apply_dark_layout(
            fig,
            title="Asset & Index Correlation Matrix",
            height=380,
            margin=dict(l=10, r=10, t=44, b=10),
            showlegend=False,
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=11), side="bottom"),
            yaxis=dict(tickfont=dict(color=C_TEXT2, size=11), autorange="reversed"),
        )
        _render_badge_row(source)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        logger.error(f"_render_correlation_heatmap: {exc}")
        st.warning("Correlation heatmap unavailable.")


# ─────────────────────────────────────────────────────────────────────────────
#  Section 4 — Drawdown Waterfall
# ─────────────────────────────────────────────────────────────────────────────

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


def _render_drawdown_waterfall(source: DataSource) -> None:
    try:
        events     = [e["event"] for e in _DRAWDOWN_EVENTS]
        dds        = [e["dd"] * 100 for e in _DRAWDOWN_EVENTS]
        durations  = [e["duration_d"] for e in _DRAWDOWN_EVENTS]
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
        apply_dark_layout(
            fig,
            title="10 Largest Shipping Market Drawdown Events",
            height=380,
            margin=dict(l=10, r=10, t=44, b=10),
            showlegend=False,
            yaxis=dict(
                title="Drawdown (%)",
                tickfont=dict(color=C_TEXT2, size=10),
                zeroline=True,
                zerolinecolor=C_BORDER,
            ),
            xaxis=dict(tickfont=dict(color=C_TEXT2, size=10), tickangle=-30),
            annotations=annotations,
            bargap=0.3,
        )
        _render_badge_row(source)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        logger.error(f"_render_drawdown_waterfall: {exc}")
        st.warning("Drawdown waterfall unavailable.")


# ─────────────────────────────────────────────────────────────────────────────
#  Section 5 — Scenario Stress Test
# ─────────────────────────────────────────────────────────────────────────────

_SCENARIOS = [
    {
        "name":           "2008 Global Financial Crisis",
        "prob":           5,
        "bdi_impact":     -75,
        "freight_impact": -68,
        "equity_impact":  -52,
        "pl_impact":      -38,
    },
    {
        "name":           "2020 COVID-19 Pandemic",
        "prob":           8,
        "bdi_impact":     -48,
        "freight_impact": -41,
        "equity_impact":  -34,
        "pl_impact":      -22,
    },
    {
        "name":           "2021 Suez Canal Blockage",
        "prob":           12,
        "bdi_impact":     +14,
        "freight_impact": +22,
        "equity_impact":  +3,
        "pl_impact":      +8,
    },
    {
        "name":           "2022 Ukraine War / Sanctions",
        "prob":           15,
        "bdi_impact":     -28,
        "freight_impact": +18,
        "equity_impact":  -21,
        "pl_impact":      -11,
    },
    {
        "name":           "2024 Red Sea Escalation",
        "prob":           35,
        "bdi_impact":     +32,
        "freight_impact": +45,
        "equity_impact":  -8,
        "pl_impact":      +14,
    },
    {
        "name":           "Custom: China Hard Landing",
        "prob":           18,
        "bdi_impact":     -55,
        "freight_impact": -48,
        "equity_impact":  -40,
        "pl_impact":      -29,
    },
]


def _pct_cell(val: int) -> str:
    color = C_HIGH if val > 0 else C_LOW if val < 0 else C_TEXT2
    sign  = "+" if val > 0 else ""
    return _mono(f"{sign}{val}%", color=color, weight=700)


def _render_stress_test(source: DataSource) -> None:
    try:
        headers = ["Scenario", "Probability", "BDI Impact", "Freight Rate",
                   "Equity Impact", "Portfolio P&L"]
        rows: list[list[str]] = []
        for sc in _SCENARIOS:
            rows.append([
                _sans(sc["name"], color=C_TEXT, weight=700),
                _mono(f"{sc['prob']}%", color=C_ACCENT, weight=700),
                _pct_cell(sc["bdi_impact"]),
                _pct_cell(sc["freight_impact"]),
                _pct_cell(sc["equity_impact"]),
                _pct_cell(sc["pl_impact"]),
            ])
        _render_badge_row(source)
        wsj_market_table(headers, rows)
    except Exception as exc:
        logger.error(f"_render_stress_test: {exc}")
        st.warning("Stress test table unavailable.")


# ─────────────────────────────────────────────────────────────────────────────
#  Section 6 — Risk Alert Queue
# ─────────────────────────────────────────────────────────────────────────────

def _build_alerts(insights, macro_data, freight_data, rng: random.Random) -> list[dict]:
    alerts: list[dict] = []
    try:
        if isinstance(insights, dict):
            raw = insights.get("alerts", insights.get("risk_alerts", []))
            if isinstance(raw, list):
                for a in raw:
                    if isinstance(a, dict):
                        alerts.append({"severity": a.get("severity", "MODERATE"),
                                       "text":     a.get("message", str(a))})
                    elif isinstance(a, str):
                        alerts.append({"severity": "MODERATE", "text": a})
        if isinstance(macro_data, dict):
            bdi = macro_data.get("bdi")
            if bdi and float(bdi) < 1000:
                alerts.append({"severity": "HIGH",
                               "text": f"BDI at {float(bdi):.0f} — below 1000 threshold, dry bulk distress."})
            vix = macro_data.get("vix")
            if vix and float(vix) > 30:
                alerts.append({"severity": "HIGH",
                               "text": f"VIX at {float(vix):.1f} — elevated macro volatility."})
    except Exception as exc:
        logger.warning(f"alert build error: {exc}")

    # Ensure at least 4 synthetic alerts for demo richness.
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


def _severity_score(sev: str) -> float:
    return {"CRITICAL": 0.95, "HIGH": 0.75, "MODERATE": 0.5, "LOW": 0.25}.get(sev, 0.5)


def _severity_action(sev: str) -> str:
    return {"CRITICAL": "Avoid", "HIGH": "Caution",
            "MODERATE": "Monitor", "LOW": "Watch"}.get(sev, "Monitor")


def _render_alert_queue(alerts: list[dict], source: DataSource) -> None:
    try:
        if not alerts:
            st.info("No active risk alerts.")
            return

        _render_badge_row(source)
        for al in alerts:
            sev = al.get("severity", "LOW")
            st.html(insight_card_html(
                title=al.get("text", ""),
                score=_severity_score(sev),
                action=_severity_action(sev),
                rationale="",
                category=sev,
            ))
    except Exception as exc:
        logger.error(f"_render_alert_queue: {exc}")
        st.warning("Alert queue unavailable.")


# ─────────────────────────────────────────────────────────────────────────────
#  Main render entry point
# ─────────────────────────────────────────────────────────────────────────────

def render(stock_data, macro_data, insights, freight_data=None):
    try:
        seed = _seed(stock_data)
        rng  = random.Random(seed)

        # Provenance sources — all sections currently draw from synthetic fixtures,
        # so every pill will display red "DEMO" to signal distrust.
        kpi_source     = _risk_demo_source("Risk KPI Model")
        matrix_source  = _risk_demo_source("Risk Factor Matrix")
        corr_source    = _risk_demo_source("Cross-Asset Correlations")
        dd_source      = _risk_demo_source("Historical Drawdown Ledger")
        stress_source  = _risk_demo_source("Macro Stress Scenarios")
        alert_source   = _risk_demo_source("Alert Engine")

        page_header(
            title="Risk Management Dashboard",
            subtitle="Institutional risk intelligence — shipping & macro factors",
            badge_text="DEMO",
            badge_color=C_LOW,
        )

        # ── Section 1: KPI hero ─────────────────────────────────────────────
        section_header(
            "Risk Dashboard",
            "Live risk KPIs across volatility, drawdown, and tail exposure",
        )
        kpis = _compute_kpis(stock_data, macro_data, freight_data, rng)
        _render_kpis(kpis, kpi_source)

        # ── Section 2: Risk factor matrix ──────────────────────────────────
        section_header(
            "Risk Factor Matrix",
            "Exposure level, recent trend, and mitigation for 10 core risk factors",
        )
        _render_risk_factor_matrix(matrix_source)

        # ── Section 3 & 4: Heatmap + drawdown side by side ────────────────
        section_header(
            "Correlation Heatmap & Historical Drawdowns",
            "Cross-asset correlations and largest shipping market drawdowns",
        )
        col_left, col_right = st.columns(2)
        with col_left:
            _render_correlation_heatmap(rng, corr_source)
        with col_right:
            _render_drawdown_waterfall(dd_source)

        # ── Section 5: Stress test ──────────────────────────────────────────
        section_header(
            "Scenario Stress Test",
            "Probability-weighted impact across 6 macro and shipping shock scenarios",
        )
        _render_stress_test(stress_source)

        # ── Section 6: Alert queue ──────────────────────────────────────────
        section_header(
            "Risk Alert Queue",
            "Current alerts ranked by severity",
        )
        alerts = _build_alerts(insights, macro_data, freight_data, rng)
        _render_alert_queue(alerts, alert_source)

        # Footer timestamp — divider provides the thin top rule; caption handles muted text.
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        st.divider()
        st.caption(f"Last updated: {now}")

    except Exception as exc:
        logger.error(f"tab_risk_matrix render error: {exc}")
        st.error(f"Risk dashboard render error: {exc}")
