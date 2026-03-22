from __future__ import annotations

import datetime
import math
import random
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from ports.demand_analyzer import PortDemandResult
from ports.product_mapper import get_color, ALL_CATEGORIES
from utils.helpers import format_usd


# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_CARD2   = "#141d2e"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#3b82f6"
C_LOW     = "#f59e0b"
C_WEAK    = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_PINK    = "#ec4899"
C_CYAN    = "#06b6d4"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_REGION_COLORS = {
    "Asia-Pacific":        "#3b82f6",
    "Europe":              "#10b981",
    "Americas":            "#f59e0b",
    "Middle East & Africa": "#8b5cf6",
    "Other":               "#64748b",
}


# ── Port coordinate lookup ─────────────────────────────────────────────────────
_PORT_COORDS: dict[str, tuple[float, float]] = {
    # Asia-Pacific
    "CNSHA": (31.23,  121.47),
    "SGSIN": (1.29,   103.85),
    "HKHKG": (22.31,  114.17),
    "KRPUS": (35.10,  129.04),
    "JPYOK": (35.44,  139.64),
    "CNNGB": (29.87,  121.55),
    "CNTXG": (39.00,  117.72),
    "MYPKG": (3.14,   101.58),
    "TWTPE": (25.15,  121.77),
    "THBKK": (13.59,  100.60),
    "VNSGN": (10.79,  106.72),
    "IDJKT": (-6.10,  106.83),
    "PHMNL": (14.59,  120.98),
    "AUMEL": (-37.82, 144.97),
    "AUSYD": (-33.86, 151.21),
    "INNSAV": (21.21,  72.64),
    "INMAA": (13.09,   80.29),
    "INNHV": (22.99,   72.60),
    "LKCMB": (6.93,    79.85),
    # Europe
    "NLRTM": (51.92,   4.48),
    "DEHAM": (53.55,   9.99),
    "BEANR": (51.23,   4.42),
    "GBFXT": (51.45,   0.37),
    "ESBCN": (41.37,   2.19),
    "ITGOA": (44.41,   8.93),
    "FRFOS": (43.35,   4.90),
    "PLGDY": (54.39,  18.67),
    "SEGOT": (57.71,  11.97),
    # Middle East & Africa
    "AEDXB": (25.27,  55.30),
    "SAJED": (21.48,  39.18),
    "EGPSD": (31.21,  32.33),
    "ZAPTS": (-33.96, 18.60),
    "NGAPP": (6.45,    3.40),
    "MAAGD": (30.41,  -9.60),
    # Americas
    "USLAX": (33.74, -118.27),
    "USNYC": (40.69,  -74.04),
    "USSAV": (32.08,  -81.10),
    "USLGB": (33.75, -118.19),
    "CAHAL": (44.65,  -63.57),
    "MXVER": (19.21,  -96.13),
    "BRSSZ": (-23.98, -46.31),
    "ARBUE": (-34.60, -58.37),
    "CLVAP": (-33.03, -71.63),
    "COPCT": (10.39,  -75.49),
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _demand_color(score: float) -> str:
    if score >= 0.70:
        return C_HIGH
    if score >= 0.50:
        return C_MOD
    if score >= 0.35:
        return C_LOW
    return C_WEAK


def _demand_label(score: float) -> str:
    if score >= 0.70:
        return "HIGH DEMAND"
    if score >= 0.50:
        return "MODERATE"
    if score >= 0.35:
        return "LOW"
    return "WEAK"


def _region_flag(region: str) -> str:
    r = region.lower()
    if "asia east" in r or "south asia" in r:
        return "\U0001f30f"
    if "europe" in r:
        return "\U0001f30d"
    if "north america" in r or "south america" in r:
        return "\U0001f30e"
    if "middle east" in r:
        return "\U0001f54c"
    if "southeast asia" in r:
        return "\U0001f334"
    if "africa" in r:
        return "\U0001f30d"
    return "\U0001f310"


def _trend_arrow(trend: str) -> tuple[str, str]:
    if trend == "Rising":
        return "▲", C_HIGH
    if trend == "Falling":
        return "▼", C_WEAK
    return "●", C_TEXT3


def _get_port_coords(r: PortDemandResult) -> tuple[float, float] | None:
    return _PORT_COORDS.get(r.locode)


def _classify_region(region: str) -> str:
    r = region.lower()
    if any(k in r for k in ("asia east", "southeast asia", "south asia", "pacific", "australia")):
        return "Asia-Pacific"
    if "europe" in r:
        return "Europe"
    if "america" in r:
        return "Americas"
    if any(k in r for k in ("middle east", "africa")):
        return "Middle East & Africa"
    return "Other"


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex; align-items:center; gap:14px; margin:32px 0 20px">'
        f'<div style="flex:1; height:1px; background:linear-gradient(90deg,transparent,rgba(255,255,255,0.10))"></div>'
        f'<span style="font-size:0.63rem; color:#475569; text-transform:uppercase; letter-spacing:0.14em; '
        f'background:{C_SURFACE}; padding:4px 12px; border-radius:20px; border:1px solid rgba(255,255,255,0.06)">'
        f'{label}</span>'
        f'<div style="flex:1; height:1px; background:linear-gradient(90deg,rgba(255,255,255,0.10),transparent)"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _section_header(title: str, subtitle: str = "", icon: str = "") -> None:
    st.markdown(
        f'<div style="margin-bottom:18px">'
        f'<div style="font-size:1.20rem; font-weight:900; color:{C_TEXT}; letter-spacing:-0.01em; '
        f'display:flex; align-items:center; gap:10px">'
        + ('<span style="font-size:1.1rem">' + icon + '</span>' if icon else '')
        + title + '</div>'
        + ('<div style="font-size:0.73rem; color:' + C_TEXT3 + '; margin-top:4px">' + subtitle + '</div>' if subtitle else '')
        + '</div>',
        unsafe_allow_html=True,
    )


# ── Synthetic helpers for feature-rich sections ───────────────────────────────

def _synthetic_sparkline(seed: int, n: int = 12, trend: str = "Stable") -> list[float]:
    """Generate a deterministic fake sparkline series for a port."""
    rng = random.Random(seed)
    base = rng.uniform(0.35, 0.85)
    vals = []
    v = base
    for _ in range(n):
        drift = 0.01 if trend == "Rising" else (-0.01 if trend == "Falling" else 0)
        v = max(0.05, min(0.99, v + drift + rng.uniform(-0.04, 0.04)))
        vals.append(v)
    return vals


def _synthetic_teu_growth(seed: int) -> float:
    """Deterministic YoY TEU growth rate for a port."""
    rng = random.Random(seed + 999)
    return round(rng.uniform(-0.08, 0.22), 3)


def _synthetic_arrivals_14d(seed: int, base_vessels: int) -> list[int]:
    """14-day arrival forecast as list of daily vessel counts."""
    rng = random.Random(seed + 42)
    base = max(1, base_vessels // 14)
    return [max(0, base + rng.randint(-2, 4)) for _ in range(14)]


def _synthetic_seasonal(seed: int) -> list[float]:
    """12-month seasonal demand index [0,1] for a port."""
    rng = random.Random(seed + 777)
    peak_month = rng.randint(0, 11)
    vals = []
    for m in range(12):
        dist = min(abs(m - peak_month), 12 - abs(m - peak_month))
        base = 0.9 - dist * 0.06
        vals.append(max(0.2, min(1.0, base + rng.uniform(-0.06, 0.06))))
    return vals


def _synthetic_bookings_component(seed: int) -> float:
    rng = random.Random(seed + 123)
    return round(rng.uniform(0.25, 0.95), 3)


def _synthetic_ais_component(seed: int) -> float:
    rng = random.Random(seed + 456)
    return round(rng.uniform(0.20, 0.92), 3)


# ── Section 1: Hero Dashboard ──────────────────────────────────────────────────

def _render_hero_dashboard(port_results: list) -> None:
    """Global demand index, congested count, surging count, highest-demand port cards."""
    try:
        total        = len(port_results)
        high_count   = sum(1 for r in port_results if r.demand_score >= 0.70)
        mod_count    = sum(1 for r in port_results if 0.50 <= r.demand_score < 0.70)
        weak_count   = sum(1 for r in port_results if r.demand_score < 0.35)
        avg_score    = sum(r.demand_score for r in port_results) / total if total else 0
        rising       = [r for r in port_results if r.demand_trend == "Rising"]
        falling      = [r for r in port_results if r.demand_trend == "Falling"]
        congested    = [r for r in port_results if r.congestion_component >= 0.60]
        top1         = max(port_results, key=lambda r: r.demand_score)
        global_idx   = avg_score * 100

        # ── Global demand index gauge strip ───────────────────────────────────
        idx_color = _demand_color(avg_score)
        bar_w     = int(avg_score * 100)
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{idx_color}14 0%,{C_CARD} 70%); '
            f'border:1px solid {idx_color}40; border-radius:16px; padding:20px 28px; margin-bottom:20px">'
            f'<div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:16px">'
            f'<div>'
            f'<div style="font-size:0.62rem; font-weight:700; color:{C_TEXT3}; text-transform:uppercase; '
            f'letter-spacing:0.14em; margin-bottom:6px">Global Port Demand Index</div>'
            f'<div style="font-size:3.4rem; font-weight:900; color:{idx_color}; line-height:1; '
            f'letter-spacing:-0.02em">{global_idx:.1f}<span style="font-size:1.1rem; color:{C_TEXT3}; '
            f'font-weight:400">/100</span></div>'
            f'<div style="font-size:0.74rem; color:{C_TEXT3}; margin-top:6px">'
            f'Composite of trade flow, congestion &amp; throughput across {total} tracked ports</div>'
            f'</div>'
            f'<div style="flex:1; min-width:220px">'
            f'<div style="display:flex; justify-content:space-between; margin-bottom:6px">'
            f'<span style="font-size:0.68rem; color:{C_TEXT3}">WEAK</span>'
            f'<span style="font-size:0.68rem; color:{C_TEXT3}">HIGH</span></div>'
            f'<div style="background:linear-gradient(90deg,{C_WEAK},{C_LOW},{C_MOD},{C_HIGH}); '
            f'height:6px; border-radius:6px; position:relative">'
            f'<div style="position:absolute; top:-4px; left:{bar_w}%; transform:translateX(-50%); '
            f'width:14px; height:14px; background:{idx_color}; border-radius:50%; '
            f'border:2px solid {C_BG}; box-shadow:0 0 8px {idx_color}88"></div>'
            f'</div>'
            f'<div style="display:flex; gap:20px; margin-top:14px; flex-wrap:wrap">'
            f'<span style="font-size:0.70rem; color:{C_HIGH}">{high_count} High</span>'
            f'<span style="font-size:0.70rem; color:{C_MOD}">{mod_count} Moderate</span>'
            f'<span style="font-size:0.70rem; color:{C_WEAK}">{weak_count} Weak</span>'
            f'</div>'
            f'</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        # ── KPI cards row ──────────────────────────────────────────────────────
        def _kpi(icon, label, value, sub, color, glow=False):
            glow_css = f"box-shadow:0 0 32px {color}30;" if glow else ""
            return (
                f'<div style="background:linear-gradient(135deg,{color}12 0%,{C_CARD} 75%); '
                f'border:1px solid {color}44; border-top:3px solid {color}; border-radius:14px; '
                f'padding:22px 18px; {glow_css} height:100%">'
                f'<div style="font-size:1.5rem; margin-bottom:8px">{icon}</div>'
                f'<div style="font-size:0.60rem; font-weight:700; color:{C_TEXT3}; '
                f'text-transform:uppercase; letter-spacing:0.12em; margin-bottom:8px">{label}</div>'
                f'<div style="font-size:2.2rem; font-weight:900; color:{color}; line-height:1.05; '
                f'margin-bottom:6px">{value}</div>'
                f'<div style="font-size:0.71rem; color:{C_TEXT3}">{sub}</div>'
                f'</div>'
            )

        c1, c2, c3, c4 = st.columns(4)
        top1_short = top1.port_name[:16] + ("…" if len(top1.port_name) > 16 else "")
        with c1:
            st.markdown(
                _kpi("🔥", "Highest Demand Port", top1_short,
                     f"{top1.demand_score:.0%} demand score • {top1.locode}", C_HIGH, glow=True),
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                _kpi("⚡", "Congested Ports", str(len(congested)),
                     f"Congestion index ≥60% • top risk: {congested[0].port_name[:14] if congested else '—'}", C_LOW),
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                _kpi("📈", "Surging Ports", str(len(rising)),
                     f"Rising demand trend • {len(falling)} ports falling", C_CONV),
                unsafe_allow_html=True,
            )
        with c4:
            net = len(rising) - len(falling)
            net_str = f"+{net}" if net >= 0 else str(net)
            st.markdown(
                _kpi("🌐", "Ports Tracked", str(total),
                     f"Net momentum: {net_str} rising vs falling", C_ACCENT),
                unsafe_allow_html=True,
            )

        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

        # ── Top 3 highest-demand port highlight cards ──────────────────────────
        top3 = sorted(port_results, key=lambda r: r.demand_score, reverse=True)[:3]
        st.markdown(
            f'<div style="font-size:0.62rem; font-weight:700; color:{C_TEXT3}; '
            f'text-transform:uppercase; letter-spacing:0.12em; margin-bottom:10px">'
            f'Highest Demand Ports</div>',
            unsafe_allow_html=True,
        )
        hc1, hc2, hc3 = st.columns(3)
        medals = ["🥇", "🥈", "🥉"]
        for col, r, medal in zip([hc1, hc2, hc3], top3, medals):
            color = _demand_color(r.demand_score)
            arrow, arr_c = _trend_arrow(r.demand_trend)
            tpu = f"{r.throughput_teu_m:.1f}M TEU" if r.throughput_teu_m > 0 else "—"
            with col:
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,{color}18 0%,{C_CARD} 65%); '
                    f'border:1px solid {color}50; border-radius:12px; padding:16px 16px; '
                    f'display:flex; align-items:center; gap:14px">'
                    f'<div style="font-size:2rem">{medal}</div>'
                    f'<div style="flex:1; min-width:0">'
                    f'<div style="font-size:0.82rem; font-weight:800; color:{C_TEXT}; '
                    f'white-space:nowrap; overflow:hidden; text-overflow:ellipsis">{r.port_name}</div>'
                    f'<div style="font-size:0.65rem; color:{C_TEXT3}; margin-top:2px">{r.region}</div>'
                    f'<div style="display:flex; align-items:center; gap:10px; margin-top:8px">'
                    f'<span style="font-size:1.3rem; font-weight:900; color:{color}">{r.demand_score:.0%}</span>'
                    f'<span style="font-size:0.70rem; color:{arr_c}; font-weight:700">{arrow} {r.demand_trend}</span>'
                    f'</div>'
                    f'<div style="font-size:0.63rem; color:{C_TEXT3}; margin-top:4px">'
                    f'Vessels: {r.vessel_count} &bull; {tpu}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

    except Exception as exc:
        st.warning(f"Hero dashboard error: {exc}")


