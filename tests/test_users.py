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
        "is_active",
    }


def test_login_unknown_user_runs_dummy_verify_for_enumeration_resistance(monkeypatch) -> None:
    """An unknown username must still run a password verification (against a
    fixed dummy) so response timing doesn't leak which usernames exist."""
    import auth.users as users

    calls: list = []
    monkeypatch.setattr(
        users, "_verify_password",
        lambda pw, h, s: calls.append((pw, h, s)) or False,
    )
    assert users.login("definitely-not-a-real-user", "whatever") is None
    assert len(calls) == 1                          # a verify ran despite no user
    assert calls[0][1] == users._DUMMY_PW_HASH      # against the fixed dummy
    assert calls[0][2] == users._DUMMY_PW_SALT


# ─── login: brute-force throttle ──────────────────────────────────────────
#
# These use DEDICATED usernames + an explicit ``clear_buckets`` in a finally
# so a drained bucket can't leak into other tests (the rate-limit registry is
# process-global; the autouse ``isolated_db`` fixture only resets the DB).


def test_login_throttles_consecutive_failures(monkeypatch) -> None:
    """After enough consecutive FAILED logins for a username, even the
    CORRECT password is throttled — the attempt is rejected at the rate-
    limit gate, before the password is ever checked."""
    import auth.users as users
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        users.signup("throttle-victim", "correct-password-123")
        # Make the draining attempts cheap + deterministic: stub the KDF so
        # the loop doesn't depend on PBKDF2 wall-time (which would let the
        # bucket refill mid-loop and make the assertion timing-flaky).
        calls: list = []
        monkeypatch.setattr(
            users, "_verify_password",
            lambda pw, h, s: calls.append(pw) or False,
        )
        for _ in range(10):  # capacity is 10 — drain it with wrong passwords
            assert users.login("throttle-victim", "wrong") is None
        verifies_before = len(calls)
        # The next attempt — even with the CORRECT password — is rejected at
        # the throttle gate, so the KDF never runs for it.
        assert users.login("throttle-victim", "correct-password-123") is None
        assert len(calls) == verifies_before  # no verify ran → it was throttled
    finally:
        clear_buckets()


def test_login_success_resets_throttle() -> None:
    """A successful login clears the failed-attempt counter, so the failures
    must be CONSECUTIVE to trip the throttle. Without the reset-on-success,
    9 + 9 = 18 failures would blow past the cap of 10 and the final correct
    login would be throttled; with it, each run of 9 starts fresh."""
    from auth.users import login, signup
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        signup("resettable", "correct-password-123")
        for _ in range(9):  # one short of the cap
            assert login("resettable", "wrong") is None
        # A success resets the bucket…
        assert login("resettable", "correct-password-123") is not None
        # …so another 9 failures still don't trip it, and the correct
        # password is still accepted afterwards.
        for _ in range(9):
            assert login("resettable", "wrong") is None
        assert login("resettable", "correct-password-123") is not None
    finally:
        clear_buckets()


# ─── signup: invite claimed atomically BEFORE user creation ───────────────


def test_signup_claims_invite_before_creating_user(monkeypatch) -> None:
    """The invite must be claimed (atomic ``consume_invitation``) BEFORE the
    user row is created. If the claim fails — e.g. a concurrent signup won
    the race — signup must refuse and leave NO account behind. Otherwise one
    single-use invite could mint multiple users (privilege escalation when
    the invite grants admin)."""
    import auth.users as users
    from auth.invitations import create_invitation
    from auth.users import count_users

    admin = users.signup("admin-user", "admin-password-123")
    assert admin is not None
    inv = create_invitation(admin.user_id, role="user")
    assert inv is not None

    before = count_users()  # just the admin so far
    # Simulate losing the atomic claim (another signup consumed it first).
    monkeypatch.setattr(
        "auth.invitations.consume_invitation", lambda token, uid: False,
    )
    result = users.signup(
        "racer", "racer-password-123", invite_token=inv.invite_token,
    )
    assert result is None                # signup refused…
    assert count_users() == before       # …and 'racer' was NOT created


def test_signup_with_valid_invite_still_creates_user_and_consumes() -> None:
    """Reorder regression: a valid invite still mints the user with the
    invite's role AND marks the invite consumed by that user."""
    from auth.users import signup
    from auth.invitations import create_invitation, get_invitation_by_token

    admin = signup("admin-user", "admin-password-123")
    assert admin is not None
    inv = create_invitation(admin.user_id, role="admin")
    assert inv is not None

    user = signup(
        "invitee", "invitee-password-123", invite_token=inv.invite_token,
    )
    assert user is not None
    assert user.role == "admin"  # inherited from the invite
    consumed = get_invitation_by_token(inv.invite_token)
    assert consumed is not None
    assert consumed.consumed_at  # invite marked consumed…
    assert consumed.consumed_by_user_id == user.user_id  # …by this user


# ─── login: recovery-code fallback (lost authenticator) ───────────────────


