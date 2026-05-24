"""Tests for ``auth.mfa`` — optional TOTP MFA second factor.

Covers:
  * ``generate_secret`` returns a fresh 32-char base32 string per call.
  * ``compute_totp`` matches the RFC 6238 reference test vector
    (Appendix B, SHA-1 — shared secret ASCII "12345678901234567890",
    20 bytes).
  * ``verify_totp`` accepts the current code, rejects wrong codes,
    accepts ±window drift (and rejects beyond window).
  * ``provisioning_uri`` builds a valid otpauth:// URI with URL-
    encoded label segments.
  * The DB-bound helpers (``enable_mfa`` / ``disable_mfa`` /
    ``is_mfa_enabled``) flip the column and round-trip.
  * The MFA gate inside ``auth.users.login`` enforces the second
    factor on enabled accounts and is silent on non-MFA accounts.
  * Audit hooks fire on enable, disable, and successful MFA login.

The RFC 6238 test vector used by ``test_compute_totp_matches_rfc6238_vector``
is the canonical Appendix B fixture: shared secret ASCII
``"12345678901234567890"`` (20 bytes), TOTP-SHA1, period 30. At T=59 the
8-digit code is ``94287082`` (last 6 = ``287082``). All six tabulated
test times round-trip correctly.
"""
from __future__ import annotations

import base64

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB at a per-test tmp_path."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# RFC 6238 Appendix B test vector (SHA-1): 20-byte ASCII secret.
_RFC6238_SECRET_ASCII = b"12345678901234567890"
_RFC6238_SECRET_B32 = base64.b32encode(_RFC6238_SECRET_ASCII).decode().rstrip("=")

# (unix_time, expected_8digit_code) tabulated in RFC 6238 Appendix B
# for the SHA-1 / 30s configuration. The function under test returns a
# 6-digit code; we slice the last 6 chars off the published 8-digit
# values to compare.
_RFC6238_VECTORS = [
    (59,            "94287082"),
    (1111111109,    "07081804"),
    (1111111111,    "14050471"),
    (1234567890,    "89005924"),
    (2000000000,    "69279037"),
    (20000000000,   "65353130"),
]


# ── generate_secret ───────────────────────────────────────────────────────

def test_generate_secret_returns_32_char_base32() -> None:
    from auth.mfa import generate_secret

    s = generate_secret()
    # 20 raw bytes → 32 base32 chars after padding-strip.
    assert isinstance(s, str)
    assert len(s) == 32
    # Must be valid base32 (re-add padding before decoding).
    pad = (-len(s)) % 8
    raw = base64.b32decode(s + "=" * pad)
    assert len(raw) == 20


def test_generate_secret_is_distinct_across_calls() -> None:
    """Two consecutive calls MUST return different secrets — otherwise
    the RNG path is broken and every user's MFA would share a secret."""
    from auth.mfa import generate_secret

    # 10 fresh calls; collisions on a 160-bit secret have vanishing
    # probability so any duplicate here is a real bug.
    seen = {generate_secret() for _ in range(10)}
    assert len(seen) == 10


# ── compute_totp ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("t, expected_8", _RFC6238_VECTORS)
def test_compute_totp_matches_rfc6238_vector(t, expected_8) -> None:
    """Hand-computed reference: every RFC 6238 SHA-1 test vector must
    round-trip exactly. The function returns 6 digits, so we compare
    against the last 6 chars of the published 8-digit values."""
    from auth.mfa import compute_totp

    code = compute_totp(_RFC6238_SECRET_B32, when=float(t))
    assert code == expected_8[-6:]


def test_compute_totp_produces_different_codes_across_periods() -> None:
    """A 30s window means t=0 and t=30 fall in different periods and
    must produce different codes (the same period would not be a useful
    second factor)."""
    from auth.mfa import compute_totp

    code_a = compute_totp(_RFC6238_SECRET_B32, when=0.0)
    code_b = compute_totp(_RFC6238_SECRET_B32, when=30.0)
    code_c = compute_totp(_RFC6238_SECRET_B32, when=60.0)
    # All three must differ — flake risk is 1-in-10^6^2 ≈ 10^-12.
    assert code_a != code_b
    assert code_b != code_c
    assert code_a != code_c


