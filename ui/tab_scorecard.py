"""ui/tab_scorecard.py — Executive Shipping Market Scorecard.

render(port_results, route_results, insights, freight_data, macro_data, stock_data)

A flagship financial-terminal scorecard: a ~30-metric executive summary across
six market pillars (freight, supply, demand, infrastructure, financial, risk),
distilled into one composite score with a headline gauge, pillar bars, an
institutional metric matrix, a 12-month score history, a supply/demand quadrant,
the week's winner and loser, and a forward 30-day outlook.

Canonical tab pattern (mirrors ``ui/tab_rate_analytics.py`` /
``ui/tab_disruption_radar.py``):
  * palette + components imported from ``ui/styles.py`` — never redeclared;
  * no hand-rolled inline-styled ``<div>`` blocks — every block is a
    ``ui/styles.py`` helper, or a ``wsj_market_table`` cell formatted with
    span content;
  * every section wrapped in try/except + ``logger.exception``;
  * labeled ``section_divider`` between the five chapters of the page;
  * ``source_footer`` at the bottom of each data block.

Sections
--------
1. Executive Summary    — headline gauge, four-pillar KPI strip, AI outlook card
2. Category Averages    — six-pillar score cards plus a relative heat bar
3. Scorecard Matrix     — 30-metric institutional table, grouped by pillar
4. Score History        — 12-month composite trend with event annotations
5. Quadrant Analysis    — supply vs demand plane, current vs historical regimes
6. Winner / Loser       — strongest, weakest, biggest week-over-week surprise
7. Forward 30-Day Outlook — five predictions with confidence bands and key risk
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
    alert_banner,
    apply_dark_layout,
    badge,
    gauge_ring,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    shipping_heat_bar,
    source_footer,
    wsj_market_table,
)


# ── Provenance (demo data sources) ────────────────────────────────────────────
_SCORECARD_SOURCES = [
    {"name": "Composite scorecard model",    "kind": "modeled", "quality": "demo"},
    {"name": "Internal market signal blend", "kind": "modeled", "quality": "demo"},
]


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

# One-line editorial framing per pillar — surfaced as the matrix sub-caption so
# the table reads with context rather than as a bare grid.
_CATEGORY_GLOSS = {
    "Freight Markets": "spot and contract rate strength across the major lanes",
    "Supply":          "vessel capacity, deliveries and scrapping discipline",
    "Demand":          "underlying trade-volume and consumer pull",
    "Infrastructure":  "port, canal and inland-network throughput",
    "Financial":       "carrier earnings power, equity and credit conditions",
    "Risk":            "geopolitical, weather and regulatory headwinds",
}

# insight_card_html() colors the action chip from ACTION_COLORS — use only keys
# that resolve to a palette color so the chip is never a fallback gray.
_ACTION_BY_BAND = {"GREEN": "Prioritize", "AMBER": "Watch", "RED": "Caution"}


# ── Cell formatters for WSJ market tables ─────────────────────────────────────
# wsj_market_table() renders each cell string as raw HTML inside a <td>. The
# table CSS already handles alignment, rule lines and hover — these helpers
# only style *content* (font family, weight, conditional color).

def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    """Monospace numeric cell content with tabular figures."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _eyebrow(value: str, color: str = C_TEXT3) -> str:
    """Small uppercase tracking-wide label — used for pillar tags in the matrix."""
    return (
        f'<span style="font-family:var(--sans);color:{color};font-weight:700;'
        f'font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;">'
        f'{value}</span>'
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
    """Return (label, color) for a 0-100 score on the GREEN/AMBER/RED scale."""
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
                return "▬"
            return "▲" if delta > 0 else "▼"
        if cur_score > prior_score + 2:
            return "▲"
        if cur_score < prior_score - 2:
            return "▼"
        return "▬"
    except Exception:
        return "▬"


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


def _category_avg(rows: list[dict], cats: list[str]) -> int:
    """Mean score across one or more pillars; 50 when no rows match."""
    s = [r["score"] for r in rows if r["category"] in cats]
    return int(sum(s) / len(s)) if s else 50


def _hex_alpha(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── Section 1: Executive Summary ──────────────────────────────────────────────

def _render_executive_summary(overall: int, rows: list[dict]) -> None:
    """Headline composite gauge, four-pillar KPI strip, and AI outlook card."""
    try:
        rag_label, rag_color = _rag(overall)
        freight_avg = _category_avg(rows, ["Freight Markets"])
        demand_avg  = _category_avg(rows, ["Demand"])
        risk_avg    = _category_avg(rows, ["Risk"])

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
            banner_level = "success"
            banner_gloss = "conditions are constructive across most pillars"
        elif overall >= 40:
            summary += (
                "Mixed signals persist across freight corridors: demand recovery is uneven and "
                "supply additions are compressing margins. Operators should monitor blank sailing "
                "announcements and canal disruption risk closely over the next 30 days."
            )
            banner_level = "warning"
            banner_gloss = "signals are mixed and warrant close monitoring"
        else:
            summary += (
                "Deteriorating freight conditions, excess supply, and softening demand create "
                "headwinds across all major trade lanes. Capital discipline and route optimization "
                "are critical. Watch for further rate erosion and potential carrier consolidation."
            )
            banner_level = "critical"
            banner_gloss = "headwinds are broad-based across the network"

        action = _ACTION_BY_BAND.get(rag_label, "Watch")

        section_header(
            "Executive Summary — Composite Market Score",
            f"Week of {_week_label()} · {len(rows)} tracked metrics across six market pillars",
        )

        # ── Headline state banner — reads before the eye reaches the gauge ──
        alert_banner(
            f"Composite market score is <b>{overall}/100</b> "
            f"(<b>{rag_label}</b>) — {banner_gloss}.",
            level=banner_level,
        )

        # ── Hero row: composite gauge beside the pillar KPI strip ──
        col_gauge, col_strip = st.columns([2, 5], gap="large")

        with col_gauge:
            fig = gauge_ring(
                overall / 100.0,
                f"{rag_label} · composite",
                color=rag_color,
                size=224,
            )
            st.plotly_chart(
                fig,
                use_container_width=True,
                config={"displayModeBar": False},
                key="scorecard_composite_gauge",
            )

        with col_strip:
            metric_card_row(
                [
                    {
                        "label":       "Composite Score",
                        "value":       f"{overall}",
                        "delta":       rag_label,
                        "delta_color": rag_color,
                        "sublabel":    "0–100 blended market read",
                        "accent":      rag_color,
                    },
                    {
                        "label":    "Freight Conditions",
                        "value":    f"{freight_avg}",
                        "sublabel": "spot & contract rate strength",
                        "accent":   C_ACCENT,
                    },
                    {
                        "label":    "Demand Fundamentals",
                        "value":    f"{demand_avg}",
                        "sublabel": "trade-volume & consumer pull",
                        "accent":   C_HIGH,
                    },
                    {
                        "label":    "Risk Environment",
                        "value":    f"{risk_avg}",
                        "sublabel": "higher reads = calmer seas",
                        "accent":   C_MOD,
                    },
                ],
                columns=2,
            )

        st.markdown(
            insight_card_html(
                title="Composite Market Outlook",
                score=overall / 100.0,
                action=action,
                rationale=summary,
                category="SCORECARD",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer(_SCORECARD_SOURCES), unsafe_allow_html=True)
        logger.debug("Executive summary rendered — overall={}", overall)
    except Exception as exc:
        logger.exception("_render_executive_summary: {}", exc)
        st.error("Executive summary unavailable.")


# ── Section 1b: Editorial Commentary (per-tab LLM + template fallback) ───────

def _render_editorial_commentary(rows: list[dict], overall: int) -> None:
    """1-2 paragraph editorial read on the current scorecard snapshot.

    Sits between the executive summary and the per-pillar deep dive. Wraps
    the engine call in try/except — template fallback is safe; the only
    failure mode we guard against here is import / DB errors.
    """
    try:
        from engine.tab_commentary import build_commentary

        rag_label, _ = _rag(overall)
        category_avgs = {
            cat: _category_avg(rows, [cat]) for cat in _CATEGORY_ORDER
        }
        # Identify the strongest and weakest pillar so the commentary can
        # name them without re-doing the work in the LLM prompt.
        if category_avgs:
            strongest = max(category_avgs, key=category_avgs.get)
            weakest = min(category_avgs, key=category_avgs.get)
        else:
            strongest = weakest = ""

        best = max(rows, key=lambda r: r["score"]) if rows else None
        worst = min(rows, key=lambda r: r["score"]) if rows else None

        context: dict[str, object] = {
            "composite_score": int(overall),
            "rag_band": rag_label,
            "n_metrics": len(rows),
            "pillar_averages": {k: int(v) for k, v in category_avgs.items()},
            "strongest_pillar": strongest,
            "weakest_pillar": weakest,
            "week_of": _week_label(),
        }
        if best is not None:
            context["best_metric"] = (
                f"{best['metric']} ({best['category']}, score {best['score']})"
            )
        if worst is not None:
            context["worst_metric"] = (
                f"{worst['metric']} ({worst['category']}, score {worst['score']})"
            )

        commentary = build_commentary("Scorecard", context)

        section_header(
            "Editorial",
            subtitle=(
                "LLM-narrated read on the current scorecard snapshot. "
                "Falls back to a deterministic template when no API key is "
                "configured."
            ),
        )

        source_label, source_color = (
            ("LLM", C_HIGH) if commentary.source == "llm"
            else ("Template", C_MOD)
        )
        meta_bits = [f"<span style='color:{source_color}'>{source_label}</span>"]
        if commentary.source == "llm" and commentary.model:
            meta_bits.append(
                f"<code style='font-size:0.66rem;color:{C_TEXT3}'>{commentary.model}</code>"
            )
        if commentary.tokens_in or commentary.tokens_out:
            meta_bits.append(
                f"<span style='font-size:0.66rem;color:{C_TEXT3}'>"
                f"{commentary.tokens_in}→{commentary.tokens_out} tok</span>"
            )

        body_html = "".join(
            f'<p style="margin:0 0 10px 0;font-size:0.86rem;line-height:1.55;'
            f'color:{C_TEXT2}">{para.strip()}</p>'
            for para in commentary.body.split("\n\n") if para.strip()
        )
        st.markdown(
            f'<div style="background:rgba(53,114,176,0.06);'
            f'border-left:3px solid {C_ACCENT};padding:14px 18px;border-radius:3px;'
            f'margin-bottom:14px">'
            f'<div style="font-size:0.66rem;text-transform:uppercase;letter-spacing:0.14em;'
            f'color:{C_TEXT3};font-weight:600;margin-bottom:6px">'
            f'Source: {" · ".join(meta_bits)}'
            f'</div>'
            f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1.05rem;'
            f'line-height:1.4;color:{C_TEXT};font-weight:600;margin-bottom:10px">'
            f'{commentary.headline}</div>'
            f'{body_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Scorecard — editorial commentary render failed")


# ── Section 2: Category Averages ──────────────────────────────────────────────

def _render_category_bar(rows: list[dict]) -> None:
    """Six-pillar score cards plus a relative-strength heat bar."""
    try:
        section_header(
            "Category Averages",
            "Aggregate scores across the six scorecard pillars — strongest pillars carry the composite",
        )

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
                "label":       cat.upper(),
                "value":       f"{avg}",
                "delta":       rag_label,
                "delta_color": color,
                "sublabel":    _CATEGORY_GLOSS.get(cat, "/ 100"),
                "accent":      color,
            })

        metric_card_row(metrics, columns=6)

        # ── Relative-strength heat bar — pillars sized by their average score ──
        heat = {cat: round(cat_avgs[cat] / 100.0, 2) for cat in _CATEGORY_ORDER}
        shipping_heat_bar(heat, title="Pillar strength — wider = stronger")

        st.markdown(source_footer(_SCORECARD_SOURCES), unsafe_allow_html=True)
        logger.debug("Category summary bar rendered")
    except Exception as exc:
        logger.exception("_render_category_bar: {}", exc)
        st.error("Category averages unavailable.")


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
                "trend": "▬",
                "notes": "N/A",
                "series": [],
            })
    return rows


