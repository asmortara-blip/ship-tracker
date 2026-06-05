"""ui/tab_idea_engine.py — Signal-to-Trade Ideas dashboard.

Phase-4 tab. Synthesizes the Phase-3 infrastructure into one screen:

  • disruption_cascade.score_equity_ideas   → ranked EquityIdea list
  • state.scenarios.overlay_value           → what-if shock on ticker returns
  • engine.portfolio_optimizer              → optimal weights for top ideas
  • processing.congestion_rate_lag (when    → port→rate lead-time context
        applicable to a route in the idea's cascade chain)

Five sections:
  1. Hero — top-conviction idea
  2. Ranked ideas table — with scenario-overlay Δ column
  3. Cascade rationale — per-idea expander with full provenance
  4. Optimization Lab Mini — max-Sharpe over top N bullish ideas
  5. Source footer
"""
from __future__ import annotations

import datetime
import html
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)
from utils.helpers import stable_hash


# ─── Color / formatting helpers ─────────────────────────────────────────────

def _direction_color(direction: str) -> str:
    return {"Bullish": C_HIGH, "Bearish": C_LOW, "Neutral": C_MOD}.get(direction, C_TEXT2)


def _conviction_color(label: str) -> str:
    return {"High": C_HIGH, "Moderate": C_MOD, "Watch": C_TEXT2, "Low": C_TEXT3}.get(
        label, C_TEXT2
    )


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


# ─── Idea synthesis (build the ranked list once, cache briefly) ─────────────

def _build_ideas(
    port_results, route_results, freight_data, macro_data, stock_data, insights,
) -> list:
    """Run the Phase-3 cascade pipeline and return a sorted list of EquityIdea.

    Composes:
      compute_shipping_stress → build_exposure_matrix → score_equity_ideas

    Each call is in its own try/except so a failure in one stage doesn't
    blank the whole tab. Returns [] if the cascade can't produce anything.
    """
    try:
        from processing.shipping_stress_index import compute_shipping_stress
        from processing.exposure_matrix import build_exposure_matrix
        from processing.disruption_cascade import score_equity_ideas

        stress_report = compute_shipping_stress(
            freight_data or {}, macro_data or {},
            port_results or [], route_results or [],
        )
        exposure = build_exposure_matrix(stock_data or {})
        ideas = score_equity_ideas(
            stress_report, exposure, stock_data or {}, insights or [],
        )
        # Sort by conviction descending — highest-conviction first.
        ideas.sort(key=lambda i: getattr(i, "conviction_score", 0.0), reverse=True)
        return ideas
    except Exception as exc:
        logger.exception(f"idea_engine: cascade pipeline failed: {exc}")
        return []


# ─── Pure figure-builder ────────────────────────────────────────────────────

def _build_idea_conviction_bars(ideas: list, *, limit: int = 12) -> go.Figure:
    """Horizontal conviction-score bars for the top N ideas.

    Sorts highest-conviction first (top of chart). Bars are coloured by
    direction (Bullish → C_HIGH, Bearish → C_LOW, Neutral → C_TEXT2)
    so the directional mix is scannable without reading the labels.
    Limits to ``limit`` rows so the chart stays on one screen even when
    the cascade surfaces a long tail of low-conviction ideas.

    Pure builder — no ``st.*`` calls — so the lock-in tests exercise it
    directly. Empty / None ideas returns an annotated-empty figure.
    """
    fig = go.Figure()
    items = list(ideas or [])
    if not items:
        fig.add_annotation(
            text="No ideas to plot",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Top ideas by conviction", height=240)
        return fig

    # Sort by conviction desc, take top N, then reverse so Plotly's
    # bottom-up categorical axis puts the strongest at the TOP of the chart.
    ranked = sorted(
        items,
        key=lambda idea: float(getattr(idea, "conviction_score", 0.0) or 0.0),
        reverse=True,
    )[:limit]
    ranked.reverse()

    tickers = [str(getattr(idea, "ticker", "?")) for idea in ranked]
    scores  = [float(getattr(idea, "conviction_score", 0.0) or 0.0)
               for idea in ranked]
    directions = [str(getattr(idea, "direction", "Neutral") or "Neutral")
                  for idea in ranked]
    labels = [str(getattr(idea, "conviction_label", "") or "")
              for idea in ranked]
    colors = [_direction_color(d) for d in directions]

    fig.add_trace(go.Bar(
        x=scores,
        y=tickers,
        orientation="h",
        marker={"color": colors,
                "line": {"color": C_BG, "width": 1}},
        text=[f"{s:.2f}" for s in scores],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 11},
        customdata=list(zip(directions, labels)),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Conviction: %{x:.2f} (%{customdata[1]})<br>"
            "Direction: %{customdata[0]}<extra></extra>"
        ),
        showlegend=False,
    ))

    apply_dark_layout(
        fig,
        title=f"Top ideas by conviction — showing {len(ranked)} of {len(items)}",
        height=max(220, 60 + 28 * len(ranked)),
    )
    fig.update_layout(
        xaxis={"title": "Conviction score (0–1)", "range": [0, 1.08],
               "gridcolor": "rgba(255,255,255,0.04)"},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 60, "t": 44, "b": 40},
        bargap=0.35,
    )
    return fig


