"""
Weather Risk & Routing Intelligence Tab

Sections:
  1. Weather Risk Dashboard    — KPI cards: events, delays, typhoon season, N-Atlantic
  2. Active Weather Events     — live disruption table
  3. Route Weather Risk Map    — Plotly scatter_geo shipping lanes colored by risk
  4. 14-Day Forecast by Route  — forecast table with conditions per day band
  5. Historical Weather Delays — avg delay by month by route (seasonal pattern chart)
  6. Port Weather Closures     — current / forecast closure table
  7. Optimal Routing Recs      — deviation recommendations per major route
  8. Seasonal Ice Route        — Northern Sea Route Arctic panel
"""
from __future__ import annotations

import random
from typing import Optional

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

_ACTIVE_EVENTS = [
    {"event": "Typhoon MAWAR-3", "type": "Typhoon",      "location": "South China Sea (16°N 118°E)", "affected_routes": "Intra-Asia, Asia-NA West Coast", "vessels_at_risk": 34, "delay_risk": "SEVERE",   "duration": "72–96 h"},
    {"event": "Pacific Low L-07",  "type": "Storm",       "location": "North Pacific (42°N 165°W)",   "affected_routes": "Transpacific",                   "vessels_at_risk": 21, "delay_risk": "MODERATE", "duration": "48 h"},
    {"event": "BOB Cyclone 02B",   "type": "Monsoon",     "location": "Bay of Bengal (13°N 87°E)",    "affected_routes": "Asia-Europe (Suez)",              "vessels_at_risk": 15, "delay_risk": "ELEVATED", "duration": "36 h"},
    {"event": "NW Europe Storm",   "type": "Storm surge", "location": "North Sea (56°N 3°E)",         "affected_routes": "North Atlantic, Intra-Europe",    "vessels_at_risk": 9,  "delay_risk": "MODERATE", "duration": "24 h"},
    {"event": "LA/LB Fog Bank",    "type": "Fog",         "location": "Los Angeles / Long Beach",      "affected_routes": "Transpacific (US arrival)",       "vessels_at_risk": 6,  "delay_risk": "LOW",      "duration": "12 h"},
    {"event": "Baltic Ice Edge",   "type": "Ice",         "location": "Gulf of Finland (60°N 27°E)",  "affected_routes": "Baltic / Intra-Europe",           "vessels_at_risk": 4,  "delay_risk": "LOW",      "duration": "Ongoing"},
]

_DELAY_COLOR = {"SEVERE": C_LOW, "ELEVATED": C_MOD, "MODERATE": C_MOD, "LOW": C_HIGH}

_FORECAST_TABLE = [
    {"route": "Transpacific (Asia → USWC)",    "d1": "MODERATE", "d3": "ROUGH",    "d7": "MODERATE", "d14": "CALM",     "overall": "MODERATE", "action": "Monitor L-07 track"},
    {"route": "Asia-Europe (Red Sea / Suez)",  "d1": "ROUGH",    "d3": "MODERATE", "d7": "CALM",     "d14": "CALM",     "overall": "ELEVATED", "action": "Delay departure 24 h"},
    {"route": "Asia-Europe (Cape of Good Hope)","d1": "CALM",    "d3": "CALM",     "d7": "MODERATE", "d14": "MODERATE", "overall": "NORMAL",   "action": "Proceed standard routing"},
    {"route": "North Atlantic (Europe → US)",  "d1": "ROUGH",    "d3": "SEVERE",   "d7": "MODERATE", "d14": "CALM",     "overall": "HIGH",     "action": "Northern deviation +180 nm"},
    {"route": "Intra-Asia (China → SE Asia)",  "d1": "SEVERE",   "d3": "ROUGH",    "d7": "CALM",     "d14": "CALM",     "overall": "HIGH",     "action": "Hold port 48 h or reroute"},
    {"route": "Australia → Asia",              "d1": "CALM",     "d3": "CALM",     "d7": "CALM",     "d14": "MODERATE", "overall": "LOW",      "action": "No action required"},
]

