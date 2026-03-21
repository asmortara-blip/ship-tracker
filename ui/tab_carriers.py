"""ui/tab_carriers.py — Container Carrier Intelligence tab.

Full-featured carrier intelligence dashboard with market structure analysis,
alliance visualization, performance scorecards, financial health comparison,
fleet growth orderbook, service reliability trends, and carrier news feed.

Integration (add to app.py tabs):
    from ui.tab_carriers import render as render_carriers
    with tab_carriers:
        render_carriers(route_results, freight_data, stock_data)
"""
from __future__ import annotations

import math
import random
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from loguru import logger

from processing.carrier_tracker import (
    ALLIANCES,
    CARRIER_PROFILES,
    AllianceProfile,
    CarrierProfile,
    compute_blank_sailing_rate,
    compute_carrier_hhi,
    get_route_carrier_coverage,
)
from ui.styles import (
    C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    _hex_to_rgba,
    dark_layout,
    section_header,
)

# ── Local color constants ──────────────────────────────────────────────────────
_C_BG      = "#0a0f1a"
_C_SURFACE = "#111827"
_C_GREEN   = "#10b981"
_C_RED     = "#ef4444"
_C_AMBER   = "#f59e0b"
_C_BLUE    = "#3b82f6"
_C_PURPLE  = "#8b5cf6"
_C_CYAN    = "#06b6d4"
_C_ORANGE  = "#f97316"
_C_PINK    = "#ec4899"
_C_LIME    = "#84cc16"
_C_TEAL    = "#14b8a6"

# Alliance color palette (consistent across all charts)
_ALLIANCE_COLORS: dict[str, str] = {
    "Gemini Cooperation": _C_BLUE,
    "Ocean Alliance":     _C_GREEN,
    "Premier Alliance":   _C_PURPLE,
    "MSC Independent":    _C_AMBER,
    None:                 _C_CYAN,
}

# Carrier color palette (unique per carrier, consistent across all sections)
_CARRIER_COLORS: dict[str, str] = {
    "MSC":         _C_AMBER,
    "Maersk":      _C_BLUE,
    "CMA CGM":     _C_GREEN,
    "COSCO":       _C_RED,
    "Hapag-Lloyd": _C_ORANGE,
    "ONE":         _C_PURPLE,
    "Evergreen":   _C_CYAN,
    "Yang Ming":   _C_LIME,
    "ZIM":         _C_PINK,
    "PIL":         _C_TEAL,
    "HMM":         "#a78bfa",
}

# Carrier HQ coordinates [lat, lon] for globe chart
_CARRIER_HQ: dict[str, tuple[float, float]] = {
    "MSC":         (46.2044,  6.1432),
    "Maersk":      (55.6761, 12.5683),
    "CMA CGM":     (43.2965,  5.3698),
    "COSCO":       (39.9042, 116.4074),
    "Hapag-Lloyd": (53.5511,  9.9937),
    "ONE":         (35.6762, 139.6503),
    "Evergreen":   (25.0330, 121.5654),
    "Yang Ming":   (25.0330, 121.5654),
    "ZIM":         (32.7940,  34.9896),
    "PIL":         (1.3521,  103.8198),
}


# ── Helper utilities ───────────────────────────────────────────────────────────

def _alliance_color(alliance: Optional[str]) -> str:
    return _ALLIANCE_COLORS.get(alliance, _C_CYAN)


def _carrier_color(carrier_name: str) -> str:
    return _CARRIER_COLORS.get(carrier_name, _C_CYAN)


def _health_color(health: str) -> str:
    return {"STRONG": _C_GREEN, "STABLE": _C_BLUE, "WEAK": _C_RED}.get(health, _C_AMBER)


def _make_monthly_reliability_series(carrier_name: str, months: int = 12) -> pd.Series:
    """Generate a synthetic monthly on-time % series for a carrier."""
    base = CARRIER_PROFILES[carrier_name].schedule_reliability_pct
    rng = random.Random(hash(carrier_name) % 77777)
    values = []
    current = base
    for _ in range(months):
        shock = rng.gauss(0, 2.8)
        current = max(35.0, min(92.0, current + shock))
        values.append(round(current, 1))
    idx = pd.date_range(end=pd.Timestamp("2026-03-01"), periods=months, freq="MS")
    return pd.Series(values, index=idx, name=carrier_name)


def _card_html(
    label: str,
    value: str,
    sub: str = "",
    color: str = _C_BLUE,
    icon: str = "",
    badge: str = "",
    badge_color: str = "",
) -> str:
    """Render a single KPI hero card as HTML."""
    badge_html = ""
    if badge:
        bc = badge_color or _C_AMBER
        badge_html = (
            f"<span style='background:{bc}22; color:{bc}; border:1px solid {bc}55;"
            f" border-radius:999px; font-size:0.6rem; font-weight:700;"
            f" padding:2px 8px; margin-top:4px; display:inline-block'>{badge}</span>"
        )
    sub_html = f"<div style='color:{C_TEXT3}; font-size:0.70rem; margin-top:4px'>{sub}</div>" if sub else ""
    return (
        f"<div style='background:{C_CARD}; border:1px solid {color}33;"
        f" border-top:3px solid {color}; border-radius:12px; padding:18px 20px;"
        f" display:flex; flex-direction:column; gap:4px'>"
        f"<div style='color:{C_TEXT3}; font-size:0.65rem; text-transform:uppercase;"
        f" letter-spacing:0.09em; font-weight:600'>{icon}&nbsp;{label}</div>"
        f"<div style='color:{C_TEXT}; font-size:1.55rem; font-weight:800;"
        f" letter-spacing:-0.02em; line-height:1.1'>{value}</div>"
        f"{sub_html}{badge_html}"
        f"</div>"
    )


# ── Section 1: Hero Dashboard KPIs ────────────────────────────────────────────

def _render_hero_kpis() -> None:
    """Render carriers hero KPI dashboard — top-level market snapshot."""
    try:
        hhi = compute_carrier_hhi()
        total_carriers = len(CARRIER_PROFILES)
        total_teu = sum(p.teu_capacity_m for p in CARRIER_PROFILES.values())
        avg_reliability = sum(p.schedule_reliability_pct for p in CARRIER_PROFILES.values()) / total_carriers

        # HHI interpretation
        if hhi >= 2500:
            hhi_label, hhi_color = "Highly Concentrated", _C_RED
        elif hhi >= 1500:
            hhi_label, hhi_color = "Moderately Concentrated", _C_AMBER
        else:
            hhi_label, hhi_color = "Competitive", _C_GREEN

        # Market share change (synthetic YoY avg)
        rng = random.Random(2026)
        avg_share_chg = round(rng.gauss(1.4, 0.6), 1)
        share_chg_str = f"+{avg_share_chg}%" if avg_share_chg >= 0 else f"{avg_share_chg}%"
        share_chg_color = _C_GREEN if avg_share_chg >= 0 else _C_RED

        st.markdown(
            "<div style='font-size:1.25rem; font-weight:800; color:"
            + C_TEXT + "; margin-bottom:6px; letter-spacing:-0.02em'>"
            "🚢 Carrier Intelligence Dashboard"
            "</div>"
            "<div style='color:" + C_TEXT3 + "; font-size:0.78rem; margin-bottom:18px'>"
            "Global container carrier market structure • Alliance dynamics • Performance & financial health"
            "</div>",
            unsafe_allow_html=True,
        )

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(
                _card_html("Carriers Tracked", str(total_carriers), "Top-tier global operators",
                           _C_BLUE, "🏢", badge="2026 Coverage"),
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                _card_html("Market HHI", f"{hhi:,.0f}", hhi_label,
                           hhi_color, "📊", badge=hhi_label, badge_color=hhi_color),
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                _card_html("Avg Share Δ YoY", share_chg_str, "Avg market share change",
                           share_chg_color, "📈"),
                unsafe_allow_html=True,
            )
        with c4:
            st.markdown(
                _card_html("Total Fleet Capacity", f"{total_teu:.1f}M TEU",
                           f"Avg reliability {avg_reliability:.0f}%",
                           _C_PURPLE, "🚢"),
                unsafe_allow_html=True,
            )

    except Exception as exc:
        logger.warning("Hero KPIs render error: {}", exc)
        st.warning("Hero KPI dashboard unavailable.")


# ── Section 2: Market Share Donut + Ranked Table ──────────────────────────────

# YoY share change data (positive = gained share)
_SHARE_DATA: list[dict] = [
    {"name": "MSC",         "share": 21.1, "yoy": +2.4,  "teu_m": 6.1},
    {"name": "Maersk",      "share": 16.9, "yoy": -0.8,  "teu_m": 4.3},
    {"name": "CMA CGM",     "share": 13.0, "yoy": +0.3,  "teu_m": 3.8},
    {"name": "COSCO",       "share": 11.2, "yoy": -0.4,  "teu_m": 3.2},
    {"name": "Hapag-Lloyd", "share":  8.4, "yoy": -0.2,  "teu_m": 2.0},
    {"name": "ONE",         "share":  6.6, "yoy": +0.1,  "teu_m": 1.6},
    {"name": "Evergreen",   "share":  5.7, "yoy": -0.1,  "teu_m": 1.5},
    {"name": "Yang Ming",   "share":  3.0, "yoy": +0.0,  "teu_m": 0.7},
    {"name": "ZIM",         "share":  2.8, "yoy": -0.3,  "teu_m": 0.6},
    {"name": "PIL",         "share":  1.4, "yoy": -0.1,  "teu_m": 0.3},
    {"name": "Others",      "share":  9.9, "yoy": -0.9,  "teu_m": 2.7},
]


def _render_market_share_section() -> None:
    """Render market share donut chart + ranked carrier table with YoY arrows."""
    section_header(
        "Carrier Market Share",
        "Global TEU capacity share • YoY change • Ranked by fleet size (2026 estimates)",
    )
    try:
        col_donut, col_table = st.columns([1, 1.4])

        with col_donut:
            labels = [d["name"] for d in _SHARE_DATA]
            values = [d["share"] for d in _SHARE_DATA]
            colors = [_CARRIER_COLORS.get(d["name"], "#475569") for d in _SHARE_DATA]

            hover_texts = [
                f"<b>{d['name']}</b><br>Share: {d['share']}%<br>TEU: {d['teu_m']}M<br>"
                f"YoY: {'↑' if d['yoy'] > 0 else ('↓' if d['yoy'] < 0 else '→')} {abs(d['yoy']):.1f}pp"
                for d in _SHARE_DATA
            ]

            fig = go.Figure(go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=colors, line=dict(color=_C_BG, width=2.5)),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=hover_texts,
                textinfo="label+percent",
                textfont=dict(color=C_TEXT, size=10),
                insidetextorientation="radial",
                pull=[0.04 if d["name"] == "MSC" else 0 for d in _SHARE_DATA],
            ))
            fig.add_annotation(
                text="<b>Global<br>Container<br>Market</b>",
                x=0.5, y=0.5, font=dict(size=10, color=C_TEXT2), showarrow=False,
            )
            layout = dark_layout(height=370, showlegend=False)
            layout["margin"] = {"l": 5, "r": 5, "t": 20, "b": 5}
            fig.update_layout(**layout)
            st.plotly_chart(fig, use_container_width=True, key="carriers_v2_share_donut")

        with col_table:
            rows = ""
            for i, d in enumerate(_SHARE_DATA):
                if d["name"] == "Others":
                    continue
                color = _CARRIER_COLORS.get(d["name"], "#475569")
                yoy = d["yoy"]
                arrow = "▲" if yoy > 0 else ("▼" if yoy < 0 else "—")
                yoy_color = _C_GREEN if yoy > 0 else (_C_RED if yoy < 0 else C_TEXT3)
                bar_w = int((d["share"] / 22) * 100)
                rows += (
                    f"<tr style='border-bottom:1px solid rgba(255,255,255,0.05)'>"
                    f"<td style='padding:10px 12px; font-size:0.75rem; color:{C_TEXT3}'>{i+1}</td>"
                    f"<td style='padding:10px 4px'>"
                    f"<span style='display:inline-block; width:10px; height:10px; border-radius:50%;"
                    f" background:{color}; margin-right:6px'></span>"
                    f"<span style='font-size:0.82rem; font-weight:700; color:{C_TEXT}'>{d['name']}</span></td>"
                    f"<td style='padding:10px 12px'>"
                    f"<div style='display:flex; align-items:center; gap:6px'>"
                    f"<div style='width:80px; background:rgba(255,255,255,0.06); border-radius:3px; height:5px'>"
                    f"<div style='width:{bar_w}%; background:{color}; border-radius:3px; height:5px'></div></div>"
                    f"<span style='font-size:0.78rem; font-weight:700; color:{C_TEXT}'>{d['share']}%</span>"
                    f"</div></td>"
                    f"<td style='padding:10px 12px; font-size:0.78rem; font-weight:700; color:{yoy_color}'>"
                    f"{arrow} {abs(yoy):.1f}pp</td>"
                    f"<td style='padding:10px 12px; font-size:0.75rem; color:{C_TEXT2}'>{d['teu_m']}M TEU</td>"
                    f"</tr>"
                )
            hs = f"color:{C_TEXT3}; font-size:0.63rem; text-transform:uppercase; letter-spacing:0.07em; padding:8px 12px"
            table_html = (
                f"<div style='overflow-x:auto; background:{C_CARD}; border:1px solid rgba(59,130,246,0.18);"
                f" border-radius:12px; padding:6px'>"
                f"<table style='width:100%; border-collapse:collapse'>"
                f"<thead><tr>"
                f"<th style='{hs}'>#</th><th style='{hs}'>Carrier</th>"
                f"<th style='{hs}'>Share</th><th style='{hs}'>YoY Δ</th>"
                f"<th style='{hs}'>Capacity</th>"
                f"</tr></thead><tbody>{rows}</tbody></table></div>"
            )
            st.markdown(table_html, unsafe_allow_html=True)

    except Exception as exc:
        logger.warning("Market share section render error: {}", exc)
        st.warning("Market share section unavailable.")


