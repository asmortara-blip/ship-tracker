"""
Performance Attribution Engine — Streamlit Tab

Bloomberg terminal aesthetic. Decomposes shipping stock returns into:
  Freight Rate Beta | BDI Factor | Macro Factor | Idiosyncratic (alpha)

All chart colours follow the shared ui.styles design system.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
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


# ── Design constants ───────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider(label: str = "") -> None:
    inner = (
        '<span style="font-size:0.62rem; color:#334155; text-transform:uppercase;'
        ' letter-spacing:0.12em; padding:0 12px">'
        + label + "</span>"
        if label else ""
    )
    st.markdown(
        '<div style="display:flex; align-items:center; gap:0; margin:22px 0">'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        + inner +
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _bar_color(value: float) -> str:
    if value > 0.5:
        return _C_POS
    if value < -0.5:
        return _C_NEG
    return _C_NEUT


def _fmt_pct(value: float, decimals: int = 2) -> str:
    sign = "+" if value >= 0 else ""
    fmt = "{sign}{v:." + str(decimals) + "f}%"
    return fmt.format(sign=sign, v=value)


def _color_for_value(value: float) -> str:
    if value > 0.5:
        return _C_POS
    if value < -0.5:
        return _C_NEG
    return _C_NEUT


# ── Section: Bloomberg-style header ──────────────────────────────────────────

def _render_header(period_label: str) -> None:
    st.markdown(
        '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
        ' border-left:3px solid #3b82f6; border-radius:10px;'
        ' padding:16px 24px; margin-bottom:20px">'

        '<div style="display:flex; justify-content:space-between; align-items:center">'

        # left — title block
        '<div>'
        '<div style="font-size:0.62rem; font-weight:800; color:#3b82f6;'
        ' text-transform:uppercase; letter-spacing:0.14em; margin-bottom:4px">'
        "BLOOMBERG TERMINAL \u00b7 EQUITY ANALYTICS"
        "</div>"
        '<div style="font-size:1.4rem; font-weight:900; color:#f1f5f9;'
        ' letter-spacing:-0.01em">Performance Attribution Engine</div>'
        '<div style="font-size:0.82rem; color:#64748b; margin-top:3px">'
        "Factor decomposition for shipping equities "
        "\u00b7 OLS regression "
        "\u00b7 Freight \u00b7 BDI \u00b7 Macro \u00b7 Alpha"
        "</div>"
        "</div>"

        # right — period badge
        '<div style="text-align:right">'
        '<div style="font-size:0.62rem; color:#64748b; text-transform:uppercase;'
        ' letter-spacing:0.1em">Period</div>'
        '<div style="font-size:1.1rem; font-weight:800; color:#06b6d4">'
        + period_label +
        "</div>"
        "</div>"

        "</div></div>",
        unsafe_allow_html=True,
    )


# ── Section: Attribution Waterfall ────────────────────────────────────────────

def _render_waterfall(attr: PerformanceAttribution) -> None:
    section_header(
        "Attribution Waterfall",
        "Total return decomposed into factor contributions for " + attr.ticker,
    )

    factors = [
        ("Freight Rate Beta", attr.freight_beta_contribution),
        ("BDI / Macro Factor", attr.macro_contribution),
        ("Sector Beta (XLI)", attr.sector_contribution),
        ("Idiosyncratic", attr.idiosyncratic_return),
    ]

    measure_list = ["relative", "relative", "relative", "relative", "total"]
    x_list = [f[0] for f in factors] + ["Total"]
    y_list = [f[1] for f in factors] + [attr.total_return_pct]

    # Colour each bar independently
    bar_colors = []
    for val in y_list[:-1]:
        bar_colors.append(_C_POS if val >= 0 else _C_NEG)
    bar_colors.append(_C_TOTAL)

    connector_color = "rgba(255,255,255,0.12)"

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=measure_list,
        x=x_list,
        y=y_list,
        text=[_fmt_pct(v) for v in y_list],
        textposition="outside",
        textfont=dict(size=11, color="#f1f5f9", family="Inter, sans-serif"),
        increasing=dict(marker=dict(color=_C_POS)),
        decreasing=dict(marker=dict(color=_C_NEG)),
        totals=dict(marker=dict(color=_C_TOTAL)),
        connector=dict(line=dict(color=connector_color, width=1, dash="dot")),
        hovertemplate="%{x}: %{y:+.2f}%<extra></extra>",
    ))

    fig.update_layout(
        template="plotly_dark",
        height=300,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=40, b=10, l=10, r=10),
        font=dict(family="Inter, sans-serif", color="#94a3b8"),
        xaxis=dict(
            tickfont=dict(size=11, color="#94a3b8"),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        yaxis=dict(
            title="% Return",
            ticksuffix="%",
            tickfont=dict(size=10, color="#64748b"),
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.12)",
            zerolinewidth=1,
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section: Factor Exposure Table ────────────────────────────────────────────

def _render_factor_exposure_table(attributions: List[PerformanceAttribution]) -> None:
    section_header(
        "Factor Exposure Table",
        "Beta contribution to each factor per ticker \u00b7 colour intensity = magnitude",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

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
    for attr in attributions:
        total_color = _C_POS if attr.total_return_pct >= 0 else _C_NEG

        cells = (
            '<td style="padding:10px 14px; font-size:0.88rem; font-weight:800;'
            ' color:#f1f5f9; white-space:nowrap">'
            + attr.ticker + "</td>"
            '<td style="padding:10px 14px; font-size:0.82rem; font-weight:700; color:'
            + total_color + '; text-align:right">'
            + _fmt_pct(attr.total_return_pct) + "</td>"
        )

        for key in factor_keys:
            val = getattr(attr, key)
            if key == "r_squared":
                # show as percentage, no sign
                intensity = int(min(255, abs(val) * 255))
                cell_color = "rgba(" + str(intensity) + ",180,246,0.15)"
                text_color = "#60a5fa"
                display_val = str(round(val * 100, 1)) + "%"
            else:
                abs_val = abs(val)
                intensity = int(min(200, abs_val * 8))
                if val >= 0:
                    cell_color = "rgba(16," + str(min(185, 80 + intensity)) + ",129,0.15)"
                    text_color = _C_POS
                else:
                    cell_color = "rgba(239,68," + str(min(68, 20 + intensity)) + ",0.15)"
                    text_color = _C_NEG
                display_val = _fmt_pct(val)

            cells += (
                '<td style="padding:10px 14px; background:' + cell_color
                + '; font-size:0.82rem; font-weight:600; color:' + text_color
                + '; text-align:right; font-variant-numeric:tabular-nums">'
                + display_val + "</td>"
            )

        rows_html += "<tr>" + cells + "</tr>"

    header_cells = (
        '<th style="padding:8px 14px; font-size:0.65rem; font-weight:700;'
        ' color:#64748b; text-transform:uppercase; letter-spacing:0.08em;'
        ' text-align:left; white-space:nowrap">Ticker</th>'
        '<th style="padding:8px 14px; font-size:0.65rem; font-weight:700;'
        ' color:#64748b; text-transform:uppercase; letter-spacing:0.08em;'
        ' text-align:right; white-space:nowrap">Total Return</th>'
    )
    for key in factor_keys:
        header_cells += (
            '<th style="padding:8px 14px; font-size:0.65rem; font-weight:700;'
            ' color:#64748b; text-transform:uppercase; letter-spacing:0.08em;'
            ' text-align:right; white-space:nowrap">'
            + factor_display[key] + "</th>"
        )

    table_html = (
        '<div style="overflow-x:auto; border-radius:10px;'
        ' border:1px solid rgba(255,255,255,0.08); margin-bottom:8px">'
        '<table style="width:100%; border-collapse:collapse;'
        ' background:#0d1117; font-family:Inter, sans-serif">'
        "<thead>"
        '<tr style="border-bottom:1px solid rgba(255,255,255,0.08)">'
        + header_cells +
        "</tr></thead>"
        "<tbody>"
        + rows_html +
        "</tbody></table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


# ── Section: R-Squared Heatmap ────────────────────────────────────────────────

def _render_r_squared_heatmap(attributions: List[PerformanceAttribution]) -> None:
    section_header(
        "R\u00b2 Factor Heatmap",
        "Proportion of variance explained by each factor group (tickers \u00d7 factors)",
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
    tickers = [a.ticker for a in attributions]

    # Build pseudo R2 matrix: |contribution| / |total_return| capped at 1
    z_matrix = []
    text_matrix = []
    for attr in attributions:
        row_z = []
        row_t = []
        total = abs(attr.total_return_pct) if abs(attr.total_return_pct) > 0.01 else 1.0
        for key in factor_keys:
            val = abs(getattr(attr, key))
            frac = min(1.0, val / total)
            row_z.append(round(frac, 3))
            row_t.append(str(round(frac * 100, 1)) + "%")
        z_matrix.append(row_z)
        text_matrix.append(row_t)

    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=factor_labels,
        y=tickers,
        colorscale=[
            [0.0,  "rgba(13,17,23,1)"],
            [0.25, "rgba(30,58,138,0.6)"],
            [0.5,  "rgba(37,99,235,0.8)"],
            [0.75, "rgba(59,130,246,0.9)"],
            [1.0,  "rgba(147,197,253,1)"],
        ],
        zmin=0.0, zmax=1.0,
        text=text_matrix,
        texttemplate="%{text}",
        textfont=dict(size=11, color="#f1f5f9", family="Inter, sans-serif"),
        hovertemplate="Ticker: %{y}<br>Factor: %{x}<br>Contribution share: %{z:.1%}<extra></extra>",
        colorbar=dict(
            thickness=12,
            outlinewidth=0,
            tickfont=dict(size=10, color="#64748b"),
            tickformat=".0%",
        ),
    ))

    fig.update_layout(
        template="plotly_dark",
        height=280,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=10, b=60, l=70, r=10),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(
            tickfont=dict(size=10, color="#94a3b8"),
            tickangle=20,
            side="top",
        ),
        yaxis=dict(tickfont=dict(size=11, color="#94a3b8")),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section: Alpha Generation bar chart ───────────────────────────────────────

def _render_alpha_generation(attributions: List[PerformanceAttribution]) -> None:
    section_header(
        "Alpha Generation",
        "Idiosyncratic (unexplained) returns per ticker \u00b7 sorted descending",
    )

    if not attributions:
        st.info("No attribution data available.")
        return

    sorted_attrs = sorted(attributions, key=lambda a: a.idiosyncratic_return, reverse=True)
    tickers = [a.ticker for a in sorted_attrs]
    values = [a.idiosyncratic_return for a in sorted_attrs]
    bar_colors = [_C_POS if v >= 0 else _C_NEG for v in values]
    text_vals = [_fmt_pct(v) for v in values]

    fig = go.Figure(go.Bar(
        x=tickers,
        y=values,
        marker_color=bar_colors,
        text=text_vals,
        textposition="outside",
        textfont=dict(size=11, color="#f1f5f9", family="Inter, sans-serif"),
        hovertemplate="%{x} Alpha: %{y:+.2f}%<extra></extra>",
    ))

    fig.add_hline(
        y=0,
        line_dash="dot",
        line_color="rgba(255,255,255,0.2)",
        line_width=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=300,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=40, b=10, l=10, r=10),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(tickfont=dict(size=12, color="#94a3b8")),
        yaxis=dict(
            title="Idiosyncratic Return (%)",
            ticksuffix="%",
            tickfont=dict(size=10, color="#64748b"),
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.15)",
        ),
        showlegend=False,
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section: Rolling Attribution ──────────────────────────────────────────────

def _compute_rolling_attribution(
    ticker: str,
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
    window: int = 30,
) -> Optional[pd.DataFrame]:
    """
    Compute a rolling window OLS attribution for a single ticker.

    Returns a DataFrame indexed by date with columns:
        freight_contribution, macro_contribution, sector_contribution,
        idiosyncratic_contribution, total_return
    Returns None if insufficient data.
    """
    stock_df = stock_data.get(ticker)
    if stock_df is None or stock_df.empty:
        return None

    stock_ret = _extract_return_series(stock_df, value_col="close")
    if stock_ret is None or len(stock_ret) < window + 5:
        return None

    # Build factor returns
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

    # Align series
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

        try:
            coeffs, _, residuals = _ols_lstsq(y, X_raw)
        except Exception:
            continue

        alpha = coeffs[0]
        betas = coeffs[1:]
        n = len(y)
        factor_means = X_raw.mean(axis=0)

        contrib: Dict[str, float] = {}
        for i, col in enumerate(factor_cols):
            contrib[col] = float(betas[i]) * float(factor_means[i]) * n * 100.0

        records.append({
            "date": date_idx,
            "freight_contribution": contrib.get("freight", 0.0),
            "macro_contribution":   contrib.get("bdi", 0.0),
            "sector_contribution":  contrib.get("xli", 0.0),
            "idiosyncratic_contribution": float(alpha) * n * 100.0,
            "total_return": float((1.0 + pd.Series(y)).prod() - 1.0) * 100.0,
        })

    if not records:
        return None

    result_df = pd.DataFrame(records).set_index("date")
    return result_df


def _render_rolling_attribution(
    ticker: str,
    stock_data: Dict[str, pd.DataFrame],
    freight_data: Dict[str, pd.DataFrame],
    macro_data: Dict[str, pd.DataFrame],
) -> None:
    section_header(
        "Rolling Attribution (30-day windows)",
        "How factor contributions shift over time for " + ticker
        + " \u00b7 stacked area chart",
    )

    rolling_df = _compute_rolling_attribution(
        ticker=ticker,
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
        window=30,
    )

    if rolling_df is None or rolling_df.empty:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:10px; padding:24px; text-align:center">'
            '<div style="font-size:0.85rem; color:' + C_TEXT2 + '">'
            "Insufficient data for rolling attribution \u2014 need at least 35 observations."
            "</div></div>",
            unsafe_allow_html=True,
        )
        return

    contrib_cols = [
        ("freight_contribution", "Freight Rate Beta",  _C_FREIGHT),
        ("macro_contribution",   "BDI / Macro Factor", _C_BDI),
        ("sector_contribution",  "Sector Beta (XLI)",  _C_SECTOR),
        ("idiosyncratic_contribution", "Idiosyncratic", _C_IDIO),
    ]

    fig = go.Figure()

    for col_name, display_name, color in contrib_cols:
        if col_name not in rolling_df.columns:
            continue
        series = rolling_df[col_name]
        rgba_fill = _hex_to_rgba(color, 0.35)
        fig.add_trace(go.Scatter(
            x=rolling_df.index,
            y=series.values,
            name=display_name,
            mode="lines",
            stackgroup="factors",
            line=dict(color=color, width=1.2),
            fillcolor=rgba_fill,
            hovertemplate=display_name + ": %{y:+.2f}%<extra></extra>",
        ))

    # Overlay total return as a line
    if "total_return" in rolling_df.columns:
        fig.add_trace(go.Scatter(
            x=rolling_df.index,
            y=rolling_df["total_return"].values,
            name="Total Return",
            mode="lines",
            line=dict(color="#f1f5f9", width=2, dash="dot"),
            hovertemplate="Total Return: %{y:+.2f}%<extra></extra>",
        ))

    fig.add_hline(
        y=0,
        line_dash="dot",
        line_color="rgba(255,255,255,0.15)",
        line_width=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=360,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        font=dict(family="Inter, sans-serif"),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", zeroline=False),
        yaxis=dict(
            title="% Return Contribution",
            ticksuffix="%",
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.15)",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section: KPI summary strip ────────────────────────────────────────────────

def _render_kpi_strip(attr: PerformanceAttribution) -> None:
    metrics = [
        {
            "label": "Total Return",
            "value": _fmt_pct(attr.total_return_pct),
            "color": _C_POS if attr.total_return_pct >= 0 else _C_NEG,
        },
        {
            "label": "R\u00b2 Explained",
            "value": str(round(attr.r_squared * 100, 1)) + "%",
            "color": C_ACCENT,
        },
        {
            "label": "Info Ratio",
            "value": str(round(attr.information_ratio, 2)),
            "color": _C_IDIO,
        },
        {
            "label": "Tracking Error",
            "value": str(round(attr.tracking_error, 1)) + "%",
            "color": C_TEXT2,
        },
        {
            "label": "Idiosyncratic",
            "value": _fmt_pct(attr.idiosyncratic_return),
            "color": _C_IDIO if attr.idiosyncratic_return >= 0 else _C_NEG,
        },
    ]

    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        with col:
            col.markdown(
                '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                ' border-top:2px solid ' + m["color"] + '; border-radius:8px;'
                ' padding:14px 16px; text-align:center">'
                '<div style="font-size:0.62rem; font-weight:700; color:#64748b;'
                ' text-transform:uppercase; letter-spacing:0.09em; margin-bottom:6px">'
                + m["label"] + "</div>"
                '<div style="font-size:1.3rem; font-weight:800; color:' + m["color"] + ';'
                ' font-variant-numeric:tabular-nums">'
                + m["value"] + "</div>"
                "</div>",
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

    # ── Controls ──────────────────────────────────────────────────────────
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 2, 3])

    with ctrl_col1:
        available_tickers = [
            t for t in SHIPPING_TICKERS if t in stock_data
        ]
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

    # ── Header ────────────────────────────────────────────────────────────
    _render_header(period_label)

    # ── Run attribution for selected ticker ───────────────────────────────
    selected_attr = AttributePerformance(
        ticker=selected_ticker,
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
        period_days=period_days,
    )

    if selected_attr is None:
        st.warning(
            "Insufficient data to run attribution for "
            + selected_ticker
            + ". Need at least "
            + str(_MIN_OBS)
            + " overlapping observations across factors."
        )
    else:
        # KPI strip
        _render_kpi_strip(selected_attr)

        _divider()

        # Waterfall
        _render_waterfall(selected_attr)

    _divider("ALL STOCKS")

    # ── Run attribution for all stocks ────────────────────────────────────
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

    # Factor exposure table
    _render_factor_exposure_table(all_attrs)

    _divider()

    # R-squared heatmap
    _render_r_squared_heatmap(all_attrs)

    _divider()

    # Alpha generation
    _render_alpha_generation(all_attrs)

    _divider()

    # Rolling attribution for selected ticker
    _render_rolling_attribution(
        ticker=selected_ticker,
        stock_data=stock_data,
        freight_data=freight_data,
        macro_data=macro_data,
    )

    # ── Factor return series (diagnostic section) ─────────────────────────
    with st.expander("Factor Return Series (Diagnostic)", expanded=False):
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
                "freight_momentum":  _C_FREIGHT,
                "bdi_trend":         _C_BDI,
                "macro_composite":   _C_IDIO,
                "sector_beta":       _C_SECTOR,
            }
            factor_label_map = {
                "freight_momentum":  "Freight Momentum (5d MA)",
                "bdi_trend":         "BDI Trend (20d MA)",
                "macro_composite":   "Macro Composite",
                "sector_beta":       "Sector Beta (XLI)",
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

            fig.add_hline(
                y=0,
                line_dash="dot",
                line_color="rgba(255,255,255,0.15)",
                line_width=1,
            )
            fig.update_layout(
                template="plotly_dark",
                height=280,
                paper_bgcolor=C_CARD,
                plot_bgcolor=C_CARD,
                margin=dict(t=20, b=10, l=10, r=10),
                font=dict(family="Inter, sans-serif"),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="center",
                    x=0.5,
                    font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)",
                ),
                xaxis=dict(gridcolor="rgba(255,255,255,0.04)"),
                yaxis=dict(
                    title="Daily Return (%)",
                    ticksuffix="%",
                    gridcolor="rgba(255,255,255,0.04)",
                    zeroline=True,
                    zerolinecolor="rgba(255,255,255,0.15)",
                ),
                hoverlabel=dict(
                    bgcolor="#1a2235",
                    bordercolor="rgba(255,255,255,0.15)",
                    font=dict(color="#f1f5f9", size=12),
                ),
            )
            st.plotly_chart(fig, use_container_width=True)
