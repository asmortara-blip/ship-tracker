"""Tests for engine.delivery_retry — schema v26 retry queue.

When a webhook/slack/email dispatch fails with a retriable error (HTTP
5xx, connection timeout, SMTP temporary failure), the alert is
enqueued for retry with exponential backoff. Worker walks the queue
every 5 minutes; up to MAX_RETRIES attempts before final failure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from engine.delivery_retry import (
    BACKOFF_BASE_SECONDS,
    MAX_RETRIES,
    RetryEntry,
    _compute_backoff_seconds,
    cancel_retry,
    cleanup_completed,
    enqueue_for_retry,
    get_due_retries,
    list_failed,
    list_pending,
    list_succeeded_recent,
    manual_retry,
    mark_retry_attempt,
    run_retry_pass,
)


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── enqueue_for_retry ────────────────────────────────────────────────────


def test_enqueue_persists_with_attempt_count_zero() -> None:
    entry = enqueue_for_retry(
        "alert-1", "ch-1", user_id="alice",
        error_message="HTTP 503",
    )
    assert entry is not None
    assert entry.alert_id == "alert-1"
    assert entry.channel_id == "ch-1"
    assert entry.user_id == "alice"
    assert entry.attempt_count == 0
    assert entry.final_status == "pending"
    assert entry.last_error == "HTTP 503"


def test_enqueue_idempotent_updates_existing_pending() -> None:
    a = enqueue_for_retry("alert-1", "ch-1", user_id="alice", error_message="first")
    b = enqueue_for_retry("alert-1", "ch-1", user_id="alice", error_message="second")
    assert a is not None and b is not None
    # Same row updated, not stacked.
    pending = list_pending(user_id="alice")
    assert len(pending) == 1
    # Most-recent error wins.
    assert pending[0].last_error == "second"


def test_enqueue_first_retry_60s_out() -> None:
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    with patch("engine.delivery_retry._now", return_value=now):
        entry = enqueue_for_retry(
            "alert-1", "ch-1", user_id="alice", error_message="fail",
        )
    assert entry is not None
    expected = (now + timedelta(seconds=BACKOFF_BASE_SECONDS)).isoformat()
    assert entry.next_attempt_at == expected


# ─── get_due_retries ──────────────────────────────────────────────────────


def test_get_due_returns_pending_and_due() -> None:
    # Enqueue with explicit past next_attempt_at via direct mark.
    enqueue_for_retry("alert-1", "ch-1", user_id="alice", error_message="fail")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    # Default enqueue makes it due 60s out — not yet due.
    assert get_due_retries(now=datetime.now(timezone.utc)) == []
    # But due 2 min later.
    assert len(get_due_retries(now=future)) == 1


def test_get_due_empty_when_nothing_pending() -> None:
    assert get_due_retries() == []


# ─── mark_retry_attempt ───────────────────────────────────────────────────


def test_mark_success_finalizes() -> None:
    entry = enqueue_for_retry("alert-1", "ch-1", user_id="alice", error_message="fail")
    assert entry is not None
    assert mark_retry_attempt(entry.queue_id, success=True) is True
    succeeded = list_succeeded_recent(user_id="alice")
    assert len(succeeded) == 1
    assert succeeded[0].final_status == "succeeded"


def test_mark_failure_bumps_attempt_count() -> None:
    entry = enqueue_for_retry("alert-1", "ch-1", user_id="alice", error_message="fail")
    assert entry is not None
    mark_retry_attempt(entry.queue_id, success=False, error_message="503 again")
    pending = list_pending(user_id="alice")
    assert len(pending) == 1
    assert pending[0].attempt_count == 1
    assert pending[0].last_error == "503 again"


def test_mark_failure_at_max_retries_finalizes_as_failed() -> None:
    entry = enqueue_for_retry("alert-1", "ch-1", user_id="alice", error_message="fail")
    assert entry is not None
    for _ in range(MAX_RETRIES):
        mark_retry_attempt(entry.queue_id, success=False, error_message="still failing")
    failed = list_failed(user_id="alice")
    assert len(failed) == 1
    assert failed[0].final_status == "failed"
    # No longer pending.
    assert list_pending(user_id="alice") == []


# ─── Backoff math ─────────────────────────────────────────────────────────


def test_backoff_exponential() -> None:
    # 60s base, 2^(n-1) multiplier
    assert _compute_backoff_seconds(1) == 60
    assert _compute_backoff_seconds(2) == 120
    assert _compute_backoff_seconds(3) == 240
    assert _compute_backoff_seconds(4) == 480
    assert _compute_backoff_seconds(5) == 960


# ─── Per-user scoping ─────────────────────────────────────────────────────


def test_list_pending_per_user_isolation() -> None:
    enqueue_for_retry("a", "ch-1", user_id="alice", error_message="x")
    enqueue_for_retry("b", "ch-2", user_id="bob", error_message="y")
    assert len(list_pending(user_id="alice")) == 1
    assert len(list_pending(user_id="bob")) == 1


def test_cancel_per_user_scoping() -> None:
    entry = enqueue_for_retry("a", "ch-1", user_id="alice", error_message="x")
    assert entry is not None
    # bob cannot cancel alice's retry.
    assert cancel_retry(entry.queue_id, user_id="bob") is False
    # alice can cancel her own.
    assert cancel_retry(entry.queue_id, user_id="alice") is True


def test_manual_retry_sets_next_attempt_to_now() -> None:
    entry = enqueue_for_retry("a", "ch-1", user_id="alice", error_message="x")
    assert entry is not None
    assert manual_retry(entry.queue_id, user_id="alice") is True
    # Now due immediately.
    due = get_due_retries(now=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert len(due) == 1


# ─── cleanup_completed ────────────────────────────────────────────────────


def test_cleanup_completed_deletes_old_finalized_rows() -> None:
    # Insert a "succeeded" row with an old final_at via direct UPDATE.
    entry = enqueue_for_retry("a", "ch-1", user_id="alice", error_message="x")
    assert entry is not None
    mark_retry_attempt(entry.queue_id, success=True)

    from state.db import get_connection
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE delivery_retry_queue SET final_at = ? WHERE queue_id = ?",
            (old, entry.queue_id),
        )

    deleted = cleanup_completed(retention_days=14)
    assert deleted == 1


def test_cleanup_preserves_recent_and_pending() -> None:
    # Pending row.
    enqueue_for_retry("a", "ch-1", user_id="alice", error_message="x")
    # Succeeded row dated NOW.
    e = enqueue_for_retry("b", "ch-2", user_id="alice", error_message="y")
    mark_retry_attempt(e.queue_id, success=True)

    deleted = cleanup_completed(retention_days=14)
    assert deleted == 0
    # Both still there.
    total = len(list_pending(user_id="alice")) + len(list_succeeded_recent(user_id="alice"))
    assert total == 2


# ─── run_retry_pass ───────────────────────────────────────────────────────


def test_run_retry_pass_never_raises() -> None:
    # Empty DB → no due rows → clean dict.
    result = run_retry_pass()
    assert isinstance(result, dict)
    assert "processed" in result


def test_run_retry_pass_swallows_redispatch_error(monkeypatch) -> None:
    e = enqueue_for_retry("a", "ch-1", user_id="alice", error_message="x")
    assert e is not None
    # Make it due NOW.
    manual_retry(e.queue_id, user_id="alice")

    def _boom(*a, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr("engine.delivery_retry._redispatch_one", _boom)
    # Must NOT propagate.
    result = run_retry_pass(now=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert isinstance(result, dict)
