"""Shipping news RSS feed fetcher with sentiment scoring."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

try:
    import feedparser  # type: ignore
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

import urllib.request
import urllib.error


# ── RSS feed sources ──────────────────────────────────────────────────────────

RSS_FEEDS: dict[str, str] = {
    "TradeWinds": "https://www.tradewindsnews.com/rss",
    "Splash247": "https://splash247.com/feed/",
    "Lloyd's List": "https://lloydslist.maritimeintelligence.informa.com/rss",
    "JOC": "https://www.joc.com/rss.xml",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ShippingNewsItem:
    title: str
    url: str
    source: str
    published_dt: datetime
    sentiment_score: float          # -1.0 to 1.0
    keywords: list[str] = field(default_factory=list)
    relevance_score: float = 0.0    # 0.0 to 1.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_dt"] = self.published_dt.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ShippingNewsItem":
        d = dict(d)
        try:
            d["published_dt"] = datetime.fromisoformat(d["published_dt"])
        except (ValueError, TypeError):
            d["published_dt"] = datetime.utcnow()
        return cls(**d)


# ── Sentiment & relevance scoring ─────────────────────────────────────────────

_POSITIVE_KEYWORDS = [
    "surge", "boom", "record", "growth", "increase",
    "demand", "recovery", "strong", "rally", "high",
]

_NEGATIVE_KEYWORDS = [
    "disruption", "delay", "strike", "congestion", "shortage",
    "crisis", "decline", "fall", "slump", "risk", "war", "attack",
]

_CORE_SHIPPING_TERMS = ["container", "freight", "shipping", "vessel", "port", "cargo"]
_ROUTE_TERMS = ["suez", "panama", "transpacific", "asia", "europe", "atlantic"]
_INDEX_TERMS = ["FBX", "BDI", "SCFI", "WCI", "freight rate"]


def _score_sentiment(text: str) -> float:
    """Score text sentiment from -1.0 (bearish) to +1.0 (bullish)."""
    lower = text.lower()
    score = 0.0
    for kw in _POSITIVE_KEYWORDS:
        if kw in lower:
            score += 0.1
    for kw in _NEGATIVE_KEYWORDS:
        if kw in lower:
            score -= 0.1
    return max(-1.0, min(1.0, score))


def _score_relevance(text: str) -> float:
    """Score text relevance to shipping (0.0 to 1.0)."""
    lower = text.lower()
    score = 0.0
    for term in _CORE_SHIPPING_TERMS:
        if term in lower:
            score += 0.2
    for term in _ROUTE_TERMS:
        if term in lower:
            score += 0.1
    for term in _INDEX_TERMS:
        if term in text:  # case-sensitive for acronyms like FBX, BDI
            score += 0.15
    return min(1.0, score)


def _extract_keywords(text: str) -> list[str]:
    """Extract matched shipping keywords from text."""
    lower = text.lower()
    found: list[str] = []
    all_terms = _POSITIVE_KEYWORDS + _NEGATIVE_KEYWORDS + _CORE_SHIPPING_TERMS + _ROUTE_TERMS
    for term in all_terms:
        if term in lower and term not in found:
            found.append(term)
    for term in _INDEX_TERMS:
        if term in text and term not in found:
            found.append(term)
    return found


# ── Feed fetching helpers ─────────────────────────────────────────────────────

def _fetch_feed_urllib(url: str, timeout: int = 5) -> str | None:
    """Fetch RSS feed content via urllib with timeout."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ShipTracker/1.0 (RSS reader)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _fetch_feed_requests(url: str, timeout: int = 5) -> str | None:
    """Fetch RSS feed content via requests with timeout."""
    if not _REQUESTS_AVAILABLE:
        return None
    try:
        resp = _requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "ShipTracker/1.0 (RSS reader)"},
        )
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _parse_feed_content(content: str, source_name: str) -> list[ShippingNewsItem]:
    """Parse RSS XML content into ShippingNewsItem list."""
    items: list[ShippingNewsItem] = []

    if _FEEDPARSER_AVAILABLE:
        parsed = feedparser.parse(content)
        entries = parsed.get("entries", [])
        for entry in entries:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            combined = f"{title} {summary}"

            # Parse publish date
            pub_dt = datetime.utcnow()
            published_parsed = entry.get("published_parsed")
            if published_parsed:
                try:
                    pub_dt = datetime(*published_parsed[:6])
                except Exception:
                    pass

            sentiment = _score_sentiment(combined)
            relevance = _score_relevance(combined)
            keywords = _extract_keywords(combined)

            if title and url:
                items.append(ShippingNewsItem(
                    title=title,
                    url=url,
                    source=source_name,
                    published_dt=pub_dt,
                    sentiment_score=sentiment,
                    keywords=keywords,
                    relevance_score=relevance,
                ))
    else:
        # Minimal fallback: parse <item> blocks with stdlib xml
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            # Try RSS 2.0 style first
            channel = root.find("channel")
            if channel is None:
                channel = root

            for item in channel.findall("item"):
                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")

                title = (title_el.text or "").strip() if title_el is not None else ""
                url = (link_el.text or "").strip() if link_el is not None else ""
                summary = (desc_el.text or "") if desc_el is not None else ""
                combined = f"{title} {summary}"

                if title and url:
                    sentiment = _score_sentiment(combined)
                    relevance = _score_relevance(combined)
                    keywords = _extract_keywords(combined)
                    items.append(ShippingNewsItem(
                        title=title,
                        url=url,
                        source=source_name,
                        published_dt=datetime.utcnow(),
                        sentiment_score=sentiment,
                        keywords=keywords,
                        relevance_score=relevance,
                    ))
        except Exception:
            pass

    return items


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache(cache_path: str, ttl_hours: float) -> list[ShippingNewsItem] | None:
    """Load cached news items if cache exists and is within TTL."""
    if not os.path.exists(cache_path):
        return None
    try:
        age_seconds = time.time() - os.path.getmtime(cache_path)
        if age_seconds > ttl_hours * 3600:
            return None
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [ShippingNewsItem.from_dict(d) for d in data]
    except Exception:
        return None


