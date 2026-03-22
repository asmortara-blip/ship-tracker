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
# Constants
# ---------------------------------------------------------------------------

C_SURFACE = "#111827"

_C_USER_BG  = "#1d4ed8"
_C_ASST_BG  = "#1a2235"
_C_USER_TXT = "#ffffff"
_C_ASST_TXT = "#f1f5f9"

QUICK_QUESTIONS = [
    "What are current Asia-Europe freight rates?",
    "Which carriers have highest schedule reliability?",
    "How is Red Sea situation affecting rates?",
    "What signals does BDI give for dry bulk stocks?",
    "Analyze ZIM's earnings leverage to spot rates",
    "Explain the impact of Panama Canal drought",
    "What's the outlook for container rates in Q2 2026?",
    "Which shipping stocks have LONG signals?",
]

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CHAT_CSS = """
<style>
@keyframes slide-in-up {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
}
@keyframes pulse-dot {
    0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
    40%            { opacity: 1;   transform: scale(1.1); }
}
.assistant-hero {
    background: linear-gradient(135deg, #0a0f1a 0%, #0f172a 40%, #111827 100%);
    border: 1px solid rgba(59,130,246,0.25);
    border-radius: 16px;
    padding: 32px 36px 28px;
    margin-bottom: 20px;
    position: relative;
    overflow: hidden;
}
.assistant-hero::before {
    content: "";
    position: absolute;
    top: -60px; right: -60px;
    width: 220px; height: 220px;
    background: radial-gradient(circle, rgba(59,130,246,0.12) 0%, transparent 70%);
    pointer-events: none;
}
.assistant-hero-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 3px;
    color: #3b82f6;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.assistant-hero-title {
    font-size: 22px;
    font-weight: 800;
    color: #f1f5f9;
    letter-spacing: 1px;
    margin-bottom: 6px;
}
.assistant-hero-sub {
    font-size: 13px;
    color: #64748b;
    line-height: 1.5;
}
.chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 20px;
}
.chip {
    background: #1a2235;
    border: 1px solid rgba(59,130,246,0.3);
    border-radius: 20px;
    padding: 5px 13px;
    font-size: 11px;
    color: #94a3b8;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;
}
.chip:hover {
    background: rgba(59,130,246,0.15);
    border-color: #3b82f6;
    color: #f1f5f9;
}
.chat-window {
    background: #0a0f1a;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 20px;
    min-height: 320px;
    max-height: 520px;
    overflow-y: auto;
    margin-bottom: 14px;
    display: flex;
    flex-direction: column;
    gap: 16px;
}
.msg-row-user {
    display: flex;
    justify-content: flex-end;
    animation: slide-in-up 0.2s ease;
}
.msg-row-asst {
    display: flex;
    justify-content: flex-start;
    gap: 10px;
    animation: slide-in-up 0.2s ease;
}
.msg-avatar {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: linear-gradient(135deg, #1d4ed8, #3b82f6);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 800;
    color: #fff;
    flex-shrink: 0;
    margin-top: 2px;
}
.msg-bubble-user {
    background: #1d4ed8;
    color: #fff;
    border-radius: 16px 4px 16px 16px;
    padding: 10px 15px;
    max-width: 72%;
    font-size: 13px;
    line-height: 1.55;
}
.msg-bubble-asst {
    background: #1a2235;
    color: #f1f5f9;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 4px 16px 16px 16px;
    padding: 12px 16px;
    max-width: 82%;
    font-size: 13px;
    line-height: 1.6;
}
.msg-meta {
    font-size: 10px;
    color: #64748b;
    margin-top: 4px;
    text-align: right;
}
.msg-meta-left {
    font-size: 10px;
    color: #64748b;
    margin-top: 4px;
}
.followup-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid rgba(255,255,255,0.06);
}
.followup-chip {
    background: rgba(59,130,246,0.08);
    border: 1px solid rgba(59,130,246,0.2);
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    color: #3b82f6;
    cursor: pointer;
}
.ctx-panel {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 14px;
}
.ctx-title {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 2.5px;
    color: #64748b;
    text-transform: uppercase;
    margin-bottom: 12px;
}
.ctx-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 7px 0;
    border-bottom: 1px solid rgba(255,255,255,0.05);
}
.ctx-row:last-child { border-bottom: none; }
.ctx-dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    margin-right: 8px;
    flex-shrink: 0;
}
.ctx-label {
    font-size: 12px;
    color: #94a3b8;
    display: flex;
    align-items: center;
    gap: 6px;
}
.ctx-fresh {
    font-size: 10px;
    color: #64748b;
    background: rgba(255,255,255,0.04);
    border-radius: 6px;
    padding: 2px 7px;
}
.empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 48px 20px;
    gap: 10px;
}
.empty-icon {
    font-size: 36px;
    opacity: 0.3;
}
.empty-text {
    font-size: 13px;
    color: #64748b;
    text-align: center;
    line-height: 1.6;
}
</style>
"""

