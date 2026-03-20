"""Port Congestion History & Forecasting tab.

Sections
--------
1.  Congestion History Timeline   — multi-port line chart 2020-2026 with event annotations
2.  Heatmap Calendar              — GitHub-style monthly heatmap for selected port
3.  Congestion Forecast           — 7d / 30d / 90d bar chart with CI error bars
4.  Correlation Matrix            — port-to-port congestion correlations
5.  Incident Probability Monitor  — table of spike probabilities and risk factors
"""
from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
import streamlit as st

# ── Color palette (matches project-wide design system) ────────────────────────
C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_SURFACE = "#111827"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"   # green — low / healthy
C_WARN   = "#f59e0b"   # amber — moderate
C_DANGER = "#ef4444"   # red   — high / crisis
C_ACCENT = "#3b82f6"
C_CONV   = "#8b5cf6"
C_MACRO  = "#06b6d4"

# Traffic-light palette for congestion scoring
C_GREEN = "#10b981"
C_AMBER = "#f59e0b"
C_RED   = "#ef4444"

# ── Port trace colors (up to 10 ports) ────────────────────────────────────────
_PORT_COLORS = [
    "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
    "#06b6d4", "#f97316", "#ec4899", "#a3e635", "#fbbf24",
]

# ── Known ports for selection UI ──────────────────────────────────────────────
_DEFAULT_PORTS = ["USLAX", "CNSHA", "NLRTM", "SGSIN", "KRPUS"]

_PORT_DISPLAY = {
    "USLAX": "Los Angeles (USLAX)",
    "CNSHA": "Shanghai (CNSHA)",
    "NLRTM": "Rotterdam (NLRTM)",
    "SGSIN": "Singapore (SGSIN)",
    "KRPUS": "Busan (KRPUS)",
    "HKHKG": "Hong Kong (HKHKG)",
    "CNSZN": "Shenzhen/Yantian (CNSZN)",
    "DEHAM": "Hamburg (DEHAM)",
    "BEANR": "Antwerp (BEANR)",
    "USNYC": "New York (USNYC)",
}

# ── Major historical events for timeline annotations ──────────────────────────
_MAJOR_EVENTS = [
    {
        "date": "2020-04-01",
        "label": "COVID Collapse",
        "color": "rgba(239,68,68,0.7)",
        "desc": "Global demand collapse",
    },
    {
        "date": "2020-11-01",
        "label": "US Demand Surge",
        "color": "rgba(245,158,11,0.7)",
        "desc": "US stimulus + e-commerce boom",
    },
    {
        "date": "2021-03-23",
        "label": "Suez Blockage",
        "color": "rgba(239,68,68,0.85)",
        "desc": "Ever Given grounds — 6 days",
    },
    {
        "date": "2021-06-01",
        "label": "Yantian Closure",
        "color": "rgba(239,68,68,0.7)",
        "desc": "COVID cluster at Yantian terminal",
    },
    {
        "date": "2022-04-01",
        "label": "Shanghai Lockdown",
        "color": "rgba(239,68,68,0.85)",
        "desc": "City-wide COVID lockdown",
    },
    {
        "date": "2023-07-01",
        "label": "Normalisation",
        "color": "rgba(16,185,129,0.6)",
        "desc": "Global congestion normalises",
    },
    {
        "date": "2024-01-01",
        "label": "Red Sea Crisis",
        "color": "rgba(239,68,68,0.85)",
        "desc": "Houthi attacks — Cape rerouting",
    },
]

