from __future__ import annotations

import datetime
import random
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── colour palette ─────────────────────────────────────────────────────────────
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
_MONO     = "'SF Mono','Menlo','Courier New',Courier,monospace"

# ── mock signal data ────────────────────────────────────────────────────────────
_MOCK_SIGNALS = [
    ("BDI",       "MOMENTUM",        "HIGH",     "LONG",  "+4.2%", "2h ago",  "BDI printed 3-week high; capesize led"),
    ("SCFI",      "MEAN REVERSION",  "MODERATE", "SHORT", "-1.8%", "4h ago",  "SCFI overbought on spot-contract spread"),
    ("WCI",       "MACRO OVERLAY",   "HIGH",     "LONG",  "+2.9%", "1h ago",  "Surge in booked TEUs ex-Shanghai"),
    ("CCFI",      "MOMENTUM",        "MODERATE", "LONG",  "+1.4%", "6h ago",  "CCFI holding above 200-day MA"),
    ("ZIM",       "BDI DIVERGENCE",  "HIGH",     "SHORT", "-3.1%", "30m ago", "Stock lagging BDI rally; fade opportunity"),
    ("MATX",      "MOMENTUM",        "MODERATE", "LONG",  "+2.2%", "3h ago",  "Trans-Pacific volume uptick"),
    ("SBLK",      "MEAN REVERSION",  "LOW",      "LONG",  "+0.6%", "5h ago",  "Panamax spot mean reversion signal"),
    ("GOGL",      "MACRO OVERLAY",   "MODERATE", "SHORT", "-2.4%", "2h ago",  "Iron ore demand softening"),
    ("DAC",       "MOMENTUM",        "HIGH",     "LONG",  "+5.1%", "1h ago",  "Containership charter rates accelerating"),
    ("FBX01",     "BDI DIVERGENCE",  "HIGH",     "LONG",  "+3.8%", "45m ago", "Trans-Pac spot diverged from futures"),
    ("FBX11",     "MOMENTUM",        "MODERATE", "SHORT", "-1.2%", "3h ago",  "Europe-Asia backhaul softening"),
    ("CAPESIZE",  "MOMENTUM",        "HIGH",     "LONG",  "+6.3%", "1h ago",  "Capesize TCE printing multi-month high"),
    ("PANAMAX",   "MEAN REVERSION",  "MODERATE", "LONG",  "+1.7%", "2h ago",  "Panamax reverting from oversold"),
    ("SUPRAMAX",  "MACRO OVERLAY",   "LOW",      "SHORT", "-0.9%", "7h ago",  "Minor bulker demand soft amid PMI miss"),
    ("HANDYSIZE", "MOMENTUM",        "LOW",      "LONG",  "+0.4%", "8h ago",  "Modest handysize improvement"),
    ("OIL_USO",   "MACRO OVERLAY",   "HIGH",     "SHORT", "-2.7%", "1h ago",  "Oil selloff pressuring tanker margins"),
    ("DXY",       "MACRO OVERLAY",   "MODERATE", "SHORT", "-1.1%", "2h ago",  "Dollar weakening supports commodity trade"),
    ("SPY",       "MACRO OVERLAY",   "MODERATE", "LONG",  "+0.8%", "4h ago",  "Risk-on supports shipping equities"),
    ("GOLD",      "MEAN REVERSION",  "LOW",      "SHORT", "-0.5%", "6h ago",  "Gold overbought; risk appetite improving"),
    ("CMRE",      "BDI DIVERGENCE",  "HIGH",     "LONG",  "+4.7%", "2h ago",  "CMRE charter backlog expanding"),
    ("EGLE",      "MOMENTUM",        "MODERATE", "LONG",  "+2.1%", "3h ago",  "Dry bulk equity momentum intact"),
    ("GNK",       "MEAN REVERSION",  "MODERATE", "LONG",  "+1.9%", "5h ago",  "GNK cheap vs. capesize TCE"),
    ("VLCC",      "MACRO OVERLAY",   "HIGH",     "SHORT", "-3.4%", "1h ago",  "VLCC rates rolling over on OPEC cuts"),
    ("AFRAMAX",   "BDI DIVERGENCE",  "MODERATE", "LONG",  "+2.6%", "3h ago",  "Aframax outperforming VLCC; structural"),
    ("MR_TANKER", "MOMENTUM",        "LOW",      "LONG",  "+0.7%", "6h ago",  "Product tanker rates stable"),
    ("BALTIC_C5", "MOMENTUM",        "HIGH",     "LONG",  "+5.8%", "30m ago", "C5 route flush with iron ore cargo"),
    ("BALTIC_C3", "MEAN REVERSION",  "MODERATE", "SHORT", "-1.6%", "4h ago",  "C3 route vol compressing"),
]