def test_compute_totp_same_code_within_one_period() -> None:
    """Two timestamps inside the same 30s window must produce the same
    code — that is what makes a 30s drift window meaningful."""
    from auth.mfa import compute_totp

    a = compute_totp(_RFC6238_SECRET_B32, when=0.0)
    b = compute_totp(_RFC6238_SECRET_B32, when=29.999)
    assert a == b


def test_compute_totp_zero_pads_short_codes() -> None:
    """A computed code that happens to start with zeros must be returned
    as a zero-padded N-digit string — otherwise authenticator apps
    (which always render 6 chars) would not match."""
    from auth.mfa import compute_totp

    # T=1111111109 yields 081804 (last 6 of the published 07081804).
    code = compute_totp(_RFC6238_SECRET_B32, when=1111111109.0)
    assert code == "081804"
    assert len(code) == 6


def test_compute_totp_rejects_invalid_digits() -> None:
    from auth.mfa import compute_totp

    with pytest.raises(ValueError):
        compute_totp(_RFC6238_SECRET_B32, digits=5)
    with pytest.raises(ValueError):
        compute_totp(_RFC6238_SECRET_B32, digits=11)


# ── verify_totp ───────────────────────────────────────────────────────────

def test_verify_totp_accepts_current_code() -> None:
    from auth.mfa import compute_totp, verify_totp

    when = 1700000000.0
    code = compute_totp(_RFC6238_SECRET_B32, when=when)
    assert verify_totp(_RFC6238_SECRET_B32, code, when=when) is True


def test_verify_totp_rejects_wrong_code() -> None:
    from auth.mfa import verify_totp

    when = 1700000000.0
    # 000000 is a valid 6-digit shape that is almost certainly NOT the
    # current code — the verifier should reject it cleanly.
    assert verify_totp(_RFC6238_SECRET_B32, "000000", when=when) is False


def test_verify_totp_window_one_accepts_plus_30s_drift() -> None:
    """window=1 (default) accepts ±period seconds. A code minted for
    `when` must verify at `when + period` (one window ahead)."""
    from auth.mfa import compute_totp, verify_totp

    when = 1700000000.0
    code = compute_totp(_RFC6238_SECRET_B32, when=when)
    # Verifier "now" is 30s LATER — the verifier should still accept
    # the older code because the code was issued one period ago.
    assert verify_totp(
        _RFC6238_SECRET_B32, code, when=when + 30, window=1
    ) is True


def test_verify_totp_window_one_accepts_minus_30s_drift() -> None:
    """And ±30s the other way — the user's clock is 30s ahead of ours."""
    from auth.mfa import compute_totp, verify_totp

    when = 1700000000.0
    code = compute_totp(_RFC6238_SECRET_B32, when=when)
    assert verify_totp(
        _RFC6238_SECRET_B32, code, when=when - 30, window=1
    ) is True


def test_verify_totp_window_zero_rejects_30s_drift() -> None:
    """window=0 means strict — codes from the prior or next period
    must NOT verify."""
    from auth.mfa import compute_totp, verify_totp

    when = 1700000000.0
    code = compute_totp(_RFC6238_SECRET_B32, when=when)
    assert verify_totp(
        _RFC6238_SECRET_B32, code, when=when + 30, window=0
    ) is False
    assert verify_totp(
        _RFC6238_SECRET_B32, code, when=when - 30, window=0
    ) is False


