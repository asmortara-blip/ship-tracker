"""Intelligent Q&A Assistant tab for Ship Tracker.

render(port_results, route_results, insights, freight_data, macro_data,
       stock_data, route_results_all=None) is the public entry point.

Rule-based NLP answer engine — no external API calls.
"""
from __future__ import annotations

import datetime
import json
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
/* ── Hero gradient animation ────────────────────────────────────────────── */
@keyframes hero-gradient-shift {
    0%   { background-position: 0% 50%; }
    50%  { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
@keyframes pulse-ring {
    0%   { box-shadow: 0 0 0 0 rgba(59,130,246,0.4); }
    70%  { box-shadow: 0 0 0 10px rgba(59,130,246,0); }
    100% { box-shadow: 0 0 0 0 rgba(59,130,246,0); }
}
@keyframes slide-in-up {
    from { opacity: 0; transform: translateY(10px); }
    to   { opacity: 1; transform: translateY(0); }
}
@keyframes fade-in {
    from { opacity: 0; }
    to   { opacity: 1; }
}
@keyframes dot-bounce {
    0%, 80%, 100% { transform: scale(0.7); opacity: 0.4; }
    40%           { transform: scale(1.0); opacity: 1.0; }
}
@keyframes shimmer {
    0%   { background-position: -200% 0; }
    100% { background-position: 200% 0; }
}

/* ── Intelligence Engine Hero ───────────────────────────────────────────── */
.ie-hero {
    background: linear-gradient(135deg,
        #0a0f1a 0%, #0f1f3d 25%, #1a1035 50%, #0d1f3c 75%, #0a0f1a 100%);
    background-size: 400% 400%;
    animation: hero-gradient-shift 12s ease infinite;
    border: 1px solid rgba(59,130,246,0.2);
    border-radius: 16px;
    padding: 28px 32px 24px;
    margin-bottom: 20px;
    position: relative;
    overflow: hidden;
}
.ie-hero::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: linear-gradient(90deg,
        transparent 0%, rgba(59,130,246,0.04) 50%, transparent 100%);
    background-size: 200% 100%;
    animation: shimmer 4s linear infinite;
    pointer-events: none;
}
.ie-hero-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
}
.ie-brand {
    display: flex;
    align-items: center;
    gap: 14px;
}
.ie-icon {
    width: 44px; height: 44px;
    background: linear-gradient(135deg, #1d4ed8, #7c3aed);
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.4rem;
    animation: pulse-ring 2.5s ease infinite;
    flex-shrink: 0;
}
.ie-title {
    font-size: 1.25rem;
    font-weight: 800;
    color: #f1f5f9;
    letter-spacing: -0.02em;
    line-height: 1.2;
}
.ie-subtitle {
    font-size: 0.72rem;
    color: #64748b;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-top: 2px;
}
.ie-badges {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}
.ie-badge {
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    border: 1px solid;
}
.ie-badge-live {
    background: rgba(16,185,129,0.1);
    color: #10b981;
    border-color: rgba(16,185,129,0.3);
}
.ie-badge-local {
    background: rgba(59,130,246,0.1);
    color: #3b82f6;
    border-color: rgba(59,130,246,0.3);
}
.ie-stats-row {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
}
.ie-stat {
    display: flex;
    flex-direction: column;
    gap: 1px;
}
.ie-stat-val {
    font-size: 1.1rem;
    font-weight: 700;
    color: #f1f5f9;
    line-height: 1;
}
.ie-stat-lbl {
    font-size: 0.64rem;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.07em;
}
.ie-divider {
    width: 1px;
    background: rgba(255,255,255,0.08);
    align-self: stretch;
    margin: 0 4px;
}

/* ── Analysis type tabs ─────────────────────────────────────────────────── */
.analysis-tabs {
    display: flex;
    gap: 6px;
    margin-bottom: 16px;
    overflow-x: auto;
    padding-bottom: 2px;
}
.at-tab {
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 0.76rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid rgba(255,255,255,0.08);
    color: #64748b;
    background: #111827;
    white-space: nowrap;
    transition: all 0.15s ease;
}
.at-tab.active {
    background: rgba(59,130,246,0.15);
    color: #3b82f6;
    border-color: rgba(59,130,246,0.35);
}

/* ── Chat bubble wrappers ───────────────────────────────────────────────── */
.chat-row {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    margin-bottom: 14px;
    animation: slide-in-up 0.25s ease-out both;
}
.chat-row.user      { flex-direction: row-reverse; }
.chat-row.assistant { flex-direction: row; }

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
    background: linear-gradient(135deg, #1d4ed8, #2563eb);
    color: #ffffff;
    border-bottom-right-radius: 4px;
    box-shadow: 0 4px 12px rgba(29,78,216,0.35);
}
.chat-bubble.assistant {
    background: #1a2235;
    color: #f1f5f9;
    border: 1px solid rgba(255,255,255,0.08);
    border-bottom-left-radius: 4px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}

/* ── Timestamp ──────────────────────────────────────────────────────────── */
.chat-ts {
    font-size: 0.68rem;
    color: #475569;
    text-align: right;
    margin-top: 3px;
    padding: 0 4px;
}
.chat-ts.left { text-align: left; }

/* ── Confidence bar ─────────────────────────────────────────────────────── */
.confidence-bar-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    padding: 0 4px;
}
.confidence-bar-bg {
    flex: 1;
    height: 3px;
    background: rgba(255,255,255,0.06);
    border-radius: 999px;
    overflow: hidden;
}
.confidence-bar-fill {
    height: 100%;
    border-radius: 999px;
    transition: width 0.5s ease;
}
.confidence-label {
    font-size: 0.64rem;
    color: #475569;
    white-space: nowrap;
}

/* ── Thinking dots ──────────────────────────────────────────────────────── */
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

/* ── Context panel data-source cards ────────────────────────────────────── */
.ctx-panel {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 14px 16px;
    font-size: 0.80rem;
    margin-bottom: 12px;
}
.ctx-section-lbl {
    font-size: 0.64rem;
    font-weight: 700;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 8px;
}
.ctx-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    color: #94a3b8;
}
.ctx-row:last-child { border-bottom: none; }
.ctx-val { color: #f1f5f9; font-weight: 600; }
.ctx-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    display: inline-block;
    margin-right: 5px;
}
.ctx-dot-green { background: #10b981; }
.ctx-dot-amber { background: #f59e0b; }
.ctx-dot-grey  { background: #475569; }

/* ── Data source freshness cards ────────────────────────────────────────── */
.ds-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 6px;
}
.ds-card-name  { font-size: 0.74rem; font-weight: 600; color: #cbd5e1; margin-bottom: 2px; }
.ds-card-fresh { font-size: 0.64rem; color: #475569; }

/* ── Recent insights panel ──────────────────────────────────────────────── */
.ri-item {
    padding: 10px 12px;
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    margin-bottom: 6px;
    animation: fade-in 0.3s ease both;
}
.ri-title { font-size: 0.80rem; font-weight: 600; color: #e2e8f0; margin-bottom: 4px; }
.ri-meta  { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.ri-tag {
    font-size: 0.60rem;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 999px;
    border: 1px solid;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.ri-ts { font-size: 0.64rem; color: #475569; }

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

/* ── Export / action buttons ────────────────────────────────────────────── */
.export-btn-row {
    display: flex;
    gap: 8px;
    margin-top: 10px;
    flex-wrap: wrap;
}
.export-btn {
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 0.74rem;
    font-weight: 600;
    border: 1px solid rgba(59,130,246,0.35);
    background: rgba(59,130,246,0.08);
    color: #60a5fa;
    cursor: pointer;
    transition: all 0.15s ease;
}
.export-btn:hover {
    background: rgba(59,130,246,0.18);
    border-color: rgba(59,130,246,0.6);
}

/* ── Analysis history cards ─────────────────────────────────────────────── */
.ah-card {
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    background: #111827;
    padding: 12px 14px;
    margin-bottom: 8px;
}
.ah-q { font-size: 0.80rem; font-weight: 600; color: #93c5fd; margin-bottom: 4px; }
.ah-ts { font-size: 0.64rem; color: #475569; margin-bottom: 6px; }
.ah-preview { font-size: 0.76rem; color: #94a3b8; line-height: 1.5; }
</style>
"""


# ---------------------------------------------------------------------------
# Session-state helpers
# ---------------------------------------------------------------------------

def _init_state() -> None:
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "analysis_type" not in st.session_state:
        st.session_state.analysis_type = "Market Analysis"
    if "saved_conversation" not in st.session_state:
        st.session_state.saved_conversation = None


def _append_message(role: str, text: str, confidence: float = 0.85) -> None:
    ts = datetime.datetime.now().strftime("%H:%M")
    st.session_state.chat_history.append({
        "role": role, "text": text, "ts": ts, "confidence": confidence,
    })


# ---------------------------------------------------------------------------
# Chat bubble renderers
# ---------------------------------------------------------------------------

def _confidence_color(score: float) -> str:
    if score >= 0.80:
        return "#10b981"
    if score >= 0.60:
        return "#f59e0b"
    return "#ef4444"


def _confidence_label(score: float) -> str:
    if score >= 0.80:
        return "High confidence"
    if score >= 0.60:
        return "Moderate confidence"
    return "Low confidence"


def _render_bubble(role: str, text: str, ts: str, confidence: float = 0.85) -> None:
    avatar = "🤖" if role == "assistant" else "👤"
    bubble_cls = "assistant" if role == "assistant" else "user"
    row_cls    = "assistant" if role == "assistant" else "user"
    ts_cls     = "left" if role == "assistant" else ""

    if role == "assistant":
        conf_color = _confidence_color(confidence)
        conf_pct   = int(confidence * 100)
        conf_lbl   = _confidence_label(confidence)

        st.markdown(
            '<div class="chat-row ' + row_cls + '">'
            '<div class="chat-avatar">' + avatar + '</div>'
            '<div style="max-width:72%">'
            '<div class="chat-bubble ' + bubble_cls + '">',
            unsafe_allow_html=True,
        )
        st.markdown(text)
        st.markdown(
            '</div>'
            '<div class="confidence-bar-wrap">'
            '<div class="confidence-bar-bg">'
            '<div class="confidence-bar-fill" style="width:' + str(conf_pct) + '%; '
            'background:' + conf_color + ';"></div>'
            '</div>'
            '<span class="confidence-label">' + conf_lbl + ' · ' + str(conf_pct) + '%</span>'
            '</div>'
            '<div class="chat-ts ' + ts_cls + '">' + ts + '</div>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
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
        '<div class="chat-avatar">🤖</div>'
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
    try:
        df = macro_data.get("BDIY")
        if df is None or df.empty:
            return None
        vals = df["value"].dropna()
        return float(vals.iloc[-1]) if not vals.empty else None
    except Exception:
        return None


def _bdi_change_30d(macro_data: dict) -> Optional[float]:
    try:
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
    except Exception:
        return None


def _wti_value(macro_data: dict) -> Optional[float]:
    try:
        df = macro_data.get("DCOILWTICO")
        if df is None or df.empty:
            return None
        vals = df["value"].dropna()
        return float(vals.iloc[-1]) if not vals.empty else None
    except Exception:
        return None


def _top_route(route_results: list[RouteOpportunity]) -> Optional[RouteOpportunity]:
    try:
        if not route_results:
            return None
        return max(route_results, key=lambda r: r.opportunity_score)
    except Exception:
        return None


def _find_port(port_results: list[PortDemandResult], name_fragment: str) -> Optional[PortDemandResult]:
    try:
        frag = name_fragment.lower().strip()
        for p in port_results:
            if frag in p.port_name.lower() or frag in p.locode.lower() or frag in p.region.lower():
                return p
        return None
    except Exception:
        return None


def _find_route(route_results: list[RouteOpportunity], fragment: str) -> Optional[RouteOpportunity]:
    try:
        frag = fragment.lower().strip()
        for r in route_results:
            if frag in r.route_name.lower() or frag in r.route_id.lower():
                return r
        return None
    except Exception:
        return None


def _avg_co2_per_teu(route_results: list[RouteOpportunity]) -> float:
    """Estimate average CO2/TEU across routes using transit_days as proxy.

    Rough industry average: ~0.5 kg CO2e per km per TEU.
    Use transit_days * 450 km/day as distance proxy.
    """
    try:
        if not route_results:
            return 0.0
        vals = []
        for r in route_results:
            km_est = r.transit_days * 450
            co2 = km_est * 0.5 / 1000  # tonnes CO2
            vals.append(co2)
        return sum(vals) / len(vals)
    except Exception:
        return 0.0


def _market_sentiment(insights: list[Insight]) -> str:
    try:
        if not insights:
            return "neutral"
        avg_score = sum(i.score for i in insights) / len(insights)
        if avg_score >= 0.65:
            return "bullish"
        if avg_score <= 0.35:
            return "bearish"
        return "mixed"
    except Exception:
        return "neutral"


def _stock_info(stock_data: dict, ticker: str) -> dict:
    """Extract price, 30d change from stock_data."""
    try:
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
    except Exception:
        return {}


def _data_freshness_label(macro_data: dict, key: str) -> str:
    """Return a freshness string for a macro series."""
    try:
        df = macro_data.get(key)
        if df is None or df.empty:
            return "No data"
        if "date" in df.columns:
            import pandas as pd
            latest = df["date"].max()
            if hasattr(latest, "date"):
                return latest.strftime("%b %d, %Y")
        return "Loaded"
    except Exception:
        return "Unknown"


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
    "What's the market outlook?":      "outlook",
    "Best route opportunity now?":     "best route",
    "Which shipping stock to buy?":    "should i buy",
    "Where is port congestion worst?": "congestion",
    "Best time to book a container?":  "best time to ship",
    "Current freight rates summary":   "freight rate summary",
    "Red Sea situation update":        "suez",
    "BDI interpretation":              "BDI",
}

# ── Analysis type → injected context hint ───────────────────────────────────
_ANALYSIS_TYPE_HINTS = {
    "Market Analysis":     "market sentiment",
    "Risk Assessment":     "risk assessment",
    "Trade Opportunities": "best route",
    "Regulatory":          "carbon emissions regulatory",
}


def _answer_confidence(question: str, matched_topic: str) -> float:
    """Estimate confidence based on how specifically the question matched."""
    q = question.lower()
    specific_signals = [
        "bdi", "baltic dry", "trans-pacific", "suez", "red sea",
        "congestion", "transit", "freight rate", "carbon", "co2",
        "outlook", "forecast", "sentiment",
    ]
    for sig in specific_signals:
        if sig in q:
            return 0.90
    tickers = ["zim", "matx", "sblk", "dac", "cmre"]
    for t in tickers:
        if t in q:
            return 0.85
    if matched_topic in ("fallback", "help"):
        return 0.60
    return 0.80


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

    # ── RISK ASSESSMENT ──────────────────────────────────────────────────────
    if "risk" in q or "risk assessment" in q:
        bdi = _bdi_value(macro_data)
        wti = _wti_value(macro_data)
        sentiment = _market_sentiment(insights)
        worst_port = max(port_results, key=lambda p: p.congestion_index) if port_results else None

        risks = []
        if bdi and bdi > 2500:
            risks.append("**BDI elevated** — dry bulk tightness may spill into container markets")
        if wti and wti > 82:
            risks.append("**Fuel cost risk** — WTI above $82 compresses margins and lifts BAF surcharges")
        if worst_port and worst_port.congestion_index > 0.65:
            risks.append(f"**Port congestion** — {worst_port.port_name} at {round(worst_port.congestion_index*100,1)}% congestion (high delay risk)")
        risks.append("**Red Sea / Suez** — ongoing Houthi disruption adds 10-14 transit days on Asia-Europe")
        risks.append("**Tariff policy** — US-China trade tension is a persistent rate volatility trigger")
        if sentiment == "bearish":
            risks.append("**Market sentiment bearish** — insight scores signal softening demand")

        risk_level = "HIGH" if len(risks) >= 4 else ("MODERATE" if len(risks) >= 2 else "LOW")
        color_map = {"HIGH": "#ef4444", "MODERATE": "#f59e0b", "LOW": "#10b981"}
        color = color_map[risk_level]

        lines = [f"**Risk Level: {risk_level}**\n"]
        lines += ["**Active risk factors:**\n"]
        for r in risks:
            lines.append(f"  • {r}")
        lines.append(
            f"\n**Overall:** {len(risks)} material risk factors identified. "
            + ("Recommend defensive positioning — favor short-haul routes and spot flexibility."
               if risk_level == "HIGH"
               else "Standard risk management applies — monitor BDI and port congestion weekly.")
        )
        return "\n".join(lines)

    # ── ANALYZE TOP OPPORTUNITIES ────────────────────────────────────────────
    if "analyze" in q and "opportunit" in q:
        if not route_results:
            return "No route data loaded for opportunity analysis."
        top3 = sorted(route_results, key=lambda r: r.opportunity_score, reverse=True)[:3]
        lines = ["**Top 3 Route Opportunities — Ranked by Opportunity Score**\n"]
        for i, r in enumerate(top3, 1):
            pct = r.rate_pct_change_30d * 100
            sign = "+" if pct >= 0 else ""
            lines.append(
                f"**{i}. {r.route_name}** ({r.route_id})\n"
                f"  Score: **{round(r.opportunity_score*100,1)}%** — {r.opportunity_label}\n"
                f"  Rate: **${round(r.current_rate_usd_feu,0):,}/FEU** ({sign}{round(pct,1)}% 30d)\n"
                f"  Transit: {r.transit_days} days | Origin congestion: {round(r.origin_congestion*100,0)}%\n"
                f"  Rationale: {r.rationale}\n"
            )
        sentiment = _market_sentiment(insights)
        lines.append(f"\n**Market backdrop:** Sentiment is **{sentiment.upper()}** across {len(insights)} active signals.")
        return "\n".join(lines)

    # ── RATE FORECAST SUMMARY ────────────────────────────────────────────────
    if "rate forecast" in q or "forecast summary" in q:
        bdi = _bdi_value(macro_data)
        wti = _wti_value(macro_data)
        top = _top_route(route_results)
        sentiment = _market_sentiment(insights)

        rising = [r for r in route_results if r.rate_trend == "Rising"]
        falling = [r for r in route_results if r.rate_trend == "Falling"]
        stable = [r for r in route_results if r.rate_trend not in ("Rising", "Falling")]

        lines = ["**Rate Forecast Summary**\n"]
        lines.append(f"  Routes rising:  **{len(rising)}** of {len(route_results)}")
        lines.append(f"  Routes falling: **{len(falling)}** of {len(route_results)}")
        lines.append(f"  Routes stable:  **{len(stable)}** of {len(route_results)}\n")

        if bdi:
            bdi_trend = "rising — bullish signal" if (bdi > 1800) else "soft — caution warranted"
            lines.append(f"  BDI: **{round(bdi,0)}** — {bdi_trend}")
        if wti:
            lines.append(f"  WTI crude: **${round(wti,1)}** — bunker surcharge pressure {'elevated' if wti > 80 else 'contained'}")
        if top:
            lines.append(f"\n  Top rate opportunity: **{top.route_name}** at **${round(top.current_rate_usd_feu,0):,}/FEU**")

        direction = "upward" if len(rising) > len(falling) else ("downward" if len(falling) > len(rising) else "flat")
        lines.append(f"\n**90-day rate bias:** {direction.upper()} — sentiment {sentiment.upper()}.")
        lines.append(
            "Recommend: Lock contracts on rising lanes; use spot flexibility on falling corridors."
            if direction == "upward"
            else "Recommend: Defer long-term commitments; negotiate spot-rate optionality."
        )
        return "\n".join(lines)

    # ── BEST ROUTE / BEST OPPORTUNITY ───────────────────────────────────────
    if any(p in q for p in ("best route", "best opportunity", "top route", "top opportunity")):
        top = _top_route(route_results)
        if top is None:
            return "No route data is currently loaded."
        pct = top.rate_pct_change_30d * 100
        sign = "+" if pct >= 0 else ""
        return (
            "**Best Route Opportunity Right Now**\n\n"
            + "**" + top.route_name + "** (" + top.route_id + ")\n"
            + "  Opportunity score:  **" + str(round(top.opportunity_score * 100, 1)) + "%** — " + top.opportunity_label + "\n"
            + "  Current rate:       **$" + str(round(top.current_rate_usd_feu, 0)) + "/FEU**\n"
            + "  30-day rate trend:  " + top.rate_trend + " (" + sign + str(round(pct, 1)) + "%)\n"
            + "  Transit time:       " + str(top.transit_days) + " days\n"
            + "  Origin congestion:  " + str(round(top.origin_congestion * 100, 0)) + "%\n"
            + "  Dest demand:        " + str(round(top.dest_demand_score * 100, 0)) + "%\n\n"
            + "**Rationale:** " + top.rationale
        )

    # ── PORT DEMAND ──────────────────────────────────────────────────────────
    if "port demand" in q or ("demand" in q and any(p.port_name.lower().split()[0] in q for p in port_results)):
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
            matched = port_results[0]

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
            "**" + matched.port_name + " (" + matched.locode + ") — Port Demand**\n\n"
            + "  Demand score:   **" + str(round(matched.demand_score * 100, 1)) + "%** (" + matched.demand_label + ")\n"
            + "  Trend:          " + matched.demand_trend + "\n"
            + "  Congestion:     " + str(round(matched.congestion_index * 100, 1)) + "%\n"
            + "  Vessel count:   " + str(matched.vessel_count) + "\n"
            + "  Throughput:     " + str(round(matched.throughput_teu_m, 1)) + "M TEU/yr\n"
            + "  Import value:   $" + str(round(matched.import_value_usd / 1e9, 2)) + "B\n"
            + "  Export value:   $" + str(round(matched.export_value_usd / 1e9, 2)) + "B\n\n"
            + "**Key demand drivers:** " + drivers_str + "."
        )

    # ── CONGESTION ───────────────────────────────────────────────────────────
    if "congestion" in q:
        matched = None
        for p in port_results:
            words = p.port_name.lower().split()
            if any(w in q for w in words if len(w) > 3) or p.locode.lower() in q:
                matched = p
                break
        if matched:
            level = "High" if matched.congestion_index > 0.65 else ("Moderate" if matched.congestion_index > 0.35 else "Low")
            return (
                "**Congestion at " + matched.port_name + " (" + matched.locode + ")**\n\n"
                + "  Index:       **" + str(round(matched.congestion_index * 100, 1)) + "%** — " + level + "\n"
                + "  Vessels:     " + str(matched.vessel_count) + " cargo vessels tracked\n\n"
                + ("High congestion can delay loading by 2-5 days and increase demurrage costs." if matched.congestion_index > 0.65
                   else "Congestion is manageable at this port currently.")
            )
        if not port_results:
            return "No port data loaded."
        worst = max(port_results, key=lambda p: p.congestion_index)
        top3 = sorted(port_results, key=lambda p: p.congestion_index, reverse=True)[:3]
        lines = ["**Ports with Highest Congestion Right Now**\n"]
        for i, p in enumerate(top3, 1):
            lines.append(
                str(i) + ". **" + p.port_name + "** (" + p.locode + "): "
                + str(round(p.congestion_index * 100, 1)) + "% — "
                + str(p.vessel_count) + " vessels"
            )
        lines.append(
            "\n**Worst bottleneck:** " + worst.port_name
            + " at " + str(round(worst.congestion_index * 100, 1)) + "% congestion index."
        )
        return "\n".join(lines)

    # ── FREIGHT RATE ─────────────────────────────────────────────────────────
    if "freight rate" in q or ("rate" in q and ("summary" in q or "overview" in q)):
        if not route_results:
            return "No freight rate data is currently loaded."
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
                "**" + matched.route_name + " — Freight Rate**\n\n"
                + "  Current rate: **$" + str(round(matched.current_rate_usd_feu, 0)) + "/FEU**\n"
                + "  30-day trend: " + matched.rate_trend + " (" + sign + str(round(pct, 1)) + "%)\n"
                + "  FBX index:    " + matched.fbx_index + "\n"
                + "  Transit:      " + str(matched.transit_days) + " days\n"
            )

        sorted_r = sorted(route_results, key=lambda r: r.current_rate_usd_feu, reverse=True)
        lines = ["**Current Freight Rates — All Tracked Routes**\n"]
        for r in sorted_r[:8]:
            pct = r.rate_pct_change_30d * 100
            sign = "+" if pct >= 0 else ""
            arrow = "↑" if r.rate_trend == "Rising" else ("↓" if r.rate_trend == "Falling" else "→")
            lines.append(
                arrow + " **" + r.route_name + "**: $"
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
                "**Baltic Dry Index (BDI)** data is not currently loaded.\n\n"
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
            "**Baltic Dry Index (BDI)**\n\n"
            + "  Current:        **" + str(round(bdi, 0)) + "**" + chg_str + "\n"
            + "  Interpretation: " + interp + "\n\n"
            + "The BDI is a composite of Capesize, Panamax, and Supramax spot rates. "
            + "Rising BDI typically leads container rate moves by 4-8 weeks."
        )

    # ── STOCK / TICKER ANALYSIS ───────────────────────────────────────────────
    tickers_found = [t for t in _TICKER_BETAS if t.lower() in q]

    if ("buy" in q or "should i" in q or "stock" in q) and not tickers_found:
        stock_insights = [i for i in insights if i.stocks_potentially_affected]
        if stock_insights:
            best = max(stock_insights, key=lambda i: i.score)
            tickers_found = best.stocks_potentially_affected[:1]
        if not tickers_found:
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

        rel_insights = [i for i in insights if ticker in i.stocks_potentially_affected]
        rec_text = ""
        if rel_insights:
            top_i = max(rel_insights, key=lambda i: i.score)
            rec_text = "\n\n**Insight:** " + top_i.title + " — " + top_i.action + " (score " + str(round(top_i.score * 100, 0)) + "%)"

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
            "**" + ticker + " — Shipping Stock Analysis**\n\n"
            + "  Type:     " + beta_info.get("type", "Shipping equity") + "\n"
            + "  Focus:    " + beta_info.get("focus", "Shipping sector") + "\n"
            + "  Beta:     **" + str(beta_info.get("beta", "N/A")) + "x** vs S&P 500\n"
            + "  Price:    **" + price_str + "**  " + pct_str + "\n"
            + "  Signal:   **" + signal + "**"
            + rec_text + "\n\n"
            + "_Note: High shipping beta means amplified moves relative to broad market. "
            + "Always review full fundamentals before trading._"
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
                "**" + matched.route_name + " — Transit Time**\n\n"
                + "  Standard transit: **" + str(matched.transit_days) + " days**\n"
                + "  Origin: " + matched.origin_locode + " (" + matched.origin_region + ")\n"
                + "  Destination: " + matched.dest_locode + " (" + matched.dest_region + ")\n\n"
                + "_Note: Actual transit varies by carrier schedule, weather, and port congestion. "
                + "Add 2-5 buffer days for planning._"
            )
        if route_results:
            lines = ["**Transit Times Across Tracked Routes**\n"]
            for r in sorted(route_results, key=lambda r: r.transit_days):
                lines.append("  " + r.route_name + ": **" + str(r.transit_days) + " days**")
            return "\n".join(lines)
        return "No route data is currently loaded."

    # ── BEST TIME TO SHIP / BOOKING WINDOW ───────────────────────────────────
    if "best time to ship" in q or "booking window" in q or "when to book" in q or "when to ship" in q:
        bdi = _bdi_value(macro_data)
        wti = _wti_value(macro_data)
        bdi_str = str(round(bdi, 0)) if bdi else "N/A"
        wti_str = "$" + str(round(wti, 1)) if wti else "N/A"
        return (
            "**Optimal Booking Window — Seasonal Advice**\n\n"
            "  Best windows:  **January-March** (post-CNY softness) and **October-November** (pre-holiday lull)\n"
            "  Avoid:         July-September peak and December pre-holiday surge\n"
            "  Book early:    6-8 weeks ahead for Trans-Pacific, 8-10 weeks for Asia-Europe\n\n"
            "**Current market signals:**\n"
            "  BDI: **" + bdi_str + "** — "
            + ("elevated, consider booking sooner" if bdi and bdi > 2000 else "moderate, standard lead time is fine")
            + "\n"
            "  WTI crude: **" + wti_str + "**"
            + (" — higher fuel surcharges likely" if wti and wti > 80 else " — fuel surcharges relatively contained")
            + "\n\n"
            "_Pro tip: Lock in spot rates when the BDI is falling and book 6 weeks out for best rate-to-service balance._"
        )

    # ── PEAK SEASON ──────────────────────────────────────────────────────────
    if "peak season" in q or "peak" in q:
        current_year = datetime.datetime.now().year
        return (
            "**Container Shipping Peak Season — " + str(current_year) + "**\n\n"
            "  Primary peak:   **July-September** (" + str(current_year) + ")\n"
            "  Driven by:      Back-to-school (July) and holiday inventory build (Aug-Sep)\n"
            "  Secondary peak: Late January-February (pre-CNY inventory rush)\n\n"
            "**What to expect during peak:**\n"
            "  - Spot rates typically rise 20-40% above off-peak levels\n"
            "  - Equipment shortages at major origin ports (Shanghai, Ningbo, Shenzhen)\n"
            "  - Booking lead times extend to 8-12 weeks\n"
            "  - Trans-Pacific Eastbound is most affected\n\n"
            "**Recommendation:** Book July-September shipments by April-May to lock in rates."
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
            "**Trans-Pacific Market Conditions**\n\n"
            "  Eastbound rate:  **" + rate_str + "**\n"
            "  30-day trend:    " + trend_str + "\n"
            "  BDI (proxy):     " + bdi_str + "\n"
            + ("  Opportunity:     **" + str(round(tp_route.opportunity_score * 100, 1)) + "%** — " + tp_route.opportunity_label + "\n" if tp_route else "")
            + "\n"
            "**Market context:** The Trans-Pacific Eastbound lane (China-US West Coast) is the "
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
            "**Suez Canal / Red Sea Situation**\n\n"
            "  Asia-Europe rate: **" + rate_str + "** (" + pct_str + " 30d trend)\n\n"
            "**Background:** Since late 2023, Houthi attacks in the Red Sea have forced most major "
            "carriers to reroute via the Cape of Good Hope, adding 10-14 days to Asia-Europe transit "
            "and increasing fuel costs by approximately $1M per voyage.\n\n"
            "**Key impacts:**\n"
            "  - Asia-Europe transit: ~25 days (Suez) vs ~35-38 days (Cape reroute)\n"
            "  - Fuel surcharges: elevated by $300-600/FEU\n"
            "  - Effective capacity reduction: ~15-20% on Asia-Europe lane\n"
            "  - Rates have repriced significantly above pre-crisis levels\n\n"
            "_Monitor: UN/naval convoy updates and carrier blank sailing announcements for re-routing reversal signals._"
        )

    # ── CARBON / EMISSIONS ───────────────────────────────────────────────────
    if "carbon" in q or "emission" in q or "co2" in q or "green" in q or "esg" in q or "regulatory" in q:
        avg_co2 = _avg_co2_per_teu(route_results)
        return (
            "**Carbon / Emissions — Shipping Sustainability**\n\n"
            "  Est. avg CO2 per TEU (across tracked routes): **" + str(round(avg_co2, 1)) + " tonnes**\n\n"
            "**Industry context:**\n"
            "  - Ocean shipping emits ~2.5% of global GHG\n"
            "  - IMO 2030 target: 40% CO2 intensity reduction vs 2008\n"
            "  - IMO 2050 target: net-zero GHG from international shipping\n"
            "  - CII (Carbon Intensity Indicator) ratings now affect vessel employment\n\n"
            "**Key levers:**\n"
            "  - Slow steaming reduces fuel burn ~25% (adds 3-5 transit days)\n"
            "  - LNG, methanol, ammonia being trialled as alternative fuels\n"
            "  - FuelEU Maritime 2025 applies GHG intensity limits to EU port calls\n\n"
            "_Shorter routes (intra-Asia) produce significantly less CO2/TEU than long-haul lanes._"
        )

    # ── MARKET SENTIMENT ─────────────────────────────────────────────────────
    if "market sentiment" in q or "sentiment" in q or "market direction" in q:
        sentiment = _market_sentiment(insights)
        bdi = _bdi_value(macro_data)
        avg_rate = 0.0
        if route_results:
            avg_rate = sum(r.current_rate_usd_feu for r in route_results) / len(route_results)
        bdi_str = str(round(bdi, 0)) if bdi else "N/A"

        sent_label = {"bullish": "green / positive", "bearish": "red / negative", "mixed": "amber / mixed"}
        return (
            "**Overall Market Sentiment**\n\n"
            "  Direction:     **" + sentiment.upper() + "** — " + sent_label.get(sentiment, "mixed") + "\n"
            "  BDI:           " + bdi_str + "\n"
            "  Avg spot rate: **$" + str(round(avg_rate, 0)) + "/FEU**\n"
            "  Insights:      " + str(len(insights)) + " active signals\n\n"
            + ("**Top insight:** " + insights[0].title + " — " + insights[0].detail if insights else "No insights loaded.")
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
            top_route_str = "  Top lane: **" + top.route_name + "** at **$" + str(round(top.current_rate_usd_feu, 0)) + "/FEU**\n"

        return (
            "**90-Day Shipping Market Outlook**\n\n"
            "**Macro backdrop:**\n"
            "  BDI:         **" + bdi_str + "**\n"
            "  WTI crude:   **" + wti_str + "**\n"
            "  Sentiment:   **" + sentiment.upper() + "**\n\n"
            "**Route picture:**\n"
            + top_route_str
            + "  Routes tracked: " + str(len(route_results)) + "\n\n"
            "**Key themes over the next 90 days:**\n"
            "  1. Red Sea / Suez — rerouting continues to constrain Asia-Europe capacity\n"
            "  2. Trans-Pacific — watch US import demand for Q3 holiday inventory build\n"
            "  3. BDI trajectory — a sustained move above 2,000 signals tightening supply\n"
            "  4. Bunker costs — WTI above $80 compresses carrier margins and lifts surcharges\n"
            "  5. Tariff risk — US-China trade friction can sharply reprice spot rates\n\n"
            "**Recommendation:** " + (
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
        "  best route · port demand · freight rates · BDI · congestion\n"
        "  transit time · best time to ship · peak season · trans-pacific\n"
        "  suez / Red Sea · carbon / emissions · market sentiment · outlook\n"
        "  ZIM / MATX / SBLK / DAC / CMRE stock info · risk assessment\n\n"
        "_Type 'help' for a full list, or click one of the quick-action buttons._"
    )


# ---------------------------------------------------------------------------
# Confidence scorer (per-question)
# ---------------------------------------------------------------------------

def _compute_confidence(question: str) -> float:
    """Heuristic confidence for a given question."""
    q = question.lower()
    if any(kw in q for kw in ["bdi", "baltic dry", "suez", "trans-pacific",
                               "congestion", "freight rate", "carbon", "outlook",
                               "sentiment", "transit", "peak season"]):
        return 0.92
    tickers = ["zim", "matx", "sblk", "dac", "cmre"]
    if any(t in q for t in tickers):
        return 0.87
    if any(kw in q for kw in ["best route", "best opportunity", "top route"]):
        return 0.90
    if any(kw in q for kw in ["risk", "analyze", "rate forecast"]):
        return 0.85
    if "help" in q:
        return 0.99
    return 0.75


# ---------------------------------------------------------------------------
# Hero header
# ---------------------------------------------------------------------------

def _render_hero(
    port_results: list,
    route_results: list,
    insights: list,
    macro_data: dict,
) -> None:
    """Render the Intelligence Engine hero banner."""
    try:
        n_ports   = len(port_results)
        n_routes  = len(route_results)
        n_insights = len(insights)
        bdi        = _bdi_value(macro_data)
        bdi_str    = str(round(bdi, 0)) if bdi else "—"
        sentiment  = _market_sentiment(insights)
        sent_color = {"bullish": "#10b981", "bearish": "#ef4444", "mixed": "#f59e0b"}.get(sentiment, "#94a3b8")
        now_str    = datetime.datetime.now().strftime("%H:%M")

        stats_html = (
            '<div class="ie-stats-row">'
            '<div class="ie-stat">'
            f'<div class="ie-stat-val">{n_ports}</div>'
            '<div class="ie-stat-lbl">Ports</div>'
            '</div>'
            '<div class="ie-divider"></div>'
            '<div class="ie-stat">'
            f'<div class="ie-stat-val">{n_routes}</div>'
            '<div class="ie-stat-lbl">Routes</div>'
            '</div>'
            '<div class="ie-divider"></div>'
            '<div class="ie-stat">'
            f'<div class="ie-stat-val">{n_insights}</div>'
            '<div class="ie-stat-lbl">Signals</div>'
            '</div>'
            '<div class="ie-divider"></div>'
            '<div class="ie-stat">'
            f'<div class="ie-stat-val">{bdi_str}</div>'
            '<div class="ie-stat-lbl">BDI</div>'
            '</div>'
            '<div class="ie-divider"></div>'
            '<div class="ie-stat">'
            f'<div class="ie-stat-val" style="color:{sent_color}">{sentiment.upper()}</div>'
            '<div class="ie-stat-lbl">Sentiment</div>'
            '</div>'
            '</div>'
        )

        badges_html = (
            '<div class="ie-badges">'
            '<span class="ie-badge ie-badge-live">Live Data</span>'
            '<span class="ie-badge ie-badge-local">Local Engine</span>'
            f'<span class="ie-badge" style="background:rgba(100,116,139,0.1);color:#64748b;'
            f'border-color:rgba(100,116,139,0.2);">Updated {now_str}</span>'
            '</div>'
        )

        st.markdown(
            '<div class="ie-hero">'
            '<div class="ie-hero-top">'
            '<div class="ie-brand">'
            '<div class="ie-icon">🧠</div>'
            '<div>'
            '<div class="ie-title">Intelligence Engine</div>'
            '<div class="ie-subtitle">Shipping Market Assistant · Rule-Based · No External Calls</div>'
            '</div>'
            '</div>'
            + badges_html +
            '</div>'
            + stats_html +
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning("Hero render failed: {}", exc)
        st.markdown("### Intelligence Engine")


# ---------------------------------------------------------------------------
# Analysis type selector
# ---------------------------------------------------------------------------

_ANALYSIS_TYPES = ["Market Analysis", "Risk Assessment", "Trade Opportunities", "Regulatory"]
_ANALYSIS_ICONS = ["📊", "🛡️", "🚢", "📋"]


def _render_analysis_tabs() -> str:
    """Render analysis type selector. Returns the selected type string."""
    try:
        current = st.session_state.get("analysis_type", "Market Analysis")
        cols = st.columns(len(_ANALYSIS_TYPES))
        for i, (atype, icon) in enumerate(zip(_ANALYSIS_TYPES, _ANALYSIS_ICONS)):
            with cols[i]:
                is_active = (atype == current)
                btn_type = "primary" if is_active else "secondary"
                if st.button(
                    icon + " " + atype,
                    key="atype_" + str(i),
                    type=btn_type,
                    use_container_width=True,
                ):
                    st.session_state.analysis_type = atype
                    return atype
        return current
    except Exception as exc:
        logger.warning("Analysis tab render failed: {}", exc)
        return "Market Analysis"


# ---------------------------------------------------------------------------
# Context panel (right sidebar-within-tab)
# ---------------------------------------------------------------------------

def _render_context_panel(
    port_results: list[PortDemandResult],
    route_results: list[RouteOpportunity],
    insights: list[Insight],
    macro_data: dict,
    stock_data: dict,
) -> None:
    """Render the expanded data-context and freshness panel."""
    try:
        top = _top_route(route_results)
        sentiment = _market_sentiment(insights)
        bdi = _bdi_value(macro_data)

        sent_color = {"bullish": C_HIGH, "bearish": C_LOW, "mixed": C_MOD}.get(sentiment, C_MOD)
        top_score_str = (str(round(top.opportunity_score * 100, 1)) + "% · " + top.route_name) if top else "N/A"
        bdi_str = str(round(bdi, 0)) if bdi else "N/A"

        badge_html = (
            '<span style="background:' + _rgba(sent_color, 0.15) + '; color:' + sent_color + '; '
            'border:1px solid ' + _rgba(sent_color, 0.3) + '; '
            'padding:2px 10px; border-radius:999px; font-size:0.68rem; font-weight:700; '
            'text-transform:uppercase; letter-spacing:0.04em;">'
            + sentiment.upper() + '</span>'
        )

        panel_rows = [
            ("Ports loaded",    str(len(port_results)),  "green"),
            ("Routes scored",   str(len(route_results)), "green"),
            ("Active signals",  str(len(insights)),      "green" if insights else "grey"),
            ("BDI",             bdi_str,                 "green" if bdi else "grey"),
            ("Top opportunity", top_score_str,           "green" if top else "grey"),
        ]

        rows_html = ""
        for label, val, dot_color in panel_rows:
            rows_html += (
                '<div class="ctx-row">'
                '<span><span class="ctx-dot ctx-dot-' + dot_color + '"></span>' + label + '</span>'
                '<span class="ctx-val">' + val + '</span>'
                '</div>'
            )

        st.markdown(
            '<div class="ctx-panel">'
            '<div class="ctx-section-lbl">Data Context</div>'
            + rows_html
            + '<div style="margin-top:10px; display:flex; align-items:center; gap:6px; '
            'color:#94a3b8; font-size:0.74rem;">Market: ' + badge_html + '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning("Context panel failed: {}", exc)

    # ── Data-source freshness cards ──────────────────────────────────────────
    try:
        st.markdown(
            '<div class="ctx-section-lbl" style="margin-top:12px; margin-bottom:6px; '
            'font-size:0.64rem; font-weight:700; color:#475569; text-transform:uppercase; '
            'letter-spacing:0.08em;">Data Sources</div>',
            unsafe_allow_html=True,
        )

        sources = [
            ("Port Demand",   len(port_results) > 0,   "Session"),
            ("Route Scores",  len(route_results) > 0,  "Session"),
            ("BDI (BDIY)",    macro_data.get("BDIY") is not None, _data_freshness_label(macro_data, "BDIY")),
            ("WTI Crude",     macro_data.get("DCOILWTICO") is not None, _data_freshness_label(macro_data, "DCOILWTICO")),
            ("Stock Data",    len(stock_data) > 0,     "Session"),
            ("Insights",      len(insights) > 0,       "Session"),
        ]

        for name, loaded, freshness in sources:
            dot = "ctx-dot-green" if loaded else "ctx-dot-grey"
            freshness_str = freshness if loaded else "Not loaded"
            st.markdown(
                '<div class="ds-card">'
                '<div class="ds-card-name">'
                '<span class="ctx-dot ' + dot + '"></span>' + name
                '</div>'
                '<div class="ds-card-fresh">' + freshness_str + '</div>'
                '</div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        logger.warning("Data sources panel failed: {}", exc)

    # ── Recent insights panel ────────────────────────────────────────────────
    try:
        if insights:
            st.markdown(
                '<div class="ctx-section-lbl" style="margin-top:12px; margin-bottom:6px; '
                'font-size:0.64rem; font-weight:700; color:#475569; text-transform:uppercase; '
                'letter-spacing:0.08em;">Recent Insights</div>',
                unsafe_allow_html=True,
            )

            tag_color_map = {
                "CONVERGENCE": ("#8b5cf6", "rgba(139,92,246,0.1)", "rgba(139,92,246,0.3)"),
                "ROUTE":       ("#3b82f6", "rgba(59,130,246,0.1)",  "rgba(59,130,246,0.3)"),
                "PORT_DEMAND": ("#10b981", "rgba(16,185,129,0.1)",  "rgba(16,185,129,0.3)"),
                "MACRO":       ("#06b6d4", "rgba(6,182,212,0.1)",   "rgba(6,182,212,0.3)"),
            }

            recent = sorted(insights, key=lambda i: i.score, reverse=True)[:5]
            for ins in recent:
                try:
                    cat = getattr(ins, "category", "ROUTE")
                    tc, bg, border = tag_color_map.get(cat, ("#94a3b8", "rgba(148,163,184,0.1)", "rgba(148,163,184,0.3)"))
                    score_pct = str(round(ins.score * 100, 0)) + "%"
                    ts_str = datetime.datetime.now().strftime("%H:%M")

                    st.markdown(
                        '<div class="ri-item">'
                        '<div class="ri-title">' + ins.title + '</div>'
                        '<div class="ri-meta">'
                        '<span class="ri-tag" style="color:' + tc + ';background:' + bg + ';border-color:' + border + ';">' + cat + '</span>'
                        '<span class="ri-tag" style="color:#64748b;background:rgba(100,116,139,0.08);border-color:rgba(100,116,139,0.15);">' + score_pct + '</span>'
                        '<span class="ri-ts">' + ts_str + '</span>'
                        '</div>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Recent insights panel failed: {}", exc)


# ---------------------------------------------------------------------------
# Quick-action buttons
# ---------------------------------------------------------------------------

_QUICK_QUESTIONS = [
    ("📈 Analyze top opportunities",   "analyze top opportunities"),
    ("📊 Explain BDI movement",        "BDI interpretation"),
    ("🛡️ Risk assessment",             "risk assessment"),
    ("🔮 Rate forecast summary",       "rate forecast summary"),
    ("🚢 Best route right now?",       "best route"),
    ("🌊 Red Sea update",              "suez red sea"),
    ("📦 Port congestion worst",       "congestion"),
    ("💹 Which stock to buy?",         "should i buy stock"),
]


def _render_quick_buttons(context: dict) -> None:
    """Render 8 quick-action buttons in 2 columns."""
    try:
        col1, col2 = st.columns(2)
        cols = [col1, col2]
        for idx, (label, kw) in enumerate(_QUICK_QUESTIONS):
            with cols[idx % 2]:
                if st.button(label, key="qq_" + str(idx)):
                    _append_message("user", label.split(" ", 1)[1] if " " in label else label)
                    try:
                        answer = answer_question(kw, context)
                        confidence = _compute_confidence(kw)
                    except Exception as exc:
                        logger.warning("Answer engine error: {}", exc)
                        answer = "Unable to process this question right now."
                        confidence = 0.0
                    _append_message("assistant", answer, confidence)
                    st.rerun()
    except Exception as exc:
        logger.warning("Quick buttons render failed: {}", exc)


# ---------------------------------------------------------------------------
# Export / save conversation
# ---------------------------------------------------------------------------

def _render_export_bar() -> None:
    """Render export conversation controls."""
    try:
        if not st.session_state.get("chat_history"):
            return

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        col_exp, col_clear, col_spacer = st.columns([1, 1, 2])

        with col_exp:
            if st.button("💾 Save conversation", key="save_convo", use_container_width=True):
                lines = []
                for msg in st.session_state.chat_history:
                    role = "You" if msg["role"] == "user" else "Assistant"
                    lines.append(f"[{msg.get('ts','')}] {role}:\n{msg['text']}\n")
                conversation_text = "\n".join(lines)
                st.session_state.saved_conversation = conversation_text
                st.toast("Conversation saved!", icon="✅")

        with col_clear:
            if st.button("🗑️ Clear chat", key="clear_chat_btn", use_container_width=True):
                st.session_state.chat_history = []
                st.session_state.saved_conversation = None
                st.rerun()

        if st.session_state.get("saved_conversation"):
            st.download_button(
                label="⬇️ Download as .txt",
                data=st.session_state.saved_conversation,
                file_name="shipping_assistant_chat.txt",
                mime="text/plain",
                key="download_convo",
            )
    except Exception as exc:
        logger.warning("Export bar failed: {}", exc)


# ---------------------------------------------------------------------------
# Analysis history (expandable cards for previous Q&A)
# ---------------------------------------------------------------------------

def _render_analysis_history() -> None:
    """Render previous assistant Q&A pairs as expandable history cards."""
    try:
        history = st.session_state.get("chat_history", [])
        # Pair up user → assistant messages
        pairs = []
        i = 0
        while i < len(history) - 1:
            if history[i]["role"] == "user" and history[i + 1]["role"] == "assistant":
                pairs.append((history[i], history[i + 1]))
                i += 2
            else:
                i += 1

        if not pairs:
            return

        st.markdown(
            '<div style="font-size:0.72rem; font-weight:700; color:#475569; '
            'text-transform:uppercase; letter-spacing:0.08em; '
            'margin-top:24px; margin-bottom:8px;">Analysis History</div>',
            unsafe_allow_html=True,
        )

        # Show last 5 pairs (oldest first in history, so reverse)
        for user_msg, asst_msg in reversed(pairs[-5:]):
            q_text = user_msg["text"][:80] + ("…" if len(user_msg["text"]) > 80 else "")
            a_preview = asst_msg["text"][:120].replace("**", "").replace("\n", " ")
            a_preview = a_preview + ("…" if len(asst_msg["text"]) > 120 else "")
            conf = asst_msg.get("confidence", 0.85)
            conf_pct = int(conf * 100)

            with st.expander(f"Q: {q_text}", expanded=False):
                st.markdown(asst_msg["text"])
                st.markdown(
                    f'<div style="font-size:0.68rem; color:#475569; margin-top:6px;">'
                    f'Confidence: {conf_pct}% · {user_msg.get("ts","")}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        logger.warning("Analysis history render failed: {}", exc)


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
    try:
        _init_state()
    except Exception as exc:
        logger.warning("State init failed: {}", exc)

    # Prefer the full route list if provided
    try:
        routes_for_engine = route_results_all if route_results_all else route_results
    except Exception:
        routes_for_engine = route_results or []

    context = {
        "port_results":  port_results,
        "route_results": routes_for_engine,
        "insights":      insights,
        "freight_data":  freight_data,
        "macro_data":    macro_data,
        "stock_data":    stock_data,
    }

    # Inject CSS
    try:
        st.markdown(_CHAT_CSS, unsafe_allow_html=True)
    except Exception:
        pass

    # ── Hero header ──────────────────────────────────────────────────────────
    try:
        _render_hero(port_results, routes_for_engine, insights, macro_data)
    except Exception as exc:
        logger.warning("Hero failed: {}", exc)

    # ── Layout: main area (3/4) + right context panel (1/4) ─────────────────
    main_col, ctx_col = st.columns([3, 1])

    with ctx_col:
        try:
            _render_context_panel(
                port_results, routes_for_engine, insights, macro_data, stock_data
            )
        except Exception as exc:
            logger.warning("Context panel outer failed: {}", exc)

    with main_col:
        # ── Analysis type selector ───────────────────────────────────────────
        try:
            _render_analysis_tabs()
        except Exception as exc:
            logger.warning("Analysis tabs failed: {}", exc)

        # ── Engine health check ──────────────────────────────────────────────
        _engine_ok = True
        try:
            answer_question("help", context)
        except Exception as _engine_exc:
            _engine_ok = False
            logger.warning("Answer engine unavailable: {}", _engine_exc)
        if not _engine_ok:
            st.warning("AI assistant engine unavailable — quick-action buttons still work.")

        # ── Chat history display ─────────────────────────────────────────────
        try:
            chat_container = st.container()
            with chat_container:
                history = st.session_state.get("chat_history", [])
                if not history:
                    st.markdown(
                        '<div style="background:#111827; border:1px solid rgba(255,255,255,0.06); '
                        'border-radius:12px; padding:36px 24px; text-align:center; '
                        'color:#475569; font-size:0.88rem; margin-bottom:12px;">'
                        '<div style="font-size:2rem; margin-bottom:12px;">🧠</div>'
                        '<div style="font-weight:600; color:#64748b; margin-bottom:6px;">'
                        'Intelligence Engine Ready</div>'
                        '<div>Ask a question below or click a quick-action button to get started.</div>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    for msg in history:
                        try:
                            _render_bubble(
                                msg["role"],
                                msg["text"],
                                msg.get("ts", ""),
                                msg.get("confidence", 0.85),
                            )
                        except Exception as exc:
                            logger.warning("Bubble render failed: {}", exc)
        except Exception as exc:
            logger.warning("Chat container failed: {}", exc)

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # ── Chat input ───────────────────────────────────────────────────────
        try:
            user_input = st.chat_input(
                "Ask about routes, rates, ports, stocks, BDI, Suez, risk...",
                key="assistant_question_input",
            )

            if user_input and user_input.strip():
                question = user_input.strip()
                _append_message("user", question)

                think_ph = st.empty()
                with think_ph:
                    try:
                        _render_thinking()
                    except Exception:
                        pass

                try:
                    answer = answer_question(question, context)
                    confidence = _compute_confidence(question)
                except Exception as exc:
                    logger.warning("Answer engine error for question '{}': {}", question, exc)
                    answer = "Unable to process that question — please try rephrasing or use a quick-action button."
                    confidence = 0.0

                think_ph.empty()
                _append_message("assistant", answer, confidence)
                logger.info("Assistant answered question: " + question[:80])
                st.rerun()
        except Exception as exc:
            logger.warning("Chat input section failed: {}", exc)

        # ── Quick-action buttons ─────────────────────────────────────────────
        try:
            st.markdown(
                '<div style="margin-top:18px; margin-bottom:8px; font-size:0.70rem; '
                'font-weight:700; color:#475569; text-transform:uppercase; '
                'letter-spacing:0.09em;">Quick Actions</div>',
                unsafe_allow_html=True,
            )
            _render_quick_buttons(context)
        except Exception as exc:
            logger.warning("Quick buttons section failed: {}", exc)

        # ── Export / save bar ────────────────────────────────────────────────
        try:
            _render_export_bar()
        except Exception as exc:
            logger.warning("Export bar outer failed: {}", exc)

        # ── Analysis history ─────────────────────────────────────────────────
        try:
            _render_analysis_history()
        except Exception as exc:
            logger.warning("Analysis history outer failed: {}", exc)
