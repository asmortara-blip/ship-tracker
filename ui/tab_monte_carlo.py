"""Monte Carlo freight rate forecasting tab.

Visualises GBM simulation results: fan charts, probability stats, and an
all-routes comparison table.
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


# ── Internal helpers ───────────────────────────────────────────────────────────

_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"


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
        f'<div style="display:flex; align-items:center; gap:12px; margin:24px 0">'
        f'<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        f'<span style="font-size:0.65rem; color:#475569; text-transform:uppercase;'
        f' letter-spacing:0.12em">{label}</span>'
        f'<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Section 2: Fan chart ───────────────────────────────────────────────────────

def _build_fan_chart(result: MonteCarloResult) -> go.Figure:
    """Build the Monte Carlo fan chart figure for one route."""
    days = list(range(1, result.forecast_days + 1))
    paths = result.simulated_paths
    current = result.current_rate

    fig = go.Figure()

    # ── Individual simulation paths ───────────────────────────────────────────
    # Colour each path based on whether it finishes above or below current_rate
    for path in paths:
        final = path[-1]
        line_color = "rgba(16,185,129,0.03)" if final >= current else "rgba(239,68,68,0.03)"
        fig.add_trace(go.Scatter(
            x=days,
            y=path,
            mode="lines",
            line={"width": 0.5, "color": line_color},
            hoverinfo="skip",
            showlegend=False,
        ))

    # ── Percentile fan fills ──────────────────────────────────────────────────
    p5  = result.percentiles["p5"]
    p25 = result.percentiles["p25"]
    p50 = result.percentiles["p50"]
    p75 = result.percentiles["p75"]
    p95 = result.percentiles["p95"]

    # p5-p95 fill (very light blue)
    fig.add_trace(go.Scatter(
        x=days + days[::-1],
        y=p95 + p5[::-1],
        fill="toself",
        fillcolor="rgba(59,130,246,0.06)",
        line={"width": 0},
        name="90% range (p5–p95)",
        hoverinfo="skip",
    ))

    # p25-p75 fill (medium blue)
    fig.add_trace(go.Scatter(
        x=days + days[::-1],
        y=p75 + p25[::-1],
        fill="toself",
        fillcolor="rgba(59,130,246,0.18)",
        line={"width": 0},
        name="50% range (p25–p75)",
        hoverinfo="skip",
    ))

    # p50 median line (bright white)
    fig.add_trace(go.Scatter(
        x=days,
        y=p50,
        mode="lines",
        line={"width": 2, "color": "#ffffff"},
        name="Median (p50)",
    ))

    # ── Reference lines ───────────────────────────────────────────────────────
    # Horizontal dashed line at current_rate
    fig.add_hline(
        y=current,
        line_dash="dash",
        line_color="rgba(245,158,11,0.7)",
        line_width=1.5,
        annotation_text=f"Current {_fmt_rate(current)}",
        annotation_font_color=C_MOD,
        annotation_font_size=11,
    )

    # Vertical dotted lines at day 30, 60, 90
    for marker_day in (30, 60, 90):
        if marker_day <= result.forecast_days:
            fig.add_vline(
                x=marker_day,
                line_dash="dot",
                line_color="rgba(255,255,255,0.2)",
                line_width=1,
                annotation_text=f"D{marker_day}",
                annotation_font_color=C_TEXT3,
                annotation_font_size=10,
                annotation_position="top",
            )

    # ── Layout ────────────────────────────────────────────────────────────────
    layout = dark_layout(
        title=f"Monte Carlo Fan Chart — {result.route_id}  ({result.n_simulations:,} paths)",
        height=450,
        showlegend=True,
    )
    layout["xaxis"]["title"] = {"text": "Days from today", "font": {"color": C_TEXT2, "size": 12}}
    layout["yaxis"]["title"] = {"text": "Rate USD/FEU",    "font": {"color": C_TEXT2, "size": 12}}
    layout["xaxis"]["range"] = [1, result.forecast_days]
    layout["template"] = "plotly_dark"
    fig.update_layout(**layout)

    return fig


# ── Section 3: Key stats ───────────────────────────────────────────────────────

def _render_key_stats(result: MonteCarloResult) -> None:
    """Render key stats in two rows of 3 columns."""
    current = result.current_rate
    sharpe  = get_risk_adjusted_opportunity(result)

    col1, col2, col3 = st.columns(3)

    with col1:
        delta_str = _pct_delta(result.expected_rate_90d, current)
        delta_color = C_HIGH if result.expected_rate_90d >= current else C_LOW
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-top:3px solid {C_ACCENT}; border-radius:10px; '
            f'padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.06em; font-weight:600">Expected Rate (p50 @90d)</div>'
            f'<div style="font-size:1.7rem; font-weight:700; color:{C_TEXT}; margin:6px 0">'
            f'{_fmt_rate(result.expected_rate_90d)}</div>'
            f'<div style="font-size:0.82rem; color:{delta_color}">{delta_str} vs current</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col2:
        bull_delta = _pct_delta(result.bull_case_90d, current)
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-top:3px solid {C_HIGH}; border-radius:10px; '
            f'padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.06em; font-weight:600">Bull Case (p90 @90d)</div>'
            f'<div style="font-size:1.7rem; font-weight:700; color:{C_HIGH}; margin:6px 0">'
            f'{_fmt_rate(result.bull_case_90d)}</div>'
            f'<div style="font-size:0.82rem; color:{C_HIGH}">{bull_delta} upside</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col3:
        bear_delta = _pct_delta(result.bear_case_90d, current)
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-top:3px solid {C_LOW}; border-radius:10px; '
            f'padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.06em; font-weight:600">Bear Case (p10 @90d)</div>'
            f'<div style="font-size:1.7rem; font-weight:700; color:{C_LOW}; margin:6px 0">'
            f'{_fmt_rate(result.bear_case_90d)}</div>'
            f'<div style="font-size:0.82rem; color:{C_LOW}">{bear_delta} downside</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    col4, col5, col6 = st.columns(3)

    with col4:
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-top:3px solid {C_LOW}; border-radius:10px; '
            f'padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.06em; font-weight:600">VaR 95% (worst expected loss)</div>'
            f'<div style="font-size:1.7rem; font-weight:700; color:{C_LOW}; margin:6px 0">'
            f'-{_fmt_rate(result.var_95)}</div>'
            f'<div style="font-size:0.82rem; color:{C_TEXT3}">95th-percentile loss scenario</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col5:
        prob_pct = result.prob_rate_increase * 100.0
        prob_color = C_HIGH if prob_pct >= 50 else C_LOW
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-top:3px solid {prob_color}; border-radius:10px; '
            f'padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.06em; font-weight:600">Probability of Rate Increase</div>'
            f'<div style="font-size:1.7rem; font-weight:700; color:{prob_color}; margin:6px 0">'
            f'{prob_pct:.1f}%</div>'
            f'<div style="font-size:0.82rem; color:{C_TEXT3}">at end of 90-day window</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col6:
        sharpe_color = C_HIGH if sharpe >= 0 else C_LOW
        st.markdown(
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
            f'border-top:3px solid {sharpe_color}; border-radius:10px; '
            f'padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.72rem; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.06em; font-weight:600">Risk-Adjusted Opportunity</div>'
            f'<div style="font-size:1.7rem; font-weight:700; color:{sharpe_color}; margin:6px 0">'
            f'{sharpe:.2f}</div>'
            f'<div style="font-size:0.82rem; color:{C_TEXT3}">Sharpe-like ratio</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Section 4: All-routes comparison table ─────────────────────────────────────

def _render_comparison_table(route_results: dict[str, MonteCarloResult]) -> None:
    """Render all-routes comparison table sorted by prob_rate_increase desc."""
    import pandas as pd

    rows = []
    for rid, r in route_results.items():
        upside_pct = (r.bull_case_90d - r.current_rate) / r.current_rate * 100.0 if r.current_rate else 0.0
        downside_pct = (r.bear_case_90d - r.current_rate) / r.current_rate * 100.0 if r.current_rate else 0.0
        rows.append({
            "Route": rid,
            "Current Rate": r.current_rate,
            "Expected 90d": r.expected_rate_90d,
            "Bull Case": r.bull_case_90d,
            "Bear Case": r.bear_case_90d,
            "Prob Up (%)": round(r.prob_rate_increase * 100.0, 1),
            "Upside (%)": round(upside_pct, 1),
            "Downside (%)": round(downside_pct, 1),
        })

    if not rows:
        st.warning("Simulation returned no results — try adjusting parameters")
        return

    df = (
        pd.DataFrame(rows)
        .sort_values("Prob Up (%)", ascending=False)
        .reset_index(drop=True)
    )

    # Format currency columns for display
    display_df = df.copy()
    for col in ("Current Rate", "Expected 90d", "Bull Case", "Bear Case"):
        display_df[col] = display_df[col].apply(lambda v: f"${v:,.0f}")

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )

    # ── Download button ───────────────────────────────────────────────────────
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download results as CSV",
        data=csv_bytes,
        file_name="monte_carlo_results.csv",
        mime="text/csv",
        key="mc_download_csv",
    )


# ── Main render ────────────────────────────────────────────────────────────────

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
    # Run simulations if caller has not pre-computed them
    if not route_results:
        with st.spinner("Running Monte Carlo simulations…"):
            route_results = simulate_all_routes(freight_data, n_simulations=300)

    if not route_results:
        st.warning("No Monte Carlo results available — check that freight data is loaded.")
        return

    # ── Section 1: Route selector ─────────────────────────────────────────────
    _divider("Route Selection")
    section_header(
        "Monte Carlo Rate Forecasting",
        "GBM simulation across 90-day horizon — select a route to inspect",
    )

    # Default to highest-opportunity route (most upside)
    top_routes = get_highest_upside_routes(route_results, top_n=len(route_results))
    default_route = top_routes[0].route_id if top_routes else next(iter(route_results))

    all_route_ids = sorted(route_results.keys())
    default_idx = all_route_ids.index(default_route) if default_route in all_route_ids else 0

    selected_route = st.selectbox(
        "Select route",
        options=all_route_ids,
        index=default_idx,
        key="mc_route_selector",
    )

    result = route_results.get(selected_route)
    if result is None:
        st.error(f"No simulation result for route: {selected_route}")
        return

    # ── Section 2: Fan chart ──────────────────────────────────────────────────
    _divider("Simulation Fan Chart")

    ci_lo, ci_hi = result.confidence_interval_90d
    st.caption(
        f"90-day confidence interval (p5–p95): "
        f"**{_fmt_rate(ci_lo)}** — **{_fmt_rate(ci_hi)}**"
        f"  |  current: **{_fmt_rate(result.current_rate)}**"
    )

    with st.spinner("Running Monte Carlo simulation..."):
        fig = _build_fan_chart(result)
    st.plotly_chart(fig, use_container_width=True, key="mc_fan_chart")

    # ── Section 3: Key stats ──────────────────────────────────────────────────
    _divider("Key Statistics")
    _render_key_stats(result)

    # ── Section 4: All-routes comparison ─────────────────────────────────────
    _divider("All-Routes Comparison")
    section_header(
        "Route Comparison",
        "All routes sorted by probability of rate increase at 90 days",
    )
    _render_comparison_table(route_results)


# ── Wire-up instructions ───────────────────────────────────────────────────────
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
# 3. Add a tab in the st.tabs(...) call, e.g.:
#        tab_labels = [..., "Monte Carlo"]
#        tabs = st.tabs(tab_labels)
#        ...
#        with tabs[<monte_carlo_index>]:
#            tab_monte_carlo.render(freight_data, mc_results)
#
# The render() function is self-contained and tolerates an empty route_results
# dict by running simulations on-the-fly (slower; prefer pre-computing above).
