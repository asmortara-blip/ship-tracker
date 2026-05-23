"""auth.users — minimum-viable multi-user identity layer.

Design
------
This module lays the FOUNDATION for multi-user auth without a big-bang
migration. It provides:

- A ``User`` dataclass with NO password material — the hash + salt stay
  in the DB and never travel out of this module.
- ``signup`` / ``login`` / ``get_user`` / ``count_users`` helpers, all of
  which catch their own exceptions and return ``None`` on any failure
  (no info-leak between "bad username" and "bad password" on login,
  no raises bubbling up into the UI layer).
- Reuses ``auth.gate._hash_password`` so we do NOT introduce a new KDF
  — the same scrypt-with-PBKDF2-fallback pattern that gates the single
  password today.

Compatibility
-------------
The existing ``auth.gate.require_auth`` keeps working unchanged. The new
``auth.gate.require_auth_with_users`` (added in ``auth/gate.py``) is the
opt-in entry point that prefers users-table login when at least one user
exists, and falls back to the single-password gate otherwise. Per-user
query scoping is intentionally left for a follow-up — every domain table
already has a nullable ``user_id`` column from the v7 migration, ready
for that work, but no module reads or writes it yet.

What this module does NOT do
----------------------------
- No OAuth / SSO / email verification / password reset / MFA.
- No session-cookie issuance — session state lives in
  ``st.session_state.current_user`` and is managed by the UI layer.
- No "remember me" / refresh tokens.
- No per-user query scoping.

Username + password rules
-------------------------
- Username: ``^[A-Za-z0-9_-]{3,32}$`` (3–32 chars, ASCII letters /
  digits / underscore / dash). Case-sensitive at storage time; the
  UNIQUE index on the column is the source of truth for collisions.
- Password: minimum length 8. No upper-case / digit / symbol
  requirements — current cryptographic guidance (NIST SP 800-63B) is
  that length matters more than composition rules.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from auth.gate import _hash_password, _verify_password, SALT_BYTES


# ── Constraints ───────────────────────────────────────────────────────────

# 3–32 chars, ASCII letters / digits / underscore / dash. This is the
# canonical validator — callers must NOT roll their own regex.
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

# OWASP guidance treats 8 as a floor for "interactive" auth; tightening
# this further is a future concern and would be a separate migration.
_MIN_PASSWORD_LEN = 8

# Anything longer than this is almost certainly a paste-the-whole-key
# accident; we reject early rather than spend KDF time on it. scrypt /
# pbkdf2 don't care about length but the UI absolutely shouldn't accept
# 10 MB blobs.
_MAX_PASSWORD_LEN = 4096


# ── Data ──────────────────────────────────────────────────────────────────

@dataclass
class User:
    """The minimum identity surface a caller needs.

    Notably absent: ``password_hash`` / ``password_salt``. Those stay in
    the DB and never travel out of this module — even if a caller logs
    or pickles a ``User`` instance, the credential material is not
    exposed.
    """
    user_id: str
    username: str
    role: str
    created_at: str
    last_login_at: str


# ── Internal helpers ──────────────────────────────────────────────────────

def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        role=row["role"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public API ────────────────────────────────────────────────────────────

def signup(username: str, password: str) -> Optional[User]:
    """Register a new user.

    Returns the new ``User`` on success, ``None`` on any validation or
    persistence failure. The function never raises — every failure path
    returns ``None`` and logs at WARNING. The empty-return contract lets
    UI callers render a generic "could not create account" message
    without leaking which check failed (good for username enumeration
    resistance — bad usernames and duplicates both look the same).
    """
    try:
        if not isinstance(username, str) or not _USERNAME_PATTERN.fullmatch(username):
            return None
        if not isinstance(password, str):
            return None
        if len(password) < _MIN_PASSWORD_LEN or len(password) > _MAX_PASSWORD_LEN:
            return None

        from state.db import get_connection
        conn = get_connection()

        # Pre-check for duplicate (the UNIQUE index on username is the
        # source of truth, but checking up-front lets us avoid spending
        # KDF time on a doomed insert).
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing is not None:
            return None

        # Fresh salt + hash per user. NEVER reuse a salt across users —
        # ``secrets.token_bytes`` is the cryptographic random source.
        salt = secrets.token_bytes(SALT_BYTES)
        hashed = _hash_password(password, salt)

        user_id = secrets.token_urlsafe(16)
        created_at = _now_iso()

        try:
            conn.execute(
                """
                INSERT INTO users
                  (user_id, username, password_hash, password_salt,
                   role, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    hashed.hex(),
                    salt.hex(),
                    "user",
                    created_at,
                    "",
                ),
            )
        except sqlite3.IntegrityError:
            # Raced with another writer past the pre-check — collapse
            # into the same generic failure path as the explicit
            # duplicate branch above.
            return None

        new_user = User(
            user_id=user_id,
            username=username,
            role="user",
            created_at=created_at,
            last_login_at="",
        )
        # Audit-log the signup. We stamp the audit row with the NEW
        # user's id (not whatever was active in session_state at the
        # moment of signup, which is usually empty pre-login). Username
        # goes in the detail payload so security review can spot
        # account creations without joining against the users table.
        try:
            from auth.audit import record_audit
            record_audit(
                "signup",
                entity_type="user",
                entity_id=user_id,
                detail={"username": username},
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return new_user
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        logger.warning(f"auth.users.signup: failed for username={username!r}: {exc}")
        return None


def login(
    username: str,
    password: str,
    *,
    mfa_code: Optional[str] = None,
) -> Optional[User]:
    """Verify credentials and return the matching ``User``.

    Returns ``None`` for either a missing user OR a bad password OR
    (when MFA is enabled on the account) a missing/wrong second-factor
    code — the same return shape, so the UI cannot tell which step
    failed (no info leak about which usernames exist or whether MFA is
    enabled on a given account).

    MFA handling
    ------------
    If the account has ``mfa_enabled = 1`` in the database:

      * ``mfa_code=None`` → returns ``None``. The caller (the login
        form) is expected to call ``login_requires_mfa(username)``
        AFTER a password-only attempt fails to decide whether to
        surface "MFA required" vs. "wrong password" in the UI.
      * ``mfa_code=<6 digits>`` → the code is verified against the
        account's ``mfa_secret`` via ``auth.mfa.verify_totp``. On
        mismatch this function returns ``None`` — same shape as bad
        password, same enumeration resistance.

    Accounts WITHOUT MFA enabled ignore ``mfa_code`` entirely. This is
    deliberate: legacy callers that do not pass the kwarg continue to
    work, and a caller that passes a bogus code for a non-MFA account
    is not punished for it (the second factor is opt-in, not required).

    On success, ``last_login_at`` is updated to the current ISO-8601
    UTC timestamp before the ``User`` is returned, and the returned
    instance carries the new value. The login audit row carries
    ``detail={'mfa': True}`` when the second factor was exercised on
    this login, so a security review can spot which logins used MFA.
    """
    try:
        if not isinstance(username, str) or not isinstance(password, str):
            return None

        from state.db import get_connection
        conn = get_connection()

        row = conn.execute(
            """
            SELECT user_id, username, password_hash, password_salt,
                   role, created_at, last_login_at,
                   mfa_secret, mfa_enabled
              FROM users
             WHERE username = ?
            """,
            (username,),
        ).fetchone()
        if row is None:
            return None

        try:
            stored_hash = bytes.fromhex(row["password_hash"])
            salt = bytes.fromhex(row["password_salt"])
        except (ValueError, TypeError):
            # Corrupt row — treat like bad password to avoid revealing
            # that the user exists in a degraded state.
            logger.warning(
                f"auth.users.login: corrupt hash/salt for "
                f"username={username!r}"
            )
            return None

        if not _verify_password(password, stored_hash, salt):
            return None

        # MFA gate. The columns are nullable-with-default-0 from the v16
        # migration, but ``row[...]`` could still surface ``None`` on a
        # row that was hand-edited; coerce defensively.
        mfa_enabled = bool(int(row["mfa_enabled"] or 0))
        mfa_used = False
        if mfa_enabled:
            if mfa_code is None:
                # Caller must prompt for the second factor and retry.
                # Returning None (same shape as bad password) keeps the
                # enumeration-resistance contract; the public
                # ``login_requires_mfa`` helper exists for the UI to ask
                # explicitly whether this username carries MFA.
                return None
            try:
                from auth.mfa import verify_totp
                ok = verify_totp(row["mfa_secret"] or "", str(mfa_code))
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.warning(
                    f"auth.users.login: mfa verify failed "
                    f"for username={username!r}: {exc}"
                )
                ok = False
            if not ok:
                # Same return shape as bad password — no info leak about
                # which step failed. Failed MFA does NOT audit (same
                # enumeration-protection as failed password).
                return None
            mfa_used = True

        # Stamp last_login_at. A failure here doesn't fail the login —
        # the user has already authenticated; analytics is best-effort.
        now = _now_iso()
        try:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE user_id = ?",
                (now, row["user_id"]),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"auth.users.login: last_login_at update failed "
                f"for {username!r}: {exc}"
            )

        authenticated = User(
            user_id=row["user_id"],
            username=row["username"],
            role=row["role"],
            created_at=row["created_at"],
            last_login_at=now,
        )
        # Audit-log ONLY successful logins. Failed logins would also be
        # valuable in principle, but recording them here would let an
        # attacker run a username-enumeration probe against the audit
        # log (which is queryable by user_id). Keeping success-only
        # matches the bad_user / bad_password symmetry above — there
        # is no observable difference between the two failure paths.
        # The detail payload carries ``{'mfa': True}`` when the second
        # factor was exercised, so a security review can spot which
        # logins used MFA without digging through the timeline.
        try:
            from auth.audit import record_audit
            record_audit(
                "login",
                entity_type="user",
                entity_id=authenticated.user_id,
                user_id=authenticated.user_id,
                detail={"mfa": True} if mfa_used else None,
            )
        except Exception:  # noqa: BLE001
            pass
        return authenticated
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        logger.warning(f"auth.users.login: failed for username={username!r}: {exc}")
        return None


