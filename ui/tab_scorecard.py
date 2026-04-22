"""ui/tab_scorecard.py — Executive Shipping Market Scorecard.

render(port_results, route_results, insights, freight_data, macro_data, stock_data)

Sections
--------
1. Executive Summary Card    — Week-of header, overall score, AI paragraph
2. Category Summary Bar      — 6-category average scores (metric_card_row)
3. Scorecard Matrix          — 30-metric institutional table (wsj_market_table)
4. Score History Chart       — 12-month trend line with event annotations
5. Quadrant Analysis         — Supply vs Demand scatter with zone labels
6. Winner / Loser of Week    — Best, worst, biggest-surprise metric cards
7. Forward 30-day Outlook    — 5 predictions with confidence % and key risk
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ports.demand_analyzer import PortDemandResult
from routes.optimizer import RouteOpportunity
from engine.insight import Insight
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_header,
    wsj_market_table,
)


# ── Scorecard metric definitions ──────────────────────────────────────────────
_METRICS = [
    # (category, metric, freight_key, macro_key, stock_key, invert)
    # invert=True means higher raw value → worse score
    ("Freight Markets", "Container Rates",       "SCFI",    None,         None,   False),
    ("Freight Markets", "Dry Bulk Rates",         "BDI",     None,         None,   False),
    ("Freight Markets", "Tanker Rates",           "BDTI",    None,         None,   False),
    ("Freight Markets", "Overall Freight Index",  "WCI",     None,         None,   False),
    ("Supply",          "Fleet Utilization",      None,      "fleet_util", None,   False),
    ("Supply",          "Blank Sailing Rate",     None,      "blank_sail", None,   True),
    ("Supply",          "Newbuild Deliveries",    None,      "newbuilds",  None,   True),
    ("Supply",          "Scrapping Pace",         None,      "scrapping",  None,   False),
    ("Demand",          "Global Trade Volume",    None,      "trade_vol",  None,   False),
    ("Demand",          "China Import/Export",    None,      "china_trade",None,   False),
    ("Demand",          "US Consumer Demand",     None,      "us_consumer",None,   False),
    ("Demand",          "India Growth",           None,      "india_gdp",  None,   False),
    ("Infrastructure",  "Port Congestion",        None,      "port_cong",  None,   True),
    ("Infrastructure",  "Canal Capacity",         None,      "canal_cap",  None,   False),
    ("Infrastructure",  "Terminal Efficiency",    None,      "term_eff",   None,   False),
    ("Infrastructure",  "Intermodal Connectivity",None,      "intermodal", None,   False),
    ("Financial",       "Carrier Profitability",  None,      None,         "ZIM",  False),
    ("Financial",       "Stock Performance",      None,      None,         "SBLK", False),
    ("Financial",       "Shipping Credit Spreads",None,      "credit_sprd",None,   True),
    ("Financial",       "Newbuild Prices",        None,      "newbld_px",  None,   True),
    ("Risk",            "Geopolitical",           None,      "geo_risk",   None,   True),
    ("Risk",            "Weather",                None,      "weather",    None,   True),
    ("Risk",            "Regulatory / ESG",       None,      "esg_risk",   None,   True),
    ("Risk",            "Currency",               None,      "fx_vol",     None,   True),
    ("Freight Markets", "Spot vs Contract Spread",None,      "spot_ctrt",  None,   False),
    ("Supply",          "Order Book / Fleet Ratio",None,     "ob_fleet",   None,   True),
    ("Demand",          "E-Commerce Lift",        None,      "ecom",       None,   False),
    ("Infrastructure",  "Rail / Truck Availability",None,    "rail_truck", None,   False),
    ("Financial",       "Bunker Price Impact",    None,      "bunker",     None,   True),
    ("Risk",            "Piracy / Security",      None,      "piracy",     None,   True),
]

_CATEGORY_ORDER = [
    "Freight Markets", "Supply", "Demand",
    "Infrastructure", "Financial", "Risk",
]

_CATEGORY_COLORS = {
    "Freight Markets": C_ACCENT,
    "Supply":          C_CONV,
    "Demand":          C_HIGH,
    "Infrastructure":  C_MACRO,
    "Financial":       C_MOD,
    "Risk":            C_LOW,
}


# ── Cell formatters ───────────────────────────────────────────────────────────

def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _last_value(series: list[float]) -> Optional[float]:
    return series[-1] if series else None


def _series_freight(freight_data: dict, key: str) -> list[float]:
    try:
        df = freight_data.get(key)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return []
        df = df.sort_values("date") if "date" in df.columns else df
        for col in ("rate_usd_per_feu", "rate_usd_feu", "value", "index_value", "close"):
            if col in df.columns:
                return [float(v) for v in df[col].dropna().tolist()]
        return []
    except Exception as exc:
        logger.debug("_series_freight {}: {}", key, exc)
        return []


def _series_macro(macro_data: dict, key: str) -> list[float]:
    try:
        df = macro_data.get(key)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return []
        df = df.sort_values("date") if "date" in df.columns else df
        for col in ("value", "index_value", "rate", "score"):
            if col in df.columns:
                return [float(v) for v in df[col].dropna().tolist()]
        return []
    except Exception as exc:
        logger.debug("_series_macro {}: {}", key, exc)
        return []


def _series_stock(stock_data: dict, ticker: str) -> list[float]:
    try:
        df = stock_data.get(ticker)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return []
        df = df.sort_values("date") if "date" in df.columns else df
        for col in ("close", "Close", "adj_close"):
            if col in df.columns:
                return [float(v) for v in df[col].dropna().tolist()]
        return []
    except Exception as exc:
        logger.debug("_series_stock {}: {}", ticker, exc)
        return []


def _score_from_series(series: list[float], invert: bool = False) -> int:
    """Convert a raw time series to a 0-100 score via percentile rank of last value."""
    try:
        if len(series) < 3:
            return 50
        last = series[-1]
        mn, mx = min(series), max(series)
        if mx == mn:
            return 50
        pct = (last - mn) / (mx - mn) * 100
        return int(100 - pct if invert else pct)
    except Exception:
        return 50


def _stable_score(seed: int, base: int = 55) -> int:
    """Deterministic pseudo-score for metrics without live data."""
    rng = random.Random(seed)
    return max(10, min(90, base + rng.randint(-25, 25)))


def _rag(score: int) -> tuple[str, str]:
    """Return (label, color) for score."""
    if score >= 65:
        return "GREEN", C_HIGH
    if score >= 40:
        return "AMBER", C_MOD
    return "RED", C_LOW


def _trend_arrow(series: list[float], prior_score: int, cur_score: int) -> str:
    try:
        if len(series) >= 2:
            delta = series[-1] - series[max(0, len(series) - 8)]
            if abs(delta) < 1e-9:
                return "→"
            return "↑" if delta > 0 else "↓"
        if cur_score > prior_score + 2:
            return "↑"
        if cur_score < prior_score - 2:
            return "↓"
        return "→"
    except Exception:
        return "→"


def _week_label() -> str:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%B %d, %Y").upper()


def _overall_score(rows: list[dict]) -> int:
    try:
        scores = [r["score"] for r in rows]
        return int(sum(scores) / len(scores)) if scores else 50
    except Exception:
        return 50


def _hex_alpha(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── Section 1: Executive Summary Card ────────────────────────────────────────

def _render_executive_summary(overall: int, rows: list[dict]) -> None:
    try:
        rag_label, rag_color = _rag(overall)
        freight_scores = [r["score"] for r in rows if r["category"] == "Freight Markets"]
        demand_scores  = [r["score"] for r in rows if r["category"] == "Demand"]
        risk_scores    = [r["score"] for r in rows if r["category"] == "Risk"]

        freight_avg = int(sum(freight_scores) / len(freight_scores)) if freight_scores else 50
        demand_avg  = int(sum(demand_scores)  / len(demand_scores))  if demand_scores  else 50
        risk_avg    = int(sum(risk_scores)    / len(risk_scores))    if risk_scores    else 50

        summary = (
            f"Global shipping markets are operating at a composite score of {overall}/100 "
            f"({rag_label}), reflecting {freight_avg}/100 in freight conditions, "
            f"{demand_avg}/100 in demand fundamentals, and a risk environment scoring "
            f"{risk_avg}/100. "
        )
        if overall >= 65:
            summary += (
                "Carriers continue to benefit from elevated spot rates and resilient consumer "
                "demand, while port infrastructure remains broadly functional. Near-term outlook "
                "is constructive with upside risk to rate forecasts."
            )
        elif overall >= 40:
            summary += (
                "Mixed signals persist across freight corridors: demand recovery is uneven and "
                "supply additions are compressing margins. Operators should monitor blank sailing "
                "announcements and canal disruption risk closely over the next 30 days."
            )
        else:
            summary += (
                "Deteriorating freight conditions, excess supply, and softening demand create "
                "headwinds across all major trade lanes. Capital discipline and route optimization "
                "are critical. Watch for further rate erosion and potential carrier consolidation."
            )

        st.html(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:3px;'
            f'padding:32px 36px 28px;margin-bottom:20px;">'
            f'<div style="display:flex;align-items:flex-start;justify-content:space-between;'
            f'flex-wrap:wrap;gap:20px;">'
            f'<div style="flex:1;min-width:260px;">'
            f'<div style="font-family:var(--sans);font-size:11px;letter-spacing:2px;'
            f'color:{C_TEXT3};text-transform:uppercase;margin-bottom:6px;">Executive Summary</div>'
            f'<div style="font-family:var(--serif);font-size:22px;font-weight:700;'
            f'color:{C_TEXT};letter-spacing:0.5px;margin-bottom:4px;">Composite Market Score</div>'
            f'<div style="font-family:var(--sans);font-size:12px;color:{C_ACCENT};'
            f'letter-spacing:1px;">WEEK OF {_week_label()}</div>'
            f'</div>'
            f'<div style="text-align:center;min-width:140px;">'
            f'<div style="font-family:var(--mono);font-size:56px;font-weight:800;'
            f'color:{rag_color};line-height:1;">{overall}</div>'
            f'<div style="font-family:var(--sans);font-size:11px;color:{C_TEXT3};'
            f'margin-top:2px;">/ 100 COMPOSITE</div>'
            f'<div style="font-family:var(--sans);font-size:13px;font-weight:700;'
            f'color:{rag_color};margin-top:4px;letter-spacing:2px;">{rag_label}</div>'
            f'</div>'
            f'</div>'
            f'<div style="margin-top:20px;background:rgba(232,230,225,0.04);'
            f'border-radius:3px;height:6px;">'
            f'<div style="width:{overall}%;height:100%;background:{rag_color};'
            f'border-radius:3px;transition:width 0.8s ease;"></div>'
            f'</div>'
            f'<div style="margin-top:20px;font-family:var(--sans);font-size:14px;'
            f'line-height:1.75;color:{C_TEXT2};max-width:900px;">{summary}</div>'
            f'</div>'
        )
        logger.debug("Executive summary rendered — overall={}", overall)
    except Exception as exc:
        logger.error("_render_executive_summary: {}", exc)
        st.error("Executive summary unavailable.")


# ── Section 2: Category Summary Bar ───────────────────────────────────────────

def _render_category_bar(rows: list[dict]) -> None:
    try:
        section_header("Category Averages", "Aggregate scores across the six scorecard pillars")

        cat_avgs: dict[str, float] = {}
        for cat in _CATEGORY_ORDER:
            scores = [r["score"] for r in rows if r["category"] == cat]
            cat_avgs[cat] = sum(scores) / len(scores) if scores else 50.0

        metrics = []
        for cat in _CATEGORY_ORDER:
            avg = int(cat_avgs[cat])
            color = _CATEGORY_COLORS.get(cat, C_TEXT3)
            rag_label, _ = _rag(avg)
            metrics.append({
                "label":    cat.upper(),
                "value":    f"{avg}",
                "delta":    f"{rag_label}",
                "delta_color": color,
                "sublabel": "/ 100",
                "accent":   color,
            })

        metric_card_row(metrics, columns=6)
        logger.debug("Category summary bar rendered")
    except Exception as exc:
        logger.error("_render_category_bar: {}", exc)


# ── Section 3: Scorecard Matrix ───────────────────────────────────────────────

def _build_rows(freight_data: dict, macro_data: dict, stock_data: dict) -> list[dict]:
    rows = []
    for i, (cat, metric, fk, mk, sk, invert) in enumerate(_METRICS):
        try:
            series: list[float] = []
            if fk:
                series = _series_freight(freight_data, fk)
            elif mk:
                series = _series_macro(macro_data, mk)
            elif sk:
                series = _series_stock(stock_data, sk)

            if series:
                score = _score_from_series(series, invert)
            else:
                score = _stable_score(i * 17 + len(metric), base=55)

            prior_score = max(10, min(90, score + random.Random(i * 7).randint(-8, 8)))
            trend = _trend_arrow(series, prior_score, score)
            rag_label, rag_color = _rag(score)

            if score >= 75:
                notes = "Strong"
            elif score >= 60:
                notes = "Elevated"
            elif score >= 45:
                notes = "Neutral"
            elif score >= 30:
                notes = "Softening"
            else:
                notes = "Weak"

            rows.append({
                "category": cat,
                "metric": metric,
                "score": score,
                "rag_label": rag_label,
                "rag_color": rag_color,
                "prior_score": prior_score,
                "trend": trend,
                "notes": notes,
                "series": series,
            })
        except Exception as exc:
            logger.warning("_build_rows metric={} err={}", metric, exc)
            rows.append({
                "category": cat,
                "metric": metric,
                "score": 50,
                "rag_label": "AMBER",
                "rag_color": C_MOD,
                "prior_score": 50,
                "trend": "→",
                "notes": "N/A",
                "series": [],
            })
    return rows


def _render_scorecard_matrix(rows: list[dict]) -> None:
    try:
        section_header("Scorecard Matrix — 30 Metrics",
                       "Per-metric score, rating, week-over-week trend")

        headers = ["Category", "Metric", "Score", "Rating", "Prior", "Trend", "Notes"]
        table_rows = []
        for row in rows:
            cat_color = _CATEGORY_COLORS.get(row["category"], C_TEXT3)
            trend_color = (
                C_HIGH if row["trend"] == "↑"
                else C_LOW if row["trend"] == "↓"
                else C_TEXT3
            )
            table_rows.append([
                _sans(row["category"].upper(), color=cat_color, weight=600),
                _sans(row["metric"], color=C_TEXT, weight=500),
                _mono(str(row["score"]), color=row["rag_color"], weight=700),
                badge(row["rag_label"], color=row["rag_color"]),
                _mono(str(row["prior_score"]), color=C_TEXT3),
                _mono(row["trend"], color=trend_color, weight=700),
                _sans(row["notes"], color=C_TEXT3),
            ])

        wsj_market_table(headers, table_rows)
        logger.debug("Scorecard matrix rendered — {} rows", len(rows))
    except Exception as exc:
        logger.error("_render_scorecard_matrix: {}", exc)
        st.error("Scorecard matrix unavailable.")


# ── Section 4: Score History Chart ───────────────────────────────────────────

def _render_score_history(overall: int) -> None:
    try:
        today = date.today()
        months = [today - timedelta(days=30 * i) for i in range(12, -1, -1)]
        rng = random.Random(42)
        base = max(30, overall - 20)
        scores = []
        cur = base
        for _ in months:
            cur = max(20, min(90, cur + rng.randint(-8, 9)))
            scores.append(cur)
        scores[-1] = overall

        labels = [m.strftime("%b %Y") for m in months]

        events = {
            2: ("Suez Disruption", C_LOW),
            5: ("Rate Rebound", C_HIGH),
            8: ("China Reopening", C_ACCENT),
            11: ("Q4 Peak Season", C_MOD),
        }

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=labels, y=scores,
            mode="lines+markers",
            line=dict(color=C_ACCENT, width=2.5),
            marker=dict(size=6, color=C_ACCENT),
            fill="tozeroy",
            fillcolor=_hex_alpha(C_ACCENT, 0.08),
            name="Composite Score",
            hovertemplate="<b>%{x}</b><br>Score: %{y}/100<extra></extra>",
        ))

        for idx, (label, color) in events.items():
            if idx < len(labels):
                fig.add_vline(x=labels[idx], line=dict(color=color, width=1, dash="dot"))
                fig.add_annotation(
                    x=labels[idx], y=scores[idx] + 5,
                    text=label, showarrow=False,
                    font=dict(size=9, color=color),
                    bgcolor=C_CARD,
                )

        fig.add_hline(y=65, line=dict(color=C_HIGH, width=1, dash="dash"),
                      annotation_text="GREEN threshold",
                      annotation_font=dict(color=C_HIGH, size=9))
        fig.add_hline(y=40, line=dict(color=C_LOW, width=1, dash="dash"),
                      annotation_text="RED threshold",
                      annotation_font=dict(color=C_LOW, size=9))

        apply_dark_layout(
            fig,
            title="Composite Score — 12-Month History",
            height=320,
            showlegend=False,
            xaxis=dict(showgrid=False, tickfont=dict(size=10)),
            yaxis=dict(range=[0, 100], tickfont=dict(size=10), title="Score"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        logger.debug("Score history chart rendered")
    except Exception as exc:
        logger.error("_render_score_history: {}", exc)
        st.error("Score history chart unavailable.")


# ── Section 5: Quadrant Analysis ─────────────────────────────────────────────

def _render_quadrant(rows: list[dict]) -> None:
    try:
        supply_cats  = ["Supply"]
        demand_cats  = ["Demand", "Freight Markets"]

        def avg_cat(cats: list[str]) -> float:
            s = [r["score"] for r in rows if r["category"] in cats]
            return sum(s) / len(s) if s else 50.0

        cur_x = avg_cat(supply_cats)
        cur_y = avg_cat(demand_cats)

        historical = [
            ("2022 Q1", 72, 78, C_HIGH),
            ("2022 Q3", 55, 68, C_ACCENT),
            ("2023 Q1", 40, 45, C_MOD),
            ("2023 Q3", 48, 52, C_MOD),
            ("2024 Q2", 62, 60, C_ACCENT),
        ]

        fig = go.Figure()

        zone_defs = [
            (50, 100, 50, 100, _hex_alpha(C_HIGH, 0.06), 75, 75, "GOLDILOCKS",  C_HIGH),
            (0,  50,  50, 100, _hex_alpha(C_LOW,  0.06), 25, 75, "UNDERSUPPLY", C_LOW),
            (50, 100, 0,  50,  _hex_alpha(C_MOD,  0.06), 75, 25, "OVERSUPPLY",  C_MOD),
            (0,  50,  0,  50,  "rgba(100,116,139,0.06)", 25, 25, "SLOWDOWN",    C_TEXT3),
        ]

        for x0, x1, y0, y1, fill, lx, ly, label, lcolor in zone_defs:
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                          fillcolor=fill, line=dict(width=0))
            fig.add_annotation(x=lx, y=ly, text=label, showarrow=False,
                               font=dict(size=9, color=lcolor),
                               opacity=0.6)

        for hname, hx, hy, hc in historical:
            fig.add_trace(go.Scatter(
                x=[hx], y=[hy], mode="markers+text",
                marker=dict(size=10, color=hc, opacity=0.6, symbol="circle"),
                text=[hname], textposition="top center",
                textfont=dict(size=9, color=hc),
                name=hname, showlegend=True,
                hovertemplate=f"<b>{hname}</b><br>Supply: {hx}<br>Demand: {hy}<extra></extra>",
            ))

        fig.add_trace(go.Scatter(
            x=[cur_x], y=[cur_y], mode="markers+text",
            marker=dict(size=18, color=C_ACCENT, symbol="star",
                        line=dict(color=C_TEXT, width=1.5)),
            text=["NOW"], textposition="top center",
            textfont=dict(size=11, color=C_TEXT),
            name="Current", showlegend=True,
            hovertemplate=f"<b>Current</b><br>Supply Outlook: {cur_x:.0f}"
                          f"<br>Demand Outlook: {cur_y:.0f}<extra></extra>",
        ))

        fig.add_hline(y=50, line=dict(color=C_BORDER, width=1))
        fig.add_vline(x=50, line=dict(color=C_BORDER, width=1))

        apply_dark_layout(
            fig,
            title="Market Quadrant Analysis — Supply vs Demand Outlook",
            height=400,
            xaxis=dict(range=[0, 100], title="Supply Outlook →", tickfont=dict(size=9)),
            yaxis=dict(range=[0, 100], title="Demand Outlook →", tickfont=dict(size=9)),
            legend=dict(font=dict(size=9, color=C_TEXT3),
                        bgcolor="rgba(0,0,0,0)",
                        bordercolor=C_BORDER, borderwidth=1),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        logger.debug("Quadrant chart rendered — cur=({:.0f},{:.0f})", cur_x, cur_y)
    except Exception as exc:
        logger.error("_render_quadrant: {}", exc)
        st.error("Quadrant analysis unavailable.")


# ── Section 6: Winner / Loser / Surprise ─────────────────────────────────────

def _render_winner_loser(rows: list[dict]) -> None:
    try:
        if not rows:
            return

        best  = max(rows, key=lambda r: r["score"])
        worst = min(rows, key=lambda r: r["score"])
        biggest = max(rows, key=lambda r: abs(r["score"] - r["prior_score"]))

        def wl_card(title: str, icon: str, metric: str, cat: str, score: int,
                    prior: int, color: str, note: str) -> str:
            delta = score - prior
            delta_str = f"+{delta}" if delta >= 0 else str(delta)
            delta_color = color if delta >= 0 else C_LOW
            return (
                f'<div style="background:{C_CARD};border:1px solid {color};border-radius:3px;'
                f'padding:22px 24px;flex:1;min-width:200px;">'
                f'<div style="font-family:var(--sans);font-size:10px;color:{color};'
                f'letter-spacing:2px;text-transform:uppercase;margin-bottom:12px;">{icon} {title}</div>'
                f'<div style="font-family:var(--sans);font-size:16px;font-weight:600;'
                f'color:{C_TEXT};margin-bottom:4px;">{metric}</div>'
                f'<div style="font-family:var(--sans);font-size:10px;color:{C_TEXT3};'
                f'margin-bottom:14px;">{cat.upper()}</div>'
                f'<div style="display:flex;align-items:baseline;gap:12px;">'
                f'<span style="font-family:var(--mono);font-size:40px;font-weight:800;'
                f'color:{color};">{score}</span>'
                f'<span style="font-family:var(--sans);font-size:11px;color:{C_TEXT3};">/ 100</span>'
                f'<span style="font-family:var(--mono);font-size:13px;color:{delta_color};'
                f'font-weight:600;">{delta_str} vs prior</span>'
                f'</div>'
                f'<div style="margin-top:12px;font-family:var(--sans);font-size:12px;'
                f'color:{C_TEXT3};">{note}</div>'
                f'</div>'
            )

        best_note  = f"Strongest performer this week across all {len(rows)} tracked metrics."
        worst_note = "Weakest signal — warrants immediate operational attention."
        surp_note  = f"Largest week-over-week move: {abs(biggest['score'] - biggest['prior_score'])} pts."

        section_header("Winner / Loser of the Week",
                       "Strongest, weakest, and largest week-over-week surprise")

        st.html(
            '<div style="display:flex;gap:16px;flex-wrap:wrap;">'
            + wl_card("Winner of the Week", "▲", best["metric"], best["category"],
                      best["score"], best["prior_score"], C_HIGH, best_note)
            + wl_card("Loser of the Week", "▼", worst["metric"], worst["category"],
                      worst["score"], worst["prior_score"], C_LOW, worst_note)
            + wl_card("Biggest Surprise", "◆", biggest["metric"], biggest["category"],
                      biggest["score"], biggest["prior_score"], C_MOD, surp_note)
            + '</div>'
        )
        logger.debug("Winner/loser section rendered")
    except Exception as exc:
        logger.error("_render_winner_loser: {}", exc)
        st.error("Winner/loser section unavailable.")


# ── Section 7: Forward 30-day Outlook ────────────────────────────────────────

def _render_outlook(rows: list[dict], overall: int) -> None:
    try:
        freight_avg = int(sum(r["score"] for r in rows if r["category"] == "Freight Markets") /
                          max(1, sum(1 for r in rows if r["category"] == "Freight Markets")))
        risk_avg    = int(sum(r["score"] for r in rows if r["category"] == "Risk") /
                          max(1, sum(1 for r in rows if r["category"] == "Risk")))

        predictions = [
            {
                "title": "Container Spot Rate Trajectory",
                "body": (
                    f"{'Spot rates expected to hold elevated levels' if freight_avg >= 60 else 'Spot rate pressure likely to persist'} "
                    "over the next 30 days as blank sailing programs offset incremental supply additions. "
                    "Asia-Europe remains most sensitive to schedule disruption."
                ),
                "confidence": min(85, freight_avg + 20),
                "key_risk": "Blank sailing reversal by top-4 carriers.",
            },
            {
                "title": "Demand Momentum — Asia-Pacific",
                "body": (
                    "China export volumes showing early signs of Q2 seasonal acceleration. "
                    "Electronics and machinery categories are the primary drivers; "
                    "watch for pre-tariff pull-forward demand from US importers."
                ),
                "confidence": 68,
                "key_risk": "US tariff escalation dampening booking velocity.",
            },
            {
                "title": "Port Congestion — Transpacific",
                "body": (
                    "USWC port dwell times are normalising after February spike. "
                    "USEC remains tight on labor availability. "
                    "Expect 1-2 day average dwell improvement by mid-month if vessel bunching clears."
                ),
                "confidence": 72,
                "key_risk": "Weather events or ILA contract renegotiation.",
            },
            {
                "title": "Fleet Capacity Additions",
                "body": (
                    "Approximately 180k TEU of new container capacity scheduled for delivery next 30 days. "
                    f"{'This is manageable given current utilization levels.' if overall >= 55 else 'Combined with soft demand, this risks further rate pressure.'} "
                    "Scrapping remains below historical pace."
                ),
                "confidence": 76,
                "key_risk": "Accelerated deliveries from Chinese yards ahead of summer.",
            },
            {
                "title": "Geopolitical & Route Risk",
                "body": (
                    f"{'Risk environment remains elevated' if risk_avg < 50 else 'Risk conditions are moderate'} "
                    "with Red Sea diversions continuing to add ~10-14 days to Asia-Europe voyages. "
                    "Panama Canal water levels stable but require monitoring into dry season."
                ),
                "confidence": 60,
                "key_risk": "Sudden Red Sea normalisation deflating rates 15-20%.",
            },
        ]

        section_header("Forward 30-Day Outlook",
                       "Five forward-looking predictions with confidence bands and key risk")

        for i, pred in enumerate(predictions):
            try:
                conf = pred["confidence"]
                conf_color = C_HIGH if conf >= 70 else (C_MOD if conf >= 55 else C_LOW)
                conf_bg = _hex_alpha(conf_color, 0.12)

                st.html(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                    f'border-radius:3px;padding:18px 22px;margin-bottom:10px;'
                    f'display:flex;gap:20px;align-items:flex-start;">'
                    f'<div style="min-width:56px;text-align:center;padding-top:2px;">'
                    f'<div style="font-family:var(--mono);font-size:22px;font-weight:800;'
                    f'color:{conf_color};">{conf}%</div>'
                    f'<div style="font-family:var(--sans);font-size:8px;color:{C_TEXT3};'
                    f'letter-spacing:1px;margin-top:2px;">CONF.</div>'
                    f'</div>'
                    f'<div style="flex:1;">'
                    f'<div style="font-family:var(--serif);font-size:13px;font-weight:700;'
                    f'color:{C_TEXT};margin-bottom:6px;">{i+1}. {pred["title"]}</div>'
                    f'<div style="font-family:var(--sans);font-size:13px;color:{C_TEXT2};'
                    f'line-height:1.65;margin-bottom:10px;">{pred["body"]}</div>'
                    f'<div style="display:inline-flex;align-items:center;gap:6px;'
                    f'background:{conf_bg};border-radius:3px;padding:3px 10px;">'
                    f'<span style="font-family:var(--sans);font-size:9px;color:{C_TEXT3};'
                    f'letter-spacing:1px;font-weight:600;">KEY RISK</span>'
                    f'<span style="font-family:var(--sans);font-size:11px;color:{conf_color};">'
                    f'{pred["key_risk"]}</span>'
                    f'</div>'
                    f'</div>'
                    f'</div>'
                )
            except Exception as exc:
                logger.debug("outlook prediction {}: {}", i, exc)

        logger.debug("Outlook section rendered — {} predictions", len(predictions))
    except Exception as exc:
        logger.error("_render_outlook: {}", exc)
        st.error("Forward outlook unavailable.")


# ── Main render ───────────────────────────────────────────────────────────────

def render(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
) -> None:
    """Render the executive shipping market scorecard."""
    try:
        logger.info("tab_scorecard.render() — building scorecard rows")

        freight_data = freight_data or {}
        macro_data   = macro_data   or {}
        stock_data   = stock_data   or {}

        rows = _build_rows(freight_data, macro_data, stock_data)
        overall = _overall_score(rows)

        logger.info("Scorecard: {} metrics, overall={}", len(rows), overall)

        page_header(
            title="Shipping Market Scorecard",
            subtitle=f"Executive scorecard of global shipping market conditions — Week of {_week_label()}",
            icon="📊",
            badge_text="Demo Data",
            badge_color=C_MOD,
        )

        _render_executive_summary(overall, rows)
        _render_category_bar(rows)
        _render_scorecard_matrix(rows)
        _render_score_history(overall)
        _render_quadrant(rows)
        _render_winner_loser(rows)
        _render_outlook(rows, overall)

        logger.success("tab_scorecard.render() complete")

    except Exception as exc:
        logger.exception("tab_scorecard.render() fatal: {}", exc)
        st.error(f"Scorecard render error: {exc}")
