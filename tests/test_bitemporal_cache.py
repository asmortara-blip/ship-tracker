"""Tests for the R110 bitemporal point-in-time cache layer in CacheManager.

The vintage store lets a backtest read data AS IT WAS KNOWN on a past date —
the latest dated vintage whose fetch_date <= as_of — instead of today's revised
values (FRED revisions, World Bank restatements, …). These tests pin the
defining point-in-time property: an as-of read returns the OLD value, never a
newer revision, and NEVER falls back to today's live data (no look-ahead).

Everything runs against a tmp cache dir; ``cache_manager.CacheManager._today_iso``
is monkeypatched per test so vintages land on controlled, distinct fetch_dates
without waiting a real calendar day.

Covers:
  - two fetches on different fetch_dates retain BOTH vintages
  - load_as_of(d) returns the vintage as-known-then (OLD value, not a revision)
  - an as_of_date before any vintage → None / empty (no live fallback)
  - retention prunes beyond N (and logs)
  - get_or_fetch with NO as_of_date is the SAME behaviour as before (back-compat)
  - the R003 fetch-ledger stamping still fires on the legacy path
"""
from __future__ import annotations

import pandas as pd
import pytest

from data import cache_manager as cm
from data.cache_manager import CacheManager


# ─── Helpers ────────────────────────────────────────────────────────────────

def _df(value: int, n: int = 3) -> pd.DataFrame:
    """A small frame whose ``value`` column carries a known 'revision' marker."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"value": [value] * n}, index=idx)


def _pin_today(monkeypatch, iso_date: str) -> None:
    """Force the manager's knowledge-date (vintage stamp) to a fixed day."""
    monkeypatch.setattr(CacheManager, "_today_iso", staticmethod(lambda: iso_date))


# ─── Two vintages on different fetch_dates are both retained ────────────────

def test_two_vintages_on_different_dates_both_retained(tmp_path, monkeypatch) -> None:
    cache = CacheManager(tmp_path)

    _pin_today(monkeypatch, "2024-01-10")
    cache.get_or_fetch("series_x", lambda: _df(100), ttl_hours=0, source="fred")

    _pin_today(monkeypatch, "2024-02-10")
    # Force a refetch (ttl<=0 would serve the legacy cache); invalidate the
    # TTL entry so the live path runs again and writes a second vintage.
    cache.invalidate("series_x", source="fred")
    cache.get_or_fetch("series_x", lambda: _df(200), ttl_hours=0, source="fred")

    vintages = cache.list_vintages("series_x", source="fred")
    assert vintages == ["2024-01-10", "2024-02-10"]


# ─── The defining point-in-time property ────────────────────────────────────

def test_load_as_of_returns_value_as_known_then_not_the_revision(tmp_path, monkeypatch) -> None:
    """As-of a date BETWEEN the two fetches, you see the OLD value (100), not 200."""
    cache = CacheManager(tmp_path)

    _pin_today(monkeypatch, "2024-01-10")
    cache.get_or_fetch("gdp", lambda: _df(100), ttl_hours=0, source="worldbank")

    _pin_today(monkeypatch, "2024-03-01")
    cache.invalidate("gdp", source="worldbank")
    cache.get_or_fetch("gdp", lambda: _df(200), ttl_hours=0, source="worldbank")

    # Knowledge-date in Feb: only the Jan vintage existed → the OLD value.
    as_of_feb = cache.load_as_of("gdp", "2024-02-15", source="worldbank")
    assert as_of_feb is not None
    assert int(as_of_feb["value"].iloc[0]) == 100

    # Knowledge-date in April: now the revised value is visible.
    as_of_apr = cache.load_as_of("gdp", "2024-04-01", source="worldbank")
    assert as_of_apr is not None
    assert int(as_of_apr["value"].iloc[0]) == 200

    # Exactly on the second fetch_date → that day's vintage (revision visible).
    as_of_exact = cache.load_as_of("gdp", "2024-03-01", source="worldbank")
    assert int(as_of_exact["value"].iloc[0]) == 200


def test_get_or_fetch_as_of_serves_vintage_not_live(tmp_path, monkeypatch) -> None:
    """as_of_date on get_or_fetch bypasses the live fetch and serves the vintage."""
    cache = CacheManager(tmp_path)

    _pin_today(monkeypatch, "2024-01-10")
    cache.get_or_fetch("k", lambda: _df(100), ttl_hours=0, source="fred")

    calls = {"n": 0}

    def boom() -> pd.DataFrame:
        calls["n"] += 1
        return _df(999)  # would be a look-ahead if served

    out = cache.get_or_fetch("k", boom, ttl_hours=0, source="fred",
                             as_of_date="2024-01-20")
    assert calls["n"] == 0                       # live fetch NOT called
    assert int(out["value"].iloc[0]) == 100      # served the vintage


