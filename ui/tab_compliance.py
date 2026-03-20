"""tab_compliance.py — Sanctions Compliance Monitor tab.

Seven sections:
  1. Sanctions World Map        — Plotly choropleth (red/orange/yellow/green by risk)
  2. Active Sanctions Dashboard — Card grid per SanctionsRegime, pulsing CRITICAL badges
  3. Dark Fleet Tracker         — Pie chart, shadow premium, detection stats
  4. Compliance Cost Calculator — Per-route due diligence cost and requirements
  5. Regulatory Timeline        — Upcoming 2026-2028 compliance deadlines
  6. IMO CII Rating Reference   — Color-coded A-E rating bands with thresholds
  7. Route Compliance Scoring   — All 17 routes with risk score, exposure, diligence level

Data currency note:
  IMO Carbon Intensity Indicator (CII) reduction factors and rating thresholds are reviewed
  annually at MEPC sessions. EU ETS shipping scope and EUA prices change quarterly.
  OFAC/EU sanctions designations are updated continuously. Always verify against the
  latest MEPC circulars, EUR-Lex OJ publications, and OFAC SDN list before operational use.
"""
from __future__ import annotations

import csv
import io

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.sanctions_tracker import (
    ACTIVE_SANCTIONS,
    DARK_FLEET_2025,
    SanctionsRegime,
    compute_route_compliance_risk,
    get_all_route_compliance_scores,
)

# ---------------------------------------------------------------------------
# Colour palette — matches app-wide dark theme
# ---------------------------------------------------------------------------

C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"   # green — safe/low
C_ACCENT = "#3b82f6"   # blue  — accent
C_WARN   = "#f59e0b"   # amber — moderate/restricted
C_DANGER = "#ef4444"   # red   — critical/prohibited
C_ORANGE = "#f97316"   # orange — high/restricted

# Sanctions impact -> colour
_IMPACT_COLOR: dict[str, str] = {
    "PROHIBITED": C_DANGER,
    "RESTRICTED": C_ORANGE,
    "MONITORED":  C_WARN,
}

# Risk level -> colour
_RISK_COLOR: dict[str, str] = {
    "CRITICAL": C_DANGER,
    "HIGH":     C_ORANGE,
    "MODERATE": C_WARN,
    "LOW":      C_HIGH,
}

# Authority -> colour accent
_AUTH_COLOR: dict[str, str] = {
    "OFAC": "#7c3aed",   # purple
    "EU":   "#1d4ed8",   # deep blue
    "UN":   "#0369a1",   # teal-blue
    "UK":   "#0f766e",   # teal
}

# Route human-readable names
_ROUTE_NAMES: dict[str, str] = {
    "transpacific_eb":       "Trans-Pacific Eastbound",
    "transpacific_wb":       "Trans-Pacific Westbound",
    "asia_europe":           "Asia-Europe",
    "transatlantic":         "Transatlantic",
    "sea_transpacific_eb":   "Southeast Asia Eastbound",
    "ningbo_europe":         "Asia-Europe via Suez (Ningbo)",
    "middle_east_to_europe": "Middle East Hub to Europe",
    "middle_east_to_asia":   "Middle East Hub to Asia",
    "south_asia_to_europe":  "South Asia to Europe",
    "intra_asia_china_sea":  "Intra-Asia: China to SE Asia",
    "intra_asia_china_japan":"Intra-Asia: China to Japan/Korea",
    "china_south_america":   "China to South America",
    "europe_south_america":  "Europe to South America",
    "med_hub_to_asia":       "Mediterranean Hub to Asia",
    "north_africa_to_europe":"North Africa/Med to Europe",
    "us_east_south_america": "US East Coast to South America",
    "longbeach_to_asia":     "US West Coast (Long Beach) to Asia",
}

# ---------------------------------------------------------------------------
# Choropleth data — sanctions status by ISO-3 country code
# ---------------------------------------------------------------------------

# (iso3, country_name, status, note)
_SANCTION_COUNTRIES: list[dict] = [
    # PROHIBITED — red
    {"iso": "RUS", "name": "Russia",      "status": "PROHIBITED", "note": "Oil price cap + broad OFAC/EU measures. Dark fleet ~600 vessels."},
    {"iso": "IRN", "name": "Iran",        "status": "PROHIBITED", "note": "Near-total OFAC prohibition. Secondary sanctions on all dealings."},
    {"iso": "PRK", "name": "North Korea", "status": "PROHIBITED", "note": "UN total embargo. STS transfers, AIS spoofing documented."},
    # RESTRICTED — orange
    {"iso": "VEN", "name": "Venezuela",   "status": "RESTRICTED", "note": "OFAC sector sanctions on PdVSA oil. Some humanitarian exemptions."},
    {"iso": "CUB", "name": "Cuba",        "status": "RESTRICTED", "note": "US embargo. Non-US vessels barred from US ports 180 days post-Cuba call."},
    {"iso": "MMR", "name": "Myanmar",     "status": "RESTRICTED", "note": "Targeted military sanctions. Jade, timber, jet fuel prohibited."},
    # MONITORED — amber
    {"iso": "SDN", "name": "Sudan",       "status": "MONITORED",  "note": "Active conflict. Darfur/Sudan OFAC sanctions. Humanitarian license required."},
    {"iso": "YEM", "name": "Yemen",       "status": "MONITORED",  "note": "Houthi SDGT designation. Red Sea attack zone. Operational + sanctions risk."},
    {"iso": "CHN", "name": "China",       "status": "MONITORED",  "note": "BIS Entity List / dual-use controls. Decoupling escalation risk."},
    {"iso": "BLR", "name": "Belarus",     "status": "MONITORED",  "note": "OFAC/EU sanctions on regime entities. Potash trade restricted."},
    {"iso": "SYR", "name": "Syria",       "status": "MONITORED",  "note": "OFAC Syria Sanctions Regulations. Broad trade restrictions in force."},
    {"iso": "LBY", "name": "Libya",       "status": "MONITORED",  "note": "UN arms embargo. Conflict-zone operational risk."},
    {"iso": "SOM", "name": "Somalia",     "status": "MONITORED",  "note": "Piracy risk zone. UN arms embargo on certain actors."},
    # CLEAR — green (major shipping nations)
    {"iso": "USA", "name": "United States",  "status": "CLEAR", "note": "Sanctions issuing authority. Full access."},
    {"iso": "DEU", "name": "Germany",        "status": "CLEAR", "note": "EU member. Standard compliance applies."},
    {"iso": "NLD", "name": "Netherlands",    "status": "CLEAR", "note": "Rotterdam — largest EU port. Full access."},
    {"iso": "SGP", "name": "Singapore",      "status": "CLEAR", "note": "Major transshipment hub. Full access."},
    {"iso": "CHE", "name": "Switzerland",    "status": "CLEAR", "note": "Full access. Neutral but mirrors EU measures on Russia."},
    {"iso": "JPN", "name": "Japan",          "status": "CLEAR", "note": "Sanctions aligned with G7."},
    {"iso": "KOR", "name": "South Korea",    "status": "CLEAR", "note": "G7-aligned sanctions posture."},
    {"iso": "AUS", "name": "Australia",      "status": "CLEAR", "note": "G7 coalition member on Russia oil price cap."},
    {"iso": "GBR", "name": "United Kingdom", "status": "CLEAR", "note": "OFSI sanctions, UK-specific Russia/Iran measures."},
    {"iso": "CAN", "name": "Canada",         "status": "CLEAR", "note": "G7 coalition. Parallel Russia sanctions in force."},
    {"iso": "IND", "name": "India",          "status": "CLEAR", "note": "Non-sanctioning. Receives Russian oil — monitor closely."},
    {"iso": "BRA", "name": "Brazil",         "status": "CLEAR", "note": "No active trade sanctions. Standard checks."},
    {"iso": "ZAF", "name": "South Africa",   "status": "CLEAR", "note": "No sanctions alignment. Cape route diversion point."},
    {"iso": "ARE", "name": "UAE",            "status": "CLEAR", "note": "Jebel Ali — major hub. Some Russia transshipment concerns."},
    {"iso": "SAU", "name": "Saudi Arabia",   "status": "CLEAR", "note": "No restrictions. OPEC+ production monitor."},
    {"iso": "EGY", "name": "Egypt",          "status": "CLEAR", "note": "Suez Canal authority. No restrictions."},
    {"iso": "MAR", "name": "Morocco",        "status": "CLEAR", "note": "Tanger Med — growing hub. Full access."},
    {"iso": "PAK", "name": "Pakistan",       "status": "CLEAR", "note": "No restrictions. Monitor Russian energy re-export."},
    {"iso": "LKA", "name": "Sri Lanka",      "status": "CLEAR", "note": "Colombo transshipment hub. Full access."},
    {"iso": "THA", "name": "Thailand",       "status": "CLEAR", "note": "Full access."},
    {"iso": "VNM", "name": "Vietnam",        "status": "CLEAR", "note": "Full access. China+1 diversion beneficiary."},
    {"iso": "IDN", "name": "Indonesia",      "status": "CLEAR", "note": "Full access. Malacca Strait corridor."},
    {"iso": "MYS", "name": "Malaysia",       "status": "CLEAR", "note": "Full access. Transshipment hub."},
    {"iso": "MEX", "name": "Mexico",         "status": "CLEAR", "note": "Full access. USMCA trade flows."},
    {"iso": "GRC", "name": "Greece",         "status": "CLEAR", "note": "Piraeus hub. Full access. World's largest ship-owning nation."},
    {"iso": "PAN", "name": "Panama",         "status": "CLEAR", "note": "Panama Canal authority. Largest ship registry (flag state)."},
    {"iso": "LBR", "name": "Liberia",        "status": "CLEAR", "note": "Open ship registry. Full access."},
    {"iso": "MHL", "name": "Marshall Islands","status": "CLEAR","note": "Major open registry. Full access."},
]

