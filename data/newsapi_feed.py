"""
data/newsapi_feed.py
────────────────────
NewsAPI feed for shipping-related news.

Fetches articles from three shipping-focused queries, deduplicates by URL,
filters junk, caches results via CacheManager (JSON sidecar), and returns
a plain list of dicts ready for consumption by processing/news_sentiment.py.

Free-tier constraints:
  - 100 requests/day
  - Articles from last 30 days only
  - Up to 100 results per request

Dependencies: requests, loguru, tenacity, streamlit (secrets), data.cache_manager
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import streamlit as st
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from data.cache_manager import CacheManager

# ── API constants ─────────────────────────────────────────────────────────────

_BASE_URL    = "https://newsapi.org/v2/everything"
_TIMEOUT     = 15          # seconds per request
_CACHE_SOURCE = "newsapi"
_INTER_REQUEST_DELAY = 1.0  # seconds between queries (rate-limit courtesy)

# ── Shipping-specific queries ─────────────────────────────────────────────────

SHIPPING_QUERIES: list[dict] = [
    {
        "q":        "shipping freight container rates",
        "sortBy":   "publishedAt",
        "language": "en",
        "pageSize": 40,
    },
    {
        "q":        "port congestion supply chain logistics",
        "sortBy":   "relevancy",
        "language": "en",
        "pageSize": 30,
    },
    {
        "q":        "ZIM MATX SBLK maritime trade tariff",
        "sortBy":   "publishedAt",
        "language": "en",
        "pageSize": 30,
    },
]


# ── API key helper ────────────────────────────────────────────────────────────


def _get_api_key() -> str:
    """Get NewsAPI key from Streamlit secrets or environment."""
    try:
        key = st.secrets.get("NEWS_API_KEY", "")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("NEWS_API_KEY", "")


def newsapi_available() -> bool:
    """Returns True if a NewsAPI key is configured."""
    return bool(_get_api_key())


# ── Cache helpers (JSON sidecar — mirrors news_sentiment pattern) ─────────────


def _cache_key(date_str: str) -> str:
    return f"newsapi_shipping_{date_str}"


def _cache_path(cache: CacheManager, date_str: str) -> Path:
    slug = CacheManager._slugify(_cache_key(date_str))
    return cache.cache_dir / _CACHE_SOURCE / f"{slug}.json"


def _load_json_cache(path: Path, ttl_hours: float) -> list[dict] | None:
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl_hours * 3600:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        logger.debug("NewsAPI cache hit: {} articles from {}", len(data), path)
        return data
    except Exception as exc:
        logger.warning("NewsAPI cache load error: {}", exc)
        return None


def _save_json_cache(path: Path, articles: list[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(articles, fh, indent=2, default=str)
        logger.debug("NewsAPI: cached {} articles → {}", len(articles), path)
    except Exception as exc:
        logger.warning("NewsAPI cache write error: {}", exc)


# ── HTTP helpers ──────────────────────────────────────────────────────────────


class _RateLimitError(Exception):
    """Raised when NewsAPI returns HTTP 429."""


@retry(
    retry=retry_if_exception_type(_RateLimitError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    reraise=True,
)
def _request_query(params: dict, api_key: str) -> list[dict]:
    """
    Execute a single NewsAPI /everything request.

    Raises _RateLimitError on HTTP 429 so tenacity can retry with backoff.
    Returns a list of raw article dicts from the API response.
    """
    full_params = {**params, "apiKey": api_key}
    try:
        resp = requests.get(_BASE_URL, params=full_params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("NewsAPI request error: {}", exc)
        return []

    if resp.status_code == 401:
        logger.error("NewsAPI: invalid API key (401). Check NEWS_API_KEY.")
        return []

    if resp.status_code == 429:
        logger.warning("NewsAPI: rate limited (429). Will retry.")
        raise _RateLimitError("rate limited")

    if resp.status_code != 200:
        logger.warning("NewsAPI: unexpected status {} for query '{}'", resp.status_code, params.get("q"))
        return []

    try:
        payload = resp.json()
    except Exception as exc:
        logger.warning("NewsAPI: JSON decode error: {}", exc)
        return []

    if payload.get("status") != "ok":
        logger.warning("NewsAPI: non-ok status in response: {}", payload.get("message", "unknown"))
        return []

    return payload.get("articles", [])


# ── Article normaliser ────────────────────────────────────────────────────────


def _parse_published_at(raw: str | None) -> datetime:
    """Parse ISO 8601 string from NewsAPI into a timezone-aware datetime."""
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            pass
    return datetime.now(tz=timezone.utc)


def _normalise_article(raw: dict) -> dict | None:
    """
    Convert a raw NewsAPI article dict into the canonical shape expected by
    news_sentiment.py.

    Returns None if the article should be filtered out.
    """
    title       = (raw.get("title") or "").strip()
    url         = (raw.get("url")   or "").strip()
    description = (raw.get("description") or "").strip()
    content     = (raw.get("content")     or "").strip()
    author      = (raw.get("author")      or "").strip()
    source_name = (raw.get("source", {}) or {}).get("name", "") or ""
    published_raw = raw.get("publishedAt")

    # Filter out removed articles
    if "[Removed]" in title or "[Removed]" in (description or "") or not title or not url:
        return None

    published_at = _parse_published_at(published_raw)

    return {
        "title":        title,
        "description":  description,
        "url":          url,
        "source_name":  source_name,
        "published_at": published_at,
        "content":      content[:500] if content else description[:500],
        "author":       author,
    }


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_newsapi_articles(
    cache: CacheManager | None = None,
    max_articles: int = 100,
    cache_ttl_hours: float = 2.0,
    from_days_back: int = 7,
) -> list[dict]:
    """
    Fetch shipping-related news from NewsAPI.

    Runs up to three targeted queries (SHIPPING_QUERIES), deduplicates by URL,
    filters removed/junk articles, sorts newest-first, and caches results.

    Args:
        cache:           CacheManager instance for JSON caching.  If None a
                         temporary CacheManager pointed at "cache/" is created.
        max_articles:    Cap on total articles returned.
        cache_ttl_hours: Cache TTL in hours (default 2.0).
        from_days_back:  Only include articles published within this many days.

    Returns:
        list[dict] — each dict has keys:
            title, description, url, source_name,
            published_at (datetime), content, author.
        Returns [] if key not configured or all requests fail.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("NewsAPI: NEWS_API_KEY not configured — skipping NewsAPI fetch")
        return []

    if cache is None:
        cache = CacheManager("cache")

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    path = _cache_path(cache, date_str)

    cached = _load_json_cache(path, cache_ttl_hours)
    if cached is not None:
        return cached[:max_articles]

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=from_days_back)
    # NewsAPI free tier only goes back 30 days; enforce that ceiling too
    from_date = (datetime.now(tz=timezone.utc) - timedelta(days=min(from_days_back, 29))).strftime("%Y-%m-%dT%H:%M:%SZ")

    raw_articles: list[dict] = []
    seen_urls: set[str] = set()

    for i, query in enumerate(SHIPPING_QUERIES):
        if i > 0:
            time.sleep(_INTER_REQUEST_DELAY)

        params = {**query, "from": from_date}
        logger.info("NewsAPI: fetching query {} — '{}'", i + 1, query.get("q"))

        try:
            raw = _request_query(params, api_key)
        except _RateLimitError:
            logger.error("NewsAPI: rate limit retry exhausted for query '{}'", query.get("q"))
            continue
        except Exception as exc:
            logger.warning("NewsAPI: query {} failed: {}", i + 1, exc)
            continue

        for item in raw:
            normalised = _normalise_article(item)
            if normalised is None:
                continue
            url = normalised["url"]
            if url in seen_urls:
                continue
            # Filter by age
            if normalised["published_at"] < cutoff:
                continue
            seen_urls.add(url)
            raw_articles.append(normalised)

    # Sort newest-first
    raw_articles.sort(key=lambda a: a["published_at"], reverse=True)

    # Apply cap
    result = raw_articles[:max_articles]

    if result:
        _save_json_cache(path, result)

    logger.info(
        "NewsAPI: {} unique articles after dedup/filter ({} queries run)",
        len(result),
        len(SHIPPING_QUERIES),
    )
    return result
