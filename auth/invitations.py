"""auth.invitations — admin-issued signup invite links (schema v21).

Design
------
An admin operator creates an invite via :func:`create_invitation`. The
function returns the full ``Invitation`` row, including the random
32-char URL-safe ``invite_token`` the admin then shares with the
recipient out-of-band (email, Slack, etc.). The recipient supplies
the token to :func:`auth.users.signup` along with their chosen
password; signup calls :func:`consume_invitation` on success to flip
the invite row to consumed.

The invite is a SIGNED authorization, not an authentication factor —
having the token does NOT log the recipient in. It only pre-
authorizes their signup: it can pin a specific email, grant a non-
default role, and bound the time window in which a signup is allowed.

What this module does NOT do
----------------------------
* No email delivery — the admin shares the token out-of-band. Adding
  an SMTP send is a future commit if we wire one up.
* No revocation of consumed invites — once consumed, the
  ``consumed_at`` stamp is permanent (the row is part of the audit
  trail). :func:`revoke_invitation` only works on unconsumed rows.
* No multi-use invites — every row is single-use. ``consume_invitation``
  rejects a token whose ``consumed_at`` is already set.
* No invitation chaining — a freshly-invited user cannot themselves
  invite others until their role grants it (future RBAC commit).

Every public helper NEVER raises — failures return ``None`` / ``False``
/ ``[]`` and log at WARNING.
"""
from __future__ import annotations

import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger


# ── Constants ─────────────────────────────────────────────────────────────

# 32 URL-safe chars matches what auth.tokens uses for API tokens. The
# token IS the secret — having it pre-authorizes a signup. 32 chars of
# secrets.token_urlsafe gives ~192 bits of entropy, well above what a
# rate-limited signup surface can be brute-forced through.
#
# ``secrets.token_urlsafe(N)`` returns roughly ``N * 4/3`` chars; we
# pass 24 to land at exactly 32 chars after the urlsafe encoding.
_TOKEN_NBYTES = 24

# 7 days is the default invite lifetime — long enough to survive a
# weekend / holiday, short enough that a leaked invite stops working
# before it lands in someone's grep history.
_DEFAULT_EXPIRES_DAYS = 7

# Roles an invitation can grant. ``'user'`` is the default; ``'admin'``
# must be explicitly requested at create time AND is the only other
# value accepted. A typo like ``'Admin'`` is rejected (case-sensitive)
# rather than silently downgraded — the task spec is explicit that an
# invite must not silently auto-grant admin.
_VALID_ROLES = frozenset({"user", "admin"})


# ── Data ──────────────────────────────────────────────────────────────────

@dataclass
class Invitation:
    """One row from the ``user_invitations`` table.

    All timestamps are ISO-8601 UTC. ``consumed_at`` / ``consumed_by_user_id``
    are empty strings until the invite is consumed (the SQL column is
    NULLable but the dataclass smooths NULL → empty-string for
    consumer ergonomics — consumers do ``if inv.consumed_at:`` rather
    than ``if inv.consumed_at is not None:``).
    """
    invite_id: str
    invite_token: str
    email: Optional[str]
    role: str
    invited_by_user_id: str
    expires_at: str
    consumed_at: Optional[str]
    consumed_by_user_id: Optional[str]
    created_at: str


# ── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_invitation(row: sqlite3.Row) -> Invitation:
    return Invitation(
        invite_id=row["invite_id"],
        invite_token=row["invite_token"],
        email=row["email"],  # may be NULL
        role=row["role"],
        invited_by_user_id=row["invited_by_user_id"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],  # may be NULL
        consumed_by_user_id=row["consumed_by_user_id"],  # may be NULL
        created_at=row["created_at"],
    )


def _is_expired(iso_expires: str, *, now: Optional[datetime] = None) -> bool:
    """Return True iff ``iso_expires`` is strictly in the past.

    Helper extracted for readability — every consumer pivots on
    "is this invite still in its validity window?". A malformed
    timestamp counts as expired (fail closed) — better to invalidate
    the row than to let a parse failure silently let an old invite
    through.
    """
    try:
        when = datetime.fromisoformat(iso_expires)
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return when <= current


# ── Public API ────────────────────────────────────────────────────────────

