from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ports.port_registry import PORTS, PORTS_BY_LOCODE
from ports.demand_analyzer import PortDemandResult
from routes.route_registry import ROUTES
from routes.optimizer import RouteOpportunity
from engine.insight import Insight
from utils.helpers import format_usd

# ── Color palette ─────────────────────────────────────────────────────────────
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _arc_points(lat1: float, lon1: float, lat2: float, lon2: float, n: int = 20):
    """Generate n intermediate points along a geodesic arc (linear interpolation)."""
    lats = [lat1 + (lat2 - lat1) * i / (n - 1) for i in range(n)]
    lons = [lon1 + (lon2 - lon1) * i / (n - 1) for i in range(n)]
    return lats, lons


def _score_to_color(score: float) -> str:
    """Map a 0-1 score to green/amber/dim."""
    if score > 0.65:
        return C_HIGH
    if score > 0.45:
        return C_WARN
    return C_DANGER


def _demand_colorscale_css(score: float) -> str:
    """Map demand score to a CSS hex color via simple linear interpolation."""
    stops = [
        (0.00, (30,  58,  95)),
        (0.25, (59, 130, 246)),
        (0.50, (16, 185, 129)),
        (0.75, (245,158, 11)),
        (1.00, (239, 68,  68)),
    ]
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t0 <= score <= t1:
            pct = (score - t0) / (t1 - t0)
            r = int(c0[0] + (c1[0] - c0[0]) * pct)
            g = int(c0[1] + (c1[1] - c0[1]) * pct)
            b = int(c0[2] + (c1[2] - c0[2]) * pct)
            return f"#{r:02x}{g:02x}{b:02x}"
    return C_HIGH


# ── Hero banner ───────────────────────────────────────────────────────────────