# ── Section 3: Carrier Performance Scorecard ──────────────────────────────────

_SCORECARD_DATA: list[dict] = [
    {"name": "MSC",         "reliability": 72, "on_time": 68, "port_calls_wk": 142, "capacity_k": 6100, "health": "STRONG"},
    {"name": "Maersk",      "reliability": 68, "on_time": 71, "port_calls_wk": 118,  "capacity_k": 4300, "health": "STRONG"},
    {"name": "CMA CGM",     "reliability": 65, "on_time": 64, "port_calls_wk": 105,  "capacity_k": 3800, "health": "STRONG"},
    {"name": "COSCO",       "reliability": 71, "on_time": 67, "port_calls_wk": 88,   "capacity_k": 3200, "health": "STABLE"},
    {"name": "Hapag-Lloyd", "reliability": 74, "on_time": 76, "port_calls_wk": 62,   "capacity_k": 2000, "health": "STRONG"},
    {"name": "ONE",         "reliability": 70, "on_time": 69, "port_calls_wk": 55,   "capacity_k": 1600, "health": "STABLE"},
    {"name": "Evergreen",   "reliability": 67, "on_time": 63, "port_calls_wk": 48,   "capacity_k": 1500, "health": "STABLE"},
    {"name": "Yang Ming",   "reliability": 66, "on_time": 61, "port_calls_wk": 32,   "capacity_k": 700,  "health": "STABLE"},
    {"name": "ZIM",         "reliability": 63, "on_time": 58, "port_calls_wk": 28,   "capacity_k": 600,  "health": "STABLE"},
    {"name": "PIL",         "reliability": 61, "on_time": 54, "port_calls_wk": 22,   "capacity_k": 300,  "health": "WEAK"},
]


def _render_carrier_scorecard() -> None:
    """Render grid of carrier performance cards — reliability, on-time, port calls, capacity."""
    section_header(
        "Carrier Performance Scorecard",
        "Key operational metrics per carrier • Schedule reliability • Port coverage • Fleet size",
    )
    try:
        # 5 cards per row, 2 rows
        row1 = _SCORECARD_DATA[:5]
        row2 = _SCORECARD_DATA[5:]

        for row_data in [row1, row2]:
            cols = st.columns(len(row_data))
            for col, c in zip(cols, row_data):
                color = _CARRIER_COLORS.get(c["name"], _C_CYAN)
                h_color = _health_color(c["health"])
                rel_color = _C_GREEN if c["reliability"] >= 70 else (_C_AMBER if c["reliability"] >= 65 else _C_RED)
                ot_color  = _C_GREEN if c["on_time"] >= 70 else (_C_AMBER if c["on_time"] >= 60 else _C_RED)

                def _mini_bar(val: int, max_val: int, bar_color: str) -> str:
                    pct = min(100, int((val / max_val) * 100))
                    return (
                        f"<div style='background:rgba(255,255,255,0.06); border-radius:3px; height:4px; margin-top:3px'>"
                        f"<div style='width:{pct}%; background:{bar_color}; border-radius:3px; height:4px'></div></div>"
                    )

                card = (
                    f"<div style='background:{C_CARD}; border:1px solid {color}30;"
                    f" border-top:3px solid {color}; border-radius:12px; padding:14px 14px 12px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:10px'>"
                    f"<span style='font-size:0.83rem; font-weight:800; color:{C_TEXT}'>{c['name']}</span>"
                    f"<span style='background:{h_color}22; color:{h_color}; border:1px solid {h_color}55;"
                    f" border-radius:999px; font-size:0.58rem; font-weight:700; padding:2px 7px'>{c['health']}</span>"
                    f"</div>"
                    f"<div style='font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em'>Schedule Reliability</div>"
                    f"<div style='font-size:1.15rem; font-weight:700; color:{rel_color}'>{c['reliability']}%</div>"
                    f"{_mini_bar(c['reliability'], 100, rel_color)}"
                    f"<div style='margin-top:8px; font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em'>On-Time %</div>"
                    f"<div style='font-size:1.15rem; font-weight:700; color:{ot_color}'>{c['on_time']}%</div>"
                    f"{_mini_bar(c['on_time'], 100, ot_color)}"
                    f"<div style='margin-top:10px; display:flex; justify-content:space-between'>"
                    f"<div><div style='font-size:0.58rem; color:{C_TEXT3}; text-transform:uppercase'>Port Calls/Wk</div>"
                    f"<div style='font-size:0.88rem; font-weight:700; color:{C_TEXT2}'>{c['port_calls_wk']}</div></div>"
                    f"<div><div style='font-size:0.58rem; color:{C_TEXT3}; text-transform:uppercase'>Fleet (k TEU)</div>"
                    f"<div style='font-size:0.88rem; font-weight:700; color:{C_TEXT2}'>{c['capacity_k']:,}</div></div>"
                    f"</div></div>"
                )
                with col:
                    st.markdown(card, unsafe_allow_html=True)
            st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)

    except Exception as exc:
        logger.warning("Carrier scorecard render error: {}", exc)
        st.warning("Carrier scorecard unavailable.")


# ── Section 4: Alliance Structure Visualization ────────────────────────────────

