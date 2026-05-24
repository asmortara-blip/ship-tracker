"""``python -m tools.db_check_cli`` — DB integrity check CLI for the
Ship Tracker SQLite state database.

Operators run this after a crash, before a backup, or during incident
response to verify that ``cache/ship_tracker.db`` has not gotten
corrupted and that the logical relationships the schema does NOT
enforce (FK-less rule references, orphaned audit rows, …) still
hold. The checks fall into four buckets:

* **SQLite-built-in** — ``PRAGMA integrity_check`` and ``PRAGMA
  foreign_key_check``. Cheap; safe to run on a busy DB modulo the
  ``--full`` slowness note below.
* **Schema-version** — kv_state.value['schema_version'] vs
  ``state.db.SCHEMA_VERSION``. The PRAGMA ``user_version`` slot is
  ALSO read for completeness, but the project's source of truth is
  the kv_state row.
* **Logical relationships** — orphan rows in tables that reference
  another table without a FK constraint (alerts.rule_id →
  alert_rules.rule_id, audit_events.user_id → users.user_id, …).
* **Hygiene** — stale api_tokens past their expires_at that are still
  active, stale invitations past expires_at that have not been
  consumed, ancient alert_silences that should have been swept, and
  duplicate rule_ids per user.

Output modes
------------
Default text mode prints a hierarchical PASS / WARN / FAIL line per
check, colourised via ANSI codes when stdout is a tty (and never
when piped into ``jq`` / a file). The ``--json`` mode emits a single
machine-readable JSON document:

    {
      "checks": [
        {"name": "...", "status": "PASS|WARN|FAIL|INFO",
         "message": "...", "details": {...}},
        ...
      ],
      "passed": N, "warned": N, "failed": N,
      "db_path": "...", "schema_version": N, "ran_at": "..."
    }

Exit codes
----------
* ``0`` — every check returned PASS, INFO, or WARN (or the run was
  fully successful with auto-fixes applied).
* ``1`` — at least one FAIL, OR a top-level exception escaped a
  check handler that itself failed to catch it (one bad check does
  not abort the whole run — each check is try/except-wrapped — but a
  failure to even ENTER a handler is reported as a FAIL).
* ``2`` — argparse rejected the invocation.

Read-only by default
--------------------
Without ``--fix`` the DB is opened with ``sqlite3.connect(..., uri=
True)`` using the ``mode=ro`` URI flag so a misconfigured operator
cannot accidentally mutate the DB they are diagnosing. With
``--fix`` the DB is opened read-write and the supported auto-fixes
run:

* Mark expired api_tokens inactive — the ``api_tokens`` schema does
  NOT carry an ``expires_at`` column (yet); the helper degrades
  gracefully and reports "skipped: no expires_at column".
* Mark expired invitations consumed (consumed_at=expires_at,
  consumed_by_user_id='SYSTEM_EXPIRED').
* Delete ancient alert_silences (>30 days past expires_at). Calls
  ``engine.alert_silences.cleanup_expired_silences`` when it exists;
  otherwise issues a direct DELETE if the table exists.

The fixer NEVER tries to repair ``integrity_check`` or
``foreign_key_check`` failures — those mean the file is corrupted on
disk and need a DBA + a recent backup.

Slowness note
-------------
``--full`` issues the heavier checks (currently nothing beyond
``integrity_check`` — the basic mode runs the cheaper ``quick_check``
variant). A full integrity scan walks every page of every table and
every index — on a multi-GB DB this can take minutes. Run it at off-
peak hours, or take a backup first and run the check against the
backup (which is what we recommend for routine pre-backup
verification).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional


# ─── Module-level constants ────────────────────────────────────────────────

# Anchor to the project root so DB_PATH fallback is stable regardless
# of CWD (the same anchoring trick state.db uses).
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Status tokens. Kept short so the text-mode output stays scannable
# (operator eyeballs the PASS / WARN / FAIL prefix in column 1).
STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_INFO = "INFO"

# Stale-window for alert_silences. Anything past expires_at by more
# than this is "ancient" — safe to delete in --fix mode and worth
# flagging in the informational tier in plain check mode.
STALE_SILENCE_DAYS = 30

# Tables we run the row-count / orphan / duplicate checks against. We
# don't bail on a missing table — older DBs may not have the v20/v21
# tables yet — but the CLI tells the operator which ones it skipped.
_KNOWN_TABLES: tuple[str, ...] = (
    "users",
    "alerts",
    "alert_rules",
    "delivery_channels",
    "report_history",
    "audit_events",
    "api_tokens",
    "kv_state",
    "data_source_health",
    "user_settings",
    "mfa_recovery_codes",
    "user_invitations",
    "report_schedules",
    "alert_silences",
)

# Known indexes (the CREATE INDEX statements in state/db.py). Each is
# checked via PRAGMA index_info to confirm it exists. Missing indexes
# are WARNs, not FAILs — the DB still works, just slower.
_KNOWN_INDEXES: tuple[str, ...] = (
    "idx_alerts_created_at",
    "idx_alerts_unacknowledged",
    "idx_report_history_generated_at",
    "idx_llm_calls_created_at",
    "idx_llm_calls_source",
    "idx_users_username",
    "idx_tab_render_events_started_at",
    "idx_tab_render_events_tab",
    "idx_investor_report_snapshots_generated_at",
    "idx_audit_events_created_at",
    "idx_audit_events_user_id",
    "idx_audit_events_action",
    "idx_api_tokens_user_id",
    "idx_api_tokens_prefix",
    "idx_data_source_health_started_at",
    "idx_data_source_health_source",
    "idx_report_schedules_next",
    "idx_mfa_recovery_user",
    "idx_invite_token",
    "idx_silences_active",
)


# ─── ANSI colour ───────────────────────────────────────────────────────────


def _color_enabled() -> bool:
    """Colour iff stdout is a real tty. Pipes / redirects / pytest
    capture all return False so the captured output stays plain text."""
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


_ANSI_RESET = "\033[0m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_CYAN = "\033[36m"


def _colorize(status: str, *, force: Optional[bool] = None) -> str:
    """Wrap ``status`` in the ANSI sequence for its severity bucket,
    iff colour is enabled. ``force`` lets tests override the tty check
    explicitly (None → use the real isatty result)."""
    use = _color_enabled() if force is None else bool(force)
    if not use:
        return status
    code = {
        STATUS_PASS: _ANSI_GREEN,
        STATUS_INFO: _ANSI_CYAN,
        STATUS_WARN: _ANSI_YELLOW,
        STATUS_FAIL: _ANSI_RED,
    }.get(status, "")
    if not code:
        return status
    return f"{code}{status}{_ANSI_RESET}"


# ─── Resolution helpers ────────────────────────────────────────────────────


def _resolve_default_db_path() -> Path:
    """Look up the live DB path through ``state.db`` so tests that
    monkeypatch ``state.db.DB_PATH`` see the redirected location.
    Falls back to the hard-coded default when state.db is unavailable
    (a pathological broken install — never hit in tests)."""
    try:
        from state import db as state_db
        return Path(state_db.DB_PATH)
    except Exception:  # noqa: BLE001
        return _PROJECT_ROOT / "cache" / "ship_tracker.db"


def _live_schema_version() -> int:
    """Best-effort lookup of the running code's expected schema
    version. Defaults to 0 when state.db can't be imported so the
    version check downgrades to a WARN rather than crashing."""
    try:
        from state import db as state_db
        return int(getattr(state_db, "SCHEMA_VERSION", 0))
    except Exception:  # noqa: BLE001
        return 0


def _open_db(db_path: Path, *, read_only: bool) -> sqlite3.Connection:
    """Open ``db_path`` directly (NOT via state.db.get_connection — the
    operator may be checking a backup or a snapshot, not the live DB).

    Read-only mode uses SQLite's URI flag so a misconfigured caller
    cannot accidentally mutate the DB they are diagnosing. Read-write
    mode uses ``isolation_level=None`` (autocommit) so the fix-mode
    UPDATEs/DELETEs land without an explicit ``commit()`` — matches
    the pattern in ``state.db.get_connection``.
    """
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        # Autocommit so the auto-fix UPDATEs/DELETEs persist without
        # an explicit commit. WAL is fine here too.
        conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Check result type ────────────────────────────────────────────────────


class CheckResult:
    """One check's outcome. Plain class instead of a dataclass to keep
    the module dependency-free (no ``dataclasses`` import overhead in
    the CLI startup path)."""

    __slots__ = ("name", "status", "message", "details")

    def __init__(
        self,
        name: str,
        status: str,
        message: str = "",
        details: Optional[dict] = None,
    ) -> None:
        self.name = str(name)
        self.status = str(status)
        self.message = str(message)
        self.details = dict(details) if details else {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


# ─── Helpers — small SQL queries ──────────────────────────────────────────


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """True iff ``name`` is in sqlite_master.type='table'. Used by
    every check that targets an optional table (v20/v21 tables on an
    older DB)."""
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name = ?",
            (name,),
        )
        return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    """True iff ``name`` is in sqlite_master.type='index'."""
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
            (name,),
        )
        return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """True iff ``table`` has ``column``. The fix path uses this to
    skip api_tokens.expires_at when the column has not been added yet
    (the api_tokens schema in state/db.py does NOT carry expires_at as
    of v21 — the spec anticipates a future column, so we degrade
    gracefully)."""
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cur.fetchall())
    except Exception:  # noqa: BLE001
        return False


def _read_kv_schema_version(conn: sqlite3.Connection) -> Optional[int]:
    """Read kv_state.value WHERE key='schema_version'. Returns None on
    "no row yet" (legacy pre-v1) and on any error. The project stores
    the schema version here (NOT in PRAGMA user_version) so this is
    the authoritative read."""
    try:
        cur = conn.execute(
            "SELECT value FROM kv_state WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return int(row[0])
    except Exception:  # noqa: BLE001
        return None


def _read_pragma_user_version(conn: sqlite3.Connection) -> int:
    """Read PRAGMA user_version. Returns 0 on any error. The project
    does NOT use this slot (it uses kv_state), but we read it for
    completeness in the JSON output so an operator can spot a DB that
    came from a different toolchain."""
    try:
        cur = conn.execute("PRAGMA user_version")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp. Returns None on any error so a
    bad-shaped row is treated as "not stale" rather than crashing the
    check that reads it."""
    if not value or not isinstance(value, str):
        return None
    try:
        # fromisoformat accepts "2024-01-02T03:04:05+00:00" and the
        # bare "2024-01-02T03:04:05" forms. Normalise the trailing Z
        # which Python <3.11 doesn't accept natively.
        s = value.rstrip("Z")
        if value.endswith("Z"):
            s = s + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None


