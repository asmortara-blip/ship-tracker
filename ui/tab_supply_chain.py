"""Supply Chain Health tab — comprehensive SCHI dashboard."""
from __future__ import annotations

import datetime

import plotly.graph_objects as go
import streamlit as st

# ── Local color palette (self-contained, no import from styles) ───────────
C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"
C_TEXT3  = "#64748b"

CATEGORY_COLORS = {
    "PORT_DEMAND":  C_HIGH,
    "ROUTE":        C_ACCENT,
    "MACRO":        "#06b6d4",
    "CONVERGENCE":  "#8b5cf6",
}


# ── Attempt to import the (possibly non-existent) engine module ────────────
try:
    from engine.supply_chain_health import compute_supply_chain_health  # type: ignore
    _SCH_MODULE_AVAILABLE = True
except ImportError:
    _SCH_MODULE_AVAILABLE = False


# ── Helpers ───────────────────────────────────────────────────────────────

def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = (
        f'<div style="color:{C_TEXT2}; font-size:0.83rem; margin-top:3px">{subtitle}</div>'
        if subtitle
        else ""
    )
    st.markdown(
        f"""<div style="margin-bottom:14px; margin-top:4px">
            <div style="font-size:1.05rem; font-weight:700; color:{C_TEXT}">{text}</div>
            {sub_html}
        </div>""",
        unsafe_allow_html=True,
    )


def _card_wrap(content_html: str, border_color: str = C_BORDER) -> str:
    return (
        f'<div style="background:{C_CARD}; border:1px solid {border_color};'
        f' border-radius:12px; padding:18px 20px; margin-bottom:10px">'
        f"{content_html}</div>"
    )


# ── Section 1: Supply Chain Health Index gauge ────────────────────────────

def _render_schi_gauge(schi_value: float, label: str) -> None:
    pct_value = round(schi_value * 100, 1)

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=pct_value,
        delta={"reference": 50, "valueformat": ".1f", "suffix": " pts vs midpoint"},
        number={"suffix": "%", "font": {"size": 64, "color": C_TEXT}},
        title={"text": label, "font": {"size": 15, "color": C_TEXT2}},
        gauge={
            "axis": {
                "range": [0, 100],
                "tickwidth": 1,
                "tickcolor": C_TEXT2,
                "tickfont": {"color": C_TEXT2, "size": 11},
            },
            "bar": {"color": C_ACCENT, "thickness": 0.28},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": [
                {"range": [0,  35], "color": "rgba(239,68,68,0.30)"},
                {"range": [35, 50], "color": "rgba(245,158,11,0.25)"},
                {"range": [50, 70], "color": "rgba(59,130,246,0.22)"},
                {"range": [70, 100], "color": "rgba(16,185,129,0.28)"},
            ],
            "threshold": {
                "line": {"color": C_TEXT, "width": 3},
                "thickness": 0.80,
                "value": pct_value,
            },
        },
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT},
        height=300,
        margin={"l": 40, "r": 40, "t": 40, "b": 10},
    )

    st.plotly_chart(fig, use_container_width=True)


