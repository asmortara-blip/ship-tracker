"""
Shipping News Intelligence Center tab.

Five sections:
  1. Breaking News Ticker   — CSS ticker tape with live or hardcoded headlines
  2. News Feed              — 2x4 grid of styled news cards with sentiment borders
  3. Sentiment Overview     — Gauge, distribution bar chart, entity word cloud
  4. Entity Mention Tracker — Sorted table of port/route mentions with sentiment
  5. Event Calendar         — Timeline of upcoming 2026-2027 shipping events
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Colour palette ────────────────────────────────────────────────────────────

C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_BORDER = "rgba(255,255,255,0.08)"
C_TEXT   = "#f1f5f9"
C_TEXT2  = "#94a3b8"
C_TEXT3  = "#64748b"
C_HIGH   = "#10b981"   # green / bullish
C_LOW    = "#ef4444"   # red   / bearish
C_NEUT   = "#64748b"   # slate / neutral
C_ACCENT = "#3b82f6"
C_CONV   = "#8b5cf6"
C_WARN   = "#f59e0b"

# ── Hardcoded fallback headlines (2026) ───────────────────────────────────────

_TICKER_HEADLINES = [
    "Maersk reports 40% surge in Trans-Pacific bookings ahead of Q2 peak season",
    "Red Sea situation: carriers rerouting adds $300M weekly to industry costs",
    "Panama Canal water levels improve, restrictions expected to ease by April",
    "COSCO orders 12 ultra-large 24,000 TEU vessels from CSSC shipyard",
    "ZIM announces new premium Trans-Pacific service with 14-day transit",
    "Port of Rotterdam handles record 15.3M TEU in 2025",
    "IMO carbon regulations driving $200B fleet investment wave through 2030",
    "Asia-Europe spot rates stabilize at $2,800/FEU after Red Sea rerouting premium",
]

# ── Hardcoded fallback news cards ─────────────────────────────────────────────

_FALLBACK_ARTICLES = [
    {
        "title": "Maersk reports 40% surge in Trans-Pacific bookings ahead of Q2 peak season",
        "url": "#",
        "source": "TradeWinds",
        "published_dt": datetime(2026, 3, 18, 8, 0, tzinfo=timezone.utc),
        "summary": (
            "A.P. Moller-Maersk has reported a significant 40 percent increase in "
            "Trans-Pacific bookings as shippers rush to lock in capacity ahead of the "
            "traditional Q2 peak season, driven by pre-tariff inventory building."
        ),
        "sentiment_score": 0.35,
        "sentiment_label": "BULLISH",
        "entities": ["Trans-Pacific", "Shanghai", "Los Angeles"],
        "relevance_score": 0.92,
    },
    {
        "title": "Red Sea situation: carriers rerouting adds $300M weekly to industry costs",
        "url": "#",
        "source": "Lloyd's List",
        "published_dt": datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
        "summary": (
            "Ongoing Houthi attacks in the Red Sea continue to force major container lines "
            "to divert around the Cape of Good Hope, adding an estimated $300 million per "
            "week to industry operating costs amid the extended disruption."
        ),
        "sentiment_score": -0.30,
        "sentiment_label": "BEARISH",
        "entities": ["Asia-Europe", "Rotterdam", "Jebel Ali (Dubai)"],
        "relevance_score": 0.90,
    },
    {
        "title": "Panama Canal water levels improve, restrictions expected to ease by April",
        "url": "#",
        "source": "Splash247",
        "published_dt": datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
        "summary": (
            "Panama Canal Authority officials confirm that Gatun Lake levels have risen "
            "following seasonal rains, and current draft restrictions of 44 feet are "
            "expected to be lifted by early April, easing Trans-Pacific transit times."
        ),
        "sentiment_score": 0.20,
        "sentiment_label": "BULLISH",
        "entities": ["Trans-Pacific", "Los Angeles", "Long Beach"],
        "relevance_score": 0.88,
    },
    {
        "title": "COSCO orders 12 ultra-large 24,000 TEU vessels from CSSC shipyard",
        "url": "#",
        "source": "gCaptain",
        "published_dt": datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc),
        "summary": (
            "COSCO Shipping Holdings has placed an order for twelve 24,000 TEU "
            "ultra-large container vessels at CSSC's Hudong-Zhonghua shipyard in a deal "
            "valued at approximately $2.4 billion, delivering 2028-2030."
        ),
        "sentiment_score": 0.25,
        "sentiment_label": "BULLISH",
        "entities": ["Shanghai", "Asia-Europe"],
        "relevance_score": 0.85,
    },
    {
        "title": "ZIM announces new premium Trans-Pacific service with 14-day transit",
        "url": "#",
        "source": "Maritime Executive",
        "published_dt": datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
        "summary": (
            "ZIM Integrated Shipping Services has launched ZIM Swift Pacific, a premium "
            "express Trans-Pacific service offering a 14-day transit from Shanghai to "
            "Los Angeles targeting high-value time-sensitive cargo segments."
        ),
        "sentiment_score": 0.30,
        "sentiment_label": "BULLISH",
        "entities": ["Trans-Pacific", "Shanghai", "Los Angeles"],
        "relevance_score": 0.83,
    },
    {
        "title": "Port of Rotterdam handles record 15.3M TEU in 2025",
        "url": "#",
        "source": "Port Technology",
        "published_dt": datetime(2026, 3, 13, 8, 30, tzinfo=timezone.utc),
        "summary": (
            "The Port of Rotterdam has published its 2025 annual throughput figures, "
            "recording a new all-time high of 15.3 million TEU, surpassing the previous "
            "record set in 2021, driven by strong import demand and transshipment growth."
        ),
        "sentiment_score": 0.40,
        "sentiment_label": "BULLISH",
        "entities": ["Rotterdam", "Antwerp-Bruges", "Asia-Europe"],
        "relevance_score": 0.82,
    },
    {
        "title": "IMO carbon regulations driving $200B fleet investment wave through 2030",
        "url": "#",
        "source": "Hellenic Shipping News",
        "published_dt": datetime(2026, 3, 12, 11, 0, tzinfo=timezone.utc),
        "summary": (
            "The IMO's Carbon Intensity Indicator framework and forthcoming 2027 "
            "emissions levies are prompting carriers to commit to an estimated $200 billion "
            "in fleet renewal, methanol dual-fuel retrofits and LNG newbuildings through 2030."
        ),
        "sentiment_score": -0.10,
        "sentiment_label": "NEUTRAL",
        "entities": ["Rotterdam", "Singapore"],
        "relevance_score": 0.79,
    },
    {
        "title": "Asia-Europe spot rates stabilize at $2,800/FEU after Red Sea rerouting premium",
        "url": "#",
        "source": "JOC",
        "published_dt": datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc),
        "summary": (
            "Asia-Europe spot container rates have found a floor around $2,800 per FEU "
            "as the market digests the structural cost uplift from Cape of Good Hope "
            "diversions, with Drewry's WCI Shanghai-Rotterdam leg stabilizing after weeks "
            "of volatility."
        ),
        "sentiment_score": 0.05,
        "sentiment_label": "NEUTRAL",
        "entities": ["Asia-Europe", "Rotterdam", "Shanghai"],
        "relevance_score": 0.87,
    },
]

# ── Upcoming event calendar (2026-2027) ───────────────────────────────────────

_EVENTS = [
    {
        "date": "April 2026",
        "sort_key": datetime(2026, 4, 1),
        "title": "Trans-Pacific Peak Season Booking Window Opens",
        "description": (
            "Shippers begin locking in peak season capacity for Q3 "
            "Trans-Pacific eastbound sailings. Expect rate negotiations "
            "and early allocation pressure."
        ),
        "type": "COMMERCIAL",
        "icon": "📦",
    },
    {
        "date": "May 2026",
        "sort_key": datetime(2026, 5, 1),
        "title": "IMO Marine Environment Protection Committee Meeting",
        "description": (
            "MEPC 84 convenes to review CII implementation progress, "
            "consider amendments to MARPOL Annex VI, and discuss the "
            "2027 emissions levy framework timeline."
        ),
        "type": "REGULATORY",
        "icon": "⚖️",
    },
    {
        "date": "June 2026",
        "sort_key": datetime(2026, 6, 1),
        "title": "Chinese New Year 2027 Booking Window Opens",
        "description": (
            "Six months before Chinese New Year 2027 (late January), "
            "shippers begin booking pre-holiday inventory shipments. "
            "Capacity on Asia-Europe and Trans-Pacific fills quickly."
        ),
        "type": "SEASONAL",
        "icon": "🧧",
    },
    {
        "date": "July 2026",
        "sort_key": datetime(2026, 7, 1),
        "title": "Traditional Peak Season Begins",
        "description": (
            "The container shipping peak season officially starts. "
            "Back-to-school and pre-holiday merchandise floods westbound "
            "vessels from Asia. Spot rates typically see strongest "
            "seasonal uplift in this window."
        ),
        "type": "SEASONAL",
        "icon": "🚀",
    },
    {
        "date": "August 2026",
        "sort_key": datetime(2026, 8, 1),
        "title": "Back-to-School Shipping Peak (US Retail)",
        "description": (
            "US retail back-to-school merchandise arrives at West Coast "
            "ports. Trans-Pacific eastbound volumes peak, congestion risk "
            "rises at Los Angeles and Long Beach."
        ),
        "type": "SEASONAL",
        "icon": "🏫",
    },
    {
        "date": "September 2026",
        "sort_key": datetime(2026, 9, 1),
        "title": "Pre-Holiday Inventory Stocking Peak",
        "description": (
            "Retailers complete final holiday inventory builds. "
            "Highest single-month TEU volumes on Trans-Pacific and "
            "Transatlantic lanes. Spot rates typically at annual highs."
        ),
        "type": "SEASONAL",
        "icon": "🎄",
    },
    {
        "date": "October 2026",
        "sort_key": datetime(2026, 10, 1),
        "title": "Traditional Peak Season Ends — Rates Normalize",
        "description": (
            "As holiday cargo has shipped, demand softens sharply. "
            "Blank sailings increase, spot rates begin seasonal decline. "
            "Contract rate negotiations for 2027 begin in parallel."
        ),
        "type": "COMMERCIAL",
        "icon": "📉",
    },
    {
        "date": "February 2027",
        "sort_key": datetime(2027, 2, 1),
        "title": "Chinese New Year — Shipping Slowdown",
        "description": (
            "Chinese factories close for 2-4 weeks. Blank sailings surge "
            "on Asia-Europe and Trans-Pacific. Carriers reduce capacity; "
            "rates often see a brief spike on remaining sailings, followed "
            "by a post-CNY demand recovery in March."
        ),
        "type": "SEASONAL",
        "icon": "🏮",
    },
]

_EVENT_TYPE_COLORS = {
    "COMMERCIAL":  C_ACCENT,
    "REGULATORY":  C_CONV,
    "SEASONAL":    C_WARN,
}

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sentiment_color(label: str) -> str:
    if label == "BULLISH":
        return C_HIGH
    if label == "BEARISH":
        return C_LOW
    return C_NEUT


def _age_str(published_dt: datetime) -> str:
    """Return human-readable age string from a datetime."""
    now = datetime.now(tz=timezone.utc)
    pub = published_dt
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    delta = now - pub
    total_seconds = int(delta.total_seconds())
    if total_seconds < 3600:
        mins = max(1, total_seconds // 60)
        return str(mins) + "m ago"
    if total_seconds < 86400:
        hrs = total_seconds // 3600
        return str(hrs) + "h ago"
    days = total_seconds // 86400
    return str(days) + "d ago"


def _source_initials(source: str) -> str:
    """Return up to 2 initials from a source name."""
    parts = source.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return source[:2].upper()


def _source_color(source: str) -> str:
    """Deterministic colour from source name for avatar circle."""
    _palette = [
        "#3b82f6", "#8b5cf6", "#06b6d4", "#f59e0b",
        "#10b981", "#f97316", "#ec4899", "#64748b",
    ]
    idx = sum(ord(c) for c in source) % len(_palette)
    return _palette[idx]


# ── Section 1: Breaking News Ticker ──────────────────────────────────────────


def _build_ticker_html(articles: list[Any]) -> str:
    """Build a CSS ticker-tape HTML string for news headlines."""
    if articles:
        headlines = []
        for a in articles[:16]:
            if hasattr(a, "title"):
                headlines.append(a.title)
            elif isinstance(a, dict):
                headlines.append(a.get("title", ""))
    else:
        headlines = list(_TICKER_HEADLINES)

    def _item_html(headline: str, idx: int) -> str:
        # Alternate bullet colours for visual variety
        colours = [C_ACCENT, C_HIGH, C_WARN, C_CONV]
        bullet_color = colours[idx % len(colours)]
        safe = headline.replace("<", "&lt;").replace(">", "&gt;")
        return (
            '<span style="display:inline-flex; align-items:center; gap:10px;'
            ' padding:0 28px; white-space:nowrap; font-size:0.83rem;">'
            '<span style="color:' + bullet_color + '; font-size:0.65rem;">&#9632;</span>'
            '<span style="color:' + C_TEXT + '; font-weight:500;">' + safe + '</span>'
            '<span style="color:' + C_TEXT3 + '; padding:0 4px;">|</span>'
            '</span>'
        )

    items_html = "".join(_item_html(h, i) for i, h in enumerate(headlines))
    ticker_content = items_html + items_html  # duplicate for seamless loop
    duration = max(20, len(headlines) * 5)
    duration_str = str(duration)

    return (
        '<div style="overflow:hidden; background:rgba(17,24,39,0.9);'
        ' border:1px solid rgba(255,255,255,0.07); border-radius:8px;'
        ' padding:10px 0; width:100%; margin-bottom:4px;">'
        '<div style="display:inline-flex;'
        ' animation:ticker-scroll ' + duration_str + 's linear infinite;">'
        + ticker_content
        + '</div></div>'
    )


def _render_ticker(articles: list[Any]) -> None:
    col_label, col_ticker = st.columns([1, 11])
    with col_label:
        st.markdown(
            '<div style="height:38px; display:flex; align-items:center;'
            ' justify-content:center;">'
            '<span style="background:' + C_LOW + '; color:white;'
            ' font-size:0.65rem; font-weight:800; letter-spacing:0.08em;'
            ' padding:4px 8px; border-radius:4px; white-space:nowrap;">BREAKING</span>'
            '</div>',
            unsafe_allow_html=True,
        )
    with col_ticker:
        st.markdown(_build_ticker_html(articles), unsafe_allow_html=True)


# ── Section 2: News Feed Cards ────────────────────────────────────────────────


def _news_card_html(article: Any) -> str:
    """Build a news card HTML string from a NewsArticle object or dict."""
    if hasattr(article, "title"):
        title           = article.title or ""
        url             = article.url or ""
        source          = article.source or ""
        pub_dt          = article.published_dt
        summary         = getattr(article, "summary", "") or ""
        sentiment_score = article.sentiment_score if article.sentiment_score is not None else 0.0
        sentiment_label = article.sentiment_label or "NEUTRAL"
        entities        = list(article.entities or [])
        relevance_score = getattr(article, "relevance_score", 0.5) or 0.5
    else:
        title           = article.get("title", "") or ""
        url             = article.get("url", "") or ""
        source          = article.get("source", "") or ""
        pub_dt          = article.get("published_dt", datetime.now(tz=timezone.utc))
        summary         = article.get("summary", "") or ""
        sentiment_score = article.get("sentiment_score") or 0.0
        sentiment_label = article.get("sentiment_label", "NEUTRAL") or "NEUTRAL"
        entities        = list(article.get("entities") or [])
        relevance_score = article.get("relevance_score", 0.5) or 0.5

    # Guard: pub_dt must be a datetime; fall back to now if missing/wrong type
    if not isinstance(pub_dt, datetime):
        try:
            pub_dt = datetime.fromisoformat(str(pub_dt))
        except Exception:
            pub_dt = datetime.now(tz=timezone.utc)

    border_color = _sentiment_color(sentiment_label)
    initials     = _source_initials(source) if source else "??"
    avatar_color = _source_color(source) if source else "#64748b"
    age          = _age_str(pub_dt)

    # Truncate title to ~90 chars for 2-line display
    title_safe = title.replace("<", "&lt;").replace(">", "&gt;")
    if len(title_safe) > 90:
        title_safe = title_safe[:87] + "..."

    # Truncate summary to ~180 chars for 3-line display
    summary_safe = summary.replace("<", "&lt;").replace(">", "&gt;")
    if len(summary_safe) > 180:
        summary_safe = summary_safe[:177] + "..."

    # Sentiment badge
    badge_bg = border_color + "22"
    score_str = ("+" if sentiment_score >= 0 else "") + str(round(sentiment_score, 2))
    sentiment_badge = (
        '<span style="background:' + badge_bg + '; color:' + border_color + ';'
        ' border:1px solid ' + border_color + '44;'
        ' border-radius:999px; font-size:0.60rem; font-weight:700;'
        ' padding:2px 7px; text-transform:uppercase; letter-spacing:0.04em;'
        ' white-space:nowrap;">'
        + sentiment_label + ' ' + score_str
        + '</span>'
    )

    # Entity tags (up to 3 to keep card compact)
    entity_tags = ""
    for ent in entities[:3]:
        entity_tags += (
            '<span style="background:rgba(16,185,129,0.08);'
            ' color:' + C_HIGH + ';'
            ' border:1px solid rgba(16,185,129,0.22);'
            ' border-radius:999px; font-size:0.57rem; padding:2px 6px;'
            ' white-space:nowrap;">' + ent + '</span>'
        )
    entity_row = (
        '<div style="display:flex; flex-wrap:wrap; gap:3px; margin-top:5px;">'
        + entity_tags
        + '</div>'
    ) if entity_tags else ""

    # Headline: render as link only when URL is a real non-empty, non-placeholder URL
    _url_clean = url.strip()
    _has_url = bool(_url_clean) and _url_clean not in ("#", "javascript:void(0)")
    if _has_url:
        headline_el = (
            '<a href="' + _url_clean + '" target="_blank" style="'
            'font-size:0.84rem; font-weight:700; color:' + C_TEXT + ';'
            ' text-decoration:none; line-height:1.35; display:block;'
            ' margin-bottom:6px; overflow:hidden;'
            ' display:-webkit-box; -webkit-line-clamp:2;'
            ' -webkit-box-orient:vertical;">'
            + title_safe + '</a>'
        )
    else:
        headline_el = (
            '<div style="font-size:0.84rem; font-weight:700; color:' + C_TEXT + ';'
            ' line-height:1.35; margin-bottom:6px; overflow:hidden;'
            ' display:-webkit-box; -webkit-line-clamp:2;'
            ' -webkit-box-orient:vertical;">'
            + title_safe + '</div>'
        )

    return (
        '<div style="background:' + C_CARD + ';'
        ' border:1px solid ' + C_BORDER + ';'
        ' border-left:4px solid ' + border_color + ';'
        ' border-radius:10px; padding:14px 14px 12px 14px;'
        ' height:100%; box-sizing:border-box;">'
        # ── Header row: avatar + source + age ─────────────────────────────
        '<div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">'
        '<div style="width:26px; height:26px; border-radius:50%;'
        ' background:' + avatar_color + '; display:flex; align-items:center;'
        ' justify-content:center; font-size:0.58rem; font-weight:800;'
        ' color:white; flex-shrink:0;">' + initials + '</div>'
        '<span style="font-size:0.65rem; color:' + C_TEXT2 + ';'
        ' font-weight:600; flex:1;">' + source + '</span>'
        '<span style="font-size:0.62rem; color:' + C_TEXT3 + ';'
        ' white-space:nowrap;">' + age + '</span>'
        '</div>'
        # ── Headline (link or plain text) ─────────────────────────────────
        + headline_el
        # ── Summary ───────────────────────────────────────────────────────
        + '<div style="font-size:0.74rem; color:' + C_TEXT2 + '; line-height:1.5;'
        ' overflow:hidden; display:-webkit-box; -webkit-line-clamp:3;'
        ' -webkit-box-orient:vertical; margin-bottom:8px;">'
        + summary_safe + '</div>'
        # ── Footer: sentiment badge + entity tags ─────────────────────────
        '<div style="display:flex; flex-direction:column; gap:5px; margin-top:auto;">'
        + sentiment_badge
        + entity_row
        + '</div>'
        '</div>'
    )


def _render_news_feed(articles: list[Any], rss_failed: bool = False) -> None:
    """Render the 4-column news card grid.

    Parameters
    ----------
    articles:   Live article list (may be empty if feed returned nothing).
    rss_failed: True when the RSS/HTTP fetch itself raised an exception — shows
                a warning banner instead of silently falling back to samples.
    """
    if rss_failed:
        st.warning("📡 News feed temporarily unavailable — showing cached headlines")
        display_articles = list(_FALLBACK_ARTICLES)
    elif not articles:
        st.info("📰 No recent shipping news available — feed refreshes every 6 hours")
        display_articles = list(_FALLBACK_ARTICLES)
    else:
        display_articles = list(articles)

    # Sort by relevance descending
    def _rel(a: Any) -> float:
        if hasattr(a, "relevance_score"):
            return a.relevance_score or 0.0
        return a.get("relevance_score", 0.0) or 0.0

    display_articles = sorted(display_articles, key=_rel, reverse=True)[:8]

    # 4-column grid, 2 rows
    rows = [display_articles[:4], display_articles[4:8]]
    for row_articles in rows:
        if not row_articles:
            continue
        cols = st.columns(len(row_articles))
        for col, article in zip(cols, row_articles):
            with col:
                st.markdown(_news_card_html(article), unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


# ── CSV Export ───────────────────────────────────────────────────────────────


def _build_news_csv(articles: list[Any]) -> str:
    """Return a CSV string of news items: headline, sentiment, source, date."""
    import io, csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Headline", "Sentiment", "Score", "Source", "Published"])
    for a in articles:
        if hasattr(a, "title"):
            title   = a.title or ""
            label   = a.sentiment_label or "NEUTRAL"
            score   = a.sentiment_score if a.sentiment_score is not None else 0.0
            source  = a.source or ""
            pub_dt  = a.published_dt
        else:
            title   = a.get("title", "") or ""
            label   = a.get("sentiment_label", "NEUTRAL") or "NEUTRAL"
            score   = a.get("sentiment_score", 0.0) or 0.0
            source  = a.get("source", "") or ""
            pub_dt  = a.get("published_dt", "")
        if isinstance(pub_dt, datetime):
            pub_str = pub_dt.strftime("%Y-%m-%d %H:%M UTC")
        else:
            pub_str = str(pub_dt) if pub_dt else ""
        writer.writerow([title, label, round(score, 4), source, pub_str])
    return buf.getvalue()


# ── Section 3: Sentiment Overview ────────────────────────────────────────────


def _compute_sentiment_stats(articles: list[Any]) -> dict:
    """Compute aggregate sentiment stats from articles."""
    if not articles:
        # Derive from fallback
        articles = _FALLBACK_ARTICLES

    scores = []
    labels = []
    entity_counts: Counter = Counter()

    for a in articles:
        if hasattr(a, "sentiment_score"):
            scores.append(a.sentiment_score if a.sentiment_score is not None else 0.0)
            labels.append(a.sentiment_label or "NEUTRAL")
            entity_counts.update(a.entities or [])
        else:
            scores.append(a.get("sentiment_score") or 0.0)
            labels.append(a.get("sentiment_label") or "NEUTRAL")
            entity_counts.update(a.get("entities") or [])

    overall = sum(scores) / len(scores) if scores else 0.0
    bullish_n = sum(1 for l in labels if l == "BULLISH")
    bearish_n = sum(1 for l in labels if l == "BEARISH")
    neutral_n = sum(1 for l in labels if l == "NEUTRAL")

    return {
        "overall": overall,
        "bullish": bullish_n,
        "bearish": bearish_n,
        "neutral": neutral_n,
        "total": len(scores),
        "entity_counts": entity_counts,
    }


def _render_sentiment_gauge(overall: float) -> None:
    """Render sentiment indicator gauge."""
    score_label = "BULLISH" if overall > 0.05 else ("BEARISH" if overall < -0.05 else "NEUTRAL")
    needle_color = C_HIGH if score_label == "BULLISH" else (C_LOW if score_label == "BEARISH" else C_NEUT)

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=round(overall, 3),
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": "Overall Market Sentiment", "font": {"size": 13, "color": C_TEXT2}},
        number={
            "font": {"color": needle_color, "size": 28},
            "suffix": "",
        },
        gauge={
            "axis": {
                "range": [-1, 1],
                "tickwidth": 1,
                "tickcolor": C_TEXT3,
                "tickfont": {"color": C_TEXT3, "size": 10},
                "nticks": 5,
            },
            "bar": {"color": needle_color, "thickness": 0.22},
            "bgcolor": C_CARD,
            "borderwidth": 0,
            "steps": [
                {"range": [-1, -0.33], "color": "rgba(239,68,68,0.15)"},
                {"range": [-0.33, 0.33], "color": "rgba(100,116,139,0.12)"},
                {"range": [0.33, 1], "color": "rgba(16,185,129,0.15)"},
            ],
            "threshold": {
                "line": {"color": "white", "width": 2},
                "thickness": 0.75,
                "value": overall,
            },
        },
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor=C_BG,
        height=240,
        margin={"l": 20, "r": 20, "t": 40, "b": 10},
        font={"color": C_TEXT, "family": "Inter, sans-serif"},
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="news_sentiment_gauge")

    # Label below gauge
    lbl_color = needle_color
    st.markdown(
        '<div style="text-align:center; margin-top:-10px;">'
        '<span style="font-size:0.78rem; font-weight:700; color:' + lbl_color + ';'
        ' text-transform:uppercase; letter-spacing:0.06em;">'
        + score_label + '</span>'
        '</div>',
        unsafe_allow_html=True,
    )


def _render_sentiment_distribution(bullish: int, bearish: int, neutral: int) -> None:
    """Render a bar chart of sentiment distribution."""
    categories = ["Bullish", "Neutral", "Bearish"]
    values = [bullish, neutral, bearish]
    colors = [C_HIGH, C_NEUT, C_LOW]

    fig = go.Figure(go.Bar(
        x=categories,
        y=values,
        marker_color=colors,
        marker_line_width=0,
        text=[str(v) for v in values],
        textposition="outside",
        textfont={"color": C_TEXT2, "size": 12},
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=C_BG,
        plot_bgcolor="#111827",
        height=240,
        margin={"l": 10, "r": 10, "t": 40, "b": 20},
        font={"color": C_TEXT, "family": "Inter, sans-serif"},
        title={"text": "Sentiment Distribution", "font": {"size": 13, "color": C_TEXT2}, "x": 0.01},
        showlegend=False,
        xaxis={
            "tickfont": {"color": C_TEXT3, "size": 11},
            "gridcolor": "rgba(255,255,255,0.05)",
            "linecolor": "rgba(255,255,255,0.08)",
        },
        yaxis={
            "tickfont": {"color": C_TEXT3, "size": 11},
            "gridcolor": "rgba(255,255,255,0.05)",
            "linecolor": "rgba(255,255,255,0.08)",
            "zeroline": False,
        },
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="news_sentiment_distribution")


def _render_entity_word_cloud(entity_counts: Counter) -> None:
    """Render a weighted HTML span word cloud of top entities."""
    st.markdown(
        '<div style="font-size:0.65rem; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:8px;">'
        'Trending Entities</div>',
        unsafe_allow_html=True,
    )

    if not entity_counts:
        st.markdown(
            '<div style="color:' + C_TEXT3 + '; font-size:0.8rem;">No entity data.</div>',
            unsafe_allow_html=True,
        )
        return

    most_common = entity_counts.most_common(20)
    max_count = most_common[0][1] if most_common else 1

    cloud_spans = []
    for entity, count in most_common:
        # Scale font size between 0.70rem and 1.4rem
        ratio = count / max_count
        font_size_val = 0.70 + ratio * 0.70
        font_size = str(round(font_size_val, 2)) + "rem"
        opacity = 0.50 + ratio * 0.50
        opacity_str = str(round(opacity, 2))
        weight = "700" if ratio > 0.6 else ("600" if ratio > 0.3 else "500")

        # Colour by count tier
        if ratio > 0.66:
            color = C_HIGH
        elif ratio > 0.33:
            color = C_ACCENT
        else:
            color = C_TEXT2

        cloud_spans.append(
            '<span style="font-size:' + font_size + '; font-weight:' + weight + ';'
            ' color:' + color + '; opacity:' + opacity_str + ';'
            ' margin:3px 5px; display:inline-block;'
            ' cursor:default;">' + entity + '</span>'
        )

    cloud_html = (
        '<div style="background:' + C_CARD + '; border:1px solid ' + C_BORDER + ';'
        ' border-radius:10px; padding:16px; line-height:2; min-height:180px;">'
        + "".join(cloud_spans)
        + '</div>'
    )
    st.markdown(cloud_html, unsafe_allow_html=True)


def _render_sentiment_overview(articles: list[Any]) -> None:
    """Render the full sentiment overview section (3 columns)."""
    stats = _compute_sentiment_stats(articles)

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        _render_sentiment_gauge(stats["overall"])

    with col2:
        _render_sentiment_distribution(
            stats["bullish"], stats["bearish"], stats["neutral"]
        )

    with col3:
        _render_entity_word_cloud(stats["entity_counts"])


# ── Section 4: Entity Mention Tracker ────────────────────────────────────────


def _build_entity_table(articles: list[Any]) -> list[dict]:
    """
    Build sorted entity mention table data from article list.

    Returns list of dicts: entity, mentions, avg_sentiment, sentiment_label,
    recent_headline.
    """
    display_articles = articles if articles else _FALLBACK_ARTICLES

    entity_mentions: dict[str, list] = {}
    entity_headline: dict[str, str] = {}
    entity_headline_dt: dict[str, datetime] = {}

    for a in display_articles:
        if hasattr(a, "entities"):
            ents   = a.entities or []
            score  = a.sentiment_score if a.sentiment_score is not None else 0.0
            title  = a.title or ""
            pub_dt = a.published_dt
        else:
            ents   = a.get("entities") or []
            score  = a.get("sentiment_score") or 0.0
            title  = a.get("title", "") or ""
            pub_dt = a.get("published_dt", datetime.now(tz=timezone.utc))

        if not isinstance(pub_dt, datetime):
            try:
                pub_dt = datetime.fromisoformat(str(pub_dt))
            except Exception:
                pub_dt = datetime.now(tz=timezone.utc)

        for ent in ents:
            entity_mentions.setdefault(ent, []).append(score)
            if ent not in entity_headline_dt or pub_dt > entity_headline_dt[ent]:
                entity_headline[ent] = title
                entity_headline_dt[ent] = pub_dt

    rows = []
    for ent, scores in entity_mentions.items():
        avg = sum(scores) / len(scores)
        if avg > 0.05:
            lbl = "BULLISH"
        elif avg < -0.05:
            lbl = "BEARISH"
        else:
            lbl = "NEUTRAL"

        headline = entity_headline.get(ent, "")
        if len(headline) > 70:
            headline = headline[:67] + "..."

        rows.append({
            "entity": ent,
            "mentions": len(scores),
            "avg_sentiment": round(avg, 3),
            "sentiment_label": lbl,
            "recent_headline": headline,
        })

    rows.sort(key=lambda r: r["mentions"], reverse=True)
    return rows


def _render_entity_tracker(articles: list[Any]) -> None:
    """Render the entity mention tracker table."""
    rows = _build_entity_table(articles)

    if not rows:
        st.info("No entity data available.")
        return

    # Header row
    header_html = (
        '<div style="display:grid;'
        ' grid-template-columns:2fr 80px 100px 4fr;'
        ' gap:8px; padding:8px 14px;'
        ' background:rgba(17,24,39,0.6);'
        ' border-radius:8px 8px 0 0;'
        ' border:1px solid ' + C_BORDER + ';'
        ' border-bottom:none;">'
        '<div style="font-size:0.65rem; font-weight:700; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.07em;">Entity</div>'
        '<div style="font-size:0.65rem; font-weight:700; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.07em; text-align:center;">'
        'Mentions</div>'
        '<div style="font-size:0.65rem; font-weight:700; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.07em; text-align:center;">'
        'Sentiment</div>'
        '<div style="font-size:0.65rem; font-weight:700; color:' + C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.07em;">Recent Headline</div>'
        '</div>'
    )
    st.markdown(header_html, unsafe_allow_html=True)

    row_htmls = []
    for i, row in enumerate(rows[:20]):
        row_bg = "rgba(26,34,53,0.9)" if i % 2 == 0 else "rgba(17,24,39,0.5)"
        sent_color = _sentiment_color(row["sentiment_label"])
        score_str = ("+" if row["avg_sentiment"] >= 0 else "") + str(row["avg_sentiment"])
        border_left = "border-left:3px solid " + sent_color + ";"

        row_htmls.append(
            '<div style="display:grid;'
            ' grid-template-columns:2fr 80px 100px 4fr;'
            ' gap:8px; padding:9px 14px;'
            ' background:' + row_bg + ';'
            ' border:1px solid ' + C_BORDER + ';'
            ' border-top:none; ' + border_left + '">'
            # Entity name
            '<div style="font-size:0.80rem; font-weight:600; color:' + C_TEXT + ';'
            ' align-self:center;">' + row["entity"] + '</div>'
            # Mentions
            '<div style="font-size:0.80rem; font-weight:700; color:' + C_ACCENT + ';'
            ' text-align:center; align-self:center;">' + str(row["mentions"]) + '</div>'
            # Sentiment badge
            '<div style="text-align:center; align-self:center;">'
            '<span style="font-size:0.60rem; font-weight:700; color:' + sent_color + ';'
            ' background:' + sent_color + '18;'
            ' border:1px solid ' + sent_color + '44;'
            ' border-radius:999px; padding:2px 8px; text-transform:uppercase;">'
            + row["sentiment_label"] + ' ' + score_str
            + '</span></div>'
            # Recent headline
            '<div style="font-size:0.74rem; color:' + C_TEXT2 + ';'
            ' align-self:center; overflow:hidden; white-space:nowrap;'
            ' text-overflow:ellipsis;">' + row["recent_headline"] + '</div>'
            '</div>'
        )

    table_html = (
        '<div style="border-radius:0 0 8px 8px; overflow:hidden;">'
        + "".join(row_htmls)
        + '</div>'
    )
    st.markdown(table_html, unsafe_allow_html=True)


# ── Section 5: Event Calendar ────────────────────────────────────────────────


def _render_event_calendar() -> None:
    """Render upcoming shipping events as an annotated timeline."""
    now = datetime.now(tz=timezone.utc)

    # Split into past (recent) and upcoming
    upcoming = []
    past = []
    for evt in _EVENTS:
        sort_dt = evt["sort_key"].replace(tzinfo=timezone.utc)
        if sort_dt >= now.replace(day=1):
            upcoming.append(evt)
        else:
            past.append(evt)

    # Show upcoming first, then recent past in expander
    def _evt_card(evt: dict, is_past: bool = False) -> str:
        evt_type = evt.get("type", "SEASONAL")
        type_color = _EVENT_TYPE_COLORS.get(evt_type, C_ACCENT)
        opacity_style = "opacity:0.55;" if is_past else ""
        icon = evt.get("icon", "")

        type_badge = (
            '<span style="font-size:0.58rem; font-weight:700;'
            ' color:' + type_color + ';'
            ' background:' + type_color + '1a;'
            ' border:1px solid ' + type_color + '44;'
            ' border-radius:999px; padding:2px 7px;'
            ' text-transform:uppercase; letter-spacing:0.05em;">'
            + evt_type + '</span>'
        )

        desc_safe = evt["description"].replace("<", "&lt;").replace(">", "&gt;")

        return (
            '<div style="background:' + C_CARD + ';'
            ' border:1px solid ' + C_BORDER + ';'
            ' border-left:4px solid ' + type_color + ';'
            ' border-radius:10px; padding:14px 16px;'
            ' margin-bottom:8px; ' + opacity_style + '">'
            # Date + icon
            '<div style="display:flex; align-items:center; gap:8px;'
            ' margin-bottom:6px;">'
            '<span style="font-size:1.1rem;">' + icon + '</span>'
            '<span style="font-size:0.72rem; font-weight:700;'
            ' color:' + type_color + ';'
            ' text-transform:uppercase; letter-spacing:0.06em;">'
            + evt["date"] + '</span>'
            + type_badge
            + ('  <span style="font-size:0.62rem; color:' + C_TEXT3 + ';">PAST</span>' if is_past else '')
            + '</div>'
            # Title
            '<div style="font-size:0.88rem; font-weight:700; color:' + C_TEXT + ';'
            ' margin-bottom:5px; line-height:1.3;">'
            + evt["title"] + '</div>'
            # Description
            '<div style="font-size:0.76rem; color:' + C_TEXT2 + '; line-height:1.55;">'
            + desc_safe + '</div>'
            '</div>'
        )

    if upcoming:
        col_a, col_b = st.columns(2)
        half = (len(upcoming) + 1) // 2
        with col_a:
            for evt in upcoming[:half]:
                st.markdown(_evt_card(evt, is_past=False), unsafe_allow_html=True)
        with col_b:
            for evt in upcoming[half:]:
                st.markdown(_evt_card(evt, is_past=False), unsafe_allow_html=True)
    else:
        st.info("No upcoming events found for 2026-2027.")

    if past:
        with st.expander("Past Events (" + str(len(past)) + ")", expanded=False):
            for evt in past:
                st.markdown(_evt_card(evt, is_past=True), unsafe_allow_html=True)


# ── Section header helper ─────────────────────────────────────────────────────


def _section_header(title: str, subtitle: str = "") -> None:
    sub_html = (
        '<div style="color:' + C_TEXT2 + '; font-size:0.82rem; margin-top:2px;">'
        + subtitle + '</div>'
    ) if subtitle else ""
    st.markdown(
        '<div style="margin-bottom:14px;">'
        '<div style="font-size:1.05rem; font-weight:700; color:' + C_TEXT + ';">'
        + title + '</div>'
        + sub_html
        + '</div>',
        unsafe_allow_html=True,
    )


# ── Public render function ────────────────────────────────────────────────────


def render(
    news_articles: list[Any] | None = None,
    port_results: Any = None,
    route_results: Any = None,
    insights: Any = None,
    rss_failed: bool = False,
) -> None:
    """
    Render the Shipping News Intelligence Center tab.

    Args:
        news_articles: List of NewsArticle objects or empty list / None.
                       Fields expected: title, url, published_dt, source,
                       summary, sentiment_score, sentiment_label, entities,
                       relevance_score.
        port_results:  Optional port analysis results (unused here, reserved).
        route_results: Optional route analysis results (unused here, reserved).
        insights:      Optional pre-computed insights (unused here, reserved).
        rss_failed:    True when the RSS fetch raised an exception; shows a
                       warning banner instead of silently falling back.
    """
    logger.debug("tab_news.render() called — articles: {}", len(news_articles or []))

    articles: list[Any] = news_articles or []

    # ── Section 1: Breaking News Ticker ──────────────────────────────────────
    _render_ticker(articles)

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

    # ── Section 2: News Feed ──────────────────────────────────────────────────
    feed_col, dl_col = st.columns([9, 1])
    with feed_col:
        _section_header(
            "Shipping News Feed",
            "Latest headlines ranked by relevance — click to read full article",
        )
    with dl_col:
        # CSV download — always export (fallback articles if live feed empty)
        _export_articles = articles if articles else list(_FALLBACK_ARTICLES)
        csv_data = _build_news_csv(_export_articles)
        st.download_button(
            label="⬇ CSV",
            data=csv_data,
            file_name="shipping_news.csv",
            mime="text/csv",
            key="news_download_csv",
            help="Download news items as CSV (headline, sentiment, source, date)",
        )
    _render_news_feed(articles, rss_failed=rss_failed)

    st.divider()

    # ── Section 3: Sentiment Overview ─────────────────────────────────────────
    _section_header(
        "Market Sentiment Overview",
        "Aggregate sentiment signal derived from recent shipping news",
    )
    _render_sentiment_overview(articles)

    st.divider()

    # ── Section 4: Entity Mention Tracker ─────────────────────────────────────
    _section_header(
        "Entity Mention Tracker",
        "Ports and routes most discussed in recent news, ranked by mention frequency",
    )
    _render_entity_tracker(articles)

    st.divider()

    # ── Section 5: Event Calendar ──────────────────────────────────────────────
    _section_header(
        "Shipping Event Calendar 2026-2027",
        "Key industry events, regulatory milestones, and seasonal windows",
    )
    _render_event_calendar()
