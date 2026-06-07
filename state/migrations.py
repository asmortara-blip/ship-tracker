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


# ─── Schema v14 → v15 ─────────────────────────────────────────────────────

def _migrate_to_v15(conn: sqlite3.Connection) -> None:
    """Add the ``user_settings`` table for per-user preferences.

    The table holds one row per user (keyed by ``user_id``) carrying a
    JSON-encoded preferences blob (``settings_json``). Storing the prefs
    as JSON means a new preference can be added by bumping the
    ``auth.settings.UserSettings`` dataclass — no further schema bump
    required. Pre-v15 users have no row; ``auth.settings.get_settings``
    returns the defaults dataclass for unknown users so the absence-of-
    row case is invisible to callers.

    Idempotent — CREATE TABLE IF NOT EXISTS — so this statement is also
    safe to re-run on every open via the schema bootstrap in state.db.
    Matches the v2 / v3 / v8 / v9 / v10 / v11 / v12 pattern (add-only
    schema).
    """
    try:
        from state.db import _SCHEMA_V15

        conn.executescript(_SCHEMA_V15)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v15 CREATE TABLE failed: {exc}"
        )


# ─── Schema v15 → v16 ─────────────────────────────────────────────────────

def _migrate_to_v16(conn: sqlite3.Connection) -> None:
    """Add the ``mfa_secret`` and ``mfa_enabled`` columns to ``users``.

    These two columns power optional TOTP MFA as a second factor on top
    of the existing password login. Pre-v16 rows pick up the empty/0
    defaults so legacy accounts continue to log in with just the
    password — MFA is strictly opt-in per account.

    Columns added (both NOT NULL with sensible legacy defaults):

      * ``mfa_secret``  — ``TEXT NOT NULL DEFAULT ''``. Canonical
        32-char base32 secret (per RFC 4226 + RFC 6238) compatible with
        every standard authenticator app. Empty when MFA is not
        enabled. Generated by ``auth.mfa.generate_secret``.
      * ``mfa_enabled`` — ``INTEGER NOT NULL DEFAULT 0``. 0/1 flag.
        ``auth.users.login`` only requires the second factor when this
        column is 1 for the looked-up user. Flipped to 1 by
        ``auth.mfa.enable_mfa``; cleared to 0 by
        ``auth.mfa.disable_mfa``.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13`` /
    ``_migrate_to_v14``: SQLite does NOT support ``IF NOT EXISTS`` on
    ALTER TABLE, so each statement is wrapped in try/except and
    "duplicate column name" errors are swallowed. Each column is added
    in its own try/except so partial completion of a prior run is also
    tolerated.
    """
    for col_name, col_def in (
        ("mfa_secret", "TEXT NOT NULL DEFAULT ''"),
        ("mfa_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            logger.warning(
                f"state.migrations: _migrate_to_v16 ALTER TABLE "
                f"({col_name}) failed: {exc}"
            )


# ─── Schema v16 → v17 ─────────────────────────────────────────────────────

def _migrate_to_v17(conn: sqlite3.Connection) -> None:
    """Add the ``public_password_hash`` and ``public_password_salt``
    columns to ``report_history`` for optional password-gated public
    share links.

    Both columns are TEXT and NULLable (no ``NOT NULL DEFAULT ''``)
    on purpose: NULL means "no password set on this share link" —
    distinguishable at the SQL level from an empty-string password.
    Pre-v17 rows pick up NULL for both columns, so existing public
    links continue to work without a password (the v5 unguessable-
    slug-only behaviour is preserved).

    The hash is hex-encoded PBKDF2-HMAC-SHA256 with 200_000
    iterations (matching the rest of the auth/ KDF iteration
    count). The salt is hex-encoded random bytes generated by
    ``secrets.token_bytes``. Both are produced by the helpers in
    ``utils.report_history`` (``_hash_public_password`` /
    ``_verify_public_password``) at ``make_public`` time and consumed
    by ``verify_public_report_password`` / ``load_public_report``.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13`` /
    ``_migrate_to_v14`` / ``_migrate_to_v16``: SQLite does NOT support
    ``IF NOT EXISTS`` on ALTER TABLE, so each statement is wrapped in
    try/except and "duplicate column name" errors are swallowed. Each
    column is added in its own try/except so partial completion of a
    prior run is also tolerated.
    """
    for col_name, col_def in (
        ("public_password_hash", "TEXT"),
        ("public_password_salt", "TEXT"),
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
                f"state.migrations: _migrate_to_v17 ALTER TABLE "
                f"({col_name}) failed: {exc}"
            )


# ─── Schema v17 → v18 ─────────────────────────────────────────────────────

def _migrate_to_v18(conn: sqlite3.Connection) -> None:
    """Add per-rule cooldown support to the alert engine.

    Two columns land in this migration, on two different tables:

      * ``alert_rules.cooldown_minutes`` — ``INTEGER NOT NULL DEFAULT 0``.
        0 (the default) means "no cooldown, fire on every evaluation"
        — the pre-v18 behaviour, preserved for existing rules. A
        positive value N means a successful fire of this rule_id
        suppresses subsequent fires of the SAME rule_id for the next
        N minutes (per user). Cooldown is orthogonal to the v14
        time-window dedup_key collapse — both can apply on the same
        alert (cooldown stops the rule from firing in the first place;
        dedup collapses bounces of an alert that DID fire).

      * ``alerts.rule_id`` — ``TEXT`` (NULLable, no DEFAULT). The
        rule_id of the AlertRule that produced this alert, stamped
        by ``fire_rule``. NULLable on purpose: alerts that come from
        the detection helpers (``check_bdi_alerts`` / ``check_signal_alerts``
        / etc.) pre-date the rule-engine path and have no associated
        rule_id, and the cooldown lookup can use ``WHERE rule_id = ?``
        to skip those rows naturally. Distinguishing "from a rule"
        (non-NULL) from "from a detector" (NULL) at the SQL level is
        the design goal here.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13`` /
    ``_migrate_to_v14`` / ``_migrate_to_v16`` / ``_migrate_to_v17``:
    SQLite does NOT support ``IF NOT EXISTS`` on ALTER TABLE, so each
    statement is wrapped in try/except and "duplicate column name"
    errors are swallowed. Each column is added in its own try/except
    so partial completion of a prior run is also tolerated, and
    re-running on a fully-migrated DB is a no-op.
    """
    for table, col_name, col_def in (
        ("alert_rules", "cooldown_minutes", "INTEGER NOT NULL DEFAULT 0"),
        ("alerts",      "rule_id",          "TEXT"),
    ):
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            logger.warning(
                f"state.migrations: _migrate_to_v18 ALTER TABLE "
                f"({table}.{col_name}) failed: {exc}"
            )


# ─── Schema v18 → v19 ─────────────────────────────────────────────────────

def _migrate_to_v19(conn: sqlite3.Connection) -> None:
    """Add bulk-acknowledgement metadata to the ``alerts`` table.

    Two columns land in this migration, both on ``alerts`` and both
    NULLable on purpose so the SQL layer can distinguish "no note set"
    (NULL) from "empty-string note" (caller passed ``note=''``):

      * ``acknowledged_note`` — ``TEXT``. The free-form note an
        operator attaches when acking a single alert via
        :func:`acknowledge_alert` (now with the optional ``note`` kwarg)
        or a set of alerts via :func:`bulk_acknowledge_alerts`. The
        full note is persisted on the row; the audit-event ``detail``
        payload truncates to 200 chars so a long note does not bloat
        the audit log. NULL on every alert acked before v19 because
        the column is freshly added.
      * ``acknowledged_by_user_id`` — ``TEXT``. The ``user_id`` of the
        operator who acked the row. Auto-stamped on every ack call
        from the ``user_id`` kwarg (resolved via ``_resolve_user_id``
        so the active Streamlit user is picked up when nothing is
        passed explicitly). NULL on rows acked before v19 — callers
        that need attribution for those rows fall back to the
        ``audit_events`` log keyed by the alert's id.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13`` /
    ``_migrate_to_v14`` / ``_migrate_to_v16`` / ``_migrate_to_v17`` /
    ``_migrate_to_v18``: SQLite does NOT support ``IF NOT EXISTS`` on
    ALTER TABLE, so each statement is wrapped in try/except and
    "duplicate column name" errors are swallowed. Each column is added
    in its own try/except so partial completion of a prior run is also
    tolerated, and re-running on a fully-migrated DB is a no-op.
    """
    for col_name, col_def in (
        ("acknowledged_note", "TEXT"),
        ("acknowledged_by_user_id", "TEXT"),
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
                f"state.migrations: _migrate_to_v19 ALTER TABLE "
                f"(alerts.{col_name}) failed: {exc}"
            )


# ─── Schema v19 → v20 ─────────────────────────────────────────────────────

def _migrate_to_v20(conn: sqlite3.Connection) -> None:
    """Add the ``report_schedules`` table for cron-driven auto-generated
    reports.

    Each row carries a 5-field cron expression (parsed in-Python by
    ``engine.report_scheduler.parse_cron_expr`` — stdlib only, no
    croniter dependency), an enabled flag, and the bookkeeping
    columns the worker uses to pick "what's due now?" off an index
    (``(enabled, next_run_at)``).

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
    EXISTS — so this statement is also safe to re-run on every open
    via the schema bootstrap in state.db. Matches the v2 / v3 / v8 /
    v9 / v10 / v11 / v12 / v15 pattern (add-only schema).
    """
    try:
        from state.db import _SCHEMA_V20

        conn.executescript(_SCHEMA_V20)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v20 CREATE TABLE failed: {exc}"
        )


