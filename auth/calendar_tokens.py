"""auth/calendar_tokens.py — per-user subscription tokens for the public
``/api/v1/incidents.ics`` calendar feed.

Why this is NOT auth.tokens
---------------------------
``auth.tokens`` mints opaque API tokens that get sent as
``Authorization: Bearer …`` headers and are stored *hashed* in the
database. They are the right primitive for scripted callers that can
attach an HTTP header to every request.

Calendar applications (Google Calendar, Apple Calendar, Outlook,
Thunderbird) CAN'T attach a bearer header — they just GET a URL on a
poll loop. The only way to authenticate is via something embedded in
the URL itself. The secret IS the URL, so:

* The token is stored **plain text** in ``UserSettings.extras``
  (not hashed). The UI displays the URL — including the token — so
  the user can copy/paste it into their calendar app. There is no
  shape in which the user proves possession of a secret they don't
  also display on-screen.
* Verification is a plain string compare against the stored value,
  done with :func:`hmac.compare_digest` for constant-time safety
  against timing attacks even though the threat model is mostly
  "the URL leaked into a chat log".
* Rotation = generating a new token (which replaces the old one in
  the user's settings row).
* Per-user scope = the verify path returns the OWNING ``user_id``;
  every endpoint that authenticates via this token threads the
  resolved user_id into its DB query so cross-user reads cannot
  succeed.

Why a separate module
---------------------
Keeping the calendar-token surface OUT of ``auth.tokens`` makes the
threat-model split explicit at the import line:

  * ``auth.tokens``  — high-value secrets, hashed at rest, bearer-
                      header auth, used by scripted callers.
  * ``auth.calendar_tokens`` — low-value subscription URLs, plain
                              at rest, query-string auth, used by
                              calendar apps.

A future auditor reading the codebase sees two distinct surfaces and
two distinct contracts; mixing them would invite the question "wait,
why is this token plain?" every time someone reads the file.

Storage
-------
* Lives in :class:`auth.settings.UserSettings.extras` under the key
  ``calendar_token``. This is the documented escape hatch for per-user
  config that doesn't warrant a dedicated column — no schema bump
  required.
* One token per user. Generating a new one REPLACES the old one in
  the ``extras`` dict (so revoking-then-regenerating is the same flow
  as plain rotation).
* The ``user_settings`` row may not exist for a user who has never
  saved preferences; :func:`auth.settings.save_settings` creates it
  on first save, so ``generate_calendar_token`` doesn't need to
  pre-seed anything.

What this module does NOT do
----------------------------
* No multi-token-per-user support. Calendar apps don't need to
  rotate without coordination — one URL, one secret, regenerate to
  rotate.
* No expiry. A subscription URL needs to last as long as the user
  uses it; the operator is expected to revoke through the UI when
  they're done.
* No audit-log entry for verification (would log on every poll —
  hundreds of times per user per day). Generation / rotation /
  revocation ARE audited via the underlying ``save_settings`` audit
  hook, which records "extras changed" (not the token value itself).
"""
from __future__ import annotations

import hmac
import secrets
from typing import Optional

from loguru import logger


# Length of the raw token in URL-safe base64 characters. 32 base64
# chars = 24 bytes of entropy = 192 bits, which is well past the
# 128-bit floor for "cryptographically random enough that brute
# force is infeasible". Calendar URLs are sometimes pasted into chat
# logs; we'd rather they be unguessable than short.
_TOKEN_BYTES: int = 24
_EXTRAS_KEY: str = "calendar_token"


def generate_calendar_token(*, user_id: str) -> Optional[str]:
    """Generate a new subscription token for ``user_id`` and persist it.

    The raw token is returned ONCE to the caller (UI display, CLI
    output). It is also stored plain in
    ``UserSettings.extras['calendar_token']`` — the calendar feed
    contract requires the verify path to compare against the same
    raw value, and the UI needs to display the URL on demand.

    Replaces any existing token on the user's row (rotation /
    regenerate is just another call to this function).

    Returns ``None`` on persistence failure or an invalid user_id
    so the caller can surface a clean error to the operator. NEVER
    raises.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            logger.warning(
                "calendar_tokens.generate: refusing to generate for "
                f"user_id={user_id!r} (empty / non-string)"
            )
            return None

        raw = secrets.token_urlsafe(_TOKEN_BYTES)

        from auth.settings import get_settings, save_settings

        settings = get_settings(user_id)
        # Mutate the extras dict — defensive copy so we don't share
        # state with the dataclass we just fetched. ``save_settings``
        # validates the dict shape on the way down.
        new_extras = dict(settings.extras or {})
        new_extras[_EXTRAS_KEY] = raw
        settings.extras = new_extras

        ok = save_settings(settings)
        if not ok:
            logger.warning(
                "calendar_tokens.generate: save_settings failed for "
                f"user_id={user_id!r}"
            )
            return None
        return raw
    except Exception as exc:  # noqa: BLE001
        # NEVER raise — the UI / CLI is expected to render None as
        # a "could not generate" message.
        logger.warning(
            f"calendar_tokens.generate: failed for user_id={user_id!r}: {exc}"
        )
        return None


def get_calendar_token(*, user_id: str) -> Optional[str]:
    """Return the saved calendar token for ``user_id`` or ``None``.

    Used by the UI to display the current subscription URL without
    minting a fresh token (which would silently revoke the user's
    existing calendar app subscription). Returns ``None`` if the
    user has no token yet or on any failure.

    NEVER raises.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return None
        from auth.settings import get_settings

        settings = get_settings(user_id)
        if not isinstance(settings.extras, dict):
            return None
        tok = settings.extras.get(_EXTRAS_KEY, "")
        if not isinstance(tok, str) or not tok:
            return None
        return tok
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"calendar_tokens.get: failed for user_id={user_id!r}: {exc}"
        )
        return None


