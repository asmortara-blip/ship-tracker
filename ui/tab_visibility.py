"""tab_visibility.py — Supply Chain Visibility tab.

Renders the "Supply Chain Visibility" module: visibility score dashboard,
data source coverage map, shipment tracking simulation, exception management,
predictive ETAs, supplier origin map, data quality scorecard, and coverage
gaps analysis — alongside the existing globe path map, journey timeline,
bottleneck analyser, disruption simulator, and resilience score cards.
"""
from __future__ import annotations

import math
import random
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.supply_chain_visibility import (
    EXAMPLE_PATHS,
    SupplyChainPath,
    get_bottleneck_details,
    recommended_buffer_days,
    simulate_disruption,
)

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_CARD2  = "#141d2e"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"
C_ORANGE = "#f97316"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"
C_TEAL   = "#14b8a6"
C_ROSE   = "#f43f5e"

# Transport mode colours
_MODE_COLOR: dict[str, str] = {
    "OCEAN":    C_ACCENT,
    "RAIL":     C_WARN,
    "TRUCK":    C_ORANGE,
    "PIPELINE": C_PURPLE,
    "INLAND":   C_CYAN,
}

# Node type colours
_TYPE_COLOR: dict[str, str] = {
    "FACTORY":      "#ec4899",
    "PORT":         C_ACCENT,
    "RAIL":         C_WARN,
    "WAREHOUSE":    C_CYAN,
    "DISTRIBUTION": C_HIGH,
}

# Node type → Plotly marker symbol
_TYPE_SYMBOL: dict[str, str] = {
    "FACTORY":      "square",
    "PORT":         "circle",
    "RAIL":         "diamond",
    "WAREHOUSE":    "diamond",
    "DISTRIBUTION": "triangle-up",
}

# Status colours
_STATUS_COLOR: dict[str, str] = {
    "OPERATIONAL": C_HIGH,
    "DELAYED":     C_WARN,
    "DISRUPTED":   C_DANGER,
    "CLOSED":      "#64748b",
}

# Risk band colours
_RISK_COLOR_BAND: list[tuple[float, str]] = [
    (0.30, C_HIGH),
    (0.50, C_WARN),
    (0.70, C_ORANGE),
    (1.01, C_DANGER),
]


def _risk_color(score: float) -> str:
    for threshold, color in _RISK_COLOR_BAND:
        if score < threshold:
            return color
    return C_DANGER


def _score_bar_html(score: float, width_px: int = 100) -> str:
    pct = int(score * 100)
    color = _risk_color(score)
    return (
        "<div style='display:inline-block; vertical-align:middle; width:"
        + str(width_px)
        + "px; background:rgba(255,255,255,0.07); border-radius:4px;"
        + " height:6px; overflow:hidden; margin-right:6px'>"
        + "<div style='width:"
        + str(pct)
        + "%; height:100%; background:"
        + color
        + "; border-radius:4px'></div></div>"
        + "<span style='font-size:0.71rem; color:"
        + color
        + "; font-weight:700'>"
        + str(pct)
        + "%</span>"
    )


def _coverage_bar_html(score: float, width_px: int = 120) -> str:
    """Green-to-red coverage bar (high score = good)."""
    pct = int(score * 100)
    if score >= 0.75:
        color = C_HIGH
    elif score >= 0.50:
        color = C_WARN
    elif score >= 0.30:
        color = C_ORANGE
    else:
        color = C_DANGER
    return (
        "<div style='display:inline-block; vertical-align:middle; width:"
        + str(width_px)
        + "px; background:rgba(255,255,255,0.07); border-radius:4px;"
        + " height:7px; overflow:hidden; margin-right:6px'>"
        + "<div style='width:"
        + str(pct)
        + "%; height:100%; background:"
        + color
        + "; border-radius:4px; box-shadow:0 0 6px "
        + color
        + "60'></div></div>"
        + "<span style='font-size:0.72rem; color:"
        + color
        + "; font-weight:700'>"
        + str(pct)
        + "%</span>"
    )


def _dark_layout(height: int = 450) -> dict:
    return dict(
        height=height,
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        margin=dict(l=0, r=0, t=0, b=0),
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=12),
    )


def _section_title(text: str, subtitle: str = "", icon: str = "") -> None:
    icon_html = (
        "<span style='font-size:1.1rem; margin-right:8px; vertical-align:middle'>"
        + icon + "</span>" if icon else ""
    )
    sub_html = (
        "<div style='color:" + C_TEXT2 + "; font-size:0.81rem;"
        " margin-top:3px; line-height:1.5'>" + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        "<div style='margin-bottom:14px; margin-top:8px; padding-bottom:10px;"
        " border-bottom:1px solid rgba(255,255,255,0.06)'>"
        "<div style='font-size:1.08rem; font-weight:700; color:"
        + C_TEXT + "'>" + icon_html + text + "</div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _metric_card(
    label: str, value: str, color: str, sub: str = "", icon: str = ""
) -> str:
    icon_span = (
        "<div style='font-size:1.4rem; margin-bottom:4px'>" + icon + "</div>"
        if icon else ""
    )
    sub_span = (
        "<div style='font-size:0.67rem; color:" + C_TEXT3 + "; margin-top:3px'>"
        + sub + "</div>"
        if sub else ""
    )
    return (
        "<div style='background:" + C_CARD + "; border:1px solid "
        + color + "30; border-radius:12px; padding:16px 18px; text-align:center;"
        " box-shadow:0 2px 12px " + color + "12'>"
        + icon_span
        + "<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.09em;"
        " color:" + C_TEXT3 + "; margin-bottom:5px'>" + label + "</div>"
        "<div style='font-size:1.55rem; font-weight:800; color:" + color + "'>"
        + value + "</div>"
        + sub_span
        + "</div>"
    )


def _badge(text: str, color: str) -> str:
    return (
        "<span style='display:inline-block; background:" + color + "20;"
        " border:1px solid " + color + "50; border-radius:20px; padding:2px 10px;"
        " font-size:0.67rem; color:" + color + "; font-weight:700;"
        " letter-spacing:0.05em; margin:2px'>" + text + "</span>"
    )


# ---------------------------------------------------------------------------
# NEW Section 0 — Visibility Score Dashboard
# ---------------------------------------------------------------------------