def _render_scorecard_matrix(rows: list[dict]) -> None:
    """30-metric institutional table, grouped pillar-by-pillar for legibility."""
    try:
        section_header(
            "Scorecard Matrix — 30 Metrics",
            "Per-metric score, RAG rating and week-over-week trend, grouped by pillar",
        )

        if not rows:
            alert_banner("No scorecard metrics available.", level="info")
            return

        headers = ["Pillar", "Metric", "Score", "Rating", "Prior", "Δ WoW", "Trend", "Read"]
        table_rows = []

        # Render pillar by pillar so the matrix reads as six labeled blocks
        # rather than one undifferentiated 30-row grid.
        for cat in _CATEGORY_ORDER:
            cat_rows = [r for r in rows if r["category"] == cat]
            if not cat_rows:
                continue
            cat_color = _CATEGORY_COLORS.get(cat, C_TEXT3)
            for j, row in enumerate(sorted(cat_rows, key=lambda r: -r["score"])):
                trend_color = (
                    C_HIGH if row["trend"] == "▲"
                    else C_LOW if row["trend"] == "▼"
                    else C_TEXT3
                )
                delta = row["score"] - row["prior_score"]
                delta_color = (
                    C_HIGH if delta > 0 else C_LOW if delta < 0 else C_TEXT3
                )
                delta_text = f"+{delta}" if delta > 0 else (str(delta) if delta < 0 else "0")
                # Pillar tag prints once per group; blank thereafter so the eye
                # reads each pillar as a contiguous band.
                pillar_cell = (
                    _eyebrow(cat.upper(), color=cat_color)
                    if j == 0 else _sans("", color=C_TEXT3)
                )
                table_rows.append([
                    pillar_cell,
                    _sans(row["metric"], color=C_TEXT, weight=600),
                    _mono(str(row["score"]), color=row["rag_color"], weight=700),
                    badge(row["rag_label"], color=row["rag_color"]),
                    _mono(str(row["prior_score"]), color=C_TEXT3),
                    _mono(delta_text, color=delta_color, weight=600),
                    _mono(row["trend"], color=trend_color, weight=700),
                    _sans(row["notes"], color=C_TEXT2),
                ])

        wsj_market_table(headers, table_rows)
        st.markdown(source_footer(_SCORECARD_SOURCES), unsafe_allow_html=True)
        logger.debug("Scorecard matrix rendered — {} rows", len(table_rows))
    except Exception as exc:
        logger.exception("_render_scorecard_matrix: {}", exc)
        st.error("Scorecard matrix unavailable.")


