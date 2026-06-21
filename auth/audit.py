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

Tamper-evidence (schema v31)
----------------------------
Each row is hash-chained to its predecessor:
``row_hash = SHA-256(prev_hash || canonical(row))``. An in-place edit, delete,
reorder, or insertion of an audit row breaks the chain and is detectable via
:func:`engine.audit_search.verify_chain`. This is tamper-EVIDENT, not tamper-
PROOF: an attacker with full DB write access can rewrite the whole chain
forward. True tamper-evidence therefore requires periodically exporting the
chain head (:func:`chain_head`) OUT-OF-BAND, so a wholesale rewrite is caught
by the mismatch against the last externally-anchored head. Sanctioned
maintenance that mutates rows (prune/redact) breaks the chain by design;
:func:`engine.audit_search.reseal_chain` re-seals it as an audited admin op.

What this module does NOT do
----------------------------
* No automatic redaction of sensitive payload fields. Callers are
  responsible for not stuffing passwords or other secrets into the
  ``detail`` dict.
* No real-time streaming / pub-sub — rows are written synchronously to
  SQLite. The audit volume from the eleven documented touchpoints is
  small enough that this is fine (one row per user-initiated action).
"""
from __future__ import annotations

import hashlib
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


def _compute_row_hash(
    prev_hash: str,
    event_id: str,
    created_at: str,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    detail_str: str,
) -> str:
    """SHA-256 over ``prev_hash`` and the row's immutable fields.

    The single source of truth for the chain hash — both the writer
    (:func:`record_audit`) and the verifier (``engine.audit_search``) MUST use
    this exact function so a recomputation matches the stored value.

    Each field is LENGTH-PREFIXED (``<byte-len>:<value>``) before joining, a
    canonical form in which a caller-controlled field that contains the
    separator cannot shift field boundaries and collide two distinct rows to
    the same hash. (Deliberately NOT json.dumps — the hash must stay
    computable even when a payload that broke ``json.dumps`` was already
    coerced to ``"{}"`` upstream.)
    """
    fields = [
        prev_hash or "", event_id or "", created_at or "", user_id or "",
        action or "", entity_type or "", entity_id or "", detail_str or "",
    ]
    parts = []
    for f in fields:
        b = f.encode("utf-8")
        parts.append(str(len(b)).encode("ascii") + b":" + b)
    return hashlib.sha256(b"|".join(parts)).hexdigest()


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
        # Hash-chain (v31): read the current head and link the new row to it,
        # ATOMICALLY. Connections are autocommit (isolation_level=None), so a
        # bare ``with conn`` would NOT serialize the head-read against the
        # insert — two writers (app vs scheduler) could read the same head and
        # fork the chain. BEGIN IMMEDIATE takes the WAL write lock up front so
        # the read+insert is serialized across threads AND processes. If the
        # caller already holds a transaction we join it (the audit row then
        # commits atomically with the action being audited).
        own_txn = not conn.in_transaction
        if own_txn:
            conn.execute("BEGIN IMMEDIATE")
        try:
            head = conn.execute(
                "SELECT row_hash FROM audit_events ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            prev_hash = (head["row_hash"] or "") if head is not None else ""
            row_hash = _compute_row_hash(
                prev_hash, event_id, created_at, uid, action,
                entity_type or "", entity_id or "", detail_str,
            )
            conn.execute(
                """
                INSERT INTO audit_events
                  (event_id, created_at, user_id, action, entity_type,
                   entity_id, detail_json, prev_hash, row_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, created_at, uid, action, entity_type or "",
                    entity_id or "", detail_str, prev_hash, row_hash,
                ),
            )
            if own_txn:
                conn.execute("COMMIT")
        except Exception:
            if own_txn:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
            raise  # to the outer try/except — record_audit still never raises
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        # Best-effort: every failure path swallows. We log at DEBUG (not
        # WARNING) so a flaky DB does not flood the operator's log with
        # audit-write noise.
        logger.debug(
            f"auth.audit.record_audit: write failed for action="
            f"{action!r}: {exc}"
        )


