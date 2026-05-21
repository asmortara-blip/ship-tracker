"""ui/tab_nowcast.py — Trade Nowcast tab.

Phase-4 tab. Surfaces processing.leading_indicators's existing machinery:

  • build_leading_indicators(macro_data)        → per-indicator list
  • compute_leading_indicator_score(macro_data) → composite score + counts
  • build_lead_lag_matrix(macro_data, freight)  → indicator × lag heatmap
  • get_recession_probability(macro_data)       → tail-risk gauge

No new model — the analytical work already lives in processing/. This tab
gives the existing machinery a dedicated home and turns the structured
output into a single-screen nowcast: "what does the leading-indicator
picture say about shipping demand 4-12 weeks out?"
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    gauge_ring,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:JetBrains Mono,monospace;color:{color};'
        f'font-size:0.82rem;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 500) -> str:
    return (
        f'<span style="font-family:Libre Franklin,sans-serif;color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _signal_color(signal: str) -> str:
    return {"BULLISH": C_HIGH, "BEARISH": C_LOW, "NEUTRAL": C_MOD}.get(signal, C_TEXT2)


def _forecast_color(forecast: str) -> str:
    return {
        "EXPANSION":   C_HIGH,
        "CONTRACTION": C_LOW,
        "STABLE":      C_MOD,
    }.get(forecast, C_TEXT2)


# ─── Section 1: Composite score hero ────────────────────────────────────────

def _render_composite(score_data: dict, recession_prob: float) -> None:
    """4-card headline strip showing the composite outlook."""
    composite = float(score_data.get("composite_score", 0.5))
    forecast = score_data.get("four_week_forecast", "STABLE")
    bullish_count = int(score_data.get("bullish_count", 0))
    bearish_count = int(score_data.get("bearish_count", 0))
    neutral_count = int(score_data.get("neutral_count", 0))
    weighted_signal = float(score_data.get("weighted_signal", 0.0))

    metric_card_row(
        [
            {"label": "Composite Score",
             "value": f"{composite:.2f}",
             "accent": (
                 C_HIGH if composite >= 0.65 else (
                     C_LOW if composite < 0.40 else C_MOD
                 )
             ),
             "sublabel": "0=bearish · 0.5=neutral · 1=bullish"},
            {"label": "4-Week Forecast",
             "value": forecast,
             "accent": _forecast_color(forecast),
             "sublabel": f"weighted signal {weighted_signal:+.2f}"},
            {"label": "Signal Mix",
             "value": f"{bullish_count} / {neutral_count} / {bearish_count}",
             "accent": (
                 C_HIGH if bullish_count > bearish_count else (
                     C_LOW if bearish_count > bullish_count else C_MOD
                 )
             ),
             "sublabel": "Bull / Neut / Bear indicator count"},
            {"label": "Recession Probability",
             "value": f"{recession_prob*100:.0f}%",
             "accent": (
                 C_LOW if recession_prob >= 0.5 else (
                     C_MOD if recession_prob >= 0.3 else C_HIGH
                 )
             ),
             "sublabel": "From leading-indicator panel"},
        ],
        columns=4,
    )


# ─── Section 2: Per-indicator detail table ──────────────────────────────────

def _render_indicators_table(indicators: list) -> None:
    """Each indicator: name, value, change %, signal, lead time, weight,
    shipping implication."""
    if not indicators:
        st.info(
            "No leading-indicator readings available. Configure FRED feeds "
            "(FRED_API_KEY in st.secrets / env) to populate this panel."
        )
        return

    section_header(
        "Leading Indicators",
        subtitle=(
            "Per-indicator readings. Sorted by absolute weighted signal "
            "contribution — strongest drivers first."
        ),
    )

    # Sort by abs change × weight × lead-time score (rough impact ordering).
    sorted_inds = sorted(
        indicators,
        key=lambda i: abs(getattr(i, "change_pct", 0.0)) * getattr(i, "weight", 0.0),
        reverse=True,
    )

    headers = ["Indicator", "Current", "Prev", "Δ %", "Signal", "Lead", "Weight", "Implication"]
    rows = []
    for ind in sorted_inds:
        signal = getattr(ind, "signal", "NEUTRAL")
        change_pct = float(getattr(ind, "change_pct", 0.0))
        change_color = (
            C_HIGH if change_pct > 0.5 else (C_LOW if change_pct < -0.5 else C_TEXT2)
        )
        rows.append([
            _sans(getattr(ind, "name", ""), color=C_TEXT, weight=600),
            _mono(f"{getattr(ind, 'current_value', 0.0):,.2f}", color=C_TEXT),
            _mono(f"{getattr(ind, 'previous_value', 0.0):,.2f}", color=C_TEXT3),
            _mono(f"{change_pct:+5.2f}%", color=change_color),
            _sans(badge(signal, color=_signal_color(signal)), color=C_TEXT2),
            _mono(f"{getattr(ind, 'lead_time_weeks', 0)}w", color=C_TEXT2),
            _mono(f"{getattr(ind, 'weight', 0.0):.2f}", color=C_TEXT3),
            _sans(
                (getattr(ind, "shipping_implication", "") or "")[:140],
                color=C_TEXT2,
            ),
        ])
    wsj_market_table(headers, rows)


# ─── Section 3: Signal-mix bar chart ────────────────────────────────────────

def _render_signal_mix_chart(indicators: list) -> None:
    """Horizontal bar chart: weighted signal contribution per indicator."""
    if not indicators:
        return

    section_header(
        "Weighted Signal Contributions",
        subtitle=(
            "Each bar shows that indicator's signed contribution to the "
            "composite score (= signal × weight). Color reflects direction."
        ),
    )

    # Compute per-indicator contribution. Use _signal_weight semantics inline:
    sig_to_w = {"BULLISH": +1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}
    contributions = []
    for ind in indicators:
        signal = getattr(ind, "signal", "NEUTRAL")
        weight = float(getattr(ind, "weight", 0.0))
        contrib = sig_to_w.get(signal, 0.0) * weight
        contributions.append((getattr(ind, "name", ""), contrib, signal))
    contributions.sort(key=lambda t: t[1])  # most-bearish first → bottom

    fig = go.Figure(go.Bar(
        x=[c[1] for c in contributions],
        y=[c[0] for c in contributions],
        orientation="h",
        marker_color=[_signal_color(c[2]) for c in contributions],
        text=[f"{c[1]:+.2f}" for c in contributions],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Contribution: %{x:+.2f}<extra></extra>",
    ))
    apply_dark_layout(
        fig, title="Per-Indicator Contribution to Composite Score",
        height=max(280, 26 * len(contributions) + 80),
        margin=dict(l=12, r=80, t=46, b=30),
        xaxis=dict(title=dict(text="Contribution (signal × weight)",
                              font=dict(color=C_TEXT2, size=11))),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ─── Section 4: Lead-lag matrix heatmap ─────────────────────────────────────

def _render_lead_lag_heatmap(macro_data: dict, freight_data: dict) -> None:
    """Build the lead-lag matrix and render as a heatmap."""
    try:
        from processing.leading_indicators import build_lead_lag_matrix
        matrix = build_lead_lag_matrix(macro_data or {}, freight_data or {})
    except Exception as exc:
        logger.debug(f"nowcast: lead-lag matrix failed: {exc}")
        return

    if matrix is None or matrix.empty:
        return

    section_header(
        "Lead-Lag Correlation Matrix",
        subtitle=(
            "Cross-correlation of each leading indicator with BDI at multiple "
            "lag horizons. Strong positive cells = indicator that meaningfully "
            "leads dry-bulk freight conditions."
        ),
    )

    fig = go.Figure(go.Heatmap(
        z=matrix.fillna(0).to_numpy(),
        x=[str(c) for c in matrix.columns],
        y=[str(i) for i in matrix.index],
        zmin=-1.0, zmax=1.0,
        colorscale=[
            [0.0, "#a83232"],       # strong negative
            [0.5, "#1a1d23"],       # zero
            [1.0, "#3572b0"],       # strong positive
        ],
        colorbar=dict(
            title=dict(text="r", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=10),
        ),
        hovertemplate="<b>%{y}</b><br>Lag: %{x}<br>r = %{z:.2f}<extra></extra>",
        showscale=True,
    ))
    apply_dark_layout(
        fig, height=max(320, 26 * len(matrix.index) + 100),
        margin=dict(l=150, r=80, t=20, b=80),
        xaxis=dict(tickfont=dict(size=10, color=C_TEXT2)),
        yaxis=dict(tickfont=dict(size=10, color=C_TEXT2)),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ─── Main render ────────────────────────────────────────────────────────────

def render(
    port_results=None,
    route_results=None,
    insights=None,
    freight_data=None,
    macro_data=None,
    stock_data=None,
    **_kwargs,
) -> None:
    """Render the Trade Nowcast tab."""
    try:
        page_header(
            title="Trade Nowcast",
            subtitle=(
                "Leading-indicator nowcast for shipping demand. Composite "
                "score, per-indicator detail, weighted contributions, and "
                "lead-lag matrix vs BDI."
            ),
            badge_text="NOWCAST",
            badge_color=C_ACCENT,
        )

        # Build the analytical inputs.
        try:
            from processing.leading_indicators import (
                build_leading_indicators,
                compute_leading_indicator_score,
                get_recession_probability,
            )
            indicators = build_leading_indicators(macro_data or {})
            score_data = compute_leading_indicator_score(macro_data or {})
            recession_prob = float(get_recession_probability(macro_data or {}))
        except Exception as exc:
            logger.exception(f"nowcast: model assembly failed: {exc}")
            st.error("Trade Nowcast could not assemble the leading indicators.")
            return

        # ── 1. Composite headline ──────────────────────────────────────────
        _render_composite(score_data, recession_prob)

        # ── 2. Top-bullish / top-bearish callouts ──────────────────────────
        top_bull = score_data.get("top_bullish_indicators", []) or []
        top_bear = score_data.get("top_bearish_indicators", []) or []
        if top_bull or top_bear:
            cols = st.columns(2, gap="medium")
            with cols[0]:
                if top_bull:
                    st.markdown(
                        insight_card_html(
                            title=f"Bullish leaders: {', '.join(top_bull[:3])}",
                            score=min(1.0, 0.5 + 0.1 * len(top_bull)),
                            action="Watch",
                            category="EXPANSION",
                        ),
                        unsafe_allow_html=True,
                    )
            with cols[1]:
                if top_bear:
                    st.markdown(
                        insight_card_html(
                            title=f"Bearish drags: {', '.join(top_bear[:3])}",
                            score=min(1.0, 0.5 + 0.1 * len(top_bear)),
                            action="Watch",
                            category="CONTRACTION",
                        ),
                        unsafe_allow_html=True,
                    )

        section_divider("Indicators")
        _render_indicators_table(indicators)

        section_divider("Contributions")
        _render_signal_mix_chart(indicators)

        section_divider("Lead-Lag Matrix")
        _render_lead_lag_heatmap(macro_data or {}, freight_data or {})

        st.markdown(
            source_footer([
                DataSource.modeled(
                    "Trade Nowcast",
                    notes=(
                        "Composite from processing.leading_indicators "
                        "(15+ FRED series, weighted by importance + lead "
                        "time). Recession probability from the indicator "
                        "panel's tail-risk scoring."
                    ),
                ),
            ]),
            unsafe_allow_html=True,
        )

    except Exception:
        logger.exception("tab_nowcast render failed")
        st.error("Trade Nowcast encountered an error. See logs.")
