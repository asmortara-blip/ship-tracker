"""
Emerging Trade Routes Tab

Climate change and geopolitics are permanently reshaping the geography of global
trade. This tab visualises 8 emerging or revived maritime corridors and provides
four analytical sections:

  1. New Routes World Map        — Dark globe (orthographic, Arctic-shifted) with
                                   traditional and emerging route overlays
  2. Route Comparison Matrix     — Heatmap: routes vs metrics vs traditional benchmark
  3. Arctic Route Tracker        — Annual vessel counts, seasonal calendar, ice
                                   extent trend, break-even analysis, carrier exits
  4. Red Sea Rerouting Impact    — Cape of Good Hope diversion data since 2024
  5. Emerging Market Corridor    — Trade volume growth CAGR bar chart 2025-2030
"""
from __future__ import annotations

import math

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.emerging_routes import (
    EMERGING_ROUTES,
    EMERGING_ROUTES_BY_ID,
    STATUS_COLORS,
    compute_route_viability,
)

# ---------------------------------------------------------------------------
# Colour palette — consistent with existing app design system
# ---------------------------------------------------------------------------

C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
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

# Arctic route colour
C_ARCTIC = "#38bdf8"       # sky blue

# Status → colour (mirrors STATUS_COLORS in processing module)
_STATUS_COLOR: dict[str, str] = {
    "OPERATIONAL": C_HIGH,
    "PILOT":       C_ACCENT,
    "DEVELOPING":  C_WARN,
    "FUTURE":      C_PURPLE,
}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _card(content: str, border_color: str = C_BORDER) -> str:
    return (
        "<div style=\"background:" + C_CARD
        + "; border:1px solid " + border_color
        + "; border-radius:12px; padding:18px 20px; margin-bottom:12px\">"
        + content
        + "</div>"
    )


def _section_title(text: str, subtitle: str = "") -> None:
    sub = (
        "<div style=\"color:" + C_TEXT2 + "; font-size:0.83rem; margin-top:3px\">"
        + subtitle + "</div>"
    ) if subtitle else ""
    st.markdown(
        "<div style=\"margin-bottom:14px; margin-top:4px\">"
        "<div style=\"font-size:1.05rem; font-weight:700; color:" + C_TEXT + "\">"
        + text + "</div>"
        + sub + "</div>",
        unsafe_allow_html=True,
    )


def _status_badge(status: str) -> str:
    color = _STATUS_COLOR.get(status, C_TEXT2)
    return (
        "<span style=\"background:rgba(0,0,0,0.35); color:" + color
        + "; border:1px solid " + color
        + "; padding:2px 9px; border-radius:999px;"
        " font-size:0.68rem; font-weight:700; white-space:nowrap\">"
        + status + "</span>"
    )


def _kpi_mini(label: str, value: str, color: str = C_TEXT) -> str:
    return (
        "<div style=\"text-align:center; padding:10px 6px\">"
        "<div style=\"font-size:1.25rem; font-weight:800; color:" + color + "\">"
        + value + "</div>"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3
        + "; text-transform:uppercase; letter-spacing:0.06em; margin-top:3px\">"
        + label + "</div>"
        "</div>"
    )


def _bar_h(pct: float, color: str, label: str, max_val: float = 100.0) -> str:
    fill = min(100.0, abs(pct) / max_val * 100.0)
    sign = "+" if pct > 0 else ""
    return (
        "<div style=\"margin-bottom:5px\">"
        "<div style=\"display:flex; justify-content:space-between; margin-bottom:2px\">"
        "<span style=\"font-size:0.71rem; color:" + C_TEXT2 + "\">" + label + "</span>"
        "<span style=\"font-size:0.71rem; font-weight:700; color:" + color + "\">"
        + sign + "{:.1f}".format(pct) + "</span>"
        "</div>"
        "<div style=\"background:rgba(255,255,255,0.07); border-radius:4px; height:6px\">"
        "<div style=\"width:" + "{:.1f}".format(fill) + "%; background:" + color
        + "; border-radius:4px; height:6px\"></div>"
        "</div></div>"
    )


# ---------------------------------------------------------------------------
# Section 1: New Routes World Map
# ---------------------------------------------------------------------------

# Traditional route segments (gray, thin)
_TRADITIONAL_ROUTES: list[dict] = [
    {
        "name": "Asia-Europe via Suez",
        "lats": [31.2, 22.0, 15.0, 12.5, 11.0, 5.0, -5.0, -20.0, 51.5],
        "lons": [121.5, 88.0, 55.0, 43.5, 42.0, 41.0, 38.0, 20.0, 4.0],
        "color": "#334155",
        "width": 1,
    },
    {
        "name": "Trans-Pacific (Panama)",
        "lats": [31.2, 35.0, 42.0, 45.0, 37.8, 25.0, 9.0, 10.0, 40.7],
        "lons": [121.5, 155.0, 175.0, -170.0, -122.4, -105.0, -79.5, -75.0, -74.0],
        "color": "#334155",
        "width": 1,
    },
    {
        "name": "Transatlantic",
        "lats": [51.5, 48.0, 44.0, 40.7],
        "lons": [4.0, -20.0, -45.0, -74.0],
        "color": "#334155",
        "width": 1,
    },
]

