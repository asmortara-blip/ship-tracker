"""tab_port_demand.py — Port Demand Forecasting: throughput forecasts, demand
drivers, regional comparison, seasonality, shock scenarios, and capacity headroom."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"

_REGION_COLORS = {
    "Asia-Pacific": C_ACCENT,
    "Europe":       C_HIGH,
    "Americas":     C_MOD,
    "Middle East":  C_PURPLE,
    "Africa":       C_CYAN,
}

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
def _kpi_card(label: str, value: str, delta: str = "", color: str = C_HIGH) -> None:
    delta_html = (
        f'<div style="font-size:0.72rem;color:{color};margin-top:2px;">{delta}</div>'
        if delta else ""
    )
    st.markdown(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:16px 18px;text-align:center;">'
        f'<div style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">{label}</div>'
        f'<div style="font-size:1.6rem;font-weight:700;color:{C_TEXT};">{value}</div>'
        f'{delta_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _section_header(title: str, subtitle: str = "") -> None:
    sub = (
        f'<div style="font-size:0.82rem;color:{C_TEXT3};margin-top:2px;">{subtitle}</div>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="margin:28px 0 12px;">'
        f'<div style="font-size:1.05rem;font-weight:600;color:{C_TEXT};">{title}</div>'
        f'{sub}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _util_color(pct: int) -> str:
    if pct >= 90:
        return C_LOW
    if pct >= 80:
        return C_MOD
    return C_HIGH


def _overflow_badge(pct: int) -> str:
    if pct >= 90:
        label, color = "CRITICAL", C_LOW
    elif pct >= 80:
        label, color = "ELEVATED", C_MOD
    elif pct >= 70:
        label, color = "MODERATE", C_ACCENT
    else:
        label, color = "LOW", C_HIGH
    return (
        f'<span style="background:{color}22;color:{color};font-size:0.7rem;'
        f'font-weight:700;padding:2px 8px;border-radius:4px;">{label}</span>'
    )


def _yoy_html(pct: float) -> str:
    color = C_HIGH if pct > 0 else C_LOW
    arrow = "▲" if pct > 0 else "▼"
    return f'<span style="color:{color};font-weight:600;">{arrow} {abs(pct):.1f}%</span>'


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _render_hero() -> None:
    try:
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{C_CARD},{C_SURFACE});'
            f'border:1px solid {C_BORDER};border-radius:14px;padding:24px 28px;margin-bottom:20px;">'
            f'<div style="font-size:1.4rem;font-weight:700;color:{C_TEXT};">Port Demand Forecasting</div>'
            f'<div style="font-size:0.85rem;color:{C_TEXT2};margin-top:4px;">'
            f'15 major ports · 3-month and 12-month forecasts · Demand shock scenarios</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _kpi_card("Global Container Throughput", "842M TEU", "▲ 3.1% YoY", C_HIGH)
        with c2:
            _kpi_card("Demand Index", "108.4", "▲ 2.7 pts vs Jan 2026", C_ACCENT)
        with c3:
            _kpi_card("12M Forecast Growth", "+4.2%", "Confidence: 78%", C_MOD)
        with c4:
            _kpi_card("Ports at >80% Utilisation", "7 / 15", "Overflow risk elevated", C_LOW)
    except Exception:
        logger.exception("Port demand hero failed")
        st.error("Hero section unavailable.")


def _render_forecast_table() -> None:
    try:
        _section_header("Port Demand Forecast Table", "15 major ports — current throughput, 3M & 12M forecasts, key demand drivers")
        cols = "1.4fr 1fr 0.8fr 0.8fr 0.8fr 0.7fr 1.8fr"
        header_html = (
            f'<div style="display:grid;grid-template-columns:{cols};'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 14px;">'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Port</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Region</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Current (M TEU)</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">3M Fcst</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">12M Fcst</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">YoY %</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Key Driver</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (port, region, curr, f3, f12, util, cap, yoy, driver) in enumerate(_PORTS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rc = _REGION_COLORS.get(region, C_TEXT3)
            rows_html += (
                f'<div style="display:grid;grid-template-columns:{cols};'
                f'gap:0;background:{bg};padding:9px 14px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{port}</span>'
                f'<span style="font-size:0.75rem;color:{rc};">{region}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT};">{curr:.1f}</span>'
                f'<span style="font-size:0.82rem;color:{C_HIGH};">{f3:.1f}</span>'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_HIGH};">{f12:.1f}</span>'
                f'<span style="font-size:0.82rem;">{_yoy_html(yoy)}</span>'
                f'<span style="font-size:0.75rem;color:{C_TEXT3};">{driver}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception:
        logger.exception("Forecast table render failed")
        st.error("Port forecast table unavailable.")


def _render_demand_drivers() -> None:
    try:
        _section_header("Demand Driver Analysis", "GDP growth → trade volume → port throughput elasticity chain")
        cols_label = "1.4fr 0.8fr 0.8fr 0.8fr"
        header_html = (
            f'<div style="display:grid;grid-template-columns:{cols_label};'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 16px;">'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Region</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">GDP Elast.</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Trade Elast.</span>'
            f'<span style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;">Port Elast.</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (region, gdp_e, trade_e, port_e) in enumerate(_ELASTICITIES):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            rows_html += (
                f'<div style="display:grid;grid-template-columns:{cols_label};'
                f'gap:0;background:{bg};padding:9px 16px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{region}</span>'
                f'<span style="font-size:0.82rem;color:{C_ACCENT};">{gdp_e:.1f}x</span>'
                f'<span style="font-size:0.82rem;color:{C_MOD};">{trade_e:.1f}x</span>'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_HIGH};">{port_e:.1f}x</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.75rem;color:{C_TEXT3};margin-top:6px;padding:0 2px;">'
            f'Elasticity = % change in output per 1% change in input. '
            f'E.g. port elasticity of 1.8x means a 1% GDP rise yields +1.8% TEU throughput growth.</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Demand drivers render failed")
        st.error("Demand driver analysis unavailable.")


def _render_regional_comparison() -> None:
    try:
        _section_header("Regional Demand Comparison", "Asia-Pacific vs Europe vs Americas vs Middle East — throughput (M TEU)")
        regions = {}
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
            marker_color=[c + "99" for c in colors],
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
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color=C_TEXT,
            barmode="group",
            xaxis=dict(tickfont_color=C_TEXT2, gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(
                tickfont_color=C_TEXT2, gridcolor="rgba(255,255,255,0.04)",
                title="M TEU", title_font_color=C_TEXT3,
            ),
            legend=dict(font_color=C_TEXT2, bgcolor="rgba(0,0,0,0)"),
            margin=dict(t=20, b=10, l=10, r=10),
            height=340,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        logger.exception("Regional comparison render failed")
        st.error("Regional comparison chart unavailable.")


def _render_seasonal_heatmap() -> None:
    try:
        _section_header("Seasonal Demand Patterns", "Port throughput index by month — 100 = peak month")
        ports_sel = list(_SEASONAL.keys())
        z = [_SEASONAL[p] for p in ports_sel]
        fig = go.Figure(go.Heatmap(
            z=z,
            x=_MONTHS,
            y=ports_sel,
            colorscale=[
                [0.0, "#1e3a5f"],
                [0.5, C_ACCENT],
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
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color=C_TEXT,
            xaxis=dict(tickfont_color=C_TEXT2),
            yaxis=dict(tickfont_color=C_TEXT2),
            margin=dict(t=10, b=10, l=10, r=10),
            height=280,
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        logger.exception("Seasonal heatmap render failed")
        st.error("Seasonal heatmap unavailable.")


def _render_shock_scenarios() -> None:
    try:
        _section_header("Demand Shock Scenarios", "Simulated throughput impact (% change) if macro shock occurs")
        scenario = st.selectbox("Select scenario", list(_SHOCK_SCENARIOS.keys()))
        impacts = _SHOCK_SCENARIOS.get(scenario, {})
        if not impacts:
            st.info("No impact data for selected scenario.")
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
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color=C_TEXT,
            xaxis=dict(tickfont_color=C_TEXT2, gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(
                tickfont_color=C_TEXT2, gridcolor="rgba(255,255,255,0.04)",
                title="Throughput Δ%", title_font_color=C_TEXT3,
                zeroline=True, zerolinecolor=C_BORDER, zerolinewidth=1,
            ),
            margin=dict(t=20, b=10, l=10, r=10),
            height=320,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(
            f'<div style="font-size:0.78rem;color:{C_TEXT3};padding:0 2px;margin-top:2px;">'
            f'Scenario: <span style="color:{C_TEXT2};">{scenario}</span> — '
            f'estimated throughput impact on affected ports. Indirect effects not modelled.</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Shock scenarios render failed")
        st.error("Demand shock scenarios unavailable.")


def _render_capacity_headroom() -> None:
    try:
        _section_header("Capacity Headroom Analysis", "Current utilisation vs max capacity — overflow risk rating per port")
        cols = "1.4fr 1fr 0.8fr 0.8fr 0.8fr 0.9fr 1fr"
        header_html = (
            f'<div style="display:grid;grid-template-columns:{cols};'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px 10px 0 0;'
            f'padding:10px 14px;">'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Port</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Region</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Current (M)</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Capacity (M)</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Utilisation</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Headroom (M)</span>'
            f'<span style="font-size:0.7rem;color:{C_TEXT3};text-transform:uppercase;">Overflow Risk</span>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)
        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 10px 10px;overflow:hidden;">'
        for i, (port, region, curr, f3, f12, util, cap, yoy, driver) in enumerate(_PORTS):
            bg = C_CARD if i % 2 == 0 else C_SURFACE
            headroom = round(cap - curr, 1)
            uc = _util_color(util)
            badge = _overflow_badge(util)
            rows_html += (
                f'<div style="display:grid;grid-template-columns:{cols};'
                f'gap:0;background:{bg};padding:9px 14px;align-items:center;">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{port}</span>'
                f'<span style="font-size:0.75rem;color:{_REGION_COLORS.get(region, C_TEXT3)};">{region}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT};">{curr:.1f}</span>'
                f'<span style="font-size:0.82rem;color:{C_TEXT2};">{cap:.1f}</span>'
                f'<span style="font-size:0.82rem;font-weight:700;color:{uc};">{util}%</span>'
                f'<span style="font-size:0.82rem;color:{C_HIGH if headroom > 5 else C_MOD};">{headroom:.1f}</span>'
                f'<span>{badge}</span>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
        st.markdown(
            f'<div style="display:flex;gap:20px;margin-top:8px;padding:0 4px;">'
            f'<span style="font-size:0.75rem;color:{C_LOW};">CRITICAL ≥90%</span>'
            f'<span style="font-size:0.75rem;color:{C_MOD};">ELEVATED ≥80%</span>'
            f'<span style="font-size:0.75rem;color:{C_ACCENT};">MODERATE ≥70%</span>'
            f'<span style="font-size:0.75rem;color:{C_HIGH};">LOW &lt;70%</span>'
            f'</div>',
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
    try:
        _render_hero()
        _render_forecast_table()
        _render_demand_drivers()
        _render_regional_comparison()
        _render_seasonal_heatmap()
        _render_shock_scenarios()
        _render_capacity_headroom()
    except Exception:
        logger.exception("tab_port_demand top-level render failed")
        st.error("Port Demand tab encountered an error.")
