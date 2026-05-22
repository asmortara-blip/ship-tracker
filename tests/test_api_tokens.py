"""Tests for ``auth.tokens`` — the v11 per-user API access token layer.

Exercises the dataclass shape (no hash/salt fields), the five public
helpers (``create_token`` / ``verify_token`` / ``list_tokens`` /
``revoke_token`` / ``record_token_use``), and the audit-log hook
integration. Also verifies the negative-space properties: the raw
secret never appears in the DB, the hash never appears in the
audit detail, revoked tokens fail to verify, and cross-user
revocation is rejected.

The fixture (autouse) routes ``state.db.DB_PATH`` to a per-test
tmp_path so no test touches the real ``cache/ship_tracker.db``.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── ApiToken dataclass shape ─────────────────────────────────────────────

def test_apitoken_dataclass_fields_exclude_hash_and_salt() -> None:
    """The dataclass exposes the seven public-facing fields. The hash
    and salt MUST NOT be reachable from an ApiToken instance — that is
    the defining property of the type."""
    from auth.tokens import ApiToken

    fields = set(ApiToken.__dataclass_fields__)
    assert fields == {
        "token_id", "user_id", "label", "token_prefix",
        "created_at", "last_used_at", "revoked",
    }
    # Neither "token_hash" nor "token_salt" may be a field on the
    # dataclass — the credential material stays in the DB.
    assert "token_hash" not in fields
    assert "token_salt" not in fields


def test_apitoken_revoked_is_bool() -> None:
    """The revoked flag is exposed as bool, not int — the dataclass is
    the public type and should not leak the SQLite-side INTEGER."""
    from auth.tokens import ApiToken

    tok = ApiToken(
        token_id="t1",
        user_id="u1",
        label="ci",
        token_prefix="abcd1234",
        created_at="2026-05-22T00:00:00+00:00",
        last_used_at="",
        revoked=False,
    )
    assert isinstance(tok.revoked, bool)


# ─── create_token: happy path ────────────────────────────────────────────

def test_create_token_returns_metadata_and_raw_string() -> None:
    """The function returns (ApiToken, raw_token). The raw token is a
    URL-safe base64 string of at least 32 chars."""
    from auth.tokens import ApiToken, create_token

    result = create_token("u-1", "ci-bot")
    assert result is not None
    meta, raw = result
    assert isinstance(meta, ApiToken)
    assert isinstance(raw, str)
    # secrets.token_urlsafe(32) → 43 chars; we accept any reasonable
    # floor as a defining property so the bound is not brittle if the
    # length policy changes.
    assert len(raw) >= 32
    # URL-safe base64 alphabet only.
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789-_"
    )
    assert set(raw).issubset(allowed)


def test_create_token_metadata_carries_label_and_prefix() -> None:
    """The ApiToken returned to the caller carries the supplied label
    and a token_prefix matching the first 8 chars of the raw secret."""
    from auth.tokens import create_token

    meta, raw = create_token("u-1", "my laptop")
    assert meta.label == "my laptop"
    assert meta.user_id == "u-1"
    assert meta.revoked is False
    assert meta.last_used_at == ""
    assert meta.token_prefix == raw[:8]
    assert len(meta.token_prefix) == 8


def test_create_token_persists_hash_not_raw_secret() -> None:
    """The DB row stores the hash + salt + 8-char prefix. The raw
    secret MUST NOT appear in the row anywhere."""
    from auth.tokens import create_token
    from state.db import get_connection

    meta, raw = create_token("u-1", "ci")
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM api_tokens WHERE token_id = ?",
        (meta.token_id,),
    ).fetchone()
    assert row is not None

    # The token_hash column has SOMETHING (the hex hash).
    assert row["token_hash"]
    assert row["token_salt"]
    # The token_prefix column matches the first 8 chars of the raw
    # secret — exactly the indexed-lookup contract.
    assert row["token_prefix"] == raw[:8]
    # The raw secret string MUST NOT be present in any column.
    for col in row.keys():
        val = row[col]
        if isinstance(val, str):
            assert raw not in val, (
                f"raw token leaked into column {col!r}"
            )


def test_create_token_rejects_empty_user_id() -> None:
    """An empty / non-string user_id is rejected with None — the
    function never raises."""
    from auth.tokens import create_token

    assert create_token("", "label") is None
    assert create_token(None, "label") is None  # type: ignore[arg-type]


def test_create_token_rejects_empty_label() -> None:
    """An empty / non-string label is rejected with None — the user
    needs to be able to tell their tokens apart."""
    from auth.tokens import create_token

    assert create_token("u-1", "") is None
    assert create_token("u-1", None) is None  # type: ignore[arg-type]


def test_create_token_distinct_secrets_each_call() -> None:
    """Two back-to-back creates produce different raw secrets — the
    randomness source is per-call, not module-level."""
    from auth.tokens import create_token

    _, raw_a = create_token("u-1", "ci")
    _, raw_b = create_token("u-1", "ci")
    assert raw_a != raw_b


def test_create_token_never_raises_on_db_failure() -> None:
    """Mock get_connection to throw; create_token must return None
    instead of bubbling up."""
    from auth import tokens

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("disk full")
        assert tokens.create_token("u-1", "ci") is None


# ─── verify_token: happy path + failures ──────────────────────────────────

def test_verify_token_returns_user_id_for_valid_secret() -> None:
    """A freshly-minted raw secret verifies and returns its owner."""
    from auth.tokens import create_token, verify_token

    _, raw = create_token("u-42", "ci")
    assert verify_token(raw) == "u-42"


def test_verify_token_rejects_tampered_secret() -> None:
    """Flipping a single character in the raw secret breaks verify."""
    from auth.tokens import create_token, verify_token

    _, raw = create_token("u-1", "ci")
    # Swap the final character for something definitely-different so
    # we have a tampered string with the same prefix (forcing the
    # hash compare rather than a prefix miss).
    if raw[-1] == "A":
        tampered = raw[:-1] + "B"
    else:
        tampered = raw[:-1] + "A"
    assert verify_token(tampered) is None


def test_verify_token_returns_none_for_unknown_prefix() -> None:
    """A raw secret whose prefix is not in the index returns None
    without doing any hash compare."""
    from auth.tokens import verify_token

    # "zzzzzzzz..." has the right shape (URL-safe base64, 43 chars)
    # but no row exists.
    assert verify_token("z" * 43) is None


def test_verify_token_rejects_too_short_input() -> None:
    """Anything shorter than the prefix length is rejected without
    touching the DB."""
    from auth.tokens import verify_token

    assert verify_token("abc") is None
    assert verify_token("") is None
    assert verify_token(None) is None  # type: ignore[arg-type]


def test_verify_token_updates_last_used_at() -> None:
    """A successful verify stamps last_used_at on the row."""
    from auth.tokens import create_token, verify_token
    from state.db import get_connection

    meta, raw = create_token("u-1", "ci")
    # Pre-verify: last_used_at is empty.
    conn = get_connection()
    before = conn.execute(
        "SELECT last_used_at FROM api_tokens WHERE token_id = ?",
        (meta.token_id,),
    ).fetchone()
    assert before["last_used_at"] == ""

    assert verify_token(raw) == "u-1"

    after = conn.execute(
        "SELECT last_used_at FROM api_tokens WHERE token_id = ?",
        (meta.token_id,),
    ).fetchone()
    assert after["last_used_at"] != ""
    # ISO-8601 UTC timestamp — at minimum it should have a "T" separator.
    assert "T" in after["last_used_at"]


def test_verify_token_returns_none_for_revoked_token() -> None:
    """A revoked token must NOT verify even when the secret matches."""
    from auth.tokens import create_token, revoke_token, verify_token

    meta, raw = create_token("u-1", "ci")
    # Sanity: verifies before revocation.
    assert verify_token(raw) == "u-1"
    assert revoke_token(meta.token_id, user_id="u-1") is True
    # After revocation: the SAME raw secret no longer authenticates.
    assert verify_token(raw) is None


def test_verify_token_never_raises_on_db_failure() -> None:
    """Mock get_connection to throw; verify_token must return None
    instead of bubbling up."""
    from auth import tokens

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("disk on fire")
        assert tokens.verify_token("a" * 43) is None


# ─── list_tokens ──────────────────────────────────────────────────────────

def test_list_tokens_returns_newest_first() -> None:
    """The list is ordered by created_at DESC so the UI can render
    the most-recent token at the top without re-sorting."""
    from auth.tokens import create_token, list_tokens
    from state.db import get_connection

    a, _ = create_token("u-1", "first")
    b, _ = create_token("u-1", "second")
    c, _ = create_token("u-1", "third")

    # Manually pin created_at values so the sort is deterministic
    # (the three calls above happen in microseconds — the ordering
    # works in practice but we tighten it here for the test).
    conn = get_connection()
    conn.execute(
        "UPDATE api_tokens SET created_at = ? WHERE token_id = ?",
        ("2026-01-01T00:00:00+00:00", a.token_id),
    )
    conn.execute(
        "UPDATE api_tokens SET created_at = ? WHERE token_id = ?",
        ("2026-03-15T00:00:00+00:00", b.token_id),
    )
    conn.execute(
        "UPDATE api_tokens SET created_at = ? WHERE token_id = ?",
        ("2026-05-22T00:00:00+00:00", c.token_id),
    )

    tokens = list_tokens("u-1")
    labels = [t.label for t in tokens]
    assert labels == ["third", "second", "first"]


def test_list_tokens_excludes_hash_and_salt() -> None:
    """The dataclass returned to the caller MUST NOT expose hash/salt.
    Confirmed by dataclass-field introspection on each row."""
    from auth.tokens import create_token, list_tokens

    create_token("u-1", "label")
    tokens = list_tokens("u-1")
    assert len(tokens) == 1
    fields = set(tokens[0].__dataclass_fields__)
    assert "token_hash" not in fields
    assert "token_salt" not in fields


def test_list_tokens_scopes_to_owner() -> None:
    """User A cannot see user B's tokens."""
    from auth.tokens import create_token, list_tokens

    create_token("alice", "alice-ci")
    create_token("bob", "bob-ci")
    create_token("bob", "bob-laptop")

    alice = list_tokens("alice")
    bob = list_tokens("bob")
    assert {t.label for t in alice} == {"alice-ci"}
    assert {t.label for t in bob} == {"bob-ci", "bob-laptop"}


