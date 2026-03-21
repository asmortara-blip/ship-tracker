from __future__ import annotations

import time
from datetime import datetime, timezone

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
C_CARD2  = "#131c2e"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"
C_ACCENT = "#3b82f6"
C_WARN   = "#f59e0b"
C_DANGER = "#ef4444"
C_PURPLE = "#8b5cf6"
C_CYAN   = "#06b6d4"
C_PINK   = "#ec4899"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _arc_points(lat1, lon1, lat2, lon2, n=20):
    lats = [lat1 + (lat2 - lat1) * i / (n - 1) for i in range(n)]
    lons = [lon1 + (lon2 - lon1) * i / (n - 1) for i in range(n)]
    return lats, lons


def _score_color(score: float) -> str:
    if score > 0.65: return C_HIGH
    if score > 0.45: return C_WARN
    return C_DANGER


def _demand_css_color(score: float) -> str:
    stops = [
        (0.00, (30,  58,  95)),
        (0.25, (59, 130, 246)),
        (0.50, (16, 185, 129)),
        (0.75, (245,158,  11)),
        (1.00, (239,  68,  68)),
    ]
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]; t1, c1 = stops[i + 1]
        if t0 <= score <= t1:
            p = (score - t0) / (t1 - t0)
            return "#{:02x}{:02x}{:02x}".format(
                int(c0[0]+(c1[0]-c0[0])*p),
                int(c0[1]+(c1[1]-c0[1])*p),
                int(c0[2]+(c1[2]-c0[2])*p),
            )
    return C_HIGH


def _rgba(h: str, a: float) -> str:
    h2 = h.lstrip("#")
    r, g, b = int(h2[0:2],16), int(h2[2:4],16), int(h2[4:6],16)
    return f"rgba({r},{g},{b},{a})"


def _demand_score_for(port, port_scores):
    r = port_scores.get(port.locode)
    return r.demand_score if (r and r.has_real_data) else 0.35


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Shared CSS animations injected once ───────────────────────────────────────

def _inject_css() -> None:
    st.markdown("""
    <style>
    @keyframes pulse {
        0%,100% { opacity:1; transform:scale(1); }
        50%      { opacity:0.45; transform:scale(1.4); }
    }
    @keyframes slideUp {
        from { opacity:0; transform:translateY(12px); }
        to   { opacity:1; transform:translateY(0); }
    }
    @keyframes ticker {
        0%   { transform: translateX(0); }
        100% { transform: translateX(-50%); }
    }
    @keyframes fadeIn {
        from { opacity:0; }
        to   { opacity:1; }
    }
    @keyframes glow {
        0%,100% { box-shadow: 0 0 12px rgba(59,130,246,0.3); }
        50%      { box-shadow: 0 0 28px rgba(59,130,246,0.6); }
    }
    .ov-hero   { animation: slideUp 0.45s ease-out; }
    .ov-card   { animation: fadeIn 0.5s ease-out; }
    .ov-pulse  { animation: pulse 2s infinite; }
    .ov-glow   { animation: glow 3s ease-in-out infinite; }
    .ticker-wrap {
        overflow: hidden;
        white-space: nowrap;
    }
    .ticker-inner {
        display: inline-block;
        animation: ticker 38s linear infinite;
    }
    </style>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Hero Banner
# ══════════════════════════════════════════════════════════════════════════════

def _render_hero(
    port_scores: dict[str, PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> None:
    try:
        has_data   = [r for r in port_scores.values() if r.has_real_data]
        avg_demand = sum(r.demand_score for r in has_data) / len(has_data) if has_data else 0.0
        top_score  = max((i.score for i in insights), default=0.0)
        hi_conv    = sum(1 for i in insights if i.score >= 0.70)
        strong_rts = sum(1 for r in route_results if r.opportunity_label == "Strong")
        avg_rate   = sum(r.current_rate_usd_feu for r in route_results if r.current_rate_usd_feu > 0)
        rate_ct    = sum(1 for r in route_results if r.current_rate_usd_feu > 0)
        avg_rate_v = avg_rate / rate_ct if rate_ct else 0

        # Market headline
        if avg_demand > 0.65:
            headline = "Global freight markets are HOT — elevated demand across key corridors"
            h_color  = C_HIGH
        elif avg_demand > 0.45:
            headline = "Mixed market signals — selective opportunities in mid-tier lanes"
            h_color  = C_WARN
        elif avg_demand > 0:
            headline = "Subdued demand environment — defensive positioning recommended"
            h_color  = C_DANGER
        else:
            headline = "Awaiting live data — platform initializing market intelligence"
            h_color  = C_TEXT2

        demand_color = _score_color(avg_demand) if avg_demand > 0 else C_TEXT3
        conv_color   = C_HIGH if hi_conv > 0 else C_TEXT3
        rate_str     = f"${avg_rate_v:,.0f}" if avg_rate_v > 0 else "—"

        kpis = [
            ("PORTS TRACKED",     str(len(port_scores)),     C_ACCENT,  "active monitors"),
            ("AVG PORT DEMAND",   f"{avg_demand:.0%}" if avg_demand > 0 else "—",
                                                             demand_color, "real-time score"),
            ("ACTIVE SIGNALS",    str(len(insights)),        C_WARN,    "engine outputs"),
            ("HIGH-CONVICTION",   str(hi_conv),              conv_color, "score ≥ 70%"),
            ("STRONG ROUTES",     str(strong_rts),           C_HIGH,    "top opportunities"),
            ("AVG FREIGHT RATE",  rate_str,                  C_CYAN,    "USD/FEU"),
            ("TOP SIGNAL SCORE",  f"{top_score:.0%}" if top_score > 0 else "—",
                                                             _score_color(top_score) if top_score else C_TEXT3,
                                                                        "best conviction"),
        ]

        kpi_html = ""
        for idx, (label, val, color, sub) in enumerate(kpis):
            sep = "border-right:1px solid rgba(255,255,255,0.06);" if idx < len(kpis)-1 else ""
            kpi_html += f"""
            <div style="flex:1;min-width:110px;text-align:center;padding:0 16px;{sep}">
                <div style="font-size:2.1rem;font-weight:900;color:{color};
                            letter-spacing:-0.02em;line-height:1;
                            text-shadow:0 0 24px {_rgba(color,0.45)}">{val}</div>
                <div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};
                            text-transform:uppercase;letter-spacing:0.12em;margin-top:6px">{label}</div>
                <div style="font-size:0.68rem;color:{C_TEXT3};margin-top:2px;opacity:0.7">{sub}</div>
            </div>"""

        live_dot = (
            f'<span class="ov-pulse" style="display:inline-block;width:8px;height:8px;'
            f'border-radius:50%;background:{C_HIGH};margin-right:7px;'
            f'box-shadow:0 0 10px {C_HIGH}"></span>'
        )

        st.markdown(f"""
        <div class="ov-hero" style="
            background:linear-gradient(135deg,{C_BG} 0%,{C_CARD} 45%,#0d1a2e 100%);
            border:1px solid rgba(59,130,246,0.28);
            border-top:3px solid {C_ACCENT};
            border-radius:18px;padding:32px 36px 28px;
            margin-bottom:20px;
            box-shadow:0 8px 48px rgba(0,0,0,0.5),inset 0 1px 0 rgba(255,255,255,0.04)">

            <div style="display:flex;justify-content:space-between;align-items:flex-start;
                        margin-bottom:24px;flex-wrap:wrap;gap:12px">
                <div>
                    <div style="font-size:0.68rem;font-weight:700;color:{C_ACCENT};
                                text-transform:uppercase;letter-spacing:0.14em;margin-bottom:6px">
                        Global Cargo Intelligence Platform
                    </div>
                    <div style="font-size:1.5rem;font-weight:800;color:{C_TEXT};
                                letter-spacing:-0.02em;line-height:1.25;max-width:680px">
                        {headline}
                    </div>
                    <div style="font-size:0.8rem;color:{C_TEXT2};margin-top:8px">
                        {live_dot}
                        <span style="color:{C_HIGH};font-weight:600">LIVE</span>
                        &nbsp;&middot;&nbsp; {_now_utc()}
                        &nbsp;&middot;&nbsp; Confidence threshold: 70%
                    </div>
                </div>
                <div style="text-align:right;flex-shrink:0">
                    <div style="font-size:0.65rem;font-weight:700;color:{C_TEXT3};
                                text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">
                        Market Tone
                    </div>
                    <div style="font-size:1.1rem;font-weight:800;color:{h_color};
                                background:{_rgba(h_color,0.1)};border:1px solid {_rgba(h_color,0.3)};
                                border-radius:10px;padding:8px 18px;
                                box-shadow:0 0 20px {_rgba(h_color,0.2)}">
                        {'BULLISH' if avg_demand > 0.65 else 'NEUTRAL' if avg_demand > 0.45 else 'BEARISH' if avg_demand > 0 else 'LOADING'}
                    </div>
                </div>
            </div>

            <div style="display:flex;flex-wrap:wrap;border-top:1px solid rgba(255,255,255,0.06);
                        padding-top:24px;gap:0">
                {kpi_html}
            </div>
        </div>
        """, unsafe_allow_html=True)
    except Exception:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:14px;'
            f'padding:20px;color:{C_TEXT2}">Hero section unavailable</div>',
            unsafe_allow_html=True
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Market Pulse Strip (animated ticker)
# ══════════════════════════════════════════════════════════════════════════════

def _render_market_pulse(route_results: list[RouteOpportunity]) -> None:
    try:
        # Simulated / derived market indices — would hook into real data feeds in production
        trans_pac = next(
            (r.current_rate_usd_feu for r in route_results
             if "trans" in r.route_name.lower() or "pacific" in r.route_name.lower()
             or "CNSHA" in (r.origin_locode or "") or "USLAX" in (r.dest_locode or "")), 0
        )
        asia_eur = next(
            (r.current_rate_usd_feu for r in route_results
             if "europe" in r.route_name.lower() or "DEHAM" in (r.dest_locode or "")
             or "NLRTM" in (r.dest_locode or "")), 0
        )

        items = [
            ("BDI",       "1,847",    "+2.3%", C_HIGH,   "Baltic Dry Index"),
            ("FBX",       "2,204",    "+0.8%", C_HIGH,   "Freightos Baltic"),
            ("TRANS-PAC", f"${trans_pac:,.0f}" if trans_pac else "$2,850", "+1.2%", C_ACCENT, "Asia-USWC $/FEU"),
            ("ASIA-EUR",  f"${asia_eur:,.0f}"  if asia_eur  else "$3,120", "-0.4%", C_WARN,   "Asia-N.Europe $/FEU"),
            ("HLAG",      "$18.42",   "+3.1%", C_HIGH,   "Hapag-Lloyd AG"),
            ("MAERSK",    "DKK 9,240","+1.7%", C_HIGH,   "A.P. Moller-Maersk"),
            ("ZIM",       "$12.88",   "-1.2%", C_DANGER, "ZIM Integrated"),
            ("MATX",      "$118.40",  "+0.9%", C_HIGH,   "Matson Inc"),
            ("EXPD",      "$102.75",  "+0.3%", C_ACCENT, "Expeditors Intl"),
            ("SBLK",      "$21.44",   "+4.2%", C_HIGH,   "Star Bulk Carriers"),
        ]

        # Build double-set for seamless loop
        def item_html(label, val, chg, color, tooltip):
            chg_color = C_HIGH if chg.startswith("+") else C_DANGER
            return (
                f'<span style="display:inline-flex;align-items:center;gap:8px;'
                f'margin-right:36px;cursor:default" title="{tooltip}">'
                f'<span style="font-size:0.65rem;font-weight:700;color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.1em">{label}</span>'
                f'<span style="font-size:0.82rem;font-weight:700;color:{C_TEXT}">{val}</span>'
                f'<span style="font-size:0.72rem;font-weight:700;color:{chg_color}">{chg}</span>'
                f'<span style="width:1px;height:14px;background:rgba(255,255,255,0.1);'
                f'display:inline-block;margin-left:2px"></span>'
                f'</span>'
            )

        inner = "".join(item_html(*i) for i in items) * 2  # double for seamless

        st.markdown(f"""
        <div style="background:{C_CARD2};border:1px solid {C_BORDER};
                    border-radius:10px;padding:10px 20px;margin-bottom:16px;
                    overflow:hidden;position:relative">
            <div style="display:flex;align-items:center;gap:0">
                <div style="font-size:0.62rem;font-weight:700;color:{C_ACCENT};
                            text-transform:uppercase;letter-spacing:0.12em;
                            white-space:nowrap;margin-right:16px;flex-shrink:0;
                            padding-right:16px;border-right:1px solid {C_BORDER}">
                    MARKET PULSE
                </div>
                <div class="ticker-wrap" style="flex:1">
                    <div class="ticker-inner">{inner}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Global Shipping Map (Scattergeo, natural earth)
# ══════════════════════════════════════════════════════════════════════════════

def _render_shipping_map(
    port_scores: dict[str, PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        DEMAND_CS = [
            [0.00, "#1e3a5f"], [0.25, "#3b82f6"],
            [0.50, "#10b981"], [0.75, "#f59e0b"], [1.00, "#ef4444"],
        ]

        fig = go.Figure()

        # Route arcs — top 15
        top_routes = sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)[:15]
        for route in top_routes:
            try:
                origin = PORTS_BY_LOCODE.get(route.origin_locode)
                dest   = PORTS_BY_LOCODE.get(route.dest_locode)
                if not origin or not dest:
                    continue
                lats, lons = _arc_points(origin.lat, origin.lon, dest.lat, dest.lon, n=22)
                sc = route.opportunity_score
                arc_color = (
                    f"rgba(16,185,129,{0.55+sc*0.35})"  if sc > 0.65 else
                    f"rgba(245,158,11,{0.4+sc*0.2})"    if sc > 0.45 else
                    "rgba(55,65,81,0.35)"
                )
                fig.add_trace(go.Scattergeo(
                    lat=lats, lon=lons, mode="lines",
                    line=dict(width=1.2 + sc * 2.4, color=arc_color),
                    hovertext=f"{route.route_name} — {sc:.0%}",
                    hoverinfo="text", showlegend=False, name=route.route_name,
                ))
            except Exception:
                continue

        # Port glow layer
        g_lats, g_lons, g_sizes, g_colors = [], [], [], []
        m_lats, m_lons, m_sizes, m_scores, m_texts = [], [], [], [], []

        for port in PORTS:
            try:
                result = port_scores.get(port.locode)
                demand = result.demand_score if (result and result.has_real_data) else 0.32
                sz = 7 + demand * 13
                color = _demand_css_color(demand)

                if result and result.has_real_data:
                    top_prod = result.top_products[0]["category"] if result.top_products else "—"
                    hover = (
                        f"<b>{port.name}</b> ({port.locode})"
                        f"<br>Region: {port.region}"
                        f"<br>Demand: {result.demand_label} ({demand:.0%})"
                        f"<br>Trend: {result.demand_trend}"
                        f"<br>Top cargo: {top_prod}"
                        f"<br>Vessels: {result.vessel_count}"
                    )
                else:
                    hover = f"<b>{port.name}</b> ({port.locode})<br>Awaiting data"

                g_lats.append(port.lat); g_lons.append(port.lon)
                g_sizes.append(sz * 1.9); g_colors.append(color)
                m_lats.append(port.lat); m_lons.append(port.lon)
                m_sizes.append(sz); m_scores.append(demand); m_texts.append(hover)
            except Exception:
                continue

        fig.add_trace(go.Scattergeo(
            lat=g_lats, lon=g_lons, mode="markers",
            marker=dict(size=g_sizes, color=g_colors, opacity=0.18, line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scattergeo(
            lat=m_lats, lon=m_lons, mode="markers",
            marker=dict(
                size=m_sizes,
                color=m_scores,
                colorscale=DEMAND_CS, cmin=0, cmax=1,
                opacity=0.92,
                line=dict(color="rgba(255,255,255,0.55)", width=0.7),
                colorbar=dict(
                    title=dict(text="Demand", font=dict(color=C_TEXT2, size=10)),
                    thickness=9, len=0.55, x=1.01,
                    tickfont=dict(color=C_TEXT2, size=9),
                    bgcolor="rgba(0,0,0,0)",
                    bordercolor="rgba(255,255,255,0.08)",
                ),
            ),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=m_texts, showlegend=False, name="ports",
        ))

        fig.update_layout(
            paper_bgcolor=C_BG,
            height=460,
            margin=dict(l=0, r=0, t=0, b=0),
            geo=dict(
                projection_type="natural earth",
                showland=True,      landcolor="#111d30",
                showocean=True,     oceancolor="#070c15",
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.12)",
                showcountries=True, countrycolor="rgba(255,255,255,0.06)",
                showlakes=False,    showframe=False,
                bgcolor=C_BG,
                lonaxis=dict(range=[-170, 190]),
                lataxis=dict(range=[-60, 75]),
            ),
            hoverlabel=dict(
                bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                font=dict(color=C_TEXT, size=12),
            ),
        )

        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">'
            f'&#127760;&nbsp; Global Port Demand Map — {len(PORTS)} ports monitored, '
            f'top {len(top_routes)} routes overlaid</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig, use_container_width=True, key="ov_shipping_map")
    except Exception as exc:
        st.warning(f"Map unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Top Signals Dashboard (6-card grid)
# ══════════════════════════════════════════════════════════════════════════════

def _render_top_signals(insights: list[Insight]) -> None:
    try:
        if not insights:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:12px;padding:24px;color:{C_TEXT2};text-align:center">'
                f'No signals generated yet — engine initializing</div>',
                unsafe_allow_html=True,
            )
            return

        CAT_COLORS = {"CONVERGENCE": C_PURPLE, "ROUTE": C_ACCENT, "PORT_DEMAND": C_HIGH, "MACRO": C_CYAN}
        CAT_ICONS  = {"CONVERGENCE": "&#9889;", "ROUTE": "&#128674;", "PORT_DEMAND": "&#128679;", "MACRO": "&#128200;"}
        ACT_COLORS = {"Prioritize": C_HIGH, "Monitor": C_ACCENT, "Watch": C_TEXT2, "Caution": C_WARN, "Avoid": C_DANGER}

        top6 = sorted(insights, key=lambda i: i.score, reverse=True)[:6]

        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">'
            f'&#128302;&nbsp; Highest-Conviction Signals — Top 6 of {len(insights)}</div>',
            unsafe_allow_html=True,
        )

        cols = st.columns(3)
        for idx, ins in enumerate(top6):
            col = cols[idx % 3]
            cc  = CAT_COLORS.get(ins.category, C_ACCENT)
            ci  = CAT_ICONS.get(ins.category, "&#128161;")
            ac  = ACT_COLORS.get(ins.action, C_ACCENT)
            sc  = _score_color(ins.score)
            pct = int(ins.score * 100)
            title_s = ins.title[:68] + ("..." if len(ins.title) > 68 else "")
            detail_s = ins.detail[:110] + ("..." if len(ins.detail) > 110 else "")
            is_conv = ins.category == "CONVERGENCE"
            conv_ring = f"box-shadow:0 0 0 2px {_rgba(C_PURPLE,0.4)},0 0 24px {_rgba(C_PURPLE,0.12)};" if is_conv else ""

            with col:
                st.markdown(f"""
                <div class="ov-card" style="
                    background:linear-gradient(145deg,{_rgba(cc,0.07)} 0%,{C_CARD} 55%);
                    border:1px solid {_rgba(cc,0.28)};
                    border-top:3px solid {cc};
                    border-radius:14px;padding:16px 18px;margin-bottom:14px;
                    {conv_ring}">
                    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
                        <div style="font-size:0.65rem;font-weight:700;color:{cc};
                                    text-transform:uppercase;letter-spacing:0.1em">
                            {ci}&nbsp; {ins.category.replace('_',' ')}
                        </div>
                        <div style="font-size:1.4rem;font-weight:900;color:{sc};
                                    line-height:1;text-shadow:0 0 16px {_rgba(sc,0.5)}">
                            {pct}%
                        </div>
                    </div>
                    <div style="font-size:0.86rem;font-weight:700;color:{C_TEXT};
                                line-height:1.35;margin-bottom:8px">{title_s}</div>
                    <div style="height:4px;border-radius:2px;background:rgba(255,255,255,0.06);
                                overflow:hidden;margin-bottom:10px">
                        <div style="width:{pct}%;height:100%;border-radius:2px;
                                    background:linear-gradient(90deg,{_rgba(sc,0.6)},{sc});
                                    box-shadow:0 0 10px {_rgba(sc,0.4)}"></div>
                    </div>
                    <div style="font-size:0.75rem;color:{C_TEXT2};line-height:1.5;
                                margin-bottom:10px">{detail_s}</div>
                    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                        <span style="background:{_rgba(ac,0.14)};color:{ac};
                                     border:1px solid {_rgba(ac,0.32)};
                                     padding:2px 10px;border-radius:999px;
                                     font-size:0.67rem;font-weight:700">{ins.action}</span>
                        {"<span style='background:rgba(245,158,11,0.1);color:#f59e0b;"
                         "padding:2px 7px;border-radius:5px;font-size:0.65rem'>stale data</span>"
                         if ins.data_freshness_warning else ""}
                    </div>
                </div>
                """, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Signals dashboard unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Market Summary Cards (4 columns)
# ══════════════════════════════════════════════════════════════════════════════

def _render_market_summary(
    port_scores: dict[str, PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> None:
    try:
        has_data = [r for r in port_scores.values() if r.has_real_data]

        # Ports
        if has_data:
            avg_d   = sum(r.demand_score for r in has_data) / len(has_data)
            high_ct = sum(1 for r in has_data if r.demand_score >= 0.70)
            rising  = sum(1 for r in has_data if r.demand_trend == "Rising")
            port_status = "HIGH" if avg_d > 0.65 else "MODERATE" if avg_d > 0.45 else "LOW"
            port_color  = _score_color(avg_d)
            port_body   = (
                f"<b style='color:{C_TEXT}'>{len(has_data)}</b> ports with live data<br>"
                f"<b style='color:{port_color}'>{high_ct}</b> in high demand &nbsp;·&nbsp; "
                f"<b style='color:{C_HIGH}'>{rising}</b> rising trend<br>"
                f"Avg demand: <b style='color:{port_color}'>{avg_d:.0%}</b>"
            )
        else:
            port_status, port_color = "LOADING", C_TEXT3
            port_body = "Demand data loading..."

        # Routes
        if route_results:
            strong   = sum(1 for r in route_results if r.opportunity_label == "Strong")
            moderate = sum(1 for r in route_results if r.opportunity_label == "Moderate")
            avg_opp  = sum(r.opportunity_score for r in route_results) / len(route_results)
            rt_status = "STRONG" if avg_opp > 0.65 else "MIXED" if avg_opp > 0.45 else "WEAK"
            rt_color  = _score_color(avg_opp)
            rt_body   = (
                f"<b style='color:{C_TEXT}'>{len(route_results)}</b> routes monitored<br>"
                f"<b style='color:{C_HIGH}'>{strong}</b> strong &nbsp;·&nbsp; "
                f"<b style='color:{C_WARN}'>{moderate}</b> moderate<br>"
                f"Avg opportunity: <b style='color:{rt_color}'>{avg_opp:.0%}</b>"
            )
        else:
            rt_status, rt_color = "LOADING", C_TEXT3
            rt_body = "Route data loading..."

        # Carriers (from stocks in insights)
        all_stocks = list({s for i in insights for s in (i.stocks_potentially_affected or [])})
        carrier_tickers = [s for s in all_stocks if s in ("MAERSK","ZIM","HLAG","MATX","EXPD","SBLK","DSX","GOGL")]
        car_status = f"{len(carrier_tickers)} TRACKED" if carrier_tickers else "MONITORING"
        car_color  = C_ACCENT
        car_body   = (
            f"Carriers in signal scope:<br>"
            f"<b style='color:{C_ACCENT}'>{', '.join(carrier_tickers[:5]) or 'None flagged'}</b><br>"
            f"{'<b style=\"color:' + C_HIGH + '\">' + str(len(carrier_tickers)) + '</b> tickers across active insights' if carrier_tickers else 'No carrier-specific signals'}"
        )

        # Macro / Insights
        macro_ins   = [i for i in insights if i.category == "MACRO"]
        conv_ins    = [i for i in insights if i.category == "CONVERGENCE"]
        macro_color = C_CYAN if macro_ins else C_TEXT3
        macro_status = f"{len(macro_ins)} SIGNALS" if macro_ins else "NONE"
        macro_body  = (
            f"<b style='color:{C_TEXT}'>{len(macro_ins)}</b> macro signals active<br>"
            f"<b style='color:{C_PURPLE}'>{len(conv_ins)}</b> convergence signals<br>"
            f"{'Top: ' + macro_ins[0].title[:45] + '...' if macro_ins else 'No macro signals detected'}"
        )

        sections = [
            ("&#128679; Ports",    port_status,   port_color,   port_body),
            ("&#128674; Routes",   rt_status,     rt_color,     rt_body),
            ("&#128756; Carriers", car_status,    car_color,    car_body),
            ("&#128200; Macro",    macro_status,  macro_color,  macro_body),
        ]

        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">'
            f'&#128203;&nbsp; Market Summary</div>',
            unsafe_allow_html=True,
        )

        cols = st.columns(4)
        for col, (title, status, color, body) in zip(cols, sections):
            with col:
                st.markdown(f"""
                <div class="ov-card" style="
                    background:{C_CARD};border:1px solid {_rgba(color,0.25)};
                    border-top:3px solid {color};border-radius:14px;
                    padding:18px 16px;height:100%">
                    <div style="display:flex;justify-content:space-between;
                                align-items:flex-start;margin-bottom:10px">
                        <div style="font-size:0.78rem;font-weight:700;color:{C_TEXT}">{title}</div>
                        <div style="font-size:0.6rem;font-weight:700;color:{color};
                                    background:{_rgba(color,0.12)};
                                    border:1px solid {_rgba(color,0.3)};
                                    padding:2px 8px;border-radius:999px;
                                    letter-spacing:0.06em;white-space:nowrap">{status}</div>
                    </div>
                    <div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.65">{body}</div>
                </div>
                """, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Market summary unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Recent Alerts Panel
# ══════════════════════════════════════════════════════════════════════════════

def _render_alerts_panel(insights: list[Insight]) -> None:
    try:
        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">'
            f'&#9888;&nbsp; Recent Alerts &amp; Signals Feed — Last {min(len(insights),10)} of {len(insights)}</div>',
            unsafe_allow_html=True,
        )

        if not insights:
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:12px;padding:20px;color:{C_TEXT2};text-align:center">'
                f'No alerts yet</div>',
                unsafe_allow_html=True,
            )
            return

        CAT_COLORS = {"CONVERGENCE": C_PURPLE, "ROUTE": C_ACCENT, "PORT_DEMAND": C_HIGH, "MACRO": C_CYAN}
        CAT_ICONS  = {"CONVERGENCE": "&#9889;", "ROUTE": "&#128674;", "PORT_DEMAND": "&#128679;", "MACRO": "&#128200;"}
        SEV_THRES  = [(0.80, "CRITICAL", C_DANGER), (0.65, "HIGH", C_WARN), (0.45, "MEDIUM", C_ACCENT), (0.0, "LOW", C_TEXT3)]

        sorted_ins = sorted(insights, key=lambda i: i.score, reverse=True)[:10]

        rows_html = ""
        for idx, ins in enumerate(sorted_ins):
            cc  = CAT_COLORS.get(ins.category, C_ACCENT)
            ci  = CAT_ICONS.get(ins.category, "&#128161;")
            sev_label, sev_color = next(((l, c) for t, l, c in SEV_THRES if ins.score >= t), ("LOW", C_TEXT3))
            title_s = ins.title[:80] + ("..." if len(ins.title) > 80 else "")
            row_bg  = "rgba(255,255,255,0.016)" if idx % 2 == 0 else "transparent"

            rows_html += f"""
            <div style="display:grid;grid-template-columns:32px 1fr auto;gap:12px;
                        align-items:center;padding:10px 16px;
                        border-bottom:1px solid rgba(255,255,255,0.04);
                        background:{row_bg};border-left:3px solid {cc}">
                <div style="font-size:1rem;text-align:center">{ci}</div>
                <div>
                    <div style="font-size:0.82rem;font-weight:600;color:{C_TEXT};
                                line-height:1.3;margin-bottom:3px">{title_s}</div>
                    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                        <span style="font-size:0.65rem;color:{C_TEXT3};
                                     text-transform:uppercase;letter-spacing:0.07em">
                            {ins.category.replace('_',' ')}
                        </span>
                        <span style="font-size:0.65rem;color:{C_TEXT3}">&middot;</span>
                        <span style="font-size:0.7rem;color:{_score_color(ins.score)};font-weight:700">
                            {ins.score:.0%} score
                        </span>
                        {"<span style='font-size:0.65rem;color:" + C_WARN + "'>&#9889; stale</span>" if ins.data_freshness_warning else ""}
                    </div>
                </div>
                <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
                    <span style="font-size:0.62rem;font-weight:700;color:{sev_color};
                                 background:{_rgba(sev_color,0.12)};
                                 border:1px solid {_rgba(sev_color,0.3)};
                                 padding:2px 8px;border-radius:999px;
                                 letter-spacing:0.08em">{sev_label}</span>
                    <span style="font-size:0.65rem;font-weight:700;color:{_score_color(ins.score)};
                                 background:{_rgba(cc,0.1)};
                                 border:1px solid {_rgba(cc,0.2)};
                                 padding:1px 8px;border-radius:999px">{ins.action}</span>
                </div>
            </div>"""

        st.markdown(f"""
        <div style="background:{C_CARD};border:1px solid {C_BORDER};
                    border-radius:14px;overflow:hidden">
            {rows_html}
        </div>
        """, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Alerts panel unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Platform Stats Footer
# ══════════════════════════════════════════════════════════════════════════════

def _render_platform_footer(
    port_scores: dict[str, PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> None:
    try:
        has_data = [r for r in port_scores.values() if r.has_real_data]
        stale    = [r for r in port_scores.values() if r.has_real_data and r.demand_score == 0]

        total_vessels = sum(r.vessel_count for r in has_data if hasattr(r, "vessel_count") and r.vessel_count)
        total_teu     = sum(r.throughput_teu_m for r in has_data if hasattr(r, "throughput_teu_m") and r.throughput_teu_m)
        all_products  = {}
        for r in has_data:
            for p in (r.top_products or []):
                cat = p.get("category", "")
                if cat:
                    all_products[cat] = all_products.get(cat, 0) + 1

        top3_prod = sorted(all_products.items(), key=lambda x: x[1], reverse=True)[:3]

        stats = [
            ("Ports Tracked",    str(len(port_scores)),     C_ACCENT, "total registered"),
            ("Live Data",        str(len(has_data)),        C_HIGH,   "with real-time feed"),
            ("Vessels Counted",  f"{total_vessels:,}" if total_vessels else "—", C_CYAN,   "across live ports"),
            ("TEU Capacity",     f"{total_teu:.1f}M" if total_teu else "—",     C_PURPLE, "annual throughput"),
            ("Routes Monitored", str(len(route_results)),   C_WARN,   "active corridors"),
            ("Signals Active",   str(len(insights)),        C_ACCENT, "engine outputs"),
            ("Data Freshness",   f"{len(has_data)}/{len(port_scores)}", C_HIGH if len(has_data) > 0 else C_DANGER, "ports current"),
            ("Last Refresh",     _now_utc(),                C_TEXT2,  "UTC timestamp"),
        ]

        prod_html = ""
        for prod, cnt in top3_prod:
            prod_html += (
                f'<span style="background:{_rgba(C_ACCENT,0.1)};color:{C_ACCENT};'
                f'border:1px solid {_rgba(C_ACCENT,0.25)};padding:2px 9px;'
                f'border-radius:999px;font-size:0.67rem;font-weight:600;margin-right:5px">'
                f'{prod} ({cnt})</span>'
            )

        stat_html = ""
        for label, val, color, sub in stats:
            stat_html += f"""
            <div style="flex:1;min-width:120px;padding:14px 16px;
                        border-right:1px solid rgba(255,255,255,0.05)">
                <div style="font-size:0.6rem;font-weight:700;color:{C_TEXT3};
                            text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">{label}</div>
                <div style="font-size:1.15rem;font-weight:800;color:{color};
                            line-height:1">{val}</div>
                <div style="font-size:0.65rem;color:{C_TEXT3};margin-top:3px">{sub}</div>
            </div>"""

        st.markdown(f"""
        <div style="background:{C_CARD2};border:1px solid {C_BORDER};
                    border-radius:14px;overflow:hidden;margin-top:8px">
            <div style="padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.05);
                        display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
                <div style="font-size:0.62rem;font-weight:700;color:{C_TEXT3};
                            text-transform:uppercase;letter-spacing:0.12em">
                    &#9729;&nbsp; Platform Data Registry
                </div>
                <div style="font-size:0.7rem;color:{C_TEXT2}">
                    Top cargo categories: &nbsp; {prod_html if prod_html else '<span style="color:' + C_TEXT3 + '">Loading...</span>'}
                </div>
            </div>
            <div style="display:flex;flex-wrap:wrap">
                {stat_html}
            </div>
        </div>
        """, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Platform footer unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Quick Action Panel
# ══════════════════════════════════════════════════════════════════════════════

def _render_quick_actions() -> None:
    try:
        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px">'
            f'&#9889;&nbsp; Quick Navigation</div>',
            unsafe_allow_html=True,
        )

        actions = [
            ("&#127760;", "Globe View",       C_ACCENT,  "Interactive 3D port map with demand heat"),
            ("&#128673;", "Signal Feed",      C_HIGH,    "Full real-time intelligence signal stream"),
            ("&#128200;", "Port Demand",      C_WARN,    "Deep-dive port scoring and trend analysis"),
            ("&#128674;", "Top Routes",       C_CYAN,    "Route opportunities ranked by score"),
            ("&#128302;", "Trade Flows",      C_PURPLE,  "Regional trade volume Sankey diagram"),
            ("&#9888;",   "Chokepoints",      C_DANGER,  "Suez, Panama, Malacca risk monitoring"),
            ("&#128240;", "Market Sentiment", C_PINK,    "Shipping news scored for market tone"),
            ("&#127775;", "Health Score",     C_HIGH,    "Supply chain composite health scorecard"),
        ]

        chips_html = ""
        for icon, label, color, tooltip in actions:
            chips_html += f"""
            <span title="{tooltip}" style="
                display:inline-flex;align-items:center;gap:6px;
                background:{_rgba(color,0.09)};color:{color};
                border:1px solid {_rgba(color,0.28)};
                padding:7px 16px;border-radius:999px;
                font-size:0.74rem;font-weight:700;letter-spacing:0.03em;
                margin:0 6px 8px 0;cursor:default;user-select:none;
                transition:background 0.2s">
                {icon}&nbsp;{label}
            </span>"""

        st.markdown(f"""
        <div style="margin-bottom:20px">
            <div style="display:flex;flex-wrap:wrap;align-items:center;gap:0">
                {chips_html}
            </div>
            <div style="font-size:0.68rem;color:{C_TEXT3};margin-top:6px">
                Use the sidebar navigation to jump to any section instantly.
            </div>
        </div>
        """, unsafe_allow_html=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# PRESERVED INTERNALS (globe builder, health scorecard, intelligence summary,
# region chart, route cards, Sankey, chokepoints, news sentiment)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_health_score(port_scores, route_results, insights):
    components = []
    has_data = [r for r in port_scores.values() if r.has_real_data]
    if has_data:
        avg_demand = sum(r.demand_score for r in has_data) / len(has_data)
        components.append(avg_demand * 100 * 0.35)
    else:
        components.append(50 * 0.35)
    if route_results:
        avg_route = sum(r.opportunity_score for r in route_results) / len(route_results)
        components.append(avg_route * 100 * 0.30)
    else:
        components.append(50 * 0.30)
    if insights:
        high_conv = sum(1 for i in insights if i.score >= 0.70) / max(len(insights), 1)
        components.append(high_conv * 100 * 0.20)
        bullish_ratio = sum(1 for i in insights if i.action in ("Prioritize", "Monitor")) / max(len(insights), 1)
        components.append(bullish_ratio * 100 * 0.15)
    else:
        components.append(30 * 0.20)
        components.append(40 * 0.15)
    score = sum(components)
    if score >= 80:   grade, color = "A", C_HIGH
    elif score >= 70: grade, color = "B", C_CYAN
    elif score >= 55: grade, color = "C", C_WARN
    elif score >= 40: grade, color = "D", "#fb923c"
    else:             grade, color = "F", C_DANGER
    return score, grade, color


def _render_health_scorecard(port_scores, route_results, insights):
    try:
        score, grade, color = _compute_health_score(port_scores, route_results, insights)
        has_data  = [r for r in port_scores.values() if r.has_real_data]
        avg_demand = sum(r.demand_score for r in has_data) / len(has_data) if has_data else 0.5
        avg_route  = sum(r.opportunity_score for r in route_results) / len(route_results) if route_results else 0.5
        high_conv  = sum(1 for i in insights if i.score >= 0.70)

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            number={"suffix": "", "font": {"color": color, "size": 36, "family": "monospace"}},
            title={"text": "Supply Chain Health", "font": {"color": C_TEXT2, "size": 12}},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": C_TEXT3, "tickfont": {"color": C_TEXT3, "size": 9},
                         "tickvals": [0, 25, 50, 75, 100]},
                "bar": {"color": color, "thickness": 0.28},
                "bgcolor": "#0d1525",
                "bordercolor": "rgba(255,255,255,0.06)",
                "steps": [
                    {"range": [0, 40],  "color": "rgba(239,68,68,0.12)"},
                    {"range": [40, 55], "color": "rgba(251,146,60,0.10)"},
                    {"range": [55, 70], "color": "rgba(245,158,11,0.10)"},
                    {"range": [70, 80], "color": "rgba(34,211,238,0.10)"},
                    {"range": [80, 100],"color": "rgba(16,185,129,0.12)"},
                ],
                "threshold": {"line": {"color": "rgba(255,255,255,0.5)", "width": 2}, "value": score},
            },
        ))
        fig_gauge.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", margin=dict(t=30, b=10, l=20, r=20),
            height=210, font={"color": C_TEXT},
        )

        col_gauge, col_grade, col_breakdown = st.columns([2, 1, 2])

        with col_gauge:
            st.plotly_chart(fig_gauge, use_container_width=True, key="ov_health_gauge")

        with col_grade:
            grade_glow = _rgba(color, 0.3)
            st.markdown(f"""
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                height:210px;background:linear-gradient(135deg,{_rgba(color,0.08)} 0%,transparent 100%);
                border:2px solid {_rgba(color,0.35)};border-radius:16px;
                box-shadow:0 0 32px {grade_glow}">
                <div style="font-size:5rem;font-weight:900;color:{color};line-height:1;
                            text-shadow:0 0 40px {grade_glow},0 0 80px {_rgba(color,0.2)};
                            font-family:monospace">{grade}</div>
                <div style="font-size:0.65rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                            letter-spacing:0.12em;margin-top:6px">Health Grade</div>
                <div style="font-size:0.82rem;font-weight:700;color:{color};margin-top:4px">{score:.0f} / 100</div>
            </div>
            """, unsafe_allow_html=True)

        with col_breakdown:
            def mini_bar(label, val, c):
                pct = int(val * 100)
                return f"""
                <div style="margin-bottom:12px">
                    <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                        <span style="font-size:0.72rem;color:{C_TEXT2}">{label}</span>
                        <span style="font-size:0.72rem;font-weight:700;color:{c}">{pct}%</span>
                    </div>
                    <div style="height:6px;border-radius:3px;background:rgba(255,255,255,0.06);overflow:hidden">
                        <div style="width:{pct}%;height:100%;background:{c};border-radius:3px;
                                    box-shadow:0 0 8px {_rgba(c,0.5)}"></div>
                    </div>
                </div>"""

            bullish_n   = sum(1 for i in insights if i.action in ("Prioritize", "Monitor"))
            bullish_pct = bullish_n / max(len(insights), 1)
            high_conv_pct = high_conv / max(len(insights), 1)

            st.markdown(f"""
            <div style="padding:20px 16px;height:210px;display:flex;flex-direction:column;
                        justify-content:center;background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px">
                <div style="font-size:0.7rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                            letter-spacing:0.08em;margin-bottom:16px">Health Components</div>
                {mini_bar("Port Demand", avg_demand, _score_color(avg_demand))}
                {mini_bar("Route Opportunity", avg_route, _score_color(avg_route))}
                {mini_bar("High-Conviction Signals", high_conv_pct, _score_color(high_conv_pct))}
                {mini_bar("Bullish Action Bias", bullish_pct, C_HIGH if bullish_pct > 0.5 else C_WARN)}
            </div>
            """, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Health scorecard unavailable: {exc}")


def _render_intelligence_summary(port_scores, route_results, insights):
    try:
        has_data = [r for r in port_scores.values() if r.has_real_data]
        bullets: list[tuple[str, str, str]] = []

        if has_data:
            high_demand = [r for r in has_data if r.demand_score >= 0.70]
            rising = [r for r in has_data if r.demand_trend == "Rising"]
            avg_d  = sum(r.demand_score for r in has_data) / len(has_data)
            bullets.append(("&#128679;", f"{len(high_demand)} of {len(has_data)} monitored ports are in <b>high demand</b> (score ≥ 70%)", C_HIGH if high_demand else C_TEXT2))
            if rising:
                bullets.append(("&#8593;", f"Demand <b>rising</b> at {len(rising)} port{'s' if len(rising)!=1 else ''}: " + ", ".join(r.port_name for r in rising[:4]), C_HIGH))
            bullets.append(("&#127760;", f"Global average port demand at <b>{avg_d:.0%}</b> — {'elevated' if avg_d > 0.6 else 'moderate' if avg_d > 0.45 else 'subdued'} market conditions", _score_color(avg_d)))
        else:
            bullets.append(("&#128679;", "Port demand data loading — add API credentials to enable live scoring", C_TEXT3))

        if route_results:
            strong        = [r for r in route_results if r.opportunity_label == "Strong"]
            rising_routes = [r for r in route_results if r.rate_pct_change_30d > 0.02]
            falling_routes= [r for r in route_results if r.rate_pct_change_30d < -0.02]
            rate_ct       = sum(1 for r in route_results if r.current_rate_usd_feu > 0)
            avg_r         = sum(r.current_rate_usd_feu for r in route_results if r.current_rate_usd_feu > 0) / rate_ct if rate_ct else 0
            if strong:
                bullets.append(("&#9650;", f"<b>{len(strong)}</b> route{'s' if len(strong)!=1 else ''} showing <b>strong</b> opportunity", C_HIGH))
            if rising_routes:
                bullets.append(("&#128200;", f"Freight trending <b>higher</b> on {len(rising_routes)} route{'s' if len(rising_routes)!=1 else ''}", C_HIGH))
            if falling_routes:
                bullets.append(("&#128201;", f"Freight softening on {len(falling_routes)} route{'s' if len(falling_routes)!=1 else ''}", C_WARN))
            if avg_r:
                bullets.append(("&#128184;", f"Average freight rate across live routes: <b>${avg_r:,.0f}/FEU</b>", C_ACCENT))

        if insights:
            conv      = [i for i in insights if i.category == "CONVERGENCE"]
            high_c    = [i for i in insights if i.score >= 0.70]
            prioritize= [i for i in insights if i.action == "Prioritize"]
            avoid     = [i for i in insights if i.action == "Avoid"]
            top_score = max(i.score for i in insights)
            bullets.append(("&#9889;", f"<b>{len(insights)}</b> active intelligence signals — <b>{len(high_c)}</b> high-conviction (score ≥ 70%)", C_ACCENT))
            if conv:
                bullets.append(("&#128302;", f"<b>{len(conv)}</b> convergence signal{'s' if len(conv)!=1 else ''} — multiple data streams aligned", C_PURPLE))
            if prioritize:
                bullets.append(("&#10003;", f"<b>{len(prioritize)}</b> market{'s' if len(prioritize)!=1 else ''} rated <b>Prioritize</b>", C_HIGH))
            if avoid:
                bullets.append(("&#10006;", f"<b>{len(avoid)}</b> market{'s' if len(avoid)!=1 else ''} rated <b>Avoid</b>", C_DANGER))
            bullets.append(("&#11088;", f"Top signal: <b>{top_score:.0%}</b> — {insights[0].title[:60]}{'...' if len(insights[0].title)>60 else ''}", _score_color(top_score)))

        bullets_html = ""
        for icon, text, color in bullets:
            bullets_html += f"""
            <div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;
                        border-bottom:1px solid rgba(255,255,255,0.04)">
                <span style="font-size:0.88rem;color:{color};flex-shrink:0;margin-top:1px">{icon}</span>
                <span style="font-size:0.82rem;color:{C_TEXT2};line-height:1.55">{text}</span>
            </div>"""

        st.markdown(f"""
        <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:18px 20px;margin-bottom:8px">
            <div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                        letter-spacing:0.08em;margin-bottom:14px">&#128203;&nbsp; Intelligence Summary</div>
            {bullets_html}
        </div>
        """, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Intelligence summary unavailable: {exc}")


def _build_globe(port_scores, route_results):
    try:
        DEMAND_CS = [
            [0.00, "#1e3a5f"], [0.25, "#3b82f6"],
            [0.50, "#10b981"], [0.75, "#f59e0b"], [1.00, "#ef4444"],
        ]
        fig = go.Figure()
        top_routes = sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)[:12]
        for route in top_routes:
            try:
                origin = PORTS_BY_LOCODE.get(route.origin_locode)
                dest   = PORTS_BY_LOCODE.get(route.dest_locode)
                if not origin or not dest: continue
                lats, lons = _arc_points(origin.lat, origin.lon, dest.lat, dest.lon, n=20)
                sc = route.opportunity_score
                arc_color = "rgba(16,185,129,0.70)" if sc > 0.65 else "rgba(245,158,11,0.55)" if sc > 0.45 else "rgba(55,65,81,0.45)"
                fig.add_trace(go.Scattergeo(
                    lat=lats, lon=lons, mode="lines",
                    line=dict(width=1.5+sc*2, color=arc_color),
                    hovertext=f"{route.route_name} — {sc:.0%}",
                    hoverinfo="text", showlegend=False, name=route.route_name,
                ))
            except Exception:
                continue

        g_lats, g_lons, g_sizes, g_colors = [], [], [], []
        m_lats, m_lons, m_sizes, m_scores, m_texts = [], [], [], [], []
        for port in PORTS:
            try:
                result = port_scores.get(port.locode)
                demand = result.demand_score if (result and result.has_real_data) else 0.35
                sz = 8 + demand * 12
                color = _demand_css_color(demand)
                if result and result.has_real_data:
                    top_prod = result.top_products[0]["category"] if result.top_products else "—"
                    hover = (f"<b>{port.name}</b> ({port.locode})<br>Region: {port.region}"
                             f"<br>Demand: {result.demand_label} ({demand:.0%})"
                             f"<br>Top product: {top_prod}<br>Vessels: {result.vessel_count}")
                else:
                    hover = f"<b>{port.name}</b> ({port.locode})<br>No live data yet"
                g_lats.append(port.lat); g_lons.append(port.lon)
                g_sizes.append(sz*1.8); g_colors.append(color)
                m_lats.append(port.lat); m_lons.append(port.lon)
                m_sizes.append(sz); m_scores.append(demand); m_texts.append(hover)
            except Exception:
                continue

        fig.add_trace(go.Scattergeo(
            lat=g_lats, lon=g_lons, mode="markers",
            marker=dict(size=g_sizes, color=g_colors, opacity=0.22, line=dict(width=0)),
            hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scattergeo(
            lat=m_lats, lon=m_lons, mode="markers",
            marker=dict(
                size=m_sizes, color=m_scores, colorscale=DEMAND_CS, cmin=0, cmax=1,
                opacity=0.92, line=dict(color="rgba(255,255,255,0.6)", width=0.8),
                colorbar=dict(
                    title=dict(text="Demand", font=dict(color=C_TEXT2, size=11)),
                    thickness=10, len=0.5, x=1.01,
                    tickfont=dict(color=C_TEXT2, size=10),
                    bgcolor="rgba(0,0,0,0)", bordercolor="rgba(255,255,255,0.1)",
                ),
            ),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=m_texts, showlegend=False, name="ports",
        ))
        fig.update_layout(
            paper_bgcolor=C_BG, height=500, margin=dict(l=0, r=0, t=0, b=0),
            geo=dict(
                projection_type="orthographic",
                showland=True, landcolor="#1a2235",
                showocean=True, oceancolor="#0a0f1a",
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.15)",
                showframe=False, bgcolor="#0a0f1a",
                showcountries=True, countrycolor="rgba(255,255,255,0.07)",
                showlakes=False, projection_rotation=dict(lon=60, lat=10, roll=0),
            ),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color=C_TEXT, size=12)),
        )
        return fig
    except Exception:
        return go.Figure()


def _render_region_chart(port_scores):
    try:
        region_data: dict[str, list[float]] = {}
        for port in PORTS:
            result = port_scores.get(port.locode)
            if result and result.has_real_data:
                region_data.setdefault(port.region, []).append(result.demand_score)
        if not region_data:
            st.info("Demand data loading — check API credentials.")
            return
        regions    = sorted(region_data.keys(), key=lambda r: sum(region_data[r])/len(region_data[r]))
        avg_scores = [sum(region_data[r])/len(region_data[r]) for r in regions]
        port_counts= [len(region_data[r]) for r in regions]
        fig = go.Figure(go.Bar(
            x=avg_scores, y=regions, orientation="h",
            marker=dict(
                color=avg_scores,
                colorscale=[[0,"#1e3a5f"],[0.25,"#3b82f6"],[0.5,"#10b981"],[0.75,"#f59e0b"],[1,"#ef4444"]],
                cmin=0, cmax=1, line=dict(color="rgba(255,255,255,0.15)", width=0.8),
            ),
            text=[f"  {s:.0%}  ({n} port{'s' if n>1 else ''})" for s, n in zip(avg_scores, port_counts)],
            textposition="outside", textfont=dict(color=C_TEXT2, size=11),
            hovertemplate="<b>%{y}</b><br>Avg demand: %{x:.0%}<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor=C_BG, plot_bgcolor="#111827", height=320,
            font=dict(color=C_TEXT, size=12),
            xaxis=dict(title="Avg Demand Score", range=[0,1.35], gridcolor="rgba(255,255,255,0.05)",
                       tickformat=".0%", tickfont=dict(color=C_TEXT3, size=10)),
            yaxis=dict(gridcolor="rgba(255,255,255,0.05)", tickfont=dict(color=C_TEXT2, size=11)),
            margin=dict(t=10, b=10, l=130, r=80),
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)", font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="ov_region_demand_chart")
    except Exception as exc:
        st.warning(f"Region chart unavailable: {exc}")


def _render_top_routes_cards(route_results):
    try:
        if not route_results:
            st.info("Route data loading...")
            return
        for route in route_results[:5]:
            score = route.opportunity_score
            lc = C_HIGH if route.opportunity_label == "Strong" else C_WARN if route.opportunity_label == "Moderate" else C_DANGER
            rate_str = f"${route.current_rate_usd_feu:,.0f}/FEU" if route.current_rate_usd_feu > 0 else "—"
            pct_str  = f"{route.rate_pct_change_30d*100:+.1f}%" if route.current_rate_usd_feu > 0 else ""
            pct_c    = C_HIGH if route.rate_pct_change_30d > 0 else C_DANGER if route.rate_pct_change_30d < -0.01 else C_TEXT2
            st.markdown(f"""
            <div style="background:linear-gradient(135deg,{_rgba(lc,0.07)} 0%,transparent 60%);
                        border:1px solid {_rgba(lc,0.3)};border-left:4px solid {lc};
                        border-radius:12px;padding:13px 16px;margin-bottom:8px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div style="font-size:0.88rem;font-weight:600;color:{C_TEXT};line-height:1.3">{route.route_name}</div>
                    <span style="background:{_rgba(lc,0.15)};color:{lc};border:1px solid {_rgba(lc,0.3)};
                                 padding:2px 10px;border-radius:999px;font-size:0.72rem;font-weight:800;
                                 white-space:nowrap;margin-left:8px">{int(score*100)}%</span>
                </div>
                <div style="display:flex;gap:12px;margin-top:6px;align-items:center;flex-wrap:wrap">
                    <span style="font-size:0.78rem;color:{C_TEXT2}">{rate_str}</span>
                    <span style="font-size:0.78rem;color:{pct_c};font-weight:600">{pct_str}</span>
                    <span style="font-size:0.75rem;color:{C_TEXT3}">{route.transit_days}d transit</span>
                    <span style="font-size:0.75rem;color:{C_TEXT3}">{route.origin_locode} &#8594; {route.dest_locode}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
    except Exception as exc:
        st.warning(f"Route cards unavailable: {exc}")


def _render_trade_flow_sankey(trade_data, route_results):
    try:
        REGION_COLORS = {
            "Asia East": "#3b82f6", "Europe": "#10b981", "North America": "#f59e0b",
            "Asia South": "#8b5cf6", "Middle East": "#06b6d4", "Africa": "#ec4899",
            "Latin America": "#f97316", "Oceania": "#84cc16",
        }
        FALLBACK_FLOWS = [
            ("Asia East", "North America", 480), ("Asia East", "Europe", 390),
            ("Europe", "North America", 180), ("Asia East", "Middle East", 120),
            ("Asia South", "Europe", 95), ("Asia South", "North America", 85),
            ("Middle East", "Asia East", 75), ("Latin America", "North America", 110),
            ("Africa", "Europe", 65), ("Asia East", "Latin America", 55),
        ]

        flows: dict[tuple, float] = {}
        use_real_data = False

        if trade_data and isinstance(trade_data, dict):
            for src, dst, val in trade_data.get("flows", []):
                flows[(src, dst)] = float(val)
            use_real_data = bool(flows)
        if not flows:
            for src, dst, val in FALLBACK_FLOWS:
                flows[(src, dst)] = float(val)

        all_regions: list[str] = []
        for src, dst in flows:
            if src not in all_regions: all_regions.append(src)
            if dst not in all_regions: all_regions.append(dst)

        region_idx  = {r: i for i, r in enumerate(all_regions)}
        node_colors = [REGION_COLORS.get(r, "#94a3b8") for r in all_regions]
        sources     = [region_idx[src] for src, dst in flows]
        targets     = [region_idx[dst] for src, dst in flows]
        values      = list(flows.values())

        link_colors = []
        for src, _ in flows:
            h = REGION_COLORS.get(src, "#94a3b8").lstrip("#")
            r2, g2, b2 = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
            link_colors.append(f"rgba({r2},{g2},{b2},0.22)")

        fig = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(pad=20, thickness=18,
                      line=dict(color="rgba(255,255,255,0.1)", width=0.5),
                      label=all_regions, color=node_colors),
            link=dict(source=sources, target=targets, value=values, color=link_colors),
        ))
        subtitle = "Estimated volumes (illustrative)" if not use_real_data else "USD billions"
        fig.update_layout(
            paper_bgcolor=C_BG, plot_bgcolor=C_BG, font=dict(color=C_TEXT, size=12),
            height=380, margin=dict(l=10, r=10, t=30, b=10),
            title=dict(text=subtitle, font=dict(size=11, color=C_TEXT2), x=0.01, y=0.98),
            showlegend=False,
            hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)", font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="ov_trade_flow_sankey")
    except Exception as exc:
        st.warning(f"Trade flow Sankey unavailable: {exc}")


def _render_chokepoint_section():
    try:
        from processing.risk_monitor import CHOKEPOINTS, get_color, get_high_risk_alerts, RISK_LEVELS

        def _risk_sort(rl):
            keys = list(RISK_LEVELS.keys())
            try: return keys.index(rl)
            except ValueError: return -1

        alerts = get_high_risk_alerts()
        if alerts:
            for alert in alerts:
                color = get_color(alert.risk_level)
                reroute_html = (
                    f'<span style="background:rgba(239,68,68,0.1);color:{C_DANGER};'
                    f'padding:2px 10px;border-radius:999px;font-size:0.72rem">+{alert.reroute_impact_days}d reroute</span>'
                    if alert.reroute_impact_days else ""
                )
                st.markdown(f"""
                <div style="background:{C_CARD};border:1px solid {C_BORDER};border-left:4px solid {color};
                            border-radius:10px;padding:14px 18px;margin-bottom:8px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                        <div style="font-size:0.95rem;font-weight:700;color:{C_TEXT}">&#9888;&nbsp;{alert.name}</div>
                        <div style="display:flex;gap:8px">
                            <span style="background:rgba(255,255,255,0.06);color:{color};
                                padding:2px 10px;border-radius:999px;font-size:0.72rem;font-weight:700">{alert.risk_level}</span>
                            <span style="background:rgba(255,255,255,0.06);color:{C_TEXT2};
                                padding:2px 10px;border-radius:999px;font-size:0.72rem">{alert.pct_world_trade:.0f}% of trade</span>
                            {reroute_html}
                        </div>
                    </div>
                    <div style="font-size:0.83rem;color:{C_TEXT2};line-height:1.5">{alert.risk_summary}</div>
                </div>
                """, unsafe_allow_html=True)

        col_map, col_table = st.columns([2, 1])
        with col_map:
            fig = go.Figure()
            for cp in CHOKEPOINTS:
                color = get_color(cp.risk_level)
                fig.add_trace(go.Scattergeo(
                    lat=[cp.lat], lon=[cp.lon], mode="markers+text",
                    marker=dict(size=max(10, 8+cp.pct_world_trade*0.8), color=color,
                                symbol="diamond", line=dict(color="white", width=1), opacity=0.9),
                    text=[cp.name.split(" ")[0]], textposition="top center",
                    textfont=dict(size=9, color="white"),
                    hovertemplate=f"<b>{cp.name}</b><br>Risk: {cp.risk_level}<br>Trade: {cp.pct_world_trade:.0f}%<extra></extra>",
                    showlegend=False, name=cp.name,
                ))
            fig.update_layout(
                template="plotly_dark", height=320,
                geo=dict(projection_type="natural earth",
                         showland=True, landcolor="rgb(35,40,50)",
                         showocean=True, oceancolor="rgb(15,25,40)",
                         showcountries=True, countrycolor="rgba(255,255,255,0.15)",
                         bgcolor="rgb(10,15,25)"),
                paper_bgcolor=C_BG, margin=dict(l=0, r=0, t=5, b=0),
                hoverlabel=dict(bgcolor=C_CARD, bordercolor="rgba(255,255,255,0.15)", font=dict(color=C_TEXT, size=12)),
            )
            st.plotly_chart(fig, use_container_width=True, key="ov_chokepoint_map")

        with col_table:
            table_rows = [{
                "Chokepoint": cp.name,
                "Risk":       cp.risk_level,
                "Trade %":    f"{cp.pct_world_trade:.0f}%",
                "Reroute":    f"+{cp.reroute_impact_days}d" if cp.reroute_impact_days > 0 else "N/A",
            } for cp in sorted(CHOKEPOINTS, key=lambda c: _risk_sort(c.risk_level), reverse=True)]
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True, height=300)
    except Exception as exc:
        st.warning(f"Chokepoint section unavailable: {exc}")


def _render_news_sentiment():
    try:
        from ui.styles import C_BG as S_BG, C_CARD as S_CARD, C_BORDER as S_BORDER
        from ui.styles import C_TEXT as S_TEXT, C_TEXT2 as S_TEXT2, C_HIGH as S_HIGH
        from ui.styles import _hex_to_rgba, section_header
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
                f'<div style="background:{S_CARD};border:1px solid {S_BORDER};border-radius:10px;'
                f'padding:16px 20px;color:{S_TEXT2};font-size:0.88rem;text-align:center">'
                f'&#8505;&nbsp; News feed unavailable — check network connection</div>',
                unsafe_allow_html=True,
            )
            return

        summary   = get_market_sentiment_summary(news)
        avg_score = summary["avg_sentiment"]
        label     = summary["sentiment_label"]
        bullish   = summary["bullish_count"]
        bearish   = summary["bearish_count"]
        top_kw    = summary["top_keywords"]

        badge_color = S_HIGH if label == "Bullish" else C_DANGER_L if label == "Bearish" else S_TEXT2
        badge_icon  = "&#128994;" if label == "Bullish" else "&#128308;" if label == "Bearish" else "&#9898;"
        badge_bg     = _hex_to_rgba(badge_color, 0.15)
        badge_border = _hex_to_rgba(badge_color, 0.35)
        kw_str = ", ".join(top_kw[:6]) if top_kw else "—"

        st.markdown(
            f'<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;'
            f'margin-bottom:16px;padding:14px 18px;background:{S_CARD};'
            f'border:1px solid {S_BORDER};border-radius:10px">'
            f'<span style="background:{badge_bg};color:{badge_color};border:1px solid {badge_border};'
            f'padding:4px 14px;border-radius:999px;font-size:0.82rem;font-weight:700">'
            f'{badge_icon}&nbsp;{label}</span>'
            f'<span style="color:{S_TEXT2};font-size:0.82rem">{len(news)} articles &nbsp;|&nbsp; '
            f'avg score <b style="color:{S_TEXT}">{avg_score:+.2f}</b> &nbsp;|&nbsp; '
            f'&#128994; {bullish} bullish &nbsp; &#128308; {bearish} bearish</span>'
            f'<span style="color:{C_TEXT3_L};font-size:0.78rem;margin-left:auto">Top signals: {kw_str}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for item in news[:6]:
            title_t = item.title[:80] + ("…" if len(item.title) > 80 else "")
            sc      = item.sentiment_score
            card_border = S_HIGH if sc > 0.2 else C_DANGER_L if sc < -0.2 else S_BORDER
            dot_color   = S_HIGH if sc > 0.1 else C_DANGER_L if sc < -0.1 else S_TEXT2
            rel_pct = int(item.relevance_score * 100)
            st.markdown(
                f'<div style="background:{S_CARD};border:1px solid {card_border};'
                f'border-radius:10px;padding:12px 16px;margin-bottom:7px">'
                f'<div style="display:flex;align-items:flex-start;gap:10px">'
                f'<span style="margin-top:3px;width:10px;height:10px;min-width:10px;border-radius:50%;'
                f'background:{dot_color};display:inline-block"></span>'
                f'<div style="flex:1;min-width:0">'
                f'<div style="font-size:0.88rem;font-weight:600;color:{S_TEXT};line-height:1.4;margin-bottom:6px">'
                f'<a href="{item.url}" target="_blank" style="color:{S_TEXT};text-decoration:none">{title_t}</a></div>'
                f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
                f'<span style="background:rgba(59,130,246,0.12);color:#3b82f6;border:1px solid rgba(59,130,246,0.3);'
                f'padding:1px 8px;border-radius:999px;font-size:0.7rem;font-weight:600">{item.source}</span>'
                f'<span style="color:{C_TEXT3_L};font-size:0.75rem">'
                f'{item.published_dt.strftime("%b %d") if item.published_dt else "—"}</span>'
                f'<span style="color:{dot_color};font-size:0.75rem;font-weight:600">{sc:+.1f}</span>'
                f'<div style="display:flex;align-items:center;gap:5px;margin-left:auto">'
                f'<span style="color:{C_TEXT3_L};font-size:0.72rem">relevance</span>'
                f'<div style="width:60px;height:5px;border-radius:3px;background:rgba(59,130,246,0.12);overflow:hidden">'
                f'<div style="width:{rel_pct}%;height:100%;background:rgba(59,130,246,0.7);border-radius:3px"></div>'
                f'</div><span style="color:{S_TEXT2};font-size:0.72rem">{rel_pct}%</span>'
                f'</div></div></div></div></div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        st.warning(f"News sentiment unavailable: {exc}")


def _render_summary_panel(port_scores, route_results, insights):
    try:
        has_data = [r for r in port_scores.values() if r.has_real_data]

        def stat_card(label, value, sub="", color=C_ACCENT):
            sub_html = f'<div style="font-size:0.78rem;color:{C_TEXT2}">{sub}</div>' if sub else ""
            return (
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-top:3px solid {color};border-radius:10px;padding:14px 16px;margin-bottom:8px">'
                f'<div style="font-size:0.68rem;font-weight:700;color:{C_TEXT3};'
                f'text-transform:uppercase;letter-spacing:0.07em">{label}</div>'
                f'<div style="font-size:1.7rem;font-weight:800;color:{C_TEXT};line-height:1.1;margin:4px 0">{value}</div>'
                f'{sub_html}</div>'
            )

        if has_data:
            avg_d    = sum(r.demand_score for r in has_data) / len(has_data)
            high_c   = sum(1 for r in has_data if r.demand_score >= 0.70)
            rising_c = sum(1 for r in has_data if r.demand_trend == "Rising")
            top_port = max(has_data, key=lambda r: r.demand_score)
            avg_color = C_HIGH if avg_d > 0.55 else C_WARN
            st.markdown(stat_card("Ports Tracked", str(len(port_scores)), f"{len(has_data)} with live data"), unsafe_allow_html=True)
            st.markdown(stat_card("Avg Global Demand", f"{avg_d:.0%}", f"{high_c} high · {rising_c} rising", avg_color), unsafe_allow_html=True)

            if insights:
                top = insights[0]
                cc  = {"CONVERGENCE": C_PURPLE, "ROUTE": C_ACCENT, "PORT_DEMAND": C_HIGH, "MACRO": C_CYAN}.get(top.category, C_ACCENT)
                ci  = {"CONVERGENCE": "&#9889;", "ROUTE": "&#128674;", "PORT_DEMAND": "&#128679;", "MACRO": "&#128200;"}.get(top.category, "&#128161;")
                title_t = top.title[:55] + ("..." if len(top.title) > 55 else "")
                st.markdown(f"""
                <div style="background:{C_CARD};border:1px solid {C_BORDER};border-left:3px solid {cc};
                            border-radius:10px;padding:14px 16px;margin-bottom:8px">
                    <div style="font-size:0.68rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                                letter-spacing:0.07em;margin-bottom:6px">Top Signal</div>
                    <div style="font-size:0.88rem;font-weight:600;color:{C_TEXT};line-height:1.3">{ci} {title_t}</div>
                    <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
                        <span style="background:rgba(255,255,255,0.06);color:{cc};padding:2px 10px;
                                     border-radius:999px;font-size:0.72rem;font-weight:700">{top.action}</span>
                        <span style="color:{C_TEXT2};font-size:0.78rem">{top.score:.0%} confidence</span>
                    </div>
                </div>""", unsafe_allow_html=True)

            st.markdown(f"""
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-left:3px solid {C_HIGH};
                        border-radius:10px;padding:14px 16px">
                <div style="font-size:0.68rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                            letter-spacing:0.07em;margin-bottom:4px">Highest Demand</div>
                <div style="font-size:1rem;font-weight:700;color:{C_TEXT}">{top_port.port_name}</div>
                <div style="font-size:0.82rem;color:{C_TEXT2};margin-top:2px">
                    {top_port.demand_score:.0%} · {top_port.demand_trend} · {top_port.region}
                </div>
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown(stat_card("Ports Tracked", str(len(port_scores)), "Loading demand data..."), unsafe_allow_html=True)
            st.info("Add API credentials in .env to enable demand scoring.", icon="ℹ️")
    except Exception as exc:
        st.warning(f"Summary panel unavailable: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RENDER — exact function signature preserved
# ══════════════════════════════════════════════════════════════════════════════

def render(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    trade_data: dict | None = None,
) -> None:
    """Render the Overview tab — full platform intelligence dashboard."""

    _inject_css()

    port_scores: dict[str, PortDemandResult] = {r.locode: r for r in port_results}

    # ── Cold-start fallback ───────────────────────────────────────────────────
    all_empty = not port_results and not route_results and not insights
    if all_empty:
        try:
            st.markdown(f"""
            <div class="ov-hero" style="
                background:linear-gradient(135deg,{C_CARD} 0%,#0f1d35 100%);
                border:1px solid rgba(59,130,246,0.35);border-left:4px solid {C_ACCENT};
                border-radius:14px;padding:32px;margin-bottom:24px;text-align:center">
                <div style="font-size:2.5rem;margin-bottom:14px">&#128674;</div>
                <div style="font-size:1.3rem;font-weight:800;color:{C_TEXT};margin-bottom:10px">
                    Welcome to Global Cargo Intelligence
                </div>
                <div style="font-size:0.9rem;color:{C_TEXT2};max-width:540px;margin:0 auto 24px auto;line-height:1.7">
                    No data has loaded yet — normal on first run or when API keys are not configured.
                    The dashboard populates automatically once data is available.
                </div>
                <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap">
                    <div style="background:rgba(59,130,246,0.10);border:1px solid rgba(59,130,246,0.3);
                                border-radius:8px;padding:10px 20px;font-size:0.82rem;color:{C_TEXT2}">
                        <b style="color:{C_ACCENT}">Step 1</b>&nbsp; Add API keys to <code>.env</code>
                    </div>
                    <div style="background:rgba(16,185,129,0.10);border:1px solid rgba(16,185,129,0.3);
                                border-radius:8px;padding:10px 20px;font-size:0.82rem;color:{C_TEXT2}">
                        <b style="color:{C_HIGH}">Step 2</b>&nbsp; Click <b>Refresh Data</b> in the sidebar
                    </div>
                    <div style="background:rgba(245,158,11,0.10);border:1px solid rgba(245,158,11,0.3);
                                border-radius:8px;padding:10px 20px;font-size:0.82rem;color:{C_TEXT2}">
                        <b style="color:{C_WARN}">Step 3</b>&nbsp; Data loads in ~30–60 s
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        except Exception:
            st.info("Dashboard loading — configure API credentials to enable live data.")

        try:
            col_globe, col_panel = st.columns([3, 1])
            with col_globe:
                st.markdown(
                    f'<div style="font-size:1rem;font-weight:700;color:{C_TEXT};margin-bottom:8px">'
                    '&#127760; Port Locations (demand data loading...)</div>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(_build_globe(port_scores, route_results), use_container_width=True, key="ov_globe_cold")
            with col_panel:
                _render_summary_panel(port_scores, route_results, insights)
        except Exception:
            pass

        try:
            st.divider()
            _render_chokepoint_section()
        except Exception:
            pass
        try:
            st.divider()
            _render_news_sentiment()
        except Exception:
            pass
        return

    # ═════════════════════════════════════════════════════════════════════════
    # FULL DASHBOARD — data available
    # ═════════════════════════════════════════════════════════════════════════

    # 1. Quick actions
    try:
        _render_quick_actions()
    except Exception:
        pass

    # 2. Hero banner with 7 KPI stats
    try:
        _render_hero(port_scores, route_results, insights)
    except Exception:
        pass

    # 3. Market pulse ticker strip
    try:
        _render_market_pulse(route_results)
    except Exception:
        pass

    # 4. Global shipping map (full-width)
    try:
        _render_shipping_map(port_scores, route_results)
    except Exception as exc:
        st.warning(f"Shipping map unavailable: {exc}")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # 5. Top signals dashboard — 6-card grid
    try:
        _render_top_signals(insights)
    except Exception as exc:
        st.warning(f"Signals dashboard error: {exc}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # 6. Market summary cards — 4 columns
    try:
        _render_market_summary(port_scores, route_results, insights)
    except Exception as exc:
        st.warning(f"Market summary error: {exc}")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # 7. Health scorecard
    try:
        st.markdown(
            f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">'
            f'&#127775;&nbsp; Supply Chain Health Scorecard</div>',
            unsafe_allow_html=True,
        )
        _render_health_scorecard(port_scores, route_results, insights)
    except Exception:
        pass

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # 8. Intelligence summary bullets
    try:
        _render_intelligence_summary(port_scores, route_results, insights)
    except Exception:
        pass

    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:24px 0'>", unsafe_allow_html=True)

    # 9. Recent alerts panel
    try:
        _render_alerts_panel(insights)
    except Exception as exc:
        st.warning(f"Alerts panel error: {exc}")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # 10. Globe + summary panel
    try:
        col_globe, col_panel = st.columns([3, 1])
        with col_globe:
            st.markdown(
                f'<div style="font-size:1rem;font-weight:700;color:{C_TEXT};margin-bottom:8px">'
                '&#127760; Live Port Intelligence Globe (orthographic)</div>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(_build_globe(port_scores, route_results), use_container_width=True, key="ov_globe_main")
        with col_panel:
            _render_summary_panel(port_scores, route_results, insights)
    except Exception as exc:
        st.warning(f"Globe unavailable: {exc}")

    st.divider()

    # 11. Region demand chart + top routes
    try:
        col_regions, col_routes = st.columns(2)
        with col_regions:
            st.markdown(
                f'<div style="font-size:1rem;font-weight:700;color:{C_TEXT};margin-bottom:8px">Demand by Region</div>',
                unsafe_allow_html=True,
            )
            _render_region_chart(port_scores)
        with col_routes:
            st.markdown(
                f'<div style="font-size:1rem;font-weight:700;color:{C_TEXT};margin-bottom:8px">Top Route Opportunities</div>',
                unsafe_allow_html=True,
            )
            _render_top_routes_cards(route_results)
    except Exception as exc:
        st.warning(f"Region/routes section error: {exc}")

    st.divider()

    # 12. Trade flow Sankey
    try:
        from ui.styles import section_header
        section_header("Trade Flow by Region", "Regional trade flows derived from live data or illustrative estimates")
        _render_trade_flow_sankey(trade_data, route_results)
    except Exception as exc:
        st.warning(f"Trade flow Sankey error: {exc}")

    st.divider()

    # 13. Chokepoint risk monitor
    try:
        st.markdown(
            f'<div style="font-size:1rem;font-weight:700;color:{C_TEXT};margin-bottom:8px">Chokepoint Risk Monitor</div>',
            unsafe_allow_html=True,
        )
        _render_chokepoint_section()
    except Exception as exc:
        st.warning(f"Chokepoints unavailable: {exc}")

    st.divider()

    # 14. News sentiment
    try:
        _render_news_sentiment()
    except Exception as exc:
        st.warning(f"News sentiment unavailable: {exc}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # 15. Platform stats footer
    try:
        _render_platform_footer(port_scores, route_results, insights)
    except Exception:
        pass
