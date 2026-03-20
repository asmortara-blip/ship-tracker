"""Shipping Network Visualization Tab — ultra-detailed interactive globe + analytics."""
from __future__ import annotations

import math
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

# ── Region colours for bar chart / legend ─────────────────────────────────────
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

# ── Country → flag emoji (ISO 3166-1 alpha-2 via port locode prefix) ──────────
_LOCODE_FLAGS: dict[str, str] = {
    "CNSHA": "\U0001f1e8\U0001f1f3",  # CN 🇨🇳
    "CNNBO": "\U0001f1e8\U0001f1f3",
    "CNSZN": "\U0001f1e8\U0001f1f3",
    "CNTAO": "\U0001f1e8\U0001f1f3",
    "CNTXG": "\U0001f1e8\U0001f1f3",
    "SGSIN": "\U0001f1f8\U0001f1ec",  # SG 🇸🇬
    "KRPUS": "\U0001f1f0\U0001f1f7",  # KR 🇰🇷
    "HKHKG": "\U0001f1ed\U0001f1f0",  # HK 🇭🇰
    "MYPKG": "\U0001f1f2\U0001f1fe",  # MY 🇲🇾
    "MYTPP": "\U0001f1f2\U0001f1fe",
    "NLRTM": "\U0001f1f3\U0001f1f1",  # NL 🇳🇱
    "AEJEA": "\U0001f1e6\U0001f1ea",  # AE 🇦🇪
    "BEANR": "\U0001f1e7\U0001f1ea",  # BE 🇧🇪
    "TWKHH": "\U0001f1f9\U0001f1fc",  # TW 🇹🇼
    "USLAX": "\U0001f1fa\U0001f1f8",  # US 🇺🇸
    "USLGB": "\U0001f1fa\U0001f1f8",
    "USNYC": "\U0001f1fa\U0001f1f8",
    "USSAV": "\U0001f1fa\U0001f1f8",
    "DEHAM": "\U0001f1e9\U0001f1ea",  # DE 🇩🇪
    "MATNM": "\U0001f1f2\U0001f1e6",  # MA 🇲🇦
    "JPYOK": "\U0001f1ef\U0001f1f5",  # JP 🇯🇵
    "LKCMB": "\U0001f1f1\U0001f1f0",  # LK 🇱🇰
    "GRPIR": "\U0001f1ec\U0001f1f7",  # GR 🇬🇷
    "GBFXT": "\U0001f1ec\U0001f1e7",  # GB 🇬🇧
    "BRSAO": "\U0001f1e7\U0001f1f7",  # BR 🇧🇷
}

# ── Corridor → route ids mapping ───────────────────────────────────────────────
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

# Chokepoints per corridor
_CHOKEPOINTS: dict[str, list[str]] = {
    "Trans-Pacific":  ["Malacca Strait", "Panama Canal", "Aleutian Pass"],
    "Asia-Europe":    ["Suez Canal", "Malacca Strait", "Bab-el-Mandeb", "Strait of Hormuz"],
    "Transatlantic":  ["English Channel", "GIUK Gap"],
    "South America":  ["Panama Canal", "Strait of Magellan", "Cape of Good Hope"],
    "Intra-Asia":     ["Malacca Strait", "Lombok Strait", "Taiwan Strait"],
    "All":            ["Suez Canal", "Panama Canal", "Malacca Strait", "Bab-el-Mandeb",
                       "Strait of Hormuz", "English Channel", "Strait of Magellan"],
}