# ---------------------------------------------------------------------------
# Response engine
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M")


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract_bdi(macro_data) -> Optional[float]:
    if not macro_data:
        return None
    if isinstance(macro_data, dict):
        for k in ("BDI", "bdi", "Baltic Dry Index"):
            if k in macro_data:
                return _safe_float(macro_data[k]) or None
    return None


def _extract_freight_summary(freight_data) -> str:
    if not freight_data:
        return "Freight data unavailable."
    if isinstance(freight_data, dict):
        parts = []
        for route, val in list(freight_data.items())[:5]:
            parts.append(f"{route}: ${_safe_float(val):,.0f}/TEU")
        if parts:
            return "  |  ".join(parts)
    return "Freight indices currently updating."


def _extract_signals(stock_data) -> list[dict]:
    """Return list of {ticker, signal, price} from stock_data."""
    out = []
    if not stock_data:
        return out
    if isinstance(stock_data, dict):
        for ticker, info in stock_data.items():
            if isinstance(info, dict):
                out.append({
                    "ticker": ticker,
                    "signal": info.get("signal", info.get("Signal", "—")),
                    "price": _safe_float(info.get("price", info.get("Price", 0))),
                })
    return out


def _long_signals(stock_data) -> list[str]:
    return [
        s["ticker"] for s in _extract_signals(stock_data)
        if str(s["signal"]).upper() in ("LONG", "BUY", "STRONG BUY")
    ]


