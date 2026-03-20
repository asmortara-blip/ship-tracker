"""
Geopolitical Risk Monitor Tab

Four sections:
  1. World Risk Map    — Plotly Scattergeo orthographic globe with chokepoints
                         and affected shipping lanes
  2. Risk Event Cards — Per-event cards with impact meters, probability,
                         affected routes, and resolution timeline
  3. Route Risk Matrix — All 17 routes ranked by geopolitical score
  4. Scenario: What if Suez Closes? — Hardcoded rerouting analysis card
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.geopolitical_monitor import (
    CURRENT_RISK_EVENTS,
    GeopoliticalEvent,
    get_chokepoint_exposure,
    compute_geopolitical_score,
    compute_expected_rate_impact,
    get_route_risk_events,
    get_all_route_scores,
    get_risk_color,
)

# ---------------------------------------------------------------------------
# Colour palette (consistent with the rest of the app)
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

# Risk-level -> colour
_LEVEL_COLOR: dict[str, str] = {
    "CRITICAL": C_DANGER,
    "HIGH":     C_ORANGE,
    "MODERATE": C_WARN,
    "LOW":      C_HIGH,
}

# ---------------------------------------------------------------------------
# Chokepoint coordinates (real-world lat/lon)
# ---------------------------------------------------------------------------

_CHOKEPOINTS: list[dict] = [
    {
        "name": "Strait of Hormuz",
        "lat": 26.6, "lon": 56.3,
        "risk": "MODERATE",
        "note": "20% global oil; elevated tension risk",
    },
    {
        "name": "Suez Canal",
        "lat": 30.0, "lon": 32.5,
        "risk": "HIGH",
        "note": "12% world trade; Red Sea crisis diverting traffic",
    },
    {
        "name": "Strait of Malacca",
        "lat": 2.5, "lon": 101.0,
        "risk": "MODERATE",
        "note": "40% world trade; South China Sea spillover risk",
    },
    {
        "name": "Panama Canal",
        "lat": 9.0, "lon": -79.5,
        "risk": "MODERATE",
        "note": "5% world trade; drought-related capacity constraints",
    },
    {
        "name": "Taiwan Strait",
        "lat": 24.0, "lon": 120.0,
        "risk": "HIGH",
        "note": "26% container trade; military exercise disruption risk",
    },
    {
        "name": "Bab-el-Mandeb (Red Sea)",
        "lat": 12.5, "lon": 43.5,
        "risk": "CRITICAL",
        "note": "Houthi attacks; major carriers fully rerouting",
    },
    {
        "name": "Danish Straits",
        "lat": 55.5, "lon": 11.0,
        "risk": "LOW",
        "note": "North Europe access; Russian shadow fleet monitoring",
    },
]

# Shipping lane waypoints (pairs of lat/lon lists for approximate great-circle lanes)
# Each lane has: name, lats, lons, risk_level (drives line colour)
_SHIPPING_LANES: list[dict] = [
    {
        "name": "Asia-Europe (Red Sea / Suez)",
        "lats": [31.2, 29.9, 20.0, 12.5, 5.0, -5.0, -25.0, -34.4, -30.0, 0.0, 20.0, 51.5],
        "lons": [121.5, 121.0, 55.0, 43.5, 43.0, 40.0, 20.0, 18.5, -5.0, -5.0, -10.0, 1.0],
        "risk": "CRITICAL",
    },
    {
        "name": "Trans-Pacific Eastbound",
        "lats": [31.2, 35.0, 40.0, 45.0, 47.0, 37.8],
        "lons": [121.5, 150.0, 170.0, -175.0, -160.0, -122.4],
        "risk": "HIGH",
    },
    {
        "name": "Transatlantic",
        "lats": [51.5, 50.0, 48.0, 44.0, 40.7],
        "lons": [1.0, -15.0, -30.0, -50.0, -74.0],
        "risk": "LOW",
    },
    {
        "name": "Middle East to Europe",
        "lats": [25.3, 26.6, 20.0, 12.5, 5.0, 0.0, 20.0, 51.5],
        "lons": [55.4, 56.3, 55.0, 43.5, 43.0, 40.0, -10.0, 1.0],
        "risk": "HIGH",
    },
    {
        "name": "Intra-Asia: China-SE Asia",
        "lats": [22.3, 15.0, 5.0, 1.3],
        "lons": [114.2, 110.0, 105.0, 103.8],
        "risk": "MODERATE",
    },
    {
        "name": "Trans-Pacific via Panama",
        "lats": [37.8, 35.0, 25.0, 15.0, 9.0, 8.0, 10.0, 25.0, 40.7],
        "lons": [-122.4, -130.0, -120.0, -100.0, -79.5, -79.5, -75.0, -70.0, -74.0],
        "risk": "MODERATE",
    },
]


# ---------------------------------------------------------------------------
# Shared HTML helpers
# ---------------------------------------------------------------------------

def _card_wrap(content_html: str, border_color: str = C_BORDER) -> str:
    return (
        "<div style=\"background:"
        + C_CARD
        + "; border:1px solid "
        + border_color
        + "; border-radius:12px; padding:18px 20px; margin-bottom:12px\">"
        + content_html
        + "</div>"
    )


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


def _risk_badge(risk_level: str, pulse: bool = False) -> str:
    color = _LEVEL_COLOR.get(risk_level, C_TEXT2)
    pulse_style = (
        " animation:geo-pulse 1.4s ease-in-out infinite;"
        if pulse
        else ""
    )
    return (
        "<span style=\"background:rgba(0,0,0,0.35); color:"
        + color
        + "; border:1px solid "
        + color
        + "; padding:2px 10px; border-radius:999px;"
        " font-size:0.70rem; font-weight:700; white-space:nowrap;"
        + pulse_style
        + "\">"
        + risk_level
        + "</span>"
    )


def _pill(text: str, color: str = C_ACCENT) -> str:
    return (
        "<span style=\"display:inline-block; background:rgba(59,130,246,0.12);"
        " color:" + color + "; border:1px solid rgba(59,130,246,0.30);"
        " padding:1px 9px; border-radius:999px; font-size:0.68rem;"
        " font-weight:600; margin:2px 3px 2px 0; white-space:nowrap\">"
        + text
        + "</span>"
    )


def _bar(pct: float, color: str, label: str) -> str:
    """Render a labelled percentage bar (0-100 scale input)."""
    abs_pct = abs(pct)
    clamped = min(100, abs_pct)
    sign = "+" if pct >= 0 else ""
    return (
        "<div style=\"margin-bottom:6px\">"
        "<div style=\"display:flex; justify-content:space-between;"
        " margin-bottom:3px\">"
        "<span style=\"font-size:0.72rem; color:" + C_TEXT2 + "\">" + label + "</span>"
        "<span style=\"font-size:0.72rem; font-weight:700; color:" + color + "\">"
        + sign + "{:.1f}%".format(pct)
        + "</span>"
        "</div>"
        "<div style=\"background:rgba(255,255,255,0.07); border-radius:4px; height:7px\">"
        "<div style=\"width:" + "{:.1f}".format(clamped) + "%; background:" + color
        + "; border-radius:4px; height:7px\"></div>"
        "</div>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Section 1: World Risk Map
# ---------------------------------------------------------------------------

def _render_world_map() -> None:
    """Orthographic dark globe with chokepoint markers and shipping lanes."""
    logger.debug("Rendering geopolitical world risk map")

    fig = go.Figure()

    # ── Shipping lane traces ─────────────────────────────────────────────
    for lane in _SHIPPING_LANES:
        lane_color = _LEVEL_COLOR.get(lane["risk"], C_TEXT2)
        opacity = 0.55 if lane["risk"] in ("CRITICAL", "HIGH") else 0.30
        fig.add_trace(go.Scattergeo(
            lat=lane["lats"],
            lon=lane["lons"],
            mode="lines",
            line=dict(color=lane_color, width=2 if lane["risk"] in ("CRITICAL", "HIGH") else 1),
            opacity=opacity,
            hoverinfo="text",
            hovertext=lane["name"] + " — Risk: " + lane["risk"],
            showlegend=False,
            name=lane["name"],
        ))

    # ── Chokepoint glow layer (larger semi-transparent ring) ─────────────
    lats_cp = [c["lat"] for c in _CHOKEPOINTS]
    lons_cp = [c["lon"] for c in _CHOKEPOINTS]
    colors_cp = [_LEVEL_COLOR.get(c["risk"], C_TEXT2) for c in _CHOKEPOINTS]
    sizes_main = [32 if c["risk"] == "CRITICAL" else 26 if c["risk"] == "HIGH" else 20
                  for c in _CHOKEPOINTS]
    hover_cp = [
        "<b>" + c["name"] + "</b><br>"
        + "Risk: " + c["risk"] + "<br>"
        + c["note"]
        for c in _CHOKEPOINTS
    ]

    fig.add_trace(go.Scattergeo(
        lat=lats_cp,
        lon=lons_cp,
        mode="markers",
        marker=dict(
            size=[s * 1.9 for s in sizes_main],
            color=colors_cp,
            opacity=0.15,
            line=dict(width=0),
        ),
        hoverinfo="skip",
        showlegend=False,
        name="chokepoint_glow",
    ))

    # ── Chokepoint main markers ──────────────────────────────────────────
    fig.add_trace(go.Scattergeo(
        lat=lats_cp,
        lon=lons_cp,
        mode="markers+text",
        marker=dict(
            size=sizes_main,
            color=colors_cp,
            opacity=0.92,
            symbol="circle",
            line=dict(color="rgba(255,255,255,0.55)", width=1.5),
        ),
        text=["  " + c["name"] for c in _CHOKEPOINTS],
        textposition="middle right",
        textfont=dict(size=9, color=C_TEXT2),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover_cp,
        showlegend=False,
        name="chokepoints",
    ))

    # ── Warning symbol overlay (exclamation "!" text) ────────────────────
    critical_lats = [c["lat"] for c in _CHOKEPOINTS if c["risk"] in ("CRITICAL", "HIGH")]
    critical_lons = [c["lon"] for c in _CHOKEPOINTS if c["risk"] in ("CRITICAL", "HIGH")]
    fig.add_trace(go.Scattergeo(
        lat=critical_lats,
        lon=critical_lons,
        mode="text",
        text=["!" for _ in critical_lats],
        textfont=dict(size=11, color="white"),
        hoverinfo="skip",
        showlegend=False,
        name="warnings",
    ))

    # ── Legend swatches ─────────────────────────────────────────────────
    for risk_label, risk_color in [
        ("CRITICAL", C_DANGER),
        ("HIGH",     C_ORANGE),
        ("MODERATE", C_WARN),
        ("LOW",      C_HIGH),
    ]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=10, color=risk_color),
            name=risk_label,
            showlegend=True,
        ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        height=500,
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="orthographic",
            showland=True,       landcolor="#1a2235",
            showocean=True,      oceancolor="#0a0f1a",
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.15)",
            showframe=False,
            bgcolor="#0a0f1a",
            showcountries=True,  countrycolor="rgba(255,255,255,0.07)",
            showlakes=False,
            projection_rotation=dict(lon=55, lat=20, roll=0),
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

    st.plotly_chart(fig, use_container_width=True, key="geo_world_risk_globe")


# ---------------------------------------------------------------------------
# Section 2: Risk Event Cards
# ---------------------------------------------------------------------------

_TIMELINE_COLOR: dict[str, str] = {
    "Days":    C_DANGER,
    "Weeks":   C_ORANGE,
    "Months":  C_WARN,
    "Ongoing": C_TEXT2,
}


def _render_event_card(ev: GeopoliticalEvent) -> str:
    """Return the full HTML for a single GeopoliticalEvent card."""
    level_color = _LEVEL_COLOR.get(ev.risk_level, C_TEXT2)
    pulse = ev.risk_level == "CRITICAL"
    border_color = level_color + "44"

    badge_html = _risk_badge(ev.risk_level, pulse=pulse)

    # Timeline badge
    tl_color = _TIMELINE_COLOR.get(ev.resolution_timeline, C_TEXT2)
    tl_badge = (
        "<span style=\"font-size:0.68rem; font-weight:600; color:"
        + tl_color
        + "; border:1px solid "
        + tl_color
        + "44; padding:1px 9px; border-radius:999px\">"
        + ev.resolution_timeline
        + "</span>"
    )

    # Rate impact bar (red)
    rate_bar = _bar(ev.rate_impact_pct, C_DANGER, "Rate Impact")
    # Volume impact bar (amber)
    volume_bar = _bar(ev.volume_impact_pct, C_WARN, "Volume Impact")

    # Probability dial (simple text dial)
    prob_pct = int(ev.probability * 100)
    prob_color = (
        C_DANGER if ev.probability >= 0.75
        else C_ORANGE if ev.probability >= 0.50
        else C_WARN if ev.probability >= 0.30
        else C_HIGH
    )
    prob_html = (
        "<div style=\"text-align:center; margin-bottom:4px\">"
        "<div style=\"font-size:1.5rem; font-weight:800; color:" + prob_color + "\">"
        + str(prob_pct) + "%</div>"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.06em\">Probability</div>"
        "</div>"
    )

    # Expected value highlight
    ev_sign = "+" if ev.expected_value_impact >= 0 else ""
    ev_html = (
        "<div style=\"background:rgba(239,68,68,0.10); border:1px solid rgba(239,68,68,0.25);"
        " border-radius:8px; padding:8px 12px; margin-top:6px; text-align:center\">"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
        " letter-spacing:0.07em; margin-bottom:3px\">Expected Rate Impact</div>"
        "<div style=\"font-size:1.1rem; font-weight:800; color:" + C_DANGER + "\">"
        + ev_sign + "{:.1f}%".format(ev.expected_value_impact)
        + "</div>"
        "<div style=\"font-size:0.65rem; color:" + C_TEXT3 + "; margin-top:2px\">"
        "rate \xd7 probability"
        "</div>"
        "</div>"
    )

    # Affected route pills
    route_pills = "".join(_pill(rid.replace("_", " ").title()) for rid in ev.affected_routes)
    cp_pills = (
        "".join(_pill(cp, color=C_ORANGE) for cp in ev.affected_chokepoints)
        if ev.affected_chokepoints
        else "<span style=\"color:" + C_TEXT3 + "; font-size:0.72rem\">None</span>"
    )

    html = (
        "<div style=\"background:" + C_CARD + "; border:1px solid " + border_color + ";"
        " border-radius:12px; padding:16px 18px; margin-bottom:10px\">"
        # Header row
        "<div style=\"display:flex; justify-content:space-between; align-items:flex-start;"
        " margin-bottom:10px; flex-wrap:wrap; gap:6px\">"
        "<div>"
        + badge_html
        + "<div style=\"font-size:0.95rem; font-weight:700; color:" + C_TEXT + ";"
        " margin-top:7px\">" + ev.title + "</div>"
        "</div>"
        + tl_badge
        + "</div>"
        # Description
        "<div style=\"font-size:0.80rem; color:" + C_TEXT2 + "; line-height:1.5;"
        " margin-bottom:12px\">" + ev.description + "</div>"
        # Metrics row
        "<div style=\"display:grid; grid-template-columns:2fr 1fr; gap:14px;"
        " margin-bottom:10px\">"
        "<div>"
        + rate_bar
        + volume_bar
        + "</div>"
        "<div>"
        + prob_html
        + ev_html
        + "</div>"
        "</div>"
        # Affected routes
        "<div style=\"margin-bottom:6px\">"
        "<div style=\"font-size:0.65rem; text-transform:uppercase; letter-spacing:0.07em;"
        " color:" + C_TEXT3 + "; margin-bottom:5px\">Affected Routes</div>"
        + route_pills
        + "</div>"
        # Chokepoints
        "<div style=\"margin-top:6px\">"
        "<div style=\"font-size:0.65rem; text-transform:uppercase; letter-spacing:0.07em;"
        " color:" + C_TEXT3 + "; margin-bottom:5px\">Chokepoints</div>"
        + cp_pills
        + "</div>"
        # Footer
        "<div style=\"margin-top:10px; font-size:0.66rem; color:" + C_TEXT3 + ";"
        " text-align:right\">Updated: " + ev.last_updated + "</div>"
        "</div>"
    )
    return html


_PULSE_CSS = """
<style>
@keyframes geo-pulse {
    0%   { opacity: 1; }
    50%  { opacity: 0.45; }
    100% { opacity: 1; }
}
</style>
"""


def _render_risk_event_cards() -> None:
    """Render all geopolitical event cards in a 2-column layout."""
    logger.debug("Rendering geopolitical risk event cards")

    if not CURRENT_RISK_EVENTS:
        st.info("No active geopolitical risk events are currently loaded.")
        return

    # Inject pulse keyframe CSS once
    st.markdown(_PULSE_CSS, unsafe_allow_html=True)

    # Sort: CRITICAL first, then HIGH, MODERATE, LOW — dict mapping avoids string sort pitfalls
    order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    sorted_events = sorted(
        CURRENT_RISK_EVENTS,
        key=lambda e: (order.get(e.risk_level, 9), -e.probability),
    )

    col_a, col_b = st.columns(2)
    for i, ev in enumerate(sorted_events):
        html = _render_event_card(ev)
        if i % 2 == 0:
            with col_a:
                st.markdown(html, unsafe_allow_html=True)
        else:
            with col_b:
                st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 3: Route Risk Matrix Table
# ---------------------------------------------------------------------------

def _recommendation(score: float, rate_impact: float) -> str:
    """Return a short recommendation string based on geo score and rate impact."""
    if score >= 0.70:
        return "Avoid or hedge — high disruption probability"
    if score >= 0.50:
        return "Price in risk premium; monitor daily"
    if score >= 0.30 and rate_impact > 10.0:
        return "Rate spike possible — secure forward bookings"
    if score >= 0.30:
        return "Monitor geopolitical developments"
    return "Relatively safe — standard commercial management"


def _render_route_matrix(route_results) -> None:
    """Render the 17-route geopolitical risk table."""
    logger.debug("Rendering geopolitical route risk matrix")

    # Build opportunity lookup from route_results
    opp_by_id: dict[str, float] = {}
    if route_results:
        for r in route_results:
            rid = getattr(r, "route_id", "")
            opp = getattr(r, "opportunity_score", 0.5)
            if rid:
                opp_by_id[rid] = float(opp)

    all_scores = get_all_route_scores()

    if not all_scores:
        st.info("No route geopolitical scores are available.")
        return

    # Sort by geo score descending
    all_scores_sorted = sorted(all_scores, key=lambda x: x[2], reverse=True)

    rows_html = ""
    for rank, (rid, name, score, rate_impact, top_event) in enumerate(all_scores_sorted, 1):
        score_pct = int(score * 100)
        score_color = (
            C_DANGER if score >= 0.70
            else C_ORANGE if score >= 0.50
            else C_WARN if score >= 0.30
            else C_HIGH
        )
        row_bg = (
            "rgba(239,68,68,0.06)" if score >= 0.70
            else "rgba(245,158,11,0.05)" if score >= 0.40
            else "transparent"
        )
        sign = "+" if rate_impact >= 0 else ""
        rec = _recommendation(score, rate_impact)

        rows_html += (
            "<tr style=\"background:" + row_bg + "; border-bottom:1px solid rgba(255,255,255,0.04)\">"
            "<td style=\"color:" + C_TEXT3 + "; font-size:0.72rem; padding:8px 7px;"
            " text-align:center; font-weight:600; min-width:28px\">" + str(rank) + "</td>"
            "<td style=\"color:" + C_TEXT + "; font-size:0.80rem; padding:8px 7px;"
            " font-weight:600; white-space:nowrap\">" + name + "</td>"
            "<td style=\"padding:8px 7px; min-width:140px\">"
            "<div style=\"display:flex; align-items:center; gap:7px\">"
            "<div style=\"flex:1; background:rgba(255,255,255,0.06); border-radius:4px; height:7px\">"
            "<div style=\"width:" + str(score_pct) + "%; background:" + score_color
            + "; border-radius:4px; height:7px\"></div>"
            "</div>"
            "<span style=\"font-size:0.76rem; font-weight:700; color:" + score_color
            + "; min-width:32px\">" + str(score_pct) + "%</span>"
            "</div></td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.75rem; padding:8px 7px;"
            " max-width:180px; line-height:1.4\">" + top_event + "</td>"
            "<td style=\"color:" + C_DANGER + "; font-size:0.76rem; padding:8px 7px;"
            " font-weight:700; white-space:nowrap\">"
            + sign + "{:.1f}%".format(rate_impact)
            + "</td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.72rem; padding:8px 7px;"
            " line-height:1.4; max-width:200px\">" + rec + "</td>"
            "</tr>"
        )

    header_style = (
        "color:" + C_TEXT3 + "; font-size:0.67rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:6px 7px; text-align:left;"
        " border-bottom:1px solid rgba(255,255,255,0.10)"
    )
    table_html = (
        "<div style=\"overflow-x:auto\">"
        "<table style=\"width:100%; border-collapse:collapse\">"
        "<thead><tr>"
        "<th style=\"" + header_style + "; text-align:center\">#</th>"
        "<th style=\"" + header_style + "\">Route</th>"
        "<th style=\"" + header_style + "\">Geo Risk Score</th>"
        "<th style=\"" + header_style + "\">Top Risk Event</th>"
        "<th style=\"" + header_style + "\">Exp. Rate Impact</th>"
        "<th style=\"" + header_style + "\">Recommendation</th>"
        "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table>"
        "</div>"
    )

    st.markdown(
        "<div style=\"background:" + C_CARD + "; border:1px solid rgba(59,130,246,0.20);"
        " border-radius:12px; padding:18px 20px; margin-bottom:12px\">"
        + table_html
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4: Suez Closure Scenario Card
# ---------------------------------------------------------------------------

def _render_suez_scenario() -> None:
    """Hardcoded 'What if Suez Closes?' analysis card."""
    logger.debug("Rendering Suez closure scenario card")

    # Rerouting data for the main affected routes
    reroute_data = [
        {
            "route": "Asia-Europe (asia_europe)",
            "current_path": "Suez Canal (Port Said) + Red Sea",
            "reroute_path": "Cape of Good Hope (South Africa)",
            "extra_days": 14,
            "extra_fuel_usd": 750,
            "rate_spike_pct": (35, 50),
        },
        {
            "route": "Ningbo-Europe (ningbo_europe)",
            "current_path": "Suez Canal via Indian Ocean",
            "reroute_path": "Cape of Good Hope — add 9,000nm",
            "extra_days": 14,
            "extra_fuel_usd": 720,
            "rate_spike_pct": (35, 50),
        },
        {
            "route": "Middle East-Europe (middle_east_to_europe)",
            "current_path": "Suez Canal (northern transit)",
            "reroute_path": "Cape of Good Hope (doubles voyage length)",
            "extra_days": 20,
            "extra_fuel_usd": 1100,
            "rate_spike_pct": (45, 60),
        },
        {
            "route": "South Asia-Europe (south_asia_to_europe)",
            "current_path": "Suez Canal via Gulf of Aden",
            "reroute_path": "Cape of Good Hope via Colombo",
            "extra_days": 15,
            "extra_fuel_usd": 800,
            "rate_spike_pct": (30, 45),
        },
        {
            "route": "Med Hub-Asia (med_hub_to_asia)",
            "current_path": "Suez Canal (southbound leg)",
            "reroute_path": "Cape of Good Hope (westbound via Atlantic)",
            "extra_days": 18,
            "extra_fuel_usd": 950,
            "rate_spike_pct": (35, 50),
        },
    ]

    # Metric summary cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_DANGER + "\">12%</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "World Trade Affected</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_DANGER + "\">+14 days</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Asia-Europe Extra Transit</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(245,158,11,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_WARN + "\">+35-50%</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Freight Rate Spike Est.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(245,158,11,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_WARN + "\">+$750/FEU</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Avg. Extra Fuel Cost</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style=\"height:14px\"></div>", unsafe_allow_html=True)

    # Rerouting table
    rows_html = ""
    for d in reroute_data:
        rows_html += (
            "<tr style=\"border-bottom:1px solid rgba(255,255,255,0.04)\">"
            "<td style=\"color:" + C_TEXT + "; font-size:0.78rem; padding:9px 8px;"
            " font-weight:600\">" + d["route"] + "</td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.75rem; padding:9px 8px\">"
            + d["current_path"] + "</td>"
            "<td style=\"color:" + C_WARN + "; font-size:0.75rem; padding:9px 8px\">"
            + d["reroute_path"] + "</td>"
            "<td style=\"color:" + C_DANGER + "; font-size:0.78rem; padding:9px 8px;"
            " font-weight:700; white-space:nowrap\">+" + str(d["extra_days"]) + " days</td>"
            "<td style=\"color:" + C_WARN + "; font-size:0.78rem; padding:9px 8px;"
            " font-weight:700; white-space:nowrap\">+$"
            + str(d["extra_fuel_usd"]) + "/FEU</td>"
            "<td style=\"color:" + C_DANGER + "; font-size:0.78rem; padding:9px 8px;"
            " font-weight:700; white-space:nowrap\">+"
            + str(d["rate_spike_pct"][0]) + "-" + str(d["rate_spike_pct"][1]) + "%</td>"
            "</tr>"
        )

    h_style = (
        "color:" + C_TEXT3 + "; font-size:0.66rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:6px 8px;"
        " border-bottom:1px solid rgba(255,255,255,0.10)"
    )
    table_html = (
        "<div style=\"overflow-x:auto\">"
        "<table style=\"width:100%; border-collapse:collapse\">"
        "<thead><tr>"
        "<th style=\"" + h_style + "\">Route</th>"
        "<th style=\"" + h_style + "\">Current Path</th>"
        "<th style=\"" + h_style + "\">Reroute Path</th>"
        "<th style=\"" + h_style + "\">Extra Days</th>"
        "<th style=\"" + h_style + "\">Extra Fuel</th>"
        "<th style=\"" + h_style + "\">Rate Spike</th>"
        "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table>"
        "</div>"
    )

    context_html = (
        "<div style=\"background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.25);"
        " border-radius:10px; padding:14px 18px; margin-bottom:14px; font-size:0.82rem;"
        " color:" + C_TEXT2 + "; line-height:1.6\">"
        "<b style=\"color:" + C_TEXT + "\">Context: </b>"
        "The Suez Canal handles approximately 12-15% of global trade and ~30% of all container "
        "traffic. A full closure — whether from Houthi escalation, Egyptian political crisis, or "
        "a vessel grounding like the Ever Given in 2021 — would force all Asia-Europe, "
        "South Asia-Europe, and Middle East-Europe carriers to reroute via the Cape of Good Hope. "
        "This adds 9,000-11,000 nautical miles per voyage. Based on current bunker prices "
        "($500-600/tonne VLSFO), the extra fuel alone costs $700-1,100/FEU. Combined with "
        "vessel utilisation effects, schedule disruption, and surcharge inflation, freight rate "
        "spikes of 35-50% are realistic within 2-4 weeks of a closure event."
        "</div>"
    )

    st.markdown(context_html, unsafe_allow_html=True)
    st.markdown(
        "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.20);"
        " border-radius:12px; padding:18px 20px\">"
        + table_html
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(route_results, port_results, freight_data, macro_data) -> None:
    """Render the Geopolitical Risk Monitor tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer.
    port_results : list[PortDemandResult]
        Current port demand results.
    freight_data : dict
        Freight rate data dict (passed through).
    macro_data : dict
        Global macro indicators dict (passed through).
    """
    logger.info("Rendering Geopolitical Risk Monitor tab")

    st.header("Geopolitical Risk Monitor")
    st.caption(
        "Real-time geopolitical risk intelligence for global shipping. "
        "Risk levels (CRITICAL / HIGH / MODERATE / LOW) are assigned using a probability-weighted "
        "composite of active conflict, sanctions, chokepoint status, and historical disruption frequency. "
        "Scores are updated each app rerun against the curated event database."
    )

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — World Risk Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "World Risk Map",
        (
            "Critical chokepoints (large markers) and affected shipping lanes colour-coded "
            "by risk severity. Rotate globe to explore."
        ),
    )
    st.caption(
        "Chokepoints are scored individually based on current conflict exposure, vessel traffic "
        "concentration, and historical closure frequency. Shipping lane colours reflect the highest "
        "risk event affecting that corridor. Marker size scales with chokepoint criticality "
        "(CRITICAL = largest). Source: curated geopolitical event database in "
        "processing/geopolitical_monitor.py."
    )
    _render_world_map()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Risk Event Cards
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Current Geopolitical Risk Events (2025-2026)",
        (
            "Active risk events — sorted by severity then probability. "
            "CRITICAL events pulse. Expected rate impact = rate impact % "
            + "\xd7"
            + " probability."
        ),
    )
    st.caption(
        "Each card shows: (1) Rate Impact — estimated freight rate change if the event escalates "
        "to full disruption; (2) Volume Impact — estimated trade volume reduction; "
        "(3) Probability — analyst-assessed likelihood of material escalation within 90 days; "
        "(4) Expected Rate Impact — probability-weighted rate impact, the key figure for "
        "commercial decision-making. Resolution timeline indicates the analyst's expected duration."
    )
    _render_risk_event_cards()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Route Risk Matrix
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Route Geopolitical Risk Matrix",
        (
            "All 17 trade lanes ranked by composite geopolitical score. "
            "Score = probability-weighted mean of event risk levels affecting that route."
        ),
    )
    st.caption(
        "Composite geopolitical score = mean of (event_probability × event_severity_weight) "
        "across all active events affecting that route's chokepoints and corridors. "
        "Severity weights: CRITICAL = 1.0, HIGH = 0.75, MODERATE = 0.50, LOW = 0.25. "
        "Expected rate impact is the probability-weighted rate change estimate aggregated "
        "from all affecting events. Recommendations are rule-based thresholds applied to "
        "the composite score and rate impact."
    )
    _render_route_matrix(route_results)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Suez Closure Scenario
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Scenario Analysis: What if the Suez Canal Closes?",
        (
            "Rerouting analysis — impact on affected routes, transit times, "
            "fuel costs, and freight rate estimates under a full Suez closure event."
        ),
    )
    st.caption(
        "This scenario models a complete Suez Canal closure forcing all affected carriers onto "
        "the Cape of Good Hope routing. Fuel cost estimates use VLSFO at $500–600/tonne. "
        "Rate spike estimates are based on the 2021 Ever Given precedent and 2024 Red Sea "
        "diversion data. Extra days and fuel figures are route-specific and reflect "
        "laden voyage distance increases only (ballast legs not included)."
    )
    _render_suez_scenario()
