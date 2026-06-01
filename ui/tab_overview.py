"""tab_overview.py — the platform HERO / front page (Dashboard ▸ Overview).

The first screen a user sees and the spine that ties the whole platform
together. A curated, WSJ-front-page lede: it states today's verdict, frames
the session in a handful of figures, runs a market ticker, then fans the user
out into the detail sections via deep-link tiles.

This tab COMPOSES existing components + data — it owns no analytics of its own:

  * Lede        — the daily-briefing TLDR (``engine.daily_briefing_tldr`` over
                  ``engine.narration_engine``), rendered via ``ui.styles.tldr_lede``.
  * Stat strip  — overall SSI (``processing.shipping_stress_index``) as a
                  ``gauge_ring`` + a ``metric_card_row`` of headline figures.
  * Ticker      — ``ui.styles.ticker_tape_html`` over the freight / equity feeds.
  * Tiles       — three+ deep-link cards that route into the detail sections
                  (Risk, Markets, Disruption Alpha, Ports & Routes) by setting
                  ``st.session_state["nav_section"]`` and rerunning.
  * Footer      — ``source_footer`` over honest ``data.quality.DataSource``
                  provenance (most of this platform is MODELED — labelled so).

Canonical tab pattern (mirrors ``ui/tab_world_graph.py``):
  * ``page_header`` first, body inside ``with track_render("overview")``;
  * every data-touching block in its OWN try/except → ``logger`` + ``st.error``
    so an empty / None / odd-shaped input degrades to a graceful tile, never a
    crash;
  * styled output ONLY through ``ui.styles`` helpers — no hand-rolled,
    inline-styled div/span markup (the inline-style budget for this tab's own
    ``st.markdown`` is 0; the lock-in / smoke test enforces it);
  * ``source_footer`` last; steel-blue WSJ identity throughout (``C_*`` only).

The ``render(...)`` signature is the platform-standard dashboard contract and
is dispatched from ``app.py`` (``if active_section == "dashboard":``) BY KEYWORD
for the optional feeds and positionally for the three required model outputs —
the order and names MUST NOT change. ``**_kwargs`` absorbs any future feed.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
from loguru import logger

import plotly.graph_objects as go

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
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
    source_footer,
    status_badge,
    ticker_tape_html,
    tldr_lede,
    wsj_market_table,
)


# ══════════════════════════════════════════════════════════════════════════════
# DEEP-LINK ROUTING CONTRACT
# ══════════════════════════════════════════════════════════════════════════════
# Each hero tile carries a "View →" button that focuses the relevant detail
# section. The orchestrator (app.py) reads ``st.session_state["nav_section"]``
# at the top of every run, so setting it + rerunning is the whole contract.
# These keys MUST match the sidebar section keys app.py dispatches on.

_VALID_SECTIONS: frozenset[str] = frozenset({
    "dashboard", "markets", "disruption_alpha", "ports_routes", "carriers",
    "trade_macro", "supply_chain", "risk", "intelligence", "reports",
})


def _route_to(section: str) -> None:
    """Focus ``section`` in the sidebar and rerun. No-op on an unknown key so
    a typo can never blank the app."""
    if section not in _VALID_SECTIONS:
        logger.warning(f"Overview deep-link to unknown section: {section!r}")
        return
    st.session_state["nav_section"] = section
    st.rerun()


def _nav_button(label: str, section: str, key: str) -> None:
    """Render a 'View →' button that deep-links into ``section``.

    Uses ``st.button`` (a native widget, not styled HTML) so it stays inside
    the inline-style budget. In the smoke harness ``st.button`` returns False,
    so the rerun branch is inert there — render completes cleanly.
    """
    if st.button(label, key=key, use_container_width=True):
        _route_to(section)


# ══════════════════════════════════════════════════════════════════════════════
# SAFE VALUE HELPERS — every feed may be empty / None / odd-shaped
# ══════════════════════════════════════════════════════════════════════════════
# freight_data / macro_data can be keyed by route → DataFrame (the smoke
# bundle) OR carry plain scalar headline keys (the live snapshot). These
# getters only format genuine scalars and degrade to a default otherwise, so
# a DataFrame value never explodes ``"{:,.0f}".format(df)``.


def _is_scalar_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _scalar(d: Any, *keys: str, fmt: str = "{}", default: str = "--") -> str:
    """First scalar value among ``keys`` in dict ``d``, formatted; else default."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if _is_scalar_number(v):
            try:
                return fmt.format(v)
            except Exception:
                return str(v)
    return default


