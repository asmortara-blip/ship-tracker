from __future__ import annotations

import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from engine.correlator import CorrelationResult, build_correlation_heatmap_data
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    _hex_to_rgba as _hex_rgba,
    section_header,
)
from processing.leading_indicators import (
    build_leading_indicators,
    build_lead_lag_matrix,
    compute_leading_indicator_score,
    get_recession_probability,
)


_SIGNAL_LABELS = {
    "BDI": "Baltic Dry Index",
    "US_Imports": "US Import Value",
    "US_Exports": "US Export Value",
    "Freight_PPI": "Freight Price Index",
    "Industrial_Production": "Industrial Production",
    "FBX01_Rate": "Trans-Pacific Freight Rate",
    "Commodity_DBA": "Agriculture Commodities (DBA)",
    "Commodity_DBB": "Base Metals (DBB)",
    "Commodity_USO": "Oil Price (USO)",
    "Commodity_XLB": "Materials Sector (XLB)",
}

# FRED series shown in the macro dashboard
_MACRO_SERIES = [
    ("BSXRLM",  "Baltic Dry Index"),
    ("WPU101",  "Fuel PPI"),
    ("MANEMP",  "Mfg Employment"),
    ("ISRATIO", "Inventory Ratio"),
    ("UMCSENT", "Consumer Sentiment"),
    ("PPIACO",  "PPI — All Commodities"),
]

# Shipping-calendar event lines (date, label)
_SHIPPING_EVENTS = [
    ("2025-01-29", "CNY 2025"),
    ("2025-07-01", "Peak Season 2025"),
    ("2026-02-17", "CNY 2026"),
    ("2026-07-01", "Peak Season 2026"),
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _direction_arrow(current: float, ago: float) -> tuple[str, str]:
    """Return (arrow, color) comparing current vs 30-day-ago value."""
    if ago == 0:
        return "—", "#94a3b8"
    pct_change = (current - ago) / abs(ago) * 100
    if pct_change > 2:
        return "▲", C_HIGH
    if pct_change < -2:
        return "▼", C_LOW
    return "—", "#94a3b8"


def _pct_change_30d(df: pd.DataFrame) -> float | None:
    """Return the 30-day % change of 'value' column, or None if insufficient data."""
    if df is None or df.empty or "value" not in df.columns:
        return None
    df2 = df.copy()
    if "date" in df2.columns:
        df2 = df2.sort_values("date")
    vals = df2["value"].dropna()
    if len(vals) < 2:
        return None
    current = float(vals.iloc[-1])
    ref_date = df2["date"].max() - pd.Timedelta(days=30)
    ago_mask = df2["date"] <= ref_date
    if not ago_mask.any():
        return None
    ago = float(df2.loc[ago_mask, "value"].dropna().iloc[-1])
    if ago == 0:
        return None
    return (current - ago) / abs(ago) * 100


def _p_value_stars(p: float) -> str:
    if p < 0.001:
        return "★★★"
    if p < 0.01:
        return "★★☆"
    if p < 0.05:
        return "★☆☆"
    return "☆☆☆"


# ── Bloomberg-style macro ticker row ──────────────────────────────────────────

def _render_macro_ticker(macro_data: dict) -> None:
    """Horizontal ticker row of key macro indicators at the very top."""
    items_html = ""
    count = 0
    for series_id, series_label in _MACRO_SERIES:
        if count >= 8:
            break
        df = macro_data.get(series_id)
        if df is None or df.empty:
            continue
        df2 = df.copy()
        if "date" in df2.columns:
            df2 = df2.sort_values("date")
        vals = df2["value"].dropna()
        if vals.empty:
            continue
        current_val = float(vals.iloc[-1])
        pct = _pct_change_30d(df)

        if abs(current_val) >= 1_000:
            val_str = f"{current_val:,.0f}"
        elif abs(current_val) >= 10:
            val_str = f"{current_val:.1f}"
        else:
            val_str = f"{current_val:.2f}"

        if pct is None:
            arrow = "—"
            pct_str = "n/a"
            change_color = "#64748b"
        elif pct > 2:
            arrow = "▲"
            pct_str = f"+{pct:.1f}%"
            change_color = "#10b981"
        elif pct < -2:
            arrow = "▼"
            pct_str = f"{pct:.1f}%"
            change_color = "#ef4444"
        else:
            arrow = "—"
            pct_str = f"{pct:+.1f}%"
            change_color = "#94a3b8"

        short_name = series_label[:16]
        items_html += (
            '<div style="display:flex; flex-direction:column; min-width:110px;'
            ' padding:4px 12px; border-right:1px solid rgba(255,255,255,0.06)">'
            '<div style="font-size:0.62rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.08em; white-space:nowrap">'
            + short_name +
            '</div>'
            '<div style="display:flex; align-items:baseline; gap:5px; margin-top:2px">'
            '<span style="font-size:0.95rem; font-weight:700; color:#f1f5f9; font-variant-numeric:tabular-nums">'
            + val_str +
            '</span>'
            '<span style="font-size:0.78rem; font-weight:600; color:' + change_color + '">'
            + arrow + " " + pct_str +
            '</span>'
            '</div>'
            '</div>'
        )
        count += 1

    if not items_html:
        return

    st.markdown(
        '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
        ' border-radius:8px; padding:10px 4px; margin-bottom:20px;'
        ' display:flex; gap:0; flex-wrap:wrap; overflow-x:auto;'
        ' align-items:center">'
        '<div style="font-size:0.6rem; font-weight:800; color:#3b82f6;'
        ' text-transform:uppercase; letter-spacing:0.12em; padding:0 14px;'
        ' border-right:1px solid rgba(255,255,255,0.06); white-space:nowrap">MACRO</div>'
        + items_html +
        '</div>',
        unsafe_allow_html=True,
    )


# ── macro dashboard ───────────────────────────────────────────────────────────

def _render_macro_dashboard(macro_data: dict, lookback: int) -> None:
    """2×3 grid of mini sparkline charts for key FRED series."""
    section_header(
        "Macro Indicators Dashboard",
        "FRED time-series · last " + str(lookback) + " days",
    )

    rows = [_MACRO_SERIES[:3], _MACRO_SERIES[3:]]

    for row_series in rows:
        cols = st.columns(3)
        for col, (series_id, series_label) in zip(cols, row_series):
            with col:
                df = macro_data.get(series_id)

                # ── placeholder if no data ────────────────────────────────
                if df is None or df.empty:
                    st.markdown(
                        f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
                        f' border-radius:10px; padding:16px; height:170px;'
                        f' display:flex; flex-direction:column; justify-content:center;'
                        f' align-items:center">'
                        f'<div style="font-size:0.75rem; font-weight:700; color:{C_TEXT3};'
                        f' text-transform:uppercase; letter-spacing:0.07em;'
                        f' margin-bottom:8px">{series_label}</div>'
                        f'<div style="font-size:0.8rem; color:{C_TEXT3}">Data unavailable</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    continue

                # ── slice to lookback window ──────────────────────────────
                df = df.copy()
                if "date" in df.columns:
                    df = df.sort_values("date")
                    cutoff = df["date"].max() - pd.Timedelta(days=lookback)
                    df = df[df["date"] >= cutoff]

                vals = df["value"].dropna()
                if vals.empty:
                    st.markdown(
                        f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
                        f' border-radius:10px; padding:16px; height:170px;'
                        f' display:flex; flex-direction:column; justify-content:center;'
                        f' align-items:center">'
                        f'<div style="font-size:0.75rem; font-weight:700; color:{C_TEXT3};'
                        f' text-transform:uppercase; letter-spacing:0.07em;'
                        f' margin-bottom:8px">{series_label}</div>'
                        f'<div style="font-size:0.8rem; color:{C_TEXT3}">Data unavailable</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    continue

                current_val = float(vals.iloc[-1])

                # value 30 days ago
                df_full = macro_data[series_id].copy()
                if "date" in df_full.columns:
                    df_full = df_full.sort_values("date")
                ref_date = df_full["date"].max() - pd.Timedelta(days=30)
                ago_mask = df_full["date"] <= ref_date
                if ago_mask.any():
                    ago_val = float(df_full.loc[ago_mask, "value"].dropna().iloc[-1])
                else:
                    ago_val = current_val

                arrow, arrow_color = _direction_arrow(current_val, ago_val)

                # ── sparkline ─────────────────────────────────────────────
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df["date"],
                    y=df["value"],
                    mode="lines",
                    fill="tozeroy",
                    line=dict(color=C_ACCENT, width=1.5),
                    fillcolor=_hex_rgba(C_ACCENT, 0.10),
                    hovertemplate="%{y:,.2f}<extra></extra>",
                ))

                # format current value compactly
                if abs(current_val) >= 1_000:
                    val_str = f"{current_val:,.0f}"
                else:
                    val_str = f"{current_val:.2f}"

                fig.update_layout(
                    template="plotly_dark",
                    height=150,
                    paper_bgcolor=C_CARD,
                    plot_bgcolor=C_CARD,
                    margin=dict(t=8, b=8, l=8, r=8),
                    showlegend=False,
                    xaxis=dict(
                        visible=False,
                        showgrid=False,
                        zeroline=False,
                    ),
                    yaxis=dict(
                        visible=False,
                        showgrid=False,
                        zeroline=False,
                    ),
                    hoverlabel=dict(
                        bgcolor="#1a2235",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color="#f1f5f9", size=12),
                    ),
                    annotations=[
                        dict(
                            text=(
                                '<span style="font-size:11px; font-weight:700;'
                                ' color:' + C_TEXT3 + '; text-transform:uppercase;'
                                ' letter-spacing:0.06em">' + series_label + '</span>'
                                '<br><span style="font-size:16px; font-weight:800;'
                                ' color:' + C_TEXT + '">' + val_str + '</span>'
                                ' <span style="font-size:14px; color:' + arrow_color + '">'
                                + arrow + '</span>'
                            ),
                            xref="paper", yref="paper",
                            x=0.04, y=0.97,
                            showarrow=False,
                            align="left",
                            xanchor="left",
                            yanchor="top",
                            font=dict(family="Inter, sans-serif"),
                        )
                    ],
                    font=dict(family="Inter, sans-serif"),
                )
                st.plotly_chart(fig, use_container_width=True, key=f"markets_macro_{series_id}")


# ── shipping sentiment gauge ───────────────────────────────────────────────────

def _render_shipping_sentiment_gauge(
    insights: list,
    correlation_results: list[CorrelationResult],
    macro_data: dict,
) -> None:
    """Fear & Greed meter for the shipping market (0-100 composite score)."""

    score = 50.0  # start neutral

    # 30%: average insight score from MACRO-type insights
    macro_insights = [i for i in (insights or []) if getattr(i, "category", "") == "MACRO"]
    if macro_insights:
        avg_score = sum(getattr(i, "score", 50) for i in macro_insights) / len(macro_insights)
        score_macro = avg_score  # already 0-100
    else:
        score_macro = 50.0

    # 30%: bullish vs bearish ratio across all insights
    all_scores = [getattr(i, "score", 50) for i in (insights or [])]
    if all_scores:
        bullish = sum(1 for s in all_scores if s >= 60)
        bearish = sum(1 for s in all_scores if s <= 40)
        total = len(all_scores)
        bull_ratio = bullish / total
        bear_ratio = bearish / total
        score_sentiment = 50 + (bull_ratio - bear_ratio) * 50
    else:
        score_sentiment = 50.0

    # 20%: BDI direction — above 30d avg = bullish
    bdi_df = macro_data.get("BSXRLM") if macro_data else None
    if bdi_df is not None and not bdi_df.empty and "value" in bdi_df.columns:
        bdi_sorted = bdi_df.sort_values("date") if "date" in bdi_df.columns else bdi_df
        bdi_vals = bdi_sorted["value"].dropna()
        if len(bdi_vals) >= 30:
            current_bdi = float(bdi_vals.iloc[-1])
            avg_30 = float(bdi_vals.iloc[-30:].mean())
            if avg_30 > 0:
                bdi_ratio = current_bdi / avg_30
                score_bdi = min(100, max(0, 50 + (bdi_ratio - 1) * 200))
            else:
                score_bdi = 50.0
        else:
            score_bdi = 50.0
    else:
        score_bdi = 50.0

    # 20%: freight rate trend (rising = bullish for shipping)
    freight_df = macro_data.get("WPU101") if macro_data else None
    if freight_df is not None and not freight_df.empty and "value" in freight_df.columns:
        f_sorted = freight_df.sort_values("date") if "date" in freight_df.columns else freight_df
        f_vals = f_sorted["value"].dropna()
        if len(f_vals) >= 10:
            recent_slope = float(f_vals.iloc[-1]) - float(f_vals.iloc[-10])
            base = float(f_vals.iloc[-10])
            if base != 0:
                pct_slope = (recent_slope / abs(base)) * 100
                score_freight = min(100, max(0, 50 + pct_slope * 5))
            else:
                score_freight = 50.0
        else:
            score_freight = 50.0
    else:
        score_freight = 50.0

    composite = (
        0.30 * score_macro
        + 0.30 * score_sentiment
        + 0.20 * score_bdi
        + 0.20 * score_freight
    )
    composite = round(min(100, max(0, composite)), 1)

    if composite <= 25:
        zone_label = "Extreme Fear"
        needle_color = "#ef4444"
    elif composite <= 45:
        zone_label = "Fear"
        needle_color = "#f97316"
    elif composite <= 55:
        zone_label = "Neutral"
        needle_color = "#94a3b8"
    elif composite <= 75:
        zone_label = "Greed"
        needle_color = "#86efac"
    else:
        zone_label = "Extreme Greed"
        needle_color = "#10b981"

    gauge_fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=composite,
        number=dict(
            font=dict(size=32, color="#f1f5f9", family="Inter, sans-serif"),
            suffix="",
        ),
        title=dict(
            text=(
                "Shipping Market Sentiment<br>"
                '<span style="font-size:0.9rem; color:' + needle_color + '">'
                + zone_label +
                "</span>"
            ),
            font=dict(size=13, color="#94a3b8", family="Inter, sans-serif"),
        ),
        gauge=dict(
            axis=dict(
                range=[0, 100],
                tickwidth=1,
                tickcolor="#64748b",
                tickfont=dict(size=10, color="#64748b"),
                tickvals=[0, 25, 45, 55, 75, 100],
                ticktext=["0", "25", "45", "55", "75", "100"],
            ),
            bar=dict(color=needle_color, thickness=0.22),
            bgcolor="#111827",
            borderwidth=0,
            steps=[
                dict(range=[0, 25],   color="rgba(239,68,68,0.18)"),
                dict(range=[25, 45],  color="rgba(249,115,22,0.15)"),
                dict(range=[45, 55],  color="rgba(148,163,184,0.12)"),
                dict(range=[55, 75],  color="rgba(134,239,172,0.15)"),
                dict(range=[75, 100], color="rgba(16,185,129,0.20)"),
            ],
            threshold=dict(
                line=dict(color=needle_color, width=3),
                thickness=0.75,
                value=composite,
            ),
        ),
    ))
    gauge_fig.update_layout(
        template="plotly_dark",
        height=260,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        margin=dict(t=40, b=10, l=20, r=20),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(gauge_fig, use_container_width=True, key="markets_sentiment_gauge")

    # Zone legend strip
    legend_html = (
        '<div style="display:flex; gap:6px; justify-content:center; flex-wrap:wrap;'
        ' margin-top:-8px; margin-bottom:8px">'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(239,68,68,0.15); color:#ef4444">Extreme Fear 0–25</span>'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(249,115,22,0.15); color:#f97316">Fear 25–45</span>'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(148,163,184,0.15); color:#94a3b8">Neutral 45–55</span>'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(134,239,172,0.15); color:#86efac">Greed 55–75</span>'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(16,185,129,0.20); color:#10b981">Extreme Greed 75–100</span>'
        '</div>'
    )
    st.markdown(legend_html, unsafe_allow_html=True)


# ── portfolio impact calculator ────────────────────────────────────────────────

def _render_portfolio_calculator(
    route_results: list,
    stock_data: dict[str, pd.DataFrame],
) -> None:
    """Portfolio sensitivity calculator — positions × shipping market exposure."""
    section_header(
        "Portfolio Impact Calculator",
        "Estimate your exposure to shipping market volatility",
    )

    TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]

    col_inputs, col_results = st.columns([1, 1])

    holdings: dict[str, int] = {}
    with col_inputs:
        st.markdown(
            '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
            ' border-radius:10px; padding:16px 20px; margin-bottom:4px">'
            '<div style="font-size:0.72rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:12px">Your Positions</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        st.caption("Enter your share count for each stock:")
        for ticker in TICKERS:
            holdings[ticker] = st.number_input(
                ticker + " shares",
                min_value=0,
                value=0,
                step=10,
                key="hold_" + ticker,
            )

    with col_results:
        # Compute prices from stock_data
        prices: dict[str, float] = {}
        for ticker in TICKERS:
            df = stock_data.get(ticker)
            if df is not None and not df.empty and "close" in df.columns:
                prices[ticker] = float(df["close"].dropna().iloc[-1])

        total_value = sum(
            holdings.get(t, 0) * prices.get(t, 0.0) for t in TICKERS
        )

        # Weighted sensitivity score from correlation results
        held_tickers = [t for t in TICKERS if holdings.get(t, 0) > 0]
        if held_tickers and route_results:
            corr_by_stock: dict[str, float] = {}
            for r in route_results:
                if r.stock in held_tickers:
                    existing = corr_by_stock.get(r.stock, 0.0)
                    if abs(r.pearson_r) > abs(existing):
                        corr_by_stock[r.stock] = r.pearson_r

            weighted_sum = 0.0
            weight_total = 0.0
            for t in held_tickers:
                pos_val = holdings[t] * prices.get(t, 0.0)
                sens = abs(corr_by_stock.get(t, 0.5)) * 100
                weighted_sum += sens * pos_val
                weight_total += pos_val
            sensitivity = (weighted_sum / weight_total) if weight_total > 0 else 50.0

            # Most exposed route
            most_exposed_result = max(
                [r for r in route_results if r.stock in held_tickers],
                key=lambda x: abs(x.pearson_r),
                default=None,
            )
            most_exposed = most_exposed_result.signal if most_exposed_result else "N/A"
        else:
            sensitivity = 0.0
            most_exposed = "N/A"

        if total_value == 0:
            # Example scenario
            st.markdown(
                '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                ' border-radius:10px; padding:20px; height:100%">'
                '<div style="font-size:0.72rem; font-weight:700; color:#64748b;'
                ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:12px">Example Scenario</div>'
                '<div style="font-size:0.82rem; color:#94a3b8; line-height:1.7">'
                'Enter share counts on the left to see your estimated portfolio value and '
                'shipping market sensitivity.<br><br>'
                '<strong style="color:#f1f5f9">Example:</strong> 500 ZIM + 200 MATX + 300 SBLK<br>'
                'Total value: ~$32,000 · Sensitivity: High'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )
        else:
            if sensitivity >= 70:
                sens_color = "#ef4444"
                sens_label = "High"
            elif sensitivity >= 40:
                sens_color = "#f59e0b"
                sens_label = "Moderate"
            else:
                sens_color = "#10b981"
                sens_label = "Low"

            most_exposed_label = _SIGNAL_LABELS.get(most_exposed, most_exposed)

            st.markdown(
                '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                ' border-radius:10px; padding:20px">'
                '<div style="font-size:0.72rem; font-weight:700; color:#64748b;'
                ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:14px">Portfolio Summary</div>'

                '<div style="display:flex; flex-direction:column; gap:12px">'

                '<div style="display:flex; justify-content:space-between; align-items:center;'
                ' border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:10px">'
                '<span style="font-size:0.82rem; color:#94a3b8">Total Value</span>'
                '<span style="font-size:1.3rem; font-weight:800; color:#f1f5f9">'
                '$' + f"{total_value:,.0f}" +
                '</span></div>'

                '<div style="display:flex; justify-content:space-between; align-items:center;'
                ' border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:10px">'
                '<span style="font-size:0.82rem; color:#94a3b8">Shipping Sensitivity</span>'
                '<span style="font-size:0.95rem; font-weight:700; color:' + sens_color + '">'
                + sens_label + " (" + f"{sensitivity:.0f}" + "%)"
                '</span></div>'

                '<div style="display:flex; justify-content:space-between; align-items:center;'
                ' border-bottom:1px solid rgba(255,255,255,0.06); padding-bottom:10px">'
                '<span style="font-size:0.82rem; color:#94a3b8">Most Exposed Route</span>'
                '<span style="font-size:0.78rem; font-weight:600; color:#06b6d4">'
                + most_exposed_label +
                '</span></div>'

                '<div style="display:flex; justify-content:space-between; align-items:center">'
                '<span style="font-size:0.82rem; color:#94a3b8">Market Beta</span>'
                '<span style="font-size:0.95rem; font-weight:700; color:#f59e0b">1.2×</span>'
                '</div>'

                '</div></div>',
                unsafe_allow_html=True,
            )


# ── signal timeline ───────────────────────────────────────────────────────────

def _render_signal_timeline(
    stock_data: dict,
    macro_data: dict,
    lookback: int,
) -> None:
    """Dual-axis normalized % change chart: shipping stock vs macro signal."""
    section_header(
        "Signal Timeline — Lead/Lag Explorer",
        "Both series normalized to % change from start of lookback window",
    )

    # ── selectors ─────────────────────────────────────────────────────────
    stock_options = [s for s, df in stock_data.items() if df is not None and not df.empty]
    macro_options = [(sid, lbl) for sid, lbl in _MACRO_SERIES if sid in macro_data]

    if not stock_options or not macro_options:
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER};'
            f' border-radius:10px; padding:24px; text-align:center">'
            f'<div style="font-size:0.9rem; color:{C_TEXT2}">Insufficient data for timeline.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    col_s, col_m, _ = st.columns([2, 2, 3])
    with col_s:
        default_stock_idx = stock_options.index("ZIM") if "ZIM" in stock_options else 0
        chosen_stock = st.selectbox("Stock", stock_options, index=default_stock_idx, key="timeline_stock")
    with col_m:
        macro_labels = [lbl for _, lbl in macro_options]
        macro_ids    = [sid for sid, _ in macro_options]
        default_macro_idx = macro_ids.index("BSXRLM") if "BSXRLM" in macro_ids else 0
        chosen_macro_idx = st.selectbox(
            "Macro Signal", macro_labels, index=default_macro_idx, key="timeline_macro"
        )
        chosen_macro_id  = macro_ids[macro_labels.index(chosen_macro_idx)]
        chosen_macro_lbl = chosen_macro_idx

    # ── slice data ────────────────────────────────────────────────────────
    stock_df = stock_data[chosen_stock].copy()
    stock_df = stock_df.sort_values("date")
    stock_cutoff = stock_df["date"].max() - pd.Timedelta(days=lookback)
    stock_df = stock_df[stock_df["date"] >= stock_cutoff]

    macro_df = macro_data[chosen_macro_id].copy()
    if "date" in macro_df.columns:
        macro_df = macro_df.sort_values("date")
    macro_cutoff = macro_df["date"].max() - pd.Timedelta(days=lookback)
    macro_df = macro_df[macro_df["date"] >= macro_cutoff]

    # ── normalize to % change from start ─────────────────────────────────
    stock_base = stock_df["close"].dropna().iloc[0] if not stock_df["close"].dropna().empty else None
    macro_vals = macro_df["value"].dropna()
    macro_base = float(macro_vals.iloc[0]) if not macro_vals.empty else None

    if stock_base is None or stock_base == 0 or macro_base is None or macro_base == 0:
        st.warning("Not enough data to normalize series.")
        return

    stock_pct = (stock_df["close"] / stock_base - 1) * 100
    macro_pct = (macro_df["value"] / macro_base - 1) * 100

    # ── build figure ──────────────────────────────────────────────────────
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Scatter(
            x=stock_df["date"],
            y=stock_pct,
            name=chosen_stock,
            mode="lines",
            line=dict(color=C_ACCENT, width=2),
            hovertemplate=chosen_stock + ": %{y:+.1f}%<extra></extra>",
        ),
        secondary_y=False,
    )

    fig.add_trace(
        go.Scatter(
            x=macro_df["date"],
            y=macro_pct,
            name=chosen_macro_lbl,
            mode="lines",
            line=dict(color="#06b6d4", width=2, dash="dot"),
            hovertemplate=chosen_macro_lbl + ": %{y:+.1f}%<extra></extra>",
        ),
        secondary_y=True,
    )

    # ── zero line ─────────────────────────────────────────────────────────
    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.15)", line_width=1)

    # ── shipping calendar event lines ─────────────────────────────────────
    event_colors = ["#f59e0b", "#8b5cf6", "#f59e0b", "#8b5cf6"]
    for (evt_date, evt_label), evt_color in zip(_SHIPPING_EVENTS, event_colors):
        fig.add_vline(
            x=evt_date,
            line_dash="dash",
            line_color=evt_color,
            line_width=1,
            opacity=0.6,
            annotation_text=evt_label,
            annotation_position="top left",
            annotation_font=dict(size=9, color=evt_color),
        )

    fig.update_layout(
        template="plotly_dark",
        height=400,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
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
            title="% change",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            ticksuffix="%",
        ),
        yaxis2=dict(
            title="% change (macro)",
            gridcolor="rgba(0,0,0,0)",
            zeroline=False,
            ticksuffix="%",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="markets_signal_timeline")