# ─── Individual checks ────────────────────────────────────────────────────


def check_schema_version(conn: sqlite3.Connection) -> CheckResult:
    """Compare kv_state.schema_version to state.db.SCHEMA_VERSION.

    Equal → PASS. DB lower than expected → WARN (running code is
    ahead; a fresh open through state.db would auto-migrate). DB
    higher than expected → WARN (DB was written by a newer build than
    the running code — restoring/reading is fine but writing through
    the older code may be unsafe; flag it loudly). Missing row →
    WARN (legacy pre-v1; the running code WILL re-init on first open
    so this is informational).
    """
    kv = _read_kv_schema_version(conn)
    user_v = _read_pragma_user_version(conn)
    expected = _live_schema_version()
    details = {
        "kv_state_schema_version": kv,
        "pragma_user_version": user_v,
        "expected_schema_version": expected,
    }
    if kv is None:
        return CheckResult(
            "schema_version",
            STATUS_WARN,
            "kv_state.schema_version not present (legacy pre-v1 DB?)",
            details,
        )
    if kv == expected:
        return CheckResult(
            "schema_version",
            STATUS_PASS,
            f"schema_version={kv} matches running code",
            details,
        )
    if kv < expected:
        return CheckResult(
            "schema_version",
            STATUS_WARN,
            f"schema_version={kv} is BELOW expected {expected} — "
            f"open through state.db.get_connection() to auto-migrate",
            details,
        )
    return CheckResult(
        "schema_version",
        STATUS_WARN,
        f"schema_version={kv} is ABOVE expected {expected} — DB was "
        f"written by a newer build than the running code",
        details,
    )