def record_screening(
    *,
    subject: str,
    inputs: Optional[dict] = None,
    list_version: str = "",
    score: Optional[float] = None,
    decision: str = "",
    illustrative: bool = True,
    user_id: Optional[str] = None,
) -> None:
    """Record one KYC/compliance-screening run for auditor replay (R128).

    A regulator-facing "show me exactly why this vessel / counterparty was
    cleared on that date" capability: every compliance-risk-score / screening
    run persists a single, structured ``audit_events`` row whose ``detail_json``
    carries the FULL BASIS of the decision — the modeled inputs, the list
    version (or content stamp), the score, the decision, the operator, and the
    wall-clock time (``created_at`` of the row). :func:`record_audit` writes the
    row, so this inherits the same NEVER-RAISES + hash-chain + user-resolution
    contract. The whole basis lives in the existing ``detail_json`` column — no
    schema migration is needed.

    Honesty (R128 / Compliance-tab provenance)
    ------------------------------------------
    The Compliance tab is ILLUSTRATIVE — the SCREENING CONTENT (the SDN/PSC
    matrices and risk weights) is modeled, not a live screen. R128 adds the
    REAL audit SUBSTRATE (record + replay). The recorded basis therefore stamps
    ``illustrative=True`` by default so a replayed clearance truthfully reports
    that its inputs were modeled — the audit trail records exactly what was
    screened (modeled inputs included); it does NOT claim the screen was live.

    Args:
        subject:      The screened SUBJECT — vessel IMO or counterparty id /
                      label. Recorded as the audit ``entity_id`` so a replay
                      can be located by subject. An empty subject is still
                      recorded (the system bucket).
        inputs:       The modeled screening inputs (route / cargo / party /
                      per-component risks / weights — whatever the call site
                      fed the score). Serialized verbatim into the basis.
        list_version: The sanctions / PSC list version, or a content-hash /
                      as-of stamp identifying which list snapshot was screened
                      against. Empty when the call site has no versioned list
                      (recorded as-is so the gap is itself auditable).
        score:        The computed compliance-risk score. Any numeric; coerced
                      to ``float`` so the basis round-trips through JSON. ``None``
                      records a missing score rather than fabricating one.
        decision:     The clearance decision (``"clear"`` / ``"flag"`` /
                      ``"block"``, or the call site's own band label). Free-form.
        illustrative: Whether the screened INPUTS are modeled (the Compliance
                      tab's provenance). Defaults to ``True`` — keep it honest.
        user_id:      The OPERATOR. ``None`` resolves from the active session
                      (the logged-in user who ran the screen); an explicit
                      empty string records a system / unattributed run.

    Returns:
        None. Never raises — a failed audit write must not break the screen
        render (the basis is best-effort, like every other audit hook).
    """
    try:
        score_val: Optional[float]
        if score is None:
            score_val = None
        else:
            try:
                score_val = float(score)
            except (TypeError, ValueError):
                score_val = None

        basis: dict[str, Any] = {
            "subject": subject if isinstance(subject, str) else str(subject),
            "inputs": inputs if isinstance(inputs, dict) else {},
            "list_version": list_version or "",
            "score": score_val,
            "decision": decision or "",
            "illustrative": bool(illustrative),
        }
    except Exception as exc:  # noqa: BLE001 — basis assembly must not raise
        logger.debug(
            f"auth.audit.record_screening: basis assembly failed: {exc}"
        )
        basis = {"subject": "", "inputs": {}, "list_version": "",
                 "score": None, "decision": "", "illustrative": True}

    record_audit(
        "screening_run",
        entity_type="screening",
        entity_id=(subject if isinstance(subject, str) else str(subject)) or "",
        detail=basis,
        user_id=user_id,
    )


def record_login_failure(username_attempt: str = "") -> None:
    """Record a failed login attempt for security review. NEVER raises.

    Called from the login form when ``auth.users.login`` returns None on a
    credential failure (#9 — failed-login auditing; successful logins were
    already audited, failures were not). We store a truncated SHA-256 hash of
    the attempted username — NOT the raw value — so a reviewer can correlate
    repeated hits on a single target (brute-force / credential-stuffing shows
    up as a spike of ``login_failed`` events) WITHOUT persisting a possibly-PII
    or typo'd-real-credential identifier in the clear. The hash is unsalted:
    its purpose is correlation, not secrecy (the audit log is admin-only).

    ``user_id`` is empty — a failed login has no authenticated user — and the
    attempt is recorded uniformly regardless of whether the username exists,
    so the audit carries none of the enumeration signal ``login`` withholds.
    No client IP is recorded: ``login`` is reached only via the Streamlit gate,
    which does not expose the request IP.
    """
    attempt = (username_attempt or "").strip().lower()
    uhash = (
        hashlib.sha256(attempt.encode("utf-8")).hexdigest()[:16]
        if attempt else ""
    )
    record_audit(
        "login_failed",
        entity_type="user",
        detail={"username_hash": uhash},
        user_id="",
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

    Chain note (v31): pruning deletes by ``created_at``, which is normally
    monotonic with insertion order (``rowid``), so a prune removes a clean
    rowid-PREFIX and :func:`engine.audit_search.verify_chain` stays ``ok``
    (it does not check the first surviving row's ``prev_hash``). If clock skew
    ever made ``created_at`` non-monotonic, a prune could leave a hole that
    verify_chain reports as a break — re-establish a clean chain with
    :func:`engine.audit_search.reseal_chain` (an audited admin op).
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


def chain_head() -> Optional[dict]:
    """Current audit-chain head — ``{rowid, row_hash}`` of the last row, or None.

    EXPORT THIS OUT-OF-BAND periodically (e.g. into a signed digest / external
    store) to make the chain truly tamper-evident: an attacker who rewrites the
    whole in-DB chain forward produces a different head than the one already
    anchored elsewhere. Never raises.
    """
    try:
        from state.db import get_connection
        row = get_connection().execute(
            "SELECT rowid AS rid, row_hash FROM audit_events "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"rowid": int(row["rid"]), "row_hash": row["row_hash"] or ""}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"auth.audit.chain_head: read failed: {exc}")
        return None


# Local alias so the import lives next to its single use site — keeps
# the public surface clean and avoids a top-level import for a one-off.
from datetime import timedelta as _timedelta  # noqa: E402


__all__ = [
    "AuditEvent",
    "record_audit",
    "record_screening",
    "record_login_failure",
    "query_audit",
    "prune_old_audit_events",
    "chain_head",
]