def test_verify_totp_window_one_rejects_beyond_window() -> None:
    """A code that is more than ``window`` periods stale must NOT
    verify — that is what makes the drift window bounded."""
    from auth.mfa import compute_totp, verify_totp

    when = 1700000000.0
    code = compute_totp(_RFC6238_SECRET_B32, when=when)
    # 90s of drift = 3 periods — clearly outside window=1.
    assert verify_totp(
        _RFC6238_SECRET_B32, code, when=when + 90, window=1
    ) is False


def test_verify_totp_malformed_code_returns_false() -> None:
    """Garbage input must surface as False, never raise."""
    from auth.mfa import verify_totp

    assert verify_totp(_RFC6238_SECRET_B32, "abcdef") is False
    assert verify_totp(_RFC6238_SECRET_B32, "") is False
    assert verify_totp(_RFC6238_SECRET_B32, "12345") is False  # too short
    assert verify_totp(_RFC6238_SECRET_B32, "1234567") is False  # too long
    assert verify_totp(_RFC6238_SECRET_B32, None) is False  # type: ignore[arg-type]


def test_verify_totp_malformed_secret_returns_false() -> None:
    """A non-base32 secret must surface as False, never raise — every
    DB-bound caller of this helper is on the auth hot path."""
    from auth.mfa import verify_totp

    assert verify_totp("not-base32-!@#$", "123456") is False
    assert verify_totp("", "123456") is False
    assert verify_totp(None, "123456") is False  # type: ignore[arg-type]


def test_verify_totp_strips_whitespace_in_code() -> None:
    """1Password and similar apps insert a space in the middle of the
    code for legibility ("123 456"). The verifier must tolerate that."""
    from auth.mfa import compute_totp, verify_totp

    when = 1700000000.0
    code = compute_totp(_RFC6238_SECRET_B32, when=when)
    spaced = code[:3] + " " + code[3:]
    assert verify_totp(_RFC6238_SECRET_B32, spaced, when=when) is True


# ── provisioning_uri ──────────────────────────────────────────────────────

def test_provisioning_uri_contains_required_pieces() -> None:
    from auth.mfa import provisioning_uri

    uri = provisioning_uri(
        "JBSWY3DPEHPK3PXP",
        account="alice@example.com",
        issuer="Ship Tracker",
    )
    assert uri.startswith("otpauth://totp/")
    # Issuer label is URL-encoded (space → %20).
    assert "Ship%20Tracker" in uri
    # Account is URL-encoded (@ → %40).
    assert "alice%40example.com" in uri
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    # The query-string issuer param is encoded by urlencode (+).
    assert "issuer=Ship+Tracker" in uri or "issuer=Ship%20Tracker" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri


def test_provisioning_uri_uses_issuer_account_label_form() -> None:
    """The label segment is ``Issuer:Account`` (Google Authenticator
    convention) — that controls how the entry is displayed in the app."""
    from auth.mfa import provisioning_uri

    uri = provisioning_uri(
        "JBSWY3DPEHPK3PXP",
        account="alice",
        issuer="ShipTracker",
    )
    # Path segment is `Issuer:Account` with both URL-encoded.
    assert "/totp/ShipTracker:alice?" in uri


# ── enable_mfa / disable_mfa / is_mfa_enabled ─────────────────────────────

def test_enable_mfa_sets_secret_and_flag() -> None:
    from auth.mfa import enable_mfa, generate_secret, is_mfa_enabled
    from auth.users import signup
    from state.db import get_connection

    user = signup("alice", "correct-password-123")
    assert user is not None

    secret = generate_secret()
    # v21 signature change: enable_mfa returns (ok, recovery_codes).
    ok, codes = enable_mfa(user.user_id, secret)
    assert ok is True
    # Auto-mint of recovery codes is expected on a successful enable.
    assert isinstance(codes, list)
    assert len(codes) == 10

    # Direct DB read to confirm the column flipped.
    conn = get_connection()
    row = conn.execute(
        "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = ?",
        (user.user_id,),
    ).fetchone()
    assert row["mfa_secret"] == secret
    assert row["mfa_enabled"] == 1
    assert is_mfa_enabled(user.user_id) is True