# ─── Schema v20 → v21 ─────────────────────────────────────────────────────

def _migrate_to_v21(conn: sqlite3.Connection) -> None:
    """Add the ``mfa_recovery_codes`` and ``user_invitations`` tables
    for the auth follow-on commit.

    Two add-only tables land in this migration. The v20 slot was held
    by a sibling agent's report_schedules migration, so this commit
    claims the next sequential slot per the task spec's coordination
    note:

      * ``mfa_recovery_codes`` — one row per single-use scratch code
        issued at MFA enrollment. Each row stores a pbkdf2-sha256 hash
        (200_000 iterations) of the plaintext code + a per-code random
        16-byte salt, the issuing ``user_id``, a NULLable ``used_at``
        ISO timestamp (flipped by
        ``auth.mfa.verify_and_consume_recovery_code`` on a match), and
        a ``created_at`` ISO timestamp. The plaintext codes themselves
        are NEVER persisted — they are returned to the caller of
        ``auth.mfa.generate_recovery_codes`` exactly once at creation
        time. The supporting index on ``(user_id, used_at)`` keeps the
        per-verify "unused codes for this user" query cheap even after
        many regeneration cycles.
      * ``user_invitations`` — one row per pre-authorized signup link
        an admin has created via
        ``auth.invitations.create_invitation``. Carries a random
        32-char URL-safe ``invite_token`` (UNIQUE) the recipient
        supplies to ``auth.users.signup``, an optional ``email`` field
        that locks the invite to a specific recipient (NULL = any
        email may consume), a ``role`` to grant on consumption
        (defaults to ``'user'`` so an invite cannot silently grant
        admin without being marked as such), the
        ``invited_by_user_id`` of the admin who issued the invite, an
        ISO ``expires_at`` timestamp, and a
        ``consumed_at`` / ``consumed_by_user_id`` pair flipped by
        ``consume_invitation`` once the signup completes.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
    EXISTS — so this statement is also safe to re-run on every open
    via the schema bootstrap in state.db. Matches the v2 / v3 / v8 /
    v9 / v10 / v11 / v12 / v15 / v20 pattern (add-only schema).
    """
    try:
        from state.db import _SCHEMA_V21

        conn.executescript(_SCHEMA_V21)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v21 CREATE TABLE failed: {exc}"
        )


