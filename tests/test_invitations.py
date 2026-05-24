"""Tests for ``auth.invitations`` — admin-issued signup invite links (v21).

Exercises the five public helpers (``create_invitation`` /
``get_invitation_by_token`` / ``consume_invitation`` /
``list_invitations`` / ``revoke_invitation``) plus the signup
extension in ``auth.users.signup`` that consumes an invite at signup
time.

Defining properties under test
------------------------------
  * ``create_invitation`` mints a 32-char URL-safe token and persists
    a row with the right metadata. The token is unguessable
    (``secrets.token_urlsafe``).
  * The role defaults to ``'user'`` — an explicit ``role='admin'`` is
    the ONLY way to mint an admin-invite. A typo (case-mismatch,
    unrecognized value) returns ``None`` rather than silently
    downgrading.
  * ``get_invitation_by_token`` round-trips the row including the
    email + role + expiry.
  * ``consume_invitation`` flips the row exactly once: a second
    consume of the same token returns ``False`` even with the same
    user_id.
  * An expired invitation cannot be consumed.
  * ``revoke_invitation`` deletes an unconsumed row and fails closed
    on an already-consumed row.
  * ``list_invitations`` filters by inviter, includes consumed only
    on opt-in, and is newest-first ordered.
  * ``auth.users.signup(invite_token=...)`` validates the invite and
    consumes it atomically with the new-user insert. A bad/expired/
    consumed invite returns None (same shape as a duplicate username).
  * A pinned-email invite rejects a signup whose username does not
    equal the invite's email.
  * A signup that succeeds via an admin-invite inherits the admin
    role.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Per-test SQLite at tmp_path — same pattern as test_mfa.py."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ── create_invitation ─────────────────────────────────────────────────────

def test_create_invitation_happy_path() -> None:
    """A basic create with the defaults: 32-char URL-safe token,
    role='user', expires 7 days out."""
    from auth.invitations import create_invitation
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    assert admin is not None

    inv = create_invitation(admin.user_id)
    assert inv is not None
    assert inv.invited_by_user_id == admin.user_id
    assert inv.role == "user"
    assert inv.email is None
    assert inv.consumed_at is None
    assert inv.consumed_by_user_id is None
    # 32 chars of URL-safe base64.
    assert isinstance(inv.invite_token, str)
    assert len(inv.invite_token) == 32
    # Round-trip the expiry — must be ~7 days from now (within a 2-min
    # tolerance for test wall-clock).
    expires = datetime.fromisoformat(inv.expires_at)
    delta = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 7 * 86400 - 120 < delta < 7 * 86400 + 120


def test_create_invitation_admin_role_requires_explicit_request() -> None:
    """An invite must NEVER silently grant admin — the caller must
    explicitly pass ``role='admin'``."""
    from auth.invitations import create_invitation
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    assert admin is not None

    # Default is user — never admin.
    inv = create_invitation(admin.user_id)
    assert inv is not None
    assert inv.role == "user"

    # Explicit admin works.
    admin_inv = create_invitation(admin.user_id, role="admin")
    assert admin_inv is not None
    assert admin_inv.role == "admin"

    # A typo / unknown role returns None — no silent coercion.
    assert create_invitation(admin.user_id, role="Admin") is None
    assert create_invitation(admin.user_id, role="superuser") is None
    assert create_invitation(admin.user_id, role="") is None


def test_create_invitation_rejects_invalid_inputs() -> None:
    """Every validation failure returns None — empty inviter, bad
    expires_in_days, etc."""
    from auth.invitations import create_invitation

    assert create_invitation("") is None
    assert create_invitation(None) is None  # type: ignore[arg-type]
    assert create_invitation("user-1", expires_in_days=0) is None
    assert create_invitation("user-1", expires_in_days=-1) is None
    # Non-string email is rejected.
    assert create_invitation("user-1", email=123) is None  # type: ignore[arg-type]


def test_create_invitation_stores_email_when_provided() -> None:
    """A pinned-email invite must persist the email so the signup
    contract (username must equal email) can be enforced."""
    from auth.invitations import create_invitation, get_invitation_by_token
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    assert admin is not None

    inv = create_invitation(admin.user_id, email="bob")
    assert inv is not None
    assert inv.email == "bob"

    # Round-trip via the lookup helper.
    fetched = get_invitation_by_token(inv.invite_token)
    assert fetched is not None
    assert fetched.email == "bob"


# ── get_invitation_by_token ───────────────────────────────────────────────

def test_get_invitation_by_token_returns_none_for_unknown() -> None:
    from auth.invitations import get_invitation_by_token

    assert get_invitation_by_token("not-a-real-token") is None
    assert get_invitation_by_token("") is None
    assert get_invitation_by_token(None) is None  # type: ignore[arg-type]


# ── consume_invitation ───────────────────────────────────────────────────

def test_consume_invitation_flips_row_once() -> None:
    """First consume returns True and stamps consumed_at; second
    consume of the same token returns False (single-use contract)."""
    from auth.invitations import (
        consume_invitation,
        create_invitation,
        get_invitation_by_token,
    )
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    invitee = signup("bob", "correct-password-123")
    assert admin is not None and invitee is not None

    inv = create_invitation(admin.user_id)
    assert inv is not None

    assert consume_invitation(inv.invite_token, invitee.user_id) is True
    # Row must show as consumed.
    fetched = get_invitation_by_token(inv.invite_token)
    assert fetched is not None
    assert fetched.consumed_at is not None
    assert fetched.consumed_by_user_id == invitee.user_id

    # Second consume must fail.
    assert consume_invitation(inv.invite_token, invitee.user_id) is False


def test_consume_invitation_rejects_expired_invite() -> None:
    """An invite past its expires_at must NOT consume — the time
    window is the load-bearing safety of the feature."""
    from auth.invitations import (
        consume_invitation,
        create_invitation,
    )
    from auth.users import signup
    from state.db import get_connection

    admin = signup("alice", "correct-password-123")
    invitee = signup("bob", "correct-password-123")
    assert admin is not None and invitee is not None

    inv = create_invitation(admin.user_id)
    assert inv is not None

    # Backdate the expiry to 1h in the past.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = get_connection()
    conn.execute(
        "UPDATE user_invitations SET expires_at = ? WHERE invite_id = ?",
        (past, inv.invite_id),
    )

    assert consume_invitation(inv.invite_token, invitee.user_id) is False


def test_consume_invitation_rejects_empty_inputs() -> None:
    from auth.invitations import consume_invitation

    assert consume_invitation("", "u-1") is False
    assert consume_invitation("token", "") is False


# ── list_invitations / revoke_invitation ─────────────────────────────────

def test_list_invitations_filters_by_inviter() -> None:
    """The per-user view must show only the inviter's own invites —
    admin-wide view is for a future commit."""
    from auth.invitations import create_invitation, list_invitations
    from auth.users import signup

    alice = signup("alice", "correct-password-123")
    bob = signup("bob", "correct-password-123")
    assert alice is not None and bob is not None

    inv_a1 = create_invitation(alice.user_id, email="x")
    inv_a2 = create_invitation(alice.user_id, email="y")
    inv_b1 = create_invitation(bob.user_id, email="z")
    assert inv_a1 and inv_a2 and inv_b1

    alice_view = list_invitations(invited_by_user_id=alice.user_id)
    bob_view = list_invitations(invited_by_user_id=bob.user_id)
    assert {i.invite_id for i in alice_view} == {inv_a1.invite_id, inv_a2.invite_id}
    assert {i.invite_id for i in bob_view} == {inv_b1.invite_id}

    # Admin-wide view (no filter) sees all three.
    all_view = list_invitations()
    assert {i.invite_id for i in all_view} == {
        inv_a1.invite_id, inv_a2.invite_id, inv_b1.invite_id
    }


def test_list_invitations_excludes_consumed_by_default() -> None:
    """The default ``include_consumed=False`` is what drives the
    "pending invites" UI panel."""
    from auth.invitations import (
        consume_invitation,
        create_invitation,
        list_invitations,
    )
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    invitee = signup("bob", "correct-password-123")
    assert admin is not None and invitee is not None

    inv = create_invitation(admin.user_id)
    assert inv is not None
    consume_invitation(inv.invite_token, invitee.user_id)

    # Default: consumed invite is hidden.
    assert list_invitations(invited_by_user_id=admin.user_id) == []
    # Opt-in: included.
    with_consumed = list_invitations(
        invited_by_user_id=admin.user_id, include_consumed=True
    )
    assert len(with_consumed) == 1
    assert with_consumed[0].consumed_at is not None


def test_revoke_invitation_deletes_unconsumed_only() -> None:
    """Revoke must succeed on a pending invite and fail on an already-
    consumed invite (consumed rows are audit-trail; immutable)."""
    from auth.invitations import (
        consume_invitation,
        create_invitation,
        get_invitation_by_token,
        list_invitations,
        revoke_invitation,
    )
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    invitee = signup("bob", "correct-password-123")
    assert admin is not None and invitee is not None

    # Pending invite — revoke succeeds.
    pending = create_invitation(admin.user_id)
    assert pending is not None
    assert revoke_invitation(pending.invite_id) is True
    assert get_invitation_by_token(pending.invite_token) is None

    # Consumed invite — revoke fails.
    other = create_invitation(admin.user_id)
    assert other is not None
    consume_invitation(other.invite_token, invitee.user_id)
    assert revoke_invitation(other.invite_id) is False
    # Row still exists.
    assert get_invitation_by_token(other.invite_token) is not None

    # Unknown id — fails.
    assert revoke_invitation("does-not-exist") is False
    assert revoke_invitation("") is False


# ── signup integration ──────────────────────────────────────────────────

def test_signup_with_valid_invite_token_consumes_invite() -> None:
    """The full happy-path: admin creates invite → recipient signs up
    with the token → user row exists AND invite row is flipped to
    consumed in the same logical flow."""
    from auth.invitations import create_invitation, get_invitation_by_token
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    assert admin is not None

    inv = create_invitation(admin.user_id)
    assert inv is not None

    invitee = signup(
        "bob", "correct-password-123", invite_token=inv.invite_token
    )
    assert invitee is not None
    assert invitee.username == "bob"
    # Default role.
    assert invitee.role == "user"

    # Invite is now consumed.
    fetched = get_invitation_by_token(inv.invite_token)
    assert fetched is not None
    assert fetched.consumed_at is not None
    assert fetched.consumed_by_user_id == invitee.user_id


def test_signup_with_invalid_invite_token_returns_none() -> None:
    """A bad/expired/consumed invite rejects the signup the same way
    a duplicate username does — None, no info leak."""
    from auth.invitations import (
        consume_invitation,
        create_invitation,
    )
    from auth.users import signup
    from state.db import get_connection

    admin = signup("alice", "correct-password-123")
    other = signup("bob", "correct-password-123")
    assert admin is not None and other is not None

    # Unknown token.
    assert signup(
        "carol", "correct-password-123", invite_token="nope"
    ) is None

    # Empty token.
    assert signup(
        "carol", "correct-password-123", invite_token=""
    ) is None

    # Already-consumed token.
    inv = create_invitation(admin.user_id)
    assert inv is not None
    consume_invitation(inv.invite_token, other.user_id)
    assert signup(
        "carol", "correct-password-123", invite_token=inv.invite_token
    ) is None

    # Expired token.
    inv2 = create_invitation(admin.user_id)
    assert inv2 is not None
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = get_connection()
    conn.execute(
        "UPDATE user_invitations SET expires_at = ? WHERE invite_id = ?",
        (past, inv2.invite_id),
    )
    assert signup(
        "dave", "correct-password-123", invite_token=inv2.invite_token
    ) is None


def test_signup_with_pinned_email_requires_username_match() -> None:
    """A pinned-email invite must reject any signup whose username
    does not equal the pinned email — usernames double as the
    canonical login identifier in this codebase."""
    from auth.invitations import create_invitation, get_invitation_by_token
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    assert admin is not None

    # Pinned to "bob".
    inv = create_invitation(admin.user_id, email="bob")
    assert inv is not None

    # Carol cannot consume the invite pinned to bob.
    assert signup(
        "carol", "correct-password-123", invite_token=inv.invite_token
    ) is None
    # Invite is still pending.
    fetched = get_invitation_by_token(inv.invite_token)
    assert fetched is not None and fetched.consumed_at is None

    # Bob can.
    bob = signup(
        "bob", "correct-password-123", invite_token=inv.invite_token
    )
    assert bob is not None


def test_signup_with_admin_invite_inherits_admin_role() -> None:
    """An admin-role invite must mint an admin user. This is the
    primary use of the admin-invite — bootstrapping co-admins
    without sharing a password."""
    from auth.invitations import create_invitation
    from auth.users import signup

    admin = signup("alice", "correct-password-123")
    assert admin is not None

    inv = create_invitation(admin.user_id, role="admin")
    assert inv is not None

    bob = signup(
        "bob", "correct-password-123", invite_token=inv.invite_token
    )
    assert bob is not None
    assert bob.role == "admin"


def test_signup_without_invite_still_works() -> None:
    """The invite_token kwarg is optional — pre-v21 signup behaviour
    must continue to work for any caller that doesn't pass it."""
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    assert user.username == "alice"
    assert user.role == "user"