# Crisis period shading bands [start, end, label]
_CRISIS_BANDS = [
    ("2020-03-01", "2020-06-30", "COVID Collapse", "rgba(239,68,68,0.06)"),
    ("2020-10-01", "2022-01-31", "Demand Surge Crisis", "rgba(245,158,11,0.06)"),
    ("2022-03-15", "2022-08-31", "Shanghai Lockdown Backlog", "rgba(239,68,68,0.07)"),
    ("2024-01-01", "2025-06-30", "Red Sea Disruption", "rgba(245,158,11,0.06)"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        '<div style="color:' + C_TEXT2 + '; font-size:0.83rem; margin-top:3px">'
        + subtitle + "</div>"
        if subtitle
        else ""
    )
    st.markdown(
        '<div style="margin-bottom:14px; margin-top:4px">'
        '<div style="font-size:1.05rem; font-weight:700; color:' + C_TEXT + '">' + text + "</div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _card(content_html: str, border_color: str = C_BORDER) -> None:
    st.markdown(
        '<div style="background:' + C_CARD + '; border:1px solid ' + border_color + ";"
        ' border-radius:12px; padding:16px 18px; margin-bottom:10px">'
        + content_html + "</div>",
        unsafe_allow_html=True,
    )


def _score_color(score: float) -> str:
    if score < 0.4:
        return C_GREEN
    if score < 0.7:
        return C_AMBER
    return C_RED


def _incident_badge(incident_type: str) -> str:
    colors = {
        "NORMAL":   (C_GREEN, "rgba(16,185,129,0.12)"),
        "ELEVATED": (C_WARN,  "rgba(245,158,11,0.12)"),
        "SPIKE":    (C_RED,   "rgba(239,68,68,0.12)"),
        "CRISIS":   ("#b91c1c", "rgba(185,28,28,0.2)"),
    }
    txt_col, bg_col = colors.get(incident_type, (C_TEXT2, "rgba(148,163,184,0.1)"))
    return (
        '<span style="display:inline-block; padding:1px 8px; border-radius:999px;'
        ' font-size:0.72rem; font-weight:700; text-transform:uppercase;'
        ' letter-spacing:0.04em; background:' + bg_col + '; color:' + txt_col + '">'
        + incident_type + "</span>"
    )


def _dark_layout(height: int = 400, title: str = "") -> dict:
    base: dict = {
        "paper_bgcolor": C_BG,
        "plot_bgcolor": C_SURFACE,
        "font": {"color": C_TEXT, "family": "Inter, sans-serif", "size": 12},
        "height": height,
        "margin": {"l": 20, "r": 20, "t": 45 if title else 20, "b": 20},
        "hoverlabel": {
            "bgcolor": C_CARD,
            "bordercolor": "rgba(255,255,255,0.15)",
            "font": {"color": C_TEXT, "size": 12},
        },
        "xaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.08)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(255,255,255,0.08)",
        },
        "yaxis": {
            "gridcolor": "rgba(255,255,255,0.05)",
            "zerolinecolor": "rgba(255,255,255,0.08)",
            "tickfont": {"color": C_TEXT3, "size": 11},
            "linecolor": "rgba(255,255,255,0.08)",
        },
    }
    if title:
        base["title"] = {"text": title, "font": {"size": 14, "color": C_TEXT}, "x": 0.01}
    return base


# ── Section 1: Congestion History Timeline ────────────────────────────────────

def _render_timeline(selected_ports: list) -> None:
    """Multi-port congestion history line chart 2020-2026 with event annotations."""
    try:
        from processing.congestion_history import CONGESTION_HISTORY
    except ImportError:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:20px">'
            "Congestion history module unavailable.</div>"
        )
        return

    fig = go.Figure()

    # ── Crisis / normal background bands ──────────────────────────────────────
    for band_start, band_end, band_label, band_color in _CRISIS_BANDS:
        fig.add_vrect(
            x0=band_start,
            x1=band_end,
            fillcolor=band_color,
            line_width=0,
            annotation_text=band_label,
            annotation_position="top left",
            annotation=dict(
                font=dict(size=9, color=C_TEXT3),
                showarrow=False,
            ),
        )

    # ── Port traces ───────────────────────────────────────────────────────────
    for idx, locode in enumerate(selected_ports[:5]):
        records = CONGESTION_HISTORY.get(locode, [])
        if not records:
            continue
        records_sorted = sorted(records, key=lambda r: r.date)
        dates = [str(r.date) for r in records_sorted]
        scores = [r.congestion_score for r in records_sorted]
        hover_texts = [
            "<b>" + locode + "</b><br>"
            "Date: " + str(r.date) + "<br>"
            "Score: " + str(round(r.congestion_score, 2)) + "<br>"
            "Type: " + r.incident_type + "<br>"
            "Driver: " + r.driver + "<br>"
            "Vessels: " + str(r.vessel_count) + "<br>"
            "Avg wait: " + str(r.avg_wait_days) + "d"
            + ("<br>" + r.notes if r.notes else "")
            for r in records_sorted
        ]
        color = _PORT_COLORS[idx % len(_PORT_COLORS)]
        display_name = _PORT_DISPLAY.get(locode, locode)

        fig.add_trace(go.Scatter(
            x=dates,
            y=scores,
            mode="lines+markers",
            name=display_name,
            line=dict(color=color, width=2.2),
            marker=dict(
                size=6,
                color=color,
                symbol="circle",
                line=dict(color="rgba(0,0,0,0.4)", width=1),
            ),
            hovertext=hover_texts,
            hoverinfo="text",
        ))

    # ── Event vertical lines ──────────────────────────────────────────────────
    for ev in _MAJOR_EVENTS:
        fig.add_vline(
            x=ev["date"],
            line_width=1.5,
            line_dash="dash",
            line_color=ev["color"],
            annotation_text=ev["label"],
            annotation_position="top right",
            annotation=dict(
                font=dict(size=9, color=ev["color"]),
                showarrow=False,
                yanchor="top",
            ),
        )

    # ── Threshold reference lines ─────────────────────────────────────────────
    fig.add_hline(y=0.70, line_width=1, line_dash="dot",
                  line_color="rgba(239,68,68,0.4)",
                  annotation_text="Crisis threshold (0.70)",
                  annotation_font_size=9,
                  annotation_font_color="rgba(239,68,68,0.7)")
    fig.add_hline(y=0.40, line_width=1, line_dash="dot",
                  line_color="rgba(245,158,11,0.4)",
                  annotation_text="Elevated threshold (0.40)",
                  annotation_font_size=9,
                  annotation_font_color="rgba(245,158,11,0.7)")

    layout = _dark_layout(height=440)
    layout["showlegend"] = True
    layout["legend"] = {
        "bgcolor": "rgba(0,0,0,0)",
        "bordercolor": C_BORDER,
        "font": {"color": C_TEXT2, "size": 11},
        "orientation": "h",
        "yanchor": "bottom",
        "y": 1.02,
        "xanchor": "right",
        "x": 1,
    }
    layout["xaxis"]["title"] = "Date"
    layout["xaxis"]["range"] = ["2020-01-01", "2026-06-01"]
    layout["yaxis"]["title"] = "Congestion Score"
    layout["yaxis"]["range"] = [0.0, 1.0]
    layout["yaxis"]["tickformat"] = ".0%"
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True)


