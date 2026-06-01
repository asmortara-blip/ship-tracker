"""tab_attribution.py — Performance Attribution Analysis tab.

Decomposes portfolio returns into factor contributions using a
Brinson-Hood-Beebower framework combined with alpha decay analysis.

Sections:
  1. Attribution Hero         — total return decomposed into 6 factors
  2. Factor Attribution Table — contribution, significance, vs history
  3. BHB Attribution          — allocation + selection + interaction by sub-sector
  4. Alpha Decay Chart        — alpha remaining after 1/5/10/20/30 days
  5. Best/Worst Decisions     — top 5 best calls, top 5 worst calls
  6. Attribution over Time    — stacked area, 12 months of factor contributions

Refactored to the shared WSJ design system (see ``docs/TAB_MIGRATION.md``):
palette constants import from ``ui.styles``; all headers/cards/tables use
shared helpers; every figure and table carries a ``source_footer`` provenance
strip that honestly labels the synthetic data as ``DEMO``.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_SURFACE,
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
    wsj_market_table,
)


# ── Domain-specific color mappings ──────────────────────────────────────────
# Semantic mappings (factor → color, sector → color) stay local to the tab.

_FACTOR_COLORS: dict[str, str] = {
    "Freight Market Alpha": C_ACCENT,
    "Macro Factor":         C_MOD,
    "Stock Selection":      C_HIGH,
    "Sentiment Timing":     C_CONV,
    "Sector Allocation":    C_MACRO,
    "Residual":             C_TEXT3,
}

_FACTOR_FILL_RGBA: dict[str, str] = {
    "Freight Market Alpha": "rgba(53,114,176,0.75)",
    "Macro Factor":         "rgba(201,150,43,0.75)",
    "Stock Selection":      "rgba(46,158,110,0.75)",
    "Sentiment Timing":     "rgba(124,110,175,0.75)",
    "Sector Allocation":    "rgba(74,144,164,0.75)",
    "Residual":             "rgba(107,103,96,0.75)",
}

_SECTOR_COLORS: dict[str, str] = {
    "Container": C_ACCENT,
    "Bulker":    C_HIGH,
    "Tanker":    C_MOD,
    "LNG":       C_CONV,
}

_SIG_BADGE_COLOR: dict[str, str] = {
    "HIGH": C_HIGH,
    "MOD":  C_MOD,
    "LOW":  C_TEXT3,
}


# ── Provenance ──────────────────────────────────────────────────────────────
# Attribution data is fully synthetic; every figure/table is followed by a
# red ``DEMO`` source_footer so users know not to trust the numbers.

def _attribution_source(notes: str = "") -> DataSource:
    return DataSource(
        name="Attribution (synthetic)",
        kind="demo",
        quality="demo",
        notes=notes or "Brinson-Hood-Beebower decomposition on simulated returns.",
    )


def _attribution_footer(notes: str = "") -> None:
    """Render the shared multi-source provenance strip beneath a figure/table."""
    st.markdown(
        source_footer([_attribution_source(notes)]),
        unsafe_allow_html=True,
    )


# ── Cell formatters for WSJ market tables ───────────────────────────────────

def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content for ``wsj_market_table``."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    """Monospace numeric cell content for ``wsj_market_table``."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;font-weight:{weight};">{value}</span>'
    )


def _signed_mono(value: float, *, width: int = 1) -> str:
    """Signed bps value rendered in monospace with green/red coloring."""
    color = C_HIGH if value > 0 else (C_LOW if value < 0 else C_TEXT3)
    sign = "+" if value > 0 else ""
    return _mono(f"{sign}{value:.{width}f}", color=color, weight=600)


def _significance_badge(sig: str) -> str:
    """Map HIGH/MOD/LOW significance string to a colored badge."""
    return badge(sig, color=_SIG_BADGE_COLOR.get(sig, C_TEXT3))


def _sector_dot(name: str) -> str:
    """Small colored dot + sector name for BHB row labels."""
    color = _SECTOR_COLORS.get(name, C_ACCENT)
    return (
        f'<span style="display:inline-block;width:10px;height:10px;'
        f'background:{color};border-radius:2px;margin-right:8px;'
        f'vertical-align:middle;"></span>'
        f'<span style="font-family:var(--sans);color:{C_TEXT};'
        f'font-weight:600;vertical-align:middle;">{name}</span>'
    )


# ── Synthetic data helpers ──────────────────────────────────────────────────
# Every data helper feeds a section labelled ``DEMO`` via ``_attribution_footer``.

def _seed() -> int:
    return 42


