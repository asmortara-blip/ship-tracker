"""tab_port_demand.py — Port Demand Forecasting: throughput forecasts, demand
drivers, regional comparison, seasonality, shock scenarios, and capacity headroom."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Local color tokens for region map (domain-specific, not palette globals) ──
_C_PURPLE = "#5b4a8a"
_C_CYAN   = "#2e6b7a"

_REGION_COLORS = {
    "Asia-Pacific": C_ACCENT,
    "Europe":       C_HIGH,
    "Americas":     C_MOD,
    "Middle East":  _C_PURPLE,
    "Africa":       _C_CYAN,
}


# ── Cell formatters for wsj_market_table() ────────────────────────────────
# wsj_market_table renders cell strings as raw HTML inside <td>. These helpers
# only style *content* (font family + conditional color); table CSS handles
# alignment and rule lines. Mirrors the pattern in ui/tab_rate_analytics.py.

def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content — permitted inline span."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content — permitted inline span."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------
_PORTS = [
    # name,           region,          curr_M_TEU, f3m_M_TEU, f12m_M_TEU, util_pct, cap_M_TEU, yoy_pct, driver
    ("Shanghai",      "Asia-Pacific",  49.2,  50.1,  52.8,  88, 57.0,  3.1, "Electronics export growth"),
    ("Singapore",     "Asia-Pacific",  38.1,  38.9,  40.5,  82, 50.0,  2.4, "Transshipment hub stability"),
    ("Ningbo",        "Asia-Pacific",  34.9,  35.8,  37.6,  85, 42.0,  4.2, "China manufacturing diversification"),
    ("Shenzhen",      "Asia-Pacific",  29.4,  30.0,  31.5,  79, 38.0,  2.8, "Tech goods demand"),
    ("Guangzhou",     "Asia-Pacific",  24.6,  25.2,  26.8,  76, 33.0,  3.5, "Consumer goods exports"),
    ("Qingdao",       "Asia-Pacific",  22.0,  22.5,  23.9,  74, 30.0,  2.1, "Northern China commodities"),
    ("Busan",         "Asia-Pacific",  21.8,  22.3,  23.5,  81, 28.0,  2.6, "Korean exports + transship"),
    ("Rotterdam",     "Europe",        14.6,  14.9,  15.7,  73, 22.0,  1.4, "European manufacturing demand"),
    ("Antwerp",       "Europe",        12.0,  12.3,  12.9,  78, 17.0,  1.8, "Benelux trade recovery"),
    ("Hamburg",       "Europe",         8.1,   8.3,   8.7,  69, 12.5,  0.9, "German export moderation"),
    ("Los Angeles",   "Americas",      10.2,  10.6,  11.4,  77, 14.5,  3.8, "US consumer demand resilience"),
    ("Long Beach",    "Americas",       9.6,   9.9,  10.5,  74, 13.5,  3.2, "Intermodal rail capacity"),
    ("New York",      "Americas",       7.4,   7.6,   8.0,  71, 10.8,  2.0, "East Coast nearshoring flow"),
    ("Dubai (DP World)", "Middle East", 15.0, 15.5,  16.4,  80, 20.0,  3.6, "Transshipment; India–Africa gateway"),
    ("Tanjung Pelepas", "Asia-Pacific", 11.2, 11.6,  12.3,  75, 16.0,  3.1, "Maersk/MSC hub expansion"),
]

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_SEASONAL = {
    "Shanghai":         [72, 68, 65, 78, 85, 88, 92, 95, 100, 98, 90, 80],
    "Rotterdam":        [75, 72, 78, 82, 85, 88, 80, 78, 90, 95, 92, 82],
    "Los Angeles":      [88, 82, 78, 84, 90, 92, 95, 100, 98, 95, 85, 90],
    "Singapore":        [82, 80, 85, 88, 90, 87, 85, 88, 92, 95, 90, 85],
    "Dubai (DP World)": [90, 88, 85, 80, 75, 70, 68, 72, 80, 88, 92, 95],
}

_SHOCK_SCENARIOS = {
    "US–China Trade War Escalation (+25% tariffs)": {
        "Shanghai":    -14.0,
        "Ningbo":      -11.0,
        "Shenzhen":    -16.0,
        "Los Angeles": -10.0,
        "Long Beach":   -9.0,
        "Singapore":    +3.0,
        "Rotterdam":    -3.0,
    },
    "Suez Canal Closure (90-day)": {
        "Rotterdam":    -8.0,
        "Antwerp":      -7.0,
        "Hamburg":      -6.0,
        "Singapore":   +12.0,
        "Dubai (DP World)": -18.0,
        "Los Angeles":  +4.0,
    },
    "Global Recession (GDP -2%)": {
        "Shanghai":    -8.0,
        "Singapore":   -6.0,
        "Rotterdam":   -7.0,
        "Los Angeles": -9.0,
        "Hamburg":     -5.0,
        "Dubai (DP World)": -5.0,
    },
    "ASEAN Manufacturing Boom (+20% output)": {
        "Singapore":  +11.0,
        "Tanjung Pelepas": +14.0,
        "Shanghai":    -3.0,
        "Shenzhen":    -5.0,
        "Los Angeles":  +4.0,
        "Rotterdam":    +3.0,
    },
}

# GDP→trade volume→port demand elasticities (illustrative)
_ELASTICITIES = [
    ("Advanced Economies",   0.9, 1.6, 1.2),
    ("Emerging Asia",        1.2, 2.1, 1.8),
    ("Latin America",        1.0, 1.7, 1.4),
    ("Africa",               1.1, 2.3, 2.0),
    ("Middle East",          0.8, 1.4, 1.1),
    ("Eastern Europe",       1.0, 1.8, 1.5),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _util_color(pct: int) -> str:
    if pct >= 90:
        return C_LOW
    if pct >= 80:
        return C_MOD
    return C_HIGH


def _overflow_badge(pct: int) -> str:
    """Return a design-system badge for utilisation overflow risk."""
    if pct >= 90:
        return badge("CRITICAL", color=C_LOW)
    if pct >= 80:
        return badge("ELEVATED", color=C_MOD)
    if pct >= 70:
        return badge("MODERATE", color=C_ACCENT)
    return badge("LOW", color=C_HIGH)


def _yoy_cell(pct: float) -> str:
    """Return a mono cell string for a YoY % value."""
    color = C_HIGH if pct > 0 else C_LOW
    sign  = "+" if pct > 0 else "-"
    return _mono(f"{sign}{abs(pct):.1f}%", color=color)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_hero() -> None:
    try:
        metric_card_row([
            {"label": "Global Container Throughput", "value": "842M TEU",
             "accent": C_HIGH,   "sublabel": "+3.1% YoY"},
            {"label": "Demand Index",                "value": "108.4",
             "accent": C_ACCENT, "sublabel": "+2.7 pts vs Jan 2026"},
            {"label": "12M Forecast Growth",         "value": "+4.2%",
             "accent": C_MOD,    "sublabel": "Confidence: 78%"},
            {"label": "Ports at >80% Utilisation",   "value": "7 / 15",
             "accent": C_LOW,    "sublabel": "Overflow risk elevated"},
        ], columns=4)
        st.markdown(source_footer([
            {"name": "Internal port-demand model", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Port demand hero failed")
        st.error("Hero section unavailable.")


def _render_forecast_table() -> None:
    try:
        section_header(
            "Port Demand Forecast Table",
            "15 major ports - current throughput, 3M & 12M forecasts, key demand drivers",
        )
        rows = []
        for port, region, curr, f3, f12, util, cap, yoy, driver in _PORTS:
            rc = _REGION_COLORS.get(region, C_TEXT3)
            yoy_color = C_HIGH if yoy > 0 else C_LOW
            yoy_sign  = "+" if yoy > 0 else "-"
            rows.append([
                _sans(port, color=C_TEXT, weight=700),
                badge(region, color=rc),
                _mono(f"{curr:.1f}"),
                _mono(f"{f3:.1f}",  color=C_HIGH),
                _mono(f"{f12:.1f}", color=C_HIGH),
                _mono(f"{yoy_sign}{abs(yoy):.1f}%", color=yoy_color),
                _sans(driver, color=C_TEXT3),
            ])
        wsj_market_table(
            ["Port", "Region", "Current (M TEU)", "3M Fcst", "12M Fcst", "YoY %", "Key Driver"],
            rows,
        )
        st.markdown(source_footer([
            {"name": "Internal port-demand forecast", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Forecast table render failed")
        st.error("Port forecast table unavailable.")


def _render_demand_drivers() -> None:
    try:
        section_header(
            "Demand Driver Analysis",
            "GDP growth - trade volume - port throughput elasticity chain",
        )
        rows = []
        for region, gdp_e, trade_e, port_e in _ELASTICITIES:
            rows.append([
                _sans(region, color=C_TEXT, weight=700),
                _mono(f"{gdp_e:.1f}x",   color=C_ACCENT),
                _mono(f"{trade_e:.1f}x", color=C_MOD),
                _mono(f"{port_e:.1f}x",  color=C_HIGH),
            ])
        wsj_market_table(
            ["Region", "GDP Elast.", "Trade Elast.", "Port Elast."],
            rows,
        )
        st.caption(
            "Elasticity = % change in output per 1% change in input. "
            "E.g. port elasticity of 1.8x means a 1% GDP rise yields "
            "+1.8% TEU throughput growth."
        )
        st.markdown(source_footer([
            {"name": "GDP/trade elasticity calibration", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Demand drivers render failed")
        st.error("Demand driver analysis unavailable.")


def _render_regional_comparison() -> None:
    try:
        section_header(
            "Regional Demand Comparison",
            "Asia-Pacific vs Europe vs Americas vs Middle East - throughput (M TEU)",
        )
        regions: dict[str, dict[str, float]] = {}
        for port, region, curr, f3, f12, *_ in _PORTS:
            regions.setdefault(region, {"curr": 0.0, "f3": 0.0, "f12": 0.0})
            regions[region]["curr"] += curr
            regions[region]["f3"]   += f3
            regions[region]["f12"]  += f12
        reg_names = list(regions.keys())
        curr_vals = [round(regions[r]["curr"], 1) for r in reg_names]
        f3_vals   = [round(regions[r]["f3"],   1) for r in reg_names]
        f12_vals  = [round(regions[r]["f12"],  1) for r in reg_names]
        colors = [_REGION_COLORS.get(r, C_TEXT3) for r in reg_names]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Current", x=reg_names, y=curr_vals,
            marker_color=colors,
            opacity=0.6,
            text=[f"{v:.0f}M" for v in curr_vals],
            textposition="outside", textfont=dict(color=C_TEXT2, size=10),
        ))
        fig.add_trace(go.Bar(
            name="3M Forecast", x=reg_names, y=f3_vals,
            marker_color=colors,
            text=[f"{v:.0f}M" for v in f3_vals],
            textposition="outside", textfont=dict(color=C_TEXT2, size=10),
        ))
        fig.add_trace(go.Bar(
            name="12M Forecast", x=reg_names, y=f12_vals,
            marker_color=[C_HIGH] * len(reg_names),
            text=[f"{v:.0f}M" for v in f12_vals],
            textposition="outside", textfont=dict(color=C_TEXT2, size=10),
        ))
        apply_dark_layout(fig, height=340, showlegend=True)
        fig.update_layout(
            barmode="group",
            margin=dict(t=20, b=10, l=10, r=10),
            yaxis={"title": "M TEU"},
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer([
            {"name": "Regional throughput rollup", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Regional comparison render failed")
        st.error("Regional comparison chart unavailable.")


def _render_seasonal_heatmap() -> None:
    try:
        section_header(
            "Seasonal Demand Patterns",
            "Port throughput index by month - 100 = peak month",
        )
        ports_sel = list(_SEASONAL.keys())
        z = [_SEASONAL[p] for p in ports_sel]
        fig = go.Figure(go.Heatmap(
            z=z,
            x=_MONTHS,
            y=ports_sel,
            colorscale=[
                [0.0, "#d4e6f1"],
                [0.5, "#5b9bd5"],
                [0.75, C_MOD],
                [1.0, C_HIGH],
            ],
            text=[[f"{v}" for v in row] for row in z],
            texttemplate="%{text}",
            textfont_size=11,
            showscale=True,
            colorbar=dict(
                tickfont_color=C_TEXT2,
                title=dict(text="Index", font_color=C_TEXT3),
            ),
        ))
        apply_dark_layout(fig, height=280, showlegend=False)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer([
            {"name": "Seasonal throughput indices", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Seasonal heatmap render failed")
        st.error("Seasonal heatmap unavailable.")


def _render_shock_scenarios() -> None:
    try:
        section_header(
            "Demand Shock Scenarios",
            "Simulated throughput impact (% change) if macro shock occurs",
        )
        scenario = st.selectbox("Select scenario", list(_SHOCK_SCENARIOS.keys()))
        impacts = _SHOCK_SCENARIOS.get(scenario, {})
        if not impacts:
            st.info("No throughput-impact data for the selected scenario.")
            return
        ports_aff  = list(impacts.keys())
        pct_vals   = list(impacts.values())
        bar_colors = [C_HIGH if v > 0 else C_LOW for v in pct_vals]
        fig = go.Figure(go.Bar(
            x=ports_aff,
            y=pct_vals,
            marker_color=bar_colors,
            text=[f"{'+' if v > 0 else ''}{v:.1f}%" for v in pct_vals],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=11),
        ))
        apply_dark_layout(
            fig,
            height=320,
            showlegend=False,
            yaxis={"title": "Throughput %"},
        )
        fig.update_layout(margin=dict(t=20, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"Scenario: {scenario} — "
            "estimated throughput impact on affected ports. Indirect effects not modelled."
        )
    except Exception:
        logger.exception("Shock scenarios render failed")
        st.error("Demand shock scenarios unavailable.")


def _render_capacity_headroom() -> None:
    try:
        section_header(
            "Capacity Headroom Analysis",
            "Current utilisation vs max capacity — overflow risk rating per port",
        )
        rows = []
        for port, region, curr, f3, f12, util, cap, yoy, driver in _PORTS:
            headroom = round(cap - curr, 1)
            rc       = _REGION_COLORS.get(region, C_TEXT3)
            uc       = _util_color(util)
            rows.append([
                _sans(port,            color=C_TEXT,  weight=700),
                _sans(region,          color=rc),
                _mono(f"{curr:.1f}",   color=C_TEXT),
                _mono(f"{cap:.1f}",    color=C_TEXT2),
                _mono(f"{util}%",      color=uc),
                _mono(f"{headroom:.1f}", color=C_HIGH if headroom > 5 else C_MOD),
                _overflow_badge(util),
            ])
        wsj_market_table(
            ["Port", "Region", "Current (M)", "Capacity (M)", "Utilisation", "Headroom (M)", "Overflow Risk"],
            rows,
        )
        # Legend using sub-section-header class (no inline style)
        st.markdown(
            '<div class="sub-section-header">Overflow risk tiers &nbsp; '
            f'{badge("CRITICAL", color=C_LOW)} 90%+ &nbsp; '
            f'{badge("ELEVATED", color=C_MOD)} 80%+ &nbsp; '
            f'{badge("MODERATE", color=C_ACCENT)} 70%+ &nbsp; '
            f'{badge("LOW", color=C_HIGH)} &lt;70%'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Capacity headroom render failed")
        st.error("Capacity headroom analysis unavailable.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def render(
    port_results=None,
    macro_data=None,
    freight_data=None,
    insights=None,
) -> None:
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('port_demand'):
        try:
            page_header(
                title="Port Demand Forecasting",
                subtitle="15 major ports — 3-month and 12-month throughput forecasts, demand drivers, and shock scenarios",
                badge_text="PORT DEMAND",
                badge_color=C_HIGH,
            )
            _render_hero()
            section_divider("Forecast")
            _render_forecast_table()
            _render_demand_drivers()
            section_divider("Regional & Seasonal")
            _render_regional_comparison()
            _render_seasonal_heatmap()
            section_divider("Scenarios & Capacity")
            _render_shock_scenarios()
            _render_capacity_headroom()
        except Exception:
            logger.exception("tab_port_demand top-level render failed")
            st.error("Port Demand tab encountered an error.")
