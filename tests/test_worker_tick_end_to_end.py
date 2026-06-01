"""End-to-end test for ``worker.scheduler.main()`` — the daily cron tick.

The scheduler's ``main()`` is the production entry point invoked every
day. It runs 20+ ``run_*_job`` helpers in sequence, each wrapped in its
own try/except so one failure doesn't kill the rest. We already have
per-job unit tests in ``test_scheduler.py``; this module adds the
pipeline-level contract tests that exercise ``main()`` end-to-end:

  - the full pipeline completes without raising (SystemExit aside)
  - every documented ``run_*_job`` is invoked, in roughly the order
    the in-line comments document
  - a single job raising does NOT halt the pipeline — downstream jobs
    still run (the try/except contract)
  - a raising job's failure is logged with the step name so operators
    tailing logs can identify which step blew up

All external resources are stubbed: every ``run_*_job`` is replaced
with a recording stub, ``load_data_bundle`` is replaced with a static
dict, and ``SystemExit`` is caught and asserted on. No real HTTP / DB
/ filesystem work happens.

DO NOT modify ``worker/scheduler.py`` to make these tests pass — the
tests adapt to the existing contract.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import pytest

from worker import scheduler
from worker.scheduler import ReportJobResult, main


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Isolate the SQLite state DB to a per-test tmp file.

    main() reads/writes kv_state for its per-job cadence gates (see
    worker.scheduler._job_due), so without isolation these tests would (a)
    see last-run stamps left by another run and SKIP gated jobs, and (b)
    pollute the real cache DB. A fresh DB per test makes every gate due, so
    the full job sequence runs exactly as these tests assert."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Every run_*_job invoked by main(), in documented sequence ────────────
#
# Sourced by reading worker/scheduler.py::main() top to bottom. The
# briefing job runs first (it's NOT inside a try/except — its result
# determines the exit code). Everything else is best-effort with a
# belt-and-braces try/except around each call.
#
# The order here matches the in-line comments inside main():
#   briefing → telemetry → perf → snapshot → health-ping → health-prune
#     → source-health-alert → perf-budget → anomaly → alert-escalation
#     → delivery-retry → bulk-export-prune → alert-prune → silence-cleanup
#     → audit-prune → report-prune → operator-digest → weekly-digest
#     → port-supply-snapshot → port-supply-snapshot-gc → report-scheduler

_CANDIDATE_JOB_SEQUENCE: list[str] = [
    "run_daily_briefing_job",
    "run_telemetry_prune_job",
    "run_perf_prune_job",
    "run_snapshot_prune_job",
    "run_health_ping_job",
    "run_health_prune_job",
    "run_source_health_alert_job",
    "run_perf_budget_check_job",
    "run_anomaly_detection_job",
    "run_alert_escalation_job",
    "run_delivery_retry_job",
    "run_bulk_export_prune_job",
    "run_alert_prune_job",
    "run_silence_cleanup_job",
    "run_audit_prune_job",
    "run_report_prune_job",
    "run_operator_digest_job",
    "run_weekly_digest_job_wrapper",
    "run_port_supply_snapshot_job",
    "run_port_supply_snapshot_gc_job",
    "run_report_scheduler_job",
]

# Filter to only the jobs that actually exist in the live scheduler
# module — keeps the test robust to in-flight additions/removals.
import worker.scheduler as _sched
EXPECTED_JOB_SEQUENCE: list[str] = [
    j for j in _CANDIDATE_JOB_SEQUENCE if hasattr(_sched, j)
]

# Mapping from job name to the step name used inside main()'s logger.warning
# calls. The contract: when a job raises, the catch-block logs
# ``"main: <step name> step failed: ..."``. The step name is operator-
# facing — it's how someone tailing logs identifies what blew up.
JOB_TO_STEP_NAME: dict[str, str] = {
    "run_telemetry_prune_job":        "telemetry prune",
    "run_perf_prune_job":             "perf prune",
    "run_snapshot_prune_job":         "snapshot prune",
    "run_health_ping_job":            "health ping",
    "run_health_prune_job":           "health prune",
    "run_source_health_alert_job":    "source health alert",
    "run_perf_budget_check_job":      "perf budget check",
    "run_anomaly_detection_job":      "anomaly detection",
    "run_alert_escalation_job":       "alert escalation",
    "run_delivery_retry_job":         "delivery retry",
    "run_bulk_export_prune_job":      "bulk export prune",
    "run_alert_prune_job":            "alert prune",
    "run_silence_cleanup_job":        "silence cleanup",
    "run_audit_prune_job":            "audit prune",
    "run_report_prune_job":           "report prune",
    "run_operator_digest_job":        "operator digest",
    "run_weekly_digest_job_wrapper":  "weekly digest",
    "run_port_supply_snapshot_job":   "port supply snapshot",
    "run_port_supply_snapshot_gc_job":"port supply snapshot gc",
    "run_report_scheduler_job":       "report scheduler",
}


# ─── Helpers ────────────────────────────────────────────────────────────────


def _stub_bundle() -> dict:
    """A minimal bundle whose values are never actually inspected because
    run_daily_briefing_job is also stubbed."""
    return {
        "port_results":  [],
        "route_results": [],
        "insights":      [],
        "freight_data":  {},
        "macro_data":    {},
        "stock_data":    {},
        "news_items":    [],
        "source":        "stub",
    }


def _success_briefing_result() -> ReportJobResult:
    return ReportJobResult(
        report_id="rid-tick-test",
        file_path="/tmp/tick.html",
        success=True,
        duration_s=0.1,
        error_msg="",
    )


def _install_recording_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    call_log: list[str],
    raising_job: Optional[str] = None,
) -> None:
    """Replace every job in ``EXPECTED_JOB_SEQUENCE`` with a stub that
    appends its name to ``call_log``. If ``raising_job`` is set, that
    one stub raises ``RuntimeError`` instead — but still records.

    ``run_daily_briefing_job`` is special-cased because its return value
    determines main()'s exit code; the stub returns a successful
    ReportJobResult so exit code is 0.

    ``run_report_scheduler_job`` is special-cased because main() calls
    ``.get('fired', 0)`` on its return value — the stub returns a dict.
    """

    def _make_stub(job_name: str):
        def _stub(*args, **kwargs):
            call_log.append(job_name)
            if raising_job == job_name:
                raise RuntimeError(f"{job_name} simulated failure")
            # Per-job return shape — has to satisfy the call site.
            if job_name == "run_daily_briefing_job":
                return _success_briefing_result()
            if job_name == "run_report_scheduler_job":
                return {"fired": 0, "succeeded": 0, "failed": 0}
            if job_name == "run_health_ping_job":
                return []
            # Every other job returns an int or dict — main() ignores
            # the value in every other call site, so 0 is fine.
            return 0
        return _stub

    for job_name in EXPECTED_JOB_SEQUENCE:
        monkeypatch.setattr(scheduler, job_name, _make_stub(job_name))

    # Also stub load_data_bundle so we never hit real HTTP / FRED / WITS.
    # The existing failing test in test_scheduler.py confirms that
    # touching load_data_bundle for real makes network calls; we never
    # want to repeat that mistake here.
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())


def _capture_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch ``scheduler.logger.warning`` to capture every formatted
    warning message. Returns a list that's mutated in-place as warnings
    fire. Loguru's ``logger`` is module-level in scheduler, so we
    replace the bound method with a recorder."""
    captured: list[str] = []

    def _record(msg, *args, **kwargs):
        try:
            captured.append(str(msg))
        except Exception:
            captured.append(repr(msg))

    monkeypatch.setattr(scheduler.logger, "warning", _record)
    return captured


