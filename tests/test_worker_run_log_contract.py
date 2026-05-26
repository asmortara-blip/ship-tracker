"""Spec tests for the @_track_run -> state.worker_runs contract.

These tests pin the contract that ``worker/scheduler.py::_track_run``
relies on. Downstream consumers — the operator-telemetry tab, the
end-to-end worker tick test, the per-job dashboard rows — all read the
records the decorator writes. A silent contract change here would break
those callers without obvious symptoms.

Each test documents one promise:

  * Each invocation writes exactly one record.
  * The record carries the documented fields:
      job_name, started_at (ISO), finished_at (ISO), status,
      error_message (str|None), duration_seconds (float).
  * status='ok' on a clean return, 'error' on an exception.
  * A dict return value round-trips through ``result_json`` so
    downstream consumers can re-parse it (counts dict serializes
    losslessly).
  * Records are queryable by ``job_name`` (exact match) and the read
    side orders **newest-first** by ``started_at``.
  * Recording is best-effort: a broken state layer never raises out of
    the decorator.

Storage is the rolling list under ``kv_state['worker_runs']`` (see
``state/worker_runs.py``). Tests isolate it with the standard
DB_PATH monkeypatch + reset_for_tests fixture so we never touch
``cache/ship_tracker.db``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from state.worker_runs import (
    WorkerJobRun,
    list_recent_runs,
    record_run,
)


# ─── Test isolation ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Each test gets a fresh kv_state DB so no shared state bleeds across.

    Mirrors the pattern in tests/test_worker_runs.py + tests/test_tab_worker_health.py.
    """
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_track_run_call(job_fn, *args, **kwargs):
    """Invoke ``job_fn`` through a fresh _track_run wrap.

    Mirrors the exact decorator from worker/scheduler.py — we re-build
    the wrapper here so the contract test is decoupled from the
    scheduler module (it imports tons of optional dependencies that
    would slow this file's collection). The wrapper body is copied
    verbatim from worker/scheduler.py::_track_run.
    """
    import functools
    from typing import Any, Optional
    from loguru import logger

    @functools.wraps(job_fn)
    def _wrapped(*a, **kw):
        started_at = datetime.now(timezone.utc).isoformat()
        result: Any = None
        error_message: Optional[str] = None
        status = "ok"
        try:
            result = job_fn(*a, **kw)
            return result
        except Exception as exc:
            status = "error"
            error_message = str(exc)
            raise
        finally:
            finished_at = datetime.now(timezone.utc).isoformat()
            try:
                from state.worker_runs import record_run as _rr
                _rr(
                    job_fn.__name__,
                    started_at=started_at,
                    finished_at=finished_at,
                    status=status,
                    result=result,
                    error_message=error_message,
                )
            except Exception as record_exc:
                logger.debug(
                    f"_track_run: record_run failed for {job_fn.__name__}: "
                    f"{record_exc}"
                )

    return _wrapped(*args, **kwargs)


# ─── CONTRACT: one invocation -> one record ─────────────────────────────────


def test_single_invocation_writes_exactly_one_record():
    """Each call to a wrapped job appends one and only one row."""

    def run_demo_job() -> dict:
        return {"deleted": 3}

    assert list_recent_runs() == []
    _make_track_run_call(run_demo_job)
    rows = list_recent_runs()
    assert len(rows) == 1
    assert rows[0].job_name == "run_demo_job"


def test_repeated_invocations_accumulate_records():
    """N invocations -> N records (no dedupe, no merging)."""

    def run_demo_job() -> dict:
        return {"x": 1}

    for _ in range(4):
        _make_track_run_call(run_demo_job)
    assert len(list_recent_runs(job_name="run_demo_job")) == 4


# ─── CONTRACT: record carries the documented fields ─────────────────────────


def test_ok_record_carries_all_required_fields():
    """A successful invocation has every documented field populated."""

    def run_demo_job() -> dict:
        return {"deleted": 7}

    _make_track_run_call(run_demo_job)
    rows = list_recent_runs(job_name="run_demo_job")
    assert len(rows) == 1
    r = rows[0]
    assert isinstance(r, WorkerJobRun)
    # job_name == fn.__name__
    assert r.job_name == "run_demo_job"
    # ISO timestamps round-trip
    assert datetime.fromisoformat(r.started_at).tzinfo is not None
    assert datetime.fromisoformat(r.finished_at).tzinfo is not None
    # status == ok, no error_message
    assert r.status == "ok"
    assert r.error_message is None
    # duration_seconds is a non-negative float
    assert isinstance(r.duration_seconds, float)
    assert r.duration_seconds >= 0.0
    # run_id is a non-empty string (uuid)
    assert isinstance(r.run_id, str) and r.run_id


def test_error_record_carries_error_message_and_status_error():
    """An exception inside the job sets status='error' + error_message."""

    def run_broken_job() -> None:
        raise RuntimeError("synthetic failure for the contract test")

    with pytest.raises(RuntimeError):
        _make_track_run_call(run_broken_job)

    rows = list_recent_runs(job_name="run_broken_job")
    assert len(rows) == 1
    r = rows[0]
    assert r.status == "error"
    assert r.error_message is not None
    assert "synthetic failure for the contract test" in r.error_message
    # Still has timestamps (finished_at lands in the finally block).
    assert r.started_at and r.finished_at
    assert r.duration_seconds >= 0.0


