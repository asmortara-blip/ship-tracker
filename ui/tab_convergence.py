"""ui/tab_convergence.py — Convergence & Divergence Lab.

Phase-4 tab. Surfaces the pairwise-correlation convergence/divergence
analysis from processing.convergence_analyzer.

Three sections:
  1. Hero — top converging + top diverging + top decoupling callouts
  2. Ranked table — every classified pair sorted by |Δr|
  3. Current-state heatmap — long-window correlation matrix

Data inputs are normalized into a single ``series_dict`` of named
time series pulled from freight_data (route rates), macro_data
(BDI / WTI / DXY / etc.), and (optionally) stock_data (carrier prices).
"""
from __future__ import annotations

from typing import Any, Optional

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


def _direction_color(direction: str) -> str:
    return {
        "Converging": C_HIGH,
        "Diverging": C_MOD,
        "Decoupling": C_LOW,
        "Stable":     C_TEXT3,
    }.get(direction, C_TEXT2)


# ─── Signal assembly ────────────────────────────────────────────────────────

def _build_series_dict(freight_data, macro_data) -> dict:
    """Assemble a {name → pd.Series} from the platform's feed inputs.

    Routes contribute their ``rate_usd_per_feu`` series; macro contributes any
    series present from a curated whitelist. All series are date-indexed so
    the analyzer can inner-join them.
    """
    series_dict: dict = {}

    # Freight routes
    if isinstance(freight_data, dict):
        for route_id, df in freight_data.items():
            if df is None or getattr(df, "empty", True):
                continue
            if "date" not in df.columns or "rate_usd_per_feu" not in df.columns:
                continue
            try:
                series = (
                    df.dropna(subset=["date", "rate_usd_per_feu"])
                    .sort_values("date")
                    .set_index(pd.DatetimeIndex(df["date"]))["rate_usd_per_feu"]
                    .astype(float)
                )
                series_dict[f"rate:{route_id}"] = series
            except Exception as exc:
                logger.debug(f"convergence: route {route_id} skipped: {exc}")

    # Macro indicators
    if isinstance(macro_data, dict):
        for key in ("BDIY", "BDI", "WCI", "FBX", "SCFI",
                    "DCOILWTICO", "DTWEXBGS", "VIXCLS", "T10Y2Y", "PMI"):
            df = macro_data.get(key)
            if df is None or getattr(df, "empty", True):
                continue
            if "date" not in df.columns or "value" not in df.columns:
                continue
            try:
                series = (
                    df.dropna(subset=["date", "value"])
                    .sort_values("date")
                    .set_index(pd.DatetimeIndex(df["date"]))["value"]
                    .astype(float)
                )
                series_dict[f"macro:{key}"] = series
            except Exception as exc:
                logger.debug(f"convergence: macro {key} skipped: {exc}")

    return series_dict


# ─── Section 1: Hero — top one per direction ────────────────────────────────

def _render_hero(pairs: list) -> None:
    """3-card strip: top Converging, top Diverging, top Decoupling."""
    from processing.convergence_analyzer import (
        find_converging, find_decoupling, find_diverging,
    )

    top_converging = next(iter(find_converging(pairs)), None)
    top_diverging = next(iter(find_diverging(pairs)), None)
    top_decoupling = next(iter(find_decoupling(pairs)), None)

    cards = []
    for label, pair, color in (
        ("Top Converging", top_converging, _direction_color("Converging")),
        ("Top Diverging", top_diverging, _direction_color("Diverging")),
        ("Top Decoupling", top_decoupling, _direction_color("Decoupling")),
    ):
        if pair is None:
            cards.append({
                "label": label,
                "value": "—",
                "accent": C_TEXT3,
                "sublabel": "none surfaced",
            })
            continue
        cards.append({
            "label": label,
            "value": f"{pair.name_a} ↔ {pair.name_b}",
            "accent": color,
            "sublabel": (
                f"short r={pair.short_r:+.2f} · long r={pair.long_r:+.2f} · "
                f"Δ={pair.delta_r:+.2f}"
            ),
        })
    metric_card_row(cards, columns=3)


# ─── Section 2: Ranked table ────────────────────────────────────────────────

