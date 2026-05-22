"""Tests for engine.perf_telemetry — per-tab render-duration telemetry.

Coverage:
  - record_render inserts a row with the right shape; auto-generates
    event_id (uuid) and started_at; coerces success bool to 0/1
  - record_render never raises — DB writes are best-effort
  - track_render context manager records duration on clean exit
  - track_render with an Exception records success=False + error_msg
    AND re-raises (so existing error handling still fires)
  - track_render does NOT swallow SystemExit / KeyboardInterrupt
  - get_perf_summary on empty DB returns zeroed dict with right keys
  - get_perf_summary aggregates a populated dataset correctly —
    totals, success_rate, by_tab (count / median / p95 / error_count),
    top_slow_tabs sorted desc
  - get_perf_summary window_hours filter excludes events older than N hours
  - prune_old_events deletes old rows and preserves new ones; returns
    the number deleted; never raises
  - worker.scheduler.run_perf_prune_job wraps prune_old_events and
    forwards the int count

All tests are hermetic — per-test SQLite isolation via the standard
monkeypatch idiom from test_state_db / test_llm_telemetry.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ─── DB isolation: every test gets a per-test SQLite file ─────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB pointed at tmp_path. Critical because
    tab_render_events is a shared state table — without isolation, tests
    would see each other's rows."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _all_events():
    """Return every row of tab_render_events ordered by started_at ASC."""
    from state.db import get_connection
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM tab_render_events ORDER BY started_at ASC"
    ).fetchall()


def _backdate_latest(hours: int) -> str:
    """Shift the most recent event's started_at back by N hours.

    Returns the new ISO timestamp. Used by window-filter tests to
    simulate older events without forging UUIDs."""
    from state.db import get_connection
    conn = get_connection()
    new_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn.execute(
        """
        UPDATE tab_render_events
        SET started_at = ?
        WHERE event_id = (
            SELECT event_id FROM tab_render_events
            ORDER BY started_at DESC LIMIT 1
        )
        """,
        (new_ts,),
    )
    return new_ts


def _backdate_latest_days(days: int) -> str:
    """Shift the most recent event's started_at back by N days."""
    return _backdate_latest(hours=days * 24)


# ═══════════════════════════════════════════════════════════════════════════
# Schema v8 — table + indexes present after init
# ═══════════════════════════════════════════════════════════════════════════

def test_schema_v8_table_exists() -> None:
    """tab_render_events table must exist after get_connection() opens a
    fresh DB — confirms the v8 migration is wired into _init_schema."""
    from state.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='tab_render_events'"
    ).fetchall()
    assert len(rows) == 1


def test_schema_v8_indexes_exist() -> None:
    """Both indexes specified in the v8 schema are created."""
    from state.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='tab_render_events'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_tab_render_events_started_at" in names
    assert "idx_tab_render_events_tab" in names


def test_schema_version_is_at_least_eight() -> None:
    """Schema version constant is at least 8 (when tab_render_events
    was introduced) AND lands in kv_state.schema_version after init."""
    from state.db import SCHEMA_VERSION, get_connection

    assert SCHEMA_VERSION >= 8
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'schema_version'"
    ).fetchone()
    assert int(row["value"]) >= 8
    assert int(row["value"]) == SCHEMA_VERSION


# ═══════════════════════════════════════════════════════════════════════════
# record_render — shape, auto-generated fields
# ═══════════════════════════════════════════════════════════════════════════

def test_record_render_inserts_row_with_correct_shape() -> None:
    """record_render writes one row with the expected column values."""
    from engine.perf_telemetry import record_render

    record_render(
        tab_name="overview",
        duration_ms=42,
        success=True,
        error_msg="",
    )

    rows = _all_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["tab_name"] == "overview"
    assert row["duration_ms"] == 42
    assert row["success"] == 1
    assert row["error_msg"] == ""