# ── Section 2: Heatmap Calendar ───────────────────────────────────────────────

def _render_heatmap_calendar(port_locode: str) -> None:
    """GitHub contribution-style heatmap: rows = years, columns = months."""
    try:
        from processing.congestion_history import CONGESTION_HISTORY
    except ImportError:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "Module unavailable.</div>"
        )
        return

    records = CONGESTION_HISTORY.get(port_locode, [])
    if not records:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "No historical data for " + port_locode + ".</div>"
        )
        return

    # Build lookup: (year, month) -> congestion_score
    score_map: dict = {}
    for r in records:
        score_map[(r.date.year, r.date.month)] = r.congestion_score

    years = sorted({r.date.year for r in records})
    months = list(range(1, 13))
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    z_matrix = []
    hover_matrix = []
    today = date.today()

    for yr in reversed(years):
        row_z = []
        row_h = []
        for mo in months:
            score = score_map.get((yr, mo))
            row_z.append(score if score is not None else None)
            if score is None:
                row_h.append("No data")
            else:
                row_h.append(
                    "<b>" + str(yr) + "-" + month_labels[mo - 1] + "</b><br>"
                    "Score: " + str(round(score, 2))
                )
        z_matrix.append(row_z)
        hover_matrix.append(row_h)

    year_labels = [str(y) for y in reversed(years)]

    fig = go.Figure(go.Heatmap(
        z=z_matrix,
        x=month_labels,
        y=year_labels,
        colorscale=[
            [0.0,  "#0d1f2d"],
            [0.25, "#10b981"],
            [0.50, "#f59e0b"],
            [0.75, "#ef4444"],
            [1.0,  "#7f1d1d"],
        ],
        zmin=0.0,
        zmax=1.0,
        hovertext=hover_matrix,
        hoverinfo="text",
        showscale=True,
        colorbar=dict(
            title=dict(text="Congestion", font=dict(size=11, color=C_TEXT2)),
            tickvals=[0.0, 0.25, 0.50, 0.75, 1.0],
            ticktext=["0%", "25%", "50%", "75%", "100%"],
            tickfont=dict(size=10, color=C_TEXT2),
            bgcolor=C_BG,
            outlinecolor=C_BORDER,
            outlinewidth=1,
            thickness=14,
            len=0.85,
        ),
        xgap=3,
        ygap=3,
    ))

    # Bold border on current month
    cur_month_idx = today.month - 1
    cur_year_str = str(today.year)
    if cur_year_str in year_labels:
        cur_row = year_labels.index(cur_year_str)
        fig.add_shape(
            type="rect",
            x0=cur_month_idx - 0.5,
            x1=cur_month_idx + 0.5,
            y0=cur_row - 0.5,
            y1=cur_row + 0.5,
            line=dict(color=C_ACCENT, width=2.5),
            fillcolor="rgba(0,0,0,0)",
        )

    layout = _dark_layout(height=max(200, 45 * len(years) + 80))
    layout["plot_bgcolor"] = C_BG
    layout["xaxis"]["showgrid"] = False
    layout["yaxis"]["showgrid"] = False
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True)


