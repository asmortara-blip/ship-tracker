"""Tests for ``auth.audit`` — the v10 audit-log foundation.

Exercises the dataclass shape, the four public helpers (``record_audit``
/ ``query_audit`` / ``prune_old_audit_events``), and the integration of
audit hooks at the eleven documented touchpoints (alert ACK / save-rules
/ reset-rules / channel CRUD / report deletion / share-link generation
/ signup / login).

The defining properties under test:

  * ``record_audit`` NEVER raises — even when the DB write throws.
  * ``record_audit`` stamps the row with the active user id when the
    caller does not supply one, and falls back to ``""`` when no user
    is set.
  * ``query_audit`` filters by ``user_id`` / ``action`` / ``entity_type``
    / ``since`` independently, sorts newest-first, and caps at ``limit``.
  * ``prune_old_audit_events`` deletes only rows strictly older than
    the cutoff and refuses to wipe everything on a non-positive
    ``retention_days``.
  * Each of the eleven hooks fires when its host function is called,
    with the expected ``action`` / ``entity_type`` / ``entity_id``
    payload.

The filename is ``test_auth_audit.py`` rather than ``test_audit.py``
because the latter already exists for ``tools.styles_audit`` smoke
tests — keeping the two test files separate prevents name collision in
pytest discovery.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── AuditEvent dataclass ─────────────────────────────────────────────────

def test_auditevent_dataclass_fields() -> None:
    """The dataclass exposes exactly the seven documented fields."""
    from auth.audit import AuditEvent

    fields = set(AuditEvent.__dataclass_fields__)
    assert fields == {
        "event_id", "created_at", "user_id", "action",
        "entity_type", "entity_id", "detail_json",
    }


def test_auditevent_detail_json_defaults_to_empty_dict() -> None:
    """Construct without detail_json → empty dict by default."""
    from auth.audit import AuditEvent

    ev = AuditEvent(
        event_id="e1",
        created_at="2026-05-22T00:00:00+00:00",
        user_id="u1",
        action="ack_alert",
        entity_type="alert",
        entity_id="a1",
    )
    assert ev.detail_json == {}


def test_auditevent_json_roundtrip() -> None:
    """A complex detail dict round-trips through json.dumps + json.loads."""
    from auth.audit import AuditEvent

    payload = {"count": 7, "nested": {"k": [1, 2]}, "flag": True}
    ev = AuditEvent(
        event_id="e1",
        created_at="2026-05-22T00:00:00+00:00",
        user_id="u1",
        action="save_rules",
        entity_type="",
        entity_id="",
        detail_json=payload,
    )
    encoded = json.dumps(ev.detail_json)
    decoded = json.loads(encoded)
    assert decoded == payload


# ─── record_audit: happy path ─────────────────────────────────────────────

def test_record_audit_inserts_row() -> None:
    from auth.audit import record_audit
    from state.db import get_connection

    record_audit(
        "ack_alert",
        entity_type="alert",
        entity_id="alert-123",
        detail={"acknowledged_at": "2026-05-22T00:00:00+00:00"},
    )
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM audit_events WHERE action = 'ack_alert'"
    ).fetchone()
    assert row is not None
    assert row["entity_type"] == "alert"
    assert row["entity_id"] == "alert-123"
    parsed = json.loads(row["detail_json"])
    assert parsed["acknowledged_at"] == "2026-05-22T00:00:00+00:00"


def test_record_audit_auto_generates_event_id() -> None:
    """Each call gets a fresh, unique event_id."""
    from auth.audit import record_audit
    from state.db import get_connection

    record_audit("ack_alert", entity_id="a1")
    record_audit("ack_alert", entity_id="a2")
    conn = get_connection()
    ids = {r["event_id"] for r in conn.execute(
        "SELECT event_id FROM audit_events"
    ).fetchall()}
    assert len(ids) == 2  # both rows present
    # IDs should be UUID-like (non-empty strings, ~36 chars).
    for eid in ids:
        assert isinstance(eid, str) and len(eid) >= 32


def test_record_audit_auto_generates_created_at() -> None:
    """The created_at column is an ISO-8601 UTC timestamp near 'now'."""
    from auth.audit import record_audit
    from state.db import get_connection

    record_audit("save_rules", detail={"count": 3})
    conn = get_connection()
    row = conn.execute(
        "SELECT created_at FROM audit_events WHERE action = 'save_rules'"
    ).fetchone()
    parsed = datetime.fromisoformat(row["created_at"])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 60


def test_record_audit_explicit_user_id_overrides_session() -> None:
    """An explicit user_id is used verbatim, not the session value."""
    from auth.audit import record_audit
    from state.db import get_connection

    # No streamlit session set up — current_user_id() returns "".
    # An explicit user_id MUST land in the row regardless.
    record_audit("ack_alert", entity_id="a1", user_id="explicit-uid")
    conn = get_connection()
    row = conn.execute(
        "SELECT user_id FROM audit_events WHERE entity_id = 'a1'"
    ).fetchone()
    assert row["user_id"] == "explicit-uid"


def test_record_audit_falls_back_to_current_user_id(monkeypatch) -> None:
    """When user_id is None, ``state.user_scope.current_user_id`` is
    consulted. We patch it to return a sentinel string."""
    import state.user_scope as scope

    monkeypatch.setattr(scope, "current_user_id", lambda: "session-uid-9")

    from auth.audit import record_audit
    from state.db import get_connection

    record_audit("save_rules", detail={"count": 1})
    conn = get_connection()
    row = conn.execute(
        "SELECT user_id FROM audit_events WHERE action = 'save_rules'"
    ).fetchone()
    assert row["user_id"] == "session-uid-9"


def test_record_audit_empty_user_when_no_session() -> None:
    """No session + no explicit user_id → empty string user_id."""
    from auth.audit import record_audit
    from state.db import get_connection

    record_audit("reset_rules")
    conn = get_connection()
    row = conn.execute(
        "SELECT user_id FROM audit_events WHERE action = 'reset_rules'"
    ).fetchone()
    assert row["user_id"] == ""


def test_record_audit_handles_none_detail() -> None:
    """``detail=None`` serializes to ``'{}'`` rather than failing."""
    from auth.audit import record_audit
    from state.db import get_connection

    record_audit("reset_rules", detail=None)
    conn = get_connection()
    row = conn.execute(
        "SELECT detail_json FROM audit_events WHERE action = 'reset_rules'"
    ).fetchone()
    assert json.loads(row["detail_json"]) == {}


def test_record_audit_serializes_non_json_payload() -> None:
    """A datetime in detail must NOT raise — default=str downgrades it
    to its repr instead of crashing."""
    from auth.audit import record_audit
    from state.db import get_connection

    record_audit(
        "save_rules",
        detail={"when": datetime(2026, 5, 22, tzinfo=timezone.utc)},
    )
    conn = get_connection()
    row = conn.execute(
        "SELECT detail_json FROM audit_events WHERE action = 'save_rules'"
    ).fetchone()
    parsed = json.loads(row["detail_json"])
    assert isinstance(parsed["when"], str)
    assert "2026-05-22" in parsed["when"]


# ─── record_audit: never raises ────────────────────────────────────────────

def test_record_audit_swallows_empty_action() -> None:
    """An empty action is a programming bug in the caller, not a crash."""
    from auth.audit import record_audit

    # Must not raise.
    record_audit("")
    record_audit("   ")  # whitespace also non-empty but the call won't crash


def test_record_audit_never_raises_on_db_failure() -> None:
    """Mock get_connection to throw; record_audit must swallow."""
    from auth import audit

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("disk full")
        # Must NOT propagate.
        audit.record_audit("ack_alert", entity_id="x")


def test_record_audit_swallow_payload_serialization_failure() -> None:
    """Even if json.dumps blows up entirely, we swallow and write the
    row with an empty detail payload."""
    from auth.audit import record_audit
    from state.db import get_connection

    with patch("auth.audit.json.dumps", side_effect=ValueError("bad")):
        record_audit("ack_alert", detail={"k": "v"})  # no raise

    # And confirm the row still landed (with a fallback empty payload).
    conn = get_connection()
    row = conn.execute(
        "SELECT detail_json FROM audit_events WHERE action = 'ack_alert'"
    ).fetchone()
    assert row is not None
    assert row["detail_json"] == "{}"


# ─── query_audit: filters ─────────────────────────────────────────────────

def _seed_events() -> None:
    """Insert a known set of audit rows used by several query tests."""
    from auth.audit import record_audit

    record_audit("login", entity_type="user", entity_id="u1", user_id="u1")
    record_audit("save_rules", detail={"count": 3}, user_id="u1")
    record_audit("delete_report", entity_type="report",
                 entity_id="r1", user_id="u2")
    record_audit("ack_alert", entity_type="alert",
                 entity_id="a1", user_id="u2")


def test_query_audit_no_filters_returns_all() -> None:
    from auth.audit import query_audit

    _seed_events()
    rows = query_audit()
    assert len(rows) == 4


def test_query_audit_filters_by_user_id() -> None:
    from auth.audit import query_audit

    _seed_events()
    rows = query_audit(user_id="u1")
    assert {r.action for r in rows} == {"login", "save_rules"}
    rows2 = query_audit(user_id="u2")
    assert {r.action for r in rows2} == {"delete_report", "ack_alert"}


def test_query_audit_filters_by_action() -> None:
    from auth.audit import query_audit

    _seed_events()
    rows = query_audit(action="ack_alert")
    assert len(rows) == 1
    assert rows[0].entity_id == "a1"


def test_query_audit_filters_by_entity_type() -> None:
    from auth.audit import query_audit

    _seed_events()
    rows = query_audit(entity_type="report")
    assert len(rows) == 1
    assert rows[0].action == "delete_report"


def test_query_audit_filters_by_since() -> None:
    """Only events with created_at >= since are returned."""
    from auth.audit import record_audit, query_audit
    from state.db import get_connection

    record_audit("login", entity_id="u_old", user_id="u1")
    record_audit("login", entity_id="u_new", user_id="u1")
    conn = get_connection()
    # Backdate the first row.
    conn.execute(
        "UPDATE audit_events SET created_at = ? WHERE entity_id = ?",
        ("2020-01-01T00:00:00+00:00", "u_old"),
    )

    rows = query_audit(since="2025-01-01T00:00:00+00:00")
    ids = {r.entity_id for r in rows}
    assert "u_new" in ids
    assert "u_old" not in ids


def test_query_audit_sorted_newest_first() -> None:
    from auth.audit import record_audit, query_audit
    from state.db import get_connection

    record_audit("login", entity_id="x1", user_id="u")
    record_audit("login", entity_id="x2", user_id="u")
    conn = get_connection()
    # Pin explicit timestamps so the sort is determinate.
    conn.execute(
        "UPDATE audit_events SET created_at = ? WHERE entity_id = ?",
        ("2026-01-01T00:00:00+00:00", "x1"),
    )
    conn.execute(
        "UPDATE audit_events SET created_at = ? WHERE entity_id = ?",
        ("2026-05-22T00:00:00+00:00", "x2"),
    )

    rows = query_audit(action="login")
    assert [r.entity_id for r in rows] == ["x2", "x1"]


def test_query_audit_caps_at_limit() -> None:
    from auth.audit import record_audit, query_audit

    for i in range(20):
        record_audit("ack_alert", entity_id=f"a{i}")
    rows = query_audit(limit=5)
    assert len(rows) == 5


def test_query_audit_limit_zero_returns_empty() -> None:
    from auth.audit import record_audit, query_audit

    record_audit("ack_alert", entity_id="a1")
    assert query_audit(limit=0) == []
    assert query_audit(limit=-1) == []


def test_query_audit_returns_empty_on_db_error() -> None:
    from auth import audit

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("disk on fire")
        result = audit.query_audit()
    assert result == []


# ─── prune_old_audit_events ───────────────────────────────────────────────

def test_prune_old_audit_events_deletes_only_old_rows() -> None:
    from auth.audit import record_audit, prune_old_audit_events
    from state.db import get_connection

    record_audit("ack_alert", entity_id="old")
    record_audit("ack_alert", entity_id="new")
    conn = get_connection()
    # Backdate the "old" row 400 days into the past.
    old_iso = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    conn.execute(
        "UPDATE audit_events SET created_at = ? WHERE entity_id = ?",
        (old_iso, "old"),
    )

    deleted = prune_old_audit_events(retention_days=365)
    assert deleted == 1
    remaining = {r["entity_id"] for r in conn.execute(
        "SELECT entity_id FROM audit_events"
    ).fetchall()}
    assert remaining == {"new"}


def test_prune_old_audit_events_refuses_non_positive() -> None:
    """Zero or negative retention must NOT wipe the table."""
    from auth.audit import record_audit, prune_old_audit_events
    from state.db import get_connection

    record_audit("ack_alert", entity_id="a1")
    assert prune_old_audit_events(0) == 0
    assert prune_old_audit_events(-30) == 0
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"]
    assert n == 1


def test_prune_old_audit_events_never_raises_on_db_error() -> None:
    from auth import audit

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("offline")
        result = audit.prune_old_audit_events(365)
    assert result == 0


# ─── Hook integration: alert engine ───────────────────────────────────────

def test_hook_acknowledge_alert_writes_audit_event() -> None:
    from auth.audit import query_audit
    from engine.alert_engine_v2 import acknowledge_alert

    acknowledge_alert("alert-xyz")
    rows = query_audit(action="ack_alert")
    assert len(rows) == 1
    assert rows[0].entity_type == "alert"
    assert rows[0].entity_id == "alert-xyz"


def test_hook_acknowledge_all_writes_audit_event() -> None:
    from auth.audit import query_audit
    from engine.alert_engine_v2 import acknowledge_all

    acknowledge_all()
    rows = query_audit(action="ack_all_alerts")
    assert len(rows) == 1
    # detail must carry the count even when zero.
    assert "count" in rows[0].detail_json


def test_hook_save_rules_writes_audit_event() -> None:
    from auth.audit import query_audit
    from engine.alert_engine_v2 import save_rules

    save_rules([
        {"id": "r1", "name": "Rule 1"},
        {"id": "r2", "name": "Rule 2"},
    ])
    rows = query_audit(action="save_rules")
    assert len(rows) == 1
    assert rows[0].detail_json["count"] == 2


def test_hook_reset_rules_writes_audit_event() -> None:
    from auth.audit import query_audit
    from engine.alert_engine_v2 import reset_rules

    reset_rules()
    rows = query_audit(action="reset_rules")
    assert len(rows) == 1


# ─── Hook integration: alert delivery ─────────────────────────────────────

def test_hook_save_channel_writes_audit_event() -> None:
    from auth.audit import query_audit
    from engine.alert_delivery import DeliveryChannel, save_channel

    ch = DeliveryChannel(
        channel_id="ch-1",
        name="ops-slack",
        kind="slack",
        target="https://hooks.example.com/abc",
        severity_threshold="HIGH",
        enabled=True,
        created_at="",
    )
    save_channel(ch)
    rows = query_audit(action="save_channel")
    assert len(rows) == 1
    assert rows[0].entity_type == "channel"
    assert rows[0].entity_id == "ch-1"
    assert rows[0].detail_json["name"] == "ops-slack"
    assert rows[0].detail_json["kind"] == "slack"
    # The webhook URL must NOT appear in the audit detail (security).
    assert "https://" not in json.dumps(rows[0].detail_json)


def test_hook_delete_channel_writes_audit_event() -> None:
    from auth.audit import query_audit
    from engine.alert_delivery import delete_channel

    delete_channel("ch-gone")
    rows = query_audit(action="delete_channel")
    assert len(rows) == 1
    assert rows[0].entity_id == "ch-gone"


# ─── Hook integration: report history ─────────────────────────────────────

def _seed_report_row() -> str:
    """Insert a synthetic report_history row directly; return report_id."""
    from state.db import get_connection

    rid = "rpt-test-1"
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO report_history
          (report_id, generated_at, report_date, sentiment_label,
           sentiment_score, risk_level, signal_count, data_quality,
           file_path, file_size_kb)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (rid, "2026-05-22T00:00:00+00:00", "May 22, 2026",
         "NEUTRAL", 0.0, "MODERATE", 3, "FULL",
         "/nonexistent/path.html", 12.0),
    )
    return rid


def test_hook_delete_report_writes_audit_event() -> None:
    from auth.audit import query_audit
    from utils.report_history import delete_report

    rid = _seed_report_row()
    ok = delete_report(rid)
    assert ok is True
    rows = query_audit(action="delete_report")
    assert len(rows) == 1
    assert rows[0].entity_type == "report"
    assert rows[0].entity_id == rid


def test_hook_make_public_writes_audit_event() -> None:
    from auth.audit import query_audit
    from utils.report_history import make_public

    rid = _seed_report_row()
    slug = make_public(rid, expires_in_days=14)
    assert slug is not None
    rows = query_audit(action="make_public")
    assert len(rows) == 1
    assert rows[0].entity_id == rid
    assert rows[0].detail_json["expires_in_days"] == 14
    # The slug must NOT leak into the audit detail (security).
    assert slug not in json.dumps(rows[0].detail_json)


def test_hook_revoke_public_writes_audit_event() -> None:
    from auth.audit import query_audit
    from utils.report_history import make_public, revoke_public

    rid = _seed_report_row()
    make_public(rid)  # creates the slug
    ok = revoke_public(rid)
    assert ok is True
    rows = query_audit(action="revoke_public")
    assert len(rows) == 1
    assert rows[0].entity_id == rid


# ─── Hook integration: auth ──────────────────────────────────────────────

def test_hook_signup_writes_audit_event() -> None:
    from auth.audit import query_audit
    from auth.users import signup

    user = signup("audit_user", "long-enough-password")
    assert user is not None
    rows = query_audit(action="signup")
    assert len(rows) == 1
    assert rows[0].entity_type == "user"
    assert rows[0].entity_id == user.user_id
    assert rows[0].detail_json["username"] == "audit_user"


def test_hook_login_writes_audit_event_only_on_success() -> None:
    from auth.audit import query_audit
    from auth.users import login, signup

    signup("login_audit", "long-enough-password")
    user = login("login_audit", "long-enough-password")
    assert user is not None

    # One signup event + one login event.
    login_rows = query_audit(action="login")
    assert len(login_rows) == 1
    assert login_rows[0].user_id == user.user_id


def test_hook_login_does_not_write_audit_event_on_failure() -> None:
    from auth.audit import query_audit
    from auth.users import login, signup

    signup("user_real", "real-password-12")
    # Wrong password — must not generate a login event.
    bad = login("user_real", "wrong-password-99")
    assert bad is None
    rows = query_audit(action="login")
    assert rows == []

    # Unknown username — same: no login event.
    bad2 = login("ghost", "anything-strong")
    assert bad2 is None
    rows = query_audit(action="login")
    assert rows == []
