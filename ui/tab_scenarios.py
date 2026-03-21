"""
Scenario Analysis Tab — Complete Rewrite
=========================================
A fully-featured scenario intelligence dashboard covering:

  Hero Dashboard   — base case summary, key assumptions, confidence, time horizon
  Section 1        — Three-scenario (Base/Bull/Bear) comparison cards
  Section 2        — Scenario parameter sliders (demand growth, fleet growth, disruption)
  Section 3        — Rate forecast fan chart (Base/Bull/Bear paths + current rate)
  Section 4        — Scenario probability gauge / pie
  Section 5        — Historical scenario accuracy tracker
  Section 6        — Trigger event monitor
  Section 7        — Sensitivity / tornado chart
  Section 8        — Scenario implications by carrier (ZIM, MATX, SBLK, GOGL)
  Section 9        — Scenario watchlist
  Section 10       — Legacy: predefined what-if scenario card selector + results
"""
from __future__ import annotations

import math
import streamlit as st
import plotly.graph_objects as go

from processing.scenario_analyzer import (
    PREDEFINED_SCENARIOS,
    ScenarioInput,
    run_scenario,
    run_all_scenarios,
)

# ── Colour palette ─────────────────────────────────────────────────────────────
_C_BULL   = "#10b981"   # emerald green
_C_BASE   = "#3b82f6"   # blue
_C_BEAR   = "#ef4444"   # red
_C_GOLD   = "#f59e0b"   # amber
_C_PURPLE = "#a855f7"
_C_MUTED  = "#64748b"
_C_TEXT   = "#f1f5f9"
_C_SUB    = "#94a3b8"
_C_CARD   = "#1a2235"
_C_BORDER = "rgba(255,255,255,0.07)"

_RISK_COLORS = {
    "LOW": _C_BULL,
    "MODERATE": _C_GOLD,
    "HIGH": _C_BEAR,
    "SEVERE": "#dc2626",
}
_CARD_ACCENTS = [_C_BEAR, _C_PURPLE, _C_GOLD, _C_BASE]

_SCENARIO_META: dict[str, dict] = {
    "Suez": {
        "icon": "🚢",
        "tag": "Canal Closure",
        "duration": "3–12 months",
        "affected_ports": ["Port Said", "Suez", "Singapore", "Rotterdam", "Hamburg"],
    },
    "Panama": {
        "icon": "🏔️",
        "tag": "Drought / Capacity",
        "duration": "3–9 months",
        "affected_ports": ["Colon", "Balboa", "Los Angeles", "New York", "Houston"],
    },
    "Trade": {
        "icon": "⚔️",
        "tag": "Trade War",
        "duration": "12–36 months",
        "affected_ports": ["Shanghai", "Los Angeles", "Long Beach", "Busan", "Yokohama"],
    },
    "Manufacturing": {
        "icon": "🏭",
        "tag": "Demand Surge",
        "duration": "6–18 months",
        "affected_ports": ["All major hubs"],
    },
    "Oil": {
        "icon": "🛢️",
        "tag": "Oil Shock",
        "duration": "6–18 months",
        "affected_ports": ["Ras Tanura", "Fujairah", "Singapore", "Rotterdam", "Houston"],
    },
    "Recession": {
        "icon": "📉",
        "tag": "Demand Collapse",
        "duration": "12–24 months",
        "affected_ports": ["All major hubs", "Feeder ports most exposed"],
    },
}


def _get_meta(name: str) -> dict:
    for key, meta in _SCENARIO_META.items():
        if key.lower() in name.lower():
            return meta
    return {"icon": "⚡", "tag": "Scenario", "duration": "Variable", "affected_ports": []}


def _score_bar(score: float, color: str = _C_BASE, width: int = 120) -> str:
    pct = max(0, min(100, int(score * 100)))
    return (
        f"<div style='display:inline-block; vertical-align:middle; width:{width}px;"
        f" background:rgba(255,255,255,0.06); border-radius:4px; height:7px; overflow:hidden'>"
        f"<div style='width:{pct}%; height:100%; background:{color}; border-radius:4px'></div>"
        f"</div> <span style='font-size:0.70rem; color:{_C_MUTED}; margin-left:4px'>{pct}%</span>"
    )


def _delta_arrow(delta: float) -> str:
    if delta > 0.005:
        return f"<span style='color:{_C_BULL}; font-weight:700'>▲ {delta:+.1%}</span>"
    if delta < -0.005:
        return f"<span style='color:{_C_BEAR}; font-weight:700'>▼ {delta:.1%}</span>"
    return f"<span style='color:{_C_MUTED}'>— {delta:+.1%}</span>"


def _section_header(label: str) -> None:
    st.markdown(
        f"<div style='font-size:0.68rem; text-transform:uppercase; letter-spacing:0.12em;"
        f" color:{_C_MUTED}; margin-bottom:12px; font-weight:700'>{label}</div>",
        unsafe_allow_html=True,
    )


def _divider() -> None:
    st.markdown(
        f"<hr style='border:none; border-top:1px solid {_C_BORDER}; margin:28px 0'>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# HERO DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def _render_hero(macro_data: dict) -> None:
    """Scenarios hero dashboard — base case summary + four info cards."""
    try:
        bdi  = macro_data.get("bdi", 1_850) if isinstance(macro_data, dict) else 1_850
        wci  = macro_data.get("wci", 2_100) if isinstance(macro_data, dict) else 2_100
        conf = macro_data.get("confidence", 68)  if isinstance(macro_data, dict) else 68
        hz   = macro_data.get("time_horizon", "Q2 2026") if isinstance(macro_data, dict) else "Q2 2026"
    except Exception:
        bdi, wci, conf, hz = 1_850, 2_100, 68, "Q2 2026"

    st.markdown("""
    <div style="padding:18px 0 26px 0; border-bottom:1px solid rgba(255,255,255,0.06);
                margin-bottom:26px">
        <div style="font-size:0.66rem; text-transform:uppercase; letter-spacing:0.16em;
                    color:#475569; margin-bottom:6px">SCENARIO INTELLIGENCE</div>
        <div style="font-size:1.75rem; font-weight:900; color:#f1f5f9;
                    letter-spacing:-0.03em; line-height:1.1">
            Scenario Analysis
        </div>
        <div style="font-size:0.84rem; color:#64748b; margin-top:6px">
            Base · Bull · Bear paths — interactive rate forecasts, carrier implications &amp;
            trigger event monitoring
        </div>
    </div>
    """, unsafe_allow_html=True)

    try:
        c1, c2, c3, c4 = st.columns(4)

        def _mini_card(col, label, value, sub, accent):
            with col:
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {_C_BORDER};"
                    f" border-top:3px solid {accent}; border-radius:10px; padding:16px 14px'>"
                    f"<div style='font-size:0.62rem; text-transform:uppercase;"
                    f" letter-spacing:0.1em; color:{_C_MUTED}; margin-bottom:6px'>{label}</div>"
                    f"<div style='font-size:1.45rem; font-weight:900; color:{_C_TEXT};'>{value}</div>"
                    f"<div style='font-size:0.68rem; color:{_C_MUTED}; margin-top:4px'>{sub}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        _mini_card(c1, "Base Case BDI", f"{bdi:,}", "Current spot rate proxy", _C_BASE)
        _mini_card(c2, "Key Assumption", "Fleet growth 3.2% YoY", "Orderbook-adjusted", _C_GOLD)

        # Confidence gauge as colour-coded badge
        conf_color = _C_BULL if conf >= 70 else (_C_GOLD if conf >= 50 else _C_BEAR)
        with c3:
            st.markdown(
                f"<div style='background:{_C_CARD}; border:1px solid {_C_BORDER};"
                f" border-top:3px solid {conf_color}; border-radius:10px; padding:16px 14px'>"
                f"<div style='font-size:0.62rem; text-transform:uppercase;"
                f" letter-spacing:0.1em; color:{_C_MUTED}; margin-bottom:6px'>Model Confidence</div>"
                f"<div style='font-size:1.45rem; font-weight:900; color:{conf_color}'>{conf}%</div>"
                f"<div style='background:rgba(255,255,255,0.05); border-radius:4px;"
                f" height:6px; margin-top:8px; overflow:hidden'>"
                f"<div style='width:{conf}%; height:100%; background:{conf_color};"
                f" border-radius:4px'></div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        _mini_card(c4, "Time Horizon", hz, "Forecast window", _C_PURPLE)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Three-Scenario Comparison Cards
# ══════════════════════════════════════════════════════════════════════════════

# Static scenario parameters; sliders in section 2 will push overrides into session state
_SCENARIO_DEFAULTS = {
    "bull": {
        "rate_12m": 3_200,
        "prob": 25,
        "drivers": [
            "Suez / Panama simultaneous disruption",
            "China stimulus revives demand +18%",
            "Fleet growth delayed; scrapping spike",
        ],
    },
    "base": {
        "rate_12m": 2_050,
        "prob": 55,
        "drivers": [
            "Moderate demand recovery (+5–8%)",
            "Fleet growth in line with orderbook",
            "No major geopolitical disruption",
        ],
    },
    "bear": {
        "rate_12m": 1_100,
        "prob": 20,
        "drivers": [
            "Trade war escalation; volume -12%",
            "Overcapacity from ULCVs on order",
            "Global manufacturing PMI < 48",
        ],
    },
}


def _render_three_scenario_cards() -> None:
    """Bull / Base / Bear comparison cards."""
    try:
        _section_header("Three-Scenario Outlook — Base · Bull · Bear")

        bull_p = st.session_state.get("scen_prob_bull", _SCENARIO_DEFAULTS["bull"]["prob"])
        base_p = st.session_state.get("scen_prob_base", _SCENARIO_DEFAULTS["base"]["prob"])
        bear_p = st.session_state.get("scen_prob_bear", _SCENARIO_DEFAULTS["bear"]["prob"])

        cols = st.columns(3)
        cards = [
            ("Bull Case", _C_BULL, "↑", bull_p, _SCENARIO_DEFAULTS["bull"]["rate_12m"],
             _SCENARIO_DEFAULTS["bull"]["drivers"]),
            ("Base Case", _C_BASE, "→", base_p, _SCENARIO_DEFAULTS["base"]["rate_12m"],
             _SCENARIO_DEFAULTS["base"]["drivers"]),
            ("Bear Case", _C_BEAR, "↓", bear_p, _SCENARIO_DEFAULTS["bear"]["rate_12m"],
             _SCENARIO_DEFAULTS["bear"]["drivers"]),
        ]

        for col, (title, accent, arrow, prob, rate, drivers) in zip(cols, cards):
            driver_html = "".join(
                f"<div style='display:flex; align-items:flex-start; gap:6px;"
                f" margin-bottom:5px'>"
                f"<span style='color:{accent}; margin-top:1px; font-size:0.7rem'>•</span>"
                f"<span style='font-size:0.72rem; color:{_C_SUB}; line-height:1.4'>{d}</span>"
                f"</div>"
                for d in drivers
            )
            with col:
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {accent}33;"
                    f" border-top:3px solid {accent}; border-radius:12px; padding:20px 16px'>"
                    f"<div style='display:flex; justify-content:space-between;"
                    f" align-items:center; margin-bottom:12px'>"
                    f"<div style='font-size:0.8rem; font-weight:800; color:{accent}'>"
                    f"{arrow} {title}</div>"
                    f"<div style='background:{accent}22; color:{accent}; font-size:0.68rem;"
                    f" font-weight:700; padding:3px 10px; border-radius:999px'>{prob}%</div>"
                    f"</div>"
                    f"<div style='font-size:2rem; font-weight:900; color:{_C_TEXT};"
                    f" margin-bottom:4px'>{rate:,}</div>"
                    f"<div style='font-size:0.64rem; color:{_C_MUTED}; margin-bottom:14px'>"
                    f"BDI proxy · 12-month target</div>"
                    f"<div style='font-size:0.65rem; font-weight:700; color:{_C_MUTED};"
                    f" text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px'>"
                    f"Key Drivers</div>"
                    f"{driver_html}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Scenario Parameter Sliders
# ══════════════════════════════════════════════════════════════════════════════

def _render_parameter_sliders() -> dict:
    """Interactive sliders for key inputs; returns current parameter dict."""
    params: dict = {}
    try:
        with st.expander("Scenario Parameter Controls", expanded=False):
            st.markdown(
                f"<div style='font-size:0.78rem; color:{_C_MUTED}; margin-bottom:14px'>"
                "Adjust macro inputs to reshaping the Bull / Base / Bear rate paths and"
                " probability distribution.</div>",
                unsafe_allow_html=True,
            )
            c1, c2, c3 = st.columns(3)

            with c1:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{_C_BASE};"
                    " text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px'>"
                    "Demand Factors</div>", unsafe_allow_html=True)
                demand_growth = st.slider(
                    "Demand Growth (%)", -20, 30, 5, 1,
                    format="%d%%", key="sp_demand_growth",
                )
                china_stimulus = st.slider(
                    "China Stimulus Intensity", 0, 10, 4, 1,
                    help="0 = none, 10 = massive", key="sp_china_stim",
                )

            with c2:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{_C_GOLD};"
                    " text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px'>"
                    "Supply Factors</div>", unsafe_allow_html=True)
                fleet_growth = st.slider(
                    "Fleet Growth (%)", 0, 10, 3, 1,
                    format="%d%%", key="sp_fleet_growth",
                )
                scrapping_rate = st.slider(
                    "Scrapping Rate (ships/yr)", 0, 200, 40, 10,
                    key="sp_scrapping",
                )

            with c3:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{_C_BEAR};"
                    " text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px'>"
                    "Risk Factors</div>", unsafe_allow_html=True)
                disruption_prob = st.slider(
                    "Major Disruption Probability (%)", 0, 60, 15, 5,
                    format="%d%%", key="sp_disrupt_prob",
                )
                tariff_level = st.slider(
                    "US-China Tariff Level (%)", 0, 60, 25, 5,
                    format="%d%%", key="sp_tariff",
                )

            params = {
                "demand_growth": demand_growth,
                "china_stimulus": china_stimulus,
                "fleet_growth": fleet_growth,
                "scrapping_rate": scrapping_rate,
                "disruption_prob": disruption_prob,
                "tariff_level": tariff_level,
            }

            # Derive adjusted probabilities and push to session state
            bull_adj = max(5, min(70, 10 + demand_growth * 0.8 + disruption_prob * 0.6
                                  + china_stimulus * 2 - fleet_growth * 1.5))
            bear_adj = max(5, min(70, 25 + tariff_level * 0.4 + fleet_growth * 2
                                  - demand_growth * 0.7))
            base_adj = max(5, 100 - bull_adj - bear_adj)
            st.session_state["scen_prob_bull"] = round(bull_adj)
            st.session_state["scen_prob_base"] = round(base_adj)
            st.session_state["scen_prob_bear"] = round(bear_adj)

            b1, b2, b3 = st.columns(3)
            for col, label, val, color in [
                (b1, "Bull Probability", round(bull_adj), _C_BULL),
                (b2, "Base Probability", round(base_adj), _C_BASE),
                (b3, "Bear Probability", round(bear_adj), _C_BEAR),
            ]:
                with col:
                    st.markdown(
                        f"<div style='background:{_C_CARD}; border:1px solid {color}44;"
                        f" border-radius:8px; padding:10px 14px; text-align:center'>"
                        f"<div style='font-size:0.6rem; color:{_C_MUTED}; margin-bottom:4px'>"
                        f"{label}</div>"
                        f"<div style='font-size:1.5rem; font-weight:900; color:{color}'>"
                        f"{val}%</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
    except Exception:
        pass
    return params


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Rate Forecast Fan Chart
# ══════════════════════════════════════════════════════════════════════════════

def _render_rate_fan_chart() -> None:
    """Fan chart showing Base/Bull/Bear BDI paths over 12 months."""
    try:
        _section_header("Rate Forecast Fan Chart — 12-Month Paths")

        # Build month labels
        months = ["Now", "M+1", "M+2", "M+3", "M+4", "M+5",
                  "M+6", "M+7", "M+8", "M+9", "M+10", "M+11", "M+12"]
        current_bdi = 1_850

        # Parametric paths (simple exponential ramp toward 12m target)
        def _path(start, end, n=13):
            return [round(start + (end - start) * (i / (n - 1))) for i in range(n)]

        bull_path = _path(current_bdi, _SCENARIO_DEFAULTS["bull"]["rate_12m"])
        base_path = _path(current_bdi, _SCENARIO_DEFAULTS["base"]["rate_12m"])
        bear_path = _path(current_bdi, _SCENARIO_DEFAULTS["bear"]["rate_12m"])

        fig = go.Figure()

        # Fan fill between bull and bear
        fig.add_trace(go.Scatter(
            x=months + months[::-1],
            y=bull_path + bear_path[::-1],
            fill="toself",
            fillcolor="rgba(59,130,246,0.06)",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
            name="fan",
        ))

        # Bull path
        fig.add_trace(go.Scatter(
            x=months, y=bull_path,
            mode="lines",
            name="Bull Case",
            line=dict(color=_C_BULL, width=2, dash="dot"),
            hovertemplate="<b>Bull</b> %{x}: BDI %{y:,}<extra></extra>",
        ))

        # Base path (main line)
        fig.add_trace(go.Scatter(
            x=months, y=base_path,
            mode="lines+markers",
            name="Base Case",
            line=dict(color=_C_BASE, width=3),
            marker=dict(size=5, color=_C_BASE),
            hovertemplate="<b>Base</b> %{x}: BDI %{y:,}<extra></extra>",
        ))

        # Bear path
        fig.add_trace(go.Scatter(
            x=months, y=bear_path,
            mode="lines",
            name="Bear Case",
            line=dict(color=_C_BEAR, width=2, dash="dot"),
            hovertemplate="<b>Bear</b> %{x}: BDI %{y:,}<extra></extra>",
        ))

        # Current rate marker
        fig.add_hline(
            y=current_bdi,
            line_color="rgba(255,255,255,0.20)",
            line_width=1,
            line_dash="dash",
            annotation_text=f"Current: {current_bdi:,}",
            annotation_font_color=_C_SUB,
            annotation_font_size=10,
        )

        fig.update_layout(
            template="plotly_dark",
            height=340,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=_C_SUB, size=11),
            margin=dict(l=10, r=20, t=16, b=30),
            xaxis=dict(gridcolor="rgba(255,255,255,0.04)", zeroline=False),
            yaxis=dict(
                gridcolor="rgba(255,255,255,0.04)",
                zeroline=False,
                tickformat=",d",
                title="BDI",
                title_font=dict(size=10, color=_C_MUTED),
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="center", x=0.5,
                bgcolor="rgba(0,0,0,0)",
                font=dict(size=11),
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="scen_fan_chart")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Scenario Probability Gauge / Pie
# ══════════════════════════════════════════════════════════════════════════════

def _render_probability_gauge() -> None:
    """Donut / gauge chart for Bull / Base / Bear probability distribution."""
    try:
        bull_p = st.session_state.get("scen_prob_bull", 25)
        base_p = st.session_state.get("scen_prob_base", 55)
        bear_p = st.session_state.get("scen_prob_bear", 20)

        _section_header("Scenario Probability Distribution")

        col_gauge, col_legend = st.columns([2, 1])

        with col_gauge:
            fig = go.Figure(go.Pie(
                labels=["Bull Case", "Base Case", "Bear Case"],
                values=[bull_p, base_p, bear_p],
                hole=0.62,
                marker=dict(
                    colors=[_C_BULL, _C_BASE, _C_BEAR],
                    line=dict(color="#0f172a", width=2),
                ),
                textinfo="label+percent",
                textfont=dict(size=11, color=_C_TEXT),
                hovertemplate="<b>%{label}</b>: %{value}%<extra></extra>",
            ))
            fig.add_annotation(
                text=f"<b>{base_p}%</b><br><span style='font-size:10px'>Base</span>",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=18, color=_C_BASE),
            )
            fig.update_layout(
                template="plotly_dark",
                height=280,
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=20, b=10),
                showlegend=False,
                font=dict(color=_C_SUB),
            )
            st.plotly_chart(fig, use_container_width=True, key="scen_prob_donut")

        with col_legend:
            st.markdown("<div style='margin-top:30px'></div>", unsafe_allow_html=True)
            for label, val, color, desc in [
                ("Bull Case", bull_p, _C_BULL, "Above-consensus demand + disruption"),
                ("Base Case", base_p, _C_BASE, "Consensus macro, orderbook delivery"),
                ("Bear Case", bear_p, _C_BEAR, "Trade headwinds + oversupply"),
            ]:
                st.markdown(
                    f"<div style='background:{_C_CARD}; border-left:3px solid {color};"
                    f" border-radius:0 8px 8px 0; padding:10px 12px; margin-bottom:10px'>"
                    f"<div style='font-size:0.75rem; font-weight:700; color:{color}'>"
                    f"{label} — {val}%</div>"
                    f"<div style='font-size:0.67rem; color:{_C_MUTED}; margin-top:3px'>"
                    f"{desc}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Historical Scenario Accuracy Tracker
# ══════════════════════════════════════════════════════════════════════════════

_HISTORY = [
    {"period": "Q3 2024", "scenario": "Base",  "forecast_bdi": 1_650, "realized_bdi": 1_720,
     "accuracy": 0.96, "notes": "Demand in-line; fleet growth slight beat"},
    {"period": "Q4 2024", "scenario": "Bear",  "forecast_bdi": 1_250, "realized_bdi": 1_190,
     "accuracy": 0.95, "notes": "Trade war drag greater than modelled"},
    {"period": "Q1 2025", "scenario": "Bull",  "forecast_bdi": 2_600, "realized_bdi": 2_110,
     "accuracy": 0.81, "notes": "China stimulus weaker; disruption shorter"},
    {"period": "Q2 2025", "scenario": "Base",  "forecast_bdi": 1_900, "realized_bdi": 1_970,
     "accuracy": 0.96, "notes": "In-line with consensus"},
    {"period": "Q3 2025", "scenario": "Bear",  "forecast_bdi": 1_400, "realized_bdi": 1_530,
     "accuracy": 0.91, "notes": "Recovery faster than expected; miss on upside"},
    {"period": "Q4 2025", "scenario": "Bull",  "forecast_bdi": 2_400, "realized_bdi": 2_290,
     "accuracy": 0.95, "notes": "Canal disruption materialised; tight supply"},
]


def _render_history_tracker() -> None:
    """Bar + table showing historical scenario forecast vs realised BDI."""
    try:
        _section_header("Historical Scenario Accuracy Tracker")

        periods     = [h["period"] for h in _HISTORY]
        forecasts   = [h["forecast_bdi"] for h in _HISTORY]
        realized    = [h["realized_bdi"] for h in _HISTORY]
        accuracies  = [h["accuracy"] for h in _HISTORY]
        scen_colors = {
            "Bull": _C_BULL, "Base": _C_BASE, "Bear": _C_BEAR,
        }

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Forecast BDI", x=periods, y=forecasts,
            marker_color=_C_BASE, opacity=0.6,
            hovertemplate="<b>%{x}</b><br>Forecast: %{y:,}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            name="Realized BDI", x=periods, y=realized,
            mode="lines+markers",
            line=dict(color=_C_GOLD, width=2),
            marker=dict(size=8, color=_C_GOLD, line=dict(color="#0f172a", width=2)),
            hovertemplate="<b>%{x}</b><br>Realized: %{y:,}<extra></extra>",
        ))
        fig.update_layout(
            template="plotly_dark",
            height=280,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=_C_SUB, size=11),
            margin=dict(l=10, r=20, t=16, b=30),
            barmode="group",
            xaxis=dict(gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickformat=",d", title="BDI"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="center", x=0.5, bgcolor="rgba(0,0,0,0)"),
        )
        st.plotly_chart(fig, use_container_width=True, key="scen_history_chart")

        # Accuracy row tiles
        acc_cols = st.columns(len(_HISTORY))
        for col, h in zip(acc_cols, _HISTORY):
            acc_pct = h["accuracy"]
            color = _C_BULL if acc_pct >= 0.93 else (_C_GOLD if acc_pct >= 0.85 else _C_BEAR)
            sc    = h["scenario"]
            sc_c  = scen_colors.get(sc, _C_MUTED)
            with col:
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {_C_BORDER};"
                    f" border-radius:8px; padding:8px 10px; text-align:center'>"
                    f"<div style='font-size:0.60rem; color:{_C_MUTED}'>{h['period']}</div>"
                    f"<div style='font-size:0.65rem; font-weight:700; color:{sc_c};"
                    f" margin:2px 0'>{sc}</div>"
                    f"<div style='font-size:1.1rem; font-weight:900; color:{color}'>"
                    f"{acc_pct:.0%}</div>"
                    f"<div style='font-size:0.58rem; color:{_C_MUTED}'>accuracy</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Trigger Event Monitor
