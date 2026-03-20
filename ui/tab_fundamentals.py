"""
Fundamentals Tab — Bloomberg-Terminal-level shipping stock analysis.

Sections
--------
1.  Shipping Cycle Indicator  — dial + history + supporting data
2.  Company Comparison Matrix — side-by-side colour-coded table
3.  Earnings Surprise Model   — beat/miss history + rate sensitivity
4.  Valuation Dashboard       — EV/EBITDA, P/B, yield gauges
5.  Shipping Beta Dashboard   — multi-factor bar chart
6.  Earnings Calendar         — next 90-day countdown badges

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

# Per-ticker accent colours for charts
TICKER_COLORS: dict[str, str] = {
    "ZIM":  "#3b82f6",
    "MATX": "#10b981",
    "SBLK": "#f59e0b",
    "DAC":  "#8b5cf6",
    "CMRE": "#06b6d4",
}

# Phase visual config
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
    """Format *value* with *fmt*, or return 'N/A' if value is None or NaN."""
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


def _divider(label: str) -> None:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin:28px 0">'
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        '<span style="font-size:0.65rem;color:{};text-transform:uppercase;'
        'letter-spacing:0.12em">{}</span>'
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        '</div>'.format(C_TEXT3, label),
        unsafe_allow_html=True,
    )


def _dark_layout(height: int = 400, title: str = "") -> dict:
    t = {"text": title, "font": {"size": 13, "color": C_TEXT2}, "x": 0.01} if title else {}
    return dict(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
        title=t,
        height=height,
        margin=dict(l=20, r=20, t=40 if title else 20, b=20),
        hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12)),
    )


def _stat_kpi(label: str, value: str, sub: str = "", color: str = C_ACCENT) -> str:
    sub_html = (
        '<div style="font-size:0.7rem;color:{};margin-top:2px">{}</div>'.format(C_TEXT3, sub)
        if sub else ""
    )
    return (
        '<div style="background:{};border:1px solid {};'
        'border-top:2px solid {};border-radius:10px;'
        'padding:14px 16px;text-align:center">'
        '<div style="font-size:0.62rem;font-weight:700;color:{};'
        'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">{}</div>'
        '<div style="font-size:1.3rem;font-weight:800;color:{};line-height:1">{}</div>'
        '{}'
        '</div>'.format(C_CARD, C_BORDER, color, C_TEXT3, label, C_TEXT, value, sub_html)
    )


# ── Section 1: Shipping Cycle Indicator ───────────────────────────────────────

def _render_shipping_cycle() -> None:
    """Large visual: 4-phase dial + supporting indicators + historical overlay."""
    _divider("Shipping Cycle Indicator")
    cycle = get_current_shipping_cycle()
    pc = PHASE_CONFIG.get(cycle.phase, {"color": C_TEXT, "icon": "→", "order": 0})
    phase_color = pc["color"]

    # ── Phase Hero Banner ──────────────────────────────────────────────────────
    years_min, years_max = cycle.typical_cycle_length_yrs
    st.markdown(
        '<div style="background:linear-gradient(135deg,{},{});'
        'border:1px solid {};border-top:4px solid {};'
        'border-radius:14px;padding:24px 28px;margin-bottom:16px">'
        '<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        '  <div>'
        '    <div style="font-size:0.65rem;color:{};text-transform:uppercase;'
        '      letter-spacing:0.1em;margin-bottom:6px">Current Shipping Cycle Phase</div>'
        '    <div style="font-size:2.2rem;font-weight:900;color:{};'
        '      text-shadow:0 0 30px {}">'
        '      {} {}'
        '    </div>'
        '    <div style="font-size:0.82rem;color:{};margin-top:8px;'
        '      max-width:600px;line-height:1.5">{}</div>'
        '  </div>'
        '  <div style="text-align:right;flex-shrink:0;margin-left:24px">'
        '    <div style="font-size:0.65rem;color:{};text-transform:uppercase;margin-bottom:4px">'
        '      Phase Duration</div>'
        '    <div style="font-size:1.5rem;font-weight:800;color:{}">'
        '      {:.1f} yrs</div>'
        '    <div style="font-size:0.72rem;color:{};margin-top:2px">'
        '      Typical: {}-{} yrs cycle</div>'
        '    <div style="margin-top:10px">'
        '      <span style="background:{};color:{};'
        '        padding:3px 12px;border-radius:999px;'
        '        font-size:0.72rem;font-weight:700">{} confidence</span>'
        '    </div>'
        '  </div>'
        '</div>'
        '</div>'.format(
            C_CARD, C_SURFACE,
            _hex_to_rgba(phase_color, 0.4), phase_color,
            C_TEXT3,
            phase_color, _hex_to_rgba(phase_color, 0.4),
            pc["icon"], cycle.phase,
            C_TEXT2, cycle.phase_description,
            C_TEXT3,
            phase_color, cycle.years_in_current_phase,
            C_TEXT3, years_min, years_max,
            _hex_to_rgba(phase_color, 0.2), phase_color,
            cycle.phase_confidence,
        ),
        unsafe_allow_html=True,
    )

    # ── Four-Quadrant Dial (Plotly polar / pie approximation) ─────────────────
    col_dial, col_indicators = st.columns([1, 1])

    with col_dial:
        _render_cycle_dial(cycle.phase, phase_color)

    with col_indicators:
        # Key data points
        bdi_color = C_HIGH if cycle.bdi_vs_longterm_avg_pct >= 0 else C_LOW
        rate_color = C_HIGH if cycle.rate_vs_longterm_avg_pct >= 0 else C_LOW
        util_color = C_HIGH if cycle.fleet_utilization_pct >= 88 else (
            C_MOD if cycle.fleet_utilization_pct >= 84 else C_LOW
        )
        ob_color = C_LOW if cycle.orderbook_to_fleet_pct > 20 else (
            C_MOD if cycle.orderbook_to_fleet_pct > 12 else C_HIGH
        )

        c1, c2 = st.columns(2)
        c1.markdown(_stat_kpi("Baltic Dry Index", "{:,}".format(cycle.bdi_level),
                               "{:+.0f}% vs LT avg".format(cycle.bdi_vs_longterm_avg_pct),
                               bdi_color), unsafe_allow_html=True)
        c2.markdown(_stat_kpi("TP Container Rate", "${:,}/FEU".format(cycle.transpacific_rate_usd),
                               "{:+.0f}% vs LT avg".format(cycle.rate_vs_longterm_avg_pct),
                               rate_color), unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        c3, c4 = st.columns(2)
        c3.markdown(_stat_kpi("Fleet Utilisation", "{:.1f}%".format(cycle.fleet_utilization_pct),
                               "Tight threshold: 88%", util_color), unsafe_allow_html=True)
        c4.markdown(_stat_kpi("Orderbook/Fleet", "{:.1f}%".format(cycle.orderbook_to_fleet_pct),
                               "Supply pressure indicator", ob_color), unsafe_allow_html=True)

        # Next phase
        next_pc = PHASE_CONFIG.get(cycle.next_phase, {"color": C_TEXT2})
        st.markdown(
            '<div style="background:{};border:1px solid {};'
            'border-radius:10px;padding:12px 16px;margin-top:12px">'
            '<div style="font-size:0.62rem;color:{};text-transform:uppercase;'
            'letter-spacing:0.08em;margin-bottom:4px">Anticipated Next Phase</div>'
            '<div style="font-size:1rem;font-weight:700;color:{}">{}</div>'
            '<div style="font-size:0.72rem;color:{};margin-top:4px">{}</div>'
            '</div>'.format(
                C_CARD, C_BORDER,
                C_TEXT3,
                next_pc["color"], cycle.next_phase,
                C_TEXT3, cycle.investment_implication[:120] + "...",
            ),
            unsafe_allow_html=True,
        )

    # ── Supporting indicators list ────────────────────────────────────────────
    with st.expander("Supporting Data Points", expanded=False, key="fundamentals_cycle_supporting_data"):
        for indicator in cycle.supporting_indicators:
            st.markdown(
                '<div style="display:flex;align-items:flex-start;gap:8px;'
                'padding:6px 0;border-bottom:1px solid {}">'
                '<span style="color:{};font-size:0.9rem;margin-top:2px">▸</span>'
                '<span style="font-size:0.82rem;color:{};line-height:1.45">{}</span>'
                '</div>'.format(C_BORDER, phase_color, C_TEXT2, indicator),
                unsafe_allow_html=True,
            )

    # ── Historical Cycle Overlay (2010–2026) ──────────────────────────────────
    _render_cycle_history(cycle)


def _render_cycle_dial(current_phase: str, phase_color: str) -> None:
    """Render a 4-quadrant semicircle gauge showing cycle position."""
    phase_order = {"TROUGH": 0, "RECOVERY": 1, "PEAK": 2, "DECLINE": 3}
    phase_idx = phase_order.get(current_phase, 0)

    # Each phase covers 45 degrees of a 180-degree arc (bottom semicircle)
    # We draw 4 coloured segments and a needle
    fig = go.Figure()

    segment_colors = [
        _hex_to_rgba(C_LOW,  0.25),   # TROUGH
        _hex_to_rgba(C_HIGH, 0.25),   # RECOVERY
        _hex_to_rgba(C_MOD,  0.25),   # PEAK
        _hex_to_rgba(C_CONV, 0.25),   # DECLINE
    ]
    segment_colors_solid = [C_LOW, C_HIGH, C_MOD, C_CONV]
    phase_labels = ["TROUGH", "RECOVERY", "PEAK", "DECLINE"]

    # Draw segments as pie slices (upper half, so values reversed for display)
    values = [1, 1, 1, 1]   # equal segments
    label_colors = segment_colors_solid

    fig.add_trace(go.Pie(
        values=values,
        labels=phase_labels,
        hole=0.55,
        rotation=90,            # Start from left (TROUGH on far left)
        direction="clockwise",
        marker=dict(
            colors=segment_colors,
            line=dict(color=C_SURFACE, width=3),
        ),
        textinfo="label",
        textfont=dict(size=10, color=C_TEXT2, family="Inter"),
        hoverinfo="label",
        pull=[0.05 if i == phase_idx else 0 for i in range(4)],
    ))

    # Active segment highlight
    active_colors = [
        segment_colors_solid[i] if i == phase_idx else segment_colors[i]
        for i in range(4)
    ]
    fig.add_trace(go.Pie(
        values=values,
        labels=phase_labels,
        hole=0.55,
        rotation=90,
        direction="clockwise",
        marker=dict(
            colors=active_colors,
            line=dict(color=C_SURFACE, width=3),
        ),
        textinfo="none",
        hoverinfo="skip",
        showlegend=False,
    ))

    # Centre annotation
    fig.update_layout(
        **_dark_layout(height=300),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=-0.15,
            xanchor="center", x=0.5,
        ),
        annotations=[dict(
            text="<b>{}</b>".format(current_phase),
            x=0.5, y=0.5,
            font=dict(size=16, color=phase_color, family="Inter"),
            showarrow=False,
        )],
    )
    st.plotly_chart(fig, use_container_width=True, key="cycle_dial")


def _render_cycle_history(cycle: Any) -> None:
    """Bar/line chart of historical BDI by year, colour-coded by phase."""
    history = cycle.cycle_history
    if not history:
        return

    years = [h["year"] for h in history]
    bdis  = [h["bdi_approx"] for h in history]
    phases = [h["phase"] for h in history]
    notes  = [h.get("notes", "") for h in history]

    colors = [PHASE_CONFIG.get(p, {"color": C_TEXT3})["color"] for p in phases]

    fig = go.Figure()

    # BDI bars coloured by phase
    for i in range(len(years)):
        fig.add_trace(go.Bar(
            x=[years[i]],
            y=[bdis[i]],
            name=phases[i],
            marker_color=_hex_to_rgba(colors[i], 0.75),
            hovertemplate=(
                "<b>{}</b><br>BDI: {:,}<br>Phase: {}<br>{}<extra></extra>".format(
                    years[i], bdis[i], phases[i], notes[i]
                )
            ),
            showlegend=(i == 0 or phases[i] != phases[i - 1]),
            legendgroup=phases[i],
            legendgrouptitle=dict(text="") if i == 0 else None,
        ))

    # Long-term average line
    lt_avg = 1_500
    fig.add_hline(
        y=lt_avg, line_dash="dot", line_color=C_MOD, line_width=1.5,
        annotation_text="  LT Avg {:,}".format(lt_avg),
        annotation_position="right",
        annotation_font=dict(color=C_MOD, size=10),
    )

    # Current year marker
    current_year = 2026
    if current_year in years:
        idx = years.index(current_year)
        fig.add_trace(go.Scatter(
            x=[current_year],
            y=[bdis[idx]],
            mode="markers",
            marker=dict(color=C_ACCENT, size=12, symbol="diamond",
                        line=dict(color="white", width=2)),
            name="Current",
            hoverinfo="skip",
        ))

    layout = _dark_layout(height=300, title="Historical Shipping Cycle  |  BDI by Year (2010-2026)")
    layout.update(dict(
        barmode="group",
        xaxis=dict(
            tickvals=years,
            ticktext=[str(y) for y in years],
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        yaxis=dict(
            title="Baltic Dry Index",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        showlegend=True,
        legend=dict(
            font=dict(color=C_TEXT2, size=10),
            bgcolor="rgba(0,0,0,0)",
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="cycle_history")


# ── Section 2: Company Comparison Matrix ──────────────────────────────────────

def _render_comparison_matrix() -> None:
    """Side-by-side colour-coded table, sorted by analyst upside."""
    _divider("Company Comparison Matrix")

    rows = get_fundamentals_summary()   # already sorted by upside desc

    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:12px">'
        'Five shipping stocks ranked by analyst upside. '
        'Green = best in class per metric; Red = worst.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    # Build HTML table with per-column colouring
    metrics_to_show = [
        ("Revenue ($B)",   True,  "${:.2f}B"),    # Higher = better (True)
        ("EBITDA Margin %", True, "{:.1f}%"),
        ("Net Debt ($B)",  False, "${:.2f}B"),    # Lower debt = better
        ("EV/EBITDA",      False, "{:.1f}x"),
        ("P/B",            False, "{:.2f}x"),
        ("Div Yield %",    True,  "{:.1f}%"),
        ("Upside %",       True,  "{:.1f}%"),
    ]

    tickers = [r["Ticker"] for r in rows]
    ticker_colors_list = [TICKER_COLORS.get(t, C_ACCENT) for t in tickers]

    # Colour scaling: find min/max per metric — skip None values
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

    # Build the grid
    header_html = "".join(
        '<th style="background:{};color:{};padding:10px 14px;'
        'font-size:0.72rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:0.07em;border-bottom:1px solid {}">{}</th>'.format(
            _hex_to_rgba(tc, 0.15), tc, C_BORDER, t,
        )
        for t, tc in zip(tickers, ticker_colors_list)
    )

    rows_html_parts = []
    for metric_key, higher_better, fmt in metrics_to_show:
        vals_raw = [r[metric_key] for r in rows]
        cells = ""
        for i, row in enumerate(rows):
            v = row[metric_key]
            col = _gradient(v, vals_raw, higher_better)
            cells += (
                '<td style="padding:9px 14px;text-align:center;'
                'font-size:0.82rem;font-weight:700;color:{};'
                'border-bottom:1px solid {}">{}</td>'.format(
                    col, C_BORDER, _fmt_or_na(v, fmt),
                )
            )
        rows_html_parts.append(
            '<tr>'
            '<td style="padding:9px 14px;font-size:0.75rem;font-weight:600;'
            'color:{};white-space:nowrap;border-bottom:1px solid {}">{}</td>'
            '{}'
            '</tr>'.format(C_TEXT3, C_BORDER, metric_key, cells)
        )

    # Rating row
    rating_cells = ""
    for row in rows:
        rc = RATING_COLORS.get(row["Rating"], C_TEXT3)
        rating_cells += (
            '<td style="padding:9px 14px;text-align:center;'
            'border-bottom:1px solid {}">'
            '<span style="background:{};color:{};'
            'padding:2px 10px;border-radius:999px;'
            'font-size:0.72rem;font-weight:700">{}</span>'
            '</td>'.format(C_BORDER, _hex_to_rgba(rc, 0.2), rc, row["Rating"])
        )
    rows_html_parts.append(
        '<tr>'
        '<td style="padding:9px 14px;font-size:0.75rem;font-weight:600;'
        'color:{};border-bottom:1px solid {}">Rating</td>'
        '{}</tr>'.format(C_TEXT3, C_BORDER, rating_cells)
    )

    # Valuation zone row
    zone_cells = ""
    for row in rows:
        zc = ZONE_COLORS.get(row["Valuation Zone"], C_TEXT3)
        zone_cells += (
            '<td style="padding:9px 14px;text-align:center;'
            'border-bottom:1px solid {}">'
            '<span style="font-size:0.75rem;font-weight:700;color:{}">{}</span>'
            '</td>'.format(C_BORDER, zc, row["Valuation Zone"])
        )
    rows_html_parts.append(
        '<tr>'
        '<td style="padding:9px 14px;font-size:0.75rem;font-weight:600;'
        'color:{};border-bottom:1px solid {}">EV/EBITDA Zone</td>'
        '{}</tr>'.format(C_TEXT3, C_BORDER, zone_cells)
    )

    table_html = (
        '<div style="overflow-x:auto;border-radius:12px;'
        'border:1px solid {};background:{}">'
        '<table style="width:100%;border-collapse:collapse">'
        '<thead><tr>'
        '<th style="padding:10px 14px;font-size:0.72rem;font-weight:700;'
        'color:{};text-transform:uppercase;letter-spacing:0.07em;'
        'border-bottom:1px solid {}">Metric</th>'
        '{}'
        '</tr></thead>'
        '<tbody>{}</tbody>'
        '</table></div>'.format(
            C_BORDER, C_CARD,
            C_TEXT3, C_BORDER,
            header_html,
            "".join(rows_html_parts),
        )
    )
    st.markdown(table_html, unsafe_allow_html=True)

    st.markdown(
        '<div style="font-size:0.68rem;color:{};margin-top:8px">'
        'Green = best in class per metric | Red = worst in class | '
        'Valuation Zone based on EV/EBITDA vs 10-year historical range.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    # CSV export
    matrix_df = pd.DataFrame(rows)
    st.download_button(
        label="Download Comparison Matrix CSV",
        data=matrix_df.to_csv(index=False).encode("utf-8"),
        file_name="shipping_comparison_matrix.csv",
        mime="text/csv",
        key="fundamentals_matrix_download",
    )


# ── Section 3: Earnings Surprise Model ────────────────────────────────────────

def _render_earnings_surprise() -> None:
    """Per-company earnings beat/miss history + rate sensitivity."""
    _divider("Earnings Surprise Model")

    tickers = list(EARNINGS_HISTORY.keys())
    selected = st.selectbox(
        "Select Company",
        options=tickers,
        format_func=lambda t: "{} — {}".format(t, COMPANY_FUNDAMENTALS[t].company_name),
        key="earnings_ticker_select",
        label_visibility="collapsed",
    )

    history = EARNINGS_HISTORY.get(selected, [])
    fund = COMPANY_FUNDAMENTALS[selected]
    sensitivity = RATE_TO_EPS_SENSITIVITY_100FEU.get(selected, 0.0)
    tc = TICKER_COLORS.get(selected, C_ACCENT)

    # ── Beat/Miss Chart ────────────────────────────────────────────────────────
    quarters    = [h.quarter for h in history]
    reported    = [h.reported_eps for h in history]
    consensus   = [h.consensus_eps for h in history]
    beat_pcts   = [h.beat_pct for h in history]
    tp_rates    = [h.freight_rate_at_report for h in history]
    bdi_vals    = [h.bdi_at_report for h in history]

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

    # EPS bars — reported
    fig.add_trace(go.Bar(
        x=quarters, y=reported,
        name="Reported EPS",
        marker_color=_hex_to_rgba(tc, 0.80),
        hovertemplate="<b>%{x}</b><br>Reported: $%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    # Consensus line
    fig.add_trace(go.Scatter(
        x=quarters, y=consensus,
        mode="lines+markers",
        name="Consensus EPS",
        line=dict(color=C_TEXT3, width=2, dash="dot"),
        marker=dict(size=7, color=C_TEXT3),
        hovertemplate="<b>%{x}</b><br>Consensus: $%{y:.2f}<extra></extra>",
    ), row=1, col=1)

    # Beat/miss labels
    for i, (q, rep, con, bp) in enumerate(zip(quarters, reported, consensus, beat_pcts)):
        beat_col = C_HIGH if bp >= 0 else C_LOW
        sign = "+" if bp >= 0 else ""
        fig.add_annotation(
            x=q, y=rep + 0.05 * max(reported),
            text="{}{}%".format(sign, round(bp, 1)),
            font=dict(size=9, color=beat_col, family="Inter"),
            showarrow=False,
            row=1, col=1,
        )

    # TP Rate bars
    rate_colors = [_hex_to_rgba(C_ACCENT, 0.65)] * len(quarters)
    fig.add_trace(go.Bar(
        x=quarters, y=tp_rates,
        name="TP Rate ($/FEU)",
        marker_color=rate_colors,
        hovertemplate="<b>%{x}</b><br>TP Rate: $%{y:,.0f}/FEU<extra></extra>",
    ), row=2, col=1)

    layout = _dark_layout(height=460)
    layout.update(dict(
        barmode="group",
        showlegend=True,
        legend=dict(
            font=dict(color=C_TEXT2, size=10),
            bgcolor="rgba(0,0,0,0)",
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        xaxis2=dict(
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        yaxis=dict(
            title="EPS ($)",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        yaxis2=dict(
            title="$/FEU",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
            tickformat="$,.0f",
        ),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="earnings_surprise_chart")

    # ── Sensitivity Cards ──────────────────────────────────────────────────────
    avg_beat = sum(beat_pcts) / len(beat_pcts) if beat_pcts else 0.0
    beat_color = C_HIGH if avg_beat >= 0 else C_LOW

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        _stat_kpi("Avg Beat/Miss", "{:+.1f}%".format(avg_beat),
                   "vs consensus (last 4Q)", beat_color),
        unsafe_allow_html=True,
    )
    c2.markdown(
        _stat_kpi(
            "Rate Sensitivity",
            "${:.3f} EPS".format(sensitivity),
            "per $100/FEU rate change",
            C_ACCENT,
        ),
        unsafe_allow_html=True,
    )
    c3.markdown(
        _stat_kpi(
            "Freight Beta",
            "{:.1f}x".format(compute_shipping_beta(selected)),
            "stock sensitivity to rates",
            tc,
        ),
        unsafe_allow_html=True,
    )

    # ── Rate-to-Earnings interpretation ───────────────────────────────────────
    st.markdown(
        '<div style="background:{};border:1px solid {};border-left:3px solid {};'
        'border-radius:10px;padding:14px 18px;margin-top:12px">'
        '<div style="font-size:0.68rem;font-weight:700;color:{};'
        'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">'
        'Rate → Earnings Interpretation</div>'
        '<div style="font-size:0.82rem;color:{};line-height:1.5">'
        'A $100/FEU change in Trans-Pacific spot rates translates to approximately '
        '<b style="color:{}">${:.3f}</b> in EPS for {}. '
        'At the current primary shipping beta of '
        '<b style="color:{}">{:.1f}x</b>, '
        'a 10% freight rate move implies roughly a '
        '<b style="color:{}">{:.1f}%</b> move in the stock.'
        '</div>'
        '</div>'.format(
            C_CARD, C_BORDER, tc,
            C_TEXT3,
            C_TEXT2, tc, sensitivity, selected,
            tc, compute_shipping_beta(selected),
            tc, compute_shipping_beta(selected) * 10,
        ),
        unsafe_allow_html=True,
    )


# ── Section 4: Valuation Dashboard ────────────────────────────────────────────

def _render_valuation_dashboard() -> None:
    """EV/EBITDA, P/B, dividend yield gauges for each company."""
    _divider("Valuation Dashboard")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px">'
        'Gauge zones: Green = Cheap vs history | Amber = Fair | Red = Expensive.'
        '</div>'.format(C_TEXT3),
        unsafe_allow_html=True,
    )

    # One row per 5 tickers (in upside-sorted order)
    sorted_tickers = [r["Ticker"] for r in get_fundamentals_summary()]

    for ticker in sorted_tickers:
        fund = COMPANY_FUNDAMENTALS[ticker]
        vr   = VALUATION_RANGES[ticker]
        tc   = TICKER_COLORS.get(ticker, C_ACCENT)
        zone = get_valuation_zone(ticker)
        zone_color = ZONE_COLORS.get(zone, C_TEXT3)
        norm_ni = compute_normalized_earnings(ticker, "DECLINE")

        with st.expander(
            "{} — {}   |   {}".format(ticker, fund.company_name, zone),
            expanded=(ticker == sorted_tickers[0]),
            key="valuation_expander_" + ticker,
        ):
            c1, c2, c3 = st.columns(3)

            with c1:
                _render_gauge(
                    label="EV / EBITDA",
                    value=fund.ev_ebitda,
                    cheap=vr.ev_ebitda_cheap,
                    fair_lo=vr.ev_ebitda_fair_lo,
                    fair_hi=vr.ev_ebitda_fair_hi,
                    expensive=vr.ev_ebitda_expensive,
                    fmt="{:.1f}x",
                    higher_expensive=True,
                    key_suffix=ticker + "_evebitda",
                    accent=tc,
                )
            with c2:
                _render_gauge(
                    label="Price / Book",
                    value=fund.price_to_book,
                    cheap=vr.pb_cheap,
                    fair_lo=vr.pb_fair_lo,
                    fair_hi=vr.pb_fair_hi,
                    expensive=vr.pb_expensive,
                    fmt="{:.2f}x",
                    higher_expensive=True,
                    key_suffix=ticker + "_pb",
                    accent=tc,
                )
            with c3:
                _render_gauge(
                    label="Dividend Yield",
                    value=fund.dividend_yield_pct,
                    cheap=vr.yield_expensive,    # High yield = cheap stock
                    fair_lo=vr.yield_fair_hi,
                    fair_hi=vr.yield_fair_lo,
                    expensive=vr.yield_cheap,
                    fmt="{:.1f}%",
                    higher_expensive=False,      # Higher yield = cheaper stock
                    key_suffix=ticker + "_yield",
                    accent=tc,
                )

            # Summary strip — guard zero/None EBITDA and None P/B / debt values
            nd_ebitda = (
                fund.net_debt_b / fund.ebitda_b
                if (fund.ebitda_b is not None and fund.ebitda_b > 0)
                else 0.0
            )
            _pt_str = _fmt_or_na(fund.price_target_usd, "${:.2f}")
            _upside_str = _fmt_or_na(fund.upside_pct, "{:.1f}%")
            _nd_str = "{:.1f}x".format(nd_ebitda)
            rating_color = RATING_COLORS.get(fund.analyst_rating, C_TEXT3)
            _upside_color = (
                C_HIGH if (fund.upside_pct is not None and fund.upside_pct > 15)
                else C_TEXT2
            )
            _ni_str = _fmt_or_na(norm_ni, "${:.3f}B")
            st.markdown(
                '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;'
                'padding-top:12px;border-top:1px solid {}">'
                '<span style="font-size:0.75rem;color:{}">'
                'Analyst: <b style="color:{}">{}</b></span>'
                '<span style="font-size:0.75rem;color:{}">'
                'PT: <b style="color:{}">{}</b></span>'
                '<span style="font-size:0.75rem;color:{}">'
                'Upside: <b style="color:{}">{}</b></span>'
                '<span style="font-size:0.75rem;color:{}">'
                'Net Debt/EBITDA: <b style="color:{}">{}</b></span>'
                '<span style="font-size:0.75rem;color:{}">'
                'Normalised NI: <b style="color:{}">{}</b></span>'
                '</div>'.format(
                    C_BORDER,
                    C_TEXT3, rating_color, fund.analyst_rating,
                    C_TEXT3, tc, _pt_str,
                    C_TEXT3, _upside_color, _upside_str,
                    C_TEXT3, C_MOD if nd_ebitda > 3 else C_TEXT2, _nd_str,
                    C_TEXT3, tc, _ni_str,
                ),
                unsafe_allow_html=True,
            )


def _render_gauge(
    label: str,
    value: float | None,
    cheap: float,
    fair_lo: float,
    fair_hi: float,
    expensive: float,
    fmt: str,
    higher_expensive: bool,
    key_suffix: str,
    accent: str,
) -> None:
    """Render a Plotly bullet/indicator gauge with cheap/fair/expensive zones."""
    if value is None:
        st.markdown(
            '<div style="background:{};border:1px solid {};border-radius:10px;'
            'padding:20px;text-align:center;color:{};font-size:0.82rem">'
            '<div style="font-size:0.68rem;font-weight:700;color:{};'
            'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">{}</div>'
            'N/A — data unavailable'
            '</div>'.format(C_CARD, C_BORDER, C_TEXT3, C_TEXT3, label),
            unsafe_allow_html=True,
        )
        return

    # Determine zone color for needle
    if higher_expensive:
        needle_color = (
            C_HIGH if value <= fair_lo else
            (C_MOD if value <= fair_hi else C_LOW)
        )
    else:
        needle_color = (
            C_HIGH if value >= fair_lo else
            (C_MOD if value >= fair_hi else C_LOW)
        )

    gauge_range = [cheap, expensive] if higher_expensive else [expensive, cheap]
    g_min = min(gauge_range) * 0.9
    g_max = max(gauge_range) * 1.1

    steps = [
        dict(range=[g_min, fair_lo if higher_expensive else fair_hi],
             color=_hex_to_rgba(C_HIGH, 0.15)),
        dict(range=[fair_lo if higher_expensive else fair_hi,
                    fair_hi if higher_expensive else fair_lo],
             color=_hex_to_rgba(C_MOD, 0.15)),
        dict(range=[fair_hi if higher_expensive else fair_lo, g_max],
             color=_hex_to_rgba(C_LOW, 0.15)),
    ]

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number=dict(font=dict(size=24, color=needle_color, family="Inter"),
                    suffix="" if "%" not in fmt else "%",
                    valueformat=".1f"),
        title=dict(text=label, font=dict(size=11, color=C_TEXT3, family="Inter")),
        gauge=dict(
            axis=dict(
                range=[g_min, g_max],
                tickfont=dict(size=9, color=C_TEXT3),
                nticks=5,
                tickcolor=C_TEXT3,
            ),
            bar=dict(color=accent, thickness=0.25),
            bgcolor=C_SURFACE,
            borderwidth=1,
            bordercolor=C_BORDER,
            steps=steps,
            threshold=dict(
                line=dict(color=needle_color, width=3),
                thickness=0.75,
                value=value,
            ),
        ),
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C_TEXT, family="Inter"),
        height=200,
        margin=dict(l=10, r=10, t=30, b=10),
    )
    st.plotly_chart(fig, use_container_width=True, key="gauge_{}".format(key_suffix))


# ── Section 5: Shipping Beta Dashboard ────────────────────────────────────────

def _render_beta_dashboard() -> None:
    """Multi-factor bar chart: each stock's beta to BDI, TP rates, oil, PMI."""
    _divider("Shipping Beta Dashboard")

    st.markdown(
        '<div style="font-size:0.78rem;color:{};margin-bottom:16px">'
        'Beta = % stock move per 1% move in each indicator. '
        'Positive = moves with indicator; Negative = moves against.'
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
        betas = get_all_betas(ticker)
        beta_vals = [betas.get(f, 0.0) for f in factors]
        tc = TICKER_COLORS.get(ticker, C_ACCENT)

        fig.add_trace(go.Bar(
            name=ticker,
            x=[factor_labels[f] for f in factors],
            y=beta_vals,
            marker_color=_hex_to_rgba(tc, 0.80),
            hovertemplate=(
                "<b>{}</b><br>%{{x}}: %{{y:.2f}}x beta<extra></extra>".format(ticker)
            ),
        ))

    # Zero line
    fig.add_hline(y=0, line_color=C_BORDER, line_width=1)

    layout = _dark_layout(height=400, title="Shipping Beta by Factor  |  Stock % Move per 1% Factor Move")
    layout.update(dict(
        barmode="group",
        showlegend=True,
        legend=dict(
            font=dict(color=C_TEXT2, size=11),
            bgcolor="rgba(0,0,0,0)",
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        yaxis=dict(
            title="Beta (x)",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
            zeroline=True,
            zerolinecolor=C_BORDER,
        ),
    ))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, key="beta_dashboard")

    # Interpretation cards
    interpretations = {
        "ZIM": (
            "ZIM is 2.5x levered to freight rates — the most rate-sensitive name in coverage. "
            "A 10% drop in Trans-Pacific spot rates would typically imply a ~25% equity decline. "
            "Asset-light model means fast earnings erosion in downturns but rapid recovery at peaks."
        ),
        "MATX": (
            "MATX has the lowest rate beta (0.8x) due to its near-monopoly domestic US routes "
            "and Jones Act protection. Hawaii/Alaska cargo demand is relatively inelastic, "
            "insulating earnings from global freight cycles."
        ),
        "SBLK": (
            "SBLK's primary driver is the BDI (1.8x beta), not container rates. "
            "Capesize and supramax dry bulk demand tracks Chinese steel/iron ore imports. "
            "Oil price headwind (-0.3x) reflects fuel-cost sensitivity on owned vessels."
        ),
        "DAC": (
            "DAC has very low spot-rate exposure (0.6x) because 90%+ of revenue is locked "
            "into long-term charters. Rate beta is indirect — new charter fixing rates follow "
            "spot markets with a 12-24 month lag."
        ),
        "CMRE": (
            "CMRE shows moderate betas across all factors, reflecting its diversified "
            "container + dry-bulk fleet. PMI sensitivity (0.9x) captures trade-volume exposure "
            "across both segments."
        ),
    }

    cols = st.columns(len(tickers))
    for col, ticker in zip(cols, tickers):
        tc = TICKER_COLORS.get(ticker, C_ACCENT)
        col.markdown(
            '<div style="background:{};border:1px solid {};'
            'border-top:3px solid {};border-radius:10px;'
            'padding:12px 14px;height:100%">'
            '<div style="font-size:0.72rem;font-weight:800;color:{};'
            'margin-bottom:6px">{}</div>'
            '<div style="font-size:0.72rem;color:{};line-height:1.45">{}</div>'
            '</div>'.format(
                C_CARD, C_BORDER, tc,
                tc, ticker,
                C_TEXT2, interpretations.get(ticker, ""),
            ),
            unsafe_allow_html=True,
        )


# ── Section 6: Earnings Calendar ──────────────────────────────────────────────

def _render_earnings_calendar() -> None:
    """Next 90 days of expected earnings dates with countdown badges."""
    _divider("Earnings Calendar — Next 90 Days")

    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=90)

    upcoming = []
    for ticker, fund in COMPANY_FUNDAMENTALS.items():
        ned = fund.next_earnings_date
        days_away = (ned - today).days
        if 0 <= days_away <= 90:
            upcoming.append((days_away, ticker, fund))

    # Sort by days away
    upcoming.sort(key=lambda x: x[0])

    if not upcoming:
        st.markdown(
            '<div style="background:{};border:1px solid {};border-radius:10px;'
            'padding:24px;text-align:center;color:{};font-size:0.88rem">'
            'No earnings dates in the next 90 days for covered companies. '
            'Next cycle begins beyond {:%B %Y}.'
            '</div>'.format(C_CARD, C_BORDER, C_TEXT3, horizon),
            unsafe_allow_html=True,
        )
        return

    # Render timeline
    for days_away, ticker, fund in upcoming:
        tc = TICKER_COLORS.get(ticker, C_ACCENT)
        rating_color = RATING_COLORS.get(fund.analyst_rating, C_TEXT3)

        if days_away == 0:
            countdown_text = "TODAY"
            countdown_color = C_LOW
        elif days_away <= 7:
            countdown_text = "{}d".format(days_away)
            countdown_color = C_MOD
        else:
            countdown_text = "{}d".format(days_away)
            countdown_color = C_TEXT2

        urgency_border = C_LOW if days_away <= 7 else C_BORDER

        st.markdown(
            '<div style="background:{};border:1px solid {};'
            'border-left:4px solid {};border-radius:12px;'
            'padding:16px 20px;margin-bottom:10px;'
            'display:flex;align-items:center;justify-content:space-between;'
            'flex-wrap:wrap;gap:12px">'

            # Left: company info
            '<div style="flex:1;min-width:200px">'
            '  <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">'
            '    <span style="font-size:1.0rem;font-weight:800;color:{}">{}</span>'
            '    <span style="font-size:0.75rem;color:{};'
            '      background:{};padding:2px 8px;border-radius:6px;'
            '      border:1px solid {}">{}</span>'
            '  </div>'
            '  <div style="font-size:0.78rem;color:{}">{}</div>'
            '  <div style="font-size:0.72rem;color:{};margin-top:2px">'
            '    Next earnings: <b style="color:{}">{:%B %d, %Y}</b>'
            '  </div>'
            '</div>'

            # Centre: key metrics
            '<div style="display:flex;gap:20px;flex-wrap:wrap">'
            '  <div style="text-align:center">'
            '    <div style="font-size:0.62rem;color:{};text-transform:uppercase;'
            '      letter-spacing:0.07em;margin-bottom:2px">Rating</div>'
            '    <div style="font-size:0.82rem;font-weight:700;color:{}">{}</div>'
            '  </div>'
            '  <div style="text-align:center">'
            '    <div style="font-size:0.62rem;color:{};text-transform:uppercase;'
            '      letter-spacing:0.07em;margin-bottom:2px">PT ($)</div>'
            '    <div style="font-size:0.82rem;font-weight:700;color:{}">'
            '      ${:.2f}</div>'
            '  </div>'
            '  <div style="text-align:center">'
            '    <div style="font-size:0.62rem;color:{};text-transform:uppercase;'
            '      letter-spacing:0.07em;margin-bottom:2px">Upside</div>'
            '    <div style="font-size:0.82rem;font-weight:700;color:{}">'
            '      {:.1f}%</div>'
            '  </div>'
            '</div>'

            # Right: countdown badge
            '<div style="text-align:center;flex-shrink:0">'
            '  <div style="background:{};border:2px solid {};'
            '    border-radius:12px;padding:10px 18px;'
            '    min-width:70px">'
            '    <div style="font-size:1.4rem;font-weight:900;color:{};line-height:1">'
            '      {}</div>'
            '    <div style="font-size:0.65rem;color:{};'
            '      text-transform:uppercase;letter-spacing:0.07em;margin-top:2px">'
            '      days away</div>'
            '  </div>'
            '</div>'

            '</div>'.format(
                C_CARD, urgency_border, tc,
                # company info
                tc, ticker,
                C_TEXT3, _hex_to_rgba(C_BORDER, 0.5), C_BORDER,
                "Q{}".format(
                    ((fund.next_earnings_date.month - 1) // 3) + 1
                ) + " {}".format(fund.next_earnings_date.year),
                C_TEXT2, fund.company_name,
                C_TEXT3, tc, fund.next_earnings_date,
                # metrics
                C_TEXT3, rating_color, fund.analyst_rating,
                C_TEXT3, tc, fund.price_target_usd,
                C_TEXT3, C_HIGH if fund.upside_pct > 15 else C_TEXT2, fund.upside_pct,
                # countdown
                _hex_to_rgba(countdown_color, 0.12), countdown_color,
                countdown_color, countdown_text,
                C_TEXT3,
            ),
            unsafe_allow_html=True,
        )

    # If some companies have no upcoming dates, show them as beyond horizon
    beyond = [
        (ticker, fund) for ticker, fund in COMPANY_FUNDAMENTALS.items()
        if (fund.next_earnings_date - today).days > 90
        or (fund.next_earnings_date - today).days < 0
    ]
    if beyond:
        st.markdown(
            '<div style="font-size:0.72rem;color:{};margin-top:12px">'
            'Beyond 90-day window: {}'
            '</div>'.format(
                C_TEXT3,
                ", ".join(
                    "{} ({:%b %d})".format(t, f.next_earnings_date)
                    for t, f in beyond
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
        Used for live price context if available; falls back to static fundamentals.
    freight_data:
        Dict route_id -> DataFrame (columns: date, rate_usd_per_feu, source).
        Used to provide rate context for earnings sensitivity.
    macro_data:
        Dict series_id -> DataFrame from FRED / WorldBank.
        Used for macro overlay data if available.
    """
    logger.info(
        "Rendering Fundamentals tab — stock_data tickers={tickers}",
        tickers=list((stock_data or {}).keys()),
    )

    # Warn if yfinance price data is absent; sections fall back to static fundamentals
    if not stock_data:
        st.info(
            "Live price data from yfinance is unavailable. "
            "All sections will display using static fundamental estimates. "
            "Refresh the app to retry fetching live data."
        )

    # ── Tab header ─────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="margin-bottom:20px">'
        '<div style="font-size:1.4rem;font-weight:800;color:{};'
        'letter-spacing:-0.02em">Shipping Stock Fundamentals</div>'
        '<div style="font-size:0.82rem;color:{};margin-top:4px">'
        'Deep fundamental analysis — ZIM · MATX · SBLK · DAC · CMRE &nbsp;|&nbsp; '
        'Q1 2026 View'
        '</div>'
        '</div>'.format(C_TEXT, C_TEXT3),
        unsafe_allow_html=True,
    )

    # ── Section 1: Shipping Cycle ──────────────────────────────────────────────
    _render_shipping_cycle()

    # ── Section 2: Comparison Matrix ──────────────────────────────────────────
    _render_comparison_matrix()

    # ── Section 3: Earnings Surprise ──────────────────────────────────────────
    _render_earnings_surprise()

    # ── Section 4: Valuation Dashboard ────────────────────────────────────────
    _render_valuation_dashboard()

    # ── Section 5: Beta Dashboard ─────────────────────────────────────────────
    _render_beta_dashboard()

    # ── Section 6: Earnings Calendar ──────────────────────────────────────────
    _render_earnings_calendar()

    # ── Footer ────────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="margin-top:32px;padding-top:16px;'
        'border-top:1px solid {};font-size:0.68rem;color:{};text-align:center">'
        'Fundamentals Analyzer &bull; Data: 2025/2026 estimates &bull; '
        'Valuation ranges: 10-year historical basis &bull; '
        'Cycle assessment: Q1 2026 &bull; '
        'Not investment advice.'
        '</div>'.format(C_BORDER, C_TEXT3),
        unsafe_allow_html=True,
    )
