"""auth.mfa — optional TOTP (Time-based One-Time Password) second factor.

Design
------
This module adds a second factor to the existing password login. Once a
user enables MFA, ``auth.users.login`` requires both the password AND a
6-digit code from the user's TOTP app (Google Authenticator, 1Password,
Authy, …). Pre-existing users keep logging in with just the password —
MFA is strictly opt-in per account.

Why stdlib-only?
----------------
The TOTP algorithm (RFC 6238 — TOTP = HOTP over Unix-time/period) is
HMAC-SHA1 of an 8-byte counter, dynamically truncated to N digits. Every
piece of that is in the stdlib: ``hmac``, ``hashlib``, ``struct``,
``time``, ``base64``. Adding ``pyotp`` would buy us nothing this module
does not already do, and would put a 3p-dep on the critical-path
authentication surface.

What this module does NOT do
----------------------------
* No scratch / recovery codes (out of scope for v1; would be a follow-up
  table keyed by user_id with single-use hashed codes).
* No SMS / email codes — TOTP only.
* No QR-code image rendering. ``provisioning_uri`` returns the canonical
  ``otpauth://totp/...`` URI; the UI layer can render a QR code from
  that URI with whatever library the project already has.
* No secret encryption at rest — the secret is stored in plaintext in
  the ``users.mfa_secret`` column. Encrypting it with ``state.vault`` is
  a sensible follow-up but not blocking; the threat model treats DB
  read-access as already compromising.
* No rate-limiting of MFA attempts — the password attempt-counter
  lockout in ``auth.gate`` covers the surrounding login surface; an
  attacker who has the password still hits the password lockout on
  retries.

Storage shape
-------------
Two new columns on the ``users`` table (schema v15):

  * ``mfa_secret``  TEXT NOT NULL DEFAULT ''   — canonical 32-char
    base32 secret. Empty when MFA is not enabled.
  * ``mfa_enabled`` INTEGER NOT NULL DEFAULT 0 — 0/1 flag. The secret
    can be non-empty with the flag still 0 during enrollment ("we
    generated a secret, the user hasn't confirmed it yet") but the
    public ``enable_mfa`` helper always sets both at once.

Every public helper NEVER raises — failures return ``False`` (or
``None`` for ``verify_totp``-style helpers) and log at WARNING.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets
import sqlite3
import struct
import time
import urllib.parse
from typing import Optional

from loguru import logger


# ── Constants ─────────────────────────────────────────────────────────────

# 20 bytes (160 bits) → 32 base32 chars after stripping padding. This is
# the canonical secret length used by every TOTP authenticator app and
# matches the RFC 6238 reference (Appendix B SHA-1 vector uses a 20-byte
# secret); a longer secret buys no security against the brute-force
# threat model and breaks some authenticator-app QR scanners that cap
# the field at 32 chars.
_SECRET_BYTES = 20

# Default digits / period — every common authenticator app defaults to
# 6 / 30, and exposing these as kwargs (rather than module constants)
# means tests can sweep them without monkeypatching.
_DEFAULT_DIGITS = 6
_DEFAULT_PERIOD = 30

# Default ±N periods of clock-drift slack accepted by verify_totp.
# window=1 (±30 s) matches the Google Authenticator default and is the
# conservative-but-usable choice. Tests covering both window=0 (strict)
# and window=1 (default) are in tests/test_mfa.py.
_DEFAULT_WINDOW = 1


# ── Pure-function TOTP core ───────────────────────────────────────────────

def _b32_decode_padded(secret_b32: str) -> bytes:
    """Decode a base32 string that may be missing its ``=`` padding.

    Authenticator-app QR codes routinely omit the padding (it's noise on
    a QR), so we re-pad before calling ``base64.b32decode`` (which is
    strict about padding by default). Also tolerates whitespace and
    lower-case input — both are silently normalized.
    """
    if not isinstance(secret_b32, str):
        raise TypeError("secret_b32 must be a str")
    cleaned = "".join(secret_b32.split()).upper()
    pad = (-len(cleaned)) % 8
    return base64.b32decode(cleaned + "=" * pad)


def generate_secret() -> str:
    """Return a fresh 32-char base32 TOTP secret.

    The canonical secret length used by every TOTP authenticator app.
    Uses ``secrets.token_bytes`` (cryptographic RNG, not ``random``) for
    the 20 raw bytes. Padding (``=``) is stripped because authenticator-
    app QR codes also strip it; ``_b32_decode_padded`` re-adds padding
    transparently on the verify side.
    """
    raw = _secrets.token_bytes(_SECRET_BYTES)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def compute_totp(
    secret_b32: str,
    *,
    when: Optional[float] = None,
    digits: int = _DEFAULT_DIGITS,
    period: int = _DEFAULT_PERIOD,
) -> str:
    """Compute the RFC 6238 TOTP code for ``secret_b32`` at ``when``.

    Args:
        secret_b32: Base32-encoded secret (with or without padding,
                    upper- or lower-case). Re-padded internally.
        when:       Unix timestamp (float). ``None`` defaults to
                    ``time.time()`` — i.e. "now".
        digits:     Code length. 6 by default; valid range 6..10. Codes
                    are zero-padded to this width.
        period:     TOTP step in seconds. 30 by default per RFC 6238.

    Returns:
        Zero-padded N-digit code as a ``str``.

    Algorithm (RFC 6238):
        counter = floor(when / period)
        hmac    = HMAC-SHA1(b32decode(secret), counter_as_8_bytes_BE)
        offset  = hmac[-1] & 0x0f
        binary  = (hmac[offset]   & 0x7f) << 24 |
                  (hmac[offset+1] & 0xff) << 16 |
                  (hmac[offset+2] & 0xff) <<  8 |
                  (hmac[offset+3] & 0xff)
        code    = (binary mod 10^digits), zero-padded to `digits`
    """
    if when is None:
        when = time.time()
    if not isinstance(digits, int) or digits < 6 or digits > 10:
        raise ValueError("digits must be int in [6, 10]")
    if not isinstance(period, int) or period <= 0:
        raise ValueError("period must be a positive int")

    key = _b32_decode_padded(secret_b32)

    counter = int(when // period)
    counter_bytes = struct.pack(">Q", counter)
    mac = hmac.new(key, counter_bytes, hashlib.sha1).digest()

    # Dynamic truncation (RFC 4226 §5.3): low nibble of the last byte
    # picks the 4-byte window; mask the top bit to avoid sign issues.
    offset = mac[-1] & 0x0f
    binary = (
        ((mac[offset] & 0x7f) << 24)
        | ((mac[offset + 1] & 0xff) << 16)
        | ((mac[offset + 2] & 0xff) << 8)
        | (mac[offset + 3] & 0xff)
    )
    code_int = binary % (10 ** digits)
    return str(code_int).zfill(digits)


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    when: Optional[float] = None,
    window: int = _DEFAULT_WINDOW,
    digits: int = _DEFAULT_DIGITS,
    period: int = _DEFAULT_PERIOD,
) -> bool:
    """Constant-time verification of ``code`` against ``secret_b32``.

    Args:
        secret_b32: Same as ``compute_totp``. Empty / malformed secret
                    → ``False`` (never raises).
        code:       The user-submitted code. Whitespace is trimmed;
                    non-digit input → ``False``.
        when:       Unix timestamp; ``None`` = now.
        window:     Number of ``period``-sized steps of clock drift to
                    accept either side of ``when`` (default 1 = ±30 s
                    with a 30-second period). A window of 0 is strict.
        digits, period: Forwarded to ``compute_totp``.

    Uses ``hmac.compare_digest`` for the comparison so a bad-by-one-
    char code is timing-indistinguishable from a totally wrong code.
    Tests the candidate codes one-at-a-time but ALWAYS exhausts the
    full window — early-out on a match would leak the matched offset
    via timing.

    NEVER raises. Returns ``False`` on any unexpected input.
    """
    try:
        if not isinstance(code, str) or not isinstance(secret_b32, str):
            return False
        code_norm = "".join(code.split())
        if not code_norm or not code_norm.isdigit():
            return False
        if len(code_norm) != digits:
            return False
        if not isinstance(window, int) or window < 0:
            return False

        if when is None:
            when = time.time()

        matched = False
        for delta in range(-window, window + 1):
            try:
                candidate = compute_totp(
                    secret_b32,
                    when=when + delta * period,
                    digits=digits,
                    period=period,
                )
            except Exception:
                # A malformed secret will raise on the first iteration —
                # propagate as False rather than letting the loop continue.
                return False
            # NOTE: we keep iterating after a match so timing does not
            # leak which offset matched. ``|=`` on a bool is fine here.
            if hmac.compare_digest(candidate, code_norm):
                matched = True
        return matched
    except Exception as exc:  # noqa: BLE001 — generic by contract
        logger.warning(f"auth.mfa.verify_totp: failed: {exc}")
        return False


def provisioning_uri(
    secret_b32: str,
    *,
    account: str,
    issuer: str = "Ship Tracker",
    digits: int = _DEFAULT_DIGITS,
    period: int = _DEFAULT_PERIOD,
) -> str:
    """Build the ``otpauth://totp/...`` URI suitable for a QR code.

    Returns the canonical KeyURI form (Google Authenticator format):

        otpauth://totp/<issuer>:<account>?secret=<S>&issuer=<I>&...

    Both ``issuer`` and ``account`` are URL-encoded so spaces, slashes,
    and other auth-app-unfriendly characters in (e.g.) the issuer name
    do not break the URI. The label uses the ``Issuer:Account`` form
    so the authenticator app shows both pieces.
    """
    issuer_enc = urllib.parse.quote(issuer, safe="")
    account_enc = urllib.parse.quote(account, safe="")
    label = f"{issuer_enc}:{account_enc}"
    params = {
        "secret": secret_b32,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": str(digits),
        "period": str(period),
    }
    # urlencode does the per-value escaping; we built ``label`` already
    # so it lands in the path portion of the URI unescaped-by-urlencode.
    return f"otpauth://totp/{label}?{urllib.parse.urlencode(params)}"


# ── DB-bound helpers ──────────────────────────────────────────────────────

def enable_mfa(user_id: str, secret: str) -> bool:
    """Persist ``secret`` and flip the ``mfa_enabled`` flag for ``user_id``.

    Returns ``True`` on success, ``False`` on any error (unknown user
    id, DB error, invalid input). NEVER raises.

    Audit-logged at ``action='enable_mfa'`` so a security review can
    see which accounts gained a second factor and when. The audit
    write is best-effort and cannot block the enable.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return False
        if not isinstance(secret, str) or not secret:
            return False
        # Defensive: a malformed secret would let MFA "enable" but then
        # every subsequent verify would fail silently. Decode-check up
        # front so the operator notices at enroll-time.
        try:
            _b32_decode_padded(secret)
        except Exception:
            logger.warning(
                f"auth.mfa.enable_mfa: malformed secret for "
                f"user_id={user_id!r}"
            )
            return False

        from state.db import get_connection
        conn = get_connection()
        cur = conn.execute(
            "UPDATE users SET mfa_secret = ?, mfa_enabled = 1 "
            "WHERE user_id = ?",
            (secret, user_id),
        )
        if cur.rowcount == 0:
            return False

        try:
            from auth.audit import record_audit
            record_audit(
                "enable_mfa",
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.enable_mfa: failed for user_id={user_id!r}: {exc}"
        )
        return False


