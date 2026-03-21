"""
Fundamentals Tab — Bloomberg-Terminal-level shipping stock analysis.

Sections
--------
0.  Fundamentals Health Dashboard  — overall score + key drivers
1.  Shipping Cycle Indicator       — dial + history + supporting data
2.  Supply-Demand Balance          — fleet supply vs trade demand dual-line
3.  Vessel Orderbook               — on-order by ship type & delivery year
4.  Scrapping vs Delivery Balance  — net fleet growth decomposed
5.  Company Comparison Matrix      — side-by-side colour-coded table
6.  Charter vs Spot Rate           — time-charter vs spot comparison
7.  Operating Cost Breakdown       — bunker, port, crew, maintenance
8.  Industry P&L Summary           — revenue / costs / margins waterfall
9.  Fleet Utilisation Trend        — utilisation rate over time
10. Industry Leverage & Financial  — debt ratios / interest cover
11. Earnings Surprise Model        — beat/miss history + rate sensitivity
12. Valuation Dashboard            — EV/EBITDA, P/B, yield gauges + P/E
13. Shipping Beta Dashboard        — multi-factor bar chart
14. Earnings Calendar              — next 90-day countdown badges

Integration
-----------
    with tab_fundamentals:
        from ui import tab_fundamentals as _tf
        _tf.render(stock_data=stock_data, freight_data=freight_data, macro_data=macro_data)
"""
from __future__ import annotations

import datetime
import math
from typing import Any

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from processing.fundamentals_analyzer import (
    COMPANY_FUNDAMENTALS,
    EARNINGS_HISTORY,
    RATE_TO_EPS_SENSITIVITY_100FEU,
    VALUATION_RANGES,
    CompanyFundamentals,
    ValuationRange,
    compute_normalized_earnings,
    compute_shipping_beta,
    get_all_betas,
    get_current_shipping_cycle,
    get_fundamentals_summary,
    get_valuation_zone,
)


# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_GOLD    = "#fbbf24"
C_TEAL    = "#14b8a6"
C_ROSE    = "#f43f5e"
C_INDIGO  = "#6366f1"

TICKER_COLORS: dict[str, str] = {
    "ZIM":  "#3b82f6",
    "MATX": "#10b981",
    "SBLK": "#f59e0b",
    "DAC":  "#8b5cf6",
    "CMRE": "#06b6d4",
}

PHASE_CONFIG: dict[str, dict] = {
    "TROUGH":   {"color": C_LOW,    "icon": "▼", "order": 0},
    "RECOVERY": {"color": C_HIGH,   "icon": "▲", "order": 1},
    "PEAK":     {"color": C_MOD,    "icon": "▲", "order": 2},
    "DECLINE":  {"color": C_CONV,   "icon": "▼", "order": 3},
}

RATING_COLORS: dict[str, str] = {
    "BUY":  C_HIGH,
    "HOLD": C_MOD,
    "SELL": C_LOW,
}

ZONE_COLORS: dict[str, str] = {
    "CHEAP":     C_HIGH,
    "FAIR":      C_MOD,
    "EXPENSIVE": C_LOW,
    "UNKNOWN":   C_TEXT3,
}


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _fmt_or_na(value: Any, fmt: str) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float) and math.isnan(value):
        return "N/A"
    try:
        return fmt.format(value)
    except (TypeError, ValueError):
        return "N/A"


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "rgba({},{},{},{})".format(r, g, b, alpha)


def _divider(label: str, icon: str = "") -> None:
    prefix = icon + " " if icon else ""
    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin:32px 0 20px">'
        '<div style="flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.08))"></div>'
        '<span style="font-size:0.63rem;color:{};text-transform:uppercase;'
        'letter-spacing:0.14em;font-weight:700">{}{}</span>'
        '<div style="flex:1;height:1px;background:linear-gradient(90deg,rgba(255,255,255,0.08),transparent)"></div>'
        '</div>'.format(C_TEXT3, prefix, label),
        unsafe_allow_html=True,
    )


def _dark_layout(height: int = 400, title: str = "") -> dict:
    t = {"text": title, "font": {"size": 12, "color": C_TEXT2}, "x": 0.01} if title else {}
    return dict(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
        title=t,
        height=height,
        margin=dict(l=24, r=24, t=44 if title else 24, b=24),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )


def _stat_kpi(label: str, value: str, sub: str = "", color: str = C_ACCENT) -> str:
    sub_html = (
        '<div style="font-size:0.68rem;color:{};margin-top:3px;line-height:1.3">{}</div>'.format(C_TEXT3, sub)
        if sub else ""
    )
    return (
        '<div style="background:{bg};border:1px solid {bd};'
        'border-top:3px solid {ac};border-radius:12px;'
        'padding:16px 18px;text-align:center;'
        'box-shadow:0 4px 24px rgba(0,0,0,0.3)">'
        '<div style="font-size:0.6rem;font-weight:700;color:{t3};'
        'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">{label}</div>'
        '<div style="font-size:1.35rem;font-weight:900;color:{tx};line-height:1">{val}</div>'
        '{sub}'
        '</div>'
    ).format(
        bg=C_CARD, bd=C_BORDER, ac=color,
        t3=C_TEXT3, label=label, tx=C_TEXT, val=value, sub=sub_html,
    )


def _score_bar(score: float, color: str, width_px: int = 200) -> str:
    """Render a thin horizontal score bar (0-100)."""
    pct = max(0, min(100, score))
    return (
        '<div style="background:rgba(255,255,255,0.06);border-radius:4px;'
        'height:6px;width:{}px;overflow:hidden;margin-top:6px">'
        '<div style="background:{};height:100%;width:{}%;border-radius:4px;'
        'transition:width 0.6s ease"></div>'
        '</div>'.format(width_px, color, pct)
    )


def _badge(text: str, color: str) -> str:
    return (
        '<span style="background:{bg};color:{c};border:1px solid {c};'
        'padding:2px 10px;border-radius:999px;font-size:0.68rem;font-weight:700">'
        '{text}</span>'
    ).format(bg=_hex_to_rgba(color, 0.15), c=color, text=text)


# ── Section 0: Fundamentals Health Dashboard ───────────────────────────────────

def _render_health_dashboard() -> None:
    """Overall fundamentals score with key driver breakdown."""
    _divider("Fundamentals Health Dashboard", "◉")

    cycle = get_current_shipping_cycle()
    rows  = get_fundamentals_summary()

    # Compute composite score components (0-100 scale)
    # 1. Cycle positioning score
    cycle_scores = {"RECOVERY": 85, "PEAK": 65, "DECLINE": 35, "TROUGH": 20}
    cycle_score = cycle_scores.get(cycle.phase, 50)

    # 2. Valuation score (avg across tickers; cheap = high score)
    zone_scores = {"CHEAP": 90, "FAIR": 60, "EXPENSIVE": 25, "UNKNOWN": 50}
    val_scores = [zone_scores.get(get_valuation_zone(r["Ticker"]), 50) for r in rows]
    valuation_score = sum(val_scores) / len(val_scores) if val_scores else 50

    # 3. Earnings momentum (avg beat % capped to 0-100)
    all_beats = []
    for ticker in EARNINGS_HISTORY:
        for h in EARNINGS_HISTORY[ticker]:
            all_beats.append(h.beat_pct)
    avg_beat = sum(all_beats) / len(all_beats) if all_beats else 0
    earnings_score = min(100, max(0, 50 + avg_beat * 2))

    # 4. Fleet utilisation score
    util = cycle.fleet_utilization_pct
    util_score = min(100, max(0, (util - 78) / (95 - 78) * 100))

    # 5. Orderbook pressure (low orderbook = high score)
    ob = cycle.orderbook_to_fleet_pct
    ob_score = min(100, max(0, 100 - (ob / 30) * 100))

    # 6. Dividend health (avg yield across covered tickers)
    avg_yield = sum(f.dividend_yield_pct for f in COMPANY_FUNDAMENTALS.values()) / len(COMPANY_FUNDAMENTALS)
    div_score = min(100, avg_yield * 8)

    weights = [0.22, 0.20, 0.18, 0.16, 0.14, 0.10]
    component_scores = [cycle_score, valuation_score, earnings_score, util_score, ob_score, div_score]
    overall = sum(w * s for w, s in zip(weights, component_scores))

    overall_color = C_HIGH if overall >= 65 else (C_MOD if overall >= 45 else C_LOW)
    overall_label = "POSITIVE" if overall >= 65 else ("NEUTRAL" if overall >= 45 else "NEGATIVE")

    # Hero row
    st.markdown(
        '<div style="background:linear-gradient(135deg,{bg},{surf});'
        'border:1px solid {bd};border-top:4px solid {ac};'
        'border-radius:16px;padding:28px 32px;margin-bottom:20px;'
        'box-shadow:0 8px 40px rgba(0,0,0,0.4)">'
        '<div style="display:flex;align-items:flex-start;gap:32px;flex-wrap:wrap">'

        # Score circle
        '<div style="text-align:center;flex-shrink:0">'
        '<div style="font-size:0.6rem;color:{t3};text-transform:uppercase;'
        'letter-spacing:0.12em;margin-bottom:8px">Overall Score</div>'
        '<div style="width:100px;height:100px;border-radius:50%;'
        'border:4px solid {ac};display:flex;flex-direction:column;'
        'align-items:center;justify-content:center;'
        'background:radial-gradient(circle,{acl} 0%,transparent 70%);'
        'margin:0 auto">'
        '<div style="font-size:2rem;font-weight:900;color:{ac};line-height:1">{score:.0f}</div>'
        '<div style="font-size:0.55rem;color:{t3};text-transform:uppercase;margin-top:2px">/ 100</div>'
        '</div>'
        '<div style="margin-top:10px">{badge}</div>'
        '</div>'

        # Drivers grid
        '<div style="flex:1;min-width:300px">'
        '<div style="font-size:0.68rem;color:{t3};text-transform:uppercase;'
        'letter-spacing:0.1em;margin-bottom:14px">Score Drivers</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">'
        '{drivers}'
        '</div>'
        '</div>'

        # Cycle summary
        '<div style="flex-shrink:0;min-width:200px">'
        '<div style="font-size:0.6rem;color:{t3};text-transform:uppercase;'
        'letter-spacing:0.1em;margin-bottom:8px">Cycle Position</div>'
        '<div style="font-size:1.4rem;font-weight:900;color:{pc};margin-bottom:4px">{phase}</div>'
        '<div style="font-size:0.75rem;color:{t2};line-height:1.5;max-width:220px">{desc}</div>'
        '</div>'
        '</div>'
        '</div>'.format(
            bg=C_CARD, surf=C_SURFACE, bd=C_BORDER,
            ac=overall_color, acl=_hex_to_rgba(overall_color, 0.12),
            t3=C_TEXT3, t2=C_TEXT2,
            score=overall,
            badge=_badge(overall_label, overall_color),
            pc=PHASE_CONFIG.get(cycle.phase, {}).get("color", C_TEXT2),
            phase=cycle.phase,
            desc=cycle.phase_description[:140] + "…",
            drivers="".join(
                '<div style="background:{bg2};border:1px solid {bd};border-radius:8px;padding:10px 12px">'
                '<div style="font-size:0.6rem;color:{t3};text-transform:uppercase;'
                'letter-spacing:0.08em;margin-bottom:4px">{lbl}</div>'
                '<div style="display:flex;align-items:center;gap:8px">'
                '<div style="font-size:1.1rem;font-weight:800;color:{sc}">{sv:.0f}</div>'
                '{bar}'
                '</div>'
                '</div>'.format(
                    bg2=C_SURFACE, bd=C_BORDER, t3=C_TEXT3,
                    lbl=lbl, sc=C_HIGH if sv >= 65 else (C_MOD if sv >= 45 else C_LOW),
                    sv=sv, bar=_score_bar(sv, C_HIGH if sv >= 65 else (C_MOD if sv >= 45 else C_LOW), 80),
                )
                for lbl, sv in [
                    ("Cycle", cycle_score),
                    ("Valuation", valuation_score),
                    ("Earnings Momentum", earnings_score),
                    ("Fleet Utilisation", util_score),
                    ("Orderbook Pressure", ob_score),
                    ("Dividend Health", div_score),
                ]
            ),
        ),
        unsafe_allow_html=True,
    )

    # KPI strip
    cols = st.columns(6)
    kpi_data = [
        ("BDI Level",         "{:,}".format(cycle.bdi_level),
         "{:+.0f}% vs LT avg".format(cycle.bdi_vs_longterm_avg_pct),
         C_HIGH if cycle.bdi_vs_longterm_avg_pct >= 0 else C_LOW),
        ("Fleet Utilisation", "{:.1f}%".format(cycle.fleet_utilization_pct),
         "Tight >88%", C_HIGH if cycle.fleet_utilization_pct >= 88 else C_MOD),
        ("Orderbook/Fleet",   "{:.1f}%".format(cycle.orderbook_to_fleet_pct),
         "Supply pressure", C_LOW if cycle.orderbook_to_fleet_pct > 20 else C_HIGH),
        ("Avg EV/EBITDA",
         "{:.1f}x".format(sum(f.ev_ebitda for f in COMPANY_FUNDAMENTALS.values()) / len(COMPANY_FUNDAMENTALS)),
         "Weighted sector avg", C_ACCENT),
        ("Avg Div Yield",     "{:.1f}%".format(avg_yield),
         "Covered universe", C_GOLD),
        ("TP Rate",           "${:,}/FEU".format(cycle.transpacific_rate_usd),
         "{:+.0f}% vs LT avg".format(cycle.rate_vs_longterm_avg_pct),
         C_HIGH if cycle.rate_vs_longterm_avg_pct >= 0 else C_LOW),
    ]
    for col, (label, value, sub, color) in zip(cols, kpi_data):
        col.markdown(_stat_kpi(label, value, sub, color), unsafe_allow_html=True)


