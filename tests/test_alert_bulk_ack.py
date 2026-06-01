"""Tests for engine.alert_engine_v2 bulk acknowledgement (v19).

Operators today click each alert individually to ack — when 30 LOW alerts
fire, that's 30 clicks. v19 introduces:

  * Optional ``note`` kwarg on the single ``acknowledge_alert``.
  * New ``bulk_acknowledge_alerts(alert_ids, *, note, user_id)`` that
    acks a SET of alerts in ONE SQL round-trip with per-user scoping
    and returns a precise per-bucket count dict.
  * New ``bulk_acknowledge_alerts_by_filter`` convenience helper that
    resolves the id set from a WHERE clause (severity tier-or-worse,
    alert_type, before_iso) and delegates to bulk_acknowledge_alerts.
  * v19 schema migration adding two NULLable columns to ``alerts``:
    ``acknowledged_note`` and ``acknowledged_by_user_id``.

This file pins behaviour the codebase MUST guarantee:

  - bulk_acknowledge_alerts with 5 IDs → all acked, counts split right
  - bulk_acknowledge_alerts with mixed inputs (valid / already-acked /
    not-found) → counts in the right buckets, sum equals len(input)
  - bulk_acknowledge_alerts persists the note on each acked row
  - bulk_acknowledge_alerts per-user scoping (alice can't ack bob's
    alerts even with their IDs)
  - bulk_acknowledge_alerts empty list → returns zero counts, no
    audit event recorded
  - bulk_acknowledge_alerts records ONE audit event per call with a
    note truncated to 200 chars in the detail payload
  - bulk_acknowledge_alerts NEVER raises: monkeypatch DB to error →
    returns "all failed" counts, sum equals input length
  - bulk_acknowledge_alerts_by_filter severity=HIGH acks both HIGH and
    CRITICAL rows (tier-or-worse semantics)
  - bulk_acknowledge_alerts_by_filter alert_type=BDI_MOVE acks only
    that type
  - bulk_acknowledge_alerts_by_filter before_iso=X acks only rows
    older than X
  - bulk_acknowledge_alerts_by_filter combined filters AND together
  - acknowledge_alert (single, v19) with note → note persists on the
    row, ack-by-user-id stamped
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine import alert_engine_v2 as engv2
from engine.alert_engine_v2 import (
    ShippingAlert,
    _make,
    acknowledge_alert,
    bulk_acknowledge_alerts,
    bulk_acknowledge_alerts_by_filter,
    load_alerts,
    save_alerts,
)


# ─── Fixture: isolate SQLite per test ──────────────────────────────────────

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

def _mk(
    *,
    severity: str = "HIGH",
    alert_type: str = "BDI_MOVE",
    ticker: str = "",
    created_at: str | None = None,
) -> ShippingAlert:
    """Build a ShippingAlert with a distinct dedup_key — set a unique
    ticker per call so the v14 dedup_key collapse does NOT merge rows
    these tests need to count separately."""
    a = _make(alert_type, severity, "title", "body", ticker=ticker)
    if created_at is not None:
        a.created_at = created_at
    return a


def _ack_state(alert_id: str) -> dict:
    """Read the v19 columns directly from SQLite so the test can assert
    on note / ack-by attribution without going through load_alerts
    (which projects through the v1-shape dataclass and drops them)."""
    from state.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT acknowledged, acknowledged_at, acknowledged_note, "
        "acknowledged_by_user_id FROM alerts WHERE alert_id = ?",
        (alert_id,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "acknowledged": bool(row["acknowledged"]),
        "acknowledged_at": row["acknowledged_at"],
        "acknowledged_note": row["acknowledged_note"],
        "acknowledged_by_user_id": row["acknowledged_by_user_id"],
    }


# ─── bulk_acknowledge_alerts: happy path ───────────────────────────────────

def test_bulk_acknowledge_acks_all_when_all_valid_and_unacked() -> None:
    """5 distinct unacked alerts → all 5 acked, counts split correctly,
    sum of buckets equals len(input)."""
    alerts = [_mk(ticker=f"T{i}") for i in range(5)]
    save_alerts(alerts)
    ids = [a.alert_id for a in alerts]

    result = bulk_acknowledge_alerts(ids)

    assert result == {
        "acked": 5,
        "skipped_already_acked": 0,
        "not_found": 0,
        "failed": 0,
    }
    assert sum(result.values()) == len(ids)
    # And every alert's acknowledged flag flipped.
    for a in alerts:
        st = _ack_state(a.alert_id)
        assert st["acknowledged"] is True


def test_bulk_acknowledge_splits_mixed_input_into_buckets() -> None:
    """3 valid unacked + 1 already-acked + 1 not-found should produce
    acked=3, skipped_already_acked=1, not_found=1, failed=0."""
    alerts = [_mk(ticker=f"T{i}") for i in range(4)]
    save_alerts(alerts)

    # Pre-ack one row so it lands in skipped_already_acked.
    acknowledge_alert(alerts[3].alert_id)

    ids = [
        alerts[0].alert_id,
        alerts[1].alert_id,
        alerts[2].alert_id,
        alerts[3].alert_id,        # already-acked
        "not-a-real-id-99999",     # not-found
    ]
    result = bulk_acknowledge_alerts(ids)

    assert result["acked"] == 3
    assert result["skipped_already_acked"] == 1
    assert result["not_found"] == 1
    assert result["failed"] == 0
    assert sum(result.values()) == len(ids)


# ─── bulk_acknowledge_alerts: note persistence ─────────────────────────────

def test_bulk_acknowledge_persists_note_on_every_acked_row() -> None:
    """The free-form note passed to bulk_acknowledge_alerts must land
    in the acknowledged_note column on every acked row."""
    alerts = [_mk(ticker=f"T{i}") for i in range(3)]
    save_alerts(alerts)
    ids = [a.alert_id for a in alerts]

    result = bulk_acknowledge_alerts(ids, note="ops triage 2025-04-01")
    assert result["acked"] == 3

    for a in alerts:
        st = _ack_state(a.alert_id)
        assert st["acknowledged_note"] == "ops triage 2025-04-01"


# ─── bulk_acknowledge_alerts: per-user scoping ─────────────────────────────

def test_bulk_acknowledge_per_user_scoping_blocks_cross_user_ack() -> None:
    """Bob cannot ack alice's alerts even when he supplies the exact
    alert_ids — the per-user scoping filter must drop the rows from the
    UPDATE and the ids must land in ``not_found``."""
    alice_alerts = [_mk(ticker=f"A{i}") for i in range(3)]
    bob_alerts   = [_mk(ticker=f"B{i}") for i in range(2)]
    save_alerts(alice_alerts, user_id="alice")
    save_alerts(bob_alerts, user_id="bob")

    # Bob tries to ack alice's three alerts.
    alice_ids = [a.alert_id for a in alice_alerts]
    result = bulk_acknowledge_alerts(alice_ids, user_id="bob")

    # All three land in not_found from bob's perspective (he can't see
    # them — they belong to alice and the legacy bucket is empty).
    assert result["acked"] == 0
    assert result["not_found"] == 3
    assert result["skipped_already_acked"] == 0
    assert sum(result.values()) == len(alice_ids)

    # And alice's alerts are still unacked.
    for a in alice_alerts:
        st = _ack_state(a.alert_id)
        assert st["acknowledged"] is False


# ─── bulk_acknowledge_alerts: empty input ──────────────────────────────────

def test_bulk_acknowledge_empty_list_returns_zeros_and_no_audit() -> None:
    """Empty alert_ids list short-circuits to all-zero counts AND must
    NOT record an audit event (a no-op call is not auditable activity)."""
    # Snapshot audit row count before + after.
    from auth.audit import query_audit
    before = query_audit(action="bulk_acknowledge")
    result = bulk_acknowledge_alerts([])
    after = query_audit(action="bulk_acknowledge")

    assert result == {
        "acked": 0,
        "skipped_already_acked": 0,
        "not_found": 0,
        "failed": 0,
    }
    assert len(after) == len(before)


# ─── bulk_acknowledge_alerts: audit event with truncated note ──────────────

def test_bulk_acknowledge_records_one_audit_event_with_truncated_note() -> None:
    """A single bulk ack call must record EXACTLY ONE audit event with
    action='bulk_acknowledge' whose detail carries (a) the input ids,
    (b) the output counts, (c) the note truncated to 200 chars."""
    from auth.audit import query_audit

    alerts = [_mk(ticker=f"T{i}") for i in range(2)]
    save_alerts(alerts)
    ids = [a.alert_id for a in alerts]

    long_note = "x" * 500  # 500 chars; should truncate to 200 in audit
    before = len(query_audit(action="bulk_acknowledge"))
    bulk_acknowledge_alerts(ids, note=long_note)
    events = query_audit(action="bulk_acknowledge")

    # Exactly one new event landed.
    assert len(events) == before + 1
    ev = events[0]  # query_audit returns newest-first
    assert ev.action == "bulk_acknowledge"
    detail = ev.detail_json
    assert detail.get("count") == 2
    assert detail.get("ids") == ids
    note_in_audit = detail.get("note_truncated_to_200_chars")
    assert note_in_audit is not None
    assert len(note_in_audit) == 200
    # Sanity: the full note still landed on each row.
    for a in alerts:
        assert _ack_state(a.alert_id)["acknowledged_note"] == long_note


# ─── bulk_acknowledge_alerts: never raises on DB error ─────────────────────

def test_bulk_acknowledge_never_raises_when_db_errors(monkeypatch) -> None:
    """Monkeypatch ``state.db.get_connection`` so the engine's lazy
    import sees a broken connection helper. The bulk call MUST swallow
    the error and return a count dict whose ``failed`` bucket equals
    the input length (sum invariant preserved)."""
    alerts = [_mk(ticker=f"T{i}") for i in range(3)]
    save_alerts(alerts)
    ids = [a.alert_id for a in alerts]

    # Replace get_connection so the next lookup inside
    # bulk_acknowledge_alerts hits a broken helper. The engine
    # re-imports state.db lazily inside the function (matching the
    # rest of the engine module) so this monkeypatch covers it.
    def _broken_get_connection():
        raise RuntimeError("simulated SQLite failure")

    monkeypatch.setattr("state.db.get_connection", _broken_get_connection)

    # MUST NOT raise — every count lands in ``failed``.
    result = bulk_acknowledge_alerts(ids)
    assert result == {
        "acked": 0,
        "skipped_already_acked": 0,
        "not_found": 0,
        "failed": len(ids),
    }
    assert sum(result.values()) == len(ids)


# ─── bulk_acknowledge_alerts_by_filter: severity ───────────────────────────

def test_bulk_acknowledge_by_filter_severity_high_acks_high_and_critical() -> None:
    """severity='HIGH' uses tier-or-worse semantics — both HIGH and
    CRITICAL rows are acked; MEDIUM and LOW are left untouched."""
    alerts = [
        _mk(ticker="A", severity="CRITICAL"),
        _mk(ticker="B", severity="HIGH"),
        _mk(ticker="C", severity="MEDIUM"),
        _mk(ticker="D", severity="LOW"),
    ]
    save_alerts(alerts)

    result = bulk_acknowledge_alerts_by_filter(severity="HIGH")
    # CRITICAL + HIGH = 2 acked.
    assert result["acked"] == 2
    assert _ack_state(alerts[0].alert_id)["acknowledged"] is True
    assert _ack_state(alerts[1].alert_id)["acknowledged"] is True
    assert _ack_state(alerts[2].alert_id)["acknowledged"] is False
    assert _ack_state(alerts[3].alert_id)["acknowledged"] is False


# ─── bulk_acknowledge_alerts_by_filter: alert_type ─────────────────────────

def test_bulk_acknowledge_by_filter_alert_type_acks_only_matching_type() -> None:
    """alert_type='BDI_MOVE' should ack only BDI_MOVE rows; other
    alert types remain unacked."""
    alerts = [
        _mk(ticker="A", alert_type="BDI_MOVE"),
        _mk(ticker="B", alert_type="BDI_MOVE"),
        _mk(ticker="C", alert_type="STOCK_MOVE"),
        _mk(ticker="D", alert_type="CONGESTION"),
    ]
    save_alerts(alerts)

    result = bulk_acknowledge_alerts_by_filter(alert_type="BDI_MOVE")
    assert result["acked"] == 2
    assert _ack_state(alerts[0].alert_id)["acknowledged"] is True
    assert _ack_state(alerts[1].alert_id)["acknowledged"] is True
    assert _ack_state(alerts[2].alert_id)["acknowledged"] is False
    assert _ack_state(alerts[3].alert_id)["acknowledged"] is False


# ─── bulk_acknowledge_alerts_by_filter: before_iso ─────────────────────────

def test_bulk_acknowledge_by_filter_before_iso_acks_only_older() -> None:
    """before_iso=X must ack ONLY rows whose created_at < X."""
    now = datetime.now(timezone.utc)
    old_ts   = (now - timedelta(days=10)).isoformat()
    fresh_ts = (now - timedelta(days=1)).isoformat()
    alerts = [
        _mk(ticker="A", created_at=old_ts),
        _mk(ticker="B", created_at=old_ts),
        _mk(ticker="C", created_at=fresh_ts),
    ]
    save_alerts(alerts)

    cutoff = (now - timedelta(days=7)).isoformat()
    result = bulk_acknowledge_alerts_by_filter(before_iso=cutoff)
    # Two rows older than 7 days → acked.
    assert result["acked"] == 2
    assert _ack_state(alerts[0].alert_id)["acknowledged"] is True
    assert _ack_state(alerts[1].alert_id)["acknowledged"] is True
    assert _ack_state(alerts[2].alert_id)["acknowledged"] is False


# ─── bulk_acknowledge_alerts_by_filter: combined filters ───────────────────

def test_bulk_acknowledge_by_filter_combines_filters_with_and() -> None:
    """severity + alert_type + before_iso all set must AND together —
    only rows matching all three are acked."""
    now = datetime.now(timezone.utc)
    old_ts   = (now - timedelta(days=10)).isoformat()
    fresh_ts = (now - timedelta(days=1)).isoformat()

    alerts = [
        # Match all three filters → acked.
        _mk(ticker="A", severity="HIGH", alert_type="BDI_MOVE",
            created_at=old_ts),
        # Right type + age but MEDIUM (HIGH tier-or-worse excludes
        # MEDIUM) → not acked.
        _mk(ticker="B", severity="MEDIUM", alert_type="BDI_MOVE",
            created_at=old_ts),
        # Right severity + age but wrong type → not acked.
        _mk(ticker="C", severity="HIGH", alert_type="STOCK_MOVE",
            created_at=old_ts),
        # Right severity + type but too recent → not acked.
        _mk(ticker="D", severity="HIGH", alert_type="BDI_MOVE",
            created_at=fresh_ts),
    ]
    save_alerts(alerts)

    cutoff = (now - timedelta(days=7)).isoformat()
    result = bulk_acknowledge_alerts_by_filter(
        severity="HIGH",
        alert_type="BDI_MOVE",
        before_iso=cutoff,
    )
    assert result["acked"] == 1
    assert _ack_state(alerts[0].alert_id)["acknowledged"] is True
    for a in alerts[1:]:
        assert _ack_state(a.alert_id)["acknowledged"] is False


# ─── acknowledge_alert (single, v19): note + user_id ───────────────────────

def test_acknowledge_alert_single_with_note_persists_note_and_user_id() -> None:
    """The single-ack helper must accept the v19 ``note`` kwarg and
    persist both the note and the resolved user_id on the alert row."""
    a = _mk(ticker="T1")
    save_alerts([a], user_id="alice")

    acknowledge_alert(a.alert_id, user_id="alice", note="seen and triaged")

    st = _ack_state(a.alert_id)
    assert st["acknowledged"] is True
    assert st["acknowledged_note"] == "seen and triaged"
    assert st["acknowledged_by_user_id"] == "alice"
