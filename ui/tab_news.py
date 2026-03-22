"""
Shipping News Intelligence — world-class rewrite.

Sections
--------
1. Sentiment Pulse        — 4 hero KPIs
2. Topic Heatmap          — 9 topics × 5 days Bloomberg-style grid
3. Breaking News          — top-5 urgent article cards
4. Full News Feed         — filterable table with expandable rows
5. Named Entity Tracker   — top-mentioned entities table
6. Geographic Sentiment   — plotly scatter_geo choropleth
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

import plotly.graph_objects as go
import streamlit as st

# ── Palette ────────────────────────────────────────────────────────────────────
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
C_PURPLE  = "#8b5cf6"
C_CYAN    = "#06b6d4"

# ── Topic taxonomy ─────────────────────────────────────────────────────────────
TOPICS = [
    "Freight Rates", "Port Congestion", "Carrier Capacity",
    "Geopolitics", "Fuel/Bunker", "Trade Policy",
    "Vessel Finance", "Sustainability", "M&A",
]

_TOPIC_COLOR = {
    "Freight Rates":    C_ACCENT,
    "Port Congestion":  C_MOD,
    "Carrier Capacity": C_CYAN,
    "Geopolitics":      C_LOW,
    "Fuel/Bunker":      C_PURPLE,
    "Trade Policy":     "#f97316",
    "Vessel Finance":   "#14b8a6",
    "Sustainability":   C_HIGH,
    "M&A":              "#ec4899",
}

_SOURCE_COLOR = {
    "Reuters":       C_ACCENT,
    "Bloomberg":     "#f59e0b",
    "Lloyd's List":  C_HIGH,
    "TradeWinds":    C_CYAN,
    "Splash247":     C_PURPLE,
    "Hellenic Shipping News": "#f97316",
    "The Loadstar":  "#14b8a6",
}

# ── Mock data ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


_MOCK_ARTICLES: list[dict] = [
    {"headline": "Maersk reports 40% surge in Trans-Pacific bookings ahead of Q2 peak",
     "source": "TradeWinds", "sentiment_score": 0.42, "topic": "Freight Rates",
     "published_at": _now() - timedelta(hours=2), "urgency": 0.81,
     "summary": "A.P. Moller-Maersk has reported a significant 40% increase in Trans-Pacific bookings as shippers rush to lock in capacity ahead of the traditional Q2 peak season, driven by pre-tariff inventory building.",
     "url": "#", "entities": ["Maersk", "Trans-Pacific", "Los Angeles", "Shanghai"]},

    {"headline": "Red Sea Houthi attacks force mass rerouting via Cape of Good Hope",
     "source": "Lloyd's List", "sentiment_score": -0.71, "topic": "Geopolitics",
     "published_at": _now() - timedelta(hours=1), "urgency": 0.97,
     "summary": "Ongoing Houthi attacks continue to force major container lines to divert around the Cape of Good Hope, adding an estimated $300 million per week in extra fuel costs to the global fleet.",
     "url": "#", "entities": ["Houthi", "Red Sea", "Cape of Good Hope", "Suez Canal"]},

    {"headline": "VLSFO bunker prices spike 8% in Singapore amid OPEC+ supply cut extension",
     "source": "Reuters", "sentiment_score": -0.38, "topic": "Fuel/Bunker",
     "published_at": _now() - timedelta(hours=3), "urgency": 0.74,
     "summary": "Very low-sulphur fuel oil prices in Singapore surged 8% this week after OPEC+ announced an extension of production cuts through Q3 2026, tightening global oil supply.",
     "url": "#", "entities": ["Singapore", "VLSFO", "OPEC+", "Saudi Aramco"]},

    {"headline": "Port of Los Angeles breaks monthly throughput record with 1.1M TEU",
     "source": "Bloomberg", "sentiment_score": 0.61, "topic": "Port Congestion",
     "published_at": _now() - timedelta(hours=5), "urgency": 0.55,
     "summary": "The Port of Los Angeles processed a record 1.1 million TEUs in February 2026, driven by front-loading ahead of potential tariff escalation and strong consumer demand.",
     "url": "#", "entities": ["Port of Los Angeles", "Long Beach", "TEU"]},

    {"headline": "MSC acquires Bolloré Africa Logistics in $5.7B landmark deal",
     "source": "Bloomberg", "sentiment_score": 0.29, "topic": "M&A",
     "published_at": _now() - timedelta(hours=6), "urgency": 0.88,
     "summary": "Mediterranean Shipping Company has completed its acquisition of Bolloré Africa Logistics for $5.7 billion, cementing its position as the dominant container carrier in sub-Saharan Africa.",
     "url": "#", "entities": ["MSC", "Bolloré", "Africa", "Aponte"]},

    {"headline": "IMO carbon intensity regulation triggers 12% capacity withdrawal from slow steaming",
     "source": "Hellenic Shipping News", "sentiment_score": -0.22, "topic": "Sustainability",
     "published_at": _now() - timedelta(hours=8), "urgency": 0.61,
     "summary": "New IMO CII ratings have forced operators to slow-steam vessels, effectively withdrawing an estimated 12% of nominal fleet capacity from the market.",
     "url": "#", "entities": ["IMO", "CII", "Hapag-Lloyd", "Evergreen"]},

    {"headline": "US-China trade tensions escalate: 35% tariff on Chinese goods looms",
     "source": "Reuters", "sentiment_score": -0.58, "topic": "Trade Policy",
     "published_at": _now() - timedelta(hours=9), "urgency": 0.91,
     "summary": "The White House confirmed it is considering a blanket 35% tariff on Chinese manufactured goods, which analysts warn could trigger an immediate 20% drop in Trans-Pacific container volumes.",
     "url": "#", "entities": ["US", "China", "Trans-Pacific", "White House"]},

    {"headline": "Hapag-Lloyd secures $3.2B green ammonia vessel financing with ING",
     "source": "The Loadstar", "sentiment_score": 0.47, "topic": "Vessel Finance",
     "published_at": _now() - timedelta(hours=11), "urgency": 0.44,
     "summary": "Hapag-Lloyd has secured a $3.2 billion facility from ING Bank for the construction of 12 ammonia-dual-fuel ultra-large container vessels, the largest green shipping finance deal of 2026.",
     "url": "#", "entities": ["Hapag-Lloyd", "ING Bank", "Green Ammonia", "ULCV"]},

    {"headline": "Shanghai port dwell times rise to 4.8 days as Chinese New Year backlog clears",
     "source": "Splash247", "sentiment_score": -0.15, "topic": "Port Congestion",
     "published_at": _now() - timedelta(hours=13), "urgency": 0.52,
     "summary": "Average dwell times at Yangshan Deep Water Port have climbed to 4.8 days as operators struggle to clear a backlog accumulated during the extended Chinese New Year period.",
     "url": "#", "entities": ["Shanghai", "Yangshan", "COSCO", "dwell time"]},

    {"headline": "Baltic Dry Index climbs 18% on Capesize demand surge from Brazilian iron ore",
     "source": "TradeWinds", "sentiment_score": 0.68, "topic": "Freight Rates",
     "published_at": _now() - timedelta(hours=14), "urgency": 0.66,
     "summary": "The Baltic Dry Index rose 18% over the past two weeks driven by strong Capesize demand from Brazilian iron ore exporters shipping to Chinese steel mills.",
     "url": "#", "entities": ["Baltic Dry Index", "Capesize", "Brazil", "Vale", "China"]},

    {"headline": "CMA CGM invests $1.2B in digital port automation across 12 terminals",
     "source": "Hellenic Shipping News", "sentiment_score": 0.38, "topic": "Carrier Capacity",
     "published_at": _now() - timedelta(hours=16), "urgency": 0.39,
     "summary": "CMA CGM announced a $1.2 billion investment in automated port technology across 12 terminals in Europe, Asia and the Americas, aiming to cut vessel turnaround times by 30%.",
     "url": "#", "entities": ["CMA CGM", "Rotterdam", "Singapore", "Automation"]},

    {"headline": "Panama Canal drought eases: daily transits climb back to 36 from 24",
     "source": "Lloyd's List", "sentiment_score": 0.54, "topic": "Port Congestion",
     "published_at": _now() - timedelta(hours=18), "urgency": 0.57,
     "summary": "Above-average rainfall over Lake Gatun has allowed Panama Canal Authority to raise daily transit limits to 36 vessels, recovering from the 24-vessel drought restriction imposed in late 2025.",
     "url": "#", "entities": ["Panama Canal", "Lake Gatun", "ACP", "Panamax"]},

    {"headline": "EU shipping ETS costs hit $420M in Q1 2026 — operators pass costs to shippers",
     "source": "Reuters", "sentiment_score": -0.33, "topic": "Sustainability",
     "published_at": _now() - timedelta(hours=20), "urgency": 0.63,
     "summary": "European shipping companies faced a combined €390 million in EU Emissions Trading System charges in Q1 2026, with most passing the costs directly to shippers via BAF surcharges.",
     "url": "#", "entities": ["EU ETS", "Maersk", "MSC", "BAF surcharge"]},

    {"headline": "Evergreen orders 20 methanol-powered 24,000 TEU vessels in $6B newbuild spree",
     "source": "Splash247", "sentiment_score": 0.22, "topic": "Vessel Finance",
     "published_at": _now() - timedelta(hours=22), "urgency": 0.48,
     "summary": "Evergreen Marine has placed orders for 20 methanol dual-fuel ultra-large container vessels at Korean yards DSME and HHI, representing the single largest newbuild order of 2026.",
     "url": "#", "entities": ["Evergreen", "DSME", "HHI", "Methanol", "Korea"]},

    {"headline": "Geopolitical risk premium adds $180/TEU to Europe-Asia spot rates",
     "source": "Bloomberg", "sentiment_score": -0.44, "topic": "Freight Rates",
     "published_at": _now() - timedelta(hours=25), "urgency": 0.78,
     "summary": "Analysts at Drewry estimate that the combined effect of Red Sea diversions and Black Sea tensions has added approximately $180 per TEU to Europe-Asia spot rates versus pre-crisis levels.",
     "url": "#", "entities": ["Drewry", "Europe-Asia", "Red Sea", "Black Sea"]},

    {"headline": "Australia bans Russian tankers from port calls citing insurance concerns",
     "source": "Lloyd's List", "sentiment_score": -0.51, "topic": "Geopolitics",
     "published_at": _now() - timedelta(hours=27), "urgency": 0.72,
     "summary": "Australia announced an immediate ban on Russian-flagged and Russian-owned tankers from its ports, citing concerns over shadow fleet insurance coverage and environmental liability.",
     "url": "#", "entities": ["Australia", "Russia", "Shadow Fleet", "Tanker"]},

    {"headline": "FuelEU Maritime: carriers scramble to secure green methanol supply chains",
     "source": "The Loadstar", "sentiment_score": -0.08, "topic": "Sustainability",
     "published_at": _now() - timedelta(hours=30), "urgency": 0.35,
     "summary": "With FuelEU Maritime regulation now in force, container lines are racing to sign long-term green methanol supply agreements with producers in Chile, Iceland and Morocco.",
     "url": "#", "entities": ["FuelEU", "Methanol", "Chile", "Iceland", "Morocco"]},

    {"headline": "Cosco Shipping acquires 30% stake in Port of Hamburg for €1.8B",
     "source": "Reuters", "sentiment_score": 0.15, "topic": "M&A",
     "published_at": _now() - timedelta(hours=33), "urgency": 0.67,
     "summary": "COSCO Shipping Ports secured a 30% stake in the Port of Hamburg's HHLA terminal operator after the EU approved the deal with conditions, ending a two-year regulatory review.",
     "url": "#", "entities": ["COSCO", "Hamburg", "HHLA", "EU Commission"]},

    {"headline": "Trans-Atlantic rates surge 22% as blank sailings thin available capacity",
     "source": "TradeWinds", "sentiment_score": 0.35, "topic": "Carrier Capacity",
     "published_at": _now() - timedelta(hours=36), "urgency": 0.59,
     "summary": "Trans-Atlantic container spot rates jumped 22% week-on-week after the Ocean Alliance and 2M announced a combined 14 blank sailings, dramatically reducing available capacity.",
     "url": "#", "entities": ["Trans-Atlantic", "Ocean Alliance", "2M", "blank sailings"]},

    {"headline": "LNG as marine fuel gains share: 18% of newbuild orders in 2025 were LNG-ready",
     "source": "Hellenic Shipping News", "sentiment_score": 0.28, "topic": "Fuel/Bunker",
     "published_at": _now() - timedelta(hours=40), "urgency": 0.31,
     "summary": "Analysis of 2025 newbuild orders shows 18% specified LNG dual-fuel capability, up from 11% in 2024, as shipowners hedge against long-term fuel price and regulatory uncertainty.",
     "url": "#", "entities": ["LNG", "Newbuild", "DNV", "Korea Shipbuilding"]},

    {"headline": "Indian subcontinent ports see 28% volume rise as nearshoring shifts trade lanes",
     "source": "Bloomberg", "sentiment_score": 0.49, "topic": "Trade Policy",
     "published_at": _now() - timedelta(hours=44), "urgency": 0.42,
     "summary": "Nhava Sheva, Mundra and Colombo ports recorded a combined 28% volume increase year-on-year as manufacturers shift production from China to India and Vietnam.",
     "url": "#", "entities": ["India", "Nhava Sheva", "Mundra", "Colombo", "Vietnam"]},
]

# ── Entity mock data ───────────────────────────────────────────────────────────

_MOCK_ENTITIES = [
    {"entity": "Maersk", "type": "Carrier", "mentions": 34, "sentiment": 0.31, "trend": "up"},
    {"entity": "MSC", "type": "Carrier", "mentions": 28, "sentiment": 0.18, "trend": "up"},
    {"entity": "Red Sea", "type": "Region", "mentions": 26, "sentiment": -0.68, "trend": "flat"},
    {"entity": "Shanghai", "type": "Port", "mentions": 21, "sentiment": -0.12, "trend": "down"},
    {"entity": "COSCO", "type": "Carrier", "mentions": 18, "sentiment": 0.09, "trend": "up"},
    {"entity": "Baltic Dry Index", "type": "Index", "mentions": 16, "sentiment": 0.55, "trend": "up"},
    {"entity": "Hapag-Lloyd", "type": "Carrier", "mentions": 15, "sentiment": 0.22, "trend": "flat"},
    {"entity": "Panama Canal", "type": "Waterway", "mentions": 14, "sentiment": 0.41, "trend": "up"},
    {"entity": "CMA CGM", "type": "Carrier", "mentions": 13, "sentiment": 0.34, "trend": "flat"},
    {"entity": "Singapore", "type": "Port", "mentions": 12, "sentiment": 0.08, "trend": "down"},
    {"entity": "VLSFO", "type": "Commodity", "mentions": 11, "sentiment": -0.29, "trend": "down"},
    {"entity": "Evergreen", "type": "Carrier", "mentions": 10, "sentiment": 0.15, "trend": "flat"},
]

# ── Geographic sentiment data ──────────────────────────────────────────────────

_GEO_DATA = [
    {"region": "Asia-Pacific", "lat": 15, "lon": 110, "sentiment": 0.12, "volume": 87},
    {"region": "Europe", "lat": 52, "lon": 10, "sentiment": -0.08, "volume": 64},
    {"region": "North America", "lat": 40, "lon": -100, "sentiment": 0.31, "volume": 52},
    {"region": "Middle East", "lat": 25, "lon": 45, "sentiment": -0.61, "volume": 71},
    {"region": "Africa", "lat": -5, "lon": 25, "sentiment": 0.04, "volume": 23},
    {"region": "South America", "lat": -15, "lon": -60, "sentiment": 0.39, "volume": 18},
    {"region": "Red Sea", "lat": 20, "lon": 38, "sentiment": -0.79, "volume": 45},
    {"region": "Trans-Pacific", "lat": 30, "lon": 170, "sentiment": 0.28, "volume": 56},
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalise(items: list[dict]) -> list[dict]:
    """Normalise incoming dicts so internal keys are consistent."""
    out = []
    for raw in items:
        try:
            pub = raw.get("published_at") or raw.get("published_dt") or _now()
            if isinstance(pub, str):
                try:
                    pub = datetime.fromisoformat(pub)
                except Exception:
                    pub = _now()
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            score = float(raw.get("sentiment_score", 0.0))
            out.append({
                "headline":        str(raw.get("headline") or raw.get("title") or ""),
                "source":          str(raw.get("source", "Unknown")),
                "sentiment_score": max(-1.0, min(1.0, score)),
                "topic":           str(raw.get("topic") or (raw.get("topic_tags") or [""])[0] or "General"),
                "published_at":    pub,
                "urgency":         float(raw.get("urgency") or raw.get("relevance_score") or 0.5),
                "summary":         str(raw.get("summary", "")),
                "url":             str(raw.get("url", "#")),
                "entities":        list(raw.get("entities") or []),
            })
        except Exception:
            pass
    return out


def _time_ago(dt: datetime) -> str:
    try:
        delta = _now() - dt
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"
    except Exception:
        return "—"


def _sentiment_label(score: float) -> tuple[str, str]:
    if score >= 0.15:
        return "BULLISH", C_HIGH
    if score <= -0.15:
        return "BEARISH", C_LOW
    return "NEUTRAL", C_TEXT3


def _topic_chip(topic: str) -> str:
    color = _TOPIC_COLOR.get(topic, C_ACCENT)
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}44;'
        f'border-radius:4px;padding:1px 7px;font-size:10px;font-weight:600;'
        f'letter-spacing:0.5px;white-space:nowrap;">{topic}</span>'
    )


def _source_badge(source: str) -> str:
    color = _SOURCE_COLOR.get(source, C_TEXT2)
    return (
        f'<span style="color:{color};font-size:10px;font-weight:700;'
        f'letter-spacing:0.8px;white-space:nowrap;">{source.upper()}</span>'
    )


def _score_pill(score: float) -> str:
    color = C_HIGH if score >= 0.15 else (C_LOW if score <= -0.15 else C_TEXT3)
    sign  = "+" if score > 0 else ""
    return (
        f'<code style="background:{color}22;color:{color};border-radius:4px;'
        f'padding:2px 6px;font-size:11px;">{sign}{score:.2f}</code>'
    )

# ── Section 1: Sentiment Pulse ─────────────────────────────────────────────────

def _render_sentiment_pulse(articles: list[dict]) -> None:
    try:
        cutoff = _now() - timedelta(hours=24)
        recent = [a for a in articles if a["published_at"] >= cutoff] or articles

        scores     = [a["sentiment_score"] for a in recent]
        avg_score  = sum(scores) / len(scores) if scores else 0.0
        bullish_n  = sum(1 for s in scores if s >= 0.15)
        bearish_n  = sum(1 for s in scores if s <= -0.15)
        volume_24h = len(recent)
        bull_pct   = 100 * bullish_n / len(scores) if scores else 0
        bear_pct   = 100 * bearish_n / len(scores) if scores else 0

        score_color = C_HIGH if avg_score >= 0.15 else (C_LOW if avg_score <= -0.15 else C_MOD)
        sign = "+" if avg_score > 0 else ""

        kpis = [
            ("Overall Sentiment", f"{sign}{avg_score:.2f}", "Score  –1 → +1", score_color),
            ("Bullish Articles",  f"{bull_pct:.0f}%",       f"{bullish_n} articles",  C_HIGH),
            ("Bearish Articles",  f"{bear_pct:.0f}%",       f"{bearish_n} articles",  C_LOW),
            ("News Volume",       f"{volume_24h}",           "articles last 24 h",     C_ACCENT),
        ]

        cols = st.columns(4, gap="small")
        for col, (label, value, sub, color) in zip(cols, kpis):
            with col:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                    f'border-top:3px solid {color};border-radius:10px;padding:18px 20px;'
                    f'text-align:center;">'
                    f'<div style="color:{C_TEXT3};font-size:11px;font-weight:600;'
                    f'letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;">{label}</div>'
                    f'<div style="color:{color};font-size:34px;font-weight:800;'
                    f'line-height:1;font-family:monospace;">{value}</div>'
                    f'<div style="color:{C_TEXT3};font-size:11px;margin-top:6px;">{sub}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        st.warning(f"Sentiment pulse error: {exc}")

# ── Section 2: Topic Heatmap ───────────────────────────────────────────────────

def _render_topic_heatmap(articles: list[dict]) -> None:
    try:
        today   = _now().date()
        day_labels = []
        for i in range(4, -1, -1):
            d = today - timedelta(days=i)
            day_labels.append(d.strftime("%a %-d"))

        # bucket articles
        grid: dict[tuple[str, str], list[float]] = defaultdict(list)
        for a in articles:
            day_key = a["published_at"].date().strftime("%a %-d")
            if day_key in day_labels:
                grid[(a["topic"], day_key)].append(a["sentiment_score"])

        # header row
        header_cells = "".join(
            f'<th style="padding:6px 10px;text-align:center;color:{C_TEXT2};'
            f'font-size:11px;font-weight:600;letter-spacing:0.5px;'
            f'border-bottom:1px solid {C_BORDER};">{d}</th>'
            for d in day_labels
        )
        header = (
            f'<tr><th style="padding:6px 12px;text-align:left;color:{C_TEXT3};'
            f'font-size:11px;">Topic</th>{header_cells}</tr>'
        )

        rows_html = ""
        for topic in TOPICS:
            tc = _TOPIC_COLOR.get(topic, C_ACCENT)
            topic_cell = (
                f'<td style="padding:8px 12px;white-space:nowrap;">'
                f'<span style="color:{tc};font-size:12px;font-weight:600;">{topic}</span></td>'
            )
            day_cells = ""
            for day in day_labels:
                scores = grid.get((topic, day), [])
                if scores:
                    avg  = sum(scores) / len(scores)
                    cnt  = len(scores)
                    bg   = C_HIGH if avg >= 0.2 else (C_LOW if avg <= -0.2 else C_MOD)
                    sign = "+" if avg > 0 else ""
                    cell_inner = (
                        f'<div style="font-size:11px;font-weight:700;color:{bg};">'
                        f'{sign}{avg:.1f}</div>'
                        f'<div style="font-size:9px;color:{C_TEXT3};">{cnt}art</div>'
                    )
                    cell_bg = f"{bg}1a"
                else:
                    cell_inner = f'<div style="color:{C_TEXT3};font-size:11px;">—</div>'
                    cell_bg    = "transparent"
                day_cells += (
                    f'<td style="padding:6px 8px;text-align:center;'
                    f'background:{cell_bg};border-radius:6px;">{cell_inner}</td>'
                )
            rows_html += f"<tr>{topic_cell}{day_cells}</tr>"

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
            f'border-radius:12px;padding:20px;overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:separate;border-spacing:4px;">'
            f'<thead>{header}</thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        st.warning(f"Heatmap error: {exc}")

# ── Section 3: Breaking News ───────────────────────────────────────────────────

def _render_breaking_news(articles: list[dict]) -> None:
    try:
        top5 = sorted(articles, key=lambda a: a["urgency"], reverse=True)[:5]
        if not top5:
            st.info("No breaking news available.")
            return

        for a in top5:
            label, lcolor = _sentiment_label(a["sentiment_score"])
            urgency_pct   = int(a["urgency"] * 100)
            urg_color     = C_LOW if urgency_pct >= 80 else (C_MOD if urgency_pct >= 55 else C_ACCENT)
            tc            = _TOPIC_COLOR.get(a["topic"], C_ACCENT)
            sc            = _SOURCE_COLOR.get(a["source"], C_TEXT2)
            ago           = _time_ago(a["published_at"])
            sign          = "+" if a["sentiment_score"] > 0 else ""
            score_str     = f"{sign}{a['sentiment_score']:.2f}"

            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-left:4px solid {urg_color};border-radius:10px;'
                f'padding:18px 22px;margin-bottom:12px;">'
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;">'
                f'<span style="color:{sc};font-size:10px;font-weight:700;letter-spacing:1px;">{a["source"].upper()}</span>'
                f'<span style="background:{lcolor}22;color:{lcolor};border:1px solid {lcolor}44;'
                f'border-radius:4px;padding:1px 8px;font-size:10px;font-weight:700;">{label}</span>'
                f'<span style="background:{tc}22;color:{tc};border:1px solid {tc}44;'
                f'border-radius:4px;padding:1px 8px;font-size:10px;font-weight:600;">{a["topic"]}</span>'
                f'<span style="margin-left:auto;color:{urg_color};font-size:10px;font-weight:700;">'
                f'URGENCY {urgency_pct}</span>'
                f'</div>'
                f'<div style="color:{C_TEXT};font-size:17px;font-weight:700;line-height:1.4;'
                f'margin-bottom:8px;">{a["headline"]}</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;line-height:1.6;margin-bottom:10px;">{a["summary"][:200]}…</div>'
                f'<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">'
                f'<span style="color:{C_TEXT3};font-size:11px;">{ago}</span>'
                f'<code style="background:{lcolor}22;color:{lcolor};border-radius:4px;'
                f'padding:1px 6px;font-size:11px;">{score_str}</code>'
                f'<a href="{a["url"]}" style="color:{C_ACCENT};font-size:11px;'
                f'text-decoration:none;margin-left:auto;">Read full story ↗</a>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        st.warning(f"Breaking news error: {exc}")

# ── Section 4: Full News Feed ──────────────────────────────────────────────────

def _render_news_feed(articles: list[dict]) -> None:
    try:
        all_topics = sorted({a["topic"] for a in articles})
        topic_opts = ["All Topics"] + all_topics
        sel_topic  = st.selectbox("Filter by topic", topic_opts, key="news_feed_topic_filter")

        filtered = articles if sel_topic == "All Topics" else [a for a in articles if a["topic"] == sel_topic]
        filtered = sorted(filtered, key=lambda a: a["published_at"], reverse=True)

        if not filtered:
            st.info("No articles match the filter.")
            return

        # Column headers
        st.markdown(
            f'<div style="display:grid;grid-template-columns:90px 1fr 70px 120px 60px;'
            f'gap:10px;padding:6px 14px;border-bottom:1px solid {C_BORDER};'
            f'color:{C_TEXT3};font-size:10px;font-weight:700;letter-spacing:0.8px;'
            f'text-transform:uppercase;">'
            f'<span>Source</span><span>Headline</span>'
            f'<span style="text-align:center;">Score</span>'
            f'<span>Topic</span><span style="text-align:right;">Time</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for idx, a in enumerate(filtered):
            label, lcolor = _sentiment_label(a["sentiment_score"])
            sc    = _SOURCE_COLOR.get(a["source"], C_TEXT2)
            tc    = _TOPIC_COLOR.get(a["topic"], C_ACCENT)
            ago   = _time_ago(a["published_at"])
            sign  = "+" if a["sentiment_score"] > 0 else ""
            score_str = f"{sign}{a['sentiment_score']:.2f}"
            hl_short  = a["headline"][:90] + ("…" if len(a["headline"]) > 90 else "")

            # Row
            row_bg = C_CARD if idx % 2 == 0 else C_SURFACE
            st.markdown(
                f'<div style="display:grid;grid-template-columns:90px 1fr 70px 120px 60px;'
                f'gap:10px;padding:10px 14px;background:{row_bg};'
                f'border-radius:6px;align-items:center;margin-bottom:2px;">'
                f'<span style="color:{sc};font-size:10px;font-weight:700;'
                f'letter-spacing:0.5px;">{a["source"]}</span>'
                f'<span style="color:{C_TEXT};font-size:13px;" title="{a["headline"]}">{hl_short}</span>'
                f'<code style="background:{lcolor}22;color:{lcolor};border-radius:4px;'
                f'padding:1px 5px;font-size:11px;text-align:center;">{score_str}</code>'
                f'<span style="background:{tc}22;color:{tc};border-radius:4px;'
                f'padding:1px 7px;font-size:10px;font-weight:600;">{a["topic"]}</span>'
                f'<span style="color:{C_TEXT3};font-size:11px;text-align:right;">{ago}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            with st.expander(f"Summary — {a['headline'][:60]}…", expanded=False):
                ent_str = ", ".join(a["entities"]) if a["entities"] else "None identified"
                st.markdown(
                    f'<div style="background:{C_BG};border-radius:8px;padding:14px 18px;">'
                    f'<p style="color:{C_TEXT};font-size:13px;line-height:1.7;margin:0 0 12px 0;">'
                    f'{a["summary"]}</p>'
                    f'<div style="color:{C_TEXT3};font-size:11px;">'
                    f'<strong style="color:{C_TEXT2};">Entities mentioned:</strong> {ent_str}</div>'
                    f'<div style="margin-top:10px;">'
                    f'<a href="{a["url"]}" style="color:{C_ACCENT};font-size:12px;">Read full article ↗</a>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception as exc:
        st.warning(f"News feed error: {exc}")

# ── Section 5: Named Entity Tracker ───────────────────────────────────────────

def _render_entity_tracker(articles: list[dict], entities: list[dict]) -> None:
    try:
        # Augment with live entity counts if we have articles
        entity_counts: Counter = Counter()
        entity_sentiments: dict[str, list[float]] = defaultdict(list)
        for a in articles:
            for ent in a["entities"]:
                entity_counts[ent] += 1
                entity_sentiments[ent].append(a["sentiment_score"])

        # Merge with _MOCK_ENTITIES
        seen = set()
        rows = []
        for e in entities:
            name = e["entity"]
            seen.add(name)
            mentions = entity_counts.get(name, e["mentions"])
            scores   = entity_sentiments.get(name, [e["sentiment"]])
            avg_sent = sum(scores) / len(scores) if scores else e["sentiment"]
            trend    = e["trend"]
            rows.append((name, e["type"], mentions, avg_sent, trend))
        for name, cnt in entity_counts.most_common(20):
            if name not in seen:
                scores   = entity_sentiments[name]
                avg_sent = sum(scores) / len(scores) if scores else 0.0
                rows.append((name, "—", cnt, avg_sent, "flat"))

        rows.sort(key=lambda r: r[2], reverse=True)

        # Header
        st.markdown(
            f'<div style="display:grid;grid-template-columns:140px 100px 80px 90px 60px;'
            f'gap:8px;padding:6px 14px;border-bottom:1px solid {C_BORDER};'
            f'color:{C_TEXT3};font-size:10px;font-weight:700;letter-spacing:0.8px;'
            f'text-transform:uppercase;">'
            f'<span>Entity</span><span>Type</span>'
            f'<span style="text-align:center;">Mentions</span>'
            f'<span style="text-align:center;">Sentiment</span>'
            f'<span style="text-align:center;">Trend</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        for i, (name, etype, mentions, avg_sent, trend) in enumerate(rows[:15]):
            label, lcolor = _sentiment_label(avg_sent)
            sign          = "+" if avg_sent > 0 else ""
            sent_str      = f"{sign}{avg_sent:.2f}"
            trend_icon    = "▲" if trend == "up" else ("▼" if trend == "down" else "●")
            trend_color   = C_HIGH if trend == "up" else (C_LOW if trend == "down" else C_TEXT3)
            row_bg        = C_CARD if i % 2 == 0 else C_SURFACE

            st.markdown(
                f'<div style="display:grid;grid-template-columns:140px 100px 80px 90px 60px;'
                f'gap:8px;padding:9px 14px;background:{row_bg};'
                f'border-radius:6px;align-items:center;margin-bottom:2px;">'
                f'<span style="color:{C_TEXT};font-size:13px;font-weight:600;">{name}</span>'
                f'<span style="color:{C_TEXT2};font-size:12px;">{etype}</span>'
                f'<span style="color:{C_ACCENT};font-size:13px;font-weight:700;'
                f'text-align:center;display:block;">{mentions}</span>'
                f'<code style="background:{lcolor}22;color:{lcolor};border-radius:4px;'
                f'padding:1px 5px;font-size:11px;display:block;text-align:center;">{sent_str}</code>'
                f'<span style="color:{trend_color};font-size:14px;font-weight:700;'
                f'text-align:center;display:block;">{trend_icon}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception as exc:
        st.warning(f"Entity tracker error: {exc}")

# ── Section 6: Geographic Sentiment Map ───────────────────────────────────────

def _render_geo_map(articles: list[dict]) -> None:
    try:
        # Prefer live data; fall back to mock
        geo = _GEO_DATA[:]

        # Build regional sentiment from articles if possible
        region_scores: dict[str, list[float]] = defaultdict(list)
        region_vol: dict[str, int] = defaultdict(int)
        for a in articles:
            for g in geo:
                if g["region"].lower() in a["headline"].lower() or g["region"].lower() in a["summary"].lower():
                    region_scores[g["region"]].append(a["sentiment_score"])
                    region_vol[g["region"]] += 1

        for g in geo:
            if region_scores[g["region"]]:
                sc = region_scores[g["region"]]
                g["sentiment"] = sum(sc) / len(sc)
                g["volume"]    = max(g["volume"], region_vol[g["region"]])

        lats   = [g["lat"]       for g in geo]
        lons   = [g["lon"]       for g in geo]
        sents  = [g["sentiment"] for g in geo]
        vols   = [g["volume"]    for g in geo]
        labels = [g["region"]    for g in geo]

        colors = [C_HIGH if s >= 0.15 else (C_LOW if s <= -0.15 else C_MOD) for s in sents]
        sizes  = [max(14, min(50, v // 2)) for v in vols]
        signs  = ["+" if s > 0 else "" for s in sents]
        texts  = [
            f"{labels[i]}<br>Sentiment: {signs[i]}{sents[i]:.2f}<br>Volume: {vols[i]} articles"
            for i in range(len(geo))
        ]

        fig = go.Figure()
        fig.add_trace(go.Scattergeo(
            lat=lats,
            lon=lons,
            mode="markers+text",
            marker=dict(
                size=sizes,
                color=sents,
                colorscale=[[0, C_LOW], [0.5, C_MOD], [1, C_HIGH]],
                cmin=-1,
                cmax=1,
                colorbar=dict(
                    title="Sentiment",
                    thickness=12,
                    len=0.6,
                    tickfont=dict(color=C_TEXT2, size=10),
                    titlefont=dict(color=C_TEXT2, size=11),
                ),
                line=dict(width=1, color=C_BORDER),
                opacity=0.88,
            ),
            text=[l[:12] for l in labels],
            textposition="top center",
            textfont=dict(color=C_TEXT2, size=9),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=texts,
        ))

        fig.update_layout(
            geo=dict(
                showframe=False,
                showcoastlines=True,
                coastlinecolor=C_BORDER,
                showland=True,
                landcolor=C_SURFACE,
                showocean=True,
                oceancolor=C_BG,
                showlakes=False,
                showcountries=True,
                countrycolor=C_BORDER,
                bgcolor=C_BG,
                projection_type="natural earth",
            ),
            paper_bgcolor=C_BG,
            plot_bgcolor=C_BG,
            font=dict(color=C_TEXT2),
            margin=dict(l=0, r=0, t=10, b=0),
            height=400,
        )

        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    except Exception as exc:
        st.warning(f"Geo map error: {exc}")

# ── Main entry point ───────────────────────────────────────────────────────────

def render(news_items: list[dict] | None = None, insights: Any = None) -> None:
    """Render the Shipping News Intelligence tab."""

    # ── Normalise & fallback ──────────────────────────────────────────────────
    try:
        raw = news_items if news_items else []
        articles = _normalise(raw)
        if not articles:
            articles = _normalise(_MOCK_ARTICLES)
            using_mock = True
        else:
            using_mock = False
    except Exception:
        articles   = _normalise(_MOCK_ARTICLES)
        using_mock = True

    # ── Page header ───────────────────────────────────────────────────────────
    try:
        updated = max((a["published_at"] for a in articles), default=_now())
        updated_str = updated.strftime("%d %b %Y %H:%M UTC")
        mock_badge = (
            f'<span style="background:{C_MOD}22;color:{C_MOD};border:1px solid {C_MOD}44;'
            f'border-radius:4px;padding:1px 8px;font-size:10px;font-weight:700;'
            f'margin-left:10px;">DEMO DATA</span>'
            if using_mock else ""
        )
        st.markdown(
            f'<div style="display:flex;align-items:baseline;gap:12px;'
            f'margin-bottom:22px;flex-wrap:wrap;">'
            f'<h2 style="color:{C_TEXT};font-size:22px;font-weight:800;margin:0;'
            f'letter-spacing:-0.3px;">Shipping News Intelligence</h2>'
            f'{mock_badge}'
            f'<span style="color:{C_TEXT3};font-size:11px;margin-left:auto;">'
            f'Last updated {updated_str}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        st.subheader("Shipping News Intelligence")

    # ── 1. Sentiment Pulse ────────────────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;letter-spacing:1.2px;'
            f'text-transform:uppercase;margin-bottom:10px;padding-left:2px;">'
            f'<span style="color:{C_ACCENT};">01</span>&nbsp;&nbsp;Sentiment Pulse</div>',
            unsafe_allow_html=True,
        )
        _render_sentiment_pulse(articles)
    except Exception as exc:
        st.warning(f"Section 1 error: {exc}")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── 2. Topic Heatmap ──────────────────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;letter-spacing:1.2px;'
            f'text-transform:uppercase;margin-bottom:10px;padding-left:2px;">'
            f'<span style="color:{C_ACCENT};">02</span>&nbsp;&nbsp;Topic Heatmap — '
            f'9 Topics × 5 Days</div>',
            unsafe_allow_html=True,
        )
        _render_topic_heatmap(articles)
    except Exception as exc:
        st.warning(f"Section 2 error: {exc}")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── 3. Breaking News ──────────────────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;letter-spacing:1.2px;'
            f'text-transform:uppercase;margin-bottom:10px;padding-left:2px;">'
            f'<span style="color:{C_LOW};">03</span>&nbsp;&nbsp;Breaking News — Top 5 Urgent</div>',
            unsafe_allow_html=True,
        )
        _render_breaking_news(articles)
    except Exception as exc:
        st.warning(f"Section 3 error: {exc}")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── 4. Full News Feed ─────────────────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;letter-spacing:1.2px;'
            f'text-transform:uppercase;margin-bottom:10px;padding-left:2px;">'
            f'<span style="color:{C_ACCENT};">04</span>&nbsp;&nbsp;Full News Feed</div>',
            unsafe_allow_html=True,
        )
        _render_news_feed(articles)
    except Exception as exc:
        st.warning(f"Section 4 error: {exc}")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── 5. Named Entity Tracker ───────────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;letter-spacing:1.2px;'
            f'text-transform:uppercase;margin-bottom:10px;padding-left:2px;">'
            f'<span style="color:{C_ACCENT};">05</span>&nbsp;&nbsp;Named Entity Tracker</div>',
            unsafe_allow_html=True,
        )
        _render_entity_tracker(articles, _MOCK_ENTITIES)
    except Exception as exc:
        st.warning(f"Section 5 error: {exc}")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── 6. Geographic Sentiment Map ───────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="color:{C_TEXT2};font-size:11px;font-weight:700;letter-spacing:1.2px;'
            f'text-transform:uppercase;margin-bottom:10px;padding-left:2px;">'
            f'<span style="color:{C_ACCENT};">06</span>&nbsp;&nbsp;Geographic Sentiment Map</div>',
            unsafe_allow_html=True,
        )
        _render_geo_map(articles)
    except Exception as exc:
        st.warning(f"Section 6 error: {exc}")

    # ── Footer ────────────────────────────────────────────────────────────────
    try:
        st.markdown(
            f'<div style="text-align:center;color:{C_TEXT3};font-size:11px;'
            f'margin-top:32px;padding-top:16px;border-top:1px solid {C_BORDER};">'
            f'Shipping News Intelligence &nbsp;|&nbsp; '
            f'{len(articles)} articles indexed &nbsp;|&nbsp; '
            f'Sentiment scored by NLP pipeline'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass
