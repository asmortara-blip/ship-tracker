"""tab_trade_flows.py — Global Trade Flows dashboard.

Refactored to follow the canonical WSJ design-system migration playbook:
palette constants now live in ``ui.styles``; all headers/cards/tables use
shared helpers; every figure and table carries a ``live_data_badge`` with
honest provenance. The underlying data is still illustrative — it will be
wired to ``data.comtrade_feed.fetch_bilateral_flows()`` in Track B, at
which point the ``DataSource.demo(...)`` calls flip to ``scraped``.
"""
from __future__ import annotations

import calendar

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ports.port_registry import PORTS
from processing.cargo_analyzer import (
    CARGO_CHARACTERISTICS,
    HS_CATEGORIES,
    _REGION_DOMINANT_CARGO,
    _ROUTE_REGIONS,
    _demand_signal,
    _key_insight,
)
from routes.route_registry import ROUTES
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    live_data_badge,
    page_header,
    section_header,
    wsj_market_table,
)


# ── Domain-specific (non-palette) constants ─────────────────────────────────
# Commodity → color lookup. Kept local because it is *semantic* to this tab
# (commodity taxonomy), not part of the shared palette.
COMMODITY_COLORS: dict[str, str] = {
    "electronics": "#3b82f6",
    "machinery":   "#8b5cf6",
    "automotive":  "#06b6d4",
    "chemicals":   "#f59e0b",
    "agriculture": "#22c55e",
    "metals":      "#f97316",
    "apparel":     "#ec4899",
}

COMMODITY_LABELS: dict[str, str] = {k: v["label"] for k, v in HS_CATEGORIES.items()}

_PORTS_BY_LOCODE = {p.locode: p for p in PORTS}

# Estimated annual trade volume per route ($ billions, illustrative).
_ROUTE_VOLUMES: dict[str, int] = {
    "transpacific_eb": 420, "asia_europe": 380, "transpacific_wb": 120,
    "transatlantic": 210, "sea_transpacific_eb": 95, "ningbo_europe": 180,
    "middle_east_to_europe": 85, "middle_east_to_asia": 110,
    "south_asia_to_europe": 65, "intra_asia_china_sea": 150,
    "intra_asia_china_japan": 130, "china_south_america": 75,
    "europe_south_america": 55, "med_hub_to_asia": 40,
    "north_africa_to_europe": 30, "us_east_south_america": 45,
    "longbeach_to_asia": 60,
}


# ── Provenance ──────────────────────────────────────────────────────────────
# Single demo-quality source used until the Comtrade feed is wired up. The
# red pill makes it unambiguous that the figures are synthetic.