# ─── Schema v21 → v22 ─────────────────────────────────────────────────────

def _migrate_to_v22(conn: sqlite3.Connection) -> None:
    """Add the ``alert_silences`` table for planned-downtime alert
    silencing.

    Each row carries:

      * ``silence_id``         — UUID PK.
      * ``user_id``            — per-user scope (alice cannot mute
        bob's alerts); the silence check inside ``fire_rule`` filters
        on this column so cross-user isolation is mechanical.
      * ``rule_id`` / ``ticker`` / ``severity`` — NULLable match
        keys. NULL on a column means "matches any value for this
        column"; non-NULL means "must equal exactly". The broadest
        silence (all three NULL) shuts up every alert for the user.
      * ``reason``             — free-form operator note ("Paused
        for FRED maintenance"). Persisted on the row; the silence
        gate logs the silence_id + reason at INFO level (NOT error)
        so the reason can carry operational context without
        spamming the error-log channel.
      * ``starts_at`` / ``expires_at`` — ISO-8601 UTC text. The
        silence is active iff ``starts_at <= now < expires_at``.
      * ``created_at`` / ``created_by_user_id`` — audit pair so an
        expired silence can still answer "who issued this and
        when?" during retention.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
    EXISTS — so this statement is also safe to re-run on every open
    via the schema bootstrap in state.db. Matches the v2 / v3 / v8 /
    v9 / v10 / v11 / v12 / v15 / v20 / v21 pattern (add-only schema).
    """
    try:
        from state.db import _SCHEMA_V22

        conn.executescript(_SCHEMA_V22)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v22 CREATE TABLE failed: {exc}"
        )