def test_list_tokens_empty_for_unknown_user() -> None:
    """An unknown user returns ``[]``, not None — the contract is
    list-shaped."""
    from auth.tokens import list_tokens

    assert list_tokens("nobody") == []


def test_list_tokens_returns_empty_for_missing_user_id() -> None:
    """Empty / non-string user_id returns ``[]`` instead of querying."""
    from auth.tokens import list_tokens

    assert list_tokens("") == []
    assert list_tokens(None) == []  # type: ignore[arg-type]


def test_list_tokens_never_raises_on_db_failure() -> None:
    """Mock get_connection to throw; list_tokens must return ``[]``."""
    from auth import tokens

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("offline")
        assert tokens.list_tokens("u-1") == []


# ─── revoke_token ─────────────────────────────────────────────────────────

def test_revoke_token_returns_true_and_flips_flag() -> None:
    """A successful revoke flips the revoked column to 1 and reports True."""
    from auth.tokens import create_token, revoke_token
    from state.db import get_connection

    meta, _ = create_token("u-1", "ci")
    assert revoke_token(meta.token_id, user_id="u-1") is True

    conn = get_connection()
    row = conn.execute(
        "SELECT revoked FROM api_tokens WHERE token_id = ?",
        (meta.token_id,),
    ).fetchone()
    assert row["revoked"] == 1


