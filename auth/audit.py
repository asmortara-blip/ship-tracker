"""auth.audit — append-only "who did what when" record for security review.

Design
------
The codebase already has identity (``auth.users``) and per-user query
scoping (``state.user_scope``). The missing piece is a queryable record
of privileged actions so a security review can answer questions like
"who deleted that report?" or "did someone push a rule change at 3 a.m.?"

This module provides the write side of that capability:

* A new ``audit_events`` table (schema v10) holds one row per recorded
  action. Columns are intentionally generic — ``action`` + ``entity_type``
  + ``entity_id`` + a free-form ``detail_json`` payload — so new
  touchpoints can opt in without another migration each time.
* :func:`record_audit` is the single write entry point. It NEVER raises:
  every existing function it hooks into is on the hot path (alert ack,
  rule save, report delete, signup/login) and an audit-log failure must
  not break those callers. Failures log at DEBUG and silently swallow.
* :func:`query_audit` is the read entry point used by the (future) audit
  UI tab and by tests verifying the hooks actually fire.
* :func:`prune_old_audit_events` is a maintenance helper; security
  review typically wants a finite retention window so the table does
  not grow unbounded over years of operation.

The hooks themselves live in the modules they audit (so the call sits
right next to the action being recorded). This file only owns the
schema mapping, the dataclass, and the read/write/prune helpers.

What this module does NOT do
----------------------------
* No "tamper-proof" cryptographic chaining of rows — this is operational
  logging, not a blockchain. If an attacker has DB write access, every
  guarantee is already lost.
* No automatic redaction of sensitive payload fields. Callers are
  responsible for not stuffing passwords or other secrets into the
  ``detail`` dict.
* No real-time streaming / pub-sub — rows are written synchronously to
  SQLite. The audit volume from the eleven documented touchpoints is
  small enough that this is fine (one row per user-initiated action).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger


# ─── Data ──────────────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """One row from the ``audit_events`` table.

    ``detail_json`` is the parsed Python dict (NOT the JSON string),
    deserialized at read time. Callers that want the raw string can
    re-serialize via ``json.dumps``.
    """
    event_id: str
    created_at: str
    user_id: str
    action: str
    entity_type: str
    entity_id: str
    detail_json: dict = field(default_factory=dict)


# ─── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    """Map a sqlite3.Row from the audit_events table to an AuditEvent.

    The ``detail_json`` column is decoded from JSON; a malformed row is
    surfaced as an empty dict rather than raising so the query helper
    can still return everything else around the bad row.
    """
    raw = row["detail_json"] or "{}"
    try:
        detail = json.loads(raw)
        if not isinstance(detail, dict):
            detail = {}
    except (TypeError, ValueError):
        detail = {}
    return AuditEvent(
        event_id=row["event_id"],
        created_at=row["created_at"],
        user_id=row["user_id"] or "",
        action=row["action"],
        entity_type=row["entity_type"] or "",
        entity_id=row["entity_id"] or "",
        detail_json=detail,
    )


def _resolve_user_id(user_id: Optional[str]) -> str:
    """Use the explicit ``user_id`` param if supplied, otherwise consult
    the active Streamlit session via ``state.user_scope.current_user_id``.

    ``None`` means "caller did not specify". An explicit empty string is
    returned as-is (treated as "no user" — the legacy global bucket).
    The session helper itself can never raise.
    """
    if user_id is None:
        try:
            from state.user_scope import current_user_id
            return current_user_id()
        except Exception:
            return ""
    return user_id


# ─── Public API: write ─────────────────────────────────────────────────────

def record_audit(
    action: str,
    *,
    entity_type: str = "",
    entity_id: str = "",
    detail: Optional[dict] = None,
    user_id: Optional[str] = None,
) -> None:
    """Record one privileged action to the audit log. NEVER raises.

    Args:
        action:      The action verb (e.g. ``'ack_alert'``, ``'save_rules'``,
                     ``'delete_report'``). Free-form string; callers in this
                     codebase use the conventions listed in the task spec.
        entity_type: Domain object type (e.g. ``'alert'``, ``'rule'``,
                     ``'channel'``, ``'report'``, ``'user'``). Empty when
                     the action is not tied to a specific entity.
        entity_id:   The id of the entity the action targets. Empty when
                     not applicable (e.g. ``ack_all_alerts``).
        detail:      Free-form payload dict. Serialized to JSON via
                     ``json.dumps(default=str)`` so datetimes / Paths /
                     other non-JSON types degrade to strings instead of
                     raising. None becomes ``{}``.
        user_id:     Explicit user id, or ``None`` to resolve from the
                     active Streamlit session. An empty string means
                     "no user" (legacy / system-level action).

    Returns:
        None. This function never raises — every existing function it
        hooks into is on the hot path; an audit-write failure cannot be
        allowed to break alerts, reports, or auth. Failures are
        swallowed and logged at DEBUG.
    """
    try:
        if not isinstance(action, str) or not action:
            # An empty action is a programming error in the caller, not
            # something to surface — fail closed and silently.
            logger.debug("auth.audit.record_audit: empty action, skipping")
            return

        uid = _resolve_user_id(user_id)
        event_id = _new_id()
        created_at = _now_iso()
        try:
            detail_str = json.dumps(detail or {}, default=str)
        except (TypeError, ValueError) as exc:
            # An un-JSON-serializable payload should not break the
            # audit write — log + fall back to an empty dict.
            logger.debug(
                f"auth.audit.record_audit: payload serialization failed "
                f"for action={action!r}: {exc}"
            )
            detail_str = "{}"

        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO audit_events
                  (event_id, created_at, user_id, action, entity_type,
                   entity_id, detail_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    created_at,
                    uid,
                    action,
                    entity_type or "",
                    entity_id or "",
                    detail_str,
                ),
            )
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        # Best-effort: every failure path swallows. We log at DEBUG (not
        # WARNING) so a flaky DB does not flood the operator's log with
        # audit-write noise.
        logger.debug(
            f"auth.audit.record_audit: write failed for action="
            f"{action!r}: {exc}"
        )


# ─── Public API: read ──────────────────────────────────────────────────────

def query_audit(
    *,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> list[AuditEvent]:
    """Read audit events matching the filters, newest-first.

    Args:
        user_id:     ``None`` returns all events (admin view across
                     every user). A non-empty string filters strictly
                     to that user_id — no dual-set semantics here; the
                     audit log is reviewed at security time, not while
                     navigating per-user data.
        action:      Filter to one action verb (e.g. ``'login'``).
        entity_type: Filter to one entity type (e.g. ``'report'``).
        since:       ISO-8601 UTC string. Only events created strictly
                     at or after this timestamp are returned. ``None``
                     means "no lower bound".
        limit:       Cap the result set. Defaults to 100. The CAP IS
                     ENFORCED in SQL via ``LIMIT``, so a malicious
                     caller cannot OOM the process by asking for
                     ``limit=10**9``. A limit <= 0 returns ``[]``.

    Returns:
        A list of ``AuditEvent`` sorted by ``created_at`` DESC. Empty
        list on any read error (never raises).
    """
    if limit <= 0:
        return []
    try:
        from state.db import get_connection

        conn = get_connection()

        # Build the WHERE clause incrementally so each filter is
        # independent. ``WHERE 1=1`` anchors the chain — every appended
        # fragment starts with ``AND`` and so concatenates cleanly.
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if entity_type is not None:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)

        sql = (
            "SELECT event_id, created_at, user_id, action, entity_type, "
            "entity_id, detail_json FROM audit_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT ?"
        )
        params.append(int(limit))

        rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"auth.audit.query_audit: read failed: {exc}")
        return []

    return [_row_to_event(r) for r in rows]


# ─── Public API: maintenance ───────────────────────────────────────────────

def prune_old_audit_events(retention_days: int = 365) -> int:
    """Delete audit rows older than ``retention_days``. NEVER raises.

    Args:
        retention_days: Rows whose ``created_at`` is strictly before
                        ``now - retention_days`` are deleted. Must be
                        positive — a non-positive value is rejected
                        and returns ``0`` rather than wiping every row
                        (which would be a silent foot-gun if a caller
                        passed ``0`` thinking it meant "no pruning").

    Returns:
        The number of rows deleted, or ``0`` on any error.
    """
    try:
        if not isinstance(retention_days, int) or retention_days <= 0:
            return 0

        from state.db import get_connection

        cutoff = (
            datetime.now(timezone.utc)
            - _timedelta(days=retention_days)
        ).isoformat()
        conn = get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM audit_events WHERE created_at < ?",
                (cutoff,),
            )
            # sqlite3.Cursor exposes the affected rowcount on the most
            # recent execute() — surface that to the caller so they can
            # log or alert on unusual prune sizes.
            return int(cur.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.audit.prune_old_audit_events: prune failed: {exc}"
        )
        return 0


# Local alias so the import lives next to its single use site — keeps
# the public surface clean and avoids a top-level import for a one-off.
from datetime import timedelta as _timedelta  # noqa: E402


__all__ = [
    "AuditEvent",
    "record_audit",
    "query_audit",
    "prune_old_audit_events",
]