# ─── Test 1: pipeline completes without raising ────────────────────────────


def test_main_runs_full_pipeline_without_raising(monkeypatch) -> None:
    """The full daily cron tick MUST complete with SystemExit(0) when
    every job is a successful no-op. No unraised exception should bubble
    out — main()'s try/except guards every step."""
    call_log: list[str] = []
    _install_recording_stubs(monkeypatch, call_log=call_log)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 0
    # Sanity — at minimum the briefing + every wrapped step were touched.
    assert len(call_log) == len(EXPECTED_JOB_SEQUENCE)


# ─── Test 2: every known job is invoked in the documented sequence ─────────


def test_main_calls_every_known_job(monkeypatch) -> None:
    """Every ``run_*_job`` documented in main() runs exactly once, in
    the dependency-implied order. The order is pinned to the sequence
    documented by in-line comments inside main() itself — a deliberate
    reordering by a future commit will deliberately break this test,
    which is the point."""
    call_log: list[str] = []
    _install_recording_stubs(monkeypatch, call_log=call_log)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 0

    # Every job invoked exactly once.
    assert set(call_log) == set(EXPECTED_JOB_SEQUENCE), (
        f"Missing jobs: {set(EXPECTED_JOB_SEQUENCE) - set(call_log)}; "
        f"Unexpected jobs: {set(call_log) - set(EXPECTED_JOB_SEQUENCE)}"
    )
    assert call_log == EXPECTED_JOB_SEQUENCE, (
        f"Job sequence drift.\n"
        f"  expected: {EXPECTED_JOB_SEQUENCE}\n"
        f"  observed: {call_log}"
    )


