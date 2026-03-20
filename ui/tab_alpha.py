"""Alpha Engine tab — multi-factor shipping stock alpha signals dashboard."""
from __future__ import annotations

import datetime
import random

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from engine.alpha_engine import (
    AlphaSignal,
    generate_all_signals,
    compute_portfolio_alpha,
    build_signal_scorecard,
)
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    section_header,
    _hex_to_rgba as _hex_rgba,
)


# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

_C_LONG    = "#10b981"   # green
_C_SHORT   = "#ef4444"   # red
_C_NEUTRAL = "#94a3b8"   # slate

_SIGNAL_TYPE_COLORS = {
    "MOMENTUM":       "#3b82f6",
    "MEAN_REVERSION": "#8b5cf6",
    "FUNDAMENTAL":    "#10b981",
    "MACRO":          "#06b6d4",
    "TECHNICAL":      "#f59e0b",
}

_CONVICTION_COLORS = {
    "HIGH":   "#10b981",
    "MEDIUM": "#f59e0b",
    "LOW":    "#94a3b8",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _direction_color(direction: str) -> str:
    return {"LONG": _C_LONG, "SHORT": _C_SHORT, "NEUTRAL": _C_NEUTRAL}.get(direction, _C_NEUTRAL)


def _signal_type_color(signal_type: str) -> str:
    return _SIGNAL_TYPE_COLORS.get(signal_type, "#64748b")


def _fmt_price(p: float) -> str:
    return "$" + str(round(p, 2))


def _fmt_pct(p: float) -> str:
    sign = "+" if p >= 0 else ""
    return sign + str(round(p, 1)) + "%"


# ---------------------------------------------------------------------------
# Dashboard header
# ---------------------------------------------------------------------------

def _render_alpha_header(signals: list) -> None:
    """Terminal-style dark header with live signal counts."""
    n_long    = sum(1 for s in signals if s.direction == "LONG")
    n_short   = sum(1 for s in signals if s.direction == "SHORT")
    n_neutral = sum(1 for s in signals if s.direction == "NEUTRAL")
    n_total   = len(signals)

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    ticker_items = ""
    for sig in signals[:8]:
        d_color = _direction_color(sig.direction)
        arrow = "+" if sig.direction == "LONG" else ("-" if sig.direction == "SHORT" else "~")
        ticker_items += (
            '<span style="display:inline-flex; align-items:center; gap:4px;'
            ' padding:0 14px; border-right:1px solid rgba(255,255,255,0.06);'
            ' white-space:nowrap">'
            '<span style="font-size:0.75rem; font-weight:800; color:#f1f5f9">' + sig.ticker + '</span>'
            '<span style="font-size:0.7rem; font-weight:600; color:' + d_color + '">'
            + arrow + str(round(sig.expected_return_pct, 1)) + '%'
            '</span>'
            '</span>'
        )

    st.markdown(
        '<div style="background:#050d1a; border:1px solid rgba(59,130,246,0.25);'
        ' border-radius:12px; padding:0; margin-bottom:20px; overflow:hidden">'

        # top bar
        '<div style="background:linear-gradient(90deg,#0d1b2e,#0a1628);'
        ' padding:14px 20px; display:flex; align-items:center; justify-content:space-between;'
        ' border-bottom:1px solid rgba(255,255,255,0.06)">'

        '<div style="display:flex; align-items:center; gap:12px">'
        '<div style="width:8px; height:8px; border-radius:50%; background:#10b981;'
        ' box-shadow:0 0 8px rgba(16,185,129,0.6); flex-shrink:0"></div>'
        '<span style="font-size:0.9rem; font-weight:900; color:#f1f5f9;'
        ' letter-spacing:0.14em; text-transform:uppercase; font-family:monospace">'
        'SHIPPING ALPHA ENGINE</span>'
        '</div>'

        '<div style="display:flex; align-items:center; gap:16px">'
        '<span style="font-size:0.72rem; font-weight:700; color:#64748b;'
        ' font-family:monospace">' + now_str + '</span>'
        '<span style="font-size:0.7rem; padding:3px 10px; border-radius:999px;'
        ' background:rgba(59,130,246,0.15); color:#60a5fa; font-weight:700">'
        + str(n_total) + ' SIGNALS'
        '</span>'
        '</div>'
        '</div>'

        # signal count row
        '<div style="padding:10px 20px; display:flex; align-items:center; gap:0;'
        ' background:#060e1c; border-bottom:1px solid rgba(255,255,255,0.04)">'
        '<span style="font-size:0.62rem; font-weight:800; color:#3b82f6;'
        ' text-transform:uppercase; letter-spacing:0.12em; padding-right:16px;'
        ' border-right:1px solid rgba(255,255,255,0.06); white-space:nowrap">SIGNALS</span>'

        '<div style="display:flex; align-items:center; gap:20px; padding-left:16px">'
        '<span style="font-size:0.85rem; font-weight:800; color:' + _C_LONG + '">'
        + str(n_long) + ' LONG</span>'
        '<span style="color:#334155; font-size:0.8rem">|</span>'
        '<span style="font-size:0.85rem; font-weight:800; color:' + _C_SHORT + '">'
        + str(n_short) + ' SHORT</span>'
        '<span style="color:#334155; font-size:0.8rem">|</span>'
        '<span style="font-size:0.85rem; font-weight:800; color:' + _C_NEUTRAL + '">'
        + str(n_neutral) + ' NEUTRAL</span>'
        '</div>'
        '</div>'

        # live ticker
        '<div style="background:#040b16; padding:8px 0; display:flex;'
        ' align-items:center; overflow-x:auto; gap:0">'
        '<span style="font-size:0.58rem; font-weight:800; color:#3b82f6;'
        ' text-transform:uppercase; letter-spacing:0.12em; padding:0 12px;'
        ' border-right:1px solid rgba(255,255,255,0.06); white-space:nowrap">LIVE</span>'
        + ticker_items +
        '</div>'

        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Top Signals Section
# ---------------------------------------------------------------------------

def _render_top_signals(signals: list) -> None:
    section_header("Top Alpha Signals", "Highest conviction opportunities — sorted by conviction + strength")

    top5 = signals[:5]
    if not top5:
        st.markdown(
            '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
            ' border-radius:10px; padding:24px; text-align:center">'
            '<div style="color:' + C_TEXT2 + '; font-size:0.9rem">No signals generated.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    for sig in top5:
        d_color  = _direction_color(sig.direction)
        st_color = _signal_type_color(sig.signal_type)
        cv_color = _CONVICTION_COLORS.get(sig.conviction, "#94a3b8")

        # Risk/reward bar widths (cap at 100px each)
        reward_pct = abs(sig.target_price - sig.entry_price) / max(sig.entry_price, 0.01) * 100
        risk_pct   = abs(sig.entry_price - sig.stop_loss)    / max(sig.entry_price, 0.01) * 100
        total = reward_pct + risk_pct if (reward_pct + risk_pct) > 0 else 1
        reward_bar = round(reward_pct / total * 120, 1)
        risk_bar   = round(risk_pct   / total * 120, 1)

        horizon_bg = {
            "1W": "rgba(249,115,22,0.15)", "1M": "rgba(59,130,246,0.12)",
            "3M": "rgba(139,92,246,0.12)",
        }.get(sig.time_horizon, "rgba(100,116,139,0.12)")
        horizon_col = {
            "1W": "#f97316", "1M": "#60a5fa", "3M": "#a78bfa",
        }.get(sig.time_horizon, "#94a3b8")

        # Pulsing conviction badge for HIGH
        pulse_style = (
            "animation:pulse-glow 2s ease-in-out infinite;" if sig.conviction == "HIGH" else ""
        )

        st.markdown(
            '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
            ' border-left:4px solid ' + d_color + ';'
            ' border-radius:12px; padding:18px 20px; margin-bottom:12px">'

            # Row 1: ticker badge + signal name + type badge
            '<div style="display:flex; align-items:center; gap:10px; margin-bottom:12px; flex-wrap:wrap">'

            '<span style="background:' + _hex_rgba(d_color, 0.18) + ';'
            ' color:' + d_color + '; border:1px solid ' + _hex_rgba(d_color, 0.4) + ';'
            ' padding:4px 12px; border-radius:6px; font-size:0.88rem;'
            ' font-weight:900; letter-spacing:0.06em; font-family:monospace">'
            + sig.ticker +
            '</span>'

            '<span style="font-size:0.95rem; font-weight:700; color:#f1f5f9; flex:1">'
            + sig.signal_name +
            '</span>'

            '<span style="background:' + _hex_rgba(st_color, 0.14) + '; color:' + st_color + ';'
            ' padding:3px 10px; border-radius:999px; font-size:0.65rem;'
            ' font-weight:700; text-transform:uppercase; letter-spacing:0.06em">'
            + sig.signal_type +
            '</span>'

            '<span style="background:' + horizon_bg + '; color:' + horizon_col + ';'
            ' padding:3px 10px; border-radius:999px; font-size:0.65rem;'
            ' font-weight:700; text-transform:uppercase">'
            + sig.time_horizon +
            '</span>'
            '</div>'

            # Row 2: Entry / Target / Stop prices
            '<div style="display:flex; gap:20px; margin-bottom:12px; flex-wrap:wrap">'

            '<div style="display:flex; flex-direction:column; gap:2px">'
            '<span style="font-size:0.62rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.07em">Entry</span>'
            '<span style="font-size:1.0rem; font-weight:800; color:#f1f5f9;'
            ' font-variant-numeric:tabular-nums">' + _fmt_price(sig.entry_price) + '</span>'
            '</div>'

            '<div style="display:flex; flex-direction:column; gap:2px">'
            '<span style="font-size:0.62rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.07em">Target</span>'
            '<span style="font-size:1.0rem; font-weight:800; color:' + _C_LONG + ';'
            ' font-variant-numeric:tabular-nums">' + _fmt_price(sig.target_price) + '</span>'
            '</div>'

            '<div style="display:flex; flex-direction:column; gap:2px">'
            '<span style="font-size:0.62rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.07em">Stop Loss</span>'
            '<span style="font-size:1.0rem; font-weight:800; color:' + _C_SHORT + ';'
            ' font-variant-numeric:tabular-nums">' + _fmt_price(sig.stop_loss) + '</span>'
            '</div>'

            '<div style="display:flex; flex-direction:column; gap:2px">'
            '<span style="font-size:0.62rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.07em">Expected Return</span>'
            '<span style="font-size:1.0rem; font-weight:800; color:' + d_color + ';'
            ' font-variant-numeric:tabular-nums">' + _fmt_pct(sig.expected_return_pct) + '</span>'
            '</div>'
            '</div>'

            # Row 3: Risk/reward bar
            '<div style="margin-bottom:10px">'
            '<div style="font-size:0.62rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px">'
            'Risk / Reward  ' + str(sig.risk_reward) + 'x'
            '</div>'
            '<div style="display:flex; align-items:center; gap:2px">'
            '<div style="height:8px; width:' + str(reward_bar) + 'px;'
            ' background:' + _C_LONG + '; border-radius:4px 0 0 4px; opacity:0.85"></div>'
            '<div style="height:8px; width:' + str(risk_bar) + 'px;'
            ' background:' + _C_SHORT + '; border-radius:0 4px 4px 0; opacity:0.85"></div>'
            '</div>'
            '</div>'

            # Row 4: Conviction + rationale
            '<div style="display:flex; align-items:flex-start; gap:10px">'
            '<span style="background:' + _hex_rgba(cv_color, 0.15) + ';'
            ' color:' + cv_color + '; border:1px solid ' + _hex_rgba(cv_color, 0.35) + ';'
            ' padding:2px 10px; border-radius:999px; font-size:0.65rem;'
            ' font-weight:700; white-space:nowrap; flex-shrink:0; ' + pulse_style + '">'
            + sig.conviction + ' CONVICTION'
            '</span>'
            '<span style="font-size:0.78rem; color:#94a3b8; line-height:1.55">'
            + sig.rationale +
            '</span>'
            '</div>'

            '</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Signal Matrix Heatmap
# ---------------------------------------------------------------------------

def _render_signal_matrix(signals: list) -> None:
    section_header("Signal Matrix", "Strength by ticker and signal type — green=LONG, red=SHORT, gray=neutral")

    if not signals:
        return

    tickers      = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]
    signal_types = ["MOMENTUM", "MEAN_REVERSION", "FUNDAMENTAL", "MACRO", "TECHNICAL"]

    # Build matrix: rows=tickers, cols=signal_types
    # Value: strength * sign (LONG=+, SHORT=-, NEUTRAL=0)
    matrix = np.zeros((len(tickers), len(signal_types)))
    for sig in signals:
        if sig.ticker not in tickers or sig.signal_type not in signal_types:
            continue
        ri = tickers.index(sig.ticker)
        ci = signal_types.index(sig.signal_type)
        sign = 1.0 if sig.direction == "LONG" else (-1.0 if sig.direction == "SHORT" else 0.0)
        matrix[ri, ci] = sign * sig.strength

    # Custom colorscale: red → gray → green
    colorscale = [
        [0.0,  "rgba(239,68,68,0.9)"],
        [0.35, "rgba(239,68,68,0.3)"],
        [0.5,  "rgba(71,85,105,0.4)"],
        [0.65, "rgba(16,185,129,0.3)"],
        [1.0,  "rgba(16,185,129,0.9)"],
    ]

    text_vals = []
    for row in matrix:
        text_row = []
        for v in row:
            if abs(v) < 0.05:
                text_row.append("")
            else:
                text_row.append(str(round(v, 2)))
        text_vals.append(text_row)

    fig = go.Figure(go.Heatmap(
        z=matrix,
        x=signal_types,
        y=tickers,
        colorscale=colorscale,
        zmid=0,
        zmin=-1,
        zmax=1,
        text=text_vals,
        texttemplate="%{text}",
        textfont=dict(size=11, color="#f1f5f9"),
        hovertemplate="Ticker: %{y}<br>Type: %{x}<br>Score: %{z:.3f}<extra></extra>",
        colorbar=dict(
            title=dict(text="Score", font=dict(color="#94a3b8", size=11)),
            tickfont=dict(color="#94a3b8", size=10),
            outlinewidth=0,
            thickness=14,
            tickvals=[-1, -0.5, 0, 0.5, 1],
            ticktext=["Strong SHORT", "Weak SHORT", "Neutral", "Weak LONG", "Strong LONG"],
        ),
    ))
    fig.update_layout(
        template="plotly_dark",
        height=320,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=120),
        font=dict(family="Inter, sans-serif"),
        xaxis=dict(tickfont=dict(size=10, color="#94a3b8"), side="top"),
        yaxis=dict(tickfont=dict(size=11, color="#f1f5f9")),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="alpha_signal_matrix")


# ---------------------------------------------------------------------------
# Portfolio Construction
# ---------------------------------------------------------------------------

def _render_portfolio_construction(
    signals: list,
    stock_data: dict,
    portfolio_alpha: dict,
) -> None:
    section_header(
        "Portfolio Construction",
        "Aggregated signal weights · $100k portfolio sizing",
    )

    weights  = portfolio_alpha.get("weights", {})
    exp_ret  = portfolio_alpha.get("expected_return", 0.0)
    port_vol = portfolio_alpha.get("portfolio_vol", 0.0)
    sharpe   = portfolio_alpha.get("sharpe", 0.0)
    max_dd   = portfolio_alpha.get("max_dd_estimate", 0.0)

    if not weights:
        st.info("No signals to construct portfolio from.")
        return

    col_pie, col_metrics = st.columns([1, 1])

    with col_pie:
        # Pie chart — LONG = green slice, SHORT = red slice
        pie_labels = []
        pie_values = []
        pie_colors = []
        for ticker, w in sorted(weights.items(), key=lambda x: -abs(x[1])):
            if abs(w) < 0.001:
                continue
            pie_labels.append(ticker + (" (L)" if w > 0 else " (S)"))
            pie_values.append(abs(w))
            pie_colors.append(_C_LONG if w > 0 else _C_SHORT)

        if pie_labels:
            fig_pie = go.Figure(go.Pie(
                labels=pie_labels,
                values=pie_values,
                marker=dict(
                    colors=pie_colors,
                    line=dict(color="#0d1117", width=2),
                ),
                textfont=dict(size=11, color="#f1f5f9"),
                hole=0.42,
                hovertemplate="%{label}<br>Weight: %{value:.1%}<extra></extra>",
            ))
            fig_pie.update_layout(
                template="plotly_dark",
                height=280,
                paper_bgcolor=C_CARD,
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=True,
                legend=dict(
                    font=dict(size=10, color="#94a3b8"),
                    bgcolor="rgba(0,0,0,0)",
                    orientation="v",
                    xanchor="right",
                    x=1,
                ),
                hoverlabel=dict(
                    bgcolor="#1a2235",
                    bordercolor="rgba(255,255,255,0.15)",
                    font=dict(color="#f1f5f9", size=11),
                ),
                annotations=[dict(
                    text="WEIGHTS",
                    x=0.5, y=0.5,
                    font=dict(size=10, color="#64748b", family="Inter, sans-serif"),
                    showarrow=False,
                )],
            )
            st.plotly_chart(fig_pie, use_container_width=True, key="alpha_portfolio_pie")

    with col_metrics:
        ret_color  = _C_LONG  if exp_ret  >= 0  else _C_SHORT
        vol_color  = _C_LOW   if port_vol > 35   else (C_MOD if port_vol > 20 else _C_LONG)
        shr_color  = _C_LONG  if sharpe   > 0.5  else (C_MOD if sharpe > 0 else _C_SHORT)
        dd_color   = _C_SHORT if max_dd   > 15   else (C_MOD if max_dd > 8 else _C_LONG)

        metrics_html = (
            '<div style="background:#0d1117; border:1px solid rgba(255,255,255,0.08);'
            ' border-radius:10px; padding:18px 20px; height:100%">'
            '<div style="font-size:0.65rem; font-weight:700; color:#64748b;'
            ' text-transform:uppercase; letter-spacing:0.1em; margin-bottom:16px">Portfolio Statistics</div>'
            '<div style="display:flex; flex-direction:column; gap:12px">'

            '<div style="display:flex; justify-content:space-between; align-items:center;'
            ' border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:10px">'
            '<span style="font-size:0.8rem; color:#94a3b8">Expected Return</span>'
            '<span style="font-size:1.1rem; font-weight:800; color:' + ret_color + ';'
            ' font-variant-numeric:tabular-nums">' + _fmt_pct(exp_ret) + '</span>'
            '</div>'

            '<div style="display:flex; justify-content:space-between; align-items:center;'
            ' border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:10px">'
            '<span style="font-size:0.8rem; color:#94a3b8">Portfolio Volatility</span>'
            '<span style="font-size:1.1rem; font-weight:800; color:' + vol_color + ';'
            ' font-variant-numeric:tabular-nums">' + str(round(port_vol, 1)) + '%</span>'
            '</div>'

            '<div style="display:flex; justify-content:space-between; align-items:center;'
            ' border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:10px">'
            '<span style="font-size:0.8rem; color:#94a3b8">Sharpe Ratio</span>'
            '<span style="font-size:1.1rem; font-weight:800; color:' + shr_color + ';'
            ' font-variant-numeric:tabular-nums">' + str(round(sharpe, 2)) + '</span>'
            '</div>'

            '<div style="display:flex; justify-content:space-between; align-items:center">'
            '<span style="font-size:0.8rem; color:#94a3b8">Max DD Estimate</span>'
            '<span style="font-size:1.1rem; font-weight:800; color:' + dd_color + ';'
            ' font-variant-numeric:tabular-nums">-' + str(round(max_dd, 1)) + '%</span>'
            '</div>'
            '</div></div>'
        )
        st.markdown(metrics_html, unsafe_allow_html=True)

    # Position sizing table ($100k portfolio)
    st.markdown(
        '<div style="font-size:0.68rem; font-weight:700; color:#64748b;'
        ' text-transform:uppercase; letter-spacing:0.08em; margin:16px 0 8px 0">'
        'Position Sizing — $100,000 Portfolio'
        '</div>',
        unsafe_allow_html=True,
    )

    sizing_rows = []
    for ticker, w in sorted(weights.items(), key=lambda x: -abs(x[1])):
        if abs(w) < 0.001:
            continue
        dollar_alloc = abs(w) * 100_000
        direction    = "LONG" if w > 0 else "SHORT"
        price        = None
        df           = stock_data.get(ticker)
        if df is not None and not df.empty and "close" in df.columns:
            vals = df["close"].dropna()
            if not vals.empty:
                price = float(vals.iloc[-1])
        shares = round(dollar_alloc / price, 0) if price and price > 0 else None
        sizing_rows.append({
            "Ticker":       ticker,
            "Direction":    direction,
            "Weight":       str(round(abs(w) * 100, 1)) + "%",
            "$ Allocation": "$" + format(int(dollar_alloc), ","),
            "Est. Shares":  str(int(shares)) if shares is not None else "N/A",
            "Entry Price":  ("$" + str(round(price, 2))) if price else "N/A",
        })

    if sizing_rows:
        st.dataframe(
            pd.DataFrame(sizing_rows),
            use_container_width=True,
            hide_index=True,
        )


# ---------------------------------------------------------------------------
# Factor Attribution
# ---------------------------------------------------------------------------

def _render_factor_attribution(signals: list) -> None:
    section_header(
        "Factor Attribution",
        "Which factors drive alpha for each ticker",
    )

    tickers      = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]
    signal_types = ["MOMENTUM", "MEAN_REVERSION", "FUNDAMENTAL", "MACRO", "TECHNICAL"]

    # Sum signed strength per (ticker, signal_type)
    scores: dict = {t: {st: 0.0 for st in signal_types} for t in tickers}
    for sig in signals:
        if sig.ticker not in scores or sig.signal_type not in signal_types:
            continue
        sign = 1.0 if sig.direction == "LONG" else (-1.0 if sig.direction == "SHORT" else 0.0)
        scores[sig.ticker][sig.signal_type] += sign * sig.strength

    fig = go.Figure()
    for stype in signal_types:
        vals    = [scores[t][stype] for t in tickers]
        s_color = _SIGNAL_TYPE_COLORS.get(stype, "#64748b")
        fig.add_trace(go.Bar(
            name=stype,
            x=tickers,
            y=vals,
            marker_color=s_color,
            opacity=0.85,
            hovertemplate="%{x} — " + stype + ": %{y:.3f}<extra></extra>",
        ))

    fig.update_layout(
        template="plotly_dark",
        barmode="stack",
        height=320,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        font=dict(family="Inter, sans-serif"),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=10, color="#94a3b8"),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(tickfont=dict(size=11, color="#f1f5f9")),
        yaxis=dict(
            title="Alpha Score",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.2)",
            zerolinewidth=1,
            tickfont=dict(size=10, color="#64748b"),
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="alpha_factor_attribution")


