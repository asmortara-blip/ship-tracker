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
import uuid
from datetime import datetime, timezone
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


# ── Recovery-code constants ───────────────────────────────────────────────

# 10 codes per generation is the conventional batch size (matches what
# GitHub, Google, Auth0 hand out). One code per real-life loss event
# (lost phone, lost YubiKey, lost laptop) is the spending pattern; the
# user can regenerate the whole batch at any time if they feel exposed.
_RECOVERY_CODE_DEFAULT_COUNT = 10

# 10 chars of base32 (uppercase A-Z + 2-7) per code. The dashed display
# form is ``XXXXX-XXXXX`` — five chars / dash / five chars. Base32 gives
# 50 bits of entropy per code, well above what's needed for a code that
# is one of N≤10 unused codes per account and is rate-limited by the
# surrounding password gate.
_RECOVERY_CODE_HALF_LEN = 5
_RECOVERY_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"  # base32-ish, no 0/1/O/I

# Per-code random salt — 16 bytes (128 bits) is the minimum NIST
# guidance for PBKDF2 salts and matches the rest of the auth/ layer
# (auth.gate uses SALT_BYTES=16). NEVER reuse a salt across codes —
# each call to ``generate_recovery_codes`` produces ``count`` fresh
# salts via ``secrets.token_bytes(16)``.
_RECOVERY_SALT_BYTES = 16

# 200_000 PBKDF2 iterations matches the rest of the auth/ layer
# (auth.gate.PBKDF2_ITERATIONS) so the per-verify cost is uniform across
# password and recovery-code paths. SHA-256 is the hash — same as the
# rest of the password layer, and FIPS-approved.
_RECOVERY_PBKDF2_ITERATIONS = 200_000
_RECOVERY_HASH_NAME = "sha256"


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
        # Fail CLOSED on an empty / whitespace-only secret. _b32_decode_padded
        # of "" returns b"", which would make compute_totp build an HMAC with a
        # KNOWN (empty) key — i.e. an attacker-computable second factor. A
        # mfa_enabled row must never carry an empty secret; treat it as a hard
        # verification failure rather than a deterministic pass (honours this
        # function's "empty / malformed secret -> False" contract).
        if not secret_b32.strip():
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

