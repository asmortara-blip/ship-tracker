"""ui/tab_scorecard.py — Executive Scorecard (Bloomberg-style one-page summary).

render(port_results, route_results, insights, freight_data, macro_data, stock_data)

Sections
--------
1.  Alert Strip          — Horizontal ticker for CRITICAL alerts (top, hidden if none)
2.  Executive Health     — Plotly donut gauge + letter grade card + 4 sub-KPI tiles
3.  KPI Grid             — 10 metric cards (5-col), top-border accent, 7d change, traffic-light
4.  Category Scorecards  — 5 categories with SVG arc bars + letter grades
5.  Period-over-Period   — HTML table: Now vs 1W Ago vs 1M Ago
6.  Benchmark Table      — Current vs historical norm, vs-norm % with traffic-light
7.  Sparkline Strip      — 6 trend charts with 7d change labels + color-accent top borders
8.  Intelligence Panel   — Routes table | Port Heatmap | Active Signals
9.  AI Action Items      — Prioritized cards in 2-column layout
10. Download / Export    — CSV + JSON download buttons
"""
from __future__ import annotations

import json
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
_ORANGE  = "#f97316"
_PINK    = "#ec4899"

_MONO    = "'SF Mono', 'Menlo', 'Courier New', Courier, monospace"
_SANS    = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "rgba({},{},{},{})".format(r, g, b, alpha)


def _series_from_macro(macro_data: dict, key: str, tail: int = 30) -> list[float]:
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


def _pct_change_7d(series: list[float]) -> Optional[float]:
    """Return % change between last value and value 7 steps ago (or None)."""
    if len(series) < 2:
        return None
    anchor = series[-8] if len(series) >= 8 else series[0]
    if anchor == 0:
        return None
    return (series[-1] - anchor) / abs(anchor) * 100


def _format_last(series: list[float], fmt: str = ",.0f", prefix: str = "") -> str:
    if not series:
        return "N/A"
    val = series[-1]
    try:
        formatted = format(val, fmt)
        return "{}{}".format(prefix, formatted)
    except Exception:
        return str(round(val, 1))


def _traffic_light(score_0_1: float) -> tuple[str, str]:
    """Return (label, color) for a 0-1 score."""
    if score_0_1 >= 0.65:
        return "STRONG", _GREEN
    if score_0_1 >= 0.40:
        return "FAIR", _AMBER
    return "WEAK", _RED


def _letter_grade(score_0_1: float) -> tuple[str, str]:
    """Return (letter, color) from score 0-1."""
    if score_0_1 >= 0.90:
        return "A+", _GREEN
    if score_0_1 >= 0.80:
        return "A",  _GREEN
    if score_0_1 >= 0.70:
        return "B+", "#4ade80"
    if score_0_1 >= 0.60:
        return "B",  _CYAN
    if score_0_1 >= 0.50:
        return "C+", _AMBER
    if score_0_1 >= 0.40:
        return "C",  _AMBER
    if score_0_1 >= 0.30:
        return "D",  _ORANGE
    return "F", _RED


# ── Core metric computations ──────────────────────────────────────────────────

def _compute_freight_index(freight_data: dict) -> tuple[float, str, str]:
    """Return (score_0_1, display_str, color)."""
    weights = {"transpacific_eb": 0.50, "asia_europe": 0.30, "transatlantic": 0.20}
    current_w = avg_w = total_w = 0.0
    for route_key, wt in weights.items():
        series = _series_from_freight(freight_data, route_key, tail=90)
        if len(series) < 2:
            continue
        current = series[-1]
        avg_90 = sum(series) / len(series)
        if avg_90 == 0:
            continue
        current_w += current * wt
        avg_w += avg_90 * wt
        total_w += wt
    if total_w == 0 or avg_w == 0:
        return 0.5, "N/A", _TEXT2
    pct = (current_w / total_w - avg_w / total_w) / (avg_w / total_w) * 100
    score = max(0.0, min(1.0, 0.5 + pct / 200.0))
    sign = "+" if pct >= 0 else ""
    arrow = "↑" if pct >= 0 else "↓"
    color = _GREEN if pct >= 0 else _RED
    return score, "{}{}{:.0f}%".format(arrow, sign, pct), color


def _compute_demand_signal(port_results: list[PortDemandResult]) -> tuple[float, str, str]:
    live = [r for r in port_results if r.has_real_data]
    if not live:
        return 0.5, "N/A", _TEXT2
    avg = sum(r.demand_score for r in live) / len(live)
    color = _GREEN if avg >= 0.65 else (_AMBER if avg >= 0.40 else _RED)
    return avg, str(int(avg * 100)), color


def _compute_alpha_score(route_results: list[RouteOpportunity]) -> tuple[float, str, str]:
    if not route_results:
        return 0.5, "N/A", _TEXT2
    best = max(r.opportunity_score for r in route_results)
    color = _GREEN if best >= 0.65 else (_AMBER if best >= 0.40 else _RED)
    return best, str(int(best * 100)), color


def _compute_sentiment(insights: list[Insight]) -> tuple[float, str, str]:
    if not insights:
        return 0.5, "NEUTRAL", _TEXT2
    avg = sum(i.score for i in insights) / len(insights)
    bullish = sum(1 for i in insights if i.score >= 0.65)
    bearish = sum(1 for i in insights if i.score < 0.35)
    total = len(insights)
    if bullish / total >= 0.5 or avg >= 0.60:
        return avg, "BULLISH", _GREEN
    if bearish / total >= 0.5 or avg < 0.40:
        return avg, "BEARISH", _RED
    return avg, "NEUTRAL", _AMBER


def _compute_overall_health(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
) -> float:
    """Return composite health score 0-1."""
    scores = []
    fi_score, _, _ = _compute_freight_index(freight_data)
    scores.append(fi_score)
    ds_score, dv, _ = _compute_demand_signal(port_results)
    if dv != "N/A":
        scores.append(ds_score)
    as_score, av, _ = _compute_alpha_score(route_results)
    if av != "N/A":
        scores.append(as_score)
    sent_score, _, _ = _compute_sentiment(insights)
    scores.append(sent_score)
    return sum(scores) / len(scores) if scores else 0.5


def _action_badge(score: float) -> tuple[str, str]:
    if score >= 0.65:
        return "BUY", _GREEN
    if score >= 0.45:
        return "WATCH", _AMBER
    return "HOLD", _TEXT3


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
        "@keyframes fadeInUp{"
        "from{opacity:0;transform:translateY(12px);}"
        "to{opacity:1;transform:translateY(0);}}"
        ".sc-card{animation:fadeInUp 0.4s ease both;}"
        ".js-plotly-plot .plotly{margin-bottom:0!important;}"
        ".sc-section-title{"
        "font-size:0.6rem;font-weight:700;color:#64748b;"
        "text-transform:uppercase;letter-spacing:0.12em;"
        "margin:18px 0 8px 0;display:flex;align-items:center;gap:8px;}"
        ".sc-section-title::after{"
        "content:'';flex:1;height:1px;background:rgba(255,255,255,0.06);}"
        "</style>",
        unsafe_allow_html=True,
    )


def _section_title(title: str, icon: str = "") -> None:
    st.markdown(
        "<div class='sc-section-title'>{}{}</div>".format(
            icon + " " if icon else "", title
        ),
        unsafe_allow_html=True,
    )


# ── Section 1: Alert Strip (top) ──────────────────────────────────────────────