_STATUS_COLOR_MAP: dict[str, str] = {
    "PROHIBITED": "#ef4444",
    "RESTRICTED": "#f97316",
    "MONITORED":  "#f59e0b",
    "CLEAR":      "#10b981",
}

_STATUS_SCORE_MAP: dict[str, float] = {
    "PROHIBITED": 1.0,
    "RESTRICTED": 0.6,
    "MONITORED":  0.3,
    "CLEAR":      0.05,
}


# ---------------------------------------------------------------------------
# HTML helper utilities
# ---------------------------------------------------------------------------

def _card_wrap(content_html: str, border_color: str = C_BORDER) -> str:
    return (
        "<div style=\"background:" + C_CARD
        + "; border:1px solid " + border_color
        + "; border-radius:12px; padding:18px 20px; margin-bottom:12px\">"
        + content_html
        + "</div>"
    )


def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        "<div style=\"color:" + C_TEXT2 + "; font-size:0.83rem; margin-top:3px\">"
        + subtitle + "</div>"
    ) if subtitle else ""
    st.markdown(
        "<div style=\"margin-bottom:14px; margin-top:4px\">"
        "<div style=\"font-size:1.05rem; font-weight:700; color:" + C_TEXT + "\">"
        + text + "</div>"
        + sub_html + "</div>",
        unsafe_allow_html=True,
    )


def _risk_badge(risk_level: str, pulse: bool = False) -> str:
    color = _RISK_COLOR.get(risk_level, C_TEXT2)
    pulse_style = " animation:sanction-pulse 1.4s ease-in-out infinite;" if pulse else ""
    return (
        "<span style=\"background:rgba(0,0,0,0.35); color:" + color
        + "; border:1px solid " + color
        + "; padding:2px 10px; border-radius:999px;"
        " font-size:0.70rem; font-weight:700; white-space:nowrap;"
        + pulse_style + "\">"
        + risk_level + "</span>"
    )


def _impact_badge(impact: str) -> str:
    color = _IMPACT_COLOR.get(impact, C_TEXT2)
    return (
        "<span style=\"background:" + color + "22; color:" + color
        + "; border:1px solid " + color + "55;"
        " padding:2px 9px; border-radius:999px;"
        " font-size:0.68rem; font-weight:700; white-space:nowrap\">"
        + impact + "</span>"
    )


def _auth_badge(authority: str) -> str:
    color = _AUTH_COLOR.get(authority, C_ACCENT)
    return (
        "<span style=\"background:" + color + "33; color:" + color
        + "; border:1px solid " + color + "66;"
        " padding:2px 9px; border-radius:6px;"
        " font-size:0.68rem; font-weight:800; white-space:nowrap\">"
        + authority + "</span>"
    )


def _pill(text: str, color: str = C_ACCENT) -> str:
    return (
        "<span style=\"display:inline-block; background:rgba(59,130,246,0.12);"
        " color:" + color + "; border:1px solid rgba(59,130,246,0.30);"
        " padding:1px 9px; border-radius:999px; font-size:0.68rem;"
        " font-weight:600; margin:2px 3px 2px 0; white-space:nowrap\">"
        + text + "</span>"
    )


def _usd(amount: int) -> str:
    """Format USD amount with commas."""
    if amount >= 1_000_000:
        return "$" + "{:.1f}M".format(amount / 1_000_000)
    if amount >= 1_000:
        return "$" + "{:,}".format(amount)
    return "$" + str(amount)


_PULSE_CSS = """
<style>
@keyframes sanction-pulse {
    0%   { opacity: 1; }
    50%  { opacity: 0.40; }
    100% { opacity: 1; }
}
</style>
"""


# ---------------------------------------------------------------------------
# Section 1: Sanctions World Map (Choropleth)
# ---------------------------------------------------------------------------

