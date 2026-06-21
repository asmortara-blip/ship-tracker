"""R011 — the scheduler cadence stamp advances ONLY on job SUCCESS.

Before R011 the due-CHECK (``_job_due``) wrote the "last run" kv_state
stamp the instant it decided a job was due. So a job that was due but
then FAILED was recorded as having run and would not re-attempt until the
next full cadence window. The fix split the read (``_job_due``, now
read-only) from the write (``stamp_run``), and the gated-run path stamps
ONLY after the job completes successfully.

These tests assert the new contract:
  * a SUCCEEDING gated job advances the stamp → next due-check is False
    within the interval;
  * a RAISING gated job does NOT advance the stamp → still due next pass
    (retried);
  * a gated job that returns an explicit failure flag (``success=False`` /
    ``{"ok": False}``) does NOT advance the stamp;
  * ``force`` runs the job regardless and stamps on success;
  * the read-only ``_job_due`` never writes the stamp on its own.

All DB writes go through an isolated per-test SQLite file.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from worker import scheduler
from worker.scheduler import (
    ReportJobResult,
    _job_due,
    _run_gated,
    main,
    stamp_run,
)


# ─── Fixture: isolate SQLite per test (mirrors tests/test_scheduler.py) ──────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    from utils import report_history as rh

    tmp_reports = tmp_path / "reports"
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_reports)
    monkeypatch.setattr(rh, "_INDEX_FILE", tmp_reports / "report_index.json")
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ─────────────────────────────────────────────────────────────────

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _raw_stamp(name: str):
    """Read the raw kv_state cadence stamp for a job (None if absent)."""
    from state.db import get_connection

    key = scheduler._JOB_LASTRUN_KEY.format(name=name)
    row = get_connection().execute(
        "SELECT value FROM kv_state WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else row["value"]


# ─── _job_due is read-only ───────────────────────────────────────────────────

def test_job_due_does_not_write_stamp() -> None:
    """A bare due-check NEVER writes the cadence stamp (R011 read/write split)."""
    assert _raw_stamp("readonly-job") is None
    # Many checks, none should stamp.
    for _ in range(3):
        assert _job_due("readonly-job", 3600, now=NOW) is True
    assert _raw_stamp("readonly-job") is None


def test_stamp_run_then_job_due_is_false_within_interval() -> None:
    """stamp_run advances the stamp; _job_due reads it and reports not-due."""
    assert _job_due("stamped-job", 3600, now=NOW) is True
    stamp_run("stamped-job", now=NOW)
    assert _raw_stamp("stamped-job") == NOW.isoformat()
    assert _job_due("stamped-job", 3600, now=NOW) is False
    # …and due again once the interval has elapsed.
    assert _job_due("stamped-job", 3600, now=NOW + timedelta(hours=2)) is True


def test_stamp_run_never_raises_on_broken_db(monkeypatch) -> None:
    """stamp_run swallows a DB failure (the job already ran)."""
    def _boom():
        raise RuntimeError("db wedged")

    monkeypatch.setattr("state.db.get_connection", _boom)
    # Must not raise.
    stamp_run("any-job", now=NOW)


# ─── _run_gated: stamp only on success ───────────────────────────────────────

def test_run_gated_success_advances_stamp() -> None:
    """A succeeding gated job stamps → next due-check is False in-interval."""
    calls = {"n": 0}

    def ok_job():
        calls["n"] += 1
        return {"ok": True, "fired": 1}

    assert _job_due("grimace", 3600, now=NOW) is True
    _run_gated("grimace", 3600, ok_job, now=NOW)
    assert calls["n"] == 1
    assert _raw_stamp("grimace") == NOW.isoformat()
    assert _job_due("grimace", 3600, now=NOW) is False  # stamped → not due

    # A second pass within the interval does NOT re-run (gated out).
    _run_gated("grimace", 3600, ok_job, now=NOW)
    assert calls["n"] == 1


def test_run_gated_raise_does_not_advance_stamp_and_retries() -> None:
    """A RAISING gated job leaves the stamp un-advanced → still due → retried."""
    calls = {"n": 0}

    def boom_job():
        calls["n"] += 1
        raise RuntimeError("job blew up")

    # First pass: due, runs, raises → swallowed, NOT stamped.
    _run_gated("hamburglar", 3600, boom_job, now=NOW)
    assert calls["n"] == 1
    assert _raw_stamp("hamburglar") is None          # no stamp written
    assert _job_due("hamburglar", 3600, now=NOW) is True  # still due

    # Next pass (same instant, within interval): re-attempted because the
    # failed job never advanced its cadence.
    _run_gated("hamburglar", 3600, boom_job, now=NOW)
    assert calls["n"] == 2
    assert _raw_stamp("hamburglar") is None


def test_run_gated_explicit_failure_flag_does_not_advance_stamp() -> None:
    """A clean return that carries success=False / ok=False is NOT stamped."""
    # success-attribute path (dataclass like ReportJobResult)
    def fail_dataclass():
        return ReportJobResult(success=False, error_msg="save failed")

    _run_gated("filet", 3600, fail_dataclass, now=NOW)
    assert _raw_stamp("filet") is None
    assert _job_due("filet", 3600, now=NOW) is True   # still due → retried

    # ok-key path (snapshot-style count dict)
    def fail_dict():
        return {"ok": False, "saved_bytes": 0}

    _run_gated("mcflurry", 3600, fail_dict, now=NOW)
    assert _raw_stamp("mcflurry") is None
    assert _job_due("mcflurry", 3600, now=NOW) is True


def test_run_gated_no_success_flag_counts_as_success() -> None:
    """Fire-and-forget returns (count dict w/o ok, int, list, None) → stamped."""
    cases = [
        ("counts-dict", lambda: {"fired": 0, "skipped_cooldown": 0}),
        ("bare-int", lambda: 0),
        ("list-result", lambda: []),
        ("none-result", lambda: None),
    ]
    for name, fn in cases:
        _run_gated(name, 3600, fn, now=NOW)
        assert _raw_stamp(name) == NOW.isoformat(), name
        assert _job_due(name, 3600, now=NOW) is False, name


def test_run_gated_force_runs_and_stamps_on_success() -> None:
    """force runs the job even when already stamped; stamps again on success."""
    calls = {"n": 0}

    def ok_job():
        calls["n"] += 1
        return {"ok": True}

    # Stamp it in the future so a normal pass would be gated out.
    later = NOW + timedelta(minutes=10)
    stamp_run("bigmac", now=NOW)
    assert _job_due("bigmac", 3600, now=later) is False  # gated within interval

    # Forced pass runs anyway and re-stamps at `later`.
    _run_gated("bigmac", 3600, ok_job, now=later, force=True)
    assert calls["n"] == 1
    assert _raw_stamp("bigmac") == later.isoformat()


def test_run_gated_force_failure_does_not_stamp() -> None:
    """A forced run that fails still does NOT advance the stamp."""
    def boom():
        raise RuntimeError("forced but broken")

    stamp_run("mcrib", now=NOW)
    original = _raw_stamp("mcrib")
    later = NOW + timedelta(minutes=10)
    _run_gated("mcrib", 3600, boom, now=later, force=True)
    # Stamp unchanged — the forced run failed.
    assert _raw_stamp("mcrib") == original


def test_run_gated_within_interval_does_not_run() -> None:
    """Inside the cadence window the gated job is skipped entirely (no run,
    no re-stamp)."""
    calls = {"n": 0}

    def ok_job():
        calls["n"] += 1
        return {"ok": True}

    stamp_run("quarter", now=NOW)
    _run_gated("quarter", 3600, ok_job, now=NOW + timedelta(minutes=5))
    assert calls["n"] == 0  # gated out → never ran


# ─── main(): a FAILED daily briefing does not advance its cadence ────────────

def test_main_failed_briefing_does_not_advance_cadence_and_retries(
    monkeypatch,
) -> None:
    """Across two back-to-back main() passes a FAILING briefing re-attempts
    on the second pass (its daily cadence never advanced); a subsequent
    success then stamps it so a third pass is gated out."""
    def _stub_bundle():
        return {
            "port_results": [], "route_results": [], "insights": [],
            "freight_data": {}, "macro_data": {}, "stock_data": {},
            "news_items": [], "source": "stub",
        }

    monkeypatch.setattr(scheduler, "load_data_bundle", _stub_bundle)

    state = {"n": 0}

    def fake_briefing(bundle, *, push_to_channels=False):
        state["n"] += 1
        # First two attempts fail, third succeeds.
        ok = state["n"] >= 3
        return ReportJobResult(
            report_id="r" if ok else "",
            file_path="/tmp/x" if ok else "",
            success=ok,
            duration_s=0.1,
            error_msg="" if ok else "boom",
        )

    monkeypatch.setattr(scheduler, "run_daily_briefing_job", fake_briefing)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    # Pass 1: due, fails → exit 1, NOT stamped.
    with pytest.raises(SystemExit) as e1:
        main()
    assert e1.value.code == 1
    assert state["n"] == 1
    assert _raw_stamp("run_daily_briefing_job") is None

    # Pass 2: STILL due (failure didn't advance cadence) → re-attempted, fails.
    with pytest.raises(SystemExit) as e2:
        main()
    assert e2.value.code == 1
    assert state["n"] == 2
    assert _raw_stamp("run_daily_briefing_job") is None

    # Pass 3: still due → re-attempted, SUCCEEDS this time → exit 0, stamped.
    with pytest.raises(SystemExit) as e3:
        main()
    assert e3.value.code == 0
    assert state["n"] == 3
    assert _raw_stamp("run_daily_briefing_job") is not None

    # Pass 4: now stamped within the daily interval → briefing gated out
    # (skipped), build not re-attempted.
    with pytest.raises(SystemExit):
        main()
    assert state["n"] == 3  # not re-run