# ── leading indicators dashboard ──────────────────────────────────────────────

def _render_leading_indicators_dashboard(macro_data: dict) -> None:
    """3x5 grid of indicator cards with mini sparkline, signal badge, and lead time."""
    section_header(
        "Leading Indicators Dashboard",
        "Economic signals that lead shipping demand · FRED data",
    )

    try:
        indicators = build_leading_indicators(macro_data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_render_leading_indicators_dashboard error: {}", exc)
        st.warning("Leading indicator data unavailable.")
        return

    if not indicators:
        st.info("📈 Market leading indicator data is loading or unavailable — FRED data refreshes every 24 hours. Ensure FRED_API_KEY is set in .env and click Refresh All Data.")
        return

    SIGNAL_COLORS = {
        "BULLISH": ("#10b981", "rgba(16,185,129,0.15)"),
        "BEARISH": ("#ef4444", "rgba(239,68,68,0.15)"),
        "NEUTRAL": ("#94a3b8", "rgba(148,163,184,0.12)"),
    }

    # Render in rows of 3 (up to 15 indicators = 5 rows)
    for row_start in range(0, len(indicators), 3):
        row_inds = indicators[row_start:row_start + 3]
        cols = st.columns(3)
        for col, ind in zip(cols, row_inds):
            with col:
                sig_color, sig_bg = SIGNAL_COLORS.get(
                    ind.signal, ("#94a3b8", "rgba(148,163,184,0.12)")
                )

                # Format current value compactly
                cv = ind.current_value
                if abs(cv) >= 1_000_000:
                    val_str = str(round(cv / 1_000_000, 2)) + "M"
                elif abs(cv) >= 1_000:
                    val_str = str(round(cv / 1_000, 1)) + "K"
                elif abs(cv) >= 10:
                    val_str = str(round(cv, 1))
                else:
                    val_str = str(round(cv, 3))

                chg_sign = "+" if ind.change_pct >= 0 else ""
                chg_str = chg_sign + str(round(ind.change_pct, 2)) + "%"
                chg_color = "#10b981" if ind.change_pct >= 0 else "#ef4444"

                lead_str = (
                    "Coincident" if ind.lead_time_weeks == 0
                    else "Leads " + str(ind.lead_time_weeks) + " wk"
                )

                # Mini sparkline from FRED data
                df_spark = macro_data.get(ind.series_id)
                sparkline_html = ""
                if df_spark is not None and not df_spark.empty and "value" in df_spark.columns:
                    spark_df = df_spark.copy()
                    if "date" in spark_df.columns:
                        spark_df = spark_df.sort_values("date")
                    spark_vals = spark_df["value"].dropna().tail(24).tolist()
                    if len(spark_vals) >= 3:
                        spark_x = list(range(len(spark_vals)))
                        spark_fig = go.Figure()
                        spark_fig.add_trace(go.Scatter(
                            x=spark_x,
                            y=spark_vals,
                            mode="lines",
                            line=dict(color=sig_color, width=1.5),
                            hoverinfo="skip",
                        ))
                        spark_fig.update_layout(
                            height=40,
                            margin=dict(t=0, b=0, l=0, r=0),
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            showlegend=False,
                            xaxis=dict(visible=False),
                            yaxis=dict(visible=False),
                        )
                        st.plotly_chart(
                            spark_fig,
                            use_container_width=True,
                            key="spark_" + ind.series_id,
                        )

                st.markdown(
                    '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
                    ' border-left:3px solid ' + sig_color + ';'
                    ' border-radius:10px; padding:12px 14px; margin-bottom:10px">'

                    '<div style="font-size:0.65rem; font-weight:700; color:#64748b;'
                    ' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px">'
                    + ind.name[:28] +
                    '</div>'

                    '<div style="display:flex; align-items:baseline; gap:8px; margin-bottom:4px">'
                    '<span style="font-size:1.15rem; font-weight:800; color:#f1f5f9;'
                    ' font-variant-numeric:tabular-nums">' + val_str + '</span>'
                    '<span style="font-size:0.78rem; font-weight:600; color:' + chg_color + '">'
                    + chg_str +
                    '</span>'
                    '</div>'

                    '<div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap">'
                    '<span style="background:' + sig_bg + '; color:' + sig_color + ';'
                    ' padding:1px 8px; border-radius:999px; font-size:0.62rem;'
                    ' font-weight:700; letter-spacing:0.06em">' + ind.signal + '</span>'
                    '<span style="font-size:0.62rem; color:#64748b">' + lead_str + '</span>'
                    '<span style="font-size:0.60rem; color:#475569; margin-left:auto">'
                    + ind.data_frequency +
                    '</span>'
                    '</div>'

                    '<div style="font-size:0.68rem; color:#64748b; margin-top:6px;'
                    ' line-height:1.45">' + ind.shipping_implication[:90] + '...</div>'

                    '</div>',
                    unsafe_allow_html=True,
                )


# ── lead-lag correlation matrix ────────────────────────────────────────────────

def _render_lead_lag_matrix(macro_data: dict) -> None:
    """Heatmap of indicator cross-correlations with BDI at multiple lead lags."""
    section_header(
        "Lead-Lag Correlation Matrix",
        "Pearson r of each leading indicator vs Baltic Dry Index at lag 0-12 weeks",
    )

    try:
        matrix_df = build_lead_lag_matrix(macro_data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_render_lead_lag_matrix error: {}", exc)
        st.warning("Lead-lag matrix unavailable.")
        return

    if matrix_df.empty:
        st.info("📈 Insufficient market data to compute lead-lag correlations — FRED data refreshes every 24 hours. Ensure FRED_API_KEY is set in .env and click Refresh All Data.")
        return

    z = matrix_df.values.tolist()
    # Annotation text — round to 2 dp, blank for NaN
    text_vals = []
    for row in z:
        text_row = []
        for v in row:
            try:
                import math
                text_row.append("" if math.isnan(float(v)) else str(round(float(v), 2)))
            except (TypeError, ValueError):
                text_row.append("")
        text_vals.append(text_row)

    hm_fig = go.Figure(go.Heatmap(
        z=z,
        x=matrix_df.columns.tolist(),
        y=matrix_df.index.tolist(),
        colorscale="Viridis",
        zmin=-1, zmax=1,
        text=text_vals,
        texttemplate="%{text}",
        textfont=dict(size=10, color="#f1f5f9"),
        hovertemplate=(
            "Indicator: %{y}<br>"
            "Lag: %{x}<br>"
            "r = %{z:.3f}"
            "<extra></extra>"
        ),
        colorbar=dict(
            title=dict(text="r", font=dict(size=11, color="#94a3b8")),
            tickfont=dict(size=10, color="#94a3b8"),
            outlinewidth=0,
            thickness=14,
            tickvals=[-1, -0.5, 0, 0.5, 1],
            ticktext=["-1.0", "-0.5", "0", "0.5", "1.0"],
        ),
    ))
    hm_fig.update_layout(
        template="plotly_dark",
        height=max(380, len(matrix_df) * 28 + 120),
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        margin=dict(t=20, b=80, l=10, r=80),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(
            tickfont=dict(size=10, color="#94a3b8"),
            side="top",
        ),
        yaxis=dict(
            tickfont=dict(size=9, color="#94a3b8"),
            autorange="reversed",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(hm_fig, use_container_width=True, key="markets_lead_lag_matrix")


# ── recession probability gauge ────────────────────────────────────────────────

def _render_recession_probability_gauge(macro_data: dict) -> None:
    """Gauge (0-100%) showing Sahm-rule + claims-slope recession probability."""
    try:
        prob = get_recession_probability(macro_data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_render_recession_probability_gauge error: {}", exc)
        prob = 0.0

    pct = round(prob * 100, 1)

    if pct < 20:
        zone_label = "Low Risk"
        bar_color = "#10b981"
        step_colors = [
            dict(range=[0, 20],  color="rgba(16,185,129,0.20)"),
            dict(range=[20, 40], color="rgba(245,158,11,0.15)"),
            dict(range=[40, 100], color="rgba(239,68,68,0.15)"),
        ]
    elif pct < 40:
        zone_label = "Elevated Risk"
        bar_color = "#f59e0b"
        step_colors = [
            dict(range=[0, 20],  color="rgba(16,185,129,0.15)"),
            dict(range=[20, 40], color="rgba(245,158,11,0.25)"),
            dict(range=[40, 100], color="rgba(239,68,68,0.15)"),
        ]
    else:
        zone_label = "High Risk"
        bar_color = "#ef4444"
        step_colors = [
            dict(range=[0, 20],  color="rgba(16,185,129,0.12)"),
            dict(range=[20, 40], color="rgba(245,158,11,0.15)"),
            dict(range=[40, 100], color="rgba(239,68,68,0.22)"),
        ]

    gauge_fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=pct,
        number=dict(
            suffix="%",
            font=dict(size=30, color="#f1f5f9", family="Inter, sans-serif"),
        ),
        title=dict(
            text=(
                "Recession Probability<br>"
                '<span style="font-size:0.85rem; color:' + bar_color + '">'
                + zone_label +
                "</span>"
            ),
            font=dict(size=13, color="#94a3b8", family="Inter, sans-serif"),
        ),
        gauge=dict(
            axis=dict(
                range=[0, 100],
                tickwidth=1,
                tickcolor="#64748b",
                tickfont=dict(size=10, color="#64748b"),
                tickvals=[0, 20, 40, 60, 80, 100],
                ticktext=["0%", "20%", "40%", "60%", "80%", "100%"],
            ),
            bar=dict(color=bar_color, thickness=0.22),
            bgcolor="#111827",
            borderwidth=0,
            steps=step_colors,
            threshold=dict(
                line=dict(color=bar_color, width=3),
                thickness=0.75,
                value=pct,
            ),
        ),
    ))
    gauge_fig.update_layout(
        template="plotly_dark",
        height=260,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        margin=dict(t=40, b=10, l=20, r=20),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(gauge_fig, use_container_width=True, key="markets_recession_gauge")

    legend_html = (
        '<div style="display:flex; gap:6px; justify-content:center; flex-wrap:wrap;'
        ' margin-top:-8px; margin-bottom:8px">'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(16,185,129,0.15); color:#10b981">Low Risk 0-20%</span>'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(245,158,11,0.15); color:#f59e0b">Elevated 20-40%</span>'
        '<span style="font-size:0.65rem; padding:2px 8px; border-radius:999px;'
        ' background:rgba(239,68,68,0.15); color:#ef4444">High Risk &gt;40%</span>'
        '</div>'
    )
    st.markdown(legend_html, unsafe_allow_html=True)


# ── composite leading score ────────────────────────────────────────────────────

def _render_composite_leading_score(macro_data: dict) -> None:
    """Large indicator widget showing the composite leading score and 4-week forecast."""
    try:
        result = compute_leading_indicator_score(macro_data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_render_composite_leading_score error: {}", exc)
        result = {
            "composite_score": 0.5,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "top_bullish_indicators": [],
            "top_bearish_indicators": [],
            "four_week_forecast": "STABLE",
            "weighted_signal": 0.0,
        }

    score_pct = round(result["composite_score"] * 100, 1)
    forecast = result["four_week_forecast"]
    weighted_signal = result["weighted_signal"]

    if forecast == "EXPANSION":
        score_color = "#10b981"
        arrow_label = "Expanding"
        delta_val = abs(weighted_signal)
    elif forecast == "CONTRACTION":
        score_color = "#ef4444"
        arrow_label = "Contracting"
        delta_val = -abs(weighted_signal)
    else:
        score_color = "#f59e0b"
        arrow_label = "Stable"
        delta_val = 0.0

    indicator_fig = go.Figure(go.Indicator(
        mode="number+delta+gauge",
        value=score_pct,
        delta=dict(
            reference=50.0,
            position="top",
            valueformat=".1f",
            increasing=dict(color="#10b981"),
            decreasing=dict(color="#ef4444"),
        ),
        number=dict(
            suffix="/100",
            font=dict(size=38, color=score_color, family="Inter, sans-serif"),
        ),
        title=dict(
            text=(
                "Composite Leading Score<br>"
                '<span style="font-size:0.85rem; color:' + score_color + '">'
                "4-Wk Forecast: " + arrow_label +
                "</span>"
            ),
            font=dict(size=13, color="#94a3b8", family="Inter, sans-serif"),
        ),
        gauge=dict(
            axis=dict(
                range=[0, 100],
                tickfont=dict(size=9, color="#64748b"),
                tickcolor="#64748b",
            ),
            bar=dict(color=score_color, thickness=0.22),
            bgcolor="#111827",
            borderwidth=0,
            steps=[
                dict(range=[0, 35],  color="rgba(239,68,68,0.18)"),
                dict(range=[35, 50], color="rgba(245,158,11,0.15)"),
                dict(range=[50, 65], color="rgba(148,163,184,0.12)"),
                dict(range=[65, 100], color="rgba(16,185,129,0.18)"),
            ],
            threshold=dict(
                line=dict(color=score_color, width=3),
                thickness=0.75,
                value=score_pct,
            ),
        ),
    ))
    indicator_fig.update_layout(
        template="plotly_dark",
        height=300,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        margin=dict(t=50, b=10, l=20, r=20),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(indicator_fig, use_container_width=True, key="markets_composite_score")

    # Signal breakdown chips
    bull_names = ", ".join(result["top_bullish_indicators"]) or "None"
    bear_names = ", ".join(result["top_bearish_indicators"]) or "None"
    breakdown_html = (
        '<div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:center;'
        ' margin-top:-4px; margin-bottom:12px">'

        '<span style="font-size:0.65rem; padding:2px 10px; border-radius:999px;'
        ' background:rgba(16,185,129,0.15); color:#10b981; white-space:nowrap">'
        + str(result["bullish_count"]) + " Bullish" +
        '</span>'

        '<span style="font-size:0.65rem; padding:2px 10px; border-radius:999px;'
        ' background:rgba(148,163,184,0.12); color:#94a3b8; white-space:nowrap">'
        + str(result["neutral_count"]) + " Neutral" +
        '</span>'

        '<span style="font-size:0.65rem; padding:2px 10px; border-radius:999px;'
        ' background:rgba(239,68,68,0.15); color:#ef4444; white-space:nowrap">'
        + str(result["bearish_count"]) + " Bearish" +
        '</span>'

        '</div>'

        '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.07);'
        ' border-radius:8px; padding:10px 14px; font-size:0.70rem; color:#94a3b8;'
        ' line-height:1.7">'
        '<strong style="color:#10b981">Top Bullish:</strong> ' + bull_names +
        '<br>'
        '<strong style="color:#ef4444">Top Bearish:</strong> ' + bear_names +
        '</div>'
    )
    st.markdown(breakdown_html, unsafe_allow_html=True)


# ── main render ───────────────────────────────────────────────────────────────

def render(
    correlation_results: list[CorrelationResult],
    stock_data: dict[str, pd.DataFrame],
    lookback_days: int = 90,
    macro_data: dict | None = None,
    insights: list | None = None,
) -> None:
    C_CARD_L = "#1a2235"; C_BORDER_L = "rgba(255,255,255,0.08)"
    C_HIGH_L  = "#10b981"; C_MOD_L = "#f59e0b"; C_LOW_L = "#ef4444"
    C_ACCENT_L = "#3b82f6"
    C_TEXT_L  = "#f1f5f9"; C_TEXT2_L = "#94a3b8"; C_TEXT3_L = "#64748b"

    def _hex_rgba_local(h, a):
        h = h.lstrip("#"); r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{a})"

    # ── 1. Bloomberg-style macro ticker row ───────────────────────────────
    if macro_data:
        _render_macro_ticker(macro_data)

    st.caption(f"Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M UTC')} • Refreshes every 1 hour (market data)")

    # ── KPI metric row — BDI, Freight PPI, and top stock 30d change ──────
    _MARKET_KPI_SERIES = [
        ("BSXRLM", "Baltic Dry Index (BDI)"),
        ("WPU101", "Freight PPI"),
        ("MANEMP", "Mfg Employment"),
        ("ISRATIO", "Inventory Ratio"),
    ]
    kpi_data = {}
    if macro_data:
        for sid, lbl in _MARKET_KPI_SERIES:
            df_m = macro_data.get(sid)
            pct = _pct_change_30d(df_m)
            vals = df_m["value"].dropna() if df_m is not None and not df_m.empty and "value" in df_m.columns else None
            current = float(vals.iloc[-1]) if vals is not None and not vals.empty else None
            kpi_data[sid] = (lbl, current, pct)

    # Also show top-performing stock 30d return if stock_data available
    top_stock_name, top_stock_pct = None, None
    if stock_data:
        for ticker, df_s in stock_data.items():
            if df_s is None or df_s.empty or "close" not in df_s.columns:
                continue
            closes = df_s["close"].dropna()
            if len(closes) < 30:
                continue
            pct_s = (float(closes.iloc[-1]) - float(closes.iloc[-30])) / abs(float(closes.iloc[-30])) * 100
            if top_stock_pct is None or abs(pct_s) > abs(top_stock_pct):
                top_stock_name, top_stock_pct = ticker, pct_s

    if kpi_data or top_stock_name:
        kpi_series_to_show = [v for v in kpi_data.values() if v[1] is not None]
        n_cols = min(4, len(kpi_series_to_show) + (1 if top_stock_name else 0))
        if n_cols > 0:
            kpi_cols_m = st.columns(n_cols)
            col_idx = 0
            for lbl, current, pct in kpi_series_to_show[:3]:
                if col_idx >= n_cols:
                    break
                with kpi_cols_m[col_idx]:
                    val_str = f"{current:,.1f}" if current is not None else "N/A"
                    delta_str = f"{pct:+.1f}% vs 30d ago" if pct is not None else "vs 30d ago"
                    st.metric(
                        label=lbl,
                        value=val_str,
                        delta=delta_str,
                        delta_color="normal",
                    )
                col_idx += 1
            if top_stock_name and col_idx < n_cols:
                with kpi_cols_m[col_idx]:
                    st.metric(
                        label=f"Top Mover: {top_stock_name}",
                        value=f"{top_stock_pct:+.1f}%",
                        delta="30-day return",
                        delta_color="normal",
                    )
        st.markdown(
            "<hr style='border-color:rgba(255,255,255,0.07); margin:12px 0'>",
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3_L};'
        f' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:4px">'
        f'Shipping\u2013Equity Correlation Analysis</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div style="font-size:0.82rem; color:{C_TEXT2_L}; margin-bottom:16px">'
        f'Rolling Pearson r at 0\u201330 day lags'
        f' \xb7 Only shows |r| \u2265 0.40 and p &lt; 0.05'
        f' \xb7 No forced connections</div>',
        unsafe_allow_html=True,
    )

    # ── 3. Sentiment gauge + Stock chart side by side ────────────────────
    col_gauge, col_chart = st.columns([1, 2])

    with col_gauge:
        _render_shipping_sentiment_gauge(
            insights or [],
            correlation_results,
            macro_data or {},
        )

    with col_chart:
        if stock_data:
            _render_stock_chart(stock_data, lookback_days)
        else:
            st.markdown(
                f'<div style="background:{C_CARD_L}; border:1px solid {C_BORDER_L};'
                f' border-radius:10px; padding:24px; text-align:center; margin-bottom:16px">'
                f'<div style="font-size:0.9rem; color:{C_TEXT2_L}">'
                f'Stock data unavailable \u2014 yfinance may be offline.</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>", unsafe_allow_html=True)

    # ── Macro dashboard ───────────────────────────────────────────────────
    if macro_data:
        _render_macro_dashboard(macro_data, lookback_days)
        st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>", unsafe_allow_html=True)

    # ── 2. Portfolio Impact Calculator ────────────────────────────────────
    if stock_data:
        _render_portfolio_calculator(correlation_results, stock_data)
        st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>", unsafe_allow_html=True)

    # ── Guard clause ──────────────────────────────────────────────────────
    # Filter to only correlations that clear the significance threshold
    # (|r| >= 0.40 and p < 0.05).  The correlator should already do this,
    # but we re-apply here as a belt-and-suspenders safety net so the tab
    # never crashes or renders empty charts for insignificant pairs.
    significant_results = [
        r for r in (correlation_results or [])
        if abs(r.pearson_r) >= 0.40 and r.p_value < 0.05
    ]

    if not significant_results:
        st.info(
            "No significant correlations detected above threshold — "
            "check back as more data accumulates"
        )
        # Still render signal timeline even with no correlations
        if macro_data and stock_data:
            st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>", unsafe_allow_html=True)
            _render_signal_timeline(stock_data, macro_data, lookback_days)

        # Leading indicators sections also render with no correlation data
        if macro_data:
            st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
            _render_leading_indicators_dashboard(macro_data)

            st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
            _render_lead_lag_matrix(macro_data)

            st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
            col_recession, col_composite = st.columns([1, 1])
            with col_recession:
                _render_recession_probability_gauge(macro_data)
            with col_composite:
                _render_composite_leading_score(macro_data)
        return

    # Alias: remainder of the function uses significant_results exclusively
    correlation_results = significant_results

    # ── 4. Enhanced Correlation Heatmap ───────────────────────────────────
    st.markdown(
        f'<div style="font-size:0.75rem; font-weight:700; color:{C_TEXT3_L};'
        f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px">'
        f'Correlation Heatmap</div>'
        f'<div style="font-size:0.78rem; color:{C_TEXT3_L}; margin-bottom:10px">'
        f'Rolling {lookback_days}d Pearson correlation | Lag-adjusted</div>',
        unsafe_allow_html=True,
    )

    with st.spinner("Loading market data..."):
        all_stocks  = sorted({r.stock  for r in correlation_results})
        all_signals = sorted({r.signal for r in correlation_results})
        matrix = build_correlation_heatmap_data(correlation_results, all_stocks, all_signals)
    signal_labels = [_SIGNAL_LABELS.get(s, s) for s in matrix.index]

    # Fill NaN cells with 0 so the colorscale has no artifacts for missing pairs
    matrix = matrix.fillna(0)

    # Build text with muted values for low |r|
    z_vals = matrix.values
    text_matrix = []
    for row in z_vals:
        text_row = []
        for v in row:
            if v == 0:
                text_row.append("")
            elif abs(v) < 0.4:
                text_row.append(f"({v:.2f})")
            else:
                text_row.append(f"{v:.2f}")
        text_matrix.append(text_row)

    # Font color matrix: muted for low |r|
    font_colors = []
    for row in z_vals:
        row_colors = []
        for v in row:
            if abs(v) < 0.4:
                row_colors.append("rgba(148,163,184,0.35)")
            else:
                row_colors.append("#f1f5f9")
        font_colors.append(row_colors)

    heatmap_fig = go.Figure(go.Heatmap(
        z=z_vals,
        x=matrix.columns.tolist(),
        y=signal_labels,
        colorscale="RdBu_r",
        zmid=0, zmin=-1, zmax=1,
        text=text_matrix,
        texttemplate="%{text}",
        textfont=dict(size=11, color="#f1f5f9"),
        hovertemplate="Signal: %{y}<br>Stock: %{x}<br>r = %{z:.3f}<extra></extra>",
        colorbar=dict(
            tickfont=dict(color=C_TEXT2_L, size=10),
            outlinewidth=0,
            thickness=14,
            tickvals=[-1, -0.65, -0.4, 0, 0.4, 0.65, 1],
            ticktext=["-1.0", "-0.65", "-0.4", "0", "0.4", "0.65", "1.0"],
        ),
    ))
    heatmap_fig.update_layout(
        template="plotly_dark",
        height=max(450, len(all_signals) * 60 + 100),
        paper_bgcolor=C_CARD_L,
        plot_bgcolor=C_CARD_L,
        margin=dict(t=20, b=80, l=10, r=80),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(
            tickfont=dict(size=11, color=C_TEXT2_L),
            side="top",
            tickangle=45,
        ),
        yaxis=dict(
            tickfont=dict(size=10, color=C_TEXT2_L),
            tickangle=-45,
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(heatmap_fig, use_container_width=True, key="markets_correlation_heatmap")

    # ── 5. Top Correlations — Bloomberg-style cards ────────────────────────
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:16px 0'>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:0.75rem; font-weight:700; color:{C_TEXT3_L};'
        f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:12px">'
        f'Top Correlations</div>',
        unsafe_allow_html=True,
    )

    top_results = sorted(correlation_results, key=lambda x: abs(x.pearson_r), reverse=True)[:6]

    for result in top_results:
        r_abs   = abs(result.pearson_r)
        r_color = C_HIGH_L if result.pearson_r >= 0 else C_LOW_L
        r_strength_color = C_HIGH_L if r_abs >= 0.65 else (C_MOD_L if r_abs >= 0.45 else C_TEXT2_L)
        sig_label = _SIGNAL_LABELS.get(result.signal, result.signal)

        if result.lag_days == 0:
            lag_text = "Concurrent"
            lag_badge_bg = "rgba(148,163,184,0.10)"
            lag_badge_color = "#94a3b8"
        else:
            lag_text = "Leading by " + str(result.lag_days) + " days"
            lag_badge_bg = "rgba(59,130,246,0.12)"
            lag_badge_color = "#60a5fa"

        stars = _p_value_stars(result.p_value)
        dir_label = "Positive" if result.pearson_r > 0 else "Negative"
        dir_color = C_HIGH_L if result.pearson_r > 0 else C_LOW_L

        # Mini scatter data from stock_data would require passing it here;
        # we show a visual correlation bar instead
        bar_width = int(r_abs * 100)
        bar_color = r_color

        st.markdown(
            '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
            ' border-left:3px solid ' + r_strength_color + ';'
            ' border-radius:10px; padding:16px 20px; margin-bottom:10px">'

            # Top row: asset names + correlation coefficient
            '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:16px">'
            '<div style="flex:1">'
            '<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px">'
            '<span style="font-size:1rem; font-weight:800; color:#f1f5f9">' + result.stock + '</span>'
            '<span style="font-size:1.1rem; color:#64748b">&#x2194;</span>'
            '<span style="font-size:0.85rem; font-weight:600; color:#94a3b8">' + sig_label + '</span>'
            '</div>'
            '<div style="font-size:0.78rem; color:#64748b; line-height:1.5">' + result.interpretation + '</div>'
            '</div>'

            # Right: big correlation number
            '<div style="text-align:right; flex-shrink:0">'
            '<div style="font-size:2rem; font-weight:900; color:' + r_color + '; line-height:1; font-variant-numeric:tabular-nums">'
            + ('+' if result.pearson_r >= 0 else '') + f"{result.pearson_r:.2f}" +
            '</div>'
            '<div style="font-size:0.7rem; color:#64748b; margin-top:2px">Pearson r</div>'
            '</div>'
            '</div>'

            # Correlation bar
            '<div style="background:rgba(255,255,255,0.05); border-radius:4px;'
            ' height:4px; margin:10px 0; position:relative">'
            '<div style="position:absolute; left:50%; width:' + str(bar_width // 2) + '%;'
            ' height:100%; background:' + bar_color + '; border-radius:4px;'
            + (' right:50%; left:auto;' if result.pearson_r < 0 else '') + '"></div>'
            '</div>'

            # Bottom row: badges
            '<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:4px">'
            '<span style="background:' + lag_badge_bg + '; color:' + lag_badge_color + ';'
            ' padding:2px 10px; border-radius:999px; font-size:0.68rem; font-weight:600">'
            + lag_text +
            '</span>'
            '<span style="background:rgba(255,255,255,0.05); color:' + dir_color + ';'
            ' padding:2px 10px; border-radius:999px; font-size:0.68rem; font-weight:600">'
            + dir_label +
            '</span>'
            '<span style="font-size:0.72rem; color:#64748b">Significance: ' + stars + '</span>'
            '<span style="font-size:0.72rem; color:#64748b; margin-left:auto">'
            'p=' + f"{result.p_value:.4f}" + ' | n=' + str(result.n_observations) +
            '</span>'
            '</div>'

            '</div>',
            unsafe_allow_html=True,
        )

    # ── Dual-axis detail charts ────────────────────────────────────────────
    top3 = sorted(correlation_results, key=lambda x: abs(x.pearson_r), reverse=True)[:3]
    for result in top3:
        _render_dual_axis_chart(result, stock_data)

    # ── Signal timeline ───────────────────────────────────────────────────
    if macro_data and stock_data:
        st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:20px 0'>", unsafe_allow_html=True)
        _render_signal_timeline(stock_data, macro_data, lookback_days)

    # ── Leading Indicators section ────────────────────────────────────────
    if macro_data:
        st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
        _render_leading_indicators_dashboard(macro_data)

        st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
        _render_lead_lag_matrix(macro_data)

        st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
        col_recession, col_composite = st.columns([1, 1])
        with col_recession:
            _render_recession_probability_gauge(macro_data)
        with col_composite:
            _render_composite_leading_score(macro_data)


# ── sub-charts ────────────────────────────────────────────────────────────────

def _render_stock_chart(stock_data: dict[str, pd.DataFrame], lookback_days: int) -> None:
    """Normalized % return chart for all stocks."""
    C_CARD_L  = "#1a2235"
    C_TEXT2_L = "#94a3b8"

    COLORS = [
        "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6",
        "#ef4444", "#06b6d4", "#f97316", "#ec4899", "#a3e635",
    ]

    fig = go.Figure()
    for i, (symbol, df) in enumerate(stock_data.items()):
        if df is None or df.empty:
            continue
        if "close" not in df.columns or "date" not in df.columns:
            logger.warning("_render_stock_chart: missing columns for {}", symbol)
            continue
        try:
            recent = df.tail(lookback_days).copy()
            if recent.empty or len(recent) < 2:
                continue
            base = recent["close"].dropna().iloc[0]
            if base == 0:
                continue
            pct_return = (recent["close"] / base - 1) * 100
        except (KeyError, IndexError) as exc:
            logger.warning("_render_stock_chart: could not compute return for {}: {}", symbol, exc)
            continue
        color = COLORS[i % len(COLORS)]

        fig.add_trace(go.Scatter(
            x=recent["date"],
            y=pct_return,
            mode="lines",
            name=symbol,
            line=dict(color=color, width=2),
            hovertemplate=f"<b>{symbol}</b>: %{{y:+.1f}}%<extra></extra>",
        ))

    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.2)", line_width=1)
    fig.update_layout(
        template="plotly_dark",
        height=320,
        paper_bgcolor=C_CARD_L,
        plot_bgcolor=C_CARD_L,
        yaxis=dict(title="Return (%)", gridcolor="rgba(255,255,255,0.05)", zeroline=False),
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5,
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(t=40, b=10, l=10, r=10),
        font=dict(family="Inter, sans-serif"),
        title=dict(
            text="Shipping Stocks & ETFs \u2014 Normalized Returns",
            font=dict(size=13, color=C_TEXT2_L),
            x=0,
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="markets_stock_chart")


def _render_dual_axis_chart(result: CorrelationResult, stock_data: dict[str, pd.DataFrame]) -> None:
    """Dual-axis chart: shipping signal vs stock close price."""
    stock_df = stock_data.get(result.stock)
    if stock_df is None or stock_df.empty:
        return

    # Guard: required columns must be present
    if "date" not in stock_df.columns or "close" not in stock_df.columns:
        logger.warning("_render_dual_axis_chart: missing columns for {}", result.stock)
        return

    with st.expander(
        f"{result.stock} vs {_SIGNAL_LABELS.get(result.signal, result.signal)}"
        f" (r={result.pearson_r:.2f})",
        key=f"markets_expander_{result.stock}_{result.signal}",
    ):
        st.caption(result.interpretation)

        try:
            stock_series = stock_df.set_index("date")["close"].dropna()
        except (KeyError, Exception) as exc:
            logger.warning("_render_dual_axis_chart: failed to build series for {}: {}", result.stock, exc)
            st.warning(f"Chart data unavailable for {result.stock}.")
            return

        # Guard: need at least one data point to build the chart
        if stock_series.empty:
            st.warning(f"No price data available for {result.stock}.")
            return

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=stock_series.index,
            y=stock_series.values,
            name=result.stock,
            line=dict(color="#4A90D9", width=2),
            yaxis="y1",
        ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0a0f1a",
            plot_bgcolor="#111827",
            height=260,
            yaxis=dict(
                title=result.stock,
                side="left",
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.1)",
            ),
            xaxis=dict(
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.1)",
            ),
            margin=dict(t=10, b=10),
            showlegend=True,
            hoverlabel=dict(
                bgcolor="#1a2235",
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color="#f1f5f9", size=12),
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"markets_dual_{result.stock}_{result.signal}")
        st.caption(
            f"Lag: {result.lag_days} days"
            f" | p-value: {result.p_value:.4f}"
            f" | N={result.n_observations}"
        )
