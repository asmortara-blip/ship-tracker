"""Daily shipping intelligence digest builder.

Synthesises port results, route results, engine insights, freight data,
macro data and stock data into a structured DailyDigest that can be
rendered as HTML email, Markdown (Slack/Discord) or JSON.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List

from loguru import logger


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DailyDigest:
    """Structured daily shipping intelligence digest."""

    date: str
    headline: str
    market_sentiment: str                    # BULLISH | BEARISH | NEUTRAL | MIXED
    sentiment_score: float                   # -1.0 to +1.0
    executive_summary: str                   # 3-paragraph prose
    top_opportunities: List[dict]            # top 3 routes/insights
    key_risks: List[str]                     # 3 risk strings
    port_highlights: List[dict]              # top 3 ports by demand
    freight_rate_moves: List[dict]           # biggest movers
    macro_snapshot: dict                     # key macro values
    stock_movers: List[dict]                 # shipping stocks with daily change
    data_quality: str                        # FULL | PARTIAL | DEGRADED
    generated_at: str


# ---------------------------------------------------------------------------
# Sentinel helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_str(val: Any, default: str = "N/A") -> str:
    if val is None:
        return default
    return str(val)


# ---------------------------------------------------------------------------
# Sentiment computation
# ---------------------------------------------------------------------------

_SENTIMENT_THRESHOLDS = [
    (0.35,  "BULLISH"),
    (0.10,  "MIXED"),
    (-0.10, "NEUTRAL"),
    (-0.35, "MIXED"),
]


def _compute_sentiment(
    insights: list,
    freight_data: dict,
    macro_data: dict,
) -> tuple[float, str]:
    """Return (score, label) where score is in [-1, 1]."""

    components: list[float] = []

    # 1. Average insight score (already [0,1], re-center to [-1,1])
    if insights:
        avg_score = sum(i.score for i in insights) / len(insights)
        components.append((avg_score - 0.5) * 2.0)
        logger.debug("Insight sentiment component: {:.3f}", components[-1])

    # 2. Freight rate trend (BDI direction or average rate change)
    bdi_change = _safe_float(freight_data.get("bdi_change_pct"), 0.0)
    if bdi_change != 0.0:
        # Clamp extreme moves to ±1
        components.append(max(-1.0, min(1.0, bdi_change / 10.0)))
        logger.debug("BDI freight component: {:.3f}", components[-1])

    # 3. Average freight rate moves list
    rate_moves = freight_data.get("rate_moves", [])
    if rate_moves:
        avg_move = sum(_safe_float(r.get("change_pct"), 0.0) for r in rate_moves) / len(rate_moves)
        components.append(max(-1.0, min(1.0, avg_move / 10.0)))
        logger.debug("Rate moves component: {:.3f}", components[-1])

    # 4. Macro: PMI relative to 50 baseline
    pmi = _safe_float(macro_data.get("global_pmi"), 0.0)
    if pmi > 0:
        components.append(max(-1.0, min(1.0, (pmi - 50.0) / 10.0)))
        logger.debug("PMI component: {:.3f}", components[-1])

    if not components:
        logger.warning("No sentiment components found; defaulting to NEUTRAL")
        return 0.0, "NEUTRAL"

    score = sum(components) / len(components)
    score = max(-1.0, min(1.0, score))

    if score >= 0.35:
        label = "BULLISH"
    elif score >= 0.10:
        label = "MIXED"
    elif score >= -0.10:
        label = "NEUTRAL"
    elif score >= -0.35:
        label = "MIXED"
    else:
        label = "BEARISH"

    logger.info("Computed sentiment: {} ({:.3f})", label, score)
    return score, label


# ---------------------------------------------------------------------------
# Opportunity extraction
# ---------------------------------------------------------------------------

def _pick_top_opportunities(insights: list, n: int = 3) -> list[dict]:
    """Filter CONVERGENCE + ROUTE insights, sort by score, return top n dicts."""
    eligible = [i for i in insights if i.category in ("CONVERGENCE", "ROUTE")]
    eligible.sort(key=lambda i: i.score, reverse=True)
    result: list[dict] = []
    for ins in eligible[:n]:
        result.append(
            {
                "title": ins.title,
                "score": round(ins.score, 4),
                "action": ins.action,
                "rationale": ins.detail,
                "category": ins.category,
                "routes": ins.routes_involved,
                "ports": ins.ports_involved,
            }
        )
    logger.debug("Picked {} top opportunities", len(result))
    return result


# ---------------------------------------------------------------------------
# Risk extraction
# ---------------------------------------------------------------------------

def _extract_key_risks(insights: list, macro_data: dict, freight_data: dict) -> list[str]:
    """Build 3 risk strings from bearish signals and data context."""
    risks: list[str] = []

    # Low-scoring convergence insights as risks
    caution_insights = [
        i for i in insights if i.action in ("Caution", "Avoid")
    ]
    caution_insights.sort(key=lambda i: i.score)
    for ins in caution_insights[:2]:
        risks.append(ins.title + ": " + ins.detail[:120].rstrip() + ("..." if len(ins.detail) > 120 else ""))

    # Macro risk
    gdp_growth = _safe_float(macro_data.get("gdp_growth_pct"), None)
    if gdp_growth is not None and gdp_growth < 1.5:
        risks.append(
            "Slowing macro growth (GDP " + str(round(gdp_growth, 2)) + "%) may compress freight demand over 2-4 quarters."
        )

    # Freight volatility risk
    volatility = _safe_float(freight_data.get("volatility_index"), 0.0)
    if volatility > 30.0:
        risks.append(
            "Elevated freight rate volatility (index " + str(round(volatility, 1)) + ") increases hedging cost and planning uncertainty."
        )

    # Fill to 3 with generic fallbacks
    fallbacks = [
        "Port congestion at key transhipment hubs could delay cargo 5-10 days and erode margins.",
        "Geopolitical disruptions on key chokepoints remain a tail risk for routing reliability.",
        "Currency fluctuations in emerging markets may distort reported freight rate gains.",
    ]
    for fb in fallbacks:
        if len(risks) >= 3:
            break
        risks.append(fb)

    logger.debug("Extracted {} key risks", len(risks[:3]))
    return risks[:3]


# ---------------------------------------------------------------------------
# Executive summary
# ---------------------------------------------------------------------------

def _build_executive_summary(
    insights: list,
    port_results: list,
    route_results: list,
    freight_data: dict,
    macro_data: dict,
    stock_data: list,
    sentiment_label: str,
    sentiment_score: float,
) -> str:
    """Build a 3-paragraph data-driven executive summary string."""

    n_insights = len(insights)
    n_conv = sum(1 for i in insights if i.category == "CONVERGENCE")
    n_high = sum(1 for i in insights if i.score >= 0.70)

    bdi = _safe_float(freight_data.get("bdi"), 0.0)
    bdi_chg = _safe_float(freight_data.get("bdi_change_pct"), 0.0)
    bdi_str = str(int(bdi)) if bdi else "N/A"
    bdi_dir = "up" if bdi_chg > 0 else ("down" if bdi_chg < 0 else "flat")

    top_port = port_results[0].get("port", "N/A") if port_results else "N/A"
    top_route = route_results[0].get("route", "N/A") if route_results else "N/A"

    pmi = _safe_float(macro_data.get("global_pmi"), 0.0)
    pmi_str = str(round(pmi, 1)) if pmi else "N/A"

    gainers = [s for s in stock_data if _safe_float(s.get("change_pct"), 0.0) > 0]

    # Paragraph 1: Market overview
    p1 = (
        "Global shipping markets are showing " + sentiment_label.lower() + " conditions today, "
        "with the decision engine surfacing " + str(n_insights) + " active signals across routes, "
        "port demand, and macro indicators. "
        "The Baltic Dry Index stands at " + bdi_str + ", " + bdi_dir + " " + str(abs(round(bdi_chg, 1))) + "% on the session, "
        "reflecting " + ("tightening vessel supply" if bdi_chg > 0 else "softening demand fundamentals") + ". "
        "Signal convergence is " + ("strong" if n_conv >= 2 else "moderate" if n_conv == 1 else "limited") + ", "
        "with " + str(n_conv) + " multi-signal convergence insights and " + str(n_high) + " high-conviction opportunities flagged."
    )

    # Paragraph 2: Opportunities and routes
    p2 = (
        "The highest-priority opportunity this session centres on " + top_route + ", "
        "driven by elevated port demand at " + top_port + " and supportive macro tailwinds. "
        "Global PMI reads " + pmi_str + ", "
        + ("above the 50-expansion threshold, underpinning near-term trade volume resilience. " if pmi and pmi > 50 else "below the 50-expansion threshold, warranting selective positioning. ")
        + "Operators are advised to focus allocation on convergence-rated routes where port demand and rate momentum align, "
        "while maintaining flexibility on routes showing single-signal support only."
    )

    # Paragraph 3: Risks and outlook
    gainer_str = str(len(gainers)) + " of " + str(len(stock_data)) if stock_data else "several"
    p3 = (
        "Key risks include geopolitical exposure on sensitive chokepoints and freight rate volatility that could compress margins if demand softens. "
        "Shipping equities are mixed, with " + gainer_str + " tracked names advancing on the session. "
        "Looking ahead, data quality is " + ("strong across all feeds" if n_insights > 5 else "partial — additional data refreshes are recommended") + ". "
        "Operators should revisit this digest at the next scheduled refresh and cross-reference with live AIS positioning for execution timing."
    )

    return p1 + "\n\n" + p2 + "\n\n" + p3


# ---------------------------------------------------------------------------
# Port highlights
# ---------------------------------------------------------------------------

def _build_port_highlights(port_results: list) -> list[dict]:
    highlights: list[dict] = []
    for pr in port_results[:3]:
        highlights.append(
            {
                "port": _safe_str(pr.get("port") or pr.get("name") or pr.get("locode")),
                "demand_score": round(_safe_float(pr.get("demand_score") or pr.get("score"), 0.0), 4),
                "trend": _safe_str(pr.get("trend")),
                "cargo_types": pr.get("cargo_types", []),
                "note": _safe_str(pr.get("note") or pr.get("detail"), ""),
            }
        )
    logger.debug("Built {} port highlights", len(highlights))
    return highlights


# ---------------------------------------------------------------------------
# Freight rate moves
# ---------------------------------------------------------------------------

def _build_freight_rate_moves(freight_data: dict) -> list[dict]:
    rate_moves = freight_data.get("rate_moves", [])
    processed: list[dict] = []
    for rm in rate_moves:
        chg = _safe_float(rm.get("change_pct"), 0.0)
        processed.append(
            {
                "route": _safe_str(rm.get("route") or rm.get("lane")),
                "rate": round(_safe_float(rm.get("rate") or rm.get("current_rate"), 0.0), 2),
                "change_pct": round(chg, 2),
                "direction": "up" if chg > 0 else ("down" if chg < 0 else "flat"),
                "currency": _safe_str(rm.get("currency"), "USD"),
            }
        )
    processed.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    logger.debug("Built {} freight rate move entries", len(processed))
    return processed


# ---------------------------------------------------------------------------
# Stock movers
# ---------------------------------------------------------------------------

def _build_stock_movers(stock_data: list) -> list[dict]:
    movers: list[dict] = []
    for s in stock_data:
        chg = _safe_float(s.get("change_pct") or s.get("daily_change_pct"), 0.0)
        movers.append(
            {
                "ticker": _safe_str(s.get("ticker") or s.get("symbol")),
                "name": _safe_str(s.get("name") or s.get("company")),
                "price": round(_safe_float(s.get("price") or s.get("close"), 0.0), 2),
                "change_pct": round(chg, 2),
                "direction": "up" if chg > 0 else ("down" if chg < 0 else "flat"),
            }
        )
    movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    logger.debug("Built {} stock mover entries", len(movers))
    return movers


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------

def _assess_data_quality(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data: dict,
    macro_data: dict,
    stock_data: list,
) -> str:
    present = sum([
        bool(port_results),
        bool(route_results),
        bool(insights),
        bool(freight_data),
        bool(macro_data),
        bool(stock_data),
    ])
    if present == 6:
        return "FULL"
    if present >= 3:
        return "PARTIAL"
    return "DEGRADED"


# ---------------------------------------------------------------------------
# Headline generator
# ---------------------------------------------------------------------------

def _build_headline(sentiment_label: str, insights: list, freight_data: dict) -> str:
    bdi_chg = _safe_float(freight_data.get("bdi_change_pct"), 0.0)
    n_conv = sum(1 for i in insights if i.category == "CONVERGENCE")

    if sentiment_label == "BULLISH":
        if n_conv >= 2:
            return "Multi-signal convergence sparks bullish freight momentum"
        return "Freight markets rally — BDI leads the charge " + ("+" + str(round(bdi_chg, 1)) + "%" if bdi_chg > 0 else "")
    if sentiment_label == "BEARISH":
        return "Freight demand softening — caution flags across key trade lanes"
    if sentiment_label == "MIXED":
        return "Cross-currents dominate: bullish routes offset by macro headwinds"
    return "Shipping markets hold steady — selective opportunities in focus"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_digest(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data: dict,
    macro_data: dict,
    stock_data: list,
) -> DailyDigest:
    """Synthesise all data sources into a structured DailyDigest.

    Parameters
    ----------
    port_results:  list of port demand dicts from the port analyser
    route_results: list of route opportunity dicts from the route optimiser
    insights:      list of Insight objects from the decision engine
    freight_data:  dict with keys like 'bdi', 'bdi_change_pct', 'rate_moves', 'volatility_index'
    macro_data:    dict with keys like 'global_pmi', 'gdp_growth_pct', 'cpi', 'fed_rate'
    stock_data:    list of stock dicts with 'ticker', 'price', 'change_pct'
    """
    logger.info("Building daily digest from {} insights, {} ports, {} routes",
                len(insights), len(port_results), len(route_results))

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    generated_at = now.isoformat()

    sentiment_score, sentiment_label = _compute_sentiment(insights, freight_data, macro_data)
    headline = _build_headline(sentiment_label, insights, freight_data)

    top_opportunities = _pick_top_opportunities(insights)
    key_risks = _extract_key_risks(insights, macro_data, freight_data)
    port_highlights = _build_port_highlights(port_results)
    freight_rate_moves = _build_freight_rate_moves(freight_data)
    stock_movers = _build_stock_movers(stock_data)
    data_quality = _assess_data_quality(
        port_results, route_results, insights, freight_data, macro_data, stock_data
    )

    # Macro snapshot — surface whichever keys are present
    macro_snapshot: dict = {}
    for key in ("global_pmi", "gdp_growth_pct", "cpi", "fed_rate", "usd_index",
                "oil_price", "container_throughput_yoy"):
        if key in macro_data:
            macro_snapshot[key] = macro_data[key]

    executive_summary = _build_executive_summary(
        insights, port_results, route_results,
        freight_data, macro_data, stock_data,
        sentiment_label, sentiment_score,
    )

    digest = DailyDigest(
        date=date_str,
        headline=headline,
        market_sentiment=sentiment_label,
        sentiment_score=round(sentiment_score, 4),
        executive_summary=executive_summary,
        top_opportunities=top_opportunities,
        key_risks=key_risks,
        port_highlights=port_highlights,
        freight_rate_moves=freight_rate_moves,
        macro_snapshot=macro_snapshot,
        stock_movers=stock_movers,
        data_quality=data_quality,
        generated_at=generated_at,
    )
    logger.success("Daily digest built: sentiment={} quality={}", sentiment_label, data_quality)
    return digest


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_SENTIMENT_COLORS = {
    "BULLISH":  ("#10b981", "#052e16"),
    "BEARISH":  ("#ef4444", "#2d0a0a"),
    "NEUTRAL":  ("#64748b", "#0f172a"),
    "MIXED":    ("#f59e0b", "#1c1408"),
}


def render_as_html(digest: DailyDigest) -> str:
    """Render the digest as a complete, standalone dark-theme HTML email."""

    sent_fg, sent_bg = _SENTIMENT_COLORS.get(digest.market_sentiment, ("#64748b", "#0f172a"))

    # --- Opportunity cards ---
    opp_cards_html = ""
    for idx, opp in enumerate(digest.top_opportunities):
        score_pct = str(round(opp["score"] * 100)) + "%"
        action = opp.get("action", "Monitor")
        title = opp.get("title", "")
        rationale = opp.get("rationale", "")[:220]
        routes_str = ", ".join(opp.get("routes", [])) or "N/A"
        action_color = {
            "Prioritize": "#10b981",
            "Monitor": "#3b82f6",
            "Watch": "#94a3b8",
            "Caution": "#f59e0b",
            "Avoid": "#ef4444",
        }.get(action, "#94a3b8")
        opp_cards_html += (
            "<div style='background:#1a2235; border:1px solid rgba(255,255,255,0.10);"
            " border-left:4px solid " + action_color + "; border-radius:12px;"
            " padding:20px 22px; margin-bottom:14px'>"
            "<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:10px'>"
            "<span style='background:rgba(255,255,255,0.07); color:#94a3b8; font-size:11px;"
            " font-weight:700; padding:3px 10px; border-radius:999px; letter-spacing:0.06em'>"
            "OPPORTUNITY " + str(idx + 1) + "</span>"
            "<span style='font-size:22px; font-weight:900; color:" + action_color + "'>" + score_pct + "</span>"
            "</div>"
            "<div style='font-size:16px; font-weight:700; color:#f1f5f9; margin-bottom:8px; line-height:1.4'>"
            + title + "</div>"
            "<div style='font-size:13px; color:#94a3b8; line-height:1.6; margin-bottom:10px'>" + rationale + "</div>"
            "<div style='font-size:11px; color:#64748b'>Routes: " + routes_str + "</div>"
            "<div style='margin-top:10px'>"
            "<span style='background:rgba(255,255,255,0.06); color:" + action_color + ";"
            " border:1px solid rgba(255,255,255,0.12); padding:3px 12px;"
            " border-radius:999px; font-size:12px; font-weight:700'>" + action + "</span>"
            "</div>"
            "</div>"
        )

    # --- Freight rate moves table ---
    rate_rows_html = ""
    for rm in digest.freight_rate_moves[:8]:
        chg = rm["change_pct"]
        dir_color = "#10b981" if rm["direction"] == "up" else ("#ef4444" if rm["direction"] == "down" else "#64748b")
        arrow = "&#9650;" if rm["direction"] == "up" else ("&#9660;" if rm["direction"] == "down" else "&#9654;")
        rate_rows_html += (
            "<tr>"
            "<td style='padding:9px 14px; color:#f1f5f9; font-size:13px; border-bottom:1px solid rgba(255,255,255,0.06)'>"
            + rm["route"] + "</td>"
            "<td style='padding:9px 14px; color:#94a3b8; font-size:13px; border-bottom:1px solid rgba(255,255,255,0.06)'>"
            + rm["currency"] + " " + str(rm["rate"]) + "</td>"
            "<td style='padding:9px 14px; font-size:13px; font-weight:700; color:" + dir_color + ";"
            " border-bottom:1px solid rgba(255,255,255,0.06)'>"
            + arrow + " " + ("+" if chg > 0 else "") + str(chg) + "%</td>"
            "</tr>"
        )

    # --- Key risks ---
    risks_html = ""
    for risk in digest.key_risks:
        risks_html += (
            "<li style='margin-bottom:10px; color:#94a3b8; font-size:14px; line-height:1.6'>"
            "<span style='color:#ef4444; font-weight:700; margin-right:6px'>&#9888;</span>"
            + risk + "</li>"
        )

    # --- Port highlights ---
    port_html = ""
    for ph in digest.port_highlights:
        score_pct = str(round(ph["demand_score"] * 100)) + "%"
        port_html += (
            "<div style='display:flex; justify-content:space-between; align-items:center;"
            " padding:10px 0; border-bottom:1px solid rgba(255,255,255,0.06)'>"
            "<div>"
            "<div style='font-size:14px; font-weight:700; color:#f1f5f9'>" + ph["port"] + "</div>"
            "<div style='font-size:12px; color:#64748b; margin-top:2px'>" + ph["trend"] + "</div>"
            "</div>"
            "<span style='font-size:18px; font-weight:800; color:#10b981'>" + score_pct + "</span>"
            "</div>"
        )

    # --- Stock movers ---
    stock_html = ""
    for sm in digest.stock_movers[:6]:
        chg = sm["change_pct"]
        s_color = "#10b981" if sm["direction"] == "up" else ("#ef4444" if sm["direction"] == "down" else "#64748b")
        stock_html += (
            "<div style='display:flex; justify-content:space-between; align-items:center;"
            " padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.06)'>"
            "<div>"
            "<span style='font-size:13px; font-weight:700; color:#f1f5f9; font-family:monospace'>"
            + sm["ticker"] + "</span>"
            "<span style='font-size:12px; color:#64748b; margin-left:8px'>" + sm["name"] + "</span>"
            "</div>"
            "<div style='text-align:right'>"
            "<div style='font-size:13px; color:#94a3b8'>$" + str(sm["price"]) + "</div>"
            "<div style='font-size:13px; font-weight:700; color:" + s_color + "'>"
            + ("+" if chg > 0 else "") + str(chg) + "%</div>"
            "</div>"
            "</div>"
        )

    # --- Macro snapshot ---
    macro_html = ""
    label_map = {
        "global_pmi": "Global PMI",
        "gdp_growth_pct": "GDP Growth",
        "cpi": "CPI",
        "fed_rate": "Fed Rate",
        "usd_index": "USD Index",
        "oil_price": "Oil Price",
        "container_throughput_yoy": "Container YoY",
    }
    for k, v in digest.macro_snapshot.items():
        macro_html += (
            "<div style='display:flex; justify-content:space-between; padding:7px 0;"
            " border-bottom:1px solid rgba(255,255,255,0.06)'>"
            "<span style='font-size:12px; color:#64748b'>" + label_map.get(k, k) + "</span>"
            "<span style='font-size:13px; font-weight:700; color:#f1f5f9'>" + str(v) + "</span>"
            "</div>"
        )

    # --- Executive summary paragraphs ---
    paras = digest.executive_summary.split("\n\n")
    exec_html = ""
    for para in paras:
        exec_html += (
            "<p style='font-size:14px; color:#94a3b8; line-height:1.8; margin:0 0 14px 0'>"
            + para.strip() + "</p>"
        )

    dq_color = {"FULL": "#10b981", "PARTIAL": "#f59e0b", "DEGRADED": "#ef4444"}.get(digest.data_quality, "#64748b")

    html = (
        "<!DOCTYPE html>"
        "<html lang='en'>"
        "<head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        "<title>Shipping Intelligence Daily - " + digest.date + "</title>"
        "</head>"
        "<body style='margin:0; padding:0; background:#0b1120; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif'>"

        # Outer wrapper
        "<table width='100%' cellpadding='0' cellspacing='0' style='background:#0b1120'>"
        "<tr><td align='center' style='padding:32px 16px'>"
        "<table width='640' cellpadding='0' cellspacing='0' style='max-width:640px; width:100%'>"

        # Header
        "<tr><td style='"
        "background:linear-gradient(135deg,#1a2235 0%,#0f1b2d 100%);"
        "border-radius:14px 14px 0 0; padding:32px 36px;"
        "border:1px solid rgba(255,255,255,0.08); border-bottom:none'>"
        "<div style='font-size:12px; font-weight:700; color:#3b82f6; text-transform:uppercase;"
        " letter-spacing:0.10em; margin-bottom:10px'>&#128674; Shipping Intelligence Daily</div>"
        "<div style='font-size:26px; font-weight:800; color:#f1f5f9; line-height:1.3; margin-bottom:8px'>"
        + digest.headline + "</div>"
        "<div style='font-size:12px; color:#64748b'>" + digest.date + "</div>"
        "</td></tr>"

        # Sentiment banner
        "<tr><td style='background:" + sent_bg + "; padding:16px 36px;"
        " border-left:1px solid rgba(255,255,255,0.08); border-right:1px solid rgba(255,255,255,0.08)'>"
        "<div style='display:flex; align-items:center; gap:12px'>"
        "<span style='font-size:28px; font-weight:900; color:" + sent_fg + ";"
        " letter-spacing:0.04em'>" + digest.market_sentiment + "</span>"
        "<span style='font-size:13px; color:" + sent_fg + "; opacity:0.7'>"
        "Sentiment score: " + str(digest.sentiment_score) + "</span>"
        "</div>"
        "</td></tr>"

        # Body
        "<tr><td style='background:#111827; padding:28px 36px;"
        " border:1px solid rgba(255,255,255,0.08); border-top:none; border-bottom:none'>"

        # Executive summary
        "<div style='font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;"
        " letter-spacing:0.09em; margin-bottom:14px'>Executive Summary</div>"
        + exec_html

        # Top Opportunities
        + "<div style='font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;"
        " letter-spacing:0.09em; margin:24px 0 14px 0'>Top Opportunities</div>"
        + opp_cards_html

        # Freight Rate Moves
        + "<div style='font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;"
        " letter-spacing:0.09em; margin:24px 0 14px 0'>Freight Rate Moves</div>"
        "<table width='100%' cellpadding='0' cellspacing='0' style='border-collapse:collapse;"
        " background:#1a2235; border-radius:10px; overflow:hidden;"
        " border:1px solid rgba(255,255,255,0.08)'>"
        "<thead><tr>"
        "<th style='padding:10px 14px; text-align:left; font-size:11px; font-weight:700;"
        " color:#64748b; text-transform:uppercase; letter-spacing:0.06em'>Route</th>"
        "<th style='padding:10px 14px; text-align:left; font-size:11px; font-weight:700;"
        " color:#64748b; text-transform:uppercase; letter-spacing:0.06em'>Rate</th>"
        "<th style='padding:10px 14px; text-align:left; font-size:11px; font-weight:700;"
        " color:#64748b; text-transform:uppercase; letter-spacing:0.06em'>Change</th>"
        "</tr></thead>"
        "<tbody>" + rate_rows_html + "</tbody>"
        "</table>"

        # Two-column: Port highlights + Stock movers
        + "<table width='100%' cellpadding='0' cellspacing='0' style='margin-top:24px'>"
        "<tr valign='top'>"
        "<td width='48%' style='padding-right:14px'>"
        "<div style='font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;"
        " letter-spacing:0.09em; margin-bottom:12px'>Port Demand Highlights</div>"
        + port_html
        + "</td>"
        "<td width='4%'></td>"
        "<td width='48%'>"
        "<div style='font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;"
        " letter-spacing:0.09em; margin-bottom:12px'>Shipping Stocks</div>"
        + stock_html
        + "</td>"
        "</tr>"
        "</table>"

        # Macro snapshot
        + "<div style='font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;"
        " letter-spacing:0.09em; margin:24px 0 12px 0'>Macro Snapshot</div>"
        "<div style='background:#1a2235; border:1px solid rgba(255,255,255,0.08);"
        " border-radius:10px; padding:12px 16px'>"
        + macro_html
        + "</div>"

        # Key risks
        + "<div style='font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;"
        " letter-spacing:0.09em; margin:24px 0 12px 0'>Key Risks</div>"
        "<ul style='margin:0; padding-left:18px'>"
        + risks_html
        + "</ul>"

        + "</td></tr>"

        # Footer
        "<tr><td style='background:#0f1b2d; border-radius:0 0 14px 14px; padding:20px 36px;"
        " border:1px solid rgba(255,255,255,0.08); border-top:none; text-align:center'>"
        "<div style='font-size:11px; color:#64748b'>Generated by Ship Tracker | Data as of "
        + digest.generated_at
        + "</div>"
        "<div style='margin-top:6px'>"
        "<span style='font-size:10px; padding:2px 10px; border-radius:999px;"
        " background:rgba(255,255,255,0.05); color:" + dq_color + ";"
        " border:1px solid " + dq_color + "; font-weight:700'>"
        "Data Quality: " + digest.data_quality + "</span>"
        "</div>"
        "</td></tr>"

        "</table>"  # inner 640px table
        "</td></tr>"
        "</table>"  # outer table
        "</body></html>"
    )

    logger.debug("Rendered HTML digest ({} chars)", len(html))
    return html


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

_SENTIMENT_EMOJI = {
    "BULLISH":  "🟢",
    "BEARISH":  "🔴",
    "NEUTRAL":  "⚪",
    "MIXED":    "🟡",
}

_DQ_EMOJI = {"FULL": "✅", "PARTIAL": "⚠️", "DEGRADED": "❌"}


def render_as_markdown(digest: DailyDigest) -> str:
    """Render digest as Markdown suitable for Slack/Discord (max ~1500 chars)."""

    sent_emoji = _SENTIMENT_EMOJI.get(digest.market_sentiment, "⚪")
    dq_emoji = _DQ_EMOJI.get(digest.data_quality, "❓")

    lines: list[str] = []
    lines.append("🚢 **Shipping Intelligence Daily — " + digest.date + "**")
    lines.append("")
    lines.append(sent_emoji + " **" + digest.market_sentiment + "** _(score: " + str(digest.sentiment_score) + ")_")
    lines.append("> " + digest.headline)
    lines.append("")

    # Executive summary — first paragraph only for brevity
    first_para = digest.executive_summary.split("\n\n")[0]
    if len(first_para) > 300:
        first_para = first_para[:297] + "..."
    lines.append("**Summary:** " + first_para)
    lines.append("")

    # Top opportunities
    lines.append("**📈 Top Opportunities**")
    for idx, opp in enumerate(digest.top_opportunities):
        score_pct = str(round(opp["score"] * 100)) + "%"
        lines.append(str(idx + 1) + ". **" + opp["title"] + "** — " + opp["action"] + " (" + score_pct + ")")
    lines.append("")

    # Key risks
    lines.append("**⚠️ Key Risks**")
    for risk in digest.key_risks:
        short_risk = risk[:100] + ("..." if len(risk) > 100 else "")
        lines.append("• " + short_risk)
    lines.append("")

    # Freight movers
    lines.append("**📊 Freight Rate Moves**")
    for rm in digest.freight_rate_moves[:4]:
        arrow = "↑" if rm["direction"] == "up" else ("↓" if rm["direction"] == "down" else "→")
        chg_str = ("+" if rm["change_pct"] > 0 else "") + str(rm["change_pct"]) + "%"
        lines.append("• " + rm["route"] + " " + arrow + " " + chg_str)
    lines.append("")

    # Port highlights
    lines.append("**🏗️ Port Demand**")
    for ph in digest.port_highlights:
        lines.append("• " + ph["port"] + " — " + str(round(ph["demand_score"] * 100)) + "% demand")
    lines.append("")

    # Data quality + timestamp
    lines.append(dq_emoji + " Data: " + digest.data_quality + " | " + digest.generated_at[:16] + " UTC")

    result = "\n".join(lines)

    # Hard cap at 1500 chars for Slack compatibility
    if len(result) > 1500:
        result = result[:1497] + "..."

    logger.debug("Rendered Markdown digest ({} chars)", len(result))
    return result


# ---------------------------------------------------------------------------
# JSON renderer
# ---------------------------------------------------------------------------

def render_as_json(digest: DailyDigest) -> str:
    """Render digest as pretty-printed JSON for API consumption."""
    payload = {
        "date": digest.date,
        "headline": digest.headline,
        "market_sentiment": digest.market_sentiment,
        "sentiment_score": digest.sentiment_score,
        "executive_summary": digest.executive_summary,
        "top_opportunities": digest.top_opportunities,
        "key_risks": digest.key_risks,
        "port_highlights": digest.port_highlights,
        "freight_rate_moves": digest.freight_rate_moves,
        "macro_snapshot": digest.macro_snapshot,
        "stock_movers": digest.stock_movers,
        "data_quality": digest.data_quality,
        "generated_at": digest.generated_at,
    }
    result = json.dumps(payload, indent=2, ensure_ascii=False)
    logger.debug("Rendered JSON digest ({} chars)", len(result))
    return result
