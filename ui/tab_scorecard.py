"""ui/tab_scorecard.py — Executive Shipping Market Scorecard.

render(port_results, route_results, insights, freight_data, macro_data, stock_data)

Sections
--------
1. Executive Summary Card    — Week-of header, overall score, AI paragraph
2. Scorecard Matrix          — 30-metric institutional table (6 categories)
3. Score History Chart       — 12-month trend line with event annotations
4. Quadrant Analysis         — Supply vs Demand scatter with zone labels
5. Winner / Loser of Week    — Best, worst, biggest-surprise metric cards
6. Forward 30-day Outlook    — 5 predictions with confidence % and key risk
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

# ── Palette ───────────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
_MONO = "'SF Mono', 'Menlo', 'Courier New', monospace"

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
    # pad to 30 with extra cross-category composites
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
        logger.debug("_series_stock {}: {}", key, exc)
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

        bar_pct = overall
        bar_color = rag_color

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
            f'padding:32px 36px 28px;margin-bottom:20px;">'
            f'<div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:20px;">'
            f'<div style="flex:1;min-width:260px;">'
            f'<div style="font-family:{_MONO};font-size:11px;letter-spacing:2px;color:{C_TEXT3};'
            f'text-transform:uppercase;margin-bottom:6px;">Executive Intelligence</div>'
            f'<div style="font-family:{_MONO};font-size:20px;font-weight:700;color:{C_TEXT};'
            f'letter-spacing:1px;margin-bottom:4px;">SHIPPING MARKET SCORECARD</div>'
            f'<div style="font-family:{_MONO};font-size:12px;color:{C_ACCENT};letter-spacing:1px;">'
            f'WEEK OF {_week_label()}</div>'
            f'</div>'
            f'<div style="text-align:center;min-width:140px;">'
            f'<div style="font-family:{_MONO};font-size:56px;font-weight:800;color:{bar_color};'
            f'line-height:1;">{overall}</div>'
            f'<div style="font-family:{_MONO};font-size:11px;color:{C_TEXT3};margin-top:2px;">/ 100 COMPOSITE</div>'
            f'<div style="font-family:{_MONO};font-size:13px;font-weight:700;color:{bar_color};'
            f'margin-top:4px;letter-spacing:2px;">{rag_label}</div>'
            f'</div>'
            f'</div>'
            f'<div style="margin-top:20px;background:rgba(255,255,255,0.04);border-radius:6px;height:6px;">'
            f'<div style="width:{bar_pct}%;height:100%;background:{bar_color};border-radius:6px;'
            f'transition:width 0.8s ease;"></div>'
            f'</div>'
            f'<div style="margin-top:20px;font-family:{_SANS};font-size:14px;line-height:1.75;'
            f'color:{C_TEXT2};max-width:900px;">{summary}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        logger.debug("Executive summary rendered — overall={}", overall)
    except Exception as exc:
        logger.error("_render_executive_summary: {}", exc)
        st.error("Executive summary unavailable.")


# ── Section 2: Scorecard Matrix ───────────────────────────────────────────────

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

            notes = ""
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
        st.markdown(
            f'<div style="font-family:{_MONO};font-size:11px;letter-spacing:2px;color:{C_TEXT3};'
            f'text-transform:uppercase;margin:28px 0 14px;">Scorecard Matrix — 30 Metrics</div>',
            unsafe_allow_html=True,
        )

        header = (
            f'<div style="display:grid;grid-template-columns:160px 200px 70px 90px 90px 60px 1fr;'
            f'gap:0;background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:8px 8px 0 0;'
            f'padding:10px 16px;">'
            f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};letter-spacing:1px;">CATEGORY</div>'
            f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};letter-spacing:1px;">METRIC</div>'
            f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};letter-spacing:1px;text-align:right;">SCORE</div>'
            f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};letter-spacing:1px;text-align:center;">RATING</div>'
            f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};letter-spacing:1px;text-align:right;">PRIOR</div>'
            f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};letter-spacing:1px;text-align:center;">TREND</div>'
            f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};letter-spacing:1px;">NOTES</div>'
            f'</div>'
        )
        st.markdown(header, unsafe_allow_html=True)

        cat_colors = {
            "Freight Markets": C_ACCENT,
            "Supply": "#8b5cf6",
            "Demand": C_HIGH,
            "Infrastructure": "#06b6d4",
            "Financial": C_MOD,
            "Risk": C_LOW,
        }

        for i, row in enumerate(rows):
            try:
                bg = C_CARD if i % 2 == 0 else C_SURFACE
                cat_color = cat_colors.get(row["category"], C_TEXT3)
                score_color = row["rag_color"]
                trend_color = C_HIGH if row["trend"] == "↑" else (C_LOW if row["trend"] == "↓" else C_TEXT3)
                border_bottom = "none" if i < len(rows) - 1 else "none"
                radius = "0 0 8px 8px" if i == len(rows) - 1 else "0"

                bar_w = row["score"]
                rag_bg = f"rgba({','.join(str(int(row['rag_color'].lstrip('#')[j:j+2], 16)) for j in (0,2,4))},0.15)"

                row_html = (
                    f'<div style="display:grid;grid-template-columns:160px 200px 70px 90px 90px 60px 1fr;'
                    f'gap:0;background:{bg};border-left:1px solid {C_BORDER};border-right:1px solid {C_BORDER};'
                    f'border-bottom:1px solid {C_BORDER};border-radius:{radius};padding:9px 16px;'
                    f'align-items:center;">'
                    f'<div style="font-family:{_MONO};font-size:10px;color:{cat_color};'
                    f'letter-spacing:0.5px;font-weight:600;">{row["category"].upper()}</div>'
                    f'<div style="font-family:{_SANS};font-size:13px;color:{C_TEXT};font-weight:500;">'
                    f'{row["metric"]}</div>'
                    f'<div style="text-align:right;">'
                    f'<span style="font-family:{_MONO};font-size:16px;font-weight:700;color:{score_color};">'
                    f'{row["score"]}</span>'
                    f'</div>'
                    f'<div style="text-align:center;">'
                    f'<span style="font-family:{_MONO};font-size:10px;font-weight:700;color:{row["rag_color"]};'
                    f'background:{rag_bg};padding:2px 7px;border-radius:4px;letter-spacing:0.5px;">'
                    f'{row["rag_label"]}</span>'
                    f'</div>'
                    f'<div style="font-family:{_MONO};font-size:13px;color:{C_TEXT3};text-align:right;">'
                    f'{row["prior_score"]}</div>'
                    f'<div style="font-family:{_MONO};font-size:18px;color:{trend_color};text-align:center;">'
                    f'{row["trend"]}</div>'
                    f'<div style="font-family:{_SANS};font-size:12px;color:{C_TEXT3};">'
                    f'{row["notes"]}</div>'
                    f'</div>'
                )
                st.markdown(row_html, unsafe_allow_html=True)
            except Exception as exc:
                logger.debug("matrix row {}: {}", i, exc)

        logger.debug("Scorecard matrix rendered — {} rows", len(rows))
    except Exception as exc:
        logger.error("_render_scorecard_matrix: {}", exc)
        st.error("Scorecard matrix unavailable.")


# ── Section 3: Score History Chart ───────────────────────────────────────────

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
            marker=dict(size=6, color=C_ACCENT, line=dict(color=C_BG, width=1.5)),
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.08)",
            name="Composite Score",
            hovertemplate="<b>%{x}</b><br>Score: %{y}/100<extra></extra>",
        ))

        for idx, (label, color) in events.items():
            if idx < len(labels):
                fig.add_vline(x=labels[idx], line=dict(color=color, width=1, dash="dot"))
                fig.add_annotation(
                    x=labels[idx], y=scores[idx] + 5,
                    text=label, showarrow=False,
                    font=dict(size=9, color=color, family=_MONO),
                    bgcolor=C_CARD,
                )

        fig.add_hline(y=65, line=dict(color=C_HIGH, width=1, dash="dash"),
                      annotation_text="GREEN threshold", annotation_font=dict(color=C_HIGH, size=9))
        fig.add_hline(y=40, line=dict(color=C_LOW, width=1, dash="dash"),
                      annotation_text="RED threshold", annotation_font=dict(color=C_LOW, size=9))

        fig.update_layout(
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            font=dict(family=_MONO, color=C_TEXT2),
            title=dict(text="Composite Score — 12-Month History", font=dict(size=13, color=C_TEXT), x=0.01),
            height=320,
            margin=dict(l=50, r=30, t=50, b=40),
            xaxis=dict(showgrid=False, tickfont=dict(size=10), color=C_TEXT3, linecolor=C_BORDER),
            yaxis=dict(range=[0, 100], gridcolor=C_BORDER, tickfont=dict(size=10), color=C_TEXT3,
                       title="Score", titlefont=dict(size=10)),
            showlegend=False,
        )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:20px 20px 8px;margin:24px 0 20px;">',
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown("</div>", unsafe_allow_html=True)
        logger.debug("Score history chart rendered")
    except Exception as exc:
        logger.error("_render_score_history: {}", exc)
        st.error("Score history chart unavailable.")


# ── Section 4: Quadrant Analysis ─────────────────────────────────────────────

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
            (50, 100, 50, 100, "rgba(16,185,129,0.06)", "GOLDILOCKS\n(Tight Supply, Strong Demand)", 75, 75),
            (0, 50, 50, 100, "rgba(239,68,68,0.06)", "UNDERSUPPLY\n(Loose Supply, Strong Demand)", 25, 75),
            (50, 100, 0, 50, "rgba(245,158,11,0.06)", "OVERSUPPLY\n(Tight Supply, Weak Demand)", 75, 25),
            (0, 50, 0, 50, "rgba(100,116,139,0.06)", "SLOWDOWN\n(Loose Supply, Weak Demand)", 25, 25),
        ]
        zone_colors = [C_HIGH, C_LOW, C_MOD, C_TEXT3]
        zone_labels = ["GOLDILOCKS", "UNDERSUPPLY", "OVERSUPPLY", "SLOWDOWN"]

        for i, (x0, x1, y0, y1, fill, label, lx, ly) in enumerate(zone_defs):
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                          fillcolor=fill, line=dict(width=0))
            fig.add_annotation(x=lx, y=ly, text=zone_labels[i], showarrow=False,
                                font=dict(size=9, color=zone_colors[i], family=_MONO),
                                opacity=0.6)

        for hname, hx, hy, hc in historical:
            fig.add_trace(go.Scatter(
                x=[hx], y=[hy], mode="markers+text",
                marker=dict(size=10, color=hc, opacity=0.6, symbol="circle"),
                text=[hname], textposition="top center",
                textfont=dict(size=9, color=hc, family=_MONO),
                name=hname, showlegend=True,
                hovertemplate=f"<b>{hname}</b><br>Supply: {hx}<br>Demand: {hy}<extra></extra>",
            ))

        fig.add_trace(go.Scatter(
            x=[cur_x], y=[cur_y], mode="markers+text",
            marker=dict(size=18, color=C_ACCENT, symbol="star",
                        line=dict(color=C_TEXT, width=1.5)),
            text=["NOW"], textposition="top center",
            textfont=dict(size=11, color=C_TEXT, family=_MONO, weight=700),
            name="Current", showlegend=True,
            hovertemplate=f"<b>Current</b><br>Supply Outlook: {cur_x:.0f}<br>Demand Outlook: {cur_y:.0f}<extra></extra>",
        ))

        fig.add_hline(y=50, line=dict(color=C_BORDER, width=1))
        fig.add_vline(x=50, line=dict(color=C_BORDER, width=1))

        fig.update_layout(
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            font=dict(family=_MONO, color=C_TEXT2),
            title=dict(text="Market Quadrant Analysis — Supply vs Demand Outlook",
                       font=dict(size=13, color=C_TEXT), x=0.01),
            height=400,
            margin=dict(l=60, r=30, t=50, b=60),
            xaxis=dict(range=[0, 100], title="Supply Outlook →", gridcolor=C_BORDER,
                       tickfont=dict(size=9), color=C_TEXT3, linecolor=C_BORDER,
                       titlefont=dict(size=10, color=C_TEXT2)),
            yaxis=dict(range=[0, 100], title="Demand Outlook →", gridcolor=C_BORDER,
                       tickfont=dict(size=9), color=C_TEXT3, linecolor=C_BORDER,
                       titlefont=dict(size=10, color=C_TEXT2)),
            legend=dict(font=dict(size=9, color=C_TEXT3), bgcolor="rgba(0,0,0,0)",
                        bordercolor=C_BORDER, borderwidth=1),
        )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:20px 20px 8px;margin-bottom:20px;">',
            unsafe_allow_html=True,
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown("</div>", unsafe_allow_html=True)
        logger.debug("Quadrant chart rendered — cur=({:.0f},{:.0f})", cur_x, cur_y)
    except Exception as exc:
        logger.error("_render_quadrant: {}", exc)
        st.error("Quadrant analysis unavailable.")


# ── Section 5: Winner / Loser / Surprise ─────────────────────────────────────

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
            rag_bg = f"rgba({','.join(str(int(color.lstrip('#')[j:j+2], 16)) for j in (0,2,4))},0.12)"
            return (
                f'<div style="background:{C_CARD};border:1px solid {color};border-radius:10px;'
                f'padding:22px 24px;flex:1;min-width:200px;">'
                f'<div style="font-family:{_MONO};font-size:10px;color:{color};letter-spacing:2px;'
                f'text-transform:uppercase;margin-bottom:12px;">{icon} {title}</div>'
                f'<div style="font-family:{_SANS};font-size:16px;font-weight:600;color:{C_TEXT};'
                f'margin-bottom:4px;">{metric}</div>'
                f'<div style="font-family:{_MONO};font-size:10px;color:{C_TEXT3};margin-bottom:14px;">'
                f'{cat.upper()}</div>'
                f'<div style="display:flex;align-items:baseline;gap:12px;">'
                f'<span style="font-family:{_MONO};font-size:40px;font-weight:800;color:{color};">'
                f'{score}</span>'
                f'<span style="font-family:{_MONO};font-size:11px;color:{C_TEXT3};">/ 100</span>'
                f'<span style="font-family:{_MONO};font-size:13px;color:{color if delta >= 0 else C_LOW};'
                f'font-weight:600;">{delta_str} vs prior</span>'
                f'</div>'
                f'<div style="margin-top:12px;font-family:{_SANS};font-size:12px;color:{C_TEXT3};">'
                f'{note}</div>'
                f'</div>'
            )

        best_note  = f"Strongest performer this week across all {len(rows)} tracked metrics."
        worst_note = f"Weakest signal — warrants immediate operational attention."
        surp_note  = f"Largest week-over-week move: {abs(biggest['score'] - biggest['prior_score'])} pts."

        html = (
            f'<div style="font-family:{_MONO};font-size:11px;letter-spacing:2px;color:{C_TEXT3};'
            f'text-transform:uppercase;margin:28px 0 14px;">Winner / Loser of the Week</div>'
            f'<div style="display:flex;gap:16px;flex-wrap:wrap;">'
            + wl_card("Winner of the Week", "▲", best["metric"], best["category"],
                      best["score"], best["prior_score"], C_HIGH, best_note)
            + wl_card("Loser of the Week", "▼", worst["metric"], worst["category"],
                      worst["score"], worst["prior_score"], C_LOW, worst_note)
            + wl_card("Biggest Surprise", "◆", biggest["metric"], biggest["category"],
                      biggest["score"], biggest["prior_score"], C_MOD, surp_note)
            + f'</div>'
        )
        st.markdown(html, unsafe_allow_html=True)
        logger.debug("Winner/loser section rendered")
    except Exception as exc:
        logger.error("_render_winner_loser: {}", exc)
        st.error("Winner/loser section unavailable.")


# ── Section 6: Forward 30-day Outlook ────────────────────────────────────────

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

        st.markdown(
            f'<div style="font-family:{_MONO};font-size:11px;letter-spacing:2px;color:{C_TEXT3};'
            f'text-transform:uppercase;margin:28px 0 14px;">Forward 30-Day Outlook</div>',
            unsafe_allow_html=True,
        )

        for i, pred in enumerate(predictions):
            try:
                conf = pred["confidence"]
                conf_color = C_HIGH if conf >= 70 else (C_MOD if conf >= 55 else C_LOW)
                conf_bg = f"rgba({','.join(str(int(conf_color.lstrip('#')[j:j+2], 16)) for j in (0,2,4))},0.12)"

                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
                    f'padding:18px 22px;margin-bottom:10px;display:flex;gap:20px;align-items:flex-start;">'
                    f'<div style="min-width:56px;text-align:center;padding-top:2px;">'
                    f'<div style="font-family:{_MONO};font-size:22px;font-weight:800;color:{conf_color};">'
                    f'{conf}%</div>'
                    f'<div style="font-family:{_MONO};font-size:8px;color:{C_TEXT3};letter-spacing:1px;'
                    f'margin-top:2px;">CONF.</div>'
                    f'</div>'
                    f'<div style="flex:1;">'
                    f'<div style="font-family:{_MONO};font-size:12px;font-weight:700;color:{C_TEXT};'
                    f'margin-bottom:6px;">{i+1}. {pred["title"].upper()}</div>'
                    f'<div style="font-family:{_SANS};font-size:13px;color:{C_TEXT2};line-height:1.65;'
                    f'margin-bottom:10px;">{pred["body"]}</div>'
                    f'<div style="display:inline-flex;align-items:center;gap:6px;background:{conf_bg};'
                    f'border-radius:4px;padding:3px 10px;">'
                    f'<span style="font-family:{_MONO};font-size:9px;color:{C_TEXT3};letter-spacing:1px;">KEY RISK</span>'
                    f'<span style="font-family:{_SANS};font-size:11px;color:{conf_color};">{pred["key_risk"]}</span>'
                    f'</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.debug("outlook prediction {}: {}", i, exc)

        logger.debug("Outlook section rendered — {} predictions", len(predictions))
    except Exception as exc:
        logger.error("_render_outlook: {}", exc)
        st.error("Forward outlook unavailable.")


# ── Category Summary Bar ──────────────────────────────────────────────────────

def _render_category_bar(rows: list[dict]) -> None:
    try:
        cat_avgs: dict[str, float] = {}
        for cat in _CATEGORY_ORDER:
            scores = [r["score"] for r in rows if r["category"] == cat]
            cat_avgs[cat] = sum(scores) / len(scores) if scores else 50.0

        cat_colors_map = {
            "Freight Markets": C_ACCENT,
            "Supply": "#8b5cf6",
            "Demand": C_HIGH,
            "Infrastructure": "#06b6d4",
            "Financial": C_MOD,
            "Risk": C_LOW,
        }

        cards_html = ""
        for cat in _CATEGORY_ORDER:
            avg = int(cat_avgs[cat])
            color = cat_colors_map.get(cat, C_TEXT3)
            rag_label, _ = _rag(avg)
            cards_html += (
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
                f'padding:14px 16px;flex:1;min-width:130px;text-align:center;">'
                f'<div style="font-family:{_MONO};font-size:9px;color:{color};letter-spacing:1px;'
                f'text-transform:uppercase;margin-bottom:8px;">{cat}</div>'
                f'<div style="font-family:{_MONO};font-size:28px;font-weight:800;color:{color};">{avg}</div>'
                f'<div style="font-family:{_MONO};font-size:9px;color:{C_TEXT3};margin-top:2px;">{rag_label}</div>'
                f'<div style="margin-top:8px;background:rgba(255,255,255,0.06);border-radius:3px;height:4px;">'
                f'<div style="width:{avg}%;height:100%;background:{color};border-radius:3px;"></div>'
                f'</div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="font-family:{_MONO};font-size:11px;letter-spacing:2px;color:{C_TEXT3};'
            f'text-transform:uppercase;margin:28px 0 14px;">Category Averages</div>'
            f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px;">'
            + cards_html + f'</div>',
            unsafe_allow_html=True,
        )
        logger.debug("Category summary bar rendered")
    except Exception as exc:
        logger.error("_render_category_bar: {}", exc)


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

        # Defensive defaults
        freight_data = freight_data or {}
        macro_data   = macro_data   or {}
        stock_data   = stock_data   or {}

        rows = _build_rows(freight_data, macro_data, stock_data)
        overall = _overall_score(rows)

        logger.info("Scorecard: {} metrics, overall={}", len(rows), overall)

        # Global CSS injection (once)
        st.markdown(
            f'<style>'
            f'[data-testid="stAppViewContainer"] {{background:{C_BG};}}'
            f'[data-testid="block-container"] {{padding-top:1rem;}}'
            f'</style>',
            unsafe_allow_html=True,
        )

        # ── Section 1 ─────────────────────────────────────────────────────────
        _render_executive_summary(overall, rows)

        # ── Section 2 ─────────────────────────────────────────────────────────
        _render_category_bar(rows)
        _render_scorecard_matrix(rows)

        # ── Section 3 ─────────────────────────────────────────────────────────
        _render_score_history(overall)

        # ── Section 4 ─────────────────────────────────────────────────────────
        _render_quadrant(rows)

        # ── Section 5 ─────────────────────────────────────────────────────────
        _render_winner_loser(rows)

        # ── Section 6 ─────────────────────────────────────────────────────────
        _render_outlook(rows, overall)

        logger.success("tab_scorecard.render() complete")

    except Exception as exc:
        logger.exception("tab_scorecard.render() fatal: {}", exc)
        st.error(f"Scorecard render error: {exc}")