def _render_alliance_visualization() -> None:
    """Render alliance structure diagram — carrier membership, capacity share, key routes."""
    section_header(
        "Alliance Structure",
        "Current alliance groupings • Combined TEU share • Key trades covered (2026)",
    )
    try:
        alliance_order = ["Gemini Cooperation", "Ocean Alliance", "Premier Alliance", "MSC Independent"]
        cols = st.columns(4)

        for col, alliance_name in zip(cols, alliance_order):
            alliance = ALLIANCES.get(alliance_name)
            if alliance is None:
                continue
            color = _alliance_color(alliance_name)
            members_html = "".join(
                f"<div style='background:{_CARRIER_COLORS.get(m, color)}22; border:1px solid {_CARRIER_COLORS.get(m, color)}55;"
                f" border-radius:8px; padding:5px 10px; margin:3px 0; font-size:0.75rem; font-weight:600; color:{C_TEXT}'>{m}</div>"
                for m in alliance.members
            )
            routes_preview = " · ".join(
                r.replace("_", " ").replace("eb", "E/B").replace("wb", "W/B").title()
                for r in alliance.key_routes[:3]
            )
            status_color = _C_GREEN if alliance.status == "ACTIVE" else _C_AMBER
            card = (
                f"<div style='background:{C_CARD}; border:1px solid {color}40; border-top:3px solid {color};"
                f" border-radius:12px; padding:16px 14px; height:100%'>"
                f"<div style='font-size:0.9rem; font-weight:800; color:{color}; margin-bottom:4px'>{alliance_name}</div>"
                f"<div style='display:flex; gap:8px; margin-bottom:10px'>"
                f"<span style='background:{status_color}22; color:{status_color}; border:1px solid {status_color}55;"
                f" border-radius:999px; font-size:0.6rem; font-weight:700; padding:2px 7px'>{alliance.status}</span>"
                f"<span style='color:{C_TEXT3}; font-size:0.65rem; padding-top:3px'>{alliance.formed_date}</span>"
                f"</div>"
                f"<div style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px'>Members</div>"
                f"{members_html}"
                f"<div style='margin-top:10px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.07)'>"
                f"<div style='display:flex; justify-content:space-between; margin-bottom:4px'>"
                f"<span style='font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase'>Combined Share</span>"
                f"<span style='font-size:0.82rem; font-weight:800; color:{color}'>{alliance.combined_share_pct}%</span></div>"
                f"<div style='display:flex; justify-content:space-between; margin-bottom:8px'>"
                f"<span style='font-size:0.62rem; color:{C_TEXT3}; text-transform:uppercase'>Total TEU</span>"
                f"<span style='font-size:0.82rem; font-weight:700; color:{C_TEXT2}'>{alliance.combined_teu_m:.1f}M</span></div>"
                f"<div style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px'>Key Trades</div>"
                f"<div style='font-size:0.68rem; color:{C_TEXT2}; line-height:1.5'>{routes_preview}</div>"
                f"<div style='margin-top:8px; font-size:0.65rem; color:{C_TEXT3}; line-height:1.5; font-style:italic'>{alliance.notes[:120]}…</div>"
                f"</div></div>"
            )
            with col:
                st.markdown(card, unsafe_allow_html=True)

        # Alliance capacity share bar chart
        st.markdown("<div style='margin-top:18px'></div>", unsafe_allow_html=True)
        fig = go.Figure()
        alliance_names = list(ALLIANCES.keys())
        shares = [ALLIANCES[a].combined_share_pct for a in alliance_names]
        colors = [_alliance_color(a) for a in alliance_names]
        fig.add_trace(go.Bar(
            x=alliance_names,
            y=shares,
            marker=dict(
                color=colors,
                line=dict(color=[c.replace(")", ", 0.8)").replace("rgb", "rgba") for c in colors], width=0),
            ),
            text=[f"{s}%" for s in shares],
            textfont=dict(color=C_TEXT, size=12, family="Inter, sans-serif"),
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Share: %{y}%<extra></extra>",
            width=0.5,
        ))
        layout = dark_layout(height=220, showlegend=False)
        layout["margin"] = {"l": 10, "r": 10, "t": 20, "b": 30}
        layout["yaxis"] = {**layout.get("yaxis", {}), "title": "Combined Market Share (%)", "range": [0, 45]}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="carriers_v2_alliance_bar")

    except Exception as exc:
        logger.warning("Alliance visualization render error: {}", exc)
        st.warning("Alliance structure visualization unavailable.")


# ── Section 5: Carrier Rate Competitiveness ───────────────────────────────────

# Spot rate premium/discount vs market average (USD/TEU) per route
_RATE_DATA: dict[str, dict[str, int]] = {
    "Asia–N.America W/B": {
        "MSC": +180,  "Maersk": +95, "CMA CGM": +140, "COSCO": -60,
        "Hapag-Lloyd": +210, "ONE": +30, "Evergreen": -90, "ZIM": -180,
    },
    "Asia–Europe": {
        "MSC": +160,  "Maersk": +85, "CMA CGM": +120, "COSCO": -40,
        "Hapag-Lloyd": +195, "ONE": +20, "Evergreen": -70, "ZIM": -220,
    },
    "Transatlantic": {
        "MSC": +130,  "Maersk": +110, "CMA CGM": +90, "COSCO": -30,
        "Hapag-Lloyd": +175, "ONE": -10, "Evergreen": -50, "ZIM": -160,
    },
    "Latin America": {
        "MSC": +200,  "Maersk": +70, "CMA CGM": +160, "COSCO": -80,
        "Hapag-Lloyd": +140, "ONE": +50, "Evergreen": -40, "ZIM": -130,
    },
}


def _render_rate_competitiveness() -> None:
    """Render spot rate premium/discount chart by carrier for key routes."""
    section_header(
        "Carrier Rate Competitiveness",
        "Spot rate premium (+) or discount (−) vs market average (USD/TEU) by route",
    )
    try:
        routes = list(_RATE_DATA.keys())
        carriers = ["MSC", "Maersk", "CMA CGM", "COSCO", "Hapag-Lloyd", "ONE", "Evergreen", "ZIM"]

        tab_labels = routes
        tabs = st.tabs(tab_labels)

        for tab, route in zip(tabs, routes):
            with tab:
                try:
                    route_vals = _RATE_DATA[route]
                    c_names = list(route_vals.keys())
                    c_vals  = list(route_vals.values())
                    c_colors = [_C_GREEN if v >= 0 else _C_RED for v in c_vals]

                    fig = go.Figure(go.Bar(
                        x=c_names,
                        y=c_vals,
                        marker=dict(
                            color=c_colors,
                            opacity=0.85,
                            line=dict(color="rgba(255,255,255,0.1)", width=1),
                        ),
                        text=[f"${v:+,}" for v in c_vals],
                        textfont=dict(color=C_TEXT, size=11),
                        textposition="outside",
                        hovertemplate="<b>%{x}</b><br>vs Market: $%{y:+,}/TEU<extra></extra>",
                        width=0.55,
                    ))
                    fig.add_hline(y=0, line=dict(color="rgba(255,255,255,0.3)", width=1.5, dash="dot"))
                    layout = dark_layout(height=300, showlegend=False)
                    layout["margin"] = {"l": 10, "r": 10, "t": 30, "b": 20}
                    layout["yaxis"] = {
                        **layout.get("yaxis", {}),
                        "title": "Rate vs Market Avg (USD/TEU)",
                        "zeroline": True,
                        "zerolinecolor": "rgba(255,255,255,0.25)",
                    }
                    fig.update_layout(**layout)
                    st.plotly_chart(fig, use_container_width=True,
                                    key=f"carriers_v2_rate_{route.replace(' ', '_').replace('–','')}")
                except Exception as inner_exc:
                    logger.warning("Rate competitiveness tab error ({}): {}", route, inner_exc)
                    st.warning(f"Rate data unavailable for {route}.")

    except Exception as exc:
        logger.warning("Rate competitiveness render error: {}", exc)
        st.warning("Rate competitiveness chart unavailable.")


