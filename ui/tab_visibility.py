"""tab_visibility.py — Supply Chain Visibility tab.

Renders the "Track Your Supply Chain" module: interactive globe path map,
journey timeline, bottleneck analyser, disruption simulator, and resilience
score cards for five major product categories.
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.supply_chain_visibility import (
    EXAMPLE_PATHS,
    SupplyChainPath,
    get_bottleneck_details,
    recommended_buffer_days,
    simulate_disruption,
)

# ---------------------------------------------------------------------------
# Colour palette
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
C_ORANGE = "#f97316"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"

# Transport mode colours
_MODE_COLOR: dict[str, str] = {
    "OCEAN":    C_ACCENT,
    "RAIL":     C_WARN,
    "TRUCK":    C_ORANGE,
    "PIPELINE": C_PURPLE,
    "INLAND":   C_CYAN,
}

# Node type colours
_TYPE_COLOR: dict[str, str] = {
    "FACTORY":      "#ec4899",
    "PORT":         C_ACCENT,
    "RAIL":         C_WARN,
    "WAREHOUSE":    C_CYAN,
    "DISTRIBUTION": C_HIGH,
}

# Node type → Plotly marker symbol
_TYPE_SYMBOL: dict[str, str] = {
    "FACTORY":      "square",
    "PORT":         "circle",
    "RAIL":         "diamond",
    "WAREHOUSE":    "diamond",
    "DISTRIBUTION": "triangle-up",
}

# Status colours
_STATUS_COLOR: dict[str, str] = {
    "OPERATIONAL": C_HIGH,
    "DELAYED":     C_WARN,
    "DISRUPTED":   C_DANGER,
    "CLOSED":      "#64748b",
}

# Risk band colours
_RISK_COLOR_BAND: list[tuple[float, str]] = [
    (0.30, C_HIGH),
    (0.50, C_WARN),
    (0.70, C_ORANGE),
    (1.01, C_DANGER),
]


def _risk_color(score: float) -> str:
    for threshold, color in _RISK_COLOR_BAND:
        if score < threshold:
            return color
    return C_DANGER


def _score_bar_html(score: float, width_px: int = 100) -> str:
    pct = int(score * 100)
    color = _risk_color(score)
    return (
        "<div style='display:inline-block; vertical-align:middle; width:"
        + str(width_px)
        + "px; background:rgba(255,255,255,0.07); border-radius:4px;"
        + " height:6px; overflow:hidden; margin-right:6px'>"
        + "<div style='width:"
        + str(pct)
        + "%; height:100%; background:"
        + color
        + "; border-radius:4px'></div></div>"
        + "<span style='font-size:0.71rem; color:"
        + color
        + "; font-weight:700'>"
        + str(pct)
        + "%</span>"
    )


def _dark_layout(height: int = 450) -> dict:
    return dict(
        height=height,
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        margin=dict(l=0, r=0, t=0, b=0),
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=12),
    )


def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        "<div style='color:" + C_TEXT2 + "; font-size:0.81rem;"
        " margin-top:3px; line-height:1.5'>" + subtitle + "</div>"
        if subtitle else ""
    )
    st.markdown(
        "<div style='margin-bottom:12px; margin-top:4px'>"
        "<div style='font-size:1.05rem; font-weight:700; color:"
        + C_TEXT + "'>" + text + "</div>"
        + sub_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _card(content_html: str, border_color: str = C_BORDER) -> str:
    return (
        "<div style='background:" + C_CARD + "; border:1px solid "
        + border_color + "; border-radius:12px; padding:16px 18px;"
        " margin-bottom:10px'>" + content_html + "</div>"
    )


# ---------------------------------------------------------------------------
# Section 1 — Supply Chain Map
# ---------------------------------------------------------------------------

def _render_supply_chain_map(path: SupplyChainPath) -> None:
    """Plotly Scattergeo dark globe showing the full supply chain path."""
    nodes = path.nodes
    modes = path.transit_modes

    traces: list[go.BaseTraceType] = []

    # ── Connecting arcs between consecutive nodes (coloured by transport mode) ──
    for i in range(len(nodes) - 1):
        n0 = nodes[i]
        n1 = nodes[i + 1]
        mode = modes[i] if i < len(modes) else "OCEAN"
        line_color = _MODE_COLOR.get(mode, C_TEXT2)

        # Interpolate intermediate points for a great-circle-like arc
        steps = 8
        lats = [n0.lat + (n1.lat - n0.lat) * t / steps for t in range(steps + 1)]
        lons = [n0.lon + (n1.lon - n0.lon) * t / steps for t in range(steps + 1)]

        # Full-opacity arc
        traces.append(go.Scattergeo(
            lat=lats,
            lon=lons,
            mode="lines",
            line=dict(width=2.5, color=line_color),
            opacity=0.85,
            name=mode,
            showlegend=False,
            hoverinfo="skip",
        ))

        # Ghost arcs at decreasing opacity for animated-path feel
        for alpha_idx, opacity in enumerate([0.35, 0.18]):
            offset = (alpha_idx + 1) * 0.5
            ghost_lats = [
                n0.lat + (n1.lat - n0.lat) * t / steps
                for t in range(steps + 1)
            ]
            ghost_lons = [
                n0.lon + (n1.lon - n0.lon) * t / steps + offset
                for t in range(steps + 1)
            ]
            traces.append(go.Scattergeo(
                lat=ghost_lats,
                lon=ghost_lons,
                mode="lines",
                line=dict(width=1.5, color=line_color),
                opacity=opacity,
                showlegend=False,
                hoverinfo="skip",
            ))

    # ── Mode legend entries ─────────────────────────────────────────────────
    seen_modes: set = set()
    for mode in modes:
        if mode not in seen_modes:
            seen_modes.add(mode)
            traces.append(go.Scattergeo(
                lat=[None], lon=[None],
                mode="lines",
                line=dict(width=3, color=_MODE_COLOR.get(mode, C_TEXT2)),
                name=mode,
                showlegend=True,
            ))

    # ── Node markers ────────────────────────────────────────────────────────
    for node in nodes:
        status_color = _STATUS_COLOR.get(node.status, C_TEXT2)
        type_color   = _TYPE_COLOR.get(node.node_type, C_TEXT2)
        symbol       = _TYPE_SYMBOL.get(node.node_type, "circle")

        hover_text = (
            "<b>" + node.location_name + "</b><br>"
            + "Type: " + node.node_type + "<br>"
            + "Status: " + node.status + "<br>"
            + "Utilisation: " + str(round(node.current_utilization * 100, 1)) + "%<br>"
            + "Delay: " + str(node.delay_days) + " days<br>"
            + "Risk: " + str(round(node.risk_score * 100, 1)) + "%"
        )

        traces.append(go.Scattergeo(
            lat=[node.lat],
            lon=[node.lon],
            mode="markers+text",
            marker=dict(
                size=14,
                color=type_color,
                symbol=symbol,
                line=dict(color=status_color, width=2),
            ),
            text=[node.location_name.split(",")[0].split("(")[0].strip()],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            hovertemplate=hover_text + "<extra></extra>",
            showlegend=False,
        ))

    layout = go.Layout(
        **_dark_layout(height=550),
        geo=dict(
            showland=True,
            landcolor="#1a2235",
            showocean=True,
            oceancolor="#0a1628",
            showcountries=True,
            countrycolor="rgba(255,255,255,0.08)",
            showcoastlines=True,
            coastlinecolor="rgba(255,255,255,0.12)",
            showframe=False,
            projection_type="natural earth",
            bgcolor=C_BG,
        ),
        legend=dict(
            orientation="h",
            x=0.01, y=0.01,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(10,15,26,0.7)",
            bordercolor=C_BORDER,
            borderwidth=1,
        ),
        title=dict(
            text="<b>" + path.product_category + "</b>  Supply Chain Path",
            font=dict(size=13, color=C_TEXT),
            x=0.01,
            y=0.97,
        ),
    )

    fig = go.Figure(data=traces, layout=layout)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Section 2 — Journey Timeline (Gantt-style)
# ---------------------------------------------------------------------------

def _render_journey_timeline(path: SupplyChainPath) -> None:
    """Horizontal Gantt chart: each node/segment as a time block."""
    nodes = path.nodes
    modes = path.transit_modes

    # Allocate transit days roughly proportional by mode
    _MODE_DAYS: dict[str, int] = {
        "OCEAN": 18, "RAIL": 4, "TRUCK": 2, "PIPELINE": 1, "INLAND": 3,
    }

    # Build segments: (label, days, color, is_node)
    segments: list[dict] = []
    total_mode_days = sum(_MODE_DAYS.get(m, 3) for m in modes)
    if total_mode_days == 0:
        total_mode_days = 1

    # Remaining days go to node dwell time
    node_dwell_total = max(0, path.total_transit_days - total_mode_days)
    node_dwell_each  = max(1, node_dwell_total // max(1, len(nodes)))

    for i, node in enumerate(nodes):
        risk_c = _risk_color(node.risk_score)
        segments.append({
            "label": node.location_name.split(",")[0].split("(")[0].strip(),
            "days": node_dwell_each + node.delay_days,
            "color": risk_c,
            "is_node": True,
            "node_type": node.node_type,
            "status": node.status,
        })
        if i < len(modes):
            mode = modes[i]
            seg_days = _MODE_DAYS.get(mode, 3)
            segments.append({
                "label": mode,
                "days": seg_days,
                "color": _MODE_COLOR.get(mode, C_TEXT2),
                "is_node": False,
                "node_type": "",
                "status": "",
            })

    total_days = sum(s["days"] for s in segments)

    fig = go.Figure()

    x_start = 0.0
    for seg in segments:
        width_pct = seg["days"] / total_days * 100.0 if total_days > 0 else 0
        x_end = x_start + width_pct

        hover = (
            "<b>" + seg["label"] + "</b><br>"
            + str(seg["days"]) + " days"
            + ("<br>Type: " + seg["node_type"] if seg["is_node"] else "")
            + ("<br>Status: " + seg["status"] if seg["status"] else "")
        )

        fig.add_trace(go.Bar(
            x=[width_pct],
            y=["Journey"],
            orientation="h",
            base=x_start,
            marker=dict(
                color=seg["color"],
                opacity=0.85 if seg["is_node"] else 0.55,
                line=dict(color="rgba(0,0,0,0.3)", width=1),
            ),
            text=seg["label"] if width_pct > 5 else "",
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=9, color=C_TEXT),
            hovertemplate=hover + "<extra></extra>",
            showlegend=False,
        ))
        x_start = x_end

    # Current position marker — roughly 40% through for "in progress"
    progress_x = total_days * 0.40
    progress_pct = progress_x / total_days * 100.0 if total_days > 0 else 0
    fig.add_vline(
        x=progress_pct,
        line=dict(color=C_HIGH, width=2, dash="dot"),
        annotation_text="Now",
        annotation_font=dict(color=C_HIGH, size=10),
    )

    fig.update_layout(
        **_dark_layout(height=140),
        barmode="stack",
        xaxis=dict(
            title="% of total transit time (" + str(path.total_transit_days) + " days)",
            range=[0, 100],
            showgrid=False,
            tickfont=dict(size=9, color=C_TEXT3),
            titlefont=dict(size=9, color=C_TEXT2),
        ),
        yaxis=dict(showticklabels=False, showgrid=False),
    )

    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Section 3 — Bottleneck Analyser
# ---------------------------------------------------------------------------

def _render_bottleneck_analyser(paths: list[SupplyChainPath]) -> None:
    """Bar chart of systemic bottleneck nodes, risk scores, and impact text."""
    details = get_bottleneck_details(paths)

    if not details:
        st.info("No shared bottleneck nodes detected across the selected paths.")
        return

    labels = [d["location_name"].split(",")[0].split("(")[0].strip() for d in details]
    counts = [d["path_count"] for d in details]
    risks  = [d["risk_score"] for d in details]
    colors = [_risk_color(r) for r in risks]
    pcts   = [d["pct_affected"] for d in details]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=labels,
        y=counts,
        marker=dict(color=colors, opacity=0.85, line=dict(color="rgba(0,0,0,0.2)", width=1)),
        text=[str(c) + " paths" for c in counts],
        textposition="outside",
        textfont=dict(size=10, color=C_TEXT2),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Appears in %{y} supply chains<br>"
            "<extra></extra>"
        ),
        name="Path count",
    ))

    fig.add_trace(go.Scatter(
        x=labels,
        y=risks,
        mode="markers+lines",
        marker=dict(size=10, color=C_WARN, symbol="diamond"),
        line=dict(color=C_WARN, width=1.5, dash="dot"),
        yaxis="y2",
        name="Risk score",
        hovertemplate="<b>%{x}</b><br>Risk: %{y:.0%}<extra></extra>",
    ))

    fig.update_layout(
        **_dark_layout(height=350),
        barmode="group",
        xaxis=dict(showgrid=False, tickfont=dict(size=9, color=C_TEXT2)),
        yaxis=dict(
            title="# Supply chains affected",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(size=9, color=C_TEXT2),
            titlefont=dict(size=9, color=C_TEXT2),
        ),
        yaxis2=dict(
            title="Risk score",
            overlaying="y",
            side="right",
            range=[0, 1],
            tickformat=".0%",
            showgrid=False,
            tickfont=dict(size=9, color=C_WARN),
            titlefont=dict(size=9, color=C_WARN),
        ),
        legend=dict(
            orientation="h",
            x=0.01, y=1.08,
            font=dict(size=10, color=C_TEXT2),
            bgcolor="rgba(0,0,0,0)",
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

    # Impact callout cards
    cols = st.columns(min(3, len(details)))
    for idx, det in enumerate(details[:3]):
        col = cols[idx % len(cols)]
        with col:
            pct = det["pct_affected"]
            risk_c = _risk_color(det["risk_score"])
            short_name = det["location_name"].split(",")[0].split("(")[0].strip()
            html = (
                "<div style='background:" + C_CARD + "; border:1px solid "
                + risk_c + "40; border-radius:10px; padding:12px 14px; text-align:center'>"
                "<div style='font-size:0.75rem; font-weight:700; color:"
                + C_TEXT + "; margin-bottom:4px'>" + short_name + "</div>"
                "<div style='font-size:1.4rem; font-weight:800; color:"
                + risk_c + "'>" + str(det["path_count"]) + " chains</div>"
                "<div style='font-size:0.70rem; color:" + C_TEXT2 + "; margin-top:3px'>"
                "If disrupted: <b style='color:" + C_DANGER + "'>"
                + str(pct) + "%</b> of supply chains affected</div>"
                "<div style='font-size:0.68rem; color:" + C_TEXT3 + "; margin-top:2px'>"
                "Risk: " + str(round(det["risk_score"] * 100, 1)) + "%"
                " &nbsp;|&nbsp; " + det["node_type"] + "</div>"
                "</div>"
            )
            st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 4 — Disruption Simulator
# ---------------------------------------------------------------------------

def _render_disruption_simulator(paths: list[SupplyChainPath]) -> None:
    """Interactive disruption simulation with ripple-effect visualisation."""
    col_a, col_b = st.columns([2, 1])

    with col_a:
        path_labels = [p.product_category for p in paths]
        path_ids    = [p.path_id for p in paths]
        selected_label = st.selectbox(
            "Select supply chain path", path_labels, key="vis_sim_path"
        )
        sim_path_idx = path_labels.index(selected_label)
        sim_path = paths[sim_path_idx]

        node_labels = [n.location_name.split(",")[0].split("(")[0].strip() for n in sim_path.nodes]
        node_ids    = [n.node_id for n in sim_path.nodes]
        sel_node_label = st.selectbox(
            "Select node to disrupt", node_labels, key="vis_sim_node"
        )
        sel_node_idx = node_labels.index(sel_node_label)
        sel_node_id  = node_ids[sel_node_idx]

    with col_b:
        duration = st.slider(
            "Disruption duration (days)", min_value=1, max_value=30, value=7,
            key="vis_sim_duration",
        )

    result = simulate_disruption(sim_path.path_id, sel_node_id, duration)

    if "error" in result:
        st.warning("Simulation error: " + result["error"])
        return

    severity_color = {
        "CRITICAL": C_DANGER,
        "HIGH":     C_ORANGE,
        "MODERATE": C_WARN,
    }.get(result["severity"], C_TEXT2)

    # ── Summary metrics ──────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    metrics = [
        (m1, "Severity", result["severity"], severity_color),
        (m2, "Extra Transit Days", "+" + str(result["additional_transit_days"]) + " days", C_DANGER),
        (m3, "Additional Cost", "$" + "{:,.0f}".format(result["additional_cost_usd"]), C_WARN),
        (m4, "New Total Days", str(result["new_total_transit_days"]) + " days", C_ACCENT),
    ]
    for col, label, value, color in metrics:
        with col:
            st.markdown(
                "<div style='background:" + C_CARD + "; border:1px solid "
                + color + "40; border-radius:10px; padding:12px 14px; text-align:center'>"
                "<div style='font-size:0.68rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
                " letter-spacing:0.08em; margin-bottom:4px'>" + label + "</div>"
                "<div style='font-size:1.2rem; font-weight:800; color:" + color + "'>"
                + value + "</div>"
                "</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # ── Ripple effect visualisation ──────────────────────────────────────────
    cascading = result.get("cascading_nodes", [])
    disrupted_node = sim_path.nodes[sel_node_idx]
    all_labels = [n.location_name.split(",")[0].split("(")[0].strip() for n in sim_path.nodes]

    fig = go.Figure()

    # Build timeline positions (x=node index, y=0)
    x_positions = list(range(len(sim_path.nodes)))

    # Ripple rings at disrupted node and downstream
    for i, node in enumerate(sim_path.nodes):
        if i < sel_node_idx:
            color = C_HIGH       # upstream — unaffected
            size  = 16
            opacity = 0.8
        elif i == sel_node_idx:
            color = C_DANGER     # disrupted
            size  = 26
            opacity = 1.0
        else:
            # Ripple intensity decreases downstream
            ripple_scale = max(0.3, 1.0 - (i - sel_node_idx) * 0.20)
            color = severity_color
            size  = int(22 * ripple_scale)
            opacity = ripple_scale * 0.9

        hover = (
            "<b>" + node.location_name + "</b><br>"
            + ("DISRUPTED" if i == sel_node_idx else ("Cascading delay" if i > sel_node_idx else "Unaffected"))
        )

        fig.add_trace(go.Scatter(
            x=[i],
            y=[0],
            mode="markers+text",
            marker=dict(
                size=size,
                color=color,
                opacity=opacity,
                line=dict(color="rgba(255,255,255,0.3)", width=1.5),
            ),
            text=[all_labels[i]],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            hovertemplate=hover + "<extra></extra>",
            showlegend=False,
        ))

    # Connecting line
    fig.add_trace(go.Scatter(
        x=x_positions,
        y=[0] * len(x_positions),
        mode="lines",
        line=dict(color="rgba(255,255,255,0.12)", width=2),
        showlegend=False,
        hoverinfo="skip",
    ))

    # Disruption annotation
    fig.add_annotation(
        x=sel_node_idx,
        y=0,
        text="DISRUPTED",
        showarrow=True,
        arrowhead=2,
        arrowcolor=C_DANGER,
        arrowwidth=2,
        ay=-45,
        font=dict(color=C_DANGER, size=10, family="Inter, sans-serif"),
    )

    fig.update_layout(
        **_dark_layout(height=220),
        xaxis=dict(
            showticklabels=False, showgrid=False, zeroline=False,
            range=[-0.5, len(sim_path.nodes) - 0.5],
        ),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False, range=[-0.5, 0.8]),
        title=dict(
            text="Cascading Impact — " + result["disrupted_node"],
            font=dict(size=11, color=C_TEXT2),
            x=0.01,
        ),
    )

    st.plotly_chart(fig, use_container_width=True)

    # ── Alternative routes and recommendation ────────────────────────────────
    c_left, c_right = st.columns([1, 1])

    with c_left:
        alt_html = (
            "<div style='background:" + C_CARD + "; border:1px solid "
            + C_BORDER + "; border-radius:10px; padding:14px 16px'>"
            "<div style='font-size:0.75rem; font-weight:700; color:"
            + C_TEXT + "; margin-bottom:8px'>Alternative Routes</div>"
        )
        for alt in result["alternative_routes"]:
            alt_html += (
                "<div style='display:flex; align-items:center; margin-bottom:6px'>"
                "<span style='color:" + C_HIGH + "; font-size:0.85rem;"
                " margin-right:6px'>&#8594;</span>"
                "<span style='font-size:0.78rem; color:" + C_TEXT2 + "'>"
                + alt + "</span></div>"
            )
        alt_html += "</div>"
        st.markdown(alt_html, unsafe_allow_html=True)

    with c_right:
        rec_color = severity_color
        rec_html = (
            "<div style='background:rgba(" + (
                "239,68,68" if result["severity"] == "CRITICAL" else
                "249,115,22" if result["severity"] == "HIGH" else
                "245,158,11"
            ) + ",0.08); border:1px solid "
            + rec_color + "30; border-radius:10px; padding:14px 16px'>"
            "<div style='font-size:0.75rem; font-weight:700; color:"
            + C_TEXT + "; margin-bottom:6px'>Recommendation</div>"
            "<div style='font-size:0.78rem; color:" + C_TEXT2
            + "; line-height:1.55'>" + result["recommendation"] + "</div>"
            "</div>"
        )
        st.markdown(rec_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 5 — Resilience Score Cards
# ---------------------------------------------------------------------------

def _render_resilience_cards(paths: list[SupplyChainPath]) -> None:
    """One card per path: resilience score, alternatives, SPOFs, buffer stock."""
    cols = st.columns(len(paths))

    for idx, path in enumerate(paths):
        col = cols[idx]
        with col:
            res_score = path.resilience_score
            res_color = _risk_color(1.0 - res_score)  # low resilience = danger
            res_pct   = int(res_score * 100)

            # Single points of failure
            spofs = [n for n in path.nodes if n.status in ("DISRUPTED", "CLOSED")]
            spof_warnings = [n.location_name.split(",")[0].split("(")[0].strip() for n in spofs]

            # Rough alternative count from resilience score
            alt_count = max(0, int((res_score - 0.5) * 10))

            buffer_days = recommended_buffer_days(path)

            # Card HTML
            short_cat = path.product_category.split("(")[0].strip()
            spof_html = ""
            if spof_warnings:
                for w in spof_warnings:
                    spof_html += (
                        "<div style='display:inline-block; background:"
                        + C_DANGER + "20; border:1px solid " + C_DANGER + "50;"
                        " border-radius:6px; padding:2px 8px; margin:2px;"
                        " font-size:0.65rem; color:" + C_DANGER + "; font-weight:700'>"
                        "&#9888; " + w + "</div>"
                    )
            else:
                spof_html = (
                    "<div style='font-size:0.68rem; color:"
                    + C_HIGH + "'>&#10003; No critical SPOFs</div>"
                )

            html = (
                "<div style='background:" + C_CARD + "; border:1px solid "
                + res_color + "40; border-radius:12px; padding:14px 16px'>"

                # Category name
                "<div style='font-size:0.78rem; font-weight:700; color:"
                + C_TEXT + "; margin-bottom:8px; border-bottom:1px solid "
                + C_BORDER + "; padding-bottom:6px'>" + short_cat + "</div>"

                # Resilience score gauge
                "<div style='text-align:center; margin-bottom:8px'>"
                "<div style='font-size:2rem; font-weight:800; color:"
                + res_color + "'>" + str(res_pct) + "</div>"
                "<div style='font-size:0.65rem; text-transform:uppercase;"
                " letter-spacing:0.08em; color:" + C_TEXT3
                + "; margin-top:-2px'>Resilience Score</div>"
                "</div>"

                # Score bar
                "<div style='margin-bottom:8px'>"
                + _score_bar_html(res_score, width_px=120)
                + "</div>"

                # Metrics row
                "<div style='display:flex; justify-content:space-between;"
                " margin-bottom:8px'>"
                "<div style='text-align:center'>"
                "<div style='font-size:1.0rem; font-weight:700; color:"
                + C_ACCENT + "'>" + str(alt_count) + "</div>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3
                + "'>Alt Routes</div>"
                "</div>"
                "<div style='text-align:center'>"
                "<div style='font-size:1.0rem; font-weight:700; color:"
                + C_WARN + "'>" + str(path.total_transit_days) + "d</div>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3
                + "'>Transit</div>"
                "</div>"
                "<div style='text-align:center'>"
                "<div style='font-size:1.0rem; font-weight:700; color:"
                + C_CYAN + "'>" + str(buffer_days) + "d</div>"
                "<div style='font-size:0.62rem; color:" + C_TEXT3
                + "'>Buffer Stock</div>"
                "</div>"
                "</div>"

                # SPOF warnings
                "<div style='margin-top:4px'>"
                "<div style='font-size:0.65rem; color:" + C_TEXT3
                + "; text-transform:uppercase; letter-spacing:0.06em;"
                " margin-bottom:3px'>SPOF Warnings</div>"
                + spof_html
                + "</div>"
                "</div>"
            )
            st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(port_results, route_results, freight_data) -> None:
    """Render the Supply Chain Visibility tab.

    Parameters
    ----------
    port_results : list[PortDemandResult]
        Current port demand results (passed through from main app).
    route_results : list[RouteOpportunity]
        Current route opportunity objects (passed through from main app).
    freight_data : dict
        Freight rate data dict (passed through from main app).
    """
    logger.info("Rendering Supply Chain Visibility tab")

    paths = EXAMPLE_PATHS

    st.markdown(
        "<div style='padding:14px 0 20px 0; border-bottom:1px solid rgba(255,255,255,0.06);"
        " margin-bottom:20px'>"
        "<div style='font-size:0.65rem; text-transform:uppercase; letter-spacing:0.15em;"
        " color:" + C_TEXT3 + "; margin-bottom:4px'>MODULE</div>"
        "<div style='font-size:1.4rem; font-weight:800; color:" + C_TEXT + "'>"
        "Supply Chain Visibility</div>"
        "<div style='font-size:0.82rem; color:" + C_TEXT2 + "; margin-top:4px'>"
        "End-to-end path tracking for five major product categories — "
        "nodes, transit modes, bottlenecks, disruption simulation, and resilience scoring."
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 + 2 — Path selector + Map + Timeline
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Supply Chain Path Map",
        "Select a product category to visualise the full end-to-end supply chain on a globe. "
        "Node shape indicates facility type; line colour indicates transport mode.",
    )

    path_labels = [p.product_category for p in paths]
    selected_label = st.radio(
        "Product category",
        path_labels,
        horizontal=True,
        key="vis_path_selector",
        label_visibility="collapsed",
    )
    selected_path = next(p for p in paths if p.product_category == selected_label)

    _render_supply_chain_map(selected_path)

    # Legend row
    legend_items = [
        ("square", "#ec4899", "Factory"),
        ("circle", C_ACCENT,  "Port"),
        ("diamond", C_WARN,   "Rail/Warehouse"),
        ("triangle-up", C_HIGH, "Distribution"),
    ]
    legend_html = (
        "<div style='display:flex; gap:18px; flex-wrap:wrap; margin-bottom:14px;"
        " font-size:0.72rem; color:" + C_TEXT2 + "'>"
    )
    for _sym, col, label in legend_items:
        legend_html += (
            "<span><span style='display:inline-block; width:10px; height:10px;"
            " border-radius:2px; background:" + col + "; margin-right:4px'></span>"
            + label + "</span>"
        )
    legend_html += "</div>"
    st.markdown(legend_html, unsafe_allow_html=True)

    # ── Journey Timeline ─────────────────────────────────────────────────────
    _section_title(
        "Journey Timeline",
        "Gantt-style view of the selected path. Colour = risk level for nodes, "
        "transport mode colour for transit segments. Dotted line = approximate current position.",
    )
    _render_journey_timeline(selected_path)

    # Path stat row
    risk_c = _risk_color(selected_path.risk_score)
    stat_html = (
        "<div style='display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px'>"
    )
    stats = [
        ("Total Transit", str(selected_path.total_transit_days) + " days", C_ACCENT),
        ("Est. Cost / 40ft", "$" + "{:,.0f}".format(selected_path.total_cost_usd), C_WARN),
        ("Path Risk Score", str(round(selected_path.risk_score * 100, 1)) + "%", risk_c),
        ("Resilience", str(int(selected_path.resilience_score * 100)) + "%", C_HIGH),
        ("Buffer Rec.", str(recommended_buffer_days(selected_path)) + " days", C_CYAN),
    ]
    for label, value, color in stats:
        stat_html += (
            "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + ";"
            " border-radius:8px; padding:8px 14px; min-width:100px'>"
            "<div style='font-size:0.62rem; color:" + C_TEXT3 + "; text-transform:uppercase;"
            " letter-spacing:0.06em'>" + label + "</div>"
            "<div style='font-size:0.95rem; font-weight:700; color:" + color + "'>"
            + value + "</div>"
            "</div>"
        )
    stat_html += "</div>"
    st.markdown(stat_html, unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — Bottleneck Analyser
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Bottleneck Analyser",
        "Nodes that appear in multiple supply chain paths are systemic single points of failure. "
        "A disruption at any of these nodes cascades across multiple product categories.",
    )
    _render_bottleneck_analyser(paths)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — Disruption Simulator
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Disruption Simulator",
        "Select a supply chain path, choose a node to disrupt, and set the duration. "
        "The simulator shows cascading impacts, alternative routes, and additional cost/days.",
    )
    _render_disruption_simulator(paths)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Resilience Score Cards
    # ══════════════════════════════════════════════════════════════════════════
    _section_title(
        "Resilience Score Cards",
        "Per-path resilience overview: score, alternative route count, SPOF warnings, "
        "and recommended buffer stock days.",
    )
    _render_resilience_cards(paths)
