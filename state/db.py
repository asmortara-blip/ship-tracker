"""Shared SQLite persistence for the Ship Tracker app.

Before this module, durable domain state lived in JSON files: alerts.json,
rules.json, and reports/index.json. Each module reimplemented its own
load/save/dedupe/cap logic, with no transactionality and no way to query
across domains (e.g. "all CRITICAL alerts from the last 30 days that
fired on a rule named X").

This module replaces those JSON layers with a single SQLite database at
``cache/ship_tracker.db``. The connection is opened lazily, WAL-mode for
concurrent reads/writes, and the schema is created on first access.

Public surface
--------------
``get_connection()``
    Return the shared sqlite3.Connection, creating + initializing the
    database file if it does not yet exist. Safe to call repeatedly.

``DB_PATH``
    Module-level Path constant. Tests monkeypatch this to point at a
    tmp_path so they never touch ``cache/ship_tracker.db``.

``reset_for_tests()``
    Drop the cached connection so the next ``get_connection()`` re-opens.
    Tests call this after monkeypatching ``DB_PATH``.

Schema versioning
-----------------
The ``kv_state`` table carries a ``schema_version`` row holding the
current integer schema version. ``_init_schema()`` checks it on startup
and runs pending migrations from ``state/migrations.py``. Current
version: 1 (initial schema).

Concurrency
-----------
SQLite WAL mode handles concurrent readers + a single writer safely.
Streamlit threads share the same Connection object (we set
``check_same_thread=False``); this is the recommended pattern when all
threads are coordinated by a single process, as in our case.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from loguru import logger


# ─── Module-level config ────────────────────────────────────────────────────

# Anchor to the project root so the path is stable regardless of CWD.
DB_PATH: Path = Path(__file__).resolve().parent.parent / "cache" / "ship_tracker.db"

# Current schema version. Bump when adding a migration in state/migrations.py.
SCHEMA_VERSION: int = 1


# ─── Connection cache ──────────────────────────────────────────────────────

_conn_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def get_connection() -> sqlite3.Connection:
    """Return the shared sqlite3.Connection, opening + initializing the
    database file lazily on first call."""
    global _conn
    with _conn_lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False is safe because we serialize through
            # _conn_lock for connection setup; SQLite WAL mode handles
            # concurrent statements once the connection is established.
            _conn = sqlite3.connect(
                DB_PATH,
                check_same_thread=False,
                isolation_level=None,  # autocommit; we use explicit transactions where it matters
            )
            _conn.row_factory = sqlite3.Row
            # WAL gives us concurrent readers + a single writer without
            # blocking on every transaction.
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _init_schema(_conn)
            logger.debug(f"state.db: opened SQLite at {DB_PATH}")
    return _conn


def reset_for_tests() -> None:
    """Drop the cached connection so the next get_connection() re-opens.

    Tests call this after monkeypatching DB_PATH to a tmp_path so each
    test gets a fresh database file."""
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


# ─── Schema ────────────────────────────────────────────────────────────────

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS kv_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id     TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    alert_type   TEXT NOT NULL,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    ticker       TEXT NOT NULL DEFAULT '',
    route_id     TEXT NOT NULL DEFAULT '',
    port_locode  TEXT NOT NULL DEFAULT '',
    value        REAL NOT NULL DEFAULT 0.0,
    threshold    REAL NOT NULL DEFAULT 0.0,
    change_pct   REAL NOT NULL DEFAULT 0.0,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_unacknowledged ON alerts(acknowledged);

CREATE TABLE IF NOT EXISTS alert_rules (
    rule_id TEXT PRIMARY KEY,
    data    TEXT NOT NULL  -- JSON-encoded rule dict
);

CREATE TABLE IF NOT EXISTS report_history (
    report_id        TEXT PRIMARY KEY,
    generated_at     TEXT NOT NULL,
    report_date      TEXT NOT NULL DEFAULT '',
    sentiment_label  TEXT NOT NULL DEFAULT '',
    sentiment_score  REAL NOT NULL DEFAULT 0.0,
    risk_level       TEXT NOT NULL DEFAULT '',
    signal_count     INTEGER NOT NULL DEFAULT 0,
    data_quality     TEXT NOT NULL DEFAULT '',
    file_path        TEXT NOT NULL,
    file_size_kb     REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_report_history_generated_at ON report_history(generated_at);
"""


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if missing, then run any pending migrations."""
    conn.executescript(_SCHEMA_V1)

    # Read current schema version (default 0 if no row yet).
    cur = conn.execute("SELECT value FROM kv_state WHERE key = 'schema_version'")
    row = cur.fetchone()
    current = int(row["value"]) if row else 0

    if current >= SCHEMA_VERSION:
        return  # Up to date

    # Run migrations. State today has only the v1 schema. Future versions
    # add migration steps here (or in state/migrations.py).
    from datetime import datetime, timezone

    # Migration 0 → 1: import legacy JSON files if present (best-effort,
    # idempotent — runs at most once because schema_version is then set).
    if current < 1:
        try:
            from state.migrations import migrate_legacy_json_files
            migrate_legacy_json_files(conn)
        except Exception as exc:
            logger.warning(f"state.db: legacy migration skipped: {exc}")

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO kv_state (key, value, updated_at) VALUES (?, ?, ?)",
        ("schema_version", str(SCHEMA_VERSION), now_iso),
    )