# ─── Schema v23 → v24 ─────────────────────────────────────────────────────

def _migrate_to_v24(conn: sqlite3.Connection) -> None:
    """Add the alert escalation chain machinery.

    Two halves on two different tables:

      * Two columns on ``alerts``:
          - ``last_escalated_at`` — ``TEXT`` (NULLable, no DEFAULT).
            ISO-8601 UTC timestamp of the most recent escalation step
            that fired for this alert. NULL on a never-escalated row.
            The escalation engine reads this column to compute "has
            step N+1's after_minutes window elapsed since the last
            step fired?" — when last_escalated_at is NULL the engine
            falls back to ``created_at`` so the first step measures
            from when the alert was first persisted.
          - ``escalation_step`` — ``INTEGER NOT NULL DEFAULT 0``.
            Tracks WHICH step of the chain has fired so far. 0 means
            "no step has fired yet; step 1 is next"; N means "step N
            fired; step N+1 is next (or the chain is exhausted if
            none exists)". Pre-v24 rows pick up the default 0,
            matching the implicit pre-feature meaning ("alert has
            never been escalated").

      * A new ``alert_escalation_chains`` table holding the per-rule
        chains. One row per step in the chain. Columns:
          - ``chain_id``     — UUID PK identifying this particular
            step row. NOT the rule_id; a chain is a SET of rows that
            share the same rule_id + user_id.
          - ``rule_id``      — the originating AlertRule. The chain
            walk groups rows by rule_id, ordered by step_number ASC.
          - ``user_id``      — per-user scoping. Alice's chain on
            rule X does NOT escalate bob's alerts on the same rule;
            the engine filters on this column for every read.
          - ``step_number``  — 1-indexed integer; 1 fires first, 2
            second, etc. The (rule_id, user_id, step_number) tuple
            is UNIQUE so add_escalation_step can REPLACE in place
            when an operator edits a single step.
          - ``after_minutes``— escalate when the alert has been
            unacked > this many minutes since the previous step's
            fire (or, for step 1, since the alert's created_at).
          - ``channel_id``   — the delivery channel to dispatch
            this step to. References ``delivery_channels.channel_id``
            by convention (no SQL FK so a channel can be soft-
            deleted without cascading the escalation row).
          - ``created_at``   — ISO-8601 UTC stamp at row write time.

    Two indexes accompany the table:
      * ``idx_escalation_rule`` on (rule_id, step_number) — covers
        the get_escalation_chain walk ("ordered by step_number ASC
        for this rule").
      * ``uq_escalation_step`` UNIQUE on (rule_id, user_id,
        step_number) — enforces "at most one row per step per user
        per rule" so add_escalation_step can REPLACE the existing
        row when an operator re-edits a step in place.

    The ALTER TABLE adds use the same idempotent OperationalError-
    swallowing pattern as v4 / v5 / v6 / v13 / v14 / v16 / v17 / v18
    / v19. The CREATE TABLE side is invoked via executescript and
    is idempotent on its own via CREATE TABLE IF NOT EXISTS / CREATE
    INDEX IF NOT EXISTS. Each column is added in its own try/except
    so partial completion of a prior run is also tolerated.
    """
    # First the two ALTER TABLE adds on ``alerts``. The columns are
    # NULLable / DEFAULT 0 on purpose so pre-v24 rows pick up
    # sensible "never escalated" semantics without a backfill.
    for col_name, col_def in (
        ("last_escalated_at", "TEXT"),
        ("escalation_step", "INTEGER NOT NULL DEFAULT 0"),
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
                f"state.migrations: _migrate_to_v24 ALTER TABLE "
                f"(alerts.{col_name}) failed: {exc}"
            )

    # Then the new ``alert_escalation_chains`` table. The CREATE
    # script is idempotent on its own (CREATE TABLE IF NOT EXISTS +
    # CREATE INDEX IF NOT EXISTS) so re-running on a fully-migrated
    # DB is a no-op.
    try:
        from state.db import _SCHEMA_V24

        conn.executescript(_SCHEMA_V24)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v24 CREATE TABLE failed: {exc}"
        )


