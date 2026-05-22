"""Tests for state.db + state.migrations — the shared SQLite layer.

Covers:
  - state.db.get_connection: lazy open, idempotent on repeat calls,
    creates the parent dir, WAL mode enabled, schema initialized
  - state.db.reset_for_tests: drops the cached connection so the next
    call re-opens against a (possibly newly-patched) DB_PATH
  - Schema: all four tables present (kv_state, alerts, alert_rules,
    report_history); schema_version stamped after init
  - state.migrations: JSON → SQLite import is idempotent, handles
    missing files, malformed records, and partial schemas
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB pointed at a per-test tmp_path."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── get_connection ────────────────────────────────────────────────────────

def test_get_connection_creates_parent_dir(tmp_path, monkeypatch) -> None:
    from state import db as state_db

    nested = tmp_path / "nested" / "deeper" / "ship_tracker.db"
    monkeypatch.setattr(state_db, "DB_PATH", nested)
    state_db.reset_for_tests()

    conn = state_db.get_connection()
    assert nested.parent.exists()
    assert nested.exists()
    assert conn is not None


def test_get_connection_is_idempotent() -> None:
    from state.db import get_connection

    a = get_connection()
    b = get_connection()
    assert a is b


def test_reset_for_tests_drops_cached_connection(tmp_path, monkeypatch) -> None:
    from state import db as state_db

    a = state_db.get_connection()
    state_db.reset_for_tests()
    # Point at a different file so we know a NEW connection opened
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "second.db")
    b = state_db.get_connection()
    assert a is not b


def test_connection_uses_wal_mode() -> None:
    from state.db import get_connection

    conn = get_connection()
    row = conn.execute("PRAGMA journal_mode").fetchone()
    # SQLite returns the active journal_mode as the first column
    assert row[0].lower() == "wal"


# ─── Schema ────────────────────────────────────────────────────────────────

def test_all_tables_exist_after_init() -> None:
    from state.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {r["name"] for r in rows}
    assert {"kv_state", "alerts", "alert_rules", "report_history"} <= table_names


def test_schema_version_stamped_after_init() -> None:
    from state.db import SCHEMA_VERSION, get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    assert int(row["value"]) == SCHEMA_VERSION


def test_alerts_table_has_indexes() -> None:
    from state.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='alerts'"
    ).fetchall()
    idx_names = {r["name"] for r in rows}
    assert "idx_alerts_created_at" in idx_names
    assert "idx_alerts_unacknowledged" in idx_names


def test_v4_migration_adds_acknowledged_at_column() -> None:
    """v4 ALTER TABLE adds ``acknowledged_at`` to the alerts table —
    the column must exist and default to empty-string on fresh inserts."""
    from state.db import get_connection

    conn = get_connection()
    cols = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(alerts)").fetchall()
    }
    assert "acknowledged_at" in cols
    # Column must be TEXT and have an empty-string default.
    assert cols["acknowledged_at"]["type"].upper() == "TEXT"
    # Default value text varies between SQLite versions ("''", "\"\"") —
    # the simplest check is that a fresh INSERT comes back with ''.
    conn.execute(
        """
        INSERT INTO alerts
          (alert_id, created_at, alert_type, severity, title, body)
        VALUES ('vc4', '2026-05-21T00:00:00+00:00', 'MACRO', 'LOW', 't', 'b')
        """
    )
    row = conn.execute(
        "SELECT acknowledged_at FROM alerts WHERE alert_id = 'vc4'"
    ).fetchone()
    assert row["acknowledged_at"] == ""


def test_foreign_keys_enabled() -> None:
    from state.db import get_connection

    conn = get_connection()
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


# ─── Migrations: alerts.json import ───────────────────────────────────────

def test_migration_imports_alerts_json(tmp_path, monkeypatch) -> None:
    """Synthesize a legacy alerts.json under the project root, run the
    migration via the schema init path, verify the rows landed in SQLite."""
    from state import db as state_db
    from state import migrations as migr

    # Build a synthetic legacy file at the path the migration helper reads
    legacy = tmp_path / "alerts.json"
    legacy.write_text(json.dumps([
        {
            "alert_id": "a1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "alert_type": "BDI_MOVE",
            "severity": "HIGH",
            "title": "T1",
            "body": "B1",
            "ticker": "ZIM",
            "value": 1.5,
            "threshold": 1.0,
            "change_pct": 50.0,
            "acknowledged": False,
        },
        {
            "alert_id": "a2",
            "created_at": "2026-01-02T00:00:00+00:00",
            "alert_type": "RATE_SURGE",
            "severity": "CRITICAL",
            "title": "T2",
            "body": "B2",
            "acknowledged": True,
        },
    ]), encoding="utf-8")
    monkeypatch.setattr(migr, "_ALERTS_JSON", legacy)

    # Force a fresh DB so the migration runs on next get_connection
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    conn = state_db.get_connection()
    rows = conn.execute(
        "SELECT alert_id, severity, acknowledged FROM alerts ORDER BY alert_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["alert_id"] == "a1"
    assert rows[0]["acknowledged"] == 0
    assert rows[1]["alert_id"] == "a2"
    assert rows[1]["acknowledged"] == 1


def test_migration_is_idempotent_via_schema_version(tmp_path, monkeypatch) -> None:
    """Once schema_version is stamped at SCHEMA_VERSION, subsequent
    get_connection() calls must NOT re-run migrations."""
    from state import db as state_db
    from state import migrations as migr

    # First run with a legacy file present
    legacy = tmp_path / "alerts.json"
    legacy.write_text(json.dumps([{
        "alert_id": "a1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "alert_type": "BDI_MOVE",
        "severity": "HIGH",
        "title": "t", "body": "b",
    }]), encoding="utf-8")
    monkeypatch.setattr(migr, "_ALERTS_JSON", legacy)
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    state_db.get_connection()

    # Reset cached connection but DO NOT delete the DB file. Now mutate
    # the legacy file to add a NEW alert and confirm it does NOT come in.
    state_db.reset_for_tests()
    legacy.write_text(json.dumps([
        {"alert_id": "a1", "created_at": "x", "alert_type": "X",
         "severity": "LOW", "title": "t", "body": "b"},
        {"alert_id": "a_new", "created_at": "y", "alert_type": "X",
         "severity": "LOW", "title": "t", "body": "b"},
    ]), encoding="utf-8")
    conn = state_db.get_connection()
    rows = conn.execute("SELECT alert_id FROM alerts").fetchall()
    ids = {r["alert_id"] for r in rows}
    # Only a1 imported on the first run; a_new must NOT appear because
    # schema_version >= 1 short-circuits the migration.
    assert "a1" in ids
    assert "a_new" not in ids


def test_migration_imports_rules_json(tmp_path, monkeypatch) -> None:
    from state import db as state_db
    from state import migrations as migr

    legacy = tmp_path / "rules.json"
    legacy.write_text(json.dumps([
        {"rule_id": "r1", "name": "Rule 1", "enabled": True},
        {"id": "r2", "name": "Rule 2", "enabled": False},  # 'id' key fallback
        {"name": "Rule 3 with no id"},  # skipped
    ]), encoding="utf-8")
    monkeypatch.setattr(migr, "_RULES_JSON", legacy)

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    conn = state_db.get_connection()
    rows = conn.execute(
        "SELECT rule_id FROM alert_rules ORDER BY rule_id"
    ).fetchall()
    ids = {r["rule_id"] for r in rows}
    assert ids == {"r1", "r2"}


def test_migration_imports_report_history(tmp_path, monkeypatch) -> None:
    from state import db as state_db
    from state import migrations as migr

    legacy = tmp_path / "report_index.json"
    legacy.write_text(json.dumps([
        {
            "report_id": "rpt-1",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "report_date": "January 1, 2026",
            "sentiment_label": "BULLISH",
            "sentiment_score": 0.7,
            "risk_level": "LOW",
            "signal_count": 5,
            "data_quality": "FULL",
            "file_path": "/tmp/r.html",
            "file_size_kb": 12.5,
        },
    ]), encoding="utf-8")
    monkeypatch.setattr(migr, "_REPORT_INDEX_JSON", legacy)

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    conn = state_db.get_connection()
    row = conn.execute(
        "SELECT * FROM report_history WHERE report_id = 'rpt-1'"
    ).fetchone()
    assert row is not None
    assert row["sentiment_label"] == "BULLISH"
    assert row["signal_count"] == 5
    assert row["file_size_kb"] == 12.5


def test_migration_handles_missing_legacy_files(tmp_path, monkeypatch) -> None:
    """No legacy files → migration runs without error, tables are empty."""
    from state import db as state_db
    from state import migrations as migr

    monkeypatch.setattr(migr, "_ALERTS_JSON", tmp_path / "does_not_exist_alerts.json")
    monkeypatch.setattr(migr, "_RULES_JSON", tmp_path / "does_not_exist_rules.json")
    monkeypatch.setattr(migr, "_REPORT_INDEX_JSON", tmp_path / "does_not_exist_index.json")
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    conn = state_db.get_connection()
    assert conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM alert_rules").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM report_history").fetchone()["n"] == 0


def test_migration_skips_malformed_records(tmp_path, monkeypatch) -> None:
    """A legacy file with mixed good/bad records imports only the good ones."""
    from state import db as state_db
    from state import migrations as migr

    legacy = tmp_path / "alerts.json"
    legacy.write_text(json.dumps([
        {"alert_id": "good", "created_at": "x", "alert_type": "T",
         "severity": "LOW", "title": "t", "body": "b"},
        "not a dict",  # skipped
        {"missing_alert_id": True},  # skipped
    ]), encoding="utf-8")
    monkeypatch.setattr(migr, "_ALERTS_JSON", legacy)
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    conn = state_db.get_connection()
    ids = {r["alert_id"] for r in conn.execute(
        "SELECT alert_id FROM alerts"
    ).fetchall()}
    assert ids == {"good"}


def test_migration_handles_non_list_payload(tmp_path, monkeypatch) -> None:
    """If a legacy file contains a dict instead of a list, migration
    skips it silently rather than crashing."""
    from state import db as state_db
    from state import migrations as migr

    bad = tmp_path / "alerts.json"
    bad.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    monkeypatch.setattr(migr, "_ALERTS_JSON", bad)
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    conn = state_db.get_connection()  # must not raise
    assert conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 0


def test_migration_handles_corrupt_json(tmp_path, monkeypatch) -> None:
    from state import db as state_db
    from state import migrations as migr

    bad = tmp_path / "alerts.json"
    bad.write_text("not valid json {{{ at all", encoding="utf-8")
    monkeypatch.setattr(migr, "_ALERTS_JSON", bad)
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "fresh.db")
    state_db.reset_for_tests()
    conn = state_db.get_connection()  # must not raise
    assert conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 0


# ─── Schema v5: report_history public-share columns ────────────────────────

def test_v5_migration_adds_public_share_columns() -> None:
    """v5 ALTER TABLE adds both ``public_slug`` and ``public_expires_at``
    to report_history, each a TEXT NOT NULL column defaulting to ''."""
    from state.db import get_connection

    conn = get_connection()
    cols = {
        r["name"]: r for r in conn.execute(
            "PRAGMA table_info(report_history)"
        ).fetchall()
    }
    assert "public_slug" in cols
    assert "public_expires_at" in cols
    assert cols["public_slug"]["type"].upper() == "TEXT"
    assert cols["public_expires_at"]["type"].upper() == "TEXT"
    # NOT NULL is enforced (notnull == 1 in PRAGMA output).
    assert cols["public_slug"]["notnull"] == 1
    assert cols["public_expires_at"]["notnull"] == 1

    # New rows must default both columns to the empty string.
    with conn:
        conn.execute(
            """
            INSERT INTO report_history
              (report_id, generated_at, file_path)
            VALUES ('v5c', '2026-05-21T00:00:00+00:00', '/tmp/x.html')
            """
        )
    row = conn.execute(
        "SELECT public_slug, public_expires_at FROM report_history "
        "WHERE report_id = 'v5c'"
    ).fetchone()
    assert row["public_slug"] == ""
    assert row["public_expires_at"] == ""


def test_v5_migration_is_idempotent_across_reopens(tmp_path, monkeypatch) -> None:
    """Re-opening a v5+ database must not raise even though the ALTER
    TABLE in _migrate_to_v5 would otherwise complain about duplicate
    columns on the second invocation."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "v5.db")
    state_db.reset_for_tests()
    state_db.get_connection()  # initial open — runs the migration

    state_db.reset_for_tests()
    state_db.get_connection()  # second open — must be a no-op

    state_db.reset_for_tests()
    conn = state_db.get_connection()  # third open
    col_names = [r["name"] for r in conn.execute(
        "PRAGMA table_info(report_history)"
    ).fetchall()]
    # No duplicates from re-running the ALTER TABLE.
    assert col_names.count("public_slug") == 1
    assert col_names.count("public_expires_at") == 1