_ROUTES = [
    "Shanghai → LA",
    "Shanghai → Rotterdam",
    "Rotterdam → NY",
    "Singapore → Rotterdam",
    "Houston → Rotterdam",
    "Dubai → Shanghai",
    "Santos → Rotterdam",
    "Dampier → Qingdao",
    "Richards Bay → Qingdao",
    "New Orleans → Yokohama",
    "Durban → Rotterdam",
    "Corpus Christi → Rotterdam",
]

_INDICES = ["BDI", "WCI", "SCFI", "CCFI"]

_ASSET_PAIRS = [
    ("BDI",  "S&P 500"),
    ("BDI",  "Gold"),
    ("BDI",  "USD Index"),
    ("BDI",  "Oil (WTI)"),
    ("WCI",  "S&P 500"),
    ("WCI",  "Gold"),
    ("WCI",  "USD Index"),
    ("WCI",  "Oil (WTI)"),
    ("SCFI", "S&P 500"),
    ("SCFI", "Gold"),
    ("SCFI", "USD Index"),
    ("SCFI", "Oil (WTI)"),
]

# ── helpers ─────────────────────────────────────────────────────────────────────

def _conviction_badge(level: str) -> str:
    if level == "HIGH":
        color = C_HIGH
    elif level == "MODERATE":
        color = C_MOD
    else:
        color = C_TEXT3
    return (
        f"<span style='color:{color};font-weight:700;font-size:11px;"
        f"letter-spacing:0.08em'>{level}</span>"
    )


def _direction_cell(direction: str) -> str:
    if direction == "LONG":
        arrow, color = "↑", C_HIGH
    else:
        arrow, color = "↓", C_LOW
    return (
        f"<span style='color:{color};font-weight:700;font-size:12px'>"
        f"{arrow} {direction}</span>"
    )


def _change_cell(change: str) -> str:
    color = C_HIGH if change.startswith("+") else C_LOW
    return (
        f"<span style='font-family:{_MONO};font-size:12px;color:{color}'>{change}</span>"
    )


def _posture_from_signals(signals: list) -> tuple[str, str]:
    longs  = sum(1 for s in signals if s[3] == "LONG")
    shorts = sum(1 for s in signals if s[3] == "SHORT")
    ratio  = longs / max(longs + shorts, 1)
    if ratio >= 0.65:
        return "BULLISH", C_HIGH
    if ratio <= 0.35:
        return "BEARISH", C_LOW
    if 0.45 <= ratio <= 0.55:
        return "NEUTRAL", C_TEXT2
    return "MIXED", C_MOD


def _cell_bg(val: float) -> str:
    """Interpolate between deep-red and deep-green for heatmap cells."""
    clamped = max(-0.15, min(0.15, val))
    if clamped >= 0:
        t = clamped / 0.15
        r = int(26  + t * (16  - 26))
        g = int(34  + t * (185 - 34))
        b = int(53  + t * (129 - 53))
    else:
        t = abs(clamped) / 0.15
        r = int(26  + t * (239 - 26))
        g = int(34  + t * (68  - 34))
        b = int(53  + t * (68  - 53))
    return f"rgb({r},{g},{b})"