def check_integrity(conn: sqlite3.Connection, *, full: bool) -> CheckResult:
    """``PRAGMA integrity_check`` (full mode) or
    ``PRAGMA quick_check`` (default).

    Both return one row holding 'ok' on a healthy DB, or one row per
    problem on a corrupted one. We accept either response and bucket
    accordingly.
    """
    pragma = "integrity_check" if full else "quick_check"
    try:
        cur = conn.execute(f"PRAGMA {pragma}")
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "integrity_check",
            STATUS_FAIL,
            f"PRAGMA {pragma} failed: {exc}",
            {"pragma": pragma},
        )
    messages = [str(r[0]) for r in rows]
    if messages == ["ok"]:
        return CheckResult(
            "integrity_check",
            STATUS_PASS,
            f"PRAGMA {pragma} returned ok",
            {"pragma": pragma},
        )
    return CheckResult(
        "integrity_check",
        STATUS_FAIL,
        f"PRAGMA {pragma} reported {len(messages)} issue(s)",
        {"pragma": pragma, "messages": messages[:20]},
    )


def check_foreign_keys(conn: sqlite3.Connection) -> CheckResult:
    """``PRAGMA foreign_key_check`` — returns one row per FK
    violation. We need foreign_keys=ON for this to actually enforce,
    but the check runs regardless and reports any rows it finds."""
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute("PRAGMA foreign_key_check")
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "foreign_key_check",
            STATUS_FAIL,
            f"PRAGMA foreign_key_check failed: {exc}",
        )
    if not rows:
        return CheckResult(
            "foreign_key_check",
            STATUS_PASS,
            "no FK violations",
        )
    # Each row is (table, rowid, parent, fkid). Cap the detail dump
    # so a thousands-of-violations DB still produces readable output.
    sample = [
        {"table": str(r[0]), "rowid": r[1], "parent": str(r[2]), "fkid": r[3]}
        for r in rows[:20]
    ]
    return CheckResult(
        "foreign_key_check",
        STATUS_FAIL,
        f"{len(rows)} FK violation(s)",
        {"violation_count": len(rows), "sample": sample},
    )


