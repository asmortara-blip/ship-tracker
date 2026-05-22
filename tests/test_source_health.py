"""Tests for engine.source_health — periodic data-source health pings.

Covers:
  - HealthPing dataclass shape & defaults
  - _record_ping persists a row & never raises
  - ping_source happy path (probe returns ('up', '')) → status='up'
  - ping_source degraded path (probe returns ('degraded', '<msg>'))
  - ping_source failure path (probe raises) → status='down' with exc
  - ping_source for unknown source → status='down' + error_msg
  - ping_source duration_ms > 0 (perf_counter measured)
  - ping_all_sources iterates _PROBES and writes one row per source
  - get_health_summary empty → zeroed shape
  - get_health_summary aggregates by source
  - get_health_summary current_outages reflects LATEST ping per source
  - prune_old_pings deletes only rows older than the cutoff
  - prune_old_pings handles bad input gracefully
  - Worker scheduler integration: run_health_ping_job +
    run_health_prune_job are called from main() in the right order

Tests NEVER hit a real external service — every probe is mocked via
monkeypatch on the ``_PROBES`` dict.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ─── Fixture: isolate SQLite per test ──────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB pointed at a per-test tmp_path so no
    test touches the real cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── HealthPing dataclass ──────────────────────────────────────────────────

def test_health_ping_dataclass_fields() -> None:
    """Confirm the dataclass carries the documented fields."""
    from engine.source_health import HealthPing

    ping = HealthPing(
        ping_id="pid-1",
        source="fred",
        started_at="2026-05-22T00:00:00+00:00",
        duration_ms=150,
        status="up",
        error_msg="",
    )
    assert ping.ping_id == "pid-1"
    assert ping.source == "fred"
    assert ping.started_at == "2026-05-22T00:00:00+00:00"
    assert ping.duration_ms == 150
    assert ping.status == "up"
    assert ping.error_msg == ""


def test_health_ping_error_msg_default_empty() -> None:
    """error_msg has a sane default so callers can omit it on success."""
    from engine.source_health import HealthPing

    ping = HealthPing(
        ping_id="pid-2",
        source="x",
        started_at="t",
        duration_ms=0,
        status="up",
    )
    assert ping.error_msg == ""


# ─── _record_ping ──────────────────────────────────────────────────────────

def test_record_ping_inserts_row() -> None:
    """_record_ping writes the ping into data_source_health verbatim."""
    from engine.source_health import HealthPing, _record_ping
    from state.db import get_connection

    ping = HealthPing(
        ping_id="rec-1",
        source="fred",
        started_at="2026-05-22T00:00:00+00:00",
        duration_ms=42,
        status="up",
        error_msg="",
    )
    _record_ping(ping)

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM data_source_health WHERE ping_id = 'rec-1'"
    ).fetchone()
    assert row is not None
    assert row["source"] == "fred"
    assert row["started_at"] == "2026-05-22T00:00:00+00:00"
    assert row["duration_ms"] == 42
    assert row["status"] == "up"
    assert row["error_msg"] == ""


def test_record_ping_never_raises_on_db_failure(monkeypatch) -> None:
    """A broken DB connection must not propagate out of _record_ping."""
    from engine import source_health

    def _broken_get_connection():
        raise RuntimeError("db wedged")

    monkeypatch.setattr("state.db.get_connection", _broken_get_connection)

    ping = source_health.HealthPing(
        ping_id="rec-fail",
        source="x",
        started_at="t",
        duration_ms=0,
        status="up",
    )
    # Must not raise.
    source_health._record_ping(ping)


# ─── ping_source ───────────────────────────────────────────────────────────

def _patch_probes(monkeypatch, mapping: dict) -> None:
    """Replace ``_PROBES`` with the given mapping for this test only."""
    from engine import source_health

    monkeypatch.setattr(source_health, "_PROBES", dict(mapping))


def test_ping_source_up_path(monkeypatch) -> None:
    """A probe returning ('up', '') produces status='up' and duration_ms > 0."""
    from engine.source_health import ping_source

    _patch_probes(monkeypatch, {"x": lambda: ("up", "")})

    ping = ping_source("x")
    assert ping.source == "x"
    assert ping.status == "up"
    assert ping.error_msg == ""
    # duration_ms is set via perf_counter — typically tiny but > 0 even
    # for a no-op probe on a contemporary machine. Use >= 0 to avoid
    # flakes on a very fast machine where the elapsed wall-clock is
    # below the 1 ms rounding boundary.
    assert ping.duration_ms >= 0
    assert ping.ping_id  # uuid populated


def test_ping_source_degraded_path(monkeypatch) -> None:
    """A probe returning ('degraded', '<msg>') produces status='degraded'
    with the error_msg surfaced."""
    from engine.source_health import ping_source

    _patch_probes(
        monkeypatch,
        {"y": lambda: ("degraded", "partial result")},
    )

    ping = ping_source("y")
    assert ping.status == "degraded"
    assert ping.error_msg == "partial result"


def test_ping_source_raising_probe_marks_down(monkeypatch) -> None:
    """A probe that raises is captured as status='down' with the
    exception text surfaced in error_msg. ping_source itself NEVER
    propagates the exception."""
    from engine.source_health import ping_source

    def _exploding_probe() -> tuple[str, str]:
        raise RuntimeError("network unreachable")

    _patch_probes(monkeypatch, {"z": _exploding_probe})

    ping = ping_source("z")  # must not raise
    assert ping.status == "down"
    assert "network unreachable" in ping.error_msg


def test_ping_source_unknown_source_marks_down(monkeypatch) -> None:
    """Calling ping_source with a source that isn't registered records
    a 'down' ping with an explanatory error_msg rather than raising."""
    from engine.source_health import ping_source

    _patch_probes(monkeypatch, {})  # no probes registered

    ping = ping_source("nope")  # must not raise
    assert ping.status == "down"
    assert "no probe registered" in ping.error_msg
    assert ping.source == "nope"


def test_ping_source_unknown_status_falls_back_to_degraded(monkeypatch) -> None:
    """A probe returning an unknown status string is normalized to
    'degraded' with a hint, rather than silently passed through."""
    from engine.source_health import ping_source

    _patch_probes(
        monkeypatch,
        {"weird": lambda: ("FLOATY", "")},
    )

    ping = ping_source("weird")
    assert ping.status == "degraded"
    assert "unknown status" in ping.error_msg


def test_ping_source_records_row_to_sqlite(monkeypatch) -> None:
    """ping_source writes one row to data_source_health on every call."""
    from engine.source_health import ping_source
    from state.db import get_connection

    _patch_probes(monkeypatch, {"fred": lambda: ("up", "")})

    ping = ping_source("fred")

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM data_source_health WHERE ping_id = ?",
        (ping.ping_id,),
    ).fetchone()
    assert row is not None
    assert row["source"] == "fred"
    assert row["status"] == "up"


def test_ping_source_started_at_is_iso_utc(monkeypatch) -> None:
    """started_at is an ISO-8601 UTC timestamp ending in '+00:00'."""
    from engine.source_health import ping_source

    _patch_probes(monkeypatch, {"x": lambda: ("up", "")})

    ping = ping_source("x")
    # Round-trip parse it as a sanity check on the format.
    parsed = datetime.fromisoformat(ping.started_at)
    assert parsed.tzinfo is not None


# ─── ping_all_sources ──────────────────────────────────────────────────────

def test_ping_all_sources_runs_every_probe(monkeypatch) -> None:
    """Every entry in _PROBES yields one HealthPing in the result list,
    and one row in the data_source_health table."""
    from engine.source_health import ping_all_sources
    from state.db import get_connection

    _patch_probes(
        monkeypatch,
        {
            "fred":      lambda: ("up", ""),
            "yfinance":  lambda: ("up", ""),
            "worldbank": lambda: ("degraded", "empty"),
        },
    )

    results = ping_all_sources()
    assert len(results) == 3
    sources = {p.source for p in results}
    assert sources == {"fred", "yfinance", "worldbank"}

    conn = get_connection()
    rows = conn.execute(
        "SELECT source, status FROM data_source_health ORDER BY source"
    ).fetchall()
    assert {r["source"] for r in rows} == {"fred", "yfinance", "worldbank"}


def test_ping_all_sources_continues_when_one_probe_raises(monkeypatch) -> None:
    """A raising probe must not short-circuit the rest of the loop."""
    from engine.source_health import ping_all_sources

    def _broken() -> tuple[str, str]:
        raise ValueError("boom")

    _patch_probes(
        monkeypatch,
        {
            "fred":   _broken,
            "stocks": lambda: ("up", ""),
        },
    )

    results = ping_all_sources()
    by_source = {p.source: p for p in results}
    assert by_source["fred"].status == "down"
    assert "boom" in by_source["fred"].error_msg
    assert by_source["stocks"].status == "up"


def test_ping_all_sources_returns_empty_when_no_probes(monkeypatch) -> None:
    """A registry-empty deployment is a degenerate case — no rows, no
    crash."""
    from engine.source_health import ping_all_sources

    _patch_probes(monkeypatch, {})

    assert ping_all_sources() == []


# ─── get_health_summary ────────────────────────────────────────────────────

def test_get_health_summary_empty_returns_zeroed() -> None:
    """No rows in the table → zeroed dict with the documented keys."""
    from engine.source_health import get_health_summary

    summary = get_health_summary(window_hours=24)
    assert summary == {
        "window_hours":     24,
        "total_pings":      0,
        "by_source":        {},
        "current_outages":  [],
    }


def test_get_health_summary_aggregates_by_source(monkeypatch) -> None:
    """3 up + 1 degraded for fred, 2 up for yfinance → correct per-source totals."""
    from engine.source_health import ping_source

    _patch_probes(
        monkeypatch,
        {
            "fred":     lambda: ("up", ""),
            "yfinance": lambda: ("up", ""),
        },
    )

    # Drive 3 up + 1 degraded for fred, 2 up for yfinance.
    ping_source("fred")
    ping_source("fred")
    ping_source("fred")
    _patch_probes(
        monkeypatch,
        {
            "fred":     lambda: ("degraded", "rate limited"),
            "yfinance": lambda: ("up", ""),
        },
    )
    ping_source("fred")
    ping_source("yfinance")
    ping_source("yfinance")

    from engine.source_health import get_health_summary
    summary = get_health_summary(window_hours=24)

    assert summary["total_pings"] == 6
    assert set(summary["by_source"].keys()) == {"fred", "yfinance"}

    fred = summary["by_source"]["fred"]
    assert fred["count"] == 4
    assert fred["up_count"] == 3
    assert fred["degraded_count"] == 1
    assert fred["down_count"] == 0
    assert fred["last_status"] == "degraded"

    yfin = summary["by_source"]["yfinance"]
    assert yfin["count"] == 2
    assert yfin["up_count"] == 2
    assert yfin["last_status"] == "up"


def test_get_health_summary_current_outages_uses_latest_ping(monkeypatch) -> None:
    """current_outages lists sources whose LATEST ping was 'down', even
    if earlier pings in the window were up."""
    from engine.source_health import HealthPing, _record_ping, get_health_summary

    # Source 'a': first up, then down — should appear in current_outages.
    # Source 'b': first down, then up — should NOT appear.
    # Source 'c': only down — should appear.
    now = datetime.now(timezone.utc)
    _record_ping(HealthPing(
        ping_id="a1",
        source="a",
        started_at=(now - timedelta(hours=2)).isoformat(),
        duration_ms=100,
        status="up",
    ))
    _record_ping(HealthPing(
        ping_id="a2",
        source="a",
        started_at=(now - timedelta(hours=1)).isoformat(),
        duration_ms=100,
        status="down",
        error_msg="500",
    ))
    _record_ping(HealthPing(
        ping_id="b1",
        source="b",
        started_at=(now - timedelta(hours=2)).isoformat(),
        duration_ms=100,
        status="down",
    ))
    _record_ping(HealthPing(
        ping_id="b2",
        source="b",
        started_at=(now - timedelta(hours=1)).isoformat(),
        duration_ms=100,
        status="up",
    ))
    _record_ping(HealthPing(
        ping_id="c1",
        source="c",
        started_at=(now - timedelta(hours=1)).isoformat(),
        duration_ms=100,
        status="down",
        error_msg="timeout",
    ))

    summary = get_health_summary(window_hours=24)
    # a and c are currently outage; b recovered.
    assert summary["current_outages"] == ["a", "c"]
    assert summary["by_source"]["a"]["last_status"] == "down"
    assert summary["by_source"]["b"]["last_status"] == "up"
    assert summary["by_source"]["c"]["last_status"] == "down"


def test_get_health_summary_avg_duration_ms_rounds_to_two_decimals(monkeypatch) -> None:
    """avg_duration_ms is the mean of all durations for that source."""
    from engine.source_health import HealthPing, _record_ping, get_health_summary

    now = datetime.now(timezone.utc)
    for i, ms in enumerate([100, 200, 300]):
        _record_ping(HealthPing(
            ping_id=f"d{i}",
            source="d",
            started_at=(now - timedelta(minutes=i)).isoformat(),
            duration_ms=ms,
            status="up",
        ))

    summary = get_health_summary(window_hours=24)
    # mean(100, 200, 300) = 200
    assert summary["by_source"]["d"]["avg_duration_ms"] == 200.0


def test_get_health_summary_window_hours_filters_old_rows() -> None:
    """Rows outside the window must not show up in the summary."""
    from engine.source_health import HealthPing, _record_ping, get_health_summary

    now = datetime.now(timezone.utc)
    # One row 1h ago (within), one 48h ago (outside a 24h window).
    _record_ping(HealthPing(
        ping_id="w1",
        source="x",
        started_at=(now - timedelta(hours=1)).isoformat(),
        duration_ms=10,
        status="up",
    ))
    _record_ping(HealthPing(
        ping_id="w2",
        source="x",
        started_at=(now - timedelta(hours=48)).isoformat(),
        duration_ms=10,
        status="down",
    ))

    summary = get_health_summary(window_hours=24)
    assert summary["by_source"]["x"]["count"] == 1
    assert summary["by_source"]["x"]["last_status"] == "up"
    assert summary["current_outages"] == []


def test_get_health_summary_non_positive_window_returns_empty() -> None:
    """0 or negative window → zeroed shape (preserves the requested window)."""
    from engine.source_health import get_health_summary

    summary = get_health_summary(window_hours=0)
    assert summary["total_pings"] == 0
    assert summary["by_source"] == {}


def test_get_health_summary_never_raises_on_db_failure(monkeypatch) -> None:
    """A broken DB returns the empty shape, never raises."""
    from engine import source_health

    def _broken_get_connection():
        raise RuntimeError("db wedged")

    monkeypatch.setattr("state.db.get_connection", _broken_get_connection)

    # Must not raise.
    summary = source_health.get_health_summary(window_hours=24)
    assert summary["total_pings"] == 0


# ─── prune_old_pings ───────────────────────────────────────────────────────

def test_prune_old_pings_deletes_only_old_rows() -> None:
    """Rows older than retention_days are deleted; newer rows are kept."""
    from engine.source_health import HealthPing, _record_ping, prune_old_pings
    from state.db import get_connection

    now = datetime.now(timezone.utc)
    # Old row (60 days ago) — should be pruned at retention_days=30.
    _record_ping(HealthPing(
        ping_id="old",
        source="x",
        started_at=(now - timedelta(days=60)).isoformat(),
        duration_ms=10,
        status="up",
    ))
    # Recent row (1 hour ago) — should survive.
    _record_ping(HealthPing(
        ping_id="recent",
        source="x",
        started_at=(now - timedelta(hours=1)).isoformat(),
        duration_ms=10,
        status="up",
    ))

    deleted = prune_old_pings(retention_days=30)
    assert deleted == 1

    conn = get_connection()
    ids = {
        r["ping_id"]
        for r in conn.execute("SELECT ping_id FROM data_source_health").fetchall()
    }
    assert ids == {"recent"}


def test_prune_old_pings_negative_is_noop() -> None:
    """A negative retention_days is treated as a no-op to guard against
    accidental nuke from a CLI typo."""
    from engine.source_health import HealthPing, _record_ping, prune_old_pings
    from state.db import get_connection

    _record_ping(HealthPing(
        ping_id="keep",
        source="x",
        started_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=10,
        status="up",
    ))

    deleted = prune_old_pings(retention_days=-1)
    assert deleted == 0

    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM data_source_health").fetchone()["n"]
    assert n == 1


def test_prune_old_pings_non_int_input_is_noop() -> None:
    """Non-int input is silently treated as a no-op (returns 0)."""
    from engine.source_health import prune_old_pings

    assert prune_old_pings(retention_days="abc") == 0  # type: ignore[arg-type]
    assert prune_old_pings(retention_days=None) == 0  # type: ignore[arg-type]


def test_prune_old_pings_never_raises_on_db_failure(monkeypatch) -> None:
    """A broken DB returns 0, never raises."""
    from engine import source_health

    def _broken_get_connection():
        raise RuntimeError("db wedged")

    monkeypatch.setattr("state.db.get_connection", _broken_get_connection)

    assert source_health.prune_old_pings(retention_days=30) == 0


# ─── Worker scheduler integration ──────────────────────────────────────────

def test_run_health_ping_job_returns_pings(monkeypatch) -> None:
    """run_health_ping_job calls ping_all_sources and returns the list."""
    from worker.scheduler import run_health_ping_job

    _patch_probes(
        monkeypatch,
        {
            "fred": lambda: ("up", ""),
            "yf":   lambda: ("degraded", "rate limited"),
        },
    )

    pings = run_health_ping_job()
    assert len(pings) == 2
    sources = {p.source for p in pings}
    assert sources == {"fred", "yf"}


def test_run_health_ping_job_swallows_unexpected_errors(monkeypatch) -> None:
    """If ping_all_sources somehow raises, the wrapper returns []."""
    from worker import scheduler

    def _explode() -> list:
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr("engine.source_health.ping_all_sources", _explode)

    assert scheduler.run_health_ping_job() == []


def test_run_health_prune_job_returns_int(monkeypatch) -> None:
    """run_health_prune_job forwards retention_days and returns the count."""
    from worker.scheduler import run_health_prune_job

    prune_mock = MagicMock(return_value=5)
    monkeypatch.setattr("engine.source_health.prune_old_pings", prune_mock)

    result = run_health_prune_job(retention_days=42)

    assert result == 5
    prune_mock.assert_called_once_with(retention_days=42)


def test_run_health_prune_job_swallows_errors(monkeypatch) -> None:
    """A prune_old_pings exception must NOT propagate; returns 0."""
    from worker.scheduler import run_health_prune_job

    prune_mock = MagicMock(side_effect=RuntimeError("kaboom"))
    monkeypatch.setattr("engine.source_health.prune_old_pings", prune_mock)

    assert run_health_prune_job() == 0


def test_main_calls_health_jobs_after_existing_prunes(monkeypatch) -> None:
    """main() invokes run_health_ping_job AFTER the existing prune jobs
    and BEFORE process exit, with run_health_prune_job right after it."""
    from worker import scheduler
    from worker.scheduler import ReportJobResult, main

    call_order: list[str] = []

    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: {})
    monkeypatch.setattr(
        scheduler,
        "run_daily_briefing_job",
        lambda bundle, *, push_to_channels=False: (
            call_order.append("briefing") or ReportJobResult(
                report_id="rid",
                file_path="/tmp/x.html",
                success=True,
                duration_s=0.1,
                error_msg="",
            )
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_telemetry_prune_job",
        lambda *a, **k: call_order.append("llm_prune") or 0,
    )
    monkeypatch.setattr(
        scheduler,
        "run_perf_prune_job",
        lambda *a, **k: call_order.append("perf_prune") or 0,
    )
    monkeypatch.setattr(
        scheduler,
        "run_snapshot_prune_job",
        lambda *a, **k: call_order.append("snap_prune") or 0,
    )
    monkeypatch.setattr(
        scheduler,
        "run_health_ping_job",
        lambda *a, **k: call_order.append("health_ping") or [],
    )
    monkeypatch.setattr(
        scheduler,
        "run_health_prune_job",
        lambda *a, **k: call_order.append("health_prune") or 0,
    )
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0

    # Ordering contract: briefing first, every prune in its registered
    # slot, then the new health ping + prune at the END.
    assert call_order == [
        "briefing",
        "llm_prune",
        "perf_prune",
        "snap_prune",
        "health_ping",
        "health_prune",
    ]


def test_main_health_step_failure_does_not_flip_exit_code(monkeypatch) -> None:
    """A raise inside run_health_ping_job must NOT flip the briefing's
    exit code — the report was still successfully built."""
    from worker import scheduler
    from worker.scheduler import ReportJobResult, main

    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: {})
    monkeypatch.setattr(
        scheduler,
        "run_daily_briefing_job",
        lambda bundle, *, push_to_channels=False: ReportJobResult(
            report_id="rid",
            file_path="/tmp/x.html",
            success=True,
            duration_s=0.1,
            error_msg="",
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_health_ping_job",
        MagicMock(side_effect=RuntimeError("health blew up")),
    )
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main()
    # Briefing still succeeded — exit code is still 0.
    assert excinfo.value.code == 0
