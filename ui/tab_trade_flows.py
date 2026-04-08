"""tab_trade_flows.py — Global Trade Flows: cargo flow map, Sankey diagram,
route cargo breakdowns, and commodity deep-dive cards."""
from __future__ import annotations

import math
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ports.port_registry import PORTS
from routes.route_registry import ROUTES
from processing.cargo_analyzer import (
    HS_CATEGORIES,
    CARGO_CHARACTERISTICS,
    _ROUTE_REGIONS,
    _REGION_DOMINANT_CARGO,
    _demand_signal,
    _key_insight,
)

# ── Palette ──────────────────────────────────────────────────────────────────
C_BG      = "#0c0e14"
C_SURFACE = "#12151e"
C_CARD    = "#181c28"
C_BORDER  = "rgba(232,230,225,0.06)"
C_HIGH    = "#2e9e6e"
C_MOD     = "#c9962b"
C_LOW     = "#c0392b"
C_ACCENT  = "#3572b0"
C_TEXT    = "#e8e6e1"
C_TEXT2   = "#9a968e"
C_TEXT3   = "#6b6760"

_SANS = "'Libre Franklin', 'Inter', system-ui, sans-serif"
_MONO = "'JetBrains Mono', 'Fira Code', monospace"

# Commodity → color mapping
COMMODITY_COLORS = {
    "electronics": "#3b82f6",
    "machinery":   "#8b5cf6",
    "automotive":  "#06b6d4",
    "chemicals":   "#f59e0b",
    "agriculture": "#22c55e",
    "metals":      "#f97316",
    "apparel":     "#ec4899",
}

COMMODITY_LABELS = {k: v["label"] for k, v in HS_CATEGORIES.items()}

# Port lookup by locode
_PORTS_BY_LOCODE = {p.locode: p for p in PORTS}

# Estimated annual trade volume per route ($ billions, illustrative)
_ROUTE_VOLUMES = {
    "transpacific_eb": 420, "asia_europe": 380, "transpacific_wb": 120,
    "transatlantic": 210, "sea_transpacific_eb": 95, "ningbo_europe": 180,
    "middle_east_to_europe": 85, "middle_east_to_asia": 110,
    "south_asia_to_europe": 65, "intra_asia_china_sea": 150,
    "intra_asia_china_japan": 130, "china_south_america": 75,
    "europe_south_america": 55, "med_hub_to_asia": 40,
    "north_africa_to_europe": 30, "us_east_south_america": 45,
    "longbeach_to_asia": 60,
}


def _rgba(h: str, a: float) -> str:
    try:
        h2 = h.lstrip("#")
        r, g, b = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        return f"rgba({r},{g},{b},{a})"
    except Exception:
        return f"rgba(255,255,255,{a})"


def _card_html(title: str, subtitle: str = "") -> str:
    sub = f'<div style="font-family:{_SANS};font-size:0.68rem;color:{C_TEXT3};margin-top:2px">{subtitle}</div>' if subtitle else ""
    return f"""<div style="font-family:{_SANS};font-size:0.78rem;font-weight:700;color:{C_TEXT};
                letter-spacing:-0.01em;margin-bottom:12px">{title}{sub}</div>"""


def _get_route_cargo_mix(route_id: str) -> dict[str, float]:
    """Get cargo category weights for a route based on origin region."""
    regions = _ROUTE_REGIONS.get(route_id)
    if not regions:
        return {"electronics": 0.25, "machinery": 0.25, "chemicals": 0.25, "metals": 0.25}
    origin_region = regions[0]
    dominant = _REGION_DOMINANT_CARGO.get(origin_region, ["electronics", "machinery"])
    n = len(dominant)
    weights = {}
    remaining = 1.0
    for i, cat in enumerate(dominant):
        w = max(0.10, (0.55 - i * 0.12))
        weights[cat] = w
        remaining -= w
    # Distribute remaining among other categories
    others = [c for c in HS_CATEGORIES if c not in weights]
    if others and remaining > 0:
        per = remaining / len(others)
        for c in others:
            weights[c] = per
    # Normalize
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Global Flow Map
# ══════════════════════════════════════════════════════════════════════════════

