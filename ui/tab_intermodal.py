"""tab_intermodal.py — Multi-modal freight analysis tab.

Sections:
  1. Mode Comparison Tool (interactive form)
  2. Cost vs Time Scatter (bubble chart, 4 modes)
  3. Air Freight Monitor (rates, utilization, surge history)
  4. Belt and Road Analyzer (BRI corridor map + comparison)
  5. Carbon Cost of Mode Choice (stacked bar, EU ETS $80/tonne)
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.cargo_analyzer import HS_CATEGORIES
from processing.intermodal_analyzer import (
    AIR_KEY_ROUTES,
    AIR_OCEAN_RATIO_HISTORY,
    AIR_SURGE_EVENTS,
    TRANSPORT_MODES,
    BeltAndRoad,
    IntermodalComparison,
    compare_modes,
    compute_carbon_costs,
)

# ---------------------------------------------------------------------------
# Color palette (matches rest of app)
# ---------------------------------------------------------------------------
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_CARD    = "#1a2235"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"
_C_HIGH    = "#10b981"
_C_ACCENT  = "#3b82f6"
_C_WARN    = "#f59e0b"
_C_DANGER  = "#ef4444"

# Per-mode display colours
_MODE_COLORS: dict[str, str] = {
    "OCEAN":             "#3b82f6",   # blue
    "AIR":               "#ef4444",   # red
    "RAIL_CHINA_EUROPE": "#10b981",   # green
    "RAIL_US":           "#10b981",   # green (same family)
    "TRUCK_EU":          "#f59e0b",   # amber
}

_MODE_LABELS: dict[str, str] = {
    "OCEAN":             "Ocean",
    "AIR":               "Air",
    "RAIL_CHINA_EUROPE": "BRI Rail",
    "RAIL_US":           "US Rail",
    "TRUCK_EU":          "EU Truck",
}

_CARGO_LABELS: dict[str, str] = {k: v["label"] for k, v in HS_CATEGORIES.items()}

_URGENCY_OPTIONS: list[str] = ["Normal", "Urgent", "Critical"]
_URGENCY_MAP: dict[str, str] = {
    "Normal": "NORMAL",
    "Urgent": "URGENT",
    "Critical": "CRITICAL",
}

_ALL_ROUTE_IDS: list[str] = [
    "transpacific_eb",
    "asia_europe",
    "transpacific_wb",
    "transatlantic",
    "sea_transpacific_eb",
    "ningbo_europe",
    "middle_east_to_europe",
    "middle_east_to_asia",
    "south_asia_to_europe",
    "intra_asia_china_sea",
    "intra_asia_china_japan",
    "china_south_america",
    "europe_south_america",
    "med_hub_to_asia",
    "north_africa_to_europe",
    "us_east_south_america",
    "longbeach_to_asia",
]

_ROUTE_LABELS_DISPLAY: dict[str, str] = {
    "transpacific_eb":       "Trans-Pacific Eastbound",
    "asia_europe":           "Asia-Europe",
    "transpacific_wb":       "Trans-Pacific Westbound",
    "transatlantic":         "Transatlantic",
    "sea_transpacific_eb":   "SE Asia Eastbound",
    "ningbo_europe":         "Ningbo-Europe via Suez",
    "middle_east_to_europe": "Middle East to Europe",
    "middle_east_to_asia":   "Middle East to Asia",
    "south_asia_to_europe":  "South Asia to Europe",
    "intra_asia_china_sea":  "Intra-Asia: China to SE Asia",
    "intra_asia_china_japan":"Intra-Asia: China to Japan/Korea",
    "china_south_america":   "China to South America",
    "europe_south_america":  "Europe to South America",
    "med_hub_to_asia":       "Mediterranean Hub to Asia",
    "north_africa_to_europe":"North Africa to Europe",
    "us_east_south_america": "US East Coast to South America",
    "longbeach_to_asia":     "Long Beach to Asia",
}

_EU_ETS_PER_TONNE: float = 80.0


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _divider(label: str) -> None:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:12px;margin:28px 0">'
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        '<span style="font-size:0.65rem;color:#475569;text-transform:uppercase;'
        'letter-spacing:0.12em">' + label + "</span>"
        '<div style="flex:1;height:1px;background:rgba(255,255,255,0.06)"></div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _badge(text: str, color: str) -> str:
    return (
        '<span style="background:' + color
        + ";color:#fff;font-size:0.68rem;font-weight:700;"
        + 'padding:2px 8px;border-radius:4px;letter-spacing:0.05em">'
        + text + "</span>"
    )


def _section_title(text: str, subtitle: str = "") -> None:
    sub = (
        '<div style="color:' + _C_TEXT2 + ';font-size:0.83rem;margin-top:3px">'
        + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        '<div style="margin-bottom:14px;margin-top:8px">'
        '<div style="font-size:1.05rem;font-weight:700;color:' + _C_TEXT + '">'
        + text + "</div>"
        + sub + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 1 — Mode Comparison Tool
# ---------------------------------------------------------------------------

def _render_mode_comparison(route_results: list) -> None:
    _divider("MODE COMPARISON TOOL")
    _section_title(
        "Intermodal Mode Selector",
        "Compare ocean, air, rail, and truck for your specific cargo and route.",
    )

    cargo_keys   = list(HS_CATEGORIES.keys())
    cargo_display = [_CARGO_LABELS.get(k, k.title()) for k in cargo_keys]

    route_ids    = _ALL_ROUTE_IDS
    route_display = [_ROUTE_LABELS_DISPLAY.get(r, r) for r in route_ids]

    # Try to pick a sensible default route from route_results
    default_route_idx = 0
    if route_results:
        try:
            first_id = getattr(route_results[0], "route_id", None)
            if first_id in route_ids:
                default_route_idx = route_ids.index(first_id)
        except (AttributeError, IndexError):
            pass

    col1, col2, col3, col4 = st.columns([1.4, 1, 1, 1.6])
    with col1:
        sel_cargo_display = st.selectbox(
            "Cargo Category",
            options=cargo_display,
            index=0,
            key="im_cargo_cat",
        )
        sel_cargo = cargo_keys[cargo_display.index(sel_cargo_display)]

    with col2:
        weight_kg = st.number_input(
            "Weight (kg)",
            min_value=1.0,
            max_value=500_000.0,
            value=1_000.0,
            step=100.0,
            key="im_weight",
        )

    with col3:
        urgency_label = st.selectbox(
            "Urgency",
            options=_URGENCY_OPTIONS,
            index=0,
            key="im_urgency",
        )
        urgency = _URGENCY_MAP[urgency_label]

    with col4:
        sel_route_display = st.selectbox(
            "Route",
            options=route_display,
            index=default_route_idx,
            key="im_route",
        )
        sel_route = route_ids[route_display.index(sel_route_display)]

    result: IntermodalComparison = compare_modes(sel_cargo, weight_kg, sel_route, urgency)
    logger.debug("im comparison computed: recommended={}", result.recommended_mode)

    # --- Recommendation card ---
    rec_color = _MODE_COLORS.get(result.recommended_mode, _C_ACCENT)
    rec_label = _MODE_LABELS.get(result.recommended_mode, result.recommended_mode)

    st.markdown(
        '<div style="background:' + _C_CARD
        + ";border:1px solid " + rec_color
        + ";border-radius:12px;padding:16px 20px;margin:16px 0\">"
        + '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">'
        + '<div style="font-size:1.05rem;font-weight:700;color:' + _C_TEXT
        + '">Recommendation</div>'
        + _badge("RECOMMENDED: " + rec_label.upper(), rec_color)
        + "</div>"
        + '<div style="font-size:0.82rem;color:' + _C_TEXT2 + ';line-height:1.6">'
        + result.recommendation_rationale
        + "</div>"
        + "</div>",
        unsafe_allow_html=True,
    )

    # --- Side-by-side comparison table ---
    headers = ["Mode", "Total Cost", "Cost/kg", "Transit (days)", "CO2 (kg)", "Reliability"]

    def _row(mode_key: str, cost_usd: float, days: float) -> list[str]:
        mode = TRANSPORT_MODES[mode_key]
        is_rec = mode_key == result.recommended_mode
        co2 = mode.co2_kg_per_kg_cargo * weight_kg
        label = _MODE_LABELS.get(mode_key, mode_key)
        marker = " *" if is_rec else ""
        return [
            label + marker,
            "$" + "{:,.0f}".format(cost_usd),
            "$" + str(round(cost_usd / weight_kg, 3)) + "/kg",
            str(round(days, 1)),
            str(round(co2, 1)) + " kg",
            str(mode.reliability_pct) + "%",
        ]

    rows: list[list[str]] = []
    rows.append(_row("OCEAN", result.ocean_cost, result.ocean_days))
    rows.append(_row("AIR",   result.air_cost,   result.air_days))
    if result.rail_cost is not None and result.rail_days is not None:
        # Determine which rail mode is available
        from processing.intermodal_analyzer import _ROUTE_INTERMODAL
        ri = _ROUTE_INTERMODAL.get(sel_route)
        rail_key = "RAIL_CHINA_EUROPE"
        if ri and "RAIL_US" in ri[2]:
            rail_key = "RAIL_US"
        rows.append(_row(rail_key, result.rail_cost, result.rail_days))
    if result.truck_cost is not None and result.truck_days is not None:
        rows.append(_row("TRUCK_EU", result.truck_cost, result.truck_days))

    col_w = [1.2, 1, 1, 1, 1, 1]
    hdr_cols = st.columns(col_w)
    for col, h in zip(hdr_cols, headers):
        col.markdown(
            '<div style="font-size:0.7rem;font-weight:700;color:' + _C_TEXT3
            + ';text-transform:uppercase;letter-spacing:0.08em">' + h + "</div>",
            unsafe_allow_html=True,
        )

    for row in rows:
        mode_name = row[0].replace(" *", "")
        mode_key_found = next(
            (k for k, v in _MODE_LABELS.items() if v == mode_name), None
        )
        bg = "rgba(59,130,246,0.08)" if mode_key_found == result.recommended_mode else "transparent"
        row_cols = st.columns(col_w)
        for i, (col, cell) in enumerate(zip(row_cols, row)):
            color = _MODE_COLORS.get(mode_key_found, _C_TEXT) if i == 0 else _C_TEXT
            col.markdown(
                '<div style="background:' + bg
                + ";padding:6px 0;font-size:0.82rem;font-weight:"
                + ("700" if i == 0 else "400")
                + ";color:" + color + '">' + cell + "</div>",
                unsafe_allow_html=True,
            )

    prem = result.cost_premium_air_vs_ocean_pct
    st.caption(
        "Air vs Ocean cost premium: +"
        + str(round(prem, 0)) + "% for this shipment.  "
        "* = recommended mode.  CO2 figures are per-shipment totals."
    )


# ---------------------------------------------------------------------------
# Section 2 — Cost vs Time Scatter (bubble chart)
# ---------------------------------------------------------------------------

def _render_cost_time_scatter() -> None:
    _divider("COST VS TRANSIT TIME — MODE BUBBLE CHART")
    _section_title(
        "Transport Mode Matrix",
        "Bubble size = CO2 footprint (kg CO2/kg cargo).  Hover for details.",
    )

    modes_to_plot = ["OCEAN", "AIR", "RAIL_CHINA_EUROPE", "TRUCK_EU"]
    # RAIL_US overlaps with TRUCK on the scatter; add as a secondary trace
    fig = go.Figure()

    for mode_key in modes_to_plot:
        mode = TRANSPORT_MODES[mode_key]
        color = _MODE_COLORS.get(mode_key, "#64748b")
        label = _MODE_LABELS.get(mode_key, mode_key)
        bubble_size = mode.co2_kg_per_kg_cargo * 60 + 12  # scale for visibility

        fig.add_trace(
            go.Scatter(
                x=[mode.transit_days_mid],
                y=[mode.cost_per_kg_usd],
                mode="markers+text",
                name=label,
                marker=dict(
                    size=bubble_size,
                    color=color,
                    opacity=0.80,
                    line=dict(color="rgba(255,255,255,0.3)", width=1.5),
                ),
                text=[label],
                textposition="top center",
                textfont=dict(color=_C_TEXT, size=11, family="Inter, sans-serif"),
                hovertemplate=(
                    "<b>" + label + "</b><br>"
                    "Transit: " + str(mode.transit_days_min) + "-" + str(mode.transit_days_max) + " days<br>"
                    "Cost: $" + str(mode.cost_per_kg_usd) + "/kg<br>"
                    "CO2: " + str(mode.co2_kg_per_kg_cargo) + " kg CO2/kg cargo<br>"
                    "Reliability: " + str(mode.reliability_pct) + "%<br>"
                    "Advantage: " + mode.key_advantage[:60] + "<br>"
                    "<extra></extra>"
                ),
            )
        )

    # Add RAIL_US as a fifth bubble
    rail_us = TRANSPORT_MODES["RAIL_US"]
    fig.add_trace(
        go.Scatter(
            x=[rail_us.transit_days_mid],
            y=[rail_us.cost_per_kg_usd],
            mode="markers+text",
            name="US Rail",
            marker=dict(
                size=rail_us.co2_kg_per_kg_cargo * 60 + 12,
                color="#22c55e",
                opacity=0.70,
                symbol="diamond",
                line=dict(color="rgba(255,255,255,0.3)", width=1.5),
            ),
            text=["US Rail"],
            textposition="bottom center",
            textfont=dict(color=_C_TEXT, size=11),
            hovertemplate=(
                "<b>US Rail</b><br>"
                "Transit: " + str(rail_us.transit_days_min) + "-" + str(rail_us.transit_days_max) + " days<br>"
                "Cost: $" + str(rail_us.cost_per_kg_usd) + "/kg<br>"
                "CO2: " + str(rail_us.co2_kg_per_kg_cargo) + " kg CO2/kg cargo<br>"
                "Reliability: " + str(rail_us.reliability_pct) + "%<br>"
                "<extra></extra>"
            ),
        )
    )

    # Annotation arrows to clarify axes
    fig.add_annotation(
        x=2, y=4.8,
        text="Air: fastest, most expensive, highest CO2",
        showarrow=False,
        font=dict(color=_C_DANGER, size=10),
        xanchor="left",
    )
    fig.add_annotation(
        x=22, y=0.12,
        text="Ocean: slowest, cheapest, lowest CO2",
        showarrow=False,
        font=dict(color=_C_ACCENT, size=10),
        xanchor="left",
    )

    fig.update_layout(
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=420,
        margin=dict(t=30, b=50, l=70, r=30),
        xaxis=dict(
            title="Transit Days (midpoint estimate)",
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
        ),
        yaxis=dict(
            title="Cost per kg (USD)",
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.05)",
            type="log",
            zeroline=False,
        ),
        legend=dict(
            font=dict(color=_C_TEXT2, size=10),
            bgcolor="rgba(0,0,0,0)",
        ),
        font=dict(color=_C_TEXT, family="Inter, sans-serif"),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Y-axis is log-scale.  Bubble size proportional to CO2 intensity (kg CO2/kg cargo). "
        "Ocean and US Rail have the smallest CO2 footprint; Air is ~120x more carbon-intensive than ocean."
    )


# ---------------------------------------------------------------------------
# Section 3 — Air Freight Monitor
# ---------------------------------------------------------------------------

def _render_air_freight_monitor() -> None:
    _divider("AIR FREIGHT MONITOR")
    _section_title(
        "Air Cargo Market Intelligence",
        "Key routes, utilization, rate ratio vs ocean, and historical surge events.",
    )

    # --- Key air route cards ---
    cols = st.columns(3)
    for i, route_info in enumerate(AIR_KEY_ROUTES):
        with cols[i]:
            normal  = route_info["normal_rate_usd_kg"]
            peak    = route_info["peak_rate_usd_kg"]
            util    = route_info["utilization_pct"]
            util_color = _C_WARN if util >= 85 else _C_HIGH if util >= 75 else _C_TEXT3
            st.markdown(
                '<div style="background:' + _C_CARD
                + ";border:1px solid " + _C_BORDER
                + ";border-radius:10px;padding:14px 16px;height:100%\">"
                + '<div style="font-size:0.75rem;font-weight:700;color:' + _C_DANGER
                + ';text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px">'
                + "Air Cargo Route</div>"
                + '<div style="font-size:0.82rem;font-weight:700;color:' + _C_TEXT
                + ';margin-bottom:10px">' + route_info["route"] + "</div>"
                + '<div style="display:flex;gap:12px;margin-bottom:8px">'
                + '<div><div style="font-size:0.68rem;color:' + _C_TEXT3
                + '">Normal Rate</div>'
                + '<div style="font-size:1rem;font-weight:800;color:' + _C_TEXT + '">'
                + "$" + str(normal) + "/kg</div></div>"
                + '<div><div style="font-size:0.68rem;color:' + _C_TEXT3
                + '">Peak Rate</div>'
                + '<div style="font-size:1rem;font-weight:800;color:' + _C_WARN + '">'
                + "$" + str(peak) + "/kg</div></div>"
                + '<div><div style="font-size:0.68rem;color:' + _C_TEXT3
                + '">Utilization</div>'
                + '<div style="font-size:1rem;font-weight:800;color:' + util_color + '">'
                + str(util) + "%</div></div>"
                + "</div>"
                + '<div style="font-size:0.7rem;color:' + _C_TEXT3 + '">'
                + route_info["dominant_cargo"] + "</div>"
                + "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # --- Air vs Ocean rate ratio chart ---
    years  = sorted(AIR_OCEAN_RATIO_HISTORY.keys())
    ratios = [AIR_OCEAN_RATIO_HISTORY[y] for y in years]

    fig_ratio = go.Figure()
    fig_ratio.add_trace(
        go.Scatter(
            x=years,
            y=ratios,
            mode="lines+markers",
            line=dict(color=_C_DANGER, width=2.5),
            marker=dict(size=7, color=_C_DANGER),
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.10)",
            name="Air/Ocean rate ratio",
            hovertemplate="Year: %{x}<br>Ratio: %{y:.0f}x<extra></extra>",
        )
    )

    # Annotate surge events
    for event in AIR_SURGE_EVENTS:
        yr = event["year"]
        if yr in AIR_OCEAN_RATIO_HISTORY:
            fig_ratio.add_annotation(
                x=yr,
                y=AIR_OCEAN_RATIO_HISTORY[yr],
                text=event["label"],
                showarrow=True,
                arrowhead=2,
                arrowcolor=_C_WARN,
                font=dict(color=_C_WARN, size=9),
                ax=0,
                ay=-36,
            )

    # Normal band (50-100x)
    fig_ratio.add_hrect(
        y0=50, y1=100,
        fillcolor="rgba(59,130,246,0.06)",
        line_width=0,
        annotation_text="Normal range (50-100x)",
        annotation_position="right",
        annotation_font=dict(color=_C_TEXT3, size=9),
    )

    fig_ratio.update_layout(
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=320,
        margin=dict(t=24, b=40, l=60, r=24),
        xaxis=dict(title="Year", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(
            title="Air / Ocean rate ratio (x)",
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.05)",
        ),
        legend=dict(font=dict(color=_C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)"),
        font=dict(color=_C_TEXT, family="Inter, sans-serif"),
    )
    st.plotly_chart(fig_ratio, use_container_width=True)

    # --- Surge events timeline ---
    st.markdown(
        '<div style="font-size:0.75rem;font-weight:700;color:' + _C_TEXT3
        + ';text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px">'
        + "Historical Surge Events</div>",
        unsafe_allow_html=True,
    )
    for event in AIR_SURGE_EVENTS:
        mult = event["peak_rate_multiplier"]
        mult_color = _C_DANGER if mult >= 2.0 else _C_WARN
        st.markdown(
            '<div style="background:' + _C_CARD
            + ";border-left:3px solid " + mult_color
            + ";border-radius:6px;padding:10px 14px;margin-bottom:8px;"
            + "display:flex;gap:16px;align-items:flex-start\">"
            + '<div style="min-width:40px;font-size:0.85rem;font-weight:800;color:'
            + mult_color + '">' + str(event["year"]) + "</div>"
            + "<div>"
            + '<div style="font-size:0.82rem;font-weight:700;color:' + _C_TEXT
            + ';margin-bottom:4px">' + event["label"] + "</div>"
            + '<div style="font-size:0.75rem;color:' + _C_TEXT2 + '">'
            + event["description"] + "</div>"
            + "</div>"
            + '<div style="margin-left:auto;text-align:right;white-space:nowrap">'
            + '<div style="font-size:0.68rem;color:' + _C_TEXT3 + '">Rate multiplier</div>'
            + '<div style="font-size:1rem;font-weight:800;color:' + mult_color + '">'
            + str(mult) + "x</div>"
            + "</div></div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 4 — Belt and Road Analyzer
# ---------------------------------------------------------------------------

def _render_bri_analyzer() -> None:
    _divider("BELT AND ROAD INITIATIVE (BRI) RAIL ANALYZER")
    _section_title(
        "China-Europe Rail Corridor Intelligence",
        "7 main corridors, transit times, cost vs ocean, and post-2022 political risk.",
    )

    bri = BeltAndRoad()

    # --- BRI Scattergeo map ---
    fig_map = go.Figure()

    risk_colors: dict[str, str] = {
        "LOW":      _C_HIGH,
        "MODERATE": _C_WARN,
        "HIGH":     _C_DANGER,
    }

    for corridor in bri.CORRIDORS:
        lats = corridor.lat_waypoints
        lons = corridor.lon_waypoints
        color = risk_colors.get(corridor.disruption_risk, _C_TEXT3)
        hover_txt = (
            "<b>" + corridor.name + "</b><br>"
            + corridor.origin_city + " → " + corridor.dest_city + "<br>"
            "Transit: " + str(corridor.transit_days) + " days<br>"
            "Cost vs Ocean: " + str(corridor.cost_vs_ocean_ratio) + "x<br>"
            "Risk: " + corridor.disruption_risk + "<br>"
            "<i>" + corridor.disruption_note[:80] + "...</i>"
            "<extra></extra>"
        )

        # Line trace
        fig_map.add_trace(
            go.Scattergeo(
                lon=lons,
                lat=lats,
                mode="lines",
                line=dict(width=2.5, color=color),
                name=corridor.name,
                hoverinfo="skip",
                showlegend=False,
            )
        )
        # Origin marker
        fig_map.add_trace(
            go.Scattergeo(
                lon=[lons[0]],
                lat=[lats[0]],
                mode="markers+text",
                marker=dict(size=9, color=color, symbol="circle"),
                text=[corridor.origin_city],
                textposition="top right",
                textfont=dict(color=_C_TEXT, size=8),
                name=corridor.name,
                hovertemplate=hover_txt,
                showlegend=True,
            )
        )
        # Destination marker
        fig_map.add_trace(
            go.Scattergeo(
                lon=[lons[-1]],
                lat=[lats[-1]],
                mode="markers",
                marker=dict(size=9, color=color, symbol="square"),
                name=corridor.name + " (dest)",
                hovertemplate=hover_txt,
                showlegend=False,
            )
        )

    fig_map.update_layout(
        geo=dict(
            showland=True,
            landcolor="#1a2235",
            showocean=True,
            oceancolor="#0a0f1a",
            showlakes=False,
            showcountries=True,
            countrycolor="rgba(255,255,255,0.06)",
            bgcolor=_C_BG,
            projection_type="natural earth",
            center=dict(lon=60, lat=45),
            lonaxis=dict(range=[60, 180]),
            lataxis=dict(range=[15, 65]),
        ),
        paper_bgcolor=_C_BG,
        height=380,
        margin=dict(t=10, b=10, l=0, r=0),
        legend=dict(
            font=dict(color=_C_TEXT2, size=9),
            bgcolor="rgba(0,0,0,0)",
            x=0.01,
            y=0.99,
        ),
        font=dict(color=_C_TEXT, family="Inter, sans-serif"),
    )

    # Risk legend annotation
    fig_map.add_annotation(
        x=0.01, y=0.06,
        xref="paper", yref="paper",
        text="Green = Low risk   Amber = Moderate   Red = High (Russia route disrupted post-2022)",
        showarrow=False,
        font=dict(color=_C_TEXT3, size=9),
        align="left",
    )

    st.plotly_chart(fig_map, use_container_width=True)

    # --- Corridor comparison table ---
    st.markdown(
        '<div style="font-size:0.75rem;font-weight:700;color:' + _C_TEXT3
        + ';text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px">'
        + "Corridor Detail Comparison</div>",
        unsafe_allow_html=True,
    )

    ocean_mid = bri.ocean_transit_days()

    col_hdrs = st.columns([1.4, 0.6, 0.7, 0.7, 2.0])
    for col, h in zip(col_hdrs, ["Corridor", "Days", "vs Ocean Cost", "Risk", "Disruption Note"]):
        col.markdown(
            '<div style="font-size:0.68rem;font-weight:700;color:' + _C_TEXT3
            + ';text-transform:uppercase;letter-spacing:0.06em">' + h + "</div>",
            unsafe_allow_html=True,
        )

    for corridor in bri.CORRIDORS:
        risk_color = risk_colors.get(corridor.disruption_risk, _C_TEXT3)
        days_saved = round(ocean_mid - corridor.transit_days, 0)
        days_txt   = str(corridor.transit_days) + "d"
        if days_saved > 0:
            days_txt += " (-" + str(int(days_saved)) + "d vs ocean)"

        row_cols = st.columns([1.4, 0.6, 0.7, 0.7, 2.0])
        row_cols[0].markdown(
            '<div style="font-size:0.80rem;font-weight:700;color:' + _C_TEXT
            + ';padding:6px 0">' + corridor.name + "</div>",
            unsafe_allow_html=True,
        )
        row_cols[1].markdown(
            '<div style="font-size:0.80rem;color:' + _C_TEXT + ';padding:6px 0">'
            + days_txt + "</div>",
            unsafe_allow_html=True,
        )
        row_cols[2].markdown(
            '<div style="font-size:0.80rem;color:' + _C_WARN + ';padding:6px 0">'
            + str(corridor.cost_vs_ocean_ratio) + "x ocean</div>",
            unsafe_allow_html=True,
        )
        row_cols[3].markdown(
            '<div style="padding:6px 0">'
            + _badge(corridor.disruption_risk, risk_color)
            + "</div>",
            unsafe_allow_html=True,
        )
        row_cols[4].markdown(
            '<div style="font-size:0.72rem;color:' + _C_TEXT2 + ';padding:6px 0;line-height:1.4">'
            + corridor.disruption_note[:120] + ("..." if len(corridor.disruption_note) > 120 else "")
            + "</div>",
            unsafe_allow_html=True,
        )

    # --- Market share and growth chart ---
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    years, growth_vals = bri.get_growth_series()

    fig_growth = go.Figure()
    fig_growth.add_trace(
        go.Scatter(
            x=years,
            y=growth_vals,
            mode="lines+markers",
            fill="tozeroy",
            fillcolor="rgba(16,185,129,0.10)",
            line=dict(color=_C_HIGH, width=2.5),
            marker=dict(size=7, color=_C_HIGH),
            name="BRI Volume Index (2015=100)",
            hovertemplate="Year: %{x}<br>Index: %{y:.0f}<extra></extra>",
        )
    )

    # 2022 disruption annotation
    fig_growth.add_vline(
        x=2022,
        line_width=1.5,
        line_dash="dash",
        line_color=_C_DANGER,
        annotation_text="Russia sanctions",
        annotation_position="top right",
        annotation_font=dict(color=_C_DANGER, size=9),
    )

    fig_growth.update_layout(
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=280,
        margin=dict(t=24, b=40, l=60, r=24),
        xaxis=dict(title="Year", color=_C_TEXT2, gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(
            title="Volume Index (2015 = 100)",
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.05)",
        ),
        legend=dict(font=dict(color=_C_TEXT2, size=10), bgcolor="rgba(0,0,0,0)"),
        font=dict(color=_C_TEXT, family="Inter, sans-serif"),
    )
    st.plotly_chart(fig_growth, use_container_width=True)

    # Market share callout
    st.markdown(
        '<div style="background:' + _C_CARD
        + ";border:1px solid " + _C_BORDER
        + ";border-radius:10px;padding:12px 16px;display:flex;gap:20px;"
        + "align-items:center;margin-top:4px\">"
        + '<div><div style="font-size:0.68rem;color:' + _C_TEXT3
        + '">BRI Market Share</div>'
        + '<div style="font-size:1.5rem;font-weight:800;color:' + _C_HIGH + '">'
        + str(bri.MARKET_SHARE_PCT) + "%</div>"
        + '<div style="font-size:0.7rem;color:' + _C_TEXT3
        + '">of Asia-Europe trade volume (2026)</div></div>'
        + '<div style="font-size:0.78rem;color:' + _C_TEXT2 + ';flex:1;line-height:1.5">'
        + "BRI rail holds ~5% of Asia-Europe trade by volume, up from near-zero in 2015. "
        "Rail wins when cargo is time-sensitive but too heavy for air (100 kg-5 t), "
        "or when Suez Canal disruptions add ocean delays. "
        "The Russia route suspension post-2022 forced re-routing via the Middle Corridor "
        "(Kazakhstan-Caspian ferry-Georgia/Turkey), adding cost and 5-7 transit days."
        + "</div></div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 5 — Carbon Cost of Mode Choice
# ---------------------------------------------------------------------------

def _render_carbon_cost(freight_data: dict) -> None:
    _divider("CARBON COST OF MODE CHOICE — EU ETS @ $80/TONNE CO2")
    _section_title(
        "Total Cost Including Carbon Offset",
        "Stacked bar: freight cost + carbon offset cost (EU ETS $80/tonne) per mode.",
    )

    # Weight selector for illustrative comparison
    weight_kg = st.slider(
        "Shipment weight for carbon comparison (kg)",
        min_value=100,
        max_value=50_000,
        value=5_000,
        step=100,
        key="im_carbon_weight",
    )

    carbon_data = compute_carbon_costs(float(weight_kg))

    mode_order  = ["OCEAN", "AIR", "RAIL_CHINA_EUROPE", "TRUCK_EU", "RAIL_US"]
    mode_keys   = [m for m in mode_order if m in carbon_data]
    mode_names  = [_MODE_LABELS.get(m, m) for m in mode_keys]
    freight_vals  = [carbon_data[m]["freight_cost_usd"] for m in mode_keys]
    carbon_vals   = [carbon_data[m]["carbon_offset_usd"] for m in mode_keys]
    co2_kg_vals   = [carbon_data[m]["co2_kg"] for m in mode_keys]
    total_vals    = [carbon_data[m]["total_cost_usd"] for m in mode_keys]
    bar_colors    = [_MODE_COLORS.get(m, "#64748b") for m in mode_keys]

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            name="Freight Cost",
            x=mode_names,
            y=freight_vals,
            marker_color=bar_colors,
            marker_opacity=0.85,
            hovertemplate=(
                "<b>%{x} — Freight</b><br>"
                "Freight: $%{y:,.2f}<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Bar(
            name="Carbon Offset Cost (EU ETS $80/t)",
            x=mode_names,
            y=carbon_vals,
            marker_color="rgba(239,68,68,0.55)",
            marker_line=dict(color="rgba(239,68,68,0.8)", width=1),
            hovertemplate=(
                "<b>%{x} — Carbon</b><br>"
                "Carbon offset: $%{y:,.2f}<extra></extra>"
            ),
        )
    )

    # Total cost annotations above each bar
    for i, (name, total, co2) in enumerate(zip(mode_names, total_vals, co2_kg_vals)):
        fig.add_annotation(
            x=name,
            y=total,
            text="$" + "{:,.0f}".format(total) + "<br>" + str(round(co2, 0)) + " kg CO2",
            showarrow=False,
            yshift=10,
            font=dict(color=_C_TEXT, size=9),
        )

    fig.update_layout(
        barmode="stack",
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_SURFACE,
        height=400,
        margin=dict(t=40, b=40, l=70, r=24),
        xaxis=dict(color=_C_TEXT2, gridcolor="rgba(0,0,0,0)"),
        yaxis=dict(
            title="Cost (USD)",
            color=_C_TEXT2,
            gridcolor="rgba(255,255,255,0.05)",
            type="log",
        ),
        legend=dict(
            font=dict(color=_C_TEXT2, size=10),
            bgcolor="rgba(0,0,0,0)",
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        font=dict(color=_C_TEXT, family="Inter, sans-serif"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Summary table
    col_w = [1.2, 1, 1, 1, 1]
    hdr_cols = st.columns(col_w)
    for col, h in zip(hdr_cols, ["Mode", "Freight Cost", "Carbon Offset", "Total Cost", "CO2 (kg)"]):
        col.markdown(
            '<div style="font-size:0.68rem;font-weight:700;color:' + _C_TEXT3
            + ';text-transform:uppercase;letter-spacing:0.06em">' + h + "</div>",
            unsafe_allow_html=True,
        )

    for mk, name, fc, cc, tot, co2 in zip(
        mode_keys, mode_names, freight_vals, carbon_vals, total_vals, co2_kg_vals
    ):
        color = _MODE_COLORS.get(mk, _C_TEXT)
        rc = st.columns(col_w)
        rc[0].markdown(
            '<div style="font-size:0.82rem;font-weight:700;color:' + color
            + ';padding:5px 0">' + name + "</div>",
            unsafe_allow_html=True,
        )
        rc[1].markdown(
            '<div style="font-size:0.82rem;color:' + _C_TEXT + ';padding:5px 0">'
            + "$" + "{:,.0f}".format(fc) + "</div>",
            unsafe_allow_html=True,
        )
        rc[2].markdown(
            '<div style="font-size:0.82rem;color:' + _C_DANGER + ';padding:5px 0">'
            + "$" + "{:,.0f}".format(cc) + "</div>",
            unsafe_allow_html=True,
        )
        rc[3].markdown(
            '<div style="font-size:0.82rem;font-weight:700;color:' + _C_TEXT + ';padding:5px 0">'
            + "$" + "{:,.0f}".format(tot) + "</div>",
            unsafe_allow_html=True,
        )
        rc[4].markdown(
            '<div style="font-size:0.82rem;color:' + _C_TEXT2 + ';padding:5px 0">'
            + str(round(co2, 1)) + " kg</div>",
            unsafe_allow_html=True,
        )

    st.caption(
        "Carbon offset cost uses EU ETS price of $80/tonne CO2.  "
        "Air freight carries a carbon cost that exceeds its freight cost for most shipments.  "
        "Y-axis is log-scale to show ocean/rail values alongside air."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(route_results: list, freight_data: dict, macro_data: dict) -> None:
    """Render the Intermodal Analysis tab.

    Parameters
    ----------
    route_results:
        List of route objects (RouteOpportunity or similar) with at least
        ``route_id`` attribute.  Used to pre-select default route in form.
    freight_data:
        Freight rate data dict (may be empty).  Passed through for future
        rate-calibration enrichment.
    macro_data:
        Macro data dict (may be empty).  Reserved for elasticity adjustment.
    """
    logger.info("tab_intermodal: render() called")

    st.markdown(
        '<h2 style="font-size:1.4rem;font-weight:800;color:' + _C_TEXT
        + ';margin-bottom:4px">Intermodal Freight Analysis</h2>'
        + '<p style="font-size:0.82rem;color:' + _C_TEXT2
        + ';margin-bottom:0">Compare ocean, air, Belt and Road rail, and trucking '
        "across cargo types, routes, and urgency levels.  Includes carbon offset costs "
        "at EU ETS $80/tonne.</p>",
        unsafe_allow_html=True,
    )

    _render_mode_comparison(route_results)
    _render_cost_time_scatter()
    _render_air_freight_monitor()
    _render_bri_analyzer()
    _render_carbon_cost(freight_data)

    logger.info("tab_intermodal: render() complete")


# ---------------------------------------------------------------------------
# Integration note (for app.py maintainer)
# ---------------------------------------------------------------------------
# Add inside the tab block:
#
#   from ui import tab_intermodal
#   with tab_intermodal_ui:
#       tab_intermodal.render(route_results, freight_data, macro_data)
#
# Signature matches the pattern used by tab_cargo, tab_trade_war, etc.
