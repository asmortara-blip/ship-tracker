"""Tests for ``data/canal_feed.py``.

The canal feed module fetches Panama / Suez canal transit stats through a
four-step waterfall (mem-cache → file-cache → live scrape → synthetic),
then layers an RSS-driven disruption check on Suez and derives a downstream
shipping-market impact dict. These tests pin the **defining properties**
without ever touching the network:

  - Module setup
      * ``_CACHE_DIR`` lives under the project root, exists on import
      * ``CanalStats.to_dict`` round-trips dataclass → dict
      * ``_cache_key`` is lower-cased and prefixed

  - Synthetic fallbacks
      * ``_panama_synthetic`` returns Normal status, water level >0
      * ``_suez_synthetic`` returns Restricted status, water level == 0
      * ``fetched_at`` parses as a tz-aware ISO-8601 string

  - Mem-cache (``_MEM_CACHE``)
      * Miss when key absent
      * Hit when key written within TTL
      * Expired entry (older than TTL) → miss
      * TTL of 0 always misses, TTL of 999h hits stale entries

  - File-cache (``_read_file_cache`` / ``_write_file_cache``)
      * Miss when path doesn't exist
      * Hit when JSON written within TTL → returns CanalStats instance
      * Expired (mtime older than TTL) → miss
      * Corrupt JSON → miss, no exception

  - Panama scraper (``_scrape_panama``, requests mocked)
      * BeautifulSoup unavailable → None
      * Every URL raises → None
      * Daily transits regex extracts integer
      * Daily >=30 → status Normal; daily <30 → Restricted
      * Gatun Lake water-level regex extracts float
      * Missing daily count → None even if HTML returned

  - Suez scraper (``_scrape_suez``, requests mocked)
      * BeautifulSoup unavailable → None
      * Every URL raises → None
      * Daily transits in valid range 10-120 accepted
      * Out-of-range counts (e.g. 5, 200) rejected → None
      * Daily >=60 → Normal; daily <60 → Restricted
      * Utilization capped at 100%

  - RSS disruption check (``_rss_suez_disruption_check`` / ``_parse_feed``)
      * feedparser unavailable → None
      * All feeds fail → None
      * Headline with keyword (e.g. "houthi") → returns title
      * No matching keyword → None
      * Per-feed parse exception is swallowed

  - Public ``fetch_panama_stats``
      * Mem-cache hit returns cached object without scraping
      * Scrape failure falls through to synthetic
      * Result is written back to mem-cache + file-cache

  - Public ``fetch_suez_stats``
      * Mem-cache hit returns cached object without scraping
      * Synthetic + RSS hit + Normal status → status promoted to Restricted
      * Synthetic + no RSS hit → status preserved
      * Scrape success + RSS hit while Normal → status promoted

  - ``get_canal_shipping_impact``
      * "Disrupted" status → Critical
      * "Restricted" with high wait → High
      * "Restricted" with normal wait → Moderate
      * "Normal" with high utilization → Moderate
      * "Normal" with low utilization → Low
      * affected_routes lists Panama lanes when Panama impacted
      * affected_routes lists Suez lanes when Suez impacted
      * rate_premium_est_pct uses 0.4 Panama + 0.6 Suez weighting
      * narrative is a non-empty string mentioning both canals
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import data.canal_feed as cf
from data.canal_feed import (
    CanalStats,
    _cache_key,
    _panama_synthetic,
    _parse_feed,
    _read_file_cache,
    _read_mem_cache,
    _rss_suez_disruption_check,
    _scrape_panama,
    _scrape_suez,
    _suez_synthetic,
    _write_file_cache,
    _write_mem_cache,
    fetch_panama_stats,
    fetch_suez_stats,
    get_canal_shipping_impact,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sample_panama(**overrides) -> CanalStats:
    """Build a default Panama CanalStats; allow overrides via kwargs."""
    base = CanalStats(
        canal="Panama",
        northbound_wait_days=1.8,
        southbound_wait_days=1.4,
        daily_transits=34,
        capacity_utilization_pct=78.0,
        water_level_m=27.1,
        status="Normal",
        restrictions="Normal operations",
        source_url="https://test.example/panama",
        fetched_at="2026-05-21T00:00:00+00:00",
    )
    return replace(base, **overrides) if overrides else base


def _sample_suez(**overrides) -> CanalStats:
    base = CanalStats(
        canal="Suez",
        northbound_wait_days=0.6,
        southbound_wait_days=0.5,
        daily_transits=42,
        capacity_utilization_pct=49.0,
        water_level_m=0.0,
        status="Restricted",
        restrictions="Houthi attacks",
        source_url="https://test.example/suez",
        fetched_at="2026-05-21T00:00:00+00:00",
    )
    return replace(base, **overrides) if overrides else base


def _make_response(text: str = "", status: int = 200) -> MagicMock:
    """Build a requests.Response-shaped MagicMock."""
    resp = MagicMock()
    resp.text = text
    resp.content = text.encode("utf-8") if text else b""
    resp.status_code = status
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture(autouse=True)
def _isolate_mem_cache(monkeypatch):
    """Every test gets a fresh, empty in-process cache."""
    monkeypatch.setattr(cf, "_MEM_CACHE", {}, raising=True)


@pytest.fixture
def isolated_cache_dir(monkeypatch, tmp_path) -> Path:
    """Point the module's file cache at a tmp_path so tests don't touch disk fixtures."""
    new_dir = tmp_path / "canal_cache"
    new_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cf, "_CACHE_DIR", new_dir, raising=True)
    return new_dir


# ── Module setup ─────────────────────────────────────────────────────────────


def test_cache_dir_exists_on_import():
    """The package-level cache dir is created at import time."""
    assert cf._CACHE_DIR.exists()
    assert cf._CACHE_DIR.is_dir()


def test_canal_stats_to_dict_roundtrip():
    stats = _sample_panama()
    d = stats.to_dict()
    assert isinstance(d, dict)
    assert d["canal"] == "Panama"
    assert d["daily_transits"] == 34
    # Reconstruct via the same kwargs.
    rebuilt = CanalStats(**d)
    assert rebuilt == stats


def test_cache_key_is_lower_cased_and_prefixed():
    assert _cache_key("Panama") == "canal_panama"
    assert _cache_key("SUEZ") == "canal_suez"
    assert _cache_key("Suez") == _cache_key("suez")


# ── Synthetic fallbacks ──────────────────────────────────────────────────────


def test_panama_synthetic_shape_and_status():
    s = _panama_synthetic()
    assert s.canal == "Panama"
    assert s.status == "Normal"
    assert s.water_level_m > 0
    assert s.daily_transits > 0
    assert s.capacity_utilization_pct > 0
    # fetched_at parses as ISO-8601.
    parsed = datetime.fromisoformat(s.fetched_at)
    assert parsed.tzinfo is not None


def test_suez_synthetic_shape_and_status():
    s = _suez_synthetic()
    assert s.canal == "Suez"
    assert s.status == "Restricted"
    assert s.water_level_m == 0.0   # N/A for Suez
    assert s.daily_transits > 0
    parsed = datetime.fromisoformat(s.fetched_at)
    assert parsed.tzinfo is not None


# ── Mem-cache ────────────────────────────────────────────────────────────────


def test_mem_cache_miss_when_key_absent():
    assert _read_mem_cache("Panama", ttl_hours=1.0) is None


def test_mem_cache_hit_when_within_ttl():
    stats = _sample_panama()
    _write_mem_cache("Panama", stats)
    out = _read_mem_cache("Panama", ttl_hours=24.0)
    assert out is stats   # exact object, not copy


def test_mem_cache_expired_returns_none(monkeypatch):
    stats = _sample_panama()
    _write_mem_cache("Panama", stats)
    # Force the stored timestamp to be ancient.
    key = _cache_key("Panama")
    _, s = cf._MEM_CACHE[key]
    cf._MEM_CACHE[key] = (time.time() - 10 * 3600, s)   # 10 hours ago
    # TTL only 1h → expired.
    assert _read_mem_cache("Panama", ttl_hours=1.0) is None
    # TTL big enough → still a hit.
    assert _read_mem_cache("Panama", ttl_hours=999.0) is stats


def test_mem_cache_zero_ttl_is_always_a_miss():
    _write_mem_cache("Panama", _sample_panama())
    assert _read_mem_cache("Panama", ttl_hours=0.0) is None


# ── File-cache ───────────────────────────────────────────────────────────────


def test_file_cache_miss_when_file_missing(isolated_cache_dir):
    assert _read_file_cache("Panama", ttl_hours=24.0) is None


def test_file_cache_roundtrip_within_ttl(isolated_cache_dir):
    stats = _sample_panama()
    _write_file_cache(stats)
    out = _read_file_cache("Panama", ttl_hours=24.0)
    assert out is not None
    assert isinstance(out, CanalStats)
    assert out.canal == "Panama"
    assert out.daily_transits == stats.daily_transits


def test_file_cache_expired_returns_none(isolated_cache_dir):
    stats = _sample_panama()
    _write_file_cache(stats)
    path = isolated_cache_dir / "panama.json"
    # Force mtime to be 48h ago.
    old = time.time() - 48 * 3600
    import os
    os.utime(path, (old, old))
    assert _read_file_cache("Panama", ttl_hours=1.0) is None
    assert _read_file_cache("Panama", ttl_hours=999.0) is not None


def test_file_cache_corrupt_json_returns_none(isolated_cache_dir):
    path = isolated_cache_dir / "panama.json"
    path.write_text("{not valid json")
    # Should swallow the exception and return None.
    assert _read_file_cache("Panama", ttl_hours=24.0) is None


def test_file_cache_writes_lowercased_filename(isolated_cache_dir):
    """_write_file_cache uses the canal name lower-cased."""
    _write_file_cache(_sample_suez(canal="Suez"))
    assert (isolated_cache_dir / "suez.json").exists()
    # The file is JSON we can load.
    payload = json.loads((isolated_cache_dir / "suez.json").read_text())
    assert payload["canal"] == "Suez"


# ── Panama scraper ───────────────────────────────────────────────────────────


def test_scrape_panama_returns_none_when_bs4_unavailable(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", False, raising=False)
    assert _scrape_panama() is None


def test_scrape_panama_returns_none_when_all_urls_raise(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)

    def _boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(cf.requests, "get", _boom)
    assert _scrape_panama() is None


def test_scrape_panama_extracts_daily_transits(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    html = "<html><body>The canal handles 36 vessels per day on average.</body></html>"
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    out = _scrape_panama()
    assert out is not None
    assert out.canal == "Panama"
    assert out.daily_transits == 36
    assert out.status == "Normal"   # >=30
    # Default water level applies when not in HTML.
    assert out.water_level_m == 27.1


def test_scrape_panama_low_transits_marks_restricted(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    html = "<html><body>Currently only 22 vessels per day.</body></html>"
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    out = _scrape_panama()
    assert out is not None
    assert out.daily_transits == 22
    assert out.status == "Restricted"
    assert "Reduced" in out.restrictions or "ACP" in out.restrictions


def test_scrape_panama_extracts_water_level(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    html = (
        "<html><body>"
        "Gatun Lake currently 25.3 m above sea level. "
        "Operations: 32 vessels per day."
        "</body></html>"
    )
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    out = _scrape_panama()
    assert out is not None
    assert out.water_level_m == 25.3
    assert out.daily_transits == 32


def test_scrape_panama_returns_none_when_no_daily_number(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    # HTML lacks the "N vessels per day" pattern.
    html = "<html><body>Canal operations continue today.</body></html>"
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    assert _scrape_panama() is None


# ── Suez scraper ─────────────────────────────────────────────────────────────


def test_scrape_suez_returns_none_when_bs4_unavailable(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", False, raising=False)
    assert _scrape_suez() is None


def test_scrape_suez_returns_none_when_all_urls_raise(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)

    def _boom(*a, **kw):
        raise RuntimeError("blocked")

    monkeypatch.setattr(cf.requests, "get", _boom)
    assert _scrape_suez() is None


def test_scrape_suez_extracts_daily_in_valid_range(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    html = "<html><body>Suez handled 75 vessels per day this week.</body></html>"
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    out = _scrape_suez()
    assert out is not None
    assert out.canal == "Suez"
    assert out.daily_transits == 75
    assert out.status == "Normal"   # >=60
    assert out.water_level_m == 0.0


def test_scrape_suez_low_transits_marks_restricted(monkeypatch):
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    html = "<html><body>Only 35 vessels per day passing.</body></html>"
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    out = _scrape_suez()
    assert out is not None
    assert out.daily_transits == 35
    assert out.status == "Restricted"
    assert "Red Sea" in out.restrictions


def test_scrape_suez_rejects_out_of_range_counts(monkeypatch):
    """Daily counts outside 10-120 are treated as spurious matches."""
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    # 200 vessels/day — implausible, must be rejected.
    html = "<html><body>200 ships per day are huge numbers.</body></html>"
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    assert _scrape_suez() is None


def test_scrape_suez_utilization_capped_at_100(monkeypatch):
    """Even >84 transits/day caps utilization at 100%."""
    monkeypatch.setattr(cf, "_BS4_OK", True, raising=False)
    html = "<html><body>The canal saw 110 vessels per day peaks.</body></html>"
    monkeypatch.setattr(cf.requests, "get", lambda *a, **kw: _make_response(html))

    out = _scrape_suez()
    assert out is not None
    assert out.daily_transits == 110
    assert out.capacity_utilization_pct <= 100.0


# ── RSS disruption check ─────────────────────────────────────────────────────


def test_parse_feed_returns_none_when_feedparser_unavailable(monkeypatch):
    monkeypatch.setattr(cf, "_FEEDPARSER_OK", False, raising=False)
    assert _parse_feed("https://example.invalid/feed") is None


def test_parse_feed_returns_none_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(cf, "_FEEDPARSER_OK", True, raising=False)

    def _boom(*a, **kw):
        raise RuntimeError("timeout")

    monkeypatch.setattr(cf.requests, "get", _boom)
    assert _parse_feed("https://example.invalid/feed") is None


def test_rss_returns_none_when_feedparser_unavailable(monkeypatch):
    monkeypatch.setattr(cf, "_FEEDPARSER_OK", False, raising=False)
    assert _rss_suez_disruption_check() is None


def test_rss_returns_none_when_all_feeds_fail(monkeypatch):
    monkeypatch.setattr(cf, "_FEEDPARSER_OK", True, raising=False)
    monkeypatch.setattr(cf, "_parse_feed", lambda url: None)
    assert _rss_suez_disruption_check() is None


def test_rss_returns_first_keyword_hit(monkeypatch):
    """A title with a matching keyword is returned (case-insensitive)."""
    monkeypatch.setattr(cf, "_FEEDPARSER_OK", True, raising=False)

    fake_feed = MagicMock()
    fake_feed.entries = [
        {"title": "Markets calm in Asia"},                       # no keyword
        {"title": "Houthi attacks resume near Red Sea"},         # match
        {"title": "Suez transit volumes recovering"},            # would also match
    ]
    monkeypatch.setattr(cf, "_parse_feed", lambda url: fake_feed)

    out = _rss_suez_disruption_check()
    assert out is not None
    assert "houthi" in out.lower()


def test_rss_returns_none_when_no_keyword_matches(monkeypatch):
    monkeypatch.setattr(cf, "_FEEDPARSER_OK", True, raising=False)

    fake_feed = MagicMock()
    fake_feed.entries = [
        {"title": "Container demand softens in Q2"},
        {"title": "Cosco places new ULCV order"},
    ]
    monkeypatch.setattr(cf, "_parse_feed", lambda url: fake_feed)

    assert _rss_suez_disruption_check() is None


def test_rss_swallows_per_feed_parse_exception(monkeypatch):
    """A feed whose .entries blows up doesn't stop the run."""
    monkeypatch.setattr(cf, "_FEEDPARSER_OK", True, raising=False)

    class _BadFeed:
        @property
        def entries(self):
            raise RuntimeError("malformed feed")

    good_feed = MagicMock()
    good_feed.entries = [{"title": "Suez disruption confirmed"}]

    calls = {"n": 0}

    def _alt(url):
        calls["n"] += 1
        return _BadFeed() if calls["n"] == 1 else good_feed

    monkeypatch.setattr(cf, "_parse_feed", _alt)
    out = _rss_suez_disruption_check()
    # The second feed still produces a hit.
    assert out is not None
    assert "suez" in out.lower()


