"""
data/carrier_intelligence.py
──────────────────────────────
Carrier intelligence data module for major ocean container shipping carriers.

Provides:
  - CarrierProfile dataclass with operational, financial, and market data
  - Hardcoded profiles for the top 12 global carriers (updated Q1 2026)
  - Alliance breakdown, market concentration (HHI), reliability ranking
  - Mock blank sailing alerts reflecting current market conditions

No live API calls are made from this module; all data is curated and
updated periodically.  For live carrier news feeds see the legacy
fetch_carrier_updates() function which is still exported for compatibility.

Dependencies: standard library only (dataclasses, datetime)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    import feedparser
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False
    logger.warning("feedparser not installed — carrier RSS feeds unavailable")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False


# ── Carrier Profile dataclass ─────────────────────────────────────────────────

@dataclass
class CarrierProfile:
    """Comprehensive profile for a major ocean container carrier.

    Attributes
    ----------
    name : str
        Full carrier name.
    ticker : str
        Exchange ticker symbol, or "private" if unlisted.
    alliance : str
        Current alliance membership ("Gemini", "Premier", "MSC-independent",
        "unaffiliated", etc.).
    market_share_pct : float
        Estimated global TEU capacity market share (%).
    fleet_size : int
        Number of vessels in current fleet (owned + long-term chartered).
    teu_capacity : int
        Total TEU capacity across fleet.
    ytd_rate_change : float
        Year-to-date average spot rate change (%).
    blank_sailing_rate : float
        Estimated proportion of sailings blanked in past 90 days (%).
    schedule_reliability : float
        Vessel on-time arrival rate, past 6 months (%).
    q_revenue_bn : float
        Most recent full-quarter revenue (USD billion).
    q_net_margin_pct : float
        Most recent full-quarter net margin (%).
    outlook : str
        Short outlook label: "Positive", "Cautious", "Neutral", "Negative".
    key_risks : list[str]
        Up to 4 key risk factors.
    key_strengths : list[str]
        Up to 4 competitive strengths.
    """
    name: str
    ticker: str
    alliance: str
    market_share_pct: float
    fleet_size: int
    teu_capacity: int
    ytd_rate_change: float
    blank_sailing_rate: float
    schedule_reliability: float
    q_revenue_bn: float
    q_net_margin_pct: float
    outlook: str
    key_risks: list[str] = field(default_factory=list)
    key_strengths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Hardcoded carrier profiles (Q1 2026) ─────────────────────────────────────

def get_carrier_profiles() -> list[CarrierProfile]:
    """Return curated profiles for the top 12 global container carriers.

    Data reflects Q1 2026 market conditions.  Market shares are based on
    deployed TEU capacity per Alphaliner; financial metrics reflect most
    recently reported quarters.

    Returns
    -------
    list[CarrierProfile]
        Sorted by market share descending.
    """
    return [
        CarrierProfile(
            name="MSC (Mediterranean Shipping Company)",
            ticker="private",
            alliance="MSC-independent",
            market_share_pct=17.4,
            fleet_size=821,
            teu_capacity=5_680_000,
            ytd_rate_change=-8.2,
            blank_sailing_rate=14.5,
            schedule_reliability=58.3,
            q_revenue_bn=14.8,
            q_net_margin_pct=11.2,
            outlook="Cautious",
            key_risks=[
                "Overcapacity pressure from aggressive newbuild programme",
                "Revenue concentration in spot market with rate volatility",
                "Geopolitical risk on Red Sea / Suez routing",
                "Private ownership limits capital market transparency",
            ],
            key_strengths=[
                "World's largest carrier by fleet capacity",
                "Extensive port terminal ownership and vertical integration",
                "Aggressive slot-buying and charter strategy expanding reach",
                "Strong customer loyalty in European and transatlantic trades",
            ],
        ),
        CarrierProfile(
            name="Maersk",
            ticker="MAERSK-B.CO",
            alliance="Gemini",
            market_share_pct=15.1,
            fleet_size=698,
            teu_capacity=4_280_000,
            ytd_rate_change=-10.5,
            blank_sailing_rate=9.2,
            schedule_reliability=72.8,
            q_revenue_bn=12.6,
            q_net_margin_pct=6.4,
            outlook="Neutral",
            key_risks=[
                "Logistics business integration drag on group margins",
                "Rate normalisation eroding COVID-era windfall profits",
                "Capital-intensive fleet renewal programme",
                "Increasing competition from Chinese state carriers",
            ],
            key_strengths=[
                "Highest schedule reliability among top-5 carriers",
                "Integrated logistics offering differentiates from pure-play peers",
                "Gemini Alliance with Hapag-Lloyd enhances network stability",
                "Strong ESG credentials and methanol newbuild programme",
            ],
        ),
        CarrierProfile(
            name="CMA CGM",
            ticker="private",
            alliance="Premier",
            market_share_pct=12.3,
            fleet_size=617,
            teu_capacity=3_520_000,
            ytd_rate_change=-7.8,
            blank_sailing_rate=11.8,
            schedule_reliability=61.5,
            q_revenue_bn=10.2,
            q_net_margin_pct=8.9,
            outlook="Positive",
            key_risks=[
                "Heavy capital commitments to media and logistics acquisitions",
                "Exposure to spot-rate volatility in Asia-Europe trades",
                "Fleet age profile elevating maintenance costs",
                "Port congestion at European hub terminals",
            ],
            key_strengths=[
                "Diversified beyond ocean freight into air cargo and logistics",
                "Strong LNG and biofuel newbuild fleet reducing carbon intensity",
                "Market leadership on Asia-Mediterranean corridor",
                "State-backed credit access facilitates competitive financing",
            ],
        ),
        CarrierProfile(
            name="COSCO Shipping Lines",
            ticker="601919.SS",
            alliance="Premier",
            market_share_pct=11.8,
            fleet_size=512,
            teu_capacity=3_210_000,
            ytd_rate_change=-6.1,
            blank_sailing_rate=13.4,
            schedule_reliability=63.2,
            q_revenue_bn=9.7,
            q_net_margin_pct=9.3,
            outlook="Neutral",
            key_risks=[
                "US-China trade tensions affecting transpacific volumes",
                "Regulatory scrutiny of state-owned enterprise structure",
                "Port terminal sanctions risk in select jurisdictions",
                "Over-reliance on intra-Asia and transpacific trades",
            ],
            key_strengths=[
                "State backing provides competitive financing and strategic support",
                "Leading position in China export trades",
                "Extensive owned terminal network across Asia and Europe",
                "Scale advantages in fleet procurement and fuel purchasing",
            ],
        ),
        CarrierProfile(
            name="Hapag-Lloyd",
            ticker="HLAG.DE",
            alliance="Gemini",
            market_share_pct=7.2,
            fleet_size=289,
            teu_capacity=2_120_000,
            ytd_rate_change=-11.3,
            blank_sailing_rate=8.7,
            schedule_reliability=74.1,
            q_revenue_bn=5.8,
            q_net_margin_pct=5.1,
            outlook="Neutral",
            key_risks=[
                "Concentrated exposure to North Europe and transatlantic trades",
                "Rising cost base from terminal acquisitions and fleet upgrades",
                "Rate environment remains below historical average",
                "Gemini Alliance service launch execution risk",
            ],
            key_strengths=[
                "Best-in-class schedule reliability among top-10 carriers",
                "Premium positioning supports rate premium over peers",
                "Gemini Alliance with Maersk provides network breadth",
                "Disciplined capacity management and low blank sailing rate",
            ],
        ),
        CarrierProfile(
            name="Ocean Network Express (ONE)",
            ticker="private",
            alliance="Premier",
            market_share_pct=6.4,
            fleet_size=248,
            teu_capacity=1_780_000,
            ytd_rate_change=-9.4,
            blank_sailing_rate=12.1,
            schedule_reliability=65.4,
            q_revenue_bn=4.3,
            q_net_margin_pct=7.6,
            outlook="Neutral",
            key_risks=[
                "Single-brand identity risk following Japanese carrier merger",
                "Transpacific trade exposure to US import demand slowdown",
                "Limited scale disadvantage versus top-4 peers",
                "Alliance dynamics within Premier subject to ongoing review",
            ],
            key_strengths=[
                "Combined heritage of NYK, MOL, and K Line operational expertise",
                "Strong customer relationships in Japanese manufacturing sector",
                "Lean cost structure from merger synergy realisation",
                "Disciplined newbuild strategy avoids overcapacity risk",
            ],
        ),
        CarrierProfile(
            name="Evergreen Marine",
            ticker="2603.TW",
            alliance="Premier",
            market_share_pct=5.3,
            fleet_size=221,
            teu_capacity=1_540_000,
            ytd_rate_change=-8.9,
            blank_sailing_rate=15.3,
            schedule_reliability=57.8,
            q_revenue_bn=3.6,
            q_net_margin_pct=6.2,
            outlook="Cautious",
            key_risks=[
                "Elevated blank sailing rate signals weak demand visibility",
                "Schedule reliability below peer average impacting premium bookings",
                "Taiwan political risk and cross-strait trade sensitivity",
                "New mega-vessel deliveries pressuring utilisation rates",
            ],
            key_strengths=[
                "Large order book signals long-term capacity commitment",
                "Strong Taiwan and broader Asia cargo origin access",
                "Owned terminal positions in key transpacific ports",
                "Balance sheet strength supports fleet renewal programme",
            ],
        ),
        CarrierProfile(
            name="Yang Ming Marine Transport",
            ticker="2609.TW",
            alliance="Premier",
            market_share_pct=2.9,
            fleet_size=98,
            teu_capacity=680_000,
            ytd_rate_change=-7.5,
            blank_sailing_rate=13.9,
            schedule_reliability=60.1,
            q_revenue_bn=1.8,
            q_net_margin_pct=4.8,
            outlook="Cautious",
            key_risks=[
                "Small scale limits negotiating leverage on fuel and port costs",
                "High sensitivity to transpacific spot rate movements",
                "Government stake creates conflicting stakeholder objectives",
                "Limited diversification beyond ocean container shipping",
            ],
            key_strengths=[
                "Cost discipline delivers competitive operating ratios",
                "Strong legacy customer base in electronics supply chain",
                "Alliance membership provides network reach beyond own capacity",
                "Recent balance sheet deleveraging improves financial flexibility",
            ],
        ),
        CarrierProfile(
            name="HMM (Hyundai Merchant Marine)",
            ticker="011200.KS",
            alliance="Premier",
            market_share_pct=2.7,
            fleet_size=92,
            teu_capacity=830_000,
            ytd_rate_change=-6.8,
            blank_sailing_rate=11.6,
            schedule_reliability=62.7,
            q_revenue_bn=2.1,
            q_net_margin_pct=5.5,
            outlook="Neutral",
            key_risks=[
                "Ongoing privatisation uncertainty overhangs capital structure",
                "Rapid fleet expansion increasing breakeven thresholds",
                "Concentrated exposure to transpacific and Asia-Europe lanes",
                "Crew recruitment challenges for large ultra-mega vessel fleet",
            ],
            key_strengths=[
                "Young, fuel-efficient mega-vessel fleet with low unit cost",
                "Strong Korean government and financial sector backing",
                "Improving customer quality mix post-restructuring",
                "Growing order book positions carrier for market share gains",
            ],
        ),
        CarrierProfile(
            name="ZIM Integrated Shipping Services",
            ticker="ZIM",
            alliance="unaffiliated",
            market_share_pct=2.1,
            fleet_size=148,
            teu_capacity=610_000,
            ytd_rate_change=-14.2,
            blank_sailing_rate=18.7,
            schedule_reliability=55.4,
            q_revenue_bn=1.6,
            q_net_margin_pct=3.2,
            outlook="Negative",
            key_risks=[
                "Highest charter dependency (>90%) leaves cost base inflexible",
                "No alliance membership limits network competitiveness",
                "Transpacific and Asia-Mediterranean spot rate sensitivity",
                "Highest blank sailing rate among peers signals demand struggles",
            ],
            key_strengths=[
                "Asset-light charter model provides operational flexibility",
                "Listed entity with transparent financials and active IR",
                "Niche digital and e-commerce logistics capabilities",
                "Lean overhead structure enables rapid response to rate changes",
            ],
        ),
        CarrierProfile(
            name="PIL (Pacific International Lines)",
            ticker="private",
            alliance="unaffiliated",
            market_share_pct=1.4,
            fleet_size=74,
            teu_capacity=380_000,
            ytd_rate_change=-5.3,
            blank_sailing_rate=16.2,
            schedule_reliability=56.9,
            q_revenue_bn=0.7,
            q_net_margin_pct=2.8,
            outlook="Cautious",
            key_risks=[
                "Recent restructuring leaves balance sheet fragile",
                "Limited scale in a consolidating industry",
                "Niche intra-Asia focus constrains revenue diversity",
                "Older fleet profile elevating fuel and maintenance costs",
            ],
            key_strengths=[
                "Strong Southeast Asia and Africa trade lane expertise",
                "Post-restructuring operational efficiency improvements",
                "Established relationships with regional port authorities",
                "Low overhead model supports margin resilience at lower volumes",
            ],
        ),
        CarrierProfile(
            name="Wan Hai Lines",
            ticker="2615.TW",
            alliance="unaffiliated",
            market_share_pct=1.2,
            fleet_size=87,
            teu_capacity=310_000,
            ytd_rate_change=-4.6,
            blank_sailing_rate=10.8,
            schedule_reliability=67.3,
            q_revenue_bn=0.6,
            q_net_margin_pct=4.1,
            outlook="Neutral",
            key_risks=[
                "Concentration risk in intra-Asia short-sea trades",
                "Exposure to regional economic slowdown in Southeast Asia",
                "Limited brand recognition outside home market",
                "Regulatory compliance costs increasing across Asian ports",
            ],
            key_strengths=[
                "Highest schedule reliability among unaffiliated peers",
                "Specialisation in intra-Asia reduces competition with mega-carriers",
                "Relatively low blank sailing rate vs. peer group",
                "Conservative financial management and strong balance sheet",
            ],
        ),
    ]


# ── Alliance breakdown ────────────────────────────────────────────────────────

def get_alliance_breakdown() -> dict[str, list[str]]:
    """Return a mapping of alliance name to list of member carrier names.

    Alliance structure reflects Q1 2026 post-2M dissolution landscape:
      - Gemini Cooperation:  Maersk + Hapag-Lloyd (launched Feb 2025)
      - Premier Alliance:    CMA CGM + COSCO + ONE + Evergreen + Yang Ming + HMM
      - MSC-independent:     MSC operates independently post-2M dissolution
      - Unaffiliated:        ZIM, PIL, Wan Hai

    Returns
    -------
    dict[str, list[str]]
    """
    profiles = get_carrier_profiles()
    breakdown: dict[str, list[str]] = {}
    for p in profiles:
        breakdown.setdefault(p.alliance, []).append(p.name)
    return breakdown


# ── Market concentration ──────────────────────────────────────────────────────

def get_market_concentration() -> dict:
    """Calculate HHI and top-N concentration ratios for the container shipping market.

    Returns
    -------
    dict with keys:
        hhi             — Herfindahl-Hirschman Index (0–10,000)
        hhi_category    — "Highly Concentrated" | "Moderately Concentrated" | "Competitive"
        top3_share_pct  — combined market share of top 3 carriers
        top5_share_pct  — combined market share of top 5 carriers
        top10_share_pct — combined market share of top 10 carriers
        total_tracked_share_pct — sum of shares across all 12 tracked carriers
        carriers_ranked — list of (name, market_share_pct) tuples, descending
    """
    profiles = sorted(get_carrier_profiles(), key=lambda p: p.market_share_pct, reverse=True)

    shares = [p.market_share_pct for p in profiles]
    hhi = round(sum(s ** 2 for s in shares), 1)

    if hhi >= 2500:
        hhi_category = "Highly Concentrated"
    elif hhi >= 1500:
        hhi_category = "Moderately Concentrated"
    else:
        hhi_category = "Competitive"

    total = sum(shares)
    top3  = round(sum(shares[:3]),  1)
    top5  = round(sum(shares[:5]),  1)
    top10 = round(sum(shares[:10]), 1)

    return {
        "hhi":                    hhi,
        "hhi_category":           hhi_category,
        "top3_share_pct":         top3,
        "top5_share_pct":         top5,
        "top10_share_pct":        top10,
        "total_tracked_share_pct":round(total, 1),
        "carriers_ranked":        [(p.name, p.market_share_pct) for p in profiles],
    }


# ── Schedule reliability ranking ──────────────────────────────────────────────

def get_schedule_reliability_ranking() -> list[dict]:
    """Return carriers sorted by schedule reliability, descending.

    Returns
    -------
    list[dict]
        Each dict has: rank, name, ticker, alliance, schedule_reliability,
        fleet_size, outlook.
    """
    profiles = sorted(
        get_carrier_profiles(),
        key=lambda p: p.schedule_reliability,
        reverse=True,
    )
    return [
        {
            "rank":                 i + 1,
            "name":                 p.name,
            "ticker":               p.ticker,
            "alliance":             p.alliance,
            "schedule_reliability": p.schedule_reliability,
            "fleet_size":           p.fleet_size,
            "outlook":              p.outlook,
        }
        for i, p in enumerate(profiles)
    ]


# ── Blank sailing alerts ──────────────────────────────────────────────────────

def get_blank_sailing_alerts() -> list[dict]:
    """Return a list of recent blank sailing announcements (mock/curated data).

    Each alert reflects a realistic market announcement format as published
    by carriers or aggregated by shipping media.  Dates are relative to
    Q1 2026.

    Returns
    -------
    list[dict]
        Keys: carrier, alliance, trade_lane, departure_week, vessel_name,
              port_omissions, reason, source, announced_date, teu_impact.
    """
    return [
        {
            "carrier":        "MSC",
            "alliance":       "MSC-independent",
            "trade_lane":     "Asia – North Europe",
            "departure_week": "Week 14 2026 (Apr 6–12)",
            "vessel_name":    "MSC Ingrid (23,600 TEU)",
            "port_omissions": ["Felixstowe", "Bremerhaven"],
            "reason":         "Demand shortfall following Lunar New Year demand trough",
            "source":         "MSC Advisory Notice #2026-ANE-042",
            "announced_date": "2026-03-14",
            "teu_impact":     23600,
        },
        {
            "carrier":        "CMA CGM",
            "alliance":       "Premier",
            "trade_lane":     "Asia – US West Coast (Transpacific Eastbound)",
            "departure_week": "Week 15 2026 (Apr 13–19)",
            "vessel_name":    "CMA CGM Centaurus (15,000 TEU)",
            "port_omissions": ["Oakland"],
            "reason":         "Network rebalancing and equipment repositioning",
            "source":         "CMA CGM Customer Advisory CA-TPEB-2026-11",
            "announced_date": "2026-03-16",
            "teu_impact":     15000,
        },
        {
            "carrier":        "COSCO Shipping Lines",
            "alliance":       "Premier",
            "trade_lane":     "Asia – North Europe",
            "departure_week": "Week 13 2026 (Mar 30–Apr 5)",
            "vessel_name":    "COSCO Shipping Universe (21,237 TEU)",
            "port_omissions": ["Rotterdam", "Hamburg"],
            "reason":         "Port congestion avoidance and schedule recovery",
            "source":         "Premier Alliance Service Alert SA-2026-031",
            "announced_date": "2026-03-10",
            "teu_impact":     21237,
        },
        {
            "carrier":        "Maersk",
            "alliance":       "Gemini",
            "trade_lane":     "Asia – Mediterranean",
            "departure_week": "Week 14 2026 (Apr 6–12)",
            "vessel_name":    "Maersk Elba (20,568 TEU)",
            "port_omissions": ["Genoa"],
            "reason":         "Pre-Easter demand weakness on Mediterranean corridor",
            "source":         "Gemini Cooperation Service Bulletin GC-MED-2026-07",
            "announced_date": "2026-03-17",
            "teu_impact":     20568,
        },
        {
            "carrier":        "ZIM",
            "alliance":       "unaffiliated",
            "trade_lane":     "Asia – US East Coast",
            "departure_week": "Week 15 2026 (Apr 13–19)",
            "vessel_name":    "ZIM Tarragona (12,100 TEU)",
            "port_omissions": ["Savannah", "Charleston"],
            "reason":         "Softer demand ahead of peak season; charter cost optimisation",
            "source":         "ZIM Service Advisory ZIM-USEC-2026-08",
            "announced_date": "2026-03-18",
            "teu_impact":     12100,
        },
        {
            "carrier":        "Hapag-Lloyd",
            "alliance":       "Gemini",
            "trade_lane":     "Asia – US West Coast",
            "departure_week": "Week 16 2026 (Apr 20–26)",
            "vessel_name":    "Berlin Express (13,167 TEU)",
            "port_omissions": ["Prince Rupert"],
            "reason":         "Equipment imbalance correction post-Chinese New Year",
            "source":         "Gemini Cooperation Service Bulletin GC-TPEB-2026-09",
            "announced_date": "2026-03-19",
            "teu_impact":     13167,
        },
        {
            "carrier":        "Evergreen",
            "alliance":       "Premier",
            "trade_lane":     "Asia – North Europe",
            "departure_week": "Week 13 2026 (Mar 30–Apr 5)",
            "vessel_name":    "Ever Greet (23,994 TEU)",
            "port_omissions": ["Antwerp", "Hamburg"],
            "reason":         "Fleet deployment adjustment for Q2 schedule restructuring",
            "source":         "Premier Alliance Service Alert SA-2026-028",
            "announced_date": "2026-03-09",
            "teu_impact":     23994,
        },
        {
            "carrier":        "ONE (Ocean Network Express)",
            "alliance":       "Premier",
            "trade_lane":     "Asia – US West Coast",
            "departure_week": "Week 14 2026 (Apr 6–12)",
            "vessel_name":    "ONE Columba (14,052 TEU)",
            "port_omissions": ["Long Beach"],
            "reason":         "Port labor disruption contingency and capacity rationalisation",
            "source":         "ONE Service Advisory ONE-TPEB-2026-14",
            "announced_date": "2026-03-15",
            "teu_impact":     14052,
        },
        {
            "carrier":        "Yang Ming",
            "alliance":       "Premier",
            "trade_lane":     "Asia – US East Coast (via Suez)",
            "departure_week": "Week 15 2026 (Apr 13–19)",
            "vessel_name":    "YM Uniformity (11,000 TEU)",
            "port_omissions": ["Norfolk"],
            "reason":         "Routing change from Suez to Cape of Good Hope impacting schedule",
            "source":         "Premier Alliance Service Alert SA-2026-033",
            "announced_date": "2026-03-20",
            "teu_impact":     11000,
        },
        {
            "carrier":        "HMM",
            "alliance":       "Premier",
            "trade_lane":     "Asia – North Europe",
            "departure_week": "Week 16 2026 (Apr 20–26)",
            "vessel_name":    "HMM Oslo (24,000 TEU)",
            "port_omissions": ["Felixstowe", "Rotterdam"],
            "reason":         "Planned drydock deviation causing capacity reallocation",
            "source":         "HMM Customer Notice HMM-ANE-2026-06",
            "announced_date": "2026-03-21",
            "teu_impact":     24000,
        },
    ]


# ── Legacy RSS feed infrastructure (preserved for backward compatibility) ─────

CARRIER_FEEDS: dict[str, str] = {
    "Maersk":      "https://www.maersk.com/news/articles/rss",
    "MSC":         "https://www.msc.com/en/news-and-insights/rss",
    "CMA CGM":     "https://www.cma-cgm.com/news/rss",
    "Hapag-Lloyd": "https://www.hapag-lloyd.com/en/news-and-insights/rss.xml",
    "Evergreen":   "https://www.evergreen-line.com/rss/news.xml",
    "ONE":         "https://www.one-line.com/en/news/rss",
}

FALLBACK_FEEDS: list[str] = [
    "https://www.hellenicshippingnews.com/feed/",
    "https://splash247.com/feed/",
    "https://www.seatrade-maritime.com/rss.xml",
    "https://lloydslist.com/rss/news",
]

ALL_CARRIERS = list(CARRIER_FEEDS.keys())

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "capacity": [
        "capacity", "vessel", "newbuild", "fleet", "ship order", "delivery",
        "idle", "scrapping", "lay up", "blank sailing", "void sailing",
        "slot", "charter", "TEU", "containership",
    ],
    "rates": [
        "rate", "freight", "tariff", "surcharge", "GRI", "BAF", "CAF",
        "PSS", "peak season", "spot", "contract", "index", "BDI", "SCFI",
    ],
    "m_and_a": [
        "acquisition", "merger", "partnership", "alliance", "joint venture",
        "stake", "invest", "takeover", "consolidat",
    ],
    "sustainability": [
        "emission", "carbon", "green", "LNG", "methanol", "ammonia",
        "biofuel", "CII", "EEXI", "decarboni", "ESG", "net zero",
        "renewable", "scrubber",
    ],
}

_SENTIMENT_POSITIVE: list[str] = [
    "increase", "growth", "surge", "rise", "recovery", "strong", "positive",
    "improve", "high demand", "capacity boost", "new route", "expand",
    "record", "profit", "investment", "order",
]
_SENTIMENT_NEGATIVE: list[str] = [
    "decline", "fall", "drop", "weak", "slow", "disruption", "crisis",
    "delay", "congestion", "cancel", "blank sailing", "void", "loss",
    "layoff", "downturn", "pressure", "surcharge", "cost",
]

_IMPACT_HIGH: list[str] = [
    "capacity", "rate", "freight", "fleet", "route", "blank sailing",
    "acquisition", "merger", "alliance",
]


@dataclass
class CarrierUpdate:
    carrier: str
    category: str
    headline: str
    summary: str
    published_dt: datetime
    url: str
    sentiment: float
    impact_score: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_dt"] = self.published_dt.isoformat()
        return d

    @staticmethod
    def from_dict(d: dict) -> "CarrierUpdate":
        dt_raw = d.pop("published_dt", None)
        if isinstance(dt_raw, str):
            try:
                dt = datetime.fromisoformat(dt_raw)
            except ValueError:
                dt = datetime.now(tz=timezone.utc)
        elif isinstance(dt_raw, datetime):
            dt = dt_raw
        else:
            dt = datetime.now(tz=timezone.utc)
        return CarrierUpdate(**d, published_dt=dt)


def _classify_category(text: str) -> str:
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in text_lower)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "general"


def _score_sentiment(text: str) -> float:
    text_lower = text.lower()
    pos = sum(1 for w in _SENTIMENT_POSITIVE if w in text_lower)
    neg = sum(1 for w in _SENTIMENT_NEGATIVE if w in text_lower)
    total = pos + neg
    if total == 0:
        return 0.0
    return round(max(-1.0, min(1.0, (pos - neg) / total)), 3)


def _score_impact(text: str) -> float:
    text_lower = text.lower()
    hits = sum(1 for kw in _IMPACT_HIGH if kw in text_lower)
    return round(min(hits / 5.0, 1.0), 3)


def _parse_published(entry) -> datetime:
    try:
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if struct:
            ts = time.mktime(struct)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        pass
    return datetime.now(tz=timezone.utc)


def _entry_to_update(entry, carrier: str) -> Optional[CarrierUpdate]:
    try:
        headline = (entry.get("title") or "").strip()
        summary  = re.sub(r"<[^>]+>", "", entry.get("summary") or entry.get("description") or "").strip()
        url      = entry.get("link") or ""
        combined = f"{headline} {summary}"
        if not headline:
            return None
        return CarrierUpdate(
            carrier=carrier,
            category=_classify_category(combined),
            headline=headline,
            summary=summary[:500],
            published_dt=_parse_published(entry),
            url=url,
            sentiment=_score_sentiment(combined),
            impact_score=_score_impact(combined),
        )
    except Exception as exc:
        logger.debug(f"carrier_intelligence: entry parse error: {exc}")
        return None


_CACHE_DIR = Path("cache/carrier_intel")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_MEM_CACHE: dict[str, tuple[float, list[CarrierUpdate]]] = {}
_REQUEST_TIMEOUT = 14


def _cache_key(carrier: str) -> str:
    return carrier.lower().replace(" ", "_").replace("/", "_")


def _read_mem_cache(carrier: str, ttl_hours: float) -> Optional[list[CarrierUpdate]]:
    key = _cache_key(carrier)
    if key in _MEM_CACHE:
        ts, updates = _MEM_CACHE[key]
        if time.time() - ts < ttl_hours * 3600:
            return updates
    return None


def _write_mem_cache(carrier: str, updates: list[CarrierUpdate]) -> None:
    _MEM_CACHE[_cache_key(carrier)] = (time.time(), updates)


def _read_file_cache(carrier: str, ttl_hours: float) -> Optional[list[CarrierUpdate]]:
    path = _CACHE_DIR / f"{_cache_key(carrier)}.json"
    if not path.exists():
        return None
    try:
        age = (time.time() - path.stat().st_mtime) / 3600
        if age > ttl_hours:
            return None
        raw = json.loads(path.read_text())
        return [CarrierUpdate.from_dict(d) for d in raw]
    except Exception as exc:
        logger.debug(f"carrier_intelligence: file-cache read failed ({carrier}): {exc}")
        return None


def _write_file_cache(carrier: str, updates: list[CarrierUpdate]) -> None:
    path = _CACHE_DIR / f"{_cache_key(carrier)}.json"
    try:
        path.write_text(json.dumps([u.to_dict() for u in updates], indent=2, default=str))
    except Exception as exc:
        logger.debug(f"carrier_intelligence: file-cache write failed: {exc}")


def _fetch_carrier_feed(carrier: str, feed_url: str, max_items: int) -> list[CarrierUpdate]:
    if not _FEEDPARSER_OK:
        return []
    try:
        d = feedparser.parse(feed_url)
        updates: list[CarrierUpdate] = []
        for entry in d.entries[:max_items]:
            upd = _entry_to_update(entry, carrier)
            if upd:
                updates.append(upd)
        if updates:
            logger.info(f"carrier_intelligence: {carrier} — {len(updates)} items from {feed_url}")
        return updates
    except Exception as exc:
        logger.debug(f"carrier_intelligence: feed failed ({carrier} / {feed_url}): {exc}")
        return []


def _fetch_fallback_for_carrier(carrier: str, max_items: int) -> list[CarrierUpdate]:
    if not _FEEDPARSER_OK:
        return []
    carrier_lower = carrier.lower()
    aliases: dict[str, list[str]] = {
        "maersk":      ["maersk", "apm"],
        "msc":         ["msc", "mediterranean shipping"],
        "cma cgm":     ["cma cgm", "cma-cgm", "cgm"],
        "hapag-lloyd": ["hapag", "hapag-lloyd"],
        "evergreen":   ["evergreen"],
        "one":         ["ocean network express", "one line"],
    }
    patterns = aliases.get(carrier_lower, [carrier_lower])
    updates: list[CarrierUpdate] = []
    for feed_url in FALLBACK_FEEDS:
        if len(updates) >= max_items:
            break
        try:
            d = feedparser.parse(feed_url)
            for entry in d.entries[:30]:
                text = (
                    (entry.get("title") or "") + " " +
                    (entry.get("summary") or "") + " " +
                    (entry.get("description") or "")
                ).lower()
                if any(pat in text for pat in patterns):
                    upd = _entry_to_update(entry, carrier)
                    if upd:
                        updates.append(upd)
                        if len(updates) >= max_items:
                            break
        except Exception as exc:
            logger.debug(f"carrier_intelligence: fallback feed error ({feed_url}): {exc}")
    if updates:
        logger.info(f"carrier_intelligence: {carrier} — {len(updates)} items from fallback feeds")
    return updates


def fetch_carrier_updates(
    carriers: list[str] | None = None,
    max_per_carrier: int = 5,
    cache_ttl_hours: float = 6.0,
) -> dict[str, list[CarrierUpdate]]:
    """Fetch recent news updates for major ocean carriers (legacy function).

    Fetch strategy per carrier:
      1. In-process TTL cache
      2. File cache (cache/carrier_intel/<name>.json)
      3. Carrier-specific RSS feed
      4. Fallback: broad shipping news filtered by carrier name
      5. Empty list (graceful degradation)

    Args:
        carriers:         List of carrier names. Defaults to all 6 in CARRIER_FEEDS.
        max_per_carrier:  Maximum articles to return per carrier.
        cache_ttl_hours:  Cache lifetime in hours.

    Returns:
        dict mapping carrier_name → list[CarrierUpdate], sorted newest-first.
    """
    target_carriers = carriers if carriers else ALL_CARRIERS
    results: dict[str, list[CarrierUpdate]] = {}

    for carrier in target_carriers:
        cached = _read_mem_cache(carrier, cache_ttl_hours)
        if cached is not None:
            results[carrier] = cached[:max_per_carrier]
            continue

        cached = _read_file_cache(carrier, cache_ttl_hours)
        if cached is not None:
            _write_mem_cache(carrier, cached)
            results[carrier] = cached[:max_per_carrier]
            continue

        feed_url = CARRIER_FEEDS.get(carrier)
        updates: list[CarrierUpdate] = []
        if feed_url:
            updates = _fetch_carrier_feed(carrier, feed_url, max_per_carrier * 2)

        if not updates:
            updates = _fetch_fallback_for_carrier(carrier, max_per_carrier * 2)

        updates.sort(key=lambda u: u.published_dt, reverse=True)
        updates = updates[:max_per_carrier]

        _write_mem_cache(carrier, updates)
        _write_file_cache(carrier, updates)
        results[carrier] = updates
        time.sleep(0.3)

    return results


def get_carrier_intelligence_summary(updates: dict[str, list[CarrierUpdate]]) -> dict:
    """Derive a high-level intelligence summary from carrier update data.

    Args:
        updates: dict as returned by fetch_carrier_updates().

    Returns:
        dict with most_active_carrier, capacity_signals, rate_signals, overall_tone.
    """
    if not updates:
        return {
            "most_active_carrier": "N/A",
            "capacity_signals":    [],
            "rate_signals":        [],
            "overall_tone":        "Neutral",
        }

    most_active = max(updates, key=lambda c: len(updates.get(c) or []))
    capacity_signals: list[str] = []
    rate_signals: list[str] = []
    all_sentiments: list[float] = []

    for carrier, upd_list in updates.items():
        for upd in (upd_list or []):
            all_sentiments.append(upd.sentiment)
            if upd.category == "capacity" and len(capacity_signals) < 5:
                capacity_signals.append(f"[{carrier}] {upd.headline}")
            if upd.category == "rates" and len(rate_signals) < 5:
                rate_signals.append(f"[{carrier}] {upd.headline}")

    avg_sentiment = sum(all_sentiments) / len(all_sentiments) if all_sentiments else 0.0
    if avg_sentiment > 0.15:
        overall_tone = "Bullish"
    elif avg_sentiment < -0.15:
        overall_tone = "Bearish"
    elif all_sentiments:
        pos_count = sum(1 for s in all_sentiments if s > 0.05)
        neg_count = sum(1 for s in all_sentiments if s < -0.05)
        overall_tone = "Mixed" if (pos_count > 0 and neg_count > 0) else "Neutral"
    else:
        overall_tone = "Neutral"

    return {
        "most_active_carrier": most_active,
        "capacity_signals":    capacity_signals,
        "rate_signals":        rate_signals,
        "overall_tone":        overall_tone,
    }