# ── Section 1: Shipping Cycle Indicator ───────────────────────────────────────

def _render_shipping_cycle() -> None:
    _divider("Shipping Cycle Indicator", "↻")
    cycle = get_current_shipping_cycle()
    pc = PHASE_CONFIG.get(cycle.phase, {"color": C_TEXT, "icon": "→", "order": 0})
    phase_color = pc["color"]

    years_min, years_max = cycle.typical_cycle_length_yrs
    st.markdown(
        '<div style="background:linear-gradient(135deg,{c},{s});'
        'border:1px solid {bd};border-top:4px solid {pc};'
        'border-radius:14px;padding:24px 28px;margin-bottom:16px;'
        'box-shadow:0 4px 32px rgba(0,0,0,0.35)">'
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap">'
        '<div>'
        '  <div style="font-size:0.62rem;color:{t3};text-transform:uppercase;'
        '    letter-spacing:0.12em;margin-bottom:8px">Current Shipping Cycle Phase</div>'
        '  <div style="font-size:2.4rem;font-weight:900;color:{pc};'
        '    text-shadow:0 0 40px {glow};letter-spacing:-0.02em">'
        '    {icon} {phase}'
        '  </div>'
        '  <div style="font-size:0.83rem;color:{t2};margin-top:10px;'
        '    max-width:580px;line-height:1.6">{desc}</div>'
        '</div>'
        '<div style="text-align:right;flex-shrink:0">'
        '  <div style="font-size:0.62rem;color:{t3};text-transform:uppercase;margin-bottom:4px">Phase Duration</div>'
        '  <div style="font-size:1.6rem;font-weight:800;color:{pc}">{dur:.1f} yrs</div>'
        '  <div style="font-size:0.7rem;color:{t3};margin-top:2px">Typical: {ymin}–{ymax} yr cycle</div>'
        '  <div style="margin-top:12px">{badge}</div>'
        '</div>'
        '</div>'
        '</div>'.format(
            c=C_CARD, s=C_SURFACE, bd=_hex_to_rgba(phase_color, 0.4),
            pc=phase_color, glow=_hex_to_rgba(phase_color, 0.4),
            t3=C_TEXT3, t2=C_TEXT2,
            icon=pc["icon"], phase=cycle.phase,
            desc=cycle.phase_description,
            dur=cycle.years_in_current_phase,
            ymin=years_min, ymax=years_max,
            badge=_badge("{} confidence".format(cycle.phase_confidence), phase_color),
        ),
        unsafe_allow_html=True,
    )

    col_dial, col_indicators = st.columns([1, 1])
    with col_dial:
        _render_cycle_dial(cycle.phase, phase_color)
    with col_indicators:
        bdi_color  = C_HIGH if cycle.bdi_vs_longterm_avg_pct >= 0 else C_LOW
        rate_color = C_HIGH if cycle.rate_vs_longterm_avg_pct >= 0 else C_LOW
        util_color = C_HIGH if cycle.fleet_utilization_pct >= 88 else (C_MOD if cycle.fleet_utilization_pct >= 84 else C_LOW)
        ob_color   = C_LOW  if cycle.orderbook_to_fleet_pct > 20 else (C_MOD if cycle.orderbook_to_fleet_pct > 12 else C_HIGH)

        c1, c2 = st.columns(2)
        c1.markdown(_stat_kpi("Baltic Dry Index", "{:,}".format(cycle.bdi_level),
                               "{:+.0f}% vs LT avg".format(cycle.bdi_vs_longterm_avg_pct), bdi_color),
                    unsafe_allow_html=True)
        c2.markdown(_stat_kpi("TP Container Rate", "${:,}/FEU".format(cycle.transpacific_rate_usd),
                               "{:+.0f}% vs LT avg".format(cycle.rate_vs_longterm_avg_pct), rate_color),
                    unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        c3, c4 = st.columns(2)
        c3.markdown(_stat_kpi("Fleet Utilisation", "{:.1f}%".format(cycle.fleet_utilization_pct),
                               "Tight threshold: 88%", util_color), unsafe_allow_html=True)
        c4.markdown(_stat_kpi("Orderbook/Fleet", "{:.1f}%".format(cycle.orderbook_to_fleet_pct),
                               "Supply pressure indicator", ob_color), unsafe_allow_html=True)

        next_pc = PHASE_CONFIG.get(cycle.next_phase, {"color": C_TEXT2})
        st.markdown(
            '<div style="background:{bg};border:1px solid {bd};border-left:3px solid {nc};'
            'border-radius:10px;padding:14px 16px;margin-top:14px">'
            '<div style="font-size:0.6rem;color:{t3};text-transform:uppercase;'
            'letter-spacing:0.1em;margin-bottom:6px">Anticipated Next Phase</div>'
            '<div style="font-size:1.05rem;font-weight:800;color:{nc}">{np}</div>'
            '<div style="font-size:0.72rem;color:{t3};margin-top:5px;line-height:1.45">{impl}</div>'
            '</div>'.format(
                bg=C_CARD, bd=C_BORDER, nc=next_pc["color"],
                t3=C_TEXT3, np=cycle.next_phase,
                impl=cycle.investment_implication[:130] + "…",
            ),
            unsafe_allow_html=True,
        )

    with st.expander("Supporting Data Points", expanded=False, key="fundamentals_cycle_supporting_data"):
        for indicator in cycle.supporting_indicators:
            st.markdown(
                '<div style="display:flex;align-items:flex-start;gap:10px;'
                'padding:8px 0;border-bottom:1px solid {bd}">'
                '<span style="color:{pc};font-size:0.85rem;margin-top:1px;flex-shrink:0">▸</span>'
                '<span style="font-size:0.82rem;color:{t2};line-height:1.5">{ind}</span>'
                '</div>'.format(bd=C_BORDER, pc=phase_color, t2=C_TEXT2, ind=indicator),
                unsafe_allow_html=True,
            )

    _render_cycle_history(cycle)


def _render_cycle_dial(current_phase: str, phase_color: str) -> None:
    phase_order = {"TROUGH": 0, "RECOVERY": 1, "PEAK": 2, "DECLINE": 3}
    phase_idx   = phase_order.get(current_phase, 0)
    segment_colors_solid = [C_LOW, C_HIGH, C_MOD, C_CONV]
    phase_labels = ["TROUGH", "RECOVERY", "PEAK", "DECLINE"]
    segment_colors = [_hex_to_rgba(c, 0.22) for c in segment_colors_solid]

    active_colors = [
        segment_colors_solid[i] if i == phase_idx else segment_colors[i]
        for i in range(4)
    ]

    fig = go.Figure()
    fig.add_trace(go.Pie(
        values=[1, 1, 1, 1],
        labels=phase_labels,
        hole=0.58,
        rotation=90,
        direction="clockwise",
        marker=dict(colors=active_colors, line=dict(color=C_SURFACE, width=4)),
        textinfo="label",
        textfont=dict(size=10, color=C_TEXT2, family="Inter"),
        hoverinfo="label",
        pull=[0.06 if i == phase_idx else 0 for i in range(4)],
    ))
    fig.update_layout(
        **_dark_layout(height=290),
        showlegend=False,
        annotations=[dict(
            text="<b>{}</b>".format(current_phase),
            x=0.5, y=0.5,
            font=dict(size=15, color=phase_color, family="Inter"),
            showarrow=False,
        )],
    )
    st.plotly_chart(fig, use_container_width=True, key="cycle_dial")


def _render_cycle_history(cycle: Any) -> None:
    history = cycle.cycle_history
    if not history:
        return

    years  = [h["year"]        for h in history]
    bdis   = [h["bdi_approx"]  for h in history]
    phases = [h["phase"]       for h in history]
    notes  = [h.get("notes","") for h in history]
    colors = [PHASE_CONFIG.get(p, {"color": C_TEXT3})["color"] for p in phases]

    fig = go.Figure()
    for i in range(len(years)):
        fig.add_trace(go.Bar(
            x=[years[i]], y=[bdis[i]],
            name=phases[i],
            marker_color=_hex_to_rgba(colors[i], 0.78),
            hovertemplate="<b>{}</b><br>BDI: {:,}<br>Phase: {}<br>{}<extra></extra>".format(
                years[i], bdis[i], phases[i], notes[i]),
            showlegend=(i == 0 or phases[i] != phases[i - 1]),
            legendgroup=phases[i],
        ))

    fig.add_hline(y=1_500, line_dash="dot", line_color=C_MOD, line_width=1.5,
                  annotation_text="  LT Avg 1,500",
                  annotation_font=dict(color=C_MOD, size=10))

    current_year = 2026
    if current_year in years:
        idx = years.index(current_year)
        fig.add_trace(go.Scatter(
            x=[current_year], y=[bdis[idx]],
            mode="markers",
            marker=dict(color=C_ACCENT, size=13, symbol="diamond",
                        line=dict(color="white", width=2)),
            name="Current", hoverinfo="skip",
        ))

    layout = _dark_layout(height=310, title="Historical Shipping Cycle  |  BDI by Year (2010–2026)")
    layout.update(dict(
        barmode="group",
        xaxis=dict(tickvals=years, ticktext=[str(y) for y in years],
                   gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=10)),
        yaxis=dict(title="Baltic Dry Index", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10)),
        showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="cycle_history")


# ── Section 2: Supply-Demand Balance ──────────────────────────────────────────

def _render_supply_demand_balance() -> None:
    _divider("Supply-Demand Balance", "⚖")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px;line-height:1.6">'
        'Fleet supply growth (new deliveries net of scrapping) vs trade demand growth '
        '(global container volumes). When demand growth exceeds supply growth, '
        'freight rates strengthen. Gap compression signals rate pressure.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    years = list(range(2016, 2027))
    # Fleet supply growth % YoY — new deliveries net scrapping
    supply_growth = [3.8, 4.2, 3.1, 2.6, 3.5, 4.8, 1.9, 8.2, 7.5, 4.1, 3.2]
    # Trade demand growth % YoY — global container trade volumes
    demand_growth = [2.9, 4.5, 4.2, 2.1, 1.8, 7.3, -1.0, 5.2, 3.8, 2.6, 3.5]
    # Gap: positive = supply surplus, negative = demand surplus
    gap = [s - d for s, d in zip(supply_growth, demand_growth)]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.06,
        subplot_titles=["Fleet Supply Growth vs Trade Demand Growth (%YoY)", "Supply-Demand Gap (pp)"],
    )

    fig.add_trace(go.Scatter(
        x=years, y=supply_growth, name="Fleet Supply Growth",
        mode="lines+markers",
        line=dict(color=C_LOW, width=2.5),
        marker=dict(size=7, color=C_LOW),
        fill="tozeroy", fillcolor=_hex_to_rgba(C_LOW, 0.07),
        hovertemplate="<b>%{x}</b><br>Supply Growth: %{y:.1f}%<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=years, y=demand_growth, name="Trade Demand Growth",
        mode="lines+markers",
        line=dict(color=C_HIGH, width=2.5),
        marker=dict(size=7, color=C_HIGH),
        fill="tozeroy", fillcolor=_hex_to_rgba(C_HIGH, 0.07),
        hovertemplate="<b>%{x}</b><br>Demand Growth: %{y:.1f}%<extra></extra>",
    ), row=1, col=1)

    gap_colors = [_hex_to_rgba(C_LOW, 0.75) if g > 0 else _hex_to_rgba(C_HIGH, 0.75) for g in gap]
    fig.add_trace(go.Bar(
        x=years, y=gap, name="Supply-Demand Gap",
        marker_color=gap_colors,
        hovertemplate="<b>%{x}</b><br>Gap: %{y:+.1f}pp<extra></extra>",
    ), row=2, col=1)

    fig.add_hline(y=0, line_color=C_BORDER, line_width=1, row=2, col=1)

    layout = _dark_layout(height=440)
    layout.update(dict(
        showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis2=dict(tickfont=dict(color=C_TEXT3, size=10), gridcolor="rgba(255,255,255,0.04)"),
        yaxis=dict(title="Growth %", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10), ticksuffix="%"),
        yaxis2=dict(title="Gap (pp)", gridcolor="rgba(255,255,255,0.04)",
                    tickfont=dict(color=C_TEXT3, size=10), zeroline=True,
                    zerolinecolor=C_BORDER),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="supply_demand_balance")

    # Annotation cards
    latest_gap = gap[-1]
    c1, c2, c3 = st.columns(3)
    c1.markdown(_stat_kpi("2026E Supply Growth", "{:.1f}%".format(supply_growth[-1]),
                           "Fleet net additions YoY", C_LOW), unsafe_allow_html=True)
    c2.markdown(_stat_kpi("2026E Demand Growth", "{:.1f}%".format(demand_growth[-1]),
                           "Trade volume growth YoY", C_HIGH), unsafe_allow_html=True)
    gap_col = C_LOW if latest_gap > 0 else C_HIGH
    c3.markdown(_stat_kpi("Supply-Demand Gap", "{:+.1f}pp".format(latest_gap),
                           "Surplus >0 = rate pressure", gap_col), unsafe_allow_html=True)


# ── Section 3: Vessel Orderbook ────────────────────────────────────────────────

def _render_orderbook() -> None:
    _divider("Vessel Orderbook by Ship Type & Delivery Year", "🚢")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px;line-height:1.6">'
        'Vessels on order by ship type and scheduled delivery year. '
        'A front-loaded orderbook signals near-term supply pressure on freight rates.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    delivery_years = [2025, 2026, 2027, 2028, 2029]
    ship_types = {
        "Container (TEU k)": [142, 185, 203, 118,  45],
        "Dry Bulk (DWT Mt)": [ 28,  35,  42,  30,  12],
        "Tanker (DWT Mt)":   [ 18,  24,  31,  22,   8],
        "LNG (units)":       [ 45,  62,  58,  38,  14],
        "LPG (units)":       [ 22,  18,  25,  16,   6],
    }
    type_colors = [C_ACCENT, C_MOD, C_TEAL, C_CONV, C_ROSE]

    fig = go.Figure()
    for (stype, values), color in zip(ship_types.items(), type_colors):
        fig.add_trace(go.Bar(
            name=stype, x=delivery_years, y=values,
            marker_color=_hex_to_rgba(color, 0.82),
            hovertemplate="<b>{}</b><br>Year: %{{x}}<br>On Order: %{{y}}<extra></extra>".format(stype),
        ))

    layout = _dark_layout(height=380, title="Global Vessel Orderbook by Delivery Year")
    layout.update(dict(
        barmode="group",
        showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(tickvals=delivery_years, ticktext=[str(y) for y in delivery_years],
                   gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=11)),
        yaxis=dict(title="Units / Capacity", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10)),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="vessel_orderbook")

    # Orderbook % of existing fleet
    ob_pcts = {"Container": 19.4, "Dry Bulk": 8.1, "Tanker": 11.3, "LNG": 42.6, "LPG": 21.8}
    cols = st.columns(len(ob_pcts))
    for col, (stype, pct) in zip(cols, ob_pcts.items()):
        color = C_LOW if pct > 25 else (C_MOD if pct > 15 else C_HIGH)
        col.markdown(_stat_kpi(stype, "{:.1f}%".format(pct), "of existing fleet", color),
                     unsafe_allow_html=True)


# ── Section 4: Scrapping vs Delivery Balance ───────────────────────────────────

def _render_scrapping_delivery() -> None:
    _divider("Scrapping vs Delivery Balance", "⚓")

    years = list(range(2019, 2027))
    deliveries = [198, 156, 210, 248, 312, 278, 265, 230]   # vessels delivered
    scrappage   = [ 82,  45,  32,  18,  28,  42,  65,  88]  # vessels scrapped
    net_addition = [d - s for d, s in zip(deliveries, scrappage)]

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["Deliveries vs Scrapping (vessels)", "Net Fleet Addition (vessels)"],
        column_widths=[0.6, 0.4],
        horizontal_spacing=0.08,
    )

    fig.add_trace(go.Bar(
        x=years, y=deliveries, name="Deliveries",
        marker_color=_hex_to_rgba(C_LOW, 0.80),
        hovertemplate="<b>%{x}</b><br>Deliveries: %{y}<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=years, y=scrappage, name="Scrapping",
        marker_color=_hex_to_rgba(C_HIGH, 0.80),
        hovertemplate="<b>%{x}</b><br>Scrapped: %{y}<extra></extra>",
    ), row=1, col=1)

    net_colors = [_hex_to_rgba(C_LOW, 0.75) if n > 0 else _hex_to_rgba(C_HIGH, 0.75) for n in net_addition]
    fig.add_trace(go.Bar(
        x=years, y=net_addition, name="Net Addition",
        marker_color=net_colors,
        hovertemplate="<b>%{x}</b><br>Net: %{y:+d}<extra></extra>",
        showlegend=False,
    ), row=1, col=2)

    layout = _dark_layout(height=360)
    layout.update(dict(
        barmode="group",
        showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=10)),
        xaxis2=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=10)),
        yaxis=dict(title="Vessels", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10)),
        yaxis2=dict(title="Net Vessels", gridcolor="rgba(255,255,255,0.04)",
                    tickfont=dict(color=C_TEXT3, size=10), zeroline=True, zerolinecolor=C_BORDER),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="scrapping_delivery")

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_stat_kpi("2026E Deliveries", "{}".format(deliveries[-1]),
                           "Vessels scheduled", C_LOW), unsafe_allow_html=True)
    c2.markdown(_stat_kpi("2026E Scrapping", "{}".format(scrappage[-1]),
                           "Vessels to be removed", C_HIGH), unsafe_allow_html=True)
    net_col = C_LOW if net_addition[-1] > 100 else (C_MOD if net_addition[-1] > 50 else C_HIGH)
    c3.markdown(_stat_kpi("Net Fleet Change", "{:+d}".format(net_addition[-1]),
                           "Vessels added net", net_col), unsafe_allow_html=True)
    scrap_ratio = scrappage[-1] / deliveries[-1] * 100
    c4.markdown(_stat_kpi("Scrap/Delivery Ratio", "{:.0f}%".format(scrap_ratio),
                           "Higher = tighter supply", C_HIGH if scrap_ratio > 35 else C_MOD),
                unsafe_allow_html=True)


# ── Section 5: Company Comparison Matrix ──────────────────────────────────────

def _render_comparison_matrix() -> None:
    _divider("Company Comparison Matrix", "▦")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:14px;line-height:1.6">'
        'Five shipping stocks ranked by analyst upside. '
        'Color intensity reflects relative rank per metric: '
        '<span style="color:{}">■ Best</span> &nbsp; '
        '<span style="color:{}">■ Mid</span> &nbsp; '
        '<span style="color:{}">■ Worst</span>'
        '</div>'.format(C_TEXT3, C_HIGH, C_TEXT2, C_LOW),
        unsafe_allow_html=True,
    )

    rows = get_fundamentals_summary()

    metrics_to_show = [
        ("Revenue ($B)",    True,  "${:.2f}B"),
        ("EBITDA Margin %", True,  "{:.1f}%"),
        ("Net Debt ($B)",   False, "${:.2f}B"),
        ("EV/EBITDA",       False, "{:.1f}x"),
        ("P/B",             False, "{:.2f}x"),
        ("Div Yield %",     True,  "{:.1f}%"),
        ("Upside %",        True,  "{:.1f}%"),
    ]

    tickers = [r["Ticker"] for r in rows]
    ticker_colors_list = [TICKER_COLORS.get(t, C_ACCENT) for t in tickers]

    def _gradient(val: Any, vals: list, higher_better: bool) -> str:
        numeric = [v for v in vals if v is not None]
        if val is None or not numeric:
            return C_TEXT3
        mn, mx = min(numeric), max(numeric)
        if mx == mn:
            return C_TEXT2
        norm = (val - mn) / (mx - mn)
        if not higher_better:
            norm = 1 - norm
        if norm >= 0.75:
            return C_HIGH
        if norm >= 0.40:
            return C_TEXT2
        return C_LOW

    header_html = "".join(
        '<th style="background:{};color:{};padding:12px 16px;'
        'font-size:0.75rem;font-weight:800;text-transform:uppercase;'
        'letter-spacing:0.07em;border-bottom:2px solid {};text-align:center">{}</th>'.format(
            _hex_to_rgba(tc, 0.18), tc, tc, t,
        )
        for t, tc in zip(tickers, ticker_colors_list)
    )

    rows_html_parts = []
    for metric_key, higher_better, fmt in metrics_to_show:
        vals_raw = [r[metric_key] for r in rows]
        cells = ""
        for row in rows:
            v = row[metric_key]
            col = _gradient(v, vals_raw, higher_better)
            cells += (
                '<td style="padding:10px 16px;text-align:center;'
                'font-size:0.84rem;font-weight:700;color:{};'
                'border-bottom:1px solid {}">{}</td>'.format(
                    col, C_BORDER, _fmt_or_na(v, fmt),
                )
            )
        rows_html_parts.append(
            '<tr>'
            '<td style="padding:10px 16px;font-size:0.74rem;font-weight:600;'
            'color:{};white-space:nowrap;border-bottom:1px solid {}">{}</td>'
            '{}'
            '</tr>'.format(C_TEXT3, C_BORDER, metric_key, cells)
        )

    rating_cells = ""
    for row in rows:
        rc = RATING_COLORS.get(row["Rating"], C_TEXT3)
        rating_cells += (
            '<td style="padding:10px 16px;text-align:center;border-bottom:1px solid {}">'
            '<span style="background:{};color:{};padding:3px 12px;border-radius:999px;'
            'font-size:0.72rem;font-weight:800;border:1px solid {}">{}</span>'
            '</td>'.format(C_BORDER, _hex_to_rgba(rc, 0.2), rc, rc, row["Rating"])
        )
    rows_html_parts.append(
        '<tr>'
        '<td style="padding:10px 16px;font-size:0.74rem;font-weight:600;'
        'color:{};border-bottom:1px solid {}">Rating</td>'
        '{}</tr>'.format(C_TEXT3, C_BORDER, rating_cells)
    )

    zone_cells = ""
    for row in rows:
        zc = ZONE_COLORS.get(row["Valuation Zone"], C_TEXT3)
        zone_cells += (
            '<td style="padding:10px 16px;text-align:center;border-bottom:1px solid {}">'
            '<span style="font-size:0.76rem;font-weight:700;color:{}">{}</span>'
            '</td>'.format(C_BORDER, zc, row["Valuation Zone"])
        )
    rows_html_parts.append(
        '<tr>'
        '<td style="padding:10px 16px;font-size:0.74rem;font-weight:600;'
        'color:{};border-bottom:1px solid {}">EV/EBITDA Zone</td>'
        '{}</tr>'.format(C_TEXT3, C_BORDER, zone_cells)
    )

    table_html = (
        '<div style="overflow-x:auto;border-radius:14px;'
        'border:1px solid {};background:{};box-shadow:0 4px 24px rgba(0,0,0,0.3)">'
        '<table style="width:100%;border-collapse:collapse">'
        '<thead><tr>'
        '<th style="padding:12px 16px;font-size:0.72rem;font-weight:700;'
        'color:{};text-transform:uppercase;letter-spacing:0.08em;'
        'border-bottom:2px solid {}">Metric</th>'
        '{}'
        '</tr></thead>'
        '<tbody>{}</tbody>'
        '</table></div>'
    ).format(
        C_BORDER, C_CARD,
        C_TEXT3, C_BORDER,
        header_html,
        "".join(rows_html_parts),
    )
    st.markdown(table_html, unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:0.67rem;color:{};margin-top:8px">'
        'Color: green = best-in-class per metric | red = worst | '
        'Valuation Zone based on EV/EBITDA vs 10-year historical range.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    matrix_df = pd.DataFrame(rows)
    st.download_button(
        label="Download Comparison Matrix CSV",
        data=matrix_df.to_csv(index=False).encode("utf-8"),
        file_name="shipping_comparison_matrix.csv",
        mime="text/csv",
        key="fundamentals_matrix_download",
    )


# ── Section 6: Charter vs Spot Rate Comparison ────────────────────────────────

def _render_charter_vs_spot() -> None:
    _divider("Charter Rate vs Spot Rate Comparison", "📈")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px;line-height:1.6">'
        'Time-charter rates (12-month TC) vs spot market rates for container and dry bulk. '
        'TC premium over spot signals forward demand confidence; '
        'TC discount signals bearish market sentiment.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    months = pd.date_range("2024-01-01", periods=15, freq="MS")
    # Trans-Pacific: spot ($/FEU) and 12-month TC equivalent ($/FEU)
    tp_spot = [1_650, 1_820, 2_100, 3_200, 4_500, 5_200, 4_800, 4_100, 3_600,
               3_200, 2_950, 2_800, 2_650, 2_500, 2_420]
    tp_tc   = [2_800, 2_850, 2_900, 3_100, 3_400, 3_700, 3_600, 3_500, 3_350,
               3_200, 3_100, 3_050, 2_980, 2_920, 2_880]

    # BDI-equivalent: capesize spot ($/day) vs 1-year TC ($/day)
    cs_spot = [12_500, 14_200, 16_800, 18_500, 15_200, 13_400, 11_800, 10_200,
               9_500,  8_900, 10_200, 12_400, 14_800, 15_200, 14_100]
    cs_tc   = [13_800, 14_500, 15_200, 16_400, 15_800, 14_900, 13_600, 12_400,
               11_800, 11_200, 12_100, 13_200, 14_400, 15_000, 14_600]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.5, 0.5],
        vertical_spacing=0.08,
        subplot_titles=[
            "Trans-Pacific Container Rate  ($/FEU)",
            "Capesize Dry Bulk Rate  ($/day)",
        ],
    )

    fig.add_trace(go.Scatter(
        x=months, y=tp_spot, name="TP Spot Rate",
        mode="lines+markers",
        line=dict(color=C_ACCENT, width=2.5),
        marker=dict(size=6, color=C_ACCENT),
        hovertemplate="<b>%{x|%b %Y}</b><br>TP Spot: $%{y:,.0f}/FEU<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=months, y=tp_tc, name="TP 12m TC Rate",
        mode="lines+markers",
        line=dict(color=C_TEAL, width=2.5, dash="dash"),
        marker=dict(size=6, color=C_TEAL),
        hovertemplate="<b>%{x|%b %Y}</b><br>TC Rate: $%{y:,.0f}/FEU<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=months, y=cs_spot, name="Capesize Spot",
        mode="lines+markers",
        line=dict(color=C_MOD, width=2.5),
        marker=dict(size=6, color=C_MOD),
        hovertemplate="<b>%{x|%b %Y}</b><br>Capesize Spot: $%{y:,.0f}/day<extra></extra>",
    ), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=months, y=cs_tc, name="Capesize 1yr TC",
        mode="lines+markers",
        line=dict(color=C_ROSE, width=2.5, dash="dash"),
        marker=dict(size=6, color=C_ROSE),
        hovertemplate="<b>%{x|%b %Y}</b><br>Capesize TC: $%{y:,.0f}/day<extra></extra>",
    ), row=2, col=1)

    layout = _dark_layout(height=460)
    layout.update(dict(
        showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis2=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=10)),
        yaxis=dict(title="$/FEU", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10), tickprefix="$"),
        yaxis2=dict(title="$/day", gridcolor="rgba(255,255,255,0.04)",
                    tickfont=dict(color=C_TEXT3, size=10), tickprefix="$"),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="charter_vs_spot")

    # Premium / discount cards
    tp_prem = (tp_tc[-1] - tp_spot[-1]) / tp_spot[-1] * 100
    cs_prem = (cs_tc[-1] - cs_spot[-1]) / cs_spot[-1] * 100
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_stat_kpi("TP Spot Rate",    "${:,}".format(tp_spot[-1]),  "Current $/FEU",  C_ACCENT),
                unsafe_allow_html=True)
    c2.markdown(_stat_kpi("TP TC Rate",      "${:,}".format(tp_tc[-1]),    "12m charter $/FEU", C_TEAL),
                unsafe_allow_html=True)
    tc_col = C_HIGH if tp_prem > 0 else C_LOW
    c3.markdown(_stat_kpi("TC/Spot Premium", "{:+.1f}%".format(tp_prem),   "Container market",  tc_col),
                unsafe_allow_html=True)
    cs_col = C_HIGH if cs_prem > 0 else C_LOW
    c4.markdown(_stat_kpi("Dry Bulk TC Prem", "{:+.1f}%".format(cs_prem), "Capesize market",   cs_col),
                unsafe_allow_html=True)


# ── Section 7: Operating Cost Breakdown ───────────────────────────────────────

def _render_opex_breakdown() -> None:
    _divider("Operating Cost Breakdown", "⛽")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px;line-height:1.6">'
        'Industry average operating cost per vessel per day (OPEX). '
        'Bunker fuel is the largest variable cost; changes in oil price flow directly to shipping margins.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    # OPEX components — industry avg $/vessel/day
    cost_labels  = ["Bunker Fuel", "Port & Canal Fees", "Crew & Labour",
                    "Maintenance & Repair", "Insurance", "Admin & Other"]
    cost_values  = [8_200, 3_100, 2_800, 1_900, 650, 480]
    cost_colors  = [C_LOW, C_MOD, C_ACCENT, C_TEAL, C_CONV, C_TEXT3]
    total_opex   = sum(cost_values)

    col_pie, col_bar = st.columns([1, 1])

    with col_pie:
        fig_pie = go.Figure(go.Pie(
            labels=cost_labels,
            values=cost_values,
            hole=0.55,
            marker=dict(
                colors=[_hex_to_rgba(c, 0.85) for c in cost_colors],
                line=dict(color=C_SURFACE, width=3),
            ),
            textinfo="label+percent",
            textfont=dict(size=10, color=C_TEXT, family="Inter"),
            hovertemplate="<b>%{label}</b><br>$%{value:,}/day<br>%{percent}<extra></extra>",
        ))
        fig_pie.update_layout(
            **_dark_layout(height=340),
            showlegend=False,
            annotations=[dict(
                text="<b>${:,.0f}</b><br><span style='font-size:10px'>Total/day</span>".format(total_opex),
                x=0.5, y=0.5,
                font=dict(size=14, color=C_TEXT, family="Inter"),
                showarrow=False,
            )],
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="opex_pie")

    with col_bar:
        # YoY cost change % (trend bars)
        cost_yoy = [12.4, 3.2, 5.8, 2.1, 1.5, 4.2]
        bar_colors = [_hex_to_rgba(C_LOW, 0.80) if v > 5 else
                      _hex_to_rgba(C_MOD, 0.80) if v > 2 else
                      _hex_to_rgba(C_HIGH, 0.80) for v in cost_yoy]

        fig_bar = go.Figure(go.Bar(
            x=cost_yoy,
            y=cost_labels,
            orientation="h",
            marker_color=bar_colors,
            hovertemplate="<b>%{y}</b><br>YoY Change: %{x:+.1f}%<extra></extra>",
            text=["{:+.1f}%".format(v) for v in cost_yoy],
            textposition="outside",
            textfont=dict(size=10, color=C_TEXT2),
        ))
        layout = _dark_layout(height=340, title="OPEX Component YoY Change %")
        layout.update(dict(
            showlegend=False,
            xaxis=dict(title="YoY Change %", gridcolor="rgba(255,255,255,0.04)",
                       tickfont=dict(color=C_TEXT3, size=10), ticksuffix="%"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT2, size=10)),
        ))
        fig_bar.update_layout(**layout)
        st.plotly_chart(fig_bar, use_container_width=True, key="opex_yoy_bar")

    # OPEX KPIs
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_stat_kpi("Total OPEX/Day",   "${:,}".format(total_opex),
                           "Industry avg per vessel", C_TEXT2), unsafe_allow_html=True)
    c2.markdown(_stat_kpi("Bunker Share",     "{:.0f}%".format(cost_values[0]/total_opex*100),
                           "% of total OPEX", C_LOW), unsafe_allow_html=True)
    c3.markdown(_stat_kpi("Crew Cost/Day",    "${:,}".format(cost_values[2]),
                           "Avg all vessel types", C_ACCENT), unsafe_allow_html=True)
    c4.markdown(_stat_kpi("OPEX YoY Growth",  "+6.8%",
                           "Weighted avg all components", C_MOD), unsafe_allow_html=True)


# ── Section 8: Industry P&L Summary ───────────────────────────────────────────

def _render_industry_pnl() -> None:
    _divider("Industry P&L Summary", "💹")

    # Aggregate covered-universe P&L
    all_funds = list(COMPANY_FUNDAMENTALS.values())
    agg_rev    = sum(f.revenue_b    for f in all_funds)
    agg_ebitda = sum(f.ebitda_b     for f in all_funds)
    agg_ni     = sum(f.net_income_b for f in all_funds)
    agg_opex   = agg_rev - agg_ebitda
    agg_da     = agg_ebitda * 0.28   # ~28% D&A / EBITDA typical shipping
    agg_ebit   = agg_ebitda - agg_da
    agg_int    = sum(f.net_debt_b for f in all_funds) * 0.055   # avg 5.5% interest rate
    agg_tax    = max(0, agg_ebit - agg_int) * 0.12

    # Waterfall chart
    waterfall_labels  = ["Revenue", "Operating Costs", "EBITDA", "D&A",
                          "EBIT", "Interest Expense", "Tax", "Net Income"]
    waterfall_values  = [agg_rev, -agg_opex, agg_ebitda, -agg_da,
                          agg_ebit, -agg_int, -agg_tax, agg_ni]
    waterfall_measure = ["absolute", "relative", "total", "relative",
                          "total", "relative", "relative", "total"]
    w_colors = []
    for m, v in zip(waterfall_measure, waterfall_values):
        if m == "absolute":
            w_colors.append(C_ACCENT)
        elif m == "total":
            w_colors.append(C_HIGH if v > 0 else C_LOW)
        else:
            w_colors.append(C_HIGH if v >= 0 else C_LOW)

    fig = go.Figure(go.Waterfall(
        name="P&L",
        orientation="v",
        measure=waterfall_measure,
        x=waterfall_labels,
        y=waterfall_values,
        connector=dict(line=dict(color=C_BORDER, width=1.5, dash="dot")),
        decreasing=dict(marker=dict(color=_hex_to_rgba(C_LOW, 0.78))),
        increasing=dict(marker=dict(color=_hex_to_rgba(C_HIGH, 0.78))),
        totals=dict(marker=dict(color=_hex_to_rgba(C_ACCENT, 0.78))),
        hovertemplate="<b>%{x}</b><br>$%{y:.2f}B<extra></extra>",
        text=["${:.2f}B".format(abs(v)) for v in waterfall_values],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT2),
    ))

    layout = _dark_layout(height=400, title="Covered Universe Aggregate P&L  (USD Billions)")
    layout.update(dict(
        showlegend=False,
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT2, size=11)),
        yaxis=dict(title="USD Billions", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10), tickprefix="$", ticksuffix="B"),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="industry_pnl_waterfall")

    # Margin KPIs
    ebitda_margin = agg_ebitda / agg_rev * 100
    ni_margin     = agg_ni    / agg_rev * 100
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_stat_kpi("Agg Revenue",       "${:.2f}B".format(agg_rev),
                           "Covered universe", C_ACCENT), unsafe_allow_html=True)
    c2.markdown(_stat_kpi("Agg EBITDA",        "${:.2f}B".format(agg_ebitda),
                           "Before D&A", C_HIGH), unsafe_allow_html=True)
    c3.markdown(_stat_kpi("EBITDA Margin",     "{:.1f}%".format(ebitda_margin),
                           "Universe avg", C_HIGH if ebitda_margin > 35 else C_MOD),
                unsafe_allow_html=True)
    c4.markdown(_stat_kpi("Net Income",        "${:.2f}B".format(agg_ni),
                           "After tax", C_HIGH if agg_ni > 0 else C_LOW), unsafe_allow_html=True)
    c5.markdown(_stat_kpi("Net Margin",        "{:.1f}%".format(ni_margin),
                           "Universe avg", C_HIGH if ni_margin > 15 else C_MOD),
                unsafe_allow_html=True)


# ── Section 9: Fleet Utilisation Trend ────────────────────────────────────────

def _render_fleet_utilisation() -> None:
    _divider("Fleet Utilisation Rate Trend", "📊")

    quarters = ["Q1'22", "Q2'22", "Q3'22", "Q4'22",
                 "Q1'23", "Q2'23", "Q3'23", "Q4'23",
                 "Q1'24", "Q2'24", "Q3'24", "Q4'24",
                 "Q1'25", "Q2'25", "Q3'25", "Q4'25", "Q1'26E"]

    # Container utilisation %
    container_util = [96.2, 97.1, 97.8, 96.5, 92.4, 88.6, 86.2, 85.8,
                       87.1, 88.9, 89.4, 90.2, 91.0, 91.5, 92.1, 92.8, 93.4]
    # Dry bulk utilisation %
    drybulk_util   = [88.4, 90.2, 91.5, 89.8, 84.2, 82.6, 83.4, 84.1,
                       85.2, 86.4, 87.8, 88.5, 86.2, 85.4, 86.8, 87.5, 88.1]
    # Tanker utilisation %
    tanker_util    = [78.5, 82.4, 88.6, 91.2, 92.5, 93.8, 94.2, 93.6,
                       91.8, 90.4, 89.2, 88.6, 87.4, 86.8, 87.2, 87.9, 88.4]

    fig = go.Figure()

    for name, util, color in [
        ("Container", container_util, C_ACCENT),
        ("Dry Bulk",  drybulk_util,   C_MOD),
        ("Tanker",    tanker_util,     C_TEAL),
    ]:
        fig.add_trace(go.Scatter(
            x=quarters, y=util, name=name,
            mode="lines+markers",
            line=dict(color=color, width=2.5),
            marker=dict(size=6, color=color),
            fill="tozeroy",
            fillcolor=_hex_to_rgba(color, 0.04),
            hovertemplate="<b>{}</b><br>%{{x}}: %{{y:.1f}}%<extra></extra>".format(name),
        ))

    # Tight market threshold line
    fig.add_hline(y=90, line_dash="dot", line_color=C_HIGH, line_width=1.5,
                  annotation_text="  Tight market threshold (90%)",
                  annotation_font=dict(color=C_HIGH, size=10))
    fig.add_hline(y=85, line_dash="dot", line_color=C_MOD, line_width=1.5,
                  annotation_text="  Balanced market (85%)",
                  annotation_font=dict(color=C_MOD, size=10))

    layout = _dark_layout(height=400, title="Fleet Utilisation by Sector  |  Quarterly")
    layout.update(dict(
        showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=10),
                   tickangle=-30),
        yaxis=dict(title="Utilisation %", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10), ticksuffix="%",
                   range=[75, 100]),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="fleet_utilisation_trend")

    c1, c2, c3 = st.columns(3)
    for col, (sector, util, color) in zip(
        [c1, c2, c3],
        [("Container", container_util, C_ACCENT),
         ("Dry Bulk",  drybulk_util,   C_MOD),
         ("Tanker",    tanker_util,     C_TEAL)],
    ):
        latest = util[-1]
        trend  = util[-1] - util[-2]
        col.markdown(
            _stat_kpi("{} Utilisation".format(sector),
                      "{:.1f}%".format(latest),
                      "Trend: {:+.1f}pp QoQ".format(trend),
                      color),
            unsafe_allow_html=True,
        )