# ── Internal helpers ───────────────────────────────────────────────────────────

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
    """Return (lats, lons) for a great-circle-approximating curved arc."""
    if n < 2:
        return [lat1, lat2], [lon1, lon2]
    # Use spherical linear interpolation (slerp) via xyz for globe curvature
    def to_xyz(lat_deg: float, lon_deg: float):
        lat_r = math.radians(lat_deg)
        lon_r = math.radians(lon_deg)
        return (
            math.cos(lat_r) * math.cos(lon_r),
            math.cos(lat_r) * math.sin(lon_r),
            math.sin(lat_r),
        )

    def to_latlon(x: float, y: float, z: float):
        lat_r = math.atan2(z, math.sqrt(x * x + y * y))
        lon_r = math.atan2(y, x)
        return math.degrees(lat_r), math.degrees(lon_r)

    x1, y1, z1 = to_xyz(lat1, lon1)
    x2, y2, z2 = to_xyz(lat2, lon2)

    dot = max(-1.0, min(1.0, x1 * x2 + y1 * y2 + z1 * z2))
    omega = math.acos(dot)

    lats, lons = [], []
    for i in range(n):
        t = i / (n - 1)
        if omega < 1e-6:
            xi, yi, zi = (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, z1 + (z2 - z1) * t)
        else:
            sin_o = math.sin(omega)
            w1 = math.sin((1 - t) * omega) / sin_o
            w2 = math.sin(t * omega) / sin_o
            xi, yi, zi = w1 * x1 + w2 * x2, w1 * y1 + w2 * y2, w1 * z1 + w2 * z2
        la, lo = to_latlon(xi, yi, zi)
        lats.append(la)
        lons.append(lo)
    return lats, lons


def _build_port_lookup(port_results: list[PortDemandResult]) -> dict[str, PortDemandResult]:
    return {r.locode: r for r in port_results}


def _route_width(rate: float, min_r: float, max_r: float) -> float:
    """Map rate to line width 1.5 – 5.0."""
    if max_r <= min_r:
        return 2.5
    norm = (rate - min_r) / (max_r - min_r)
    return 1.5 + norm * 3.5


def _section_header(label: str) -> None:
    st.markdown(
        '<div style="display:flex; align-items:center; gap:12px; margin:28px 0 16px 0">'
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        '<span style="font-size:0.65rem; color:#475569; text-transform:uppercase; '
        'letter-spacing:0.14em; font-weight:700">' + label + "</span>"
        '<div style="flex:1; height:1px; background:rgba(255,255,255,0.06)"></div>'
        "</div>",
        unsafe_allow_html=True,
    )


# ── Section 1: 3-D Animated Trade Network Globe ────────────────────────────────