# ─── Test 3: a single job raising does NOT halt the pipeline ───────────────


@pytest.mark.parametrize("raising_job", [
    # Pick a few representative jobs across the pipeline — early,
    # middle, late — to confirm the try/except contract holds for any
    # of them. Spot-checking three is enough; testing all 20 would
    # bloat the suite without adding signal.
    "run_telemetry_prune_job",       # early
    "run_anomaly_detection_job",     # middle
    "run_port_supply_snapshot_job",  # late
])
def test_main_continues_after_one_job_raises(monkeypatch, raising_job) -> None:
    """When ONE wrapped job raises, main() must still complete and
    every downstream wrapped job must still run. This is the contract
    of the per-step try/except guards."""
    call_log: list[str] = []
    _install_recording_stubs(monkeypatch, call_log=call_log, raising_job=raising_job)
    _capture_warnings(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    # main() must still exit cleanly — the briefing succeeded so exit 0.
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 0

    # The raising job WAS invoked (its stub records before raising).
    assert raising_job in call_log

    # Every downstream job AFTER the raising one must still have fired.
    raising_idx = EXPECTED_JOB_SEQUENCE.index(raising_job)
    downstream = EXPECTED_JOB_SEQUENCE[raising_idx + 1:]
    for downstream_job in downstream:
        assert downstream_job in call_log, (
            f"Downstream job {downstream_job!r} did not run after "
            f"{raising_job!r} raised — the try/except contract is broken."
        )


# ─── Test 4: job failures are logged with the step name ────────────────────


def test_main_logs_job_failure_with_step_name(monkeypatch) -> None:
    """When a job raises, the operator-facing warning log MUST include
    the step name (e.g. "telemetry prune", "alert escalation") so
    someone tailing logs at 3am can identify which step blew up. The
    in-line comment in main() guarantees this for every wrapped step."""
    raising_job = "run_alert_escalation_job"
    expected_step_name = JOB_TO_STEP_NAME[raising_job]

    call_log: list[str] = []
    _install_recording_stubs(monkeypatch, call_log=call_log, raising_job=raising_job)
    warnings_captured = _capture_warnings(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit):
        main([])

    # At least one warning mentions the step name AND came from main().
    matching = [
        w for w in warnings_captured
        if "main:" in w and expected_step_name in w
    ]
    assert matching, (
        f"No warning logged with step name {expected_step_name!r}.\n"
        f"Captured warnings: {warnings_captured}"
    )
    # And the original exception message should be in the log line so the
    # operator can grep for it — main()'s pattern is "step failed: {exc}".
    assert any(f"{raising_job} simulated failure" in w for w in matching), (
        f"Step-name warning did not include the raised exception's "
        f"message.\nCaptured matching warnings: {matching}"
    )