# ─── No look-ahead fallback before the first vintage ────────────────────────

def test_as_of_before_any_vintage_returns_empty_no_live_fallback(tmp_path, monkeypatch) -> None:
    cache = CacheManager(tmp_path)

    _pin_today(monkeypatch, "2024-06-01")
    cache.get_or_fetch("k", lambda: _df(100), ttl_hours=0, source="fred")

    # load_as_of before the first vintage → None.
    assert cache.load_as_of("k", "2024-01-01", source="fred") is None

    # get_or_fetch with that as_of → empty frame, and the fetcher is NOT called
    # (no fabrication, no fall-back to today's live data).
    calls = {"n": 0}

    def fetch() -> pd.DataFrame:
        calls["n"] += 1
        return _df(100)

    out = cache.get_or_fetch("k", fetch, ttl_hours=0, source="fred",
                             as_of_date="2024-01-01")
    assert isinstance(out, pd.DataFrame)
    assert out.empty
    assert calls["n"] == 0


def test_load_as_of_unknown_key_returns_none(tmp_path) -> None:
    cache = CacheManager(tmp_path)
    assert cache.load_as_of("never_fetched", "2030-01-01", source="fred") is None
    assert cache.list_vintages("never_fetched", source="fred") == []


# ─── Retention prunes beyond N (and logs) ───────────────────────────────────

def test_retention_prunes_oldest_beyond_n(tmp_path, monkeypatch) -> None:
    cache = CacheManager(tmp_path, vintage_retention=3)

    # Five distinct fetch_dates → only the newest 3 survive.
    for day in ("01", "02", "03", "04", "05"):
        _pin_today(monkeypatch, f"2024-01-{day}")
        cache.invalidate("k", source="fred")
        cache.get_or_fetch("k", lambda d=day: _df(int(d)), ttl_hours=0, source="fred")

    vintages = cache.list_vintages("k", source="fred")
    assert vintages == ["2024-01-03", "2024-01-04", "2024-01-05"]

    # The pruned-away early dates are gone (no look-back possible past retention).
    assert cache.load_as_of("k", "2024-01-01", source="fred") is None
    # The oldest surviving vintage is the floor.
    kept = cache.load_as_of("k", "2024-01-03", source="fred")
    assert int(kept["value"].iloc[0]) == 3


def test_retention_logs_pruned_vintage(tmp_path, monkeypatch) -> None:
    """Pruning must be loud — no silent unbounded-growth-then-drop."""
    cache = CacheManager(tmp_path, vintage_retention=1)
    logged: list[str] = []
    monkeypatch.setattr(cm.logger, "info", lambda msg, *a, **k: logged.append(str(msg)))

    _pin_today(monkeypatch, "2024-01-01")
    cache.get_or_fetch("k", lambda: _df(1), ttl_hours=0, source="fred")
    _pin_today(monkeypatch, "2024-01-02")
    cache.invalidate("k", source="fred")
    cache.get_or_fetch("k", lambda: _df(2), ttl_hours=0, source="fred")

    assert any("Pruned vintage" in m and "2024-01-01" in m for m in logged)


def test_retention_clamped_to_at_least_one(tmp_path) -> None:
    cache = CacheManager(tmp_path, vintage_retention=0)
    assert cache.vintage_retention == 1


# ─── Backward-compat: no as_of_date → byte-for-byte the legacy path ─────────

def test_get_or_fetch_no_as_of_is_legacy_behaviour(tmp_path) -> None:
    """A miss fetches+caches; a hit serves cache without re-calling fetch."""
    cache = CacheManager(tmp_path)
    calls = {"n": 0}

    def fetch() -> pd.DataFrame:
        calls["n"] += 1
        return _df(7)

    out1 = cache.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    assert calls["n"] == 1
    assert len(out1) == 3
    assert (tmp_path / "src" / "k.parquet").exists()

    out2 = cache.get_or_fetch("k", fetch, ttl_hours=1.0, source="src")
    assert calls["n"] == 1                       # hit — fetch NOT re-called
    assert int(out2["value"].iloc[0]) == 7


