"""ui/tab_indices.py — Shipping Index Tracking and Comparison tab.

Renders a comprehensive shipping index intelligence dashboard with:
  1. Hero KPI metric row (quick-scan stats)
  2. Index dashboard cards with sparklines and 52-week context
  3. Multi-index normalized comparison chart (100 = base)
  4. BDI component breakdown (Capesize / Panamax / Supramax / Handysize proxies)
  5. FBX route breakdown (individual lane rates)
  6. Index vs stock performance correlation
  7. Index seasonality (average monthly values)
  8. Momentum indicators (RSI-style + rate of change)
  9. Performance heatmap across time periods
 10. Cross-correlation heatmap
 11. Signal summary feed

Function signature: render(macro_data, freight_data, stock_data, lookback_days) -> None
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from processing.shipping_indices import (
    INDEX_METADATA,
    ShippingIndex,
    build_indices,
    get_index_correlation_matrix,
    _get_index_time_series,
)

# ── Design tokens ─────────────────────────────────────────────────────────────
_C_BG       = "#0a0f1a"
_C_SURFACE  = "#111827"
_C_CARD     = "#1a2235"
_C_CARD2    = "#151f30"
_C_BORDER   = "rgba(255,255,255,0.08)"
_C_BORDER2  = "rgba(255,255,255,0.04)"
_C_TEXT     = "#f1f5f9"
_C_TEXT2    = "#94a3b8"
_C_TEXT3    = "#64748b"
_C_BULL     = "#10b981"
_C_BEAR     = "#ef4444"
_C_SIDE     = "#94a3b8"
_C_ACCENT   = "#3b82f6"
_C_AMBER    = "#f59e0b"
_C_PURPLE   = "#8b5cf6"
_C_CYAN     = "#06b6d4"
_C_ORANGE   = "#f97316"

_TREND_COLORS  = {"BULL": _C_BULL, "BEAR": _C_BEAR, "SIDEWAYS": _C_SIDE}
_TREND_BORDER  = {"BULL": _C_BULL, "BEAR": _C_BEAR, "SIDEWAYS": "#334155"}
_TREND_BG      = {
    "BULL": "rgba(16,185,129,0.10)",
    "BEAR": "rgba(239,68,68,0.10)",
    "SIDEWAYS": "rgba(148,163,184,0.06)",
}

# Per-index consistent palette
_INDEX_COLORS: dict[str, str] = {
    "BDI":        _C_ACCENT,
    "FBX_GLOBAL": _C_BULL,
    "FBX01":      _C_AMBER,
    "FBX03":      _C_PURPLE,
    "FBX11":      _C_CYAN,
    "PPIACO":     _C_ORANGE,
}
_LINE_COLORS = list(_INDEX_COLORS.values())


# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_value(value: float | None, index_id: str) -> str:
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not np.isfinite(v) or v == 0.0:
        return "N/A"
    if index_id == "BDI":
        return f"{v:,.0f}"
    if index_id == "PPIACO":
        return f"{v:.1f}"
    return f"${v:,.0f}"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "N/A"
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return "N/A"
    if not np.isfinite(v):
        return "N/A"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _pct_color(pct: float | None) -> str:
    if pct is None:
        return _C_SIDE
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return _C_SIDE
    if not np.isfinite(v):
        return _C_SIDE
    if v > 0.5:
        return _C_BULL
    if v < -0.5:
        return _C_BEAR
    return _C_SIDE


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ── Layout helpers ─────────────────────────────────────────────────────────────

def _divider() -> None:
    st.markdown(
        "<hr style='border:none; border-top:1px solid rgba(255,255,255,0.07);"
        " margin:24px 0 20px'>",
        unsafe_allow_html=True,
    )


def _section_header(title: str, subtitle: str = "", icon: str = "") -> None:
    icon_html = (
        f'<span style="margin-right:8px; font-size:1.1rem">{icon}</span>'
        if icon else ""
    )
    sub_html = (
        f'<div style="color:{_C_TEXT3}; font-size:0.78rem; margin-top:3px;'
        f' font-weight:400">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin-bottom:16px">'
        f'<div style="font-size:1.0rem; font-weight:700; color:{_C_TEXT};'
        f' display:flex; align-items:center">'
        f'{icon_html}{title}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Sparkline builder ─────────────────────────────────────────────────────────

def _build_sparkline(
    series: pd.Series | None,
    color: str,
    lookback: int = 30,
) -> go.Figure:
    """Return a tiny area sparkline figure (no axes, no margin)."""
    fig = go.Figure()
    if series is not None and not series.empty:
        s = series.sort_index().tail(lookback).dropna()
        if len(s) >= 2:
            fig.add_trace(go.Scatter(
                x=s.index,
                y=s.values,
                mode="lines",
                line=dict(color=color, width=1.8),
                fill="tozeroy",
                fillcolor=color + "1a",
                hoverinfo="skip",
            ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        height=42,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=False,
    )
    return fig


# ── Section 1: Hero KPI row ───────────────────────────────────────────────────

def _render_hero_metrics(indices: list[ShippingIndex]) -> None:
    """Compact st.metric() strip — quick-scan top-line view."""
    primary = [i for i in indices if i.index_id in ("BDI", "FBX_GLOBAL", "FBX01", "FBX03")]
    if not primary:
        primary = indices[:4]

    cols = st.columns(len(primary))
    for col, idx in zip(cols, primary):
        d30 = _safe_float(idx.change_30d)
        delta_str = f"{d30:+.1f}% (30d)" if d30 is not None else None
        with col:
            st.metric(
                label=idx.name,
                value=_fmt_value(idx.current_value, idx.index_id),
                delta=delta_str,
                delta_color="normal",
            )


# ── Section 2: Index Dashboard Cards with Sparklines ─────────────────────────

def _render_index_cards(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
) -> None:
    _section_header(
        "Index Snapshot Dashboard",
        "Key global shipping benchmarks — current values, momentum, and 52-week range",
        "📊",
    )

    # Fetch time series once for sparklines
    ts_cache: dict[str, pd.Series | None] = {}
    for idx in indices:
        try:
            ts_cache[idx.index_id] = _get_index_time_series(
                idx.index_id, macro_data, freight_data
            )
        except Exception:
            ts_cache[idx.index_id] = None

    n_cols = 3
    for row_start in range(0, len(indices), n_cols):
        row_items = indices[row_start : row_start + n_cols]
        cols = st.columns(n_cols)
        for col_i, (col, idx) in enumerate(zip(cols, row_items)):
            with col:
                color       = _INDEX_COLORS.get(idx.index_id, _C_ACCENT)
                trend_color = _TREND_COLORS.get(idx.trend, _C_SIDE)
                border_top  = _TREND_BORDER.get(idx.trend, "#334155")
                badge_bg    = _TREND_BG.get(idx.trend, "rgba(148,163,184,0.08)")

                val_str  = _fmt_value(idx.current_value, idx.index_id)
                d1_str   = _fmt_pct(idx.change_1d)
                d7_str   = _fmt_pct(idx.change_7d)
                d1_color = _pct_color(idx.change_1d)
                d7_color = _pct_color(idx.change_7d)

                # 52-week range bar
                hi   = _safe_float(idx.yoy_52w_high)
                lo   = _safe_float(idx.yoy_52w_low)
                cur  = _safe_float(idx.current_value)
                rng  = (hi - lo) if (hi and lo and hi != lo) else None
                pos  = ((cur - lo) / rng * 100.0) if (rng and cur is not None and lo is not None) else 50.0
                pos  = max(0.0, min(100.0, pos))

                # From 52w high badge
                from_hi   = _safe_float(idx.pct_from_52w_high)
                from_hi_s = _fmt_pct(from_hi)
                from_hi_c = _pct_color(from_hi)

                hi_str = _fmt_value(idx.yoy_52w_high, idx.index_id)
                lo_str = _fmt_value(idx.yoy_52w_low, idx.index_id)

                st.markdown(
                    f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
                    f' border-top:3px solid {border_top}; border-radius:12px;'
                    f' padding:16px 18px; min-height:220px">'

                    # Header row: id + name + trend badge
                    f'<div style="display:flex; justify-content:space-between;'
                    f' align-items:flex-start; margin-bottom:8px">'
                    f'<div>'
                    f'<div style="font-size:0.68rem; font-weight:700; color:{_C_TEXT3};'
                    f' text-transform:uppercase; letter-spacing:0.07em">'
                    f'{idx.index_id}</div>'
                    f'<div style="font-size:0.82rem; font-weight:600; color:{_C_TEXT2};'
                    f' margin-top:1px">{idx.name}</div>'
                    f'</div>'
                    f'<span style="background:{badge_bg}; color:{trend_color};'
                    f' padding:2px 7px; border-radius:999px; font-size:0.6rem;'
                    f' font-weight:800; letter-spacing:0.06em">'
                    f'{"▲" if idx.trend == "BULL" else "▼" if idx.trend == "BEAR" else "—"}'
                    f' {idx.trend}</span>'
                    f'</div>'

                    # Large current value
                    f'<div style="font-size:1.65rem; font-weight:800; color:{_C_TEXT};'
                    f' letter-spacing:-0.02em; line-height:1; margin-bottom:5px">'
                    f'{val_str}</div>'

                    # Change row
                    f'<div style="font-size:0.72rem; margin-bottom:10px;'
                    f' display:flex; gap:12px">'
                    f'<span style="color:{d1_color}; font-weight:600">1d: {d1_str}</span>'
                    f'<span style="color:{d7_color}; font-weight:600">7d: {d7_str}</span>'
                    f'<span style="color:{_C_TEXT3}">|</span>'
                    f'<span style="color:{from_hi_c}; font-weight:500">'
                    f'{from_hi_s} vs 52w Hi</span>'
                    f'</div>'

                    # 52-week range bar with glowing dot
                    f'<div style="margin-bottom:6px">'
                    f'<div style="background:rgba(255,255,255,0.06); border-radius:4px;'
                    f' height:5px; position:relative">'
                    f'<div style="position:absolute; left:{pos:.1f}%; top:-2px;'
                    f' width:9px; height:9px; border-radius:50%;'
                    f' background:{color}; transform:translateX(-50%);'
                    f' box-shadow:0 0 6px {color}88"></div>'
                    f'</div>'
                    f'</div>'
                    f'<div style="display:flex; justify-content:space-between;'
                    f' font-size:0.62rem; color:{_C_TEXT3}">'
                    f'<span>L: {lo_str}</span>'
                    f'<span>52-Week Range</span>'
                    f'<span>H: {hi_str}</span>'
                    f'</div>'

                    f'</div>',
                    unsafe_allow_html=True,
                )

                # Sparkline rendered below the card
                spark_key = f"spark_{idx.index_id}_{row_start}_{col_i}"
                fig = _build_sparkline(ts_cache.get(idx.index_id), color, lookback=30)
                st.plotly_chart(
                    fig,
                    use_container_width=True,
                    config={"displayModeBar": False},
                    key=spark_key,
                )


# ── Section 3: Multi-Index Normalized Comparison Chart ───────────────────────

def _render_comparison_chart(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
    lookback_days: int = 90,
) -> None:
    _section_header(
        "Multi-Index Normalized Comparison",
        "All indices rebased to 100 at the start of the lookback window — divergence reveals relative outperformance",
        "📈",
    )

    all_ids = [idx.index_id for idx in indices]
    selected = st.multiselect(
        "Select indices",
        options=all_ids,
        default=all_ids,
        format_func=lambda iid: INDEX_METADATA.get(iid, {}).get("name", iid),
        key="indices_comparison_select",
    )

    if not selected:
        st.info("Select at least one index to display the comparison chart.")
        return

    fig = go.Figure()
    has_data    = False
    unavailable: list[str] = []
    ts_export: dict[str, pd.Series] = {}

    for i, index_id in enumerate(selected):
        try:
            series = _get_index_time_series(index_id, macro_data, freight_data)
        except Exception as exc:
            logger.warning("Index %s fetch error: %s", index_id, exc)
            series = None

        label = INDEX_METADATA.get(index_id, {}).get("name", index_id)
        color = _INDEX_COLORS.get(index_id, _LINE_COLORS[i % len(_LINE_COLORS)])

        if series is None or series.empty:
            unavailable.append(label)
            continue

        series = series.sort_index().dropna()
        cutoff  = series.index.max() - pd.Timedelta(days=lookback_days)
        sliced  = series[series.index >= cutoff]

        if sliced.empty or len(sliced) < 2:
            unavailable.append(label)
            continue

        base = float(sliced.iloc[0])
        if base == 0 or not np.isfinite(base):
            unavailable.append(label)
            continue

        normalized = (sliced / base * 100.0).replace([np.inf, -np.inf], np.nan)
        current_norm = _safe_float(normalized.iloc[-1])
        delta_txt = (
            f"{current_norm - 100:+.1f} pts"
            if current_norm is not None else ""
        )

        fig.add_trace(go.Scatter(
            x=normalized.index,
            y=normalized.values,
            name=f"{label} ({delta_txt})",
            mode="lines",
            line=dict(color=color, width=2.2),
            connectgaps=False,
            hovertemplate=f"<b>{label}</b><br>%{{x|%b %d, %Y}}<br>Index: %{{y:.1f}}<extra></extra>",
        ))
        has_data = True
        ts_export[label] = normalized.rename(label)

    # Baseline reference line
    fig.add_hline(
        y=100,
        line_dash="dot",
        line_color="rgba(255,255,255,0.18)",
        line_width=1.2,
        annotation_text="Base (100)",
        annotation_font=dict(color=_C_TEXT3, size=10),
        annotation_position="left",
    )

    if unavailable:
        st.info("No data in window for: " + ", ".join(f"**{n}**" for n in unavailable))

    if not has_data:
        st.warning("No time-series data available for the selected indices.")
        return

    fig.update_layout(
        template="plotly_dark",
        height=420,
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(l=48, r=24, t=48, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            font=dict(size=10, color=_C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            tickfont=dict(size=10, color=_C_TEXT2),
        ),
        yaxis=dict(
            title=dict(text="Index (base = 100)", font=dict(size=10, color=_C_TEXT3)),
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            tickfont=dict(size=10, color=_C_TEXT2),
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="indices_comparison_chart")

    if ts_export:
        ts_df = pd.concat(ts_export.values(), axis=1).sort_index()
        ts_df.index.name = "Date"
        st.download_button(
            label="Download normalized time-series CSV",
            data=ts_df.reset_index().to_csv(index=False),
            file_name="shipping_indices_normalized.csv",
            mime="text/csv",
            key="dl_normalized_ts_csv",
        )


# ── Section 4: BDI Component Breakdown ───────────────────────────────────────

_BDI_COMPONENTS = {
    "Capesize":  {"weight": 0.40, "color": "#3b82f6", "desc": "180k+ DWT bulk carriers, iron ore & coal"},
    "Panamax":   {"weight": 0.30, "color": "#10b981", "desc": "65–80k DWT, grain & coal"},
    "Supramax":  {"weight": 0.20, "color": "#f59e0b", "desc": "50–60k DWT, minor bulks"},
    "Handysize": {"weight": 0.10, "color": "#8b5cf6", "desc": "28–40k DWT, regional trades"},
}

# Approximate historical volatility ratios vs BDI (Capesize highest beta)
_BDI_BETA: dict[str, float] = {
    "Capesize":  1.55,
    "Panamax":   0.90,
    "Supramax":  0.75,
    "Handysize": 0.55,
}


def _render_bdi_breakdown(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
    lookback_days: int,
) -> None:
    _section_header(
        "BDI Component Breakdown",
        "Capesize, Panamax, Supramax, and Handysize sub-index estimates derived from the Baltic Dry composite",
        "⚓",
    )

    bdi = next((i for i in indices if i.index_id == "BDI"), None)
    if bdi is None or _safe_float(bdi.current_value) in (None, 0.0):
        st.info("BDI data unavailable — component breakdown cannot be estimated.")
        return

    bdi_val    = float(bdi.current_value)
    bdi_series = _get_index_time_series("BDI", macro_data, freight_data)
    comp_names = list(_BDI_COMPONENTS.keys())

    # ── Component summary cards ──────────────────────────────────────────
    comp_cols = st.columns(len(comp_names))
    for col, name in zip(comp_cols, comp_names):
        meta   = _BDI_COMPONENTS[name]
        weight = meta["weight"]
        contribution = bdi_val * weight
        share_pct    = weight * 100

        with col:
            st.markdown(
                f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
                f' border-top:3px solid {meta["color"]}; border-radius:10px;'
                f' padding:14px 16px; text-align:center">'
                f'<div style="font-size:0.65rem; font-weight:700; color:{_C_TEXT3};'
                f' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:5px">'
                f'{name}</div>'
                f'<div style="font-size:1.4rem; font-weight:800; color:{_C_TEXT};'
                f' line-height:1.1">{contribution:,.0f}</div>'
                f'<div style="font-size:0.65rem; color:{_C_TEXT3}; margin-top:3px">'
                f'{share_pct:.0f}% weight</div>'
                f'<div style="font-size:0.6rem; color:{_C_TEXT3}; margin-top:6px;'
                f' font-style:italic; line-height:1.35">{meta["desc"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Stacked area chart showing synthetic sub-index contributions ─────
    if bdi_series is not None and not bdi_series.empty:
        cutoff = bdi_series.index.max() - pd.Timedelta(days=lookback_days)
        sliced = bdi_series[bdi_series.index >= cutoff].dropna()

        if len(sliced) >= 4:
            fig = go.Figure()
            for name in comp_names:
                meta         = _BDI_COMPONENTS[name]
                contribution_series = sliced * meta["weight"]
                fig.add_trace(go.Scatter(
                    x=sliced.index,
                    y=contribution_series.values,
                    name=name,
                    mode="lines",
                    line=dict(color=meta["color"], width=1.5),
                    stackgroup="bdi",
                    hovertemplate=f"<b>{name}</b><br>%{{x|%b %d}}<br>Contribution: %{{y:.0f}} pts<extra></extra>",
                ))

            fig.update_layout(
                template="plotly_dark",
                height=280,
                paper_bgcolor=_C_CARD,
                plot_bgcolor=_C_CARD,
                margin=dict(l=48, r=20, t=16, b=36),
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.01,
                    xanchor="center", x=0.5,
                    font=dict(size=10, color=_C_TEXT2),
                    bgcolor="rgba(0,0,0,0)",
                ),
                font=dict(family="Inter, sans-serif"),
                xaxis=dict(gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                           tickfont=dict(size=9, color=_C_TEXT2)),
                yaxis=dict(title=dict(text="BDI Points", font=dict(size=9, color=_C_TEXT3)),
                           gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                           tickfont=dict(size=9, color=_C_TEXT2)),
                hoverlabel=dict(bgcolor="#1a2235",
                                bordercolor="rgba(255,255,255,0.15)",
                                font=dict(color=_C_TEXT, size=11)),
            )
            st.plotly_chart(fig, use_container_width=True, key="bdi_component_area")


# ── Section 5: FBX Route Breakdown ───────────────────────────────────────────

_FBX_ROUTE_META: dict[str, dict] = {
    "FBX01": {
        "label": "Trans-Pacific EB",
        "detail": "Asia to US West Coast",
        "color": _C_AMBER,
        "route_key": "transpacific_eb",
    },
    "FBX03": {
        "label": "Asia-Europe",
        "detail": "Shanghai to Rotterdam",
        "color": _C_PURPLE,
        "route_key": "asia_europe",
    },
    "FBX11": {
        "label": "Transatlantic EB",
        "detail": "US East Coast to Europe",
        "color": _C_CYAN,
        "route_key": "transatlantic",
    },
    "FBX_GLOBAL": {
        "label": "FBX Global",
        "detail": "Composite — all lanes",
        "color": _C_BULL,
        "route_key": "global",
    },
}


def _render_fbx_breakdown(
    indices: list[ShippingIndex],
    freight_data: dict,
    lookback_days: int,
) -> None:
    _section_header(
        "FBX Container Rate Route Breakdown",
        "Individual Freightos Baltic Index lane rates — spot USD per FEU",
        "🚢",
    )

    fbx_indices = [i for i in indices if i.index_id in _FBX_ROUTE_META]
    if not fbx_indices:
        st.info("No FBX route data available.")
        return

    # ── Summary bar chart (current rates by lane) ─────────────────────────
    names  = [_FBX_ROUTE_META[i.index_id]["label"]  for i in fbx_indices]
    values = [_safe_float(i.current_value) or 0.0    for i in fbx_indices]
    colors = [_FBX_ROUTE_META[i.index_id]["color"]   for i in fbx_indices]

    fig_bar = go.Figure(go.Bar(
        x=names,
        y=values,
        marker=dict(
            color=colors,
            opacity=0.85,
            line=dict(color=[c + "88" for c in colors], width=1),
        ),
        text=[f"${v:,.0f}" for v in values],
        textposition="outside",
        textfont=dict(size=11, color=_C_TEXT2),
        hovertemplate="<b>%{x}</b><br>Rate: $%{y:,.0f}/FEU<extra></extra>",
    ))
    fig_bar.update_layout(
        template="plotly_dark",
        height=260,
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(l=20, r=20, t=16, b=40),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(tickfont=dict(size=11, color=_C_TEXT2), gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(title=dict(text="USD / FEU", font=dict(size=9, color=_C_TEXT3)),
                   gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                   tickfont=dict(size=9, color=_C_TEXT2)),
        bargap=0.4,
        hoverlabel=dict(bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=_C_TEXT, size=12)),
    )
    st.plotly_chart(fig_bar, use_container_width=True,
                    config={"displayModeBar": False}, key="fbx_bar_chart")

    # ── Time-series line chart of all FBX routes ──────────────────────────
    fig_ts = go.Figure()
    for idx in fbx_indices:
        rmeta     = _FBX_ROUTE_META[idx.index_id]
        route_key = rmeta["route_key"]
        df = freight_data.get(route_key)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            continue
        if "rate_usd_per_feu" not in df.columns:
            continue
        df = df.copy()
        if "date" in df.columns:
            df = df.sort_values("date")
            df["date"] = pd.to_datetime(df["date"])
            cutoff = df["date"].max() - pd.Timedelta(days=lookback_days)
            df = df[df["date"] >= cutoff]
        rates = df["rate_usd_per_feu"].dropna()
        if rates.empty or len(rates) < 2:
            continue
        dates = df.loc[rates.index, "date"] if "date" in df.columns else rates.index

        fig_ts.add_trace(go.Scatter(
            x=list(dates),
            y=rates.values,
            name=rmeta["label"],
            mode="lines",
            line=dict(color=rmeta["color"], width=2),
            hovertemplate=(
                f"<b>{rmeta['label']}</b><br>%{{x|%b %d}}<br>"
                f"$%{{y:,.0f}}/FEU<extra></extra>"
            ),
        ))

    if fig_ts.data:
        fig_ts.update_layout(
            template="plotly_dark",
            height=320,
            paper_bgcolor=_C_CARD,
            plot_bgcolor=_C_CARD,
            margin=dict(l=56, r=20, t=16, b=40),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.01,
                xanchor="center", x=0.5,
                font=dict(size=10, color=_C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
            ),
            font=dict(family="Inter, sans-serif"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                       tickfont=dict(size=10, color=_C_TEXT2)),
            yaxis=dict(title=dict(text="USD / FEU", font=dict(size=9, color=_C_TEXT3)),
                       gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                       tickfont=dict(size=10, color=_C_TEXT2)),
            hoverlabel=dict(bgcolor="#1a2235",
                            bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig_ts, use_container_width=True, key="fbx_route_timeseries")


# ── Section 6: Index vs Stock Performance ─────────────────────────────────────

_STOCK_TICKERS = {
    "ZIM":  {"label": "ZIM Integrated Shipping", "color": "#ef4444"},
    "MATX": {"label": "Matson Inc.",              "color": "#f97316"},
    "SBLK": {"label": "Star Bulk Carriers",       "color": "#f59e0b"},
    "GOGL": {"label": "Golden Ocean Group",       "color": "#10b981"},
    "DSGX": {"label": "Descartes Systems",        "color": "#3b82f6"},
    "EXPD": {"label": "Expeditors Intl.",         "color": "#8b5cf6"},
}


def _render_index_vs_stocks(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
    stock_data: dict | None,
    lookback_days: int,
) -> None:
    _section_header(
        "Index vs. Shipping Stock Performance",
        "Normalized returns — BDI and FBX plotted alongside major shipping equities",
        "📉",
    )

    fig     = go.Figure()
    has_any = False

    # BDI as the benchmark line
    bdi_series = _get_index_time_series("BDI", macro_data, freight_data)
    if bdi_series is not None and not bdi_series.empty:
        s = bdi_series.sort_index().dropna()
        cutoff = s.index.max() - pd.Timedelta(days=lookback_days)
        s = s[s.index >= cutoff]
        if len(s) >= 2 and float(s.iloc[0]) != 0:
            norm = s / float(s.iloc[0]) * 100.0
            fig.add_trace(go.Scatter(
                x=norm.index, y=norm.values,
                name="BDI (Index)",
                mode="lines",
                line=dict(color=_C_ACCENT, width=2.5, dash="solid"),
                hovertemplate="<b>BDI</b><br>%{x|%b %d}<br>%{y:.1f}<extra></extra>",
            ))
            has_any = True

    # Overlay stocks if provided
    if stock_data and isinstance(stock_data, dict):
        for ticker, smeta in _STOCK_TICKERS.items():
            df = stock_data.get(ticker)
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue
            price_col = next(
                (c for c in ("close", "Close", "price", "adj_close") if c in df.columns),
                None,
            )
            if price_col is None:
                continue
            df = df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date")
                cutoff = df["date"].max() - pd.Timedelta(days=lookback_days)
                df = df[df["date"] >= cutoff]
            prices = df[price_col].dropna()
            if prices.empty or len(prices) < 2 or float(prices.iloc[0]) == 0:
                continue
            dates = df.loc[prices.index, "date"] if "date" in df.columns else prices.index
            norm  = prices / float(prices.iloc[0]) * 100.0
            fig.add_trace(go.Scatter(
                x=list(dates), y=norm.values,
                name=f"{ticker} — {smeta['label']}",
                mode="lines",
                line=dict(color=smeta["color"], width=1.6),
                opacity=0.80,
                hovertemplate=f"<b>{ticker}</b><br>%{{x|%b %d}}<br>%{{y:.1f}}<extra></extra>",
            ))
            has_any = True

    fig.add_hline(
        y=100, line_dash="dot",
        line_color="rgba(255,255,255,0.18)", line_width=1,
    )

    if not has_any:
        st.info(
            "No stock or index data available for this chart. "
            "Provide stock data via the `stock_data` dict with ticker symbols as keys."
        )
        return

    fig.update_layout(
        template="plotly_dark",
        height=400,
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(l=48, r=20, t=16, b=40),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01,
            xanchor="center", x=0.5,
            font=dict(size=9, color=_C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                   tickfont=dict(size=10, color=_C_TEXT2)),
        yaxis=dict(title=dict(text="Return Index (base = 100)", font=dict(size=9, color=_C_TEXT3)),
                   gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                   tickfont=dict(size=10, color=_C_TEXT2)),
        hoverlabel=dict(bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=_C_TEXT, size=12)),
    )
    st.plotly_chart(fig, use_container_width=True, key="index_vs_stocks_chart")


# ── Section 7: Seasonality Chart ─────────────────────────────────────────────

_MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _render_seasonality(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
) -> None:
    _section_header(
        "Index Seasonality",
        "Average monthly values across all available history — reveals recurring seasonal patterns",
        "📅",
    )

    index_ids = [idx.index_id for idx in indices]
    sel = st.selectbox(
        "Select index for seasonality",
        options=index_ids,
        format_func=lambda iid: INDEX_METADATA.get(iid, {}).get("name", iid),
        key="seasonality_index_select",
    )
    if not sel:
        return

    try:
        series = _get_index_time_series(sel, macro_data, freight_data)
    except Exception as exc:
        st.warning(f"Could not load data for seasonality: {exc}")
        return

    if series is None or series.empty or len(series) < 24:
        st.info("Insufficient history for seasonality analysis (need at least 24 data points).")
        return

    series  = series.sort_index().dropna()
    df_s    = series.to_frame("value")
    df_s["month"] = df_s.index.month

    monthly = df_s.groupby("month")["value"].agg(["mean", "std", "min", "max"]).reset_index()
    monthly.columns = ["month", "mean", "std", "low", "high"]
    monthly["label"] = monthly["month"].apply(lambda m: _MONTH_LABELS[m - 1])

    color = _INDEX_COLORS.get(sel, _C_ACCENT)

    fig = go.Figure()

    # Historical min/max shaded band
    fig.add_trace(go.Scatter(
        x=monthly["label"].tolist() + monthly["label"].tolist()[::-1],
        y=monthly["high"].tolist() + monthly["low"].tolist()[::-1],
        fill="toself",
        fillcolor=color + "22",
        line=dict(color="rgba(0,0,0,0)"),
        name="Historical Range",
        hoverinfo="skip",
    ))

    # ±1 std band
    fig.add_trace(go.Scatter(
        x=monthly["label"].tolist() + monthly["label"].tolist()[::-1],
        y=(monthly["mean"] + monthly["std"]).tolist()
          + (monthly["mean"] - monthly["std"]).tolist()[::-1],
        fill="toself",
        fillcolor=color + "33",
        line=dict(color="rgba(0,0,0,0)"),
        name="±1 Std Dev",
        hoverinfo="skip",
    ))

    # Mean line
    fig.add_trace(go.Scatter(
        x=monthly["label"],
        y=monthly["mean"],
        name="Monthly Average",
        mode="lines+markers",
        line=dict(color=color, width=2.2),
        marker=dict(size=6, color=color, line=dict(color=_C_CARD, width=1.5)),
        hovertemplate="<b>%{x}</b><br>Avg: %{y:,.1f}<extra></extra>",
    ))

    fig.update_layout(
        template="plotly_dark",
        height=340,
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(l=56, r=20, t=16, b=40),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01,
            xanchor="center", x=0.5,
            font=dict(size=10, color=_C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                   tickfont=dict(size=11, color=_C_TEXT2)),
        yaxis=dict(gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                   tickfont=dict(size=10, color=_C_TEXT2)),
        hoverlabel=dict(bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=_C_TEXT, size=12)),
    )
    st.plotly_chart(fig, use_container_width=True, key="seasonality_chart")


# ── Section 8: Momentum Indicators ───────────────────────────────────────────

def _compute_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_roc(series: pd.Series, window: int = 10) -> pd.Series:
    """Rate of Change (%)."""
    return series.pct_change(window) * 100.0


def _render_momentum(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
    lookback_days: int,
) -> None:
    _section_header(
        "Momentum Indicators",
        "RSI (14-period) and Rate-of-Change (10-period) — identify overbought/oversold conditions",
        "⚡",
    )

    index_ids = [idx.index_id for idx in indices]
    sel = st.selectbox(
        "Select index for momentum analysis",
        options=index_ids,
        format_func=lambda iid: INDEX_METADATA.get(iid, {}).get("name", iid),
        key="momentum_index_select",
    )
    if not sel:
        return

    try:
        series = _get_index_time_series(sel, macro_data, freight_data)
    except Exception as exc:
        st.warning(f"Could not load momentum data: {exc}")
        return

    if series is None or series.empty or len(series) < 20:
        st.info("Insufficient data for momentum indicators (need at least 20 data points).")
        return

    series  = series.sort_index().dropna()
    cutoff  = series.index.max() - pd.Timedelta(days=max(lookback_days, 90))
    sliced  = series[series.index >= cutoff]
    if len(sliced) < 20:
        sliced = series.tail(60)

    rsi   = _compute_rsi(sliced, window=14)
    roc   = _compute_roc(sliced, window=10)
    color = _INDEX_COLORS.get(sel, _C_ACCENT)

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.45, 0.27, 0.28],
        vertical_spacing=0.05,
        subplot_titles=["Price", "RSI (14)", "Rate of Change (10)"],
    )

    # Price panel
    fig.add_trace(go.Scatter(
        x=sliced.index, y=sliced.values,
        name=INDEX_METADATA.get(sel, {}).get("name", sel),
        mode="lines",
        line=dict(color=color, width=2),
        hovertemplate="%{x|%b %d}<br>%{y:,.1f}<extra></extra>",
    ), row=1, col=1)

    # RSI panel
    rsi_clean = rsi.dropna()
    if not rsi_clean.empty:
        rsi_color = [
            _C_BEAR if v > 70 else _C_BULL if v < 30 else _C_ACCENT
            for v in rsi_clean.values
        ]
        fig.add_trace(go.Bar(
            x=rsi_clean.index, y=rsi_clean.values,
            name="RSI",
            marker=dict(color=rsi_color, opacity=0.75),
            hovertemplate="RSI: %{y:.1f}<extra></extra>",
        ), row=2, col=1)
        for level, lcolor in [(70, _C_BEAR), (30, _C_BULL), (50, _C_TEXT3)]:
            fig.add_hline(
                y=level, row=2, col=1,
                line_dash="dot", line_color=lcolor + "88", line_width=1,
            )

    # ROC panel
    roc_clean = roc.dropna()
    if not roc_clean.empty:
        roc_colors = [_C_BULL if v >= 0 else _C_BEAR for v in roc_clean.values]
        fig.add_trace(go.Bar(
            x=roc_clean.index, y=roc_clean.values,
            name="ROC (10)",
            marker=dict(color=roc_colors, opacity=0.75),
            hovertemplate="ROC: %{y:.1f}%<extra></extra>",
        ), row=3, col=1)
        fig.add_hline(
            y=0, row=3, col=1,
            line_dash="solid", line_color="rgba(255,255,255,0.12)", line_width=1,
        )

    fig.update_layout(
        template="plotly_dark",
        height=520,
        paper_bgcolor=_C_CARD,
        plot_bgcolor=_C_CARD,
        margin=dict(l=56, r=20, t=36, b=40),
        showlegend=False,
        font=dict(family="Inter, sans-serif"),
        hoverlabel=dict(bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=_C_TEXT, size=11)),
    )
    for row_i in range(1, 4):
        fig.update_xaxes(gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                         tickfont=dict(size=9, color=_C_TEXT2), row=row_i, col=1)
        fig.update_yaxes(gridcolor="rgba(255,255,255,0.04)", zeroline=False,
                         tickfont=dict(size=9, color=_C_TEXT2), row=row_i, col=1)
    for ann in fig.layout.annotations:
        ann.font.color = _C_TEXT3
        ann.font.size  = 10

    st.plotly_chart(fig, use_container_width=True, key="momentum_chart")

    # ── Momentum summary cards ────────────────────────────────────────────
    rsi_now = _safe_float(rsi.dropna().iloc[-1]) if not rsi.dropna().empty else None
    roc_now = _safe_float(roc.dropna().iloc[-1]) if not roc.dropna().empty else None

    if rsi_now is not None or roc_now is not None:
        m_cols = st.columns(4)
        with m_cols[0]:
            if rsi_now is not None:
                label  = "Overbought" if rsi_now > 70 else "Oversold" if rsi_now < 30 else "Neutral"
                lcolor = _C_BEAR if rsi_now > 70 else _C_BULL if rsi_now < 30 else _C_TEXT3
                st.markdown(
                    f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
                    f' border-radius:10px; padding:12px 16px; text-align:center">'
                    f'<div style="font-size:0.65rem; color:{_C_TEXT3}; font-weight:700;'
                    f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:5px">'
                    f'RSI (14)</div>'
                    f'<div style="font-size:1.5rem; font-weight:800; color:{lcolor}">'
                    f'{rsi_now:.1f}</div>'
                    f'<div style="font-size:0.7rem; color:{lcolor}; margin-top:3px">'
                    f'{label}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        with m_cols[1]:
            if roc_now is not None:
                roc_c = _C_BULL if roc_now >= 0 else _C_BEAR
                st.markdown(
                    f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
                    f' border-radius:10px; padding:12px 16px; text-align:center">'
                    f'<div style="font-size:0.65rem; color:{_C_TEXT3}; font-weight:700;'
                    f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:5px">'
                    f'Rate of Change (10)</div>'
                    f'<div style="font-size:1.5rem; font-weight:800; color:{roc_c}">'
                    f'{roc_now:+.1f}%</div>'
                    f'<div style="font-size:0.7rem; color:{roc_c}; margin-top:3px">'
                    f'{"Accelerating" if roc_now >= 0 else "Decelerating"}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


# ── Section 9: Historical Context (52-week range bars) ────────────────────────

def _render_historical_context(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
) -> None:
    _section_header(
        "Historical Context — 52-Week Range",
        "Current value vs. 52-week high, low, and average — gauge where each index stands in its annual cycle",
        "📆",
    )

    for idx in indices:
        try:
            series = _get_index_time_series(idx.index_id, macro_data, freight_data)
        except Exception:
            series = None

        cur = _safe_float(idx.current_value) or 0.0
        hi  = _safe_float(idx.yoy_52w_high)
        lo  = _safe_float(idx.yoy_52w_low)

        if series is not None and not series.empty:
            cutoff  = series.index.max() - pd.Timedelta(weeks=52)
            s52     = series[series.index >= cutoff].dropna()
            avg_52w = float(s52.mean()) if not s52.empty else None
        else:
            avg_52w = None

        color = _INDEX_COLORS.get(idx.index_id, _C_ACCENT)
        rng   = (hi - lo) if (hi and lo and hi != lo) else None
        pos   = ((cur - lo) / rng * 100.0) if (rng and lo is not None) else 50.0
        pos   = max(0.0, min(100.0, pos))
        avg_pos = ((avg_52w - lo) / rng * 100.0) if (rng and avg_52w and lo is not None) else None

        val_str  = _fmt_value(cur, idx.index_id)
        hi_str   = _fmt_value(hi, idx.index_id)
        lo_str   = _fmt_value(lo, idx.index_id)
        avg_str  = _fmt_value(avg_52w, idx.index_id) if avg_52w else "N/A"
        from_hi  = _fmt_pct(idx.pct_from_52w_high)
        from_hi_c = _pct_color(idx.pct_from_52w_high)

        avg_marker = ""
        if avg_pos is not None:
            avg_marker = (
                f'<div style="position:absolute; left:{avg_pos:.1f}%; top:-3px;'
                f' width:2px; height:16px; background:{_C_AMBER}88;'
                f' transform:translateX(-50%)"></div>'
            )

        st.markdown(
            f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
            f' border-radius:10px; padding:14px 18px; margin-bottom:8px">'

            # Header
            f'<div style="display:flex; justify-content:space-between;'
            f' align-items:center; margin-bottom:10px">'
            f'<div>'
            f'<span style="font-size:0.7rem; font-weight:700; color:{_C_TEXT3};'
            f' text-transform:uppercase; letter-spacing:0.07em">{idx.index_id}</span>'
            f'<span style="font-size:0.82rem; color:{_C_TEXT2}; margin-left:8px">{idx.name}</span>'
            f'</div>'
            f'<div style="display:flex; align-items:center; gap:16px">'
            f'<span style="font-size:1.1rem; font-weight:800; color:{_C_TEXT}">{val_str}</span>'
            f'<span style="font-size:0.72rem; color:{from_hi_c}; font-weight:600">{from_hi} vs Hi</span>'
            f'</div>'
            f'</div>'

            # Range bar with glowing position dot
            f'<div style="position:relative; background:rgba(255,255,255,0.06);'
            f' border-radius:6px; height:10px; margin-bottom:8px">'
            f'<div style="position:absolute; left:0; width:{pos:.1f}%; height:100%;'
            f' background:linear-gradient(90deg, {color}44, {color});'
            f' border-radius:6px"></div>'
            f'<div style="position:absolute; left:{pos:.1f}%; top:-3px;'
            f' width:16px; height:16px; border-radius:50%;'
            f' background:{color}; border:2px solid {_C_CARD};'
            f' transform:translateX(-50%);'
            f' box-shadow:0 0 8px {color}88"></div>'
            f'{avg_marker}'
            f'</div>'

            # Labels
            f'<div style="display:flex; justify-content:space-between;'
            f' font-size:0.62rem; color:{_C_TEXT3}">'
            f'<span>52w L: <span style="color:{_C_TEXT2}">{lo_str}</span></span>'
            f'<span style="color:{_C_AMBER}88">Avg: {avg_str}</span>'
            f'<span>52w H: <span style="color:{_C_TEXT2}">{hi_str}</span></span>'
            f'</div>'

            f'</div>',
            unsafe_allow_html=True,
        )


# ── Section 10: Performance Heatmap ──────────────────────────────────────────

def _render_performance_heatmap(indices: list[ShippingIndex]) -> None:
    _section_header(
        "Performance Heatmap",
        "Color-coded returns across 1d / 7d / 30d / YTD — green = up, red = down",
        "🌡️",
    )

    periods     = ["1d", "7d", "30d", "YTD"]
    index_names = [idx.name for idx in indices]

    z_vals: list[list[float]] = []
    text_vals: list[list[str]] = []

    for idx in indices:
        raw_vals    = [idx.change_1d, idx.change_7d, idx.change_30d, idx.change_ytd]
        period_vals = [
            float(v) if (v is not None and np.isfinite(float(v))) else 0.0
            for v in raw_vals
        ]
        z_vals.append(period_vals)
        text_vals.append([_fmt_pct(v) for v in raw_vals])

    z_arr       = np.array(z_vals, dtype=float)
    finite_vals = z_arr[np.isfinite(z_arr)]
    z_extreme   = max(
        abs(finite_vals.min()) if len(finite_vals) else 0,
        abs(finite_vals.max()) if len(finite_vals) else 0,
        5.0,
    )

    rdgn = [
        [0.0,  "#b91c1c"],
        [0.25, "#ef4444"],
        [0.45, "#94a3b8"],
        [0.5,  "#64748b"],
        [0.55, "#94a3b8"],
        [0.75, "#10b981"],
        [1.0,  "#059669"],
    ]

    fig = go.Figure(go.Heatmap(
        z=z_arr,
        x=periods,
        y=index_names,
        colorscale=rdgn,
        zmid=0,
        zmin=-z_extreme,
        zmax=z_extreme,
        text=text_vals,
        texttemplate="%{text}",
        textfont=dict(size=12, color="#f1f5f9"),
        hovertemplate="<b>%{y}</b><br>%{x}: %{text}<extra></extra>",
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
        margin=dict(l=40, r=80, t=40, b=40),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(tickfont=dict(size=12, color=_C_TEXT2), side="top"),
        yaxis=dict(tickfont=dict(size=11, color=_C_TEXT2), autorange="reversed"),
        hoverlabel=dict(bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=_C_TEXT, size=12)),
    )
    st.plotly_chart(
        fig, use_container_width=True,
        config={"displayModeBar": False},
        key="indices_performance_heatmap",
    )


# ── Section 11: Cross-Correlation Heatmap ─────────────────────────────────────

def _render_correlation_heatmap(
    indices: list[ShippingIndex],
    macro_data: dict,
    freight_data: dict,
) -> None:
    _section_header(
        "Index Cross-Correlation Matrix",
        "Pairwise Pearson r between all index time series — 1.0 = perfect positive, -1.0 = inverse",
        "🔗",
    )

    corr_df = get_index_correlation_matrix(indices, macro_data, freight_data)
    if corr_df.empty:
        st.info(
            "Insufficient overlapping data to compute correlations. "
            "At least 5 common observations per pair are required."
        )
        return

    labels      = [INDEX_METADATA.get(c, {}).get("name", c) for c in corr_df.columns]
    z_arr       = corr_df.values
    text_matrix = [[f"{v:.2f}" for v in row] for row in z_arr]

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
        hovertemplate="<b>%{x}</b> × <b>%{y}</b><br>r = %{z:.3f}<extra></extra>",
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
        margin=dict(l=40, r=80, t=40, b=80),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(tickfont=dict(size=10, color=_C_TEXT2), side="top", tickangle=30),
        yaxis=dict(tickfont=dict(size=10, color=_C_TEXT2), autorange="reversed"),
        hoverlabel=dict(bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=_C_TEXT, size=12)),
    )
    st.plotly_chart(
        fig, use_container_width=True,
        config={"displayModeBar": False},
        key="indices_correlation_heatmap",
    )


# ── Section 12: Signal Summary Feed ──────────────────────────────────────────

_BULL_INSIGHTS: dict[str, str] = {
    "BDI": (
        "Baltic Dry Index is trending higher, signaling rising demand for dry bulk "
        "commodities and an improving global trade outlook. Positive for bulk carrier stocks."
    ),
    "FBX_GLOBAL": (
        "Global container freight rates are rising, pointing to tighter capacity "
        "and potential peak-season congestion pressures. Watch for contract-vs-spot spread widening."
    ),
    "FBX01": (
        "Trans-Pacific eastbound rates are surging, reflecting strong US import "
        "demand from Asia. Possible front-loading or inventory build cycle under way."
    ),
    "FBX03": (
        "Asia-Europe rates are climbing — likely driven by Suez Canal disruption rerouting "
        "via Cape of Good Hope, adding 10–14 days and consuming effective capacity."
    ),
    "FBX11": (
        "Transatlantic eastbound rates are rising, suggesting robust US export "
        "volumes and tightening capacity on North Atlantic lanes."
    ),
    "PPIACO": (
        "Producer Price Index is advancing — building input cost pressures that may "
        "translate into higher fuel surcharges and GRIs across container lanes."
    ),
}

_BEAR_INSIGHTS: dict[str, str] = {
    "BDI": (
        "Baltic Dry Index is declining, a bearish signal for global commodity "
        "trade volumes and dry bulk demand. Capesize and Panamax charter rates likely under pressure."
    ),
    "FBX_GLOBAL": (
        "Global container freight rates are falling — softer demand or oversupply of "
        "vessel capacity across major lanes. Spot rates may undercut long-term contracts."
    ),
    "FBX01": (
        "Trans-Pacific eastbound rates are weakening — softening US import demand "
        "or a build-up of idle vessel capacity in Asian load ports."
    ),
    "FBX03": (
        "Asia-Europe rates are sliding — easing capacity constraints or a reduction "
        "in European import appetite. Rerouting via Suez may resume if safe."
    ),
    "FBX11": (
        "Transatlantic eastbound rates are declining — weaker US export flows "
        "or excess tonnage on the North Atlantic corridor."
    ),
    "PPIACO": (
        "Producer Price Index is softening — easing input cost pressures that "
        "could reduce shipping demand from manufacturing sectors."
    ),
}


def _render_signal_feed(indices: list[ShippingIndex]) -> None:
    _section_header(
        "Index Signal Feed",
        "Market insight for all indices with active BULL or BEAR trends",
        "📡",
    )

    active = [idx for idx in indices if idx.trend in ("BULL", "BEAR")]

    if not active:
        st.markdown(
            f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
            f' border-radius:10px; padding:20px; text-align:center">'
            f'<div style="font-size:0.9rem; color:{_C_TEXT2}">'
            f'All indices are currently in a SIDEWAYS trend. No directional signals active.'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        return

    for idx in active:
        color  = _C_BULL if idx.trend == "BULL" else _C_BEAR
        bg_clr = _TREND_BG.get(idx.trend, "rgba(148,163,184,0.06)")
        icon   = "▲" if idx.trend == "BULL" else "▼"

        insight_text = (
            _BULL_INSIGHTS if idx.trend == "BULL" else _BEAR_INSIGHTS
        ).get(
            idx.index_id,
            f"{idx.name} is in a {idx.trend.lower()} trend.",
        )

        val_str = _fmt_value(idx.current_value, idx.index_id)
        d7_str  = _fmt_pct(idx.change_7d)
        d30_str = _fmt_pct(idx.change_30d)

        st.markdown(
            f'<div style="background:{bg_clr}; border:1px solid rgba(255,255,255,0.06);'
            f' border-left:3px solid {color};'
            f' border-radius:10px; padding:14px 18px; margin-bottom:8px">'

            f'<div style="display:flex; align-items:center; gap:10px; margin-bottom:6px">'
            f'<span style="font-size:0.68rem; font-weight:800; letter-spacing:0.06em;'
            f' text-transform:uppercase; color:{color}; white-space:nowrap">'
            f'{icon} {idx.trend}</span>'
            f'<span style="font-size:0.82rem; font-weight:700; color:{_C_TEXT}">'
            f'{idx.name}</span>'
            f'<span style="font-size:0.7rem; color:{_C_TEXT3}; margin-left:auto;'
            f' font-family:monospace; white-space:nowrap">'
            f'{val_str} &nbsp;·&nbsp; '
            f'7d: <span style="color:{_pct_color(idx.change_7d)}">{d7_str}</span>'
            f'&nbsp;·&nbsp;'
            f'30d: <span style="color:{_pct_color(idx.change_30d)}">{d30_str}</span>'
            f'</span>'
            f'</div>'

            f'<div style="font-size:0.8rem; color:{_C_TEXT2}; line-height:1.6">'
            f'{insight_text}'
            f'</div>'

            f'</div>',
            unsafe_allow_html=True,
        )


# ── CSV Export ────────────────────────────────────────────────────────────────

def _render_data_export(indices: list[ShippingIndex]) -> None:
    def _sr(v, n=2):
        if v is None:
            return None
        try:
            f = float(v)
            return round(f, n) if np.isfinite(f) else None
        except (TypeError, ValueError):
            return None

    df = pd.DataFrame([
        {
            "Index":          idx.name,
            "Index ID":       idx.index_id,
            "Current Value":  idx.current_value,
            "Trend":          idx.trend,
            "1d Change (%)":  _sr(idx.change_1d),
            "7d Change (%)":  _sr(idx.change_7d),
            "30d Change (%)": _sr(idx.change_30d),
            "YTD Change (%)": _sr(idx.change_ytd),
            "52w High":       idx.yoy_52w_high,
            "52w Low":        idx.yoy_52w_low,
            "% From 52w Hi":  _sr(idx.pct_from_52w_high),
            "Source":         idx.source,
            "Last Updated":   idx.last_updated,
        }
        for idx in indices
    ])
    st.download_button(
        label="Download index snapshot CSV",
        data=df.to_csv(index=False),
        file_name="shipping_indices_snapshot.csv",
        mime="text/csv",
        key="dl_indices_snapshot_csv",
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
        macro_data:    FRED series dict (series_id -> DataFrame with date/value columns).
        freight_data:  Freight scraper dict (route_id -> DataFrame with rate_usd_per_feu).
        stock_data:    Optional stock data dict (ticker -> DataFrame with date/close columns).
        lookback_days: Lookback window in days for time-series charts.
    """
    try:
        indices = build_indices(macro_data or {}, freight_data or {})
    except Exception as exc:
        logger.error("Failed to build shipping indices: %s", exc)
        st.error(f"Could not load shipping index data: {exc}")
        return

    if not indices:
        st.warning("No shipping index data is available.")
        return

    _macro   = macro_data   or {}
    _freight = freight_data or {}
    _stocks  = stock_data   or {}

    # ── Hero KPI strip ────────────────────────────────────────────────────
    _render_hero_metrics(indices)
    _divider()

    # ── 1. Index Dashboard Cards with Sparklines ──────────────────────────
    try:
        _render_index_cards(indices, _macro, _freight)
    except Exception as exc:
        logger.warning("Index cards error: %s", exc)
        st.warning("Index dashboard cards unavailable.")
    _divider()

    # ── 2. Multi-Index Normalized Comparison ─────────────────────────────
    try:
        _render_comparison_chart(indices, _macro, _freight, lookback_days)
    except Exception as exc:
        logger.warning("Comparison chart error: %s", exc)
        st.warning("Comparison chart unavailable.")
    _divider()

    # ── 3 & 4: BDI breakdown + FBX routes side by side ───────────────────
    left_col, right_col = st.columns([1, 1], gap="large")
    with left_col:
        try:
            _render_bdi_breakdown(indices, _macro, _freight, lookback_days)
        except Exception as exc:
            logger.warning("BDI breakdown error: %s", exc)
            st.warning("BDI component breakdown unavailable.")
    with right_col:
        try:
            _render_fbx_breakdown(indices, _freight, lookback_days)
        except Exception as exc:
            logger.warning("FBX breakdown error: %s", exc)
            st.warning("FBX route breakdown unavailable.")
    _divider()

    # ── 5. Index vs Stocks ────────────────────────────────────────────────
    try:
        _render_index_vs_stocks(indices, _macro, _freight, _stocks, lookback_days)
    except Exception as exc:
        logger.warning("Index vs stocks error: %s", exc)
        st.warning("Index vs stock performance chart unavailable.")
    _divider()

    # ── 6 & 7: Seasonality + Momentum in tabs ────────────────────────────
    tab_season, tab_momentum = st.tabs(["Seasonality", "Momentum"])
    with tab_season:
        try:
            _render_seasonality(indices, _macro, _freight)
        except Exception as exc:
            logger.warning("Seasonality error: %s", exc)
            st.warning("Seasonality chart unavailable.")
    with tab_momentum:
        try:
            _render_momentum(indices, _macro, _freight, lookback_days)
        except Exception as exc:
            logger.warning("Momentum error: %s", exc)
            st.warning("Momentum indicators unavailable.")
    _divider()

    # ── 8. Historical Context (52w range bars) ────────────────────────────
    try:
        _render_historical_context(indices, _macro, _freight)
    except Exception as exc:
        logger.warning("Historical context error: %s", exc)
        st.warning("Historical context unavailable.")
    _divider()

    # ── 9. Performance Heatmap ────────────────────────────────────────────
    try:
        _render_performance_heatmap(indices)
    except Exception as exc:
        logger.warning("Performance heatmap error: %s", exc)
        st.warning("Performance heatmap unavailable.")
    _divider()

    # ── 10. Cross-Correlation Heatmap ─────────────────────────────────────
    try:
        _render_correlation_heatmap(indices, _macro, _freight)
    except Exception as exc:
        logger.warning("Correlation heatmap error: %s", exc)
        st.warning("Correlation heatmap unavailable.")
    _divider()

    # ── 11. Signal Feed ───────────────────────────────────────────────────
    try:
        _render_signal_feed(indices)
    except Exception as exc:
        logger.warning("Signal feed error: %s", exc)
        st.warning("Signal feed unavailable.")
    _divider()

    # ── Data export ───────────────────────────────────────────────────────
    _render_data_export(indices)
