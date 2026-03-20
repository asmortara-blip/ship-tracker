"""ui/tab_carriers.py — Container Carrier Intelligence tab.

Renders carrier market structure, alliance maps, schedule reliability,
stock performance proxies, blank sailing tracking, and HHI concentration.

Integration (add to app.py tabs):
    from ui.tab_carriers import render as render_carriers
    with tab_carriers:
        render_carriers(route_results, freight_data, stock_data)
"""
from __future__ import annotations

import math
import random
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from processing.carrier_tracker import (
    ALLIANCES,
    CARRIER_PROFILES,
    AllianceProfile,
    CarrierProfile,
    compute_blank_sailing_rate,
    compute_carrier_hhi,
    get_route_carrier_coverage,
)
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    _hex_to_rgba,
    dark_layout,
    section_header,
)

# ── Local color constants ─────────────────────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_GREEN   = "#10b981"
_C_RED     = "#ef4444"
_C_AMBER   = "#f59e0b"
_C_BLUE    = "#3b82f6"
_C_PURPLE  = "#8b5cf6"
_C_CYAN    = "#06b6d4"
_C_ORANGE  = "#f97316"

# Alliance color palette (consistent across all charts)
_ALLIANCE_COLORS: dict[str, str] = {
    "Gemini Cooperation": _C_BLUE,
    "Ocean Alliance":     _C_GREEN,
    "Premier Alliance":   _C_PURPLE,
    "MSC Independent":    _C_AMBER,
    None:                 _C_CYAN,         # independent carriers (ZIM, PIL)
}

# Carrier HQ coordinates [lat, lon] for globe chart
_CARRIER_HQ: dict[str, tuple[float, float]] = {
    "MSC":         (46.2044,  6.1432),   # Geneva, Switzerland
    "Maersk":      (55.6761, 12.5683),   # Copenhagen, Denmark
    "CMA CGM":     (43.2965,  5.3698),   # Marseille, France
    "COSCO":       (39.9042, 116.4074),  # Beijing, China
    "Hapag-Lloyd": (53.5511,  9.9937),   # Hamburg, Germany
    "ONE":         (35.6762, 139.6503),  # Tokyo, Japan
    "Evergreen":   (25.0330, 121.5654),  # Taipei, Taiwan
    "Yang Ming":   (25.0330, 121.5654),  # Taipei, Taiwan (offset slightly below)
    "ZIM":         (32.7940,  34.9896),  # Haifa, Israel
    "PIL":         (1.3521,  103.8198),  # Singapore
}


# ── Helper utilities ──────────────────────────────────────────────────────────

def _alliance_color(alliance: Optional[str]) -> str:
    """Return the hex color for a given alliance name (or None for independents)."""
    return _ALLIANCE_COLORS.get(alliance, _C_CYAN)


def _carrier_alliance_color(carrier_name: str) -> str:
    """Return the alliance color for a specific carrier."""
    profile = CARRIER_PROFILES.get(carrier_name)
    if profile is None:
        return _C_CYAN
    return _alliance_color(profile.alliance)


def _health_color(health: str) -> str:
    return {
        "STRONG": _C_GREEN,
        "STABLE": _C_BLUE,
        "WEAK":   _C_RED,
    }.get(health, _C_AMBER)


def _make_synthetic_stock_series(
    ticker: str,
    days: int = 90,
    seed: Optional[int] = None,
) -> pd.Series:
    """Generate a plausible 90-day synthetic stock price series.

    Uses a geometric Brownian motion walk seeded from the ticker name so
    results are reproducible across renders.
    """
    rng = random.Random(seed if seed is not None else hash(ticker) % 99999)
    prices = [100.0]
    for _ in range(days - 1):
        drift = rng.gauss(0.0003, 0.022)
        prices.append(round(prices[-1] * (1 + drift), 2))
    idx = pd.date_range(end=pd.Timestamp("2025-03-19"), periods=days, freq="D")
    return pd.Series(prices, index=idx, name=ticker)


def _make_reliability_trend(
    carrier_name: str,
    days: int = 90,
) -> pd.Series:
    """Generate a synthetic 90-day schedule reliability trend."""
    base = CARRIER_PROFILES[carrier_name].schedule_reliability_pct
    rng = random.Random(hash(carrier_name) % 88888)
    values = []
    current = base
    for _ in range(days):
        shock = rng.gauss(0, 1.5)
        current = max(40.0, min(95.0, current + shock))
        values.append(round(current, 1))
    idx = pd.date_range(end=pd.Timestamp("2025-03-19"), periods=days, freq="D")
    return pd.Series(values, index=idx, name=carrier_name + "_reliability")


