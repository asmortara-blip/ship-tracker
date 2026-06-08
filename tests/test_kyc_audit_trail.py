"""Tests for R128 — the KYC/screening audit trail with auditor replay.

The regulator-facing "show me exactly why this vessel / counterparty was
cleared on that date" capability. The audit SUBSTRATE (record + replay) is
REAL infrastructure; the screened CONTENT on the Compliance tab is illustrative
/ modeled, so every recorded basis carries ``illustrative=True`` to keep that
honest.

Defining properties under test:

  * ``auth.audit.record_screening`` persists ONE ``screening_run`` audit event
    carrying the FULL basis — subject, modeled inputs, list version, score,
    decision, and operator.
  * ``engine.audit_search.replay_screening(event_id)`` returns that EXACT basis
    (round-trip — what was recorded is what replays).
  * ``screening_history(subject)`` returns multiple runs on the same subject,
    newest-first (time-ordered).
  * The OPERATOR (the audited user_id) is captured.
  * A malformed / partial input does NOT raise (best-effort), and the recorded
    basis degrades to honest empties.
  * Replay of an unknown id returns ``None`` (never raises).

Uses the same per-test isolated SQLite fixture as ``test_auth_audit.py`` so no
test touches the real ``cache/ship_tracker.db``.
"""
from __future__ import annotations

import pytest


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test touches
    the real cache/ship_tracker.db (mirrors test_auth_audit.py)."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _record_one(
    *,
    subject="9876543",
    inputs=None,
    list_version="illustrative-matrix/2026.06",
    score=82.0,
    decision="block",
    operator="alice@x",
):
    """Record one screening run and return its event_id (the newest event)."""
    from auth.audit import record_screening, query_audit

    if inputs is None:
        inputs = {"route": "Russia → Asia", "counterparty": "Russia",
                  "route_risk": 80, "counterparty_risk": 85}
    record_screening(
        subject=subject,
        inputs=inputs,
        list_version=list_version,
        score=score,
        decision=decision,
        illustrative=True,
        user_id=operator,
    )
    events = query_audit(action="screening_run", limit=1)
    assert events, "expected the screening_run event to persist"
    return events[0].event_id


# ─── Recording persists a full-basis audit event ───────────────────────────

def test_record_screening_persists_event() -> None:
    """A screening run writes exactly one ``screening_run`` audit event."""
    from auth.audit import record_screening, query_audit

    record_screening(
        subject="9876543",
        inputs={"route": "Iran → China", "counterparty": "Iran"},
        list_version="illustrative-matrix/2026.06",
        score=90.0,
        decision="block",
        user_id="op1",
    )
    events = query_audit(action="screening_run", limit=10)
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "screening_run"
    assert ev.entity_type == "screening"
    assert ev.entity_id == "9876543"


def test_record_screening_basis_carries_every_field() -> None:
    """The persisted detail_json carries subject/inputs/list_version/score/
    decision/illustrative — the full decision basis."""
    from auth.audit import record_screening, query_audit

    inputs = {"route": "Russia → EU", "counterparty": "Russia", "route_risk": 88}
    record_screening(
        subject="counterparty:Russia",
        inputs=inputs,
        list_version="v2026.06",
        score=77.0,
        decision="block",
        user_id="op2",
    )
    ev = query_audit(action="screening_run", limit=1)[0]
    basis = ev.detail_json
    assert basis["subject"] == "counterparty:Russia"
    assert basis["inputs"] == inputs
    assert basis["list_version"] == "v2026.06"
    assert basis["score"] == 77.0
    assert basis["decision"] == "block"
    assert basis["illustrative"] is True


# ─── Replay is an exact round-trip ─────────────────────────────────────────

def test_replay_screening_round_trip() -> None:
    """replay_screening(event_id) returns the EXACT recorded basis."""
    from engine.audit_search import replay_screening

    inputs = {"route": "Russia → Asia", "counterparty": "Russia",
              "route_risk": 80, "counterparty_risk": 85,
              "weights": {"route": 0.45, "counterparty": 0.40, "cargo": 0.15}}
    eid = _record_one(
        subject="9876543",
        inputs=inputs,
        list_version="illustrative-matrix/2026.06",
        score=82.0,
        decision="block",
        operator="alice@x",
    )
    rec = replay_screening(eid)
    assert rec is not None
    assert rec["event_id"] == eid
    assert rec["subject"] == "9876543"
    assert rec["inputs"] == inputs            # nested dict round-trips
    assert rec["list_version"] == "illustrative-matrix/2026.06"
    assert rec["score"] == 82.0
    assert rec["decision"] == "block"
    assert rec["operator"] == "alice@x"       # operator captured
    assert rec["user_id"] == "alice@x"
    assert rec["illustrative"] is True
    assert rec["created_at"]                  # when — non-empty timestamp


