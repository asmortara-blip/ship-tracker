"""tab_disruption_radar.py — Disruption Radar (Disruption Alpha · stage 2).

Detect and forecast what is disrupted across the modeled fleet. The fleet-wide
Shipping Stress Index (SSI) blends five components — chokepoint, congestion,
weather, rate and vulnerability — into one 0–1 composite, then resolves it per
route. Below the headline gauge sit the component breakdown, a route stress
heat bar, a per-route disruption table sorted worst-first, and a 7/30-day
stress forecast.

Canonical tab pattern (mirrors ``ui/tab_rate_analytics.py`` /
``ui/tab_voyage_tracker.py``):
  * palette + components imported from ``ui/styles.py`` — never redeclared;
  * no hand-rolled inline-styled divs — every block is a ``ui/styles.py``
    helper, or a ``wsj_market_table`` cell formatted with span content;
  * ``render(...)`` ends with ``**kwargs`` for argument safety;
  * every section wrapped in try/except + ``logger.exception``;
  * ``source_footer`` at the bottom.

The Shipping Stress Index and the forecast are both modeled — see
``processing/shipping_stress_index.py`` and ``processing/disruption_forecast.py``.

Sections
--------
A. Page header (badge "MODELED")
B. Fleet-wide SSI — status banner, headline gauge, read-out + component cards
C. Per-route stress heat bar — shipping_heat_bar
D. Per-route disruption table — wsj_market_table, sorted worst-first
E. 7/30-day stress forecast — featured-lane insight card + wsj_market_table
"""
from __future__ import annotations

import streamlit as st
from loguru import logger

# Single source of truth for palette, typography, and component helpers.
# Never redeclare color constants in a tab module — always import them.
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    apply_dark_layout,
    badge,
    gauge_ring,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    shipping_heat_bar,
    source_footer,
    wsj_market_table,
    wsj_news_list,
)

# ── Domain-specific color mappings ──────────────────────────────────────────
# Semantic mappings (label → color) stay local to the tab. Palette constants
# themselves live in ui/styles.py and are imported above, never redeclared.

_SSI_LABEL_COLOR: dict[str, str] = {
    "Calm":     C_HIGH,
    "Elevated": C_MOD,
    "Stressed": C_LOW,
    "Severe":   C_LOW,
}

# Each SSI band maps to an alert-banner severity so the headline state reads at
# a glance before the eye reaches the gauge.
_SSI_LABEL_LEVEL: dict[str, str] = {
    "Calm":     "success",
    "Elevated": "warning",
    "Stressed": "critical",
    "Severe":   "critical",
}

_TREND_COLOR: dict[str, str] = {
    "Improving": C_HIGH,
    "Stable":    C_TEXT2,
    "Worsening": C_LOW,
}

# Plain-English framing for each band — used in the headline status banner.
_SSI_LABEL_GLOSS: dict[str, str] = {
    "Calm":     "lanes are flowing freely with little disruption pressure",
    "Elevated": "disruption pressure is building on parts of the network",
    "Stressed": "multiple lanes are carrying material disruption stress",
    "Severe":   "the network is under acute, broad-based disruption stress",
}