# ─── Section 1: Hero — top-conviction idea ───────────────────────────────────

def _render_hero(top_idea, active_scenario) -> None:
    if top_idea is None:
        st.info(
            "No ideas surfaced yet — the cascade scorer hasn't produced output. "
            "Try refreshing data or selecting a scenario from the sidebar."
        )
        return
    direction = getattr(top_idea, "direction", "Neutral")
    direction_color = _direction_color(direction)
    conviction_color = _conviction_color(getattr(top_idea, "conviction_label", ""))
    scenario_chip = ""
    if active_scenario is not None:
        scenario_chip = (
            f' · <span style="color:{C_ACCENT};font-weight:600">'
            f'scenario: {active_scenario.name}</span>'
        )

    st.markdown(
        f'<div style="background:rgba(53,114,176,0.10);'
        f'border-left:3px solid {direction_color};padding:14px 18px;'
        f'border-radius:3px;margin-bottom:12px">'
        f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
        f'color:{C_TEXT3};font-weight:600;margin-bottom:4px">'
        f'TOP CONVICTION IDEA · {direction.upper()} '
        f'· conviction {getattr(top_idea, "conviction_score", 0.0):.2f}'
        f'{scenario_chip}</div>'
        f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1.25rem;'
        f'color:{C_TEXT};font-weight:700">'
        f'{getattr(top_idea, "ticker", "?")} — {getattr(top_idea, "company_name", "")}</div>'
        f'<div style="font-family:Libre Franklin,sans-serif;font-size:0.86rem;'
        f'color:{C_TEXT2};margin-top:8px;line-height:1.5">'
        f'{getattr(top_idea, "thesis", "")}</div>'
        f'<div style="margin-top:8px">'
        f'{badge(getattr(top_idea, "conviction_label", ""), color=conviction_color)}'
        f' {badge(direction, color=direction_color)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ─── Section 2: Ranked ideas table with scenario overlay ────────────────────

def _render_ranked_table(ideas: list, active_scenario) -> None:
    """Each row: rank, ticker, direction, conviction, base return, scenario-
    adjusted return, Δ%, top driver. The scenario columns are populated only
    when an active scenario is set."""
    if not ideas:
        return

    section_header(
        "Ranked Trade Ideas",
        subtitle=(
            "Sorted by conviction. Right two columns reflect the active "
            "what-if scenario from the sidebar — Δ% is the return shock the "
            "scenario applies to this ticker."
            if active_scenario else
            "Sorted by conviction. Select a scenario in the sidebar to see "
            "how each idea moves under a what-if shock."
        ),
    )

    try:
        from state.scenarios import overlay_multiplier
    except Exception:
        overlay_multiplier = lambda target, scenario=None: 1.0  # type: ignore

    headers = ["#", "Ticker", "Direction", "Conviction", "Base 30d %"]
    if active_scenario is not None:
        headers += [f"Scenario {active_scenario.id} %", "Δ vs Base"]
    headers += ["Top Driver"]

    rows: list[list[str]] = []
    for rank, idea in enumerate(ideas[:15], start=1):
        ticker = getattr(idea, "ticker", "?")
        direction = getattr(idea, "direction", "Neutral")
        conviction_label = getattr(idea, "conviction_label", "")
        conviction_score = getattr(idea, "conviction_score", 0.0)
        base_30d = float(getattr(idea, "change_30d", 0.0) or 0.0) * 100.0

        top_driver = "—"
        signals = getattr(idea, "supporting_signals", []) or []
        if signals:
            top_driver = (signals[0] or "—")[:60]

        row = [
            _mono(f"{rank}", color=C_TEXT3),
            _sans(ticker, color=C_TEXT, weight=700),
            _sans(direction, color=_direction_color(direction)),
            _sans(
                f"{conviction_label} ({conviction_score:.2f})",
                color=_conviction_color(conviction_label),
            ),
            _mono(
                f"{base_30d:+5.1f}%",
                color=(C_HIGH if base_30d > 0 else (C_LOW if base_30d < 0 else C_TEXT2)),
            ),
        ]
        if active_scenario is not None:
            mult = overlay_multiplier(f"ticker:{ticker}.return", active_scenario)
            scenario_30d = base_30d * mult
            delta = scenario_30d - base_30d
            row += [
                _mono(
                    f"{scenario_30d:+5.1f}%",
                    color=(C_HIGH if scenario_30d > 0 else (C_LOW if scenario_30d < 0 else C_TEXT2)),
                ),
                _mono(
                    f"{delta:+5.1f}%",
                    color=(C_HIGH if delta > 0 else (C_LOW if delta < 0 else C_TEXT3)),
                ),
            ]
        row += [_sans(top_driver, color=C_TEXT2)]
        rows.append(row)

    wsj_market_table(headers, rows)


# ─── Section 3: Per-idea cascade rationale (expanders) ──────────────────────

def _render_rationale(ideas: list, limit: int = 5) -> None:
    if not ideas:
        return
    section_header(
        "Cascade Rationale",
        subtitle=f"Full provenance for the top {min(limit, len(ideas))} ideas.",
    )
    for idea in ideas[:limit]:
        ticker = getattr(idea, "ticker", "?")
        direction = getattr(idea, "direction", "Neutral")
        conviction_label = getattr(idea, "conviction_label", "")
        conviction_score = getattr(idea, "conviction_score", 0.0)
        dot = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "⚪"}.get(direction, "⚪")
        with st.expander(
            f"{dot} {ticker} — {direction} · {conviction_label} ({conviction_score:.2f})",
            expanded=False,
        ):
            # Thesis
            st.markdown(
                f'<div style="font-size:0.82rem;line-height:1.5;color:{C_TEXT2};'
                f'margin-bottom:10px">{getattr(idea, "thesis", "—")}</div>',
                unsafe_allow_html=True,
            )

            # Cascade chain — each hop on its own line.
            chain = getattr(idea, "cascade_chain", []) or []
            if chain:
                st.markdown(
                    f'<div style="font-size:0.7rem;text-transform:uppercase;'
                    f'letter-spacing:0.10em;color:{C_TEXT};font-weight:700;'
                    f'margin-bottom:4px">Cascade chain</div>',
                    unsafe_allow_html=True,
                )
                for hop in chain:
                    explain = getattr(hop, "explanation", None) or str(hop)
                    weight_val = getattr(hop, "weight", None)
                    weight_str = (
                        f' <span style="color:{C_TEXT3}">[w={weight_val:.2f}]</span>'
                        if weight_val is not None else ""
                    )
                    st.markdown(
                        f'<div style="font-size:0.76rem;line-height:1.45;color:{C_TEXT2};'
                        f'padding-left:14px;border-left:1px dotted {C_TEXT3};'
                        f'margin-bottom:4px">'
                        f'• {explain}{weight_str}</div>',
                        unsafe_allow_html=True,
                    )

            # Supporting signals + risk flags side by side
            signals = getattr(idea, "supporting_signals", []) or []
            risks = getattr(idea, "risk_flags", []) or []
            cols = st.columns(2, gap="medium")
            with cols[0]:
                if signals:
                    st.markdown(
                        f'<div style="font-size:0.7rem;text-transform:uppercase;'
                        f'letter-spacing:0.10em;color:{C_HIGH};font-weight:700;'
                        f'margin-top:8px">Supporting</div>',
                        unsafe_allow_html=True,
                    )
                    for s in signals[:6]:
                        st.markdown(
                            f'<div style="font-size:0.74rem;color:{C_TEXT2};'
                            f'margin-bottom:2px">+ {s}</div>',
                            unsafe_allow_html=True,
                        )
            with cols[1]:
                if risks:
                    st.markdown(
                        f'<div style="font-size:0.7rem;text-transform:uppercase;'
                        f'letter-spacing:0.10em;color:{C_LOW};font-weight:700;'
                        f'margin-top:8px">Risks</div>',
                        unsafe_allow_html=True,
                    )
                    for r in risks[:6]:
                        st.markdown(
                            f'<div style="font-size:0.74rem;color:{C_TEXT2};'
                            f'margin-bottom:2px">⚠ {r}</div>',
                            unsafe_allow_html=True,
                        )