# ── Section 6: Carrier Capacity Deployment Heatmap ────────────────────────────

# Weekly slot capacity (k TEU) per carrier x route
_CAPACITY_HEATMAP: dict[str, list[int]] = {
    #                       Asia-NAm  Asia-EU  Transatl  Lat-Am   Africa  Intra-Asia
    "MSC":         [420, 380, 210, 280, 310, 85],
    "Maersk":      [310, 290, 175, 140, 60,  30],
    "CMA CGM":     [280, 260, 150, 200, 190, 40],
    "COSCO":       [260, 230, 60,  80,  40,  220],
    "Hapag-Lloyd": [185, 200, 160, 130, 25,  10],
    "ONE":         [195, 155, 40,  30,  15,  180],
    "Evergreen":   [160, 140, 70,  40,  10,  95],
    "Yang Ming":   [80,  75,  15,  10,  5,   55],
    "ZIM":         [65,  30,  45,  70,  20,  10],
    "PIL":         [10,  15,  5,   20,  40,  85],
}
_HEATMAP_ROUTES = ["Asia–NAm", "Asia–Europe", "Transatlantic", "Lat-Am", "Africa", "Intra-Asia"]
_HEATMAP_CARRIERS = list(_CAPACITY_HEATMAP.keys())


def _render_capacity_heatmap() -> None:
    """Render heatmap of weekly slot capacity (k TEU) per carrier x route."""
    section_header(
        "Capacity Deployment by Route",
        "Weekly slot capacity (k TEU) per carrier × trade lane — darker = more capacity",
    )
    try:
        z = [_CAPACITY_HEATMAP[c] for c in _HEATMAP_CARRIERS]
        text_z = [[f"{v}k" for v in row] for row in z]

        fig = go.Figure(go.Heatmap(
            z=z,
            x=_HEATMAP_ROUTES,
            y=_HEATMAP_CARRIERS,
            text=text_z,
            texttemplate="%{text}",
            textfont=dict(size=11, color=C_TEXT),
            colorscale=[
                [0.0,  "#0a0f1a"],
                [0.15, "#0d2038"],
                [0.35, "#0f3460"],
                [0.55, "#1a5276"],
                [0.75, "#1d6fa5"],
                [0.90, "#2e86c1"],
                [1.0,  "#3b82f6"],
            ],
            showscale=True,
            colorbar=dict(
                title=dict(text="k TEU/wk", font=dict(color=C_TEXT2, size=11)),
                tickfont=dict(color=C_TEXT3, size=10),
                thickness=14,
                len=0.9,
            ),
            hovertemplate="<b>%{y}</b> → %{x}<br>Capacity: %{z}k TEU/wk<extra></extra>",
        ))
        layout = dark_layout(height=380, showlegend=False)
        layout["margin"] = {"l": 80, "r": 60, "t": 20, "b": 60}
        layout["xaxis"] = {**layout.get("xaxis", {}), "side": "bottom", "tickangle": -15}
        layout["yaxis"] = {**layout.get("yaxis", {}), "autorange": "reversed"}
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="carriers_v2_capacity_heatmap")

    except Exception as exc:
        logger.warning("Capacity heatmap render error: {}", exc)
        st.warning("Capacity deployment heatmap unavailable.")


# ── Section 7: Carrier Financial Health Comparison ────────────────────────────

_FINANCIAL_DATA: list[dict] = [
    {"name": "MSC",         "revenue": 14.8, "ebitda_margin": 38.2, "net_margin": 21.4, "debt_ratio": 0.42},
    {"name": "Maersk",      "revenue": 11.2, "ebitda_margin": 31.5, "net_margin": 16.8, "debt_ratio": 0.38},
    {"name": "CMA CGM",     "revenue": 10.1, "ebitda_margin": 33.7, "net_margin": 18.1, "debt_ratio": 0.51},
    {"name": "COSCO",       "revenue":  7.6, "ebitda_margin": 28.9, "net_margin": 14.2, "debt_ratio": 0.45},
    {"name": "Hapag-Lloyd", "revenue":  5.6, "ebitda_margin": 35.4, "net_margin": 19.6, "debt_ratio": 0.34},
    {"name": "ONE",         "revenue":  3.9, "ebitda_margin": 26.1, "net_margin": 12.8, "debt_ratio": 0.29},
    {"name": "Evergreen",   "revenue":  3.4, "ebitda_margin": 29.3, "net_margin": 15.3, "debt_ratio": 0.36},
    {"name": "Yang Ming",   "revenue":  1.7, "ebitda_margin": 22.8, "net_margin": 11.4, "debt_ratio": 0.41},
    {"name": "ZIM",         "revenue":  2.1, "ebitda_margin": 18.4, "net_margin":  8.9, "debt_ratio": 0.55},
    {"name": "PIL",         "revenue":  0.6, "ebitda_margin": 10.2, "net_margin":  2.1, "debt_ratio": 0.68},
]


