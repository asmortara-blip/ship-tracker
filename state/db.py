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
version: 6 (adds ``delivery_channels.digest_mode`` column —
``immediate`` per-alert delivery (default, preserves legacy behavior)
or ``daily`` digest mode that batches the eligible alerts into a
single delivery).

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
#
# Version history (skip-v5 is intentional — see note below):
#   1 — initial schema (alerts / alert_rules / report_history / kv_state)
#   2 — adds delivery_channels table for outbound delivery
#   3 — adds llm_calls table for LLM cost telemetry
#   4 — adds alerts.acknowledged_at for median time-to-ack analytics
#   5 — RESERVED for a sibling-agent migration that may land separately
#   6 — adds delivery_channels.digest_mode column (immediate | daily)
#
# v5 is held aside because this branch was authored in parallel with
# another agent's schema bump. Per the digest-mode task spec, this
# change takes the next available slot (v6) so both can ship without
# colliding on the same version number.
SCHEMA_VERSION: int = 6


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

# Schema v2 adds the delivery_channels table. Kept as a separate script so
# the v2 migration helper can re-use the exact same CREATE TABLE statement
# (idempotent via IF NOT EXISTS).
#
# NOTE: the v6 migration adds a ``digest_mode`` column to this table via
# ALTER TABLE (see ``_SCHEMA_V6_NOTE`` and ``_migrate_to_v6``). The column
# is added separately because SQLite cannot do IF NOT EXISTS on ALTER
# TABLE; keeping the CREATE statement unchanged avoids forking the v2
# bootstrap path.
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS delivery_channels (
    channel_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL,            -- 'slack' today; 'email'/'sms' later
    target              TEXT NOT NULL,            -- webhook URL for slack
    severity_threshold  TEXT NOT NULL DEFAULT 'LOW',
    enabled             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL
);
"""

# Schema v3 adds the llm_calls table for LLM cost telemetry. Every Anthropic
# call (commentary, narration, future) records one row here so the platform
# can answer "how much have I spent on Claude this week?".
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id       TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    source        TEXT NOT NULL,           -- 'commentary' / 'narration' / future
    tab_name      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    est_cost_usd  REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_source ON llm_calls(source);
"""

# Schema v4 adds the ``acknowledged_at`` column to the alerts table so
# the alert analytics module can compute median time-to-ack. SQLite's
# ALTER TABLE ADD COLUMN does NOT support IF NOT EXISTS, so the column
# add is wrapped in try/except inside ``_migrate_to_v4`` and is therefore
# idempotent across re-runs and across fresh-DB initialization (where
# the column has already been created — but a fresh DB started from the
# v1 schema script does NOT have it; we run the migration unconditionally
# in _init_schema below and let the OperationalError no-op handle the
# already-exists case).
_SCHEMA_V4_NOTE: str = (
    "v4: alerts.acknowledged_at TEXT NOT NULL DEFAULT '' "
    "(added via ALTER TABLE in _migrate_to_v4)"
)

# Schema v5 adds two columns to report_history for read-only public-share
# links: ``public_slug`` (URL-safe base64url token, empty when the report
# has not been shared) and ``public_expires_at`` (ISO-8601 UTC timestamp;
# the link is valid only while this is in the future). Same idempotent
# ALTER-TABLE-in-try/except pattern as v4 — SQLite does not support
# IF NOT EXISTS on ALTER TABLE.
_SCHEMA_V5_NOTE: str = (
    "v5: report_history.public_slug TEXT NOT NULL DEFAULT '' + "
    "report_history.public_expires_at TEXT NOT NULL DEFAULT '' "
    "(added via ALTER TABLE in _migrate_to_v5)"
)