def _trade_flow_source() -> DataSource:
    return DataSource(
        name="Illustrative Trade Flows",
        kind="demo",
        quality="demo",
        notes=(
            "Demo data derived from route registry + region dominant-cargo heuristics. "
            "Wire to data.comtrade_feed.fetch_bilateral_flows() in Track B."
        ),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _rgba(hex_color: str, alpha: float) -> str:
    """Local hex → rgba helper (kept because translucent link/arc colors are
    hot-path inside the Plotly traces)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    except Exception:
        return f"rgba(255,255,255,{alpha})"


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content for wsj_market_table."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content for wsj_market_table."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _get_route_cargo_mix(route_id: str) -> dict[str, float]:
    """Return cargo category weights for a route based on origin region."""
    regions = _ROUTE_REGIONS.get(route_id)
    if not regions:
        return {"electronics": 0.25, "machinery": 0.25, "chemicals": 0.25, "metals": 0.25}
    origin_region = regions[0]
    dominant = _REGION_DOMINANT_CARGO.get(origin_region, ["electronics", "machinery"])
    weights: dict[str, float] = {}
    remaining = 1.0
    for i, cat in enumerate(dominant):
        w = max(0.10, (0.55 - i * 0.12))
        weights[cat] = w
        remaining -= w
    others = [c for c in HS_CATEGORIES if c not in weights]
    if others and remaining > 0:
        per = remaining / len(others)
        for c in others:
            weights[c] = per
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def _provenance_pill() -> None:
    """Render the shared data-quality pill above a figure/table."""
    st.html(live_data_badge(_trade_flow_source()))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Global Flow Map
# ══════════════════════════════════════════════════════════════════════════════

def _render_flow_map() -> None:
    try:
        fig = go.Figure()

        # Port markers
        fig.add_trace(go.Scattergeo(
            lat=[p.lat for p in PORTS],
            lon=[p.lon for p in PORTS],
            text=[p.name for p in PORTS],
            mode="markers+text",
            marker=dict(size=6, color=C_ACCENT, opacity=0.8,
                        line=dict(width=0.5, color=C_TEXT3)),
            textposition="top center",
            textfont=dict(size=8, color=C_TEXT3),
            hovertemplate="<b>%{text}</b><extra></extra>",
            showlegend=False,
        ))

        # Route arcs colored by dominant commodity
        legend_added: set[str] = set()
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

        apply_dark_layout(
            fig,
            height=480,
            margin=dict(l=0, r=0, t=10, b=10),
            showlegend=True,
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
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                font=dict(color=C_TEXT2, size=11,
                          family="'Libre Franklin','Inter',system-ui,sans-serif"),
                orientation="h",
                yanchor="bottom", y=1.02,
                xanchor="center", x=0.5,
            ),
        )

        _provenance_pill()
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})

    except Exception as exc:
        logger.warning(f"Flow map render failed: {exc}")
        st.error(f"Flow map error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Commodity Flow Sankey
# ══════════════════════════════════════════════════════════════════════════════

def _render_sankey() -> None:
    try:
        origin_regions = sorted({r[0] for r in _ROUTE_REGIONS.values()})
        commodities = list(HS_CATEGORIES.keys())
        dest_regions = sorted({r[1] for r in _ROUTE_REGIONS.values()})

        all_origins = [f"{r} (origin)" for r in origin_regions]
        all_dests = [f"{r} (dest)" for r in dest_regions]
        all_commodities = [COMMODITY_LABELS.get(c, c.title()) for c in commodities]
        labels = all_origins + all_commodities + all_dests

        origin_idx = {r: i for i, r in enumerate(origin_regions)}
        commodity_idx = {c: len(origin_regions) + i for i, c in enumerate(commodities)}
        dest_idx = {
            r: len(origin_regions) + len(commodities) + i
            for i, r in enumerate(dest_regions)
        }

        sources, targets, values, link_colors = [], [], [], []

        # Origin → Commodity links
        for route_id, (orig_region, _dest_region) in _ROUTE_REGIONS.items():
            if orig_region not in origin_idx:
                continue
            vol = _ROUTE_VOLUMES.get(route_id, 30)
            for cat, weight in _get_route_cargo_mix(route_id).items():
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
        for route_id, (_orig_region, dest_region) in _ROUTE_REGIONS.items():
            if dest_region not in dest_idx:
                continue
            vol = _ROUTE_VOLUMES.get(route_id, 30)
            for cat, weight in _get_route_cargo_mix(route_id).items():
                if cat not in commodity_idx:
                    continue
                flow_val = vol * weight
                if flow_val < 2:
                    continue
                sources.append(commodity_idx[cat])
                targets.append(dest_idx[dest_region])
                values.append(flow_val)
                link_colors.append(_rgba(COMMODITY_COLORS.get(cat, C_ACCENT), 0.4))

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

        apply_dark_layout(
            fig,
            height=450,
            margin=dict(l=10, r=10, t=10, b=10),
            showlegend=False,
        )

        _provenance_pill()
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})

    except Exception as exc:
        logger.warning(f"Sankey render failed: {exc}")
        st.error(f"Sankey error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Route Cargo Breakdown (WSJ market table)
# ══════════════════════════════════════════════════════════════════════════════

def _render_route_breakdown() -> None:
    try:
        headers = ["Route", "Volume", "Top Commodity", "Second", "Mix"]
        rows = []

        for route in ROUTES:
            cargo_mix = _get_route_cargo_mix(route.id)
            vol = _ROUTE_VOLUMES.get(route.id, 30)
            sorted_cats = sorted(cargo_mix.items(), key=lambda kv: -kv[1])

            # Inline stacked-bar "mix" visual (pure HTML inside the cell)
            bar_segments = ""
            for cat, weight in sorted_cats:
                if weight < 0.03:
                    continue
                color = COMMODITY_COLORS.get(cat, C_ACCENT)
                pct = weight * 100
                label = COMMODITY_LABELS.get(cat, cat.title())
                bar_segments += (
                    f'<span style="display:inline-block;width:{pct}%;height:8px;'
                    f'background:{color};" title="{label}: {pct:.0f}%"></span>'
                )
            mix_cell = (
                f'<span style="display:inline-block;width:140px;height:8px;'
                f'border-radius:3px;overflow:hidden;background:{_rgba(C_TEXT, 0.06)};'
                f'font-size:0;line-height:0;vertical-align:middle;">'
                f'{bar_segments}</span>'
            )

            top = sorted_cats[0] if sorted_cats else ("electronics", 0.0)
            second = sorted_cats[1] if len(sorted_cats) > 1 else ("", 0.0)

            top_label = COMMODITY_LABELS.get(top[0], top[0].title())
            second_label = (
                COMMODITY_LABELS.get(second[0], second[0].title())
                if second[0] else "—"
            )
            top_badge = badge(
                f"{top_label} {top[1]:.0%}",
                color=COMMODITY_COLORS.get(top[0], C_ACCENT),
            )
            second_badge = (
                badge(
                    f"{second_label} {second[1]:.0%}",
                    color=COMMODITY_COLORS.get(second[0], C_TEXT2),
                )
                if second[0] else _sans("—", color=C_TEXT3)
            )

            rows.append([
                _sans(route.name, color=C_TEXT, weight=600),
                _mono(f"${vol}B", color=C_TEXT),
                top_badge,
                second_badge,
                mix_cell,
            ])

        _provenance_pill()
        wsj_market_table(headers, rows)

    except Exception as exc:
        logger.warning(f"Route breakdown render failed: {exc}")
        st.error(f"Route breakdown error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Commodity Deep Dive Cards
# ══════════════════════════════════════════════════════════════════════════════

def _top_routes_for(cat_key: str, limit: int = 3) -> str:
    """Comma-joined top-N route names for a given commodity."""
    matches: list[tuple[str, float]] = []
    for route in ROUTES:
        mix = _get_route_cargo_mix(route.id)
        if cat_key in mix and mix[cat_key] >= 0.15:
            matches.append((route.name, mix[cat_key]))
    matches.sort(key=lambda kv: -kv[1])
    return ", ".join(n for n, _ in matches[:limit]) if matches else "Various"


def _render_commodity_cards() -> None:
    try:
        headers = [
            "Commodity", "Signal", "YoY", "Peak",
            "Vessel", "Sensitivity", "Top Routes",
        ]
        rows = []

        for cat_key, cat_meta in HS_CATEGORIES.items():
            chars = CARGO_CHARACTERISTICS.get(cat_key, {})
            label = cat_meta["label"]
            color = COMMODITY_COLORS.get(cat_key, C_ACCENT)
            yoy = chars.get("yoy_growth", 0)
            peak = chars.get("seasonal_peak", 6)
            shipping = chars.get("shipping", "container")
            sensitivity = chars.get("sensitivity", "moderate")
            signal, signal_color = _demand_signal(yoy)

            rows.append([
                _sans(label, color=color, weight=700),
                badge(signal, color=signal_color),
                _mono(f"{yoy:+.1f}%", color=C_HIGH if yoy > 0 else C_LOW),
                _sans(calendar.month_abbr[peak], color=C_TEXT),
                _sans(shipping, color=C_TEXT2),
                _sans(sensitivity.title(), color=C_TEXT2),
                _sans(_top_routes_for(cat_key), color=C_TEXT3),
            ])

        _provenance_pill()
        wsj_market_table(headers, rows)

        # Key insights list (one per commodity, compact)
        st.html('<div class="sub-section-header">Key Insights</div>')
        for cat_key, cat_meta in HS_CATEGORIES.items():
            chars = CARGO_CHARACTERISTICS.get(cat_key, {})
            yoy = chars.get("yoy_growth", 0)
            signal, _ = _demand_signal(yoy)
            insight = _key_insight(cat_key, yoy, signal)
            color = COMMODITY_COLORS.get(cat_key, C_ACCENT)
            label = cat_meta["label"]
            st.html(
                f'<div style="border-left:2px solid {color};padding:6px 12px;'
                f'margin-bottom:6px;font-family:var(--sans);font-size:0.82rem;'
                f'color:{C_TEXT2};line-height:1.5;">'
                f'<span style="color:{C_TEXT};font-weight:600;">{label}.</span> '
                f'{insight}'
                f'</div>'
            )

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
        # 1. Page header
        page_header(
            title="Global Trade Flows",
            subtitle=(
                "Mapping what the world ships — commodity flows by route, "
                "region, and vessel type"
            ),
            badge_text="Demo Data",
            badge_color=C_MOD,
        )

        # 2. Flow map
        section_header(
            "Bilateral Trade Arcs",
            subtitle=(
                "Route arcs coloured by dominant commodity; arc width scaled to "
                "illustrative annual trade value ($B)"
            ),
        )
        _render_flow_map()

        # 3. Sankey + Route breakdown side by side
        section_header(
            "Origin → Commodity → Destination",
            subtitle="How bulk flows resolve through commodity categories",
        )
        left, right = st.columns([3, 2])
        with left:
            st.html('<div class="sub-section-header">Commodity Flow Sankey</div>')
            _render_sankey()
        with right:
            st.html('<div class="sub-section-header">Route Cargo Breakdown</div>')
            _render_route_breakdown()

        # 4. Commodity deep dive
        section_header(
            "Commodity Intelligence",
            subtitle="Demand signals, seasonal patterns, and key routes by cargo type",
        )
        _render_commodity_cards()

    except Exception as exc:
        logger.error(f"tab_trade_flows.render fatal: {exc}")
        st.error(f"Trade Flows error: {exc}")