def _render_zone_card(schi_value: float) -> None:
    """Render a dramatic zone-label card that changes color + message by zone."""
    if schi_value >= 0.70:
        bg_color  = "rgba(16,185,129,0.15)"
        bd_color  = "rgba(16,185,129,0.55)"
        txt_color = C_HIGH
        icon      = "✅"
        headline  = "SUPPLY CHAIN HEALTHY"
        detail    = "Conditions are favorable for shipping operations"
    elif schi_value >= 0.50:
        bg_color  = "rgba(59,130,246,0.15)"
        bd_color  = "rgba(59,130,246,0.55)"
        txt_color = C_ACCENT
        icon      = "🔵"
        headline  = "MODERATE / NORMAL"
        detail    = "Standard operating conditions — no major stress signals"
    elif schi_value >= 0.35:
        bg_color  = "rgba(245,158,11,0.15)"
        bd_color  = "rgba(245,158,11,0.55)"
        txt_color = C_WARN
        icon      = "⚠️"
        headline  = "SUPPLY CHAIN STRESS"
        detail    = "Monitor key indicators — elevated concern in multiple dimensions"
    else:
        bg_color  = "rgba(239,68,68,0.18)"
        bd_color  = "rgba(239,68,68,0.65)"
        txt_color = C_DANGER
        icon      = "🚨"
        headline  = "CRITICAL CONDITIONS"
        detail    = "Significant disruption risk — immediate attention recommended"

    st.markdown(
        f'<div style="background:{bg_color}; border:2px solid {bd_color};'
        f' border-radius:14px; padding:20px 22px; margin-bottom:12px">'
        f'<div style="font-size:1.8rem; line-height:1; margin-bottom:8px">{icon}</div>'
        f'<div style="font-size:1.0rem; font-weight:900; color:{txt_color};'
        f' letter-spacing:0.06em; margin-bottom:6px">{headline}</div>'
        f'<div style="font-size:0.83rem; color:{C_TEXT2}; line-height:1.5">{detail}</div>'
        f'<div style="margin-top:10px; font-size:0.78rem; color:{C_TEXT3}">'
        f'SCHI Score: <span style="color:{txt_color}; font-weight:700">{schi_value:.1%}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


# ── Section 2: 6-Dimension Radar ─────────────────────────────────────────

def _render_radar(dimension_scores: dict[str, float]) -> None:
    dims = list(dimension_scores.keys())
    vals = list(dimension_scores.values())
    # Close the polygon
    dims_closed = dims + [dims[0]]
    vals_closed = vals + [vals[0]]

    fig = go.Figure(go.Scatterpolar(
        r=vals_closed,
        theta=dims_closed,
        fill="toself",
        fillcolor="rgba(59,130,246,0.2)",
        line={"color": C_ACCENT, "width": 2},
        hovertemplate="<b>%{theta}</b><br>Score: %{r:.0%}<extra></extra>",
    ))

    fig.update_layout(
        polar={
            "bgcolor": "#111827",
            "radialaxis": {
                "visible": True,
                "range": [0, 1],
                "tickformat": ".0%",
                "tickfont": {"size": 9, "color": C_TEXT2},
                "gridcolor": "rgba(255,255,255,0.08)",
                "linecolor": "rgba(255,255,255,0.08)",
            },
            "angularaxis": {
                "tickfont": {"size": 11, "color": C_TEXT},
                "gridcolor": "rgba(255,255,255,0.08)",
                "linecolor": "rgba(255,255,255,0.10)",
            },
        },
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT},
        height=350,
        margin={"l": 60, "r": 60, "t": 30, "b": 30},
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True)


# ── Section 3: Key Risks & Tailwinds ─────────────────────────────────────

def _render_risks_and_tailwinds(risks: list[str], tailwinds: list[str]) -> None:
    col_risk, col_tail = st.columns(2)

    def _bullet_list(items: list[str], color: str) -> str:
        if not items:
            return f'<li style="color:{C_TEXT2}; font-size:0.85rem">No signals identified</li>'
        return "".join(
            f'<li style="color:{C_TEXT}; font-size:0.85rem; margin-bottom:6px; line-height:1.4">{item}</li>'
            for item in items
        )

    with col_risk:
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_DANGER};'
            f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">Key Risks</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            _card_wrap(
                f'<ul style="padding-left:18px; margin:0">{_bullet_list(risks, C_DANGER)}</ul>',
                border_color="rgba(239,68,68,0.25)",
            ),
            unsafe_allow_html=True,
        )

    with col_tail:
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_HIGH};'
            f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:8px">Key Tailwinds</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            _card_wrap(
                f'<ul style="padding-left:18px; margin:0">{_bullet_list(tailwinds, C_HIGH)}</ul>',
                border_color="rgba(16,185,129,0.25)",
            ),
            unsafe_allow_html=True,
        )


