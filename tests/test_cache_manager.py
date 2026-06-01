"""Tests for data.cache_manager.CacheManager.

CacheManager is a TTL-bounded on-disk Parquet cache. Each test gets a fresh
cache directory under tmp_path; TTL semantics are exercised by patching
cache_manager.time.time (no real sleeps).

Covers:
  - __init__: cache directory is created on instantiation
  - _slugify: lowercasing, whitespace/punct collapsing, 120-char trunc,
    non-string input
  - _path: subdir layout cache_dir/{source}/{slug}.parquet
  - _is_fresh: missing file → False; ttl_hours <= 0 → always True;
    fresh under TTL → True; stale over TTL → False
  - get_or_fetch:
      * cache miss writes parquet, returns df, fetch called once
      * cache hit within TTL returns cached df without calling fetch
      * stale cache triggers refetch
      * empty / None fetch result is not cached and returns DataFrame
      * dtype preservation (timezone-aware DatetimeIndex round-trip)
  - invalidate: removes a specific entry; no-op when missing
  - invalidate_source: removes every parquet under a source dir;
    returns count; missing source dir returns 0
  - invalidate_all: removes every parquet across all sources; returns count
  - cache_age_hours: None when missing; positive float when present
  - is_cached: True only when a fresh entry exists; default ttl=0 → True if file exists
  - list_entries: empty when dir missing; per-source filtering; full-tree mode;
    sorted by age_hours ascending; includes source/key/age_hours/size_kb keys
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from data import cache_manager as cm
from data.cache_manager import CacheManager


# ─── Helpers ────────────────────────────────────────────────────────────────

def _df(n: int = 3) -> pd.DataFrame:
    """Small DataFrame with a tz-aware DatetimeIndex (parquet-safe)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"value": list(range(n))}, index=idx)


def _make_clock(start: float):
    """Return a mutable clock: state['t'] is the current 'time.time()'."""
    state = {"t": float(start)}

    def _now() -> float:
        return state["t"]

    return state, _now


# ─── __init__ ──────────────────────────────────────────────────────────────

def test_init_creates_cache_dir(tmp_path) -> None:
    target = tmp_path / "nested" / "cache"
    assert not target.exists()
    cache = CacheManager(target)
    assert target.exists()
    assert cache.cache_dir == target


def test_init_accepts_str_and_path(tmp_path) -> None:
    c1 = CacheManager(str(tmp_path / "a"))
    c2 = CacheManager(tmp_path / "b")
    assert isinstance(c1.cache_dir, Path)
    assert isinstance(c2.cache_dir, Path)


def test_init_idempotent_on_existing_dir(tmp_path) -> None:
    """exist_ok=True — no error when dir already exists."""
    CacheManager(tmp_path)
    CacheManager(tmp_path)  # second call must not raise


# ─── _slugify ──────────────────────────────────────────────────────────────

def test_slugify_lowercases_and_strips() -> None:
    assert CacheManager._slugify("  Hello World  ") == "hello_world"


def test_slugify_collapses_whitespace_underscores_hyphens() -> None:
    """Spaces, underscores, and hyphens (one or more) all collapse to '_'."""
    assert CacheManager._slugify("a   b") == "a_b"
    assert CacheManager._slugify("a___b") == "a_b"
    assert CacheManager._slugify("a---b") == "a_b"
    assert CacheManager._slugify("a -_- b") == "a_b"


def test_slugify_drops_punctuation() -> None:
    """[^\\w\\s-] removed; word chars and hyphens kept."""
    assert CacheManager._slugify("foo!@#bar?") == "foobar"
    assert CacheManager._slugify("file.name/path") == "filenamepath"


def test_slugify_truncates_to_120_chars() -> None:
    out = CacheManager._slugify("a" * 500)
    assert len(out) == 120


def test_slugify_coerces_non_string_input() -> None:
    """str() conversion lets ints / other types pass through."""
    assert CacheManager._slugify(12345) == "12345"


def test_slugify_preserves_alnum_and_underscore() -> None:
    assert CacheManager._slugify("comtrade_CNSHA_8471_2024") == "comtrade_cnsha_8471_2024"


# ─── _path ─────────────────────────────────────────────────────────────────

