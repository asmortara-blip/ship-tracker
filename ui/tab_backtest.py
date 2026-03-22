"""Backtesting tab — historical alpha signal performance dashboard."""
from __future__ import annotations

import traceback

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from ui.styles import (
    C_BG, C_SURFACE, C_CARD, C_BORDER,
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_TEXT, C_TEXT2, C_TEXT3,
    C_CONV, C_MACRO,
    dark_layout,
    section_header,
)

# ---------------------------------------------------------------------------
# Color aliases
# ---------------------------------------------------------------------------

_C_WIN   = C_HIGH    # green
_C_LOSS  = C_LOW     # red
_C_NEUT  = C_ACCENT  # blue
_C_AMB   = C_MOD     # amber
_C_PURP  = C_CONV    # purple
_C_CYAN  = C_MACRO   # cyan
_C_BG    = C_BG
_C_SURF  = C_SURFACE
_C_CARD  = C_CARD

_CONVICTION_COLORS = {
    "HIGH":   _C_WIN,
    "MEDIUM": _C_AMB,
    "LOW":    _C_LOSS,
}

_TYPE_COLORS = {
    "MOMENTUM":       _C_NEUT,
    "MEAN_REVERSION": _C_PURP,
    "MACRO":          _C_CYAN,
    "TECHNICAL":      _C_AMB,
    "FUNDAMENTAL":    _C_WIN,
}


# ---------------------------------------------------------------------------
# KPI card helper
# ---------------------------------------------------------------------------

def _kpi_card(label: str, value: str, color: str, sub: str = "") -> str:
    sub_html = f'<div style="font-size:0.68rem;color:{C_TEXT3};margin-top:2px;">{sub}</div>' if sub else ""
    return f"""
    <div style="
        background:{_C_CARD};
        border:1px solid {C_BORDER};
        border-top:3px solid {color};
        border-radius:8px;
        padding:0.9rem 1.1rem;
        flex:1;
        min-width:130px;
    ">
        <div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;
                    letter-spacing:0.1em;margin-bottom:0.4rem;">{label}</div>
        <div style="font-size:1.5rem;font-weight:700;color:{color};line-height:1.1;">{value}</div>
        {sub_html}
    </div>
    """


def _insight_card(title: str, body: str, color: str = _C_NEUT) -> str:
    return f"""
    <div style="
        background:{_C_CARD};
        border:1px solid {C_BORDER};
        border-left:3px solid {color};
        border-radius:8px;
        padding:0.8rem 1rem;
        margin-bottom:0.6rem;
    ">
        <div style="font-size:0.78rem;font-weight:600;color:{C_TEXT};margin-bottom:0.25rem;">{title}</div>
        <div style="font-size:0.72rem;color:{C_TEXT2};line-height:1.5;">{body}</div>
    </div>
    """


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def _equity_curve_chart(equity_curve: list[dict], stock_data: dict) -> go.Figure:
    """Cumulative return of alpha signals vs buy-and-hold SPY proxy."""
    if not equity_curve:
        fig = go.Figure()
        fig.update_layout(**dark_layout(title="Equity Curve — No Data", height=380))
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
        line=dict(color=_C_WIN, width=2.5),
        fill="tozeroy",
        fillcolor=f"rgba(16,185,129,0.08)",
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

    fig.update_layout(**dark_layout(
        title="Cumulative Return — Alpha Signals vs Buy & Hold",
        height=380,
    ))
    fig.update_yaxes(ticksuffix="%")
    return fig


def _conviction_bar_chart(by_conviction: dict) -> go.Figure:
    """Grouped bar: win rate + avg return per conviction tier."""
    if not by_conviction:
        fig = go.Figure()
        fig.update_layout(**dark_layout(title="Performance by Conviction — No Data", height=340))
        return fig

    convictions = ["HIGH", "MEDIUM", "LOW"]
    convictions = [c for c in convictions if c in by_conviction]

    win_rates = [by_conviction[c]["win_rate"] for c in convictions]
    avg_returns = [by_conviction[c]["avg_return"] for c in convictions]
    colors = [_CONVICTION_COLORS.get(c, _C_NEUT) for c in convictions]

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
        marker_color=[f"rgba(59,130,246,0.7)"] * len(convictions),
        text=[f"{v:+.2f}%" for v in avg_returns],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>Avg Return: %{y:+.2f}%<extra></extra>",
    ))

    layout = dark_layout(title="Performance by Conviction Tier", height=340)
    layout["barmode"] = "group"
    layout["yaxis"] = {**layout.get("yaxis", {}), "title": "Win Rate (%)", "ticksuffix": "%"}
    layout["yaxis2"] = {
        "title": "Avg Return (%)",
        "overlaying": "y",
        "side": "right",
        "ticksuffix": "%",
        "gridcolor": "rgba(255,255,255,0.03)",
        "tickfont": {"color": C_TEXT3, "size": 11},
    }
    fig.update_layout(**layout)
    return fig


