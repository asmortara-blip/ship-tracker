"""auth.tokens — per-user API access tokens (PATs).

Design
------
Multi-user auth (v7) and the audit log (v10) ship in earlier commits.
This module adds the third leg: long-lived per-user secrets that
external scripts use to authenticate to (future) API endpoints
without needing the user's password.

Lifecycle:

1. ``create_token(user_id, label)`` — generates a fresh 43-char
   URL-safe secret via ``secrets.token_urlsafe(32)``, hashes via
   the SAME KDF the password layer uses (``auth.gate._hash_password``
   — scrypt with a PBKDF2-HMAC-SHA256 fallback on platforms where
   scrypt is unavailable), and writes ONLY the hash + salt + first
   8 chars (the "prefix") to the ``api_tokens`` table. The raw
   secret is returned ONCE to the caller and never written anywhere.
2. ``verify_token(raw_token)`` — looks up by prefix in O(log n) via
   ``idx_api_tokens_prefix``, then constant-time compares the hash
   via ``auth.gate._verify_password`` (``hmac.compare_digest``).
   Stamps ``last_used_at`` on a successful verify. Returns ``None``
   for any miss / revoked row / hash mismatch — no information leak
   about whether the prefix existed.
3. ``list_tokens(user_id)`` — newest-first list of a user's tokens.
   Returns the ``ApiToken`` metadata dataclass which does NOT carry
   the hash or salt — even if a caller logs / pickles a token, the
   credential material never travels out of this module.
4. ``revoke_token(token_id, *, user_id)`` — sets ``revoked=1`` ONLY
   when the token belongs to the supplied user. Cross-user revocation
   is silently a no-op (returns ``False``).

What this module does NOT do
----------------------------
* No HTTP middleware — that's the (future) API-server commit.
* No UI surface — exposing tokens in ``ui.tab_alerts`` or a new
  auth tab is a follow-up. This commit ships the data layer only.
* No automatic expiry. A token lives until ``revoke_token`` is
  called. (We could add an ``expires_at`` column later; the schema
  is designed so that's an additive ALTER, not a breaking change.)
* No raw-token caching. The raw token exists ONLY in the
  ``create_token`` return value — nowhere else in process memory.

Security properties under test
------------------------------
* The raw secret is never persisted: only the hash + salt + prefix.
* The audit-log hooks (create + revoke) carry the label but NEVER
  the raw secret OR the hash.
* ``verify_token`` uses ``hmac.compare_digest`` (via
  ``auth.gate._verify_password``) for constant-time comparison.
* All functions wrap their SQLite ops in try/except → return
  None/False/[] on error. None of them raises.
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from auth.gate import SALT_BYTES, _hash_password, _verify_password


# ── Tuning constants ──────────────────────────────────────────────────────

# Length (in bytes) of the random material that becomes the raw
# secret. ``secrets.token_urlsafe(32)`` produces 43 chars of URL-safe
# base64 — long enough that brute-force is irrelevant against a
# scrypt/PBKDF2 hash with per-token salt.
_TOKEN_RAW_BYTES = 32

# Number of characters the plaintext PREFIX stores. The prefix is the
# first N chars of the raw secret and lives unhashed in the
# ``token_prefix`` column with a supporting index. It exists so
# ``verify_token`` can look up by prefix in O(log n) instead of
# scanning every row + KDF-hashing each one. 8 chars at 6 bits each
# = 48 bits of search space — too small to be the secret on its own,
# but large enough that prefix collisions are vanishingly rare in
# realistic deployments. The full hash compare is the security
# boundary; the prefix is purely an index hint.
_TOKEN_PREFIX_LEN = 8


# ── Data ──────────────────────────────────────────────────────────────────

@dataclass
class ApiToken:
    """Public-facing metadata for one row in the ``api_tokens`` table.

    Notably absent: ``token_hash`` / ``token_salt``. Those stay in the
    DB and never travel out of this module — even if a caller logs or
    pickles an ``ApiToken``, the credential material is not exposed.
    The raw token itself is returned ONLY from ``create_token`` and is
    never re-derivable from this dataclass.
    """
    token_id: str
    user_id: str
    label: str
    token_prefix: str
    created_at: str
    last_used_at: str
    revoked: bool


# ── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_token(row: sqlite3.Row) -> ApiToken:
    """Map a sqlite3.Row from the api_tokens table to an ApiToken.

    Drops the ``token_hash`` and ``token_salt`` columns on purpose —
    callers outside this module must never see the hash or salt.
    """
    return ApiToken(
        token_id=row["token_id"],
        user_id=row["user_id"],
        label=row["label"],
        token_prefix=row["token_prefix"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked=bool(row["revoked"]),
    )


# ── Public API: write ─────────────────────────────────────────────────────

def create_token(user_id: str, label: str) -> Optional[tuple[ApiToken, str]]:
    """Mint a fresh API token for ``user_id`` with the supplied label.

    Returns a ``(ApiToken, raw_secret)`` tuple on success. The
    ``raw_secret`` is a 43-char URL-safe base64 string and is the
    ONLY copy that will ever exist outside the user's possession —
    the database stores only the hash + salt + 8-char prefix. The
    caller is responsible for handing the raw secret back to the user
    (typically once, at creation time).

    Returns ``None`` on any validation or persistence failure. The
    function never raises — every failure path returns ``None`` and
    logs at WARNING.

    Args:
        user_id: The id of the user who owns the new token. Must be
                 a non-empty string. We do NOT enforce a foreign-key
                 reference to ``users`` here: the foreign-key check
                 belongs at the UI layer (which already knows the
                 active user via ``state.user_scope.current_user_id``)
                 and the looser contract here makes the module
                 testable in isolation without seeding a user row.
        label:   A user-supplied free-form label so the user can tell
                 their tokens apart in a UI list ("CI bot", "Personal
                 laptop", …). Must be a non-empty string.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return None
        if not isinstance(label, str) or not label:
            return None

        # Generate the raw secret + per-token salt FIRST so we have
        # everything we need before touching the DB. The salt is
        # cryptographic-grade random (``secrets.token_bytes``); the
        # raw secret is 32 bytes of entropy encoded as URL-safe
        # base64 → 43 chars long.
        raw_token = secrets.token_urlsafe(_TOKEN_RAW_BYTES)
        salt = secrets.token_bytes(SALT_BYTES)
        hashed = _hash_password(raw_token, salt)
        token_prefix = raw_token[:_TOKEN_PREFIX_LEN]

        token_id = secrets.token_urlsafe(16)
        created_at = _now_iso()

        from state.db import get_connection
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO api_tokens
              (token_id, user_id, label, token_hash, token_salt,
               token_prefix, created_at, last_used_at, revoked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                token_id,
                user_id,
                label,
                hashed.hex(),
                salt.hex(),
                token_prefix,
                created_at,
                "",
            ),
        )

        # Audit-log the creation. NEVER include the raw secret OR the
        # hash in the audit detail — only the user-facing label and
        # the plaintext prefix (which is already index-visible and so
        # is not additional information leak).
        try:
            from auth.audit import record_audit
            record_audit(
                "create_api_token",
                entity_type="api_token",
                entity_id=token_id,
                detail={"label": label, "prefix": token_prefix},
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass

        meta = ApiToken(
            token_id=token_id,
            user_id=user_id,
            label=label,
            token_prefix=token_prefix,
            created_at=created_at,
            last_used_at="",
            revoked=False,
        )
        return meta, raw_token
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        logger.warning(
            f"auth.tokens.create_token: failed for user_id={user_id!r}: {exc}"
        )
        return None


def revoke_token(token_id: str, *, user_id: str) -> bool:
    """Mark ``token_id`` as revoked. Cross-user revocation is rejected.

    Sets ``revoked = 1`` ONLY when the row's ``user_id`` matches the
    supplied ``user_id``. Returns ``True`` if exactly one row was
    updated, ``False`` otherwise (token does not exist, token belongs
    to a different user, or any DB error).

    The function never raises. The cross-user check is enforced in
    SQL via the WHERE clause so there is no race window between the
    ownership check and the update.
    """
    try:
        if not isinstance(token_id, str) or not token_id:
            return False
        if not isinstance(user_id, str) or not user_id:
            return False

        from state.db import get_connection
        conn = get_connection()
        cur = conn.execute(
            """
            UPDATE api_tokens
               SET revoked = 1
             WHERE token_id = ?
               AND user_id  = ?
               AND revoked  = 0
            """,
            (token_id, user_id),
        )
        revoked = (cur.rowcount or 0) > 0

        if revoked:
            try:
                from auth.audit import record_audit
                record_audit(
                    "revoke_api_token",
                    entity_type="api_token",
                    entity_id=token_id,
                    user_id=user_id,
                )
            except Exception:  # noqa: BLE001
                pass
        return revoked
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.tokens.revoke_token: failed for token_id={token_id!r}: {exc}"
        )
        return False


