"""Pure-function tests for processing.news_sentiment.

The news_sentiment module scrapes six RSS feeds, scores each article with
shipping-domain keyword rules, extracts entity / region / topic mentions,
deduplicates by token Jaccard similarity, caches results as JSON, and
optionally merges in NewsAPI articles. These tests pin:

  * `_score_sentiment` — keyword accumulation and [-1, +1] clamping;
    pure-bullish text scores positive, pure-bearish scores negative,
    mixed text cancels toward zero, neutral text scores 0.0.
  * `_label_from_score` — threshold mapping at ±0.05.
  * `_extract_entities` — port + route lookups, longest-first matching
    (so "port klang" beats "klang"), and dedup of multi-alias mentions.
  * `_score_relevance` — accumulates from `_SHIPPING_RELEVANCE_TERMS`
    and entity count; clamped to [0, 1].
  * `_classify_topic` — picks the topic with the highest keyword hit
    count; falls back to "general".
  * `_score_urgency` — urgency word boosts + "!" / "\\d+%" / ALLCAPS
    regex bumps; clamped to [0, 1].
  * `_extract_regions` — region keyword matching, dedupe per region.
  * `_titles_similar` / `_deduplicate` — Jaccard >= 0.70 collapses
    near-duplicates; empty title returns False, identical kept once.
  * `NewsArticle.to_dict` / `from_dict` — round-trip with ISO datetime,
    `age_str` formatting for minutes / hours / days.
  * `_cache_path` — both CacheManager-shaped and plain-string inputs.
  * `_load_json_cache` — missing path, stale TTL, malformed JSON,
    happy path round-trip with `_save_json_cache`.
  * `_parse_published` — happy path + missing-key fallback to now.
  * `_fetch_one_feed` — feedparser mocked, returns scored NewsArticles;
    HTML stripped from summary, articles older than _CUTOFF_DAYS dropped,
    entries missing title / link skipped, request exception swallowed.
  * `fetch_all_news` — uses cache when present, otherwise iterates RSS_FEEDS
    via patched `_fetch_one_feed`, sorts by relevance desc.
  * `get_sentiment_summary` — empty sentinel dict; populated case computes
    weighted overall score, bullish/bearish/neutral counts, top-5
    bullish/bearish, trending_entities, topic_breakdown, region_breakdown,
    urgency_distribution buckets, source_count, newsapi_articles tally.
  * `get_route_news` — filters by `_ROUTE_ENTITY_LOOKUP` entity overlap
    AND falls back to keyword search; sorts by published_dt desc.
  * `build_sentiment_trend` — empty input returns empty DataFrame;
    populated input produces date / avg_sentiment / count / bullish_pct
    / bearish_pct rows, full-date reindex fills gaps.
  * `get_top_stories` — empty sentinel, ranked combined-impact score.
  * `fetch_all_news_enhanced` — RSS-only path when NewsAPI key missing;
    NewsAPI merge with dedup when key is present (both `requests.get`
    and the NewsAPI module are mocked).

All Anthropic / requests / feedparser / NewsAPI calls are mocked; no
network access. Duck-typed dataclasses are used for inputs where the
real module exports rich types. Integer seeds only.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from processing.news_sentiment import (
    NewsArticle,
    RSS_FEEDS,
    REGION_KEYWORDS,
    _BULLISH_KEYWORDS,
    _BEARISH_KEYWORDS,
    _cache_path,
    _classify_topic,
    _deduplicate,
    _extract_entities,
    _extract_regions,
    _fetch_one_feed,
    _label_from_score,
    _load_json_cache,
    _parse_published,
    _save_json_cache,
    _score_relevance,
    _score_sentiment,
    _score_urgency,
    _titles_similar,
    build_sentiment_trend,
    fetch_all_news,
    fetch_all_news_enhanced,
    get_route_news,
    get_sentiment_summary,
    get_top_stories,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_article(
    title: str = "Container freight rates rise",
    score: float = 0.20,
    label: str = "BULLISH",
    entities: list[str] | None = None,
    relevance: float = 0.60,
    topic: str = "freight_rates",
    urgency: float = 0.10,
    regions: list[str] | None = None,
    source: str = "Hellenic Shipping News",
    summary: str = "Spot rates surged on tight capacity.",
    published_dt: datetime | None = None,
    url: str = "https://example.com/a1",
) -> NewsArticle:
    return NewsArticle(
        title=title,
        url=url,
        published_dt=published_dt or datetime.now(tz=timezone.utc),
        source=source,
        summary=summary,
        sentiment_score=score,
        sentiment_label=label,
        entities=entities if entities is not None else ["Shanghai"],
        relevance_score=relevance,
        topic=topic,
        urgency_score=urgency,
        regions=regions if regions is not None else ["Asia-Pacific"],
    )


class _FeedEntry(dict):
    """A feedparser-entry stand-in supporting `.get(...)` over a dict."""


# ── _score_sentiment ────────────────────────────────────────────────────────


def test_score_sentiment_empty_string_is_zero():
    assert _score_sentiment("") == 0.0


def test_score_sentiment_pure_bullish_positive():
    text = "Freight surge and record growth boost shipping rates"
    s = _score_sentiment(text)
    assert s > 0
    assert s <= 1.0


def test_score_sentiment_pure_bearish_negative():
    text = "Container freight rates plunge as demand crashes and slowdown deepens"
    s = _score_sentiment(text)
    assert s < 0
    assert s >= -1.0


def test_score_sentiment_clamps_to_upper_bound():
    # Every bullish keyword in one string; should clamp at +1.0.
    text = " ".join(phrase for phrase, _ in _BULLISH_KEYWORDS) * 3
    assert _score_sentiment(text) == 1.0


def test_score_sentiment_clamps_to_lower_bound():
    text = " ".join(phrase for phrase, _ in _BEARISH_KEYWORDS) * 3
    assert _score_sentiment(text) == -1.0


def test_score_sentiment_mixed_cancels():
    # One bullish + one bearish keyword of equal weight => net 0.
    text = "surge and decline"
    assert _score_sentiment(text) == pytest.approx(0.0, abs=1e-9)


def test_score_sentiment_is_case_insensitive():
    assert _score_sentiment("SURGE") == _score_sentiment("surge") == _score_sentiment("Surge")


# ── _label_from_score ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.10, "BULLISH"),
        (1.0, "BULLISH"),
        (-0.10, "BEARISH"),
        (-1.0, "BEARISH"),
        (0.0, "NEUTRAL"),
        (0.05, "NEUTRAL"),      # boundary — not > 0.05
        (-0.05, "NEUTRAL"),     # boundary — not < -0.05
        (0.0500001, "BULLISH"),
    ],
)
def test_label_from_score_thresholds(score, expected):
    assert _label_from_score(score) == expected


# ── _extract_entities ───────────────────────────────────────────────────────


def test_extract_entities_finds_canonical_names():
    text = "Shanghai port reports record TEU throughput"
    out = _extract_entities(text)
    assert "Shanghai" in out


def test_extract_entities_multi_alias_dedupe():
    # "shanghai" and "cnsha" both map to "Shanghai" — only one in output.
    text = "Shanghai CNSHA traffic surged"
    out = _extract_entities(text)
    assert out.count("Shanghai") == 1


def test_extract_entities_longest_first_match():
    # "port klang" must beat the shorter "klang" if the latter were a key.
    out = _extract_entities("Port Klang volumes are up")
    assert "Port Klang" in out


def test_extract_entities_route_mention():
    out = _extract_entities("Trans-Pacific freight rates climb on demand")
    assert "Trans-Pacific" in out


def test_extract_entities_red_sea_proxy_for_asia_europe():
    # "red sea" is mapped to "Asia-Europe" by domain convention.
    out = _extract_entities("Red Sea disruption forces reroute")
    assert "Asia-Europe" in out


def test_extract_entities_empty_string():
    assert _extract_entities("") == []


def test_extract_entities_no_known_terms():
    assert _extract_entities("the cat sat on the mat") == []


# ── _score_relevance ────────────────────────────────────────────────────────


def test_score_relevance_clamps_to_one():
    text = ("container freight shipping vessel port cargo teu carrier ocean "
            "maritime route charter bunker logistics supply chain fbx bdi scfi")
    entities = ["Shanghai", "Singapore", "Rotterdam", "Long Beach", "Hamburg"]
    assert _score_relevance(text, entities) == 1.0


def test_score_relevance_zero_when_no_terms_or_entities():
    assert _score_relevance("nothing here", []) == 0.0


def test_score_relevance_entity_boost_is_capped():
    # 10 entities should hit the 0.4 entity-cap.
    text = "nothing"
    entities = [f"E{i}" for i in range(10)]
    assert _score_relevance(text, entities) == 0.4


# ── _classify_topic ─────────────────────────────────────────────────────────


def test_classify_topic_picks_dominant_bucket():
    text = "container demand cargo shipment teu volume trade is climbing"
    assert _classify_topic(text) == "demand"


def test_classify_topic_general_when_no_keywords():
    assert _classify_topic("a sunny day") == "general"


def test_classify_topic_geopolitical_red_sea():
    text = "Red Sea conflict and Houthi attacks endanger the Suez chokepoint"
    assert _classify_topic(text) == "geopolitical"


# ── _score_urgency ──────────────────────────────────────────────────────────


def test_score_urgency_zero_for_calm_text():
    assert _score_urgency("a normal calm report") == 0.0


def test_score_urgency_words_accumulate():
    s = _score_urgency("BREAKING urgent crisis halt suspended")
    assert s > 0.5


def test_score_urgency_exclamation_bumps():
    base = _score_urgency("rates moved")
    bumped = _score_urgency("rates moved!")
    assert bumped > base


def test_score_urgency_pct_and_allcaps_bumps():
    s = _score_urgency("Rates climbed 15% — JUMP recorded")
    # +0.05 for "15%", +0.05 for "JUMP" => 0.10 (urgency-words list does not contain JUMP)
    assert s >= 0.10


def test_score_urgency_clamps_to_one():
    s = _score_urgency("breaking urgent alert warning immediate crisis emergency halt suspended force majeure")
    assert s == 1.0


# ── _extract_regions ────────────────────────────────────────────────────────


def test_extract_regions_basic_match():
    out = _extract_regions("Shanghai exports to Rotterdam fell")
    # Two regions hit; both should appear (deduped per-region by `break`).
    assert "Asia-Pacific" in out
    assert "Europe" in out


def test_extract_regions_dedupe_per_region():
    # Multiple Asia-Pacific keywords — region should appear exactly once.
    out = _extract_regions("china shanghai shenzhen singapore vietnam")
    assert out.count("Asia-Pacific") == 1


def test_extract_regions_empty():
    assert _extract_regions("") == []


def test_region_keywords_module_constant_shape():
    # Every value should be a non-empty list of strings.
    for region, kws in REGION_KEYWORDS.items():
        assert isinstance(region, str)
        assert isinstance(kws, list) and kws
        assert all(isinstance(k, str) for k in kws)


# ── _titles_similar / _deduplicate ──────────────────────────────────────────


def test_titles_similar_identical_returns_true():
    assert _titles_similar("Shanghai port surges", "Shanghai port surges") is True


def test_titles_similar_disjoint_returns_false():
    assert _titles_similar("freight rates rise", "weather report") is False


def test_titles_similar_empty_string():
    assert _titles_similar("", "anything at all") is False
    assert _titles_similar("anything", "") is False


def test_titles_similar_threshold_can_be_tightened():
    # "shanghai port surges" vs "shanghai port booms" — 2/4 = 0.5; below 0.70.
    assert _titles_similar("shanghai port surges", "shanghai port booms") is False


def test_deduplicate_collapses_near_duplicate_titles():
    a1 = _make_article(title="Shanghai port reports record volumes")
    a2 = _make_article(title="Shanghai port reports record volumes", url="https://e.com/dup")
    a3 = _make_article(title="Rotterdam strike forces diversions")
    kept = _deduplicate([a1, a2, a3])
    assert len(kept) == 2
    titles = {a.title for a in kept}
    assert "Shanghai port reports record volumes" in titles
    assert "Rotterdam strike forces diversions" in titles


def test_deduplicate_empty_returns_empty():
    assert _deduplicate([]) == []


# ── NewsArticle round-trip / age_str ────────────────────────────────────────


def test_news_article_to_dict_from_dict_round_trip():
    a = _make_article()
    d = a.to_dict()
    assert isinstance(d["published_dt"], str)  # ISO format
    a2 = NewsArticle.from_dict(d)
    assert a2.title == a.title
    assert a2.sentiment_label == a.sentiment_label
    assert a2.entities == a.entities


def test_news_article_from_dict_handles_bad_datetime():
    a = _make_article()
    d = a.to_dict()
    d["published_dt"] = "not-a-real-datetime"
    a2 = NewsArticle.from_dict(d)
    # Falls back to "now" — just verify it's a tz-aware datetime.
    assert isinstance(a2.published_dt, datetime)
    assert a2.published_dt.tzinfo is not None


def test_age_str_minutes_hours_days():
    now = datetime.now(tz=timezone.utc)
    a_min = _make_article(published_dt=now - timedelta(minutes=5))
    a_hr = _make_article(published_dt=now - timedelta(hours=3))
    a_day = _make_article(published_dt=now - timedelta(days=2))
    assert a_min.age_str.endswith("m ago")
    assert a_hr.age_str.endswith("h ago")
    assert a_day.age_str.endswith("d ago")


def test_age_str_handles_naive_datetime():
    # Naive datetimes should be treated as UTC, not crash.
    naive = datetime.utcnow() - timedelta(hours=2)
    a = _make_article(published_dt=naive)
    assert a.age_str.endswith("h ago")


# ── _cache_path ─────────────────────────────────────────────────────────────


def test_cache_path_with_cache_manager_like_object():
    mock_cache = MagicMock()
    mock_cache.cache_dir = "/tmp/foo"
    p = _cache_path(mock_cache)
    assert p.startswith("/tmp/foo/")
    assert p.endswith(".json")


def test_cache_path_with_string():
    p = _cache_path("/tmp/foo")
    assert p.startswith("/tmp/foo/")
    assert "news_sentiment" in p
    assert p.endswith("all_feeds.json")


# ── _load_json_cache / _save_json_cache ─────────────────────────────────────


def test_load_json_cache_missing_returns_none(tmp_path):
    p = str(tmp_path / "does_not_exist.json")
    assert _load_json_cache(p, ttl_hours=24) is None


def test_load_json_cache_stale_returns_none(tmp_path):
    p = tmp_path / "stale.json"
    p.write_text("[]", encoding="utf-8")
    # Force mtime far in the past.
    very_old = (datetime.now() - timedelta(days=7)).timestamp()
    os.utime(str(p), (very_old, very_old))
    assert _load_json_cache(str(p), ttl_hours=1.0) is None


def test_load_json_cache_corrupt_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json", encoding="utf-8")
    assert _load_json_cache(str(p), ttl_hours=24.0) is None


def test_save_then_load_round_trip(tmp_path):
    a1 = _make_article(title="Article 1")
    a2 = _make_article(title="Article 2", url="https://example.com/a2")
    path = str(tmp_path / "news_sentiment" / "all_feeds.json")
    _save_json_cache(path, [a1, a2])
    loaded = _load_json_cache(path, ttl_hours=24.0)
    assert loaded is not None
    assert len(loaded) == 2
    assert {a.title for a in loaded} == {"Article 1", "Article 2"}


# ── _parse_published ────────────────────────────────────────────────────────


def test_parse_published_happy_path():
    entry = _FeedEntry(published_parsed=(2026, 5, 21, 12, 0, 0, 0, 0, 0))
    dt = _parse_published(entry)
    assert dt.tzinfo is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 21


def test_parse_published_missing_falls_back_to_now():
    entry = _FeedEntry()
    dt = _parse_published(entry)
    # Just verify it returned a tz-aware datetime close to "now".
    assert dt.tzinfo is not None
    assert abs((datetime.now(tz=timezone.utc) - dt).total_seconds()) < 5


# ── _fetch_one_feed (network + feedparser mocked) ───────────────────────────


def _make_parsed_feed(entries: list[dict], bozo: int = 0) -> MagicMock:
    """Build a feedparser-like mock with `.get('entries')` / `.get('bozo')`."""
    m = MagicMock()
    m.get = lambda k, default=None: {"entries": entries, "bozo": bozo}.get(k, default)
    return m


def test_fetch_one_feed_returns_scored_articles():
    now = datetime.now(tz=timezone.utc)
    entries = [
        {
            "title": "Container freight surge on Trans-Pacific route",
            "link": "https://example.com/x1",
            "summary": "<p>Rates jumped on Shanghai-Los Angeles lane.</p>",
            "published_parsed": (now.year, now.month, now.day, 12, 0, 0, 0, 0, 0),
        }
    ]
    parsed = _make_parsed_feed(entries)
    with patch("processing.news_sentiment._REQUESTS_OK", True), \
         patch("processing.news_sentiment._FEEDPARSER_OK", True), \
         patch("processing.news_sentiment._requests") as mock_req, \
         patch("processing.news_sentiment._feedparser") as mock_fp:
        mock_resp = MagicMock()
        mock_resp.content = b"<rss></rss>"
        mock_req.get.return_value = mock_resp
        mock_fp.parse.return_value = parsed
        out = _fetch_one_feed("Hellenic Shipping News", "https://test.feed/rss")
    assert len(out) == 1
    art = out[0]
    assert art.source == "Hellenic Shipping News"
    assert art.sentiment_label in {"BULLISH", "BEARISH", "NEUTRAL"}
    # HTML stripped from summary.
    assert "<p>" not in art.summary
    assert "</p>" not in art.summary


def test_fetch_one_feed_skips_old_entries():
    very_old = datetime.now(tz=timezone.utc) - timedelta(days=30)
    entries = [
        {
            "title": "Ancient news",
            "link": "https://example.com/old",
            "summary": "stale",
            "published_parsed": (very_old.year, very_old.month, very_old.day, 0, 0, 0, 0, 0, 0),
        }
    ]
    parsed = _make_parsed_feed(entries)
    with patch("processing.news_sentiment._REQUESTS_OK", True), \
         patch("processing.news_sentiment._FEEDPARSER_OK", True), \
         patch("processing.news_sentiment._requests") as mock_req, \
         patch("processing.news_sentiment._feedparser") as mock_fp:
        mock_resp = MagicMock()
        mock_resp.content = b""
        mock_req.get.return_value = mock_resp
        mock_fp.parse.return_value = parsed
        out = _fetch_one_feed("Lloyd's List", "https://test.feed/rss")
    assert out == []


def test_fetch_one_feed_skips_entries_missing_title_or_link():
    entries = [
        {"title": "", "link": "https://example.com/x", "summary": "no title"},
        {"title": "Real title", "link": "", "summary": "no link"},
    ]
    parsed = _make_parsed_feed(entries)
    with patch("processing.news_sentiment._REQUESTS_OK", True), \
         patch("processing.news_sentiment._FEEDPARSER_OK", True), \
         patch("processing.news_sentiment._requests") as mock_req, \
         patch("processing.news_sentiment._feedparser") as mock_fp:
        mock_resp = MagicMock()
        mock_resp.content = b""
        mock_req.get.return_value = mock_resp
        mock_fp.parse.return_value = parsed
        out = _fetch_one_feed("Splash 247", "https://test.feed/rss")
    assert out == []


def test_fetch_one_feed_swallows_request_exception():
    with patch("processing.news_sentiment._REQUESTS_OK", True), \
         patch("processing.news_sentiment._FEEDPARSER_OK", True), \
         patch("processing.news_sentiment._requests") as mock_req:
        mock_req.get.side_effect = RuntimeError("network down")
        out = _fetch_one_feed("Maritime Executive", "https://test.feed/rss")
    assert out == []


def test_fetch_one_feed_disabled_when_feedparser_missing():
    with patch("processing.news_sentiment._FEEDPARSER_OK", False):
        assert _fetch_one_feed("X", "https://test.feed/rss") == []


# ── fetch_all_news ──────────────────────────────────────────────────────────


def test_fetch_all_news_uses_cache_when_fresh(tmp_path):
    # Pre-populate cache file at the path _cache_path() will resolve.
    a = _make_article(title="Cached article")
    cache_dir = str(tmp_path)
    path = _cache_path(cache_dir)
    _save_json_cache(path, [a])
    with patch("processing.news_sentiment._fetch_one_feed") as mock_fetch:
        out = fetch_all_news(cache_dir, ttl_hours=24.0)
    # Cache hit — no per-feed fetch.
    assert mock_fetch.call_count == 0
    assert len(out) == 1
    assert out[0].title == "Cached article"


def test_fetch_all_news_iterates_all_feeds_on_cache_miss(tmp_path):
    cache_dir = str(tmp_path)
    a = _make_article(title="Fetched article", relevance=0.8)
    with patch("processing.news_sentiment._fetch_one_feed", return_value=[a]) as mock_fetch:
        out = fetch_all_news(cache_dir, ttl_hours=0.0001)
    # One call per registered feed.
    assert mock_fetch.call_count == len(RSS_FEEDS)
    # Output is deduped (all six articles share the same title).
    assert len(out) == 1


def test_fetch_all_news_sorts_by_relevance_desc(tmp_path):
    cache_dir = str(tmp_path)
    low = _make_article(title="Low rel", relevance=0.1, url="https://e.com/low")
    high = _make_article(title="High rel", relevance=0.9, url="https://e.com/high")

    # Cycle through articles so each feed returns a different one.
    feeds = list(RSS_FEEDS.keys())
    feed_returns = {feeds[0]: [high], feeds[1]: [low]}
    for f in feeds[2:]:
        feed_returns[f] = []

    def _side_effect(source_name, url):
        return feed_returns.get(source_name, [])

    with patch("processing.news_sentiment._fetch_one_feed", side_effect=_side_effect):
        out = fetch_all_news(cache_dir, ttl_hours=0.0001)

    assert [a.title for a in out] == ["High rel", "Low rel"]


# ── get_sentiment_summary ───────────────────────────────────────────────────


def test_get_sentiment_summary_empty_returns_sentinel():
    s = get_sentiment_summary([])
    assert s["article_count"] == 0
    assert s["overall_score"] == 0.0
    assert s["label"] == "NEUTRAL"
    assert s["top_bullish"] == []
    assert s["top_bearish"] == []
    assert s["trending_entities"] == []
    assert s["urgency_distribution"] == {"high": 0, "medium": 0, "low": 0}
    assert s["newsapi_articles"] == 0


def test_get_sentiment_summary_counts_labels():
    articles = [
        _make_article(title="t1", label="BULLISH", score=0.5),
        _make_article(title="t2", label="BULLISH", score=0.4),
        _make_article(title="t3", label="BEARISH", score=-0.5),
        _make_article(title="t4", label="NEUTRAL", score=0.0),
    ]
    s = get_sentiment_summary(articles)
    assert s["article_count"] == 4
    assert s["bullish_count"] == 2
    assert s["bearish_count"] == 1
    assert s["neutral_count"] == 1
    # overall_score weighted by relevance (all equal), should be > 0.
    assert s["overall_score"] > 0
    assert s["label"] == "BULLISH"


def test_get_sentiment_summary_top_bullish_sorted_desc():
    articles = [
        _make_article(title=f"t{i}", label="BULLISH", score=s / 10.0)
        for i, s in enumerate([1, 2, 3, 4, 5, 6, 7])
    ]
    s = get_sentiment_summary(articles)
    top = s["top_bullish"]
    assert len(top) == 5  # capped at 5
    scores = [a.sentiment_score for a in top]
    assert scores == sorted(scores, reverse=True)


def test_get_sentiment_summary_trending_entities_sorted_by_count():
    articles = [
        _make_article(title="t1", entities=["Shanghai", "Singapore"]),
        _make_article(title="t2", entities=["Shanghai"]),
        _make_article(title="t3", entities=["Shanghai", "Rotterdam"]),
    ]
    out = get_sentiment_summary(articles)["trending_entities"]
    assert out[0] == "Shanghai"   # 3 mentions wins


def test_get_sentiment_summary_topic_breakdown():
    articles = [
        _make_article(title="t1", topic="freight_rates", score=0.2),
        _make_article(title="t2", topic="freight_rates", score=0.4),
        _make_article(title="t3", topic="demand", score=-0.1),
    ]
    tb = get_sentiment_summary(articles)["topic_breakdown"]
    assert tb["freight_rates"]["count"] == 2
    assert tb["demand"]["count"] == 1
    # avg_sentiment ≈ 0.3 for freight_rates
    assert tb["freight_rates"]["avg_sentiment"] == pytest.approx(0.3, abs=1e-3)


def test_get_sentiment_summary_urgency_buckets():
    articles = [
        _make_article(title="hi", urgency=0.9),
        _make_article(title="md", urgency=0.3),
        _make_article(title="lo", urgency=0.05),
    ]
    u = get_sentiment_summary(articles)["urgency_distribution"]
    assert u == {"high": 1, "medium": 1, "low": 1}


def test_get_sentiment_summary_newsapi_tally():
    articles = [
        _make_article(title="rss", source="Hellenic Shipping News"),
        _make_article(title="newsapi", source="Reuters"),
    ]
    s = get_sentiment_summary(articles)
    assert s["newsapi_articles"] == 1
    assert s["source_count"] == 2


def test_get_sentiment_summary_overall_score_clamped():
    # All max-bullish — weighted overall should be 1.0 (not above).
    articles = [_make_article(title=f"t{i}", score=1.0, relevance=1.0) for i in range(3)]
    s = get_sentiment_summary(articles)
    assert s["overall_score"] == 1.0
    assert s["label"] == "BULLISH"


# ── get_route_news ──────────────────────────────────────────────────────────


def test_get_route_news_filters_by_entity_overlap():
    a_tp = _make_article(title="LA-Shanghai surge", entities=["Trans-Pacific", "Shanghai"])
    a_ae = _make_article(title="Rotterdam rebound", entities=["Asia-Europe", "Rotterdam"])
    out = get_route_news("transpacific_eb", [a_tp, a_ae])
    assert a_tp in out
    assert a_ae not in out


def test_get_route_news_fallback_keyword_match():
    # No entity overlap, but route_id words appear in title.
    a = _make_article(
        title="Asia europe trade lane recovers",
        entities=[],
        summary="No entities here.",
    )
    out = get_route_news("asia_europe", [a])
    assert a in out


def test_get_route_news_sorts_by_published_desc():
    now = datetime.now(tz=timezone.utc)
    older = _make_article(title="Older", entities=["Trans-Pacific"], published_dt=now - timedelta(days=2))
    newer = _make_article(title="Newer", entities=["Trans-Pacific"], published_dt=now - timedelta(hours=1))
    out = get_route_news("transpacific_eb", [older, newer])
    assert out[0] is newer
    assert out[1] is older


def test_get_route_news_unknown_route_returns_empty_on_no_keyword_match():
    a = _make_article(title="Random", entities=["Shanghai"])
    out = get_route_news("nonexistent_route_id_xyz", [a])
    # No entity overlap, and "nonexistent route id xyz" not in title — filtered.
    assert out == []


# ── build_sentiment_trend ───────────────────────────────────────────────────


def test_build_sentiment_trend_empty_returns_empty_df():
    df = build_sentiment_trend([])
    assert hasattr(df, "columns")
    assert len(df) == 0
    assert set(df.columns) == {"date", "avg_sentiment", "count", "bullish_pct", "bearish_pct"}


def test_build_sentiment_trend_one_day_one_row():
    now = datetime.now(tz=timezone.utc)
    articles = [
        _make_article(title="a", score=0.2, label="BULLISH", published_dt=now),
        _make_article(title="b", score=-0.1, label="BEARISH", published_dt=now),
    ]
    df = build_sentiment_trend(articles, days=30)
    # One unique date → exactly one populated row (after reindex, still 1).
    assert len(df) == 1
    row = df.iloc[0]
    assert row["count"] == 2
    assert row["bullish_pct"] == 50.0
    assert row["bearish_pct"] == 50.0


def test_build_sentiment_trend_filters_outside_window():
    now = datetime.now(tz=timezone.utc)
    in_window = _make_article(title="recent", published_dt=now - timedelta(days=1))
    out_window = _make_article(title="old", published_dt=now - timedelta(days=60))
    df = build_sentiment_trend([in_window, out_window], days=30)
    # Only the in-window article should drive any data; the row count after
    # reindex equals 1 unique day.
    assert len(df) == 1


def test_build_sentiment_trend_reindexes_full_date_range():
    import pandas as pd
    now = datetime.now(tz=timezone.utc)
    # Two articles 3 days apart — reindex should produce 3 rows (day 0, 1, 2).
    a1 = _make_article(title="day0", published_dt=now - timedelta(days=2))
    a2 = _make_article(title="day2", published_dt=now)
    df = build_sentiment_trend([a1, a2], days=30)
    assert len(df) == 3
    # Middle day should have NaN count.
    assert pd.isna(df.iloc[1]["count"])


# ── get_top_stories ─────────────────────────────────────────────────────────


def test_get_top_stories_empty_returns_empty():
    assert get_top_stories([]) == []


def test_get_top_stories_ranks_by_combined_score():
    # Article A: high abs-sentiment, low relevance/urgency.
    a = _make_article(title="A", score=0.9, relevance=0.0, urgency=0.0)
    # Article B: low sentiment, high relevance, high urgency.
    b = _make_article(title="B", score=0.0, relevance=1.0, urgency=1.0)
    # B impact = 0.4 + 0.2 = 0.6; A impact = 0.36. B should rank first.
    out = get_top_stories([a, b], n=2)
    assert out[0].title == "B"
    assert out[1].title == "A"


def test_get_top_stories_caps_at_n():
    articles = [_make_article(title=f"t{i}", relevance=i / 10.0) for i in range(15)]
    out = get_top_stories(articles, n=5)
    assert len(out) == 5


# ── fetch_all_news_enhanced ─────────────────────────────────────────────────


def test_fetch_all_news_enhanced_rss_only_when_newsapi_unavailable(tmp_path):
    cache_dir = str(tmp_path)
    rss_articles = [_make_article(title="RSS only", relevance=0.7)]

    with patch("processing.news_sentiment.fetch_all_news", return_value=rss_articles):
        # Simulate the newsapi module being importable but key not configured.
        mock_mod = MagicMock()
        mock_mod.newsapi_available.return_value = False
        with patch.dict("sys.modules", {"data.newsapi_feed": mock_mod}):
            out = fetch_all_news_enhanced(cache_dir, max_items=50, cache_ttl_hours=1.0)

    assert len(out) == 1
    assert out[0].title == "RSS only"


def test_fetch_all_news_enhanced_merges_newsapi_articles(tmp_path):
    cache_dir = str(tmp_path)
    rss = [_make_article(title="Rotterdam strike forces diversions", relevance=0.6)]

    # Two NewsAPI dicts: one a duplicate (matches RSS title), one unique.
    newsapi_dicts = [
        {
            "title": "Rotterdam strike forces diversions",
            "url": "https://newsapi.example/dup",
            "description": "Same story from NewsAPI.",
            "source_name": "Reuters",
            "published_at": "2026-05-20T10:00:00Z",
        },
        {
            "title": "Shanghai port records new monthly TEU peak",
            "url": "https://newsapi.example/unique",
            "description": "Volumes climbed on robust export demand.",
            "source_name": "Bloomberg",
            "published_at": "2026-05-21T09:00:00Z",
        },
    ]

    mock_mod = MagicMock()
    mock_mod.newsapi_available.return_value = True
    mock_mod.fetch_newsapi_articles.return_value = newsapi_dicts

    with patch("processing.news_sentiment.fetch_all_news", return_value=rss), \
         patch.dict("sys.modules", {"data.newsapi_feed": mock_mod}):
        out = fetch_all_news_enhanced(cache_dir, max_items=50, cache_ttl_hours=1.0)

    titles = [a.title for a in out]
    # The duplicate should NOT appear twice (collapsed by similarity dedup).
    assert titles.count("Rotterdam strike forces diversions") == 1
    # The unique NewsAPI article should be present.
    assert "Shanghai port records new monthly TEU peak" in titles


def test_fetch_all_news_enhanced_caps_at_max_items(tmp_path):
    cache_dir = str(tmp_path)
    # 10 unique RSS articles.
    rss = [_make_article(title=f"RSS {i}", url=f"https://e.com/{i}", relevance=0.5 + i * 0.01)
           for i in range(10)]
    mock_mod = MagicMock()
    mock_mod.newsapi_available.return_value = False

    with patch("processing.news_sentiment.fetch_all_news", return_value=rss), \
         patch.dict("sys.modules", {"data.newsapi_feed": mock_mod}):
        out = fetch_all_news_enhanced(cache_dir, max_items=4, cache_ttl_hours=1.0)

    assert len(out) == 4


def test_fetch_all_news_enhanced_handles_rss_exception(tmp_path):
    cache_dir = str(tmp_path)
    mock_mod = MagicMock()
    mock_mod.newsapi_available.return_value = False

    with patch("processing.news_sentiment.fetch_all_news",
               side_effect=RuntimeError("rss kaput")), \
         patch.dict("sys.modules", {"data.newsapi_feed": mock_mod}):
        out = fetch_all_news_enhanced(cache_dir, max_items=50, cache_ttl_hours=1.0)

    # Should return an empty list, not raise.
    assert out == []


def test_extract_regions_word_boundary_no_substring_false_positives() -> None:
    """Regression: bare 'us'/'eu' region tokens must match on WORD BOUNDARIES,
    not as substrings of Russia/August/customs/Reuters/neutral — the substring
    test mistagged nearly every article's region."""
    from processing.news_sentiment import _extract_regions
    assert "North America" not in _extract_regions(
        "Russia tightens August customs checks on surplus cargo")
    assert "Europe" not in _extract_regions(
        "Reuters: a neutral outlook from the cluster survey")
    # Genuine standalone US / EU mentions still tag correctly.
    assert "North America" in _extract_regions("US ports report record volume")
    assert "Europe" in _extract_regions("EU tariffs hit container rates")


def test_deduplicate_keeps_higher_relevance_copy() -> None:
    """Regression: among near-duplicate titles, dedup keeps the HIGHER
    relevance_score copy (the docstring contract), not whichever was fetched
    first (old behavior: survivor was just feed-iteration order, and dedup
    runs before the relevance sort)."""
    from processing.news_sentiment import _deduplicate
    title = "Suez Canal traffic resumes after Houthi ceasefire pause ends"
    low = _make_article(title=title, relevance=0.10)
    high = _make_article(title=title + " today", relevance=0.90)  # near-identical
    kept = _deduplicate([low, high])          # low encountered first
    assert len(kept) == 1
    assert kept[0].relevance_score == 0.90    # higher-relevance copy survives