def _render_hero_banner(
    port_scores: dict[str, PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> None:
    """Full-width dramatic animated hero stat banner."""
    total_ports = len(port_scores)
    total_routes = len(route_results)
    total_insights = len(insights)
    top_score = max((i.score for i in insights), default=0.0)
    has_data = [r for r in port_scores.values() if r.has_real_data]
    avg_demand = sum(r.demand_score for r in has_data) / len(has_data) if has_data else 0.0

    stats = [
        ("PORTS ANALYZED",   str(total_ports),          C_ACCENT),
        ("ROUTES MONITORED", str(total_routes),          C_HIGH),
        ("ACTIVE SIGNALS",   str(total_insights),        C_WARN),
        ("TOP SIGNAL",       f"{top_score:.0%}",         C_HIGH if top_score > 0.65 else C_WARN),
        ("AVG GLOBAL DEMAND",f"{avg_demand:.0%}",        C_HIGH if avg_demand > 0.55 else C_WARN),
    ]

    cols_html = ""
    for label, value, color in stats:
        cols_html += f"""
        <div style="flex:1; min-width:110px; text-align:center; padding:0 12px;
                    border-right:1px solid rgba(255,255,255,0.06)">
            <div style="font-size:2rem; font-weight:800; color:{color};
                        letter-spacing:-0.02em; line-height:1.1;
                        text-shadow:0 0 20px {color}55">{value}</div>
            <div style="font-size:0.62rem; font-weight:700; color:{C_TEXT3};
                        text-transform:uppercase; letter-spacing:0.1em;
                        margin-top:5px">{label}</div>
        </div>"""

    live_dot = (
        '<span style="display:inline-block; width:8px; height:8px; border-radius:50%;'
        f' background:{C_HIGH}; margin-right:6px; box-shadow:0 0 8px {C_HIGH};'
        ' animation:pulse 2s infinite"></span>'
    )

    st.markdown(f"""
    <style>
    @keyframes pulse {{
        0%,100% {{ opacity:1; transform:scale(1); }}
        50%      {{ opacity:0.5; transform:scale(1.35); }}
    }}
    @keyframes slideIn {{
        from {{ opacity:0; transform:translateY(-8px); }}
        to   {{ opacity:1; transform:translateY(0); }}
    }}
    .hero-banner {{ animation: slideIn 0.5s ease-out; }}
    </style>

    <div class="hero-banner" style="
        background: linear-gradient(135deg, {C_BG} 0%, {C_CARD} 50%, #0f1d35 100%);
        border: 1px solid rgba(59,130,246,0.3);
        border-radius: 16px;
        padding: 28px 32px;
        margin-bottom: 24px;
        box-shadow: 0 0 40px rgba(59,130,246,0.1), inset 0 1px 0 rgba(255,255,255,0.05)">

        <div style="display:flex; align-items:center; margin-bottom:20px;
                    justify-content:space-between; flex-wrap:wrap; gap:8px">
            <div>
                <div style="font-size:1.35rem; font-weight:800; color:{C_TEXT};
                            letter-spacing:-0.01em">
                    Global Cargo Intelligence
                </div>
                <div style="font-size:0.78rem; color:{C_TEXT2}; margin-top:3px">
                    Real-time port demand, route signals &amp; market intelligence
                </div>
            </div>
            <div style="font-size:0.75rem; font-weight:600; color:{C_HIGH};
                        background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.25);
                        padding:5px 14px; border-radius:999px">
                {live_dot}LIVE FEED ACTIVE
            </div>
        </div>

        <div style="display:flex; gap:0; flex-wrap:wrap">
            {cols_html}
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── 3D Globe ──────────────────────────────────────────────────────────────────

def _build_globe(
    port_scores: dict[str, PortDemandResult],
    route_results: list[RouteOpportunity],
) -> go.Figure:
    """Build 3D orthographic globe with glowing arcs and pulsing port markers."""
    fig = go.Figure()

    DEMAND_COLORSCALE = [
        [0.00, "#1e3a5f"],
        [0.25, "#3b82f6"],
        [0.50, "#10b981"],
        [0.75, "#f59e0b"],
        [1.00, "#ef4444"],
    ]

    # ── Shipping lane arcs ─────────────────────────────────────────────────
    top_routes = sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)[:12]

    for route in top_routes:
        origin = PORTS_BY_LOCODE.get(route.origin_locode)
        dest   = PORTS_BY_LOCODE.get(route.dest_locode)
        if not origin or not dest:
            continue

        lats, lons = _arc_points(origin.lat, origin.lon, dest.lat, dest.lon, n=20)
        score = route.opportunity_score

        if score > 0.65:
            arc_color = "rgba(16,185,129,0.70)"
        elif score > 0.45:
            arc_color = "rgba(245,158,11,0.55)"
        else:
            arc_color = "rgba(55,65,81,0.45)"

        arc_width = 1.5 + score * 2

        rationale_short = route.rationale[:80] + "..." if len(route.rationale) > 80 else route.rationale
        hover_text = (
            f"{route.route_name}"
            f"<br>Score: {score:.0%}"
            f"<br>{rationale_short}"
        )

        fig.add_trace(go.Scattergeo(
            lat=lats,
            lon=lons,
            mode="lines",
            line=dict(width=arc_width, color=arc_color),
            hoverinfo="text",
            hovertext=hover_text,
            showlegend=False,
            name=route.route_name,
        ))

    # ── Port markers — glow layer (larger, transparent) ───────────────────
    glow_lats, glow_lons, glow_sizes, glow_colors, glow_texts = [], [], [], [], []
    main_lats, main_lons, main_sizes, main_colors, main_texts = [], [], [], [], []

    for port in PORTS:
        result = port_scores.get(port.locode)
        demand = result.demand_score if (result and result.has_real_data) else 0.35

        size_main = 8 + demand * 12
        size_glow = size_main * 1.8
        color = _demand_colorscale_css(demand)

        if result and result.has_real_data:
            top_prod = result.top_products[0]["category"] if result.top_products else "—"
            teu_str  = f"{result.throughput_teu_m:.1f}M TEU/yr" if result.throughput_teu_m > 0 else ""
            hover = (
                f"<b>{port.name}</b> ({port.locode})"
                f"<br>Region: {port.region}"
                f"<br>Demand: {result.demand_label} ({demand:.0%})"
                f"<br>Top product: {top_prod}"
                f"<br>Vessels: {result.vessel_count}"
                + (f"<br>{teu_str}" if teu_str else "")
            )
        else:
            hover = f"<b>{port.name}</b> ({port.locode})<br>No live data yet"

        glow_lats.append(port.lat);  glow_lons.append(port.lon)
        glow_sizes.append(size_glow); glow_colors.append(color)
        glow_texts.append(hover)

        main_lats.append(port.lat);  main_lons.append(port.lon)
        main_sizes.append(size_main); main_colors.append(color)
        main_texts.append(hover)

    # Glow layer
    fig.add_trace(go.Scattergeo(
        lat=glow_lats,
        lon=glow_lons,
        mode="markers",
        marker=dict(
            size=glow_sizes,
            color=glow_colors,
            opacity=0.22,
            line=dict(width=0),
        ),
        hoverinfo="skip",
        showlegend=False,
        name="port_glow",
    ))

    # Main marker layer with colorscale bar
    fig.add_trace(go.Scattergeo(
        lat=main_lats,
        lon=main_lons,
        mode="markers",
        marker=dict(
            size=main_sizes,
            color=[_demand_score_for(p, port_scores) for p in PORTS],
            colorscale=DEMAND_COLORSCALE,
            cmin=0,
            cmax=1,
            opacity=0.92,
            line=dict(color="rgba(255,255,255,0.6)", width=0.8),
            colorbar=dict(
                title=dict(text="Demand", font=dict(color=C_TEXT2, size=11)),
                thickness=10,
                len=0.5,
                x=1.01,
                tickfont=dict(color=C_TEXT2, size=10),
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(255,255,255,0.1)",
            ),
        ),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=main_texts,
        showlegend=False,
        name="ports",
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        height=500,
        margin=dict(l=0, r=0, t=0, b=0),
        geo=dict(
            projection_type="orthographic",
            showland=True,     landcolor="#1a2235",
            showocean=True,    oceancolor="#0a0f1a",
            showcoastlines=True, coastlinecolor="rgba(255,255,255,0.15)",
            showframe=False,
            bgcolor="#0a0f1a",
            showcountries=True,
            countrycolor="rgba(255,255,255,0.07)",
            showlakes=False,
            projection_rotation=dict(lon=60, lat=10, roll=0),
        ),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    return fig


def _demand_score_for(port, port_scores: dict[str, PortDemandResult]) -> float:
    result = port_scores.get(port.locode)
    return result.demand_score if (result and result.has_real_data) else 0.35


# ── Signal Feed ───────────────────────────────────────────────────────────────

def _render_signal_feed(insights: list[Insight]) -> None:
    """Scrollable live signal feed showing top 8 insights."""
    import time

    now_ts = time.time()

    st.markdown(f"""
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px">
        <div style="font-size:1rem; font-weight:700; color:{C_TEXT}">Signal Feed</div>
        <div style="font-size:0.72rem; font-weight:700; color:{C_HIGH};
                    background:rgba(16,185,129,0.12); border:1px solid rgba(16,185,129,0.3);
                    padding:2px 10px; border-radius:999px; display:flex; align-items:center; gap:5px">
            <span style="display:inline-block; width:7px; height:7px; border-radius:50%;
                         background:{C_HIGH}; animation:pulse 1.5s infinite"></span>
            LIVE
        </div>
        <div style="font-size:0.72rem; color:{C_TEXT3}; margin-left:auto">
            {len(insights)} active signals
        </div>
    </div>
    """, unsafe_allow_html=True)

    if not insights:
        st.markdown(
            f'<div style="color:{C_TEXT2}; font-size:0.85rem; padding:16px;">No signals yet.</div>',
            unsafe_allow_html=True,
        )
        return

    CAT_COLORS = {
        "CONVERGENCE": "#8b5cf6",
        "ROUTE":       C_ACCENT,
        "PORT_DEMAND": C_HIGH,
        "MACRO":       "#06b6d4",
    }
    CAT_ICONS = {
        "CONVERGENCE": "&#9889;",
        "ROUTE":       "&#128674;",
        "PORT_DEMAND": "&#128679;",
        "MACRO":       "&#128200;",
    }

    items_html = ""
    for idx, ins in enumerate(insights[:8]):
        color = CAT_COLORS.get(ins.category, C_ACCENT)
        icon  = CAT_ICONS.get(ins.category, "&#128161;")
        score_color = C_HIGH if ins.score > 0.65 else C_WARN if ins.score > 0.45 else C_DANGER
        row_bg = "rgba(255,255,255,0.018)" if idx % 2 == 0 else "transparent"

        action_bg_map = {
            "Prioritize": "rgba(16,185,129,0.12)",
            "Monitor":    "rgba(59,130,246,0.12)",
            "Watch":      "rgba(148,163,184,0.10)",
            "Caution":    "rgba(245,158,11,0.12)",
            "Avoid":      "rgba(239,68,68,0.12)",
        }
        action_bg = action_bg_map.get(ins.action, "rgba(255,255,255,0.06)")

        title_short = ins.title[:72] + ("..." if len(ins.title) > 72 else "")

        items_html += f"""
        <div style="display:flex; align-items:flex-start; gap:10px;
                    padding:10px 14px; border-left:3px solid {color};
                    background:{row_bg}; border-bottom:1px solid rgba(255,255,255,0.04)">
            <div style="font-size:1.1rem; line-height:1; margin-top:1px">{icon}</div>
            <div style="flex:1; min-width:0">
                <div style="font-size:0.84rem; font-weight:600; color:{C_TEXT};
                            line-height:1.35; margin-bottom:4px">{title_short}</div>
                <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
                    <span style="background:{action_bg}; color:{color};
                                 padding:1px 8px; border-radius:999px;
                                 font-size:0.68rem; font-weight:700;
                                 text-transform:uppercase; letter-spacing:0.05em">{ins.action}</span>
                    <span style="font-size:0.7rem; color:{C_TEXT3}">{ins.category}</span>
                    <span style="font-size:0.75rem; font-weight:700; color:{score_color};
                                 margin-left:auto">{ins.score:.0%}</span>
                </div>
            </div>
        </div>"""

    st.markdown(f"""
    <div style="background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:12px;
                overflow:hidden; height:240px; overflow-y:auto;
                box-shadow:inset 0 -20px 30px rgba(10,15,26,0.5)">
        {items_html}
    </div>
    """, unsafe_allow_html=True)


# ── Region chart (enhanced) ────────────────────────────────────────────────────

def _render_region_chart(port_scores: dict[str, PortDemandResult]) -> None:
    """Dramatic horizontal bar chart showing demand by region."""
    from ports.port_registry import PORTS

    region_data: dict[str, list[float]] = {}
    for port in PORTS:
        result = port_scores.get(port.locode)
        if result and result.has_real_data:
            region_data.setdefault(port.region, []).append(result.demand_score)

    if not region_data:
        st.info("Demand data loading — check API credentials.")
        return

    regions = sorted(region_data.keys(), key=lambda r: sum(region_data[r]) / len(region_data[r]))
    avg_scores  = [sum(region_data[r]) / len(region_data[r]) for r in regions]
    port_counts = [len(region_data[r]) for r in regions]

    bar_colors = [_demand_colorscale_css(s) for s in avg_scores]

    fig = go.Figure(go.Bar(
        x=avg_scores,
        y=regions,
        orientation="h",
        marker=dict(
            color=avg_scores,
            colorscale=[
                [0.00, "#1e3a5f"],
                [0.25, "#3b82f6"],
                [0.50, "#10b981"],
                [0.75, "#f59e0b"],
                [1.00, "#ef4444"],
            ],
            cmin=0,
            cmax=1,
            line=dict(color="rgba(255,255,255,0.15)", width=0.8),
        ),
        text=[
            f"  {s:.0%}  ({n} port{'s' if n > 1 else ''})"
            for s, n in zip(avg_scores, port_counts)
        ],
        textposition="outside",
        textfont=dict(color=C_TEXT2, size=11),
        hovertemplate="<b>%{y}</b><br>Avg demand: %{x:.0%}<extra></extra>",
    ))

    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=320,
        font=dict(color=C_TEXT, size=12),
        xaxis=dict(
            title="Avg Demand Score",
            range=[0, 1.35],
            gridcolor="rgba(255,255,255,0.05)",
            zerolinecolor="rgba(255,255,255,0.1)",
            tickformat=".0%",
            tickfont=dict(color=C_TEXT3, size=10),
        ),
        yaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            tickfont=dict(color=C_TEXT2, size=11),
        ),
        margin=dict(t=10, b=10, l=130, r=80),
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="region_demand_chart")


# ── Top routes (enhanced gradient cards) ─────────────────────────────────────

def _render_top_routes(route_results: list[RouteOpportunity]) -> None:
    if not route_results:
        st.info("Route data loading...")
        return

    for route in route_results[:5]:
        score = route.opportunity_score
        label_color = (
            C_HIGH   if route.opportunity_label == "Strong"   else
            C_WARN   if route.opportunity_label == "Moderate" else
            C_DANGER
        )

        rate_str = f"${route.current_rate_usd_feu:,.0f}/FEU" if route.current_rate_usd_feu > 0 else "—"
        pct_str  = f"{route.rate_pct_change_30d * 100:+.1f}%" if route.current_rate_usd_feu > 0 else ""
        pct_color = (
            C_HIGH   if route.rate_pct_change_30d > 0 else
            C_DANGER if route.rate_pct_change_30d < -0.01 else
            C_TEXT2
        )
        score_pct = int(score * 100)

        # gradient border trick: outer wrapper with gradient bg, inner card
        glow_color = label_color
        pct_html = (
            f'<span style="font-size:0.78rem; color:{pct_color}; font-weight:600">{pct_str}</span>'
            if pct_str else ""
        )

        st.markdown(f"""
        <div style="background:linear-gradient(135deg, {glow_color}33 0%, transparent 60%);
                    border:1px solid {glow_color}55; border-radius:12px;
                    padding:13px 16px; margin-bottom:8px;
                    box-shadow:0 0 16px {glow_color}18;
                    position:relative; overflow:hidden">
            <div style="position:absolute; top:0; left:0; width:4px; height:100%;
                        background:{glow_color}; border-radius:12px 0 0 12px"></div>
            <div style="padding-left:8px">
                <div style="display:flex; justify-content:space-between; align-items:flex-start">
                    <div style="font-size:0.88rem; font-weight:600; color:{C_TEXT};
                                line-height:1.3">{route.route_name}</div>
                    <span style="background:{glow_color}22; color:{glow_color};
                                 border:1px solid {glow_color}44;
                                 padding:2px 10px; border-radius:999px;
                                 font-size:0.72rem; font-weight:800;
                                 white-space:nowrap; margin-left:8px">{score_pct}%</span>
                </div>
                <div style="display:flex; gap:12px; margin-top:6px; align-items:center; flex-wrap:wrap">
                    <span style="font-size:0.78rem; color:{C_TEXT2}">{rate_str}</span>
                    {pct_html}
                    <span style="font-size:0.75rem; color:{C_TEXT3}">{route.transit_days}d transit</span>
                    <span style="font-size:0.75rem; color:{C_TEXT3}">{route.origin_locode} &#8594; {route.dest_locode}</span>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)


# ── Trade flow Sankey ─────────────────────────────────────────────────────────

def _render_trade_flow_sankey(
    trade_data: dict | None,
    route_results: list,
) -> None:
    """Render a Sankey diagram of trade flows between regions."""
    from ports.port_registry import PORTS_BY_LOCODE

    REGION_COLORS: dict[str, str] = {
        "Asia East":           "#3b82f6",
        "Europe":              "#10b981",
        "North America West":  "#f59e0b",
        "North America East":  "#f59e0b",
        "Southeast Asia":      "#8b5cf6",
        "Middle East":         "#ef4444",
        "South America":       "#06b6d4",
        "Africa":              "#ec4899",
        "South Asia":          "#84cc16",
    }

    FALLBACK_FLOWS = [
        ("Asia East",    "Europe",               140),
        ("Asia East",    "North America West",   180),
        ("Asia East",    "Southeast Asia",        80),
        ("Europe",       "North America East",    60),
        ("Southeast Asia", "North America West",  40),
        ("Middle East",  "Europe",                35),
        ("South America", "Europe",               25),
    ]

    use_real_data = bool(trade_data)
    flows: dict[tuple[str, str], float] = {}

    if use_real_data:
        for locode, df in trade_data.items():
            if df is None or df.empty:
                continue
            port = PORTS_BY_LOCODE.get(locode)
            if port is None:
                continue
            port_region = port.region
            if "flow" not in df.columns or "value_usd" not in df.columns:
                continue
            for _, row in df.iterrows():
                flow_dir = str(row.get("flow", "")).strip()
                val = float(row.get("value_usd", 0) or 0)
                if val <= 0:
                    continue
                if flow_dir == "Export":
                    src, dst = port_region, "Unknown"
                elif flow_dir == "Import":
                    src, dst = "Unknown", port_region
                else:
                    continue
                key = (src, dst)
                flows[key] = flows.get(key, 0.0) + val / 1e9

        flows = {k: v for k, v in flows.items() if "Unknown" not in k}

    if not flows:
        use_real_data = False
        for src, dst, val in FALLBACK_FLOWS:
            flows[(src, dst)] = float(val)

    all_regions: list[str] = []
    for src, dst in flows:
        if src not in all_regions:
            all_regions.append(src)
        if dst not in all_regions:
            all_regions.append(dst)

    region_idx  = {r: i for i, r in enumerate(all_regions)}
    node_colors = [REGION_COLORS.get(r, "#94a3b8") for r in all_regions]
    sources     = [region_idx[src] for src, dst in flows]
    targets     = [region_idx[dst] for src, dst in flows]
    values      = list(flows.values())

    if use_real_data:
        link_colors = []
        for src, _ in flows:
            hex_c = REGION_COLORS.get(src, "#94a3b8").lstrip("#")
            r_val = int(hex_c[0:2], 16)
            g_val = int(hex_c[2:4], 16)
            b_val = int(hex_c[4:6], 16)
            link_colors.append(f"rgba({r_val},{g_val},{b_val},0.25)")
    else:
        link_colors = ["rgba(255,255,255,0.15)"] * len(sources)

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=20,
            thickness=18,
            line=dict(color="rgba(255,255,255,0.1)", width=0.5),
            label=all_regions,
            color=node_colors,
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_colors,
        ),
    ))

    subtitle = "Estimated volumes (illustrative)" if not use_real_data else "USD billions"
    fig.update_layout(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        font=dict(color=C_TEXT, size=12),
        height=380,
        margin=dict(l=10, r=10, t=30, b=10),
        title=dict(
            text=subtitle,
            font=dict(size=11, color=C_TEXT2),
            x=0.01,
            y=0.98,
        ),
        showlegend=False,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )
    st.plotly_chart(fig, use_container_width=True, key="trade_flow_sankey")