def _render_globe(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    logger.debug("Rendering 3D trade network globe")

    port_lookup = _build_port_lookup(port_results)

    # Collect rate range for arc width scaling
    rates = [r.current_rate_usd_feu for r in route_results if r.current_rate_usd_feu > 0]
    min_rate = min(rates) if rates else 0.0
    max_rate = max(rates) if rates else 1.0

    fig = go.Figure()

    # ── Arc traces (one trace per route) ──────────────────────────────────────
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
            dest_port.lat, dest_port.lon,
            n=40,
        )

        arc_color = _score_color(route_opp.opportunity_score)
        # Make low-opportunity arcs dimmer
        if route_opp.opportunity_score < 0.35:
            arc_color = "rgba(239,68,68,0.35)"
        elif route_opp.opportunity_score < 0.65:
            arc_color = "rgba(245,158,11,0.65)"
        else:
            arc_color = "rgba(16,185,129,0.85)"

        lw = _route_width(route_opp.current_rate_usd_feu, min_rate, max_rate)

        rate_30d = route_opp.rate_pct_change_30d * 100
        change_sign = "+" if rate_30d >= 0 else ""
        hover_text = (
            "<b>" + route_opp.route_name + "</b><br>"
            + "Rate: $" + f"{route_opp.current_rate_usd_feu:,.0f}" + "/FEU<br>"
            + "30d Change: " + change_sign + f"{rate_30d:.1f}%" + "<br>"
            + "Opportunity: " + f"{route_opp.opportunity_score:.2f}"
            + " (" + route_opp.opportunity_label + ")<br>"
            + "Transit: " + str(route_opp.transit_days) + " days"
        )

        fig.add_trace(
            go.Scattergeo(
                lat=arc_lats,
                lon=arc_lons,
                mode="lines",
                line=dict(color=arc_color, width=lw),
                hovertemplate=hover_text + "<extra></extra>",
                name=route_opp.route_name,
                showlegend=False,
            )
        )

    # ── Port glow traces (larger, transparent — pulse layer) ──────────────────
    glow_lats, glow_lons, glow_sizes, glow_colors, glow_texts = [], [], [], [], []
    for pr in port_results:
        port = PORTS_BY_LOCODE.get(pr.locode)
        if port is None:
            continue
        size = pr.demand_score * 30 + 10
        tier = _demand_tier(pr.demand_score)
        color = _score_color(pr.demand_score)

        top3 = ", ".join(p["category"] for p in pr.top_products[:3]) if pr.top_products else "N/A"
        hover = (
            "<b>" + pr.port_name + "</b><br>"
            + "Region: " + pr.region + "<br>"
            + "Demand Score: " + f"{pr.demand_score:.2f}" + " (" + tier + ")<br>"
            + "Vessels: " + str(pr.vessel_count) + "<br>"
            + "Top Products: " + top3
        )

        glow_lats.append(port.lat)
        glow_lons.append(port.lon)
        glow_sizes.append(size * 1.8)
        glow_colors.append(color)
        glow_texts.append(hover)

    fig.add_trace(
        go.Scattergeo(
            lat=glow_lats,
            lon=glow_lons,
            mode="markers",
            marker=dict(
                size=glow_sizes,
                color=glow_colors,
                opacity=0.18,
                symbol="circle",
            ),
            hoverinfo="skip",
            showlegend=False,
            name="port_glow",
        )
    )

    # ── Port solid traces ──────────────────────────────────────────────────────
    solid_lats, solid_lons, solid_sizes, solid_colors, solid_texts = [], [], [], [], []
    for pr in port_results:
        port = PORTS_BY_LOCODE.get(pr.locode)
        if port is None:
            continue
        size = pr.demand_score * 30 + 10
        color = _score_color(pr.demand_score)
        tier = _demand_tier(pr.demand_score)
        top3 = ", ".join(p["category"] for p in pr.top_products[:3]) if pr.top_products else "N/A"
        hover = (
            "<b>" + pr.port_name + "</b><br>"
            + "Region: " + pr.region + "<br>"
            + "Demand Score: " + f"{pr.demand_score:.2f}" + " (" + tier + ")<br>"
            + "Vessels: " + str(pr.vessel_count) + "<br>"
            + "Top Products: " + top3
        )
        solid_lats.append(port.lat)
        solid_lons.append(port.lon)
        solid_sizes.append(size)
        solid_colors.append(color)
        solid_texts.append(hover)

    fig.add_trace(
        go.Scattergeo(
            lat=solid_lats,
            lon=solid_lons,
            mode="markers",
            marker=dict(
                size=solid_sizes,
                color=solid_colors,
                opacity=0.95,
                symbol="circle",
                line=dict(width=1.2, color="rgba(255,255,255,0.35)"),
            ),
            hovertemplate="%{text}<extra></extra>",
            text=solid_texts,
            showlegend=False,
            name="ports",
        )
    )

    # ── Legend (manual) for tiers ──────────────────────────────────────────────
    for color, label in [(C_HIGH, "High Demand"), (C_MOD, "Moderate Demand"), (C_LOW, "Low Demand")]:
        fig.add_trace(
            go.Scattergeo(
                lat=[None], lon=[None],
                mode="markers",
                marker=dict(size=10, color=color, symbol="circle"),
                name=label,
                showlegend=True,
            )
        )

    fig.update_layout(
        height=600,
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(10,15,26,0.7)",
            bordercolor="rgba(255,255,255,0.12)",
            borderwidth=1,
            font=dict(color=_C_TEXT2, size=11),
            x=0.01, y=0.99,
            xanchor="left", yanchor="top",
        ),
        geo=dict(
            projection_type="orthographic",
            showland=True,
            landcolor="#1a2235",
            showocean=True,
            oceancolor="#0d1117",
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.12)",
            showlakes=False,
            showcountries=True,
            countrycolor="rgba(255,255,255,0.06)",
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

    # Legend note for arc colors
    st.markdown(
        '<div style="display:flex; gap:20px; margin-top:-8px; margin-bottom:8px; '
        'flex-wrap:wrap; padding-left:4px">'
        '<span style="font-size:0.72rem; color:#94a3b8">'
        '<span style="color:#10b981; font-weight:700">&#9644;</span> Strong route (&gt;0.65)</span>'
        '<span style="font-size:0.72rem; color:#94a3b8">'
        '<span style="color:#f59e0b; font-weight:700">&#9644;</span> Moderate route (&gt;0.35)</span>'
        '<span style="font-size:0.72rem; color:#94a3b8">'
        '<span style="color:#ef4444; font-weight:700">&#9644;</span> Weak route</span>'
        '<span style="font-size:0.72rem; color:#94a3b8">Arc width = freight rate level</span>'
        "</div>",
        unsafe_allow_html=True,
    )