# ── Section 4: Historical context placeholder ─────────────────────────────

def _render_historical_placeholder() -> None:
    st.markdown(
        _card_wrap(
            f'<div style="display:flex; align-items:flex-start; gap:14px">'
            f'<span style="font-size:1.4rem; line-height:1">&#8505;</span>'
            f'<div>'
            f'<div style="font-size:0.88rem; font-weight:600; color:{C_TEXT2}; margin-bottom:4px">'
            f"Historical SCHI Trend"
            f"</div>"
            f'<div style="font-size:0.83rem; color:{C_TEXT3}; line-height:1.5">'
            f"Historical SCHI trend requires 30+ days of operation. "
            f"Check back as the system accumulates data."
            f"</div>"
            f"</div></div>",
            border_color=C_BORDER,
        ),
        unsafe_allow_html=True,
    )


# ── Section 5: Insight bar chart by category ─────────────────────────────

def _render_insight_category_chart(insights: list) -> None:
    cats = ["PORT_DEMAND", "ROUTE", "MACRO", "CONVERGENCE"]
    counts = {c: 0 for c in cats}
    for ins in insights:
        cat = getattr(ins, "category", None)
        if cat in counts:
            counts[cat] += 1

    labels = list(counts.keys())
    values = list(counts.values())
    colors = [CATEGORY_COLORS.get(c, C_TEXT2) for c in labels]

    fig = go.Figure(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color=colors,
        text=[str(v) for v in values],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Count: %{x}<extra></extra>",
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font={"color": C_TEXT},
        height=200,
        xaxis={
            "title": "Insight Count",
            "gridcolor": "rgba(255,255,255,0.06)",
            "color": C_TEXT2,
        },
        yaxis={"color": C_TEXT2},
        margin={"l": 10, "r": 50, "t": 10, "b": 10},
        showlegend=False,
    )

    st.plotly_chart(fig, use_container_width=True)


# ── NEW: Port Network Graph ───────────────────────────────────────────────

