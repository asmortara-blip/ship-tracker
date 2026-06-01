"""Tests for engine.alert_escalation (schema v24).

Operators wanted an unacknowledged alert to climb a ladder of
fallback channels instead of going stale on the first channel that
missed the page. v24 adds per-rule escalation chains: each step has
an ``after_minutes`` timer and a target ``channel_id``; the worker
walks unacked alerts every five minutes and dispatches the next due
step.

Covers:

* CRUD: add_escalation_step / get_escalation_chain /
  delete_escalation_step / delete_chain persistence + the
  REPLACE-in-place idiom on (rule_id, user_id, step_number).
* Per-user scoping: alice's chain on rule X is invisible to bob and
  does NOT escalate bob's alerts.
* get_alerts_due_for_escalation walks the chain: alert escalates to
  step 1, then step 2 after another N minutes, then step 3 after a
  further N minutes — the state machine on
  (escalation_step, last_escalated_at) is correct end-to-end.
* escalate_alert dispatches via deliver_alert + stamps the state
  machine on success; never raises even when deliver_alert blows up
  or the channel_id is missing.
* run_escalation_pass returns the count dict and never raises even
  when one alert in the batch is malformed.
* Acknowledging mid-chain stops further escalation (the unacked
  filter excludes ack'd rows from get_alerts_due_for_escalation).
* The worker scheduler wrapper returns counts and swallows engine
  exceptions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from engine import alert_escalation as escmod
from engine.alert_delivery import DeliveryChannel, DeliveryResult, save_channel
from engine.alert_engine_v2 import _make, save_alerts
from engine.alert_escalation import (
    EscalationStep,
    add_escalation_step,
    delete_chain,
    delete_escalation_step,
    escalate_alert,
    get_alerts_due_for_escalation,
    get_escalation_chain,
    run_escalation_pass,
)


# ─── Isolation fixture ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation. Same pattern as test_alert_cooldown."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _channel(
    *,
    channel_id: str = "ch_test",
    name: str = "Test",
    user_id: str = "alice",
    kind: str = "slack",
) -> DeliveryChannel:
    """Persist a DeliveryChannel under ``user_id`` and return it."""
    ch = DeliveryChannel(
        channel_id=channel_id,
        name=name,
        kind=kind,
        target=f"https://hooks.example.com/{channel_id}",
        severity_threshold="LOW",
        enabled=True,
    )
    save_channel(ch, user_id=user_id)
    return ch


def _persist_alert(
    *,
    user_id: str = "alice",
    rule_id: str = "rule_x",
    severity: str = "HIGH",
    created_at: str | None = None,
) -> str:
    """Insert one unacked alert with rule_id stamped, return alert_id.

    Uses the engine's save_alerts so the row matches the production
    layout. ``created_at`` can be backdated to exercise the
    after_minutes boundary; left None it lands at NOW.
    """
    alert = _make(
        "BDI_MOVE", severity, "title", "body",
        value=100.0, threshold=5.0, change_pct=10.0,
    )
    if created_at is not None:
        alert.created_at = created_at
    save_alerts([alert], user_id=user_id, rule_id=rule_id)
    return alert.alert_id


def _set_escalation_state(alert_id: str, *, step: int, last_escalated_at: str | None) -> None:
    """Direct UPDATE on the alerts row to simulate prior escalations.

    Tests need to position the state machine at "step N just fired
    at timestamp T" so the after_minutes boundary can be probed
    deterministically. This is the cheapest way to do that without
    actually running the escalation engine multiple times.
    """
    from state.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE alerts SET escalation_step = ?, last_escalated_at = ? "
            "WHERE alert_id = ?",
            (step, last_escalated_at, alert_id),
        )


# ─── CRUD ─────────────────────────────────────────────────────────────────

def test_add_escalation_step_persists_with_all_fields() -> None:
    step = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    assert step is not None
    assert step.rule_id == "rule_x"
    assert step.user_id == "alice"
    assert step.step_number == 1
    assert step.after_minutes == 15
    assert step.channel_id == "ch1"
    assert step.chain_id  # populated UUID
    assert step.created_at  # populated ISO timestamp

    # Round-trip via get_escalation_chain.
    chain = get_escalation_chain("rule_x", user_id="alice")
    assert len(chain) == 1
    assert chain[0].channel_id == "ch1"
    assert chain[0].after_minutes == 15


def test_add_escalation_step_replaces_existing() -> None:
    """Re-adding the same (rule_id, user_id, step_number) REPLACES."""
    first = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch_old",
    )
    assert first is not None
    second = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=30, channel_id="ch_new",
    )
    assert second is not None
    # New row has a fresh chain_id (REPLACE not in-place UPDATE).
    assert second.chain_id != first.chain_id

    chain = get_escalation_chain("rule_x", user_id="alice")
    assert len(chain) == 1
    assert chain[0].channel_id == "ch_new"
    assert chain[0].after_minutes == 30


def test_get_escalation_chain_ordered_by_step_number_asc() -> None:
    # Insert out of order — the SELECT must return ordered.
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=3, after_minutes=60, channel_id="ch3",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=2, after_minutes=30, channel_id="ch2",
    )

    chain = get_escalation_chain("rule_x", user_id="alice")
    assert [s.step_number for s in chain] == [1, 2, 3]
    assert [s.channel_id for s in chain] == ["ch1", "ch2", "ch3"]


def test_delete_escalation_step_per_user_scoping() -> None:
    a_step = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    b_step = add_escalation_step(
        rule_id="rule_x", user_id="bob",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    assert a_step is not None
    assert b_step is not None

    # bob cannot delete alice's row by passing her chain_id.
    assert delete_escalation_step(a_step.chain_id, user_id="bob") is False
    assert len(get_escalation_chain("rule_x", user_id="alice")) == 1

    # alice can delete her own row.
    assert delete_escalation_step(a_step.chain_id, user_id="alice") is True
    assert len(get_escalation_chain("rule_x", user_id="alice")) == 0
    # bob's row untouched.
    assert len(get_escalation_chain("rule_x", user_id="bob")) == 1


def test_delete_chain_bulk_removes_all_steps_for_a_rule() -> None:
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=2, after_minutes=30, channel_id="ch2",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=3, after_minutes=60, channel_id="ch3",
    )
    # Different rule's chain stays untouched.
    add_escalation_step(
        rule_id="rule_other", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch_other",
    )

    removed = delete_chain("rule_x", user_id="alice")
    assert removed == 3
    assert get_escalation_chain("rule_x", user_id="alice") == []
    assert len(get_escalation_chain("rule_other", user_id="alice")) == 1


# ─── get_alerts_due_for_escalation ────────────────────────────────────────

def test_get_alerts_due_returns_empty_when_no_chains_exist() -> None:
    # Alert exists with rule_id but no chain configured.
    _persist_alert(user_id="alice", rule_id="rule_x")
    assert get_alerts_due_for_escalation() == []


def test_get_alerts_due_returns_empty_when_alerts_all_acked() -> None:
    alert_id = _persist_alert(user_id="alice", rule_id="rule_x")
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=0, channel_id="ch1",
    )
    # Manually flip the row to acked.
    from state.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE alerts SET acknowledged = 1 WHERE alert_id = ?",
            (alert_id,),
        )
    assert get_alerts_due_for_escalation() == []


def test_get_alerts_due_returns_alert_when_window_elapsed() -> None:
    # Alert created 30 min ago; step 1 has 15 min window → due NOW.
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    alert_id = _persist_alert(
        user_id="alice", rule_id="rule_x", created_at=backdate,
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )

    due = get_alerts_due_for_escalation()
    assert len(due) == 1
    alert, step = due[0]
    assert alert["alert_id"] == alert_id
    assert step.step_number == 1
    assert step.channel_id == "ch1"


def test_get_alerts_due_respects_after_minutes_too_early() -> None:
    """Alert created 5 min ago; step 1 has 15 min window → NOT yet due."""
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _persist_alert(user_id="alice", rule_id="rule_x", created_at=backdate)
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    assert get_alerts_due_for_escalation() == []


def test_get_alerts_due_walks_chain_step_1_then_step_2() -> None:
    """After step 1 fires at T, step 2 should become due at T + N min."""
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    alert_id = _persist_alert(
        user_id="alice", rule_id="rule_x", created_at=backdate,
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=2, after_minutes=20, channel_id="ch2",
    )

    # Step 1 just fired 25 min ago — step 2's 20-min window has elapsed.
    last_esc = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
    _set_escalation_state(alert_id, step=1, last_escalated_at=last_esc)

    due = get_alerts_due_for_escalation()
    assert len(due) == 1
    alert, step = due[0]
    assert step.step_number == 2
    assert step.channel_id == "ch2"


def test_get_alerts_due_per_user_scoping() -> None:
    """alice's chain does NOT escalate bob's alert on the same rule."""
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    bob_alert = _persist_alert(
        user_id="bob", rule_id="rule_x", created_at=backdate,
    )
    # alice configures the chain — bob has NO chain.
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )

    # bob's alert is past the timer, but he has no chain → not due.
    assert get_alerts_due_for_escalation() == []

    # Now bob configures his own chain → he becomes due.
    add_escalation_step(
        rule_id="rule_x", user_id="bob",
        step_number=1, after_minutes=15, channel_id="ch_bob",
    )
    due = get_alerts_due_for_escalation()
    assert len(due) == 1
    alert, step = due[0]
    assert alert["alert_id"] == bob_alert
    assert step.user_id == "bob"
    assert step.channel_id == "ch_bob"


# ─── escalate_alert ───────────────────────────────────────────────────────

def test_escalate_alert_dispatches_via_deliver_alert() -> None:
    _channel(channel_id="ch1", user_id="alice")
    alert_id = _persist_alert(user_id="alice", rule_id="rule_x")
    step = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=0, channel_id="ch1",
    )
    assert step is not None

    sent: list[tuple[str, str]] = []

    def fake_deliver(alert, channel):
        sent.append((alert.alert_id, channel.channel_id))
        return DeliveryResult(success=True, status_code=200)

    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        ok = escalate_alert(alert_id, step, user_id="alice")
    assert ok is True
    assert len(sent) == 1
    assert sent[0] == (alert_id, "ch1")


def test_escalate_alert_stamps_state_machine_on_success() -> None:
    _channel(channel_id="ch1", user_id="alice")
    alert_id = _persist_alert(user_id="alice", rule_id="rule_x")
    step = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=0, channel_id="ch1",
    )
    assert step is not None

    def fake_deliver(alert, channel):
        return DeliveryResult(success=True, status_code=200)

    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        assert escalate_alert(alert_id, step, user_id="alice") is True

    # Read back the row and confirm last_escalated_at + escalation_step.
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT escalation_step, last_escalated_at FROM alerts "
        "WHERE alert_id = ?",
        (alert_id,),
    ).fetchone()
    assert row["escalation_step"] == 1
    assert row["last_escalated_at"]  # non-empty ISO timestamp


def test_escalate_alert_never_raises_when_deliver_raises() -> None:
    _channel(channel_id="ch1", user_id="alice")
    alert_id = _persist_alert(user_id="alice", rule_id="rule_x")
    step = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=0, channel_id="ch1",
    )
    assert step is not None

    def boom(alert, channel):
        raise RuntimeError("network exploded")

    with patch("engine.alert_delivery.deliver_alert", boom):
        ok = escalate_alert(alert_id, step, user_id="alice")
    assert ok is False

    # State machine NOT advanced — the next pass will retry.
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT escalation_step, last_escalated_at FROM alerts "
        "WHERE alert_id = ?",
        (alert_id,),
    ).fetchone()
    assert row["escalation_step"] == 0
    assert row["last_escalated_at"] is None


def test_escalate_alert_returns_false_when_channel_missing() -> None:
    """Step points at a channel that does not exist → False, no advance."""
    # NO channel saved.
    alert_id = _persist_alert(user_id="alice", rule_id="rule_x")
    step = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=0, channel_id="ch_missing",
    )
    assert step is not None
    ok = escalate_alert(alert_id, step, user_id="alice")
    assert ok is False

    from state.db import get_connection
    row = get_connection().execute(
        "SELECT escalation_step FROM alerts WHERE alert_id = ?",
        (alert_id,),
    ).fetchone()
    assert row["escalation_step"] == 0


# ─── run_escalation_pass ──────────────────────────────────────────────────

def test_run_escalation_pass_returns_counts() -> None:
    _channel(channel_id="ch1", user_id="alice")
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    _persist_alert(user_id="alice", rule_id="rule_x", created_at=backdate)
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )

    def fake_deliver(alert, channel):
        return DeliveryResult(success=True, status_code=200)

    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        counts = run_escalation_pass()
    assert counts["checked"] == 1
    assert counts["escalated"] == 1
    assert counts["failed"] == 0


def test_run_escalation_pass_per_user_scoping() -> None:
    """Only chains owned by the alert OWNER are walked."""
    _channel(channel_id="ch_a", user_id="alice")
    _channel(channel_id="ch_b", user_id="bob")
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    a_alert = _persist_alert(
        user_id="alice", rule_id="rule_x", created_at=backdate,
    )
    b_alert = _persist_alert(
        user_id="bob", rule_id="rule_x", created_at=backdate,
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch_a",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="bob",
        step_number=1, after_minutes=15, channel_id="ch_b",
    )

    delivered: list[tuple[str, str]] = []

    def fake_deliver(alert, channel):
        delivered.append((alert.alert_id, channel.channel_id))
        return DeliveryResult(success=True, status_code=200)

    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        counts = run_escalation_pass()
    assert counts["checked"] == 2
    assert counts["escalated"] == 2
    # alice's alert went to ch_a; bob's to ch_b — no cross-user dispatch.
    assert (a_alert, "ch_a") in delivered
    assert (b_alert, "ch_b") in delivered


def test_run_escalation_pass_never_raises_on_per_alert_failure() -> None:
    """One bad alert does not break the loop for the rest."""
    _channel(channel_id="ch_good", user_id="alice")
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    good = _persist_alert(
        user_id="alice", rule_id="rule_good", created_at=backdate,
    )
    bad = _persist_alert(
        user_id="alice", rule_id="rule_bad", created_at=backdate,
    )
    add_escalation_step(
        rule_id="rule_good", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch_good",
    )
    # Bad chain points at a channel that does not exist.
    add_escalation_step(
        rule_id="rule_bad", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch_missing",
    )

    delivered: list[str] = []

    def fake_deliver(alert, channel):
        delivered.append(alert.alert_id)
        return DeliveryResult(success=True, status_code=200)

    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        counts = run_escalation_pass()
    # The good alert escalated. The bad one either gets counted as
    # failed (if get_alerts_due_for_escalation returns it and the
    # dispatch fails) OR is pre-filtered out before dispatch (if the
    # helper drops alerts whose chain channel is missing). Both are
    # valid "loop continued" semantics — what matters is the good
    # alert went out and the bad one did not.
    assert counts["escalated"] >= 1
    assert good in delivered
    assert bad not in delivered


# ─── Worker scheduler wrapper ─────────────────────────────────────────────

def test_run_alert_escalation_job_returns_counts() -> None:
    """The scheduler wrapper passes through engine counts unchanged."""
    from worker.scheduler import run_alert_escalation_job

    _channel(channel_id="ch1", user_id="alice")
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    _persist_alert(user_id="alice", rule_id="rule_x", created_at=backdate)
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )

    def fake_deliver(alert, channel):
        return DeliveryResult(success=True, status_code=200)

    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        counts = run_alert_escalation_job()
    assert counts == {"checked": 1, "escalated": 1, "failed": 0}


def test_run_alert_escalation_job_swallows_engine_errors() -> None:
    """A top-level engine exception returns all-zero counts, not raise."""
    from worker.scheduler import run_alert_escalation_job

    def boom(*args, **kwargs):
        raise RuntimeError("engine on fire")

    with patch.object(escmod, "run_escalation_pass", boom):
        counts = run_alert_escalation_job()
    assert counts == {"checked": 0, "escalated": 0, "failed": 0}


# ─── End-to-end: walking the chain ────────────────────────────────────────

def test_acknowledge_mid_chain_stops_further_escalation() -> None:
    """Once an alert is ack'd, get_alerts_due_for_escalation excludes it."""
    _channel(channel_id="ch1", user_id="alice")
    _channel(channel_id="ch2", user_id="alice")
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    alert_id = _persist_alert(
        user_id="alice", rule_id="rule_x", created_at=backdate,
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=2, after_minutes=10, channel_id="ch2",
    )

    def fake_deliver(alert, channel):
        return DeliveryResult(success=True, status_code=200)

    # First pass — step 1 fires.
    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        counts = run_escalation_pass()
    assert counts["escalated"] == 1

    # Operator acks the alert.
    from state.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE alerts SET acknowledged = 1 WHERE alert_id = ?",
            (alert_id,),
        )

    # Even with the step-2 window elapsed, NO further escalation
    # because the alert is acked.
    last_esc = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    _set_escalation_state(alert_id, step=1, last_escalated_at=last_esc)
    # Re-ack since _set_escalation_state may have cleared nothing —
    # but explicitly re-flip just to be safe.
    with conn:
        conn.execute(
            "UPDATE alerts SET acknowledged = 1 WHERE alert_id = ?",
            (alert_id,),
        )

    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        counts = run_escalation_pass()
    assert counts == {"checked": 0, "escalated": 0, "failed": 0}


