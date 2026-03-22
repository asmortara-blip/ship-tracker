"""Blank Sailing Tracker — near-term supply signal from carrier voyage cancellations.

Blank sailings (also called void sailings) are the single biggest short-run supply
signal in container shipping: when carriers cancel scheduled voyages, effective
capacity on a trade lane drops immediately, typically driving spot rates higher.

This module:
 • Polls four free/public RSS feeds from major maritime news sites.
 • Filters articles containing blank-sailing keywords.
 • Parses carrier name, trade route, week label, and TEU estimate from headlines.
 • Assigns an impact score based on same-route concentration.
 • Returns structured BlankSailing dataclasses and a summary dict.

All network I/O is guarded with try/except so the module degrades gracefully when
feeds are unreachable (e.g., in CI or offline environments).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

# ── Optional dependencies ──────────────────────────────────────────────────────
try:
    import feedparser  # type: ignore
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False
    logger.warning("feedparser not installed; blank sailing feed unavailable. pip install feedparser")

try:
    import streamlit as st
    _ST_OK = True
except ImportError:
    _ST_OK = False


# ── Feed URLs ──────────────────────────────────────────────────────────────────
BLANK_SAILING_FEEDS: list[str] = [
    "https://www.hellenicshippingnews.com/feed/",
    "https://splash247.com/feed/",
    "https://gcaptain.com/feed/",
    "https://www.maritime-executive.com/feed",
]

# ── Keyword filter ─────────────────────────────────────────────────────────────
_KEYWORDS: list[str] = [
    "blank sailing",
    "void sailing",
    "cancelled voyage",
    "canceled voyage",
    "service suspension",
    "capacity withdrawal",
    "blanked sailing",
    "blanked voyage",
    "cancelled departure",
    "canceled departure",
]

# ── Carrier name map ───────────────────────────────────────────────────────────
_CARRIERS: dict[str, str] = {
    "maersk":       "Maersk",
    "msc":          "MSC",
    "cma cgm":      "CMA CGM",
    "cma-cgm":      "CMA CGM",
    "cosco":        "COSCO",
    "evergreen":    "Evergreen",
    "hapag":        "Hapag-Lloyd",
    "hapag-lloyd":  "Hapag-Lloyd",
    "one ":         "ONE",
    "yang ming":    "Yang Ming",
    "yangming":     "Yang Ming",
    "hmm":          "HMM",
    "zim":          "ZIM",
    "pil":          "PIL",
    "wan hai":      "Wan Hai",
    "sm line":      "SM Line",
    "sea lead":     "Sea Lead",
}

# ── Route keyword map ──────────────────────────────────────────────────────────
_ROUTES: dict[str, str] = {
    "trans-pacific":          "Trans-Pacific",
    "transpacific":           "Trans-Pacific",
    "asia.europe":            "Asia-Europe",
    "asia-europe":            "Asia-Europe",
    "far east.europe":        "Asia-Europe",
    "far east europe":        "Asia-Europe",
    "transatlantic":          "Transatlantic",
    "trans-atlantic":         "Transatlantic",
    "asia.north america":     "Trans-Pacific",
    "asia-north america":     "Trans-Pacific",
    "us.asia":                "Trans-Pacific",
    "china.us":               "Trans-Pacific",
    "china-us":               "Trans-Pacific",
    "asia.latin":             "Asia-Latin America",
    "latin america":          "Asia-Latin America",
    "intra-asia":             "Intra-Asia",
    "intra asia":             "Intra-Asia",
    "middle east":            "Middle East",
    "indian subcontinent":    "Indian Subcontinent",
    "africa":                 "Africa",
    "mediterranean":          "Mediterranean",
    "north europe":           "Asia-Europe",
    "north america":          "Trans-Pacific",
}

# ── In-process cache (avoids re-fetching within a Python process lifetime) ─────
_CACHE: dict = {
    "ts": 0.0,
    "data": [],
}


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class BlankSailing:
    """A single confirmed or reported blank-sailing event."""
    carrier: str               # e.g. "Maersk"
    route: str                 # e.g. "Asia-Europe"
    departure_week: str        # e.g. "Week 14 2026"
    teus_removed: int          # estimated TEUs; 0 if not mentioned
    source: str                # human-readable feed name
    url: str                   # article URL
    published_dt: datetime     # publication timestamp (UTC)
    headline: str              # original article title
    impact_score: float = 0.0  # 0.0–1.0; higher = more supply removed


# ── Internal helpers ───────────────────────────────────────────────────────────

def _source_label(url: str) -> str:
    """Derive a short human-readable source label from a feed URL."""
    host = urlparse(url).netloc.lower()
    label_map = {
        "hellenicshippingnews.com": "Hellenic Shipping News",
        "splash247.com":            "Splash 247",
        "gcaptain.com":             "gCaptain",
        "maritime-executive.com":   "Maritime Executive",
    }
    for key, label in label_map.items():
        if key in host:
            return label
    return host


def _matches_keywords(text: str) -> bool:
    """Return True if *text* contains at least one blank-sailing keyword."""
    lower = text.lower()
    return any(kw in lower for kw in _KEYWORDS)


def _extract_carrier(text: str) -> str:
    """Return the first carrier name found in *text*, or 'Unknown'."""
    lower = text.lower()
    for pattern, name in _CARRIERS.items():
        if pattern in lower:
            return name
    return "Unknown"


def _extract_route(text: str) -> str:
    """Return the first trade route detected in *text*, or 'Multiple Routes'."""
    lower = text.lower()
    for pattern, name in _ROUTES.items():
        if re.search(re.escape(pattern), lower):
            return name
    return "Multiple Routes"


def _extract_teus(text: str) -> int:
    """Scan *text* for a TEU count (e.g. '5,000 TEU', '8500 containers')."""
    # Match patterns like "5,000 TEU", "8 500 TEUs", "10000 containers"
    patterns = [
        r"([\d,\s]+)\s*teus?\b",
        r"([\d,\s]+)\s*containers?\b",
    ]
    for pat in patterns:
        m = re.search(pat, text.lower())
        if m:
            raw = re.sub(r"[\s,]", "", m.group(1))
            try:
                val = int(raw)
                # Sanity: plausible TEU range 100 – 30 000
                if 100 <= val <= 30_000:
                    return val
            except ValueError:
                pass
    return 0


def _extract_departure_week(text: str, pub_dt: datetime) -> str:
    """Try to pull a specific week reference from the headline; fall back to pub-date week."""
    # Look for "week 14", "wk 14", "week 14 2026"
    m = re.search(r"\bweek\s+(\d{1,2})(?:\s+(\d{4}))?\b", text.lower())
    if m:
        week_num = int(m.group(1))
        year     = int(m.group(2)) if m.group(2) else pub_dt.year
        return f"Week {week_num} {year}"
    # Fall back to ISO week of publication date
    iso_cal   = pub_dt.isocalendar()
    return f"Week {iso_cal[1]} {iso_cal[0]}"


def _parse_published(entry) -> datetime:
    """Extract a timezone-aware UTC datetime from a feedparser entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            ts = time.mktime(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    return datetime.now(tz=timezone.utc)


def _assign_impact_scores(sailings: list[BlankSailing]) -> list[BlankSailing]:
    """
    Impact score = how many blank sailings share the same route.
    More blanks on one lane → higher supply pressure → higher score.
    Score = min(count_on_route * 0.2, 1.0)
    """
    from collections import Counter
    route_counts = Counter(s.route for s in sailings)
    for s in sailings:
        s.impact_score = min(route_counts[s.route] * 0.2, 1.0)
    return sailings


def _fetch_feed(feed_url: str, max_items: int) -> list[BlankSailing]:
    """Fetch a single RSS feed and return matching BlankSailing objects."""
    if not _FEEDPARSER_OK:
        return []
    results: list[BlankSailing] = []
    source_label = _source_label(feed_url)
    try:
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:max_items]:
            title   = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            full    = f"{title} {summary}"
            if not _matches_keywords(full):
                continue
            pub_dt = _parse_published(entry)
            link   = getattr(entry, "link", feed_url)
            results.append(BlankSailing(
                carrier        = _extract_carrier(full),
                route          = _extract_route(full),
                departure_week = _extract_departure_week(full, pub_dt),
                teus_removed   = _extract_teus(full),
                source         = source_label,
                url            = link,
                published_dt   = pub_dt,
                headline       = title,
            ))
    except Exception as exc:
        logger.warning(f"blank_sailing_feed: error fetching {feed_url}: {exc}")
    return results


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_blank_sailings(
    max_items: int = 30,
    cache_ttl_hours: float = 4.0,
) -> list[BlankSailing]:
    """Fetch recent blank sailing reports from all configured RSS feeds.

    Parameters
    ----------
    max_items:       Maximum entries to inspect per feed.
    cache_ttl_hours: How long (hours) to reuse an in-process cached result.

    Returns
    -------
    list[BlankSailing] — deduplicated, impact-scored, sorted newest-first.
    """
    now = time.time()
    ttl_sec = cache_ttl_hours * 3600

    if _CACHE["data"] and (now - _CACHE["ts"]) < ttl_sec:
        logger.debug("blank_sailing_feed: returning cached result")
        return _CACHE["data"]  # type: ignore[return-value]

    if not _FEEDPARSER_OK:
        logger.warning("blank_sailing_feed: feedparser unavailable; returning empty list")
        return []

    all_sailings: list[BlankSailing] = []
    seen_urls: set[str] = set()

    for feed_url in BLANK_SAILING_FEEDS:
        items = _fetch_feed(feed_url, max_items)
        for s in items:
            if s.url not in seen_urls:
                seen_urls.add(s.url)
                all_sailings.append(s)

    all_sailings = _assign_impact_scores(all_sailings)
    all_sailings.sort(key=lambda s: s.published_dt, reverse=True)

    _CACHE["ts"]   = now
    _CACHE["data"] = all_sailings  # type: ignore[assignment]

    logger.info(f"blank_sailing_feed: found {len(all_sailings)} blank sailing articles")
    return all_sailings