def test_record_render_auto_generates_event_id_as_uuid() -> None:
    """event_id is a valid uuid4 string — caller doesn't supply one."""
    from engine.perf_telemetry import record_render

    record_render(tab_name="overview", duration_ms=10)
    rows = _all_events()
    parsed = uuid.UUID(rows[0]["event_id"])
    assert str(parsed) == rows[0]["event_id"]


def test_record_render_auto_generates_iso_started_at() -> None:
    """started_at is set to ISO 8601 UTC at insertion time."""
    from engine.perf_telemetry import record_render

    before = datetime.now(timezone.utc)
    record_render(tab_name="overview", duration_ms=5)
    after = datetime.now(timezone.utc)

    rows = _all_events()
    ts = datetime.fromisoformat(rows[0]["started_at"])
    assert before - timedelta(seconds=1) <= ts <= after + timedelta(seconds=1)


def test_record_render_failure_path_stores_error_msg() -> None:
    """success=False + error_msg lands in the row verbatim."""
    from engine.perf_telemetry import record_render

    record_render(
        tab_name="rates",
        duration_ms=123,
        success=False,
        error_msg="ValueError: bad input",
    )
    row = _all_events()[0]
    assert row["success"] == 0
    assert row["error_msg"] == "ValueError: bad input"


def test_record_render_returns_none() -> None:
    """record_render is a side-effect-only API — explicit return is None."""
    from engine.perf_telemetry import record_render

    assert record_render(tab_name="x", duration_ms=1) is None


def test_record_render_coerces_negative_duration_to_zero() -> None:
    """A negative duration (clock skew) lands as 0, not as a negative
    INTEGER that would skew aggregation."""
    from engine.perf_telemetry import record_render

    record_render(tab_name="overview", duration_ms=-5)
    row = _all_events()[0]
    assert row["duration_ms"] == 0


def test_record_render_swallows_db_errors(monkeypatch) -> None:
    """record_render must never raise — a broken DB connection is logged
    at debug and the call returns normally."""
    from engine import perf_telemetry as telem

    def _broken_get_connection():
        raise RuntimeError("database is locked")

    monkeypatch.setattr("state.db.get_connection", _broken_get_connection)

    # MUST NOT raise.
    telem.record_render(tab_name="x", duration_ms=10)


# ═══════════════════════════════════════════════════════════════════════════
# track_render — context manager
# ═══════════════════════════════════════════════════════════════════════════

def test_track_render_records_duration_on_success() -> None:
    """A successful with-block records exactly one row with success=1
    and a non-negative duration_ms."""
    import time

    from engine.perf_telemetry import track_render

    with track_render("overview"):
        time.sleep(0.01)  # 10 ms — enough to register a positive duration

    rows = _all_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["tab_name"] == "overview"
    assert row["success"] == 1
    assert row["error_msg"] == ""
    # The wrapped sleep was 10 ms; duration must be at least 1 ms (rounded).
    # Upper bound is generous — slow CI shouldn't fail this.
    assert row["duration_ms"] >= 1


def test_track_render_records_failure_and_reraises() -> None:
    """An Exception inside the with-block records success=0 + error_msg
    AND propagates the original exception unchanged."""
    from engine.perf_telemetry import track_render

    with pytest.raises(ValueError, match="bad render"):
        with track_render("portfolio"):
            raise ValueError("bad render")

    rows = _all_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["tab_name"] == "portfolio"
    assert row["success"] == 0
    assert "bad render" in row["error_msg"]


def test_track_render_does_not_swallow_system_exit() -> None:
    """SystemExit is a control-flow signal and MUST propagate without
    being recorded as a render failure — the caller wants the process
    to terminate, not a telemetry row in the log."""
    from engine.perf_telemetry import track_render

    with pytest.raises(SystemExit):
        with track_render("x"):
            raise SystemExit(2)

    # No row should be written because we only catch Exception, not
    # BaseException.
    assert _all_events() == []


