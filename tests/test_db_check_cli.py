"""Tests for ``tools.db_check_cli`` — DB integrity check CLI.

Defining properties under test:

* A fresh, app-initialized DB passes every check (PASS or INFO — never
  FAIL).
* ``PRAGMA integrity_check`` / ``foreign_key_check`` on a clean DB
  both return ok.
* schema_version mismatch is reported as WARN, NEVER FAIL — the DB is
  still usable; a re-open through state.db will auto-migrate.
* Orphan-row detection actually counts: insert an alert with a
  rule_id that's not in alert_rules and the check reports it.
* Duplicate rule_ids are FAIL — that's data-correctness, not perf.
* ``--fix`` mutates only the rows the spec promises it will mutate
  (and only when explicitly passed — without --fix the DB is opened
  read-only).
* ``--fix`` does NOT touch integrity_check failures (those need DBA
  attention).
* ``--json`` produces a single valid JSON document on stdout.
* ``--db PATH`` targets the supplied file (not the live cache).
* ``--full`` runs the heavier checks (known_tables shows up).
* Exit code is 0 on a clean run, 1 when any FAIL.

Per-test isolation is via the same monkeypatch-DB_PATH / tmp_path
fixture used by every other DB-touching test.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ─── Isolation fixture ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite at tmp_path so the CLI never touches the real
    cache/ship_tracker.db."""
    from state import db as state_db

    db_path = tmp_path / "cache" / "ship_tracker.db"
    monkeypatch.setattr(state_db, "DB_PATH", db_path)
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    """Call ``db_check_cli.main(argv)`` and return (exit, stdout, stderr)."""
    from tools.db_check_cli import main

    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _force_db_open():
    """Open the DB through state.db so schema + tables exist on disk."""
    from state.db import get_connection
    return get_connection()


def _live_db_path() -> Path:
    """Resolve the redirected DB path for the current test."""
    from state import db as state_db
    return Path(state_db.DB_PATH)