# Schema v6 adds the ``digest_mode`` column to ``delivery_channels`` so a
# channel can batch the eligible alerts since ``since`` into a single
# delivery instead of POSTing one message per alert. Values:
#
#   "immediate" — legacy behaviour: one delivery per alert (the default,
#                 so pre-v6 rows keep behaving exactly as they did).
#   "daily"     — batch every eligible alert into one digest delivery
#                 each time ``deliver_pending`` runs.
#
# Same idempotent ALTER-TABLE-in-try/except pattern as v4 / v5 — SQLite
# does not support IF NOT EXISTS on ALTER TABLE. Added via
# ``_migrate_to_v6`` in ``state/migrations.py``.
_SCHEMA_V6_NOTE: str = (
    "v6: delivery_channels.digest_mode TEXT NOT NULL DEFAULT 'immediate' "
    "(added via ALTER TABLE in _migrate_to_v6)"
)


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if missing, then run any pending migrations."""
    conn.executescript(_SCHEMA_V1)
    # v2 add-only schema (CREATE TABLE IF NOT EXISTS) — safe to run on
    # every open so fresh databases skip the explicit migration step.
    conn.executescript(_SCHEMA_V2)
    # v3 add-only schema (CREATE TABLE IF NOT EXISTS) — same pattern; the
    # explicit _migrate_to_v3 path in state.migrations re-runs this
    # script on upgrade, so a fresh DB never needs the helper.
    conn.executescript(_SCHEMA_V3)
    # v4 column add — ALTER TABLE ADD COLUMN cannot be IF-NOT-EXISTS in
    # SQLite, so the helper swallows OperationalError when the column
    # already exists. Safe to run on every open (fresh DB: adds the
    # column; existing DB: no-op).
    try:
        from state.migrations import _migrate_to_v4
        _migrate_to_v4(conn)
    except Exception as exc:
        logger.warning(f"state.db: v4 column add skipped: {exc}")
    # v5 column add — same idempotent pattern as v4. Adds the public-
    # share-link columns to report_history.
    try:
        from state.migrations import _migrate_to_v5
        _migrate_to_v5(conn)
    except Exception as exc:
        logger.warning(f"state.db: v5 column add skipped: {exc}")
    # v6 column add — same idempotent pattern as v4 / v5. Adds the
    # digest_mode column to delivery_channels so a channel can batch
    # its alerts into one delivery instead of one-per-alert.
    try:
        from state.migrations import _migrate_to_v6
        _migrate_to_v6(conn)
    except Exception as exc:
        logger.warning(f"state.db: v6 column add skipped: {exc}")

    # Read current schema version (default 0 if no row yet).
    cur = conn.execute("SELECT value FROM kv_state WHERE key = 'schema_version'")
    row = cur.fetchone()
    current = int(row["value"]) if row else 0

    if current >= SCHEMA_VERSION:
        return  # Up to date

    # Run migrations. Each step is idempotent (CREATE IF NOT EXISTS,
    # INSERT OR IGNORE) so it's safe to re-run if a previous bump only
    # partially completed.
    from datetime import datetime, timezone

    # Migration 0 → 1: import legacy JSON files if present (best-effort,
    # idempotent — runs at most once because schema_version is then set).
    if current < 1:
        try:
            from state.migrations import migrate_legacy_json_files
            migrate_legacy_json_files(conn)
        except Exception as exc:
            logger.warning(f"state.db: legacy migration skipped: {exc}")

    # Migration 1 → 2: add the delivery_channels table.
    if current < 2:
        try:
            from state.migrations import _migrate_to_v2
            _migrate_to_v2(conn)
        except Exception as exc:
            logger.warning(f"state.db: v2 migration skipped: {exc}")

    # Migration 2 → 3: add the llm_calls telemetry table.
    if current < 3:
        try:
            from state.migrations import _migrate_to_v3
            _migrate_to_v3(conn)
        except Exception as exc:
            logger.warning(f"state.db: v3 migration skipped: {exc}")

    # Migration 3 → 4: add the alerts.acknowledged_at column so the
    # alert-analytics module can compute median time-to-ack. The helper
    # is idempotent (it's already invoked once unconditionally above);
    # this branch exists to formally register the upgrade in the
    # version-step ladder.
    if current < 4:
        try:
            from state.migrations import _migrate_to_v4
            _migrate_to_v4(conn)
        except Exception as exc:
            logger.warning(f"state.db: v4 migration skipped: {exc}")

    # Migration 4 → 5: add report_history.public_slug + .public_expires_at
    # for read-only public-share links. As with v4, the helper is already
    # invoked unconditionally above and is idempotent; this branch keeps
    # the version-step ladder explicit.
    if current < 5:
        try:
            from state.migrations import _migrate_to_v5
            _migrate_to_v5(conn)
        except Exception as exc:
            logger.warning(f"state.db: v5 migration skipped: {exc}")

    # Migration 5 → 6: add delivery_channels.digest_mode column so a
    # channel can batch the alerts created since ``since`` into a single
    # delivery instead of POSTing one-per-alert. Existing rows default
    # to 'immediate', which preserves the original per-alert behaviour.
    if current < 6:
        try:
            from state.migrations import _migrate_to_v6
            _migrate_to_v6(conn)
        except Exception as exc:
            logger.warning(f"state.db: v6 migration skipped: {exc}")

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO kv_state (key, value, updated_at) VALUES (?, ?, ?)",
        ("schema_version", str(SCHEMA_VERSION), now_iso),
    )