def test_track_render_does_not_swallow_keyboard_interrupt() -> None:
    """Same contract as SystemExit — KeyboardInterrupt is BaseException
    and must propagate untouched."""
    from engine.perf_telemetry import track_render

    with pytest.raises(KeyboardInterrupt):
        with track_render("x"):
            raise KeyboardInterrupt()

    assert _all_events() == []


# ═══════════════════════════════════════════════════════════════════════════
# get_perf_summary — empty DB
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_empty_db_returns_zeroed_dict() -> None:
    """No events recorded → all totals zero, all breakdowns empty."""
    from engine.perf_telemetry import get_perf_summary

    s = get_perf_summary(window_hours=24)
    assert s["window_hours"] == 24
    assert s["total_renders"] == 0
    assert s["success_rate"] == 0.0
    assert s["by_tab"] == {}
    assert s["top_slow_tabs"] == []


def test_summary_has_all_expected_keys() -> None:
    """Lock the public shape of the summary dict so UI code can rely on
    keys being present even on an empty DB."""
    from engine.perf_telemetry import get_perf_summary

    s = get_perf_summary(window_hours=24)
    expected = {
        "window_hours", "total_renders", "success_rate",
        "by_tab", "top_slow_tabs",
    }
    assert set(s.keys()) == expected


def test_summary_invalid_window_hours_returns_empty() -> None:
    """Non-positive window_hours returns the empty shape rather than
    raising or running an unbounded query."""
    from engine.perf_telemetry import get_perf_summary, record_render

    record_render(tab_name="overview", duration_ms=10)

    s = get_perf_summary(window_hours=0)
    assert s["total_renders"] == 0
    assert s["by_tab"] == {}


# ═══════════════════════════════════════════════════════════════════════════
# get_perf_summary — aggregation
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_aggregates_totals_and_success_rate() -> None:
    """Five events (four success, one failure) → totals=5, success_rate=0.8."""
    from engine.perf_telemetry import get_perf_summary, record_render

    record_render(tab_name="a", duration_ms=10, success=True)
    record_render(tab_name="a", duration_ms=20, success=True)
    record_render(tab_name="b", duration_ms=30, success=True)
    record_render(tab_name="b", duration_ms=40, success=True)
    record_render(tab_name="b", duration_ms=50, success=False, error_msg="oops")

    s = get_perf_summary(window_hours=24)
    assert s["total_renders"] == 5
    assert s["success_rate"] == pytest.approx(0.8, abs=1e-6)


def test_summary_by_tab_aggregates_per_tab_correctly() -> None:
    """by_tab buckets events by tab_name; count + median + error_count
    are computed per bucket."""
    from engine.perf_telemetry import get_perf_summary, record_render

    # Tab 'a' — 3 success events with durations 10, 20, 30. Median = 20.
    record_render(tab_name="a", duration_ms=10, success=True)
    record_render(tab_name="a", duration_ms=20, success=True)
    record_render(tab_name="a", duration_ms=30, success=True)
    # Tab 'b' — 2 events, one success (100ms) and one failure (200ms).
    # Median = 150 (average of 100 and 200 for even-length).
    record_render(tab_name="b", duration_ms=100, success=True)
    record_render(tab_name="b", duration_ms=200, success=False, error_msg="x")

    s = get_perf_summary(window_hours=24)

    assert s["by_tab"]["a"]["count"] == 3
    assert s["by_tab"]["a"]["median_ms"] == 20
    assert s["by_tab"]["a"]["error_count"] == 0

    assert s["by_tab"]["b"]["count"] == 2
    assert s["by_tab"]["b"]["median_ms"] == 150
    assert s["by_tab"]["b"]["error_count"] == 1


