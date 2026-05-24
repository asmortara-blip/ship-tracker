"""engine/alert_silences.py — bounded alert silencing for planned downtime.

Operators want to "shut up rule X for the next 4 hours" before they
take a feed offline for maintenance. The pre-silences workflow forces
them to disable the rule and hope they remember to re-enable — a
classic footgun that leaves a system mute the morning after a deploy.

This module persists a small ``alert_silences`` table (schema v22) and
exposes a tight CRUD surface plus the ``is_alert_silenced`` lookup the
alert engine uses to decide "should I drop this alert on the floor?".

Silence match semantics
-----------------------
A silence has three NULLable match keys — ``rule_id``, ``ticker``, and
``severity``. The match logic is uniformly "NULL means matches any
value":

* ``silence.rule_id is None`` matches every rule_id; ``silence.rule_id
  == alert.rule_id`` matches only that one.
* ``silence.ticker is None`` matches every ticker (including ``""``);
  ``silence.ticker == alert.ticker`` matches only that one.
* ``silence.severity is None`` matches every severity; ``silence.severity
  == alert.severity`` matches only that one.

All three must match for the silence to fire. The broadest silence
(all three NULL) shuts up every alert under the silence's user_id.
The narrowest (all three set) shuts up exactly one (alert_type,
ticker, severity) combination on one rule.

Per-user scoping
----------------
Silences are user-scoped. Alice's silence does NOT suppress Bob's
alerts. The silence table carries a ``user_id`` column; every read
filters on exact match. The cross-user isolation is a hard
requirement of the design — an admin who silences "all CRITICAL
alerts for me" must not accidentally mute their team-mates.

Lifecycle + retention
---------------------
A silence is active while ``starts_at <= now < expires_at``. Expired
silences are NOT deleted immediately — they're kept around for
``cleanup_expired_silences(retention_days=30)`` so an audit query
"what was muted yesterday?" still answers. The cleanup helper runs
once per day from the worker scheduler.

The silence gate inside ``engine.alert_engine_v2.fire_rule`` is
checked AFTER the cooldown + flap gates so a silenced rule still
records its threshold crossings for flap-detection consistency — the
fact that a rule is muted has no bearing on whether it is also
flapping.

All helpers NEVER raise. Every read returns ``[]`` / ``None`` / ``False``
on any internal error (DB unavailable, malformed row, etc.) and logs
the failure via loguru. The contract matches the rest of the alert-
engine helpers — operator UX requires that a hiccup in the silence
layer can never block the underlying alert pipeline.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AlertSilence:
    """One row of the ``alert_silences`` table.

    ``rule_id`` / ``ticker`` / ``severity`` are ``Optional[str]`` and
    None means "matches any value for this column" — the silence is
    broader the more NULLs it carries.
    """
    silence_id: str
    user_id: str
    rule_id: Optional[str]
    ticker: Optional[str]
    severity: Optional[str]
    reason: Optional[str]
    starts_at: str
    expires_at: str
    created_at: str
    created_by_user_id: str


# ─────────────────────────────────────────────────────────────────────────────
#  Suppressed-counter key (kv_state)
# ─────────────────────────────────────────────────────────────────────────────

# Same pattern as the cooldown / flap counters: store a running total in
# kv_state.value (TEXT) so the operator overview / data health tabs can
# surface "N alerts silenced this run" without a dedicated table. Callers
# parse the value with int() and treat a missing row as 0.
_SILENCED_COUNTER_KEY: str = "alerts_suppressed_by_silence"


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso(now: Optional[datetime] = None) -> str:
    """Format ``now`` as UTC ISO-8601. Defaults to the current wall
    clock — tests pass an explicit ``now`` to make the result
    deterministic without monkey-patching ``datetime.now``."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_silence(row) -> AlertSilence:
    """Project a sqlite3.Row onto the dataclass shape. Centralised so
    the column order is named in exactly one place — the table evolved
    via schema v22 and every subsequent migration that touches a
    nullable column ought to update this helper."""
    return AlertSilence(
        silence_id=row["silence_id"],
        user_id=row["user_id"] or "",
        rule_id=row["rule_id"],
        ticker=row["ticker"],
        severity=row["severity"],
        reason=row["reason"],
        starts_at=row["starts_at"] or "",
        expires_at=row["expires_at"] or "",
        created_at=row["created_at"] or "",
        created_by_user_id=row["created_by_user_id"] or "",
    )


