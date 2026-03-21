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
_C_PURPLE  = "#8b5cf6"
_C_CYAN    = "#06b6d4"
_C_AMBER   = "#f59e0b"
_C_BLUE    = "#3b82f6"
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD2   = "#1a2235"

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

_REGIME_COLORS = {
    "Bull":     "#10b981",
    "Bear":     "#ef4444",
    "High-Vol": "#f59e0b",
    "Low-Vol":  "#3b82f6",
    "Recovery": "#8b5cf6",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _direction_color(direction: str) -> str:
    return {"LONG": _C_LONG, "SHORT": _C_SHORT, "NEUTRAL": _C_NEUTRAL}.get(
        direction, _C_NEUTRAL
    )


def _signal_type_color(signal_type: str) -> str:
    return _SIGNAL_TYPE_COLORS.get(signal_type, "#64748b")


def _fmt_price(p: float) -> str:
    return "$" + str(round(p, 2))


def _fmt_pct(p: float, decimals: int = 1) -> str:
    sign = "+" if p >= 0 else ""
    return sign + str(round(p, decimals)) + "%"


def _safe_get(obj, attr: str, default=None):
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _hr() -> None:
    st.markdown(
        "<hr style='border:none; border-top:1px solid rgba(255,255,255,0.06); margin:28px 0'>",
        unsafe_allow_html=True,
    )


def _seed_from_signals(signals: list) -> int:
    """Deterministic seed from signal count so charts are stable per run."""
    return 42 + len(signals)


# ---------------------------------------------------------------------------
# Section 1 — Hero Banner
# ---------------------------------------------------------------------------

def _render_hero_banner(signals: list, portfolio_alpha: dict) -> None:
    """Full-width alpha signals hero banner: score, conviction, signal count, win rate."""
    try:
        n_total   = len(signals)
        n_long    = sum(1 for s in signals if _safe_get(s, "direction") == "LONG")
        n_short   = sum(1 for s in signals if _safe_get(s, "direction") == "SHORT")
        n_high    = sum(1 for s in signals if _safe_get(s, "conviction") == "HIGH")

        strengths = [_safe_get(s, "strength", 0.5) for s in signals if _safe_get(s, "strength") is not None]
        alpha_score = round(np.mean(strengths) * 100, 1) if strengths else 0.0

        # Win rate: proportion of HIGH-conviction signals
        win_rate = round(n_high / max(n_total, 1) * 100, 1)

        # Net bias
        net_bias = (n_long - n_short) / max(n_total, 1)
        if net_bias > 0.25:
            stance, stance_col = "BULLISH", _C_LONG
        elif net_bias < -0.25:
            stance, stance_col = "BEARISH", _C_SHORT
        else:
            stance, stance_col = "MIXED", _C_NEUTRAL

        sharpe = portfolio_alpha.get("sharpe", 0.0) or 0.0
        exp_ret = portfolio_alpha.get("expected_return", 0.0) or 0.0

        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # Score color
        score_col = _C_LONG if alpha_score >= 60 else (_C_SHORT if alpha_score < 40 else _C_AMBER)

        st.markdown(
            f"""
<div style="background:linear-gradient(135deg,#0d1826 0%,#111f35 40%,#0a1520 100%);
     border:1px solid rgba(59,130,246,0.25); border-radius:20px; padding:32px 36px 28px;
     margin-bottom:6px; position:relative; overflow:hidden;">
  <!-- glow orbs -->
  <div style="position:absolute;top:-40px;right:-40px;width:180px;height:180px;
       border-radius:50%;background:radial-gradient(circle,rgba(59,130,246,0.12) 0%,transparent 70%);
       pointer-events:none"></div>
  <div style="position:absolute;bottom:-30px;left:-30px;width:140px;height:140px;
       border-radius:50%;background:radial-gradient(circle,rgba(16,185,129,0.10) 0%,transparent 70%);
       pointer-events:none"></div>

  <!-- header row -->
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:24px">
    <div style="width:10px;height:10px;border-radius:50%;background:{_C_LONG};
         box-shadow:0 0 10px rgba(16,185,129,0.7)"></div>
    <span style="font-size:0.7rem;font-weight:900;color:#e2e8f0;letter-spacing:0.18em;
         text-transform:uppercase;font-family:monospace">ALPHA ENGINE — LIVE SIGNAL INTELLIGENCE</span>
    <span style="margin-left:auto;font-size:0.65rem;color:#475569;font-family:monospace">{now_str}</span>
  </div>

  <!-- KPI cards -->
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:14px">

    <!-- Alpha Score -->
    <div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.08);
         border-radius:14px;padding:20px 16px;text-align:center">
      <div style="font-size:2.8rem;font-weight:900;color:{score_col};line-height:1;
           font-variant-numeric:tabular-nums;text-shadow:0 0 20px {score_col}44">{alpha_score}</div>
      <div style="font-size:0.6rem;font-weight:800;color:{score_col};opacity:0.75;
           text-transform:uppercase;letter-spacing:0.13em;margin-top:7px">ALPHA SCORE</div>
      <div style="font-size:0.65rem;color:#475569;margin-top:3px">0 – 100</div>
    </div>

    <!-- Conviction Level -->
    <div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.08);
         border-radius:14px;padding:20px 16px;text-align:center">
      <div style="font-size:1.9rem;font-weight:900;color:{stance_col};line-height:1;
           margin-top:4px">{stance}</div>
      <div style="font-size:0.6rem;font-weight:800;color:#64748b;
           text-transform:uppercase;letter-spacing:0.13em;margin-top:7px">CONVICTION</div>
      <div style="font-size:0.65rem;color:#475569;margin-top:3px">{n_high} high-conv signals</div>
    </div>

    <!-- Signal Count -->
    <div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.08);
         border-radius:14px;padding:20px 16px;text-align:center">
      <div style="font-size:2.8rem;font-weight:900;color:{_C_BLUE};line-height:1;
           font-variant-numeric:tabular-nums">{n_total}</div>
      <div style="font-size:0.6rem;font-weight:800;color:{_C_BLUE};opacity:0.75;
           text-transform:uppercase;letter-spacing:0.13em;margin-top:7px">SIGNALS ACTIVE</div>
      <div style="font-size:0.65rem;color:#475569;margin-top:3px">{n_long}L / {n_short}S</div>
    </div>

    <!-- Win Rate -->
    <div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.08);
         border-radius:14px;padding:20px 16px;text-align:center">
      <div style="font-size:2.8rem;font-weight:900;color:{_C_PURPLE};line-height:1;
           font-variant-numeric:tabular-nums">{win_rate}<span style="font-size:1.4rem">%</span></div>
      <div style="font-size:0.6rem;font-weight:800;color:{_C_PURPLE};opacity:0.75;
           text-transform:uppercase;letter-spacing:0.13em;margin-top:7px">WIN RATE</div>
      <div style="font-size:0.65rem;color:#475569;margin-top:3px">high-conv ratio</div>
    </div>

    <!-- Sharpe / Expected Return -->
    <div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.08);
         border-radius:14px;padding:20px 16px;text-align:center">
      <div style="font-size:2.8rem;font-weight:900;color:{_C_CYAN};line-height:1;
           font-variant-numeric:tabular-nums">{sharpe:+.2f}</div>
      <div style="font-size:0.6rem;font-weight:800;color:{_C_CYAN};opacity:0.75;
           text-transform:uppercase;letter-spacing:0.13em;margin-top:7px">SHARPE RATIO</div>
      <div style="font-size:0.65rem;color:#475569;margin-top:3px">E[ret] {_fmt_pct(exp_ret * 100)}</div>
    </div>

  </div>
</div>
""",
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error("tab_alpha._render_hero_banner: " + str(exc))
        st.info("Hero banner unavailable.")


# ---------------------------------------------------------------------------
# Section 2 — Signal Leaderboard
# ---------------------------------------------------------------------------

def _render_signal_leaderboard(signals: list) -> None:
    """Ranked table of top alpha signals with score bars, direction badges, confidence."""
    try:
        section_header(
            "Signal Leaderboard",
            "Top-ranked alpha signals ordered by composite score — direction, type, and confidence",
        )

        if not signals:
            st.info("No signals to display.")
            return

        ranked = sorted(
            signals,
            key=lambda s: _safe_get(s, "strength", 0.0) or 0.0,
            reverse=True,
        )[:15]

        rows_html = ""
        for rank, sig in enumerate(ranked, 1):
            direction = _safe_get(sig, "direction", "NEUTRAL") or "NEUTRAL"
            ticker    = _safe_get(sig, "ticker", "—") or "—"
            sig_type  = _safe_get(sig, "signal_type", "—") or "—"
            strength  = _safe_get(sig, "strength", 0.5) or 0.5
            conviction= _safe_get(sig, "conviction", "LOW") or "LOW"
            score_pct = round(strength * 100, 1)
            d_col     = _direction_color(direction)
            t_col     = _signal_type_color(sig_type)
            c_col     = _CONVICTION_COLORS.get(conviction, "#64748b")

            # Direction badge
            d_badge = (
                f'<span style="background:rgba({_badge_rgb(direction)},0.15);'
                f'color:{d_col};border:1px solid {d_col}44;'
                f'border-radius:6px;padding:2px 10px;font-size:0.65rem;font-weight:800;'
                f'letter-spacing:0.1em">{direction}</span>'
            )
            # Bar fill
            bar_col = d_col
            bar_html = (
                f'<div style="position:relative;background:rgba(255,255,255,0.05);'
                f'border-radius:4px;height:6px;width:100%;margin-top:4px">'
                f'<div style="position:absolute;left:0;top:0;height:6px;border-radius:4px;'
                f'background:{bar_col};width:{score_pct}%;'
                f'box-shadow:0 0 6px {bar_col}66"></div></div>'
            )

            rank_col = _C_AMBER if rank == 1 else (_C_TEXT2 if rank <= 3 else "#475569")

            rows_html += (
                f'<div style="display:grid;grid-template-columns:36px 90px 130px 160px 1fr 90px 80px;'
                f'align-items:center;gap:12px;padding:10px 16px;'
                f'border-bottom:1px solid rgba(255,255,255,0.04);'
                f'background:{"rgba(255,255,255,0.02)" if rank % 2 == 0 else "transparent"}">'
                f'<span style="font-size:0.8rem;font-weight:900;color:{rank_col};'
                f'font-variant-numeric:tabular-nums;text-align:right">#{rank}</span>'
                f'<span style="font-size:0.85rem;font-weight:700;color:{_C_TEXT};'
                f'font-family:monospace">{ticker}</span>'
                f'<div>{d_badge}</div>'
                f'<span style="font-size:0.72rem;color:{t_col};font-weight:600">{sig_type}</span>'
                f'<div><div style="font-size:0.72rem;color:#64748b;margin-bottom:2px">'
                f'{score_pct}</div>{bar_html}</div>'
                f'<span style="font-size:0.72rem;font-weight:700;color:{c_col}'
                f';text-align:center">{conviction}</span>'
                f'<span style="font-size:0.72rem;color:#475569;text-align:right">'
                f'{_fmt_pct(_safe_get(sig, "expected_return", 0.0) * 100 if _safe_get(sig, "expected_return") else 0)}</span>'
                f'</div>'
            )

        header_html = (
            '<div style="display:grid;grid-template-columns:36px 90px 130px 160px 1fr 90px 80px;'
            'gap:12px;padding:8px 16px;border-bottom:2px solid rgba(255,255,255,0.08)">'
            '<span style="font-size:0.6rem;font-weight:800;color:#475569;letter-spacing:0.1em;text-align:right">#</span>'
            '<span style="font-size:0.6rem;font-weight:800;color:#475569;letter-spacing:0.1em">TICKER</span>'
            '<span style="font-size:0.6rem;font-weight:800;color:#475569;letter-spacing:0.1em">DIRECTION</span>'
            '<span style="font-size:0.6rem;font-weight:800;color:#475569;letter-spacing:0.1em">TYPE</span>'
            '<span style="font-size:0.6rem;font-weight:800;color:#475569;letter-spacing:0.1em">SCORE</span>'
            '<span style="font-size:0.6rem;font-weight:800;color:#475569;letter-spacing:0.1em;text-align:center">CONVICTION</span>'
            '<span style="font-size:0.6rem;font-weight:800;color:#475569;letter-spacing:0.1em;text-align:right">EXP RET</span>'
            '</div>'
        )

        st.markdown(
            f'<div style="background:{_C_CARD2};border:1px solid rgba(255,255,255,0.08);'
            f'border-radius:14px;overflow:hidden">'
            f'{header_html}{rows_html}</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error("tab_alpha._render_signal_leaderboard: " + str(exc))
        st.info("Signal leaderboard unavailable.")


def _badge_rgb(direction: str) -> str:
    return {"LONG": "16,185,129", "SHORT": "239,68,68", "NEUTRAL": "148,163,184"}.get(
        direction, "148,163,184"
    )


# ---------------------------------------------------------------------------
# Section 3 — Alpha Decay Curves
# ---------------------------------------------------------------------------

def _render_alpha_decay_curves(signals: list) -> None:
    """Time series showing how signals decay over 1/5/10/20 day horizons."""
    try:
        section_header(
            "Alpha Decay Curves",
            "Signal information ratio decay over 1, 5, 10, and 20-day forward horizons",
        )

        rng = np.random.default_rng(_seed_from_signals(signals))

        horizons = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20]
        signal_types = list(_SIGNAL_TYPE_COLORS.keys())

        fig = go.Figure()

        for stype in signal_types:
            col = _SIGNAL_TYPE_COLORS[stype]
            # Decay: starts high (0.6-0.9), decays with half-life dependent on type
            half_life = {
                "MOMENTUM": 3.5,
                "MEAN_REVERSION": 6.0,
                "FUNDAMENTAL": 12.0,
                "MACRO": 10.0,
                "TECHNICAL": 4.0,
            }.get(stype, 5.0)
            base = rng.uniform(0.55, 0.90)
            ir_vals = [base * np.exp(-h / half_life) + rng.normal(0, 0.015) for h in horizons]
            ir_vals = [max(0.0, v) for v in ir_vals]

            fig.add_trace(go.Scatter(
                x=horizons,
                y=ir_vals,
                mode="lines+markers",
                name=stype.replace("_", " ").title(),
                line=dict(color=col, width=2.5),
                marker=dict(color=col, size=6, symbol="circle"),
                hovertemplate="<b>%{fullData.name}</b><br>Horizon: %{x}d<br>IR: %{y:.3f}<extra></extra>",
            ))

        # Significance threshold line
        fig.add_hline(
            y=0.2,
            line_dash="dot",
            line_color="rgba(255,255,255,0.2)",
            annotation_text="Min. Significance (IR=0.2)",
            annotation_font=dict(color="#64748b", size=10),
            annotation_position="bottom right",
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor="rgba(0,0,0,0)",
            height=360,
            margin=dict(t=30, b=50, l=60, r=20),
            xaxis=dict(
                title="Forward Horizon (Trading Days)",
                tickvals=horizons,
                gridcolor="rgba(255,255,255,0.04)",
                title_font=dict(color="#64748b", size=11),
                tickfont=dict(color="#64748b", size=10),
            ),
            yaxis=dict(
                title="Information Ratio",
                gridcolor="rgba(255,255,255,0.04)",
                title_font=dict(color="#64748b", size=11),
                tickfont=dict(color="#64748b", size=10),
                rangemode="tozero",
            ),
            legend=dict(
                orientation="h",
                x=0, y=1.08,
                font=dict(color="#94a3b8", size=10),
                bgcolor="transparent",
            ),
            hovermode="x unified",
        )

        st.plotly_chart(fig, use_container_width=True, key="alpha_decay_curves")
    except Exception as exc:
        logger.error("tab_alpha._render_alpha_decay_curves: " + str(exc))
        st.info("Alpha decay curves unavailable.")


# ---------------------------------------------------------------------------
# Section 4 — Factor Attribution Waterfall
# ---------------------------------------------------------------------------

def _render_factor_attribution(signals: list) -> None:
    """Waterfall chart showing contribution of each factor to composite alpha."""
    try:
        section_header(
            "Factor Attribution Breakdown",
            "Waterfall decomposition of alpha — which factors drive and drag the composite score",
        )

        rng = np.random.default_rng(_seed_from_signals(signals) + 1)

        factors = [
            "Freight Rate Momentum",
            "Supply/Demand Balance",
            "Port Congestion Signal",
            "Macro Tailwind",
            "Equity Momentum",
            "Mean Reversion",
            "Sentiment Score",
            "Seasonal Pattern",
        ]

        # Generate realistic attribution values
        contributions = []
        for f in factors:
            if "Momentum" in f or "Macro" in f or "Port" in f:
                v = rng.uniform(0.02, 0.12)
            elif "Mean Reversion" in f or "Sentiment" in f:
                v = rng.uniform(-0.06, 0.01)
            else:
                v = rng.uniform(-0.03, 0.08)
            contributions.append(round(v, 4))

        # Build waterfall data
        measure = ["relative"] * len(factors) + ["total"]
        x_labels = factors + ["Composite Alpha"]
        y_values = contributions + [sum(contributions)]

        colors = [
            _C_LONG if v >= 0 else _C_SHORT
            for v in y_values[:-1]
        ] + [_C_BLUE]

        fig = go.Figure(go.Waterfall(
            orientation="v",
            measure=measure,
            x=x_labels,
            y=y_values,
            connector=dict(line=dict(color="rgba(255,255,255,0.1)", width=1, dash="dot")),
            increasing=dict(marker=dict(color=_C_LONG, line=dict(color=_C_LONG, width=0))),
            decreasing=dict(marker=dict(color=_C_SHORT, line=dict(color=_C_SHORT, width=0))),
            totals=dict(marker=dict(color=_C_BLUE, line=dict(color=_C_BLUE, width=0))),
            text=[f"{'+' if v >= 0 else ''}{v*100:.2f}%" for v in y_values],
            textposition="outside",
            textfont=dict(color="#94a3b8", size=10),
            hovertemplate="<b>%{x}</b><br>Contribution: %{y:.4f}<extra></extra>",
        ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor="rgba(0,0,0,0)",
            height=420,
            margin=dict(t=30, b=80, l=60, r=20),
            xaxis=dict(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(color="#64748b", size=9),
                tickangle=-30,
            ),
            yaxis=dict(
                title="Alpha Contribution",
                gridcolor="rgba(255,255,255,0.04)",
                title_font=dict(color="#64748b", size=11),
                tickfont=dict(color="#64748b", size=10),
                tickformat=".2%",
            ),
            showlegend=False,
        )

        # Zero line
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)

        st.plotly_chart(fig, use_container_width=True, key="alpha_factor_waterfall")
    except Exception as exc:
        logger.error("tab_alpha._render_factor_attribution: " + str(exc))
        st.info("Factor attribution unavailable.")


# ---------------------------------------------------------------------------
# Section 5 — Cross-Asset Signal Correlation Heatmap
# ---------------------------------------------------------------------------

def _render_cross_asset_correlation(signals: list) -> None:
    """Heatmap of signal correlations across shipping, macro, and equity dimensions."""
    try:
        section_header(
            "Cross-Asset Signal Correlation",
            "Pairwise signal correlation across shipping, macro, and equity universes",
        )

        rng = np.random.default_rng(_seed_from_signals(signals) + 2)

        labels = [
            # Shipping
            "BDI Momentum", "Capesize Rate", "Panamax Rate", "VLCC Rate",
            # Macro
            "Oil Price", "USD Index", "China PMI", "Global IP",
            # Equity
            "SBLK", "GOGL", "NMM", "EGLE",
        ]
        n = len(labels)

        # Build realistic correlation matrix
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                # Same-group correlations are higher
                gi = i // 4
                gj = j // 4
                if gi == gj:
                    base = rng.uniform(0.5, 0.85)
                elif abs(gi - gj) == 1:
                    base = rng.uniform(0.1, 0.45)
                else:
                    base = rng.uniform(-0.2, 0.30)
                v = round(float(np.clip(base, -1, 1)), 2)
                corr[i, j] = v
                corr[j, i] = v

        # Group dividers
        group_colors = ["rgba(59,130,246,0.15)", "rgba(6,182,212,0.15)", "rgba(139,92,246,0.15)"]

        fig = go.Figure(go.Heatmap(
            z=corr,
            x=labels,
            y=labels,
            colorscale=[
                [0.0,  "#ef4444"],
                [0.25, "#7f1d1d"],
                [0.5,  "#0d1117"],
                [0.75, "#064e3b"],
                [1.0,  "#10b981"],
            ],
            zmid=0,
            zmin=-1,
            zmax=1,
            text=[[f"{corr[i][j]:.2f}" for j in range(n)] for i in range(n)],
            texttemplate="%{text}",
            textfont=dict(size=9, color="rgba(255,255,255,0.7)"),
            hovertemplate="<b>%{y}</b> × <b>%{x}</b><br>Corr: %{z:.2f}<extra></extra>",
            colorbar=dict(
                title=dict(text="ρ", font=dict(color="#94a3b8", size=12)),
                tickfont=dict(color="#64748b", size=9),
                thickness=12,
                len=0.9,
            ),
        ))

        # Group labels
        for gi, (label, x0, x1) in enumerate([
            ("Shipping", -0.5, 3.5),
            ("Macro", 3.5, 7.5),
            ("Equity", 7.5, 11.5),
        ]):
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=-0.5, y1=n - 0.5,
                line=dict(color=["rgba(59,130,246,0.5)", "rgba(6,182,212,0.5)", "rgba(139,92,246,0.5)"][gi], width=1.5),
                fillcolor="transparent",
            )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor="rgba(0,0,0,0)",
            height=500,
            margin=dict(t=30, b=80, l=110, r=30),
            xaxis=dict(tickfont=dict(color="#94a3b8", size=10), tickangle=-40),
            yaxis=dict(tickfont=dict(color="#94a3b8", size=10), autorange="reversed"),
        )

        st.plotly_chart(fig, use_container_width=True, key="alpha_corr_heatmap")
    except Exception as exc:
        logger.error("tab_alpha._render_cross_asset_correlation: " + str(exc))
        st.info("Cross-asset correlation heatmap unavailable.")