# ── Section 4: Score History Chart ────────────────────────────────────────────

def _render_score_history(overall: int) -> None:
    """12-month composite trend with event annotations and RAG threshold guides."""
    try:
        section_header(
            "Composite Score — 12-Month History",
            "Composite trend with market-event annotations and GREEN / RED threshold guides",
        )
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
        cur_color = _rag(overall)[1]

        events = {
            2: ("Suez Disruption", C_LOW),
            5: ("Rate Rebound", C_HIGH),
            8: ("China Reopening", C_ACCENT),
            11: ("Q4 Peak Season", C_MOD),
        }

        fig = go.Figure()

        # Soft band between the RAG thresholds — gives the trend line a stage.
        fig.add_hrect(y0=40, y1=65, fillcolor=_hex_alpha(C_MOD, 0.04),
                      line_width=0, layer="below")

        fig.add_trace(go.Scatter(
            x=labels, y=scores,
            mode="lines",
            line=dict(color=cur_color, width=2.6, shape="spline", smoothing=0.6),
            fill="tozeroy",
            fillcolor=_hex_alpha(cur_color, 0.08),
            name="Composite Score",
            hovertemplate="<b>%{x}</b><br>Score %{y}/100<extra></extra>",
        ))

        # Markers as a second trace so only the data points carry dots —
        # the latest point is enlarged as the "you are here" anchor.
        marker_sizes = [6] * len(labels)
        marker_lines = [0] * len(labels)
        if marker_sizes:
            marker_sizes[-1] = 12
            marker_lines[-1] = 1.6
        fig.add_trace(go.Scatter(
            x=labels, y=scores,
            mode="markers",
            marker=dict(
                size=marker_sizes,
                color=cur_color,
                line=dict(color=C_TEXT, width=marker_lines),
            ),
            showlegend=False,
            hoverinfo="skip",
        ))

        for idx, (label, color) in events.items():
            if idx < len(labels):
                fig.add_vline(x=labels[idx], line=dict(color=color, width=1, dash="dot"))
                fig.add_annotation(
                    x=labels[idx], y=scores[idx] + 7,
                    text=label, showarrow=False,
                    font=dict(size=9, color=color),
                    bgcolor=C_CARD,
                    bordercolor=_hex_alpha(color, 0.4),
                    borderpad=3,
                )

        fig.add_hline(y=65, line=dict(color=C_HIGH, width=1, dash="dash"),
                      annotation_text="GREEN 65",
                      annotation_position="right",
                      annotation_font=dict(color=C_HIGH, size=9))
        fig.add_hline(y=40, line=dict(color=C_LOW, width=1, dash="dash"),
                      annotation_text="RED 40",
                      annotation_position="right",
                      annotation_font=dict(color=C_LOW, size=9))

        apply_dark_layout(
            fig,
            title="",
            height=340,
            showlegend=False,
            xaxis=dict(showgrid=False, tickfont=dict(size=10)),
            yaxis=dict(range=[0, 100], tickfont=dict(size=10),
                       title="Composite Score", dtick=20),
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key="scorecard_history_chart",
        )
        st.markdown(source_footer(_SCORECARD_SOURCES), unsafe_allow_html=True)
        logger.debug("Score history chart rendered")
    except Exception as exc:
        logger.exception("_render_score_history: {}", exc)
        st.error("Score history chart unavailable.")