def _render_port_network(port_results: list, route_results: list) -> None:
    """Force-directed-style port network graph using geographic lat/lon positions."""
    from ports.port_registry import PORTS_BY_LOCODE  # type: ignore

    traces = []

    # Build a quick lookup: locode -> port_result for demand scores
    demand_by_locode: dict = {}
    for pr in port_results:
        locode = getattr(pr, "locode", None) or getattr(pr, "port_locode", None)
        if locode:
            demand_by_locode[locode] = pr

    # ── Edge traces (one trace per route for individual hover/color) ──────
    for route in route_results:
        origin_locode = getattr(route, "origin_locode", None)
        dest_locode   = getattr(route, "dest_locode", None)
        if not origin_locode or not dest_locode:
            continue

        origin_port = PORTS_BY_LOCODE.get(origin_locode)
        dest_port   = PORTS_BY_LOCODE.get(dest_locode)
        if not origin_port or not dest_port:
            continue

        opp = getattr(route, "opportunity_score", 0.5)
        if opp > 0.65:
            edge_color = "#10b981"
        elif opp > 0.45:
            edge_color = "#f59e0b"
        else:
            edge_color = "#374151"

        edge_width = 1 + opp * 4

        origin_name = getattr(origin_port, "name", origin_locode)
        dest_name   = getattr(dest_port, "name", dest_locode)
        hover_txt   = (
            f"{origin_name} → {dest_name}<br>"
            f"Opportunity: {opp:.0%}"
        )

        traces.append(go.Scatter(
            x=[origin_port.lon, dest_port.lon, None],
            y=[origin_port.lat, dest_port.lat, None],
            mode="lines",
            line=dict(color=edge_color, width=edge_width),
            hoverinfo="text",
            text=hover_txt,
            showlegend=False,
            opacity=0.75,
        ))

    # ── Node traces ───────────────────────────────────────────────────────
    # Collect all ports that appear in either port_results or routes
    seen_locodes: set = set()
    for pr in port_results:
        locode = getattr(pr, "locode", None) or getattr(pr, "port_locode", None)
        if locode:
            seen_locodes.add(locode)
    for route in route_results:
        for attr in ("origin_locode", "dest_locode"):
            lc = getattr(route, attr, None)
            if lc:
                seen_locodes.add(lc)

    node_x, node_y, node_sizes, node_colors, node_text = [], [], [], [], []

    for locode in seen_locodes:
        port = PORTS_BY_LOCODE.get(locode)
        if not port:
            continue

        pr_obj = demand_by_locode.get(locode)
        demand = getattr(pr_obj, "demand_score", 0.5) if pr_obj else 0.5

        if demand >= 0.70:
            node_color = C_DANGER
            level_txt  = "High Demand"
        elif demand >= 0.45:
            node_color = C_WARN
            level_txt  = "Moderate"
        else:
            node_color = C_HIGH
            level_txt  = "Low Demand / Capacity"

        node_size = 8 + demand * 20

        node_x.append(port.lon)
        node_y.append(port.lat)
        node_sizes.append(node_size)
        node_colors.append(node_color)
        node_text.append(
            f"<b>{port.name}</b><br>"
            f"Locode: {locode}<br>"
            f"Region: {port.region}<br>"
            f"Demand: {demand:.0%} ({level_txt})"
        )

    if node_x:
        traces.append(go.Scatter(
            x=node_x,
            y=node_y,
            mode="markers+text",
            marker=dict(
                size=node_sizes,
                color=node_colors,
                line=dict(color="rgba(255,255,255,0.25)", width=1.5),
                opacity=0.92,
            ),
            text=[PORTS_BY_LOCODE[lc].name for lc in seen_locodes if PORTS_BY_LOCODE.get(lc)],
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT2),
            hovertext=node_text,
            hoverinfo="text",
            showlegend=False,
        ))

    if not traces:
        st.markdown(
            _card_wrap(
                f'<div style="text-align:center; color:{C_TEXT2}; font-size:0.88rem; padding:8px 0">'
                f"No route or port data available for network graph."
                f"</div>"
            ),
            unsafe_allow_html=True,
        )
        return

    fig = go.Figure(data=traces)
    fig.update_layout(
        paper_bgcolor="#0a0f1a",
        plot_bgcolor="#0a0f1a",
        height=450,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showgrid=False, zeroline=False, visible=False),
        hovermode="closest",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


# ── NEW: Supply Chain Disruption Timeline ─────────────────────────────────

