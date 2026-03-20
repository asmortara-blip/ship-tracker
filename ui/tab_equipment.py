"""tab_equipment.py — Container Equipment Availability tab.

Renders a comprehensive view of container equipment status, trade imbalances,
reefer market dynamics, and equipment cost calculations for cargo shipping.

Container equipment availability is a major driver of shipping costs and
delays — this tab makes those dynamics visible and quantifiable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.equipment_tracker import (
    CONTAINER_TYPES,
    REGIONS,
    REGIONAL_EQUIPMENT_STATUS,
    TRADE_IMBALANCE_DATA,
    EquipmentStatus,
    compute_equipment_adjusted_rate,
    get_global_equipment_index,
    get_reefer_summary,
    get_trade_imbalance,
)
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    RISK_COLORS,
    dark_layout,
    section_header,
)

# ── Local palette ─────────────────────────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_GREEN   = "#10b981"
_C_RED     = "#ef4444"
_C_AMBER   = "#f59e0b"
_C_BLUE    = "#3b82f6"
_C_PURPLE  = "#8b5cf6"
_C_CYAN    = "#06b6d4"
_C_TEAL    = "#14b8a6"

# Heatmap color scale: green (surplus) → amber → red (critical)
_UTIL_COLORSCALE = [
    [0.00, "#064e3b"],
    [0.50, "#10b981"],
    [0.65, "#f59e0b"],
    [0.80, "#ef4444"],
    [1.00, "#7f1d1d"],
]

# Shortage risk → display color
_RISK_COLOR: Dict[str, str] = {
    "CRITICAL": "#b91c1c",
    "HIGH":     "#ef4444",
    "MODERATE": "#f59e0b",
    "LOW":      "#10b981",
}

# Container type display labels (no slash — avoids backslash in f-string)
_TYPE_LABELS: Dict[str, str] = {
    "20FT_DRY":    "20ft Dry",
    "40FT_DRY":    "40ft Dry",
    "40FT_HC":     "40ft HC",
    "40FT_REEFER": "40ft Reefer",
    "20FT_TANK":   "20ft Tank",
}

# Historical equipment balance index (0 = severe shortage, 100 = large surplus)
# Constructed from industry reports: Container xChange ECI, Drewry, Alphaliner.
_BALANCE_TIMELINE: Dict[str, Dict[str, List]] = {
    "years": [2020, 2021, 2022, 2023, 2024, 2025, 2026],
    "Asia Pacific": [65, 28, 42, 58, 72, 75, 76],
    "North America": [60, 22, 35, 48, 55, 52, 50],
    "Europe": [62, 25, 38, 52, 60, 62, 63],
    "South America": [58, 20, 32, 45, 52, 54, 55],
    "Middle East": [62, 30, 44, 56, 64, 66, 67],
    "Africa": [55, 18, 30, 42, 50, 52, 53],
}

# Reefer seasonal demand index (1–12 months; 100 = average; >100 = above avg demand)
_REEFER_SEASONAL: Dict[str, List[float]] = {
    "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
    "labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    # Global composite
    "Global":       [92, 88, 95, 105, 118, 120, 115, 110, 108, 102, 95, 90],
    # Driven by southern hemisphere fruit harvest (Chile, SA) + citrus
    "South America": [130, 125, 120, 110, 95, 85, 80, 85, 95, 110, 125, 135],
    # European stone fruit, berry exports; pharma stable year-round
    "Europe":       [85, 82, 90, 102, 118, 125, 130, 128, 112, 95, 85, 82],
    # Asian aquaculture, fruit; demand peaks around CNY
    "Asia Pacific": [88, 110, 95, 92, 95, 100, 108, 115, 118, 112, 100, 92],
    # North American holiday season + fresh produce
    "North America": [88, 85, 90, 95, 102, 105, 108, 118, 125, 130, 115, 95],
}

# Top reefer commodity details
_REEFER_COMMODITIES: List[Dict[str, Any]] = [
    {
        "name": "Bananas",
        "share_pct": 22,
        "peak_months": "Year-round (Oct–Mar peak)",
        "key_origins": "Ecuador, Colombia, Costa Rica",
        "color": _C_AMBER,
    },
    {
        "name": "Meat & Poultry",
        "share_pct": 18,
        "peak_months": "Nov–Jan (holiday demand)",
        "key_origins": "Brazil, Australia, USA",
        "color": _C_RED,
    },
    {
        "name": "Avocados",
        "share_pct": 10,
        "peak_months": "Mar–Aug",
        "key_origins": "Mexico, Peru, South Africa",
        "color": _C_GREEN,
    },
    {
        "name": "Pharmaceuticals",
        "share_pct": 9,
        "peak_months": "Stable year-round",
        "key_origins": "Europe, India, USA",
        "color": _C_BLUE,
    },
    {
        "name": "Citrus Fruit",
        "share_pct": 8,
        "peak_months": "Apr–Sep (Southern Hemisphere harvest)",
        "key_origins": "South Africa, Spain, Argentina",
        "color": _C_AMBER,
    },
    {
        "name": "Wine & Beer",
        "share_pct": 6,
        "peak_months": "Sep–Dec",
        "key_origins": "France, Australia, Chile",
        "color": _C_PURPLE,
    },
    {
        "name": "Seafood",
        "share_pct": 10,
        "peak_months": "Oct–Dec (holiday)",
        "key_origins": "Norway, Chile, Vietnam",
        "color": _C_CYAN,
    },
    {
        "name": "Other Perishables",
        "share_pct": 17,
        "peak_months": "Variable",
        "key_origins": "Global",
        "color": C_TEXT3,
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> str:
    """Convert #rrggbb to 'r,g,b' string for rgba() usage."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return str(r) + "," + str(g) + "," + str(b)