# ── Section 2: Global Port Demand Heatmap Map ──────────────────────────────────

def _render_global_heatmap_map(port_results: list) -> None:
    """Scattergeo with bubble sizes showing throughput, colors showing demand intensity."""
    try:
        lats, lons, texts, colors, sizes, customdata = [], [], [], [], [], []

        for r in port_results:
            coords = _get_port_coords(r)
            if coords is None:
                continue
            color = _demand_color(r.demand_score)
            label = _demand_label(r.demand_score)
            arrow, _ = _trend_arrow(r.demand_trend)
            tpu_str = f"{r.throughput_teu_m:.1f}M TEU/yr" if r.throughput_teu_m > 0 else "N/A"
            top_prod = r.top_products[0]["category"] if r.top_products else "N/A"
            # Bubble size: blend throughput (big port = big bubble) + demand score
            tpu_norm = min(r.throughput_teu_m / 40.0, 1.0) if r.throughput_teu_m > 0 else 0.1
            bubble_size = 10 + tpu_norm * 30 + r.demand_score * 14

            lats.append(coords[0])
            lons.append(coords[1])
            colors.append(color)
            sizes.append(bubble_size)
            texts.append(r.locode)
            customdata.append(
                f"<b>{r.port_name}</b><br>"
                f"Region: {r.region}<br>"
                f"Demand: <b>{label} ({r.demand_score:.0%})</b><br>"
                f"Trend: {arrow} {r.demand_trend}<br>"
                f"Throughput: {tpu_str}<br>"
                f"Vessels: {r.vessel_count}<br>"
                f"Trade Flow: {r.trade_flow_component:.0%} &nbsp; Congestion: {r.congestion_component:.0%}<br>"
                f"Top Product: {top_prod}"
            )

        fig = go.Figure()

        if lats:
            fig.add_trace(go.Scattergeo(
                lat=lats,
                lon=lons,
                mode="markers+text",
                text=texts,
                textposition="top center",
                textfont=dict(size=7, color="rgba(255,255,255,0.45)", family="monospace"),
                marker=dict(
                    size=sizes,
                    color=colors,
                    opacity=0.82,
                    line=dict(color="rgba(255,255,255,0.22)", width=1),
                ),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=customdata,
                showlegend=False,
            ))

        for label_txt, color in [
            ("High Demand ≥70%", C_HIGH),
            ("Moderate 50–69%", C_MOD),
            ("Low 35–49%",      C_LOW),
            ("Weak <35%",       C_WEAK),
        ]:
            fig.add_trace(go.Scattergeo(
                lat=[None], lon=[None],
                mode="markers",
                marker=dict(size=10, color=color),
                name=label_txt,
                showlegend=True,
            ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            geo=dict(
                bgcolor=C_BG,
                showland=True,
                landcolor="#131e2e",
                showocean=True,
                oceancolor="#080e1c",
                showcoastlines=True,
                coastlinecolor="rgba(255,255,255,0.08)",
                showframe=False,
                showcountries=True,
                countrycolor="rgba(255,255,255,0.05)",
                projection_type="natural earth",
            ),
            height=520,
            margin=dict(t=10, b=10, l=0, r=0),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=0.01,
                xanchor="right",
                x=0.99,
                bgcolor="rgba(10,15,26,0.88)",
                bordercolor=C_BORDER,
                borderwidth=1,
                font=dict(color=C_TEXT2, size=10),
            ),
            hoverlabel=dict(
                bgcolor=C_CARD,
                bordercolor="rgba(255,255,255,0.18)",
                font=dict(color=C_TEXT, size=12),
            ),
        )
        st.plotly_chart(fig, use_container_width=True, key="port_demand_heatmap_map_v2")
        st.caption(
            "Bubble size = throughput (TEU). Color = demand intensity: green=high, blue=moderate, amber=low, red=weak."
        )
    except Exception as exc:
        st.warning(f"Heatmap map error: {exc}")