def _render_disruption_timeline(port_results: list, route_results: list, macro_data: dict) -> None:
    """Horizontal timeline of disruption events over the last 90 days."""
    today = datetime.date.today()
    day30_ago = today - datetime.timedelta(days=30)

    # ── Collect events ────────────────────────────────────────────────────
    # Each event: (date, category_label, y_pos, color, marker_symbol, hover_text)
    events: list[tuple] = []

    # Category Y positions
    CAT_Y = {
        "High Demand Alert": 3,
        "Rate Spike":        2,
        "Macro Shift":       1,
    }
    CAT_COLORS = {
        "High Demand Alert": C_DANGER,
        "Rate Spike":        C_WARN,
        "Macro Shift":       "#06b6d4",
    }

    # High Demand Alerts
    for pr in port_results:
        demand = getattr(pr, "demand_score", 0.0)
        if demand > 0.70:
            name = getattr(pr, "port_name", getattr(pr, "name", "Unknown Port"))
            events.append((
                today,
                "High Demand Alert",
                CAT_Y["High Demand Alert"],
                CAT_COLORS["High Demand Alert"],
                "diamond",
                f"<b>High Demand Alert</b><br>{name}<br>Demand: {demand:.0%}<br>Date: {today}",
            ))

    # Rate Spike events
    for route in route_results:
        rate_chg = getattr(route, "rate_pct_change_30d", 0.0) or 0.0
        if rate_chg > 0.15:
            origin = getattr(route, "origin_locode", "?")
            dest   = getattr(route, "dest_locode",   "?")
            events.append((
                day30_ago,
                "Rate Spike",
                CAT_Y["Rate Spike"],
                CAT_COLORS["Rate Spike"],
                "diamond",
                f"<b>Rate Spike</b><br>{origin} → {dest}<br>"
                f"30d change: +{rate_chg:.0%}<br>Date: {day30_ago}",
            ))

    # Macro Shift event from BDI
    if isinstance(macro_data, dict):
        bdi_series = macro_data.get("bdi") or macro_data.get("BDI")
        if isinstance(bdi_series, list) and len(bdi_series) >= 2:
            current_bdi = bdi_series[-1]
            avg_30d_bdi = sum(bdi_series[-30:]) / len(bdi_series[-30:])
            if avg_30d_bdi > 0 and (current_bdi - avg_30d_bdi) / avg_30d_bdi > 0.10:
                events.append((
                    today,
                    "Macro Shift",
                    CAT_Y["Macro Shift"],
                    CAT_COLORS["Macro Shift"],
                    "diamond",
                    f"<b>Macro Shift — BDI Surge</b><br>"
                    f"Current BDI: {current_bdi:.0f}<br>"
                    f"30d avg: {avg_30d_bdi:.0f}<br>"
                    f"Change: +{(current_bdi - avg_30d_bdi) / avg_30d_bdi:.0%}",
                ))

    # Build one trace per category so legend works cleanly
    cat_traces: dict[str, dict] = {
        cat: {"x": [], "y": [], "text": [], "color": CAT_COLORS[cat]}
        for cat in CAT_Y
    }
    for ev_date, cat, _y, _color, _sym, hover in events:
        cat_traces[cat]["x"].append(str(ev_date))
        cat_traces[cat]["y"].append(cat)
        cat_traces[cat]["text"].append(hover)

    fig = go.Figure()

    # Background band for "last 90 days" reference
    x_start = str(today - datetime.timedelta(days=90))
    x_end   = str(today)

    for cat, data in cat_traces.items():
        if not data["x"]:
            # Still add empty trace so legend shows
            fig.add_trace(go.Scatter(
                x=[], y=[],
                mode="markers",
                name=cat,
                marker=dict(color=data["color"], size=12, symbol="diamond"),
                showlegend=True,
            ))
        else:
            fig.add_trace(go.Scatter(
                x=data["x"],
                y=data["y"],
                mode="markers",
                name=cat,
                marker=dict(
                    color=data["color"],
                    size=14,
                    symbol="diamond",
                    line=dict(color="rgba(255,255,255,0.3)", width=1.5),
                    opacity=0.92,
                ),
                hovertext=data["text"],
                hoverinfo="text",
                showlegend=True,
            ))

    fig.update_layout(
        paper_bgcolor="#0a0f1a",
        plot_bgcolor="#111827",
        height=250,
        margin=dict(l=20, r=20, t=20, b=20),
        font=dict(color=C_TEXT),
        xaxis=dict(
            range=[x_start, x_end],
            gridcolor="rgba(255,255,255,0.06)",
            color=C_TEXT2,
            title="Date (last 90 days)",
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.06)",
            color=C_TEXT2,
            categoryorder="array",
            categoryarray=["Macro Shift", "Rate Spike", "High Demand Alert"],
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
        hovermode="closest",
    )

    if not events:
        fig.add_annotation(
            text="No disruption events detected in current dataset",
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=13, color=C_TEXT3),
        )

    st.plotly_chart(fig, use_container_width=True)