_FCST_COLOR = {"SEVERE": C_LOW, "ROUGH": C_MOD, "MODERATE": "#f59e0b88", "CALM": C_HIGH, "ELEVATED": C_MOD, "HIGH": C_LOW, "NORMAL": C_HIGH, "LOW": C_HIGH}

_PORT_CLOSURES = [
    {"port": "Kaohsiung (Taiwan)",    "current": "RESTRICTED — typhoon alert",  "d3": "OPEN",       "vessels_delayed": 12, "reopening": "~36 h"},
    {"port": "Hong Kong",             "current": "PARTIAL — reduced throughput","d3": "NORMAL",     "vessels_delayed": 7,  "reopening": "~18 h"},
    {"port": "Chennai (India)",       "current": "RESTRICTED — cyclone watch",  "d3": "RESTRICTED", "vessels_delayed": 5,  "reopening": "~60 h"},
    {"port": "Hamburg",               "current": "NORMAL",                      "d3": "RESTRICTED", "vessels_delayed": 0,  "reopening": "D+3 storm"},
    {"port": "Los Angeles / Long Beach","current":"SLOW — dense fog",           "d3": "NORMAL",     "vessels_delayed": 8,  "reopening": "~12 h"},
    {"port": "Helsinki",              "current": "ICE ESCORT required",         "d3": "ICE ESCORT", "vessels_delayed": 3,  "reopening": "Mar 28"},
]

_PORT_STATUS_COLOR = {
    "NORMAL": C_HIGH, "OPEN": C_HIGH, "RESTRICTED": C_MOD,
    "SLOW": C_MOD, "ICE ESCORT": C_ACCENT,
}

def _port_color(status: str) -> str:
    for k, v in _PORT_STATUS_COLOR.items():
        if k in status.upper():
            return v
    return C_LOW

_ROUTING_RECS = [
    {"route": "Transpacific",          "standard": "Great Circle via 40°N",          "deviation": "Southerly detour to 35°N avoiding L-07",    "extra_nm": 210,  "extra_fuel": "$18 400", "delay_avoided": 22},
    {"route": "Asia-Europe (Suez)",    "standard": "Via Malacca → Indian Ocean",     "deviation": "Delay 24 h, hug Indian coast past BOB",     "extra_nm": 0,    "extra_fuel": "$0",      "delay_avoided": 36},
    {"route": "North Atlantic WB",     "standard": "Rhumb line 50°N",                "deviation": "Northern HiLat routing 54°N avoiding storm","extra_nm": 180,  "extra_fuel": "$14 200", "delay_avoided": 31},
    {"route": "Intra-Asia (SC Sea)",   "standard": "Direct Manila → Singapore",      "deviation": "Hold Kaohsiung port 48 h for typhoon to pass","extra_nm": 0,  "extra_fuel": "$0",      "delay_avoided": 54},
    {"route": "Australia → NE Asia",   "standard": "Via Coral Sea / Philippine Sea", "deviation": "Standard — no active deviations recommended","extra_nm": 0,   "extra_fuel": "$0",      "delay_avoided": 0},
]

_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# Historical avg delay (hours) by route × month
_HIST_DELAY = {
    "Transpacific":      [8, 10, 7,  5, 4,  5,  9,  18, 22, 20, 14, 11],
    "Asia-Europe (Suez)":[6, 8,  12, 14,16, 8,  5,  6,  7,  9,  7,  6 ],
    "North Atlantic":    [20,22, 18, 12, 8,  5,  4,  5,  8, 14, 19, 21],
    "Intra-Asia":        [5, 5,  6,  9, 14, 16, 18, 22, 20, 12, 6,  5 ],
    "Mediterranean":     [8, 9,  7,  5, 4,  3,  3,  4,  5,  7,  9,  10],
}