def check_orphan_alerts(conn: sqlite3.Connection) -> CheckResult:
    """Count ``alerts`` rows whose ``rule_id`` is set but not present
    in ``alert_rules``. The relationship is logical only — there is
    no FK — so a deleted rule will leave dangling references.
    """
    if not _table_exists(conn, "alerts"):
        return CheckResult(
            "orphan_alerts", STATUS_INFO, "alerts table not found",
        )
    if not _column_exists(conn, "alerts", "rule_id"):
        return CheckResult(
            "orphan_alerts",
            STATUS_INFO,
            "alerts.rule_id column not present (pre-v18 DB)",
        )
    if not _table_exists(conn, "alert_rules"):
        return CheckResult(
            "orphan_alerts", STATUS_INFO, "alert_rules table not found",
        )
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE rule_id IS NOT NULL "
            "AND rule_id != '' AND rule_id NOT IN "
            "(SELECT rule_id FROM alert_rules)"
        )
        n = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "orphan_alerts",
            STATUS_FAIL,
            f"query failed: {exc}",
        )
    if n == 0:
        return CheckResult(
            "orphan_alerts",
            STATUS_PASS,
            "no alerts reference missing rule_ids",
            {"orphan_count": 0},
        )
    return CheckResult(
        "orphan_alerts",
        STATUS_WARN,
        f"{n} alert(s) reference rule_ids that no longer exist",
        {"orphan_count": n},
    )


def check_orphan_audit_users(conn: sqlite3.Connection) -> CheckResult:
    """Count ``audit_events`` rows whose ``user_id`` is set but not
    present in ``users``. Audit events with empty user_id are legit
    (system actions); we only flag a non-empty user_id that points at
    a missing user row.
    """
    if not _table_exists(conn, "audit_events"):
        return CheckResult(
            "orphan_audit_events",
            STATUS_INFO,
            "audit_events table not found",
        )
    if not _table_exists(conn, "users"):
        return CheckResult(
            "orphan_audit_events", STATUS_INFO, "users table not found",
        )
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE user_id != '' "
            "AND user_id NOT IN (SELECT user_id FROM users)"
        )
        n = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "orphan_audit_events",
            STATUS_FAIL,
            f"query failed: {exc}",
        )
    if n == 0:
        return CheckResult(
            "orphan_audit_events",
            STATUS_PASS,
            "no audit_events reference missing users",
            {"orphan_count": 0},
        )
    return CheckResult(
        "orphan_audit_events",
        STATUS_WARN,
        f"{n} audit_events reference user_ids that no longer exist",
        {"orphan_count": n},
    )


def check_stale_api_tokens(conn: sqlite3.Connection) -> CheckResult:
    """Count api_tokens whose ``expires_at`` is past and which are
    still active (``revoked = 0``). The api_tokens schema as of v21
    does NOT actually carry an ``expires_at`` column, so this check
    degrades to INFO ("column not present") on every DB built against
    the current schema. The check is here for forward-compat: when a
    future migration adds ``expires_at`` the same code lights up
    automatically.
    """
    if not _table_exists(conn, "api_tokens"):
        return CheckResult(
            "stale_api_tokens", STATUS_INFO, "api_tokens table not found",
        )
    if not _column_exists(conn, "api_tokens", "expires_at"):
        return CheckResult(
            "stale_api_tokens",
            STATUS_INFO,
            "api_tokens.expires_at column not present — check skipped "
            "(api_tokens currently uses 'revoked' without an expiry "
            "timestamp)",
        )
    # The "active" name in the spec maps to ``revoked = 0`` here —
    # we read both columns generically so a renamed column doesn't
    # break the check.
    active_col = "active" if _column_exists(conn, "api_tokens", "active") else None
    revoked_col = (
        "revoked" if _column_exists(conn, "api_tokens", "revoked") else None
    )
    now_iso = _now_utc().isoformat()
    try:
        if active_col is not None:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM api_tokens "
                f"WHERE {active_col} = 1 AND expires_at != '' "
                f"AND expires_at < ?",
                (now_iso,),
            )
        elif revoked_col is not None:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM api_tokens "
                f"WHERE {revoked_col} = 0 AND expires_at != '' "
                f"AND expires_at < ?",
                (now_iso,),
            )
        else:
            return CheckResult(
                "stale_api_tokens",
                STATUS_INFO,
                "neither active nor revoked column found",
            )
        n = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "stale_api_tokens",
            STATUS_FAIL,
            f"query failed: {exc}",
        )
    if n == 0:
        return CheckResult(
            "stale_api_tokens",
            STATUS_PASS,
            "no expired api_tokens still active",
            {"stale_count": 0},
        )
    return CheckResult(
        "stale_api_tokens",
        STATUS_INFO,
        f"{n} expired api_token(s) still flagged active — "
        f"--fix will mark them inactive",
        {"stale_count": n},
    )