# ─── Schema v24 → v25 ─────────────────────────────────────────────────────

def _migrate_to_v25(conn: sqlite3.Connection) -> None:
    """Add the ``monthly_budget`` column to ``delivery_channels``.

    Operators want to cap noisy channels — e.g. "Slack #trading-desk
    gets max 200 alerts/month; PagerDuty gets max 50". When the per-
    channel monthly counter hits the budget, further deliveries are
    suppressed (and counted in the ``budget_suppressed_counter``
    kv_state row) until the next calendar month rolls in.

    The column is INTEGER NOT NULL DEFAULT 0. ``0`` is sentinel for
    "no budget — unlimited" so pre-v25 rows pick up the default and
    behave EXACTLY as today; the budget machinery only kicks in once
    an operator opts a channel in by setting a positive cap.

    Per-channel usage is NOT a column on this table — it lives in
    ``kv_state`` under the key
    ``channel_usage:<user_id>:<channel_id>:<YYYY-MM>`` so the same
    row can be reset / inspected per calendar month without bloating
    the channels table.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v6`` / ``_migrate_to_v13``:
    SQLite does NOT support ``IF NOT EXISTS`` on ALTER TABLE, so we
    wrap the statement in try/except and treat
    ``OperationalError: duplicate column name`` as a no-op. This
    makes the helper safe to re-run on every database open.
    """
    try:
        conn.execute(
            "ALTER TABLE delivery_channels ADD COLUMN "
            "monthly_budget INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        logger.warning(
            f"state.migrations: _migrate_to_v25 ALTER TABLE failed: {exc}"
        )


# ─── Schema v25 → v26 ─────────────────────────────────────────────────────

def _migrate_to_v26(conn: sqlite3.Connection) -> None:
    """Add the ``delivery_retry_queue`` table for the retry-queue machinery.

    When an outbound dispatch fails with a retriable transport error
    (HTTP 5xx, network timeout, SMTP blip), the alert previously stayed
    in ``alerts`` but the outgoing delivery was effectively lost — the
    failure was logged once and never re-attempted. v26 adds a
    persistent queue so a transient blip is retried on the next worker
    pass with exponential backoff (60s → 120s → 240s → 480s → 960s)
    and finally marked permanently-failed after MAX_RETRIES exhaustion.

    The CREATE TABLE script is idempotent on its own (CREATE TABLE IF
    NOT EXISTS + CREATE INDEX IF NOT EXISTS) so re-running on a fully-
    migrated DB is a no-op — matches the v2 / v3 / v8 / v9 / v10 / v11
    / v12 / v15 / v20 / v21 / v22 / v23 add-only pattern.
    """
    try:
        from state.db import _SCHEMA_V26

        conn.executescript(_SCHEMA_V26)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v26 CREATE TABLE failed: {exc}"
        )