# ── Section 3: Congestion Forecast Bars ──────────────────────────────────────

def _render_forecast_chart(port_results: list, macro_data: dict) -> None:
    """Bar chart: current + 7d/30d/90d forecast with CI error bars and traffic-light color."""
    try:
        from processing.congestion_history import forecast_all_ports
    except ImportError:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "Forecast module unavailable.</div>"
        )
        return

    forecasts = forecast_all_ports(port_results, macro_data)
    if not forecasts:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "No port results available for forecasting.</div>"
        )
        return

    port_locodes = list(forecasts.keys())[:8]
    horizons = ["Current", "7-Day", "30-Day", "90-Day"]
    horizon_colors = [C_ACCENT, C_GREEN, C_WARN, C_RED]

    fig = go.Figure()

    for h_idx, (horizon_label, h_color) in enumerate(zip(horizons, horizon_colors)):
        y_vals = []
        e_lower = []
        e_upper = []
        hover_texts = []

        for locode in port_locodes:
            fc = forecasts[locode]
            if h_idx == 0:
                val = fc.current_score
                el = 0.0
                eu = 0.0
            elif h_idx == 1:
                val = fc.forecast_7d
                el = max(0.0, val - fc.ci_lower_7d)
                eu = max(0.0, fc.ci_upper_7d - val)
            elif h_idx == 2:
                val = fc.forecast_30d
                el = max(0.0, val - fc.ci_lower_30d)
                eu = max(0.0, fc.ci_upper_30d - val)
            else:
                val = fc.forecast_90d
                el = max(0.0, val - fc.ci_lower_90d)
                eu = max(0.0, fc.ci_upper_90d - val)

            y_vals.append(round(val, 3))
            e_lower.append(round(el, 3))
            e_upper.append(round(eu, 3))
            hover_texts.append(
                "<b>" + _PORT_DISPLAY.get(locode, locode) + " — " + horizon_label + "</b><br>"
                "Score: " + str(round(val, 2)) + "<br>"
                "Trend: " + fc.trend + "<br>"
                "Confidence: " + str(round(fc.confidence * 100)) + "%<br>"
                "Spike prob (30d): " + str(round(fc.incident_probability * 100)) + "%"
            )

        # Individual bar colors by score level
        bar_colors = [_score_color(v) for v in y_vals]

        fig.add_trace(go.Bar(
            name=horizon_label,
            x=[_PORT_DISPLAY.get(lc, lc).split(" (")[0] for lc in port_locodes],
            y=y_vals,
            marker=dict(
                color=bar_colors if h_idx == 0 else h_color,
                opacity=0.85 - h_idx * 0.1,
                line=dict(color="rgba(0,0,0,0.3)", width=1),
            ),
            error_y=dict(
                type="data",
                symmetric=False,
                array=e_upper,
                arrayminus=e_lower,
                color="rgba(255,255,255,0.4)",
                thickness=1.5,
                width=5,
            ) if any(eu > 0 for eu in e_upper) else None,
            hovertext=hover_texts,
            hoverinfo="text",
        ))

    layout = _dark_layout(height=380)
    layout["barmode"] = "group"
    layout["showlegend"] = True
    layout["legend"] = {
        "bgcolor": "rgba(0,0,0,0)",
        "bordercolor": C_BORDER,
        "font": {"color": C_TEXT2, "size": 11},
        "orientation": "h",
        "yanchor": "bottom",
        "y": 1.02,
        "xanchor": "right",
        "x": 1,
    }
    layout["yaxis"]["range"] = [0.0, 1.0]
    layout["yaxis"]["tickformat"] = ".0%"
    layout["yaxis"]["title"] = "Congestion Score"
    layout["xaxis"]["title"] = "Port"

    # Traffic-light reference lines
    fig.add_hline(y=0.70, line_width=1, line_dash="dot",
                  line_color="rgba(239,68,68,0.5)")
    fig.add_hline(y=0.40, line_width=1, line_dash="dot",
                  line_color="rgba(245,158,11,0.5)")

    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True)

    # Traffic-light legend
    st.markdown(
        '<div style="display:flex; gap:16px; margin-top:-4px; margin-bottom:8px">'
        '<span style="font-size:0.78rem; color:' + C_GREEN + '">'
        '&#9632; Low (&lt;0.40)</span>'
        '<span style="font-size:0.78rem; color:' + C_WARN + '">'
        '&#9632; Amber (0.40-0.70)</span>'
        '<span style="font-size:0.78rem; color:' + C_RED + '">'
        '&#9632; High (&gt;0.70)</span>'
        '</div>',
        unsafe_allow_html=True,
    )