def _render_visibility_score_dashboard(paths: list[SupplyChainPath]) -> None:
    """Overall supply chain visibility score, data coverage %, blind spots."""

    # Derive aggregate metrics from path data
    all_nodes = [n for p in paths for n in p.nodes]
    total_nodes = len(all_nodes)
    operational = sum(1 for n in all_nodes if n.status == "OPERATIONAL")
    delayed      = sum(1 for n in all_nodes if n.status == "DELAYED")
    disrupted    = sum(1 for n in all_nodes if n.status == "DISRUPTED")
    closed_count = sum(1 for n in all_nodes if n.status == "CLOSED")

    # Visibility score: weighted by operational + partial credit for delayed
    raw_vis = (operational + delayed * 0.5) / max(1, total_nodes)
    vis_score = min(1.0, raw_vis)
    vis_pct = int(vis_score * 100)

    # Data coverage: % of nodes with utilisation data > 0
    covered = sum(1 for n in all_nodes if n.current_utilization > 0)
    coverage_pct = int(covered / max(1, total_nodes) * 100)

    # Blind spots: nodes that are DISRUPTED or CLOSED — zero visibility
    blind_spots = [n for n in all_nodes if n.status in ("DISRUPTED", "CLOSED")]

    # Average risk across all paths
    avg_risk = sum(p.risk_score for p in paths) / max(1, len(paths))

    # Overall health colour
    if vis_pct >= 75:
        vis_color = C_HIGH
        vis_label = "GOOD"
    elif vis_pct >= 50:
        vis_color = C_WARN
        vis_label = "MODERATE"
    else:
        vis_color = C_DANGER
        vis_label = "POOR"

    # ── Hero gauge + KPI row ─────────────────────────────────────────────────
    col_gauge, col_kpis = st.columns([1, 2])

    with col_gauge:
        # Plotly gauge
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=vis_pct,
            number=dict(suffix="%", font=dict(size=36, color=vis_color, family="Inter, sans-serif")),
            title=dict(text="<b>Visibility Score</b>", font=dict(size=11, color=C_TEXT2)),
            gauge=dict(
                axis=dict(
                    range=[0, 100],
                    tickfont=dict(size=8, color=C_TEXT3),
                    tickwidth=1,
                    tickcolor=C_BORDER,
                ),
                bar=dict(color=vis_color, thickness=0.25),
                bgcolor="rgba(0,0,0,0)",
                borderwidth=0,
                steps=[
                    dict(range=[0, 30],  color="rgba(239,68,68,0.15)"),
                    dict(range=[30, 60], color="rgba(245,158,11,0.12)"),
                    dict(range=[60, 85], color="rgba(16,185,129,0.10)"),
                    dict(range=[85, 100],color="rgba(16,185,129,0.18)"),
                ],
                threshold=dict(
                    line=dict(color=vis_color, width=2),
                    thickness=0.75,
                    value=vis_pct,
                ),
            ),
        ))
        fig_gauge.update_layout(
            **_dark_layout(height=200),
            margin=dict(l=10, r=10, t=20, b=0),
        )
        st.plotly_chart(fig_gauge, use_container_width=True, key="vis_score_gauge")
        st.markdown(
            "<div style='text-align:center; margin-top:-10px'>"
            + _badge(vis_label + " VISIBILITY", vis_color)
            + "</div>",
            unsafe_allow_html=True,
        )

    with col_kpis:
        kpi_data = [
            ("Data Coverage",    str(coverage_pct) + "%",  C_ACCENT, str(covered) + " / " + str(total_nodes) + " nodes tracked"),
            ("Paths Monitored",  str(len(paths)),           C_PURPLE, "product categories"),
            ("Operational Nodes", str(operational),          C_HIGH,  "real-time data"),
            ("Delayed Nodes",    str(delayed),              C_WARN,  "partial tracking"),
            ("Blind Spots",      str(len(blind_spots)),     C_DANGER, "disrupted / closed"),
            ("Avg Path Risk",    str(int(avg_risk * 100)) + "%", _risk_color(avg_risk), "portfolio exposure"),
        ]
        rows = [kpi_data[:3], kpi_data[3:]]
        for row in rows:
            cols = st.columns(3)
            for col, (label, val, color, sub) in zip(cols, row):
                with col:
                    st.markdown(_metric_card(label, val, color, sub), unsafe_allow_html=True)
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Blind spot callout ───────────────────────────────────────────────────
    if blind_spots:
        bs_html = (
            "<div style='background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.25);"
            " border-radius:12px; padding:14px 18px; margin-top:4px'>"
            "<div style='font-size:0.75rem; font-weight:700; color:" + C_DANGER
            + "; margin-bottom:8px; letter-spacing:0.05em'>BLIND SPOTS — ZERO VISIBILITY</div>"
            "<div style='display:flex; flex-wrap:wrap; gap:6px'>"
        )
        for n in blind_spots:
            short = n.location_name.split(",")[0].split("(")[0].strip()
            status_c = _STATUS_COLOR.get(n.status, C_TEXT3)
            bs_html += _badge(n.status + " · " + short, status_c)
        bs_html += "</div></div>"
        st.markdown(bs_html, unsafe_allow_html=True)
    else:
        st.markdown(
            "<div style='background:rgba(16,185,129,0.06); border:1px solid rgba(16,185,129,0.2);"
            " border-radius:10px; padding:10px 16px; font-size:0.78rem; color:"
            + C_HIGH + "; margin-top:4px'>All monitored nodes operational — no blind spots detected.</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# NEW Section — Data Source Coverage Map
# ---------------------------------------------------------------------------

def _render_data_coverage_map(paths: list[SupplyChainPath]) -> None:
    """Choropleth-style scatter showing node coverage quality by region."""

    all_nodes = [n for p in paths for n in p.nodes]

    # Build per-country coverage aggregations
    country_data: dict[str, dict] = {}
    for node in all_nodes:
        c = node.country
        if c not in country_data:
            country_data[c] = {"total": 0, "good": 0, "risk_sum": 0.0, "names": []}
        country_data[c]["total"] += 1
        if node.status in ("OPERATIONAL", "DELAYED"):
            country_data[c]["good"] += 1
        country_data[c]["risk_sum"] += node.risk_score
        short = node.location_name.split(",")[0].split("(")[0].strip()
        country_data[c]["names"].append(short)

    # Coverage score per country
    for c, d in country_data.items():
        d["coverage"] = d["good"] / max(1, d["total"])
        d["avg_risk"] = d["risk_sum"] / max(1, d["total"])

    # Scatter the actual nodes on the geo map, coloured by coverage
    lats, lons, texts, colors, sizes, hovers = [], [], [], [], [], []
    for node in all_nodes:
        cov = country_data[node.country]["coverage"]
        if cov >= 0.75:
            col = C_HIGH
        elif cov >= 0.50:
            col = C_WARN
        elif cov >= 0.25:
            col = C_ORANGE
        else:
            col = C_DANGER

        lats.append(node.lat)
        lons.append(node.lon)
        texts.append(node.location_name.split(",")[0].split("(")[0].strip())
        colors.append(col)
        sizes.append(12 + int(node.current_utilization * 10))
        hovers.append(
            "<b>" + node.location_name + "</b><br>"
            "Country: " + node.country + "<br>"
            "Coverage: " + str(int(cov * 100)) + "%<br>"
            "Status: " + node.status + "<br>"
            "Utilisation: " + str(round(node.current_utilization * 100, 1)) + "%<br>"
            "Risk: " + str(round(node.risk_score * 100, 1)) + "%"
        )

    fig = go.Figure()

    # Coverage quality legend ghost traces
    for label, col in [("Good (≥75%)", C_HIGH), ("Moderate (50–74%)", C_WARN),
                        ("Low (25–49%)", C_ORANGE), ("Blind Spot (<25%)", C_DANGER)]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=10, color=col),
            name=label,
            showlegend=True,
        ))

    fig.add_trace(go.Scattergeo(
        lat=lats,
        lon=lons,
        mode="markers+text",
        marker=dict(
            size=sizes,
            color=colors,
            opacity=0.88,
            line=dict(color="rgba(255,255,255,0.25)", width=1),
        ),
        text=texts,
        textposition="top center",
        textfont=dict(size=8, color=C_TEXT2),
        hovertemplate=[h + "<extra></extra>" for h in hovers],
        showlegend=False,
    ))

    fig.update_layout(
        **_dark_layout(height=440),
        geo=dict(
            showland=True,
            landcolor="#1a2235",
            showocean=True,
            oceancolor="#0a1628",
            showcountries=True,
            countrycolor="rgba(255,255,255,0.08)",
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.12)",
            showframe=False,
            projection_type="natural earth",
            bgcolor=C_BG,
        ),
        legend=dict(
            orientation="h",
            x=0.01, y=0.01,
            font=dict(size=9, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.75)",
            bordercolor=C_BORDER,
            borderwidth=1,
        ),
        title=dict(
            text="Data Coverage Quality by Node  ·  size = utilisation",
            font=dict(size=11, color=C_TEXT2),
            x=0.01, y=0.97,
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="vis_coverage_map")

    # Per-country coverage summary
    country_cols = st.columns(min(4, len(country_data)))
    for idx, (country, d) in enumerate(
        sorted(country_data.items(), key=lambda x: x[1]["coverage"], reverse=True)
    ):
        with country_cols[idx % len(country_cols)]:
            cov = d["coverage"]
            if cov >= 0.75:
                c = C_HIGH
            elif cov >= 0.50:
                c = C_WARN
            elif cov >= 0.25:
                c = C_ORANGE
            else:
                c = C_DANGER
            card_html = (
                "<div style='background:" + C_CARD + "; border:1px solid " + c + "30;"
                " border-radius:10px; padding:10px 14px; margin-bottom:8px'>"
                "<div style='font-size:0.72rem; font-weight:700; color:" + C_TEXT
                + "; margin-bottom:4px'>" + country + "</div>"
                + _coverage_bar_html(cov, 90)
                + "<div style='font-size:0.65rem; color:" + C_TEXT3 + "; margin-top:4px'>"
                + str(d["total"]) + " nodes · avg risk "
                + str(int(d["avg_risk"] * 100)) + "%"
                + "</div></div>"
            )
            st.markdown(card_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# NEW Section — Shipment Tracking Simulation (Journey Stages)
# ---------------------------------------------------------------------------

def _render_shipment_tracking(path: SupplyChainPath) -> None:
    """Animated step-flow showing cargo journey stages with status indicators."""

    nodes  = path.nodes
    modes  = path.transit_modes
    n      = len(nodes)

    # Derive a simulated "current stage" — find first non-operational node
    # or default to ~40% progress
    current_stage = int(n * 0.4)
    for i, node in enumerate(nodes):
        if node.status in ("DELAYED", "DISRUPTED"):
            current_stage = i
            break

    # Build flow HTML
    flow_parts = ["<div style='display:flex; align-items:center; flex-wrap:nowrap; "
                  "overflow-x:auto; padding:16px 0; gap:0'>"]

    for i, node in enumerate(nodes):
        short = node.location_name.split(",")[0].split("(")[0].strip()
        status_c = _STATUS_COLOR.get(node.status, C_TEXT2)
        type_c   = _TYPE_COLOR.get(node.node_type, C_TEXT2)
        is_current = (i == current_stage)
        is_done    = (i < current_stage)
        is_future  = (i > current_stage)

        if is_done:
            bg_color  = C_HIGH + "20"
            bd_color  = C_HIGH + "60"
            dot_color = C_HIGH
            dot_label = "&#10003;"
        elif is_current:
            bg_color  = status_c + "20"
            bd_color  = status_c
            dot_color = status_c
            dot_label = "&#9654;"  # play arrow
        else:
            bg_color  = "rgba(255,255,255,0.03)"
            bd_color  = C_BORDER
            dot_color = C_TEXT3
            dot_label = str(i + 1)

        pulse_style = (
            "animation: pulse 1.5s infinite;" if is_current else ""
        )

        node_html = (
            "<div style='display:flex; flex-direction:column; align-items:center;"
            " min-width:90px; max-width:110px'>"
            # Dot indicator
            "<div style='width:36px; height:36px; border-radius:50%;"
            " background:" + bg_color + "; border:2px solid " + bd_color + ";"
            " display:flex; align-items:center; justify-content:center;"
            " font-size:0.8rem; color:" + dot_color + "; font-weight:700;"
            " box-shadow:0 0 " + ("12px " + dot_color + "80" if is_current else "0px transparent") + ";'>"
            + dot_label +
            "</div>"
            # Node type icon
            "<div style='font-size:0.65rem; color:" + type_c + "; margin-top:4px;"
            " font-weight:700; letter-spacing:0.05em'>" + node.node_type + "</div>"
            # Location name
            "<div style='font-size:0.70rem; color:" + (C_TEXT if is_current else C_TEXT2 if is_done else C_TEXT3)
            + "; text-align:center; margin-top:2px; line-height:1.3; max-width:90px'>"
            + short + "</div>"
            # Status badge
            "<div style='margin-top:4px'>" + _badge(node.status, status_c) + "</div>"
            # Delay note
            + ("<div style='font-size:0.63rem; color:" + C_WARN + "; margin-top:2px'>+"
               + str(node.delay_days) + " days</div>" if node.delay_days > 0 else "")
            + "</div>"
        )
        flow_parts.append(node_html)

        # Arrow / mode connector between nodes
        if i < len(modes):
            mode = modes[i]
            mode_c = _MODE_COLOR.get(mode, C_TEXT3)
            connector = (
                "<div style='display:flex; flex-direction:column; align-items:center;"
                " min-width:52px; padding-top:6px'>"
                "<div style='height:2px; width:44px; background: linear-gradient(90deg,"
                + mode_c + "80, " + mode_c + "); border-radius:2px'></div>"
                "<div style='font-size:0.58rem; color:" + mode_c + "; margin-top:3px;"
                " font-weight:600; letter-spacing:0.04em'>" + mode + "</div>"
                "</div>"
            )
            flow_parts.append(connector)

    flow_parts.append("</div>")
    flow_html = "".join(flow_parts)

    # Wrap in a styled container
    st.markdown(
        "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
        " border-radius:14px; padding:14px 18px; overflow-x:auto'>"
        + flow_html + "</div>",
        unsafe_allow_html=True,
    )

    # Progress stats
    pct_complete = int(current_stage / max(1, n - 1) * 100)
    total_delay  = sum(nd.delay_days for nd in nodes)
    stages_done  = current_stage
    stages_left  = n - 1 - current_stage

    prog_cols = st.columns(4)
    for col, (lbl, val, col_c) in zip(prog_cols, [
        ("Journey Complete",     str(pct_complete) + "%",       C_ACCENT),
        ("Stages Done",          str(stages_done) + " / " + str(n - 1), C_HIGH),
        ("Stages Remaining",     str(stages_left),              C_TEXT2),
        ("Accumulated Delay",    "+" + str(total_delay) + " days", C_WARN if total_delay else C_HIGH),
    ]):
        with col:
            st.markdown(_metric_card(lbl, val, col_c), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# NEW Section — Exception Management Dashboard
# ---------------------------------------------------------------------------

def _render_exception_dashboard(paths: list[SupplyChainPath]) -> None:
    """Shipments at risk, delayed, requiring attention — sorted by severity."""

    exceptions: list[dict] = []
    for path in paths:
        for node in path.nodes:
            if node.status in ("DELAYED", "DISRUPTED", "CLOSED") or node.risk_score >= 0.45:
                severity = (
                    "CRITICAL" if node.status in ("DISRUPTED", "CLOSED") or node.risk_score >= 0.65
                    else "HIGH"   if node.risk_score >= 0.50 or node.delay_days >= 5
                    else "WATCH"
                )
                exceptions.append({
                    "path":     path.product_category.split("(")[0].strip(),
                    "node":     node.location_name.split(",")[0].split("(")[0].strip(),
                    "status":   node.status,
                    "delay":    node.delay_days,
                    "risk":     node.risk_score,
                    "severity": severity,
                    "type":     node.node_type,
                    "country":  node.country,
                    "util":     node.current_utilization,
                })

    # Sort: CRITICAL first, then HIGH, then WATCH
    _sev_order = {"CRITICAL": 0, "HIGH": 1, "WATCH": 2}
    exceptions.sort(key=lambda x: (_sev_order.get(x["severity"], 3), -x["risk"]))

    if not exceptions:
        st.markdown(
            "<div style='background:rgba(16,185,129,0.07); border:1px solid rgba(16,185,129,0.2);"
            " border-radius:12px; padding:16px 20px; text-align:center;"
            " color:" + C_HIGH + "; font-size:0.85rem'>"
            "No active exceptions — all shipments within normal parameters.</div>",
            unsafe_allow_html=True,
        )
        return

    # Summary counts
    n_crit  = sum(1 for e in exceptions if e["severity"] == "CRITICAL")
    n_high  = sum(1 for e in exceptions if e["severity"] == "HIGH")
    n_watch = sum(1 for e in exceptions if e["severity"] == "WATCH")

    s_cols = st.columns(3)
    for col, (lbl, val, col_c) in zip(s_cols, [
        ("Critical Exceptions", str(n_crit),  C_DANGER),
        ("High Priority",       str(n_high),  C_ORANGE),
        ("Under Watch",         str(n_watch), C_WARN),
    ]):
        with col:
            st.markdown(_metric_card(lbl, val, col_c), unsafe_allow_html=True)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # Exception table as styled cards
    _SEV_COLOR = {"CRITICAL": C_DANGER, "HIGH": C_ORANGE, "WATCH": C_WARN}
    _SEV_BG    = {"CRITICAL": "239,68,68", "HIGH": "249,115,22", "WATCH": "245,158,11"}

    for exc in exceptions[:12]:  # cap at 12
        sev_c  = _SEV_COLOR.get(exc["severity"], C_TEXT2)
        sev_bg = _SEV_BG.get(exc["severity"], "100,116,139")
        risk_c = _risk_color(exc["risk"])

        row_html = (
            "<div style='background:rgba(" + sev_bg + ",0.05); border:1px solid rgba("
            + sev_bg + ",0.20); border-radius:10px; padding:10px 16px; margin-bottom:6px;"
            " display:flex; align-items:center; gap:12px; flex-wrap:wrap'>"
            # Severity badge
            "<div style='min-width:72px'>" + _badge(exc["severity"], sev_c) + "</div>"
            # Path + node
            "<div style='flex:1; min-width:160px'>"
            "<div style='font-size:0.78rem; font-weight:700; color:" + C_TEXT + "'>"
            + exc["node"] + "</div>"
            "<div style='font-size:0.68rem; color:" + C_TEXT2 + "'>"
            + exc["path"] + "  ·  " + exc["type"] + "  ·  " + exc["country"] + "</div>"
            "</div>"
            # Status
            "<div style='min-width:90px; text-align:center'>"
            + _badge(exc["status"], _STATUS_COLOR.get(exc["status"], C_TEXT2))
            + "</div>"
            # Delay
            "<div style='min-width:70px; text-align:center'>"
            "<div style='font-size:0.85rem; font-weight:700; color:"
            + (C_WARN if exc["delay"] > 0 else C_HIGH)
            + "'>+" + str(exc["delay"]) + "d</div>"
            "<div style='font-size:0.62rem; color:" + C_TEXT3 + "'>delay</div>"
            "</div>"
            # Risk
            "<div style='min-width:80px; text-align:right'>"
            "<div style='font-size:0.85rem; font-weight:700; color:" + risk_c + "'>"
            + str(int(exc["risk"] * 100)) + "%</div>"
            "<div style='font-size:0.62rem; color:" + C_TEXT3 + "'>risk score</div>"
            "</div>"
            # Utilisation bar
            "<div style='min-width:90px'>"
            + _coverage_bar_html(exc["util"], 80)
            + "<div style='font-size:0.60rem; color:" + C_TEXT3 + "; margin-top:2px'>utilisation</div>"
            "</div>"
            "</div>"
        )
        st.markdown(row_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# NEW Section — Predictive ETAs / Early Warning
# ---------------------------------------------------------------------------

def _render_predictive_etas(paths: list[SupplyChainPath]) -> None:
    """Early warning of potential delays before they materialise."""

    # Simulate ETA predictions per path
    eta_items: list[dict] = []
    for path in paths:
        base_days = path.total_transit_days
        # Accumulated delay from all nodes
        node_delay = sum(n.delay_days for n in path.nodes)
        # Risk-adjusted additional delay projection
        risk_penalty = int(path.risk_score * 12)
        projected_extra = node_delay + risk_penalty

        # Determine early-warning level
        if path.risk_score >= 0.60 or projected_extra >= 10:
            warning = "HIGH RISK"
            w_color = C_DANGER
        elif path.risk_score >= 0.40 or projected_extra >= 5:
            warning = "WATCH"
            w_color = C_WARN
        else:
            warning = "ON TRACK"
            w_color = C_HIGH

        # Bottleneck driver
        bn_node = next(
            (n for n in path.nodes if n.node_id == path.bottleneck_node),
            path.nodes[-1] if path.nodes else None,
        )
        driver = bn_node.location_name.split(",")[0].split("(")[0].strip() if bn_node else "Unknown"

        eta_items.append({
            "path":        path.product_category.split("(")[0].strip(),
            "base_days":   base_days,
            "node_delay":  node_delay,
            "risk_extra":  risk_penalty,
            "projected":   base_days + projected_extra,
            "warning":     warning,
            "w_color":     w_color,
            "driver":      driver,
            "risk":        path.risk_score,
        })

    # Sort: highest risk first
    eta_items.sort(key=lambda x: x["risk"], reverse=True)

    # ETA bar chart
    labels    = [e["path"] for e in eta_items]
    base_vals = [e["base_days"] for e in eta_items]
    nd_vals   = [e["node_delay"] for e in eta_items]
    rp_vals   = [e["risk_extra"] for e in eta_items]
    colors    = [e["w_color"] for e in eta_items]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Baseline ETA",
        y=labels,
        x=base_vals,
        orientation="h",
        marker=dict(color=C_ACCENT, opacity=0.75),
        hovertemplate="<b>%{y}</b><br>Baseline: %{x} days<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Current Delays",
        y=labels,
        x=nd_vals,
        orientation="h",
        marker=dict(color=C_WARN, opacity=0.85),
        hovertemplate="<b>%{y}</b><br>Node delays: +%{x} days<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Risk Projection",
        y=labels,
        x=rp_vals,
        orientation="h",
        marker=dict(color=C_DANGER, opacity=0.70),
        hovertemplate="<b>%{y}</b><br>Risk projection: +%{x} days<extra></extra>",
    ))

    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(height=280),
        barmode="stack",
        xaxis=dict(
            title="Days",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=9, color=C_TEXT2),
        ),
        yaxis=dict(
            showgrid=False,
            tickfont=dict(size=9, color=C_TEXT2),
        ),
        legend=dict(
            orientation="h", x=0.01, y=1.06,
            font=dict(size=9, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="vis_predictive_etas")

    # Per-path ETA cards
    cols = st.columns(len(eta_items))
    for col, e in zip(cols, eta_items):
        with col:
            delta = e["projected"] - e["base_days"]
            card = (
                "<div style='background:" + C_CARD + "; border:1px solid "
                + e["w_color"] + "35; border-radius:10px; padding:10px 12px; text-align:center'>"
                "<div style='font-size:0.68rem; font-weight:700; color:" + C_TEXT
                + "; margin-bottom:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap'>"
                + e["path"] + "</div>"
                + _badge(e["warning"], e["w_color"])
                + "<div style='margin-top:6px'>"
                "<span style='font-size:1.3rem; font-weight:800; color:" + e["w_color"] + "'>"
                + str(e["projected"]) + "</span>"
                "<span style='font-size:0.68rem; color:" + C_TEXT3 + "'> days</span>"
                "</div>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3 + "; margin-top:2px'>"
                + ("+" + str(delta) + "d vs baseline" if delta > 0 else "on schedule")
                + "</div>"
                "<div style='font-size:0.60rem; color:" + C_TEXT2 + "; margin-top:4px;"
                " overflow:hidden; text-overflow:ellipsis; white-space:nowrap'>"
                "Driver: " + e["driver"] + "</div>"
                "</div>"
            )
            st.markdown(card, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# NEW Section — Supplier Origin Map
# ---------------------------------------------------------------------------

def _render_supplier_origin_map(
    paths: list[SupplyChainPath],
    trade_data: Any,
) -> None:
    """Origin country distribution of trade flows — supplier geography."""

    # Aggregate factory / origin nodes per country
    origin_counts: dict[str, dict] = {}
    for path in paths:
        for node in path.nodes:
            if node.node_type == "FACTORY":
                c = node.country
                if c not in origin_counts:
                    origin_counts[c] = {
                        "count": 0,
                        "lats": [],
                        "lons": [],
                        "names": [],
                        "risk_sum": 0.0,
                        "util_sum": 0.0,
                    }
                origin_counts[c]["count"] += 1
                origin_counts[c]["lats"].append(node.lat)
                origin_counts[c]["lons"].append(node.lon)
                origin_counts[c]["names"].append(
                    node.location_name.split(",")[0].split("(")[0].strip()
                )
                origin_counts[c]["risk_sum"] += node.risk_score
                origin_counts[c]["util_sum"] += node.current_utilization

    # If no factory nodes, fall back to all nodes
    if not origin_counts:
        for path in paths:
            for node in path.nodes:
                c = node.country
                if c not in origin_counts:
                    origin_counts[c] = {
                        "count": 0, "lats": [], "lons": [],
                        "names": [], "risk_sum": 0.0, "util_sum": 0.0,
                    }
                origin_counts[c]["count"] += 1
                origin_counts[c]["lats"].append(node.lat)
                origin_counts[c]["lons"].append(node.lon)
                origin_counts[c]["names"].append(
                    node.location_name.split(",")[0].split("(")[0].strip()
                )
                origin_counts[c]["risk_sum"] += node.risk_score
                origin_counts[c]["util_sum"] += node.current_utilization

    # Bubble scatter: one bubble per country centroid
    fig = go.Figure()

    max_count = max((d["count"] for d in origin_counts.values()), default=1)
    for country, d in origin_counts.items():
        avg_lat  = sum(d["lats"]) / len(d["lats"])
        avg_lon  = sum(d["lons"]) / len(d["lons"])
        avg_risk = d["risk_sum"] / d["count"]
        avg_util = d["util_sum"] / d["count"]
        bubble_size = 14 + int(d["count"] / max_count * 30)
        risk_c = _risk_color(avg_risk)

        fig.add_trace(go.Scattergeo(
            lat=[avg_lat],
            lon=[avg_lon],
            mode="markers+text",
            marker=dict(
                size=bubble_size,
                color=risk_c,
                opacity=0.82,
                line=dict(color="rgba(255,255,255,0.3)", width=1.5),
            ),
            text=[country],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT),
            hovertemplate=(
                "<b>" + country + "</b><br>"
                "Supplier nodes: " + str(d["count"]) + "<br>"
                "Avg risk: " + str(int(avg_risk * 100)) + "%<br>"
                "Avg utilisation: " + str(int(avg_util * 100)) + "%<br>"
                "Facilities: " + ", ".join(d["names"][:3])
                + ("<extra></extra>")
            ),
            showlegend=False,
        ))

    # Risk colour legend
    for label, col in [("Low Risk", C_HIGH), ("Moderate", C_WARN), ("High Risk", C_DANGER)]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None], mode="markers",
            marker=dict(size=10, color=col),
            name=label, showlegend=True,
        ))

    fig.update_layout(
        **_dark_layout(height=400),
        geo=dict(
            showland=True, landcolor="#1a2235",
            showocean=True, oceancolor="#0a1628",
            showcountries=True, countrycolor="rgba(255,255,255,0.08)",
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.12)",
            showframe=False, projection_type="natural earth", bgcolor=C_BG,
        ),
        legend=dict(
            orientation="h", x=0.01, y=0.01,
            font=dict(size=9, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.75)",
            bordercolor=C_BORDER, borderwidth=1,
        ),
        title=dict(
            text="Supplier Origin Geography  ·  bubble size = node count",
            font=dict(size=11, color=C_TEXT2),
            x=0.01, y=0.97,
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="vis_supplier_origin_map")

    # Country concentration risk note
    total_nodes = sum(d["count"] for d in origin_counts.values())
    top_countries = sorted(origin_counts.items(), key=lambda x: x[1]["count"], reverse=True)[:3]
    concentration = sum(d["count"] for _, d in top_countries) / max(1, total_nodes)
    conc_color = C_DANGER if concentration > 0.70 else C_WARN if concentration > 0.50 else C_HIGH
    st.markdown(
        "<div style='background:" + C_CARD + "; border:1px solid " + conc_color + "30;"
        " border-radius:10px; padding:10px 16px; font-size:0.76rem'>"
        "<span style='color:" + conc_color + "; font-weight:700'>Concentration Risk: </span>"
        "<span style='color:" + C_TEXT2 + "'>Top 3 countries account for "
        + str(int(concentration * 100)) + "% of supplier nodes ("
        + ", ".join(c for c, _ in top_countries) + ").</span>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# NEW Section — Data Quality Scorecard
# ---------------------------------------------------------------------------

def _render_data_quality_scorecard(paths: list[SupplyChainPath]) -> None:
    """Completeness, timeliness, accuracy per data source."""

    # Synthesise quality dimensions from path/node data
    all_nodes = [n for p in paths for n in p.nodes]
    total = len(all_nodes)

    # Completeness: % nodes with all fields populated (utilization + risk > 0)
    complete = sum(
        1 for n in all_nodes
        if n.current_utilization > 0 and n.risk_score > 0 and n.delay_days >= 0
    )
    completeness = complete / max(1, total)

    # Timeliness: % operational (data fresh) vs disrupted/closed (stale)
    timely = sum(1 for n in all_nodes if n.status in ("OPERATIONAL", "DELAYED"))
    timeliness = timely / max(1, total)

    # Accuracy proxy: nodes with utilisation in plausible range [0.05, 0.98]
    accurate = sum(1 for n in all_nodes if 0.05 <= n.current_utilization <= 0.98)
    accuracy = accurate / max(1, total)

    # Per data source definitions
    sources = [
        {
            "name":         "AIS / Vessel Tracking",
            "completeness": min(1.0, completeness * 0.95),
            "timeliness":   min(1.0, timeliness  * 0.90),
            "accuracy":     min(1.0, accuracy     * 0.92),
            "description":  "Automatic Identification System vessel positions",
            "icon":         "🛳",
        },
        {
            "name":         "Port Congestion Data",
            "completeness": min(1.0, completeness * 1.00),
            "timeliness":   min(1.0, timeliness  * 0.85),
            "accuracy":     min(1.0, accuracy     * 0.88),
            "description":  "Real-time berth utilisation and queue lengths",
            "icon":         "⚓",
        },
        {
            "name":         "Freight Rate Feeds",
            "completeness": min(1.0, completeness * 0.80),
            "timeliness":   min(1.0, timeliness  * 1.00),
            "accuracy":     min(1.0, accuracy     * 0.95),
            "description":  "Spot and contract rate indices",
            "icon":         "💱",
        },
        {
            "name":         "Trade Flow Records",
            "completeness": min(1.0, completeness * 0.88),
            "timeliness":   min(1.0, timeliness  * 0.70),
            "accuracy":     min(1.0, accuracy     * 0.97),
            "description":  "Customs and bill-of-lading data",
            "icon":         "📦",
        },
        {
            "name":         "Risk Intelligence",
            "completeness": min(1.0, completeness * 0.75),
            "timeliness":   min(1.0, timeliness  * 0.95),
            "accuracy":     min(1.0, accuracy     * 0.82),
            "description":  "Geopolitical and weather risk signals",
            "icon":         "⚠",
        },
    ]

    # Radar chart
    categories = ["Completeness", "Timeliness", "Accuracy"]

    fig = go.Figure()
    palette = [C_ACCENT, C_HIGH, C_WARN, C_PURPLE, C_CYAN]
    for i, src in enumerate(sources):
        vals = [src["completeness"], src["timeliness"], src["accuracy"]]
        vals_closed = vals + [vals[0]]
        cats_closed = categories + [categories[0]]
        fig.add_trace(go.Scatterpolar(
            r=[v * 100 for v in vals_closed],
            theta=cats_closed,
            fill="toself",
            fillcolor=palette[i % len(palette)] + "18",
            line=dict(color=palette[i % len(palette)], width=2),
            name=src["name"],
            hovertemplate=(
                "<b>" + src["name"] + "</b><br>"
                "%{theta}: %{r:.0f}%<extra></extra>"
            ),
        ))

    fig.update_layout(
        **_dark_layout(height=360),
        polar=dict(
            bgcolor=C_BG,
            angularaxis=dict(
                tickfont=dict(size=10, color=C_TEXT2),
                linecolor=C_BORDER,
                gridcolor="rgba(255,255,255,0.07)",
            ),
            radialaxis=dict(
                range=[0, 100],
                ticksuffix="%",
                tickfont=dict(size=8, color=C_TEXT3),
                gridcolor="rgba(255,255,255,0.06)",
                linecolor=C_BORDER,
            ),
        ),
        legend=dict(
            orientation="v",
            x=1.02, y=0.5,
            font=dict(size=9, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="vis_data_quality_radar")

    # Scorecard table
    for src in sources:
        overall = (src["completeness"] + src["timeliness"] + src["accuracy"]) / 3.0
        ov_c    = C_HIGH if overall >= 0.75 else C_WARN if overall >= 0.50 else C_DANGER

        sc_html = (
            "<div style='background:" + C_CARD + "; border:1px solid " + ov_c + "22;"
            " border-radius:10px; padding:10px 16px; margin-bottom:6px;"
            " display:flex; align-items:center; gap:16px; flex-wrap:wrap'>"
            # Name + description
            "<div style='min-width:180px; flex:1'>"
            "<div style='font-size:0.78rem; font-weight:700; color:" + C_TEXT + "'>"
            + src["name"] + "</div>"
            "<div style='font-size:0.65rem; color:" + C_TEXT3 + "; margin-top:1px'>"
            + src["description"] + "</div>"
            "</div>"
            # Three dimension bars
        )
        for dim_label, dim_val in [
            ("Completeness", src["completeness"]),
            ("Timeliness",   src["timeliness"]),
            ("Accuracy",     src["accuracy"]),
        ]:
            dim_c = C_HIGH if dim_val >= 0.75 else C_WARN if dim_val >= 0.50 else C_DANGER
            sc_html += (
                "<div style='min-width:110px; text-align:center'>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3 + "; margin-bottom:3px;"
                " letter-spacing:0.04em'>" + dim_label + "</div>"
                + _coverage_bar_html(dim_val, 90)
                + "</div>"
            )
        sc_html += (
            # Overall score
            "<div style='min-width:60px; text-align:center'>"
            "<div style='font-size:1.1rem; font-weight:800; color:" + ov_c + "'>"
            + str(int(overall * 100)) + "%</div>"
            "<div style='font-size:0.60rem; color:" + C_TEXT3 + "'>overall</div>"
            "</div>"
            "</div>"
        )
        st.markdown(sc_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# NEW Section — Coverage Gaps Analysis
# ---------------------------------------------------------------------------

def _render_coverage_gaps(paths: list[SupplyChainPath]) -> None:
    """Where more data is needed for better supply chain visibility."""

    all_nodes = [n for p in paths for n in p.nodes]
    gaps: list[dict] = []

    for path in paths:
        for i, node in enumerate(path.nodes):
            # Gap indicators
            gap_reasons = []
            gap_score   = 0.0

            if node.status in ("DISRUPTED", "CLOSED"):
                gap_reasons.append("No live data — " + node.status.lower())
                gap_score += 0.40
            if node.current_utilization == 0:
                gap_reasons.append("Missing utilisation telemetry")
                gap_score += 0.25
            if node.risk_score == 0:
                gap_reasons.append("No risk signal available")
                gap_score += 0.20
            if node.delay_days == 0 and node.status == "DELAYED":
                gap_reasons.append("Delay reported but unquantified")
                gap_score += 0.15
            # Check if node is isolated (no modes adjacent — approximation)
            if i > 0 and i < len(path.nodes) - 1:
                mode_before = path.transit_modes[i - 1] if i - 1 < len(path.transit_modes) else None
                mode_after  = path.transit_modes[i]     if i     < len(path.transit_modes) else None
                if mode_before is None or mode_after is None:
                    gap_reasons.append("Missing transit mode data")
                    gap_score += 0.10

            if gap_reasons:
                gaps.append({
                    "path":    path.product_category.split("(")[0].strip(),
                    "node":    node.location_name.split(",")[0].split("(")[0].strip(),
                    "country": node.country,
                    "type":    node.node_type,
                    "reasons": gap_reasons,
                    "score":   min(1.0, gap_score),
                    "priority": "HIGH" if gap_score >= 0.50 else "MEDIUM" if gap_score >= 0.25 else "LOW",
                })

    if not gaps:
        st.markdown(
            "<div style='background:rgba(16,185,129,0.07); border:1px solid rgba(16,185,129,0.2);"
            " border-radius:12px; padding:14px 18px; color:" + C_HIGH
            + "; font-size:0.82rem'>Full data coverage — no gaps detected.</div>",
            unsafe_allow_html=True,
        )
        return

    # Sort highest gap score first
    gaps.sort(key=lambda x: -x["score"])

    # Horizontal bar chart of gap scores
    labels     = [g["node"] for g in gaps[:10]]
    scores_pct = [int(g["score"] * 100) for g in gaps[:10]]
    gap_colors = [
        C_DANGER if g["priority"] == "HIGH" else C_WARN if g["priority"] == "MEDIUM" else C_TEXT3
        for g in gaps[:10]
    ]

    fig = go.Figure(go.Bar(
        y=labels,
        x=scores_pct,
        orientation="h",
        marker=dict(color=gap_colors, opacity=0.82,
                    line=dict(color="rgba(0,0,0,0.2)", width=1)),
        text=[str(s) + "%" for s in scores_pct],
        textposition="outside",
        textfont=dict(size=9, color=C_TEXT2),
        hovertemplate=(
            "<b>%{y}</b><br>Gap severity: %{x}%<extra></extra>"
        ),
    ))
    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(height=max(200, len(gaps[:10]) * 34 + 40)),
        xaxis=dict(
            title="Gap Severity Score (%)",
            range=[0, 110],
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=9, color=C_TEXT2),
        ),
        yaxis=dict(showgrid=False, tickfont=dict(size=9, color=C_TEXT2)),
    )
    st.plotly_chart(fig, use_container_width=True, key="vis_coverage_gaps")

    # Detailed gap cards
    _PRIO_COLOR = {"HIGH": C_DANGER, "MEDIUM": C_WARN, "LOW": C_TEXT3}

    for gap in gaps[:8]:
        prio_c = _PRIO_COLOR.get(gap["priority"], C_TEXT3)
        reasons_html = "".join(
            "<div style='font-size:0.68rem; color:" + C_TEXT2 + "; margin-bottom:2px'>"
            "&#8227; " + r + "</div>"
            for r in gap["reasons"]
        )
        card_html = (
            "<div style='background:" + C_CARD + "; border-left:3px solid " + prio_c + ";"
            " border-radius:0 10px 10px 0; padding:10px 14px; margin-bottom:6px;"
            " display:flex; gap:12px; align-items:flex-start'>"
            "<div style='min-width:72px'>" + _badge(gap["priority"], prio_c) + "</div>"
            "<div style='flex:1'>"
            "<div style='font-size:0.76rem; font-weight:700; color:" + C_TEXT + "'>"
            + gap["node"] + "</div>"
            "<div style='font-size:0.64rem; color:" + C_TEXT3 + "; margin-bottom:5px'>"
            + gap["path"] + "  ·  " + gap["type"] + "  ·  " + gap["country"] + "</div>"
            + reasons_html
            + "</div>"
            "<div style='min-width:56px; text-align:center'>"
            "<div style='font-size:1.1rem; font-weight:800; color:" + prio_c + "'>"
            + str(int(gap["score"] * 100)) + "%</div>"
            "<div style='font-size:0.60rem; color:" + C_TEXT3 + "'>severity</div>"
            "</div>"
            "</div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)

    # Recommendation summary
    n_high   = sum(1 for g in gaps if g["priority"] == "HIGH")
    n_medium = sum(1 for g in gaps if g["priority"] == "MEDIUM")
    st.markdown(
        "<div style='background:rgba(59,130,246,0.07); border:1px solid rgba(59,130,246,0.25);"
        " border-radius:10px; padding:12px 16px; margin-top:8px; font-size:0.76rem;"
        " color:" + C_TEXT2 + "'>"
        "<span style='color:" + C_ACCENT + "; font-weight:700'>Recommendation: </span>"
        "Prioritise data acquisition for " + str(n_high) + " high-severity nodes. "
        "Connect AIS feeds, integrate port authority APIs, and deploy IoT sensors at "
        + str(n_medium) + " medium-priority facilities to close visibility gaps."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# NEW — Visibility Intelligence Scorecard (top-of-tab summary strip)
# ---------------------------------------------------------------------------

def _render_visibility_intelligence_scorecard(paths: list[SupplyChainPath]) -> None:
    """Compact top-of-tab scorecard: overall visibility %, data coverage %, blind spots count.

    This section surfaces the five core intelligence items in a single glance:
      1. Overall Visibility % KPI card
      2. Data Coverage % KPI card
      3. Blind Spots count KPI card
      4. Data quality summary (completeness / timeliness) as progress bars
      5. Coverage gap count badge
    """
    _section_title(
        "Visibility Intelligence Scorecard",
        "Top-line data coverage health at a glance — drilldown sections follow below.",
        icon="",
    )

    all_nodes  = [n for p in paths for n in p.nodes]
    total      = len(all_nodes)
    operational = sum(1 for n in all_nodes if n.status == "OPERATIONAL")
    delayed     = sum(1 for n in all_nodes if n.status == "DELAYED")
    disrupted   = sum(1 for n in all_nodes if n.status == "DISRUPTED")
    closed_n    = sum(1 for n in all_nodes if n.status == "CLOSED")

    # ── KPI 1: Overall Visibility % ──────────────────────────────────────────
    vis_score   = min(1.0, (operational + delayed * 0.5) / max(1, total))
    vis_pct     = int(vis_score * 100)
    vis_color   = C_HIGH if vis_pct >= 75 else C_WARN if vis_pct >= 50 else C_DANGER

    # ── KPI 2: Data Coverage % ───────────────────────────────────────────────
    covered       = sum(1 for n in all_nodes if n.current_utilization > 0)
    coverage_pct  = int(covered / max(1, total) * 100)
    cov_color     = C_HIGH if coverage_pct >= 75 else C_WARN if coverage_pct >= 50 else C_DANGER

    # ── KPI 3: Blind Spots ───────────────────────────────────────────────────
    blind_spots   = disrupted + closed_n
    blind_color   = C_DANGER if blind_spots > 3 else C_WARN if blind_spots > 0 else C_HIGH

    # ── KPI 4: Data Quality (completeness + timeliness) ─────────────────────
    complete  = sum(1 for n in all_nodes if n.current_utilization > 0 and n.risk_score > 0)
    completeness = complete / max(1, total)
    timely       = (operational + delayed) / max(1, total)

    # ── KPI 5: Coverage Gaps count ───────────────────────────────────────────
    gap_count = sum(
        1 for p in paths for n in p.nodes
        if n.status in ("DISRUPTED", "CLOSED") or n.current_utilization == 0 or n.risk_score == 0
    )
    gap_color = C_DANGER if gap_count > 6 else C_WARN if gap_count > 2 else C_HIGH

    # ── Layout: 3 KPI cards + 2 progress bar columns ─────────────────────────
    kpi_col1, kpi_col2, kpi_col3, qual_col, gap_col = st.columns([1, 1, 1, 2, 1])

    for col, label, value, sub, color in [
        (kpi_col1, "Overall Visibility",  f"{vis_pct}%",        f"{operational} nodes operational",     vis_color),
        (kpi_col2, "Data Coverage",       f"{coverage_pct}%",   f"{covered}/{total} nodes tracked",     cov_color),
        (kpi_col3, "Blind Spots",         str(blind_spots),      "disrupted or closed nodes",           blind_color),
    ]:
        with col:
            col.markdown(
                f'<div style="background:{C_CARD2}; border:1px solid {color}28;'
                f' border-top:3px solid {color}; border-radius:10px;'
                f' padding:14px 16px; text-align:center; height:100%">'
                f'<div style="font-size:0.58rem; font-weight:700; color:{C_TEXT3};'
                f' text-transform:uppercase; letter-spacing:0.10em; margin-bottom:6px">'
                f'{label}</div>'
                f'<div style="font-size:1.8rem; font-weight:900; color:{color};'
                f' font-variant-numeric:tabular-nums; line-height:1">'
                f'{value}</div>'
                f'<div style="font-size:0.64rem; color:{C_TEXT3}; margin-top:6px">{sub}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    with qual_col:
        qual_col.markdown(
            f'<div style="background:{C_CARD2}; border:1px solid {C_BORDER};'
            f' border-radius:10px; padding:14px 16px; height:100%">'
            f'<div style="font-size:0.58rem; font-weight:700; color:{C_TEXT3};'
            f' text-transform:uppercase; letter-spacing:0.10em; margin-bottom:10px">'
            f'Data Quality Snapshot</div>',
            unsafe_allow_html=True,
        )
        for dim_label, dim_val in [
            ("Completeness", completeness),
            ("Timeliness",   timely),
        ]:
            dim_c = C_HIGH if dim_val >= 0.75 else C_WARN if dim_val >= 0.50 else C_DANGER
            qual_col.markdown(
                f'<div style="margin-bottom:8px">'
                f'<div style="display:flex; justify-content:space-between;'
                f' align-items:center; margin-bottom:3px">'
                f'<span style="font-size:0.72rem; color:{C_TEXT2}">{dim_label}</span>'
                f'<span style="font-size:0.78rem; font-weight:700; color:{dim_c}">'
                f'{int(dim_val*100)}%</span></div>'
                f'<div style="background:rgba(255,255,255,0.06); border-radius:4px;'
                f' height:5px; overflow:hidden">'
                f'<div style="background:{dim_c}; width:{int(dim_val*100)}%;'
                f' height:5px; border-radius:4px; box-shadow:0 0 6px {dim_c}60">'
                f'</div></div></div>',
                unsafe_allow_html=True,
            )
        qual_col.markdown('</div>', unsafe_allow_html=True)

    with gap_col:
        gap_col.markdown(
            f'<div style="background:{C_CARD2}; border:1px solid {gap_color}28;'
            f' border-top:3px solid {gap_color}; border-radius:10px;'
            f' padding:14px 16px; text-align:center; height:100%">'
            f'<div style="font-size:0.58rem; font-weight:700; color:{C_TEXT3};'
            f' text-transform:uppercase; letter-spacing:0.10em; margin-bottom:6px">'
            f'Coverage Gaps</div>'
            f'<div style="font-size:1.8rem; font-weight:900; color:{gap_color};'
            f' font-variant-numeric:tabular-nums; line-height:1">'
            f'{gap_count}</div>'
            f'<div style="font-size:0.64rem; color:{C_TEXT3}; margin-top:6px">'
            f'nodes need attention</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Existing Section — Supply Chain Map
# ---------------------------------------------------------------------------

def _render_supply_chain_map(path: SupplyChainPath) -> None:
    """Plotly Scattergeo dark globe showing the full supply chain path."""
    nodes = path.nodes
    modes = path.transit_modes

    traces: list[go.BaseTraceType] = []

    # Connecting arcs between consecutive nodes (coloured by transport mode)
    for i in range(len(nodes) - 1):
        n0 = nodes[i]
        n1 = nodes[i + 1]
        mode = modes[i] if i < len(modes) else "OCEAN"
        line_color = _MODE_COLOR.get(mode, C_TEXT2)

        steps = 8
        lats = [n0.lat + (n1.lat - n0.lat) * t / steps for t in range(steps + 1)]
        lons = [n0.lon + (n1.lon - n0.lon) * t / steps for t in range(steps + 1)]

        traces.append(go.Scattergeo(
            lat=lats,
            lon=lons,
            mode="lines",
            line=dict(width=2.5, color=line_color),
            opacity=0.85,
            name=mode,
            showlegend=False,
            hoverinfo="skip",
        ))

        for alpha_idx, opacity in enumerate([0.35, 0.18]):
            offset = (alpha_idx + 1) * 0.5
            ghost_lats = [n0.lat + (n1.lat - n0.lat) * t / steps for t in range(steps + 1)]
            ghost_lons = [n0.lon + (n1.lon - n0.lon) * t / steps + offset for t in range(steps + 1)]
            traces.append(go.Scattergeo(
                lat=ghost_lats, lon=ghost_lons,
                mode="lines",
                line=dict(width=1.5, color=line_color),
                opacity=opacity,
                showlegend=False,
                hoverinfo="skip",
            ))

    # Mode legend entries
    seen_modes: set = set()
    for mode in modes:
        if mode not in seen_modes:
            seen_modes.add(mode)
            traces.append(go.Scattergeo(
                lat=[None], lon=[None],
                mode="lines",
                line=dict(width=3, color=_MODE_COLOR.get(mode, C_TEXT2)),
                name=mode,
                showlegend=True,
            ))

    # Node markers
    for node in nodes:
        status_color = _STATUS_COLOR.get(node.status, C_TEXT2)
        type_color   = _TYPE_COLOR.get(node.node_type, C_TEXT2)
        symbol       = _TYPE_SYMBOL.get(node.node_type, "circle")

        hover_text = (
            "<b>" + node.location_name + "</b><br>"
            + "Type: " + node.node_type + "<br>"
            + "Status: " + node.status + "<br>"
            + "Utilisation: " + str(round(node.current_utilization * 100, 1)) + "%<br>"
            + "Delay: " + str(node.delay_days) + " days<br>"
            + "Risk: " + str(round(node.risk_score * 100, 1)) + "%"
        )

        traces.append(go.Scattergeo(
            lat=[node.lat],
            lon=[node.lon],
            mode="markers+text",
            marker=dict(
                size=14,
                color=type_color,
                symbol=symbol,
                line=dict(color=status_color, width=2),
            ),
            text=[node.location_name.split(",")[0].split("(")[0].strip()],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            hovertemplate=hover_text + "<extra></extra>",
            showlegend=False,
        ))

    layout = go.Layout(
        **_dark_layout(height=550),
        geo=dict(
            showland=True,
            landcolor="#1a2235",
            showocean=True,
            oceancolor="#0a1628",
            showcountries=True,
            countrycolor="rgba(255,255,255,0.08)",
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.12)",
            showframe=False,
            projection_type="natural earth",
            bgcolor=C_BG,
        ),
        legend=dict(
            orientation="h",
            x=0.01, y=0.01,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.7)",
            bordercolor=C_BORDER,
            borderwidth=1,
        ),
        title=dict(
            text="<b>" + path.product_category + "</b>  Supply Chain Path",
            font=dict(size=13, color=C_TEXT),
            x=0.01,
            y=0.97,
        ),
    )

    fig = go.Figure(data=traces, layout=layout)
    st.plotly_chart(fig, use_container_width=True, key="vis_supply_chain_map")


# ---------------------------------------------------------------------------
# Existing Section — Journey Timeline (Gantt-style)
# ---------------------------------------------------------------------------

def _render_journey_timeline(path: SupplyChainPath) -> None:
    """Horizontal Gantt chart: each node/segment as a time block."""
    nodes = path.nodes
    modes = path.transit_modes

    _MODE_DAYS: dict[str, int] = {
        "OCEAN": 18, "RAIL": 4, "TRUCK": 2, "PIPELINE": 1, "INLAND": 3,
    }

    segments: list[dict] = []
    total_mode_days = sum(_MODE_DAYS.get(m, 3) for m in modes)
    if total_mode_days == 0:
        total_mode_days = 1

    node_dwell_total = max(0, path.total_transit_days - total_mode_days)
    node_dwell_each  = max(1, node_dwell_total // max(1, len(nodes)))

    for i, node in enumerate(nodes):
        risk_c = _risk_color(node.risk_score)
        segments.append({
            "label":     node.location_name.split(",")[0].split("(")[0].strip(),
            "days":      node_dwell_each + node.delay_days,
            "color":     risk_c,
            "is_node":   True,
            "node_type": node.node_type,
            "status":    node.status,
        })
        if i < len(modes):
            mode = modes[i]
            seg_days = _MODE_DAYS.get(mode, 3)
            segments.append({
                "label":     mode,
                "days":      seg_days,
                "color":     _MODE_COLOR.get(mode, C_TEXT2),
                "is_node":   False,
                "node_type": "",
                "status":    "",
            })

    total_days = sum(s["days"] for s in segments)

    fig = go.Figure()
    x_start = 0.0
    for seg in segments:
        width_pct = seg["days"] / total_days * 100.0 if total_days > 0 else 0
        x_end = x_start + width_pct

        hover = (
            "<b>" + seg["label"] + "</b><br>"
            + str(seg["days"]) + " days"
            + ("<br>Type: " + seg["node_type"] if seg["is_node"] else "")
            + ("<br>Status: " + seg["status"] if seg["status"] else "")
        )

        fig.add_trace(go.Bar(
            x=[width_pct],
            y=["Journey"],
            orientation="h",
            base=x_start,
            marker=dict(
                color=seg["color"],
                opacity=0.85 if seg["is_node"] else 0.55,
                line=dict(color="rgba(0,0,0,0.3)", width=1),
            ),
            text=seg["label"] if width_pct > 5 else "",
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=9, color=C_TEXT),
            hovertemplate=hover + "<extra></extra>",
            showlegend=False,
        ))
        x_start = x_end

    progress_x   = total_days * 0.40
    progress_pct = progress_x / total_days * 100.0 if total_days > 0 else 0
    fig.add_vline(
        x=progress_pct,
        line=dict(color=C_HIGH, width=2, dash="dot"),
        annotation_text="Now",
        annotation_font=dict(color=C_HIGH, size=10),
    )

    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(height=140),
        barmode="stack",
        xaxis=dict(
            title="% of total transit time (" + str(path.total_transit_days) + " days)",
            range=[0, 100],
            showgrid=False,
            tickfont=dict(size=9, color=C_TEXT3),
            titlefont=dict(size=9, color=C_TEXT2),
        ),
        yaxis=dict(showticklabels=False, showgrid=False),
    )

    st.plotly_chart(fig, use_container_width=True, key="vis_journey_timeline")


# ---------------------------------------------------------------------------
# Existing Section — Bottleneck Analyser
# ---------------------------------------------------------------------------

def _render_bottleneck_analyser(paths: list[SupplyChainPath]) -> None:
    """Bar chart of systemic bottleneck nodes, risk scores, and impact text."""
    details = get_bottleneck_details(paths)

    if not details:
        st.info("No shared bottleneck nodes detected across the selected paths.")
        return

    labels = [d["location_name"].split(",")[0].split("(")[0].strip() for d in details]
    counts = [d["path_count"] for d in details]
    risks  = [d["risk_score"] for d in details]
    colors = [_risk_color(r) for r in risks]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=labels,
        y=counts,
        marker=dict(color=colors, opacity=0.85, line=dict(color="rgba(0,0,0,0.2)", width=1)),
        text=[str(c) + " paths" for c in counts],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT2),
        hovertemplate="<b>%{x}</b><br>Appears in %{y} supply chains<extra></extra>",
        name="Path count",
    ))

    fig.add_trace(go.Scatter(
        x=labels,
        y=risks,
        mode="markers+lines",
        marker=dict(size=10, color=C_WARN, symbol="diamond"),
        line=dict(color=C_WARN, width=1.5, dash="dot"),
        yaxis="y2",
        name="Risk score",
        hovertemplate="<b>%{x}</b><br>Risk: %{y:.0%}<extra></extra>",
    ))

    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(height=350),
        barmode="group",
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color=C_TEXT2)),
        yaxis=dict(
            title="# Supply chains affected",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=9, color=C_TEXT2),
            titlefont=dict(size=9, color=C_TEXT2),
        ),
        yaxis2=dict(
            title="Risk score",
            overlaying="y",
            side="right",
            range=[0, 1],
            tickformat=".0%",
            showgrid=False,
            tickfont=dict(size=9, color=C_WARN),
            titlefont=dict(size=9, color=C_WARN),
        ),
        legend=dict(
            orientation="h",
            x=0.01, y=1.08,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="vis_bottleneck_analyser")

    # Impact callout cards
    cols = st.columns(min(3, len(details)))
    for idx, det in enumerate(details[:3]):
        col = cols[idx % len(cols)]
        with col:
            pct    = det["pct_affected"]
            risk_c = _risk_color(det["risk_score"])
            short_name = det["location_name"].split(",")[0].split("(")[0].strip()
            html = (
                "<div style='background:" + C_CARD + "; border:1px solid "
                + risk_c + "40; border-radius:10px; padding:12px 14px; text-align:center'>"
                "<div style='font-size:0.75rem; font-weight:700; color:"
                + C_TEXT + "; margin-bottom:4px'>" + short_name + "</div>"
                "<div style='font-size:1.4rem; font-weight:800; color:"
                + risk_c + "'>" + str(det["path_count"]) + " chains</div>"
                "<div style='font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px'>"
                "If disrupted: <b style='color:" + C_DANGER + "'>"
                + str(pct) + "%</b> of supply chains affected</div>"
                "<div style='font-size:0.68rem; color:" + C_TEXT3 + "; margin-top:2px'>"
                "Risk: " + str(round(det["risk_score"] * 100, 1)) + "%"
                " &nbsp;|&nbsp; " + det["node_type"] + "</div>"
                "</div>"
            )
            st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Existing Section — Disruption Simulator