# ── Section 2: Flow Volume Heatmap ────────────────────────────────────────────

def _render_heatmap(route_results: list[RouteOpportunity]) -> None:
    logger.debug("Rendering flow volume heatmap")

    if not route_results:
        st.info("No route data available to build the region heatmap.")
        return

    # Collect unique regions from routes
    origin_regions = sorted({r.origin_region for r in route_results})
    dest_regions   = sorted({r.dest_region   for r in route_results})

    # Build grid: sum of opportunity scores + metadata
    grid: dict[tuple[str, str], list[RouteOpportunity]] = defaultdict(list)
    for r in route_results:
        grid[(r.origin_region, r.dest_region)].append(r)

    z_vals: list[list[float]] = []
    hover_texts: list[list[str]] = []
    annot_texts: list[list[str]] = []

    for orig in origin_regions:
        row_z, row_hover, row_annot = [], [], []
        for dest in dest_regions:
            routes_in_cell = grid.get((orig, dest), [])
            if routes_in_cell:
                score_sum = sum(r.opportunity_score for r in routes_in_cell)
                dom = max(routes_in_cell, key=lambda r: r.opportunity_score)
                avg_rate = sum(r.current_rate_usd_feu for r in routes_in_cell) / len(routes_in_cell)
                hover = (
                    "<b>" + orig + " → " + dest + "</b><br>"
                    + "Routes: " + str(len(routes_in_cell)) + "<br>"
                    + "Opp Score Sum: " + f"{score_sum:.2f}" + "<br>"
                    + "Top Route: " + dom.route_name + "<br>"
                    + "Avg Rate: $" + f"{avg_rate:,.0f}" + "/FEU"
                )
                dom_short = dom.route_name[:18] + ("…" if len(dom.route_name) > 18 else "")
                annot = dom_short + "<br>$" + f"{avg_rate/1000:.0f}" + "k"
            else:
                score_sum = 0.0
                hover = orig + " → " + dest + "<br>No direct routes"
                annot = ""
            row_z.append(score_sum)
            row_hover.append(hover)
            row_annot.append(annot)
        z_vals.append(row_z)
        hover_texts.append(row_hover)
        annot_texts.append(row_annot)

    fig = go.Figure(
        go.Heatmap(
            z=z_vals,
            x=dest_regions,
            y=origin_regions,
            colorscale="Viridis",
            showscale=True,
            hovertemplate="%{text}<extra></extra>",
            text=hover_texts,
            colorbar=dict(
                title=dict(text="Opp Score", font=dict(color=_C_TEXT2, size=11)),
                tickfont=dict(color=_C_TEXT2),
                thickness=14,
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(255,255,255,0.08)",
            ),
        )
    )

    # Annotations for each cell
    annotations = []
    for i, orig in enumerate(origin_regions):
        for j, dest in enumerate(dest_regions):
            text = annot_texts[i][j]
            if text:
                annotations.append(
                    dict(
                        x=dest, y=orig,
                        text=text,
                        showarrow=False,
                        font=dict(color=_C_TEXT, size=9, family="monospace"),
                        align="center",
                    )
                )

    fig.update_layout(
        height=350,
        paper_bgcolor=C_BG,
        plot_bgcolor=_C_SURFACE,
        margin=dict(l=10, r=20, t=10, b=80),
        annotations=annotations,
        xaxis=dict(
            title="Destination Region",
            tickfont=dict(color=_C_TEXT2, size=10),
            tickangle=-30,
            gridcolor="rgba(255,255,255,0.04)",
        ),
        yaxis=dict(
            title="Origin Region",
            tickfont=dict(color=_C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.2)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="network_heatmap")


# ── Section 3: Port Centrality Rankings ───────────────────────────────────────

def _render_centrality(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    logger.debug("Rendering port centrality rankings")

    if not port_results:
        st.info("No port data available for centrality rankings.")
        return

    port_lookup = _build_port_lookup(port_results)

    # Count routes touching each port locode
    route_count: dict[str, int] = defaultdict(int)
    for r in route_results:
        route_count[r.origin_locode] += 1
        route_count[r.dest_locode]   += 1

    # Centrality score = routes_touching * demand_score
    centrality: list[tuple[str, float, str, str]] = []
    for pr in port_results:
        routes_n = route_count.get(pr.locode, 0)
        score    = routes_n * pr.demand_score
        flag     = _LOCODE_FLAGS.get(pr.locode, "\U0001f310")
        label    = flag + " " + pr.port_name
        centrality.append((label, score, pr.region, pr.locode))

    centrality.sort(key=lambda x: x[1], reverse=True)

    labels  = [c[0] for c in centrality]
    values  = [c[1] for c in centrality]
    regions = [c[2] for c in centrality]
    colors  = [_REGION_COLORS.get(r, _C_ACCENT) for r in regions]

    # Build hover texts
    hover_texts = []
    for i, (label, score, region, locode) in enumerate(centrality):
        pr = port_lookup.get(locode)
        routes_n = route_count.get(locode, 0)
        demand_s = pr.demand_score if pr else 0.0
        hover_texts.append(
            "<b>" + label + "</b><br>"
            + "Centrality: " + f"{score:.2f}" + "<br>"
            + "Routes: " + str(routes_n) + "<br>"
            + "Demand: " + f"{demand_s:.2f}" + "<br>"
            + "Region: " + region
        )

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker=dict(
                color=colors,
                opacity=0.88,
                line=dict(width=0.5, color="rgba(255,255,255,0.2)"),
            ),
            hovertemplate="%{text}<extra></extra>",
            text=hover_texts,
            texttemplate="%{x:.1f}",
            textposition="outside",
            textfont=dict(color=_C_TEXT2, size=10),
        )
    )

    # Animate with frames for an initial sweep effect
    fig.update_layout(
        height=max(420, len(centrality) * 20),
        paper_bgcolor=C_BG,
        plot_bgcolor=_C_SURFACE,
        margin=dict(l=10, r=60, t=10, b=20),
        xaxis=dict(
            title="Centrality Score  (routes × demand)",
            tickfont=dict(color=_C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
        ),
        yaxis=dict(
            tickfont=dict(color=_C_TEXT, size=11),
            autorange="reversed",
            gridcolor="rgba(255,255,255,0.04)",
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.2)",
            font=dict(color=_C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="network_centrality")

    # Region colour legend
    legend_html = '<div style="display:flex; flex-wrap:wrap; gap:10px; margin-top:-4px; margin-bottom:8px">'
    seen = set()
    for region, color in _REGION_COLORS.items():
        if region in seen:
            continue
        seen.add(region)
        legend_html += (
            '<span style="font-size:0.72rem; color:' + _C_TEXT2 + '">'
            '<span style="display:inline-block; width:10px; height:10px; border-radius:50%; '
            "background:" + color + '; margin-right:4px; vertical-align:middle"></span>'
            + region + "</span>"
        )
    legend_html += "</div>"
    st.markdown(legend_html, unsafe_allow_html=True)


# ── Section 4: Route Corridor Analysis ────────────────────────────────────────

def _render_corridor_analysis(
    route_results: list[RouteOpportunity],
    freight_data: dict[str, pd.DataFrame],
) -> None:
    logger.debug("Rendering corridor analysis")

    route_lookup = {r.route_id: r for r in route_results}

    corridor_options = ["All", "Trans-Pacific", "Asia-Europe", "Transatlantic",
                        "South America", "Intra-Asia"]

    col_sel, _col_spacer = st.columns([1, 3])
    with col_sel:
        chosen = st.selectbox(
            "Select Corridor",
            corridor_options,
            key="corridor_select",
        )

    # Resolve which route ids to show
    if chosen == "All":
        route_ids = [r.route_id for r in route_results]
    else:
        route_ids = _CORRIDOR_ROUTES.get(chosen, [])

    corridor_routes = [route_lookup[rid] for rid in route_ids if rid in route_lookup]

    if not corridor_routes:
        st.warning("No route data available for this corridor.")
        return

    # ── KPI row ───────────────────────────────────────────────────────────────
    rates = [r.current_rate_usd_feu for r in corridor_routes if r.current_rate_usd_feu > 0]
    avg_rate = sum(rates) / len(rates) if rates else 0.0

    opp_scores = [r.opportunity_score for r in corridor_routes]
    health_score = sum(opp_scores) / len(opp_scores) if opp_scores else 0.0

    rate_changes = [r.rate_pct_change_30d for r in corridor_routes]
    avg_change   = sum(rate_changes) / len(rate_changes) if rate_changes else 0.0

    chokepoints = _CHOKEPOINTS.get(chosen, _CHOKEPOINTS["All"])

    health_color = _score_color(health_score)

    def kpi_card(title: str, value: str, sub: str, color: str) -> str:
        return (
            '<div style="background:' + C_CARD + '; border:1px solid ' + _C_BORDER + '; '
            "border-top:3px solid " + color + "; border-radius:10px; "
            'padding:16px 18px; text-align:center">'
            '<div style="font-size:0.65rem; font-weight:700; color:' + _C_TEXT3 + '; '
            'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px">' + title + "</div>"
            '<div style="font-size:1.8rem; font-weight:800; color:' + color + '; line-height:1.1; '
            'margin:4px 0">' + value + "</div>"
            '<div style="font-size:0.72rem; color:' + _C_TEXT3 + '">' + sub + "</div>"
            "</div>"
        )

    sign = "+" if avg_change >= 0 else ""
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(
        kpi_card(
            "Avg Freight Rate",
            "$" + f"{avg_rate:,.0f}",
            "USD per FEU (corridor avg)",
            _C_ACCENT,
        ),
        unsafe_allow_html=True,
    )
    c2.markdown(
        kpi_card(
            "Corridor Health",
            f"{health_score:.2f}",
            "Weighted avg opportunity score",
            health_color,
        ),
        unsafe_allow_html=True,
    )
    c3.markdown(
        kpi_card(
            "30d Rate Change",
            sign + f"{avg_change * 100:.1f}%",
            "Avg across corridor routes",
            C_HIGH if avg_change >= 0 else C_LOW,
        ),
        unsafe_allow_html=True,
    )
    c4.markdown(
        kpi_card(
            "Routes",
            str(len(corridor_routes)),
            "Active shipping lanes",
            _C_CONV,
        ),
        unsafe_allow_html=True,
    )

    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

    # ── Rate time series multi-line ───────────────────────────────────────────
    fig_ts = go.Figure()

    route_colors = [
        "#3b82f6", "#10b981", "#f59e0b", "#ef4444",
        "#8b5cf6", "#06b6d4", "#84cc16", "#f97316",
        "#e11d48", "#0ea5e9", "#a855f7", "#22c55e",
    ]

    has_ts_data = False
    for idx, route_opp in enumerate(corridor_routes):
        df = freight_data.get(route_opp.route_id)
        if df is None or df.empty:
            continue
        if "rate_usd_per_feu" not in df.columns:
            continue

        df = df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            if "date" in df.columns:
                df = df.set_index("date")
            elif "timestamp" in df.columns:
                df = df.set_index("timestamp")
        # Strip timezone info to prevent mixing tz-aware and tz-naive datetimes,
        # which causes a TypeError in pandas comparisons and Plotly rendering.
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        color = route_colors[idx % len(route_colors)]
        rate_col = "rate_usd_per_feu"

        fig_ts.add_trace(
            go.Scatter(
                x=df.index,
                y=df[rate_col],
                mode="lines",
                name=route_opp.route_name,
                line=dict(color=color, width=2),
                hovertemplate=(
                    "<b>" + route_opp.route_name + "</b><br>"
                    + "Date: %{x}<br>"
                    + "Rate: $%{y:,.0f}/FEU<extra></extra>"
                ),
            )
        )
        has_ts_data = True

    if has_ts_data:
        fig_ts.update_layout(
            height=300,
            paper_bgcolor=C_BG,
            plot_bgcolor=_C_SURFACE,
            margin=dict(l=10, r=10, t=10, b=40),
            xaxis=dict(
                title="Date",
                tickfont=dict(color=_C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.1)",
            ),
            yaxis=dict(
                title="Rate (USD/FEU)",
                tickfont=dict(color=_C_TEXT2, size=10),
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.1)",
                tickprefix="$",
                tickformat=",",
            ),
            legend=dict(
                font=dict(color=_C_TEXT2, size=10),
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(255,255,255,0.08)",
            ),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.2)",
                font=dict(color=_C_TEXT, size=12),
            ),
        )
        st.markdown(
            '<div style="font-size:0.78rem; font-weight:700; color:' + _C_TEXT3 + '; '
            'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">'
            "Rate Time Series — Corridor Routes</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig_ts, use_container_width=True, key="network_timeseries")
    else:
        st.info("No historical rate data available for this corridor.")

    # ── Chokepoints ───────────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.78rem; font-weight:700; color:' + _C_TEXT3 + '; '
        'text-transform:uppercase; letter-spacing:0.07em; margin:16px 0 8px 0">'
        "Key Chokepoints</div>",
        unsafe_allow_html=True,
    )

    choke_html = '<div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px">'
    for cp in chokepoints:
        choke_html += (
            '<span style="background:#1a2235; border:1px solid rgba(245,158,11,0.4); '
            "color:#f59e0b; border-radius:6px; padding:4px 12px; "
            'font-size:0.78rem; font-weight:600">' + cp + "</span>"
        )
    choke_html += "</div>"
    st.markdown(choke_html, unsafe_allow_html=True)

    # ── Per-route summary table ────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.78rem; font-weight:700; color:' + _C_TEXT3 + '; '
        'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">'
        "Corridor Route Detail</div>",
        unsafe_allow_html=True,
    )

    rows_html = []
    for i, r in enumerate(sorted(corridor_routes, key=lambda x: x.opportunity_score, reverse=True)):
        opp_color = _score_color(r.opportunity_score)
        change_sign = "+" if r.rate_pct_change_30d >= 0 else ""
        change_color = C_HIGH if r.rate_pct_change_30d >= 0 else C_LOW
        row_bg = C_CARD if i % 2 == 0 else "#151e2e"
        trend_arrow = "▲" if r.rate_trend == "Rising" else ("▼" if r.rate_trend == "Falling" else "●")
        rows_html.append(
            '<tr style="background:' + row_bg + '">'
            '<td style="padding:10px 14px; font-size:0.85rem; font-weight:700; color:' + _C_TEXT + '">'
            + r.route_name + "</td>"
            '<td style="padding:10px 14px; font-size:0.85rem; color:' + _C_TEXT2 + '">'
            + r.origin_region + " → " + r.dest_region + "</td>"
            '<td style="padding:10px 14px; font-size:0.85rem; color:' + _C_TEXT + '">$'
            + f"{r.current_rate_usd_feu:,.0f}" + "</td>"
            '<td style="padding:10px 14px; font-size:0.85rem; color:' + change_color + '">'
            + trend_arrow + " " + change_sign + f"{r.rate_pct_change_30d * 100:.1f}%" + "</td>"
            '<td style="padding:10px 14px; font-size:0.85rem; font-weight:700; color:' + opp_color + '">'
            + f"{r.opportunity_score:.2f}" + " (" + r.opportunity_label + ")</td>"
            '<td style="padding:10px 14px; font-size:0.82rem; color:' + _C_TEXT2 + '">'
            + str(r.transit_days) + "d</td>"
            "</tr>"
        )

    table_html = (
        '<div style="border:1px solid ' + _C_BORDER + '; border-radius:10px; overflow:hidden">'
        '<table style="width:100%; border-collapse:collapse; font-family:sans-serif">'
        '<thead><tr style="background:#0d1526">'
        '<th style="padding:10px 14px; font-size:0.66rem; color:' + _C_TEXT3 + '; '
        'text-align:left; text-transform:uppercase; letter-spacing:0.07em">Route</th>'
        '<th style="padding:10px 14px; font-size:0.66rem; color:' + _C_TEXT3 + '; '
        'text-align:left; text-transform:uppercase; letter-spacing:0.07em">Corridor</th>'
        '<th style="padding:10px 14px; font-size:0.66rem; color:' + _C_TEXT3 + '; '
        'text-align:left; text-transform:uppercase; letter-spacing:0.07em">Rate/FEU</th>'
        '<th style="padding:10px 14px; font-size:0.66rem; color:' + _C_TEXT3 + '; '
        'text-align:left; text-transform:uppercase; letter-spacing:0.07em">30d Change</th>'
        '<th style="padding:10px 14px; font-size:0.66rem; color:' + _C_TEXT3 + '; '
        'text-align:left; text-transform:uppercase; letter-spacing:0.07em">Opportunity</th>'
        '<th style="padding:10px 14px; font-size:0.66rem; color:' + _C_TEXT3 + '; '
        'text-align:left; text-transform:uppercase; letter-spacing:0.07em">Transit</th>'
        "</tr></thead>"
        "<tbody>" + "".join(rows_html) + "</tbody>"
        "</table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)

    # ── CSV download for corridor route detail ────────────────────────────────
    sorted_routes = sorted(corridor_routes, key=lambda x: x.opportunity_score, reverse=True)
    csv_rows = [
        {
            "Route": r.route_name,
            "Corridor": r.origin_region + " → " + r.dest_region,
            "Origin Locode": r.origin_locode,
            "Dest Locode": r.dest_locode,
            "Rate USD/FEU": r.current_rate_usd_feu,
            "30d Rate Change": round(r.rate_pct_change_30d * 100, 2),
            "Rate Trend": r.rate_trend,
            "Opportunity Score": round(r.opportunity_score, 4),
            "Opportunity Label": r.opportunity_label,
            "Transit Days": r.transit_days,
        }
        for r in sorted_routes
    ]
    df_csv = pd.DataFrame(csv_rows)
    corridor_slug = chosen.lower().replace(" ", "_").replace("-", "_")
    st.download_button(
        label="Download corridor data CSV",
        data=df_csv.to_csv(index=False).encode("utf-8"),
        file_name=f"corridor_{corridor_slug}.csv",
        mime="text/csv",
        key="network_corridor_csv",
    )


# ── Public entry point ─────────────────────────────────────────────────────────

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
        st.info(
            "No network data available. Check API credentials in .env and click Refresh."
        )
        return

    if not port_results:
        st.warning("Port data unavailable — globe and centrality rankings will be skipped.")

    if not route_results:
        st.warning(
            "Route data unavailable — heatmap, corridor analysis, and arc overlays "
            "will be skipped. Showing port nodes only."
        )

    # ── Section 1: 3D Globe ────────────────────────────────────────────────────
    _section_header("3D Trade Network — All Ports & Routes")

    st.markdown(
        '<div style="font-size:0.82rem; color:' + _C_TEXT3 + '; margin-bottom:10px">'
        + str(len(port_results)) + " ports &nbsp;·&nbsp; "
        + str(len(route_results)) + " trade routes &nbsp;·&nbsp; "
        "Node size = demand score &nbsp;·&nbsp; Arc width = freight rate"
        "</div>",
        unsafe_allow_html=True,
    )

    if port_results:
        _render_globe(port_results, route_results)
    else:
        st.info("Globe skipped — no port data loaded.")

    # ── Section 2: Heatmap ────────────────────────────────────────────────────
    _section_header("Flow Volume Heatmap — Region × Region Opportunity")
    _render_heatmap(route_results)

    # ── Section 3: Centrality ─────────────────────────────────────────────────
    _section_header("Port Network Centrality Rankings")
    st.markdown(
        '<div style="font-size:0.82rem; color:' + _C_TEXT3 + '; margin-bottom:10px">'
        "Centrality = (number of routes touching port) \u00d7 demand score"
        "</div>",
        unsafe_allow_html=True,
    )
    _render_centrality(port_results, route_results)

    # ── Section 4: Corridor Analysis ──────────────────────────────────────────
    _section_header("Route Corridor Analysis")
    if route_results:
        _render_corridor_analysis(route_results, freight_data)
    else:
        st.info("Corridor analysis skipped — no route data loaded.")

    logger.info("Network tab render complete")
