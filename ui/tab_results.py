from __future__ import annotations

import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from loguru import logger
from scipy import stats as scipy_stats

from ui.styles import (
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)


# ── Cell formatters for wsj_market_table() ────────────────────────────────
# wsj_market_table renders cell strings as raw HTML inside <td>. These helpers
# only style content (font + conditional color); table CSS handles alignment
# and rule lines. Mirrors the pattern in ui/tab_rate_analytics.py.

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )

SIGNAL_TYPES = [
    "Momentum", "Mean Reversion", "BDI Divergence", "Rate Breakout",
    "Congestion Play", "Macro Overlay", "Sentiment Shift", "Carrier Alpha",
]

SIGNAL_COLORS = {
    "Momentum":       "#3572b0",
    "Mean Reversion": "#7c6eaf",
    "BDI Divergence": "#2e9e6e",
    "Rate Breakout":  "#c9962b",
    "Congestion Play":"#c0392b",
    "Macro Overlay":  "#4a90a4",
    "Sentiment Shift":"#ec4899",
    "Carrier Alpha":  "#a3e635",
}

INSTRUMENTS = ["ZIM", "MATX", "DAC", "SBLK", "GOGL", "STNG",
               "TDRY", "GNK", "DSX", "CMRE"]

ROUTES = ["SHNG-ROTT", "SHNG-LOSA", "SING-ROTT", "BUEN-HBUR", "ROTT-NYBA"]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _card_wrap(inner: str, accent: str = C_ACCENT) -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-top:2px solid {accent};border-radius:6px;padding:20px 24px;'
        f'box-shadow:0 4px 24px rgba(0,0,0,0.25);margin-bottom:12px">'
        f'{inner}</div>'
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub = (f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-top:3px">'
           f'{subtitle}</div>') if subtitle else ""
    st.markdown(
        f'<div style="margin:28px 0 14px 0">'
        f'<div style="font-size:0.68rem;font-weight:800;color:{C_ACCENT};'
        f'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:4px">'
        f'ALPHA SIGNALS</div>'
        f'<div style="font-size:1.05rem;font-weight:800;color:{C_TEXT};'
        f'letter-spacing:-0.01em">{title}</div>{sub}</div>', unsafe_allow_html=True)


def _kpi(label: str, value: str, delta: str = "", accent: str = C_ACCENT) -> str:
    delta_html = (
        f'<div style="font-size:0.72rem;color:{C_HIGH if not delta.startswith("-") else C_LOW};'
        f'margin-top:4px">{delta}</div>'
    ) if delta else ""
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-top:2px solid {accent};border-radius:6px;padding:16px 18px">'
        f'<div style="font-size:0.68rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">{label}</div>'
        f'<div style="font-size:1.55rem;font-weight:800;color:{C_TEXT};'
        f'letter-spacing:-0.02em">{value}</div>'
        f'{delta_html}</div>'
    )


def _color_pct(v: float) -> str:
    return C_HIGH if v >= 0 else C_LOW


def _fmt_pct(v: float, decimals: int = 1) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


# ── Data generators ────────────────────────────────────────────────────────────

def _build_signal_log(insights, n: int = 300) -> pd.DataFrame:
    """Build a realistic mock signal log, enriching with real insights where possible."""
    rng = np.random.default_rng(42)
    rows = []
    base_date = datetime(2023, 1, 1)

    for i in range(n):
        sig_type = rng.choice(SIGNAL_TYPES)
        instrument = rng.choice(INSTRUMENTS + ROUTES)
        direction = rng.choice(["LONG", "SHORT"])
        conviction = float(rng.uniform(0.45, 0.98))
        days_ago = int(rng.integers(1, 500))
        entry_date = base_date + timedelta(days=(500 - days_ago))
        hold_days = int(rng.integers(1, 35))
        exit_date = entry_date + timedelta(days=hold_days)
        status = "CLOSED" if exit_date < datetime.now() else "OPEN"

        # Signal-type specific return distributions
        mu_map = {
            "Momentum": 1.8, "Mean Reversion": 1.4, "BDI Divergence": 2.1,
            "Rate Breakout": 2.6, "Congestion Play": 1.1, "Macro Overlay": 1.5,
            "Sentiment Shift": 0.9, "Carrier Alpha": 2.3,
        }
        mu = mu_map.get(sig_type, 1.5)
        ret = float(rng.normal(mu * (1 if direction == "LONG" else -0.3), 4.2))
        entry_px = float(rng.uniform(8, 65))
        exit_px = entry_px * (1 + ret / 100)

        rows.append({
            "date":       entry_date,
            "instrument": instrument,
            "signal_type":sig_type,
            "direction":  direction,
            "conviction": round(conviction, 2),
            "entry":      round(entry_px, 2),
            "exit":       round(exit_px, 2),
            "return_pct": round(ret, 2),
            "hold_days":  hold_days,
            "status":     status,
            "win":        ret > 0,
        })

    # Splice in real insights if available
    try:
        if insights:
            for ins in insights[:20]:
                rows.append({
                    "date":        getattr(ins, "generated_at", datetime.now()),
                    "instrument":  getattr(ins, "ticker", "N/A"),
                    "signal_type": "Carrier Alpha",
                    "direction":   getattr(ins, "action", "LONG").upper(),
                    "conviction":  round(float(getattr(ins, "confidence", 0.7)), 2),
                    "entry":       0.0,
                    "exit":        0.0,
                    "return_pct":  float(rng.normal(1.9, 3.5)),
                    "hold_days":   int(rng.integers(3, 21)),
                    "status":      "CLOSED",
                    "win":         True,
                })
    except Exception as exc:
        logger.warning(f"tab_results: insight splice error: {exc}")

    df = pd.DataFrame(rows).sort_values("date", ascending=False).reset_index(drop=True)
    return df