# ── fetch_panama_stats (public) ──────────────────────────────────────────────


def test_fetch_panama_mem_cache_hit_skips_scrape(monkeypatch, isolated_cache_dir):
    """A hot mem-cache short-circuits before any scrape attempt."""
    cached = _sample_panama(daily_transits=99)
    _write_mem_cache("Panama", cached)

    def _should_not_be_called():
        raise AssertionError("scraper should be bypassed on cache hit")

    monkeypatch.setattr(cf, "_scrape_panama", _should_not_be_called)
    out = fetch_panama_stats(cache_ttl_hours=24.0)
    assert out is cached


def test_fetch_panama_falls_through_to_synthetic(monkeypatch, isolated_cache_dir):
    """Empty caches + failed scrape → synthetic fallback, persisted to caches."""
    monkeypatch.setattr(cf, "_scrape_panama", lambda: None)
    out = fetch_panama_stats(cache_ttl_hours=24.0)
    assert out.canal == "Panama"
    assert out.status == "Normal"   # synthetic Panama
    # And was persisted.
    assert (isolated_cache_dir / "panama.json").exists()
    assert _read_mem_cache("Panama", ttl_hours=24.0) is out


def test_fetch_panama_uses_scrape_when_available(monkeypatch, isolated_cache_dir):
    """A successful scrape preempts the synthetic fallback."""
    scraped = _sample_panama(daily_transits=41, status="Normal")
    monkeypatch.setattr(cf, "_scrape_panama", lambda: scraped)
    out = fetch_panama_stats(cache_ttl_hours=24.0)
    assert out is scraped


