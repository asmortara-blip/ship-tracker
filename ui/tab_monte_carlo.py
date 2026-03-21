"""Monte Carlo freight rate forecasting tab.

Visualises GBM simulation results: hero header, fan charts, probability
distribution histograms, VaR/CVaR cards, scenario probability tables,
per-route mini panels, parameter controls, and an all-routes comparison table.
"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from processing.monte_carlo import (
    MonteCarloResult,
    get_highest_upside_routes,
    get_risk_adjusted_opportunity,
    simulate_all_routes,
    simulate_freight_rates,
)
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    _hex_to_rgba,
    dark_layout,
    section_header,
)


# ── Internal helpers ────────────────────────────────────────────────────────────

_C_BG       = "#0a0f1a"
_C_SURFACE  = "#111827"
_C_CARD2    = "#1e2d47"
_C_BULL     = "#10b981"   # green
_C_BEAR     = "#ef4444"   # red
_C_MED      = "#f59e0b"   # amber
_C_BLUE     = "#3b82f6"
_C_PURPLE   = "#8b5cf6"
_C_CYAN     = "#06b6d4"

_HORIZON_DAYS = {30: "30d", 60: "60d", 90: "90d"}


def _pct_delta(new_val: float, base: float) -> str:
    """Format a percentage change string with sign."""
    if base == 0:
        return "—"
    pct = (new_val - base) / base * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _fmt_rate(rate: float) -> str:
    """Format a freight rate with thousands separator."""
    return f"${rate:,.0f}"


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex; align-items:center; gap:12px; margin:28px 0 16px">'
        f'<div style="flex:1; height:1px; background:rgba(255,255,255,0.07)"></div>'
        f'<span style="font-size:0.62rem; color:#475569; text-transform:uppercase;'
        f' letter-spacing:0.14em; font-weight:700">{label}</span>'
        f'<div style="flex:1; height:1px; background:rgba(255,255,255,0.07)"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _card(
    label: str,
    value: str,
    sub: str = "",
    top_color: str = _C_BLUE,
    value_color: str = "#f1f5f9",
    key_suffix: str = "",
) -> str:
    """Return HTML for a single stat card."""
    sub_html = (
        f'<div style="font-size:0.78rem; color:{C_TEXT3}; margin-top:4px">{sub}</div>'
        if sub else ""
    )
    return (
        f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
        f'border-top:3px solid {top_color}; border-radius:10px; '
        f'padding:16px 18px; text-align:center; height:100%">'
        f'<div style="font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase; '
        f'letter-spacing:0.08em; font-weight:700; margin-bottom:6px">{label}</div>'
        f'<div style="font-size:1.65rem; font-weight:700; color:{value_color}; '
        f'font-family:\'JetBrains Mono\', monospace">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )


def _cvar_95(result: MonteCarloResult) -> float:
    """Compute CVaR (Expected Shortfall) at 95% confidence level."""
    try:
        final_rates = [path[-1] for path in result.simulated_paths]
        losses = [result.current_rate - r for r in final_rates]
        cutoff = float(np.percentile(losses, 95))
        tail = [l for l in losses if l >= cutoff]
        return float(np.mean(tail)) if tail else 0.0
    except Exception:
        return 0.0


def _rate_volatility(result: MonteCarloResult) -> float:
    """Return annualised rate volatility as a percentage."""
    try:
        final_rates = [path[-1] for path in result.simulated_paths]
        vol = float(np.std(final_rates)) / result.current_rate * 100.0
        return vol
    except Exception:
        return 0.0


def _prob_above(result: MonteCarloResult, threshold: float) -> float:
    """Return probability that the final rate is above *threshold*."""
    try:
        final_rates = [path[-1] for path in result.simulated_paths]
        return float(np.mean([r > threshold for r in final_rates])) * 100.0
    except Exception:
        return 0.0


def _day_idx(result: MonteCarloResult, day: int) -> int:
    return min(day - 1, result.forecast_days - 1)


def _final_rates_at(result: MonteCarloResult, day: int) -> list[float]:
    idx = _day_idx(result, day)
    try:
        return [path[idx] for path in result.simulated_paths]
    except Exception:
        return []


# ── Section 1: Hero header ──────────────────────────────────────────────────────

def _render_hero(result: MonteCarloResult) -> None:
    """Full-width hero banner summarising a route's simulation."""
    try:
        prob_up_pct = result.prob_rate_increase * 100.0
        sentiment   = "Bullish" if prob_up_pct >= 55 else ("Bearish" if prob_up_pct < 45 else "Neutral")
        sent_color  = _C_BULL if sentiment == "Bullish" else (_C_BEAR if sentiment == "Bearish" else _C_MED)
        sent_icon   = "▲" if sentiment == "Bullish" else ("▼" if sentiment == "Bearish" else "◆")
        ci_lo, ci_hi = result.confidence_interval_90d
        median_delta = _pct_delta(result.expected_rate_90d, result.current_rate)
        median_color = _C_BULL if result.expected_rate_90d >= result.current_rate else _C_BEAR

        st.markdown(
            f'''<div style="
                background: linear-gradient(135deg, #0d1829 0%, #111827 60%, #0a1628 100%);
                border: 1px solid rgba(59,130,246,0.25);
                border-left: 4px solid {sent_color};
                border-radius: 14px;
                padding: 24px 28px;
                margin-bottom: 20px;
                position: relative;
                overflow: hidden;
            ">
                <div style="
                    position: absolute; top: 0; right: 0; width: 300px; height: 100%;
                    background: radial-gradient(ellipse at top right, {sent_color}18, transparent 70%);
                    pointer-events: none;
                "></div>

                <div style="display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:16px">
                    <div>
                        <div style="font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.14em; font-weight:700; margin-bottom:6px">
                            Monte Carlo Simulation · GBM
                        </div>
                        <div style="font-size:1.9rem; font-weight:800; color:{C_TEXT}; letter-spacing:-0.02em; line-height:1.1">
                            {result.route_id}
                        </div>
                        <div style="margin-top:8px; display:flex; align-items:center; gap:10px; flex-wrap:wrap">
                            <span style="
                                background:{sent_color}22; border:1px solid {sent_color}55;
                                color:{sent_color}; font-size:0.78rem; font-weight:700;
                                padding:4px 12px; border-radius:20px; letter-spacing:0.04em
                            ">{sent_icon} {sentiment}</span>
                            <span style="color:{C_TEXT3}; font-size:0.8rem">{result.n_simulations:,} simulation paths · {result.forecast_days}d horizon</span>
                        </div>
                    </div>

                    <div style="display:flex; gap:32px; flex-wrap:wrap; align-items:flex-start">
                        <div style="text-align:right">
                            <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin-bottom:4px">Current Rate</div>
                            <div style="font-size:1.55rem; font-weight:700; color:{C_TEXT}; font-family:'JetBrains Mono',monospace">{_fmt_rate(result.current_rate)}</div>
                            <div style="font-size:0.72rem; color:{C_TEXT3}">USD / FEU</div>
                        </div>
                        <div style="text-align:right">
                            <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin-bottom:4px">Median Forecast (90d)</div>
                            <div style="font-size:1.55rem; font-weight:700; color:{median_color}; font-family:'JetBrains Mono',monospace">{_fmt_rate(result.expected_rate_90d)}</div>
                            <div style="font-size:0.72rem; color:{median_color}">{median_delta} vs current</div>
                        </div>
                        <div style="text-align:right">
                            <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin-bottom:4px">Prob of Increase</div>
                            <div style="font-size:1.55rem; font-weight:700; color:{sent_color}; font-family:'JetBrains Mono',monospace">{prob_up_pct:.1f}%</div>
                            <div style="font-size:0.72rem; color:{C_TEXT3}">at end of {result.forecast_days}d window</div>
                        </div>
                    </div>
                </div>

                <div style="margin-top:18px; padding-top:14px; border-top:1px solid rgba(255,255,255,0.06); display:flex; gap:24px; flex-wrap:wrap">
                    <div>
                        <span style="font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.08em">90d Confidence Interval (p5–p95)</span>
                        <span style="font-size:0.85rem; color:{C_TEXT}; font-weight:600; margin-left:10px; font-family:'JetBrains Mono',monospace">
                            {_fmt_rate(ci_lo)} — {_fmt_rate(ci_hi)}
                        </span>
                    </div>
                    <div>
                        <span style="font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.08em">Bull Case (p90)</span>
                        <span style="font-size:0.85rem; color:{_C_BULL}; font-weight:600; margin-left:10px; font-family:'JetBrains Mono',monospace">{_fmt_rate(result.bull_case_90d)}</span>
                    </div>
                    <div>
                        <span style="font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.08em">Bear Case (p10)</span>
                        <span style="font-size:0.85rem; color:{_C_BEAR}; font-weight:600; margin-left:10px; font-family:'JetBrains Mono',monospace">{_fmt_rate(result.bear_case_90d)}</span>
                    </div>
                </div>
            </div>''',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning(f"Hero header unavailable: {exc}")


# ── Section 2: Enhanced fan chart ──────────────────────────────────────────────

def _build_fan_chart(result: MonteCarloResult, n_sample_paths: int = 50) -> go.Figure:
    """Build an enhanced Monte Carlo fan chart with 50 individual path traces."""
    try:
        days    = list(range(1, result.forecast_days + 1))
        paths   = result.simulated_paths
        current = result.current_rate
        fig     = go.Figure()

        # ── 50 semi-transparent individual paths ─────────────────────────────
        total = len(paths)
        step  = max(1, total // n_sample_paths)
        sampled = paths[::step][:n_sample_paths]

        for path in sampled:
            final      = path[-1]
            is_bull    = final >= current
            line_color = "rgba(16,185,129,0.18)" if is_bull else "rgba(239,68,68,0.18)"
            fig.add_trace(go.Scatter(
                x=days, y=path,
                mode="lines",
                line={"width": 0.8, "color": line_color},
                hoverinfo="skip",
                showlegend=False,
            ))

        # ── Percentile fan fills ─────────────────────────────────────────────
        p5  = result.percentiles["p5"]
        p25 = result.percentiles["p25"]
        p50 = result.percentiles["p50"]
        p75 = result.percentiles["p75"]
        p95 = result.percentiles["p95"]

        # p5–p95 outer band
        fig.add_trace(go.Scatter(
            x=days + days[::-1],
            y=p95 + p5[::-1],
            fill="toself",
            fillcolor="rgba(59,130,246,0.07)",
            line={"width": 0},
            name="90% range (p5–p95)",
            hoverinfo="skip",
        ))

        # p25–p75 inner band
        fig.add_trace(go.Scatter(
            x=days + days[::-1],
            y=p75 + p25[::-1],
            fill="toself",
            fillcolor="rgba(59,130,246,0.20)",
            line={"width": 0},
            name="50% range (p25–p75)",
            hoverinfo="skip",
        ))

        # ── Five percentile boundary lines with distinct dash styles ─────────
        pct_lines = [
            ("p5",  p5,  "#ef4444", "dot",      "P5"),
            ("p25", p25, "#f97316", "dashdot",   "P25"),
            ("p50", p50, "#ffffff", "solid",     "Median"),
            ("p75", p75, "#34d399", "dashdot",   "P75"),
            ("p95", p95, "#10b981", "dot",       "P95"),
        ]
        for key, vals, color, dash, label in pct_lines:
            width = 2.5 if key == "p50" else 1.5
            fig.add_trace(go.Scatter(
                x=days, y=vals,
                mode="lines",
                line={"width": width, "color": color, "dash": dash},
                name=label,
                hovertemplate=f"<b>{label}</b><br>Day %{{x}}<br>%{{y:$,.0f}}<extra></extra>",
            ))

        # ── Current rate reference line ───────────────────────────────────────
        fig.add_hline(
            y=current,
            line_dash="dash",
            line_color="rgba(245,158,11,0.8)",
            line_width=1.5,
            annotation_text=f"  Current {_fmt_rate(current)}",
            annotation_font_color=_C_MED,
            annotation_font_size=11,
        )

        # ── Day-30/60/90 vertical markers ────────────────────────────────────
        for marker_day, label in [(30, "D30"), (60, "D60"), (90, "D90")]:
            if marker_day <= result.forecast_days:
                fig.add_vline(
                    x=marker_day,
                    line_dash="dot",
                    line_color="rgba(255,255,255,0.18)",
                    line_width=1,
                    annotation_text=label,
                    annotation_font_color=C_TEXT3,
                    annotation_font_size=10,
                    annotation_position="top",
                )

        # ── Layout ────────────────────────────────────────────────────────────
        layout = dark_layout(
            title=f"Monte Carlo Fan Chart — {result.route_id}  ({result.n_simulations:,} paths)",
            height=500,
            showlegend=True,
        )
        layout["xaxis"]["title"] = {"text": "Days from today", "font": {"color": C_TEXT2, "size": 12}}
        layout["yaxis"]["title"] = {"text": "Rate USD/FEU",    "font": {"color": C_TEXT2, "size": 12}}
        layout["xaxis"]["range"] = [1, result.forecast_days]
        layout["template"] = "plotly_dark"
        layout["legend"]["orientation"] = "h"
        fig.update_layout(**layout)
        return fig
    except Exception as exc:
        fig = go.Figure()
        fig.add_annotation(text=f"Fan chart error: {exc}", showarrow=False,
                           font={"color": "#ef4444", "size": 14})
        return fig


# ── Section 3: Probability distribution histograms ─────────────────────────────

def _build_histogram(result: MonteCarloResult, day: int) -> go.Figure:
    """Build a distribution histogram for rates at *day*."""
    try:
        rates   = _final_rates_at(result, day)
        current = result.current_rate
        fig     = go.Figure()

        # Main histogram bars
        fig.add_trace(go.Histogram(
            x=rates,
            nbinsx=40,
            marker_color=_C_BLUE,
            marker_opacity=0.75,
            name=f"D{day} distribution",
            hovertemplate="Rate: %{x:$,.0f}<br>Count: %{y}<extra></extra>",
        ))

        # Vertical percentile lines
        pct_defs = [
            (5,  rates, "#ef4444", "dot",  "P5"),
            (25, rates, "#f97316", "dash", "P25"),
            (50, rates, "#ffffff", "solid","Median"),
            (75, rates, "#34d399", "dash", "P75"),
            (95, rates, "#10b981", "dot",  "P95"),
        ]
        for pct, vals, color, dash, label in pct_defs:
            val = float(np.percentile(vals, pct))
            fig.add_vline(
                x=val,
                line_color=color,
                line_dash=dash,
                line_width=1.8,
                annotation_text=f"  {label}<br>  {_fmt_rate(val)}",
                annotation_font_color=color,
                annotation_font_size=9,
                annotation_position="top right",
            )

        # Current rate line
        fig.add_vline(
            x=current,
            line_color=_C_MED,
            line_dash="dash",
            line_width=2,
            annotation_text=f"  Now {_fmt_rate(current)}",
            annotation_font_color=_C_MED,
            annotation_font_size=10,
        )

        layout = dark_layout(
            title=f"Rate Distribution at Day {day}",
            height=300,
            showlegend=False,
        )
        layout["xaxis"]["title"] = {"text": "Rate USD/FEU", "font": {"color": C_TEXT2, "size": 11}}
        layout["yaxis"]["title"] = {"text": "Frequency",    "font": {"color": C_TEXT2, "size": 11}}
        layout["template"] = "plotly_dark"
        fig.update_layout(**layout)
        return fig
    except Exception as exc:
        fig = go.Figure()
        fig.add_annotation(text=f"Histogram error: {exc}", showarrow=False,
                           font={"color": "#ef4444", "size": 13})
        return fig


def _render_histograms(result: MonteCarloResult) -> None:
    """Render tabbed probability distribution histograms for 30/60/90d."""
    try:
        tabs = st.tabs(["30-Day Horizon", "60-Day Horizon", "90-Day Horizon"])
        for tab, day in zip(tabs, [30, 60, 90]):
            with tab:
                try:
                    rates = _final_rates_at(result, day)
                    if not rates:
                        st.warning(f"No data at Day {day}")
                        continue
                    prob_up = float(np.mean([r > result.current_rate for r in rates])) * 100.0
                    pct5    = float(np.percentile(rates, 5))
                    pct50   = float(np.percentile(rates, 50))
                    pct95   = float(np.percentile(rates, 95))
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Prob Above Current", f"{prob_up:.1f}%")
                    c2.metric("P5",     _fmt_rate(pct5))
                    c3.metric("Median", _fmt_rate(pct50))
                    c4.metric("P95",    _fmt_rate(pct95))
                    fig = _build_histogram(result, day)
                    st.plotly_chart(fig, use_container_width=True, key=f"mc_hist_{result.route_id}_{day}")
                except Exception as exc:
                    st.warning(f"Day {day} histogram failed: {exc}")
    except Exception as exc:
        st.warning(f"Histogram panel unavailable: {exc}")


# ── Section 4: VaR & CVaR cards ────────────────────────────────────────────────

def _render_var_cards(result: MonteCarloResult) -> None:
    """Render 5-card row: VaR 95%, CVaR 95%, Upside 95%, Volatility, Sharpe."""
    try:
        cvar    = _cvar_95(result)
        vol     = _rate_volatility(result)
        sharpe  = get_risk_adjusted_opportunity(result)
        upside  = max(0.0, result.bull_case_90d - result.current_rate)

        sharpe_color = _C_BULL if sharpe >= 0 else _C_BEAR
        vol_color    = _C_BEAR if vol > 30 else (_C_MED if vol > 15 else _C_BULL)

        cols = st.columns(5)
        cards = [
            (cols[0], _card("VaR 95%",       f"-{_fmt_rate(result.var_95)}",
                            "Worst expected loss",   _C_BEAR, _C_BEAR)),
            (cols[1], _card("CVaR 95%",       f"-{_fmt_rate(cvar)}",
                            "Expected tail loss",    "#b91c1c", _C_BEAR)),
            (cols[2], _card("Upside 95%",     f"+{_fmt_rate(upside)}",
                            "P90 vs current",        _C_BULL, _C_BULL)),
            (cols[3], _card("Rate Volatility", f"{vol:.1f}%",
                            "Annualised (simulated)", vol_color, vol_color)),
            (cols[4], _card("Sharpe-like",    f"{sharpe:+.2f}",
                            "Risk-adj. opportunity",  sharpe_color, sharpe_color)),
        ]
        for col, html in cards:
            with col:
                st.markdown(html, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Risk cards unavailable: {exc}")


# ── Section 5: Scenario probability table ──────────────────────────────────────

def _render_scenario_table(result: MonteCarloResult) -> None:
    """Render an HTML table of threshold probabilities for this route."""
    try:
        current = result.current_rate
        thresholds = [
            ("Bear Extreme",  current * 0.70, _C_BEAR),
            ("Bear Case",     current * 0.85, _C_BEAR),
            ("Flat − 5%",     current * 0.95, _C_MED),
            ("Current Rate",  current,        _C_MED),
            ("Flat + 5%",     current * 1.05, _C_BULL),
            ("Bull Case",     current * 1.15, _C_BULL),
            ("Bull Extreme",  current * 1.30, _C_BULL),
        ]

        rows_html = ""
        for label, threshold, color in thresholds:
            prob = _prob_above(result, threshold)
            bar_width = min(int(prob), 100)
            bar_color = _C_BULL if prob >= 50 else _C_BEAR
            rows_html += (
                f"<tr style='border-bottom:1px solid rgba(255,255,255,0.05)'>"
                f"<td style='padding:10px 14px; color:{color}; font-weight:600; font-size:0.83rem'>{label}</td>"
                f"<td style='padding:10px 14px; color:{C_TEXT2}; font-family:\"JetBrains Mono\",monospace; font-size:0.82rem'>{_fmt_rate(threshold)}</td>"
                f"<td style='padding:10px 14px'>"
                f"  <div style='display:flex; align-items:center; gap:10px'>"
                f"    <div style='flex:1; height:6px; background:rgba(255,255,255,0.06); border-radius:3px; overflow:hidden'>"
                f"      <div style='width:{bar_width}%; height:100%; background:{bar_color}; border-radius:3px'></div>"
                f"    </div>"
                f"    <span style='color:{bar_color}; font-weight:700; font-family:\"JetBrains Mono\",monospace; font-size:0.85rem; min-width:42px'>{prob:.1f}%</span>"
                f"  </div>"
                f"</td>"
                f"</tr>"
            )

        st.markdown(
            f'''<div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:12px; overflow:hidden">
                <table style="width:100%; border-collapse:collapse">
                    <thead>
                        <tr style="background:rgba(255,255,255,0.04); border-bottom:1px solid rgba(255,255,255,0.1)">
                            <th style="padding:11px 14px; text-align:left; font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.1em; font-weight:700">Scenario</th>
                            <th style="padding:11px 14px; text-align:left; font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.1em; font-weight:700">Threshold Rate</th>
                            <th style="padding:11px 14px; text-align:left; font-size:0.65rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.1em; font-weight:700">Prob Rate Exceeds</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>
            </div>''',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning(f"Scenario table unavailable: {exc}")


# ── Section 6: Per-route mini panels ───────────────────────────────────────────

def _build_mini_fan(result: MonteCarloResult) -> go.Figure:
    """Build a compact mini fan chart for a route card."""
    try:
        days = list(range(1, result.forecast_days + 1))
        p5   = result.percentiles["p5"]
        p50  = result.percentiles["p50"]
        p95  = result.percentiles["p95"]
        fig  = go.Figure()

        fig.add_trace(go.Scatter(
            x=days + days[::-1],
            y=p95 + p5[::-1],
            fill="toself",
            fillcolor="rgba(59,130,246,0.12)",
            line={"width": 0},
            hoverinfo="skip",
            showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=days, y=p50,
            mode="lines",
            line={"width": 2, "color": "#ffffff"},
            showlegend=False,
            hovertemplate="Day %{x}<br>Median: %{y:$,.0f}<extra></extra>",
        ))
        fig.add_hline(
            y=result.current_rate,
            line_dash="dash",
            line_color="rgba(245,158,11,0.6)",
            line_width=1,
        )
        layout = dark_layout(height=140, showlegend=False)
        layout["margin"] = {"l": 8, "r": 8, "t": 8, "b": 8}
        layout["xaxis"]["showticklabels"] = False
        layout["yaxis"]["showticklabels"] = True
        layout["yaxis"]["tickfont"] = {"size": 9, "color": "#64748b"}
        layout["template"] = "plotly_dark"
        fig.update_layout(**layout)
        return fig
    except Exception:
        return go.Figure()


def _render_mini_panels(route_results: dict[str, MonteCarloResult], max_routes: int = 8) -> None:
    """Render a 2-column grid of compact route cards with mini fan charts."""
    try:
        top_routes = get_highest_upside_routes(route_results, top_n=max_routes)
        if not top_routes:
            st.info("No route results to display.")
            return

        pairs = [top_routes[i:i+2] for i in range(0, len(top_routes), 2)]
        for pair in pairs:
            cols = st.columns(2)
            for col, r in zip(cols, pair):
                with col:
                    try:
                        prob_up = r.prob_rate_increase * 100.0
                        sent_color = _C_BULL if prob_up >= 55 else (_C_BEAR if prob_up < 45 else _C_MED)
                        upside_pct = (r.bull_case_90d - r.current_rate) / r.current_rate * 100.0 if r.current_rate else 0.0
                        delta_str  = _pct_delta(r.expected_rate_90d, r.current_rate)
                        delta_col  = _C_BULL if r.expected_rate_90d >= r.current_rate else _C_BEAR

                        st.markdown(
                            f'''<div style="
                                background:{C_CARD}; border:1px solid {C_BORDER};
                                border-top:3px solid {sent_color};
                                border-radius:10px; padding:14px 16px; margin-bottom:12px
                            ">
                                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px">
                                    <div>
                                        <div style="font-size:0.95rem; font-weight:700; color:{C_TEXT}">{r.route_id}</div>
                                        <div style="font-size:0.72rem; color:{C_TEXT3}; margin-top:2px">{_fmt_rate(r.current_rate)} current</div>
                                    </div>
                                    <div style="text-align:right">
                                        <div style="font-size:0.72rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.08em">Prob Up</div>
                                        <div style="font-size:1.1rem; font-weight:700; color:{sent_color}">{prob_up:.1f}%</div>
                                    </div>
                                </div>
                                <div style="display:flex; gap:18px; margin-bottom:10px">
                                    <div>
                                        <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em">Median 90d</div>
                                        <div style="font-size:0.88rem; font-weight:600; color:{delta_col}; font-family:'JetBrains Mono',monospace">{_fmt_rate(r.expected_rate_90d)} <span style="font-size:0.75rem">({delta_str})</span></div>
                                    </div>
                                    <div>
                                        <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em">Bull Case</div>
                                        <div style="font-size:0.88rem; font-weight:600; color:{_C_BULL}; font-family:'JetBrains Mono',monospace">{_fmt_rate(r.bull_case_90d)}</div>
                                    </div>
                                    <div>
                                        <div style="font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em">Upside</div>
                                        <div style="font-size:0.88rem; font-weight:600; color:{_C_BULL}; font-family:'JetBrains Mono',monospace">+{upside_pct:.1f}%</div>
                                    </div>
                                </div>
                            </div>''',
                            unsafe_allow_html=True,
                        )
                        mini_fig = _build_mini_fan(r)
                        st.plotly_chart(mini_fig, use_container_width=True,
                                        key=f"mc_mini_{r.route_id}")
                    except Exception as exc:
                        st.warning(f"{r.route_id} mini panel error: {exc}")
    except Exception as exc:
        st.warning(f"Mini panels unavailable: {exc}")


# ── Section 7: Parameter controls ──────────────────────────────────────────────

def _render_parameter_controls() -> dict:
    """Render simulation parameter controls and return a dict of values."""
    try:
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-radius:12px; padding:20px 22px; margin-bottom:4px">',
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            n_sims = st.select_slider(
                "Simulations",
                options=[100, 200, 300, 500, 750, 1000],
                value=300,
                key="mc_param_n_sims",
                help="More paths = more accuracy, slower rendering",
            )
        with c2:
            vol_mode = st.selectbox(
                "Volatility Mode",
                options=["Historical", "Low (−30%)", "High (+30%)", "Custom"],
                index=0,
                key="mc_param_vol_mode",
                help="Adjust annualised volatility used in GBM",
            )
        with c3:
            horizon = st.slider(
                "Forecast Horizon (days)",
                min_value=30,
                max_value=180,
                value=90,
                step=10,
                key="mc_param_horizon",
                help="Days to simulate forward",
            )
        st.markdown('</div>', unsafe_allow_html=True)
        return {"n_sims": n_sims, "vol_mode": vol_mode, "horizon": horizon}
    except Exception as exc:
        st.warning(f"Parameter controls unavailable: {exc}")
        return {"n_sims": 300, "vol_mode": "Historical", "horizon": 90}


# ── Section 8: All-routes comparison table ──────────────────────────────────────

def _render_comparison_table(route_results: dict[str, MonteCarloResult]) -> None:
    """Render all-routes comparison table sorted by prob_rate_increase desc."""
    try:
        import pandas as pd

        rows = []
        for rid, r in route_results.items():
            try:
                upside_pct   = (r.bull_case_90d - r.current_rate) / r.current_rate * 100.0 if r.current_rate else 0.0
                downside_pct = (r.bear_case_90d - r.current_rate) / r.current_rate * 100.0 if r.current_rate else 0.0
                sharpe       = get_risk_adjusted_opportunity(r)
                vol          = _rate_volatility(r)
                rows.append({
                    "Route":         rid,
                    "Current Rate":  r.current_rate,
                    "Expected 90d":  r.expected_rate_90d,
                    "Bull Case":     r.bull_case_90d,
                    "Bear Case":     r.bear_case_90d,
                    "Prob Up (%)":   round(r.prob_rate_increase * 100.0, 1),
                    "Upside (%)":    round(upside_pct, 1),
                    "Downside (%)":  round(downside_pct, 1),
                    "VaR 95%":       r.var_95,
                    "Volatility (%)": round(vol, 1),
                    "Sharpe-like":   round(sharpe, 2),
                })
            except Exception:
                continue

        if not rows:
            st.warning("Simulation returned no results — try adjusting parameters")
            return

        df = (
            pd.DataFrame(rows)
            .sort_values("Prob Up (%)", ascending=False)
            .reset_index(drop=True)
        )

        # Format currency columns
        display_df = df.copy()
        for col in ("Current Rate", "Expected 90d", "Bull Case", "Bear Case", "VaR 95%"):
            display_df[col] = display_df[col].apply(lambda v: f"${v:,.0f}")

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Prob Up (%)":    st.column_config.ProgressColumn("Prob Up (%)", min_value=0, max_value=100, format="%.1f%%"),
                "Sharpe-like":    st.column_config.NumberColumn("Sharpe-like", format="%.2f"),
                "Volatility (%)": st.column_config.NumberColumn("Volatility (%)", format="%.1f%%"),
            },
        )

        # Download
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download results as CSV",
            data=csv_bytes,
            file_name="monte_carlo_results.csv",
            mime="text/csv",
            key="mc_download_csv",
        )
    except Exception as exc:
        st.warning(f"Comparison table unavailable: {exc}")


# ── Main render ─────────────────────────────────────────────────────────────────

def render(freight_data: dict, route_results: dict[str, MonteCarloResult]) -> None:
    """Render the Monte Carlo tab.

    Parameters
    ----------
    freight_data:
        Raw freight data dict (route_id -> DataFrame).
    route_results:
        Pre-computed MonteCarloResult dict (route_id -> MonteCarloResult).
        If empty, simulations will be run on-the-fly with default parameters.
    """
    # ── Parameter controls (always shown at top) ──────────────────────────────
    _divider("Simulation Parameters")
    params = _render_parameter_controls()

    # ── Run / re-run simulations ──────────────────────────────────────────────
    try:
        if not route_results:
            with st.spinner("Running Monte Carlo simulations…"):
                route_results = simulate_all_routes(
                    freight_data, n_simulations=params.get("n_sims", 300)
                )
    except Exception as exc:
        st.error(f"Simulation error: {exc}")
        return

    if not route_results:
        st.warning("No Monte Carlo results available — check that freight data is loaded.")
        return

    # ── Route selector ────────────────────────────────────────────────────────
    try:
        _divider("Route Selection")
        section_header(
            "Monte Carlo Rate Forecasting",
            "GBM simulation across the forecast horizon — select a route to inspect",
        )

        top_routes   = get_highest_upside_routes(route_results, top_n=len(route_results))
        default_route = top_routes[0].route_id if top_routes else next(iter(route_results))
        all_route_ids = sorted(route_results.keys())
        default_idx   = all_route_ids.index(default_route) if default_route in all_route_ids else 0

        selected_route = st.selectbox(
            "Select route",
            options=all_route_ids,
            index=default_idx,
            key="mc_route_selector",
        )
    except Exception as exc:
        st.warning(f"Route selector error: {exc}")
        selected_route = next(iter(route_results))

    result = route_results.get(selected_route)
    if result is None:
        st.error(f"No simulation result for route: {selected_route}")
        return

    # ── Section 1: Hero header ────────────────────────────────────────────────
    _divider("Overview")
    _render_hero(result)

    # ── Section 2: Enhanced fan chart ─────────────────────────────────────────
    _divider("Simulation Fan Chart")
    try:
        fig = _build_fan_chart(result)
        st.plotly_chart(fig, use_container_width=True, key="mc_fan_chart")
    except Exception as exc:
        st.warning(f"Fan chart unavailable: {exc}")

    # ── Section 3: Probability distribution histograms ────────────────────────
    _divider("Probability Distributions")
    st.caption("Distribution of simulated rates at 30, 60, and 90-day horizons. Vertical lines mark key percentiles.")
    _render_histograms(result)

    # ── Section 4: VaR & CVaR cards ──────────────────────────────────────────
    _divider("Risk Metrics")
    _render_var_cards(result)

    # ── Section 5: Scenario probability table ─────────────────────────────────
    _divider("Scenario Probabilities")
    st.caption(f"Probability that the simulated rate exceeds each threshold at day {result.forecast_days}.")
    _render_scenario_table(result)

    # ── Section 6: Per-route mini panels ─────────────────────────────────────
    _divider("Top Route Opportunities")
    st.caption("Top routes ranked by bull-case upside. Mini fan charts show the 90% confidence band and median.")
    _render_mini_panels(route_results)

    # ── Section 8: All-routes comparison table ────────────────────────────────
    _divider("All-Routes Comparison")
    try:
        section_header(
            "Route Comparison",
            "All routes sorted by probability of rate increase — includes VaR and Sharpe columns",
        )
        _render_comparison_table(route_results)
    except Exception as exc:
        st.warning(f"Comparison table section failed: {exc}")


# ── Wire-up instructions ────────────────────────────────────────────────────────
#
# To integrate this tab into app.py:
#
# 1. Import at the top of app.py:
#        from processing.monte_carlo import simulate_all_routes
#        import ui.tab_monte_carlo as tab_monte_carlo
#
# 2. After loading freight_data, compute (or cache) results once:
#        @st.cache_data(ttl=3600, show_spinner=False)
#        def _cached_mc(n_sims: int = 300):
#            return simulate_all_routes(freight_data, n_simulations=n_sims)
#        mc_results = _cached_mc()
#
# 3. Add a tab in the st.tabs(...) call:
#        with tabs[<monte_carlo_index>]:
#            tab_monte_carlo.render(freight_data, mc_results)
#
# The render() function is self-contained and tolerates an empty route_results
# dict by running simulations on-the-fly (slower; prefer pre-computing above).
