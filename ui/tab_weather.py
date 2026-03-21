"""
Weather Risk Tab

Displays seasonal and real-time weather risk analysis across global shipping routes.

Sections (NEW — rendered at top):
  0. Active Alerts Banner     — colored severity banner with live alert count
  NEW Weather Risk World Map  — Scattergeo with storm/cyclone markers (flat projection)
  NEW Seasonal Patterns Chart — bar chart of historical weather risk by month per region
  NEW Route Risk Table        — route, risk level, expected delay, alternative route
  NEW Season Tracker          — typhoon/hurricane active season status cards
  NEW Port Weather Windows    — best months calendar heatmap for major ports

Sections (ORIGINAL — preserved below):
  1. Weather Risk Globe       — orthographic Plotly Scattergeo with active weather
                                systems, risk zones, and affected port highlights
  2. Seasonal Risk Calendar   — 12-month x N-route heatmap (go.Heatmap)
  3. Current Weather Alerts   — card stream of ACTIVE/ELEVATED/SEASONAL alerts
  4. Route Weather Profile    — per-route detail: timeline bar chart, top risks,
                                disruption frequency, weather-adjusted ETA
  5. El Nino / La Nina Monitor — ENSO phase card with Panama Canal and rate impacts
"""
from __future__ import annotations

import datetime
from typing import List

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.weather_risk import (
    ALL_ROUTE_IDS,
    ROUTE_DISPLAY_NAMES,
    WEATHER_RISK_EVENTS,
    WeatherRiskEvent,
    WeatherRiskIndex,
    compute_route_weather_risk,
    compute_weather_adjusted_eta,
    get_current_season_alerts,
    get_nominal_transit_days,
)

# ---------------------------------------------------------------------------
# Colour palette
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
C_TEAL   = "#06b6d4"

_RISK_LEVEL_COLOR: dict[str, str] = {
    "ACTIVE":   C_DANGER,
    "ELEVATED": C_ORANGE,
    "SEASONAL": C_WARN,
    "LOW":      C_HIGH,
}

_RISK_TYPE_COLOR: dict[str, str] = {
    "TYPHOON":   "#8b5cf6",
    "HURRICANE": "#ef4444",
    "MONSOON":   "#06b6d4",
    "STORM":     "#f97316",
    "FOG":       "#94a3b8",
    "ICE":       "#bfdbfe",
}

_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# ---------------------------------------------------------------------------
# Geographic centres for weather system markers
# ---------------------------------------------------------------------------

_WEATHER_SYSTEM_CENTERS: list[dict] = [
    {
        "name": "Western Pacific Typhoon Zone",
        "lat": 18.0, "lon": 135.0,
        "risk_type": "TYPHOON",
        "event_name": "Western Pacific Typhoon Season",
        "radius_lat": 12.0, "radius_lon": 18.0,
    },
    {
        "name": "Atlantic Hurricane Zone",
        "lat": 25.0, "lon": -70.0,
        "risk_type": "HURRICANE",
        "event_name": "Atlantic Hurricane Season",
        "radius_lat": 10.0, "radius_lon": 20.0,
    },
    {
        "name": "North Pacific Storm Track",
        "lat": 48.0, "lon": -170.0,
        "risk_type": "STORM",
        "event_name": "North Pacific Winter Storms",
        "radius_lat": 6.0, "radius_lon": 25.0,
    },
    {
        "name": "North Sea Storm Zone",
        "lat": 56.0, "lon": 3.0,
        "risk_type": "STORM",
        "event_name": "North Sea Winter Storm Season",
        "radius_lat": 4.0, "radius_lon": 6.0,
    },
    {
        "name": "Bay of Bengal Cyclone Zone",
        "lat": 14.0, "lon": 87.0,
        "risk_type": "TYPHOON",
        "event_name": "Bay of Bengal Cyclone Season",
        "radius_lat": 7.0, "radius_lon": 8.0,
    },
    {
        "name": "Arabian Sea / Monsoon Zone",
        "lat": 14.0, "lon": 63.0,
        "risk_type": "MONSOON",
        "event_name": "NE Indian Ocean Monsoon Season",
        "radius_lat": 8.0, "radius_lon": 12.0,
    },
    {
        "name": "Shanghai Fog Zone",
        "lat": 31.5, "lon": 122.5,
        "risk_type": "FOG",
        "event_name": "Fog Season - Shanghai / Yangtze Delta",
        "radius_lat": 3.0, "radius_lon": 4.0,
    },
    {
        "name": "Mediterranean Scirocco Zone",
        "lat": 36.0, "lon": 18.0,
        "risk_type": "STORM",
        "event_name": "Mediterranean Scirocco and Tramontane Winds",
        "radius_lat": 4.0, "radius_lon": 8.0,
    },
    {
        "name": "Red Sea Heat Zone",
        "lat": 20.0, "lon": 38.0,
        "risk_type": "STORM",
        "event_name": "Red Sea Summer Heat / Cargo Damage Risk",
        "radius_lat": 5.0, "radius_lon": 4.0,
    },
    {
        "name": "Gulf of Mexico Hurricane Zone",
        "lat": 24.0, "lon": -90.0,
        "risk_type": "HURRICANE",
        "event_name": "Atlantic Hurricane Season",
        "radius_lat": 6.0, "radius_lon": 10.0,
    },
    {
        "name": "South Indian Ocean Cyclone Zone",
        "lat": -18.0, "lon": 65.0,
        "risk_type": "TYPHOON",
        "event_name": "South-West Indian Ocean Cyclone Season",
        "radius_lat": 8.0, "radius_lon": 14.0,
    },
    {
        "name": "Bering Sea Storm Zone",
        "lat": 57.0, "lon": -175.0,
        "risk_type": "STORM",
        "event_name": "Bering Sea Cyclones / Sub-Arctic Routing",
        "radius_lat": 5.0, "radius_lon": 10.0,
    },
]

# Amber/red ports affected by weather events
_AFFECTED_PORT_COORDS: list[dict] = [
    {"locode": "CNSHA", "lat": 31.2,  "lon": 121.5,  "name": "Shanghai",      "risk": "HIGH"},
    {"locode": "CNSZN", "lat": 22.5,  "lon": 114.1,  "name": "Shenzhen",      "risk": "HIGH"},
    {"locode": "HKHKG", "lat": 22.3,  "lon": 114.2,  "name": "Hong Kong",     "risk": "HIGH"},
    {"locode": "KRPUS", "lat": 35.1,  "lon": 129.0,  "name": "Busan",         "risk": "MODERATE"},
    {"locode": "JPYOK", "lat": 35.4,  "lon": 139.6,  "name": "Yokohama",      "risk": "MODERATE"},
    {"locode": "USNYC", "lat": 40.7,  "lon": -74.0,  "name": "New York",      "risk": "MODERATE"},
    {"locode": "USSAV", "lat": 32.1,  "lon": -81.1,  "name": "Savannah",      "risk": "MODERATE"},
    {"locode": "NLRTM", "lat": 51.9,  "lon": 4.5,    "name": "Rotterdam",     "risk": "MODERATE"},
    {"locode": "DEHAM", "lat": 53.5,  "lon": 10.0,   "name": "Hamburg",       "risk": "MODERATE"},
    {"locode": "GBFXT", "lat": 51.96, "lon": 1.35,   "name": "Felixstowe",    "risk": "MODERATE"},
    {"locode": "LKCMB", "lat": 6.9,   "lon": 79.9,   "name": "Colombo",       "risk": "HIGH"},
    {"locode": "SGSIN", "lat": 1.35,  "lon": 103.8,  "name": "Singapore",     "risk": "MODERATE"},
    {"locode": "CNNBO", "lat": 29.9,  "lon": 121.6,  "name": "Ningbo",        "risk": "HIGH"},
    {"locode": "BDCGP", "lat": 22.3,  "lon": 91.8,   "name": "Chittagong",    "risk": "HIGH"},
    {"locode": "GRPIR", "lat": 37.9,  "lon": 23.6,   "name": "Piraeus",       "risk": "MODERATE"},
    {"locode": "MATNM", "lat": 35.9,  "lon": -5.5,   "name": "Tanger Med",    "risk": "MODERATE"},
    {"locode": "EGPSD", "lat": 31.25, "lon": 32.3,   "name": "Port Said",     "risk": "MODERATE"},
]