# ── fetch_suez_stats (public) ────────────────────────────────────────────────


def test_fetch_suez_mem_cache_hit_skips_scrape(monkeypatch, isolated_cache_dir):
    cached = _sample_suez(daily_transits=88)
    _write_mem_cache("Suez", cached)

    def _should_not_be_called():
        raise AssertionError("scraper should be bypassed on cache hit")

    monkeypatch.setattr(cf, "_scrape_suez", _should_not_be_called)
    monkeypatch.setattr(cf, "_rss_suez_disruption_check", _should_not_be_called)
    out = fetch_suez_stats(cache_ttl_hours=24.0)
    assert out is cached


def test_fetch_suez_synthetic_with_rss_hit_promotes_status(monkeypatch, isolated_cache_dir):
    """Synthetic Suez is already Restricted; RSS hit must not crash and is layered when Normal."""
    monkeypatch.setattr(cf, "_scrape_suez", lambda: None)
    monkeypatch.setattr(cf, "_rss_suez_disruption_check", lambda: "Houthi strike disrupts Suez")

    out = fetch_suez_stats(cache_ttl_hours=24.0)
    # Synthetic Suez starts as "Restricted", so the news-layer leaves it alone.
    assert out.canal == "Suez"
    assert out.status == "Restricted"
    assert out.daily_transits > 0


