"""ui/tab_scorecard.py — Executive Scorecard (Bloomberg-style one-page summary).

render(port_results, route_results, insights, freight_data, macro_data, stock_data)

Sections
--------
1. Top Banner       — 4 mega-stats (Freight Index, Demand Signal, Alpha Score, Sentiment)
2. 3-column middle  — Routes table | Port heat map | Active signals
3. Bottom strip     — 6 mini-sparkline charts (BDI, Trans-Pac, Asia-Eur, ZIM, WTI, USD/CNY)
4. Alert strip      — Horizontal ticker for CRITICAL alerts (hidden if none)
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ports.demand_analyzer import PortDemandResult
from routes.optimizer import RouteOpportunity
from engine.insight import Insight

# ── Color palette ─────────────────────────────────────────────────────────────
_BG      = "#0a0f1a"
_CARD    = "#1a2235"
_BORDER  = "rgba(255,255,255,0.08)"
_GLASS   = "rgba(255,255,255,0.03)"
_TEXT    = "#f1f5f9"
_TEXT2   = "#94a3b8"
_TEXT3   = "#64748b"
_GREEN   = "#10b981"
_AMBER   = "#f59e0b"
_RED     = "#ef4444"
_BLUE    = "#3b82f6"
_PURPLE  = "#8b5cf6"
_CYAN    = "#06b6d4"

# Monospace font stack — no external load needed
_MONO    = "'SF Mono', 'Menlo', 'Courier New', Courier, monospace"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "rgba({},{},{},{})".format(r, g, b, alpha)


def _series_from_macro(macro_data: dict, key: str, tail: int = 30) -> list[float]:
    """Return a list of up to *tail* float values from macro_data[key]."""
    df = macro_data.get(key)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    df2 = df.copy()
    if "date" in df2.columns:
        df2 = df2.sort_values("date")
    if "value" not in df2.columns:
        return []
    vals = df2["value"].dropna().tolist()
    return [float(v) for v in vals[-tail:]]


def _series_from_freight(freight_data: dict, route_key: str, tail: int = 30) -> list[float]:
    """Return a list of up to *tail* float rates from freight_data[route_key]."""
    df = freight_data.get(route_key)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    df2 = df.copy()
    if "date" in df2.columns:
        df2 = df2.sort_values("date")
    col = (
        "rate_usd_per_feu" if "rate_usd_per_feu" in df2.columns else (
            "rate_usd_feu" if "rate_usd_feu" in df2.columns else (
                "value" if "value" in df2.columns else None
            )
        )
    )
    if col is None:
        return []
    vals = df2[col].dropna().tolist()
    return [float(v) for v in vals[-tail:]]


def _series_from_stock(stock_data: dict, ticker: str, tail: int = 30) -> list[float]:
    """Return up to *tail* closing prices for *ticker*."""
    df = stock_data.get(ticker)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    df2 = df.copy()
    if "date" in df2.columns:
        df2 = df2.sort_values("date")
    col = "close" if "close" in df2.columns else (
        "Close" if "Close" in df2.columns else None
    )
    if col is None:
        return []
    vals = df2[col].dropna().tolist()
    return [float(v) for v in vals[-tail:]]


def _arrow_dir(series: list[float]) -> str:
    """Return ↑, ↓, or → based on overall direction of a series."""
    if len(series) < 2:
        return "→"
    delta = series[-1] - series[0]
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def _arrow_color(arrow: str) -> str:
    if arrow == "↑":
        return _GREEN
    if arrow == "↓":
        return _RED
    return _TEXT2


def _format_last(series: list[float], fmt: str = ",.0f", prefix: str = "") -> str:
    """Format the last value in a series."""
    if not series:
        return "N/A"
    val = series[-1]
    try:
        formatted = format(val, fmt)
        return "{}{}".format(prefix, formatted)
    except Exception:
        return str(round(val, 1))


# ── Freight Index computation ─────────────────────────────────────────────────

def _compute_freight_index(freight_data: dict) -> tuple[str, str]:
    """Return (display_string, color) for the weighted freight index KPI.

    Weighted average of FBX01 / FBX03 / FBX11 vs 90-day average.
    Weights: FBX01 50 %, FBX03 30 %, FBX11 20 %.
    """
    weights = {"transpacific_eb": 0.50, "asia_europe": 0.30, "transatlantic": 0.20}
    current_weighted = 0.0
    avg_weighted = 0.0
    total_weight = 0.0

    for route_key, wt in weights.items():
        series = _series_from_freight(freight_data, route_key, tail=90)
        if len(series) < 2:
            continue
        current = series[-1]
        avg_90 = sum(series) / len(series)
        if avg_90 == 0:
            continue
        current_weighted += current * wt
        avg_weighted += avg_90 * wt
        total_weight += wt

    if total_weight == 0 or avg_weighted == 0:
        return "N/A", _TEXT2

    pct = (current_weighted / total_weight - avg_weighted / total_weight) / (avg_weighted / total_weight) * 100
    sign = "+" if pct >= 0 else ""
    arrow = "↑" if pct >= 0 else "↓"
    color = _GREEN if pct >= 0 else _RED
    display = "{}{}{:.0f}%".format(arrow, sign, pct)
    return display, color


# ── Demand signal computation ─────────────────────────────────────────────────

def _compute_demand_signal(port_results: list[PortDemandResult]) -> tuple[str, str]:
    """Return (0-100 score string, color)."""
    live = [r for r in port_results if r.has_real_data]
    if not live:
        return "N/A", _TEXT2
    avg = sum(r.demand_score for r in live) / len(live)
    score = int(avg * 100)
    if score >= 65:
        color = _GREEN
    elif score >= 40:
        color = _AMBER
    else:
        color = _RED
    return str(score), color


# ── Alpha score computation ────────────────────────────────────────────────────

def _compute_alpha_score(route_results: list[RouteOpportunity]) -> tuple[str, str]:
    """Return (0-100 best route score string, color)."""
    if not route_results:
        return "N/A", _TEXT2
    best = max(r.opportunity_score for r in route_results)
    score = int(best * 100)
    if score >= 65:
        color = _GREEN
    elif score >= 40:
        color = _AMBER
    else:
        color = _RED
    return str(score), color


# ── Sentiment computation ─────────────────────────────────────────────────────

def _compute_sentiment(insights: list[Insight]) -> tuple[str, str]:
    """Return (BULLISH/BEARISH/NEUTRAL, color) from insight scoring."""
    if not insights:
        return "NEUTRAL", _TEXT2
    avg = sum(i.score for i in insights) / len(insights)
    bullish_count = sum(1 for i in insights if i.score >= 0.65)
    bearish_count = sum(1 for i in insights if i.score < 0.35)
    total = len(insights)
    if bullish_count / total >= 0.5 or avg >= 0.60:
        return "BULLISH", _GREEN
    if bearish_count / total >= 0.5 or avg < 0.40:
        return "BEARISH", _RED
    return "NEUTRAL", _AMBER


# ── Section 1: Top Banner ─────────────────────────────────────────────────────

def _render_banner(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
) -> None:
    freight_val, freight_color = _compute_freight_index(freight_data)
    demand_val, demand_color   = _compute_demand_signal(port_results)
    alpha_val, alpha_color     = _compute_alpha_score(route_results)
    sentiment_val, sent_color  = _compute_sentiment(insights)

    # Build suffix for demand and alpha (out of 100)
    demand_suffix = "/100" if demand_val != "N/A" else ""
    alpha_suffix  = "/100" if alpha_val  != "N/A" else ""

    stats = [
        ("FREIGHT INDEX",  freight_val,  "",             freight_color, "FBX01/03/11 vs 90d avg"),
        ("DEMAND SIGNAL",  demand_val,   demand_suffix,  demand_color,  "Global port demand"),
        ("ALPHA SCORE",    alpha_val,    alpha_suffix,   alpha_color,   "Best route opportunity"),
        ("SENTIMENT",      sentiment_val, "",            sent_color,    "Insight-based signal"),
    ]

    cells_html = ""
    for i, (label, val, suffix, color, sub) in enumerate(stats):
        border_r = "border-right:1px solid rgba(255,255,255,0.06);" if i < 3 else ""
        glow = "text-shadow:0 0 24px {}55;".format(color)
        cells_html += (
            "<div style='flex:1; min-width:140px; text-align:center; "
            "padding:18px 20px; {}'>"
            "<div style='font-family:{}; font-size:2.4rem; font-weight:800; "
            "color:{}; letter-spacing:-0.03em; line-height:1; {}'>"
            "{}<span style='font-size:1.1rem; opacity:0.7'>{}</span></div>"
            "<div style='font-size:0.6rem; font-weight:700; color:{}; "
            "text-transform:uppercase; letter-spacing:0.12em; margin-top:6px'>{}</div>"
            "<div style='font-size:0.68rem; color:{}; margin-top:3px'>{}</div>"
            "</div>"
        ).format(
            border_r, _MONO,
            color, glow,
            val, suffix,
            _TEXT3, label,
            _TEXT3, sub,
        )

    st.markdown(
        "<style>"
        "@keyframes sc_pulse{"
        "0%,100%{opacity:1;transform:scale(1);}"
        "50%{opacity:0.55;transform:scale(1.3);}}"
        "</style>"
        "<div style='"
        "background:linear-gradient(135deg,{bg} 0%,{card} 60%,#0f1d35 100%);"
        "border:1px solid rgba(59,130,246,0.25);"
        "border-radius:14px;"
        "box-shadow:0 0 40px rgba(59,130,246,0.08),inset 0 1px 0 rgba(255,255,255,0.04);"
        "display:flex; flex-wrap:wrap; margin-bottom:10px;'>"
        "{cells}"
        "</div>".format(bg=_BG, card=_CARD, cells=cells_html),
        unsafe_allow_html=True,
    )


# ── Section 2 LEFT: Top 5 Routes table ───────────────────────────────────────

def _action_badge(score: float) -> tuple[str, str]:
    """Return (label, color) for action badge based on score."""
    if score >= 0.65:
        return "BUY", _GREEN
    if score >= 0.45:
        return "WATCH", _AMBER
    return "HOLD", _TEXT3


def _render_routes_column(route_results: list[RouteOpportunity]) -> None:
    st.markdown(
        "<div style='font-size:0.65rem; font-weight:700; color:{}; "
        "text-transform:uppercase; letter-spacing:0.1em; margin-bottom:8px'>"
        "TOP ROUTES</div>".format(_TEXT3),
        unsafe_allow_html=True,
    )

    if not route_results:
        st.markdown(
            "<div style='color:{}; font-size:0.8rem; padding:12px;'>No route data</div>".format(_TEXT2),
            unsafe_allow_html=True,
        )
        return

    rows_html = ""
    for rank, route in enumerate(route_results[:5], 1):
        score = route.opportunity_score
        score_pct = int(score * 100)
        action_label, action_color = _action_badge(score)

        # Score bar (0-100 mapped to 0-100% width)
        bar_w = score_pct
        bar_bg = _rgba(action_color, 0.8)

        pct_30d = route.rate_pct_change_30d
        pct_str = "{:+.1f}%".format(pct_30d * 100) if route.current_rate_usd_feu > 0 else "—"
        pct_color = _GREEN if pct_30d > 0 else (_RED if pct_30d < -0.01 else _TEXT2)

        # Truncate route name
        rname = route.route_name
        rname_short = rname[:28] + "…" if len(rname) > 28 else rname

        badge_bg   = _rgba(action_color, 0.12)
        badge_bord = _rgba(action_color, 0.35)
        row_bg     = "rgba(255,255,255,0.015)" if rank % 2 == 0 else "transparent"

        rows_html += (
            "<div style='display:flex; align-items:center; gap:8px; "
            "padding:7px 10px; background:{}; "
            "border-bottom:1px solid rgba(255,255,255,0.03);'>"
            # rank
            "<span style='font-family:{}; font-size:0.68rem; color:{}; "
            "width:14px; flex-shrink:0; font-weight:700'>#{}</span>"
            # name + bar
            "<div style='flex:1; min-width:0;'>"
            "<div style='font-size:0.75rem; color:{}; font-weight:600; "
            "white-space:nowrap; overflow:hidden; text-overflow:ellipsis'>{}</div>"
            "<div style='height:3px; background:rgba(255,255,255,0.06); "
            "border-radius:2px; margin-top:4px; overflow:hidden;'>"
            "<div style='height:3px; width:{}%; background:{}; border-radius:2px;'></div>"
            "</div></div>"
            # 30d change
            "<span style='font-family:{}; font-size:0.7rem; color:{}; "
            "white-space:nowrap; flex-shrink:0'>{}</span>"
            # action badge
            "<span style='background:{}; color:{}; border:1px solid {}; "
            "font-size:0.6rem; font-weight:800; padding:2px 7px; "
            "border-radius:999px; flex-shrink:0'>{}</span>"
            "</div>"
        ).format(
            row_bg,
            _MONO, _TEXT3, rank,
            _TEXT, rname_short,
            bar_w, bar_bg,
            _MONO, pct_color, pct_str,
            badge_bg, action_color, badge_bord, action_label,
        )

    st.markdown(
        "<div style='"
        "background:{glass}; backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);"
        "border:1px solid {border}; border-radius:10px; overflow:hidden;'>"
        "{rows}"
        "</div>".format(glass=_GLASS, border=_BORDER, rows=rows_html),
        unsafe_allow_html=True,
    )


# ── Section 2 CENTER: Port heat map ──────────────────────────────────────────

def _demand_to_color(score: float) -> str:
    """Map demand score [0,1] to a red→amber→green background rgba visible on dark backgrounds.

    Low  (0.00–0.39): red family,   alpha 0.25–0.35
    Mid  (0.40–0.64): amber family, alpha 0.22–0.32
    High (0.65–1.00): green family, alpha 0.28–0.70
    """
    score = max(0.0, min(1.0, score))
    if score >= 0.65:
        # green: rgb(16, 185, 129) = _GREEN
        t = (score - 0.65) / 0.35          # 0 → 1
        r = int(10 + t * 6)                # 10 → 16
        g = int(120 + t * 65)              # 120 → 185
        b = int(60 + t * 69)               # 60 → 129
        alpha = 0.28 + t * 0.42            # 0.28 → 0.70
    elif score >= 0.40:
        # amber: rgb(245, 158, 11) = _AMBER
        t = (score - 0.40) / 0.25          # 0 → 1
        r = int(180 + t * 65)              # 180 → 245
        g = int(100 + t * 58)              # 100 → 158
        b = int(8 + t * 3)                 # 8 → 11
        alpha = 0.22 + t * 0.10            # 0.22 → 0.32
    else:
        # red: rgb(239, 68, 68) = _RED
        t = score / 0.40                   # 0 → 1
        r = int(180 + t * 59)              # 180 → 239
        g = int(30 + t * 38)               # 30 → 68
        b = int(30 + t * 38)               # 30 → 68
        alpha = 0.25 + t * 0.10            # 0.25 → 0.35
    return "rgba({},{},{},{:.2f})".format(r, g, b, alpha)


def _render_heatmap_column(port_results: list[PortDemandResult]) -> None:
    st.markdown(
        "<div style='font-size:0.65rem; font-weight:700; color:{}; "
        "text-transform:uppercase; letter-spacing:0.1em; margin-bottom:8px'>"
        "PORT DEMAND HEAT MAP</div>".format(_TEXT3),
        unsafe_allow_html=True,
    )

    # Pick the 16 most interesting ports (has_real_data first, then by score)
    live   = sorted([r for r in port_results if r.has_real_data], key=lambda r: r.demand_score, reverse=True)
    others = [r for r in port_results if not r.has_real_data]
    pool   = (live + others)[:16]

    # Pad to 16 with placeholders
    while len(pool) < 16:
        pool.append(None)

    cells_html = ""
    for item in pool:
        if item is None:
            cells_html += "<div style='background:rgba(255,255,255,0.02); border-radius:6px;'></div>"
            continue

        score  = item.demand_score if item.has_real_data else 0.0
        bg     = _demand_to_color(score)
        # Border and text colour follow the same green/amber/red tier as the background
        tier_color = _GREEN if score >= 0.65 else (_AMBER if score >= 0.40 else _RED)
        border = _rgba(tier_color, 0.30 + score * 0.25)
        label  = item.demand_label if item.has_real_data else "—"

        # Abbreviate port name to fit cell
        name = item.port_name
        name_short = name[:10] + "…" if len(name) > 10 else name

        score_txt = "{:.0f}".format(score * 100) if item.has_real_data else "?"
        txt_color = tier_color

        cells_html += (
            "<div style='background:{}; border:1px solid {}; border-radius:6px; "
            "padding:6px 4px; display:flex; flex-direction:column; align-items:center; "
            "justify-content:center; text-align:center; min-height:52px;'>"
            "<div style='font-size:0.6rem; color:{}; font-weight:600; "
            "line-height:1.2; word-break:break-word'>{}</div>"
            "<div style='font-family:{}; font-size:0.75rem; font-weight:800; "
            "color:{}; margin-top:2px'>{}</div>"
            "</div>"
        ).format(bg, border, _TEXT2, name_short, _MONO, txt_color, score_txt)

    st.markdown(
        "<div style='"
        "background:{glass}; backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);"
        "border:1px solid {border}; border-radius:10px; padding:10px;'>"
        "<div style='display:grid; grid-template-columns:repeat(4,1fr); gap:5px;'>"
        "{cells}"
        "</div></div>".format(glass=_GLASS, border=_BORDER, cells=cells_html),
        unsafe_allow_html=True,
    )


# ── Section 2 RIGHT: Active signals ──────────────────────────────────────────

_CAT_ICONS = {
    "CONVERGENCE": "🔥",
    "ROUTE":       "⚡",
    "PORT_DEMAND": "🏗️",
    "MACRO":       "📊",
}
_CAT_COLORS = {
    "CONVERGENCE": _PURPLE,
    "ROUTE":       _BLUE,
    "PORT_DEMAND": _GREEN,
    "MACRO":       _CYAN,
}


def _render_signals_column(insights: list[Insight]) -> None:
    st.markdown(
        "<div style='font-size:0.65rem; font-weight:700; color:{}; "
        "text-transform:uppercase; letter-spacing:0.1em; margin-bottom:8px'>"
        "ACTIVE SIGNALS</div>".format(_TEXT3),
        unsafe_allow_html=True,
    )

    if not insights:
        st.markdown(
            "<div style='color:{}; font-size:0.8rem; padding:12px;'>No signals</div>".format(_TEXT2),
            unsafe_allow_html=True,
        )
        return

    cards_html = ""
    for idx, ins in enumerate(insights[:5]):
        icon     = _CAT_ICONS.get(ins.category, "📌")
        color    = _CAT_COLORS.get(ins.category, _BLUE)
        score_pct = int(ins.score * 100)
        score_color = _GREEN if ins.score >= 0.65 else (_AMBER if ins.score >= 0.40 else _RED)

        badge_bg   = _rgba(score_color, 0.12)
        badge_bord = _rgba(score_color, 0.35)
        row_bg     = "rgba(255,255,255,0.015)" if idx % 2 == 0 else "transparent"

        # Title truncated to fit one line
        title_short = ins.title[:52] + "…" if len(ins.title) > 52 else ins.title

        cards_html += (
            "<div style='display:flex; align-items:center; gap:8px; "
            "padding:8px 10px; background:{}; border-left:2px solid {}; "
            "border-bottom:1px solid rgba(255,255,255,0.03);'>"
            "<span style='font-size:0.9rem; flex-shrink:0; line-height:1'>{}</span>"
            "<div style='flex:1; min-width:0;'>"
            "<div style='font-size:0.72rem; color:{}; font-weight:600; "
            "white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:1.3'>{}</div>"
            "<div style='font-size:0.62rem; color:{}; margin-top:2px; "
            "text-transform:uppercase; letter-spacing:0.04em'>{}</div>"
            "</div>"
            "<span style='font-family:{}; font-size:0.68rem; font-weight:800; "
            "color:{}; background:{}; border:1px solid {}; "
            "padding:1px 6px; border-radius:999px; flex-shrink:0'>{}%</span>"
            "</div>"
        ).format(
            row_bg, color,
            icon,
            _TEXT, title_short,
            _TEXT3, ins.action,
            _MONO, score_color, badge_bg, badge_bord, score_pct,
        )

    st.markdown(
        "<div style='"
        "background:{glass}; backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);"
        "border:1px solid {border}; border-radius:10px; overflow:hidden;'>"
        "{cards}"
        "</div>".format(glass=_GLASS, border=_BORDER, cards=cards_html),
        unsafe_allow_html=True,
    )


# ── Score table + CSV download ───────────────────────────────────────────────

def _render_score_table_download(
    route_results: list[RouteOpportunity],
    port_results: list[PortDemandResult],
) -> None:
    """Collapsible section with the full route/port score table and a CSV download button."""
    if not route_results and not port_results:
        return

    with st.expander("Score Table & CSV Export", expanded=False):
        rows: list[dict] = []

        for r in route_results:
            action_label, _ = _action_badge(r.opportunity_score)
            rows.append({
                "Type": "Route",
                "Name": r.route_name,
                "ID": r.route_id,
                "Origin": r.origin_locode,
                "Destination": r.dest_locode,
                "Opportunity Score": round(r.opportunity_score, 4),
                "Score %": f"{r.opportunity_score * 100:.0f}%",
                "Action": action_label,
                "Rate (USD/FEU)": r.current_rate_usd_feu if r.current_rate_usd_feu > 0 else None,
                "Rate Trend": r.rate_trend,
                "30d Change %": f"{r.rate_pct_change_30d * 100:+.1f}%" if r.current_rate_usd_feu > 0 else "—",
                "Transit Days": r.transit_days,
                "FBX Index": r.fbx_index,
                "Generated At": r.generated_at,
            })

        for p in port_results:
            demand_score = p.demand_score if p.has_real_data else None
            rows.append({
                "Type": "Port",
                "Name": p.port_name,
                "ID": p.locode,
                "Origin": p.locode,
                "Destination": "",
                "Opportunity Score": demand_score,
                "Score %": f"{demand_score * 100:.0f}%" if demand_score is not None else "N/A",
                "Action": p.demand_label if p.has_real_data else "—",
                "Rate (USD/FEU)": None,
                "Rate Trend": getattr(p, "demand_trend", ""),
                "30d Change %": "—",
                "Transit Days": None,
                "FBX Index": "",
                "Generated At": "",
            })

        df_table = pd.DataFrame(rows)

        # Display styled table
        st.dataframe(
            df_table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Opportunity Score": st.column_config.ProgressColumn(
                    "Score",
                    min_value=0.0,
                    max_value=1.0,
                    format="%.0f%%",
                ),
                "Rate (USD/FEU)": st.column_config.NumberColumn(
                    "Rate USD/FEU",
                    format="$%,.0f",
                ),
            },
        )

        # CSV download
        csv_bytes = df_table.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download CSV",
            data=csv_bytes,
            file_name="scorecard_scores.csv",
            mime="text/csv",
            key="scorecard_csv_download",
        )


# ── Section 3: Mini sparkline charts ─────────────────────────────────────────

def _build_sparkline(series: list[float], color: str = _BLUE) -> go.Figure:
    """Return a bare-bones Plotly Scatter sparkline (height=60, no axes)."""
    if not series:
        series = [0.0]

    fill_color = _rgba(color, 0.18)
    fig = go.Figure(
        go.Scatter(
            y=series,
            mode="lines",
            line=dict(color=color, width=1.5),
            fill="tozeroy",
            fillcolor=fill_color,
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=60,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        xaxis=dict(visible=False, showgrid=False, zeroline=False, fixedrange=True),
        yaxis=dict(visible=False, showgrid=False, zeroline=False, fixedrange=True),
    )
    return fig


def _render_sparkline_strip(
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
) -> None:
    """Render 6-column mini-chart strip."""
    # Gather series
    bdi_series    = _series_from_macro(macro_data, "BSXRLM")
    tp_series     = _series_from_freight(freight_data, "transpacific_eb")
    ae_series     = _series_from_freight(freight_data, "asia_europe")
    zim_series    = _series_from_stock(stock_data, "ZIM")
    wti_series    = _series_from_macro(macro_data, "DCOILWTICO")
    cny_series    = _series_from_macro(macro_data, "DEXCHUS")  # USD/CNY from FRED

    specs = [
        ("BDI",              bdi_series,  _AMBER,  ",.0f",  ""),
        ("TRANS-PAC",        tp_series,   _BLUE,   ",.0f",  "$"),
        ("ASIA-EUR",         ae_series,   _PURPLE, ",.0f",  "$"),
        ("ZIM",              zim_series,  _GREEN,  ".2f",   "$"),
        ("WTI CRUDE",        wti_series,  _CYAN,   ".1f",   "$"),
        ("USD/CNY",          cny_series,  _AMBER,  ".3f",   ""),
    ]
    cols = st.columns(6)
    for col, (title, series, color, fmt, prefix) in zip(cols, specs):
        arrow = _arrow_dir(series)
        arrow_color = _arrow_color(arrow)
        last_val = _format_last(series, fmt, prefix)

        col.markdown(
            "<div style='"
            "background:{glass}; backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);"
            "border:1px solid {border}; border-radius:8px; padding:8px 10px 4px 10px;'>"
            "<div style='display:flex; justify-content:space-between; align-items:baseline;'>"
            "<span style='font-size:0.58rem; font-weight:700; color:{t3}; "
            "text-transform:uppercase; letter-spacing:0.08em'>{title}</span>"
            "<span style='font-size:0.62rem; color:{ac}; font-weight:700'>{arrow}</span>"
            "</div>"
            "<div style='font-family:{mono}; font-size:0.88rem; font-weight:700; "
            "color:{val_c}; line-height:1; margin-top:2px'>{val}</div>"
            "</div>".format(
                glass=_GLASS, border=_BORDER,
                t3=_TEXT3,
                title=title,
                ac=arrow_color, arrow=arrow,
                mono=_MONO,
                val_c=arrow_color, val=last_val,
            ),
            unsafe_allow_html=True,
        )

        fig = _build_sparkline(series, color)
        col.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False, "staticPlot": True},
            key=f"scorecard_sparkline_{title}",
        )


# ── Section 4: Alert strip ────────────────────────────────────────────────────

def _render_alert_strip(alerts: list) -> None:
    """Render horizontal scrolling ticker of CRITICAL alerts. Hidden if none."""
    critical = [a for a in alerts if getattr(a, "severity", "") == "CRITICAL"]
    if not critical:
        return

    items_html = ""
    for alert in critical:
        icon  = getattr(alert, "icon",  "🚨")
        title = getattr(alert, "title", "Alert")
        msg   = getattr(alert, "message", "")
        short = (title + " — " + msg)[:80]

        items_html += (
            "<span style='display:inline-flex; align-items:center; gap:8px; "
            "padding:0 24px; white-space:nowrap; font-size:0.78rem;'>"
            "<span style='animation:sc_pulse 1.2s ease-in-out infinite; "
            "display:inline-block'>{}</span>"
            "<span style='color:#fca5a5; font-weight:600'>{}</span>"
            "<span style='color:rgba(255,255,255,0.2)'>|</span>"
            "</span>"
        ).format(icon, short)

    # Duplicate for seamless loop
    ticker_content = items_html * 3
    duration = max(12, len(critical) * 6)

    st.markdown(
        "<div style='overflow:hidden; background:rgba(185,28,28,0.12); "
        "border:1px solid rgba(239,68,68,0.35); border-radius:8px; "
        "padding:7px 0; margin-bottom:8px;'>"
        "<div style='display:inline-flex; "
        "animation:ticker-scroll {}s linear infinite;'>"
        "{}"
        "</div></div>".format(duration, ticker_content),
        unsafe_allow_html=True,
    )


# ── CSS injection ─────────────────────────────────────────────────────────────

def _inject_scorecard_css() -> None:
    st.markdown(
        "<style>"
        "@keyframes sc_pulse{"
        "0%,100%{opacity:1;transform:scale(1);}"
        "50%{opacity:0.45;transform:scale(1.35);}}"
        "@keyframes ticker-scroll{"
        "0%{transform:translateX(0);}"
        "100%{transform:translateX(-33.33%);}}"
        "/* suppress Plotly chart bottom padding */"
        ".js-plotly-plot .plotly{margin-bottom:0!important;}"
        "</style>",
        unsafe_allow_html=True,
    )


# ── Public entry point ────────────────────────────────────────────────────────

def render(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: Optional[dict] = None,
    macro_data: Optional[dict] = None,
    stock_data: Optional[dict] = None,
) -> None:
    """Render the executive scorecard tab.

    Args:
        port_results:  List of PortDemandResult (all tracked ports).
        route_results: List of RouteOpportunity sorted by score desc.
        insights:      List of Insight objects from the decision engine.
        freight_data:  dict[route_key, pd.DataFrame] from freight_scraper.
        macro_data:    dict[series_id, pd.DataFrame] from fred_feed.
        stock_data:    dict[ticker, pd.DataFrame] from stock_feed.
    """
    freight_data = freight_data or {}
    macro_data   = macro_data   or {}
    stock_data   = stock_data   or {}

    if not port_results and not route_results and not insights:
        st.info(
            "No scorecard data available yet. "
            "Check API credentials and click Refresh to load routes and port data."
        )
        return

    logger.debug(
        "Rendering scorecard: {} ports, {} routes, {} insights",
        len(port_results), len(route_results), len(insights),
    )

    _inject_scorecard_css()

    # ── Section 1: Banner ─────────────────────────────────────────────────
    _render_banner(port_results, route_results, insights, freight_data)

    # ── Section 2: 3-column middle ────────────────────────────────────────
    col_left, col_center, col_right = st.columns([1, 1, 1])

    with col_left:
        _render_routes_column(route_results)

    with col_center:
        _render_heatmap_column(port_results)

    with col_right:
        _render_signals_column(insights)

    # ── Score table download ──────────────────────────────────────────────
    _render_score_table_download(route_results, port_results)

    # ── Section 3: Sparkline strip ────────────────────────────────────────
    st.markdown(
        "<div style='margin-top:10px;'></div>",
        unsafe_allow_html=True,
    )
    _render_sparkline_strip(freight_data, macro_data, stock_data)

    # ── Section 4: Alert strip ────────────────────────────────────────────
    # Pull alerts from session state if available (populated by alert_engine)
    alerts = []
    try:
        from engine.alert_engine import generate_alerts
        alerts = generate_alerts(port_results, route_results, freight_data, macro_data, insights)
    except Exception as exc:
        logger.debug("Alert engine not available in scorecard: {}", exc)

    _render_alert_strip(alerts)