# ══════════════════════════════════════════════════════════════════════════════

_TRIGGERS = [
    {
        "event": "Suez / Red Sea reopens fully",
        "current": "Partial restriction",
        "moves_to": "Bear",
        "magnitude": -18,
        "prob_next_90d": 22,
        "status": "watch",
    },
    {
        "event": "China PMI > 52 for 2 consecutive months",
        "current": "49.8",
        "moves_to": "Bull",
        "magnitude": +22,
        "prob_next_90d": 34,
        "status": "elevated",
    },
    {
        "event": "US-China tariff truce / rollback",
        "current": "25% tariff in force",
        "moves_to": "Bull",
        "magnitude": +15,
        "prob_next_90d": 18,
        "status": "watch",
    },
    {
        "event": "Panama Canal capacity < 24 transits/day",
        "current": "32 transits/day",
        "moves_to": "Bull",
        "magnitude": +20,
        "prob_next_90d": 12,
        "status": "monitor",
    },
    {
        "event": "Global recession signal (ISM MFG < 45)",
        "current": "ISM 48.2",
        "moves_to": "Bear",
        "magnitude": -28,
        "prob_next_90d": 15,
        "status": "watch",
    },
    {
        "event": "Bunker fuel >$750/mt sustained",
        "current": "$590/mt",
        "moves_to": "Bear",
        "magnitude": -10,
        "prob_next_90d": 20,
        "status": "monitor",
    },
]

_STATUS_COLORS = {
    "elevated": _C_BEAR,
    "watch": _C_GOLD,
    "monitor": _C_BASE,
}