def check_stale_invitations(conn: sqlite3.Connection) -> CheckResult:
    """Count user_invitations whose ``expires_at`` is past and whose
    ``consumed_at`` is NULL. Treated as informational (--fix will
    mark them consumed)."""
    if not _table_exists(conn, "user_invitations"):
        return CheckResult(
            "stale_invitations",
            STATUS_INFO,
            "user_invitations table not found",
        )
    now_iso = _now_utc().isoformat()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM user_invitations "
            "WHERE consumed_at IS NULL AND expires_at < ?",
            (now_iso,),
        )
        n = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "stale_invitations",
            STATUS_FAIL,
            f"query failed: {exc}",
        )
    if n == 0:
        return CheckResult(
            "stale_invitations",
            STATUS_PASS,
            "no expired unconsumed invitations",
            {"stale_count": 0},
        )
    return CheckResult(
        "stale_invitations",
        STATUS_INFO,
        f"{n} expired invitation(s) still unconsumed — "
        f"--fix will mark them consumed",
        {"stale_count": n},
    )


def check_stale_silences(conn: sqlite3.Connection) -> CheckResult:
    """Count alert_silences whose ``expires_at`` is older than 30
    days. These should have been swept by the engine's cleanup helper
    long ago; lingering rows are informational (--fix will delete
    them). Tolerates missing table — alert_silences is a parallel-
    agent addition that may not be in this DB yet."""
    if not _table_exists(conn, "alert_silences"):
        return CheckResult(
            "stale_silences",
            STATUS_INFO,
            "alert_silences table not found",
        )
    cutoff_iso = (_now_utc() - timedelta(days=STALE_SILENCE_DAYS)).isoformat()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM alert_silences WHERE expires_at < ?",
            (cutoff_iso,),
        )
        n = int(cur.fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "stale_silences",
            STATUS_FAIL,
            f"query failed: {exc}",
        )
    if n == 0:
        return CheckResult(
            "stale_silences",
            STATUS_PASS,
            f"no alert_silences older than {STALE_SILENCE_DAYS} days",
            {"stale_count": 0},
        )
    return CheckResult(
        "stale_silences",
        STATUS_INFO,
        f"{n} alert_silences expired >{STALE_SILENCE_DAYS} days ago — "
        f"--fix will delete them",
        {"stale_count": n},
    )


def check_duplicate_rule_ids(conn: sqlite3.Connection) -> CheckResult:
    """Each user should have a unique ``rule_id`` per row in
    ``alert_rules``. A duplicate means two rules with the same id —
    queries that "look up the rule by id for this user" are now
    ambiguous. This is a FAIL because it is data-correctness, not
    perf.

    alert_rules.rule_id is the PRIMARY KEY (per v1 schema), so this
    SHOULD never happen across the whole table — but the schema only
    enforces global uniqueness, and the per-user user_id column was
    added in v7. If a column-mismatched insert ever got past the PK
    by hitting a different table or via raw SQL outside the app, the
    duplicate would land here.
    """
    if not _table_exists(conn, "alert_rules"):
        return CheckResult(
            "duplicate_rule_ids",
            STATUS_INFO,
            "alert_rules table not found",
        )
    # Use user_id when present; if not (legacy DB), fall back to
    # global rule_id duplication.
    has_user_id = _column_exists(conn, "alert_rules", "user_id")
    try:
        if has_user_id:
            cur = conn.execute(
                "SELECT user_id, rule_id, COUNT(*) AS n "
                "FROM alert_rules "
                "GROUP BY user_id, rule_id "
                "HAVING n > 1"
            )
        else:
            cur = conn.execute(
                "SELECT '' AS user_id, rule_id, COUNT(*) AS n "
                "FROM alert_rules "
                "GROUP BY rule_id "
                "HAVING n > 1"
            )
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "duplicate_rule_ids",
            STATUS_FAIL,
            f"query failed: {exc}",
        )
    if not rows:
        return CheckResult(
            "duplicate_rule_ids",
            STATUS_PASS,
            "every rule_id is unique (per user where applicable)",
            {"duplicate_count": 0},
        )
    sample = [
        {"user_id": str(r[0]), "rule_id": str(r[1]), "count": int(r[2])}
        for r in rows[:20]
    ]
    return CheckResult(
        "duplicate_rule_ids",
        STATUS_FAIL,
        f"{len(rows)} duplicate (user_id, rule_id) group(s)",
        {"duplicate_count": len(rows), "sample": sample},
    )


