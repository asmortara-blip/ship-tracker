"""Backtesting tab — historical alpha signal performance dashboard.

Two complementary validation views live here:

* **Real-Signal Validation** — the platform's *actual* signals (the disruption
  cascade's ranked ``EquityIdea`` list and the commodity-shipping signals) are
  measured directly against forward returns over the platform's synthetic price
  history. This is the transparent hit-rate scorecard built in
  ``processing.signal_validation`` and is shown first.
* **Heuristic Signal Backtest** — the original ``backtest_engine`` simulation of
  hard-coded momentum / mean-reversion / divergence heuristics, kept below for
  comparison.

Every figure here is computed on synthetic / modeled data and is labelled as
such — it is a signal-quality scorecard, never investment advice.
"""
from __future__ import annotations

import traceback

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataKind, DataQuality, DataSource
from ui.styles import (
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_TEXT, C_TEXT2, C_TEXT3,
    C_CONV, C_MACRO, C_SURFACE,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ---------------------------------------------------------------------------
# Domain-specific color mappings — kept local to the tab
# ---------------------------------------------------------------------------

_CONVICTION_COLORS: dict[str, str] = {
    "HIGH":   C_HIGH,
    "MEDIUM": C_MOD,
    "LOW":    C_LOW,
}

_TYPE_COLORS: dict[str, str] = {
    "MOMENTUM":       C_ACCENT,
    "MEAN_REVERSION": C_CONV,
    "MACRO":          C_MACRO,
    "TECHNICAL":      C_MOD,
    "FUNDAMENTAL":    C_HIGH,
}

# Conviction-tier palette for the real-signal validation scorecard. The cascade
# emits High/Moderate/Low/Watch; commodity signals carry no tier and bucket
# under "Commodity".
_TIER_COLORS: dict[str, str] = {
    "High":      C_HIGH,
    "Moderate":  C_MOD,
    "Low":       C_LOW,
    "Watch":     C_TEXT3,
    "Commodity": C_MACRO,
}

# Direction-word palette — the real signals are framed Bullish/Bearish/Neutral.
_DIRECTION_COLORS: dict[str, str] = {
    "Bullish": C_HIGH,
    "Bearish": C_LOW,
    "Neutral": C_TEXT2,
}


# ---------------------------------------------------------------------------
# Cell formatters for WSJ market tables
# ---------------------------------------------------------------------------

def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _conviction_color(conv: str) -> str:
    return _CONVICTION_COLORS.get(conv, C_ACCENT)


def _type_color(signal_type: str) -> str:
    return _TYPE_COLORS.get(signal_type, C_ACCENT)


def _return_color(val: float) -> str:
    if val > 0:
        return C_HIGH
    if val < 0:
        return C_LOW
    return C_TEXT2


def _winrate_color(wr: float) -> str:
    if wr >= 55:
        return C_HIGH
    if wr >= 45:
        return C_MOD
    return C_LOW


def _sharpe_color(sh: float) -> str:
    if sh >= 1:
        return C_HIGH
    if sh >= 0:
        return C_MOD
    return C_LOW


def _drawdown_color(dd: float) -> str:
    """dd is typically negative (e.g. -8.5); more negative = worse."""
    if dd >= -5:
        return C_HIGH
    if dd >= -15:
        return C_MOD
    return C_LOW


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def _equity_curve_chart(equity_curve: list[dict], stock_data: dict) -> go.Figure:
    """Cumulative return of alpha signals vs buy-and-hold SPY proxy."""
    if not equity_curve:
        fig = go.Figure()
        apply_dark_layout(fig, title="Equity Curve — No Data", height=380)
        return fig

    df = pd.DataFrame(equity_curve)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    fig = go.Figure()

    # Alpha signal curve
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["cumulative_return"],
        name="Alpha Signals",
        line=dict(color=C_HIGH, width=2.5),
        fill="tozeroy",
        fillcolor="rgba(46,158,110,0.08)",
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Cumulative Return: %{y:.1f}%<extra></extra>",
    ))

    # Buy-and-hold proxy from any available ticker (SPY or first available)
    bh_ticker = None
    for t in ("SPY", "XLI", "ZIM", "MATX"):
        if t in stock_data and not stock_data[t].empty:
            bh_ticker = t
            break

    if bh_ticker:
        bh_df = stock_data[bh_ticker].copy()
        if "date" in bh_df.columns:
            bh_df["date"] = pd.to_datetime(bh_df["date"])
            bh_df = bh_df.sort_values("date")
            # Align date range to equity curve
            start = df["date"].min()
            end = df["date"].max()
            bh_df = bh_df[(bh_df["date"] >= start) & (bh_df["date"] <= end)]
            if not bh_df.empty and "close" in bh_df.columns:
                p0 = bh_df["close"].iloc[0]
                if p0 > 0:
                    bh_df["bh_return"] = (bh_df["close"] / p0 - 1) * 100
                    fig.add_trace(go.Scatter(
                        x=bh_df["date"],
                        y=bh_df["bh_return"],
                        name=f"Buy & Hold ({bh_ticker})",
                        line=dict(color=C_TEXT3, width=1.5, dash="dot"),
                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>B&H Return: %{y:.1f}%<extra></extra>",
                    ))

    apply_dark_layout(
        fig,
        title="Cumulative Return — Alpha Signals vs Buy & Hold",
        height=380,
    )
    fig.update_yaxes(ticksuffix="%")
    return fig


