"""
Risk Matrix Tab

Comprehensive supply-chain vulnerability visualization:
  1. Risk Matrix Scatter Plot (2x2 quadrant — Probability vs Impact)
  2. Risk Radar for selected route (Scatterpolar)
  3. Vulnerability Leaderboard (styled HTML table)
  4. Port Risk Heatmap (Scattergeo globe)
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from processing.vulnerability_scorer import (
    SupplyChainVulnerability,
    score_all_routes,
    get_vulnerability_color,
    VULNERABILITY_COLORS,
)

# ---------------------------------------------------------------------------
# Color palette (self-contained)
# ---------------------------------------------------------------------------

C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        f'<div style="color:{C_TEXT2}; font-size:0.83rem; margin-top:3px">{subtitle}</div>'
        if subtitle
        else ""
    )
    st.markdown(
        f'<div style="margin-bottom:14px; margin-top:4px">'
        f'<div style="font-size:1.05rem; font-weight:700; color:{C_TEXT}">{text}</div>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _card_wrap(content_html: str, border_color: str = C_BORDER) -> str:
    return (
        f'<div style="background:{C_CARD}; border:1px solid {border_color};'
        f' border-radius:12px; padding:18px 20px; margin-bottom:10px">'
        f'{content_html}</div>'
    )


def _label_badge(label: str) -> str:
    color = VULNERABILITY_COLORS.get(label, "#94a3b8")
    return (
        f'<span style="background:rgba(0,0,0,0.3); color:{color};'
        f' border:1px solid {color}; padding:1px 8px; border-radius:999px;'
        f' font-size:0.70rem; font-weight:700; white-space:nowrap">{label}</span>'
    )


# ---------------------------------------------------------------------------
# Section 1: Risk Matrix Scatter Plot (2x2 quadrant)
# ---------------------------------------------------------------------------

def _render_risk_matrix(vulnerabilities: list[SupplyChainVulnerability], route_results) -> None:
    """Classic 2x2 risk matrix as interactive scatter plot."""

    # Build opportunity score lookup from route_results
    opp_by_id: dict[str, float] = {}
    if route_results:
        for r in route_results:
            rid = getattr(r, "route_id", "")
            opp = getattr(r, "opportunity_score", 0.5)
            if rid:
                opp_by_id[rid] = float(opp)

    if not vulnerabilities:
        st.info("No vulnerability data available to build the risk matrix.")
        return

    # Axes:
    #   X = Probability  = (geopolitical_risk + chokepoint_dependency) / 2
    #   Y = Impact       = (concentration_risk + weather_risk) / 2
    #   Bubble size      = opportunity_score (bigger = more at stake)

    x_vals, y_vals, sizes, colors, texts, labels_list = [], [], [], [], [], []

    for v in vulnerabilities:
        x = (v.geopolitical_risk + v.chokepoint_dependency) / 2.0
        y = (v.concentration_risk + v.weather_risk) / 2.0
        opp = opp_by_id.get(v.route_id, 0.50)
        bubble_size = 10 + opp * 30

        color = get_vulnerability_color(v.vulnerability_label)

        hover = (
            f"<b>{v.route_name}</b><br>"
            f"Label: {v.vulnerability_label}<br>"
            f"Overall Vulnerability: {v.overall_vulnerability:.0%}<br>"
            f"Probability (X): {x:.0%}<br>"
            f"Impact (Y): {y:.0%}<br>"
            f"Opportunity: {opp:.0%}<br>"
            f"Top Risk: {v.risk_factors[0] if v.risk_factors else 'N/A'}"
        )

        x_vals.append(round(x, 4))
        y_vals.append(round(y, 4))
        sizes.append(bubble_size)
        colors.append(color)
        texts.append(v.route_name[:22])
        labels_list.append(hover)

    fig = go.Figure()

    # Quadrant shading regions
    quad_configs = [
        # x0, x1, y0, y1, fill, label text, label x, label y
        (0.5, 1.0, 0.5, 1.0, "rgba(239,68,68,0.07)",   "HIGH PRIORITY RISK",  0.75, 0.92),
        (0.0, 0.5, 0.5, 1.0, "rgba(245,158,11,0.06)",  "MONITOR",             0.25, 0.92),
        (0.5, 1.0, 0.0, 0.5, "rgba(59,130,246,0.06)",  "CONTINGENCY",         0.75, 0.08),
        (0.0, 0.5, 0.0, 0.5, "rgba(16,185,129,0.05)",  "ACCEPTABLE",          0.25, 0.08),
    ]
    for x0, x1, y0, y1, fill, _label, _lx, _ly in quad_configs:
        fig.add_shape(
            type="rect",
            x0=x0, x1=x1, y0=y0, y1=y1,
            fillcolor=fill,
            line=dict(width=0),
            layer="below",
        )

    # Quadrant divider lines
    fig.add_shape(
        type="line", x0=0.5, x1=0.5, y0=0, y1=1,
        line=dict(color="rgba(255,255,255,0.18)", width=1, dash="dot"),
    )
    fig.add_shape(
        type="line", x0=0, x1=1, y0=0.5, y1=0.5,
        line=dict(color="rgba(255,255,255,0.18)", width=1, dash="dot"),
    )

    # Quadrant labels as annotations
    quad_annots = [
        ("HIGH PRIORITY RISK", 0.75, 0.92, C_DANGER),
        ("MONITOR",            0.25, 0.92, C_WARN),
        ("CONTINGENCY",        0.75, 0.08, C_ACCENT),
        ("ACCEPTABLE",         0.25, 0.08, C_HIGH),
    ]
    for ann_text, ax, ay, acolor in quad_annots:
        fig.add_annotation(
            x=ax, y=ay,
            xref="paper", yref="paper",
            text=ann_text,
            showarrow=False,
            font=dict(size=10, color=acolor, family="monospace"),
            opacity=0.55,
        )

    # Bubble scatter trace
    fig.add_trace(go.Scatter(
        x=x_vals,
        y=y_vals,
        mode="markers+text",
        marker=dict(
            size=sizes,
            color=colors,
            opacity=0.85,
            line=dict(color="rgba(255,255,255,0.3)", width=1),
        ),
        text=texts,
        textposition="top center",
        textfont=dict(size=8, color=C_TEXT2),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=labels_list,
        showlegend=False,
    ))

    # Legend swatches (manual, via invisible scatter traces)
    for lbl, lcolor in VULNERABILITY_COLORS.items():
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="markers",
            marker=dict(size=10, color=lcolor),
            name=lbl,
            showlegend=True,
        ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=500,
        font=dict(color=C_TEXT),
        xaxis=dict(
            title="Probability (Geopolitical + Chokepoint Risk)",
            range=[-0.02, 1.02],
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            tickformat=".0%",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        yaxis=dict(
            title="Impact (Concentration + Weather Risk)",
            range=[-0.02, 1.02],
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            tickformat=".0%",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
        margin=dict(l=60, r=20, t=60, b=60),
    )

    st.plotly_chart(fig, use_container_width=True, key="risk_matrix_scatter")


# ---------------------------------------------------------------------------
# Section 2: Risk Radar for Selected Route
# ---------------------------------------------------------------------------

def _render_route_radar(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    """Selectbox + Scatterpolar radar for a chosen route."""
    if not vulnerabilities:
        st.info("No vulnerability data available.")
        return

    route_names = [v.route_name for v in vulnerabilities]
    selected_name = st.selectbox(
        "Select a route to inspect:",
        options=route_names,
        index=0,
        key="risk_matrix_route_select",
    )

    v = next((x for x in vulnerabilities if x.route_name == selected_name), vulnerabilities[0])

    # 6 axes — redundancy is inverted (high redundancy = low risk displayed)
    categories = [
        "Chokepoint Dependency",
        "Concentration Risk",
        "Weather Risk",
        "Geopolitical Risk",
        "Infrastructure Risk",
        "Low Redundancy",
    ]
    values = [
        v.chokepoint_dependency,
        v.concentration_risk,
        v.weather_risk,
        v.geopolitical_risk,
        v.infrastructure_risk,
        1.0 - v.redundancy_score,   # invert so high = more vulnerable
    ]

    # Close the polygon
    cats_closed = categories + [categories[0]]
    vals_closed = values + [values[0]]

    label_color = get_vulnerability_color(v.vulnerability_label)

    fig = go.Figure(go.Scatterpolar(
        r=vals_closed,
        theta=cats_closed,
        fill="toself",
        fillcolor=f"{label_color}33",
        line=dict(color=label_color, width=2),
        hovertemplate="<b>%{theta}</b><br>Score: %{r:.0%}<extra></extra>",
        name=v.route_name,
    ))

    fig.update_layout(
        template="plotly_dark",
        polar=dict(
            bgcolor="#111827",
            radialaxis=dict(
                visible=True,
                range=[0, 1],
                tickformat=".0%",
                tickfont=dict(size=9, color=C_TEXT2),
                gridcolor="rgba(255,255,255,0.08)",
                linecolor="rgba(255,255,255,0.08)",
            ),
            angularaxis=dict(
                tickfont=dict(size=11, color=C_TEXT),
                gridcolor="rgba(255,255,255,0.08)",
                linecolor="rgba(255,255,255,0.10)",
            ),
        ),
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT),
        height=400,
        margin=dict(l=60, r=60, t=50, b=30),
        showlegend=False,
        title=dict(
            text=(
                f"{v.route_name} — "
                f"<span style='color:{label_color}'>{v.vulnerability_label}</span>"
                f" ({v.overall_vulnerability:.0%})"
            ),
            font=dict(size=13, color=C_TEXT),
            x=0.5,
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="risk_matrix_radar")

    # Show risk factors and mitigations below radar
    col_rf, col_mit = st.columns(2)
    with col_rf:
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_DANGER};'
            f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px">'
            f'Top Risk Factors</div>',
            unsafe_allow_html=True,
        )
        risk_factors = v.risk_factors or ["No risk factors recorded."]
        items_html = "".join(
            f'<li style="color:{C_TEXT}; font-size:0.84rem; margin-bottom:5px">{rf}</li>'
            for rf in risk_factors
        )
        st.markdown(
            _card_wrap(
                f'<ul style="padding-left:18px; margin:0">{items_html}</ul>',
                border_color="rgba(239,68,68,0.25)",
            ),
            unsafe_allow_html=True,
        )

    with col_mit:
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_HIGH};'
            f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px">'
            f'Mitigation Options</div>',
            unsafe_allow_html=True,
        )
        mitigation_options = v.mitigation_options or ["No mitigations recorded."]
        mit_html = "".join(
            f'<li style="color:{C_TEXT}; font-size:0.84rem; margin-bottom:5px">{m}</li>'
            for m in mitigation_options
        )
        st.markdown(
            _card_wrap(
                f'<ul style="padding-left:18px; margin:0">{mit_html}</ul>',
                border_color="rgba(16,185,129,0.25)",
            ),
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 3: Vulnerability Leaderboard
# ---------------------------------------------------------------------------

def _render_leaderboard(vulnerabilities: list[SupplyChainVulnerability]) -> None:
    """Ranked HTML table sorted by overall vulnerability descending."""

    if not vulnerabilities:
        st.info("No vulnerability data available for the leaderboard.")
        return

    LABEL_ROW_BG: dict[str, str] = {
        "CRITICAL": "rgba(239,68,68,0.08)",
        "HIGH":     "rgba(245,158,11,0.07)",
        "MODERATE": "rgba(59,130,246,0.07)",
        "LOW":      "rgba(16,185,129,0.06)",
    }

    rows_html = ""
    for rank, v in enumerate(vulnerabilities, start=1):
        color     = get_vulnerability_color(v.vulnerability_label)
        row_bg    = LABEL_ROW_BG.get(v.vulnerability_label, "transparent")
        bar_pct   = int(v.overall_vulnerability * 100)
        top_risk  = v.risk_factors[0] if v.risk_factors else "—"
        mitigation = v.mitigation_options[0] if v.mitigation_options else "—"
        badge_html = _label_badge(v.vulnerability_label)

        rows_html += (
            f'<tr style="background:{row_bg}; border-bottom:1px solid rgba(255,255,255,0.04)">'
            f'<td style="color:{C_TEXT3}; font-size:0.75rem; padding:9px 8px; text-align:center;'
            f' font-weight:600; min-width:30px">{rank}</td>'
            f'<td style="color:{C_TEXT}; font-size:0.82rem; padding:9px 8px; font-weight:600;'
            f' white-space:nowrap">{v.route_name}</td>'
            f'<td style="padding:9px 8px; min-width:130px">'
            f'<div style="display:flex; align-items:center; gap:7px">'
            f'<div style="flex:1; background:rgba(255,255,255,0.06); border-radius:4px; height:7px">'
            f'<div style="width:{bar_pct}%; background:{color}; border-radius:4px; height:7px"></div>'
            f'</div>'
            f'<span style="font-size:0.78rem; font-weight:700; color:{color}; min-width:34px">'
            f'{bar_pct}%</span>'
            f'</div></td>'
            f'<td style="padding:9px 8px">{badge_html}</td>'
            f'<td style="color:{C_TEXT2}; font-size:0.78rem; padding:9px 8px;'
            f' line-height:1.4; max-width:200px">{top_risk}</td>'
            f'<td style="color:{C_TEXT3}; font-size:0.76rem; padding:9px 8px;'
            f' line-height:1.4; max-width:220px">{mitigation}</td>'
            f'</tr>'
        )

    header_style = (
        f'color:{C_TEXT3}; font-size:0.68rem; text-transform:uppercase;'
        f' letter-spacing:0.07em; padding:6px 8px; text-align:left;'
        f' border-bottom:1px solid rgba(255,255,255,0.10)'
    )

    table_html = (
        f'<div style="overflow-x:auto">'
        f'<table style="width:100%; border-collapse:collapse">'
        f'<thead><tr>'
        f'<th style="{header_style}; text-align:center">#</th>'
        f'<th style="{header_style}">Route</th>'
        f'<th style="{header_style}">Vulnerability</th>'
        f'<th style="{header_style}">Label</th>'
        f'<th style="{header_style}">Top Risk Factor</th>'
        f'<th style="{header_style}">Mitigation</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table>'
        f'</div>'
    )

    st.markdown(
        _card_wrap(table_html, border_color="rgba(59,130,246,0.20)"),
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 4: Port Risk Heatmap (Scattergeo globe)
# ---------------------------------------------------------------------------

def _render_port_heatmap(port_results) -> None:
    """Geographical heatmap of port-level risk on a dark orthographic globe."""
    try:
        from ports.port_registry import PORTS_BY_LOCODE  # noqa
    except ImportError:
        st.warning("Port registry not available.")
        return

    lats, lons, risk_vals, sizes, hover_texts, port_names_list = [], [], [], [], [], []

    for pr in port_results:
        locode = getattr(pr, "locode", None) or getattr(pr, "port_locode", None)
        if not locode:
            continue

        port = PORTS_BY_LOCODE.get(locode)
        if not port:
            continue

        demand    = float(getattr(pr, "demand_score",     0.0))
        congestion = float(getattr(pr, "congestion_index", 0.0))
        teu        = float(getattr(pr, "throughput_teu_m", 0.0))
        port_name  = getattr(pr, "port_name", port.name)

        # Combined risk: weighted demand + congestion
        combined_risk = demand * 0.55 + congestion * 0.45
        combined_risk = min(1.0, max(0.0, combined_risk))

        # Marker size proportional to TEU volume; clamp between 8-35
        marker_size = 8 + min(teu, 50.0) * 0.54
        marker_size = max(8.0, min(35.0, marker_size))

        hover = (
            f"<b>{port_name}</b> ({locode})<br>"
            f"Region: {port.region}<br>"
            f"Combined Risk: {combined_risk:.0%}<br>"
            f"Demand Score: {demand:.0%}<br>"
            f"Congestion: {congestion:.0%}<br>"
            f"Throughput: {teu:.1f}M TEU/yr"
        )

        lats.append(port.lat)
        lons.append(port.lon)
        risk_vals.append(round(combined_risk, 4))
        sizes.append(marker_size)
        hover_texts.append(hover)
        port_names_list.append(port_name)

    if not lats:
        st.markdown(
            _card_wrap(
                f'<div style="text-align:center; color:{C_TEXT2}; font-size:0.88rem; padding:8px 0">'
                f'No port data available for risk heatmap.'
                f'</div>'
            ),
            unsafe_allow_html=True,
        )
        return

    RISK_COLORSCALE = [
        [0.00, "#1e3a5f"],
        [0.25, "#3b82f6"],
        [0.50, "#10b981"],
        [0.75, "#f59e0b"],
        [1.00, "#ef4444"],
    ]

    fig = go.Figure()

    # Glow layer (semi-transparent larger markers)
    fig.add_trace(go.Scattergeo(
        lat=lats,
        lon=lons,
        mode="markers",
        marker=dict(
            size=[s * 1.8 for s in sizes],
            color=risk_vals,
            colorscale=RISK_COLORSCALE,
            cmin=0,
            cmax=1,
            opacity=0.20,
            line=dict(width=0),
        ),
        hoverinfo="skip",
        showlegend=False,
        name="port_glow",
    ))

    # Main marker layer
    fig.add_trace(go.Scattergeo(
        lat=lats,
        lon=lons,
        mode="markers",
        marker=dict(
            size=sizes,
            color=risk_vals,
            colorscale=RISK_COLORSCALE,
            cmin=0,
            cmax=1,
            opacity=0.90,
            line=dict(color="rgba(255,255,255,0.5)", width=0.8),
            colorbar=dict(
                title=dict(text="Port Risk", font=dict(color=C_TEXT2, size=11)),
                thickness=10,
                len=0.5,
                x=1.01,
                tickformat=".0%",
                tickfont=dict(color=C_TEXT2, size=10),
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(255,255,255,0.1)",
            ),
        ),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover_texts,
        showlegend=False,
        name="ports",
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        height=500,
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="orthographic",
            showland=True,        landcolor="#1a2235",
            showocean=True,       oceancolor="#0a0f1a",
            showcoastlines=True,  coastlinecolor="rgba(255,255,255,0.15)",
            showframe=False,
            bgcolor="#0a0f1a",
            showcountries=True,   countrycolor="rgba(255,255,255,0.07)",
            showlakes=False,
            projection_rotation=dict(lon=60, lat=10, roll=0),
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )

    st.plotly_chart(fig, use_container_width=True, key="risk_matrix_port_heatmap")


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(route_results, port_results, macro_data) -> None:
    """Render the Risk Matrix tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer.
    port_results : list[PortDemandResult]
        Current port demand results.
    macro_data : dict
        Global macro indicators dict (passed through; may be used by future sections).
    """
    st.header("Supply Chain Risk Matrix")
    st.caption(
        "Composite vulnerability assessment across all monitored trade lanes. "
        "Scores are derived from six sub-indices — geopolitical risk, chokepoint dependency, "
        "concentration risk, weather risk, infrastructure risk, and route redundancy — "
        "each normalised to a 0–100% scale. Data is refreshed on each app rerun."
    )

    # Compute vulnerability scores for all routes
    vulnerabilities = score_all_routes(route_results)

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Risk Matrix Scatter Plot
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Risk Matrix — Probability vs. Impact",
        (
            "Bubble position = probability (X) vs impact (Y). "
            "Bubble size = route opportunity score. "
            "Quadrant lines at 50%."
        ),
    )
    st.caption(
        "X-axis (Probability) = average of geopolitical risk and chokepoint dependency scores. "
        "Y-axis (Impact) = average of concentration risk and weather risk scores. "
        "Bubble size scales with the route's opportunity score — larger bubbles mean more commercial "
        "exposure if a disruption occurs. Use this matrix to prioritise hedging and contingency planning."
    )
    _render_risk_matrix(vulnerabilities, route_results)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Risk Radar for Selected Route
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Route Risk Radar",
        "Six-dimension vulnerability profile for a selected trade lane",
    )
    st.caption(
        "Each axis represents one vulnerability dimension scored 0–100%. "
        "'Low Redundancy' is the inverse of the redundancy score — a high value means few "
        "alternative routings exist. The filled area and colour reflect the route's overall "
        "vulnerability label (CRITICAL / HIGH / MODERATE / LOW)."
    )
    _render_route_radar(vulnerabilities)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Vulnerability Leaderboard
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Vulnerability Leaderboard",
        "All routes ranked by overall vulnerability score — highest risk first",
    )
    st.caption(
        "Overall vulnerability is the probability-weighted mean of all six sub-indices. "
        "The progress bar shows the absolute score; the label (CRITICAL / HIGH / MODERATE / LOW) "
        "is assigned by fixed thresholds: CRITICAL ≥ 70%, HIGH ≥ 50%, MODERATE ≥ 30%, LOW < 30%. "
        "Top risk factor and first mitigation option are drawn from the route's risk profile."
    )
    _render_leaderboard(vulnerabilities)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Port Risk Heatmap
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Port Risk Heatmap",
        "Globe view — marker color = combined demand + congestion risk; size = TEU throughput",
    )
    st.caption(
        "Combined port risk = (demand score × 0.55) + (congestion index × 0.45), clamped to 0–100%. "
        "Demand score reflects capacity utilisation relative to forecast; congestion index is derived "
        "from vessel waiting times and berth occupancy rates. Marker size scales with annual "
        "throughput (TEU/yr). Drag the globe to reorient the view."
    )
    _render_port_heatmap(port_results)


# ---------------------------------------------------------------------------
# Integration note (for app.py)
# ---------------------------------------------------------------------------
# To add this tab to app.py, include a tab entry and call:
#
#   from ui import tab_risk_matrix
#   with tab_risk_matrix_st:
#       tab_risk_matrix.render(route_results, port_results, macro_data)
#
# where tab_risk_matrix_st is the Streamlit tab object returned by st.tabs([...]).
