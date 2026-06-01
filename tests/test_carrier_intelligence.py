"""Tests for data.carrier_intelligence — curated carrier profiles + RSS feed plumbing.

Covers:
  - CarrierProfile dataclass:
      * instantiation with minimal required fields, defaults to empty lists
      * to_dict round-trips every attribute, preserves list contents
  - get_carrier_profiles():
      * returns exactly 12 CarrierProfile entries (Q1 2026 top-12)
      * every profile has all 14 required attributes populated correctly typed
      * market_share_pct values are positive floats summing to plausible total
      * fleet_size, teu_capacity strictly positive integers
      * schedule_reliability, blank_sailing_rate in [0, 100]
      * outlook ∈ {"Positive", "Cautious", "Neutral", "Negative"}
      * alliance ∈ {"Gemini", "Premier", "MSC-independent", "unaffiliated"}
      * key_risks / key_strengths each contain 4 non-empty strings
      * function returns a fresh list each call (no shared mutable state)
  - get_alliance_breakdown():
      * mapping covers exactly the four alliance buckets
      * Gemini = Maersk + Hapag-Lloyd; MSC-independent = MSC only
      * union of all members equals the 12 profile names
  - get_market_concentration():
      * hhi computed as Σ(share^2), rounded to 1 decimal
      * hhi_category thresholds: >=2500 Highly, >=1500 Moderately, else Competitive
      * top3 / top5 / top10 monotonically non-decreasing, top10 ≤ total
      * carriers_ranked sorted descending by market_share_pct
      * carriers_ranked length == 12
  - get_schedule_reliability_ranking():
      * sorted descending by schedule_reliability
      * rank field starts at 1, increments by 1, length == 12
      * top entry is Hapag-Lloyd (74.1) per curated data
  - get_blank_sailing_alerts():
      * exactly 10 entries (Q1 2026 curated)
      * every entry has the 10 documented keys
      * teu_impact is a positive integer; announced_date parses as ISO date
  - Module-level catalogs:
      * CARRIER_FEEDS has 6 URL-shaped entries
      * FALLBACK_FEEDS is a non-empty list of URL strings
      * ALL_CARRIERS == list(CARRIER_FEEDS.keys())
      * _CATEGORY_KEYWORDS has the 4 documented categories with non-empty lists
      * _SENTIMENT_POSITIVE / _SENTIMENT_NEGATIVE / _IMPACT_HIGH non-empty
  - _classify_category:
      * "capacity"-heavy text → "capacity"
      * "rate" / "freight" → "rates"
      * "merger" / "alliance" → "m_and_a"
      * "LNG" / "emission" → "sustainability"
      * empty / unrelated text → "general"
      * case-insensitive matching
  - _score_sentiment:
      * empty text → 0.0
      * pure positive → 1.0 (clamped)
      * pure negative → -1.0
      * balanced → 0.0
      * result always in [-1.0, 1.0], rounded to 3 decimals
  - _score_impact:
      * empty text → 0.0
      * single keyword → 1/5 = 0.2
      * 5+ keywords → 1.0 (clamped)
  - _parse_published:
      * entry with published_parsed struct → UTC datetime from struct
      * entry missing both fields → datetime.now (within fresh delta)
      * malformed entry → fallback to now()
  - _entry_to_update:
      * blank title → None
      * full entry → CarrierUpdate with category/sentiment/impact populated
      * HTML tags stripped from summary
      * summary truncated to 500 chars
  - CarrierUpdate round-trip:
      * to_dict() yields ISO-format published_dt
      * from_dict round-trips datetime
      * from_dict handles malformed published_dt → falls back to now()
  - _cache_key:
      * lowercases, replaces spaces/slashes with underscore
  - Mem-cache:
      * _write then _read returns the same updates within TTL
      * outside TTL returns None
      * unknown carrier → None
  - get_carrier_intelligence_summary:
      * empty dict → sentinel ({most_active_carrier: "N/A", overall_tone: "Neutral"})
      * all-positive updates → overall_tone == "Bullish"
      * all-negative → "Bearish"
      * mixed → "Mixed"
      * carrier with most items wins most_active
      * capacity/rate signals capped at 5 and formatted "[carrier] headline"
  - fetch_carrier_updates (cache-hit path only — network is monkeypatched empty):
      * pre-populated mem cache short-circuits, returns cached items
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import pytest

from data.carrier_intelligence import (
    ALL_CARRIERS,
    CARRIER_FEEDS,
    CarrierProfile,
    CarrierUpdate,
    FALLBACK_FEEDS,
    _CATEGORY_KEYWORDS,
    _IMPACT_HIGH,
    _MEM_CACHE,
    _SENTIMENT_NEGATIVE,
    _SENTIMENT_POSITIVE,
    _cache_key,
    _classify_category,
    _entry_to_update,
    _parse_published,
    _read_mem_cache,
    _score_impact,
    _score_sentiment,
    _write_mem_cache,
    fetch_carrier_updates,
    get_alliance_breakdown,
    get_blank_sailing_alerts,
    get_carrier_intelligence_summary,
    get_carrier_profiles,
    get_market_concentration,
    get_schedule_reliability_ranking,
)


_VALID_OUTLOOKS = {"Positive", "Cautious", "Neutral", "Negative"}
_VALID_ALLIANCES = {"Gemini", "Premier", "MSC-independent", "unaffiliated"}


# ── CarrierProfile dataclass ─────────────────────────────────────────────────


def test_carrier_profile_defaults_empty_lists() -> None:
    p = CarrierProfile(
        name="Test", ticker="T", alliance="Gemini",
        market_share_pct=1.0, fleet_size=10, teu_capacity=1000,
        ytd_rate_change=0.0, blank_sailing_rate=5.0,
        schedule_reliability=80.0, q_revenue_bn=0.5,
        q_net_margin_pct=2.0, outlook="Neutral",
    )
    assert p.key_risks == []
    assert p.key_strengths == []
    # Defaults are independent per instance (no shared list).
    p.key_risks.append("risk")
    p2 = CarrierProfile(
        name="Other", ticker="O", alliance="Gemini",
        market_share_pct=1.0, fleet_size=10, teu_capacity=1000,
        ytd_rate_change=0.0, blank_sailing_rate=5.0,
        schedule_reliability=80.0, q_revenue_bn=0.5,
        q_net_margin_pct=2.0, outlook="Neutral",
    )
    assert p2.key_risks == []


def test_carrier_profile_to_dict_round_trip() -> None:
    p = CarrierProfile(
        name="Acme", ticker="ACM", alliance="Premier",
        market_share_pct=3.5, fleet_size=42, teu_capacity=10_000,
        ytd_rate_change=-5.0, blank_sailing_rate=8.0,
        schedule_reliability=70.0, q_revenue_bn=1.2,
        q_net_margin_pct=4.5, outlook="Positive",
        key_risks=["r1", "r2"], key_strengths=["s1"],
    )
    d = p.to_dict()
    assert d["name"] == "Acme"
    assert d["fleet_size"] == 42
    assert d["key_risks"] == ["r1", "r2"]
    assert d["key_strengths"] == ["s1"]
    assert d["outlook"] == "Positive"


# ── get_carrier_profiles catalog shape ───────────────────────────────────────


def test_get_carrier_profiles_returns_12_entries() -> None:
    profiles = get_carrier_profiles()
    assert len(profiles) == 12
    assert all(isinstance(p, CarrierProfile) for p in profiles)


def test_get_carrier_profiles_returns_fresh_list_each_call() -> None:
    a = get_carrier_profiles()
    b = get_carrier_profiles()
    assert a is not b  # not the same list object
    # Mutating one should not affect the other (no shared state).
    a[0].key_risks.append("MUTATION")
    assert "MUTATION" not in b[0].key_risks


def test_get_carrier_profiles_required_fields_typed() -> None:
    for p in get_carrier_profiles():
        assert isinstance(p.name, str) and p.name
        assert isinstance(p.ticker, str) and p.ticker
        assert isinstance(p.alliance, str)
        assert isinstance(p.market_share_pct, float)
        assert isinstance(p.fleet_size, int)
        assert isinstance(p.teu_capacity, int)
        assert isinstance(p.outlook, str)
        assert isinstance(p.key_risks, list)
        assert isinstance(p.key_strengths, list)


def test_get_carrier_profiles_value_ranges() -> None:
    for p in get_carrier_profiles():
        assert p.market_share_pct > 0
        assert p.fleet_size > 0
        assert p.teu_capacity > 0
        assert 0.0 <= p.schedule_reliability <= 100.0
        assert 0.0 <= p.blank_sailing_rate <= 100.0


def test_get_carrier_profiles_outlook_alliance_enum() -> None:
    for p in get_carrier_profiles():
        assert p.outlook in _VALID_OUTLOOKS, f"{p.name}: outlook={p.outlook}"
        assert p.alliance in _VALID_ALLIANCES, f"{p.name}: alliance={p.alliance}"


def test_get_carrier_profiles_key_lists_populated() -> None:
    for p in get_carrier_profiles():
        assert len(p.key_risks) == 4, f"{p.name}: {len(p.key_risks)} risks"
        assert len(p.key_strengths) == 4, f"{p.name}: {len(p.key_strengths)} strengths"
        assert all(isinstance(r, str) and r for r in p.key_risks)
        assert all(isinstance(s, str) and s for s in p.key_strengths)


def test_get_carrier_profiles_total_market_share_plausible() -> None:
    # The top-12 should cover the bulk of the market (>80%).
    total = sum(p.market_share_pct for p in get_carrier_profiles())
    assert 80.0 < total < 100.0


# ── get_alliance_breakdown ───────────────────────────────────────────────────


def test_alliance_breakdown_keys() -> None:
    bd = get_alliance_breakdown()
    assert set(bd.keys()) == _VALID_ALLIANCES


def test_alliance_breakdown_gemini_members() -> None:
    bd = get_alliance_breakdown()
    assert bd["Gemini"] == ["Maersk", "Hapag-Lloyd"]


def test_alliance_breakdown_msc_independent() -> None:
    bd = get_alliance_breakdown()
    msc_members = bd["MSC-independent"]
    assert len(msc_members) == 1
    assert "MSC" in msc_members[0]


def test_alliance_breakdown_union_covers_all_profiles() -> None:
    bd = get_alliance_breakdown()
    members = {m for ms in bd.values() for m in ms}
    profile_names = {p.name for p in get_carrier_profiles()}
    assert members == profile_names


# ── get_market_concentration ─────────────────────────────────────────────────


def test_market_concentration_hhi_formula() -> None:
    mc = get_market_concentration()
    shares = [p.market_share_pct for p in get_carrier_profiles()]
    expected = round(sum(s ** 2 for s in shares), 1)
    assert mc["hhi"] == expected


def test_market_concentration_category_thresholds() -> None:
    mc = get_market_concentration()
    hhi = mc["hhi"]
    cat = mc["hhi_category"]
    if hhi >= 2500:
        assert cat == "Highly Concentrated"
    elif hhi >= 1500:
        assert cat == "Moderately Concentrated"
    else:
        assert cat == "Competitive"


def test_market_concentration_top_n_monotonic() -> None:
    mc = get_market_concentration()
    assert mc["top3_share_pct"] <= mc["top5_share_pct"] <= mc["top10_share_pct"]
    assert mc["top10_share_pct"] <= mc["total_tracked_share_pct"]


def test_market_concentration_carriers_ranked_sorted_descending() -> None:
    mc = get_market_concentration()
    ranked = mc["carriers_ranked"]
    assert len(ranked) == 12
    shares = [share for _, share in ranked]
    assert shares == sorted(shares, reverse=True)
    # Top entry should be MSC (highest market share in Q1 2026 dataset).
    assert "MSC" in ranked[0][0]


def test_market_concentration_top3_matches_manual_sum() -> None:
    mc = get_market_concentration()
    profiles = sorted(get_carrier_profiles(),
                      key=lambda p: p.market_share_pct, reverse=True)
    expected = round(sum(p.market_share_pct for p in profiles[:3]), 1)
    assert mc["top3_share_pct"] == expected


# ── get_schedule_reliability_ranking ─────────────────────────────────────────


def test_schedule_ranking_sorted_descending_with_rank() -> None:
    ranking = get_schedule_reliability_ranking()
    assert len(ranking) == 12
    reliabilities = [r["schedule_reliability"] for r in ranking]
    assert reliabilities == sorted(reliabilities, reverse=True)
    assert [r["rank"] for r in ranking] == list(range(1, 13))


def test_schedule_ranking_top_entry_is_hapag_lloyd() -> None:
    ranking = get_schedule_reliability_ranking()
    top = ranking[0]
    assert top["name"] == "Hapag-Lloyd"
    assert top["schedule_reliability"] == 74.1
    assert top["rank"] == 1


def test_schedule_ranking_entry_shape() -> None:
    ranking = get_schedule_reliability_ranking()
    required_keys = {"rank", "name", "ticker", "alliance",
                     "schedule_reliability", "fleet_size", "outlook"}
    for entry in ranking:
        assert set(entry.keys()) == required_keys


# ── get_blank_sailing_alerts ─────────────────────────────────────────────────


def test_blank_sailing_alerts_count() -> None:
    alerts = get_blank_sailing_alerts()
    assert len(alerts) == 10


def test_blank_sailing_alerts_required_keys() -> None:
    required = {"carrier", "alliance", "trade_lane", "departure_week",
                "vessel_name", "port_omissions", "reason", "source",
                "announced_date", "teu_impact"}
    for alert in get_blank_sailing_alerts():
        assert set(alert.keys()) == required


def test_blank_sailing_alerts_teu_impact_positive_int() -> None:
    for alert in get_blank_sailing_alerts():
        assert isinstance(alert["teu_impact"], int)
        assert alert["teu_impact"] > 0


def test_blank_sailing_alerts_dates_parse_iso() -> None:
    for alert in get_blank_sailing_alerts():
        # Should be ISO-formatted yyyy-mm-dd and parse cleanly.
        dt = datetime.fromisoformat(alert["announced_date"])
        assert dt.year == 2026


def test_blank_sailing_alerts_alliance_matches_known_set() -> None:
    for alert in get_blank_sailing_alerts():
        assert alert["alliance"] in _VALID_ALLIANCES


# ── Module-level catalogs ────────────────────────────────────────────────────


def test_carrier_feeds_shape() -> None:
    assert isinstance(CARRIER_FEEDS, dict)
    assert len(CARRIER_FEEDS) == 6
    for name, url in CARRIER_FEEDS.items():
        assert isinstance(name, str) and name
        assert url.startswith("http")


def test_fallback_feeds_shape() -> None:
    assert isinstance(FALLBACK_FEEDS, list)
    assert len(FALLBACK_FEEDS) >= 1
    assert all(u.startswith("http") for u in FALLBACK_FEEDS)


def test_all_carriers_matches_feed_keys() -> None:
    assert ALL_CARRIERS == list(CARRIER_FEEDS.keys())


def test_category_keywords_documented_categories() -> None:
    assert set(_CATEGORY_KEYWORDS.keys()) == {
        "capacity", "rates", "m_and_a", "sustainability"
    }
    for cat, kws in _CATEGORY_KEYWORDS.items():
        assert len(kws) >= 5, f"{cat} has too few keywords"
        assert all(isinstance(k, str) and k for k in kws)


def test_sentiment_and_impact_catalogs_nonempty() -> None:
    assert len(_SENTIMENT_POSITIVE) >= 5
    assert len(_SENTIMENT_NEGATIVE) >= 5
    assert len(_IMPACT_HIGH) >= 5


# ── _classify_category ───────────────────────────────────────────────────────


def test_classify_category_capacity() -> None:
    text = "Carrier announces new vessel newbuild and fleet expansion"
    assert _classify_category(text) == "capacity"


def test_classify_category_rates() -> None:
    text = "Freight rate hike with GRI and spot index tariff"
    assert _classify_category(text) == "rates"


def test_classify_category_m_and_a() -> None:
    text = "Acquisition completed via partnership merger"
    assert _classify_category(text) == "m_and_a"


def test_classify_category_sustainability() -> None:
    text = "Methanol-powered LNG fleet reduces emission and carbon"
    assert _classify_category(text) == "sustainability"


def test_classify_category_empty_returns_general() -> None:
    assert _classify_category("") == "general"
    assert _classify_category("hello world") == "general"


def test_classify_category_case_insensitive() -> None:
    assert _classify_category("CAPACITY EXPANSION") == "capacity"


# ── _score_sentiment ─────────────────────────────────────────────────────────


def test_score_sentiment_empty_returns_zero() -> None:
    assert _score_sentiment("") == 0.0


def test_score_sentiment_pure_positive_returns_one() -> None:
    text = "growth surge rise recovery strong profit"
    assert _score_sentiment(text) == 1.0


def test_score_sentiment_pure_negative_returns_negative_one() -> None:
    text = "decline drop weak crisis loss"
    assert _score_sentiment(text) == -1.0


def test_score_sentiment_balanced_returns_zero() -> None:
    text = "growth and decline"  # 1 pos, 1 neg
    assert _score_sentiment(text) == 0.0


def test_score_sentiment_clamped_and_rounded() -> None:
    text = "growth growth growth decline"  # not a real text, but unique words
    score = _score_sentiment(text)
    assert -1.0 <= score <= 1.0
    # Result rounded to 3 decimals.
    assert round(score, 3) == score


# ── _score_impact ────────────────────────────────────────────────────────────


def test_score_impact_empty_returns_zero() -> None:
    assert _score_impact("") == 0.0


def test_score_impact_single_keyword() -> None:
    assert _score_impact("capacity") == 0.2


def test_score_impact_clamped_at_one() -> None:
    # 5+ unique keywords from _IMPACT_HIGH list.
    text = "capacity rate freight fleet route blank sailing acquisition merger"
    assert _score_impact(text) == 1.0


# ── _parse_published ─────────────────────────────────────────────────────────


def test_parse_published_with_struct() -> None:
    # struct_time tuple for 2026-01-15 12:00:00 UTC
    target_ts = time.mktime(time.struct_time((2026, 1, 15, 12, 0, 0, 0, 15, 0)))
    target_struct = time.localtime(target_ts)
    entry = {"published_parsed": target_struct}
    dt = _parse_published(entry)
    assert isinstance(dt, datetime)
    assert dt.year == 2026
    assert dt.tzinfo is not None


def test_parse_published_missing_fields_returns_now() -> None:
    entry = {}
    dt = _parse_published(entry)
    now = datetime.now(tz=timezone.utc)
    assert abs((dt - now).total_seconds()) < 5


def test_parse_published_malformed_struct_falls_back() -> None:
    # invalid struct should hit the except branch
    class BadEntry:
        def get(self, key, default=None):
            if key == "published_parsed":
                return "not-a-struct"
            return None
    dt = _parse_published(BadEntry())
    now = datetime.now(tz=timezone.utc)
    assert abs((dt - now).total_seconds()) < 5


# ── _entry_to_update ─────────────────────────────────────────────────────────


def test_entry_to_update_blank_title_returns_none() -> None:
    entry = {"title": "", "summary": "stuff", "link": "x"}
    assert _entry_to_update(entry, "Maersk") is None


def test_entry_to_update_strips_html_from_summary() -> None:
    entry = {
        "title": "Capacity reduction",
        "summary": "<p>Blank <b>sailing</b> announced</p>",
        "link": "http://x",
    }
    upd = _entry_to_update(entry, "MSC")
    assert upd is not None
    assert "<p>" not in upd.summary
    assert "<b>" not in upd.summary
    assert "Blank sailing announced" in upd.summary


def test_entry_to_update_populates_classification() -> None:
    entry = {
        "title": "Carrier raises freight rate via GRI",
        "summary": "spot index tariff increase",
        "link": "http://x",
    }
    upd = _entry_to_update(entry, "Hapag-Lloyd")
    assert upd is not None
    assert upd.carrier == "Hapag-Lloyd"
    assert upd.category == "rates"
    assert -1.0 <= upd.sentiment <= 1.0
    assert 0.0 <= upd.impact_score <= 1.0


def test_entry_to_update_truncates_long_summary() -> None:
    long_text = "x" * 1000
    entry = {"title": "T", "summary": long_text, "link": "u"}
    upd = _entry_to_update(entry, "MSC")
    assert upd is not None
    assert len(upd.summary) == 500


# ── CarrierUpdate round-trip ─────────────────────────────────────────────────


def test_carrier_update_to_dict_iso_datetime() -> None:
    dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    upd = CarrierUpdate(
        carrier="MSC", category="capacity", headline="H", summary="S",
        published_dt=dt, url="http://x", sentiment=0.1, impact_score=0.2,
    )
    d = upd.to_dict()
    assert d["published_dt"] == dt.isoformat()
    assert d["carrier"] == "MSC"


def test_carrier_update_from_dict_round_trip() -> None:
    dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    upd = CarrierUpdate(
        carrier="MSC", category="rates", headline="h", summary="s",
        published_dt=dt, url="u", sentiment=0.5, impact_score=0.3,
    )
    d = upd.to_dict()
    restored = CarrierUpdate.from_dict(d)
    assert restored.carrier == upd.carrier
    assert restored.category == upd.category
    assert restored.published_dt == dt


def test_carrier_update_from_dict_malformed_dt_falls_back() -> None:
    d = {
        "carrier": "Maersk", "category": "general", "headline": "h",
        "summary": "s", "url": "u", "sentiment": 0.0, "impact_score": 0.0,
        "published_dt": "not-a-date",
    }
    restored = CarrierUpdate.from_dict(d)
    now = datetime.now(tz=timezone.utc)
    assert abs((restored.published_dt - now).total_seconds()) < 5


# ── _cache_key ───────────────────────────────────────────────────────────────


def test_cache_key_lowercases_and_replaces_spaces() -> None:
    assert _cache_key("CMA CGM") == "cma_cgm"
    assert _cache_key("Hapag-Lloyd") == "hapag-lloyd"
    assert _cache_key("ABC/DEF GHI") == "abc_def_ghi"


# ── Mem cache ────────────────────────────────────────────────────────────────


def test_mem_cache_round_trip_within_ttl() -> None:
    _MEM_CACHE.clear()
    dt = datetime.now(tz=timezone.utc)
    updates = [CarrierUpdate(
        carrier="Maersk", category="rates", headline="h", summary="s",
        published_dt=dt, url="u", sentiment=0.0, impact_score=0.0,
    )]
    _write_mem_cache("Maersk", updates)
    out = _read_mem_cache("Maersk", ttl_hours=6.0)
    assert out is not None
    assert len(out) == 1
    assert out[0].carrier == "Maersk"


def test_mem_cache_expired_returns_none() -> None:
    _MEM_CACHE.clear()
    # Insert with a stale timestamp (1 hour ago).
    stale_ts = time.time() - 3600
    _MEM_CACHE[_cache_key("Maersk")] = (stale_ts, [])
    # TTL of 0.5 hours → 1 hour ago is expired.
    assert _read_mem_cache("Maersk", ttl_hours=0.5) is None


def test_mem_cache_unknown_carrier_returns_none() -> None:
    _MEM_CACHE.clear()
    assert _read_mem_cache("Nonexistent", ttl_hours=6.0) is None


# ── get_carrier_intelligence_summary ─────────────────────────────────────────


def test_summary_empty_returns_sentinel() -> None:
    out = get_carrier_intelligence_summary({})
    assert out == {
        "most_active_carrier": "N/A",
        "capacity_signals": [],
        "rate_signals": [],
        "overall_tone": "Neutral",
    }


def _make_update(carrier: str, category: str, sentiment: float,
                 headline: str = "Test headline") -> CarrierUpdate:
    return CarrierUpdate(
        carrier=carrier, category=category, headline=headline,
        summary="", published_dt=datetime.now(tz=timezone.utc),
        url="", sentiment=sentiment, impact_score=0.0,
    )


def test_summary_most_active_carrier_wins() -> None:
    updates = {
        "Maersk": [_make_update("Maersk", "general", 0.0)],
        "MSC": [
            _make_update("MSC", "capacity", 0.0),
            _make_update("MSC", "rates", 0.0),
        ],
    }
    out = get_carrier_intelligence_summary(updates)
    assert out["most_active_carrier"] == "MSC"


def test_summary_overall_tone_bullish() -> None:
    updates = {
        "MSC": [_make_update("MSC", "general", 0.5),
                _make_update("MSC", "general", 0.4)],
    }
    out = get_carrier_intelligence_summary(updates)
    assert out["overall_tone"] == "Bullish"


def test_summary_overall_tone_bearish() -> None:
    updates = {
        "MSC": [_make_update("MSC", "general", -0.5),
                _make_update("MSC", "general", -0.6)],
    }
    out = get_carrier_intelligence_summary(updates)
    assert out["overall_tone"] == "Bearish"


def test_summary_overall_tone_mixed() -> None:
    # Average near zero but with both signs present.
    updates = {
        "MSC": [_make_update("MSC", "general", 0.1),
                _make_update("MSC", "general", -0.1)],
    }
    out = get_carrier_intelligence_summary(updates)
    assert out["overall_tone"] == "Mixed"


def test_summary_signals_format_and_cap() -> None:
    # Build 7 capacity updates — only 5 should be retained.
    updates = {
        "MSC": [
            _make_update("MSC", "capacity", 0.0, headline=f"Capacity event {i}")
            for i in range(7)
        ],
        "Maersk": [
            _make_update("Maersk", "rates", 0.0, headline=f"Rate event {i}")
            for i in range(7)
        ],
    }
    out = get_carrier_intelligence_summary(updates)
    assert len(out["capacity_signals"]) == 5
    assert len(out["rate_signals"]) == 5
    # Format should be "[carrier] headline".
    assert out["capacity_signals"][0].startswith("[MSC] ")
    assert out["rate_signals"][0].startswith("[Maersk] ")


# ── fetch_carrier_updates: cache-hit path (no network) ───────────────────────


def test_fetch_carrier_updates_uses_mem_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """When mem cache is fresh, fetch_carrier_updates should never call the
    network plumbing. We pre-populate the cache and assert returned items
    match — and monkeypatch the feed parser to raise if anyone touches it.
    """
    _MEM_CACHE.clear()
    cached = [CarrierUpdate(
        carrier="Maersk", category="rates", headline="cached-headline",
        summary="", published_dt=datetime.now(tz=timezone.utc),
        url="", sentiment=0.0, impact_score=0.0,
    )]
    _write_mem_cache("Maersk", cached)

    def _exploding_parse(url):
        raise AssertionError(f"network call attempted to {url}")

    monkeypatch.setattr(
        "data.carrier_intelligence._parse_feed", _exploding_parse
    )

    out = fetch_carrier_updates(carriers=["Maersk"], cache_ttl_hours=6.0)
    assert "Maersk" in out
    assert len(out["Maersk"]) == 1
    assert out["Maersk"][0].headline == "cached-headline"


def test_fetch_carrier_updates_respects_max_per_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _MEM_CACHE.clear()
    now = datetime.now(tz=timezone.utc)
    cached = [
        CarrierUpdate(
            carrier="MSC", category="general", headline=f"h{i}",
            summary="", published_dt=now, url="",
            sentiment=0.0, impact_score=0.0,
        )
        for i in range(10)
    ]
    _write_mem_cache("MSC", cached)
    monkeypatch.setattr(
        "data.carrier_intelligence._parse_feed", lambda u: None
    )
    out = fetch_carrier_updates(carriers=["MSC"], max_per_carrier=3,
                                cache_ttl_hours=6.0)
    assert len(out["MSC"]) == 3