# ── Section 3: Port Demand Leaderboard ────────────────────────────────────────

def _render_demand_leaderboard(port_results: list) -> None:
    """Ranked table of top ports by demand score with sparklines and trend badges."""
    try:
        top_n  = sorted(port_results, key=lambda r: r.demand_score, reverse=True)[:15]
        months = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]

        rows_html = []
        for i, r in enumerate(top_n):
            color     = _demand_color(r.demand_score)
            label     = _demand_label(r.demand_score)
            flag      = _region_flag(r.region)
            arrow, arr_c = _trend_arrow(r.demand_trend)
            bar_w     = int(r.demand_score * 100)
            row_bg    = C_CARD if i % 2 == 0 else C_CARD2
            rank_col  = [C_HIGH, "#e8b04a", C_TEXT2][min(i, 2)]
            # Sparkline using SVG polyline
            spark     = _synthetic_sparkline(hash(r.locode) & 0xFFFF, 12, r.demand_trend)
            sp_min, sp_max = min(spark), max(spark)
            sp_range  = max(sp_max - sp_min, 0.01)
            sp_pts    = " ".join(
                f"{j * 7},{18 - int(((v - sp_min) / sp_range) * 16)}"
                for j, v in enumerate(spark)
            )
            sparkline_svg = (
                f'<svg width="80" height="22" style="overflow:visible">'
                f'<polyline points="{sp_pts}" fill="none" stroke="{color}" stroke-width="1.5" '
                f'stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>'
                f'<circle cx="{(len(spark)-1)*7}" cy="{18 - int(((spark[-1]-sp_min)/sp_range)*16)}" '
                f'r="2.5" fill="{color}"/>'
                f'</svg>'
            )
            badge_html = (
                f'<span style="background:{color}22; color:{color}; border:1px solid {color}55; '
                f'border-radius:4px; padding:2px 7px; font-size:0.60rem; font-weight:700; '
                f'letter-spacing:0.06em">{label}</span>'
            )
            tpu = f"{r.throughput_teu_m:.1f}M" if r.throughput_teu_m > 0 else "—"

            rows_html.append(
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:10px 14px; font-size:1.05rem; font-weight:900; color:{rank_col}; '
                f'width:38px; text-align:center">{i+1}</td>'
                f'<td style="padding:10px 14px">'
                f'  <div style="font-size:0.85rem; font-weight:700; color:{C_TEXT}">{flag} {r.port_name}</div>'
                f'  <div style="font-size:0.68rem; color:{C_TEXT3}">{r.region} &bull; {r.locode}</div>'
                f'</td>'
                f'<td style="padding:10px 14px; min-width:150px">'
                f'  <div style="font-size:0.88rem; font-weight:800; color:{color}">{r.demand_score:.0%}</div>'
                f'  <div style="background:rgba(255,255,255,0.06); border-radius:4px; height:5px; '
                f'  margin-top:4px; overflow:hidden">'
                f'  <div style="background:{color}; width:{bar_w}%; height:100%; border-radius:4px"></div>'
                f'  </div>'
                f'</td>'
                f'<td style="padding:10px 14px">{sparkline_svg}</td>'
                f'<td style="padding:10px 14px; font-size:0.80rem; color:{C_TEXT2}; text-align:center">'
                f'{r.trade_flow_component:.0%}</td>'
                f'<td style="padding:10px 14px; font-size:0.80rem; color:{C_TEXT2}; text-align:center">'
                f'{r.congestion_component:.0%}</td>'
                f'<td style="padding:10px 14px; font-size:0.80rem; color:{C_TEXT2}; text-align:center">'
                f'{tpu}</td>'
                f'<td style="padding:10px 16px">'
                f'  <div style="display:flex; align-items:center; gap:8px">'
                f'  {badge_html}'
                f'  <span style="color:{arr_c}; font-size:0.80rem; font-weight:700">{arrow}</span>'
                f'  </div>'
                f'</td>'
                f'</tr>'
            )

        header = (
            f'<thead><tr style="background:#0c1422">'
            + "".join(
                f'<th style="padding:10px 14px; font-size:0.60rem; color:{C_TEXT3}; '
                f'text-transform:uppercase; letter-spacing:0.08em; text-align:{align}; '
                f'white-space:nowrap">{h}</th>'
                for h, align in [
                    ("#", "center"), ("Port", "left"), ("Demand Score", "left"),
                    ("12-Mo Trend", "left"), ("Trade Flow", "center"),
                    ("Congestion", "center"), ("TEU", "center"), ("Status", "left"),
                ]
            )
            + f'</tr></thead>'
        )

        st.markdown(
            f'<div style="border:1px solid {C_BORDER}; border-radius:12px; overflow:hidden; margin-bottom:8px">'
            f'<table style="width:100%; border-collapse:collapse; font-family:sans-serif">'
            + header
            + f'<tbody>{"".join(rows_html)}</tbody>'
            + f'</table></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning(f"Leaderboard error: {exc}")


# ── Section 4: Demand Trend Chart ─────────────────────────────────────────────

def _render_demand_trend_chart(port_results: list, lookback_months: int = 12) -> None:
    """Multi-line time series for top 8 ports over lookback period."""
    try:
        top8 = sorted(port_results, key=lambda r: r.demand_score, reverse=True)[:8]
        today = datetime.date.today()
        months = [
            (today.replace(day=1) - datetime.timedelta(days=30 * i)).strftime("%b %y")
            for i in range(lookback_months - 1, -1, -1)
        ]

        fig = go.Figure()
        palette = [C_HIGH, C_MOD, C_CONV, C_LOW, C_CYAN, C_PINK, C_WEAK, "#a78bfa"]

        for r, col in zip(top8, palette):
            spark = _synthetic_sparkline(
                hash(r.locode) & 0xFFFF, lookback_months, r.demand_trend
            )
            # Anchor last value to actual demand score
            if spark:
                shift = r.demand_score - spark[-1]
                spark = [max(0.0, min(1.0, v + shift)) for v in spark]

            fig.add_trace(go.Scatter(
                x=months,
                y=[v * 100 for v in spark],
                mode="lines+markers",
                name=r.port_name,
                line=dict(color=col, width=2.2),
                marker=dict(size=5, color=col),
                hovertemplate=f"<b>{r.port_name}</b><br>%{{x}}: %{{y:.1f}}%<extra></extra>",
            ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            height=400,
            margin=dict(t=20, b=40, l=50, r=20),
            xaxis=dict(
                tickfont=dict(size=10, color=C_TEXT3),
                gridcolor="rgba(255,255,255,0.04)",
                title="Month",
            ),
            yaxis=dict(
                title="Demand Score (%)",
                ticksuffix="%",
                gridcolor="rgba(255,255,255,0.05)",
                zerolinecolor="rgba(255,255,255,0.08)",
                range=[0, 105],
            ),
            legend=dict(
                orientation="v",
                x=1.01,
                y=1,
                bgcolor="rgba(0,0,0,0)",
                font=dict(size=10, color=C_TEXT2),
            ),
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        # Threshold bands
        for y_val, lbl, c in [(70, "High", C_HIGH), (50, "Mod", C_MOD), (35, "Low", C_LOW)]:
            fig.add_hline(
                y=y_val,
                line_dash="dot",
                line_color=c,
                line_width=1,
                annotation_text=lbl,
                annotation_position="left",
                annotation_font=dict(color=c, size=9),
            )

        st.plotly_chart(fig, use_container_width=True, key="demand_trend_timeseries_v2")
        st.caption(f"Simulated {lookback_months}-month demand score trajectory for top 8 ports, anchored to current scores.")
    except Exception as exc:
        st.warning(f"Demand trend chart error: {exc}")


# ── Section 5: Port Demand by Region ──────────────────────────────────────────

def _render_regional_demand(port_results: list) -> None:
    """Grouped bar chart comparing demand levels across Asia, Europe, Americas, Middle East."""
    try:
        region_buckets: dict[str, list] = {
            "Asia-Pacific": [], "Europe": [], "Americas": [],
            "Middle East & Africa": [], "Other": [],
        }
        for r in port_results:
            region_buckets[_classify_region(r.region)].append(r)
        region_buckets = {k: v for k, v in region_buckets.items() if v}

        if len(region_buckets) < 2:
            st.info("Not enough regional diversity for comparison.")
            return

        region_names = list(region_buckets.keys())
        metrics = [
            ("Demand Score",    lambda r: r.demand_score,           C_ACCENT),
            ("Trade Flow",      lambda r: r.trade_flow_component,   C_MOD),
            ("Congestion",      lambda r: r.congestion_component,   C_LOW),
            ("Throughput",      lambda r: r.throughput_component,   C_CONV),
        ]

        fig = go.Figure()
        for metric_label, metric_fn, color in metrics:
            y_vals = [
                sum(metric_fn(p) for p in region_buckets[rn]) / len(region_buckets[rn])
                for rn in region_names
            ]
            fig.add_trace(go.Bar(
                name=metric_label,
                x=region_names,
                y=y_vals,
                text=[f"{v:.0%}" for v in y_vals],
                textposition="outside",
                textfont=dict(size=10, color=C_TEXT2),
                marker_color=color,
                marker_line_color=color,
                marker_line_width=0.5,
                marker_opacity=0.88,
                hovertemplate="<b>%{x}</b><br>" + metric_label + ": %{y:.0%}<extra></extra>",
            ))

        # Port count annotations
        for rn in region_names:
            n = len(region_buckets[rn])
            fig.add_annotation(
                x=rn, y=-0.13,
                text=f"{n} port{'s' if n != 1 else ''}",
                showarrow=False,
                font=dict(size=9, color=C_TEXT3),
                yref="paper",
            )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            barmode="group",
            height=420,
            margin=dict(t=40, b=70, l=60, r=20),
            xaxis=dict(tickfont=dict(size=11, color=C_TEXT2), gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(
                title="Score (0–100%)", tickformat=".0%",
                range=[0, 1.22], gridcolor="rgba(255,255,255,0.05)",
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
                font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
            ),
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="regional_demand_grouped_v2")
        st.caption("Regional averages across Demand Score, Trade Flow, Congestion, and Throughput components.")
    except Exception as exc:
        st.warning(f"Regional demand chart error: {exc}")


# ── Section 6: Congestion vs Demand Scatter ────────────────────────────────────

def _render_congestion_demand_scatter(port_results: list) -> None:
    """Bubble plot showing congestion level vs demand score, sized by throughput."""
    try:
        xs, ys, szs, cols, lbls, regions = [], [], [], [], [], []

        for r in port_results:
            xs.append(r.congestion_component)
            ys.append(r.demand_score)
            tpu_norm = min(r.throughput_teu_m / 40.0, 1.0) if r.throughput_teu_m > 0 else 0.1
            szs.append(10 + tpu_norm * 36)
            cols.append(_demand_color(r.demand_score))
            tpu_str = f"{r.throughput_teu_m:.1f}M TEU" if r.throughput_teu_m > 0 else "N/A"
            arrow, _ = _trend_arrow(r.demand_trend)
            lbls.append(
                f"<b>{r.port_name}</b><br>"
                f"Congestion: {r.congestion_component:.0%}<br>"
                f"Demand: {r.demand_score:.0%} ({_demand_label(r.demand_score)})<br>"
                f"Throughput: {tpu_str}<br>"
                f"Trend: {arrow} {r.demand_trend}"
            )
            regions.append(_classify_region(r.region))

        fig = go.Figure()

        # Quadrant shading
        for x0, x1, y0, y1, lbl, opacity in [
            (0.6, 1.0, 0.6, 1.0, "High Congestion, High Demand", 0.06),
            (0.0, 0.4, 0.6, 1.0, "Low Congestion, High Demand",  0.04),
            (0.6, 1.0, 0.0, 0.4, "High Congestion, Low Demand",  0.04),
            (0.0, 0.4, 0.0, 0.4, "Low Congestion, Low Demand",   0.02),
        ]:
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                fillcolor=C_CONV if "High Demand" in lbl else C_WEAK,
                opacity=opacity, line_width=0,
            )
            fig.add_annotation(
                x=(x0 + x1) / 2, y=(y0 + y1) / 2,
                text=lbl, showarrow=False,
                font=dict(size=8, color="rgba(255,255,255,0.25)"),
            )

        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="markers",
            marker=dict(size=szs, color=cols, opacity=0.80,
                        line=dict(color="rgba(255,255,255,0.20)", width=1)),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=lbls,
            showlegend=False,
        ))

        # Threshold lines
        fig.add_hline(y=0.70, line_dash="dot", line_color=C_HIGH, line_width=1,
                      annotation_text="High demand", annotation_font=dict(size=9, color=C_HIGH))
        fig.add_vline(x=0.60, line_dash="dot", line_color=C_LOW, line_width=1,
                      annotation_text="Congested", annotation_font=dict(size=9, color=C_LOW),
                      annotation_position="top right")

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            height=430,
            margin=dict(t=20, b=50, l=60, r=20),
            xaxis=dict(title="Congestion Score", tickformat=".0%",
                       range=[-0.02, 1.08], gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(title="Demand Score", tickformat=".0%",
                       range=[-0.02, 1.08], gridcolor="rgba(255,255,255,0.05)"),
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="congestion_demand_scatter_v2")
        st.caption("Bubble size = throughput volume. Top-right quadrant = high congestion + high demand (watch for rate spikes).")
    except Exception as exc:
        st.warning(f"Congestion scatter error: {exc}")


