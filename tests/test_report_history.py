"""Tests for utils.report_history — report persistence layer (SQLite-backed).

Reports are stored as HTML files under cache/reports/ with metadata in the
SQLite ``report_history`` table (see ``state.db``). Each test redirects
DB_PATH to a tmp_path so the real database is never touched.

Covers:
  - ReportMeta dataclass shape
  - Module constants (REPORT_DIR, MAX_REPORTS)
  - _attr: attribute access, dict access, None → default, missing → default
  - _safe_float / _safe_int: numeric strings, garbage → default
  - _row_to_meta: SQLite Row → ReportMeta round-trip
  - _prune_old_reports: ≤ MAX_REPORTS no-op; trims to newest N from the
    SQLite table; deletes pruned files on disk; missing files don't raise
  - save_report: writes HTML, inserts row, returns populated ReportMeta;
    extracts metadata from object attrs OR dict; safe defaults when fields
    are missing; pruning triggers when count > MAX_REPORTS; returns None
    on write failure (REPORT_DIR unwritable)
  - list_reports: newest-first sort; filters out entries whose file is
    gone; cleans index when files missing; returns [] on error
  - load_report_html: returns HTML for a known id; None for unknown id;
    None when file deleted underneath; round-trips unicode
  - delete_report: removes row and file; returns True on success; False
    for unknown id; tolerates already-deleted files
  - get_report_stats: empty → zeroed dict; populated → totals, oldest/newest,
    average sentiment, label distribution
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import report_history as rh
from utils.report_history import (
    MAX_REPORTS,
    ReportMeta,
    _attr,
    _prune_old_reports,
    _safe_float,
    _safe_int,
    delete_report,
    get_report_stats,
    list_reports,
    load_report_html,
    save_report,
)


# ─── Fixture: isolate REPORT_DIR + SQLite DB per test ──────────────────────

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    """Redirect both the HTML file dir AND the SQLite DB so no test
    touches real cache/."""
    from state import db as state_db

    tmp_reports = tmp_path / "reports"
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_reports)
    monkeypatch.setattr(rh, "_INDEX_FILE", tmp_reports / "report_index.json")
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Stand-in report object ────────────────────────────────────────────────

@dataclass
class _FakeReport:
    date: str = "January 15, 2026"
    market_sentiment: str = "BULLISH"
    sentiment_score: float = 0.65
    risk_level: str = "MODERATE"
    signal_count: int = 7
    data_quality: str = "FULL"


def _insert_meta(
    report_id: str,
    file_path: str,
    generated_at: str | None = None,
    sentiment_label: str = "BULLISH",
    sentiment_score: float = 0.5,
    file_size_kb: float = 12.5,
    report_date: str = "Test Date",
    risk_level: str = "MODERATE",
    signal_count: int = 3,
    data_quality: str = "FULL",
) -> ReportMeta:
    """Insert a row directly into the SQLite report_history table.
    Replaces the old `_save_index([_meta(...)])` pattern."""
    from state.db import get_connection

    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (report_id, generated_at, report_date, sentiment_label,
             sentiment_score, risk_level, signal_count, data_quality,
             file_path, file_size_kb),
        )
    return ReportMeta(
        report_id=report_id,
        generated_at=generated_at,
        report_date=report_date,
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
        risk_level=risk_level,
        signal_count=signal_count,
        data_quality=data_quality,
        file_path=file_path,
        file_size_kb=file_size_kb,
    )


def _row_count() -> int:
    from state.db import get_connection
    return get_connection().execute(
        "SELECT COUNT(*) AS n FROM report_history"
    ).fetchone()["n"]


# ─── Dataclass + constants ──────────────────────────────────────────────────

def test_report_meta_shape() -> None:
    m = ReportMeta(
        report_id="x", generated_at="t", report_date="d",
        sentiment_label="BULLISH", sentiment_score=0.5, risk_level="LOW",
        signal_count=3, data_quality="FULL", file_path="/tmp/x",
        file_size_kb=1.0,
    )
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


# ─── _prune_old_reports (SQLite-backed) ────────────────────────────────────

def test_prune_noop_when_at_or_below_cap(monkeypatch) -> None:
    """Fewer rows than MAX_REPORTS → no deletion."""
    monkeypatch.setattr(rh, "MAX_REPORTS", 5)
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        f = rh.REPORT_DIR / f"r{i}.html"
        f.write_text("<html>", encoding="utf-8")
        _insert_meta(f"r{i}", str(f))
    _prune_old_reports()
    assert _row_count() == 3


def test_prune_keeps_only_newest_n(monkeypatch) -> None:
    """More rows than MAX_REPORTS → newest two survive."""
    monkeypatch.setattr(rh, "MAX_REPORTS", 2)
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = datetime.now(timezone.utc)
    for i in range(5):
        f = rh.REPORT_DIR / f"r{i}.html"
        f.write_text("<html>", encoding="utf-8")
        _insert_meta(
            f"r{i}", str(f),
            generated_at=(base + timedelta(seconds=i)).isoformat(),
        )
    _prune_old_reports()
    # r3 and r4 have the largest timestamps
    survivors = {m.report_id for m in list_reports()}
    assert survivors == {"r3", "r4"}


def test_prune_deletes_files_for_pruned_entries(monkeypatch) -> None:
    monkeypatch.setattr(rh, "MAX_REPORTS", 1)
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = datetime.now(timezone.utc)
    f_old = rh.REPORT_DIR / "old.html"
    f_new = rh.REPORT_DIR / "new.html"
    f_old.write_text("old", encoding="utf-8")
    f_new.write_text("new", encoding="utf-8")
    _insert_meta("old", str(f_old), generated_at=base.isoformat())
    _insert_meta("new", str(f_new),
                 generated_at=(base + timedelta(seconds=10)).isoformat())

    _prune_old_reports()
    assert not f_old.exists(), "pruned file should be deleted"
    assert f_new.exists(), "kept file should remain"


def test_prune_tolerates_missing_files(monkeypatch) -> None:
    """A pruned row pointing at a nonexistent file must not raise."""
    monkeypatch.setattr(rh, "MAX_REPORTS", 1)
    base = datetime.now(timezone.utc)
    _insert_meta("ghost", "/nonexistent/path/x.html",
                 generated_at=base.isoformat())
    _insert_meta("kept", "/nonexistent/path/y.html",
                 generated_at=(base + timedelta(seconds=5)).isoformat())
    _prune_old_reports()  # must not raise
    assert _row_count() == 1


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


def test_save_report_inserts_row() -> None:
    save_report("<a>", _FakeReport())
    save_report("<b>", _FakeReport())
    assert _row_count() == 2


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
    assert save_report("<html>", _FakeReport()) is None


def test_save_report_triggers_pruning(monkeypatch) -> None:
    """When more than MAX_REPORTS exist, the index trims down."""
    monkeypatch.setattr(rh, "MAX_REPORTS", 2)
    save_report("<a>", _FakeReport())
    save_report("<b>", _FakeReport())
    save_report("<c>", _FakeReport())
    assert _row_count() == 2


# ─── list_reports ──────────────────────────────────────────────────────────

def test_list_reports_empty_when_no_rows() -> None:
    assert list_reports() == []


def test_list_reports_sorts_newest_first() -> None:
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = datetime.now(timezone.utc)
    files = []
    for i in range(3):
        f = rh.REPORT_DIR / f"r{i}.html"
        f.write_text("<html>", encoding="utf-8")
        files.append(f)
        _insert_meta(
            f"r{i}", str(f),
            generated_at=(base + timedelta(seconds=i * 5)).isoformat(),
        )
    out = list_reports()
    # Newest first: r2, r1, r0
    assert [m.report_id for m in out] == ["r2", "r1", "r0"]


def test_list_reports_filters_out_missing_files(tmp_path) -> None:
    """If an HTML file is deleted, the row is removed from the SQLite
    index and the cleaned list is returned."""
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    real = rh.REPORT_DIR / "real.html"
    real.write_text("<html>", encoding="utf-8")
    _insert_meta("real", str(real))
    _insert_meta("ghost", str(tmp_path / "gone.html"))

    out = list_reports()
    assert {m.report_id for m in out} == {"real"}
    # Persisted cleanup
    assert _row_count() == 1


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


def test_load_report_html_empty_db_returns_none() -> None:
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
    assert _row_count() == 0


def test_delete_report_unknown_id_returns_false() -> None:
    save_report("<html>", _FakeReport())
    assert delete_report("never-existed") is False
    assert _row_count() == 1


def test_delete_report_tolerates_already_missing_file() -> None:
    meta = save_report("<html>", _FakeReport())
    assert meta is not None
    Path(meta.file_path).unlink()
    # The file is gone, but the row must still be removed cleanly.
    assert delete_report(meta.report_id) is True
    assert _row_count() == 0


def test_delete_report_empty_db_returns_false() -> None:
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
    for i, (label, score) in enumerate([
        ("BULLISH", 0.6),
        ("BULLISH", 0.4),
        ("BEARISH", -0.5),
    ]):
        f = rh.REPORT_DIR / f"r{i}.html"
        f.write_text("<html>", encoding="utf-8")
        _insert_meta(
            f"r{i}", str(f),
            generated_at=(base + timedelta(seconds=i)).isoformat(),
            sentiment_label=label,
            sentiment_score=score,
            file_size_kb=1024.0,  # 1 MB each
        )

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
    _insert_meta("real", str(real), file_size_kb=512.0)
    _insert_meta("ghost", "/no/such/file.html", file_size_kb=999.0)
    stats = get_report_stats()
    assert stats["total_reports"] == 1
    assert stats["total_size_mb"] == 0.5


# ─── Public-share columns flow through _row_to_meta ────────────────────────

def test_list_reports_defaults_public_fields_for_unshared_rows() -> None:
    """A freshly-saved report has empty public_slug + public_expires_at,
    and list_reports() must surface those defaults on the ReportMeta."""
    save_report("<html>", _FakeReport())
    out = list_reports()
    assert len(out) == 1
    assert out[0].public_slug == ""
    assert out[0].public_expires_at == ""


def test_list_reports_carries_public_fields_after_make_public() -> None:
    """After make_public(), list_reports() must surface the slug + expiry
    on the corresponding ReportMeta."""
    meta = save_report("<html>", _FakeReport())
    assert meta is not None
    slug = rh.make_public(meta.report_id, expires_in_days=7)
    assert slug is not None

    rows = list_reports()
    matching = [r for r in rows if r.report_id == meta.report_id]
    assert len(matching) == 1
    assert matching[0].public_slug == slug
    assert matching[0].public_expires_at != ""
