"""Tests for ``engine.audit_search`` — operator-facing audit-log search.

Covers the dataclass shape, the four public helpers (``search_audit`` /
``search_audit_count`` / ``get_distinct_actions`` /
``get_distinct_entity_types``), and the defining properties:

* ``search_audit`` NEVER raises (DB outage → empty result).
* Every filter is independently optional and composes via AND.
* ``action_prefix`` matches multiple verbs sharing the same stem.
* ``text`` is case-insensitive and grep's ``detail_json`` + ``action``
  + ``entity_id``.
* ``LIMIT`` clips the row set without disturbing ``total_matched``.
* User-supplied ``%`` / ``_`` / ``'`` in ``text`` are LIKE-escaped, not
  interpreted — a search for a literal underscore matches the literal
  character, not "any single character".
"""
from __future__ import annotations

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


# ─── Helper: seed a deterministic set of audit rows ────────────────────────

def _seed(rows: list[dict]) -> None:
    """Insert the given rows directly so the test can pin created_at
    timestamps and exact column values. ``record_audit`` stamps its own
    timestamp + event_id, which makes ORDER BY assertions flaky."""
    import json

    from state.db import get_connection

    conn = get_connection()
    with conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO audit_events
                  (event_id, created_at, user_id, action, entity_type,
                   entity_id, detail_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["event_id"],
                    r["created_at"],
                    r.get("user_id", ""),
                    r["action"],
                    r.get("entity_type", ""),
                    r.get("entity_id", ""),
                    json.dumps(r.get("detail_json", {})),
                ),
            )


# ─── search_audit: empty-query / ordering ──────────────────────────────────

def test_search_audit_empty_query_returns_recent_events_descending() -> None:
    """No filters → every row, newest-first."""
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "login_success", "user_id": "u1"},
        {"event_id": "e2", "created_at": "2026-05-22T12:00:00+00:00",
         "action": "save_rules", "user_id": "u1"},
        {"event_id": "e3", "created_at": "2026-05-22T11:00:00+00:00",
         "action": "ack_alert", "user_id": "u2"},
    ])

    result = search_audit(AuditSearchQuery())
    assert result.total_matched == 3
    assert len(result.events) == 3
    # Newest first: e2 (12:00) > e3 (11:00) > e1 (10:00).
    assert [e["event_id"] for e in result.events] == ["e2", "e3", "e1"]


# ─── search_audit: per-column filters ──────────────────────────────────────

def test_search_audit_filters_by_user_id() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "login_success", "user_id": "alice"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "login_success", "user_id": "bob"},
    ])

    result = search_audit(AuditSearchQuery(user_id="alice"))
    assert result.total_matched == 1
    assert result.events[0]["user_id"] == "alice"


def test_search_audit_filters_by_action_exact() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "login_success"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "login_failure"},
        {"event_id": "e3", "created_at": "2026-05-22T10:02:00+00:00",
         "action": "save_rules"},
    ])

    result = search_audit(AuditSearchQuery(action="login_success"))
    assert result.total_matched == 1
    assert result.events[0]["action"] == "login_success"


def test_search_audit_action_prefix_matches_multiple_verbs() -> None:
    """``login_`` matches BOTH ``login_success`` and ``login_failure``."""
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "login_success"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "login_failure"},
        {"event_id": "e3", "created_at": "2026-05-22T10:02:00+00:00",
         "action": "save_rules"},
    ])

    result = search_audit(AuditSearchQuery(action_prefix="login_"))
    assert result.total_matched == 2
    actions = sorted(e["action"] for e in result.events)
    assert actions == ["login_failure", "login_success"]


def test_search_audit_filters_by_entity_type() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "ack_alert", "entity_type": "alert"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "save_channel", "entity_type": "channel"},
    ])

    result = search_audit(AuditSearchQuery(entity_type="alert"))
    assert result.total_matched == 1
    assert result.events[0]["entity_type"] == "alert"


def test_search_audit_filters_by_entity_id() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "ack_alert", "entity_type": "alert",
         "entity_id": "alert-123"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "ack_alert", "entity_type": "alert",
         "entity_id": "alert-999"},
    ])

    result = search_audit(AuditSearchQuery(entity_id="alert-123"))
    assert result.total_matched == 1
    assert result.events[0]["entity_id"] == "alert-123"