# ─── Schema v22 → v23 ─────────────────────────────────────────────────────

def _migrate_to_v23(conn: sqlite3.Connection) -> None:
    """Add the ``alert_annotations`` table for per-alert operator
    commentary threads.

    Pre-v23 the only writable field on an alert was
    ``acknowledged_note`` (a single string, set once at ack). v23
    adds an unbounded per-alert thread that the ops team can edit
    and delete — limited to the original author so cross-operator
    audit trails stay intact.

    Each row carries:

      * ``annotation_id``  — UUID PK.
      * ``alert_id``       — the alert the comment belongs to.
        Convention-only reference (no FOREIGN KEY) so the delete
        policy stays loose — matches the audit_events pattern.
      * ``user_id``        — the OWNER of the alert. Per-user
        scoping means alice cannot see bob's alert annotations;
        the column is filtered on every read.
      * ``author_user_id`` — who actually WROTE this comment.
        Usually equals ``user_id`` but a multi-user-share workflow
        may differ (a teammate granted shared visibility leaves a
        note on someone else's alert). Edit / delete authorisation
        matches this column.
      * ``body``           — free-form TEXT. The engine layer
        silently truncates at 4000 chars on write. Stored
        VERBATIM — UI is responsible for safe rendering.
      * ``created_at``     — ISO-8601 UTC stamp at write time.
      * ``edited_at``      — NULLable ISO-8601 UTC stamp. NULL on
        never-edited rows; flipped to NOW by edit_annotation on a
        successful author-match edit.

    Idempotent — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
    EXISTS — so this statement is also safe to re-run on every open
    via the schema bootstrap in state.db. Matches the v2 / v3 / v8 /
    v9 / v10 / v11 / v12 / v15 / v20 / v21 / v22 pattern (add-only
    schema).
    """
    try:
        from state.db import _SCHEMA_V23

        conn.executescript(_SCHEMA_V23)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v23 CREATE TABLE failed: {exc}"
        )


# ─── Schema v26 → v27 ─────────────────────────────────────────────────────

def _migrate_to_v27(conn: sqlite3.Connection) -> None:
    """Add the ``expires_at`` column to ``api_tokens`` (PAT expiry / TTL).

    API tokens previously never expired — a leaked PAT stayed valid until it
    was explicitly revoked. v27 adds an optional expiry: ``create_token``
    stamps an ISO-8601 UTC ``expires_at`` (default 90 days, env-tunable via
    ``API_TOKEN_TTL_DAYS``; 0 ⇒ non-expiring) and ``verify_token`` rejects a
    token whose ``expires_at`` is in the past.

    The column is ``TEXT NOT NULL DEFAULT ''`` and an EMPTY string means
    "never expires", so every PRE-v27 token is grandfathered as non-expiring
    — the expiry only applies to tokens minted after this upgrade. Same
    idempotent ALTER TABLE pattern as ``_migrate_to_v4`` / ``_migrate_to_v5``
    / ``_migrate_to_v25``: SQLite has no ``IF NOT EXISTS`` on ALTER TABLE, so
    we swallow ``OperationalError: duplicate column name`` to stay safe to
    re-run on every database open.
    """
    try:
        conn.execute(
            "ALTER TABLE api_tokens ADD COLUMN "
            "expires_at TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        logger.warning(
            f"state.migrations: _migrate_to_v27 ALTER TABLE failed: {exc}"
        )


# ─── Schema v27 → v28 ─────────────────────────────────────────────────────