def _leaderboard_stats(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for st_type in SIGNAL_TYPES:
        sub = df[df["signal_type"] == st_type]
        if sub.empty:
            continue
        n = len(sub)
        win_rate = sub["win"].mean() * 100
        avg_ret = sub["return_pct"].mean()
        avg_hold = sub["hold_days"].mean()
        sharpe = (sub["return_pct"].mean() / sub["return_pct"].std()) * np.sqrt(252 / max(avg_hold, 1)) if sub["return_pct"].std() > 0 else 0.0
        ic = float(np.corrcoef(sub["conviction"], sub["return_pct"])[0, 1]) if len(sub) > 2 else 0.0
        rows.append({
            "Signal Type":    st_type,
            "Total Signals":  n,
            "Win Rate":       round(win_rate, 1),
            "Avg Return":     round(avg_ret, 2),
            "Avg Hold (d)":   round(avg_hold, 1),
            "Sharpe":         round(sharpe, 2),
            "IC":             round(ic, 3),
        })
    return pd.DataFrame(rows).sort_values("Win Rate", ascending=False).reset_index(drop=True)


def _instrument_stats(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for inst in df["instrument"].unique():
        sub = df[df["instrument"] == inst]
        if len(sub) < 3:
            continue
        best  = sub.loc[sub["return_pct"].idxmax()]
        worst = sub.loc[sub["return_pct"].idxmin()]
        rows.append({
            "Instrument":  inst,
            "Signals":     len(sub),
            "Win Rate":    round(sub["win"].mean() * 100, 1),
            "Total Alpha": round(sub["return_pct"].sum(), 1),
            "Best Call":   round(best["return_pct"], 2),
            "Worst Call":  round(worst["return_pct"], 2),
        })
    return (pd.DataFrame(rows)
            .sort_values("Total Alpha", ascending=False)
            .reset_index(drop=True))


def _monthly_attribution(df: pd.DataFrame) -> pd.DataFrame:
    df2 = df.copy()
    df2["month"] = pd.to_datetime(df2["date"]).dt.to_period("M")
    pivot = (df2.groupby(["month", "signal_type"])["return_pct"]
               .sum()
               .unstack(fill_value=0.0))
    pivot.index = pivot.index.astype(str)
    return pivot.tail(12)


def _decay_data(df: pd.DataFrame) -> pd.DataFrame:
    hold_bins = [1, 3, 5, 10, 20, 30]
    rows = []
    for sig in SIGNAL_TYPES:
        sub = df[df["signal_type"] == sig]
        for h in hold_bins:
            window = sub[sub["hold_days"] <= h]
            avg_ret = window["return_pct"].mean() if not window.empty else 0.0
            rows.append({"signal_type": sig, "hold_days": h, "avg_return": round(avg_ret, 3)})
    return pd.DataFrame(rows)


# ── Chart builders ─────────────────────────────────────────────────────────────

def _plotly_timeline(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for sig in SIGNAL_TYPES:
        sub = df[df["signal_type"] == sig]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["date"],
            y=sub["return_pct"],
            mode="markers",
            name=sig,
            marker=dict(
                size=sub["conviction"] * 14 + 3,
                color=SIGNAL_COLORS[sig],
                opacity=0.75,
                line=dict(width=0.5, color="rgba(255,255,255,0.2)"),
            ),
            hovertemplate=(
                "<b>%{text}</b><br>Date: %{x|%Y-%m-%d}<br>"
                "Return: %{y:.2f}%<extra></extra>"
            ),
            text=sub["instrument"],
        ))
    apply_dark_layout(fig, height=380)
    fig.update_layout(
        margin=dict(l=40, r=20, t=30, b=40),
        yaxis={"title": "Return %", "zeroline": True, "zerolinecolor": C_TEXT3},
        hovermode="closest",
    )
    return fig


def _plotly_return_dist(df: pd.DataFrame) -> go.Figure:
    rets = df["return_pct"].dropna().values
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=rets, nbinsx=50, name="Signal Returns",
        marker_color=C_ACCENT, opacity=0.75,
        histnorm="probability density",
    ))
    # Overlay normal
    mu, sigma = rets.mean(), rets.std()
    x_range = np.linspace(rets.min(), rets.max(), 200)
    y_norm = scipy_stats.norm.pdf(x_range, mu, sigma)
    fig.add_trace(go.Scatter(
        x=x_range, y=y_norm, mode="lines", name="Normal Fit",
        line=dict(color=C_HIGH, width=2, dash="dash"),
    ))
    apply_dark_layout(fig, height=320)
    fig.update_layout(
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis={"title": "Return %"},
        yaxis={"title": "Density"},
    )
    return fig