def test_disable_mfa_clears_secret_and_flag() -> None:
    from auth.mfa import disable_mfa, enable_mfa, generate_secret, is_mfa_enabled
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())
    assert is_mfa_enabled(user.user_id) is True

    assert disable_mfa(user.user_id) is True
    assert is_mfa_enabled(user.user_id) is False


def test_enable_mfa_rejects_unknown_user() -> None:
    from auth.mfa import enable_mfa, generate_secret

    # v21: failure path returns (False, None) — same shape as success
    # so callers cannot accidentally treat the codes as truthy.
    ok, codes = enable_mfa("does-not-exist", generate_secret())
    assert ok is False
    assert codes is None


def test_enable_mfa_rejects_malformed_secret() -> None:
    """A secret that does not base32-decode would silently break every
    subsequent login attempt — reject it at enroll time so the operator
    finds out immediately."""
    from auth.mfa import enable_mfa
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    ok, codes = enable_mfa(user.user_id, "!!! not base32 !!!")
    assert ok is False
    assert codes is None


def test_enable_mfa_rejects_empty_inputs() -> None:
    from auth.mfa import enable_mfa

    for inputs in (("", "abcd"), ("user-id", ""), (None, "abcd")):
        ok, codes = enable_mfa(inputs[0], inputs[1])  # type: ignore[arg-type]
        assert ok is False
        assert codes is None


def test_is_mfa_enabled_unknown_user_returns_false() -> None:
    from auth.mfa import is_mfa_enabled

    assert is_mfa_enabled("does-not-exist") is False
    assert is_mfa_enabled("") is False


# ── auth.users.login: MFA gate ────────────────────────────────────────────

def test_login_mfa_enabled_account_without_code_returns_none() -> None:
    """An MFA-enabled account must NOT log in on password alone."""
    from auth.mfa import enable_mfa, generate_secret
    from auth.users import login, signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())

    # Right password, no code → None (caller must supply the second factor).
    assert login("alice", "correct-password-123") is None


def test_login_mfa_enabled_account_with_correct_code_returns_user() -> None:
    """Right password + valid TOTP code → User."""
    from auth.mfa import compute_totp, enable_mfa, generate_secret
    from auth.users import login, signup
    import time as _time

    user = signup("alice", "correct-password-123")
    assert user is not None
    secret = generate_secret()
    enable_mfa(user.user_id, secret)

    # Compute the code for "now" — the verify_totp default window of 1
    # absorbs any small timing skew between this line and the actual
    # verification inside login().
    code = compute_totp(secret, when=_time.time())
    logged_in = login("alice", "correct-password-123", mfa_code=code)
    assert logged_in is not None
    assert logged_in.username == "alice"


def test_login_mfa_enabled_account_with_wrong_code_returns_none() -> None:
    """Wrong second-factor code must surface as None (same shape as bad
    password — no info leak about which step failed)."""
    from auth.mfa import enable_mfa, generate_secret
    from auth.users import login, signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())

    assert login(
        "alice", "correct-password-123", mfa_code="000000"
    ) is None


def test_login_non_mfa_account_ignores_mfa_code() -> None:
    """An account without MFA enabled must still log in with just the
    password — passing a code does NOT cause a failure (and does NOT
    require the code to be valid for some hypothetical secret)."""
    from auth.users import login, signup

    user = signup("alice", "correct-password-123")
    assert user is not None

    # No MFA on the account; arbitrary code is ignored.
    a = login("alice", "correct-password-123")
    b = login("alice", "correct-password-123", mfa_code="000000")
    c = login("alice", "correct-password-123", mfa_code="random-nonsense")
    assert a is not None and a.username == "alice"
    assert b is not None and b.username == "alice"
    assert c is not None and c.username == "alice"


