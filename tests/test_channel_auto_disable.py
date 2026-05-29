"""Tests for the channel auto-disable circuit breaker.

Covers the helpers in ``engine.alert_delivery``:
  * ``get_consecutive_failures`` (read-only, zero default, never raise)
  * ``record_delivery_failure`` (increment, return new count)
  * ``record_delivery_success`` (reset to 0)
  * ``reset_consecutive_failures`` (operator-triggered zero + flag clear)
  * ``check_and_auto_disable`` (no-op below threshold, full
    disable + alert + audit at threshold, per-user scoping, never
    raise on bad input, idempotent on already-disabled)
  * ``is_auto_disabled`` (kv_state flag read)

And the integration in ``deliver_alert``:
  * 10 consecutive failures auto-disable the channel
  * A success after partial failures resets the counter
  * The counter resets on success even after partial failures

Each test uses the standard tmp-path SQLite isolation fixture so no
test touches the real ``cache/ship_tracker.db``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from engine import alert_delivery
from engine.alert_delivery import (
    AUTO_DISABLE_THRESHOLD,
    DeliveryChannel,
    check_and_auto_disable,
    deliver_alert,
    get_consecutive_failures,
    is_auto_disabled,
    load_channels,
    record_delivery_failure,
    record_delivery_success,
    reset_consecutive_failures,
    save_channel,
)
from engine.alert_engine_v2 import ShippingAlert


# ─── Fixture: isolate SQLite DB per test ──────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _make_alert(severity: str = "HIGH", *, alert_id: str = "a1") -> ShippingAlert:
    return ShippingAlert(
        alert_id=alert_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        alert_type="STOCK_MOVE",
        severity=severity,
        title="t",
        body="b",
        ticker="ZIM",
        route_id="",
        port_locode="",
        value=1.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=False,
    )


def _make_channel(
    *,
    channel_id: str = "c1",
    name: str = "ch1",
    kind: str = "webhook",
    target: str = "https://example.com/hook",
    enabled: bool = True,
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=channel_id,
        name=name,
        kind=kind,
        target=target,
        severity_threshold="LOW",
        enabled=enabled,
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


# ─── Helper-level tests ───────────────────────────────────────────────────


def test_get_consecutive_failures_zero_for_new_channel() -> None:
    """A brand-new channel has no kv_state row → counter reads 0."""
    assert get_consecutive_failures("brand-new-channel", user_id="alice") == 0


def test_record_delivery_failure_increments_counter() -> None:
    """Each call increments by 1 and returns the new count."""
    assert record_delivery_failure("c1", user_id="alice") == 1
    assert record_delivery_failure("c1", user_id="alice") == 2
    assert record_delivery_failure("c1", user_id="alice") == 3
    assert get_consecutive_failures("c1", user_id="alice") == 3


def test_record_delivery_success_resets_to_zero() -> None:
    """Success path zeros the counter (the DELETE branch)."""
    record_delivery_failure("c1", user_id="alice")
    record_delivery_failure("c1", user_id="alice")
    assert get_consecutive_failures("c1", user_id="alice") == 2
    record_delivery_success("c1", user_id="alice")
    assert get_consecutive_failures("c1", user_id="alice") == 0


def test_reset_consecutive_failures_zeros_counter() -> None:
    """Operator-triggered reset clears the counter AND returns True."""
    for _ in range(5):
        record_delivery_failure("c1", user_id="alice")
    assert get_consecutive_failures("c1", user_id="alice") == 5
    assert reset_consecutive_failures("c1", user_id="alice") is True
    assert get_consecutive_failures("c1", user_id="alice") == 0


def test_check_and_auto_disable_below_threshold_no_action() -> None:
    """Counter at AUTO_DISABLE_THRESHOLD - 1 → no-op, channel stays on."""
    ch = _make_channel(channel_id="c-below")
    save_channel(ch, user_id="alice")
    for _ in range(AUTO_DISABLE_THRESHOLD - 1):
        record_delivery_failure("c-below", user_id="alice")
    disabled = check_and_auto_disable(ch, user_id="alice")
    assert disabled is False
    assert ch.enabled is True
    # And on disk
    channels = load_channels(user_id="alice")
    assert channels[0].enabled is True


def test_check_and_auto_disable_at_threshold_disables_channel() -> None:
    """At threshold, returns True + flips enabled on disk."""
    ch = _make_channel(channel_id="c-thresh")
    save_channel(ch, user_id="alice")
    for _ in range(AUTO_DISABLE_THRESHOLD):
        record_delivery_failure("c-thresh", user_id="alice")
    disabled = check_and_auto_disable(
        ch, user_id="alice", last_error="HTTP 404: gone",
    )
    assert disabled is True
    assert ch.enabled is False
    # On disk
    channels = load_channels(user_id="alice")
    assert channels[0].enabled is False
    # Counter is preserved so the operator sees what tripped it
    assert get_consecutive_failures("c-thresh", user_id="alice") == AUTO_DISABLE_THRESHOLD
    # The auto-disabled flag is set
    assert is_auto_disabled("c-thresh", user_id="alice") is True


def test_check_and_auto_disable_fires_alert_via_save_alerts() -> None:
    """Auto-disable persists a CHANNEL_AUTO_DISABLED alert through the
    normal pipeline so other channels / digests pick it up."""
    from engine.alert_engine_v2 import load_alerts

    ch = _make_channel(channel_id="c-alert", name="Webhook backup")
    save_channel(ch, user_id="alice")
    for _ in range(AUTO_DISABLE_THRESHOLD):
        record_delivery_failure("c-alert", user_id="alice")
    check_and_auto_disable(
        ch, user_id="alice", last_error="HTTP 500: gateway timeout",
    )
    alerts = load_alerts(user_id="alice")
    matches = [a for a in alerts if a.alert_type == "CHANNEL_AUTO_DISABLED"]
    assert len(matches) == 1
    assert matches[0].severity == "HIGH"
    assert "Webhook backup" in matches[0].title
    assert str(AUTO_DISABLE_THRESHOLD) in matches[0].title
    assert "HTTP 500" in matches[0].body  # the last_error excerpt


def test_check_and_auto_disable_records_audit_event() -> None:
    """Auto-disable writes a ``channel_auto_disabled`` audit row."""
    from auth.audit import query_audit

    ch = _make_channel(channel_id="c-audit", name="alpha")
    save_channel(ch, user_id="alice")
    for _ in range(AUTO_DISABLE_THRESHOLD):
        record_delivery_failure("c-audit", user_id="alice")
    check_and_auto_disable(ch, user_id="alice", last_error="boom")
    events = query_audit(action="channel_auto_disabled")
    assert any(
        getattr(e, "entity_id", "") == "c-audit" for e in events
    ), f"no channel_auto_disabled audit row found: {events}"


def test_check_and_auto_disable_per_user_scoping() -> None:
    """Alice's failure counter does not affect Bob's channel of the
    same channel_id — the kv_state key segregates by user_id."""
    ch_alice = _make_channel(channel_id="shared-id", name="alice's ch")
    ch_bob = _make_channel(channel_id="shared-id", name="bob's ch")
    save_channel(ch_alice, user_id="alice")
    save_channel(ch_bob, user_id="bob")
    # Trip alice past threshold
    for _ in range(AUTO_DISABLE_THRESHOLD):
        record_delivery_failure("shared-id", user_id="alice")
    # Bob's counter still 0
    assert get_consecutive_failures("shared-id", user_id="bob") == 0
    # Bob's check is a no-op
    assert check_and_auto_disable(ch_bob, user_id="bob") is False
    assert ch_bob.enabled is True


def test_check_and_auto_disable_never_raises_on_bad_input() -> None:
    """Helpers must NEVER raise on missing/None/bad arguments — the
    breaker is operator hygiene, not correctness."""
    # No channel object — fall through the defensive top-level except.
    assert check_and_auto_disable(None, user_id="alice") is False  # type: ignore[arg-type]
    # Channel with no channel_id attr — same.
    class _BadCh:
        enabled = True

    assert check_and_auto_disable(_BadCh(), user_id="alice") is False  # type: ignore[arg-type]


def test_all_helpers_never_raise_on_bad_input() -> None:
    """get/record/reset must also never raise."""
    # Empty channel_id, empty user_id — kv_state writes/reads still work
    # but should not error.
    assert get_consecutive_failures("", user_id="") == 0
    assert isinstance(record_delivery_failure("", user_id=""), int)
    record_delivery_success("", user_id="")
    assert reset_consecutive_failures("", user_id="") is True


def test_get_consecutive_failures_handles_corrupted_kv_state_row() -> None:
    """A non-int 'value' in kv_state (corruption) reads as 0 rather
    than raising or returning garbage."""
    from state.db import get_connection

    conn = get_connection()
    now_iso = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (
                "channel_consecutive_failures:alice:c-corrupt",
                "not-an-int",
                now_iso,
            ),
        )
    assert get_consecutive_failures("c-corrupt", user_id="alice") == 0


def test_manual_reenable_does_not_re_disable_until_next_success() -> None:
    """Auto-disable preserves the counter at threshold. If the operator
    flips enabled=True back on WITHOUT clearing the counter,
    check_and_auto_disable would re-disable on the next failure.
    Verify: counter stays at threshold until reset OR until next
    success."""
    ch = _make_channel(channel_id="c-reenable")
    save_channel(ch, user_id="alice")
    for _ in range(AUTO_DISABLE_THRESHOLD):
        record_delivery_failure("c-reenable", user_id="alice")
    check_and_auto_disable(ch, user_id="alice")
    assert ch.enabled is False
    # Counter preserved
    assert get_consecutive_failures("c-reenable", user_id="alice") == AUTO_DISABLE_THRESHOLD
    # Operator manually re-enables WITHOUT resetting → counter still high
    ch.enabled = True
    save_channel(ch, user_id="alice")
    assert get_consecutive_failures("c-reenable", user_id="alice") == AUTO_DISABLE_THRESHOLD
    # But a single success path clears it
    record_delivery_success("c-reenable", user_id="alice")
    assert get_consecutive_failures("c-reenable", user_id="alice") == 0


# ─── Integration: deliver_alert + auto-disable wiring ─────────────────────


def test_deliver_alert_10_failures_disables_channel(monkeypatch) -> None:
    """A webhook that returns HTTP 500 ten times in a row trips the
    breaker on the 10th call. The 10th deliver_alert call also flips
    enabled=False; the 11th would short-circuit on the disabled check."""
    ch = _make_channel(channel_id="c-10x", name="flaky webhook")
    save_channel(ch, user_id="")

    def fake_post(url, json=None, timeout=None, **kw):
        return _FakeResponse(status_code=500, text="boom")

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)

    for i in range(AUTO_DISABLE_THRESHOLD):
        result = deliver_alert(_make_alert(alert_id=f"a-{i}"), ch)
        # Every call returns the failed result; the 10th also flips enabled.
        assert result.success is False
    # Channel is now disabled in-memory + on disk
    assert ch.enabled is False
    channels = load_channels(user_id="")
    assert all(c.enabled is False for c in channels if c.channel_id == "c-10x")


def test_deliver_alert_5_failures_then_success_resets_counter(monkeypatch) -> None:
    """5 failures followed by a success → counter goes to 0 (the
    AUTO_DISABLE_RESET_ON_SUCCESS contract)."""
    ch = _make_channel(channel_id="c-5then1")
    save_channel(ch, user_id="")

    # First 5 calls fail
    state = {"status": 500}

    def fake_post(url, json=None, timeout=None, **kw):
        return _FakeResponse(status_code=state["status"])

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)

    for i in range(5):
        deliver_alert(_make_alert(alert_id=f"a-{i}"), ch)
    assert get_consecutive_failures("c-5then1", user_id="") == 5
    # Now flip to success
    state["status"] = 200
    deliver_alert(_make_alert(alert_id="a-good"), ch)
    assert get_consecutive_failures("c-5then1", user_id="") == 0
    # Channel is still enabled (we never crossed the threshold)
    assert ch.enabled is True


def test_deliver_alert_counter_resets_on_success_even_after_partial_failures(
    monkeypatch,
) -> None:
    """Repeat the fail-then-succeed cycle a few times to confirm the
    counter never accumulates across success boundaries."""
    ch = _make_channel(channel_id="c-cycle")
    save_channel(ch, user_id="")

    state = {"status": 500}

    def fake_post(url, json=None, timeout=None, **kw):
        return _FakeResponse(status_code=state["status"])

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)

    # 3 fail, 1 success, 4 fail, 1 success, 2 fail, 1 success
    for cycle_fail, _ in [(3, 0), (4, 0), (2, 0)]:
        state["status"] = 500
        for i in range(cycle_fail):
            deliver_alert(_make_alert(alert_id=f"a-c-{i}"), ch)
        state["status"] = 200
        deliver_alert(_make_alert(alert_id="a-c-ok"), ch)
        assert get_consecutive_failures("c-cycle", user_id="") == 0
    # Channel was never disabled
    assert ch.enabled is True


def test_auto_disable_is_scoped_update_preserving_owner_and_target() -> None:
    """Regression (security): auto-disable must flip ONLY `enabled` via a
    scoped UPDATE keyed on channel_id — never a full-row UPSERT that would
    rewrite the channel's owner to the alert owner's id (cross-user hijack),
    downgrade an encrypted target to plaintext, or clobber concurrent edits.

    Trigger the breaker under an alert-owner scope ('') that differs from
    the channel's owner ('alice') and assert the row's user_id + target are
    preserved while enabled flips to 0.
    """
    from state.db import get_connection

    secret_target = "https://hooks.slack.com/services/T/B/SEKRIT"
    ch = _make_channel(channel_id="c-scoped", kind="slack", target=secret_target)
    save_channel(ch, user_id="alice")

    # Breaker keys its counter by the passed user_id; trip it under ''.
    for _ in range(AUTO_DISABLE_THRESHOLD):
        record_delivery_failure("c-scoped", user_id="")
    assert check_and_auto_disable(ch, user_id="", last_error="connection error: x") is True

    row = get_connection().execute(
        "SELECT user_id, enabled, target FROM delivery_channels "
        "WHERE channel_id = ?",
        ("c-scoped",),
    ).fetchone()
    assert row is not None
    assert row["user_id"] == "alice"            # NOT rewritten to '' — no hijack
    assert int(row["enabled"]) == 0             # disabled
    assert row["target"] == secret_target       # not clobbered / downgraded


# ─── severity_threshold normalization at save (fail-loud, not fail-open) ─────

def _chan_with_threshold(channel_id: str, threshold: str) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=channel_id, name="n", kind="webhook",
        target="https://example.com/hook", severity_threshold=threshold,
        enabled=True,
    )


def test_save_channel_normalizes_empty_threshold_to_low() -> None:
    """An empty threshold would make _meets_threshold deliver EVERY severity
    unpredictably; save normalizes it to the explicit deliver-all band."""
    save_channel(_chan_with_threshold("c-empty", ""), user_id="alice")
    loaded = load_channels(user_id="alice")
    assert loaded[0].severity_threshold == "LOW"


def test_save_channel_normalizes_unknown_threshold_to_low() -> None:
    save_channel(_chan_with_threshold("c-typo", "HGIH"), user_id="alice")
    loaded = load_channels(user_id="alice")
    assert loaded[0].severity_threshold == "LOW"


def test_save_channel_keeps_canonical_threshold() -> None:
    save_channel(_chan_with_threshold("c-ok", "HIGH"), user_id="alice")
    loaded = load_channels(user_id="alice")
    assert loaded[0].severity_threshold == "HIGH"
