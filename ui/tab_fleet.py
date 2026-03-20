"""Fleet Supply & Orderbook tab — global container fleet capacity and supply dynamics.

Renders supply-side analysis: fleet size, orderbook, vessel categories, age profile,
market tightness gauge, and trader implications.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from processing.fleet_tracker import (
    FLEET_2025,
    VESSEL_CATEGORIES,
    get_fleet_data,
    get_supply_pressure_score,
)
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    section_header,
    dark_layout,
)

# ── Local color constants ─────────────────────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_GRAY    = "#475569"
_C_GREEN   = "#10b981"
_C_RED     = "#ef4444"
_C_AMBER   = "#f59e0b"
_C_BLUE    = "#3b82f6"


# ── Section 1: Fleet Overview Hero ───────────────────────────────────────────

def _render_hero(fleet) -> None:
    section_header("Fleet Overview", "Global container fleet capacity — 2025 baseline (Clarksons/Alphaliner)")

    c1, c2, c3, c4 = st.columns(4)

    balance_sign = "+" if fleet.supply_demand_balance >= 0 else ""

    with c1:
        st.metric(
            label="Total Fleet",
            value=f"{fleet.total_teu_capacity_m:.1f}M TEU",
            delta=f"+{fleet.deliveries_next_12m_teu_m:.1f}M TEU deliveries next 12m",
            delta_color="inverse",  # more supply = bearish for rates
        )

    with c2:
        st.metric(
            label="Orderbook",
            value=f"{fleet.orderbook_teu_m:.1f}M TEU",
            delta=f"{fleet.orderbook_pct:.1f}% of current fleet on order",
            delta_color="inverse",  # high orderbook = bearish for rates
        )

    with c3:
        st.metric(
            label="Net Supply Growth",
            value=f"+{fleet.net_supply_growth_pct:.1f}%",
            delta=f"vs ~{fleet.demand_growth_estimate_pct:.1f}% demand growth est.",
            delta_color="inverse" if fleet.net_supply_growth_pct > fleet.demand_growth_estimate_pct else "normal",
        )

    with c4:
        balance_label = "OVERSUPPLIED" if fleet.supply_demand_balance < 0 else "BALANCED"
        st.metric(
            label="Supply-Demand Balance",
            value=f"{balance_sign}{fleet.supply_demand_balance:.1f}pp",
            delta=balance_label,
            delta_color="inverse" if fleet.supply_demand_balance < 0 else "normal",
        )


# ── Section 2: Orderbook Waterfall Chart ─────────────────────────────────────

def _render_waterfall(fleet) -> None:
    section_header("Fleet Capacity Waterfall", "Current fleet → projected end-2026 after deliveries and scrapping")

    scrapping_abs = round(fleet.total_teu_capacity_m * fleet.scrapping_rate_annual_pct / 100, 2)
    net_fleet     = round(fleet.total_teu_capacity_m + fleet.deliveries_next_12m_teu_m - scrapping_abs, 2)
    demand_line   = round(fleet.total_teu_capacity_m * (1 + fleet.demand_growth_estimate_pct / 100), 2)

    fig = go.Figure()

    # Waterfall bars
    fig.add_trace(go.Waterfall(
        name="Fleet TEU (M)",
        orientation="v",
        measure=["absolute", "relative", "relative", "total"],
        x=["Current Fleet", "Deliveries (+)", "Scrapping (−)", "Net Fleet End-2026"],
        y=[fleet.total_teu_capacity_m,
           fleet.deliveries_next_12m_teu_m,
           -scrapping_abs,
           0],
        text=[f"{fleet.total_teu_capacity_m}M", f"+{fleet.deliveries_next_12m_teu_m}M",
              f"-{scrapping_abs}M", f"{net_fleet}M"],
        textposition="outside",
        connector={"line": {"color": "rgba(255,255,255,0.15)", "width": 1, "dash": "dot"}},
        increasing={"marker": {"color": _C_GREEN}},
        decreasing={"marker": {"color": _C_RED}},
        totals={"marker": {"color": _C_BLUE}},
        textfont={"color": C_TEXT, "size": 12},
    ))

    # Demand growth overlay line
    fig.add_trace(go.Scatter(
        x=["Current Fleet", "Net Fleet End-2026"],
        y=[fleet.total_teu_capacity_m, demand_line],
        mode="lines+markers+text",
        name="Demand Growth Trajectory",
        line={"color": _C_AMBER, "width": 2, "dash": "dash"},
        marker={"size": 7, "color": _C_AMBER},
        text=["", f"Demand: {demand_line}M"],
        textposition="top right",
        textfont={"color": _C_AMBER, "size": 11},
    ))

    layout = dark_layout(title="Container Fleet TEU Capacity (Millions)", height=320)
    layout["template"] = "plotly_dark"
    layout["margin"] = dict(l=40, r=20, t=40, b=40)
    layout["xaxis"]["showgrid"] = False
    layout["yaxis"]["title"] = "TEU Capacity (Millions)"
    layout["yaxis"]["range"] = [0, net_fleet * 1.15]
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, key="fleet_waterfall")


# ── Section 3: Vessel Category Donuts ────────────────────────────────────────

def _render_category_donuts() -> None:
    section_header(
        "Vessel Category Breakdown",
        "Fleet vs orderbook composition — ultra-large vessels dominate new orders",
    )

    if not VESSEL_CATEGORIES:
        st.info("No vessel category data available.")
        return

    names          = [c["name"] for c in VESSEL_CATEGORIES]
    fleet_shares   = [c["fleet_share"] for c in VESSEL_CATEGORIES]
    orderbook_shares = [c["orderbook_share"] for c in VESSEL_CATEGORIES]

    if sum(fleet_shares) == 0 and sum(orderbook_shares) == 0:
        st.info("Vessel utilisation and capacity data are all zero — charts will appear once fleet data is loaded.")
        return

    donut_colors = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#06b6d4"]

    fig = make_subplots(
        rows=1, cols=2,
        specs=[[{"type": "pie"}, {"type": "pie"}]],
        subplot_titles=["Current Fleet by Size", "Orderbook by Size"],
    )

    fig.add_trace(
        go.Pie(
            labels=names,
            values=fleet_shares,
            hole=0.55,
            marker={"colors": donut_colors,
                    "line": {"color": _C_BG, "width": 2}},
            textinfo="label+percent",
            textfont={"size": 10, "color": C_TEXT},
            hovertemplate="%{label}<br>Fleet share: %{percent}<extra></extra>",
        ),
        row=1, col=1,
    )

    fig.add_trace(
        go.Pie(
            labels=names,
            values=orderbook_shares,
            hole=0.55,
            marker={"colors": donut_colors,
                    "line": {"color": _C_BG, "width": 2}},
            textinfo="label+percent",
            textfont={"size": 10, "color": C_TEXT},
            hovertemplate="%{label}<br>Orderbook share: %{percent}<extra></extra>",
        ),
        row=1, col=2,
    )

    layout = dark_layout(height=360, showlegend=False)
    layout["template"] = "plotly_dark"
    layout["paper_bgcolor"] = _C_BG
    layout["margin"] = dict(l=40, r=20, t=40, b=40)
    layout["annotations"] = [
        {"text": "Fleet", "x": 0.18, "y": 0.5, "showarrow": False,
         "font": {"size": 13, "color": C_TEXT2}, "xref": "paper", "yref": "paper"},
        {"text": "Orderbook", "x": 0.82, "y": 0.5, "showarrow": False,
         "font": {"size": 13, "color": C_TEXT2}, "xref": "paper", "yref": "paper"},
    ]
    # Update subplot title font colors
    for ann in layout.get("annotations", []):
        ann["font"] = ann.get("font", {})
        ann["font"]["color"] = C_TEXT2
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True, key="fleet_category_donuts")


# ── Section 4: Age Profile Horizontal Bars ───────────────────────────────────

def _age_color(avg_age: float) -> str:
    if avg_age > 15:
        return _C_RED
    if avg_age > 10:
        return _C_AMBER
    return _C_GREEN


def _render_age_profile() -> None:
    section_header(
        "Vessel Age Profile",
        "Average age by category — older vessels are scrapping candidates",
    )

    if not VESSEL_CATEGORIES:
        st.info("No vessel age profile data available.")
        return

    names  = [c["name"] for c in VESSEL_CATEGORIES]
    ages   = [c["avg_age"] for c in VESSEL_CATEGORIES]

    if not ages or max(ages) == 0:
        st.info("Vessel age data is unavailable or all zero.")
        return

    colors = [_age_color(a) for a in ages]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=ages,
        y=names,
        orientation="h",
        marker={"color": colors, "line": {"color": "rgba(255,255,255,0.05)", "width": 1}},
        text=[f"{a:.1f} yrs" for a in ages],
        textposition="outside",
        textfont={"color": C_TEXT, "size": 11},
        hovertemplate="%{y}<br>Avg age: %{x:.1f} years<extra></extra>",
    ))

    # Reference lines
    for threshold, label, color in [(10, "10yr", _C_AMBER), (15, "15yr (scrap risk)", _C_RED)]:
        fig.add_vline(
            x=threshold,
            line={"color": color, "dash": "dash", "width": 1.5},
            annotation_text=label,
            annotation_position="top",
            annotation_font={"color": color, "size": 10},
        )

    layout = dark_layout(title="Average Vessel Age by Category (Years)", height=280)
    layout["template"] = "plotly_dark"
    layout["margin"] = dict(l=40, r=20, t=40, b=40)
    layout["xaxis"]["title"] = "Average Age (Years)"
    layout["xaxis"]["range"] = [0, max(ages) * 1.25]
    layout["yaxis"]["autorange"] = "reversed"
    fig.update_layout(**layout)

    # Legend note
    legend_html = (
        f"<span style='color:{_C_GREEN};font-weight:600;'>Green</span> &lt; 7 yrs &nbsp;"
        f"<span style='color:{_C_AMBER};font-weight:600;'>Amber</span> 7–15 yrs &nbsp;"
        f"<span style='color:{_C_RED};font-weight:600;'>Red</span> &gt; 15 yrs (scrapping candidate)"
    )
    st.markdown(
        f"<div style='font-size:0.78rem;color:{C_TEXT2};margin-bottom:4px;'>{legend_html}</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(fig, use_container_width=True, key="fleet_age_profile")


# ── Section 5: Market Tightness Gauge ────────────────────────────────────────

def _render_tightness_gauge(fleet) -> None:
    section_header(
        "Market Tightness Gauge",
        "Supply pressure score: 0 = severe oversupply, 100 = very tight market",
    )

    score_01  = get_supply_pressure_score()
    score_100 = round(score_01 * 100, 1)

    # Determine zone label and color from score
    if score_100 < 30:
        zone_label = "OVERSUPPLIED"
        needle_color = _C_RED
    elif score_100 < 50:
        zone_label = "LOOSE"
        needle_color = _C_AMBER
    elif score_100 < 65:
        zone_label = "BALANCED"
        needle_color = _C_BLUE
    elif score_100 < 80:
        zone_label = "TIGHT"
        needle_color = _C_GREEN
    else:
        zone_label = "VERY TIGHT"
        needle_color = "#22c55e"

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=score_100,
        title={"text": f"Supply Pressure — <b>{zone_label}</b>",
               "font": {"color": C_TEXT, "size": 14}},
        delta={"reference": 50, "suffix": " vs balanced (50)",
               "font": {"size": 12},
               "decreasing": {"color": _C_RED},
               "increasing": {"color": _C_GREEN}},
        number={"suffix": " / 100", "font": {"color": C_TEXT, "size": 28}},
        gauge={
            "axis": {
                "range": [0, 100],
                "tickwidth": 1,
                "tickcolor": C_TEXT3,
                "tickfont": {"color": C_TEXT3, "size": 10},
            },
            "bar": {"color": needle_color, "thickness": 0.25},
            "bgcolor": _C_SURFACE,
            "borderwidth": 1,
            "bordercolor": C_BORDER,
            "steps": [
                {"range": [0,  30], "color": "rgba(239,68,68,0.18)"},    # red — oversupplied
                {"range": [30, 50], "color": "rgba(245,158,11,0.18)"},   # amber — loose
                {"range": [50, 65], "color": "rgba(59,130,246,0.18)"},   # blue — balanced
                {"range": [65, 80], "color": "rgba(16,185,129,0.18)"},   # green — tight
                {"range": [80, 100], "color": "rgba(34,197,94,0.22)"},   # bright green — very tight
            ],
            "threshold": {
                "line": {"color": C_TEXT2, "width": 2},
                "thickness": 0.75,
                "value": score_100,
            },
        },
    ))

    layout = dark_layout(height=300, showlegend=False)
    layout["template"] = "plotly_dark"
    layout["paper_bgcolor"] = _C_BG
    layout["margin"] = dict(l=40, r=20, t=40, b=40)
    fig.update_layout(**layout)

    col_g, col_l = st.columns([2, 1])
    with col_g:
        st.plotly_chart(fig, use_container_width=True, key="fleet_tightness_gauge")
    with col_l:
        zones = [
            (80, 100, "VERY TIGHT",  "#22c55e"),
            (65,  80, "TIGHT",       _C_GREEN),
            (50,  65, "BALANCED",    _C_BLUE),
            (30,  50, "LOOSE",       _C_AMBER),
            (0,   30, "OVERSUPPLIED", _C_RED),
        ]
        st.markdown(f"<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
        for lo, hi, label, color in zones:
            active = lo <= score_100 < hi or (hi == 100 and score_100 == 100)
            bg     = f"rgba({_hex_to_rgb(color)},0.15)" if active else "transparent"
            border = f"1px solid {color}" if active else f"1px solid {C_BORDER}"
            st.markdown(
                f"""<div style="background:{bg};border:{border};border-radius:6px;
                               padding:7px 12px;margin-bottom:6px;display:flex;
                               align-items:center;gap:10px;">
                      <div style="width:10px;height:10px;border-radius:50%;
                                  background:{color};flex-shrink:0;"></div>
                      <span style="font-size:0.78rem;color:{C_TEXT};font-weight:{'600' if active else '400'};">
                        {lo}–{hi}: {label}
                      </span>
                    </div>""",
                unsafe_allow_html=True,
            )


def _hex_to_rgb(hex_color: str) -> str:
    """Convert #rrggbb to 'r,g,b' string for rgba() use."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"{r},{g},{b}"