def test_search_audit_filters_by_since_until_window() -> None:
    """Half-open: ``since`` is inclusive, ``until`` is exclusive."""
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "before", "created_at": "2026-05-22T09:00:00+00:00",
         "action": "ack_alert"},
        {"event_id": "inside-a", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "ack_alert"},
        {"event_id": "inside-b", "created_at": "2026-05-22T11:00:00+00:00",
         "action": "ack_alert"},
        {"event_id": "after", "created_at": "2026-05-22T12:00:00+00:00",
         "action": "ack_alert"},
    ])

    result = search_audit(AuditSearchQuery(
        since="2026-05-22T10:00:00+00:00",
        until="2026-05-22T12:00:00+00:00",
    ))
    assert result.total_matched == 2
    ids = sorted(e["event_id"] for e in result.events)
    assert ids == ["inside-a", "inside-b"]


# ─── search_audit: text grep ───────────────────────────────────────────────

def test_search_audit_text_grep_on_detail_json() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "save_channel",
         "detail_json": {"name": "trading-desk", "kind": "slack"}},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "save_channel",
         "detail_json": {"name": "ops-pager", "kind": "pagerduty"}},
    ])

    result = search_audit(AuditSearchQuery(text="trading"))
    assert result.total_matched == 1
    assert result.events[0]["event_id"] == "e1"


def test_search_audit_text_grep_on_action() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "make_public"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "revoke_public"},
        {"event_id": "e3", "created_at": "2026-05-22T10:02:00+00:00",
         "action": "save_rules"},
    ])

    result = search_audit(AuditSearchQuery(text="public"))
    assert result.total_matched == 2
    actions = sorted(e["action"] for e in result.events)
    assert actions == ["make_public", "revoke_public"]


def test_search_audit_text_grep_is_case_insensitive() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "save_channel",
         "detail_json": {"webhook": "Trading-DESK"}},
    ])

    # All four casings should match.
    for term in ("trading", "TRADING", "Trading", "tRaDiNg"):
        result = search_audit(AuditSearchQuery(text=term))
        assert result.total_matched == 1, f"failed for casing: {term!r}"


# ─── search_audit: AND composition ────────────────────────────────────────

def test_search_audit_combined_filters_and_together() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": "match", "created_at": "2026-05-22T10:30:00+00:00",
         "action": "ack_alert", "entity_type": "alert",
         "user_id": "alice"},
        {"event_id": "wrong-user", "created_at": "2026-05-22T10:30:00+00:00",
         "action": "ack_alert", "entity_type": "alert",
         "user_id": "bob"},
        {"event_id": "wrong-action", "created_at": "2026-05-22T10:30:00+00:00",
         "action": "save_rules", "entity_type": "alert",
         "user_id": "alice"},
        {"event_id": "out-of-window", "created_at": "2026-05-22T08:30:00+00:00",
         "action": "ack_alert", "entity_type": "alert",
         "user_id": "alice"},
    ])

    result = search_audit(AuditSearchQuery(
        user_id="alice",
        action="ack_alert",
        entity_type="alert",
        since="2026-05-22T10:00:00+00:00",
    ))
    assert result.total_matched == 1
    assert result.events[0]["event_id"] == "match"


# ─── search_audit_count ────────────────────────────────────────────────────

def test_search_audit_count_returns_count_without_fetching() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit_count

    _seed([
        {"event_id": f"e{i}", "created_at": f"2026-05-22T10:0{i}:00+00:00",
         "action": "ack_alert", "user_id": "alice"}
        for i in range(5)
    ])

    n = search_audit_count(AuditSearchQuery(user_id="alice"))
    assert n == 5


def test_search_audit_count_returns_zero_on_no_match() -> None:
    from engine.audit_search import AuditSearchQuery, search_audit_count

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "ack_alert", "user_id": "alice"},
    ])

    n = search_audit_count(AuditSearchQuery(user_id="nobody"))
    assert n == 0


# ─── search_audit: LIMIT semantics ────────────────────────────────────────

def test_search_audit_limit_caps_results_but_total_reflects_all() -> None:
    """``LIMIT`` clips events but ``total_matched`` keeps the full count
    so a paginating UI can render 'showing N of M'."""
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        {"event_id": f"e{i}", "created_at": f"2026-05-22T10:00:{i:02d}+00:00",
         "action": "ack_alert", "user_id": "alice"}
        for i in range(20)
    ])

    result = search_audit(AuditSearchQuery(user_id="alice", limit=5))
    assert result.total_matched == 20
    assert len(result.events) == 5