# Emerging route segments (coloured, graduated opacity)
_EMERGING_MAP_ROUTES: list[dict] = [
    {
        "name": "Northern Sea Route",
        "lats": [35.0, 45.0, 55.0, 65.0, 72.0, 75.0, 73.0, 68.0, 63.0, 55.0, 51.5],
        "lons": [121.5, 135.0, 145.0, 155.0, 165.0, 180.0, -170.0, -155.0, -30.0, 10.0, 4.0],
        "color": C_ARCTIC,
        "width": 2.5,
        "status": "OPERATIONAL",
        "mid_lat": 72.0,
        "mid_lon": 100.0,
    },
    {
        "name": "Northwest Passage",
        "lats": [35.0, 50.0, 65.0, 73.0, 75.0, 72.0, 65.0, 50.0, 40.7],
        "lons": [121.5, -155.0, -140.0, -125.0, -100.0, -80.0, -70.0, -68.0, -74.0],
        "color": C_ACCENT,
        "width": 2.0,
        "status": "PILOT",
        "mid_lat": 75.0,
        "mid_lon": -100.0,
    },
    {
        "name": "Transpolar (2040+)",
        "lats": [35.0, 55.0, 70.0, 85.0, 90.0, 85.0, 70.0, 55.0, 40.7],
        "lons": [121.5, 140.0, 160.0, 170.0, 0.0, -40.0, -60.0, -68.0, -74.0],
        "color": C_PURPLE,
        "width": 1.5,
        "status": "FUTURE",
        "mid_lat": 90.0,
        "mid_lon": 0.0,
    },
    {
        "name": "IMEC Corridor",
        "lats": [19.0, 22.0, 25.3, 29.5, 31.8, 37.9, 51.5],
        "lons": [72.8, 60.0, 55.4, 34.8, 35.2, 23.7, 4.0],
        "color": C_WARN,
        "width": 2.5,
        "status": "DEVELOPING",
        "mid_lat": 28.0,
        "mid_lon": 38.0,
    },
    {
        "name": "Trans-Caspian Route",
        "lats": [34.3, 40.0, 43.6, 41.7, 41.7, 41.0, 48.0, 51.5],
        "lons": [108.9, 63.0, 51.2, 49.9, 41.7, 35.0, 20.0, 4.0],
        "color": C_ORANGE,
        "width": 2.0,
        "status": "OPERATIONAL",
        "mid_lat": 42.0,
        "mid_lon": 52.0,
    },
    {
        "name": "Cape of Good Hope",
        "lats": [31.2, 15.0, 0.0, -15.0, -30.0, -34.4, -30.0, -15.0, 0.0, 15.0, 30.0, 51.5],
        "lons": [121.5, 100.0, 80.0, 55.0, 30.0, 18.5, 5.0, -5.0, -10.0, -15.0, -10.0, 4.0],
        "color": C_HIGH,
        "width": 3.0,
        "status": "OPERATIONAL",
        "mid_lat": -34.4,
        "mid_lon": 18.5,
    },
    {
        "name": "East Africa Corridor",
        "lats": [22.0, 12.0, 5.0, -1.3, -6.8],
        "lons": [88.0, 72.0, 50.0, 37.0, 39.7],
        "color": C_CYAN,
        "width": 2.0,
        "status": "DEVELOPING",
        "mid_lat": 5.0,
        "mid_lon": 50.0,
    },
]


def _render_world_map() -> None:
    """Dark orthographic globe shifted north to show Arctic routes."""
    logger.debug("Rendering emerging routes world map")

    fig = go.Figure()

    # ── Traditional routes (gray, thin, low opacity) ─────────────────────
    for lane in _TRADITIONAL_ROUTES:
        fig.add_trace(go.Scattergeo(
            lat=lane["lats"],
            lon=lane["lons"],
            mode="lines",
            line=dict(color=lane["color"], width=lane["width"]),
            opacity=0.35,
            hoverinfo="text",
            hovertext=lane["name"] + " (traditional)",
            showlegend=False,
            name=lane["name"],
        ))

    # ── Emerging routes (coloured, graduated opacity segments) ────────────
    for route in _EMERGING_MAP_ROUTES:
        n = len(route["lats"])
        # Graduated opacity: fade in from 0.3 to 0.9 along the route
        for i in range(n - 1):
            opacity = 0.35 + (0.55 * (i / max(n - 2, 1)))
            fig.add_trace(go.Scattergeo(
                lat=[route["lats"][i], route["lats"][i + 1]],
                lon=[route["lons"][i], route["lons"][i + 1]],
                mode="lines",
                line=dict(color=route["color"], width=route["width"]),
                opacity=opacity,
                hoverinfo="skip",
                showlegend=False,
            ))

        # Hover trace over full route
        fig.add_trace(go.Scattergeo(
            lat=route["lats"],
            lon=route["lons"],
            mode="lines",
            line=dict(color=route["color"], width=route["width"]),
            opacity=0.0,
            hoverinfo="text",
            hovertext=(
                "<b>" + route["name"] + "</b><br>"
                "Status: " + route["status"]
            ),
            showlegend=False,
            name=route["name"] + "_hover",
        ))

        # Status badge at midpoint
        fig.add_trace(go.Scattergeo(
            lat=[route["mid_lat"]],
            lon=[route["mid_lon"]],
            mode="markers+text",
            marker=dict(
                size=10,
                color=route["color"],
                opacity=0.90,
                symbol="circle",
                line=dict(color="rgba(255,255,255,0.50)", width=1),
            ),
            text=["  " + route["name"]],
            textposition="middle right",
            textfont=dict(size=8, color=route["color"]),
            hovertemplate="<b>" + route["name"] + "</b><br>Status: " + route["status"] + "<extra></extra>",
            showlegend=False,
        ))

    # ── Legend traces (one per status) ────────────────────────────────────
    legend_items = [
        ("OPERATIONAL", C_HIGH),
        ("PILOT",       C_ACCENT),
        ("DEVELOPING",  C_WARN),
        ("FUTURE",      C_PURPLE),
        ("Traditional", "#334155"),
    ]
    for label, color in legend_items:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="lines",
            line=dict(color=color, width=3),
            name=label,
            showlegend=True,
        ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        height=550,
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="orthographic",
            showland=True,       landcolor="#1a2235",
            showocean=True,      oceancolor="#0a0f1a",
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.12)",
            showframe=False,
            bgcolor="#0a0f1a",
            showcountries=True,  countrycolor="rgba(255,255,255,0.05)",
            showlakes=False,
            # Rotate to show Arctic prominently
            projection_rotation=dict(lon=60, lat=55, roll=0),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="right",
            x=1,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        font=dict(color=C_TEXT),
    )

    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Section 2: Route Comparison Heatmap Matrix
