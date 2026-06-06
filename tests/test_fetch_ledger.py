"""Per-fetch provenance ledger (schema v33; rec R003/R097)."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def ledger_db(monkeypatch, tmp_path):
    """Isolated DB + provenance recording RE-ENABLED (conftest turns it off)."""
    from state import db as state_db
    import state.fetch_ledger as fl
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    monkeypatch.setattr(fl, "RECORDING_ENABLED", True, raising=False)
    yield
    state_db.reset_for_tests()


def test_v33_table_exists(ledger_db) -> None:
    from state.db import SCHEMA_VERSION, get_connection
    cols = [r[1] for r in get_connection().execute("PRAGMA table_info(data_fetches)")]
    assert SCHEMA_VERSION >= 33
    assert {"source", "kind", "row_count", "byte_hash", "fetched_at"} <= set(cols)


def test_record_and_read_back(ledger_db) -> None:
    from state.fetch_ledger import record_fetch, recent_fetches
    assert record_fetch("stocks", "zim_180d", "live", row_count=120, quality="GOOD")
    assert record_fetch("fred", "BDI", "cache", row_count=300)
    rows = recent_fetches()
    assert len(rows) == 2
    by_source = {r["source"]: r for r in rows}
    assert by_source["stocks"]["kind"] == "live" and by_source["stocks"]["row_count"] == 120
    assert by_source["fred"]["kind"] == "cache"


def test_recent_filters_by_source(ledger_db) -> None:
    from state.fetch_ledger import record_fetch, recent_fetches
    record_fetch("stocks", "a", "live")
    record_fetch("fred", "b", "live")
    assert {r["source"] for r in recent_fetches(source="fred")} == {"fred"}


def test_realness_summary_rates(ledger_db) -> None:
    from state.fetch_ledger import fetch_realness_summary, record_fetch
    record_fetch("stocks", "a", "live")
    record_fetch("stocks", "b", "cache")
    record_fetch("ais", "c", "synthetic")
    record_fetch("ais", "d", "synthetic")
    s = fetch_realness_summary()
    assert s["n"] == 4
    assert s["realness_rate"] == pytest.approx(0.5)    # (1 live + 1 cache)/4
    assert s["freshness_rate"] == pytest.approx(0.25)  # 1 live /4
    assert s["synthetic_rate"] == pytest.approx(0.5)   # 2 synth /4
    assert s["by_source"]["ais"]["synthetic_rate"] == 1.0
    assert s["by_source"]["stocks"]["realness_rate"] == 1.0


def test_empty_summary_stable(ledger_db) -> None:
    from state.fetch_ledger import fetch_realness_summary
    s = fetch_realness_summary()
    assert s["n"] == 0 and s["by_source"] == {} and s["realness_rate"] == 0.0


def test_unknown_kind_coerced(ledger_db) -> None:
    from state.fetch_ledger import record_fetch, recent_fetches
    record_fetch("x", "k", "synthetic_baseline")     # legacy label
    assert recent_fetches()[0]["kind"] == "synthetic"


def test_disabled_recording_is_noop(ledger_db, monkeypatch) -> None:
    import state.fetch_ledger as fl
    monkeypatch.setattr(fl, "RECORDING_ENABLED", False)
    assert fl.record_fetch("x", "k", "live") is False
    assert fl.recent_fetches() == []


def test_record_never_raises_on_garbage(ledger_db) -> None:
    from state.fetch_ledger import record_fetch
    assert record_fetch(None, None, None) in (True, False)   # must not raise


def test_cheap_hash_distinguishes_frames(ledger_db) -> None:
    from state.fetch_ledger import cheap_content_hash
    a = cheap_content_hash(pd.DataFrame({"x": [1, 2, 3]}))
    b = cheap_content_hash(pd.DataFrame({"x": [1, 2, 4]}))
    assert a and b and a != b
    assert cheap_content_hash(pd.DataFrame()) == ""
    assert cheap_content_hash(None) == ""


# ── cache_manager choke-point wiring ─────────────────────────────────────────

def test_cache_manager_stamps_live_then_cache(ledger_db, tmp_path) -> None:
    from data.cache_manager import CacheManager
    from state.fetch_ledger import recent_fetches
    cache = CacheManager(cache_dir=tmp_path / "cache")
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    # miss -> live
    cache.get_or_fetch("k", lambda: df, ttl_hours=1.0, source="stocks")
    # hit -> cache (fetch_fn must not even be called)
    cache.get_or_fetch("k", lambda: (_ for _ in ()).throw(AssertionError),
                       ttl_hours=1.0, source="stocks")
    kinds = [r["kind"] for r in recent_fetches(source="stocks")]
    assert "live" in kinds and "cache" in kinds


def test_cache_manager_recording_off_by_default_in_tests(tmp_path) -> None:
    # WITHOUT the ledger_db fixture, the conftest autouse keeps recording OFF,
    # so a fetch writes NO provenance row (no real-DB pollution).
    from data.cache_manager import CacheManager
    import state.fetch_ledger as fl
    assert fl.RECORDING_ENABLED is False           # conftest disabled it
    cache = CacheManager(cache_dir=tmp_path / "cache")
    cache.get_or_fetch("k", lambda: pd.DataFrame({"a": [1]}),
                       ttl_hours=1.0, source="src")   # must not raise / write
