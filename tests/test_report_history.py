"""Tests for utils.report_history — report persistence layer.

The module stores investor reports as HTML files under cache/reports/ with a
JSON sidecar index (report_index.json). Every public function is wrapped in a
try/except returning a sensible default — these tests verify both the happy
paths and the defensive defaults.

Covers:
  - ReportMeta dataclass shape
  - Module constants (REPORT_DIR, MAX_REPORTS, _INDEX_FILE)
  - _attr: attribute access, dict access, None → default, missing → default
  - _safe_float: numeric strings, ints, None/garbage → default
  - _safe_int: numeric strings, floats, None/garbage → default
  - _load_index: missing file → []; corrupt JSON → []; malformed entries skipped
  - _save_index: writes parseable JSON; creates REPORT_DIR if absent
  - _prune_old_reports: ≤ MAX_REPORTS no-op; trims to newest N; deletes
    pruned files on disk; missing files don't raise
  - save_report: writes HTML, returns populated ReportMeta, appends to index;
    extracts metadata from object attrs OR dict; safe defaults when fields
    are missing; pruning triggers when len > MAX_REPORTS; returns None on
    write failure (REPORT_DIR unwritable)
  - list_reports: newest-first sort; filters out entries whose file is gone;
    cleans index when files missing; returns [] on error
  - load_report_html: returns HTML for a known id; None for unknown id; None
    when file deleted underneath; round-trips unicode
  - delete_report: removes index entry and file; returns True on success,
    False for unknown id; tolerates already-deleted files
  - get_report_stats: empty → zeroed dict; populated → totals, oldest/newest,
    average sentiment, label distribution
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import report_history as rh
from utils.report_history import (
    MAX_REPORTS,
    ReportMeta,
    _attr,
    _load_index,
    _prune_old_reports,
    _safe_float,
    _safe_int,
    _save_index,
    delete_report,
    get_report_stats,
    list_reports,
    load_report_html,
    save_report,
)


# ─── Fixture: isolate REPORT_DIR + _INDEX_FILE per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_report_dir(monkeypatch, tmp_path):
    """Redirect the module-level paths so no test touches real cache/reports."""
    tmp_dir = tmp_path / "reports"
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_dir)
    monkeypatch.setattr(rh, "_INDEX_FILE", tmp_dir / "report_index.json")


# ─── Stand-in report object ────────────────────────────────────────────────

@dataclass
class _FakeReport:
    date: str = "January 15, 2026"
    market_sentiment: str = "BULLISH"
    sentiment_score: float = 0.65
    risk_level: str = "MODERATE"
    signal_count: int = 7
    data_quality: str = "FULL"


def _meta(
    report_id: str = "rid-1",
    generated_at: str | None = None,
    file_path: str = "/tmp/fake.html",
    sentiment_label: str = "BULLISH",
    sentiment_score: float = 0.5,
    file_size_kb: float = 12.5,
) -> ReportMeta:
    """Helper to fabricate a ReportMeta for index-level tests."""
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()
    return ReportMeta(
        report_id=report_id,
        generated_at=generated_at,
        report_date="Test Date",
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
        risk_level="MODERATE",
        signal_count=3,
        data_quality="FULL",
        file_path=file_path,
        file_size_kb=file_size_kb,
    )


# ─── Dataclass + constants ──────────────────────────────────────────────────

def test_report_meta_shape() -> None:
    m = _meta()
    assert m.report_id == "rid-1"
    assert m.sentiment_label == "BULLISH"
    assert m.signal_count == 3


def test_max_reports_is_positive_int() -> None:
    assert isinstance(MAX_REPORTS, int)
    assert MAX_REPORTS > 0


# ─── _attr ──────────────────────────────────────────────────────────────────

def test_attr_returns_attribute_when_present() -> None:
    assert _attr(_FakeReport(market_sentiment="BEARISH"),
                 "market_sentiment", "NEUTRAL") == "BEARISH"


def test_attr_returns_default_when_missing() -> None:
    assert _attr(_FakeReport(), "no_such_field", "fallback") == "fallback"


def test_attr_returns_default_when_attr_is_none() -> None:
    @dataclass
    class _N:
        x: object = None
    assert _attr(_N(), "x", "fallback") == "fallback"


def test_attr_supports_dict_access() -> None:
    assert _attr({"date": "March 1"}, "date", "x") == "March 1"


def test_attr_dict_falls_back_to_default_when_key_missing() -> None:
    assert _attr({"other": 1}, "date", "default") == "default"


# ─── _safe_float / _safe_int ───────────────────────────────────────────────

def test_safe_float_handles_strings_ints_floats() -> None:
    assert _safe_float("1.5") == 1.5
    assert _safe_float(2) == 2.0
    assert _safe_float(3.25) == 3.25


def test_safe_float_returns_default_for_garbage() -> None:
    assert _safe_float(None) == 0.0
    assert _safe_float("not a number") == 0.0
    assert _safe_float(None, default=-1.0) == -1.0


def test_safe_int_handles_strings_floats() -> None:
    assert _safe_int("4") == 4
    assert _safe_int(7.9) == 7  # int() truncates


def test_safe_int_returns_default_for_garbage() -> None:
    assert _safe_int(None) == 0
    assert _safe_int("nan") == 0
    assert _safe_int(object(), default=99) == 99


# ─── _load_index / _save_index ─────────────────────────────────────────────

def test_load_index_missing_file_returns_empty() -> None:
    """Fresh fixture: _INDEX_FILE does not exist yet."""
    assert _load_index() == []


def test_save_index_creates_dir_and_writes_json() -> None:
    m = _meta()
    _save_index([m])
    assert rh._INDEX_FILE.exists()
    parsed = json.loads(rh._INDEX_FILE.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    assert parsed[0]["report_id"] == "rid-1"


def test_save_and_load_index_round_trip() -> None:
    a = _meta(report_id="a", sentiment_label="BULLISH")
    b = _meta(report_id="b", sentiment_label="BEARISH")
    _save_index([a, b])
    loaded = _load_index()
    assert len(loaded) == 2
    assert {m.report_id for m in loaded} == {"a", "b"}


def test_load_index_corrupt_json_returns_empty() -> None:
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rh._INDEX_FILE.write_text("not valid json {{", encoding="utf-8")
    assert _load_index() == []


def test_load_index_skips_malformed_entries() -> None:
    """Index containing both a good and a malformed record yields only the good one."""
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        {  # good
            "report_id": "ok",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_date": "x",
            "sentiment_label": "BULLISH",
            "sentiment_score": 0.5,
            "risk_level": "LOW",
            "signal_count": 1,
            "data_quality": "FULL",
            "file_path": "/tmp/x.html",
            "file_size_kb": 1.0,
        },
        {"missing": "everything"},  # malformed → skipped
    ]
    rh._INDEX_FILE.write_text(json.dumps(payload), encoding="utf-8")
    loaded = _load_index()
    assert len(loaded) == 1
    assert loaded[0].report_id == "ok"


# ─── _prune_old_reports ─────────────────────────────────────────────────────

def test_prune_noop_when_at_or_below_cap(monkeypatch) -> None:
    monkeypatch.setattr(rh, "MAX_REPORTS", 5)
    metas = [_meta(report_id=f"r{i}") for i in range(3)]
    out = _prune_old_reports(metas)
    assert len(out) == 3


def test_prune_keeps_only_newest_n(monkeypatch) -> None:
    monkeypatch.setattr(rh, "MAX_REPORTS", 2)
    base = datetime.now(timezone.utc)
    metas = [
        _meta(report_id=f"r{i}",
              generated_at=(base + timedelta(seconds=i)).isoformat())
        for i in range(5)
    ]
    out = _prune_old_reports(metas)
    assert len(out) == 2
    # Newest two are r3 and r4 (largest timestamps)
    kept = {m.report_id for m in out}
    assert kept == {"r3", "r4"}


def test_prune_deletes_files_for_pruned_entries(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rh, "MAX_REPORTS", 1)
    base = datetime.now(timezone.utc)
    f_old = tmp_path / "old.html"
    f_new = tmp_path / "new.html"
    f_old.write_text("old", encoding="utf-8")
    f_new.write_text("new", encoding="utf-8")

    metas = [
        _meta(report_id="old", file_path=str(f_old),
              generated_at=base.isoformat()),
        _meta(report_id="new", file_path=str(f_new),
              generated_at=(base + timedelta(seconds=10)).isoformat()),
    ]
    _prune_old_reports(metas)
    assert not f_old.exists(), "pruned file should be deleted"
    assert f_new.exists(), "kept file should remain"


def test_prune_tolerates_missing_files(monkeypatch) -> None:
    """A pruned entry pointing at a nonexistent file must not raise."""
    monkeypatch.setattr(rh, "MAX_REPORTS", 1)
    base = datetime.now(timezone.utc)
    metas = [
        _meta(report_id="ghost", file_path="/nonexistent/path/x.html",
              generated_at=base.isoformat()),
        _meta(report_id="kept", file_path="/nonexistent/path/y.html",
              generated_at=(base + timedelta(seconds=5)).isoformat()),
    ]
    out = _prune_old_reports(metas)  # should not raise
    assert len(out) == 1


# ─── save_report ────────────────────────────────────────────────────────────

def test_save_report_writes_file_and_returns_meta() -> None:
    meta = save_report("<html>hi</html>", _FakeReport())
    assert meta is not None
    assert meta.sentiment_label == "BULLISH"
    assert meta.sentiment_score == 0.65
    assert meta.signal_count == 7
    assert meta.data_quality == "FULL"
    assert Path(meta.file_path).exists()
    assert Path(meta.file_path).read_text(encoding="utf-8") == "<html>hi</html>"


def test_save_report_appends_to_index() -> None:
    save_report("<a>", _FakeReport())
    save_report("<b>", _FakeReport())
    loaded = _load_index()
    assert len(loaded) == 2


def test_save_report_uses_safe_defaults_for_bare_object() -> None:
    """An object lacking all expected attrs → meta still populates."""
    class _Empty:
        pass
    meta = save_report("<html>", _Empty())
    assert meta is not None
    assert meta.sentiment_label == "NEUTRAL"
    assert meta.risk_level == "MODERATE"
    assert meta.signal_count == 0
    assert meta.data_quality == "PARTIAL"
    assert meta.sentiment_score == 0.0


def test_save_report_accepts_dict_input() -> None:
    """_attr supports dict access, so save_report must work with a dict."""
    meta = save_report("<html>", {
        "market_sentiment": "BEARISH",
        "sentiment_score": -0.3,
        "risk_level": "HIGH",
        "signal_count": 4,
        "data_quality": "PARTIAL",
        "date": "March 5, 2026",
    })
    assert meta is not None
    assert meta.sentiment_label == "BEARISH"
    assert meta.sentiment_score == -0.3
    assert meta.signal_count == 4


def test_save_report_records_file_size_kb() -> None:
    big = "x" * 2048  # ~2 KB
    meta = save_report(big, _FakeReport())
    assert meta is not None
    assert meta.file_size_kb >= 1.5


def test_save_report_returns_none_when_dir_unwritable(monkeypatch) -> None:
    """If REPORT_DIR cannot be created, save_report swallows and returns None."""
    bad = Path("/proc/should_not_exist/cannot_make")
    monkeypatch.setattr(rh, "REPORT_DIR", bad)
    monkeypatch.setattr(rh, "_INDEX_FILE", bad / "report_index.json")
    assert save_report("<html>", _FakeReport()) is None


def test_save_report_triggers_pruning(monkeypatch) -> None:
    """When more than MAX_REPORTS exist, the index trims down."""
    monkeypatch.setattr(rh, "MAX_REPORTS", 2)
    save_report("<a>", _FakeReport())
    save_report("<b>", _FakeReport())
    save_report("<c>", _FakeReport())
    loaded = _load_index()
    assert len(loaded) == 2


# ─── list_reports ──────────────────────────────────────────────────────────

def test_list_reports_empty_when_no_index() -> None:
    assert list_reports() == []


def test_list_reports_sorts_newest_first() -> None:
    base = datetime.now(timezone.utc)
    # Save three reports, then rewrite their generated_at to fixed values
    m1 = save_report("<1>", _FakeReport())
    m2 = save_report("<2>", _FakeReport())
    m3 = save_report("<3>", _FakeReport())
    assert m1 and m2 and m3
    forged = [
        _meta(report_id=m1.report_id, file_path=m1.file_path,
              generated_at=base.isoformat()),
        _meta(report_id=m2.report_id, file_path=m2.file_path,
              generated_at=(base + timedelta(seconds=5)).isoformat()),
        _meta(report_id=m3.report_id, file_path=m3.file_path,
              generated_at=(base + timedelta(seconds=10)).isoformat()),
    ]
    _save_index(forged)
    out = list_reports()
    assert [m.report_id for m in out] == [m3.report_id, m2.report_id, m1.report_id]


def test_list_reports_filters_out_missing_files(tmp_path) -> None:
    """If an HTML file is deleted out from under the index, the entry is
    removed and the cleaned index is persisted."""
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    real = rh.REPORT_DIR / "real.html"
    real.write_text("<html>", encoding="utf-8")
    metas = [
        _meta(report_id="real", file_path=str(real)),
        _meta(report_id="ghost", file_path=str(tmp_path / "gone.html")),
    ]
    _save_index(metas)

    out = list_reports()
    assert {m.report_id for m in out} == {"real"}
    # Persisted cleanup
    on_disk = _load_index()
    assert {m.report_id for m in on_disk} == {"real"}


# ─── load_report_html ──────────────────────────────────────────────────────

def test_load_report_html_returns_html_for_known_id() -> None:
    meta = save_report("<html>hello</html>", _FakeReport())
    assert meta is not None
    html = load_report_html(meta.report_id)
    assert html == "<html>hello</html>"


def test_load_report_html_returns_none_for_unknown_id() -> None:
    save_report("<html>", _FakeReport())
    assert load_report_html("not-a-real-id") is None


def test_load_report_html_returns_none_when_file_deleted() -> None:
    meta = save_report("<html>", _FakeReport())
    assert meta is not None
    Path(meta.file_path).unlink()
    assert load_report_html(meta.report_id) is None


def test_load_report_html_round_trips_unicode() -> None:
    body = "<html>价格 — €1,234.56 — café</html>"
    meta = save_report(body, _FakeReport())
    assert meta is not None
    assert load_report_html(meta.report_id) == body


def test_load_report_html_empty_index_returns_none() -> None:
    assert load_report_html("anything") is None


# ─── delete_report ─────────────────────────────────────────────────────────

def test_delete_report_removes_entry_and_file() -> None:
    meta = save_report("<html>", _FakeReport())
    assert meta is not None
    path = Path(meta.file_path)
    assert path.exists()

    ok = delete_report(meta.report_id)
    assert ok is True
    assert not path.exists()
    loaded = _load_index()
    assert all(m.report_id != meta.report_id for m in loaded)


def test_delete_report_unknown_id_returns_false() -> None:
    save_report("<html>", _FakeReport())
    assert delete_report("never-existed") is False
    # Index intact
    assert len(_load_index()) == 1


def test_delete_report_tolerates_already_missing_file() -> None:
    meta = save_report("<html>", _FakeReport())
    assert meta is not None
    Path(meta.file_path).unlink()
    # The file is gone, but the entry must still be removed cleanly.
    assert delete_report(meta.report_id) is True
    assert _load_index() == []


def test_delete_report_empty_index_returns_false() -> None:
    assert delete_report("any-id") is False


# ─── get_report_stats ──────────────────────────────────────────────────────

def test_get_report_stats_empty_returns_zeroed_dict() -> None:
    stats = get_report_stats()
    assert stats["total_reports"] == 0
    assert stats["total_size_mb"] == 0.0
    assert stats["oldest_date"] is None
    assert stats["newest_date"] is None
    assert stats["avg_sentiment_score"] == 0.0
    assert stats["sentiment_distribution"] == {}


def test_get_report_stats_aggregates_totals() -> None:
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = datetime.now(timezone.utc)
    metas = []
    for i, (label, score) in enumerate([
        ("BULLISH", 0.6),
        ("BULLISH", 0.4),
        ("BEARISH", -0.5),
    ]):
        f = rh.REPORT_DIR / f"r{i}.html"
        f.write_text("<html>", encoding="utf-8")
        metas.append(_meta(
            report_id=f"r{i}",
            file_path=str(f),
            generated_at=(base + timedelta(seconds=i)).isoformat(),
            sentiment_label=label,
            sentiment_score=score,
            file_size_kb=1024.0,  # 1 MB each
        ))
    _save_index(metas)

    stats = get_report_stats()
    assert stats["total_reports"] == 3
    assert stats["total_size_mb"] == 3.0
    assert stats["sentiment_distribution"] == {"BULLISH": 2, "BEARISH": 1}
    # avg (0.6 + 0.4 + -0.5) / 3 = 0.16667
    assert abs(stats["avg_sentiment_score"] - 0.1667) < 0.001
    assert stats["oldest_date"] < stats["newest_date"]


def test_get_report_stats_excludes_entries_with_missing_files() -> None:
    """get_report_stats delegates to list_reports, which filters missing files."""
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    real = rh.REPORT_DIR / "real.html"
    real.write_text("<html>", encoding="utf-8")
    metas = [
        _meta(report_id="real", file_path=str(real), file_size_kb=512.0),
        _meta(report_id="ghost", file_path="/no/such/file.html",
              file_size_kb=999.0),
    ]
    _save_index(metas)
    stats = get_report_stats()
    assert stats["total_reports"] == 1
    # 512 KB → 0.5 MB
    assert stats["total_size_mb"] == 0.5
