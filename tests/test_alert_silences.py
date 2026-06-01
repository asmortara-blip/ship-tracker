"""Tests for engine.alert_silences (schema v22).

Operators wanted "shut up rule X for the next 4 hours" before doing
maintenance — instead of disabling the rule and forgetting to
re-enable it, silence it with an auto-expiring window. This module
covers:

* CRUD: create_silence / get_active_silences / list_silences /
  delete_silence persistence semantics, including the NULL-means-
  "matches anything" convention for the three match keys
  (rule_id / ticker / severity).
* Per-user scoping: alice cannot read, mutate, or delete bob's
  silences; the silence gate inside fire_rule does not suppress
  another user's alerts.
* Time-window correctness: get_active_silences only returns rows
  where starts_at <= now < expires_at; expired rows fall out.
* is_alert_silenced match logic: exact match, NULL-matches-all,
  rule_id mismatch, expired silence, cross-user silence.
* fire_rule integration: an active silence drops the alert + bumps
  the silenced_counter without saving a row; fire_rule still returns
  True (silencing is not a failure mode).
* Retention: cleanup_expired_silences deletes only rows expired more
  than retention_days ago; recent expirations stay for audit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine import alert_engine_v2 as engv2
from engine import alert_silences as silmod
from engine.alert_engine_v2 import ShippingAlert, _make, fire_rule
from engine.alert_silences import (
    AlertSilence,
    cleanup_expired_silences,
    create_silence,
    delete_silence,
    get_active_silences,
    get_suppressed_by_silence_count,
    is_alert_silenced,
    list_silences,
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

def _mk_user(username: str) -> str:
    """Create a real user via auth.users.signup and return the
    user_id. The alert_silences table has a FOREIGN KEY constraint on
    ``created_by_user_id`` so the test users must actually exist in
    the ``users`` table — mirrors the pattern used in test_mfa.py."""
    from auth.users import signup
    user = signup(username, "correct-password-123")
    assert user is not None, f"signup failed for {username!r}"
    return user.user_id


def _alert(
    *,
    alert_type: str = "BDI_MOVE",
    severity: str = "HIGH",
    ticker: str = "",
    route_id: str = "",
    port_locode: str = "",
    value: float = 1000.0,
    change_pct: float = 5.0,
) -> ShippingAlert:
    return _make(
        alert_type, severity, "title", "body",
        ticker=ticker, route_id=route_id, port_locode=port_locode,
        value=value, threshold=5.0, change_pct=change_pct,
    )


def _rule(rule_id: str = "rule_test", cooldown_minutes: int = 0) -> dict:
    return {
        "rule_id": rule_id,
        "name": f"Test rule {rule_id}",
        "metric": "Freight Rate",
        "threshold": 5.0,
        "condition": "Above",
        "severity": "HIGH",
        "enabled": True,
        "cooldown_minutes": cooldown_minutes,
    }


def _count_rows_for_rule(rule_id: str) -> int:
    from state.db import get_connection
    return int(
        get_connection().execute(
            "SELECT COUNT(*) FROM alerts WHERE rule_id = ?", (rule_id,)
        ).fetchone()[0]
    )


def _backdate_silence(silence_id: str, *, expires_minutes_ago: float) -> None:
    """Move a silence's expires_at to ``expires_minutes_ago`` minutes
    in the past so it counts as expired without sleeping. Also moves
    starts_at proportionally so the active-window math stays sane.
    """
    from state.db import get_connection

    new_expires = (
        datetime.now(timezone.utc) - timedelta(minutes=expires_minutes_ago)
    ).isoformat()
    new_starts = (
        datetime.now(timezone.utc) - timedelta(minutes=expires_minutes_ago + 60)
    ).isoformat()
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE alert_silences SET starts_at = ?, expires_at = ? "
            "WHERE silence_id = ?",
            (new_starts, new_expires, silence_id),
        )


# ─── create_silence persistence ──────────────────────────────────────────

def test_create_silence_persists_all_three_match_keys() -> None:
    """When create_silence gets explicit rule_id + ticker + severity,
    every field lands on the row."""
    alice = _mk_user("alice")
    s = create_silence(
        user_id=alice,
        rule_id="rule_bdi",
        ticker="ZIM",
        severity="HIGH",
        reason="FRED maintenance",
        duration_minutes=240,
        created_by_user_id=alice,
    )
    assert s is not None
    assert s.user_id == alice
    assert s.rule_id == "rule_bdi"
    assert s.ticker == "ZIM"
    assert s.severity == "HIGH"
    assert s.reason == "FRED maintenance"
    assert s.created_by_user_id == alice
    # Round-trip via list_silences confirms the row is queryable.
    listed = list_silences(user_id=alice)
    assert len(listed) == 1
    assert listed[0].silence_id == s.silence_id


def test_create_silence_with_nulls_persists_as_matches_all() -> None:
    """Omitted match keys become NULL on the row; the SQL layer can
    answer 'is rule_id None?' with IS NULL rather than comparing to
    an empty string."""
    alice = _mk_user("alice")
    s = create_silence(
        user_id=alice,
        duration_minutes=60,
        created_by_user_id=alice,
    )
    assert s is not None
    assert s.rule_id is None
    assert s.ticker is None
    assert s.severity is None
    assert s.reason is None
    # Direct DB check — confirm NULL, not "".
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT rule_id, ticker, severity, reason FROM alert_silences "
        "WHERE silence_id = ?",
        (s.silence_id,),
    ).fetchone()
    assert row["rule_id"] is None
    assert row["ticker"] is None
    assert row["severity"] is None
    assert row["reason"] is None


def test_create_silence_clamps_non_positive_duration_to_one_minute() -> None:
    """A zero / negative duration is operator error; clamp to 1
    rather than persisting a silence that expires the same instant."""
    alice = _mk_user("alice")
    s = create_silence(
        user_id=alice,
        duration_minutes=0,
        created_by_user_id=alice,
    )
    assert s is not None
    # expires_at - starts_at should be exactly 1 minute.
    starts = datetime.fromisoformat(s.starts_at)
    expires = datetime.fromisoformat(s.expires_at)
    assert (expires - starts).total_seconds() == 60


def test_create_silence_empty_string_match_keys_become_null() -> None:
    """A caller that passes "" for rule_id / ticker / severity (thinking
    it means "no value") should land NULL on the row so the
    matches-everything semantic still fires."""
    alice = _mk_user("alice")
    s = create_silence(
        user_id=alice,
        rule_id="",
        ticker="",
        severity="",
        reason="",
        duration_minutes=30,
        created_by_user_id=alice,
    )
    assert s is not None
    assert s.rule_id is None
    assert s.ticker is None
    assert s.severity is None
    assert s.reason is None


# ─── get_active_silences ─────────────────────────────────────────────────

def test_get_active_silences_returns_currently_active() -> None:
    """A freshly-created silence is in the active window."""
    alice = _mk_user("alice")
    s = create_silence(
        user_id=alice,
        duration_minutes=60,
        created_by_user_id=alice,
    )
    assert s is not None
    active = get_active_silences(user_id=alice)
    assert len(active) == 1
    assert active[0].silence_id == s.silence_id


def test_get_active_silences_excludes_expired() -> None:
    """An expired silence falls out of the active list."""
    alice = _mk_user("alice")
    s = create_silence(
        user_id=alice,
        duration_minutes=60,
        created_by_user_id=alice,
    )
    assert s is not None
    _backdate_silence(s.silence_id, expires_minutes_ago=10)
    assert get_active_silences(user_id=alice) == []


def test_get_active_silences_per_user_scoping() -> None:
    """Alice's silence does NOT appear in Bob's active list."""
    alice = _mk_user("alice")
    bob = _mk_user("bob")
    a = create_silence(user_id=alice, duration_minutes=60,
                       created_by_user_id=alice)
    b = create_silence(user_id=bob, duration_minutes=60,
                       created_by_user_id=bob)
    assert a is not None and b is not None
    alice_active = get_active_silences(user_id=alice)
    bob_active = get_active_silences(user_id=bob)
    assert len(alice_active) == 1 and alice_active[0].silence_id == a.silence_id
    assert len(bob_active) == 1 and bob_active[0].silence_id == b.silence_id


# ─── is_alert_silenced match logic ──────────────────────────────────────

def test_is_alert_silenced_exact_match_returns_the_silence() -> None:
    """A silence with explicit rule_id + ticker + severity matches an
    alert with the exact same triple."""
    alice = _mk_user("alice")
    silence = create_silence(
        user_id=alice,
        rule_id="rule_bdi",
        ticker="ZIM",
        severity="HIGH",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    assert silence is not None
    alert = _alert(ticker="ZIM", severity="HIGH")
    setattr(alert, "rule_id", "rule_bdi")
    match = is_alert_silenced(alert, user_id=alice)
    assert match is not None
    assert match.silence_id == silence.silence_id


def test_is_alert_silenced_null_rule_id_matches_any_rule() -> None:
    """A silence with rule_id NULL matches every rule_id including
    the empty string and any specific rule_id value."""
    alice = _mk_user("alice")
    create_silence(
        user_id=alice,
        # rule_id omitted -> NULL
        ticker="ZIM",
        severity="HIGH",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    alert = _alert(ticker="ZIM", severity="HIGH")
    setattr(alert, "rule_id", "rule_any")
    assert is_alert_silenced(alert, user_id=alice) is not None


def test_is_alert_silenced_different_rule_returns_none() -> None:
    """A silence for rule_X does NOT match an alert from rule_Y."""
    alice = _mk_user("alice")
    create_silence(
        user_id=alice,
        rule_id="rule_X",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    alert = _alert(ticker="ZIM", severity="HIGH")
    setattr(alert, "rule_id", "rule_Y")
    assert is_alert_silenced(alert, user_id=alice) is None


def test_is_alert_silenced_expired_silence_returns_none() -> None:
    """An expired silence is no longer a match even when every key
    aligns."""
    alice = _mk_user("alice")
    s = create_silence(
        user_id=alice,
        rule_id="rule_X",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    assert s is not None
    _backdate_silence(s.silence_id, expires_minutes_ago=5)
    alert = _alert()
    setattr(alert, "rule_id", "rule_X")
    assert is_alert_silenced(alert, user_id=alice) is None


def test_is_alert_silenced_per_user_scoping() -> None:
    """Alice's silence does NOT match an alert that belongs to Bob."""
    alice = _mk_user("alice")
    bob = _mk_user("bob")
    create_silence(
        user_id=alice,
        rule_id="rule_X",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    alert = _alert()
    setattr(alert, "rule_id", "rule_X")
    # Same rule_id but Bob's user scope — alice's silence must not match.
    assert is_alert_silenced(alert, user_id=bob) is None


# ─── delete_silence ──────────────────────────────────────────────────────

def test_delete_silence_per_user_scoping() -> None:
    """Alice cannot delete Bob's silence — delete_silence with the
    wrong user_id returns False and the row survives."""
    alice = _mk_user("alice")
    bob = _mk_user("bob")
    s = create_silence(user_id=bob, duration_minutes=60,
                       created_by_user_id=bob)
    assert s is not None
    assert delete_silence(s.silence_id, user_id=alice) is False
    # Bob's silence is still there.
    assert len(list_silences(user_id=bob)) == 1


def test_delete_silence_unknown_id_returns_false() -> None:
    """An id that does not exist is indistinguishable from a cross-
    user id by design (no enumeration vector). Both return False."""
    alice = _mk_user("alice")
    assert delete_silence("does-not-exist", user_id=alice) is False


def test_delete_silence_owner_can_cancel() -> None:
    """The silence owner can cancel their own silence early."""
    alice = _mk_user("alice")
    s = create_silence(user_id=alice, duration_minutes=60,
                       created_by_user_id=alice)
    assert s is not None
    assert delete_silence(s.silence_id, user_id=alice) is True
    assert list_silences(user_id=alice) == []


# ─── cleanup_expired_silences ────────────────────────────────────────────

def test_cleanup_deletes_only_those_expired_past_retention() -> None:
    """A silence expired more than retention_days ago is swept;
    a silence expired LESS than retention_days ago survives for the
    audit window."""
    alice = _mk_user("alice")
    # One ancient silence (well past 30 days) + one recent expired
    # silence (within retention).
    ancient = create_silence(user_id=alice, duration_minutes=60,
                             created_by_user_id=alice)
    recent = create_silence(user_id=alice, duration_minutes=60,
                            created_by_user_id=alice)
    assert ancient is not None and recent is not None
    _backdate_silence(ancient.silence_id, expires_minutes_ago=60 * 24 * 45)
    _backdate_silence(recent.silence_id, expires_minutes_ago=60 * 24 * 5)

    deleted = cleanup_expired_silences(retention_days=30)
    assert deleted == 1
    surviving = list_silences(user_id=alice, include_expired=True)
    assert len(surviving) == 1
    assert surviving[0].silence_id == recent.silence_id


def test_cleanup_keeps_recent_expired_for_audit() -> None:
    """A silence that expired a day ago must survive the default
    30-day retention sweep so 'what was muted yesterday?' answers."""
    alice = _mk_user("alice")
    s = create_silence(user_id=alice, duration_minutes=60,
                       created_by_user_id=alice)
    assert s is not None
    _backdate_silence(s.silence_id, expires_minutes_ago=60 * 24)  # 1 day ago

    cleanup_expired_silences(retention_days=30)
    surviving = list_silences(user_id=alice, include_expired=True)
    assert len(surviving) == 1


# ─── fire_rule integration ──────────────────────────────────────────────

def test_fire_rule_with_active_silence_drops_alert() -> None:
    """A rule whose alert matches an active silence does NOT persist
    an alerts row, but fire_rule still returns True."""
    alice = _mk_user("alice")
    rule = _rule(rule_id="rule_silenced", cooldown_minutes=0)
    create_silence(
        user_id=alice,
        rule_id="rule_silenced",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    alert = _alert(ticker="ZIM", severity="HIGH")
    fired = fire_rule(rule, [alert], user_id=alice)
    assert fired is True
    assert _count_rows_for_rule("rule_silenced") == 0


def test_fire_rule_with_active_silence_bumps_counter() -> None:
    """Every silenced fire bumps the kv_state counter."""
    alice = _mk_user("alice")
    rule = _rule(rule_id="rule_count", cooldown_minutes=0)
    create_silence(
        user_id=alice,
        rule_id="rule_count",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    before = get_suppressed_by_silence_count()
    fire_rule(rule, [_alert(ticker="ZIM")], user_id=alice)
    fire_rule(rule, [_alert(ticker="MATX")], user_id=alice)
    fire_rule(rule, [_alert(ticker="SBLK")], user_id=alice)
    after = get_suppressed_by_silence_count()
    assert after - before == 3


def test_fire_rule_with_expired_silence_fires_normally() -> None:
    """A silence that has expired does NOT suppress; the alert
    persists normally."""
    alice = _mk_user("alice")
    rule = _rule(rule_id="rule_exp", cooldown_minutes=0)
    s = create_silence(
        user_id=alice,
        rule_id="rule_exp",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    assert s is not None
    _backdate_silence(s.silence_id, expires_minutes_ago=5)

    fired = fire_rule(rule, [_alert(ticker="ZIM")], user_id=alice)
    assert fired is True
    assert _count_rows_for_rule("rule_exp") == 1


def test_fire_rule_with_silence_different_user_fires_normally() -> None:
    """Alice's silence does NOT suppress Bob's rule fire."""
    alice = _mk_user("alice")
    bob = _mk_user("bob")
    rule = _rule(rule_id="rule_cross", cooldown_minutes=0)
    create_silence(
        user_id=alice,
        rule_id="rule_cross",
        duration_minutes=60,
        created_by_user_id=alice,
    )
    fired = fire_rule(rule, [_alert(ticker="ZIM")], user_id=bob)
    assert fired is True
    # The alert lands under Bob's user_id, unaffected by alice's silence.
    assert _count_rows_for_rule("rule_cross") == 1


def test_integration_create_silence_then_fire_matching_rule_not_saved() -> None:
    """End-to-end: create silence → fire matching rule → confirm zero
    alert rows + counter bump + fire_rule returned True."""
    alice = _mk_user("alice")
    rule = _rule(rule_id="rule_e2e", cooldown_minutes=0)
    create_silence(
        user_id=alice,
        rule_id="rule_e2e",
        ticker="ZIM",
        severity="HIGH",
        reason="planned downtime",
        duration_minutes=240,
        created_by_user_id=alice,
    )
    before = get_suppressed_by_silence_count()
    result = fire_rule(rule, [_alert(ticker="ZIM", severity="HIGH")],
                       user_id=alice)
    assert result is True
    assert _count_rows_for_rule("rule_e2e") == 0
    assert get_suppressed_by_silence_count() - before == 1