# ---------------------------------------------------------------------------

def _render_disruption_simulator(paths: list[SupplyChainPath]) -> None:
    """Interactive disruption simulation with ripple-effect visualisation."""
    col_a, col_b = st.columns([2, 1])

    with col_a:
        path_labels = [p.product_category for p in paths]
        selected_label = st.selectbox(
            "Select supply chain path", path_labels, key="vis_sim_path"
        )
        sim_path_idx = path_labels.index(selected_label)
        sim_path = paths[sim_path_idx]

        node_labels = [n.location_name.split(",")[0].split("(")[0].strip() for n in sim_path.nodes]
        node_ids    = [n.node_id for n in sim_path.nodes]
        sel_node_label = st.selectbox(
            "Select node to disrupt", node_labels, key="vis_sim_node"
        )
        sel_node_idx = node_labels.index(sel_node_label)
        sel_node_id  = node_ids[sel_node_idx]

    with col_b:
        duration = st.slider(
            "Disruption duration (days)", min_value=1, max_value=30, value=7,
            key="vis_sim_duration",
        )

    result = simulate_disruption(sim_path.path_id, sel_node_id, duration)

    if "error" in result:
        st.warning("Simulation error: " + result["error"])
        return

    severity_color = {
        "CRITICAL": C_DANGER,
        "HIGH":     C_ORANGE,
        "MODERATE": C_WARN,
    }.get(result["severity"], C_TEXT2)

    m1, m2, m3, m4 = st.columns(4)
    metrics = [
        (m1, "Severity",         result["severity"],                              severity_color),
        (m2, "Extra Days",       "+" + str(result["additional_transit_days"]) + " days", C_DANGER),
        (m3, "Additional Cost",  "$" + "{:,.0f}".format(result["additional_cost_usd"]), C_WARN),
        (m4, "New Total Days",   str(result["new_total_transit_days"]) + " days", C_ACCENT),
    ]
    for col, label, value, color in metrics:
        with col:
            st.markdown(_metric_card(label, value, color), unsafe_allow_html=True)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # Ripple effect visualisation
    cascading = result.get("cascading_nodes", [])
    all_labels = [n.location_name.split(",")[0].split("(")[0].strip() for n in sim_path.nodes]

    fig = go.Figure()
    x_positions = list(range(len(sim_path.nodes)))

    for i, node in enumerate(sim_path.nodes):
        if i < sel_node_idx:
            color, size, opacity = C_HIGH, 16, 0.8
        elif i == sel_node_idx:
            color, size, opacity = C_DANGER, 26, 1.0
        else:
            ripple_scale = max(0.3, 1.0 - (i - sel_node_idx) * 0.20)
            color   = severity_color
            size    = int(22 * ripple_scale)
            opacity = ripple_scale * 0.9

        hover = (
            "<b>" + node.location_name + "</b><br>"
            + ("DISRUPTED" if i == sel_node_idx
               else "Cascading delay" if i > sel_node_idx
               else "Unaffected")
        )

        fig.add_trace(go.Scatter(
            x=[i], y=[0],
            mode="markers+text",
            marker=dict(
                size=size, color=color, opacity=opacity,
                line=dict(color="rgba(255,255,255,0.3)", width=1.5),
            ),
            text=[all_labels[i]],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            hovertemplate=hover + "<extra></extra>",
            showlegend=False,
        ))

    fig.add_trace(go.Scatter(
        x=x_positions, y=[0] * len(x_positions),
        mode="lines",
        line=dict(color="rgba(255,255,255,0.12)", width=2),
        showlegend=False, hoverinfo="skip",
    ))

    fig.add_annotation(
        x=sel_node_idx, y=0,
        text="DISRUPTED",
        showarrow=True, arrowhead=2,
        arrowcolor=C_DANGER, arrowwidth=2, ay=-45,
        font=dict(color=C_DANGER, size=10, family="Inter, sans-serif"),
    )

    fig.update_layout(
        template="plotly_dark",
        **_dark_layout(height=220),
        xaxis=dict(
            showticklabels=False, showgrid=False, zeroline=False,
            range=[-0.5, len(sim_path.nodes) - 0.5],
        ),
        yaxis=dict(
            showticklabels=False, showgrid=False, zeroline=False, range=[-0.5, 0.8],
        ),
        title=dict(
            text="Cascading Impact — " + result["disrupted_node"],
            font=dict(size=11, color=C_TEXT2),
            x=0.01,
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="vis_disruption_simulator")

    c_left, c_right = st.columns([1, 1])

    with c_left:
        alt_html = (
            "<div style='background:" + C_CARD + "; border:1px solid "
            + C_BORDER + "; border-radius:10px; padding:14px 16px'>"
            "<div style='font-size:0.75rem; font-weight:700; color:"
            + C_TEXT + "; margin-bottom:8px'>Alternative Routes</div>"
        )
        for alt in result["alternative_routes"]:
            alt_html += (
                "<div style='display:flex; align-items:center; margin-bottom:6px'>"
                "<span style='color:" + C_HIGH + "; font-size:0.85rem;"
                " margin-right:6px'>&#8594;</span>"
                "<span style='font-size:0.78rem; color:" + C_TEXT2 + "'>"
                + alt + "</span></div>"
            )
        alt_html += "</div>"
        st.markdown(alt_html, unsafe_allow_html=True)

    with c_right:
        rec_color = severity_color
        rec_html = (
            "<div style='background:rgba(" + (
                "239,68,68" if result["severity"] == "CRITICAL" else
                "249,115,22" if result["severity"] == "HIGH" else
                "245,158,11"
            ) + ",0.08); border:1px solid "
            + rec_color + "30; border-radius:10px; padding:14px 16px'>"
            "<div style='font-size:0.75rem; font-weight:700; color:"
            + C_TEXT + "; margin-bottom:6px'>Recommendation</div>"
            "<div style='font-size:0.78rem; color:" + C_TEXT2
            + "; line-height:1.55'>" + result["recommendation"] + "</div>"
            "</div>"
        )
        st.markdown(rec_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Existing Section — Resilience Score Cards
# ---------------------------------------------------------------------------

def _render_resilience_cards(paths: list[SupplyChainPath]) -> None:
    """One card per path: resilience score, alternatives, SPOFs, buffer stock."""
    cols = st.columns(len(paths))

    for idx, path in enumerate(paths):
        col = cols[idx]
        with col:
            res_score = path.resilience_score
            res_color = _risk_color(1.0 - res_score)
            res_pct   = int(res_score * 100)

            spofs = [n for n in path.nodes if n.status in ("DISRUPTED", "CLOSED")]
            spof_warnings = [n.location_name.split(",")[0].split("(")[0].strip() for n in spofs]
            alt_count  = max(0, int((res_score - 0.5) * 10))
            buffer_days = recommended_buffer_days(path)
            short_cat   = path.product_category.split("(")[0].strip()

            spof_html = ""
            if spof_warnings:
                for w in spof_warnings:
                    spof_html += (
                        "<div style='display:inline-block; background:"
                        + C_DANGER + "20; border:1px solid " + C_DANGER + "50;"
                        " border-radius:6px; padding:2px 8px; margin:2px;"
                        " font-size:0.65rem; color:" + C_DANGER + "; font-weight:700'>"
                        "&#9888; " + w + "</div>"
                    )
            else:
                spof_html = (
                    "<div style='font-size:0.68rem; color:" + C_HIGH
                    + "'>&#10003; No critical SPOFs</div>"
                )

            html = (
                "<div style='background:" + C_CARD + "; border:1px solid "
                + res_color + "40; border-radius:12px; padding:14px 16px'>"
                "<div style='font-size:0.78rem; font-weight:700; color:"
                + C_TEXT + "; margin-bottom:8px; border-bottom:1px solid "
                + C_BORDER + "; padding-bottom:6px'>" + short_cat + "</div>"
                "<div style='text-align:center; margin-bottom:8px'>"
                "<div style='font-size:2rem; font-weight:800; color:"
                + res_color + "'>" + str(res_pct) + "</div>"
                "<div style='font-size:0.65rem; text-transform:uppercase;"
                " letter-spacing:0.08em; color:" + C_TEXT3
                + "; margin-top:-2px'>Resilience Score</div>"
                "</div>"
                "<div style='margin-bottom:8px'>"
                + _score_bar_html(res_score, width_px=120)
                + "</div>"
                "<div style='display:flex; justify-content:space-between; margin-bottom:8px'>"
                "<div style='text-align:center'>"
                "<div style='font-size:1.0rem; font-weight:700; color:"
                + C_ACCENT + "'>" + str(alt_count) + "</div>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3 + "'>Alt Routes</div>"
                "</div>"
                "<div style='text-align:center'>"
                "<div style='font-size:1.0rem; font-weight:700; color:"
                + C_WARN + "'>" + str(path.total_transit_days) + "d</div>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3 + "'>Transit</div>"
                "</div>"
                "<div style='text-align:center'>"
                "<div style='font-size:1.0rem; font-weight:700; color:"
                + C_CYAN + "'>" + str(buffer_days) + "d</div>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3 + "'>Buffer Stock</div>"
                "</div>"
                "</div>"
                "<div style='margin-top:4px'>"
                "<div style='font-size:0.65rem; color:" + C_TEXT3
                + "; text-transform:uppercase; letter-spacing:0.06em;"
                " margin-bottom:3px'>SPOF Warnings</div>"
                + spof_html
                + "</div>"
                "</div>"
            )
            st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(port_results, route_results, trade_data, freight_data) -> None:
    """Render the Supply Chain Visibility tab.

    Parameters
    ----------
    port_results : list[PortDemandResult]
        Current port demand results (passed through from main app).
    route_results : list[RouteOpportunity]
        Current route opportunity objects (passed through from main app).
    trade_data : dict
        Trade flow data dict (passed through from main app).
    freight_data : dict
        Freight rate data dict (passed through from main app).
    """
    logger.info("Rendering Supply Chain Visibility tab")

    paths = EXAMPLE_PATHS

    # ── Page header ──────────────────────────────────────────────────────────
    # NOTE: Visibility Intelligence Scorecard is rendered immediately after the
    # page header as the enhanced top-of-tab summary strip (see below).
    st.markdown(
        "<div style='padding:16px 0 22px 0; border-bottom:1px solid rgba(255,255,255,0.06);"
        " margin-bottom:24px'>"
        "<div style='font-size:0.62rem; text-transform:uppercase; letter-spacing:0.16em;"
        " color:" + C_TEXT3 + "; margin-bottom:5px'>MODULE</div>"
        "<div style='font-size:1.55rem; font-weight:800; color:" + C_TEXT + ";"
        " letter-spacing:-0.01em'>Supply Chain Visibility</div>"
        "<div style='font-size:0.83rem; color:" + C_TEXT2 + "; margin-top:6px;"
        " max-width:720px; line-height:1.6'>"
        "End-to-end path tracking, data coverage intelligence, exception management, "
        "predictive ETAs, and resilience scoring across five major product categories."
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # ENHANCED SECTION — Visibility Intelligence Scorecard (top-of-tab strip)
    # Surfaces: visibility %, data coverage %, blind spots, quality bars, gaps
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _render_visibility_intelligence_scorecard(paths)
    except Exception as _exc_scorecard:
        logger.warning("Visibility intelligence scorecard error: {}", _exc_scorecard)
        st.warning("Visibility scorecard unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 0 — Visibility Score Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Visibility Score Dashboard",
        "Overall supply chain visibility score, data coverage percentage, and blind spot inventory.",
        icon="",
    )
    try:
        _render_visibility_score_dashboard(paths)
    except Exception as exc:
        logger.warning("Visibility score dashboard error: {}", exc)
        st.warning("Visibility score dashboard unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Shipment Tracking + Supply Chain Map + Timeline
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Supply Chain Path Map",
        "Select a product category to visualise the full end-to-end supply chain on a globe. "
        "Node shape = facility type; line colour = transport mode.",
        icon="",
    )

    path_labels    = [p.product_category for p in paths]
    selected_label = st.radio(
        "Product category",
        path_labels,
        horizontal=True,
        key="vis_path_selector",
        label_visibility="collapsed",
    )
    selected_path = next(p for p in paths if p.product_category == selected_label)

    _render_supply_chain_map(selected_path)

    # Legend row
    legend_items = [
        ("square",      "#ec4899", "Factory"),
        ("circle",      C_ACCENT,  "Port"),
        ("diamond",     C_WARN,    "Rail / Warehouse"),
        ("triangle-up", C_HIGH,    "Distribution"),
    ]
    legend_html = (
        "<div style='display:flex; gap:18px; flex-wrap:wrap; margin-bottom:14px;"
        " font-size:0.72rem; color:" + C_TEXT2 + "'>"
    )
    for _sym, col, label in legend_items:
        legend_html += (
            "<span><span style='display:inline-block; width:10px; height:10px;"
            " border-radius:2px; background:" + col + "; margin-right:4px'></span>"
            + label + "</span>"
        )
    legend_html += "</div>"
    st.markdown(legend_html, unsafe_allow_html=True)

    # ── Shipment Tracking Simulation ─────────────────────────────────────────
    _section_title(
        "Shipment Tracking — Journey Stages",
        "Animated stage-by-stage cargo journey view. "
        "Completed stages are green; active stage shows current position; future stages are dimmed.",
        icon="",
    )
    try:
        _render_shipment_tracking(selected_path)
    except Exception as exc:
        logger.warning("Shipment tracking error: {}", exc)
        st.warning("Shipment tracking unavailable.")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Journey Timeline ─────────────────────────────────────────────────────
    _section_title(
        "Journey Timeline",
        "Gantt-style view of the selected path. Colour = risk level for nodes; "
        "transport mode colour for transit segments. Dotted line = current position.",
        icon="",
    )
    _render_journey_timeline(selected_path)

    # Path stat row
    risk_c = _risk_color(selected_path.risk_score)
    stat_html = "<div style='display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px'>"
    stats = [
        ("Total Transit",   str(selected_path.total_transit_days) + " days",                C_ACCENT),
        ("Est. Cost / 40ft","$" + "{:,.0f}".format(selected_path.total_cost_usd),           C_WARN),
        ("Path Risk",       str(round(selected_path.risk_score * 100, 1)) + "%",            risk_c),
        ("Resilience",      str(int(selected_path.resilience_score * 100)) + "%",           C_HIGH),
        ("Buffer Rec.",     str(recommended_buffer_days(selected_path)) + " days",          C_CYAN),
    ]
    for label, value, color in stats:
        stat_html += (
            "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
            " border-radius:8px; padding:8px 14px; min-width:100px'>"
            "<div style='font-size:0.60rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
            " letter-spacing:0.06em'>" + label + "</div>"
            "<div style='font-size:0.95rem; font-weight:700; color:" + color + "'>"
            + value + "</div>"
            "</div>"
        )
    stat_html += "</div>"
    st.markdown(stat_html, unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Exception Management Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Exception Management",
        "Shipments at risk, delayed, or requiring immediate attention — sorted by severity.",
        icon="",
    )
    try:
        _render_exception_dashboard(paths)
    except Exception as exc:
        logger.warning("Exception dashboard error: {}", exc)
        st.warning("Exception dashboard unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Predictive ETAs
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Predictive ETAs — Early Warning",
        "Risk-adjusted delivery projections highlighting paths at risk of delay before they materialise.",
        icon="",
    )
    try:
        _render_predictive_etas(paths)
    except Exception as exc:
        logger.warning("Predictive ETAs error: {}", exc)
        st.warning("Predictive ETA engine unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Data Source Coverage Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Data Source Coverage Map",
        "Geographic distribution of data quality — which regions and nodes have strong vs poor coverage.",
        icon="",
    )
    try:
        _render_data_coverage_map(paths)
    except Exception as exc:
        logger.warning("Data coverage map error: {}", exc)
        st.warning("Data coverage map unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Supplier Origin Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Supplier Origin Geography",
        "Origin country distribution of supply chain flows — concentration risk and geographic diversification.",
        icon="",
    )
    try:
        _render_supplier_origin_map(paths, trade_data)
    except Exception as exc:
        logger.warning("Supplier origin map error: {}", exc)
        st.warning("Supplier origin map unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 6 — Bottleneck Analyser
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Bottleneck Analyser",
        "Nodes that appear across multiple supply chain paths are systemic single points of failure. "
        "A disruption at any of these cascades across multiple product categories.",
        icon="",
    )
    _render_bottleneck_analyser(paths)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 7 — Disruption Simulator
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Disruption Simulator",
        "Select a path and node to disrupt, set the duration. "
        "Cascading impacts, alternative routes, and additional cost/days are shown.",
        icon="",
    )
    _render_disruption_simulator(paths)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 8 — Data Quality Scorecard
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Data Quality Scorecard",
        "Completeness, timeliness, and accuracy ratings for each data source powering this module.",
        icon="",
    )
    try:
        _render_data_quality_scorecard(paths)
    except Exception as exc:
        logger.warning("Data quality scorecard error: {}", exc)
        st.warning("Data quality scorecard unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 9 — Coverage Gaps Analysis
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Coverage Gaps Analysis",
        "Nodes and corridors where additional data collection would most improve visibility quality.",
        icon="",
    )
    try:
        _render_coverage_gaps(paths)
    except Exception as exc:
        logger.warning("Coverage gaps error: {}", exc)
        st.warning("Coverage gaps analysis unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 10 — Resilience Score Cards
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Resilience Score Cards",
        "Per-path resilience overview: score, alternative route count, SPOF warnings, "
        "and recommended buffer stock days.",
        icon="",
    )

    _all_zero_resilience = all(p.resilience_score == 0.0 for p in paths)
    if _all_zero_resilience:
        st.warning(
            "All visibility scores are zero — no AIS or real-time tracking data "
            "has been received. Resilience scores shown are uncalibrated defaults. "
            "Connect a live AIS feed to enable accurate path tracking.",
        )

    _render_resilience_cards(paths)