def test_login_requires_mfa_helper_reflects_account_state() -> None:
    """The helper that lets the UI decide whether to show the second-
    factor input field must be True iff the account exists AND has MFA
    enabled."""
    from auth.mfa import disable_mfa, enable_mfa, generate_secret
    from auth.users import login_requires_mfa, signup

    user = signup("alice", "correct-password-123")
    assert user is not None

    # Fresh account: no MFA.
    assert login_requires_mfa("alice") is False
    # Unknown username: also False.
    assert login_requires_mfa("does-not-exist") is False
    assert login_requires_mfa("") is False

    # Enable MFA → True.
    enable_mfa(user.user_id, generate_secret())
    assert login_requires_mfa("alice") is True

    # Disable → False.
    disable_mfa(user.user_id)
    assert login_requires_mfa("alice") is False


# ── Audit hooks ───────────────────────────────────────────────────────────

def test_enable_mfa_fires_audit() -> None:
    from auth.audit import query_audit
    from auth.mfa import enable_mfa, generate_secret
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())

    events = query_audit(action="enable_mfa")
    assert len(events) >= 1
    assert events[0].entity_type == "user"
    assert events[0].entity_id == user.user_id
    assert events[0].user_id == user.user_id


def test_disable_mfa_fires_audit() -> None:
    from auth.audit import query_audit
    from auth.mfa import disable_mfa, enable_mfa, generate_secret
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())
    disable_mfa(user.user_id)

    events = query_audit(action="disable_mfa")
    assert len(events) >= 1
    assert events[0].entity_type == "user"
    assert events[0].entity_id == user.user_id


def test_successful_mfa_login_audit_carries_mfa_flag() -> None:
    """A successful login that exercised the second factor must record
    ``detail={'mfa': True}`` so an admin auditing logins can see which
    used MFA."""
    from auth.audit import query_audit
    from auth.mfa import compute_totp, enable_mfa, generate_secret
    from auth.users import login, signup
    import time as _time

    user = signup("alice", "correct-password-123")
    assert user is not None
    secret = generate_secret()
    enable_mfa(user.user_id, secret)

    code = compute_totp(secret, when=_time.time())
    logged_in = login("alice", "correct-password-123", mfa_code=code)
    assert logged_in is not None

    # The 'login' audit row for this user must carry mfa: True. Filter
    # by action='login' and user_id to ignore the surrounding signup /
    # enable_mfa rows.
    events = query_audit(action="login", user_id=user.user_id)
    assert len(events) >= 1
    assert events[0].detail_json.get("mfa") is True


def test_failed_mfa_login_does_not_audit() -> None:
    """Failed MFA does NOT audit (same enumeration-protection as
    failed password) — otherwise an attacker who knows a password could
    enumerate MFA-enabled accounts by counting audit rows."""
    from auth.audit import query_audit
    from auth.mfa import enable_mfa, generate_secret
    from auth.users import login, signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())

    # Wrong code → None, no audit row.
    assert login("alice", "correct-password-123", mfa_code="000000") is None

    # No login audit rows for this user. (The signup + enable_mfa rows
    # use different actions, so filtering by action='login' isolates.)
    events = query_audit(action="login", user_id=user.user_id)
    assert events == []


def test_non_mfa_login_audit_does_not_carry_mfa_flag() -> None:
    """A successful login WITHOUT MFA must NOT set detail.mfa — the
    flag is what distinguishes MFA logins in the audit timeline."""
    from auth.audit import query_audit
    from auth.users import login, signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    logged_in = login("alice", "correct-password-123")
    assert logged_in is not None

    events = query_audit(action="login", user_id=user.user_id)
    assert len(events) >= 1
    # detail_json is either {} or missing the mfa key.
    assert events[0].detail_json.get("mfa") is not True


# ── Recovery codes (v21) ──────────────────────────────────────────────────