def _corr_bg(val: float) -> str:
    """Interpolate between deep-red (−1) through neutral (0) to deep-green (+1)."""
    if val >= 0:
        t = val
        r = int(26  + t * (16  - 26))
        g = int(34  + t * (185 - 34))
        b = int(53  + t * (129 - 53))
    else:
        t = abs(val)
        r = int(26  + t * (239 - 26))
        g = int(34  + t * (68  - 34))
        b = int(53  + t * (68  - 53))
    return f"rgb({r},{g},{b})"


def _section_title(text: str, subtitle: str = "") -> str:
    sub_html = (
        f"<div style='font-size:12px;color:{C_TEXT3};margin-top:2px'>{subtitle}</div>"
        if subtitle else ""
    )
    return (
        f"<div style='margin:28px 0 14px'>"
        f"<div style='font-size:15px;font-weight:700;color:{C_TEXT};letter-spacing:0.04em'>"
        f"{text}</div>{sub_html}</div>"
    )


def _card_wrap(inner: str, padding: str = "20px 24px") -> str:
    return (
        f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
        f"border-radius:12px;padding:{padding};margin-bottom:4px'>"
        f"{inner}</div>"
    )


# ── section 1: signal intelligence hero ────────────────────────────────────────

def _render_signal_hero(signals: list) -> None:
    try:
        total = len(signals)
        high_n  = sum(1 for s in signals if s[2] == "HIGH")
        mod_n   = sum(1 for s in signals if s[2] == "MODERATE")
        low_n   = sum(1 for s in signals if s[2] == "LOW")

        posture, posture_color = _posture_from_signals(signals)

        # mock trend
        rng = random.Random(42)
        new_today = rng.randint(3, 18)
        trend_sign = "↑" if new_today > 8 else "↓"
        trend_color = C_HIGH if new_today > 8 else C_LOW

        pill_style_base = (
            "display:inline-block;padding:4px 14px;border-radius:20px;"
            "font-size:12px;font-weight:700;letter-spacing:0.08em;margin-right:8px"
        )

        inner = (
            f"<div style='display:flex;align-items:center;justify-content:space-between;"
            f"flex-wrap:wrap;gap:16px'>"

            # left: posture
            f"<div>"
            f"<div style='font-size:11px;color:{C_TEXT3};letter-spacing:0.12em;"
            f"text-transform:uppercase;margin-bottom:6px'>Market Posture</div>"
            f"<div style='font-size:36px;font-weight:800;color:{posture_color};"
            f"letter-spacing:0.06em;line-height:1'>{posture}</div>"
            f"<div style='font-size:13px;color:{trend_color};margin-top:8px'>"
            f"{trend_sign} {new_today} new signals vs yesterday</div>"
            f"</div>"

            # right: pills + total
            f"<div style='text-align:right'>"
            f"<div style='font-size:11px;color:{C_TEXT3};letter-spacing:0.12em;"
            f"text-transform:uppercase;margin-bottom:10px'>Signal Breakdown</div>"
            f"<div style='margin-bottom:10px'>"
            f"<span style='{pill_style_base};background:rgba(16,185,129,0.15);"
            f"color:{C_HIGH};border:1px solid rgba(16,185,129,0.35)'>HIGH {high_n}</span>"
            f"<span style='{pill_style_base};background:rgba(245,158,11,0.15);"
            f"color:{C_MOD};border:1px solid rgba(245,158,11,0.35)'>MOD {mod_n}</span>"
            f"<span style='{pill_style_base};background:rgba(100,116,139,0.15);"
            f"color:{C_TEXT2};border:1px solid rgba(100,116,139,0.3)'>LOW {low_n}</span>"
            f"</div>"
            f"<div style='font-size:28px;font-weight:700;color:{C_TEXT}'>{total}"
            f"<span style='font-size:13px;color:{C_TEXT3};margin-left:6px'>total signals</span>"
            f"</div></div>"

            f"</div>"
        )

        st.markdown(_card_wrap(inner, "24px 28px"), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"signal hero error: {exc}")
        st.warning("Signal hero unavailable.")