def test_get_or_fetch_empty_unchanged_and_no_vintage(tmp_path) -> None:
    """Empty fetch result: not cached, no vintage written, returns empty frame."""
    cache = CacheManager(tmp_path)
    out = cache.get_or_fetch("k", lambda: pd.DataFrame(), ttl_hours=1.0, source="src")
    assert isinstance(out, pd.DataFrame) and out.empty
    assert not (tmp_path / "src" / "k.parquet").exists()
    assert cache.list_vintages("k", source="src") == []


def test_legacy_positional_call_still_works(tmp_path) -> None:
    """imf_feed/oecd_feed call positionally: get_or_fetch(key, fn, ttl_hours=, source=)."""
    cache = CacheManager(tmp_path)
    out = cache.get_or_fetch("k", lambda: _df(5), ttl_hours=1.0, source="imf")
    assert int(out["value"].iloc[0]) == 5


def test_successful_fetch_writes_a_vintage_for_today(tmp_path, monkeypatch) -> None:
    """The default (no as_of) live path ALSO drops today's vintage, best-effort."""
    cache = CacheManager(tmp_path)
    _pin_today(monkeypatch, "2024-07-04")
    cache.get_or_fetch("k", lambda: _df(42), ttl_hours=1.0, source="fred")
    assert cache.list_vintages("k", source="fred") == ["2024-07-04"]


# ─── The R003 fetch-ledger stamping still fires ─────────────────────────────

def test_fetch_ledger_stamp_fires_on_legacy_path(tmp_path, monkeypatch) -> None:
    """_stamp_provenance is still invoked with 'live' on a miss, 'cache' on a hit."""
    stamps: list[tuple] = []
    monkeypatch.setattr(
        CacheManager, "_stamp_provenance",
        staticmethod(lambda source, key, kind, df: stamps.append((source, key, kind))),
    )
    cache = CacheManager(tmp_path)
    cache.get_or_fetch("k", lambda: _df(1), ttl_hours=1.0, source="fred")  # miss → live
    cache.get_or_fetch("k", lambda: _df(1), ttl_hours=1.0, source="fred")  # hit → cache

    kinds = [s[2] for s in stamps]
    assert kinds == ["live", "cache"]


def test_fetch_ledger_stamp_fires_on_as_of_read(tmp_path, monkeypatch) -> None:
    """As-of reads also stamp provenance: 'cache' when a vintage is served,
    'empty' when none is as-known-then (honest, no fabrication)."""
    cache = CacheManager(tmp_path)
    _pin_today(monkeypatch, "2024-01-10")
    cache.get_or_fetch("k", lambda: _df(1), ttl_hours=0, source="fred")

    stamps: list[tuple] = []
    monkeypatch.setattr(
        CacheManager, "_stamp_provenance",
        staticmethod(lambda source, key, kind, df: stamps.append((source, key, kind))),
    )

    cache.get_or_fetch("k", lambda: _df(9), ttl_hours=0, source="fred",
                       as_of_date="2024-02-01")          # vintage exists → cache
    cache.get_or_fetch("k", lambda: _df(9), ttl_hours=0, source="fred",
                       as_of_date="2023-12-01")          # before first → empty

    kinds = [s[2] for s in stamps]
    assert kinds == ["cache", "empty"]


# ─── The vintage store never raises from the cache path ─────────────────────

def test_vintage_write_failure_degrades_to_normal_fetch(tmp_path, monkeypatch) -> None:
    """A broken vintage write must NOT break the fetch — the legacy result stands."""
    cache = CacheManager(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(CacheManager, "_write_vintage", boom)
    out = cache.get_or_fetch("k", lambda: _df(5), ttl_hours=1.0, source="src")
    assert int(out["value"].iloc[0]) == 5
    assert (tmp_path / "src" / "k.parquet").exists()  # legacy cache still written


def test_vintage_dir_layout(tmp_path, monkeypatch) -> None:
    """cache/vintages/{source}/{slug}-{hash}/{fetch_date}.parquet."""
    cache = CacheManager(tmp_path)
    _pin_today(monkeypatch, "2024-01-15")
    cache.get_or_fetch("My Series", lambda: _df(1), ttl_hours=1.0, source="fred")

    vroot = tmp_path / "vintages" / "fred"
    assert vroot.is_dir()
    subdirs = list(vroot.iterdir())
    assert len(subdirs) == 1
    assert subdirs[0].name.startswith("my_series-")  # slug + short hash
    assert (subdirs[0] / "2024-01-15.parquet").exists()