# ── Section 4: Correlation Matrix ─────────────────────────────────────────────

def _render_correlation_matrix(selected_ports: list) -> None:
    """Heatmap of pairwise port congestion correlations."""
    try:
        from processing.congestion_history import compute_congestion_correlation_matrix
    except ImportError:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "Correlation module unavailable.</div>"
        )
        return

    if len(selected_ports) < 2:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "Select at least 2 ports to compute correlation.</div>"
        )
        return

    corr_df = compute_congestion_correlation_matrix(selected_ports)
    if corr_df.empty:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "Insufficient overlapping data to compute correlations.</div>"
        )
        return

    port_labels = [_PORT_DISPLAY.get(p, p).split(" (")[0] for p in selected_ports]
    z_vals = corr_df.values.tolist()

    # Build annotation text for each cell
    annotations = []
    for i, row_vals in enumerate(z_vals):
        for j, val in enumerate(row_vals):
            annotations.append(dict(
                x=port_labels[j],
                y=port_labels[i],
                text=str(round(val, 2)),
                font=dict(
                    size=11,
                    color=C_TEXT if abs(val) < 0.7 else "#ffffff",
                    family="Inter, sans-serif",
                ),
                showarrow=False,
            ))

    fig = go.Figure(go.Heatmap(
        z=z_vals,
        x=port_labels,
        y=port_labels,
        colorscale=[
            [0.0,  "#1e3a5f"],
            [0.25, "#1e40af"],
            [0.50, "#94a3b8"],
            [0.75, "#f59e0b"],
            [1.0,  "#ef4444"],
        ],
        zmin=-1.0,
        zmax=1.0,
        hovertemplate="<b>%{y} vs %{x}</b><br>Correlation: %{z:.3f}<extra></extra>",
        showscale=True,
        colorbar=dict(
            title=dict(text="Pearson r", font=dict(size=11, color=C_TEXT2)),
            tickvals=[-1.0, -0.5, 0.0, 0.5, 1.0],
            ticktext=["-1.0", "-0.5", "0.0", "0.5", "1.0"],
            tickfont=dict(size=10, color=C_TEXT2),
            bgcolor=C_BG,
            outlinecolor=C_BORDER,
            outlinewidth=1,
            thickness=14,
        ),
        xgap=2,
        ygap=2,
    ))
    fig.update_layout(annotations=annotations)

    n = len(selected_ports)
    cell_size = 70
    layout = _dark_layout(height=max(280, n * cell_size + 80))
    layout["plot_bgcolor"] = C_BG
    layout["xaxis"]["showgrid"] = False
    layout["yaxis"]["showgrid"] = False
    layout["xaxis"]["side"] = "bottom"
    fig.update_layout(**layout)

    st.plotly_chart(fig, use_container_width=True)

    # Cluster interpretation note
    _card(
        '<div style="font-size:0.82rem; color:' + C_TEXT2 + '; line-height:1.6">'
        '<span style="color:' + C_DANGER + '; font-weight:600">Red (r &gt; 0.7)</span>'
        " — ports in the same congestion system (e.g. LA + Shanghai during demand surge). "
        '<span style="color:' + C_ACCENT + '; font-weight:600">Blue (r &lt; 0)</span>'
        " — counter-cyclical ports (e.g. cargo diversion benefits one when another is closed)."
        "</div>",
        border_color="rgba(59,130,246,0.2)",
    )