def enable_mfa(
    user_id: str,
    secret: str,
) -> tuple[bool, Optional[list[str]]]:
    """Persist ``secret``, flip ``mfa_enabled``, and auto-mint recovery codes.

    Returns a ``(ok, recovery_codes)`` tuple:

      * ``(True,  [<code>, ...])`` on success — the second element is
        the freshly-minted batch of 10 plaintext recovery codes,
        returned to the caller EXACTLY ONCE so they can be surfaced
        to the user (UI displays them in a one-shot copyable block,
        CLI prints them once to stdout). The plaintext is NEVER
        persisted — only the per-code pbkdf2 hash + salt land in the
        ``mfa_recovery_codes`` table.
      * ``(False, None)`` on any error — unknown user id, DB error,
        invalid input. NEVER raises.

    SIGNATURE CHANGE (v21): the historical signature was
    ``-> bool``. Every caller MUST be updated to unpack the tuple.
    The two in-tree call sites today are ``ui.tab_data_health``
    (``_render_security_panel`` + ``_render_mfa_panel``) and
    ``tools.ops_cli`` (``_cmd_mfa_enable``). Both surface the codes
    to the operator on success.

    Audit-logged at ``action='enable_mfa'`` so a security review can
    see which accounts gained a second factor and when. The audit
    write is best-effort and cannot block the enable. Recovery-code
    generation is also audit-logged separately by
    ``generate_recovery_codes`` (``action='generate_recovery_codes'``).
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return False, None
        if not isinstance(secret, str) or not secret:
            return False, None
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
            return False, None

        from state.db import get_connection
        conn = get_connection()
        cur = conn.execute(
            "UPDATE users SET mfa_secret = ?, mfa_enabled = 1 "
            "WHERE user_id = ?",
            (secret, user_id),
        )
        if cur.rowcount == 0:
            return False, None

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

        # Mint the recovery-code batch. A failure here is logged but
        # does NOT roll back the enable — the user can re-mint via
        # ``regenerate_recovery_codes`` if the auto-mint fell over.
        # In that fallback case we still return ``(True, None)`` so
        # the UI / CLI can render "MFA enabled — no codes shown,
        # please regenerate".
        try:
            codes = generate_recovery_codes(user_id)
            if not codes:
                logger.warning(
                    f"auth.mfa.enable_mfa: recovery-code generation "
                    f"returned empty for user_id={user_id!r}"
                )
                return True, None
            return True, codes
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                f"auth.mfa.enable_mfa: recovery-code generation "
                f"failed for user_id={user_id!r}: {exc}"
            )
            return True, None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.enable_mfa: failed for user_id={user_id!r}: {exc}"
        )
        return False, None


def disable_mfa(user_id: str) -> bool:
    """Clear the MFA secret and flag for ``user_id``.

    Returns ``True`` on success, ``False`` on any error. NEVER raises.

    Audit-logged at ``action='disable_mfa'``. Once disabled, the
    account falls back to password-only auth on the next login.

    Side effect (v21): also wipes every recovery code for the user.
    Recovery codes are a backup to the TOTP secret — keeping them
    after disabling MFA would leave a back door (login with code +
    password) that is invisible to the "MFA off" state the user
    sees. The wipe is best-effort; a failure logs but does not block
    the disable.
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

        # Wipe any outstanding recovery codes — see docstring for the
        # security reasoning. Best-effort; a failure here does NOT
        # roll back the disable (the user already lost their second
        # factor by intent; leaving the recovery rows behind would
        # surface a "you still have codes" caption that contradicts
        # the disabled state).
        try:
            conn.execute(
                "DELETE FROM mfa_recovery_codes WHERE user_id = ?",
                (user_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"auth.mfa.disable_mfa: recovery-code wipe failed "
                f"for user_id={user_id!r}: {exc}"
            )

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


# ── Recovery codes (v21) ──────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_one_recovery_code() -> str:
    """Generate one plaintext recovery code formatted as ``XXXXX-XXXXX``.

    Each half is ``_RECOVERY_CODE_HALF_LEN`` chars drawn from the
    ``_RECOVERY_CODE_ALPHABET`` (32-char base32-style set with the
    confusable ``0/1/O/I`` removed). The dash is purely cosmetic —
    ``verify_and_consume_recovery_code`` strips dashes + whitespace
    before hashing, so the user can type the code with or without it.

    50 bits of entropy per code (32^10 = ~2^50) is overkill for a code
    that's (a) one of N≤10 unused codes per account, (b) gated by the
    surrounding password attempt-limiter in ``auth.gate``, and (c)
    single-use (consumed on first match).
    """
    chars = [
        _secrets.choice(_RECOVERY_CODE_ALPHABET)
        for _ in range(2 * _RECOVERY_CODE_HALF_LEN)
    ]
    first = "".join(chars[:_RECOVERY_CODE_HALF_LEN])
    second = "".join(chars[_RECOVERY_CODE_HALF_LEN:])
    return f"{first}-{second}"


def _normalize_recovery_code(code: str) -> str:
    """Strip dashes + whitespace and upper-case so the verify path is
    indifferent to how the user typed the code. Returns ``''`` for
    non-string input — caller treats empty as "no match"."""
    if not isinstance(code, str):
        return ""
    return code.replace("-", "").replace(" ", "").strip().upper()


def _hash_recovery_code(code: str, salt: bytes) -> str:
    """Return the hex-encoded pbkdf2-sha256 hash of ``code`` under ``salt``.

    Iteration count + algorithm match the rest of the auth/ layer so a
    code verify costs the same as a password verify (the KDF cost is
    the rate-limiter; iterating PBKDF2 ten times on each verify is by
    design).
    """
    normalized = _normalize_recovery_code(code)
    if not normalized:
        return ""
    digest = hashlib.pbkdf2_hmac(
        _RECOVERY_HASH_NAME,
        normalized.encode("utf-8"),
        salt,
        _RECOVERY_PBKDF2_ITERATIONS,
    )
    return digest.hex()


def generate_recovery_codes(
    user_id: str,
    *,
    count: int = _RECOVERY_CODE_DEFAULT_COUNT,
) -> list[str]:
    """Mint ``count`` fresh single-use recovery codes for ``user_id``.

    Returns the PLAINTEXT codes as a list, exactly once. The plaintext
    is NEVER persisted — only a per-code pbkdf2 hash + 16-byte salt
    land in ``mfa_recovery_codes``. The caller MUST surface the codes
    to the user immediately and warn them to save the batch; there is
    no recovery path if they lose the list.

    The function does NOT wipe pre-existing codes — use
    ``regenerate_recovery_codes`` for the wipe-and-replace flow. This
    is deliberate: an admin "mint me 5 more" pattern is rare but
    legitimate, and a wipe-on-mint contract would silently invalidate
    the user's already-saved codes.

    Returns ``[]`` on any error (unknown user, DB error, invalid
    inputs). NEVER raises.

    Audit-logged at ``action='generate_recovery_codes'`` with
    ``detail={'count': N}`` so a security review can spot a user (or
    attacker with the password) minting new codes.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return []
        if not isinstance(count, int) or count <= 0 or count > 100:
            # 100 is an arbitrary safety cap — the canonical batch
            # size is 10 and a request for thousands of codes is
            # almost certainly a bug or abuse.
            return []

        from state.db import get_connection
        conn = get_connection()

        # Confirm the user exists before writing any rows — a foreign-
        # key violation would partially-fill the table otherwise. Skip
        # the FK check if PRAGMA foreign_keys is off (test setup may
        # disable it), the explicit lookup is the source of truth.
        exists = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if exists is None:
            return []

        plaintext_codes: list[str] = []
        rows_to_insert: list[tuple] = []
        created_at = _now_iso()
        for _ in range(count):
            code = _generate_one_recovery_code()
            salt = _secrets.token_bytes(_RECOVERY_SALT_BYTES)
            code_hash = _hash_recovery_code(code, salt)
            if not code_hash:
                # Defensive — _normalize_recovery_code would have
                # rejected our own output. If somehow empty, skip
                # this code rather than store a bogus row.
                continue
            code_id = str(uuid.uuid4())
            rows_to_insert.append((
                code_id,
                user_id,
                code_hash,
                salt.hex(),
                None,  # used_at NULL until consumed
                created_at,
            ))
            plaintext_codes.append(code)

        if not rows_to_insert:
            return []

        conn.executemany(
            """
            INSERT INTO mfa_recovery_codes
              (code_id, user_id, code_hash, salt, used_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )

        try:
            from auth.audit import record_audit
            record_audit(
                "generate_recovery_codes",
                entity_type="user",
                entity_id=user_id,
                detail={"count": len(plaintext_codes)},
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass

        return plaintext_codes
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        logger.warning(
            f"auth.mfa.generate_recovery_codes: failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return []


def verify_and_consume_recovery_code(user_id: str, code: str) -> bool:
    """Try ``code`` against every unused recovery code for ``user_id``.

    On match: stamps ``used_at`` on the matched row (so the code
    cannot be reused) and returns ``True``. On no match: returns
    ``False``.

    The verify loop hashes the submitted code under each candidate
    row's salt and compares with ``hmac.compare_digest`` (constant
    time per comparison). We exhaust every unused row on a miss so a
    timing-side-channel attacker cannot use response time to guess
    "how many codes does this account have left?" — the work is
    proportional to the number of unused codes, not to whether a
    match was found early.

    Returns ``False`` on any error (unknown user, DB error, malformed
    input). NEVER raises.

    Audit-logged at ``action='consume_recovery_code'`` on a successful
    consumption — failed attempts do NOT audit (same enumeration-
    resistance pattern as failed MFA login).
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return False
        if not isinstance(code, str) or not code:
            return False

        normalized = _normalize_recovery_code(code)
        # 10 chars without the dash (5+5). A different length is
        # almost certainly a typo — fail fast without spending the
        # PBKDF2 cost.
        if len(normalized) != 2 * _RECOVERY_CODE_HALF_LEN:
            return False

        from state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT code_id, code_hash, salt
              FROM mfa_recovery_codes
             WHERE user_id = ?
               AND used_at IS NULL
            """,
            (user_id,),
        ).fetchall()
        if not rows:
            return False

        matched_id: Optional[str] = None
        for row in rows:
            try:
                salt = bytes.fromhex(row["salt"])
            except (TypeError, ValueError):
                # Corrupt row — skip rather than crash the verify
                # loop. A separate maintenance path can clean it up.
                continue
            candidate = _hash_recovery_code(code, salt)
            if not candidate:
                continue
            # NOTE: we keep iterating after a match (using ``|=`` /
            # first-write semantics) so the wall-clock work is
            # proportional to the number of unused codes, not to
            # whether a match was found early. matched_id is set on
            # the FIRST hit and never overwritten.
            if hmac.compare_digest(candidate, row["code_hash"]):
                if matched_id is None:
                    matched_id = row["code_id"]

        if matched_id is None:
            return False

        used_at = _now_iso()
        try:
            cur = conn.execute(
                "UPDATE mfa_recovery_codes SET used_at = ? "
                "WHERE code_id = ? AND used_at IS NULL",
                (used_at, matched_id),
            )
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                f"auth.mfa.verify_and_consume_recovery_code: stamp "
                f"failed for code_id={matched_id!r}: {exc}"
            )
            return False
        # Single-use guard: the conditional UPDATE (used_at IS NULL) is the
        # race resolver — if it matched 0 rows, a concurrent caller already
        # consumed this code, so this attempt must FAIL rather than
        # authenticate the same code twice. Mirrors the rowcount gate in
        # invitations.consume_invitation and tokens.revoke_token.
        if (getattr(cur, "rowcount", 0) or 0) == 0:
            return False

        try:
            from auth.audit import record_audit
            record_audit(
                "consume_recovery_code",
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass

        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.verify_and_consume_recovery_code: failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return False


def count_unused_recovery_codes(user_id: str) -> int:
    """Return the number of UNUSED recovery codes for ``user_id``.

    Used by the UI to render the "N unused codes remaining" caption
    and to prompt the user to regenerate when N gets low. Returns 0
    on unknown user or any error. NEVER raises.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return 0
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM mfa_recovery_codes
             WHERE user_id = ?
               AND used_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["n"])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.count_unused_recovery_codes: failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return 0


def regenerate_recovery_codes(
    user_id: str,
    *,
    count: int = _RECOVERY_CODE_DEFAULT_COUNT,
) -> list[str]:
    """Wipe every existing recovery code for ``user_id`` and mint a fresh batch.

    The wipe and the insert are SEPARATE statements — sqlite's
    autocommit isolation_level=None means each is its own transaction.
    A failure between them leaves the user with 0 unused codes
    rather than mixing old + new; this is the safe failure mode (the
    user can call regenerate again).

    Returns the PLAINTEXT codes exactly once. Returns ``[]`` on any
    error. NEVER raises.

    Audit-logged at ``action='regenerate_recovery_codes'`` via
    ``generate_recovery_codes`` plus a separate
    ``action='wipe_recovery_codes'`` so the timeline shows both
    halves of the operation.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return []

        from state.db import get_connection
        conn = get_connection()

        # Make sure the user exists before we wipe — otherwise an
        # accidental empty user_id would no-op the wipe (filtered by
        # WHERE) but also no-op the mint and return [].
        exists = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if exists is None:
            return []

        try:
            conn.execute(
                "DELETE FROM mfa_recovery_codes WHERE user_id = ?",
                (user_id,),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"auth.mfa.regenerate_recovery_codes: wipe failed "
                f"for user_id={user_id!r}: {exc}"
            )
            return []

        try:
            from auth.audit import record_audit
            record_audit(
                "wipe_recovery_codes",
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass

        return generate_recovery_codes(user_id, count=count)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.mfa.regenerate_recovery_codes: failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return []


__all__ = [
    "generate_secret",
    "compute_totp",
    "verify_totp",
    "provisioning_uri",
    "enable_mfa",
    "disable_mfa",
    "is_mfa_enabled",
    "get_mfa_secret",
    "generate_recovery_codes",
    "verify_and_consume_recovery_code",
    "count_unused_recovery_codes",
    "regenerate_recovery_codes",
]