def _conviction_bar_chart(by_conviction: dict) -> go.Figure:
    """Grouped bar: win rate + avg return per conviction tier."""
    if not by_conviction:
        fig = go.Figure()
        apply_dark_layout(fig, title="Performance by Conviction — No Data", height=340)
        return fig

    convictions = ["HIGH", "MEDIUM", "LOW"]
    convictions = [c for c in convictions if c in by_conviction]

    win_rates = [by_conviction[c]["win_rate"] for c in convictions]
    avg_returns = [by_conviction[c]["avg_return"] for c in convictions]
    colors = [_CONVICTION_COLORS.get(c, C_ACCENT) for c in convictions]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Win Rate (%)",
        x=convictions,
        y=win_rates,
        marker_color=colors,
        marker_opacity=0.85,
        text=[f"{v:.1f}%" for v in win_rates],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT),
        yaxis="y",
        hovertemplate="<b>%{x}</b><br>Win Rate: %{y:.1f}%<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Avg Return (%)",
        x=convictions,
        y=avg_returns,
        marker_color=["rgba(53,114,176,0.7)"] * len(convictions),
        text=[f"{v:+.2f}%" for v in avg_returns],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Avg Return: %{y:+.2f}%<extra></extra>",
    ))

    apply_dark_layout(
        fig,
        title="Performance by Conviction Tier",
        height=340,
        barmode="group",
        yaxis={"title": "Win Rate (%)", "ticksuffix": "%"},
        yaxis2={
            "title": "Avg Return (%)",
            "overlaying": "y",
            "side": "right",
            "ticksuffix": "%",
            "gridcolor": "rgba(232,230,225,0.03)",
            "tickfont": {"color": C_TEXT3, "size": 11},
        },
    )
    return fig


def _signal_type_chart(by_type: dict) -> go.Figure:
    """Horizontal bar: win rate per signal type."""
    if not by_type:
        fig = go.Figure()
        apply_dark_layout(fig, title="Performance by Signal Type — No Data", height=300)
        return fig

    types = sorted(by_type.keys(), key=lambda t: by_type[t]["win_rate"], reverse=True)
    win_rates = [by_type[t]["win_rate"] for t in types]
    avg_rets = [by_type[t]["avg_return"] for t in types]
    colors = [_TYPE_COLORS.get(t, C_ACCENT) for t in types]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        orientation="h",
        name="Win Rate",
        x=win_rates,
        y=types,
        marker_color=colors,
        marker_opacity=0.85,
        text=[f"{v:.1f}%  (avg {r:+.1f}%)" for v, r in zip(win_rates, avg_rets)],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT2),
        hovertemplate="<b>%{y}</b><br>Win Rate: %{x:.1f}%<extra></extra>",
    ))

    apply_dark_layout(
        fig,
        title="Win Rate by Signal Type",
        height=max(300, len(types) * 55 + 80),
        showlegend=False,
        xaxis={"ticksuffix": "%", "range": [0, 110]},
    )
    return fig