def test_enable_mfa_returns_recovery_codes() -> None:
    """The v21 enable_mfa contract: success returns
    ``(True, list_of_10_codes)``. Tests pin the shape so callers can
    safely unpack the tuple."""
    from auth.mfa import enable_mfa, generate_secret
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    ok, codes = enable_mfa(user.user_id, generate_secret())
    assert ok is True
    assert isinstance(codes, list)
    assert len(codes) == 10
    # Every code is a string in the canonical XXXXX-XXXXX format
    # (5 chars + dash + 5 chars = 11 chars total).
    for c in codes:
        assert isinstance(c, str)
        assert len(c) == 11
        assert c[5] == "-"


def test_generate_recovery_codes_are_distinct() -> None:
    """Two consecutive mints must produce non-overlapping batches —
    the RNG path must NOT recycle codes. A collision on a 50-bit
    code space is vanishingly rare so any duplicate here is a bug."""
    from auth.mfa import generate_recovery_codes
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None

    batch_a = generate_recovery_codes(user.user_id)
    batch_b = generate_recovery_codes(user.user_id)
    assert len(batch_a) == 10
    assert len(batch_b) == 10
    # No code overlap between batches.
    assert set(batch_a).isdisjoint(set(batch_b))
    # And the combined set has 20 distinct entries.
    assert len(set(batch_a) | set(batch_b)) == 20


def test_recovery_codes_never_stored_in_plaintext() -> None:
    """The DB column ``code_hash`` must NEVER equal any plaintext
    code — only the pbkdf2 digest lands on disk. This is the load-
    bearing security property of the whole recovery-codes feature."""
    from auth.mfa import generate_recovery_codes
    from auth.users import signup
    from state.db import get_connection

    user = signup("alice", "correct-password-123")
    assert user is not None
    codes = generate_recovery_codes(user.user_id)
    assert len(codes) == 10

    conn = get_connection()
    rows = conn.execute(
        "SELECT code_hash, salt FROM mfa_recovery_codes "
        "WHERE user_id = ?",
        (user.user_id,),
    ).fetchall()
    assert len(rows) == 10
    stored_hashes = {r["code_hash"] for r in rows}
    # No plaintext code may ever match a stored hash.
    assert stored_hashes.isdisjoint(set(codes))
    # No plaintext code may even appear as a substring of a hash
    # (defense against a buggy storage path that just hex-encoded
    # the code instead of hashing it).
    for plaintext in codes:
        for h in stored_hashes:
            assert plaintext not in h
    # Every salt is unique — fresh salt per code.
    salts = {r["salt"] for r in rows}
    assert len(salts) == 10


def test_verify_and_consume_recovery_code_accepts_valid_code() -> None:
    """The verify path must accept any unused code from the batch and
    flip its ``used_at`` so it cannot be reused."""
    from auth.mfa import (
        generate_recovery_codes,
        verify_and_consume_recovery_code,
    )
    from auth.users import signup
    from state.db import get_connection

    user = signup("alice", "correct-password-123")
    assert user is not None
    codes = generate_recovery_codes(user.user_id)
    assert codes

    assert verify_and_consume_recovery_code(user.user_id, codes[0]) is True
    # Second attempt with the same code must FAIL — it's now consumed.
    assert verify_and_consume_recovery_code(user.user_id, codes[0]) is False

    # The other 9 codes are still usable.
    assert verify_and_consume_recovery_code(user.user_id, codes[1]) is True

    # Direct DB confirmation: used_at populated on 2 rows.
    conn = get_connection()
    used = conn.execute(
        "SELECT COUNT(*) AS n FROM mfa_recovery_codes "
        "WHERE user_id = ? AND used_at IS NOT NULL",
        (user.user_id,),
    ).fetchone()
    assert used["n"] == 2