def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the explicit ``user_id`` parameter, or fall back to the
    Streamlit session's ``current_user``.

    Mirrors the helper in ``engine.alert_engine_v2._resolve_user_id`` —
    same contract so callers can shuttle the same ``user_id=None``
    sentinel between the two modules without thinking about which one
    resolves it.
    """
    if user_id is None:
        try:
            from state.user_scope import current_user_id
            return current_user_id()
        except Exception:
            # current_user_id itself never raises, but the import
            # could in principle. Belt-and-braces: degrade to the
            # legacy global bucket.
            return ""
    return user_id


def _alert_field(alert: Any, name: str, default: Any = None) -> Any:
    """Return ``alert[name]`` for dicts or ``getattr(alert, name,
    default)`` for dataclass instances. Lets ``is_alert_silenced``
    accept either a ShippingAlert (dataclass) or a plain dict without
    branching at every call site. Never raises."""
    try:
        if isinstance(alert, dict):
            return alert.get(name, default)
        return getattr(alert, name, default)
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_silence(
    *,
    user_id: str,
    rule_id: Optional[str] = None,
    ticker: Optional[str] = None,
    severity: Optional[str] = None,
    reason: Optional[str] = None,
    duration_minutes: int,
    created_by_user_id: str,
    now: Optional[datetime] = None,
) -> Optional[AlertSilence]:
    """Persist a new silence + return the row.

    ``duration_minutes`` is interpreted relative to ``now`` (defaults
    to the current UTC clock). Negative / zero durations are clamped
    to 1 minute — a silence that expires the same instant it was
    created would be useless, and a negative duration is operator
    error rather than a meaningful "silence backwards in time".

    NULLable match keys land as SQL NULL (not empty string) so the
    SQL layer can answer "is rule_id None?" with ``IS NULL`` and the
    silence match logic stays a tight equality / null check.

    Returns the persisted AlertSilence on success, or ``None`` on
    any failure. Never raises.
    """
    try:
        from state.db import get_connection

        if not user_id:
            logger.warning("create_silence: empty user_id")
            return None
        if not created_by_user_id:
            logger.warning("create_silence: empty created_by_user_id")
            return None

        # Duration sanity. Treat anything non-positive as 1 minute —
        # the operator's intent is "silence for some bounded window";
        # zero / negative is nonsensical input that should not poison
        # the table.
        try:
            duration = int(duration_minutes)
        except (TypeError, ValueError):
            logger.warning(
                f"create_silence: invalid duration_minutes "
                f"{duration_minutes!r}, defaulting to 1"
            )
            duration = 1
        if duration <= 0:
            duration = 1

        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        starts_iso = now.isoformat()
        expires_iso = (now + timedelta(minutes=duration)).isoformat()

        silence = AlertSilence(
            silence_id=_new_id(),
            user_id=user_id,
            # Coerce empty string to None so the "matches anything"
            # semantic does not accidentally trip on a caller that
            # passes "" thinking it means "no value".
            rule_id=rule_id if rule_id else None,
            ticker=ticker if ticker else None,
            severity=severity if severity else None,
            reason=reason if reason else None,
            starts_at=starts_iso,
            expires_at=expires_iso,
            created_at=starts_iso,
            created_by_user_id=created_by_user_id,
        )

        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO alert_silences
                  (silence_id, user_id, rule_id, ticker, severity,
                   reason, starts_at, expires_at, created_at,
                   created_by_user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    silence.silence_id,
                    silence.user_id,
                    silence.rule_id,
                    silence.ticker,
                    silence.severity,
                    silence.reason,
                    silence.starts_at,
                    silence.expires_at,
                    silence.created_at,
                    silence.created_by_user_id,
                ),
            )
        return silence
    except Exception as exc:
        logger.error(f"create_silence failed: {exc}")
        return None


def get_active_silences(
    *,
    user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[AlertSilence]:
    """Return currently-active silences for the given user.

    "Active" means ``starts_at <= now < expires_at``. The default
    ``now`` is the current UTC clock; tests pass an explicit value
    for determinism.

    Per-user scoping is by EXACT match (not the dual-set scope used
    by load_alerts) — silences belong to exactly one user and there
    is no legacy ``""`` bucket to absorb. When ``user_id`` is None
    we resolve via ``current_user_id()``; an explicit ``""`` matches
    only legacy / unauthenticated rows (which never exist in normal
    operation but the contract is preserved).
    """
    try:
        from state.db import get_connection

        uid = _resolve_user_id(user_id)
        now_iso = _now_iso(now)

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM alert_silences
            WHERE user_id = ?
              AND starts_at <= ?
              AND expires_at > ?
            ORDER BY expires_at ASC
            """,
            (uid, now_iso, now_iso),
        ).fetchall()
        return [_row_to_silence(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_active_silences failed: {exc}")
        return []


def list_silences(
    *,
    user_id: Optional[str] = None,
    include_expired: bool = False,
    now: Optional[datetime] = None,
) -> list[AlertSilence]:
    """Return silences for the given user, optionally including
    expired ones (kept around for audit until cleanup_expired_silences
    sweeps them out).

    Sorted by ``expires_at`` descending so the most-recently-relevant
    rows land at the top of the list — useful for the UI panel that
    surfaces "your most recent silences first".
    """
    try:
        from state.db import get_connection

        uid = _resolve_user_id(user_id)
        conn = get_connection()

        if include_expired:
            rows = conn.execute(
                "SELECT * FROM alert_silences WHERE user_id = ? "
                "ORDER BY expires_at DESC",
                (uid,),
            ).fetchall()
        else:
            now_iso = _now_iso(now)
            rows = conn.execute(
                "SELECT * FROM alert_silences WHERE user_id = ? "
                "AND expires_at > ? ORDER BY expires_at DESC",
                (uid, now_iso),
            ).fetchall()
        return [_row_to_silence(r) for r in rows]
    except Exception as exc:
        logger.error(f"list_silences failed: {exc}")
        return []


def delete_silence(
    silence_id: str,
    *,
    user_id: Optional[str] = None,
) -> bool:
    """Cancel a silence early. Per-user scoped — alice cannot delete
    bob's silence.

    Returns True iff a row was actually deleted (the silence existed
    in the caller's scope). Cross-user attempts and unknown ids both
    return False — they collapse intentionally so a probing caller
    cannot enumerate other users' silence ids by 404 vs 403.
    """
    try:
        from state.db import get_connection

        if not silence_id:
            return False
        uid = _resolve_user_id(user_id)

        conn = get_connection()
        # Look the row up first so we can return a meaningful boolean
        # — DELETE silently affects zero rows on a cross-user id; we
        # want to distinguish "deleted" from "wasn't yours".
        row = conn.execute(
            "SELECT silence_id FROM alert_silences "
            "WHERE silence_id = ? AND user_id = ?",
            (silence_id, uid),
        ).fetchone()
        if row is None:
            return False
        with conn:
            conn.execute(
                "DELETE FROM alert_silences WHERE silence_id = ?",
                (silence_id,),
            )
        return True
    except Exception as exc:
        logger.error(f"delete_silence failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Silence matching
# ─────────────────────────────────────────────────────────────────────────────

def is_alert_silenced(
    alert: Any,
    *,
    user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[AlertSilence]:
    """Return the matching silence (or ``None`` when the alert is not
    silenced).

    Match logic (all three must hold for the silence to fire):

      * ``silence.rule_id`` is None OR equals ``alert.rule_id``
      * ``silence.ticker``  is None OR equals ``alert.ticker``
      * ``silence.severity`` is None OR equals ``alert.severity``
      * ``silence.starts_at`` <= ``now`` < ``silence.expires_at``

    When multiple silences would match (e.g. a broad "all rules"
    silence plus a narrow "rule X only" silence both apply), the one
    returned is the FIRST active silence in the active-silences list
    (sorted by ``expires_at`` ascending — the silence about to lapse
    is returned first so the operator log line carries the silence
    most likely to need attention).

    Per-user scoping is by exact match. When ``user_id`` is None we
    resolve via ``current_user_id()``. An explicit empty string
    matches only the legacy ``""`` bucket — useful in tests but
    irrelevant in normal operation.

    Returns the matching ``AlertSilence`` (not just True) so the
    caller can log silence_id + reason for the operator audit trail
    without re-querying.

    Never raises — any internal error returns None (i.e. "not
    silenced") so a malformed silence row can never block the alert
    pipeline.
    """
    try:
        # ``alert`` may be a dict OR a ShippingAlert dataclass. Pull
        # the three match fields via the polymorphic helper so this
        # function does not have to import ShippingAlert (avoids a
        # circular import with engine.alert_engine_v2).
        alert_rule_id = _alert_field(alert, "rule_id")
        alert_ticker = _alert_field(alert, "ticker", "")
        alert_severity = _alert_field(alert, "severity", "")

        # The DB query already filters by user_id + active-window
        # bounds; we only need to walk the small returned list to
        # find the first silence whose three optional keys match.
        active = get_active_silences(user_id=user_id, now=now)
        if not active:
            return None

        for silence in active:
            if silence.rule_id is not None and silence.rule_id != alert_rule_id:
                continue
            if silence.ticker is not None and silence.ticker != alert_ticker:
                continue
            if silence.severity is not None and silence.severity != alert_severity:
                continue
            return silence
        return None
    except Exception as exc:
        # Fail OPEN — a silence-layer crash must never silently block
        # an alert. Mirrors the cooldown layer's defensive posture.
        logger.warning(f"is_alert_silenced: returning None on error: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Retention
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_expired_silences(*, retention_days: int = 30) -> int:
    """Delete silences expired more than ``retention_days`` days ago.

    Expired silences are kept around for a window so an audit query
    "what was muted yesterday?" still answers; the default 30 days
    matches the data-source health prune window so the operator can
    correlate a silence with the health row that motivated it.

    Returns the count of rows deleted (``0`` on no-op or any error).
    Never raises. Designed to run once per day from the worker
    scheduler.
    """
    try:
        from state.db import get_connection

        try:
            retention = int(retention_days)
        except (TypeError, ValueError):
            logger.warning(
                f"cleanup_expired_silences: invalid retention_days "
                f"{retention_days!r}, defaulting to 30"
            )
            retention = 30
        if retention < 0:
            retention = 0

        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=retention)
        ).isoformat()

        conn = get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM alert_silences WHERE expires_at < ?",
                (cutoff_iso,),
            )
            deleted = int(cur.rowcount or 0)
        if deleted:
            logger.info(
                f"cleanup_expired_silences: deleted {deleted} rows "
                f"(retention_days={retention})"
            )
        return deleted
    except Exception as exc:
        logger.warning(f"cleanup_expired_silences failed: {exc}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Suppressed counter (telemetry — read by the operator overview tab)
# ─────────────────────────────────────────────────────────────────────────────

def _bump_silenced_counter() -> None:
    """Increment the kv_state counter of silenced-alert events.

    Mirrors ``engine.alert_engine_v2._bump_suppressed_counter`` exactly
    — same kv_state read / write / int-fallback pattern, just keyed on
    a different row. Best-effort: any failure is swallowed and logged
    because the counter is for operator-overview telemetry, not
    correctness.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = _now_iso()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_SILENCED_COUNTER_KEY,),
        ).fetchone()
        try:
            current = int(row["value"]) if row else 0
        except (TypeError, ValueError):
            # Corrupt counter resets to 0 so a single bad write does
            # not jam the increment path forever.
            current = 0
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (_SILENCED_COUNTER_KEY, str(current + 1), now_iso),
        )
    except Exception as exc:
        logger.warning(f"_bump_silenced_counter: kv_state write failed: {exc}")


def get_suppressed_by_silence_count() -> int:
    """Return the cumulative count of alert fires suppressed by an
    active silence since the app started writing the counter (or
    since the last manual reset).

    Returns 0 when the kv_state row does not yet exist. Never raises.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_SILENCED_COUNTER_KEY,),
        ).fetchone()
    except Exception as exc:
        logger.warning(f"get_suppressed_by_silence_count: read failed: {exc}")
        return 0
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0
