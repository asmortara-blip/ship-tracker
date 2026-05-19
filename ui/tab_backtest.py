"""Backtesting tab — historical alpha signal performance dashboard."""
from __future__ import annotations

import traceback

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
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
# Main render
# ---------------------------------------------------------------------------

def render(stock_data: dict, macro_data: dict, insights: object) -> None:
    """Render the full Backtesting tab."""

    page_header(
        title="Alpha Signal Backtester",
        subtitle="Simulate historical performance of shipping stock signals",
        badge_text="DEMO",
        badge_color=C_LOW,
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
