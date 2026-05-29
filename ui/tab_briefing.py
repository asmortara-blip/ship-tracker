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

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from utils.tz import format_user_tz
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    tldr_lede,
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


# ─── Pure figure-builder (testable; no Streamlit) ──────────────────────────

_TREND_COLOR: dict[str, str] = {
    "Worsening": C_LOW,
    "Improving": C_HIGH,
    "Stable":    C_TEXT2,
}


def _build_forecast_quadrant_scatter(forecasts: list) -> go.Figure:
    """Today × 30-day-forecast stress scatter for every tracked route.

    x = current_stress · y = stress_30d · colour = trend
    (Worsening / Stable / Improving) · marker size scales with the absolute
    rate-forecast move so the routes carrying the loudest rate signal stand
    out. A y=x reference diagonal makes the visual answer "which routes are
    forecast to worsen (above the line) vs. ease (below the line)?" without
    requiring the reader to do mental subtraction.

    Pure builder — no ``st.*`` calls — exercised directly by the lock-in
    tests. Empty / missing forecasts return an annotated-empty figure.
    """
    fig = go.Figure()
    items = list(forecasts or [])
    if not items:
        fig.add_annotation(
            text="No route forecasts available",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(
            fig, title="Stress today vs. 30-day forecast", height=300,
        )
        return fig

    # Group by trend so each direction gets its own legend entry. We group
    # *before* extracting axes so the colour & ordering stay deterministic.
    buckets: dict[str, list] = {"Worsening": [], "Stable": [], "Improving": []}
    for f in items:
        trend = getattr(f, "trend", "Stable") or "Stable"
        buckets.setdefault(trend, []).append(f)

    for trend in ("Worsening", "Stable", "Improving"):
        bucket = buckets.get(trend, [])
        if not bucket:
            continue
        color = _TREND_COLOR.get(trend, C_TEXT2)
        x_vals = [float(getattr(f, "current_stress", 0.0)) for f in bucket]
        y_vals = [float(getattr(f, "stress_30d", 0.0)) for f in bucket]
        rates  = [float(getattr(f, "rate_forecast_pct", 0.0)) * 100.0
                  for f in bucket]
        names  = [
            (getattr(f, "route_name", "") or getattr(f, "route_id", "") or "—")
            for f in bucket
        ]
        # Marker size scales with |rate move| — clamped to 10–28 px so a
        # single loud forecast doesn't dwarf everything else.
        sizes = [max(10, min(28, 10 + 4 * abs(r))) for r in rates]
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            mode="markers",
            name=trend,
            marker={
                "size": sizes,
                "color": color,
                "line": {"color": C_BG, "width": 1.5},
                "opacity": 0.88,
            },
            customdata=list(zip(names, rates)),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Today: %{x:.2f}<br>"
                "30-day: %{y:.2f}<br>"
                "Rate forecast: %{customdata[1]:+.1f}%<br>"
                "Trend: " + trend + "<extra></extra>"
            ),
        ))

    # y = x diagonal — the "no change" reference. Anything above it is
    # forecast to worsen; anything below is forecast to ease.
    fig.add_shape(
        type="line",
        x0=0, y0=0, x1=1, y1=1,
        line={"color": "rgba(255,255,255,0.12)", "width": 1, "dash": "dot"},
        layer="below",
    )
    fig.add_annotation(
        x=0.95, y=0.97, text="y = x", showarrow=False,
        font={"color": C_TEXT3, "size": 10},
        xref="x", yref="y",
    )

    apply_dark_layout(
        fig, title="Stress today vs. 30-day forecast", height=320,
    )
    fig.update_layout(
        xaxis={"title": "Current stress (0–1)", "range": [0, 1.05],
               "gridcolor": "rgba(255,255,255,0.04)"},
        yaxis={"title": "30-day forecast stress (0–1)", "range": [0, 1.05],
               "gridcolor": "rgba(255,255,255,0.04)"},
        legend={"orientation": "h", "y": -0.20,
                "font": {"color": C_TEXT3, "size": 10}},
        margin={"l": 70, "r": 24, "t": 44, "b": 50},
    )
    return fig


