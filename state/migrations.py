"""One-time migration helpers for the SQLite state layer.

When the SQLite database is created for the first time, ``state.db._init_schema``
calls ``migrate_legacy_json_files()`` to import any existing JSON-file
persistence into the new tables. The legacy JSON files are NOT deleted —
they remain on disk as a safety net during the transition.

Each migration function:
  - Is idempotent (running it twice has no extra effect — INSERT OR
    IGNORE / INSERT OR REPLACE).
  - Catches its own exceptions and logs them rather than raising, so a
    malformed legacy file doesn't block the schema initialization.

This module imports lazily from ``engine.alert_engine_v2`` and
``utils.report_history`` for their legacy path constants, so it must
not be imported at module-load time of those modules (avoiding cycles).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from loguru import logger


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def migrate_legacy_json_files(conn: sqlite3.Connection) -> None:
    """Best-effort import of legacy JSON persistence into the SQLite DB.

    Idempotent — uses INSERT OR IGNORE so re-running on the same DB
    (which shouldn't happen, but defensively) does not duplicate rows."""
    _migrate_alerts_json(conn)
    _migrate_rules_json(conn)
    _migrate_report_history_index(conn)


# ─── Alerts ────────────────────────────────────────────────────────────────

_ALERTS_JSON = _PROJECT_ROOT / "cache" / "alerts" / "alerts.json"


def _migrate_alerts_json(conn: sqlite3.Connection) -> None:
    if not _ALERTS_JSON.exists():
        return
    try:
        with _ALERTS_JSON.open("r", encoding="utf-8") as fh:
            records = json.load(fh)
    except Exception as exc:
        logger.warning(f"state.migrations: failed to read {_ALERTS_JSON}: {exc}")
        return
    if not isinstance(records, list):
        return

    rows = []
    for rec in records:
        if not isinstance(rec, dict) or "alert_id" not in rec:
            continue
        rows.append((
            rec.get("alert_id"),
            rec.get("created_at", ""),
            rec.get("alert_type", "MACRO"),
            rec.get("severity", "LOW"),
            rec.get("title", ""),
            rec.get("body", ""),
            rec.get("ticker", "") or "",
            rec.get("route_id", "") or "",
            rec.get("port_locode", "") or "",
            float(rec.get("value", 0.0) or 0.0),
            float(rec.get("threshold", 0.0) or 0.0),
            float(rec.get("change_pct", 0.0) or 0.0),
            1 if rec.get("acknowledged") else 0,
        ))

    if rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO alerts
              (alert_id, created_at, alert_type, severity, title, body,
               ticker, route_id, port_locode, value, threshold, change_pct,
               acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        logger.info(f"state.migrations: imported {len(rows)} alerts from {_ALERTS_JSON}")


# ─── Alert rules ──────────────────────────────────────────────────────────

_RULES_JSON = _PROJECT_ROOT / "cache" / "alerts" / "rules.json"


def _migrate_rules_json(conn: sqlite3.Connection) -> None:
    if not _RULES_JSON.exists():
        return
    try:
        with _RULES_JSON.open("r", encoding="utf-8") as fh:
            rules = json.load(fh)
    except Exception as exc:
        logger.warning(f"state.migrations: failed to read {_RULES_JSON}: {exc}")
        return
    if not isinstance(rules, list):
        return

    rows = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        rule_id = r.get("rule_id") or r.get("id")
        if not rule_id:
            continue
        rows.append((str(rule_id), json.dumps(r, default=str)))

    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO alert_rules (rule_id, data) VALUES (?, ?)",
            rows,
        )
        logger.info(f"state.migrations: imported {len(rows)} alert rules from {_RULES_JSON}")


# ─── Report history index ─────────────────────────────────────────────────

_REPORT_INDEX_JSON = _PROJECT_ROOT / "cache" / "reports" / "report_index.json"


def _migrate_report_history_index(conn: sqlite3.Connection) -> None:
    if not _REPORT_INDEX_JSON.exists():
        return
    try:
        with _REPORT_INDEX_JSON.open("r", encoding="utf-8") as fh:
            records = json.load(fh)
    except Exception as exc:
        logger.warning(f"state.migrations: failed to read {_REPORT_INDEX_JSON}: {exc}")
        return
    if not isinstance(records, list):
        return

    rows = []
    for rec in records:
        if not isinstance(rec, dict) or "report_id" not in rec:
            continue
        rows.append((
            rec.get("report_id"),
            rec.get("generated_at", ""),
            rec.get("report_date", "") or "",
            rec.get("sentiment_label", "") or "",
            float(rec.get("sentiment_score", 0.0) or 0.0),
            rec.get("risk_level", "") or "",
            int(rec.get("signal_count", 0) or 0),
            rec.get("data_quality", "") or "",
            rec.get("file_path", ""),
            float(rec.get("file_size_kb", 0.0) or 0.0),
        ))

    if rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        logger.info(
            f"state.migrations: imported {len(rows)} reports from {_REPORT_INDEX_JSON}"
        )


# ─── Schema v1 → v2 ───────────────────────────────────────────────────────

def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Add the delivery_channels table for external alert delivery.

    Idempotent — uses CREATE TABLE IF NOT EXISTS so this can be re-run
    safely (the schema bootstrap in state.db also runs the same statement
    via ``_SCHEMA_V2`` so a fresh DB never needs this helper, but we still
    register it as the explicit upgrade path from v1).
    """
    from state.db import _SCHEMA_V2

    conn.executescript(_SCHEMA_V2)


# ─── Schema v2 → v3 ───────────────────────────────────────────────────────

def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """Add the llm_calls table for LLM cost telemetry.

    Each successful Anthropic API call writes one row here (commentary
    and narration paths today; new sources may join later). Idempotent —
    CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS — so this
    statement is also safe to re-run on every open via the schema
    bootstrap in state.db.
    """
    from state.db import _SCHEMA_V3

    conn.executescript(_SCHEMA_V3)


# ─── Schema v3 → v4 ───────────────────────────────────────────────────────

def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Add the ``acknowledged_at`` column to the alerts table.

    The column is a TEXT default-empty-string field that the engine
    sets to the current ISO timestamp whenever an alert is acked. Pre-
    v4 rows keep an empty string here — the analytics module treats
    those as "no ack timestamp available" and excludes them from the
    median time-to-ack metric (rather than imputing a fake timestamp).

    SQLite's ``ALTER TABLE ADD COLUMN`` does NOT accept ``IF NOT EXISTS``,
    so this helper wraps the statement in try/except and treats
    ``OperationalError: duplicate column name`` as a no-op. That makes
    the helper safe to re-run on every database open — both fresh DBs
    (where the column needs to be added because v1 schema did not have
    it) and already-upgraded DBs (where the column is already present).
    """
    try:
        conn.execute(
            "ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError as exc:
        # Idempotent path — SQLite raises "duplicate column name:
        # acknowledged_at" when the column already exists. Any other
        # OperationalError is unexpected; log it and continue rather
        # than blocking schema initialization.
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        logger.warning(f"state.migrations: _migrate_to_v4 ALTER TABLE failed: {exc}")


# ─── Schema v4 → v5 ───────────────────────────────────────────────────────

def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Add the public-share-link columns to ``report_history``.

    Two TEXT columns, both default-empty-string:

      * ``public_slug``       — URL-safe base64url token from
        ``secrets.token_urlsafe(12)``. Empty when the report has not
        been shared. Looked up by ``load_public_report(slug)``.
      * ``public_expires_at`` — ISO-8601 UTC timestamp (also empty when
        not shared). The link is treated as valid only while this is
        strictly in the future.

    Pre-v5 rows keep empty strings for both columns. Same idempotent
    ALTER TABLE pattern as ``_migrate_to_v4``: we swallow
    ``OperationalError: duplicate column name`` so the helper is safe to
    re-run on every database open. Each column is added in its own
    try/except so partial completion of a prior run is also tolerated.
    """
    for col_name, col_def in (
        ("public_slug", "TEXT NOT NULL DEFAULT ''"),
        ("public_expires_at", "TEXT NOT NULL DEFAULT ''"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE report_history ADD COLUMN {col_name} {col_def}"
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            logger.warning(
                f"state.migrations: _migrate_to_v5 ALTER TABLE "
                f"({col_name}) failed: {exc}"
            )


# ─── Schema v5 → v6 ───────────────────────────────────────────────────────

def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    """Add the ``digest_mode`` column to ``delivery_channels``.

    The column is a TEXT field defaulting to ``'immediate'`` (the only
    value pre-v6 rows could implicitly have had). Valid values:

      * ``'immediate'`` — one delivery per alert. Matches the original
        behaviour of ``deliver_pending`` so existing channels keep
        behaving the same way after the upgrade.
      * ``'daily'``     — batch every eligible alert into a single
        digest delivery each time ``deliver_pending`` runs. The
        actual scheduling cadence is the caller's responsibility
        (cron / worker); this column only flips ``deliver_pending``
        between per-alert loops and one-shot digest mode.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5``: SQLite does NOT support ``IF NOT EXISTS`` on
    ALTER TABLE, so we wrap the statement in try/except and treat
    ``OperationalError: duplicate column name`` as a no-op. This makes
    the helper safe to re-run on every database open.
    """
    try:
        conn.execute(
            "ALTER TABLE delivery_channels ADD COLUMN "
            "digest_mode TEXT NOT NULL DEFAULT 'immediate'"
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        logger.warning(
            f"state.migrations: _migrate_to_v6 ALTER TABLE failed: {exc}"
        )


# ─── Schema v6 → v7 ───────────────────────────────────────────────────────

def _migrate_to_v7(conn: sqlite3.Connection) -> None:
    """Add a nullable ``user_id`` column to each of the five existing
    domain tables (alerts, alert_rules, report_history, delivery_channels,
    llm_calls).

    The ``users`` table itself is created via the ``_SCHEMA_V7`` script
    in ``state.db._init_schema`` (CREATE TABLE IF NOT EXISTS, so it is
    safe to re-run on every open). This helper handles only the column
    adds, which SQLite cannot wrap in IF NOT EXISTS — each ALTER lives
    in its own try/except so partial completion of a prior run is
    tolerated, and re-running on a fully-migrated DB is a no-op.

    The new column is defined as ``TEXT NOT NULL DEFAULT ''`` so legacy
    rows belong to "no user" and remain visible under the existing
    single-password gate. Per-user query scoping is left for a follow-up
    — this migration is intentionally non-invasive.
    """
    targets = (
        "alerts",
        "alert_rules",
        "report_history",
        "delivery_channels",
        "llm_calls",
    )
    for table in targets:
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                "user_id TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            logger.warning(
                f"state.migrations: _migrate_to_v7 ALTER TABLE "
                f"({table}.user_id) failed: {exc}"
            )


# ─── Schema v7 → v8 ───────────────────────────────────────────────────────

def _migrate_to_v8(conn: sqlite3.Connection) -> None:
    """Add the ``tab_render_events`` table for per-tab render telemetry.

    Every tab render() that opts into ``engine.perf_telemetry.track_render``
    writes one row here. The platform reads the table to answer "which
    tabs are slow?" without firing up a profiler.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS —
    so this statement is also safe to re-run on every open via the schema
    bootstrap in state.db. Matches the v2 / v3 pattern (add-only schema).
    """
    try:
        from state.db import _SCHEMA_V8

        conn.executescript(_SCHEMA_V8)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v8 CREATE TABLE failed: {exc}"
        )


# ─── Schema v8 → v9 ───────────────────────────────────────────────────────

def _migrate_to_v9(conn: sqlite3.Connection) -> None:
    """Add the ``investor_report_snapshots`` table.

    Persists the slim ReportSnapshot dataclass (defined in
    ``processing.report_snapshot``) so the briefing tab "what changed"
    diff has prior snapshots to diff against after a Streamlit restart.

    Each row is a JSON-encoded payload of the slim snapshot — never the
    full InvestorReport, which carries pandas DataFrames and would not
    round-trip cleanly. The diff helper only reads a handful of
    attribute paths anyway (sentiment.overall_score, alpha.signals,
    market.risk_level, freight.routes), so the slim shape is sufficient.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS —
    so this statement is also safe to re-run on every open via the schema
    bootstrap in state.db. Matches the v2 / v3 / v8 pattern (add-only
    schema).
    """
    try:
        from state.db import _SCHEMA_V9

        conn.executescript(_SCHEMA_V9)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v9 CREATE TABLE failed: {exc}"
        )


# ─── Schema v9 → v10 ──────────────────────────────────────────────────────

def _migrate_to_v10(conn: sqlite3.Connection) -> None:
    """Add the ``audit_events`` table for security-review audit logging.

    Each privileged user action (alert ack, rule edit, channel CRUD,
    report deletion, share-link generation, signup/login) writes one
    row here via ``auth.audit.record_audit``. The table is intentionally
    wide-and-flat: ``action`` + ``entity_type`` + ``entity_id`` + a
    free-form ``detail_json`` payload absorbs new touchpoints without
    requiring another migration each time.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS —
    so this statement is also safe to re-run on every open via the schema
    bootstrap in state.db. Matches the v2 / v3 / v8 / v9 pattern
    (add-only schema).
    """
    try:
        from state.db import _SCHEMA_V10

        conn.executescript(_SCHEMA_V10)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v10 CREATE TABLE failed: {exc}"
        )


# ─── Schema v10 → v11 ─────────────────────────────────────────────────────

def _migrate_to_v11(conn: sqlite3.Connection) -> None:
    """Add the ``api_tokens`` table for per-user API access tokens (PATs).

    Each row stores a hashed-and-salted token (reusing ``auth.gate``'s
    KDF — same scrypt-with-PBKDF2-fallback as the password layer), an
    8-char plaintext prefix for O(log n) verify lookups via an index,
    a user-supplied label, created_at / last_used_at timestamps, and a
    revoked flag. The raw secret is returned exactly once at creation
    time and is NEVER written to disk in plaintext.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS —
    so this statement is also safe to re-run on every open via the schema
    bootstrap in state.db. Matches the v2 / v3 / v8 / v9 / v10 pattern
    (add-only schema).
    """
    try:
        from state.db import _SCHEMA_V11

        conn.executescript(_SCHEMA_V11)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v11 CREATE TABLE failed: {exc}"
        )


# ─── Schema v11 → v12 ─────────────────────────────────────────────────────

def _migrate_to_v12(conn: sqlite3.Connection) -> None:
    """Add the ``data_source_health`` table for periodic feed liveness probes.

    Each ping_source / ping_all_sources invocation writes one row here
    carrying ``ping_id`` (uuid), ``source`` (e.g. 'fred', 'yfinance',
    'worldbank'), ``started_at`` (ISO UTC), ``duration_ms`` (wall-clock
    via time.perf_counter), ``status`` (one of 'up' | 'degraded' |
    'down'), and a free-form ``error_msg`` (empty on success). The
    platform reads the table to answer "is FRED degrading right now?"
    without scrolling through logs.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS —
    so this statement is also safe to re-run on every open via the schema
    bootstrap in state.db. Matches the v2 / v3 / v8 / v9 / v10 / v11
    pattern (add-only schema).
    """
    try:
        from state.db import _SCHEMA_V12

        conn.executescript(_SCHEMA_V12)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v12 CREATE TABLE failed: {exc}"
        )


# ─── Schema v12 → v13 ─────────────────────────────────────────────────────

def _migrate_to_v13(conn: sqlite3.Connection) -> None:
    """Add the three quiet-hours columns to ``delivery_channels``.

    Columns added (all NOT NULL, all with sensible legacy defaults so
    pre-v13 rows preserve their old "no quiet hours" behaviour):

      * ``quiet_start``               — ``TEXT NOT NULL DEFAULT ''``.
        HH:MM UTC string; empty disables the quiet window.
      * ``quiet_end``                 — ``TEXT NOT NULL DEFAULT ''``.
        HH:MM UTC string; empty disables the quiet window.
      * ``quiet_override_critical``   — ``INTEGER NOT NULL DEFAULT 1``.
        When 1, a CRITICAL alert bypasses the quiet window; when 0, even
        CRITICAL alerts are suppressed during the window.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v6``: SQLite does NOT support
    ``IF NOT EXISTS`` on ALTER TABLE, so each statement is wrapped in
    try/except and "duplicate column name" errors are swallowed. This
    makes the helper safe to re-run on every database open. Each column
    is added in its own try/except so partial completion of a prior run
    is also tolerated.
    """
    for col_name, col_def in (
        ("quiet_start", "TEXT NOT NULL DEFAULT ''"),
        ("quiet_end", "TEXT NOT NULL DEFAULT ''"),
        ("quiet_override_critical", "INTEGER NOT NULL DEFAULT 1"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE delivery_channels ADD COLUMN {col_name} {col_def}"
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            logger.warning(
                f"state.migrations: _migrate_to_v13 ALTER TABLE "
                f"({col_name}) failed: {exc}"
            )


# ─── Schema v13 → v14 ─────────────────────────────────────────────────────

def _migrate_to_v14(conn: sqlite3.Connection) -> None:
    """Add the ``fire_count`` and ``last_fired_at`` columns to ``alerts``.

    These two columns power time-window alert deduplication in
    ``engine.alert_engine_v2.save_alerts``. When the same dedup_key
    (``alert_type`` + ``severity`` + ``ticker`` + ``route_id`` +
    ``port_locode``) fires multiple times within the configured
    ``_DEDUP_WINDOW_MINUTES`` (default 60), the engine UPDATEs the
    existing row's ``fire_count`` and ``last_fired_at`` instead of
    inserting a new row — a flaky data feed that bounces a value
    across its threshold N times in an hour leaves one row, not N.

    Columns added (both NOT NULL with sensible legacy defaults so
    pre-v14 rows preserve their old meaning):

      * ``fire_count``    — ``INTEGER NOT NULL DEFAULT 1``. Pre-v14
        rows pick up 1, which matches the implicit pre-feature meaning
        ("this alert fired once").
      * ``last_fired_at`` — ``TEXT NOT NULL DEFAULT ''``. ISO-8601 UTC.
        Pre-v14 rows pick up the empty string; callers that surface
        the value in the UI should fall back to ``created_at`` when
        ``last_fired_at`` is empty.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13``:
    SQLite does NOT support ``IF NOT EXISTS`` on ALTER TABLE, so each
    statement is wrapped in try/except and "duplicate column name"
    errors are swallowed. Each column is added in its own try/except
    so partial completion of a prior run is also tolerated.
    """
    for col_name, col_def in (
        ("fire_count", "INTEGER NOT NULL DEFAULT 1"),
        ("last_fired_at", "TEXT NOT NULL DEFAULT ''"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE alerts ADD COLUMN {col_name} {col_def}"
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            logger.warning(
                f"state.migrations: _migrate_to_v14 ALTER TABLE "
                f"({col_name}) failed: {exc}"
            )