def _render_ranked_table(pairs: list) -> None:
    """Top 20 pairs by |Δr| with all four fields visible."""
    if not pairs:
        st.info("No pairs surfaced — need at least 2 series with overlapping history.")
        return

    section_header(
        "Pairs Ranked by |Δr|",
        subtitle=(
            "Short-window r vs long-window r per pair. Sorted by absolute "
            "shift. Converging = magnitude grew; Diverging = shrank; "
            "Decoupling = sign flipped from a meaningful long-window correlation."
        ),
    )

    headers = ["#", "Pair", "Direction", "Short r", "Long r", "Δr", "Interpretation"]
    rows: list[list[str]] = []
    for i, p in enumerate(pairs[:20], start=1):
        rows.append([
            _mono(f"{i}", color=C_TEXT3),
            _sans(f"{p.name_a} ↔ {p.name_b}", color=C_TEXT, weight=600),
            _sans(badge(p.direction, color=_direction_color(p.direction)), color=C_TEXT2),
            _mono(
                f"{p.short_r:+5.2f}",
                color=(C_HIGH if p.short_r > 0 else (C_LOW if p.short_r < 0 else C_TEXT2)),
            ),
            _mono(
                f"{p.long_r:+5.2f}",
                color=(C_HIGH if p.long_r > 0 else (C_LOW if p.long_r < 0 else C_TEXT2)),
            ),
            _mono(
                f"{p.delta_r:+5.2f}",
                color=(C_HIGH if abs(p.delta_r) > 0.4 else (C_MOD if abs(p.delta_r) > 0.2 else C_TEXT2)),
            ),
            _sans(p.interpretation[:120], color=C_TEXT2),
        ])
    wsj_market_table(headers, rows)


# ─── Section 3: Correlation heatmap ─────────────────────────────────────────

