"""ui/tab_briefing.py — Daily Briefing tab.

Dedicated single-screen surface for the LLM-narrated daily briefing.
Uses engine.narration_engine.generate_daily_narration (Claude API, day-
cached) and renders it with editorial typography — generous spacing,
serif headline, multi-column section grid.

A miniaturized version of this lives in tab_overview as a panel; this tab
gives the briefing room to breathe and adds a transparency panel
("Today's Inputs") that shows the structured signals that fed the
narration, so users can trace from the prose back to the data.

Sections:
  1. Page header
  2. Headline card (big serif, accent-bordered)
  3. Body paragraphs (editorial column)
  4. Sections grid (3-column, breathing room)
  5. Today's Inputs — SSI snapshot, top route forecasts, notable indicators
  6. Source detail (model, tokens, generated_at, cache state)
  7. Refresh button (bypasses the day cache)
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

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
    badge,
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


def _ssi_label_color(label: str) -> str:
    return {
        "Calm":     C_HIGH,
        "Elevated": C_MOD,
        "Stressed": C_LOW,
        "Severe":   C_LOW,
    }.get(label, C_TEXT2)


# ─── Signal assembly ────────────────────────────────────────────────────────

def _assemble_context(port_results, route_results, freight_data, macro_data):
    """Build (NarrationContext, raw_signals_for_display).

    Computes SSI + top route forecasts + headline indicators. Returns the
    NarrationContext for the narrator and the raw signals dict so the
    "Today's Inputs" transparency panel can render the same data the LLM saw.
    """
    from engine.narration_engine import NarrationContext

    stress_report = None
    forecasts: list = []
    try:
        from processing.shipping_stress_index import compute_shipping_stress
        stress_report = compute_shipping_stress(
            freight_data or {}, macro_data or {},
            port_results or [], route_results or [],
        )
    except Exception as exc:
        logger.debug(f"tab_briefing: SSI compute failed: {exc}")

    try:
        from processing.disruption_forecast import forecast_all_stress
        all_forecasts = forecast_all_stress(
            freight_data or {}, macro_data or {}, route_results or [],
            stress_report=stress_report,
        )
        forecasts = sorted(
            all_forecasts, key=lambda f: getattr(f, "stress_30d", 0.0),
            reverse=True,
        )[:6]
    except Exception as exc:
        logger.debug(f"tab_briefing: forecast compute failed: {exc}")

    notable: dict[str, float] = {}
    try:
        if isinstance(macro_data, dict):
            for k in ("BDIY", "BDI", "WCI", "FBX", "SCFI", "DCOILWTICO"):
                df = macro_data.get(k)
                if df is not None and not getattr(df, "empty", True):
                    if "value" in getattr(df, "columns", []):
                        notable[k] = float(df["value"].dropna().iloc[-1])
    except Exception:
        pass

    ctx = NarrationContext(
        stress_report=stress_report,
        top_forecasts=forecasts,
        notable_indicators=notable,
    )
    return ctx, {
        "stress_report": stress_report,
        "forecasts": forecasts,
        "indicators": notable,
    }


# ─── Section renderers ──────────────────────────────────────────────────────

def _render_headline_card(narration) -> None:
    source_label = {
        "claude":   ("LLM", C_HIGH),
        "template": ("Template", C_MOD),
    }.get(narration.source, ("Unknown", C_TEXT3))

    model_chip = ""
    if narration.source == "claude" and narration.model:
        model_chip = (
            f' · <code style="font-size:0.66rem;color:{C_TEXT3}">'
            f'{narration.model}</code>'
        )
    token_chip = ""
    if narration.tokens_in or narration.tokens_out:
        token_chip = (
            f' · <span style="font-size:0.66rem;color:{C_TEXT3}">'
            f'{narration.tokens_in}→{narration.tokens_out} tok</span>'
        )

    st.markdown(
        f'<div style="background:rgba(53,114,176,0.08);'
        f'border-left:3px solid {C_ACCENT};padding:20px 24px;'
        f'border-radius:3px;margin-bottom:18px">'
        f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
        f'color:{C_TEXT3};font-weight:600;margin-bottom:8px">'
        f'Source: <span style="color:{source_label[1]}">{source_label[0]}</span>'
        f' · {narration.date}{model_chip}{token_chip}</div>'
        f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1.5rem;'
        f'line-height:1.35;color:{C_TEXT};font-weight:700">{narration.headline}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_body(narration) -> None:
    """Editorial-column body rendering — slightly wider type, more line-height."""
    paragraphs = [p.strip() for p in narration.body.split("\n\n") if p.strip()]
    body_html = "".join(
        f'<p style="margin:0 0 14px 0;font-size:0.95rem;line-height:1.65;'
        f'color:{C_TEXT2};font-family:Libre Franklin,sans-serif">{para}</p>'
        for para in paragraphs
    )
    st.markdown(
        f'<div style="max-width:780px;margin:0 0 18px 0">{body_html}</div>',
        unsafe_allow_html=True,
    )


def _render_sections_grid(narration) -> None:
    if not narration.sections:
        return
    section_header("Breakdown", subtitle="Key topical points from the briefing")

    sections = narration.sections
    n = len(sections)
    n_cols = min(n, 3)
    cols = st.columns(n_cols, gap="medium")
    for i, sec in enumerate(sections):
        with cols[i % n_cols]:
            bullets_html = "".join(
                f'<li style="font-size:0.8rem;line-height:1.5;color:{C_TEXT2};'
                f'margin-bottom:6px">{b}</li>'
                for b in sec.bullets
            )
            st.markdown(
                f'<div style="background:rgba(255,255,255,0.02);'
                f'border:1px solid rgba(232,230,225,0.08);'
                f'border-radius:3px;padding:14px 18px;margin-bottom:10px;'
                f'min-height:140px">'
                f'<div style="font-size:0.72rem;text-transform:uppercase;'
                f'letter-spacing:0.12em;color:{C_TEXT};font-weight:700;'
                f'margin-bottom:8px;border-bottom:1px solid {C_TEXT3};'
                f'padding-bottom:4px">{sec.title}</div>'
                f'<ul style="margin:0;padding-left:18px">{bullets_html}</ul>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _render_inputs_panel(signals: dict) -> None:
    """Transparency panel — show the structured signals the LLM saw."""
    stress_report = signals.get("stress_report")
    forecasts = signals.get("forecasts", [])
    indicators = signals.get("indicators", {})

    section_header(
        "Today's Inputs",
        subtitle=(
            "Structured signals that fed the briefing. Cross-check the prose "
            "against the numbers."
        ),
    )

    # SSI strip
    if stress_report is not None:
        ssi_color = _ssi_label_color(getattr(stress_report, "ssi_label", ""))
        n_disruptions = len(getattr(stress_report, "top_disruptions", []) or [])
        n_routes = len(getattr(stress_report, "route_stress", []) or [])
        metric_card_row(
            [
                {"label": "Shipping Stress Index",
                 "value": f"{getattr(stress_report, 'overall_ssi', 0.0):.2f}",
                 "accent": ssi_color,
                 "sublabel": getattr(stress_report, "ssi_label", "")},
                {"label": "Active Disruptions",
                 "value": f"{n_disruptions}",
                 "accent": C_LOW if n_disruptions >= 3 else (C_MOD if n_disruptions >= 1 else C_HIGH),
                 "sublabel": "From the top_disruptions list"},
                {"label": "Routes Tracked",
                 "value": f"{n_routes}",
                 "accent": C_TEXT,
                 "sublabel": "With stress scores"},
                {"label": "WoW SSI Change",
                 "value": f"{getattr(stress_report, 'wow_change', 0.0)*100:+5.1f}pp",
                 "accent": (
                     C_LOW if getattr(stress_report, "wow_change", 0.0) > 0.02
                     else (C_HIGH if getattr(stress_report, "wow_change", 0.0) < -0.02 else C_TEXT2)
                 ),
                 "sublabel": "Week-over-week"},
            ],
            columns=4,
        )

    # Top route forecasts table
    if forecasts:
        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_TEXT2};font-weight:700;'
            f'margin:14px 0 6px 0">Top Route Forecasts</div>',
            unsafe_allow_html=True,
        )
        headers = ["Route", "Current Stress", "30d Forecast", "Trend", "Rate 30d %"]
        rows = []
        for f in forecasts:
            current = float(getattr(f, "current_stress", 0.0))
            stress_30d = float(getattr(f, "stress_30d", 0.0))
            trend = getattr(f, "trend", "")
            rate_pct = float(getattr(f, "rate_forecast_pct", 0.0)) * 100.0

            stress_color = (
                C_LOW if stress_30d >= 0.7 else
                (C_MOD if stress_30d >= 0.5 else C_HIGH)
            )
            trend_color = {
                "Worsening": C_LOW, "Improving": C_HIGH, "Stable": C_TEXT2,
            }.get(trend, C_TEXT2)
            rate_color = C_HIGH if rate_pct > 1 else (C_LOW if rate_pct < -1 else C_TEXT2)

            rows.append([
                _sans(
                    getattr(f, "route_name", "") or getattr(f, "route_id", ""),
                    color=C_TEXT, weight=600,
                ),
                _mono(f"{current:.2f}", color=C_TEXT2),
                _mono(f"{stress_30d:.2f}", color=stress_color),
                _sans(trend, color=trend_color),
                _mono(f"{rate_pct:+5.1f}%", color=rate_color),
            ])
        wsj_market_table(headers, rows)

    # Notable indicators
    if indicators:
        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_TEXT2};font-weight:700;'
            f'margin:14px 0 6px 0">Notable Indicators</div>',
            unsafe_allow_html=True,
        )
        chips = " ".join(
            f'<span style="display:inline-block;background:rgba(255,255,255,0.03);'
            f'border:1px solid rgba(232,230,225,0.08);border-radius:3px;'
            f'padding:4px 10px;margin-right:6px;margin-bottom:4px;'
            f'font-size:0.78rem;color:{C_TEXT}">'
            f'<b style="color:{C_TEXT3};font-size:0.66rem">{k}</b> {v:.2f}</span>'
            for k, v in indicators.items()
        )
        st.markdown(chips, unsafe_allow_html=True)


def _render_actions_bar(ctx) -> None:
    """Refresh button + brief usage notes."""
    cols = st.columns([1, 4], gap="small")
    with cols[0]:
        if st.button("↻ Force refresh", use_container_width=True,
                     key="briefing_refresh"):
            # Bypass the day cache by calling with use_cache=False.
            from engine.narration_engine import generate_daily_narration
            try:
                fresh = generate_daily_narration(ctx, use_cache=False)
                source_word = (
                    "from Claude" if fresh.source == "claude" else "from template"
                )
                st.success(f"Briefing refreshed — {source_word}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Refresh failed: {exc}")
    with cols[1]:
        st.markdown(
            f'<div style="font-size:0.72rem;color:{C_TEXT3};line-height:1.45;'
            f'padding-top:8px">'
            f'The briefing is cached per UTC day — first viewer of each day '
            f'triggers a fresh call. Use ↻ to force regeneration. The LLM '
            f'path requires <code>ANTHROPIC_API_KEY</code> in <code>st.secrets</code> '
            f'or env; without it the template path runs.'
            f'</div>',
            unsafe_allow_html=True,
        )


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
    """Render the Daily Briefing tab."""
    try:
        page_header(
            title="Daily Briefing",
            subtitle=(
                "LLM-narrated synthesis of today's shipping signals. "
                "Cached per UTC day with template fallback when no API key."
            ),
            badge_text="BRIEFING",
            badge_color=C_ACCENT,
        )

        try:
            from engine.narration_engine import generate_daily_narration
        except Exception as exc:
            st.error(f"Narration engine unavailable: {exc}")
            return

        ctx, signals = _assemble_context(
            port_results, route_results, freight_data, macro_data,
        )
        narration = generate_daily_narration(ctx)

        # ── 1-2. Headline + Body ───────────────────────────────────────────
        _render_headline_card(narration)
        _render_body(narration)

        # ── 3. Sections grid ───────────────────────────────────────────────
        _render_sections_grid(narration)

        section_divider("Inputs")

        # ── 4. Transparency panel ──────────────────────────────────────────
        _render_inputs_panel(signals)

        section_divider("Actions")

        # ── 5. Refresh button + notes ──────────────────────────────────────
        _render_actions_bar(ctx)

        # ── 6. Source footer ───────────────────────────────────────────────
        st.markdown(
            source_footer([
                DataSource.modeled(
                    "Daily Briefing",
                    notes=(
                        "Narration via engine.narration_engine. "
                        "Inputs from shipping_stress_index + disruption_forecast "
                        "+ macro_data indicators."
                    ),
                ),
            ]),
            unsafe_allow_html=True,
        )

    except Exception:
        logger.exception("tab_briefing render failed")
        st.error("Daily Briefing encountered an error. See logs.")