def test_replay_unknown_id_returns_none() -> None:
    """An unknown / empty event id replays as None, never raises."""
    from engine.audit_search import replay_screening

    assert replay_screening("does-not-exist") is None
    assert replay_screening("") is None
    assert replay_screening(None) is None  # type: ignore[arg-type]


def test_replay_ignores_non_screening_events() -> None:
    """A normal audit event id is not replayable as a screening (action gate)."""
    from auth.audit import record_audit, query_audit
    from engine.audit_search import replay_screening

    record_audit("login", entity_type="user", entity_id="op9", user_id="op9")
    ev = query_audit(action="login", limit=1)[0]
    assert replay_screening(ev.event_id) is None


# ─── Multiple runs on one subject — time-ordered history ───────────────────

def test_screening_history_multiple_runs_newest_first() -> None:
    """Several runs on the same subject return as a newest-first history."""
    from engine.audit_search import screening_history

    subject = "9876543"
    for sc, dec in [(20.0, "clear"), (55.0, "flag"), (90.0, "block")]:
        _record_one(subject=subject, score=sc, decision=dec, operator="op")

    hist = screening_history(subject)
    assert len(hist) == 3
    # created_at DESC: the last-written (block) is first.
    decisions = [r["decision"] for r in hist]
    assert decisions[0] == "block"
    assert set(decisions) == {"clear", "flag", "block"}
    # created_at is monotonically non-increasing.
    ts = [r["created_at"] for r in hist]
    assert ts == sorted(ts, reverse=True)
    # every record carries the subject + operator.
    assert all(r["subject"] == subject for r in hist)
    assert all(r["operator"] == "op" for r in hist)


def test_screening_history_scoped_to_subject() -> None:
    """History for one subject excludes runs on other subjects."""
    from engine.audit_search import screening_history

    _record_one(subject="AAA", operator="op")
    _record_one(subject="BBB", operator="op")
    _record_one(subject="AAA", operator="op")

    assert len(screening_history("AAA")) == 2
    assert len(screening_history("BBB")) == 1
    assert screening_history("ZZZ") == []
    assert screening_history("") == []
    assert screening_history("AAA", limit=0) == []


# ─── Operator capture (session fallback) ───────────────────────────────────

def test_operator_captured_from_explicit_user() -> None:
    """An explicit user_id is recorded as the operator verbatim."""
    from engine.audit_search import replay_screening

    eid = _record_one(operator="regulator-bot@x")
    rec = replay_screening(eid)
    assert rec is not None
    assert rec["operator"] == "regulator-bot@x"


def test_operator_empty_for_system_run() -> None:
    """An explicit empty operator records a system/unattributed run."""
    from engine.audit_search import replay_screening

    eid = _record_one(operator="")
    rec = replay_screening(eid)
    assert rec is not None
    assert rec["operator"] == ""


# ─── Best-effort: malformed / partial inputs never raise ───────────────────

def test_record_screening_partial_inputs_do_not_raise() -> None:
    """A missing/partial basis is recorded with honest empties, never raises."""
    from auth.audit import record_screening, query_audit

    # No inputs, no list version, no score, no decision.
    record_screening(subject="X", user_id="op")
    ev = query_audit(action="screening_run", limit=1)[0]
    basis = ev.detail_json
    assert basis["subject"] == "X"
    assert basis["inputs"] == {}
    assert basis["list_version"] == ""
    assert basis["score"] is None
    assert basis["decision"] == ""
    assert basis["illustrative"] is True


def test_record_screening_malformed_types_do_not_raise() -> None:
    """Non-dict inputs / non-numeric score degrade gracefully (no raise)."""
    from auth.audit import record_screening, query_audit

    record_screening(
        subject="Y",
        inputs="not-a-dict",          # type: ignore[arg-type]
        score="not-a-number",         # type: ignore[arg-type]
        decision="flag",
        user_id="op",
    )
    ev = query_audit(action="screening_run", limit=1)[0]
    basis = ev.detail_json
    assert basis["inputs"] == {}      # coerced to empty dict
    assert basis["score"] is None     # non-numeric → None
    assert basis["decision"] == "flag"


def test_record_screening_non_string_subject_does_not_raise() -> None:
    """A non-string subject (e.g. an int IMO) is coerced, never raises, and the
    history lookup uses the coerced form."""
    from auth.audit import record_screening
    from engine.audit_search import screening_history

    record_screening(subject=9876543, score=10.0, decision="clear", user_id="op")  # type: ignore[arg-type]
    hist = screening_history("9876543")
    assert len(hist) == 1
    assert hist[0]["subject"] == "9876543"
    assert hist[0]["decision"] == "clear"


# ─── Honesty: the recorded basis is stamped illustrative ───────────────────

def test_basis_is_stamped_illustrative_by_default() -> None:
    """The Compliance tab is illustrative — the recorded basis says so."""
    from engine.audit_search import replay_screening

    eid = _record_one()
    rec = replay_screening(eid)
    assert rec is not None
    assert rec["illustrative"] is True
