from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.insight import Insight
from engine.signals import SignalComponent


# ── Shared palette ─────────────────────────────────────────────────────────────
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_HIGH   = "#10b981"
C_MOD    = "#f59e0b"
C_LOW    = "#ef4444"
C_ACCENT = "#3b82f6"
C_CONV   = "#8b5cf6"
C_MACRO  = "#06b6d4"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"

CATEGORY_COLORS = {"CONVERGENCE": C_CONV,   "ROUTE": C_ACCENT, "PORT_DEMAND": C_HIGH, "MACRO": C_MACRO}
CATEGORY_ICONS  = {"CONVERGENCE": "🔮",     "ROUTE": "🚢",     "PORT_DEMAND": "🏗️",  "MACRO": "📊"}
ACTION_COLORS   = {"Prioritize": C_HIGH, "Monitor": C_ACCENT, "Watch": C_TEXT2, "Caution": C_MOD, "Avoid": C_LOW}


def _hex_rgba(h: str, a: float) -> str:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{a})"


# ── 1. Convergence Meter ────────────────────────────────────────────────────────

def _render_convergence_meter(insights: list[Insight]) -> None:
    conv_insights = [i for i in insights if i.category == "CONVERGENCE"]

    if conv_insights:
        convergence_pct = sum(i.score for i in conv_insights) / len(conv_insights) * 100
    else:
        port_scores  = [i.score for i in insights if i.category == "PORT_DEMAND"]
        route_scores = [i.score for i in insights if i.category == "ROUTE"]
        if port_scores and route_scores:
            avg_port  = sum(port_scores)  / len(port_scores)
            avg_route = sum(route_scores) / len(route_scores)
            convergence_pct = (avg_port + avg_route) / 2 * 100
        elif port_scores or route_scores:
            combined = port_scores + route_scores
            convergence_pct = sum(combined) / len(combined) * 100
        else:
            convergence_pct = 0.0

    if convergence_pct >= 65:
        color = C_HIGH
    elif convergence_pct >= 35:
        color = C_MOD
    else:
        color = C_LOW

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=convergence_pct,
        title={"text": "Signal Convergence", "font": {"color": "#f1f5f9", "size": 14}},
        number={"suffix": "%", "font": {"color": "#f1f5f9", "size": 26}},
        delta={"reference": 55, "increasing": {"color": C_HIGH}, "decreasing": {"color": C_LOW}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#64748b", "tickfont": {"color": "#64748b", "size": 10}},
            "bar": {"color": color},
            "bgcolor": "#111827",
            "bordercolor": "rgba(255,255,255,0.1)",
            "steps": [
                {"range": [0,  35], "color": "rgba(239,68,68,0.15)"},
                {"range": [35, 65], "color": "rgba(245,158,11,0.15)"},
                {"range": [65, 100], "color": "rgba(16,185,129,0.15)"},
            ],
            "threshold": {
                "line": {"color": "#f1f5f9", "width": 2},
                "value": convergence_pct,
            },
        },
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": C_TEXT},
        margin=dict(t=20, b=10, l=30, r=30),
        height=220,
    )

    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.plotly_chart(fig, use_container_width=True)

    # Stat pills row
    port_n  = sum(1 for i in insights if i.category == "PORT_DEMAND")
    route_n = sum(1 for i in insights if i.category == "ROUTE")
    macro_n = sum(1 for i in insights if i.category == "MACRO")

    pill_style = (
        "display:inline-block; padding:4px 14px; border-radius:999px; font-size:0.72rem; font-weight:700;"
        " letter-spacing:0.04em; margin:0 4px;"
    )
    st.markdown(
        "<div style='text-align:center; margin-top:-6px; margin-bottom:18px'>"
        + f"<span style='{pill_style} background:{_hex_rgba(C_HIGH,0.15)}; color:{C_HIGH}; border:1px solid {_hex_rgba(C_HIGH,0.3)}'>PORT signals: {port_n}</span>"
        + f"<span style='{pill_style} background:{_hex_rgba(C_ACCENT,0.15)}; color:{C_ACCENT}; border:1px solid {_hex_rgba(C_ACCENT,0.3)}'>ROUTE signals: {route_n}</span>"
        + f"<span style='{pill_style} background:{_hex_rgba(C_MACRO,0.15)}; color:{C_MACRO}; border:1px solid {_hex_rgba(C_MACRO,0.3)}'>MACRO signals: {macro_n}</span>"
        + "</div>",
        unsafe_allow_html=True,
    )