# ── NEW: Risk Score Breakdown Table ──────────────────────────────────────

def _render_risk_breakdown_table(dimension_scores: dict[str, float]) -> None:
    """Styled HTML table showing each SCHI dimension with a mini progress bar."""

    DIM_DESCRIPTIONS = {
        "Port Capacity":     "Port utilization relative to historical throughput averages",
        "Freight Cost":      "Spot rate momentum across major trade corridors",
        "Macro Environment": "Global trade volume, PMI, and currency headwinds",
        "Chokepoint Risk":   "Congestion and geopolitical risk at strategic straits",
        "Inventory Cycle":   "Retail inventory-to-sales ratio and restocking demand",
        "Seasonal Factors":  "Seasonal demand peaks and historic shipping patterns",
    }

    def _status_emoji(score: float) -> str:
        if score >= 0.70:
            return "✅"
        elif score >= 0.50:
            return "🔵"
        elif score >= 0.35:
            return "⚠️"
        return "🔴"

    def _bar_color(score: float) -> str:
        if score >= 0.70:
            return C_HIGH
        elif score >= 0.50:
            return C_ACCENT
        elif score >= 0.35:
            return C_WARN
        return C_DANGER

    # Sort ascending by score (worst first)
    sorted_dims = sorted(dimension_scores.items(), key=lambda kv: kv[1])

    rows_html = ""
    for dim, score in sorted_dims:
        color   = _bar_color(score)
        emoji   = _status_emoji(score)
        desc    = DIM_DESCRIPTIONS.get(dim, "")
        bar_pct = int(score * 100)
        score_pct = f"{bar_pct}%"

        rows_html += (
            "<tr>"
            f'<td style="font-weight:600; color:#f1f5f9; padding:10px 8px; white-space:nowrap">'
            f'{emoji} {dim}</td>'
            f'<td style="width:38%; padding:10px 8px">'
            f'<div style="display:flex; align-items:center; gap:8px">'
            f'<div style="flex:1; background:#374151; border-radius:4px; height:6px">'
            f'<div style="width:{bar_pct}%; background:{color}; border-radius:4px; height:6px"></div>'
            f'</div>'
            f'<span style="font-size:0.78rem; font-weight:700; color:{color}; min-width:32px">'
            f'{score_pct}</span>'
            f'</div></td>'
            f'<td style="color:#94a3b8; font-size:0.80rem; padding:10px 8px; line-height:1.4">'
            f'{desc}</td>'
            "</tr>"
        )

    table_html = (
        f'<table style="width:100%; border-collapse:collapse">'
        f'<thead><tr>'
        f'<th style="color:#64748b; font-size:0.72rem; text-transform:uppercase;'
        f' letter-spacing:0.07em; padding:6px 8px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Dimension</th>'
        f'<th style="color:#64748b; font-size:0.72rem; text-transform:uppercase;'
        f' letter-spacing:0.07em; padding:6px 8px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Score</th>'
        f'<th style="color:#64748b; font-size:0.72rem; text-transform:uppercase;'
        f' letter-spacing:0.07em; padding:6px 8px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.08)">Driver</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table>'
    )

    st.markdown(
        _card_wrap(table_html, border_color="rgba(59,130,246,0.25)"),
        unsafe_allow_html=True,
    )


# ── Main render function ───────────────────────────────────────────────────