# ---------------------------------------------------------------------------
# Shared HTML helpers
# ---------------------------------------------------------------------------

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        "<div style=\"color:" + C_TEXT2 + "; font-size:0.83rem; margin-top:3px\">"
        + subtitle
        + "</div>"
    ) if subtitle else ""
    st.markdown(
        "<div style=\"margin-bottom:14px; margin-top:4px\">"
        "<div style=\"font-size:1.05rem; font-weight:700; color:" + C_TEXT + "\">"
        + text
        + "</div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _risk_badge(level: str, pulse: bool = False) -> str:
    color = _RISK_LEVEL_COLOR.get(level, C_TEXT2)
    pulse_css = " animation:weather-pulse 1.4s ease-in-out infinite;" if pulse else ""
    return (
        "<span style=\"background:rgba(0,0,0,0.35); color:" + color
        + "; border:1px solid " + color
        + "; padding:2px 10px; border-radius:999px;"
        " font-size:0.70rem; font-weight:700; white-space:nowrap;"
        + pulse_css
        + "\">"
        + level
        + "</span>"
    )


def _type_badge(risk_type: str) -> str:
    color = _RISK_TYPE_COLOR.get(risk_type, C_TEXT2)
    return (
        "<span style=\"background:rgba(0,0,0,0.4); color:" + color
        + "; border:1px solid " + color + "55"
        + "; padding:1px 9px; border-radius:999px;"
        " font-size:0.68rem; font-weight:700; white-space:nowrap\">"
        + risk_type
        + "</span>"
    )


def _pill(text: str, color: str = C_ACCENT) -> str:
    return (
        "<span style=\"display:inline-block; background:rgba(59,130,246,0.10);"
        " color:" + color + "; border:1px solid rgba(59,130,246,0.28);"
        " padding:1px 8px; border-radius:999px; font-size:0.67rem;"
        " font-weight:600; margin:2px 3px 2px 0; white-space:nowrap\">"
        + text
        + "</span>"
    )


_PULSE_CSS = """
<style>
@keyframes weather-pulse {
    0%   { opacity: 1; }
    50%  { opacity: 0.40; }
    100% { opacity: 1; }
}
@keyframes alert-slide-in {
    from { transform: translateY(-8px); opacity: 0; }
    to   { transform: translateY(0);    opacity: 1; }
}
@keyframes season-glow {
    0%, 100% { box-shadow: 0 0 8px rgba(239,68,68,0.3); }
    50%       { box-shadow: 0 0 20px rgba(239,68,68,0.6); }
}
</style>
"""

# ===========================================================================
# NEW SECTION 0 — Active Weather Alerts Banner
# ===========================================================================

def _render_alerts_banner() -> None:
    """Colored severity banner at the top showing active alert summary."""
    alerts = get_current_season_alerts()
    month_name = _MONTH_NAMES[datetime.date.today().month - 1]

    active_count    = sum(1 for a in alerts if a.current_risk_level == "ACTIVE")
    elevated_count  = sum(1 for a in alerts if a.current_risk_level == "ELEVATED")
    seasonal_count  = sum(1 for a in alerts if a.current_risk_level == "SEASONAL")

    if active_count > 0:
        banner_bg     = "linear-gradient(135deg, rgba(239,68,68,0.18) 0%, rgba(239,68,68,0.06) 100%)"
        banner_border = "#ef4444"
        banner_icon   = "🔴"
        severity_text = "CRITICAL — Active Disruptions"
        severity_color = C_DANGER
    elif elevated_count > 0:
        banner_bg     = "linear-gradient(135deg, rgba(249,115,22,0.18) 0%, rgba(249,115,22,0.06) 100%)"
        banner_border = "#f97316"
        banner_icon   = "🟠"
        severity_text = "ELEVATED RISK — Monitor Closely"
        severity_color = C_ORANGE
    elif seasonal_count > 0:
        banner_bg     = "linear-gradient(135deg, rgba(245,158,11,0.18) 0%, rgba(245,158,11,0.06) 100%)"
        banner_border = "#f59e0b"
        banner_icon   = "🟡"
        severity_text = "SEASONAL RISK — Normal Operations"
        severity_color = C_WARN
    else:
        banner_bg     = "linear-gradient(135deg, rgba(16,185,129,0.12) 0%, rgba(16,185,129,0.04) 100%)"
        banner_border = "#10b981"
        banner_icon   = "🟢"
        severity_text = "ALL CLEAR — No Significant Risks"
        severity_color = C_HIGH

    # Build alert type pills
    affected_types = list({a.risk_type for a in alerts})
    type_pills_html = " ".join(
        '<span style="background:rgba(0,0,0,0.3); color:' + _RISK_TYPE_COLOR.get(t, C_TEXT2) + '; '
        'border:1px solid ' + _RISK_TYPE_COLOR.get(t, C_TEXT2) + '55; padding:2px 10px; '
        'border-radius:999px; font-size:0.70rem; font-weight:700;">' + t + '</span>'
        for t in affected_types[:5]
    )

    # Stat chips
    stats_html = ""
    if active_count:
        stats_html += (
            '<span style="color:' + C_DANGER + '; font-weight:800; font-size:1.1rem;">' + str(active_count) + '</span>'
            ' <span style="color:' + C_TEXT3 + '; font-size:0.78rem;">Active</span>&nbsp;&nbsp;'
        )
    if elevated_count:
        stats_html += (
            '<span style="color:' + C_ORANGE + '; font-weight:800; font-size:1.1rem;">' + str(elevated_count) + '</span>'
            ' <span style="color:' + C_TEXT3 + '; font-size:0.78rem;">Elevated</span>&nbsp;&nbsp;'
        )
    if seasonal_count:
        stats_html += (
            '<span style="color:' + C_WARN + '; font-weight:800; font-size:1.1rem;">' + str(seasonal_count) + '</span>'
            ' <span style="color:' + C_TEXT3 + '; font-size:0.78rem;">Seasonal</span>&nbsp;&nbsp;'
        )
    if not alerts:
        stats_html = '<span style="color:' + C_HIGH + '; font-weight:700; font-size:0.85rem;">No alerts for ' + month_name + '</span>'

    total_routes_affected = len({r for a in alerts for r in a.affected_routes})
    total_ports_affected  = len({p for a in alerts for p in a.affected_ports})

    st.markdown(
        '<div style="background:' + banner_bg + '; border:1px solid ' + banner_border + '44; '
        'border-left:4px solid ' + banner_border + '; border-radius:12px; '
        'padding:16px 20px; margin-bottom:18px; animation:alert-slide-in 0.35s ease-out both;">'

        '<div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:10px;">'

        '<div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">'
        '<span style="font-size:1.6rem; line-height:1;">' + banner_icon + '</span>'
        '<div>'
        '<div style="font-size:0.70rem; font-weight:800; color:' + severity_color + '; '
        'text-transform:uppercase; letter-spacing:0.10em; margin-bottom:4px;">' + severity_text + '</div>'
        '<div style="display:flex; align-items:baseline; gap:4px; flex-wrap:wrap;">' + stats_html + '</div>'
        '</div>'
        '</div>'

        '<div style="text-align:right; flex-shrink:0;">'
        '<div style="margin-bottom:6px;">' + type_pills_html + '</div>'
        '<div style="font-size:0.72rem; color:' + C_TEXT3 + ';">'
        '<span style="color:' + C_TEXT2 + '; font-weight:600;">' + str(total_routes_affected) + '</span> routes &nbsp;|&nbsp; '
        '<span style="color:' + C_TEXT2 + '; font-weight:600;">' + str(total_ports_affected) + '</span> ports &nbsp;|&nbsp; '
        '<span style="color:' + C_TEXT2 + '; font-weight:600;">' + month_name + ' ' + str(datetime.date.today().year) + '</span>'
        '</div>'
        '</div>'

        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ===========================================================================
# NEW SECTION — Weather Risk World Map (flat Scattergeo)
# ===========================================================================

def _render_world_risk_map() -> None:
    """Flat natural-earth projection world map with storm/cyclone markers and risk zones."""
    logger.debug("Rendering flat world weather risk map")

    month = datetime.date.today().month
    active_event_names = {ev.event_name for ev in WEATHER_RISK_EVENTS if month in ev.season_months}

    fig = go.Figure()

    # ── Risk zone blobs ───────────────────────────────────────────────────────
    for system in _WEATHER_SYSTEM_CENTERS:
        if system.get("lat") is None or system.get("lon") is None:
            continue
        is_active = system["event_name"] in active_event_names
        base_color = _RISK_TYPE_COLOR.get(system["risk_type"], C_TEXT2)
        base_size = 48 if is_active else 28

        # Outer glow
        fig.add_trace(go.Scattergeo(
            lat=[system["lat"]], lon=[system["lon"]],
            mode="markers",
            marker=dict(size=base_size * 2.8, color=base_color, opacity=0.06, line=dict(width=0)),
            hoverinfo="skip", showlegend=False, name=system["name"] + "_glow3",
        ))
        # Mid ring
        fig.add_trace(go.Scattergeo(
            lat=[system["lat"]], lon=[system["lon"]],
            mode="markers",
            marker=dict(size=base_size * 1.7, color=base_color, opacity=0.11, line=dict(width=0)),
            hoverinfo="skip", showlegend=False, name=system["name"] + "_glow2",
        ))
        # Core marker
        hover_text = (
            "<b>" + system["name"] + "</b><br>"
            + "Type: " + system["risk_type"] + "<br>"
            + ("IN SEASON" if is_active else "Out of season")
        )
        fig.add_trace(go.Scattergeo(
            lat=[system["lat"]], lon=[system["lon"]],
            mode="markers+text" if is_active else "markers",
            text=[system["risk_type"][0]] if is_active else [],
            textfont=dict(size=9, color="white"),
            textposition="middle center",
            marker=dict(
                size=base_size,
                color=base_color,
                opacity=0.30 if is_active else 0.12,
                line=dict(color=base_color, width=2.0 if is_active else 0.5),
            ),
            hovertemplate=hover_text + "<extra></extra>",
            showlegend=False,
            name=system["name"],
        ))

    # ── Affected port diamonds ────────────────────────────────────────────────
    valid_ports = [p for p in _AFFECTED_PORT_COORDS if p.get("lat") is not None]
    if valid_ports:
        port_colors = [C_DANGER if p["risk"] == "HIGH" else C_WARN for p in valid_ports]
        port_hover  = [
            "<b>" + p["name"] + "</b> (" + p["locode"] + ")<br>Weather Risk: " + p["risk"]
            for p in valid_ports
        ]
        fig.add_trace(go.Scattergeo(
            lat=[p["lat"] for p in valid_ports],
            lon=[p["lon"] for p in valid_ports],
            mode="markers",
            marker=dict(size=16, color=port_colors, opacity=0.10, line=dict(width=0)),
            hoverinfo="skip", showlegend=False, name="port_glow",
        ))
        fig.add_trace(go.Scattergeo(
            lat=[p["lat"] for p in valid_ports],
            lon=[p["lon"] for p in valid_ports],
            mode="markers",
            marker=dict(
                size=7, color=port_colors, opacity=0.88,
                symbol="diamond",
                line=dict(color="rgba(255,255,255,0.4)", width=1),
            ),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=port_hover,
            showlegend=False,
            name="affected_ports",
        ))

    # ── Legend traces ─────────────────────────────────────────────────────────
    for rtype, rcolor in [
        ("TYPHOON",   "#8b5cf6"),
        ("HURRICANE", "#ef4444"),
        ("STORM",     "#f97316"),
        ("MONSOON",   "#06b6d4"),
        ("FOG",       "#94a3b8"),
    ]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=10, color=rcolor),
            name=rtype,
            showlegend=True,
        ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        height=440,
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="natural earth",
            showland=True,        landcolor="#1e293b",
            showocean=True,       oceancolor="#0a0f1a",
            showcoastlines=True,  coastlinecolor="rgba(255,255,255,0.18)",
            showframe=False,
            bgcolor=C_BG,
            showcountries=True,   countrycolor="rgba(255,255,255,0.07)",
            showlakes=False,
            showrivers=False,
            lataxis=dict(range=[-65, 75]),
            lonaxis=dict(range=[-180, 180]),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="right",  x=1,
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
    st.plotly_chart(fig, use_container_width=True, key="weather_world_risk_map")


# ===========================================================================
# NEW SECTION — Seasonal Patterns Bar Chart (risk by month per major region)
# ===========================================================================

_REGION_RISK_DATA: dict[str, dict] = {
    "Western Pacific": {
        "color": "#8b5cf6",
        "monthly": [0.1, 0.1, 0.1, 0.15, 0.25, 0.40, 0.70, 0.90, 0.95, 0.75, 0.35, 0.15],
    },
    "Atlantic Basin": {
        "color": "#ef4444",
        "monthly": [0.1, 0.1, 0.1, 0.12, 0.20, 0.40, 0.65, 0.80, 0.90, 0.75, 0.35, 0.15],
    },
    "N. Indian Ocean": {
        "color": "#06b6d4",
        "monthly": [0.15, 0.10, 0.15, 0.25, 0.55, 0.65, 0.45, 0.40, 0.45, 0.60, 0.50, 0.20],
    },
    "North Sea / NW Europe": {
        "color": "#f97316",
        "monthly": [0.80, 0.70, 0.55, 0.30, 0.15, 0.10, 0.10, 0.15, 0.25, 0.45, 0.65, 0.80],
    },
    "Trans-Pacific": {
        "color": "#3b82f6",
        "monthly": [0.55, 0.45, 0.30, 0.15, 0.10, 0.12, 0.35, 0.55, 0.60, 0.50, 0.45, 0.60],
    },
    "S. Indian Ocean": {
        "color": "#10b981",
        "monthly": [0.65, 0.70, 0.55, 0.30, 0.15, 0.10, 0.08, 0.08, 0.10, 0.20, 0.40, 0.55],
    },
}


def _render_seasonal_patterns_chart() -> None:
    """Grouped bar chart of historical weather risk by month for major regions."""
    logger.debug("Rendering seasonal patterns bar chart")

    current_month = datetime.date.today().month - 1  # 0-based

    fig = go.Figure()

    for region_name, region_info in _REGION_RISK_DATA.items():
        fig.add_trace(go.Bar(
            name=region_name,
            x=_MONTH_NAMES,
            y=region_info["monthly"],
            marker_color=region_info["color"],
            opacity=0.82,
            hovertemplate=(
                "<b>" + region_name + "</b><br>"
                "Month: %{x}<br>"
                "Risk Index: %{y:.2f}<extra></extra>"
            ),
        ))

    # Current month vertical highlight shape
    fig.add_shape(
        type="rect",
        xref="x", yref="paper",
        x0=current_month - 0.5, x1=current_month + 0.5,
        y0=0, y1=1,
        fillcolor="rgba(255,255,255,0.05)",
        line=dict(color="rgba(255,255,255,0.40)", width=1.5),
        layer="below",
    )

    # "NOW" annotation
    fig.add_annotation(
        x=current_month, y=1.02,
        xref="x", yref="paper",
        text="NOW",
        showarrow=False,
        font=dict(size=9, color=C_TEAL, family="monospace"),
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=320,
        barmode="group",
        bargap=0.18,
        bargroupgap=0.04,
        margin=dict(l=0, r=0, t=24, b=0),
        font=dict(color=C_TEXT2, size=11),
        xaxis=dict(
            tickfont=dict(size=10, color=C_TEXT2),
            gridcolor="rgba(255,255,255,0.04)",
            linecolor="rgba(255,255,255,0.08)",
        ),
        yaxis=dict(
            range=[0, 1.08],
            tickfont=dict(size=10, color=C_TEXT2),
            gridcolor="rgba(255,255,255,0.06)",
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["None", "Low", "Mod", "High", "Max"],
            title=dict(text="Risk Index", font=dict(size=10, color=C_TEXT3)),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="right",  x=1,
            font=dict(size=9, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="weather_seasonal_patterns_chart")


# ===========================================================================
# NEW SECTION — Route-Specific Weather Risk Table
# ===========================================================================

_ROUTE_ALT_MAP: dict[str, str] = {
    "transpacific_eb":          "Northern Great Circle via Aleutians (winter dodge)",
    "asia_europe":              "Cape of Good Hope bypass (Red Sea avoidance)",
    "ningbo_europe":            "Cape of Good Hope bypass (Red Sea avoidance)",
    "transatlantic":            "Southern routing via Azores (hurricane dodge Aug-Oct)",
    "south_asia_to_europe":     "Cape of Good Hope (monsoon season diversion)",
    "sea_transpacific_eb":      "Direct Singapore-Long Beach (avoid NW Pacific storms)",
    "middle_east_to_europe":    "Cape of Good Hope (Red Sea risk bypass)",
    "intra_asia_china_sea":     "Eastern Philippine Sea routing (typhoon avoidance)",
    "china_south_america":      "Southern Pacific corridor (avoid typhoon zone)",
    "med_hub_to_asia":          "Northern Europe via Suez/Cape based on conditions",
    "transpacific_wb":          "Southern routing via Hawaii (winter storm avoidance)",
    "aus_nz_to_asia":           "Coral Sea corridor (cyclone season bypass Dec-Mar)",
    "africa_west_coast":        "Offshore routing avoiding Gulf of Guinea squalls",
    "south_america_eb":         "Mid-Atlantic routing avoiding southern storm tracks",
}


def _render_route_risk_table() -> None:
    """Table of route, current risk level, expected delay, and alternative route."""
    logger.debug("Rendering route weather risk table")

    rows = []

    for route_id in ALL_ROUTE_IDS[:14]:  # cap at 14 for display
        try:
            idx = compute_route_weather_risk(route_id)
            nom = get_nominal_transit_days(route_id)
            exp_d, wst_d = compute_weather_adjusted_eta(route_id, float(nom))
            delay_d = exp_d - nom

            # Derive a simple risk label from score
            score = idx.current_risk_score
            if score >= 0.70:
                risk_level = "HIGH"
                risk_color = C_DANGER
            elif score >= 0.45:
                risk_level = "ELEVATED"
                risk_color = C_ORANGE
            elif score >= 0.25:
                risk_level = "MODERATE"
                risk_color = C_WARN
            else:
                risk_level = "LOW"
                risk_color = C_HIGH

            delay_str = "+" + str(delay_d) + "d" if delay_d > 0 else "Nominal"
            delay_color = C_WARN if delay_d > 2 else (C_DANGER if delay_d > 5 else C_HIGH)

            alt_route = _ROUTE_ALT_MAP.get(route_id, "Standard routing")
            display   = ROUTE_DISPLAY_NAMES.get(route_id, route_id)

            rows.append({
                "display":     display,
                "risk_level":  risk_level,
                "risk_color":  risk_color,
                "score":       score,
                "delay_str":   delay_str,
                "delay_color": delay_color,
                "delay_d":     delay_d,
                "nom":         nom,
                "exp_d":       exp_d,
                "alt_route":   alt_route,
            })
        except Exception:
            pass

    # Sort: highest risk first
    rows.sort(key=lambda r: r["score"], reverse=True)

    # Render as HTML table
    header_html = (
        '<div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,255,255,0.08); '
        'border-radius:12px; overflow:hidden; margin-bottom:8px;">'
        '<table style="width:100%; border-collapse:collapse; font-size:0.80rem;">'
        '<thead>'
        '<tr style="background:rgba(255,255,255,0.04); color:' + C_TEXT3 + '; '
        'font-size:0.65rem; text-transform:uppercase; letter-spacing:0.08em;">'
        '<th style="padding:10px 14px; text-align:left; font-weight:700;">Route</th>'
        '<th style="padding:10px 14px; text-align:center; font-weight:700;">Risk Level</th>'
        '<th style="padding:10px 14px; text-align:center; font-weight:700;">Risk Score</th>'
        '<th style="padding:10px 14px; text-align:center; font-weight:700;">Expected Delay</th>'
        '<th style="padding:10px 14px; text-align:center; font-weight:700;">Transit</th>'
        '<th style="padding:10px 14px; text-align:left; font-weight:700;">Alternative Route</th>'
        '</tr>'
        '</thead>'
        '<tbody>'
    )

    rows_html = ""
    for i, row in enumerate(rows):
        bg = "rgba(255,255,255,0.015)" if i % 2 == 0 else "rgba(0,0,0,0)"
        # Score bar
        bar_w = int(row["score"] * 80)
        score_bar = (
            '<div style="display:flex; align-items:center; gap:6px;">'
            '<div style="flex:1; background:rgba(255,255,255,0.07); border-radius:3px; height:6px;">'
            '<div style="width:' + str(bar_w) + '%; background:' + row["risk_color"] + '; border-radius:3px; height:6px;"></div>'
            '</div>'
            '<span style="color:' + row["risk_color"] + '; font-weight:700; font-size:0.72rem; white-space:nowrap;">'
            + str(int(row["score"] * 100)) + '%</span>'
            '</div>'
        )
        rows_html += (
            '<tr style="background:' + bg + '; border-top:1px solid rgba(255,255,255,0.04);">'
            '<td style="padding:9px 14px; color:' + C_TEXT + '; font-weight:600;">' + row["display"] + '</td>'
            '<td style="padding:9px 14px; text-align:center;">'
            '<span style="background:rgba(0,0,0,0.3); color:' + row["risk_color"] + '; '
            'border:1px solid ' + row["risk_color"] + '55; padding:2px 9px; border-radius:999px; '
            'font-size:0.67rem; font-weight:800;">' + row["risk_level"] + '</span>'
            '</td>'
            '<td style="padding:9px 14px;">' + score_bar + '</td>'
            '<td style="padding:9px 14px; text-align:center; color:' + row["delay_color"] + '; font-weight:700;">'
            + row["delay_str"] + '</td>'
            '<td style="padding:9px 14px; text-align:center; color:' + C_TEXT2 + ';">'
            + str(row["nom"]) + 'd &rarr; ' + str(row["exp_d"]) + 'd</td>'
            '<td style="padding:9px 14px; color:' + C_TEXT3 + '; font-size:0.74rem;">' + row["alt_route"] + '</td>'
            '</tr>'
        )

    footer_html = "</tbody></table></div>"
    st.markdown(header_html + rows_html + footer_html, unsafe_allow_html=True)


# ===========================================================================
# NEW SECTION — Typhoon/Hurricane Season Tracker
# ===========================================================================

_SEASON_DATA: list[dict] = [
    {
        "name": "Western Pacific Typhoon",
        "icon": "🌀",
        "active_months": [5, 6, 7, 8, 9, 10, 11],
        "peak_months": [8, 9, 10],
        "color": "#8b5cf6",
        "region": "W. Pacific / SE Asia",
        "avg_storms": "26 named storms",
        "impact": "Trans-Pacific, intra-Asia, Australia routes",
        "status_active": "Above-average intensity expected",
        "status_inactive": "Off-season — monitoring begins May",
    },
    {
        "name": "Atlantic Hurricane",
        "icon": "🌪",
        "active_months": [6, 7, 8, 9, 10, 11],
        "peak_months": [8, 9, 10],
        "color": "#ef4444",
        "region": "Atlantic / Gulf of Mexico / Caribbean",
        "avg_storms": "14-18 named storms",
        "impact": "US East Coast, Gulf ports, transatlantic routes",
        "status_active": "NOAA forecasts above-normal season",
        "status_inactive": "Off-season — re-activates June 1",
    },
    {
        "name": "SW Indian Ocean Cyclone",
        "icon": "🌊",
        "active_months": [11, 12, 1, 2, 3, 4],
        "peak_months": [1, 2, 3],
        "color": "#06b6d4",
        "region": "South Indian Ocean / Mozambique Channel",
        "avg_storms": "9-12 named cyclones",
        "impact": "Cape of Good Hope routing, Durban, Madagascar",
        "status_active": "Southern hemisphere season — Mozambique channel at risk",
        "status_inactive": "Off-season — activates November",
    },
    {
        "name": "Bay of Bengal Cyclone",
        "icon": "🌧",
        "active_months": [4, 5, 10, 11],
        "peak_months": [5, 11],
        "color": "#f59e0b",
        "region": "Bay of Bengal / N. Indian Ocean",
        "avg_storms": "4-6 cyclones biannually",
        "impact": "Chittagong, Kolkata, Chennai, Colombo",
        "status_active": "Pre/post-monsoon cyclone window active",
        "status_inactive": "Monsoon suppresses cyclone formation",
    },
]


def _render_season_tracker() -> None:
    """Active season status cards for typhoon/hurricane seasons."""
    logger.debug("Rendering typhoon/hurricane season tracker")

    current_month = datetime.date.today().month
    cols = st.columns(4)

    for i, season in enumerate(_SEASON_DATA):
        is_active = current_month in season["active_months"]
        is_peak   = current_month in season["peak_months"]

        if is_peak:
            card_border = season["color"]
            status_text = "PEAK SEASON"
            status_color = C_DANGER
            pulse_style = " animation:season-glow 2s ease-in-out infinite;"
        elif is_active:
            card_border = season["color"] + "88"
            status_text = "IN SEASON"
            status_color = C_ORANGE
            pulse_style = ""
        else:
            card_border = "rgba(255,255,255,0.08)"
            status_text = "OFF SEASON"
            status_color = C_TEXT3
            pulse_style = ""

        detail_text = season["status_active"] if is_active else season["status_inactive"]

        # Month strip: 12 segments colored if in season
        month_strip_html = '<div style="display:flex; gap:2px; margin-bottom:10px;">'
        for m in range(12):
            in_season = (m + 1) in season["active_months"]
            in_peak   = (m + 1) in season["peak_months"]
            seg_color = season["color"] if in_season else "rgba(255,255,255,0.08)"
            seg_opacity = "1" if in_peak else ("0.55" if in_season else "1")
            month_strip_html += (
                '<div style="flex:1; height:5px; border-radius:2px; '
                'background:' + seg_color + '; opacity:' + seg_opacity + ';"></div>'
            )
        month_strip_html += '</div>'

        with cols[i % 4]:
            st.markdown(
                '<div style="background:' + C_CARD + '; border:1px solid ' + card_border + '; '
                'border-radius:12px; padding:16px; height:100%;' + pulse_style + '">'

                '<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px;">'
                '<span style="font-size:1.8rem;">' + season["icon"] + '</span>'
                '<span style="background:rgba(0,0,0,0.35); color:' + status_color + '; '
                'border:1px solid ' + status_color + '55; padding:2px 8px; border-radius:999px; '
                'font-size:0.62rem; font-weight:800; letter-spacing:0.06em;">' + status_text + '</span>'
                '</div>'

                '<div style="font-size:0.88rem; font-weight:700; color:' + C_TEXT + '; margin-bottom:4px;">'
                + season["name"] + '</div>'
                '<div style="font-size:0.70rem; color:' + season["color"] + '; font-weight:600; margin-bottom:8px;">'
                + season["region"] + '</div>'

                '<div style="font-size:0.72rem; color:' + C_TEXT3 + '; margin-bottom:6px;">'
                'Avg: ' + season["avg_storms"] + '</div>'

                + month_strip_html

                + '<div style="font-size:0.70rem; color:' + C_TEXT3 + '; line-height:1.45; margin-bottom:8px;">'
                '<b style="color:' + C_TEXT2 + ';">Impact:</b> ' + season["impact"] + '</div>'

                '<div style="font-size:0.72rem; color:' + status_color + '; line-height:1.4; '
                'font-style:italic;">' + detail_text + '</div>'

                '</div>',
                unsafe_allow_html=True,
            )


# ===========================================================================
# NEW SECTION — Port Weather Windows Calendar Heatmap
# ===========================================================================

_PORT_WEATHER_WINDOWS: dict[str, list[float]] = {
    # Risk index per month (0 = ideal, 1 = worst): lower is better for operations
    "Shanghai":    [0.30, 0.25, 0.25, 0.20, 0.25, 0.35, 0.55, 0.65, 0.65, 0.50, 0.35, 0.30],
    "Singapore":   [0.20, 0.20, 0.25, 0.30, 0.35, 0.40, 0.40, 0.35, 0.35, 0.35, 0.30, 0.20],
    "Rotterdam":   [0.70, 0.65, 0.50, 0.30, 0.20, 0.15, 0.15, 0.18, 0.25, 0.40, 0.55, 0.70],
    "Los Angeles": [0.35, 0.30, 0.25, 0.20, 0.15, 0.15, 0.20, 0.25, 0.30, 0.30, 0.30, 0.35],
    "Busan":       [0.40, 0.35, 0.30, 0.25, 0.20, 0.25, 0.50, 0.65, 0.70, 0.50, 0.35, 0.40],
    "Hong Kong":   [0.25, 0.25, 0.30, 0.35, 0.40, 0.50, 0.75, 0.80, 0.75, 0.55, 0.35, 0.25],
    "Colombo":     [0.30, 0.25, 0.25, 0.35, 0.60, 0.75, 0.70, 0.65, 0.55, 0.50, 0.45, 0.30],
    "Hamburg":     [0.75, 0.70, 0.55, 0.30, 0.20, 0.15, 0.15, 0.18, 0.28, 0.42, 0.58, 0.72],
    "New York":    [0.50, 0.45, 0.40, 0.30, 0.20, 0.20, 0.25, 0.35, 0.50, 0.45, 0.40, 0.50],
    "Chittagong":  [0.25, 0.20, 0.25, 0.40, 0.65, 0.80, 0.75, 0.70, 0.65, 0.55, 0.35, 0.25],
    "Port Said":   [0.30, 0.30, 0.35, 0.30, 0.25, 0.30, 0.40, 0.45, 0.40, 0.30, 0.30, 0.30],
    "Yokohama":    [0.35, 0.30, 0.30, 0.25, 0.20, 0.22, 0.45, 0.60, 0.65, 0.50, 0.35, 0.35],
}


def _render_port_weather_windows() -> None:
    """Calendar heatmap showing best/worst months for major ports."""
    logger.debug("Rendering port weather windows heatmap")

    port_names = list(_PORT_WEATHER_WINDOWS.keys())
    z_data = [_PORT_WEATHER_WINDOWS[p] for p in port_names]

    current_month_idx = datetime.date.today().month - 1

    fig = go.Figure(data=go.Heatmap(
        z=z_data,
        x=_MONTH_NAMES,
        y=port_names,
        colorscale=[
            [0.0,  "#10b981"],
            [0.25, "#34d399"],
            [0.40, "#fbbf24"],
            [0.60, "#f97316"],
            [0.80, "#ef4444"],
            [1.0,  "#7f1d1d"],
        ],
        zmin=0.0,
        zmax=1.0,
        showscale=True,
        colorbar=dict(
            title=dict(text="Risk (0=Best, 1=Worst)", font=dict(size=10, color=C_TEXT3)),
            tickfont=dict(size=9, color=C_TEXT2),
            tickvals=[0.0, 0.25, 0.50, 0.75, 1.0],
            ticktext=["Best", "Good", "Caution", "Poor", "Avoid"],
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(255,255,255,0.10)",
            thickness=12,
            len=0.8,
        ),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Month: %{x}<br>"
            "Operational Risk: %{z:.2f}<extra></extra>"
        ),
        xgap=3,
        ygap=2,
    ))

    # Current month highlight
    fig.add_shape(
        type="rect", xref="x", yref="paper",
        x0=current_month_idx - 0.5, x1=current_month_idx + 0.5,
        y0=0, y1=1,
        fillcolor="rgba(255,255,255,0.05)",
        line=dict(color="rgba(255,255,255,0.40)", width=1.5),
        layer="above",
    )
    fig.add_annotation(
        x=current_month_idx, y=1.03,
        xref="x", yref="paper",
        text="NOW",
        showarrow=False,
        font=dict(size=9, color=C_TEAL, family="monospace"),
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=400,
        margin=dict(l=0, r=80, t=24, b=0),
        font=dict(color=C_TEXT),
        xaxis=dict(
            tickfont=dict(size=10, color=C_TEXT2),
            side="top",
            gridcolor="rgba(255,255,255,0.03)",
        ),
        yaxis=dict(
            tickfont=dict(size=10, color=C_TEXT2),
            autorange="reversed",
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="weather_port_windows_heatmap")

    # Legend note
    st.markdown(
        '<div style="font-size:0.72rem; color:' + C_TEXT3 + '; margin-top:2px;">'
        'Green = ideal operating window &nbsp;|&nbsp; Red = avoid if possible &nbsp;|&nbsp; '
        'Highlighted column = current month</div>',
        unsafe_allow_html=True,
    )


# ===========================================================================
# ORIGINAL Section 1 — Weather Risk Globe
# ===========================================================================

def _render_weather_globe() -> None:
    """Orthographic dark globe with weather risk zones and affected ports."""
    logger.debug("Rendering weather risk globe")

    month = datetime.date.today().month
    active_event_names = {ev.event_name for ev in WEATHER_RISK_EVENTS if month in ev.season_months}

    fig = go.Figure()

    # ── Risk zone blurred circles — layered opacity rings per system ──────
    import math
    for system in _WEATHER_SYSTEM_CENTERS:
        # Guard: skip entries missing required coordinate keys
        if system.get("lat") is None or system.get("lon") is None:
            logger.warning("Weather system {} missing lat/lon — skipping", system.get("name", "?"))
            continue
        is_active = system["event_name"] in active_event_names
        base_color = _RISK_TYPE_COLOR.get(system["risk_type"], C_TEXT2)
        base_size = 55 if is_active else 35

        # Glow halo (large, very transparent)
        fig.add_trace(go.Scattergeo(
            lat=[system["lat"]],
            lon=[system["lon"]],
            mode="markers",
            marker=dict(
                size=base_size * 2.5,
                color=base_color,
                opacity=0.07,
                line=dict(width=0),
            ),
            hoverinfo="skip",
            showlegend=False,
            name=system["name"] + "_glow3",
        ))

        # Middle ring
        fig.add_trace(go.Scattergeo(
            lat=[system["lat"]],
            lon=[system["lon"]],
            mode="markers",
            marker=dict(
                size=base_size * 1.5,
                color=base_color,
                opacity=0.13,
                line=dict(width=0),
            ),
            hoverinfo="skip",
            showlegend=False,
            name=system["name"] + "_glow2",
        ))

        # Inner ring
        hover_text = (
            "<b>" + system["name"] + "</b><br>"
            + "Type: " + system["risk_type"] + "<br>"
            + ("IN SEASON" if is_active else "Out of season")
        )
        fig.add_trace(go.Scattergeo(
            lat=[system["lat"]],
            lon=[system["lon"]],
            mode="markers",
            marker=dict(
                size=base_size,
                color=base_color,
                opacity=0.22 if is_active else 0.10,
                line=dict(
                    color=base_color,
                    width=1.5 if is_active else 0.5,
                ),
            ),
            hovertemplate=hover_text + "<extra></extra>",
            showlegend=False,
            name=system["name"],
        ))

    # ── Active system label markers ───────────────────────────────────────
    active_lats = [s["lat"] for s in _WEATHER_SYSTEM_CENTERS
                   if s["event_name"] in active_event_names]
    active_lons = [s["lon"] for s in _WEATHER_SYSTEM_CENTERS
                   if s["event_name"] in active_event_names]
    active_labels = [s["risk_type"][0] for s in _WEATHER_SYSTEM_CENTERS
                     if s["event_name"] in active_event_names]

    if active_lats:
        fig.add_trace(go.Scattergeo(
            lat=active_lats,
            lon=active_lons,
            mode="text",
            text=active_labels,
            textfont=dict(size=10, color="white"),
            hoverinfo="skip",
            showlegend=False,
            name="active_labels",
        ))

    # ── Affected ports — amber/red highlights ─────────────────────────────
    # Guard: skip any port entries missing lat/lon coordinates
    valid_ports = [
        p for p in _AFFECTED_PORT_COORDS
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    port_lats = [p["lat"] for p in valid_ports]
    port_lons = [p["lon"] for p in valid_ports]
    port_colors = [C_DANGER if p["risk"] == "HIGH" else C_WARN for p in valid_ports]
    port_hover = [
        "<b>" + p["name"] + "</b> (" + p["locode"] + ")<br>Weather Risk: " + p["risk"]
        for p in valid_ports
    ]

    # Port glow layer
    fig.add_trace(go.Scattergeo(
        lat=port_lats,
        lon=port_lons,
        mode="markers",
        marker=dict(size=18, color=port_colors, opacity=0.12, line=dict(width=0)),
        hoverinfo="skip",
        showlegend=False,
        name="port_glow",
    ))

    # Port main markers
    fig.add_trace(go.Scattergeo(
        lat=port_lats,
        lon=port_lons,
        mode="markers",
        marker=dict(
            size=7,
            color=port_colors,
            opacity=0.85,
            symbol="diamond",
            line=dict(color="rgba(255,255,255,0.4)", width=1),
        ),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=port_hover,
        showlegend=False,
        name="affected_ports",
    ))

    # ── Legend swatches ───────────────────────────────────────────────────
    for rtype, rcolor in [
        ("TYPHOON",   "#8b5cf6"),
        ("HURRICANE", "#ef4444"),
        ("STORM",     "#f97316"),
        ("MONSOON",   "#06b6d4"),
        ("FOG",       "#94a3b8"),
        ("ICE",       "#bfdbfe"),
    ]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=10, color=rcolor),
            name=rtype,
            showlegend=True,
        ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        height=500,
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="orthographic",
            showland=True,        landcolor="#1a2235",
            showocean=True,       oceancolor="#0a0f1a",
            showcoastlines=True,  coastlinecolor="rgba(255,255,255,0.15)",
            showframe=False,
            bgcolor="#0a0f1a",
            showcountries=True,   countrycolor="rgba(255,255,255,0.06)",
            showlakes=False,
            projection_rotation=dict(lon=85, lat=20, roll=0),
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

    st.plotly_chart(fig, use_container_width=True, key="weather_globe_chart")


# ---------------------------------------------------------------------------
# Section 2: Seasonal Risk Calendar (12-month heatmap)
# ---------------------------------------------------------------------------

# Routes shown in the heatmap (major lanes)
_HEATMAP_ROUTES: list[str] = [
    "transpacific_eb",
    "asia_europe",
    "ningbo_europe",
    "transatlantic",
    "south_asia_to_europe",
    "sea_transpacific_eb",
    "middle_east_to_europe",
    "intra_asia_china_sea",
    "china_south_america",
    "med_hub_to_asia",
]

_RISK_LEVEL_NUM: dict[str, float] = {
    "ACTIVE":   1.0,
    "ELEVATED": 0.7,
    "SEASONAL": 0.4,
    "LOW":      0.1,
}


def _compute_monthly_risk_matrix() -> tuple:
    """Return (z_matrix, y_labels) for the heatmap.

    z_matrix[route_idx][month_idx] = risk score 0-1 for that route x month.
    """
    z: list[list[float]] = []
    y_labels: list[str] = []
    annotations: list[dict] = []

    for ri, route_id in enumerate(_HEATMAP_ROUTES):
        row: list[float] = []
        y_labels.append(ROUTE_DISPLAY_NAMES.get(route_id, route_id))
        events = [ev for ev in WEATHER_RISK_EVENTS if route_id in ev.affected_routes]
        for month_idx in range(1, 13):
            # Sum contributions from events active in this month
            month_score = 0.0
            primary_type = ""
            best_contrib = 0.0
            for ev in events:
                if month_idx in ev.season_months:
                    lvl = _RISK_LEVEL_NUM.get(ev.current_risk_level, 0.1)
                    prob = ev.probability_pct / 100.0
                    contrib = lvl * prob
                    month_score += contrib
                    if contrib > best_contrib:
                        best_contrib = contrib
                        primary_type = ev.risk_type[0]  # first letter abbreviation
            clamped = min(1.0, month_score)
            row.append(round(clamped, 3))
            if clamped > 0.15 and primary_type:
                annotations.append(dict(
                    x=month_idx - 1,
                    y=ri,
                    text=primary_type,
                    font=dict(size=8, color="rgba(255,255,255,0.55)"),
                    showarrow=False,
                ))
        z.append(row)

    return z, y_labels, annotations


def _render_seasonal_calendar() -> None:
    """12-month x N-route risk heatmap with current month highlighted."""
    logger.debug("Rendering seasonal risk calendar heatmap")

    z, y_labels, annotations = _compute_monthly_risk_matrix()
    current_month_idx = datetime.date.today().month - 1  # 0-based

    # Current month vertical highlight via shape
    shapes = [
        dict(
            type="rect",
            xref="x",
            yref="paper",
            x0=current_month_idx - 0.5,
            x1=current_month_idx + 0.5,
            y0=0,
            y1=1,
            fillcolor="rgba(255,255,255,0.06)",
            line=dict(color="rgba(255,255,255,0.35)", width=1.5),
            layer="above",
        )
    ]

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=_MONTH_NAMES,
        y=y_labels,
        colorscale=[
            [0.0,  "#0a0f1a"],
            [0.15, "#1e3a5f"],
            [0.35, "#1d4ed8"],
            [0.55, "#f59e0b"],
            [0.75, "#f97316"],
            [1.0,  "#ef4444"],
        ],
        zmin=0.0,
        zmax=1.0,
        showscale=True,
        colorbar=dict(
            title=dict(text="Risk Score", font=dict(size=11, color=C_TEXT2)),
            tickfont=dict(size=10, color=C_TEXT2),
            tickvals=[0.0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["None", "Low", "Mod", "High", "Max"],
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(255,255,255,0.10)",
            thickness=12,
            len=0.8,
        ),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Month: %{x}<br>"
            "Risk Score: %{z:.2f}<br>"
            "<extra></extra>"
        ),
        xgap=2,
        ygap=2,
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=380,
        margin=dict(l=0, r=60, t=10, b=0),
        font=dict(color=C_TEXT),
        xaxis=dict(
            tickfont=dict(size=10, color=C_TEXT2),
            side="top",
            gridcolor="rgba(255,255,255,0.04)",
        ),
        yaxis=dict(
            tickfont=dict(size=10, color=C_TEXT2),
            autorange="reversed",
        ),
        annotations=annotations,
        shapes=shapes,
    )

    st.plotly_chart(fig, use_container_width=True, key="weather_seasonal_calendar_chart")


# ---------------------------------------------------------------------------
# Section 3: Current Weather Alerts
# ---------------------------------------------------------------------------

def _render_alert_card(ev: WeatherRiskEvent) -> str:
    """Return HTML for a single weather alert card."""
    level_color = _RISK_LEVEL_COLOR.get(ev.current_risk_level, C_TEXT2)
    border_color = level_color + "44"
    pulse = ev.current_risk_level == "ACTIVE"

    badge_html = _risk_badge(ev.current_risk_level, pulse=pulse)
    type_badge_html = _type_badge(ev.risk_type)

    route_pills = "".join(
        _pill(rid.replace("_", " ").title())
        for rid in ev.affected_routes[:4]
    )
    if len(ev.affected_routes) > 4:
        route_pills += _pill("+" + str(len(ev.affected_routes) - 4) + " more", C_TEXT3)

    port_pills = "".join(_pill(p, C_ORANGE) for p in ev.affected_ports[:5])
    if len(ev.affected_ports) > 5:
        port_pills += _pill("+" + str(len(ev.affected_ports) - 5) + " more", C_TEXT3)

    prob_color = (
        C_DANGER  if ev.probability_pct >= 70
        else C_ORANGE if ev.probability_pct >= 50
        else C_WARN   if ev.probability_pct >= 30
        else C_HIGH
    )

    delay_str = str(ev.delay_days_if_occurs)
    if ev.delay_days_if_occurs == int(ev.delay_days_if_occurs):
        delay_str = str(int(ev.delay_days_if_occurs))

    month_abbrs = ", ".join(_MONTH_NAMES[m - 1] for m in sorted(ev.season_months))

    html = (
        "<div style=\"background:" + C_CARD + "; border:1px solid " + border_color + ";"
        " border-radius:12px; padding:15px 17px; margin-bottom:10px\">"
        # Header
        "<div style=\"display:flex; justify-content:space-between; align-items:flex-start;"
        " margin-bottom:9px; flex-wrap:wrap; gap:5px\">"
        "<div style=\"display:flex; gap:7px; align-items:center; flex-wrap:wrap\">"
        + badge_html + type_badge_html
        + "</div>"
        "<div style=\"font-size:0.68rem; color:" + C_TEXT3 + "; white-space:nowrap\">"
        "Season: " + month_abbrs
        + "</div>"
        "</div>"
        # Title
        "<div style=\"font-size:0.92rem; font-weight:700; color:" + C_TEXT + ";"
        " margin-bottom:7px\">" + ev.event_name + "</div>"
        # Description (truncated)
        "<div style=\"font-size:0.78rem; color:" + C_TEXT2 + "; line-height:1.5;"
        " margin-bottom:10px\">"
        + ev.description[:260] + ("..." if len(ev.description) > 260 else "")
        + "</div>"
        # Metrics row
        "<div style=\"display:grid; grid-template-columns:1fr 1fr; gap:10px;"
        " margin-bottom:9px\">"
        # Probability
        "<div style=\"background:rgba(0,0,0,0.2); border-radius:8px; padding:8px 10px;"
        " text-align:center\">"
        "<div style=\"font-size:1.3rem; font-weight:800; color:" + prob_color + "\">"
        + str(int(ev.probability_pct)) + "%</div>"
        "<div style=\"font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.06em\">Disruption Prob.</div>"
        "</div>"
        # Delay
        "<div style=\"background:rgba(0,0,0,0.2); border-radius:8px; padding:8px 10px;"
        " text-align:center\">"
        "<div style=\"font-size:1.3rem; font-weight:800; color:" + C_WARN + "\">"
        "+" + delay_str + "d</div>"
        "<div style=\"font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.06em\">If Occurs</div>"
        "</div>"
        "</div>"
        # Routes
        "<div style=\"margin-bottom:6px\">"
        "<div style=\"font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.07em; margin-bottom:4px\">Affected Routes</div>"
        + route_pills
        + "</div>"
        # Ports
        "<div style=\"margin-bottom:6px\">"
        "<div style=\"font-size:0.63rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.07em; margin-bottom:4px\">Affected Ports</div>"
        + port_pills
        + "</div>"
        # Mitigation
        "<div style=\"background:rgba(16,185,129,0.06); border:1px solid rgba(16,185,129,0.18);"
        " border-radius:6px; padding:7px 10px; margin-top:6px\">"
        "<div style=\"font-size:0.62rem; color:" + C_HIGH + "; text-transform:uppercase;"
        " letter-spacing:0.07em; margin-bottom:3px\">Mitigation</div>"
        "<div style=\"font-size:0.74rem; color:" + C_TEXT2 + "; line-height:1.45\">"
        + ev.mitigation[:220] + ("..." if len(ev.mitigation) > 220 else "")
        + "</div>"
        "</div>"
        "</div>"
    )
    return html


def _render_current_alerts() -> None:
    """Render ACTIVE/ELEVATED/SEASONAL alert cards for the current month."""
    logger.debug("Rendering current weather alert cards")

    st.markdown(_PULSE_CSS, unsafe_allow_html=True)
    alerts = get_current_season_alerts()

    if not alerts:
        st.success("No major weather disruptions affecting tracked routes this month.")
        return

    month_name = _MONTH_NAMES[datetime.date.today().month - 1]
    st.markdown(
        "<div style=\"font-size:0.78rem; color:" + C_TEXT2 + "; margin-bottom:12px\">"
        + str(len(alerts)) + " active weather risks in " + month_name + " — "
        "sorted by severity."
        "</div>",
        unsafe_allow_html=True,
    )

    col_a, col_b = st.columns(2)
    for i, ev in enumerate(alerts):
        html = _render_alert_card(ev)
        if i % 2 == 0:
            with col_a:
                st.markdown(html, unsafe_allow_html=True)
        else:
            with col_b:
                st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 4: Route Weather Profile
# ---------------------------------------------------------------------------

def _render_route_profile(route_id: str) -> None:
    """Render detailed weather profile for the selected route."""
    logger.debug("Rendering route weather profile for {}", route_id)

    idx = compute_route_weather_risk(route_id)
    nominal = get_nominal_transit_days(route_id)
    expected_days, worst_days = compute_weather_adjusted_eta(route_id, float(nominal))
    events = [ev for ev in WEATHER_RISK_EVENTS if route_id in ev.affected_routes]
    display_name = ROUTE_DISPLAY_NAMES.get(route_id, route_id)

    # ── Key metrics row ───────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    score_pct = int(idx.current_risk_score * 100)
    score_color = (
        C_DANGER  if idx.current_risk_score >= 0.70
        else C_ORANGE if idx.current_risk_score >= 0.50
        else C_WARN   if idx.current_risk_score >= 0.30
        else C_HIGH
    )
    with m1:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.25);"
            " border-radius:10px; padding:14px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + score_color + "\">"
            + str(score_pct) + "%</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Weather Risk Score</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(245,158,11,0.25);"
            " border-radius:10px; padding:14px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_WARN + "\">"
            "+" + str(round(idx.annualized_delay_days, 1)) + "d</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Ann. Delay Days</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(59,130,246,0.25);"
            " border-radius:10px; padding:14px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_ACCENT + "\">"
            + str(expected_days) + "d</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Expected ETA (days)</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with m4:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.25);"
            " border-radius:10px; padding:14px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_DANGER + "\">"
            + str(worst_days) + "d</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Worst-Case ETA (days)</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style=\"height:14px\"></div>", unsafe_allow_html=True)

    # ── Monthly risk timeline bar chart ───────────────────────────────────
    monthly_scores: list[float] = []
    for month in range(1, 13):
        month_score = 0.0
        for ev in events:
            if month in ev.season_months:
                lvl = _RISK_LEVEL_NUM.get(ev.current_risk_level, 0.1)
                prob = ev.probability_pct / 100.0
                month_score += lvl * prob
        monthly_scores.append(min(1.0, month_score))

    current_month = datetime.date.today().month
    bar_colors = []
    for mi, score in enumerate(monthly_scores):
        if mi + 1 == current_month:
            bar_colors.append(C_TEAL)
        elif score >= 0.6:
            bar_colors.append(C_DANGER)
        elif score >= 0.35:
            bar_colors.append(C_ORANGE)
        elif score >= 0.15:
            bar_colors.append(C_WARN)
        else:
            bar_colors.append("rgba(255,255,255,0.08)")

    fig_bar = go.Figure(data=go.Bar(
        x=_MONTH_NAMES,
        y=monthly_scores,
        marker_color=bar_colors,
        hovertemplate="<b>%{x}</b><br>Risk Score: %{y:.2f}<extra></extra>",
        name=display_name,
    ))
    fig_bar.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=220,
        margin=dict(l=0, r=0, t=10, b=0),
        font=dict(color=C_TEXT2, size=11),
        xaxis=dict(
            tickfont=dict(size=10),
            gridcolor="rgba(255,255,255,0.04)",
            linecolor="rgba(255,255,255,0.08)",
        ),
        yaxis=dict(
            range=[0, 1.05],
            tickfont=dict(size=10),
            gridcolor="rgba(255,255,255,0.06)",
            tickvals=[0, 0.25, 0.5, 0.75, 1.0],
            ticktext=["0", ".25", ".50", ".75", "1.0"],
        ),
        showlegend=False,
        bargap=0.12,
    )
    st.plotly_chart(fig_bar, use_container_width=True, key="weather_route_monthly_bar_chart")

    # ── Top 3 weather risks ───────────────────────────────────────────────
    if events:
        sorted_events = sorted(
            events,
            key=lambda e: (e.probability_pct / 100.0) * e.delay_days_if_occurs,
            reverse=True,
        )
        top3 = sorted_events[:3]

        st.markdown(
            "<div style=\"font-size:0.78rem; font-weight:700; color:" + C_TEXT + ";"
            " margin-bottom:10px; margin-top:4px\">Top Weather Risks for This Route</div>",
            unsafe_allow_html=True,
        )
        for ev in top3:
            ev_color = _RISK_LEVEL_COLOR.get(ev.current_risk_level, C_TEXT2)
            st.markdown(
                "<div style=\"background:" + C_CARD + "; border-left:3px solid " + ev_color + ";"
                " border-radius:0 8px 8px 0; padding:12px 14px; margin-bottom:8px\">"
                "<div style=\"display:flex; justify-content:space-between; align-items:center;"
                " margin-bottom:6px; flex-wrap:wrap; gap:5px\">"
                "<div style=\"font-size:0.85rem; font-weight:700; color:" + C_TEXT + "\">"
                + ev.event_name
                + "</div>"
                + _type_badge(ev.risk_type)
                + "</div>"
                "<div style=\"font-size:0.76rem; color:" + C_TEXT2 + "; line-height:1.4;"
                " margin-bottom:8px\">"
                + ev.description[:200] + "..."
                + "</div>"
                "<div style=\"background:rgba(16,185,129,0.06);"
                " border:1px solid rgba(16,185,129,0.15);"
                " border-radius:6px; padding:6px 10px\">"
                "<span style=\"font-size:0.62rem; color:" + C_HIGH + "; text-transform:uppercase;"
                " letter-spacing:0.07em\">Mitigation: </span>"
                "<span style=\"font-size:0.74rem; color:" + C_TEXT2 + "\">"
                + ev.mitigation[:160] + "..."
                + "</span>"
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    # ── Historical disruption frequency ───────────────────────────────────
    hist_pct = idx.historical_disruption_frequency_pct
    freq_color = (
        C_DANGER  if hist_pct >= 65
        else C_ORANGE if hist_pct >= 45
        else C_WARN   if hist_pct >= 25
        else C_HIGH
    )
    clamped_bar_w = min(100, hist_pct)
    st.markdown(
        "<div style=\"background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
        " border-radius:10px; padding:14px 16px; margin-top:4px\">"
        "<div style=\"font-size:0.72rem; font-weight:700; color:" + C_TEXT + ";"
        " margin-bottom:8px\">Historical Disruption Frequency</div>"
        "<div style=\"display:flex; justify-content:space-between; margin-bottom:5px\">"
        "<span style=\"font-size:0.72rem; color:" + C_TEXT2 + "\">"
        "% of voyages historically disrupted by weather</span>"
        "<span style=\"font-size:0.82rem; font-weight:800; color:" + freq_color + "\">"
        + str(hist_pct) + "%"
        + "</span>"
        "</div>"
        "<div style=\"background:rgba(255,255,255,0.07); border-radius:4px; height:8px\">"
        "<div style=\"width:" + str(clamped_bar_w) + "%; background:" + freq_color + ";"
        " border-radius:4px; height:8px\"></div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── Weather-adjusted vs nominal ETA ──────────────────────────────────
    # Derive a confidence level from the number and quality of data events
    if events:
        avg_confidence = sum(
            ev.probability_pct for ev in events
        ) / len(events)
        if len(events) >= 5 and avg_confidence >= 60:
            conf_label = "High"
            conf_color = C_HIGH
        elif len(events) >= 2 or avg_confidence >= 35:
            conf_label = "Medium"
            conf_color = C_WARN
        else:
            conf_label = "Low"
            conf_color = C_ORANGE
    else:
        conf_label = "Low (no event data)"
        conf_color = C_TEXT3

    st.markdown(
        "<div style=\"background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
        " border-radius:10px; padding:14px 16px; margin-top:10px\">"
        "<div style=\"display:flex; justify-content:space-between; align-items:center;"
        " margin-bottom:10px\">"
        "<div style=\"font-size:0.72rem; font-weight:700; color:" + C_TEXT + "\">"
        "ETA Comparison (days transit)</div>"
        "<div style=\"font-size:0.68rem; color:" + C_TEXT3 + "\">"
        "Estimate confidence: "
        "<span style=\"font-weight:700; color:" + conf_color + "\">" + conf_label + "</span>"
        "</div>"
        "</div>"
        "<div style=\"display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px;"
        " text-align:center\">"
        # Nominal
        "<div>"
        "<div style=\"font-size:1.25rem; font-weight:800; color:" + C_TEXT + "\">"
        + str(nominal)
        + "d</div>"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.06em\">Nominal</div>"
        "</div>"
        # Expected
        "<div>"
        "<div style=\"font-size:1.25rem; font-weight:800; color:" + C_WARN + "\">"
        + str(expected_days)
        + "d</div>"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.06em\">Expected</div>"
        "</div>"
        # Worst case
        "<div>"
        "<div style=\"font-size:1.25rem; font-weight:800; color:" + C_DANGER + "\">"
        + str(worst_days)
        + "d</div>"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.06em\">Worst Case</div>"
        "</div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 5: El Nino / La Nina Monitor
# ---------------------------------------------------------------------------

def _render_enso_monitor() -> None:
    """Special card for current ENSO phase and shipping implications."""
    logger.debug("Rendering ENSO monitor card")

    # Hardcoded: Neutral transitioning to La Nina (current as of early 2026)
    enso_phase = "Neutral (transitioning to La Nina)"
    enso_color = C_TEAL
    enso_icon = "~"  # neutral indicator

    st.markdown(
        # Outer card
        "<div style=\"background:" + C_CARD + "; border:1px solid rgba(6,182,212,0.30);"
        " border-radius:12px; padding:18px 20px; margin-bottom:12px\">"

        # Title bar
        "<div style=\"display:flex; justify-content:space-between; align-items:center;"
        " margin-bottom:14px; flex-wrap:wrap; gap:8px\">"
        "<div>"
        "<div style=\"font-size:0.95rem; font-weight:700; color:" + C_TEXT + "\">"
        "El Nino / La Nina ENSO Monitor"
        "</div>"
        "<div style=\"font-size:0.75rem; color:" + C_TEXT2 + "; margin-top:3px\">"
        "El Nino-Southern Oscillation — key driver of global shipping weather patterns"
        "</div>"
        "</div>"
        "<span style=\"background:rgba(6,182,212,0.12); color:" + enso_color + ";"
        " border:1px solid rgba(6,182,212,0.30);"
        " padding:4px 14px; border-radius:999px; font-size:0.78rem; font-weight:700\">"
        + enso_icon + " " + enso_phase
        + "</span>"
        "</div>"

        # 3-column impact grid
        "<div style=\"display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px;"
        " margin-bottom:14px\">"

        # Panama Canal
        "<div style=\"background:rgba(0,0,0,0.2); border-radius:8px; padding:12px\">"
        "<div style=\"font-size:0.68rem; text-transform:uppercase; letter-spacing:0.07em;"
        " color:" + C_TEXT3 + "; margin-bottom:6px\">Panama Canal Water Levels</div>"
        "<div style=\"font-size:0.82rem; color:" + C_TEXT + "; font-weight:600;"
        " margin-bottom:5px\">Recovering / Improving</div>"
        "<div style=\"font-size:0.74rem; color:" + C_TEXT2 + "; line-height:1.45\">"
        "La Nina conditions bring above-normal rainfall to the Panama watershed. "
        "Gatun Lake levels are expected to recover from the 2023-24 El Nino lows. "
        "Draft restrictions should ease to near-normal by mid-2026. "
        "Neo-panamax transit capacity expected to increase from Q2 2026."
        "</div>"
        "</div>"

        # Trans-Pacific Weather
        "<div style=\"background:rgba(0,0,0,0.2); border-radius:8px; padding:12px\">"
        "<div style=\"font-size:0.68rem; text-transform:uppercase; letter-spacing:0.07em;"
        " color:" + C_TEXT3 + "; margin-bottom:6px\">Trans-Pacific Weather Patterns</div>"
        "<div style=\"font-size:0.82rem; color:" + C_WARN + "; font-weight:600;"
        " margin-bottom:5px\">Above-Normal Storm Risk</div>"
        "<div style=\"font-size:0.74rem; color:" + C_TEXT2 + "; line-height:1.45\">"
        "La Nina phases correlate with more intense western Pacific typhoon seasons "
        "and stronger North Pacific winter storms. Trans-Pacific eastbound crossings "
        "face elevated delay risk November-March. Typhoon season expected to be "
        "above-average intensity in 2026 peak season (August-October)."
        "</div>"
        "</div>"

        # Rate Implications
        "<div style=\"background:rgba(0,0,0,0.2); border-radius:8px; padding:12px\">"
        "<div style=\"font-size:0.68rem; text-transform:uppercase; letter-spacing:0.07em;"
        " color:" + C_TEXT3 + "; margin-bottom:6px\">Rate Implications</div>"
        "<div style=\"font-size:0.82rem; color:" + C_HIGH + "; font-weight:600;"
        " margin-bottom:5px\">Panama Relief, Pacific Premium</div>"
        "<div style=\"font-size:0.74rem; color:" + C_TEXT2 + "; line-height:1.45\">"
        "Panama Canal capacity recovery removes the 2024 draft surcharge (+$300-600/FEU) "
        "on eligible vessels. Trans-Pacific westbound rates may see a weather premium "
        "of $100-200/FEU in winter season. South American Pacific Coast cargo faces "
        "calmer conditions under La Nina, reducing schedule delays."
        "</div>"
        "</div>"

        "</div>"  # end grid

        # ENSO index visual (simplified text bar)
        "<div style=\"border-top:1px solid rgba(255,255,255,0.07); padding-top:12px\">"
        "<div style=\"font-size:0.68rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.07em; margin-bottom:7px\">Nino 3.4 SST Anomaly Index (Schematic)</div>"
        "<div style=\"position:relative; height:20px; background:linear-gradient("
        "to right, #1d4ed8 0%, #06b6d4 40%, #1a2235 50%, #f59e0b 65%, #ef4444 100%);"
        " border-radius:4px; margin-bottom:5px\">"
        # Pointer near neutral (50% = 0.0 anomaly)
        "<div style=\"position:absolute; left:48%; top:-4px; width:4px; height:28px;"
        " background:white; border-radius:2px\"></div>"
        "</div>"
        "<div style=\"display:flex; justify-content:space-between;"
        " font-size:0.62rem; color:" + C_TEXT3 + "\">"
        "<span>Strong La Nina (-2.0)</span>"
        "<span>Neutral (0.0)</span>"
        "<span>Strong El Nino (+2.0)</span>"
        "</div>"
        "<div style=\"font-size:0.73rem; color:" + C_TEXT2 + "; margin-top:8px;"
        " line-height:1.5\">"
        "<b style=\"color:" + C_TEXT + "\">Current reading:</b> "
        "ONI (Oceanic Nino Index) approximately -0.2 to -0.4 "
        "(Neutral, with cooling trend toward La Nina threshold of -0.5). "
        "Three-month forecast consensus (CPC/IRI): 55% probability of La Nina "
        "conditions developing by Q2 2026. ENSO-neutral likely to persist through "
        "mid-2026 before potential La Nina consolidation. Monitoring advisory remains "
        "in effect."
        "</div>"
        "</div>"

        "</div>",  # end outer card
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(port_results, route_results, freight_data=None) -> None:
    """Render the Weather Risk tab.

    Parameters
    ----------
    port_results : list[PortDemandResult]
        Current port demand results.
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer.
    freight_data : dict, optional
        Freight index data (reserved for future use in new sections).
    """
    logger.info("Rendering Weather Risk tab")

    st.markdown(_PULSE_CSS, unsafe_allow_html=True)
    st.header("Weather Risk Intelligence")

    # ══════════════════════════════════════════════════════════════════════════
    # NEW — Active Alerts Banner
    # ══════════════════════════════════════════════════════════════════════════
    _render_alerts_banner()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW — Weather Risk World Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Global Weather Risk Map",
        (
            "Natural-earth projection with active storm systems, cyclone zones, and "
            "affected port markers (diamonds). Larger glows = higher severity. "
            "In-season systems are brighter. Hover for details."
        ),
    )
    _render_world_risk_map()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW — Seasonal Patterns Chart
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Seasonal Weather Risk Patterns by Region",
        (
            "Historical risk index by month for six major shipping regions. "
            "Highlighted column = current month. Higher bars = more weather-related disruption risk."
        ),
    )
    _render_seasonal_patterns_chart()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW — Route-Specific Weather Risk Table
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Route Weather Risk Summary",
        (
            "Current weather risk score, expected delay, and recommended alternative routing "
            "for all tracked trade lanes. Sorted highest-risk first."
        ),
    )
    _render_route_risk_table()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW — Typhoon/Hurricane Season Tracker
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Active Season Tracker — Cyclone & Hurricane Basins",
        (
            "Real-time season status across the four major tropical storm basins. "
            "Month strip shows the season window (bright = peak months)."
        ),
    )
    _render_season_tracker()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # NEW — Port Weather Windows Heatmap
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Port Weather Windows — Best Operating Months",
        (
            "Operational risk calendar for 12 major ports. "
            "Green = ideal window, red = highest weather risk. "
            "Use this to plan booking windows and vessel scheduling."
        ),
    )
    _render_port_weather_windows()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # ORIGINAL Section 1 — Weather Risk Globe
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "3D Weather Risk Globe",
        (
            "Orthographic globe with active weather systems, risk zones, and affected ports. "
            "Circle size indicates severity. Rotate to explore. "
            "T=Typhoon, H=Hurricane, S=Storm, M=Monsoon, F=Fog, I=Ice."
        ),
    )
    _render_weather_globe()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # ORIGINAL Section 2 — Seasonal Risk Calendar
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Seasonal Risk Calendar (12-Month x Route Heatmap)",
        (
            "Risk score for each major route by calendar month. "
            "Current month is highlighted with a white border. "
            "Letter annotations show the dominant risk type per cell. "
            "Darker red = higher combined weather risk."
        ),
    )
    _render_seasonal_calendar()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # ORIGINAL Section 3 — Current Weather Alerts
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Current Weather Alerts",
        (
            "Events where the current month falls within the seasonal window and risk level "
            "is ACTIVE, ELEVATED, or SEASONAL. Sorted by severity."
        ),
    )
    _render_current_alerts()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # ORIGINAL Section 4 — Route Weather Profile
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Route Weather Profile",
        "Select a route for a full annual weather risk breakdown and weather-adjusted ETA.",
    )

    # Build selectbox options
    route_options = {
        ROUTE_DISPLAY_NAMES.get(rid, rid): rid for rid in ALL_ROUTE_IDS
    }
    selected_display = st.selectbox(
        "Select Route",
        options=list(route_options.keys()),
        index=0,
        key="weather_route_selector",
        label_visibility="collapsed",
    )
    selected_route_id = route_options[selected_display]
    _render_route_profile(selected_route_id)

    # CSV export for all-route weather risk summary
    import io as _wio, csv as _wcsv

    def _route_risk_csv() -> bytes:
        buf = _wio.StringIO()
        w = _wcsv.writer(buf)
        w.writerow([
            "Route ID", "Display Name", "Risk Score (%)",
            "Ann. Delay Days", "Hist. Disruption Freq (%)",
            "Expected ETA (days)", "Worst-Case ETA (days)",
        ])
        for rid in ALL_ROUTE_IDS:
            try:
                ridx = compute_route_weather_risk(rid)
                nom = get_nominal_transit_days(rid)
                exp_d, wst_d = compute_weather_adjusted_eta(rid, float(nom))
                w.writerow([
                    rid,
                    ROUTE_DISPLAY_NAMES.get(rid, rid),
                    int(ridx.current_risk_score * 100),
                    round(ridx.annualized_delay_days, 1),
                    ridx.historical_disruption_frequency_pct,
                    exp_d,
                    wst_d,
                ])
            except Exception:
                pass
        return buf.getvalue().encode()

    st.download_button(
        label="Download all-route weather risk CSV",
        data=_route_risk_csv(),
        file_name="weather_route_risk_summary.csv",
        mime="text/csv",
        key="dl_route_risk_csv",
    )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # ORIGINAL Section 5 — El Nino / La Nina Monitor
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "El Nino / La Nina ENSO Monitor",
        (
            "Current ENSO phase and its implications for Panama Canal water levels, "
            "trans-Pacific weather patterns, and freight rate adjustments."
        ),
    )
    _render_enso_monitor()