def _risk_badge_html(risk: str) -> str:
    """Return inline HTML for a colored risk badge."""
    color = _RISK_COLOR.get(risk, C_TEXT2)
    rgb = _hex_to_rgb(color)
    return (
        "<span style=\"display:inline-block;padding:1px 7px;border-radius:999px;"
        "font-size:0.65rem;font-weight:700;text-transform:uppercase;"
        "letter-spacing:0.04em;"
        "background:rgba(" + rgb + ",0.18);"
        "color:" + color + ";"
        "border:1px solid rgba(" + rgb + ",0.35);\">"
        + risk +
        "</span>"
    )


def _build_equip_matrix() -> tuple:
    """Return (z_util, z_text, z_risk) matrices aligned to REGIONS x CONTAINER_TYPES."""
    z_util: List[List[float]] = []
    z_text: List[List[str]] = []
    z_risk: List[List[str]] = []

    # Index for O(1) lookup
    idx: Dict[tuple, EquipmentStatus] = {
        (e.region, e.container_type): e for e in REGIONAL_EQUIPMENT_STATUS
    }

    for region in REGIONS:
        row_util: List[float] = []
        row_text: List[str] = []
        row_risk: List[str] = []
        for ctype in CONTAINER_TYPES:
            equip = idx.get((region, ctype))
            if equip is not None:
                row_util.append(equip.utilization_pct)
                row_text.append(
                    str(int(equip.utilization_pct)) + "% " + equip.shortage_risk
                )
                row_risk.append(equip.shortage_risk)
            else:
                row_util.append(0.0)
                row_text.append("N/A")
                row_risk.append("LOW")
        z_util.append(row_util)
        z_text.append(row_text)
        z_risk.append(row_risk)

    return z_util, z_text, z_risk


# ── Section 1: Equipment Heat Map ─────────────────────────────────────────