# ─── Section 4: Mini portfolio optimization on top bullish ideas ────────────

def _synth_returns_panel(tickers: list[str], n: int = 504) -> pd.DataFrame:
    """Deterministic synthetic 2-year daily-returns panel.

    Same construction as in tab_portfolio — per-ticker mean/vol via
    stable_hash, uniform 0.30 pairwise correlation. The platform doesn't
    yet persist a per-ticker history; this gives the optimizer a real
    panel to work with without inventing a new data source for this tab.
    """
    if not tickers:
        return pd.DataFrame()
    k = len(tickers)
    means = np.array([
        0.0002 + (stable_hash(t + "mu") % 10) / 10_000.0
        for t in tickers
    ])
    vols = np.array([
        0.014 + (stable_hash(t + "vol") % 100) / 5_000.0
        for t in tickers
    ])
    corr = np.full((k, k), 0.30)
    np.fill_diagonal(corr, 1.0)
    cov = np.outer(vols, vols) * corr
    rng = np.random.default_rng(stable_hash("idea_panel_" + "".join(tickers)) % (2**31))
    samples = rng.multivariate_normal(mean=means, cov=cov, size=n)
    dates = pd.date_range(end=datetime.date.today(), periods=n, freq="B")
    return pd.DataFrame(samples, index=dates, columns=tickers)