# ─── Schema v7: users table + per-table user_id columns ───────────────────

def test_v7_migration_creates_users_table_and_adds_user_id_columns() -> None:
    """v7 lays the foundation for multi-user auth:

      * Adds a new ``users`` table.
      * Adds a nullable ``user_id`` column to each of the five existing
        domain tables (alerts, alert_rules, report_history,
        delivery_channels, llm_calls).
    """
    from state.db import get_connection

    conn = get_connection()

    # 1. users table exists with the documented columns.
    user_cols = {
        r["name"]: r for r in conn.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    }
    assert set(user_cols.keys()) >= {
        "user_id", "username", "password_hash", "password_salt",
        "role", "created_at", "last_login_at",
    }
    # username is UNIQUE (notnull and indexed).
    assert user_cols["username"]["notnull"] == 1

    # 2. Each of the five domain tables has a user_id column.
    for table in (
        "alerts",
        "alert_rules",
        "report_history",
        "delivery_channels",
        "llm_calls",
    ):
        cols = {
            r["name"]: r for r in conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        assert "user_id" in cols, (
            f"v7 migration missed {table}.user_id"
        )
        assert cols["user_id"]["type"].upper() == "TEXT"
        # NOT NULL with empty-string default — legacy rows belong to "no user".
        assert cols["user_id"]["notnull"] == 1


def test_v7_user_id_default_is_empty_string_on_insert() -> None:
    """A row inserted without specifying user_id must default to ''."""
    from state.db import get_connection

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO alerts
          (alert_id, created_at, alert_type, severity, title, body)
        VALUES ('v7c', '2026-05-22T00:00:00+00:00', 'MACRO', 'LOW', 't', 'b')
        """
    )
    row = conn.execute(
        "SELECT user_id FROM alerts WHERE alert_id = 'v7c'"
    ).fetchone()
    assert row["user_id"] == ""


def test_v7_migration_is_idempotent_across_reopens(tmp_path, monkeypatch) -> None:
    """Re-opening a v7+ DB must not raise. The ALTER TABLE swallows
    "duplicate column name" errors, and the CREATE TABLE IF NOT EXISTS
    on the users table is a no-op on re-run."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "v7.db")
    state_db.reset_for_tests()
    state_db.get_connection()

    state_db.reset_for_tests()
    state_db.get_connection()

    state_db.reset_for_tests()
    conn = state_db.get_connection()
    # No duplicate user_id columns from re-running the ALTER TABLE.
    for table in (
        "alerts",
        "alert_rules",
        "report_history",
        "delivery_channels",
        "llm_calls",
    ):
        col_names = [r["name"] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()]
        assert col_names.count("user_id") == 1, (
            f"{table} has more than one user_id column after re-open"
        )


# ─── Schema v9: investor_report_snapshots table ───────────────────────────

def test_v9_migration_adds_investor_report_snapshots_table() -> None:
    """v9 adds the ``investor_report_snapshots`` table so the briefing-tab
    diff survives Streamlit restarts. Confirms the table exists with the
    documented columns and that the supporting index is in place."""
    from state.db import get_connection

    conn = get_connection()

    # 1. Table exists with the documented columns.
    cols = {
        r["name"]: r for r in conn.execute(
            "PRAGMA table_info(investor_report_snapshots)"
        ).fetchall()
    }
    assert set(cols.keys()) >= {
        "snapshot_id", "generated_at", "report_date", "payload_json", "user_id",
    }
    # snapshot_id is the primary key.
    assert cols["snapshot_id"]["pk"] == 1
    # report_date + user_id default to empty string.
    assert cols["report_date"]["dflt_value"] in ("''", '""')
    assert cols["user_id"]["dflt_value"] in ("''", '""')

    # 2. The supporting index on generated_at is present.
    idx_rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='investor_report_snapshots'"
    ).fetchall()
    idx_names = {r["name"] for r in idx_rows}
    assert "idx_investor_report_snapshots_generated_at" in idx_names

    # 3. A fresh INSERT round-trips.
    conn.execute(
        """
        INSERT INTO investor_report_snapshots
          (snapshot_id, generated_at, payload_json)
        VALUES ('v9c', '2026-05-22T00:00:00+00:00', '{}')
        """
    )
    row = conn.execute(
        "SELECT * FROM investor_report_snapshots WHERE snapshot_id = 'v9c'"
    ).fetchone()
    assert row is not None
    # Defaults landed.
    assert row["report_date"] == ""
    assert row["user_id"] == ""