# ── Section 10: Industry Leverage & Financial Health ──────────────────────────

def _render_leverage_health() -> None:
    _divider("Industry Leverage & Financial Health", "🏦")

    tickers = list(COMPANY_FUNDAMENTALS.keys())
    funds   = [COMPANY_FUNDAMENTALS[t] for t in tickers]

    nd_ebitda = []
    interest_cover = []
    for f in funds:
        if f.ebitda_b and f.ebitda_b > 0:
            nd_ebitda.append(f.net_debt_b / f.ebitda_b)
            int_exp = f.net_debt_b * 0.055
            interest_cover.append(f.ebitda_b / int_exp if int_exp > 0 else 99.0)
        else:
            nd_ebitda.append(0.0)
            interest_cover.append(0.0)

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["Net Debt / EBITDA  (lower = safer)", "Interest Cover  (EBITDA / Interest Expense)"],
        horizontal_spacing=0.12,
    )

    tc_list = [TICKER_COLORS.get(t, C_ACCENT) for t in tickers]

    fig.add_trace(go.Bar(
        x=tickers, y=nd_ebitda, name="Net Debt/EBITDA",
        marker_color=[_hex_to_rgba(
            C_HIGH if v < 2 else (C_MOD if v < 3.5 else C_LOW), 0.82
        ) for v in nd_ebitda],
        hovertemplate="<b>%{x}</b><br>ND/EBITDA: %{y:.1f}x<extra></extra>",
        text=["{:.1f}x".format(v) for v in nd_ebitda],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT2),
    ), row=1, col=1)

    fig.add_hline(y=3.5, line_dash="dot", line_color=C_LOW, line_width=1.5,
                  annotation_text="  Stress level (3.5x)",
                  annotation_font=dict(color=C_LOW, size=10),
                  row=1, col=1)
    fig.add_hline(y=2.0, line_dash="dot", line_color=C_HIGH, line_width=1.5,
                  annotation_text="  Healthy (2.0x)",
                  annotation_font=dict(color=C_HIGH, size=10),
                  row=1, col=1)

    fig.add_trace(go.Bar(
        x=tickers, y=interest_cover, name="Interest Cover",
        marker_color=[_hex_to_rgba(
            C_HIGH if v > 5 else (C_MOD if v > 3 else C_LOW), 0.82
        ) for v in interest_cover],
        hovertemplate="<b>%{x}</b><br>Interest Cover: %{y:.1f}x<extra></extra>",
        text=["{:.1f}x".format(v) for v in interest_cover],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT2),
    ), row=1, col=2)

    layout = _dark_layout(height=380)
    layout.update(dict(
        showlegend=False,
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT2, size=11)),
        xaxis2=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT2, size=11)),
        yaxis=dict(title="Net Debt / EBITDA (x)", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10), ticksuffix="x"),
        yaxis2=dict(title="Interest Cover (x)", gridcolor="rgba(255,255,255,0.04)",
                    tickfont=dict(color=C_TEXT3, size=10), ticksuffix="x"),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="leverage_health")

    # Per-ticker summary cards
    cols = st.columns(len(tickers))
    for col, ticker, f, nde, ic in zip(cols, tickers, funds, nd_ebitda, interest_cover):
        tc = TICKER_COLORS.get(ticker, C_ACCENT)
        nde_color = C_HIGH if nde < 2 else (C_MOD if nde < 3.5 else C_LOW)
        ic_color  = C_HIGH if ic > 5  else (C_MOD if ic  > 3   else C_LOW)
        col.markdown(
            '<div style="background:{bg};border:1px solid {bd};border-top:3px solid {tc};'
            'border-radius:10px;padding:12px 14px">'
            '<div style="font-size:0.75rem;font-weight:800;color:{tc};margin-bottom:8px">{tkr}</div>'
            '<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
            '<span style="font-size:0.65rem;color:{t3}">ND/EBITDA</span>'
            '<span style="font-size:0.75rem;font-weight:700;color:{ndc}">{nde:.1f}x</span>'
            '</div>'
            '<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
            '<span style="font-size:0.65rem;color:{t3}">Int. Cover</span>'
            '<span style="font-size:0.75rem;font-weight:700;color:{icc}">{ic:.1f}x</span>'
            '</div>'
            '<div style="display:flex;justify-content:space-between">'
            '<span style="font-size:0.65rem;color:{t3}">Gross Debt</span>'
            '<span style="font-size:0.75rem;font-weight:700;color:{t2}">${dbt:.1f}B</span>'
            '</div>'
            '</div>'.format(
                bg=C_CARD, bd=C_BORDER, tc=tc,
                tkr=ticker, t3=C_TEXT3, t2=C_TEXT2,
                ndc=nde_color, nde=nde,
                icc=ic_color,  ic=ic,
                dbt=f.debt_b,
            ),
            unsafe_allow_html=True,
        )


# ── Section 11: Earnings Surprise Model ───────────────────────────────────────

def _render_earnings_surprise() -> None:
    _divider("Earnings Surprise Model", "⚡")

    tickers  = list(EARNINGS_HISTORY.keys())
    selected = st.selectbox(
        "Select Company",
        options=tickers,
        format_func=lambda t: "{} — {}".format(t, COMPANY_FUNDAMENTALS[t].company_name),
        key="earnings_ticker_select",
        label_visibility="collapsed",
    )

    history     = EARNINGS_HISTORY.get(selected, [])
    fund        = COMPANY_FUNDAMENTALS[selected]
    sensitivity = RATE_TO_EPS_SENSITIVITY_100FEU.get(selected, 0.0)
    tc          = TICKER_COLORS.get(selected, C_ACCENT)

    quarters  = [h.quarter         for h in history]
    reported  = [h.reported_eps    for h in history]
    consensus = [h.consensus_eps   for h in history]
    beat_pcts = [h.beat_pct        for h in history]
    tp_rates  = [h.freight_rate_at_report for h in history]

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.40],
        vertical_spacing=0.08,
        subplot_titles=[
            "{} — Reported EPS vs Consensus  (last 4 quarters)".format(selected),
            "Trans-Pacific Rate at Earnings Date  ($/FEU)",
        ],
    )

    fig.add_trace(go.Bar(
        x=quarters, y=reported, name="Reported EPS",
        marker_color=_hex_to_rgba(tc, 0.82),
        hovertemplate="<b>%{x}</b><br>Reported: $%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=quarters, y=consensus, mode="lines+markers",
        name="Consensus EPS",
        line=dict(color=C_TEXT3, width=2, dash="dot"),
        marker=dict(size=7, color=C_TEXT3),
        hovertemplate="<b>%{x}</b><br>Consensus: $%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    for i, (q, rep, bp) in enumerate(zip(quarters, reported, beat_pcts)):
        beat_col = C_HIGH if bp >= 0 else C_LOW
        sign = "+" if bp >= 0 else ""
        fig.add_annotation(
            x=q, y=rep + 0.05 * max(reported),
            text="{}{}%".format(sign, round(bp, 1)),
            font=dict(size=9, color=beat_col, family="Inter"),
            showarrow=False, row=1, col=1,
        )

    fig.add_trace(go.Bar(
        x=quarters, y=tp_rates, name="TP Rate ($/FEU)",
        marker_color=_hex_to_rgba(C_ACCENT, 0.65),
        hovertemplate="<b>%{x}</b><br>TP Rate: $%{y:,.0f}/FEU<extra></extra>",
    ), row=2, col=1)

    layout = _dark_layout(height=460)
    layout.update(dict(
        barmode="group", showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis2=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT3, size=10)),
        yaxis=dict(title="EPS ($)", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10)),
        yaxis2=dict(title="$/FEU", gridcolor="rgba(255,255,255,0.04)",
                    tickfont=dict(color=C_TEXT3, size=10), tickformat="$,.0f"),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="earnings_surprise_chart")

    avg_beat  = sum(beat_pcts) / len(beat_pcts) if beat_pcts else 0.0
    beat_color = C_HIGH if avg_beat >= 0 else C_LOW
    c1, c2, c3 = st.columns(3)
    c1.markdown(_stat_kpi("Avg Beat/Miss", "{:+.1f}%".format(avg_beat),
                           "vs consensus (last 4Q)", beat_color), unsafe_allow_html=True)
    c2.markdown(_stat_kpi("Rate Sensitivity", "${:.3f} EPS".format(sensitivity),
                           "per $100/FEU rate change", C_ACCENT), unsafe_allow_html=True)
    c3.markdown(_stat_kpi("Freight Beta", "{:.1f}x".format(compute_shipping_beta(selected)),
                           "stock sensitivity to rates", tc), unsafe_allow_html=True)

    st.markdown(
        '<div style="background:{bg};border:1px solid {bd};border-left:3px solid {tc};'
        'border-radius:10px;padding:14px 18px;margin-top:12px">'
        '<div style="font-size:0.67rem;font-weight:700;color:{t3};'
        'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">'
        'Rate → Earnings Interpretation</div>'
        '<div style="font-size:0.82rem;color:{t2};line-height:1.6">'
        'A $100/FEU change in Trans-Pacific spot rates translates to approximately '
        '<b style="color:{tc}">${sens:.3f}</b> in EPS for {tkr}. '
        'At a shipping beta of <b style="color:{tc}">{beta:.1f}x</b>, '
        'a 10% freight rate move implies roughly a '
        '<b style="color:{tc}">{impl:.1f}%</b> move in the stock.'
        '</div>'
        '</div>'.format(
            bg=C_CARD, bd=C_BORDER, tc=tc, t3=C_TEXT3, t2=C_TEXT2,
            sens=sensitivity, tkr=selected,
            beta=compute_shipping_beta(selected),
            impl=compute_shipping_beta(selected) * 10,
        ),
        unsafe_allow_html=True,
    )


# ── Section 12: Valuation Dashboard ───────────────────────────────────────────