# ─── Distinct-value dropdown helpers ──────────────────────────────────────

def test_get_distinct_actions_returns_sorted_list() -> None:
    from engine.audit_search import get_distinct_actions

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "save_rules"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "ack_alert"},
        {"event_id": "e3", "created_at": "2026-05-22T10:02:00+00:00",
         "action": "ack_alert"},  # duplicate — DISTINCT must dedupe
        {"event_id": "e4", "created_at": "2026-05-22T10:03:00+00:00",
         "action": "login_success"},
    ])

    actions = get_distinct_actions()
    assert actions == sorted(actions)
    assert actions == ["ack_alert", "login_success", "save_rules"]


def test_get_distinct_entity_types_returns_sorted_list() -> None:
    from engine.audit_search import get_distinct_entity_types

    _seed([
        {"event_id": "e1", "created_at": "2026-05-22T10:00:00+00:00",
         "action": "ack_alert", "entity_type": "alert"},
        {"event_id": "e2", "created_at": "2026-05-22T10:01:00+00:00",
         "action": "save_channel", "entity_type": "channel"},
        {"event_id": "e3", "created_at": "2026-05-22T10:02:00+00:00",
         "action": "delete_report", "entity_type": "report"},
        # Empty entity_type should be filtered out — the "untagged"
        # bucket is exposed via the UI's "(any)" option.
        {"event_id": "e4", "created_at": "2026-05-22T10:03:00+00:00",
         "action": "login_success", "entity_type": ""},
    ])

    types = get_distinct_entity_types()
    assert types == sorted(types)
    assert types == ["alert", "channel", "report"]


# ─── Defensive contract: NEVER raises ──────────────────────────────────────

def test_search_audit_never_raises_on_db_error(monkeypatch) -> None:
    """A failing DB layer must surface an empty result, not an exception."""
    from engine import audit_search

    class _BoomConn:
        def execute(self, *a, **kw):  # noqa: ARG002
            raise RuntimeError("DB melted")

    # Patch the lazy get_connection import inside audit_search.
    monkeypatch.setattr(
        "state.db.get_connection", lambda: _BoomConn()
    )

    result = audit_search.search_audit(audit_search.AuditSearchQuery())
    # No exception bubbled up.
    assert result.events == []
    assert result.total_matched == 0


# ─── SQL injection / LIKE-escape safety ────────────────────────────────────

def test_search_audit_text_with_sql_meta_chars_does_not_break() -> None:
    """``%`` / ``_`` / ``'`` in user-supplied text are bound, not
    interpolated, and LIKE meta-chars are escaped so they match
    literally."""
    from engine.audit_search import AuditSearchQuery, search_audit

    _seed([
        # Row whose detail_json contains a literal underscore + percent.
        {"event_id": "literal",
         "created_at": "2026-05-22T10:00:00+00:00",
         "action": "save_rules",
         "detail_json": {"note": "key_with_%_in_it"}},
        # Row that should NOT match a literal underscore search.
        {"event_id": "no-underscore",
         "created_at": "2026-05-22T10:01:00+00:00",
         "action": "save_rules",
         "detail_json": {"note": "key-with-dash"}},
        # Row whose action contains a single quote — a naive
        # interpolated query would explode here.
        {"event_id": "quoted",
         "created_at": "2026-05-22T10:02:00+00:00",
         "action": "save_rules",
         "detail_json": {"note": "alice's note"}},
    ])

    # Literal underscore — the LIKE wildcard ``_`` would match any one
    # char, but we escape it so only the literal underscore matches.
    r1 = search_audit(AuditSearchQuery(text="key_with"))
    assert r1.total_matched == 1
    assert r1.events[0]["event_id"] == "literal"

    # Literal percent.
    r2 = search_audit(AuditSearchQuery(text="%"))
    assert r2.total_matched == 1
    assert r2.events[0]["event_id"] == "literal"

    # Single quote — bound parameter must not break the query. We
    # only assert the query runs and returns the matching row.
    r3 = search_audit(AuditSearchQuery(text="alice's"))
    assert r3.total_matched == 1
    assert r3.events[0]["event_id"] == "quoted"