def _build_factor_contributions() -> Dict[str, float]:
    """Return factor contributions in basis points (sum = total return)."""
    rng = np.random.default_rng(_seed())
    return {
        "Freight Market Alpha": float(rng.normal(185, 30)),
        "Macro Factor":         float(rng.normal(-42, 15)),
        "Stock Selection":      float(rng.normal(97, 25)),
        "Sentiment Timing":     float(rng.normal(34, 12)),
        "Sector Allocation":    float(rng.normal(61, 18)),
        "Residual":             float(rng.normal(-18, 8)),
    }


def _build_factor_table() -> pd.DataFrame:
    rng = np.random.default_rng(_seed() + 1)
    factors = [
        "Freight Market Alpha", "Macro Factor", "Stock Selection",
        "Sentiment Timing", "Sector Allocation", "Residual",
    ]
    data = []
    for f in factors:
        contrib  = float(rng.normal(50, 80))
        hist_avg = float(rng.normal(30, 40))
        t_stat   = float(rng.normal(1.8, 0.9))
        sig      = "HIGH" if abs(t_stat) > 2.0 else ("MOD" if abs(t_stat) > 1.0 else "LOW")
        data.append({
            "Factor": f,
            "Contribution (bps)": round(contrib, 1),
            "t-stat":             round(t_stat, 2),
            "Significance":       sig,
            "Current":            round(contrib, 1),
            "Hist Avg (bps)":     round(hist_avg, 1),
            "vs Avg":             round(contrib - hist_avg, 1),
        })
    return pd.DataFrame(data)


def _build_bhb_data() -> pd.DataFrame:
    rng = np.random.default_rng(_seed() + 2)
    sectors = ["Container", "Bulker", "Tanker", "LNG"]
    rows = []
    for s in sectors:
        alloc = float(rng.normal(20, 35))
        sel   = float(rng.normal(30, 45))
        inter = float(rng.normal(-5, 10))
        rows.append({
            "Sub-Sector":        s,
            "Allocation Effect": round(alloc, 1),
            "Selection Effect":  round(sel, 1),
            "Interaction":       round(inter, 1),
            "Total":             round(alloc + sel + inter, 1),
        })
    return pd.DataFrame(rows)


def _build_alpha_decay() -> pd.DataFrame:
    days = [1, 5, 10, 20, 30]
    curve = [100, 78, 58, 37, 22]
    rng = np.random.default_rng(_seed() + 3)
    noise = rng.normal(0, 2, len(days))
    return pd.DataFrame({
        "Days":                days,
        "Alpha Remaining (%)": [max(0, v + n) for v, n in zip(curve, noise)],
    })


def _build_best_worst() -> tuple[pd.DataFrame, pd.DataFrame]:
    best = [
        ("Long ZIM Jan-25",       "Container", "+312 bps", "Long freight spike"),
        ("Long MATX Feb-25",      "Container", "+218 bps", "Post-CNY demand surge"),
        ("Long FLNG Mar-25",      "LNG",       "+187 bps", "Winter premium trade"),
        ("Short BDI puts Apr-25", "Bulker",    "+143 bps", "Vol compression play"),
        ("Long DSX May-25",       "Bulker",    "+121 bps", "Panamax rate recovery"),
    ]
    worst = [
        ("Long SBLK Jun-24",  "Bulker",    "-198 bps", "Iron ore demand miss"),
        ("Long TK Jul-24",    "Tanker",    "-156 bps", "Geopolitical unwind"),
        ("Long ZIM Aug-24",   "Container", "-134 bps", "Rate normalization"),
        ("Long HAFN Sep-24",  "Tanker",    "-98 bps",  "Refinery margin squeeze"),
        ("Long NMM Oct-24",   "Container", "-76 bps",  "Charter rate reversal"),
    ]
    cols = ["Trade", "Sector", "Impact", "Reason"]
    return pd.DataFrame(best, columns=cols), pd.DataFrame(worst, columns=cols)


def _build_monthly_attribution() -> pd.DataFrame:
    rng = np.random.default_rng(_seed() + 5)
    months = pd.date_range("2025-03", periods=12, freq="MS")
    factors = [
        "Freight Market Alpha", "Macro Factor", "Stock Selection",
        "Sentiment Timing", "Sector Allocation", "Residual",
    ]
    data: dict = {"Month": months}
    for f in factors:
        data[f] = rng.normal(30, 60, 12).tolist()
    return pd.DataFrame(data)


# ── Section renderers ───────────────────────────────────────────────────────