# ── Section 1: Alliance Globe Map ─────────────────────────────────────────────

def _render_alliance_globe() -> None:
    section_header(
        "Global Alliance Map",
        "Carrier headquarters colored by alliance — lines connect alliance partners",
    )

    fig = go.Figure()

    # Plot each carrier HQ as a dot
    for carrier_name, (lat, lon) in _CARRIER_HQ.items():
        profile = CARRIER_PROFILES.get(carrier_name)
        if profile is None:
            continue

        color      = _carrier_alliance_color(carrier_name)
        alliance_l = profile.alliance if profile.alliance else "Independent"
        hover_txt  = (
            carrier_name
            + "<br>Alliance: " + alliance_l
            + "<br>Market share: " + str(profile.market_share_pct) + "%"
            + "<br>TEU capacity: " + str(profile.teu_capacity_m) + "M"
            + "<br>Reliability: " + str(profile.schedule_reliability_pct) + "%"
        )

        fig.add_trace(go.Scattergeo(
            lat=[lat],
            lon=[lon],
            mode="markers+text",
            marker={
                "size": 10 + profile.market_share_pct * 1.2,
                "color": color,
                "opacity": 0.90,
                "line": {"color": "rgba(255,255,255,0.25)", "width": 1.2},
            },
            text=[carrier_name],
            textposition="top center",
            textfont={"color": C_TEXT, "size": 10},
            hovertext=[hover_txt],
            hoverinfo="text",
            showlegend=False,
        ))

    # Draw curved lines connecting alliance partners
    alliance_pairs: list[tuple[str, str, str]] = []
    for alliance_name, alliance in ALLIANCES.items():
        members = [m for m in alliance.members if m in _CARRIER_HQ]
        color = _ALLIANCE_COLORS.get(alliance_name, _C_CYAN)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                alliance_pairs.append((members[i], members[j], color))

    for c1, c2, color in alliance_pairs:
        lat1, lon1 = _CARRIER_HQ[c1]
        lat2, lon2 = _CARRIER_HQ[c2]
        # Intermediate great-circle midpoint (simple average for short arcs)
        mid_lat = (lat1 + lat2) / 2 + 5.0
        mid_lon = (lon1 + lon2) / 2
        fig.add_trace(go.Scattergeo(
            lat=[lat1, mid_lat, lat2],
            lon=[lon1, mid_lon, lon2],
            mode="lines",
            line={"color": color, "width": 1.8},
            opacity=0.55,
            hoverinfo="skip",
            showlegend=False,
        ))

    # Legend entries per alliance
    for alliance_name, color in _ALLIANCE_COLORS.items():
        label = alliance_name if alliance_name else "Independent"
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker={"size": 10, "color": color},
            name=label,
            showlegend=True,
        ))

    fig.update_layout(
        geo={
            "showframe":        False,
            "showcoastlines":   True,
            "coastlinecolor":   "rgba(255,255,255,0.12)",
            "showland":         True,
            "landcolor":        "#1a2235",
            "showocean":        True,
            "oceancolor":       _C_BG,
            "showlakes":        False,
            "showcountries":    True,
            "countrycolor":     "rgba(255,255,255,0.06)",
            "projection":       {"type": "orthographic"},
            "bgcolor":          _C_BG,
        },
        paper_bgcolor=_C_BG,
        plot_bgcolor=_C_BG,
        height=400,
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        showlegend=True,
        legend={
            "bgcolor":     "rgba(17,24,39,0.85)",
            "bordercolor": "rgba(255,255,255,0.1)",
            "borderwidth": 1,
            "font":        {"color": C_TEXT2, "size": 11},
            "x": 0.01,
            "y": 0.98,
            "xanchor":    "left",
            "yanchor":    "top",
        },
        font={"color": C_TEXT, "family": "Inter, sans-serif"},
    )

    st.plotly_chart(fig, use_container_width=True, key="carriers_globe_map")


# ── Section 2: Market Share Treemap ──────────────────────────────────────────