def _render_sanctions_map() -> None:
    """Choropleth showing sanctions risk by country. Dark background, colour-coded."""
    logger.debug("Rendering sanctions choropleth world map")

    isos    = [d["iso"]    for d in _SANCTION_COUNTRIES]
    names   = [d["name"]   for d in _SANCTION_COUNTRIES]
    scores  = [_STATUS_SCORE_MAP[d["status"]] for d in _SANCTION_COUNTRIES]
    statuses = [d["status"] for d in _SANCTION_COUNTRIES]
    notes   = [d["note"]   for d in _SANCTION_COUNTRIES]

    hover_texts = [
        "<b>" + names[i] + "</b><br>"
        + "Status: <b>" + statuses[i] + "</b><br>"
        + notes[i]
        for i in range(len(isos))
    ]

    # Custom discrete colour scale
    colorscale = [
        [0.00, "#10b981"],  # CLEAR — green
        [0.25, "#10b981"],
        [0.26, "#f59e0b"],  # MONITORED — amber
        [0.55, "#f59e0b"],
        [0.56, "#f97316"],  # RESTRICTED — orange
        [0.85, "#f97316"],
        [0.86, "#ef4444"],  # PROHIBITED — red
        [1.00, "#ef4444"],
    ]

    fig = go.Figure(go.Choropleth(
        locations=isos,
        z=scores,
        text=hover_texts,
        hovertemplate="%{text}<extra></extra>",
        colorscale=colorscale,
        zmin=0.0,
        zmax=1.0,
        showscale=False,
        marker_line_color="rgba(255,255,255,0.08)",
        marker_line_width=0.5,
    ))

    # Legend swatches (manual — can't use choropleth colorbar for discrete)
    legend_items = [
        ("PROHIBITED", "#ef4444"),
        ("RESTRICTED", "#f97316"),
        ("MONITORED",  "#f59e0b"),
        ("CLEAR",      "#10b981"),
    ]
    for label, color in legend_items:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=12, color=color, symbol="square"),
            name=label,
            showlegend=True,
        ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=460,
        margin=dict(l=0, r=0, t=10, b=0),
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.12)",
            showland=True,
            landcolor="#1a2235",
            showocean=True,
            oceancolor="#0a0f1a",
            showcountries=True,
            countrycolor="rgba(255,255,255,0.07)",
            showlakes=False,
            bgcolor=C_BG,
            projection_type="natural earth",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.00,
            xanchor="right",
            x=1.0,
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

    st.plotly_chart(fig, use_container_width=True, key="compliance_sanctions_map")


# ---------------------------------------------------------------------------
# Section 2: Active Sanctions Dashboard — Card grid
# ---------------------------------------------------------------------------

def _render_sanctions_card(regime: SanctionsRegime) -> str:
    """Build HTML card for a single SanctionsRegime."""
    risk_color  = _RISK_COLOR.get(regime.risk_level, C_TEXT2)
    border_color = risk_color + "44"
    pulse = regime.risk_level == "CRITICAL"

    badges_row = (
        _auth_badge(regime.sanctioning_authority)
        + "&nbsp;&nbsp;"
        + _risk_badge(regime.risk_level, pulse=pulse)
        + "&nbsp;&nbsp;"
        + _impact_badge(regime.shipping_impact)
    )

    # Route pills (abbreviate if many)
    route_pills_html = "".join(
        _pill(rid.replace("_", " ").title()[:22])
        for rid in regime.affected_routes[:5]
    )
    if len(regime.affected_routes) > 5:
        route_pills_html += _pill(
            "+" + str(len(regime.affected_routes) - 5) + " more",
            color=C_TEXT3,
        )

    # Penalty prominent display
    penalty_html = (
        "<div style=\"text-align:center\">"
        "<div style=\"font-size:1.35rem; font-weight:800; color:" + C_DANGER + "\">"
        + _usd(regime.penalty_per_violation_usd)
        + "</div>"
        "<div style=\"font-size:0.62rem; color:" + C_TEXT3
        + "; text-transform:uppercase; letter-spacing:0.06em\">Max Penalty / Violation</div>"
        "</div>"
    )

    # Enforcement count
    enforce_color = C_DANGER if regime.enforcement_cases_2024 >= 30 else (
        C_ORANGE if regime.enforcement_cases_2024 >= 10 else C_WARN
    )
    enforce_html = (
        "<div style=\"text-align:center\">"
        "<div style=\"font-size:1.35rem; font-weight:800; color:" + enforce_color + "\">"
        + str(regime.enforcement_cases_2024)
        + "</div>"
        "<div style=\"font-size:0.62rem; color:" + C_TEXT3
        + "; text-transform:uppercase; letter-spacing:0.06em\">Enforcement Cases 2024</div>"
        "</div>"
    )

    html = (
        "<div style=\"background:" + C_CARD + "; border:1px solid " + border_color
        + "; border-radius:12px; padding:15px 17px; margin-bottom:10px\">"
        # Header
        "<div style=\"margin-bottom:9px\">"
        + badges_row
        + "</div>"
        "<div style=\"font-size:0.93rem; font-weight:700; color:" + C_TEXT
        + "; margin-bottom:7px; line-height:1.35\">"
        + regime.regime_name
        + "</div>"
        # Scope
        "<div style=\"font-size:0.75rem; color:" + C_TEXT2
        + "; line-height:1.5; margin-bottom:10px\">"
        + regime.scope[:280]
        + ("..." if len(regime.scope) > 280 else "")
        + "</div>"
        # Metrics row
        "<div style=\"display:grid; grid-template-columns:1fr 1fr; gap:10px;"
        " margin-bottom:10px; background:rgba(0,0,0,0.20);"
        " border-radius:8px; padding:10px 12px\">"
        + penalty_html
        + enforce_html
        + "</div>"
        # Affected routes
        "<div style=\"margin-bottom:8px\">"
        "<div style=\"font-size:0.62rem; text-transform:uppercase; letter-spacing:0.07em;"
        " color:" + C_TEXT3 + "; margin-bottom:4px\">Affected Routes</div>"
        + route_pills_html
        + "</div>"
        # Compliance cost
        "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "\">"
        "Due diligence cost: <span style=\"color:" + C_WARN + "; font-weight:700\">"
        + _usd(regime.compliance_cost_per_voyage_usd)
        + " / voyage</span>"
        "&nbsp;&nbsp;|&nbsp;&nbsp;Effective: "
        + regime.effective_date.strftime("%b %d, %Y")
        + "</div>"
        "</div>"
    )
    return html


def _render_sanctions_dashboard() -> None:
    """Render 2-column card grid for all active sanctions regimes."""
    logger.debug("Rendering active sanctions dashboard cards")

    if not ACTIVE_SANCTIONS:
        st.info("No active sanctions regimes loaded.")
        return

    st.markdown(_PULSE_CSS, unsafe_allow_html=True)

    # Sort: CRITICAL first, then by enforcement cases desc
    order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    sorted_regimes = sorted(
        ACTIVE_SANCTIONS,
        key=lambda r: (order.get(r.risk_level, 9), -r.enforcement_cases_2024),
    )

    col_a, col_b = st.columns(2)
    for i, regime in enumerate(sorted_regimes):
        html = _render_sanctions_card(regime)
        if i % 2 == 0:
            with col_a:
                st.markdown(html, unsafe_allow_html=True)
        else:
            with col_b:
                st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 3: Dark Fleet Tracker
# ---------------------------------------------------------------------------

