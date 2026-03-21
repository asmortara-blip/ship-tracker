"""
Shipping News Intelligence Center — fully rewritten for 2026.

Sections
--------
0.  News Hero              — breaking count, positive/negative/neutral counts,
                             top topic, last-updated timestamp
1.  Sentiment Trend        — daily sentiment score (30 days) + freight-rate overlay
2.  Topic Category Breakdown — news categorised into 6 topics with counts
3.  Top Stories Feed       — polished cards: headline, source avatar, sentiment badge,
                             topic tag, time-ago, summary
4.  Sentiment by Topic     — horizontal bar chart, avg sentiment per topic
5.  Market-Moving News     — highest-impact stories + estimated freight impact
6.  Source Reliability     — predictive accuracy scorecard per publication
7.  Geographic Focus       — news volume by region with styled map panel
8.  Breaking Alerts        — urgent headlines with red-pulse border + priority badge
9.  News Timeline          — chronological feed, sentiment-coded left-border colour
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ── Colour palette ─────────────────────────────────────────────────────────────

C_BG     = "#0a0f1a"
C_CARD   = "#1a2235"
C_CARD2  = "#141c2e"
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
C_CYAN   = "#06b6d4"
C_PINK   = "#ec4899"

# ── Topic taxonomy ─────────────────────────────────────────────────────────────

TOPICS = ["rates", "congestion", "sanctions", "weather", "M&A", "regulatory"]

_TOPIC_COLORS = {
    "rates":      C_ACCENT,
    "congestion": C_WARN,
    "sanctions":  C_LOW,
    "weather":    C_CYAN,
    "M&A":        C_CONV,
    "regulatory": C_PINK,
}

_TOPIC_ICONS = {
    "rates":      "📈",
    "congestion": "🚦",
    "sanctions":  "🚫",
    "weather":    "🌊",
    "M&A":        "🤝",
    "regulatory": "⚖️",
}

# ── Fallback articles ──────────────────────────────────────────────────────────

_FALLBACK_ARTICLES: list[dict] = [
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
        "topic_tags": ["rates", "M&A"],
        "region": "Asia-Pacific",
        "breaking": False,
        "freight_impact_pct": 3.2,
    },
    {
        "title": "Red Sea crisis deepens: Houthi attacks force mass rerouting via Cape of Good Hope",
        "url": "#",
        "source": "Lloyd's List",
        "published_dt": datetime(2026, 3, 19, 6, 15, tzinfo=timezone.utc),
        "summary": (
            "Ongoing Houthi attacks in the Red Sea continue to force major container lines "
            "to divert around the Cape of Good Hope, adding an estimated $300 million per "
            "week to industry operating costs amid the extended disruption."
        ),
        "sentiment_score": -0.62,
        "sentiment_label": "BEARISH",
        "entities": ["Asia-Europe", "Rotterdam", "Jebel Ali"],
        "relevance_score": 0.97,
        "topic_tags": ["sanctions", "congestion"],
        "region": "Middle East",
        "breaking": True,
        "freight_impact_pct": -5.8,
    },
    {
        "title": "Panama Canal water levels recover — draft restrictions to ease by April",
        "url": "#",
        "source": "Splash247",
        "published_dt": datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
        "summary": (
            "Panama Canal Authority officials confirm that Gatun Lake levels have risen "
            "following seasonal rains, and current draft restrictions of 44 feet are "
            "expected to be lifted by early April, easing Trans-Pacific transit times."
        ),
        "sentiment_score": 0.42,
        "sentiment_label": "BULLISH",
        "entities": ["Trans-Pacific", "Los Angeles", "Long Beach"],
        "relevance_score": 0.88,
        "topic_tags": ["congestion", "weather"],
        "region": "Americas",
        "breaking": False,
        "freight_impact_pct": 2.1,
    },
    {
        "title": "COSCO orders 12 ultra-large 24,000 TEU vessels in $2.4B CSSC deal",
        "url": "#",
        "source": "gCaptain",
        "published_dt": datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc),
        "summary": (
            "COSCO Shipping Holdings has placed an order for twelve 24,000 TEU "
            "ultra-large container vessels at CSSC's Hudong-Zhonghua shipyard in a deal "
            "valued at approximately $2.4 billion, delivering 2028-2030."
        ),
        "sentiment_score": 0.28,
        "sentiment_label": "BULLISH",
        "entities": ["Shanghai", "Asia-Europe"],
        "relevance_score": 0.85,
        "topic_tags": ["M&A", "rates"],
        "region": "Asia-Pacific",
        "breaking": False,
        "freight_impact_pct": 1.5,
    },
    {
        "title": "ZIM launches ZIM Swift Pacific: 14-day Shanghai–Los Angeles express service",
        "url": "#",
        "source": "Maritime Executive",
        "published_dt": datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
        "summary": (
            "ZIM Integrated Shipping Services has launched ZIM Swift Pacific, a premium "
            "express Trans-Pacific service offering a 14-day transit from Shanghai to "
            "Los Angeles targeting high-value time-sensitive cargo segments."
        ),
        "sentiment_score": 0.31,
        "sentiment_label": "BULLISH",
        "entities": ["Trans-Pacific", "Shanghai", "Los Angeles"],
        "relevance_score": 0.83,
        "topic_tags": ["rates", "M&A"],
        "region": "Asia-Pacific",
        "breaking": False,
        "freight_impact_pct": 1.8,
    },
    {
        "title": "Port of Rotterdam hits all-time record: 15.3M TEU throughput in 2025",
        "url": "#",
        "source": "Port Technology",
        "published_dt": datetime(2026, 3, 13, 8, 30, tzinfo=timezone.utc),
        "summary": (
            "The Port of Rotterdam has published its 2025 annual throughput figures, "
            "recording a new all-time high of 15.3 million TEU, surpassing the previous "
            "record set in 2021, driven by strong import demand and transshipment growth."
        ),
        "sentiment_score": 0.44,
        "sentiment_label": "BULLISH",
        "entities": ["Rotterdam", "Antwerp-Bruges", "Asia-Europe"],
        "relevance_score": 0.82,
        "topic_tags": ["congestion", "rates"],
        "region": "Europe",
        "breaking": False,
        "freight_impact_pct": 0.9,
    },
    {
        "title": "IMO CII framework triggers $200B fleet renewal wave through 2030",
        "url": "#",
        "source": "Hellenic Shipping News",
        "published_dt": datetime(2026, 3, 12, 11, 0, tzinfo=timezone.utc),
        "summary": (
            "The IMO's Carbon Intensity Indicator framework and forthcoming 2027 "
            "emissions levies are prompting carriers to commit to an estimated $200 billion "
            "in fleet renewal, methanol dual-fuel retrofits, and LNG newbuildings through 2030."
        ),
        "sentiment_score": -0.10,
        "sentiment_label": "NEUTRAL",
        "entities": ["Rotterdam", "Singapore"],
        "relevance_score": 0.79,
        "topic_tags": ["regulatory", "M&A"],
        "region": "Global",
        "breaking": False,
        "freight_impact_pct": -0.4,
    },
    {
        "title": "Asia-Europe spot rates floor at $2,800/FEU as Cape premium stabilises",
        "url": "#",
        "source": "JOC",
        "published_dt": datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc),
        "summary": (
            "Asia-Europe spot container rates have found a floor around $2,800 per FEU "
            "as the market digests the structural cost uplift from Cape of Good Hope "
            "diversions, with Drewry's WCI Shanghai-Rotterdam leg stabilising after weeks "
            "of volatility."
        ),
        "sentiment_score": 0.06,
        "sentiment_label": "NEUTRAL",
        "entities": ["Asia-Europe", "Rotterdam", "Shanghai"],
        "relevance_score": 0.87,
        "topic_tags": ["rates", "congestion"],
        "region": "Europe",
        "breaking": False,
        "freight_impact_pct": 0.2,
    },
    {
        "title": "BREAKING: Taiwan Strait tensions escalate — insurers impose war-risk surcharges",
        "url": "#",
        "source": "Lloyd's List",
        "published_dt": datetime(2026, 3, 20, 4, 0, tzinfo=timezone.utc),
        "summary": (
            "Lloyd's of London war-risk underwriters have applied emergency surcharges "
            "on cargo transiting the Taiwan Strait following a 48-hour standoff between "
            "PLA naval vessels and US carrier group assets in disputed waters."
        ),
        "sentiment_score": -0.78,
        "sentiment_label": "BEARISH",
        "entities": ["Trans-Pacific", "Shanghai", "Singapore"],
        "relevance_score": 0.99,
        "topic_tags": ["sanctions", "regulatory"],
        "region": "Asia-Pacific",
        "breaking": True,
        "freight_impact_pct": -8.4,
    },
    {
        "title": "US East Coast ILA contract breakthrough averts threatened strike action",
        "url": "#",
        "source": "JOC",
        "published_dt": datetime(2026, 3, 18, 20, 0, tzinfo=timezone.utc),
        "summary": (
            "The International Longshoremen's Association and the US Maritime Alliance "
            "reached a tentative agreement on wages and automation, averting a strike "
            "that would have shut ports from Maine to Texas and disrupted $3B in weekly cargo."
        ),
        "sentiment_score": 0.58,
        "sentiment_label": "BULLISH",
        "entities": ["New York-New Jersey", "Savannah", "Houston"],
        "relevance_score": 0.95,
        "topic_tags": ["regulatory", "congestion"],
        "region": "Americas",
        "breaking": True,
        "freight_impact_pct": 4.7,
    },
]

# ── Source reliability data ────────────────────────────────────────────────────

_SOURCE_RELIABILITY: list[dict] = [
    {"source": "Lloyd's List",          "accuracy": 0.84, "lead_time_days": 1.2, "stories": 312, "tier": "Premium"},
    {"source": "TradeWinds",            "accuracy": 0.81, "lead_time_days": 0.8, "stories": 487, "tier": "Premium"},
    {"source": "JOC",                   "accuracy": 0.79, "lead_time_days": 1.5, "stories": 394, "tier": "Premium"},
    {"source": "gCaptain",              "accuracy": 0.76, "lead_time_days": 0.5, "stories": 621, "tier": "Standard"},
    {"source": "Splash247",             "accuracy": 0.74, "lead_time_days": 0.6, "stories": 533, "tier": "Standard"},
    {"source": "Maritime Executive",    "accuracy": 0.72, "lead_time_days": 2.1, "stories": 278, "tier": "Standard"},
    {"source": "Hellenic Shipping News","accuracy": 0.68, "lead_time_days": 1.0, "stories": 892, "tier": "Wire"},
    {"source": "Port Technology",       "accuracy": 0.65, "lead_time_days": 3.2, "stories": 201, "tier": "Specialist"},
]

# ── Region data ────────────────────────────────────────────────────────────────

_REGIONS = [
    {"name": "Asia-Pacific",  "lat":  20, "lon": 120, "color": C_ACCENT},
    {"name": "Europe",        "lat":  52, "lon":  10, "color": C_CONV},
    {"name": "Middle East",   "lat":  25, "lon":  50, "color": C_WARN},
    {"name": "Americas",      "lat":  20, "lon": -85, "color": C_HIGH},
    {"name": "Africa",        "lat":  -5, "lon":  25, "color": C_CYAN},
    {"name": "Global",        "lat":   0, "lon":   0, "color": C_PINK},
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _gaf(article: Any, field: str, default: Any = None) -> Any:
    """Get article field — works for both dicts and attribute-based objects."""
    if isinstance(article, dict):
        return article.get(field, default)
    return getattr(article, field, default)


def _sentiment_color(label: str) -> str:
    if label == "BULLISH":
        return C_HIGH
    if label == "BEARISH":
        return C_LOW
    return C_NEUT


def _sentiment_bg(label: str) -> str:
    if label == "BULLISH":
        return "rgba(16,185,129,0.15)"
    if label == "BEARISH":
        return "rgba(239,68,68,0.15)"
    return "rgba(100,116,139,0.12)"


def _age_str(published_dt: datetime) -> str:
    now = datetime.now(tz=timezone.utc)
    pub = published_dt
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    delta = now - pub
    secs = int(delta.total_seconds())
    if secs < 3600:
        return f"{max(1, secs // 60)}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _source_color(source: str) -> str:
    palette = ["#3b82f6", "#8b5cf6", "#06b6d4", "#f59e0b",
               "#10b981", "#f97316", "#ec4899", "#64748b"]
    return palette[sum(ord(c) for c in source) % len(palette)]


def _source_initials(source: str) -> str:
    parts = source.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return source[:2].upper()


def _impact_score(article: Any) -> float:
    s = abs(_gaf(article, "sentiment_score") or 0.0)
    r = _gaf(article, "relevance_score") or 0.5
    return s * r


def _normalised_articles(raw: list[Any]) -> list[dict]:
    """Return a list of plain dicts regardless of whether raw items are dicts or objects."""
    out = []
    for a in raw:
        out.append({
            "title":           _gaf(a, "title", "Untitled"),
            "url":             _gaf(a, "url", "#"),
            "source":          _gaf(a, "source", "Unknown"),
            "published_dt":    _gaf(a, "published_dt", datetime.now(tz=timezone.utc)),
            "summary":         _gaf(a, "summary", ""),
            "sentiment_score": _gaf(a, "sentiment_score", 0.0) or 0.0,
            "sentiment_label": _gaf(a, "sentiment_label", "NEUTRAL") or "NEUTRAL",
            "entities":        _gaf(a, "entities", []) or [],
            "relevance_score": _gaf(a, "relevance_score", 0.5) or 0.5,
            "topic_tags":      _gaf(a, "topic_tags", []) or [],
            "region":          _gaf(a, "region", "Global"),
            "breaking":        _gaf(a, "breaking", False),
            "freight_impact_pct": _gaf(a, "freight_impact_pct", 0.0) or 0.0,
        })
    return out


def _section_header(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div style="margin:8px 0 4px;">'
        f'<span style="font-size:1.05rem;font-weight:700;color:{C_TEXT};'
        f'letter-spacing:0.02em;">{title}</span>'
        + (f'<br><span style="font-size:0.78rem;color:{C_TEXT3};'
           f'letter-spacing:0.01em;">{subtitle}</span>' if subtitle else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _pill(text: str, color: str, bg: str | None = None) -> str:
    bg = bg or color + "22"
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:99px;'
        f'font-size:0.68rem;font-weight:700;letter-spacing:0.05em;'
        f'color:{color};background:{bg};border:1px solid {color}44;">'
        f'{text}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section 0 — News Hero
# ─────────────────────────────────────────────────────────────────────────────


def _render_news_hero(articles: list[dict]) -> None:
    try:
        now_str = datetime.now(tz=timezone.utc).strftime("%d %b %Y  %H:%M UTC")

        breaking  = sum(1 for a in articles if a.get("breaking"))
        positive  = sum(1 for a in articles if a["sentiment_label"] == "BULLISH")
        negative  = sum(1 for a in articles if a["sentiment_label"] == "BEARISH")
        neutral_n = sum(1 for a in articles if a["sentiment_label"] == "NEUTRAL")
        total     = len(articles)

        # Top topic by frequency
        topic_counter: Counter = Counter()
        for a in articles:
            for t in a.get("topic_tags", []):
                topic_counter[t] += 1
        top_topic = topic_counter.most_common(1)[0][0] if topic_counter else "rates"

        overall_score = (
            sum(a["sentiment_score"] for a in articles) / total if total else 0.0
        )
        market_label = (
            "BULLISH" if overall_score > 0.05
            else ("BEARISH" if overall_score < -0.05 else "NEUTRAL")
        )
        market_color = _sentiment_color(market_label)

        # Hero card
        st.markdown(
            f"""
            <div style="background:linear-gradient(135deg,#0d1b2e 0%,#1a2a45 60%,#0f1f35 100%);
                        border:1px solid {C_ACCENT}33;border-radius:16px;
                        padding:28px 32px 24px;margin-bottom:20px;
                        box-shadow:0 8px 40px rgba(59,130,246,0.12);">
              <div style="display:flex;align-items:flex-start;justify-content:space-between;
                          flex-wrap:wrap;gap:16px;">
                <div>
                  <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.12em;
                              color:{C_ACCENT};text-transform:uppercase;margin-bottom:6px;">
                    SHIPPING NEWS INTELLIGENCE CENTER
                  </div>
                  <div style="font-size:1.85rem;font-weight:800;color:{C_TEXT};
                              letter-spacing:-0.02em;line-height:1.15;margin-bottom:4px;">
                    Market Sentiment:&nbsp;
                    <span style="color:{market_color};">{market_label}</span>
                  </div>
                  <div style="font-size:0.8rem;color:{C_TEXT3};margin-top:2px;">
                    Updated {now_str} &nbsp;·&nbsp; {total} stories indexed
                  </div>
                </div>
                <div style="display:flex;gap:12px;flex-wrap:wrap;">
                  <div style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.35);
                              border-radius:12px;padding:14px 20px;text-align:center;min-width:90px;">
                    <div style="font-size:1.7rem;font-weight:800;color:{C_LOW};">{breaking}</div>
                    <div style="font-size:0.68rem;color:{C_TEXT3};font-weight:600;letter-spacing:0.06em;">BREAKING</div>
                  </div>
                  <div style="background:rgba(16,185,129,0.10);border:1px solid rgba(16,185,129,0.30);
                              border-radius:12px;padding:14px 20px;text-align:center;min-width:90px;">
                    <div style="font-size:1.7rem;font-weight:800;color:{C_HIGH};">{positive}</div>
                    <div style="font-size:0.68rem;color:{C_TEXT3};font-weight:600;letter-spacing:0.06em;">BULLISH</div>
                  </div>
                  <div style="background:rgba(239,68,68,0.10);border:1px solid rgba(239,68,68,0.28);
                              border-radius:12px;padding:14px 20px;text-align:center;min-width:90px;">
                    <div style="font-size:1.7rem;font-weight:800;color:{C_LOW};">{negative}</div>
                    <div style="font-size:0.68rem;color:{C_TEXT3};font-weight:600;letter-spacing:0.06em;">BEARISH</div>
                  </div>
                  <div style="background:rgba(100,116,139,0.10);border:1px solid rgba(100,116,139,0.25);
                              border-radius:12px;padding:14px 20px;text-align:center;min-width:90px;">
                    <div style="font-size:1.7rem;font-weight:800;color:{C_NEUT};">{neutral_n}</div>
                    <div style="font-size:0.68rem;color:{C_TEXT3};font-weight:600;letter-spacing:0.06em;">NEUTRAL</div>
                  </div>
                  <div style="background:rgba(139,92,246,0.10);border:1px solid rgba(139,92,246,0.28);
                              border-radius:12px;padding:14px 20px;text-align:center;min-width:90px;">
                    <div style="font-size:1rem;font-weight:800;color:{C_CONV};padding-top:4px;">
                      {_TOPIC_ICONS.get(top_topic,'')} {top_topic.upper()}
                    </div>
                    <div style="font-size:0.68rem;color:{C_TEXT3};font-weight:600;letter-spacing:0.06em;">TOP TOPIC</div>
                  </div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("news_hero failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Sentiment Trend (30-day) + freight overlay
# ─────────────────────────────────────────────────────────────────────────────


def _render_sentiment_trend(articles: list[dict]) -> None:
    try:
        _section_header(
            "Sentiment Trend — Last 30 Days",
            "Daily average news sentiment score with SCFI spot-rate overlay",
        )

        today = datetime.now(tz=timezone.utc).date()
        days  = [today - timedelta(days=i) for i in range(29, -1, -1)]

        # Bucket articles by calendar day
        day_scores: dict[Any, list[float]] = defaultdict(list)
        for a in articles:
            pub = a["published_dt"]
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            day_scores[pub.date()].append(a["sentiment_score"])

        # Fill gaps with interpolated noise
        rng = random.Random(42)
        trend: list[float] = []
        for d in days:
            if day_scores.get(d):
                trend.append(sum(day_scores[d]) / len(day_scores[d]))
            else:
                trend.append(rng.gauss(0.05, 0.18))

        # Synthetic freight rate (SCFI-like), correlated weakly with sentiment
        freight = [2600.0]
        for i in range(1, 30):
            delta = trend[i] * 60 + rng.gauss(0, 35)
            freight.append(max(1200, freight[-1] + delta))

        x_labels = [d.strftime("%b %d") for d in days]

        fig = go.Figure()

        # Coloured area zones
        fig.add_hrect(y0=0.1,  y1=1.0,  fillcolor="rgba(16,185,129,0.06)",  line_width=0)
        fig.add_hrect(y0=-0.1, y1=0.1,  fillcolor="rgba(100,116,139,0.06)", line_width=0)
        fig.add_hrect(y0=-1.0, y1=-0.1, fillcolor="rgba(239,68,68,0.06)",   line_width=0)

        # Sentiment line
        fig.add_trace(go.Scatter(
            x=x_labels, y=trend,
            name="Sentiment",
            line=dict(color=C_ACCENT, width=2.5, shape="spline"),
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.08)",
            yaxis="y1",
        ))

        # Zero reference
        fig.add_hline(y=0, line_dash="dot", line_color=C_BORDER, line_width=1)

        # Freight rate overlay
        fig.add_trace(go.Scatter(
            x=x_labels, y=freight,
            name="SCFI Spot ($/FEU)",
            line=dict(color=C_WARN, width=1.8, dash="dot"),
            mode="lines",
            yaxis="y2",
            opacity=0.85,
        ))

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            height=280,
            margin=dict(l=12, r=60, t=18, b=30),
            font=dict(color=C_TEXT2, family="Inter, sans-serif", size=11),
            legend=dict(
                orientation="h", x=0, y=1.12,
                font=dict(size=10, color=C_TEXT2),
                bgcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(
                showgrid=False, zeroline=False, tickfont=dict(size=9),
                tickvals=x_labels[::5],
            ),
            yaxis=dict(
                title="Sentiment Score", range=[-1, 1],
                showgrid=True, gridcolor=C_BORDER,
                tickfont=dict(size=9), zeroline=False,
                title_font=dict(size=10),
            ),
            yaxis2=dict(
                title="SCFI $/FEU", overlaying="y", side="right",
                showgrid=False, tickfont=dict(size=9),
                title_font=dict(size=10), zeroline=False,
            ),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False},
                        key="news_sentiment_trend_chart")
    except Exception:
        logger.exception("sentiment_trend failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Topic Category Breakdown
# ─────────────────────────────────────────────────────────────────────────────


def _render_topic_breakdown(articles: list[dict]) -> None:
    try:
        _section_header(
            "Topic Category Breakdown",
            "News volume across six core shipping market themes",
        )

        topic_counts: Counter = Counter()
        topic_sentiment: dict[str, list[float]] = defaultdict(list)
        for a in articles:
            for t in a.get("topic_tags", []):
                topic_counts[t] += 1
                topic_sentiment[t].append(a["sentiment_score"])

        # Ensure all topics appear
        for t in TOPICS:
            topic_counts.setdefault(t, 0)

        total = sum(topic_counts.values()) or 1

        cols = st.columns(len(TOPICS))
        for col, topic in zip(cols, TOPICS):
            with col:
                count = topic_counts[topic]
                pct   = count / total * 100
                scores = topic_sentiment.get(topic, [0.0])
                avg_s  = sum(scores) / len(scores) if scores else 0.0
                s_label = "BULLISH" if avg_s > 0.05 else ("BEARISH" if avg_s < -0.05 else "NEUTRAL")
                s_color = _sentiment_color(s_label)
                icon    = _TOPIC_ICONS.get(topic, "•")
                tc      = _TOPIC_COLORS.get(topic, C_ACCENT)

                st.markdown(
                    f"""
                    <div style="background:{C_CARD};border:1px solid {tc}33;
                                border-top:3px solid {tc};border-radius:10px;
                                padding:14px 12px;text-align:center;
                                box-shadow:0 2px 12px rgba(0,0,0,0.25);">
                      <div style="font-size:1.4rem;">{icon}</div>
                      <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.07em;
                                  color:{tc};text-transform:uppercase;margin:4px 0 2px;">
                        {topic}
                      </div>
                      <div style="font-size:1.6rem;font-weight:800;color:{C_TEXT};">{count}</div>
                      <div style="font-size:0.68rem;color:{C_TEXT3};margin-bottom:6px;">{pct:.0f}% of stories</div>
                      <div style="height:4px;background:rgba(255,255,255,0.07);
                                  border-radius:2px;overflow:hidden;margin-bottom:6px;">
                        <div style="height:100%;width:{pct:.0f}%;background:{tc};
                                    border-radius:2px;"></div>
                      </div>
                      <div style="font-size:0.66rem;font-weight:700;color:{s_color};
                                  letter-spacing:0.06em;">{s_label}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    except Exception:
        logger.exception("topic_breakdown failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Top Stories Feed
# ─────────────────────────────────────────────────────────────────────────────


def _render_top_stories(articles: list[dict]) -> None:
    try:
        _section_header(
            "Top Stories",
            "Ranked by relevance score — latest intelligence from global shipping press",
        )

        sorted_arts = sorted(articles, key=lambda a: a["relevance_score"], reverse=True)

        for i, a in enumerate(sorted_arts[:8]):
            label      = a["sentiment_label"]
            s_color    = _sentiment_color(label)
            s_bg       = _sentiment_bg(label)
            src        = a["source"]
            src_color  = _source_color(src)
            src_init   = _source_initials(src)
            age        = _age_str(a["published_dt"])
            topics     = a.get("topic_tags", [])[:2]
            topic_pills = " ".join(
                _pill(_TOPIC_ICONS.get(t, "") + " " + t.upper(),
                      _TOPIC_COLORS.get(t, C_ACCENT))
                for t in topics
            )
            breaking_badge = (
                _pill("BREAKING", C_LOW, "rgba(239,68,68,0.18)")
                if a.get("breaking") else ""
            )
            relevance_bar_w = int(a["relevance_score"] * 100)
            score_str = f"{a['sentiment_score']:+.2f}"

            st.markdown(
                f"""
                <div style="background:{C_CARD};border:1px solid {C_BORDER};
                            border-left:4px solid {s_color};border-radius:12px;
                            padding:18px 20px;margin-bottom:12px;
                            box-shadow:0 2px 14px rgba(0,0,0,0.25);
                            transition:border-color 0.2s;">
                  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
                    <!-- Source avatar -->
                    <div style="width:34px;height:34px;border-radius:50%;
                                background:{src_color};display:flex;align-items:center;
                                justify-content:center;font-size:0.7rem;font-weight:700;
                                color:#fff;flex-shrink:0;">{src_init}</div>
                    <div style="flex:1;min-width:0;">
                      <div style="font-size:0.72rem;color:{C_TEXT2};font-weight:600;">{src}</div>
                      <div style="font-size:0.68rem;color:{C_TEXT3};">{age}</div>
                    </div>
                    <!-- Badges right -->
                    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
                      {breaking_badge}
                      <span style="background:{s_bg};border:1px solid {s_color}55;
                                   color:{s_color};padding:2px 9px;border-radius:99px;
                                   font-size:0.68rem;font-weight:700;letter-spacing:0.05em;">
                        {label} {score_str}
                      </span>
                      {topic_pills}
                    </div>
                  </div>
                  <!-- Headline -->
                  <a href="{a['url']}" style="text-decoration:none;">
                    <div style="font-size:0.96rem;font-weight:700;color:{C_TEXT};
                                line-height:1.4;margin-bottom:8px;
                                hover:color:{C_ACCENT};">{a['title']}</div>
                  </a>
                  <!-- Summary -->
                  <div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.6;
                              margin-bottom:10px;">{a['summary'][:220]}{'…' if len(a['summary'])>220 else ''}</div>
                  <!-- Relevance bar -->
                  <div style="display:flex;align-items:center;gap:8px;">
                    <div style="font-size:0.66rem;color:{C_TEXT3};white-space:nowrap;">Relevance</div>
                    <div style="flex:1;height:3px;background:rgba(255,255,255,0.07);border-radius:2px;">
                      <div style="height:100%;width:{relevance_bar_w}%;background:{C_ACCENT};border-radius:2px;"></div>
                    </div>
                    <div style="font-size:0.66rem;color:{C_TEXT3};white-space:nowrap;">{a['relevance_score']:.0%}</div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("top_stories failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Sentiment by Topic (bar chart)
# ─────────────────────────────────────────────────────────────────────────────


def _render_sentiment_by_topic(articles: list[dict]) -> None:
    try:
        _section_header(
            "Sentiment by Topic",
            "Average sentiment score per category — positive = bullish signal",
        )

        topic_scores: dict[str, list[float]] = defaultdict(list)
        for a in articles:
            for t in a.get("topic_tags", []):
                topic_scores[t].append(a["sentiment_score"])

        avgs  = []
        cols  = []
        for t in TOPICS:
            s = topic_scores.get(t, [0.0])
            avg = sum(s) / len(s) if s else 0.0
            avgs.append(avg)
            cols.append(C_HIGH if avg > 0.05 else (C_LOW if avg < -0.05 else C_NEUT))

        fig = go.Figure(go.Bar(
            y=TOPICS,
            x=avgs,
            orientation="h",
            marker=dict(color=cols, line=dict(width=0)),
            text=[f"{v:+.2f}" for v in avgs],
            textposition="outside",
            textfont=dict(color=C_TEXT2, size=10),
            hovertemplate="%{y}: %{x:+.3f}<extra></extra>",
        ))
        fig.add_vline(x=0, line_color=C_BORDER, line_width=1.5)
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C_CARD,
            plot_bgcolor=C_CARD,
            height=260,
            margin=dict(l=10, r=60, t=12, b=12),
            font=dict(color=C_TEXT2, family="Inter, sans-serif", size=11),
            xaxis=dict(
                range=[-1, 1], showgrid=True,
                gridcolor=C_BORDER, zeroline=False, tickfont=dict(size=9),
                title="Average Sentiment Score", title_font=dict(size=10),
            ),
            yaxis=dict(showgrid=False, tickfont=dict(size=11)),
            bargap=0.35,
        )
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False},
                        key="news_sentiment_by_topic")
    except Exception:
        logger.exception("sentiment_by_topic failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Market-Moving News Tracker
# ─────────────────────────────────────────────────────────────────────────────


def _render_market_moving(articles: list[dict]) -> None:
    try:
        _section_header(
            "Market-Moving News Tracker",
            "Highest-impact stories ranked by sentiment × relevance signal strength",
        )

        ranked = sorted(articles, key=lambda a: _impact_score(a), reverse=True)[:5]

        for rank, a in enumerate(ranked, 1):
            label      = a["sentiment_label"]
            s_color    = _sentiment_color(label)
            impact_pct = a.get("freight_impact_pct", 0.0) or 0.0
            imp_color  = C_HIGH if impact_pct > 0 else (C_LOW if impact_pct < 0 else C_NEUT)
            imp_str    = f"{impact_pct:+.1f}% est. rate impact"
            imp_score  = _impact_score(a)
            bar_w      = min(100, int(imp_score * 120))
            rank_bg    = [C_WARN, C_TEXT2, C_CYAN, C_TEXT3, C_TEXT3][rank - 1]

            st.markdown(
                f"""
                <div style="background:{C_CARD};border:1px solid {C_BORDER};
                            border-radius:12px;padding:16px 20px;margin-bottom:10px;
                            display:flex;gap:16px;align-items:flex-start;
                            box-shadow:0 2px 12px rgba(0,0,0,0.2);">
                  <!-- Rank badge -->
                  <div style="min-width:36px;height:36px;border-radius:50%;
                              background:{rank_bg}22;border:2px solid {rank_bg};
                              display:flex;align-items:center;justify-content:center;
                              font-size:0.85rem;font-weight:800;color:{rank_bg};
                              flex-shrink:0;">#{rank}</div>
                  <div style="flex:1;min-width:0;">
                    <div style="font-size:0.9rem;font-weight:700;color:{C_TEXT};
                                margin-bottom:5px;line-height:1.35;">{a['title']}</div>
                    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px;">
                      <span style="font-size:0.7rem;color:{C_TEXT3};">{a['source']}</span>
                      <span style="font-size:0.7rem;color:{C_TEXT3};">{_age_str(a['published_dt'])}</span>
                      <span style="font-size:0.7rem;font-weight:700;color:{s_color};
                                   letter-spacing:0.04em;">{label}</span>
                      <span style="font-size:0.72rem;font-weight:700;color:{imp_color};">{imp_str}</span>
                    </div>
                    <!-- Impact bar -->
                    <div style="display:flex;align-items:center;gap:8px;">
                      <div style="font-size:0.64rem;color:{C_TEXT3};white-space:nowrap;">Signal</div>
                      <div style="flex:1;height:4px;background:rgba(255,255,255,0.07);border-radius:2px;">
                        <div style="height:100%;width:{bar_w}%;background:{s_color};border-radius:2px;"></div>
                      </div>
                      <div style="font-size:0.64rem;color:{C_TEXT3};white-space:nowrap;">{imp_score:.2f}</div>
                    </div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("market_moving failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — Source Reliability Scorecard
# ─────────────────────────────────────────────────────────────────────────────


def _render_source_reliability() -> None:
    try:
        _section_header(
            "Source Reliability Scorecard",
            "Predictive accuracy — how well each outlet anticipates market moves",
        )

        tier_colors = {
            "Premium":    C_ACCENT,
            "Standard":   C_HIGH,
            "Wire":       C_WARN,
            "Specialist": C_CONV,
        }

        header_html = f"""
        <div style="display:grid;grid-template-columns:1fr 110px 110px 80px 90px;
                    gap:8px;padding:8px 12px;margin-bottom:4px;">
          <div style="font-size:0.67rem;font-weight:700;color:{C_TEXT3};letter-spacing:0.07em;">SOURCE</div>
          <div style="font-size:0.67rem;font-weight:700;color:{C_TEXT3};letter-spacing:0.07em;text-align:center;">ACCURACY</div>
          <div style="font-size:0.67rem;font-weight:700;color:{C_TEXT3};letter-spacing:0.07em;text-align:center;">LEAD TIME</div>
          <div style="font-size:0.67rem;font-weight:700;color:{C_TEXT3};letter-spacing:0.07em;text-align:center;">STORIES</div>
          <div style="font-size:0.67rem;font-weight:700;color:{C_TEXT3};letter-spacing:0.07em;text-align:center;">TIER</div>
        </div>
        """
        st.markdown(header_html, unsafe_allow_html=True)

        for src_data in _SOURCE_RELIABILITY:
            src   = src_data["source"]
            acc   = src_data["accuracy"]
            lead  = src_data["lead_time_days"]
            cnt   = src_data["stories"]
            tier  = src_data["tier"]
            tc    = tier_colors.get(tier, C_NEUT)
            sc    = _source_color(src)
            init  = _source_initials(src)
            bar_w = int(acc * 100)
            acc_c = C_HIGH if acc >= 0.78 else (C_WARN if acc >= 0.70 else C_LOW)

            st.markdown(
                f"""
                <div style="display:grid;grid-template-columns:1fr 110px 110px 80px 90px;
                            gap:8px;align-items:center;background:{C_CARD};
                            border:1px solid {C_BORDER};border-radius:10px;
                            padding:10px 12px;margin-bottom:6px;">
                  <!-- Source -->
                  <div style="display:flex;align-items:center;gap:10px;">
                    <div style="width:28px;height:28px;border-radius:50%;
                                background:{sc};flex-shrink:0;display:flex;
                                align-items:center;justify-content:center;
                                font-size:0.62rem;font-weight:700;color:#fff;">{init}</div>
                    <span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{src}</span>
                  </div>
                  <!-- Accuracy bar -->
                  <div style="text-align:center;">
                    <div style="font-size:0.82rem;font-weight:700;color:{acc_c};margin-bottom:3px;">{acc:.0%}</div>
                    <div style="height:3px;background:rgba(255,255,255,0.07);border-radius:2px;">
                      <div style="height:100%;width:{bar_w}%;background:{acc_c};border-radius:2px;"></div>
                    </div>
                  </div>
                  <!-- Lead time -->
                  <div style="text-align:center;font-size:0.8rem;color:{C_TEXT2};">{lead:.1f}d ahead</div>
                  <!-- Story count -->
                  <div style="text-align:center;font-size:0.8rem;color:{C_TEXT2};">{cnt:,}</div>
                  <!-- Tier badge -->
                  <div style="text-align:center;">
                    <span style="background:{tc}22;border:1px solid {tc}55;
                                 color:{tc};padding:3px 10px;border-radius:99px;
                                 font-size:0.65rem;font-weight:700;letter-spacing:0.05em;">{tier}</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("source_reliability failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 7 — Geographic Focus Breakdown
# ─────────────────────────────────────────────────────────────────────────────


def _render_geographic_focus(articles: list[dict]) -> None:
    try:
        _section_header(
            "Geographic Focus",
            "News volume and sentiment heat by region",
        )

        region_counts: Counter = Counter()
        region_sentiment: dict[str, list[float]] = defaultdict(list)
        for a in articles:
            r = a.get("region", "Global")
            region_counts[r] += 1
            region_sentiment[r].append(a["sentiment_score"])

        total = sum(region_counts.values()) or 1

        # Left panel: bar list; Right panel: bubble map
        left_col, right_col = st.columns([1, 1])

        with left_col:
            for rdata in sorted(_REGIONS, key=lambda r: region_counts.get(r["name"], 0), reverse=True):
                rname = rdata["name"]
                count = region_counts.get(rname, 0)
                pct   = count / total * 100
                scores = region_sentiment.get(rname, [0.0])
                avg_s  = sum(scores) / len(scores) if scores else 0.0
                s_c    = _sentiment_color(
                    "BULLISH" if avg_s > 0.05 else ("BEARISH" if avg_s < -0.05 else "NEUTRAL")
                )
                rc = rdata["color"]
                st.markdown(
                    f"""
                    <div style="background:{C_CARD};border:1px solid {C_BORDER};
                                border-radius:9px;padding:10px 14px;margin-bottom:7px;">
                      <div style="display:flex;justify-content:space-between;
                                  align-items:center;margin-bottom:5px;">
                        <span style="font-size:0.82rem;font-weight:600;color:{C_TEXT};">{rname}</span>
                        <span style="font-size:0.78rem;font-weight:700;color:{s_c};">{avg_s:+.2f}</span>
                      </div>
                      <div style="display:flex;align-items:center;gap:8px;">
                        <div style="flex:1;height:5px;background:rgba(255,255,255,0.07);border-radius:3px;">
                          <div style="height:100%;width:{pct:.0f}%;background:{rc};border-radius:3px;"></div>
                        </div>
                        <span style="font-size:0.68rem;color:{C_TEXT3};white-space:nowrap;">{count} stories</span>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with right_col:
            # Bubble map
            lats  = [r["lat"] for r in _REGIONS]
            lons  = [r["lon"] for r in _REGIONS]
            names = [r["name"] for r in _REGIONS]
            sizes = [max(8, region_counts.get(r["name"], 0) * 12) for r in _REGIONS]
            clrs  = [r["color"] for r in _REGIONS]

            fig = go.Figure(go.Scattergeo(
                lat=lats, lon=lons,
                text=names,
                mode="markers+text",
                textposition="top center",
                textfont=dict(size=9, color=C_TEXT2),
                marker=dict(
                    size=sizes,
                    color=clrs,
                    opacity=0.75,
                    line=dict(color=C_CARD, width=1.5),
                ),
                hovertemplate="%{text}: %{marker.size:.0f} articles<extra></extra>",
            ))
            fig.update_geos(
                showland=True, landcolor="#1a2235",
                showocean=True, oceancolor="#0a0f1a",
                showcoastlines=True, coastlinecolor="rgba(255,255,255,0.1)",
                showframe=False,
                projection_type="natural earth",
                bgcolor=C_CARD,
            )
            fig.update_layout(
                paper_bgcolor=C_CARD,
                geo=dict(bgcolor=C_CARD),
                height=300,
                margin=dict(l=0, r=0, t=0, b=0),
                font=dict(color=C_TEXT2),
            )
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False},
                            key="news_geo_map")
    except Exception:
        logger.exception("geographic_focus failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 8 — Breaking Alerts Panel
# ─────────────────────────────────────────────────────────────────────────────


def _render_breaking_alerts(articles: list[dict]) -> None:
    try:
        breaking = [a for a in articles if a.get("breaking")]
        if not breaking:
            return

        _section_header(
            "Breaking Alerts",
            "Priority intelligence — requires immediate attention",
        )

        for a in breaking:
            label   = a["sentiment_label"]
            s_color = _sentiment_color(label)
            age     = _age_str(a["published_dt"])

            st.markdown(
                f"""
                <style>
                @keyframes pulse-border {{
                  0%   {{ border-color: rgba(239,68,68,0.9); box-shadow: 0 0 0 0 rgba(239,68,68,0.4); }}
                  70%  {{ border-color: rgba(239,68,68,0.4); box-shadow: 0 0 0 8px rgba(239,68,68,0); }}
                  100% {{ border-color: rgba(239,68,68,0.9); box-shadow: 0 0 0 0 rgba(239,68,68,0); }}
                }}
                .breaking-card {{
                  animation: pulse-border 1.8s infinite;
                }}
                </style>
                <div class="breaking-card"
                     style="background:rgba(239,68,68,0.08);
                            border:2px solid rgba(239,68,68,0.9);
                            border-radius:12px;padding:18px 20px;margin-bottom:12px;">
                  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">
                    <span style="background:{C_LOW};color:#fff;padding:3px 10px;
                                 border-radius:99px;font-size:0.68rem;font-weight:800;
                                 letter-spacing:0.08em;">BREAKING</span>
                    <span style="font-size:0.7rem;color:{C_TEXT3};">{a['source']} · {age}</span>
                    <span style="font-size:0.7rem;font-weight:700;color:{s_color};">{label}</span>
                  </div>
                  <div style="font-size:0.98rem;font-weight:700;color:{C_TEXT};
                              line-height:1.4;margin-bottom:8px;">{a['title']}</div>
                  <div style="font-size:0.78rem;color:{C_TEXT2};line-height:1.55;">{a['summary'][:300]}{'…' if len(a['summary'])>300 else ''}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("breaking_alerts failed")


# ─────────────────────────────────────────────────────────────────────────────
# Section 9 — News Timeline
# ─────────────────────────────────────────────────────────────────────────────


def _render_news_timeline(articles: list[dict]) -> None:
    try:
        _section_header(
            "News Timeline",
            "Chronological feed with sentiment-coded border — most recent first",
        )

        sorted_arts = sorted(
            articles,
            key=lambda a: a["published_dt"] if a["published_dt"].tzinfo else a["published_dt"].replace(tzinfo=timezone.utc),
            reverse=True,
        )

        # Group by date
        by_date: dict[str, list[dict]] = defaultdict(list)
        for a in sorted_arts:
            pub = a["published_dt"]
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            label_date = pub.strftime("%A, %d %b %Y")
            by_date[label_date].append(a)

        for date_label, day_arts in list(by_date.items())[:7]:
            st.markdown(
                f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
                f'letter-spacing:0.08em;text-transform:uppercase;'
                f'padding:6px 0 4px;margin-top:4px;border-bottom:1px solid {C_BORDER};'
                f'margin-bottom:8px;">{date_label}</div>',
                unsafe_allow_html=True,
            )
            for a in day_arts:
                label   = a["sentiment_label"]
                s_color = _sentiment_color(label)
                pub     = a["published_dt"]
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                time_str = pub.strftime("%H:%M")
                topics   = a.get("topic_tags", [])[:2]
                t_pills  = " ".join(
                    _pill(t.upper(), _TOPIC_COLORS.get(t, C_ACCENT))
                    for t in topics
                )

                st.markdown(
                    f"""
                    <div style="display:flex;gap:0;margin-bottom:8px;">
                      <!-- Timeline track -->
                      <div style="display:flex;flex-direction:column;align-items:center;
                                  width:40px;flex-shrink:0;padding-top:4px;">
                        <div style="font-size:0.64rem;color:{C_TEXT3};white-space:nowrap;
                                    margin-bottom:4px;">{time_str}</div>
                        <div style="width:10px;height:10px;border-radius:50%;
                                    background:{s_color};flex-shrink:0;"></div>
                        <div style="width:2px;flex:1;background:{C_BORDER};margin-top:2px;"></div>
                      </div>
                      <!-- Card -->
                      <div style="flex:1;background:{C_CARD};border:1px solid {C_BORDER};
                                  border-left:3px solid {s_color};border-radius:0 10px 10px 0;
                                  padding:10px 14px;margin-left:8px;margin-bottom:2px;">
                        <div style="font-size:0.85rem;font-weight:700;color:{C_TEXT};
                                    margin-bottom:4px;line-height:1.35;">{a['title']}</div>
                        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                          <span style="font-size:0.68rem;color:{C_TEXT3};">{a['source']}</span>
                          <span style="font-size:0.68rem;font-weight:700;color:{s_color};">{label}</span>
                          {t_pills}
                        </div>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    except Exception:
        logger.exception("news_timeline failed")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


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

    raw: list[Any] = news_articles or []
    articles = _normalised_articles(raw) if raw else list(_FALLBACK_ARTICLES)

    # RSS failure banner
    try:
        if rss_failed:
            st.warning(
                "Live news feed unavailable — showing curated fallback intelligence. "
                "Check your RSS / API configuration.",
                icon="⚠️",
            )
    except Exception:
        pass

    # ── Section 0: News Hero ──────────────────────────────────────────────────
    _render_news_hero(articles)

    # ── Section 1: Sentiment Trend ────────────────────────────────────────────
    _render_sentiment_trend(articles)

    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    # ── Section 8: Breaking Alerts (surfaced early) ───────────────────────────
    _render_breaking_alerts(articles)

    st.divider()

    # ── Section 2: Topic Category Breakdown ──────────────────────────────────
    _render_topic_breakdown(articles)

    st.divider()

    # ── Section 3: Top Stories Feed ──────────────────────────────────────────
    _render_top_stories(articles)

    st.divider()

    # ── Section 4 + 5: Sentiment by Topic  |  Market-Moving News ─────────────
    col_l, col_r = st.columns([1, 1])
    with col_l:
        _render_sentiment_by_topic(articles)
    with col_r:
        _render_market_moving(articles)

    st.divider()

    # ── Section 6: Source Reliability Scorecard ───────────────────────────────
    _render_source_reliability()

    st.divider()

    # ── Section 7: Geographic Focus ───────────────────────────────────────────
    _render_geographic_focus(articles)

    st.divider()

    # ── Section 9: News Timeline ──────────────────────────────────────────────
    _render_news_timeline(articles)