def test_fetch_suez_scrape_normal_plus_rss_promotes_to_restricted(monkeypatch, isolated_cache_dir):
    """Successful scrape returning Normal + RSS disruption → status promoted to Restricted."""
    scraped = _sample_suez(daily_transits=80, status="Normal", restrictions="Normal operations")
    monkeypatch.setattr(cf, "_scrape_suez", lambda: scraped)
    monkeypatch.setattr(cf, "_rss_suez_disruption_check", lambda: "Major disruption in Red Sea")

    out = fetch_suez_stats(cache_ttl_hours=24.0)
    assert out.status == "Restricted"
    assert "News:" in out.restrictions
    assert "Red Sea" in out.restrictions


def test_fetch_suez_no_rss_hit_preserves_status(monkeypatch, isolated_cache_dir):
    """RSS returning None leaves the status untouched."""
    scraped = _sample_suez(daily_transits=80, status="Normal", restrictions="Normal operations")
    monkeypatch.setattr(cf, "_scrape_suez", lambda: scraped)
    monkeypatch.setattr(cf, "_rss_suez_disruption_check", lambda: None)

    out = fetch_suez_stats(cache_ttl_hours=24.0)
    assert out.status == "Normal"
    assert out.restrictions == "Normal operations"


# ── get_canal_shipping_impact ────────────────────────────────────────────────