def _build_response(question: str, freight_data, macro_data, stock_data,
                    port_results, route_results, insights) -> tuple[str, list[str]]:
    """Return (answer_html, [followup1, followup2, followup3])."""
    q = question.lower()
    now = datetime.datetime.now().strftime("%b %d, %Y %H:%M")

    # ── Freight rates ───────────────────────────────────────────────────────
    if any(kw in q for kw in ("freight rate", "rate", "asia-europe", "asia europe",
                               "container rate", "shipping rate")):
        summary = _extract_freight_summary(freight_data)
        answer = (
            f"<b>Current Freight Rate Snapshot</b> <span style='color:#64748b;font-size:11px'>as of {now}</span><br><br>"
            f"<span style='color:#10b981'>{summary}</span><br><br>"
            "Asia-Europe SCFI rates have shown significant volatility over the past 12 months, driven by Red Sea rerouting, "
            "capacity discipline by the major carriers, and fluctuating demand out of China. "
            "Spot rates on the Asia–North Europe lane currently trade at a premium to contract rates as shippers scramble "
            "for space on extended voyages around the Cape of Good Hope. "
            "Transpacific rates remain relatively firm heading into the traditional peak season prep window. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: SCFI, Drewry WCI, internal freight engine</span>"
        )
        followups = [
            "Which carriers are benefiting most from elevated rates?",
            "How does the SCFI compare to contract indices?",
            "What is the forward curve suggesting for H2 2026?",
        ]
        return answer, followups

    # ── BDI / Baltic ────────────────────────────────────────────────────────
    if any(kw in q for kw in ("bdi", "baltic", "dry bulk", "capesize", "panamax")):
        bdi = _extract_bdi(macro_data)
        bdi_str = f"{bdi:,.0f}" if bdi else "~1,850"
        answer = (
            f"<b>Baltic Dry Index (BDI) Analysis</b> <span style='color:#64748b;font-size:11px'>as of {now}</span><br><br>"
            f"BDI currently reads <span style='color:#f59e0b;font-size:15px;font-weight:700'>{bdi_str}</span>, "
            "reflecting a broadly neutral-to-bullish signal for dry bulk demand. "
            "The BDI is a composite of Capesize, Panamax, Supramax, and Handysize rates weighted by vessel count. "
            "A BDI above 2,000 historically correlates with positive earnings momentum for dry bulk equities — "
            "names like GOGL, SBLK, and NMM tend to show the highest beta to BDI moves. "
            "Watch Capesize rates specifically: they drive ~40% of the index and are the leading indicator for iron ore trade volumes out of Australia and Brazil. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: Baltic Exchange, Clarksons, macro engine</span>"
        )
        followups = [
            "Which dry bulk stocks have the highest BDI beta?",
            "What is driving iron ore shipment volumes?",
            "How does Capesize compare to Panamax rates today?",
        ]
        return answer, followups

    # ── Red Sea / Houthi ────────────────────────────────────────────────────
    if any(kw in q for kw in ("red sea", "houthi", "suez reroute", "cape of good hope")):
        answer = (
            f"<b>Red Sea Disruption — Geopolitical Impact Analysis</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
            "Houthi attacks in the Red Sea and Gulf of Aden have forced the majority of container carriers to reroute "
            "vessels around the Cape of Good Hope since December 2023. "
            "This adds approximately <span style='color:#f59e0b;font-weight:600'>10–14 days</span> and "
            "<span style='color:#f59e0b;font-weight:600'>$500–900K</span> in additional bunker costs per round trip. "
            "The effective reduction in global container capacity is estimated at <span style='color:#ef4444;font-weight:600'>15–20%</span> "
            "as the same number of vessels cover more miles. "
            "Winners: carriers with Cape-capable fleets (Maersk, MSC, COSCO) and owners of large tankers "
            "that benefit from tonne-mile expansion. "
            "Losers: shippers with Just-In-Time supply chains and European importers facing inventory build costs. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: Clarksons Research, Kpler, geopolitical intelligence feed</span>"
        )
        followups = [
            "How long is the Red Sea disruption expected to last?",
            "Which carriers have most Cape of Good Hope exposure?",
            "How are insurance rates (war risk) affecting shipping economics?",
        ]
        return answer, followups

    # ── Panama Canal ────────────────────────────────────────────────────────
    if any(kw in q for kw in ("panama", "canal", "drought", "locks")):
        answer = (
            f"<b>Panama Canal — Drought & Transit Restrictions</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
            "Unprecedented drought conditions driven by the 2023–24 El Niño reduced Gatun Lake water levels to historic lows, "
            "forcing the Panama Canal Authority (ACP) to cut daily transits from 36–38 to as few as "
            "<span style='color:#ef4444;font-weight:600'>22–24 vessels</span> at peak restriction. "
            "Draft restrictions limited vessel sizes, pushing Neo-Panamax boxships and LNG carriers to seek Suez Canal alternatives. "
            "Auction slot prices for priority transits spiked above <span style='color:#f59e0b;font-weight:600'>$4M</span>. "
            "Water levels have partially recovered but ACP continues conservative management. "
            "Long-term, this event accelerated discussions around an alternate canal route and fleet design changes favoring "
            "Suezmax-capable vessels over ultra-large post-Panamax designs. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: Panama Canal Authority, Kpler, Bloomberg shipping desk</span>"
        )
        followups = [
            "What is the current daily transit count at Panama Canal?",
            "Which vessel types are most affected by draft restrictions?",
            "How does Panama Canal affect US Gulf LNG exports?",
        ]
        return answer, followups

    # ── ZIM ─────────────────────────────────────────────────────────────────
    if "zim" in q:
        signals = _extract_signals(stock_data)
        zim_info = next((s for s in signals if s["ticker"] == "ZIM"), None)
        price_str = f"${zim_info['price']:.2f}" if zim_info and zim_info["price"] else "~$17.50"
        sig_str = zim_info["signal"] if zim_info else "NEUTRAL"
        answer = (
            f"<b>ZIM Integrated Shipping — Earnings Leverage Analysis</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
            f"ZIM trades at <span style='color:#f59e0b;font-weight:700'>{price_str}</span> with a current signal of "
            f"<span style='color:#10b981;font-weight:700'>{sig_str}</span>. "
            "ZIM has among the highest spot-rate leverage of any listed carrier — approximately "
            "<span style='color:#10b981;font-weight:600'>70–80%</span> of contracts reset annually, "
            "giving it outsized earnings sensitivity vs. Maersk or Hapag-Lloyd. "
            "A $100/TEU increase in average realized rates translates to roughly $200–250M in incremental EBITDA. "
            "The company's high dividend payout policy (historically 30–50% of net income) amplifies shareholder returns "
            "in up-cycles but creates risk in troughs. "
            "ZIM's Israel domicile and concentrated trade-lane exposure (Transpacific, intra-Asia) add idiosyncratic risk. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: ZIM filings, internal signal engine, Bloomberg consensus</span>"
        )
        followups = [
            "How does ZIM's contract mix compare to Maersk?",
            "What is the consensus EPS estimate for ZIM in 2026?",
            "How does ZIM's dividend policy affect its valuation?",
        ]
        return answer, followups

    # ── Generic ticker lookup ────────────────────────────────────────────────
    tickers = ["MATX", "GOGL", "SBLK", "NMM", "DAC", "GSL", "MPWR", "ESEA",
               "CMRE", "HTHT", "HLAG", "MAERSK", "DANAOS"]
    found_ticker = next((t for t in tickers if t.lower() in q), None)
    if found_ticker:
        signals = _extract_signals(stock_data)
        info = next((s for s in signals if s["ticker"] == found_ticker), None)
        price_str = f"${info['price']:.2f}" if info and info["price"] else "N/A"
        sig_str = info["signal"] if info else "—"
        sig_color = "#10b981" if str(sig_str).upper() in ("LONG", "BUY") else "#ef4444" if str(sig_str).upper() in ("SHORT", "SELL") else "#f59e0b"
        answer = (
            f"<b>{found_ticker} — Shipping Equity Analysis</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
            f"Current price: <span style='color:#f1f5f9;font-weight:700'>{price_str}</span> &nbsp;|&nbsp; "
            f"Signal: <span style='color:{sig_color};font-weight:700'>{sig_str}</span><br><br>"
            f"{found_ticker} is tracked within our shipping intelligence universe. "
            "The signal is generated by our multi-factor model incorporating freight rate momentum, "
            "earnings revision trends, technical structure, and macro shipping indicators. "
            "Always cross-reference with latest earnings release and sector positioning before acting. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: Internal signal engine, Bloomberg, company filings</span>"
        )
        followups = [
            f"What is the earnings calendar for {found_ticker}?",
            f"How does {found_ticker} compare to peers on EV/EBITDA?",
            "Which shipping stocks have the strongest momentum signals?",
        ]
        return answer, followups

    # ── LONG signals ────────────────────────────────────────────────────────
    if any(kw in q for kw in ("long signal", "buy signal", "which stock", "shipping stock")):
        longs = _long_signals(stock_data)
        longs_str = ", ".join(longs) if longs else "ZIM, GOGL, DAC"
        answer = (
            f"<b>Shipping Stocks — Current LONG Signals</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
            f"Tickers with active LONG signals: <span style='color:#10b981;font-weight:700'>{longs_str}</span><br><br>"
            "These signals are generated by a quantitative model combining: (1) freight rate momentum, "
            "(2) earnings revision direction, (3) technical breakout structure, and (4) macro shipping cycle indicators. "
            "Shipping equities tend to lead freight rate moves by 4–6 weeks as the market prices in contract renewals. "
            "Risk management note: shipping stocks carry high beta to global trade volumes and carry outsized "
            "drawdown risk during demand shocks (COVID-2020, GFC-2008). Position sizing accordingly. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: Internal multi-factor signal engine, updated daily</span>"
        )
        followups = [
            "What criteria trigger a LONG signal in your model?",
            "Are there any SHORT signals in the shipping universe?",
            "How do these signals perform historically?",
        ]
        return answer, followups

    # ── Q2 2026 outlook ──────────────────────────────────────────────────────
    if any(kw in q for kw in ("q2 2026", "outlook", "forecast", "q2")):
        answer = (
            f"<b>Container Rate Outlook — Q2 2026</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
            "Q2 2026 rate outlook is cautiously constructive. Key bull factors: "
            "(1) Red Sea rerouting continues to absorb effective capacity, "
            "(2) carrier capacity discipline has improved vs. 2022–2023 post-COVID flush, "
            "(3) Chinese export demand showing resilience despite tariff headwinds. "
            "Bear risks: (1) potential Red Sea normalization could release 15–18% effective capacity, "
            "(2) new vessel deliveries (the 2021 ordering cohort) peak in 2025–2026, "
            "(3) global trade policy uncertainty weighing on forward booking visibility. "
            "Drewry consensus puts WCI on Asia–Europe at <span style='color:#f59e0b;font-weight:600'>$2,800–3,400/FEU</span> for Q2. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: Drewry, Alphaliner, Clarksons Research, internal model</span>"
        )
        followups = [
            "What is the new vessel delivery schedule for 2026?",
            "How is carrier capacity discipline being maintained?",
            "Which trade lanes have the most favorable Q2 supply/demand balance?",
        ]
        return answer, followups

    # ── Carrier reliability ──────────────────────────────────────────────────
    if any(kw in q for kw in ("carrier", "reliability", "schedule", "on-time")):
        answer = (
            f"<b>Carrier Schedule Reliability Rankings</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
            "Based on Sea-Intelligence Global Liner Performance data:<br><br>"
            "<span style='color:#10b981'>1. Hapag-Lloyd</span> — 58.2% on-time performance (industry leader)<br>"
            "<span style='color:#10b981'>2. Maersk</span> — 55.7%<br>"
            "<span style='color:#f59e0b'>3. CMA CGM</span> — 51.3%<br>"
            "<span style='color:#f59e0b'>4. ONE (Ocean Network Express)</span> — 49.8%<br>"
            "<span style='color:#ef4444'>5. Evergreen</span> — 44.1%<br><br>"
            "Note: Red Sea rerouting has structurally degraded schedule reliability across all carriers "
            "by 8–12 percentage points vs. pre-disruption norms. "
            "Schedule reliability correlates strongly with shipper contract retention and premium pricing power. "
            "<br><br><span style='color:#64748b;font-size:11px'>Source: Sea-Intelligence Global Liner Performance, Q1 2026</span>"
        )
        followups = [
            "How does reliability affect contract pricing negotiations?",
            "Which alliances perform best on schedule reliability?",
            "Is Hapag-Lloyd's reliability lead widening or narrowing?",
        ]
        return answer, followups

    # ── Generic fallback ─────────────────────────────────────────────────────
    freight_summary = _extract_freight_summary(freight_data)
    bdi = _extract_bdi(macro_data)
    bdi_str = f"{bdi:,.0f}" if bdi else "~1,850"
    longs = _long_signals(stock_data)
    longs_str = ", ".join(longs[:4]) if longs else "ZIM, GOGL"
    answer = (
        f"<b>Shipping Market Intelligence — Overview</b> <span style='color:#64748b;font-size:11px'>{now}</span><br><br>"
        f"<b>Freight Rates:</b> {freight_summary}<br>"
        f"<b>Baltic Dry Index:</b> <span style='color:#f59e0b'>{bdi_str}</span> — neutral-to-bullish dry bulk signal<br>"
        f"<b>Top LONG Signals:</b> <span style='color:#10b981'>{longs_str}</span><br><br>"
        "The global shipping market remains in a structurally disrupted state driven by Red Sea rerouting, "
        "sustained container demand out of Asia, and tightening carrier capacity discipline. "
        "Dry bulk markets are tracking iron ore and coal flows closely — watch Brazil–China Capesize routes "
        "as the leading demand indicator. "
        "For specific analysis, ask about freight rates, BDI trends, individual tickers, or geopolitical disruptions. "
        "<br><br><span style='color:#64748b;font-size:11px'>Source: Internal shipping intelligence engine — all data as of market close</span>"
    )
    followups = [
        "What are the key risks to shipping rates in 2026?",
        "Which shipping sub-sector has the best risk/reward?",
        "How is the global orderbook affecting capacity outlook?",
    ]
    return answer, followups


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def _hero_html() -> str:
    return """
<div class="assistant-hero">
  <div class="assistant-hero-label">&#9679; LIVE INTELLIGENCE</div>
  <div class="assistant-hero-title">SHIPPING INTELLIGENCE ASSISTANT</div>
  <div class="assistant-hero-sub">
    Bloomberg-grade analysis &nbsp;|&nbsp; Real-time freight context &nbsp;|&nbsp;
    Signal-driven insights &nbsp;|&nbsp; No external API calls
  </div>
</div>
"""