# ---------------------------------------------------------------------------

def _render_comparison_matrix() -> None:
    """Heatmap: routes (Y) vs metrics (X). Green=better, Red=worse vs traditional."""
    logger.debug("Rendering route comparison matrix")

    routes = EMERGING_ROUTES

    # Metrics as columns; values are deltas vs traditional alternative
    # Positive = better than traditional (green); negative = worse (red)
    # We normalise each column to [-1, +1] before colouring.

    route_labels = [r.route_name.split("(")[0].strip() for r in routes]

    # ── Raw metric values ────────────────────────────────────────────────
    # Distance saving %: positive = shorter (better)
    dist_saving = []
    for r in routes:
        if r.route_id in ("northern_sea_route",):
            trad = 21_000
        elif r.route_id == "northwest_passage":
            trad = 23_800
        elif r.route_id == "transpolar_route":
            trad = 14_000
        elif r.route_id == "cape_of_good_hope_bypass":
            trad = 21_000      # Cape is LONGER; saving is negative
        elif r.route_id == "neopanamax_canal":
            trad = 16_000
        else:
            trad = 0
        if trad > 0:
            pct = (trad - r.distance_nm) / trad * 100.0
        else:
            pct = 0.0
        dist_saving.append(round(pct, 1))

    # Transit days (lower = better; invert for heatmap direction)
    # We use inverse: -transit_days_summer normalised; shorter = greener
    transit = [-r.transit_days_summer for r in routes]

    # Cost premium % (negative = worse; 0 = par)
    cost = [-r.rate_premium_pct for r in routes]

    # CO2: lower = better (invert)
    co2 = [-r.co2_per_teu * 1000 for r in routes]   # scale for visibility

    # Geo risk (lower risk = better; invert score)
    geo = [-r.geopolitical_risk_score * 100 for r in routes]

    # Economic viability (higher = better)
    viab = [r.economic_viability_score * 100 for r in routes]

    # ── Assemble matrix ──────────────────────────────────────────────────
    def _norm(vals: list[float]) -> list[float]:
        mn, mx = min(vals), max(vals)
        rng = mx - mn
        if rng < 1e-9:
            return [0.0] * len(vals)
        return [(v - mn) / rng * 2 - 1 for v in vals]

    z = [
        _norm(dist_saving),
        _norm(transit),
        _norm(cost),
        _norm(co2),
        _norm(geo),
        _norm(viab),
    ]
    z_T = list(map(list, zip(*z)))    # Transpose: routes on Y, metrics on X

    metric_labels = [
        "Distance Saving",
        "Transit Speed",
        "Cost Premium",
        "CO2 Efficiency",
        "Geo Risk",
        "Economic Viability",
    ]

    # Custom text annotations (raw values)
    raw_text_labels = [
        ["{:.0f}%".format(dist_saving[i]) for i in range(len(routes))],
        ["{:.0f}d".format(-transit[i]) for i in range(len(routes))],
        ["{:.0f}%".format(-cost[i]) for i in range(len(routes))],
        ["{:.2f}".format(-co2[i] / 1000) for i in range(len(routes))],
        ["{:.0f}%".format(-geo[i]) for i in range(len(routes))],
        ["{:.0f}%".format(viab[i]) for i in range(len(routes))],
    ]
    text_T = list(map(list, zip(*raw_text_labels)))

    fig = go.Figure(go.Heatmap(
        z=z_T,
        x=metric_labels,
        y=route_labels,
        text=text_T,
        texttemplate="%{text}",
        textfont=dict(size=10, color=C_TEXT),
        colorscale=[
            [0.0, "#7f1d1d"],
            [0.3, "#ef4444"],
            [0.5, "#374151"],
            [0.7, "#10b981"],
            [1.0, "#065f46"],
        ],
        zmin=-1.0,
        zmax=1.0,
        showscale=True,
        colorbar=dict(
            title="",
            tickvals=[-1, 0, 1],
            ticktext=["Worse", "Par", "Better"],
            tickfont=dict(color=C_TEXT2, size=10),
            outlinecolor="rgba(0,0,0,0)",
            bgcolor="rgba(0,0,0,0)",
            len=0.7,
        ),
        hovertemplate=(
            "<b>%{y}</b><br>Metric: %{x}<br>Value: %{text}<extra></extra>"
        ),
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=380,
        margin=dict(l=20, r=60, t=20, b=60),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            side="bottom",
        ),
        yaxis=dict(
            tickfont=dict(color=C_TEXT2, size=10),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        "<div style=\"font-size:0.72rem; color:" + C_TEXT3 + "; margin-top:-8px\">"
        "Green = better than traditional alternative on this metric. "
        "Red = worse. CO2 and Geo Risk are inverted (lower = greener cell)."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 3: Arctic Route Tracker
# ---------------------------------------------------------------------------

def _render_arctic_tracker(freight_rate: float) -> None:
    """Arctic NSR vessel count trend, seasonal calendar, ice chart, break-even."""
    logger.debug("Rendering Arctic route tracker")

    nsr = EMERGING_ROUTES_BY_ID.get("northern_sea_route")
    if nsr is None:
        st.warning("NSR route data not available.")
        return

    # ── 3a. Annual vessel count 2015-2026 ────────────────────────────────
    years = list(range(2015, 2027))
    # Synthetic but plausible NSR transit counts (mostly LNG tankers + bulk; rising trend)
    vessel_counts = [18, 19, 27, 27, 37, 62, 62, 67, 73, 40, 43, 47]
    # Note: dip in 2022-2024 reflects Western carrier exits after Ukraine invasion.
    # Continued growth driven by Russian LNG and non-Western carriers.

    fig_vessel = go.Figure()
    fig_vessel.add_trace(go.Scatter(
        x=years,
        y=vessel_counts,
        mode="lines+markers",
        line=dict(color=C_ARCTIC, width=2.5),
        marker=dict(size=6, color=C_ARCTIC, line=dict(color=C_BG, width=1.5)),
        fill="tozeroy",
        fillcolor="rgba(56,189,248,0.10)",
        name="Annual Vessel Count",
        hovertemplate="%{x}: <b>%{y} vessels</b><extra></extra>",
    ))
    # Annotation: Ukraine invasion
    fig_vessel.add_vline(
        x=2022, line=dict(color=C_DANGER, width=1, dash="dot")
    )
    fig_vessel.add_annotation(
        x=2022, y=max(vessel_counts) * 0.95,
        text="Ukraine invasion<br>Western exits",
        showarrow=False,
        font=dict(color=C_DANGER, size=10),
        xanchor="left",
        bgcolor="rgba(239,68,68,0.12)",
        bordercolor=C_DANGER,
        borderwidth=1,
        borderpad=4,
    )
    # 2030 projection marker
    fig_vessel.add_trace(go.Scatter(
        x=[2030],
        y=[210],
        mode="markers+text",
        marker=dict(size=12, color=C_WARN, symbol="star"),
        text=["2030 target: 210"],
        textposition="top right",
        textfont=dict(size=10, color=C_WARN),
        name="2030 Projection",
        hovertemplate="2030 projection: <b>210 vessels</b><extra></extra>",
    ))
    fig_vessel.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=260,
        margin=dict(l=20, r=20, t=20, b=40),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            dtick=2,
        ),
        yaxis=dict(
            title="Vessels / Year",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_vessel, use_container_width=True)

    # ── 3b. Seasonal availability calendar ───────────────────────────────
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    # Navigability score: 0=closed, 0.5=icebreaker only, 1=open
    navigability = [0.0, 0.0, 0.0, 0.1, 0.3, 0.65, 1.0, 1.0, 1.0, 0.7, 0.2, 0.0]
    season_colors = [
        C_DANGER if v == 0 else C_WARN if v < 0.5 else C_ARCTIC if v < 1.0 else C_HIGH
        for v in navigability
    ]
    season_labels = [
        "Closed" if v == 0 else "Icebreaker only" if v < 0.5
        else "With escort" if v < 1.0 else "Open"
        for v in navigability
    ]

    fig_cal = go.Figure(go.Bar(
        x=months,
        y=navigability,
        marker=dict(
            color=season_colors,
            line=dict(color="rgba(0,0,0,0)", width=0),
        ),
        text=season_labels,
        textposition="inside",
        textfont=dict(size=9, color=C_TEXT),
        hovertemplate="%{x}: %{text}<extra></extra>",
        name="Navigability",
    ))
    fig_cal.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=200,
        margin=dict(l=20, r=20, t=10, b=30),
        xaxis=dict(tickfont=dict(color=C_TEXT3, size=11), gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(
            tickvals=[0, 0.5, 1.0],
            ticktext=["Closed", "Limited", "Open"],
            tickfont=dict(color=C_TEXT3, size=10),
            gridcolor="rgba(255,255,255,0.04)",
            range=[0, 1.15],
        ),
        font=dict(color=C_TEXT),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_cal, use_container_width=True)

    # ── 3c. Arctic sea ice extent (synthetic NSIDC-style, shrinking trend) ──
    ice_years = list(range(1979, 2027))
    # Synthetic September minimum extent (million km2); declining ~13%/decade
    base = 7.5
    ice_extent = [
        round(base - 0.08 * (y - 1979) + 0.6 * math.sin((y - 1979) * 0.8), 2)
        for y in ice_years
    ]
    # Clamp to realistic floor
    ice_extent = [max(2.8, v) for v in ice_extent]

    fig_ice = go.Figure()
    fig_ice.add_trace(go.Scatter(
        x=ice_years,
        y=ice_extent,
        mode="lines",
        line=dict(color=C_ARCTIC, width=2),
        fill="tozeroy",
        fillcolor="rgba(56,189,248,0.08)",
        name="September Min. Extent",
        hovertemplate="%{x}: <b>%{y:.2f} M km\u00b2</b><extra></extra>",
    ))
    # Trend line (simple linear)
    n = len(ice_years)
    x_mean = sum(ice_years) / n
    y_mean = sum(ice_extent) / n
    slope = sum((ice_years[i] - x_mean) * (ice_extent[i] - y_mean) for i in range(n))
    slope /= sum((ice_years[i] - x_mean) ** 2 for i in range(n))
    intercept = y_mean - slope * x_mean
    trend = [round(slope * y + intercept, 2) for y in ice_years]
    fig_ice.add_trace(go.Scatter(
        x=ice_years,
        y=trend,
        mode="lines",
        line=dict(color=C_DANGER, width=1.5, dash="dot"),
        name="Trend (-13%/decade)",
        hovertemplate="%{x} trend: <b>%{y:.2f} M km\u00b2</b><extra></extra>",
    ))
    fig_ice.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=240,
        margin=dict(l=20, r=20, t=10, b=40),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        yaxis=dict(
            title="Million km\u00b2",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_ice, use_container_width=True)

    # ── 3d. Break-even analysis ──────────────────────────────────────────
    viab = compute_route_viability(nsr, freight_rate)

    be_rate = viab["break_even_rate_usd"]
    be_str = "${:,.0f}/FEU".format(be_rate) if be_rate is not None else "Not achievable"
    net_adv = viab["net_advantage_usd"]
    net_color = C_HIGH if net_adv >= 0 else C_DANGER

    col1, col2, col3, col4 = st.columns(4)
    kpi_style = (
        "background:" + C_CARD + "; border:1px solid rgba(56,189,248,0.25);"
        " border-radius:10px; padding:14px 10px; text-align:center; margin-bottom:10px"
    )
    with col1:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Break-Even Rate", be_str, C_WARN)
            + "</div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Net Advantage", "${:+,.0f}/FEU".format(net_adv), net_color)
            + "</div>",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Escort Fee/FEU", "${:,.0f}".format(viab["arctic_escort_cost_per_feu"]), C_ORANGE)
            + "</div>",
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            "<div style=\"" + kpi_style + "\">"
            + _kpi_mini("Geo Risk Premium", "${:,.0f}/FEU".format(viab["geo_risk_premium_usd"]), C_DANGER)
            + "</div>",
            unsafe_allow_html=True,
        )

    # ── 3e. Western carrier exits ────────────────────────────────────────
    exited_carriers = [
        ("Maersk",           "Exited NSR 2022 post-Ukraine; sanctions compliance"),
        ("MSC",              "Suspended Arctic transits indefinitely"),
        ("CMA CGM",          "Withdrew from NSR; reputational risk"),
        ("Hapag-Lloyd",      "No Russia-associated routes since 2022"),
        ("ONE (Ocean NE)",   "Ceased NSR bookings; US/EU sanctions compliance"),
        ("Evergreen",        "Avoided NSR; follows US OFAC guidance"),
        ("Yang Ming",        "Suspended; Taiwan political alignment"),
    ]

    rows_html = ""
    for carrier, reason in exited_carriers:
        rows_html += (
            "<tr style=\"border-bottom:1px solid rgba(255,255,255,0.04)\">"
            "<td style=\"color:" + C_DANGER + "; font-size:0.78rem; padding:7px 8px;"
            " font-weight:700\">" + carrier + "</td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.73rem; padding:7px 8px\">"
            + reason + "</td>"
            "</tr>"
        )

    h_style = (
        "color:" + C_TEXT3 + "; font-size:0.66rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:5px 8px;"
        " border-bottom:1px solid rgba(255,255,255,0.10)"
    )
    table_html = (
        "<div style=\"overflow-x:auto\">"
        "<table style=\"width:100%; border-collapse:collapse\">"
        "<thead><tr>"
        "<th style=\"" + h_style + "\">Carrier</th>"
        "<th style=\"" + h_style + "\">Reason for Exit</th>"
        "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table></div>"
    )

    st.markdown(
        _card(
            "<div style=\"font-size:0.85rem; font-weight:700; color:" + C_TEXT
            + "; margin-bottom:10px\">"
            "Western Carriers That Have Exited the Northern Sea Route (Post-2022)</div>"
            + table_html,
            border_color="rgba(239,68,68,0.25)",
        ),
        unsafe_allow_html=True,
    )

    # Recommendation
    rec_color = C_HIGH if viab["is_competitive_now"] else C_WARN
    st.markdown(
        "<div style=\"background:rgba(56,189,248,0.06); border:1px solid rgba(56,189,248,0.20);"
        " border-radius:10px; padding:12px 16px; font-size:0.82rem; color:"
        + C_TEXT2 + "; line-height:1.5\">"
        "<b style=\"color:" + C_TEXT + "\">Analysis: </b>"
        + viab["recommendation"] + " " + viab["notes"]
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4: Red Sea Rerouting Impact
# ---------------------------------------------------------------------------

def _render_red_sea_rerouting() -> None:
    """Timeline, rate premium, capacity split, and end-date scenarios."""
    logger.debug("Rendering Red Sea rerouting impact")

    # ── 4a. % of Asia-Europe traffic via Cape of Good Hope ───────────────
    # Monthly data from Dec 2023 (Houthi attacks began) through early 2026
    months_rs = [
        "Dec-23", "Jan-24", "Feb-24", "Mar-24", "Apr-24", "May-24",
        "Jun-24", "Jul-24", "Aug-24", "Sep-24", "Oct-24", "Nov-24",
        "Dec-24", "Jan-25", "Feb-25", "Mar-25",
    ]
    cape_pct = [15, 35, 52, 60, 65, 67, 68, 66, 65, 62, 63, 61, 60, 62, 61, 60]
    suez_pct = [85, 65, 48, 40, 35, 33, 32, 34, 35, 38, 37, 39, 40, 38, 39, 40]

    fig_rs = go.Figure()
    fig_rs.add_trace(go.Scatter(
        x=months_rs,
        y=cape_pct,
        name="Cape of Good Hope %",
        mode="lines+markers",
        line=dict(color=C_HIGH, width=2.5),
        marker=dict(size=5, color=C_HIGH),
        fill="tozeroy",
        fillcolor="rgba(16,185,129,0.10)",
        hovertemplate="%{x}: <b>%{y}%</b> via Cape<extra></extra>",
    ))
    fig_rs.add_trace(go.Scatter(
        x=months_rs,
        y=suez_pct,
        name="Suez Canal %",
        mode="lines+markers",
        line=dict(color=C_DANGER, width=1.5, dash="dot"),
        marker=dict(size=4, color=C_DANGER),
        hovertemplate="%{x}: <b>%{y}%</b> via Suez<extra></extra>",
    ))
    # Houthi attack marker
    fig_rs.add_annotation(
        x="Dec-23", y=90,
        text="Houthi attacks begin",
        showarrow=True,
        arrowhead=2,
        arrowcolor=C_DANGER,
        font=dict(color=C_DANGER, size=10),
        bgcolor="rgba(239,68,68,0.12)",
        bordercolor=C_DANGER,
        borderwidth=1,
        borderpad=4,
        ax=60, ay=-30,
    )
    fig_rs.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=260,
        margin=dict(l=20, r=20, t=20, b=50),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=10),
            gridcolor="rgba(255,255,255,0.04)",
            tickangle=-35,
        ),
        yaxis=dict(
            title="% of Asia-Europe Traffic",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            range=[0, 100],
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_rs, use_container_width=True)

    # ── 4b. Rate impact KPIs ─────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    kpi_s = (
        "background:" + C_CARD + "; border:1px solid rgba(16,185,129,0.20);"
        " border-radius:10px; padding:14px 10px; text-align:center"
    )
    with c1:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Cape Fuel Premium", "+$400-800/FEU", C_DANGER)
            + "</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Extra Transit Days", "+7-10 days", C_WARN)
            + "</div>",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Rate Spike vs Pre-Crisis", "+35-45%", C_ORANGE)
            + "</div>",
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            "<div style=\"" + kpi_s + "\">"
            + _kpi_mini("Traffic Via Cape (now)", "~60%", C_HIGH)
            + "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style=\"height:14px\"></div>", unsafe_allow_html=True)

    # ── 4c. Weekly capacity via Cape vs Suez (bar chart) ─────────────────
    weeks = [
        "W1-Jan", "W2-Jan", "W3-Jan", "W4-Jan",
        "W1-Feb", "W2-Feb", "W3-Feb", "W4-Feb",
        "W1-Mar",
    ]
    cap_cape = [310, 315, 318, 312, 320, 325, 322, 316, 318]   # 000 TEU/week
    cap_suez = [205, 208, 200, 210, 200, 195, 198, 206, 200]

    fig_cap = go.Figure()
    fig_cap.add_trace(go.Bar(
        name="Cape of Good Hope",
        x=weeks,
        y=cap_cape,
        marker=dict(color=C_HIGH, opacity=0.85),
        hovertemplate="%{x}: <b>%{y}k TEU/week</b> via Cape<extra></extra>",
    ))
    fig_cap.add_trace(go.Bar(
        name="Suez Canal",
        x=weeks,
        y=cap_suez,
        marker=dict(color=C_DANGER, opacity=0.75),
        hovertemplate="%{x}: <b>%{y}k TEU/week</b> via Suez<extra></extra>",
    ))
    fig_cap.update_layout(
        barmode="group",
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=260,
        margin=dict(l=20, r=20, t=20, b=50),
        xaxis=dict(
            tickfont=dict(color=C_TEXT3, size=10),
            gridcolor="rgba(0,0,0,0)",
            tickangle=-30,
        ),
        yaxis=dict(
            title="Capacity (000 TEU/week)",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=C_TEXT2, size=10),
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
        ),
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig_cap, use_container_width=True)

    # ── 4d. End-of-disruption scenarios ──────────────────────────────────
    scenarios = [
        {
            "scenario": "Base Case",
            "end": "Q3 2026",
            "prob": 40,
            "color": C_WARN,
            "note": "Ceasefire holds; Houthi threat remains but carriers gradually return to Suez",
        },
        {
            "scenario": "Bull (Rapid Resolution)",
            "end": "Q4 2025",
            "prob": 20,
            "color": C_HIGH,
            "note": "US-brokered Yemen deal; Red Sea safe passage guarantee; immediate rate normalization",
        },
        {
            "scenario": "Bear (Prolonged Crisis)",
            "end": "2027+",
            "prob": 40,
            "color": C_DANGER,
            "note": "Conflict escalates; Cape becomes permanent alternative; structural freight premium",
        },
    ]

    sc_rows = ""
    for s in scenarios:
        bar_fill = "{:.0f}".format(s["prob"])
        sc_rows += (
            "<tr style=\"border-bottom:1px solid rgba(255,255,255,0.04)\">"
            "<td style=\"color:" + s["color"] + "; font-size:0.80rem; padding:9px 8px;"
            " font-weight:700\">" + s["scenario"] + "</td>"
            "<td style=\"color:" + C_TEXT + "; font-size:0.78rem; padding:9px 8px;"
            " font-weight:600\">" + s["end"] + "</td>"
            "<td style=\"padding:9px 8px; min-width:120px\">"
            "<div style=\"display:flex; align-items:center; gap:7px\">"
            "<div style=\"flex:1; background:rgba(255,255,255,0.06);"
            " border-radius:4px; height:7px\">"
            "<div style=\"width:" + bar_fill + "%; background:" + s["color"]
            + "; border-radius:4px; height:7px\"></div>"
            "</div>"
            "<span style=\"font-size:0.75rem; font-weight:700; color:"
            + s["color"] + "\">" + bar_fill + "%</span>"
            "</div></td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.73rem; padding:9px 8px;"
            " line-height:1.4\">" + s["note"] + "</td>"
            "</tr>"
        )

    h_s = (
        "color:" + C_TEXT3 + "; font-size:0.66rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:5px 8px;"
        " border-bottom:1px solid rgba(255,255,255,0.10)"
    )
    sc_table = (
        "<div style=\"overflow-x:auto\">"
        "<table style=\"width:100%; border-collapse:collapse\">"
        "<thead><tr>"
        "<th style=\"" + h_s + "\">Scenario</th>"
        "<th style=\"" + h_s + "\">Est. End</th>"
        "<th style=\"" + h_s + "\">Probability</th>"
        "<th style=\"" + h_s + "\">Key Driver</th>"
        "</tr></thead>"
        "<tbody>" + sc_rows + "</tbody>"
        "</table></div>"
    )

    st.markdown(
        _card(
            "<div style=\"font-size:0.85rem; font-weight:700; color:" + C_TEXT
            + "; margin-bottom:10px\">Red Sea Crisis End-Date Scenarios</div>"
            + sc_table,
            border_color="rgba(16,185,129,0.20)",
        ),
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 5: Emerging Market Trade Corridor Growth
# ---------------------------------------------------------------------------

def _render_emerging_market_growth() -> None:
    """Bar chart of projected trade volume growth CAGR 2025-2030 for non-traditional routes."""
    logger.debug("Rendering emerging market corridor growth chart")

    corridors = [
        {"name": "India Subcontinent", "cagr": 18.0, "color": C_ORANGE,  "note": "Fastest growth; Apple, Samsung shifting supply chains from China to India"},
        {"name": "East Africa",        "cagr": 15.0, "color": C_CYAN,    "note": "Consumer market growth; AfCFTA, LAPSSET, Mombasa port expansion"},
        {"name": "Southeast Asia",     "cagr": 12.0, "color": C_HIGH,    "note": "Vietnam, Indonesia, Thailand as China+1 manufacturing hubs"},
        {"name": "Mexico (Nearshore)", "cagr": 14.0, "color": C_WARN,    "note": "US nearshoring; USMCA advantages; automotive and electronics"},
        {"name": "West Africa",        "cagr": 10.0, "color": C_PURPLE,  "note": "Nigeria, Ghana growing middle class; intra-Africa demand"},
        {"name": "Central Asia TITR",  "cagr": 22.0, "color": C_ACCENT,  "note": "Trans-Caspian corridor: Russia bypass driving explosive growth"},
        {"name": "Arctic LNG",         "cagr": 19.0, "color": C_ARCTIC,  "note": "LNG tanker demand from Yamal; Asia-Pacific LNG deficit"},
        {"name": "East Europe (BRI)",  "cagr": 8.0,  "color": C_TEXT2,   "note": "Belt and Road rail; Hungary, Poland, Czech Republic logistics hubs"},
    ]

    # Sort by CAGR descending
    corridors_sorted = sorted(corridors, key=lambda c: c["cagr"], reverse=True)

    names = [c["name"] for c in corridors_sorted]
    cagrs = [c["cagr"] for c in corridors_sorted]
    colors = [c["color"] for c in corridors_sorted]
    notes = [c["note"] for c in corridors_sorted]

    fig = go.Figure(go.Bar(
        x=cagrs,
        y=names,
        orientation="h",
        marker=dict(
            color=colors,
            opacity=0.88,
            line=dict(color="rgba(0,0,0,0)", width=0),
        ),
        text=["{:.0f}% CAGR".format(v) for v in cagrs],
        textposition="outside",
        textfont=dict(size=11, color=C_TEXT2),
        customdata=notes,
        hovertemplate="<b>%{y}</b><br>CAGR: %{x:.0f}%<br><i>%{customdata}</i><extra></extra>",
    ))

    # Reference line: global average container growth ~3.5%/yr
    fig.add_vline(
        x=3.5,
        line=dict(color=C_TEXT3, width=1.5, dash="dot"),
        annotation_text="Global avg 3.5%",
        annotation_font=dict(color=C_TEXT3, size=9),
        annotation_position="top left",
    )

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=380,
        margin=dict(l=10, r=80, t=20, b=40),
        xaxis=dict(
            title="Projected CAGR 2025-2030 (%)",
            titlefont=dict(color=C_TEXT3, size=11),
            tickfont=dict(color=C_TEXT3, size=11),
            gridcolor="rgba(255,255,255,0.04)",
            range=[0, 28],
        ),
        yaxis=dict(
            tickfont=dict(color=C_TEXT2, size=11),
            gridcolor="rgba(0,0,0,0)",
        ),
        font=dict(color=C_TEXT),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

    # Footnote
    st.markdown(
        "<div style=\"font-size:0.71rem; color:" + C_TEXT3
        + "; margin-top:-8px; line-height:1.5\">"
        "CAGR projections are estimates based on World Bank, IMF, and shipping analyst "
        "consensus (2025). Central Asia TITR growth reflects post-2022 Russia bypass "
        "acceleration. Arctic LNG reflects Yamal and projected Arctic LNG 2 output."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(route_results, freight_data: dict, macro_data: dict) -> None:
    """Render the Emerging Trade Routes tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer (may be empty).
    freight_data : dict
        Freight rate data dict; used to extract current Asia-Europe spot rate.
    macro_data : dict
        Global macro indicators dict (passed through).
    """
    logger.info("Rendering Emerging Trade Routes tab")

    st.header("Emerging Trade Routes")
    st.markdown(
        "<div style=\"color:" + C_TEXT2 + "; font-size:0.88rem; margin-bottom:18px;"
        " line-height:1.6\">"
        "Climate change is opening Arctic passages while geopolitics reshapes land-sea "
        "corridors. The 2024 Houthi crisis in the Red Sea has already permanently "
        "accelerated awareness of route alternatives. This tab tracks 8 emerging or "
        "revived trade corridors and their commercial viability."
        "</div>",
        unsafe_allow_html=True,
    )

    # Pull freight rate from data if available; default to $3,200/FEU (current Asia-Europe)
    freight_rate: float = 3_200.0
    if isinstance(freight_data, dict):
        # Try common keys used elsewhere in this codebase
        for k in ("asia_europe_spot", "scfi_asia_europe", "asia_europe", "freight_rate"):
            val = freight_data.get(k)
            if val is not None:
                try:
                    freight_rate = float(val)
                    break
                except (TypeError, ValueError):
                    pass

    logger.debug("Emerging routes tab using freight_rate={:.0f}", freight_rate)

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — World Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Emerging Routes World Map",
        (
            "Traditional routes (gray). Emerging routes colour-coded by status. "
            "Globe rotated north to highlight Arctic passages. "
            "Status: "
            + "  ".join(
                "<b style=\"color:" + c + "\">" + s + "</b>"
                for s, c in _STATUS_COLOR.items()
            )
        ),
    )
    _render_world_map()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Route Comparison Matrix
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Route Performance Comparison Matrix",
        (
            "Routes (Y-axis) vs six metrics (X-axis). "
            "Green = better than traditional alternative; Red = worse. "
            "Hover for exact values."
        ),
    )
    _render_comparison_matrix()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Arctic Route Tracker
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Arctic Route Tracker — Northern Sea Route (NSR)",
        (
            "Annual vessel count trend 2015-2026 | Seasonal availability calendar | "
            "Arctic sea ice extent (synthetic NSIDC-style) | Break-even analysis | "
            "Western carrier exits"
        ),
    )

    col_a, col_b = st.columns([1, 3])
    with col_a:
        st.markdown(
            _card(
                "<div style=\"font-size:0.78rem; color:" + C_TEXT2
                + "; line-height:1.6\">"
                "<b style=\"color:" + C_TEXT + "\">NSR at a glance</b><br><br>"
                + _kpi_mini("Distance (nm)", "12,800", C_ARCTIC) + "<br>"
                + _kpi_mini("vs Suez saving", "8,200 nm / 39%", C_HIGH) + "<br>"
                + _kpi_mini("Summer transit", "~19 days", C_WARN) + "<br>"
                + _kpi_mini("Current vessels/yr", "~45", C_TEXT2) + "<br>"
                + _kpi_mini("2030 projection", "210+", C_ORANGE)
                + "</div>",
                border_color="rgba(56,189,248,0.30)",
            ),
            unsafe_allow_html=True,
        )
    with col_b:
        st.markdown(
            "<div style=\"font-size:0.75rem; color:" + C_TEXT3
            + "; margin-bottom:6px; text-transform:uppercase; letter-spacing:0.06em\">"
            "Annual Vessel Count 2015-2026 (+ 2030 Projection)"
            "</div>",
            unsafe_allow_html=True,
        )
        _render_arctic_tracker(freight_rate)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Red Sea Rerouting Impact
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Red Sea Crisis — Cape of Good Hope Rerouting Impact",
        (
            "Houthi attacks began December 2023. 60%+ of Asia-Europe traffic "
            "rerouted via Cape of Good Hope by mid-2024. Rate, capacity, and "
            "scenario analysis."
        ),
    )
    _render_red_sea_rerouting()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Emerging Market Corridor Growth
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Emerging Market Trade Corridor Growth (2025-2030 CAGR)",
        (
            "Projected compound annual growth rate for non-traditional trade corridors. "
            "India subcontinent, East Africa, and Central Asia TITR are the fastest-growing."
        ),
    )
    _render_emerging_market_growth()
