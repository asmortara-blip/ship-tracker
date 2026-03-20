"""Dedicated Macro-Economics Dashboard tab for Ship Tracker.

render(macro_data, freight_data, stock_data) is the public entry point.

Sections
--------
1. Macro Dashboard Grid  — 4x4 sparkline cards (16 key indicators)
2. Yield Curve           — current / 3-month-ago / 1-year-ago Treasury curves
3. Trade Balance Tracker — deficit bar + USD/CNY line, trade-war shading
4. Commodity Monitor     — WTI / Brent / Diesel area chart (abs or normalized)
5. ISM/PMI vs Freight    — scatter with regression + R² annotation
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from loguru import logger

import streamlit as st

from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_MACRO,
    _hex_to_rgba as _rgba,
    section_header,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 16 indicators for the 4×4 grid (row-major order)
_GRID_SERIES: list[tuple[str, str]] = [
    ("AMTMNO",        "Mfg New Orders"),
    ("AMDMNO",        "Durable Gds Orders"),
    ("AMDMUS",        "Durable Unfilled"),
    ("AMTMTI",        "Mfg Inventories"),
    ("MANEMP",        "Mfg Employment"),
    ("USPHCI",        "Philly Fed PMI"),
    ("CFNAI",         "Chicago Fed NFAI"),
    ("UMCSENT",       "Consumer Sentiment"),
    ("HOUST",         "Housing Starts"),
    ("PERMIT",        "Building Permits"),
    ("HSN1F",         "New Home Sales"),
    ("PCE",           "Pers. Consumption"),
    ("DCOILWTICO",    "WTI Crude"),
    ("DCOILBRENTEU",  "Brent Crude"),
    ("GASDESW",       "Diesel Price"),
    ("VIXCLS",        "VIX"),
]

# Yield curve maturities in display order
_YIELD_SERIES: list[tuple[str, str]] = [
    ("DGS1M",  "1M"),
    ("DGS3M",  "3M"),
    ("DGS6M",  "6M"),
    ("DGS1",   "1Y"),
    ("DGS2",   "2Y"),
    ("DGS5",   "5Y"),
    ("DGS10",  "10Y"),
    ("DGS30",  "30Y"),
]

# Periods of notable trade friction for shading
_TRADE_WAR_PERIODS: list[tuple[str, str, str]] = [
    ("2018-03-01", "2020-01-15", "US-China Trade War 2018-19"),
    ("2025-02-01", "2025-12-31", "Tariff Escalation 2025"),
]

# Commodity series
_COMMODITY_SERIES: list[tuple[str, str, str]] = [
    ("DCOILWTICO",   "WTI Crude",   C_ACCENT),
    ("DCOILBRENTEU", "Brent Crude", "#06b6d4"),
    ("GASDESW",      "US Diesel",   "#f59e0b"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_value(df: pd.DataFrame) -> float | None:
    if df is None or df.empty or "value" not in df.columns:
        return None
    v = df["value"].dropna()
    return float(v.iloc[-1]) if not v.empty else None


def _value_n_days_ago(df: pd.DataFrame, n: int = 30) -> float | None:
    """Return the value approximately *n* days before the last observation."""
    if df is None or df.empty or "value" not in df.columns:
        return None
    df2 = df.copy()
    if "date" not in df2.columns:
        return None
    df2 = df2.sort_values("date")
    max_date = df2["date"].max()
    cutoff = max_date - pd.Timedelta(days=n)
    mask = df2["date"] <= cutoff
    if not mask.any():
        return None
    v = df2.loc[mask, "value"].dropna()
    return float(v.iloc[-1]) if not v.empty else None


def _pct_change(current: float | None, ago: float | None) -> float | None:
    if current is None or ago is None or ago == 0:
        return None
    return (current - ago) / abs(ago) * 100


def _format_value(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def _card_border_color(pct: float | None) -> str:
    """Green = bullish (>+2%), red = bearish (<-2%), gray = neutral."""
    if pct is None:
        return C_TEXT3
    if pct > 2:
        return C_HIGH
    if pct < -2:
        return C_LOW
    return C_TEXT3


def _slice_lookback(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()
    df2 = df.sort_values("date")
    cutoff = df2["date"].max() - pd.Timedelta(days=days)
    return df2[df2["date"] >= cutoff]


def _series_on_date(df: pd.DataFrame, target_date: datetime) -> list[float | None]:
    """Return yield-curve values as a list aligned to _YIELD_SERIES maturities."""
    raise NotImplementedError  # only used internally via _yield_vector


def _yield_vector(
    macro_data: dict[str, pd.DataFrame],
    target_date: datetime | None = None,
) -> tuple[list[str], list[float | None]]:
    """Return (maturity_labels, yield_values) for the yield curve.

    If target_date is None, use the most recent available observation.
    """
    labels: list[str] = []
    values: list[float | None] = []

    for series_id, label in _YIELD_SERIES:
        df = macro_data.get(series_id)
        if df is None or df.empty or "value" not in df.columns or "date" not in df.columns:
            labels.append(label)
            values.append(None)
            continue
        df2 = df.dropna(subset=["value"]).sort_values("date")
        if target_date is None:
            val = float(df2["value"].iloc[-1])
        else:
            mask = df2["date"] <= pd.Timestamp(target_date)
            if not mask.any():
                val = None
            else:
                val = float(df2.loc[mask, "value"].iloc[-1])
        labels.append(label)
        values.append(val)

    return labels, values


def _regression_line(
    x: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, float, float, float]:
    """OLS regression. Returns (y_hat_sorted, slope, intercept, r_squared)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mask = np.isfinite(x) & np.isfinite(y)
        xm, ym = x[mask], y[mask]
        if len(xm) < 3:
            return np.array([]), 0.0, 0.0, 0.0
        coeffs = np.polyfit(xm, ym, 1)
        slope, intercept = float(coeffs[0]), float(coeffs[1])
        y_hat = np.polyval(coeffs, xm)
        ss_res = float(np.sum((ym - y_hat) ** 2))
        ss_tot = float(np.sum((ym - np.mean(ym)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0.0
        x_sorted = np.sort(xm)
        return np.polyval(coeffs, x_sorted), slope, intercept, r2


# ---------------------------------------------------------------------------
# Section 1 — Macro Dashboard Grid (4×4)
# ---------------------------------------------------------------------------

def _render_macro_grid(macro_data: dict[str, pd.DataFrame]) -> None:
    section_header(
        "Macro Dashboard",
        "16 key indicators — 30-day signal · green border = bullish, red = bearish",
    )

    rows = [_GRID_SERIES[i : i + 4] for i in range(0, 16, 4)]

    for row in rows:
        cols = st.columns(4)
        for col, (sid, label) in zip(cols, row):
            with col:
                df = macro_data.get(sid)
                current = _latest_value(df)
                ago = _value_n_days_ago(df, 30) if df is not None else None
                pct = _pct_change(current, ago)
                border_color = _card_border_color(pct)

                # --- unavailable placeholder ---
                if df is None or df.empty or current is None:
                    st.markdown(
                        '<div style="background:' + C_CARD + '; border:1px solid '
                        + border_color + '; border-radius:10px; padding:10px;'
                        ' height:110px; display:flex; flex-direction:column;'
                        ' justify-content:center; align-items:center">'
                        '<div style="font-size:0.62rem; font-weight:700; color:'
                        + C_TEXT3 + '; text-transform:uppercase;'
                        ' letter-spacing:0.07em; margin-bottom:6px">' + label + '</div>'
                        '<div style="font-size:0.75rem; color:' + C_TEXT3
                        + '">No data</div>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    continue

                # format numbers
                val_str = _format_value(current)
                if pct is None:
                    pct_str = "n/a"
                    pct_color = C_TEXT3
                    arrow = "—"
                elif pct > 2:
                    pct_str = "+" + f"{pct:.1f}" + "%"
                    pct_color = C_HIGH
                    arrow = "▲"
                elif pct < -2:
                    pct_str = f"{pct:.1f}" + "%"
                    pct_color = C_LOW
                    arrow = "▼"
                else:
                    pct_str = f"{pct:+.1f}" + "%"
                    pct_color = C_TEXT3
                    arrow = "—"

                # sparkline — height=40, no axes, fill=tozeroy
                df_sliced = _slice_lookback(df, 90)
                fig = go.Figure()
                if not df_sliced.empty:
                    fig.add_trace(go.Scatter(
                        x=df_sliced["date"],
                        y=df_sliced["value"],
                        mode="lines",
                        fill="tozeroy",
                        line=dict(color=border_color if pct is not None else C_ACCENT, width=1),
                        fillcolor=_rgba(
                            border_color if pct is not None else C_ACCENT, 0.10
                        ),
                        hoverinfo="skip",
                    ))

                fig.update_layout(
                    height=40,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(t=0, b=0, l=0, r=0),
                    showlegend=False,
                    xaxis=dict(visible=False),
                    yaxis=dict(visible=False),
                )

                # card HTML wrapper with colored border
                st.markdown(
                    '<div style="background:' + C_CARD + '; border:1px solid '
                    + border_color + '; border-radius:10px; padding:10px 12px 4px 12px;'
                    ' margin-bottom:2px">'
                    '<div style="font-size:0.60rem; font-weight:700; color:' + C_TEXT3
                    + '; text-transform:uppercase; letter-spacing:0.07em;'
                    ' white-space:nowrap; overflow:hidden; text-overflow:ellipsis">'
                    + label +
                    '</div>'
                    '<div style="display:flex; align-items:baseline; gap:6px;'
                    ' margin-top:3px">'
                    '<span style="font-size:1rem; font-weight:800; color:' + C_TEXT
                    + '; font-variant-numeric:tabular-nums">' + val_str + '</span>'
                    '<span style="font-size:0.70rem; font-weight:600; color:'
                    + pct_color + '">' + arrow + " " + pct_str + '</span>'
                    '</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=f"macro_grid_{sid}")


# ---------------------------------------------------------------------------
# Section 2 — Yield Curve
# ---------------------------------------------------------------------------

def _render_yield_curve(macro_data: dict[str, pd.DataFrame]) -> None:
    section_header(
        "Treasury Yield Curve",
        "Current · 3-month ago (dashed) · 1-year ago (dotted) — red shading = inversion",
    )

    now = datetime.now()
    date_3m = now - timedelta(days=91)
    date_1y = now - timedelta(days=365)

    labels_now, vals_now = _yield_vector(macro_data, target_date=None)
    _, vals_3m = _yield_vector(macro_data, target_date=date_3m)
    _, vals_1y = _yield_vector(macro_data, target_date=date_1y)

    # only keep maturities where current curve has data
    valid_idx = [i for i, v in enumerate(vals_now) if v is not None]
    if not valid_idx:
        st.info("Treasury yield data not available. Ensure DGS* series are fetched.")
        return

    x_labels = [labels_now[i] for i in valid_idx]
    y_now = [vals_now[i] for i in valid_idx]
    y_3m  = [vals_3m[i]  if vals_3m[i]  is not None else None for i in valid_idx]
    y_1y  = [vals_1y[i]  if vals_1y[i]  is not None else None for i in valid_idx]

    # check inversion: 10Y < 2Y
    try:
        idx_2y  = x_labels.index("2Y")
        idx_10y = x_labels.index("10Y")
        inverted = (y_now[idx_10y] is not None and y_now[idx_2y] is not None
                    and y_now[idx_10y] < y_now[idx_2y])
    except ValueError:
        inverted = False

    fig = go.Figure()

    # ── red inversion shading ───────────────────────────────────────────────
    if inverted:
        # shade the entire area under the curve red
        y_min = min(v for v in y_now if v is not None) - 0.1
        fig.add_hrect(
            y0=y_min, y1=max(v for v in y_now if v is not None) + 0.1,
            fillcolor="rgba(239,68,68,0.07)",
            line_width=0,
            annotation_text="INVERTED — Recession Signal",
            annotation_position="top left",
            annotation_font=dict(color="#ef4444", size=11),
        )

    # ── 1-year ago (dotted) ─────────────────────────────────────────────────
    if any(v is not None for v in y_1y):
        fig.add_trace(go.Scatter(
            x=x_labels,
            y=y_1y,
            mode="lines+markers",
            name="1Y Ago",
            line=dict(color=C_TEXT3, width=1.5, dash="dot"),
            marker=dict(size=5, color=C_TEXT3),
            connectgaps=True,
            hovertemplate="%{x}: %{y:.2f}%<extra>1Y Ago</extra>",
        ))

    # ── 3-month ago (dashed) ────────────────────────────────────────────────
    if any(v is not None for v in y_3m):
        fig.add_trace(go.Scatter(
            x=x_labels,
            y=y_3m,
            mode="lines+markers",
            name="3M Ago",
            line=dict(color=C_TEXT2, width=1.5, dash="dash"),
            marker=dict(size=5, color=C_TEXT2),
            connectgaps=True,
            hovertemplate="%{x}: %{y:.2f}%<extra>3M Ago</extra>",
        ))

    # ── current (solid) ─────────────────────────────────────────────────────
    curve_color = "#ef4444" if inverted else C_HIGH
    fig.add_trace(go.Scatter(
        x=x_labels,
        y=y_now,
        mode="lines+markers",
        name="Current",
        line=dict(color=curve_color, width=2.5),
        marker=dict(size=7, color=curve_color),
        fill="tozeroy",
        fillcolor=_rgba(curve_color, 0.08),
        connectgaps=True,
        hovertemplate="%{x}: %{y:.2f}%<extra>Current</extra>",
    ))

    inversion_note = " — INVERTED (10Y < 2Y)" if inverted else ""
    fig.update_layout(
        template="plotly_dark",
        height=350,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        title=dict(
            text="US Treasury Yield Curve" + inversion_note,
            font=dict(size=13, color="#ef4444" if inverted else C_TEXT2),
            x=0.01,
        ),
        xaxis=dict(
            title="Maturity",
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        yaxis=dict(
            title="Yield (%)",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            ticksuffix="%",
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(family="Inter, sans-serif"),
    )

    st.plotly_chart(fig, use_container_width=True)

    if inverted:
        st.markdown(
            '<div style="background:rgba(239,68,68,0.10); border:1px solid'
            ' rgba(239,68,68,0.30); border-radius:8px; padding:10px 16px;'
            ' font-size:0.82rem; color:#ef4444; margin-top:-8px">'
            '<strong>Curve Inverted:</strong> The 10-year yield is below the 2-year'
            ' yield — historically a leading indicator of recession within 12–18 months.'
            '</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 3 — Trade Balance Tracker
# ---------------------------------------------------------------------------

def _render_trade_balance(macro_data: dict[str, pd.DataFrame]) -> None:
    section_header(
        "Trade Balance Tracker",
        "Goods trade deficit (bar) vs USD/CNY exchange rate (line) — shaded = trade-war periods",
    )

    trade_df = macro_data.get("BOPGSTB")
    cny_df   = macro_data.get("DEXCHUS")

    if (trade_df is None or trade_df.empty) and (cny_df is None or cny_df.empty):
        st.info("Trade balance (BOPGSTB) and USD/CNY (DEXCHUS) data not available.")
        return

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ── trade balance bars ──────────────────────────────────────────────────
    if trade_df is not None and not trade_df.empty:
        tb = trade_df.dropna(subset=["value"]).sort_values("date")
        bar_colors = [C_LOW if v < 0 else C_HIGH for v in tb["value"]]
        fig.add_trace(
            go.Bar(
                x=tb["date"],
                y=tb["value"],
                name="Trade Balance (Goods, $B)",
                marker_color=bar_colors,
                opacity=0.75,
                hovertemplate="Date: %{x|%b %Y}<br>Balance: $%{y:,.1f}B<extra></extra>",
            ),
            secondary_y=False,
        )

    # ── USD/CNY line ────────────────────────────────────────────────────────
    if cny_df is not None and not cny_df.empty:
        cny = cny_df.dropna(subset=["value"]).sort_values("date")
        fig.add_trace(
            go.Scatter(
                x=cny["date"],
                y=cny["value"],
                name="USD/CNY",
                mode="lines",
                line=dict(color="#f59e0b", width=2),
                hovertemplate="Date: %{x|%b %Y}<br>USD/CNY: %{y:.3f}<extra></extra>",
            ),
            secondary_y=True,
        )

    # ── trade-war shading ───────────────────────────────────────────────────
    for start, end, label in _TRADE_WAR_PERIODS:
        fig.add_vrect(
            x0=start, x1=end,
            fillcolor="rgba(239,68,68,0.08)",
            line_width=0,
            annotation_text=label,
            annotation_position="top left",
            annotation_font=dict(color="#ef4444", size=9),
        )

    # ── correlation annotation ──────────────────────────────────────────────
    corr_text = ""
    if (trade_df is not None and not trade_df.empty
            and cny_df is not None and not cny_df.empty):
        try:
            tb_s = trade_df.dropna(subset=["value"]).set_index("date")["value"]
            cx_s = cny_df.dropna(subset=["value"]).set_index("date")["value"]
            merged = pd.concat([tb_s, cx_s], axis=1, join="inner").dropna()
            if len(merged) >= 6:
                r = merged.iloc[:, 0].corr(merged.iloc[:, 1])
                corr_text = "r(trade deficit, USD/CNY) = " + f"{r:.2f}"
        except Exception:
            pass

    fig.update_layout(
        template="plotly_dark",
        height=380,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=30, b=20, l=10, r=10),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
        yaxis=dict(
            title="Trade Balance ($B)",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.15)",
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        yaxis2=dict(
            title="USD/CNY",
            gridcolor="rgba(0,0,0,0)",
            zeroline=False,
            tickfont=dict(color="#f59e0b", size=11),
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(family="Inter, sans-serif"),
        annotations=[
            dict(
                text=corr_text,
                xref="paper", yref="paper",
                x=0.99, y=0.01,
                showarrow=False,
                align="right",
                xanchor="right",
                yanchor="bottom",
                font=dict(size=11, color=C_TEXT2),
                bgcolor="rgba(26,34,53,0.85)",
                bordercolor="rgba(255,255,255,0.10)",
                borderpad=6,
            )
        ] if corr_text else [],
    )

    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Section 4 — Commodity Price Monitor
# ---------------------------------------------------------------------------

def _render_commodity_monitor(macro_data: dict[str, pd.DataFrame]) -> None:
    section_header(
        "Commodity Price Monitor",
        "WTI Crude · Brent Crude · US Diesel — toggle between absolute and indexed (base = 100)",
    )

    col_toggle, _ = st.columns([1, 3])
    with col_toggle:
        normalized = st.toggle("Normalize to 100", value=False, key="macro_commodity_norm")

    lookback_days = 730  # 2 years of history
    fig = go.Figure()
    has_data = False

    corr_data: dict[str, pd.Series] = {}

    for sid, label, color in _COMMODITY_SERIES:
        df = macro_data.get(sid)
        if df is None or df.empty or "value" not in df.columns:
            continue
        df2 = _slice_lookback(df, lookback_days).dropna(subset=["value"])
        if df2.empty:
            continue
        has_data = True

        y_vals = df2["value"].values.astype(float)
        if normalized:
            base = y_vals[0] if y_vals[0] != 0 else 1.0
            y_vals = y_vals / base * 100.0

        corr_data[label] = pd.Series(y_vals, index=df2["date"].values)

        hover_suffix = "" if not normalized else " (idx)"
        fig.add_trace(go.Scatter(
            x=df2["date"],
            y=y_vals,
            name=label,
            mode="lines",
            fill="tozeroy",
            line=dict(color=color, width=2),
            fillcolor=_rgba(color, 0.07),
            hovertemplate=label + ": %{y:,.2f}" + hover_suffix + "<extra></extra>",
        ))

    if not has_data:
        st.info("No commodity price data available (DCOILWTICO / DCOILBRENTEU / GASDESW).")
        return

    y_title = "Price (Indexed, base=100)" if normalized else "Price (USD)"
    fig.update_layout(
        template="plotly_dark",
        height=360,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        yaxis=dict(
            title=y_title,
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.04)",
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── correlation table with shipping rates ────────────────────────────────
    bdi_df = macro_data.get("BDIY")
    if bdi_df is not None and not bdi_df.empty and corr_data:
        bdi_s = (
            bdi_df.dropna(subset=["value"])
            .sort_values("date")
            .set_index("date")["value"]
        )
        rows = []
        for label, series in corr_data.items():
            try:
                merged = pd.concat(
                    [bdi_s.rename("bdi"), series.rename("comm")], axis=1
                ).dropna()
                if len(merged) >= 6:
                    r = merged["bdi"].corr(merged["comm"])
                    rows.append({"Commodity": label, "r vs BDI": round(r, 3)})
            except Exception:
                pass
        if rows:
            corr_table = pd.DataFrame(rows).set_index("Commodity")
            st.caption("Pearson correlation with Baltic Dry Index")
            def _color_r(val):
                try:
                    v = float(val)
                except (ValueError, TypeError):
                    return ""
                if v >= 0.4:
                    return "background-color: rgba(16,185,129,0.25); color:#10b981"
                if v <= -0.4:
                    return "background-color: rgba(239,68,68,0.25); color:#ef4444"
                return ""
            st.dataframe(
                corr_table.style.format({"r vs BDI": "{:.3f}"}).applymap(_color_r, subset=["r vs BDI"]),
                use_container_width=False,
            )


# ---------------------------------------------------------------------------
# Section 5 — ISM / PMI vs Freight Rates
# ---------------------------------------------------------------------------

_QUARTER_COLORS = {
    "Q1": "#3b82f6",
    "Q2": "#10b981",
    "Q3": "#f59e0b",
    "Q4": "#8b5cf6",
}


def _render_pmi_vs_freight(
    macro_data: dict[str, pd.DataFrame],
    freight_data: dict[str, pd.DataFrame] | None,
) -> None:
    section_header(
        "PMI / Manufacturing vs Freight Rates",
        "Scatter: x=PMI proxy, y=BDI — color by quarter — regression + R²",
    )

    # ── choose PMI proxy ────────────────────────────────────────────────────
    pmi_candidates = [
        ("USPHCI",  "Philly Fed PMI"),
        ("MANEMP",  "Manufacturing Employment"),
        ("CFNAI",   "Chicago Fed NFAI"),
        ("AMTMNO",  "Mfg New Orders"),
    ]
    pmi_df: pd.DataFrame | None = None
    pmi_label = "PMI Proxy"
    for sid, lbl in pmi_candidates:
        candidate = macro_data.get(sid)
        if candidate is not None and not candidate.empty:
            pmi_df = candidate
            pmi_label = lbl
            break

    bdi_df = macro_data.get("BDIY")

    if pmi_df is None or bdi_df is None or bdi_df.empty:
        st.info(
            "PMI or BDI data not available. "
            "Ensure USPHCI (or MANEMP/CFNAI) and BDIY are fetched."
        )
        return

    # ── align on monthly basis ──────────────────────────────────────────────
    pmi_s = (
        pmi_df.dropna(subset=["value"])
        .sort_values("date")
        .set_index("date")["value"]
        .resample("MS")
        .last()
    )
    bdi_s = (
        bdi_df.dropna(subset=["value"])
        .sort_values("date")
        .set_index("date")["value"]
        .resample("MS")
        .mean()
    )

    merged = pd.concat(
        [pmi_s.rename("pmi"), bdi_s.rename("bdi")], axis=1
    ).dropna()

    if len(merged) < 6:
        st.info("Insufficient overlapping PMI and BDI data for scatter analysis.")
        return

    merged = merged.reset_index()
    merged["quarter"] = "Q" + merged["date"].dt.quarter.astype(str)
    merged["year"] = merged["date"].dt.year

    # optional: trade volume sizing (use IMPGS if available)
    impgs_df = macro_data.get("IMPGS")
    size_col = None
    if impgs_df is not None and not impgs_df.empty:
        imp_s = (
            impgs_df.dropna(subset=["value"])
            .sort_values("date")
            .set_index("date")["value"]
            .resample("MS")
            .last()
        )
        merged = merged.join(imp_s.rename("imports"), on="date")
        if "imports" in merged.columns and merged["imports"].notna().sum() > 3:
            imp_vals = merged["imports"].fillna(merged["imports"].median())
            imp_min = imp_vals.min()
            imp_rng = imp_vals.max() - imp_min
            if imp_rng > 0:
                merged["sz"] = 8 + (imp_vals - imp_min) / imp_rng * 20
                size_col = "sz"

    fig = go.Figure()

    # ── scatter points by quarter ───────────────────────────────────────────
    for q, grp in merged.groupby("quarter"):
        color = _QUARTER_COLORS.get(q, C_ACCENT)
        sizes = grp[size_col].tolist() if size_col else [10] * len(grp)
        fig.add_trace(go.Scatter(
            x=grp["pmi"].tolist(),
            y=grp["bdi"].tolist(),
            mode="markers",
            name=q,
            marker=dict(
                color=color,
                size=sizes,
                opacity=0.80,
                line=dict(color="rgba(255,255,255,0.25)", width=1),
            ),
            text=[
                str(d.year) + "-" + str(d.month).zfill(2)
                for d in grp["date"]
            ],
            hovertemplate=(
                pmi_label + ": %{x:.1f}<br>"
                "BDI: %{y:,.0f}<br>"
                "Date: %{text}<extra>" + q + "</extra>"
            ),
        ))

    # ── regression line ─────────────────────────────────────────────────────
    x_arr = merged["pmi"].values.astype(float)
    y_arr = merged["bdi"].values.astype(float)
    y_hat_sorted, slope, intercept, r2 = _regression_line(x_arr, y_arr)

    if len(y_hat_sorted) > 0:
        x_sorted = np.sort(x_arr[np.isfinite(x_arr) & np.isfinite(y_arr)])
        direction = "positive" if slope > 0 else "negative"
        fig.add_trace(go.Scatter(
            x=x_sorted.tolist(),
            y=y_hat_sorted.tolist(),
            mode="lines",
            name="Regression",
            line=dict(color="rgba(255,255,255,0.45)", width=2, dash="dash"),
            hoverinfo="skip",
        ))

        # R² annotation
        r2_text = "R\u00b2 = " + f"{r2:.3f}" + " | slope = " + f"{slope:.1f}" + " (" + direction + ")"
        fig.add_annotation(
            x=0.99, y=0.97,
            xref="paper", yref="paper",
            text=r2_text,
            showarrow=False,
            align="right",
            xanchor="right",
            yanchor="top",
            font=dict(size=11, color=C_TEXT2),
            bgcolor="rgba(26,34,53,0.85)",
            bordercolor="rgba(255,255,255,0.10)",
            borderpad=6,
        )

    # ── PMI=50 vertical reference ───────────────────────────────────────────
    pmi_min = float(merged["pmi"].min())
    pmi_max = float(merged["pmi"].max())
    if pmi_min < 50 < pmi_max:
        fig.add_vline(
            x=50,
            line_dash="dot",
            line_color="rgba(255,255,255,0.25)",
            line_width=1,
            annotation_text="PMI = 50",
            annotation_position="top",
            annotation_font=dict(size=9, color=C_TEXT3),
        )

    # ── strategic note ──────────────────────────────────────────────────────
    fig.add_annotation(
        x=0.01, y=0.03,
        xref="paper", yref="paper",
        text="PMI > 50 + BDI rising = historically best time to be long shipping",
        showarrow=False,
        align="left",
        xanchor="left",
        yanchor="bottom",
        font=dict(size=10, color=C_MOD),
        bgcolor="rgba(26,34,53,0.75)",
        borderpad=4,
    )

    fig.update_layout(
        template="plotly_dark",
        height=440,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        xaxis=dict(
            title=pmi_label,
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        yaxis=dict(
            title="Baltic Dry Index (BDI)",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=11), bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(family="Inter, sans-serif"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # caption below
    size_note = " · Point size ∝ Import Volume (IMPGS)" if size_col else ""
    st.caption(
        "Color = calendar quarter" + size_note + " · Dashed line = OLS regression"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    macro_data: dict[str, pd.DataFrame] | None,
    freight_data: dict[str, pd.DataFrame] | None = None,
    stock_data: dict[str, pd.DataFrame] | None = None,
) -> None:
    """Render the full Macro Economics dashboard tab.

    Parameters
    ----------
    macro_data:
        Dict mapping FRED series_id -> normalized DataFrame with at minimum
        'date' and 'value' columns (output of fred_feed.fetch_macro_series).
    freight_data:
        Optional dict of freight-rate DataFrames (e.g., spot rates by route).
        Currently used only for future extensions.
    stock_data:
        Optional dict of shipping stock DataFrames. Currently unused but
        accepted for a consistent call signature.
    """
    macro_data = macro_data or {}

    if not macro_data:
        st.warning(
            "No macro data loaded. "
            "Set FRED_API_KEY and ensure fredapi is installed."
        )
        return

    n_loaded = len(macro_data)
    logger.info("tab_macro: rendering with " + str(n_loaded) + " FRED series")

    # ── Section 1: Macro Dashboard Grid ─────────────────────────────────────
    _render_macro_grid(macro_data)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 2: Yield Curve ───────────────────────────────────────────────
    _render_yield_curve(macro_data)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 3: Trade Balance Tracker ────────────────────────────────────
    _render_trade_balance(macro_data)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 4: Commodity Price Monitor ──────────────────────────────────
    _render_commodity_monitor(macro_data)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # ── Section 5: ISM/PMI vs Freight Rates ─────────────────────────────────
    _render_pmi_vs_freight(macro_data, freight_data)