def _render_heatmap() -> None:
    section_header(
        "Container Equipment Utilization Heat Map",
        "6 regions x 5 container types — color = utilization %. "
        "Red = tight/shortage risk, green = surplus.",
    )

    z_util, z_text, z_risk = _build_equip_matrix()
    x_labels = [_TYPE_LABELS.get(ct, ct) for ct in CONTAINER_TYPES]

    fig = go.Figure(go.Heatmap(
        z=z_util,
        x=x_labels,
        y=REGIONS,
        colorscale=_UTIL_COLORSCALE,
        zmin=50,
        zmax=100,
        text=z_text,
        texttemplate="%{text}",
        textfont={"size": 11, "color": "#f1f5f9"},
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Container: %{x}<br>"
            "Utilization: %{z:.1f}%<br>"
            "<extra></extra>"
        ),
        showscale=True,
        colorbar={
            "title": {"text": "Utilization %", "font": {"color": C_TEXT2, "size": 11}},
            "tickfont": {"color": C_TEXT3, "size": 10},
            "bgcolor": _C_SURFACE,
            "bordercolor": C_BORDER,
            "borderwidth": 1,
            "len": 0.85,
        },
        xgap=3,
        ygap=3,
    ))

    layout = dark_layout(height=350, showlegend=False)
    layout["xaxis"]["tickfont"] = {"color": C_TEXT2, "size": 11}
    layout["yaxis"]["tickfont"] = {"color": C_TEXT2, "size": 11}
    layout["xaxis"]["side"] = "bottom"
    layout["margin"] = {"l": 110, "r": 20, "t": 30, "b": 40}
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True)

    # Risk badge legend row
    legend_parts = []
    for risk, color in _RISK_COLOR.items():
        legend_parts.append(_risk_badge_html(risk))
    legend_html = " &nbsp; ".join(legend_parts)
    st.markdown(
        "<div style='font-size:0.78rem;color:" + C_TEXT3 + ";margin-top:-8px;"
        "margin-bottom:4px;'>Shortage Risk: " + legend_html + "</div>",
        unsafe_allow_html=True,
    )


# ── Section 2: Trade Imbalance Sankey ────────────────────────────────────

def _render_sankey() -> None:
    section_header(
        "Empty Container Flow — Trade Imbalance Visualizer",
        "Sankey diagram: loaded eastbound flows (bright) vs. westbound empty "
        "repositioning flows (dim). Width = relative TEU volume.",
    )

    # Select routes with significant imbalance for readability
    # Full 17-route Sankey would be unreadable; show key corridors
    selected_routes = [
        ("transpacific_eb",     "Asia Pacific", "North America", "EB Loaded",   True),
        ("transpacific_wb",     "North America", "Asia Pacific", "WB Empties",  False),
        ("asia_europe",         "Asia Pacific",  "Europe",       "EB Loaded",   True),
        ("med_hub_to_asia",     "Europe",        "Asia Pacific", "WB Empties",  False),
        ("ningbo_europe",       "Asia Pacific",  "Europe",       "EB Loaded",   True),
        ("transatlantic",       "Europe",        "North America","EB Loaded",   True),
        ("china_south_america", "Asia Pacific",  "South America","EB Loaded",   True),
        ("europe_south_america","Europe",        "South America","EB Loaded",   True),
    ]

    node_labels_raw = [
        "Asia Pacific",
        "North America",
        "Europe",
        "South America",
        "Middle East",
        "Africa",
    ]

    # Give each node an index
    node_idx = {lbl: i for i, lbl in enumerate(node_labels_raw)}

    node_colors = [
        "rgba(59,130,246,0.85)",    # Asia Pacific — blue
        "rgba(16,185,129,0.85)",    # North America — green
        "rgba(139,92,246,0.85)",    # Europe — purple
        "rgba(245,158,11,0.85)",    # South America — amber
        "rgba(6,182,212,0.85)",     # Middle East — cyan
        "rgba(239,68,68,0.85)",     # Africa — red
    ]

    sources: List[int] = []
    targets: List[int] = []
    values: List[float] = []
    link_colors: List[str] = []
    link_labels: List[str] = []

    imb_idx = {m.route_id: m for m in TRADE_IMBALANCE_DATA}

    for route_id, origin, dest, flow_label, is_loaded in selected_routes:
        metrics = imb_idx.get(route_id)
        if metrics is None:
            continue

        src = node_idx.get(origin)
        tgt = node_idx.get(dest)
        if src is None or tgt is None:
            continue

        if is_loaded:
            # Loaded leg: use imbalance_ratio as relative volume, bright color
            vol = max(metrics.imbalance_ratio * 10, 5)
            color = "rgba(59,130,246,0.55)"
            cost_label = (
                flow_label
                + " | Reposition cost: $"
                + str(metrics.empty_container_repositioning_cost_per_feu)
                + "/FEU"
            )
        else:
            # Empty leg: smaller flow, dim color
            vol = max((2.0 - metrics.imbalance_ratio) * 8, 3)
            color = "rgba(100,116,139,0.30)"
            cost_label = (
                flow_label
                + " | Empty reposition: $"
                + str(metrics.empty_container_repositioning_cost_per_feu)
                + "/FEU | "
                + str(metrics.repositioning_days)
                + " days"
            )

        sources.append(src)
        targets.append(tgt)
        values.append(vol)
        link_colors.append(color)
        link_labels.append(cost_label)

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node={
            "pad": 18,
            "thickness": 22,
            "line": {"color": "rgba(255,255,255,0.15)", "width": 0.8},
            "label": node_labels_raw,
            "color": node_colors,
            "hovertemplate": "%{label}<extra></extra>",
        },
        link={
            "source": sources,
            "target": targets,
            "value": values,
            "color": link_colors,
            "label": link_labels,
            "hovertemplate": "%{label}<extra></extra>",
        },
    ))

    layout = dark_layout(height=420, showlegend=False)
    layout["paper_bgcolor"] = _C_BG
    layout["font"] = {"color": C_TEXT, "size": 12, "family": "Inter, sans-serif"}
    layout["margin"] = {"l": 20, "r": 20, "t": 30, "b": 20}
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True)

    # Legend note
    st.markdown(
        "<div style='font-size:0.78rem;color:" + C_TEXT3 + ";margin-top:-6px;'>"
        "<span style='color:" + _C_BLUE + ";font-weight:600;'>Blue links</span> = "
        "loaded eastbound flows &nbsp;|&nbsp; "
        "<span style='color:" + C_TEXT2 + ";'>Gray links</span> = "
        "westbound empty repositioning &nbsp;|&nbsp; "
        "Width = relative TEU volume</div>",
        unsafe_allow_html=True,
    )


