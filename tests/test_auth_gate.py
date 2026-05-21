"""Tests for the single-password authentication gate (auth/gate.py).

These tests exercise the engine layer (no Streamlit) directly with
``monkeypatch.setenv``, and the session-state layer through the
``mock_streamlit`` fixture from ``conftest.py``. They never read real
secrets — env vars are scrubbed at the top of every test.
"""
from __future__ import annotations

import hmac
import inspect
import secrets

import pytest

from auth import gate as auth_gate
from auth.gate import (
    ENV_HASH_KEY,
    ENV_SALT_KEY,
    MAX_ATTEMPTS,
    SCRYPT_DKLEN,
    AuthState,
    _get_password_config,
    _hash_password,
    _verify_password,
    generate_password_hash,
    is_authenticated,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    """Make sure no real APP_PASSWORD_* env var bleeds into a test."""
    monkeypatch.delenv(ENV_HASH_KEY, raising=False)
    monkeypatch.delenv(ENV_SALT_KEY, raising=False)
    # Reset the module-level "warned about open mode" flag so the
    # ``logger.warning`` branch behaves identically in every test.
    auth_gate._OPEN_MODE_WARNED = False
    yield


# ── _hash_password — determinism + salt sensitivity ───────────────────────

def test_hash_password_is_deterministic_for_same_inputs() -> None:
    salt = b"\x00" * 16
    h1 = _hash_password("hunter2", salt)
    h2 = _hash_password("hunter2", salt)
    assert h1 == h2
    assert len(h1) == SCRYPT_DKLEN


def test_hash_password_differs_for_different_salts() -> None:
    h1 = _hash_password("hunter2", b"\x00" * 16)
    h2 = _hash_password("hunter2", b"\x01" * 16)
    assert h1 != h2


def test_hash_password_differs_for_different_plaintexts() -> None:
    salt = b"\x00" * 16
    assert _hash_password("alpha", salt) != _hash_password("beta", salt)


# ── _verify_password — correctness + constant-time check ──────────────────

def test_verify_password_accepts_correct_password() -> None:
    salt = secrets.token_bytes(16)
    stored = _hash_password("correct-horse-battery-staple", salt)
    assert _verify_password("correct-horse-battery-staple", stored, salt) is True


def test_verify_password_rejects_wrong_password() -> None:
    salt = secrets.token_bytes(16)
    stored = _hash_password("correct-horse-battery-staple", salt)
    assert _verify_password("Tr0ub4dor&3", stored, salt) is False


def test_verify_password_uses_constant_time_compare() -> None:
    """The function MUST use ``hmac.compare_digest`` — a normal ``==``
    comparison would leak password length / prefix-match information
    via timing. We assert this by inspecting the source."""
    src = inspect.getsource(_verify_password)
    assert "hmac.compare_digest" in src, (
        "_verify_password must use hmac.compare_digest for constant-time "
        "comparison to avoid timing attacks."
    )


# ── _get_password_config — env var handling ───────────────────────────────

def test_get_password_config_returns_none_when_env_unset() -> None:
    # _scrub_env autouse already cleared both — this is the open-mode case.
    assert _get_password_config() is None


def test_get_password_config_returns_none_when_only_hash_set(monkeypatch) -> None:
    monkeypatch.setenv(ENV_HASH_KEY, "ab" * 32)
    assert _get_password_config() is None


def test_get_password_config_returns_none_when_only_salt_set(monkeypatch) -> None:
    monkeypatch.setenv(ENV_SALT_KEY, "ab" * 16)
    assert _get_password_config() is None


def test_get_password_config_returns_tuple_when_both_set(monkeypatch) -> None:
    hash_hex, salt_hex = generate_password_hash("secret")
    monkeypatch.setenv(ENV_HASH_KEY, hash_hex)
    monkeypatch.setenv(ENV_SALT_KEY, salt_hex)
    cfg = _get_password_config()
    assert cfg is not None
    h, s = cfg
    assert h == bytes.fromhex(hash_hex)
    assert s == bytes.fromhex(salt_hex)
    # And the round-trip verifies correctly:
    assert _verify_password("secret", h, s) is True


def test_get_password_config_returns_none_for_invalid_hex(monkeypatch) -> None:
    monkeypatch.setenv(ENV_HASH_KEY, "not-hex-at-all")
    monkeypatch.setenv(ENV_SALT_KEY, "also-not-hex")
    assert _get_password_config() is None


def test_get_password_config_returns_none_for_wrong_length(monkeypatch) -> None:
    # Valid hex but wrong byte count → treat as unconfigured.
    monkeypatch.setenv(ENV_HASH_KEY, "ab" * 4)   # 4 bytes, expected 32
    monkeypatch.setenv(ENV_SALT_KEY, "cd" * 4)   # 4 bytes, expected 16
    assert _get_password_config() is None


# ── generate_password_hash — one-shot helper ──────────────────────────────

def test_generate_password_hash_returns_hex_strings() -> None:
    hash_hex, salt_hex = generate_password_hash("mypassword")
    # 32 bytes → 64 hex chars; 16 bytes → 32 hex chars
    assert len(hash_hex) == 64
    assert len(salt_hex) == 32
    # Must be valid hex
    bytes.fromhex(hash_hex)
    bytes.fromhex(salt_hex)


def test_generate_password_hash_uses_fresh_salt_each_call() -> None:
    h1, s1 = generate_password_hash("mypassword")
    h2, s2 = generate_password_hash("mypassword")
    # Same plaintext + different random salts → different hashes
    assert s1 != s2
    assert h1 != h2


# ── is_authenticated — open mode + session state ──────────────────────────

def test_is_authenticated_returns_true_in_open_mode() -> None:
    # No env vars set; engine layer alone — no Streamlit needed.
    assert is_authenticated() is True


def test_is_authenticated_reflects_session_state_when_configured(
    monkeypatch, mock_streamlit
) -> None:
    hash_hex, salt_hex = generate_password_hash("secret")
    monkeypatch.setenv(ENV_HASH_KEY, hash_hex)
    monkeypatch.setenv(ENV_SALT_KEY, salt_hex)

    # Fresh session — not authenticated
    assert is_authenticated() is False

    # Flip the session flag — now authenticated
    state = auth_gate._get_or_create_auth_state()
    assert isinstance(state, AuthState)
    state.authenticated = True
    assert is_authenticated() is True

    # Clear it — back to unauthenticated
    state.authenticated = False
    assert is_authenticated() is False


# ── Lockout policy ────────────────────────────────────────────────────────

def test_max_attempts_constant_is_five() -> None:
    # Locks documented contract: 5 attempts before lockout.
    assert MAX_ATTEMPTS == 5


def test_locked_session_is_detected(mock_streamlit) -> None:
    from datetime import datetime, timedelta, timezone
    state = AuthState()
    state.locked_until = (
        datetime.now(timezone.utc) + timedelta(seconds=60)
    ).isoformat()
    assert auth_gate._is_locked(state) is True
    assert auth_gate._seconds_until_unlock(state) > 0


def test_expired_lockout_is_not_locked() -> None:
    from datetime import datetime, timedelta, timezone
    state = AuthState()
    state.locked_until = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()
    assert auth_gate._is_locked(state) is False
    assert auth_gate._seconds_until_unlock(state) == 0


def test_empty_locked_until_is_not_locked() -> None:
    state = AuthState()
    assert state.locked_until == ""
    assert auth_gate._is_locked(state) is False


# ── End-to-end: hash → store as env → verify a login attempt ──────────────

def test_end_to_end_setup_and_verify(monkeypatch) -> None:
    """Mirror exactly what a user does at setup:
       1. Generate (hash_hex, salt_hex) for their chosen password.
       2. Put them in env vars / secrets.toml.
       3. The next login attempt must verify successfully.
    """
    hash_hex, salt_hex = generate_password_hash("dock-the-yacht-2026")
    monkeypatch.setenv(ENV_HASH_KEY, hash_hex)
    monkeypatch.setenv(ENV_SALT_KEY, salt_hex)
    cfg = _get_password_config()
    assert cfg is not None
    stored_hash, salt = cfg
    assert _verify_password("dock-the-yacht-2026", stored_hash, salt) is True
    assert _verify_password("wrong-password", stored_hash, salt) is False


# ── Defensive: engine layer must not import Streamlit ─────────────────────

def test_engine_functions_do_not_import_streamlit() -> None:
    """The hash/verify/config engine functions must work without
    Streamlit installed. Inspecting the source ensures no top-level
    streamlit import sneaks in."""
    src = inspect.getsource(auth_gate)
    # Top-level imports section ends at the first `def `.
    top = src.split("\ndef ", 1)[0]
    assert "import streamlit" not in top, (
        "auth/gate.py must not import streamlit at module scope — "
        "only require_auth/logout/session-state helpers may import it."
    )


# ── AuthState dataclass shape ─────────────────────────────────────────────

def test_auth_state_default_values() -> None:
    state = AuthState()
    assert state.authenticated is False
    assert state.attempt_count == 0
    assert state.locked_until == ""


def test_auth_state_is_mutable() -> None:
    state = AuthState()
    state.authenticated = True
    state.attempt_count = 3
    state.locked_until = "2026-01-01T00:00:00+00:00"
    assert state.authenticated is True
    assert state.attempt_count == 3
    assert state.locked_until == "2026-01-01T00:00:00+00:00"