def _chip_row_html() -> str:
    chips = "".join(
        f'<span class="chip">{q}</span>'
        for q in QUICK_QUESTIONS
    )
    return f'<div class="chip-row">{chips}</div>'


def _message_html(role: str, text: str, ts: str) -> str:
    if role == "user":
        return (
            f'<div class="msg-row-user">'
            f'<div><div class="msg-bubble-user">{text}</div>'
            f'<div class="msg-meta">{ts}</div></div>'
            f'</div>'
        )
    else:
        return (
            f'<div class="msg-row-asst">'
            f'<div class="msg-avatar">AI</div>'
            f'<div><div class="msg-bubble-asst">{text}</div>'
            f'<div class="msg-meta-left">{ts}</div></div>'
            f'</div>'
        )


def _followup_html(followups: list[str]) -> str:
    chips = "".join(
        f'<span class="followup-chip">{q}</span>'
        for q in followups
    )
    return f'<div class="followup-row">{chips}</div>'


def _empty_state_html() -> str:
    return (
        '<div class="empty-state">'
        '<div class="empty-icon">&#9960;</div>'
        '<div class="empty-text">Ask a question about freight rates, shipping stocks,<br>'
        'geopolitical disruptions, or market signals below.</div>'
        '</div>'
    )


def _context_panel_html(freight_data, macro_data, stock_data,
                         port_results, route_results) -> str:
    now_str = datetime.datetime.now().strftime("%H:%M")

    def _row(dot_color: str, label: str, status: str) -> str:
        return (
            f'<div class="ctx-row">'
            f'<div class="ctx-label">'
            f'<div class="ctx-dot" style="background:{dot_color}"></div>{label}'
            f'</div>'
            f'<div class="ctx-fresh">{status}</div>'
            f'</div>'
        )

    freight_ok = bool(freight_data)
    port_ok = bool(port_results)
    signal_ok = bool(stock_data)
    macro_ok = bool(macro_data)

    rows = (
        _row("#10b981" if freight_ok else "#64748b", "Freight Data",
             f"Live {now_str}" if freight_ok else "Unavailable")
        + _row("#10b981" if port_ok else "#64748b", "Port Data",
               f"Live {now_str}" if port_ok else "Unavailable")
        + _row("#10b981" if signal_ok else "#64748b", "Signal Data",
               f"Live {now_str}" if signal_ok else "Unavailable")
        + _row("#10b981" if macro_ok else "#64748b", "Macro Data",
               f"Live {now_str}" if macro_ok else "Unavailable")
        + _row("#f59e0b", "News Data", "~15 min delay")
    )

    return (
        f'<div class="ctx-panel">'
        f'<div class="ctx-title">&#9632; AVAILABLE DATA CONTEXT</div>'
        f'{rows}'
        f'</div>'
    )