# ---------------------------------------------------------------------------
# Signal History (Simulated Backtest)
# ---------------------------------------------------------------------------

def _render_signal_history(signals: list, stock_data: dict) -> None:
    section_header(
        "Signal History — Simulated Backtest",
        "90-day synthetic backtested returns if signals had been followed",
    )

    tickers   = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]
    n_days    = 90
    today     = datetime.date.today()
    dates     = [today - datetime.timedelta(days=n_days - i) for i in range(n_days)]

    fig = go.Figure()

    rng = random.Random(42)
    COLORS = [
        "#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444",
    ]

    for idx, ticker in enumerate(tickers):
        # Check if ticker has any signal
        ticker_sigs = [s for s in signals if s.ticker == ticker]

        # Build synthetic daily PnL
        # Base: random walk shaped to match signal direction
        net_direction = 0.0
        for s in ticker_sigs:
            sign = 1.0 if s.direction == "LONG" else (-1.0 if s.direction == "SHORT" else 0.0)
            net_direction += sign * s.strength

        # Annual drift based on signal
        annual_drift = net_direction * 30  # pct
        daily_drift  = annual_drift / 252

        # Generate returns
        daily_vol = 0.025  # ~40% annualized
        cum_ret = [0.0]
        for _ in range(n_days - 1):
            r = rng.gauss(daily_drift / 100, daily_vol)
            cum_ret.append(cum_ret[-1] + r * 100)

        color = COLORS[idx % len(COLORS)]
        fig.add_trace(go.Scatter(
            x=dates,
            y=cum_ret,
            name=ticker,
            mode="lines",
            line=dict(color=color, width=2),
            hovertemplate=ticker + ": %{y:+.1f}%<extra></extra>",
        ))

    # Equal-weight portfolio line
    all_rets = []
    for _ in range(n_days):
        all_rets.append(0.0)

    for ticker in tickers:
        ticker_sigs = [s for s in signals if s.ticker == ticker]
        net_direction = sum(
            (1.0 if s.direction == "LONG" else (-1.0 if s.direction == "SHORT" else 0.0)) * s.strength
            for s in ticker_sigs
        )
        daily_drift = net_direction * 30 / 252 / 100
        rng2 = random.Random(hash(ticker) & 0xFFFF)
        cum = 0.0
        for i in range(n_days):
            r = rng2.gauss(daily_drift, 0.025)
            all_rets[i] += r * 100 / len(tickers)

    portfolio_cum = []
    running = 0.0
    for r in all_rets:
        running += r
        portfolio_cum.append(round(running, 3))

    fig.add_trace(go.Scatter(
        x=dates,
        y=portfolio_cum,
        name="Portfolio (EW)",
        mode="lines",
        line=dict(color="#ffffff", width=3, dash="dot"),
        hovertemplate="Portfolio: %{y:+.1f}%<extra></extra>",
    ))

    fig.add_hline(
        y=0,
        line_dash="dot",
        line_color="rgba(255,255,255,0.18)",
        line_width=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=380,
        paper_bgcolor=C_CARD,
        plot_bgcolor=C_CARD,
        margin=dict(t=20, b=20, l=10, r=10),
        font=dict(family="Inter, sans-serif"),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=10, color="#94a3b8"),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=10, color="#64748b"),
        ),
        yaxis=dict(
            title="Cumulative Return (%)",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            ticksuffix="%",
            tickfont=dict(size=10, color="#64748b"),
        ),
        hoverlabel=dict(
            bgcolor="#1a2235",
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color="#f1f5f9", size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="alpha_signal_history")

    st.markdown(
        '<div style="font-size:0.72rem; color:#475569; margin-top:-8px; margin-bottom:4px;'
        ' font-style:italic">'
        'Simulated returns use synthetic random walks calibrated to signal strength and direction. '
        'Not a guarantee of future performance. For illustrative purposes only.'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Scorecard Table
# ---------------------------------------------------------------------------

def _render_scorecard(signals: list) -> None:
    if not signals:
        return
    section_header("Full Signal Scorecard", "All signals sorted by conviction then strength")
    df = build_signal_scorecard(signals)
    if df.empty:
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(
    route_results: list,
    port_results: list,
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
    insights: list,
) -> None:
    """Render the Alpha Engine dashboard tab.

    Args:
        route_results:  list of RouteOpportunity objects
        port_results:   list of PortDemandResult objects
        freight_data:   dict[series_id -> DataFrame with 'value','date' columns]
        macro_data:     dict[series_id -> DataFrame with 'value','date' columns]
        stock_data:     dict[ticker -> DataFrame with 'close','date' columns]
        insights:       list of Insight objects (from InsightScorer)
    """
    logger.info("tab_alpha: rendering Alpha Engine dashboard")

    # --- Generate signals --------------------------------------------------
    try:
        signals = generate_all_signals(
            stock_data=stock_data or {},
            freight_data=freight_data or {},
            macro_data=macro_data or {},
            port_results=port_results or [],
            route_results=route_results or [],
        )
    except Exception as exc:
        logger.error("tab_alpha: signal generation failed: " + str(exc))
        signals = []

    # --- Compute portfolio alpha -------------------------------------------
    try:
        portfolio_alpha = compute_portfolio_alpha(signals, stock_data or {})
    except Exception as exc:
        logger.error("tab_alpha: portfolio alpha computation failed: " + str(exc))
        portfolio_alpha = {
            "weights": {}, "expected_return": 0.0,
            "portfolio_vol": 0.0, "sharpe": 0.0, "max_dd_estimate": 0.0,
        }

    # --- Header -----------------------------------------------------------
    _render_alpha_header(signals)

    # --- Section 1: Top Signals -------------------------------------------
    _render_top_signals(signals)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # --- Section 2: Signal Matrix -----------------------------------------
    _render_signal_matrix(signals)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # --- Section 3: Portfolio Construction --------------------------------
    _render_portfolio_construction(signals, stock_data or {}, portfolio_alpha)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # --- Section 4: Factor Attribution ------------------------------------
    _render_factor_attribution(signals)

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # --- Section 5: Signal History (Backtest) -----------------------------
    _render_signal_history(signals, stock_data or {})

    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )

    # --- Section 6: Full Scorecard ----------------------------------------
    _render_scorecard(signals)
