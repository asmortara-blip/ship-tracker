"""Tests for ``auth.users`` — the v7 multi-user identity layer.

Exercises the four public helpers (``signup`` / ``login`` / ``get_user``
/ ``count_users``) plus the validation rules they enforce. Every test
runs against a fresh SQLite DB via the autouse ``isolated_db`` fixture.

The defining properties under test:

  * ``signup`` rejects malformed usernames / weak passwords / duplicates
    and generates a fresh salt per user (so two identical passwords end
    up with different hashes).
  * ``login`` accepts the right password, rejects the wrong password,
    rejects unknown usernames with the same return shape (no info leak),
    and updates ``last_login_at`` on success.
  * ``get_user`` is a happy-path lookup that returns ``None`` for
    unknown ids.
  * ``count_users`` returns 0 on an empty DB and the exact number of
    rows once seeded.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB at a per-test tmp_path."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── signup ────────────────────────────────────────────────────────────────

def test_signup_happy_path() -> None:
    from auth.users import signup

    user = signup("alice", "correct-horse-battery-staple")
    assert user is not None
    assert user.username == "alice"
    assert user.role == "user"
    assert user.user_id  # non-empty
    assert user.created_at  # ISO timestamp populated
    assert user.last_login_at == ""  # never logged in yet


@pytest.mark.parametrize("bad_username", [
    "ab",                # too short (< 3)
    "a" * 33,            # too long (> 32)
    "with space",        # spaces not allowed
    "with.dot",          # dots not allowed
    "with!bang",         # punctuation not allowed
    "",                  # empty
    "üñîçødé",          # non-ASCII
])
def test_signup_rejects_invalid_username(bad_username) -> None:
    from auth.users import signup

    assert signup(bad_username, "validpassword") is None


@pytest.mark.parametrize("valid_username", [
    "abc",               # exact min length
    "a" * 32,            # exact max length
    "with_underscore",
    "with-dash",
    "MixedCase123",
])
def test_signup_accepts_valid_username(valid_username) -> None:
    from auth.users import signup

    user = signup(valid_username, "validpassword")
    assert user is not None
    assert user.username == valid_username


def test_signup_rejects_weak_password() -> None:
    from auth.users import signup

    # < 8 chars
    assert signup("alice", "short") is None
    assert signup("alice", "1234567") is None


def test_signup_accepts_exact_min_password_length() -> None:
    from auth.users import signup

    user = signup("alice", "12345678")  # exactly 8 chars
    assert user is not None


def test_signup_rejects_duplicate_username() -> None:
    from auth.users import signup

    first = signup("alice", "password1234")
    assert first is not None
    second = signup("alice", "differentPass")
    assert second is None


def test_signup_generates_fresh_salt_per_user() -> None:
    """Two users with the same password MUST have different salts (and
    therefore different stored hashes) — anything else would let a
    rainbow-table attack lift identical passwords across rows."""
    from auth.users import signup
    from state.db import get_connection

    a = signup("alice", "samepassword")
    b = signup("bob", "samepassword")
    assert a is not None
    assert b is not None

    conn = get_connection()
    rows = conn.execute(
        "SELECT username, password_hash, password_salt FROM users "
        "WHERE username IN ('alice', 'bob')"
    ).fetchall()
    by_username = {r["username"]: r for r in rows}
    assert by_username["alice"]["password_salt"] != by_username["bob"]["password_salt"]
    assert by_username["alice"]["password_hash"] != by_username["bob"]["password_hash"]


def test_signup_returns_none_for_non_string_inputs() -> None:
    from auth.users import signup

    assert signup(None, "validpassword") is None  # type: ignore[arg-type]
    assert signup("alice", None) is None  # type: ignore[arg-type]
    assert signup(123, "validpassword") is None  # type: ignore[arg-type]


# ─── login ─────────────────────────────────────────────────────────────────

def test_login_happy_path() -> None:
    from auth.users import login, signup

    signup("alice", "correct-password-123")
    user = login("alice", "correct-password-123")
    assert user is not None
    assert user.username == "alice"


def test_login_rejects_bad_password() -> None:
    from auth.users import login, signup

    signup("alice", "correct-password-123")
    assert login("alice", "wrong-password-456") is None


def test_login_rejects_unknown_username() -> None:
    from auth.users import login

    # No signup — username does not exist.
    assert login("ghost", "anything-1234") is None


def test_login_bad_user_and_bad_password_have_same_return_shape() -> None:
    """Both failure modes must return ``None`` — same shape, no info
    leak about which usernames exist."""
    from auth.users import login, signup

    signup("alice", "correct-password-123")
    bad_user = login("ghost", "any-password")
    bad_pass = login("alice", "wrong-password")
    assert bad_user is bad_pass  # both are None


def test_login_updates_last_login_at() -> None:
    from auth.users import login, signup

    fresh = signup("alice", "correct-password-123")
    assert fresh is not None
    assert fresh.last_login_at == ""

    user = login("alice", "correct-password-123")
    assert user is not None
    assert user.last_login_at  # populated

    # And the updated timestamp must round-trip through get_user.
    from auth.users import get_user
    again = get_user(user.user_id)
    assert again is not None
    assert again.last_login_at == user.last_login_at

    # Sanity: must parse as a real ISO timestamp.
    parsed = datetime.fromisoformat(user.last_login_at)
    # And must be a UTC-aware timestamp not wildly in the past.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 60


def test_login_returns_none_for_non_string_inputs() -> None:
    from auth.users import login

    assert login(None, "anything") is None  # type: ignore[arg-type]
    assert login("alice", None) is None  # type: ignore[arg-type]


# ─── get_user ──────────────────────────────────────────────────────────────

def test_get_user_happy_path() -> None:
    from auth.users import get_user, signup

    fresh = signup("alice", "correct-password-123")
    assert fresh is not None
    user = get_user(fresh.user_id)
    assert user is not None
    assert user.username == "alice"
    assert user.user_id == fresh.user_id


def test_get_user_returns_none_for_unknown_id() -> None:
    from auth.users import get_user

    assert get_user("does-not-exist") is None


def test_get_user_returns_none_for_empty_id() -> None:
    from auth.users import get_user

    assert get_user("") is None


# ─── count_users ──────────────────────────────────────────────────────────

def test_count_users_empty_db() -> None:
    from auth.users import count_users

    assert count_users() == 0


def test_count_users_after_signups() -> None:
    from auth.users import count_users, signup

    assert count_users() == 0
    signup("alice", "password1234")
    assert count_users() == 1
    signup("bob", "password1234")
    assert count_users() == 2
    # Duplicate signup does not bump the count.
    signup("alice", "different1234")
    assert count_users() == 2


# ─── User dataclass shape ─────────────────────────────────────────────────

def test_user_dataclass_has_no_password_material() -> None:
    """The returned ``User`` MUST NOT carry password_hash or
    password_salt — they live only in the DB."""
    from auth.users import User, signup

    user = signup("alice", "validpassword")
    assert user is not None
    fields = set(user.__dataclass_fields__)
    assert "password_hash" not in fields
    assert "password_salt" not in fields
    assert fields == {
        "user_id", "username", "role", "created_at", "last_login_at",
    }