def _signal_type_chart(by_type: dict) -> go.Figure:
    """Horizontal bar: win rate per signal type."""
    if not by_type:
        fig = go.Figure()
        fig.update_layout(**dark_layout(title="Performance by Signal Type — No Data", height=300))
        return fig

    types = sorted(by_type.keys(), key=lambda t: by_type[t]["win_rate"], reverse=True)
    win_rates = [by_type[t]["win_rate"] for t in types]
    avg_rets = [by_type[t]["avg_return"] for t in types]
    colors = [_TYPE_COLORS.get(t, _C_NEUT) for t in types]

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

    layout = dark_layout(title="Win Rate by Signal Type", height=max(300, len(types) * 55 + 80))
    layout["xaxis"] = {**layout.get("xaxis", {}), "ticksuffix": "%", "range": [0, 110]}
    layout["showlegend"] = False
    fig.update_layout(**layout)
    return fig


def _monthly_heatmap(monthly_returns: list[dict]) -> go.Figure:
    """Heatmap of monthly returns — month vs year."""
    if not monthly_returns:
        fig = go.Figure()
        fig.update_layout(**dark_layout(title="Monthly Returns Heatmap — No Data", height=300))
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
            [0.0, "#ef4444"],
            [0.4, "#111827"],
            [0.5, "#1a2235"],
            [0.6, "#111827"],
            [1.0, "#10b981"],
        ],
        zmid=0,
        showscale=True,
        colorbar=dict(
            ticksuffix="%",
            tickfont=dict(color=C_TEXT3, size=10),
            bgcolor=_C_SURF,
        ),
        hovertemplate="<b>%{y} %{x}</b><br>Return: %{z:+.2f}%<extra></extra>",
    ))

    layout = dark_layout(title="Monthly Avg Return Heatmap", height=max(280, len(years) * 40 + 120))
    layout["showlegend"] = False
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Trade log formatter
# ---------------------------------------------------------------------------