def _render_heatmap(series_dict: dict, window: int) -> None:
    """Long-window pairwise correlation matrix as a Plotly heatmap."""
    from processing.convergence_analyzer import compute_correlation_matrix

    if not series_dict or len(series_dict) < 2:
        return

    section_header(
        "Long-Window Correlation Matrix",
        subtitle=f"Pearson r over the trailing {window} days. Diagonal is 1.0 by definition.",
    )

    matrix = compute_correlation_matrix(series_dict, window=window)
    if matrix.empty:
        st.info("Not enough overlapping history to compute correlations.")
        return

    # Order labels for readability — group by namespace ("rate:" then "macro:").
    labels = sorted(matrix.columns, key=lambda n: (n.split(":")[0], n))
    matrix = matrix.loc[labels, labels]

    fig = go.Figure(go.Heatmap(
        z=matrix.fillna(0).to_numpy(),
        x=labels, y=labels,
        zmin=-1.0, zmax=1.0,
        colorscale=[
            [0.0, "#a83232"],       # strong negative — red
            [0.5, "#1a1d23"],       # zero — chart bg
            [1.0, "#3572b0"],       # strong positive — accent blue
        ],
        colorbar=dict(
            title=dict(text="r", font=dict(color=C_TEXT2, size=11)),
            tickfont=dict(color=C_TEXT2, size=10),
        ),
        hovertemplate="<b>%{y}</b> ↔ <b>%{x}</b><br>r = %{z:.2f}<extra></extra>",
        showscale=True,
    ))
    apply_dark_layout(
        fig, height=max(360, 24 * len(labels) + 80),
        margin=dict(l=120, r=80, t=20, b=120),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9, color=C_TEXT2)),
        yaxis=dict(tickfont=dict(size=9, color=C_TEXT2)),
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
    """Render the Convergence & Divergence Lab tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('convergence'):
        try:
            page_header(
                title="Convergence & Divergence Lab",
                subtitle=(
                    "Pairs of routes / indices / commodities whose correlations "
                    "are converging, diverging, or decoupling. Built on rolling "
                    "Pearson r over short vs long windows."
                ),
                badge_text="CONV",
                badge_color=C_ACCENT,
            )

            # ── Controls ──────────────────────────────────────────────────────
            c1, c2, c3 = st.columns(3, gap="medium")
            with c1:
                short_window = st.slider(
                    "Short window (days)", 10, 60, 30, step=5,
                    key="conv_short_window",
                )
            with c2:
                long_window = st.slider(
                    "Long window (days)", 60, 180, 90, step=10,
                    key="conv_long_window",
                )
            with c3:
                min_delta = st.slider(
                    "Min |Δr| threshold", 0.05, 0.50, 0.20, step=0.05,
                    key="conv_min_delta",
                )

            if short_window >= long_window:
                st.warning(
                    f"Short window ({short_window}) must be less than long "
                    f"window ({long_window}). Adjusting…"
                )
                short_window = max(10, long_window - 30)

            # ── Build inputs ──────────────────────────────────────────────────
            series_dict = _build_series_dict(freight_data, macro_data)
            if len(series_dict) < 2:
                st.info(
                    "Need at least 2 series with date+value columns. "
                    "Configure freight feeds or wait for macro data."
                )
                return

            from processing.convergence_analyzer import find_pair_convergence
            pairs = find_pair_convergence(
                series_dict,
                short_window=short_window, long_window=long_window,
                min_delta=min_delta,
            )

            # ── 1. Hero ───────────────────────────────────────────────────────
            section_divider("Top Picks")
            _render_hero(pairs)

            # ── 2. Ranked table ──────────────────────────────────────────────
            section_divider("All Pairs")
            _render_ranked_table(pairs)

            # ── 3. Heatmap ───────────────────────────────────────────────────
            section_divider("Heatmap")
            _render_heatmap(series_dict, window=long_window)

            # ── Export this view (PDF) ────────────────────────────────────────
            try:
                from utils.view_export import (
                    ViewSection, ViewSnapshot, ViewTable, render_export_button,
                )
                from processing.convergence_analyzer import (
                    find_converging, find_decoupling, find_diverging,
                )
                top_conv = next(iter(find_converging(pairs)), None)
                top_div = next(iter(find_diverging(pairs)), None)
                top_dec = next(iter(find_decoupling(pairs)), None)
                headline_parts = []
                if top_conv:
                    headline_parts.append(
                        f"Converging: {top_conv.name_a} ↔ {top_conv.name_b} "
                        f"(Δr {top_conv.delta_r:+.2f})"
                    )
                if top_dec:
                    headline_parts.append(
                        f"Decoupling: {top_dec.name_a} ↔ {top_dec.name_b}"
                    )
                headline = " · ".join(headline_parts) if headline_parts else "No significant convergence shifts"

                table_rows = [
                    [
                        str(i + 1),
                        f"{p.name_a} ↔ {p.name_b}",
                        p.direction,
                        f"{p.short_r:+.2f}",
                        f"{p.long_r:+.2f}",
                        f"{p.delta_r:+.2f}",
                    ]
                    for i, p in enumerate(pairs[:15])
                ]
                snapshot = ViewSnapshot(
                    title="Convergence & Divergence Lab",
                    subtitle=(
                        f"{len(series_dict)} series, {len(pairs)} classifiable pairs · "
                        f"windows {short_window}d / {long_window}d · "
                        f"min |Δr| {min_delta:.2f}"
                    ),
                    headline=headline,
                    sections=[
                        ViewSection(
                            title="Top 15 Pairs by |Δr|",
                            tables=[ViewTable(
                                title="Sorted by absolute change",
                                headers=["#", "Pair", "Direction",
                                         "Short r", "Long r", "Δr"],
                                rows=table_rows,
                            )],
                        ),
                    ],
                    footer_note=(
                        "Rolling-window Pearson correlation from "
                        "processing.convergence_analyzer."
                    ),
                )
                cols = st.columns([1, 5], gap="small")
                with cols[0]:
                    render_export_button(
                        snapshot, "convergence", key="convergence_export",
                    )
            except Exception as exc:
                logger.debug(f"tab_convergence: PDF export skipped: {exc}")

            # ── Source footer ─────────────────────────────────────────────────
            st.markdown(
                source_footer([
                    DataSource.modeled(
                        "Convergence Analyzer",
                        notes=(
                            f"{len(series_dict)} input series; "
                            f"{len(pairs)} classifiable pairs."
                        ),
                    ),
                ]),
                unsafe_allow_html=True,
            )

        except Exception:
            logger.exception("tab_convergence render failed")
            st.error("Convergence Lab encountered an error. See logs.")
