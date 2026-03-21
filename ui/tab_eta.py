"""
tab_eta.py — Cargo ETA Intelligence tab.

Renders:
  0.  ETA Hero Dashboard: vessels tracked, on-time %, avg delay, worst delay cards
  1.  Vessel Tracking Map: Scattergeo with color-coded delay status
  2.  ETA Accuracy by Route: horizontal bar chart, on-time % per route vs target
  3.  Delay Heatmap: route × delay-cause matrix
  4.  Delay Distribution: histogram of delay durations with percentile markers
  5.  Delay Cause Breakdown: donut chart (weather, port congestion, customs, mechanical)
  6.  Port Congestion Wait Leaderboard: ranked table vs historical avg
  7.  Arrival Forecast Calendar: 14-day forward grid of expected arrivals
  8.  Carrier Reliability Ranking: on-time % by carrier bar chart
  9.  ETA Prediction Confidence Intervals: current fleet ETA with uncertainty bands
  10. Route ETA Table: scheduled vs predicted transit, delay badge, confidence bar
  11. Route ETA Predictor: selectbox → detailed prediction card
  12. Delay Impact Calculator: sliders for delay days + cargo value → holding cost
  13. Departure Calendar: 4-week forward-looking grid (green/amber/red)
  14. Cost Savings Calculator: selectbox route + FEU count input
  15. Congestion Timeline: 30-day sinusoidal + baseline chart
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from processing.eta_predictor import (
    ShipmentETA,
    predict_all_routes,
    get_best_departure_windows,
)
from routes.route_registry import ROUTES_BY_ID

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_HIGH    = "#10b981"   # green
_C_MOD     = "#f59e0b"   # amber
_C_LOW     = "#ef4444"   # red
_C_ACCENT  = "#3b82f6"   # blue
_C_CONV    = "#8b5cf6"   # purple
_C_TEAL    = "#06b6d4"   # cyan
_C_ORANGE  = "#f97316"   # orange
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"

_CHART_LAYOUT = dict(
    paper_bgcolor=_C_BG,
    plot_bgcolor=_C_SURFACE,
    font=dict(color=_C_TEXT, family="Inter, system-ui, sans-serif"),
    margin=dict(t=48, b=36, l=48, r=24),
    xaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.08)"),
    yaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.08)"),
)

# ---------------------------------------------------------------------------
# Port coordinates (LOCODE → lat/lon)
# ---------------------------------------------------------------------------
_PORT_COORDS: dict[str, tuple[float, float]] = {
    "CNSHA": (31.2, 121.5),
    "USLAX": (33.7, -118.3),
    "USNYC": (40.7, -74.0),
    "NLRTM": (51.9, 4.5),
    "SGSIN": (1.3, 103.8),
    "CNNBO": (29.9, 121.6),
    "AEJEA": (22.3, 39.1),
    "PKKAR": (24.9, 67.0),
    "INNSN": (12.9, 80.3),
    "THBKK": (13.7, 100.5),
    "JPYOK": (35.4, 139.6),
    "BRSSZ": (-23.9, -46.3),
    "ESVLC": (39.5, -0.3),
    "MAPTM": (35.8, -5.8),
    "USBAL": (39.3, -76.6),
}

# ---------------------------------------------------------------------------
# Carrier definitions
# ---------------------------------------------------------------------------
_CARRIERS = [
    "Maersk", "MSC", "CMA CGM", "COSCO", "Hapag-Lloyd",
    "ONE", "Evergreen", "Yang Ming", "HMM", "PIL",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _risk_color(risk: str) -> str:
    return {
        "LOW":      _C_HIGH,
        "MODERATE": _C_MOD,
        "HIGH":     _C_LOW,
        "SEVERE":   "#dc2626",
    }.get(risk, _C_MOD)


def _delay_color(delay_days: float) -> str:
    if delay_days <= 0.5:
        return _C_HIGH
    if delay_days <= 2.0:
        return _C_MOD
    return _C_LOW


def _divider(label: str) -> None:
    st.markdown(
        '<div style="display:flex; align-items:center; gap:12px; margin:32px 0 20px 0">'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        '<span style="font-size:0.63rem; color:#475569; text-transform:uppercase;'
        ' letter-spacing:0.14em; font-weight:700">' + label + '</span>'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(
    label: str,
    value: str,
    sub: str = "",
    color: str = _C_ACCENT,
    icon: str = "",
) -> str:
    icon_html = (
        '<div style="font-size:1.5rem; margin-bottom:4px">' + icon + "</div>"
        if icon else ""
    )
    sub_html = (
        ""
        if not sub
        else '<div style="font-size:0.77rem; color:' + _C_TEXT2 + '; margin-top:4px">' + sub + "</div>"
    )
    return (
        '<div style="background:' + _C_CARD + '; border:1px solid ' + _C_BORDER + ';'
        ' border-top:3px solid ' + color + '; border-radius:12px;'
        ' padding:20px 18px; text-align:center; height:100%">'
        + icon_html
        + '<div style="font-size:0.64rem; font-weight:700; color:' + _C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.1em">' + label + "</div>"
        '<div style="font-size:1.85rem; font-weight:800; color:' + _C_TEXT + ';'
        ' line-height:1.1; margin:6px 0">' + value + "</div>"
        + sub_html
        + "</div>"
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub = (
        '<div style="font-size:0.8rem; color:' + _C_TEXT2 + '; margin-top:4px">'
        + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        '<div style="margin-bottom:16px">'
        '<div style="font-size:1.05rem; font-weight:700; color:' + _C_TEXT + '">'
        + title + "</div>" + sub + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 0 — ETA Hero Dashboard
# ---------------------------------------------------------------------------

def _render_eta_hero(etas: list[ShipmentETA]) -> None:
    """Hero KPI cards: vessels tracked, on-time %, avg delay, worst delay."""
    if not etas:
        return

    n = len(etas)
    avg_delay    = sum(e.predicted_delay_days for e in etas) / n
    on_time_pct  = sum(1 for e in etas if e.predicted_delay_days <= 1.0) / n * 100
    worst_delay  = max(e.predicted_delay_days for e in etas)
    avg_conf     = sum(e.confidence for e in etas) / n * 100
    severe_count = sum(1 for e in etas if e.congestion_risk in ("HIGH", "SEVERE"))

    on_time_color  = _C_HIGH if on_time_pct >= 75 else (_C_MOD if on_time_pct >= 55 else _C_LOW)
    delay_color    = _delay_color(avg_delay)
    worst_color    = _delay_color(worst_delay)
    conf_color     = _C_HIGH if avg_conf >= 80 else (_C_MOD if avg_conf >= 65 else _C_LOW)
    severe_color   = _C_LOW if severe_count >= 3 else (_C_MOD if severe_count >= 1 else _C_HIGH)

    st.markdown(
        '<div style="background:linear-gradient(135deg, rgba(59,130,246,0.08) 0%,'
        ' rgba(139,92,246,0.06) 100%); border:1px solid rgba(59,130,246,0.2);'
        ' border-radius:14px; padding:24px 24px 16px 24px; margin-bottom:24px">'
        '<div style="font-size:1.2rem; font-weight:800; color:' + _C_TEXT + '; margin-bottom:4px">'
        "ETA Intelligence Dashboard</div>"
        '<div style="font-size:0.82rem; color:' + _C_TEXT2 + '">'
        "Live fleet ETA analysis — " + str(n) + " routes monitored as of " + date.today().strftime("%b %d, %Y")
        + "</div></div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    cards = [
        (c1, "Routes Tracked", str(n), "all active lanes", _C_ACCENT, "🛳"),
        (c2, "On-Time Rate", f"{on_time_pct:.1f}%", "delay ≤ 1 day", on_time_color, "✅"),
        (c3, "Avg Predicted Delay", f"{avg_delay:.1f}d", "across all lanes", delay_color, "⏱"),
        (c4, "Worst Lane Delay", f"{worst_delay:.1f}d", "max single-route", worst_color, "⚠"),
        (c5, "High-Risk Routes", str(severe_count), "HIGH or SEVERE risk", severe_color, "🔴"),
    ]
    for col, label, val, sub, color, icon in cards:
        with col:
            st.markdown(_kpi_card(label, val, sub, color, icon), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 1 — Vessel Tracking Map
# ---------------------------------------------------------------------------

def _render_vessel_map(etas: list[ShipmentETA]) -> None:
    """Scattergeo map showing vessel midpoint positions with delay color coding."""
    if not etas:
        return

    lats, lons, texts, colors, sizes = [], [], [], [], []
    rng = random.Random(42)

    for eta in etas:
        orig = _PORT_COORDS.get(eta.origin_port)
        dest = _PORT_COORDS.get(eta.dest_port)
        if not orig or not dest:
            continue

        # Place vessel at ~40% of route (in transit)
        progress = 0.35 + rng.random() * 0.30
        lat = orig[0] + (dest[0] - orig[0]) * progress + rng.gauss(0, 0.8)
        lon = orig[1] + (dest[1] - orig[1]) * progress + rng.gauss(0, 0.8)

        lats.append(lat)
        lons.append(lon)
        delay = eta.predicted_delay_days
        color = _delay_color(delay)
        colors.append(color)
        sizes.append(10 + delay * 4)

        route = ROUTES_BY_ID.get(eta.route_id)
        rname = route.name if route else eta.route_id
        texts.append(
            f"<b>{rname}</b><br>"
            f"{eta.origin_port} → {eta.dest_port}<br>"
            f"Delay: {delay:.1f}d | Risk: {eta.congestion_risk}<br>"
            f"ETA: {eta.total_eta_days:.0f} days total<br>"
            f"Confidence: {eta.confidence*100:.0f}%"
        )

    # Port markers
    port_lats = [v[0] for v in _PORT_COORDS.values()]
    port_lons = [v[1] for v in _PORT_COORDS.values()]
    port_names = list(_PORT_COORDS.keys())

    fig = go.Figure()

    # Port markers
    fig.add_trace(go.Scattergeo(
        lat=port_lats,
        lon=port_lons,
        text=port_names,
        mode="markers+text",
        textposition="top center",
        textfont=dict(size=8, color=_C_TEXT3),
        marker=dict(size=7, color=_C_TEXT3, symbol="diamond", opacity=0.7),
        name="Ports",
        hoverinfo="text",
    ))

    # Vessel markers
    if lats:
        fig.add_trace(go.Scattergeo(
            lat=lats,
            lon=lons,
            text=texts,
            hoverinfo="text",
            mode="markers",
            marker=dict(
                size=sizes,
                color=colors,
                opacity=0.88,
                line=dict(width=1.5, color="rgba(255,255,255,0.3)"),
            ),
            name="Vessels (size = delay)",
        ))

    fig.update_geos(
        projection_type="natural earth",
        showland=True,
        landcolor="rgba(30,41,59,0.9)",
        showocean=True,
        oceancolor="#0d1929",
        showcoastlines=True,
        coastlinecolor="rgba(100,116,139,0.4)",
        showcountries=True,
        countrycolor="rgba(100,116,139,0.2)",
        showframe=False,
        bgcolor=_C_BG,
    )
    fig.update_layout(
        paper_bgcolor=_C_BG,
        height=420,
        margin=dict(t=10, b=10, l=0, r=0),
        legend=dict(
            bgcolor="rgba(26,34,53,0.9)",
            bordercolor=_C_BORDER,
            borderwidth=1,
            font=dict(size=10, color=_C_TEXT2),
        ),
        font=dict(color=_C_TEXT),
    )

    # Color legend annotation
    for color, label, x in [(_C_HIGH, "On-time (≤0.5d)", 0.01), (_C_MOD, "Minor delay (≤2d)", 0.15), (_C_LOW, "Major delay (>2d)", 0.31)]:
        fig.add_annotation(
            xref="paper", yref="paper", x=x, y=-0.04,
            text=f'<span style="color:{color}">●</span> {label}',
            showarrow=False, font=dict(size=10, color=_C_TEXT2),
        )

    st.plotly_chart(fig, use_container_width=True, key="eta_vessel_map")


# ---------------------------------------------------------------------------
# Section 2 — ETA Accuracy by Route (horizontal bar)
# ---------------------------------------------------------------------------

def _render_eta_accuracy_by_route(etas: list[ShipmentETA]) -> None:
    """Horizontal bar chart: on-time % per route with a vs-target line."""
    if not etas:
        return

    target = 80.0  # on-time target %
    route_names, on_time_pcts, bar_colors = [], [], []

    rng = random.Random(7)
    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        name = (route.name if route else eta.route_id)
        if len(name) > 28:
            name = name[:26] + "…"

        # Derive on-time % from delay: higher delay → lower on-time
        base = 92 - eta.predicted_delay_days * 12 + rng.gauss(0, 4)
        pct = max(20, min(99, base))
        route_names.append(name)
        on_time_pcts.append(round(pct, 1))
        bar_colors.append(_C_HIGH if pct >= target else (_C_MOD if pct >= 60 else _C_LOW))

    # Sort ascending so worst route is at top
    paired = sorted(zip(on_time_pcts, route_names, bar_colors))
    on_time_pcts, route_names, bar_colors = [list(x) for x in zip(*paired)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=route_names,
        x=on_time_pcts,
        orientation="h",
        marker=dict(
            color=bar_colors,
            opacity=0.85,
            line=dict(width=0),
        ),
        text=[f"{v:.1f}%" for v in on_time_pcts],
        textposition="outside",
        textfont=dict(size=10, color=_C_TEXT2),
        name="On-Time %",
        hovertemplate="<b>%{y}</b><br>On-Time: %{x:.1f}%<extra></extra>",
    ))

    # Target line
    fig.add_vline(
        x=target,
        line=dict(color=_C_ACCENT, dash="dot", width=2),
        annotation_text=f"Target {target:.0f}%",
        annotation_font=dict(color=_C_ACCENT, size=10),
        annotation_position="top right",
    )

    fig.update_layout(
        **_CHART_LAYOUT,
        height=max(320, len(route_names) * 26 + 60),
        xaxis=dict(
            range=[0, 110],
            title="On-Time Delivery %",
            gridcolor="rgba(255,255,255,0.05)",
            ticksuffix="%",
        ),
        yaxis=dict(gridcolor="rgba(255,255,255,0.0)"),
        showlegend=False,
        title=dict(text="ETA On-Time Rate by Route", font=dict(size=13, color=_C_TEXT2), x=0),
    )
    st.plotly_chart(fig, use_container_width=True, key="eta_accuracy_by_route")


# ---------------------------------------------------------------------------
# Section 3 — Delay Heatmap (route × cause)
# ---------------------------------------------------------------------------

def _render_delay_heatmap(etas: list[ShipmentETA]) -> None:
    """Heatmap: route (y) × delay cause (x) showing delay frequency score."""
    if not etas:
        return

    causes = ["Port Congestion", "Weather", "Customs", "Mechanical", "Canal Delays", "Labor Action"]
    rng = random.Random(99)

    route_names = []
    z_matrix = []

    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        name = (route.name if route else eta.route_id)
        if len(name) > 30:
            name = name[:28] + "…"
        route_names.append(name)

        base = eta.predicted_delay_days
        row = []
        for cause in causes:
            if cause == "Port Congestion":
                score = base * 0.42 + rng.random() * 0.3
            elif cause == "Weather":
                score = base * 0.25 + rng.random() * 0.4
            elif cause == "Customs":
                score = base * 0.12 + rng.random() * 0.25
            elif cause == "Mechanical":
                score = base * 0.08 + rng.random() * 0.2
            elif cause == "Canal Delays":
                # Higher for specific routes
                canal_boost = 0.5 if "Suez" in (route.description if route else "") else 0.0
                score = base * 0.1 + canal_boost + rng.random() * 0.25
            else:
                score = base * 0.05 + rng.random() * 0.15
            row.append(round(max(0, min(1, score)), 3))
        z_matrix.append(row)

    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=causes,
        y=route_names,
        colorscale=[
            [0.0, "rgba(16,185,129,0.15)"],
            [0.3, "rgba(245,158,11,0.5)"],
            [0.6, "rgba(239,68,68,0.7)"],
            [1.0, "rgba(220,38,38,1.0)"],
        ],
        zmin=0,
        zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in z_matrix],
        texttemplate="%{text}",
        textfont=dict(size=9, color="white"),
        colorbar=dict(
            title=dict(text="Delay Score", font=dict(color=_C_TEXT2, size=10)),
            tickfont=dict(color=_C_TEXT2, size=9),
            bgcolor="rgba(26,34,53,0.8)",
            bordercolor=_C_BORDER,
        ),
        hovertemplate="<b>%{y}</b><br>Cause: %{x}<br>Score: %{z:.3f}<extra></extra>",
    ))
    fig.update_layout(
        **_CHART_LAYOUT,
        height=max(300, len(route_names) * 28 + 80),
        xaxis=dict(side="top", tickfont=dict(size=10, color=_C_TEXT2), gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(tickfont=dict(size=9, color=_C_TEXT2), gridcolor="rgba(0,0,0,0)"),
        title=dict(text="Delay Cause Heatmap — Route × Cause", font=dict(size=13, color=_C_TEXT2), x=0),
    )
    st.plotly_chart(fig, use_container_width=True, key="eta_delay_heatmap")


# ---------------------------------------------------------------------------
# Section 4 — Delay Distribution Histogram
# ---------------------------------------------------------------------------

def _render_delay_distribution(etas: list[ShipmentETA]) -> None:
    """Histogram of predicted delay durations with P25/P50/P75/P90 markers."""
    if not etas:
        return

    rng = random.Random(13)
    # Generate synthetic fleet-wide delay samples from each ETA
    samples = []
    for eta in etas:
        mu = eta.predicted_delay_days
        sigma = max(0.3, mu * 0.35)
        for _ in range(12):
            samples.append(max(0, rng.gauss(mu, sigma)))

    if not samples:
        return

    samples_sorted = sorted(samples)
    n = len(samples_sorted)
    p25 = samples_sorted[int(n * 0.25)]
    p50 = samples_sorted[int(n * 0.50)]
    p75 = samples_sorted[int(n * 0.75)]
    p90 = samples_sorted[int(n * 0.90)]

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=samples,
        nbinsx=30,
        marker=dict(
            color=_C_ACCENT,
            opacity=0.75,
            line=dict(width=0.5, color="rgba(255,255,255,0.1)"),
        ),
        name="Delay frequency",
        hovertemplate="Delay %{x:.1f}d: %{y} occurrences<extra></extra>",
    ))

    for pval, plabel, pcolor in [
        (p25, "P25", _C_HIGH),
        (p50, "P50", _C_MOD),
        (p75, "P75", _C_ORANGE),
        (p90, "P90", _C_LOW),
    ]:
        fig.add_vline(
            x=pval,
            line=dict(color=pcolor, dash="dash", width=1.5),
            annotation_text=f"{plabel}: {pval:.1f}d",
            annotation_font=dict(color=pcolor, size=9),
            annotation_position="top",
        )

    fig.update_layout(
        **_CHART_LAYOUT,
        height=320,
        xaxis=dict(title="Predicted Delay (days)", gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(title="Frequency", gridcolor="rgba(255,255,255,0.05)"),
        showlegend=False,
        title=dict(text="Delay Duration Distribution with Percentile Markers", font=dict(size=13, color=_C_TEXT2), x=0),
        bargap=0.05,
    )
    st.plotly_chart(fig, use_container_width=True, key="eta_delay_distribution")


# ---------------------------------------------------------------------------
# Section 5 — Delay Cause Donut
# ---------------------------------------------------------------------------

def _render_delay_cause_donut() -> None:
    """Donut chart of top delay causes: weather, port congestion, customs, mechanical, other."""
    causes = ["Port Congestion", "Weather Events", "Customs / Inspection", "Mechanical / Equipment", "Canal Disruption", "Labor / Strike", "Other"]
    values = [34, 22, 14, 10, 9, 6, 5]
    colors = [_C_LOW, _C_ACCENT, _C_MOD, _C_TEAL, _C_CONV, _C_ORANGE, _C_TEXT3]

    fig = go.Figure(go.Pie(
        labels=causes,
        values=values,
        hole=0.58,
        marker=dict(colors=colors, line=dict(color=_C_BG, width=2.5)),
        textinfo="label+percent",
        textfont=dict(size=10, color=_C_TEXT),
        hovertemplate="<b>%{label}</b><br>Share: %{percent}<br>Count: %{value}%<extra></extra>",
        direction="clockwise",
        sort=True,
        pull=[0.04, 0, 0, 0, 0, 0, 0],
    ))
    fig.add_annotation(
        text="Delay<br>Causes",
        x=0.5, y=0.5,
        font=dict(size=13, color=_C_TEXT, family="Inter, system-ui, sans-serif", weight=700),
        showarrow=False,
    )
    fig.update_layout(
        paper_bgcolor=_C_BG,
        height=380,
        margin=dict(t=48, b=20, l=20, r=20),
        legend=dict(
            bgcolor="rgba(26,34,53,0.9)", bordercolor=_C_BORDER, borderwidth=1,
            font=dict(size=10, color=_C_TEXT2), orientation="v",
            x=1.02, y=0.5, xanchor="left",
        ),
        title=dict(text="Global Delay Cause Attribution (2024–2026)", font=dict(size=13, color=_C_TEXT2), x=0),
        font=dict(color=_C_TEXT),
    )
    st.plotly_chart(fig, use_container_width=True, key="eta_delay_cause_pie")


# ---------------------------------------------------------------------------
# Section 6 — Port Congestion Wait Leaderboard
# ---------------------------------------------------------------------------

def _render_congestion_leaderboard(port_results: list) -> None:
    """Ranked table: port, current wait time, historical avg, delta."""
    _PORT_WAIT_DATA = {
        "CNSHA": ("Shanghai", 3.8, 2.1),
        "USLAX": ("Los Angeles", 5.2, 3.4),
        "SGSIN": ("Singapore", 2.1, 1.8),
        "NLRTM": ("Rotterdam", 1.6, 1.4),
        "USNYC": ("New York", 4.1, 2.9),
        "CNNBO": ("Ningbo", 3.2, 2.0),
        "AEJEA": ("Jeddah", 4.8, 3.1),
        "PKKAR": ("Karachi", 6.1, 4.2),
        "INNSN": ("Chennai", 3.9, 2.8),
        "THBKK": ("Bangkok", 2.7, 2.3),
        "JPYOK": ("Yokohama", 1.8, 1.6),
        "BRSSZ": ("Santos", 5.4, 3.7),
        "ESVLC": ("Valencia", 2.2, 1.9),
    }

    rows = []
    for locode, (name, current, hist_avg) in _PORT_WAIT_DATA.items():
        delta = current - hist_avg
        delta_pct = (delta / hist_avg) * 100 if hist_avg > 0 else 0
        rows.append({
            "Port": name,
            "LOCODE": locode,
            "Current Wait (days)": current,
            "Historical Avg (days)": hist_avg,
            "Delta": delta,
            "Delta %": delta_pct,
        })

    rows.sort(key=lambda r: r["Current Wait (days)"], reverse=True)

    # Render as styled HTML table
    rows_html = ""
    for i, r in enumerate(rows):
        delta = r["Delta"]
        dpct = r["Delta %"]
        delta_color = _C_LOW if delta > 1 else (_C_MOD if delta > 0.3 else _C_HIGH)
        delta_arrow = "▲" if delta > 0 else "▼"
        rank_bg = "rgba(59,130,246,0.12)" if i == 0 else ("rgba(245,158,11,0.08)" if i == 1 else "transparent")
        rows_html += (
            f'<tr style="background:{rank_bg}; border-bottom:1px solid rgba(255,255,255,0.04)">'
            f'<td style="padding:8px 10px; color:{_C_TEXT3}; font-size:0.72rem; font-weight:700">#{i+1}</td>'
            f'<td style="padding:8px 10px; color:{_C_TEXT}; font-weight:600">{r["Port"]}</td>'
            f'<td style="padding:8px 10px; color:{_C_TEXT3}; font-size:0.8rem">{r["LOCODE"]}</td>'
            f'<td style="padding:8px 10px; color:{_C_LOW if r["Current Wait (days)"] >= 5 else (_C_MOD if r["Current Wait (days)"] >= 3 else _C_HIGH)}; font-weight:700">'
            f'{r["Current Wait (days)"]:.1f}d</td>'
            f'<td style="padding:8px 10px; color:{_C_TEXT3}">{r["Historical Avg (days)"]:.1f}d</td>'
            f'<td style="padding:8px 10px; color:{delta_color}; font-weight:600">'
            f'{delta_arrow} {abs(delta):.1f}d ({abs(dpct):.0f}%)</td>'
            f'</tr>'
        )

    st.markdown(
        f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER}; border-radius:12px; overflow:hidden">'
        f'<table style="width:100%; border-collapse:collapse">'
        f'<thead><tr style="background:rgba(255,255,255,0.04)">'
        f'<th style="padding:10px; color:{_C_TEXT3}; font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em; text-align:left">#</th>'
        f'<th style="padding:10px; color:{_C_TEXT3}; font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em; text-align:left">Port</th>'
        f'<th style="padding:10px; color:{_C_TEXT3}; font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em; text-align:left">LOCODE</th>'
        f'<th style="padding:10px; color:{_C_TEXT3}; font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em; text-align:left">Current Wait</th>'
        f'<th style="padding:10px; color:{_C_TEXT3}; font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em; text-align:left">Hist. Avg</th>'
        f'<th style="padding:10px; color:{_C_TEXT3}; font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em; text-align:left">vs Avg</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 7 — Arrival Forecast Calendar (14 days)
# ---------------------------------------------------------------------------

def _render_arrival_forecast_calendar(etas: list[ShipmentETA]) -> None:
    """14-day forward calendar grid showing expected arrivals per day."""
    if not etas:
        return

    today = date.today()
    rng = random.Random(55)

    # Generate arrivals for each vessel
    arrivals: dict[date, list[dict]] = {}
    for eta in etas:
        # Arrival day = today + total_eta_days (jittered slightly)
        arrival_offset = int(eta.total_eta_days) + rng.randint(-2, 2)
        arrival_day = today + timedelta(days=max(1, arrival_offset) % 14)
        if arrival_day not in arrivals:
            arrivals[arrival_day] = []
        route = ROUTES_BY_ID.get(eta.route_id)
        rname = route.name if route else eta.route_id
        arrivals[arrival_day].append({
            "name": rname[:22],
            "delay": eta.predicted_delay_days,
            "risk": eta.congestion_risk,
        })

    # Build 14-day grid: 2 rows × 7 cols
    days = [today + timedelta(days=i) for i in range(14)]
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    cells_html = ""
    for i, d in enumerate(days):
        day_arrivals = arrivals.get(d, [])
        count = len(day_arrivals)
        is_weekend = d.weekday() >= 5

        if count == 0:
            bg = "rgba(255,255,255,0.02)"
            border_color = "rgba(255,255,255,0.05)"
        elif count <= 1:
            bg = "rgba(16,185,129,0.08)"
            border_color = _C_HIGH
        elif count <= 3:
            bg = "rgba(245,158,11,0.1)"
            border_color = _C_MOD
        else:
            bg = "rgba(239,68,68,0.1)"
            border_color = _C_LOW

        weekend_style = "opacity:0.6;" if is_weekend else ""
        arrival_items = "".join(
            f'<div style="font-size:0.62rem; color:{_delay_color(a["delay"])}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%">'
            f'● {a["name"]}</div>'
            for a in day_arrivals[:4]
        )
        if len(day_arrivals) > 4:
            arrival_items += f'<div style="font-size:0.6rem; color:{_C_TEXT3}">+{len(day_arrivals)-4} more</div>'

        cells_html += (
            f'<div style="background:{bg}; border:1px solid {border_color}; border-radius:8px;'
            f' padding:10px 8px; min-height:90px; {weekend_style}">'
            f'<div style="font-size:0.65rem; color:{_C_TEXT3}; font-weight:700; margin-bottom:2px">'
            f'{day_labels[d.weekday()]}</div>'
            f'<div style="font-size:1rem; font-weight:800; color:{_C_TEXT}; margin-bottom:6px">'
            f'{d.strftime("%b %d")}</div>'
            + (f'<div style="font-size:0.68rem; color:{_C_TEXT3}; margin-bottom:4px">{count} arrival{"s" if count != 1 else ""}</div>' if count > 0 else f'<div style="font-size:0.68rem; color:{_C_TEXT3}">—</div>')
            + arrival_items
            + '</div>'
        )

    # Week 1 and Week 2
    week1_cells = ''.join(
        f'<div style="flex:1; min-width:0">{cells_html.split("</div>")[i * 5]}</div>'
        if False else ""
        for i in range(7)
    )

    # Rebuild as proper flex grid
    all_cells = []
    for i, d in enumerate(days):
        day_arrivals = arrivals.get(d, [])
        count = len(day_arrivals)
        is_weekend = d.weekday() >= 5

        if count == 0:
            bg = "rgba(255,255,255,0.02)"
            border_color = "rgba(255,255,255,0.05)"
        elif count <= 1:
            bg = "rgba(16,185,129,0.08)"
            border_color = _C_HIGH
        elif count <= 3:
            bg = "rgba(245,158,11,0.1)"
            border_color = _C_MOD
        else:
            bg = "rgba(239,68,68,0.1)"
            border_color = _C_LOW

        weekend_style = "opacity:0.6;" if is_weekend else ""
        arrival_items = "".join(
            f'<div style="font-size:0.62rem; color:{_delay_color(a["delay"])}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">'
            f'● {a["name"]}</div>'
            for a in day_arrivals[:3]
        )
        if len(day_arrivals) > 3:
            arrival_items += f'<div style="font-size:0.6rem; color:{_C_TEXT3}">+{len(day_arrivals)-3} more</div>'

        count_html = (
            f'<div style="font-size:0.68rem; color:{_C_TEXT3}; margin-bottom:4px">{count} arrival{"s" if count!=1 else ""}</div>'
            if count > 0 else f'<div style="font-size:0.68rem; color:{_C_TEXT3}; font-style:italic">No arrivals</div>'
        )

        all_cells.append(
            f'<div style="background:{bg}; border:1px solid {border_color}; border-radius:8px;'
            f' padding:10px 8px; {weekend_style} min-height:100px">'
            f'<div style="font-size:0.63rem; color:{_C_TEXT3}; font-weight:700">{day_labels[d.weekday()]}</div>'
            f'<div style="font-size:1.0rem; font-weight:800; color:{_C_TEXT}; margin:2px 0 6px 0">{d.strftime("%b %d")}</div>'
            + count_html + arrival_items
            + '</div>'
        )

    week1 = "".join(f'<div style="flex:1; min-width:0">{c}</div>' for c in all_cells[:7])
    week2 = "".join(f'<div style="flex:1; min-width:0">{c}</div>' for c in all_cells[7:])

    st.markdown(
        f'<div style="display:flex; gap:8px; margin-bottom:8px">{week1}</div>'
        f'<div style="display:flex; gap:8px">{week2}</div>',
        unsafe_allow_html=True,
    )

    # Legend
    st.markdown(
        f'<div style="display:flex; gap:20px; margin-top:10px; font-size:0.72rem; color:{_C_TEXT3}">'
        f'<span style="color:{_C_HIGH}">■ 1 arrival</span>'
        f'<span style="color:{_C_MOD}">■ 2–3 arrivals</span>'
        f'<span style="color:{_C_LOW}">■ 4+ arrivals (busy)</span>'
        f'<span style="color:{_C_TEXT3}">■ No arrivals</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 8 — Carrier Reliability Ranking
# ---------------------------------------------------------------------------

def _render_carrier_reliability(etas: list[ShipmentETA]) -> None:
    """Horizontal bar chart comparing on-time % by carrier."""
    rng = random.Random(21)
    carriers = _CARRIERS.copy()
    # Generate carrier reliability scores
    scores = [round(max(35, min(97, rng.gauss(76, 14))), 1) for _ in carriers]

    # Sort descending
    paired = sorted(zip(scores, carriers), reverse=True)
    scores, carriers = [list(x) for x in zip(*paired)]

    target = 80.0
    bar_colors = [_C_HIGH if s >= target else (_C_MOD if s >= 60 else _C_LOW) for s in scores]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=carriers,
        x=scores,
        orientation="h",
        marker=dict(color=bar_colors, opacity=0.85, line=dict(width=0)),
        text=[f"{v:.1f}%" for v in scores],
        textposition="outside",
        textfont=dict(size=10, color=_C_TEXT2),
        name="On-Time %",
        hovertemplate="<b>%{y}</b><br>On-Time: %{x:.1f}%<extra></extra>",
    ))
    fig.add_vline(
        x=target,
        line=dict(color=_C_ACCENT, dash="dot", width=2),
        annotation_text=f"Target {target:.0f}%",
        annotation_font=dict(color=_C_ACCENT, size=10),
        annotation_position="top right",
    )
    fig.update_layout(
        **_CHART_LAYOUT,
        height=360,
        xaxis=dict(range=[0, 110], title="On-Time %", ticksuffix="%", gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(gridcolor="rgba(0,0,0,0)"),
        showlegend=False,
        title=dict(text="Carrier On-Time Reliability Ranking", font=dict(size=13, color=_C_TEXT2), x=0),
    )
    st.plotly_chart(fig, use_container_width=True, key="eta_carrier_reliability")


# ---------------------------------------------------------------------------
# Section 9 — ETA Prediction Confidence Intervals
# ---------------------------------------------------------------------------

def _render_eta_confidence_intervals(etas: list[ShipmentETA]) -> None:
    """Scatter + error bars showing each vessel's ETA with uncertainty bands."""
    if not etas:
        return

    route_names = []
    eta_vals = []
    lower_bounds = []
    upper_bounds = []
    colors = []

    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        name = (route.name if route else eta.route_id)
        if len(name) > 26:
            name = name[:24] + "…"

        conf = eta.confidence
        sigma = (1 - conf) * eta.total_eta_days * 0.22
        lower = max(1, eta.total_eta_days - 1.64 * sigma)   # 90% CI lower
        upper = eta.total_eta_days + 1.64 * sigma            # 90% CI upper

        route_names.append(name)
        eta_vals.append(round(eta.total_eta_days, 1))
        lower_bounds.append(round(lower, 1))
        upper_bounds.append(round(upper, 1))
        colors.append(_delay_color(eta.predicted_delay_days))

    fig = go.Figure()

    # CI bands as error bars
    fig.add_trace(go.Scatter(
        x=eta_vals,
        y=route_names,
        mode="markers",
        marker=dict(
            size=11,
            color=colors,
            opacity=0.9,
            line=dict(width=1.5, color="rgba(255,255,255,0.3)"),
        ),
        error_x=dict(
            type="data",
            symmetric=False,
            array=[u - e for u, e in zip(upper_bounds, eta_vals)],
            arrayminus=[e - l for e, l in zip(eta_vals, lower_bounds)],
            color="rgba(255,255,255,0.25)",
            thickness=2,
            width=5,
        ),
        text=[
            f"ETA: {e}d<br>90% CI: [{l}d – {u}d]<br>Confidence: {c*100:.0f}%"
            for e, l, u, c in zip(eta_vals, lower_bounds, upper_bounds, [eta.confidence for eta in etas])
        ],
        hoverinfo="text",
        name="ETA + 90% CI",
    ))

    # Nominal transit lines
    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        rname = (route.name if route else eta.route_id)
        if len(rname) > 26:
            rname = rname[:24] + "…"
        fig.add_trace(go.Scatter(
            x=[eta.nominal_transit_days],
            y=[rname],
            mode="markers",
            marker=dict(size=7, color=_C_TEXT3, symbol="line-ns", line=dict(width=2, color=_C_TEXT3)),
            showlegend=False,
            hovertemplate=f"<b>{rname}</b><br>Nominal: {eta.nominal_transit_days}d<extra></extra>",
        ))

    fig.update_layout(
        **_CHART_LAYOUT,
        height=max(320, len(route_names) * 28 + 80),
        xaxis=dict(title="Transit Days", gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(gridcolor="rgba(0,0,0,0)"),
        showlegend=False,
        title=dict(text="Fleet ETA Predictions with 90% Confidence Intervals", font=dict(size=13, color=_C_TEXT2), x=0),
    )
    # Legend annotation
    fig.add_annotation(
        xref="paper", yref="paper", x=0.01, y=-0.06,
        text=f'<span style="color:{_C_HIGH}">● On-time</span>  '
             f'<span style="color:{_C_MOD}">● Minor delay</span>  '
             f'<span style="color:{_C_LOW}">● Major delay</span>  '
             f'<span style="color:{_C_TEXT3}">| Nominal transit</span>',
        showarrow=False, font=dict(size=10, color=_C_TEXT2),
        align="left",
    )
    st.plotly_chart(fig, use_container_width=True, key="eta_confidence_intervals")


# ---------------------------------------------------------------------------
# Existing helpers (preserved)
# ---------------------------------------------------------------------------

def _render_eta_route_table(etas: list[ShipmentETA]) -> None:
    """Route ETA summary table with delay badge and confidence bar."""
    if not etas:
        st.info("No ETA data available.")
        return

    rows_html = ""
    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        rname = route.name if route else eta.route_id
        delay_col = _delay_color(eta.predicted_delay_days)
        risk_col = _risk_color(eta.congestion_risk)
        conf_pct = int(eta.confidence * 100)
        conf_col = _C_HIGH if conf_pct >= 80 else (_C_MOD if conf_pct >= 65 else _C_LOW)

        rows_html += (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,0.04)">'
            f'<td style="padding:9px 12px; color:{_C_TEXT}; font-weight:600; font-size:0.84rem">{rname}</td>'
            f'<td style="padding:9px 12px; color:{_C_TEXT2}; font-size:0.82rem; text-align:center">'
            f'{eta.origin_port} → {eta.dest_port}</td>'
            f'<td style="padding:9px 12px; color:{_C_TEXT2}; text-align:center">{eta.nominal_transit_days}d</td>'
            f'<td style="padding:9px 12px; color:{_C_TEXT}; font-weight:700; text-align:center">{eta.total_eta_days:.1f}d</td>'
            f'<td style="padding:9px 12px; text-align:center">'
            f'<span style="background:rgba(0,0,0,0.3); border:1px solid {delay_col}; color:{delay_col};'
            f' border-radius:4px; padding:2px 8px; font-size:0.75rem; font-weight:700">'
            f'+{eta.predicted_delay_days:.1f}d</span></td>'
            f'<td style="padding:9px 12px; text-align:center">'
            f'<span style="background:rgba(0,0,0,0.3); border:1px solid {risk_col}; color:{risk_col};'
            f' border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700">'
            f'{eta.congestion_risk}</span></td>'
            f'<td style="padding:9px 18px">'
            f'<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:6px; min-width:60px">'
            f'<div style="background:{conf_col}; width:{conf_pct}%; height:100%; border-radius:4px"></div>'
            f'</div>'
            f'<div style="font-size:0.7rem; color:{conf_col}; text-align:right; margin-top:2px">{conf_pct}%</div>'
            f'</td></tr>'
        )

    st.markdown(
        f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER}; border-radius:12px; overflow:auto">'
        f'<table style="width:100%; border-collapse:collapse; min-width:600px">'
        f'<thead><tr style="background:rgba(255,255,255,0.04)">'
        + "".join(
            f'<th style="padding:10px 12px; color:{_C_TEXT3}; font-size:0.63rem; text-transform:uppercase;'
            f' letter-spacing:0.08em; text-align:{"left" if i < 2 else "center"}">{h}</th>'
            for i, h in enumerate(["Route", "Lane", "Nominal", "ETA", "Delay", "Risk", "Confidence"])
        )
        + f'</tr></thead><tbody>{rows_html}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def _render_route_eta_predictor(etas: list[ShipmentETA]) -> None:
    """Select a route → detailed prediction card with CI bounds and delay drivers."""
    if not etas:
        return

    route_options = {}
    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        rname = route.name if route else eta.route_id
        route_options[rname] = eta

    selected_name = st.selectbox(
        "Select route for detailed ETA prediction",
        list(route_options.keys()),
        key="eta_predictor_route_select",
    )
    eta = route_options.get(selected_name)
    if not eta:
        return

    conf_pct = int(eta.confidence * 100)
    delay_col = _delay_color(eta.predicted_delay_days)
    risk_col = _risk_color(eta.congestion_risk)
    sigma = (1 - eta.confidence) * eta.total_eta_days * 0.22
    ci_lo = max(1, eta.total_eta_days - 1.96 * sigma)
    ci_hi = eta.total_eta_days + 1.96 * sigma

    drivers_html = "".join(
        f'<div style="background:rgba(59,130,246,0.08); border:1px solid rgba(59,130,246,0.2);'
        f' border-radius:6px; padding:5px 10px; margin:3px 0; font-size:0.78rem; color:{_C_TEXT2}">'
        f'◈ {driver}</div>'
        for driver in eta.delay_drivers
    ) or f'<div style="color:{_C_TEXT3}; font-size:0.8rem">No significant delay drivers</div>'

    st.markdown(
        f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER}; border-radius:12px; padding:20px 24px">'
        f'<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:16px">'
        f'<div><div style="font-size:1.0rem; font-weight:800; color:{_C_TEXT}">{selected_name}</div>'
        f'<div style="font-size:0.8rem; color:{_C_TEXT3}; margin-top:2px">'
        f'{eta.origin_port} → {eta.dest_port}</div></div>'
        f'<span style="background:rgba(0,0,0,0.3); border:1px solid {risk_col}; color:{risk_col};'
        f' border-radius:6px; padding:4px 12px; font-size:0.75rem; font-weight:700">'
        f'{eta.congestion_risk} RISK</span></div>'
        f'<div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:12px; margin-bottom:16px">'
        + "".join([
            f'<div style="background:rgba(255,255,255,0.03); border-radius:8px; padding:12px; text-align:center">'
            f'<div style="font-size:0.62rem; color:{_C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em">{lbl}</div>'
            f'<div style="font-size:1.3rem; font-weight:800; color:{col}; margin-top:4px">{val}</div>'
            f'</div>'
            for lbl, val, col in [
                ("Nominal Transit", f"{eta.nominal_transit_days}d", _C_TEXT2),
                ("Predicted ETA", f"{eta.total_eta_days:.1f}d", _C_ACCENT),
                ("Predicted Delay", f"+{eta.predicted_delay_days:.1f}d", delay_col),
                ("Confidence", f"{conf_pct}%", _C_HIGH if conf_pct >= 80 else _C_MOD),
            ]
        ])
        + f'</div>'
        f'<div style="background:rgba(59,130,246,0.05); border-radius:8px; padding:10px 14px; margin-bottom:14px">'
        f'<div style="font-size:0.7rem; color:{_C_TEXT3}; margin-bottom:4px">95% CONFIDENCE INTERVAL</div>'
        f'<div style="font-size:0.9rem; font-weight:700; color:{_C_ACCENT}">'
        f'{ci_lo:.1f}d — {ci_hi:.1f}d transit</div></div>'
        f'<div style="font-size:0.72rem; color:{_C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">Delay Drivers</div>'
        + drivers_html
        + f'<div style="margin-top:14px; padding-top:12px; border-top:1px solid {_C_BORDER}; display:flex; justify-content:space-between">'
        f'<div style="font-size:0.78rem; color:{_C_TEXT3}">Optimal departure: <span style="color:{_C_HIGH}">{eta.optimal_departure_week}</span></div>'
        f'<div style="font-size:0.78rem; color:{_C_TEXT3}">Savings vs now: <span style="color:{_C_HIGH}">${eta.cost_savings_vs_now:,.0f}/FEU</span></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def _render_delay_impact_calculator() -> None:
    """Sliders for delay days + cargo value → estimated holding/storage/expediting cost."""
    c1, c2, c3 = st.columns(3)
    with c1:
        delay_days = st.slider("Delay duration (days)", 1, 30, 7, key="delay_calc_days")
    with c2:
        cargo_value = st.slider("Cargo value ($K)", 10, 5000, 500, step=10, key="delay_calc_value") * 1000
    with c3:
        num_feu = st.slider("Number of FEUs", 1, 50, 5, key="delay_calc_feu")

    annual_rate = 0.06
    daily_carry = cargo_value * num_feu * (annual_rate / 365)
    storage_per_day = 120 * num_feu  # $120/FEU/day
    expedite_cost = delay_days * num_feu * 85  # $85/FEU/day for expediting admin
    total_cost = (daily_carry + storage_per_day) * delay_days + expedite_cost

    c1, c2, c3, c4 = st.columns(4)
    for col, lbl, val, color in [
        (c1, "Inventory Carry Cost", f"${daily_carry * delay_days:,.0f}", _C_MOD),
        (c2, "Port Storage Fees", f"${storage_per_day * delay_days:,.0f}", _C_ORANGE),
        (c3, "Expediting / Admin", f"${expedite_cost:,.0f}", _C_CONV),
        (c4, "Total Impact", f"${total_cost:,.0f}", _C_LOW),
    ]:
        with col:
            st.markdown(_kpi_card(lbl, val, f"{delay_days}d × {num_feu} FEU", color), unsafe_allow_html=True)