# ── Section 6: Trader Implications ───────────────────────────────────────────

def _render_implications(fleet) -> None:
    section_header(
        "Implications for Traders",
        "Key takeaways from current supply-demand dynamics",
    )

    if not fleet.implications:
        st.info("No trader implications available for the current fleet data.")
        return

    icons = ["", "", ""]
    card_colors = [_C_RED, _C_AMBER, _C_BLUE]

    cols = st.columns(len(fleet.implications))
    for col, text, icon, accent in zip(cols, fleet.implications, icons, card_colors):
        with col:
            st.markdown(
                f"""
                <div style="background:{C_CARD};border:1px solid {C_BORDER};
                            border-top:3px solid {accent};border-radius:10px;
                            padding:18px 16px;min-height:120px;">
                  <div style="font-size:1.5rem;margin-bottom:8px;">{icon}</div>
                  <div style="font-size:0.84rem;color:{C_TEXT};line-height:1.55;">
                    {text}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # Data vintage footnote
    st.markdown(
        f"<div style='font-size:0.72rem;color:{C_TEXT3};margin-top:10px;'>"
        f"Data vintage: {fleet.data_vintage}</div>",
        unsafe_allow_html=True,
    )


# ── Main render entry point ───────────────────────────────────────────────────

def render(freight_data=None, macro_data=None) -> None:
    """Render the Fleet Supply & Orderbook tab.

    Parameters
    ----------
    freight_data:
        Passed from the main app for potential future integration (unused here;
        fleet data is sourced from the hardcoded 2025 baseline in fleet_tracker).
    macro_data:
        Same as above — available for future demand-growth overrides.
    """
    with st.spinner("Loading fleet data..."):
        fleet = get_fleet_data()

    _render_hero(fleet)
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_waterfall(fleet)
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_category_donuts()
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_age_profile()

    _fleet_df = pd.DataFrame([
        {
            "Vessel Category": c["name"],
            "Fleet Share (%)": c["fleet_share"],
            "Orderbook Share (%)": c["orderbook_share"],
            "Average Age (yrs)": c["avg_age"],
        }
        for c in VESSEL_CATEGORIES
    ])
    csv = _fleet_df.to_csv(index=False)
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name="fleet_data.csv",
        mime="text/csv",
        key="download_fleet_data_csv",
    )

    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_tightness_gauge(fleet)
    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    _render_implications(fleet)


# ── Integration instructions ──────────────────────────────────────────────────
# To wire this tab into app.py:
#
# 1. Import at the top of app.py:
#        from ui import tab_fleet
#
# 2. Add a new tab in the st.tabs() call, e.g.:
#        tab_ov, tab_mk, ..., tab_fleet_tab = st.tabs([
#            "Overview", "Markets", ..., "Fleet & Supply"
#        ])
#
# 3. Render inside the tab context:
#        with tab_fleet_tab:
#            tab_fleet.render(freight_data=freight_data, macro_data=macro_data)
#
# The render() function is intentionally decoupled — it reads from
# processing.fleet_tracker directly and does not require freight_data or
# macro_data to produce a complete view.