# ── Section 5: Incident Probability Monitor ───────────────────────────────────

def _render_incident_monitor(port_results: list, macro_data: dict) -> None:
    """Styled table: port, current congestion, spike probability, last incident, risk factors."""
    try:
        from processing.congestion_history import (
            CONGESTION_HISTORY,
            forecast_all_ports,
            get_last_major_incident,
            get_incident_frequency,
        )
    except ImportError:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "Monitor module unavailable.</div>"
        )
        return

    forecasts = forecast_all_ports(port_results, macro_data)
    if not forecasts:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:12px">'
            "No port results available for monitor.</div>"
        )
        return

    # Primary risk factors by driver pattern
    _RISK_LABELS: dict = {
        "WEATHER":        "Weather events",
        "LABOR":          "Labor / industrial action",
        "DEMAND_SURGE":   "Demand surge / volume spike",
        "EQUIPMENT":      "Equipment failures",
        "INFRASTRUCTURE": "Infrastructure / geopolitical",
        "PANDEMIC":       "Pandemic / health emergency",
    }

    rows_html = ""
    sorted_locodes = sorted(
        forecasts.keys(),
        key=lambda lc: forecasts[lc].incident_probability,
        reverse=True,
    )

    for locode in sorted_locodes:
        fc = forecasts[locode]
        display = _PORT_DISPLAY.get(locode, locode)

        # Congestion level bar
        score = fc.current_score
        sc_color = _score_color(score)
        bar_pct = int(score * 100)

        # Spike probability badge
        sp = fc.incident_probability
        if sp >= 0.50:
            sp_color = C_RED
            sp_bg = "rgba(239,68,68,0.12)"
        elif sp >= 0.25:
            sp_color = C_WARN
            sp_bg = "rgba(245,158,11,0.12)"
        else:
            sp_color = C_GREEN
            sp_bg = "rgba(16,185,129,0.12)"
        sp_pct_str = str(round(sp * 100)) + "%"

        # Last major incident
        last_inc = get_last_major_incident(locode)
        if last_inc:
            last_inc_str = str(last_inc.date) + " (" + last_inc.incident_type + ")"
        else:
            last_inc_str = "No major incidents on record"

        # Primary risk factors from last few records
        records = CONGESTION_HISTORY.get(locode, [])
        recent = sorted(records, key=lambda r: r.date)[-6:]
        driver_counts: dict = {}
        for rr in recent:
            driver_counts[rr.driver] = driver_counts.get(rr.driver, 0) + 1
        top_drivers = sorted(driver_counts, key=lambda d: driver_counts[d], reverse=True)[:2]
        risk_factors = " / ".join(_RISK_LABELS.get(d, d) for d in top_drivers) or "—"

        rows_html += (
            "<tr>"
            '<td style="padding:10px 8px; white-space:nowrap; font-weight:600; color:' + C_TEXT + '">'
            + display + "</td>"
            '<td style="padding:10px 8px; min-width:130px">'
            '<div style="display:flex; align-items:center; gap:8px">'
            '<div style="flex:1; background:#374151; border-radius:4px; height:7px">'
            '<div style="width:' + str(bar_pct) + '%; background:' + sc_color + '; border-radius:4px; height:7px"></div>'
            "</div>"
            '<span style="font-size:0.82rem; font-weight:700; color:' + sc_color + '; min-width:34px">'
            + str(bar_pct) + "%</span>"
            "</div></td>"
            '<td style="padding:10px 8px; text-align:center">'
            '<span style="display:inline-block; padding:2px 10px; border-radius:999px;'
            ' font-size:0.78rem; font-weight:700; background:' + sp_bg + '; color:' + sp_color + '">'
            + sp_pct_str + "</span></td>"
            '<td style="padding:10px 8px; color:' + C_TEXT2 + '; font-size:0.80rem">'
            + last_inc_str + "</td>"
            '<td style="padding:10px 8px; color:' + C_TEXT2 + '; font-size:0.80rem; line-height:1.4">'
            + risk_factors + "</td>"
            "</tr>"
        )

    header_style = (
        "color:" + C_TEXT3 + "; font-size:0.72rem; text-transform:uppercase;"
        " letter-spacing:0.07em; padding:6px 8px; text-align:left;"
        " border-bottom:1px solid rgba(255,255,255,0.08)"
    )
    table_html = (
        '<table style="width:100%; border-collapse:collapse">'
        "<thead><tr>"
        '<th style="' + header_style + '">Port</th>'
        '<th style="' + header_style + '">Congestion</th>'
        '<th style="' + header_style + '">Spike Prob (30d)</th>'
        '<th style="' + header_style + '">Last Major Incident</th>'
        '<th style="' + header_style + '">Primary Risk Factors</th>'
        "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table>"
    )

    st.markdown(
        '<div style="background:' + C_CARD + '; border:1px solid rgba(59,130,246,0.2);'
        ' border-radius:12px; padding:16px 18px; margin-bottom:10px; overflow-x:auto">'
        + table_html + "</div>",
        unsafe_allow_html=True,
    )


