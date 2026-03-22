"""Live Vessel Tracking Map tab.

Sections
--------
A. Global Vessel Traffic Map   — Scattergeo globe with ports, vessels, shipping lanes
B. Port Vessel Table           — Port selector + sortable vessel list within 50 nm
C. Fleet Composition Donut     — Container/bulk/tanker/other breakdown for selected port
D. Live Metrics Strip          — Total tracked, avg speed, busiest port, container %
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_BG, C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_MOD, C_LOW, C_ACCENT,
    _hex_to_rgba, section_header, dark_layout,
)

# ── Colours ───────────────────────────────────────────────────────────────────

_C_BLUE    = "#3b82f6"
_C_ORANGE  = "#f97316"
_C_RED     = "#ef4444"
_C_AMBER   = "#f59e0b"
_C_GREEN   = "#10b981"
_C_PURPLE  = "#8b5cf6"
_C_CYAN    = "#06b6d4"
_C_GRAY    = "#475569"
_C_INDIGO  = "#6366f1"
_C_BG_SURF = "#111827"

_TYPE_COLOR: dict[str, str] = {
    "Container":   _C_BLUE,
    "Cargo":       _C_INDIGO,
    "Bulk Carrier": _C_ORANGE,
    "Tanker":      _C_RED,
    "LNG":         _C_AMBER,
    "Passenger":   _C_GREEN,
    "Fishing":     _C_PURPLE,
    "Other":       _C_GRAY,
    "Unknown":     _C_GRAY,
}

# ── Major shipping lane endpoints (for lane overlay lines) ────────────────────

_MAJOR_ROUTES: list[tuple[tuple[float, float], tuple[float, float], str]] = [
    # (start_lat, start_lon), (end_lat, end_lon), label
    ((31.23, 121.47), (33.74, -118.27), "Trans-Pacific"),
    ((31.23, 121.47), (51.92, 4.48),   "Asia-Europe"),
    ((51.92, 4.48),   (40.66, -74.04), "Transatlantic"),
    ((1.29, 103.85),  (33.74, -118.27), "SE Asia-US"),
    ((31.23, 121.47), (1.29, 103.85),  "Intra-Asia"),
    ((24.99, 55.06),  (51.92, 4.48),   "ME-Europe"),
    ((24.99, 55.06),  (31.23, 121.47), "ME-Asia"),
    ((51.92, 4.48),   (-23.95, -46.33), "Europe-South America"),
    ((31.23, 121.47), (-23.95, -46.33), "Asia-South America"),
    ((35.89, -5.50),  (51.92, 4.48),   "N Africa-Europe"),
    ((37.94, 23.64),  (31.23, 121.47), "Med-Asia"),
    ((35.10, 129.04), (31.23, 121.47), "Intra-Asia NE"),
    # Suez Canal corridor approximation
    ((29.97, 32.57),  (24.99, 55.06),  "Red Sea-Gulf"),
    ((29.97, 32.57),  (37.94, 23.64),  "Suez-Med"),
]

# ── Port metadata table ───────────────────────────────────────────────────────

_PORTS: list[dict] = [
    {"locode": "USLAX", "name": "Los Angeles",     "lat": 33.74,  "lon": -118.27, "region": "North America West"},
    {"locode": "CNSHA", "name": "Shanghai",         "lat": 31.23,  "lon": 121.47,  "region": "Asia East"},
    {"locode": "NLRTM", "name": "Rotterdam",        "lat": 51.92,  "lon": 4.48,    "region": "Europe"},
    {"locode": "SGSIN", "name": "Singapore",        "lat": 1.29,   "lon": 103.85,  "region": "Southeast Asia"},
    {"locode": "DEHAM", "name": "Hamburg",          "lat": 53.55,  "lon": 9.99,    "region": "Europe"},
    {"locode": "KRPUS", "name": "Busan",            "lat": 35.10,  "lon": 129.04,  "region": "Asia East"},
    {"locode": "JPYOK", "name": "Yokohama",         "lat": 35.44,  "lon": 139.64,  "region": "Asia East"},
    {"locode": "HKHKG", "name": "Hong Kong",        "lat": 22.29,  "lon": 114.18,  "region": "Asia East"},
    {"locode": "BEANR", "name": "Antwerp-Bruges",   "lat": 51.26,  "lon": 4.40,    "region": "Europe"},
    {"locode": "AEJEA", "name": "Jebel Ali",        "lat": 24.99,  "lon": 55.06,   "region": "Middle East"},
    {"locode": "USNYC", "name": "New York/NJ",      "lat": 40.66,  "lon": -74.04,  "region": "North America East"},
    {"locode": "CNNBO", "name": "Ningbo-Zhoushan",  "lat": 29.87,  "lon": 121.55,  "region": "Asia East"},
    {"locode": "USLGB", "name": "Long Beach",       "lat": 33.76,  "lon": -118.19, "region": "North America West"},
    {"locode": "MATNM", "name": "Tanger Med",       "lat": 35.89,  "lon": -5.50,   "region": "Africa"},
    {"locode": "LKCMB", "name": "Colombo",          "lat": 6.93,   "lon": 79.84,   "region": "South Asia"},
    {"locode": "GRPIR", "name": "Piraeus",          "lat": 37.94,  "lon": 23.64,   "region": "Europe"},
    {"locode": "USSAV", "name": "Savannah",         "lat": 32.08,  "lon": -81.10,  "region": "North America East"},
    {"locode": "GBFXT", "name": "Felixstowe",       "lat": 51.96,  "lon": 1.35,    "region": "Europe"},
    {"locode": "BRSAO", "name": "Santos",           "lat": -23.95, "lon": -46.33,  "region": "South America"},
    {"locode": "TWKHH", "name": "Kaohsiung",        "lat": 22.61,  "lon": 120.29,  "region": "Asia East"},
    {"locode": "CNSZN", "name": "Shenzhen",         "lat": 22.54,  "lon": 113.94,  "region": "Asia East"},
    {"locode": "CNTAO", "name": "Qingdao",          "lat": 36.07,  "lon": 120.33,  "region": "Asia East"},
    {"locode": "MYPKG", "name": "Port Klang",       "lat": 3.00,   "lon": 101.39,  "region": "Southeast Asia"},
    {"locode": "MYTPP", "name": "Tanjung Pelepas",  "lat": 1.36,   "lon": 103.55,  "region": "Southeast Asia"},
    {"locode": "CNTXG", "name": "Tianjin",          "lat": 38.99,  "lon": 117.72,  "region": "Asia East"},
]

_REGION_COLORS: dict[str, str] = {
    "Asia East":          _C_BLUE,
    "Southeast Asia":     _C_CYAN,
    "Europe":             _C_PURPLE,
    "North America West": _C_GREEN,
    "North America East": "#34d399",
    "Middle East":        _C_AMBER,
    "Africa":             _C_ORANGE,
    "South Asia":         "#84cc16",
    "South America":      "#f43f5e",
}


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _load_all_vessels() -> dict[str, list[dict]]:
    """Load vessel lists for all ports; returns {locode: [vessel, ...]}."""
    try:
        from data.aisstream_feed import fetch_all_port_vessels
        return fetch_all_port_vessels(_PORTS)
    except Exception as exc:
        logger.warning(f"Vessel data load error: {exc}")
        return {}


def _flatten_vessels(vessel_map: dict[str, list[dict]]) -> list[dict]:
    """Flatten {locode: [vessel]} → [vessel] with port_locode injected."""
    result = []
    for locode, vessels in vessel_map.items():
        for v in vessels:
            v2 = dict(v)
            v2["port_locode"] = locode
            result.append(v2)
    return result


# ── Demand score helper (from port_results if available) ──────────────────────

def _port_demand_scores(port_results: Any) -> dict[str, float]:
    """Extract {locode: demand_score} from port_results (list of PortResult objects)."""
    scores: dict[str, float] = {}
    if not port_results:
        return scores
    try:
        for p in port_results:
            locode = getattr(p, "locode", None) or getattr(p, "port_locode", None)
            score = getattr(p, "demand_score", 0.5)
            if locode:
                scores[str(locode)] = float(score)
    except Exception:
        pass
    return scores


# ── Section A: Global vessel map ──────────────────────────────────────────────

def _render_global_map(
    vessel_map: dict[str, list[dict]],
    demand_scores: dict[str, float],
) -> None:
    section_header("Global Vessel Traffic Map", "Live AIS positions · shipping lanes · port demand")

    all_vessels = _flatten_vessels(vessel_map)

    fig = go.Figure()

    # ─ Shipping lane overlays ─
    for (s_lat, s_lon), (e_lat, e_lon), label in _MAJOR_ROUTES:
        fig.add_trace(go.Scattergeo(
            lat=[s_lat, e_lat],
            lon=[s_lon, e_lon],
            mode="lines",
            line=dict(width=0.8, color="rgba(100,160,255,0.18)", dash="dot"),
            showlegend=False,
            hoverinfo="skip",
            name=label,
        ))

    # ─ Port markers sized by demand score ─
    port_lats = [p["lat"] for p in _PORTS]
    port_lons = [p["lon"] for p in _PORTS]
    port_names = [p["name"] for p in _PORTS]
    port_locodes = [p["locode"] for p in _PORTS]
    port_regions = [p["region"] for p in _PORTS]
    port_colors = [_REGION_COLORS.get(r, _C_GRAY) for r in port_regions]
    port_counts = [len(vessel_map.get(lc, [])) for lc in port_locodes]
    port_demand = [demand_scores.get(lc, 0.5) for lc in port_locodes]
    # Size: 8–22 px, proportional to vessel count
    max_count = max(port_counts) or 1
    port_sizes = [8 + 14 * (c / max_count) for c in port_counts]
    port_hover = [
        f"<b>{name}</b><br>{lc}<br>Vessels tracked: {cnt}<br>Demand score: {ds:.0%}"
        for name, lc, cnt, ds in zip(port_names, port_locodes, port_counts, port_demand)
    ]

    fig.add_trace(go.Scattergeo(
        lat=port_lats,
        lon=port_lons,
        mode="markers+text",
        marker=dict(
            size=port_sizes,
            color=port_colors,
            symbol="circle",
            opacity=0.9,
            line=dict(width=1, color="rgba(255,255,255,0.3)"),
        ),
        text=port_names,
        textposition="top center",
        textfont=dict(color="#94a3b8", size=9),
        name="Ports",
        hovertext=port_hover,
        hoverinfo="text",
    ))

    # ─ Vessel markers ─
    if all_vessels:
        v_lats   = [v["lat"] for v in all_vessels]
        v_lons   = [v["lon"] for v in all_vessels]
        v_colors = [_TYPE_COLOR.get(v.get("vessel_type", "Unknown"), _C_GRAY) for v in all_vessels]
        v_custom = [
            [
                v.get("name", "UNKNOWN"),
                v.get("vessel_type", "Unknown"),
                v.get("speed_kts", 0),
                v.get("destination", "—"),
                v.get("flag", "—"),
                v.get("length_m", "—"),
            ]
            for v in all_vessels
        ]

        fig.add_trace(go.Scattergeo(
            lat=v_lats,
            lon=v_lons,
            mode="markers",
            marker=dict(
                size=5,
                color=v_colors,
                symbol="triangle-up",
                opacity=0.85,
                line=dict(width=0.5, color="rgba(255,255,255,0.15)"),
            ),
            customdata=v_custom,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Type: %{customdata[1]}<br>"
                "Speed: %{customdata[2]} kts<br>"
                "Dest: %{customdata[3]}<br>"
                "Flag: %{customdata[4]}<br>"
                "Length: %{customdata[5]} m"
                "<extra></extra>"
            ),
            name="Vessels",
        ))

    # ─ Legend entries for vessel types ─
    for vtype, color in list(_TYPE_COLOR.items())[:6]:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=8, color=color, symbol="triangle-up"),
            name=vtype,
            showlegend=True,
        ))

    fig.update_layout(
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(100,130,180,0.4)",
            showland=True,
            landcolor="rgba(18,26,46,1)",
            showocean=True,
            oceancolor="rgba(8,16,36,1)",
            showlakes=False,
            showrivers=False,
            showcountries=True,
            countrycolor="rgba(60,80,120,0.3)",
            projection_type="natural earth",
            bgcolor="rgba(10,15,26,1)",
        ),
        paper_bgcolor="rgba(10,15,26,1)",
        plot_bgcolor="rgba(10,15,26,1)",
        height=620,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(17,24,39,0.85)",
            bordercolor="rgba(255,255,255,0.08)",
            borderwidth=1,
            font=dict(color="#94a3b8", size=11),
            x=0.01, y=0.01,
            xanchor="left", yanchor="bottom",
        ),
    )

    try:
        src_label = "AISstream.io" if any(
            v.get("source") == "aisstream" for v in all_vessels
        ) else "Synthetic (configure AISSTREAM_KEY for live data)"
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True}, key="vessel_map_global")
        st.caption(f"Data source: {src_label} · {len(all_vessels)} vessels tracked across {len(_PORTS)} ports")
    except Exception as e:
        st.error(f"Map render error: {e}")


# ── Section B: Port vessel table ──────────────────────────────────────────────

def _render_port_vessel_table(vessel_map: dict[str, list[dict]]) -> str:
    """Render port dropdown + vessel list. Returns the selected locode."""
    section_header("Port Vessel List", "Vessels within 50 nm of selected port")

    port_options = {p["name"]: p["locode"] for p in _PORTS if p["locode"] in vessel_map}

    if not port_options:
        st.info("No vessel data available for any port.")
        return list(vessel_map.keys())[0] if vessel_map else ""

    selected_name = st.selectbox(
        "Select port",
        options=list(port_options.keys()),
        key="vessel_map_port_select",
    )
    selected_locode = port_options.get(selected_name, "")
    vessels = vessel_map.get(selected_locode, [])

    if not vessels:
        st.info(f"No vessel data for {selected_name}.")
        return selected_locode

    try:
        rows = []
        for v in vessels:
            rows.append({
                "Vessel":       v.get("name", "—"),
                "Type":         v.get("vessel_type", "—"),
                "Flag":         v.get("flag", "—"),
                "Speed (kts)":  v.get("speed_kts", 0),
                "Heading (°)":  v.get("heading", "—"),
                "Destination":  v.get("destination", "—"),
                "ETA":          v.get("eta", "—"),
                "Length (m)":   v.get("length_m", "—"),
            })

        df = pd.DataFrame(rows)

        sort_col = st.selectbox(
            "Sort by",
            ["Speed (kts)", "Vessel", "Type", "Length (m)"],
            key="vessel_map_sort",
        )
        try:
            df = df.sort_values(sort_col, ascending=(sort_col == "Vessel"))
        except Exception:
            pass

        # Colour-code the Type column via background_gradient equivalent using style
        def _type_style(val: str) -> str:
            color = _TYPE_COLOR.get(val, _C_GRAY)
            return f"color: {color}; font-weight: 600;"

        styled = (
            df.reset_index(drop=True)
            .style
            .map(_type_style, subset=["Type"])
            .format({"Speed (kts)": "{:.1f}"}, na_rep="—")
        )
        st.dataframe(styled, use_container_width=True, hide_index=True, key="vessel_map_table")
        st.caption(f"{len(vessels)} vessels tracked near {selected_name}")
    except Exception as e:
        st.error(f"Vessel table error: {e}")

    return selected_locode


# ── Section C: Fleet composition donut ───────────────────────────────────────

def _render_fleet_donut(vessel_map: dict[str, list[dict]], selected_locode: str) -> None:
    section_header("Fleet Composition", "Vessel type breakdown for selected port")

    vessels = vessel_map.get(selected_locode, [])
    if not vessels:
        st.info("No vessel data to chart.")
        return

    try:
        from collections import Counter
        type_counts = Counter(v.get("vessel_type", "Unknown") for v in vessels)

        # Merge rare types into "Other"
        labels, values, colors = [], [], []
        for vtype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            labels.append(vtype)
            values.append(count)
            colors.append(_TYPE_COLOR.get(vtype, _C_GRAY))

        fig = go.Figure(go.Pie(
            labels=labels,
            values=values,
            hole=0.62,
            marker=dict(
                colors=colors,
                line=dict(color="rgba(10,15,26,1)", width=2),
            ),
            textinfo="label+percent",
            textfont=dict(color="#f1f5f9", size=11),
            hovertemplate="<b>%{label}</b><br>%{value} vessels (%{percent})<extra></extra>",
        ))

        port_name = next((p["name"] for p in _PORTS if p["locode"] == selected_locode), selected_locode)
        total = sum(values)
        fig.update_layout(
            **dark_layout(title="", height=320),
            annotations=[dict(
                text=f"<b>{total}</b><br><span style='font-size:10px'>vessels</span>",
                x=0.5, y=0.5,
                font=dict(size=20, color="#f1f5f9"),
                showarrow=False,
            )],
            showlegend=True,
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                font=dict(color="#94a3b8", size=11),
                orientation="v",
                x=1.02, y=0.5,
            ),
            margin=dict(l=10, r=120, t=20, b=10),
        )

        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="vessel_map_donut")
    except Exception as e:
        st.error(f"Fleet composition chart error: {e}")


# ── Section D: Live metrics strip ─────────────────────────────────────────────

def _render_metrics_strip(vessel_map: dict[str, list[dict]]) -> None:
    all_vessels = _flatten_vessels(vessel_map)
    total = len(all_vessels)

    try:
        speeds = [v["speed_kts"] for v in all_vessels if v.get("speed_kts", 0) > 0.5]
        avg_speed = sum(speeds) / len(speeds) if speeds else 0.0
    except Exception:
        avg_speed = 0.0

    try:
        busiest_locode = max(vessel_map, key=lambda lc: len(vessel_map[lc])) if vessel_map else "—"
        busiest_name = next((p["name"] for p in _PORTS if p["locode"] == busiest_locode), busiest_locode)
        busiest_count = len(vessel_map.get(busiest_locode, []))
    except Exception:
        busiest_name = "—"
        busiest_count = 0

    try:
        container_types = {"Container", "Cargo"}
        container_n = sum(
            1 for v in all_vessels if v.get("vessel_type") in container_types
        )
        container_pct = (container_n / total * 100) if total else 0.0
    except Exception:
        container_pct = 0.0

    try:
        from data.aisstream_feed import aisstream_available
        live_label = "Live AIS" if aisstream_available() else "Synthetic"
    except Exception:
        live_label = "Synthetic"

    c1, c2, c3, c4, c5 = st.columns(5)
    metric_style = (
        "background:#1a2235;border:1px solid rgba(255,255,255,0.07);border-radius:10px;"
        "padding:14px 16px;text-align:center;"
    )

    def _metric_card(col, value: str, label: str, color: str = _C_BLUE) -> None:
        col.markdown(
            f'<div style="{metric_style}">'
            f'<div style="font-size:1.5rem;font-weight:800;color:{color};line-height:1">{value}</div>'
            f'<div style="font-size:0.68rem;color:#64748b;text-transform:uppercase;'
            f'letter-spacing:0.08em;margin-top:4px">{label}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

    _metric_card(c1, str(total), "Vessels Tracked", _C_BLUE)
    _metric_card(c2, f"{avg_speed:.1f} kts", "Avg Speed", _C_GREEN)
    _metric_card(c3, busiest_name, f"Busiest Port ({busiest_count})", _C_AMBER)
    _metric_card(c4, f"{container_pct:.0f}%", "Container/Cargo Share", _C_INDIGO)
    _metric_card(c5, live_label, "Data Source", _C_CYAN if live_label == "Live AIS" else _C_GRAY)


# ── Public render entry-point ─────────────────────────────────────────────────

def render(port_results: Any, route_results: Any, freight_data: Any) -> None:
    """Render the full Live Vessel Tracking Map tab."""

    # ── Load vessel data ──────────────────────────────────────────────────────
    vessel_map: dict[str, list[dict]] = {}
    try:
        vessel_map = _load_all_vessels()
    except Exception as exc:
        st.warning(f"Could not load vessel data: {exc}")

    if not vessel_map:
        st.info(
            "No vessel data loaded. "
            "Set the **AISSTREAM_KEY** secret for live AIS data, "
            "or the synthetic fallback will populate shortly."
        )
        try:
            # Force synthetic load
            from data.aisstream_feed import fetch_all_port_vessels
            vessel_map = fetch_all_port_vessels(_PORTS)
        except Exception as exc2:
            st.error(f"Synthetic vessel generation failed: {exc2}")
            return

    demand_scores = _port_demand_scores(port_results)

    # ── D. Metrics strip (top) ────────────────────────────────────────────────
    try:
        _render_metrics_strip(vessel_map)
    except Exception as e:
        st.error(f"Metrics strip error: {e}")

    st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)

    # ── A. Global map ─────────────────────────────────────────────────────────
    try:
        _render_global_map(vessel_map, demand_scores)
    except Exception as e:
        st.error(f"Global map error: {e}")

    st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)

    # ── B + C. Port table and donut side by side ──────────────────────────────
    col_left, col_right = st.columns([3, 2], gap="large")

    with col_left:
        selected_locode = ""
        try:
            selected_locode = _render_port_vessel_table(vessel_map)
        except Exception as e:
            st.error(f"Port vessel table error: {e}")

    with col_right:
        try:
            _render_fleet_donut(vessel_map, selected_locode or list(vessel_map.keys())[0])
        except Exception as e:
            st.error(f"Fleet donut error: {e}")