def _build_trade_df(trades: list) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()

    rows = []
    for t in trades:
        rows.append({
            "Ticker": t.ticker,
            "Signal": t.signal_name,
            "Type": t.signal_type,
            "Dir": t.direction,
            "Conv": t.conviction,
            "Entry Date": t.entry_date,
            "Exit Date": t.exit_date,
            "Entry $": t.entry_price,
            "Exit $": t.exit_price,
            "Return %": t.return_pct,
            "Expected %": t.signal_expected_pct,
            "Hit": "✓" if t.hit else "✗",
            "Hold Days": t.holding_days,
            "Max DD %": t.max_drawdown_pct,
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("Entry Date", ascending=False).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Auto-generated key insights
# ---------------------------------------------------------------------------

def _build_insights(results) -> list[tuple[str, str, str]]:
    """Return list of (title, body, color) insight tuples."""
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
            _CONVICTION_COLORS.get(best_conv, _C_NEUT),
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
            _TYPE_COLORS.get(best_type, _C_NEUT),
        ))

    # Drawdown / risk insight
    if results.total_trades > 0:
        avg_dd = sum(t.max_drawdown_pct for t in results.trades) / len(results.trades)
        worst = results.worst_trade
        best = results.best_trade
        insights.append((
            f"Risk profile: avg intraday drawdown {avg_dd:.1f}%, worst trade {worst.return_pct:+.1f}% ({worst.ticker})",
            f"Best trade: {best.ticker} {best.signal_name} returned {best.return_pct:+.2f}% "
            f"(held {best.holding_days}d). "
            f"Worst trade: {worst.ticker} {worst.signal_name} returned {worst.return_pct:+.2f}% "
            f"(held {worst.holding_days}d). "
            f"Position sizing and stop-loss discipline are critical given shipping stock volatility.",
            _C_AMB,
        ))

    return insights


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(stock_data: dict, macro_data: dict, insights: object) -> None:
    """Render the full Backtesting tab."""

    section_header("Alpha Signal Backtester", "Simulate historical performance of shipping stock signals")

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
    st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)

    tr_color = _C_WIN if results.total_return_pct >= 0 else _C_LOSS
    wr_color = _C_WIN if results.win_rate >= 55 else (_C_AMB if results.win_rate >= 45 else _C_LOSS)
    sh_color = _C_WIN if results.sharpe_ratio >= 1 else (_C_AMB if results.sharpe_ratio >= 0 else _C_LOSS)
    dd_color = _C_WIN if results.max_drawdown >= -5 else (_C_AMB if results.max_drawdown >= -15 else _C_LOSS)

    kpi_html = "".join([
        _kpi_card("Total Return", f"{results.total_return_pct:+.1f}%", tr_color, "sum equal-weight"),
        _kpi_card("Win Rate", f"{results.win_rate:.1f}%", wr_color, "trades in right direction"),
        _kpi_card("Sharpe Ratio", f"{results.sharpe_ratio:.2f}", sh_color, "annualized"),
        _kpi_card("Total Trades", str(results.total_trades), _C_NEUT, f"~{lookback}d window"),
        _kpi_card("Max Drawdown", f"{results.max_drawdown:.1f}%", dd_color, "worst intraday"),
    ])

    st.markdown(
        f'<div style="display:flex;gap:0.75rem;flex-wrap:wrap;margin-bottom:1.5rem;">{kpi_html}</div>',
        unsafe_allow_html=True,
    )

    # ── Equity Curve ──────────────────────────────────────────────────────────
    st.markdown("#### Equity Curve")
    try:
        fig_eq = _equity_curve_chart(results.equity_curve, stock_data)
        st.plotly_chart(fig_eq, use_container_width=True, key="bt_equity_curve")
    except Exception as e:
        st.error(f"Equity curve error: {e}")

    # ── Performance by Conviction ─────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### By Conviction Tier")
        try:
            fig_conv = _conviction_bar_chart(results.by_conviction)
            st.plotly_chart(fig_conv, use_container_width=True, key="bt_conviction_bar")
        except Exception as e:
            st.error(f"Conviction chart error: {e}")

    with col2:
        st.markdown("#### By Signal Type")
        try:
            fig_type = _signal_type_chart(results.by_type)
            st.plotly_chart(fig_type, use_container_width=True, key="bt_type_bar")
        except Exception as e:
            st.error(f"Signal type chart error: {e}")

    # ── By Ticker Table ───────────────────────────────────────────────────────
    st.markdown("#### Performance by Ticker")
    try:
        if results.by_ticker:
            ticker_rows = []
            for ticker, stats in results.by_ticker.items():
                ticker_rows.append({
                    "Ticker": ticker,
                    "Trades": stats["count"],
                    "Win Rate %": stats["win_rate"],
                    "Avg Return %": stats["avg_return"],
                    "Total Return %": stats["total_return"],
                })
            ticker_df = pd.DataFrame(ticker_rows).sort_values("Win Rate %", ascending=False)

            def _color_ret(val):
                if isinstance(val, float):
                    color = "#10b981" if val > 0 else "#ef4444"
                    return f"color: {color}"
                return ""

            styled = ticker_df.style.applymap(
                _color_ret, subset=["Avg Return %", "Total Return %"]
            ).format({
                "Win Rate %": "{:.1f}%",
                "Avg Return %": "{:+.2f}%",
                "Total Return %": "{:+.2f}%",
            })
            st.dataframe(styled, use_container_width=True, key="bt_ticker_table")
    except Exception as e:
        st.error(f"Ticker table error: {e}")

    # ── Monthly Heatmap ───────────────────────────────────────────────────────
    st.markdown("#### Monthly Return Heatmap")
    try:
        fig_heat = _monthly_heatmap(results.monthly_returns)
        st.plotly_chart(fig_heat, use_container_width=True, key="bt_monthly_heatmap")
    except Exception as e:
        st.error(f"Monthly heatmap error: {e}")

    # ── Trade Log ─────────────────────────────────────────────────────────────
    st.markdown("#### Full Trade Log")
    try:
        trade_df = _build_trade_df(results.trades)
        if not trade_df.empty:
            def _color_return(val):
                if isinstance(val, (int, float)):
                    return f"color: {'#10b981' if val > 0 else '#ef4444'}"
                return ""

            def _color_hit(val):
                if val == "✓":
                    return "color: #10b981"
                if val == "✗":
                    return "color: #ef4444"
                return ""

            styled_trades = trade_df.style.applymap(
                _color_return, subset=["Return %", "Expected %", "Max DD %"]
            ).applymap(
                _color_hit, subset=["Hit"]
            ).format({
                "Entry $": "{:.2f}",
                "Exit $": "{:.2f}",
                "Return %": "{:+.2f}%",
                "Expected %": "{:+.2f}%",
                "Max DD %": "{:.2f}%",
            })
            st.dataframe(
                styled_trades,
                use_container_width=True,
                height=400,
                key="bt_trade_log",
            )
        else:
            st.info("No trades to display.")
    except Exception as e:
        st.error(f"Trade log error: {e}")

    # ── Key Insights ──────────────────────────────────────────────────────────
    st.markdown("#### Key Insights")
    try:
        insight_list = _build_insights(results)
        for title, body, color in insight_list:
            st.markdown(_insight_card(title, body, color), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Insights error: {e}")
