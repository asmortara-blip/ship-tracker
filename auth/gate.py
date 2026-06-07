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

# ── Session lifetime (R118) ───────────────────────────────────────────────
# Idle timeout: a session with no activity (no page render that passes the
# gate) for longer than this is bounced back to the login form.
IDLE_TIMEOUT_MINUTES = 60
# Absolute lifetime cap: a session older than this is bounced regardless of
# activity, so an always-open browser cannot stay authenticated forever.
ABSOLUTE_LIFETIME_HOURS = 12

# Session-state keys holding the two lifetime stamps (ISO-8601 UTC strings).
# Stamped at login success alongside ``current_user`` and refreshed on each
# gate pass. Kept as sibling keys (not on the User dataclass) so the User
# identity surface stays immutable and credential-free.
_SESSION_AUTHED_AT_KEY = "_session_authed_at"
_SESSION_LAST_SEEN_KEY = "_session_last_seen"

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


def _parse_iso_utc(value: object) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a tz-aware UTC ``datetime``.

    Returns ``None`` when *value* is missing, the wrong type, or
    unparseable. A tz-naive timestamp is ASSUMED to be UTC (the gate only
    ever writes tz-aware UTC stamps; a naive one means a hand-edited or
    legacy value, and assuming UTC is the safe interpretation here).
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _session_expiry_check(
    authed_at: object,
    last_seen: object,
    now: datetime,
) -> Tuple[bool, str]:
    """Pure decision: has this session expired? ``(expired, reason)``.

    Inputs are the raw stored stamps (ISO strings or anything) and the
    current time. This function NEVER raises and has no Streamlit
    dependency, so it is unit-tested in isolation.

    Rules (fail-closed on corruption, fail-open on first-seen):

    * BOTH stamps missing/empty → ``(False, "no_stamps")``. A session that
      has a user but no lifetime stamps is treated as *just starting* its
      window — the caller stamps ``now`` and allows. This avoids expiring
      pre-existing sessions (and pre-R118 tests) that never got stamped.
    * A stamp that is PRESENT but unparseable → ``(True, "unparseable_*")``.
      Corruption is treated as expired (fail-closed for security): we
      cannot trust a timestamp we cannot read.
    * ``now - authed_at > ABSOLUTE_LIFETIME_HOURS`` → ``(True, "absolute")``.
    * ``now - last_seen > IDLE_TIMEOUT_MINUTES`` → ``(True, "idle")``.
    * Otherwise → ``(False, "ok")``.

    The absolute cap is checked before idle so an old-but-recently-active
    session reports the more fundamental reason.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    authed_present = isinstance(authed_at, str) and bool(authed_at)
    last_seen_present = isinstance(last_seen, str) and bool(last_seen)

    # First-seen-without-stamps: allow and let the caller stamp now.
    if not authed_present and not last_seen_present:
        return (False, "no_stamps")

    authed_dt = _parse_iso_utc(authed_at) if authed_present else None
    last_seen_dt = _parse_iso_utc(last_seen) if last_seen_present else None

    # A stamp that exists but won't parse is corruption → fail closed.
    if authed_present and authed_dt is None:
        return (True, "unparseable_authed_at")
    if last_seen_present and last_seen_dt is None:
        return (True, "unparseable_last_seen")

    # Absolute lifetime cap (checked first — the more fundamental bound).
    if authed_dt is not None:
        if now - authed_dt > timedelta(hours=ABSOLUTE_LIFETIME_HOURS):
            return (True, "absolute")

    # Idle timeout.
    if last_seen_dt is not None:
        if now - last_seen_dt > timedelta(minutes=IDLE_TIMEOUT_MINUTES):
            return (True, "idle")

    return (False, "ok")


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


# ── Session lifetime helpers (R118) ───────────────────────────────────────

def _now_iso() -> str:
    """Current time as an ISO-8601 UTC string (matches the stored shape)."""
    return datetime.now(timezone.utc).isoformat()


def stamp_session_start(st) -> None:
    """Record session start: set both ``authed_at`` and ``last_seen`` to now.

    Called on every login-success path (single-password and multi-user) so
    the idle + absolute windows begin at the moment of authentication.
    Never raises — a session-state hiccup must not break login.
    """
    try:
        now = _now_iso()
        st.session_state[_SESSION_AUTHED_AT_KEY] = now
        st.session_state[_SESSION_LAST_SEEN_KEY] = now
    except Exception:
        pass


def _touch_last_seen(st) -> None:
    """Mark this gate-pass as activity: refresh ``last_seen`` to now. Never
    raises."""
    try:
        st.session_state[_SESSION_LAST_SEEN_KEY] = _now_iso()
    except Exception:
        pass


def _pop_session_key(st, key) -> None:
    """Best-effort remove one session_state key. Never raises."""
    try:
        if key in st.session_state:
            del st.session_state[key]
    except Exception:
        try:
            st.session_state[key] = None
        except Exception:
            pass


def _clear_session_lifetime(st) -> None:
    """Drop the two lifetime stamps. Never raises."""
    _pop_session_key(st, _SESSION_AUTHED_AT_KEY)
    _pop_session_key(st, _SESSION_LAST_SEEN_KEY)


# User-scoped values cached in ``st.session_state`` that MUST be purged when a
# session ENDS (timeout-expiry OR logout) — otherwise the next user to sign in
# on the SAME browser session inherits the prior user's positions / alert rules /
# investor reports / pending-MFA secrets, and the init-once-no-rekey tab loaders
# (e.g. tab_portfolio._init_positions, tab_alerts) adopt them: cross-user data
# disclosure + ledger write-corruption. Keep in sync with the per-tab session
# caches. (R118 adversarial-review finding.)
_USER_SCOPED_SESSION_KEYS: tuple[str, ...] = (
    "portfolio_positions",
    "user_alerts",
    "active_filter_payload",
    "active_filter_name",
    "current_investor_report",
    "previous_investor_report",
    "mfa_pending_secret",
    "mfa_pending_codes",
    "pending_mfa_secret",
    "mfa_confirm_disable",
    "mfa_regen_confirm",
    "setup_generated_hash",
    "setup_generated_salt",
)


def _clear_user_scoped_session(st) -> None:
    """Fully tear down a user's session: drop ``current_user``, the lifetime
    stamps, AND every user-scoped cache, so the next sign-in on the same browser
    inherits NOTHING. Shared by the timeout-expiry and logout paths so the two
    re-auth triggers purge identically. Never raises."""
    _pop_session_key(st, "current_user")
    _clear_session_lifetime(st)
    for key in _USER_SCOPED_SESSION_KEYS:
        _pop_session_key(st, key)


def _expire_multiuser_session(st) -> None:
    """Tear down an expired multi-user session — drop the current user, the
    lifetime stamps, and every user-scoped cache so the gate falls through to the
    login form with nothing for the next user to inherit. Never raises."""
    _clear_user_scoped_session(st)


def _enforce_session_lifetime(st) -> bool:
    """Apply the R118 idle + absolute lifetime policy to the current
    session. Returns ``True`` if the session is still valid (and refreshes
    ``last_seen``), ``False`` if it just expired (and tears the session
    down). Never raises — on any unexpected error it returns ``True`` so the
    gate fails open to the EXISTING behaviour rather than crashing a live
    session (the stamps are best-effort hardening, not the primary auth
    decision, which still rests on ``current_user`` being present).
    """
    try:
        authed_at = st.session_state.get(_SESSION_AUTHED_AT_KEY)
        last_seen = st.session_state.get(_SESSION_LAST_SEEN_KEY)
        expired, reason = _session_expiry_check(
            authed_at, last_seen, datetime.now(timezone.utc)
        )
        if expired:
            logger.info(f"auth.gate: session expired ({reason}); re-auth required.")
            _expire_multiuser_session(st)
            return False
        if reason == "no_stamps":
            # Legacy/edge session with a user but no stamps: start the
            # window now rather than expiring it.
            stamp_session_start(st)
        else:
            _touch_last_seen(st)
        return True
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"auth.gate: session-lifetime check failed; allowing: {exc}")
        return True


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
        # R118: enforce idle + absolute lifetime on the single-password
        # session too. On expiry, flip the flag off and fall through to the
        # login form below; otherwise refresh ``last_seen``.
        if _enforce_session_lifetime(st):
            return True
        state.authenticated = False
        try:
            st.info("Your session expired. Please sign in again.")
        except Exception:
            pass

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
            stamp_session_start(st)  # R118: begin idle + absolute windows
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
    # Multi-user session: drop current_user, the lifetime stamps, AND every
    # user-scoped cache (positions/alerts/reports/pending-MFA secrets), so the
    # next user on the same browser inherits nothing. Converges the logout +
    # timeout-expiry teardown (R118 review).
    _clear_user_scoped_session(st)
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
    # session token is already set, allow through immediately — but FIRST
    # enforce the R118 idle + absolute lifetime policy. An expired session
    # is torn down (current_user dropped) and falls through to the login
    # form below; a valid one has its ``last_seen`` refreshed.
    existing = st.session_state.get("current_user")
    if isinstance(existing, User):
        if _enforce_session_lifetime(st):
            return True
        # Expired: brief notice, then render the login form (same path an
        # unauthenticated user takes — do NOT crash).
        try:
            st.info("Your session expired. Please sign in again.")
        except Exception:
            pass

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
                stamp_session_start(st)  # R118: begin idle + absolute windows
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
                stamp_session_start(st)  # R118: begin idle + absolute windows
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