def _render_dark_fleet() -> None:
    """Render dark fleet tracker section with pie chart and stats."""
    logger.debug("Rendering dark fleet tracker section")

    df = DARK_FLEET_2025

    # Summary metric cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.7rem; font-weight:800; color:" + C_DANGER + "\">~"
            + str(df.estimated_vessels) + "</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Dark Fleet Vessels (Est.)</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(249,115,22,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.7rem; font-weight:800; color:" + C_ORANGE + "\">~4,700</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Total Global Tanker Fleet</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(245,158,11,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.7rem; font-weight:800; color:" + C_WARN + "\">$2-4M</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Shadow Premium / Voyage</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with col4:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(16,185,129,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.7rem; font-weight:800; color:" + C_HIGH + "\">~50%</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Cap Enforcement Effectiveness</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style=\"height:14px\"></div>", unsafe_allow_html=True)

    # Charts row
    chart_col, info_col = st.columns([1, 1])

    with chart_col:
        # Pie chart: dark fleet vs clean fleet
        dark_count = df.estimated_vessels
        clean_count = max(0, 4700 - dark_count)  # guard: estimated_vessels should not exceed total

        fig_pie = go.Figure(go.Pie(
            labels=["Dark Fleet (Unregulated)", "Clean / Western Fleet"],
            values=[dark_count, clean_count],
            hole=0.55,
            marker=dict(
                colors=[C_DANGER, C_HIGH],
                line=dict(color=C_BG, width=3),
            ),
            textinfo="label+percent",
            textfont=dict(size=11, color=C_TEXT),
            hovertemplate=(
                "<b>%{label}</b><br>"
                "Vessels: %{value:,}<br>"
                "Share: %{percent}<extra></extra>"
            ),
        ))
        fig_pie.update_layout(
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            height=300,
            margin=dict(l=10, r=10, t=30, b=10),
            title=dict(
                text="Global Tanker Fleet — Dark vs. Clean (2025)",
                font=dict(size=12, color=C_TEXT2),
                x=0.5,
            ),
            legend=dict(
                font=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
                orientation="h",
                yanchor="bottom",
                y=-0.10,
                xanchor="center",
                x=0.5,
            ),
            annotations=[dict(
                text="~" + str(dark_count) + "<br>dark",
                x=0.5, y=0.5,
                font=dict(size=13, color=C_DANGER, family="sans-serif"),
                showarrow=False,
            )],
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="compliance_dark_fleet_pie")

    with info_col:
        # Flag states
        flag_pills = "".join(
            _pill(fs, color=C_ORANGE) for fs in df.flag_states
        )

        # Operating regions
        region_pills = "".join(
            _pill(r, color=C_WARN) for r in df.operating_regions[:5]
        )
        if len(df.operating_regions) > 5:
            region_pills += _pill("+" + str(len(df.operating_regions) - 5) + " more", color=C_TEXT3)

        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.20);"
            " border-radius:12px; padding:16px 18px\">"
            "<div style=\"font-size:0.85rem; font-weight:700; color:" + C_TEXT
            + "; margin-bottom:10px\">Dark Fleet Profile</div>"
            "<div style=\"font-size:0.62rem; text-transform:uppercase; letter-spacing:0.07em;"
            " color:" + C_TEXT3 + "; margin-bottom:4px\">Primary Flag States</div>"
            + flag_pills
            + "<div style=\"font-size:0.62rem; text-transform:uppercase; letter-spacing:0.07em;"
            " color:" + C_TEXT3 + "; margin-top:10px; margin-bottom:4px\">Operating Regions</div>"
            + region_pills
            + "<div style=\"font-size:0.72rem; color:" + C_TEXT2
            + "; margin-top:10px; line-height:1.55\">"
            + "<b style=\"color:" + C_TEXT + "\">Insurance: </b>"
            "No Western P&amp;I. Using Russian RNRC, Iranian government mutual, or uninsured. "
            "~35% operating with no valid insurance."
            "</div>"
            "<div style=\"margin-top:8px; font-size:0.72rem; color:" + C_TEXT2
            + "; line-height:1.55\">"
            + "<b style=\"color:" + C_TEXT + "\">Detection risk: </b>"
            "<span style=\"color:" + C_WARN + "; font-weight:700\">" + df.detection_risk + "</span>"
            " — AIS spoofing common; satellite AIS monitoring improving detection rates."
            "</div>"
            # Seizure stats
            "<div style=\"background:rgba(239,68,68,0.07); border:1px solid rgba(239,68,68,0.20);"
            " border-radius:8px; padding:10px 12px; margin-top:10px\">"
            "<div style=\"font-size:0.72rem; font-weight:700; color:" + C_TEXT
            + "; margin-bottom:5px\">2024 Enforcement Statistics</div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "\">Vessel detentions (dark fleet related): <b style=\"color:" + C_DANGER + "\">23</b></div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">OFAC designations — shipping entities: <b style=\"color:" + C_DANGER + "\">41</b></div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">EU dark fleet advisories issued: <b style=\"color:" + C_ORANGE + "\">8</b></div>"
            "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; margin-top:3px\">Cargo seizures (sanctions violations): <b style=\"color:" + C_WARN + "\">17</b></div>"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    # Market impact narrative
    st.markdown(
        "<div style=\"background:rgba(239,68,68,0.06); border:1px solid rgba(239,68,68,0.20);"
        " border-radius:10px; padding:14px 18px; margin-top:4px;"
        " font-size:0.82rem; color:" + C_TEXT2 + "; line-height:1.6\">"
        "<b style=\"color:" + C_TEXT + "\">Market Impact: </b>"
        + df.market_impact
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4: Compliance Cost Calculator
# ---------------------------------------------------------------------------

def _render_compliance_calculator(route_results) -> None:
    """Interactive compliance cost calculator for route selection."""
    logger.debug("Rendering compliance cost calculator")

    route_names_ordered = [
        (rid, _ROUTE_NAMES.get(rid, rid.replace("_", " ").title()))
        for rid in sorted(_ROUTE_NAMES.keys())
    ]
    route_options = [name for (_, name) in route_names_ordered]
    route_ids     = [rid  for (rid, _) in route_names_ordered]

    selected_name = st.selectbox(
        "Select Route",
        options=route_options,
        index=0,
        key="compliance_route_selector",
    )
    selected_idx = route_options.index(selected_name)
    selected_rid = route_ids[selected_idx]

    result = compute_route_compliance_risk(selected_rid)

    risk_color = _RISK_COLOR.get(result["risk_level"], C_TEXT2)
    dd = result["due_diligence"]

    # Metric row
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid "
            + risk_color + "44;"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.5rem; font-weight:800; color:" + risk_color + "\">"
            + str(int(result["risk_score"] * 100)) + "</div>"
            "<div style=\"font-size:0.68rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Compliance Risk Score (0-100)</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with mc2:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid "
            + risk_color + "44;"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.5rem; font-weight:800; color:" + risk_color + "\">"
            + result["risk_level"] + "</div>"
            "<div style=\"font-size:0.68rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Risk Level</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with mc3:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(245,158,11,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.5rem; font-weight:800; color:" + C_WARN + "\">"
            + _usd(result["compliance_cost_usd"]) + "</div>"
            "<div style=\"font-size:0.68rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Due Diligence Cost / Voyage</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with mc4:
        # Dark fleet rate comparison
        dark_discount = 0.72 if result["risk_level"] in ("CRITICAL", "HIGH") else 0.85
        clean_rate_placeholder = 3200  # representative $/FEU
        dark_rate = int(clean_rate_placeholder * dark_discount)
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.30);"
            " border-radius:10px; padding:14px 16px; text-align:center\">"
            "<div style=\"font-size:1.5rem; font-weight:800; color:" + C_DANGER + "\">"
            "$" + str(dark_rate) + "</div>"
            "<div style=\"font-size:0.68rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Dark Fleet Rate (est. $/FEU) vs $" + str(clean_rate_placeholder) + " clean</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style=\"height:10px\"></div>", unsafe_allow_html=True)

    detail_col, diligence_col = st.columns([1, 1])

    with detail_col:
        # Sanctions exposures table
        exposures = result["primary_exposures"]
        if exposures:
            exp_rows = ""
            for exp in exposures:
                imp_color = _IMPACT_COLOR.get(exp["shipping_impact"], C_TEXT2)
                exp_rows += (
                    "<tr style=\"border-bottom:1px solid rgba(255,255,255,0.04)\">"
                    "<td style=\"color:" + C_TEXT + "; font-size:0.78rem;"
                    " padding:7px 8px; font-weight:600\">" + exp["country"] + "</td>"
                    "<td style=\"color:" + imp_color + "; font-size:0.75rem;"
                    " padding:7px 8px; font-weight:700\">" + exp["shipping_impact"] + "</td>"
                    "<td style=\"color:" + C_TEXT2 + "; font-size:0.75rem;"
                    " padding:7px 8px\">"
                    + "{:.0f}%".format(exp["exposure_weight"] * 100)
                    + "</td>"
                    "<td style=\"color:" + C_DANGER + "; font-size:0.75rem;"
                    " padding:7px 8px; font-weight:700\">"
                    + _usd(exp["max_penalty_usd"])
                    + "</td>"
                    "</tr>"
                )
            h_style = (
                "color:" + C_TEXT3 + "; font-size:0.65rem; text-transform:uppercase;"
                " letter-spacing:0.07em; padding:5px 8px;"
                " border-bottom:1px solid rgba(255,255,255,0.10)"
            )
            exp_table = (
                "<div style=\"overflow-x:auto\">"
                "<table style=\"width:100%; border-collapse:collapse\">"
                "<thead><tr>"
                "<th style=\"" + h_style + "\">Country</th>"
                "<th style=\"" + h_style + "\">Impact</th>"
                "<th style=\"" + h_style + "\">Exposure</th>"
                "<th style=\"" + h_style + "\">Max Penalty</th>"
                "</tr></thead>"
                "<tbody>" + exp_rows + "</tbody>"
                "</table></div>"
            )
            st.markdown(
                _card_wrap(
                    "<div style=\"font-size:0.80rem; font-weight:700; color:" + C_TEXT
                    + "; margin-bottom:10px\">Sanctions Exposure Breakdown</div>"
                    + exp_table
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                _card_wrap(
                    "<div style=\"color:" + C_HIGH + "; font-size:0.82rem\">"
                    "No identified sanctions exposure on this route.</div>"
                ),
                unsafe_allow_html=True,
            )

    with diligence_col:
        # Due diligence steps
        steps_html = "".join(
            "<div style=\"display:flex; align-items:flex-start; gap:8px;"
            " margin-bottom:6px\">"
            "<span style=\"color:" + risk_color + "; font-size:0.85rem; margin-top:1px\">"
            "&#x2713;</span>"
            "<span style=\"font-size:0.76rem; color:" + C_TEXT2 + "; line-height:1.4\">"
            + step + "</span>"
            "</div>"
            for step in dd["steps"]
        )

        docs_html = "".join(
            "<div style=\"font-size:0.73rem; color:" + C_TEXT2 + ";"
            " margin-bottom:4px; padding-left:8px;"
            " border-left:2px solid rgba(59,130,246,0.40)\">"
            + doc + "</div>"
            for doc in dd["documentation"]
        )

        st.markdown(
            _card_wrap(
                "<div style=\"font-size:0.80rem; font-weight:700; color:" + C_TEXT
                + "; margin-bottom:6px\">Due Diligence Requirements</div>"
                "<div style=\"font-size:0.72rem; color:" + risk_color
                + "; font-weight:600; margin-bottom:10px\">"
                + dd["level"] + "</div>"
                "<div style=\"margin-bottom:10px\">"
                + steps_html
                + "</div>"
                "<div style=\"font-size:0.65rem; text-transform:uppercase;"
                " letter-spacing:0.07em; color:" + C_TEXT3
                + "; margin-bottom:6px\">Required Documentation</div>"
                + docs_html
                + "<div style=\"background:rgba(239,68,68,0.08);"
                " border:1px solid rgba(239,68,68,0.20);"
                " border-radius:8px; padding:10px 12px; margin-top:10px\">"
                "<div style=\"font-size:0.72rem; color:" + C_TEXT2 + "; line-height:1.5\">"
                "<b style=\"color:" + C_TEXT + "\">Recommendation: </b>"
                + result["recommendation"]
                + "</div></div>"
            ),
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 5: Regulatory Timeline
# ---------------------------------------------------------------------------

_REGULATORY_TIMELINE: list[dict] = [
    {
        "year": "2025",
        "quarter": "Q2",
        "title": "EU Russia Oil Sanctions — Package XIV",
        "description": (
            "Expected additional EU sanctions package targeting Russia dark fleet and "
            "shadow insurance providers. Enhanced vessel identification requirements."
        ),
        "impact": "HIGH",
        "affected": ["tanker market", "EU-Russia routes"],
    },
    {
        "year": "2025",
        "quarter": "Q3",
        "title": "OFAC Russia Enforcement Escalation",
        "description": (
            "OFAC signalled heightened enforcement against price cap violations. "
            "New designation authority for vessels transporting above-cap Russian crude. "
            "Expected secondary sanctions risk expansion."
        ),
        "impact": "CRITICAL",
        "affected": ["tanker market", "Russian oil routes", "shadow fleet insurers"],
    },
    {
        "year": "2026",
        "quarter": "Q1",
        "title": "Enhanced Beneficial Ownership Disclosure (EU)",
        "description": (
            "EU Anti-Money Laundering Regulation (AMLR) requires enhanced UBO disclosure "
            "for all shipping transactions above EUR 10,000. Shipping companies must "
            "verify and report beneficial owners across all corporate layers."
        ),
        "impact": "HIGH",
        "affected": ["all shipping", "EU-flag vessels", "EU port calls"],
    },
    {
        "year": "2026",
        "quarter": "Q1",
        "title": "EU Carbon Border Adjustment Mechanism — Full Implementation",
        "description": (
            "CBAM fully operational for steel, cement, aluminium, fertilizers, hydrogen, "
            "and electricity. Shipping companies importing these goods into EU must provide "
            "carbon certificates. Compliance cost: estimated $15-45/tonne CO2 equivalent."
        ),
        "impact": "HIGH",
        "affected": ["bulk cargo", "EU-bound routes", "steel/fertilizer shipments"],
    },
    {
        "year": "2026",
        "quarter": "Q3",
        "title": "IMO Fuel EU / EU ETS — Shipping Fully In Scope",
        "description": (
            "European Union Emissions Trading System extended to cover 100% of emissions "
            "from intra-EU voyages, and 50% from EU-departing/arriving international voyages. "
            "Shipping operators must purchase EU Allowances (EUAs) — forecast ~EUR 60-90/tonne CO2."
        ),
        "impact": "HIGH",
        "affected": ["all EU-touching routes", "container shipping", "tankers"],
    },
    {
        "year": "2027",
        "quarter": "Q1",
        "title": "IMO CII Rating Tightening — Category D/E Vessels",
        "description": (
            "IMO Carbon Intensity Indicator (CII) annual reduction factor tightens. "
            "Vessels rated D or E for 3 consecutive years face mandatory Corrective Action Plan. "
            "2027 tightening increases proportion of fleet facing non-compliance risk to est. 25%."
        ),
        "impact": "MODERATE",
        "affected": ["older vessels", "tankers", "bulk carriers", "container ships"],
    },
    {
        "year": "2027",
        "quarter": "Q2",
        "title": "OFAC Digital Asset Sanctions — Crypto Payments Watch",
        "description": (
            "OFAC expanding sanctions compliance guidance to cover digital asset payments "
            "in shipping transactions. Sanctions evasion via cryptocurrency flagged as "
            "emerging risk — especially for Russia, Iran, and North Korea trades."
        ),
        "impact": "MODERATE",
        "affected": ["OFAC-regulated entities", "vessels accepting crypto payments"],
    },
    {
        "year": "2028",
        "quarter": "Q1",
        "title": "Potential IMO Digital Vessel Compliance Requirements",
        "description": (
            "IMO working group on digitalisation expected to propose mandatory electronic "
            "compliance certificates, AIS data retention requirements, and digital "
            "customs pre-clearance protocols for major ports. Real-time vessel monitoring "
            "for sanctions compliance could become mandatory."
        ),
        "impact": "MODERATE",
        "affected": ["all international shipping", "port state control inspections"],
    },
]


def _render_regulatory_timeline() -> None:
    """Render upcoming regulatory events as a styled timeline."""
    logger.debug("Rendering regulatory compliance timeline")

    timeline_html = ""
    prev_year = ""

    for event in _REGULATORY_TIMELINE:
        is_new_year = event["year"] != prev_year
        prev_year = event["year"]

        impact_color = _RISK_COLOR.get(event["impact"], C_TEXT2)

        year_marker = ""
        if is_new_year:
            year_marker = (
                "<div style=\"font-size:1.1rem; font-weight:800; color:" + C_ACCENT
                + "; margin-top:16px; margin-bottom:8px; "
                "border-bottom:1px solid rgba(59,130,246,0.25); padding-bottom:6px\">"
                + event["year"]
                + "</div>"
            )

        affected_pills = "".join(
            _pill(a, color=C_TEXT3) for a in event["affected"]
        )

        card = (
            "<div style=\"display:flex; gap:12px; margin-bottom:10px\">"
            "<div style=\"flex-shrink:0; text-align:center; width:38px\">"
            "<div style=\"font-size:0.65rem; font-weight:700; color:" + impact_color
            + "; background:" + impact_color + "22; border:1px solid "
            + impact_color + "44;"
            " border-radius:6px; padding:3px 5px; line-height:1.2\">"
            + event["quarter"]
            + "</div>"
            "<div style=\"width:2px; height:calc(100% - 28px); background:"
            + impact_color + "33; margin:4px auto 0 auto\"></div>"
            "</div>"
            "<div style=\"flex:1; background:" + C_CARD
            + "; border:1px solid rgba(255,255,255,0.07);"
            " border-left:3px solid " + impact_color + ";"
            " border-radius:0 10px 10px 0; padding:12px 14px\">"
            "<div style=\"display:flex; justify-content:space-between;"
            " align-items:flex-start; flex-wrap:wrap; gap:6px; margin-bottom:6px\">"
            "<div style=\"font-size:0.86rem; font-weight:700; color:" + C_TEXT + "\">"
            + event["title"] + "</div>"
            "<span style=\"font-size:0.66rem; font-weight:700; color:" + impact_color
            + "; border:1px solid " + impact_color + "44;"
            " padding:2px 8px; border-radius:999px; white-space:nowrap\">"
            + event["impact"] + "</span>"
            "</div>"
            "<div style=\"font-size:0.76rem; color:" + C_TEXT2
            + "; line-height:1.5; margin-bottom:8px\">"
            + event["description"]
            + "</div>"
            "<div>" + affected_pills + "</div>"
            "</div>"
            "</div>"
        )

        timeline_html += year_marker + card

    st.markdown(
        "<div style=\"background:" + C_CARD + "; border:1px solid rgba(59,130,246,0.15);"
        " border-radius:12px; padding:16px 20px\">"
        + timeline_html
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 6: Route Compliance Scoring Table
# ---------------------------------------------------------------------------

_DILIGENCE_LABELS: dict[str, str] = {
    "CRITICAL": "Do Not Use",
    "HIGH":     "Enhanced",
    "MODERATE": "Standard Enhanced",
    "LOW":      "Standard",
}

_DILIGENCE_COLOR: dict[str, str] = {
    "CRITICAL": C_DANGER,
    "HIGH":     C_ORANGE,
    "MODERATE": C_WARN,
    "LOW":      C_HIGH,
}


def _render_route_scoring_table() -> None:
    """Render all 17 routes with compliance risk score, exposure, and diligence level."""
    logger.debug("Rendering route compliance scoring table")

    all_scores = get_all_route_compliance_scores()

    if not all_scores:
        st.info("No route compliance data available.")
        return

    rows_html = ""
    for rank, (rid, score, risk_level) in enumerate(all_scores, 1):
        score_pct = int(score * 100)
        score_color = _RISK_COLOR.get(risk_level, C_TEXT2)
        row_bg = (
            "rgba(239,68,68,0.06)" if risk_level == "CRITICAL"
            else "rgba(249,115,22,0.04)" if risk_level == "HIGH"
            else "rgba(245,158,11,0.03)" if risk_level == "MODERATE"
            else "transparent"
        )

        route_name = _ROUTE_NAMES.get(rid, rid.replace("_", " ").title())
        diligence_label = _DILIGENCE_LABELS.get(risk_level, "Standard")
        diligence_color = _DILIGENCE_COLOR.get(risk_level, C_HIGH)

        # Primary sanctions exposure countries
        result = compute_route_compliance_risk(rid)
        primary_countries = [
            e["country"] for e in result["primary_exposures"][:3]
        ]
        exposure_text = ", ".join(primary_countries) if primary_countries else "None"

        compliance_cost = result["compliance_cost_usd"]

        rows_html += (
            "<tr style=\"background:" + row_bg
            + "; border-bottom:1px solid rgba(255,255,255,0.04)\">"
            "<td style=\"color:" + C_TEXT3 + "; font-size:0.72rem; padding:8px 7px;"
            " text-align:center; font-weight:600\">" + str(rank) + "</td>"
            "<td style=\"color:" + C_TEXT + "; font-size:0.79rem; padding:8px 7px;"
            " font-weight:600\">" + route_name + "</td>"
            "<td style=\"padding:8px 7px; min-width:130px\">"
            "<div style=\"display:flex; align-items:center; gap:6px\">"
            "<div style=\"flex:1; background:rgba(255,255,255,0.06);"
            " border-radius:4px; height:7px\">"
            "<div style=\"width:" + str(score_pct) + "%; background:" + score_color
            + "; border-radius:4px; height:7px\"></div>"
            "</div>"
            "<span style=\"font-size:0.75rem; font-weight:700; color:" + score_color
            + "; min-width:28px\">" + str(score_pct) + "</span>"
            "</div></td>"
            "<td style=\"color:" + score_color + "; font-size:0.75rem; padding:8px 7px;"
            " font-weight:700\">" + risk_level + "</td>"
            "<td style=\"color:" + C_TEXT2 + "; font-size:0.74rem; padding:8px 7px\">"
            + exposure_text + "</td>"
            "<td style=\"color:" + C_WARN + "; font-size:0.74rem; padding:8px 7px;"
            " font-weight:600; white-space:nowrap\">"
            + _usd(compliance_cost) + "</td>"
            "<td style=\"padding:8px 7px; white-space:nowrap\">"
            "<span style=\"font-size:0.70rem; font-weight:700; color:" + diligence_color
            + "; background:" + diligence_color + "18;"
            " border:1px solid " + diligence_color + "44;"
            " padding:2px 9px; border-radius:999px\">"
            + diligence_label + "</span>"
            "</td>"
            "</tr>"
        )

    h_style = (
        "color:" + C_TEXT3 + "; font-size:0.66rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:6px 7px; text-align:left;"
        " border-bottom:1px solid rgba(255,255,255,0.10)"
    )
    table_html = (
        "<div style=\"overflow-x:auto\">"
        "<table style=\"width:100%; border-collapse:collapse\">"
        "<thead><tr>"
        "<th style=\"" + h_style + "; text-align:center\">#</th>"
        "<th style=\"" + h_style + "\">Route</th>"
        "<th style=\"" + h_style + "\">Risk Score</th>"
        "<th style=\"" + h_style + "\">Risk Level</th>"
        "<th style=\"" + h_style + "\">Primary Exposure</th>"
        "<th style=\"" + h_style + "\">Due Diligence Cost</th>"
        "<th style=\"" + h_style + "\">Diligence Level</th>"
        "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table>"
        "</div>"
    )

    st.markdown(
        "<div style=\"background:" + C_CARD + "; border:1px solid rgba(59,130,246,0.18);"
        " border-radius:12px; padding:16px 18px\">"
        + table_html
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 6: IMO CII Rating Reference (A–E color-coded)
# ---------------------------------------------------------------------------

# CII rating colour mapping — IMO standard (MEPC.339(76) and subsequent circulars)
_CII_COLORS: dict[str, str] = {
    "A": "#10b981",   # green      — superior efficiency
    "B": "#34d399",   # light green — minor superior efficiency
    "C": "#f59e0b",   # amber      — moderate (compliant baseline)
    "D": "#f97316",   # orange     — minor inferior efficiency (corrective action if 3 consecutive)
    "E": "#ef4444",   # red        — inferior efficiency (mandatory corrective action plan)
}

# Descriptive band metadata
_CII_BANDS: list[dict] = [
    {
        "rating": "A",
        "label": "Superior",
        "description": (
            "Vessel significantly exceeds the required annual CII. "
            "Demonstrates best-in-class carbon efficiency. "
            "No regulatory action required."
        ),
        "action": "None — recognised as industry leader",
        "threshold": "≥ 15% above required CII",
    },
    {
        "rating": "B",
        "label": "Minor Superior",
        "description": (
            "Vessel moderately exceeds the required annual CII. "
            "Good efficiency performance relative to IMO trajectory."
        ),
        "action": "None — compliant with margin",
        "threshold": "5–15% above required CII",
    },
    {
        "rating": "C",
        "label": "Moderate (Baseline)",
        "description": (
            "Vessel meets the required annual CII. "
            "Compliant baseline — the minimum acceptable performance level. "
            "~30–35% of the global fleet currently rated C."
        ),
        "action": "None — meets minimum IMO requirement",
        "threshold": "Within ±5% of required CII",
    },
    {
        "rating": "D",
        "label": "Minor Inferior",
        "description": (
            "Vessel falls below the required annual CII. "
            "If rated D for 3 consecutive years, a mandatory Corrective Action Plan "
            "must be submitted to flag state and approved."
        ),
        "action": "Corrective Action Plan required after 3 consecutive D ratings",
        "threshold": "5–15% below required CII",
    },
    {
        "rating": "E",
        "label": "Inferior",
        "description": (
            "Vessel significantly exceeds required carbon intensity. "
            "Mandatory Corrective Action Plan required immediately — "
            "a single E rating triggers compliance action. "
            "Port State Control may scrutinise vessels rated E."
        ),
        "action": "Immediate Corrective Action Plan required",
        "threshold": "≥ 15% below required CII",
    },
]


def _render_cii_rating_reference() -> None:
    """Render IMO CII A–E rating reference with color-coded bands and descriptions."""
    logger.debug("Rendering IMO CII rating reference section")

    # Data currency warning
    st.markdown(
        "<div style=\"background:rgba(245,158,11,0.08); border:1px solid rgba(245,158,11,0.30);"
        " border-radius:10px; padding:12px 16px; margin-bottom:16px;"
        " font-size:0.78rem; color:" + C_TEXT2 + "; line-height:1.5\">"
        "<b style=\"color:" + C_WARN + "\">&#9888; Data Currency Notice:</b> "
        "IMO CII reduction factors and rating boundary vectors (d&#x2081;–d&#x2084;) are "
        "reviewed and tightened annually at MEPC sessions. The bands shown here reflect "
        "MEPC.339(76) guidance and subsequent circulars. Verify against the latest "
        "MEPC circular (MEPC.1/Circ.896 series) before operational or chartering decisions. "
        "Next scheduled MEPC review: MEPC 84 (2025)."
        "</div>",
        unsafe_allow_html=True,
    )

    # Rating band cards (one per rating, horizontal row)
    cols = st.columns(5)
    for col, band in zip(cols, _CII_BANDS):
        rating = band["rating"]
        color = _CII_COLORS[rating]
        bg = color + "18"
        border = color + "55"
        with col:
            st.markdown(
                "<div style=\"background:" + bg + "; border:1px solid " + border + ";"
                " border-top:4px solid " + color + ";"
                " border-radius:10px; padding:14px 12px; text-align:center;"
                " min-height:220px\">"
                "<div style=\"font-size:2.2rem; font-weight:900; color:" + color + ";"
                " line-height:1\">" + rating + "</div>"
                "<div style=\"font-size:0.68rem; font-weight:700; color:" + color + ";"
                " text-transform:uppercase; letter-spacing:0.06em; margin-top:4px;"
                " margin-bottom:10px\">" + band["label"] + "</div>"
                "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; line-height:1.45;"
                " margin-bottom:8px; text-align:left\">" + band["description"] + "</div>"
                "<div style=\"background:rgba(0,0,0,0.25); border-radius:6px;"
                " padding:6px 8px; margin-top:8px; text-align:left\">"
                "<div style=\"font-size:0.60rem; text-transform:uppercase; letter-spacing:0.06em;"
                " color:" + C_TEXT3 + "; margin-bottom:3px\">Threshold</div>"
                "<div style=\"font-size:0.68rem; color:" + color + "; font-weight:600\">"
                + band["threshold"] + "</div>"
                "</div>"
                "<div style=\"background:rgba(0,0,0,0.20); border-radius:6px;"
                " padding:6px 8px; margin-top:6px; text-align:left\">"
                "<div style=\"font-size:0.60rem; text-transform:uppercase; letter-spacing:0.06em;"
                " color:" + C_TEXT3 + "; margin-bottom:3px\">Required Action</div>"
                "<div style=\"font-size:0.65rem; color:" + C_TEXT2 + "; line-height:1.35\">"
                + band["action"] + "</div>"
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    # CII bar chart — visual rating spectrum
    st.markdown("<div style=\"height:12px\"></div>", unsafe_allow_html=True)
    fig_cii = go.Figure()

    cii_labels = [b["rating"] + " — " + b["label"] for b in _CII_BANDS]
    cii_colors = [_CII_COLORS[b["rating"]] for b in _CII_BANDS]
    # Arbitrary equal-width bars to show the color spectrum
    cii_values = [1, 1, 1, 1, 1]

    fig_cii.add_trace(go.Bar(
        x=cii_labels,
        y=cii_values,
        marker_color=cii_colors,
        marker_line_width=0,
        text=[b["threshold"] for b in _CII_BANDS],
        textposition="inside",
        textfont=dict(color="#0a0f1a", size=10, family="Inter, sans-serif"),
        hovertemplate=(
            "<b>CII Rating %{x}</b><br>"
            "Threshold: %{text}<br>"
            "<extra></extra>"
        ),
        showlegend=False,
    ))

    fig_cii.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=100,
        margin=dict(l=10, r=10, t=8, b=8),
        xaxis=dict(
            tickfont=dict(color=C_TEXT2, size=11),
            showgrid=False,
            zeroline=False,
            linecolor="rgba(255,255,255,0.07)",
        ),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
        bargap=0.04,
        font=dict(color=C_TEXT),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig_cii, use_container_width=True, key="compliance_cii_spectrum_bar")

    # Fleet context note
    st.markdown(
        "<div style=\"background:rgba(59,130,246,0.06); border:1px solid rgba(59,130,246,0.20);"
        " border-radius:8px; padding:10px 14px; font-size:0.76rem; color:" + C_TEXT2 + ";"
        " line-height:1.55\">"
        "<b style=\"color:" + C_TEXT + "\">2025 Fleet Distribution (estimated):</b> "
        "~14% rated A &nbsp;|&nbsp; ~21% rated B &nbsp;|&nbsp; ~34% rated C &nbsp;|&nbsp; "
        "~18% rated D &nbsp;|&nbsp; ~13% rated E. "
        "IMO targets a 40% reduction in carbon intensity across the fleet by 2030 vs 2008 baseline."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# CSV export helper
# ---------------------------------------------------------------------------

def _build_compliance_csv() -> str:
    """Build CSV string of all route compliance scores."""
    all_scores = get_all_route_compliance_scores()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Rank",
        "Route ID",
        "Route Name",
        "Risk Score (0-100)",
        "Risk Level",
        "Primary Exposure Countries",
        "Due Diligence Cost (USD)",
        "Diligence Level",
    ])
    for rank, (rid, score, risk_level) in enumerate(all_scores, 1):
        result = compute_route_compliance_risk(rid)
        primary_countries = ", ".join(
            e["country"] for e in result["primary_exposures"][:3]
        ) or "None"
        writer.writerow([
            rank,
            rid,
            _ROUTE_NAMES.get(rid, rid.replace("_", " ").title()),
            int(score * 100),
            risk_level,
            primary_countries,
            result["compliance_cost_usd"],
            _DILIGENCE_LABELS.get(risk_level, "Standard"),
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(route_results, port_results, macro_data) -> None:
    """Render the Sanctions Compliance Monitor tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer.
    port_results : list[PortDemandResult]
        Current port demand results.
    macro_data : dict
        Global macro indicators dict.
    """
    logger.info("Rendering Sanctions Compliance Monitor tab")

    st.header("Sanctions Compliance Monitor")

    total_critical = sum(1 for s in ACTIVE_SANCTIONS if s.risk_level == "CRITICAL")
    total_prohibited = sum(1 for s in ACTIVE_SANCTIONS if s.shipping_impact == "PROHIBITED")
    total_enforcement = sum(s.enforcement_cases_2024 for s in ACTIVE_SANCTIONS)

    # Top-line summary
    sc1, sc2, sc3, sc4 = st.columns(4)
    with sc1:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.30);"
            " border-radius:10px; padding:12px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_DANGER + "\">"
            + str(total_critical) + "</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "CRITICAL Regimes Active</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with sc2:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(239,68,68,0.30);"
            " border-radius:10px; padding:12px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_DANGER + "\">"
            + str(total_prohibited) + "</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "PROHIBITED Sanctions</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with sc3:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(245,158,11,0.30);"
            " border-radius:10px; padding:12px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_WARN + "\">"
            + str(total_enforcement) + "</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Public Enforcement Actions (2024)</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with sc4:
        st.markdown(
            "<div style=\"background:" + C_CARD + "; border:1px solid rgba(249,115,22,0.30);"
            " border-radius:10px; padding:12px 16px; text-align:center\">"
            "<div style=\"font-size:1.6rem; font-weight:800; color:" + C_ORANGE + "\">~"
            + str(DARK_FLEET_2025.estimated_vessels) + "</div>"
            "<div style=\"font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px\">"
            "Dark Fleet Vessels Active</div>"
            "</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style=\"height:10px\"></div>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Sanctions World Map
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Sanctions World Map",
        (
            "Countries colour-coded by shipping sanctions status. "
            "Red = prohibited trade. Orange = restricted. "
            "Amber = monitored / caution. Green = clear for standard operations."
        ),
    )
    _render_sanctions_map()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Active Sanctions Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Active Sanctions Regimes (2025-2026)",
        (
            str(len(ACTIVE_SANCTIONS)) + " active regimes tracked. "
            "CRITICAL badges pulse. Penalty amounts in red. "
            "Sorted by risk severity and 2024 enforcement frequency."
        ),
    )
    _render_sanctions_dashboard()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Dark Fleet Tracker
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Dark Fleet Tracker (2025)",
        (
            "~600 vessels operating outside Western regulatory framework, "
            "primarily serving Russian, Iranian, and Venezuelan oil flows. "
            "No Western P&I insurance. Significant cap enforcement leakage."
        ),
    )
    _render_dark_fleet()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Compliance Cost Calculator
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Compliance Cost Calculator",
        (
            "Select any route to view its sanctions risk score, "
            "due diligence requirements, documentation checklist, "
            "and risk-adjusted rate comparison vs. dark fleet operators."
        ),
    )
    _render_compliance_calculator(route_results)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Regulatory Timeline
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Regulatory Timeline — Upcoming Compliance Requirements",
        "Key upcoming sanctions actions, regulatory deadlines, and compliance milestones (2025-2028).",
    )
    _render_regulatory_timeline()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 6 — IMO CII Rating Reference
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "IMO CII Rating Reference (A–E)",
        (
            "Carbon Intensity Indicator ratings — color-coded by severity. "
            "Mandatory under MARPOL Annex VI since 2023. "
            "Thresholds tighten annually. Verify against latest MEPC circular."
        ),
    )
    _render_cii_rating_reference()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 7 — Route Compliance Scoring
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "All 17 Routes — Sanctions Compliance Scoring",
        (
            "Routes ranked by compliance risk score (0-100). "
            "'Do Not Use' = CRITICAL sanctions exposure requiring legal clearance. "
            "'Enhanced' = legal review recommended. 'Standard Enhanced' = compliance checklist."
        ),
    )
    _render_route_scoring_table()

    # ── CSV export ─────────────────────────────────────────────────────────
    st.markdown("<div style=\"height:14px\"></div>", unsafe_allow_html=True)
    _section_title(
        "Export Compliance Data",
        "Download all-route compliance scores, risk levels, and due diligence costs as CSV.",
    )
    csv_data = _build_compliance_csv()
    st.download_button(
        label="Download Compliance CSV",
        data=csv_data,
        file_name="route_compliance_scores.csv",
        mime="text/csv",
        key="compliance_download_csv",
    )