def check_index_health(conn: sqlite3.Connection) -> CheckResult:
    """Confirm every known CREATE INDEX from state.db.* lives in
    sqlite_master. A missing index is a WARN — the DB still works, it
    just runs the planned-cheap query as a table scan."""
    missing: list[str] = []
    for ix in _KNOWN_INDEXES:
        if not _index_exists(conn, ix):
            missing.append(ix)
    if not missing:
        return CheckResult(
            "index_health",
            STATUS_PASS,
            f"all {len(_KNOWN_INDEXES)} known indexes present",
            {"checked": len(_KNOWN_INDEXES), "missing": []},
        )
    return CheckResult(
        "index_health",
        STATUS_WARN,
        f"{len(missing)} known index(es) missing",
        {"checked": len(_KNOWN_INDEXES), "missing": missing},
    )


def check_known_tables(conn: sqlite3.Connection) -> CheckResult:
    """Report any KNOWN table that is missing. Used by --full only —
    a fresh DB through state.db.get_connection() will have every
    table, but a hand-restored partial backup may not."""
    missing: list[str] = []
    for t in _KNOWN_TABLES:
        if not _table_exists(conn, t):
            missing.append(t)
    if not missing:
        return CheckResult(
            "known_tables",
            STATUS_PASS,
            f"all {len(_KNOWN_TABLES)} known tables present",
            {"checked": len(_KNOWN_TABLES), "missing": []},
        )
    return CheckResult(
        "known_tables",
        STATUS_WARN,
        f"{len(missing)} known table(s) missing",
        {"checked": len(_KNOWN_TABLES), "missing": missing},
    )


# ─── Auto-fix helpers ─────────────────────────────────────────────────────


def fix_stale_api_tokens(conn: sqlite3.Connection) -> dict:
    """Mark expired api_tokens inactive. Behaviour depends on what
    columns the table actually has:

    * If ``active`` exists → set active=0 where active=1 AND expires_at
      is past.
    * Else if ``revoked`` exists → set revoked=1 where revoked=0 AND
      expires_at is past.
    * Else → skip with reason in the return payload.

    Returns ``{"action": "...", "affected": N, "skipped_reason": str}``.
    """
    if not _table_exists(conn, "api_tokens"):
        return {"action": "fix_stale_api_tokens", "affected": 0,
                "skipped_reason": "table not found"}
    if not _column_exists(conn, "api_tokens", "expires_at"):
        return {"action": "fix_stale_api_tokens", "affected": 0,
                "skipped_reason": "no expires_at column"}
    now_iso = _now_utc().isoformat()
    try:
        if _column_exists(conn, "api_tokens", "active"):
            cur = conn.execute(
                "UPDATE api_tokens SET active = 0 "
                "WHERE active = 1 AND expires_at != '' AND expires_at < ?",
                (now_iso,),
            )
        elif _column_exists(conn, "api_tokens", "revoked"):
            cur = conn.execute(
                "UPDATE api_tokens SET revoked = 1 "
                "WHERE revoked = 0 AND expires_at != '' AND expires_at < ?",
                (now_iso,),
            )
        else:
            return {"action": "fix_stale_api_tokens", "affected": 0,
                    "skipped_reason": "no active/revoked column"}
        return {"action": "fix_stale_api_tokens",
                "affected": int(cur.rowcount or 0)}
    except Exception as exc:  # noqa: BLE001
        return {"action": "fix_stale_api_tokens", "affected": 0,
                "skipped_reason": f"error: {exc}"}


def fix_stale_invitations(conn: sqlite3.Connection) -> dict:
    """Mark expired user_invitations consumed by SYSTEM_EXPIRED.

    Sets consumed_at = expires_at (so the audit trail shows the
    invite was auto-consumed at its expiry, not at fix-run time) and
    consumed_by_user_id = 'SYSTEM_EXPIRED' (the recognised sentinel).
    """
    if not _table_exists(conn, "user_invitations"):
        return {"action": "fix_stale_invitations", "affected": 0,
                "skipped_reason": "table not found"}
    now_iso = _now_utc().isoformat()
    try:
        cur = conn.execute(
            "UPDATE user_invitations "
            "SET consumed_at = expires_at, "
            "    consumed_by_user_id = 'SYSTEM_EXPIRED' "
            "WHERE consumed_at IS NULL AND expires_at < ?",
            (now_iso,),
        )
        return {"action": "fix_stale_invitations",
                "affected": int(cur.rowcount or 0)}
    except Exception as exc:  # noqa: BLE001
        return {"action": "fix_stale_invitations", "affected": 0,
                "skipped_reason": f"error: {exc}"}