def _render_market_share_treemap() -> None:
    section_header(
        "Market Share Treemap",
        "Carrier rectangles sized by TEU capacity — grouped by alliance",
    )

    # Build parent/child structure
    # Top-level parents are alliances; children are carriers
    labels: list[str] = []
    parents: list[str] = []
    values: list[float] = []
    colors: list[str] = []
    hover_texts: list[str] = []

    # Alliance parent nodes
    alliance_teu: dict[str, float] = {}
    for carrier_name, profile in CARRIER_PROFILES.items():
        parent_key = profile.alliance if profile.alliance else "Independent"
        alliance_teu[parent_key] = alliance_teu.get(parent_key, 0.0) + profile.teu_capacity_m

    for alliance_name, teu in alliance_teu.items():
        labels.append(alliance_name)
        parents.append("")
        values.append(teu)
        colors.append(_ALLIANCE_COLORS.get(alliance_name, _C_CYAN))
        hover_texts.append(alliance_name + "<br>Combined TEU: " + str(round(teu, 1)) + "M")

    # Carrier child nodes
    for carrier_name, profile in CARRIER_PROFILES.items():
        parent_key = profile.alliance if profile.alliance else "Independent"
        labels.append(carrier_name)
        parents.append(parent_key)
        values.append(profile.teu_capacity_m)
        colors.append(_carrier_alliance_color(carrier_name))
        hover_texts.append(
            carrier_name
            + "<br>TEU: " + str(profile.teu_capacity_m) + "M"
            + "<br>Share: " + str(profile.market_share_pct) + "%"
            + "<br>Reliability: " + str(profile.schedule_reliability_pct) + "%"
            + "<br>Health: " + profile.financial_health
        )

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        marker={
            "colors": colors,
            "line":   {"width": 1.5, "color": _C_BG},
            "pad":    {"t": 20, "l": 4, "r": 4, "b": 4},
        },
        hovertext=hover_texts,
        hoverinfo="text",
        textinfo="label+value",
        textfont={"size": 12, "color": C_TEXT},
        pathbar={"visible": True, "thickness": 20},
        tiling={"pad": 2},
    ))

    layout = dark_layout(height=380, showlegend=False)
    layout["margin"] = {"l": 8, "r": 8, "t": 30, "b": 8}
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, key="carriers_market_share_treemap")


# ── Section 3: Schedule Reliability Chart ────────────────────────────────────

def _render_reliability_chart() -> None:
    section_header(
        "On-Time Performance (2024)",
        "Schedule reliability % by carrier — sorted best to worst. Industry avg ~68%.",
    )

    # Sort carriers by reliability descending
    sorted_carriers = sorted(
        CARRIER_PROFILES.items(),
        key=lambda kv: kv[1].schedule_reliability_pct,
        reverse=True,
    )

    names         = [p.carrier_name for _, p in sorted_carriers]
    reliabilities = [p.schedule_reliability_pct for _, p in sorted_carriers]
    bar_colors    = [_carrier_alliance_color(n) for n, _ in sorted_carriers]

    # Shorten display names for readability
    short_names = [n.split(" ")[0] if len(n) > 12 else n for n, _ in sorted_carriers]

    industry_avg = 68.0

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=reliabilities,
        y=short_names,
        orientation="h",
        marker={
            "color": bar_colors,
            "line":  {"color": "rgba(255,255,255,0.08)", "width": 1},
        },
        text=[str(r) + "%" for r in reliabilities],
        textposition="outside",
        textfont={"color": C_TEXT, "size": 11},
        hovertemplate="%{y}<br>Reliability: %{x:.1f}%<extra></extra>",
    ))

    # Industry average line
    fig.add_vline(
        x=industry_avg,
        line={"color": _C_AMBER, "dash": "dash", "width": 1.8},
        annotation_text="Industry avg " + str(industry_avg) + "%",
        annotation_position="top",
        annotation_font={"color": _C_AMBER, "size": 10},
    )

    layout = dark_layout(title="Schedule Reliability by Carrier (2024)", height=340)
    layout["xaxis"]["title"] = "On-Time Arrival %"
    layout["xaxis"]["range"] = [50, 85]
    layout["yaxis"]["autorange"] = "reversed"
    layout["margin"] = {"l": 100, "r": 40, "t": 40, "b": 30}
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, key="carriers_reliability_bar")


# ── Section 4: Carrier vs Stock Performance ───────────────────────────────────

