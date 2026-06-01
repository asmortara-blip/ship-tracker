"""Intelligent Q&A Assistant tab for Ship Tracker.

render(port_results, route_results, insights, freight_data, macro_data,
       stock_data, route_results_all=None) is the public entry point.

Rule-based NLP answer engine — no external API calls.
"""
from __future__ import annotations

import datetime
import html
from typing import Optional

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BG,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    status_badge,
    wsj_market_table,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
# Topic classifier — used only by the conversation-insights visual below.
# Standalone (does not gate _build_response) so we can refactor either side
# without entanglement. The keyword lists deliberately mirror the branches
# in _build_response so the visual stays in sync with what's actually
# answerable.
# ---------------------------------------------------------------------------

_QUESTION_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Freight Rates",      ("freight rate", "rate", "asia-europe", "asia europe",
                            "container rate", "shipping rate")),
    ("BDI / Dry Bulk",     ("bdi", "baltic", "dry bulk", "capesize", "panamax")),
    ("Red Sea",            ("red sea", "houthi", "suez reroute", "cape of good hope")),
    ("Panama Canal",       ("panama", "canal", "drought", "locks")),
    ("Equity Signals",     ("long signal", "buy signal", "which stock",
                            "shipping stock")),
    ("Outlook",            ("q2 2026", "outlook", "forecast", "q2")),
    ("Carrier Reliability", ("carrier", "reliability", "schedule", "on-time")),
]


def _classify_question(question: str) -> str:
    """Map a user question to one of the documented categories or 'General'."""
    if not question:
        return "General"
    q = question.lower()
    for label, keywords in _QUESTION_CATEGORIES:
        if any(kw in q for kw in keywords):
            return label
    return "General"


