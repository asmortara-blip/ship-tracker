"""Weather Risk & Routing Intelligence tab.

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

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_header,
    wsj_market_table,
)

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

_DELAY_BADGE = {"SEVERE": "red", "ELEVATED": "yellow", "MODERATE": "yellow", "LOW": "green"}

_FORECAST_TABLE = [
    {"route": "Transpacific (Asia → USWC)",     "d1": "MODERATE", "d3": "ROUGH",    "d7": "MODERATE", "d14": "CALM",     "overall": "MODERATE", "action": "Monitor L-07 track"},
    {"route": "Asia-Europe (Red Sea / Suez)",   "d1": "ROUGH",    "d3": "MODERATE", "d7": "CALM",     "d14": "CALM",     "overall": "ELEVATED", "action": "Delay departure 24 h"},
    {"route": "Asia-Europe (Cape of Good Hope)","d1": "CALM",     "d3": "CALM",     "d7": "MODERATE", "d14": "MODERATE", "overall": "NORMAL",   "action": "Proceed standard routing"},
    {"route": "North Atlantic (Europe → US)",   "d1": "ROUGH",    "d3": "SEVERE",   "d7": "MODERATE", "d14": "CALM",     "overall": "HIGH",     "action": "Northern deviation +180 nm"},
    {"route": "Intra-Asia (China → SE Asia)",   "d1": "SEVERE",   "d3": "ROUGH",    "d7": "CALM",     "d14": "CALM",     "overall": "HIGH",     "action": "Hold port 48 h or reroute"},
    {"route": "Australia → Asia",               "d1": "CALM",     "d3": "CALM",     "d7": "CALM",     "d14": "MODERATE", "overall": "LOW",      "action": "No action required"},
]

_COND_BADGE = {
    "SEVERE": "red", "ROUGH": "yellow", "MODERATE": "yellow", "CALM": "green",
    "ELEVATED": "yellow", "HIGH": "red", "NORMAL": "green", "LOW": "green",
}

_PORT_CLOSURES = [
    {"port": "Kaohsiung (Taiwan)",     "current": "RESTRICTED — typhoon alert",   "d3": "OPEN",       "vessels_delayed": 12, "reopening": "~36 h"},
    {"port": "Hong Kong",              "current": "PARTIAL — reduced throughput", "d3": "NORMAL",     "vessels_delayed": 7,  "reopening": "~18 h"},
    {"port": "Chennai (India)",        "current": "RESTRICTED — cyclone watch",   "d3": "RESTRICTED", "vessels_delayed": 5,  "reopening": "~60 h"},
    {"port": "Hamburg",                "current": "NORMAL",                       "d3": "RESTRICTED", "vessels_delayed": 0,  "reopening": "D+3 storm"},
    {"port": "Los Angeles / Long Beach","current": "SLOW — dense fog",            "d3": "NORMAL",     "vessels_delayed": 8,  "reopening": "~12 h"},
    {"port": "Helsinki",               "current": "ICE ESCORT required",          "d3": "ICE ESCORT", "vessels_delayed": 3,  "reopening": "Mar 28"},
]

_PORT_STATUS_COLOR = {
    "NORMAL": C_HIGH, "OPEN": C_HIGH, "PARTIAL": C_MOD,
    "RESTRICTED": C_MOD, "SLOW": C_MOD, "ICE ESCORT": C_ACCENT,
}


def _port_color(status: str) -> str:
    up = status.upper()
    for k, v in _PORT_STATUS_COLOR.items():
        if k in up:
            return v
    return C_LOW


_ROUTING_RECS = [
    {"route": "Transpacific",          "standard": "Great Circle via 40°N",          "deviation": "Southerly detour to 35°N avoiding L-07",      "extra_nm": 210, "extra_fuel": "$18,400", "delay_avoided": 22},
    {"route": "Asia-Europe (Suez)",    "standard": "Via Malacca → Indian Ocean",     "deviation": "Delay 24 h, hug Indian coast past BOB",       "extra_nm": 0,   "extra_fuel": "$0",      "delay_avoided": 36},
    {"route": "North Atlantic WB",     "standard": "Rhumb line 50°N",                "deviation": "Northern HiLat routing 54°N avoiding storm",  "extra_nm": 180, "extra_fuel": "$14,200", "delay_avoided": 31},
    {"route": "Intra-Asia (SC Sea)",   "standard": "Direct Manila → Singapore",      "deviation": "Hold Kaohsiung port 48 h for typhoon to pass", "extra_nm": 0,  "extra_fuel": "$0",      "delay_avoided": 54},
    {"route": "Australia → NE Asia",   "standard": "Via Coral Sea / Philippine Sea", "deviation": "Standard — no active deviations recommended", "extra_nm": 0,   "extra_fuel": "$0",      "delay_avoided": 0},
]

_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

_HIST_DELAY = {
    "Transpacific":       [8, 10, 7,  5,  4,  5,  9, 18, 22, 20, 14, 11],
    "Asia-Europe (Suez)": [6,  8, 12, 14, 16, 8,  5,  6,  7,  9,  7,  6],
    "North Atlantic":     [20, 22, 18, 12,  8, 5,  4,  5,  8, 14, 19, 21],
    "Intra-Asia":         [5,  5,  6,  9, 14, 16, 18, 22, 20, 12,  6,  5],
    "Mediterranean":      [8,  9,  7,  5,  4,  3,  3,  4,  5,  7,  9, 10],
}

_ROUTE_LINES = [
    ("Transpacific",       [31, 35, 40, 38, 34],                         [121, 140, 165, -170, -118],         "elevated"),
    ("Asia-Europe (Suez)", [22, 12, 12,  4, 12,  30, 32, 37, 51, 52],    [114, 80,  65, 44, 43,  32, 32, 15,  4,  4], "normal"),
    ("Asia-Europe (Cape)", [22, -5, -20,-34,-28, -10,  4, 51],           [114, 80,  72, 18, 15,  15,  3,  3], "normal"),
    ("North Atlantic",     [52, 50, 48, 45, 41, 40],                     [3,  -5, -20, -35, -55, -74],        "elevated"),
    ("Mediterranean",      [37, 36, 37, 36, 37],                          [15,  20,  25,  28,  35],           "normal"),
    ("Intra-Asia SC Sea",  [22, 16, 10,  5,  1],                          [114, 118, 115, 110, 104],          "severe"),
]

_RISK_LINE_COLOR = {"severe": C_LOW, "elevated": C_MOD, "normal": C_HIGH}

_STORM_MARKERS = [
    {"name": "Typhoon MAWAR-3",  "lat": 16.0, "lon": 118.0,  "symbol": "T", "color": C_LOW},
    {"name": "Pacific Low L-07", "lat": 42.0, "lon": -165.0, "symbol": "L", "color": C_MOD},
    {"name": "BOB Cyclone 02B",  "lat": 13.0, "lon":  87.0,  "symbol": "C", "color": C_MOD},
    {"name": "NW Europe Storm",  "lat": 56.0, "lon":   3.0,  "symbol": "S", "color": C_MOD},
]

# ---------------------------------------------------------------------------
# Cell formatters
# ---------------------------------------------------------------------------


def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return f'<span style="font-family:var(--mono);color:{color};font-weight:{weight};">{value}</span>'


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">{value}</span>'


def _cond_cell(cond: str) -> str:
    return f'<div style="text-align:center;">{badge(cond, _COND_BADGE.get(cond.upper(), "gray"))}</div>'


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_kpis() -> None:
    try:
        metric_card_row(
            [
                {"label": "High-Risk Weather Events", "value": "6",        "accent": C_LOW, "sublabel": "routes currently affected"},
                {"label": "Avg Vessel Delay (30d)",   "value": "14.2 h",   "accent": C_MOD, "sublabel": "weather-attributed, all routes"},
                {"label": "Typhoon Season",           "value": "ACTIVE",   "accent": C_LOW, "sublabel": "Western Pacific — Jun–Nov"},
                {"label": "N. Atlantic Storm Activity","value": "ELEVATED","accent": C_MOD, "sublabel": "3 systems tracked, above seasonal avg"},
            ],
            columns=4,
        )
    except Exception:
        logger.exception("KPI render failed")
        st.warning("KPI cards unavailable.")


def _render_active_events() -> None:
    try:
        rows = []
        for ev in _ACTIVE_EVENTS:
            rows.append([
                _sans(ev["event"], weight=700),
                _sans(ev["type"], color=C_TEXT2),
                _sans(ev["location"], color=C_TEXT2),
                _sans(ev["affected_routes"], color=C_TEXT2),
                _mono(str(ev["vessels_at_risk"]), weight=700),
                badge(ev["delay_risk"], _DELAY_BADGE.get(ev["delay_risk"], "gray")),
                _sans(ev["duration"], color=C_TEXT3),
            ])
        wsj_market_table(
            ["Event", "Type", "Location", "Affected Routes", "Vessels", "Delay Risk", "Duration"],
            rows,
        )
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
        apply_dark_layout(
            fig,
            height=420,
            margin=dict(l=0, r=0, t=0, b=0),
            showlegend=True,
            geo=dict(
                bgcolor=C_BG,
                showland=True, landcolor=C_CARD,
                showocean=True, oceancolor=C_BG,
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.12)",
                showframe=False,
                projection_type="natural earth",
            ),
            legend=dict(bgcolor=C_CARD, bordercolor=C_BORDER, borderwidth=1, font=dict(color=C_TEXT2)),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        st.html(
            '<div style="display:flex;gap:20px;padding:8px 0;font-family:var(--sans);">'
            f'<span style="color:{C_LOW};font-weight:700;">▬ Severe risk</span>'
            f'<span style="color:{C_MOD};font-weight:700;">▬ Elevated risk</span>'
            f'<span style="color:{C_HIGH};font-weight:700;">▬ Normal</span>'
            f'<span style="color:{C_TEXT3};font-size:12px;margin-left:12px;">Markers: T=Typhoon  L=Low pressure  C=Cyclone  S=Storm</span>'
            '</div>'
        )
    except Exception:
        logger.exception("Risk map render failed")
        st.warning("Route weather risk map unavailable.")


def _render_forecast_table() -> None:
    try:
        rows = []
        for row in _FORECAST_TABLE:
            oc = _COND_BADGE.get(row["overall"].upper(), "gray")
            rows.append([
                _sans(row["route"], weight=700),
                _cond_cell(row["d1"]),
                _cond_cell(row["d3"]),
                _cond_cell(row["d7"]),
                _cond_cell(row["d14"]),
                badge(row["overall"], oc),
                _sans(row["action"], color=C_TEXT2),
            ])
        wsj_market_table(
            ["Route", "D+1", "D+3", "D+7", "D+14", "Overall", "Recommended Action"],
            rows,
        )
    except Exception:
        logger.exception("Forecast table failed")
        st.warning("14-day forecast table unavailable.")


def _render_historical_delays() -> None:
    try:
        fig = go.Figure()
        colors = [C_ACCENT, C_MOD, C_LOW, "#7c6eaf", "#4a90a4"]
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
        apply_dark_layout(
            fig,
            height=360,
            margin=dict(l=50, r=20, t=20, b=40),
            barmode="group",
            yaxis=dict(title="Avg Delay (hours)", gridcolor=C_BORDER, linecolor=C_BORDER),
            xaxis=dict(gridcolor=C_BORDER, linecolor=C_BORDER),
            legend=dict(bgcolor=C_CARD, bordercolor=C_BORDER, borderwidth=1, font=dict(color=C_TEXT2)),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        st.html(
            f'<div style="color:{C_TEXT3};font-size:12px;padding:4px 0;font-family:var(--sans);">'
            'Seasonal patterns: Aug–Oct typhoon peak (Pacific) | Nov–Mar N. Atlantic storms | '
            'Apr–Jun Bay of Bengal cyclone risk | Year-round fog delays at LA, Rotterdam'
            '</div>'
        )
    except Exception:
        logger.exception("Historical delays chart failed")
        st.warning("Historical delay chart unavailable.")


def _render_port_closures() -> None:
    try:
        rows = []
        for port in _PORT_CLOSURES:
            cc = _port_color(port["current"])
            fc = _port_color(port["d3"])
            vd = port["vessels_delayed"]
            vd_color = C_LOW if vd > 8 else (C_MOD if vd > 3 else C_HIGH)
            rows.append([
                _sans(port["port"], weight=700),
                _sans(port["current"], color=cc, weight=600),
                _sans(port["d3"], color=fc),
                _mono(str(vd), color=vd_color, weight=700),
                _sans(port["reopening"], color=C_TEXT3),
            ])
        wsj_market_table(
            ["Port", "Current Status", "Forecast D+3", "Vessels Delayed", "Est. Reopening"],
            rows,
        )
    except Exception:
        logger.exception("Port closures table failed")
        st.warning("Port weather closures unavailable.")


def _render_routing_recs() -> None:
    try:
        rows = []
        for rec in _ROUTING_RECS:
            da = rec["delay_avoided"]
            nm = rec["extra_nm"]
            da_color = C_HIGH if da > 0 else C_TEXT3
            nm_color = C_MOD if nm > 0 else C_TEXT3
            rows.append([
                _sans(rec["route"], weight=700),
                _sans(rec["standard"], color=C_TEXT2),
                _sans(rec["deviation"]),
                _mono(str(nm) if nm else "—", color=nm_color, weight=700),
                _mono(rec["extra_fuel"], color=nm_color),
                _mono(str(da) if da else "—", color=da_color, weight=700),
            ])
        wsj_market_table(
            ["Route", "Standard Path", "Recommended Deviation", "+Distance (nm)", "Extra Fuel", "Delay Avoided (h)"],
            rows,
        )
    except Exception:
        logger.exception("Routing recs table failed")
        st.warning("Optimal routing recommendations unavailable.")


def _render_ice_route() -> None:
    try:
        c_info, c_stats = st.columns([1.4, 1])

        with c_info:
            st.html(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:6px;padding:20px 22px;font-family:var(--sans);">'
                f'<div style="color:{C_TEXT};font-family:var(--serif);font-size:16px;font-weight:700;margin-bottom:14px;">Northern Sea Route (NSR) — Arctic Corridor</div>'
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
            apply_dark_layout(
                fig,
                title="Asia → Europe Transit Time (days)",
                height=200,
                margin=dict(l=10, r=60, t=40, b=30),
                showlegend=False,
                xaxis=dict(gridcolor=C_BORDER, linecolor=C_BORDER, range=[0, 45]),
                yaxis=dict(gridcolor="rgba(0,0,0,0)", linecolor=C_BORDER),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        st.html(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:8px;padding:12px 16px;margin-top:10px;font-family:var(--sans);">'
            f'<span style="color:{C_TEXT3};font-size:12px;">'
            'NSR saves ~16 transit days vs Suez and ~26 vs Cape on Asia–Europe runs. '
            'Key constraints: Russian permit (Rosatom), mandatory icebreaker escort in certain sectors, '
            'limited rescue infrastructure, and narrow seasonal window. Fuel premium offset partly by shorter distance (10,800 nm vs 12,400 nm Suez).'
            '</span></div>'
        )
    except Exception:
        logger.exception("Ice route panel failed")
        st.warning("Seasonal ice route panel unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def render(port_results=None, route_results=None) -> None:
    try:
        logger.info("Rendering weather risk tab")

        page_header(
            title="Weather Risk & Routing Intelligence",
            subtitle="Live disruption events, forecast tables, and deviation recommendations across global shipping lanes",
            icon="⛈️",
            badge_text="Demo Data",
            badge_color=C_MOD,
        )

        st.html(
            f'<div style="background:linear-gradient(135deg,{C_LOW}18,{C_MOD}12);'
            f'border:1px solid {C_LOW}44;border-radius:6px;padding:14px 20px;margin-bottom:18px;font-family:var(--sans);">'
            f'<span style="color:{C_LOW};font-family:var(--serif);font-size:14px;font-weight:700;">LIVE WEATHER ALERT</span>'
            f'<span style="color:{C_TEXT};font-size:13px;margin-left:12px;">'
            'Typhoon MAWAR-3 active in South China Sea — 34 vessels at risk — rerouting recommended for Intra-Asia and Asia-NA West Coast departures'
            '</span></div>'
        )

        section_header("Weather Risk Dashboard", "Current conditions and seasonal status — updated hourly")
        _render_kpis()

        section_header("Active Weather Events", "Live disruptions affecting global shipping lanes")
        _render_active_events()

        section_header("Route Weather Risk Map", "Major shipping lanes colored by current weather risk — storm markers show active systems")
        _render_risk_map()

        section_header("14-Day Weather Forecast by Route", "Conditions outlook per route — CALM / MODERATE / ROUGH / SEVERE")
        _render_forecast_table()

        section_header("Historical Weather Delays by Month", "Average delay hours by route — reveals seasonal risk patterns")
        _render_historical_delays()

        section_header("Port Weather Closures & Restrictions", "Current berth closures and forecast restrictions at major ports")
        _render_port_closures()

        section_header("Optimal Routing Recommendations", "Current weather-avoidance deviations with cost-benefit analysis")
        _render_routing_recs()

        section_header("Seasonal Ice Route — Northern Sea Route (Arctic)", "Current passability, transit comparison, and operational requirements")
        _render_ice_route()

    except Exception:
        logger.exception("Weather tab top-level render failed")
        st.error("Weather risk tab encountered an error. Check logs for details.")