def disable_mfa(user_id: str) -> bool:
    """Clear the MFA secret and flag for ``user_id``.

    Returns ``True`` on success, ``False`` on any error. NEVER raises.

    Audit-logged at ``action='disable_mfa'``. Once disabled, the
    account falls back to password-only auth on the next login.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return False

        from state.db import get_connection
        conn = get_connection()
        cur = conn.execute(
            "UPDATE users SET mfa_secret = '', mfa_enabled = 0 "
            "WHERE user_id = ?",
            (user_id,),
        )
        if cur.rowcount == 0:
            return False

        try:
            from auth.audit import record_audit
            record_audit(
                "disable_mfa",
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.disable_mfa: failed for user_id={user_id!r}: {exc}"
        )
        return False


def is_mfa_enabled(user_id: str) -> bool:
    """Quick read: True iff ``user_id`` has the MFA flag set.

    Returns ``False`` on unknown user or any error. NEVER raises.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return False
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT mfa_enabled FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return False
        return bool(int(row["mfa_enabled"] or 0))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.is_mfa_enabled: failed for user_id={user_id!r}: {exc}"
        )
        return False


def get_mfa_secret(user_id: str) -> str:
    """Return the persisted MFA secret for ``user_id`` or empty string.

    Internal helper used by ``auth.users.login`` to verify the
    submitted code. NEVER raises — returns ``''`` on any failure.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return ""
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT mfa_secret FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return ""
        return str(row["mfa_secret"] or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.get_mfa_secret: failed for user_id={user_id!r}: {exc}"
        )
        return ""


__all__ = [
    "generate_secret",
    "compute_totp",
    "verify_totp",
    "provisioning_uri",
    "enable_mfa",
    "disable_mfa",
    "is_mfa_enabled",
    "get_mfa_secret",
]