def _migrate_to_v28(conn: sqlite3.Connection) -> None:
    """Add ``users.mfa_last_used_step`` for TOTP replay protection.

    A TOTP code is valid for its whole ±window (≈90s at window=1), so a
    captured code could be replayed until it ages out. v28 records the
    highest TOTP time-step (``floor(unix_time / period)``) the account has
    ever authenticated with; ``auth.users.login`` rejects any code whose
    matched step is ``<=`` the stored value, making each code single-use and
    forbidding an older in-window code after a newer one.

    The column is ``INTEGER NOT NULL DEFAULT -1`` — ``-1`` is the "no step
    consumed yet" sentinel (real steps are large positive integers, and even
    a test using ``when=0`` lands on step 0 > -1, so the first login always
    passes). Same idempotent ALTER TABLE pattern as ``_migrate_to_v16`` /
    ``_migrate_to_v25`` / ``_migrate_to_v27``: SQLite has no ``IF NOT
    EXISTS`` on ALTER TABLE, so we swallow ``OperationalError: duplicate
    column name`` to stay safe to re-run on every database open.
    """
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN "
            "mfa_last_used_step INTEGER NOT NULL DEFAULT -1"
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        logger.warning(
            f"state.migrations: _migrate_to_v28 ALTER TABLE failed: {exc}"
        )


# ─── Schema v28 → v29 ─────────────────────────────────────────────────────

def _migrate_to_v29(conn: sqlite3.Connection) -> None:
    """Add the ``positions`` table — a durable, per-user position ledger.

    The portfolio book previously lived only in ``st.session_state`` seeded
    from a hardcoded default list, so it evaporated on refresh and was
    identical for every user. v29 persists positions per ``user_id`` with a
    point-in-time history: an edit closes the prior open rows (stamps
    ``closed_at``) and inserts the new set at ``version + 1`` rather than
    overwriting, so the book is reconstructable as-of any past write.

    The CREATE TABLE script is idempotent on its own (CREATE TABLE IF NOT
    EXISTS + CREATE INDEX IF NOT EXISTS) so re-running on a fully-migrated DB
    is a no-op — matches the v2 / v8 / v26 add-only pattern.
    """
    try:
        from state.db import _SCHEMA_V29

        conn.executescript(_SCHEMA_V29)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v29 CREATE TABLE failed: {exc}"
        )


# ─── Schema v29 → v30 ─────────────────────────────────────────────────────

def _migrate_to_v30(conn: sqlite3.Connection) -> None:
    """Add ``users.is_active`` for the account disable/suspend kill-switch.

    An admin (or ops) can disable an account; ``auth.users.login`` and
    ``auth.tokens.verify_token`` then reject it, cutting off both interactive
    logins and API tokens without deleting the row (so the audit trail and any
    owned data stay intact). The column is ``INTEGER NOT NULL DEFAULT 1`` —
    ``1`` = active, so every pre-v30 account is grandfathered active. Same
    idempotent ALTER pattern as ``_migrate_to_v27`` / ``_migrate_to_v28``:
    SQLite has no ``IF NOT EXISTS`` on ALTER TABLE, so we swallow the
    duplicate-column error to stay safe to re-run on every database open.
    """
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        logger.warning(
            f"state.migrations: _migrate_to_v30 ALTER TABLE failed: {exc}"
        )


# ─── Schema v30 → v31 ─────────────────────────────────────────────────────

def _migrate_to_v31(conn: sqlite3.Connection) -> None:
    """Add ``audit_events.prev_hash`` + ``row_hash`` for the audit hash-chain.

    Each new audit row commits to all prior rows via
    ``row_hash = SHA-256(prev_hash || canonical(row))`` so an in-place edit,
    deletion, reorder, or insertion of an audit row breaks the chain and is
    detectable by ``engine.audit_search.verify_chain``. Both columns default
    to ``''`` so pre-v31 rows are grandfathered as ``unchained`` (the chain
    starts at the first v31 row). Same idempotent ALTER pattern as
    ``_migrate_to_v27`` / ``_migrate_to_v28`` / ``_migrate_to_v30``: each ADD
    is independent and swallows the duplicate-column error so the migration is
    safe to re-run on every open.
    """
    for col in ("prev_hash", "row_hash"):
        try:
            conn.execute(
                f"ALTER TABLE audit_events ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            logger.warning(
                f"state.migrations: _migrate_to_v31 ALTER TABLE ({col}) failed: {exc}"
            )


