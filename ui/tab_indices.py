"""ui/tab_indices.py — Shipping Index Tracking and Comparison tab.

Renders a comprehensive shipping index dashboard with:
  1. Index card grid (2x3)
  2. Performance heatmap across time periods
  3. Normalized multi-line comparison chart
  4. Cross-correlation heatmap
  5. Trend signal summary feed

Integration (add to app.py tabs):
    from ui.tab_indices import render as render_indices
    with tab_indices:
        render_indices(macro_data, freight_data, stock_data)
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.shipping_indices import (
    INDEX_METADATA,
    ShippingIndex,
    build_indices,
    get_index_correlation_matrix,
    _get_index_time_series,
)

# ── Local color constants ─────────────────────────────────────────────────────
_C_BG     = "#0a0f1a"
_C_CARD   = "#1a2235"
_C_BORDER = "rgba(255,255,255,0.08)"
_C_TEXT   = "#f1f5f9"
_C_TEXT2  = "#94a3b8"
_C_TEXT3  = "#64748b"
_C_BULL   = "#10b981"
_C_BEAR   = "#ef4444"
_C_SIDE   = "#94a3b8"
_C_ACCENT = "#3b82f6"

_TREND_COLORS = {
    "BULL": _C_BULL,
    "BEAR": _C_BEAR,
    "SIDEWAYS": _C_SIDE,
}
_TREND_BORDER = {
    "BULL": _C_BULL,
    "BEAR": _C_BEAR,
    "SIDEWAYS": "#334155",
}

# Chart line colors (one per index)
_LINE_COLORS = [
    "#3b82f6",  # blue    — BDI
    "#10b981",  # green   — FBX Global
    "#f59e0b",  # amber   — FBX01
    "#8b5cf6",  # purple  — FBX03
    "#06b6d4",  # cyan    — FBX11
    "#f97316",  # orange  — PPIACO
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_value(value: float, index_id: str) -> str:
    """Format a value for display based on index type."""
    if value == 0.0:
        return "N/A"
    if index_id in ("BDI",):
        return f"{value:,.0f}"
    if index_id == "PPIACO":
        return f"{value:.1f}"
    # FBX indices — USD per FEU
    return f"${value:,.0f}"


def _fmt_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _pct_color(pct: float) -> str:
    if pct > 0.5:
        return _C_BULL
    if pct < -0.5:
        return _C_BEAR
    return _C_SIDE


def _section_title(title: str, subtitle: str = "") -> None:
    sub_html = (
        f'<div style="color:{_C_TEXT2}; font-size:0.83rem; margin-top:3px">'
        f'{subtitle}</div>'
        if subtitle
        else ""
    )
    st.markdown(
        f'<div style="margin-bottom:14px; margin-top:4px">'
        f'<div style="font-size:1.05rem; font-weight:700; color:{_C_TEXT}">'
        f'{title}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Section 1: Index Card Grid ────────────────────────────────────────────────

def _render_index_cards(indices: list[ShippingIndex]) -> None:
    _section_title(
        "Shipping Index Dashboard",
        "Live snapshots of key global shipping and freight benchmarks",
    )

    # Pad to 6 for a clean 2x3 grid
    display = indices[:6]
    while len(display) < 6:
        display = list(display) + [None]

    rows = [display[:3], display[3:6]]
    for row_items in rows:
        cols = st.columns(3)
        for col, idx in zip(cols, row_items):
            with col:
                if idx is None:
                    st.markdown(
                        f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
                        f' border-radius:12px; padding:18px 20px; min-height:160px"></div>',
                        unsafe_allow_html=True,
                    )
                    continue

                trend_color = _TREND_COLORS.get(idx.trend, _C_SIDE)
                border_top  = _TREND_BORDER.get(idx.trend, "#334155")
                val_str     = _fmt_value(idx.current_value, idx.index_id)
                d1_str      = _fmt_pct(idx.change_1d)
                d1_color    = _pct_color(idx.change_1d)
                d30_str     = _fmt_pct(idx.change_30d)
                d30_color   = _pct_color(idx.change_30d)

                # 30-day change bar (clamped to +/-30%)
                bar_pct  = min(abs(idx.change_30d), 30.0) / 30.0 * 100.0
                bar_color = _C_BULL if idx.change_30d >= 0 else _C_BEAR

                badge_bg  = (
                    "rgba(16,185,129,0.15)" if idx.trend == "BULL"
                    else "rgba(239,68,68,0.15)" if idx.trend == "BEAR"
                    else "rgba(148,163,184,0.10)"
                )

                high_str = _fmt_value(idx.yoy_52w_high, idx.index_id)
                low_str  = _fmt_value(idx.yoy_52w_low, idx.index_id)
                from_hi  = _fmt_pct(idx.pct_from_52w_high)

                st.markdown(
                    f'<div style="'
                    f'background:{_C_CARD};'
                    f' border:1px solid {_C_BORDER};'
                    f' border-top:3px solid {border_top};'
                    f' border-radius:12px;'
                    f' padding:18px 20px;'
                    f' margin-bottom:0;'
                    f' min-height:190px">'

                    # Row 1: name + trend badge
                    f'<div style="display:flex; justify-content:space-between;'
                    f' align-items:flex-start; margin-bottom:10px">'
                    f'<div style="font-size:0.78rem; font-weight:700; color:{_C_TEXT3};'
                    f' text-transform:uppercase; letter-spacing:0.07em; line-height:1.3">'
                    f'{idx.name}'
                    f'</div>'
                    f'<span style="background:{badge_bg}; color:{trend_color};'
                    f' padding:2px 8px; border-radius:999px; font-size:0.65rem;'
                    f' font-weight:700; letter-spacing:0.04em; white-space:nowrap">'
                    f'{idx.trend}'
                    f'</span>'
                    f'</div>'

                    # Row 2: current value (large)
                    f'<div style="font-size:1.55rem; font-weight:800; color:{_C_TEXT};'
                    f' letter-spacing:-0.02em; line-height:1.1; margin-bottom:6px">'
                    f'{val_str}'
                    f'</div>'

                    # Row 3: 1d change
                    f'<div style="font-size:0.8rem; font-weight:600; color:{d1_color};'
                    f' margin-bottom:10px">'
                    f'1d: {d1_str}'
                    f'<span style="color:{_C_TEXT3}; font-weight:400; margin-left:10px">'
                    f'30d: <span style="color:{d30_color}">{d30_str}</span>'
                    f'</span>'
                    f'</div>'

                    # Row 4: 30-day change bar
                    f'<div style="background:rgba(255,255,255,0.06); border-radius:3px;'
                    f' height:3px; margin-bottom:10px">'
                    f'<div style="width:{bar_pct:.1f}%; height:100%;'
                    f' background:{bar_color}; border-radius:3px"></div>'
                    f'</div>'

                    # Row 5: 52w hi/lo
                    f'<div style="font-size:0.68rem; color:{_C_TEXT3};'
                    f' display:flex; justify-content:space-between">'
                    f'<span>52w H: {high_str}</span>'
                    f'<span>L: {low_str}</span>'
                    f'<span style="color:{_pct_color(idx.pct_from_52w_high)}">'
                    f'{from_hi} vs Hi</span>'
                    f'</div>'

                    f'</div>',
                    unsafe_allow_html=True,
                )


# ── Section 2: Performance Heatmap ────────────────────────────────────────────

def _render_performance_heatmap(indices: list[ShippingIndex]) -> None:
    _section_title(
        "Index Performance Heatmap",
        "Color-coded returns across time periods — green=up, red=down",
    )

    periods  = ["1d", "7d", "30d", "YTD"]
    index_names = [idx.name for idx in indices]

    # Build z matrix: rows=indices, cols=periods
    z_vals: list[list[float]] = []
    text_vals: list[list[str]] = []

    for idx in indices:
        period_vals = [idx.change_1d, idx.change_7d, idx.change_30d, idx.change_ytd]
        z_vals.append(period_vals)
        text_vals.append([_fmt_pct(v) for v in period_vals])

    z_arr = np.array(z_vals)

    # RdGn colorscale centered at 0
    rdgn_colorscale = [
        [0.0,  "#b91c1c"],
        [0.25, "#ef4444"],
        [0.45, "#94a3b8"],
        [0.5,  "#64748b"],
        [0.55, "#94a3b8"],
        [0.75, "#10b981"],
        [1.0,  "#059669"],
    ]

    z_extreme = max(abs(z_arr.min()), abs(z_arr.max()), 5.0)

    fig = go.Figure(go.Heatmap(
        z=z_arr,
        x=periods,
        y=index_names,
        colorscale=rdgn_colorscale,
        zmid=0,
        zmin=-z_extreme,
        zmax=z_extreme,
        text=text_vals,
        texttemplate="%{text}",
        textfont=dict(size=12, color="#f1f5f9"),
        hovertemplate="Index: %{y}<br>Period: %{x}<br>Change: %{text}<extra></extra>",
        colorbar=dict(
            tickfont=dict(color=_C_TEXT2, size=10),
            outlinewidth=0,
            thickness=12,
            title=dict(text="%", font=dict(color=_C_TEXT3, size=10)),
        ),
    ))
    fig.update_layout(
        template="plotly_dark",
        height=max(280, len(indices) * 48 + 80),
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(t=16, b=20, l=10, r=80),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(
            tickfont=dict(size=12, color=_C_TEXT2),
            side="top",
        ),
        yaxis=dict(
            tickfont=dict(size=11, color=_C_TEXT2),
            autorange="reversed",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Section 3: Normalized Comparison Chart ────────────────────────────────────

def _render_comparison_chart(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
    lookback_days: int = 90,
) -> None:
    _section_title(
        "Index Comparison Chart",
        "All indices normalized to 100 at lookback start",
    )

    all_ids = [idx.index_id for idx in indices]
    selected = st.multiselect(
        "Select indices to display",
        options=all_ids,
        default=all_ids,
        format_func=lambda iid: INDEX_METADATA.get(iid, {}).get("name", iid),
        key="indices_comparison_select",
    )

    if not selected:
        st.info("Select at least one index to display the comparison chart.")
        return

    fig = go.Figure()
    has_data = False

    for i, index_id in enumerate(selected):
        series = _get_index_time_series(index_id, macro_data, freight_data)
        if series is None or series.empty:
            continue

        series = series.sort_index().dropna()
        # Slice to lookback window
        cutoff = series.index.max() - pd.Timedelta(days=lookback_days)
        sliced = series[series.index >= cutoff]
        if sliced.empty or len(sliced) < 2:
            continue

        base = float(sliced.iloc[0])
        if base == 0:
            continue

        normalized = sliced / base * 100.0
        color = _LINE_COLORS[i % len(_LINE_COLORS)]
        label = INDEX_METADATA.get(index_id, {}).get("name", index_id)

        fig.add_trace(go.Scatter(
            x=normalized.index,
            y=normalized.values,
            name=label,
            mode="lines",
            line=dict(color=color, width=2),
            hovertemplate=f"<b>{label}</b>: %{{y:.1f}}<extra></extra>",
        ))
        has_data = True

    fig.add_hline(
        y=100,
        line_dash="dot",
        line_color="rgba(255,255,255,0.2)",
        line_width=1,
    )

    if not has_data:
        st.warning("No time-series data available for the selected indices.")
        return

    fig.update_layout(
        template="plotly_dark",
        height=400,
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            font=dict(size=11),
            bgcolor="rgba(0,0,0,0)",
        ),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)", zeroline=False),
        yaxis=dict(
            title="Index (base=100)",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section 4: Cross-Correlation Heatmap ─────────────────────────────────────

def _render_correlation_heatmap(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
) -> None:
    _section_title(
        "Index Cross-Correlation",
        "Pairwise Pearson r between all index time series",
    )

    corr_df = get_index_correlation_matrix(indices, macro_data, freight_data)

    if corr_df.empty:
        st.info(
            "Insufficient overlapping data to compute correlations. "
            "More historical data is needed (min 5 common data points per pair)."
        )
        return

    # Build display labels
    labels = [INDEX_METADATA.get(c, {}).get("name", c) for c in corr_df.columns]
    z_arr  = corr_df.values

    # Annotate cells with r values
    text_matrix: list[list[str]] = []
    for row in z_arr:
        text_matrix.append([f"{v:.2f}" for v in row])

    fig = go.Figure(go.Heatmap(
        z=z_arr,
        x=labels,
        y=labels,
        colorscale="RdBu_r",
        zmid=0,
        zmin=-1,
        zmax=1,
        text=text_matrix,
        texttemplate="%{text}",
        textfont=dict(size=11, color="#f1f5f9"),
        hovertemplate="X: %{x}<br>Y: %{y}<br>r = %{z:.3f}<extra></extra>",
        colorbar=dict(
            tickfont=dict(color=_C_TEXT2, size=10),
            outlinewidth=0,
            thickness=12,
            tickvals=[-1, -0.5, 0, 0.5, 1],
            ticktext=["-1.0", "-0.5", "0", "0.5", "1.0"],
        ),
    ))
    fig.update_layout(
        template="plotly_dark",
        height=max(350, len(corr_df) * 55 + 80),
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(t=20, b=80, l=10, r=80),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(
            tickfont=dict(size=10, color=_C_TEXT2),
            side="top",
            tickangle=30,
        ),
        yaxis=dict(
            tickfont=dict(size=10, color=_C_TEXT2),
            autorange="reversed",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Section 5: Signal Summary Feed ───────────────────────────────────────────

_BULL_INSIGHTS: dict[str, str] = {
    "BDI": (
        "Baltic Dry Index is trending higher, signaling rising demand for dry bulk "
        "commodities and an improving global trade outlook."
    ),
    "FBX_GLOBAL": (
        "Global container freight rates are rising, pointing to tighter capacity "
        "and potential peak-season congestion pressures."
    ),
    "FBX01": (
        "Trans-Pacific eastbound rates are surging, reflecting strong US import "
        "demand from Asia and possible front-loading activity."
    ),
    "FBX03": (
        "Asia-Europe rates are climbing, likely driven by Suez Canal disruptions "
        "or strong European import demand pushing vessels via Cape of Good Hope."
    ),
    "FBX11": (
        "Transatlantic eastbound rates are rising, suggesting robust US export "
        "volumes and tightening capacity on North Atlantic lanes."
    ),
    "PPIACO": (
        "Producer Price Index is advancing, indicating building input cost "
        "pressures that may translate into higher shipping surcharges."
    ),
}

_BEAR_INSIGHTS: dict[str, str] = {
    "BDI": (
        "Baltic Dry Index is declining, a bearish signal for global commodity "
        "trade volumes and dry bulk demand in the near term."
    ),
    "FBX_GLOBAL": (
        "Global container freight rates are falling, pointing to softer demand "
        "or an oversupply of vessel capacity across major lanes."
    ),
    "FBX01": (
        "Trans-Pacific eastbound rates are weakening, which may signal softening "
        "US import demand or a build-up of idle vessel capacity in Asia."
    ),
    "FBX03": (
        "Asia-Europe rates are sliding, suggesting easing capacity constraints "
        "or a reduction in European import appetite for Asian goods."
    ),
    "FBX11": (
        "Transatlantic eastbound rates are declining, indicating weaker US export "
        "flows or excess tonnage on the North Atlantic corridor."
    ),
    "PPIACO": (
        "Producer Price Index is softening, signaling easing input cost pressures "
        "that could reduce shipping demand from manufacturing sectors."
    ),
}


def _render_signal_feed(indices: list[ShippingIndex]) -> None:
    _section_title(
        "Index Signal Summary",
        "Market insight for indices with active BULL or BEAR trends",
    )

    active = [idx for idx in indices if idx.trend in ("BULL", "BEAR")]

    if not active:
        st.markdown(
            f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
            f' border-radius:10px; padding:20px; text-align:center">'
            f'<div style="font-size:0.9rem; color:{_C_TEXT2}">'
            f'All indices are currently in SIDEWAYS trend. '
            f'No directional signals are active.'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        return

    for idx in active:
        color  = _C_BULL if idx.trend == "BULL" else _C_BEAR
        bg_clr = (
            "rgba(16,185,129,0.07)" if idx.trend == "BULL"
            else "rgba(239,68,68,0.07)"
        )
        icon   = "▲" if idx.trend == "BULL" else "▼"

        if idx.trend == "BULL":
            insight_text = _BULL_INSIGHTS.get(
                idx.index_id,
                f"{idx.name} is in a bullish trend, signaling strengthening market conditions.",
            )
        else:
            insight_text = _BEAR_INSIGHTS.get(
                idx.index_id,
                f"{idx.name} is in a bearish trend, signaling weakening market conditions.",
            )

        val_str = _fmt_value(idx.current_value, idx.index_id)
        d30_str = _fmt_pct(idx.change_30d)

        st.markdown(
            f'<div style="background:{bg_clr}; border:1px solid rgba(255,255,255,0.06);'
            f' border-left:3px solid {color};'
            f' border-radius:10px; padding:14px 18px; margin-bottom:8px">'

            f'<div style="display:flex; align-items:center; gap:10px; margin-bottom:6px">'
            f'<span style="font-size:0.7rem; font-weight:800; letter-spacing:0.06em;'
            f' text-transform:uppercase; color:{color}; white-space:nowrap">'
            f'{icon} {idx.trend}'
            f'</span>'
            f'<span style="font-size:0.82rem; font-weight:700; color:{_C_TEXT}">'
            f'{idx.name}'
            f'</span>'
            f'<span style="font-size:0.72rem; color:{_C_TEXT3}; margin-left:auto;'
            f' font-family:monospace; white-space:nowrap">'
            f'{val_str} &nbsp;|&nbsp; 30d: '
            f'<span style="color:{color}">{d30_str}</span>'
            f'</span>'
            f'</div>'

            f'<div style="font-size:0.82rem; color:{_C_TEXT2}; line-height:1.55">'
            f'{insight_text}'
            f'</div>'

            f'</div>',
            unsafe_allow_html=True,
        )


# ── Main render entry point ───────────────────────────────────────────────────

def render(
    macro_data: dict,
    freight_data: dict,
    stock_data: Optional[dict] = None,
    lookback_days: int = 90,
) -> None:
    """Render the full Shipping Indices tab.

    Args:
        macro_data:   FRED series dict (series_id -> DataFrame with date/value columns).
        freight_data: Freight scraper dict (route_id -> DataFrame with rate_usd_per_feu).
        stock_data:   Optional stock data dict (unused directly, reserved for future use).
        lookback_days: Lookback window in days for the comparison chart.
    """
    # ── Build indices ─────────────────────────────────────────────────────
    try:
        indices = build_indices(macro_data or {}, freight_data or {})
    except Exception as exc:
        logger.error("Failed to build shipping indices: %s", exc)
        st.error(f"Could not load shipping index data: {exc}")
        return

    if not indices:
        st.warning("No shipping index data is available.")
        return

    # ── Section 1: Card grid ──────────────────────────────────────────────
    _render_index_cards(indices)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 2: Performance heatmap ───────────────────────────────────
    _render_performance_heatmap(indices)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 3: Comparison chart ───────────────────────────────────────
    _render_comparison_chart(indices, macro_data or {}, freight_data or {}, lookback_days)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 4: Cross-correlation heatmap ─────────────────────────────
    _render_correlation_heatmap(indices, macro_data or {}, freight_data or {})

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 5: Signal feed ────────────────────────────────────────────
    _render_signal_feed(indices)


# ── Integration comment ───────────────────────────────────────────────────────
# To integrate into app.py, add a new tab and call:
#
#   tab_idx = st.tabs([..., "📊  Indices"])
#   with tab_idx:
#       from ui.tab_indices import render as render_indices
#       render_indices(macro_data, freight_data, stock_data, lookback_days=lookback)
