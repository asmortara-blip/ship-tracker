"""Tests for state.worker_runs — per-job execution telemetry.

Storage is a single rolling list under kv_state['worker_runs'], capped at
200 most-recent runs. Defending the contract:

  * Persistence: round-trips every field including None-able error_message.
  * Ordering: list_recent_runs returns descending by started_at.
  * Filtering: job_name filter is exact-match.
  * Rolling cap: 201st run drops the oldest.
  * Coercion: non-JSON-serializable result still persists with a _repr
    fallback rather than failing the whole record.
  * Summary: summarize_jobs covers every KNOWN_JOBS entry even when
    never-run; success_rate_24h math matches a hand-calculated value.
  * Defensive: NEVER raises on bad input.
  * Per-test isolation via DB_PATH monkeypatching.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from state.worker_runs import (
    KNOWN_JOBS,
    WorkerJobRun,
    clear_all_runs,
    get_last_run,
    list_recent_runs,
    record_run,
    summarize_jobs,
)


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Each test gets a fresh kv_state DB so no shared state bleeds across."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ────────────────────────────────────────────────────────────────


def _iso(offset_seconds: int = 0) -> str:
    """ISO timestamp now + offset, UTC."""
    return (datetime.now(timezone.utc)
            + timedelta(seconds=offset_seconds)).isoformat()


def _record_simple(
    job_name: str,
    *,
    offset: int = 0,
    duration_s: int = 1,
    status: str = "ok",
    result=None,
    error_message=None,
) -> None:
    """Convenience wrapper — records a run with sensible defaults."""
    start = _iso(offset_seconds=offset)
    finish = _iso(offset_seconds=offset + duration_s)
    record_run(
        job_name,
        started_at=start,
        finished_at=finish,
        status=status,
        result=result if result is not None else {"deleted": 5},
        error_message=error_message,
    )


# ─── record_run — round-trip ────────────────────────────────────────────────


def test_record_run_persists_all_fields() -> None:
    """A single record_run is round-trippable with every field intact."""
    start = _iso(-10)
    finish = _iso(0)
    ok = record_run(
        "run_alert_prune_job",
        started_at=start,
        finished_at=finish,
        status="ok",
        result={"deleted": 7, "retention_days": 180},
        error_message=None,
    )
    assert ok is True

    runs = list_recent_runs()
    assert len(runs) == 1
    r = runs[0]
    assert isinstance(r, WorkerJobRun)
    assert r.job_name == "run_alert_prune_job"
    assert r.status == "ok"
    assert r.started_at == start
    assert r.finished_at == finish
    assert r.error_message is None
    assert r.duration_seconds == pytest.approx(10.0, abs=0.5)
    # result_json round-trips into a parseable dict.
    parsed = json.loads(r.result_json)
    assert parsed == {"deleted": 7, "retention_days": 180}


def test_record_run_error_status_carries_message() -> None:
    """A status='error' row preserves the error_message verbatim."""
    record_run(
        "run_health_ping_job",
        started_at=_iso(-1),
        finished_at=_iso(0),
        status="error",
        result=None,
        error_message="ConnectionError: FRED 503",
    )
    runs = list_recent_runs()
    assert len(runs) == 1
    assert runs[0].status == "error"
    assert runs[0].error_message == "ConnectionError: FRED 503"


# ─── list_recent_runs — ordering, limit, filter ─────────────────────────────


def test_list_recent_runs_descending_by_started_at() -> None:
    """Most-recent run is first regardless of insertion order."""
    # Insert oldest first, then newest, then middle — the read-side sort
    # must still put newest first.
    _record_simple("run_a", offset=-100)
    _record_simple("run_a", offset=-10)
    _record_simple("run_a", offset=-50)

    runs = list_recent_runs(job_name="run_a")
    assert len(runs) == 3
    times = [r.started_at for r in runs]
    assert times == sorted(times, reverse=True)


def test_list_recent_runs_limit_caps_results() -> None:
    """The limit kwarg caps the result count."""
    for i in range(10):
        _record_simple("run_x", offset=-i)

    assert len(list_recent_runs(limit=3)) == 3
    assert len(list_recent_runs(limit=10)) == 10
    # limit > total returns total
    assert len(list_recent_runs(limit=999)) == 10


def test_list_recent_runs_filter_by_job_name() -> None:
    """Only the matching job is returned."""
    _record_simple("run_a", offset=-10)
    _record_simple("run_b", offset=-5)
    _record_simple("run_a", offset=-1)

    a_only = list_recent_runs(job_name="run_a")
    assert len(a_only) == 2
    assert all(r.job_name == "run_a" for r in a_only)

    b_only = list_recent_runs(job_name="run_b")
    assert len(b_only) == 1
    assert b_only[0].job_name == "run_b"

    # Unknown job — empty result, not an error.
    assert list_recent_runs(job_name="does_not_exist") == []


# ─── get_last_run ───────────────────────────────────────────────────────────


def test_get_last_run_returns_most_recent_for_job() -> None:
    """The newest run for a given job, regardless of other jobs' runs."""
    _record_simple("run_alpha", offset=-100, result={"step": 1})
    _record_simple("run_beta", offset=-50, result={"step": 99})
    _record_simple("run_alpha", offset=-10, result={"step": 2})

    last = get_last_run("run_alpha")
    assert last is not None
    assert last.job_name == "run_alpha"
    parsed = json.loads(last.result_json)
    assert parsed["step"] == 2  # the newest, not the oldest


def test_get_last_run_returns_none_for_never_run_job() -> None:
    """A job that has never been recorded returns None, not an error."""
    assert get_last_run("never_seen_job") is None
    assert get_last_run("") is None