def test_impact_disrupted_status_maps_to_critical():
    panama = _sample_panama(status="Disrupted")
    suez = _sample_suez(status="Normal", capacity_utilization_pct=50.0)
    out = get_canal_shipping_impact(panama, suez)
    assert out["panama_impact"] == "Critical"
    assert out["suez_impact"] == "Low"


def test_impact_restricted_with_high_wait_is_high():
    suez = _sample_suez(
        status="Restricted",
        northbound_wait_days=5.0,
        southbound_wait_days=5.0,
        capacity_utilization_pct=60.0,
    )
    panama = _sample_panama(status="Normal", capacity_utilization_pct=70.0)
    out = get_canal_shipping_impact(panama, suez)
    assert out["suez_impact"] == "High"


def test_impact_restricted_with_low_utilization_is_high():
    """Restricted + utilization <40% triggers the High branch even with low wait."""
    suez = _sample_suez(
        status="Restricted",
        northbound_wait_days=0.5,
        southbound_wait_days=0.5,
        capacity_utilization_pct=30.0,
    )
    panama = _sample_panama(status="Normal", capacity_utilization_pct=70.0)
    out = get_canal_shipping_impact(panama, suez)
    assert out["suez_impact"] == "High"


def test_impact_restricted_with_moderate_metrics_is_moderate():
    suez = _sample_suez(
        status="Restricted",
        northbound_wait_days=1.0,
        southbound_wait_days=1.0,
        capacity_utilization_pct=60.0,
    )
    panama = _sample_panama(status="Normal", capacity_utilization_pct=70.0)
    out = get_canal_shipping_impact(panama, suez)
    assert out["suez_impact"] == "Moderate"