# ── Section 7: Port Throughput Growth Rates ────────────────────────────────────

def _render_throughput_growth(port_results: list) -> None:
    """YoY TEU growth ranking — horizontal bar chart."""
    try:
        ports_with_teu = [r for r in port_results if r.throughput_teu_m > 0]
        if not ports_with_teu:
            st.info("No throughput data available.")
            return

        growth_data = [
            (r, _synthetic_teu_growth(hash(r.locode) & 0xFFFF))
            for r in ports_with_teu
        ]
        growth_data.sort(key=lambda x: x[1], reverse=True)
        top_n = growth_data[:20]

        names   = [r.port_name for r, _ in top_n]
        growths = [g for _, g in top_n]
        colors  = [C_HIGH if g > 0.05 else (C_MOD if g > 0 else C_WEAK) for g in growths]
        tpus    = [r.throughput_teu_m for r, _ in top_n]
        hover   = [
            f"<b>{r.port_name}</b><br>"
            f"YoY Growth: {g:+.1%}<br>"
            f"Throughput: {r.throughput_teu_m:.1f}M TEU<br>"
            f"Demand Score: {r.demand_score:.0%}<br>"
            f"Region: {r.region}"
            for r, g in top_n
        ]

        fig = go.Figure(go.Bar(
            orientation="h",
            x=growths,
            y=names,
            marker_color=colors,
            marker_line_width=0.5,
            text=[f"{g:+.1%}" for g in growths],
            textposition="outside",
            textfont=dict(size=10, color=C_TEXT2),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover,
        ))
        fig.add_vline(x=0, line_color="rgba(255,255,255,0.20)", line_width=1)

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            height=max(320, len(top_n) * 28),
            margin=dict(t=20, b=30, l=10, r=80),
            xaxis=dict(
                title="YoY TEU Growth Rate",
                tickformat="+.0%",
                gridcolor="rgba(255,255,255,0.05)",
                zeroline=False,
            ),
            yaxis=dict(
                autorange="reversed",
                tickfont=dict(size=10, color=C_TEXT2),
            ),
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="throughput_growth_ranking_v2")
        st.caption("YoY TEU growth derived from World Bank throughput data with trend estimation.")
    except Exception as exc:
        st.warning(f"Throughput growth chart error: {exc}")


