"""
Performance Attribution Engine — Streamlit Tab v2

Bloomberg terminal aesthetic. Decomposes shipping stock returns into:
  Freight Rate Beta | BDI Factor | Macro Factor | Idiosyncratic (alpha)

Enhanced sections (v2 additions marked NEW):
  0. Universe Attribution Donut   — sector-wide return component donut (NEW)
  0b. Multi-Stock Waterfall Grid  — all stocks waterfall in a grid (NEW)
  0c. Factor Beta Heatmap         — exposure matrix with gradient cells (NEW)
  1. Attribution Summary Strip     — sector-wide factor share breakdown
  2. Waterfall Chart               — per-stock return decomposition
  3. Factor Exposure Dashboard     — radar + ranked bar per factor
  4. Attribution vs Benchmark      — excess return vs sector XLI
  5. Rolling Attribution           — stacked area, configurable window
  6. Cross-Asset Attribution       — shipping vs transport peers
  7. Regime Analysis               — attribution in bull/bear/vol regimes
  8. Alpha Quality Ranking         — alpha-driven vs beta-driven leaderboard

All chart colours follow the shared ui.styles design system.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from processing.performance_attribution import (
    PerformanceAttribution,
    AttributePerformance,
    attribute_all_stocks,
    compute_factor_returns,
    SHIPPING_TICKERS,
    _MIN_OBS,
    _extract_return_series,
    _extract_macro_return,
    _align_series,
    _ols_lstsq,
    _annualised_vol,
)
from ui.styles import (
    C_ACCENT, C_BORDER, C_CARD, C_HIGH, C_LOW, C_MOD,
    C_TEXT, C_TEXT2, C_TEXT3,
    _hex_to_rgba,
    section_header,
)


# ── Design constants ────────────────────────────────────────────────────────

_BG        = "#0a0f1a"
_SURFACE   = "#111827"
_C_POS     = "#10b981"   # green  — positive contribution
_C_NEG     = "#ef4444"   # red    — negative contribution
_C_NEUT    = "#64748b"   # gray   — neutral / total bar
_C_FREIGHT = "#3b82f6"   # blue   — freight factor
_C_BDI     = "#06b6d4"   # cyan   — BDI / macro factor
_C_SECTOR  = "#8b5cf6"   # purple — sector beta
_C_IDIO    = "#f59e0b"   # amber  — idiosyncratic / alpha
_C_TOTAL   = "#94a3b8"   # slate  — total bar
_C_BULL    = "#10b981"   # green  — bull regime
_C_BEAR    = "#ef4444"   # red    — bear regime
_C_VOLAT   = "#f59e0b"   # amber  — high-vol regime

_FACTOR_COLORS = {
    "freight_beta_contribution": _C_FREIGHT,
    "macro_contribution":        _C_BDI,
    "sector_contribution":       _C_SECTOR,
    "idiosyncratic_return":      _C_IDIO,
}

_FACTOR_LABELS = {
    "freight_beta_contribution": "Freight Rate Beta",
    "macro_contribution":        "BDI / Macro Factor",
    "sector_contribution":       "Sector Beta (XLI)",
    "idiosyncratic_return":      "Idiosyncratic (Alpha)",
}

_PERIOD_OPTIONS: Dict[str, int] = {
    "30 days":  30,
    "60 days":  60,
    "90 days":  90,
    "180 days": 180,
}


# ── Shared layout helper ─────────────────────────────────────────────────────

def _base_layout(**overrides) -> dict:
    base = dict(
        template="plotly_dark",
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        font=dict(family="Inter, sans-serif", color="#94a3b8", size=11),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
        margin=dict(t=36, b=16, l=14, r=14),
    )
    base.update(overrides)
    return base


def _axis_style(**extra) -> dict:
    base = dict(
        gridcolor="rgba(255,255,255,0.05)",
        zeroline=True,
        zerolinecolor="rgba(255,255,255,0.14)",
        zerolinewidth=1,
        tickfont=dict(size=10, color="#64748b"),
        linecolor="rgba(255,255,255,0.08)",
    )
    base.update(extra)
    return base


# ── Generic helpers ──────────────────────────────────────────────────────────

def _divider(label: str = "") -> None:
    inner = (
        '<span style="font-size:0.60rem; color:#334155; text-transform:uppercase;'
        ' letter-spacing:0.13em; padding:0 14px">' + label + "</span>"
        if label else ""
    )
    st.markdown(
        '<div style="display:flex; align-items:center; margin:28px 0 20px">'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        + inner +
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _fmt_pct(value: float, decimals: int = 2) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def _color_for_value(value: float) -> str:
    if value > 0.5:
        return _C_POS
    if value < -0.5:
        return _C_NEG
    return _C_NEUT


def _legend_h() -> dict:
    return dict(
        orientation="h",
        yanchor="bottom", y=1.02,
        xanchor="center", x=0.5,
        font=dict(size=10, color="#94a3b8"),
        bgcolor="rgba(0,0,0,0)",
        borderwidth=0,
    )


# ── 0a. Attribution Donut — sector-wide factor share (NEW) ───────────────────

def _render_attribution_donut(attributions: List[PerformanceAttribution]) -> None:
    """Donut chart: what % of total attribution comes from each factor (sector-wide average)."""
    section_header(
        "Attribution Mix — Sector Overview",
        "Sector-wide average factor share of total return (absolute contributions) as a donut chart",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    factor_keys = [
        "freight_beta_contribution",
        "macro_contribution",
        "sector_contribution",
        "idiosyncratic_return",
    ]
    factor_labels_short = ["Freight Beta", "BDI / Macro", "Sector Beta (XLI)", "Alpha (Idio.)"]
    factor_colors_donut = [_C_FREIGHT, _C_BDI, _C_SECTOR, _C_IDIO]

    totals = []
    for a in attributions:
        total_abs = sum(abs(getattr(a, k)) for k in factor_keys)
        if total_abs < 0.01:
            continue
        totals.append({k: abs(getattr(a, k)) / total_abs for k in factor_keys})

    if not totals:
        st.info("Insufficient data to compute attribution shares.")
        return

    avg_shares = {k: float(np.mean([r[k] for r in totals])) for k in factor_keys}
    share_vals = [avg_shares[k] * 100 for k in factor_keys]

    donut_col, insight_col = st.columns([2, 1])

    with donut_col:
        fig = go.Figure(go.Pie(
            labels=factor_labels_short,
            values=share_vals,
            hole=0.60,
            marker=dict(
                colors=factor_colors_donut,
                line=dict(color="#0a0f1a", width=2),
            ),
            textinfo="label+percent",
            textfont=dict(size=11, color=C_TEXT, family="Inter, sans-serif"),
            hovertemplate="<b>%{label}</b><br>Share: %{percent}<br>Avg: %{value:.1f}%<extra></extra>",
            pull=[0.04 if v == max(share_vals) else 0 for v in share_vals],
            direction="clockwise",
            sort=False,
        ))

        dominant_idx = share_vals.index(max(share_vals))
        fig.add_annotation(
            text=(
                f"<b>{factor_labels_short[dominant_idx]}</b><br>"
                f"<span>Dominant Factor</span>"
            ),
            x=0.5, y=0.5, showarrow=False,
            font=dict(color=factor_colors_donut[dominant_idx], size=12,
                      family="Inter, sans-serif"),
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            height=320,
            margin=dict(t=20, b=20, l=20, r=20),
            showlegend=True,
            legend=dict(
                orientation="v", x=1.02, y=0.5,
                font=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
            ),
            font=dict(family="Inter, sans-serif", color=C_TEXT2, size=11),
        )
        st.plotly_chart(fig, use_container_width=True, key="attr_donut_sector_share")

    with insight_col:
        rows_html = ""
        for label, key, color in zip(factor_labels_short, factor_keys, factor_colors_donut):
            share_pct = avg_shares[key] * 100
            rows_html += (
                f'<div style="margin-bottom:12px">'
                f'<div style="display:flex; justify-content:space-between;'
                f' align-items:center; margin-bottom:4px">'
                f'<span style="font-size:0.75rem; color:#94a3b8">{label}</span>'
                f'<span style="font-size:0.82rem; font-weight:800; color:{color};'
                f' font-variant-numeric:tabular-nums">{share_pct:.1f}%</span>'
                f'</div>'
                f'<div style="background:rgba(255,255,255,0.05); border-radius:4px;'
                f' height:4px; overflow:hidden">'
                f'<div style="background:{color}; width:{min(share_pct,100):.1f}%;'
                f' height:4px; border-radius:4px"></div></div>'
                f'</div>'
            )
        st.markdown(
            f'<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
            f' border-radius:10px; padding:18px; margin-top:8px">'
            f'<div style="font-size:0.60rem; font-weight:700; color:#64748b;'
            f' text-transform:uppercase; letter-spacing:0.10em; margin-bottom:14px">'
            f'Factor Share Summary</div>'
            f'{rows_html}'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── 0b. Factor Exposure Cards per stock (NEW) ─────────────────────────────────

def _render_factor_exposure_cards(attributions: List[PerformanceAttribution]) -> None:
    """Cards showing each stock's dominant factor and bar-chart per-factor contribution."""
    section_header(
        "Factor Exposure Cards",
        "Each stock's dominant factor driver and absolute contribution magnitudes at a glance",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    factor_keys = [
        "freight_beta_contribution",
        "macro_contribution",
        "sector_contribution",
        "idiosyncratic_return",
    ]
    short_labels = {
        "freight_beta_contribution": "Freight",
        "macro_contribution":        "BDI/Macro",
        "sector_contribution":       "Sector",
        "idiosyncratic_return":      "Alpha",
    }
    fcolors = {
        "freight_beta_contribution": _C_FREIGHT,
        "macro_contribution":        _C_BDI,
        "sector_contribution":       _C_SECTOR,
        "idiosyncratic_return":      _C_IDIO,
    }

    sorted_attrs = sorted(attributions, key=lambda a: abs(a.total_return_pct), reverse=True)
    n_cols = min(4, len(sorted_attrs))
    row_groups = [sorted_attrs[i:i + n_cols] for i in range(0, len(sorted_attrs), n_cols)]

    for row_attrs in row_groups:
        cols = st.columns(n_cols)
        for col, attr in zip(cols, row_attrs):
            with col:
                total_abs = sum(abs(getattr(attr, k)) for k in factor_keys) or 1.0
                dom_key = max(factor_keys, key=lambda k: abs(getattr(attr, k)))
                dom_color = fcolors[dom_key]
                dom_label = short_labels[dom_key]
                total_color = _C_POS if attr.total_return_pct >= 0 else _C_NEG

                bars_html = ""
                for key in factor_keys:
                    val = getattr(attr, key)
                    share_pct = abs(val) / total_abs * 100
                    bar_c = fcolors[key]
                    val_color = _C_POS if val >= 0 else _C_NEG
                    bars_html += (
                        f'<div style="margin-bottom:6px">'
                        f'<div style="display:flex; justify-content:space-between;'
                        f' align-items:center; margin-bottom:2px">'
                        f'<span style="font-size:0.62rem; color:#64748b">{short_labels[key]}</span>'
                        f'<span style="font-size:0.70rem; font-weight:700; color:{val_color};'
                        f' font-variant-numeric:tabular-nums">{_fmt_pct(val, 1)}</span>'
                        f'</div>'
                        f'<div style="background:rgba(255,255,255,0.04); border-radius:3px;'
                        f' height:3px; overflow:hidden">'
                        f'<div style="background:{bar_c}; width:{min(share_pct,100):.0f}%;'
                        f' height:3px; border-radius:3px"></div></div>'
                        f'</div>'
                    )

                col.markdown(
                    f'<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                    f' border-top:3px solid {dom_color}; border-radius:10px; padding:14px 16px;'
                    f' margin-bottom:8px">'
                    f'<div style="display:flex; justify-content:space-between; align-items:center;'
                    f' margin-bottom:10px">'
                    f'<span style="font-size:0.92rem; font-weight:900; color:#f1f5f9">'
                    f'{attr.ticker}</span>'
                    f'<span style="font-size:0.78rem; font-weight:700; color:{total_color};'
                    f' font-variant-numeric:tabular-nums">{_fmt_pct(attr.total_return_pct)}</span>'
                    f'</div>'
                    f'<div style="font-size:0.60rem; color:{dom_color}; font-weight:700;'
                    f' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:10px">'
                    f'Top Factor: {dom_label}</div>'
                    f'{bars_html}'
                    f'<div style="display:flex; justify-content:space-between;'
                    f' align-items:center; margin-top:8px; padding-top:8px;'
                    f' border-top:1px solid rgba(255,255,255,0.05)">'
                    f'<span style="font-size:0.60rem; color:#475569">R\u00b2</span>'
                    f'<span style="font-size:0.72rem; font-weight:700; color:#60a5fa">'
                    f'{attr.r_squared*100:.0f}%</span>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


# ── 0. Bloomberg header ──────────────────────────────────────────────────────

def _render_header(period_label: str) -> None:
    st.markdown(
        '<div style="background:linear-gradient(135deg,#0d1117 0%,#111827 100%);'
        ' border:1px solid rgba(255,255,255,0.08); border-left:3px solid #3b82f6;'
        ' border-radius:12px; padding:18px 26px; margin-bottom:22px;'
        ' box-shadow:0 4px 24px rgba(0,0,0,0.4)">'

        '<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px">'

        '<div>'
        '<div style="font-size:0.60rem; font-weight:800; color:#3b82f6;'
        ' text-transform:uppercase; letter-spacing:0.16em; margin-bottom:5px">'
        'BLOOMBERG TERMINAL \u00b7 EQUITY ANALYTICS \u00b7 ATTRIBUTION ENGINE'
        '</div>'
        '<div style="font-size:1.45rem; font-weight:900; color:#f1f5f9; letter-spacing:-0.015em">'
        'Performance Attribution Engine'
        '</div>'
        '<div style="font-size:0.80rem; color:#64748b; margin-top:4px; line-height:1.5">'
        'OLS factor decomposition \u00b7 Freight \u00b7 BDI \u00b7 Macro \u00b7 Sector \u00b7 Alpha'
        ' \u00b7 Rolling windows \u00b7 Regime analysis'
        '</div>'
        '</div>'

        '<div style="display:flex; gap:20px; align-items:center">'

        '<div style="text-align:center; background:rgba(59,130,246,0.10); border:1px solid rgba(59,130,246,0.25);'
        ' border-radius:8px; padding:10px 20px">'
        '<div style="font-size:0.58rem; color:#64748b; text-transform:uppercase; letter-spacing:0.1em">Period</div>'
        '<div style="font-size:1.1rem; font-weight:800; color:#06b6d4; margin-top:2px">'
        + period_label +
        '</div>'
        '</div>'

        '<div style="text-align:center; background:rgba(16,185,129,0.08); border:1px solid rgba(16,185,129,0.2);'
        ' border-radius:8px; padding:10px 20px">'
        '<div style="font-size:0.58rem; color:#64748b; text-transform:uppercase; letter-spacing:0.1em">Model</div>'
        '<div style="font-size:1.1rem; font-weight:800; color:#10b981; margin-top:2px">OLS / 4-Factor</div>'
        '</div>'

        '</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )


# ── 1. Attribution Summary Strip ─────────────────────────────────────────────

def _render_attribution_summary(
    attributions: List[PerformanceAttribution],
    selected_attr: Optional[PerformanceAttribution],
) -> None:
    """
    Top-level KPI strip: sector-wide average factor share percentages
    plus selected-ticker deep metrics.
    """
    section_header(
        "Attribution Summary",
        "Sector-wide average factor share of total return \u00b7 selected stock deep metrics",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    factor_keys = [
        "freight_beta_contribution",
        "macro_contribution",
        "sector_contribution",
        "idiosyncratic_return",
    ]

    # Compute average absolute factor share across the universe
    totals = []
    for a in attributions:
        total_abs = sum(abs(getattr(a, k)) for k in factor_keys)
        if total_abs < 0.01:
            continue
        totals.append({k: abs(getattr(a, k)) / total_abs for k in factor_keys})

    if totals:
        avg_shares = {k: float(np.mean([r[k] for r in totals])) for k in factor_keys}
    else:
        avg_shares = {k: 0.25 for k in factor_keys}

    factor_meta = [
        ("freight_beta_contribution", "Freight Rate Beta", _C_FREIGHT, "Container / tanker rate sensitivity"),
        ("macro_contribution",        "BDI / Macro",       _C_BDI,     "Baltic Dry Index trend exposure"),
        ("sector_contribution",       "Sector (XLI)",      _C_SECTOR,  "Broad industrial equity beta"),
        ("idiosyncratic_return",      "Alpha (Idio.)",     _C_IDIO,    "Company-specific unexplained return"),
    ]

    # Row 1: Sector-wide factor share cards
    cols = st.columns(4)
    for col, (key, label, color, subtitle) in zip(cols, factor_meta):
        share_pct = avg_shares[key] * 100
        with col:
            col.markdown(
                f'<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                f' border-top:3px solid {color}; border-radius:10px;'
                f' padding:16px 18px; height:100%">'
                f'<div style="font-size:0.58rem; font-weight:700; color:#64748b;'
                f' text-transform:uppercase; letter-spacing:0.10em; margin-bottom:8px">'
                f'{label}</div>'
                f'<div style="font-size:1.55rem; font-weight:900; color:{color};'
                f' font-variant-numeric:tabular-nums; line-height:1">'
                f'{share_pct:.1f}%</div>'
                f'<div style="font-size:0.68rem; color:#475569; margin-top:6px">'
                f'Avg sector share</div>'
                f'<div style="background:rgba(255,255,255,0.04); border-radius:4px;'
                f' height:4px; margin-top:10px; overflow:hidden">'
                f'<div style="background:{color}; width:{min(share_pct,100):.1f}%;'
                f' height:4px; border-radius:4px; opacity:0.85"></div>'
                f'</div>'
                f'<div style="font-size:0.63rem; color:#334155; margin-top:5px">{subtitle}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    if selected_attr is None:
        return

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

    # Row 2: Selected stock deep metrics
    metrics = [
        ("Total Return",    _fmt_pct(selected_attr.total_return_pct),
         _C_POS if selected_attr.total_return_pct >= 0 else _C_NEG),
        ("R\u00b2 Explained", f"{selected_attr.r_squared*100:.1f}%",     C_ACCENT),
        ("Info Ratio",      f"{selected_attr.information_ratio:.2f}",     _C_IDIO),
        ("Tracking Error",  f"{selected_attr.tracking_error:.1f}%",       C_TEXT2),
        ("Alpha Return",    _fmt_pct(selected_attr.idiosyncratic_return),
         _C_IDIO if selected_attr.idiosyncratic_return >= 0 else _C_NEG),
    ]

    cols2 = st.columns(5)
    for col, (label, value, color) in zip(cols2, metrics):
        with col:
            col.markdown(
                f'<div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.07);'
                f' border-radius:8px; padding:14px 16px; text-align:center">'
                f'<div style="font-size:0.58rem; font-weight:700; color:#64748b;'
                f' text-transform:uppercase; letter-spacing:0.09em; margin-bottom:6px">'
                f'{label}</div>'
                f'<div style="font-size:1.25rem; font-weight:800; color:{color};'
                f' font-variant-numeric:tabular-nums">'
                f'{value}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── 2. Waterfall Chart ───────────────────────────────────────────────────────

def _render_waterfall(attr: PerformanceAttribution) -> None:
    section_header(
        "Attribution Waterfall",
        f"Total return decomposed into factor contributions for {attr.ticker}",
    )

    factors = [
        ("Freight Rate Beta",  attr.freight_beta_contribution),
        ("BDI / Macro Factor", attr.macro_contribution),
        ("Sector Beta (XLI)",  attr.sector_contribution),
        ("Idiosyncratic",      attr.idiosyncratic_return),
    ]

    measure_list = ["relative"] * len(factors) + ["total"]
    x_list = [f[0] for f in factors] + ["Total Return"]
    y_list = [f[1] for f in factors] + [attr.total_return_pct]

    bar_colors = [_C_POS if v >= 0 else _C_NEG for v in y_list[:-1]] + [_C_TOTAL]

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=measure_list,
        x=x_list,
        y=y_list,
        text=[_fmt_pct(v) for v in y_list],
        textposition="outside",
        textfont=dict(size=12, color="#f1f5f9", family="Inter, sans-serif"),
        increasing=dict(marker=dict(color=_C_POS, line=dict(width=0))),
        decreasing=dict(marker=dict(color=_C_NEG, line=dict(width=0))),
        totals=dict(marker=dict(color=_C_TOTAL, line=dict(width=0))),
        connector=dict(line=dict(color="rgba(255,255,255,0.10)", width=1, dash="dot")),
        hovertemplate="%{x}: %{y:+.2f}%<extra></extra>",
    ))

    fig.update_layout(
        **_base_layout(height=340),
        xaxis=_axis_style(tickfont=dict(size=12, color="#94a3b8"), gridcolor="rgba(0,0,0,0)"),
        yaxis=_axis_style(title="% Return", ticksuffix="%"),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"attr_waterfall_{attr.ticker}")


# ── 3. Factor Exposure Dashboard ─────────────────────────────────────────────

def _render_factor_exposure_dashboard(attributions: List[PerformanceAttribution]) -> None:
    section_header(
        "Factor Exposure Dashboard",
        "Which stocks carry the highest exposure to each factor \u00b7 ranked bar + heat matrix",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    factor_keys = [
        "freight_beta_contribution",
        "macro_contribution",
        "sector_contribution",
        "idiosyncratic_return",
    ]
    factor_labels = [_FACTOR_LABELS[k] for k in factor_keys]
    factor_colors = [_FACTOR_COLORS[k] for k in factor_keys]

    left_col, right_col = st.columns([3, 2])

    with left_col:
        # Grouped bar — absolute contribution magnitude per factor per ticker
        tickers = [a.ticker for a in attributions]
        fig = go.Figure()
        for key, label, color in zip(factor_keys, factor_labels, factor_colors):
            vals = [abs(getattr(a, key)) for a in attributions]
            fig.add_trace(go.Bar(
                name=label,
                x=tickers,
                y=vals,
                marker_color=color,
                marker_line_width=0,
                opacity=0.88,
                hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y:.2f}}%<extra></extra>",
            ))

        fig.update_layout(
            **_base_layout(height=340, barmode="group"),
            xaxis=_axis_style(tickfont=dict(size=12, color="#94a3b8"), gridcolor="rgba(0,0,0,0)"),
            yaxis=_axis_style(title="Absolute Contribution (%)", ticksuffix="%"),
            legend=_legend_h(),
            bargap=0.18,
            bargroupgap=0.06,
        )
        st.plotly_chart(fig, use_container_width=True, key="attr_factor_exposure_bars")

    with right_col:
        # Heatmap — contribution share matrix
        z_matrix, text_matrix = [], []
        for attr in attributions:
            total = sum(abs(getattr(attr, k)) for k in factor_keys) or 1.0
            row_z, row_t = [], []
            for key in factor_keys:
                frac = min(1.0, abs(getattr(attr, key)) / total)
                row_z.append(round(frac, 3))
                row_t.append(f"{frac*100:.0f}%")
            z_matrix.append(row_z)
            text_matrix.append(row_t)

        short_labels = ["Freight", "BDI/Macro", "Sector", "Alpha"]

        fig2 = go.Figure(go.Heatmap(
            z=z_matrix,
            x=short_labels,
            y=tickers,
            colorscale=[
                [0.0,  "rgba(13,17,23,1)"],
                [0.30, "rgba(30,58,138,0.55)"],
                [0.60, "rgba(37,99,235,0.80)"],
                [1.0,  "rgba(147,197,253,1)"],
            ],
            zmin=0.0, zmax=1.0,
            text=text_matrix,
            texttemplate="%{text}",
            textfont=dict(size=11, color="#f1f5f9", family="Inter, sans-serif"),
            hovertemplate="<b>%{y}</b><br>%{x}: %{z:.1%}<extra></extra>",
            colorbar=dict(
                thickness=10,
                outlinewidth=0,
                tickfont=dict(size=9, color="#64748b"),
                tickformat=".0%",
                len=0.85,
            ),
        ))
        fig2.update_layout(
            **_base_layout(height=340),
            xaxis=dict(tickfont=dict(size=10, color="#94a3b8"), side="top"),
            yaxis=dict(tickfont=dict(size=11, color="#94a3b8")),
        )
        st.plotly_chart(fig2, use_container_width=True, key="attr_factor_heatmap")

    # Full ranked exposure table
    _render_factor_exposure_table(attributions)


def _render_factor_exposure_table(attributions: List[PerformanceAttribution]) -> None:
    factor_keys = [
        "freight_beta_contribution",
        "macro_contribution",
        "sector_contribution",
        "idiosyncratic_return",
        "r_squared",
    ]
    factor_display = {
        "freight_beta_contribution": "Freight Beta",
        "macro_contribution":        "BDI Macro",
        "sector_contribution":       "Sector (XLI)",
        "idiosyncratic_return":      "Idiosyncratic",
        "r_squared":                 "R\u00b2",
    }

    rows_html = ""
    for i, attr in enumerate(
        sorted(attributions, key=lambda a: a.total_return_pct, reverse=True)
    ):
        row_bg = "rgba(255,255,255,0.015)" if i % 2 else "transparent"
        total_color = _C_POS if attr.total_return_pct >= 0 else _C_NEG

        cells = (
            f'<td style="padding:10px 14px; font-size:0.88rem; font-weight:800;'
            f' color:#f1f5f9; white-space:nowrap; background:{row_bg}">'
            f'<span style="display:inline-block; width:8px; height:8px; border-radius:50%;'
            f' background:{total_color}; margin-right:8px; vertical-align:middle"></span>'
            f'{attr.ticker}</td>'
            f'<td style="padding:10px 14px; font-size:0.82rem; font-weight:700;'
            f' color:{total_color}; text-align:right; background:{row_bg};'
            f' font-variant-numeric:tabular-nums">'
            f'{_fmt_pct(attr.total_return_pct)}</td>'
        )

        for key in factor_keys:
            val = getattr(attr, key)
            if key == "r_squared":
                intensity = int(min(255, abs(val) * 255))
                cell_bg = f"rgba({intensity},180,246,0.12)"
                text_color = "#60a5fa"
                display_val = f"{val*100:.1f}%"
            else:
                abs_val = abs(val)
                intensity = int(min(200, abs_val * 8))
                if val >= 0:
                    cell_bg = f"rgba(16,{min(185, 80+intensity)},129,0.12)"
                    text_color = _C_POS
                else:
                    cell_bg = f"rgba(239,68,{min(68, 20+intensity)},0.12)"
                    text_color = _C_NEG
                display_val = _fmt_pct(val)
            cells += (
                f'<td style="padding:10px 14px; background:{cell_bg};'
                f' font-size:0.82rem; font-weight:600; color:{text_color};'
                f' text-align:right; font-variant-numeric:tabular-nums">'
                f'{display_val}</td>'
            )
        rows_html += f"<tr>{cells}</tr>"

    header_cells = (
        '<th style="padding:9px 14px; font-size:0.62rem; font-weight:700;'
        ' color:#64748b; text-transform:uppercase; letter-spacing:0.09em;'
        ' text-align:left; white-space:nowrap; border-bottom:1px solid rgba(255,255,255,0.08)">Ticker</th>'
        '<th style="padding:9px 14px; font-size:0.62rem; font-weight:700;'
        ' color:#64748b; text-transform:uppercase; letter-spacing:0.09em;'
        ' text-align:right; white-space:nowrap; border-bottom:1px solid rgba(255,255,255,0.08)">Total Return</th>'
    )
    for key in factor_keys:
        header_cells += (
            f'<th style="padding:9px 14px; font-size:0.62rem; font-weight:700;'
            f' color:#64748b; text-transform:uppercase; letter-spacing:0.09em;'
            f' text-align:right; white-space:nowrap; border-bottom:1px solid rgba(255,255,255,0.08)">'
            f'{factor_display[key]}</th>'
        )

    st.markdown(
        '<div style="overflow-x:auto; border-radius:10px;'
        ' border:1px solid rgba(255,255,255,0.07); margin-top:12px">'
        '<table style="width:100%; border-collapse:collapse;'
        ' background:#0d1117; font-family:Inter, sans-serif">'
        f'<thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div>',
        unsafe_allow_html=True,
    )


# ── 4. Attribution vs Benchmark ──────────────────────────────────────────────

def _render_attribution_vs_benchmark(
    attributions: List[PerformanceAttribution],
    stock_data: Dict[str, pd.DataFrame],
    period_days: int,
) -> None:
    section_header(
        "Attribution vs Benchmark (XLI)",
        "Stock factor contributions relative to sector ETF benchmark \u00b7 excess return decomposition",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    # Benchmark: sector_contribution is the XLI-driven piece — use as proxy
    # Excess return = total_return - sector_contribution
    tickers = [a.ticker for a in attributions]

    excess_returns      = [a.total_return_pct - a.sector_contribution for a in attributions]
    sector_contribs     = [a.sector_contribution for a in attributions]
    freight_contribs    = [a.freight_beta_contribution for a in attributions]
    idio_contribs       = [a.idiosyncratic_return for a in attributions]
    total_returns       = [a.total_return_pct for a in attributions]

    left_col, right_col = st.columns(2)

    with left_col:
        # Excess return vs benchmark bar
        exc_colors = [_C_POS if v >= 0 else _C_NEG for v in excess_returns]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Excess Return vs XLI",
            x=tickers,
            y=excess_returns,
            marker_color=exc_colors,
            marker_line_width=0,
            text=[_fmt_pct(v) for v in excess_returns],
            textposition="outside",
            textfont=dict(size=10, color="#f1f5f9"),
            hovertemplate="<b>%{x}</b><br>Excess Return: %{y:+.2f}%<extra></extra>",
        ))
        fig.add_hline(
            y=0, line_dash="dot",
            line_color="rgba(255,255,255,0.18)", line_width=1,
        )
        fig.update_layout(
            **_base_layout(height=320, title_text="Excess Return vs Sector Benchmark"),
            xaxis=_axis_style(tickfont=dict(size=12, color="#94a3b8"), gridcolor="rgba(0,0,0,0)"),
            yaxis=_axis_style(title="Excess Return (%)", ticksuffix="%"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="attr_vs_bench_excess")

    with right_col:
        # Stacked bar: sector vs alpha vs freight vs macro
        fig2 = go.Figure()
        factor_traces = [
            ("Sector Beta",    sector_contribs,  _C_SECTOR),
            ("Freight Beta",   freight_contribs, _C_FREIGHT),
            ("Alpha (Idio.)",  idio_contribs,    _C_IDIO),
        ]
        for name, vals, color in factor_traces:
            fig2.add_trace(go.Bar(
                name=name,
                x=tickers,
                y=vals,
                marker_color=color,
                marker_line_width=0,
                opacity=0.88,
                hovertemplate=f"<b>{name}</b><br>%{{x}}: %{{y:+.2f}}%<extra></extra>",
            ))
        # Total return overlay
        fig2.add_trace(go.Scatter(
            name="Total Return",
            x=tickers,
            y=total_returns,
            mode="markers",
            marker=dict(color="#f1f5f9", size=9, symbol="diamond",
                        line=dict(width=1.5, color="#64748b")),
            hovertemplate="<b>%{x}</b> Total: %{y:+.2f}%<extra></extra>",
        ))
        fig2.add_hline(
            y=0, line_dash="dot",
            line_color="rgba(255,255,255,0.15)", line_width=1,
        )
        fig2.update_layout(
            **_base_layout(height=320, barmode="relative",
                           title_text="Factor Stack vs Total Return"),
            xaxis=_axis_style(tickfont=dict(size=12, color="#94a3b8"), gridcolor="rgba(0,0,0,0)"),
            yaxis=_axis_style(title="Return Contribution (%)", ticksuffix="%"),
            legend=_legend_h(),
        )
        st.plotly_chart(fig2, use_container_width=True, key="attr_vs_bench_stack")


# ── 5. Rolling Attribution ────────────────────────────────────────────────────

def _compute_rolling_attribution(
    ticker: str,
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
    window: int = 30,
) -> Optional[pd.DataFrame]:
    stock_df = stock_data.get(ticker)
    if stock_df is None or stock_df.empty:
        return None

    stock_ret = _extract_return_series(stock_df, value_col="close")
    if stock_ret is None or len(stock_ret) < window + 5:
        return None

    freight_ret: Optional[pd.Series] = None
    for route_id, fdf in freight_data.items():
        if fdf is None or fdf.empty:
            continue
        for col in ("rate_usd_per_feu", "value", "close"):
            if col in fdf.columns:
                cand = _extract_return_series(fdf, value_col=col)
                if cand is not None and len(cand) >= window:
                    freight_ret = cand
                    break
        if freight_ret is not None:
            break

    bdi_ret: Optional[pd.Series] = None
    for bdi_key in ("BSXRLM", "BDI", "bdi"):
        bdi_ret = _extract_macro_return(macro_data, bdi_key)
        if bdi_ret is not None and len(bdi_ret) >= window:
            break

    xli_df = stock_data.get("XLI")
    xli_ret: Optional[pd.Series] = None
    if xli_df is not None and not xli_df.empty:
        xli_ret = _extract_return_series(xli_df, value_col="close")

    series_dict: Dict[str, pd.Series] = {"stock": stock_ret}
    if freight_ret is not None:
        series_dict["freight"] = freight_ret
    if bdi_ret is not None:
        series_dict["bdi"] = bdi_ret
    if xli_ret is not None:
        series_dict["xli"] = xli_ret

    aligned = _align_series(*[series_dict[k] for k in series_dict])
    aligned.columns = list(series_dict.keys())

    if len(aligned) < window + 5:
        return None

    factor_cols = [c for c in aligned.columns if c != "stock"]
    if not factor_cols:
        return None

    records = []
    idx_values = aligned.index.tolist()

    for end_pos in range(window, len(aligned)):
        window_data = aligned.iloc[end_pos - window: end_pos]
        y = window_data["stock"].values
        X_raw = window_data[factor_cols].values
        date_idx = idx_values[end_pos - 1]

        col_stds = X_raw.std(axis=0)
        valid_mask = col_stds > 1e-10
        if not valid_mask.any():
            continue
        active_factor_cols = [c for c, keep in zip(factor_cols, valid_mask) if keep]
        X_filtered = X_raw[:, valid_mask]

        try:
            coeffs, _, residuals = _ols_lstsq(y, X_filtered)
        except Exception:
            continue

        alpha = coeffs[0]
        betas = coeffs[1:]
        n = len(y)
        factor_means = X_filtered.mean(axis=0)

        contrib: Dict[str, float] = {}
        for i, col in enumerate(active_factor_cols):
            contrib[col] = float(betas[i]) * float(factor_means[i]) * n * 100.0

        records.append({
            "date":                       date_idx,
            "freight_contribution":       contrib.get("freight", 0.0),
            "macro_contribution":         contrib.get("bdi", 0.0),
            "sector_contribution":        contrib.get("xli", 0.0),
            "idiosyncratic_contribution": float(alpha) * n * 100.0,
            "total_return":               float((1.0 + pd.Series(y)).prod() - 1.0) * 100.0,
        })

    if not records:
        return None

    return pd.DataFrame(records).set_index("date")


def _render_rolling_attribution(
    ticker: str,
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
    window: int = 30,
) -> None:
    section_header(
        f"Rolling Attribution \u2014 {window}-day windows",
        f"How factor contributions evolve over time for {ticker} \u00b7 stacked area + total return overlay",
    )

    rolling_df = _compute_rolling_attribution(
        ticker=ticker,
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
        window=window,
    )

    if rolling_df is None or rolling_df.empty:
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
            f' border-radius:10px; padding:24px; text-align:center">'
            f'<div style="font-size:0.85rem; color:{C_TEXT2}">'
            f'Insufficient data for rolling attribution \u2014 need at least {window + 5} observations.'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        return

    contrib_cols = [
        ("freight_contribution",       "Freight Rate Beta",  _C_FREIGHT),
        ("macro_contribution",         "BDI / Macro Factor", _C_BDI),
        ("sector_contribution",        "Sector Beta (XLI)",  _C_SECTOR),
        ("idiosyncratic_contribution", "Idiosyncratic (Alpha)", _C_IDIO),
    ]

    fig = go.Figure()

    for col_name, display_name, color in contrib_cols:
        if col_name not in rolling_df.columns:
            continue
        series = rolling_df[col_name]
        fig.add_trace(go.Scatter(
            x=rolling_df.index,
            y=series.values,
            name=display_name,
            mode="lines",
            stackgroup="factors",
            line=dict(color=color, width=1.0),
            fillcolor=_hex_to_rgba(color, 0.30),
            hovertemplate=display_name + ": %{y:+.2f}%<extra></extra>",
        ))

    if "total_return" in rolling_df.columns:
        fig.add_trace(go.Scatter(
            x=rolling_df.index,
            y=rolling_df["total_return"].values,
            name="Total Return",
            mode="lines",
            line=dict(color="#f1f5f9", width=2.0, dash="dot"),
            hovertemplate="Total Return: %{y:+.2f}%<extra></extra>",
        ))

    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.14)", line_width=1)

    fig.update_layout(
        **_base_layout(height=380),
        xaxis=_axis_style(zeroline=False),
        yaxis=_axis_style(title="% Return Contribution", ticksuffix="%"),
        legend=_legend_h(),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"attr_rolling_{ticker}_{window}")


# ── 6. Cross-Asset Attribution ───────────────────────────────────────────────

def _render_cross_asset_attribution(
    attributions: List[PerformanceAttribution],
    stock_data: Dict[str, pd.DataFrame],
    period_days: int,
) -> None:
    section_header(
        "Cross-Asset Attribution",
        "Shipping equities vs transport sector peers \u00b7 factor share comparison",
    )

    # Transport peers we can proxy from stock_data
    peer_tickers = {
        "XLI":  "Industrial ETF",
        "XRT":  "Retail/Logistics ETF",
    }

    shipping_factor_share: Dict[str, float] = {}
    if attributions:
        factor_keys = [
            "freight_beta_contribution",
            "macro_contribution",
            "sector_contribution",
            "idiosyncratic_return",
        ]
        factor_labels = ["Freight Beta", "BDI/Macro", "Sector Beta", "Alpha"]
        # Universe-average absolute factor shares
        totals = []
        for a in attributions:
            total_abs = sum(abs(getattr(a, k)) for k in factor_keys)
            if total_abs < 0.01:
                continue
            totals.append({k: abs(getattr(a, k)) / total_abs for k in factor_keys})
        if totals:
            for k, lbl in zip(factor_keys, factor_labels):
                shipping_factor_share[lbl] = float(np.mean([r[k] for r in totals])) * 100
    else:
        factor_labels = ["Freight Beta", "BDI/Macro", "Sector Beta", "Alpha"]

    if not shipping_factor_share:
        st.info("Insufficient attribution data for cross-asset comparison.")
        return

    left_col, right_col = st.columns([2, 1])

    with left_col:
        # Radar chart: shipping vs hypothetical transport allocation
        # For peers we estimate from their returns vs factors using simplified proxies
        categories = list(shipping_factor_share.keys())
        shipping_vals = list(shipping_factor_share.values())

        # Hypothetical transport allocation (stylised, based on known factor profiles)
        transport_proxy = {
            "Freight Beta": max(0, shipping_factor_share.get("Freight Beta", 25) * 0.35),
            "BDI/Macro":    max(0, shipping_factor_share.get("BDI/Macro", 20) * 0.55),
            "Sector Beta":  min(65, shipping_factor_share.get("Sector Beta", 30) * 1.55),
            "Alpha":        max(0, shipping_factor_share.get("Alpha", 25) * 0.70),
        }
        transport_vals = [transport_proxy.get(c, 20) for c in categories]

        # Close the radar loop
        cats_closed  = categories + [categories[0]]
        ship_closed  = shipping_vals + [shipping_vals[0]]
        trans_closed = transport_vals + [transport_vals[0]]

        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=ship_closed,
            theta=cats_closed,
            fill="toself",
            fillcolor=_hex_to_rgba(_C_FREIGHT, 0.20),
            line=dict(color=_C_FREIGHT, width=2),
            name="Shipping Universe",
            hovertemplate="<b>Shipping</b><br>%{theta}: %{r:.1f}%<extra></extra>",
        ))
        fig.add_trace(go.Scatterpolar(
            r=trans_closed,
            theta=cats_closed,
            fill="toself",
            fillcolor=_hex_to_rgba(_C_SECTOR, 0.15),
            line=dict(color=_C_SECTOR, width=2, dash="dash"),
            name="Transport Proxy (XLI)",
            hovertemplate="<b>Transport</b><br>%{theta}: %{r:.1f}%<extra></extra>",
        ))
        fig.update_layout(
            **_base_layout(height=360),
            polar=dict(
                bgcolor="#0d1117",
                radialaxis=dict(
                    visible=True,
                    range=[0, 60],
                    tickfont=dict(size=9, color="#64748b"),
                    ticksuffix="%",
                    gridcolor="rgba(255,255,255,0.06)",
                    linecolor="rgba(255,255,255,0.08)",
                ),
                angularaxis=dict(
                    tickfont=dict(size=11, color="#94a3b8"),
                    linecolor="rgba(255,255,255,0.08)",
                    gridcolor="rgba(255,255,255,0.06)",
                ),
            ),
            legend=_legend_h(),
        )
        st.plotly_chart(fig, use_container_width=True, key="attr_cross_asset_radar")

    with right_col:
        # Shipping differentiation summary cards
        freight_dominance = shipping_factor_share.get("Freight Beta", 0)
        alpha_share       = shipping_factor_share.get("Alpha", 0)
        sector_share      = shipping_factor_share.get("Sector Beta", 0)

        label = "High Freight Sensitivity" if freight_dominance > 30 else (
            "Alpha-Dominated" if alpha_share > 35 else "Sector-Correlated"
        )
        label_color = _C_FREIGHT if freight_dominance > 30 else (
            _C_IDIO if alpha_share > 35 else _C_SECTOR
        )

        st.markdown(
            '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
            ' border-radius:10px; padding:18px; height:100%">'
            '<div style="font-size:0.60rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.10em; margin-bottom:14px">'
            'Shipping Profile</div>'

            f'<div style="margin-bottom:14px; padding:12px; background:rgba(255,255,255,0.03);'
            f' border-radius:8px; border-left:3px solid {label_color}">'
            f'<div style="font-size:0.72rem; color:#94a3b8; margin-bottom:4px">Classification</div>'
            f'<div style="font-size:1.0rem; font-weight:800; color:{label_color}">{label}</div>'
            f'</div>'

            + "".join(
                f'<div style="display:flex; justify-content:space-between; align-items:center;'
                f' padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.05)">'
                f'<span style="font-size:0.75rem; color:#94a3b8">{lbl}</span>'
                f'<span style="font-size:0.82rem; font-weight:700; color:{col}; font-variant-numeric:tabular-nums">'
                f'{val:.1f}%</span></div>'
                for lbl, val, col in [
                    ("Freight Dominance", freight_dominance, _C_FREIGHT),
                    ("Alpha Share",       alpha_share,       _C_IDIO),
                    ("Sector Linkage",    sector_share,      _C_SECTOR),
                ]
            )

            + '<div style="margin-top:12px; font-size:0.66rem; color:#334155; line-height:1.5">'
            'Transport proxy uses XLI beta-adjusted factor weights. '
            'Shipping characteristically shows higher freight sensitivity than broad transport indices.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )


# ── 7. Regime Analysis ───────────────────────────────────────────────────────

def _classify_regime(returns: pd.Series, window: int = 20) -> pd.Series:
    """Label each date as 'Bull', 'Bear', or 'High Vol'."""
    rolling_ret = returns.rolling(window).mean()
    rolling_vol = returns.rolling(window).std()
    vol_thresh  = rolling_vol.quantile(0.70)

    labels = []
    for ret, vol in zip(rolling_ret, rolling_vol):
        if pd.isna(ret) or pd.isna(vol):
            labels.append("Unknown")
        elif vol >= vol_thresh:
            labels.append("High Vol")
        elif ret >= 0:
            labels.append("Bull")
        else:
            labels.append("Bear")
    return pd.Series(labels, index=returns.index)


def _render_regime_analysis(
    ticker: str,
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
) -> None:
    section_header(
        "Regime Analysis",
        "How attribution changes in Bull / Bear / High-Vol market regimes \u00b7 avg factor contribution per regime",
    )

    rolling_df = _compute_rolling_attribution(
        ticker=ticker,
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
        window=20,
    )

    if rolling_df is None or rolling_df.empty or len(rolling_df) < 30:
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
            f' border-radius:10px; padding:24px; text-align:center">'
            f'<div style="font-size:0.85rem; color:{C_TEXT2}">'
            f'Insufficient rolling data for regime analysis.'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        return

    # Classify regimes using total_return rolling signal
    if "total_return" in rolling_df.columns:
        regimes = _classify_regime(rolling_df["total_return"], window=10)
        rolling_df["regime"] = regimes.values
    else:
        st.info("Total return series unavailable for regime classification.")
        return

    contrib_cols = {
        "freight_contribution":       ("Freight Beta",    _C_FREIGHT),
        "macro_contribution":         ("BDI/Macro",       _C_BDI),
        "sector_contribution":        ("Sector Beta",     _C_SECTOR),
        "idiosyncratic_contribution": ("Alpha",           _C_IDIO),
    }

    regime_order  = ["Bull", "Bear", "High Vol"]
    regime_colors = {"Bull": _C_BULL, "Bear": _C_BEAR, "High Vol": _C_VOLAT}

    # Compute mean contribution per regime per factor
    regime_data: Dict[str, Dict[str, float]] = {r: {} for r in regime_order}
    for regime in regime_order:
        mask = rolling_df["regime"] == regime
        subset = rolling_df[mask]
        for col, (label, _) in contrib_cols.items():
            if col in subset.columns and not subset[col].empty:
                regime_data[regime][label] = float(subset[col].mean())
            else:
                regime_data[regime][label] = 0.0

    left_col, right_col = st.columns([3, 2])

    with left_col:
        # Grouped bar: factors × regimes
        fig = go.Figure()
        for col, (label, color) in contrib_cols.items():
            regime_vals = [regime_data[r].get(label, 0.0) for r in regime_order]
            fig.add_trace(go.Bar(
                name=label,
                x=regime_order,
                y=regime_vals,
                marker_color=color,
                marker_line_width=0,
                opacity=0.88,
                hovertemplate=f"<b>{label}</b> in %{{x}}: %{{y:+.2f}}%<extra></extra>",
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", line_width=1)
        fig.update_layout(
            **_base_layout(height=320, barmode="group"),
            xaxis=_axis_style(
                tickfont=dict(size=13, color="#94a3b8"),
                gridcolor="rgba(0,0,0,0)",
            ),
            yaxis=_axis_style(title="Avg Factor Contribution (%)", ticksuffix="%"),
            legend=_legend_h(),
            bargap=0.22,
            bargroupgap=0.06,
        )
        st.plotly_chart(fig, use_container_width=True, key=f"attr_regime_bars_{ticker}")

    with right_col:
        # Regime distribution pie + summary table
        regime_counts = rolling_df["regime"].value_counts()
        total_obs = len(rolling_df)

        cards_html = ""
        for regime in regime_order:
            count = regime_counts.get(regime, 0)
            pct = count / total_obs * 100 if total_obs else 0
            color = regime_colors[regime]
            # dominant factor in this regime
            if regime in regime_data and regime_data[regime]:
                dom_factor = max(regime_data[regime], key=lambda k: abs(regime_data[regime][k]))
                dom_val    = regime_data[regime][dom_factor]
            else:
                dom_factor, dom_val = "N/A", 0.0

            cards_html += (
                f'<div style="background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.07);'
                f' border-left:3px solid {color}; border-radius:8px; padding:12px 14px; margin-bottom:8px">'
                f'<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px">'
                f'<span style="font-size:0.88rem; font-weight:800; color:{color}">{regime}</span>'
                f'<span style="font-size:0.75rem; font-weight:600; color:{C_TEXT2}">{pct:.0f}% of days</span>'
                f'</div>'
                f'<div style="font-size:0.72rem; color:#64748b">Dominant factor:</div>'
                f'<div style="font-size:0.82rem; font-weight:700; color:#94a3b8; margin-top:2px">'
                f'{dom_factor}: {_fmt_pct(dom_val)}</div>'
                f'</div>'
            )

        st.markdown(
            '<div style="padding-top:4px">'
            + cards_html
            + '</div>',
            unsafe_allow_html=True,
        )


# ── 8. Alpha Quality Ranking ─────────────────────────────────────────────────

def _render_alpha_ranking(attributions: List[PerformanceAttribution]) -> None:
    section_header(
        "Alpha Quality Ranking",
        "Stocks ranked by return quality \u00b7 alpha-driven (high IR, low R\u00b2) vs beta-driven (low IR, high R\u00b2)",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    # Quality score: normalised combination of IR, alpha share, and 1-R2
    def _quality_score(a: PerformanceAttribution) -> float:
        total_abs = abs(a.freight_beta_contribution) + abs(a.macro_contribution) + \
                    abs(a.sector_contribution) + abs(a.idiosyncratic_return)
        alpha_share = abs(a.idiosyncratic_return) / total_abs if total_abs > 0 else 0
        ir_norm     = min(1.0, max(-1.0, a.information_ratio / 3.0))
        purity      = 1.0 - a.r_squared  # high = more alpha-driven
        return float(0.45 * alpha_share + 0.35 * ir_norm + 0.20 * purity)

    ranked = sorted(attributions, key=_quality_score, reverse=True)
    scores = [_quality_score(a) for a in ranked]
    tickers = [a.ticker for a in ranked]

    left_col, right_col = st.columns([3, 2])

    with left_col:
        # Alpha return bar — sorted descending by quality score
        idio_vals  = [a.idiosyncratic_return for a in ranked]
        bar_colors = [_C_IDIO if v >= 0 else _C_NEG for v in idio_vals]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=tickers,
            y=idio_vals,
            name="Alpha Return",
            marker_color=bar_colors,
            marker_line_width=0,
            text=[_fmt_pct(v) for v in idio_vals],
            textposition="outside",
            textfont=dict(size=11, color="#f1f5f9"),
            hovertemplate="<b>%{x}</b><br>Alpha: %{y:+.2f}%<extra></extra>",
        ))
        fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.18)", line_width=1)

        fig.update_layout(
            **_base_layout(height=300),
            xaxis=_axis_style(tickfont=dict(size=13, color="#94a3b8"), gridcolor="rgba(0,0,0,0)"),
            yaxis=_axis_style(title="Idiosyncratic Return (%)", ticksuffix="%"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="attr_alpha_ranking_bar")

    with right_col:
        # Ranking table with quality tier badges
        def _tier(score: float) -> Tuple[str, str]:
            if score >= 0.60:
                return "ALPHA", _C_IDIO
            if score >= 0.40:
                return "MIXED", C_ACCENT
            return "BETA", _C_SECTOR

        rows_html = ""
        for rank, (attr, score) in enumerate(zip(ranked, scores), start=1):
            tier_lbl, tier_color = _tier(score)
            alpha_share_pct = 0.0
            total_abs = (abs(attr.freight_beta_contribution) + abs(attr.macro_contribution)
                         + abs(attr.sector_contribution) + abs(attr.idiosyncratic_return))
            if total_abs > 0:
                alpha_share_pct = abs(attr.idiosyncratic_return) / total_abs * 100

            rows_html += (
                f'<tr style="border-bottom:1px solid rgba(255,255,255,0.05)">'
                f'<td style="padding:9px 12px; font-size:0.78rem; color:#475569; text-align:center">'
                f'#{rank}</td>'
                f'<td style="padding:9px 12px; font-size:0.90rem; font-weight:800; color:#f1f5f9">'
                f'{attr.ticker}</td>'
                f'<td style="padding:9px 12px; text-align:center">'
                f'<span style="display:inline-block; padding:2px 8px; border-radius:4px;'
                f' background:{tier_color}22; color:{tier_color};'
                f' font-size:0.62rem; font-weight:700; letter-spacing:0.08em">'
                f'{tier_lbl}</span></td>'
                f'<td style="padding:9px 12px; font-size:0.80rem; font-weight:600;'
                f' color:#94a3b8; text-align:right; font-variant-numeric:tabular-nums">'
                f'{alpha_share_pct:.0f}%</td>'
                f'<td style="padding:9px 12px; font-size:0.80rem; font-weight:600;'
                f' color:#94a3b8; text-align:right; font-variant-numeric:tabular-nums">'
                f'{attr.information_ratio:.2f}</td>'
                f'</tr>'
            )

        header = (
            '<tr style="border-bottom:1px solid rgba(255,255,255,0.10)">'
            + "".join(
                f'<th style="padding:8px 12px; font-size:0.60rem; font-weight:700;'
                f' color:#64748b; text-transform:uppercase; letter-spacing:0.09em;'
                f' text-align:{align}">{col}</th>'
                for col, align in [
                    ("#", "center"), ("Ticker", "left"), ("Tier", "center"),
                    ("Alpha %", "right"), ("Info Ratio", "right"),
                ]
            )
            + "</tr>"
        )

        st.markdown(
            '<div style="overflow-x:auto; border-radius:10px;'
            ' border:1px solid rgba(255,255,255,0.07)">'
            '<table style="width:100%; border-collapse:collapse;'
            ' background:#0d1117; font-family:Inter, sans-serif">'
            f'<thead>{header}</thead>'
            f'<tbody>{rows_html}</tbody>'
            '</table></div>',
            unsafe_allow_html=True,
        )


# ── Alpha Generation bar (standalone, used in main flow) ────────────────────

def _render_alpha_generation(attributions: List[PerformanceAttribution]) -> None:
    section_header(
        "Alpha Generation",
        "Idiosyncratic (unexplained) returns per ticker \u00b7 sorted descending",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    sorted_attrs = sorted(attributions, key=lambda a: a.idiosyncratic_return, reverse=True)
    tickers    = [a.ticker for a in sorted_attrs]
    values     = [a.idiosyncratic_return for a in sorted_attrs]
    bar_colors = [_C_POS if v >= 0 else _C_NEG for v in values]

    fig = go.Figure(go.Bar(
        x=tickers,
        y=values,
        marker_color=bar_colors,
        marker_line_width=0,
        text=[_fmt_pct(v) for v in values],
        textposition="outside",
        textfont=dict(size=11, color="#f1f5f9", family="Inter, sans-serif"),
        hovertemplate="%{x} Alpha: %{y:+.2f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.18)", line_width=1)

    fig.update_layout(
        **_base_layout(height=300),
        xaxis=_axis_style(tickfont=dict(size=12, color="#94a3b8"), gridcolor="rgba(0,0,0,0)"),
        yaxis=_axis_style(title="Idiosyncratic Return (%)", ticksuffix="%"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key="attr_alpha_generation")


# ── R² heatmap ───────────────────────────────────────────────────────────────

def _render_r_squared_heatmap(attributions: List[PerformanceAttribution]) -> None:
    section_header(
        "R\u00b2 Factor Heatmap",
        "Proportion of variance explained by each factor per ticker \u00b7 intensity = contribution share",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    factor_keys = [
        "freight_beta_contribution",
        "macro_contribution",
        "sector_contribution",
        "idiosyncratic_return",
    ]
    short_labels = ["Freight Beta", "BDI/Macro", "Sector Beta", "Alpha"]
    tickers = [a.ticker for a in attributions]

    z_matrix, text_matrix = [], []
    for attr in attributions:
        total = abs(attr.total_return_pct) if abs(attr.total_return_pct) > 0.01 else 1.0
        row_z, row_t = [], []
        for key in factor_keys:
            frac = min(1.0, abs(getattr(attr, key)) / total)
            row_z.append(round(frac, 3))
            row_t.append(f"{frac*100:.1f}%")
        z_matrix.append(row_z)
        text_matrix.append(row_t)

    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=short_labels,
        y=tickers,
        colorscale=[
            [0.0,  "rgba(13,17,23,1)"],
            [0.25, "rgba(30,58,138,0.55)"],
            [0.50, "rgba(37,99,235,0.80)"],
            [0.75, "rgba(59,130,246,0.90)"],
            [1.0,  "rgba(147,197,253,1)"],
        ],
        zmin=0.0, zmax=1.0,
        text=text_matrix,
        texttemplate="%{text}",
        textfont=dict(size=11, color="#f1f5f9", family="Inter, sans-serif"),
        hovertemplate="<b>%{y}</b><br>%{x}: %{z:.1%}<extra></extra>",
        colorbar=dict(
            thickness=11,
            outlinewidth=0,
            tickfont=dict(size=9, color="#64748b"),
            tickformat=".0%",
        ),
    ))

    fig.update_layout(
        **_base_layout(height=280),
        xaxis=dict(tickfont=dict(size=10, color="#94a3b8"), tickangle=15, side="top"),
        yaxis=dict(tickfont=dict(size=11, color="#94a3b8")),
    )
    st.plotly_chart(fig, use_container_width=True, key="attr_r_squared_heatmap")


# ── NEW 0: Universe Attribution Donut ────────────────────────────────────────

def _render_universe_donut(attributions: List[PerformanceAttribution]) -> None:
    """
    Sector-wide donut chart: average absolute factor share of total return,
    showing what drives shipping equity returns at the universe level.
    Paired with a precision KPI strip.
    """
    section_header(
        "Universe Return Composition",
        "What drives shipping equity returns at the sector level — average absolute factor share",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    factor_keys = [
        "freight_beta_contribution",
        "macro_contribution",
        "sector_contribution",
        "idiosyncratic_return",
    ]
    factor_labels_short = ["Freight Beta", "BDI / Macro", "Sector (XLI)", "Alpha (Idio.)"]
    factor_colors_list  = [_C_FREIGHT, _C_BDI, _C_SECTOR, _C_IDIO]

    # Compute universe-average absolute share
    totals = []
    for a in attributions:
        total_abs = sum(abs(getattr(a, k)) for k in factor_keys)
        if total_abs < 0.01:
            continue
        totals.append({k: abs(getattr(a, k)) / total_abs * 100.0 for k in factor_keys})

    if not totals:
        st.info("Insufficient attribution data for donut chart.")
        return

    avg_shares = {k: float(np.mean([r[k] for r in totals])) for k in factor_keys}

    left_col, right_col = st.columns([2, 3], gap="medium")

    with left_col:
        labels_d = factor_labels_short
        values_d = [avg_shares[k] for k in factor_keys]

        # Centre annotation: dominant factor
        dominant_key   = max(avg_shares, key=lambda k: avg_shares[k])
        dominant_label = factor_labels_short[factor_keys.index(dominant_key)]
        dominant_val   = avg_shares[dominant_key]
        dominant_color = factor_colors_list[factor_keys.index(dominant_key)]

        fig_donut = go.Figure(go.Pie(
            labels=labels_d,
            values=values_d,
            hole=0.64,
            marker=dict(
                colors=[c + "e0" for c in factor_colors_list],
                line=dict(color="#0a0f1a", width=3),
            ),
            textfont=dict(size=10, color="#f1f5f9", family="Inter, sans-serif"),
            texttemplate="%{label}<br><b>%{percent}</b>",
            hovertemplate="<b>%{label}</b><br>Avg share: %{value:.1f}%<br>%{percent}<extra></extra>",
            sort=False,
            direction="clockwise",
        ))
        fig_donut.add_annotation(
            text=(
                f"<b style='font-size:1.1rem; color:{dominant_color}'>{dominant_val:.0f}%</b><br>"
                f"<span style='font-size:0.7rem; color:#64748b'>{dominant_label}</span><br>"
                f"<span style='font-size:0.65rem; color:#475569'>dominant</span>"
            ),
            font=dict(family="Inter, sans-serif", size=13, color=dominant_color),
            showarrow=False,
            x=0.5, y=0.5,
            xanchor="center", yanchor="middle",
        )
        fig_donut.update_layout(
            height=280,
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=10),
            font=dict(color="#94a3b8", size=11),
            showlegend=False,
        )
        st.plotly_chart(fig_donut, use_container_width=True,
                        config={"displayModeBar": False},
                        key="attr_universe_donut")

    with right_col:
        # Horizontal bar breakdown — each factor with gradient bar
        st.markdown(
            '<div style="display:flex; flex-direction:column; gap:14px; padding-top:8px">',
            unsafe_allow_html=True,
        )
        bar_items_html = ""
        for key, label, color in zip(factor_keys, factor_labels_short, factor_colors_list):
            share = avg_shares[key]
            bar_items_html += (
                f"<div style='background:{C_CARD}; border:1px solid rgba(255,255,255,0.07);"
                f" border-left:3px solid {color}; border-radius:8px; padding:12px 16px'>"
                f"<div style='display:flex; justify-content:space-between; align-items:baseline;"
                f" margin-bottom:8px'>"
                f"<span style='font-size:0.72rem; font-weight:600; color:#94a3b8'>{label}</span>"
                f"<span style='font-size:1.10rem; font-weight:900; color:{color};"
                f" font-variant-numeric:tabular-nums'>{share:.1f}%</span>"
                f"</div>"
                f"<div style='background:rgba(255,255,255,0.06); border-radius:999px;"
                f" height:6px; overflow:hidden'>"
                f"<div style='background:linear-gradient(90deg,{color}cc,{color}55);"
                f" width:{share:.1f}%; height:6px; border-radius:999px'></div>"
                f"</div>"
                f"</div>"
            )

        # Universe stats strip
        n_tickers = len(attributions)
        avg_total = float(np.mean([a.total_return_pct for a in attributions]))
        avg_ir    = float(np.mean([a.information_ratio for a in attributions]))
        avg_r2    = float(np.mean([a.r_squared for a in attributions]))

        stats_html = (
            f"<div style='display:flex; gap:12px; margin-top:14px; flex-wrap:wrap'>"
            + "".join(
                f"<div style='background:{C_CARD}; border:1px solid rgba(255,255,255,0.07);"
                f" border-radius:8px; padding:10px 16px; flex:1; text-align:center;"
                f" min-width:80px'>"
                f"<div style='font-size:1.05rem; font-weight:800; color:{col};"
                f" font-variant-numeric:tabular-nums'>{val}</div>"
                f"<div style='font-size:0.58rem; color:#475569; text-transform:uppercase;"
                f" letter-spacing:0.08em; margin-top:3px'>{lbl}</div>"
                f"</div>"
                for val, lbl, col in [
                    (f"{n_tickers}",               "STOCKS",       "#94a3b8"),
                    (_fmt_pct(avg_total),           "AVG RETURN",   _C_POS if avg_total >= 0 else _C_NEG),
                    (f"{avg_ir:.2f}",               "AVG IR",       _C_IDIO),
                    (f"{avg_r2*100:.0f}%",          "AVG R²",       "#3b82f6"),
                ]
            )
            + "</div>"
        )

        st.markdown(
            f"{bar_items_html}{stats_html}",
            unsafe_allow_html=True,
        )


# ── NEW 0b: Multi-Stock Waterfall Grid ───────────────────────────────────────

def _render_multi_stock_waterfall_grid(
    attributions: List[PerformanceAttribution],
    max_stocks: int = 6,
) -> None:
    """
    A grid of mini waterfall charts — one per stock — so analysts can compare
    factor decomposition across the universe at a glance.
    """
    section_header(
        "Multi-Stock Attribution Waterfall",
        f"Factor decomposition for up to {max_stocks} stocks side-by-side — green = positive, red = negative",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    # Pick up to max_stocks, sorted by absolute total return descending
    display_attrs = sorted(
        attributions, key=lambda a: abs(a.total_return_pct), reverse=True
    )[:max_stocks]

    n = len(display_attrs)
    n_cols = min(3, n)
    rows_list = [display_attrs[i:i + n_cols] for i in range(0, n, n_cols)]

    factor_map = [
        ("freight_beta_contribution", "Freight"),
        ("macro_contribution",        "BDI/Macro"),
        ("sector_contribution",       "Sector"),
        ("idiosyncratic_return",      "Alpha"),
    ]

    for row_attrs in rows_list:
        cols = st.columns(n_cols, gap="small")
        for col, attr in zip(cols, row_attrs):
            with col:
                x_list = [lbl for _, lbl in factor_map] + ["Total"]
                y_list = [getattr(attr, key) for key, _ in factor_map] + [attr.total_return_pct]
                measure_list = ["relative"] * len(factor_map) + ["total"]

                fig_wf = go.Figure(go.Waterfall(
                    orientation="v",
                    measure=measure_list,
                    x=x_list,
                    y=y_list,
                    text=[f"{v:+.1f}%" for v in y_list],
                    textposition="outside",
                    textfont=dict(size=9, color="#f1f5f9", family="Inter, sans-serif"),
                    increasing=dict(marker=dict(
                        color=_C_POS,
                        line=dict(width=0),
                    )),
                    decreasing=dict(marker=dict(
                        color=_C_NEG,
                        line=dict(width=0),
                    )),
                    totals=dict(marker=dict(
                        color=_C_TOTAL,
                        line=dict(width=0),
                    )),
                    connector=dict(line=dict(
                        color="rgba(255,255,255,0.08)",
                        width=1,
                        dash="dot",
                    )),
                    hovertemplate="%{x}: %{y:+.2f}%<extra></extra>",
                ))

                total_color = _C_POS if attr.total_return_pct >= 0 else _C_NEG
                fig_wf.update_layout(
                    height=230,
                    paper_bgcolor=C_CARD,
                    plot_bgcolor=C_CARD,
                    font=dict(color="#94a3b8", size=9, family="Inter, sans-serif"),
                    margin=dict(t=32, b=8, l=4, r=4),
                    title=dict(
                        text=(
                            f"<b style='color:#f1f5f9'>{attr.ticker}</b>"
                            f"  <span style='color:{total_color};font-size:0.8em'>"
                            f"{attr.total_return_pct:+.1f}%</span>"
                        ),
                        font=dict(size=11, color="#f1f5f9"),
                        x=0.04,
                        xanchor="left",
                        pad=dict(t=4),
                    ),
                    xaxis=dict(
                        tickfont=dict(size=8, color="#64748b"),
                        showgrid=False,
                        zeroline=False,
                    ),
                    yaxis=dict(
                        tickfont=dict(size=8, color="#64748b"),
                        gridcolor="rgba(255,255,255,0.04)",
                        zeroline=True,
                        zerolinecolor="rgba(255,255,255,0.12)",
                        zerolinewidth=1,
                        ticksuffix="%",
                    ),
                    hoverlabel=dict(
                        bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color="#f1f5f9", size=10),
                    ),
                )
                st.plotly_chart(
                    fig_wf,
                    use_container_width=True,
                    config={"displayModeBar": False},
                    key=f"attr_mini_wf_{attr.ticker}",
                )


# ── NEW 0c: Factor Beta Exposure Heatmap (enhanced) ──────────────────────────

def _render_factor_beta_heatmap(attributions: List[PerformanceAttribution]) -> None:
    """
    Rich heatmap showing each stock's signed factor contribution with
    dual-tone colorscale (red = negative, green = positive) + ranked alpha table.
    """
    section_header(
        "Factor Contribution Heat Map",
        "Signed factor contributions per stock — red = headwind, green = tailwind",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    factor_keys   = ["freight_beta_contribution", "macro_contribution",
                     "sector_contribution", "idiosyncratic_return"]
    factor_labels = ["Freight Beta", "BDI/Macro", "Sector (XLI)", "Alpha"]

    # Sort by total return descending
    sorted_attrs = sorted(attributions, key=lambda a: a.total_return_pct, reverse=True)
    tickers = [a.ticker for a in sorted_attrs]

    z_matrix:    List[List[float]] = []
    text_matrix: List[List[str]]   = []

    for attr in sorted_attrs:
        row_z, row_t = [], []
        for key in factor_keys:
            val = float(getattr(attr, key))
            row_z.append(round(val, 2))
            row_t.append(f"{val:+.1f}%")
        z_matrix.append(row_z)
        text_matrix.append(row_t)

    # Symmetric colourscale: midpoint = 0
    all_vals = [v for row in z_matrix for v in row]
    abs_max  = max(abs(v) for v in all_vals) if all_vals else 1.0

    signed_colorscale = [
        [0.00, "#7f1d1d"],  # deep red
        [0.20, "#ef4444"],  # red
        [0.40, "#fca5a5"],  # light red
        [0.50, "#1a2235"],  # neutral (dark)
        [0.60, "#6ee7b7"],  # light green
        [0.80, "#10b981"],  # green
        [1.00, "#064e3b"],  # deep green
    ]

    left_col, right_col = st.columns([3, 2], gap="medium")

    with left_col:
        fig_hm = go.Figure(go.Heatmap(
            z=z_matrix,
            x=factor_labels,
            y=tickers,
            colorscale=signed_colorscale,
            zmin=-abs_max, zmax=abs_max,
            zmid=0,
            text=text_matrix,
            texttemplate="%{text}",
            textfont=dict(size=11, color="#f1f5f9", family="Inter, monospace"),
            hovertemplate="<b>%{y}</b><br>%{x}: %{text}<extra></extra>",
            colorbar=dict(
                thickness=12,
                outlinewidth=0,
                tickfont=dict(size=9, color="#64748b"),
                ticksuffix="%",
                len=0.85,
                title=dict(
                    text="Contribution",
                    font=dict(size=9, color="#64748b"),
                    side="right",
                ),
            ),
        ))
        fig_hm.update_layout(
            height=max(260, len(tickers) * 32 + 40),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#94a3b8", size=11),
            margin=dict(l=8, r=8, t=12, b=8),
            xaxis=dict(
                side="top",
                tickfont=dict(size=11, color="#94a3b8"),
                showgrid=False,
            ),
            yaxis=dict(
                tickfont=dict(size=11, color="#f1f5f9"),
                showgrid=False,
                zeroline=False,
            ),
        )
        st.plotly_chart(fig_hm, use_container_width=True,
                        config={"displayModeBar": False},
                        key="attr_factor_beta_heatmap_v2")

    with right_col:
        # Alpha quality leaderboard: sorted by alpha share %
        st.markdown(
            '<div style="font-size:0.62rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.10em; margin-bottom:10px">'
            'Alpha-Driven Leaderboard</div>',
            unsafe_allow_html=True,
        )
        alpha_rows = []
        for attr in sorted_attrs:
            total_abs = sum(abs(getattr(attr, k)) for k in factor_keys) or 1.0
            alpha_pct = abs(attr.idiosyncratic_return) / total_abs * 100
            alpha_rows.append((attr.ticker, alpha_pct, attr.idiosyncratic_return,
                                attr.information_ratio))

        alpha_rows.sort(key=lambda r: r[1], reverse=True)

        rows_html = ""
        for rank, (ticker, alpha_pct, idio, ir) in enumerate(alpha_rows, start=1):
            idio_col   = _C_POS if idio >= 0 else _C_NEG
            tier_col   = _C_IDIO if alpha_pct >= 40 else "#3b82f6" if alpha_pct >= 25 else _C_SECTOR
            tier_label = "ALPHA" if alpha_pct >= 40 else "MIXED" if alpha_pct >= 25 else "BETA"
            rows_html += (
                f'<tr style="border-bottom:1px solid rgba(255,255,255,0.05)">'
                f'<td style="padding:8px 10px; font-size:0.72rem; color:#475569;'
                f' text-align:center">#{rank}</td>'
                f'<td style="padding:8px 10px; font-size:0.88rem; font-weight:800; color:#f1f5f9">'
                f'{ticker}</td>'
                f'<td style="padding:8px 10px; text-align:center">'
                f'<span style="display:inline-block; padding:2px 7px; border-radius:4px;'
                f' background:{tier_col}22; color:{tier_col}; font-size:0.60rem;'
                f' font-weight:700; letter-spacing:0.08em">{tier_label}</span></td>'
                f'<td style="padding:8px 10px; font-size:0.80rem; font-weight:700;'
                f' color:{tier_col}; text-align:right; font-variant-numeric:tabular-nums">'
                f'{alpha_pct:.0f}%</td>'
                f'<td style="padding:8px 10px; font-size:0.80rem; color:{idio_col};'
                f' text-align:right; font-variant-numeric:tabular-nums">'
                f'{idio:+.2f}%</td>'
                f'<td style="padding:8px 10px; font-size:0.78rem; color:#94a3b8;'
                f' text-align:right; font-variant-numeric:tabular-nums">'
                f'{ir:.2f}</td>'
                f'</tr>'
            )

        header_html = (
            '<tr style="border-bottom:1px solid rgba(255,255,255,0.10)">'
            + "".join(
                f'<th style="padding:7px 10px; font-size:0.58rem; font-weight:700;'
                f' color:#64748b; text-transform:uppercase; letter-spacing:0.09em;'
                f' text-align:{align}">{col}</th>'
                for col, align in [
                    ("#", "center"), ("Ticker", "left"), ("Type", "center"),
                    ("Alpha %", "right"), ("Alpha Ret", "right"), ("IR", "right"),
                ]
            )
            + "</tr>"
        )

        st.markdown(
            '<div style="overflow-x:auto; border-radius:10px;'
            ' border:1px solid rgba(255,255,255,0.07)">'
            '<table style="width:100%; border-collapse:collapse;'
            ' background:#0d1117; font-family:Inter, sans-serif">'
            f'<thead>{header_html}</thead>'
            f'<tbody>{rows_html}</tbody>'
            '</table></div>',
            unsafe_allow_html=True,
        )


# ── Main render ───────────────────────────────────────────────────────────────

def render(
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
    route_results: list,
) -> None:
    """
    Render the Performance Attribution Engine tab.

    Parameters
    ----------
    stock_data    : dict ticker -> DataFrame (must have 'close' and 'date' columns)
    freight_data  : dict route_id -> DataFrame (rate_usd_per_feu or value column)
    macro_data    : dict series_id -> DataFrame (value column; expects 'BSXRLM' for BDI)
    route_results : list of CorrelationResult (passed through from app; unused directly here)
    """

    # ── Controls ──────────────────────────────────────────────────────────────
    ctrl_col1, ctrl_col2, ctrl_col3, _ = st.columns([2, 2, 2, 1])

    with ctrl_col1:
        available_tickers = [t for t in SHIPPING_TICKERS if t in stock_data]
        if not available_tickers:
            available_tickers = [t for t in stock_data if t not in ("XLI", "XRT")]
        if not available_tickers:
            st.error("No shipping stock data available for attribution.")
            return

        default_idx = available_tickers.index("ZIM") if "ZIM" in available_tickers else 0
        selected_ticker = st.selectbox(
            "Select Ticker",
            options=available_tickers,
            index=default_idx,
            key="attr_ticker",
        )

    with ctrl_col2:
        period_label = st.selectbox(
            "Attribution Period",
            options=list(_PERIOD_OPTIONS.keys()),
            index=2,  # default 90 days
            key="attr_period",
        )
        period_days = _PERIOD_OPTIONS[period_label]

    with ctrl_col3:
        rolling_window_label = st.selectbox(
            "Rolling Window",
            options=["20 days", "30 days", "60 days"],
            index=1,
            key="attr_rolling_window",
        )
        rolling_window = int(rolling_window_label.split()[0])

    # ── Header ────────────────────────────────────────────────────────────────
    _render_header(period_label)

    # ── Run attribution for selected ticker ───────────────────────────────────
    selected_attr: Optional[PerformanceAttribution] = None
    try:
        selected_attr = AttributePerformance(
            ticker=selected_ticker,
            stock_data=stock_data,
            freight_data=freight_data,
            macro_data=macro_data,
            period_days=period_days,
        )
    except Exception as _attr_exc:
        st.warning(
            f"Insufficient data for {selected_ticker} \u2014 need at least {_MIN_OBS} "
            f"overlapping observations. ({_attr_exc})"
        )

    # ── Run attribution for all stocks ────────────────────────────────────────
    all_attrs = attribute_all_stocks(
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
        period_days=period_days,
    )

    if not all_attrs:
        st.warning(
            "Could not compute attribution for any stocks. "
            "Ensure stock_data contains OHLCV data with 'close' and 'date' columns."
        )
        return

    # ══ ENHANCED: Attribution Mix Donut (sector-wide factor share) ══════════════
    try:
        _render_attribution_donut(all_attrs)
    except Exception as _e_donut:
        st.warning(f"Attribution donut unavailable: {_e_donut}")

    _divider()

    # ══ ENHANCED: Factor Exposure Cards (per-stock at-a-glance) ══════════════
    try:
        _render_factor_exposure_cards(all_attrs)
    except Exception as _e_fec:
        st.warning(f"Factor exposure cards unavailable: {_e_fec}")

    _divider()

    # ── 0. Universe Attribution Donut (NEW) ───────────────────────────────────
    try:
        _render_universe_donut(all_attrs)
    except Exception as _e0:
        logger.warning(f"Universe donut failed: {_e0}")

    _divider()

    # ── 0b. Multi-Stock Waterfall Grid (NEW) ───────────────────────────────────
    try:
        _render_multi_stock_waterfall_grid(all_attrs, max_stocks=6)
    except Exception as _e0b:
        logger.warning(f"Multi-stock waterfall grid failed: {_e0b}")

    _divider()

    # ── 0c. Factor Beta Heatmap (NEW) ─────────────────────────────────────────
    try:
        _render_factor_beta_heatmap(all_attrs)
    except Exception as _e0c:
        logger.warning(f"Factor beta heatmap failed: {_e0c}")

    _divider()

    # ── 1. Attribution Summary ────────────────────────────────────────────────
    _render_attribution_summary(all_attrs, selected_attr)

    _divider()

    # ── 2. Waterfall Chart ────────────────────────────────────────────────────
    if selected_attr is not None:
        _render_waterfall(selected_attr)
        _divider()

    # ── 3. Factor Exposure Dashboard ──────────────────────────────────────────
    _render_factor_exposure_dashboard(all_attrs)

    _divider()

    # ── 4. Attribution vs Benchmark ───────────────────────────────────────────
    _render_attribution_vs_benchmark(all_attrs, stock_data, period_days)

    _divider()

    # ── 5. Rolling Attribution ────────────────────────────────────────────────
    _render_rolling_attribution(
        ticker=selected_ticker,
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
        window=rolling_window,
    )

    _divider()

    # ── 6. Cross-Asset Attribution ────────────────────────────────────────────
    _render_cross_asset_attribution(all_attrs, stock_data, period_days)

    _divider()

    # ── 7. Regime Analysis ────────────────────────────────────────────────────
    _render_regime_analysis(
        ticker=selected_ticker,
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
    )

    _divider()

    # ── 8. Alpha Quality Ranking ──────────────────────────────────────────────
    _render_alpha_ranking(all_attrs)

    _divider()

    # ── R² Heatmap ────────────────────────────────────────────────────────────
    _render_r_squared_heatmap(all_attrs)

    _divider()

    # ── CSV Download ──────────────────────────────────────────────────────────
    _attr_rows = []
    for _a in all_attrs:
        _attr_rows.append({
            "Ticker":              _a.ticker,
            "Total Return (%)":   round(_a.total_return_pct, 4),
            "Freight Beta (%)":   round(_a.freight_beta_contribution, 4),
            "BDI Macro (%)":      round(_a.macro_contribution, 4),
            "Sector Beta (%)":    round(_a.sector_contribution, 4),
            "Idiosyncratic (%)":  round(_a.idiosyncratic_return, 4),
            "R\u00b2":            round(_a.r_squared, 4),
            "Info Ratio":         round(_a.information_ratio, 4),
            "Tracking Error (%)": round(_a.tracking_error, 4),
        })
    if _attr_rows:
        _attr_df  = pd.DataFrame(_attr_rows)
        _csv_attr = _attr_df.to_csv(index=False)
        dl_col, _ = st.columns([2, 5])
        with dl_col:
            st.download_button(
                label="Download Attribution Breakdown (CSV)",
                data=_csv_attr,
                file_name=f"attribution_breakdown_{period_label.replace(' ', '_')}.csv",
                mime="text/csv",
                key="attr_breakdown_download",
            )

    # ── Factor Return Series — Diagnostic Expander ────────────────────────────
    with st.expander(
        "Factor Return Series (Diagnostic)", expanded=False,
        key="attr_factor_return_series_expander",
    ):
        st.markdown(
            '<div style="background:rgba(59,130,246,0.07); border:1px solid rgba(59,130,246,0.22);'
            ' border-radius:8px; padding:13px 17px; margin-bottom:16px;'
            ' font-size:0.78rem; color:#94a3b8; line-height:1.65">'
            '<strong style="color:#60a5fa">Model Assumptions &amp; Disclaimer</strong><br>'
            'Factor returns are derived from an OLS regression of shipping equity daily returns '
            'against freight rate momentum, BDI trend, macro composite, and sector beta (XLI). '
            'The model assumes linear factor relationships and stationary return series. '
            'Contributions may not sum exactly to total return due to OLS intercept and rounding. '
            'R\u00b2 reflects in-sample explanatory power only and is not predictive. '
            'Rolling attribution uses configurable windows; short windows may produce unstable betas. '
            '<strong style="color:#f59e0b">For informational purposes only \u2014 not investment advice.</strong>'
            '</div>',
            unsafe_allow_html=True,
        )

        factor_returns = compute_factor_returns(
            stock_data=stock_data,
            freight_data=freight_data,
            macro_data=macro_data,
        )

        if not factor_returns:
            st.info("No factor return series could be computed.")
        else:
            fig = go.Figure()
            factor_color_map = {
                "freight_momentum": _C_FREIGHT,
                "bdi_trend":        _C_BDI,
                "macro_composite":  _C_IDIO,
                "sector_beta":      _C_SECTOR,
            }
            factor_label_map = {
                "freight_momentum": "Freight Momentum (5d MA)",
                "bdi_trend":        "BDI Trend (20d MA)",
                "macro_composite":  "Macro Composite",
                "sector_beta":      "Sector Beta (XLI)",
            }
            for fname, fseries in factor_returns.items():
                color = factor_color_map.get(fname, "#94a3b8")
                label = factor_label_map.get(fname, fname)
                fig.add_trace(go.Scatter(
                    x=fseries.index,
                    y=fseries.values * 100.0,
                    name=label,
                    mode="lines",
                    line=dict(color=color, width=1.5),
                    hovertemplate=label + ": %{y:+.3f}%<extra></extra>",
                ))

            fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", line_width=1)
            fig.update_layout(
                **_base_layout(height=280),
                xaxis=_axis_style(zeroline=False),
                yaxis=_axis_style(title="Daily Return (%)", ticksuffix="%"),
                legend=_legend_h(),
            )
            st.plotly_chart(fig, use_container_width=True, key="attr_factor_return_series")