# ─── Signal assembly ────────────────────────────────────────────────────────

def _assemble_context(port_results, route_results, freight_data, macro_data):
    """Build (NarrationContext, raw_signals_for_display).

    Delegates context assembly to engine.narration_engine.
    build_narration_context (the single source of truth shared with the
    headless worker), then reconstructs the raw-signals dict from the
    context so the "Today's Inputs" transparency panel renders exactly the
    data the LLM saw.
    """
    from engine.narration_engine import build_narration_context

    ctx = build_narration_context(
        port_results, route_results, freight_data, macro_data,
    )
    return ctx, {
        "stress_report": ctx.stress_report,
        "forecasts": ctx.top_forecasts,
        "indicators": ctx.notable_indicators,
        "top_port_deficits": ctx.top_port_deficits,
    }


# ─── Section renderers ──────────────────────────────────────────────────────

def _render_tldr_lede(narration) -> None:
    """One-paragraph TL;DR above the full briefing — the 10-second read.

    Day-cached via engine.daily_briefing_tldr (first viewer of each UTC
    day pays one cheap Haiku call; everyone else hits the cache,
    invalidated when a force-refresh changes the narration), so this adds
    no per-render LLM latency. Best-effort: generate_tldr never raises,
    and the import guard keeps the tab resilient if the module is
    somehow unavailable.
    """
    try:
        from engine.daily_briefing_tldr import generate_tldr
        summary = generate_tldr(narration)
    except Exception:
        logger.debug("tab_briefing: TLDR lede unavailable")
        return

    # Route the markup through the design-system helper (keeps tab_briefing
    # within its inline-style budget; see test_tab_briefing_refactor).
    tldr_lede(summary.text, summary.source)


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

    # Render generated_at in the user's timezone (UTC fallback when no
    # user is set or settings/zoneinfo lookup fails — format_user_tz
    # returns "" on failure, which renders as no chip).
    gen_at_local = format_user_tz(narration.generated_at) if narration.generated_at else ""
    gen_chip = (
        f' · <span style="font-size:0.66rem;color:{C_TEXT3}">'
        f'generated {gen_at_local}</span>'
    ) if gen_at_local else ""

    st.markdown(
        f'<div style="background:rgba(53,114,176,0.08);'
        f'border-left:3px solid {C_ACCENT};padding:20px 24px;'
        f'border-radius:3px;margin-bottom:18px">'
        f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
        f'color:{C_TEXT3};font-weight:600;margin-bottom:8px">'
        f'Source: <span style="color:{source_label[1]}">{source_label[0]}</span>'
        f' · {narration.date}{model_chip}{token_chip}{gen_chip}</div>'
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


def _render_editorial_commentary(narration, signals: dict) -> None:
    """Per-tab editorial commentary from ``engine.tab_commentary``.

    Sits between the briefing prose and the structured-inputs panel — gives
    a tight, cached editorial read on the same signals using a different
    voice from the main daily-narration block above. Wraps the engine call
    in try/except — template fallback is safe; the only failure mode we
    guard against here is import / DB errors.
    """
    try:
        from engine.tab_commentary import build_commentary

        stress_report = signals.get("stress_report")
        forecasts = signals.get("forecasts") or []
        indicators = signals.get("indicators") or {}

        context: dict[str, object] = {
            "narration_source": getattr(narration, "source", ""),
            "narration_date": getattr(narration, "date", ""),
            "n_forecasts": len(forecasts),
            "n_indicators": len(indicators),
        }
        if stress_report is not None:
            context["ssi"] = round(float(getattr(stress_report, "overall_ssi", 0.0)), 3)
            context["ssi_label"] = str(getattr(stress_report, "ssi_label", ""))
            context["wow_change_pp"] = round(
                float(getattr(stress_report, "wow_change", 0.0)) * 100.0, 2
            )
            context["active_disruptions"] = len(
                getattr(stress_report, "top_disruptions", []) or []
            )
        if forecasts:
            top = forecasts[0]
            context["top_route"] = (
                f"{getattr(top, 'route_name', '') or getattr(top, 'route_id', '')}"
                f" (stress 30d {float(getattr(top, 'stress_30d', 0.0)):.2f})"
            ).strip()
        if indicators:
            # Keep the indicator dict JSON-serializable (round to 2 dp).
            context["indicators"] = {
                str(k): round(float(v), 2) for k, v in list(indicators.items())[:6]
            }

        commentary = build_commentary("Briefing", context)

        section_header(
            "Editorial",
            subtitle=(
                "LLM-narrated read on today's inputs. Falls back to a "
                "deterministic template when no API key is configured."
            ),
        )

        source_label, source_color = (
            ("LLM", C_HIGH) if commentary.source == "llm"
            else ("Template", C_MOD)
        )
        meta_bits = [f"<span style='color:{source_color}'>{source_label}</span>"]
        if commentary.source == "llm" and commentary.model:
            meta_bits.append(
                f"<code style='font-size:0.66rem;color:{C_TEXT3}'>{commentary.model}</code>"
            )
        if commentary.tokens_in or commentary.tokens_out:
            meta_bits.append(
                f"<span style='font-size:0.66rem;color:{C_TEXT3}'>"
                f"{commentary.tokens_in}→{commentary.tokens_out} tok</span>"
            )

        body_html = "".join(
            f'<p style="margin:0 0 10px 0;font-size:0.86rem;line-height:1.55;'
            f'color:{C_TEXT2}">{para.strip()}</p>'
            for para in commentary.body.split("\n\n") if para.strip()
        )
        st.markdown(
            f'<div style="background:rgba(53,114,176,0.06);'
            f'border-left:3px solid {C_ACCENT};padding:14px 18px;border-radius:3px;'
            f'margin-bottom:14px">'
            f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
            f'color:{C_TEXT3};font-weight:600;margin-bottom:6px">'
            f'Source: {" · ".join(meta_bits)}'
            f'</div>'
            f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1.05rem;'
            f'line-height:1.4;color:{C_TEXT};font-weight:600;margin-bottom:10px">'
            f'{commentary.headline}</div>'
            f'{body_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Briefing — editorial commentary render failed")


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

    # Quadrant scatter — overview of where stress is and where it's going,
    # before the per-route detail table that follows. Routes above the y=x
    # diagonal are forecast to worsen; below, to ease.
    if forecasts:
        st.plotly_chart(
            _build_forecast_quadrant_scatter(forecasts),
            use_container_width=True,
            key="briefing_forecast_quadrant",
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


def _build_export_snapshot(narration, signals: dict):
    """Translate the current briefing into a ViewSnapshot for PDF export.

    Mirrors the on-screen content: headline, multi-paragraph body, one
    section per narration section, plus an Inputs section showing the
    SSI snapshot, top route forecasts table, and indicator chips. The
    snapshot is built lazily — caller wraps in try/except so failure
    here can't take down the tab.
    """
    from utils.view_export import ViewSection, ViewSnapshot, ViewTable

    sections = [
        ViewSection(title=s.title, bullets=list(s.bullets))
        for s in narration.sections
    ]

    # Inputs section: SSI snapshot + top forecasts table + indicators chip line.
    stress_report = signals.get("stress_report")
    forecasts = signals.get("forecasts", [])
    indicators = signals.get("indicators", {})
    input_bullets: list[str] = []
    input_tables: list[ViewTable] = []

    if stress_report is not None:
        input_bullets.extend([
            f"SSI: {getattr(stress_report, 'overall_ssi', 0.0):.2f} "
            f"({getattr(stress_report, 'ssi_label', '')})",
            f"Active disruptions: {len(getattr(stress_report, 'top_disruptions', []) or [])}",
            f"Routes tracked: {len(getattr(stress_report, 'route_stress', []) or [])}",
            f"WoW SSI change: {getattr(stress_report, 'wow_change', 0.0)*100:+.1f}pp",
        ])
    if forecasts:
        rows = []
        for f in forecasts:
            rows.append([
                str(getattr(f, "route_name", "") or getattr(f, "route_id", "")),
                f"{float(getattr(f, 'current_stress', 0.0)):.2f}",
                f"{float(getattr(f, 'stress_30d', 0.0)):.2f}",
                str(getattr(f, "trend", "")),
                f"{float(getattr(f, 'rate_forecast_pct', 0.0))*100:+5.1f}%",
            ])
        input_tables.append(ViewTable(
            title="Top Route Forecasts",
            headers=["Route", "Current", "30d", "Trend", "Rate 30d %"],
            rows=rows,
        ))
    if indicators:
        input_bullets.append(
            "Indicators: " + " · ".join(f"{k}={v:.2f}" for k, v in indicators.items())
        )

    if input_bullets or input_tables:
        sections.append(ViewSection(
            title="Today's Inputs",
            bullets=input_bullets,
            tables=input_tables,
        ))

    source_label = "LLM (Claude)" if narration.source == "claude" else "Template"
    # The PDF footer renders generated_at user-facing — convert from
    # the stored UTC ISO 8601 string to a localized "YYYY-MM-DD HH:MM TZ"
    # rendering in the active user's timezone. format_user_tz returns
    # the empty string on any failure; pass that through so ViewSnapshot
    # falls back to its own default rendering.
    generated_at_local = format_user_tz(narration.generated_at) if narration.generated_at else ""
    return ViewSnapshot(
        title=f"Daily Briefing — {narration.date}",
        subtitle=f"Source: {source_label}"
        + (f" · {narration.model}" if narration.source == "claude" and narration.model else ""),
        headline=narration.headline,
        body=narration.body,
        sections=sections,
        footer_note=(
            "Ship Tracker daily briefing. Narration via engine.narration_engine; "
            "structured inputs from shipping_stress_index + disruption_forecast."
        ),
        generated_at=generated_at_local or narration.generated_at,
    )


def _rotate_investor_report_in_session(
    port_results, route_results, insights, freight_data, macro_data, stock_data,
) -> None:
    """Build a fresh InvestorReport, rotate it through session state, AND
    persist a slim ReportSnapshot to SQLite.

    Pattern: each briefing-tab visit rotates ``current`` → ``previous`` and
    sets ``current`` to a freshly-built InvestorReport in session state.
    In parallel we extract a slim ReportSnapshot from the report and
    INSERT it into ``investor_report_snapshots`` so the "what changed"
    diff survives Streamlit restarts (the session-state rotation only
    persists for the lifetime of one Streamlit session).

    Wrapped in try/except so failures here cannot break the rest of the
    tab. The snapshot save itself never raises (``save_snapshot`` returns
    False on error), so even a broken SQLite layer degrades gracefully.
    """
    try:
        from processing.investor_report_engine import build_investor_report

        current_report = build_investor_report(
            port_results=port_results or [],
            route_results=route_results or [],
            insights=insights or [],
            freight_data=freight_data or {},
            macro_data=macro_data or {},
            stock_data=stock_data or {},
            with_tldr=False,   # diff-only snapshot; skip the lede LLM call
        )
        prev = st.session_state.get("current_investor_report")
        if prev is not None:
            st.session_state["previous_investor_report"] = prev
        st.session_state["current_investor_report"] = current_report

        # Persist the slim snapshot so the diff survives a restart.
        # Best-effort — save_snapshot never raises; the in-session
        # rotation above is a working fallback for the first-visit case.
        try:
            from processing.report_snapshot import (
                extract_snapshot,
                save_snapshot,
            )
            from state.user_scope import current_user_id

            snapshot = extract_snapshot(current_report)
            save_snapshot(snapshot, user_id=current_user_id())
        except Exception:
            logger.exception("tab_briefing: snapshot persistence failed")
    except Exception:
        logger.exception("tab_briefing: investor-report session rotation failed")


def _render_report_diff() -> None:
    """Render a "What Changed" widget comparing today vs. previous report.

    Primary path: pull the two newest ReportSnapshot rows from the
    ``investor_report_snapshots`` SQLite table — this survives Streamlit
    restarts so the diff is durable across deploys.

    Secondary fallback: if the SQLite layer has fewer than two snapshots
    (e.g. the very first briefing-tab visit before the snapshot has
    been saved), fall back to the legacy session-state pair so the
    in-session diff still works.
    """
    section_divider("What Changed")
    try:
        from processing.report_diff import compute_report_diff, format_diff_html
        from processing.report_snapshot import load_latest_snapshots
        from state.user_scope import current_user_id

        # Primary: SQLite-backed snapshots (survive restart).
        snapshots = load_latest_snapshots(n=2, user_id=current_user_id())
        if len(snapshots) >= 2:
            # newest is index 0; second-newest is the prior snapshot.
            diff = compute_report_diff(snapshots[1], snapshots[0])
            html = format_diff_html(diff)
            st.markdown(html, unsafe_allow_html=True)
            return

        # Secondary fallback: in-session rotation pair (works on the
        # very first visit before two snapshots have accumulated).
        current = st.session_state.get("current_investor_report")
        previous = st.session_state.get("previous_investor_report")
        if current is None or previous is None:
            st.info(
                "No prior report to diff against yet. Generate a fresh "
                "briefing to populate."
            )
            return
        diff = compute_report_diff(previous, current)
        html = format_diff_html(diff)
        st.markdown(html, unsafe_allow_html=True)
    except Exception:
        logger.exception("tab_briefing: report-diff render failed")


def _render_actions_bar(ctx, narration, signals: dict) -> None:
    """Refresh button + PDF export + usage notes."""
    cols = st.columns([1, 1, 4], gap="small")
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
        try:
            from utils.view_export import build_view_pdf
            snapshot = _build_export_snapshot(narration, signals)
            pdf_bytes = build_view_pdf(snapshot)
            filename = f"briefing_{narration.date}.pdf"
            st.download_button(
                "⇩ Export PDF",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf",
                use_container_width=True,
                key="briefing_export_pdf",
            )
        except Exception as exc:
            logger.debug(f"tab_briefing: PDF export unavailable: {exc}")
            st.button("⇩ Export PDF", disabled=True, use_container_width=True,
                       key="briefing_export_pdf_disabled",
                       help=f"PDF export unavailable: {exc}")
    with cols[2]:
        st.markdown(
            f'<div style="font-size:0.72rem;color:{C_TEXT3};line-height:1.45;'
            f'padding-top:8px">'
            f'The briefing is cached per UTC day — first viewer of each day '
            f'triggers a fresh call. Use ↻ to force regeneration, ⇩ to '
            f'download the current view as a PDF. The LLM path requires '
            f'<code>ANTHROPIC_API_KEY</code> in <code>st.secrets</code> '
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
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('briefing'):
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

            # Rotate the InvestorReport snapshot through session state so the
            # "What Changed" widget below has a prev/curr pair to diff.
            _rotate_investor_report_in_session(
                port_results, route_results, insights,
                freight_data, macro_data, stock_data,
            )

            # ── 0. TL;DR lede — one-paragraph 10-second read ───────────────────
            _render_tldr_lede(narration)

            # ── 1-2. Headline + Body ───────────────────────────────────────────
            _render_headline_card(narration)
            _render_body(narration)

            # ── 3. Sections grid ───────────────────────────────────────────────
            _render_sections_grid(narration)

            # ── 3b. Editorial commentary (per-tab LLM + template fallback) ─────
            _render_editorial_commentary(narration, signals)

            section_divider("Inputs")

            # ── 4. Transparency panel ──────────────────────────────────────────
            _render_inputs_panel(signals)

            # ── 4b. What Changed — diff vs. prior briefing in session ──────────
            _render_report_diff()

            section_divider("Actions")

            # ── 5. Refresh button + PDF export + notes ─────────────────────────
            _render_actions_bar(ctx, narration, signals)

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