def _render_hero(contributions: Dict[str, float]) -> None:
    try:
        total = sum(contributions.values())
        total_color = C_HIGH if total >= 0 else C_LOW
        total_sign  = "+" if total >= 0 else ""

        # Total portfolio return card — shared metric card helper, full width
        metric_card_row(
            [{
                "label":       "Total Portfolio Return",
                "value":       f"{total_sign}{total:.0f} bps",
                "accent":      total_color,
                "sublabel":    "Attribution decomposition across 6 factors",
                "delta_color": total_color,
            }],
            columns=1,
        )

        # Factor-level breakdown using shared metric cards
        cards = []
        for name, val in contributions.items():
            accent = _FACTOR_COLORS.get(name, C_ACCENT)
            v_color = C_HIGH if val >= 0 else C_LOW
            sign = "+" if val >= 0 else ""
            cards.append({
                "label":    name,
                "value":    f"{sign}{val:.0f}",
                "accent":   accent,
                "sublabel": "bps",
                "delta_color": v_color,
            })
        metric_card_row(cards, columns=6)
    except Exception:
        logger.exception("Attribution hero render failed")
        st.error("Attribution hero unavailable.")


def _render_factor_table(df: pd.DataFrame) -> None:
    try:
        headers = [
            "Factor", "Contrib (bps)", "t-stat", "Significance",
            "Current", "Hist Avg", "vs Avg",
        ]
        rows = []
        for _, row in df.iterrows():
            rows.append([
                _sans(row["Factor"], color=C_TEXT, weight=600),
                _signed_mono(row["Contribution (bps)"]),
                _mono(f"{row['t-stat']:.2f}", color=C_TEXT2),
                _significance_badge(row["Significance"]),
                _signed_mono(row["Current"]),
                _signed_mono(row["Hist Avg (bps)"]),
                _signed_mono(row["vs Avg"]),
            ])
        wsj_market_table(headers, rows)
    except Exception:
        logger.exception("Factor table render failed")
        st.error("Factor attribution table unavailable.")


def _render_bhb(df: pd.DataFrame) -> None:
    try:
        headers = ["Sub-Sector", "Allocation", "Selection", "Interaction", "Total"]
        rows = []
        for _, row in df.iterrows():
            total = row["Total"]
            total_color = C_HIGH if total > 0 else (C_LOW if total < 0 else C_TEXT3)
            total_sign = "+" if total > 0 else ""
            rows.append([
                _sector_dot(row["Sub-Sector"]),
                _signed_mono(row["Allocation Effect"]),
                _signed_mono(row["Selection Effect"]),
                _signed_mono(row["Interaction"]),
                _mono(f"{total_sign}{total:.1f}", color=total_color, weight=700),
            ])
        wsj_market_table(headers, rows)
    except Exception:
        logger.exception("BHB render failed")
        st.error("BHB attribution table unavailable.")


