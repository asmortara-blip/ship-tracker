"""Tests for ``engine.report_scheduler`` — cron-driven report scheduling.

Covers the defining properties of the module:

* ``parse_cron_expr`` accepts the documented forms (``*``, ``*/N``,
  literals, comma-lists) and rejects every malformed shape with a
  ``ValueError`` (not a swallow-and-fallback) so a UI typing bug
  surfaces loud.
* ``validate_cron_expr`` returns ``(True, '')`` / ``(False, msg)`` and
  NEVER raises — the UI can call it on every keystroke without a
  red-screen risk.
* ``compute_next_run_at`` returns a tz-aware datetime strictly after
  the base and matches the cron's intent (Vixie semantics on the day
  fields).
* ``save_schedule`` / ``load_schedules`` / ``get_schedule`` /
  ``delete_schedule`` round-trip cleanly, enforce per-user scope, and
  refuse to persist an invalid cron expression.
* ``get_due_schedules`` returns only enabled schedules whose
  ``next_run_at`` is past, and ignores disabled rows even when their
  time has come.
* ``run_report_scheduler_job`` (in ``worker.scheduler``) fires every
  due schedule, advances ``next_run_at`` on success AND on failure
  (so a broken schedule does not stick), and never raises.

The per-test isolation fixture monkeypatches ``state.db.DB_PATH`` so
each test runs against its own tmp-path SQLite file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ─── Isolation fixture ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite at tmp_path. Same pattern as test_ops_cli.py."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── parse_cron_expr — happy paths ────────────────────────────────────────

def test_parse_every_minute() -> None:
    from engine.report_scheduler import parse_cron_expr

    m, h, dom, mo, dow = parse_cron_expr("* * * * *")
    assert m == list(range(0, 60))
    assert h == list(range(0, 24))
    assert dom == list(range(1, 32))
    assert mo == list(range(1, 13))
    assert dow == list(range(0, 7))


def test_parse_daily_9am() -> None:
    from engine.report_scheduler import parse_cron_expr

    m, h, dom, mo, dow = parse_cron_expr("0 9 * * *")
    assert m == [0]
    assert h == [9]
    assert dom == list(range(1, 32))
    assert mo == list(range(1, 13))
    assert dow == list(range(0, 7))


def test_parse_every_15_minutes() -> None:
    from engine.report_scheduler import parse_cron_expr

    m, _, _, _, _ = parse_cron_expr("*/15 * * * *")
    assert m == [0, 15, 30, 45]


def test_parse_comma_list_minutes() -> None:
    from engine.report_scheduler import parse_cron_expr

    m, h, _, _, _ = parse_cron_expr("5,25,45 9,17 * * *")
    assert m == [5, 25, 45]
    assert h == [9, 17]


def test_parse_specific_dow_monday() -> None:
    """Cron day-of-week is 0=Sunday; 1=Monday."""
    from engine.report_scheduler import parse_cron_expr

    _, _, _, _, dow = parse_cron_expr("0 9 * * 1")
    assert dow == [1]


# ─── parse_cron_expr — rejections ─────────────────────────────────────────

def test_parse_rejects_empty() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError):
        parse_cron_expr("")


def test_parse_rejects_wrong_arity_too_few() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="5 fields"):
        parse_cron_expr("0 9 *")


def test_parse_rejects_wrong_arity_too_many() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="5 fields"):
        parse_cron_expr("0 9 * * * extra-field")


def test_parse_rejects_out_of_range_minute() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="minute"):
        parse_cron_expr("60 * * * *")


def test_parse_rejects_out_of_range_hour() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="hour"):
        parse_cron_expr("0 24 * * *")


def test_parse_rejects_out_of_range_dom() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="day_of_month"):
        parse_cron_expr("0 0 32 * *")


def test_parse_rejects_range_syntax() -> None:
    """Range syntax (``1-5``) is documented as unsupported; the parser
    must reject loud instead of silently truncating."""
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="range"):
        parse_cron_expr("0 9 * * 1-5")


def test_parse_rejects_non_int_literal() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="not an int"):
        parse_cron_expr("0 abc * * *")


def test_parse_rejects_zero_step() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError, match="positive"):
        parse_cron_expr("*/0 * * * *")


def test_parse_rejects_non_string() -> None:
    from engine.report_scheduler import parse_cron_expr

    with pytest.raises(ValueError):
        parse_cron_expr(None)  # type: ignore[arg-type]


# ─── validate_cron_expr ────────────────────────────────────────────────────

def test_validate_returns_true_for_valid() -> None:
    from engine.report_scheduler import validate_cron_expr

    assert validate_cron_expr("0 9 * * *") == (True, "")


def test_validate_returns_false_for_invalid() -> None:
    from engine.report_scheduler import validate_cron_expr

    ok, err = validate_cron_expr("not a cron")
    assert ok is False
    assert err != ""


def test_validate_never_raises_on_garbage_input() -> None:
    """The UI calls validate on every keystroke; any exception escapes
    would red-screen the whole panel."""
    from engine.report_scheduler import validate_cron_expr

    for bad in ("", None, 12345, [], {}, "* * *", "60 * * * *"):
        ok, _ = validate_cron_expr(bad)  # type: ignore[arg-type]
        assert ok is False


# ─── compute_next_run_at ───────────────────────────────────────────────────

def test_next_run_daily_9am_before_threshold() -> None:
    """From 08:00 on Jan 1, the next 09:00 daily fires the same day."""
    from engine.report_scheduler import compute_next_run_at

    base = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    nxt = compute_next_run_at("0 9 * * *", base=base)
    assert nxt == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def test_next_run_daily_9am_after_threshold() -> None:
    """From 09:01 on Jan 1, the next 09:00 daily fires the NEXT day."""
    from engine.report_scheduler import compute_next_run_at

    base = datetime(2026, 1, 1, 9, 1, tzinfo=timezone.utc)
    nxt = compute_next_run_at("0 9 * * *", base=base)
    assert nxt == datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)


def test_next_run_every_15_minutes() -> None:
    """From 09:07, the next */15 fires at 09:15."""
    from engine.report_scheduler import compute_next_run_at

    base = datetime(2026, 1, 1, 9, 7, tzinfo=timezone.utc)
    nxt = compute_next_run_at("*/15 * * * *", base=base)
    assert nxt == datetime(2026, 1, 1, 9, 15, tzinfo=timezone.utc)


def test_next_run_weekly_monday() -> None:
    """``0 9 * * 1`` = Mondays at 9am. From a Friday, the next fire is
    the following Monday."""
    from engine.report_scheduler import compute_next_run_at

    # 2026-01-02 is a Friday.
    friday_noon = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    nxt = compute_next_run_at("0 9 * * 1", base=friday_noon)
    # Next Monday should be 2026-01-05.
    assert nxt == datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    # And it must indeed land on a Monday (isoweekday=1).
    assert nxt.isoweekday() == 1


def test_next_run_strictly_after_base() -> None:
    """Even when base lands EXACTLY on a match, the returned time must
    be strictly after — otherwise the worker would re-fire forever."""
    from engine.report_scheduler import compute_next_run_at

    base = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    nxt = compute_next_run_at("0 9 * * *", base=base)
    assert nxt > base
    assert nxt == datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)


def test_next_run_naive_base_treated_as_utc() -> None:
    """A naive datetime is assumed UTC; the result is always tz-aware."""
    from engine.report_scheduler import compute_next_run_at

    naive = datetime(2026, 1, 1, 8, 0)
    nxt = compute_next_run_at("0 9 * * *", base=naive)
    assert nxt.tzinfo is not None
    assert nxt == datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


# ─── save_schedule + load_schedules round-trip ────────────────────────────

def _mk(schedule_id: str = "s1", user_id: str = "alice",
        name: str = "Morning Macro", cron_expr: str = "0 9 * * *",
        enabled: bool = True):
    from engine.report_scheduler import ReportSchedule
    return ReportSchedule(
        schedule_id=schedule_id,
        user_id=user_id,
        name=name,
        cron_expr=cron_expr,
        enabled=enabled,
    )


def test_save_and_load_round_trip() -> None:
    from engine.report_scheduler import load_schedules, save_schedule

    assert save_schedule(_mk("s1")) is True

    loaded = load_schedules(user_id="alice")
    assert len(loaded) == 1
    assert loaded[0].schedule_id == "s1"
    assert loaded[0].name == "Morning Macro"
    assert loaded[0].cron_expr == "0 9 * * *"
    assert loaded[0].enabled is True
    # save_schedule computes next_run_at on every save.
    assert loaded[0].next_run_at is not None and loaded[0].next_run_at != ""


def test_save_refuses_invalid_cron() -> None:
    from engine.report_scheduler import load_schedules, save_schedule

    bad = _mk("s-bad", cron_expr="not a cron")
    assert save_schedule(bad) is False
    assert load_schedules(user_id="alice") == []


def test_save_refuses_empty_schedule_id() -> None:
    from engine.report_scheduler import save_schedule

    bad = _mk(schedule_id="")
    assert save_schedule(bad) is False


def test_save_preserves_created_at_on_resave() -> None:
    """Re-saving a row with a new name keeps the original created_at —
    it is a row-age field, not an updated-at one."""
    from engine.report_scheduler import (
        get_schedule, save_schedule,
    )

    sched = _mk("s1")
    assert save_schedule(sched) is True
    first = get_schedule("s1", user_id="alice")
    assert first is not None
    original_created = first.created_at

    sched.name = "Evening Macro"
    assert save_schedule(sched) is True
    second = get_schedule("s1", user_id="alice")
    assert second is not None
    assert second.name == "Evening Macro"
    assert second.created_at == original_created


# ─── delete_schedule per-user scope ───────────────────────────────────────

def test_delete_returns_true_for_owner() -> None:
    from engine.report_scheduler import (
        delete_schedule, load_schedules, save_schedule,
    )

    assert save_schedule(_mk("s1", user_id="alice")) is True
    assert delete_schedule("s1", user_id="alice") is True
    assert load_schedules(user_id="alice") == []


def test_delete_returns_false_for_unknown() -> None:
    from engine.report_scheduler import delete_schedule

    assert delete_schedule("does-not-exist", user_id="alice") is False


def test_delete_is_user_scoped() -> None:
    """Alice cannot delete Bob's schedule — the scope filter blocks it.

    Cross-user delete returns False; Bob's row survives unchanged.
    """
    from engine.report_scheduler import (
        delete_schedule, load_schedules, save_schedule,
    )

    assert save_schedule(_mk("bob-s1", user_id="bob")) is True
    # Alice tries to nuke Bob's schedule.
    assert delete_schedule("bob-s1", user_id="alice") is False
    bobs = load_schedules(user_id="bob")
    assert len(bobs) == 1
    assert bobs[0].schedule_id == "bob-s1"


# ─── get_due_schedules ─────────────────────────────────────────────────────

def test_get_due_returns_only_past_enabled() -> None:
    """A schedule whose next_run_at is in the past AND enabled=1 is
    due; a future one and a disabled one are not."""
    from engine.report_scheduler import (
        get_due_schedules, save_schedule,
    )

    # Past — save first, then manually rewind next_run_at via the DB
    # because save_schedule always computes forward.
    sched_past = _mk("past", cron_expr="*/15 * * * *")
    assert save_schedule(sched_past) is True

    from state.db import get_connection
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE report_schedules SET next_run_at = ? WHERE schedule_id = 'past'",
            (past_iso,),
        )

    # Future
    assert save_schedule(_mk("future", cron_expr="0 9 1 1 *")) is True

    due = get_due_schedules()
    due_ids = {s.schedule_id for s in due}
    assert "past" in due_ids
    assert "future" not in due_ids


def test_get_due_ignores_disabled_even_if_past() -> None:
    """A disabled schedule must not appear in the due list even when
    its next_run_at has already passed — otherwise an operator's
    'temporarily off' would still trigger fires."""
    from engine.report_scheduler import get_due_schedules, save_schedule

    sched = _mk("disabled-past", enabled=False)
    assert save_schedule(sched) is True

    from state.db import get_connection
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE report_schedules SET next_run_at = ? WHERE schedule_id = 'disabled-past'",
            (past_iso,),
        )

    due = get_due_schedules()
    assert all(s.schedule_id != "disabled-past" for s in due)


def test_get_due_with_explicit_now() -> None:
    """Passing an explicit ``now`` lets tests be deterministic — the
    helper compares against the value passed in, not wall clock."""
    from engine.report_scheduler import get_due_schedules, save_schedule

    sched = _mk("s1")
    assert save_schedule(sched) is True
    # save_schedule will have computed next_run_at into the future
    # relative to "now". Pass a 'now' a year in the future to make it due.
    future_now = datetime.now(timezone.utc) + timedelta(days=365)
    due = get_due_schedules(now=future_now)
    assert any(s.schedule_id == "s1" for s in due)


# ─── run_report_scheduler_job ──────────────────────────────────────────────

def _force_past_due(schedule_id: str) -> None:
    """Rewind a schedule's next_run_at into the past so the worker
    treats it as due on the next tick."""
    from state.db import get_connection

    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE report_schedules SET next_run_at = ? WHERE schedule_id = ?",
            (past_iso, schedule_id),
        )


def test_run_job_fires_due_and_updates_last_run() -> None:
    """A due schedule produces a fire; on success last_run_status='ok'
    and next_run_at is advanced into the future."""
    from worker import scheduler as worker_sched
    from engine.report_scheduler import get_schedule, save_schedule

    assert save_schedule(_mk("s1")) is True
    _force_past_due("s1")

    # Patch out the data bundle + briefing job so we never touch real
    # data feeds. The job result is what the worker would build on a
    # successful run.
    fake_result = worker_sched.ReportJobResult(
        report_id="rid-1",
        file_path="/tmp/fake.html",
        success=True,
        duration_s=0.01,
        error_msg="",
    )
    with patch.object(worker_sched, "load_data_bundle", return_value={}), \
         patch.object(worker_sched, "run_daily_briefing_job", return_value=fake_result):
        summary = worker_sched.run_report_scheduler_job()

    assert summary == {"fired": 1, "succeeded": 1, "failed": 0}
    after = get_schedule("s1", user_id="alice")
    assert after is not None
    assert after.last_run_status == "ok"
    assert (after.last_run_message or "") == ""
    # next_run_at must have been bumped forward.
    assert after.next_run_at is not None
    assert after.next_run_at > datetime.now(timezone.utc).isoformat()


def test_run_job_marks_error_but_still_bumps_next_run_at() -> None:
    """A generator error must NOT stick the schedule — last_run_status
    flips to 'error' AND next_run_at advances so the worker doesn't
    re-fire the same broken schedule every tick."""
    from worker import scheduler as worker_sched
    from engine.report_scheduler import get_schedule, save_schedule

    assert save_schedule(_mk("s2")) is True
    _force_past_due("s2")

    fake_result = worker_sched.ReportJobResult(
        report_id="",
        file_path="",
        success=False,
        duration_s=0.01,
        error_msg="engine blew up",
    )
    with patch.object(worker_sched, "load_data_bundle", return_value={}), \
         patch.object(worker_sched, "run_daily_briefing_job", return_value=fake_result):
        summary = worker_sched.run_report_scheduler_job()

    assert summary == {"fired": 1, "succeeded": 0, "failed": 1}
    after = get_schedule("s2", user_id="alice")
    assert after is not None
    assert after.last_run_status == "error"
    assert "engine blew up" in (after.last_run_message or "")
    # The defining property — broken schedules MUST advance.
    assert after.next_run_at is not None
    assert after.next_run_at > datetime.now(timezone.utc).isoformat()


def test_run_job_returns_empty_summary_when_nothing_due() -> None:
    """No due schedules → zeroes across the board, no exception, no
    bundle load."""
    from worker import scheduler as worker_sched

    # Sentinel — if the helper calls load_data_bundle when nothing is
    # due we want the test to fail loudly. side_effect=AssertionError
    # makes any call blow the test.
    with patch.object(worker_sched, "load_data_bundle", side_effect=AssertionError("should not run")):
        summary = worker_sched.run_report_scheduler_job()
    assert summary == {"fired": 0, "succeeded": 0, "failed": 0}


def test_run_job_never_raises_even_when_briefing_raises() -> None:
    """A briefing-job that raises (instead of returning a failing
    ReportJobResult) must NOT take down the worker — the per-schedule
    loop catches and converts to an error result."""
    from worker import scheduler as worker_sched
    from engine.report_scheduler import get_schedule, save_schedule

    assert save_schedule(_mk("s3")) is True
    _force_past_due("s3")

    with patch.object(worker_sched, "load_data_bundle", return_value={}), \
         patch.object(worker_sched, "run_daily_briefing_job",
                      side_effect=RuntimeError("boom")):
        # The contract is "never raises"; if this call raises, the
        # test fails immediately rather than via assertion below.
        summary = worker_sched.run_report_scheduler_job()

    assert summary["fired"] == 1
    assert summary["failed"] == 1
    after = get_schedule("s3", user_id="alice")
    assert after is not None
    assert after.last_run_status == "error"
    assert "boom" in (after.last_run_message or "")