def _enable_mfa_with_codes(username: str, password: str):
    """Sign up ``username``, enable MFA, return (user, secret, codes)."""
    from auth.users import signup
    from auth.mfa import enable_mfa, generate_secret

    user = signup(username, password)
    assert user is not None
    secret = generate_secret()
    ok, codes = enable_mfa(user.user_id, secret)
    assert ok is True
    assert codes  # a batch of plaintext recovery codes
    return user, secret, codes


def test_login_accepts_recovery_code_as_mfa_fallback() -> None:
    """A user who lost their authenticator can log in by entering a single-
    use recovery code in the mfa_code field — previously these codes were
    mintable but UNREACHABLE from login (a dead feature / lockout footgun)."""
    from auth.users import login
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        _, _secret, codes = _enable_mfa_with_codes(
            "rec-user", "correct-password-123"
        )
        user = login("rec-user", "correct-password-123", mfa_code=codes[0])
        assert user is not None
        assert user.username == "rec-user"
    finally:
        clear_buckets()


def test_login_recovery_code_is_single_use() -> None:
    """A recovery code consumed at login cannot be reused; a different
    unused code from the batch still works."""
    from auth.users import login
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        _, _secret, codes = _enable_mfa_with_codes(
            "rec-once", "correct-password-123"
        )
        assert login(
            "rec-once", "correct-password-123", mfa_code=codes[0]
        ) is not None
        # Same code again → rejected (consumed).
        assert login(
            "rec-once", "correct-password-123", mfa_code=codes[0]
        ) is None
        # A different, still-unused code → works.
        assert login(
            "rec-once", "correct-password-123", mfa_code=codes[1]
        ) is not None
    finally:
        clear_buckets()


def test_login_still_accepts_totp_after_recovery_wiring() -> None:
    """Regression: wiring the recovery-code fallback must not break the
    primary TOTP path."""
    from auth.users import login
    from auth.mfa import compute_totp
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        _, secret, _codes = _enable_mfa_with_codes(
            "totp-user", "correct-password-123"
        )
        user = login(
            "totp-user", "correct-password-123", mfa_code=compute_totp(secret)
        )
        assert user is not None
    finally:
        clear_buckets()


def test_login_rejects_unminted_recovery_code() -> None:
    """A recovery-shaped value that was never minted is rejected (None) —
    it must not authenticate just because it has the right shape."""
    from auth.users import login
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        _enable_mfa_with_codes("rec-bogus", "correct-password-123")
        assert login(
            "rec-bogus", "correct-password-123", mfa_code="ZZZZZ-ZZZZZ"
        ) is None
    finally:
        clear_buckets()


# ─── login: TOTP replay protection (#5) ───────────────────────────────────


def test_login_rejects_replayed_totp_code() -> None:
    """A TOTP code is single-use at login: the very code that just
    authenticated is rejected on a second login (anti-replay). Deterministic
    regardless of window-boundary timing — verify reports the CODE's step,
    and the consumed step is already recorded."""
    from auth.users import login
    from auth.mfa import compute_totp
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        _, secret, _ = _enable_mfa_with_codes(
            "replay-user", "correct-password-123"
        )
        code = compute_totp(secret)
        assert login(
            "replay-user", "correct-password-123", mfa_code=code
        ) is not None
        # SAME code again → rejected (the step is already consumed).
        assert login(
            "replay-user", "correct-password-123", mfa_code=code
        ) is None
    finally:
        clear_buckets()


def test_login_accepts_newer_totp_after_replay_block() -> None:
    """Replay protection blocks REUSE, not forward progress: a code from a
    later step still authenticates after an earlier one was consumed."""
    import time as _time
    from auth.users import login
    from auth.mfa import compute_totp
    from auth.rate_limit import clear_buckets

    clear_buckets()
    try:
        _, secret, _ = _enable_mfa_with_codes(
            "replay-fwd", "correct-password-123"
        )
        assert login(
            "replay-fwd", "correct-password-123",
            mfa_code=compute_totp(secret),
        ) is not None
        # A code from the next step (still inside the verify window) → ok.
        newer = compute_totp(secret, when=_time.time() + 30)
        assert login(
            "replay-fwd", "correct-password-123", mfa_code=newer
        ) is not None
    finally:
        clear_buckets()


def test_signup_rejects_case_insensitive_duplicate_username() -> None:
    """#13: a new username that collides with an existing one under case-
    folding is rejected (can't register 'Admin' alongside an existing 'admin')
    — closes a forward impersonation/confusion vector. Login lookup is
    unchanged (still case-sensitive); only NEW signups are constrained."""
    from auth.users import signup

    assert signup("admin", "correct-horse-battery-staple") is not None
    # Every case-variant is now a duplicate.
    assert signup("Admin", "another-good-password") is None
    assert signup("ADMIN", "another-good-password") is None
    assert signup("aDmIn", "another-good-password") is None
    # A genuinely distinct username is unaffected.
    assert signup("admin2", "another-good-password") is not None
