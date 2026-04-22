"""tab_bellwethers.py — WSJ-style Trade Bellwether Indicators & Earnings Calendar.

Sections:
  1. Page header
  2. Composite bellwether KPI strip
  3. Indicator breakdown table
  4. Yield curve chart + implication callout + spread KPIs
  5. Upcoming shipping earnings calendar
"""
from __future__ import annotations

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


# ── Domain colour mappings ─────────────────────────────────────────────────
# Score → semantic palette. Kept local because "bellwether score" is
# domain-specific; the palette constants live in ``ui/styles.py``.
def _score_color(score: float) -> str:
    if score >= 0.65:
        return C_HIGH
    if score >= 0.45:
        return C_MOD
    return C_LOW


def _composite_label(score: float) -> str:
    if score >= 0.65:
        return "Bullish signals dominate leading indicators"
    if score >= 0.45:
        return "Mixed signals warrant cautious positioning"
    return "Bearish undertones in economic bellwethers"


def _spread_color(spread_val: float) -> str:
    if spread_val < 0:
        return C_LOW
    if spread_val < 0.5:
        return C_MOD
    return C_HIGH


def _urgency_color(days: int) -> str:
    if days <= 7:
        return C_LOW
    if days <= 30:
        return C_MOD
    return C_TEXT2


def _regime_from_label(label: str) -> str:
    """Normalise composite_label to a short badge label."""
    low = (label or "").lower()
    if "bull" in low or "expansion" in low or "growth" in low:
        return "BULLISH"
    if "bear" in low or "contraction" in low:
        return "BEARISH"
    return "MIXED"