def test_summary_p95_on_populated_dataset() -> None:
    """p95 of a known sequence — 100 events at durations 1..100 — must
    land at 95 (the canonical "type-7" linear-interpolation percentile
    matches numpy / pandas)."""
    from engine.perf_telemetry import get_perf_summary, record_render

    for i in range(1, 101):  # 1..100 ms
        record_render(tab_name="t", duration_ms=i, success=True)

    s = get_perf_summary(window_hours=24)
    # statistics.quantiles(n=100)[94] on 1..100 returns 95.95; rounded → 96.
    # The test asserts the value is in the expected percentile band rather
    # than pinning a specific Python statistics implementation detail.
    p95 = s["by_tab"]["t"]["p95_ms"]
    assert 94 <= p95 <= 96


def test_summary_p95_degenerate_single_sample() -> None:
    """A single-sample tab returns the only value as p95 rather than
    raising on statistics.quantiles (which needs >= 2 samples)."""
    from engine.perf_telemetry import get_perf_summary, record_render

    record_render(tab_name="solo", duration_ms=42, success=True)
    s = get_perf_summary(window_hours=24)
    assert s["by_tab"]["solo"]["p95_ms"] == 42


def test_summary_top_slow_tabs_sorted_desc_by_median() -> None:
    """top_slow_tabs is sorted by median_ms desc — slowest first."""
    from engine.perf_telemetry import get_perf_summary, record_render

    # Three tabs with different medians: fast=5, mid=50, slow=500.
    record_render(tab_name="fast", duration_ms=5,   success=True)
    record_render(tab_name="mid",  duration_ms=50,  success=True)
    record_render(tab_name="slow", duration_ms=500, success=True)

    s = get_perf_summary(window_hours=24)
    names = [entry["tab_name"] for entry in s["top_slow_tabs"]]
    assert names == ["slow", "mid", "fast"]
    # Each entry carries the full by_tab payload.
    assert s["top_slow_tabs"][0]["median_ms"] == 500
    assert s["top_slow_tabs"][0]["count"] == 1


def test_summary_top_slow_tabs_capped_at_ten() -> None:
    """Even with 20 distinct tabs, top_slow_tabs returns at most 10."""
    from engine.perf_telemetry import get_perf_summary, record_render

    for i in range(20):
        # Increasing median so all tabs are distinct.
        record_render(tab_name=f"t{i:02d}", duration_ms=10 + i, success=True)

    s = get_perf_summary(window_hours=24)
    assert len(s["top_slow_tabs"]) == 10
    # The 10 slowest (t10..t19) should be present, sorted desc.
    expected = [f"t{i:02d}" for i in range(19, 9, -1)]
    assert [e["tab_name"] for e in s["top_slow_tabs"]] == expected


def test_summary_window_hours_excludes_older_events() -> None:
    """An event backdated past the window cutoff is excluded."""
    from engine.perf_telemetry import get_perf_summary, record_render

    record_render(tab_name="recent", duration_ms=10, success=True)
    record_render(tab_name="old", duration_ms=20, success=True)
    _backdate_latest(hours=48)  # 2 days back — past a 24h window

    s = get_perf_summary(window_hours=24)
    assert s["total_renders"] == 1
    assert "recent" in s["by_tab"]
    assert "old" not in s["by_tab"]

    # Widening the window picks up the backdated event.
    s_wide = get_perf_summary(window_hours=72)
    assert s_wide["total_renders"] == 2
    assert "old" in s_wide["by_tab"]


def test_summary_swallows_db_errors(monkeypatch) -> None:
    """A broken DB must NOT raise — returns the empty shape."""
    from engine import perf_telemetry as telem

    def _broken_get_connection():
        raise RuntimeError("database is locked")

    monkeypatch.setattr("state.db.get_connection", _broken_get_connection)

    s = telem.get_perf_summary(window_hours=24)
    # Still has the right shape even when the query couldn't run.
    assert s["total_renders"] == 0
    assert s["by_tab"] == {}


# ═══════════════════════════════════════════════════════════════════════════
# prune_old_events — retention
# ═══════════════════════════════════════════════════════════════════════════