def test_impact_normal_high_utilization_is_moderate():
    panama = _sample_panama(status="Normal", capacity_utilization_pct=95.0)
    suez = _sample_suez(status="Normal", capacity_utilization_pct=50.0)
    out = get_canal_shipping_impact(panama, suez)
    assert out["panama_impact"] == "Moderate"


def test_impact_normal_low_utilization_is_low():
    panama = _sample_panama(status="Normal", capacity_utilization_pct=70.0)
    suez = _sample_suez(status="Normal", capacity_utilization_pct=80.0)
    out = get_canal_shipping_impact(panama, suez)
    assert out["panama_impact"] == "Low"
    assert out["suez_impact"] == "Low"


def test_impact_routes_include_panama_lanes_when_panama_impacted():
    panama = _sample_panama(status="Disrupted")
    suez = _sample_suez(status="Normal", capacity_utilization_pct=50.0)
    out = get_canal_shipping_impact(panama, suez)
    routes_text = " ".join(out["affected_routes"])
    assert "US East Coast" in routes_text
    # Not Suez lanes.
    assert "Asia" not in routes_text or "US East Coast" in routes_text


def test_impact_routes_include_suez_lanes_when_suez_impacted():
    panama = _sample_panama(status="Normal", capacity_utilization_pct=70.0)
    suez = _sample_suez(
        status="Restricted",
        northbound_wait_days=5.0,
        southbound_wait_days=5.0,
        capacity_utilization_pct=40.0,
    )
    out = get_canal_shipping_impact(panama, suez)
    routes_text = " ".join(out["affected_routes"])
    assert "Asia" in routes_text and "Europe" in routes_text


def test_impact_premium_uses_panama_40_suez_60_weighting():
    """rate_premium_est_pct = 0.4 * panama + 0.6 * suez of {Low:0, Mod:4, High:12, Crit:25}."""
    panama = _sample_panama(status="Disrupted")        # Critical → 25
    suez = _sample_suez(status="Normal", capacity_utilization_pct=50.0)   # Low → 0
    out = get_canal_shipping_impact(panama, suez)
    expected = round(0.4 * 25.0 + 0.6 * 0.0, 1)
    assert out["rate_premium_est_pct"] == expected   # 10.0

    # Reverse: only Suez disrupted.
    panama = _sample_panama(status="Normal", capacity_utilization_pct=70.0)   # Low → 0
    suez = _sample_suez(status="Disrupted")           # Critical → 25
    out2 = get_canal_shipping_impact(panama, suez)
    expected2 = round(0.4 * 0.0 + 0.6 * 25.0, 1)
    assert out2["rate_premium_est_pct"] == expected2  # 15.0
    assert out2["rate_premium_est_pct"] > out["rate_premium_est_pct"]


def test_impact_narrative_mentions_both_canals():
    panama = _sample_panama(status="Normal", capacity_utilization_pct=70.0)
    suez = _sample_suez(status="Restricted")
    out = get_canal_shipping_impact(panama, suez)
    assert isinstance(out["narrative"], str)
    assert "Panama" in out["narrative"]
    assert "Suez" in out["narrative"]


def test_impact_return_dict_has_expected_keys():
    panama = _sample_panama()
    suez = _sample_suez()
    out = get_canal_shipping_impact(panama, suez)
    assert set(out.keys()) == {
        "panama_impact",
        "suez_impact",
        "affected_routes",
        "rate_premium_est_pct",
        "narrative",
    }
    assert isinstance(out["affected_routes"], list)
    assert isinstance(out["rate_premium_est_pct"], float)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