# ── Chokepoint section ────────────────────────────────────────────────────────

def _render_chokepoint_section() -> None:
    """Render chokepoint risk map and alerts."""
    from processing.risk_monitor import CHOKEPOINTS, get_color, get_high_risk_alerts, RISK_LEVELS

    alerts = get_high_risk_alerts()
    if alerts:
        for alert in alerts:
            color = get_color(alert.risk_level)
            reroute_html = (
                f'<span style="background:rgba(239,68,68,0.1); color:{C_DANGER};'
                f' padding:2px 10px; border-radius:999px; font-size:0.72rem">'
                f'+{alert.reroute_impact_days}d reroute</span>'
                if alert.reroute_impact_days else ""
            )
            st.markdown(f"""
            <div style="background:{C_CARD}; border:1px solid {C_BORDER};
                        border-left:4px solid {color};
                        border-radius:10px; padding:14px 18px; margin-bottom:8px">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px">
                    <div style="font-size:0.95rem; font-weight:700; color:{C_TEXT}">
                        &#9888;&nbsp;{alert.name}
                    </div>
                    <div style="display:flex; gap:8px">
                        <span style="background:rgba(255,255,255,0.06); color:{color};
                            padding:2px 10px; border-radius:999px; font-size:0.72rem; font-weight:700">
                            {alert.risk_level}</span>
                        <span style="background:rgba(255,255,255,0.06); color:{C_TEXT2};
                            padding:2px 10px; border-radius:999px; font-size:0.72rem">
                            {alert.pct_world_trade:.0f}% of trade</span>
                        {reroute_html}
                    </div>
                </div>
                <div style="font-size:0.83rem; color:{C_TEXT2}; line-height:1.5">{alert.risk_summary}</div>
            </div>
            """, unsafe_allow_html=True)

    col_map, col_table = st.columns([2, 1])

    with col_map:
        fig = go.Figure()

        for cp in CHOKEPOINTS:
            color = get_color(cp.risk_level)
            size  = 8 + cp.pct_world_trade * 0.8

            fig.add_trace(go.Scattergeo(
                lat=[cp.lat],
                lon=[cp.lon],
                mode="markers+text",
                marker=dict(
                    size=max(10, size),
                    color=color,
                    symbol="diamond",
                    line=dict(color="white", width=1),
                    opacity=0.9,
                ),
                text=[cp.name.split(" ")[0]],
                textposition="top center",
                textfont=dict(size=9, color="white"),
                hovertemplate=(
                    f"<b>{cp.name}</b><br>"
                    f"Risk: {cp.risk_level}<br>"
                    f"World trade: {cp.pct_world_trade:.0f}%<br>"
                    f"{cp.risk_summary[:100]}...<extra></extra>"
                ),
                showlegend=False,
                name=cp.name,
            ))

        fig.update_layout(
            template="plotly_dark",
            height=320,
            geo=dict(
                projection_type="natural earth",
                showland=True,    landcolor="rgb(35,40,50)",
                showocean=True,   oceancolor="rgb(15,25,40)",
                showcountries=True, countrycolor="rgba(255,255,255,0.15)",
                bgcolor="rgb(10,15,25)",
            ),
            paper_bgcolor=C_BG,
            margin=dict(l=0, r=0, t=5, b=0),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=12),
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="chokepoint_map")

    with col_table:
        table_rows = [{
            "Chokepoint": cp.name,
            "Risk":       cp.risk_level,
            "Trade %":    f"{cp.pct_world_trade:.0f}%",
            "Reroute":    f"+{cp.reroute_impact_days}d" if cp.reroute_impact_days > 0 else "N/A",
        } for cp in sorted(
            CHOKEPOINTS,
            key=lambda c: _risk_level_sort_key(c.risk_level, list(RISK_LEVELS.keys())),
            reverse=True,
        )]

        st.dataframe(
            pd.DataFrame(table_rows),
            use_container_width=True,
            hide_index=True,
            height=300,
        )