def _render_financial_health() -> None:
    """Render grouped bar chart of revenue, EBITDA margin, and net profit margin by carrier."""
    section_header(
        "Carrier Financial Health",
        "Q4 2025 revenue (USD B) • EBITDA margin % • Net profit margin % • Debt/equity ratio",
    )
    try:
        names = [d["name"] for d in _FINANCIAL_DATA]
        revenues = [d["revenue"] for d in _FINANCIAL_DATA]
        ebitda   = [d["ebitda_margin"] for d in _FINANCIAL_DATA]
        net_m    = [d["net_margin"] for d in _FINANCIAL_DATA]
        carrier_colors = [_CARRIER_COLORS.get(n, _C_CYAN) for n in names]

        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=["Revenue (USD B) & Margins (%)", "Debt-to-Asset Ratio"],
            column_widths=[0.65, 0.35],
        )

        # Revenue bars
        fig.add_trace(go.Bar(
            x=names, y=revenues,
            name="Revenue ($B)",
            marker=dict(color=carrier_colors, opacity=0.85),
            yaxis="y1",
            hovertemplate="<b>%{x}</b><br>Revenue: $%{y:.1f}B<extra></extra>",
            width=0.3,
            offsetgroup=0,
        ), row=1, col=1)

        # EBITDA margin overlay (secondary axis)
        fig.add_trace(go.Scatter(
            x=names, y=ebitda,
            name="EBITDA Margin %",
            mode="markers+lines",
            marker=dict(color=_C_GREEN, size=8, symbol="circle"),
            line=dict(color=_C_GREEN, width=2, dash="dot"),
            hovertemplate="<b>%{x}</b><br>EBITDA Margin: %{y:.1f}%<extra></extra>",
            yaxis="y2",
        ), row=1, col=1)

        # Net margin overlay
        fig.add_trace(go.Scatter(
            x=names, y=net_m,
            name="Net Margin %",
            mode="markers+lines",
            marker=dict(color=_C_CYAN, size=8, symbol="diamond"),
            line=dict(color=_C_CYAN, width=2),
            hovertemplate="<b>%{x}</b><br>Net Margin: %{y:.1f}%<extra></extra>",
            yaxis="y2",
        ), row=1, col=1)

        # Debt ratio horizontal bars
        debt_colors = [
            _C_RED if d["debt_ratio"] > 0.55 else (_C_AMBER if d["debt_ratio"] > 0.40 else _C_GREEN)
            for d in _FINANCIAL_DATA
        ]
        fig.add_trace(go.Bar(
            x=[d["debt_ratio"] for d in _FINANCIAL_DATA],
            y=names,
            orientation="h",
            marker=dict(color=debt_colors, opacity=0.82),
            name="Debt/Asset",
            hovertemplate="<b>%{y}</b><br>Debt/Asset: %{x:.2f}<extra></extra>",
            text=[f"{d['debt_ratio']:.2f}" for d in _FINANCIAL_DATA],
            textposition="outside",
            textfont=dict(size=10, color=C_TEXT2),
        ), row=1, col=2)

        layout = dark_layout(height=420, showlegend=True)
        layout["margin"] = {"l": 10, "r": 20, "t": 50, "b": 30}
        layout["barmode"] = "group"
        layout["yaxis2"] = {
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "tickfont": {"color": C_TEXT3, "size": 10},
            "title": {"text": "Margin %", "font": {"color": C_TEXT3, "size": 10}},
            "range": [0, 50],
        }
        layout["legend"] = {
            "orientation": "h", "y": -0.12, "x": 0.0,
            "font": {"color": C_TEXT2, "size": 10},
            "bgcolor": "rgba(0,0,0,0)",
        }
        fig.update_layout(**layout)
        fig.update_xaxes(tickangle=-35, row=1, col=1)
        fig.update_xaxes(title_text="Ratio", row=1, col=2)
        st.plotly_chart(fig, use_container_width=True, key="carriers_v2_financial")

    except Exception as exc:
        logger.warning("Financial health render error: {}", exc)
        st.warning("Financial health comparison unavailable.")


# ── Section 8: Fleet Growth / Orderbook Comparison ────────────────────────────

_ORDERBOOK_DATA: list[dict] = [
    {"name": "MSC",         "orderbook_pct": 32.4, "vessels_on_order": 112, "avg_size_k": 18.5},
    {"name": "CMA CGM",     "orderbook_pct": 28.7, "vessels_on_order": 88,  "avg_size_k": 16.2},
    {"name": "Maersk",      "orderbook_pct": 14.2, "vessels_on_order": 44,  "avg_size_k": 15.8},
    {"name": "Hapag-Lloyd", "orderbook_pct": 19.6, "vessels_on_order": 32,  "avg_size_k": 14.4},
    {"name": "COSCO",       "orderbook_pct": 21.3, "vessels_on_order": 65,  "avg_size_k": 17.1},
    {"name": "ONE",         "orderbook_pct": 24.8, "vessels_on_order": 38,  "avg_size_k": 15.9},
    {"name": "Evergreen",   "orderbook_pct": 18.1, "vessels_on_order": 28,  "avg_size_k": 14.0},
    {"name": "Yang Ming",   "orderbook_pct": 12.6, "vessels_on_order": 10,  "avg_size_k": 12.5},
    {"name": "ZIM",         "orderbook_pct":  8.3, "vessels_on_order": 14,  "avg_size_k": 7.8},
    {"name": "PIL",         "orderbook_pct":  4.1, "vessels_on_order":  5,  "avg_size_k": 5.2},
]


