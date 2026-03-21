"""Shipping Network Visualization Tab — ultra-detailed interactive analytics."""
from __future__ import annotations

import csv as _csv
import io as _io
import math
import random
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ports.demand_analyzer import PortDemandResult
from ports.port_registry import PORTS_BY_LOCODE
from routes.optimizer import RouteOpportunity
from routes.route_registry import ROUTES_BY_ID

# ── Color constants ────────────────────────────────────────────────────────────
C_BG   = "#0a0f1a"
C_CARD = "#1a2235"
C_HIGH = "#10b981"
C_MOD  = "#f59e0b"
C_LOW  = "#ef4444"

_C_SURFACE = "#111827"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"
_C_ACCENT  = "#3b82f6"
_C_CONV    = "#8b5cf6"

_REGION_COLORS: dict[str, str] = {
    "Asia East":          "#3b82f6",
    "Southeast Asia":     "#06b6d4",
    "South Asia":         "#8b5cf6",
    "Middle East":        "#f97316",
    "Europe":             "#10b981",
    "North America West": "#f59e0b",
    "North America East": "#eab308",
    "South America":      "#ef4444",
    "Africa":             "#84cc16",
}

_LOCODE_FLAGS: dict[str, str] = {
    "CNSHA": "\U0001f1e8\U0001f1f3",
    "CNNBO": "\U0001f1e8\U0001f1f3",
    "CNSZN": "\U0001f1e8\U0001f1f3",
    "CNTAO": "\U0001f1e8\U0001f1f3",
    "CNTXG": "\U0001f1e8\U0001f1f3",
    "SGSIN": "\U0001f1f8\U0001f1ec",
    "KRPUS": "\U0001f1f0\U0001f1f7",
    "HKHKG": "\U0001f1ed\U0001f1f0",
    "MYPKG": "\U0001f1f2\U0001f1fe",
    "MYTPP": "\U0001f1f2\U0001f1fe",
    "NLRTM": "\U0001f1f3\U0001f1f1",
    "AEJEA": "\U0001f1e6\U0001f1ea",
    "BEANR": "\U0001f1e7\U0001f1ea",
    "TWKHH": "\U0001f1f9\U0001f1fc",
    "USLAX": "\U0001f1fa\U0001f1f8",
    "USLGB": "\U0001f1fa\U0001f1f8",
    "USNYC": "\U0001f1fa\U0001f1f8",
    "USSAV": "\U0001f1fa\U0001f1f8",
    "DEHAM": "\U0001f1e9\U0001f1ea",
    "MATNM": "\U0001f1f2\U0001f1e6",
    "JPYOK": "\U0001f1ef\U0001f1f5",
    "LKCMB": "\U0001f1f1\U0001f1f0",
    "GRPIR": "\U0001f1ec\U0001f1f7",
    "GBFXT": "\U0001f1ec\U0001f1e7",
    "BRSAO": "\U0001f1e7\U0001f1f7",
}

_CORRIDOR_ROUTES: dict[str, list[str]] = {
    "Trans-Pacific": [
        "transpacific_eb", "transpacific_wb",
        "sea_transpacific_eb", "longbeach_to_asia",
    ],
    "Asia-Europe": [
        "asia_europe", "ningbo_europe", "middle_east_to_europe",
        "south_asia_to_europe", "med_hub_to_asia",
    ],
    "Transatlantic": ["transatlantic"],
    "South America": [
        "china_south_america", "europe_south_america",
        "us_east_south_america",
    ],
    "Intra-Asia": [
        "intra_asia_china_sea", "intra_asia_china_japan",
        "middle_east_to_asia",
    ],
}

_CHOKEPOINTS: dict[str, list[str]] = {
    "Trans-Pacific":  ["Malacca Strait", "Panama Canal", "Aleutian Pass"],
    "Asia-Europe":    ["Suez Canal", "Malacca Strait", "Bab-el-Mandeb", "Strait of Hormuz"],
    "Transatlantic":  ["English Channel", "GIUK Gap"],
    "South America":  ["Panama Canal", "Strait of Magellan", "Cape of Good Hope"],
    "Intra-Asia":     ["Malacca Strait", "Lombok Strait", "Taiwan Strait"],
    "All":            ["Suez Canal", "Panama Canal", "Malacca Strait", "Bab-el-Mandeb",
                       "Strait of Hormuz", "English Channel", "Strait of Magellan"],
}

# Carrier data: which carriers operate which corridor types
_CARRIER_ROUTES: dict[str, list[str]] = {
    "MSC":          ["Trans-Pacific", "Asia-Europe", "Transatlantic", "South America"],
    "Maersk":       ["Trans-Pacific", "Asia-Europe", "Transatlantic", "South America", "Intra-Asia"],
    "CMA CGM":      ["Trans-Pacific", "Asia-Europe", "Transatlantic", "South America"],
    "COSCO":        ["Trans-Pacific", "Asia-Europe", "Intra-Asia"],
    "Hapag-Lloyd":  ["Trans-Pacific", "Asia-Europe", "Transatlantic"],
    "Evergreen":    ["Trans-Pacific", "Asia-Europe", "Intra-Asia"],
    "ONE":          ["Trans-Pacific", "Asia-Europe", "Intra-Asia"],
    "Yang Ming":    ["Trans-Pacific", "Intra-Asia"],
    "HMM":          ["Trans-Pacific", "Asia-Europe", "Intra-Asia"],
    "PIL":          ["Southeast Asia", "Intra-Asia", "South America"],
    "ZIM":          ["Trans-Pacific", "Transatlantic"],
    "Wan Hai":      ["Intra-Asia"],
}