def _render_track_record(stock_data=None) -> None:
    """Forward, frozen track record from the point-in-time signal ledger (R004).

    Shows how PAST EquityIdeas — frozen AT ISSUE, never refit — have done when
    marked on real closes (no look-ahead). Empty-states until the daily freeze
    job has accrued history.
    """
    try:
        from state.signal_ledger import (
            load_ledger,
            oos_scorecard,
            tier_drawdown,
            track_record_summary,
        )

        n_frozen = len(load_ledger(limit=10_000))
        if n_frozen == 0:
            st.caption(
                "No frozen signals yet — the point-in-time track record accrues "
                "daily as ideas are issued (schema v32)."
            )
            return
        summ = track_record_summary(stock_data)
        if summ["n"] == 0:
            st.caption(
                f"{n_frozen} signal(s) frozen; awaiting priced marks "
                "(live prices needed to score them)."
            )
            return
        metric_card_row([
            {"label": "Marked Signals", "value": str(summ["n"]),
             "accent": C_ACCENT, "sublabel": f"of {n_frozen} frozen"},
            {"label": "Hit Rate", "value": f"{summ['hit_rate'] * 100:.0f}%",
             "accent": (C_HIGH if summ["hit_rate"] > 0.5 else C_LOW),
             "sublabel": "signed return > 0"},
            {"label": "Mean Signed Return",
             "value": f"{summ['mean_signed_return_pct']:+.1f}%",
             "accent": (C_HIGH if summ["mean_signed_return_pct"] > 0 else C_LOW),
             "sublabel": "per idea, forward"},
        ], columns=3)
        by = summ.get("by_label", {})
        if by:
            st.caption("By conviction — " + " · ".join(
                f"{lab}: {b['hit_rate'] * 100:.0f}% hit, "
                f"{b['mean_signed_return_pct']:+.1f}%"
                for lab, b in sorted(by.items())
            ))
        # Drawdown kill-switch — surface any conviction tier whose forward
        # track record has cratered (mirrors the SIGNAL_DRAWDOWN alert the
        # scheduler fires; honest read, no look-ahead).
        tiers = tier_drawdown(stock_data)
        stand_down = sorted(
            t for t, info in tiers.items() if info.get("status") == "STAND_DOWN"
        )
        if stand_down:
            parts = "; ".join(
                f"<b>{html.escape(t)}</b> "
                f"({tiers[t]['current_drawdown_pct']:.0f}% drawdown, "
                f"{tiers[t]['hit_rate'] * 100:.0f}% hit over {tiers[t]['n']})"
                for t in stand_down
            )
            alert_banner(
                "Drawdown kill-switch — STAND DOWN on: " + parts
                + ". Demote or pause new ideas at this conviction until the "
                "track record recovers.",
                level="critical",
            )
        sc = oos_scorecard(stock_data)
        if sc.get("sufficient"):
            st.caption(f"Significance — {sc['verdict']}")
        st.caption(
            "Frozen at issue, never refit, marked on real closes — no "
            "look-ahead. The honest forward track record."
        )
    except Exception:
        logger.exception("Idea Engine — track record panel failed")