def test_verify_recovery_code_strips_dash_and_whitespace() -> None:
    """The user might type the dash, omit it, or paste with stray
    whitespace. Verify must normalize before hashing."""
    from auth.mfa import (
        generate_recovery_codes,
        verify_and_consume_recovery_code,
    )
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    codes = generate_recovery_codes(user.user_id)
    assert codes

    # Without dash.
    no_dash = codes[0].replace("-", "")
    assert verify_and_consume_recovery_code(user.user_id, no_dash) is True
    # With surrounding whitespace.
    spaced = f"  {codes[1]}  "
    assert verify_and_consume_recovery_code(user.user_id, spaced) is True
    # Lower-case input still matches.
    lowered = codes[2].lower()
    assert verify_and_consume_recovery_code(user.user_id, lowered) is True


def test_verify_recovery_code_rejects_wrong_code() -> None:
    """A code that does not match any unused row must return False."""
    from auth.mfa import (
        generate_recovery_codes,
        verify_and_consume_recovery_code,
    )
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    generate_recovery_codes(user.user_id)

    # 11-char string in the right shape but not a real code.
    assert verify_and_consume_recovery_code(
        user.user_id, "ZZZZZ-ZZZZZ"
    ) is False
    # Empty string.
    assert verify_and_consume_recovery_code(user.user_id, "") is False
    # Wrong length.
    assert verify_and_consume_recovery_code(user.user_id, "AB") is False


def test_count_unused_recovery_codes_reflects_consumption() -> None:
    """The count helper drives the "N unused codes remaining" UI
    caption. Must decrement on each successful consume."""
    from auth.mfa import (
        count_unused_recovery_codes,
        generate_recovery_codes,
        verify_and_consume_recovery_code,
    )
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    assert count_unused_recovery_codes(user.user_id) == 0

    codes = generate_recovery_codes(user.user_id)
    assert count_unused_recovery_codes(user.user_id) == 10

    verify_and_consume_recovery_code(user.user_id, codes[0])
    assert count_unused_recovery_codes(user.user_id) == 9

    verify_and_consume_recovery_code(user.user_id, codes[1])
    assert count_unused_recovery_codes(user.user_id) == 8


def test_regenerate_recovery_codes_wipes_old_batch() -> None:
    """regenerate_recovery_codes must wipe the previous batch and
    return a fresh one. Old codes must no longer verify."""
    from auth.mfa import (
        count_unused_recovery_codes,
        generate_recovery_codes,
        regenerate_recovery_codes,
        verify_and_consume_recovery_code,
    )
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    old_codes = generate_recovery_codes(user.user_id)
    assert len(old_codes) == 10

    new_codes = regenerate_recovery_codes(user.user_id)
    assert len(new_codes) == 10
    assert set(old_codes).isdisjoint(set(new_codes))

    # Count is exactly 10 (old batch wiped + new batch minted).
    assert count_unused_recovery_codes(user.user_id) == 10

    # Old codes must no longer verify.
    for old in old_codes:
        assert verify_and_consume_recovery_code(user.user_id, old) is False
    # New codes do verify.
    assert verify_and_consume_recovery_code(user.user_id, new_codes[0]) is True


def test_disable_mfa_wipes_recovery_codes() -> None:
    """Disabling MFA must wipe the recovery codes — leaving them behind
    would create an invisible back door past the "MFA off" state the
    user sees."""
    from auth.mfa import (
        count_unused_recovery_codes,
        disable_mfa,
        enable_mfa,
        generate_secret,
    )
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    ok, codes = enable_mfa(user.user_id, generate_secret())
    assert ok and codes
    assert count_unused_recovery_codes(user.user_id) == 10

    assert disable_mfa(user.user_id) is True
    assert count_unused_recovery_codes(user.user_id) == 0


def test_generate_recovery_codes_rejects_unknown_user() -> None:
    """An unknown user_id must return [] without inserting any rows —
    foreign-key integrity demands the user row exist first."""
    from auth.mfa import generate_recovery_codes
    from state.db import get_connection

    assert generate_recovery_codes("does-not-exist") == []
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM mfa_recovery_codes"
    ).fetchone()
    assert row["n"] == 0