def record_token_use(token_id: str) -> None:
    """Stamp ``last_used_at`` on the row. NEVER raises.

    Called from ``verify_token`` on a successful verify so the user
    can tell at a glance which token is still active and which has
    gone idle. Also safe to call directly from any future API
    middleware that wants to record per-request use independently of
    the verify path.
    """
    try:
        if not isinstance(token_id, str) or not token_id:
            return
        from state.db import get_connection
        conn = get_connection()
        conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_id = ?",
            (_now_iso(), token_id),
        )
    except Exception as exc:  # noqa: BLE001
        # Best-effort: a stale ``last_used_at`` is not worth breaking
        # the verify path over.
        logger.debug(
            f"auth.tokens.record_token_use: update failed "
            f"for token_id={token_id!r}: {exc}"
        )


# ── Public API: read ──────────────────────────────────────────────────────

def verify_token(raw_token: str) -> Optional[str]:
    """Verify a raw API token and return the owning ``user_id``.

    Returns the matching ``user_id`` on success, ``None`` on:

      * Type / shape rejection (not a non-empty string, prefix
        too short to look up).
      * Prefix not present in the index.
      * Every row sharing the prefix is revoked OR fails the
        constant-time hash compare.

    On success, stamps ``last_used_at`` via ``record_token_use`` so
    the user can see at a glance which tokens are still in use. The
    function never raises — every failure path returns ``None``.

    Security:
      * The hash compare uses ``hmac.compare_digest`` via
        ``auth.gate._verify_password`` so a timing-side-channel can
        NOT distinguish "wrong password" from "right user, wrong
        password" rows.
      * Revoked rows are excluded BEFORE the hash compare so a
        revoked token cannot leak the row's existence through
        timing differences.
    """
    try:
        if not isinstance(raw_token, str) or not raw_token:
            return None
        if len(raw_token) < _TOKEN_PREFIX_LEN:
            # Too short to even index into the prefix column. Reject
            # without hitting the DB.
            return None

        prefix = raw_token[:_TOKEN_PREFIX_LEN]

        from state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT token_id, user_id, token_hash, token_salt, revoked
              FROM api_tokens
             WHERE token_prefix = ?
               AND revoked = 0
            """,
            (prefix,),
        ).fetchall()
        if not rows:
            return None

        for row in rows:
            try:
                stored_hash = bytes.fromhex(row["token_hash"])
                salt = bytes.fromhex(row["token_salt"])
            except (ValueError, TypeError):
                # Corrupt row — skip rather than break the lookup.
                # We do not log the token_id at WARNING because a
                # noisy log on a per-verify path is its own
                # information-leak surface.
                continue
            if _verify_password(raw_token, stored_hash, salt):
                # Match. Stamp last_used_at (best-effort) and return.
                record_token_use(row["token_id"])
                return row["user_id"]
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"auth.tokens.verify_token: failed: {exc}")
        return None


def list_tokens(user_id: str) -> list[ApiToken]:
    """Return ``user_id``'s tokens, newest first.

    Includes both active AND revoked tokens — a UI typically wants
    to display revoked rows so the user can see what was revoked
    when (the revoked flag is part of the dataclass). To filter to
    "active only", the caller can apply ``[t for t in list_tokens()
    if not t.revoked]``.

    Returns ``[]`` on a missing / empty user_id OR on any DB error.
    Never raises.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return []
        from state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT token_id, user_id, label, token_prefix,
                   created_at, last_used_at, revoked
              FROM api_tokens
             WHERE user_id = ?
             ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [_row_to_token(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.tokens.list_tokens: failed for user_id={user_id!r}: {exc}"
        )
        return []


__all__ = [
    "ApiToken",
    "create_token",
    "list_tokens",
    "record_token_use",
    "revoke_token",
    "verify_token",
]