def test_walk_the_chain_step_1_then_step_2_then_step_3() -> None:
    """Full end-to-end: alert escalates through three steps in order."""
    _channel(channel_id="ch1", user_id="alice")
    _channel(channel_id="ch2", user_id="alice")
    _channel(channel_id="ch3", user_id="alice")
    backdate = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    alert_id = _persist_alert(
        user_id="alice", rule_id="rule_x", created_at=backdate,
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch1",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=2, after_minutes=10, channel_id="ch2",
    )
    add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=3, after_minutes=5, channel_id="ch3",
    )

    fired: list[str] = []

    def fake_deliver(alert, channel):
        fired.append(channel.channel_id)
        return DeliveryResult(success=True, status_code=200)

    # Pass 1 — step 1 (15m window, alert is 30m old).
    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        run_escalation_pass()
    assert fired == ["ch1"]

    # Rewind last_escalated_at to "11 min ago" so step 2 (10m window)
    # is due on the next pass.
    last_esc = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    _set_escalation_state(alert_id, step=1, last_escalated_at=last_esc)
    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        run_escalation_pass()
    assert fired == ["ch1", "ch2"]

    # Rewind for step 3.
    last_esc = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    _set_escalation_state(alert_id, step=2, last_escalated_at=last_esc)
    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        run_escalation_pass()
    assert fired == ["ch1", "ch2", "ch3"]

    # Chain exhausted — next pass is a no-op even though the timer
    # would have elapsed.
    last_esc = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    _set_escalation_state(alert_id, step=3, last_escalated_at=last_esc)
    with patch("engine.alert_delivery.deliver_alert", fake_deliver):
        counts = run_escalation_pass()
    assert counts == {"checked": 0, "escalated": 0, "failed": 0}
    assert fired == ["ch1", "ch2", "ch3"]