def test_ok_record_has_none_error_message():
    """A clean run never carries a non-None error_message."""

    def run_demo_job() -> int:
        return 42

    _make_track_run_call(run_demo_job)
    rows = list_recent_runs(job_name="run_demo_job")
    assert rows[0].error_message is None


# ─── CONTRACT: result dict is serialized into result_json ───────────────────


def test_dict_return_value_round_trips_through_result_json():
    """A count-dict return value re-parses cleanly from result_json.

    The operator dashboard renders these counts inline; if the
    serialization shape changes, the dashboard rendering breaks.
    """

    def run_count_job() -> dict:
        return {"fired": 3, "skipped_cooldown": 1, "errored": 0}

    _make_track_run_call(run_count_job)
    rows = list_recent_runs(job_name="run_count_job")
    assert len(rows) == 1
    parsed = json.loads(rows[0].result_json)
    assert parsed == {"fired": 3, "skipped_cooldown": 1, "errored": 0}


def test_int_return_value_round_trips_through_result_json():
    """An int return value (e.g. prune-job's `deleted` count) round-trips."""

    def run_prune_job() -> int:
        return 17

    _make_track_run_call(run_prune_job)
    rows = list_recent_runs(job_name="run_prune_job")
    parsed = json.loads(rows[0].result_json)
    assert parsed == 17


def test_list_return_value_round_trips_through_result_json():
    """A list return value (e.g. run_health_ping_job) persists as a list.

    Lists of dataclasses get the json.dumps(default=str) coercion since
    HealthPing is not JSON-serializable; the resulting record_json stays
    parseable as a JSON array so the dashboard can show the length.
    """

    def run_listy_job() -> list:
        return [1, 2, 3]

    _make_track_run_call(run_listy_job)
    rows = list_recent_runs(job_name="run_listy_job")
    parsed = json.loads(rows[0].result_json)
    assert parsed == [1, 2, 3]


def test_non_serializable_return_value_still_persists():
    """An exotic return type still produces a parseable result_json.

    The contract: persistence is never blocked by an unencodable return.
    Either the value coerces via default=str OR it lands in the
    _unencodable envelope. Both forms are acceptable.
    """

    class _Exotic:
        def __repr__(self) -> str:
            return "<exotic-sentinel>"

    def run_weird_job() -> _Exotic:
        return _Exotic()

    _make_track_run_call(run_weird_job)
    rows = list_recent_runs(job_name="run_weird_job")
    assert len(rows) == 1
    assert "exotic-sentinel" in rows[0].result_json


# ─── CONTRACT: read side is queryable by job_name + ordered newest-first ────


def test_records_queryable_by_job_name_exact_match():
    """list_recent_runs(job_name=X) returns only X's runs."""

    def run_alpha_job() -> dict:
        return {"k": "a"}

    def run_beta_job() -> dict:
        return {"k": "b"}

    _make_track_run_call(run_alpha_job)
    _make_track_run_call(run_beta_job)
    _make_track_run_call(run_alpha_job)

    alpha_rows = list_recent_runs(job_name="run_alpha_job")
    assert len(alpha_rows) == 2
    assert all(r.job_name == "run_alpha_job" for r in alpha_rows)

    beta_rows = list_recent_runs(job_name="run_beta_job")
    assert len(beta_rows) == 1
    assert beta_rows[0].job_name == "run_beta_job"


def test_records_returned_newest_first_by_started_at():
    """The read side pins ordering: newest started_at first.

    PINNED in this test so other agents writing dashboards / queries
    can rely on the ordering without re-sorting.
    """
    # Record three runs at slightly different started_at values by
    # pacing the calls. The decorator captures started_at at entry, so
    # the simplest way to spread them is to invoke them sequentially.
    def run_seq_job() -> int:
        return 1

    _make_track_run_call(run_seq_job)
    _make_track_run_call(run_seq_job)
    _make_track_run_call(run_seq_job)

    rows = list_recent_runs(job_name="run_seq_job")
    assert len(rows) == 3
    times = [r.started_at for r in rows]
    # Newest-first: descending sort by started_at.
    assert times == sorted(times, reverse=True), (
        "list_recent_runs must return newest-first by started_at"
    )


# ─── CONTRACT: defensive — recording never blocks the worker ────────────────


def test_recording_failure_does_not_propagate_out_of_decorator(monkeypatch):
    """A broken record_run must never take down the wrapped job.

    The decorator catches exceptions from record_run in its own
    try/except and only debug-logs them. The wrapped job's return
    value is unchanged.
    """
    # Monkeypatch record_run to blow up.
    import state.worker_runs as wr_module

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated state-layer failure")

    monkeypatch.setattr(wr_module, "record_run", _explode)

    def run_demo_job() -> str:
        return "still-returns"

    # The wrapped call must still return the job's value, with no row
    # persisted (the recorder blew up). No exception should propagate.
    result = _make_track_run_call(run_demo_job)
    assert result == "still-returns"
    # No row landed (the recorder was broken).
    assert list_recent_runs(job_name="run_demo_job") == []


def test_record_run_returns_bool_not_raises():
    """Direct record_run with bad input returns False, never raises."""
    # Empty job_name -> False.
    assert record_run(
        "",
        started_at=_now_iso(),
        finished_at=_now_iso(),
        status="ok",
    ) is False