def _render_optimization_mini(ideas: list, stock_data=None) -> None:
    """Run max-Sharpe over the top bullish ideas and surface the weights +
    expected metrics. A miniature of the tab_portfolio Optimization Lab,
    scoped to the ideas the Idea Engine just surfaced."""
    bullish = [
        i for i in ideas
        if getattr(i, "direction", "") == "Bullish"
        and getattr(i, "ticker", "")
    ]
    if len(bullish) < 2:
        return

    section_header(
        "Suggested Portfolio (top bullish ideas)",
        subtitle=(
            "Max-Sharpe weights over the top bullish names from this idea "
            "list, on real cached returns where available (synthetic fallback "
            "when prices are dark)."
        ),
    )

    tickers = [getattr(i, "ticker") for i in bullish[:6]]
    try:
        from engine.portfolio_optimizer import optimize_portfolio
        from processing.book_pnl import returns_panel
        # Prefer REAL returns; synthetic fallback (labeled) when prices dark.
        real_panel = returns_panel(stock_data, tickers)
        if not real_panel.empty:
            returns_df = real_panel
            st.caption("Returns: real cached daily closes (yfinance).")
        else:
            returns_df = _synth_returns_panel(tickers)
            st.caption("Returns: synthetic panel (demo) — live prices unavailable.")
        opt = optimize_portfolio(returns_df, method="max_sharpe", weight_cap=0.40, rf=0.045)
    except Exception as exc:
        logger.exception(f"idea_engine: optimization mini failed: {exc}")
        return

    # Summary metric strip
    metric_card_row(
        [
            {"label": "Method", "value": "Max Sharpe",
             "accent": C_ACCENT, "sublabel": f"{len(tickers)} candidates"},
            {"label": "Expected Return",
             "value": f"{opt.expected_return*100:+5.1f}%",
             "accent": (C_HIGH if opt.expected_return > 0.10 else
                        (C_MOD if opt.expected_return > 0 else C_LOW)),
             "sublabel": "Annualized"},
            {"label": "Expected Vol",
             "value": f"{opt.expected_vol*100:5.1f}%",
             "accent": C_TEXT2,
             "sublabel": "Annualized"},
            {"label": "Sharpe",
             "value": f"{opt.sharpe:5.2f}",
             "accent": (C_HIGH if opt.sharpe > 1.0 else
                        (C_MOD if opt.sharpe > 0.4 else C_LOW)),
             "sublabel": "Net of rf=4.5%"},
        ],
        columns=4,
    )

    # Weights bar chart
    weights_sorted = sorted(opt.weights.items(), key=lambda kv: kv[1], reverse=True)
    fig = go.Figure(go.Bar(
        x=[t for t, _ in weights_sorted],
        y=[w * 100 for _, w in weights_sorted],
        marker_color=[
            C_LOW if w >= 0.40 - 0.005 else (C_HIGH if w >= 0.20 else C_ACCENT)
            for _, w in weights_sorted
        ],
        text=[f"{w*100:.0f}%" for _, w in weights_sorted],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Weight: %{y:.1f}%<extra></extra>",
    ))
    apply_dark_layout(
        fig, title="Optimal Weights (40% cap)",
        height=260, margin=dict(l=12, r=12, t=46, b=30),
        yaxis=dict(title=dict(text="Weight %", font=dict(color=C_TEXT2, size=11))),
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
    """Render the Idea Engine tab — Signal-to-Trade Ideas synthesis."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('idea_engine'):
        try:
            page_header(
                title="Idea Engine",
                subtitle=(
                    "Synthesis of disruption cascade, scenarios, and portfolio "
                    "optimization into a ranked trade-ideas dashboard."
                ),
                badge_text="IDEAS",
                badge_color=C_ACCENT,
            )

            # Pull the active scenario if any (set in the sidebar).
            try:
                from state.scenarios import active_scenario
                scenario = active_scenario()
            except Exception:
                scenario = None

            # ── Build the ideas list ──────────────────────────────────────────
            ideas = _build_ideas(
                port_results, route_results, freight_data, macro_data,
                stock_data, insights,
            )

            # ── Hero ──────────────────────────────────────────────────────────
            _render_hero(ideas[0] if ideas else None, scenario)

            if not ideas:
                st.info("No equity ideas surfaced from the cascade. Try refreshing data.")
                return

            # ── Conviction bars (overview before the detail table) ────────────
            try:
                st.plotly_chart(
                    _build_idea_conviction_bars(ideas),
                    use_container_width=True,
                    config={"displayModeBar": False},
                    key="idea_engine_conviction_bars",
                )
            except Exception:
                logger.exception("Idea Engine — conviction bars failed")

            # ── Ranked table ──────────────────────────────────────────────────
            section_divider("Ranked Table")
            _render_ranked_table(ideas, scenario)

            # ── Rationale (top 5 expanders) ───────────────────────────────────
            section_divider("Rationale")
            _render_rationale(ideas, limit=5)

            # ── Optimization mini ─────────────────────────────────────────────
            section_divider("Portfolio Construction")
            _render_optimization_mini(ideas, stock_data)

            # ── Track record (point-in-time signal ledger; R004) ──────────────
            section_divider("Track Record")
            _render_track_record(stock_data)

            # ── Export this view (PDF) ────────────────────────────────────────
            try:
                from utils.view_export import (
                    ViewSection, ViewSnapshot, ViewTable, render_export_button,
                )
                top = ideas[0]
                top_label = (
                    f"{getattr(top, 'ticker', '?')} — "
                    f"{getattr(top, 'direction', '')} "
                    f"({getattr(top, 'conviction_label', '')})"
                )
                ranked_rows = []
                for rank, idea in enumerate(ideas[:10], start=1):
                    ranked_rows.append([
                        str(rank),
                        getattr(idea, "ticker", "?"),
                        getattr(idea, "direction", ""),
                        getattr(idea, "conviction_label", ""),
                        f"{getattr(idea, 'conviction_score', 0.0):.2f}",
                        (getattr(idea, "thesis", "") or "")[:80],
                    ])
                scenario_note = (
                    f"Active scenario: {scenario.name}" if scenario else "No active scenario"
                )
                snapshot = ViewSnapshot(
                    title="Idea Engine — Trade Ideas",
                    subtitle=scenario_note,
                    headline=f"Top conviction: {top_label}",
                    body=(getattr(top, "thesis", "") or "")[:600],
                    sections=[
                        ViewSection(
                            title="Top 10 Ideas",
                            tables=[ViewTable(
                                title=f"Sorted by conviction ({len(ideas)} total)",
                                headers=["#", "Ticker", "Direction", "Conviction",
                                         "Score", "Thesis (truncated)"],
                                rows=ranked_rows,
                            )],
                        ),
                    ],
                    footer_note=(
                        "Cascade output from processing.disruption_cascade. "
                        "Scenario overlay from state.scenarios."
                    ),
                )
                cols = st.columns([1, 5], gap="small")
                with cols[0]:
                    render_export_button(
                        snapshot, "idea_engine", key="idea_engine_export",
                    )
            except Exception as exc:
                logger.debug(f"tab_idea_engine: PDF export skipped: {exc}")

            # ── Source footer ─────────────────────────────────────────────────
            st.markdown(
                source_footer([
                    DataSource.modeled(
                        "Idea Engine",
                        notes=(
                            "Cascade output from processing.disruption_cascade. "
                            "Scenario overlay from state.scenarios (sidebar-controlled). "
                            "Portfolio weights from engine.portfolio_optimizer over a "
                            "synthetic 2-year return panel (seeded via stable_hash)."
                        ),
                    ),
                ]),
                unsafe_allow_html=True,
            )

        except Exception:
            logger.exception("tab_idea_engine render failed")
            st.error("Idea Engine encountered an error. See logs.")