# ─── CLI / API surface helpers (v24 operator wiring) ─────────────────────
#
# The CLI's ``escalations list`` handler uses ``_format_chain_for_table``
# to project the chain onto the row-dict shape the table renderer wants.
# It looks up channel names from a (channel_id → DeliveryChannel) map
# and falls back to "(missing)" when a chain step points at a deleted
# channel. These two cases pin both halves of that contract.


def test_format_chain_for_table_renders_channel_names() -> None:
    """The helper substitutes channel names from the lookup map so
    operators see "Trading desk Slack" instead of an opaque UUID."""
    from tools.ops_cli import _format_chain_for_table

    _channel(channel_id="ch_one", name="On-call Slack", user_id="alice")
    step1 = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch_one",
    )
    assert step1 is not None
    chain = get_escalation_chain("rule_x", user_id="alice")

    from engine.alert_delivery import load_channels
    channels = load_channels(user_id="alice")
    by_id = {c.channel_id: c for c in channels}

    rows = _format_chain_for_table(chain, by_id)
    assert len(rows) == 1
    assert rows[0]["step"] == 1
    assert rows[0]["after_minutes"] == 15
    assert rows[0]["channel"] == "On-call Slack"


def test_format_chain_for_table_handles_missing_channel() -> None:
    """When a chain step points at a channel that no longer exists in
    the lookup, the helper substitutes "(missing)" so the operator
    output is still legible."""
    from tools.ops_cli import _format_chain_for_table

    _channel(channel_id="ch_one", name="Slack", user_id="alice")
    step1 = add_escalation_step(
        rule_id="rule_x", user_id="alice",
        step_number=1, after_minutes=15, channel_id="ch_one",
    )
    assert step1 is not None
    chain = get_escalation_chain("rule_x", user_id="alice")

    # Empty lookup — simulates the "channel deleted out from under
    # the chain" case.
    rows = _format_chain_for_table(chain, {})
    assert len(rows) == 1
    assert rows[0]["channel"] == "(missing)"
    assert rows[0]["channel_id"] == "ch_one"[:10]