def _render_stock_vs_reliability() -> None:
    section_header(
        "Carrier Stock & Reliability Trend",
        "ZIM (NYSE:ZIM) and MATX (Matson — US proxy) 90-day price vs schedule reliability",
    )

    days = 90
    zim_price    = _make_synthetic_stock_series("ZIM",  days, seed=42)
    matx_price   = _make_synthetic_stock_series("MATX", days, seed=77)
    zim_rel      = _make_reliability_trend("ZIM",  days)
    # MATX is a US domestic carrier; use Evergreen reliability as a proxy comparator
    evergreen_rel = _make_reliability_trend("Evergreen", days)

    dates = zim_price.index

    fig = make_subplots(
        rows=1, cols=2,
        specs=[[{"secondary_y": True}, {"secondary_y": True}]],
        subplot_titles=["ZIM — Stock vs Reliability", "MATX — Stock vs Reliability (Proxy)"],
    )

    # ── ZIM ──
    fig.add_trace(
        go.Scatter(
            x=dates, y=zim_price.values,
            name="ZIM Price",
            line={"color": _C_AMBER, "width": 2},
            mode="lines",
            hovertemplate="%{x|%b %d}<br>Price: $%{y:.2f}<extra>ZIM</extra>",
        ),
        row=1, col=1, secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=dates, y=zim_rel.values,
            name="ZIM Reliability %",
            line={"color": _C_RED, "width": 1.5, "dash": "dot"},
            mode="lines",
            hovertemplate="%{x|%b %d}<br>Reliability: %{y:.1f}%<extra>ZIM Rel</extra>",
        ),
        row=1, col=1, secondary_y=True,
    )

    # ── MATX ──
    fig.add_trace(
        go.Scatter(
            x=dates, y=matx_price.values,
            name="MATX Price",
            line={"color": _C_BLUE, "width": 2},
            mode="lines",
            hovertemplate="%{x|%b %d}<br>Price: $%{y:.2f}<extra>MATX</extra>",
        ),
        row=1, col=2, secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=dates, y=evergreen_rel.values,
            name="Evergreen Reliability %",
            line={"color": _C_GREEN, "width": 1.5, "dash": "dot"},
            mode="lines",
            hovertemplate="%{x|%b %d}<br>Reliability: %{y:.1f}%<extra>EG Rel</extra>",
        ),
        row=1, col=2, secondary_y=True,
    )

    # Axes labels
    fig.update_yaxes(
        title_text="Stock Price (USD, indexed)", secondary_y=False, row=1, col=1,
        tickfont={"color": C_TEXT3, "size": 10}, gridcolor="rgba(255,255,255,0.05)",
    )
    fig.update_yaxes(
        title_text="Reliability (%)", secondary_y=True, row=1, col=1,
        tickfont={"color": C_TEXT3, "size": 10}, range=[40, 90],
    )
    fig.update_yaxes(
        title_text="Stock Price (USD, indexed)", secondary_y=False, row=1, col=2,
        tickfont={"color": C_TEXT3, "size": 10}, gridcolor="rgba(255,255,255,0.05)",
    )
    fig.update_yaxes(
        title_text="Reliability (%)", secondary_y=True, row=1, col=2,
        tickfont={"color": C_TEXT3, "size": 10}, range=[40, 90],
    )

    layout = dark_layout(height=340, showlegend=True, legend_orientation="h")
    layout["paper_bgcolor"] = _C_BG
    layout["plot_bgcolor"]  = _C_SURFACE
    layout["margin"] = {"l": 40, "r": 60, "t": 50, "b": 30}
    for ann in layout.get("annotations", []):
        ann["font"] = {"color": C_TEXT2, "size": 11}
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, key="carriers_stock_reliability")

    st.markdown(
        "<div style='font-size:0.72rem;color:" + C_TEXT3 + ";margin-top:-6px;'>"
        "Note: Stock prices are synthetic illustrative series. "
        "Reliability trend generated from carrier baseline with stochastic noise. "
        "MATX is a US Jones Act carrier — used as a listed shipping proxy; "
        "reliability mapped from Evergreen as Asia-Pacific operational comparator."
        "</div>",
        unsafe_allow_html=True,
    )


# ── Section 5: Alliance Impact Analysis ──────────────────────────────────────

_ALLIANCE_ROUTE_CAPACITY: dict[str, dict[str, float]] = {
    "Gemini Cooperation": {
        "Trans-Pacific":  2.10,
        "Asia-Europe":    2.40,
        "Transatlantic":  0.80,
    },
    "Ocean Alliance": {
        "Trans-Pacific":  3.60,
        "Asia-Europe":    3.20,
        "Transatlantic":  0.60,
    },
    "Premier Alliance": {
        "Trans-Pacific":  1.40,
        "Asia-Europe":    1.20,
        "Transatlantic":  0.30,
    },
    "MSC Independent": {
        "Trans-Pacific":  1.80,
        "Asia-Europe":    2.10,
        "Transatlantic":  0.90,
    },
}

