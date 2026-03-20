from __future__ import annotations

import datetime
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ports.demand_analyzer import PortDemandResult
from ports.product_mapper import get_color, ALL_CATEGORIES
from utils.helpers import format_usd


# ── Colour palette ────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#3b82f6"
C_LOW     = "#f59e0b"
C_WEAK    = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"


def _demand_color(score: float) -> str:
    if score >= 0.70:
        return C_HIGH
    if score >= 0.50:
        return C_MOD
    if score >= 0.35:
        return C_LOW
    return C_WEAK


def _demand_label(score: float) -> str:
    if score >= 0.70:
        return "HIGH DEMAND"
    if score >= 0.50:
        return "MODERATE"
    if score >= 0.35:
        return "LOW"
    return "WEAK"


def _region_flag(region: str) -> str:
    r = region.lower()
    if "asia east" in r or "south asia" in r:
        return "\U0001f30f"   # 🌏
    if "europe" in r:
        return "\U0001f30d"   # 🌍
    if "north america" in r or "south america" in r:
        return "\U0001f30e"   # 🌎
    if "middle east" in r:
        return "\U0001f54c"   # 🕌
    if "southeast asia" in r:
        return "\U0001f334"   # 🌴
    if "africa" in r:
        return "\U0001f30d"   # 🌍
    return "\U0001f310"       # 🌐


def _trend_arrow(trend: str) -> tuple[str, str]:
    """Return (arrow glyph, color)."""
    if trend == "Rising":
        return "▲", C_HIGH
    if trend == "Falling":
        return "▼", C_WEAK
    return "●", C_TEXT3