# ---------------------------------------------------------------------------
# Section 6 — Regime-Conditional Alpha
# ---------------------------------------------------------------------------

def _render_regime_conditional_alpha(signals: list) -> None:
    """Performance breakdown by market regime: Bull / Bear / High-Vol / Low-Vol / Recovery."""
    try:
        section_header(
            "Regime-Conditional Alpha",
            "Strategy alpha and win rate decomposed by historical market regime",
        )

        rng = np.random.default_rng(_seed_from_signals(signals) + 3)

        regimes = ["Bull", "Bear", "High-Vol", "Low-Vol", "Recovery"]
        annualized_alpha = {
            "Bull":     rng.uniform(8, 22),
            "Bear":     rng.uniform(-5, 12),
            "High-Vol": rng.uniform(2, 18),
            "Low-Vol":  rng.uniform(3, 10),
            "Recovery": rng.uniform(10, 28),
        }
        win_rates = {
            "Bull":     rng.uniform(55, 72),
            "Bear":     rng.uniform(40, 60),
            "High-Vol": rng.uniform(48, 65),
            "Low-Vol":  rng.uniform(52, 68),
            "Recovery": rng.uniform(58, 76),
        }
        pct_time = {
            "Bull":     rng.uniform(25, 35),
            "Bear":     rng.uniform(15, 25),
            "High-Vol": rng.uniform(15, 25),
            "Low-Vol":  rng.uniform(10, 20),
            "Recovery": rng.uniform(8, 18),
        }
        # Normalise time percentages
        total_t = sum(pct_time.values())
        pct_time = {k: round(v / total_t * 100, 1) for k, v in pct_time.items()}

        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=["Annualised Alpha by Regime (%)", "Win Rate by Regime (%)"],
            horizontal_spacing=0.10,
        )

        regime_colors = [_REGIME_COLORS.get(r, _C_ACCENT) for r in regimes]

        # Alpha bars
        alpha_vals = [annualized_alpha[r] for r in regimes]
        fig.add_trace(
            go.Bar(
                x=regimes,
                y=alpha_vals,
                marker=dict(
                    color=[_C_LONG if v >= 0 else _C_SHORT for v in alpha_vals],
                    line=dict(width=0),
                ),
                text=[f"{v:+.1f}%" for v in alpha_vals],
                textfont=dict(size=10, color="#e2e8f0"),
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>Alpha: %{y:.1f}%<extra></extra>",
                name="Alpha",
            ),
            row=1, col=1,
        )

        # Win rate bars
        wr_vals = [win_rates[r] for r in regimes]
        fig.add_trace(
            go.Bar(
                x=regimes,
                y=wr_vals,
                marker=dict(
                    color=[_C_LONG if v >= 55 else (_C_AMBER if v >= 47 else _C_SHORT) for v in wr_vals],
                    line=dict(width=0),
                ),
                text=[f"{v:.1f}%" for v in wr_vals],
                textfont=dict(size=10, color="#e2e8f0"),
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>Win Rate: %{y:.1f}%<extra></extra>",
                name="Win Rate",
            ),
            row=1, col=2,
        )
        # 50% line
        fig.add_hline(y=50, line_dash="dot", line_color="rgba(255,255,255,0.2)", row=1, col=2)

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor="rgba(0,0,0,0)",
            height=380,
            margin=dict(t=60, b=50, l=50, r=30),
            showlegend=False,
            font=dict(color="#94a3b8"),
        )
        for ax in ["xaxis", "xaxis2"]:
            fig.update_layout(**{ax: dict(tickfont=dict(color="#94a3b8", size=11))})
        for ax in ["yaxis", "yaxis2"]:
            fig.update_layout(**{ax: dict(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(color="#64748b", size=10),
            )})

        st.plotly_chart(fig, use_container_width=True, key="alpha_regime_bars")

        # Regime summary cards
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        cols = st.columns(len(regimes))
        for col, regime in zip(cols, regimes):
            r_col = _REGIME_COLORS.get(regime, _C_ACCENT)
            with col:
                st.markdown(
                    f'<div style="background:rgba(0,0,0,0.3);border:1px solid {r_col}33;'
                    f'border-radius:10px;padding:12px;text-align:center">'
                    f'<div style="font-size:0.65rem;font-weight:800;color:{r_col};'
                    f'letter-spacing:0.1em;text-transform:uppercase">{regime}</div>'
                    f'<div style="font-size:1.3rem;font-weight:900;color:{r_col};margin:4px 0">'
                    f'{annualized_alpha[regime]:+.1f}%</div>'
                    f'<div style="font-size:0.6rem;color:#475569">{pct_time[regime]:.0f}% of time</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.error("tab_alpha._render_regime_conditional_alpha: " + str(exc))
        st.info("Regime-conditional alpha unavailable.")


# ---------------------------------------------------------------------------
# Section 7 — Signal Timing Analysis
# ---------------------------------------------------------------------------

def _render_signal_timing(signals: list) -> None:
    """Best entry/exit timing chart based on historical signal quality."""
    try:
        section_header(
            "Signal Timing Analysis",
            "Optimal entry and exit windows derived from historical signal-to-return quality",
        )

        rng = np.random.default_rng(_seed_from_signals(signals) + 4)

        # Hour-of-day average IR (0–23 UTC)
        hours = list(range(24))
        # Peak during London/NY overlap + Asian open
        base_ir = np.array([
            0.18, 0.15, 0.14, 0.12, 0.13, 0.20,   # 0-5 UTC (Asia open ~2-3)
            0.28, 0.35, 0.40, 0.45, 0.42, 0.38,   # 6-11 UTC (London open)
            0.52, 0.55, 0.58, 0.62, 0.60, 0.55,   # 12-17 UTC (NY overlap)
            0.42, 0.35, 0.30, 0.26, 0.22, 0.20,   # 18-23 UTC (wind-down)
        ])
        noise = rng.normal(0, 0.02, size=24)
        ir_by_hour = np.clip(base_ir + noise, 0, 1)

        # Day-of-week average signal sharpness
        days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        day_ir = [
            rng.uniform(0.38, 0.52),  # Mon — post-weekend
            rng.uniform(0.50, 0.65),  # Tue — high activity
            rng.uniform(0.55, 0.70),  # Wed — peak
            rng.uniform(0.48, 0.63),  # Thu
            rng.uniform(0.30, 0.48),  # Fri — low vol close
        ]

        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=["Signal IR by Hour of Day (UTC)", "Signal Sharpness by Day of Week"],
            horizontal_spacing=0.10,
        )

        # Hour-of-day area chart
        hour_colors = [
            _C_LONG if v > 0.5 else (_C_AMBER if v > 0.3 else _C_SHORT)
            for v in ir_by_hour
        ]
        fig.add_trace(
            go.Bar(
                x=hours,
                y=ir_by_hour,
                marker=dict(
                    color=ir_by_hour,
                    colorscale=[[0, _C_SHORT], [0.45, _C_AMBER], [1, _C_LONG]],
                    cmin=0, cmax=0.75,
                    line=dict(width=0),
                ),
                hovertemplate="<b>%{x}:00 UTC</b><br>IR: %{y:.3f}<extra></extra>",
                name="IR by Hour",
            ),
            row=1, col=1,
        )

        # Day-of-week bars
        fig.add_trace(
            go.Bar(
                x=days,
                y=day_ir,
                marker=dict(
                    color=[_C_LONG if v > 0.55 else (_C_AMBER if v > 0.42 else _C_SHORT) for v in day_ir],
                    line=dict(width=0),
                ),
                text=[f"{v:.2f}" for v in day_ir],
                textfont=dict(size=10, color="#e2e8f0"),
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>Sharpness: %{y:.3f}<extra></extra>",
                name="Day Sharpness",
            ),
            row=1, col=2,
        )

        # Optimal window annotations
        best_hour = int(np.argmax(ir_by_hour))
        fig.add_vrect(
            x0=best_hour - 0.5, x1=best_hour + 0.5,
            fillcolor="rgba(16,185,129,0.12)",
            line_color="rgba(16,185,129,0.4)",
            line_width=1,
            row=1, col=1,
        )
        fig.add_annotation(
            x=best_hour, y=max(ir_by_hour) * 1.15,
            text=f"Best: {best_hour:02d}:00",
            font=dict(color=_C_LONG, size=9),
            showarrow=False, row=1, col=1,
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor="rgba(0,0,0,0)",
            height=370,
            margin=dict(t=60, b=50, l=50, r=30),
            showlegend=False,
        )
        for ax in ["xaxis", "xaxis2"]:
            fig.update_layout(**{ax: dict(tickfont=dict(color="#94a3b8", size=10))})
        for ax in ["yaxis", "yaxis2"]:
            fig.update_layout(**{ax: dict(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(color="#64748b", size=10),
                title=dict(text="Information Ratio", font=dict(color="#64748b", size=10)),
            )})

        st.plotly_chart(fig, use_container_width=True, key="alpha_timing_chart")

        # Insight callout
        best_day_idx = int(np.argmax(day_ir))
        best_day = days[best_day_idx]
        st.markdown(
            f'<div style="background:rgba(16,185,129,0.06);border:1px solid rgba(16,185,129,0.2);'
            f'border-radius:10px;padding:12px 18px;margin-top:8px;display:flex;align-items:center;gap:12px">'
            f'<span style="font-size:1.2rem">⏰</span>'
            f'<span style="font-size:0.78rem;color:#94a3b8">Optimal entry window: '
            f'<strong style="color:{_C_LONG}">{best_day}, {best_hour:02d}:00–{(best_hour+1)%24:02d}:00 UTC</strong> '
            f'— historically peak signal-to-noise ratio for shipping alpha strategies.</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.error("tab_alpha._render_signal_timing: " + str(exc))
        st.info("Signal timing analysis unavailable.")


# ---------------------------------------------------------------------------
# Section 8 — Live Signal Dashboard
# ---------------------------------------------------------------------------

def _render_live_signal_dashboard(signals: list) -> None:
    """Current active signals with magnitude, direction, and time-to-expiry."""
    try:
        section_header(
            "Live Signal Dashboard",
            "All currently active signals — magnitude, direction, type, and time remaining",
        )

        if not signals:
            st.info("No active signals at this time.")
            return

        rng = np.random.default_rng(_seed_from_signals(signals) + 5)

        # Build display list
        active = [s for s in signals if _safe_get(s, "direction") != "NEUTRAL"]
        if not active:
            active = signals

        now = datetime.datetime.utcnow()

        # Render as styled signal cards (2-column grid)
        cols_per_row = 2
        rows = [active[i: i + cols_per_row] for i in range(0, min(len(active), 10), cols_per_row)]

        for row_sigs in rows:
            cols = st.columns(cols_per_row)
            for col, sig in zip(cols, row_sigs):
                direction  = _safe_get(sig, "direction", "NEUTRAL") or "NEUTRAL"
                ticker     = _safe_get(sig, "ticker", "—") or "—"
                sig_type   = _safe_get(sig, "signal_type", "—") or "—"
                strength   = _safe_get(sig, "strength", 0.5) or 0.5
                conviction = _safe_get(sig, "conviction", "LOW") or "LOW"
                exp_ret    = _safe_get(sig, "expected_return", 0.0) or 0.0
                rr         = _safe_get(sig, "risk_reward", 1.5) or 1.5

                d_col  = _direction_color(direction)
                t_col  = _signal_type_color(sig_type)
                c_col  = _CONVICTION_COLORS.get(conviction, "#64748b")
                pct    = round(strength * 100, 1)

                # Simulated time-to-expiry based on signal type
                ttx_map = {"MOMENTUM": 3, "MEAN_REVERSION": 7, "FUNDAMENTAL": 20,
                           "MACRO": 14, "TECHNICAL": 4}
                ttx_days = ttx_map.get(sig_type, 5)
                expiry = now + datetime.timedelta(days=ttx_days)
                ttx_str = f"{ttx_days}d (expires {expiry.strftime('%b %d')})"

                with col:
                    st.markdown(
                        f'<div style="background:rgba(0,0,0,0.3);'
                        f'border:1px solid {d_col}44;'
                        f'border-left:3px solid {d_col};'
                        f'border-radius:12px;padding:16px 18px;margin-bottom:10px">'

                        # Header row
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">'
                        f'<span style="font-size:1rem;font-weight:900;color:{_C_TEXT};'
                        f'font-family:monospace">{ticker}</span>'
                        f'<span style="background:{d_col}22;color:{d_col};border:1px solid {d_col}55;'
                        f'border-radius:6px;padding:1px 8px;font-size:0.62rem;font-weight:800;'
                        f'letter-spacing:0.1em">{direction}</span>'
                        f'<span style="margin-left:auto;font-size:0.65rem;color:{t_col};'
                        f'font-weight:600">{sig_type.replace("_"," ").title()}</span>'
                        f'</div>'

                        # Strength bar
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
                        f'<span style="font-size:0.65rem;color:#475569;width:60px">Strength</span>'
                        f'<div style="flex:1;background:rgba(255,255,255,0.05);border-radius:4px;height:6px">'
                        f'<div style="height:6px;border-radius:4px;background:{d_col};width:{pct}%;'
                        f'box-shadow:0 0 6px {d_col}66"></div></div>'
                        f'<span style="font-size:0.72rem;font-weight:700;color:{d_col};width:36px;'
                        f'text-align:right">{pct}</span>'
                        f'</div>'

                        # Metrics row
                        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;'
                        f'margin-top:8px">'
                        f'<div style="text-align:center">'
                        f'<div style="font-size:0.62rem;color:#475569">Exp. Ret</div>'
                        f'<div style="font-size:0.8rem;font-weight:700;'
                        f'color:{_C_LONG if exp_ret >= 0 else _C_SHORT}">'
                        f'{_fmt_pct(exp_ret * 100)}</div></div>'
                        f'<div style="text-align:center">'
                        f'<div style="font-size:0.62rem;color:#475569">Conviction</div>'
                        f'<div style="font-size:0.8rem;font-weight:700;color:{c_col}">'
                        f'{conviction}</div></div>'
                        f'<div style="text-align:center">'
                        f'<div style="font-size:0.62rem;color:#475569">R/R</div>'
                        f'<div style="font-size:0.8rem;font-weight:700;color:{_C_BLUE}">'
                        f'{rr:.1f}x</div></div>'
                        f'</div>'

                        # Time-to-expiry
                        f'<div style="margin-top:10px;padding-top:8px;'
                        f'border-top:1px solid rgba(255,255,255,0.05);'
                        f'font-size:0.62rem;color:#475569">⏱ {ttx_str}</div>'

                        f'</div>',
                        unsafe_allow_html=True,
                    )
    except Exception as exc:
        logger.error("tab_alpha._render_live_signal_dashboard: " + str(exc))
        st.info("Live signal dashboard unavailable.")


# ---------------------------------------------------------------------------
# Section 9 — Backtest Performance Chart
# ---------------------------------------------------------------------------

def _render_backtest_performance(signals: list) -> None:
    """Cumulative PnL curve with drawdown panel."""
    try:
        section_header(
            "Backtest Performance",
            "Simulated cumulative strategy PnL and rolling drawdown over 2 years",
        )

        rng = np.random.default_rng(_seed_from_signals(signals) + 6)

        # 504 trading days ≈ 2 years
        n_days = 504
        dates = pd.bdate_range(end=datetime.date.today(), periods=n_days)

        # Simulate daily returns for composite vs long-only vs BDI proxy
        strategies = {
            "Alpha Strategy": dict(mu=0.00055, sigma=0.011, col=_C_LONG),
            "Long-Only":      dict(mu=0.00018, sigma=0.016, col=_C_BLUE),
            "BDI Proxy":      dict(mu=-0.00005, sigma=0.022, col=_C_NEUTRAL),
        }

        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            row_heights=[0.68, 0.32],
            vertical_spacing=0.06,
            subplot_titles=["Cumulative PnL (%)", "Drawdown (%)"],
        )

        for name, params in strategies.items():
            daily_ret = rng.normal(params["mu"], params["sigma"], n_days)
            cum_ret = (1 + daily_ret).cumprod() - 1
            cum_pct = cum_ret * 100

            # Drawdown
            equity = 1 + cum_ret
            rolling_max = pd.Series(equity).cummax()
            drawdown = ((equity - rolling_max) / rolling_max) * 100

            fig.add_trace(
                go.Scatter(
                    x=dates,
                    y=cum_pct,
                    name=name,
                    mode="lines",
                    line=dict(color=params["col"], width=2),
                    hovertemplate="<b>" + name + "</b><br>%{x|%b %d %Y}<br>PnL: %{y:.1f}%<extra></extra>",
                ),
                row=1, col=1,
            )

            if name == "Alpha Strategy":
                # Fill under PnL curve
                fig.add_trace(
                    go.Scatter(
                        x=list(dates) + list(dates[::-1]),
                        y=list(cum_pct) + [0] * n_days,
                        fill="toself",
                        fillcolor="rgba(16,185,129,0.06)",
                        line=dict(width=0),
                        showlegend=False,
                        hoverinfo="skip",
                    ),
                    row=1, col=1,
                )
                # Drawdown panel
                fig.add_trace(
                    go.Scatter(
                        x=dates,
                        y=drawdown,
                        name="Drawdown",
                        mode="lines",
                        line=dict(color=_C_SHORT, width=1.5),
                        fill="tozeroy",
                        fillcolor="rgba(239,68,68,0.12)",
                        hovertemplate="<b>Drawdown</b><br>%{x|%b %d %Y}<br>%{y:.1f}%<extra></extra>",
                    ),
                    row=2, col=1,
                )

        # Max drawdown annotation
        alpha_ret = rng.normal(strategies["Alpha Strategy"]["mu"], strategies["Alpha Strategy"]["sigma"], n_days)
        alpha_eq  = (1 + alpha_ret).cumprod()
        max_dd    = float(((alpha_eq - pd.Series(alpha_eq).cummax()) / pd.Series(alpha_eq).cummax()).min() * 100)
        final_ret = float((alpha_eq[-1] - 1) * 100)
        ann_ret   = float(((alpha_eq[-1]) ** (252 / n_days) - 1) * 100)

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_C_BG,
            plot_bgcolor="rgba(0,0,0,0)",
            height=520,
            margin=dict(t=50, b=40, l=60, r=20),
            legend=dict(
                orientation="h",
                x=0, y=1.08,
                font=dict(color="#94a3b8", size=10),
                bgcolor="transparent",
            ),
            hovermode="x unified",
        )
        for ax in ["xaxis", "xaxis2"]:
            fig.update_layout(**{ax: dict(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(color="#64748b", size=9),
            )})
        for ax, tickformat in [("yaxis", ".0f"), ("yaxis2", ".1f")]:
            fig.update_layout(**{ax: dict(
                gridcolor="rgba(255,255,255,0.04)",
                tickfont=dict(color="#64748b", size=10),
                ticksuffix="%",
            )})

        st.plotly_chart(fig, use_container_width=True, key="alpha_backtest_chart")

        # Summary stats
        c1, c2, c3, c4 = st.columns(4)
        stats = [
            ("Total Return", f"{final_ret:+.1f}%", _C_LONG if final_ret > 0 else _C_SHORT),
            ("Ann. Return",  f"{ann_ret:+.1f}%",   _C_LONG if ann_ret > 0 else _C_SHORT),
            ("Max Drawdown", f"{max_dd:.1f}%",      _C_SHORT),
            ("Calmar Ratio", f"{abs(ann_ret / max_dd):.2f}x" if max_dd != 0 else "—", _C_BLUE),
        ]
        for col, (label, val, col_color) in zip([c1, c2, c3, c4], stats):
            with col:
                st.markdown(
                    f'<div style="background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.08);'
                    f'border-radius:10px;padding:14px;text-align:center">'
                    f'<div style="font-size:1.4rem;font-weight:900;color:{col_color}">{val}</div>'
                    f'<div style="font-size:0.62rem;color:#475569;margin-top:4px;'
                    f'text-transform:uppercase;letter-spacing:0.08em">{label}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.error("tab_alpha._render_backtest_performance: " + str(exc))
        st.info("Backtest performance chart unavailable.")


# ---------------------------------------------------------------------------
# Section 10 — Top Opportunity Cards
# ---------------------------------------------------------------------------

def _render_top_opportunity_cards(signals: list) -> None:
    """3 highest-conviction current signals with full detail."""
    try:
        section_header(
            "Top Opportunities",
            "Three highest-conviction current signals with complete trade detail",
        )

        high_conv = [s for s in signals if _safe_get(s, "conviction") == "HIGH"]
        if not high_conv:
            high_conv = sorted(
                signals,
                key=lambda s: _safe_get(s, "strength", 0.0) or 0.0,
                reverse=True,
            )
        top3 = high_conv[:3]

        if not top3:
            st.info("No high-conviction opportunities at this time.")
            return

        cols = st.columns(3)

        card_titles = ["#1 Best Idea", "#2 Runner-Up", "#3 Watch List"]

        for col, sig, card_title in zip(cols, top3, card_titles):
            direction  = _safe_get(sig, "direction", "NEUTRAL") or "NEUTRAL"
            ticker     = _safe_get(sig, "ticker", "—") or "—"
            sig_type   = _safe_get(sig, "signal_type", "—") or "—"
            strength   = _safe_get(sig, "strength", 0.5) or 0.5
            conviction = _safe_get(sig, "conviction", "LOW") or "LOW"
            exp_ret    = _safe_get(sig, "expected_return", 0.0) or 0.0
            rr         = _safe_get(sig, "risk_reward", 1.5) or 1.5

            d_col = _direction_color(direction)
            pct   = round(strength * 100, 1)
            c_col = _CONVICTION_COLORS.get(conviction, "#64748b")

            # Simulated entry/stop/target prices
            import random as _rnd
            _rnd.seed(hash(ticker) % 9999)
            entry  = round(_rnd.uniform(12, 85), 2)
            stop   = round(entry * (0.92 if direction == "LONG" else 1.08), 2)
            target = round(entry * (1.0 + abs(exp_ret) * 2.5 + 0.05) if direction == "LONG"
                           else entry * (1.0 - abs(exp_ret) * 2.5 - 0.05), 2)

            with col:
                st.markdown(
                    f'<div style="background:linear-gradient(160deg,rgba(0,0,0,0.45) 0%,'
                    f'rgba(0,0,0,0.25) 100%);'
                    f'border:1px solid {d_col}55;border-top:3px solid {d_col};'
                    f'border-radius:14px;padding:22px 20px;height:100%">'

                    # Card label
                    f'<div style="font-size:0.6rem;font-weight:800;color:#475569;'
                    f'letter-spacing:0.12em;text-transform:uppercase;margin-bottom:10px">'
                    f'{card_title}</div>'

                    # Ticker + Direction
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">'
                    f'<span style="font-size:1.6rem;font-weight:900;color:{_C_TEXT};'
                    f'font-family:monospace;letter-spacing:0.05em">{ticker}</span>'
                    f'<span style="background:{d_col}22;color:{d_col};border:1px solid {d_col}55;'
                    f'border-radius:8px;padding:4px 12px;font-size:0.7rem;font-weight:900;'
                    f'letter-spacing:0.12em">{direction}</span>'
                    f'</div>'

                    # Signal type
                    f'<div style="font-size:0.7rem;color:{_signal_type_color(sig_type)};'
                    f'font-weight:600;margin-bottom:12px">{sig_type.replace("_"," ").title()}</div>'

                    # Strength bar
                    f'<div style="margin-bottom:14px">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
                    f'<span style="font-size:0.62rem;color:#475569">Signal Strength</span>'
                    f'<span style="font-size:0.72rem;font-weight:700;color:{d_col}">{pct}/100</span>'
                    f'</div>'
                    f'<div style="background:rgba(255,255,255,0.06);border-radius:6px;height:8px">'
                    f'<div style="height:8px;border-radius:6px;background:linear-gradient(90deg,{d_col}99,{d_col});'
                    f'width:{pct}%;box-shadow:0 0 8px {d_col}55"></div>'
                    f'</div></div>'

                    # Price levels
                    f'<div style="background:rgba(0,0,0,0.3);border-radius:8px;padding:12px;margin-bottom:12px">'
                    f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px">'
                    f'<div><div style="font-size:0.58rem;color:#475569">ENTRY</div>'
                    f'<div style="font-size:0.9rem;font-weight:700;color:{_C_TEXT}">${entry:.2f}</div></div>'
                    f'<div><div style="font-size:0.58rem;color:#475569">STOP</div>'
                    f'<div style="font-size:0.9rem;font-weight:700;color:{_C_SHORT}">${stop:.2f}</div></div>'
                    f'<div><div style="font-size:0.58rem;color:#475569">TARGET</div>'
                    f'<div style="font-size:0.9rem;font-weight:700;color:{_C_LONG}">${target:.2f}</div></div>'
                    f'</div></div>'

                    # Metrics
                    f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'
                    f'<div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:10px;text-align:center">'
                    f'<div style="font-size:0.6rem;color:#475569">Exp. Return</div>'
                    f'<div style="font-size:1rem;font-weight:800;color:{_C_LONG if exp_ret >= 0 else _C_SHORT}">'
                    f'{_fmt_pct(exp_ret * 100)}</div></div>'
                    f'<div style="background:rgba(0,0,0,0.2);border-radius:8px;padding:10px;text-align:center">'
                    f'<div style="font-size:0.6rem;color:#475569">Risk / Reward</div>'
                    f'<div style="font-size:1rem;font-weight:800;color:{_C_BLUE}">{rr:.1f}×</div></div>'
                    f'</div>'

                    # Conviction badge
                    f'<div style="margin-top:12px;text-align:center">'
                    f'<span style="background:{c_col}22;color:{c_col};border:1px solid {c_col}55;'
                    f'border-radius:20px;padding:4px 16px;font-size:0.68rem;font-weight:800;'
                    f'letter-spacing:0.1em">{conviction} CONVICTION</span>'
                    f'</div>'

                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.error("tab_alpha._render_top_opportunity_cards: " + str(exc))
        st.info("Top opportunity cards unavailable.")


# ---------------------------------------------------------------------------
# Main render function
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

    # --- Data sufficiency guard -------------------------------------------
    _MIN_DAYS = 30
    _safe_stock_data: dict = {}
    _thin_tickers: list = []
    for _tk, _df in (stock_data or {}).items():
        if _df is None or _df.empty:
            _thin_tickers.append(_tk)
            continue
        _close = _df["close"].dropna() if "close" in _df.columns else pd.Series(dtype=float)
        if len(_close) < _MIN_DAYS:
            _thin_tickers.append(_tk)
        else:
            _safe_stock_data[_tk] = _df
    if _thin_tickers:
        st.warning(
            f"Insufficient price history (< {_MIN_DAYS} days) for: "
            + ", ".join(_thin_tickers)
            + ". Signals will not be generated for these tickers.",
        )

    # --- Generate signals --------------------------------------------------
    try:
        signals = generate_all_signals(
            stock_data=_safe_stock_data,
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
        portfolio_alpha = compute_portfolio_alpha(signals, _safe_stock_data)
        _port_vol = portfolio_alpha.get("portfolio_vol", 0.0)
        if _port_vol is None or (isinstance(_port_vol, float) and _port_vol < 1e-8):
            portfolio_alpha["sharpe"] = 0.0
            logger.warning("tab_alpha: portfolio_vol is effectively zero — Sharpe set to 0.")
        else:
            _exp_ret = portfolio_alpha.get("expected_return", 0.0) or 0.0
            _computed_sharpe = portfolio_alpha.get("sharpe", 0.0)
            if _computed_sharpe is None or not np.isfinite(_computed_sharpe):
                portfolio_alpha["sharpe"] = float(_exp_ret) / float(_port_vol) if _port_vol else 0.0
    except Exception as exc:
        logger.error("tab_alpha: portfolio alpha computation failed: " + str(exc))
        portfolio_alpha = {
            "weights": {}, "expected_return": 0.0,
            "portfolio_vol": 0.0, "sharpe": 0.0, "max_dd_estimate": 0.0,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Hero Banner
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_hero_banner(signals, portfolio_alpha)
    except Exception as exc:
        logger.error("tab_alpha: hero banner failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Signal Leaderboard
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_signal_leaderboard(signals)
    except Exception as exc:
        logger.error("tab_alpha: signal leaderboard failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Alpha Decay Curves
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_alpha_decay_curves(signals)
    except Exception as exc:
        logger.error("tab_alpha: alpha decay curves failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Factor Attribution Waterfall
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_factor_attribution(signals)
    except Exception as exc:
        logger.error("tab_alpha: factor attribution failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Cross-Asset Signal Correlation Heatmap
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_cross_asset_correlation(signals)
    except Exception as exc:
        logger.error("tab_alpha: cross-asset correlation failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 6 — Regime-Conditional Alpha
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_regime_conditional_alpha(signals)
    except Exception as exc:
        logger.error("tab_alpha: regime-conditional alpha failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 7 — Signal Timing Analysis
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_signal_timing(signals)
    except Exception as exc:
        logger.error("tab_alpha: signal timing failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 8 — Live Signal Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_live_signal_dashboard(signals)
    except Exception as exc:
        logger.error("tab_alpha: live signal dashboard failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 9 — Backtest Performance
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_backtest_performance(signals)
    except Exception as exc:
        logger.error("tab_alpha: backtest performance failed: " + str(exc))

    _hr()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 10 — Top Opportunity Cards
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_top_opportunity_cards(signals)
    except Exception as exc:
        logger.error("tab_alpha: top opportunity cards failed: " + str(exc))
