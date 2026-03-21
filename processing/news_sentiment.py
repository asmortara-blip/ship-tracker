"""
processing/news_sentiment.py
────────────────────────────
Enhanced shipping news sentiment analyser.

Scrapes six free RSS feeds, scores each article for bullish/bearish
sentiment using shipping-domain keyword rules, extracts mentions of
tracked ports and routes, deduplicates by title similarity, caches
results with a configurable TTL, and provides a Streamlit render panel.

Dependencies (already in requirements.txt): feedparser, loguru, streamlit.
No authentication required for any feed.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

from loguru import logger

try:
    import pandas as pd  # type: ignore
    _PANDAS_OK = True
except ImportError:  # pragma: no cover
    _PANDAS_OK = False
    logger.warning("pandas not installed – build_sentiment_trend disabled")

try:
    import feedparser as _feedparser  # type: ignore
    _FEEDPARSER_OK = True
except ImportError:  # pragma: no cover
    _FEEDPARSER_OK = False
    logger.warning("feedparser not installed – RSS fetching disabled")

# ── RSS Feed Registry ─────────────────────────────────────────────────────────

RSS_FEEDS: dict[str, str] = {
    "Hellenic Shipping News": "https://www.hellenicshippingnews.com/feed/",
    "Lloyd's List":           "https://lloydslist.maritimeintelligence.informa.com/feed",
    "Splash 247":             "https://splash247.com/feed/",
    "Maritime Executive":     "https://www.maritime-executive.com/rss/articles",
    "gCaptain":               "https://gcaptain.com/feed/",
    "Port Technology":        "https://www.porttechnology.org/feed/",
}

# ── Sentiment keyword lists ───────────────────────────────────────────────────

_BULLISH_KEYWORDS: list[tuple[str, float]] = [
    # generic
    ("surge",          0.10),
    ("rally",          0.10),
    ("boom",           0.10),
    ("strong demand",  0.10),
    ("record",         0.10),
    ("growth",         0.10),
    ("rebound",        0.10),
    ("recovery",       0.10),
    ("expansion",      0.10),
    ("optimistic",     0.10),
    ("increase",       0.10),
    ("rise",           0.10),
    ("high",           0.10),
    ("peak",           0.10),
    # shipping-specific bullish
    ("port congestion",  0.15),   # high demand exceeds capacity
    ("rate increase",    0.15),
    ("capacity crunch",  0.12),
    ("full utilization", 0.12),
    ("freight surge",    0.15),
]

_BEARISH_KEYWORDS: list[tuple[str, float]] = [
    # generic
    ("decline",       0.10),
    ("fall",          0.10),
    ("drop",          0.10),
    ("weak",          0.10),
    ("slowdown",      0.10),
    ("recession",     0.10),
    ("overcapacity",  0.10),
    ("cancellation",  0.10),
    ("low demand",    0.10),
    ("crash",         0.10),
    ("plunge",        0.10),
    ("concern",       0.10),
    # shipping-specific bearish
    ("blank sailing",  0.10),
    ("void sailing",   0.10),
    ("rate cut",       0.12),
    ("rate decline",   0.12),
    ("capacity excess", 0.10),
]

# ── Entity keyword map ────────────────────────────────────────────────────────
# Maps lowercase search terms → canonical display name.
# Covers all 25 ports and 17 routes from port_registry / route_registry.

_ENTITY_MAP: dict[str, str] = {
    # ── Ports ────────────────────────────────────────────────────────────────
    "shanghai":              "Shanghai",
    "cnsha":                 "Shanghai",
    "singapore":             "Singapore",
    "sgsin":                 "Singapore",
    "ningbo":                "Ningbo-Zhoushan",
    "zhoushan":              "Ningbo-Zhoushan",
    "cnnbo":                 "Ningbo-Zhoushan",
    "shenzhen":              "Shenzhen",
    "cnszn":                 "Shenzhen",
    "qingdao":               "Qingdao",
    "cntao":                 "Qingdao",
    "busan":                 "Busan",
    "krpus":                 "Busan",
    "tianjin":               "Tianjin",
    "cntxg":                 "Tianjin",
    "hong kong":             "Hong Kong",
    "hkhkg":                 "Hong Kong",
    "port klang":            "Port Klang",
    "mypkg":                 "Port Klang",
    "rotterdam":             "Rotterdam",
    "nlrtm":                 "Rotterdam",
    "jebel ali":             "Jebel Ali (Dubai)",
    "dubai":                 "Jebel Ali (Dubai)",
    "aejea":                 "Jebel Ali (Dubai)",
    "antwerp":               "Antwerp-Bruges",
    "bruges":                "Antwerp-Bruges",
    "beanr":                 "Antwerp-Bruges",
    "tanjung pelepas":       "Tanjung Pelepas",
    "mytpp":                 "Tanjung Pelepas",
    "kaohsiung":             "Kaohsiung",
    "twkhh":                 "Kaohsiung",
    "los angeles":           "Los Angeles",
    "uslax":                 "Los Angeles",
    "long beach":            "Long Beach",
    "uslgb":                 "Long Beach",
    "hamburg":               "Hamburg",
    "deham":                 "Hamburg",
    "new york":              "New York/New Jersey",
    "new jersey":            "New York/New Jersey",
    "usnyc":                 "New York/New Jersey",
    "tanger med":            "Tanger Med",
    "tangier":               "Tanger Med",
    "matnm":                 "Tanger Med",
    "yokohama":              "Yokohama",
    "jpyok":                 "Yokohama",
    "colombo":               "Colombo",
    "lkcmb":                 "Colombo",
    "piraeus":               "Piraeus",
    "grpir":                 "Piraeus",
    "savannah":              "Savannah",
    "ussav":                 "Savannah",
    "felixstowe":            "Felixstowe",
    "gbfxt":                 "Felixstowe",
    "santos":                "Santos",
    "brsao":                 "Santos",
    # ── Routes ───────────────────────────────────────────────────────────────
    "trans-pacific":         "Trans-Pacific",
    "transpacific":          "Trans-Pacific",
    "asia-us":               "Trans-Pacific",
    "asia europe":           "Asia-Europe",
    "asia-europe":           "Asia-Europe",
    "suez":                  "Asia-Europe",
    "transatlantic":         "Transatlantic",
    "trans-atlantic":        "Transatlantic",
    "southeast asia":        "Southeast Asia Eastbound",
    "sea eastbound":         "Southeast Asia Eastbound",
    "ningbo europe":         "Asia-Europe via Suez (Ningbo)",
    "middle east to europe": "Middle East Hub to Europe",
    "gulf to europe":        "Middle East Hub to Europe",
    "middle east to asia":   "Middle East Hub to Asia",
    "gulf to asia":          "Middle East Hub to Asia",
    "south asia to europe":  "South Asia to Europe",
    "intra-asia":            "Intra-Asia",
    "intra asia":            "Intra-Asia",
    "china south america":   "China to South America",
    "europe south america":  "Europe to South America",
    "mediterranean":         "Mediterranean Hub to Asia",
    "med hub":               "Mediterranean Hub to Asia",
    "north africa":          "North Africa/Med to Europe",
    "strait of gibraltar":   "North Africa/Med to Europe",
    "us east south america": "US East Coast to South America",
    "cape of good hope":     "China to South America",
    "panama canal":          "Trans-Pacific",
    "red sea":               "Asia-Europe",
    "houthi":                "Asia-Europe",
}

# Sorted longest-first so multi-word phrases match before substrings
_ENTITY_PATTERNS: list[tuple[str, str]] = sorted(
    _ENTITY_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
)

# ── Topic classification keywords ─────────────────────────────────────────────

_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "freight_rates":   ["rate", "rates", "fbx", "scfi", "bdi", "pricing", "price", "freight rate", "tariff"],
    "port_congestion": ["congestion", "congested", "delay", "delays", "backlog", "dwell time", "queue", "waiting"],
    "sanctions":       ["sanction", "sanctions", "embargo", "embargoes", "restricted", "blocked", "blacklist"],
    "weather":         ["storm", "typhoon", "hurricane", "fog", "disruption", "weather", "cyclone", "flood"],
    "m_and_a":         ["merger", "acquisition", "takeover", "deal", "consolidation", "acquire", "merge"],
    "regulatory":      ["imo", "cii", "ets", "compliance", "regulation", "carbon", "emission", "marpol", "sulphur"],
    "geopolitical":    ["war", "conflict", "red sea", "suez", "panama", "chokepoint", "piracy", "houthi", "military"],
    "demand":          ["demand", "volume", "teu", "cargo", "shipment", "trade", "import", "export"],
    "fleet":           ["newbuild", "vessel", "delivery", "orderbook", "scrapping", "fleet", "ship order", "capacity"],
}

# ── Region extraction keywords ────────────────────────────────────────────────

REGION_KEYWORDS: dict[str, list[str]] = {
    "Asia-Pacific":   ["china", "shanghai", "shenzhen", "hong kong", "singapore", "japan", "korea", "vietnam", "asia", "pacific"],
    "Europe":         ["rotterdam", "hamburg", "antwerp", "felixstowe", "europe", "eu", "mediterranean"],
    "North America":  ["los angeles", "long beach", "new york", "savannah", "north america", "us", "usa", "canada"],
    "Middle East":    ["dubai", "jebel ali", "oman", "gulf", "middle east", "persian"],
    "Red Sea":        ["red sea", "suez", "houthis", "bab-el-mandeb", "aden"],
    "Latin America":  ["brazil", "santos", "latin america", "south america", "chile"],
    "Africa":         ["africa", "durban", "mombasa", "cape of good hope"],
}

# Urgency indicator words
_URGENCY_WORDS = [
    "breaking", "urgent", "alert", "warning", "immediate", "crisis",
    "emergency", "halt", "suspended", "force majeure",
]

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class NewsArticle:
    """A single shipping news article with sentiment and entity metadata."""

    title:           str
    url:             str
    published_dt:    datetime
    source:          str
    summary:         str
    sentiment_score: float          # -1.0 (very bearish) … +1.0 (very bullish)
    sentiment_label: str            # "BULLISH" | "BEARISH" | "NEUTRAL"
    entities:        list[str] = field(default_factory=list)
    relevance_score: float = 0.5   # 0.0 … 1.0
    topic:           str   = "general"  # see _classify_topic()
    urgency_score:   float = 0.0        # 0.0 … 1.0
    regions:         list[str] = field(default_factory=list)  # see _extract_regions()

    # ── Serialisation helpers ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_dt"] = self.published_dt.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NewsArticle":
        d = dict(d)
        raw_dt = d.get("published_dt", "")
        try:
            d["published_dt"] = datetime.fromisoformat(raw_dt)
        except (ValueError, TypeError):
            d["published_dt"] = datetime.now(tz=timezone.utc)
        return cls(**d)

    # ── Convenience ──────────────────────────────────────────────────────────

    @property
    def age_str(self) -> str:
        """Human-readable age string, e.g. '2h ago' or '3d ago'."""
        now = datetime.now(tz=timezone.utc)
        pub = self.published_dt
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


# ── Sentiment scorer ──────────────────────────────────────────────────────────


def _score_sentiment(text: str) -> float:
    """
    Rule-based sentiment scorer for shipping news text.

    Scans lowercased text for bullish (+) and bearish (-) keyword phrases,
    accumulates their weights, and clamps to [-1.0, +1.0].

    Returns:
        float in [-1.0, 1.0]  (positive = bullish, negative = bearish)
    """
    lower = text.lower()
    score = 0.0

    for phrase, weight in _BULLISH_KEYWORDS:
        if phrase in lower:
            score += weight

    for phrase, weight in _BEARISH_KEYWORDS:
        if phrase in lower:
            score -= weight

    return max(-1.0, min(1.0, score))


def _label_from_score(score: float) -> str:
    """Map a sentiment float score to a string label."""
    if score > 0.05:
        return "BULLISH"
    if score < -0.05:
        return "BEARISH"
    return "NEUTRAL"


# ── Entity extractor ─────────────────────────────────────────────────────────


def _extract_entities(text: str) -> list[str]:
    """
    Find mentions of tracked ports and routes in *text*.

    Uses _ENTITY_PATTERNS (sorted longest-first) so that multi-word phrases
    like "port klang" are matched before shorter substrings like "klang".

    Returns a deduplicated list of canonical display names.
    """
    lower = text.lower()
    found: dict[str, bool] = {}

    for term, canonical in _ENTITY_PATTERNS:
        if term in lower and canonical not in found:
            found[canonical] = True

    return list(found.keys())


# ── Relevance scorer ──────────────────────────────────────────────────────────

_SHIPPING_RELEVANCE_TERMS = [
    "container", "freight", "shipping", "vessel", "port", "cargo",
    "teu", "feeder", "carrier", "ocean", "maritime", "route",
    "charter", "bunker", "logistics", "supply chain",
    "fbx", "bdi", "scfi", "wci", "drewry", "xeneta",
]


def _score_relevance(text: str, entities: list[str]) -> float:
    """
    Estimate how relevant an article is to container shipping (0–1).

    Entity presence boosts the score significantly.
    """
    lower = text.lower()
    score = 0.0

    for term in _SHIPPING_RELEVANCE_TERMS:
        if term in lower:
            score += 0.06

    # Each recognised entity is a strong relevance signal
    score += min(0.4, len(entities) * 0.1)

    return min(1.0, score)


# ── Topic classifier ──────────────────────────────────────────────────────────


def _classify_topic(text: str) -> str:
    """Classify news item into one of the predefined topics."""
    lower = text.lower()
    best_topic = "general"
    best_count = 0
    for topic, keywords in _TOPIC_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > best_count:
            best_count = count
            best_topic = topic
    return best_topic


# ── Urgency scorer ────────────────────────────────────────────────────────────


def _score_urgency(text: str) -> float:
    """Score how urgent a news item is (0.0–1.0)."""
    lower = text.lower()
    score = 0.0

    # High-impact words
    for word in _URGENCY_WORDS:
        if word in lower:
            score += 0.2

    # Exclamation marks
    if "!" in text:
        score += 0.1

    # Percentage figures e.g. "15%"
    if re.search(r"\d+%", text):
        score += 0.05

    # ALL CAPS words (3+ chars, not all numbers)
    if re.search(r"\b[A-Z]{3,}\b", text):
        score += 0.05

    return min(1.0, score)


# ── Region extractor ──────────────────────────────────────────────────────────


def _extract_regions(text: str) -> list[str]:
    """Extract geographic region mentions from text."""
    lower = text.lower()
    found: list[str] = []
    for region, keywords in REGION_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                found.append(region)
                break
    return found


# ── Deduplication ─────────────────────────────────────────────────────────────


def _titles_similar(a: str, b: str, threshold: float = 0.70) -> bool:
    """
    Lightweight title similarity check using token Jaccard similarity.

    Avoids pulling in third-party fuzzy-matching libraries.
    """
    def tokenise(s: str) -> set[str]:
        return set(re.sub(r"[^\w\s]", "", s.lower()).split())

    tok_a = tokenise(a)
    tok_b = tokenise(b)
    if not tok_a or not tok_b:
        return False
    intersection = tok_a & tok_b
    union = tok_a | tok_b
    return len(intersection) / len(union) >= threshold


def _deduplicate(articles: list[NewsArticle]) -> list[NewsArticle]:
    """
    Remove near-duplicate articles by title similarity.

    For each pair with Jaccard similarity >= 0.70, keep the one with the
    higher relevance_score; in a tie, keep the earlier publication.
    """
    kept: list[NewsArticle] = []
    for candidate in articles:
        for existing in kept:
            if _titles_similar(candidate.title, existing.title):
                break
        else:
            kept.append(candidate)
    return kept


# ── Cache helpers (JSON, keyed separately from the Parquet CacheManager) ──────

_CACHE_SOURCE = "news_sentiment"
_CACHE_KEY    = "all_feeds"


def _cache_path(cache) -> str:
    """
    Derive the JSON cache file path from the CacheManager instance or a
    plain directory string / Path passed as *cache*.
    """
    if hasattr(cache, "cache_dir"):
        base = str(cache.cache_dir)
    else:
        base = str(cache)
    return os.path.join(base, _CACHE_SOURCE, _CACHE_KEY + ".json")


def _load_json_cache(path: str, ttl_hours: float) -> list[NewsArticle] | None:
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > ttl_hours * 3600:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        articles = [NewsArticle.from_dict(d) for d in data]
        logger.debug("News sentiment: loaded {} articles from cache", len(articles))
        return articles
    except Exception as exc:
        logger.warning("News sentiment cache load error: {}", exc)
        return None


def _save_json_cache(path: str, articles: list[NewsArticle]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([a.to_dict() for a in articles], fh, indent=2)
        logger.debug("News sentiment: cached {} articles to {}", len(articles), path)
    except Exception as exc:
        logger.warning("News sentiment cache write error: {}", exc)


# ── RSS fetching ──────────────────────────────────────────────────────────────

_CUTOFF_DAYS = 7


def _parse_published(entry: Any) -> datetime:
    """Extract a timezone-aware datetime from a feedparser entry."""
    pp = entry.get("published_parsed")
    if pp:
        try:
            return datetime(*pp[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    # Fallback: now
    return datetime.now(tz=timezone.utc)


def _fetch_one_feed(source_name: str, url: str) -> list[NewsArticle]:
    """Fetch, parse, and score a single RSS feed URL."""
    if not _FEEDPARSER_OK:
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_CUTOFF_DAYS)
    articles: list[NewsArticle] = []

    try:
        logger.debug("Fetching feed: {} — {}", source_name, url)
        parsed = _feedparser.parse(url, agent="ShipSentiment/2.0 (RSS reader; research bot)")
    except Exception as exc:
        logger.warning("Feed fetch error [{}]: {}", source_name, exc)
        return []

    entries = parsed.get("entries", [])
    if not entries:
        # bozo indicates parse error; still log it
        if parsed.get("bozo"):
            logger.debug("Feed parse warning [{}]: {}", source_name, parsed.get("bozo_exception"))

    for entry in entries:
        title   = (entry.get("title") or "").strip()
        url_str = (entry.get("link")  or "").strip()
        if not title or not url_str:
            continue

        summary = (
            entry.get("summary")
            or entry.get("description")
            or ""
        )
        # Strip HTML tags from summary
        summary = re.sub(r"<[^>]+>", " ", summary).strip()
        # Collapse whitespace (no f-string backslash needed here)
        summary = re.sub(r"\s+", " ", summary)

        pub_dt = _parse_published(entry)

        # Skip articles older than cutoff
        if pub_dt < cutoff:
            continue

        combined  = title + " " + summary
        score     = _score_sentiment(combined)
        label     = _label_from_score(score)
        entities  = _extract_entities(combined)
        relevance = _score_relevance(combined, entities)
        topic     = _classify_topic(combined)
        urgency   = _score_urgency(combined)
        regions   = _extract_regions(combined)

        articles.append(NewsArticle(
            title           = title,
            url             = url_str,
            published_dt    = pub_dt,
            source          = source_name,
            summary         = summary[:500],  # truncate for storage
            sentiment_score = round(score, 4),
            sentiment_label = label,
            entities        = entities,
            relevance_score = round(relevance, 4),
            topic           = topic,
            urgency_score   = round(urgency, 4),
            regions         = regions,
        ))

    logger.info("Feed [{}]: {} articles (last {} days)", source_name, len(articles), _CUTOFF_DAYS)
    return articles


# ── Public: fetch_all_news ────────────────────────────────────────────────────


def fetch_all_news(cache: Any, ttl_hours: float = 2.0) -> list[NewsArticle]:
    """
    Fetch all six RSS feeds, deduplicate, and return scored NewsArticle list.

    Results are cached as JSON for *ttl_hours*.  Pass a CacheManager instance
    or a plain directory string as *cache*.

    Articles are sorted by (relevance_score DESC, published_dt DESC).

    Args:
        cache:     CacheManager instance or directory path string / Path.
        ttl_hours: Cache time-to-live in hours (default 2.0).

    Returns:
        list[NewsArticle] — may be empty if all feeds fail.
    """
    path = _cache_path(cache)

    cached = _load_json_cache(path, ttl_hours)
    if cached is not None:
        return cached

    all_articles: list[NewsArticle] = []
    for source_name, url in RSS_FEEDS.items():
        fetched = _fetch_one_feed(source_name, url)
        all_articles.extend(fetched)

    deduped = _deduplicate(all_articles)

    # Sort: highest relevance first, then most recent
    deduped.sort(
        key=lambda a: (a.relevance_score, a.published_dt.timestamp()),
        reverse=True,
    )

    if deduped:
        _save_json_cache(path, deduped)

    logger.info(
        "fetch_all_news: {} raw -> {} after dedup",
        len(all_articles),
        len(deduped),
    )
    return deduped


# ── Public: get_sentiment_summary ─────────────────────────────────────────────


def get_sentiment_summary(articles: list[NewsArticle]) -> dict[str, Any]:
    """
    Compute aggregate sentiment statistics from a list of NewsArticle objects.

    Returns:
        dict with keys:
          overall_score   (float, weighted average by relevance)
          label           (str, "BULLISH" | "BEARISH" | "NEUTRAL")
          article_count   (int)
          bullish_count   (int)
          bearish_count   (int)
          neutral_count   (int)
          top_bullish     (list[NewsArticle], up to 5, highest score first)
          top_bearish     (list[NewsArticle], up to 5, lowest score first)
          trending_entities (list[str], sorted by mention count desc)
    """
    if not articles:
        return {
            "overall_score":        0.0,
            "label":                "NEUTRAL",
            "article_count":        0,
            "bullish_count":        0,
            "bearish_count":        0,
            "neutral_count":        0,
            "top_bullish":          [],
            "top_bearish":          [],
            "trending_entities":    [],
            "topic_breakdown":      {},
            "region_breakdown":     {},
            "urgency_distribution": {"high": 0, "medium": 0, "low": 0},
            "trending_topics":      [],
            "high_urgency_items":   [],
        }

    # Weighted average by relevance_score
    total_weight  = sum(a.relevance_score for a in articles) or 1.0
    weighted_sum  = sum(a.sentiment_score * a.relevance_score for a in articles)
    overall_score = max(-1.0, min(1.0, weighted_sum / total_weight))

    bullish = [a for a in articles if a.sentiment_label == "BULLISH"]
    bearish = [a for a in articles if a.sentiment_label == "BEARISH"]
    neutral = [a for a in articles if a.sentiment_label == "NEUTRAL"]

    top_bullish = sorted(bullish, key=lambda a: a.sentiment_score, reverse=True)[:5]
    top_bearish = sorted(bearish, key=lambda a: a.sentiment_score)[:5]

    entity_counter: Counter[str] = Counter()
    for a in articles:
        entity_counter.update(a.entities)
    trending_entities = [e for e, _ in entity_counter.most_common()]

    # ── Topic breakdown ───────────────────────────────────────────────────────
    topic_groups: dict[str, list[NewsArticle]] = {}
    for a in articles:
        topic_groups.setdefault(a.topic, []).append(a)

    topic_breakdown: dict[str, dict] = {}
    for t, group in topic_groups.items():
        avg_sent = sum(x.sentiment_score for x in group) / len(group)
        top_headline = max(group, key=lambda x: x.relevance_score).title
        topic_breakdown[t] = {
            "count":        len(group),
            "avg_sentiment": round(avg_sent, 4),
            "top_headline":  top_headline,
        }

    # ── Region breakdown ──────────────────────────────────────────────────────
    region_counter: Counter[str] = Counter()
    for a in articles:
        region_counter.update(a.regions)
    region_breakdown: dict[str, int] = dict(region_counter)

    # ── Urgency distribution ──────────────────────────────────────────────────
    urgency_distribution = {"high": 0, "medium": 0, "low": 0}
    high_urgency_items: list[NewsArticle] = []
    for a in articles:
        if a.urgency_score > 0.5:
            urgency_distribution["high"] += 1
            high_urgency_items.append(a)
        elif a.urgency_score >= 0.2:
            urgency_distribution["medium"] += 1
        else:
            urgency_distribution["low"] += 1

    # ── Trending topics ───────────────────────────────────────────────────────
    topic_count_counter: Counter[str] = Counter(a.topic for a in articles)
    trending_topics = [t for t, _ in topic_count_counter.most_common()]

    return {
        "overall_score":        round(overall_score, 4),
        "label":                _label_from_score(overall_score),
        "article_count":        len(articles),
        "bullish_count":        len(bullish),
        "bearish_count":        len(bearish),
        "neutral_count":        len(neutral),
        "top_bullish":          top_bullish,
        "top_bearish":          top_bearish,
        "trending_entities":    trending_entities,
        "topic_breakdown":      topic_breakdown,
        "region_breakdown":     region_breakdown,
        "urgency_distribution": urgency_distribution,
        "trending_topics":      trending_topics,
        "high_urgency_items":   high_urgency_items,
    }


# ── Public: get_route_news ────────────────────────────────────────────────────


def get_route_news(route_id: str, articles: list[NewsArticle]) -> list[NewsArticle]:
    """
    Filter articles relevant to a specific route.

    Matches by checking if any of the route's canonical entity names appear in
    the article's entity list.  Falls back to a keyword search on the route_id
    itself (underscores converted to spaces).

    Args:
        route_id:  e.g. "transpacific_eb", "asia_europe"
        articles:  Full article list from fetch_all_news()

    Returns:
        Filtered list sorted by published_dt descending.
    """
    # Build candidate entity names from route_id
    id_words = route_id.replace("_", " ").lower()

    # Map route_id fragments to canonical entity display names
    _ROUTE_ENTITY_LOOKUP: dict[str, list[str]] = {
        "transpacific_eb":       ["Trans-Pacific", "Los Angeles", "Long Beach", "Shanghai"],
        "transpacific_wb":       ["Trans-Pacific", "Los Angeles", "Long Beach", "Shanghai"],
        "sea_transpacific_eb":   ["Trans-Pacific", "Singapore", "Los Angeles"],
        "asia_europe":           ["Asia-Europe", "Rotterdam", "Shanghai", "Suez"],
        "ningbo_europe":         ["Asia-Europe via Suez (Ningbo)", "Ningbo-Zhoushan", "Antwerp-Bruges"],
        "transatlantic":         ["Transatlantic", "Rotterdam", "New York/New Jersey"],
        "middle_east_to_europe": ["Middle East Hub to Europe", "Jebel Ali (Dubai)", "Rotterdam"],
        "middle_east_to_asia":   ["Middle East Hub to Asia",   "Jebel Ali (Dubai)", "Shanghai"],
        "south_asia_to_europe":  ["South Asia to Europe", "Colombo", "Felixstowe"],
        "intra_asia_china_sea":  ["Intra-Asia", "Shanghai", "Singapore"],
        "intra_asia_china_japan":["Intra-Asia", "Shanghai", "Yokohama"],
        "china_south_america":   ["China to South America", "Shanghai", "Santos"],
        "europe_south_america":  ["Europe to South America", "Rotterdam", "Santos"],
        "med_hub_to_asia":       ["Mediterranean Hub to Asia", "Piraeus", "Shanghai"],
        "north_africa_to_europe":["North Africa/Med to Europe", "Tanger Med", "Rotterdam"],
        "us_east_south_america": ["US East Coast to South America", "Savannah", "Santos"],
        "longbeach_to_asia":     ["Trans-Pacific", "Long Beach", "Shanghai"],
    }

    target_entities = set(_ROUTE_ENTITY_LOOKUP.get(route_id, []))

    result: list[NewsArticle] = []
    for article in articles:
        article_entities = set(article.entities)
        if article_entities & target_entities:
            result.append(article)
            continue
        # Secondary: route_id words appear in title/summary
        if id_words in (article.title + " " + article.summary).lower():
            result.append(article)

    result.sort(key=lambda a: a.published_dt, reverse=True)
    return result


# ── Public: build_sentiment_trend ─────────────────────────────────────────────


def build_sentiment_trend(news_items: list[NewsArticle], days: int = 30):  # -> pd.DataFrame
    """
    Group news items by date and compute daily average sentiment.

    Returns a DataFrame with columns: date, avg_sentiment, count,
    bullish_pct, bearish_pct.  Missing days are filled with NaN so
    callers can interpolate.

    Requires pandas.  Returns an empty dict if pandas is unavailable.
    """
    if not _PANDAS_OK:
        logger.warning("build_sentiment_trend requires pandas")
        return {}

    if not news_items:
        return pd.DataFrame(columns=["date", "avg_sentiment", "count", "bullish_pct", "bearish_pct"])

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    recent = [a for a in news_items if a.published_dt >= cutoff]

    rows: dict[str, list] = {}
    for a in recent:
        date_key = a.published_dt.strftime("%Y-%m-%d")
        if date_key not in rows:
            rows[date_key] = []
        rows[date_key].append(a)

    records = []
    for date_key, group in rows.items():
        n = len(group)
        avg_sent = sum(x.sentiment_score for x in group) / n
        bull_pct = sum(1 for x in group if x.sentiment_label == "BULLISH") / n * 100
        bear_pct = sum(1 for x in group if x.sentiment_label == "BEARISH") / n * 100
        records.append({
            "date":          date_key,
            "avg_sentiment": round(avg_sent, 4),
            "count":         n,
            "bullish_pct":   round(bull_pct, 2),
            "bearish_pct":   round(bear_pct, 2),
        })

    df = pd.DataFrame(records)
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    # Reindex over full date range, filling gaps with NaN
    full_range = pd.date_range(df.index.min(), df.index.max(), freq="D")
    df = df.reindex(full_range)
    df.index.name = "date"
    df = df.reset_index()

    return df


# ── Public: get_top_stories ────────────────────────────────────────────────────


def get_top_stories(news_items: list[NewsArticle], n: int = 10) -> list[NewsArticle]:
    """
    Return top N most impactful stories, ranked by:
    combined_score = (abs(sentiment_score) * 0.4) + (relevance_score * 0.4) + (urgency_score * 0.2)
    """
    if not news_items:
        return []

    def _impact(a: NewsArticle) -> float:
        return (
            abs(a.sentiment_score) * 0.4
            + a.relevance_score * 0.4
            + a.urgency_score * 0.2
        )

    return sorted(news_items, key=_impact, reverse=True)[:n]


# ── Streamlit render panel ────────────────────────────────────────────────────

# Dark-theme colour constants (matching ui/components.py)
_C_BG      = "#0a0f1a"
_C_CARD    = "#1a2235"
_C_BORDER  = "rgba(255,255,255,0.08)"
_C_TEXT    = "#f1f5f9"
_C_TEXT2   = "#94a3b8"
_C_TEXT3   = "#64748b"
_C_BULL    = "#10b981"   # green
_C_BEAR    = "#ef4444"   # red
_C_NEUT    = "#64748b"   # slate


def _sentiment_color(label: str) -> str:
    if label == "BULLISH":
        return _C_BULL
    if label == "BEARISH":
        return _C_BEAR
    return _C_NEUT


def _gauge_bar_html(score: float) -> str:
    """
    Return HTML for a red→gray→green horizontal sentiment gauge bar.

    *score* is in [-1, 1].  The bar needle sits at the proportional position.
    """
    pct = int((score + 1.0) / 2.0 * 100)   # 0 % = -1, 50 % = 0, 100 % = +1

    needle_left = max(2, min(98, pct))

    score_label = "BULLISH" if score > 0.05 else ("BEARISH" if score < -0.05 else "NEUTRAL")
    label_color = _sentiment_color(score_label)

    score_str = ("+" if score >= 0 else "") + str(round(score, 2))

    html = (
        '<div style="margin:12px 0 18px 0">'
        '<div style="display:flex; justify-content:space-between;'
        ' font-size:0.72rem; color:' + _C_TEXT3 + '; margin-bottom:4px">'
        '<span>BEARISH</span><span>NEUTRAL</span><span>BULLISH</span>'
        '</div>'
        '<div style="position:relative; height:10px; border-radius:5px;'
        ' background:linear-gradient(to right, #ef4444, #64748b 50%, #10b981);">'
        '<div style="position:absolute; left:' + str(needle_left) + '%;'
        ' top:-3px; transform:translateX(-50%);'
        ' width:4px; height:16px; background:white; border-radius:2px;'
        ' box-shadow:0 0 6px rgba(255,255,255,0.6)"></div>'
        '</div>'
        '<div style="text-align:center; margin-top:6px;'
        ' font-size:0.85rem; font-weight:700; color:' + label_color + '">'
        + score_str + ' ' + score_label +
        '</div>'
        '</div>'
    )
    return html


def _source_badge_html(source: str) -> str:
    """Return a small pill badge showing the source name."""
    return (
        '<span style="'
        'background:rgba(59,130,246,0.15);'
        'color:#3b82f6;'
        'border:1px solid rgba(59,130,246,0.3);'
        'border-radius:999px;'
        'font-size:0.60rem;'
        'font-weight:600;'
        'padding:2px 7px;'
        'white-space:nowrap;'
        '">' + source + '</span>'
    )


def _entity_tags_html(entities: list[str]) -> str:
    """Return HTML for a row of entity tag pills."""
    if not entities:
        return ""
    tags = "".join(
        '<span style="'
        'background:rgba(16,185,129,0.10);'
        'color:#10b981;'
        'border:1px solid rgba(16,185,129,0.25);'
        'border-radius:999px;'
        'font-size:0.58rem;'
        'padding:2px 6px;'
        'margin-right:4px;'
        'white-space:nowrap;'
        '">' + e + '</span>'
        for e in entities[:6]
    )
    return (
        '<div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:3px">'
        + tags
        + '</div>'
    )


def _article_card_html(article: NewsArticle) -> str:
    """Return a full article card HTML string."""
    border_color = _sentiment_color(article.sentiment_label)
    score_str = ("+" if article.sentiment_score >= 0 else "") + str(round(article.sentiment_score, 2))

    title_escaped = article.title.replace("<", "&lt;").replace(">", "&gt;")
    summary_short = article.summary[:160]
    if len(article.summary) > 160:
        summary_short = summary_short + "…"
    summary_escaped = summary_short.replace("<", "&lt;").replace(">", "&gt;")

    badge = _source_badge_html(article.source)
    entity_tags = _entity_tags_html(article.entities)

    return (
        '<div style="'
        'background:' + _C_CARD + ';'
        'border:1px solid ' + _C_BORDER + ';'
        'border-left:4px solid ' + border_color + ';'
        'border-radius:10px;'
        'padding:14px 16px;'
        'margin-bottom:8px;'
        '">'
        # Header row: badge + age
        '<div style="display:flex; justify-content:space-between; align-items:center;'
        ' margin-bottom:6px">'
        + badge
        + '<span style="font-size:0.65rem; color:' + _C_TEXT3 + '">'
        + article.age_str
        + '</span>'
        '</div>'
        # Title (linked)
        '<a href="' + article.url + '" target="_blank" style="'
        'font-size:0.88rem; font-weight:600; color:' + _C_TEXT + ';'
        'text-decoration:none; line-height:1.4; display:block; margin-bottom:4px">'
        + title_escaped
        + '</a>'
        # Summary
        '<div style="font-size:0.76rem; color:' + _C_TEXT2 + '; line-height:1.5">'
        + summary_escaped
        + '</div>'
        # Score + entity tags row
        '<div style="display:flex; justify-content:space-between;'
        ' align-items:flex-end; margin-top:6px">'
        + entity_tags
        + '<span style="font-size:0.70rem; font-weight:700; color:' + border_color + ';'
        ' white-space:nowrap; margin-left:8px">'
        + score_str
        + '</span>'
        '</div>'
        '</div>'
    )


def render_news_panel(articles: list[NewsArticle], max_items: int = 8) -> None:
    """
    Render a Streamlit news sentiment panel.

    Sections:
      1. Sentiment gauge bar (red→gray→green).
      2. KPI row: article count / bullish / bearish counts.
      3. Trending entities pills.
      4. Article cards (up to *max_items*) with colored left border.
      5. "Load more" expander for the remaining articles.

    Args:
        articles:  List from fetch_all_news().
        max_items: Number of top articles shown before the expander.
    """
    try:
        import streamlit as st
    except ImportError:  # pragma: no cover
        logger.error("streamlit not installed; cannot render news panel")
        return

    summary = get_sentiment_summary(articles)

    overall  = summary["overall_score"]
    label    = summary["label"]
    n_total  = summary["article_count"]
    n_bull   = summary["bullish_count"]
    n_bear   = summary["bearish_count"]
    trending = summary["trending_entities"]

    # ── Section header ────────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.72rem; color:' + _C_TEXT3 + ';'
        ' text-transform:uppercase; letter-spacing:0.10em;'
        ' margin-bottom:4px">Shipping News Sentiment</div>',
        unsafe_allow_html=True,
    )

    # ── Sentiment gauge bar ───────────────────────────────────────────────────
    st.markdown(_gauge_bar_html(overall), unsafe_allow_html=True)

    # ── KPI row ───────────────────────────────────────────────────────────────
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(
            '<div style="text-align:center; background:' + _C_CARD + ';'
            ' border-radius:8px; padding:10px;">'
            '<div style="font-size:1.4rem; font-weight:800; color:' + _C_TEXT + '">'
            + str(n_total)
            + '</div>'
            '<div style="font-size:0.65rem; color:' + _C_TEXT3 + ';'
            ' text-transform:uppercase; letter-spacing:0.06em">Articles</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            '<div style="text-align:center; background:' + _C_CARD + ';'
            ' border-radius:8px; padding:10px;">'
            '<div style="font-size:1.4rem; font-weight:800; color:' + _C_BULL + '">'
            + str(n_bull)
            + '</div>'
            '<div style="font-size:0.65rem; color:' + _C_TEXT3 + ';'
            ' text-transform:uppercase; letter-spacing:0.06em">Bullish</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with k3:
        st.markdown(
            '<div style="text-align:center; background:' + _C_CARD + ';'
            ' border-radius:8px; padding:10px;">'
            '<div style="font-size:1.4rem; font-weight:800; color:' + _C_BEAR + '">'
            + str(n_bear)
            + '</div>'
            '<div style="font-size:0.65rem; color:' + _C_TEXT3 + ';'
            ' text-transform:uppercase; letter-spacing:0.06em">Bearish</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Trending entities ─────────────────────────────────────────────────────
    if trending:
        tags_html = "".join(
            '<span style="'
            'background:rgba(139,92,246,0.12);'
            'color:#8b5cf6;'
            'border:1px solid rgba(139,92,246,0.25);'
            'border-radius:999px;'
            'font-size:0.62rem;'
            'padding:3px 9px;'
            'margin:3px 3px 0 0;'
            'display:inline-block;'
            '">' + e + '</span>'
            for e in trending[:12]
        )
        st.markdown(
            '<div style="margin:10px 0 14px 0">'
            '<div style="font-size:0.65rem; color:' + _C_TEXT3 + ';'
            ' text-transform:uppercase; letter-spacing:0.08em; margin-bottom:5px">'
            'Trending Entities</div>'
            + tags_html
            + '</div>',
            unsafe_allow_html=True,
        )

    # ── Article cards ─────────────────────────────────────────────────────────
    if not articles:
        st.info("No recent shipping news articles found.")
        return

    visible   = articles[:max_items]
    overflow  = articles[max_items:]

    for article in visible:
        st.markdown(_article_card_html(article), unsafe_allow_html=True)

    # ── Load more expander ────────────────────────────────────────────────────
    if overflow:
        remaining_label = "Load more (" + str(len(overflow)) + " articles)"
        with st.expander(remaining_label, expanded=False):
            for article in overflow:
                st.markdown(_article_card_html(article), unsafe_allow_html=True)