# ── section 2: signal table ─────────────────────────────────────────────────────

def _render_signal_table(signals: list) -> None:
    try:
        th_style = (
            f"padding:10px 14px;text-align:left;font-size:10px;font-weight:700;"
            f"letter-spacing:0.1em;color:{C_TEXT3};text-transform:uppercase;"
            f"border-bottom:1px solid {C_BORDER};white-space:nowrap"
        )
        td_style = (
            f"padding:10px 14px;font-size:12px;color:{C_TEXT2};"
            f"border-bottom:1px solid rgba(255,255,255,0.04);vertical-align:middle"
        )

        header = (
            f"<thead><tr>"
            f"<th style='{th_style}'>Instrument</th>"
            f"<th style='{th_style}'>Signal</th>"
            f"<th style='{th_style}'>Conviction</th>"
            f"<th style='{th_style}'>Direction</th>"
            f"<th style='{th_style}'>Change</th>"
            f"<th style='{th_style}'>Time</th>"
            f"<th style='{th_style}'>Basis</th>"
            f"</tr></thead>"
        )

        rows = []
        for sig in signals:
            instrument, signal_type, conviction, direction, change, time_ago, basis = sig
            instr_html = (
                f"<span style='color:{C_TEXT};font-weight:700;font-size:12px;"
                f"font-family:{_MONO}'>{instrument}</span>"
            )
            sig_html = (
                f"<span style='font-size:11px;color:{C_ACCENT};font-weight:600;"
                f"letter-spacing:0.05em'>{signal_type}</span>"
            )
            time_html = (
                f"<span style='font-size:11px;color:{C_TEXT3}'>{time_ago}</span>"
            )
            basis_html = (
                f"<span style='font-size:11px;color:{C_TEXT3}'>{basis}</span>"
            )
            rows.append(
                f"<tr>"
                f"<td style='{td_style}'>{instr_html}</td>"
                f"<td style='{td_style}'>{sig_html}</td>"
                f"<td style='{td_style}'>{_conviction_badge(conviction)}</td>"
                f"<td style='{td_style}'>{_direction_cell(direction)}</td>"
                f"<td style='{td_style}'>{_change_cell(change)}</td>"
                f"<td style='{td_style}'>{time_html}</td>"
                f"<td style='{td_style}'>{basis_html}</td>"
                f"</tr>"
            )

        body = f"<tbody>{''.join(rows)}</tbody>"
        table = (
            f"<div style='overflow-x:auto'>"
            f"<table style='width:100%;border-collapse:collapse;background:{C_CARD};"
            f"border-radius:10px;overflow:hidden'>"
            f"{header}{body}"
            f"</table></div>"
        )
        st.markdown(table, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"signal table error: {exc}")
        st.warning("Signal table unavailable.")


# ── section 3: multi-index performance chart ────────────────────────────────────