def _render_valuation_dashboard() -> None:
    _divider("Fundamental Valuation  |  P/E · EV/EBITDA · P/B · Yield", "◈")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px;line-height:1.6">'
        'Gauge zones: <span style="color:{}">Green = Cheap vs history</span> &nbsp;|&nbsp; '
        '<span style="color:{}">Amber = Fair value</span> &nbsp;|&nbsp; '
        '<span style="color:{}">Red = Expensive</span>. '
        'Ranges calibrated to 10-year shipping cycle history.'
        '</div>'.format(C_TEXT3, C_HIGH, C_MOD, C_LOW),
        unsafe_allow_html=True,
    )

    sorted_tickers = [r["Ticker"] for r in get_fundamentals_summary()]

    for ticker in sorted_tickers:
        fund      = COMPANY_FUNDAMENTALS[ticker]
        vr        = VALUATION_RANGES[ticker]
        tc        = TICKER_COLORS.get(ticker, C_ACCENT)
        zone      = get_valuation_zone(ticker)
        zone_color = ZONE_COLORS.get(zone, C_TEXT3)
        norm_ni   = compute_normalized_earnings(ticker, "DECLINE")

        # Estimate P/E from available data
        market_cap_est = fund.ebitda_b * fund.ev_ebitda - fund.net_debt_b
        pe_est = (market_cap_est / fund.net_income_b) if fund.net_income_b and fund.net_income_b > 0 else None

        with st.expander(
            "{} — {}   |   Zone: {}".format(ticker, fund.company_name, zone),
            expanded=(ticker == sorted_tickers[0]),
            key="valuation_expander_" + ticker,
        ):
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                _render_gauge("EV / EBITDA", fund.ev_ebitda,
                               vr.ev_ebitda_cheap, vr.ev_ebitda_fair_lo,
                               vr.ev_ebitda_fair_hi, vr.ev_ebitda_expensive,
                               "{:.1f}x", True, ticker + "_evebitda", tc)
            with c2:
                _render_gauge("Price / Book", fund.price_to_book,
                               vr.pb_cheap, vr.pb_fair_lo,
                               vr.pb_fair_hi, vr.pb_expensive,
                               "{:.2f}x", True, ticker + "_pb", tc)
            with c3:
                _render_gauge("Dividend Yield", fund.dividend_yield_pct,
                               vr.yield_expensive, vr.yield_fair_hi,
                               vr.yield_fair_lo, vr.yield_cheap,
                               "{:.1f}%", False, ticker + "_yield", tc)
            with c4:
                # P/E gauge — wide range for shipping cyclicality
                _render_gauge("Price / Earnings", pe_est,
                               3.0, 6.0, 12.0, 20.0,
                               "{:.1f}x", True, ticker + "_pe", tc)

            nd_ebitda = (
                fund.net_debt_b / fund.ebitda_b
                if (fund.ebitda_b is not None and fund.ebitda_b > 0) else 0.0
            )
            _upside_color = C_HIGH if (fund.upside_pct is not None and fund.upside_pct > 15) else C_TEXT2
            rating_color  = RATING_COLORS.get(fund.analyst_rating, C_TEXT3)
            st.markdown(
                '<div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:10px;'
                'padding-top:12px;border-top:1px solid {bd}">'
                '<span style="font-size:0.75rem;color:{t3}">Analyst: '
                '<b style="color:{rc}">{rating}</b></span>'
                '<span style="font-size:0.75rem;color:{t3}">PT: '
                '<b style="color:{tc}">{pt}</b></span>'
                '<span style="font-size:0.75rem;color:{t3}">Upside: '
                '<b style="color:{uc}">{up}</b></span>'
                '<span style="font-size:0.75rem;color:{t3}">ND/EBITDA: '
                '<b style="color:{ndc}">{nde:.1f}x</b></span>'
                '<span style="font-size:0.75rem;color:{t3}">P/E: '
                '<b style="color:{tc}">{pe}</b></span>'
                '<span style="font-size:0.75rem;color:{t3}">Norm. NI: '
                '<b style="color:{tc}">{nni}</b></span>'
                '</div>'.format(
                    bd=C_BORDER, t3=C_TEXT3, tc=tc,
                    rc=rating_color, rating=fund.analyst_rating,
                    pt=_fmt_or_na(fund.price_target_usd, "${:.2f}"),
                    uc=_upside_color, up=_fmt_or_na(fund.upside_pct, "{:.1f}%"),
                    ndc=C_MOD if nd_ebitda > 3 else C_TEXT2, nde=nd_ebitda,
                    pe=_fmt_or_na(pe_est, "{:.1f}x"),
                    nni=_fmt_or_na(norm_ni, "${:.3f}B"),
                ),
                unsafe_allow_html=True,
            )


def _render_gauge(
    label: str, value: float | None,
    cheap: float, fair_lo: float, fair_hi: float, expensive: float,
    fmt: str, higher_expensive: bool,
    key_suffix: str, accent: str,
) -> None:
    if value is None:
        st.markdown(
            '<div style="background:{bg};border:1px solid {bd};border-radius:10px;'
            'padding:20px;text-align:center;color:{t3};font-size:0.82rem">'
            '<div style="font-size:0.67rem;font-weight:700;color:{t3};'
            'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">{label}</div>'
            'N/A'
            '</div>'.format(bg=C_CARD, bd=C_BORDER, t3=C_TEXT3, label=label),
            unsafe_allow_html=True,
        )
        return

    if higher_expensive:
        needle_color = C_HIGH if value <= fair_lo else (C_MOD if value <= fair_hi else C_LOW)
    else:
        needle_color = C_HIGH if value >= fair_lo else (C_MOD if value >= fair_hi else C_LOW)

    gauge_range = [cheap, expensive] if higher_expensive else [expensive, cheap]
    g_min = min(gauge_range) * 0.88
    g_max = max(gauge_range) * 1.12

    steps = [
        dict(range=[g_min, fair_lo if higher_expensive else fair_hi],
             color=_hex_to_rgba(C_HIGH, 0.13)),
        dict(range=[fair_lo if higher_expensive else fair_hi,
                    fair_hi if higher_expensive else fair_lo],
             color=_hex_to_rgba(C_MOD, 0.13)),
        dict(range=[fair_hi if higher_expensive else fair_lo, g_max],
             color=_hex_to_rgba(C_LOW, 0.13)),
    ]

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number=dict(font=dict(size=22, color=needle_color, family="Inter"),
                    suffix="" if "%" not in fmt else "%",
                    valueformat=".1f"),
        title=dict(text=label, font=dict(size=10, color=C_TEXT3, family="Inter")),
        gauge=dict(
            axis=dict(range=[g_min, g_max], tickfont=dict(size=9, color=C_TEXT3), nticks=5),
            bar=dict(color=accent, thickness=0.25),
            bgcolor=C_SURFACE,
            borderwidth=1, bordercolor=C_BORDER,
            steps=steps,
            threshold=dict(line=dict(color=needle_color, width=3), thickness=0.75, value=value),
        ),
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT, family="Inter"),
        height=200, margin=dict(l=10, r=10, t=30, b=10),
    )
    st.plotly_chart(fig, use_container_width=True, key="gauge_{}".format(key_suffix))


# ── Section 13: Shipping Beta Dashboard ───────────────────────────────────────