def fix_stale_silences(
    conn: sqlite3.Connection, *, use_engine_helper: bool = True,
) -> dict:
    """Delete alert_silences older than STALE_SILENCE_DAYS past their
    expires_at. Tries the engine helper first
    (``engine.alert_silences.cleanup_expired_silences``), falling back
    to a direct DELETE if it doesn't exist or fails.

    ``use_engine_helper`` lets the CLI skip the helper when running
    against a non-live DB (--db PATH targeting a backup). The helper
    operates on ``state.db.get_connection`` which would write to the
    wrong DB in that case.
    """
    if not _table_exists(conn, "alert_silences"):
        return {"action": "fix_stale_silences", "affected": 0,
                "skipped_reason": "table not found"}
    cutoff_iso = (_now_utc() - timedelta(days=STALE_SILENCE_DAYS)).isoformat()
    if use_engine_helper:
        try:
            from engine.alert_silences import cleanup_expired_silences  # type: ignore
            try:
                affected_helper = cleanup_expired_silences()
                return {"action": "fix_stale_silences",
                        "affected": int(affected_helper or 0),
                        "source": "engine.alert_silences.cleanup_expired_silences"}
            except Exception:  # noqa: BLE001 — fall through to direct SQL
                pass
        except Exception:  # noqa: BLE001 — module not present
            pass
    try:
        cur = conn.execute(
            "DELETE FROM alert_silences WHERE expires_at < ?",
            (cutoff_iso,),
        )
        return {"action": "fix_stale_silences",
                "affected": int(cur.rowcount or 0),
                "source": "direct SQL"}
    except Exception as exc:  # noqa: BLE001
        return {"action": "fix_stale_silences", "affected": 0,
                "skipped_reason": f"error: {exc}"}


# ─── Check runner ─────────────────────────────────────────────────────────


def run_all_checks(
    conn: sqlite3.Connection, *, full: bool = False,
) -> list[CheckResult]:
    """Run every check against ``conn`` in a fixed order. Each check
    is independently try/except-wrapped so one bad check does not
    abort the rest."""
    # (name, callable) — each callable takes the conn and returns a
    # CheckResult. We use a tuple-list rather than dispatching by name
    # so the order in the report is deterministic.
    runners: list[tuple[str, Callable[[sqlite3.Connection], CheckResult]]] = [
        ("schema_version", check_schema_version),
        ("integrity_check", lambda c: check_integrity(c, full=full)),
        ("foreign_key_check", check_foreign_keys),
        ("orphan_alerts", check_orphan_alerts),
        ("orphan_audit_events", check_orphan_audit_users),
        ("stale_api_tokens", check_stale_api_tokens),
        ("stale_invitations", check_stale_invitations),
        ("stale_silences", check_stale_silences),
        ("duplicate_rule_ids", check_duplicate_rule_ids),
        ("index_health", check_index_health),
    ]
    if full:
        runners.append(("known_tables", check_known_tables))

    results: list[CheckResult] = []
    for name, runner in runners:
        try:
            results.append(runner(conn))
        except Exception as exc:  # noqa: BLE001
            # A check that didn't even produce a CheckResult — record
            # it as FAIL so the operator notices.
            results.append(CheckResult(
                name, STATUS_FAIL,
                f"check raised: {exc}",
                {"exception_type": type(exc).__name__},
            ))
    return results


def run_all_fixes(
    conn: sqlite3.Connection, *, use_engine_helper: bool = True,
) -> list[dict]:
    """Run every supported auto-fix against ``conn``. The conn MUST
    be open read-write — the caller is responsible for that (the CLI
    only switches to RW when --fix is passed).

    ``use_engine_helper`` is passed through to ``fix_stale_silences``
    — set False when ``conn`` targets a different DB than the live
    cache/ship_tracker.db (e.g. --db PATH against a backup), because
    the engine helper writes to ``state.db.get_connection``.
    """
    fixes: list[dict] = []
    for fixer, kwargs in (
        (fix_stale_api_tokens, {}),
        (fix_stale_invitations, {}),
        (fix_stale_silences, {"use_engine_helper": use_engine_helper}),
    ):
        try:
            fixes.append(fixer(conn, **kwargs))
        except Exception as exc:  # noqa: BLE001
            fixes.append({
                "action": fixer.__name__,
                "affected": 0,
                "skipped_reason": f"error: {exc}",
            })
    return fixes


# ─── Output rendering ─────────────────────────────────────────────────────


def _aggregate_counts(results: list[CheckResult]) -> dict:
    counts = {"passed": 0, "warned": 0, "failed": 0, "info": 0}
    for r in results:
        if r.status == STATUS_PASS:
            counts["passed"] += 1
        elif r.status == STATUS_WARN:
            counts["warned"] += 1
        elif r.status == STATUS_FAIL:
            counts["failed"] += 1
        elif r.status == STATUS_INFO:
            counts["info"] += 1
    return counts