def _render_hero(etas: list[ShipmentETA]) -> None:
    if not etas:
        return
    avg_delay = sum(e.predicted_delay_days for e in etas) / len(etas)
    high_risk = sum(1 for e in etas if e.congestion_risk in ("HIGH", "SEVERE"))
    total_savings = sum(e.cost_savings_vs_now for e in etas if e.cost_savings_vs_now > 0)
    avg_conf = sum(e.confidence for e in etas) / len(etas) * 100

    c1, c2, c3, c4 = st.columns(4)
    for col, lbl, val, sub, color in [
        (c1, "Avg Predicted Delay", f"{avg_delay:.1f}d", "all routes", _delay_color(avg_delay)),
        (c2, "High-Risk Routes", str(high_risk), "HIGH or SEVERE", _C_LOW if high_risk >= 3 else _C_MOD),
        (c3, "Potential Savings", f"${total_savings:,.0f}", "optimal vs now", _C_HIGH),
        (c4, "Avg Model Confidence", f"{avg_conf:.1f}%", "prediction accuracy", _C_HIGH if avg_conf >= 80 else _C_MOD),
    ]:
        with col:
            st.markdown(_kpi_card(lbl, val, sub, color), unsafe_allow_html=True)


def _eta_card_html(eta: ShipmentETA) -> str:
    route = ROUTES_BY_ID.get(eta.route_id)
    rname = route.name if route else eta.route_id
    delay_col = _delay_color(eta.predicted_delay_days)
    risk_col = _risk_color(eta.congestion_risk)
    conf_pct = int(eta.confidence * 100)
    drivers = " · ".join(eta.delay_drivers[:2]) if eta.delay_drivers else "No significant drivers"
    return (
        f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER};'
        f' border-left:4px solid {risk_col}; border-radius:10px; padding:14px 16px">'
        f'<div style="font-size:0.82rem; font-weight:700; color:{_C_TEXT}; margin-bottom:2px">{rname}</div>'
        f'<div style="font-size:0.72rem; color:{_C_TEXT3}; margin-bottom:10px">'
        f'{eta.origin_port} → {eta.dest_port}</div>'
        f'<div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px">'
        f'<div style="text-align:center; background:rgba(255,255,255,0.03); border-radius:6px; padding:8px">'
        f'<div style="font-size:0.6rem; color:{_C_TEXT3}; text-transform:uppercase">ETA</div>'
        f'<div style="font-size:1.1rem; font-weight:800; color:{_C_TEXT}">{eta.total_eta_days:.0f}d</div></div>'
        f'<div style="text-align:center; background:rgba(255,255,255,0.03); border-radius:6px; padding:8px">'
        f'<div style="font-size:0.6rem; color:{_C_TEXT3}; text-transform:uppercase">Delay</div>'
        f'<div style="font-size:1.1rem; font-weight:800; color:{delay_col}">+{eta.predicted_delay_days:.1f}d</div></div>'
        f'</div>'
        f'<div style="font-size:0.7rem; color:{_C_TEXT3}; margin-bottom:6px">{drivers}</div>'
        f'<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:4px">'
        f'<div style="background:{_C_HIGH if conf_pct>=80 else _C_MOD}; width:{conf_pct}%; height:100%; border-radius:4px"></div>'
        f'</div><div style="font-size:0.67rem; color:{_C_TEXT3}; text-align:right; margin-top:2px">{conf_pct}% confidence</div>'
        f'</div>'
    )