def _monthly_heatmap(monthly_returns: list[dict]) -> go.Figure:
    """Heatmap of monthly returns — month vs year."""
    if not monthly_returns:
        fig = go.Figure()
        apply_dark_layout(fig, title="Monthly Returns Heatmap — No Data", height=300)
        return fig

    df = pd.DataFrame(monthly_returns)
    df["year"] = df["month"].str[:4]
    df["mon"] = df["month"].str[5:7].astype(int)

    mon_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    years = sorted(df["year"].unique())
    months_present = sorted(df["mon"].unique())

    # Build z matrix: rows=years, cols=months
    z = []
    text = []
    for yr in years:
        row_z = []
        row_t = []
        for m in months_present:
            match = df[(df["year"] == yr) & (df["mon"] == m)]
            if not match.empty:
                val = round(float(match["return_pct"].iloc[0]), 2)
                row_z.append(val)
                row_t.append(f"{val:+.2f}%")
            else:
                row_z.append(None)
                row_t.append("")
        z.append(row_z)
        text.append(row_t)

    mon_names = [mon_labels[m - 1] for m in months_present]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=mon_names,
        y=years,
        text=text,
        texttemplate="%{text}",
        colorscale=[
            [0.0, "#c0392b"],
            [0.4, "#12151e"],
            [0.5, "#181c28"],
            [0.6, "#12151e"],
            [1.0, "#2e9e6e"],
        ],
        zmid=0,
        showscale=True,
        colorbar=dict(
            ticksuffix="%",
            tickfont=dict(color=C_TEXT3, size=10),
            bgcolor=C_SURFACE,
        ),
        hovertemplate="<b>%{y} %{x}</b><br>Return: %{z:+.2f}%<extra></extra>",
    ))

    apply_dark_layout(
        fig,
        title="Monthly Avg Return Heatmap",
        height=max(280, len(years) * 40 + 120),
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Trade log WSJ table builder
# ---------------------------------------------------------------------------

def _build_trade_rows(trades: list) -> list[list[str]]:
    """Convert trade objects to wsj_market_table row lists."""
    rows = []
    for t in trades:
        ret_color = C_HIGH if t.return_pct > 0 else C_LOW
        hit_color = C_HIGH if t.hit else C_LOW
        hit_label = "Hit" if t.hit else "Miss"
        rows.append([
            _sans(t.ticker, color=C_TEXT, weight=700),
            _sans(t.signal_name[:24], color=C_TEXT2),
            badge(t.conviction, color=_conviction_color(t.conviction)),
            _mono(str(t.entry_date)[:10], color=C_TEXT2),
            _mono(f"{t.return_pct:+.2f}%", color=ret_color),
            _mono(f"{t.holding_days}d", color=C_TEXT2),
            badge(hit_label, color=hit_color),
        ])
    return rows


# ---------------------------------------------------------------------------
# Auto-generated key insights
# ---------------------------------------------------------------------------

def _build_insights(results) -> list[tuple[str, str, str, float]]:
    """Return list of (title, body, color, score) insight tuples.

    ``score`` is a 0–1 confidence the insight-card progress bar fills to —
    win-rate-derived for the conviction/type insights so the bar is meaningful
    rather than a flat zero.
    """
    insights = []

    # Conviction insight
    by_conv = results.by_conviction
    if by_conv:
        best_conv = max(by_conv, key=lambda k: by_conv[k]["win_rate"])
        stats = by_conv[best_conv]
        insights.append((
            f"{best_conv} conviction signals win {stats['win_rate']:.0f}% of the time",
            f"Across {stats['count']} trades, {best_conv} conviction signals returned an average of "
            f"{stats['avg_return']:+.2f}% per trade. "
            f"{'This is well above the 50% random baseline, indicating strong predictive power.' if stats['win_rate'] > 55 else 'Conviction tiers help filter signal quality — use HIGH conviction signals as primary entries.'}",
            _CONVICTION_COLORS.get(best_conv, C_ACCENT),
            max(0.0, min(1.0, stats["win_rate"] / 100.0)),
        ))

    # Signal type insight
    by_type = results.by_type
    if by_type:
        best_type = max(by_type, key=lambda k: by_type[k]["avg_return"])
        stats = by_type[best_type]
        readable = best_type.replace("_", " ").title()
        insights.append((
            f"{readable} signals deliver the highest average return",
            f"{readable} trades averaged {stats['avg_return']:+.2f}% per trade with a "
            f"{stats['win_rate']:.0f}% win rate across {stats['count']} occurrences. "
            f"{'Consider overweighting this signal type during trending periods.' if stats['win_rate'] > 52 else 'Review market regime conditions when deploying this strategy.'}",
            _TYPE_COLORS.get(best_type, C_ACCENT),
            max(0.0, min(1.0, stats["win_rate"] / 100.0)),
        ))

    # Drawdown / risk insight
    if results.total_trades > 0:
        avg_dd = sum(t.max_drawdown_pct for t in results.trades) / len(results.trades)
        worst = results.worst_trade
        best = results.best_trade
        # Risk score: shallower average drawdown reads as a stronger fill.
        risk_score = max(0.0, min(1.0, 1.0 + avg_dd / 25.0))
        insights.append((
            f"Risk profile: avg intraday drawdown {avg_dd:.1f}%, worst trade {worst.return_pct:+.1f}% ({worst.ticker})",
            f"Best trade: {best.ticker} {best.signal_name} returned {best.return_pct:+.2f}% "
            f"(held {best.holding_days}d). "
            f"Worst trade: {worst.ticker} {worst.signal_name} returned {worst.return_pct:+.2f}% "
            f"(held {worst.holding_days}d). "
            f"Position sizing and stop-loss discipline are critical given shipping stock volatility.",
            C_MOD,
            risk_score,
        ))

    return insights


# ---------------------------------------------------------------------------
# Real-signal validation — the platform's actual signals, not hardcoded mocks
# ---------------------------------------------------------------------------

def _hit_rate_color(hr: float) -> str:
    """Colour a hit rate: >=55% green, >=45% amber, else red."""
    if hr >= 0.55:
        return C_HIGH
    if hr >= 0.45:
        return C_MOD
    return C_LOW


def _edge_color(edge: float) -> str:
    """Colour an edge-vs-baseline figure: positive green, ~flat amber, negative red."""
    if edge > 0.02:
        return C_HIGH
    if edge < -0.02:
        return C_LOW
    return C_MOD


def _hit_rate_bar(hr: float, color: str) -> str:
    """Inline hit-rate bar — proportion filled, percentage label alongside."""
    pct = max(0.0, min(100.0, hr * 100.0))
    return (
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<div style="width:74px;background:rgba(232,230,225,0.06);'
        f'border-radius:3px;height:7px;flex-shrink:0;">'
        f'<div style="width:{pct:.0f}%;background:{color};height:100%;'
        f'border-radius:3px;"></div></div>'
        f'<span style="font-family:var(--mono);color:{color};font-size:0.78rem;'
        f'font-weight:700;">{pct:.0f}%</span></div>'
    )


def _validation_tier_rows(tiers: list) -> list[list[str]]:
    """Build wsj_market_table rows for the conviction-tier breakdown."""
    rows: list[list[str]] = []
    for t in tiers:
        tier_color = _TIER_COLORS.get(t.tier, C_ACCENT)
        hr_color = _hit_rate_color(t.hit_rate)
        edge_color = _edge_color(t.edge_vs_baseline)
        edge_str = f"{t.edge_vs_baseline * 100:+.0f} pts"
        rows.append([
            badge(t.tier, color=tier_color),
            _mono(str(t.n_signals), color=C_TEXT2),
            _mono(str(t.n_observations), color=C_TEXT3),
            _hit_rate_bar(t.hit_rate, hr_color),
            _mono(f"{t.baseline_hit_rate * 100:.0f}%", color=C_TEXT2),
            _mono(edge_str, color=edge_color),
            _mono(f"{t.directional_return * 100:+.2f}%",
                  color=_return_color(t.directional_return)),
        ])
    return rows


def _validation_signal_rows(signals: list) -> list[list[str]]:
    """Build wsj_market_table rows for the per-signal validation detail."""
    rows: list[list[str]] = []
    for s in signals:
        tier_color = _TIER_COLORS.get(s.conviction_label, C_ACCENT)
        dir_color = _DIRECTION_COLORS.get(s.direction.title(), C_TEXT2)
        hr_color = _hit_rate_color(s.hit_rate)
        edge_color = _edge_color(s.edge_vs_baseline)
        result_label = "Low sample" if s.low_sample else (
            "Edge" if s.edge_vs_baseline > 0.02
            else ("Lags" if s.edge_vs_baseline < -0.02 else "In line")
        )
        result_color = C_TEXT3 if s.low_sample else edge_color
        rows.append([
            _sans(s.signal_id, color=C_TEXT, weight=700),
            _sans(s.signal_kind, color=C_TEXT3),
            badge(s.direction.title(), color=dir_color),
            badge(s.conviction_label, color=tier_color),
            _hit_rate_bar(s.hit_rate, hr_color),
            _mono(f"{s.directional_return * 100:+.2f}%",
                  color=_return_color(s.directional_return)),
            _mono(f"{s.edge_vs_baseline * 100:+.0f} pts", color=edge_color),
            _mono(str(s.n_observations), color=C_TEXT3),
            badge(result_label, color=result_color),
        ])
    return rows


def _validation_tier_chart(tiers: list) -> go.Figure:
    """Grouped bar: conviction-tier hit rate vs equal-weight baseline."""
    fig = go.Figure()
    if not tiers:
        apply_dark_layout(fig, title="Hit Rate by Conviction Tier — No Data", height=320)
        return fig

    labels = [t.tier for t in tiers]
    hit_rates = [t.hit_rate * 100 for t in tiers]
    baselines = [t.baseline_hit_rate * 100 for t in tiers]
    bar_colors = [_TIER_COLORS.get(t.tier, C_ACCENT) for t in tiers]

    fig.add_trace(go.Bar(
        name="Signal Hit Rate",
        x=labels,
        y=hit_rates,
        marker_color=bar_colors,
        marker_opacity=0.9,
        text=[f"{v:.0f}%" for v in hit_rates],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT),
        hovertemplate="<b>%{x}</b><br>Hit Rate: %{y:.0f}%<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Equal-Weight Baseline",
        x=labels,
        y=baselines,
        marker_color=["rgba(154,150,142,0.45)"] * len(labels),
        text=[f"{v:.0f}%" for v in baselines],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT3),
        hovertemplate="<b>%{x}</b><br>Baseline: %{y:.0f}%<extra></extra>",
    ))

    apply_dark_layout(
        fig,
        title="Hit Rate by Conviction Tier vs Synthetic Baseline",
        height=340,
        barmode="group",
        yaxis={"title": "Hit Rate (%)", "ticksuffix": "%", "range": [0, 110]},
    )
    return fig