def _safe_avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _last_close(df: Any) -> float | None:
    """Last 'close' (or 'value'/'price') from a price-like DataFrame, defensively."""
    try:
        if df is None or getattr(df, "empty", True):
            return None
        cols = list(getattr(df, "columns", []))
        for col in ("close", "value", "price", "rate_usd_per_feu"):
            if col in cols:
                series = df[col].dropna()
                if len(series):
                    return float(series.iloc[-1])
    except Exception:
        return None
    return None


def _pct_change(df: Any) -> float:
    """First→last % change of a price-like DataFrame; 0.0 if not derivable."""
    try:
        if df is None or getattr(df, "empty", True):
            return 0.0
        cols = list(getattr(df, "columns", []))
        for col in ("close", "value", "price", "rate_usd_per_feu"):
            if col in cols:
                series = df[col].dropna()
                if len(series) >= 2 and float(series.iloc[0]) != 0.0:
                    first, last = float(series.iloc[0]), float(series.iloc[-1])
                    return (last - first) / abs(first) * 100.0
    except Exception:
        return 0.0
    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# PROVENANCE — honest about modeled vs real (most of this is MODELED)
# ══════════════════════════════════════════════════════════════════════════════

def _overview_sources() -> list[DataSource]:
    """Build the footer's source list from ``data.quality`` primitives.

    Honest provenance: the stress index, route optimizer and insight engine
    are MODELED derivations; freight indices are SCRAPED; macro/FX is LIVE.
    """
    try:
        return [
            DataSource.modeled(
                "Shipping Stress Index",
                notes="Fleet-wide SSI composite over the modeled route book.",
            ),
            DataSource.modeled(
                "Decision engine",
                notes="Insights, route opportunities and alerts — modeled.",
            ),
            DataSource.scraped("Freight indices", notes="BDI / SCFI / WCI spot."),
            DataSource.live("Macro / FX"),
        ]
    except Exception:
        logger.exception("Overview — source list build failed")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# PURE BUILDER — signal-conviction heatmap (retained, unit-tested directly)
# ══════════════════════════════════════════════════════════════════════════════
# A self-contained, ``st``-free figure builder kept on the module surface so the
# lock-in tests can exercise the corridor × commodity conviction heatmap in
# isolation. The hero front page itself stays lean (it leads with the lede +
# stat strip + tiles), but this builder is available for any deeper view that
# wants the matrix, and pins the colour-scale / annotation contract.


