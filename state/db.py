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
#   7 — adds users table + nullable user_id columns on five existing
#       tables (alerts, alert_rules, report_history, delivery_channels,
#       llm_calls). Per-user data scoping is left as a follow-up; the
#       empty default keeps legacy rows behaving as before.
#   8 — adds tab_render_events table for per-tab render-duration
#       telemetry so the platform can answer "which tabs are slow?"
#       without firing up a profiler. Populated by ``engine.perf_telemetry``
#       via a context manager any tab can opt into.
#   9 — adds investor_report_snapshots table so the "what changed" diff
#       on the daily briefing tab survives Streamlit restarts. Stores a
#       slimmed-down ReportSnapshot dataclass JSON-encoded — only the
#       fields ``processing.report_diff.compute_report_diff`` actually
#       reads (sentiment.overall_score, alpha.signals, freight.routes,
#       market.risk_level) so we never have to round-trip the full
#       InvestorReport (which carries pandas objects).
#  10 — adds audit_events table for security-review "who did what when"
#       record-keeping. Hooked at the privileged user-action touchpoints
#       (alert acknowledgement, rule changes, channel CRUD, report
#       deletion, share-link generation, signup/login). Every write is
#       best-effort + never raises — an audit-log failure must never
#       break the hot path it sits inside. v10 was claimed for this
#       commit while another sibling agent took v9 for InvestorReport
#       snapshots in the same batch.
#  11 — adds api_tokens table for per-user API access tokens (PATs).
#       Each row carries a hashed-and-salted token (same scrypt-with-
#       PBKDF2-fallback KDF as auth.gate so we do not introduce a new
#       one), an 8-char plaintext prefix for O(log n) lookup via an
#       index, a user-supplied label, created_at / last_used_at
#       timestamps, and a revoked flag. The raw secret is returned
#       exactly once at creation time and never written to disk in
#       plaintext. Lets external scripts authenticate to future API
#       endpoints without needing the user's password.
#  12 — adds data_source_health table so the platform can answer
#       "is FRED degrading right now?" without scrolling through logs.
#       Each periodic liveness probe (fred, yfinance, worldbank,
#       currency, alphavantage, newsapi, oecd, imf, comtrade, ais,
#       canal_panama, canal_suez) writes one row carrying ping_id,
#       source, started_at, duration_ms, status (up | degraded | down)
#       and an error_msg. Populated by ``engine.source_health`` from
#       the worker scheduler; pruned by ``prune_old_pings``.
#  13 — adds three quiet-hours columns to delivery_channels:
#       ``quiet_start`` (HH:MM UTC, empty = no quiet window),
#       ``quiet_end`` (HH:MM UTC), and ``quiet_override_critical``
#       (INTEGER 0/1, default 1 — CRITICAL alerts always deliver).
#       Lets an operator silence a channel during e.g. overnight hours
#       without disabling it outright. Alerts that fire during the
#       window are still persisted to SQLite, just not delivered (no
#       queue-then-drain; this is suppress-only).
#  14 — adds two columns to the alerts table for time-window alert
#       deduplication: ``fire_count INTEGER NOT NULL DEFAULT 1`` and
#       ``last_fired_at TEXT NOT NULL DEFAULT ''``. A flaky data feed
#       that bounces the BDI across its threshold N times in an hour
#       previously inserted N alert rows; with v14, the engine collapses
#       repeat fires of the same (alert_type, severity, ticker,
#       route_id, port_locode) tuple within a configurable window
#       (default 60 min) into one row whose fire_count counts the
#       bounces and whose last_fired_at marks the most-recent fire.
#       The existing alert_id-based INSERT-OR-IGNORE dedup is unchanged
#       — that one still blocks EXACT-duplicate inserts within a single
#       save call; v14 layers a NEAR-duplicate window-based dedup on top.
#  15 — adds the ``user_settings`` table for per-user preferences (NOT
#       domain data — these are UI/UX knobs like timezone, theme,
#       default report window, default alert severity threshold). One
#       row per user, keyed by ``user_id``, with a free-form
#       ``settings_json`` TEXT blob so future preferences can be added
#       by bumping the ``UserSettings`` dataclass without a schema
#       bump. Pre-v15 users have no row; ``get_settings`` returns the
#       defaults dataclass for unknown users so the absence-of-row case
#       is invisible to callers. Populated by ``auth.settings``.
#  16 — adds two columns to the ``users`` table for optional TOTP MFA
#       as a second factor on top of the password login:
#       ``mfa_secret TEXT NOT NULL DEFAULT ''`` (canonical 32-char
#       base32 secret compatible with every standard authenticator app)
#       and ``mfa_enabled INTEGER NOT NULL DEFAULT 0``. Pre-v16 rows
#       pick up the empty/0 defaults — MFA is off, legacy accounts
#       keep logging in with just the password. The TOTP implementation
#       lives in ``auth.mfa`` (stdlib hmac/hashlib/struct — no pyotp
#       dependency). The login surface in ``auth.users.login`` grows an
#       optional ``mfa_code`` kwarg, and ``auth.gate.require_auth_with_users``
#       grows a third "MFA code" form field that only matters once a
#       user enables MFA. Same idempotent ALTER-TABLE-in-try/except
#       pattern as v4 / v5 / v6 / v13 / v14 — each column add is
#       independently safe to re-run.
#  17 — adds two columns to the ``report_history`` table for optional
#       password-gated public report links (layered on top of the v5
#       unguessable slug, so the slug is still required and the
#       password is an EXTRA factor):
#       ``public_password_hash TEXT`` (hex-encoded pbkdf2-sha256 hash;
#       NULL when no password is set → link behaves as today) and
#       ``public_password_salt TEXT`` (hex-encoded random salt; NULL
#       when no password is set). Pre-v17 rows pick up NULL for both
#       columns — viewing any existing public link still works without
#       a password. The hash uses ``hashlib.pbkdf2_hmac('sha256', …,
#       200_000)`` to match the iteration count used elsewhere in
#       ``auth/`` (PBKDF2_ITERATIONS). Same idempotent ALTER-TABLE-in-
#       try/except pattern as v4 / v5 / v6 / v13 / v14 / v16 — each
#       column add is independently safe to re-run. Unlike the other
#       password columns these are declared NULLable (no DEFAULT) so
#       "no password set" is distinguishable from "empty-string
#       password" at the SQL level.
#  18 — adds per-rule cooldown so an AlertRule whose condition stays
#       tripped does not spam downstream channels. Two ALTERs:
#         * ``alert_rules.cooldown_minutes INTEGER NOT NULL DEFAULT 0``
#           — 0 (the default) means no cooldown, preserving the legacy
#           behaviour that every evaluation that trips the rule fires.
#           A positive value N means a successful fire suppresses the
#           same rule_id for the next N minutes (per user).
#         * ``alerts.rule_id TEXT`` — NULLable. Stamped on every alert
#           that is fire_rule()-dispatched so the cooldown query can
#           ask "when did this rule_id last fire?". NULL on legacy /
#           detection-path alerts that never went through fire_rule.
#       Same idempotent ALTER-TABLE-in-try/except pattern as
#       v4/v5/v6/v13/v14/v16/v17 — each column add is independently
#       safe to re-run.
#
# v5 is held aside because this branch was authored in parallel with
# another agent's schema bump. Per the digest-mode task spec, this
# change takes the next available slot (v6) so both can ship without
# colliding on the same version number.
SCHEMA_VERSION: int = 18


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

