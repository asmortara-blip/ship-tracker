"""Tests for engine.worker_deadman — the worker self-monitor (R117).

Defining properties under test:

  Pure ``assess_worker_health`` (no DB, no clock — fixed now_iso):
    - A stale critical job (last_run_at older than the window) → CRITICAL
      'stale' finding.
    - last_status == 'NEVER' for a critical job → CRITICAL 'stale'.
    - An unparseable last_run_at for a critical job → CRITICAL 'stale'
      (fail-loud: alert rather than silently pass).
    - last_status == 'error' (recent run) for a critical job → HIGH
      'failing'.
    - A low 24h success rate (runs_in_window > 0) → HIGH 'failing'.
    - A healthy recent job → no finding.
    - A NON-critical job that is stale/failing → no finding.
    - A quiet job (runs_in_window == 0, success_rate 1.0) is NOT read as
      failing.
    - Empty summaries → [].
    - A job that is BOTH stale and erroring → a single STALE finding
      (stale is strictly worse).

  Orchestrator ``check_worker_health_and_fire`` (isolated DB):
    - A stale critical job fires >= 1 alert with a sane ShippingAlert
      (alert_type='WORKER_DEADMAN', CRITICAL, job_name in the entity key).
    - A second call within the cooldown window is suppressed
      (skipped_cooldown >= 1, no new fire).
    - A summarize_jobs that raises → errored counter, never raises.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.worker_deadman import (
    DeadmanFinding,
    assess_worker_health,
    check_worker_health_and_fire,
)


# ─── Per-test SQLite isolation ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Fresh DB at tmp_path so no test touches cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

# A fixed reference clock — every pure test pins this so nothing is flaky.
NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = NOW.isoformat()

CRITICAL = {"run_daily_briefing_job", "run_signal_ledger_freeze_job"}


def _summary(
    job_name: str,
    *,
    last_status: str = "ok",
    last_run_at: str = "",
    runs_in_window: int = 1,
    success_rate_24h: float = 1.0,
) -> dict:
    """Build one summarize_jobs-shaped row."""
    return {
        "job_name": job_name,
        "last_status": last_status,
        "last_run_at": last_run_at,
        "last_duration": 1.0,
        "last_result_summary": "",
        "runs_in_window": runs_in_window,
        "success_rate_24h": success_rate_24h,
    }


def _ago(minutes: float) -> str:
    """ISO timestamp `minutes` before the fixed NOW."""
    return (NOW - timedelta(minutes=minutes)).isoformat()


# ─── assess_worker_health — STALE (CRITICAL) ──────────────────────────────

def test_stale_critical_job_is_critical_finding() -> None:
    # 26h old > the default 25h (1500 min) window.
    summaries = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at=_ago(26 * 60)),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.job_name == "run_daily_briefing_job"
    assert f.reason == "stale"
    assert f.severity == "CRITICAL"
    assert f.detail  # non-empty human-readable detail


def test_never_run_critical_job_is_stale() -> None:
    summaries = [
        _summary("run_daily_briefing_job", last_status="NEVER",
                 last_run_at="", runs_in_window=0),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert len(findings) == 1
    assert findings[0].reason == "stale"
    assert findings[0].severity == "CRITICAL"


def test_unparseable_last_run_at_is_stale() -> None:
    summaries = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at="not-a-timestamp"),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert len(findings) == 1
    assert findings[0].reason == "stale"
    assert findings[0].severity == "CRITICAL"


# ─── assess_worker_health — FAILING (HIGH) ────────────────────────────────

def test_errored_critical_job_is_high_finding() -> None:
    summaries = [
        _summary("run_daily_briefing_job", last_status="error",
                 last_run_at=_ago(60), runs_in_window=3, success_rate_24h=0.66),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert len(findings) == 1
    assert findings[0].reason == "failing"
    assert findings[0].severity == "HIGH"


def test_low_success_rate_critical_job_is_high_finding() -> None:
    # Recent (not stale), last_status ok, but only 40% success across 5 runs.
    summaries = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at=_ago(30), runs_in_window=5, success_rate_24h=0.4),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert len(findings) == 1
    assert findings[0].reason == "failing"
    assert findings[0].severity == "HIGH"


# ─── assess_worker_health — NO FINDING ────────────────────────────────────

def test_healthy_recent_job_yields_no_finding() -> None:
    summaries = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at=_ago(60), runs_in_window=1, success_rate_24h=1.0),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert findings == []


def test_non_critical_stale_job_yields_no_finding() -> None:
    # Stale + erroring, but it's not in the critical set → ignored.
    summaries = [
        _summary("run_alert_prune_job", last_status="error",
                 last_run_at=_ago(99 * 60), runs_in_window=0),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert findings == []


def test_quiet_job_no_data_is_not_failing() -> None:
    # runs_in_window == 0 with success_rate 1.0 means "no data" — must NOT
    # read as failing. The job ran recently (not stale) but has no in-window
    # runs (e.g. a job that runs once a day, just outside the 24h count edge
    # but inside the staleness window).
    summaries = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at=_ago(60), runs_in_window=0, success_rate_24h=1.0),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert findings == []


def test_empty_summaries_yields_empty() -> None:
    assert assess_worker_health([], now_iso=NOW_ISO, critical_jobs=CRITICAL) == []


def test_stale_and_erroring_yields_single_stale_finding() -> None:
    # Both conditions true; stale (strictly worse) wins and we emit ONE
    # finding, not two.
    summaries = [
        _summary("run_daily_briefing_job", last_status="error",
                 last_run_at=_ago(40 * 60), runs_in_window=2,
                 success_rate_24h=0.0),
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert len(findings) == 1
    assert findings[0].reason == "stale"
    assert findings[0].severity == "CRITICAL"


def test_multiple_jobs_mixed_states() -> None:
    summaries = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at=_ago(40 * 60)),               # stale → CRITICAL
        _summary("run_signal_ledger_freeze_job", last_status="error",
                 last_run_at=_ago(60)),                    # failing → HIGH
        _summary("run_alert_prune_job", last_status="NEVER"),  # non-crit → skip
    ]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    by_job = {f.job_name: f for f in findings}
    assert set(by_job) == {"run_daily_briefing_job",
                           "run_signal_ledger_freeze_job"}
    assert by_job["run_daily_briefing_job"].severity == "CRITICAL"
    assert by_job["run_signal_ledger_freeze_job"].severity == "HIGH"


def test_empty_critical_set_yields_no_findings() -> None:
    summaries = [
        _summary("run_daily_briefing_job", last_status="NEVER"),
    ]
    assert assess_worker_health(summaries, now_iso=NOW_ISO, critical_jobs=[]) == []


def test_assess_never_raises_on_garbage_rows() -> None:
    # Mixed garbage entries must be skipped, not raise.
    summaries = [None, 42, "nope", {"job_name": None}, _summary(
        "run_daily_briefing_job", last_status="NEVER")]
    findings = assess_worker_health(
        summaries, now_iso=NOW_ISO, critical_jobs=CRITICAL,
    )
    assert len(findings) == 1
    assert findings[0].job_name == "run_daily_briefing_job"


# ─── Orchestrator: fire + cooldown ────────────────────────────────────────

def test_orchestrator_fires_and_then_cooldown_suppresses(monkeypatch) -> None:
    """A stale critical job fires a sane ShippingAlert; a second call within
    cooldown is suppressed."""
    import engine.worker_deadman as wd

    # summarize_jobs → one stale critical job. Patch where the orchestrator
    # imports it from (state.worker_runs).
    stale_summary = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at=_ago(99 * 60)),
    ]
    import state.worker_runs as worker_runs
    monkeypatch.setattr(
        worker_runs, "summarize_jobs", lambda: stale_summary, raising=True,
    )

    # Capture the alerts handed to save_alerts. Patch on the real module so
    # the lazy `from engine.alert_engine_v2 import save_alerts` picks it up.
    captured: list = []

    import engine.alert_engine_v2 as aev2

    def _capture(alerts, *, user_id=None, rule_id=None):
        captured.extend(alerts)

    monkeypatch.setattr(aev2, "save_alerts", _capture, raising=True)

    # Pin user_id so the cooldown key is deterministic (no Streamlit).
    counts = wd.check_worker_health_and_fire(now=NOW, user_id="tester")

    assert counts["fired"] >= 1
    assert counts["errored"] == 0
    assert len(captured) >= 1

    alert = captured[0]
    assert alert.alert_type == "WORKER_DEADMAN"
    assert alert.severity == "CRITICAL"
    # job_name rides in port_locode as the dedup entity key.
    assert alert.port_locode == "run_daily_briefing_job"
    assert "run_daily_briefing_job" in alert.title
    assert alert.acknowledged is False

    # Second call within the cooldown window → suppressed, no new fire.
    captured.clear()
    counts2 = wd.check_worker_health_and_fire(now=NOW, user_id="tester")
    assert counts2["fired"] == 0
    assert counts2["skipped_cooldown"] >= 1
    assert captured == []


def test_orchestrator_swallows_summarize_failure(monkeypatch) -> None:
    """A summarize_jobs that raises → errored counter, never propagates."""
    import engine.worker_deadman as wd
    import state.worker_runs as worker_runs

    def _boom():
        raise RuntimeError("db wedged")

    monkeypatch.setattr(worker_runs, "summarize_jobs", _boom, raising=True)

    counts = wd.check_worker_health_and_fire(now=NOW, user_id="tester")
    assert counts == {"fired": 0, "skipped_cooldown": 0, "errored": 1}


def test_orchestrator_healthy_fires_nothing(monkeypatch) -> None:
    """A healthy snapshot fires nothing and writes no cooldown."""
    import engine.worker_deadman as wd
    import state.worker_runs as worker_runs
    import engine.alert_engine_v2 as aev2

    healthy = [
        _summary("run_daily_briefing_job", last_status="ok",
                 last_run_at=_ago(60)),
        _summary("run_signal_ledger_freeze_job", last_status="ok",
                 last_run_at=_ago(120)),
    ]
    monkeypatch.setattr(worker_runs, "summarize_jobs", lambda: healthy)

    fired: list = []
    monkeypatch.setattr(
        aev2, "save_alerts",
        lambda alerts, *, user_id=None, rule_id=None: fired.extend(alerts),
    )

    counts = wd.check_worker_health_and_fire(now=NOW, user_id="tester")
    assert counts == {"fired": 0, "skipped_cooldown": 0, "errored": 0}
    assert fired == []


def test_orchestrator_round_trips_through_real_save_alerts(monkeypatch) -> None:
    # Closes the stub-only gap: fire through the REAL save_alerts + read back, so
    # the new WORKER_DEADMAN alert_type actually persists through the live path.
    import engine.worker_deadman as wd
    import state.worker_runs as worker_runs
    from engine.alert_engine_v2 import load_alerts

    stale = [_summary("run_daily_briefing_job", last_status="NEVER", last_run_at="")]
    monkeypatch.setattr(worker_runs, "summarize_jobs", lambda: stale, raising=True)

    counts = wd.check_worker_health_and_fire(now=NOW, user_id="tester")
    assert counts["fired"] >= 1
    deadman = [a for a in load_alerts(user_id="tester")
               if a.alert_type == "WORKER_DEADMAN"]
    assert deadman, "WORKER_DEADMAN alert must persist through real save_alerts"
    assert deadman[0].severity in ("CRITICAL", "HIGH")
    assert "run_daily_briefing_job" in (
        deadman[0].port_locode + deadman[0].title + deadman[0].body)