_ALLIANCE_RATE_IMPACT: dict[str, str] = {
    "Gemini Cooperation": (
        "Schedule-focused product — fewer void sailings expected. "
        "Reliability premium may support +5-8% rate over spot index."
    ),
    "Ocean Alliance": (
        "Largest combined capacity. Dominant on Asia-Europe and Trans-Pacific. "
        "Rate leverage high; consolidation risk flagged by EC (DG COMP review 2027)."
    ),
    "Premier Alliance": (
        "Mid-tier capacity share. Slot-exchange model limits blank-sailing flexibility. "
        "Rates broadly in line with market — less pricing power than Ocean Alliance."
    ),
    "MSC Independent": (
        "Fully independent since 2M dissolution (Jan 2025). "
        "Can deploy/redeploy capacity freely without partner constraints. "
        "Aggressive pricing history; watch for undercutting during slack periods."
    ),
}

_ALLIANCE_SHIPPER_IMPLICATIONS: dict[str, str] = {
    "Gemini Cooperation": (
        "Book early for premium reliability slots. "
        "Gemini's hub-and-spoke model may add 1-2 day transit on feeder legs."
    ),
    "Ocean Alliance": (
        "Broad port coverage. Rate negotiation leverage limited — "
        "combined capacity means fewer alternatives on core lanes."
    ),
    "Premier Alliance": (
        "Good alternative on Trans-Pacific. "
        "Less reliable than Gemini on average; monitor Q1 2025 performance data."
    ),
    "MSC Independent": (
        "Widest network globally. "
        "Space availability often better than alliances due to sole operator flexibility. "
        "Negotiate annual contracts carefully — spot rate volatility is higher."
    ),
}


def _render_alliance_impact() -> None:
    section_header(
        "Alliance Impact Analysis",
        "Capacity deployed by route, rate implications, and shipper guidance",
    )

    for alliance_name, alliance in ALLIANCES.items():
        color       = _ALLIANCE_COLORS.get(alliance_name, _C_CYAN)
        member_list = ", ".join(alliance.members)
        route_data  = _ALLIANCE_ROUTE_CAPACITY.get(alliance_name, {})
        rate_impact = _ALLIANCE_RATE_IMPACT.get(alliance_name, "N/A")
        shipper_imp = _ALLIANCE_SHIPPER_IMPLICATIONS.get(alliance_name, "N/A")

        with st.expander(alliance_name + "  ·  " + str(round(alliance.combined_share_pct, 1)) + "% market share  ·  " + alliance.status, expanded=False, key=f"carriers_alliance_{alliance_name}"):
            col_a, col_b = st.columns([1, 1])

            with col_a:
                st.markdown(
                    "<div style='font-size:0.8rem;color:" + C_TEXT2 + ";margin-bottom:6px;'>"
                    "<b style='color:" + C_TEXT + ";'>Members:</b> " + member_list + "</div>"
                    "<div style='font-size:0.8rem;color:" + C_TEXT2 + ";margin-bottom:4px;'>"
                    "<b style='color:" + C_TEXT + ";'>Combined TEU:</b> "
                    + str(alliance.combined_teu_m) + "M</div>"
                    "<div style='font-size:0.8rem;color:" + C_TEXT2 + ";margin-bottom:4px;'>"
                    "<b style='color:" + C_TEXT + ";'>Cooperation:</b> "
                    + alliance.cooperation_type + "</div>"
                    "<div style='font-size:0.8rem;color:" + C_TEXT2 + ";margin-bottom:12px;'>"
                    "<b style='color:" + C_TEXT + ";'>Formed:</b> "
                    + alliance.formed_date + "</div>",
                    unsafe_allow_html=True,
                )

                if route_data:
                    routes_display = list(route_data.keys())
                    teus_display   = list(route_data.values())
                    bar_fig = go.Figure(go.Bar(
                        x=routes_display,
                        y=teus_display,
                        marker={"color": color, "opacity": 0.80,
                                "line": {"color": "rgba(255,255,255,0.08)", "width": 1}},
                        text=[str(v) + "M" for v in teus_display],
                        textposition="outside",
                        textfont={"color": C_TEXT, "size": 10},
                        hovertemplate="%{x}<br>Capacity: %{y:.2f}M TEU<extra></extra>",
                    ))
                    bar_layout = dark_layout(height=200, showlegend=False)
                    bar_layout["margin"] = {"l": 20, "r": 20, "t": 20, "b": 40}
                    bar_layout["yaxis"]["title"] = "TEU (M)"
                    bar_fig.update_layout(**bar_layout)
                    st.plotly_chart(bar_fig, use_container_width=True, key=f"carriers_chart_{alliance_name}")

            with col_b:
                st.markdown(
                    "<div style='background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
                    "border-left:3px solid " + color + ";border-radius:8px;"
                    "padding:14px 16px;margin-bottom:10px;'>"
                    "<div style='font-size:0.72rem;color:" + C_TEXT3 + ";text-transform:uppercase;"
                    "letter-spacing:0.06em;margin-bottom:6px;'>Rate Impact</div>"
                    "<div style='font-size:0.82rem;color:" + C_TEXT + ";line-height:1.55;'>"
                    + rate_impact + "</div></div>"
                    "<div style='background:" + C_CARD + ";border:1px solid " + C_BORDER + ";"
                    "border-left:3px solid " + _C_AMBER + ";border-radius:8px;"
                    "padding:14px 16px;'>"
                    "<div style='font-size:0.72rem;color:" + C_TEXT3 + ";text-transform:uppercase;"
                    "letter-spacing:0.06em;margin-bottom:6px;'>Shipper Implications</div>"
                    "<div style='font-size:0.82rem;color:" + C_TEXT + ";line-height:1.55;'>"
                    + shipper_imp + "</div></div>",
                    unsafe_allow_html=True,
                )

            if alliance.notes:
                st.markdown(
                    "<div style='font-size:0.75rem;color:" + C_TEXT3 + ";margin-top:8px;'>"
                    + alliance.notes + "</div>",
                    unsafe_allow_html=True,
                )