# ── Section 3: Reefer Spotlight ───────────────────────────────────────────

def _render_reefer_spotlight() -> None:
    section_header(
        "Reefer Container Spotlight",
        "Refrigerated containers: globally the tightest equipment segment. "
        "Utilization 86-91% across regions — HIGH/CRITICAL shortage risk.",
    )

    reefer_data = get_reefer_summary()
    logger.debug("Reefer summary: {}", reefer_data)

    # ── KPI row ──────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    avg_util = reefer_data.get("avg_utilization_pct", 0.0)
    avg_rate = reefer_data.get("avg_lease_rate_usd", 0.0)
    total_k = reefer_data.get("total_units_k", 0.0)
    # Reefer premium over 40FT_DRY (approx): $3.50 avg vs $0.88 avg
    dry_avg_rate = 0.88
    premium_x = round(avg_rate / dry_avg_rate, 1) if dry_avg_rate > 0 else 0.0

    for col, label, value, unit, color in [
        (col1, "Avg Reefer Utilization", str(avg_util) + "%",        "",        _C_RED),
        (col2, "Total Reefer Units",     str(total_k) + "K",         "tracked", _C_BLUE),
        (col3, "Avg Daily Lease Rate",   "$" + str(avg_rate) + "/day","",        _C_AMBER),
        (col4, "Rate Premium vs Dry",    str(premium_x) + "x",       "dry avg", _C_PURPLE),
    ]:
        with col:
            st.markdown(
                "<div style=\"background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
                "border-top:3px solid " + color + ";border-radius:10px;"
                "padding:18px 16px;text-align:center;\">"
                "<div style=\"font-size:0.70rem;color:" + C_TEXT2 + ";text-transform:uppercase;"
                "letter-spacing:0.07em;margin-bottom:6px;\">" + label + "</div>"
                "<div style=\"font-size:1.8rem;font-weight:700;color:" + C_TEXT + ";\">"
                + value +
                "</div>"
                "<div style=\"font-size:0.75rem;color:" + C_TEXT2 + ";margin-top:4px;\">"
                + unit +
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='margin-top:14px;'></div>", unsafe_allow_html=True)

    # ── Two columns: seasonal chart + commodity breakdown ─────────────────
    col_chart, col_comm = st.columns([3, 2])

    with col_chart:
        # Seasonal demand line chart
        months = _REEFER_SEASONAL["labels"]
        fig = go.Figure()

        seasonal_region_colors = {
            "Global":       C_TEXT2,
            "South America": _C_AMBER,
            "Europe":       _C_PURPLE,
            "Asia Pacific": _C_BLUE,
            "North America": _C_GREEN,
        }

        for region, color in seasonal_region_colors.items():
            y_vals = _REEFER_SEASONAL.get(region, [])
            if not y_vals:
                continue
            is_global = region == "Global"
            fig.add_trace(go.Scatter(
                x=months,
                y=y_vals,
                name=region,
                mode="lines+markers",
                line={
                    "color": color,
                    "width": 2.5 if is_global else 1.5,
                    "dash": "solid" if is_global else "dot",
                },
                marker={"size": 5 if is_global else 4, "color": color},
                hovertemplate=region + " — %{x}: %{y}<extra></extra>",
            ))

        # Reference line at 100 (average)
        fig.add_hline(
            y=100,
            line={"color": "rgba(255,255,255,0.20)", "dash": "dash", "width": 1},
            annotation_text="Avg (100)",
            annotation_position="right",
            annotation_font={"color": C_TEXT3, "size": 10},
        )

        layout = dark_layout(
            title="Reefer Seasonal Demand Index (100 = annual average)",
            height=300,
        )
        layout["xaxis"]["title"] = "Month"
        layout["yaxis"]["title"] = "Demand Index"
        layout["yaxis"]["range"] = [60, 150]
        layout["legend"]["orientation"] = "h"
        layout["legend"]["y"] = -0.25
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True)

    with col_comm:
        st.markdown(
            "<div style='font-size:0.78rem;font-weight:700;color:" + C_TEXT2 + ";"
            "text-transform:uppercase;letter-spacing:0.07em;"
            "margin-bottom:10px;'>Top Reefer Commodities</div>",
            unsafe_allow_html=True,
        )
        for comm in _REEFER_COMMODITIES:
            color = comm["color"]
            rgb = _hex_to_rgb(color)
            bar_width = int(comm["share_pct"] * 3)  # scale for visual
            st.markdown(
                "<div style=\"background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
                "border-left:3px solid " + color + ";"
                "border-radius:8px;padding:10px 12px;margin-bottom:6px;\">"
                "<div style=\"display:flex;justify-content:space-between;"
                "align-items:center;margin-bottom:4px;\">"
                "<span style=\"font-size:0.82rem;font-weight:600;color:" + C_TEXT + ";\">"
                + comm["name"] +
                "</span>"
                "<span style=\"font-size:0.78rem;font-weight:700;"
                "color:" + color + ";\">"
                + str(comm["share_pct"]) + "%"
                "</span>"
                "</div>"
                "<div style=\"background:rgba(" + rgb + ",0.12);border-radius:3px;"
                "height:4px;margin-bottom:6px;\">"
                "<div style=\"background:" + color + ";width:" + str(bar_width) + "%;"
                "height:4px;border-radius:3px;\"></div>"
                "</div>"
                "<div style=\"font-size:0.72rem;color:" + C_TEXT3 + ";\">"
                + comm["peak_months"] +
                " &nbsp;|&nbsp; " + comm["key_origins"] +
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )


# ── Section 4: Equipment Cost Calculator ─────────────────────────────────

def _render_cost_calculator(route_results: Any) -> None:
    section_header(
        "Equipment Cost Calculator",
        "Select a route and TEU count to see the repositioning cost impact "
        "on your freight rate.",
    )

    # Build route options from TRADE_IMBALANCE_DATA
    route_options = {
        m.route_id: (
            m.route_id.replace("_", " ").title()
            + " (" + m.origin_region
            + " → " + m.dest_region + ")"
        )
        for m in TRADE_IMBALANCE_DATA
    }
    route_display_list = list(route_options.values())
    route_id_list = list(route_options.keys())

    col_sel, col_teu, col_base = st.columns([3, 2, 2])
    with col_sel:
        selected_display = st.selectbox(
            "Route",
            options=route_display_list,
            index=0,
            key="equip_calc_route",
        )
    with col_teu:
        teu_count = st.number_input(
            "TEU Count",
            min_value=1,
            max_value=15000,
            value=500,
            step=100,
            key="equip_calc_teu",
        )
    with col_base:
        base_rate_per_feu = st.number_input(
            "Base Rate (USD/FEU)",
            min_value=100,
            max_value=25000,
            value=2500,
            step=100,
            key="equip_calc_base",
        )

    selected_idx = route_display_list.index(selected_display)
    selected_route_id = route_id_list[selected_idx]

    metrics = get_trade_imbalance(selected_route_id)

    if metrics is None:
        st.warning("No trade imbalance data available for the selected route.")
        return

    feu_count = teu_count / 2.0
    reposition_cost_per_feu = metrics.empty_container_repositioning_cost_per_feu
    adjusted_rate = compute_equipment_adjusted_rate(selected_route_id, base_rate_per_feu)
    total_base = base_rate_per_feu * feu_count
    total_reposition = reposition_cost_per_feu * feu_count
    total_adjusted = adjusted_rate * feu_count
    cost_increase_pct = (reposition_cost_per_feu / base_rate_per_feu) * 100

    # Imbalance direction label
    if metrics.imbalance_ratio > 1.3:
        imbalance_label = "Export-heavy origin (empties flow back)"
        imbalance_color = _C_RED
    elif metrics.imbalance_ratio < 0.8:
        imbalance_label = "Import-heavy origin (carrier absorbs empty return)"
        imbalance_color = _C_AMBER
    else:
        imbalance_label = "Near-balanced trade flow"
        imbalance_color = _C_GREEN

    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    for col, label, value, color in [
        (c1, "Base Freight Cost",     "${:,.0f}".format(total_base),        _C_BLUE),
        (c2, "Repositioning Cost",    "${:,.0f}".format(total_reposition),  _C_RED),
        (c3, "Equipment-Adj. Total",  "${:,.0f}".format(total_adjusted),    _C_AMBER),
        (c4, "Rate Uplift",           "{:.1f}%".format(cost_increase_pct),  _C_PURPLE),
    ]:
        with col:
            st.markdown(
                "<div style=\"background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
                "border-top:3px solid " + color + ";border-radius:10px;"
                "padding:18px 16px;text-align:center;\">"
                "<div style=\"font-size:0.70rem;color:" + C_TEXT2 + ";text-transform:uppercase;"
                "letter-spacing:0.07em;margin-bottom:6px;\">" + label + "</div>"
                "<div style=\"font-size:1.6rem;font-weight:700;color:" + C_TEXT + ";\">"
                + value +
                "</div>"
                "<div style=\"font-size:0.72rem;color:" + C_TEXT2 + ";margin-top:4px;\">"
                "per " + "{:,.0f}".format(feu_count) + " FEU"
                "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

    # Detail card
    st.markdown(
        "<div style=\"background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
        "border-radius:10px;padding:16px 20px;\">"
        "<div style=\"display:flex;gap:32px;flex-wrap:wrap;\">"
        "<div>"
        "<div style=\"font-size:0.70rem;color:" + C_TEXT2 + ";text-transform:uppercase;"
        "letter-spacing:0.07em;\">Trade Imbalance Ratio</div>"
        "<div style=\"font-size:1.1rem;font-weight:700;color:" + imbalance_color + ";margin-top:4px;\">"
        "{:.2f}".format(metrics.imbalance_ratio) + ":1"
        "</div>"
        "<div style=\"font-size:0.75rem;color:" + imbalance_color + ";\">" + imbalance_label + "</div>"
        "</div>"
        "<div>"
        "<div style=\"font-size:0.70rem;color:" + C_TEXT2 + ";text-transform:uppercase;"
        "letter-spacing:0.07em;\">Repositioning Days</div>"
        "<div style=\"font-size:1.1rem;font-weight:700;color:" + C_TEXT + ";margin-top:4px;\">"
        + str(metrics.repositioning_days) + " days"
        "</div>"
        "<div style=\"font-size:0.75rem;color:" + C_TEXT2 + ";\">empty transit back to origin</div>"
        "</div>"
        "<div>"
        "<div style=\"font-size:0.70rem;color:" + C_TEXT2 + ";text-transform:uppercase;"
        "letter-spacing:0.07em;\">Reposition per FEU</div>"
        "<div style=\"font-size:1.1rem;font-weight:700;color:" + _C_AMBER + ";margin-top:4px;\">"
        + "${:,.0f}".format(reposition_cost_per_feu)
        + "</div>"
        "<div style=\"font-size:0.75rem;color:" + C_TEXT2 + ";\">adds to eastbound rate</div>"
        "</div>"
        "<div>"
        "<div style=\"font-size:0.70rem;color:" + C_TEXT2 + ";text-transform:uppercase;"
        "letter-spacing:0.07em;\">Adj. Rate per FEU</div>"
        "<div style=\"font-size:1.1rem;font-weight:700;color:" + _C_RED + ";margin-top:4px;\">"
        + "${:,.0f}".format(adjusted_rate)
        + "</div>"
        "<div style=\"font-size:0.75rem;color:" + C_TEXT2 + ";\">vs base ${:,.0f}/FEU".format(base_rate_per_feu) + "</div>"
        "</div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ── Section 5: Regional Surplus/Deficit Timeline ─────────────────────────

def _render_surplus_deficit_timeline() -> None:
    section_header(
        "Regional Equipment Balance Timeline (2020–2026)",
        "Equipment balance index: 100 = well-supplied, 0 = severe shortage. "
        "2021 = COVID demand surge drove historic shortage; regional imbalances persist.",
    )

    years = _BALANCE_TIMELINE["years"]

    region_colors = {
        "Asia Pacific":  _C_BLUE,
        "North America": _C_GREEN,
        "Europe":        _C_PURPLE,
        "South America": _C_AMBER,
        "Middle East":   _C_CYAN,
        "Africa":        _C_RED,
    }

    fig = go.Figure()

    for region, color in region_colors.items():
        y_vals = _BALANCE_TIMELINE.get(region, [])
        if not y_vals:
            continue
        fig.add_trace(go.Scatter(
            x=years,
            y=y_vals,
            name=region,
            mode="lines+markers",
            line={"color": color, "width": 2},
            marker={"size": 7, "color": color,
                    "line": {"color": _C_BG, "width": 1.5}},
            hovertemplate=region + " %{x}: %{y}<extra></extra>",
        ))

    # Annotate key events
    annotations = [
        {"x": 2021, "y": 18, "text": "COVID surge\npeak shortage",
         "ax": 0, "ay": -50},
        {"x": 2022.5, "y": 40, "text": "Gradual\nrecovery",
         "ax": 30, "ay": -40},
        {"x": 2025, "y": 76, "text": "Asia\nsurplus",
         "ax": 30, "ay": -30},
    ]

    for ann in annotations:
        fig.add_annotation(
            x=ann["x"], y=ann["y"],
            text=ann["text"],
            showarrow=True,
            arrowhead=2,
            arrowcolor="rgba(255,255,255,0.3)",
            arrowwidth=1.5,
            ax=ann["ax"], ay=ann["ay"],
            font={"color": C_TEXT3, "size": 10},
            bgcolor="rgba(17,24,39,0.85)",
            bordercolor="rgba(255,255,255,0.1)",
            borderwidth=1,
            borderpad=4,
        )

    # Threshold bands
    fig.add_hrect(
        y0=0, y1=35,
        fillcolor="rgba(239,68,68,0.06)",
        line_width=0,
        annotation_text="Shortage zone",
        annotation_position="left",
        annotation_font={"color": _C_RED, "size": 10},
    )
    fig.add_hrect(
        y0=35, y1=65,
        fillcolor="rgba(245,158,11,0.04)",
        line_width=0,
        annotation_text="Transition",
        annotation_position="left",
        annotation_font={"color": _C_AMBER, "size": 10},
    )
    fig.add_hrect(
        y0=65, y1=100,
        fillcolor="rgba(16,185,129,0.04)",
        line_width=0,
        annotation_text="Surplus zone",
        annotation_position="left",
        annotation_font={"color": _C_GREEN, "size": 10},
    )

    layout = dark_layout(
        title="Equipment Balance Index by Region (100 = well-supplied)",
        height=380,
    )
    layout["xaxis"]["title"] = "Year"
    layout["xaxis"]["tickvals"] = years
    layout["xaxis"]["ticktext"] = [str(y) for y in years]
    layout["yaxis"]["title"] = "Balance Index"
    layout["yaxis"]["range"] = [0, 105]
    layout["legend"]["orientation"] = "h"
    layout["legend"]["y"] = -0.22
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True)

    # Narrative callout
    global_idx = get_global_equipment_index()
    if global_idx >= 85:
        idx_label = "TIGHT"
        idx_color = _C_RED
    elif global_idx >= 70:
        idx_label = "NORMAL"
        idx_color = _C_AMBER
    else:
        idx_label = "SURPLUS"
        idx_color = _C_GREEN

    st.markdown(
        "<div style=\"background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
        "border-left:4px solid " + idx_color + ";"
        "border-radius:8px;padding:14px 18px;margin-top:8px;\">"
        "<span style=\"font-size:0.78rem;font-weight:700;color:" + C_TEXT2 + ";"
        "text-transform:uppercase;letter-spacing:0.06em;\">"
        "Current Global Equipment Index: "
        "</span>"
        "<span style=\"font-size:1.1rem;font-weight:800;color:" + idx_color + ";\">"
        "{:.1f}%".format(global_idx) + " &nbsp;"
        "</span>"
        "<span style=\"display:inline-block;padding:2px 10px;border-radius:999px;"
        "font-size:0.72rem;font-weight:700;"
        "background:rgba(" + _hex_to_rgb(idx_color) + ",0.15);"
        "color:" + idx_color + ";"
        "border:1px solid rgba(" + _hex_to_rgb(idx_color) + ",0.35);\">"
        + idx_label +
        "</span>"
        "<div style=\"font-size:0.80rem;color:" + C_TEXT2 + ";margin-top:6px;\">"
        "Weighted average utilization across all 6 regions and 5 container types. "
        "Above 85% = tight; below 70% = surplus."
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ── Main render entry point ───────────────────────────────────────────────

def render(
    route_results: Any = None,
    freight_data: Any = None,
    macro_data: Any = None,
) -> None:
    """Render the Container Equipment Availability tab.

    Parameters
    ----------
    route_results:
        List of ShippingRoute objects from route_registry (used for Equipment
        Cost Calculator context).  May be None — tab functions independently.
    freight_data:
        Freight market data dict from the main app.  May be None.
    macro_data:
        Macro data dict from FRED/World Bank feeds.  May be None.
    """
    logger.debug("tab_equipment.render() called.")

    _render_heatmap()
    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

    st.divider()

    _render_sankey()
    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

    st.divider()

    _render_reefer_spotlight()
    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

    st.divider()

    _render_cost_calculator(route_results)
    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

    st.divider()

    _render_surplus_deficit_timeline()


# ── Integration notes ────────────────────────────────────────────────────
# To wire into app.py:
#
# 1. Import at top of app.py:
#        from ui import tab_equipment
#
# 2. Add tab in st.tabs() call:
#        ..., tab_equip = st.tabs([..., "Equipment"])
#
# 3. Render:
#        with tab_equip:
#            tab_equipment.render(
#                route_results=route_results,
#                freight_data=freight_data,
#                macro_data=macro_data,
#            )