# ── Section 5: Quadrant Analysis ──────────────────────────────────────────────

def _render_quadrant(rows: list[dict]) -> None:
    """Supply vs demand plane — current position against historical regimes."""
    try:
        section_header(
            "Market Quadrant Analysis — Supply vs Demand Outlook",
            "Current position against historical regimes across the supply / demand plane",
        )
        supply_cats  = ["Supply"]
        demand_cats  = ["Demand", "Freight Markets"]

        cur_x = float(_category_avg(rows, supply_cats))
        cur_y = float(_category_avg(rows, demand_cats))

        # Plain-English read of which quadrant the current point sits in.
        if cur_x >= 50 and cur_y >= 50:
            zone_name, zone_color = "GOLDILOCKS", C_HIGH
        elif cur_x < 50 and cur_y >= 50:
            zone_name, zone_color = "UNDERSUPPLY", C_LOW
        elif cur_x >= 50 and cur_y < 50:
            zone_name, zone_color = "OVERSUPPLY", C_MOD
        else:
            zone_name, zone_color = "SLOWDOWN", C_TEXT3

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
            (0,  50,  0,  50,  _hex_alpha(C_TEXT3, 0.10), 25, 25, "SLOWDOWN",   C_TEXT3),
        ]

        for x0, x1, y0, y1, fill, lx, ly, label, lcolor in zone_defs:
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                          fillcolor=fill, line=dict(width=0), layer="below")
            fig.add_annotation(x=lx, y=ly, text=label, showarrow=False,
                               font=dict(size=10, color=lcolor),
                               opacity=0.55)

        for hname, hx, hy, hc in historical:
            fig.add_trace(go.Scatter(
                x=[hx], y=[hy], mode="markers+text",
                marker=dict(size=10, color=hc, opacity=0.55, symbol="circle"),
                text=[hname], textposition="top center",
                textfont=dict(size=9, color=hc),
                name=hname, showlegend=True,
                hovertemplate=f"<b>{hname}</b><br>Supply {hx}<br>Demand {hy}<extra></extra>",
            ))

        # Faint trail connecting the historical regimes in time order.
        fig.add_trace(go.Scatter(
            x=[h[1] for h in historical],
            y=[h[2] for h in historical],
            mode="lines",
            line=dict(color=_hex_alpha(C_TEXT3, 0.45), width=1, dash="dot"),
            showlegend=False, hoverinfo="skip",
        ))

        fig.add_trace(go.Scatter(
            x=[cur_x], y=[cur_y], mode="markers+text",
            marker=dict(size=20, color=zone_color, symbol="star",
                        line=dict(color=C_TEXT, width=1.6)),
            text=["NOW"], textposition="top center",
            textfont=dict(size=11, color=C_TEXT),
            name="Current", showlegend=True,
            hovertemplate=f"<b>Current — {zone_name}</b><br>Supply Outlook {cur_x:.0f}"
                          f"<br>Demand Outlook {cur_y:.0f}<extra></extra>",
        ))

        fig.add_hline(y=50, line=dict(color=C_BORDER, width=1))
        fig.add_vline(x=50, line=dict(color=C_BORDER, width=1))

        apply_dark_layout(
            fig,
            title="",
            height=420,
            xaxis=dict(range=[0, 100], title="Supply Outlook →",
                       tickfont=dict(size=9), dtick=25),
            yaxis=dict(range=[0, 100], title="Demand Outlook →",
                       tickfont=dict(size=9), dtick=25),
            legend=dict(font=dict(size=9, color=C_TEXT3),
                        bgcolor="rgba(0,0,0,0)",
                        bordercolor=C_BORDER, borderwidth=1),
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"displayModeBar": False},
            key="scorecard_quadrant_chart",
        )

        # Editorial read of where the current point landed.
        alert_banner(
            f"The market currently sits in the <b>{zone_name}</b> quadrant — "
            f"supply outlook <b>{cur_x:.0f}</b>, demand outlook <b>{cur_y:.0f}</b>.",
            level="info",
        )
        st.markdown(source_footer(_SCORECARD_SOURCES), unsafe_allow_html=True)
        logger.debug("Quadrant chart rendered — cur=({:.0f},{:.0f})", cur_x, cur_y)
    except Exception as exc:
        logger.exception("_render_quadrant: {}", exc)
        st.error("Quadrant analysis unavailable.")