def _render_flow_map() -> None:
    try:
        from ui.styles import dark_layout

        fig = go.Figure()

        # Port markers
        lats = [p.lat for p in PORTS]
        lons = [p.lon for p in PORTS]
        names = [p.name for p in PORTS]
        fig.add_trace(go.Scattergeo(
            lat=lats, lon=lons, text=names,
            mode="markers+text",
            marker=dict(size=6, color=C_ACCENT, opacity=0.8, line=dict(width=0.5, color=C_TEXT3)),
            textposition="top center",
            textfont=dict(size=8, color=C_TEXT3),
            hovertemplate="<b>%{text}</b><extra></extra>",
            showlegend=False,
        ))

        # Route arcs colored by dominant commodity
        legend_added = set()
        for route in ROUTES:
            origin = _PORTS_BY_LOCODE.get(route.origin_locode)
            dest = _PORTS_BY_LOCODE.get(route.dest_locode)
            if not origin or not dest:
                continue

            cargo_mix = _get_route_cargo_mix(route.id)
            dominant_cat = max(cargo_mix, key=cargo_mix.get) if cargo_mix else "electronics"
            color = COMMODITY_COLORS.get(dominant_cat, C_ACCENT)
            label = COMMODITY_LABELS.get(dominant_cat, dominant_cat.title())
            vol = _ROUTE_VOLUMES.get(route.id, 50)
            width = max(1, min(5, vol / 100))

            show_legend = dominant_cat not in legend_added
            legend_added.add(dominant_cat)

            fig.add_trace(go.Scattergeo(
                lat=[origin.lat, dest.lat],
                lon=[origin.lon, dest.lon],
                mode="lines",
                line=dict(width=width, color=color),
                opacity=0.6,
                name=label,
                showlegend=show_legend,
                hovertemplate=(
                    f"<b>{route.name}</b><br>"
                    f"Dominant: {label} ({cargo_mix.get(dominant_cat, 0):.0%})<br>"
                    f"Est. volume: ${vol}B/yr<br>"
                    f"Transit: {route.transit_days}d<extra></extra>"
                ),
            ))

        fig.update_layout(
            geo=dict(
                bgcolor=C_BG,
                landcolor=C_SURFACE,
                oceancolor=C_BG,
                lakecolor=C_BG,
                coastlinecolor=C_TEXT3,
                countrycolor=_rgba(C_TEXT, 0.08),
                showframe=False,
                projection_type="natural earth",
                lataxis_range=[-50, 75],
                lonaxis_range=[-130, 160],
            ),
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            height=480,
            margin=dict(l=0, r=0, t=10, b=10),
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                font=dict(color=C_TEXT2, size=11, family=_SANS),
                orientation="h",
                yanchor="bottom", y=1.02,
                xanchor="center", x=0.5,
            ),
        )

        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    except Exception as exc:
        logger.warning(f"Flow map render failed: {exc}")
        st.error(f"Flow map error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Commodity Flow Sankey
# ══════════════════════════════════════════════════════════════════════════════

def _render_sankey() -> None:
    try:
        # Build nodes: origin regions + commodity types + dest regions
        origin_regions = sorted(set(r[0] for r in _ROUTE_REGIONS.values()))
        commodities = list(HS_CATEGORIES.keys())
        dest_regions = sorted(set(r[1] for r in _ROUTE_REGIONS.values()))

        # Deduplicate regions that appear on both sides
        all_origins = [f"{r} (origin)" for r in origin_regions]
        all_dests = [f"{r} (dest)" for r in dest_regions]
        all_commodities = [COMMODITY_LABELS.get(c, c.title()) for c in commodities]
        labels = all_origins + all_commodities + all_dests

        origin_idx = {r: i for i, r in enumerate(origin_regions)}
        commodity_idx = {c: len(origin_regions) + i for i, c in enumerate(commodities)}
        dest_idx = {r: len(origin_regions) + len(commodities) + i for i, r in enumerate(dest_regions)}

        sources, targets, values, link_colors = [], [], [], []

        # Origin → Commodity links
        for route_id, (orig_region, dest_region) in _ROUTE_REGIONS.items():
            if orig_region not in origin_idx:
                continue
            vol = _ROUTE_VOLUMES.get(route_id, 30)
            cargo_mix = _get_route_cargo_mix(route_id)
            for cat, weight in cargo_mix.items():
                if cat not in commodity_idx:
                    continue
                flow_val = vol * weight
                if flow_val < 2:
                    continue
                sources.append(origin_idx[orig_region])
                targets.append(commodity_idx[cat])
                values.append(flow_val)
                link_colors.append(_rgba(COMMODITY_COLORS.get(cat, C_ACCENT), 0.4))

        # Commodity → Destination links
        for route_id, (orig_region, dest_region) in _ROUTE_REGIONS.items():
            if dest_region not in dest_idx:
                continue
            vol = _ROUTE_VOLUMES.get(route_id, 30)
            cargo_mix = _get_route_cargo_mix(route_id)
            for cat, weight in cargo_mix.items():
                if cat not in commodity_idx:
                    continue
                flow_val = vol * weight
                if flow_val < 2:
                    continue
                sources.append(commodity_idx[cat])
                targets.append(dest_idx[dest_region])
                values.append(flow_val)
                link_colors.append(_rgba(COMMODITY_COLORS.get(cat, C_ACCENT), 0.4))

        # Node colors
        node_colors = (
            [_rgba(C_ACCENT, 0.7)] * len(origin_regions)
            + [COMMODITY_COLORS.get(c, C_ACCENT) for c in commodities]
            + [_rgba(C_HIGH, 0.7)] * len(dest_regions)
        )

        fig = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(
                pad=15, thickness=20,
                label=labels,
                color=node_colors,
                line=dict(width=0),
            ),
            link=dict(
                source=sources, target=targets, value=values,
                color=link_colors,
            ),
        ))

        fig.update_layout(
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            font=dict(color=C_TEXT2, size=11, family=_SANS),
            height=450,
            margin=dict(l=10, r=10, t=10, b=10),
        )

        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    except Exception as exc:
        logger.warning(f"Sankey render failed: {exc}")
        st.error(f"Sankey error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Route Cargo Breakdown
# ══════════════════════════════════════════════════════════════════════════════

def _render_route_breakdown() -> None:
    try:
        rows_html = ""
        for route in ROUTES:
            cargo_mix = _get_route_cargo_mix(route.id)
            vol = _ROUTE_VOLUMES.get(route.id, 30)

            # Build stacked bar segments
            bar_segments = ""
            sorted_cats = sorted(cargo_mix.items(), key=lambda x: -x[1])
            for cat, weight in sorted_cats:
                if weight < 0.03:
                    continue
                color = COMMODITY_COLORS.get(cat, C_ACCENT)
                pct = weight * 100
                label = COMMODITY_LABELS.get(cat, cat.title())
                bar_segments += (
                    f'<div style="width:{pct}%;height:100%;background:{color};display:inline-block"'
                    f' title="{label}: {pct:.0f}%"></div>'
                )

            # Top 2 commodities as text
            top2 = sorted_cats[:2]
            top_text = ", ".join(
                f"{COMMODITY_LABELS.get(c, c.title())} {w:.0%}" for c, w in top2
            )

            rows_html += f"""
            <div style="padding:8px 0;border-bottom:1px solid {_rgba(C_TEXT, 0.04)}">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                    <span style="font-family:{_SANS};font-size:0.76rem;color:{C_TEXT};
                                 font-weight:600;min-width:180px">{route.name}</span>
                    <span style="font-family:{_MONO};font-size:0.66rem;color:{C_TEXT3}">${vol}B/yr</span>
                </div>
                <div style="height:8px;border-radius:4px;overflow:hidden;background:{_rgba(C_TEXT, 0.06)};
                            font-size:0;line-height:0;white-space:nowrap">{bar_segments}</div>
                <div style="font-family:{_SANS};font-size:0.64rem;color:{C_TEXT3};margin-top:3px">{top_text}</div>
            </div>"""

        html = (
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;padding:16px 20px">'
            + _card_html("Route Cargo Breakdown", "Inferred commodity mix by trade lane")
            + rows_html
            + "</div>"
        )
        st.markdown(html, unsafe_allow_html=True)

    except Exception as exc:
        logger.warning(f"Route breakdown render failed: {exc}")
        st.error(f"Route breakdown error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Commodity Deep Dive Cards
# ══════════════════════════════════════════════════════════════════════════════

def _render_commodity_cards() -> None:
    try:
        import calendar

        cards_html = ""
        for cat_key, cat_meta in HS_CATEGORIES.items():
            chars = CARGO_CHARACTERISTICS.get(cat_key, {})
            label = cat_meta["label"]
            color = COMMODITY_COLORS.get(cat_key, C_ACCENT)
            yoy = chars.get("yoy_growth", 0)
            peak = chars.get("seasonal_peak", 6)
            shipping = chars.get("shipping", "container")
            sensitivity = chars.get("sensitivity", "moderate")
            signal, signal_color = _demand_signal(yoy)
            insight = _key_insight(cat_key, yoy, signal)

            # Find top routes for this commodity
            top_routes = []
            for route in ROUTES:
                mix = _get_route_cargo_mix(route.id)
                if cat_key in mix and mix[cat_key] >= 0.15:
                    top_routes.append((route.name, mix[cat_key]))
            top_routes.sort(key=lambda x: -x[1])
            routes_text = ", ".join(f"{n}" for n, _ in top_routes[:3]) if top_routes else "Various"

            cards_html += f"""
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;
                        padding:14px 18px;border-left:3px solid {color}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
                    <div style="font-family:{_SANS};font-size:0.82rem;font-weight:700;color:{C_TEXT}">{label}</div>
                    <span style="background:{_rgba(signal_color, 0.12)};color:{signal_color};
                                 padding:2px 8px;border-radius:10px;font-size:0.6rem;
                                 font-weight:700;font-family:{_SANS}">{signal}</span>
                </div>
                <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px">
                    <div>
                        <div style="font-family:{_SANS};font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em">YoY Growth</div>
                        <div style="font-family:{_MONO};font-size:0.82rem;font-weight:700;
                                    color:{C_HIGH if yoy > 0 else C_LOW}">{yoy:+.1f}%</div>
                    </div>
                    <div>
                        <div style="font-family:{_SANS};font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em">Peak Season</div>
                        <div style="font-family:{_MONO};font-size:0.82rem;color:{C_TEXT}">{calendar.month_abbr[peak]}</div>
                    </div>
                    <div>
                        <div style="font-family:{_SANS};font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em">Ship Type</div>
                        <div style="font-family:{_SANS};font-size:0.78rem;color:{C_TEXT}">{shipping}</div>
                    </div>
                    <div>
                        <div style="font-family:{_SANS};font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em">Sensitivity</div>
                        <div style="font-family:{_SANS};font-size:0.78rem;color:{C_TEXT}">{sensitivity.title()}</div>
                    </div>
                </div>
                <div style="font-family:{_SANS};font-size:0.72rem;color:{C_TEXT2};line-height:1.5;
                            margin-bottom:6px">{insight}</div>
                <div style="font-family:{_SANS};font-size:0.64rem;color:{C_TEXT3}">
                    Top routes: {routes_text}</div>
            </div>"""

        # Render in 2-column grid
        cats = list(HS_CATEGORIES.keys())
        mid = (len(cats) + 1) // 2
        col1, col2 = st.columns(2)

        # Build per-column HTML
        for i, cat_key in enumerate(cats):
            chars = CARGO_CHARACTERISTICS.get(cat_key, {})
            cat_meta = HS_CATEGORIES[cat_key]
            label = cat_meta["label"]
            color = COMMODITY_COLORS.get(cat_key, C_ACCENT)
            yoy = chars.get("yoy_growth", 0)
            peak = chars.get("seasonal_peak", 6)
            shipping = chars.get("shipping", "container")
            sensitivity = chars.get("sensitivity", "moderate")
            signal, signal_color = _demand_signal(yoy)
            insight = _key_insight(cat_key, yoy, signal)

            top_routes = []
            for route in ROUTES:
                mix = _get_route_cargo_mix(route.id)
                if cat_key in mix and mix[cat_key] >= 0.15:
                    top_routes.append((route.name, mix[cat_key]))
            top_routes.sort(key=lambda x: -x[1])
            routes_text = ", ".join(f"{n}" for n, _ in top_routes[:3]) if top_routes else "Various"

            card = f"""
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;
                        padding:14px 18px;border-left:3px solid {color};margin-bottom:12px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
                    <div style="font-family:{_SANS};font-size:0.82rem;font-weight:700;color:{C_TEXT}">{label}</div>
                    <span style="background:{_rgba(signal_color, 0.12)};color:{signal_color};
                                 padding:2px 8px;border-radius:10px;font-size:0.6rem;
                                 font-weight:700;font-family:{_SANS}">{signal}</span>
                </div>
                <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px">
                    <div>
                        <div style="font-family:{_SANS};font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em">YoY Growth</div>
                        <div style="font-family:{_MONO};font-size:0.82rem;font-weight:700;
                                    color:{C_HIGH if yoy > 0 else C_LOW}">{yoy:+.1f}%</div>
                    </div>
                    <div>
                        <div style="font-family:{_SANS};font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em">Peak</div>
                        <div style="font-family:{_MONO};font-size:0.82rem;color:{C_TEXT}">{calendar.month_abbr[peak]}</div>
                    </div>
                    <div>
                        <div style="font-family:{_SANS};font-size:0.58rem;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em">Vessel</div>
                        <div style="font-family:{_SANS};font-size:0.78rem;color:{C_TEXT}">{shipping}</div>
                    </div>
                </div>
                <div style="font-family:{_SANS};font-size:0.72rem;color:{C_TEXT2};line-height:1.5;
                            margin-bottom:6px">{insight}</div>
                <div style="font-family:{_SANS};font-size:0.64rem;color:{C_TEXT3}">
                    Routes: {routes_text}</div>
            </div>"""

            target = col1 if i < mid else col2
            with target:
                st.markdown(card, unsafe_allow_html=True)

    except Exception as exc:
        logger.warning(f"Commodity cards render failed: {exc}")
        st.error(f"Commodity cards error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER
# ══════════════════════════════════════════════════════════════════════════════

def render(
    trade_data=None,
    route_results=None,
    port_results=None,
    freight_data=None,
    macro_data=None,
) -> None:
    """Global Trade Flows dashboard."""
    try:
        # Section header
        st.markdown(f"""
        <div style="margin-bottom:16px">
            <div style="font-family:{_SANS};font-size:0.92rem;font-weight:700;color:{C_TEXT};
                        letter-spacing:-0.02em">Global Trade Flows</div>
            <div style="font-family:{_SANS};font-size:0.76rem;color:{C_TEXT3};margin-top:2px">
                Mapping what the world ships — commodity flows by route, region, and vessel type</div>
        </div>
        """, unsafe_allow_html=True)

        # 1. Flow map
        _render_flow_map()

        # 2. Sankey + Route breakdown side by side
        left, right = st.columns([3, 2])
        with left:
            st.markdown(_card_html("Commodity Flow Sankey", "Origin region → commodity → destination region"), unsafe_allow_html=True)
            _render_sankey()
        with right:
            _render_route_breakdown()

        # 3. Commodity deep dive
        st.markdown(f"""
        <div style="margin:20px 0 12px">
            <div style="font-family:{_SANS};font-size:0.82rem;font-weight:700;color:{C_TEXT}">
                Commodity Intelligence</div>
            <div style="font-family:{_SANS};font-size:0.68rem;color:{C_TEXT3};margin-top:2px">
                Demand signals, seasonal patterns, and key routes by cargo type</div>
        </div>
        """, unsafe_allow_html=True)
        _render_commodity_cards()

    except Exception as exc:
        logger.error(f"tab_trade_flows.render fatal: {exc}")
        st.error(f"Trade Flows error: {exc}")