def render(port_results: list[PortDemandResult]) -> None:
    """Render the Port Demand tab."""
    st.header("Port Demand Analysis")
    st.caption(f"Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M UTC')} • Refreshes every 168 hours (trade flow data)")

    if not port_results:
        st.info("No port data available. Check API credentials in .env and click Refresh.")
        return

    with st.spinner("Loading port demand data..."):
        sorted_results = sorted(port_results, key=lambda r: r.demand_score, reverse=True)

    # ── 1. Demand tier summary badges ─────────────────────────────────────────
    high_count = sum(1 for r in port_results if r.demand_score >= 0.70)
    mod_count  = sum(1 for r in port_results if 0.50 <= r.demand_score < 0.70)
    low_count  = sum(1 for r in port_results if 0.35 <= r.demand_score < 0.50)
    weak_count = sum(1 for r in port_results if r.demand_score < 0.35)
    rising_count = sum(1 for r in port_results if r.demand_trend == "Rising")
    top = sorted_results[0]

    def _badge(label, count, color):
        return (
            f'<span style="display:inline-block; background:{color}20; border:1px solid {color}60; '
            f'color:{color}; border-radius:20px; padding:6px 18px; font-size:0.82rem; '
            f'font-weight:700; margin-right:10px; margin-bottom:6px; letter-spacing:0.04em">'
            f'{label}: &nbsp;<strong style="font-size:1.05rem">{count}</strong> ports</span>'
        )

    st.markdown(
        '<div style="margin-bottom:4px; margin-top:4px">'
        + _badge("HIGH DEMAND \u226570%", high_count, C_HIGH)
        + _badge("MODERATE 50-69%", mod_count, C_MOD)
        + _badge("LOW 35-49%", low_count, C_LOW)
        + _badge("WEAK <35%", weak_count, C_WEAK)
        + "</div>",
        unsafe_allow_html=True,
    )

    # ── 2. KPI row ────────────────────────────────────────────────────────────
    def kpi(label, value, sub="", color=C_ACCENT):
        sub_html = (
            f'<div style="font-size:0.78rem; color:{C_TEXT2}">{sub}</div>' if sub else ""
        )
        return (
            f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-top:3px solid {color}; '
            f'border-radius:10px; padding:16px 18px; text-align:center">'
            f'<div style="font-size:0.68rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.07em">{label}</div>'
            f'<div style="font-size:1.9rem; font-weight:800; color:{C_TEXT}; line-height:1.1; margin:5px 0">{value}</div>'
            f'{sub_html}</div>'
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(
        kpi("Highest Demand", top.port_name[:12], f"{top.demand_score:.0%} score", C_HIGH),
        unsafe_allow_html=True,
    )
    c2.markdown(
        kpi("High Demand", str(high_count), f"of {len(port_results)} ports", C_HIGH),
        unsafe_allow_html=True,
    )
    c3.markdown(
        kpi("Weak Demand", str(weak_count), f"of {len(port_results)} ports", C_WEAK),
        unsafe_allow_html=True,
    )
    c4.markdown(
        kpi("Rising Trend", str(rising_count), "ports trending up", C_ACCENT),
        unsafe_allow_html=True,
    )

    st.divider()

    # ── 3. Animated donut gauge grid (top 12 ports) ───────────────────────────
    st.markdown(
        f'<div style="font-size:1.15rem; font-weight:800; color:{C_TEXT}; '
        f'letter-spacing:0.02em; margin-bottom:16px">Demand Gauge Grid — Top 12 Ports</div>',
        unsafe_allow_html=True,
    )

    top12 = sorted_results[:12]
    rows = [top12[i : i + 4] for i in range(0, len(top12), 4)]

    for row_ports in rows:
        cols = st.columns(4)
        for col, r in zip(cols, row_ports):
            score = r.demand_score
            color = _demand_color(score)
            label = _demand_label(score)
            arrow, arrow_color = _trend_arrow(r.demand_trend)

            fig = go.Figure(
                go.Pie(
                    values=[score, 1 - score],
                    hole=0.72,
                    marker_colors=[color, "#1a2235"],
                    textinfo="none",
                    hoverinfo="skip",
                    direction="clockwise",
                    sort=False,
                )
            )
            fig.add_annotation(
                text=f"{score:.0%}",
                x=0.5,
                y=0.55,
                font=dict(size=22, color=color, family="Arial Black"),
                showarrow=False,
            )
            fig.add_annotation(
                text=label,
                x=0.5,
                y=0.35,
                font=dict(size=8, color=C_TEXT3, family="Arial"),
                showarrow=False,
            )
            fig.update_layout(
                height=200,
                paper_bgcolor=C_BG,
                margin=dict(l=5, r=5, t=5, b=5),
                showlegend=False,
            )
            fig.update_traces(
                rotation=90,
            )

            port_short = r.port_name if len(r.port_name) <= 14 else r.port_name[:13] + "\u2026"
            card_html = (
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
                f'border-top:3px solid {color}; border-radius:12px; '
                f'padding:12px 8px 8px 8px; margin-bottom:8px; text-align:center">'
                f'<div style="font-size:0.78rem; font-weight:700; color:{C_TEXT}; '
                f'margin-bottom:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">'
                f'{port_short}</div>'
                f'<div style="font-size:0.68rem; color:{C_TEXT3}">{r.region}</div>'
            )
            with col:
                st.markdown(card_html, unsafe_allow_html=True)
                st.plotly_chart(fig, use_container_width=True, key=f"gauge_{r.locode}")
                st.markdown(
                    f'<div style="text-align:center; margin-top:-8px; margin-bottom:8px">'
                    f'<span style="color:{arrow_color}; font-size:0.82rem; font-weight:700">'
                    f'{arrow} {r.demand_trend}</span></div>',
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── 4. Port Rankings table ────────────────────────────────────────────────
    st.markdown(
        f'<div style="font-size:1.15rem; font-weight:800; color:{C_TEXT}; '
        f'letter-spacing:0.02em; margin-bottom:16px">Port Rankings</div>',
        unsafe_allow_html=True,
    )

    rank_rows_html = []
    for i, r in enumerate(sorted_results):
        color    = _demand_color(r.demand_score)
        label    = _demand_label(r.demand_score)
        flag     = _region_flag(r.region)
        bar_w    = int(r.demand_score * 100)
        tf_pct   = int(r.trade_flow_component * 100)
        cg_pct   = int(r.congestion_component * 100)
        row_bg   = C_CARD if i % 2 == 0 else "#151e2e"
        rank_col = color if i < 3 else C_TEXT2
        rank_num = str(i + 1)

        badge_html = (
            f'<span style="background:{color}22; color:{color}; border:1px solid {color}55; '
            f'border-radius:4px; padding:2px 7px; font-size:0.65rem; font-weight:700; '
            f'letter-spacing:0.06em">{label}</span>'
        )
        progress_html = (
            f'<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:6px; '
            f'width:100%; overflow:hidden; margin-top:4px">'
            f'<div style="background:{color}; width:{bar_w}%; height:100%; border-radius:4px"></div>'
            f'</div>'
        )

        rank_rows_html.append(
            f'<tr style="background:{row_bg}">'
            f'<td style="padding:10px 14px; font-size:1.1rem; font-weight:900; color:{rank_col}; width:40px">{rank_num}</td>'
            f'<td style="padding:10px 14px">'
            f'  <div style="font-size:0.88rem; font-weight:700; color:{C_TEXT}">{flag} {r.port_name}</div>'
            f'  <div style="font-size:0.72rem; color:{C_TEXT3}">{r.region}</div>'
            f'</td>'
            f'<td style="padding:10px 14px; min-width:140px">'
            f'  <div style="font-size:0.88rem; font-weight:700; color:{color}">{r.demand_score:.0%}</div>'
            f'  {progress_html}'
            f'</td>'
            f'<td style="padding:10px 14px; font-size:0.82rem; color:{C_TEXT2}; text-align:center">{tf_pct}%</td>'
            f'<td style="padding:10px 14px; font-size:0.82rem; color:{C_TEXT2}; text-align:center">{cg_pct}%</td>'
            f'<td style="padding:10px 14px">{badge_html}</td>'
            f'</tr>'
        )

    table_html = (
        f'<div style="border:1px solid {C_BORDER}; border-radius:10px; overflow:hidden; margin-bottom:16px">'
        f'<table style="width:100%; border-collapse:collapse; font-family:sans-serif">'
        f'<thead><tr style="background:#0d1526">'
        f'<th style="padding:10px 14px; font-size:0.68rem; color:{C_TEXT3}; text-align:left; '
        f'text-transform:uppercase; letter-spacing:0.07em">#</th>'
        f'<th style="padding:10px 14px; font-size:0.68rem; color:{C_TEXT3}; text-align:left; '
        f'text-transform:uppercase; letter-spacing:0.07em">Port</th>'
        f'<th style="padding:10px 14px; font-size:0.68rem; color:{C_TEXT3}; text-align:left; '
        f'text-transform:uppercase; letter-spacing:0.07em">Demand Score</th>'
        f'<th style="padding:10px 14px; font-size:0.68rem; color:{C_TEXT3}; text-align:center; '
        f'text-transform:uppercase; letter-spacing:0.07em">Trade Flow</th>'
        f'<th style="padding:10px 14px; font-size:0.68rem; color:{C_TEXT3}; text-align:center; '
        f'text-transform:uppercase; letter-spacing:0.07em">Congestion</th>'
        f'<th style="padding:10px 14px; font-size:0.68rem; color:{C_TEXT3}; text-align:left; '
        f'text-transform:uppercase; letter-spacing:0.07em">Status</th>'
        f'</tr></thead>'
        f'<tbody>{"".join(rank_rows_html)}</tbody>'
        f'</table></div>'
    )
    st.markdown(table_html, unsafe_allow_html=True)

    st.divider()

    # ── 5. Dramatic port detail section ───────────────────────────────────────
    st.markdown(
        f'<div style="font-size:1.15rem; font-weight:800; color:{C_TEXT}; '
        f'letter-spacing:0.02em; margin-bottom:16px">Port Detail</div>',
        unsafe_allow_html=True,
    )

    col_sel, _ = st.columns([1, 3])
    with col_sel:
        selected_name = st.selectbox(
            "Select port",
            [r.port_name for r in sorted_results],
            key="port_select",
        )

    selected = next((r for r in port_results if r.port_name == selected_name), None)

    if selected:
        dem_color = _demand_color(selected.demand_score)
        dem_label = _demand_label(selected.demand_score)
        flag      = _region_flag(selected.region)
        arrow, arrow_color = _trend_arrow(selected.demand_trend)
        top_prod  = selected.top_products[0]["category"] if selected.top_products else "N/A"
        tpu_str   = (
            f"{selected.throughput_teu_m:.1f}M TEU/yr"
            if selected.throughput_teu_m > 0
            else "N/A"
        )

        # Full-width header card
        st.markdown(
            f'<div style="background:linear-gradient(135deg, {dem_color}18 0%, {C_CARD} 60%); '
            f'border:1px solid {dem_color}44; border-left:5px solid {dem_color}; '
            f'border-radius:14px; padding:22px 28px; margin-bottom:20px">'
            f'<div style="font-size:0.72rem; font-weight:700; color:{dem_color}; '
            f'text-transform:uppercase; letter-spacing:0.1em; margin-bottom:6px">'
            f'{dem_label} &nbsp;|&nbsp; {flag} {selected.region}</div>'
            f'<div style="font-size:2.2rem; font-weight:900; color:{C_TEXT}; '
            f'letter-spacing:-0.01em; line-height:1.1">{selected.port_name}</div>'
            f'<div style="font-size:0.82rem; color:{C_TEXT3}; margin-top:6px">'
            f'LOCODE: <span style="color:{C_TEXT2}; font-weight:600">{selected.locode}</span>'
            f' &nbsp;&bull;&nbsp; {selected.country_iso3}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Row 1: 3 metric cards
        def metric_card(title, value, desc, color=C_ACCENT):
            return (
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
                f'border-top:3px solid {color}; border-radius:12px; padding:18px 16px; height:100%">'
                f'<div style="font-size:0.65rem; font-weight:700; color:{C_TEXT3}; '
                f'text-transform:uppercase; letter-spacing:0.08em; margin-bottom:6px">{title}</div>'
                f'<div style="font-size:1.75rem; font-weight:900; color:{color}; line-height:1.1; '
                f'margin-bottom:6px">{value}</div>'
                f'<div style="font-size:0.73rem; color:{C_TEXT3}">{desc}</div>'
                f'</div>'
            )

        r1c1, r1c2, r1c3 = st.columns(3)
        r1c1.markdown(
            metric_card(
                "Demand Score", f"{selected.demand_score:.0%}",
                "Composite of trade, congestion & throughput", dem_color,
            ),
            unsafe_allow_html=True,
        )
        r1c2.markdown(
            metric_card(
                "Trade Flow", f"{selected.trade_flow_component:.0%}",
                "Normalized import/export value (40% weight)", C_MOD,
            ),
            unsafe_allow_html=True,
        )
        r1c3.markdown(
            metric_card(
                "Congestion", f"{selected.congestion_component:.0%}",
                f"{selected.vessel_count} cargo vessels detected (35% weight)", C_LOW,
            ),
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

        # Row 2: 3 metric cards
        r2c1, r2c2, r2c3 = st.columns(3)
        r2c1.markdown(
            metric_card(
                "Throughput", tpu_str,
                "Annual TEU capacity — World Bank data (25% weight)", C_CONV,
            ),
            unsafe_allow_html=True,
        )
        r2c2.markdown(
            metric_card(
                "Demand Trend", f"{arrow} {selected.demand_trend}",
                "Derived from import value time-series slope", arrow_color,
            ),
            unsafe_allow_html=True,
        )
        r2c3.markdown(
            metric_card(
                "Top Product", top_prod,
                "Largest import category by USD value", C_TEXT2,
            ),
            unsafe_allow_html=True,
        )

        st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

        # Score breakdown bars + products chart side by side
        col_left, col_right = st.columns([1, 2])

        with col_left:
            st.markdown(
                f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3}; '
                f'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:10px">'
                f'Score Breakdown</div>',
                unsafe_allow_html=True,
            )
            for name, val, wt in [
                ("Trade Flow", selected.trade_flow_component, 0.40),
                ("Congestion", selected.congestion_component, 0.35),
                ("Throughput", selected.throughput_component, 0.25),
            ]:
                bar_color = C_HIGH if val > 0.6 else (C_WEAK if val < 0.35 else C_MOD)
                bar_w = int(val * 100)
                st.markdown(
                    f'<div style="margin-bottom:12px">'
                    f'<div style="display:flex; justify-content:space-between; margin-bottom:4px">'
                    f'<span style="font-size:0.78rem; color:{C_TEXT2}">{name} '
                    f'<span style="color:{C_TEXT3}">({wt:.0%} wt)</span></span>'
                    f'<span style="font-size:0.78rem; font-weight:700; color:{bar_color}">{val:.0%}</span>'
                    f'</div>'
                    f'<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:8px; overflow:hidden">'
                    f'<div style="background:{bar_color}; width:{bar_w}%; height:100%; border-radius:4px; '
                    f'transition:width 0.5s ease"></div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

        with col_right:
            if selected.top_products:
                prod_names  = [p["category"] for p in selected.top_products]
                prod_vals   = [p["value_usd"] / 1e9 for p in selected.top_products]
                prod_colors = [p.get("color", "#4A90D9") for p in selected.top_products]

                prod_fig = go.Figure(
                    go.Bar(
                        x=prod_vals,
                        y=prod_names,
                        orientation="h",
                        marker_color=prod_colors,
                        text=[f"${v:.2f}B" for v in prod_vals],
                        textposition="outside",
                    )
                )
                prod_fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor=C_BG,
                    plot_bgcolor=C_SURFACE,
                    height=260,
                    title=dict(
                        text="Top Import Categories",
                        font=dict(size=12, color=C_TEXT3),
                        x=0,
                    ),
                    xaxis=dict(
                        title="Import Value ($B)",
                        gridcolor="rgba(255,255,255,0.05)",
                        zerolinecolor="rgba(255,255,255,0.1)",
                    ),
                    yaxis=dict(
                        gridcolor="rgba(255,255,255,0.05)",
                        zerolinecolor="rgba(255,255,255,0.1)",
                    ),
                    margin=dict(t=30, b=10, l=10, r=60),
                    hoverlabel=dict(
                        bgcolor=C_CARD,
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color=C_TEXT, size=12),
                    ),
                )
                st.plotly_chart(prod_fig, use_container_width=True)
            else:
                st.info("No product breakdown available — Comtrade data needed.")

    st.divider()

    # ── 6. All ports summary table ─────────────────────────────────────────────
    st.subheader("All Ports — Summary Table")
    table_data = []
    for r in sorted_results:
        table_data.append(
            {
                "Port": r.port_name,
                "LOCODE": r.locode,
                "Region": r.region,
                "Score": round(r.demand_score, 3),
                "Label": r.demand_label,
                "Trend": r.demand_trend,
                "Imports ($B)": round(r.import_value_usd / 1e9, 2) if r.import_value_usd > 0 else "—",
                "Vessels": r.vessel_count,
                "TEU (M)": round(r.throughput_teu_m, 1) if r.throughput_teu_m > 0 else "—",
            }
        )

    df = pd.DataFrame(table_data)

    def _color_score(val):
        """Color Score column without matplotlib."""
        try:
            v = float(str(val).replace("%", "")) / 100
        except (ValueError, TypeError):
            return ""
        if v >= 0.70:
            return "background-color: rgba(16,185,129,0.25); color: #10b981"
        if v >= 0.50:
            return "background-color: rgba(245,158,11,0.20); color: #f59e0b"
        return "background-color: rgba(239,68,68,0.18); color: #ef4444"

    styled = df.style.map(_color_score, subset=["Score"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    st.divider()

    with st.expander("Port Comparison Tool", expanded=False):
        _render_port_comparison(port_results)


def _render_port_comparison(port_results: list) -> None:
    """Render a side-by-side radar chart and table for up to 4 selected ports."""

    if not port_results:
        st.info("No port data available for comparison")
        return

    PORT_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444"]

    # Default selection: top 2 ports by demand score
    sorted_by_demand = sorted(port_results, key=lambda r: r.demand_score, reverse=True)
    all_names        = [r.port_name for r in sorted_by_demand]
    default_selection = all_names[:2]

    selected_names = st.multiselect(
        "Select 2–4 ports to compare",
        options=all_names,
        default=default_selection,
        max_selections=4,
        key="port_comparison_select",
    )

    if len(selected_names) < 2:
        st.info("Select at least 2 ports to enable comparison.")
        return

    selected_results = [r for r in port_results if r.port_name in selected_names]
    selected_results = sorted(
        selected_results, key=lambda r: selected_names.index(r.port_name)
    )

    trend_map = {"Rising": 1.0, "Stable": 0.5, "Falling": 0.0}

    def _momentum(r) -> float:
        return trend_map.get(r.demand_trend, 0.5)

    def _norm(values: list[float]) -> list[float]:
        mn, mx = min(values), max(values)
        if mx == mn:
            return [0.5] * len(values)
        return [(v - mn) / (mx - mn) for v in values]

    all_demand     = [r.demand_score         for r in port_results]
    all_trade      = [r.trade_flow_component for r in port_results]
    all_congestion = [r.congestion_component for r in port_results]
    all_throughput = [r.throughput_component for r in port_results]
    all_momentum   = [_momentum(r)           for r in port_results]

    port_index = {r.port_name: i for i, r in enumerate(port_results)}

    norm_demand     = _norm(all_demand)
    norm_trade      = _norm(all_trade)
    norm_congestion = _norm(all_congestion)
    norm_throughput = _norm(all_throughput)
    norm_momentum   = _norm(all_momentum)

    axes = ["Demand Score", "Trade Flow Score", "Congestion Score", "Throughput Score", "Momentum"]

    fig = go.Figure()

    for i, r in enumerate(selected_results):
        idx = port_index[r.port_name]
        values = [
            norm_demand[idx],
            norm_trade[idx],
            norm_congestion[idx],
            norm_throughput[idx],
            norm_momentum[idx],
        ]
        values_closed = values + [values[0]]
        axes_closed   = axes   + [axes[0]]

        color = PORT_COLORS[i % len(PORT_COLORS)]
        fill_rgba = "rgba({},{},{},0.15)".format(
            int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        )
        fig.add_trace(
            go.Scatterpolar(
                r=values_closed,
                theta=axes_closed,
                fill="toself",
                fillcolor=fill_rgba,
                line=dict(color=color, width=2),
                name=r.port_name,
                hovertemplate=(
                    "<b>" + r.port_name + "</b><br>"
                    "%{theta}: %{r:.2f}<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        polar=dict(
            bgcolor=C_SURFACE,
            radialaxis=dict(
                visible=True,
                range=[0, 1],
                tickfont=dict(color=C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.08)",
                linecolor="rgba(255,255,255,0.08)",
            ),
            angularaxis=dict(
                tickfont=dict(color=C_TEXT, size=11),
                gridcolor="rgba(255,255,255,0.08)",
                linecolor="rgba(255,255,255,0.08)",
            ),
        ),
        legend=dict(
            font=dict(color=C_TEXT, size=11),
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(255,255,255,0.08)",
            borderwidth=1,
        ),
        height=440,
        margin=dict(t=30, b=30, l=60, r=60),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Score Breakdown**")

    table_rows = []
    for r in selected_results:
        top_hs = r.top_products[0]["category"] if r.top_products else "—"
        table_rows.append(
            {
                "Port":            r.port_name,
                "Region":          r.region,
                "Demand Score":    f"{r.demand_score:.0%}",
                "Trade Flow":      f"{r.trade_flow_component:.0%}",
                "Congestion":      f"{r.congestion_component:.0%}",
                "Throughput":      f"{r.throughput_component:.0%}",
                "Top HS Category": top_hs,
            }
        )

    cmp_df = pd.DataFrame(table_rows)
    st.dataframe(cmp_df, use_container_width=True, hide_index=True)