# ── 2. Dramatic hero card ───────────────────────────────────────────────────────

def _render_hero_card(hero: Insight) -> None:
    hero_color  = CATEGORY_COLORS.get(hero.category, C_ACCENT)
    hero_icon   = CATEGORY_ICONS.get(hero.category, "💡")
    action_color = ACTION_COLORS.get(hero.action, C_ACCENT)
    is_conv     = hero.category == "CONVERGENCE"

    pulsing_dot = (
        "<span style='display:inline-block; width:8px; height:8px; border-radius:50%;"
        f" background:{C_HIGH}; box-shadow:0 0 6px {C_HIGH}; margin-right:6px; vertical-align:middle'></span>"
        if is_conv else ""
    )
    conv_badge = (
        f"<span style='background:{_hex_rgba(C_CONV,0.2)}; color:{C_CONV};"
        f" border:1px solid {_hex_rgba(C_CONV,0.4)}; padding:2px 10px;"
        f" border-radius:999px; font-size:0.7rem; font-weight:700; margin-right:6px'>"
        f"{pulsing_dot}CONVERGENCE</span>"
        if is_conv else ""
    )

    tags_html = ""
    all_tags = (hero.ports_involved + hero.routes_involved + hero.stocks_potentially_affected)[:8]
    if all_tags:
        tags_html = (
            "<div style='display:flex; gap:6px; flex-wrap:wrap; margin-top:14px'>"
            + "".join(
                f"<span style='background:rgba(255,255,255,0.07); color:{C_TEXT2};"
                f" padding:2px 9px; border-radius:6px; font-size:0.72rem; font-family:monospace'>{t}</span>"
                for t in all_tags
            )
            + "</div>"
        )

    # Inline signal breakdown for hero (no expander)
    signals_html = ""
    if hero.supporting_signals:
        sig_items = ""
        for s in hero.supporting_signals[:6]:
            dir_color = C_HIGH if s.direction == "bullish" else C_LOW if s.direction == "bearish" else C_TEXT3
            sig_items += (
                f"<div style='display:flex; justify-content:space-between; align-items:center;"
                f" padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.04)'>"
                f"<span style='font-size:0.75rem; color:{C_TEXT2}'>{s.direction_emoji} {s.name}</span>"
                f"<span style='font-size:0.75rem; font-weight:700; color:{dir_color}'>{s.value:.0%}</span>"
                f"</div>"
            )
        signals_html = (
            f"<div style='margin-top:16px; padding:12px 14px; background:rgba(0,0,0,0.25);"
            f" border-radius:8px; border:1px solid rgba(255,255,255,0.06)'>"
            f"<div style='font-size:0.67rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;"
            f" letter-spacing:0.07em; margin-bottom:8px'>Supporting Signals</div>"
            + sig_items
            + "</div>"
        )

    stale_html = (
        "<div style='margin-top:10px'><span style='background:rgba(245,158,11,0.12); color:#f59e0b;"
        " padding:2px 8px; border-radius:5px; font-size:0.7rem'>⚠️ stale data</span></div>"
        if hero.data_freshness_warning else ""
    )

    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, rgba(16,185,129,0.08) 0%, rgba(59,130,246,0.05) 50%, rgba(139,92,246,0.08) 100%);
            border:1px solid {_hex_rgba(hero_color,0.35)};
            border-left:4px solid {hero_color};
            border-radius:14px;
            padding:26px 28px;
            margin-bottom:20px;
            position:relative;
        ">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:14px">
                <div>
                    <div style="font-size:0.7rem; font-weight:700; color:{hero_color}; text-transform:uppercase; letter-spacing:0.09em; margin-bottom:8px">
                        {hero_icon} &nbsp;{hero.category.replace("_"," ")}
                    </div>
                    <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center">
                        {conv_badge}
                        <span style="background:{_hex_rgba(action_color,0.15)}; color:{action_color};
                            border:1px solid {_hex_rgba(action_color,0.3)}; padding:3px 12px;
                            border-radius:999px; font-size:0.75rem; font-weight:700">{hero.action}</span>
                        <span style="background:{_hex_rgba(hero_color,0.12)}; color:{C_TEXT3};
                            border:1px solid rgba(255,255,255,0.08); padding:3px 10px;
                            border-radius:999px; font-size:0.7rem">{hero.score_label}</span>
                    </div>
                </div>
                <div style="font-size:2.5rem; font-weight:900; color:{hero_color};
                    line-height:1; text-shadow:0 0 24px {_hex_rgba(hero_color,0.5)};
                    flex-shrink:0; padding-left:16px">{hero.score:.0%}</div>
            </div>
            <div style="font-size:1.4rem; font-weight:700; color:{C_TEXT}; line-height:1.4; margin-bottom:10px;
                text-shadow:0 0 30px rgba(255,255,255,0.05)">{hero.title}</div>
            <div style="font-size:0.87rem; color:{C_TEXT2}; line-height:1.7">{hero.detail}</div>
            {tags_html}
            {signals_html}
            {stale_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 4. Stacked signal bar (replaces expander breakdown) ──────────────────────────

def _render_signal_bar(signals: list[SignalComponent], chart_key: str = "signal_bar") -> None:
    """Horizontal stacked bar showing signal contributions — one segment per signal."""
    if not signals:
        return

    direction_color_map = {"bullish": "#10b981", "bearish": "#ef4444", "neutral": "#64748b"}

    fig = go.Figure()
    for s in signals:
        seg_color = direction_color_map.get(s.direction, "#64748b")
        fig.add_trace(go.Bar(
            x=[s.contribution],
            y=["Signals"],
            orientation="h",
            marker_color=seg_color,
            text=s.name,
            textposition="inside",
            insidetextanchor="middle",
            hovertemplate=f"<b>{s.name}</b><br>{s.value:.0%} × {s.weight:.0%} = {s.contribution:.0%}<br>Direction: {s.direction}<extra></extra>",
            name=s.name,
            showlegend=False,
        ))

    fig.update_layout(
        barmode="stack",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=60,
        margin=dict(t=0, b=0, l=0, r=0),
        xaxis=dict(visible=False, range=[0, max(sum(s.contribution for s in signals) * 1.05, 0.1)]),
        yaxis=dict(visible=False),
    )
    st.plotly_chart(fig, use_container_width=True, key=chart_key)


# ── 5. Insight timeline / landscape ────────────────────────────────────────────

def _render_insight_timeline(insights: list[Insight], chart_key: str = "insight_timeline") -> None:
    if not insights:
        return

    cat_y = {"PORT_DEMAND": 0, "ROUTE": 1, "MACRO": 2, "CONVERGENCE": 3}
    cat_labels = {0: "Port Demand", 1: "Routes", 2: "Macro", 3: "Convergence"}

    xs = [i.score * 100 for i in insights]
    ys = [cat_y.get(i.category, 1) for i in insights]
    sizes = [i.score * 30 for i in insights]
    hover = [i.title + "<br>" + i.action + " · " + str(round(i.score * 100)) + "%" for i in insights]
    colors = [i.score * 100 for i in insights]

    fig = go.Figure()

    # Threshold lines
    for x_val, label, dash in [(55, "Signal threshold", "dot"), (70, "High conviction", "dash")]:
        fig.add_vline(
            x=x_val,
            line_color="rgba(255,255,255,0.2)",
            line_dash=dash,
            annotation_text=label,
            annotation_position="top",
            annotation_font_color=C_TEXT3,
            annotation_font_size=10,
        )

    fig.add_trace(go.Scatter(
        x=xs,
        y=ys,
        mode="markers",
        marker=dict(
            size=sizes,
            color=colors,
            colorscale=[[0, "#ef4444"], [0.35, "#f59e0b"], [0.65, "#3b82f6"], [1.0, "#10b981"]],
            cmin=0, cmax=100,
            showscale=True,
            colorbar=dict(
                title=dict(text="Score", font=dict(color=C_TEXT3, size=10)),
                tickfont=dict(color=C_TEXT3, size=9),
                thickness=10,
                len=0.8,
            ),
            line=dict(color="rgba(255,255,255,0.2)", width=1),
            opacity=0.85,
        ),
        text=hover,
        hovertemplate="%{text}<extra></extra>",
    ))

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(26,34,53,0.6)",
        height=280,
        margin=dict(t=10, b=40, l=90, r=60),
        font=dict(color=C_TEXT2),
        xaxis=dict(
            title=dict(text="Score (%)", font=dict(color=C_TEXT3, size=11)),
            range=[0, 105],
            tickcolor=C_TEXT3,
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        yaxis=dict(
            tickvals=list(cat_labels.keys()),
            ticktext=list(cat_labels.values()),
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(255,255,255,0.05)",
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key=chart_key)


# ── Insight card (categories other than hero) ──────────────────────────────────

def _render_insight_card(insight: Insight, cat_colors: dict, cat_icons: dict, action_colors: dict, card_key: str = "card") -> None:
    color   = cat_colors.get(insight.category, C_ACCENT)
    icon    = cat_icons.get(insight.category, "💡")
    a_color = action_colors.get(insight.action, C_ACCENT)

    tags_html = ""
    all_tags = (insight.ports_involved + insight.routes_involved + insight.stocks_potentially_affected)[:5]
    if all_tags:
        tags_html = (
            "<div style='display:flex; gap:5px; flex-wrap:wrap; margin-top:10px'>"
            + "".join(
                f"<span style='background:rgba(255,255,255,0.05); color:{C_TEXT2}; padding:1px 7px;"
                f" border-radius:5px; font-size:0.7rem; font-family:monospace'>{t}</span>"
                for t in all_tags
            )
            + "</div>"
        )

    detail_text = insight.detail[:200] + ("..." if len(insight.detail) > 200 else "")
    stale_html = (
        "<div style='margin-top:8px'><span style='background:rgba(245,158,11,0.12); color:#f59e0b;"
        " padding:2px 8px; border-radius:5px; font-size:0.7rem'>⚠️ stale data</span></div>"
        if insight.data_freshness_warning else ""
    )

    st.markdown(
        f"""
        <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {color};
                    border-radius:10px; padding:15px 18px; margin-bottom:4px">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px">
                <div style="font-size:0.88rem; font-weight:600; color:{C_TEXT}; line-height:1.3; padding-right:12px">
                    {icon} &nbsp;{insight.title}</div>
                <div style="display:flex; gap:5px; flex-shrink:0">
                    <span style="background:{_hex_rgba(a_color,0.12)}; color:{a_color};
                        padding:2px 9px; border-radius:999px; font-size:0.7rem; font-weight:700">{insight.action}</span>
                    <span style="background:{_hex_rgba(color,0.12)}; color:{color};
                        padding:2px 9px; border-radius:999px; font-size:0.7rem; font-weight:700">{insight.score:.0%}</span>
                </div>
            </div>
            <div style="font-size:0.81rem; color:{C_TEXT2}; line-height:1.5">{detail_text}</div>
            {tags_html}
            {stale_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Signal stacked bar — always visible, no expander
    if insight.supporting_signals:
        _render_signal_bar(insight.supporting_signals, chart_key=f"signal_bar_{card_key}")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


# ── Main render ────────────────────────────────────────────────────────────────

def render(insights: list[Insight]) -> None:

    st.markdown(
        '<div style="font-size:0.72rem; font-weight:700; color:#64748b; text-transform:uppercase;'
        ' letter-spacing:0.08em; margin-bottom:16px">Decision Engine Output</div>',
        unsafe_allow_html=True,
    )

    if not insights:
        st.markdown(
            f"""
            <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:12px; padding:32px; text-align:center">
                <div style="font-size:2rem; margin-bottom:12px">🔍</div>
                <div style="font-size:1rem; font-weight:600; color:{C_TEXT}; margin-bottom:8px">No insights generated yet</div>
                <div style="font-size:0.85rem; color:{C_TEXT2}">Add API credentials in .env and click Refresh All Data in the sidebar.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    # ── KPI row ────────────────────────────────────────────────────────────────
    convergence_count = sum(1 for i in insights if i.category == "CONVERGENCE")
    high_count        = sum(1 for i in insights if i.score >= 0.70)
    stale_count       = sum(1 for i in insights if i.data_freshness_warning)
    top               = insights[0]

    def kpi(label, value, sub="", color=C_ACCENT):
        return (
            f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-top:3px solid {color};"
            f" border-radius:10px; padding:14px 16px; text-align:center'>"
            f"<div style='font-size:0.67rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em'>{label}</div>"
            f"<div style='font-size:1.7rem; font-weight:800; color:{C_TEXT}; line-height:1.1; margin:4px 0'>{value}</div>"
            + (f"<div style='font-size:0.75rem; color:{C_TEXT2}'>{sub}</div>" if sub else "")
            + "</div>"
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(kpi("Top Signal",   top.action,           f"{top.score:.0%} confidence",  ACTION_COLORS.get(top.action, C_ACCENT)), unsafe_allow_html=True)
    c2.markdown(kpi("Total Insights", str(len(insights)), "active signals"),               unsafe_allow_html=True)
    c3.markdown(kpi("Convergence",  str(convergence_count), "multi-signal aligned", C_CONV), unsafe_allow_html=True)
    if stale_count:
        c4.markdown(kpi("Stale Data",   str(stale_count),  "sources need refresh",  C_MOD),  unsafe_allow_html=True)
    else:
        c4.markdown(kpi("Data Quality", "OK",              "all sources fresh",     C_HIGH), unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Convergence meter ──────────────────────────────────────────────────────
    _render_convergence_meter(insights)

    # ── Category tabs ──────────────────────────────────────────────────────────
    cats = ["CONVERGENCE", "PORT_DEMAND", "ROUTE", "MACRO"]
    tab_labels = ["All (" + str(len(insights)) + ")"] + [
        CATEGORY_ICONS.get(c, "📌") + " "
        + c.replace("_", " ").title()
        + " (" + str(sum(1 for i in insights if i.category == c)) + ")"
        for c in cats
    ]

    all_tab, conv_tab, port_tab, route_tab, macro_tab = st.tabs(tab_labels)

    tab_map = {
        all_tab:   insights,
        conv_tab:  [i for i in insights if i.category == "CONVERGENCE"],
        port_tab:  [i for i in insights if i.category == "PORT_DEMAND"],
        route_tab: [i for i in insights if i.category == "ROUTE"],
        macro_tab: [i for i in insights if i.category == "MACRO"],
    }

    for i, (tab_obj, tab_insights) in enumerate(tab_map.items()):
        with tab_obj:
            if not tab_insights:
                st.markdown(
                    f"<div style='color:{C_TEXT3}; font-size:0.85rem; padding:16px 0'>No insights in this category.</div>",
                    unsafe_allow_html=True,
                )
                continue

            # Hero card for the top insight in this tab
            _render_hero_card(tab_insights[0])

            # Remaining cards
            for j, insight in enumerate(tab_insights[1:]):
                _render_insight_card(insight, CATEGORY_COLORS, CATEGORY_ICONS, ACTION_COLORS, card_key=f"{i}_{j}")

            # Insight landscape at the bottom of each tab
            with st.expander("Insight Landscape", expanded=True, key=f"results_landscape_{i}"):
                _render_insight_timeline(tab_insights, chart_key=f"insight_timeline_{i}")

    # ── Seasonal patterns ──────────────────────────────────────────────────────
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;'
        f' letter-spacing:0.08em; margin-bottom:12px">Seasonal Patterns</div>',
        unsafe_allow_html=True,
    )
    try:
        from processing.seasonal import get_active_seasonal_signals
        seasonal = get_active_seasonal_signals()
        active   = [s for s in seasonal if s.active_now]
        upcoming = [s for s in seasonal if not s.active_now and s.days_until <= 60]

        if active:
            for sig in active:
                s_color = C_HIGH if sig.direction == "bullish" else C_LOW if sig.direction == "bearish" else C_TEXT2
                st.markdown(
                    f"""
                    <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {s_color};
                                border-radius:10px; padding:14px 18px; margin-bottom:8px">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px">
                            <div style="font-size:0.9rem; font-weight:700; color:{C_TEXT}">🔄 &nbsp;{sig.name}</div>
                            <span style="background:{_hex_rgba(s_color,0.15)}; color:{s_color};
                                padding:2px 10px; border-radius:999px; font-size:0.7rem; font-weight:700">
                                ACTIVE · {sig.strength:.0%} strength</span>
                        </div>
                        <div style="font-size:0.82rem; color:{C_TEXT2}; line-height:1.5">{sig.description}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f'<div style="color:{C_TEXT3}; font-size:0.85rem">No active seasonal patterns. Next: see upcoming events below.</div>',
                unsafe_allow_html=True,
            )

        if upcoming:
            with st.expander(f"Upcoming within 60 days ({len(upcoming)} events)", key="results_upcoming_seasonal"):
                for sig in upcoming:
                    u_color = C_ACCENT if sig.direction == "bullish" else C_MOD if sig.direction == "bearish" else C_TEXT2
                    desc_preview = sig.description[:110] + ("..." if len(sig.description) > 110 else "")
                    st.markdown(
                        f"""
                        <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {u_color};
                                    border-radius:8px; padding:10px 14px; margin-bottom:6px">
                            <div style="display:flex; justify-content:space-between">
                                <span style="font-size:0.85rem; font-weight:600; color:{C_TEXT}">{sig.name}</span>
                                <span style="font-size:0.75rem; color:{C_TEXT2}">in {sig.days_until}d</span>
                            </div>
                            <div style="font-size:0.8rem; color:{C_TEXT2}; margin-top:4px; line-height:1.4">{desc_preview}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
    except Exception:
        pass

    # ── Data Sources & Health ──────────────────────────────────────────────────
    with st.expander("Data Sources & Health", expanded=False):
        _render_data_health()

    # ── Export ─────────────────────────────────────────────────────────────────
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase;'
        f' letter-spacing:0.08em; margin-bottom:12px">Export</div>',
        unsafe_allow_html=True,
    )

    filtered = insights  # export all insights

    export_rows = [
        {
            "ID": i.insight_id, "Category": i.category, "Title": i.title,
            "Action": i.action, "Score": round(i.score, 4), "Detail": i.detail,
            "Ports": ", ".join(i.ports_involved), "Routes": ", ".join(i.routes_involved),
            "Stocks": ", ".join(i.stocks_potentially_affected), "Generated": i.generated_at,
        }
        for i in filtered
    ]

    col_csv, col_json, col_txt = st.columns(3)

    with col_csv:
        buf = io.StringIO()
        pd.DataFrame(export_rows).to_csv(buf, index=False)
        st.download_button("Export as CSV", buf.getvalue(), file_name="ship_insights.csv",
                           mime="text/csv", use_container_width=True)

    with col_json:
        json_records = []
        for i in filtered:
            signals_list = [
                {"name": s.name, "score": round(s.value, 4), "weight": round(s.weight, 4), "raw": round(s.contribution, 4)}
                for s in i.supporting_signals
            ]
            json_records.append({
                "insight_id": i.insight_id, "title": i.title, "category": i.category,
                "score": round(i.score, 4), "score_label": i.score_label, "action": i.action,
                "detail": i.detail, "signals": signals_list,
                "ports_involved": i.ports_involved, "routes_involved": i.routes_involved,
                "stocks_potentially_affected": i.stocks_potentially_affected,
                "generated_at": i.generated_at, "data_freshness_warning": i.data_freshness_warning,
            })
        json_str = json.dumps(json_records, indent=2)
        st.download_button("Export as JSON", json_str, file_name="ship_insights.json",
                           mime="application/json", use_container_width=True)

    with col_txt:
        report_str = _build_text_report(filtered, high_count)
        st.download_button("Export Text Report", report_str, file_name="ship_intelligence_report.md",
                           mime="text/markdown", use_container_width=True)

    # ── Daily Digest ───────────────────────────────────────────────────────────
    st.markdown(
        "<hr style='border-color:rgba(255,255,255,0.07); margin:24px 0'>",
        unsafe_allow_html=True,
    )
    st.markdown("## 📧 Daily Digest")

    try:
        from utils.digest_builder import build_digest, render_as_html, render_as_json, render_as_markdown

        _digest = build_digest(
            port_results=[],
            route_results=[],
            insights=insights,
            freight_data={},
            macro_data={},
            stock_data=[],
        )

        _html_str  = render_as_html(_digest)
        _md_str    = render_as_markdown(_digest)
        _json_str  = render_as_json(_digest)

        _col_dl_html, _col_md, _col_dl_json = st.columns(3)

        with _col_dl_html:
            st.download_button(
                "Download HTML Report",
                data=_html_str.encode("utf-8"),
                file_name="shipping_digest_" + _digest.date + ".html",
                mime="text/html",
                use_container_width=True,
            )

        with _col_md:
            st.text_area(
                "Copy Markdown",
                value=_md_str,
                height=200,
            )

        with _col_dl_json:
            st.download_button(
                "Download JSON",
                data=_json_str.encode("utf-8"),
                file_name="shipping_digest_" + _digest.date + ".json",
                mime="application/json",
                use_container_width=True,
            )

        st.info(_digest.executive_summary.split("\n\n")[0])

        _sent_color = {
            "BULLISH":  "#10b981",
            "BEARISH":  "#ef4444",
            "NEUTRAL":  "#94a3b8",
            "MIXED":    "#f59e0b",
        }.get(_digest.market_sentiment, "#94a3b8")

        _sent_bg = {
            "BULLISH":  "rgba(16,185,129,0.12)",
            "BEARISH":  "rgba(239,68,68,0.12)",
            "NEUTRAL":  "rgba(100,116,139,0.12)",
            "MIXED":    "rgba(245,158,11,0.12)",
        }.get(_digest.market_sentiment, "rgba(100,116,139,0.12)")

        st.markdown(
            "<div style='"
            "display:inline-flex; align-items:center; gap:12px;"
            "background:" + _sent_bg + ";"
            "border:2px solid " + _sent_color + ";"
            "border-radius:14px; padding:16px 28px; margin-top:10px"
            "'>"
            "<span style='font-size:32px; font-weight:900; color:" + _sent_color + ";"
            " letter-spacing:0.04em'>" + _digest.market_sentiment + "</span>"
            "<span style='font-size:14px; color:" + _sent_color + "; opacity:0.75'>"
            "Market Sentiment &nbsp;|&nbsp; score: " + str(_digest.sentiment_score) + "</span>"
            "</div>",
            unsafe_allow_html=True,
        )

    except Exception as _digest_err:
        st.warning("Daily digest unavailable: " + str(_digest_err))


# ── Supporting functions ────────────────────────────────────────────────────────

def _build_text_report(insights: list[Insight], n_high: int) -> str:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    n = len(insights)

    lines: list[str] = []
    lines.append("# Global Shipping Intelligence Report")
    lines.append(f"Generated: {timestamp}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(f"{n} active signals | {n_high} high-conviction insights")
    lines.append("")
    lines.append("## Top Insights")

    for idx, ins in enumerate(insights, start=1):
        lines.append(f"### {idx}. {ins.title} [{ins.score:.0%}]")
        lines.append(f"Action: {ins.action}")
        lines.append(ins.detail)
        if ins.supporting_signals:
            sig_parts = [f"{s.name} ({s.value:.0%} x {s.weight:.0%})" for s in ins.supporting_signals]
            lines.append("Signals: " + ", ".join(sig_parts))
        tags = ins.ports_involved + ins.routes_involved + ins.stocks_potentially_affected
        if tags:
            lines.append("Tags: " + ", ".join(tags))
        lines.append("")

    lines.append("## Seasonal Context")
    try:
        from processing.seasonal import get_active_seasonal_signals
        seasonal = get_active_seasonal_signals()
        active = [s for s in seasonal if s.active_now]
        if active:
            for sig in active:
                lines.append(f"- **{sig.name}** (ACTIVE, {sig.strength:.0%} strength): {sig.description}")
        else:
            lines.append("- No active seasonal patterns at time of export.")
    except Exception:
        lines.append("- Seasonal data unavailable.")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by Ship Tracker Decision Engine*")

    return "\n".join(lines)


def _render_data_health() -> None:
    SOURCE_DEFS = [
        ("stocks",    "Stocks",            2.0),
        ("freight",   "Freight (FBX)",    26.0),
        ("fred",      "Macro (FRED)",     26.0),
        ("worldbank", "World Bank",       168.0),
        ("comtrade",  "Trade (Comtrade)", 168.0),
        ("ais",       "AIS Vessel",         8.0),
    ]

    cache_dir = Path("cache")
    now = time.time()

    rows: list[dict] = []
    for subdir, label, stale_hours in SOURCE_DEFS:
        source_path = cache_dir / subdir
        if not source_path.exists():
            rows.append({"source": label, "last_updated": "—", "age": "—", "status": "Missing", "status_color": C_LOW})
            continue

        parquet_files = list(source_path.glob("*.parquet"))
        if not parquet_files:
            rows.append({"source": label, "last_updated": "—", "age": "—", "status": "Missing", "status_color": C_LOW})
            continue

        newest    = max(parquet_files, key=lambda f: os.path.getmtime(f))
        mtime     = os.path.getmtime(newest)
        age_secs  = now - mtime
        age_hours = age_secs / 3600.0

        last_updated_str = datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%d %H:%M UTC")

        if age_hours < 1.0:
            age_str = f"{int(age_secs / 60)}m ago"
        elif age_hours < 48.0:
            age_str = f"{age_hours:.1f}h ago"
        else:
            age_str = f"{age_hours / 24:.1f}d ago"

        status       = "Fresh" if age_hours <= stale_hours else "Stale"
        status_color = C_HIGH  if age_hours <= stale_hours else C_MOD

        rows.append({"source": label, "last_updated": last_updated_str, "age": age_str,
                     "status": status, "status_color": status_color})

    header_style = (
        "font-size:0.7rem; font-weight:700; color:" + C_TEXT3
        + "; text-transform:uppercase; letter-spacing:0.07em; padding:8px 12px; text-align:left;"
    )
    cell_style = (
        "font-size:0.82rem; color:" + C_TEXT2
        + "; padding:8px 12px; border-top:1px solid " + C_BORDER + ";"
    )
    source_cell_style = (
        "font-size:0.82rem; font-weight:600; color:" + C_TEXT
        + "; padding:8px 12px; border-top:1px solid " + C_BORDER + ";"
    )

    table_rows_html = ""
    for row in rows:
        h = row["status_color"].lstrip("#")
        rv, gv, bv = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        badge_style = (
            "display:inline-block; padding:2px 10px; border-radius:999px;"
            " font-size:0.7rem; font-weight:700;"
            " background:rgba(" + str(rv) + "," + str(gv) + "," + str(bv) + ",0.15);"
            " color:" + row["status_color"] + ";"
            " border:1px solid rgba(" + str(rv) + "," + str(gv) + "," + str(bv) + ",0.3);"
        )
        table_rows_html += (
            "<tr>"
            "<td style='" + source_cell_style + "'>" + row["source"] + "</td>"
            "<td style='" + cell_style + "'>" + row["last_updated"] + "</td>"
            "<td style='" + cell_style + "'>" + row["age"] + "</td>"
            "<td style='" + cell_style + "'><span style='" + badge_style + "'>" + row["status"] + "</span></td>"
            "</tr>"
        )

    table_html = (
        "<table style='width:100%; border-collapse:collapse;"
        " background:" + C_CARD + "; border:1px solid " + C_BORDER + "; border-radius:10px; overflow:hidden'>"
        "<thead><tr>"
        "<th style='" + header_style + "'>Source</th>"
        "<th style='" + header_style + "'>Last Updated</th>"
        "<th style='" + header_style + "'>Age</th>"
        "<th style='" + header_style + "'>Status</th>"
        "</tr></thead>"
        "<tbody>" + table_rows_html + "</tbody>"
        "</table>"
    )

    st.markdown(table_html, unsafe_allow_html=True)