def test_prune_old_events_empty_db_returns_zero() -> None:
    """No rows at all → no-op, returns 0, does not raise."""
    from engine.perf_telemetry import prune_old_events

    assert prune_old_events(retention_days=30) == 0
    assert _all_events() == []


def test_prune_old_events_preserves_recent_and_deletes_old() -> None:
    """Two old events (backdated past cutoff) + one fresh → 2 deleted,
    1 survives."""
    from engine.perf_telemetry import prune_old_events, record_render

    record_render(tab_name="old1", duration_ms=10)
    _backdate_latest_days(days=45)
    record_render(tab_name="old2", duration_ms=20)
    _backdate_latest_days(days=60)
    record_render(tab_name="fresh", duration_ms=30)

    deleted = prune_old_events(retention_days=30)
    assert deleted == 2

    survivors = _all_events()
    assert len(survivors) == 1
    assert survivors[0]["tab_name"] == "fresh"


def test_prune_old_events_negative_retention_is_noop() -> None:
    """A negative retention_days is treated as a no-op — guards against
    an accidental "nuke everything" from a CLI typo."""
    from engine.perf_telemetry import prune_old_events, record_render

    for _ in range(3):
        record_render(tab_name="x", duration_ms=10)

    assert prune_old_events(retention_days=-1) == 0
    assert len(_all_events()) == 3


def test_prune_old_events_swallows_db_errors(monkeypatch) -> None:
    """A broken DB must NOT raise — returns 0."""
    from engine import perf_telemetry as telem

    def _broken_get_connection():
        raise RuntimeError("database is locked")

    monkeypatch.setattr("state.db.get_connection", _broken_get_connection)

    assert telem.prune_old_events(retention_days=30) == 0


def test_prune_old_events_default_retention_is_30_days() -> None:
    """Default retention window is 30 days — verified by the contract."""
    from engine.perf_telemetry import prune_old_events, record_render

    record_render(tab_name="just_outside", duration_ms=10)
    _backdate_latest_days(days=31)
    record_render(tab_name="just_inside", duration_ms=20)
    _backdate_latest_days(days=29)

    deleted = prune_old_events()  # default
    assert deleted == 1

    survivors = _all_events()
    assert len(survivors) == 1
    assert survivors[0]["tab_name"] == "just_inside"


# ═══════════════════════════════════════════════════════════════════════════
# worker.scheduler.run_perf_prune_job — wrapper
# ═══════════════════════════════════════════════════════════════════════════

def test_run_perf_prune_job_returns_int(monkeypatch) -> None:
    """Returns the int count from prune_old_events; never raises."""
    from worker.scheduler import run_perf_prune_job

    prune_mock = MagicMock(return_value=12)
    monkeypatch.setattr("engine.perf_telemetry.prune_old_events", prune_mock)

    result = run_perf_prune_job(retention_days=15)
    assert result == 12
    prune_mock.assert_called_once_with(retention_days=15)


def test_run_perf_prune_job_swallows_errors(monkeypatch) -> None:
    """A prune_old_events exception must NOT propagate; returns 0."""
    from worker.scheduler import run_perf_prune_job

    prune_mock = MagicMock(side_effect=RuntimeError("db wedged"))
    monkeypatch.setattr("engine.perf_telemetry.prune_old_events", prune_mock)

    # Must not raise.
    assert run_perf_prune_job() == 0


def test_run_perf_prune_job_default_retention_is_30_days(monkeypatch) -> None:
    """Default retention forwarded to prune_old_events is 30 days."""
    from worker.scheduler import run_perf_prune_job

    captured: dict = {}

    def _fake_prune(*, retention_days):
        captured["retention_days"] = retention_days
        return 0

    monkeypatch.setattr("engine.perf_telemetry.prune_old_events", _fake_prune)

    run_perf_prune_job()
    assert captured["retention_days"] == 30