def test_revoke_token_rejects_cross_user_revocation() -> None:
    """Bob cannot revoke Alice's token — the SQL WHERE clause enforces
    ownership in one query rather than splitting into a check + update."""
    from auth.tokens import create_token, revoke_token, verify_token

    alice_meta, alice_raw = create_token("alice", "ci")
    # Bob tries to revoke Alice's token.
    assert revoke_token(alice_meta.token_id, user_id="bob") is False
    # The token must STILL verify because the revoke was rejected.
    assert verify_token(alice_raw) == "alice"


def test_revoke_token_returns_false_for_unknown_id() -> None:
    """Trying to revoke a token that does not exist is a no-op,
    not a raise."""
    from auth.tokens import revoke_token

    assert revoke_token("does-not-exist", user_id="u-1") is False


def test_revoke_token_returns_false_for_already_revoked() -> None:
    """Revoking the same token twice returns False on the second
    call — the row no longer matches the ``revoked = 0`` clause."""
    from auth.tokens import create_token, revoke_token

    meta, _ = create_token("u-1", "ci")
    assert revoke_token(meta.token_id, user_id="u-1") is True
    assert revoke_token(meta.token_id, user_id="u-1") is False


def test_revoke_token_rejects_empty_inputs() -> None:
    """Empty / non-string args return False without touching the DB."""
    from auth.tokens import revoke_token

    assert revoke_token("", user_id="u-1") is False
    assert revoke_token("tok-1", user_id="") is False