def _build_signal_conviction_heatmap(
    corridors: list[str],
    commodities: list[str],
    score_grid: list[list[float]],
) -> go.Figure:
    """Heatmap of corridor × commodity conviction scores (0–1).

    Colour scale runs red → gray → green so the bullish cells light up and the
    avoid cells fade out. Each cell is annotated with its percentage so the
    heatmap can stand alone. Pure builder — no ``st.*`` calls. An empty /
    mis-shaped grid returns an annotated-empty figure.
    """
    fig = go.Figure()
    if (not corridors or not commodities
            or not score_grid
            or len(score_grid) != len(corridors)
            or any(len(row) != len(commodities) for row in score_grid)):
        fig.add_annotation(
            text="No signal conviction data",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Signal Conviction", height=240)
        return fig

    # Colour scale: low (avoid) → mid (neutral) → high (strong).
    colorscale = [
        [0.00, "#c0392b"],   # avoid (red)
        [0.35, "#c9962b"],   # caution (amber)
        [0.50, "#5a5650"],   # neutral (mid-gray)
        [0.65, "#2e9e6e"],   # bullish (green)
        [1.00, "#1f8a5b"],   # strong (deeper green)
    ]
    text = [[f"{int(s * 100)}%" for s in row] for row in score_grid]

    fig.add_trace(go.Heatmap(
        z=score_grid,
        x=commodities,
        y=corridors,
        colorscale=colorscale,
        zmin=0.0,
        zmax=1.0,
        text=text,
        texttemplate="%{text}",
        textfont={"color": "#0c0e14", "size": 12},
        hovertemplate=(
            "<b>%{y}</b> · %{x}<br>"
            "Conviction: %{z:.0%}<extra></extra>"
        ),
        showscale=True,
        colorbar={
            "title": {"text": "Conviction", "side": "right",
                      "font": {"color": C_TEXT3, "size": 10}},
            "tickfont": {"color": C_TEXT3, "size": 9},
            "len": 0.85,
            "thickness": 12,
            "outlinewidth": 0,
        },
    ))

    apply_dark_layout(
        fig,
        title="Signal Conviction — corridor × commodity",
        height=max(220, 80 + 44 * len(corridors)),
    )
    fig.update_layout(
        xaxis={"title": None, "side": "top",
               "tickfont": {"color": C_TEXT2, "size": 11}},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 60, "t": 60, "b": 24},
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 2. LEDE — daily-briefing TLDR
# ══════════════════════════════════════════════════════════════════════════════

def _render_lede(
    port_results: list, route_results: list,
    freight_data: dict, macro_data: dict,
) -> None:
    """Render the one-paragraph TLDR lede via ``ui.styles.tldr_lede``.

    Best-effort: assemble a ``NarrationContext`` from the existing platform
    signals (SSI + top forecasts), distill it with
    ``engine.daily_briefing_tldr.generate_tldr`` (Haiku, with a deterministic
    template + day-cache fallback when no API key is set), and render the
    resulting paragraph. Any failure synthesizes a sensible line from the
    SSI label so the front page always opens with a verdict.
    """
    text, source = "", "template"
    try:
        from engine.narration_engine import (
            NarrationContext,
            generate_daily_narration,
        )
        from engine.daily_briefing_tldr import generate_tldr

        stress_report = None
        try:
            from processing.shipping_stress_index import compute_shipping_stress
            stress_report = compute_shipping_stress(
                freight_data, macro_data, port_results, route_results,
            )
        except Exception as exc:
            logger.debug(f"Overview lede: SSI compute failed: {exc}")

        forecasts: list = []
        try:
            from processing.disruption_forecast import forecast_all_stress
            forecasts = sorted(
                forecast_all_stress(
                    freight_data, macro_data, route_results,
                    stress_report=stress_report,
                ),
                key=lambda f: getattr(f, "stress_30d", 0.0),
                reverse=True,
            )[:5]
        except Exception as exc:
            logger.debug(f"Overview lede: forecast compute failed: {exc}")

        ctx = NarrationContext(
            stress_report=stress_report,
            top_forecasts=forecasts,
            notable_indicators={},
        )
        narration = generate_daily_narration(ctx)
        summary = generate_tldr(narration)
        text, source = (summary.text or "").strip(), summary.source
    except Exception:
        logger.exception("Overview — lede TLDR generation failed")

    # Synthesized fallback — derive a verdict line from the SSI directly so the
    # hero never opens blank, even with no narration / no key / empty feeds.
    if not text:
        try:
            from processing.shipping_stress_index import compute_shipping_stress
            rep = compute_shipping_stress(
                freight_data, macro_data, port_results, route_results,
            )
            label = getattr(rep, "ssi_label", "Calm")
            ssi = getattr(rep, "overall_ssi", 0.0)
            top = list(getattr(rep, "top_disruptions", []) or [])[:1]
            lead = f" Leading driver: {top[0]}." if top else ""
            text = (
                f"Shipping stress reads {label.lower()} at {ssi:.0%} across the "
                f"tracked route book.{lead} Scan the strip below for the session's "
                f"headline figures, then drill into any section from the tiles."
            )
        except Exception:
            logger.exception("Overview — synthesized lede fallback failed")
            text = (
                "Live feeds have not populated yet — the figures below are "
                "illustrative until a refresh completes."
            )

    try:
        tldr_lede(text, source=source)
    except Exception:
        logger.exception("Overview — tldr_lede render failed")


# ══════════════════════════════════════════════════════════════════════════════
# 3. STAT STRIP — SSI gauge + headline KPI cards
# ══════════════════════════════════════════════════════════════════════════════

def _compute_ssi(port_results, route_results, freight_data, macro_data):
    """Best-effort SSI report; None if the engine is unavailable."""
    try:
        from processing.shipping_stress_index import compute_shipping_stress
        return compute_shipping_stress(
            freight_data, macro_data, port_results, route_results,
        )
    except Exception:
        logger.exception("Overview — SSI compute failed")
        return None


def _biggest_mover(stock_data: dict) -> tuple[str, float]:
    """Ticker + abs-largest % move across the equity feed; ('--', 0.0) if none."""
    best_sym, best_pct = "--", 0.0
    if isinstance(stock_data, dict):
        for sym, df in stock_data.items():
            pct = _pct_change(df)
            if abs(pct) > abs(best_pct):
                best_sym, best_pct = str(sym), pct
    return best_sym, best_pct


def _top_chokepoint(ssi_report) -> str:
    """The headline disruption driver from the SSI report, if any."""
    try:
        top = list(getattr(ssi_report, "top_disruptions", []) or [])
        if top:
            return str(top[0])[:34]
    except Exception:
        pass
    return "None flagged"


def _render_stat_strip(
    ssi_report, alerts: list, stock_data: dict,
) -> None:
    """SSI gauge alongside a headline ``metric_card_row``.

    Cards: overall SSI label, active alert load, biggest equity mover, and the
    highest-risk chokepoint. Each carries a semantic accent. The gauge is a
    ``gauge_ring`` (a ``ui.styles`` figure helper) so the lede reads as a
    dashboard, not a wall of numbers.
    """
    try:
        section_header(
            "The session in figures",
            subtitle="Stress index, alert load, the biggest equity mover and the "
            "leading chokepoint — the whole platform's pulse in one strip.",
        )
        ssi = float(getattr(ssi_report, "overall_ssi", 0.0) or 0.0)
        ssi_label = str(getattr(ssi_report, "ssi_label", "--") or "--")
        ssi_color = str(getattr(ssi_report, "ssi_color", "") or "") or C_ACCENT

        n_alerts = len(alerts) if alerts else 0
        crit = sum(
            1 for a in (alerts or [])
            if str(getattr(a, "severity", "")).upper() in ("CRITICAL", "HIGH")
        )
        alert_color = C_LOW if crit else (C_MOD if n_alerts else C_HIGH)
        alert_sub = (
            f"{crit} critical" if crit
            else ("monitoring" if n_alerts else "all clear")
        )

        mover_sym, mover_pct = _biggest_mover(stock_data)
        mover_val = f"{mover_pct:+.1f}%" if mover_sym != "--" else "--"
        mover_color = (
            C_HIGH if mover_pct > 0 else (C_LOW if mover_pct < 0 else C_TEXT3)
        )

        choke = _top_chokepoint(ssi_report)
        choke_color = C_LOW if choke not in ("None flagged", "--") else C_TEXT3

        gauge_col, cards_col = st.columns([1, 3], gap="large")
        with gauge_col:
            try:
                st.plotly_chart(
                    gauge_ring(
                        max(0.0, min(1.0, ssi)),
                        label="Stress Index",
                        color=ssi_color,
                        size=180,
                    ),
                    use_container_width=True,
                    config={"displayModeBar": False},
                    key="overview_ssi_gauge",
                )
            except Exception:
                logger.exception("Overview — SSI gauge render failed")
        with cards_col:
            metric_card_row(
                [
                    {"label": "Stress Index", "value": ssi_label,
                     "accent": ssi_color, "sublabel": f"SSI {ssi:.0%} composite"},
                    {"label": "Active Alerts", "value": str(n_alerts),
                     "accent": alert_color, "sublabel": alert_sub},
                    {"label": "Biggest Mover", "value": mover_val,
                     "accent": mover_color,
                     "sublabel": mover_sym if mover_sym != "--" else "no equity feed"},
                    {"label": "Top Chokepoint", "value": choke,
                     "accent": choke_color, "sublabel": "leading stress driver"},
                ],
                columns=4,
            )
    except Exception:
        logger.exception("Overview — stat strip render failed")


# ══════════════════════════════════════════════════════════════════════════════
# 3b. FEATURED SIGNAL — the single highest-conviction call (editorial lead)
# ══════════════════════════════════════════════════════════════════════════════

def _render_featured_signal(insights: list) -> None:
    """Promote the single highest-conviction insight to an editorial card.

    Uses ``ui.styles.insight_card_html`` so the front page leads with one
    decisive verdict before the eye reaches the tiles. No-ops gracefully when
    no signals are present.
    """
    try:
        ranked = sorted(
            (i for i in insights if getattr(i, "score", None) is not None),
            key=lambda i: getattr(i, "score", 0.0), reverse=True,
        )
        if not ranked:
            return
        lead = ranked[0]
        section_header(
            "Featured Signal",
            subtitle="The single highest-conviction call across the signal book.",
        )
        st.markdown(
            insight_card_html(
                title=(getattr(lead, "title", "--") or "--")[:90],
                score=max(0.0, min(1.0, float(getattr(lead, "score", 0.5) or 0.5))),
                action=getattr(lead, "action", "Monitor") or "Monitor",
                rationale=(getattr(lead, "detail", "") or "").strip()[:220],
                category=(getattr(lead, "category", "") or "").upper(),
            ),
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Overview — featured signal render failed")


# ══════════════════════════════════════════════════════════════════════════════
# 4. TICKER TAPE — key freight / equity markers
# ══════════════════════════════════════════════════════════════════════════════

def _render_ticker(freight_data: dict, stock_data: dict) -> None:
    """Render a scrolling market ticker via ``ui.styles.ticker_tape_html``.

    ``ticker_tape_html`` RETURNS HTML, so it's rendered through
    ``st.markdown(..., unsafe_allow_html=True)`` — allowed because the HTML
    comes from a ``ui.styles`` helper, not a hand-rolled inline div. Best-effort
    items from the freight indices and the equity feed; on no scalar feed it
    falls back to illustrative benchmark markers so the tape never reads empty.
    """
    try:
        items: list[dict] = []

        # Freight / macro benchmarks — only scalar headline keys (the live
        # snapshot); route→DataFrame feeds are skipped here (they have no
        # single headline scalar).
        fd = freight_data if isinstance(freight_data, dict) else {}
        benchmark_specs = [
            ("BDI", ("bdi", "BDI"), "{:,.0f}"),
            ("SCFI", ("scfi", "SCFI"), "{:,.0f}"),
            ("WCI", ("wci", "WCI"), "{:,.0f}"),
            ("VLCC", ("vlcc_rate", "vlcc"), "${:,.0f}"),
        ]
        for label, keys, fmt in benchmark_specs:
            val = _scalar(fd, *keys, fmt=fmt, default="")
            if val:
                items.append({"label": label, "value": val, "unit": "", "change": 0.0})

        # Equities — last close + first→last % change from the feed DataFrames.
        if isinstance(stock_data, dict):
            for sym, df in list(stock_data.items())[:8]:
                close = _last_close(df)
                if close is None:
                    continue
                items.append({
                    "label": str(sym),
                    "value": f"{close:,.2f}",
                    "unit": "",
                    "change": float(_pct_change(df)),
                })

        # Fallback — illustrative benchmarks so the tape always has content.
        if not items:
            items = [
                {"label": "BDI", "value": "1,847", "unit": "", "change": 1.3},
                {"label": "SCFI", "value": "2,856", "unit": "", "change": 2.4},
                {"label": "WCI", "value": "3,210", "unit": "", "change": -1.4},
                {"label": "VLCC", "value": "$32,500", "unit": "", "change": 0.6},
                {"label": "Crude", "value": "81.34", "unit": "", "change": 0.6},
            ]

        st.markdown(ticker_tape_html(items), unsafe_allow_html=True)
    except Exception:
        logger.exception("Overview — ticker tape render failed")


# ══════════════════════════════════════════════════════════════════════════════
# 5. DEEP-LINK TILES — curated summaries that route into the detail sections
# ══════════════════════════════════════════════════════════════════════════════

_SEV_STATUS: dict[str, str] = {
    "CRITICAL": "danger", "HIGH": "danger",
    "WARNING": "warning", "MEDIUM": "warning", "MODERATE": "warning",
    "INFO": "info", "LOW": "success",
}


def _tile_alerts(insights: list, alerts: list) -> None:
    """Top Alerts → Risk. Highest-severity items, with a deep-link button."""
    try:
        items: list[tuple[str, str]] = []
        if alerts:
            for a in alerts[:3]:
                sev = str(getattr(a, "severity", "WARNING") or "WARNING").upper()
                title = (
                    getattr(a, "title", "") or getattr(a, "message", "") or "--"
                )
                items.append((sev, str(title)[:48]))
        else:
            risky = sorted(
                (i for i in insights if getattr(i, "score", 0) >= 0.75),
                key=lambda i: getattr(i, "score", 0), reverse=True,
            )[:3]
            for ins in risky:
                sev = "HIGH" if getattr(ins, "score", 0) >= 0.85 else "MODERATE"
                items.append((sev, str(getattr(ins, "title", "--") or "--")[:48]))

        section_divider("Top Alerts")
        if items:
            rows = [
                [status_badge(sev, _SEV_STATUS.get(sev, "warning")), badge(title, color=C_TEXT3)]
                for sev, title in items
            ]
            wsj_market_table(["Severity", "Detail"], rows)
        else:
            alert_banner("No active alerts — the network reads clean.", level="success")
        _nav_button("View Risk →", "risk", key="overview_tile_alerts_btn")
    except Exception:
        logger.exception("Overview — alerts tile render failed")
        st.error("Top-alerts tile unavailable.")


def _tile_equities(stock_data: dict) -> None:
    """Equity Ideas → Markets. Biggest movers in the feed, with a deep-link."""
    try:
        movers: list[tuple[str, float]] = []
        if isinstance(stock_data, dict):
            for sym, df in stock_data.items():
                pct = _pct_change(df)
                if _last_close(df) is not None:
                    movers.append((str(sym), pct))
        movers.sort(key=lambda t: abs(t[1]), reverse=True)
        movers = movers[:3]

        section_divider("Equity Ideas")
        if movers:
            rows = []
            for sym, pct in movers:
                color = C_HIGH if pct > 0 else (C_LOW if pct < 0 else C_TEXT3)
                rows.append([badge(sym, color=C_ACCENT), badge(f"{pct:+.1f}%", color=color)])
            wsj_market_table(["Ticker", "Move"], rows)
        else:
            alert_banner(
                "No equity feed yet — connect a price source to surface ideas.",
                level="info",
            )
        _nav_button("View Markets →", "markets", key="overview_tile_equities_btn")
    except Exception:
        logger.exception("Overview — equities tile render failed")
        st.error("Equity-ideas tile unavailable.")


def _tile_disruptions(ssi_report) -> None:
    """Disruptions → Disruption Alpha. SSI label + top drivers, with a deep-link."""
    try:
        section_divider("Disruptions")
        label = str(getattr(ssi_report, "ssi_label", "--") or "--")
        ssi = float(getattr(ssi_report, "overall_ssi", 0.0) or 0.0)
        color = str(getattr(ssi_report, "ssi_color", "") or "") or C_ACCENT
        drivers = list(getattr(ssi_report, "top_disruptions", []) or [])[:3]

        rows = [[badge("Stress Index", color=C_TEXT3),
                 badge(f"{label} · {ssi:.0%}", color=color)]]
        for d in drivers:
            rows.append([badge("Driver", color=C_LOW), badge(str(d)[:46], color=C_TEXT3)])
        wsj_market_table(["Signal", "Reading"], rows)

        if not drivers:
            alert_banner("No active disruptions flagged on the route book.", level="success")
        _nav_button("View Disruption Alpha →", "disruption_alpha",
                    key="overview_tile_disruptions_btn")
    except Exception:
        logger.exception("Overview — disruptions tile render failed")
        st.error("Disruptions tile unavailable.")


def _tile_routes(route_results: list) -> None:
    """Ports & Routes → Ports & Routes. Top-scoring lanes, with a deep-link."""
    try:
        strong = sorted(
            (r for r in route_results if getattr(r, "opportunity_score", 0) >= 0.55),
            key=lambda r: getattr(r, "opportunity_score", 0), reverse=True,
        )[:3]

        section_divider("Ports & Routes")
        if strong:
            rows = []
            for r in strong:
                name = (
                    getattr(r, "route_name", "")
                    or getattr(r, "route_id", "") or "--"
                )
                score = float(getattr(r, "opportunity_score", 0.0) or 0.0)
                sc = C_HIGH if score >= 0.70 else (C_MOD if score >= 0.45 else C_LOW)
                rows.append([badge(str(name)[:26], color=C_ACCENT),
                             badge(f"{int(score * 100)}%", color=sc)])
            wsj_market_table(["Lane", "Score"], rows)
        else:
            alert_banner(
                "No standout lanes right now — the optimizer found no strong "
                "opportunities.",
                level="info",
            )
        _nav_button("View Ports & Routes →", "ports_routes",
                    key="overview_tile_routes_btn")
    except Exception:
        logger.exception("Overview — routes tile render failed")
        st.error("Ports & routes tile unavailable.")


def _render_tiles(
    insights: list, alerts: list, stock_data: dict,
    ssi_report, route_results: list,
) -> None:
    """Lay the four deep-link tiles out across a column grid."""
    try:
        section_header(
            "Jump to a desk",
            subtitle="Each tile previews its desk and links straight through — "
            "Risk, Markets, Disruption Alpha and Ports & Routes.",
        )
        cols = st.columns(4, gap="large")
        with cols[0]:
            _tile_alerts(insights, alerts)
        with cols[1]:
            _tile_equities(stock_data)
        with cols[2]:
            _tile_disruptions(ssi_report)
        with cols[3]:
            _tile_routes(route_results)
    except Exception:
        logger.exception("Overview — tiles row render failed")
        st.error("Deep-link tiles unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ══════════════════════════════════════════════════════════════════════════════

def render(
    port_results,
    route_results,
    insights,
    freight_data=None,
    macro_data=None,
    stock_data=None,
    alerts=None,
    **_kwargs,
) -> None:
    """Render the platform HERO / Overview front page.

    Parameters
    ----------
    port_results, route_results, insights:
        Platform-standard model outputs computed at the top of ``app.py``.
        Required and positional — ``app.py`` passes these by position.
    freight_data, macro_data, stock_data, alerts:
        Optional feed dicts / alert list — each may be ``None`` or empty, in
        which case the hero degrades to illustrative tiles and a synthesized
        lede rather than raising. Passed BY KEYWORD from the dashboard dispatch.
    **_kwargs:
        Absorbs any future feed the orchestrator may pass, keeping the
        signature forward-compatible.

    The render body runs inside ``engine.perf_telemetry.track_render`` (the
    same one-line wrapper every tab uses); the context manager re-raises, so
    the outer try/except still catches every exception — the hero NEVER
    crashes, it only degrades.
    """
    from engine.perf_telemetry import track_render

    try:
        with track_render("overview"):
            port_results  = port_results  or []
            route_results = route_results or []
            insights      = insights      or []
            freight_data  = freight_data  if isinstance(freight_data, dict) else {}
            macro_data    = macro_data    if isinstance(macro_data, dict) else {}
            stock_data    = stock_data    if isinstance(stock_data, dict) else {}
            alerts        = alerts        or []

            # ── 1. Page header ──────────────────────────────────────────────
            live = bool(stock_data) or bool(freight_data) or bool(macro_data)
            page_header(
                title="Shipping Intelligence",
                subtitle="Today's verdict, the session in figures, and a fast "
                "path into every desk — the whole platform on one page.",
                badge_text="LIVE" if live else "OVERVIEW",
                badge_color=C_HIGH if live else C_ACCENT,
            )

            # ── 2. Lede — daily-briefing TLDR ───────────────────────────────
            _render_lede(port_results, route_results, freight_data, macro_data)

            # SSI computed once and threaded through the strip + the
            # disruptions tile (one source of truth, one engine call).
            ssi_report = _compute_ssi(
                port_results, route_results, freight_data, macro_data,
            )

            # ── 3. Stat strip — SSI gauge + headline KPI cards ──────────────
            _render_stat_strip(ssi_report, alerts, stock_data)

            # ── 3b. Featured signal — editorial lead card ───────────────────
            _render_featured_signal(insights)

            # ── 4. Ticker tape — key freight / equity markers ───────────────
            _render_ticker(freight_data, stock_data)

            # ── 5. Deep-link tiles — curated summaries + "View →" routing ───
            _render_tiles(insights, alerts, stock_data, ssi_report, route_results)

            # ── 6. Source footer — honest modeled-vs-real provenance ────────
            try:
                st.markdown(
                    source_footer(_overview_sources()),
                    unsafe_allow_html=True,
                )
            except Exception:
                logger.exception("Overview — source footer render failed")
    except Exception as exc:
        logger.exception("tab_overview.render fatal")
        st.error(f"Overview hero error: {exc}")