# ─── Schema v31 → v32 ─────────────────────────────────────────────────────

def _migrate_to_v32(conn: sqlite3.Connection) -> None:
    """Add the ``signal_ledger`` table — a point-in-time track record.

    Each row is one ``EquityIdea`` frozen AS ISSUED (ticker, direction,
    conviction, weight_set, issue_date, issue_close), never refit. Marked
    forward on real closes, this gives an honest, look-ahead-free equity-idea
    track record — the thing the platform admits it has never had. Idempotent
    CREATE TABLE / CREATE INDEX IF NOT EXISTS (same add-only pattern as
    v26/v29), safe to re-run on every open.
    """
    try:
        from state.db import _SCHEMA_V32

        conn.executescript(_SCHEMA_V32)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v32 CREATE TABLE failed: {exc}"
        )


def _migrate_to_v33(conn: sqlite3.Connection) -> None:
    """Add the ``data_fetches`` table — a per-fetch provenance ledger.

    Each row stamps one feed fetch with its realness (kind: live/cache/
    synthetic/empty), quality, row_count, a short content hash and the data's
    as-of, so an auditor can prove which feeds were real when a signal issued.
    Idempotent CREATE TABLE / CREATE INDEX IF NOT EXISTS (same add-only pattern
    as v26/v29/v32), safe to re-run on every open.
    """
    try:
        from state.db import _SCHEMA_V33

        conn.executescript(_SCHEMA_V33)
    except Exception as exc:
        logger.warning(
            f"state.migrations: _migrate_to_v33 CREATE TABLE failed: {exc}"
        )


# ─── Schema v33 → v34 ─────────────────────────────────────────────────────

def _migrate_to_v34(conn: sqlite3.Connection) -> None:
    """Add ``report_version`` + ``supersedes_id`` to ``report_history`` for
    immutable report version / supersede-amend lineage (R119).

    A report can no longer be edited in place — an amendment mints a NEW
    immutable ``report_history`` row that LINKS back to its predecessor, so
    the full version chain stays auditable. Two columns land in this
    migration, both on ``report_history``:

      * ``report_version`` — ``INTEGER NOT NULL DEFAULT 1``. The 1-indexed
        position of this row in its lineage. Pre-v34 rows (and every
        first-issue report) pick up the default 1, which matches the
        implicit pre-feature meaning ("this is the original, un-amended
        report"). An amendment writes ``previous.report_version + 1``.
      * ``supersedes_id`` — ``TEXT`` (NULLable, no DEFAULT). The
        ``report_id`` of the immediately-prior version this row supersedes.
        NULL on purpose for a v1 / original report: at the SQL level a NULL
        ``supersedes_id`` is the unambiguous "this is the head of the
        chain" marker, distinguishable from an empty-string id. The
        version-chain walk follows this column backwards (newest → oldest)
        and terminates at the NULL.

    Same idempotent ALTER TABLE pattern as ``_migrate_to_v4`` /
    ``_migrate_to_v5`` / ``_migrate_to_v17`` / ``_migrate_to_v18``: SQLite
    does NOT support ``IF NOT EXISTS`` on ALTER TABLE, so each statement is
    wrapped in try/except and "duplicate column name" errors are swallowed.
    Each column is added in its own try/except so partial completion of a
    prior run is also tolerated, and re-running on a fully-migrated DB is a
    no-op.
    """
    for col_name, col_def in (
        ("report_version", "INTEGER NOT NULL DEFAULT 1"),
        ("supersedes_id", "TEXT"),
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
                f"state.migrations: _migrate_to_v34 ALTER TABLE "
                f"({col_name}) failed: {exc}"
            )