def _render_multi_index_chart() -> None:
    try:
        rng = np.random.default_rng(7)
        days = pd.date_range(end=pd.Timestamp.today(), periods=90, freq="B")

        def _gen_index(seed_val: float, vol: float) -> np.ndarray:
            returns = rng.normal(0, vol, len(days))
            prices  = 100 * np.cumprod(1 + returns)
            prices[0] = 100.0
            return prices

        series = {
            "BDI":  (_gen_index(1.0, 0.018), C_ACCENT),
            "WCI":  (_gen_index(1.0, 0.014), "#10b981"),
            "SCFI": (_gen_index(1.0, 0.012), "#f59e0b"),
            "CCFI": (_gen_index(1.0, 0.010), "#8b5cf6"),
        }

        fig = go.Figure()
        for name, (vals, color) in series.items():
            fig.add_trace(go.Scatter(
                x=list(days),
                y=vals,
                name=name,
                line=dict(color=color, width=2),
                hovertemplate=f"<b>{name}</b>: %{{y:.1f}}<extra></extra>",
            ))

        fig.add_hline(
            y=100, line_dash="dot", line_color="rgba(255,255,255,0.15)", line_width=1
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(
                orientation="h",
                yanchor="bottom", y=1.02,
                xanchor="right",  x=1,
                font=dict(size=11, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(
                showgrid=False,
                tickfont=dict(color=C_TEXT3, size=10),
                tickformat="%b %d",
            ),
            yaxis=dict(
                showgrid=True,
                gridcolor="rgba(255,255,255,0.05)",
                tickfont=dict(color=C_TEXT3, size=10),
                ticksuffix="",
            ),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        logger.warning(f"multi-index chart error: {exc}")
        st.warning("Multi-index chart unavailable.")


# ── section 4: freight rate heatmap ────────────────────────────────────────────

def _render_freight_heatmap(freight_data) -> None:
    try:
        rng = np.random.default_rng(99)
        n_weeks = 7
        week_labels = []
        today = pd.Timestamp.today()
        for i in range(n_weeks - 1, -1, -1):
            d = today - pd.Timedelta(weeks=i)
            week_labels.append(d.strftime("W%W %b %d"))

        # build rate change matrix (rows = routes, cols = weeks)
        changes: list[list[float]] = []
        for _ in _ROUTES:
            row = rng.normal(0.02, 0.06, n_weeks).tolist()
            changes.append(row)

        th_style = (
            f"padding:8px 12px;font-size:10px;font-weight:700;letter-spacing:0.08em;"
            f"color:{C_TEXT3};text-transform:uppercase;text-align:center;"
            f"border-bottom:1px solid {C_BORDER}"
        )
        route_td = (
            f"padding:8px 12px;font-size:11px;color:{C_TEXT2};font-weight:600;"
            f"white-space:nowrap;border-bottom:1px solid rgba(255,255,255,0.04)"
        )

        header_cells = "".join(
            f"<th style='{th_style}'>{w}</th>" for w in week_labels
        )
        header = (
            f"<thead><tr>"
            f"<th style='{th_style};text-align:left'>Route</th>"
            f"{header_cells}"
            f"</tr></thead>"
        )

        rows = []
        for route, row_vals in zip(_ROUTES, changes):
            cells = ""
            for v in row_vals:
                bg  = _cell_bg(v)
                pct = f"{v*100:+.1f}%"
                txt_color = C_TEXT if abs(v) > 0.06 else C_TEXT2
                cells += (
                    f"<td style='padding:8px 10px;text-align:center;background:{bg};"
                    f"font-family:{_MONO};font-size:11px;color:{txt_color};"
                    f"border-bottom:1px solid rgba(255,255,255,0.04)'>{pct}</td>"
                )
            rows.append(
                f"<tr>"
                f"<td style='{route_td}'>{route}</td>"
                f"{cells}</tr>"
            )

        body = f"<tbody>{''.join(rows)}</tbody>"
        table = (
            f"<div style='overflow-x:auto'>"
            f"<table style='width:100%;border-collapse:collapse;background:{C_CARD};"
            f"border-radius:10px;overflow:hidden'>"
            f"{header}{body}"
            f"</table></div>"
        )
        st.markdown(table, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"freight heatmap error: {exc}")
        st.warning("Freight heatmap unavailable.")


# ── section 5: correlation matrix ──────────────────────────────────────────────

def _render_correlation_matrix() -> None:
    try:
        rng = np.random.default_rng(17)
        shipping_indices = ["BDI", "WCI", "SCFI", "CCFI"]
        macro_assets     = ["S&P 500", "Gold", "USD Index", "Oil (WTI)"]

        # realistic-ish mock correlations
        corr_mock = np.array([
            [ 0.42,  0.18, -0.31,  0.55],
            [ 0.38,  0.22, -0.27,  0.49],
            [ 0.51,  0.09, -0.38,  0.44],
            [ 0.46,  0.14, -0.29,  0.51],
        ])
        # add small noise
        corr_mock = np.clip(corr_mock + rng.normal(0, 0.04, corr_mock.shape), -1, 1)

        th_style = (
            f"padding:10px 14px;font-size:10px;font-weight:700;letter-spacing:0.08em;"
            f"color:{C_TEXT3};text-transform:uppercase;text-align:center;"
            f"border-bottom:1px solid {C_BORDER}"
        )
        row_header_style = (
            f"padding:10px 14px;font-size:11px;font-weight:700;color:{C_TEXT2};"
            f"font-family:{_MONO};border-bottom:1px solid rgba(255,255,255,0.04);"
            f"white-space:nowrap"
        )

        asset_headers = "".join(
            f"<th style='{th_style}'>{a}</th>" for a in macro_assets
        )
        header = (
            f"<thead><tr>"
            f"<th style='{th_style};text-align:left'>Index</th>"
            f"{asset_headers}"
            f"</tr></thead>"
        )

        rows = []
        for i, idx in enumerate(shipping_indices):
            cells = ""
            for j in range(len(macro_assets)):
                v   = float(corr_mock[i, j])
                bg  = _corr_bg(v)
                txt_color = C_TEXT if abs(v) > 0.3 else C_TEXT2
                cells += (
                    f"<td style='padding:10px 14px;text-align:center;background:{bg};"
                    f"font-family:{_MONO};font-size:12px;color:{txt_color};"
                    f"font-weight:600;border-bottom:1px solid rgba(255,255,255,0.04)'>"
                    f"{v:+.2f}</td>"
                )
            rows.append(
                f"<tr>"
                f"<td style='{row_header_style}'>{idx}</td>"
                f"{cells}</tr>"
            )

        body = f"<tbody>{''.join(rows)}</tbody>"
        legend_items = [
            (C_HIGH,  "+1.0 = perfect positive"),
            (C_TEXT3, " 0.0 = no correlation"),
            (C_LOW,   "-1.0 = perfect negative"),
        ]
        legend_html = "".join(
            f"<span style='margin-right:16px;font-size:11px;color:{c}'>{l}</span>"
            for c, l in legend_items
        )
        table = (
            f"<div style='overflow-x:auto'>"
            f"<table style='width:100%;border-collapse:collapse;background:{C_CARD};"
            f"border-radius:10px;overflow:hidden'>"
            f"{header}<tbody>{''.join(rows)}</tbody>"
            f"</table>"
            f"<div style='margin-top:10px;padding:0 4px'>{legend_html}</div>"
            f"</div>"
        )
        st.markdown(table, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"correlation matrix error: {exc}")
        st.warning("Correlation matrix unavailable.")


# ── section 6: conviction distribution chart ────────────────────────────────────

def _render_conviction_chart(signals: list) -> None:
    try:
        counts = {
            "HIGH":     sum(1 for s in signals if s[2] == "HIGH"),
            "MODERATE": sum(1 for s in signals if s[2] == "MODERATE"),
            "LOW":      sum(1 for s in signals if s[2] == "LOW"),
        }
        labels = list(counts.keys())
        values = list(counts.values())
        colors = [C_HIGH, C_MOD, C_TEXT3]

        fig = go.Figure(go.Bar(
            y=labels,
            x=values,
            orientation="h",
            marker=dict(color=colors, line=dict(width=0)),
            text=[str(v) for v in values],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=12),
            hovertemplate="<b>%{y}</b>: %{x} signals<extra></extra>",
        ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            height=180,
            margin=dict(l=0, r=40, t=10, b=0),
            xaxis=dict(
                showgrid=True,
                gridcolor="rgba(255,255,255,0.05)",
                tickfont=dict(color=C_TEXT3, size=10),
                range=[0, max(values) * 1.25],
            ),
            yaxis=dict(
                tickfont=dict(color=C_TEXT2, size=12, family=_MONO),
                showgrid=False,
            ),
            showlegend=False,
            bargap=0.35,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        logger.warning(f"conviction chart error: {exc}")
        st.warning("Conviction chart unavailable.")


# ── main entry point ─────────────────────────────────────────────────────────────

def render(stock_data, macro_data, insights, freight_data=None) -> None:
    """Institutional markets & signals dashboard."""

    # inject page-level CSS once
    st.markdown(
        f"<style>"
        f"[data-testid='stAppViewContainer'] {{background:{C_BG}}}"
        f"[data-testid='block-container'] {{padding-top:1rem}}"
        f"</style>",
        unsafe_allow_html=True,
    )

    # resolve signals: prefer live insights, fallback to mock
    signals: list = _MOCK_SIGNALS
    try:
        if insights and hasattr(insights, "__iter__"):
            live = []
            for item in insights:
                try:
                    sig = (
                        str(getattr(item, "ticker",    item.get("ticker",    "UNK"))),
                        str(getattr(item, "signal",    item.get("signal",    "MOMENTUM"))),
                        str(getattr(item, "conviction",item.get("conviction","MODERATE"))).upper(),
                        str(getattr(item, "direction", item.get("direction", "LONG"))).upper(),
                        str(getattr(item, "change",    item.get("change",    "—"))),
                        str(getattr(item, "time_ago",  item.get("time_ago",  "—"))),
                        str(getattr(item, "basis",     item.get("basis",     "—"))),
                    )
                    live.append(sig)
                except Exception:
                    pass
            if len(live) >= 5:
                signals = live
    except Exception as exc:
        logger.debug(f"insights parse skipped: {exc}")

    # ── hero ────────────────────────────────────────────────────────────────────
    try:
        st.markdown(
            f"<div style='margin-bottom:20px'>"
            f"<div style='font-size:11px;color:{C_TEXT3};letter-spacing:0.14em;"
            f"text-transform:uppercase;margin-bottom:4px'>Signal Intelligence</div>"
            f"<div style='font-size:22px;font-weight:800;color:{C_TEXT}'>"
            f"Markets &amp; Signals Dashboard</div>"
            f"<div style='font-size:12px;color:{C_TEXT3};margin-top:2px'>"
            f"Live signal monitoring across shipping indices, routes, and equities</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"page header error: {exc}")

    # ── section 1: signal hero ──────────────────────────────────────────────────
    try:
        _render_signal_hero(signals)
    except Exception as exc:
        logger.error(f"signal hero section failed: {exc}")

    # ── section 2: signal table ─────────────────────────────────────────────────
    try:
        st.markdown(
            _section_title(
                "Signal Intelligence Table",
                f"{len(signals)} active signals · sortable by conviction",
            ),
            unsafe_allow_html=True,
        )
        _render_signal_table(signals)
    except Exception as exc:
        logger.error(f"signal table section failed: {exc}")

    # ── section 3: multi-index performance ──────────────────────────────────────
    try:
        st.markdown(
            _section_title(
                "Multi-Index Performance",
                "BDI, WCI, SCFI, CCFI · indexed to 100 · trailing 90 trading days",
            ),
            unsafe_allow_html=True,
        )
        with st.container():
            st.markdown(
                f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                f"border-radius:12px;padding:16px 20px'>",
                unsafe_allow_html=True,
            )
            _render_multi_index_chart()
            st.markdown("</div>", unsafe_allow_html=True)
    except Exception as exc:
        logger.error(f"multi-index section failed: {exc}")

    # ── section 4 & 5 side-by-side ──────────────────────────────────────────────
    try:
        col_heat, col_corr = st.columns([3, 2], gap="medium")

        with col_heat:
            try:
                st.markdown(
                    _section_title(
                        "Freight Rate Heatmap",
                        "12 trade routes · weekly rate change · deep green = up, deep red = down",
                    ),
                    unsafe_allow_html=True,
                )
                _render_freight_heatmap(freight_data)
            except Exception as exc:
                logger.error(f"freight heatmap column failed: {exc}")

        with col_corr:
            try:
                st.markdown(
                    _section_title(
                        "Correlation Matrix",
                        "Shipping indices vs macro assets · 90-day rolling",
                    ),
                    unsafe_allow_html=True,
                )
                _render_correlation_matrix()
            except Exception as exc:
                logger.error(f"correlation matrix column failed: {exc}")
    except Exception as exc:
        logger.error(f"layout columns failed: {exc}")

    # ── section 6: conviction distribution ─────────────────────────────────────
    try:
        col_conv, col_meta = st.columns([2, 3], gap="medium")

        with col_conv:
            st.markdown(
                _section_title(
                    "Conviction Distribution",
                    "Signal count by confidence tier",
                ),
                unsafe_allow_html=True,
            )
            with st.container():
                st.markdown(
                    f"<div style='background:{C_CARD};border:1px solid {C_BORDER};"
                    f"border-radius:12px;padding:16px 20px'>",
                    unsafe_allow_html=True,
                )
                _render_conviction_chart(signals)
                st.markdown("</div>", unsafe_allow_html=True)

        with col_meta:
            try:
                st.markdown(
                    _section_title("Signal Type Breakdown", "Distribution by signal methodology"),
                    unsafe_allow_html=True,
                )
                type_counts: dict[str, int] = {}
                for sig in signals:
                    t = sig[1]
                    type_counts[t] = type_counts.get(t, 0) + 1

                type_colors = {
                    "MOMENTUM":       C_ACCENT,
                    "MEAN REVERSION": C_HIGH,
                    "BDI DIVERGENCE": C_MOD,
                    "MACRO OVERLAY":  "#8b5cf6",
                }

                rows_html = ""
                total_sigs = max(len(signals), 1)
                for stype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
                    pct    = count / total_sigs * 100
                    bar_c  = type_colors.get(stype, C_TEXT3)
                    rows_html += (
                        f"<div style='margin-bottom:14px'>"
                        f"<div style='display:flex;justify-content:space-between;"
                        f"margin-bottom:5px'>"
                        f"<span style='font-size:12px;color:{C_TEXT2};font-weight:600'>"
                        f"{stype}</span>"
                        f"<span style='font-size:12px;color:{C_TEXT3};font-family:{_MONO}'>"
                        f"{count} ({pct:.0f}%)</span>"
                        f"</div>"
                        f"<div style='height:6px;background:rgba(255,255,255,0.06);"
                        f"border-radius:3px;overflow:hidden'>"
                        f"<div style='height:100%;width:{pct:.1f}%;background:{bar_c};"
                        f"border-radius:3px;transition:width 0.4s ease'></div>"
                        f"</div></div>"
                    )

                st.markdown(
                    _card_wrap(rows_html, "20px 24px"),
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.warning(f"signal type breakdown error: {exc}")

    except Exception as exc:
        logger.error(f"conviction section failed: {exc}")

    # ── footer ──────────────────────────────────────────────────────────────────
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        st.markdown(
            f"<div style='margin-top:32px;padding:16px 0;border-top:1px solid {C_BORDER};"
            f"display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px'>"
            f"<span style='font-size:11px;color:{C_TEXT3}'>Markets &amp; Signals · "
            f"Institutional Dashboard</span>"
            f"<span style='font-size:11px;color:{C_TEXT3};font-family:{_MONO}'>"
            f"Last updated: {now}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"footer error: {exc}")