def test_revoke_token_never_raises_on_db_failure() -> None:
    """Mock get_connection to throw; revoke_token must return False."""
    from auth import tokens

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("offline")
        assert tokens.revoke_token("tok-1", user_id="u-1") is False


# ─── record_token_use ─────────────────────────────────────────────────────

def test_record_token_use_updates_last_used_at() -> None:
    """Calling record_token_use directly stamps last_used_at without
    going through verify_token."""
    from auth.tokens import create_token, record_token_use
    from state.db import get_connection

    meta, _ = create_token("u-1", "ci")
    conn = get_connection()
    before = conn.execute(
        "SELECT last_used_at FROM api_tokens WHERE token_id = ?",
        (meta.token_id,),
    ).fetchone()
    assert before["last_used_at"] == ""

    record_token_use(meta.token_id)

    after = conn.execute(
        "SELECT last_used_at FROM api_tokens WHERE token_id = ?",
        (meta.token_id,),
    ).fetchone()
    assert after["last_used_at"] != ""


def test_record_token_use_never_raises_on_db_failure() -> None:
    """Mock get_connection to throw; record_token_use must return
    None silently rather than propagating."""
    from auth import tokens

    with patch("state.db.get_connection") as mock_conn:
        mock_conn.side_effect = RuntimeError("offline")
        # Must NOT raise.
        result = tokens.record_token_use("tok-1")
    assert result is None


def test_record_token_use_ignores_empty_id() -> None:
    """An empty token_id is rejected without touching the DB; no raise."""
    from auth.tokens import record_token_use

    # Must NOT raise.
    record_token_use("")
    record_token_use(None)  # type: ignore[arg-type]


# ─── Audit-log hook integration ───────────────────────────────────────────

def test_create_token_writes_audit_event() -> None:
    """create_token emits a ``create_api_token`` audit event tagged
    to the owning user."""
    from auth.audit import query_audit
    from auth.tokens import create_token

    meta, _ = create_token("u-77", "ci-bot")
    rows = query_audit(action="create_api_token")
    assert len(rows) == 1
    assert rows[0].entity_type == "api_token"
    assert rows[0].entity_id == meta.token_id
    assert rows[0].user_id == "u-77"
    assert rows[0].detail_json["label"] == "ci-bot"


def test_create_token_audit_does_not_leak_raw_secret_or_hash() -> None:
    """The audit detail payload MUST NOT carry the raw token OR the
    hash — only the label and the (already-indexed) prefix."""
    from auth.audit import query_audit
    from auth.tokens import create_token
    from state.db import get_connection

    meta, raw = create_token("u-1", "label")
    rows = query_audit(action="create_api_token")
    assert len(rows) == 1
    detail_str = json.dumps(rows[0].detail_json)
    assert raw not in detail_str

    # And confirm the hash is not in the detail either.
    conn = get_connection()
    row = conn.execute(
        "SELECT token_hash FROM api_tokens WHERE token_id = ?",
        (meta.token_id,),
    ).fetchone()
    assert row["token_hash"] not in detail_str


def test_revoke_token_writes_audit_event() -> None:
    """revoke_token emits a ``revoke_api_token`` audit event when the
    revoke actually fires."""
    from auth.audit import query_audit
    from auth.tokens import create_token, revoke_token

    meta, _ = create_token("u-1", "ci")
    assert revoke_token(meta.token_id, user_id="u-1") is True
    rows = query_audit(action="revoke_api_token")
    assert len(rows) == 1
    assert rows[0].entity_id == meta.token_id
    assert rows[0].user_id == "u-1"


def test_revoke_token_cross_user_does_not_write_audit_event() -> None:
    """A rejected cross-user revoke MUST NOT generate an audit event —
    the action did not actually occur. (If we logged rejections, an
    attacker could probe a victim's token_ids by checking the log.)"""
    from auth.audit import query_audit
    from auth.tokens import create_token, revoke_token

    meta, _ = create_token("alice", "ci")
    assert revoke_token(meta.token_id, user_id="bob") is False
    rows = query_audit(action="revoke_api_token")
    assert rows == []