_ROUTE_LINES = [
    # (name, lats, lons, risk)
    ("Transpacific",       [31, 35, 40, 38, 34],   [121, 140, 165, -170, -118], "elevated"),
    ("Asia-Europe (Suez)", [22, 12, 12,  4, 12,  30, 32, 37, 51, 52], [114, 80,  65, 44, 43,  32, 32, 15,  4,  4], "normal"),
    ("Asia-Europe (Cape)", [22, -5, -20,-34,-28, -10, 4, 51], [114, 80,  72, 18, 15,  15, 3,  3], "normal"),
    ("North Atlantic",     [52, 50, 48, 45, 41, 40], [3,  -5, -20, -35, -55, -74], "elevated"),
    ("Mediterranean",      [37, 36, 37, 36, 37],     [15,  20,  25,  28, 35],     "normal"),
    ("Intra-Asia SC Sea",  [22, 16, 10,  5,  1],     [114, 118, 115, 110, 104],   "severe"),
]

_RISK_LINE_COLOR = {"severe": C_LOW, "elevated": C_MOD, "normal": C_HIGH}

_STORM_MARKERS = [
    {"name": "Typhoon MAWAR-3", "lat": 16.0, "lon": 118.0, "symbol": "T", "color": C_LOW},
    {"name": "Pacific Low L-07", "lat": 42.0, "lon": -165.0, "symbol": "L", "color": C_MOD},
    {"name": "BOB Cyclone 02B",  "lat": 13.0, "lon":  87.0, "symbol": "C", "color": C_MOD},
    {"name": "NW Europe Storm",  "lat": 56.0, "lon":   3.0, "symbol": "S", "color": C_MOD},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cond_badge(cond: str) -> str:
    color = _FCST_COLOR.get(cond.upper(), C_TEXT2)
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}66;'
        f'border-radius:4px;padding:2px 7px;font-size:12px;font-weight:700;">{cond}</span>'
    )

def _kpi_card(label: str, value: str, sub: str, color: str) -> str:
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:18px 20px;text-align:center;">'
        f'<div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">{label}</div>'
        f'<div style="color:{color};font-size:28px;font-weight:800;line-height:1;">{value}</div>'
        f'<div style="color:{C_TEXT2};font-size:12px;margin-top:6px;">{sub}</div>'
        f'</div>'
    )

