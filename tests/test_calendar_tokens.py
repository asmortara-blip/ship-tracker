"""Tests for auth.calendar_tokens — per-user calendar-subscription tokens.

Calendar tokens are different from auth.tokens: they live in
UserSettings.extras (not a separate table) and are stored PLAIN
because the secret IS the URL — calendar apps fetch via plain GET
with no Authorization header, so we can't keep them hashed.

verify_calendar_token uses hmac.compare_digest for constant-time
matching against the per-user stored value.
"""
from __future__ import annotations

import pytest

from auth.calendar_tokens import (
    generate_calendar_token,
    get_calendar_token,
    revoke_calendar_token,
    verify_calendar_token,
)


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── generate / get / verify / revoke ─────────────────────────────────────


def test_generate_calendar_token_persists() -> None:
    token = generate_calendar_token(user_id="alice")
    assert token is not None
    assert isinstance(token, str)
    assert len(token) >= 16
    stored = get_calendar_token(user_id="alice")
    assert stored == token


def test_generate_calendar_token_replaces_existing() -> None:
    first = generate_calendar_token(user_id="alice")
    second = generate_calendar_token(user_id="alice")
    assert first != second
    # Only the second one is retrievable.
    assert get_calendar_token(user_id="alice") == second


def test_get_calendar_token_unknown_user_returns_none() -> None:
    assert get_calendar_token(user_id="nobody") is None


def test_verify_calendar_token_matches_returns_user_id() -> None:
    token = generate_calendar_token(user_id="alice")
    assert verify_calendar_token(token) == "alice"


def test_verify_calendar_token_unknown_returns_none() -> None:
    assert verify_calendar_token("not-a-real-token-abcdef") is None


def test_verify_calendar_token_empty_returns_none() -> None:
    assert verify_calendar_token("") is None


def test_revoke_calendar_token_clears() -> None:
    generate_calendar_token(user_id="alice")
    assert revoke_calendar_token(user_id="alice") is True
    assert get_calendar_token(user_id="alice") is None


def test_revoke_calendar_token_unknown_user_returns_false_or_true() -> None:
    # Either is acceptable — the operator-facing semantics is 'unsubscribed'.
    result = revoke_calendar_token(user_id="nobody")
    assert result in (True, False)


# ─── Per-user isolation ───────────────────────────────────────────────────


def test_per_user_isolation() -> None:
    a = generate_calendar_token(user_id="alice")
    b = generate_calendar_token(user_id="bob")
    assert a != b
    assert verify_calendar_token(a) == "alice"
    assert verify_calendar_token(b) == "bob"
    # Revoking alice's doesn't affect bob's.
    revoke_calendar_token(user_id="alice")
    assert verify_calendar_token(b) == "bob"


# ─── Defensive ────────────────────────────────────────────────────────────


def test_generate_never_raises_on_bad_input() -> None:
    # Empty user_id should return None (or a token, but not raise).
    result = generate_calendar_token(user_id="")
    assert result is None or isinstance(result, str)


def test_verify_never_raises_on_garbage() -> None:
    # Should not raise on weird inputs.
    assert verify_calendar_token("\x00\x01\x02") in (None,)
    assert verify_calendar_token(" " * 100) is None


def test_verify_matches_regardless_of_row_position() -> None:
    """The verifier scans ALL rows (no early-return on match), so a token
    belonging to any user resolves correctly and a non-token returns None.
    (The no-early-return is what keeps the lookup timing from leaking the
    match position.)"""
    t_alice = generate_calendar_token(user_id="alice")
    t_bob = generate_calendar_token(user_id="bob")
    t_carol = generate_calendar_token(user_id="carol")
    assert verify_calendar_token(t_alice) == "alice"
    assert verify_calendar_token(t_bob) == "bob"
    assert verify_calendar_token(t_carol) == "carol"
    assert verify_calendar_token("nope-not-a-real-token") is None