def _save_cache(cache_path: str, items: list[ShippingNewsItem]) -> None:
    """Save news items to JSON cache file."""
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump([item.to_dict() for item in items], f, indent=2)
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_shipping_news(
    max_items: int = 20,
    cache_ttl_hours: float = 1.0,
    cache_dir: str = "cache",
) -> list[ShippingNewsItem]:
    """
    Fetch shipping news from RSS feeds, with JSON caching.

    Returns items sorted by relevance_score descending, limited to max_items.
    Returns [] gracefully if all feeds fail or feedparser/requests unavailable.
    """
    cache_path = os.path.join(cache_dir, "news_cache.json")

    # Try cache first
    cached = _load_cache(cache_path, cache_ttl_hours)
    if cached is not None:
        return cached[:max_items]

    all_items: list[ShippingNewsItem] = []

    for source_name, url in RSS_FEEDS.items():
        try:
            # Try requests first, fall back to urllib
            content = _fetch_feed_requests(url, timeout=5)
            if not content:
                content = _fetch_feed_urllib(url, timeout=5)
            if not content:
                continue
            items = _parse_feed_content(content, source_name)
            all_items.extend(items)
        except Exception:
            continue

    # Sort by relevance descending
    all_items.sort(key=lambda x: x.relevance_score, reverse=True)
    result = all_items[:max_items]

    if result:
        _save_cache(cache_path, result)

    return result


def get_market_sentiment_summary(news: list[ShippingNewsItem]) -> dict:
    """
    Compute a market sentiment summary from a list of news items.

    Returns:
        {
            "avg_sentiment": float,
            "sentiment_label": str,   # "Bullish", "Bearish", or "Neutral"
            "bullish_count": int,
            "bearish_count": int,
            "top_keywords": list[str],
        }
    """
    if not news:
        return {
            "avg_sentiment": 0.0,
            "sentiment_label": "Neutral",
            "bullish_count": 0,
            "bearish_count": 0,
            "top_keywords": [],
        }

    avg_sentiment = sum(item.sentiment_score for item in news) / len(news)

    if avg_sentiment > 0.1:
        sentiment_label = "Bullish"
    elif avg_sentiment < -0.1:
        sentiment_label = "Bearish"
    else:
        sentiment_label = "Neutral"

    bullish_count = sum(1 for item in news if item.sentiment_score > 0.1)
    bearish_count = sum(1 for item in news if item.sentiment_score < -0.1)

    # Aggregate keyword frequency
    kw_counts: dict[str, int] = {}
    for item in news:
        for kw in item.keywords:
            kw_counts[kw] = kw_counts.get(kw, 0) + 1
    top_keywords = sorted(kw_counts, key=lambda k: kw_counts[k], reverse=True)[:10]

    return {
        "avg_sentiment": avg_sentiment,
        "sentiment_label": sentiment_label,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "top_keywords": top_keywords,
    }
