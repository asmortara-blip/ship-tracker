"""Intelligent Q&A Assistant tab for Ship Tracker.

render(port_results, route_results, insights, freight_data, macro_data,
       stock_data, route_results_all=None) is the public entry point.

Rule-based NLP answer engine — no external API calls.
"""
from __future__ import annotations

import datetime
from typing import Optional

import streamlit as st
from loguru import logger

from ports.demand_analyzer import PortDemandResult
from routes.optimizer import RouteOpportunity
from engine.insight import Insight
from ui.styles import (
    C_BG, C_CARD, C_BORDER, C_TEXT, C_TEXT2, C_TEXT3,
    C_HIGH, C_LOW, C_ACCENT, C_MOD,
    _hex_to_rgba as _rgba,
    section_header,
)


# ---------------------------------------------------------------------------
# Color / style helpers
# ---------------------------------------------------------------------------

_C_USER_BG  = "#1d4ed8"   # blue-700
_C_ASST_BG  = "#1a2235"   # dark card
_C_USER_TXT = "#ffffff"
_C_ASST_TXT = "#f1f5f9"


# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------

_CHAT_CSS = """
<style>
/* ── Chat bubble wrappers ───────────────────────────────────────────────── */
.chat-row {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    margin-bottom: 14px;
    animation: slide-in-up 0.25s ease-out both;
}
.chat-row.user {
    flex-direction: row-reverse;
}
.chat-row.assistant {
    flex-direction: row;
}

/* ── Avatars ────────────────────────────────────────────────────────────── */
.chat-avatar {
    font-size: 1.4rem;
    flex-shrink: 0;
    line-height: 1;
    margin-bottom: 4px;
}

/* ── Bubbles ────────────────────────────────────────────────────────────── */
.chat-bubble {
    max-width: 72%;
    padding: 12px 16px;
    border-radius: 16px;
    font-size: 0.88rem;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
}
.chat-bubble.user {
    background: #1d4ed8;
    color: #ffffff;
    border-bottom-right-radius: 4px;
}
.chat-bubble.assistant {
    background: #1a2235;
    color: #f1f5f9;
    border: 1px solid rgba(255,255,255,0.08);
    border-bottom-left-radius: 4px;
}

/* ── Timestamp ──────────────────────────────────────────────────────────── */
.chat-ts {
    font-size: 0.68rem;
    color: #475569;
    text-align: right;
    margin-top: 3px;
    padding: 0 4px;
}
.chat-ts.left {
    text-align: left;
}

/* ── Thinking dots ──────────────────────────────────────────────────────── */
@keyframes dot-bounce {
    0%, 80%, 100% { transform: scale(0.7); opacity: 0.4; }
    40%           { transform: scale(1.0); opacity: 1.0; }
}
.thinking-dots span {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: #3b82f6;
    margin: 0 2px;
    animation: dot-bounce 1.2s infinite ease-in-out;
}
.thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
.thinking-dots span:nth-child(3) { animation-delay: 0.4s; }

/* ── Sidebar-within-tab context panel ───────────────────────────────────── */
.ctx-panel {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 14px 16px;
    font-size: 0.80rem;
}
.ctx-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 5px 0;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    color: #94a3b8;
}
.ctx-row:last-child { border-bottom: none; }
.ctx-val { color: #f1f5f9; font-weight: 600; }

/* ── Quick-question buttons ─────────────────────────────────────────────── */
.stButton > button {
    background: #1a2235 !important;
    color: #94a3b8 !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    border-radius: 10px !important;
    font-size: 0.80rem !important;
    font-weight: 500 !important;
    text-align: left !important;
    width: 100% !important;
    transition: all 0.15s ease !important;
}
.stButton > button:hover {
    border-color: #3b82f6 !important;
    color: #f1f5f9 !important;
    background: #243050 !important;
    transform: translateY(-1px) !important;
}
</style>
"""


# ---------------------------------------------------------------------------
# Chat history helpers
# ---------------------------------------------------------------------------

def _init_history() -> None:
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []


def _append_message(role: str, text: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M")
    st.session_state.chat_history.append({"role": role, "text": text, "ts": ts})


def _render_bubble(role: str, text: str, ts: str) -> None:
    avatar = "🚢" if role == "assistant" else "👤"
    bubble_cls = "assistant" if role == "assistant" else "user"
    row_cls    = "assistant" if role == "assistant" else "user"
    ts_cls     = "left" if role == "assistant" else ""

    # escape < > & in text to avoid HTML injection
    safe_text = (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    html = (
        '<div class="chat-row ' + row_cls + '">'
        '<div class="chat-avatar">' + avatar + '</div>'
        '<div>'
        '<div class="chat-bubble ' + bubble_cls + '">' + safe_text + '</div>'
        '<div class="chat-ts ' + ts_cls + '">' + ts + '</div>'
        '</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _render_thinking() -> None:
    html = (
        '<div class="chat-row assistant">'
        '<div class="chat-avatar">🚢</div>'
        '<div class="chat-bubble assistant">'
        '<div class="thinking-dots">'
        '<span></span><span></span><span></span>'
        '</div>'
        '</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def _bdi_value(macro_data: dict) -> Optional[float]:
    df = macro_data.get("BDIY")
    if df is None or df.empty:
        return None
    vals = df["value"].dropna()
    return float(vals.iloc[-1]) if not vals.empty else None


def _bdi_change_30d(macro_data: dict) -> Optional[float]:
    df = macro_data.get("BDIY")
    if df is None or df.empty or "value" not in df.columns:
        return None
    import pandas as pd
    df2 = df.copy()
    if "date" in df2.columns:
        df2 = df2.sort_values("date")
    vals = df2["value"].dropna()
    if len(vals) < 2:
        return None
    current = float(vals.iloc[-1])
    if "date" in df2.columns:
        ref = df2["date"].max() - pd.Timedelta(days=30)
        mask = df2["date"] <= ref
        if not mask.any():
            return None
        ago = float(df2.loc[mask, "value"].dropna().iloc[-1])
    else:
        idx = max(0, len(vals) - 31)
        ago = float(vals.iloc[idx])
    if ago == 0:
        return None
    return (current - ago) / abs(ago) * 100


def _wti_value(macro_data: dict) -> Optional[float]:
    df = macro_data.get("DCOILWTICO")
    if df is None or df.empty:
        return None
    vals = df["value"].dropna()
    return float(vals.iloc[-1]) if not vals.empty else None


def _top_route(route_results: list[RouteOpportunity]) -> Optional[RouteOpportunity]:
    if not route_results:
        return None
    return max(route_results, key=lambda r: r.opportunity_score)


def _find_port(port_results: list[PortDemandResult], name_fragment: str) -> Optional[PortDemandResult]:
    frag = name_fragment.lower().strip()
    for p in port_results:
        if frag in p.port_name.lower() or frag in p.locode.lower() or frag in p.region.lower():
            return p
    return None


def _find_route(route_results: list[RouteOpportunity], fragment: str) -> Optional[RouteOpportunity]:
    frag = fragment.lower().strip()
    for r in route_results:
        if frag in r.route_name.lower() or frag in r.route_id.lower():
            return r
    return None


def _avg_co2_per_teu(route_results: list[RouteOpportunity]) -> float:
    """Estimate average CO2/TEU across routes using transit_days as proxy.

    Rough industry average: ~0.5 kg CO2e per km per TEU.
    Use transit_days * 450 km/day as distance proxy.
    """
    if not route_results:
        return 0.0
    vals = []
    for r in route_results:
        km_est = r.transit_days * 450
        co2 = km_est * 0.5 / 1000  # tonnes CO2
        vals.append(co2)
    return sum(vals) / len(vals)


def _market_sentiment(insights: list[Insight]) -> str:
    if not insights:
        return "neutral"
    avg_score = sum(i.score for i in insights) / len(insights)
    if avg_score >= 0.65:
        return "bullish"
    if avg_score <= 0.35:
        return "bearish"
    return "mixed"


def _stock_info(stock_data: dict, ticker: str) -> dict:
    """Extract price, 30d change from stock_data."""
    df = stock_data.get(ticker)
    if df is None or df.empty or "close" not in df.columns:
        return {}
    import pandas as pd
    df2 = df.copy()
    if "date" in df2.columns:
        df2 = df2.sort_values("date")
    vals = df2["close"].dropna()
    if vals.empty:
        return {}
    current = float(vals.iloc[-1])
    pct_30 = None
    if "date" in df2.columns and len(vals) > 1:
        ref = df2["date"].max() - pd.Timedelta(days=30)
        mask = df2["date"] <= ref
        if mask.any():
            ago = float(df2.loc[mask, "close"].dropna().iloc[-1])
            if ago != 0:
                pct_30 = (current - ago) / abs(ago) * 100
    return {"price": current, "pct_30": pct_30}


# ---------------------------------------------------------------------------
# Answer engine
# ---------------------------------------------------------------------------

_TICKER_BETAS = {
    "ZIM":  {"beta": 2.1, "focus": "Trans-Pacific container shipping", "type": "Container carrier"},
    "MATX": {"beta": 1.4, "focus": "Domestic US container trade (Jones Act)", "type": "Container carrier"},
    "SBLK": {"beta": 1.8, "focus": "Dry bulk (iron ore, grain, coal)", "type": "Dry bulk carrier"},
    "DAC":  {"beta": 1.6, "focus": "Container ship lessor / charter market", "type": "Container lessor"},
    "CMRE": {"beta": 1.5, "focus": "Containership leasing & offshore", "type": "Containership leasing"},
}

_QUICK_QUESTION_MAP = {
    "What's the market outlook?":    "outlook",
    "Best route opportunity now?":   "best route",
    "Which shipping stock to buy?":  "should i buy",
    "Where is port congestion worst?": "congestion",
    "Best time to book a container?": "best time to ship",
    "Current freight rates summary":  "freight rate summary",
    "Red Sea situation update":       "suez",
    "BDI interpretation":             "BDI",
}


def answer_question(question: str, context: dict) -> str:
    """Rule-based NLP: parse question by keyword and return data-grounded answer."""
    q = question.lower().strip()

    port_results: list[PortDemandResult] = context.get("port_results", [])
    route_results: list[RouteOpportunity] = context.get("route_results", [])
    insights: list[Insight] = context.get("insights", [])
    freight_data: dict = context.get("freight_data", {})
    macro_data: dict = context.get("macro_data", {})
    stock_data: dict = context.get("stock_data", {})

    # ── HELP ────────────────────────────────────────────────────────────────
    if "help" in q:
        return (
            "I can answer questions about the shipping data loaded in this app.\n\n"
            "Try asking me:\n"
            "  - What is the best route opportunity right now?\n"
            "  - Port demand for Shanghai\n"
            "  - Freight rate for Trans-Pacific\n"
            "  - What is the BDI?\n"
            "  - Should I buy ZIM?\n"
            "  - Where is congestion worst?\n"
            "  - Transit time for Asia-Europe\n"
            "  - Best time to ship / peak season\n"
            "  - Trans-Pacific market conditions\n"
            "  - Suez / Red Sea situation\n"
            "  - Carbon / emissions\n"
            "  - Market sentiment\n"
            "  - 90-day outlook\n"
            "  - ZIM / MATX / SBLK / DAC / CMRE stock info\n\n"
            "I use only data currently loaded in the app — no external calls."
        )

    # ── BEST ROUTE / BEST OPPORTUNITY ───────────────────────────────────────
    if any(p in q for p in ("best route", "best opportunity", "top route", "top opportunity")):
        top = _top_route(route_results)
        if top is None:
            return "No route data is currently loaded."
        pct = top.rate_pct_change_30d * 100
        sign = "+" if pct >= 0 else ""
        return (
            "Best route opportunity right now:\n\n"
            + top.route_name + " (" + top.route_id + ")\n"
            + "  Opportunity score:  " + str(round(top.opportunity_score * 100, 1)) + "% — " + top.opportunity_label + "\n"
            + "  Current rate:       $" + str(round(top.current_rate_usd_feu, 0)) + "/FEU\n"
            + "  30-day rate trend:  " + top.rate_trend + " (" + sign + str(round(pct, 1)) + "%)\n"
            + "  Transit time:       " + str(top.transit_days) + " days\n"
            + "  Origin congestion:  " + str(round(top.origin_congestion * 100, 0)) + "%\n"
            + "  Dest demand:        " + str(round(top.dest_demand_score * 100, 0)) + "%\n\n"
            + "Rationale: " + top.rationale
        )

    # ── PORT DEMAND ──────────────────────────────────────────────────────────
    if "port demand" in q or ("demand" in q and any(p.port_name.lower().split()[0] in q for p in port_results)):
        # Try to extract port name
        matched = None
        for p in port_results:
            words = p.port_name.lower().split()
            if any(w in q for w in words if len(w) > 3):
                matched = p
                break
            if p.locode.lower() in q:
                matched = p
                break
        if matched is None and port_results:
            matched = port_results[0]  # highest demand port

        if matched is None:
            return "No port demand data is currently loaded."

        drivers = []
        if matched.trade_flow_component >= 0.6:
            drivers.append("strong trade flows")
        if matched.congestion_component >= 0.6:
            drivers.append("elevated vessel congestion")
        if matched.throughput_component >= 0.6:
            drivers.append("high TEU throughput")
        drivers_str = ", ".join(drivers) if drivers else "mixed signals"

        return (
            matched.port_name + " (" + matched.locode + ") — Port Demand\n\n"
            + "  Demand score:   " + str(round(matched.demand_score * 100, 1)) + "% (" + matched.demand_label + ")\n"
            + "  Trend:          " + matched.demand_trend + "\n"
            + "  Congestion:     " + str(round(matched.congestion_index * 100, 1)) + "%\n"
            + "  Vessel count:   " + str(matched.vessel_count) + "\n"
            + "  Throughput:     " + str(round(matched.throughput_teu_m, 1)) + "M TEU/yr\n"
            + "  Import value:   $" + str(round(matched.import_value_usd / 1e9, 2)) + "B\n"
            + "  Export value:   $" + str(round(matched.export_value_usd / 1e9, 2)) + "B\n\n"
            + "Key demand drivers: " + drivers_str + "."
        )

    # ── CONGESTION ───────────────────────────────────────────────────────────
    if "congestion" in q:
        # Check if a specific port is mentioned
        matched = None
        for p in port_results:
            words = p.port_name.lower().split()
            if any(w in q for w in words if len(w) > 3) or p.locode.lower() in q:
                matched = p
                break
        if matched:
            level = "High" if matched.congestion_index > 0.65 else ("Moderate" if matched.congestion_index > 0.35 else "Low")
            return (
                "Congestion at " + matched.port_name + " (" + matched.locode + "):\n\n"
                + "  Index:       " + str(round(matched.congestion_index * 100, 1)) + "% — " + level + "\n"
                + "  Vessels:     " + str(matched.vessel_count) + " cargo vessels tracked\n\n"
                + ("High congestion can delay loading by 2-5 days and increase demurrage costs." if matched.congestion_index > 0.65
                   else "Congestion is manageable at this port currently.")
            )
        # Worst congestion globally
        if not port_results:
            return "No port data loaded."
        worst = max(port_results, key=lambda p: p.congestion_index)
        top3 = sorted(port_results, key=lambda p: p.congestion_index, reverse=True)[:3]
        lines = ["Ports with highest congestion right now:\n"]
        for i, p in enumerate(top3, 1):
            lines.append(
                str(i) + ". " + p.port_name + " (" + p.locode + "): "
                + str(round(p.congestion_index * 100, 1)) + "% — "
                + str(p.vessel_count) + " vessels"
            )
        lines.append(
            "\nWorst bottleneck: " + worst.port_name
            + " at " + str(round(worst.congestion_index * 100, 1)) + "% congestion index."
        )
        return "\n".join(lines)

    # ── FREIGHT RATE ─────────────────────────────────────────────────────────
    if "freight rate" in q or ("rate" in q and ("summary" in q or "overview" in q)):
        if not route_results:
            return "No freight rate data is currently loaded."
        # Check for specific route
        matched = None
        for r in route_results:
            words = r.route_name.lower().split()
            if any(w in q for w in words if len(w) > 4):
                matched = r
                break
            if r.route_id.lower().replace("_", " ") in q:
                matched = r
                break

        if matched:
            pct = matched.rate_pct_change_30d * 100
            sign = "+" if pct >= 0 else ""
            return (
                matched.route_name + " — Freight Rate\n\n"
                + "  Current rate: $" + str(round(matched.current_rate_usd_feu, 0)) + "/FEU\n"
                + "  30-day trend: " + matched.rate_trend + " (" + sign + str(round(pct, 1)) + "%)\n"
                + "  FBX index:    " + matched.fbx_index + "\n"
                + "  Transit:      " + str(matched.transit_days) + " days\n"
            )

        # Summary of all routes
        sorted_r = sorted(route_results, key=lambda r: r.current_rate_usd_feu, reverse=True)
        lines = ["Current freight rates across all tracked routes:\n"]
        for r in sorted_r[:8]:
            pct = r.rate_pct_change_30d * 100
            sign = "+" if pct >= 0 else ""
            arrow = "↑" if r.rate_trend == "Rising" else ("↓" if r.rate_trend == "Falling" else "→")
            lines.append(
                arrow + " " + r.route_name + ": $"
                + str(round(r.current_rate_usd_feu, 0))
                + "/FEU  (" + sign + str(round(pct, 1)) + "% 30d)"
            )
        return "\n".join(lines)

    # ── BDI ──────────────────────────────────────────────────────────────────
    if "bdi" in q or "baltic dry" in q:
        bdi = _bdi_value(macro_data)
        chg = _bdi_change_30d(macro_data)
        if bdi is None:
            return (
                "Baltic Dry Index (BDI) data is not currently loaded.\n\n"
                "The BDI tracks daily charter rates for dry bulk vessels (Capesize, Panamax, "
                "Supramax). It is a leading indicator of global trade demand."
            )
        chg_str = ""
        if chg is not None:
            sign = "+" if chg >= 0 else ""
            chg_str = " (" + sign + str(round(chg, 1)) + "% vs 30 days ago)"

        if bdi > 2500:
            interp = "Elevated — strong dry bulk demand, signaling robust global commodity trade."
        elif bdi > 1500:
            interp = "Moderate — balanced market, typical seasonal conditions."
        elif bdi > 800:
            interp = "Soft — below-average demand, vessel oversupply or slowing trade."
        else:
            interp = "Depressed — very weak dry bulk market, potential recession signal."

        return (
            "Baltic Dry Index (BDI)\n\n"
            + "  Current:       " + str(round(bdi, 0)) + chg_str + "\n"
            + "  Interpretation: " + interp + "\n\n"
            + "The BDI is a composite of Capesize, Panamax, and Supramax spot rates. "
            + "Rising BDI typically leads container rate moves by 4-8 weeks."
        )

    # ── STOCK / TICKER ANALYSIS ───────────────────────────────────────────────
    tickers_found = [t for t in _TICKER_BETAS if t.lower() in q]

    if ("buy" in q or "should i" in q or "stock" in q) and not tickers_found:
        # General buy recommendation — pick best alpha signal from insights
        stock_insights = [i for i in insights if i.stocks_potentially_affected]
        if stock_insights:
            best = max(stock_insights, key=lambda i: i.score)
            tickers_found = best.stocks_potentially_affected[:1]
        if not tickers_found:
            # Default to all tickers ranked by 30d performance
            perf = []
            for t in _TICKER_BETAS:
                info = _stock_info(stock_data, t)
                if info and info.get("pct_30") is not None:
                    perf.append((t, info["pct_30"]))
            if perf:
                perf.sort(key=lambda x: x[1], reverse=True)
                tickers_found = [perf[0][0]]
            else:
                tickers_found = ["ZIM"]

    if tickers_found:
        ticker = tickers_found[0].upper()
        beta_info = _TICKER_BETAS.get(ticker, {})
        info = _stock_info(stock_data, ticker)

        price_str = "$" + str(round(info["price"], 2)) if info.get("price") else "N/A"
        pct_str = ""
        if info.get("pct_30") is not None:
            sign = "+" if info["pct_30"] >= 0 else ""
            pct_str = sign + str(round(info["pct_30"], 1)) + "% (30d)"

        # Find relevant insight
        rel_insights = [i for i in insights if ticker in i.stocks_potentially_affected]
        rec_text = ""
        if rel_insights:
            top_i = max(rel_insights, key=lambda i: i.score)
            rec_text = "\n\nInsight: " + top_i.title + " — " + top_i.action + " (score " + str(round(top_i.score * 100, 0)) + "%)"

        # Simple sentiment from 30d performance
        if info.get("pct_30") is not None:
            if info["pct_30"] > 10:
                signal = "LONG — strong momentum"
            elif info["pct_30"] > 2:
                signal = "LONG — mild momentum"
            elif info["pct_30"] < -10:
                signal = "CAUTION — meaningful drawdown"
            else:
                signal = "NEUTRAL — range-bound"
        else:
            signal = "Insufficient price data"

        return (
            ticker + " — Shipping Stock Analysis\n\n"
            + "  Type:     " + beta_info.get("type", "Shipping equity") + "\n"
            + "  Focus:    " + beta_info.get("focus", "Shipping sector") + "\n"
            + "  Beta:     " + str(beta_info.get("beta", "N/A")) + "x vs S&P 500\n"
            + "  Price:    " + price_str + "  " + pct_str + "\n"
            + "  Signal:   " + signal
            + rec_text + "\n\n"
            + "Note: High shipping beta means amplified moves relative to broad market. "
            + "Always review full fundamentals before trading."
        )

    # ── TRANSIT TIME ─────────────────────────────────────────────────────────
    if "transit" in q or "transit time" in q:
        matched = None
        for r in route_results:
            words = r.route_name.lower().split()
            if any(w in q for w in words if len(w) > 4) or r.route_id.lower().replace("_", " ") in q:
                matched = r
                break
        if matched:
            return (
                matched.route_name + " — Transit Time\n\n"
                + "  Standard transit: " + str(matched.transit_days) + " days\n"
                + "  Origin: " + matched.origin_locode + " (" + matched.origin_region + ")\n"
                + "  Destination: " + matched.dest_locode + " (" + matched.dest_region + ")\n\n"
                + "Note: Actual transit varies by carrier schedule, weather, and port congestion. "
                + "Add 2-5 buffer days for planning."
            )
        if route_results:
            lines = ["Transit times across tracked routes:\n"]
            for r in sorted(route_results, key=lambda r: r.transit_days):
                lines.append("  " + r.route_name + ": " + str(r.transit_days) + " days")
            return "\n".join(lines)
        return "No route data is currently loaded."

    # ── BEST TIME TO SHIP / BOOKING WINDOW ───────────────────────────────────
    if "best time to ship" in q or "booking window" in q or "when to book" in q or "when to ship" in q:
        bdi = _bdi_value(macro_data)
        wti = _wti_value(macro_data)
        bdi_str = str(round(bdi, 0)) if bdi else "N/A"
        wti_str = "$" + str(round(wti, 1)) if wti else "N/A"
        return (
            "Optimal Booking Window — Seasonal Advice\n\n"
            "  Best windows:  January-March (post-CNY softness) and October-November (pre-holiday lull)\n"
            "  Avoid:         July-September peak and December pre-holiday surge\n"
            "  Book early:    6-8 weeks ahead for Trans-Pacific, 8-10 weeks for Asia-Europe\n\n"
            "Current market signals:\n"
            "  BDI: " + bdi_str + " — "
            + ("elevated, consider booking sooner" if bdi and bdi > 2000 else "moderate, standard lead time is fine")
            + "\n"
            "  WTI crude: " + wti_str
            + (" — higher fuel surcharges likely" if wti and wti > 80 else " — fuel surcharges relatively contained")
            + "\n\n"
            "Pro tip: Lock in spot rates when the BDI is falling and book 6 weeks out for best rate-to-service balance."
        )

    # ── PEAK SEASON ──────────────────────────────────────────────────────────
    if "peak season" in q or "peak" in q:
        current_year = datetime.datetime.now().year
        return (
            "Container Shipping Peak Season — " + str(current_year) + "\n\n"
            "  Primary peak:   July-September (" + str(current_year) + ")\n"
            "  Driven by:      Back-to-school (July) and holiday inventory build (Aug-Sep)\n"
            "  Secondary peak: Late January-February (pre-CNY inventory rush)\n\n"
            "What to expect during peak:\n"
            "  - Spot rates typically rise 20-40% above off-peak levels\n"
            "  - Equipment shortages at major origin ports (Shanghai, Ningbo, Shenzhen)\n"
            "  - Booking lead times extend to 8-12 weeks\n"
            "  - Trans-Pacific Eastbound is most affected\n\n"
            "Recommendation: Book July-September shipments by April-May to lock in rates."
        )

    # ── TRANS-PACIFIC ────────────────────────────────────────────────────────
    if "trans-pacific" in q or "transpacific" in q:
        tp_route = _find_route(route_results, "trans-pacific eastbound")
        if tp_route is None:
            tp_route = _find_route(route_results, "transpacific")
        bdi = _bdi_value(macro_data)

        if tp_route:
            pct = tp_route.rate_pct_change_30d * 100
            sign = "+" if pct >= 0 else ""
            rate_str = "$" + str(round(tp_route.current_rate_usd_feu, 0)) + "/FEU"
            trend_str = tp_route.rate_trend + " (" + sign + str(round(pct, 1)) + "% in 30d)"
        else:
            rate_str = "N/A"
            trend_str = "N/A"

        bdi_str = str(round(bdi, 0)) if bdi else "N/A"
        return (
            "Trans-Pacific Market Conditions\n\n"
            "  Eastbound rate:  " + rate_str + "\n"
            "  30-day trend:    " + trend_str + "\n"
            "  BDI (proxy):     " + bdi_str + "\n"
            + ("  Opportunity:     " + str(round(tp_route.opportunity_score * 100, 1)) + "% — " + tp_route.opportunity_label + "\n" if tp_route else "")
            + "\n"
            "Market context: The Trans-Pacific Eastbound lane (China-US West Coast) is the "
            "world's highest-volume container corridor. Rates are heavily influenced by US retail "
            "import demand, Chinese manufacturing output, and vessel capacity deployed by the major "
            "alliances. Watch for blank sailings announcements as a rate floor signal."
        )

    # ── SUEZ / RED SEA ───────────────────────────────────────────────────────
    if "suez" in q or "red sea" in q or "houthi" in q or "rerouting" in q:
        ae_route = _find_route(route_results, "asia-europe")
        if ae_route:
            pct = ae_route.rate_pct_change_30d * 100
            sign = "+" if pct >= 0 else ""
            rate_str = "$" + str(round(ae_route.current_rate_usd_feu, 0)) + "/FEU"
            pct_str = sign + str(round(pct, 1)) + "%"
        else:
            rate_str = "N/A"
            pct_str = "N/A"
        return (
            "Suez Canal / Red Sea Situation\n\n"
            "  Asia-Europe rate: " + rate_str + " (" + pct_str + " 30d trend)\n\n"
            "Background: Since late 2023, Houthi attacks in the Red Sea have forced most major "
            "carriers to reroute via the Cape of Good Hope, adding 10-14 days to Asia-Europe transit "
            "and increasing fuel costs by approximately $1M per voyage.\n\n"
            "Key impacts:\n"
            "  - Asia-Europe transit: ~25 days (Suez) vs ~35-38 days (Cape reroute)\n"
            "  - Fuel surcharges: elevated by $300-600/FEU\n"
            "  - Effective capacity reduction: ~15-20% on Asia-Europe lane\n"
            "  - Rates have repriced significantly above pre-crisis levels\n\n"
            "Monitor: UN/naval convoy updates and carrier blank sailing announcements for re-routing reversal signals."
        )

    # ── CARBON / EMISSIONS ───────────────────────────────────────────────────
    if "carbon" in q or "emission" in q or "co2" in q or "green" in q or "esg" in q:
        avg_co2 = _avg_co2_per_teu(route_results)
        return (
            "Carbon / Emissions — Shipping Sustainability\n\n"
            "  Est. avg CO2 per TEU (across tracked routes): " + str(round(avg_co2, 1)) + " tonnes\n\n"
            "Industry context:\n"
            "  - Ocean shipping emits ~2.5% of global GHG\n"
            "  - IMO 2030 target: 40% CO2 intensity reduction vs 2008\n"
            "  - IMO 2050 target: net-zero GHG from international shipping\n"
            "  - CII (Carbon Intensity Indicator) ratings now affect vessel employment\n\n"
            "Key levers:\n"
            "  - Slow steaming reduces fuel burn ~25% (adds 3-5 transit days)\n"
            "  - LNG, methanol, ammonia being trialled as alternative fuels\n"
            "  - FuelEU Maritime 2025 applies GHG intensity limits to EU port calls\n\n"
            "Shorter routes (intra-Asia) produce significantly less CO2/TEU than long-haul lanes."
        )

    # ── MARKET SENTIMENT ─────────────────────────────────────────────────────
    if "market sentiment" in q or "sentiment" in q or "market direction" in q:
        sentiment = _market_sentiment(insights)
        bdi = _bdi_value(macro_data)
        avg_rate = 0.0
        if route_results:
            avg_rate = sum(r.current_rate_usd_feu for r in route_results) / len(route_results)
        bdi_str = str(round(bdi, 0)) if bdi else "N/A"

        sent_emoji = {"bullish": "green / positive", "bearish": "red / negative", "mixed": "amber / mixed"}
        return (
            "Overall Market Sentiment\n\n"
            "  Direction:     " + sentiment.upper() + " — " + sent_emoji.get(sentiment, "mixed") + "\n"
            "  BDI:           " + bdi_str + "\n"
            "  Avg spot rate: $" + str(round(avg_rate, 0)) + "/FEU\n"
            "  Insights:      " + str(len(insights)) + " active signals\n\n"
            + ("Top insight: " + insights[0].title + " — " + insights[0].detail if insights else "No insights loaded.")
        )

    # ── OUTLOOK / 90-DAY ─────────────────────────────────────────────────────
    if "outlook" in q or "90-day" in q or "90 day" in q or "forecast" in q:
        top = _top_route(route_results)
        sentiment = _market_sentiment(insights)
        bdi = _bdi_value(macro_data)
        wti = _wti_value(macro_data)
        bdi_str = str(round(bdi, 0)) if bdi else "N/A"
        wti_str = "$" + str(round(wti, 1)) if wti else "N/A"

        top_route_str = ""
        if top:
            top_route_str = "  Top lane: " + top.route_name + " at $" + str(round(top.current_rate_usd_feu, 0)) + "/FEU\n"

        return (
            "90-Day Shipping Market Outlook\n\n"
            "Macro backdrop:\n"
            "  BDI:         " + bdi_str + "\n"
            "  WTI crude:   " + wti_str + "\n"
            "  Sentiment:   " + sentiment.upper() + "\n\n"
            "Route picture:\n"
            + top_route_str
            + "  Routes tracked: " + str(len(route_results)) + "\n\n"
            "Key themes over the next 90 days:\n"
            "  1. Red Sea / Suez — rerouting continues to constrain Asia-Europe capacity\n"
            "  2. Trans-Pacific — watch US import demand for Q3 holiday inventory build\n"
            "  3. BDI trajectory — a sustained move above 2,000 signals tightening supply\n"
            "  4. Bunker costs — WTI above $80 compresses carrier margins and lifts surcharges\n"
            "  5. Tariff risk — US-China trade friction can sharply reprice spot rates\n\n"
            "Recommendation: " + (
                "Prioritize locking in long-term contracts on high-scoring routes now."
                if sentiment == "bullish"
                else (
                    "Favor spot flexibility; avoid long-term commitments at current rates."
                    if sentiment == "bearish"
                    else "Maintain balanced contract-to-spot ratio; monitor BDI weekly."
                )
            )
        )

    # ── FALLBACK ─────────────────────────────────────────────────────────────
    logger.debug("Assistant: no pattern matched for question: " + question)
    return (
        "I'm not sure I understood that question. Here are topics I can help with:\n\n"
        "  best route, port demand, freight rates, BDI, congestion,\n"
        "  transit time, best time to ship, peak season, trans-pacific,\n"
        "  suez / Red Sea, carbon / emissions, market sentiment, outlook,\n"
        "  ZIM / MATX / SBLK / DAC / CMRE stock info\n\n"
        "Type 'help' for a full list, or click one of the quick buttons below."
    )


# ---------------------------------------------------------------------------
# Context panel (sidebar-within-tab)
# ---------------------------------------------------------------------------

def _render_context_panel(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    macro_data: dict,
) -> None:
    """Render a compact data-context card."""
    top = _top_route(route_results)
    sentiment = _market_sentiment(insights)
    bdi = _bdi_value(macro_data)

    sent_color = {"bullish": C_HIGH, "bearish": C_LOW, "mixed": C_MOD}.get(sentiment, C_MOD)

    top_score_str = (str(round(top.opportunity_score * 100, 1)) + "% — " + top.route_name) if top else "N/A"
    bdi_str = str(round(bdi, 0)) if bdi else "N/A"

    panel_rows = [
        ("Ports loaded", str(len(port_results))),
        ("Routes scored", str(len(route_results))),
        ("Active insights", str(len(insights))),
        ("BDI", bdi_str),
        ("Top opportunity", top_score_str),
    ]

    rows_html = ""
    for label, val in panel_rows:
        rows_html += (
            '<div class="ctx-row">'
            '<span>' + label + '</span>'
            '<span class="ctx-val">' + val + '</span>'
            '</div>'
        )

    badge_html = (
        '<span style="background:' + _rgba(sent_color, 0.15) + '; color:' + sent_color + '; '
        'border:1px solid ' + _rgba(sent_color, 0.3) + '; '
        'padding:2px 10px; border-radius:999px; font-size:0.72rem; font-weight:700; '
        'text-transform:uppercase; letter-spacing:0.04em;">'
        + sentiment.upper()
        + '</span>'
    )

    st.markdown(
        '<div class="ctx-panel">'
        '<div style="font-size:0.72rem; font-weight:700; color:#475569; '
        'text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px">Data Context</div>'
        + rows_html
        + '<div style="margin-top:10px; display:flex; align-items:center; gap:8px; '
        'color:#94a3b8; font-size:0.78rem;">Market: ' + badge_html + '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Quick question buttons
# ---------------------------------------------------------------------------

_QUICK_QUESTIONS = [
    "What's the market outlook?",
    "Best route opportunity now?",
    "Which shipping stock to buy?",
    "Where is port congestion worst?",
    "Best time to book a container?",
    "Current freight rates summary",
    "Red Sea situation update",
    "BDI interpretation",
]

_QUICK_QUESTION_EMOJIS = [
    "📊", "🚢", "⚡", "📦", "💰", "🌊", "🌍", "📈",
]


def _render_quick_buttons(context: dict) -> None:
    """Render 8 suggested questions in 2 columns. Clicking triggers the answer."""
    col1, col2 = st.columns(2)
    cols = [col1, col2]
    for idx, (label, emoji) in enumerate(zip(_QUICK_QUESTIONS, _QUICK_QUESTION_EMOJIS)):
        with cols[idx % 2]:
            full_label = emoji + " " + label
            if st.button(full_label, key="qq_" + str(idx)):
                # Map to engine keyword
                kw = _QUICK_QUESTION_MAP.get(label, label.lower())
                _append_message("user", label)
                answer = answer_question(kw, context)
                _append_message("assistant", answer)
                st.rerun()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
    route_results_all: list[RouteOpportunity] | None = None,
) -> None:
    """Render the AI Assistant tab."""
    _init_history()

    # Prefer the full route list if provided
    routes_for_engine = route_results_all if route_results_all else route_results

    context = {
        "port_results":  port_results,
        "route_results": routes_for_engine,
        "insights":      insights,
        "freight_data":  freight_data,
        "macro_data":    macro_data,
        "stock_data":    stock_data,
    }

    # Inject CSS
    st.markdown(_CHAT_CSS, unsafe_allow_html=True)

    # Layout: main chat area + right context panel
    main_col, ctx_col = st.columns([3, 1])

    with ctx_col:
        _render_context_panel(port_results, routes_for_engine, insights, macro_data)

    with main_col:
        section_header(
            "Shipping Intelligence Assistant",
            "Ask questions about the data loaded in this session — rule-based, no external API calls.",
        )

        # ── Chat history display ─────────────────────────────────────────
        chat_container = st.container()
        with chat_container:
            if not st.session_state.chat_history:
                st.markdown(
                    '<div style="color:#475569; font-size:0.85rem; '
                    'padding:24px 0; text-align:center;">'
                    '🚢 Ask a question below or click a quick button to get started.'
                    '</div>',
                    unsafe_allow_html=True,
                )
            else:
                for msg in st.session_state.chat_history:
                    _render_bubble(msg["role"], msg["text"], msg["ts"])

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # ── Input ────────────────────────────────────────────────────────
        # st.chat_input must be called at tab scope, not inside a column
        user_input = st.chat_input(
            "Ask about routes, rates, ports, stocks, BDI, Suez...",
            key="assistant_chat_input",
        )

        if user_input and user_input.strip():
            question = user_input.strip()
            _append_message("user", question)

            # Brief "thinking" placeholder
            think_ph = st.empty()
            with think_ph:
                _render_thinking()

            answer = answer_question(question, context)
            think_ph.empty()
            _append_message("assistant", answer)
            logger.info("Assistant answered question: " + question[:80])
            st.rerun()

        # ── Quick questions ──────────────────────────────────────────────
        st.markdown(
            '<div style="margin-top:16px; margin-bottom:6px; font-size:0.72rem; '
            'font-weight:700; color:#475569; text-transform:uppercase; '
            'letter-spacing:0.08em;">Quick Questions</div>',
            unsafe_allow_html=True,
        )
        _render_quick_buttons(context)

        # ── Clear history button ─────────────────────────────────────────
        if st.session_state.chat_history:
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            if st.button("Clear conversation", key="clear_chat"):
                st.session_state.chat_history = []
                st.rerun()