# Schema v7 lays the foundation for multi-user auth WITHOUT a big-bang
# migration. Two halves:
#
#   1. A new ``users`` table holds the per-user identity row (username,
#      password hash + salt — reusing ``auth.gate._hash_password`` so we
#      do not introduce a new KDF), role, created_at, last_login_at.
#   2. Each of the five existing domain tables (alerts, alert_rules,
#      report_history, delivery_channels, llm_calls) grows a nullable
#      ``user_id TEXT NOT NULL DEFAULT ''`` column. The empty default
#      means legacy rows stay legacy — they belong to "no user" and
#      remain visible under the existing single-password gate. Per-user
#      query scoping is left for a follow-up.
#
# The new table is created via CREATE TABLE IF NOT EXISTS in
# ``_SCHEMA_V7``; the five ALTER TABLE column adds live in
# ``_migrate_to_v7`` with the same idempotent try/except wrapper used
# for v4 / v5 / v6.
_SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    password_salt   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    created_at      TEXT NOT NULL,
    last_login_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""

_SCHEMA_V7_NOTE: str = (
    "v7: users table + user_id TEXT NOT NULL DEFAULT '' on alerts, "
    "alert_rules, report_history, delivery_channels, llm_calls "
    "(added via ALTER TABLE in _migrate_to_v7)"
)