def login_requires_mfa(username: str) -> bool:
    """True iff ``username`` exists AND has ``mfa_enabled = 1``.

    The login UI calls this AFTER a password-only ``login`` attempt
    returns ``None`` to decide between:

      * "Wrong password" (username unknown, password wrong on a non-MFA
        account, password right on an MFA account but no code) and
      * "MFA code required" (password presumed right but the account
        carries MFA).

    The function does NOT verify the password — it only inspects the
    flag column. This is intentional: the caller has already tried the
    password and gotten ``None``, so the only useful additional bit is
    "should we now ask for the second factor?".

    Returns ``False`` on unknown user or any DB error. NEVER raises.

    NOTE: This helper does leak the boolean "does this username carry
    MFA?" to anyone who can call it. That is a deliberate trade-off —
    without this signal, the login form cannot decide whether to render
    the second-factor input field. Pair with a rate-limited login
    surface (the existing ``auth.gate`` lockout policy) to keep brute-
    force enumeration costly.
    """
    try:
        if not isinstance(username, str) or not username:
            return False
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT mfa_enabled FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return False
        return bool(int(row["mfa_enabled"] or 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.users.login_requires_mfa: failed for "
            f"username={username!r}: {exc}"
        )
        return False


def get_user(user_id: str) -> Optional[User]:
    """Look up a user by their primary key. Returns ``None`` if missing
    or on any error."""
    try:
        if not isinstance(user_id, str) or not user_id:
            return None
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            """
            SELECT user_id, username, role, created_at, last_login_at
              FROM users
             WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_user(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"auth.users.get_user: lookup failed for {user_id!r}: {exc}")
        return None


def count_users() -> int:
    """Number of registered users. Returns ``0`` on any error — callers
    use this to decide whether to fall back to the single-password
    gate, so a DB hiccup should NOT trick them into showing the
    multi-user UI to nobody."""
    try:
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) if row is not None else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"auth.users.count_users: failed: {exc}")
        return 0


def list_users() -> list[User]:
    """Return every registered user, ordered newest-first.

    Returns ``[]`` on any error — the helper is consumed by operator
    tooling (``python -m tools.ops users list``) and must never raise
    into the CLI. Credential material (``password_hash`` / ``password_salt``)
    is dropped at the ``_row_to_user`` boundary so this list can be
    JSON-serialized without leaking secrets.
    """
    try:
        from state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT user_id, username, role, created_at, last_login_at
              FROM users
             ORDER BY created_at DESC
            """,
        ).fetchall()
        return [_row_to_user(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"auth.users.list_users: failed: {exc}")
        return []


__all__ = [
    "User",
    "signup",
    "login",
    "login_requires_mfa",
    "get_user",
    "count_users",
    "list_users",
]