def _plotly_win_rate_bar(inst_df: pd.DataFrame) -> go.Figure:
    df_sorted = inst_df.sort_values("Win Rate", ascending=True)
    colors = [C_HIGH if v >= 55 else C_MOD if v >= 48 else C_LOW
              for v in df_sorted["Win Rate"]]
    fig = go.Figure(go.Bar(
        x=df_sorted["Win Rate"],
        y=df_sorted["Instrument"],
        orientation="h",
        marker_color=colors,
        text=[f"{v:.1f}%" for v in df_sorted["Win Rate"]],
        textposition="outside",
        textfont=dict(color=C_TEXT, size=11),
    ))
    apply_dark_layout(fig, height=350, showlegend=False)
    fig.update_layout(
        margin=dict(l=20, r=60, t=20, b=30),
        xaxis={"title": "Win Rate %", "range": [0, 90]},
        yaxis={"gridcolor": "rgba(0,0,0,0)"},
    )
    return fig


def _plotly_decay(decay_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for sig in SIGNAL_TYPES:
        sub = decay_df[decay_df["signal_type"] == sig]
        fig.add_trace(go.Scatter(
            x=sub["hold_days"],
            y=sub["avg_return"],
            mode="lines+markers",
            name=sig,
            line=dict(color=SIGNAL_COLORS[sig], width=2),
            marker=dict(size=6),
            hovertemplate="<b>%{fullData.name}</b><br>Day %{x}: %{y:.2f}%<extra></extra>",
        ))
    apply_dark_layout(fig, height=350)
    fig.update_layout(
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis={"title": "Holding Day", "tickvals": [1, 3, 5, 10, 20, 30]},
        yaxis={"title": "Avg Return %", "zeroline": True, "zerolinecolor": C_TEXT3},
    )
    return fig


# ── Monthly attribution HTML table ─────────────────────────────────────────────

def _monthly_attr_html(pivot: pd.DataFrame) -> str:
    def cell_bg(v: float) -> str:
        if v > 8:
            return f"rgba(46,158,110,0.55)"
        if v > 3:
            return f"rgba(46,158,110,0.30)"
        if v > 0:
            return f"rgba(46,158,110,0.12)"
        if v > -3:
            return f"rgba(192,57,43,0.12)"
        if v > -8:
            return f"rgba(192,57,43,0.30)"
        return f"rgba(192,57,43,0.55)"

    th_style = (f'style="padding:8px 10px;font-size:0.65rem;font-weight:700;'
                f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;'
                f'border-bottom:1px solid {C_BORDER};white-space:nowrap;background:{C_CARD}"')
    rows_html = ""
    for month, row in pivot.iterrows():
        cells = f'<td style="padding:8px 10px;font-size:0.72rem;color:{C_TEXT2};white-space:nowrap">{month}</td>'
        for col in pivot.columns:
            v = row[col]
            bg = cell_bg(v)
            color = C_HIGH if v >= 0 else C_LOW
            sign = "+" if v >= 0 else ""
            cells += (
                f'<td style="padding:8px 10px;text-align:right;font-size:0.72rem;'
                f'font-weight:600;color:{color};background:{bg};'
                f'border:1px solid rgba(232,230,225,0.03)">'
                f'{sign}{v:.1f}%</td>'
            )
        rows_html += f'<tr style="border-bottom:1px solid {C_BORDER}">{cells}</tr>'

    cols_header = f'<th {th_style}>Month</th>' + "".join(
        f'<th {th_style}>{c}</th>' for c in pivot.columns
    )
    return (
        f'<div style="overflow-x:auto;border-radius:6px;border:1px solid {C_BORDER}">'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{cols_header}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
    )


# ── Signal log table ───────────────────────────────────────────────────────────

def _signal_log_html(df: pd.DataFrame, n: int = 50) -> str:
    sample = df.head(n)
    headers = ["Date", "Instrument", "Signal Type", "Dir", "Conv", "Entry", "Exit", "Return", "Status"]
    th_s = (f'style="padding:9px 12px;font-size:0.65rem;font-weight:700;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;'
            f'border-bottom:1px solid {C_BORDER};background:{C_CARD};white-space:nowrap"')
    head_html = "".join(f'<th {th_s}>{h}</th>' for h in headers)

    rows_html = ""
    for _, r in sample.iterrows():
        ret = r["return_pct"]
        ret_color = C_HIGH if ret >= 0 else C_LOW
        ret_sign = "+" if ret >= 0 else ""
        dir_color = C_HIGH if r["direction"] == "LONG" else C_LOW
        status_color = C_ACCENT if r["status"] == "OPEN" else C_TEXT3
        conv_bar = int(r["conviction"] * 10)
        conv_str = f'{"█" * conv_bar}{"░" * (10 - conv_bar)} {r["conviction"]:.2f}'

        td = f'style="padding:8px 12px;font-size:0.72rem;color:{C_TEXT2};border-bottom:1px solid rgba(232,230,225,0.04)"'
        date_str = r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime") else str(r["date"])[:10]
        rows_html += (
            f'<tr style="transition:background 0.15s" '
            f'onmouseover="this.style.background=\'rgba(53,114,176,0.05)\'" '
            f'onmouseout="this.style.background=\'transparent\'">'
            f'<td {td}>{date_str}</td>'
            f'<td {td} style="font-weight:700;color:{C_TEXT}">{r["instrument"]}</td>'
            f'<td {td}>'
            f'<span style="background:rgba(53,114,176,0.15);color:{SIGNAL_COLORS.get(r["signal_type"], C_ACCENT)};'
            f'padding:2px 7px;border-radius:4px;font-size:0.65rem;font-weight:700">'
            f'{r["signal_type"]}</span></td>'
            f'<td {td} style="font-weight:700;color:{dir_color}">{r["direction"]}</td>'
            f'<td {td} style="font-family:JetBrains Mono,monospace;font-size:0.65rem;color:{C_TEXT3}">{conv_str}</td>'
            f'<td {td}>${r["entry"]:.2f}</td>'
            f'<td {td}>${r["exit"]:.2f}</td>'
            f'<td {td} style="font-weight:700;color:{ret_color}">{ret_sign}{ret:.2f}%</td>'
            f'<td {td}><span style="color:{status_color};font-weight:700">{r["status"]}</span></td>'
            f'</tr>'
        )
    return (
        f'<div style="overflow-x:auto;max-height:520px;overflow-y:auto;'
        f'border-radius:6px;border:1px solid {C_BORDER}">'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{head_html}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
    )


# ── Leaderboard table ──────────────────────────────────────────────────────────

def _leaderboard_html(lb: pd.DataFrame) -> str:
    headers = ["Rank", "Signal Type", "Total Signals", "Win Rate", "Avg Return", "Avg Hold", "Sharpe", "IC"]
    th_s = (f'style="padding:10px 14px;font-size:0.65rem;font-weight:700;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;'
            f'border-bottom:1px solid {C_BORDER};background:{C_CARD};white-space:nowrap"')
    head_html = "".join(f'<th {th_s}>{h}</th>' for h in headers)

    medal = {0: "🥇", 1: "🥈", 2: "🥉"}
    rows_html = ""
    for i, r in lb.head(10).iterrows():
        wr_color = C_HIGH if r["Win Rate"] >= 55 else C_MOD if r["Win Rate"] >= 48 else C_LOW
        ret_color = C_HIGH if r["Avg Return"] >= 0 else C_LOW
        ret_sign = "+" if r["Avg Return"] >= 0 else ""
        rank_lbl = medal.get(i, f"#{i+1}")
        sig_color = SIGNAL_COLORS.get(r["Signal Type"], C_ACCENT)
        td = f'style="padding:10px 14px;font-size:0.75rem;color:{C_TEXT2};border-bottom:1px solid rgba(232,230,225,0.04)"'
        rows_html += (
            f'<tr>'
            f'<td {td} style="font-size:0.9rem">{rank_lbl}</td>'
            f'<td {td} style="font-weight:700;color:{sig_color}">{r["Signal Type"]}</td>'
            f'<td {td} style="text-align:center">{r["Total Signals"]}</td>'
            f'<td {td} style="font-weight:800;color:{wr_color}">{r["Win Rate"]:.1f}%</td>'
            f'<td {td} style="font-weight:700;color:{ret_color}">{ret_sign}{r["Avg Return"]:.2f}%</td>'
            f'<td {td} style="text-align:center">{r["Avg Hold (d)"]:.1f}d</td>'
            f'<td {td} style="font-weight:700;color:{C_MOD}">{r["Sharpe"]:.2f}</td>'
            f'<td {td}>{r["IC"]:.3f}</td>'
            f'</tr>'
        )
    return (
        f'<div style="border-radius:6px;border:1px solid {C_BORDER};overflow:hidden">'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{head_html}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
    )


# ── Instrument table ───────────────────────────────────────────────────────────

def _instrument_table_html(inst_df: pd.DataFrame) -> str:
    headers = ["Instrument", "Signals", "Win Rate", "Total Alpha", "Best Call", "Worst Call"]
    th_s = (f'style="padding:9px 12px;font-size:0.65rem;font-weight:700;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;'
            f'border-bottom:1px solid {C_BORDER};background:{C_CARD}"')
    head_html = "".join(f'<th {th_s}>{h}</th>' for h in headers)
    rows_html = ""
    for _, r in inst_df.iterrows():
        wr_c = C_HIGH if r["Win Rate"] >= 55 else C_MOD if r["Win Rate"] >= 48 else C_LOW
        ta_c = C_HIGH if r["Total Alpha"] >= 0 else C_LOW
        ta_s = "+" if r["Total Alpha"] >= 0 else ""
        td = f'style="padding:9px 12px;font-size:0.73rem;color:{C_TEXT2};border-bottom:1px solid rgba(232,230,225,0.04)"'
        rows_html += (
            f'<tr>'
            f'<td {td} style="font-weight:800;color:{C_TEXT}">{r["Instrument"]}</td>'
            f'<td {td} style="text-align:center">{r["Signals"]}</td>'
            f'<td {td} style="font-weight:700;color:{wr_c}">{r["Win Rate"]:.1f}%</td>'
            f'<td {td} style="font-weight:700;color:{ta_c}">{ta_s}{r["Total Alpha"]:.1f}%</td>'
            f'<td {td} style="color:{C_HIGH}">+{r["Best Call"]:.2f}%</td>'
            f'<td {td} style="color:{C_LOW}">{r["Worst Call"]:.2f}%</td>'
            f'</tr>'
        )
    return (
        f'<div style="border-radius:6px;border:1px solid {C_BORDER};overflow:hidden">'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{head_html}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
    )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ══════════════════════════════════════════════════════════════════════════════

def render(stock_data, insights, freight_data=None):
    try:
        logger.info("tab_results: render start")

        page_header(
            title="Signal Performance & Backtest Results",
            subtitle="Aggregate alpha generation across all signal types, instruments, and freight routes.",
            badge_text="ALPHA SIGNALS",
            badge_color=C_ACCENT,
        )

        # ── Build signal log ───────────────────────────────────────────────
        try:
            df = _build_signal_log(insights, n=300)
        except Exception as exc:
            logger.error(f"tab_results: signal log build failed: {exc}")
            df = pd.DataFrame(columns=["date","instrument","signal_type","direction",
                                       "conviction","entry","exit","return_pct","hold_days","status","win"])

        closed = df[df["status"] == "CLOSED"]

        # ── Aggregate KPIs ─────────────────────────────────────────────────
        try:
            total_signals    = len(df)
            correct_calls    = int(df["win"].sum())
            correct_pct      = (correct_calls / total_signals * 100) if total_signals else 0
            avg_ret          = closed["return_pct"].mean() if not closed.empty else 0.0
            avg_hold         = df["hold_days"].mean() if not df.empty else 0.0
            rets             = closed["return_pct"].dropna().values
            sharpe           = float((rets.mean() / rets.std()) * np.sqrt(252 / max(avg_hold, 1))) if len(rets) > 1 and rets.std() > 0 else 0.0
            ic               = float(np.corrcoef(df["conviction"], df["return_pct"])[0, 1]) if len(df) > 2 else 0.0
            skewness         = float(scipy_stats.skew(rets)) if len(rets) > 2 else 0.0
            kurt             = float(scipy_stats.kurtosis(rets)) if len(rets) > 2 else 0.0
            pct_pos          = (rets > 0).mean() * 100 if len(rets) > 0 else 0.0
        except Exception as exc:
            logger.error(f"tab_results: KPI calc failed: {exc}")
            total_signals = correct_calls = 0
            correct_pct = avg_ret = avg_hold = sharpe = ic = skewness = kurt = pct_pos = 0.0

        signal_sources = [
            {"name": "Internal alpha-signal backtest", "kind": "modeled", "quality": "demo"},
            {"name": "Synthetic instrument log",       "kind": "modeled", "quality": "demo"},
        ]

        # ══════════════════════════════════════════════════════════════════
        # 1. SIGNAL PERFORMANCE DASHBOARD
        # ══════════════════════════════════════════════════════════════════
        section_header(
            "Signal Performance Dashboard",
            "Aggregate backtest statistics across all signal types and instruments",
        )

        try:
            avg_ret_color = C_HIGH if avg_ret >= 0 else C_LOW
            metric_card_row([
                {"label": "Total Signals Generated", "value": f"{total_signals:,}",
                 "accent": C_ACCENT, "sublabel": "all instruments / routes"},
                {"label": "Correct Direction Calls", "value": f"{correct_calls:,}",
                 "accent": C_HIGH,   "sublabel": f"{correct_pct:.1f}% accuracy"},
                {"label": "Avg Return per Signal",   "value": _fmt_pct(avg_ret),
                 "accent": avg_ret_color, "sublabel": "closed signals only"},
                {"label": "Avg Holding Period",      "value": f"{avg_hold:.1f} days",
                 "accent": C_MOD,    "sublabel": "across all signals"},
                {"label": "Signal Sharpe Ratio",     "value": f"{sharpe:.2f}",
                 "accent": C_MOD,    "sublabel": "annualized"},
                {"label": "Information Coefficient", "value": f"{ic:.3f}",
                 "accent": C_ACCENT, "sublabel": "conviction vs return corr"},
            ], columns=3)
        except Exception as exc:
            logger.error(f"tab_results: KPI render failed: {exc}")
            st.warning("KPI render error.")

        # ══════════════════════════════════════════════════════════════════
        # 2. SIGNAL LEADERBOARD
        # ══════════════════════════════════════════════════════════════════
        section_header("Signal Leaderboard", "Top-performing signal types ranked by win rate")
        try:
            lb = _leaderboard_stats(df)
            medal = {0: "🥇", 1: "🥈", 2: "🥉"}
            lb_rows = []
            for i, r in lb.head(10).iterrows():
                wr_color  = C_HIGH if r["Win Rate"] >= 55 else (C_MOD if r["Win Rate"] >= 48 else C_LOW)
                ret_color = C_HIGH if r["Avg Return"] >= 0 else C_LOW
                ret_sign  = "+" if r["Avg Return"] >= 0 else ""
                rank_lbl  = medal.get(i, f"#{i+1}")
                sig_color = SIGNAL_COLORS.get(r["Signal Type"], C_ACCENT)
                lb_rows.append([
                    _sans(rank_lbl, color=C_TEXT, weight=700),
                    _sans(r["Signal Type"], color=sig_color, weight=700),
                    _mono(str(r["Total Signals"])),
                    _mono(f"{r['Win Rate']:.1f}%", color=wr_color),
                    _mono(f"{ret_sign}{r['Avg Return']:.2f}%", color=ret_color),
                    _mono(f"{r['Avg Hold (d)']:.1f}d"),
                    _mono(f"{r['Sharpe']:.2f}", color=C_MOD),
                    _mono(f"{r['IC']:.3f}"),
                ])
            wsj_market_table(
                ["Rank", "Signal Type", "Total Signals", "Win Rate",
                 "Avg Return", "Avg Hold", "Sharpe", "IC"],
                lb_rows,
            )
            st.markdown(source_footer(signal_sources), unsafe_allow_html=True)
        except Exception as exc:
            logger.error(f"tab_results: leaderboard failed: {exc}")
            st.warning("Leaderboard unavailable.")

        # ══════════════════════════════════════════════════════════════════
        # 3. INSTRUMENT PERFORMANCE
        # ══════════════════════════════════════════════════════════════════
        section_header("Instrument Performance", "Alpha generated per ticker and freight route")
        try:
            inst_df = _instrument_stats(df)
            c1, c2 = st.columns([1, 1])
            with c1:
                inst_rows = []
                for _, r in inst_df.iterrows():
                    wr_c = C_HIGH if r["Win Rate"] >= 55 else (C_MOD if r["Win Rate"] >= 48 else C_LOW)
                    ta_c = C_HIGH if r["Total Alpha"] >= 0 else C_LOW
                    ta_s = "+" if r["Total Alpha"] >= 0 else ""
                    inst_rows.append([
                        _sans(r["Instrument"], color=C_TEXT, weight=800),
                        _mono(str(r["Signals"])),
                        _mono(f"{r['Win Rate']:.1f}%", color=wr_c),
                        _mono(f"{ta_s}{r['Total Alpha']:.1f}%", color=ta_c),
                        _mono(f"+{r['Best Call']:.2f}%", color=C_HIGH),
                        _mono(f"{r['Worst Call']:.2f}%", color=C_LOW),
                    ])
                wsj_market_table(
                    ["Instrument", "Signals", "Win Rate",
                     "Total Alpha", "Best Call", "Worst Call"],
                    inst_rows,
                )
            with c2:
                fig_bar = _plotly_win_rate_bar(inst_df)
                st.plotly_chart(fig_bar, use_container_width=True, key="win_rate_bar")
            st.markdown(source_footer(signal_sources), unsafe_allow_html=True)
        except Exception as exc:
            logger.error(f"tab_results: instrument perf failed: {exc}")
            st.warning("Instrument performance unavailable.")

        # ══════════════════════════════════════════════════════════════════
        # 4. SIGNAL TIMELINE
        # ══════════════════════════════════════════════════════════════════
        section_header(
            "Signal Timeline",
            "All signals plotted by date vs subsequent return — size = conviction",
        )
        try:
            fig_timeline = _plotly_timeline(df)
            st.plotly_chart(fig_timeline, use_container_width=True, key="signal_timeline")
            st.markdown(source_footer(signal_sources), unsafe_allow_html=True)
        except Exception as exc:
            logger.error(f"tab_results: timeline failed: {exc}")
            st.warning("Timeline chart unavailable.")

        # ══════════════════════════════════════════════════════════════════
        # 5. RETURN DISTRIBUTION
        # ══════════════════════════════════════════════════════════════════
        section_header("Return Distribution", "Empirical distribution of all signal returns")
        try:
            c1, c2 = st.columns([2, 1])
            with c1:
                fig_dist = _plotly_return_dist(closed if not closed.empty else df)
                st.plotly_chart(fig_dist, use_container_width=True, key="return_dist")
            with c2:
                std_dev = closed["return_pct"].std() if not closed.empty else 0.0
                stat_rows = [
                    [_sans("Mean Return", color=C_TEXT2),
                     _mono(_fmt_pct(avg_ret), color=(C_HIGH if avg_ret >= 0 else C_LOW))],
                    [_sans("Std Dev", color=C_TEXT2),
                     _mono(f"{std_dev:.2f}%")],
                    [_sans("Skewness", color=C_TEXT2),
                     _mono(f"{skewness:.3f}")],
                    [_sans("Kurtosis", color=C_TEXT2),
                     _mono(f"{kurt:.3f}")],
                    [_sans("% Positive", color=C_TEXT2),
                     _mono(f"{pct_pos:.1f}%", color=C_HIGH)],
                    [_sans("Sharpe", color=C_TEXT2),
                     _mono(f"{sharpe:.2f}", color=C_MOD)],
                    [_sans("IC", color=C_TEXT2),
                     _mono(f"{ic:.3f}", color=C_ACCENT)],
                ]
                wsj_market_table(["Distribution Stats", "Value"], stat_rows)
            st.markdown(source_footer(signal_sources), unsafe_allow_html=True)
        except Exception as exc:
            logger.error(f"tab_results: return dist failed: {exc}")
            st.warning("Return distribution unavailable.")

        # ══════════════════════════════════════════════════════════════════
        # 6. MONTHLY ATTRIBUTION
        # ══════════════════════════════════════════════════════════════════
        section_header(
            "Monthly Attribution",
            "Alpha generated per month per signal type — red=negative, green=positive",
        )
        try:
            pivot = _monthly_attribution(df)

            def _heat_cell(v: float) -> str:
                if v > 8:
                    bg = "rgba(46,158,110,0.55)"
                elif v > 3:
                    bg = "rgba(46,158,110,0.30)"
                elif v > 0:
                    bg = "rgba(46,158,110,0.12)"
                elif v > -3:
                    bg = "rgba(192,57,43,0.12)"
                elif v > -8:
                    bg = "rgba(192,57,43,0.30)"
                else:
                    bg = "rgba(192,57,43,0.55)"
                color = C_HIGH if v >= 0 else C_LOW
                sign  = "+" if v >= 0 else ""
                return (
                    f'<span style="display:inline-block;width:100%;padding:2px 4px;'
                    f'background:{bg};color:{color};font-family:var(--mono);'
                    f'font-weight:600;font-variant-numeric:tabular-nums;">'
                    f'{sign}{v:.1f}%</span>'
                )

            heat_rows = []
            for month, row in pivot.iterrows():
                cells = [_sans(str(month), color=C_TEXT2, weight=600)]
                for col in pivot.columns:
                    cells.append(_heat_cell(row[col]))
                heat_rows.append(cells)
            wsj_market_table(["Month"] + list(pivot.columns), heat_rows)
            st.markdown(source_footer(signal_sources), unsafe_allow_html=True)
        except Exception as exc:
            logger.error(f"tab_results: monthly attribution failed: {exc}")
            st.warning("Monthly attribution unavailable.")

        # ══════════════════════════════════════════════════════════════════
        # 7. RECENT SIGNAL LOG
        # ══════════════════════════════════════════════════════════════════
        section_header("Recent Signal Log", "Last 50 signals — open and closed")
        try:
            sample = df.head(50)
            log_rows = []
            for _, r in sample.iterrows():
                ret = r["return_pct"]
                ret_color = C_HIGH if ret >= 0 else C_LOW
                ret_sign  = "+" if ret >= 0 else ""
                dir_color = C_HIGH if r["direction"] == "LONG" else C_LOW
                status_color = C_ACCENT if r["status"] == "OPEN" else C_TEXT3
                conv_bar = int(r["conviction"] * 10)
                conv_str = f'{"█" * conv_bar}{"░" * (10 - conv_bar)} {r["conviction"]:.2f}'
                date_str = (
                    r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime")
                    else str(r["date"])[:10]
                )
                sig_color = SIGNAL_COLORS.get(r["signal_type"], C_ACCENT)
                log_rows.append([
                    _mono(date_str, color=C_TEXT2),
                    _sans(r["instrument"], color=C_TEXT, weight=700),
                    badge(r["signal_type"], color=sig_color),
                    _sans(r["direction"], color=dir_color, weight=700),
                    _mono(conv_str, color=C_TEXT3),
                    _mono(f"${r['entry']:.2f}"),
                    _mono(f"${r['exit']:.2f}"),
                    _mono(f"{ret_sign}{ret:.2f}%", color=ret_color),
                    _sans(r["status"], color=status_color, weight=700),
                ])
            wsj_market_table(
                ["Date", "Instrument", "Signal Type", "Dir", "Conv",
                 "Entry", "Exit", "Return", "Status"],
                log_rows,
            )
            st.markdown(source_footer(signal_sources), unsafe_allow_html=True)
        except Exception as exc:
            logger.error(f"tab_results: signal log render failed: {exc}")
            st.warning("Signal log unavailable.")

        # ══════════════════════════════════════════════════════════════════
        # 8. SIGNAL DECAY ANALYSIS
        # ══════════════════════════════════════════════════════════════════
        section_header(
            "Signal Decay Analysis",
            "Average return by holding day — shows how quickly each signal type decays",
        )
        try:
            decay_df = _decay_data(df)
            fig_decay = _plotly_decay(decay_df)
            st.plotly_chart(fig_decay, use_container_width=True, key="signal_decay")

            decay_drop = decay_df.groupby("signal_type").apply(
                lambda g: g.set_index("hold_days")["avg_return"].get(1, 0) -
                          g.set_index("hold_days")["avg_return"].get(30, 0)
            )
            fastest = decay_drop.idxmax()
            slowest = decay_drop.idxmin()

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(insight_card_html(
                    title=f"Fastest Decay — {fastest}",
                    score=0.85,
                    action="Caution",
                    rationale="Signal degrades most quickly over the holding period; treat as short-half-life and exit promptly.",
                    category="DECAY",
                ), unsafe_allow_html=True)
            with col_b:
                st.markdown(insight_card_html(
                    title=f"Slowest Decay — {slowest}",
                    score=0.25,
                    action="Watch",
                    rationale="Maintains alpha across longer holding windows; suitable for multi-day positioning.",
                    category="DECAY",
                ), unsafe_allow_html=True)
            st.markdown(source_footer(signal_sources), unsafe_allow_html=True)
        except Exception as exc:
            logger.error(f"tab_results: decay analysis failed: {exc}")
            st.warning("Signal decay analysis unavailable.")

        logger.info("tab_results: render complete")

    except Exception as exc:
        logger.error(f"tab_results: fatal render error: {exc}")
        st.error(f"Results tab encountered an error: {exc}")