# ── Main render function ──────────────────────────────────────────────────────

def render(
    port_results: list,
    freight_data: dict,
    macro_data: dict,
) -> None:
    """Render the Port Congestion History and Forecasting tab.

    Parameters
    ----------
    port_results: List of port result objects/dicts (need port_locode, optional
                  current_congestion).
    freight_data: Freight data dict (unused directly; passed for consistency).
    macro_data:   Macro indicator dict (BDI_rising, PMI, ISM, etc.).
    """
    st.header("Port Congestion: History & Forecast")

    # ── Derive available locodes ───────────────────────────────────────────────
    try:
        from processing.congestion_history import CONGESTION_HISTORY
        all_locodes = sorted(CONGESTION_HISTORY.keys())
    except ImportError:
        all_locodes = _DEFAULT_PORTS

    # Also include any locodes from port_results
    for pr in port_results:
        lc = (
            pr.get("port_locode") if isinstance(pr, dict)
            else (getattr(pr, "port_locode", None) or getattr(pr, "locode", None))
        )
        if lc and lc not in all_locodes:
            all_locodes.append(lc)

    display_options = [
        _PORT_DISPLAY.get(lc, lc) for lc in all_locodes
    ]

    # ── Controls row ──────────────────────────────────────────────────────────
    ctrl_col1, ctrl_col2 = st.columns([3, 2])

    with ctrl_col1:
        selected_displays = st.multiselect(
            "Ports for timeline (max 5)",
            options=display_options,
            default=[
                _PORT_DISPLAY.get(lc, lc)
                for lc in _DEFAULT_PORTS
                if _PORT_DISPLAY.get(lc, lc) in display_options
            ][:5],
            max_selections=5,
            key="congestion_port_select",
        )
        # Reverse-map display name -> locode
        display_to_locode = {v: k for k, v in _PORT_DISPLAY.items()}
        selected_ports = [display_to_locode.get(d, d.split(" (")[-1].rstrip(")")) for d in selected_displays]

    with ctrl_col2:
        heatmap_display = st.selectbox(
            "Port for calendar heatmap",
            options=display_options,
            index=0,
            key="congestion_heatmap_port",
        )
        heatmap_locode = display_to_locode.get(heatmap_display, heatmap_display.split(" (")[-1].rstrip(")"))

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Congestion History Timeline
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Congestion History Timeline (2020 - 2026)",
        "Monthly congestion scores with annotated crisis events and periods",
    )
    _render_timeline(selected_ports)

    # ── Event legend ──────────────────────────────────────────────────────────
    legend_items = [
        ("COVID Collapse (Apr 2020)", "rgba(239,68,68,0.7)"),
        ("US Demand Surge (Nov 2020)", "rgba(245,158,11,0.7)"),
        ("Suez Blockage (Mar 2021)", "rgba(239,68,68,0.85)"),
        ("Yantian Closure (Jun 2021)", "rgba(239,68,68,0.7)"),
        ("Shanghai Lockdown (Apr 2022)", "rgba(239,68,68,0.85)"),
        ("Red Sea Crisis (Jan 2024)", "rgba(239,68,68,0.85)"),
    ]
    legend_html = '<div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:8px">'
    for label, color in legend_items:
        legend_html += (
            '<span style="display:inline-flex; align-items:center; gap:5px;'
            ' font-size:0.75rem; color:' + C_TEXT2 + '">'
            '<span style="display:inline-block; width:18px; height:2px; border-top:2px dashed '
            + color + '"></span>' + label + "</span>"
        )
    legend_html += "</div>"
    st.markdown(legend_html, unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — Heatmap Calendar
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Monthly Congestion Calendar — " + _PORT_DISPLAY.get(heatmap_locode, heatmap_locode),
        "Year-over-year monthly congestion heatmap (GitHub contribution style) — blue border = current month",
    )
    _render_heatmap_calendar(heatmap_locode)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Forecast Bars
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Congestion Forecast — 7d / 30d / 90d",
        "Exponential smoothing with seasonal + macro adjustment and Monte Carlo confidence intervals",
    )

    if not port_results:
        _card(
            '<div style="color:' + C_TEXT2 + '; text-align:center; padding:16px">'
            "Run analysis with port data to populate forecasts.</div>"
        )
    else:
        _render_forecast_chart(port_results, macro_data)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Correlation Matrix
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Port Congestion Correlation Matrix",
        "Pearson correlation of monthly congestion scores — high correlation = same systemic shock",
    )
    _render_correlation_matrix(selected_ports)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Incident Probability Monitor
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Incident Probability Monitor",
        "Current congestion level, 30-day spike probability, last major incident, and primary risk factors",
    )

    if not port_results:
        # Fall back to showing all historical ports
        fallback_results = [
            {"port_locode": lc, "current_congestion": None}
            for lc in _DEFAULT_PORTS
        ]
        _render_incident_monitor(fallback_results, macro_data)
    else:
        _render_incident_monitor(port_results, macro_data)
