"""tab_attribution.py — Performance Attribution Analysis tab.

Decomposes portfolio returns into factor contributions using a
Brinson-Hood-Beebower framework combined with alpha decay analysis.

Sections:
  1. Attribution Hero        — total return decomposed into 6 factors
  2. Factor Attribution Table — contribution, significance, vs history
  3. BHB Attribution          — allocation + selection + interaction by sub-sector
  4. Alpha Decay Chart         — alpha remaining after 1/5/10/20/30 days
  5. Best/Worst Decisions      — top 5 best calls, top 5 worst calls
  6. Attribution over Time     — stacked area, 12 months of factor contributions
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# ── Design constants ─────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_CHART_LAYOUT = dict(
    paper_bgcolor=C_SURFACE,
    plot_bgcolor=C_SURFACE,
    font=dict(family="monospace", color=C_TEXT2, size=11),
    margin=dict(l=48, r=24, t=40, b=40),
)


# ── Synthetic data helpers ────────────────────────────────────────────────────

def _seed() -> int:
    return 42


def _build_factor_contributions() -> Dict[str, float]:
    """Return factor contributions in basis points (sum = total return)."""
    rng = np.random.default_rng(_seed())
    factors = {
        "Freight Market Alpha": float(rng.normal(185, 30)),
        "Macro Factor":         float(rng.normal(-42, 15)),
        "Stock Selection":      float(rng.normal(97, 25)),
        "Sentiment Timing":     float(rng.normal(34, 12)),
        "Sector Allocation":    float(rng.normal(61, 18)),
        "Residual":             float(rng.normal(-18, 8)),
    }
    return factors


def _build_factor_table() -> pd.DataFrame:
    rng = np.random.default_rng(_seed() + 1)
    factors = [
        "Freight Market Alpha",
        "Macro Factor",
        "Stock Selection",
        "Sentiment Timing",
        "Sector Allocation",
        "Residual",
    ]
    data = []
    for f in factors:
        contrib = float(rng.normal(50, 80))
        hist_avg = float(rng.normal(30, 40))
        t_stat = float(rng.normal(1.8, 0.9))
        sig = "HIGH" if abs(t_stat) > 2.0 else ("MOD" if abs(t_stat) > 1.0 else "LOW")
        data.append({
            "Factor": f,
            "Contribution (bps)": round(contrib, 1),
            "t-stat": round(t_stat, 2),
            "Significance": sig,
            "Current": round(contrib, 1),
            "Hist Avg (bps)": round(hist_avg, 1),
            "vs Avg": round(contrib - hist_avg, 1),
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
    decay_curve = [100, 78, 58, 37, 22]
    rng = np.random.default_rng(_seed() + 3)
    noise = rng.normal(0, 2, len(days))
    return pd.DataFrame({
        "Days": days,
        "Alpha Remaining (%)": [max(0, v + n) for v, n in zip(decay_curve, noise)],
    })


def _build_best_worst() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(_seed() + 4)
    best_calls = [
        ("Long ZIM Jan-25",      "Container",  "+312 bps", "Long freight spike"),
        ("Long MATX Feb-25",     "Container",  "+218 bps", "Post-CNY demand surge"),
        ("Long FLNG Mar-25",     "LNG",        "+187 bps", "Winter premium trade"),
        ("Short BDI puts Apr-25","Bulker",     "+143 bps", "Vol compression play"),
        ("Long DSX May-25",      "Bulker",     "+121 bps", "Panamax rate recovery"),
    ]
    worst_calls = [
        ("Long SBLK Jun-24",     "Bulker",     "-198 bps", "Iron ore demand miss"),
        ("Long TK Jul-24",       "Tanker",     "-156 bps", "Geopolitical unwind"),
        ("Long ZIM Aug-24",      "Container",  "-134 bps", "Rate normalization"),
        ("Long HAFN Sep-24",     "Tanker",     "-98 bps",  "Refinery margin squeeze"),
        ("Long NMM Oct-24",      "Container",  "-76 bps",  "Charter rate reversal"),
    ]
    cols = ["Trade", "Sector", "Impact", "Reason"]
    return pd.DataFrame(best_calls, columns=cols), pd.DataFrame(worst_calls, columns=cols)


def _build_monthly_attribution() -> pd.DataFrame:
    rng = np.random.default_rng(_seed() + 5)
    months = pd.date_range("2025-03", periods=12, freq="MS")
    factors = [
        "Freight Market Alpha",
        "Macro Factor",
        "Stock Selection",
        "Sentiment Timing",
        "Sector Allocation",
        "Residual",
    ]
    data = {"Month": months}
    for f in factors:
        data[f] = rng.normal(30, 60, 12).tolist()
    return pd.DataFrame(data)


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_hero(contributions: Dict[str, float]) -> None:
    try:
        total = sum(contributions.values())
        total_color = C_HIGH if total >= 0 else C_LOW
        total_sign  = "+" if total >= 0 else ""

        factor_colors = {
            "Freight Market Alpha": "#3b82f6",
            "Macro Factor":         "#f59e0b",
            "Stock Selection":      "#10b981",
            "Sentiment Timing":     "#8b5cf6",
            "Sector Allocation":    "#06b6d4",
            "Residual":             "#64748b",
        }

        cards_html = ""
        for name, val in contributions.items():
            color = C_HIGH if val >= 0 else C_LOW
            sign  = "+" if val >= 0 else ""
            fc    = factor_colors.get(name, C_ACCENT)
            cards_html += (
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-top:3px solid {fc};border-radius:8px;padding:14px 16px;'
                f'text-align:center;min-width:120px;flex:1;">'
                f'<div style="color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
                f'letter-spacing:1px;margin-bottom:6px;">{name}</div>'
                f'<div style="color:{color};font-size:22px;font-weight:700;'
                f'font-family:monospace;">{sign}{val:.0f}</div>'
                f'<div style="color:{C_TEXT3};font-size:10px;margin-top:2px;">bps</div>'
                f'</div>'
            )

        html = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:10px;padding:20px 24px;margin-bottom:20px;">'
            f'<div style="display:flex;align-items:center;gap:16px;margin-bottom:16px;">'
            f'<div>'
            f'<div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;'
            f'letter-spacing:1.5px;">Total Portfolio Return</div>'
            f'<div style="color:{total_color};font-size:42px;font-weight:800;'
            f'font-family:monospace;line-height:1;">{total_sign}{total:.0f} bps</div>'
            f'<div style="color:{C_TEXT3};font-size:11px;margin-top:4px;">'
            f'Attribution decomposition across 6 factors</div>'
            f'</div>'
            f'</div>'
            f'<div style="display:flex;gap:10px;flex-wrap:wrap;">'
            f'{cards_html}'
            f'</div>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Attribution hero unavailable: {exc}")


def _render_factor_table(df: pd.DataFrame) -> None:
    try:
        def sig_badge(sig: str) -> str:
            color = C_HIGH if sig == "HIGH" else (C_MOD if sig == "MOD" else C_TEXT3)
            return (
                f'<span style="background:{color}22;color:{color};'
                f'padding:2px 8px;border-radius:4px;font-size:10px;'
                f'font-weight:600;">{sig}</span>'
            )

        def val_cell(v: float) -> str:
            color = C_HIGH if v > 0 else (C_LOW if v < 0 else C_TEXT3)
            sign  = "+" if v > 0 else ""
            return f'<span style="color:{color};font-family:monospace;">{sign}{v:.1f}</span>'

        rows_html = ""
        for _, row in df.iterrows():
            rows_html += (
                f'<tr style="border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 12px;color:{C_TEXT};font-size:13px;">'
                f'{row["Factor"]}</td>'
                f'<td style="padding:10px 12px;text-align:right;">'
                f'{val_cell(row["Contribution (bps)"])}</td>'
                f'<td style="padding:10px 12px;text-align:right;'
                f'color:{C_TEXT2};font-family:monospace;">{row["t-stat"]:.2f}</td>'
                f'<td style="padding:10px 12px;text-align:center;">'
                f'{sig_badge(row["Significance"])}</td>'
                f'<td style="padding:10px 12px;text-align:right;">'
                f'{val_cell(row["Current"])}</td>'
                f'<td style="padding:10px 12px;text-align:right;">'
                f'{val_cell(row["Hist Avg (bps)"])}</td>'
                f'<td style="padding:10px 12px;text-align:right;">'
                f'{val_cell(row["vs Avg"])}</td>'
                f'</tr>'
            )

        header_style = (
            f'color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:1px;padding:10px 12px;text-align:right;'
            f'border-bottom:1px solid {C_BORDER};font-weight:600;'
        )
        first_style = (
            f'color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:1px;padding:10px 12px;text-align:left;'
            f'border-bottom:1px solid {C_BORDER};font-weight:600;'
        )

        html = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:10px;overflow:hidden;margin-bottom:20px;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="background:{C_CARD};">'
            f'<th style="{first_style}">Factor</th>'
            f'<th style="{header_style}">Contrib (bps)</th>'
            f'<th style="{header_style}">t-stat</th>'
            f'<th style="{header_style}text-align:center;">Significance</th>'
            f'<th style="{header_style}">Current</th>'
            f'<th style="{header_style}">Hist Avg</th>'
            f'<th style="{header_style}">vs Avg</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Factor table unavailable: {exc}")


def _render_bhb(df: pd.DataFrame) -> None:
    try:
        sector_colors = {
            "Container": "#3b82f6",
            "Bulker":    "#10b981",
            "Tanker":    "#f59e0b",
            "LNG":       "#8b5cf6",
        }

        rows_html = ""
        for _, row in df.iterrows():
            color = sector_colors.get(row["Sub-Sector"], C_ACCENT)

            def cell(v: float) -> str:
                c = C_HIGH if v > 0 else (C_LOW if v < 0 else C_TEXT3)
                s = "+" if v > 0 else ""
                return f'<td style="padding:10px 12px;text-align:right;color:{c};font-family:monospace;">{s}{v:.1f}</td>'

            total_c = C_HIGH if row["Total"] > 0 else (C_LOW if row["Total"] < 0 else C_TEXT3)
            total_s = "+" if row["Total"] > 0 else ""
            rows_html += (
                f'<tr style="border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 12px;">'
                f'<span style="display:inline-block;width:10px;height:10px;'
                f'background:{color};border-radius:2px;margin-right:8px;"></span>'
                f'<span style="color:{C_TEXT};font-size:13px;">{row["Sub-Sector"]}</span>'
                f'</td>'
                f'{cell(row["Allocation Effect"])}'
                f'{cell(row["Selection Effect"])}'
                f'{cell(row["Interaction"])}'
                f'<td style="padding:10px 12px;text-align:right;color:{total_c};'
                f'font-family:monospace;font-weight:700;">{total_s}{row["Total"]:.1f}</td>'
                f'</tr>'
            )

        th = (
            f'color:{C_TEXT3};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:1px;padding:10px 12px;border-bottom:1px solid {C_BORDER};'
            f'font-weight:600;text-align:right;'
        )
        th_first = th.replace("text-align:right;", "text-align:left;")

        html = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
            f'border-radius:10px;overflow:hidden;margin-bottom:20px;">'
            f'<div style="padding:14px 16px;background:{C_CARD};'
            f'border-bottom:1px solid {C_BORDER};">'
            f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">'
            f'Brinson-Hood-Beebower Attribution by Sub-Sector</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;margin-left:10px;">'
            f'All values in basis points</span>'
            f'</div>'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="background:{C_CARD};">'
            f'<th style="{th_first}">Sub-Sector</th>'
            f'<th style="{th}">Allocation</th>'
            f'<th style="{th}">Selection</th>'
            f'<th style="{th}">Interaction</th>'
            f'<th style="{th}">Total</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"BHB table unavailable: {exc}")


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
            marker=dict(size=9, color=C_ACCENT, line=dict(color=C_SURFACE, width=2)),
            fill="tozeroy",
            fillcolor=f"rgba(59,130,246,0.12)",
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

        fig.update_layout(
            **_CHART_LAYOUT,
            title=dict(
                text="Alpha Decay Curve — Optimal Holding Period",
                font=dict(color=C_TEXT, size=13),
                x=0,
            ),
            xaxis=dict(
                title="Holding Period (Days)",
                tickvals=[1, 5, 10, 20, 30],
                gridcolor=C_BORDER,
                showline=False,
                color=C_TEXT2,
            ),
            yaxis=dict(
                title="Alpha Remaining (%)",
                range=[0, 110],
                gridcolor=C_BORDER,
                color=C_TEXT2,
            ),
            showlegend=False,
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        st.warning(f"Alpha decay chart unavailable: {exc}")


def _render_best_worst(best: pd.DataFrame, worst: pd.DataFrame) -> None:
    try:
        col_l, col_r = st.columns(2)

        with col_l:
            rows_html = ""
            for _, row in best.iterrows():
                rows_html += (
                    f'<tr style="border-bottom:1px solid {C_BORDER};">'
                    f'<td style="padding:9px 12px;color:{C_TEXT};font-size:12px;">'
                    f'{row["Trade"]}</td>'
                    f'<td style="padding:9px 12px;color:{C_TEXT3};font-size:11px;">'
                    f'{row["Sector"]}</td>'
                    f'<td style="padding:9px 12px;color:{C_HIGH};font-family:monospace;'
                    f'font-weight:700;font-size:12px;">{row["Impact"]}</td>'
                    f'<td style="padding:9px 12px;color:{C_TEXT3};font-size:11px;">'
                    f'{row["Reason"]}</td>'
                    f'</tr>'
                )
            html = (
                f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
                f'border-top:3px solid {C_HIGH};border-radius:10px;overflow:hidden;">'
                f'<div style="padding:12px 16px;background:{C_CARD};">'
                f'<span style="color:{C_HIGH};font-size:12px;font-weight:700;">'
                f'TOP 5 BEST CALLS</span></div>'
                f'<table style="width:100%;border-collapse:collapse;">'
                f'<tbody>{rows_html}</tbody>'
                f'</table></div>'
            )
            st.markdown(html, unsafe_allow_html=True)

        with col_r:
            rows_html = ""
            for _, row in worst.iterrows():
                rows_html += (
                    f'<tr style="border-bottom:1px solid {C_BORDER};">'
                    f'<td style="padding:9px 12px;color:{C_TEXT};font-size:12px;">'
                    f'{row["Trade"]}</td>'
                    f'<td style="padding:9px 12px;color:{C_TEXT3};font-size:11px;">'
                    f'{row["Sector"]}</td>'
                    f'<td style="padding:9px 12px;color:{C_LOW};font-family:monospace;'
                    f'font-weight:700;font-size:12px;">{row["Impact"]}</td>'
                    f'<td style="padding:9px 12px;color:{C_TEXT3};font-size:11px;">'
                    f'{row["Reason"]}</td>'
                    f'</tr>'
                )
            html = (
                f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};'
                f'border-top:3px solid {C_LOW};border-radius:10px;overflow:hidden;">'
                f'<div style="padding:12px 16px;background:{C_CARD};">'
                f'<span style="color:{C_LOW};font-size:12px;font-weight:700;">'
                f'TOP 5 WORST CALLS</span></div>'
                f'<table style="width:100%;border-collapse:collapse;">'
                f'<tbody>{rows_html}</tbody>'
                f'</table></div>'
            )
            st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Best/worst decisions unavailable: {exc}")


def _render_attribution_over_time(df: pd.DataFrame) -> None:
    try:
        factor_colors = [
            "#3b82f6",  # Freight Market Alpha
            "#f59e0b",  # Macro Factor
            "#10b981",  # Stock Selection
            "#8b5cf6",  # Sentiment Timing
            "#06b6d4",  # Sector Allocation
            "#64748b",  # Residual
        ]
        factors = [c for c in df.columns if c != "Month"]
        months  = df["Month"].dt.strftime("%b %Y").tolist()

        fig = go.Figure()
        for i, factor in enumerate(factors):
            fig.add_trace(go.Scatter(
                x=months,
                y=df[factor].tolist(),
                name=factor,
                mode="lines",
                stackgroup="one",
                line=dict(width=0),
                fillcolor=factor_colors[i % len(factor_colors)]
                          .replace("#", "rgba(").rstrip(")")
                          + ",0.75)",
            ))

        # Fix fillcolor — use rgba properly
        rgba_map = [
            "rgba(59,130,246,0.75)",
            "rgba(245,158,11,0.75)",
            "rgba(16,185,129,0.75)",
            "rgba(139,92,246,0.75)",
            "rgba(6,182,212,0.75)",
            "rgba(100,116,139,0.75)",
        ]
        for i, trace in enumerate(fig.data):
            trace.fillcolor = rgba_map[i % len(rgba_map)]
            trace.line = dict(width=0)

        fig.update_layout(
            **_CHART_LAYOUT,
            title=dict(
                text="Factor Contributions — Rolling 12 Months (bps)",
                font=dict(color=C_TEXT, size=13),
                x=0,
            ),
            xaxis=dict(gridcolor=C_BORDER, color=C_TEXT2, showline=False),
            yaxis=dict(
                title="Contribution (bps)",
                gridcolor=C_BORDER,
                color=C_TEXT2,
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
            height=360,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        st.warning(f"Attribution over time chart unavailable: {exc}")


def _section_header(title: str, subtitle: str = "") -> None:
    sub = (
        f'<span style="color:{C_TEXT3};font-size:11px;margin-left:10px;">'
        f'{subtitle}</span>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:24px 0 10px;padding-bottom:8px;'
        f'border-bottom:1px solid {C_BORDER};">'
        f'<span style="color:{C_TEXT};font-size:14px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:1px;">{title}</span>'
        f'{sub}</div>',
        unsafe_allow_html=True,
    )


# ── Main render ───────────────────────────────────────────────────────────────

def render(stock_data=None, insights=None, freight_data=None):
    """Render the Performance Attribution Analysis tab."""
    try:
        st.markdown(
            f'<div style="padding:4px 0 18px;">'
            f'<span style="color:{C_TEXT};font-size:18px;font-weight:800;'
            f'letter-spacing:0.5px;">Performance Attribution</span>'
            f'<span style="color:{C_TEXT3};font-size:12px;margin-left:12px;">'
            f'Factor decomposition &amp; alpha analysis</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.subheader("Performance Attribution")

    # ── 1. Attribution Hero ───────────────────────────────────────────────────
    try:
        _section_header("1. Attribution Hero", "Total return decomposed into 6 factors")
        contributions = _build_factor_contributions()
        _render_hero(contributions)
    except Exception as exc:
        st.warning(f"Section 1 error: {exc}")

    # ── 2. Factor Attribution Table ───────────────────────────────────────────
    try:
        _section_header(
            "2. Factor Attribution Table",
            "Contribution, significance, current vs historical average",
        )
        factor_df = _build_factor_table()
        _render_factor_table(factor_df)
    except Exception as exc:
        st.warning(f"Section 2 error: {exc}")

    # ── 3. BHB Attribution ────────────────────────────────────────────────────
    try:
        _section_header(
            "3. Brinson-Hood-Beebower Attribution",
            "Allocation + Selection + Interaction by sub-sector",
        )
        bhb_df = _build_bhb_data()
        _render_bhb(bhb_df)
    except Exception as exc:
        st.warning(f"Section 3 error: {exc}")

    # ── 4. Alpha Decay Chart ──────────────────────────────────────────────────
    try:
        _section_header(
            "4. Alpha Decay Chart",
            "Alpha remaining after 1 / 5 / 10 / 20 / 30 days — optimal holding period",
        )
        decay_df = _build_alpha_decay()
        _render_alpha_decay_chart(decay_df)
    except Exception as exc:
        st.warning(f"Section 4 error: {exc}")

    # ── 5. Best / Worst Attribution Decisions ─────────────────────────────────
    try:
        _section_header(
            "5. Best / Worst Attribution Decisions",
            "Top 5 best calls and top 5 worst calls by attribution impact",
        )
        best_df, worst_df = _build_best_worst()
        _render_best_worst(best_df, worst_df)
    except Exception as exc:
        st.warning(f"Section 5 error: {exc}")

    # ── 6. Attribution over Time ──────────────────────────────────────────────
    try:
        _section_header(
            "6. Attribution over Time",
            "Stacked area — factor contributions each month, last 12 months",
        )
        monthly_df = _build_monthly_attribution()
        _render_attribution_over_time(monthly_df)
    except Exception as exc:
        st.warning(f"Section 6 error: {exc}")