def verify_calendar_token(token: str) -> Optional[str]:
    """Return the owning ``user_id`` if ``token`` matches a stored
    subscription token, ``None`` otherwise.

    This is the auth gate for ``/api/v1/incidents.ics?token=…``. The
    comparison uses :func:`hmac.compare_digest` for constant-time
    behaviour — even though the realistic threat is "the URL leaked
    into a Slack message" not "an attacker has nanosecond timing
    measurements over my LAN", the cost of constant-time is zero so
    we always pay it.

    Implementation
    --------------
    The token is stored in ``UserSettings.extras['calendar_token']``,
    which is a JSON blob on the user_settings table. There is no
    index on extras (the column is opaque JSON to SQLite), so we
    scan every row. For the realistic 1-100 user count of a typical
    Ship Tracker deploy this is a sub-millisecond operation; if the
    user count grew past a thousand we'd promote the token to its
    own column with an index. The scan is bounded; an unbounded
    table would still need a per-iteration timeout but we already
    bound at the DB layer.

    Returns ``None`` on:
      * empty / non-string token,
      * any DB error,
      * no matching row.

    NEVER raises.
    """
    try:
        if not isinstance(token, str) or not token:
            return None

        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT user_id, settings_json FROM user_settings",
        ).fetchall()
        # Scan ALL rows and DO NOT early-return on a match — returning as soon
        # as the token is found would leak (via timing) whether/where it
        # matched. Accumulate the first hit and keep going, like verify_totp /
        # the recovery-code path.
        matched_uid: Optional[str] = None
        for row in rows or []:
            # ``rows`` from a Row-factory connection support both
            # dict-style and index access; handle either.
            try:
                stored_uid = row["user_id"]
                blob = row["settings_json"] or ""
            except (KeyError, TypeError):
                try:
                    stored_uid = row[0]
                    blob = row[1] or ""
                except Exception:
                    continue
            if not stored_uid or not blob:
                continue
            try:
                import json
                parsed = json.loads(blob) if isinstance(blob, str) else {}
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            extras = parsed.get("extras") or {}
            if not isinstance(extras, dict):
                continue
            stored = extras.get(_EXTRAS_KEY, "")
            if not isinstance(stored, str) or not stored:
                continue
            # Constant-time compare. ``compare_digest`` accepts
            # either bytes or strings of equal type. Record the first hit
            # but keep iterating so timing doesn't leak the match position.
            if hmac.compare_digest(stored, token):
                if matched_uid is None:
                    matched_uid = str(stored_uid)
        return matched_uid
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"calendar_tokens.verify: failed: {exc}")
        return None


def revoke_calendar_token(*, user_id: str) -> bool:
    """Delete the saved subscription token for ``user_id``.

    Returns ``True`` if a token existed and was cleared, ``False`` if
    no token was set or persistence failed. NEVER raises.

    Calling this immediately invalidates the user's existing
    subscription URL — calendar apps will fail to fetch and either
    show a "could not refresh" warning or stop syncing entirely.
    The operator is expected to communicate that out-of-band.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return False

        from auth.settings import get_settings, save_settings

        settings = get_settings(user_id)
        if not isinstance(settings.extras, dict):
            return False
        if _EXTRAS_KEY not in settings.extras:
            return False
        new_extras = dict(settings.extras)
        new_extras.pop(_EXTRAS_KEY, None)
        settings.extras = new_extras
        ok = save_settings(settings)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"calendar_tokens.revoke: failed for user_id={user_id!r}: {exc}"
        )
        return False


__all__ = [
    "generate_calendar_token",
    "get_calendar_token",
    "verify_calendar_token",
    "revoke_calendar_token",
]