# Hub tier classification
_HUB_TIERS: dict[str, str] = {
    "SGSIN": "Tier 1 — Global",
    "HKHKG": "Tier 1 — Global",
    "CNSHA": "Tier 1 — Global",
    "NLRTM": "Tier 1 — Global",
    "AEJEA": "Tier 1 — Global",
    "KRPUS": "Tier 2 — Regional",
    "MYTPP": "Tier 2 — Regional",
    "MYPKG": "Tier 2 — Regional",
    "DEHAM": "Tier 2 — Regional",
    "BEANR": "Tier 2 — Regional",
    "GRPIR": "Tier 2 — Regional",
    "MATNM": "Tier 2 — Regional",
    "LKCMB": "Tier 2 — Regional",
    "USLAX": "Tier 3 — Gateway",
    "USLGB": "Tier 3 — Gateway",
    "USNYC": "Tier 3 — Gateway",
    "USSAV": "Tier 3 — Gateway",
    "JPYOK": "Tier 3 — Gateway",
    "CNNBO": "Tier 3 — Gateway",
    "CNSZN": "Tier 3 — Gateway",
    "CNTAO": "Tier 3 — Gateway",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _score_color(score: float) -> str:
    if score >= 0.65:
        return C_HIGH
    if score >= 0.35:
        return C_MOD
    return C_LOW


def _demand_tier(score: float) -> str:
    if score >= 0.65:
        return "High"
    if score >= 0.35:
        return "Moderate"
    return "Low"


def _arc_points(lat1: float, lon1: float, lat2: float, lon2: float, n: int = 40):
    if n < 2:
        return [lat1, lat2], [lon1, lon2]

    def to_xyz(lat_deg, lon_deg):
        lr, lo = math.radians(lat_deg), math.radians(lon_deg)
        return math.cos(lr)*math.cos(lo), math.cos(lr)*math.sin(lo), math.sin(lr)

    def to_latlon(x, y, z):
        return math.degrees(math.atan2(z, math.sqrt(x*x+y*y))), math.degrees(math.atan2(y, x))

    x1,y1,z1 = to_xyz(lat1,lon1); x2,y2,z2 = to_xyz(lat2,lon2)
    dot = max(-1.0, min(1.0, x1*x2+y1*y2+z1*z2))
    omega = math.acos(dot)
    lats, lons = [], []
    for i in range(n):
        t = i/(n-1)
        if omega < 1e-6:
            xi,yi,zi = x1+(x2-x1)*t, y1+(y2-y1)*t, z1+(z2-z1)*t
        else:
            so = math.sin(omega)
            w1,w2 = math.sin((1-t)*omega)/so, math.sin(t*omega)/so
            xi,yi,zi = w1*x1+w2*x2, w1*y1+w2*y2, w1*z1+w2*z2
        la,lo = to_latlon(xi,yi,zi)
        lats.append(la); lons.append(lo)
    return lats, lons


def _build_port_lookup(port_results: list[PortDemandResult]) -> dict[str, PortDemandResult]:
    return {r.locode: r for r in port_results}


def _route_width(rate: float, min_r: float, max_r: float) -> float:
    if max_r <= min_r:
        return 2.5
    return 1.5 + (rate - min_r) / (max_r - min_r) * 3.5


def _compute_betweenness(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> dict[str, float]:
    all_locodes = {pr.locode for pr in port_results}
    touch_count: dict[str, int] = defaultdict(int)
    for r in route_results:
        touch_count[r.origin_locode] += 1
        touch_count[r.dest_locode]   += 1
    port_lookup = _build_port_lookup(port_results)
    total_routes = max(1, len(route_results))
    betweenness: dict[str, float] = {}
    for locode in all_locodes:
        pr = port_lookup.get(locode)
        if pr is None:
            betweenness[locode] = 0.0
            continue
        betweenness[locode] = touch_count.get(locode, 0) * pr.demand_score / total_routes
    return betweenness


def _section_header(label: str) -> None:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin:32px 0 18px 0">'
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        '<span style="font-size:0.65rem;color:#475569;text-transform:uppercase;'
        'letter-spacing:0.14em;font-weight:700">' + label + '</span>'
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(title: str, value: str, sub: str, color: str, icon: str = "") -> str:
    icon_html = f'<div style="font-size:1.4rem;margin-bottom:4px">{icon}</div>' if icon else ""
    return (
        '<div style="background:' + C_CARD + ';border:1px solid ' + _C_BORDER + ';'
        'border-top:3px solid ' + color + ';border-radius:12px;'
        'padding:18px 16px;text-align:center;height:100%">'
        + icon_html +
        '<div style="font-size:0.6rem;font-weight:700;color:' + _C_TEXT3 + ';'
        'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">' + title + '</div>'
        '<div style="font-size:1.85rem;font-weight:900;color:' + color + ';line-height:1.1;'
        'margin:4px 0;font-variant-numeric:tabular-nums">' + value + '</div>'
        '<div style="font-size:0.69rem;color:' + _C_TEXT3 + ';margin-top:4px">' + sub + '</div>'
        '</div>'
    )


def _region_legend_html() -> str:
    html = '<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;margin-bottom:4px">'
    for region, color in _REGION_COLORS.items():
        html += (
            '<span style="font-size:0.71rem;color:' + _C_TEXT2 + '">'
            '<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
            'background:' + color + ';margin-right:4px;vertical-align:middle"></span>'
            + region + '</span>'
        )
    return html + '</div>'


# ══════════════════════════════════════════════════════════════════════════════
#  Section 1: Network Overview Hero
# ══════════════════════════════════════════════════════════════════════════════

def _render_network_hero(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        n_nodes = len(port_results)
        n_edges = len(route_results)
        max_edges = n_nodes * (n_nodes - 1) / 2 if n_nodes > 1 else 1
        density = n_edges / max_edges if max_edges > 0 else 0.0

        degree_map: dict[str, int] = defaultdict(int)
        neighbors: dict[str, set] = defaultdict(set)
        for r in route_results:
            degree_map[r.origin_locode] += 1
            degree_map[r.dest_locode]   += 1
            neighbors[r.origin_locode].add(r.dest_locode)
            neighbors[r.dest_locode].add(r.origin_locode)

        avg_degree = sum(degree_map.values()) / max(1, len(degree_map))
        hub_count  = sum(1 for d in degree_map.values() if d >= 3)
        regions    = {pr.region for pr in port_results}

        # Avg path length proxy: diameter approximation via BFS sample
        # We approximate with inverse density as a proxy
        avg_path_len = round(1.0 / density if density > 0 else 99.0, 1)
        avg_path_len = min(avg_path_len, 9.9)  # cap for display

        # Network efficiency = density * avg_degree / n_nodes (normalized proxy)
        efficiency = min(1.0, density * avg_degree / max(1, n_nodes) * 10)

        # Connectivity = hub ports / total ports
        connectivity_pct = hub_count / max(1, n_nodes) * 100

        # Avg opportunity score
        avg_opp = (
            sum(r.opportunity_score for r in route_results) / len(route_results)
            if route_results else 0.0
        )

        # Hero banner
        st.markdown(
            '<div style="background:linear-gradient(135deg,#0d1526 0%,#111c35 60%,#0a1628 100%);'
            'border:1px solid rgba(59,130,246,0.2);border-radius:16px;padding:24px 28px 20px 28px;'
            'margin-bottom:20px">'
            '<div style="font-size:1.35rem;font-weight:900;color:' + _C_TEXT + ';letter-spacing:-0.01em">'
            'Global Shipping Network Intelligence'
            '</div>'
            '<div style="font-size:0.82rem;color:' + _C_TEXT3 + ';margin-top:4px">'
            + str(n_nodes) + ' tracked ports across ' + str(len(regions)) + ' regions &nbsp;·&nbsp; '
            + str(n_edges) + ' active trade lanes &nbsp;·&nbsp; '
            'Real-time network topology analysis'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        cols = st.columns(8)
        cards = [
            ("Nodes",        str(n_nodes),                  "Tracked ports",            _C_ACCENT, ""),
            ("Active Lanes", str(n_edges),                  "Direct connections",        _C_CONV,   ""),
            ("Graph Density",f"{density:.3f}",              "Network connectedness",     C_MOD,     ""),
            ("Avg Degree",   f"{avg_degree:.1f}",           "Routes per port",           C_HIGH,    ""),
            ("Hub Ports",    str(hub_count),                "Degree \u2265 3",           "#f97316", ""),
            ("Avg Path",     f"{avg_path_len}x",            "Hops between nodes (est.)", "#06b6d4", ""),
            ("Efficiency",   f"{efficiency:.2f}",           "Network efficiency score",  C_HIGH,    ""),
            ("Avg Opp",      f"{avg_opp:.2f}",              "Mean opportunity score",    _C_TEXT2,  ""),
        ]
        for col, (title, value, sub, color, icon) in zip(cols, cards):
            col.markdown(_kpi_card(title, value, sub, color, icon), unsafe_allow_html=True)

    except Exception:
        logger.exception("Error rendering network hero")
        st.error("Network overview unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 2: Shipping Network Graph (Plotly Scattergeo globe)
# ══════════════════════════════════════════════════════════════════════════════

def _render_globe(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        logger.debug("Rendering 3D trade network globe")
        port_lookup = _build_port_lookup(port_results)
        rates = [r.current_rate_usd_feu for r in route_results if r.current_rate_usd_feu > 0]
        min_rate = min(rates) if rates else 0.0
        max_rate = max(rates) if rates else 1.0

        # Build degree map for node sizing
        degree_map: dict[str, int] = defaultdict(int)
        for r in route_results:
            degree_map[r.origin_locode] += 1
            degree_map[r.dest_locode]   += 1

        fig = go.Figure()

        # Route arcs
        for route_opp in route_results:
            route = ROUTES_BY_ID.get(route_opp.route_id)
            if route is None:
                continue
            orig_port = PORTS_BY_LOCODE.get(route.origin_locode)
            dest_port = PORTS_BY_LOCODE.get(route.dest_locode)
            if orig_port is None or dest_port is None:
                continue

            arc_lats, arc_lons = _arc_points(
                orig_port.lat, orig_port.lon,
                dest_port.lat, dest_port.lon, n=40,
            )

            opp = route_opp.opportunity_score
            if opp >= 0.65:
                arc_color = "rgba(16,185,129,0.82)"
            elif opp >= 0.35:
                arc_color = "rgba(245,158,11,0.65)"
            else:
                arc_color = "rgba(239,68,68,0.38)"

            lw = _route_width(route_opp.current_rate_usd_feu, min_rate, max_rate)
            rate_30d = route_opp.rate_pct_change_30d * 100
            sign = "+" if rate_30d >= 0 else ""
            hover = (
                "<b>" + route_opp.route_name + "</b><br>"
                + "Rate: $" + f"{route_opp.current_rate_usd_feu:,.0f}" + "/FEU<br>"
                + "30d Change: " + sign + f"{rate_30d:.1f}%<br>"
                + "Opportunity: " + f"{route_opp.opportunity_score:.2f}"
                + " (" + route_opp.opportunity_label + ")<br>"
                + "Transit: " + str(route_opp.transit_days) + " days"
            )
            fig.add_trace(go.Scattergeo(
                lat=arc_lats, lon=arc_lons,
                mode="lines",
                line=dict(color=arc_color, width=lw),
                hovertemplate=hover + "<extra></extra>",
                name=route_opp.route_name,
                showlegend=False,
            ))

        # Port glow halos (sized by degree)
        glow_lats, glow_lons, glow_sizes, glow_colors = [], [], [], []
        solid_lats, solid_lons, solid_sizes, solid_colors, solid_texts = [], [], [], [], []

        for pr in port_results:
            port = PORTS_BY_LOCODE.get(pr.locode)
            if port is None:
                continue
            base_size = pr.demand_score * 28 + 10
            deg_bonus  = min(degree_map.get(pr.locode, 0) * 2, 12)
            size = base_size + deg_bonus
            color = _score_color(pr.demand_score)
            flag  = _LOCODE_FLAGS.get(pr.locode, "\U0001f310")
            tier  = _HUB_TIERS.get(pr.locode, "Tier 4 — Feeder")
            top3  = ", ".join(p["category"] for p in pr.top_products[:3]) if pr.top_products else "N/A"
            hover = (
                "<b>" + flag + " " + pr.port_name + "</b><br>"
                + tier + "<br>"
                + "Region: " + pr.region + "<br>"
                + "Demand Score: " + f"{pr.demand_score:.2f}" + " (" + _demand_tier(pr.demand_score) + ")<br>"
                + "Route Degree: " + str(degree_map.get(pr.locode, 0)) + "<br>"
                + "Vessels: " + str(pr.vessel_count) + "<br>"
                + "Top Products: " + top3
            )
            glow_lats.append(port.lat); glow_lons.append(port.lon)
            glow_sizes.append(size * 2.2); glow_colors.append(color)
            solid_lats.append(port.lat); solid_lons.append(port.lon)
            solid_sizes.append(size); solid_colors.append(color)
            solid_texts.append(hover)

        fig.add_trace(go.Scattergeo(
            lat=glow_lats, lon=glow_lons, mode="markers",
            marker=dict(size=glow_sizes, color=glow_colors, opacity=0.15, symbol="circle"),
            hoverinfo="skip", showlegend=False, name="port_glow",
        ))
        fig.add_trace(go.Scattergeo(
            lat=solid_lats, lon=solid_lons, mode="markers",
            marker=dict(
                size=solid_sizes, color=solid_colors, opacity=0.95,
                symbol="circle", line=dict(width=1.5, color="rgba(255,255,255,0.4)"),
            ),
            hovertemplate="%{text}<extra></extra>",
            text=solid_texts, showlegend=False, name="ports",
        ))

        # Manual legend traces
        for color, label in [
            (C_HIGH, "High Demand / Strong Route"),
            (C_MOD,  "Moderate"),
            (C_LOW,  "Low / Weak"),
        ]:
            fig.add_trace(go.Scattergeo(
                lat=[None], lon=[None], mode="markers",
                marker=dict(size=10, color=color, symbol="circle"),
                name=label, showlegend=True,
            ))

        fig.update_layout(
            height=640,
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            margin=dict(l=0, r=0, t=10, b=0),
            showlegend=True,
            legend=dict(
                bgcolor="rgba(10,15,26,0.75)",
                bordercolor="rgba(255,255,255,0.12)",
                borderwidth=1,
                font=dict(color=_C_TEXT2, size=11),
                x=0.01, y=0.99, xanchor="left", yanchor="top",
            ),
            geo=dict(
                projection_type="orthographic",
                showland=True, landcolor="#1a2235",
                showocean=True, oceancolor="#060d1a",
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.14)",
                showlakes=False,
                showcountries=True, countrycolor="rgba(255,255,255,0.07)",
                bgcolor=C_BG,
                framecolor="rgba(255,255,255,0.08)",
                projection_rotation=dict(lon=60, lat=20, roll=0),
            ),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.2)",
                font=dict(color=_C_TEXT, size=12),
            ),
        )

        st.plotly_chart(fig, use_container_width=True, key="network_globe")

        st.markdown(
            '<div style="display:flex;gap:24px;margin-top:-6px;margin-bottom:8px;'
            'flex-wrap:wrap;padding-left:4px">'
            '<span style="font-size:0.72rem;color:' + _C_TEXT2 + '">'
            '<span style="color:' + C_HIGH + ';font-weight:700">&#9644;</span>'
            ' Strong route (&gt;0.65)</span>'
            '<span style="font-size:0.72rem;color:' + _C_TEXT2 + '">'
            '<span style="color:' + C_MOD + ';font-weight:700">&#9644;</span>'
            ' Moderate route (&gt;0.35)</span>'
            '<span style="font-size:0.72rem;color:' + _C_TEXT2 + '">'
            '<span style="color:' + C_LOW + ';font-weight:700">&#9644;</span>'
            ' Weak route</span>'
            '<span style="font-size:0.72rem;color:' + _C_TEXT2 + '">'
            'Node size = demand × degree &nbsp;·&nbsp; Arc width = freight rate level'
            '</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Error rendering globe")
        st.error("Network globe unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 3: Hub Centrality Ranking
# ══════════════════════════════════════════════════════════════════════════════

def _render_hub_betweenness(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        logger.debug("Rendering hub betweenness centrality")
        if not port_results:
            st.info("No port data for betweenness ranking.")
            return

        betweenness = _compute_betweenness(port_results, route_results)
        port_lookup  = _build_port_lookup(port_results)

        degree_map: dict[str, int] = defaultdict(int)
        for r in route_results:
            degree_map[r.origin_locode] += 1
            degree_map[r.dest_locode]   += 1

        ranked = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:20]
        labels, values, colors, hovers, tier_labels = [], [], [], [], []

        for locode, score in ranked:
            pr     = port_lookup.get(locode)
            flag   = _LOCODE_FLAGS.get(locode, "\U0001f310")
            name   = pr.port_name if pr else locode
            region = pr.region if pr else "Unknown"
            tier   = _HUB_TIERS.get(locode, "Tier 4 — Feeder")
            color  = _REGION_COLORS.get(region, _C_ACCENT)
            degree = degree_map.get(locode, 0)
            labels.append(flag + " " + name)
            values.append(score)
            colors.append(color)
            tier_labels.append(tier)
            hovers.append(
                "<b>" + flag + " " + name + "</b><br>"
                + tier + "<br>"
                + "Betweenness (proxy): " + f"{score:.4f}<br>"
                + "Region: " + region + "<br>"
                + "Route Degree: " + str(degree) + "<br>"
                + "Demand: " + (f"{pr.demand_score:.2f}" if pr else "N/A")
            )

        col_chart, col_table = st.columns([3, 2])

        with col_chart:
            fig = go.Figure(go.Bar(
                x=values, y=labels, orientation="h",
                marker=dict(
                    color=colors,
                    opacity=0.88,
                    line=dict(width=0.5, color="rgba(255,255,255,0.18)"),
                ),
                hovertemplate="%{text}<extra></extra>",
                text=hovers,
                texttemplate="%{x:.4f}",
                textposition="outside",
                textfont=dict(color=_C_TEXT2, size=10),
            ))
            fig.update_layout(
                height=max(400, len(ranked) * 24),
                paper_bgcolor=C_BG,
                plot_bgcolor=_C_SURFACE,
                margin=dict(l=10, r=70, t=10, b=20),
                xaxis=dict(
                    title="Betweenness Centrality (route-touch \u00d7 demand / total routes)",
                    tickfont=dict(color=_C_TEXT2, size=10),
                    gridcolor="rgba(255,255,255,0.05)",
                    zerolinecolor="rgba(255,255,255,0.1)",
                ),
                yaxis=dict(
                    tickfont=dict(color=_C_TEXT, size=11),
                    autorange="reversed",
                    gridcolor="rgba(255,255,255,0.04)",
                ),
                hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                                font=dict(color=_C_TEXT, size=12)),
            )
            st.plotly_chart(fig, use_container_width=True, key="network_betweenness_chart")
            st.markdown(_region_legend_html(), unsafe_allow_html=True)

        with col_table:
            # Tier breakdown summary
            tier_counts: dict[str, int] = defaultdict(int)
            for pr in port_results:
                t = _HUB_TIERS.get(pr.locode, "Tier 4 — Feeder")
                tier_counts[t] += 1

            tier_colors = {
                "Tier 1 — Global":   "#f59e0b",
                "Tier 2 — Regional": "#3b82f6",
                "Tier 3 — Gateway":  "#10b981",
                "Tier 4 — Feeder":   "#64748b",
            }
            st.markdown(
                '<div style="font-size:0.67rem;font-weight:700;color:' + _C_TEXT3 + ';'
                'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px">'
                'Hub Tier Distribution</div>',
                unsafe_allow_html=True,
            )
            for tier_name, tc_color in tier_colors.items():
                count = tier_counts.get(tier_name, 0)
                pct = count / max(1, len(port_results)) * 100
                st.markdown(
                    '<div style="margin-bottom:10px">'
                    '<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
                    '<span style="font-size:0.75rem;color:' + _C_TEXT2 + '">' + tier_name + '</span>'
                    '<span style="font-size:0.75rem;font-weight:700;color:' + tc_color + '">'
                    + str(count) + ' ports</span>'
                    '</div>'
                    '<div style="background:rgba(255,255,255,0.05);border-radius:4px;height:6px">'
                    '<div style="background:' + tc_color + ';width:' + f"{pct:.0f}" + '%;'
                    'height:6px;border-radius:4px;transition:width 0.3s"></div>'
                    '</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

            # Top 5 critical hubs callout
            st.markdown(
                '<div style="margin-top:18px;padding:14px;background:rgba(59,130,246,0.07);'
                'border:1px solid rgba(59,130,246,0.2);border-radius:10px">'
                '<div style="font-size:0.67rem;font-weight:700;color:' + _C_ACCENT + ';'
                'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px">'
                'Top 5 Critical Hubs</div>',
                unsafe_allow_html=True,
            )
            for rank, (locode, score) in enumerate(ranked[:5], 1):
                pr   = port_lookup.get(locode)
                flag = _LOCODE_FLAGS.get(locode, "\U0001f310")
                name = pr.port_name if pr else locode
                st.markdown(
                    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px">'
                    '<span style="font-size:0.78rem;font-weight:900;color:' + _C_ACCENT + ';'
                    'min-width:18px">' + str(rank) + '</span>'
                    '<span style="font-size:0.82rem;color:' + _C_TEXT + '">'
                    + flag + ' ' + name + '</span>'
                    '<span style="font-size:0.72rem;color:' + _C_TEXT3 + ';margin-left:auto">'
                    + f"{score:.3f}" + '</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
            st.markdown('</div>', unsafe_allow_html=True)

    except Exception:
        logger.exception("Error rendering hub betweenness")
        st.error("Hub centrality chart unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 4: Network Flow Sankey
# ══════════════════════════════════════════════════════════════════════════════

def _render_trade_flow_sankey(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    freight_data: dict[str, pd.DataFrame],
) -> None:
    try:
        logger.debug("Rendering trade flow Sankey")
        if not route_results:
            st.info("No route data for Sankey diagram.")
            return

        flow: dict[tuple[str, str], float] = defaultdict(float)
        flow_count: dict[tuple[str, str], int] = defaultdict(int)
        for r in route_results:
            if r.current_rate_usd_feu > 0:
                flow[(r.origin_region, r.dest_region)] += r.current_rate_usd_feu
                flow_count[(r.origin_region, r.dest_region)] += 1

        if not flow:
            st.info("Insufficient rate data for Sankey diagram.")
            return

        origins      = sorted({k[0] for k in flow})
        destinations = sorted({k[1] for k in flow})
        all_nodes    = origins + [d + " \u2192" for d in destinations]
        node_idx     = {n: i for i, n in enumerate(all_nodes)}

        source_idx, target_idx, values, link_colors, link_labels = [], [], [], [], []
        for (orig, dest), vol in sorted(flow.items(), key=lambda x: x[1], reverse=True):
            s = node_idx.get(orig)
            t = node_idx.get(dest + " \u2192")
            if s is None or t is None:
                continue
            source_idx.append(s)
            target_idx.append(t)
            values.append(vol)
            count = flow_count.get((orig, dest), 1)
            rc = _REGION_COLORS.get(orig, "#3b82f6")
            r_, g_, b_ = [int(rc.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
            link_colors.append(f"rgba({r_},{g_},{b_},0.3)")
            link_labels.append(
                orig + " \u2192 " + dest
                + ": $" + f"{vol:,.0f}" + "/FEU avg"
                + " (" + str(count) + " lanes)"
            )

        node_colors = []
        for node in all_nodes:
            base = node.replace(" \u2192", "")
            c    = _REGION_COLORS.get(base, _C_ACCENT)
            if "\u2192" in node:
                r_, g_, b_ = [int(c.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)]
                c = f"rgba({r_},{g_},{b_},0.6)"
            node_colors.append(c)

        fig = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(
                pad=20, thickness=20,
                line=dict(color="rgba(255,255,255,0.12)", width=0.5),
                label=all_nodes,
                color=node_colors,
                hovertemplate="<b>%{label}</b><br>Total Flow: $%{value:,.0f}<extra></extra>",
            ),
            link=dict(
                source=source_idx,
                target=target_idx,
                value=values,
                label=link_labels,
                color=link_colors,
                hovertemplate="%{label}<extra></extra>",
            ),
        ))
        fig.update_layout(
            height=500,
            paper_bgcolor=C_BG,
            font=dict(color=_C_TEXT, size=11, family="Inter, sans-serif"),
            margin=dict(l=10, r=10, t=36, b=10),
            title=dict(
                text="<b>Network Flow Sankey</b> — Origin Region \u2192 Destination Region (USD/FEU volume)",
                font=dict(size=12, color=_C_TEXT),
                x=0.01,
            ),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="network_trade_flow_sankey")
        st.markdown(
            '<div style="font-size:0.74rem;color:' + _C_TEXT3 + ';padding:2px 2px 8px">'
            'Left nodes = origin regions · Right nodes = destination regions · '
            'Link width = cumulative freight rate volume (USD/FEU) · Hover for lane counts'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Error rendering Sankey")
        st.error("Trade flow Sankey unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 5: Connectivity Matrix (port × port heatmap)
# ══════════════════════════════════════════════════════════════════════════════

def _render_connectivity_matrix(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        logger.debug("Rendering connectivity matrix")
        if not route_results or not port_results:
            st.info("No data for connectivity matrix.")
            return

        # Limit to top N ports by degree for readability
        degree_map: dict[str, int] = defaultdict(int)
        for r in route_results:
            degree_map[r.origin_locode] += 1
            degree_map[r.dest_locode]   += 1

        top_ports = sorted(degree_map.keys(), key=lambda x: degree_map[x], reverse=True)[:18]
        port_lookup = _build_port_lookup(port_results)

        # Build connection matrix
        labels = []
        for lc in top_ports:
            pr = port_lookup.get(lc)
            flag = _LOCODE_FLAGS.get(lc, "\U0001f310")
            labels.append(flag + " " + (pr.port_name[:12] if pr else lc))

        n = len(top_ports)
        z = [[0.0] * n for _ in range(n)]
        hover = [[""] * n for _ in range(n)]

        # Strength = avg opportunity score of routes connecting pair
        route_pairs: dict[tuple[str, str], list[float]] = defaultdict(list)
        for r in route_results:
            key = tuple(sorted([r.origin_locode, r.dest_locode]))
            route_pairs[key].append(r.opportunity_score)  # type: ignore[arg-type]

        for i, lc_i in enumerate(top_ports):
            for j, lc_j in enumerate(top_ports):
                if i == j:
                    z[i][j] = 1.0
                    hover[i][j] = "<b>Self</b>"
                    continue
                key = tuple(sorted([lc_i, lc_j]))
                scores = route_pairs.get(key, [])  # type: ignore[arg-type]
                if scores:
                    strength = sum(scores) / len(scores)
                    z[i][j] = strength
                    pr_i = port_lookup.get(lc_i)
                    pr_j = port_lookup.get(lc_j)
                    hover[i][j] = (
                        "<b>" + (pr_i.port_name if pr_i else lc_i) + " \u2194 "
                        + (pr_j.port_name if pr_j else lc_j) + "</b><br>"
                        + "Connections: " + str(len(scores)) + "<br>"
                        + "Avg Opportunity: " + f"{strength:.2f}"
                    )
                else:
                    hover[i][j] = (
                        (port_lookup[lc_i].port_name if lc_i in port_lookup else lc_i)
                        + " \u2194 "
                        + (port_lookup[lc_j].port_name if lc_j in port_lookup else lc_j)
                        + "<br>No direct connection"
                    )

        fig = go.Figure(go.Heatmap(
            z=z, x=labels, y=labels,
            colorscale=[
                [0.0,  "#0a0f1a"],
                [0.01, "#0d1526"],
                [0.35, "#1e3a5f"],
                [0.65, "#1d4ed8"],
                [1.0,  "#10b981"],
            ],
            showscale=True,
            zmin=0, zmax=1,
            hovertemplate="%{text}<extra></extra>",
            text=hover,
            colorbar=dict(
                title=dict(text="Connection Strength", font=dict(color=_C_TEXT2, size=11)),
                tickfont=dict(color=_C_TEXT2),
                thickness=14,
                bgcolor="rgba(0,0,0,0)",
                bordercolor=_C_BORDER,
                tickvals=[0, 0.35, 0.65, 1.0],
                ticktext=["None", "Weak", "Moderate", "Strong"],
            ),
        ))
        fig.update_layout(
            height=520,
            paper_bgcolor=C_BG,
            plot_bgcolor=_C_SURFACE,
            margin=dict(l=10, r=20, t=10, b=100),
            xaxis=dict(tickfont=dict(color=_C_TEXT2, size=10), tickangle=-45,
                       gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(tickfont=dict(color=_C_TEXT2, size=10), autorange="reversed",
                       gridcolor="rgba(255,255,255,0.04)"),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="network_connectivity_matrix")
        st.markdown(
            '<div style="font-size:0.74rem;color:' + _C_TEXT3 + ';padding:2px 2px 8px">'
            'Top 18 ports by route degree · Cell color = avg opportunity score of direct connections · '
            'Diagonal = self-connection (1.0) · Hover for details'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Error rendering connectivity matrix")
        st.error("Connectivity matrix unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 6: Alliance Network Map (carrier × route heatmap)
# ══════════════════════════════════════════════════════════════════════════════

def _render_alliance_network(route_results: list[RouteOpportunity]) -> None:
    try:
        logger.debug("Rendering alliance network map")
        corridors = list(_CORRIDOR_ROUTES.keys())
        carriers  = list(_CARRIER_ROUTES.keys())

        # Build presence matrix
        z = []
        hover = []
        for carrier in carriers:
            row_z, row_h = [], []
            served = set(_CARRIER_ROUTES.get(carrier, []))
            for corridor in corridors:
                cids = _CORRIDOR_ROUTES.get(corridor, [])
                # Find matching routes
                matching = [r for r in route_results if r.route_id in cids]
                present = 1.0 if corridor in served else 0.0
                if matching and corridor in served:
                    avg_opp = sum(r.opportunity_score for r in matching) / len(matching)
                    cell_val = 0.4 + avg_opp * 0.6  # scale 0.4–1.0
                    htxt = (
                        "<b>" + carrier + "</b><br>"
                        + "Corridor: " + corridor + "<br>"
                        + "Lanes: " + str(len(matching)) + "<br>"
                        + "Avg Opportunity: " + f"{avg_opp:.2f}"
                    )
                elif corridor in served:
                    cell_val = 0.4
                    htxt = "<b>" + carrier + "</b><br>" + corridor + ": Operates (no rate data)"
                else:
                    cell_val = 0.0
                    htxt = "<b>" + carrier + "</b><br>" + corridor + ": Does not serve"
                row_z.append(cell_val)
                row_h.append(htxt)
            z.append(row_z)
            hover.append(row_h)

        fig = go.Figure(go.Heatmap(
            z=z, x=corridors, y=carriers,
            colorscale=[
                [0.0, "#0a0f1a"],
                [0.01, "#1a1a2e"],
                [0.4, "#1e3a5f"],
                [0.7, "#2563eb"],
                [1.0, "#10b981"],
            ],
            showscale=True,
            zmin=0, zmax=1,
            hovertemplate="%{text}<extra></extra>",
            text=hover,
            colorbar=dict(
                title=dict(text="Carrier Presence + Opportunity", font=dict(color=_C_TEXT2, size=11)),
                tickfont=dict(color=_C_TEXT2),
                thickness=14,
                bgcolor="rgba(0,0,0,0)",
                bordercolor=_C_BORDER,
                tickvals=[0, 0.4, 0.7, 1.0],
                ticktext=["Not served", "Operates", "Good", "Strong"],
            ),
        ))

        # Annotate cells
        annotations = []
        for i, carrier in enumerate(carriers):
            for j, corridor in enumerate(corridors):
                val = z[i][j]
                if val > 0:
                    annotations.append(dict(
                        x=corridor, y=carrier,
                        text="✓" if val >= 0.4 else "",
                        showarrow=False,
                        font=dict(color="rgba(255,255,255,0.7)", size=13),
                    ))

        fig.update_layout(
            height=420,
            paper_bgcolor=C_BG,
            plot_bgcolor=_C_SURFACE,
            margin=dict(l=10, r=20, t=10, b=60),
            annotations=annotations,
            xaxis=dict(tickfont=dict(color=_C_TEXT2, size=11), tickangle=-20,
                       gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(tickfont=dict(color=_C_TEXT, size=11), autorange="reversed",
                       gridcolor="rgba(255,255,255,0.04)"),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="network_alliance_map")
        st.markdown(
            '<div style="font-size:0.74rem;color:' + _C_TEXT3 + ';padding:2px 2px 8px">'
            'Cell brightness = carrier presence + route opportunity score · '
            'Checkmark = active operator · Hover for corridor details'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Error rendering alliance network")
        st.error("Alliance network map unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 7: Route Redundancy Analysis
# ══════════════════════════════════════════════════════════════════════════════

def _render_route_redundancy(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        logger.debug("Rendering route redundancy analysis")
        if not route_results:
            st.info("No route data for redundancy analysis.")
            return

        port_lookup = _build_port_lookup(port_results)

        # Group routes by corridor (origin_region, dest_region)
        lane_groups: dict[tuple[str, str], list[RouteOpportunity]] = defaultdict(list)
        for r in route_results:
            lane_groups[(r.origin_region, r.dest_region)].append(r)

        # Build redundancy table
        rows = []
        for (orig_r, dest_r), lane_routes in sorted(
            lane_groups.items(), key=lambda x: len(x[1]), reverse=True
        ):
            n_paths = len(lane_routes)
            avg_opp = sum(r.opportunity_score for r in lane_routes) / n_paths
            avg_rate = sum(r.current_rate_usd_feu for r in lane_routes if r.current_rate_usd_feu > 0)
            if avg_rate > 0:
                avg_rate /= sum(1 for r in lane_routes if r.current_rate_usd_feu > 0)
            avg_transit = sum(r.transit_days for r in lane_routes) / n_paths

            # Reliability score: more paths = more reliable, weighted by opportunity
            reliability = min(1.0, (n_paths / 4.0) * 0.5 + avg_opp * 0.5)
            redundancy_label = (
                "High" if n_paths >= 3 else "Medium" if n_paths == 2 else "Single-path"
            )
            rows.append({
                "lane": orig_r + " \u2192 " + dest_r,
                "paths": n_paths,
                "reliability": reliability,
                "redundancy": redundancy_label,
                "avg_opp": avg_opp,
                "avg_rate": avg_rate,
                "avg_transit": avg_transit,
            })

        # Chart: lollipop-style
        lanes     = [r["lane"] for r in rows]
        path_vals = [r["paths"] for r in rows]
        rel_vals  = [r["reliability"] for r in rows]
        bar_colors = [
            C_HIGH if r["paths"] >= 3 else C_MOD if r["paths"] == 2 else C_LOW
            for r in rows
        ]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=path_vals, y=lanes, orientation="h",
            name="Alternative Paths",
            marker=dict(color=bar_colors, opacity=0.75, line=dict(width=0)),
            hovertemplate=(
                "<b>%{y}</b><br>Paths: %{x}<extra></extra>"
            ),
        ))
        fig.add_trace(go.Scatter(
            x=rel_vals, y=lanes,
            mode="markers",
            name="Reliability Score",
            marker=dict(
                size=12, color="#f59e0b",
                symbol="diamond",
                line=dict(width=1, color="rgba(255,255,255,0.4)"),
            ),
            xaxis="x2",
            hovertemplate="<b>%{y}</b><br>Reliability: %{x:.2f}<extra></extra>",
        ))
        fig.update_layout(
            height=max(360, len(rows) * 28),
            paper_bgcolor=C_BG,
            plot_bgcolor=_C_SURFACE,
            margin=dict(l=10, r=10, t=10, b=30),
            xaxis=dict(
                title="Number of Alternative Paths",
                tickfont=dict(color=_C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.05)",
                side="bottom",
            ),
            xaxis2=dict(
                title="Reliability Score",
                tickfont=dict(color=_C_TEXT2, size=10),
                overlaying="x",
                side="top",
                range=[0, 1.1],
                showgrid=False,
            ),
            yaxis=dict(
                tickfont=dict(color=_C_TEXT, size=11),
                autorange="reversed",
                gridcolor="rgba(255,255,255,0.04)",
            ),
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                font=dict(color=_C_TEXT2, size=11),
                x=0.75, y=0.01,
            ),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="network_redundancy_chart")

        # Summary table
        header_style = (
            'padding:9px 14px;font-size:0.63rem;color:' + _C_TEXT3 + ';'
            'text-align:left;text-transform:uppercase;letter-spacing:0.07em'
        )
        rows_html = []
        for i, row in enumerate(rows):
            rclr = C_HIGH if row["paths"] >= 3 else C_MOD if row["paths"] == 2 else C_LOW
            rel_color = _score_color(row["reliability"])
            bg = C_CARD if i % 2 == 0 else "#151e2e"
            rows_html.append(
                '<tr style="background:' + bg + '">'
                '<td style="padding:9px 14px;font-size:0.83rem;font-weight:600;color:' + _C_TEXT + '">'
                + row["lane"] + '</td>'
                '<td style="padding:9px 14px;font-size:0.88rem;font-weight:800;color:' + rclr + ';text-align:center">'
                + str(row["paths"]) + '</td>'
                '<td style="padding:9px 14px;font-size:0.82rem;color:' + rclr + ';text-align:center">'
                + row["redundancy"] + '</td>'
                '<td style="padding:9px 14px;font-size:0.85rem;font-weight:700;color:' + rel_color + ';text-align:center">'
                + f"{row['reliability']:.2f}" + '</td>'
                '<td style="padding:9px 14px;font-size:0.83rem;color:' + _C_TEXT2 + ';text-align:center">'
                + f"{row['avg_opp']:.2f}" + '</td>'
                '<td style="padding:9px 14px;font-size:0.83rem;color:' + _C_TEXT + ';text-align:center">$'
                + f"{row['avg_rate']:,.0f}" + '</td>'
                '</tr>'
            )
        headers = ["Lane", "Alt. Paths", "Redundancy", "Reliability", "Avg Opp", "Avg Rate/FEU"]
        table_html = (
            '<div style="border:1px solid ' + _C_BORDER + ';border-radius:10px;overflow:hidden;margin-top:12px">'
            '<table style="width:100%;border-collapse:collapse;font-family:sans-serif">'
            '<thead><tr style="background:#0d1526">'
            + "".join('<th style="' + header_style + '">' + h + '</th>' for h in headers)
            + '</tr></thead><tbody>' + "".join(rows_html) + '</tbody></table></div>'
        )
        st.markdown(table_html, unsafe_allow_html=True)

    except Exception:
        logger.exception("Error rendering route redundancy")
        st.error("Route redundancy analysis unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 8: Network Disruption Simulation
# ══════════════════════════════════════════════════════════════════════════════

def _render_disruption_simulator(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        logger.debug("Rendering disruption simulator")
        if not port_results or not route_results:
            st.info("Port and route data required for disruption simulation.")
            return

        betweenness = _compute_betweenness(port_results, route_results)
        port_lookup  = _build_port_lookup(port_results)

        # Sort ports by criticality (betweenness)
        ranked_ports = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)
        ranked_locodes = [lc for lc, _ in ranked_ports]

        col_ctrl, col_info = st.columns([2, 3])
        with col_ctrl:
            n_remove = st.slider(
                "Number of top hub ports to remove",
                min_value=1, max_value=min(10, len(ranked_locodes)),
                value=2, key="disruption_slider",
            )

        removed_locodes = set(ranked_locodes[:n_remove])
        removed_names   = []
        for lc in ranked_locodes[:n_remove]:
            pr   = port_lookup.get(lc)
            flag = _LOCODE_FLAGS.get(lc, "\U0001f310")
            removed_names.append(flag + " " + (pr.port_name if pr else lc))

        with col_info:
            st.markdown(
                '<div style="padding:10px 14px;background:rgba(239,68,68,0.07);'
                'border:1px solid rgba(239,68,68,0.25);border-radius:8px;margin-top:4px">'
                '<div style="font-size:0.67rem;font-weight:700;color:' + C_LOW + ';'
                'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px">'
                'Ports Removed</div>'
                '<div style="font-size:0.82rem;color:' + _C_TEXT2 + '">'
                + " &nbsp;·&nbsp; ".join(removed_names)
                + '</div></div>',
                unsafe_allow_html=True,
            )

        # Simulate impact
        severed   = [r for r in route_results
                     if r.origin_locode in removed_locodes or r.dest_locode in removed_locodes]
        surviving = [r for r in route_results
                     if r.origin_locode not in removed_locodes and r.dest_locode not in removed_locodes]

        total_rate_at_risk = sum(r.current_rate_usd_feu for r in severed if r.current_rate_usd_feu > 0)
        surviving_rate     = sum(r.current_rate_usd_feu for r in surviving if r.current_rate_usd_feu > 0)
        pct_affected       = len(severed) / max(1, len(route_results)) * 100
        avg_opp_lost       = sum(r.opportunity_score for r in severed) / max(1, len(severed))
        connectivity_loss  = len(removed_locodes) / max(1, len(port_results)) * 100

        risk_color = C_LOW if pct_affected > 30 else C_MOD if pct_affected > 15 else C_HIGH

        # Impact KPI row
        c1, c2, c3, c4, c5 = st.columns(5)
        impact_cards = [
            ("Lanes Severed",     str(len(severed)),              "Direct routes lost",         risk_color),
            ("Rate Vol. at Risk", "$" + f"{total_rate_at_risk:,.0f}", "USD/FEU on severed lanes", C_LOW),
            ("Network Impact",    f"{pct_affected:.1f}%",          "% of all routes affected",   risk_color),
            ("Connectivity Loss", f"{connectivity_loss:.1f}%",     "% of node pool removed",     C_LOW),
            ("Avg Opp Lost",      f"{avg_opp_lost:.2f}",           "Avg opportunity score lost", C_MOD),
        ]
        for col, (title, value, sub, color) in zip([c1, c2, c3, c4, c5], impact_cards):
            col.markdown(_kpi_card(title, value, sub, color), unsafe_allow_html=True)

        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)

        # Before/After comparison bar chart
        total_orig_rate = sum(r.current_rate_usd_feu for r in route_results if r.current_rate_usd_feu > 0)
        fig_impact = go.Figure()
        fig_impact.add_trace(go.Bar(
            x=["Routes", "Rate Volume ($M)", "Avg Opp Score"],
            y=[len(route_results), total_orig_rate / 1e6,
               sum(r.opportunity_score for r in route_results) / max(1, len(route_results))],
            name="Before Disruption",
            marker=dict(color=C_HIGH, opacity=0.85),
        ))
        fig_impact.add_trace(go.Bar(
            x=["Routes", "Rate Volume ($M)", "Avg Opp Score"],
            y=[len(surviving), surviving_rate / 1e6,
               sum(r.opportunity_score for r in surviving) / max(1, len(surviving))],
            name="After Disruption",
            marker=dict(color=C_LOW, opacity=0.85),
        ))
        fig_impact.update_layout(
            height=300,
            barmode="group",
            paper_bgcolor=C_BG,
            plot_bgcolor=_C_SURFACE,
            margin=dict(l=10, r=10, t=10, b=30),
            xaxis=dict(tickfont=dict(color=_C_TEXT2, size=12), gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(tickfont=dict(color=_C_TEXT2, size=10), gridcolor="rgba(255,255,255,0.05)"),
            legend=dict(font=dict(color=_C_TEXT2, size=11), bgcolor="rgba(0,0,0,0)"),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig_impact, use_container_width=True, key="network_disruption_impact_chart")

        # Severed routes table (top 10 by rate at risk)
        if severed:
            top_severed = sorted(severed, key=lambda x: x.current_rate_usd_feu, reverse=True)[:10]
            rows_html = []
            for i, r in enumerate(top_severed):
                opp_color = _score_color(r.opportunity_score)
                bg = C_CARD if i % 2 == 0 else "#151e2e"
                rows_html.append(
                    '<tr style="background:' + bg + '">'
                    '<td style="padding:9px 14px;font-size:0.83rem;font-weight:700;color:' + _C_TEXT + '">'
                    + r.route_name + '</td>'
                    '<td style="padding:9px 14px;font-size:0.8rem;color:' + _C_TEXT2 + '">'
                    + r.origin_region + " \u2192 " + r.dest_region + '</td>'
                    '<td style="padding:9px 14px;font-size:0.83rem;color:' + _C_TEXT + '">$'
                    + f"{r.current_rate_usd_feu:,.0f}" + '</td>'
                    '<td style="padding:9px 14px;font-size:0.83rem;font-weight:700;color:' + opp_color + '">'
                    + f"{r.opportunity_score:.2f}" + '</td>'
                    '</tr>'
                )
            hs = 'padding:9px 14px;font-size:0.63rem;color:' + _C_TEXT3 + ';text-align:left;text-transform:uppercase;letter-spacing:0.07em'
            st.markdown(
                '<div style="border:1px solid ' + _C_BORDER + ';border-radius:10px;overflow:hidden;margin-top:8px">'
                '<div style="padding:9px 14px;background:#0d1526;font-size:0.65rem;color:' + _C_TEXT3 + ';'
                'text-transform:uppercase;letter-spacing:0.07em;font-weight:700">Top Severed Lanes by Rate Volume</div>'
                '<table style="width:100%;border-collapse:collapse;font-family:sans-serif">'
                '<thead><tr style="background:#0d1526">'
                '<th style="' + hs + '">Route</th>'
                '<th style="' + hs + '">Corridor</th>'
                '<th style="' + hs + '">Rate/FEU</th>'
                '<th style="' + hs + '">Opp Score</th>'
                '</tr></thead><tbody>' + "".join(rows_html) + '</tbody></table></div>',
                unsafe_allow_html=True,
            )

        # Surviving network callout
        st.markdown(
            '<div style="margin-top:12px;padding:10px 14px;background:rgba(16,185,129,0.06);'
            'border:1px solid rgba(16,185,129,0.2);border-radius:8px;font-size:0.8rem;color:' + _C_TEXT2 + '">'
            '<b style="color:' + C_HIGH + '">Surviving network:</b> '
            + str(len(surviving)) + " active lanes &nbsp;·&nbsp; Total rate capacity: $"
            + f"{surviving_rate:,.0f}" + "/FEU remaining"
            '</div>',
            unsafe_allow_html=True,
        )

    except Exception:
        logger.exception("Error rendering disruption simulator")
        st.error("Disruption simulation unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 9: Feeder Network Breakdown
# ══════════════════════════════════════════════════════════════════════════════

def _render_feeder_breakdown(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        logger.debug("Rendering feeder network breakdown")
        if not port_results:
            st.info("No port data for feeder breakdown.")
            return

        port_lookup = _build_port_lookup(port_results)
        degree_map: dict[str, int] = defaultdict(int)
        for r in route_results:
            degree_map[r.origin_locode] += 1
            degree_map[r.dest_locode]   += 1

        mainline, feeder, gateway = [], [], []
        for pr in port_results:
            tier = _HUB_TIERS.get(pr.locode, "Tier 4 — Feeder")
            if "Tier 1" in tier:
                mainline.append(pr)
            elif "Tier 2" in tier:
                gateway.append(pr)
            else:
                feeder.append(pr)

        total = max(1, len(port_results))

        col_pie, col_detail = st.columns([1, 2])

        with col_pie:
            labels_pie   = ["Mainline Hubs (T1)", "Regional Hubs (T2)", "Feeder/Gateway (T3-4)"]
            values_pie   = [len(mainline), len(gateway), len(feeder)]
            colors_pie   = ["#f59e0b", "#3b82f6", "#64748b"]

            fig_pie = go.Figure(go.Pie(
                labels=labels_pie,
                values=values_pie,
                marker=dict(colors=colors_pie, line=dict(color=C_BG, width=2)),
                textinfo="label+percent",
                textfont=dict(color=_C_TEXT, size=11),
                hovertemplate="<b>%{label}</b><br>Ports: %{value}<br>Share: %{percent}<extra></extra>",
                hole=0.45,
            ))
            fig_pie.update_layout(
                height=320,
                paper_bgcolor=C_BG,
                margin=dict(l=10, r=10, t=10, b=10),
                showlegend=False,
                hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                                font=dict(color=_C_TEXT, size=12)),
                annotations=[dict(
                    text=str(len(port_results)) + "<br>ports",
                    x=0.5, y=0.5, showarrow=False,
                    font=dict(color=_C_TEXT, size=13, family="Inter, sans-serif"),
                )],
            )
            st.plotly_chart(fig_pie, use_container_width=True, key="network_feeder_pie")

        with col_detail:
            # Traffic distribution bar
            tiers_data = [
                ("Mainline Hubs (T1)",    mainline, "#f59e0b"),
                ("Regional Hubs (T2)",    gateway,  "#3b82f6"),
                ("Feeder / Gateway (T3+)",feeder,   "#64748b"),
            ]

            for tier_name, tier_ports, tier_color in tiers_data:
                total_demand  = sum(p.demand_score for p in tier_ports)
                total_vessels = sum(p.vessel_count for p in tier_ports)
                total_routes  = sum(degree_map.get(p.locode, 0) for p in tier_ports)
                pct = len(tier_ports) / total * 100

                st.markdown(
                    '<div style="margin-bottom:14px;padding:12px 16px;'
                    'background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);'
                    'border-left:3px solid ' + tier_color + ';border-radius:8px">'
                    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
                    '<span style="font-size:0.82rem;font-weight:700;color:' + tier_color + '">' + tier_name + '</span>'
                    '<span style="font-size:0.75rem;color:' + _C_TEXT3 + '">'
                    + str(len(tier_ports)) + ' ports (' + f"{pct:.0f}" + '%)</span>'
                    '</div>'
                    '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">'
                    '<div style="text-align:center">'
                    '<div style="font-size:1.1rem;font-weight:800;color:' + _C_TEXT + '">'
                    + f"{total_demand:.1f}" + '</div>'
                    '<div style="font-size:0.65rem;color:' + _C_TEXT3 + '">Total Demand</div>'
                    '</div>'
                    '<div style="text-align:center">'
                    '<div style="font-size:1.1rem;font-weight:800;color:' + _C_TEXT + '">'
                    + f"{total_vessels:,}" + '</div>'
                    '<div style="font-size:0.65rem;color:' + _C_TEXT3 + '">Vessels</div>'
                    '</div>'
                    '<div style="text-align:center">'
                    '<div style="font-size:1.1rem;font-weight:800;color:' + _C_TEXT + '">'
                    + str(total_routes) + '</div>'
                    '<div style="font-size:0.65rem;color:' + _C_TEXT3 + '">Route Touches</div>'
                    '</div>'
                    '</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

            # Top feeder ports
            if feeder:
                top_feeders = sorted(feeder, key=lambda p: p.demand_score, reverse=True)[:6]
                st.markdown(
                    '<div style="margin-top:6px;font-size:0.67rem;font-weight:700;'
                    'color:' + _C_TEXT3 + ';text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px">'
                    'Top Feeder Ports</div>',
                    unsafe_allow_html=True,
                )
                feeder_html = '<div style="display:flex;flex-wrap:wrap;gap:8px">'
                for p in top_feeders:
                    flag = _LOCODE_FLAGS.get(p.locode, "\U0001f310")
                    feeder_html += (
                        '<span style="background:#1a2235;border:1px solid ' + _C_BORDER + ';'
                        'border-radius:6px;padding:4px 10px;font-size:0.75rem;color:' + _C_TEXT2 + '">'
                        + flag + ' ' + p.port_name
                        + ' <span style="color:' + _score_color(p.demand_score) + '">'
                        + f"{p.demand_score:.2f}" + '</span></span>'
                    )
                feeder_html += '</div>'
                st.markdown(feeder_html, unsafe_allow_html=True)

    except Exception:
        logger.exception("Error rendering feeder breakdown")
        st.error("Feeder network breakdown unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Section 10: Network Efficiency Over Time
# ══════════════════════════════════════════════════════════════════════════════

def _render_network_efficiency_trend(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    freight_data: dict[str, pd.DataFrame],
) -> None:
    try:
        logger.debug("Rendering network efficiency trend")
        if not freight_data:
            st.info("No historical freight data for efficiency trend.")
            return

        # Aggregate all time-series data across routes to compute network-level metrics
        all_dfs: list[pd.DataFrame] = []
        route_lookup = {r.route_id: r for r in route_results}
        for route_id, df in freight_data.items():
            if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
                continue
            df = df.copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                if "date" in df.columns:
                    df = df.set_index("date")
                elif "timestamp" in df.columns:
                    df = df.set_index("timestamp")
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df["route_id"] = route_id
            route_opp = route_lookup.get(route_id)
            df["opp_score"] = route_opp.opportunity_score if route_opp else 0.5
            all_dfs.append(df[["rate_usd_per_feu", "opp_score"]])

        if not all_dfs:
            st.info("No time-series rate data found across any routes.")
            return

        combined = pd.concat(all_dfs)
        if not isinstance(combined.index, pd.DatetimeIndex):
            st.info("Unable to build time index for efficiency trend.")
            return

        combined = combined.sort_index()
        # Resample weekly
        weekly_avg_rate  = combined["rate_usd_per_feu"].resample("W").mean()
        weekly_max_rate  = combined["rate_usd_per_feu"].resample("W").max()
        weekly_min_rate  = combined["rate_usd_per_feu"].resample("W").min()
        weekly_opp       = combined["opp_score"].resample("W").mean()
        # Route count proxy (number of non-null per week)
        weekly_coverage  = combined["rate_usd_per_feu"].resample("W").count()

        # Efficiency index = normalized avg rate (relative to period max) * avg opp
        rate_max = weekly_avg_rate.max()
        efficiency_idx = (weekly_avg_rate / max(rate_max, 1)) * weekly_opp

        # Drop weeks with too little data
        mask = weekly_coverage >= 2
        weekly_avg_rate  = weekly_avg_rate[mask]
        weekly_max_rate  = weekly_max_rate[mask]
        weekly_min_rate  = weekly_min_rate[mask]
        weekly_opp       = weekly_opp[mask]
        efficiency_idx   = efficiency_idx[mask]

        if len(weekly_avg_rate) < 2:
            st.info("Insufficient data points for efficiency trend chart.")
            return

        fig = go.Figure()

        # Rate band (fill between min and max)
        fig.add_trace(go.Scatter(
            x=list(weekly_max_rate.index) + list(weekly_min_rate.index[::-1]),
            y=list(weekly_max_rate.values) + list(weekly_min_rate.values[::-1]),
            fill="toself",
            fillcolor="rgba(59,130,246,0.08)",
            line=dict(color="rgba(59,130,246,0)"),
            name="Rate Range",
            showlegend=True,
            hoverinfo="skip",
        ))

        # Avg rate line
        fig.add_trace(go.Scatter(
            x=weekly_avg_rate.index,
            y=weekly_avg_rate.values,
            mode="lines",
            name="Avg Freight Rate (USD/FEU)",
            line=dict(color=_C_ACCENT, width=2.5),
            hovertemplate="<b>Avg Rate</b><br>Week: %{x}<br>$%{y:,.0f}/FEU<extra></extra>",
        ))

        # Opportunity score (secondary y)
        fig.add_trace(go.Scatter(
            x=weekly_opp.index,
            y=weekly_opp.values,
            mode="lines",
            name="Avg Opportunity Score",
            line=dict(color=C_HIGH, width=2, dash="dot"),
            yaxis="y2",
            hovertemplate="<b>Avg Opp Score</b><br>Week: %{x}<br>Score: %{y:.3f}<extra></extra>",
        ))

        # Efficiency index
        fig.add_trace(go.Scatter(
            x=efficiency_idx.index,
            y=efficiency_idx.values,
            mode="lines",
            name="Network Efficiency Index",
            line=dict(color="#f59e0b", width=2, dash="dashdot"),
            yaxis="y2",
            hovertemplate="<b>Efficiency Index</b><br>Week: %{x}<br>%{y:.3f}<extra></extra>",
        ))

        fig.update_layout(
            height=380,
            paper_bgcolor=C_BG,
            plot_bgcolor=_C_SURFACE,
            margin=dict(l=10, r=60, t=10, b=40),
            xaxis=dict(
                title="Week",
                tickfont=dict(color=_C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.1)",
            ),
            yaxis=dict(
                title="Avg Rate (USD/FEU)",
                tickfont=dict(color=_C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.05)",
                tickprefix="$", tickformat=",",
            ),
            yaxis2=dict(
                title="Score / Index",
                tickfont=dict(color=_C_TEXT2, size=10),
                overlaying="y",
                side="right",
                range=[0, 1.1],
                showgrid=False,
            ),
            legend=dict(
                font=dict(color=_C_TEXT2, size=11),
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(255,255,255,0.08)",
                x=0.01, y=0.99, xanchor="left", yanchor="top",
            ),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="network_efficiency_trend")

        # Trend summary chips
        if len(weekly_avg_rate) >= 4:
            recent_rate  = weekly_avg_rate.iloc[-4:].mean()
            earlier_rate = weekly_avg_rate.iloc[-8:-4].mean() if len(weekly_avg_rate) >= 8 else weekly_avg_rate.iloc[:4].mean()
            rate_trend_pct = (recent_rate - earlier_rate) / max(1, earlier_rate) * 100
            recent_opp   = weekly_opp.iloc[-4:].mean()
            opp_color    = _score_color(float(recent_opp))

            sign = "+" if rate_trend_pct >= 0 else ""
            rate_trend_color = C_HIGH if rate_trend_pct >= 0 else C_LOW

            st.markdown(
                '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px">'
                '<div style="padding:8px 14px;background:rgba(255,255,255,0.03);'
                'border:1px solid rgba(255,255,255,0.07);border-radius:8px">'
                '<span style="font-size:0.68rem;color:' + _C_TEXT3 + '">4-week rate trend: </span>'
                '<span style="font-size:0.82rem;font-weight:700;color:' + rate_trend_color + '">'
                + sign + f"{rate_trend_pct:.1f}%" + '</span>'
                '</div>'
                '<div style="padding:8px 14px;background:rgba(255,255,255,0.03);'
                'border:1px solid rgba(255,255,255,0.07);border-radius:8px">'
                '<span style="font-size:0.68rem;color:' + _C_TEXT3 + '">Recent avg opportunity: </span>'
                '<span style="font-size:0.82rem;font-weight:700;color:' + opp_color + '">'
                + f"{recent_opp:.2f}" + '</span>'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    except Exception:
        logger.exception("Error rendering network efficiency trend")
        st.error("Network efficiency trend unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Retained: Flow Volume Heatmap (region × region)
# ══════════════════════════════════════════════════════════════════════════════

def _render_heatmap(route_results: list[RouteOpportunity]) -> None:
    try:
        logger.debug("Rendering flow volume heatmap")
        if not route_results:
            st.info("No route data for region heatmap.")
            return

        origin_regions = sorted({r.origin_region for r in route_results})
        dest_regions   = sorted({r.dest_region   for r in route_results})
        grid: dict[tuple[str, str], list[RouteOpportunity]] = defaultdict(list)
        for r in route_results:
            grid[(r.origin_region, r.dest_region)].append(r)

        z_vals, hover_texts, annot_texts = [], [], []
        for orig in origin_regions:
            row_z, row_hover, row_annot = [], [], []
            for dest in dest_regions:
                routes_in_cell = grid.get((orig, dest), [])
                if routes_in_cell:
                    score_sum = sum(r.opportunity_score for r in routes_in_cell)
                    dom       = max(routes_in_cell, key=lambda r: r.opportunity_score)
                    avg_rate  = sum(r.current_rate_usd_feu for r in routes_in_cell) / len(routes_in_cell)
                    hover = (
                        "<b>" + orig + " \u2192 " + dest + "</b><br>"
                        + "Routes: " + str(len(routes_in_cell)) + "<br>"
                        + "Opp Score Sum: " + f"{score_sum:.2f}<br>"
                        + "Top Route: " + dom.route_name + "<br>"
                        + "Avg Rate: $" + f"{avg_rate:,.0f}/FEU"
                    )
                    annot = dom.route_name[:18] + ("…" if len(dom.route_name) > 18 else "")
                else:
                    score_sum = 0.0
                    hover = orig + " \u2192 " + dest + "<br>No direct routes"
                    annot = ""
                row_z.append(score_sum)
                row_hover.append(hover)
                row_annot.append(annot)
            z_vals.append(row_z)
            hover_texts.append(row_hover)
            annot_texts.append(row_annot)

        fig = go.Figure(go.Heatmap(
            z=z_vals, x=dest_regions, y=origin_regions,
            colorscale="Viridis", showscale=True,
            hovertemplate="%{text}<extra></extra>",
            text=hover_texts,
            colorbar=dict(
                title=dict(text="Opp Score Sum", font=dict(color=_C_TEXT2, size=11)),
                tickfont=dict(color=_C_TEXT2), thickness=14,
                bgcolor="rgba(0,0,0,0)", bordercolor=_C_BORDER,
            ),
        ))

        annotations = []
        for i, orig in enumerate(origin_regions):
            for j, dest in enumerate(dest_regions):
                if annot_texts[i][j]:
                    annotations.append(dict(
                        x=dest, y=orig, text=annot_texts[i][j],
                        showarrow=False,
                        font=dict(color=_C_TEXT, size=9, family="monospace"),
                    ))

        fig.update_layout(
            height=360,
            paper_bgcolor=C_BG, plot_bgcolor=_C_SURFACE,
            margin=dict(l=10, r=20, t=10, b=80),
            annotations=annotations,
            xaxis=dict(title="Destination Region", tickfont=dict(color=_C_TEXT2, size=10),
                       tickangle=-30, gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(title="Origin Region", tickfont=dict(color=_C_TEXT2, size=10),
                       gridcolor="rgba(255,255,255,0.04)"),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                            font=dict(color=_C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="network_heatmap")
    except Exception:
        logger.exception("Error rendering heatmap")
        st.error("Flow volume heatmap unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Retained: Corridor Analysis
# ══════════════════════════════════════════════════════════════════════════════

def _render_corridor_analysis(
    route_results: list[RouteOpportunity],
    freight_data: dict[str, pd.DataFrame],
) -> None:
    try:
        logger.debug("Rendering corridor analysis")
        route_lookup = {r.route_id: r for r in route_results}
        corridor_options = ["All", "Trans-Pacific", "Asia-Europe", "Transatlantic",
                            "South America", "Intra-Asia"]

        col_sel, _spacer = st.columns([1, 3])
        with col_sel:
            chosen = st.selectbox("Select Corridor", corridor_options, key="corridor_select")

        route_ids = [r.route_id for r in route_results] if chosen == "All" else _CORRIDOR_ROUTES.get(chosen, [])
        corridor_routes = [route_lookup[rid] for rid in route_ids if rid in route_lookup]

        if not corridor_routes:
            st.warning("No route data available for this corridor.")
            return

        rates      = [r.current_rate_usd_feu for r in corridor_routes if r.current_rate_usd_feu > 0]
        avg_rate   = sum(rates) / len(rates) if rates else 0.0
        opp_scores = [r.opportunity_score for r in corridor_routes]
        health_score = sum(opp_scores) / len(opp_scores) if opp_scores else 0.0
        rate_changes = [r.rate_pct_change_30d for r in corridor_routes]
        avg_change   = sum(rate_changes) / len(rate_changes) if rate_changes else 0.0
        chokepoints  = _CHOKEPOINTS.get(chosen, _CHOKEPOINTS["All"])
        health_color = _score_color(health_score)

        sign = "+" if avg_change >= 0 else ""
        c1, c2, c3, c4 = st.columns(4)
        cards = [
            ("Avg Freight Rate", "$" + f"{avg_rate:,.0f}", "USD per FEU", _C_ACCENT),
            ("Corridor Health", f"{health_score:.2f}", "Weighted avg opp score", health_color),
            ("30d Rate Change", sign + f"{avg_change*100:.1f}%", "Avg across corridor", C_HIGH if avg_change >= 0 else C_LOW),
            ("Active Routes", str(len(corridor_routes)), "Shipping lanes", _C_CONV),
        ]
        for col, (title, value, sub, color) in zip([c1, c2, c3, c4], cards):
            col.markdown(_kpi_card(title, value, sub, color), unsafe_allow_html=True)

        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

        # Rate time series
        route_colors = ["#3b82f6","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4",
                        "#84cc16","#f97316","#e11d48","#0ea5e9","#a855f7","#22c55e"]
        fig_ts = go.Figure()
        has_ts = False
        for idx, route_opp in enumerate(corridor_routes):
            df = freight_data.get(route_opp.route_id)
            if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
                continue
            df = df.copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                if "date" in df.columns:
                    df = df.set_index("date")
                elif "timestamp" in df.columns:
                    df = df.set_index("timestamp")
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            color = route_colors[idx % len(route_colors)]
            fig_ts.add_trace(go.Scatter(
                x=df.index, y=df["rate_usd_per_feu"],
                mode="lines", name=route_opp.route_name,
                line=dict(color=color, width=2),
                hovertemplate=(
                    "<b>" + route_opp.route_name + "</b><br>"
                    "Date: %{x}<br>Rate: $%{y:,.0f}/FEU<extra></extra>"
                ),
            ))
            has_ts = True

        if has_ts:
            fig_ts.update_layout(
                height=300, paper_bgcolor=C_BG, plot_bgcolor=_C_SURFACE,
                margin=dict(l=10, r=10, t=10, b=40),
                xaxis=dict(title="Date", tickfont=dict(color=_C_TEXT2, size=10),
                           gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(title="Rate (USD/FEU)", tickfont=dict(color=_C_TEXT2, size=10),
                           gridcolor="rgba(255,255,255,0.05)", tickprefix="$", tickformat=","),
                legend=dict(font=dict(color=_C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)"),
                hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.2)",
                                font=dict(color=_C_TEXT, size=12)),
            )
            st.markdown(
                '<div style="font-size:0.75rem;font-weight:700;color:' + _C_TEXT3 + ';'
                'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:8px">'
                'Rate Time Series — Corridor Routes</div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(fig_ts, use_container_width=True, key="network_timeseries")
        else:
            st.info("No historical rate data for this corridor.")

        # Chokepoints
        choke_html = (
            '<div style="font-size:0.75rem;font-weight:700;color:' + _C_TEXT3 + ';'
            'text-transform:uppercase;letter-spacing:0.07em;margin:14px 0 8px">Key Chokepoints</div>'
            '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px">'
        )
        for cp in chokepoints:
            choke_html += (
                '<span style="background:#1a2235;border:1px solid rgba(245,158,11,0.4);'
                'color:#f59e0b;border-radius:6px;padding:4px 12px;font-size:0.78rem;font-weight:600">'
                + cp + '</span>'
            )
        choke_html += '</div>'
        st.markdown(choke_html, unsafe_allow_html=True)

        # Per-route table
        rows_html = []
        hs = 'padding:9px 14px;font-size:0.64rem;color:' + _C_TEXT3 + ';text-align:left;text-transform:uppercase;letter-spacing:0.07em'
        for i, r in enumerate(sorted(corridor_routes, key=lambda x: x.opportunity_score, reverse=True)):
            opp_color    = _score_color(r.opportunity_score)
            change_sign  = "+" if r.rate_pct_change_30d >= 0 else ""
            change_color = C_HIGH if r.rate_pct_change_30d >= 0 else C_LOW
            bg           = C_CARD if i % 2 == 0 else "#151e2e"
            arrow        = "▲" if r.rate_trend == "Rising" else ("▼" if r.rate_trend == "Falling" else "●")
            rows_html.append(
                '<tr style="background:' + bg + '">'
                '<td style="padding:9px 14px;font-size:0.84rem;font-weight:700;color:' + _C_TEXT + '">' + r.route_name + '</td>'
                '<td style="padding:9px 14px;font-size:0.8rem;color:' + _C_TEXT2 + '">' + r.origin_region + ' \u2192 ' + r.dest_region + '</td>'
                '<td style="padding:9px 14px;font-size:0.84rem;color:' + _C_TEXT + '">$' + f"{r.current_rate_usd_feu:,.0f}" + '</td>'
                '<td style="padding:9px 14px;font-size:0.84rem;color:' + change_color + '">' + arrow + ' ' + change_sign + f"{r.rate_pct_change_30d*100:.1f}%" + '</td>'
                '<td style="padding:9px 14px;font-size:0.84rem;font-weight:700;color:' + opp_color + '">' + f"{r.opportunity_score:.2f}" + ' (' + r.opportunity_label + ')</td>'
                '<td style="padding:9px 14px;font-size:0.8rem;color:' + _C_TEXT2 + '">' + str(r.transit_days) + 'd</td>'
                '</tr>'
            )
        st.markdown(
            '<div style="border:1px solid ' + _C_BORDER + ';border-radius:10px;overflow:hidden">'
            '<table style="width:100%;border-collapse:collapse;font-family:sans-serif">'
            '<thead><tr style="background:#0d1526">'
            '<th style="' + hs + '">Route</th>'
            '<th style="' + hs + '">Corridor</th>'
            '<th style="' + hs + '">Rate/FEU</th>'
            '<th style="' + hs + '">30d Change</th>'
            '<th style="' + hs + '">Opportunity</th>'
            '<th style="' + hs + '">Transit</th>'
            '</tr></thead><tbody>' + "".join(rows_html) + '</tbody></table></div>',
            unsafe_allow_html=True,
        )

        # CSV download
        sorted_routes = sorted(corridor_routes, key=lambda x: x.opportunity_score, reverse=True)
        csv_rows = [{
            "Route": r.route_name,
            "Corridor": r.origin_region + " \u2192 " + r.dest_region,
            "Origin Locode": r.origin_locode,
            "Dest Locode": r.dest_locode,
            "Rate USD/FEU": r.current_rate_usd_feu,
            "30d Rate Change": round(r.rate_pct_change_30d * 100, 2),
            "Rate Trend": r.rate_trend,
            "Opportunity Score": round(r.opportunity_score, 4),
            "Opportunity Label": r.opportunity_label,
            "Transit Days": r.transit_days,
        } for r in sorted_routes]
        corridor_slug = chosen.lower().replace(" ", "_").replace("-", "_")
        st.download_button(
            label="Download corridor data CSV",
            data=pd.DataFrame(csv_rows).to_csv(index=False).encode("utf-8"),
            file_name=f"corridor_{corridor_slug}.csv",
            mime="text/csv",
            key="network_corridor_csv",
        )
    except Exception:
        logger.exception("Error rendering corridor analysis")
        st.error("Corridor analysis unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Retained: Network Metrics Table
# ══════════════════════════════════════════════════════════════════════════════

def _render_network_metrics_table(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        logger.debug("Rendering network metrics table")
        if not port_results:
            st.info("No port data for network metrics table.")
            return

        port_lookup = _build_port_lookup(port_results)
        betweenness = _compute_betweenness(port_results, route_results)
        degree_map: dict[str, int] = defaultdict(int)
        neighbors: dict[str, set] = defaultdict(set)
        for r in route_results:
            degree_map[r.origin_locode] += 1
            degree_map[r.dest_locode]   += 1
            neighbors[r.origin_locode].add(r.dest_locode)
            neighbors[r.dest_locode].add(r.origin_locode)

        rows = []
        for pr in port_results:
            locode = pr.locode
            degree = degree_map.get(locode, 0)
            btwn   = betweenness.get(locode, 0.0)
            nbrs   = neighbors.get(locode, set())
            if len(nbrs) < 2:
                clustering = 0.0
            else:
                connected_pairs = 0
                nbr_list = list(nbrs)
                for i in range(len(nbr_list)):
                    for j in range(i + 1, len(nbr_list)):
                        if nbr_list[j] in neighbors.get(nbr_list[i], set()):
                            connected_pairs += 1
                max_pairs = len(nbr_list) * (len(nbr_list) - 1) / 2
                clustering = connected_pairs / max_pairs if max_pairs > 0 else 0.0
            flag = _LOCODE_FLAGS.get(locode, "\U0001f310")
            tier = _HUB_TIERS.get(locode, "Tier 4 — Feeder")
            rows.append({
                "flag": flag, "port": pr.port_name, "locode": locode,
                "region": pr.region, "tier": tier, "degree": degree,
                "betweenness": round(btwn, 4), "clustering": round(clustering, 3),
                "demand": round(pr.demand_score, 2),
            })

        rows.sort(key=lambda x: x["betweenness"], reverse=True)

        hs = 'padding:10px 14px;font-size:0.63rem;color:' + _C_TEXT3 + ';text-align:left;text-transform:uppercase;letter-spacing:0.07em'
        headers = ["Port", "Tier", "Region", "Degree", "Betweenness", "Clustering", "Demand"]
        rows_html = []
        for i, row in enumerate(rows):
            bg           = C_CARD if i % 2 == 0 else "#151e2e"
            region_color = _REGION_COLORS.get(row["region"], _C_ACCENT)
            deg_color    = C_HIGH if row["degree"] >= 4 else C_MOD if row["degree"] >= 2 else _C_TEXT2
            btwn_color   = C_HIGH if row["betweenness"] >= 0.05 else C_MOD if row["betweenness"] >= 0.02 else _C_TEXT2
            clst_color   = C_HIGH if row["clustering"] >= 0.5 else C_MOD if row["clustering"] >= 0.2 else _C_TEXT2
            tier_color   = {"Tier 1 — Global": "#f59e0b", "Tier 2 — Regional": "#3b82f6",
                            "Tier 3 — Gateway": "#10b981"}.get(row["tier"], _C_TEXT3)
            rows_html.append(
                '<tr style="background:' + bg + '">'
                '<td style="padding:10px 14px;font-size:0.83rem;font-weight:700;color:' + _C_TEXT + '">'
                + row["flag"] + ' ' + row["port"]
                + '<span style="font-size:0.68rem;color:' + _C_TEXT3 + ';margin-left:6px">' + row["locode"] + '</span>'
                '</td>'
                '<td style="padding:10px 14px;font-size:0.72rem;color:' + tier_color + '">' + row["tier"] + '</td>'
                '<td style="padding:10px 14px">'
                '<span style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);'
                'border-radius:4px;padding:1px 7px;font-size:0.71rem;color:' + region_color + '">'
                + row["region"] + '</span></td>'
                '<td style="padding:10px 14px;font-size:0.88rem;font-weight:800;color:' + deg_color + ';text-align:center">' + str(row["degree"]) + '</td>'
                '<td style="padding:10px 14px;font-size:0.84rem;font-family:monospace;color:' + btwn_color + ';text-align:center">' + f"{row['betweenness']:.4f}" + '</td>'
                '<td style="padding:10px 14px;font-size:0.84rem;font-family:monospace;color:' + clst_color + ';text-align:center">' + f"{row['clustering']:.3f}" + '</td>'
                '<td style="padding:10px 14px;font-size:0.85rem;font-weight:800;color:' + _score_color(row["demand"]) + ';text-align:center">' + f"{row['demand']:.2f}" + '</td>'
                '</tr>'
            )

        st.markdown(
            '<div style="border:1px solid ' + _C_BORDER + ';border-radius:12px;overflow:hidden">'
            '<table style="width:100%;border-collapse:collapse;font-family:sans-serif">'
            '<thead><tr style="background:#0d1526">'
            + "".join('<th style="' + hs + '">' + h + '</th>' for h in headers)
            + '</tr></thead><tbody>' + "".join(rows_html) + '</tbody></table></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="font-size:0.73rem;color:' + _C_TEXT3 + ';margin-top:6px">'
            'Betweenness = route-touch \u00d7 demand / total routes (proxy). '
            'Clustering = fraction of neighbour pairs sharing a direct route. '
            'Sorted by betweenness descending.'
            '</div>',
            unsafe_allow_html=True,
        )

        # CSV download
        buf = _io.StringIO()
        writer = _csv.DictWriter(buf, fieldnames=["port","locode","region","tier","degree","betweenness","clustering","demand"])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in ["port","locode","region","tier","degree","betweenness","clustering","demand"]})
        st.download_button(
            label="Download network metrics CSV",
            data=buf.getvalue().encode("utf-8"),
            file_name="network_metrics.csv",
            mime="text/csv",
            key="network_metrics_csv_download",
        )
    except Exception:
        logger.exception("Error rendering network metrics table")
        st.error("Network metrics table unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
#  Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def render(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    freight_data: dict[str, pd.DataFrame],
    trade_data: dict | None = None,
) -> None:
    """Render the Shipping Network Visualization tab.

    Args:
        port_results:  List of PortDemandResult (one per tracked port).
        route_results: List of RouteOpportunity (one per tracked route).
        freight_data:  Dict route_id -> DataFrame with rate_usd_per_feu column.
        trade_data:    Optional extra trade data (reserved for future use).
    """
    logger.info(
        "Rendering network tab — ports={} routes={}",
        len(port_results), len(route_results),
    )

    st.header("Shipping Network Intelligence")

    if not port_results and not route_results:
        st.info("No network data available. Check API credentials in .env and click Refresh.")
        return

    if not port_results:
        st.warning("Port data unavailable — globe and centrality rankings will be skipped.")
    if not route_results:
        st.warning(
            "Route data unavailable — heatmap, corridor analysis, and arc overlays "
            "will be skipped. Showing port nodes only."
        )

    # ── 1. Network Overview Hero ───────────────────────────────────────────────
    _section_header("Network Overview")
    _render_network_hero(port_results, route_results)

    # ── 2. Shipping Network Graph (Globe) ──────────────────────────────────────
    _section_header("Shipping Network Graph — Ports & Routes")
    st.markdown(
        '<div style="font-size:0.81rem;color:' + _C_TEXT3 + ';margin-bottom:10px">'
        + str(len(port_results)) + " ports &nbsp;·&nbsp; "
        + str(len(route_results)) + " trade routes &nbsp;·&nbsp; "
        "Node size = demand × degree &nbsp;·&nbsp; Arc width = freight rate"
        '</div>',
        unsafe_allow_html=True,
    )
    if port_results:
        _render_globe(port_results, route_results)
    else:
        st.info("Globe skipped — no port data loaded.")

    # ── 3. Hub Centrality Ranking ──────────────────────────────────────────────
    _section_header("Hub Centrality Ranking — Betweenness Analysis")
    st.markdown(
        '<div style="font-size:0.81rem;color:' + _C_TEXT3 + ';margin-bottom:10px">'
        "Ports ranked by proxy betweenness centrality (route-touch \u00d7 demand). "
        "Higher = more critical network intermediary."
        '</div>',
        unsafe_allow_html=True,
    )
    _render_hub_betweenness(port_results, route_results)

    # ── 4. Network Flow Sankey ──────────────────────────────────────────────────
    _section_header("Network Flow Sankey — Origin Region \u2192 Hub \u2192 Destination")
    _render_trade_flow_sankey(port_results, route_results, freight_data)

    # ── 5. Connectivity Matrix ─────────────────────────────────────────────────
    _section_header("Connectivity Matrix — Port \u00d7 Port Connection Strength")
    _render_connectivity_matrix(port_results, route_results)

    # ── 6. Alliance Network Map ────────────────────────────────────────────────
    _section_header("Alliance Network Map — Carrier \u00d7 Corridor Coverage")
    _render_alliance_network(route_results)

    # ── 7. Route Redundancy Analysis ───────────────────────────────────────────
    _section_header("Route Redundancy Analysis — Alternative Paths & Reliability")
    _render_route_redundancy(port_results, route_results)

    # ── 8. Network Disruption Simulation ──────────────────────────────────────
    _section_header("Network Disruption Simulation — Hub Removal Impact")
    st.markdown(
        '<div style="font-size:0.81rem;color:' + _C_TEXT3 + ';margin-bottom:10px">'
        "Use the slider to remove top hub ports and see the cascading impact on "
        "network connectivity, freight volume, and lane coverage."
        '</div>',
        unsafe_allow_html=True,
    )
    _render_disruption_simulator(port_results, route_results)

    # ── 9. Feeder Network Breakdown ────────────────────────────────────────────
    _section_header("Feeder Network Breakdown — Mainline vs Feeder Traffic")
    _render_feeder_breakdown(port_results, route_results)

    # ── 10. Network Efficiency Over Time ──────────────────────────────────────
    _section_header("Network Efficiency Over Time — Key Metrics Trend")
    _render_network_efficiency_trend(port_results, route_results, freight_data)

    # ── Flow Volume Heatmap ────────────────────────────────────────────────────
    _section_header("Flow Volume Heatmap — Region \u00d7 Region Opportunity")
    _render_heatmap(route_results)

    # ── Corridor Analysis ──────────────────────────────────────────────────────
    _section_header("Route Corridor Analysis")
    if route_results:
        _render_corridor_analysis(route_results, freight_data)
    else:
        st.info("Corridor analysis skipped — no route data loaded.")

    # ── Network Metrics Table ──────────────────────────────────────────────────
    _section_header("Network Metrics Table — Degree, Betweenness & Clustering")
    _render_network_metrics_table(port_results, route_results)

    logger.info("Network tab render complete")