# ── Section 6: Winner / Loser / Surprise ──────────────────────────────────────

def _render_winner_loser(rows: list[dict]) -> None:
    """Strongest, weakest and largest week-over-week surprise metric."""
    try:
        section_header(
            "Winner / Loser of the Week",
            "Strongest and weakest metrics, plus the largest week-over-week surprise",
        )

        if not rows:
            alert_banner("No scorecard metrics available to rank.", level="info")
            return

        best    = max(rows, key=lambda r: r["score"])
        worst   = min(rows, key=lambda r: r["score"])
        biggest = max(rows, key=lambda r: abs(r["score"] - r["prior_score"]))

        def _delta_label(score: int, prior: int) -> str:
            d = score - prior
            return f"+{d} vs prior week" if d >= 0 else f"{d} vs prior week"

        metric_card_row(
            [
                {
                    "label":       f"WINNER · {best['category'].upper()}",
                    "value":       best["metric"],
                    "delta":       _delta_label(best["score"], best["prior_score"]),
                    "delta_color": C_HIGH,
                    "sublabel":    f"Score {best['score']} / 100 — strongest of {len(rows)} metrics",
                    "accent":      C_HIGH,
                },
                {
                    "label":       f"LOSER · {worst['category'].upper()}",
                    "value":       worst["metric"],
                    "delta":       _delta_label(worst["score"], worst["prior_score"]),
                    "delta_color": C_LOW,
                    "sublabel":    f"Score {worst['score']} / 100 — weakest pillar, warrants attention",
                    "accent":      C_LOW,
                },
                {
                    "label":       f"BIGGEST SURPRISE · {biggest['category'].upper()}",
                    "value":       biggest["metric"],
                    "delta":       _delta_label(biggest["score"], biggest["prior_score"]),
                    "delta_color": C_MOD,
                    "sublabel":    (
                        f"{abs(biggest['score'] - biggest['prior_score'])}-point move — "
                        f"largest week-over-week swing"
                    ),
                    "accent":      C_MOD,
                },
            ],
            columns=3,
        )
        st.markdown(source_footer(_SCORECARD_SOURCES), unsafe_allow_html=True)
        logger.debug("Winner/loser section rendered")
    except Exception as exc:
        logger.exception("_render_winner_loser: {}", exc)
        st.error("Winner/loser section unavailable.")


# ── Section 7: Forward 30-day Outlook ─────────────────────────────────────────

def _render_outlook(rows: list[dict], overall: int) -> None:
    """Five forward-looking predictions with confidence bands and key risk."""
    try:
        freight_avg = _category_avg(rows, ["Freight Markets"])
        risk_avg    = _category_avg(rows, ["Risk"])

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

        section_header(
            "Forward 30-Day Outlook",
            "Five forward-looking predictions, each with a confidence band and its key risk",
        )

        # Lead caption — average confidence across the five calls.
        avg_conf = int(sum(p["confidence"] for p in predictions) / len(predictions))
        conf_level = "success" if avg_conf >= 70 else ("warning" if avg_conf >= 55 else "critical")
        alert_banner(
            f"Average forecast confidence is <b>{avg_conf}%</b> across "
            f"{len(predictions)} forward calls for the next 30 days.",
            level=conf_level,
        )

        for i, pred in enumerate(predictions):
            try:
                conf   = pred["confidence"]
                action = (
                    "Prioritize" if conf >= 70
                    else ("Watch" if conf >= 55 else "Caution")
                )
                rationale = (
                    f'{pred["body"]} <strong>Key risk:</strong> {pred["key_risk"]}'
                )
                st.markdown(
                    insight_card_html(
                        title=f"{i + 1}. {pred['title']}",
                        score=conf / 100.0,
                        action=action,
                        rationale=rationale,
                        category="MACRO",
                    ),
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.debug("outlook prediction {}: {}", i, exc)

        st.markdown(source_footer(_SCORECARD_SOURCES), unsafe_allow_html=True)
        logger.debug("Outlook section rendered — {} predictions", len(predictions))
    except Exception as exc:
        logger.exception("_render_outlook: {}", exc)
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
    """Render the executive shipping market scorecard.

    Parameters
    ----------
    port_results, route_results, insights:
        Platform-standard inputs computed at the top of ``app.py``; accepted for
        signature parity and may be empty — the scorecard is self-contained.
    freight_data, macro_data, stock_data:
        Keyed time-series dicts. Each may be ``None`` or empty; metrics without a
        live series fall back to a deterministic modeled score, so the tab
        renders cleanly with no inputs at all.
    """
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
            badge_text="SCORECARD",
            badge_color=C_ACCENT,
        )

        # ── Chapter 1: the headline read ──
        _render_executive_summary(overall, rows)

        # ── Editorial commentary (per-tab LLM + template fallback) ──
        _render_editorial_commentary(rows, overall)

        section_divider("Pillar Detail")
        _render_category_bar(rows)

        st.divider()
        _render_scorecard_matrix(rows)

        # ── Chapter 2: trends and positioning ──
        section_divider("Trends & Positioning")
        _render_score_history(overall)

        st.divider()
        _render_quadrant(rows)

        # ── Chapter 3: this week, and the month ahead ──
        section_divider("Week in Review & Outlook")
        _render_winner_loser(rows)

        st.divider()
        _render_outlook(rows, overall)

        logger.success("tab_scorecard.render() complete")

    except Exception as exc:
        logger.exception("tab_scorecard.render() fatal: {}", exc)
        st.error(f"Scorecard render error: {exc}")