def _render_real_signal_validation(stock_data: dict, insights: object) -> None:
    """Render the real-signal validation scorecard.

    Builds the platform's *actual* signals — the disruption cascade's ranked
    ``EquityIdea`` list and the commodity-shipping signals — then validates each
    signal's directional claim against forward returns over the synthetic price
    history via ``processing.signal_validation``. Surfaces the conviction-tier
    hit rates, the vs-baseline comparison, and a per-signal detail table.

    Wrapped defensively: any failure degrades to an inline notice rather than
    breaking the tab.
    """
    section_header(
        "Real-Signal Validation",
        "The platform's own cascade & commodity signals, scored on synthetic history",
    )

    st.markdown(
        f"<p style='color:{C_TEXT2};font-size:0.82rem;line-height:1.6;"
        f"margin:0 0 14px;'>"
        "Unlike the heuristic backtest below, this section validates the "
        "<b>signals the platform actually surfaces</b> — the disruption "
        "cascade's ranked equity ideas and the commodity-shipping signals. "
        "Each signal's directional claim (Bullish / Bearish / Neutral) is "
        "measured against forward returns over the platform's "
        "<b>synthetic price history</b>, then broken down by the cascade's own "
        "conviction tiers and compared with an equal-weight, always-long "
        "baseline. Every number is a plain count of forward windows — no fitted "
        "model. <b>Modeled demo data — a signal-quality scorecard, not "
        "investment advice.</b></p>",
        unsafe_allow_html=True,
    )

    try:
        from processing.exposure_matrix import build_exposure_matrix
        from processing.shipping_stress_index import compute_shipping_stress
        from processing.signal_validation import build_validation_report

        # The Backtest tab has no pre-computed cascade in hand, so build the SSI
        # and exposure matrix here, then run the real pipeline + validation. All
        # of these tolerate empty inputs and return neutral defaults.
        stress_report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
        exposure_matrix = build_exposure_matrix(stock_data)
        report = build_validation_report(
            stress_report, exposure_matrix, stock_data, insights=insights,
        )
    except Exception as e:
        logger.exception("tab_backtest: real-signal validation failed")
        st.warning(f"Real-signal validation unavailable: {e}")
        return

    val_source = report.source or DataSource(
        name="Real-Signal Validator (synthetic history)",
        kind=DataKind.MODELED,
        quality=DataQuality.DEMO,
    )

    if report.n_signals_validated == 0:
        st.info(
            "No real signals could be validated — no synthetic price history "
            "was available for any signalled ticker. Ensure stock data is "
            "loaded."
        )
        st.markdown(source_footer([val_source]), unsafe_allow_html=True)
        return

    # ── Headline KPIs ────────────────────────────────────────────────────
    metric_card_row(
        [
            {
                "label":    "Signals Validated",
                "value":    str(report.n_signals_validated),
                "accent":   C_ACCENT,
                "sublabel": f"{report.n_signals_skipped} skipped (no history)",
            },
            {
                "label":    "Aggregate Hit Rate",
                "value":    f"{report.overall_hit_rate * 100:.0f}%",
                "accent":   _hit_rate_color(report.overall_hit_rate),
                "sublabel": f"{report.forward_days}-day forward horizon",
            },
            {
                "label":    "Synthetic Baseline",
                "value":    f"{report.overall_baseline_hit_rate * 100:.0f}%",
                "accent":   C_TEXT2,
                "sublabel": "equal-weight, always-long",
            },
            {
                "label":    "Edge vs Baseline",
                "value":    f"{report.overall_edge * 100:+.0f} pts",
                "accent":   _edge_color(report.overall_edge),
                "sublabel": "hit rate over baseline",
            },
            {
                "label":    "Avg In-Favour Return",
                "value":    f"{report.overall_directional_return * 100:+.2f}%",
                "accent":   _return_color(report.overall_directional_return),
                "sublabel": "mean directional move",
            },
        ],
        columns=5,
    )
    st.markdown(source_footer([val_source]), unsafe_allow_html=True)

    # ── Plain-language headline ──────────────────────────────────────────
    st.markdown(
        insight_card_html(
            title="Validation Summary",
            score=max(0.0, min(1.0, report.overall_hit_rate)),
            action=(
                "Prioritize" if report.overall_edge > 0.02
                else ("Caution" if report.overall_edge < -0.02 else "Monitor")
            ),
            rationale=report.summary,
            category="SYNTHETIC",
        ),
        unsafe_allow_html=True,
    )

    # ── Conviction-tier breakdown ────────────────────────────────────────
    section_header(
        "Hit Rate by Conviction Tier",
        "Do higher-conviction cascade signals actually hit more often?",
    )
    try:
        fig_tier = _validation_tier_chart(report.tiers)
        st.plotly_chart(fig_tier, use_container_width=True, key="bt_val_tier_chart")
    except Exception as e:
        logger.exception("tab_backtest: tier chart failed")
        st.error(f"Tier chart error: {e}")

    try:
        if report.tiers:
            wsj_market_table(
                headers=[
                    "Tier", "Signals", "Windows", "Hit Rate",
                    "Baseline", "Edge", "Avg In-Favour Return",
                ],
                rows=_validation_tier_rows(report.tiers),
            )
            st.markdown(
                f"<p style='color:{C_TEXT3};font-size:0.72rem;margin-top:6px;'>"
                "&lsquo;Windows&rsquo; counts the forward-return observations "
                "behind each tier. &lsquo;Edge&rsquo; is hit rate minus the "
                "equal-weight synthetic baseline — positive means the tier beat "
                "doing nothing. Neutral-direction signals score a hit when the "
                "synthetic tape stayed flat.</p>",
                unsafe_allow_html=True,
            )
            st.markdown(source_footer([val_source]), unsafe_allow_html=True)
    except Exception as e:
        logger.exception("tab_backtest: tier table failed")
        st.error(f"Tier table error: {e}")

    # ── Per-signal detail ────────────────────────────────────────────────
    section_header(
        "Per-Signal Track Record",
        "Every cascade idea and commodity signal, ranked by conviction",
    )
    try:
        if report.signals:
            wsj_market_table(
                headers=[
                    "Signal", "Kind", "Direction", "Conviction",
                    "Hit Rate", "In-Favour Return", "Edge", "Windows", "Result",
                ],
                rows=_validation_signal_rows(report.signals),
            )
            n_low = sum(1 for s in report.signals if s.low_sample)
            caveat = (
                f" {n_low} signal(s) flagged low-sample — read those hit rates "
                f"as indicative only."
                if n_low else ""
            )
            st.markdown(
                f"<p style='color:{C_TEXT3};font-size:0.72rem;margin-top:6px;'>"
                f"Each signal's direction tested against {report.forward_days}-day "
                f"forward returns over {report.price_history_days} rows of "
                f"synthetic price history.{caveat}</p>",
                unsafe_allow_html=True,
            )
            st.markdown(source_footer([val_source]), unsafe_allow_html=True)
        else:
            st.info("No per-signal records to display.")
    except Exception as e:
        logger.exception("tab_backtest: per-signal table failed")
        st.error(f"Per-signal table error: {e}")


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(stock_data: dict, macro_data: dict = None, insights: object = None,
           **kwargs) -> None:
    """Render the full Backtesting tab.

    Surfaces two views: the real-signal validation scorecard (the platform's
    actual cascade & commodity signals, validated on synthetic history) followed
    by the original heuristic-signal backtest simulation.

    ``macro_data`` / ``insights`` are optional and ``**kwargs`` is accepted so
    the tab is robust to caller arg drift.
    """
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('backtest'):
        macro_data = macro_data or {}

        page_header(
            title="Signal Validation & Backtester",
            subtitle="Validate the platform's real signals, then the heuristic backtest — all on synthetic data",
            badge_text="DEMO",
            badge_color=C_LOW,
        )

        # ── Real-signal validation (the platform's actual signals) ───────────────
        try:
            _render_real_signal_validation(stock_data, insights)
        except Exception as e:
            logger.exception("tab_backtest: real-signal validation section crashed")
            st.error(f"Real-signal validation section error: {e}")

        section_divider("Heuristic Signal Backtest")

        section_header(
            "Heuristic Signal Backtest",
            "The original simulation of hard-coded momentum / mean-reversion / divergence rules",
        )

        # ── Controls ─────────────────────────────────────────────────────────────
        with st.expander("Backtest Settings", expanded=False):
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                lookback = st.slider(
                    "Lookback window (days)",
                    min_value=60,
                    max_value=365,
                    value=180,
                    step=30,
                    key="bt_lookback",
                )
            with col_b:
                hold_1w = st.number_input("1W hold (trading days)", value=5, min_value=1, max_value=15, key="bt_hold_1w")
                hold_1m = st.number_input("1M hold (trading days)", value=21, min_value=5, max_value=45, key="bt_hold_1m")
            with col_c:
                hold_3m = st.number_input("3M hold (trading days)", value=63, min_value=20, max_value=90, key="bt_hold_3m")

        hold_days_map = {"1W": int(hold_1w), "1M": int(hold_1m), "3M": int(hold_3m)}

        # ── Run backtest ─────────────────────────────────────────────────────────
        run_btn = st.button("Run Backtest", type="primary", key="bt_run_btn")

        bt_results_key = "bt_results_cache"

        if run_btn or bt_results_key not in st.session_state:
            if not stock_data:
                st.warning("No stock data available — cannot run backtest.")
                return
            with st.spinner("Running backtest simulation..."):
                try:
                    from processing.backtest_engine import run_backtest
                    results = run_backtest(
                        stock_data=stock_data,
                        lookback_days=lookback,
                        hold_days_map=hold_days_map,
                    )
                    st.session_state[bt_results_key] = results
                except Exception as e:
                    st.error(f"Backtest engine error: {e}")
                    logger.error(f"Backtest render error: {traceback.format_exc()}")
                    return

        results = st.session_state.get(bt_results_key)
        if results is None or results.total_trades == 0:
            st.info("No trades generated. Try increasing the lookback window or check that stock data is loaded.")
            return

        # ── Hero KPIs ─────────────────────────────────────────────────────────────
        _demo_source = DataSource.demo("Backtest Engine (simulated)")

        metric_card_row(
            [
                {
                    "label":    "Total Return",
                    "value":    f"{results.total_return_pct:+.1f}%",
                    "accent":   _return_color(results.total_return_pct),
                    "sublabel": "sum equal-weight",
                },
                {
                    "label":    "Win Rate",
                    "value":    f"{results.win_rate:.1f}%",
                    "accent":   _winrate_color(results.win_rate),
                    "sublabel": "trades in right direction",
                },
                {
                    "label":    "Sharpe Ratio",
                    "value":    f"{results.sharpe_ratio:.2f}",
                    "accent":   _sharpe_color(results.sharpe_ratio),
                    "sublabel": "annualized",
                },
                {
                    "label":    "Total Trades",
                    "value":    str(results.total_trades),
                    "accent":   C_ACCENT,
                    "sublabel": f"~{lookback}d window",
                },
                {
                    "label":    "Max Drawdown",
                    "value":    f"{results.max_drawdown:.1f}%",
                    "accent":   _drawdown_color(results.max_drawdown),
                    "sublabel": "worst intraday",
                },
            ],
            columns=5,
        )
        st.markdown(source_footer([_demo_source]), unsafe_allow_html=True)

        section_divider("Performance")

        # ── Equity Curve ──────────────────────────────────────────────────────────
        section_header("Equity Curve", "Cumulative alpha vs a buy-and-hold benchmark")
        try:
            fig_eq = _equity_curve_chart(results.equity_curve, stock_data)
            st.plotly_chart(fig_eq, use_container_width=True, key="bt_equity_curve")
            st.markdown(source_footer([_demo_source]), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Equity curve error: {e}")

        # ── Performance by Conviction / Signal Type ───────────────────────────────
        section_header("Performance Breakdown")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown('<div class="sub-section-header">By Conviction Tier</div>', unsafe_allow_html=True)
            try:
                fig_conv = _conviction_bar_chart(results.by_conviction)
                st.plotly_chart(fig_conv, use_container_width=True, key="bt_conviction_bar")
            except Exception as e:
                st.error(f"Conviction chart error: {e}")

        with col2:
            st.markdown('<div class="sub-section-header">By Signal Type</div>', unsafe_allow_html=True)
            try:
                fig_type = _signal_type_chart(results.by_type)
                st.plotly_chart(fig_type, use_container_width=True, key="bt_type_bar")
            except Exception as e:
                st.error(f"Signal type chart error: {e}")

        st.markdown(source_footer([_demo_source]), unsafe_allow_html=True)

        # ── By Ticker Table ───────────────────────────────────────────────────────
        section_header("Performance by Ticker")
        try:
            if results.by_ticker:
                ticker_rows = []
                for ticker, stats in sorted(
                    results.by_ticker.items(),
                    key=lambda kv: kv[1]["win_rate"],
                    reverse=True,
                ):
                    ticker_rows.append([
                        _sans(ticker, color=C_TEXT, weight=700),
                        _mono(str(stats["count"]), color=C_TEXT2),
                        _mono(f"{stats['win_rate']:.1f}%", color=_winrate_color(stats["win_rate"])),
                        _mono(f"{stats['avg_return']:+.2f}%", color=_return_color(stats["avg_return"])),
                        _mono(f"{stats['total_return']:+.2f}%", color=_return_color(stats["total_return"])),
                    ])
                wsj_market_table(
                    headers=["Ticker", "Trades", "Win Rate", "Avg Return", "Total Return"],
                    rows=ticker_rows,
                )
                st.markdown(source_footer([_demo_source]), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Ticker table error: {e}")

        section_divider("Trade Detail")

        # ── Monthly Heatmap ───────────────────────────────────────────────────────
        section_header("Monthly Return Heatmap", "Average signal return by calendar month")
        try:
            fig_heat = _monthly_heatmap(results.monthly_returns)
            st.plotly_chart(fig_heat, use_container_width=True, key="bt_monthly_heatmap")
            st.markdown(source_footer([_demo_source]), unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Monthly heatmap error: {e}")

        # ── Trade Log ─────────────────────────────────────────────────────────────
        section_header("Full Trade Log")
        try:
            if results.trades:
                trade_rows = _build_trade_rows(results.trades)
                wsj_market_table(
                    headers=["Ticker", "Signal", "Conviction", "Entry Date", "Return %", "Hold", "Result"],
                    rows=trade_rows,
                )
                st.markdown(source_footer([_demo_source]), unsafe_allow_html=True)
            else:
                st.info("No trades to display.")
        except Exception as e:
            st.error(f"Trade log error: {e}")

        section_divider("Takeaways")

        # ── Key Insights ──────────────────────────────────────────────────────────
        section_header("Key Insights", "Auto-generated takeaways from the backtest run")
        try:
            insight_list = _build_insights(results)
            for title, body, color, score in insight_list:
                # Map the domain color → closest action label for insight_card_html
                if color == C_HIGH:
                    action = "Prioritize"
                elif color == C_MOD:
                    action = "Monitor"
                elif color == C_LOW:
                    action = "Caution"
                else:
                    action = "Watch"
                st.markdown(
                    insight_card_html(title=title, score=score, action=action, rationale=body),
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.error(f"Insights error: {e}")