# ── Section 6: Blank Sailing Tracker ─────────────────────────────────────────

_BLANK_SAILING_ROUTES = [
    ("transpacific_eb",  "Trans-Pacific EB"),
    ("asia_europe",      "Asia-Europe"),
    ("transatlantic",    "Transatlantic"),
    ("latin_america",    "Latin America"),
]


def _render_blank_sailing_tracker() -> None:
    section_header(
        "Blank Sailing Tracker",
        "Estimated void sailings as % of scheduled departures — higher = more capacity pulled",
    )

    rows: list[dict] = []
    for carrier_name in CARRIER_PROFILES:
        for route_id, route_label in _BLANK_SAILING_ROUTES:
            profile = CARRIER_PROFILES[carrier_name]
            if route_id not in profile.routes_served:
                continue
            rate = compute_blank_sailing_rate(carrier_name, route_id)
            # Implied capacity reduction: blank_rate * share of deployed TEU on that route
            # Approximate: 30% of carrier TEU on any given major route
            deployed_teu = round(profile.teu_capacity_m * 0.30, 2)
            capacity_reduction_teu = round(deployed_teu * rate / 100, 3)
            # Rate impact: higher blank sailing = tighter effective supply = bullish rates
            if rate < 6:
                rate_impact = "Neutral"
                rate_color  = C_TEXT2
            elif rate < 9:
                rate_impact = "Mildly Supportive"
                rate_color  = _C_AMBER
            elif rate < 12:
                rate_impact = "Supportive"
                rate_color  = _C_GREEN
            else:
                rate_impact = "Strongly Supportive"
                rate_color  = _C_CYAN

            rows.append({
                "Carrier":              carrier_name,
                "Route":                route_label,
                "Alliance":             profile.alliance if profile.alliance else "Independent",
                "Blank Sailing Rate":   rate,
                "Capacity Pulled (M TEU)": capacity_reduction_teu,
                "_rate_impact":         rate_impact,
                "_rate_color":          rate_color,
            })

    if not rows:
        st.info("🚢 No blank sailing data available — carrier capacity data uses baseline 2025 figures. Verify carrier configuration and click Refresh All Data.")
        return

    df = pd.DataFrame(rows).sort_values("Blank Sailing Rate", ascending=False)

    # Render as styled HTML table
    table_rows_html = ""
    for _, row in df.iterrows():
        bs_color = _C_RED if row["Blank Sailing Rate"] > 12 else (
            _C_AMBER if row["Blank Sailing Rate"] > 8 else _C_GREEN
        )
        table_rows_html += (
            "<tr>"
            "<td style='padding:7px 12px;color:" + C_TEXT + ";'>" + str(row["Carrier"]) + "</td>"
            "<td style='padding:7px 12px;color:" + C_TEXT2 + ";'>" + str(row["Route"]) + "</td>"
            "<td style='padding:7px 12px;color:" + C_TEXT2 + ";'>" + str(row["Alliance"]) + "</td>"
            "<td style='padding:7px 12px;color:" + bs_color + ";font-weight:600;'>"
            + str(row["Blank Sailing Rate"]) + "%</td>"
            "<td style='padding:7px 12px;color:" + C_TEXT2 + ";'>"
            + str(row["Capacity Pulled (M TEU)"]) + "M</td>"
            "<td style='padding:7px 12px;color:" + str(row["_rate_color"]) + ";'>"
            + str(row["_rate_impact"]) + "</td>"
            "</tr>"
        )

    header_style = (
        "padding:8px 12px;font-size:0.72rem;font-weight:700;color:" + C_TEXT3 + ";"
        "text-transform:uppercase;letter-spacing:0.06em;border-bottom:1px solid " + C_BORDER + ";"
    )
    table_html = (
        "<div style='overflow-x:auto;'>"
        "<table style='width:100%;border-collapse:collapse;font-size:0.83rem;"
        "background:" + C_CARD + ";border-radius:8px;overflow:hidden;'>"
        "<thead><tr>"
        "<th style='" + header_style + "'>Carrier</th>"
        "<th style='" + header_style + "'>Route</th>"
        "<th style='" + header_style + "'>Alliance</th>"
        "<th style='" + header_style + "'>Blank Rate</th>"
        "<th style='" + header_style + "'>Cap. Pulled</th>"
        "<th style='" + header_style + "'>Rate Impact</th>"
        "</tr></thead>"
        "<tbody>" + table_rows_html + "</tbody>"
        "</table></div>"
    )

    st.markdown(table_html, unsafe_allow_html=True)

    csv_df = df[["Carrier", "Route", "Alliance", "Blank Sailing Rate", "Capacity Pulled (M TEU)", "_rate_impact"]].rename(columns={"_rate_impact": "Rate Impact"})
    csv = csv_df.to_csv(index=False)
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name="carrier_data.csv",
        mime="text/csv",
        key="download_carrier_data_csv",
    )

    st.markdown(
        "<div style='font-size:0.72rem;color:" + C_TEXT3 + ";margin-top:8px;'>"
        "Blank sailing rates are synthetic estimates based on carrier financial health, "
        "route demand, and 2024 H2 Sea-Intelligence baseline. Capacity pulled = 30% "
        "of deployed TEU assumption per route.</div>",
        unsafe_allow_html=True,
    )