def _risk_level_sort_key(risk_level: str, risk_levels_keys: list) -> int:
    """Return sort index for a risk level, defaulting to -1 if not found."""
    try:
        return risk_levels_keys.index(risk_level)
    except ValueError:
        return -1


# ── News sentiment ────────────────────────────────────────────────────────────

def _render_news_sentiment() -> None:
    """Render the Market Sentiment news section using shipping RSS feeds."""
    from ui.styles import (
        C_BG, C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_HIGH,
        _hex_to_rgba, section_header,
    )
    from processing.news_feed import fetch_shipping_news, get_market_sentiment_summary

    C_WARN_L   = "#f59e0b"
    C_DANGER_L = "#ef4444"
    C_TEXT3_L  = "#64748b"

    section_header("Market Sentiment", "Live shipping news scored for market tone")

    try:
        news = fetch_shipping_news()
    except Exception:
        news = []

    if not news:
        st.markdown(
            f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER};
                border-radius:10px; padding:16px 20px; color:{C_TEXT2};
                font-size:0.88rem; text-align:center">
                &#8505;&nbsp; News feed unavailable — check network connection
            </div>""",
            unsafe_allow_html=True,
        )
        return

    summary    = get_market_sentiment_summary(news)
    avg_score  = summary["avg_sentiment"]
    label      = summary["sentiment_label"]
    bullish    = summary["bullish_count"]
    bearish    = summary["bearish_count"]
    top_kw     = summary["top_keywords"]

    if label == "Bullish":
        badge_icon  = "&#128994;"
        badge_color = C_HIGH
    elif label == "Bearish":
        badge_icon  = "&#128308;"
        badge_color = C_DANGER_L
    else:
        badge_icon  = "&#9898;"
        badge_color = C_TEXT2

    badge_bg     = _hex_to_rgba(badge_color, 0.15)
    badge_border = _hex_to_rgba(badge_color, 0.35)

    kw_str = ", ".join(top_kw[:6]) if top_kw else "—"
    st.markdown(
        f"""<div style="display:flex; gap:12px; align-items:center;
                flex-wrap:wrap; margin-bottom:16px; padding:14px 18px;
                background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:10px">
            <span style="background:{badge_bg}; color:{badge_color};
                border:1px solid {badge_border}; padding:4px 14px;
                border-radius:999px; font-size:0.82rem; font-weight:700">
                {badge_icon}&nbsp;{label}
            </span>
            <span style="color:{C_TEXT2}; font-size:0.82rem">
                {len(news)} articles &nbsp;|&nbsp;
                avg score <b style="color:{C_TEXT}">{avg_score:+.2f}</b> &nbsp;|&nbsp;
                &#128994; {bullish} bullish &nbsp; &#128308; {bearish} bearish
            </span>
            <span style="color:{C_TEXT3_L}; font-size:0.78rem; margin-left:auto">
                Top signals: {kw_str}
            </span>
        </div>""",
        unsafe_allow_html=True,
    )

    for item in news[:6]:
        title_truncated = item.title[:80] + ("…" if len(item.title) > 80 else "")
        score     = item.sentiment_score
        relevance = item.relevance_score

        if score > 0.2:
            card_border = C_HIGH
        elif score < -0.2:
            card_border = C_DANGER_L
        else:
            card_border = C_BORDER

        if score > 0.1:
            dot_color = C_HIGH;      dot_title = "Bullish"
        elif score < -0.1:
            dot_color = C_DANGER_L;  dot_title = "Bearish"
        else:
            dot_color = C_TEXT2;     dot_title = "Neutral"

        src_bg     = _hex_to_rgba("#3b82f6", 0.12)
        src_border = _hex_to_rgba("#3b82f6", 0.3)
        rel_pct    = int(relevance * 100)
        rel_fill   = _hex_to_rgba("#3b82f6", 0.7)
        rel_bg     = _hex_to_rgba("#3b82f6", 0.12)

        st.markdown(
            f"""<div style="background:{C_CARD}; border:1px solid {card_border};
                    border-radius:10px; padding:12px 16px; margin-bottom:7px">
                <div style="display:flex; align-items:flex-start; gap:10px">
                    <span style="margin-top:3px; width:10px; height:10px; min-width:10px;
                        border-radius:50%; background:{dot_color};
                        display:inline-block" title="{dot_title}"></span>
                    <div style="flex:1; min-width:0">
                        <div style="font-size:0.88rem; font-weight:600; color:{C_TEXT};
                            line-height:1.4; margin-bottom:6px">
                            <a href="{item.url}" target="_blank"
                               style="color:{C_TEXT}; text-decoration:none"
                               onmouseover="this.style.textDecoration='underline'"
                               onmouseout="this.style.textDecoration='none'"
                            >{title_truncated}</a>
                        </div>
                        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
                            <span style="background:{src_bg}; color:#3b82f6;
                                border:1px solid {src_border};
                                padding:1px 8px; border-radius:999px;
                                font-size:0.7rem; font-weight:600">{item.source}</span>
                            <span style="color:{C_TEXT3_L}; font-size:0.75rem">
                                {item.published_dt.strftime("%b %d") if item.published_dt else "—"}
                            </span>
                            <span style="color:{dot_color}; font-size:0.75rem; font-weight:600">
                                {score:+.1f}
                            </span>
                            <div style="display:flex; align-items:center; gap:5px; margin-left:auto">
                                <span style="color:{C_TEXT3_L}; font-size:0.72rem">relevance</span>
                                <div style="width:60px; height:5px; border-radius:3px;
                                    background:{rel_bg}; overflow:hidden">
                                    <div style="width:{rel_pct}%; height:100%;
                                        background:{rel_fill}; border-radius:3px"></div>
                                </div>
                                <span style="color:{C_TEXT2}; font-size:0.72rem">{rel_pct}%</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )


# ── Summary panel ─────────────────────────────────────────────────────────────

def _render_summary_panel(
    port_scores: dict[str, PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> None:
    has_data = [r for r in port_scores.values() if r.has_real_data]
    C_CONV   = "#8b5cf6"

    def stat_card(label, value, sub="", color=C_ACCENT):
        sub_html = f'<div style="font-size:0.78rem; color:{C_TEXT2}">{sub}</div>' if sub else ""
        return f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER};
            border-top:3px solid {color}; border-radius:10px; padding:14px 16px; margin-bottom:8px">
            <div style="font-size:0.68rem; font-weight:700; color:{C_TEXT3};
                text-transform:uppercase; letter-spacing:0.07em">{label}</div>
            <div style="font-size:1.7rem; font-weight:800; color:{C_TEXT}; line-height:1.1; margin:4px 0">{value}</div>
            {sub_html}
        </div>"""

    if has_data:
        avg_d    = sum(r.demand_score for r in has_data) / len(has_data)
        high_c   = sum(1 for r in has_data if r.demand_score >= 0.70)
        rising_c = sum(1 for r in has_data if r.demand_trend == "Rising")
        top_port = max(has_data, key=lambda r: r.demand_score)

        avg_color = C_HIGH if avg_d > 0.55 else C_WARN
        st.markdown(
            stat_card("Ports Tracked", str(len(port_scores)), f"{len(has_data)} with live data"),
            unsafe_allow_html=True,
        )
        st.markdown(
            stat_card(
                "Avg Global Demand",
                f"{avg_d:.0%}",
                f"{high_c} high · {rising_c} rising",
                avg_color,
            ),
            unsafe_allow_html=True,
        )

        if insights:
            top = insights[0]
            cat_colors = {
                "CONVERGENCE": C_CONV,
                "ROUTE":       C_ACCENT,
                "PORT_DEMAND": C_HIGH,
                "MACRO":       "#06b6d4",
            }
            cat_icons = {
                "CONVERGENCE": "&#9889;",
                "ROUTE":       "&#128674;",
                "PORT_DEMAND": "&#128679;",
                "MACRO":       "&#128200;",
            }
            color = cat_colors.get(top.category, C_ACCENT)
            icon  = cat_icons.get(top.category, "&#128161;")
            title_trunc = top.title[:55] + ("..." if len(top.title) > 55 else "")
            st.markdown(f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER};
                border-left:3px solid {color}; border-radius:10px; padding:14px 16px; margin-bottom:8px">
                <div style="font-size:0.68rem; font-weight:700; color:{C_TEXT3};
                    text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px">Top Signal</div>
                <div style="font-size:0.88rem; font-weight:600; color:{C_TEXT}; line-height:1.3">
                    {icon} {title_trunc}</div>
                <div style="margin-top:8px; display:flex; gap:8px; align-items:center">
                    <span style="background:rgba(255,255,255,0.06); color:{color};
                        padding:2px 10px; border-radius:999px; font-size:0.72rem; font-weight:700">{top.action}</span>
                    <span style="color:{C_TEXT2}; font-size:0.78rem">{top.score:.0%} confidence</span>
                </div>
            </div>""", unsafe_allow_html=True)

        st.markdown(f"""<div style="background:{C_CARD}; border:1px solid {C_BORDER};
            border-left:3px solid {C_HIGH}; border-radius:10px; padding:14px 16px">
            <div style="font-size:0.68rem; font-weight:700; color:{C_TEXT3};
                text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px">Highest Demand</div>
            <div style="font-size:1rem; font-weight:700; color:{C_TEXT}">{top_port.port_name}</div>
            <div style="font-size:0.82rem; color:{C_TEXT2}; margin-top:2px">
                {top_port.demand_score:.0%} · {top_port.demand_trend} · {top_port.region}</div>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(
            stat_card("Ports Tracked", str(len(port_scores)), "Loading demand data..."),
            unsafe_allow_html=True,
        )
        st.info("Add API credentials in .env to enable demand scoring.", icon="ℹ️")


# ── Main render ───────────────────────────────────────────────────────────────

def render(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    trade_data: dict | None = None,
) -> None:
    """Render the Overview tab — 3D globe + global summary."""

    # Build lookup for port results
    port_scores: dict[str, PortDemandResult] = {r.locode: r for r in port_results}

    # ── 1. Hero banner ───────────────────────────────────────────────────────
    _render_hero_banner(port_scores, route_results, insights)

    # ── 2. Globe + summary panel ─────────────────────────────────────────────
    col_globe, col_panel = st.columns([3, 1])

    with col_globe:
        st.markdown(
            f'<div style="font-size:1rem; font-weight:700; color:{C_TEXT}; margin-bottom:8px">'
            '&#127760; Live Port Intelligence Globe'
            '</div>',
            unsafe_allow_html=True,
        )
        fig_globe = _build_globe(port_scores, route_results)
        st.plotly_chart(fig_globe, use_container_width=True, key="globe_chart")

    with col_panel:
        _render_summary_panel(port_scores, route_results, insights)

    # ── 3. Signal feed ───────────────────────────────────────────────────────
    st.divider()
    _render_signal_feed(insights)

    st.divider()

    # ── 4. Region breakdown + top routes ─────────────────────────────────────
    col_regions, col_routes = st.columns(2)

    with col_regions:
        st.markdown(
            f'<div style="font-size:1rem; font-weight:700; color:{C_TEXT}; margin-bottom:8px">'
            'Demand by Region'
            '</div>',
            unsafe_allow_html=True,
        )
        _render_region_chart(port_scores)

    with col_routes:
        st.markdown(
            f'<div style="font-size:1rem; font-weight:700; color:{C_TEXT}; margin-bottom:8px">'
            'Top Route Opportunities'
            '</div>',
            unsafe_allow_html=True,
        )
        _render_top_routes(route_results)

    # ── 5. Trade flow Sankey ─────────────────────────────────────────────────
    st.divider()
    from ui.styles import section_header
    section_header(
        "Trade Flow by Region",
        "Regional trade flows derived from live data or illustrative estimates",
    )
    _render_trade_flow_sankey(trade_data, route_results)

    # ── 6. Chokepoint risk monitor ───────────────────────────────────────────
    st.divider()
    st.markdown(
        f'<div style="font-size:1rem; font-weight:700; color:{C_TEXT}; margin-bottom:8px">'
        'Chokepoint Risk Monitor'
        '</div>',
        unsafe_allow_html=True,
    )
    _render_chokepoint_section()

    # ── 7. News sentiment ────────────────────────────────────────────────────
    st.divider()
    _render_news_sentiment()