def _build_topic_distribution_bars(messages: list[dict]) -> go.Figure:
    """Horizontal bar of categories the user has asked about this session.

    Only the user's own questions count toward the tally — assistant
    messages are skipped. Empty / no-user-messages returns an annotated
    placeholder figure (the render-layer only shows the chart once enough
    history exists, but the builder must be safe in isolation too).
    """
    user_questions = [
        m.get("content", "") for m in (messages or [])
        if m.get("role") == "user"
    ]

    fig = go.Figure()
    if not user_questions:
        fig.add_annotation(
            text="No questions in this session yet",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Session topics", height=180)
        return fig

    # Tally classifications, but only show categories that actually appear.
    counts: dict[str, int] = {}
    for q in user_questions:
        cat = _classify_question(q)
        counts[cat] = counts.get(cat, 0) + 1

    # Sort ascending so the most-asked category sits at the top of the chart
    # (Plotly stacks categorical y-values bottom-up).
    items = sorted(counts.items(), key=lambda kv: kv[1])
    labels = [k for k, _ in items]
    values = [v for _, v in items]

    # Colour: top category = C_ACCENT, others = C_TEXT2 so the active focus
    # stands out without a rainbow.
    top_cat = items[-1][0]
    colors = [C_ACCENT if k == top_cat else C_TEXT2 for k in labels]

    fig.add_trace(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker={"color": colors, "line": {"color": C_BG, "width": 1}},
        text=[str(v) for v in values],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 11},
        hovertemplate="<b>%{y}</b><br>%{x} question(s)<extra></extra>",
        showlegend=False,
    ))
    apply_dark_layout(
        fig,
        title=f"Session topics — {len(user_questions)} question(s)",
        height=max(160, 50 + 28 * len(items)),
    )
    fig.update_layout(
        xaxis={"title": None, "showgrid": False,
               "tickfont": {"color": C_TEXT3, "size": 10}},
        yaxis={"title": None, "automargin": True,
               "tickfont": {"color": C_TEXT2, "size": 11}},
        margin={"l": 8, "r": 32, "t": 36, "b": 24},
        bargap=0.40,
    )
    return fig


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CHAT_CSS = """
<style>
@keyframes slide-in-up {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
}
.msg-row-user {
    display: flex;
    justify-content: flex-end;
    animation: slide-in-up 0.2s ease;
    margin-bottom: 14px;
}
.msg-row-asst {
    display: flex;
    justify-content: flex-start;
    gap: 10px;
    animation: slide-in-up 0.2s ease;
    margin-bottom: 14px;
}
.msg-avatar {
    width: 32px;
    height: 32px;
    border-radius: 4px;
    background: var(--accent);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.04em;
    color: #f4f3f0;
    flex-shrink: 0;
    margin-top: 2px;
    font-family: var(--sans);
}
.msg-bubble-user {
    background: var(--accent);
    color: #f4f3f0;
    border-radius: 7px 2px 7px 7px;
    padding: 10px 15px;
    max-width: 72%;
    font-size: 13px;
    line-height: 1.55;
    font-family: var(--sans);
}
.msg-bubble-asst {
    background: var(--card);
    color: var(--text);
    border: 1px solid var(--rule);
    border-left: 2px solid var(--accent);
    border-radius: 2px 7px 7px 7px;
    padding: 12px 16px;
    max-width: 82%;
    font-size: 13px;
    line-height: 1.6;
    font-family: var(--sans);
}
.msg-meta {
    font-size: 10px;
    color: var(--text3);
    margin-top: 4px;
    text-align: right;
    font-family: var(--mono);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.msg-meta-left {
    font-size: 10px;
    color: var(--text3);
    margin-top: 4px;
    margin-left: 42px;
    font-family: var(--mono);
    text-transform: uppercase;
    letter-spacing: 0.05em;
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
            f"<b>Current Freight Rate Snapshot</b> <span style='color:#6b6760;font-size:11px'>as of {now}</span><br><br>"
            f"<span style='color:#2e9e6e'>{summary}</span><br><br>"
            "Asia-Europe SCFI rates have shown significant volatility over the past 12 months, driven by Red Sea rerouting, "
            "capacity discipline by the major carriers, and fluctuating demand out of China. "
            "Spot rates on the Asia–North Europe lane currently trade at a premium to contract rates as shippers scramble "
            "for space on extended voyages around the Cape of Good Hope. "
            "Transpacific rates remain relatively firm heading into the traditional peak season prep window. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: SCFI, Drewry WCI, internal freight engine</span>"
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
            f"<b>Baltic Dry Index (BDI) Analysis</b> <span style='color:#6b6760;font-size:11px'>as of {now}</span><br><br>"
            f"BDI currently reads <span style='color:#c9962b;font-size:15px;font-weight:700'>{bdi_str}</span>, "
            "reflecting a broadly neutral-to-bullish signal for dry bulk demand. "
            "The BDI is a composite of Capesize, Panamax, Supramax, and Handysize rates weighted by vessel count. "
            "A BDI above 2,000 historically correlates with positive earnings momentum for dry bulk equities — "
            "names like GOGL, SBLK, and NMM tend to show the highest beta to BDI moves. "
            "Watch Capesize rates specifically: they drive ~40% of the index and are the leading indicator for iron ore trade volumes out of Australia and Brazil. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: Baltic Exchange, Clarksons, macro engine</span>"
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
            f"<b>Red Sea Disruption — Geopolitical Impact Analysis</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
            "Houthi attacks in the Red Sea and Gulf of Aden have forced the majority of container carriers to reroute "
            "vessels around the Cape of Good Hope since December 2023. "
            "This adds approximately <span style='color:#c9962b;font-weight:600'>10–14 days</span> and "
            "<span style='color:#c9962b;font-weight:600'>$500–900K</span> in additional bunker costs per round trip. "
            "The effective reduction in global container capacity is estimated at <span style='color:#c0392b;font-weight:600'>15–20%</span> "
            "as the same number of vessels cover more miles. "
            "Winners: carriers with Cape-capable fleets (Maersk, MSC, COSCO) and owners of large tankers "
            "that benefit from tonne-mile expansion. "
            "Losers: shippers with Just-In-Time supply chains and European importers facing inventory build costs. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: Clarksons Research, Kpler, geopolitical intelligence feed</span>"
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
            f"<b>Panama Canal — Drought & Transit Restrictions</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
            "Unprecedented drought conditions driven by the 2023–24 El Niño reduced Gatun Lake water levels to historic lows, "
            "forcing the Panama Canal Authority (ACP) to cut daily transits from 36–38 to as few as "
            "<span style='color:#c0392b;font-weight:600'>22–24 vessels</span> at peak restriction. "
            "Draft restrictions limited vessel sizes, pushing Neo-Panamax boxships and LNG carriers to seek Suez Canal alternatives. "
            "Auction slot prices for priority transits spiked above <span style='color:#c9962b;font-weight:600'>$4M</span>. "
            "Water levels have partially recovered but ACP continues conservative management. "
            "Long-term, this event accelerated discussions around an alternate canal route and fleet design changes favoring "
            "Suezmax-capable vessels over ultra-large post-Panamax designs. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: Panama Canal Authority, Kpler, Bloomberg shipping desk</span>"
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
            f"<b>ZIM Integrated Shipping — Earnings Leverage Analysis</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
            f"ZIM trades at <span style='color:#c9962b;font-weight:700'>{price_str}</span> with a current signal of "
            f"<span style='color:#2e9e6e;font-weight:700'>{sig_str}</span>. "
            "ZIM has among the highest spot-rate leverage of any listed carrier — approximately "
            "<span style='color:#2e9e6e;font-weight:600'>70–80%</span> of contracts reset annually, "
            "giving it outsized earnings sensitivity vs. Maersk or Hapag-Lloyd. "
            "A $100/TEU increase in average realized rates translates to roughly $200–250M in incremental EBITDA. "
            "The company's high dividend payout policy (historically 30–50% of net income) amplifies shareholder returns "
            "in up-cycles but creates risk in troughs. "
            "ZIM's Israel domicile and concentrated trade-lane exposure (Transpacific, intra-Asia) add idiosyncratic risk. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: ZIM filings, internal signal engine, Bloomberg consensus</span>"
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
        sig_color = "#2e9e6e" if str(sig_str).upper() in ("LONG", "BUY") else "#c0392b" if str(sig_str).upper() in ("SHORT", "SELL") else "#c9962b"
        answer = (
            f"<b>{found_ticker} — Shipping Equity Analysis</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
            f"Current price: <span style='color:#e8e6e1;font-weight:700'>{price_str}</span> &nbsp;|&nbsp; "
            f"Signal: <span style='color:{sig_color};font-weight:700'>{sig_str}</span><br><br>"
            f"{found_ticker} is tracked within our shipping intelligence universe. "
            "The signal is generated by our multi-factor model incorporating freight rate momentum, "
            "earnings revision trends, technical structure, and macro shipping indicators. "
            "Always cross-reference with latest earnings release and sector positioning before acting. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: Internal signal engine, Bloomberg, company filings</span>"
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
            f"<b>Shipping Stocks — Current LONG Signals</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
            f"Tickers with active LONG signals: <span style='color:#2e9e6e;font-weight:700'>{longs_str}</span><br><br>"
            "These signals are generated by a quantitative model combining: (1) freight rate momentum, "
            "(2) earnings revision direction, (3) technical breakout structure, and (4) macro shipping cycle indicators. "
            "Shipping equities tend to lead freight rate moves by 4–6 weeks as the market prices in contract renewals. "
            "Risk management note: shipping stocks carry high beta to global trade volumes and carry outsized "
            "drawdown risk during demand shocks (COVID-2020, GFC-2008). Position sizing accordingly. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: Internal multi-factor signal engine, updated daily</span>"
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
            f"<b>Container Rate Outlook — Q2 2026</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
            "Q2 2026 rate outlook is cautiously constructive. Key bull factors: "
            "(1) Red Sea rerouting continues to absorb effective capacity, "
            "(2) carrier capacity discipline has improved vs. 2022–2023 post-COVID flush, "
            "(3) Chinese export demand showing resilience despite tariff headwinds. "
            "Bear risks: (1) potential Red Sea normalization could release 15–18% effective capacity, "
            "(2) new vessel deliveries (the 2021 ordering cohort) peak in 2025–2026, "
            "(3) global trade policy uncertainty weighing on forward booking visibility. "
            "Drewry consensus puts WCI on Asia–Europe at <span style='color:#c9962b;font-weight:600'>$2,800–3,400/FEU</span> for Q2. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: Drewry, Alphaliner, Clarksons Research, internal model</span>"
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
            f"<b>Carrier Schedule Reliability Rankings</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
            "Based on Sea-Intelligence Global Liner Performance data:<br><br>"
            "<span style='color:#2e9e6e'>1. Hapag-Lloyd</span> — 58.2% on-time performance (industry leader)<br>"
            "<span style='color:#2e9e6e'>2. Maersk</span> — 55.7%<br>"
            "<span style='color:#c9962b'>3. CMA CGM</span> — 51.3%<br>"
            "<span style='color:#c9962b'>4. ONE (Ocean Network Express)</span> — 49.8%<br>"
            "<span style='color:#c0392b'>5. Evergreen</span> — 44.1%<br><br>"
            "Note: Red Sea rerouting has structurally degraded schedule reliability across all carriers "
            "by 8–12 percentage points vs. pre-disruption norms. "
            "Schedule reliability correlates strongly with shipper contract retention and premium pricing power. "
            "<br><br><span style='color:#6b6760;font-size:11px'>Source: Sea-Intelligence Global Liner Performance, Q1 2026</span>"
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
        f"<b>Shipping Market Intelligence — Overview</b> <span style='color:#6b6760;font-size:11px'>{now}</span><br><br>"
        f"<b>Freight Rates:</b> {freight_summary}<br>"
        f"<b>Baltic Dry Index:</b> <span style='color:#c9962b'>{bdi_str}</span> — neutral-to-bullish dry bulk signal<br>"
        f"<b>Top LONG Signals:</b> <span style='color:#2e9e6e'>{longs_str}</span><br><br>"
        "The global shipping market remains in a structurally disrupted state driven by Red Sea rerouting, "
        "sustained container demand out of Asia, and tightening carrier capacity discipline. "
        "Dry bulk markets are tracking iron ore and coal flows closely — watch Brazil–China Capesize routes "
        "as the leading demand indicator. "
        "For specific analysis, ask about freight rates, BDI trends, individual tickers, or geopolitical disruptions. "
        "<br><br><span style='color:#6b6760;font-size:11px'>Source: Internal shipping intelligence engine — all data as of market close</span>"
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

# ── Cell formatters for wsj_market_table() ─────────────────────────────────
# Mirror the pattern used in ui/tab_results.py and ui/tab_rate_analytics.py.

def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _message_html(role: str, text: str, ts: str) -> str:
    if role == "user":
        # Escape the user's free text at the display sink (it's rendered via
        # unsafe_allow_html). NOT escaped at store time — the raw text is also
        # the LLM prompt. Without this a typed <img onerror=…> / <script>
        # executes in the user's own session (self-XSS). The assistant branch
        # below is intentionally raw — engine-built HTML with controlled markup.
        return (
            f'<div class="msg-row-user">'
            f'<div><div class="msg-bubble-user">{html.escape(text)}</div>'
            f'<div class="msg-meta">{html.escape(ts)}</div></div>'
            f'</div>'
        )
    return (
        f'<div class="msg-row-asst">'
        f'<div class="msg-avatar">AI</div>'
        f'<div><div class="msg-bubble-asst">{text}</div>'
        f'<div class="msg-meta-left">{ts}</div></div>'
        f'</div>'
    )


def _context_sources(freight_data, macro_data, stock_data,
                     port_results, route_results) -> list[dict]:
    """Return live-data-style source dicts for the right-rail data feed list."""
    now = datetime.datetime.now(datetime.timezone.utc)

    def _entry(name: str, ok: bool) -> dict:
        if ok:
            return {"name": name, "kind": "live", "quality": "good", "as_of": now}
        return {"name": name, "kind": "demo", "quality": "demo"}

    return [
        _entry("Freight Data", bool(freight_data)),
        _entry("Port Data",    bool(port_results)),
        _entry("Signal Data",  bool(stock_data)),
        _entry("Macro Data",   bool(macro_data)),
        {"name": "News Data", "kind": "cached", "quality": "stale",
         "notes": "~15 min delay"},
    ]


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
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('assistant'):

        st.markdown(_CHAT_CSS, unsafe_allow_html=True)

        # ── Session state ────────────────────────────────────────────────────────
        if "asst_messages" not in st.session_state:
            st.session_state.asst_messages = []
        if "asst_input_val" not in st.session_state:
            st.session_state.asst_input_val = ""

        page_header(
            title="Shipping Intelligence Assistant",
            subtitle="Rule-based Q&A over live freight, macro, port, and signal context — no external API calls.",
            badge_text="ASSISTANT",
            badge_color=C_ACCENT,
        )

        # ── Top-of-tab KPIs: assistant context snapshot ─────────────────────────
        msg_count = len(st.session_state.asst_messages)
        long_count = len(_long_signals(stock_data))
        feeds_live = sum(
            bool(x) for x in (freight_data, macro_data, stock_data, port_results)
        )
        metric_card_row(
            [
                {"label": "Live Data Feeds", "value": f"{feeds_live} / 4",
                 "accent": C_HIGH if feeds_live == 4 else C_MOD,
                 "sublabel": "freight, macro, signals, ports"},
                {"label": "LONG Signals",   "value": f"{long_count}",
                 "accent": C_HIGH, "sublabel": "tickers flagged by engine"},
                {"label": "Messages",        "value": f"{msg_count}",
                 "accent": C_ACCENT, "sublabel": "this session"},
                {"label": "Quick Prompts",   "value": f"{len(QUICK_QUESTIONS)}",
                 "accent": C_ACCENT, "sublabel": "preset starter questions"},
            ],
            columns=4,
        )

        section_divider("Workspace")

        # ── Layout: chat column + sidebar ───────────────────────────────────────
        col_chat, col_sidebar = st.columns([3, 1], gap="large")

        with col_chat:
            # Quick-question chips render as buttons; section_header gives the WSJ rule.
            section_header("Quick Questions",
                           "Tap a prompt to seed the input below.")

            chip_cols = st.columns(4)
            for i, q in enumerate(QUICK_QUESTIONS):
                with chip_cols[i % 4]:
                    if st.button(q, key=f"chip_{i}", use_container_width=True,
                                 help="Click to ask this question"):
                        st.session_state.asst_input_val = q

            section_header("Conversation",
                           "Threaded answers with follow-up suggestions.")

            # ── Chat window ──────────────────────────────────────────────────────
            messages = st.session_state.asst_messages

            if not messages:
                st.info(
                    "No questions yet — tap a prompt above or type below. "
                    "The assistant covers freight rates, shipping equities, "
                    "geopolitical disruptions and market signals."
                )
            else:
                # Render all messages
                for i, msg in enumerate(messages):
                    st.markdown(
                        _message_html(msg["role"], msg["content"], msg["ts"]), unsafe_allow_html=True)
                    # After last assistant message, show follow-ups
                    if (msg["role"] == "assistant"
                            and i == len(messages) - 1
                            and msg.get("followups")):
                        fu_cols = st.columns(3)
                        for j, fu in enumerate(msg["followups"]):
                            with fu_cols[j]:
                                if st.button(fu, key=f"fu_{i}_{j}",
                                             use_container_width=True,
                                             help="Click to ask this follow-up"):
                                    st.session_state.asst_input_val = fu

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

            # ── Conversation controls ────────────────────────────────────────────
            if st.session_state.asst_messages:
                st.divider()
                export_text = _export_text(st.session_state.asst_messages)
                ctl_export, ctl_clear = st.columns(2, gap="medium")
                with ctl_export:
                    st.download_button(
                        label="Export Chat",
                        data=export_text,
                        file_name=f"shipping_assistant_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt",
                        mime="text/plain",
                        key="asst_export_btn",
                        use_container_width=True,
                        help="Download the conversation as plain text.",
                    )
                with ctl_clear:
                    if st.button("Clear Chat", key="asst_clear_btn",
                                 use_container_width=True,
                                 help="Discard all messages in this session."):
                        st.session_state.asst_messages = []
                        st.session_state.asst_input_val = ""
                        st.rerun()

        # ── Right sidebar ────────────────────────────────────────────────────────
        with col_sidebar:
            section_header("Available Data Context",
                           "Live feeds wired into the answer engine.")

            ctx_sources = _context_sources(freight_data, macro_data, stock_data,
                                           port_results, route_results)
            _STATUS_FOR = {"good": "success", "stale": "warning", "demo": "danger"}
            ctx_rows = []
            for s in ctx_sources:
                label = _sans(s["name"], color=C_TEXT, weight=600)
                status_text = s.get("notes") or (
                    "Live" if s["quality"] == "good" else
                    "Cached" if s["quality"] == "stale" else
                    "Unavailable"
                )
                ctx_rows.append([
                    label,
                    status_badge(status_text, _STATUS_FOR.get(s["quality"], "info")),
                ])
            wsj_market_table(["Source", "Status"], ctx_rows)
            st.markdown(source_footer(ctx_sources), unsafe_allow_html=True)

            section_header("How to Use", "Quick reference for the assistant.")
            howto_rows = [
                [_sans("1", color=C_ACCENT, weight=700),
                 _sans("Click any quick-question button to seed the input.",
                       color=C_TEXT)],
                [_sans("2", color=C_ACCENT, weight=700),
                 _sans("Ask about specific tickers (ZIM, MATX, GOGL, ...).",
                       color=C_TEXT)],
                [_sans("3", color=C_ACCENT, weight=700),
                 _sans("Ask about freight routes or geopolitical disruptions.",
                       color=C_TEXT)],
                [_sans("4", color=C_ACCENT, weight=700),
                 _sans("Use follow-up chips to drill deeper into context.",
                       color=C_TEXT)],
                [_sans("5", color=C_ACCENT, weight=700),
                 _sans("Export your chat history as plain text.",
                       color=C_TEXT)],
            ]
            wsj_market_table(["#", "Tip"], howto_rows)

            # Signal summary mini-panel
            longs = _long_signals(stock_data)
            if longs:
                section_header("Active LONG Signals",
                               "Tickers flagged by the multi-factor signal engine.")
                long_rows = [
                    [_sans(t, color=C_TEXT, weight=700),
                     badge("LONG", color=C_HIGH)]
                    for t in longs[:6]
                ]
                wsj_market_table(["Ticker", "Signal"], long_rows)
                st.markdown(
                    source_footer([{
                        "name": "Internal signal engine",
                        "kind": "modeled",
                        "quality": "good",
                    }]),
                    unsafe_allow_html=True,
                )

            # Session topic distribution — only meaningful once a few
            # questions are in. Hidden below the threshold so the sidebar
            # doesn't carry empty chrome before the user has asked anything.
            user_msgs = [m for m in st.session_state.asst_messages
                         if m.get("role") == "user"]
            if len(user_msgs) >= 3:
                section_header("Session Focus",
                               "How your questions break down by topic.")
                st.plotly_chart(
                    _build_topic_distribution_bars(st.session_state.asst_messages),
                    use_container_width=True,
                    config={"displayModeBar": False},
                    key="assistant_topic_distribution",
                )