# ─── summarize_jobs ─────────────────────────────────────────────────────────


def test_summarize_jobs_includes_never_run_jobs() -> None:
    """Every KNOWN_JOBS entry appears in the summary even with zero runs.

    The dashboard must surface "this prune never ran" loud and clear,
    not silently omit the row.
    """
    summary = summarize_jobs()
    job_names = {row["job_name"] for row in summary}
    for known in KNOWN_JOBS:
        assert known in job_names

    # And every never-run row carries the sentinel last_status='NEVER'.
    for row in summary:
        if row["job_name"] in KNOWN_JOBS:
            assert row["last_status"] == "NEVER"
            assert row["last_run_at"] == ""
            assert row["runs_in_window"] == 0


def test_summarize_jobs_success_rate_math() -> None:
    """A job with 3 ok + 1 error in the 24h window reports 0.75 success."""
    name = "run_alert_prune_job"
    # 4 runs in the last hour — three ok, one error.
    _record_simple(name, offset=-300, status="ok")
    _record_simple(name, offset=-200, status="ok")
    _record_simple(name, offset=-100, status="error",
                   error_message="boom")
    _record_simple(name, offset=-10, status="ok")

    summary = summarize_jobs()
    row = next(r for r in summary if r["job_name"] == name)
    assert row["runs_in_window"] == 4
    assert row["success_rate_24h"] == pytest.approx(0.75, abs=1e-9)
    # The last-status is the most-recent run — 'ok', not 'error'.
    assert row["last_status"] == "ok"
    # Result summary is a non-empty one-liner from the most-recent result.
    assert row["last_result_summary"]


def test_summarize_jobs_result_summary_is_kv_one_liner() -> None:
    """A dict result renders as 'k=v · k=v' for the dashboard table."""
    name = "run_perf_budget_check_job"
    _record_simple(name, offset=-5,
                   result={"checked": 12, "breached": 0, "alerted": 0})
    summary = summarize_jobs()
    row = next(r for r in summary if r["job_name"] == name)
    summary_text = row["last_result_summary"]
    assert "checked=12" in summary_text
    assert "breached=0" in summary_text


# ─── Defensive contracts ────────────────────────────────────────────────────


def test_record_run_never_raises_on_bad_input() -> None:
    """Bad input degrades to False, never an exception."""
    # All of these are nonsense — none should raise.
    assert record_run("", started_at="", finished_at="", status="ok") is False
    assert record_run(None, started_at="", finished_at="", status="ok") is False  # type: ignore[arg-type]
    assert record_run("ok_job", started_at="not-a-date",
                      finished_at="also-not", status="ok") is True
    # Even an exotic result must not raise.
    class _Weird:
        pass
    weird = _Weird()
    assert record_run("ok_job2", started_at=_iso(-1), finished_at=_iso(0),
                      status="ok", result=weird) is True
    # The persisted row exists and the result was coerced to a string
    # via ``json.dumps(default=str)``. Either form is acceptable — the
    # contract is "never lost, never raised".
    runs = list_recent_runs(job_name="ok_job2")
    assert len(runs) == 1
    parsed = json.loads(runs[0].result_json)
    # Coercion either preserved the repr as a plain string, or wrapped
    # it in the _unencodable envelope — both are non-empty payloads.
    if isinstance(parsed, dict):
        assert parsed.get("_unencodable") is True
    else:
        assert isinstance(parsed, str) and "_Weird" in parsed


def test_rolling_cap_drops_oldest_runs() -> None:
    """201st run evicts the oldest — never more than _MAX_RUNS persisted."""
    from state.worker_runs import _MAX_RUNS

    # Record _MAX_RUNS + 5 — only _MAX_RUNS should remain.
    for i in range(_MAX_RUNS + 5):
        _record_simple("run_cap", offset=-(_MAX_RUNS + 5 - i))

    runs = list_recent_runs(limit=_MAX_RUNS + 100, job_name="run_cap")
    assert len(runs) == _MAX_RUNS


def test_non_serializable_result_coerced_via_default_str() -> None:
    """A non-JSON-serializable result coerces via json.dumps(default=str).

    The coercion path is the same one ``json.dumps`` uses for unknown
    types — the result is preserved as the str() of the object so the
    dashboard shows something meaningful instead of dropping the run.
    """
    class _CustomRepr:
        def __repr__(self) -> str:
            return "<custom-repr-marker>"

    record_run(
        "run_health_ping_job",
        started_at=_iso(-1),
        finished_at=_iso(0),
        status="ok",
        result=_CustomRepr(),
    )
    runs = list_recent_runs(job_name="run_health_ping_job")
    assert len(runs) == 1
    # The persisted result_json is non-empty and contains either the
    # raw repr OR the _unencodable envelope. Both are valid coercions.
    assert "custom-repr-marker" in runs[0].result_json


def test_clear_all_runs_resets_storage() -> None:
    """The test-isolation helper actually drops every row."""
    _record_simple("x", offset=-1)
    _record_simple("y", offset=-2)
    assert len(list_recent_runs()) == 2
    clear_all_runs()
    assert list_recent_runs() == []


def test_per_test_isolation_starts_empty() -> None:
    """Each test starts with no persisted rows (fixture-driven)."""
    # No record_run has been called in this test.
    assert list_recent_runs() == []
    # And summarize_jobs returns the NEVER rows for every known job.
    summary = summarize_jobs()
    assert all(r["last_status"] == "NEVER"
               for r in summary if r["job_name"] in KNOWN_JOBS)