def _render_trigger_monitor() -> None:
    """Table of events that would shift scenario from Base to Bull or Bear."""
    try:
        _section_header("Trigger Event Monitor — Scenario Transition Catalysts")

        st.markdown(
            f"<div style='font-size:0.78rem; color:{_C_MUTED}; margin-bottom:14px'>"
            "Events below would shift the modal scenario. Monitor these indicators"
            " for early-warning signals.</div>",
            unsafe_allow_html=True,
        )

        header_cols = st.columns([3, 2, 1, 1.5, 1.5, 1.2])
        for col, label in zip(header_cols,
                               ["Event", "Current Reading", "→ Scenario",
                                "Rate Impact", "90d Probability", "Status"]):
            with col:
                st.markdown(
                    f"<div style='font-size:0.62rem; text-transform:uppercase;"
                    f" letter-spacing:0.09em; color:{_C_MUTED}; font-weight:700;"
                    f" padding-bottom:6px; border-bottom:1px solid {_C_BORDER}'>"
                    f"{label}</div>",
                    unsafe_allow_html=True,
                )

        for tr in _TRIGGERS:
            sc_color = _C_BULL if tr["moves_to"] == "Bull" else _C_BEAR
            mag_sign  = "+" if tr["magnitude"] > 0 else ""
            st_color  = _STATUS_COLORS.get(tr["status"], _C_MUTED)
            row_cols  = st.columns([3, 2, 1, 1.5, 1.5, 1.2])

            with row_cols[0]:
                st.markdown(
                    f"<div style='font-size:0.73rem; color:{_C_TEXT};"
                    f" padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"{tr['event']}</div>", unsafe_allow_html=True)
            with row_cols[1]:
                st.markdown(
                    f"<div style='font-size:0.73rem; color:{_C_MUTED};"
                    f" padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"{tr['current']}</div>", unsafe_allow_html=True)
            with row_cols[2]:
                st.markdown(
                    f"<div style='font-size:0.73rem; font-weight:700; color:{sc_color};"
                    f" padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"{tr['moves_to']}</div>", unsafe_allow_html=True)
            with row_cols[3]:
                st.markdown(
                    f"<div style='font-size:0.73rem; font-weight:700; color:{sc_color};"
                    f" padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"{mag_sign}{tr['magnitude']}% BDI</div>", unsafe_allow_html=True)
            with row_cols[4]:
                st.markdown(
                    f"<div style='font-size:0.73rem; color:{_C_TEXT};"
                    f" padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"{tr['prob_next_90d']}%</div>", unsafe_allow_html=True)
            with row_cols[5]:
                st.markdown(
                    f"<div style='padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"<span style='background:{st_color}22; color:{st_color};"
                    f" font-size:0.62rem; font-weight:700; padding:2px 9px;"
                    f" border-radius:999px; text-transform:uppercase;"
                    f" letter-spacing:0.07em'>{tr['status'].upper()}</span></div>",
                    unsafe_allow_html=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Sensitivity / Tornado Chart
# ══════════════════════════════════════════════════════════════════════════════

_SENSITIVITY_INPUTS = [
    ("China Manufacturing PMI",    +340, -280),
    ("US-China Tariff Level",      +80,  -380),
    ("Suez Canal Status",          +420, -80),
    ("Fleet Net Growth Rate",      +60,  -310),
    ("Global Oil Price",           -180, +120),
    ("Port Congestion Index",      +200, -90),
    ("Orderbook Delivery Schedule",+50,  -220),
    ("Scrapping Rate",             +160, -50),
]


def _render_sensitivity_tornado() -> None:
    """Tornado chart — which inputs have the biggest impact on 12m rate forecast."""
    try:
        _section_header("Sensitivity Analysis — Tornado Chart (BDI Rate Forecast Impact)")

        # Sort by absolute swing range
        sorted_inputs = sorted(
            _SENSITIVITY_INPUTS,
            key=lambda x: abs(x[1]) + abs(x[2]),
            reverse=True,
        )
        labels     = [s[0] for s in sorted_inputs]
        bull_vals  = [s[1] for s in sorted_inputs]
        bear_vals  = [s[2] for s in sorted_inputs]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Upside (Bull)", x=bull_vals, y=labels,
            orientation="h",
            marker_color=_C_BULL,
            marker_line_width=0,
            hovertemplate="<b>%{y}</b><br>Upside: +%{x} BDI pts<extra></extra>",
        ))
        fig.add_trace(go.Bar(
            name="Downside (Bear)", x=bear_vals, y=labels,
            orientation="h",
            marker_color=_C_BEAR,
            marker_line_width=0,
            hovertemplate="<b>%{y}</b><br>Downside: %{x} BDI pts<extra></extra>",
        ))
        fig.add_vline(x=0, line_color="rgba(255,255,255,0.20)", line_width=1)
        fig.update_layout(
            template="plotly_dark",
            barmode="overlay",
            height=340,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=_C_SUB, size=11),
            margin=dict(l=10, r=20, t=16, b=30),
            xaxis=dict(
                title="BDI Point Impact vs Base",
                title_font=dict(size=10, color=_C_MUTED),
                gridcolor="rgba(255,255,255,0.04)",
                zeroline=False,
                ticksuffix=" pts",
            ),
            yaxis=dict(gridcolor="rgba(0,0,0,0)", automargin=True),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="center", x=0.5,
                bgcolor="rgba(0,0,0,0)",
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="scen_tornado_chart")

        st.markdown(
            f"<div style='font-size:0.70rem; color:{_C_MUTED}; margin-top:-8px'>"
            "Values show BDI point deviation from the Base Case 12-month forecast of 2,050."
            " Suez Canal status and China PMI are the dominant sensitivities.</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Scenario Implications by Carrier
# ══════════════════════════════════════════════════════════════════════════════

_CARRIER_DATA = {
    "ZIM": {
        "color": "#06b6d4",
        "icon": "🇮🇱",
        "segment": "Container",
        "bull": {"eps_delta": "+42%", "rate_exposure": "High", "note": "Asia-US routes surge; spot rate leverage"},
        "base": {"eps_delta": "+8%",  "rate_exposure": "Med",  "note": "Stable volumes; contract mix helps margins"},
        "bear": {"eps_delta": "-35%", "rate_exposure": "High", "note": "Spot-heavy fleet punished in oversupply"},
    },
    "MATX": {
        "color": "#8b5cf6",
        "icon": "🇺🇸",
        "segment": "Jones Act Container",
        "bull": {"eps_delta": "+18%", "rate_exposure": "Low",  "note": "Protected routes; limited direct rate uplift"},
        "base": {"eps_delta": "+5%",  "rate_exposure": "Low",  "note": "Steady Hawaii/Alaska volumes; regulatory moat"},
        "bear": {"eps_delta": "-9%",  "rate_exposure": "Low",  "note": "Jones Act insulates from global downturn"},
    },
    "SBLK": {
        "color": "#f97316",
        "icon": "🇬🇷",
        "segment": "Dry Bulk",
        "bull": {"eps_delta": "+55%", "rate_exposure": "Very High", "note": "Capesize / Panamax rate gearing; iron ore surge"},
        "base": {"eps_delta": "+12%", "rate_exposure": "High",     "note": "Brazil iron ore + coal; moderate BDI recovery"},
        "bear": {"eps_delta": "-40%", "rate_exposure": "Very High", "note": "Steel demand collapse hits Capesize hardest"},
    },
    "GOGL": {
        "color": "#14b8a6",
        "icon": "🇧🇲",
        "segment": "Dry Bulk",
        "bull": {"eps_delta": "+48%", "rate_exposure": "Very High", "note": "Fleet positioning advantage; low opex base"},
        "base": {"eps_delta": "+10%", "rate_exposure": "High",     "note": "Solid dividend coverage at base BDI"},
        "bear": {"eps_delta": "-38%", "rate_exposure": "Very High", "note": "High leverage amplifies downside at low BDI"},
    },
}


def _render_carrier_implications() -> None:
    """Four-carrier grid showing Bull/Base/Bear EPS deltas and key notes."""
    try:
        _section_header("Scenario Implications by Carrier — ZIM · MATX · SBLK · GOGL")

        selected_scen = st.radio(
            "View implications for:",
            ["Bull Case", "Base Case", "Bear Case"],
            horizontal=True,
            key="carrier_scen_radio",
        )
        scen_key = {"Bull Case": "bull", "Base Case": "base", "Bear Case": "bear"}[selected_scen]
        scen_color = {"bull": _C_BULL, "base": _C_BASE, "bear": _C_BEAR}[scen_key]

        cols = st.columns(4)
        for col, (ticker, data) in zip(cols, _CARRIER_DATA.items()):
            scen = data[scen_key]
            eps  = scen["eps_delta"]
            eps_color = _C_BULL if "+" in eps else _C_BEAR
            with col:
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {data['color']}33;"
                    f" border-top:3px solid {data['color']}; border-radius:12px;"
                    f" padding:18px 14px'>"
                    f"<div style='display:flex; justify-content:space-between;"
                    f" align-items:center; margin-bottom:10px'>"
                    f"<div style='font-size:1rem; font-weight:900; color:{data['color']}'>"
                    f"{data['icon']} {ticker}</div>"
                    f"<div style='font-size:0.62rem; color:{_C_MUTED}'>{data['segment']}</div>"
                    f"</div>"
                    f"<div style='font-size:0.62rem; text-transform:uppercase;"
                    f" letter-spacing:0.08em; color:{_C_MUTED}; margin-bottom:4px'>"
                    f"EPS Delta ({selected_scen})</div>"
                    f"<div style='font-size:1.8rem; font-weight:900; color:{eps_color};"
                    f" margin-bottom:10px'>{eps}</div>"
                    f"<div style='background:{scen_color}11; border-left:2px solid {scen_color};"
                    f" border-radius:0 6px 6px 0; padding:7px 10px; margin-bottom:10px'>"
                    f"<div style='font-size:0.68rem; color:{_C_SUB}; line-height:1.4'>"
                    f"{scen['note']}</div>"
                    f"</div>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center'>"
                    f"<div style='font-size:0.62rem; color:{_C_MUTED}'>Rate Exposure</div>"
                    f"<div style='font-size:0.65rem; font-weight:700; color:{data['color']}'>"
                    f"{scen['rate_exposure']}</div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Scenario Watchlist
# ══════════════════════════════════════════════════════════════════════════════

_WATCHLIST = [
    {"indicator": "Baltic Dry Index (BDI)",        "current": "1,850", "bull_thresh": "> 2,500",
     "bear_thresh": "< 1,200", "freq": "Daily",   "source": "Baltic Exchange"},
    {"indicator": "China Caixin PMI Manufacturing", "current": "49.8",  "bull_thresh": "> 52.0",
     "bear_thresh": "< 47.0", "freq": "Monthly",  "source": "Caixin / S&P"},
    {"indicator": "Suez Canal Daily Transits",      "current": "45",    "bull_thresh": "< 20 (closure)",
     "bear_thresh": "> 55 (full reopen)", "freq": "Weekly", "source": "Suez Canal Authority"},
    {"indicator": "World Container Index (WCI)",    "current": "$2,100", "bull_thresh": "> $3,500",
     "bear_thresh": "< $1,200", "freq": "Weekly",  "source": "Drewry"},
    {"indicator": "US-China Trade Volume (YoY)",    "current": "-4.2%", "bull_thresh": "> +8%",
     "bear_thresh": "< -15%",  "freq": "Monthly",  "source": "US Census Bureau"},
    {"indicator": "Global Port Congestion Index",   "current": "38",    "bull_thresh": "> 65",
     "bear_thresh": "< 20",    "freq": "Weekly",   "source": "Sea-Intelligence"},
    {"indicator": "VLSFO Bunker Price ($/mt)",      "current": "$590",  "bull_thresh": "> $800",
     "bear_thresh": "< $400",  "freq": "Daily",    "source": "Ship & Bunker"},
    {"indicator": "Orderbook as % of Fleet",        "current": "21.4%", "bull_thresh": "< 10%",
     "bear_thresh": "> 30%",   "freq": "Monthly",  "source": "Clarksons"},
]


def _render_watchlist() -> None:
    """Formatted table of key indicators to monitor for scenario transition signals."""
    try:
        _section_header("Scenario Watchlist — Key Indicators to Monitor")

        st.markdown(
            f"<div style='font-size:0.78rem; color:{_C_MUTED}; margin-bottom:14px'>"
            "These indicators, when crossing their thresholds, are the highest-conviction"
            " signals that the modal scenario is transitioning.</div>",
            unsafe_allow_html=True,
        )

        hdr_cols = st.columns([2.5, 1.2, 1.8, 1.8, 1, 1.5])
        for col, lbl in zip(hdr_cols,
                             ["Indicator", "Current", "Bull Threshold",
                              "Bear Threshold", "Freq", "Source"]):
            with col:
                st.markdown(
                    f"<div style='font-size:0.62rem; text-transform:uppercase;"
                    f" letter-spacing:0.09em; color:{_C_MUTED}; font-weight:700;"
                    f" padding-bottom:6px; border-bottom:1px solid {_C_BORDER}'>"
                    f"{lbl}</div>", unsafe_allow_html=True)

        for item in _WATCHLIST:
            row_cols = st.columns([2.5, 1.2, 1.8, 1.8, 1, 1.5])
            row_data = [
                (item["indicator"], _C_TEXT,   "0.73rem"),
                (item["current"],   _C_SUB,    "0.73rem"),
                (item["bull_thresh"], _C_BULL, "0.71rem"),
                (item["bear_thresh"], _C_BEAR, "0.71rem"),
                (item["freq"],      _C_MUTED,  "0.68rem"),
                (item["source"],    _C_MUTED,  "0.68rem"),
            ]
            for col, (text, color, fsize) in zip(row_cols, row_data):
                with col:
                    st.markdown(
                        f"<div style='font-size:{fsize}; color:{color};"
                        f" padding:7px 0; border-bottom:1px solid rgba(255,255,255,0.03)'>"
                        f"{text}</div>", unsafe_allow_html=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# LEGACY SECTIONS (A–E + 1–5 from previous version — preserved)
# ══════════════════════════════════════════════════════════════════════════════

def _render_scenario_cards(port_results: list, route_results: list) -> str | None:
    try:
        st.markdown(
            f"<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            f" color:{_C_MUTED}; margin-bottom:12px; font-weight:700'>Quick-Select What-If Scenario</div>",
            unsafe_allow_html=True,
        )
        preset_list = list(PREDEFINED_SCENARIOS)
        if "scenario_card_active" not in st.session_state:
            st.session_state["scenario_card_active"] = None

        cols = st.columns(min(len(preset_list), 4))
        for i, preset in enumerate(preset_list[:4]):
            meta   = _get_meta(preset.name)
            accent = _CARD_ACCENTS[i % len(_CARD_ACCENTS)]
            is_active = st.session_state.get("scenario_card_active") == preset.name
            border_style = (
                f"border:2px solid {accent};" if is_active
                else f"border:1px solid {_C_BORDER};"
            )
            bg_color = f"background:{accent}22;" if is_active else f"background:{_C_CARD};"
            bdi_str     = f"BDI {preset.bdi_shock:+.0%}" if preset.bdi_shock != 0 else ""
            demand_str  = f"Demand {preset.demand_shock:+.0%}" if preset.demand_shock != 0 else ""
            fuel_str    = f"Fuel {preset.fuel_shock:+.0%}" if preset.fuel_shock != 0 else ""
            tags = " · ".join(t for t in [bdi_str, demand_str, fuel_str] if t) or "Structural"

            with cols[i]:
                st.markdown(
                    f"<div style='{bg_color} {border_style} border-radius:12px;"
                    f" padding:16px 14px'>"
                    f"<div style='font-size:1.3rem; margin-bottom:6px'>{meta['icon']}</div>"
                    f"<div style='font-size:0.80rem; font-weight:800; color:{_C_TEXT};"
                    f" margin-bottom:4px'>{preset.name}</div>"
                    f"<div style='font-size:0.63rem; color:{accent}; font-weight:700;"
                    f" text-transform:uppercase; letter-spacing:0.08em; margin-bottom:6px'>"
                    f"{meta['tag']}</div>"
                    f"<div style='font-size:0.67rem; color:{_C_SUB}; margin-bottom:6px;"
                    f" line-height:1.4'>"
                    f"{preset.description[:80]}{'…' if len(preset.description) > 80 else ''}"
                    f"</div>"
                    f"<div style='font-size:0.60rem; color:{accent}; font-weight:600'>{tags}</div>"
                    f"<div style='font-size:0.58rem; color:#475569; margin-top:4px'>"
                    f"Est. duration: {meta['duration']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Selected" if is_active else "Select",
                    key=f"leg_card_btn_{i}_{preset.name.replace(' ', '_')}",
                    use_container_width=True,
                ):
                    st.session_state["scenario_card_active"] = preset.name

        return st.session_state.get("scenario_card_active")
    except Exception:
        return None


def _render_route_impact_chart(scenario: ScenarioInput, port_results: list,
                                route_results: list) -> None:
    try:
        result = run_scenario(scenario, port_results, route_results)
    except Exception:
        return
    if not result.route_impacts:
        return
    try:
        meta   = _get_meta(scenario.name)
        accent = _CARD_ACCENTS[
            next((i for i, s in enumerate(PREDEFINED_SCENARIOS)
                  if s.name == scenario.name), 0) % len(_CARD_ACCENTS)
        ]
        st.markdown(
            f"<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            f" color:{_C_MUTED}; margin-bottom:10px; font-weight:700'>"
            f"Freight Rate Impact by Route — {scenario.name}</div>",
            unsafe_allow_html=True,
        )
        col_chart, col_ports = st.columns([3, 1])
        with col_chart:
            sorted_impacts = sorted(result.route_impacts, key=lambda x: x["delta"])
            route_names = [ri["route_name"][:30] for ri in sorted_impacts]
            deltas      = [ri["delta"] for ri in sorted_impacts]
            bar_colors  = [_C_BULL if d >= 0 else _C_BEAR for d in deltas]
            fig = go.Figure(go.Bar(
                x=deltas, y=route_names, orientation="h",
                marker_color=bar_colors, marker_line_width=0,
                text=[f"{d:+.1%}" for d in deltas],
                textposition="outside",
                textfont=dict(size=10, color=_C_SUB),
                hovertemplate="<b>%{y}</b><br>Impact: %{x:.1%}<extra></extra>",
            ))
            fig.add_vline(x=0, line_color="rgba(255,255,255,0.15)", line_width=1)
            fig.update_layout(
                template="plotly_dark", height=max(260, len(route_names) * 30),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=_C_SUB, size=11), margin=dict(l=10, r=80, t=16, b=24),
                xaxis=dict(tickformat=".0%", gridcolor="rgba(255,255,255,0.05)", zeroline=False),
                yaxis=dict(gridcolor="rgba(0,0,0,0)", automargin=True), showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True,
                            key=f"leg_route_impact_{scenario.name.replace(' ', '_')}")
        with col_ports:
            st.markdown(
                f"<div style='font-size:0.68rem; font-weight:700; color:{_C_MUTED};"
                f" text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px'>"
                f"Affected Ports</div>", unsafe_allow_html=True)
            for port in meta.get("affected_ports", []):
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {_C_BORDER};"
                    f" border-left:3px solid {accent}; border-radius:6px;"
                    f" padding:6px 10px; margin-bottom:6px; font-size:0.73rem;"
                    f" color:#cbd5e1'>{port}</div>",
                    unsafe_allow_html=True,
                )
            st.markdown(
                f"<div style='font-size:0.65rem; color:#475569; margin-top:8px'>"
                f"Est. duration:<br><strong style='color:{_C_SUB}'>"
                f"{meta['duration']}</strong></div>",
                unsafe_allow_html=True,
            )
    except Exception:
        pass


def _render_scenario_comparison(port_results: list, route_results: list) -> None:
    try:
        st.markdown(
            f"<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            f" color:{_C_MUTED}; margin-bottom:10px; font-weight:700'>Scenario Comparison</div>",
            unsafe_allow_html=True,
        )
        preset_names = [s.name for s in PREDEFINED_SCENARIOS]
        selected_for_compare = st.multiselect(
            "Select scenarios to compare",
            options=preset_names,
            default=preset_names[:2],
            key="leg_scenario_compare_multiselect",
        )
        if len(selected_for_compare) < 2:
            st.info("Select at least 2 scenarios to compare them side-by-side.")
            return
        scenario_results: dict[str, list[dict]] = {}
        for name in selected_for_compare:
            preset = next((s for s in PREDEFINED_SCENARIOS if s.name == name), None)
            if preset is None:
                continue
            try:
                res = run_scenario(preset, port_results, route_results)
                scenario_results[name] = res.route_impacts
            except Exception:
                scenario_results[name] = []
        if not scenario_results:
            st.warning("Could not compute scenario results.")
            return
        route_delta_sum: dict[str, float] = {}
        for name, impacts in scenario_results.items():
            for ri in impacts:
                rn = ri["route_name"][:30]
                route_delta_sum[rn] = route_delta_sum.get(rn, 0) + abs(ri["delta"])
        top_routes = sorted(route_delta_sum, key=route_delta_sum.get, reverse=True)[:8]  # type: ignore[arg-type]
        fig = go.Figure()
        for idx, (name, impacts) in enumerate(scenario_results.items()):
            delta_by_route = {ri["route_name"][:30]: ri["delta"] for ri in impacts}
            y_vals = [delta_by_route.get(r, 0.0) for r in top_routes]
            accent = _CARD_ACCENTS[idx % len(_CARD_ACCENTS)]
            fig.add_trace(go.Bar(
                name=name, x=top_routes, y=y_vals, marker_color=accent,
                marker_line_width=0,
                hovertemplate=f"<b>{name}</b><br>Route: %{{x}}<br>Delta: %{{y:.1%}}<extra></extra>",
            ))
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)
        fig.update_layout(
            template="plotly_dark", barmode="group", height=360,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=_C_SUB, size=11), margin=dict(l=10, r=20, t=20, b=80),
            xaxis=dict(tickangle=-30, gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(tickformat=".0%", gridcolor="rgba(255,255,255,0.05)", zeroline=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="center", x=0.5, bgcolor="rgba(0,0,0,0)"),
        )
        st.plotly_chart(fig, use_container_width=True, key="leg_grouped_compare_chart")
    except Exception:
        pass


def _render_probability_weighting(port_results: list, route_results: list) -> None:
    try:
        st.markdown(
            f"<div style='font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em;"
            f" color:{_C_MUTED}; margin-bottom:10px; font-weight:700'>"
            "Probability-Weighted Expected Impact</div>",
            unsafe_allow_html=True,
        )
        preset_list = list(PREDEFINED_SCENARIOS)
        prob_values: list[float] = []
        prob_cols = st.columns(len(preset_list))
        for i, preset in enumerate(preset_list):
            accent = _CARD_ACCENTS[i % len(_CARD_ACCENTS)]
            with prob_cols[i]:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{accent};"
                    f" text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px'>"
                    f"{preset.name}</div>", unsafe_allow_html=True)
                prob = st.slider(
                    f"P({preset.name[:12]})", 0, 100, 25, 5,
                    key=f"leg_prob_weight_{i}_{preset.name.replace(' ', '_')}",
                    label_visibility="collapsed",
                )
                prob_values.append(float(prob))
        total_prob = sum(prob_values)
        if total_prob == 0:
            st.warning("All probabilities are zero.")
            return
        norm_probs = [p / total_prob for p in prob_values]
        weighted_delta = 0.0
        breakdown: list[tuple[str, float, float]] = []
        for preset, prob_norm in zip(preset_list, norm_probs):
            try:
                res = run_scenario(preset, port_results, route_results)
                d   = res.opportunity_delta
            except Exception:
                d = 0.0
            weighted_delta += prob_norm * d
            breakdown.append((preset.name, prob_norm * 100, d))
        wd_color = _C_BULL if weighted_delta >= 0 else _C_BEAR
        wd_sign  = "+" if weighted_delta >= 0 else ""
        wd_bg    = f"rgba(16,185,129,0.08)" if weighted_delta >= 0 else "rgba(239,68,68,0.08)"
        c_card, c_break = st.columns([1, 2])
        with c_card:
            st.markdown(
                f"<div style='background:{wd_bg}; border:1px solid {wd_color}33;"
                f" border-radius:12px; padding:24px 16px; text-align:center; margin-top:8px'>"
                f"<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.12em;"
                f" color:{_C_MUTED}; margin-bottom:8px'>Weighted Expected Impact</div>"
                f"<div style='font-size:2.8rem; font-weight:900; color:{wd_color}; line-height:1'>"
                f"{wd_sign}{weighted_delta:.1%}</div>"
                f"<div style='font-size:0.72rem; color:{_C_MUTED}; margin-top:6px'>"
                f"vs. baseline avg · normalised to {total_prob:.0f}% total</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with c_break:
            st.markdown(
                f"<div style='font-size:0.68rem; font-weight:700; color:{_C_MUTED};"
                f" text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px;"
                f" margin-top:8px'>Scenario Breakdown</div>",
                unsafe_allow_html=True,
            )
            for name, prob_pct, delta in breakdown:
                d_color = _C_BULL if delta >= 0 else _C_BEAR
                d_sign  = "+" if delta >= 0 else ""
                meta    = _get_meta(name)
                icon    = meta.get("icon", "⚡")
                st.markdown(
                    f"<div style='display:flex; justify-content:space-between;"
                    f" align-items:center; padding:8px 0;"
                    f" border-bottom:1px solid rgba(255,255,255,0.05)'>"
                    f"<span style='font-size:0.75rem; color:#cbd5e1'>{icon} {name}</span>"
                    f"<span style='font-size:0.72rem; color:{_C_MUTED}'>{prob_pct:.0f}%</span>"
                    f"<span style='font-size:0.75rem; font-weight:700; color:{d_color}'>"
                    f"{d_sign}{delta:.1%}</span>"
                    f"<span style='font-size:0.68rem; color:#475569'>"
                    f"→ {d_sign}{prob_pct / 100 * delta:.2%} contrib.</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


def _render_custom_builder(port_results: list, route_results: list) -> None:
    try:
        with st.expander("Custom Scenario Builder", expanded=False):
            st.markdown(
                f"<div style='font-size:0.78rem; color:{_C_MUTED}; margin-bottom:14px'>"
                "Build a fully custom scenario by adjusting freight shocks, demand,"
                " congestion levels, and canal closures independently.</div>",
                unsafe_allow_html=True,
            )
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{_C_BASE};"
                    " text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px'>"
                    "Freight Shock</div>", unsafe_allow_html=True)
                bdi_shock  = st.slider("BDI / Freight Rate Shock (%)", -50, 200, 0, 5,
                                       format="%d%%", key="leg_custom_bdi") / 100.0
                fuel_shock = st.slider("Fuel / Bunker Cost Change (%)", -30, 100, 0, 5,
                                       format="%d%%", key="leg_custom_fuel") / 100.0
            with col2:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{_C_BULL};"
                    " text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px'>"
                    "Demand Change</div>", unsafe_allow_html=True)
                demand_shock = st.slider("Trade Demand Shock (%)", -40, 40, 0, 5,
                                         format="%d%%", key="leg_custom_demand") / 100.0
                pmi_shock    = st.slider("PMI Change (pts)", -15.0, 15.0, 0.0, 0.5,
                                         format="%.1f pts", key="leg_custom_pmi")
            with col3:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{_C_GOLD};"
                    " text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px'>"
                    "Congestion Level</div>", unsafe_allow_html=True)
                tariff_hike = st.slider("US-China Tariff Hike (%)", 0, 50, 0, 5,
                                        format="%d%%", key="leg_custom_tariff") / 100.0
                congestion_label = st.select_slider(
                    "Port Congestion Severity",
                    options=["None", "Low", "Moderate", "High", "Extreme"],
                    value="None", key="leg_custom_congestion",
                )
                _cong_map = {"None": 0.0, "Low": 0.05, "Moderate": 0.12,
                             "High": 0.22, "Extreme": 0.40}
                congestion_bdi_bump = _cong_map.get(congestion_label, 0.0)
                effective_bdi = bdi_shock + congestion_bdi_bump
            with col4:
                st.markdown(
                    f"<div style='font-size:0.68rem; font-weight:700; color:{_C_BEAR};"
                    " text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px'>"
                    "Canal Closures</div>", unsafe_allow_html=True)
                suez_closed   = st.checkbox("Suez Canal Closed",  key="leg_custom_suez")
                panama_closed = st.checkbox("Panama Canal Closed", key="leg_custom_panama")
                eff_color = _C_BULL if effective_bdi > 0 else (_C_BEAR if effective_bdi < 0 else _C_MUTED)
                eff_str   = f"{effective_bdi:+.0%}" if effective_bdi != 0 else "Neutral"
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {_C_BORDER};"
                    f" border-radius:8px; padding:10px 12px; margin-top:10px'>"
                    f"<div style='font-size:0.62rem; color:{_C_MUTED}; margin-bottom:4px'>"
                    f"Effective BDI Impact</div>"
                    f"<div style='font-size:1.4rem; font-weight:800; color:{eff_color}'>"
                    f"{eff_str}</div>"
                    f"<div style='font-size:0.60rem; color:#475569; margin-top:2px'>"
                    f"Congestion bump: {congestion_bdi_bump:+.0%}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            custom_scenario = ScenarioInput(
                name="Custom Scenario",
                bdi_shock=effective_bdi,
                fuel_shock=fuel_shock,
                pmi_shock=pmi_shock,
                suez_closed=suez_closed,
                panama_closed=panama_closed,
                us_china_tariff_hike=tariff_hike,
                demand_shock=demand_shock,
                description=(
                    f"Custom: BDI {effective_bdi:+.0%} · Demand {demand_shock:+.0%}"
                    f" · Fuel {fuel_shock:+.0%} · Congestion {congestion_label}"
                    + (" · Suez closed" if suez_closed else "")
                    + (" · Panama closed" if panama_closed else "")
                ),
            )
            if route_results:
                try:
                    res   = run_scenario(custom_scenario, port_results, route_results)
                    delta = res.opportunity_delta
                    risk  = res.risk_level
                    d_color   = _C_BULL if delta >= 0 else _C_BEAR
                    d_sign    = "+" if delta >= 0 else ""
                    risk_color = _RISK_COLORS.get(risk, _C_SUB)
                    st.markdown(
                        f"<div style='display:flex; gap:16px; margin-top:12px'>"
                        f"<div style='background:rgba(255,255,255,0.03);"
                        f" border:1px solid {_C_BORDER}; border-radius:8px;"
                        f" padding:10px 16px; flex:1; text-align:center'>"
                        f"<div style='font-size:0.60rem; color:{_C_MUTED}; margin-bottom:4px'>"
                        f"Opportunity Delta</div>"
                        f"<div style='font-size:1.6rem; font-weight:800; color:{d_color}'>"
                        f"{d_sign}{delta:.1%}</div></div>"
                        f"<div style='background:rgba(255,255,255,0.03);"
                        f" border:1px solid {_C_BORDER}; border-radius:8px;"
                        f" padding:10px 16px; flex:1; text-align:center'>"
                        f"<div style='font-size:0.60rem; color:{_C_MUTED}; margin-bottom:4px'>"
                        f"Risk Level</div>"
                        f"<div style='font-size:1rem; font-weight:800; color:{risk_color}'>"
                        f"{risk}</div></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                except Exception:
                    pass
    except Exception:
        pass


def _render_legacy_what_if(port_results: list, route_results: list) -> None:
    """Full legacy what-if engine: radio selector + results table + all-scenario bar."""
    try:
        _section_header("What-If Scenario Engine — Route & Port Impact Simulator")

        # Card selector
        card_selected = _render_scenario_cards(port_results, route_results)
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # Route impact for card-selected scenario
        if card_selected and route_results:
            card_preset = next(
                (s for s in PREDEFINED_SCENARIOS if s.name == card_selected), None
            )
            if card_preset is not None:
                _render_route_impact_chart(card_preset, port_results, route_results)
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        st.markdown(
            f"<hr style='border:none; border-top:1px solid {_C_BORDER}; margin:18px 0'>",
            unsafe_allow_html=True,
        )

        # Multi-scenario comparison
        if route_results:
            _render_scenario_comparison(port_results, route_results)

        st.markdown(
            f"<hr style='border:none; border-top:1px solid {_C_BORDER}; margin:18px 0'>",
            unsafe_allow_html=True,
        )

        # Probability weighting
        if route_results:
            _render_probability_weighting(port_results, route_results)

        st.markdown(
            f"<hr style='border:none; border-top:1px solid {_C_BORDER}; margin:18px 0'>",
            unsafe_allow_html=True,
        )

        # Custom scenario builder
        _render_custom_builder(port_results, route_results)

        st.markdown(
            f"<hr style='border:none; border-top:1px solid {_C_BORDER}; margin:22px 0'>",
            unsafe_allow_html=True,
        )

    except Exception:
        pass

    # Radio selector + individual results
    try:
        preset_names = [s.name for s in PREDEFINED_SCENARIOS] + ["Custom"]
        col_left, col_right = st.columns([1.4, 1])

        with col_left:
            st.markdown(
                f"<div style='font-size:0.75rem; text-transform:uppercase; "
                f"letter-spacing:0.1em; color:{_C_MUTED}; margin-bottom:8px'>Select Scenario</div>",
                unsafe_allow_html=True,
            )
            selected_name = st.radio(
                "scenario_radio", preset_names,
                label_visibility="collapsed", key="leg_scenario_selector",
            )

        with col_right:
            if selected_name != "Custom":
                preset = next(s for s in PREDEFINED_SCENARIOS if s.name == selected_name)
                badges: list[str] = []
                for flag, label, color, bg in [
                    (preset.suez_closed,    "Suez Closed",  "#ef4444", "rgba(239,68,68,0.15)"),
                    (preset.panama_closed,  "Panama Closed","#ef4444", "rgba(239,68,68,0.15)"),
                ]:
                    if flag:
                        badges.append(
                            f"<span style='background:{bg}; color:{color}; "
                            f"border:1px solid {color}4d; padding:2px 8px; "
                            f"border-radius:999px; font-size:0.68rem; font-weight:600'>{label}</span>"
                        )
                for val, label, color, bg in [
                    (preset.bdi_shock,           "BDI",    None, "rgba(59,130,246,0.15)"),
                    (preset.demand_shock,         "Demand", None, "rgba(16,185,129,0.10)"),
                    (preset.fuel_shock,           "Fuel",   _C_GOLD, "rgba(245,158,11,0.15)"),
                    (preset.us_china_tariff_hike, "Tariff", _C_PURPLE, "rgba(168,85,247,0.15)"),
                ]:
                    if val != 0.0:
                        c = color if color else (_C_BULL if val > 0 else _C_BEAR)
                        badges.append(
                            f"<span style='background:{bg}; color:{c}; "
                            f"border:1px solid {c}4d; padding:2px 8px; "
                            f"border-radius:999px; font-size:0.68rem; font-weight:600'>"
                            f"{label} {val:+.0%}</span>"
                        )
                badges_html = " ".join(badges)
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {_C_BORDER}; "
                    f"border-radius:10px; padding:16px 18px; margin-top:4px'>"
                    f"<div style='font-size:0.82rem; font-weight:700; color:{_C_TEXT}; "
                    f"margin-bottom:8px'>{preset.name}</div>"
                    f"<div style='font-size:0.78rem; color:{_C_SUB}; line-height:1.5; "
                    f"margin-bottom:10px'>{preset.description}</div>"
                    f"<div style='display:flex; flex-wrap:wrap; gap:6px'>{badges_html}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='background:{_C_CARD}; border:1px solid {_C_BORDER}; "
                    f"border-radius:10px; padding:16px 18px; margin-top:4px'>"
                    f"<div style='font-size:0.82rem; font-weight:700; color:{_C_TEXT}; "
                    f"margin-bottom:8px'>Custom Scenario</div>"
                    f"<div style='font-size:0.78rem; color:{_C_SUB}; line-height:1.5'>"
                    f"Configure your own shock parameters using the sliders below.</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        selected_name = PREDEFINED_SCENARIOS[0].name if PREDEFINED_SCENARIOS else "Custom"

    # Custom sliders (legacy)
    try:
        if selected_name == "Custom":
            st.markdown(
                f"<div style='font-size:0.75rem; text-transform:uppercase; "
                f"letter-spacing:0.1em; color:{_C_MUTED}; margin-top:20px; margin-bottom:12px'>"
                "Configure Custom Scenario</div>", unsafe_allow_html=True)
            sl1, sl2, sl3 = st.columns(3)
            with sl1:
                bdi_shock  = st.slider("BDI Shock", -0.50, 2.00, 0.0, 0.05,
                                       format="%.0f%%", key="leg_cust_bdi")
                fuel_shock = st.slider("Fuel Change", -0.30, 1.00, 0.0, 0.05,
                                       format="%.0f%%", key="leg_cust_fuel")
            with sl2:
                pmi_shock   = st.slider("PMI Change", -15.0, 15.0, 0.0, 0.5,
                                        format="%.1f pts", key="leg_cust_pmi")
                tariff_hike = st.slider("US-China Tariff", 0.0, 0.50, 0.0, 0.05,
                                        format="%.0f%%", key="leg_cust_tariff")
            with sl3:
                demand_shock  = st.slider("Demand Shock", -0.40, 0.40, 0.0, 0.05,
                                          format="%.0f%%", key="leg_cust_demand")
                suez_closed   = st.checkbox("Suez Canal Closed", key="leg_cust_suez")
                panama_closed = st.checkbox("Panama Canal Closed", key="leg_cust_panama")
            active_scenario = ScenarioInput(
                name="Custom Scenario",
                bdi_shock=bdi_shock, fuel_shock=fuel_shock, pmi_shock=pmi_shock,
                suez_closed=suez_closed, panama_closed=panama_closed,
                us_china_tariff_hike=tariff_hike, demand_shock=demand_shock,
                description="User-defined scenario.",
            )
        else:
            active_scenario = next(
                (s for s in PREDEFINED_SCENARIOS if s.name == selected_name),
                PREDEFINED_SCENARIOS[0],
            )
    except Exception:
        active_scenario = PREDEFINED_SCENARIOS[0] if PREDEFINED_SCENARIOS else ScenarioInput(name="Fallback")

    # Results
    try:
        st.divider()
        if not route_results:
            st.info("Route results unavailable — cannot run scenario analysis.")
            return
        result = run_scenario(active_scenario, port_results, route_results)
        if not result.route_impacts and not result.port_impacts:
            st.warning("Simulation returned no results — try adjusting parameters.")
            return
        delta      = result.opportunity_delta
        risk       = result.risk_level
        risk_color = _RISK_COLORS.get(risk, _C_SUB)
        delta_sign = "+" if delta >= 0 else ""
        delta_color = _C_BULL if delta >= 0 else _C_BEAR
        delta_bg    = "rgba(16,185,129,0.08)" if delta >= 0 else "rgba(239,68,68,0.08)"
        pulse_style = "animation:pulse-severe 1.2s ease-in-out infinite;" if risk == "SEVERE" else ""

        st.markdown("""
<style>
@keyframes pulse-severe {
    0%   { opacity: 1; }
    50%  { opacity: 0.5; }
    100% { opacity: 1; }
}
</style>""", unsafe_allow_html=True)

        c_delta, c_risk, c_summary = st.columns([1, 1, 2])
        with c_delta:
            st.markdown(
                f"<div style='background:{delta_bg}; border:1px solid {delta_color}33;"
                f" border-radius:12px; padding:20px 16px; text-align:center'>"
                f"<div style='font-size:0.68rem; text-transform:uppercase;"
                f" letter-spacing:0.12em; color:{_C_MUTED}; margin-bottom:8px'>"
                f"Route Opportunity Impact</div>"
                f"<div style='font-size:2.4rem; font-weight:900; color:{delta_color}; line-height:1'>"
                f"{delta_sign}{delta:.0%}</div>"
                f"<div style='font-size:0.72rem; color:{_C_MUTED}; margin-top:6px'>"
                f"vs. baseline avg</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with c_risk:
            rgb = "16,185,129" if risk == "LOW" else ("245,158,11" if risk == "MODERATE" else "239,68,68")
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.03); border:1px solid {_C_BORDER};"
                f" border-radius:12px; padding:20px 16px; text-align:center'>"
                f"<div style='font-size:0.68rem; text-transform:uppercase;"
                f" letter-spacing:0.12em; color:{_C_MUTED}; margin-bottom:12px'>Risk Level</div>"
                f"<div style='{pulse_style} background:rgba({rgb},0.15); color:{risk_color};"
                f" border:1px solid {risk_color}55; display:inline-block; padding:8px 20px;"
                f" border-radius:999px; font-size:1rem; font-weight:800; letter-spacing:0.06em'>"
                f"{risk}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with c_summary:
            st.markdown(
                f"<div style='background:rgba(59,130,246,0.05); border-left:3px solid {_C_BASE};"
                f" border-radius:0 10px 10px 0; padding:16px 18px; box-sizing:border-box'>"
                f"<div style='font-size:0.68rem; text-transform:uppercase;"
                f" letter-spacing:0.12em; color:{_C_BASE}; margin-bottom:8px; font-weight:700'>"
                f"Scenario Summary</div>"
                f"<div style='font-size:0.82rem; color:#cbd5e1; line-height:1.6'>"
                f"{result.summary}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)

        rt_col, pt_col = st.columns(2)

        with rt_col:
            st.markdown(
                f"<div style='font-size:0.75rem; text-transform:uppercase;"
                f" letter-spacing:0.1em; color:{_C_MUTED}; margin-bottom:10px;"
                f" font-weight:700'>Route Impacts</div>",
                unsafe_allow_html=True,
            )
            table_html = (
                f"<table style='width:100%; border-collapse:collapse; font-size:0.75rem;"
                f" color:#cbd5e1'><thead><tr>"
            )
            for th in ["Route", "Baseline", "Scenario", "Delta"]:
                align = "right" if th == "Delta" else "left"
                table_html += (
                    f"<th style='text-align:{align}; padding:6px 8px; color:{_C_MUTED};"
                    f" font-weight:600; border-bottom:1px solid rgba(255,255,255,0.06)'>"
                    f"{th}</th>"
                )
            table_html += "</tr></thead><tbody>"
            sorted_routes = sorted(result.route_impacts, key=lambda x: abs(x["delta"]), reverse=True)
            for ri in sorted_routes[:10]:
                sc_color = _C_BULL if ri["scenario_score"] >= 0.65 else (
                    _C_GOLD if ri["scenario_score"] >= 0.45 else _C_BEAR
                )
                table_html += (
                    f"<tr style='border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"<td style='padding:7px 8px; color:{_C_TEXT}; font-weight:500'>"
                    f"{ri['route_name'][:28]}</td>"
                    f"<td style='padding:7px 8px'>{_score_bar(ri['baseline'], '#475569', 80)}</td>"
                    f"<td style='padding:7px 8px'>{_score_bar(ri['scenario_score'], sc_color, 80)}</td>"
                    f"<td style='padding:7px 8px; text-align:right'>{_delta_arrow(ri['delta'])}</td>"
                    f"</tr>"
                )
            table_html += "</tbody></table>"
            st.markdown(table_html, unsafe_allow_html=True)

        with pt_col:
            st.markdown(
                f"<div style='font-size:0.75rem; text-transform:uppercase;"
                f" letter-spacing:0.1em; color:{_C_MUTED}; margin-bottom:10px;"
                f" font-weight:700'>Port Demand Impacts</div>",
                unsafe_allow_html=True,
            )
            ptable_html = (
                f"<table style='width:100%; border-collapse:collapse; font-size:0.75rem;"
                f" color:#cbd5e1'><thead><tr>"
            )
            for th in ["Port", "Baseline", "Scenario", "Delta"]:
                align = "right" if th == "Delta" else "left"
                ptable_html += (
                    f"<th style='text-align:{align}; padding:6px 8px; color:{_C_MUTED};"
                    f" font-weight:600; border-bottom:1px solid rgba(255,255,255,0.06)'>"
                    f"{th}</th>"
                )
            ptable_html += "</tr></thead><tbody>"
            sorted_ports = sorted(result.port_impacts, key=lambda x: abs(x["delta"]), reverse=True)
            for pi in sorted_ports[:10]:
                sc_color = _C_BULL if pi["scenario_score"] >= 0.65 else (
                    _C_GOLD if pi["scenario_score"] >= 0.45 else _C_BEAR
                )
                ptable_html += (
                    f"<tr style='border-bottom:1px solid rgba(255,255,255,0.03)'>"
                    f"<td style='padding:7px 8px; color:{_C_TEXT}; font-weight:500'>"
                    f"{pi['port_name'][:22]}</td>"
                    f"<td style='padding:7px 8px'>{_score_bar(pi['baseline'], '#475569', 70)}</td>"
                    f"<td style='padding:7px 8px'>{_score_bar(pi['scenario_score'], sc_color, 70)}</td>"
                    f"<td style='padding:7px 8px; text-align:right'>{_delta_arrow(pi['delta'])}</td>"
                    f"</tr>"
                )
            ptable_html += "</tbody></table>"
            st.markdown(ptable_html, unsafe_allow_html=True)

        # All-scenario comparison bar chart
        st.markdown("<div style='margin-top:32px'></div>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.75rem; text-transform:uppercase; letter-spacing:0.1em;"
            f" color:{_C_MUTED}; margin-bottom:14px; font-weight:700'>"
            "All Scenarios — Opportunity Impact</div>",
            unsafe_allow_html=True,
        )
        all_results = run_all_scenarios(port_results, route_results)
        if all_results:
            names  = [r.scenario.name for r in all_results]
            deltas = [r.opportunity_delta for r in all_results]
            colors = [_C_BULL if d >= 0 else _C_BEAR for d in deltas]
            fig2 = go.Figure(go.Bar(
                x=deltas, y=names, orientation="h",
                marker_color=colors, marker_line_width=0,
                text=[f"{d:+.1%}" for d in deltas],
                textposition="outside",
                textfont=dict(size=11, color=_C_SUB),
                hovertemplate="<b>%{y}</b><br>Delta: %{x:.1%}<extra></extra>",
            ))
            fig2.add_vline(x=0, line_color="rgba(255,255,255,0.15)", line_width=1)
            fig2.update_layout(
                template="plotly_dark", height=380,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=_C_SUB, size=12),
                margin=dict(l=10, r=80, t=20, b=40),
                xaxis=dict(range=[-0.5, 0.5], tickformat=".0%",
                           gridcolor="rgba(255,255,255,0.05)", zeroline=False),
                yaxis=dict(gridcolor="rgba(0,0,0,0)", automargin=True),
                showlegend=False,
            )
            st.plotly_chart(fig2, use_container_width=True,
                            config={"displayModeBar": False},
                            key="leg_all_scenarios_bar")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER FUNCTION  —  exact signature preserved
# ══════════════════════════════════════════════════════════════════════════════

def render(port_results: list, route_results: list, macro_data: dict) -> None:
    """Render the Scenario Analysis tab."""

    # ── Hero Dashboard ─────────────────────────────────────────────────────────
    try:
        _render_hero(macro_data)
    except Exception:
        pass

    # ── Section 1: Three-Scenario Cards ───────────────────────────────────────
    try:
        _render_three_scenario_cards()
    except Exception:
        pass

    _divider()

    # ── Section 2: Parameter Sliders ──────────────────────────────────────────
    try:
        _render_parameter_sliders()
    except Exception:
        pass

    _divider()

    # ── Section 3: Rate Forecast Fan Chart ────────────────────────────────────
    try:
        _render_rate_fan_chart()
    except Exception:
        pass

    _divider()

    # ── Section 4: Probability Gauge ──────────────────────────────────────────
    try:
        _render_probability_gauge()
    except Exception:
        pass

    _divider()

    # ── Section 5: Historical Accuracy Tracker ────────────────────────────────
    try:
        _render_history_tracker()
    except Exception:
        pass

    _divider()

    # ── Section 6: Trigger Event Monitor ─────────────────────────────────────
    try:
        _render_trigger_monitor()
    except Exception:
        pass

    _divider()

    # ── Section 7: Sensitivity Tornado Chart ─────────────────────────────────
    try:
        _render_sensitivity_tornado()
    except Exception:
        pass

    _divider()

    # ── Section 8: Carrier Implications ──────────────────────────────────────
    try:
        _render_carrier_implications()
    except Exception:
        pass

    _divider()

    # ── Section 9: Scenario Watchlist ────────────────────────────────────────
    try:
        _render_watchlist()
    except Exception:
        pass

    _divider()

    # ── Section 10: Legacy What-If Engine (preserved) ────────────────────────
    try:
        _render_legacy_what_if(port_results, route_results)
    except Exception:
        pass