def _render_alpha_decay_chart(df: pd.DataFrame) -> None:
    try:
        optimal_day = df.loc[
            (df["Alpha Remaining (%)"] - 50).abs().idxmin(), "Days"
        ]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["Days"],
            y=df["Alpha Remaining (%)"],
            mode="lines+markers",
            name="Alpha Remaining",
            line=dict(color=C_ACCENT, width=3),
            marker=dict(size=9, color=C_ACCENT,
                        line=dict(color=C_SURFACE, width=2)),
            fill="tozeroy",
            fillcolor="rgba(53,114,176,0.12)",
        ))
        fig.add_hline(
            y=50,
            line=dict(color=C_MOD, width=1.5, dash="dash"),
            annotation_text="50% Halflife",
            annotation_font_color=C_MOD,
            annotation_position="top right",
        )
        fig.add_vline(
            x=optimal_day,
            line=dict(color=C_HIGH, width=1.5, dash="dot"),
            annotation_text=f"Optimal hold: {optimal_day}d",
            annotation_font_color=C_HIGH,
            annotation_position="top left",
        )
        apply_dark_layout(
            fig,
            title="Alpha Decay Curve - Optimal Holding Period",
            height=320,
            showlegend=False,
            xaxis=dict(
                title="Holding Period (Days)",
                tickvals=[1, 5, 10, 20, 30],
            ),
            yaxis=dict(
                title="Alpha Remaining (%)",
                range=[0, 110],
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="attribution_alpha_decay")
    except Exception:
        logger.exception("Alpha decay chart render failed")
        st.error("Alpha decay chart unavailable.")


def _render_best_worst(best: pd.DataFrame, worst: pd.DataFrame) -> None:
    try:
        col_l, col_r = st.columns(2)

        headers = ["Trade", "Sector", "Impact", "Reason"]

        with col_l:
            section_header(
                "Top 5 Best Calls",
                "Largest positive attribution impact",
            )
            best_rows = [
                [
                    _sans(row["Trade"], color=C_TEXT, weight=600),
                    _sans(row["Sector"], color=C_TEXT3, weight=500),
                    _mono(row["Impact"], color=C_HIGH, weight=700),
                    _sans(row["Reason"], color=C_TEXT3, weight=400),
                ]
                for _, row in best.iterrows()
            ]
            wsj_market_table(headers, best_rows)

        with col_r:
            section_header(
                "Top 5 Worst Calls",
                "Largest drag on attribution impact",
            )
            worst_rows = [
                [
                    _sans(row["Trade"], color=C_TEXT, weight=600),
                    _sans(row["Sector"], color=C_TEXT3, weight=500),
                    _mono(row["Impact"], color=C_LOW, weight=700),
                    _sans(row["Reason"], color=C_TEXT3, weight=400),
                ]
                for _, row in worst.iterrows()
            ]
            wsj_market_table(headers, worst_rows)
    except Exception:
        logger.exception("Best/worst render failed")
        st.error("Best/worst decisions unavailable.")


def _render_attribution_over_time(df: pd.DataFrame) -> None:
    try:
        factors = [c for c in df.columns if c != "Month"]
        months  = df["Month"].dt.strftime("%b %Y").tolist()

        fig = go.Figure()
        for factor in factors:
            fig.add_trace(go.Scatter(
                x=months,
                y=df[factor].tolist(),
                name=factor,
                mode="lines",
                stackgroup="one",
                line=dict(width=0),
                fillcolor=_FACTOR_FILL_RGBA.get(factor, "rgba(107,103,96,0.75)"),
            ))

        apply_dark_layout(
            fig,
            title="Factor Contributions - Rolling 12 Months (bps)",
            height=380,
            showlegend=True,
            xaxis=dict(showline=False),
            yaxis=dict(
                title="Contribution (bps)",
                zeroline=True,
                zerolinecolor=C_BORDER,
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="left",
                x=0,
                font=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="attribution_over_time")
    except Exception:
        logger.exception("Attribution over time render failed")
        st.error("Attribution over time chart unavailable.")


# ── Main render ─────────────────────────────────────────────────────────────

def render(stock_data=None, insights=None, freight_data=None, *args, **kwargs) -> None:
    """Render the Performance Attribution Analysis tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('attribution'):

        page_header(
            title="Performance Attribution",
            subtitle="Brinson-Hood-Beebower factor decomposition and alpha decay analysis",
            badge_text="ATTRIBUTION",
            badge_color=C_ACCENT,
        )

        # ── Return decomposition ────────────────────────────────────────────────
        try:
            section_header(
                "Attribution Hero",
                subtitle="Total portfolio return decomposed into six factor contributions",
            )
            contributions = _build_factor_contributions()
            _render_hero(contributions)
            _attribution_footer("Total portfolio return and per-factor contribution (bps).")
        except Exception:
            logger.exception("Attribution hero section failed")
            st.warning("Attribution hero unavailable.")

        try:
            section_header(
                "Factor Attribution Table",
                subtitle="Contribution, significance, and current vs historical average",
            )
            factor_df = _build_factor_table()
            _render_factor_table(factor_df)
            _attribution_footer("Per-factor contribution with t-stat significance.")
        except Exception:
            logger.exception("Attribution factor-table section failed")
            st.warning("Factor attribution table unavailable.")

        try:
            section_header(
                "Brinson-Hood-Beebower Attribution",
                subtitle="Allocation, selection, and interaction effects by sub-sector (bps)",
            )
            bhb_df = _build_bhb_data()
            _render_bhb(bhb_df)
            _attribution_footer("Allocation / selection / interaction by sub-sector.")
        except Exception:
            logger.exception("Attribution BHB section failed")
            st.warning("BHB attribution table unavailable.")

        section_divider("Alpha Decay")

        # ── Alpha decay ─────────────────────────────────────────────────────────
        try:
            section_header(
                "Alpha Decay Curve",
                subtitle="Alpha remaining after 1 / 5 / 10 / 20 / 30 days — optimal holding period",
            )
            decay_df = _build_alpha_decay()
            _render_alpha_decay_chart(decay_df)
            _attribution_footer("Alpha-remaining curve over 1-30 day holding periods.")
        except Exception:
            logger.exception("Attribution alpha-decay section failed")
            st.warning("Alpha decay chart unavailable.")

        section_divider("Decision Quality")

        # ── Decision quality ────────────────────────────────────────────────────
        try:
            section_header(
                "Best / Worst Attribution Decisions",
                subtitle="The five highest and five lowest trades by attribution impact",
            )
            best_df, worst_df = _build_best_worst()
            _render_best_worst(best_df, worst_df)
            _attribution_footer("Top and bottom 5 trades by attribution impact.")
        except Exception:
            logger.exception("Attribution best/worst section failed")
            st.warning("Best/worst decisions unavailable.")

        st.divider()

        try:
            section_header(
                "Attribution Over Time",
                subtitle="Stacked factor contributions per month across the trailing 12 months",
            )
            monthly_df = _build_monthly_attribution()
            _render_attribution_over_time(monthly_df)
            _attribution_footer("Rolling 12-month factor contributions (bps).")
        except Exception:
            logger.exception("Attribution over-time section failed")
            st.warning("Attribution over time chart unavailable.")