def test_v9_migration_is_idempotent_across_reopens(tmp_path, monkeypatch) -> None:
    """Re-opening a v9+ DB does not raise — CREATE TABLE IF NOT EXISTS is
    inherently idempotent, but we exercise the path anyway."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "v9.db")
    state_db.reset_for_tests()
    state_db.get_connection()

    state_db.reset_for_tests()
    state_db.get_connection()

    state_db.reset_for_tests()
    conn = state_db.get_connection()
    # Table still has exactly one row in sqlite_master (no duplicates).
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='investor_report_snapshots'"
    ).fetchall()
    assert len(rows) == 1


# ─── Schema v10: audit_events table ───────────────────────────────────────

def test_v10_migration_adds_audit_events_table() -> None:
    """v10 adds the ``audit_events`` table so privileged user actions
    (alert acks, rule edits, channel CRUD, report deletion, share-link
    generation, signup/login) leave a queryable record for security
    review. Confirms the table exists with the documented columns and
    that the three supporting indexes are in place."""
    from state.db import get_connection

    conn = get_connection()

    # 1. Table exists with the documented columns.
    cols = {
        r["name"]: r for r in conn.execute(
            "PRAGMA table_info(audit_events)"
        ).fetchall()
    }
    assert set(cols.keys()) >= {
        "event_id", "created_at", "user_id", "action",
        "entity_type", "entity_id", "detail_json",
    }
    # event_id is the primary key.
    assert cols["event_id"]["pk"] == 1
    # The defaulted columns default to empty string / '{}' on the
    # detail_json column.
    assert cols["user_id"]["dflt_value"] in ("''", '""')
    assert cols["entity_type"]["dflt_value"] in ("''", '""')
    assert cols["entity_id"]["dflt_value"] in ("''", '""')
    assert cols["detail_json"]["dflt_value"] in ("'{}'", '"{}"')

    # 2. All three supporting indexes are present.
    idx_rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='audit_events'"
    ).fetchall()
    idx_names = {r["name"] for r in idx_rows}
    assert "idx_audit_events_created_at" in idx_names
    assert "idx_audit_events_user_id" in idx_names
    assert "idx_audit_events_action" in idx_names

    # 3. A fresh INSERT round-trips and the defaults land.
    conn.execute(
        """
        INSERT INTO audit_events
          (event_id, created_at, action)
        VALUES ('v10c', '2026-05-22T00:00:00+00:00', 'test_action')
        """
    )
    row = conn.execute(
        "SELECT * FROM audit_events WHERE event_id = 'v10c'"
    ).fetchone()
    assert row is not None
    assert row["user_id"] == ""
    assert row["entity_type"] == ""
    assert row["entity_id"] == ""
    assert row["detail_json"] == "{}"


def test_v10_schema_version_stamped() -> None:
    """After init, the stored schema_version must be exactly 10.

    Guards against an accidental SCHEMA_VERSION rollback in a future
    edit — the audit log must continue to ship at v10 (or higher) so
    the audit_events table is always present.
    """
    from state.db import SCHEMA_VERSION, get_connection

    assert SCHEMA_VERSION >= 10
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'schema_version'"
    ).fetchone()
    assert int(row["value"]) >= 10