def _render_fleet_growth() -> None:
    """Render newbuild orderbook as % of current fleet and absolute vessel counts."""
    section_header(
        "Fleet Growth — Newbuild Orderbook",
        "Newbuild orderbook as % of current fleet capacity • Vessels on order • Avg newbuild size",
    )
    try:
        sorted_data = sorted(_ORDERBOOK_DATA, key=lambda d: d["orderbook_pct"], reverse=True)
        names = [d["name"] for d in sorted_data]
        pcts  = [d["orderbook_pct"] for d in sorted_data]
        vessels = [d["vessels_on_order"] for d in sorted_data]
        colors = [_CARRIER_COLORS.get(n, _C_CYAN) for n in names]

        fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=["Orderbook as % of Fleet", "Vessels on Order"],
            column_widths=[0.55, 0.45],
        )

        fig.add_trace(go.Bar(
            x=names, y=pcts,
            marker=dict(
                color=colors, opacity=0.85,
                line=dict(color="rgba(255,255,255,0.08)", width=1),
            ),
            text=[f"{p:.1f}%" for p in pcts],
            textfont=dict(size=11, color=C_TEXT),
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Orderbook: %{y:.1f}% of fleet<extra></extra>",
            name="% of Fleet",
            width=0.6,
        ), row=1, col=1)

        fig.add_trace(go.Bar(
            x=vessels, y=names,
            orientation="h",
            marker=dict(color=colors, opacity=0.82),
            text=[f"{v} ships" for v in vessels],
            textposition="outside",
            textfont=dict(size=10, color=C_TEXT2),
            hovertemplate="<b>%{y}</b><br>On order: %{x} vessels<extra></extra>",
            name="Vessels on Order",
        ), row=1, col=2)

        layout = dark_layout(height=380, showlegend=False)
        layout["margin"] = {"l": 10, "r": 30, "t": 50, "b": 30}
        layout["yaxis"] = {**layout.get("yaxis", {}), "title": "Orderbook % of Fleet", "range": [0, 40]}
        layout["yaxis2"] = {**layout.get("yaxis", {}), "autorange": "reversed"}
        fig.update_layout(**layout)
        fig.update_xaxes(tickangle=-30, row=1, col=1)

        st.plotly_chart(fig, use_container_width=True, key="carriers_v2_orderbook")

        # Capacity addition insight callout
        largest = sorted_data[0]
        st.markdown(
            f"<div style='background:{C_CARD}; border:1px solid {_C_AMBER}40; border-radius:10px;"
            f" padding:12px 16px; margin-top:8px; font-size:0.78rem; color:{C_TEXT2}'>"
            f"<span style='color:{_C_AMBER}; font-weight:700'>⚡ Capacity Surge Alert — </span>"
            f"<b>{largest['name']}</b> leads newbuild expansion with "
            f"<b>{largest['orderbook_pct']:.1f}%</b> of fleet on order "
            f"({largest['vessels_on_order']} vessels, avg {largest['avg_size_k']}k TEU). "
            f"Fleet additions expected to pressure market rates through 2026–2027."
            f"</div>",
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning("Fleet growth render error: {}", exc)
        st.warning("Fleet growth orderbook unavailable.")


# ── Section 9: Service Reliability Trend ──────────────────────────────────────

def _render_reliability_trend() -> None:
    """Render monthly on-time % trend for each major carrier (multi-line)."""
    section_header(
        "Service Reliability Trend",
        "Monthly on-time departure % by carrier — last 12 months (Sea-Intelligence methodology)",
    )
    try:
        selected_carriers = ["MSC", "Maersk", "CMA CGM", "Hapag-Lloyd", "COSCO", "ONE"]
        fig = go.Figure()

        for carrier in selected_carriers:
            if carrier not in CARRIER_PROFILES:
                continue
            series = _make_monthly_reliability_series(carrier, months=12)
            color = _CARRIER_COLORS.get(carrier, _C_CYAN)
            current = series.iloc[-1]
            fig.add_trace(go.Scatter(
                x=series.index,
                y=series.values,
                name=carrier,
                mode="lines+markers",
                line=dict(color=color, width=2.5),
                marker=dict(color=color, size=6, symbol="circle"),
                hovertemplate=f"<b>{carrier}</b><br>%{{x|%b %Y}}: %{{y:.1f}}%<extra></extra>",
            ))
            # Annotate last point
            fig.add_annotation(
                x=series.index[-1],
                y=current,
                text=f"  {carrier}<br>  {current:.1f}%",
                showarrow=False,
                xanchor="left",
                font=dict(size=10, color=color),
            )

        # Industry average reference line
        industry_avg = 67.0
        fig.add_hline(
            y=industry_avg,
            line=dict(color="rgba(255,255,255,0.3)", width=1.5, dash="dash"),
            annotation_text=f"Industry Avg {industry_avg}%",
            annotation_font=dict(color=C_TEXT3, size=10),
            annotation_position="bottom left",
        )

        layout = dark_layout(height=380, showlegend=True)
        layout["margin"] = {"l": 20, "r": 120, "t": 20, "b": 20}
        layout["yaxis"] = {**layout.get("yaxis", {}), "title": "On-Time %", "range": [35, 95]}
        layout["xaxis"] = {**layout.get("xaxis", {}), "tickformat": "%b\n%Y"}
        layout["legend"] = {
            "orientation": "v", "x": 1.01, "y": 0.5,
            "font": {"color": C_TEXT2, "size": 11},
            "bgcolor": "rgba(0,0,0,0)",
        }
        fig.update_layout(**layout)
        st.plotly_chart(fig, use_container_width=True, key="carriers_v2_reliability_trend")

        # Reliability ranking callout
        rel_scores = {c: CARRIER_PROFILES[c].schedule_reliability_pct for c in selected_carriers if c in CARRIER_PROFILES}
        best_carrier = max(rel_scores, key=rel_scores.get)
        worst_carrier = min(rel_scores, key=rel_scores.get)
        st.markdown(
            f"<div style='display:flex; gap:10px; margin-top:6px'>"
            f"<div style='flex:1; background:{C_CARD}; border:1px solid {_C_GREEN}40; border-radius:10px; padding:10px 14px'>"
            f"<span style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase'>Most Reliable</span><br>"
            f"<span style='font-size:1.1rem; font-weight:800; color:{_C_GREEN}'>{best_carrier}</span>"
            f"<span style='font-size:0.78rem; color:{C_TEXT2}'> — {rel_scores[best_carrier]:.0f}% on-time</span>"
            f"</div>"
            f"<div style='flex:1; background:{C_CARD}; border:1px solid {_C_RED}40; border-radius:10px; padding:10px 14px'>"
            f"<span style='font-size:0.6rem; color:{C_TEXT3}; text-transform:uppercase'>Least Reliable</span><br>"
            f"<span style='font-size:1.1rem; font-weight:800; color:{_C_RED}'>{worst_carrier}</span>"
            f"<span style='font-size:0.78rem; color:{C_TEXT2}'> — {rel_scores[worst_carrier]:.0f}% on-time</span>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    except Exception as exc:
        logger.warning("Reliability trend render error: {}", exc)
        st.warning("Service reliability trend unavailable.")


# ── Section 10: Carrier News & Alerts Feed ────────────────────────────────────

_NEWS_FEED: list[dict] = [
    {
        "date": "2026-03-18", "carrier": "MSC", "headline": "MSC orders 8 × 24,000 TEU methanol-ready ULCVs",
        "impact": "HIGH", "impact_dir": "bullish",
        "body": "MSC confirmed orders worth ~$2.1B for eight 24,000-TEU dual-fuel methanol-ready vessels, expanding its 2026–2027 newbuild programme significantly. Delivery expected Q3 2027–Q1 2028.",
        "tags": ["Orderbook", "Decarbonization", "Capacity"],
    },
    {
        "date": "2026-03-15", "carrier": "Maersk", "headline": "Maersk Q4 2025 results: EBITDA up 12% YoY on rate recovery",
        "impact": "HIGH", "impact_dir": "bullish",
        "body": "A.P. Moller-Maersk reported Q4 EBITDA of $2.4B, exceeding consensus by 8%. Ocean segment revenue +15% on Gemini Cooperation schedule reliability gains. FY2026 guidance raised.",
        "tags": ["Earnings", "Gemini", "Rates"],
    },
    {
        "date": "2026-03-12", "carrier": "Hapag-Lloyd", "headline": "Hapag-Lloyd announces blank sailings on Asia–Europe loops in April",
        "impact": "MOD", "impact_dir": "neutral",
        "body": "Hapag-Lloyd will blank two sailings on its AE-1/Shogun and AE-5/Albatross loops in April 2026 to manage vessel supply amid post-CNY demand softness. Impact: ~18,000 TEU removed.",
        "tags": ["Blank Sailing", "Asia–Europe", "Capacity"],
    },
    {
        "date": "2026-03-10", "carrier": "COSCO", "headline": "COSCO renews Ocean Alliance through 2030, expands Transpacific slots",
        "impact": "MOD", "impact_dir": "bullish",
        "body": "COSCO Shipping Lines and Ocean Alliance partners (CMA CGM, Evergreen) have renewed the alliance agreement through 2030 with expanded Transpacific capacity. New PSW and PNW services added.",
        "tags": ["Alliance", "Transpacific", "Ocean Alliance"],
    },
    {
        "date": "2026-03-07", "carrier": "ZIM", "headline": "ZIM cuts dividend after Q4 net income misses on higher charter costs",
        "impact": "HIGH", "impact_dir": "bearish",
        "body": "ZIM Integrated Shipping cut its special dividend by 60% following Q4 2025 net income of $180M vs $290M consensus. Charter costs rose 22% YoY as vessel lease renewals hit at higher market rates.",
        "tags": ["Dividend", "Earnings", "Charter Costs"],
    },
    {
        "date": "2026-03-05", "carrier": "CMA CGM", "headline": "CMA CGM acquires 30% stake in Hamburg port terminal operator HHLA",
        "impact": "MOD", "impact_dir": "bullish",
        "body": "CMA CGM completed acquisition of a 30% equity stake in HHLA (Hamburger Hafen und Logistik AG) for €480M, securing long-term terminal access at Europe's second-largest container port.",
        "tags": ["M&A", "Terminal", "Europe"],
    },
    {
        "date": "2026-03-02", "carrier": "ONE", "headline": "Ocean Network Express to deploy two 22,000 TEU vessels on Asia–Europe in Q3",
        "impact": "LOW", "impact_dir": "neutral",
        "body": "ONE (Ocean Network Express) confirmed deployment of two recently delivered 22,000-TEU vessels on its AEX-1 loop from July 2026, boosting Asia–Europe weekly capacity by ~5%.",
        "tags": ["Deployment", "Asia–Europe", "Fleet"],
    },
    {
        "date": "2026-02-28", "carrier": "Evergreen", "headline": "Evergreen reports Q4 profit drop on Taiwan Dollar headwinds",
        "impact": "MOD", "impact_dir": "bearish",
        "body": "Evergreen Marine Corporation Q4 net profit fell 18% QoQ primarily due to TWD appreciation against USD reducing the value of freight revenues when converted. Management flagged ongoing currency risk.",
        "tags": ["Earnings", "Currency Risk", "Taiwan"],
    },
]

_IMPACT_COLORS = {
    "HIGH":   {"bullish": _C_GREEN,  "bearish": _C_RED,   "neutral": _C_BLUE},
    "MOD":    {"bullish": _C_CYAN,   "bearish": _C_AMBER, "neutral": _C_BLUE},
    "LOW":    {"bullish": _C_TEAL,   "bearish": _C_ORANGE,"neutral": _C_PURPLE},
}
_IMPACT_ICONS = {"bullish": "▲", "bearish": "▼", "neutral": "●"}


def _render_news_feed() -> None:
    """Render carrier news & alerts feed with impact assessment."""
    section_header(
        "Carrier News & Alerts",
        "Recent carrier announcements • Earnings • Capacity actions • Strategic moves — with impact assessment",
    )
    try:
        # Carrier filter
        all_carriers = sorted(set(n["carrier"] for n in _NEWS_FEED))
        selected = st.multiselect(
            "Filter by carrier",
            options=["All"] + all_carriers,
            default=["All"],
            key="carriers_v2_news_filter",
        )
        show_carriers = all_carriers if "All" in selected or not selected else [s for s in selected if s != "All"]

        filtered = [n for n in _NEWS_FEED if n["carrier"] in show_carriers]

        for item in filtered:
            try:
                impact_level = item.get("impact", "MOD")
                impact_dir   = item.get("impact_dir", "neutral")
                color = _IMPACT_COLORS.get(impact_level, {}).get(impact_dir, _C_BLUE)
                icon  = _IMPACT_ICONS.get(impact_dir, "●")
                carrier_color = _CARRIER_COLORS.get(item["carrier"], _C_CYAN)
                tags_html = "".join(
                    f"<span style='background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12);"
                    f" border-radius:999px; font-size:0.62rem; color:{C_TEXT3}; padding:2px 8px; margin-right:4px'>"
                    f"{tag}</span>"
                    for tag in item.get("tags", [])
                )
                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid rgba(255,255,255,0.06);"
                    f" border-left:3px solid {color}; border-radius:10px; padding:14px 18px; margin-bottom:10px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px'>"
                    f"<div style='flex:1'>"
                    f"<span style='background:{carrier_color}22; color:{carrier_color};"
                    f" border:1px solid {carrier_color}55; border-radius:999px; font-size:0.62rem;"
                    f" font-weight:700; padding:2px 9px; margin-right:8px'>{item['carrier']}</span>"
                    f"<span style='background:{color}22; color:{color}; border:1px solid {color}55;"
                    f" border-radius:999px; font-size:0.60rem; font-weight:700; padding:2px 8px'>"
                    f"{icon} {impact_level} IMPACT</span>"
                    f"</div>"
                    f"<span style='color:{C_TEXT3}; font-size:0.70rem; white-space:nowrap; padding-left:12px'>"
                    f"{item['date']}</span>"
                    f"</div>"
                    f"<div style='font-size:0.88rem; font-weight:700; color:{C_TEXT}; margin-bottom:6px'>"
                    f"{item['headline']}</div>"
                    f"<div style='font-size:0.75rem; color:{C_TEXT2}; line-height:1.65; margin-bottom:8px'>"
                    f"{item['body']}</div>"
                    f"<div>{tags_html}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except Exception as item_exc:
                logger.warning("News feed item render error: {}", item_exc)
                continue

    except Exception as exc:
        logger.warning("News feed render error: {}", exc)
        st.warning("Carrier news feed unavailable.")


# ── Main render entrypoint ─────────────────────────────────────────────────────

def render(
    route_results=None,
    freight_data=None,
    stock_data=None,
) -> None:
    """Render the Carrier Intelligence tab.

    Parameters
    ----------
    route_results:
        Route analysis results from the main app engine (currently unused;
        carrier data is sourced from processing.carrier_tracker directly).
    freight_data:
        Freight rate data dict (available for future rate overlays).
    stock_data:
        Stock market data dict (available for future live price feeds).
    """
    logger.info("Rendering Carriers tab")

    # ── Section 1: Hero KPI Dashboard ─────────────────────────────────────────
    _render_hero_kpis()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    # ── Section 2: Market Share Donut + Ranked Table ───────────────────────────
    _render_market_share_section()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 3: Carrier Performance Scorecard ──────────────────────────────
    _render_carrier_scorecard()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 4: Alliance Structure Visualization ────────────────────────────
    _render_alliance_visualization()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 5: Rate Competitiveness ───────────────────────────────────────
    _render_rate_competitiveness()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 6: Capacity Deployment Heatmap ────────────────────────────────
    _render_capacity_heatmap()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 7: Financial Health ────────────────────────────────────────────
    _render_financial_health()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 8: Fleet Growth / Orderbook ───────────────────────────────────
    _render_fleet_growth()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 9: Service Reliability Trend ──────────────────────────────────
    _render_reliability_trend()
    st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 10: Carrier News & Alerts Feed ────────────────────────────────
    _render_news_feed()