# ── Cell formatters for WSJ market tables ──────────────────────────────────
def _mono(value: str, color: str = C_TEXT, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ── Data-source declarations ───────────────────────────────────────────────
# Bellwether indicators are sourced from FRED macro data; the earnings
# calendar is a hand-curated schedule.
_FRED_SRC = DataSource.cached(
    "FRED Macro",
    age_hours=6.0,
    url="https://fred.stlouisfed.org",
    sla_hours=24.0,
    notes="Yield curve, PMI, housing, sentiment, trade balance",
)
_EARNINGS_SRC = DataSource.scraped(
    "Earnings Calendar",
    url="https://www.nasdaq.com/market-activity/earnings",
    notes="Curated schedule for tracked shipping companies",
)


# ── Public entry point ─────────────────────────────────────────────────────

def render(macro_data=None, **kwargs) -> None:
    """Render the Trade Bellwethers dashboard."""
    try:
        from processing.trade_bellwethers import (
            compute_bellwether_score,
            compute_earnings_calendar,
            compute_yield_curve_analysis,
        )
    except ImportError as e:
        st.error(f"Trade bellwethers module not available: {e}")
        return

    macro_data = macro_data or {}

    # ── 1. Page header ─────────────────────────────────────────────────────
    page_header(
        title="Trade Bellwether Index",
        subtitle="Composite leading indicator for global shipping demand",
        badge_text="LEADING",
        badge_color=C_ACCENT,
    )

    # ── 2. Composite bellwether score (KPI strip + editorial narrative) ────
    bell = compute_bellwether_score(macro_data)
    score = bell["composite_score"]
    label = bell["composite_label"]
    sc = _score_color(score)

    metric_card_row(
        [
            {
                "label":    "Composite Score",
                "value":    f"{score:.0%}",
                "accent":   sc,
                "sublabel": label.upper(),
            },
            {
                "label":    "Regime",
                "value":    _regime_from_label(label),
                "accent":   sc,
                "sublabel": "Derived from composite",
            },
            {
                "label":    "Indicators Tracked",
                "value":    f"{len(bell.get('indicators', {}))}",
                "accent":   C_ACCENT,
                "sublabel": "Leading macro series",
            },
        ],
        columns=3,
    )
    st.html(live_data_badge(_FRED_SRC))

    # Editorial narrative (insight card pattern)
    st.markdown(
        insight_card_html(
            title=_composite_label(score),
            score=score,
            action=_regime_from_label(label),
            rationale=bell.get("narrative", "Narrative unavailable."),
            category="BELLWETHER",
        ),
        unsafe_allow_html=True,
    )

    # ── 3. Indicator breakdown ─────────────────────────────────────────────
    indicators = bell.get("indicators", {})
    if indicators:
        section_header(
            "Indicator Breakdown",
            subtitle="Component scores driving the composite reading",
        )

        rows = []
        for key, ind in indicators.items():
            raw = ind.get("raw")
            raw_str = f"{raw:.2f}" if raw is not None else "--"
            unit = ind.get("unit", "")
            if unit:
                raw_str = f"{raw_str} {unit}"
            ind_score = ind.get("score", 0.5)
            ind_color = _score_color(ind_score)
            interp = ind.get("interpretation", "")
            pct = int(ind_score * 100)

            rows.append([
                _sans(ind.get("label", key), color=C_TEXT, weight=700),
                _mono(raw_str, color=C_TEXT2),
                _mono(f"{pct}%", color=ind_color, weight=700),
                badge(interp, color=ind_color) if interp else _sans("--"),
            ])

        wsj_market_table(
            headers=["Indicator", "Reading", "Score", "Signal"],
            rows=rows,
        )
        st.html(live_data_badge(_FRED_SRC))

    # ── 4. Yield curve analysis ────────────────────────────────────────────
    yc = compute_yield_curve_analysis(macro_data)
    if yc.get("curve_points"):
        section_header(
            "Treasury Yield Curve",
            subtitle=f"Curve shape: {yc['shape']}",
        )

        # Yield curve chart
        try:
            import plotly.graph_objects as go

            tenors = [p["tenor"] for p in yc["curve_points"]]
            yields = [p["yield"] for p in yc["curve_points"]]
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=tenors,
                y=yields,
                mode="lines+markers",
                line=dict(color=C_ACCENT, width=2),
                marker=dict(size=6, color=C_ACCENT),
                name="Current Curve",
            ))
            apply_dark_layout(
                fig,
                title="US Treasury Yield Curve",
                height=320,
                showlegend=False,
                xaxis=dict(title="Maturity"),
                yaxis=dict(title="Yield (%)", ticksuffix="%"),
            )
            st.plotly_chart(
                fig,
                use_container_width=True,
                config={"displayModeBar": False},
                key="bellwethers_yield_curve",
            )
            st.html(live_data_badge(_FRED_SRC))
        except Exception as exc:
            logger.warning(f"Yield curve chart failed: {exc}")

        # Implication callout
        implication = yc.get("implication", "")
        if implication:
            st.markdown(
                insight_card_html(
                    title=implication,
                    score=0.5,
                    action="WATCH",
                    rationale="Derived from the slope and shape of the US Treasury yield curve.",
                    category="YIELD CURVE",
                ),
                unsafe_allow_html=True,
            )

        # Spread KPI row
        spreads = yc.get("spreads")
        if spreads:
            metric_card_row(
                [
                    {
                        "label":  spread_name,
                        "value":  f"{spread_val:+.2f}%",
                        "accent": _spread_color(spread_val),
                    }
                    for spread_name, spread_val in spreads.items()
                ],
                columns=min(len(spreads), 4),
            )
            st.html(live_data_badge(_FRED_SRC))

    # ── 5. Earnings calendar ───────────────────────────────────────────────
    calendar = compute_earnings_calendar()
    if calendar:
        section_header(
            "Shipping Earnings Calendar",
            subtitle=(
                f"Next {min(len(calendar), 10)} upcoming earnings reports "
                "from tracked shipping companies"
            ),
        )

        rows = []
        for evt in calendar[:10]:
            days = evt["days_until"]
            urgency = _urgency_color(days)
            company_cell = _sans(evt["company"], color=C_TEXT, weight=700)
            if evt.get("status") == "This Week":
                company_cell += " " + badge("THIS WEEK", color=C_LOW)

            rows.append([
                company_cell,
                _mono(evt["ticker"], color=C_ACCENT, weight=700),
                _sans(evt["sector"], color=C_TEXT2),
                _mono(evt["quarter"], color=C_TEXT2),
                _mono(evt["date_display"], color=C_TEXT2),
                _mono(f"{days}d", color=urgency, weight=700),
            ])

        wsj_market_table(
            headers=["Company", "Ticker", "Sector", "Quarter", "Date", "Days Until"],
            rows=rows,
        )
        st.html(source_footer([_EARNINGS_SRC, _FRED_SRC]))