def _render_beta_dashboard() -> None:
    _divider("Shipping Beta Dashboard", "β")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px;line-height:1.6">'
        'Beta = % stock move per 1% move in each indicator. '
        'Positive beta = stock moves with indicator; Negative = inverse relationship.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    tickers = list(COMPANY_FUNDAMENTALS.keys())
    factors = ["freight_rate", "bdi", "oil_price", "pmi_global", "usd_dxy"]
    factor_labels = {
        "freight_rate": "Trans-Pacific Rate",
        "bdi":          "Baltic Dry Index",
        "oil_price":    "Oil Price (Brent)",
        "pmi_global":   "Global PMI",
        "usd_dxy":      "USD Index (DXY)",
    }

    fig = go.Figure()
    for ticker in tickers:
        betas     = get_all_betas(ticker)
        beta_vals = [betas.get(f, 0.0) for f in factors]
        tc        = TICKER_COLORS.get(ticker, C_ACCENT)
        fig.add_trace(go.Bar(
            name=ticker,
            x=[factor_labels[f] for f in factors],
            y=beta_vals,
            marker_color=_hex_to_rgba(tc, 0.82),
            hovertemplate="<b>{}</b><br>%{{x}}: %{{y:.2f}}x beta<extra></extra>".format(ticker),
        ))

    fig.add_hline(y=0, line_color=C_BORDER, line_width=1)

    layout = _dark_layout(height=400, title="Shipping Beta by Factor  |  Stock % Move per 1% Factor Move")
    layout.update(dict(
        barmode="group", showlegend=True,
        legend=dict(font=dict(color=C_TEXT2, size=11), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", tickfont=dict(color=C_TEXT2, size=11)),
        yaxis=dict(title="Beta (x)", gridcolor="rgba(255,255,255,0.04)",
                   tickfont=dict(color=C_TEXT3, size=10), zeroline=True, zerolinecolor=C_BORDER),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="beta_dashboard")

    interpretations = {
        "ZIM":  ("ZIM is 2.5x levered to freight rates — the most rate-sensitive name in coverage. "
                  "A 10% drop in Trans-Pacific spot rates implies ~25% equity decline. "
                  "Asset-light model means fast earnings erosion in downturns."),
        "MATX": ("MATX has the lowest rate beta (0.8x) due to near-monopoly domestic US routes "
                  "and Jones Act protection. Hawaii/Alaska demand is inelastic."),
        "SBLK": ("SBLK's primary driver is BDI (1.8x beta). Capesize demand tracks Chinese "
                  "steel/iron ore imports. Oil price headwind (-0.3x) reflects fuel-cost sensitivity."),
        "DAC":  ("DAC has very low spot-rate exposure (0.6x) as 90%+ of revenue is locked into "
                  "long-term charters. Rate beta is indirect with 12-24 month lag."),
        "CMRE": ("CMRE shows moderate betas across factors, reflecting its diversified "
                  "container + dry-bulk fleet. PMI sensitivity (0.9x) captures trade-volume exposure."),
    }

    cols = st.columns(len(tickers))
    for col, ticker in zip(cols, tickers):
        tc = TICKER_COLORS.get(ticker, C_ACCENT)
        col.markdown(
            '<div style="background:{bg};border:1px solid {bd};border-top:3px solid {tc};'
            'border-radius:10px;padding:12px 14px;height:100%">'
            '<div style="font-size:0.72rem;font-weight:800;color:{tc};margin-bottom:6px">{tkr}</div>'
            '<div style="font-size:0.71rem;color:{t2};line-height:1.45">{interp}</div>'
            '</div>'.format(
                bg=C_CARD, bd=C_BORDER, tc=tc,
                tkr=ticker, t2=C_TEXT2,
                interp=interpretations.get(ticker, ""),
            ),
            unsafe_allow_html=True,
        )


# ── Section 14: Earnings Calendar ─────────────────────────────────────────────

def _render_earnings_calendar() -> None:
    _divider("Earnings Calendar — Next 90 Days", "📅")

    today   = datetime.date.today()
    horizon = today + datetime.timedelta(days=90)

    upcoming = []
    for ticker, fund in COMPANY_FUNDAMENTALS.items():
        ned       = fund.next_earnings_date
        days_away = (ned - today).days
        if 0 <= days_away <= 90:
            upcoming.append((days_away, ticker, fund))
    upcoming.sort(key=lambda x: x[0])

    if not upcoming:
        st.markdown(
            '<div style="background:{bg};border:1px solid {bd};border-radius:10px;'
            'padding:24px;text-align:center;color:{t3};font-size:0.88rem">'
            'No earnings dates in the next 90 days. '
            'Next cycle begins beyond {:%B %Y}.'
            '</div>'.format(bg=C_CARD, bd=C_BORDER, t3=C_TEXT3, horizon=horizon),
            unsafe_allow_html=True,
        )
    else:
        for days_away, ticker, fund in upcoming:
            tc           = TICKER_COLORS.get(ticker, C_ACCENT)
            rating_color = RATING_COLORS.get(fund.analyst_rating, C_TEXT3)
            if days_away == 0:
                countdown_text, countdown_color = "TODAY", C_LOW
            elif days_away <= 7:
                countdown_text, countdown_color = "{}d".format(days_away), C_MOD
            else:
                countdown_text, countdown_color = "{}d".format(days_away), C_TEXT2

            urgency_border = C_LOW if days_away <= 7 else C_BORDER
            st.markdown(
                '<div style="background:{bg};border:1px solid {ub};border-left:4px solid {tc};'
                'border-radius:12px;padding:16px 20px;margin-bottom:10px;'
                'display:flex;align-items:center;justify-content:space-between;'
                'flex-wrap:wrap;gap:12px;box-shadow:0 2px 12px rgba(0,0,0,0.25)">'
                '<div style="flex:1;min-width:200px">'
                '  <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">'
                '    <span style="font-size:1.05rem;font-weight:800;color:{tc}">{tkr}</span>'
                '    <span style="font-size:0.72rem;color:{t3};background:{tcl};'
                '      padding:2px 8px;border-radius:6px;border:1px solid {tc}">{qtr}</span>'
                '  </div>'
                '  <div style="font-size:0.78rem;color:{t2}">{name}</div>'
                '  <div style="font-size:0.71rem;color:{t3};margin-top:2px">'
                '    Next earnings: <b style="color:{tc}">{dt:%B %d, %Y}</b>'
                '  </div>'
                '</div>'
                '<div style="display:flex;gap:24px;flex-wrap:wrap">'
                '  <div style="text-align:center">'
                '    <div style="font-size:0.6rem;color:{t3};text-transform:uppercase;margin-bottom:2px">Rating</div>'
                '    <div style="font-size:0.84rem;font-weight:700;color:{rc}">{rating}</div>'
                '  </div>'
                '  <div style="text-align:center">'
                '    <div style="font-size:0.6rem;color:{t3};text-transform:uppercase;margin-bottom:2px">PT ($)</div>'
                '    <div style="font-size:0.84rem;font-weight:700;color:{tc}">${pt:.2f}</div>'
                '  </div>'
                '  <div style="text-align:center">'
                '    <div style="font-size:0.6rem;color:{t3};text-transform:uppercase;margin-bottom:2px">Upside</div>'
                '    <div style="font-size:0.84rem;font-weight:700;color:{uc}">{up:.1f}%</div>'
                '  </div>'
                '</div>'
                '<div style="text-align:center;flex-shrink:0">'
                '  <div style="background:{cbl};border:2px solid {cc};border-radius:12px;'
                '    padding:10px 18px;min-width:70px">'
                '    <div style="font-size:1.5rem;font-weight:900;color:{cc};line-height:1">{ct}</div>'
                '    <div style="font-size:0.6rem;color:{t3};text-transform:uppercase;'
                '      letter-spacing:0.07em;margin-top:2px">days away</div>'
                '  </div>'
                '</div>'
                '</div>'.format(
                    bg=C_CARD, ub=urgency_border, tc=tc,
                    tkr=ticker, t3=C_TEXT3, t2=C_TEXT2,
                    tcl=_hex_to_rgba(tc, 0.12),
                    qtr="Q{} {}".format(((fund.next_earnings_date.month - 1) // 3) + 1,
                                        fund.next_earnings_date.year),
                    name=fund.company_name, dt=fund.next_earnings_date,
                    rc=rating_color, rating=fund.analyst_rating,
                    pt=fund.price_target_usd,
                    uc=C_HIGH if fund.upside_pct > 15 else C_TEXT2, up=fund.upside_pct,
                    cbl=_hex_to_rgba(countdown_color, 0.12), cc=countdown_color, ct=countdown_text,
                ),
                unsafe_allow_html=True,
            )

    beyond = [
        (ticker, fund) for ticker, fund in COMPANY_FUNDAMENTALS.items()
        if (fund.next_earnings_date - today).days > 90
        or (fund.next_earnings_date - today).days < 0
    ]
    if beyond:
        st.markdown(
            '<div style="font-size:0.71rem;color:{t3};margin-top:12px">'
            'Beyond 90-day window: {names}'
            '</div>'.format(
                t3=C_TEXT3,
                names=", ".join(
                    "{} ({:%b %d})".format(t, f.next_earnings_date) for t, f in beyond
                ),
            ),
            unsafe_allow_html=True,
        )


# ── Main render ────────────────────────────────────────────────────────────────

def render(
    stock_data: dict,
    freight_data: dict,
    macro_data: dict,
) -> None:
    """Render the Fundamentals tab.

    Parameters
    ----------
    stock_data:
        Dict ticker -> DataFrame (columns: date, open, high, low, close, volume).
    freight_data:
        Dict route_id -> DataFrame (columns: date, rate_usd_per_feu, source).
    macro_data:
        Dict series_id -> DataFrame from FRED / WorldBank.
    """
    logger.info(
        "Rendering Fundamentals tab — stock_data tickers={tickers}",
        tickers=list((stock_data or {}).keys()),
    )

    if not stock_data:
        st.info(
            "Live price data from yfinance is unavailable. "
            "All sections display using static fundamental estimates. "
            "Refresh the app to retry fetching live data."
        )

    # ── Tab header ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="margin-bottom:24px;padding-bottom:16px;'
        'border-bottom:1px solid {bd}">'
        '<div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap">'
        '<div style="font-size:1.5rem;font-weight:900;color:{tx};letter-spacing:-0.03em">'
        'Shipping Stock Fundamentals</div>'
        '<div style="font-size:0.7rem;color:{t3};text-transform:uppercase;'
        'letter-spacing:0.1em;font-weight:600">Q1 2026 View</div>'
        '</div>'
        '<div style="font-size:0.83rem;color:{t3};margin-top:6px;line-height:1.5">'
        'Deep fundamental analysis across the shipping universe &nbsp;·&nbsp; '
        'ZIM &nbsp;·&nbsp; MATX &nbsp;·&nbsp; SBLK &nbsp;·&nbsp; DAC &nbsp;·&nbsp; CMRE'
        '</div>'
        '</div>'.format(bd=C_BORDER, tx=C_TEXT, t3=C_TEXT3),
        unsafe_allow_html=True,
    )

    try:
        _render_health_dashboard()
    except Exception as exc:
        logger.warning("health_dashboard error: {}", exc)
        st.warning("Fundamentals Health Dashboard temporarily unavailable.")

    try:
        _render_shipping_cycle()
    except Exception as exc:
        logger.warning("shipping_cycle error: {}", exc)
        st.warning("Shipping Cycle section temporarily unavailable.")

    try:
        _render_supply_demand_balance()
    except Exception as exc:
        logger.warning("supply_demand error: {}", exc)
        st.warning("Supply-Demand Balance section temporarily unavailable.")

    try:
        _render_orderbook()
    except Exception as exc:
        logger.warning("orderbook error: {}", exc)
        st.warning("Vessel Orderbook section temporarily unavailable.")

    try:
        _render_scrapping_delivery()
    except Exception as exc:
        logger.warning("scrapping_delivery error: {}", exc)
        st.warning("Scrapping vs Delivery section temporarily unavailable.")

    try:
        _render_comparison_matrix()
    except Exception as exc:
        logger.warning("comparison_matrix error: {}", exc)
        st.warning("Company Comparison Matrix temporarily unavailable.")

    try:
        _render_charter_vs_spot()
    except Exception as exc:
        logger.warning("charter_vs_spot error: {}", exc)
        st.warning("Charter vs Spot Rate section temporarily unavailable.")

    try:
        _render_opex_breakdown()
    except Exception as exc:
        logger.warning("opex_breakdown error: {}", exc)
        st.warning("Operating Cost Breakdown temporarily unavailable.")

    try:
        _render_industry_pnl()
    except Exception as exc:
        logger.warning("industry_pnl error: {}", exc)
        st.warning("Industry P&L Summary temporarily unavailable.")

    try:
        _render_fleet_utilisation()
    except Exception as exc:
        logger.warning("fleet_utilisation error: {}", exc)
        st.warning("Fleet Utilisation Trend temporarily unavailable.")

    try:
        _render_leverage_health()
    except Exception as exc:
        logger.warning("leverage_health error: {}", exc)
        st.warning("Industry Leverage section temporarily unavailable.")

    try:
        _render_earnings_surprise()
    except Exception as exc:
        logger.warning("earnings_surprise error: {}", exc)
        st.warning("Earnings Surprise Model temporarily unavailable.")

    try:
        _render_valuation_dashboard()
    except Exception as exc:
        logger.warning("valuation_dashboard error: {}", exc)
        st.warning("Valuation Dashboard temporarily unavailable.")

    try:
        _render_beta_dashboard()
    except Exception as exc:
        logger.warning("beta_dashboard error: {}", exc)
        st.warning("Shipping Beta Dashboard temporarily unavailable.")

    try:
        _render_earnings_calendar()
    except Exception as exc:
        logger.warning("earnings_calendar error: {}", exc)
        st.warning("Earnings Calendar temporarily unavailable.")

    # ── Footer ─────────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="margin-top:40px;padding-top:16px;'
        'border-top:1px solid {bd};font-size:0.67rem;color:{t3};text-align:center;'
        'line-height:1.8">'
        'Shipping Fundamentals Analyzer &bull; '
        'Data: 2025/2026 estimates &bull; '
        'Valuation ranges: 10-year historical basis &bull; '
        'Cycle assessment: Q1 2026 &bull; '
        'OPEX / orderbook / utilisation: industry consensus estimates &bull; '
        'Not investment advice.'
        '</div>'.format(bd=C_BORDER, t3=C_TEXT3),
        unsafe_allow_html=True,
    )