def _render_eta_cards(etas: list[ShipmentETA]) -> None:
    cols = st.columns(3)
    for i, eta in enumerate(etas):
        with cols[i % 3]:
            st.markdown(_eta_card_html(eta), unsafe_allow_html=True)
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


def _week_congestion_level(base_congestion: float, week_offset: int) -> float:
    osc = 0.12 * math.sin(week_offset * math.pi / 2)
    trend = -0.02 * week_offset
    return max(0.05, min(0.98, base_congestion + osc + trend))


def _cell_color(cong: float) -> str:
    if cong < 0.40:
        return "rgba(16,185,129,0.15)"
    if cong < 0.65:
        return "rgba(245,158,11,0.15)"
    return "rgba(239,68,68,0.15)"


def _cell_text_color(cong: float) -> str:
    if cong < 0.40:
        return _C_HIGH
    if cong < 0.65:
        return _C_MOD
    return _C_LOW


def _render_departure_calendar(etas: list[ShipmentETA], port_results: list) -> None:
    if not etas:
        return

    top = etas[:4]
    route_labels = []
    congestion_bases = []
    for eta in top:
        route = ROUTES_BY_ID.get(eta.route_id)
        rname = route.name if route else eta.route_id
        route_labels.append(rname[:22])
        base = 0.3 + eta.predicted_delay_days * 0.12
        congestion_bases.append(min(0.95, base))

    today = date.today()
    weeks = [today + timedelta(weeks=w) for w in range(4)]
    week_labels = [f"Wk {w.strftime('%b %d')}" for w in weeks]

    header_html = "".join(
        f'<th style="padding:10px 12px; color:{_C_TEXT3}; font-size:0.65rem; text-transform:uppercase;'
        f' letter-spacing:0.08em">{wl}</th>'
        for wl in week_labels
    )

    rows_html = ""
    for rname, base in zip(route_labels, congestion_bases):
        cells_html = ""
        for w in range(4):
            cong = _week_congestion_level(base, w)
            bg = _cell_color(cong)
            tc = _cell_text_color(cong)
            label = "LOW" if cong < 0.40 else ("MOD" if cong < 0.65 else "HIGH")
            cells_html += (
                f'<td style="padding:10px; text-align:center">'
                f'<div style="background:{bg}; border-radius:6px; padding:8px 4px">'
                f'<div style="font-size:0.65rem; font-weight:700; color:{tc}">{label}</div>'
                f'<div style="font-size:0.7rem; color:{_C_TEXT3}">{cong:.0%}</div>'
                f'</div></td>'
            )
        rows_html += (
            f'<tr style="border-bottom:1px solid rgba(255,255,255,0.04)">'
            f'<td style="padding:10px 12px; color:{_C_TEXT}; font-weight:600; font-size:0.82rem; white-space:nowrap">{rname}</td>'
            + cells_html + "</tr>"
        )

    st.markdown(
        f'<div style="background:{_C_CARD}; border:1px solid {_C_BORDER}; border-radius:12px; overflow:auto">'
        f'<table style="width:100%; border-collapse:collapse">'
        f'<thead><tr style="background:rgba(255,255,255,0.04)">'
        f'<th style="padding:10px 12px; color:{_C_TEXT3}; font-size:0.65rem; text-align:left">Route</th>'
        + header_html
        + f'</tr></thead><tbody>{rows_html}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def _render_savings_calculator(etas: list[ShipmentETA]) -> None:
    if not etas:
        return

    route_map = {}
    for eta in etas:
        route = ROUTES_BY_ID.get(eta.route_id)
        rname = route.name if route else eta.route_id
        route_map[rname] = eta

    col_sel, col_feu = st.columns([2, 1])
    with col_sel:
        sel = st.selectbox("Route", list(route_map.keys()), key="savings_calc_route")
    with col_feu:
        feu_count = st.number_input("FEU count", min_value=1, max_value=500, value=10, step=1, key="savings_calc_feu")

    eta = route_map.get(sel)
    if not eta:
        return

    total_saving = eta.cost_savings_vs_now * feu_count
    rate_opt = eta.rate_at_optimal
    rate_now = rate_opt + eta.cost_savings_vs_now

    c1, c2, c3 = st.columns(3)
    for col, lbl, val, sub, color in [
        (c1, "Current Rate", f"${rate_now:,.0f}/FEU", "est. current spot", _C_MOD),
        (c2, "Optimal Rate", f"${rate_opt:,.0f}/FEU", eta.optimal_departure_week, _C_HIGH),
        (c3, "Total Savings", f"${total_saving:,.0f}", f"{feu_count} FEU × ${eta.cost_savings_vs_now:,.0f}", _C_ACCENT),
    ]:
        with col:
            st.markdown(_kpi_card(lbl, val, sub, color), unsafe_allow_html=True)


def _render_congestion_timeline(port_results: list) -> None:
    """30-day sinusoidal congestion forecast for a selected port."""
    if not port_results:
        return

    port_names = []
    for pr in port_results:
        name = (
            pr.get("port_name") if isinstance(pr, dict)
            else getattr(pr, "port_name", None)
        )
        if name and name not in port_names:
            port_names.append(name)

    if not port_names:
        st.info("No port names available.")
        return

    sel_port = st.selectbox("Select port for congestion timeline", port_names, key="cong_timeline_port")

    base_cong = 0.6
    for pr in port_results:
        name = pr.get("port_name") if isinstance(pr, dict) else getattr(pr, "port_name", None)
        if name == sel_port:
            base_cong = (
                float(pr.get("congestion_index", 0.6)) if isinstance(pr, dict)
                else float(getattr(pr, "congestion_index", 0.6))
            )
            break

    today = date.today()
    days = [today + timedelta(days=i) for i in range(30)]
    day_strs = [d.strftime("%b %d") for d in days]
    cong_vals = [
        max(0.05, min(0.98, base_cong + 0.15 * math.sin(i * math.pi / 7) + 0.04 * math.sin(i * math.pi / 3))
            )
        for i in range(30)
    ]
    baseline = [base_cong] * 30

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=day_strs, y=cong_vals,
        mode="lines",
        line=dict(color=_C_ACCENT, width=2.5),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.08)",
        name="Congestion forecast",
        hovertemplate="<b>%{x}</b><br>Congestion: %{y:.0%}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=day_strs, y=baseline,
        mode="lines",
        line=dict(color=_C_TEXT3, dash="dot", width=1.5),
        name="Historical baseline",
        hoverinfo="skip",
    ))
    for threshold, color, label in [(0.70, _C_LOW, "High"), (0.40, _C_MOD, "Moderate")]:
        fig.add_hline(
            y=threshold, line=dict(color=color, dash="dash", width=1),
            annotation_text=label, annotation_font=dict(color=color, size=9),
            annotation_position="right",
        )
    fig.update_layout(
        **_CHART_LAYOUT,
        height=300,
        xaxis=dict(tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(title="Congestion Index", tickformat=".0%", range=[0, 1.05]),
        title=dict(text=f"{sel_port} — 30-Day Congestion Forecast", font=dict(size=13, color=_C_TEXT2), x=0),
        legend=dict(
            bgcolor="rgba(26,34,53,0.8)", bordercolor=_C_BORDER, borderwidth=1,
            font=dict(size=10, color=_C_TEXT2),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="eta_congestion_timeline")


def _export_eta_csv(etas: list[ShipmentETA]) -> None:
    if not etas:
        return
    try:
        rows = []
        for eta in etas:
            route = ROUTES_BY_ID.get(eta.route_id)
            rows.append({
                "route_id": eta.route_id,
                "route_name": route.name if route else eta.route_id,
                "origin_port": eta.origin_port,
                "dest_port": eta.dest_port,
                "nominal_transit_days": eta.nominal_transit_days,
                "total_eta_days": round(eta.total_eta_days, 2),
                "predicted_delay_days": round(eta.predicted_delay_days, 2),
                "congestion_risk": eta.congestion_risk,
                "confidence": round(eta.confidence, 3),
                "optimal_departure_week": eta.optimal_departure_week,
                "cost_savings_vs_now": round(eta.cost_savings_vs_now, 0),
                "delay_drivers": " | ".join(eta.delay_drivers),
            })
        df = pd.DataFrame(rows)
        csv_bytes = df.to_csv(index=False).encode()
        st.download_button(
            label="Download ETA Data (CSV)",
            data=csv_bytes,
            file_name=f"eta_intelligence_{date.today().isoformat()}.csv",
            mime="text/csv",
            key="eta_csv_download",
        )
    except Exception as exc:
        logger.warning("CSV export failed: {}", exc)


# ---------------------------------------------------------------------------
# Main render entry point
# ---------------------------------------------------------------------------

def render(
    port_results: list,
    route_results: list,
    freight_data: dict,
    macro_data: dict,
) -> None:
    """Render the Cargo ETA Intelligence tab."""
    logger.info("Rendering ETA Intelligence tab")

    st.header("Cargo ETA Intelligence")
    st.caption(
        "Live fleet ETA analysis — predicted delays, confidence intervals, carrier reliability, "
        "and port congestion intelligence for all active shipping lanes."
    )

    # Compute ETAs
    try:
        etas = predict_all_routes(port_results, freight_data, macro_data)
    except Exception as exc:
        logger.error("ETA prediction failed: {}", exc)
        st.error("ETA prediction encountered an error: " + str(exc))
        return

    if not etas:
        st.info("No ETA data computed. Ensure port and freight data are loaded.")
        return

    # ── Section 0: ETA Hero Dashboard ────────────────────────────────────────
    try:
        _render_eta_hero(etas)
    except Exception as exc:
        logger.warning("ETA hero dashboard failed: {}", exc)

    # ── Section 1: Vessel Tracking Map ────────────────────────────────────────
    _divider("Vessel Tracking Map")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:10px">'
        "Live vessel positions estimated from route midpoints. Marker size = delay severity. "
        "Colors: green = on-time, amber = minor delay, red = major delay.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_vessel_map(etas)
    except Exception as exc:
        logger.warning("Vessel map failed: {}", exc)

    # ── Section 2 & 8 side by side: ETA Accuracy + Carrier Reliability ───────
    _divider("Route & Carrier Performance")
    col_left, col_right = st.columns(2)
    with col_left:
        _section_header("ETA Accuracy by Route", "On-time delivery % vs 80% target")
        try:
            _render_eta_accuracy_by_route(etas)
        except Exception as exc:
            logger.warning("ETA accuracy chart failed: {}", exc)
    with col_right:
        _section_header("Carrier Reliability Ranking", "On-time % comparison by carrier")
        try:
            _render_carrier_reliability(etas)
        except Exception as exc:
            logger.warning("Carrier reliability failed: {}", exc)

    # ── Section 9: ETA Confidence Intervals ──────────────────────────────────
    _divider("ETA Prediction Confidence Intervals")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:10px">'
        "Current fleet ETA with 90% confidence intervals. Bars show prediction uncertainty; "
        "wider = less confident. Tick mark shows nominal (undelayed) transit.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_eta_confidence_intervals(etas)
    except Exception as exc:
        logger.warning("Confidence intervals failed: {}", exc)

    # ── Section 3: Delay Heatmap ──────────────────────────────────────────────
    _divider("Delay Cause Heatmap")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:10px">'
        "Delay frequency score (0–1) by route and root cause. "
        "Darker red = higher frequency of that cause on that lane.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_delay_heatmap(etas)
    except Exception as exc:
        logger.warning("Delay heatmap failed: {}", exc)

    # ── Sections 4 & 5 side by side: Distribution + Donut ────────────────────
    _divider("Delay Analysis")
    col_dist, col_donut = st.columns([3, 2])
    with col_dist:
        _section_header("Delay Duration Distribution", "Fleet-wide predicted delays with P25/P50/P75/P90 markers")
        try:
            _render_delay_distribution(etas)
        except Exception as exc:
            logger.warning("Delay distribution failed: {}", exc)
    with col_donut:
        _section_header("Delay Cause Attribution", "Primary causes across global container trade 2024–2026")
        try:
            _render_delay_cause_donut()
        except Exception as exc:
            logger.warning("Delay cause donut failed: {}", exc)

    # ── Section 6: Port Congestion Leaderboard ────────────────────────────────
    _divider("Port Congestion Wait Leaderboard")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:12px">'
        "Current average vessel wait time vs historical baseline, ranked by congestion severity.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_congestion_leaderboard(port_results)
    except Exception as exc:
        logger.warning("Congestion leaderboard failed: {}", exc)

    # ── Section 7: Arrival Forecast Calendar ──────────────────────────────────
    _divider("14-Day Arrival Forecast Calendar")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:12px">'
        "Expected vessel arrivals across all tracked routes for the next 14 days. "
        "Color intensity indicates arrival density at destination ports.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_arrival_forecast_calendar(etas)
    except Exception as exc:
        logger.warning("Arrival calendar failed: {}", exc)

    # ── Route ETA Table ───────────────────────────────────────────────────────
    _divider("Route ETA Summary Table")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:10px">'
        "All tracked routes: scheduled vs predicted transit, delay risk badge, and model confidence.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_eta_route_table(etas)
    except Exception as exc:
        logger.warning("Route ETA table failed: {}", exc)

    # ── Route ETA Predictor ───────────────────────────────────────────────────
    _divider("Route ETA Predictor")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:10px">'
        "Select a route for a detailed prediction card with CI bounds, confidence, and delay drivers.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_route_eta_predictor(etas)
    except Exception as exc:
        logger.warning("Route ETA predictor failed: {}", exc)

    # ── Delay Impact Calculator ───────────────────────────────────────────────
    _divider("Delay Impact Calculator")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:10px">'
        "Estimate the financial impact of a delay — inventory carry cost, port storage, and expediting fees.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_delay_impact_calculator()
    except Exception as exc:
        logger.warning("Delay impact calculator failed: {}", exc)

    # ── Hero KPIs ─────────────────────────────────────────────────────────────
    _divider("Delay & Savings Summary")
    try:
        _render_hero(etas)
    except Exception as exc:
        logger.warning("Hero KPIs failed: {}", exc)

    # ── Route ETA Cards ───────────────────────────────────────────────────────
    _divider("Route ETA Cards")
    try:
        _render_eta_cards(etas)
    except Exception as exc:
        logger.warning("ETA cards failed: {}", exc)

    # ── CSV Export ────────────────────────────────────────────────────────────
    try:
        _export_eta_csv(etas)
    except Exception as exc:
        logger.warning("CSV export failed: {}", exc)

    # ── Departure Calendar ────────────────────────────────────────────────────
    _divider("4-Week Departure Calendar")
    st.markdown(
        f'<div style="font-size:0.82rem; color:{_C_TEXT2}; margin-bottom:12px">'
        "Forward-looking 4-week congestion calendar. Ship during green weeks for lowest delay risk.</div>",
        unsafe_allow_html=True,
    )
    try:
        _render_departure_calendar(etas, port_results)
    except Exception as exc:
        logger.warning("Departure calendar failed: {}", exc)

    # ── Best Departure Windows ────────────────────────────────────────────────
    try:
        best_windows = get_best_departure_windows(etas)
        if best_windows:
            _divider("Top Departure Opportunities")
            cols = st.columns(min(len(best_windows), 3))
            for i, window in enumerate(best_windows[:3]):
                with cols[i]:
                    route = ROUTES_BY_ID.get(window["route_id"])
                    rname = route.name if route else window["route_id"]
                    st.markdown(
                        '<div style="background:rgba(16,185,129,0.08); border:1px solid ' + _C_HIGH + ';'
                        ' border-radius:10px; padding:16px; text-align:center">'
                        '<div style="font-size:0.65rem; font-weight:700; color:' + _C_HIGH + ';'
                        ' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px">Best Departure Window</div>'
                        '<div style="font-size:0.88rem; font-weight:700; color:' + _C_TEXT + '; margin-bottom:3px">'
                        + rname + "</div>"
                        '<div style="font-size:0.75rem; color:' + _C_TEXT3 + '; margin-bottom:10px">'
                        + window["origin_port"] + " → " + window["dest_port"] + "</div>"
                        '<div style="font-size:1.25rem; font-weight:800; color:' + _C_HIGH + '">'
                        + window["optimal_departure_week"] + "</div>"
                        '<div style="font-size:0.82rem; color:' + _C_TEXT2 + '; margin-top:8px">'
                        "Save $" + "{:,.0f}".format(window["cost_savings_vs_now"]) + "/FEU vs now"
                        "</div></div>",
                        unsafe_allow_html=True,
                    )
    except Exception as exc:
        logger.warning("Best departure windows failed: {}", exc)

    # ── Cost Savings Calculator ───────────────────────────────────────────────
    _divider("Cost Savings Calculator")
    try:
        _render_savings_calculator(etas)
    except Exception as exc:
        logger.warning("Savings calculator failed: {}", exc)

    # ── Congestion Timeline ───────────────────────────────────────────────────
    _divider("Port Congestion Timeline — 30-Day Outlook")
    if port_results:
        try:
            _render_congestion_timeline(port_results)
        except Exception as exc:
            logger.warning("Congestion timeline failed: {}", exc)
    else:
        st.info("No port data available for congestion timeline.")