def test_path_layout(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    p = cache._path("comtrade", "Foo Bar")
    assert p == tmp_path / "comtrade" / "foo_bar.parquet"


def test_path_uses_slugified_key(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    p = cache._path("fred", "BSXRLM!?")
    assert p.name == "bsxrlm.parquet"


# ─── _is_fresh ─────────────────────────────────────────────────────────────

def test_is_fresh_false_when_missing(tmp_path) -> None:
    assert CacheManager._is_fresh(tmp_path / "nope.parquet", ttl_hours=1.0) is False


def test_is_fresh_true_when_ttl_zero_and_file_exists(tmp_path) -> None:
    """ttl<=0 means 'cache forever'."""
    f = tmp_path / "f.parquet"
    f.write_bytes(b"x")
    assert CacheManager._is_fresh(f, ttl_hours=0) is True
    assert CacheManager._is_fresh(f, ttl_hours=-5) is True


def test_is_fresh_true_when_within_ttl(tmp_path, monkeypatch) -> None:
    f = tmp_path / "f.parquet"
    f.write_bytes(b"x")
    mtime = f.stat().st_mtime
    state, fake_now = _make_clock(mtime + 1800)  # 0.5h later
    monkeypatch.setattr(cm.time, "time", fake_now)
    assert CacheManager._is_fresh(f, ttl_hours=1.0) is True


def test_is_fresh_false_when_past_ttl(tmp_path, monkeypatch) -> None:
    f = tmp_path / "f.parquet"
    f.write_bytes(b"x")
    mtime = f.stat().st_mtime
    state, fake_now = _make_clock(mtime + 7200)  # 2h later
    monkeypatch.setattr(cm.time, "time", fake_now)
    assert CacheManager._is_fresh(f, ttl_hours=1.0) is False


# ─── get_or_fetch ──────────────────────────────────────────────────────────

def test_get_or_fetch_miss_calls_fetch_and_writes_parquet(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    calls = {"n": 0}

    def fetch() -> pd.DataFrame:
        calls["n"] += 1
        return _df()

    out = cache.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    assert calls["n"] == 1
    assert len(out) == 3
    assert (tmp_path / "src" / "k.parquet").exists()


def test_get_or_fetch_hit_returns_cached_without_calling_fetch(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    calls = {"n": 0}

    def fetch() -> pd.DataFrame:
        calls["n"] += 1
        return _df()

    cache.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    out = cache.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    assert calls["n"] == 1  # second call hit cache
    assert len(out) == 3


def test_get_or_fetch_stale_triggers_refetch(tmp_path, monkeypatch) -> None:
    cache = CacheManager(tmp_path)
    calls = {"n": 0}

    def fetch() -> pd.DataFrame:
        calls["n"] += 1
        return _df()

    cache.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    # Advance the clock past TTL.
    real_now = time.time()
    state, fake_now = _make_clock(real_now + 7200)  # +2h
    monkeypatch.setattr(cm.time, "time", fake_now)
    cache.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    assert calls["n"] == 2


def test_get_or_fetch_empty_result_not_cached_and_returns_df(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    out = cache.get_or_fetch("k", lambda: pd.DataFrame(), ttl_hours=1.0, source="src")
    assert isinstance(out, pd.DataFrame)
    assert out.empty
    assert not (tmp_path / "src" / "k.parquet").exists()


def test_get_or_fetch_none_result_returns_empty_df(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    out = cache.get_or_fetch("k", lambda: None, ttl_hours=1.0, source="src")
    assert isinstance(out, pd.DataFrame)
    assert out.empty
    assert not (tmp_path / "src" / "k.parquet").exists()


def test_get_or_fetch_preserves_tz_aware_datetimeindex(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    df = _df(5)
    cache.get_or_fetch("k", lambda: df, ttl_hours=1.0, source="src")
    out = cache.get_or_fetch("k", lambda: df, ttl_hours=1.0, source="src")
    assert isinstance(out.index, pd.DatetimeIndex)
    assert out.index.tz is not None
    assert str(out.index.tz) == "UTC"


def test_get_or_fetch_default_source_is_misc(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k", lambda: _df(), ttl_hours=1.0)
    assert (tmp_path / "misc" / "k.parquet").exists()


def test_get_or_fetch_creates_source_subdir(tmp_path) -> None:
    """source dir is created lazily by get_or_fetch, not __init__."""
    cache = CacheManager(tmp_path)
    assert not (tmp_path / "fred").exists()
    cache.get_or_fetch("k", lambda: _df(), ttl_hours=1.0, source="fred")
    assert (tmp_path / "fred").is_dir()


# ─── invalidate ────────────────────────────────────────────────────────────

def test_invalidate_removes_existing_entry(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k", lambda: _df(), ttl_hours=1.0, source="src")
    path = tmp_path / "src" / "k.parquet"
    assert path.exists()
    cache.invalidate("k", source="src")
    assert not path.exists()


def test_invalidate_missing_entry_is_noop(tmp_path) -> None:
    """No error when target does not exist."""
    cache = CacheManager(tmp_path)
    cache.invalidate("ghost", source="src")  # must not raise


# ─── invalidate_source ─────────────────────────────────────────────────────

def test_invalidate_source_removes_all_in_dir_and_returns_count(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    for k in ("a", "b", "c"):
        cache.get_or_fetch(k, lambda: _df(), ttl_hours=1.0, source="src")
    n = cache.invalidate_source("src")
    assert n == 3
    assert list((tmp_path / "src").glob("*.parquet")) == []


def test_invalidate_source_returns_zero_when_dir_missing(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    assert cache.invalidate_source("does_not_exist") == 0


def test_invalidate_source_leaves_other_sources_alone(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k1", lambda: _df(), ttl_hours=1.0, source="a")
    cache.get_or_fetch("k2", lambda: _df(), ttl_hours=1.0, source="b")
    cache.invalidate_source("a")
    assert not (tmp_path / "a" / "k1.parquet").exists()
    assert (tmp_path / "b" / "k2.parquet").exists()


# ─── invalidate_all ────────────────────────────────────────────────────────

def test_invalidate_all_removes_every_parquet(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k1", lambda: _df(), ttl_hours=1.0, source="a")
    cache.get_or_fetch("k2", lambda: _df(), ttl_hours=1.0, source="b")
    n = cache.invalidate_all()
    assert n == 2
    assert list(tmp_path.rglob("*.parquet")) == []


def test_invalidate_all_returns_zero_when_empty(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    assert cache.invalidate_all() == 0


# ─── cache_age_hours ───────────────────────────────────────────────────────

def test_cache_age_hours_returns_none_when_missing(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    assert cache.cache_age_hours("ghost", source="src") is None


def test_cache_age_hours_returns_positive_float(tmp_path, monkeypatch) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k", lambda: _df(), ttl_hours=1.0, source="src")
    path = tmp_path / "src" / "k.parquet"
    mtime = path.stat().st_mtime
    # Advance virtual clock to 2 hours after mtime.
    state, fake_now = _make_clock(mtime + 7200)
    monkeypatch.setattr(cm.time, "time", fake_now)
    age = cache.cache_age_hours("k", source="src")
    assert age is not None
    assert age == pytest.approx(2.0, abs=1e-6)


# ─── is_cached ─────────────────────────────────────────────────────────────

def test_is_cached_false_when_missing(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    assert cache.is_cached("ghost", source="src") is False


def test_is_cached_true_with_default_ttl_zero(tmp_path) -> None:
    """Default ttl_hours=0 → 'cache forever' — any existing file is fresh."""
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k", lambda: _df(), ttl_hours=1.0, source="src")
    assert cache.is_cached("k", source="src") is True


def test_is_cached_false_when_stale(tmp_path, monkeypatch) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k", lambda: _df(), ttl_hours=1.0, source="src")
    path = tmp_path / "src" / "k.parquet"
    mtime = path.stat().st_mtime
    state, fake_now = _make_clock(mtime + 7200)
    monkeypatch.setattr(cm.time, "time", fake_now)
    assert cache.is_cached("k", source="src", ttl_hours=1.0) is False


# ─── list_entries ──────────────────────────────────────────────────────────

def test_list_entries_empty_when_dir_missing(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    # Source dir does not exist yet.
    assert cache.list_entries(source="nope") == []


def test_list_entries_per_source(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("a", lambda: _df(), ttl_hours=1.0, source="src")
    cache.get_or_fetch("b", lambda: _df(), ttl_hours=1.0, source="src")
    cache.get_or_fetch("c", lambda: _df(), ttl_hours=1.0, source="other")
    out = cache.list_entries(source="src")
    keys = {e["key"] for e in out}
    assert keys == {"a", "b"}


def test_list_entries_full_tree_when_source_none(tmp_path) -> None:
    """source=None walks every subdir recursively."""
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("a", lambda: _df(), ttl_hours=1.0, source="x")
    cache.get_or_fetch("b", lambda: _df(), ttl_hours=1.0, source="y")
    out = cache.list_entries()
    sources = {e["source"] for e in out}
    assert sources == {"x", "y"}
    assert len(out) == 2


def test_list_entries_has_expected_keys(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k", lambda: _df(), ttl_hours=1.0, source="src")
    out = cache.list_entries(source="src")
    assert len(out) == 1
    entry = out[0]
    assert set(entry.keys()) == {"source", "key", "age_hours", "size_kb"}
    assert entry["source"] == "src"
    assert entry["key"] == "k"
    assert isinstance(entry["age_hours"], float)
    assert entry["size_kb"] > 0


def test_list_entries_sorted_by_age_ascending(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    # Three files with distinct mtimes.
    for k in ("a", "b", "c"):
        cache.get_or_fetch(k, lambda: _df(), ttl_hours=1.0, source="src")
    # Manually backdate to fix ordering deterministically.
    base = time.time()
    (tmp_path / "src" / "a.parquet").touch()
    import os
    os.utime(tmp_path / "src" / "a.parquet", (base - 300, base - 300))   # oldest
    os.utime(tmp_path / "src" / "b.parquet", (base - 100, base - 100))
    os.utime(tmp_path / "src" / "c.parquet", (base - 10, base - 10))     # newest
    out = cache.list_entries(source="src")
    keys_in_order = [e["key"] for e in out]
    # Sorted by age_hours ascending → newest first.
    assert keys_in_order == ["c", "b", "a"]


# ─── Integration: round-trip across instances ──────────────────────────────

def test_round_trip_across_instances(tmp_path) -> None:
    """Two CacheManager instances pointing at the same dir share cache."""
    c1 = CacheManager(tmp_path)
    c1.get_or_fetch("k", lambda: _df(), ttl_hours=1.0, source="src")

    calls = {"n": 0}

    def fetch() -> pd.DataFrame:
        calls["n"] += 1
        return _df()

    c2 = CacheManager(tmp_path)
    out = c2.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    assert calls["n"] == 0  # served from cache
    assert len(out) == 3