def _section_header(title: str, sub: str = "") -> None:
    sub_html = f'<div style="color:{C_TEXT3};font-size:13px;margin-top:2px;">{sub}</div>' if sub else ""
    st.markdown(
        f'<div style="margin:28px 0 12px 0;">'
        f'<span style="color:{C_TEXT};font-size:18px;font-weight:700;">{title}</span>'
        f'{sub_html}</div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_kpis() -> None:
    try:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(_kpi_card("High-Risk Weather Events", "6", "routes currently affected", C_LOW), unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi_card("Avg Vessel Delay (30d)", "14.2 h", "weather-attributed, all routes", C_MOD), unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi_card("Typhoon Season", "ACTIVE", "Western Pacific — Jun–Nov", C_LOW), unsafe_allow_html=True)
        with c4:
            st.markdown(_kpi_card("N. Atlantic Storm Activity", "ELEVATED", "3 systems tracked, above seasonal avg", C_MOD), unsafe_allow_html=True)
    except Exception:
        logger.exception("KPI render failed")
        st.warning("KPI cards unavailable.")


def _render_active_events() -> None:
    try:
        header_html = (
            '<div style="display:grid;grid-template-columns:1.6fr 1fr 2fr 2fr 1fr 1fr 1fr;'
            f'gap:8px;padding:8px 12px;background:{C_SURFACE};border-radius:8px 8px 0 0;'
            f'border:1px solid {C_BORDER};margin-bottom:1px;">'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Event</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Type</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Location</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Affected Routes</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:right;">Vessels</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Delay Risk</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Duration</span>'
            '</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)

        for i, ev in enumerate(_ACTIVE_EVENTS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            dc = _DELAY_COLOR.get(ev["delay_risk"], C_TEXT2)
            row_html = (
                f'<div style="display:grid;grid-template-columns:1.6fr 1fr 2fr 2fr 1fr 1fr 1fr;'
                f'gap:8px;padding:10px 12px;background:{bg};border:1px solid {C_BORDER};'
                f'border-top:none;{"border-radius:0 0 8px 8px;" if i==len(_ACTIVE_EVENTS)-1 else ""}">'
                f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">{ev["event"]}</span>'
                f'<span style="color:{C_TEXT2};font-size:13px;">{ev["type"]}</span>'
                f'<span style="color:{C_TEXT2};font-size:12px;">{ev["location"]}</span>'
                f'<span style="color:{C_TEXT2};font-size:12px;">{ev["affected_routes"]}</span>'
                f'<span style="color:{C_TEXT};font-size:13px;font-weight:700;text-align:right;">{ev["vessels_at_risk"]}</span>'
                f'<span style="color:{dc};font-size:12px;font-weight:700;">{ev["delay_risk"]}</span>'
                f'<span style="color:{C_TEXT3};font-size:12px;">{ev["duration"]}</span>'
                '</div>'
            )
            st.markdown(row_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Active events table failed")
        st.warning("Active weather events unavailable.")


def _render_risk_map() -> None:
    try:
        fig = go.Figure()

        for name, lats, lons, risk in _ROUTE_LINES:
            color = _RISK_LINE_COLOR.get(risk, C_TEXT2)
            fig.add_trace(go.Scattergeo(
                lat=lats, lon=lons,
                mode="lines",
                line={"width": 2.5, "color": color},
                name=name,
                hovertemplate=f"<b>{name}</b><br>Risk: {risk.upper()}<extra></extra>",
                legendgroup=risk,
            ))

        for sm in _STORM_MARKERS:
            fig.add_trace(go.Scattergeo(
                lat=[sm["lat"]], lon=[sm["lon"]],
                mode="markers+text",
                marker={"size": 18, "color": sm["color"], "symbol": "circle", "opacity": 0.85,
                        "line": {"width": 2, "color": "#fff"}},
                text=[sm["symbol"]],
                textfont={"size": 11, "color": "#fff", "family": "monospace"},
                textposition="middle center",
                name=sm["name"],
                hovertemplate=f"<b>{sm['name']}</b><extra></extra>",
            ))

        fig.update_layout(
            geo={
                "showland": True, "landcolor": "#1e293b",
                "showocean": True, "oceancolor": "#0f172a",
                "showcoastlines": True, "coastlinecolor": "#334155",
                "showframe": False,
                "projection_type": "natural earth",
                "bgcolor": C_BG,
            },
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            height=420,
            legend={"font": {"color": C_TEXT2}, "bgcolor": C_CARD, "bordercolor": C_BORDER, "borderwidth": 1},
            showlegend=True,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        legend_html = (
            '<div style="display:flex;gap:20px;padding:8px 0;">'
            f'<span style="color:{C_LOW};font-weight:700;">&#9644; Severe risk</span>'
            f'<span style="color:{C_MOD};font-weight:700;">&#9644; Elevated risk</span>'
            f'<span style="color:{C_HIGH};font-weight:700;">&#9644; Normal</span>'
            f'<span style="color:{C_TEXT3};font-size:12px;margin-left:12px;">Markers: T=Typhoon  L=Low pressure  C=Cyclone  S=Storm</span>'
            '</div>'
        )
        st.markdown(legend_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Risk map render failed")
        st.warning("Route weather risk map unavailable.")


def _render_forecast_table() -> None:
    try:
        cols_def = "2.2fr 1fr 1fr 1fr 1fr 1.2fr 2fr"
        header_html = (
            f'<div style="display:grid;grid-template-columns:{cols_def};gap:6px;'
            f'padding:8px 12px;background:{C_SURFACE};border-radius:8px 8px 0 0;border:1px solid {C_BORDER};margin-bottom:1px;">'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Route</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:center;">D+1</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:center;">D+3</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:center;">D+7</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:center;">D+14</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:center;">Overall</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Recommended Action</span>'
            '</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)

        for i, row in enumerate(_FORECAST_TABLE):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            oc = _FCST_COLOR.get(row["overall"].upper(), C_TEXT2)
            row_html = (
                f'<div style="display:grid;grid-template-columns:{cols_def};gap:6px;'
                f'padding:10px 12px;background:{bg};border:1px solid {C_BORDER};border-top:none;'
                f'{"border-radius:0 0 8px 8px;" if i==len(_FORECAST_TABLE)-1 else ""}">'
                f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">{row["route"]}</span>'
                f'<div style="text-align:center;">{_cond_badge(row["d1"])}</div>'
                f'<div style="text-align:center;">{_cond_badge(row["d3"])}</div>'
                f'<div style="text-align:center;">{_cond_badge(row["d7"])}</div>'
                f'<div style="text-align:center;">{_cond_badge(row["d14"])}</div>'
                f'<div style="text-align:center;"><span style="color:{oc};font-weight:700;font-size:13px;">{row["overall"]}</span></div>'
                f'<span style="color:{C_TEXT2};font-size:12px;">{row["action"]}</span>'
                '</div>'
            )
            st.markdown(row_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Forecast table failed")
        st.warning("14-day forecast table unavailable.")


def _render_historical_delays() -> None:
    try:
        fig = go.Figure()
        colors = [C_ACCENT, C_MOD, C_LOW, "#8b5cf6", "#06b6d4"]
        for idx, (route, delays) in enumerate(_HIST_DELAY.items()):
            fig.add_trace(go.Bar(
                x=_MONTHS,
                y=delays,
                name=route,
                marker_color=colors[idx % len(colors)],
                opacity=0.85,
                hovertemplate=f"<b>{route}</b><br>%{{x}}: %{{y}} h avg delay<extra></extra>",
            ))

        fig.add_annotation(
            x="Sep", y=24, text="Typhoon season peak (Aug–Oct)", showarrow=True,
            arrowhead=2, arrowcolor=C_LOW, font={"color": C_LOW, "size": 11},
            ax=40, ay=-30,
        )
        fig.add_annotation(
            x="Jan", y=22, text="N. Atlantic winter storms", showarrow=True,
            arrowhead=2, arrowcolor=C_MOD, font={"color": C_MOD, "size": 11},
            ax=50, ay=-30,
        )

        fig.update_layout(
            barmode="group",
            paper_bgcolor=C_BG, plot_bgcolor=C_SURFACE,
            font={"color": C_TEXT2, "size": 12},
            xaxis={"gridcolor": C_BORDER, "linecolor": C_BORDER},
            yaxis={"gridcolor": C_BORDER, "linecolor": C_BORDER, "title": "Avg Delay (hours)"},
            legend={"bgcolor": C_CARD, "bordercolor": C_BORDER, "borderwidth": 1, "font": {"color": C_TEXT2}},
            margin={"l": 50, "r": 20, "t": 20, "b": 40},
            height=360,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        note_html = (
            f'<div style="color:{C_TEXT3};font-size:12px;padding:4px 0;">'
            'Seasonal patterns: Aug–Oct typhoon peak (Pacific) | Nov–Mar N. Atlantic storms | '
            'Apr–Jun Bay of Bengal cyclone risk | Year-round fog delays at LA, Rotterdam'
            '</div>'
        )
        st.markdown(note_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Historical delays chart failed")
        st.warning("Historical delay chart unavailable.")


def _render_port_closures() -> None:
    try:
        cols_def = "1.8fr 2.2fr 1.4fr 1fr 1.4fr"
        header_html = (
            f'<div style="display:grid;grid-template-columns:{cols_def};gap:8px;'
            f'padding:8px 12px;background:{C_SURFACE};border-radius:8px 8px 0 0;border:1px solid {C_BORDER};margin-bottom:1px;">'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Port</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Current Status</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Forecast D+3</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:right;">Vessels Delayed</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Est. Reopening</span>'
            '</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)

        for i, port in enumerate(_PORT_CLOSURES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            cc = _port_color(port["current"])
            fc = _port_color(port["d3"])
            vd_color = C_LOW if port["vessels_delayed"] > 8 else (C_MOD if port["vessels_delayed"] > 3 else C_HIGH)
            row_html = (
                f'<div style="display:grid;grid-template-columns:{cols_def};gap:8px;'
                f'padding:10px 12px;background:{bg};border:1px solid {C_BORDER};border-top:none;'
                f'{"border-radius:0 0 8px 8px;" if i==len(_PORT_CLOSURES)-1 else ""}">'
                f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">{port["port"]}</span>'
                f'<span style="color:{cc};font-size:12px;font-weight:600;">{port["current"]}</span>'
                f'<span style="color:{fc};font-size:12px;">{port["d3"]}</span>'
                f'<span style="color:{vd_color};font-size:13px;font-weight:700;text-align:right;">{port["vessels_delayed"]}</span>'
                f'<span style="color:{C_TEXT3};font-size:12px;">{port["reopening"]}</span>'
                '</div>'
            )
            st.markdown(row_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Port closures table failed")
        st.warning("Port weather closures unavailable.")


def _render_routing_recs() -> None:
    try:
        cols_def = "1.4fr 2fr 2.2fr 0.9fr 1fr 1.1fr"
        header_html = (
            f'<div style="display:grid;grid-template-columns:{cols_def};gap:6px;'
            f'padding:8px 12px;background:{C_SURFACE};border-radius:8px 8px 0 0;border:1px solid {C_BORDER};margin-bottom:1px;">'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Route</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Standard Path</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;">Recommended Deviation</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:right;">+Distance (nm)</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:right;">Extra Fuel</span>'
            f'<span style="color:{C_TEXT3};font-size:11px;font-weight:700;text-transform:uppercase;text-align:right;">Delay Avoided (h)</span>'
            '</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)

        for i, rec in enumerate(_ROUTING_RECS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            da_color = C_HIGH if rec["delay_avoided"] > 0 else C_TEXT3
            nm_color = C_MOD if rec["extra_nm"] > 0 else C_TEXT3
            row_html = (
                f'<div style="display:grid;grid-template-columns:{cols_def};gap:6px;'
                f'padding:10px 12px;background:{bg};border:1px solid {C_BORDER};border-top:none;'
                f'{"border-radius:0 0 8px 8px;" if i==len(_ROUTING_RECS)-1 else ""}">'
                f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">{rec["route"]}</span>'
                f'<span style="color:{C_TEXT2};font-size:12px;">{rec["standard"]}</span>'
                f'<span style="color:{C_TEXT};font-size:12px;">{rec["deviation"]}</span>'
                f'<span style="color:{nm_color};font-size:13px;font-weight:700;text-align:right;">{rec["extra_nm"] if rec["extra_nm"] else "—"}</span>'
                f'<span style="color:{nm_color};font-size:13px;text-align:right;">{rec["extra_fuel"]}</span>'
                f'<span style="color:{da_color};font-size:13px;font-weight:700;text-align:right;">{rec["delay_avoided"] if rec["delay_avoided"] else "—"}</span>'
                '</div>'
            )
            st.markdown(row_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Routing recs table failed")
        st.warning("Optimal routing recommendations unavailable.")


def _render_ice_route() -> None:
    try:
        c_info, c_stats = st.columns([1.4, 1])

        with c_info:
            ice_html = (
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;padding:20px 22px;">'
                f'<div style="color:{C_TEXT};font-size:16px;font-weight:700;margin-bottom:14px;">Northern Sea Route (NSR) — Arctic Corridor</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">'
                f'<div><div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:1px;">Current Ice Extent</div>'
                f'<div style="color:{C_ACCENT};font-size:20px;font-weight:700;">4.2M km²</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">Below 10-yr avg — passable</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:1px;">Season Passability</div>'
                f'<div style="color:{C_HIGH};font-size:20px;font-weight:700;">OPEN</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">July–October window</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:1px;">Vessels This Season</div>'
                f'<div style="color:{C_TEXT};font-size:20px;font-weight:700;">28</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">transits YTD 2026</div></div>'
                f'<div><div style="color:{C_TEXT3};font-size:11px;text-transform:uppercase;letter-spacing:1px;">Icebreaker Required</div>'
                f'<div style="color:{C_MOD};font-size:20px;font-weight:700;">CLASS 1+</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">Rosatom escort ~$180k</div></div>'
                f'</div></div>'
            )
            st.markdown(ice_html, unsafe_allow_html=True)

        with c_stats:
            routes_comp = [
                ("Northern Sea Route", 12, C_ACCENT),
                ("Suez Canal",         28, C_MOD),
                ("Cape of Good Hope",  38, C_TEXT3),
            ]
            fig = go.Figure(go.Bar(
                x=[r[1] for r in routes_comp],
                y=[r[0] for r in routes_comp],
                orientation="h",
                marker_color=[r[2] for r in routes_comp],
                text=[f"{r[1]} days" for r in routes_comp],
                textposition="outside",
                textfont={"color": C_TEXT2},
                hovertemplate="%{y}: %{x} days transit<extra></extra>",
            ))
            fig.update_layout(
                title={"text": "Asia → Europe Transit Time (days)", "font": {"color": C_TEXT, "size": 13}},
                paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                font={"color": C_TEXT2},
                xaxis={"gridcolor": C_BORDER, "linecolor": C_BORDER, "range": [0, 45]},
                yaxis={"gridcolor": "rgba(0,0,0,0)", "linecolor": C_BORDER},
                margin={"l": 10, "r": 60, "t": 40, "b": 30},
                height=200,
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        note_html = (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:8px;padding:12px 16px;margin-top:10px;">'
            f'<span style="color:{C_TEXT3};font-size:12px;">'
            'NSR saves ~16 transit days vs Suez and ~26 vs Cape on Asia–Europe runs. '
            'Key constraints: Russian permit (Rosatom), mandatory icebreaker escort in certain sectors, '
            'limited rescue infrastructure, and narrow seasonal window. Fuel premium offset partly by shorter distance (10 800 nm vs 12 400 nm Suez).'
            '</span></div>'
        )
        st.markdown(note_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Ice route panel failed")
        st.warning("Seasonal ice route panel unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render(port_results=None, route_results=None) -> None:
    try:
        logger.info("Rendering weather risk tab")

        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_LOW}18,{C_MOD}12);'
            f'border:1px solid {C_LOW}44;border-radius:10px;padding:14px 20px;margin-bottom:18px;">'
            f'<span style="color:{C_LOW};font-size:14px;font-weight:700;">LIVE WEATHER ALERT</span>'
            f'<span style="color:{C_TEXT};font-size:13px;margin-left:12px;">'
            'Typhoon MAWAR-3 active in South China Sea — 34 vessels at risk — rerouting recommended for Intra-Asia and Asia-NA West Coast departures'
            '</span></div>',
            unsafe_allow_html=True,
        )

        # 1. KPIs
        _section_header("Weather Risk Dashboard", "Current conditions and seasonal status — updated hourly")
        _render_kpis()

        # 2. Active Events
        _section_header("Active Weather Events", "Live disruptions affecting global shipping lanes")
        _render_active_events()

        # 3. Risk Map
        _section_header("Route Weather Risk Map", "Major shipping lanes colored by current weather risk — storm markers show active systems")
        _render_risk_map()

        # 4. 14-Day Forecast
        _section_header("14-Day Weather Forecast by Route", "Conditions outlook per route — CALM / MODERATE / ROUGH / SEVERE")
        _render_forecast_table()

        # 5. Historical Delays
        _section_header("Historical Weather Delays by Month", "Average delay hours by route — reveals seasonal risk patterns")
        _render_historical_delays()

        # 6. Port Closures
        _section_header("Port Weather Closures & Restrictions", "Current berth closures and forecast restrictions at major ports")
        _render_port_closures()

        # 7. Routing Recs
        _section_header("Optimal Routing Recommendations", "Current weather-avoidance deviations with cost-benefit analysis")
        _render_routing_recs()

        # 8. Ice Route
        _section_header("Seasonal Ice Route — Northern Sea Route (Arctic)", "Current passability, transit comparison, and operational requirements")
        _render_ice_route()

    except Exception:
        logger.exception("Weather tab top-level render failed")
        st.error("Weather risk tab encountered an error. Check logs for details.")