def _stress_color(score: float) -> str:
    """Map a 0–1 stress score to a semantic palette color (higher = worse)."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return C_TEXT2
    if s >= 0.66:
        return C_LOW
    if s >= 0.40:
        return C_MOD
    return C_HIGH


def _trend_color(trend: str) -> str:
    return _TREND_COLOR.get(trend, C_TEXT2)


def _pct_change_color(pct: float) -> str:
    """Color a fractional rate change (positive = rate up = warmer)."""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return C_TEXT2
    if p > 0.01:
        return C_MOD
    if p < -0.01:
        return C_HIGH
    return C_TEXT2


# ── Cell formatters for WSJ market tables ───────────────────────────────────
# wsj_market_table() renders each cell string as raw HTML inside a <td>. These
# helpers only need to style *content* (font family + conditional color) — the
# table CSS handles alignment, rule lines and hover.

def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _route_cell(name: str) -> str:
    """Primary route-name cell — the bold lane name leading each table row."""
    return _sans(str(name)[:28], color=C_TEXT, weight=700)


# ── Compute layer (cached) ──────────────────────────────────────────────────
# compute_shipping_stress / forecast_all_stress take plain dicts and lists.
# st.cache_data cannot hash dicts, so the cached wrappers are *no-arg*: they
# rebuild their inputs internally (the voyage fleet is modeled and self-
# contained; freight / macro / port / route inputs degrade to neutral
# defaults when unavailable). This mirrors how the Voyage Tracker tab caches
# its modeled fleet with a no-arg wrapper.

@st.cache_data(ttl=3600, show_spinner=False)
def _load_voyage_fleet() -> list:
    """Build (and cache) the modeled voyage fleet for delayed-voyage counts."""
    from data.voyage_dataset import build_voyage_fleet
    return build_voyage_fleet()


def _compute_stress(freight_data, macro_data, port_results, route_results):
    """Compute the Shipping Stress Index report.

    Inputs are normalized to the neutral empty defaults the processing module
    documents as safe, then a modeled voyage fleet is attached so each
    ``RouteStress`` reports its delayed-voyage count.
    """
    from processing.shipping_stress_index import compute_shipping_stress

    try:
        fleet = _load_voyage_fleet()
    except Exception:
        logger.exception("Disruption Radar — voyage fleet build failed")
        fleet = None

    return compute_shipping_stress(
        freight_data or {},
        macro_data or {},
        port_results or [],
        route_results or [],
        voyage_fleet=fleet,
    )


def _compute_forecast(freight_data, macro_data, route_results, stress_report):
    """Forecast 7/30-day stress for every tracked route."""
    from processing.disruption_forecast import forecast_all_stress

    return forecast_all_stress(
        freight_data or {},
        macro_data or {},
        route_results or [],
        stress_report=stress_report,
    )


# ── Section B: fleet-wide SSI ───────────────────────────────────────────────

# Maps the SSI's internal component keys to display labels + accents. The
# processing module keys component_scores by component name; we render
# whichever of these four are present and fall back gracefully otherwise.
# Each weight mirrors COMPONENT_WEIGHTS in processing/shipping_stress_index.py
# and is surfaced as a card sublabel so the blend is legible, not implicit.
_COMPONENT_META: tuple[tuple[str, str, str, int], ...] = (
    ("chokepoint", "Chokepoint Stress", C_LOW,    32),
    ("congestion", "Congestion Stress", C_MOD,    22),
    ("weather",    "Weather Stress",    C_MACRO,  18),
    ("rate",       "Rate Stress",       C_ACCENT, 18),
)


def _render_ssi_banner(label: str, ssi: float) -> None:
    """Render the headline disruption-state banner above the SSI overview."""
    level = _SSI_LABEL_LEVEL.get(label, "info")
    gloss = _SSI_LABEL_GLOSS.get(label, "fleet disruption pressure is mixed")
    alert_banner(
        f"Fleet stress reads <b>{label}</b> at "
        f"<b>{ssi * 100:.0f}%</b> — {gloss}.",
        level=level,
    )


def _render_ssi_readout(ssi: float, label: str, color: str, wow: float) -> None:
    """Render the SSI read-out beside the gauge as a metric-card row.

    The headline composite restated as type, the band classification, and the
    week-over-week move — three ``kpi-card``s so the gauge has an editorial
    caption rather than sitting alone in its column.
    """
    wow_color = _pct_change_color(wow)
    wow_arrow = "▲" if wow > 0.0005 else ("▼" if wow < -0.0005 else "▬")
    wow_sign = "+" if wow >= 0 else ""

    metric_card_row(
        [
            {
                "label":    "Fleet SSI",
                "value":    f"{ssi * 100:.0f}%",
                "accent":   color,
                "sublabel": "0–100% disruption composite",
            },
            {
                "label":    "Stress Band",
                "value":    label,
                "accent":   color,
                "sublabel": "chokepoint-weighted classification",
            },
            {
                "label":    "Week-over-Week",
                "value":    f"{wow_arrow} {wow_sign}{wow * 100:.1f}%",
                "accent":   wow_color,
                "sublabel": "shift in fleet-wide SSI",
            },
        ],
        columns=3,
    )


def _render_top_disruptions(top: list) -> None:
    """Render the SSI's surfaced top disruptions as an editorial brief.

    Uses ``wsj_news_list`` — the platform's "What's News" component — so the
    disruption brief carries the same editorial identity as the rest of the UI.
    """
    if not top:
        return
    wsj_news_list([str(d) for d in top[:5]])


def _render_ssi_overview(report, macro_data=None) -> None:
    """Render the headline SSI banner, gauge, read-out and component cards."""
    ssi = float(getattr(report, "overall_ssi", 0.0) or 0.0)
    label = getattr(report, "ssi_label", "") or "Unknown"
    color = getattr(report, "ssi_color", "") or _SSI_LABEL_COLOR.get(label, C_ACCENT)
    wow = float(getattr(report, "wow_change", 0.0) or 0.0)

    section_header(
        "Shipping Stress Index",
        "Fleet-wide disruption composite — chokepoint, congestion, weather, "
        "rate and vulnerability blended into one 0–100% read",
    )

    # ─ Headline disruption-state banner ─
    _render_ssi_banner(label, ssi)

    col_gauge, col_readout = st.columns([2, 3], gap="large")

    # ─ Headline gauge ─
    with col_gauge:
        fig = gauge_ring(ssi, f"SSI · {label}", color=color, size=220)
        # Route the gauge through the house dark layout for chart consistency
        # (transparent canvas + WSJ theme), preserving its tight margins and
        # the centered SSI annotation by keeping the legend off.
        apply_dark_layout(
            fig,
            height=220,
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            showlegend=False,
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key="disruption_radar_ssi_gauge",
        )

    # ─ SSI read-out cards beside the gauge ─
    with col_readout:
        _render_ssi_readout(ssi, label, color, wow)

    # ─ Component KPI cards — each captioned with its blend weight ─
    components: dict = getattr(report, "component_scores", {}) or {}
    cards = []
    for key, card_label, _accent, weight in _COMPONENT_META:
        val = components.get(key)
        if val is None:
            value_text = "n/a"
            card_accent = C_TEXT3
            sublabel = f"{weight}% of blend · no data"
        else:
            fval = float(val)
            value_text = f"{fval * 100:.0f}%"
            card_accent = _stress_color(fval)
            sublabel = f"{weight}% of SSI blend"
        cards.append({
            "label":    card_label,
            "value":    value_text,
            "accent":   card_accent,
            "sublabel": sublabel,
        })
    metric_card_row(cards, columns=4)

    # ─ Effective fleet supply — how much nominal capacity is immobilised by
    #   congestion + chokepoint diversion ("supply destruction by friction").
    try:
        from processing.effective_capacity import (
            effective_supply, friction_read,
        )
        cong, chok = components.get("congestion"), components.get("chokepoint")
        if cong is not None and chok is not None:
            es = effective_supply(float(cong), float(chok))
            section_header(
                "Effective Fleet Supply",
                "Nominal capacity discounted by congestion + chokepoint diversion")
            metric_card_row([
                {"label": "Effective Supply",
                 "value": f"{es.effective_supply_pct * 100:.0f}%",
                 "accent": _stress_color(es.drag_pct),
                 "sublabel": "of nominal fleet capacity"},
                {"label": "Friction Drag",
                 "value": f"{es.drag_pct * 100:.0f}%",
                 "accent": _stress_color(es.drag_pct),
                 "sublabel": (f"congestion {es.congestion_drag * 100:.0f}% · "
                              f"diversion {es.diversion_drag * 100:.0f}%")},
            ], columns=2)
            st.caption(
                "Effective supply below nominal means vessel-days + ton-miles are "
                "absorbed by friction — when freight rises into this, tightness is "
                "supply-destruction-driven, not demand. Modeled weights "
                "(congestion/diversion 50/50, drag capped at 60%).")

            # ─ Friction classification: cross the supply drag with the BDI
            #   (Baltic Dry Index, FRED BSXRLM) 30d freight move to name the
            #   regime. Reuses the tested alpha-engine helper; absent BDI →
            #   freight_chg 0.0 → an honest "Balanced" read.
            from engine.alpha_engine import _bdi_pct_change
            freight_chg = _bdi_pct_change(macro_data or {})
            fr = friction_read(es.drag_pct, freight_chg)
            alert_banner(
                f"<strong>{fr.label}</strong> — {fr.rationale} "
                f"(BDI 30d {freight_chg:+.1f}%)",
                level="success" if fr.bullish_carriers else "info")
    except Exception as exc:
        logger.debug(f"effective supply readout skipped: {exc}")

    # ─ Top disruptions, if the report surfaced any ─
    top: list = getattr(report, "top_disruptions", []) or []
    _render_top_disruptions(top)


# ── Section C-prefix: route explanations (operator-facing English) ──────────
# Optional explanatory layer that sits *above* the stress heat bar and the
# disruption table. Uses ``engine.disruption_explainer`` — template-based,
# deterministic, zero LLM tokens — to turn each top-N stressed route into a
# one-paragraph English brief. The explainer module is lazy-imported here so
# tab-load critical path stays small, and the whole helper is wrapped in
# defensive try/except + per-card isolation so one malformed RouteStress can
# never take the panel down.

_FOCUS_BADGE_COLOR: dict[str, str] = {
    "escalate":    C_LOW,
    "investigate": C_MOD,
    "monitor":     C_ACCENT,
}


def _render_route_explanations(stresses: list) -> None:
    """Render the operator-facing English route explanations panel.

    Slotted ABOVE the per-route stress table inside a *collapsed* expander so
    it doesn't dominate the page. Inside: one card per top-stressed route
    (headline, bullet "why" list, focus badge, optional chokepoint chips).

    NEVER raises — the explainer module itself is exception-safe, but this
    helper still wraps the call + per-card render in try/except so a bad
    row can never take the surrounding tab down.
    """
    # Empty / all-Calm input — render a quiet info message, no expander.
    if not stresses:
        st.caption(
            "No stressed routes right now — system is operating normally."
        )
        return

    try:
        # Lazy import keeps the explainer off the tab-load critical path
        # (and means a broken engine import only hurts this panel).
        from engine.disruption_explainer import explain_top_disruptions

        try:
            explanations = explain_top_disruptions(stresses, top_n=5)
        except Exception:
            logger.exception("Disruption Radar — explain_top_disruptions failed")
            explanations = []

        # All routes Calm → the explainer returns [] (Calm routes are filtered
        # before explanation). Surface the same "operating normally" caption.
        if not explanations:
            st.caption(
                "No stressed routes right now — system is operating normally."
            )
            return

        with st.expander(
            "Why these routes are stressed", expanded=False
        ):
            for exp in explanations:
                # Per-card try/except so one malformed RouteExplanation cannot
                # take the whole panel down.
                try:
                    _render_one_route_explanation(exp)
                except Exception:
                    logger.exception(
                        "Disruption Radar — route explanation card failed"
                    )
                    continue
    except Exception:
        logger.exception("Disruption Radar — explanations panel failed")
        st.warning("Explanations unavailable")


def _render_one_route_explanation(exp) -> None:
    """Render a single RouteExplanation as a card inside the expander."""
    with st.container():
        # ─ Bold headline ─
        headline = str(getattr(exp, "headline", "") or "")
        st.markdown(
            f'<div style="font-family:var(--serif);font-weight:700;'
            f'font-size:0.96rem;color:{C_TEXT};margin-bottom:6px;">'
            f'{headline}</div>',
            unsafe_allow_html=True,
        )

        # ─ "Why" bullets ─
        bullets: list = list(getattr(exp, "why", []) or [])
        if bullets:
            st.markdown(
                "  \n".join(f"- {b}" for b in bullets)
            )

        # ─ Recommended-focus badge (color-coded; omitted when empty) ─
        focus = str(getattr(exp, "recommended_focus", "") or "")
        if focus:
            badge_color = _FOCUS_BADGE_COLOR.get(focus, C_TEXT2)
            st.markdown(
                f'<div style="margin-top:4px;font-size:0.74rem;'
                f'color:{C_TEXT3};font-family:var(--sans);">'
                f'Recommended focus: {badge(focus.title(), color=badge_color)}'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ─ Affected chokepoints chip row ─
        chokepoints: list = list(getattr(exp, "affected_chokepoints", []) or [])
        if chokepoints:
            chips = "".join(
                f'<span style="display:inline-block;padding:2px 8px;'
                f'margin:2px 4px 2px 0;border-radius:10px;'
                f'background:rgba(204,82,82,0.10);'
                f'color:{C_LOW};border:1px solid rgba(204,82,82,0.25);'
                f'font-size:0.72rem;font-family:var(--sans);">'
                f'{str(cp)[:32]}</span>'
                for cp in chokepoints
            )
            st.markdown(
                f'<div style="margin-top:6px;">'
                f'<span style="font-size:0.7rem;color:{C_TEXT3};'
                f'font-family:var(--sans);text-transform:uppercase;'
                f'letter-spacing:0.05em;margin-right:6px;">'
                f'Affected chokepoints</span>{chips}</div>',
                unsafe_allow_html=True,
            )

        # ─ Light spacer between cards ─
        st.markdown(
            f'<div style="border-bottom:1px solid {C_TEXT3};opacity:0.18;'
            f'margin:10px 0 12px 0;"></div>',
            unsafe_allow_html=True,
        )


# ── Section C: per-route stress heat bar ────────────────────────────────────

def _render_heat_bar(report) -> None:
    """Render a heat bar of per-route stress, hottest lanes first."""
    route_stress: list = getattr(report, "route_stress", []) or []
    if not route_stress:
        alert_banner("No per-route stress data available.", level="info")
        return

    section_header(
        "Route Stress Heat Bar",
        "Relative disruption stress across tracked lanes — hottest first",
    )

    # shipping_heat_bar takes a {label: score} dict. Sort hottest-first and cap
    # the label set so the legend stays readable.
    ordered = sorted(
        route_stress,
        key=lambda r: float(getattr(r, "stress_score", 0.0) or 0.0),
        reverse=True,
    )
    scores: dict[str, float] = {}
    for r in ordered[:8]:
        name = str(getattr(r, "route_name", "") or getattr(r, "route_id", "route"))
        scores[name[:22]] = round(float(getattr(r, "stress_score", 0.0) or 0.0), 2)

    if scores:
        shipping_heat_bar(scores, title="Per-Route Shipping Stress")
    else:
        alert_banner("No per-route stress data available.", level="info")


# ── Section D: per-route disruption table ───────────────────────────────────

def _render_route_table(report) -> None:
    """Render the per-route disruption table, sorted worst-first."""
    route_stress: list = getattr(report, "route_stress", []) or []
    if not route_stress:
        alert_banner("No per-route disruption data available.", level="info")
        return

    section_header(
        "Route Disruption Detail",
        "Per-lane stress, dominant driver and delayed-voyage count — "
        "most-stressed first",
    )

    rows = []
    for r in sorted(
        route_stress,
        key=lambda x: float(getattr(x, "stress_score", 0.0) or 0.0),
        reverse=True,
    ):
        stress = float(getattr(r, "stress_score", 0.0) or 0.0)
        driver = str(getattr(r, "dominant_driver", "") or "—")
        chokes: list = getattr(r, "affected_chokepoints", []) or []
        choke_text = ", ".join(str(c) for c in chokes) if chokes else "—"
        delayed = int(getattr(r, "delayed_voyage_count", 0) or 0)
        route_name = str(
            getattr(r, "route_name", "") or getattr(r, "route_id", "route")
        )
        delayed_color = (
            C_LOW if delayed >= 3 else (C_MOD if delayed >= 1 else C_TEXT3)
        )
        choke_color = C_TEXT2 if chokes else C_TEXT3

        rows.append([
            _route_cell(route_name),
            _mono(f"{stress * 100:.0f}%", color=_stress_color(stress)),
            badge(driver, color=_stress_color(stress)),
            _sans(choke_text[:34], color=choke_color),
            _mono(str(delayed), color=delayed_color),
        ])

    wsj_market_table(
        headers=[
            "Route", "SSI Score", "Dominant Driver",
            "Affected Chokepoints", "Delayed Voyages",
        ],
        rows=rows,
    )


# ── Section E: 7/30-day stress forecast ─────────────────────────────────────

def _render_forecast_callout(forecasts: list) -> None:
    """Render an editorial insight card for the highest-risk forecast lane.

    Surfaces the model's own ``narrative`` and ``drivers`` — fields the table
    cannot show — for whichever lane carries the worst 30-day projection,
    rendered through the platform ``insight_card_html`` component.
    """
    if not forecasts:
        return

    lead = max(
        forecasts,
        key=lambda f: float(getattr(f, "stress_30d", 0.0) or 0.0),
    )
    route_name = str(
        getattr(lead, "route_name", "") or getattr(lead, "route_id", "route")
    )
    s30 = float(getattr(lead, "stress_30d", 0.0) or 0.0)
    trend = str(getattr(lead, "trend", "") or "Stable")
    narrative = str(getattr(lead, "narrative", "") or "")
    drivers: list = getattr(lead, "drivers", []) or []

    if not narrative and not drivers:
        return

    # Compose a one-paragraph rationale: the model narrative, then its drivers.
    rationale = narrative
    if drivers:
        driver_text = "; ".join(str(d) for d in drivers[:4])
        rationale = (
            f"{narrative}  Key drivers: {driver_text}."
            if narrative else f"Key drivers: {driver_text}."
        )

    st.markdown(
        insight_card_html(
            title=f"{route_name} — highest 30-day risk",
            score=max(0.0, min(1.0, s30)),
            action=trend,
            rationale=rationale,
            category="FORECAST",
        ),
        unsafe_allow_html=True,
    )


def _render_forecast_accuracy_panel() -> None:
    """Surface the disruption-forecast accuracy backtest below the forecast.

    Tells the operator how accurate past forecasts have been so the table
    above carries credibility: 4 aggregate KPIs (mean 7d MAE, mean 30d MAE,
    7d sign-agreement, 30d sign-agreement) plus a per-route scorecard table.

    Lazy-imported so a backtest-module failure can't break the rest of the
    tab. Empty / failed backtest is a soft warning, not an error.
    """
    try:
        from processing.disruption_forecast_backtest import (
            backtest_disruption_forecast,
        )
    except Exception:
        logger.exception("Disruption Radar — backtest module import failed")
        return

    section_header(
        "Forecast Accuracy",
        "How accurate have past forecasts been? "
        "MAE = mean absolute error against realized stress; "
        "sign-agreement = % of windows the direction was right.",
    )
    try:
        report = backtest_disruption_forecast()
    except Exception:
        logger.exception("Disruption Radar — forecast backtest failed")
        alert_banner(
            "Forecast accuracy backtest unavailable — see logs.",
            level="warning",
        )
        return

    if not report.scorecards:
        alert_banner("No forecast accuracy data available yet.", level="info")
        return

    # Aggregate KPI row
    def _mae_color(mae: float) -> str:
        if mae <= 0.06:
            return C_HIGH
        if mae <= 0.12:
            return C_MOD
        return C_LOW

    def _sa_color(sa: float) -> str:
        if sa >= 0.60:
            return C_HIGH
        if sa >= 0.50:
            return C_MOD
        return C_LOW

    metric_card_row([
        {"label": "Mean 7d MAE",
         "value": f"{report.mean_mae_7d:.3f}",
         "accent": _mae_color(report.mean_mae_7d),
         "sublabel": "lower = closer to realized"},
        {"label": "Mean 30d MAE",
         "value": f"{report.mean_mae_30d:.3f}",
         "accent": _mae_color(report.mean_mae_30d),
         "sublabel": "wider window, looser fit"},
        {"label": "7d Sign Agreement",
         "value": f"{report.mean_sign_agreement_7d * 100:.1f}%",
         "accent": _sa_color(report.mean_sign_agreement_7d),
         "sublabel": "directional hit rate"},
        {"label": "30d Sign Agreement",
         "value": f"{report.mean_sign_agreement_30d * 100:.1f}%",
         "accent": _sa_color(report.mean_sign_agreement_30d),
         "sublabel": "directional hit rate"},
    ], columns=4)

    # Per-route scorecard table
    headers = ["Route", "Obs", "7d MAE", "30d MAE", "7d Sign", "30d Sign"]
    rows = []
    for sc in sorted(report.scorecards,
                     key=lambda s: (s.mae_7d + s.mae_30d)):
        rows.append([
            badge(sc.route_id, color=C_ACCENT),
            badge(str(sc.n_observations), color=C_TEXT2),
            badge(f"{sc.mae_7d:.3f}", color=_mae_color(sc.mae_7d)),
            badge(f"{sc.mae_30d:.3f}", color=_mae_color(sc.mae_30d)),
            badge(f"{sc.sign_agreement_7d * 100:.0f}%",
                  color=_sa_color(sc.sign_agreement_7d)),
            badge(f"{sc.sign_agreement_30d * 100:.0f}%",
                  color=_sa_color(sc.sign_agreement_30d)),
        ])
    wsj_market_table(headers, rows)
    st.caption(report.summary)


def _render_forecast_table(forecasts: list) -> None:
    """Render the 7/30-day stress forecast table."""
    if not forecasts:
        alert_banner("No stress forecast available.", level="info")
        return

    section_header(
        "7 / 30-Day Stress Forecast",
        "Projected disruption stress, trend and modeled rate outlook per lane",
    )

    # Editorial insight card for the worst-projected lane, above the table.
    _render_forecast_callout(forecasts)

    rows = []
    for f in sorted(
        forecasts,
        key=lambda x: float(getattr(x, "stress_30d", 0.0) or 0.0),
        reverse=True,
    ):
        route_name = str(
            getattr(f, "route_name", "") or getattr(f, "route_id", "route")
        )
        current = float(getattr(f, "current_stress", 0.0) or 0.0)
        s7 = float(getattr(f, "stress_7d", 0.0) or 0.0)
        s30 = float(getattr(f, "stress_30d", 0.0) or 0.0)
        trend = str(getattr(f, "trend", "") or "Stable")
        rate_fc = float(getattr(f, "rate_forecast_pct", 0.0) or 0.0)
        p90 = float(getattr(f, "mc_p90_upside", 0.0) or 0.0)
        band = getattr(f, "stress_30d_band", (0.0, 0.0)) or (0.0, 0.0)
        b_lo, b_hi = float(band[0]), float(band[1])

        rows.append([
            _route_cell(route_name),
            _mono(f"{current * 100:.0f}%", color=_stress_color(current)),
            _mono(f"{s7 * 100:.0f}%", color=_stress_color(s7)),
            _mono(f"{s30 * 100:.0f}%", color=_stress_color(s30)),
            _sans(f"{b_lo * 100:.0f}–{b_hi * 100:.0f}%", color=C_TEXT3),
            badge(trend, color=_trend_color(trend)),
            _mono(f"{rate_fc * 100:+.1f}%", color=_pct_change_color(rate_fc)),
            _mono(f"{p90 * 100:+.1f}%", color=C_TEXT2),
        ])

    wsj_market_table(
        headers=[
            "Route", "Current", "7-Day", "30-Day", "30d Band",
            "Trend", "30d Rate", "MC P90",
        ],
        rows=rows,
    )
    st.caption(
        "30d Band — an illustrative ±1σ (≈68%) interval around the 30-day "
        "stress point, dispersion blended from the Monte-Carlo rate-tail "
        "spread + the route's own rate volatility. Every forward point ships "
        "an interval; it is not a fitted predictive band."
    )


# ── Public entry point ──────────────────────────────────────────────────────

def render(
    freight_data=None,
    macro_data=None,
    port_results=None,
    route_results=None,
    **kwargs,
) -> None:
    """Render the Disruption Radar tab.

    Parameters
    ----------
    freight_data, macro_data, port_results, route_results:
        Platform-standard inputs computed at the top of ``app.py``. Each may be
        ``None`` or empty — the processing modules degrade to neutral defaults,
        so this tab renders cleanly with no inputs at all.
    """
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('disruption_radar'):
        # ── A. Page header ──────────────────────────────────────────────────────
        page_header(
            title="Disruption Radar",
            subtitle="Fleet-wide Shipping Stress Index, per-route disruption "
            "detail and a 7/30-day stress forecast",
            badge_text="MODELED",
            badge_color=C_ACCENT,
        )

        # ── Compute the Shipping Stress Index report ────────────────────────────
        try:
            report = _compute_stress(
                freight_data, macro_data, port_results, route_results
            )
        except Exception:
            logger.exception("Disruption Radar — SSI computation failed")
            st.error("Could not compute the Shipping Stress Index.")
            return

        if report is None:
            alert_banner("No Shipping Stress Index report available.", level="info")
            return

        # ── B. Fleet-wide SSI overview ──────────────────────────────────────────
        try:
            _render_ssi_overview(report, macro_data)
        except Exception:
            logger.exception("Disruption Radar — SSI overview failed")
            st.error("Shipping Stress Index overview unavailable.")

        section_divider("Route Detail")

        # ── C. Per-route stress heat bar ────────────────────────────────────────
        try:
            _render_heat_bar(report)
        except Exception:
            logger.exception("Disruption Radar — heat bar failed")
            st.error("Route stress heat bar unavailable.")

        st.divider()

        # ── D-pre. Route explanations (operator-facing English) ─────────────────
        # Lives ABOVE the per-route disruption table so the operator gets the
        # *why* before the *what*. Whole helper is defensive — its own failure
        # cannot block the table that follows.
        try:
            _render_route_explanations(
                getattr(report, "route_stress", []) or []
            )
        except Exception:
            logger.exception("Disruption Radar — route explanations failed")
            st.warning("Explanations unavailable")

        # ── D. Per-route disruption table ───────────────────────────────────────
        try:
            _render_route_table(report)
        except Exception:
            logger.exception("Disruption Radar — route table failed")
            st.error("Route disruption table unavailable.")

        section_divider("Forecast")

        # ── E. 7/30-day stress forecast ─────────────────────────────────────────
        try:
            forecasts = _compute_forecast(
                freight_data, macro_data, route_results, report
            )
            _render_forecast_table(forecasts)
        except Exception:
            logger.exception("Disruption Radar — stress forecast failed")
            st.error("Stress forecast unavailable.")

        # ── Section E2: forecast-accuracy backtest ──────────────────────────────
        try:
            _render_forecast_accuracy_panel()
        except Exception:
            logger.exception("Disruption Radar — forecast accuracy panel failed")
            st.error("Forecast accuracy backtest unavailable.")

        # ── Provenance footer ───────────────────────────────────────────────────
        try:
            from data.quality import DataSource
            sources = [
                DataSource.modeled(
                    "Modeled Shipping Stress Index",
                    notes="Composite of chokepoint, congestion, weather, rate "
                    "and vulnerability components.",
                ),
                DataSource.modeled(
                    "Modeled Disruption Forecast",
                    notes="7/30-day stress projection over rate and congestion "
                    "forecasters.",
                ),
            ]
            st.markdown(source_footer(sources), unsafe_allow_html=True)
        except Exception:
            logger.exception("Disruption Radar — source footer failed")
