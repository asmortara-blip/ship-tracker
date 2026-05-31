"""auth.gate — single-password authentication gate.

Design
------
- Engine layer (no Streamlit dependency): ``_hash_password``,
  ``_verify_password``, ``_get_password_config``,
  ``generate_password_hash``. These are testable in isolation.
- UI layer (depends on Streamlit, imported lazily): ``require_auth``,
  ``logout``. These render the login form and manage session state.

Password storage
----------------
The password is NEVER stored in the SQLite DB. Only the scrypt hash
and salt are read from env vars (or ``st.secrets`` as a fallback,
matching the existing codebase pattern in
``engine/narration_engine.py::_get_anthropic_key``):

    APP_PASSWORD_HASH  — hex-encoded scrypt output (32 bytes → 64 hex chars)
    APP_PASSWORD_SALT  — hex-encoded 16-byte salt (32 hex chars)

When NEITHER is configured, the app runs in OPEN MODE (no auth
required) and a warning is logged at startup. This is intentional
for local development.

Lockout policy
--------------
After ``MAX_ATTEMPTS`` (5) consecutive failed login attempts, the
session is locked for ``LOCKOUT_DURATION_SECONDS`` (300s / 5min).
The lockout is session-scoped — clearing the browser session resets
it. That's acceptable for a single-password gate; if you need
durable rate-limiting you have outgrown this module.

Why scrypt (not bcrypt/argon2)?
-------------------------------
``hashlib.scrypt`` is in the Python stdlib — no new dependency.
Parameters (n=2**14, r=8, p=1) match the RFC 7914 reference
"interactive login" profile, which is appropriate here.

NOTE: ``hashlib.scrypt`` requires the platform's OpenSSL to have
been compiled with scrypt support. macOS system Python is shipped
against LibreSSL which historically omits it. When ``hashlib.scrypt``
is unavailable we transparently fall back to PBKDF2-HMAC-SHA256 at
600,000 iterations (OWASP-recommended floor for 2023+). Both
remain stdlib-only — still no third-party dependency.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from loguru import logger

# ── Tuning constants ──────────────────────────────────────────────────────
SCRYPT_N = 2 ** 14          # CPU/memory cost factor
SCRYPT_R = 8                # block size
SCRYPT_P = 1                # parallelization
SCRYPT_DKLEN = 32           # derived key length (bytes)
SALT_BYTES = 16             # salt length (bytes)

# Fallback KDF parameters (used only when hashlib.scrypt is unavailable
# on this platform — e.g. macOS system Python linked against LibreSSL).
PBKDF2_ITERATIONS = 600_000  # OWASP 2023+ minimum for SHA-256
PBKDF2_HASH = "sha256"

# True iff scrypt is available on this Python build. Resolved once at
# import time so tests can monkeypatch it to exercise both branches.
_SCRYPT_AVAILABLE = hasattr(hashlib, "scrypt")

MAX_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 300  # 5 minutes

ENV_HASH_KEY = "APP_PASSWORD_HASH"
ENV_SALT_KEY = "APP_PASSWORD_SALT"

# Session-state key used to hold the AuthState dataclass.
_SESSION_STATE_KEY = "_auth_state"

# Track whether we've already logged the open-mode warning so we don't
# spam the log on every Streamlit rerun.
_OPEN_MODE_WARNED = False


# ── Data ──────────────────────────────────────────────────────────────────

@dataclass
class AuthState:
    """Per-session auth state. Stored in ``st.session_state``."""
    authenticated: bool = False
    attempt_count: int = 0
    # ISO-8601 timestamp string for when the lockout expires, or ""
    # if not currently locked. We use a string (not datetime) so it
    # serializes cleanly through Streamlit's session_state.
    locked_until: str = ""


# ── Engine: pure functions, no Streamlit imports ──────────────────────────

def _hash_password(plaintext: str, salt: bytes) -> bytes:
    """Hash *plaintext* with the given *salt*.

    Uses ``hashlib.scrypt`` when available; otherwise transparently
    falls back to PBKDF2-HMAC-SHA256 at ``PBKDF2_ITERATIONS`` rounds.
    Deterministic for the same (plaintext, salt) pair on a given
    platform — the bytes you store at setup are the bytes that must
    verify at login.
    """
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be a str")
    if not isinstance(salt, (bytes, bytearray)):
        raise TypeError("salt must be bytes")
    salt_bytes = bytes(salt)
    pw_bytes = plaintext.encode("utf-8")
    if _SCRYPT_AVAILABLE:
        return hashlib.scrypt(
            pw_bytes,
            salt=salt_bytes,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=SCRYPT_DKLEN,
        )
    # Fallback — pure stdlib, no third-party dependency
    return hashlib.pbkdf2_hmac(
        PBKDF2_HASH,
        pw_bytes,
        salt_bytes,
        PBKDF2_ITERATIONS,
        dklen=SCRYPT_DKLEN,
    )


def _verify_password(plaintext: str, stored_hash: bytes, salt: bytes) -> bool:
    """Constant-time verification of *plaintext* against *stored_hash*.

    Uses ``hmac.compare_digest`` to avoid leaking timing information.
    """
    try:
        candidate = _hash_password(plaintext, salt)
    except Exception:
        return False
    return hmac.compare_digest(candidate, stored_hash)


def _read_secret(name: str) -> str:
    """Read a secret from env, falling back to ``st.secrets`` if
    Streamlit is available. Mirrors the pattern used in
    ``engine/narration_engine.py::_get_anthropic_key``."""
    val = os.environ.get(name, "")
    if val:
        return val
    try:
        import streamlit as st  # imported lazily — not a hard dep
        val = st.secrets.get(name, "")
        if val:
            return str(val)
    except Exception:
        pass
    return ""


def _get_password_config() -> Optional[Tuple[bytes, bytes]]:
    """Return ``(hash_bytes, salt_bytes)`` if both env vars are set
    and decode as hex, else ``None``.

    A missing or malformed pair → returns ``None`` (open mode).
    """
    hash_hex = _read_secret(ENV_HASH_KEY)
    salt_hex = _read_secret(ENV_SALT_KEY)
    if not hash_hex or not salt_hex:
        return None
    try:
        hash_bytes = bytes.fromhex(hash_hex)
        salt_bytes = bytes.fromhex(salt_hex)
    except ValueError:
        logger.warning(
            f"auth.gate: {ENV_HASH_KEY} or {ENV_SALT_KEY} is not valid "
            "hex; treating as not configured (open mode)."
        )
        return None
    if len(hash_bytes) != SCRYPT_DKLEN or len(salt_bytes) != SALT_BYTES:
        logger.warning(
            f"auth.gate: hash length {len(hash_bytes)} or salt length "
            f"{len(salt_bytes)} unexpected; treating as not configured."
        )
        return None
    return hash_bytes, salt_bytes


def generate_password_hash(plaintext: str) -> Tuple[str, str]:
    """One-shot helper: produce ``(hash_hex, salt_hex)`` for a plaintext.

    Run once at setup time, copy the two hex strings into your
    ``secrets.toml`` (or set as env vars):

        python3 -c "from auth.gate import generate_password_hash; \\
                    print(generate_password_hash('mypassword'))"
    """
    salt = secrets.token_bytes(SALT_BYTES)
    hashed = _hash_password(plaintext, salt)
    return hashed.hex(), salt.hex()


# ── Session state helpers ─────────────────────────────────────────────────

def _get_or_create_auth_state() -> AuthState:
    """Read or initialize the per-session ``AuthState`` in
    ``st.session_state``. Importing Streamlit is deferred so the engine
    layer above stays import-clean."""
    import streamlit as st
    raw = st.session_state.get(_SESSION_STATE_KEY)
    if isinstance(raw, AuthState):
        return raw
    state = AuthState()
    st.session_state[_SESSION_STATE_KEY] = state
    return state


def _is_locked(state: AuthState) -> bool:
    """True if the session is currently in lockout."""
    if not state.locked_until:
        return False
    try:
        unlock_at = datetime.fromisoformat(state.locked_until)
    except ValueError:
        # Corrupt timestamp → fail open (cleared on next attempt)
        return False
    now = datetime.now(timezone.utc)
    if unlock_at.tzinfo is None:
        unlock_at = unlock_at.replace(tzinfo=timezone.utc)
    return now < unlock_at


def _seconds_until_unlock(state: AuthState) -> int:
    """Whole seconds remaining in the current lockout (>=0)."""
    if not state.locked_until:
        return 0
    try:
        unlock_at = datetime.fromisoformat(state.locked_until)
    except ValueError:
        return 0
    if unlock_at.tzinfo is None:
        unlock_at = unlock_at.replace(tzinfo=timezone.utc)
    delta = unlock_at - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds()))


# ── Public API ────────────────────────────────────────────────────────────

def is_authenticated() -> bool:
    """True if no password is configured (open mode) OR the current
    session has authenticated successfully."""
    global _OPEN_MODE_WARNED
    config = _get_password_config()
    if config is None:
        if not _OPEN_MODE_WARNED:
            logger.warning(
                f"auth.gate: {ENV_HASH_KEY}/{ENV_SALT_KEY} not configured "
                "— app is running in OPEN MODE (no authentication required)."
            )
            _OPEN_MODE_WARNED = True
        return True
    try:
        state = _get_or_create_auth_state()
    except Exception:
        # No Streamlit runtime (e.g. import-time check) → not auth'd
        return False
    return bool(state.authenticated)


def require_auth() -> bool:
    """Gate the page on authentication.

    Returns ``True`` immediately if no password is configured (open
    mode) or the session is already authenticated.

    Otherwise renders a login form via Streamlit at the top of the
    page and calls ``st.stop()`` to halt the rest of the page from
    rendering. This function returns ``False`` only on the path that
    is immediately followed by ``st.stop()`` — callers don't need
    to check the return value, but the contract is documented for
    clarity.
    """
    config = _get_password_config()
    if config is None:
        # Open mode — log once via is_authenticated() and return
        return is_authenticated()

    import streamlit as st
    state = _get_or_create_auth_state()
    if state.authenticated:
        return True

    stored_hash, salt = config

    st.markdown(
        '<div style="max-width:420px;margin:64px auto 0 auto;'
        'padding:32px 28px;background:rgba(232,230,225,0.02);'
        'border:1px solid rgba(232,230,225,0.08);border-radius:6px">'
        '<div style="font-family:Libre Baskerville,Georgia,serif;'
        'font-size:1.4rem;font-weight:700;color:#e8e6e1;'
        'margin-bottom:4px">The Ship Tracker</div>'
        '<div style="font-family:Libre Franklin,sans-serif;'
        'font-size:0.7rem;font-weight:600;color:#6b6760;'
        'letter-spacing:0.12em;text-transform:uppercase;'
        'margin-bottom:24px">Restricted Access</div>',
        unsafe_allow_html=True,
    )

    if _is_locked(state):
        remaining = _seconds_until_unlock(state)
        st.error(
            f"Too many failed attempts. Try again in {remaining}s."
        )
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()
        return False  # unreachable — st.stop() halts execution

    with st.form("auth_login_form", clear_on_submit=True):
        password = st.text_input(
            "Password",
            type="password",
            key="auth_password_input",
            label_visibility="collapsed",
            placeholder="Password",
        )
        submitted = st.form_submit_button("Sign in", use_container_width=True)

    if submitted:
        if _verify_password(password or "", stored_hash, salt):
            state.authenticated = True
            state.attempt_count = 0
            state.locked_until = ""
            st.markdown("</div>", unsafe_allow_html=True)
            st.rerun()
            return True  # unreachable in practice — rerun reloads the script
        else:
            state.attempt_count += 1
            remaining_attempts = MAX_ATTEMPTS - state.attempt_count
            if state.attempt_count >= MAX_ATTEMPTS:
                unlock_at = datetime.now(timezone.utc) + timedelta(
                    seconds=LOCKOUT_DURATION_SECONDS
                )
                state.locked_until = unlock_at.isoformat()
                st.error(
                    f"Too many failed attempts. Locked for "
                    f"{LOCKOUT_DURATION_SECONDS // 60} minutes."
                )
            else:
                st.error(
                    f"Incorrect password. {remaining_attempts} "
                    f"attempt{'s' if remaining_attempts != 1 else ''} remaining."
                )

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()
    return False  # unreachable — st.stop() halts execution


def logout() -> None:
    """Clear the authenticated flag and rerun the page."""
    import streamlit as st
    state = _get_or_create_auth_state()
    state.authenticated = False
    state.attempt_count = 0
    state.locked_until = ""
    # Multi-user session: also clear the per-user session token so the
    # next page render bounces back to the login form.
    if "current_user" in st.session_state:
        try:
            del st.session_state["current_user"]
        except Exception:
            st.session_state["current_user"] = None
    st.rerun()


# ── Multi-user (v7+) gate ─────────────────────────────────────────────────

def require_auth_with_users() -> bool:
    """Multi-user-aware authentication gate.

    Mode selection (the function decides at call time):

      * ``count_users() > 0`` — render the multi-user login / signup
        tabs. On success, the authenticated ``User`` is stored in
        ``st.session_state.current_user``; subsequent calls return
        immediately while that session-state key is set.
      * ``count_users() == 0`` AND ``APP_PASSWORD_HASH`` set — fall
        back to the legacy single-password gate (delegates to
        ``require_auth``).
      * ``count_users() == 0`` AND ``APP_PASSWORD_HASH`` not set —
        OPEN MODE: returns ``True`` immediately. ``is_authenticated``
        already logs the warning on first call.

    Adopting multi-user auth is therefore opt-in via the call site:
    the existing ``require_auth`` keeps working unchanged, and this
    new entry point is what app.py would call once it's ready to
    switch.

    Returns ``True`` when the request may proceed. Calls ``st.stop()``
    and does not return on the un-authenticated path (matching the
    contract of ``require_auth``).
    """
    # Defer the streamlit import: the engine layer above must stay
    # importable without a running Streamlit runtime.
    import streamlit as st

    # Defer the users import too — it touches the SQLite layer.
    from auth.users import (
        User,
        count_users,
        login,
        login_requires_mfa,
        signup,
    )

    n_users = count_users()

    # Mode 2 / 3: no users registered → fall back to legacy behaviour.
    if n_users == 0:
        # require_auth() handles both the open-mode and single-password
        # cases on its own. We just delegate.
        return require_auth()

    # Mode 1: at least one user → render the multi-user UI. If a
    # session token is already set, allow through immediately.
    existing = st.session_state.get("current_user")
    if isinstance(existing, User):
        return True

    # Render the auth surface. Same Refined-Steel container styling as
    # the single-password gate.
    st.markdown(
        '<div style="max-width:420px;margin:64px auto 0 auto;'
        'padding:32px 28px;background:rgba(232,230,225,0.02);'
        'border:1px solid rgba(232,230,225,0.08);border-radius:6px">'
        '<div style="font-family:Libre Baskerville,Georgia,serif;'
        'font-size:1.4rem;font-weight:700;color:#e8e6e1;'
        'margin-bottom:4px">The Ship Tracker</div>'
        '<div style="font-family:Libre Franklin,sans-serif;'
        'font-size:0.7rem;font-weight:600;color:#6b6760;'
        'letter-spacing:0.12em;text-transform:uppercase;'
        'margin-bottom:24px">Sign in or create an account</div>',
        unsafe_allow_html=True,
    )

    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    with login_tab:
        with st.form("auth_users_login_form", clear_on_submit=False):
            u = st.text_input(
                "Username",
                key="auth_users_login_username",
                label_visibility="collapsed",
                placeholder="Username",
            )
            p = st.text_input(
                "Password",
                type="password",
                key="auth_users_login_password",
                label_visibility="collapsed",
                placeholder="Password",
            )
            # Third field: 6-digit TOTP code from the user's
            # authenticator app. Optional in the form — non-MFA accounts
            # ignore it; MFA-enabled accounts require it. We always
            # render the field (rather than conditionally showing it
            # after the password is accepted) so the login flow is one
            # round-trip and there is no observable "MFA prompt
            # appeared" signal an attacker could use to enumerate which
            # accounts have MFA on. The placeholder spells out the
            # intent so first-time non-MFA users know to ignore it.
            mfa = st.text_input(
                "MFA code (if enabled)",
                key="auth_users_login_mfa",
                label_visibility="collapsed",
                placeholder="MFA code (only if enabled)",
                max_chars=10,
            )
            submitted = st.form_submit_button(
                "Log in", use_container_width=True
            )
        if submitted:
            username_in = u or ""
            password_in = p or ""
            mfa_in = (mfa or "").strip()

            # First-pass: try password (and MFA if supplied). On success
            # we are done. On None, distinguish the "MFA required" case
            # so the user gets actionable feedback rather than the
            # generic "wrong credentials" toast.
            user = login(
                username_in,
                password_in,
                mfa_code=(mfa_in or None),
            )
            if user is not None:
                st.session_state["current_user"] = user
                st.markdown("</div>", unsafe_allow_html=True)
                st.rerun()
                return True  # unreachable in practice
            # MFA-enabled account, no code supplied → tell the user
            # what is missing. We only consult ``login_requires_mfa``
            # AFTER the password-only attempt has failed, so we never
            # leak the MFA flag for usernames that nobody is logging
            # into. (If the password was wrong, the second branch
            # below fires regardless of the MFA flag.)
            if not mfa_in and login_requires_mfa(username_in):
                st.error(
                    "MFA code required for this account. Enter the "
                    "current code from your authenticator app."
                )
            else:
                # #9: audit the failed credential attempt for security review
                # (brute-force / credential-stuffing surfaces as a spike of
                # login_failed events). Guarded so an audit hiccup can never
                # break the login form. Not fired on the benign MFA-required
                # branch above (a legit user's password-then-code two-step).
                try:
                    from auth.audit import record_login_failure
                    record_login_failure(username_in)
                except Exception:
                    pass
                st.error("Invalid username, password, or MFA code.")

    with signup_tab:
        with st.form("auth_users_signup_form", clear_on_submit=False):
            new_u = st.text_input(
                "Username (3-32 chars: letters, digits, _ or -)",
                key="auth_users_signup_username",
                label_visibility="collapsed",
                placeholder="Username",
            )
            new_p = st.text_input(
                "Password (min 8 chars)",
                type="password",
                key="auth_users_signup_password",
                label_visibility="collapsed",
                placeholder="Password",
            )
            create = st.form_submit_button(
                "Create account", use_container_width=True
            )
        if create:
            user = signup(new_u or "", new_p or "")
            if user is not None:
                st.session_state["current_user"] = user
                st.markdown("</div>", unsafe_allow_html=True)
                st.rerun()
                return True  # unreachable in practice
            st.error(
                "Could not create account. Check the username and "
                "password requirements above."
            )

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()
    return False  # unreachable — st.stop() halts execution