def create_invitation(
    invited_by_user_id: str,
    *,
    email: Optional[str] = None,
    role: str = "user",
    expires_in_days: int = _DEFAULT_EXPIRES_DAYS,
) -> Optional[Invitation]:
    """Create a new invitation row and return it.

    Args:
        invited_by_user_id: The admin (or future-invited user) whose id
                            is stamped on the row. Must be non-empty —
                            the audit trail needs an attribution.
        email:              Optional. If supplied, the invitation is
                            bound to this email at signup time —
                            :func:`auth.users.signup` will reject a
                            signup whose chosen username/email mismatch
                            this value. ``None`` (the default) allows
                            any recipient to consume the invite.
        role:               The role to grant on consumption. One of
                            ``{'user', 'admin'}``. Defaults to
                            ``'user'`` — an admin invite MUST be
                            explicitly requested. A typo (case
                            mismatch, unrecognized value) returns
                            ``None`` rather than silently coercing.
        expires_in_days:    Lifetime of the invite, in whole days.
                            Must be > 0 (a 0-day invite is already
                            expired the moment it's created — almost
                            certainly a bug in the caller). Default 7.

    Returns:
        The new ``Invitation`` on success, ``None`` on any failure.
        NEVER raises.

    Audit-logged at ``action='create_invitation'`` with
    ``detail={'invite_id': ..., 'role': ..., 'email_provided': bool}``
    so a security review can spot invite mints without dragging the
    token plaintext into the log.
    """
    try:
        if not isinstance(invited_by_user_id, str) or not invited_by_user_id:
            return None
        if not isinstance(role, str) or role not in _VALID_ROLES:
            return None
        if not isinstance(expires_in_days, int) or expires_in_days <= 0:
            return None
        # email is optional; reject only non-string non-None values.
        if email is not None and not isinstance(email, str):
            return None
        if isinstance(email, str):
            email = email.strip()
            if not email:
                # Empty-string email is the same as "no binding" —
                # collapse to None so we don't store an empty string
                # that would later test as falsy/truthy inconsistently.
                email = None

        from state.db import get_connection
        conn = get_connection()

        # 32 URL-safe chars from secrets.token_urlsafe(24). The token
        # column carries a UNIQUE index so a duplicate would raise
        # IntegrityError; that's a vanishingly rare event (~192 bits
        # of entropy) but we catch it cleanly rather than crash.
        invite_token = secrets.token_urlsafe(_TOKEN_NBYTES)
        invite_id = str(uuid.uuid4())
        created_at = _now_iso()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        ).isoformat()

        try:
            conn.execute(
                """
                INSERT INTO user_invitations
                  (invite_id, invite_token, email, role,
                   invited_by_user_id, expires_at, consumed_at,
                   consumed_by_user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    invite_id,
                    invite_token,
                    email,
                    role,
                    invited_by_user_id,
                    expires_at,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            logger.warning(
                f"auth.invitations.create_invitation: integrity error "
                f"on insert: {exc}"
            )
            return None

        invitation = Invitation(
            invite_id=invite_id,
            invite_token=invite_token,
            email=email,
            role=role,
            invited_by_user_id=invited_by_user_id,
            expires_at=expires_at,
            consumed_at=None,
            consumed_by_user_id=None,
            created_at=created_at,
        )

        try:
            from auth.audit import record_audit
            record_audit(
                "create_invitation",
                entity_type="invitation",
                entity_id=invite_id,
                detail={
                    "invite_id": invite_id,
                    "role": role,
                    "email_provided": email is not None,
                },
                user_id=invited_by_user_id,
            )
        except Exception:  # noqa: BLE001
            pass

        return invitation
    except Exception as exc:  # noqa: BLE001 — generic by contract
        logger.warning(
            f"auth.invitations.create_invitation: failed for "
            f"invited_by={invited_by_user_id!r}: {exc}"
        )
        return None


def get_invitation_by_token(token: str) -> Optional[Invitation]:
    """Look up an invitation by its plaintext token.

    Returns the full ``Invitation`` regardless of whether the invite
    is currently valid — the caller (``consume_invitation`` /
    ``auth.users.signup``) is responsible for checking ``expires_at``
    and ``consumed_at`` before honouring it. Returns ``None`` for
    unknown / malformed tokens or any DB error.

    NEVER raises.
    """
    try:
        if not isinstance(token, str) or not token:
            return None
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            """
            SELECT invite_id, invite_token, email, role,
                   invited_by_user_id, expires_at, consumed_at,
                   consumed_by_user_id, created_at
              FROM user_invitations
             WHERE invite_token = ?
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_invitation(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.invitations.get_invitation_by_token: failed: {exc}"
        )
        return None


def consume_invitation(token: str, user_id: str) -> bool:
    """Mark the invite identified by ``token`` as consumed by ``user_id``.

    Returns ``True`` on a successful consumption, ``False`` if:

      * the token is unknown / malformed,
      * the invite has already been consumed,
      * the invite has expired,
      * ``user_id`` is empty,
      * any DB error.

    The function is INTENTIONALLY NOT idempotent — a second call with
    the same token returns ``False`` even if it succeeded the first
    time. The ``consumed_at`` stamp is the single source of truth for
    "has this invite been used"; tests can rely on the
    "second-consume-fails" contract.

    Audit-logged at ``action='consume_invitation'`` on success.

    NEVER raises.
    """
    try:
        if not isinstance(token, str) or not token:
            return False
        if not isinstance(user_id, str) or not user_id:
            return False

        invite = get_invitation_by_token(token)
        if invite is None:
            return False
        if invite.consumed_at is not None and invite.consumed_at != "":
            return False
        if _is_expired(invite.expires_at):
            return False

        from state.db import get_connection
        conn = get_connection()
        consumed_at = _now_iso()
        # Use a conditional UPDATE so a race between two
        # consume_invitation calls on the same token gets resolved
        # by the DB — only ONE update will flip the row from NULL to
        # the timestamp; the other gets rowcount=0.
        cur = conn.execute(
            """
            UPDATE user_invitations
               SET consumed_at = ?, consumed_by_user_id = ?
             WHERE invite_id = ?
               AND consumed_at IS NULL
            """,
            (consumed_at, user_id, invite.invite_id),
        )
        if cur.rowcount == 0:
            # Raced with another consume — fail closed.
            return False

        try:
            from auth.audit import record_audit
            record_audit(
                "consume_invitation",
                entity_type="invitation",
                entity_id=invite.invite_id,
                detail={"consumed_by_user_id": user_id},
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass

        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.invitations.consume_invitation: failed: {exc}"
        )
        return False


def list_invitations(
    *,
    invited_by_user_id: Optional[str] = None,
    include_consumed: bool = False,
) -> list[Invitation]:
    """Return invitations matching the filters, newest-first.

    Args:
        invited_by_user_id: ``None`` returns invitations from every
                            inviter (admin-wide view). A non-empty
                            string filters strictly to that inviter
                            — per the task spec, an admin only sees
                            their own invites in the per-user view;
                            a future RBAC commit can grow an
                            "admin sees all" mode.
        include_consumed:   Default ``False`` — only unconsumed
                            invites are returned (the UI's "pending
                            invites" view). Set ``True`` to include
                            consumed rows as well (audit view).

    Returns ``[]`` on any error. NEVER raises.
    """
    try:
        from state.db import get_connection
        conn = get_connection()

        clauses: list[str] = ["1=1"]
        params: list = []
        if invited_by_user_id is not None:
            clauses.append("invited_by_user_id = ?")
            params.append(invited_by_user_id)
        if not include_consumed:
            clauses.append("consumed_at IS NULL")

        sql = (
            "SELECT invite_id, invite_token, email, role, "
            "invited_by_user_id, expires_at, consumed_at, "
            "consumed_by_user_id, created_at "
            "FROM user_invitations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC"
        )

        rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_invitation(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.invitations.list_invitations: failed: {exc}"
        )
        return []


def revoke_invitation(invite_id: str) -> bool:
    """Delete an UNCONSUMED invitation row.

    The hard DELETE (not a flag flip) is deliberate: a revoked invite
    has no audit value beyond the original ``create_invitation``
    audit row, and leaving a "revoked" state would complicate the
    consumed-vs-unconsumed query model. Already-consumed invites
    cannot be revoked — those rows are part of the consumption
    audit trail.

    Returns ``True`` if the delete affected exactly one
    unconsumed row, ``False`` otherwise (unknown id, already
    consumed, or any DB error). NEVER raises.

    Audit-logged at ``action='revoke_invitation'`` on success.
    """
    try:
        if not isinstance(invite_id, str) or not invite_id:
            return False
        from state.db import get_connection
        conn = get_connection()
        # Only delete if the row is currently unconsumed — a consumed
        # invite is part of the audit trail.
        cur = conn.execute(
            """
            DELETE FROM user_invitations
             WHERE invite_id = ?
               AND consumed_at IS NULL
            """,
            (invite_id,),
        )
        if cur.rowcount == 0:
            return False

        try:
            from auth.audit import record_audit
            record_audit(
                "revoke_invitation",
                entity_type="invitation",
                entity_id=invite_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.invitations.revoke_invitation: failed for "
            f"invite_id={invite_id!r}: {exc}"
        )
        return False


__all__ = [
    "Invitation",
    "create_invitation",
    "get_invitation_by_token",
    "consume_invitation",
    "list_invitations",
    "revoke_invitation",
]
