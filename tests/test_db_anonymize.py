"""Tests for ``tools.db_anonymize`` — SQLite DB anonymizer.

Defining properties under test:

* Empty DB → output is valid + empty (no crash, schema preserved).
* Output preserves the source's table count (no tables dropped).
* 'standard' profile: NO original user email/username survives.
* 'standard': api_tokens is empty after run.
* 'standard': delivery_channels.target is a stub, never an original URL.
* 'standard': alert structure (count + columns) is preserved.
* 'aggressive': annotation bodies are emptied (length-zero string).
* 'aggressive': every audit detail_json is '{}'.
* 'redact-only': strings are replaced with REDACTED, row counts preserved.
* Deterministic: same source → same output across two runs.
* Source DB is NEVER modified (mtime + sha256 unchanged before/after).
* dry_run=True does NOT write the output file.
* get_profile('unknown') raises ValueError.
* kv_state rows matching secret patterns are dropped (standard/aggressive).
* report_history file_paths are stubbed to a constant.

Tests use a hand-built source DB rather than going through state.db so
the test surface is independent of schema-version drift — each test
creates exactly the tables it needs and stops worrying about migrations.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Tuple

import pytest

from tools.db_anonymize import (
    _looks_like_secret_key,
    _stable_hash,
    anonymize_db,
    get_profile,
)


# ─── Fixture: a small representative source DB ────────────────────────────


def _seed_db(path: Path) -> None:
    """Create a minimal DB with the tables and a few rows touching every
    anonymization pass. The schema mirrors state/db.py columns the
    passes consult; we don't recreate every index/column because the
    anonymizer is column-list-driven, not index-driven."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE kv_state (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE alerts (
            alert_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            alert_type TEXT NOT NULL, severity TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL,
            ticker TEXT NOT NULL DEFAULT '', route_id TEXT NOT NULL DEFAULT '',
            port_locode TEXT NOT NULL DEFAULT '',
            value REAL NOT NULL DEFAULT 0.0, threshold REAL NOT NULL DEFAULT 0.0,
            change_pct REAL NOT NULL DEFAULT 0.0,
            acknowledged INTEGER NOT NULL DEFAULT 0,
            acknowledged_at TEXT NOT NULL DEFAULT '',
            acknowledged_note TEXT, acknowledged_by_user_id TEXT
        );
        CREATE TABLE alert_rules (
            rule_id TEXT PRIMARY KEY, data TEXT NOT NULL,
            cooldown_minutes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE report_history (
            report_id TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
            report_date TEXT NOT NULL DEFAULT '',
            sentiment_label TEXT NOT NULL DEFAULT '',
            sentiment_score REAL NOT NULL DEFAULT 0.0,
            risk_level TEXT NOT NULL DEFAULT '',
            signal_count INTEGER NOT NULL DEFAULT 0,
            data_quality TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL,
            file_size_kb REAL NOT NULL DEFAULT 0.0,
            public_password_hash TEXT, public_password_salt TEXT
        );
        CREATE TABLE delivery_channels (
            channel_id TEXT PRIMARY KEY, name TEXT NOT NULL,
            kind TEXT NOT NULL, target TEXT NOT NULL,
            severity_threshold TEXT NOT NULL DEFAULT 'LOW',
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, password_salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL,
            last_login_at TEXT NOT NULL DEFAULT '',
            mfa_secret TEXT NOT NULL DEFAULT '',
            mfa_enabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE api_tokens (
            token_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            label TEXT NOT NULL, token_hash TEXT NOT NULL,
            token_salt TEXT NOT NULL, token_prefix TEXT NOT NULL,
            created_at TEXT NOT NULL, last_used_at TEXT NOT NULL DEFAULT '',
            revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE audit_events (
            event_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT '', action TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id TEXT NOT NULL DEFAULT '',
            detail_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE alert_annotations (
            annotation_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL,
            user_id TEXT NOT NULL, author_user_id TEXT NOT NULL,
            body TEXT NOT NULL, created_at TEXT NOT NULL, edited_at TEXT
        );
        CREATE TABLE mfa_recovery_codes (
            code_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            code_hash TEXT NOT NULL, salt TEXT NOT NULL,
            used_at TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE user_invitations (
            invite_id TEXT PRIMARY KEY, invite_token TEXT NOT NULL UNIQUE,
            email TEXT, role TEXT NOT NULL DEFAULT 'user',
            invited_by_user_id TEXT NOT NULL, expires_at TEXT NOT NULL,
            consumed_at TEXT, consumed_by_user_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE user_settings (
            user_id TEXT PRIMARY KEY,
            settings_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        """
    )

    # Seed rows.
    conn.executemany(
        "INSERT INTO users (user_id, username, password_hash, password_salt, "
        "role, created_at, last_login_at, mfa_secret, mfa_enabled) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("u1", "alice@corp.com", "real_hash_aaa", "real_salt_aaa", "admin",
             "2026-01-01T00:00:00Z", "2026-05-22T10:00:00Z", "JBSWY3DPEHPK3PXP", 1),
            ("u2", "bob@corp.com", "real_hash_bbb", "real_salt_bbb", "user",
             "2026-02-01T00:00:00Z", "2026-05-21T09:00:00Z", "", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO api_tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("t1", "u1", "alice's laptop", "th1", "ts1", "pfx00001",
             "2026-03-01T00:00:00Z", "2026-05-22T08:00:00Z", 0),
            ("t2", "u2", "ci runner", "th2", "ts2", "pfx00002",
             "2026-03-02T00:00:00Z", "", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO delivery_channels VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("c1", "Eng on-call", "slack",
             "https://hooks.slack.com/services/T00/B00/REAL_TOKEN_XYZ",
             "HIGH", 1, "2026-01-15T00:00:00Z"),
            ("c2", "Pager", "pagerduty", "PAGERDUTY_REAL_KEY_ABC",
             "CRITICAL", 1, "2026-01-15T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO kv_state VALUES (?, ?, ?)",
        [
            ("schema_version", "25", "2026-05-22T00:00:00Z"),
            ("vault:anthropic_api", "sk-ant-realsecret", "2026-05-22T00:00:00Z"),
            ("user_42_token", "raw_secret_xyz", "2026-05-22T00:00:00Z"),
            ("feature_flag_x", "on", "2026-05-22T00:00:00Z"),
            ("smtp_password", "p4ssw0rd!", "2026-05-22T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO alerts (alert_id, created_at, alert_type, severity, "
        "title, body, acknowledged_note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("a1", "2026-05-20T00:00:00Z", "bdi_drop", "HIGH",
             "BDI dropped 12%", "Detection: BDI 7d return -12.4%", "looked at it"),
            ("a2", "2026-05-21T00:00:00Z", "freight_spike", "MEDIUM",
             "C5 spike", "Detection: C5 +5%", None),
        ],
    )
    conn.executemany(
        "INSERT INTO alert_annotations VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("an1", "a1", "u1", "u1", "trader X called about this",
             "2026-05-20T01:00:00Z", None),
            ("an2", "a1", "u1", "u2", "follow-up — see #incident-42",
             "2026-05-20T02:00:00Z", None),
        ],
    )
    conn.executemany(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", "2026-05-22T00:00:00Z", "u1", "login_success",
             "user", "u1", '{"ip": "10.0.0.5", "ua": "Chrome"}'),
            ("e2", "2026-05-22T01:00:00Z", "u1", "alert_ack",
             "alert", "a1", '{"alert_id": "a1"}'),
            ("e3", "2026-05-22T02:00:00Z", "u1", "token_create",
             "api_token", "t1", '{"label": "alice laptop"}'),
        ],
    )
    conn.executemany(
        "INSERT INTO report_history (report_id, generated_at, file_path, "
        "public_password_hash, public_password_salt) VALUES (?, ?, ?, ?, ?)",
        [
            ("r1", "2026-05-20T00:00:00Z",
             "/Users/alice/dev/Ship/cache/reports/r1.html",
             "pbkdf2_real_hash", "salt_real_hex"),
            ("r2", "2026-05-21T00:00:00Z",
             "/Users/alice/dev/Ship/cache/reports/r2.html", None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO mfa_recovery_codes VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("rc1", "u1", "h1", "s1", None, "2026-01-01T00:00:00Z"),
            ("rc2", "u1", "h2", "s2", None, "2026-01-01T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO user_invitations (invite_id, invite_token, email, role, "
        "invited_by_user_id, expires_at, consumed_at, consumed_by_user_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("i1", "tok_unconsumed_aaa", "charlie@corp.com", "user",
             "u1", "2026-06-01T00:00:00Z", None, None,
             "2026-05-15T00:00:00Z"),
            ("i2", "tok_consumed_bbb", "dave@corp.com", "user",
             "u1", "2026-06-01T00:00:00Z", "2026-05-18T00:00:00Z", "u3",
             "2026-05-15T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO user_settings VALUES (?, ?, ?)",
        [
            ("u1", '{"tz": "America/New_York", "watchlist": ["NVDA", "TSLA"]}',
             "2026-05-22T00:00:00Z"),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """A small but representative source DB at tmp_path."""
    db = tmp_path / "src.db"
    _seed_db(db)
    return db


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """Source DB with the schema but zero rows."""
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE kv_state (key TEXT PRIMARY KEY, value TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE TABLE users (user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL, last_login_at TEXT NOT NULL DEFAULT '',
            mfa_secret TEXT NOT NULL DEFAULT '',
            mfa_enabled INTEGER NOT NULL DEFAULT 0);
        """
    )
    conn.commit()
    conn.close()
    return db


def _file_sha256(p: Path) -> str:
    """Stable file hash for source-untouched assertions."""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _count_tables(p: Path) -> int:
    conn = sqlite3.connect(str(p))
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
        return cur.fetchone()[0]
    finally:
        conn.close()


def _rows(p: Path, sql: str) -> list:
    conn = sqlite3.connect(str(p))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ─── Tests ────────────────────────────────────────────────────────────────


def test_anonymize_empty_db_produces_valid_empty_output(empty_db, tmp_path):
    """Empty source → output exists, opens cleanly, has the same tables."""
    out = tmp_path / "out.db"
    anonymize_db(empty_db, out)
    assert out.exists()
    # Output is a valid SQLite file we can re-open.
    assert _count_tables(out) == _count_tables(empty_db)
    # No rows in either users or kv_state.
    assert _rows(out, "SELECT COUNT(*) FROM users")[0][0] == 0
    assert _rows(out, "SELECT COUNT(*) FROM kv_state")[0][0] == 0


def test_anonymize_preserves_table_count(seeded_db, tmp_path):
    """No table is dropped — only rows + columns are mutated."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out)
    assert _count_tables(out) == _count_tables(seeded_db)


def test_standard_scrubs_user_emails_completely(seeded_db, tmp_path):
    """No original username survives in the output."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="standard")
    rows = _rows(out, "SELECT username, password_hash, mfa_secret, mfa_enabled FROM users")
    usernames = {r[0] for r in rows}
    assert "alice@corp.com" not in usernames
    assert "bob@corp.com" not in usernames
    # Every replacement uses the example.com domain.
    for u in usernames:
        assert u.endswith("@example.com")
    # Password hashes are scrubbed; MFA disabled.
    for _u, ph, mfas, mfae in rows:
        assert ph != "real_hash_aaa"
        assert ph != "real_hash_bbb"
        assert mfas == ""
        assert mfae == 0


def test_standard_empties_api_tokens(seeded_db, tmp_path):
    """api_tokens is wiped under the standard profile."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="standard")
    assert _rows(out, "SELECT COUNT(*) FROM api_tokens")[0][0] == 0


def test_standard_replaces_delivery_channel_targets(seeded_db, tmp_path):
    """No real webhook URL or PagerDuty key survives."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="standard")
    targets = [r[0] for r in _rows(out, "SELECT target FROM delivery_channels")]
    for t in targets:
        assert "REAL_TOKEN_XYZ" not in t
        assert "PAGERDUTY_REAL_KEY_ABC" not in t
    # Each row got a stub of some kind.
    assert all(t for t in targets)


def test_standard_keeps_alert_structure(seeded_db, tmp_path):
    """Alert count + columns preserved — alert.body is NOT redacted
    under the standard profile."""
    out = tmp_path / "out.db"
    src_n = _rows(seeded_db, "SELECT COUNT(*) FROM alerts")[0][0]
    anonymize_db(seeded_db, out, profile="standard")
    assert _rows(out, "SELECT COUNT(*) FROM alerts")[0][0] == src_n
    bodies = [r[0] for r in _rows(out, "SELECT body FROM alerts")]
    # Standard leaves alert.body alone.
    assert any("Detection:" in b for b in bodies)


def test_aggressive_empties_annotation_bodies(seeded_db, tmp_path):
    """Aggressive blanks every annotation body to empty string."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="aggressive")
    bodies = [r[0] for r in _rows(out, "SELECT body FROM alert_annotations")]
    assert bodies  # rows preserved
    assert all(b == "" for b in bodies)


def test_aggressive_empties_audit_detail_json(seeded_db, tmp_path):
    """Aggressive zeros EVERY audit detail_json — not just sensitive ones."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="aggressive")
    payloads = [r[0] for r in _rows(out, "SELECT detail_json FROM audit_events")]
    assert payloads
    assert all(p == "{}" for p in payloads)


def test_redact_only_preserves_counts_but_replaces_strings(seeded_db, tmp_path):
    """Every table keeps its row count; strings are replaced with
    REDACTED markers."""
    out = tmp_path / "out.db"
    src_api = _rows(seeded_db, "SELECT COUNT(*) FROM api_tokens")[0][0]
    src_kv = _rows(seeded_db, "SELECT COUNT(*) FROM kv_state")[0][0]
    src_inv = _rows(seeded_db, "SELECT COUNT(*) FROM user_invitations")[0][0]
    src_mfa = _rows(seeded_db, "SELECT COUNT(*) FROM mfa_recovery_codes")[0][0]
    anonymize_db(seeded_db, out, profile="redact-only")
    assert _rows(out, "SELECT COUNT(*) FROM api_tokens")[0][0] == src_api
    assert _rows(out, "SELECT COUNT(*) FROM kv_state")[0][0] == src_kv
    assert _rows(out, "SELECT COUNT(*) FROM user_invitations")[0][0] == src_inv
    assert _rows(out, "SELECT COUNT(*) FROM mfa_recovery_codes")[0][0] == src_mfa
    # Spot-check: api_token.label is now "REDACTED".
    labels = [r[0] for r in _rows(out, "SELECT label FROM api_tokens")]
    assert all(l == "REDACTED" for l in labels)


def test_anonymize_is_deterministic(seeded_db, tmp_path):
    """Two runs against the same source produce the same user mapping +
    the same row-level outputs. SQLite file metadata differs across runs
    (timestamp pages) so we compare CONTENT, not file bytes."""
    out_a = tmp_path / "a.db"
    out_b = tmp_path / "b.db"
    anonymize_db(seeded_db, out_a, profile="standard")
    anonymize_db(seeded_db, out_b, profile="standard")

    # User mapping must match exactly.
    users_a = sorted(
        _rows(out_a, "SELECT user_id, username, password_hash FROM users ORDER BY user_id")
    )
    users_b = sorted(
        _rows(out_b, "SELECT user_id, username, password_hash FROM users ORDER BY user_id")
    )
    assert users_a == users_b

    # Annotation bodies (length-suffixed) must match.
    ann_a = _rows(out_a, "SELECT annotation_id, body FROM alert_annotations ORDER BY annotation_id")
    ann_b = _rows(out_b, "SELECT annotation_id, body FROM alert_annotations ORDER BY annotation_id")
    assert ann_a == ann_b


def test_source_db_is_never_modified(seeded_db, tmp_path):
    """sha256 of the source file is identical before + after a wet run."""
    before = _file_sha256(seeded_db)
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="aggressive", verbose=True)
    after = _file_sha256(seeded_db)
    assert before == after


def test_dry_run_does_not_write_output_file(seeded_db, tmp_path):
    """dry_run=True returns counts but never creates the output."""
    out = tmp_path / "should_not_exist.db"
    result = anonymize_db(seeded_db, out, dry_run=True)
    assert not out.exists()
    assert "scrubbed" in result
    assert "dropped" in result
    # Dry-run still reports what WOULD be scrubbed/dropped.
    assert result["scrubbed"].get("users", 0) >= 1 or result["dropped"]


def test_get_profile_unknown_raises_value_error():
    """Unknown profile name fails fast; no silent fallback."""
    with pytest.raises(ValueError):
        get_profile("nonexistent")


def test_kv_state_secret_rows_dropped(seeded_db, tmp_path):
    """vault:*, *secret*, *token*, *key*, *password* rows go away."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="standard")
    keys = {r[0] for r in _rows(out, "SELECT key FROM kv_state")}
    # The benign rows survive.
    assert "schema_version" in keys
    assert "feature_flag_x" in keys
    # The secret-pattern rows are gone.
    assert "vault:anthropic_api" not in keys
    assert "user_42_token" not in keys
    assert "smtp_password" not in keys


def test_report_history_file_paths_stubbed(seeded_db, tmp_path):
    """Every report.file_path is replaced by the constant stub; the
    original fs layout never leaks. public_password_hash is wiped to
    NULL so the share-link cannot be brute-forced offline."""
    out = tmp_path / "out.db"
    anonymize_db(seeded_db, out, profile="standard")
    rows = _rows(out, "SELECT file_path, public_password_hash, public_password_salt FROM report_history")
    assert rows
    for fp, pph, pps in rows:
        assert fp == "cache/reports/REDACTED.html"
        assert pph is None
        assert pps is None


# ─── Extra unit tests for internals ────────────────────────────────────────


def test_stable_hash_is_deterministic():
    """Same input → same 8-hex output across calls."""
    assert _stable_hash("alice@corp.com") == _stable_hash("alice@corp.com")
    assert _stable_hash("alice@corp.com") != _stable_hash("bob@corp.com")
    assert len(_stable_hash("anything")) == 8


def test_looks_like_secret_key_matches_documented_patterns():
    """The matcher catches vault:, secret, token, key, password,
    credential — but not benign keys like feature_flag_x."""
    assert _looks_like_secret_key("vault:anthropic_api")
    assert _looks_like_secret_key("USER_42_TOKEN")  # case-insensitive
    assert _looks_like_secret_key("smtp_password")
    assert _looks_like_secret_key("private_key")
    assert _looks_like_secret_key("aws_credential_id")
    # Negative cases.
    assert not _looks_like_secret_key("schema_version")
    assert not _looks_like_secret_key("feature_flag_x")
    assert not _looks_like_secret_key("")