def render(
    port_results: list,
    route_results: list,
    freight_data: dict,
    macro_data: dict,
    insights: list,
) -> None:
    """Render the Supply Chain Health tab."""

    st.header("Supply Chain Health")

    # ── Attempt to compute SCHI via engine module ─────────────────────────
    schi_report = None
    if _SCH_MODULE_AVAILABLE:
        try:
            schi_report = compute_supply_chain_health(
                port_results, route_results, freight_data, macro_data
            )
        except Exception:
            schi_report = None

    # ── Derive SCHI value and dimension scores ────────────────────────────
    if schi_report is not None:
        schi_value = getattr(schi_report, "index", None)
        if schi_value is None:
            schi_value = getattr(schi_report, "score", 0.5)
        schi_label = "Supply Chain Health Index"
        raw_dims = getattr(schi_report, "dimension_scores", None)
    else:
        # Fallback: simple average of port demand scores
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        if has_data:
            schi_value = sum(r.demand_score for r in has_data) / len(has_data)
        else:
            schi_value = 0.5
        schi_label = "Supply Chain Index (est.)"
        raw_dims = None

    # ── Dimension scores (6 axes) ─────────────────────────────────────────
    if raw_dims and isinstance(raw_dims, dict) and len(raw_dims) >= 6:
        dimension_scores = {
            "Port Capacity":     raw_dims.get("port_capacity", 0.5),
            "Freight Cost":      raw_dims.get("freight_cost", 0.5),
            "Macro Environment": raw_dims.get("macro", 0.5),
            "Chokepoint Risk":   raw_dims.get("chokepoint", 0.5),
            "Inventory Cycle":   raw_dims.get("inventory", 0.5),
            "Seasonal Factors":  raw_dims.get("seasonal", 0.5),
        }
    else:
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        avg_demand = (
            sum(r.demand_score for r in has_data) / len(has_data) if has_data else 0.5
        )
        dimension_scores = {
            "Port Capacity":     1.0 - avg_demand,
            "Freight Cost":      0.5,
            "Macro Environment": 0.5,
            "Chokepoint Risk":   0.5,
            "Inventory Cycle":   0.5,
            "Seasonal Factors":  0.5,
        }

    # ── Derive risks & tailwinds ──────────────────────────────────────────
    if schi_report is not None:
        risks     = getattr(schi_report, "risks",     [])
        tailwinds = getattr(schi_report, "tailwinds", [])
    else:
        has_data = [r for r in port_results if getattr(r, "has_real_data", False)]
        high_demand = [r for r in has_data if r.demand_score >= 0.70]
        low_demand  = [r for r in has_data if r.demand_score <= 0.35]

        risks = [
            f"{r.port_name} — elevated congestion risk ({r.demand_score:.0%} demand)"
            for r in sorted(high_demand, key=lambda x: x.demand_score, reverse=True)[:4]
        ]
        if not risks:
            risks = ["No high-demand congestion hotspots identified at this time"]

        tailwinds = [
            f"{r.port_name} — available capacity ({r.demand_score:.0%} demand)"
            for r in sorted(low_demand, key=lambda x: x.demand_score)[:4]
        ]
        if not tailwinds:
            tailwinds = ["No low-demand capacity windows identified at this time"]

    # ══════════════════════════════════════════════════════════════════════
    # Section 1 — SCHI Gauge  (enhanced)
    # ══════════════════════════════════════════════════════════════════════
    _section_title(
        "Supply Chain Health Index (SCHI)",
        "Composite score across port, freight, macro, and geopolitical dimensions",
    )

    col_gauge, col_zone = st.columns([3, 2])

    with col_gauge:
        _render_schi_gauge(schi_value, schi_label)

    with col_zone:
        # Zone legend
        zones = [
            ("0 – 35%",  "Critical Stress",  C_DANGER),
            ("35 – 50%", "Elevated Concern",  C_WARN),
            ("50 – 70%", "Moderate / Normal", C_ACCENT),
            ("70 – 100%","Healthy Supply Chain", C_HIGH),
        ]
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3};'
            f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:10px; margin-top:16px">'
            f"Score Zones</div>",
            unsafe_allow_html=True,
        )
        for rng, name, color in zones:
            st.markdown(
                f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:7px">'
                f'<div style="width:12px; height:12px; border-radius:3px; background:{color};'
                f' flex-shrink:0"></div>'
                f'<div>'
                f'<span style="font-size:0.8rem; font-weight:600; color:{color}">{rng}</span>'
                f'<span style="font-size:0.78rem; color:{C_TEXT2}"> — {name}</span>'
                f"</div></div>",
                unsafe_allow_html=True,
            )

        # Dramatic zone card
        _render_zone_card(schi_value)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # Section 2 — Radar chart + Risk Score Breakdown Table
    # ══════════════════════════════════════════════════════════════════════
    _section_title(
        "6-Dimension Breakdown",
        "Normalized sub-scores [0 = stress / low capacity, 1 = healthy / high capacity]",
    )

    col_radar, col_dim_table = st.columns([3, 2])

    with col_radar:
        _render_radar(dimension_scores)

    with col_dim_table:
        st.markdown(
            f'<div style="font-size:0.72rem; font-weight:700; color:{C_TEXT3};'
            f' text-transform:uppercase; letter-spacing:0.07em; margin-bottom:10px; margin-top:20px">'
            f"Dimension Scores</div>",
            unsafe_allow_html=True,
        )
        for dim, score in dimension_scores.items():
            if score >= 0.70:
                bar_color = C_HIGH
            elif score >= 0.50:
                bar_color = C_ACCENT
            elif score >= 0.35:
                bar_color = C_WARN
            else:
                bar_color = C_DANGER

            bar_pct = int(score * 100)
            st.markdown(
                f'<div style="margin-bottom:10px">'
                f'<div style="display:flex; justify-content:space-between;'
                f' margin-bottom:3px">'
                f'<span style="font-size:0.82rem; color:{C_TEXT}">{dim}</span>'
                f'<span style="font-size:0.82rem; font-weight:700; color:{bar_color}">'
                f"{score:.0%}</span></div>"
                f'<div style="background:rgba(255,255,255,0.06); border-radius:4px;'
                f' height:6px; overflow:hidden">'
                f'<div style="width:{bar_pct}%; height:100%; background:{bar_color};'
                f' border-radius:4px"></div>'
                f"</div></div>",
                unsafe_allow_html=True,
            )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # Section 2b — Risk Score Breakdown Table (detailed, sorted worst-first)
    # ══════════════════════════════════════════════════════════════════════
    _section_title(
        "Risk Score Breakdown",
        "All 6 SCHI dimensions ranked by severity — worst conditions first",
    )
    _render_risk_breakdown_table(dimension_scores)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # Section 3 — Port Network Graph
    # ══════════════════════════════════════════════════════════════════════
    _section_title(
        "Global Port Network",
        "Routes colored by opportunity score — nodes sized by demand pressure",
    )
    _render_port_network(port_results, route_results)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # Section 4 — Disruption Timeline
    # ══════════════════════════════════════════════════════════════════════
    _section_title(
        "Supply Chain Disruption Timeline",
        "Key events detected over the last 90 days",
    )
    _render_disruption_timeline(port_results, route_results, macro_data)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # Section 5 — Risks & Tailwinds
    # ══════════════════════════════════════════════════════════════════════
    _section_title(
        "Key Risks & Tailwinds",
        "Derived from live port demand analysis and engine signals",
    )
    _render_risks_and_tailwinds(risks, tailwinds)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # Section 6 — Historical context
    # ══════════════════════════════════════════════════════════════════════
    _section_title("Historical Context")
    _render_historical_placeholder()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # Section 7 — Insights by category
    # ══════════════════════════════════════════════════════════════════════
    _section_title(
        "Insight Summary by Category",
        "Count of active signals generated by the analysis engine",
    )

    if insights:
        _render_insight_category_chart(insights)
    else:
        st.markdown(
            _card_wrap(
                f'<div style="text-align:center; color:{C_TEXT2}; font-size:0.88rem; padding:8px 0">'
                f"No insights generated yet — run analysis with active API credentials."
                f"</div>"
            ),
            unsafe_allow_html=True,
        )