# Schema v8 adds the ``tab_render_events`` table for per-tab render-duration
# telemetry. Every successful (or failed) tab render() that opts in via
# ``engine.perf_telemetry.track_render`` writes one row here. The table is
# narrow on purpose — answering "which tabs are slow?" only needs the tab
# name, the duration, and a success/error column.
#
# Same idempotent CREATE-IF-NOT-EXISTS pattern as v2 / v3: a fresh DB picks
# up the table via the executescript path in _init_schema; the explicit
# ``_migrate_to_v8`` helper re-runs the same script on upgrade.
_SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS tab_render_events (
    event_id     TEXT PRIMARY KEY,
    tab_name     TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    success      INTEGER NOT NULL DEFAULT 1,
    error_msg    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tab_render_events_started_at
    ON tab_render_events(started_at);
CREATE INDEX IF NOT EXISTS idx_tab_render_events_tab
    ON tab_render_events(tab_name);
"""

_SCHEMA_V8_NOTE: str = (
    "v8: tab_render_events table (event_id PK, tab_name, started_at, "
    "duration_ms, success, error_msg) + two indexes "
    "(added via CREATE TABLE IF NOT EXISTS in _migrate_to_v8)"
)

# Schema v9 adds the ``investor_report_snapshots`` table so the "what
# changed" diff in ``ui.tab_briefing`` survives Streamlit restarts. Each
# row stores a slim ReportSnapshot JSON-encoded (sentiment overall score,
# signals, freight routes, risk level) — only the fields
# ``processing.report_diff.compute_report_diff`` actually reads. Never
# tries to round-trip the full InvestorReport (which carries pandas
# objects). Same idempotent CREATE-IF-NOT-EXISTS pattern as v2 / v3 / v8:
# a fresh DB picks up the table via the executescript path in
# _init_schema; the explicit ``_migrate_to_v9`` helper re-runs the same
# script on upgrade.
_SCHEMA_V9 = """
CREATE TABLE IF NOT EXISTS investor_report_snapshots (
    snapshot_id   TEXT PRIMARY KEY,
    generated_at  TEXT NOT NULL,
    report_date   TEXT NOT NULL DEFAULT '',
    payload_json  TEXT NOT NULL,
    user_id       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_investor_report_snapshots_generated_at
    ON investor_report_snapshots(generated_at);
"""

_SCHEMA_V9_NOTE: str = (
    "v9: investor_report_snapshots table (snapshot_id PK, generated_at, "
    "report_date, payload_json, user_id) + index on generated_at "
    "(added via CREATE TABLE IF NOT EXISTS in _migrate_to_v9)"
)

# Schema v10 adds the ``audit_events`` table so privileged user actions
# (alert acks, rule edits, channel CRUD, report deletion, share-link
# generation, signup/login) leave a queryable record for security
# review. Wide on purpose — ``action`` + ``entity_type`` + ``entity_id``
# + a free-form ``detail_json`` payload keeps the table general enough
# to absorb new touchpoints without another migration each time.
#
# Same idempotent CREATE-IF-NOT-EXISTS pattern as v2 / v3 / v8 / v9: a
# fresh DB picks up the table via the executescript path in
# _init_schema; the explicit ``_migrate_to_v10`` helper re-runs the
# same script on upgrade. Three indexes — created_at (range queries),
# user_id (per-user audit views), action (filter by action type).
_SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id     TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    user_id      TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL DEFAULT '',
    entity_id    TEXT NOT NULL DEFAULT '',
    detail_json  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_user_id
    ON audit_events(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_action
    ON audit_events(action);
"""

_SCHEMA_V10_NOTE: str = (
    "v10: audit_events table (event_id PK, created_at, user_id, action, "
    "entity_type, entity_id, detail_json) + three indexes "
    "(added via CREATE TABLE IF NOT EXISTS in _migrate_to_v10)"
)

# Schema v11 adds the ``api_tokens`` table for per-user API access
# tokens (PATs). Each row stores ONLY the hash + salt + 8-char prefix
# of the raw secret — the secret itself is returned exactly once at
# creation time and is never persisted in plaintext. The prefix is in
# plaintext on purpose: ``verify_token`` looks up by prefix in O(log n)
# via the index instead of scanning every row, then constant-time
# compares the hash with ``auth.gate._verify_password``.
#
# Same idempotent CREATE-IF-NOT-EXISTS pattern as v2 / v3 / v8 / v9 /
# v10: a fresh DB picks up the table via the executescript path in
# _init_schema; the explicit ``_migrate_to_v11`` helper re-runs the
# same script on upgrade. Two indexes — user_id (per-user list/revoke
# queries), token_prefix (O(log n) verify lookup).
_SCHEMA_V11 = """
CREATE TABLE IF NOT EXISTS api_tokens (
    token_id      TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    label         TEXT NOT NULL,
    token_hash    TEXT NOT NULL,
    token_salt    TEXT NOT NULL,
    token_prefix  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT NOT NULL DEFAULT '',
    revoked       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id
    ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_prefix
    ON api_tokens(token_prefix);
"""

_SCHEMA_V11_NOTE: str = (
    "v11: api_tokens table (token_id PK, user_id, label, token_hash, "
    "token_salt, token_prefix, created_at, last_used_at, revoked) + "
    "two indexes (added via CREATE TABLE IF NOT EXISTS in _migrate_to_v11)"
)

# Schema v12 adds the ``data_source_health`` table so periodic liveness
# probes of external feeds (FRED, yfinance, World Bank, etc.) leave a
# queryable record. Each row carries ``ping_id`` (uuid), ``source``
# (e.g. 'fred' / 'yfinance' / 'worldbank'), ``started_at`` (ISO UTC),
# ``duration_ms`` (wall-clock measured via ``time.perf_counter``),
# ``status`` (one of 'up' | 'degraded' | 'down'), and a free-form
# ``error_msg`` (empty on success). Two indexes — started_at (window
# queries for "in the last 24h") and source (per-feed dashboards).
#
# Same idempotent CREATE-IF-NOT-EXISTS pattern as v2 / v3 / v8 / v9 /
# v10 / v11: a fresh DB picks up the table via the executescript path
# in ``_init_schema``; the explicit ``_migrate_to_v12`` helper re-runs
# the same script on upgrade.
_SCHEMA_V12 = """
CREATE TABLE IF NOT EXISTS data_source_health (
    ping_id      TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL,
    status       TEXT NOT NULL,
    error_msg    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_data_source_health_started_at
    ON data_source_health(started_at);
CREATE INDEX IF NOT EXISTS idx_data_source_health_source
    ON data_source_health(source);
"""

_SCHEMA_V12_NOTE: str = (
    "v12: data_source_health table (ping_id PK, source, started_at, "
    "duration_ms, status, error_msg) + two indexes "
    "(added via CREATE TABLE IF NOT EXISTS in _migrate_to_v12)"
)

# Schema v13 adds three quiet-hours columns to ``delivery_channels`` so
# an operator can silence a channel during e.g. overnight hours without
# disabling it outright:
#
#   * ``quiet_start``  — HH:MM UTC ("22:00"); empty = no quiet window.
#   * ``quiet_end``    — HH:MM UTC ("07:00"); empty = no quiet window.
#   * ``quiet_override_critical`` — INTEGER 0/1, default 1. When 1, a
#     CRITICAL alert bypasses the quiet window. When 0, even CRITICAL
#     alerts are suppressed during the window.
#
# Pre-v13 rows pick up the column DEFAULTs (empty strings / 1) so the
# legacy behaviour ("no quiet hours configured") is preserved.
#
# Same idempotent ALTER-TABLE-in-try/except pattern as v4 / v5 / v6 —
# SQLite does not support IF NOT EXISTS on ALTER TABLE. Each column is
# added in its own try/except so partial completion of a prior run is
# also tolerated. Added via ``_migrate_to_v13`` in ``state/migrations.py``.
_SCHEMA_V13_NOTE: str = (
    "v13: delivery_channels.quiet_start TEXT NOT NULL DEFAULT '' + "
    "delivery_channels.quiet_end TEXT NOT NULL DEFAULT '' + "
    "delivery_channels.quiet_override_critical INTEGER NOT NULL DEFAULT 1 "
    "(added via ALTER TABLE in _migrate_to_v13)"
)

# Schema v14 adds two columns to the ``alerts`` table for time-window
# alert deduplication:
#
#   * ``fire_count``    — INTEGER NOT NULL DEFAULT 1. Tracks how many
#     times the same dedup_key fired within the configured window.
#     Pre-v14 rows pick up the default (1) which matches the implicit
#     pre-feature meaning ("this alert fired once").
#   * ``last_fired_at`` — TEXT NOT NULL DEFAULT ''. ISO-8601 UTC
#     timestamp of the most-recent fire. Empty for pre-v14 rows; the
#     engine fills it in on every save and on every dedup-bump.
#
# The dedup_key itself is computed in-engine from (alert_type, severity,
# ticker, route_id, port_locode) — it is NOT stored in a dedicated
# column. Storing it would add a redundant denormalised field; the
# in-engine WHERE clause filters by the same five columns directly,
# which the existing ``idx_alerts_created_at`` index can cover in
# combination with the row-set filter SQLite applies.
#
# Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
# ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13``: SQLite
# does NOT support ``IF NOT EXISTS`` on ALTER TABLE, so each statement
# is wrapped in try/except and "duplicate column name" errors are
# swallowed. Added via ``_migrate_to_v14`` in ``state/migrations.py``.
_SCHEMA_V14_NOTE: str = (
    "v14: alerts.fire_count INTEGER NOT NULL DEFAULT 1 + "
    "alerts.last_fired_at TEXT NOT NULL DEFAULT '' "
    "(added via ALTER TABLE in _migrate_to_v14)"
)

# Schema v15 adds the ``user_settings`` table for per-user preferences
# (NOT domain data — these are UI/UX knobs like timezone, theme, default
# report window, default alert severity threshold). One row per user
# keyed by ``user_id``; the actual prefs live JSON-encoded in
# ``settings_json``. Storing them as JSON in a single column means
# adding a NEW preference is a one-line change to the
# ``auth.settings.UserSettings`` dataclass — no schema bump required.
# Pre-v15 users have no row; ``auth.settings.get_settings`` returns the
# defaults dataclass for unknown users so the absence-of-row case is
# invisible to callers.
#
# Same idempotent CREATE-IF-NOT-EXISTS pattern as v2 / v3 / v8 / v9 /
# v10 / v11 / v12: a fresh DB picks up the table via the executescript
# path in ``_init_schema``; the explicit ``_migrate_to_v15`` helper
# re-runs the same script on upgrade. No supporting index — the table
# is keyed by ``user_id`` (the PRIMARY KEY auto-indexes it).
_SCHEMA_V15 = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id       TEXT PRIMARY KEY,
    settings_json TEXT NOT NULL DEFAULT '{}',
    updated_at    TEXT NOT NULL
);
"""

_SCHEMA_V15_NOTE: str = (
    "v15: user_settings table (user_id PK, settings_json, updated_at) "
    "(added via CREATE TABLE IF NOT EXISTS in _migrate_to_v15)"
)

# Schema v16 adds two columns to the ``users`` table for optional TOTP
# MFA as a second factor on top of the password login:
#
#   * ``mfa_secret``  TEXT NOT NULL DEFAULT ''   — canonical 32-char
#     base32 secret used by every standard authenticator app
#     (Google Authenticator, 1Password, Authy, …). Empty when MFA is
#     not enabled.
#   * ``mfa_enabled`` INTEGER NOT NULL DEFAULT 0 — 0/1 flag. Pre-v16
#     rows pick up 0, which preserves the password-only behaviour;
#     ``auth.users.login`` only requires the second factor when this
#     column is 1 for the looked-up user.
#
# Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
# ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13`` /
# ``_migrate_to_v14``: SQLite does NOT support ``IF NOT EXISTS`` on
# ALTER TABLE, so each statement is wrapped in try/except and "duplicate
# column name" errors are swallowed. Each column is added in its own
# try/except so partial completion of a prior run is also tolerated.
# Added via ``_migrate_to_v16`` in ``state/migrations.py``.
_SCHEMA_V16_NOTE: str = (
    "v16: users.mfa_secret TEXT NOT NULL DEFAULT '' + "
    "users.mfa_enabled INTEGER NOT NULL DEFAULT 0 "
    "(added via ALTER TABLE in _migrate_to_v16)"
)

# Schema v17 adds two NULLable columns to ``report_history`` for
# optional password-gated public report links:
#
#   * ``public_password_hash`` TEXT — hex-encoded PBKDF2-HMAC-SHA256
#     digest of the user-chosen password. NULL when no password is set
#     for this report's public link (the default — preserves the v5
#     "anyone with the slug can view" behaviour).
#   * ``public_password_salt`` TEXT — hex-encoded random salt that was
#     used to derive ``public_password_hash``. NULL when no password is
#     set.
#
# Unlike most other columns in this database these are NULLable (no
# ``NOT NULL DEFAULT ''``) so the platform can distinguish "no
# password set on this share link" (NULL) from "empty-string password"
# at the SQL level. ``make_public(report_id, password=...)`` populates
# both columns when a password is supplied; ``verify_public_report_
# password`` and ``load_public_report(slug, password=...)`` consume
# them. The password is hashed immediately on ``make_public`` and is
# never persisted in plaintext.
#
# Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
# ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13`` /
# ``_migrate_to_v14`` / ``_migrate_to_v16``: SQLite does NOT support
# ``IF NOT EXISTS`` on ALTER TABLE, so each statement is wrapped in
# try/except and "duplicate column name" errors are swallowed. Each
# column is added in its own try/except so partial completion of a
# prior run is also tolerated.
_SCHEMA_V17_NOTE: str = (
    "v17: report_history.public_password_hash TEXT + "
    "report_history.public_password_salt TEXT "
    "(added via ALTER TABLE in _migrate_to_v17)"
)

# Schema v18 adds per-rule cooldown so an ``AlertRule`` whose
# condition stays tripped doesn't spam downstream channels. Two
# ALTERs land in this migration:
#
#   * ``alert_rules.cooldown_minutes`` — ``INTEGER NOT NULL DEFAULT 0``.
#     0 means "no cooldown, fire on every evaluation" (the pre-v18
#     behaviour, preserved for existing rows). A positive N means
#     successful fires of this rule suppress subsequent fires of the
#     same rule_id for N minutes (per user).
#   * ``alerts.rule_id`` — ``TEXT`` (NULLable, no DEFAULT). Stamped
#     by ``fire_rule`` so the cooldown query can ask "when did this
#     rule_id last fire for this user?". NULL on alerts that came
#     from the detection helpers (``check_bdi_alerts`` et al.) which
#     pre-date the rule-engine path — those alerts have no associated
#     rule_id so the cooldown logic skips them entirely.
#
# Same idempotent ALTER-TABLE-in-try/except pattern as v4 / v5 / v6 /
# v13 / v14 / v16 / v17. The ``cooldown_minutes`` column lands on
# ``alert_rules`` (which is currently a thin two-column JSON-blob
# store, but a real column lets callers query "rules with cooldown >
# 0" without parsing every blob). The ``rule_id`` column is NULLable
# on purpose — distinguishing "alert came from a rule" (non-NULL)
# from "alert came from a detection helper" (NULL) at the SQL level
# matters because only the former participates in cooldown.
_SCHEMA_V18_NOTE: str = (
    "v18: alert_rules.cooldown_minutes INTEGER NOT NULL DEFAULT 0 + "
    "alerts.rule_id TEXT "
    "(added via ALTER TABLE in _migrate_to_v18)"
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
    # v7 add-only schema + per-table user_id column adds. Splits into:
    #   - the ``users`` CREATE TABLE IF NOT EXISTS (safe on every open)
    #   - the five ALTER TABLE column adds (each in its own try/except
    #     inside ``_migrate_to_v7`` for idempotency).
    conn.executescript(_SCHEMA_V7)
    try:
        from state.migrations import _migrate_to_v7
        _migrate_to_v7(conn)
    except Exception as exc:
        logger.warning(f"state.db: v7 column adds skipped: {exc}")
    # v8 add-only schema (CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
    # EXISTS) — safe to run on every open so fresh databases skip the
    # explicit migration step. The explicit ``_migrate_to_v8`` path in
    # state.migrations re-runs this script on upgrade.
    conn.executescript(_SCHEMA_V8)
    # v9 add-only schema (CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
    # EXISTS) — same pattern as v2 / v3 / v8. Persists slim ReportSnapshot
    # rows so the briefing-tab diff survives Streamlit restarts.
    conn.executescript(_SCHEMA_V9)
    # v10 add-only schema (CREATE TABLE IF NOT EXISTS + three indexes) —
    # same pattern. Adds the audit_events table for "who did what when"
    # security-review record-keeping.
    conn.executescript(_SCHEMA_V10)
    # v11 add-only schema (CREATE TABLE IF NOT EXISTS + two indexes) —
    # same pattern. Adds the api_tokens table for per-user API access
    # tokens (PATs) so external scripts can authenticate to future API
    # endpoints without needing the user's password.
    conn.executescript(_SCHEMA_V11)
    # v12 add-only schema (CREATE TABLE IF NOT EXISTS + two indexes) —
    # same pattern. Adds the data_source_health table so periodic feed
    # probes (FRED, yfinance, World Bank, etc.) leave a queryable
    # liveness/freshness record.
    conn.executescript(_SCHEMA_V12)
    # v13 column adds — same idempotent ALTER-TABLE-in-try/except pattern
    # as v4 / v5 / v6. Adds the quiet_start / quiet_end /
    # quiet_override_critical columns to delivery_channels. Safe to run
    # on every open (fresh DB: adds the columns; existing DB: no-op when
    # the columns already exist).
    try:
        from state.migrations import _migrate_to_v13
        _migrate_to_v13(conn)
    except Exception as exc:
        logger.warning(f"state.db: v13 column adds skipped: {exc}")
    # v14 column adds — same idempotent ALTER-TABLE-in-try/except pattern
    # as v4 / v5 / v6 / v13. Adds fire_count + last_fired_at to alerts
    # so the engine can collapse repeat fires of the same dedup_key
    # within a configurable window into a single row. Safe to run on
    # every open (fresh DB: adds the columns; existing DB: no-op when
    # the columns already exist).
    try:
        from state.migrations import _migrate_to_v14
        _migrate_to_v14(conn)
    except Exception as exc:
        logger.warning(f"state.db: v14 column adds skipped: {exc}")
    # v15 add-only schema (CREATE TABLE IF NOT EXISTS) — same pattern as
    # v2 / v3 / v8 / v9 / v10 / v11 / v12. Adds the user_settings table
    # so the platform has a place to persist per-user preferences
    # (timezone, theme, default report window, default alert severity
    # threshold, plus a free-form extras dict) without polluting the
    # domain tables.
    conn.executescript(_SCHEMA_V15)
    # v16 column adds — same idempotent ALTER-TABLE-in-try/except pattern
    # as v4 / v5 / v6 / v13 / v14. Adds the mfa_secret + mfa_enabled
    # columns to the users table so the platform can offer optional TOTP
    # MFA as a second factor on top of password login. Safe to run on
    # every open (fresh DB: adds the columns; existing DB: no-op when
    # the columns already exist).
    try:
        from state.migrations import _migrate_to_v16
        _migrate_to_v16(conn)
    except Exception as exc:
        logger.warning(f"state.db: v16 column adds skipped: {exc}")
    # v17 column adds — same idempotent ALTER-TABLE-in-try/except pattern
    # as v4 / v5 / v6 / v13 / v14 / v16. Adds public_password_hash +
    # public_password_salt to report_history so a public share link can
    # be guarded by an optional password (layered on top of the
    # unguessable v5 slug). NULL on both columns means "no password set",
    # which preserves the existing v5 behaviour.
    try:
        from state.migrations import _migrate_to_v17
        _migrate_to_v17(conn)
    except Exception as exc:
        logger.warning(f"state.db: v17 column adds skipped: {exc}")
    # v18 column adds — same idempotent ALTER-TABLE-in-try/except pattern
    # as v4 / v5 / v6 / v13 / v14 / v16 / v17. Adds alert_rules.
    # cooldown_minutes (per-rule cooldown that suppresses repeat fires of
    # the same rule_id within N minutes) and alerts.rule_id (stamped by
    # fire_rule so the cooldown query can ask "when did this rule last
    # fire?"). Both columns are safe to re-add on every open — the
    # OperationalError "duplicate column" is swallowed.
    try:
        from state.migrations import _migrate_to_v18
        _migrate_to_v18(conn)
    except Exception as exc:
        logger.warning(f"state.db: v18 column adds skipped: {exc}")

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

    # Migration 6 → 7: lay the foundation for multi-user auth. The
    # ``users`` table is created via ``_SCHEMA_V7`` (already executed
    # unconditionally above), and each domain table gains a nullable
    # ``user_id`` column via the ALTER-TABLE-in-try/except helper. As
    # with v4 / v5 / v6, the helper is also called unconditionally above
    # so a fresh DB picks up the columns at first open; this branch
    # keeps the version-step ladder explicit.
    if current < 7:
        try:
            from state.migrations import _migrate_to_v7
            _migrate_to_v7(conn)
        except Exception as exc:
            logger.warning(f"state.db: v7 migration skipped: {exc}")

    # Migration 7 → 8: add the ``tab_render_events`` table so the
    # platform can answer "which tabs are slow?" without a profiler.
    # Same CREATE-IF-NOT-EXISTS idempotency as v2 / v3 — the helper is
    # already invoked unconditionally above; this branch keeps the
    # version-step ladder explicit.
    if current < 8:
        try:
            from state.migrations import _migrate_to_v8
            _migrate_to_v8(conn)
        except Exception as exc:
            logger.warning(f"state.db: v8 migration skipped: {exc}")

    # Migration 8 → 9: add the ``investor_report_snapshots`` table so the
    # briefing tab "what changed" diff survives Streamlit restarts. Same
    # CREATE-IF-NOT-EXISTS idempotency — the helper is already invoked
    # unconditionally above; this branch keeps the version-step ladder
    # explicit.
    if current < 9:
        try:
            from state.migrations import _migrate_to_v9
            _migrate_to_v9(conn)
        except Exception as exc:
            logger.warning(f"state.db: v9 migration skipped: {exc}")

    # Migration 9 → 10: add the ``audit_events`` table so privileged
    # user actions leave a queryable security-review record. Same
    # CREATE-IF-NOT-EXISTS idempotency — the helper is already invoked
    # unconditionally above; this branch keeps the version-step ladder
    # explicit.
    if current < 10:
        try:
            from state.migrations import _migrate_to_v10
            _migrate_to_v10(conn)
        except Exception as exc:
            logger.warning(f"state.db: v10 migration skipped: {exc}")

    # Migration 10 → 11: add the ``api_tokens`` table for per-user API
    # access tokens (PATs). Same CREATE-IF-NOT-EXISTS idempotency — the
    # helper is already invoked unconditionally above; this branch keeps
    # the version-step ladder explicit.
    if current < 11:
        try:
            from state.migrations import _migrate_to_v11
            _migrate_to_v11(conn)
        except Exception as exc:
            logger.warning(f"state.db: v11 migration skipped: {exc}")

    # Migration 11 → 12: add the ``data_source_health`` table so the
    # platform can answer "is FRED degrading right now?" without
    # scrolling through logs. Same CREATE-IF-NOT-EXISTS idempotency —
    # the helper is already invoked unconditionally above; this branch
    # keeps the version-step ladder explicit.
    if current < 12:
        try:
            from state.migrations import _migrate_to_v12
            _migrate_to_v12(conn)
        except Exception as exc:
            logger.warning(f"state.db: v12 migration skipped: {exc}")

    # Migration 12 → 13: add the quiet-hours columns to
    # delivery_channels. Same idempotent ALTER-TABLE-in-try/except
    # pattern as v4 / v5 / v6 — the helper is already invoked
    # unconditionally above; this branch keeps the version-step ladder
    # explicit.
    if current < 13:
        try:
            from state.migrations import _migrate_to_v13
            _migrate_to_v13(conn)
        except Exception as exc:
            logger.warning(f"state.db: v13 migration skipped: {exc}")

    # Migration 13 → 14: add the alerts.fire_count + alerts.last_fired_at
    # columns so the alert engine can collapse repeat fires of the same
    # (alert_type, severity, ticker, route_id, port_locode) dedup_key
    # within the configurable _DEDUP_WINDOW_MINUTES into one row. Same
    # idempotent ALTER-TABLE-in-try/except pattern as v4 / v5 / v6 —
    # the helper is already invoked unconditionally above; this branch
    # keeps the version-step ladder explicit.
    if current < 14:
        try:
            from state.migrations import _migrate_to_v14
            _migrate_to_v14(conn)
        except Exception as exc:
            logger.warning(f"state.db: v14 migration skipped: {exc}")

    # Migration 14 → 15: add the ``user_settings`` table so per-user
    # preferences (timezone / theme / default report window / default
    # alert severity threshold / extras) survive across sessions
    # without polluting the domain tables. Same CREATE-IF-NOT-EXISTS
    # idempotency as v2 / v3 / v8 — the helper is already invoked
    # unconditionally above; this branch keeps the version-step ladder
    # explicit.
    if current < 15:
        try:
            from state.migrations import _migrate_to_v15
            _migrate_to_v15(conn)
        except Exception as exc:
            logger.warning(f"state.db: v15 migration skipped: {exc}")

    # Migration 15 → 16: add the users.mfa_secret + users.mfa_enabled
    # columns so accounts can opt in to TOTP MFA as a second factor on
    # top of the password login. Same idempotent ALTER-TABLE-in-try/
    # except pattern as v4 / v5 / v6 / v13 / v14 — the helper is
    # already invoked unconditionally above; this branch keeps the
    # version-step ladder explicit.
    if current < 16:
        try:
            from state.migrations import _migrate_to_v16
            _migrate_to_v16(conn)
        except Exception as exc:
            logger.warning(f"state.db: v16 migration skipped: {exc}")

    # Migration 16 → 17: add the report_history.public_password_hash +
    # report_history.public_password_salt columns so a public share
    # link can be guarded by an optional user-chosen password (layered
    # on top of the unguessable slug from v5). Same idempotent ALTER-
    # TABLE-in-try/except pattern as v4 / v5 / v6 / v13 / v14 / v16 —
    # the helper is already invoked unconditionally above; this branch
    # keeps the version-step ladder explicit.
    if current < 17:
        try:
            from state.migrations import _migrate_to_v17
            _migrate_to_v17(conn)
        except Exception as exc:
            logger.warning(f"state.db: v17 migration skipped: {exc}")

    # Migration 17 → 18: add the alert_rules.cooldown_minutes column
    # (per-rule cooldown that suppresses repeat fires of the same
    # rule_id within N minutes) + the alerts.rule_id column (stamped
    # by fire_rule). Same idempotent ALTER-TABLE-in-try/except pattern
    # as v4 / v5 / v6 / v13 / v14 / v16 / v17 — the helper is already
    # invoked unconditionally above; this branch keeps the version-step
    # ladder explicit.
    if current < 18:
        try:
            from state.migrations import _migrate_to_v18
            _migrate_to_v18(conn)
        except Exception as exc:
            logger.warning(f"state.db: v18 migration skipped: {exc}")

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO kv_state (key, value, updated_at) VALUES (?, ?, ?)",
        ("schema_version", str(SCHEMA_VERSION), now_iso),
    )