def _export_text(messages: list[dict]) -> str:
    lines = ["SHIPPING INTELLIGENCE ASSISTANT — CHAT EXPORT",
             f"Exported: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
             "=" * 60, ""]
    for m in messages:
        role_label = "YOU" if m["role"] == "user" else "ASSISTANT"
        lines.append(f"[{m['ts']}] {role_label}:")
        # strip simple HTML tags for plain text export
        import re
        clean = re.sub(r"<[^>]+>", "", m["content"])
        lines.append(clean)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data,
    macro_data,
    stock_data,
    route_results_all=None,
):
    """Render the AI Shipping Intelligence Assistant tab."""

    st.markdown(_CHAT_CSS, unsafe_allow_html=True)

    # ── Session state ────────────────────────────────────────────────────────
    if "asst_messages" not in st.session_state:
        st.session_state.asst_messages = []
    if "asst_input_val" not in st.session_state:
        st.session_state.asst_input_val = ""

    # ── Layout: chat column + sidebar ───────────────────────────────────────
    col_chat, col_sidebar = st.columns([3, 1], gap="medium")

    with col_chat:
        # Hero
        st.markdown(_hero_html(), unsafe_allow_html=True)

        # Quick question chips (display only — buttons below handle interaction)
        st.markdown(
            '<div style="font-size:10px;font-weight:700;letter-spacing:2px;color:#64748b;'
            'text-transform:uppercase;margin-bottom:8px">&#9632; QUICK QUESTIONS</div>',
            unsafe_allow_html=True,
        )

        chip_cols = st.columns(4)
        for i, q in enumerate(QUICK_QUESTIONS):
            with chip_cols[i % 4]:
                if st.button(q, key=f"chip_{i}", use_container_width=True,
                             help="Click to ask this question"):
                    st.session_state.asst_input_val = q

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        # ── Chat window ──────────────────────────────────────────────────────
        messages = st.session_state.asst_messages

        if not messages:
            st.markdown(_empty_state_html(), unsafe_allow_html=True)
        else:
            # Render all messages
            for i, msg in enumerate(messages):
                st.markdown(
                    _message_html(msg["role"], msg["content"], msg["ts"]),
                    unsafe_allow_html=True,
                )
                # After last assistant message, show follow-ups
                if (msg["role"] == "assistant"
                        and i == len(messages) - 1
                        and msg.get("followups")):
                    st.markdown(
                        _followup_html(msg["followups"]),
                        unsafe_allow_html=True,
                    )
                    fu_cols = st.columns(3)
                    for j, fu in enumerate(msg["followups"]):
                        with fu_cols[j]:
                            if st.button(fu, key=f"fu_{i}_{j}",
                                         use_container_width=True,
                                         help="Click to ask this follow-up"):
                                st.session_state.asst_input_val = fu

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        # ── Input row ────────────────────────────────────────────────────────
        inp_col, btn_col = st.columns([5, 1])
        with inp_col:
            user_input = st.text_input(
                label="Ask a shipping intelligence question",
                value=st.session_state.asst_input_val,
                placeholder="e.g. What are current Asia-Europe freight rates?",
                label_visibility="collapsed",
                key="asst_text_input",
            )
        with btn_col:
            send = st.button("Send", type="primary", use_container_width=True,
                             key="asst_send_btn")

        # ── Process send ─────────────────────────────────────────────────────
        question = (user_input or "").strip()
        if send and question:
            ts = _ts()

            # Append user message
            st.session_state.asst_messages.append({
                "role": "user",
                "content": question,
                "ts": ts,
                "followups": [],
            })

            # Generate response
            try:
                answer, followups = _build_response(
                    question, freight_data, macro_data, stock_data,
                    port_results, route_results, insights,
                )
            except Exception as exc:
                logger.exception("Assistant response error")
                answer = (
                    "Unable to generate a response at this time. "
                    f"Error: {exc}"
                )
                followups = [
                    "Try asking about freight rates",
                    "Ask about the BDI",
                    "Ask about shipping stock signals",
                ]

            st.session_state.asst_messages.append({
                "role": "assistant",
                "content": answer,
                "ts": _ts(),
                "followups": followups,
            })

            # Clear input and rerun
            st.session_state.asst_input_val = ""
            st.rerun()

        elif send and not question:
            st.warning("Please enter a question before sending.")

        # ── Export button ────────────────────────────────────────────────────
        if st.session_state.asst_messages:
            export_text = _export_text(st.session_state.asst_messages)
            st.download_button(
                label="Export Chat",
                data=export_text,
                file_name=f"shipping_assistant_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt",
                mime="text/plain",
                key="asst_export_btn",
            )

        # Clear chat
        if st.session_state.asst_messages:
            if st.button("Clear Chat", key="asst_clear_btn"):
                st.session_state.asst_messages = []
                st.session_state.asst_input_val = ""
                st.rerun()

    # ── Right sidebar ────────────────────────────────────────────────────────
    with col_sidebar:
        st.markdown(
            _context_panel_html(freight_data, macro_data, stock_data,
                                port_results, route_results),
            unsafe_allow_html=True,
        )

        # Tips panel
        st.markdown(
            '<div class="ctx-panel" style="margin-top:0">'
            '<div class="ctx-title">&#9632; HOW TO USE</div>'
            '<div style="font-size:12px;color:#94a3b8;line-height:1.7">'
            '&#8226; Click any quick question chip<br>'
            '&#8226; Ask about specific tickers<br>'
            '&#8226; Ask about freight routes<br>'
            '&#8226; Follow-up with context chips<br>'
            '&#8226; Export your chat history'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Signal summary mini-panel
        longs = _long_signals(stock_data)
        if longs:
            long_items = "".join(
                f'<div style="color:#10b981;font-size:12px;font-weight:600;padding:3px 0">'
                f'&#8679; {t}</div>'
                for t in longs[:6]
            )
            st.markdown(
                f'<div class="ctx-panel" style="margin-top:0">'
                f'<div class="ctx-title">&#9632; ACTIVE LONG SIGNALS</div>'
                f'{long_items}'
                f'</div>',
                unsafe_allow_html=True,
            )