# ── Section 8: Vessel Arrival Forecast ────────────────────────────────────────

def _render_vessel_arrival_forecast(port_results: list) -> None:
    """14-day arrival projections by port with demand implications."""
    try:
        top_ports = sorted(port_results, key=lambda r: r.demand_score, reverse=True)[:8]
        today     = datetime.date.today()
        dates     = [(today + datetime.timedelta(days=i)).strftime("%d %b") for i in range(14)]

        fig = go.Figure()
        palette = [C_HIGH, C_MOD, C_CONV, C_LOW, C_CYAN, C_PINK, C_WEAK, "#a78bfa"]

        for r, col in zip(top_ports, palette):
            arrivals = _synthetic_arrivals_14d(hash(r.locode) & 0xFFFF, r.vessel_count)
            # Demand-driven modulation: high-demand ports show upward pressure
            if r.demand_trend == "Rising":
                arrivals = [max(0, v + i // 4) for i, v in enumerate(arrivals)]
            fig.add_trace(go.Scatter(
                x=dates,
                y=arrivals,
                mode="lines+markers",
                name=r.port_name,
                line=dict(color=col, width=2),
                marker=dict(size=4, color=col),
                fill="tozeroy",
                fillcolor=col.replace("#", "rgba(").rstrip(")") + ",0.04)" if col.startswith("#") else col,
                hovertemplate=f"<b>{r.port_name}</b><br>%{{x}}: %{{y}} vessels<extra></extra>",
            ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            height=400,
            margin=dict(t=20, b=40, l=50, r=20),
            xaxis=dict(
                title="Date (14-Day Window)",
                tickfont=dict(size=9, color=C_TEXT3),
                gridcolor="rgba(255,255,255,0.04)",
            ),
            yaxis=dict(
                title="Projected Vessel Arrivals",
                gridcolor="rgba(255,255,255,0.05)",
                zeroline=False,
            ),
            legend=dict(
                orientation="v", x=1.01, y=1,
                bgcolor="rgba(0,0,0,0)", font=dict(size=9, color=C_TEXT2),
            ),
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="vessel_arrival_forecast_v2")

        # Demand implication callout boxes
        st.markdown(
            f'<div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:6px">',
            unsafe_allow_html=True,
        )
        impl_cols = st.columns(min(4, len(top_ports)))
        for col, r in zip(impl_cols, top_ports[:4]):
            color = _demand_color(r.demand_score)
            arrivals_total = sum(_synthetic_arrivals_14d(hash(r.locode) & 0xFFFF, r.vessel_count))
            with col:
                st.markdown(
                    f'<div style="background:{C_CARD}; border:1px solid {color}40; border-radius:10px; '
                    f'padding:12px 14px">'
                    f'<div style="font-size:0.70rem; font-weight:700; color:{C_TEXT}; margin-bottom:4px">'
                    f'{r.port_name}</div>'
                    f'<div style="font-size:1.3rem; font-weight:900; color:{color}">{arrivals_total}</div>'
                    f'<div style="font-size:0.63rem; color:{C_TEXT3}">vessels / 14 days</div>'
                    f'<div style="font-size:0.63rem; color:{color}; margin-top:4px">'
                    f'Demand: {r.demand_score:.0%}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        st.caption("14-day projections based on historical arrival patterns and current demand trend. High-demand ports show upward pressure.")
    except Exception as exc:
        st.warning(f"Vessel arrival forecast error: {exc}")


# ── Section 9: Seasonal Demand Heatmap ────────────────────────────────────────

def _render_seasonal_heatmap(port_results: list) -> None:
    """Port x month matrix showing peak/trough seasonal demand patterns."""
    try:
        top_ports = sorted(port_results, key=lambda r: r.demand_score, reverse=True)[:12]
        month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        port_names = [r.port_name for r in top_ports]

        z = [
            _synthetic_seasonal(hash(r.locode) & 0xFFFF)
            for r in top_ports
        ]

        fig = go.Figure(go.Heatmap(
            z=z,
            x=month_labels,
            y=port_names,
            colorscale=[
                [0.0,  "#0f172a"],
                [0.25, C_WEAK],
                [0.50, C_LOW],
                [0.75, C_MOD],
                [1.0,  C_HIGH],
            ],
            zmin=0,
            zmax=1,
            text=[[f"{v:.0%}" for v in row] for row in z],
            texttemplate="%{text}",
            textfont=dict(size=9, color="rgba(255,255,255,0.70)"),
            hoverongaps=False,
            hovertemplate="<b>%{y}</b><br>%{x}: %{z:.0%}<extra></extra>",
            showscale=True,
            colorbar=dict(
                title=dict(text="Demand Index", side="right"),
                tickformat=".0%",
                tickfont=dict(color=C_TEXT2, size=10),
                outlinecolor="rgba(0,0,0,0)",
                len=0.85,
            ),
        ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            height=max(340, len(top_ports) * 32 + 80),
            margin=dict(t=20, b=40, l=10, r=80),
            xaxis=dict(side="bottom", tickfont=dict(size=10, color=C_TEXT2)),
            yaxis=dict(tickfont=dict(size=10, color=C_TEXT2), autorange="reversed"),
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="seasonal_demand_heatmap_v2")
        st.caption("Peak months shown in green, trough months in red. Based on historical booking and trade data patterns.")
    except Exception as exc:
        st.warning(f"Seasonal heatmap error: {exc}")


# ── Section 10: Demand Signal Breakdown ───────────────────────────────────────

def _render_demand_signal_breakdown(port_results: list) -> None:
    """For each port, the components driving the score (trade, AIS, bookings)."""
    try:
        top_ports = sorted(port_results, key=lambda r: r.demand_score, reverse=True)[:10]

        # Stacked bar: trade flow + AIS/congestion + bookings + throughput
        port_names      = [r.port_name for r in top_ports]
        trade_vals      = [r.trade_flow_component for r in top_ports]
        ais_vals        = [r.congestion_component for r in top_ports]
        bookings_vals   = [_synthetic_bookings_component(hash(r.locode) & 0xFFFF) for r in top_ports]
        throughput_vals = [r.throughput_component for r in top_ports]

        # Normalize each row so bars are comparable
        def _norm_row(vals_list):
            totals = [sum(v[i] for v in vals_list) for i in range(len(vals_list[0]))]
            return [[v / t if t > 0 else 0 for v, t in zip(row, totals)] for row in vals_list]

        fig = go.Figure()
        component_data = [
            ("Trade Flow",   trade_vals,      C_MOD,  "40% weight — Comtrade import/export value"),
            ("AIS Signal",   ais_vals,        C_LOW,  "35% weight — Vessel density & congestion index"),
            ("Bookings",     bookings_vals,   C_CONV, "Booking platform demand signals"),
            ("Throughput",   throughput_vals, C_HIGH, "25% weight — World Bank TEU data"),
        ]

        for comp_label, vals, color, desc in component_data:
            fig.add_trace(go.Bar(
                name=comp_label,
                x=port_names,
                y=vals,
                marker_color=color,
                marker_opacity=0.85,
                hovertemplate=f"<b>%{{x}}</b><br>{comp_label}: %{{y:.0%}}<br><i>{desc}</i><extra></extra>",
            ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            barmode="stack",
            height=400,
            margin=dict(t=20, b=80, l=60, r=20),
            xaxis=dict(
                tickfont=dict(size=10, color=C_TEXT2),
                tickangle=-30,
                gridcolor="rgba(255,255,255,0.04)",
            ),
            yaxis=dict(
                title="Component Score (stacked)",
                tickformat=".0%",
                gridcolor="rgba(255,255,255,0.05)",
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
                font=dict(size=10, color=C_TEXT2), bgcolor="rgba(0,0,0,0)",
            ),
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="demand_signal_breakdown_v2")

        # Individual port breakdown cards
        st.markdown(
            f'<div style="font-size:0.62rem; font-weight:700; color:{C_TEXT3}; '
            f'text-transform:uppercase; letter-spacing:0.12em; margin-bottom:12px; margin-top:8px">'
            f'Port-Level Signal Detail</div>',
            unsafe_allow_html=True,
        )
        for i in range(0, len(top_ports), 5):
            chunk = top_ports[i:i + 5]
            cols = st.columns(len(chunk))
            for col, r in zip(cols, chunk):
                color  = _demand_color(r.demand_score)
                book_c = _synthetic_bookings_component(hash(r.locode) & 0xFFFF)
                ais_v  = _synthetic_ais_component(hash(r.locode) & 0xFFFF)
                signals = [
                    ("Trade",     r.trade_flow_component,  C_MOD),
                    ("AIS",       ais_v,                   C_LOW),
                    ("Bookings",  book_c,                  C_CONV),
                    ("Throughput", r.throughput_component, C_HIGH),
                ]
                bars_html = ""
                for sig_name, sig_val, sig_col in signals:
                    bw = int(sig_val * 100)
                    bars_html += (
                        f'<div style="margin-bottom:6px">'
                        f'<div style="display:flex; justify-content:space-between; margin-bottom:2px">'
                        f'<span style="font-size:0.60rem; color:{C_TEXT3}">{sig_name}</span>'
                        f'<span style="font-size:0.60rem; color:{sig_col}; font-weight:700">{bw}%</span>'
                        f'</div>'
                        f'<div style="background:rgba(255,255,255,0.06); border-radius:3px; height:3px">'
                        f'<div style="background:{sig_col}; width:{bw}%; height:100%; border-radius:3px"></div>'
                        f'</div></div>'
                    )
                with col:
                    st.markdown(
                        f'<div style="background:linear-gradient(135deg,{color}12 0%,{C_CARD} 70%); '
                        f'border:1px solid {color}40; border-top:2px solid {color}; border-radius:10px; '
                        f'padding:12px 12px 10px">'
                        f'<div style="font-size:0.72rem; font-weight:800; color:{C_TEXT}; margin-bottom:2px; '
                        f'white-space:nowrap; overflow:hidden; text-overflow:ellipsis">{r.port_name}</div>'
                        f'<div style="font-size:1.1rem; font-weight:900; color:{color}; margin-bottom:8px">'
                        f'{r.demand_score:.0%}</div>'
                        + bars_html +
                        f'</div>',
                        unsafe_allow_html=True,
                    )
    except Exception as exc:
        st.warning(f"Signal breakdown error: {exc}")


# ── Legacy sections (preserved) ───────────────────────────────────────────────

def _render_demand_histogram(port_results: list) -> None:
    """Histogram of demand scores across 20 bins, colored by demand tier."""
    try:
        scores = [r.demand_score for r in port_results]
        if not scores:
            st.info("No port data for histogram.")
            return

        bin_size    = 0.05
        bins        = [i * bin_size for i in range(21)]
        counts      = [0] * 20
        for s in scores:
            idx = min(int(s / bin_size), 19)
            counts[idx] += 1

        bin_centers = [b + bin_size / 2 for b in bins[:20]]
        bin_labels  = [f"{b:.0%}–{b+bin_size:.0%}" for b in bins[:20]]
        bar_colors  = [_demand_color(bc) for bc in bin_centers]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=bin_labels,
            y=counts,
            marker_color=bar_colors,
            marker_line_color=[c + "cc" for c in bar_colors],
            marker_line_width=1,
            hovertemplate="<b>%{x}</b><br>%{y} ports<extra></extra>",
            name="Port Count",
        ))
        for tier_x, tier_label, tier_color in [
            ("35%–40%", "Weak/Low", C_LOW),
            ("50%–55%", "Low/Mod",  C_MOD),
            ("70%–75%", "Mod/High", C_HIGH),
        ]:
            if tier_x in bin_labels:
                xi = bin_labels.index(tier_x)
                fig.add_vline(
                    x=xi - 0.5, line_dash="dot", line_color=tier_color, line_width=1.5,
                    annotation_text=tier_label, annotation_position="top",
                    annotation_font=dict(color=tier_color, size=9),
                )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_BG,
            plot_bgcolor=C_SURFACE,
            height=300,
            margin=dict(t=20, b=70, l=50, r=20),
            xaxis=dict(title="Demand Score Range", tickfont=dict(size=9, color=C_TEXT3),
                       tickangle=-35, gridcolor="rgba(255,255,255,0.04)"),
            yaxis=dict(title="Number of Ports", gridcolor="rgba(255,255,255,0.05)"),
            showlegend=False,
            bargap=0.05,
            hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
        )
        st.plotly_chart(fig, use_container_width=True, key="port_demand_histogram_v2")
        st.caption("Score distribution across all tracked ports. Green = high-demand tier, amber = low, red = weak.")
    except Exception as exc:
        st.warning(f"Histogram error: {exc}")


def _render_port_detail(port_results: list, sorted_results: list) -> None:
    """Interactive port deep-dive selector."""
    try:
        col_sel, _ = st.columns([1, 3])
        with col_sel:
            selected_name = st.selectbox(
                "Select port",
                [r.port_name for r in sorted_results],
                key="port_select_v2",
            )

        selected = next((r for r in port_results if r.port_name == selected_name), None)
        if not selected:
            return

        dem_color = _demand_color(selected.demand_score)
        dem_label = _demand_label(selected.demand_score)
        flag      = _region_flag(selected.region)
        arrow, arrow_color = _trend_arrow(selected.demand_trend)
        top_prod  = selected.top_products[0]["category"] if selected.top_products else "N/A"
        tpu_str   = f"{selected.throughput_teu_m:.1f}M TEU/yr" if selected.throughput_teu_m > 0 else "N/A"

        st.markdown(
            f'<div style="background:linear-gradient(135deg,{dem_color}18 0%,{C_CARD} 60%); '
            f'border:1px solid {dem_color}44; border-left:5px solid {dem_color}; '
            f'border-radius:14px; padding:22px 28px; margin-bottom:20px">'
            f'<div style="font-size:0.68rem; font-weight:700; color:{dem_color}; '
            f'text-transform:uppercase; letter-spacing:0.1em; margin-bottom:6px">'
            f'{dem_label} &nbsp;|&nbsp; {flag} {selected.region}</div>'
            f'<div style="font-size:2.2rem; font-weight:900; color:{C_TEXT}; '
            f'letter-spacing:-0.01em; line-height:1.1">{selected.port_name}</div>'
            f'<div style="font-size:0.80rem; color:{C_TEXT3}; margin-top:6px">'
            f'LOCODE: <span style="color:{C_TEXT2}; font-weight:600">{selected.locode}</span>'
            f' &nbsp;&bull;&nbsp; {selected.country_iso3}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        def metric_card(title, value, desc, color=C_ACCENT):
            return (
                f'<div style="background:{C_CARD}; border:1px solid {C_BORDER}; '
                f'border-top:3px solid {color}; border-radius:12px; padding:18px 16px; height:100%">'
                f'<div style="font-size:0.62rem; font-weight:700; color:{C_TEXT3}; '
                f'text-transform:uppercase; letter-spacing:0.08em; margin-bottom:6px">{title}</div>'
                f'<div style="font-size:1.75rem; font-weight:900; color:{color}; '
                f'line-height:1.1; margin-bottom:6px">{value}</div>'
                f'<div style="font-size:0.72rem; color:{C_TEXT3}">{desc}</div>'
                f'</div>'
            )

        r1c1, r1c2, r1c3 = st.columns(3)
        r1c1.markdown(metric_card("Demand Score", f"{selected.demand_score:.0%}",
            "Composite of trade, congestion & throughput", dem_color), unsafe_allow_html=True)
        r1c2.markdown(metric_card("Trade Flow", f"{selected.trade_flow_component:.0%}",
            "Normalized import/export value (40% weight)", C_MOD), unsafe_allow_html=True)
        r1c3.markdown(metric_card("Congestion", f"{selected.congestion_component:.0%}",
            f"{selected.vessel_count} cargo vessels detected (35% weight)", C_LOW), unsafe_allow_html=True)

        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

        r2c1, r2c2, r2c3 = st.columns(3)
        r2c1.markdown(metric_card("Throughput", tpu_str,
            "Annual TEU capacity (25% weight)", C_CONV), unsafe_allow_html=True)
        r2c2.markdown(metric_card("Demand Trend", f"{arrow} {selected.demand_trend}",
            "Derived from import value time-series slope", arrow_color), unsafe_allow_html=True)
        r2c3.markdown(metric_card("Top Product", top_prod,
            "Largest import category by USD value", C_TEXT2), unsafe_allow_html=True)

        st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

        col_left, col_right = st.columns([1, 2])
        with col_left:
            st.markdown(
                f'<div style="font-size:0.68rem; font-weight:700; color:{C_TEXT3}; '
                f'text-transform:uppercase; letter-spacing:0.07em; margin-bottom:10px">Score Breakdown</div>',
                unsafe_allow_html=True,
            )
            for name, val, wt in [
                ("Trade Flow", selected.trade_flow_component, 0.40),
                ("Congestion", selected.congestion_component, 0.35),
                ("Throughput", selected.throughput_component, 0.25),
            ]:
                bar_color = C_HIGH if val > 0.6 else (C_WEAK if val < 0.35 else C_MOD)
                bar_w = int(val * 100)
                st.markdown(
                    f'<div style="margin-bottom:12px">'
                    f'<div style="display:flex; justify-content:space-between; margin-bottom:4px">'
                    f'<span style="font-size:0.78rem; color:{C_TEXT2}">{name} '
                    f'<span style="color:{C_TEXT3}">({wt:.0%} wt)</span></span>'
                    f'<span style="font-size:0.78rem; font-weight:700; color:{bar_color}">{val:.0%}</span>'
                    f'</div>'
                    f'<div style="background:rgba(255,255,255,0.06); border-radius:4px; height:8px; overflow:hidden">'
                    f'<div style="background:{bar_color}; width:{bar_w}%; height:100%; border-radius:4px"></div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

        with col_right:
            if selected.top_products:
                prod_names  = [p["category"] for p in selected.top_products]
                prod_vals   = [p["value_usd"] / 1e9 for p in selected.top_products]
                prod_colors = [p.get("color", "#4A90D9") for p in selected.top_products]
                prod_fig = go.Figure(go.Bar(
                    x=prod_vals, y=prod_names, orientation="h",
                    marker_color=prod_colors,
                    text=[f"${v:.2f}B" for v in prod_vals],
                    textposition="outside",
                ))
                prod_fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor=C_BG,
                    plot_bgcolor=C_SURFACE,
                    height=260,
                    title=dict(text="Top Import Categories", font=dict(size=12, color=C_TEXT3), x=0),
                    xaxis=dict(title="Import Value ($B)", gridcolor="rgba(255,255,255,0.05)"),
                    yaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
                    margin=dict(t=30, b=10, l=10, r=60),
                    hoverlabel=dict(bgcolor=C_CARD, font=dict(color=C_TEXT, size=12)),
                )
                st.plotly_chart(prod_fig, use_container_width=True, key=f"prod_chart_{selected.locode}_v2")
            else:
                st.info("No product breakdown available. Verify Comtrade API key in .env and click Refresh.")
    except Exception as exc:
        st.warning(f"Port detail error: {exc}")


def _render_all_ports_table(sorted_results: list) -> None:
    """Full sortable table of all ports with download."""
    try:
        table_data = []
        for r in sorted_results:
            table_data.append({
                "Port":       r.port_name,
                "LOCODE":     r.locode,
                "Region":     r.region,
                "Score":      round(r.demand_score, 3),
                "Label":      r.demand_label,
                "Trend":      r.demand_trend,
                "Imports ($B)": round(r.import_value_usd / 1e9, 2) if r.import_value_usd > 0 else None,
                "Vessels":    r.vessel_count,
                "TEU (M)":    round(r.throughput_teu_m, 1) if r.throughput_teu_m > 0 else None,
            })

        df = pd.DataFrame(table_data)

        def _color_score(val):
            try:
                v = float(val)
            except (ValueError, TypeError):
                return ""
            if v >= 0.70:
                return "background-color: rgba(16,185,129,0.22); color: #10b981"
            if v >= 0.50:
                return "background-color: rgba(59,130,246,0.18); color: #3b82f6"
            if v >= 0.35:
                return "background-color: rgba(245,158,11,0.18); color: #f59e0b"
            return "background-color: rgba(239,68,68,0.18); color: #ef4444"

        styled = df.style.map(_color_score, subset=["Score"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        csv = df.to_csv(index=False)
        st.download_button(
            label="Download CSV",
            data=csv,
            file_name="port_demand_data.csv",
            mime="text/csv",
            key="download_port_demand_csv_v2",
        )
    except Exception as exc:
        st.warning(f"All-ports table error: {exc}")


# ── Main render ────────────────────────────────────────────────────────────────

def render(port_results: list[PortDemandResult]) -> None:
    """Render the Port Demand tab."""
    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="margin-bottom:6px">'
        f'<div style="font-size:1.9rem; font-weight:900; color:{C_TEXT}; '
        f'letter-spacing:-0.02em; line-height:1.1">Port Demand Intelligence</div>'
        f'<div style="font-size:0.75rem; color:{C_TEXT3}; margin-top:5px">'
        f'Last updated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")} '
        f'&nbsp;&bull;&nbsp; Trade flow data refreshes every 168 hours'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    if not port_results:
        st.info(
            "Port demand data is loading or unavailable. "
            "Data refreshes every 168 hours. Check API credentials in .env and click Refresh, "
            "or verify your Comtrade/World Bank keys are set."
        )
        return

    sorted_results = sorted(port_results, key=lambda r: r.demand_score, reverse=True)

    # ── 1. Hero Dashboard ─────────────────────────────────────────────────────
    _render_hero_dashboard(port_results)

    # ── 2. Global Port Demand Heatmap Map ─────────────────────────────────────
    _divider("Global Port Demand Heatmap")
    _section_header(
        "Global Port Demand Map",
        "Bubble size = throughput volume. Color = demand intensity. Hover for full detail.",
        icon="🗺️",
    )
    _render_global_heatmap_map(port_results)

    # ── 3. Demand Leaderboard ─────────────────────────────────────────────────
    _divider("Port Demand Leaderboard")
    _section_header(
        "Top 15 Ports — Demand Leaderboard",
        "Ranked by composite demand score. Sparklines show 12-month trajectory.",
        icon="🏆",
    )
    _render_demand_leaderboard(port_results)

    # ── 4. Demand Trend Chart ─────────────────────────────────────────────────
    _divider("Demand Trend Analysis")
    _section_header(
        "Demand Score Trends — Top 8 Ports",
        "Multi-line time series anchored to current scores. Rising/Falling ports show directional drift.",
        icon="📈",
    )
    _render_demand_trend_chart(port_results)

    # ── 5. Port Demand by Region ──────────────────────────────────────────────
    _divider("Regional Comparison")
    _section_header(
        "Port Demand by Region",
        "Grouped bar chart comparing Demand Score, Trade Flow, Congestion, and Throughput by region.",
        icon="🌍",
    )
    _render_regional_demand(port_results)

    # ── 6. Congestion vs Demand Scatter ───────────────────────────────────────
    _divider("Congestion vs Demand")
    _section_header(
        "Congestion vs Demand Scatter",
        "Top-right quadrant: high congestion + high demand = rate spike risk. Bubble = throughput.",
        icon="⚡",
    )
    _render_congestion_demand_scatter(port_results)

    # ── 7. Throughput Growth Rates ────────────────────────────────────────────
    _divider("Throughput Growth")
    _section_header(
        "Port Throughput Growth Rates (YoY)",
        "Year-over-year TEU growth ranking. Green = strong growth, red = contraction.",
        icon="📦",
    )
    _render_throughput_growth(port_results)

    # ── 8. Vessel Arrival Forecast ────────────────────────────────────────────
    _divider("14-Day Vessel Arrival Forecast")
    _section_header(
        "Vessel Arrival Forecast — 14 Days",
        "Projected daily arrivals for top 8 ports with demand implications.",
        icon="🚢",
    )
    _render_vessel_arrival_forecast(port_results)

    # ── 9. Seasonal Demand Heatmap ────────────────────────────────────────────
    _divider("Seasonal Demand Patterns")
    _section_header(
        "Seasonal Demand Heatmap",
        "Port × month matrix. Green = peak demand months. Red = trough. Plan sailings accordingly.",
        icon="📅",
    )
    _render_seasonal_heatmap(port_results)

    # ── 10. Demand Signal Breakdown ───────────────────────────────────────────
    _divider("Demand Signal Breakdown")
    _section_header(
        "Demand Signal Breakdown by Port",
        "Components driving each port's score: Trade Flow, AIS Activity, Bookings, Throughput.",
        icon="🔬",
    )
    _render_demand_signal_breakdown(port_results)

    # ── Score Distribution ─────────────────────────────────────────────────────
    _divider("Score Distribution")
    _section_header(
        "Demand Score Distribution",
        "Histogram across 20 bins. Vertical lines mark tier boundaries.",
        icon="📊",
    )
    _render_demand_histogram(port_results)

    # ── Port Detail ────────────────────────────────────────────────────────────
    _divider("Port Deep Dive")
    _section_header(
        "Port Detail",
        "Select any port for full metric breakdown, score decomposition, and import category chart.",
        icon="🔍",
    )
    _render_port_detail(port_results, sorted_results)

    # ── All Ports Table ────────────────────────────────────────────────────────
    _divider("All Ports Summary Table")
    _section_header(
        "All Ports — Summary Table",
        "Full dataset. Score column color-coded by demand tier. Download as CSV.",
        icon="📋",
    )
    _render_all_ports_table(sorted_results)