def _render_text(
    results: list[CheckResult],
    *,
    db_path: Path,
    fixes: Optional[list[dict]] = None,
    color: Optional[bool] = None,
) -> str:
    """Render the text-mode report. Returns a single string so the
    caller can either ``print`` it OR (in tests) assert on its
    contents."""
    lines: list[str] = []
    lines.append(f"DB integrity check: {db_path}")
    lines.append(f"ran_at: {_now_utc().isoformat()}")
    lines.append("")
    for r in results:
        prefix = _colorize(r.status, force=color)
        lines.append(f"  {prefix:<6}  {r.name}: {r.message}")
        # Inline a couple of useful detail keys when present.
        if r.details:
            interesting = {
                k: v for k, v in r.details.items()
                if k in ("orphan_count", "stale_count", "duplicate_count",
                         "violation_count", "missing", "expected_schema_version",
                         "kv_state_schema_version", "messages", "pragma")
            }
            if interesting:
                # Keep one line — don't blow up the output for an
                # operator who's just eyeballing.
                summary = ", ".join(f"{k}={v}" for k, v in interesting.items())
                lines.append(f"          {summary}")
    counts = _aggregate_counts(results)
    lines.append("")
    lines.append(
        f"summary: passed={counts['passed']} warned={counts['warned']} "
        f"failed={counts['failed']} info={counts['info']}"
    )
    if fixes:
        lines.append("")
        lines.append("auto-fixes:")
        for f in fixes:
            action = f.get("action", "?")
            affected = f.get("affected", 0)
            reason = f.get("skipped_reason")
            if reason:
                lines.append(f"  - {action}: skipped ({reason})")
            else:
                lines.append(f"  - {action}: affected={affected}")
    return "\n".join(lines)


def _render_json(
    results: list[CheckResult],
    *,
    db_path: Path,
    schema_version: Optional[int],
    fixes: Optional[list[dict]] = None,
) -> str:
    """Render the --json document. ``schema_version`` is the value
    read from kv_state, NOT the running-code expected value (those
    are recorded in each check's details)."""
    counts = _aggregate_counts(results)
    doc = {
        "checks": [r.to_dict() for r in results],
        "passed": counts["passed"],
        "warned": counts["warned"],
        "failed": counts["failed"],
        "info": counts["info"],
        "db_path": str(db_path),
        "schema_version": schema_version,
        "ran_at": _now_utc().isoformat(),
    }
    if fixes is not None:
        doc["fixes"] = fixes
    return json.dumps(doc, indent=2, sort_keys=True, default=str)


# ─── argparse + entry ─────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.db_check_cli",
        description=(
            "DB integrity check + auto-fix CLI for the Ship Tracker "
            "SQLite state database."
        ),
    )
    p.add_argument(
        "--full",
        action="store_true",
        help=(
            "Run the deeper PRAGMA integrity_check (page-by-page scan) "
            "instead of the cheaper quick_check. Slow on multi-GB DBs."
        ),
    )
    p.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Auto-fix safe issues: mark expired api_tokens inactive, "
            "mark expired invitations consumed, delete alert_silences "
            "expired >30 days. Does NOT touch integrity_check or "
            "foreign_key_check failures."
        ),
    )
    p.add_argument(
        "--db",
        default=None,
        help=(
            "Path to a specific .db file. Default: live cache/ship_tracker.db "
            "as resolved through state.db.DB_PATH."
        ),
    )
    p.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="Emit machine-readable JSON instead of the text report.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns:

    * ``0`` — every check returned PASS / WARN / INFO.
    * ``1`` — at least one FAIL, or a top-level exception escaped.
    * ``2`` — argparse rejected the invocation.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        return code

    db_path = Path(args.db) if args.db else _resolve_default_db_path()
    if not db_path.exists():
        # We treat "no DB" as a FAIL — operators run this when they
        # expect a DB to be there. Print to stderr so a piped --json
        # consumer still gets clean JSON on stdout (in this case the
        # stdout is empty and the exit code carries the signal).
        print(
            f"error: DB not found at {db_path}",
            file=sys.stderr,
        )
        return 1

    try:
        conn = _open_db(db_path, read_only=not args.fix)
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot open DB at {db_path}: {exc}", file=sys.stderr)
        return 1

    try:
        results = run_all_checks(conn, full=args.full)
        fixes: Optional[list[dict]] = None
        if args.fix:
            # Disable the engine helper when --db targets a different
            # file than the live cache/ship_tracker.db. The helper
            # writes through state.db.get_connection(), so it would
            # mutate the LIVE DB while we're meant to be fixing the
            # supplied one.
            live_path = _resolve_default_db_path()
            same_db = Path(db_path).resolve() == Path(live_path).resolve()
            fixes = run_all_fixes(conn, use_engine_helper=same_db)
        schema_v = _read_kv_schema_version(conn)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if args.json_out:
        print(_render_json(
            results, db_path=db_path,
            schema_version=schema_v, fixes=fixes,
        ))
    else:
        print(_render_text(results, db_path=db_path, fixes=fixes))

    # Exit code: 0 unless any check is FAIL.
    any_fail = any(r.status == STATUS_FAIL for r in results)
    return 1 if any_fail else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