def _render_alert_strip(alerts: list) -> None:
    try:
        critical = [a for a in alerts if getattr(a, "severity", "") == "CRITICAL"]
        if not critical:
            return
        items_html = ""
        for alert in critical:
            icon  = getattr(alert, "icon",  "🚨")
            title = getattr(alert, "title", "Alert")
            msg   = getattr(alert, "message", "")
            short = (title + " — " + msg)[:90]
            items_html += (
                "<span style='display:inline-flex;align-items:center;gap:8px;"
                "padding:0 28px;white-space:nowrap;font-size:0.78rem;'>"
                "<span style='animation:sc_pulse 1.2s ease-in-out infinite;"
                "display:inline-block'>{}</span>"
                "<span style='color:#fca5a5;font-weight:600'>{}</span>"
                "<span style='color:rgba(255,255,255,0.18);margin-left:8px'>|</span>"
                "</span>"
            ).format(icon, short)
        ticker_content = items_html * 3
        duration = max(14, len(critical) * 7)
        st.markdown(
            "<div style='overflow:hidden;background:rgba(185,28,28,0.13);"
            "border:1px solid rgba(239,68,68,0.38);border-radius:8px;"
            "padding:8px 0;margin-bottom:10px;'>"
            "<div style='display:inline-flex;"
            "animation:ticker-scroll {}s linear infinite;'>"
            "{}"
            "</div></div>".format(duration, ticker_content),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.debug("Alert strip render error: {}", exc)


# ── Section 2: Executive Health Gauge ────────────────────────────────────────

def _render_executive_health(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
) -> None:
    try:
        _section_title("EXECUTIVE HEALTH OVERVIEW", "◈")

        overall = _compute_overall_health(port_results, route_results, insights, freight_data)
        letter, letter_color = _letter_grade(overall)
        score_pct = int(overall * 100)

        fi_score, fi_val, fi_color = _compute_freight_index(freight_data)
        ds_score, ds_val, ds_color = _compute_demand_signal(port_results)
        as_score, as_val, as_color = _compute_alpha_score(route_results)
        sent_score, sent_val, sent_color = _compute_sentiment(insights)

        # Gauge color based on score
        gauge_color = (
            _GREEN if overall >= 0.65 else
            _AMBER if overall >= 0.40 else
            _RED
        )

        # Plotly donut gauge
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score_pct,
            number={"suffix": "", "font": {"size": 36, "color": gauge_color, "family": _MONO}},
            gauge={
                "axis": {
                    "range": [0, 100],
                    "tickwidth": 0,
                    "tickcolor": "transparent",
                    "tickvals": [],
                },
                "bar": {"color": gauge_color, "thickness": 0.72},
                "bgcolor": "rgba(255,255,255,0.04)",
                "borderwidth": 0,
                "steps": [
                    {"range": [0,  40],  "color": _rgba(_RED,   0.12)},
                    {"range": [40, 65],  "color": _rgba(_AMBER, 0.12)},
                    {"range": [65, 100], "color": _rgba(_GREEN, 0.12)},
                ],
                "threshold": {
                    "line": {"color": gauge_color, "width": 3},
                    "thickness": 0.85,
                    "value": score_pct,
                },
                "shape": "angular",
            },
            title={
                "text": "HEALTH SCORE",
                "font": {"size": 10, "color": _TEXT3, "family": _SANS},
            },
            domain={"x": [0, 1], "y": [0, 1]},
        ))
        fig.update_layout(
            template="plotly_dark",
            height=200,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=20, t=30, b=10),
            font={"family": _MONO},
        )

        col_gauge, col_grade, col_kpis = st.columns([2, 1, 3])

        with col_gauge:
            st.plotly_chart(
                fig,
                use_container_width=True,
                config={"displayModeBar": False, "staticPlot": True},
                key="scorecard_health_gauge",
            )

        with col_grade:
            glow = "0 0 40px {}60".format(letter_color)
            st.markdown(
                "<div style='"
                "background:linear-gradient(135deg,{card} 0%,{bg2} 100%);"
                "border:1px solid {bc};"
                "border-radius:14px;padding:20px 12px;"
                "text-align:center;height:100%;display:flex;"
                "flex-direction:column;justify-content:center;align-items:center;"
                "box-shadow:{glow};'>"
                "<div style='font-size:0.55rem;font-weight:700;color:{t3};"
                "text-transform:uppercase;letter-spacing:0.14em;margin-bottom:8px'>"
                "OVERALL GRADE</div>"
                "<div style='font-family:{mono};font-size:3.8rem;font-weight:900;"
                "color:{lc};line-height:1;text-shadow:{glow}'>{letter}</div>"
                "<div style='font-family:{mono};font-size:1.05rem;color:{lc};"
                "font-weight:700;margin-top:6px;opacity:0.85'>{pct}/100</div>"
                "</div>".format(
                    card=_CARD, bg2="#0f1d35",
                    bc=_rgba(letter_color, 0.35),
                    glow=glow,
                    t3=_TEXT3, mono=_MONO,
                    lc=letter_color,
                    letter=letter, pct=score_pct,
                ),
                unsafe_allow_html=True,
            )

        with col_kpis:
            sub_kpis = [
                ("FREIGHT INDEX", fi_val, "", fi_color, "FBX weighted vs 90d avg"),
                ("DEMAND SIGNAL", ds_val, "/100" if ds_val != "N/A" else "", ds_color, "Global port demand composite"),
                ("ALPHA SCORE",   as_val, "/100" if as_val != "N/A" else "", as_color, "Best route opportunity"),
                ("SENTIMENT",     sent_val, "", sent_color, "Insight-weighted signal"),
            ]
            tiles_html = ""
            for label, val, suffix, color, sub in sub_kpis:
                tl_label, tl_color = _traffic_light(
                    ds_score if label == "DEMAND SIGNAL" else
                    as_score if label == "ALPHA SCORE" else
                    sent_score if label == "SENTIMENT" else
                    fi_score
                )
                badge = (
                    "<span style='background:{bg};color:{c};border:1px solid {bc};"
                    "font-size:0.52rem;font-weight:800;padding:1px 6px;"
                    "border-radius:999px;margin-left:4px'>{lbl}</span>"
                ).format(
                    bg=_rgba(tl_color, 0.12), c=tl_color,
                    bc=_rgba(tl_color, 0.35), lbl=tl_label,
                )
                glow2 = "text-shadow:0 0 18px {}50".format(color)
                tiles_html += (
                    "<div style='"
                    "background:{card};border:1px solid {bc};"
                    "border-top:3px solid {c};"
                    "border-radius:10px;padding:14px 16px;"
                    "flex:1;min-width:130px;'>"
                    "<div style='font-size:0.55rem;font-weight:700;color:{t3};"
                    "text-transform:uppercase;letter-spacing:0.12em;"
                    "display:flex;align-items:center;gap:4px'>"
                    "{label}{badge}</div>"
                    "<div style='font-family:{mono};font-size:1.55rem;font-weight:800;"
                    "color:{c};line-height:1;margin-top:8px;{glow}'>"
                    "{val}<span style='font-size:0.75rem;opacity:0.7'>{suffix}</span></div>"
                    "<div style='font-size:0.6rem;color:{t3};margin-top:4px'>{sub}</div>"
                    "</div>"
                ).format(
                    card=_CARD, bc=_rgba(color, 0.25), c=color,
                    t3=_TEXT3, mono=_MONO, glow=glow2,
                    label=label, badge=badge,
                    val=val, suffix=suffix, sub=sub,
                )
            st.markdown(
                "<div style='display:flex;flex-wrap:wrap;gap:8px;height:100%;"
                "align-content:center;'>{}</div>".format(tiles_html),
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.warning("Executive health render error: {}", exc)
        st.warning("Executive health overview unavailable.")


# ── Section 3: KPI Grid (10 cards, 5-col) ────────────────────────────────────

def _render_kpi_grid(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
) -> None:
    try:
        _section_title("KEY PERFORMANCE INDICATORS", "▦")

        bdi_s  = _series_from_macro(macro_data, "BSXRLM")
        tp_s   = _series_from_freight(freight_data, "transpacific_eb")
        ae_s   = _series_from_freight(freight_data, "asia_europe")
        ta_s   = _series_from_freight(freight_data, "transatlantic")
        zim_s  = _series_from_stock(stock_data, "ZIM")
        wti_s  = _series_from_macro(macro_data, "DCOILWTICO")
        cny_s  = _series_from_macro(macro_data, "DEXCHUS")
        maer_s = _series_from_stock(stock_data, "MAERSK-B.CO")
        hlag_s = _series_from_stock(stock_data, "HLAG.DE")
        cpri_s = _series_from_freight(freight_data, "china_us_west")

        # (label, series, fmt, prefix, color_accent, higher_is_better)
        kpis = [
            ("BDI",           bdi_s,  ",.0f", "",  _AMBER,  True),
            ("TRANS-PAC",     tp_s,   ",.0f", "$", _BLUE,   True),
            ("ASIA-EUR",      ae_s,   ",.0f", "$", _PURPLE, True),
            ("TRANS-ATL",     ta_s,   ",.0f", "$", _CYAN,   True),
            ("ZIM STOCK",     zim_s,  ".2f",  "$", _GREEN,  True),
            ("WTI CRUDE",     wti_s,  ".1f",  "$", _ORANGE, False),
            ("USD/CNY",       cny_s,  ".3f",  "",  _AMBER,  False),
            ("MAERSK",        maer_s, ".0f",  "$", _BLUE,   True),
            ("HAPAG-LLOYD",   hlag_s, ".0f",  "$", _CYAN,   True),
            ("CHINA-US W",    cpri_s, ",.0f", "$", _GREEN,  True),
        ]

        rows = [kpis[:5], kpis[5:]]
        for row_kpis in rows:
            cols = st.columns(5)
            for col, (label, series, fmt, prefix, accent, higher_good) in zip(cols, row_kpis):
                last_val = _format_last(series, fmt, prefix)
                pct_7d = _pct_change_7d(series)
                arrow = _arrow_dir(series)

                if pct_7d is not None:
                    positive = pct_7d >= 0
                    is_good = positive if higher_good else not positive
                    pct_color = _GREEN if is_good else _RED
                    pct_str = "{:+.1f}%".format(pct_7d)
                    tl_score = 0.7 if is_good else 0.3
                else:
                    pct_color = _TEXT3
                    pct_str = "—"
                    tl_score = 0.5

                tl_label, tl_color = _traffic_light(tl_score)
                ac_color = arrow_color = _GREEN if arrow == "↑" else (_RED if arrow == "↓" else _TEXT2)

                col.markdown(
                    "<div class='sc-card' style='"
                    "background:{card};"
                    "border:1px solid {bc};"
                    "border-top:3px solid {accent};"
                    "border-radius:10px;padding:14px 14px 12px 14px;"
                    "margin-bottom:2px;'>"
                    # Header row
                    "<div style='display:flex;justify-content:space-between;"
                    "align-items:center;margin-bottom:8px;'>"
                    "<span style='font-size:0.54rem;font-weight:700;color:{t3};"
                    "text-transform:uppercase;letter-spacing:0.1em'>{label}</span>"
                    "<span style='background:{tlbg};color:{tlc};border:1px solid {tlbc};"
                    "font-size:0.5rem;font-weight:800;padding:1px 5px;"
                    "border-radius:999px'>{tll}</span>"
                    "</div>"
                    # Big value
                    "<div style='font-family:{mono};font-size:1.35rem;font-weight:800;"
                    "color:{tc};line-height:1;'>{val}</div>"
                    # 7d change row
                    "<div style='display:flex;align-items:center;gap:4px;margin-top:6px;'>"
                    "<span style='font-size:0.9rem;color:{ac}'>{arrow}</span>"
                    "<span style='font-family:{mono};font-size:0.65rem;color:{pc};font-weight:700'>{pct} 7d</span>"
                    "</div>"
                    "</div>".format(
                        card=_CARD, bc=_BORDER, accent=accent,
                        t3=_TEXT3, mono=_MONO,
                        label=label,
                        tlbg=_rgba(tl_color, 0.12), tlc=tl_color,
                        tlbc=_rgba(tl_color, 0.32), tll=tl_label,
                        tc=_TEXT, val=last_val,
                        ac=ac_color, arrow=arrow,
                        pc=pct_color, pct=pct_str,
                    ),
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning("KPI grid render error: {}", exc)
        st.warning("KPI grid unavailable.")


# ── Section 4: Category Scorecards ───────────────────────────────────────────

def _svg_arc(score_0_1: float, color: str, size: int = 56) -> str:
    """Return an inline SVG arc bar for a score 0-1."""
    pct = max(0.0, min(1.0, score_0_1))
    r = 22
    cx = cy = size // 2
    # Arc from 210° to 330° (150° sweep)
    total_deg = 180
    start_deg = 180
    end_deg   = start_deg + total_deg * pct
    import math

    def polar(deg: float):
        rad = math.radians(deg)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)

    sx, sy = polar(start_deg)
    ex, ey = polar(end_deg)
    large = 1 if total_deg * pct > 180 else 0
    stroke_w = 5

    # Background arc
    bx, by = polar(start_deg + total_deg)
    bg_arc = (
        "M {:.1f} {:.1f} A {} {} 0 1 1 {:.1f} {:.1f}"
    ).format(sx, sy, r, r, bx, by)

    # Foreground arc
    if pct > 0.005:
        fg_arc = (
            "M {:.1f} {:.1f} A {} {} 0 {} 1 {:.1f} {:.1f}"
        ).format(sx, sy, r, r, large, ex, ey)
        fg_path = "<path d='{}' fill='none' stroke='{}' stroke-width='{}' stroke-linecap='round'/>".format(
            fg_arc, color, stroke_w
        )
    else:
        fg_path = ""

    return (
        "<svg width='{s}' height='{s}' viewBox='0 0 {s} {s}' "
        "xmlns='http://www.w3.org/2000/svg'>"
        "<path d='{bg}' fill='none' stroke='rgba(255,255,255,0.07)' "
        "stroke-width='{sw}' stroke-linecap='round'/>"
        "{fg}"
        "<text x='{cx}' y='{cy}' text-anchor='middle' dominant-baseline='middle' "
        "font-family='{mono}' font-size='9' font-weight='800' fill='{c}'>{pct}</text>"
        "</svg>"
    ).format(
        s=size, bg=bg_arc, sw=stroke_w, fg=fg_path,
        cx=cx, cy=cy + 2, mono=_MONO, c=color,
        pct=str(int(pct * 100)),
    )


def _compute_category_scores(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
) -> list[tuple[str, float, str]]:
    """Return list of (category_name, score_0_1, icon)."""
    # Ports score
    live_ports = [r for r in port_results if r.has_real_data]
    port_score = (sum(r.demand_score for r in live_ports) / len(live_ports)) if live_ports else 0.5

    # Routes score
    route_score = (sum(r.opportunity_score for r in route_results) / len(route_results)) if route_results else 0.5

    # Markets (freight index)
    fi_score, _, _ = _compute_freight_index(freight_data)

    # Supply chain (inverse of route congestion proxy)
    congested = [r for r in port_results if r.has_real_data and r.demand_score >= 0.80]
    sc_score = max(0.0, 1.0 - (len(congested) / max(len(live_ports), 1)) * 0.8)

    # Risk (from insights)
    risk_insights = [i for i in insights if getattr(i, "category", "") in ("MACRO", "CONVERGENCE")]
    risk_score = 0.5
    if risk_insights:
        avg_risk = sum(i.score for i in risk_insights) / len(risk_insights)
        # Low avg risk score = low concern = good (high score for us)
        risk_score = 1.0 - avg_risk if avg_risk < 0.5 else 1.0 - (avg_risk - 0.5)

    return [
        ("PORTS",         port_score,   "🏗"),
        ("ROUTES",        route_score,  "⚡"),
        ("MARKETS",       fi_score,     "📊"),
        ("SUPPLY CHAIN",  sc_score,     "🔗"),
        ("RISK",          risk_score,   "🛡"),
    ]


def _render_category_scorecards(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
) -> None:
    try:
        _section_title("CATEGORY SCORECARDS", "◉")
        categories = _compute_category_scores(port_results, route_results, insights, freight_data)
        cols = st.columns(5)
        for col, (cat_name, score, icon) in zip(cols, categories):
            letter, letter_color = _letter_grade(score)
            tl_label, tl_color = _traffic_light(score)
            arc_svg = _svg_arc(score, letter_color, size=60)
            score_pct = int(score * 100)

            col.markdown(
                "<div class='sc-card' style='"
                "background:{card};border:1px solid {bc};"
                "border-radius:12px;padding:16px 12px;"
                "text-align:center;'>"
                # Icon + arc
                "<div style='display:flex;justify-content:center;margin-bottom:6px'>"
                "{arc}"
                "</div>"
                # Category name
                "<div style='font-size:0.56rem;font-weight:700;color:{t3};"
                "text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px'>"
                "{icon} {name}</div>"
                # Grade
                "<div style='font-family:{mono};font-size:2rem;font-weight:900;"
                "color:{lc};line-height:1'>{letter}</div>"
                # Score
                "<div style='font-family:{mono};font-size:0.72rem;color:{lc};"
                "opacity:0.8;margin-top:2px'>{pct}/100</div>"
                # Status badge
                "<div style='margin-top:8px'>"
                "<span style='background:{tlbg};color:{tlc};border:1px solid {tlbc};"
                "font-size:0.52rem;font-weight:800;padding:2px 8px;border-radius:999px'>"
                "{tll}</span></div>"
                "</div>".format(
                    card=_CARD, bc=_rgba(letter_color, 0.25),
                    arc=arc_svg,
                    t3=_TEXT3, mono=_MONO,
                    icon=icon, name=cat_name,
                    lc=letter_color, letter=letter, pct=score_pct,
                    tlbg=_rgba(tl_color, 0.12), tlc=tl_color,
                    tlbc=_rgba(tl_color, 0.32), tll=tl_label,
                ),
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.warning("Category scorecards render error: {}", exc)
        st.warning("Category scorecards unavailable.")


# ── Section 5: Period-over-Period Table ───────────────────────────────────────

def _render_period_over_period(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    freight_data: dict,
    macro_data: dict,
) -> None:
    try:
        _section_title("PERIOD-OVER-PERIOD COMPARISON", "⟳")

        bdi_s = _series_from_macro(macro_data, "BSXRLM", tail=35)
        tp_s  = _series_from_freight(freight_data, "transpacific_eb", tail=35)
        ae_s  = _series_from_freight(freight_data, "asia_europe", tail=35)
        wti_s = _series_from_macro(macro_data, "DCOILWTICO", tail=35)
        zim_s = _series_from_stock({}, "ZIM", tail=35)

        live_ports = [r for r in port_results if r.has_real_data]
        demand_now = (sum(r.demand_score for r in live_ports) / len(live_ports) * 100) if live_ports else None
        alpha_now  = (max(r.opportunity_score for r in route_results) * 100) if route_results else None

        def _nth_last(series: list[float], n: int) -> Optional[float]:
            if len(series) >= n:
                return series[-n]
            return None

        def _fmt(val: Optional[float], prefix: str = "", decimals: int = 0) -> str:
            if val is None:
                return "<span style='color:#64748b'>—</span>"
            return "{}{:.{}f}".format(prefix, val, decimals)

        def _delta_badge(now: Optional[float], then: Optional[float]) -> str:
            if now is None or then is None or then == 0:
                return ""
            pct = (now - then) / abs(then) * 100
            color = _GREEN if pct >= 0 else _RED
            arrow = "↑" if pct >= 0 else "↓"
            return (
                " <span style='color:{c};font-size:0.62rem;font-weight:700'>"
                "{a}{:.0f}%</span>"
            ).format(abs(pct), c=color, a=arrow)

        # Metric definitions: (label, now, 7d_ago, 30d_ago, prefix, decimals)
        metrics = [
            ("BDI",          _nth_last(bdi_s, 1), _nth_last(bdi_s, 8),  _nth_last(bdi_s, 31), "",  0),
            ("Trans-Pac $/FEU", _nth_last(tp_s, 1),  _nth_last(tp_s, 8),  _nth_last(tp_s, 31),  "$", 0),
            ("Asia-Eur $/FEU",  _nth_last(ae_s, 1),  _nth_last(ae_s, 8),  _nth_last(ae_s, 31),  "$", 0),
            ("WTI $/bbl",      _nth_last(wti_s, 1), _nth_last(wti_s, 8), _nth_last(wti_s, 31), "$", 1),
            ("Demand Signal",  demand_now,           None,                 None,                 "",  0),
            ("Alpha Score",    alpha_now,             None,                 None,                 "",  0),
        ]

        header = (
            "<tr style='background:rgba(255,255,255,0.03);'>"
            "<th style='text-align:left;padding:10px 14px;font-size:0.6rem;"
            "font-weight:700;color:{t3};text-transform:uppercase;letter-spacing:0.1em;"
            "border-bottom:1px solid rgba(255,255,255,0.06);'>METRIC</th>"
            "<th style='text-align:right;padding:10px 14px;font-size:0.6rem;"
            "font-weight:700;color:{blue};text-transform:uppercase;letter-spacing:0.1em;"
            "border-bottom:1px solid rgba(255,255,255,0.06);'>NOW</th>"
            "<th style='text-align:right;padding:10px 14px;font-size:0.6rem;"
            "font-weight:700;color:{t3};text-transform:uppercase;letter-spacing:0.1em;"
            "border-bottom:1px solid rgba(255,255,255,0.06);'>1W AGO</th>"
            "<th style='text-align:right;padding:10px 14px;font-size:0.6rem;"
            "font-weight:700;color:{t3};text-transform:uppercase;letter-spacing:0.1em;"
            "border-bottom:1px solid rgba(255,255,255,0.06);'>1M AGO</th>"
            "<th style='text-align:right;padding:10px 14px;font-size:0.6rem;"
            "font-weight:700;color:{t3};text-transform:uppercase;letter-spacing:0.1em;"
            "border-bottom:1px solid rgba(255,255,255,0.06);'>7D Δ</th>"
            "<th style='text-align:right;padding:10px 14px;font-size:0.6rem;"
            "font-weight:700;color:{t3};text-transform:uppercase;letter-spacing:0.1em;"
            "border-bottom:1px solid rgba(255,255,255,0.06);'>30D Δ</th>"
            "</tr>"
        ).format(t3=_TEXT3, blue=_BLUE)

        rows_html = ""
        for i, (label, now, w1, m1, prefix, dec) in enumerate(metrics):
            row_bg = "rgba(255,255,255,0.015)" if i % 2 == 0 else "transparent"
            rows_html += (
                "<tr style='background:{rbg};'>"
                "<td style='padding:9px 14px;font-size:0.72rem;font-weight:600;"
                "color:{t};border-bottom:1px solid rgba(255,255,255,0.03)'>{lbl}</td>"
                "<td style='padding:9px 14px;font-family:{mono};font-size:0.78rem;"
                "font-weight:700;color:{blue};text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{now}</td>"
                "<td style='padding:9px 14px;font-family:{mono};font-size:0.72rem;"
                "color:{t2};text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{w1}</td>"
                "<td style='padding:9px 14px;font-family:{mono};font-size:0.72rem;"
                "color:{t2};text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{m1}</td>"
                "<td style='padding:9px 14px;text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{d7}</td>"
                "<td style='padding:9px 14px;text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{d30}</td>"
                "</tr>"
            ).format(
                rbg=row_bg, t=_TEXT, mono=_MONO, blue=_BLUE, t2=_TEXT2,
                lbl=label,
                now=_fmt(now, prefix, dec),
                w1=_fmt(w1, prefix, dec),
                m1=_fmt(m1, prefix, dec),
                d7=_delta_badge(now, w1),
                d30=_delta_badge(now, m1),
            )

        st.markdown(
            "<div style='overflow-x:auto;'>"
            "<table style='width:100%;border-collapse:collapse;"
            "background:{card};border:1px solid {border};"
            "border-radius:12px;overflow:hidden;'>"
            "<thead>{header}</thead>"
            "<tbody>{rows}</tbody>"
            "</table></div>".format(
                card=_CARD, border=_BORDER, header=header, rows=rows_html,
            ),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning("Period-over-period render error: {}", exc)
        st.warning("Period-over-period comparison unavailable.")


# ── Section 6: Benchmark Table ────────────────────────────────────────────────

def _render_benchmark_table(
    freight_data: dict,
    macro_data: dict,
) -> None:
    try:
        _section_title("BENCHMARK vs HISTORICAL NORM", "⊟")

        bdi_s = _series_from_macro(macro_data, "BSXRLM", tail=90)
        tp_s  = _series_from_freight(freight_data, "transpacific_eb", tail=90)
        ae_s  = _series_from_freight(freight_data, "asia_europe", tail=90)
        ta_s  = _series_from_freight(freight_data, "transatlantic", tail=90)
        wti_s = _series_from_macro(macro_data, "DCOILWTICO", tail=90)

        def _norm_row(label: str, series: list[float], prefix: str = "", decimals: int = 0):
            if len(series) < 5:
                return None
            current = series[-1]
            norm_30 = sum(series[-31:-1]) / 30 if len(series) >= 31 else sum(series[:-1]) / max(len(series) - 1, 1)
            norm_90 = sum(series) / len(series)
            vs_30 = (current - norm_30) / abs(norm_30) * 100 if norm_30 != 0 else 0
            vs_90 = (current - norm_90) / abs(norm_90) * 100 if norm_90 != 0 else 0
            return (label, current, norm_30, norm_90, vs_30, vs_90, prefix, decimals)

        specs = [
            ("BDI",         bdi_s, "",  0),
            ("Trans-Pac",   tp_s,  "$", 0),
            ("Asia-Eur",    ae_s,  "$", 0),
            ("Trans-Atl",   ta_s,  "$", 0),
            ("WTI Crude",   wti_s, "$", 1),
        ]
        rows_data = [_norm_row(l, s, p, d) for l, s, p, d in specs]
        rows_data = [r for r in rows_data if r is not None]

        if not rows_data:
            st.markdown(
                "<div style='color:{};font-size:0.8rem;padding:12px;text-align:center'>"
                "Insufficient data for benchmark comparison.</div>".format(_TEXT3),
                unsafe_allow_html=True,
            )
            return

        def _vs_cell(pct: float, higher_good: bool = True) -> str:
            is_good = pct >= 0 if higher_good else pct <= 0
            color = _GREEN if is_good else _RED
            arrow = "↑" if pct >= 0 else "↓"
            return (
                "<span style='color:{c};font-weight:700;font-size:0.72rem'>"
                "{a} {:.1f}%</span>"
            ).format(abs(pct), c=color, a=arrow)

        def _status_badge(vs_90: float) -> str:
            if vs_90 >= 15:
                label, color = "ABOVE NORM", _GREEN
            elif vs_90 >= -15:
                label, color = "AT NORM", _AMBER
            else:
                label, color = "BELOW NORM", _RED
            return (
                "<span style='background:{bg};color:{c};border:1px solid {bc};"
                "font-size:0.52rem;font-weight:800;padding:2px 7px;"
                "border-radius:999px'>{lbl}</span>"
            ).format(
                bg=_rgba(color, 0.12), c=color,
                bc=_rgba(color, 0.35), lbl=label,
            )

        header = (
            "<tr style='background:rgba(255,255,255,0.03);'>"
            + "".join([
                "<th style='text-align:{a};padding:10px 14px;font-size:0.6rem;"
                "font-weight:700;color:{tc};text-transform:uppercase;letter-spacing:0.1em;"
                "border-bottom:1px solid rgba(255,255,255,0.06);'>{h}</th>".format(
                    a=a, tc=c, h=h
                )
                for h, a, c in [
                    ("METRIC", "left",  _TEXT3),
                    ("CURRENT", "right", _BLUE),
                    ("30D NORM", "right", _TEXT3),
                    ("90D NORM", "right", _TEXT3),
                    ("vs 30D", "right", _TEXT3),
                    ("vs 90D", "right", _TEXT3),
                    ("STATUS", "center", _TEXT3),
                ]
            ])
            + "</tr>"
        )

        rows_html = ""
        for i, (label, current, norm_30, norm_90, vs_30, vs_90, prefix, dec) in enumerate(rows_data):
            row_bg = "rgba(255,255,255,0.015)" if i % 2 == 0 else "transparent"
            fmt_str = "{}{:.{}f}".format(prefix, current, dec)
            fmt_30  = "{}{:.{}f}".format(prefix, norm_30, dec)
            fmt_90  = "{}{:.{}f}".format(prefix, norm_90, dec)
            rows_html += (
                "<tr style='background:{rbg};'>"
                "<td style='padding:9px 14px;font-size:0.72rem;font-weight:600;"
                "color:{t};border-bottom:1px solid rgba(255,255,255,0.03)'>{lbl}</td>"
                "<td style='padding:9px 14px;font-family:{mono};font-size:0.78rem;"
                "font-weight:700;color:{blue};text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{cur}</td>"
                "<td style='padding:9px 14px;font-family:{mono};font-size:0.72rem;"
                "color:{t2};text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{n30}</td>"
                "<td style='padding:9px 14px;font-family:{mono};font-size:0.72rem;"
                "color:{t2};text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{n90}</td>"
                "<td style='padding:9px 14px;text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{v30}</td>"
                "<td style='padding:9px 14px;text-align:right;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{v90}</td>"
                "<td style='padding:9px 14px;text-align:center;"
                "border-bottom:1px solid rgba(255,255,255,0.03)'>{badge}</td>"
                "</tr>"
            ).format(
                rbg=row_bg, t=_TEXT, mono=_MONO, blue=_BLUE, t2=_TEXT2,
                lbl=label, cur=fmt_str, n30=fmt_30, n90=fmt_90,
                v30=_vs_cell(vs_30), v90=_vs_cell(vs_90),
                badge=_status_badge(vs_90),
            )

        st.markdown(
            "<div style='overflow-x:auto;'>"
            "<table style='width:100%;border-collapse:collapse;"
            "background:{card};border:1px solid {border};"
            "border-radius:12px;overflow:hidden;'>"
            "<thead>{header}</thead>"
            "<tbody>{rows}</tbody>"
            "</table></div>".format(
                card=_CARD, border=_BORDER, header=header, rows=rows_html,
            ),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning("Benchmark table render error: {}", exc)
        st.warning("Benchmark table unavailable.")


# ── Section 7: Sparkline Strip ────────────────────────────────────────────────

def _build_sparkline(series: list[float], color: str = _BLUE, height: int = 70) -> go.Figure:
    if not series:
        series = [0.0]
    fill_color = _rgba(color, 0.18)
    fig = go.Figure(
        go.Scatter(
            y=series,
            mode="lines",
            line=dict(color=color, width=1.8),
            fill="tozeroy",
            fillcolor=fill_color,
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=height,
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
    try:
        _section_title("MARKET SPARKLINES — 30D TREND", "〜")

        bdi_s   = _series_from_macro(macro_data, "BSXRLM")
        tp_s    = _series_from_freight(freight_data, "transpacific_eb")
        ae_s    = _series_from_freight(freight_data, "asia_europe")
        zim_s   = _series_from_stock(stock_data, "ZIM")
        wti_s   = _series_from_macro(macro_data, "DCOILWTICO")
        cny_s   = _series_from_macro(macro_data, "DEXCHUS")

        specs = [
            ("BDI",        bdi_s,  _AMBER,  ",.0f", "",  True),
            ("TRANS-PAC",  tp_s,   _BLUE,   ",.0f", "$", True),
            ("ASIA-EUR",   ae_s,   _PURPLE, ",.0f", "$", True),
            ("ZIM",        zim_s,  _GREEN,  ".2f",  "$", True),
            ("WTI CRUDE",  wti_s,  _CYAN,   ".1f",  "$", False),
            ("USD/CNY",    cny_s,  _AMBER,  ".3f",  "",  False),
        ]
        cols = st.columns(6)
        for col, (title, series, color, fmt, prefix, higher_good) in zip(cols, specs):
            arrow = _arrow_dir(series)
            arrow_color = _arrow_color(arrow)
            last_val = _format_last(series, fmt, prefix)
            pct_7d = _pct_change_7d(series)

            positive = (pct_7d or 0) >= 0
            is_good = positive if higher_good else not positive
            pct_color = _GREEN if is_good else _RED
            pct_str = "{:+.1f}%".format(pct_7d) if pct_7d is not None else "—"

            col.markdown(
                "<div style='"
                "background:{glass};"
                "backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);"
                "border:1px solid {border};"
                "border-top:3px solid {accent};"
                "border-radius:10px;padding:10px 12px 4px 12px;'>"
                "<div style='display:flex;justify-content:space-between;align-items:baseline;'>"
                "<span style='font-size:0.55rem;font-weight:700;color:{t3};"
                "text-transform:uppercase;letter-spacing:0.08em'>{title}</span>"
                "<span style='font-size:0.65rem;color:{ac};font-weight:800'>{arrow}</span>"
                "</div>"
                "<div style='font-family:{mono};font-size:1rem;font-weight:700;"
                "color:{vc};line-height:1;margin-top:3px'>{val}</div>"
                "<div style='font-family:{mono};font-size:0.62rem;font-weight:700;"
                "color:{pc};margin-top:2px'>{pct} <span style='color:{t3};font-weight:400'>7d</span></div>"
                "</div>".format(
                    glass=_GLASS, border=_BORDER, accent=color,
                    t3=_TEXT3, mono=_MONO,
                    title=title, ac=arrow_color, arrow=arrow,
                    vc=arrow_color, val=last_val,
                    pc=pct_color, pct=pct_str,
                ),
                unsafe_allow_html=True,
            )
            fig = _build_sparkline(series, color, height=65)
            col.plotly_chart(
                fig,
                use_container_width=True,
                config={"displayModeBar": False, "staticPlot": True},
                key="scorecard_spark_{}".format(title.replace(" ", "_").replace("/", "_")),
            )
    except Exception as exc:
        logger.warning("Sparkline strip render error: {}", exc)
        st.warning("Sparkline strip unavailable.")


# ── Section 8: Intelligence Panel ─────────────────────────────────────────────

def _demand_to_color(score: float) -> str:
    score = max(0.0, min(1.0, score))
    if score >= 0.65:
        t = (score - 0.65) / 0.35
        r = int(10 + t * 6)
        g = int(120 + t * 65)
        b = int(60 + t * 69)
        alpha = 0.28 + t * 0.42
    elif score >= 0.40:
        t = (score - 0.40) / 0.25
        r = int(180 + t * 65)
        g = int(100 + t * 58)
        b = int(8 + t * 3)
        alpha = 0.22 + t * 0.10
    else:
        t = score / 0.40
        r = int(180 + t * 59)
        g = int(30 + t * 38)
        b = int(30 + t * 38)
        alpha = 0.25 + t * 0.10
    return "rgba({},{},{},{:.2f})".format(r, g, b, alpha)


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


def _render_intelligence_panel(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> None:
    try:
        _section_title("INTELLIGENCE PANEL", "◈")
        col_routes, col_heat, col_signals = st.columns([1, 1, 1])

        # LEFT: Top Routes
        with col_routes:
            st.markdown(
                "<div style='font-size:0.6rem;font-weight:700;color:{};text-transform:uppercase;"
                "letter-spacing:0.1em;margin-bottom:8px'>TOP ROUTES</div>".format(_TEXT3),
                unsafe_allow_html=True,
            )
            if not route_results:
                st.markdown(
                    "<div style='color:{};font-size:0.8rem;padding:12px;'>No route data</div>".format(_TEXT2),
                    unsafe_allow_html=True,
                )
            else:
                rows_html = ""
                for rank, route in enumerate(route_results[:5], 1):
                    score = route.opportunity_score
                    score_pct = int(score * 100)
                    action_label, action_color = _action_badge(score)
                    pct_30d = route.rate_pct_change_30d
                    pct_str = "{:+.1f}%".format(pct_30d * 100) if route.current_rate_usd_feu > 0 else "—"
                    pct_color = _GREEN if pct_30d > 0 else (_RED if pct_30d < -0.01 else _TEXT2)
                    rname = route.route_name
                    rname_short = rname[:26] + "…" if len(rname) > 26 else rname
                    badge_bg = _rgba(action_color, 0.12)
                    badge_bord = _rgba(action_color, 0.35)
                    row_bg = "rgba(255,255,255,0.015)" if rank % 2 == 0 else "transparent"
                    rows_html += (
                        "<div style='display:flex;align-items:center;gap:8px;"
                        "padding:7px 10px;background:{rbg};"
                        "border-bottom:1px solid rgba(255,255,255,0.03);'>"
                        "<span style='font-family:{mono};font-size:0.65rem;color:{t3};"
                        "width:14px;flex-shrink:0;font-weight:700'>#{rank}</span>"
                        "<div style='flex:1;min-width:0;'>"
                        "<div style='font-size:0.72rem;color:{t};font-weight:600;"
                        "white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{name}</div>"
                        "<div style='height:3px;background:rgba(255,255,255,0.06);"
                        "border-radius:2px;margin-top:4px;overflow:hidden;'>"
                        "<div style='height:3px;width:{bw}%;background:{bc};border-radius:2px;'></div>"
                        "</div></div>"
                        "<span style='font-family:{mono};font-size:0.68rem;color:{pc};"
                        "white-space:nowrap;flex-shrink:0'>{pct}</span>"
                        "<span style='background:{bbg};color:{ac};border:1px solid {bbd};"
                        "font-size:0.58rem;font-weight:800;padding:2px 7px;"
                        "border-radius:999px;flex-shrink:0'>{al}</span>"
                        "</div>"
                    ).format(
                        rbg=row_bg, mono=_MONO, t3=_TEXT3, t=_TEXT,
                        rank=rank, name=rname_short,
                        bw=score_pct, bc=_rgba(action_color, 0.8),
                        pc=pct_color, pct=pct_str,
                        bbg=badge_bg, ac=action_color, bbd=badge_bord, al=action_label,
                    )
                st.markdown(
                    "<div style='background:{glass};backdrop-filter:blur(10px);"
                    "-webkit-backdrop-filter:blur(10px);border:1px solid {border};"
                    "border-radius:10px;overflow:hidden;'>{rows}</div>".format(
                        glass=_GLASS, border=_BORDER, rows=rows_html,
                    ),
                    unsafe_allow_html=True,
                )

        # CENTER: Port Heatmap
        with col_heat:
            st.markdown(
                "<div style='font-size:0.6rem;font-weight:700;color:{};text-transform:uppercase;"
                "letter-spacing:0.1em;margin-bottom:8px'>PORT DEMAND HEAT MAP</div>".format(_TEXT3),
                unsafe_allow_html=True,
            )
            live   = sorted([r for r in port_results if r.has_real_data], key=lambda r: r.demand_score, reverse=True)
            others = [r for r in port_results if not r.has_real_data]
            pool   = (live + others)[:16]
            while len(pool) < 16:
                pool.append(None)

            cells_html = ""
            for item in pool:
                if item is None:
                    cells_html += "<div style='background:rgba(255,255,255,0.02);border-radius:6px;'></div>"
                    continue
                score  = item.demand_score if item.has_real_data else 0.0
                bg     = _demand_to_color(score)
                tier_color = _GREEN if score >= 0.65 else (_AMBER if score >= 0.40 else _RED)
                border = _rgba(tier_color, 0.30 + score * 0.25)
                name   = item.port_name
                name_s = name[:9] + "…" if len(name) > 9 else name
                score_txt = "{:.0f}".format(score * 100) if item.has_real_data else "?"
                cells_html += (
                    "<div style='background:{bg};border:1px solid {bc};border-radius:6px;"
                    "padding:5px 3px;display:flex;flex-direction:column;"
                    "align-items:center;justify-content:center;text-align:center;min-height:50px;'>"
                    "<div style='font-size:0.55rem;color:{t2};font-weight:600;"
                    "line-height:1.2;word-break:break-word'>{name}</div>"
                    "<div style='font-family:{mono};font-size:0.72rem;font-weight:800;"
                    "color:{tc};margin-top:2px'>{s}</div>"
                    "</div>"
                ).format(
                    bg=bg, bc=border, t2=_TEXT2, name=name_s,
                    mono=_MONO, tc=tier_color, s=score_txt,
                )
            st.markdown(
                "<div style='background:{glass};backdrop-filter:blur(10px);"
                "-webkit-backdrop-filter:blur(10px);border:1px solid {border};"
                "border-radius:10px;padding:10px;'>"
                "<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:5px;'>"
                "{cells}</div></div>".format(
                    glass=_GLASS, border=_BORDER, cells=cells_html,
                ),
                unsafe_allow_html=True,
            )

        # RIGHT: Active Signals
        with col_signals:
            st.markdown(
                "<div style='font-size:0.6rem;font-weight:700;color:{};text-transform:uppercase;"
                "letter-spacing:0.1em;margin-bottom:8px'>ACTIVE SIGNALS</div>".format(_TEXT3),
                unsafe_allow_html=True,
            )
            if not insights:
                st.markdown(
                    "<div style='color:{};font-size:0.8rem;padding:12px;'>No signals</div>".format(_TEXT2),
                    unsafe_allow_html=True,
                )
            else:
                cards_html = ""
                for idx, ins in enumerate(insights[:6]):
                    icon     = _CAT_ICONS.get(ins.category, "📌")
                    color    = _CAT_COLORS.get(ins.category, _BLUE)
                    score_pct = int(ins.score * 100)
                    score_color = _GREEN if ins.score >= 0.65 else (_AMBER if ins.score >= 0.40 else _RED)
                    badge_bg   = _rgba(score_color, 0.12)
                    badge_bord = _rgba(score_color, 0.35)
                    row_bg     = "rgba(255,255,255,0.015)" if idx % 2 == 0 else "transparent"
                    title_short = ins.title[:48] + "…" if len(ins.title) > 48 else ins.title
                    # Category tag
                    cat_color = color
                    cards_html += (
                        "<div style='display:flex;align-items:center;gap:8px;"
                        "padding:8px 10px;background:{rbg};"
                        "border-left:2px solid {c};"
                        "border-bottom:1px solid rgba(255,255,255,0.03);'>"
                        "<span style='font-size:0.88rem;flex-shrink:0;line-height:1'>{icon}</span>"
                        "<div style='flex:1;min-width:0;'>"
                        "<div style='font-size:0.7rem;color:{t};font-weight:600;"
                        "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3'>{title}</div>"
                        "<div style='font-size:0.58rem;color:{cc};margin-top:2px;"
                        "text-transform:uppercase;letter-spacing:0.04em;font-weight:700'>{cat}</div>"
                        "</div>"
                        "<span style='font-family:{mono};font-size:0.66rem;font-weight:800;"
                        "color:{sc};background:{sbg};border:1px solid {sbd};"
                        "padding:1px 6px;border-radius:999px;flex-shrink:0'>{}%</span>"
                        "</div>"
                    ).format(
                        score_pct,
                        rbg=row_bg, c=color, icon=icon,
                        t=_TEXT, title=title_short,
                        cc=cat_color, cat=ins.category,
                        mono=_MONO, sc=score_color, sbg=badge_bg, sbd=badge_bord,
                    )
                st.markdown(
                    "<div style='background:{glass};backdrop-filter:blur(10px);"
                    "-webkit-backdrop-filter:blur(10px);border:1px solid {border};"
                    "border-radius:10px;overflow:hidden;'>{cards}</div>".format(
                        glass=_GLASS, border=_BORDER, cards=cards_html,
                    ),
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning("Intelligence panel render error: {}", exc)
        st.warning("Intelligence panel unavailable.")


# ── Section 9: AI Action Items ────────────────────────────────────────────────

def _build_action_items(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> list[dict]:
    """Generate a prioritized list of action item dicts."""
    items = []

    # Top route opportunities
    for route in sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)[:3]:
        action_label, action_color = _action_badge(route.opportunity_score)
        items.append({
            "priority": 1 if route.opportunity_score >= 0.65 else 2,
            "icon": "⚡",
            "color": _GREEN if route.opportunity_score >= 0.65 else _AMBER,
            "action": action_label,
            "title": "Route: {}".format(route.route_name[:40]),
            "detail": "Score {:.0f}/100 — ${:,.0f}/FEU".format(
                route.opportunity_score * 100,
                route.current_rate_usd_feu,
            ) if route.current_rate_usd_feu > 0 else "Score {:.0f}/100".format(route.opportunity_score * 100),
            "tag": "ROUTE",
            "tag_color": _BLUE,
        })

    # High-demand ports
    hot_ports = sorted(
        [r for r in port_results if r.has_real_data and r.demand_score >= 0.65],
        key=lambda r: r.demand_score, reverse=True,
    )[:2]
    for port in hot_ports:
        items.append({
            "priority": 2,
            "icon": "🏗",
            "color": _GREEN,
            "action": "MONITOR",
            "title": "Port: {} demand surge".format(port.port_name[:30]),
            "detail": "Demand {:.0f}/100 — {}".format(
                port.demand_score * 100, port.demand_label if port.has_real_data else "",
            ),
            "tag": "PORT",
            "tag_color": _GREEN,
        })

    # Top insights
    top_insights = sorted(insights, key=lambda i: i.score, reverse=True)[:2]
    for ins in top_insights:
        color = _CAT_COLORS.get(ins.category, _BLUE)
        items.append({
            "priority": 1 if ins.score >= 0.75 else 2,
            "icon": _CAT_ICONS.get(ins.category, "📌"),
            "color": color,
            "action": "ALERT" if ins.score >= 0.75 else "WATCH",
            "title": ins.title[:45],
            "detail": getattr(ins, "message", "")[:80] or "Signal score {:.0f}/100".format(ins.score * 100),
            "tag": ins.category,
            "tag_color": color,
        })

    # Sort by priority then score
    items.sort(key=lambda x: x["priority"])
    return items[:8]


def _render_ai_action_items(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
) -> None:
    try:
        _section_title("AI ACTION ITEMS", "▶")
        items = _build_action_items(port_results, route_results, insights)
        if not items:
            st.markdown(
                "<div style='color:{};font-size:0.8rem;padding:16px;text-align:center'>"
                "No action items at this time.</div>".format(_TEXT3),
                unsafe_allow_html=True,
            )
            return

        # 2-column layout via st.columns
        left_items  = items[::2]
        right_items = items[1::2]
        col_l, col_r = st.columns(2)

        for col, col_items in [(col_l, left_items), (col_r, right_items)]:
            cards_html = ""
            for item in col_items:
                p = item["priority"]
                pri_color = _RED if p == 1 else _AMBER
                pri_label = "HIGH" if p == 1 else "MEDIUM"
                action_color = item["color"]

                cards_html += (
                    "<div class='sc-card' style='"
                    "background:{card};border:1px solid {bc};"
                    "border-left:3px solid {ac};"
                    "border-radius:10px;padding:14px 16px;margin-bottom:8px;'>"
                    # Header
                    "<div style='display:flex;align-items:center;gap:6px;margin-bottom:8px;'>"
                    "<span style='font-size:1rem'>{icon}</span>"
                    "<span style='font-size:0.72rem;font-weight:700;color:{t};flex:1;"
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{title}</span>"
                    # Priority badge
                    "<span style='background:{pbg};color:{pc};border:1px solid {pbc};"
                    "font-size:0.5rem;font-weight:800;padding:1px 6px;border-radius:999px;"
                    "flex-shrink:0'>{pri}</span>"
                    "</div>"
                    # Detail
                    "<div style='font-size:0.66rem;color:{t2};line-height:1.4;margin-bottom:8px'>{detail}</div>"
                    # Footer row
                    "<div style='display:flex;align-items:center;gap:6px;'>"
                    # Tag
                    "<span style='background:{tbg};color:{tc};border:1px solid {tbc};"
                    "font-size:0.52rem;font-weight:700;padding:1px 6px;"
                    "border-radius:999px'>{tag}</span>"
                    # Action button
                    "<span style='background:{abg};color:{ac};border:1px solid {abc};"
                    "font-size:0.58rem;font-weight:800;padding:2px 8px;"
                    "border-radius:999px;margin-left:auto'>{action}</span>"
                    "</div>"
                    "</div>"
                ).format(
                    card=_CARD, bc=_rgba(action_color, 0.22), ac=action_color,
                    icon=item["icon"], t=_TEXT, title=item["title"],
                    pbg=_rgba(pri_color, 0.12), pc=pri_color,
                    pbc=_rgba(pri_color, 0.32), pri=pri_label,
                    t2=_TEXT2, detail=item["detail"],
                    tbg=_rgba(item["tag_color"], 0.10), tc=item["tag_color"],
                    tbc=_rgba(item["tag_color"], 0.28), tag=item["tag"],
                    abg=_rgba(action_color, 0.15),
                    abc=_rgba(action_color, 0.40),
                    action=item["action"],
                )
            col.markdown(cards_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning("AI action items render error: {}", exc)
        st.warning("AI action items unavailable.")


# ── Section 10: Download / Export ────────────────────────────────────────────

def _render_downloads(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
) -> None:
    try:
        _section_title("DOWNLOAD / EXPORT", "↓")
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
                "Score %": "{:.0f}%".format(r.opportunity_score * 100),
                "Action": action_label,
                "Rate (USD/FEU)": r.current_rate_usd_feu if r.current_rate_usd_feu > 0 else None,
                "Rate Trend": r.rate_trend,
                "30d Change %": "{:+.1f}%".format(r.rate_pct_change_30d * 100) if r.current_rate_usd_feu > 0 else "—",
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
                "Score %": "{:.0f}%".format(demand_score * 100) if demand_score is not None else "N/A",
                "Action": p.demand_label if p.has_real_data else "—",
                "Rate (USD/FEU)": None,
                "Rate Trend": getattr(p, "demand_trend", ""),
                "30d Change %": "—",
                "Transit Days": None,
                "FBX Index": "",
                "Generated At": "",
            })

        if not rows:
            st.markdown(
                "<div style='color:{};font-size:0.8rem;padding:12px;text-align:center'>"
                "No data to export.</div>".format(_TEXT3),
                unsafe_allow_html=True,
            )
            return

        df_export = pd.DataFrame(rows)

        with st.expander("Preview Export Data", expanded=False, key="scorecard_export_expander"):
            st.dataframe(
                df_export,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Opportunity Score": st.column_config.ProgressColumn(
                        "Score", min_value=0.0, max_value=1.0, format="%.0f%%",
                    ),
                    "Rate (USD/FEU)": st.column_config.NumberColumn(
                        "Rate USD/FEU", format="$%,.0f",
                    ),
                },
            )

        col_csv, col_json, col_spacer = st.columns([1, 1, 4])
        with col_csv:
            csv_bytes = df_export.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="⬇ Download CSV",
                data=csv_bytes,
                file_name="scorecard_export.csv",
                mime="text/csv",
                key="scorecard_csv_dl",
                use_container_width=True,
            )
        with col_json:
            json_str = df_export.to_json(orient="records", indent=2, default_handler=str)
            st.download_button(
                label="⬇ Download JSON",
                data=json_str.encode("utf-8"),
                file_name="scorecard_export.json",
                mime="application/json",
                key="scorecard_json_dl",
                use_container_width=True,
            )
    except Exception as exc:
        logger.warning("Download section render error: {}", exc)
        st.warning("Download/export unavailable.")


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

    # ── 1. Alert strip (top, hidden if no criticals) ──────────────────────
    alerts = []
    try:
        from engine.alert_engine import generate_alerts
        alerts = generate_alerts(port_results, route_results, freight_data, macro_data, insights)
    except Exception as exc:
        logger.debug("Alert engine not available in scorecard: {}", exc)
    _render_alert_strip(alerts)

    # ── 2. Executive Health Gauge ─────────────────────────────────────────
    _render_executive_health(port_results, route_results, insights, freight_data)

    # ── 3. KPI Grid ───────────────────────────────────────────────────────
    _render_kpi_grid(port_results, route_results, insights, freight_data, macro_data, stock_data)

    # ── 4. Category Scorecards ────────────────────────────────────────────
    _render_category_scorecards(port_results, route_results, insights, freight_data)

    # ── 5. Period-over-Period ─────────────────────────────────────────────
    _render_period_over_period(port_results, route_results, freight_data, macro_data)

    # ── 6. Benchmark Table ────────────────────────────────────────────────
    _render_benchmark_table(freight_data, macro_data)

    # ── 7. Sparkline Strip ────────────────────────────────────────────────
    _render_sparkline_strip(freight_data, macro_data, stock_data)

    # ── 8. Intelligence Panel ─────────────────────────────────────────────
    _render_intelligence_panel(port_results, route_results, insights)

    # ── 9. AI Action Items ────────────────────────────────────────────────
    _render_ai_action_items(port_results, route_results, insights)

    # ── 10. Download / Export ─────────────────────────────────────────────
    _render_downloads(port_results, route_results)
