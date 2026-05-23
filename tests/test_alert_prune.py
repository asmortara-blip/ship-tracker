"""Tests for engine.alert_engine_v2.prune_old_alerts +
worker.scheduler.run_alert_prune_job.

Acknowledged alerts past the cutoff get deleted; unacknowledged ones
do not (unless only_acknowledged=False is passed). Defaults to 180
days so multi-month alert_backtest backtests still find rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.alert_engine_v2 import prune_old_alerts


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def _insert_alert(alert_id: str, created_at: str, *, acknowledged: bool = False) -> None:
    from state.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO alerts
              (alert_id, created_at, alert_type, severity, title, body,
               ticker, route_id, port_locode, value, threshold, change_pct,
               acknowledged)
            VALUES (?, ?, 'MACRO', 'LOW', 't', 'b', ?, '', '', 0, 0, 0, ?)
            """,
            (alert_id, created_at, alert_id, 1 if acknowledged else 0),
        )


def _row_count() -> int:
    from state.db import get_connection
    return get_connection().execute(
        "SELECT COUNT(*) AS n FROM alerts"
    ).fetchone()["n"]


# ─── prune_old_alerts ─────────────────────────────────────────────────────

def test_prune_old_alerts_empty_db_returns_zero() -> None:
    assert prune_old_alerts(retention_days=180) == 0


def test_prune_old_alerts_negative_retention_is_noop() -> None:
    _insert_alert("a", (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
                  acknowledged=True)
    assert prune_old_alerts(retention_days=-1) == 0
    assert _row_count() == 1


def test_prune_old_alerts_keeps_recent_acknowledged() -> None:
    """Acknowledged but within the window → keep."""
    _insert_alert("recent", (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
                  acknowledged=True)
    deleted = prune_old_alerts(retention_days=180)
    assert deleted == 0
    assert _row_count() == 1


def test_prune_old_alerts_deletes_old_acknowledged() -> None:
    _insert_alert("old_ack", (datetime.now(timezone.utc) - timedelta(days=300)).isoformat(),
                  acknowledged=True)
    deleted = prune_old_alerts(retention_days=180)
    assert deleted == 1
    assert _row_count() == 0


def test_prune_old_alerts_preserves_old_unacknowledged_by_default() -> None:
    """The whole point — old unacknowledged alerts must NOT be auto-pruned.
    A 10-month-old unack'd CRITICAL should still be visible to the operator."""
    _insert_alert("ghost", (datetime.now(timezone.utc) - timedelta(days=300)).isoformat(),
                  acknowledged=False)
    deleted = prune_old_alerts(retention_days=180)
    assert deleted == 0
    assert _row_count() == 1


def test_prune_old_alerts_only_acknowledged_false_deletes_everything_old() -> None:
    """Admin path — also wipe old unacked rows past the cutoff."""
    _insert_alert("old_unack", (datetime.now(timezone.utc) - timedelta(days=300)).isoformat(),
                  acknowledged=False)
    _insert_alert("old_ack", (datetime.now(timezone.utc) - timedelta(days=300)).isoformat(),
                  acknowledged=True)
    deleted = prune_old_alerts(retention_days=180, only_acknowledged=False)
    assert deleted == 2
    assert _row_count() == 0


def test_prune_old_alerts_mixed_ages_deletes_only_old_ack() -> None:
    base = datetime.now(timezone.utc)
    _insert_alert("recent_ack", (base - timedelta(days=10)).isoformat(),
                  acknowledged=True)
    _insert_alert("old_ack",    (base - timedelta(days=300)).isoformat(),
                  acknowledged=True)
    _insert_alert("recent_unack", (base - timedelta(days=10)).isoformat(),
                  acknowledged=False)
    _insert_alert("old_unack",  (base - timedelta(days=300)).isoformat(),
                  acknowledged=False)
    deleted = prune_old_alerts(retention_days=180)
    assert deleted == 1   # only old_ack
    assert _row_count() == 3


# ─── worker.scheduler.run_alert_prune_job ────────────────────────────────

def test_run_alert_prune_job_wraps_prune_old_alerts() -> None:
    from worker.scheduler import run_alert_prune_job

    _insert_alert("old", (datetime.now(timezone.utc) - timedelta(days=300)).isoformat(),
                  acknowledged=True)
    deleted = run_alert_prune_job(retention_days=180)
    assert deleted == 1


def test_run_alert_prune_job_swallows_engine_error(monkeypatch) -> None:
    """A prune helper failure must NOT propagate into the worker pipeline."""
    from worker import scheduler as sched

    def _boom(*a, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr(
        "engine.alert_engine_v2.prune_old_alerts", _boom,
    )
    # run_alert_prune_job catches the exception → returns 0
    assert sched.run_alert_prune_job() == 0


# ─── run_audit_prune_job ──────────────────────────────────────────────────

def test_run_audit_prune_job_wraps_helper() -> None:
    """Insert a stale audit row + verify the worker prune cleans it."""
    from datetime import datetime, timedelta, timezone
    from state.db import get_connection
    from worker.scheduler import run_audit_prune_job

    conn = get_connection()
    old_iso = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO audit_events
              (event_id, created_at, user_id, action, entity_type, entity_id, detail_json)
            VALUES (?, ?, '', 'test_action', '', '', '{}')
            """,
            ("ev1", old_iso),
        )
    deleted = run_audit_prune_job(retention_days=365)
    assert deleted == 1


def test_run_audit_prune_job_swallows_engine_error(monkeypatch) -> None:
    from worker import scheduler as sched

    def _boom(*a, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr("auth.audit.prune_old_audit_events", _boom)
    assert sched.run_audit_prune_job() == 0


# ─── run_report_prune_job ─────────────────────────────────────────────────

def test_run_report_prune_job_keeps_only_newest_n() -> None:
    """Seed N+2 report rows + verify the worker prune leaves only N."""
    from datetime import datetime, timedelta, timezone
    from state.db import get_connection
    from worker.scheduler import run_report_prune_job

    conn = get_connection()
    base = datetime.now(timezone.utc)
    with conn:
        for i in range(5):
            conn.execute(
                """
                INSERT INTO report_history
                  (report_id, generated_at, report_date, sentiment_label,
                   sentiment_score, risk_level, signal_count, data_quality,
                   file_path, file_size_kb)
                VALUES (?, ?, '', 'NEUTRAL', 0.0, 'MODERATE', 0,
                        'PARTIAL', '/no/such/path', 0.0)
                """,
                (f"r{i}", (base - timedelta(hours=i)).isoformat()),
            )
    deleted = run_report_prune_job(keep_n=2)
    assert deleted == 3
    n_left = conn.execute("SELECT COUNT(*) AS n FROM report_history").fetchone()["n"]
    assert n_left == 2


def test_run_report_prune_job_swallows_error(monkeypatch) -> None:
    from worker import scheduler as sched

    def _boom(*a, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr("utils.report_history.prune_old_reports", _boom)
    assert sched.run_report_prune_job() == 0