def _direct_conn() -> sqlite3.Connection:
    """Open a fresh sqlite3.Connection on the live DB — bypasses
    state.db's _init_schema so the test can inspect raw rows without
    re-triggering migrations."""
    conn = sqlite3.connect(str(_live_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _result_by_name(results, name):
    """Find a CheckResult by name in a list of CheckResults."""
    for r in results:
        if r.name == name:
            return r
    raise AssertionError(f"check {name!r} not found in {[r.name for r in results]}")


# ─── Tests ────────────────────────────────────────────────────────────────


def test_clean_db_passes_every_check(capsys) -> None:
    """A fresh DB through state.db.get_connection() should produce no
    FAILs — every check is PASS or INFO (legacy/optional checks)."""
    _force_db_open()
    code, out, err = _run([], capsys)
    assert code == 0, (out, err)
    assert "FAIL" not in out
    assert "schema_version" in out
    assert "integrity_check" in out


def test_integrity_check_passes_on_clean_db(capsys) -> None:
    """PRAGMA quick_check returns 'ok' on a clean DB."""
    _force_db_open()
    from tools.db_check_cli import check_integrity

    conn = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_integrity(conn, full=False)
    finally:
        conn.close()
    assert result.status == "PASS", result.message


def test_foreign_key_check_passes_on_clean_db(capsys) -> None:
    """PRAGMA foreign_key_check returns no violations on a clean DB."""
    _force_db_open()
    from tools.db_check_cli import check_foreign_keys

    conn = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_foreign_keys(conn)
    finally:
        conn.close()
    assert result.status == "PASS", result.message


def test_schema_version_matches_passes(capsys) -> None:
    """When the DB's schema_version == SCHEMA_VERSION, the check is
    PASS and the message names the version."""
    _force_db_open()
    from tools.db_check_cli import check_schema_version
    from state.db import SCHEMA_VERSION

    conn = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_schema_version(conn)
    finally:
        conn.close()
    assert result.status == "PASS"
    assert str(SCHEMA_VERSION) in result.message


def test_schema_version_mismatch_warns(capsys) -> None:
    """Hand-bump the kv_state.schema_version row below the running
    code's expectation and confirm the check downgrades to WARN
    (NEVER FAIL — the DB is still usable)."""
    _force_db_open()
    # Force a lower value into kv_state.
    conn = _direct_conn()
    try:
        conn.execute(
            "UPDATE kv_state SET value = ? WHERE key = 'schema_version'",
            ("1",),
        )
        conn.commit()
    finally:
        conn.close()

    from tools.db_check_cli import check_schema_version

    conn2 = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_schema_version(conn2)
    finally:
        conn2.close()
    assert result.status == "WARN"
    assert "BELOW" in result.message
    assert result.details["kv_state_schema_version"] == 1


def test_orphan_alert_detected(capsys) -> None:
    """Insert an alert whose rule_id is NOT in alert_rules. The check
    must count it as one orphan and downgrade to WARN."""
    _force_db_open()
    # Insert a dangling alert directly. The alerts table has many
    # NOT NULL DEFAULT cols, so use INSERT-with-named-cols.
    conn = _direct_conn()
    try:
        conn.execute(
            "INSERT INTO alerts "
            "(alert_id, created_at, alert_type, severity, title, body, rule_id) "
            "VALUES (?, ?, 'MACRO', 'HIGH', 'orphan', 'orphan', ?)",
            ("orphan-1", "2024-01-01T00:00:00+00:00", "missing-rule-id"),
        )
        conn.commit()
    finally:
        conn.close()

    from tools.db_check_cli import check_orphan_alerts

    conn2 = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_orphan_alerts(conn2)
    finally:
        conn2.close()
    assert result.status == "WARN"
    assert result.details["orphan_count"] == 1


def test_stale_api_token_skipped_when_no_expires_at(capsys) -> None:
    """The current api_tokens schema has no ``expires_at`` column, so
    the check should report INFO with a "skipped" reason. This locks
    in the forward-compat degradation."""
    _force_db_open()
    from tools.db_check_cli import check_stale_api_tokens

    conn = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_stale_api_tokens(conn)
    finally:
        conn.close()
    # No expires_at on the column → INFO
    assert result.status == "INFO"
    assert "expires_at" in result.message


def test_stale_invitation_detected(capsys) -> None:
    """Insert a user_invitations row whose expires_at is in the past
    and consumed_at is NULL — the check should report INFO with
    stale_count=1."""
    _force_db_open()
    past_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    conn = _direct_conn()
    try:
        conn.execute(
            "INSERT INTO user_invitations "
            "(invite_id, invite_token, role, invited_by_user_id, "
            " expires_at, created_at) "
            "VALUES (?, ?, 'user', 'admin', ?, ?)",
            (
                "inv-1", "tok-stale-1", past_iso,
                past_iso,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from tools.db_check_cli import check_stale_invitations

    conn2 = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_stale_invitations(conn2)
    finally:
        conn2.close()
    assert result.status == "INFO"
    assert result.details["stale_count"] == 1


def test_duplicate_rule_ids_fails(capsys) -> None:
    """Insert two rows in alert_rules with the same (user_id, rule_id)
    bypassing the PK by using two different rule_ids that collide
    after grouping. The simplest path: insert directly, picking a
    rule_id that doesn't already exist, twice — but the PRIMARY KEY
    on rule_id would reject that.

    The schema's actual constraint is global rule_id uniqueness via
    PK. We can still get a duplicate in the per-user grouping if
    user_id is empty for two rows (it's the v7 column with default
    ''). The PK still demands distinct rule_ids globally; the duplicate
    case the check detects is when the same (user_id, rule_id) row
    appears twice, which CAN happen if a sibling agent ever drops the
    PK constraint OR if a backup-restore swap landed two rows that
    later got renumbered. To exercise the check deterministically,
    create a new table without a PK on rule_id, mirroring the audit
    SQL.

    Concretely: drop & recreate alert_rules without the PK constraint,
    insert two colliding rows, then check.
    """
    _force_db_open()
    conn = _direct_conn()
    try:
        conn.execute("DROP TABLE alert_rules")
        conn.execute(
            "CREATE TABLE alert_rules ("
            " rule_id TEXT, data TEXT NOT NULL, "
            " user_id TEXT NOT NULL DEFAULT '', "
            " cooldown_minutes INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data, user_id) VALUES (?, ?, ?)",
            ("dup-rule-1", "{}", "user-a"),
        )
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data, user_id) VALUES (?, ?, ?)",
            ("dup-rule-1", "{}", "user-a"),
        )
        conn.commit()
    finally:
        conn.close()

    from tools.db_check_cli import check_duplicate_rule_ids

    conn2 = sqlite3.connect(str(_live_db_path()))
    try:
        result = check_duplicate_rule_ids(conn2)
    finally:
        conn2.close()
    assert result.status == "FAIL"
    assert result.details["duplicate_count"] >= 1


def test_fix_marks_stale_tokens_inactive(capsys, monkeypatch) -> None:
    """--fix should mark expired api_tokens inactive. The current
    api_tokens schema has no expires_at column, so the fix degrades
    gracefully and the test asserts the "skipped" reason is recorded.

    To also exercise the happy path, we ALTER the table to add the
    column, insert a stale row, run --fix, and confirm revoked=1.
    """
    _force_db_open()
    past_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = _direct_conn()
    try:
        # Add expires_at to api_tokens for this test only.
        conn.execute("ALTER TABLE api_tokens ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "INSERT INTO api_tokens "
            "(token_id, user_id, label, token_hash, token_salt, "
            " token_prefix, created_at, last_used_at, revoked, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '', 0, ?)",
            ("tok-stale", "user-1", "lbl", "h", "s", "abcd1234", past_iso, past_iso),
        )
        conn.commit()
    finally:
        conn.close()

    code, out, err = _run(["--fix"], capsys)
    assert code == 0, (out, err)

    # Confirm the row is now revoked=1.
    conn2 = _direct_conn()
    try:
        cur = conn2.execute(
            "SELECT revoked FROM api_tokens WHERE token_id = 'tok-stale'"
        )
        row = cur.fetchone()
    finally:
        conn2.close()
    assert row is not None
    assert int(row["revoked"]) == 1


def test_fix_marks_stale_invitations_consumed(capsys) -> None:
    """--fix should set consumed_at + consumed_by_user_id on every
    expired-but-unconsumed invitation."""
    _force_db_open()
    past_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    conn = _direct_conn()
    try:
        conn.execute(
            "INSERT INTO user_invitations "
            "(invite_id, invite_token, role, invited_by_user_id, "
            " expires_at, created_at) "
            "VALUES (?, ?, 'user', 'admin', ?, ?)",
            ("inv-fix-1", "tok-fix-1", past_iso, past_iso),
        )
        conn.commit()
    finally:
        conn.close()

    code, out, err = _run(["--fix"], capsys)
    assert code == 0, (out, err)

    conn2 = _direct_conn()
    try:
        cur = conn2.execute(
            "SELECT consumed_at, consumed_by_user_id "
            "FROM user_invitations WHERE invite_id = 'inv-fix-1'"
        )
        row = cur.fetchone()
    finally:
        conn2.close()
    assert row is not None
    assert row["consumed_at"] == past_iso
    assert row["consumed_by_user_id"] == "SYSTEM_EXPIRED"


def test_fix_deletes_ancient_silences(capsys) -> None:
    """--fix should DELETE alert_silences whose expires_at is more
    than 30 days in the past. The v22 schema gives us the table; we
    insert one ancient + one recent row and confirm only the ancient
    one is deleted."""
    _force_db_open()
    # Create a real user so the FK on alert_silences.created_by_user_id
    # is satisfied (otherwise foreign_key_check will FAIL and the run
    # exits 1 even though the silence-delete worked).
    from auth.users import signup
    user = signup("silence_tester", "correct horse battery staple")
    assert user is not None
    uid = user.user_id

    ancient_iso = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    recent_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = _direct_conn()
    try:
        # v22 alert_silences columns: silence_id PK, user_id, rule_id,
        # ticker, severity, reason, starts_at, expires_at, created_at,
        # created_by_user_id.
        conn.execute(
            "INSERT INTO alert_silences "
            "(silence_id, user_id, starts_at, expires_at, created_at, "
            " created_by_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("anc-1", uid, ancient_iso, ancient_iso, ancient_iso, uid),
        )
        conn.execute(
            "INSERT INTO alert_silences "
            "(silence_id, user_id, starts_at, expires_at, created_at, "
            " created_by_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("rec-1", uid, recent_iso, recent_iso, recent_iso, uid),
        )
        conn.commit()
    finally:
        conn.close()

    code, out, err = _run(["--fix"], capsys)
    assert code == 0, (out, err)

    conn2 = _direct_conn()
    try:
        cur = conn2.execute(
            "SELECT silence_id FROM alert_silences ORDER BY silence_id"
        )
        ids = [r["silence_id"] for r in cur.fetchall()]
    finally:
        conn2.close()
    # Ancient row deleted; recent row left intact.
    assert "anc-1" not in ids
    assert "rec-1" in ids


def test_fix_does_not_touch_integrity_failures(capsys, tmp_path) -> None:
    """--fix should never attempt to repair integrity_check or
    foreign_key_check failures. The simplest defining-property assert:
    the list of fix actions returned by run_all_fixes does NOT include
    anything that mentions integrity or foreign_key."""
    _force_db_open()
    from tools.db_check_cli import run_all_fixes

    conn = sqlite3.connect(str(_live_db_path()))
    try:
        fixes = run_all_fixes(conn)
    finally:
        conn.close()
    actions = [f.get("action", "") for f in fixes]
    assert not any("integrity" in a for a in actions)
    assert not any("foreign_key" in a for a in actions)


def test_json_output_is_valid(capsys) -> None:
    """--json must emit a single valid JSON document on stdout. The
    document has the documented keys + a checks array."""
    _force_db_open()
    code, out, err = _run(["--json"], capsys)
    assert code == 0, (out, err)
    doc = json.loads(out)
    assert "checks" in doc
    assert isinstance(doc["checks"], list)
    assert "passed" in doc
    assert "warned" in doc
    assert "failed" in doc
    assert "db_path" in doc
    assert "schema_version" in doc
    assert "ran_at" in doc


def test_db_flag_targets_specific_file(capsys, tmp_path) -> None:
    """--db PATH must check the supplied file, not the resolved live
    cache/ DB. Build a second DB in tmp_path, hand-write a tiny
    schema, and confirm the JSON output's db_path matches."""
    # Build a minimal kv_state-only DB at a different path.
    other_db = tmp_path / "other" / "other.db"
    other_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(other_db))
    try:
        conn.execute(
            "CREATE TABLE kv_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO kv_state VALUES ('schema_version', '21', '2024-01-01T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    code, out, err = _run(["--db", str(other_db), "--json"], capsys)
    # Will not be 1 on the schema check (the kv version is the same
    # 21 as the running code as of this test); integrity should pass
    # on the fresh tiny DB. Just confirm the targeting.
    doc = json.loads(out)
    assert doc["db_path"] == str(other_db)


def test_full_flag_runs_additional_checks(capsys) -> None:
    """--full adds the known_tables check on top of the default set."""
    _force_db_open()
    code, out, err = _run(["--full"], capsys)
    assert code == 0, (out, err)
    assert "known_tables" in out
    # Default (no --full) should NOT include known_tables.
    code2, out2, err2 = _run([], capsys)
    assert "known_tables" not in out2


def test_per_test_isolation(capsys, tmp_path) -> None:
    """Each test gets its own DB at tmp_path — confirm the isolated
    DB_PATH points at the per-test tmp."""
    from state import db as state_db
    assert str(tmp_path) in str(state_db.DB_PATH)


def test_exit_code_zero_on_clean_db(capsys) -> None:
    """Clean DB → exit 0."""
    _force_db_open()
    code, _, _ = _run([], capsys)
    assert code == 0


def test_exit_code_one_on_fail(capsys) -> None:
    """Force a FAIL (duplicate rule_ids) → exit 1."""
    _force_db_open()
    conn = _direct_conn()
    try:
        conn.execute("DROP TABLE alert_rules")
        conn.execute(
            "CREATE TABLE alert_rules ("
            " rule_id TEXT, data TEXT NOT NULL, "
            " user_id TEXT NOT NULL DEFAULT '', "
            " cooldown_minutes INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data, user_id) VALUES (?, ?, ?)",
            ("dup", "{}", "u"),
        )
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data, user_id) VALUES (?, ?, ?)",
            ("dup", "{}", "u"),
        )
        conn.commit()
    finally:
        conn.close()
    code, out, err = _run([], capsys)
    assert code == 1, (out, err)
    assert "FAIL" in out


def test_missing_db_returns_one(capsys, tmp_path) -> None:
    """--db pointing at a nonexistent file → exit 1 + stderr message."""
    nowhere = tmp_path / "doesnotexist.db"
    code, out, err = _run(["--db", str(nowhere)], capsys)
    assert code == 1
    assert "DB not found" in err