def get_blank_sailing_summary(sailings: list[BlankSailing]) -> dict:
    """Aggregate blank sailing data into a dashboard-ready summary dict.

    Parameters
    ----------
    sailings: Output of fetch_blank_sailings().

    Returns
    -------
    dict with keys:
        total_count          int
        total_teus_removed_estimate  int   (sum of known TEU figures)
        by_carrier           dict[str, int]  carrier → count
        by_route             dict[str, int]  route → count
        weekly_trend         list[dict]      [{week, count}] sorted by week label
        supply_impact_label  str   "CRITICAL" | "HIGH" | "MODERATE" | "LOW" | "MINIMAL"
    """
    from collections import Counter, defaultdict

    if not sailings:
        return {
            "total_count":                 0,
            "total_teus_removed_estimate": 0,
            "by_carrier":                  {},
            "by_route":                    {},
            "weekly_trend":                [],
            "supply_impact_label":         "MINIMAL",
        }

    total_count  = len(sailings)
    total_teus   = sum(s.teus_removed for s in sailings)
    by_carrier   = dict(Counter(s.carrier for s in sailings).most_common())
    by_route     = dict(Counter(s.route for s in sailings).most_common())

    # Weekly trend — count per week label
    week_counts: dict[str, int] = defaultdict(int)
    for s in sailings:
        week_counts[s.departure_week] += 1
    weekly_trend = [
        {"week": wk, "count": cnt}
        for wk, cnt in sorted(week_counts.items())
    ]

    # Supply impact label based on total count
    if total_count >= 15:
        label = "CRITICAL"
    elif total_count >= 10:
        label = "HIGH"
    elif total_count >= 5:
        label = "MODERATE"
    elif total_count >= 1:
        label = "LOW"
    else:
        label = "MINIMAL"

    return {
        "total_count":                 total_count,
        "total_teus_removed_estimate": total_teus,
        "by_carrier":                  by_carrier,
        "by_route":                    by_route,
        "weekly_trend":                weekly_trend,
        "supply_impact_label":         label,
    }