# ── Section 7: HHI Concentration Gauge ───────────────────────────────────────

def _render_hhi_gauge() -> None:
    section_header(
        "Market Concentration (HHI)",
        "Herfindahl-Hirschman Index for container shipping. Above 2500 = highly concentrated.",
    )

    hhi = compute_carrier_hhi()
    logger.info("Rendering HHI gauge: {:.1f}", hhi)

    if hhi < 1500:
        zone_label = "COMPETITIVE"
        hhi_color  = _C_GREEN
    elif hhi < 2500:
        zone_label = "MODERATELY CONCENTRATED"
        hhi_color  = _C_AMBER
    else:
        zone_label = "HIGHLY CONCENTRATED"
        hhi_color  = _C_RED

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=hhi,
        title={
            "text": "HHI — <b>" + zone_label + "</b>",
            "font": {"color": C_TEXT, "size": 14},
        },
        number={
            "font":   {"color": C_TEXT, "size": 32},
            "suffix": " / 10,000",
        },
        gauge={
            "axis": {
                "range":     [0, 10000],
                "tickwidth": 1,
                "tickcolor": C_TEXT3,
                "tickfont":  {"color": C_TEXT3, "size": 10},
                "tickvals":  [0, 1500, 2500, 5000, 7500, 10000],
                "ticktext":  ["0", "1500", "2500", "5000", "7500", "10K"],
            },
            "bar":      {"color": hhi_color, "thickness": 0.26},
            "bgcolor":  _C_SURFACE,
            "borderwidth": 1,
            "bordercolor": C_BORDER,
            "steps": [
                {"range": [0,    1500], "color": "rgba(16,185,129,0.15)"},    # green — competitive
                {"range": [1500, 2500], "color": "rgba(245,158,11,0.15)"},   # amber — moderate
                {"range": [2500, 10000], "color": "rgba(239,68,68,0.15)"},   # red — concentrated
            ],
            "threshold": {
                "line":      {"color": C_TEXT2, "width": 2},
                "thickness": 0.75,
                "value":     hhi,
            },
        },
    ))

    layout = dark_layout(height=310, showlegend=False)
    layout["paper_bgcolor"] = _C_BG
    layout["margin"] = {"l": 30, "r": 30, "t": 50, "b": 20}
    fig.update_layout(**layout)

    col_gauge, col_legend = st.columns([2, 1])

    with col_gauge:
        st.plotly_chart(fig, use_container_width=True, key="carriers_hhi_gauge")

    with col_legend:
        st.markdown("<div style='margin-top:32px;'></div>", unsafe_allow_html=True)

        zones = [
            (2500, 10000, "HIGHLY CONCENTRATED", _C_RED,
             "Market power concentrated; antitrust scrutiny likely."),
            (1500, 2500,  "MODERATELY CONCENTRATED", _C_AMBER,
             "Some pricing power; regulators monitoring alliances."),
            (0,    1500,  "COMPETITIVE", _C_GREEN,
             "Low concentration; robust competition constrains rates."),
        ]

        for lo, hi, label, color, desc in zones:
            active = lo <= hhi < hi or (hi == 10000 and hhi >= lo)
            bg     = _hex_to_rgba(color, 0.12) if active else "transparent"
            border = "1px solid " + color if active else "1px solid " + C_BORDER

            st.markdown(
                "<div style='background:" + bg + ";border:" + border + ";"
                "border-radius:7px;padding:8px 12px;margin-bottom:8px;'>"
                "<div style='font-size:0.75rem;font-weight:700;color:" + color + ";'>"
                + label + "</div>"
                "<div style='font-size:0.7rem;color:" + C_TEXT3 + ";margin-top:2px;'>"
                "HHI " + str(lo) + " – " + (str(hi) if hi < 10000 else "10,000") + "</div>"
                "<div style='font-size:0.72rem;color:" + C_TEXT2 + ";margin-top:4px;line-height:1.4;'>"
                + desc + "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

        st.markdown(
            "<div style='font-size:0.72rem;color:" + C_TEXT3 + ";margin-top:6px;'>"
            "Current HHI: <b style='color:" + hhi_color + ";'>" + str(round(hhi, 0)) + "</b>"
            "<br>Computed from " + str(len(CARRIER_PROFILES)) + " carriers in registry."
            "</div>",
            unsafe_allow_html=True,
        )


# ── Section 0: Hero KPI strip ─────────────────────────────────────────────────

def _render_hero_kpis() -> None:
    hhi       = compute_carrier_hhi()
    top_share = max(p.market_share_pct for p in CARRIER_PROFILES.values())
    top_name  = next(
        n for n, p in CARRIER_PROFILES.items()
        if p.market_share_pct == top_share
    )
    avg_reliability = round(
        sum(p.schedule_reliability_pct for p in CARRIER_PROFILES.values())
        / len(CARRIER_PROFILES),
        1,
    )
    n_alliances = sum(1 for a in ALLIANCES.values() if a.status == "ACTIVE")
    industry_avg_reliability = 68.0
    reliability_delta = round(avg_reliability - industry_avg_reliability, 1)

    cols = st.columns(4)
    with cols[0]:
        st.metric(
            label="Largest Carrier",
            value=top_name,
            delta=f"{top_share}% market share by TEU",
            delta_color="off",
        )
    with cols[1]:
        st.metric(
            label="Avg On-Time Reliability",
            value=f"{avg_reliability}%",
            delta=f"{reliability_delta:+.1f}pp vs {industry_avg_reliability}% industry avg",
            delta_color="normal",
        )
    with cols[2]:
        st.metric(
            label="Active Alliances",
            value=str(n_alliances),
            delta="Gemini · Ocean · Premier + MSC",
            delta_color="off",
        )
    with cols[3]:
        hhi_zone = "Highly Concentrated" if hhi >= 2500 else ("Moderate" if hhi >= 1500 else "Competitive")
        st.metric(
            label="HHI Index",
            value=f"{round(hhi):,}",
            delta=f"{hhi_zone} (threshold: 2,500)",
            delta_color="inverse" if hhi >= 2500 else "off",
        )


# ── Main render entry point ───────────────────────────────────────────────────

def render(
    route_results=None,
    freight_data=None,
    stock_data=None,
) -> None:
    """Render the Carrier Intelligence tab.

    Parameters
    ----------
    route_results:
        Route analysis results from the main app engine (currently unused;
        carrier data is sourced from processing.carrier_tracker directly).
    freight_data:
        Freight rate data dict (available for future rate overlays).
    stock_data:
        Stock market data dict (available for future live price feeds).
    """
    logger.info("Rendering Carriers tab")

    _render_hero_kpis()
    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)

    _render_alliance_globe()
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    col_l, col_r = st.columns([1, 1])
    with col_l:
        _render_market_share_treemap()
    with col_r:
        _render_reliability_chart()

    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_stock_vs_reliability()
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_alliance_impact()
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_blank_sailing_tracker()
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_hhi_gauge()
