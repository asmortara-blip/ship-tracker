"""engine/alert_escalation.py — per-rule alert escalation chains (v24).

When an alert fires and the on-call operator does not acknowledge it
quickly enough, the alert needs to climb a ladder of fallback
notification channels: page the desk Slack first, then the duty
phone after 15 minutes, then PagerDuty after another 30. Pre-v24 the
engine had no notion of a chain — once ``deliver_alert`` had
delivered to the per-rule target channels there was nothing more it
could do.

This module persists the schema v24 ``alert_escalation_chains`` table
and exposes the end-to-end orchestrator the worker calls every five
minutes. The chain is per-rule (and per-user); each step has its own
``after_minutes`` timer counted from the previous step's fire (or
from ``alerts.created_at`` for the first step).

State machine
-------------
Two columns on the ``alerts`` table track the escalation state:

* ``escalation_step`` — INTEGER 0/1/.... 0 means "no step has fired
  yet; step 1 is next". N means "step N fired; step N+1 is next (or
  the chain is exhausted if no step N+1 exists)".
* ``last_escalated_at`` — ISO-8601 UTC timestamp of the most recent
  step that fired. NULL on never-escalated rows; the engine falls
  back to ``created_at`` so the first step measures from when the
  alert was persisted.

An alert is **due for escalation** when ALL of:
  - ``acknowledged`` is False
  - the chain has a step at ``escalation_step + 1``
  - ``(last_escalated_at or created_at) + step.after_minutes <= now``

Per-user scoping
----------------
Every read and write filters on ``user_id``. Alice's chain on
rule X cannot escalate bob's alert on the same rule — the
get_alerts_due_for_escalation query joins ``alerts.user_id`` to
``alert_escalation_chains.user_id`` so cross-user isolation is
mechanical. The legacy ``user_id=''`` bucket only collides with
other legacy rows (same posture as load_alerts / fire_rule).

Defensive contract
------------------
Every helper in this module NEVER raises. The orchestrator
``run_escalation_pass`` is wrapped in a top-level try/except so a
malformed row cannot break the worker loop. Per-alert errors are
counted in ``failed`` and logged at WARNING level; the loop
continues. This matches the rest of the alert-engine helpers — a
hiccup in escalation must NEVER block the underlying alert
pipeline or any sibling worker job.

Channel secrets are NEVER logged — the channel_id is the only
identifier emitted to the log trail. The actual target URL /
webhook is never inlined into a log line.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EscalationStep:
    """One step in a per-rule escalation chain.

    ``step_number`` is 1-indexed: step 1 fires first, step 2 fires
    after step 1's ``after_minutes`` window has elapsed since step 1
    fired, etc. The (rule_id, user_id, step_number) tuple is UNIQUE
    in the underlying table so ``add_escalation_step`` REPLACEs an
    existing row when the operator edits a step in place.
    """
    chain_id: str
    rule_id: str
    user_id: str
    step_number: int
    after_minutes: int
    channel_id: str
    created_at: str


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_step(row) -> EscalationStep:
    """Project a sqlite3.Row onto the EscalationStep dataclass."""
    return EscalationStep(
        chain_id=row["chain_id"],
        rule_id=row["rule_id"] or "",
        user_id=row["user_id"] or "",
        step_number=int(row["step_number"]),
        after_minutes=int(row["after_minutes"]),
        channel_id=row["channel_id"] or "",
        created_at=row["created_at"] or "",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

def add_escalation_step(
    *,
    rule_id: str,
    user_id: str,
    step_number: int,
    after_minutes: int,
    channel_id: str,
) -> Optional[EscalationStep]:
    """Persist (or replace) one step in a rule's escalation chain.

    Returns the persisted EscalationStep on success, ``None`` on any
    failure (empty arguments, SQLite error, etc.). The
    (rule_id, user_id, step_number) tuple is UNIQUE in the table so
    re-adding the SAME step_number REPLACES the existing row: the
    chain_id is regenerated, the after_minutes / channel_id pair is
    overwritten, and created_at is refreshed to NOW. This is the
    "edit in place" idiom the UI relies on — the operator does not
    have to delete the old row first.

    Never raises. Channel secrets are NEVER logged; only the
    channel_id is referenced in error messages.
    """
    if not rule_id or not user_id or not channel_id:
        logger.warning(
            f"add_escalation_step: missing required field "
            f"(rule_id={rule_id!r}, user_id={user_id!r}, "
            f"channel_id={channel_id!r})"
        )
        return None
    try:
        step_n = int(step_number)
        after_min = int(after_minutes)
    except (TypeError, ValueError):
        logger.warning(
            f"add_escalation_step: step_number/after_minutes must be "
            f"coerceable to int (got {step_number!r}/{after_minutes!r})"
        )
        return None
    if step_n < 1 or after_min < 0:
        logger.warning(
            f"add_escalation_step: step_number must be >= 1 and "
            f"after_minutes >= 0 (got {step_n}/{after_min})"
        )
        return None

    chain_id = _new_id()
    created_at = _now_iso()
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            # The UNIQUE (rule_id, user_id, step_number) index makes
            # INSERT-or-REPLACE the right idiom for "edit in place".
            # We DELETE first (vs ON CONFLICT REPLACE) so the new
            # row carries a fresh chain_id — that gives the UI a
            # stable handle to reference the latest edit by, instead
            # of mutating a chain_id the caller may already hold.
            conn.execute(
                "DELETE FROM alert_escalation_chains "
                "WHERE rule_id = ? AND user_id = ? AND step_number = ?",
                (rule_id, user_id, step_n),
            )
            conn.execute(
                """
                INSERT INTO alert_escalation_chains
                  (chain_id, rule_id, user_id, step_number,
                   after_minutes, channel_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chain_id, rule_id, user_id, step_n,
                    after_min, channel_id, created_at,
                ),
            )
    except Exception as exc:
        logger.warning(
            f"add_escalation_step: SQLite write failed for "
            f"rule_id={rule_id!r} step={step_n}: {exc}"
        )
        return None

    return EscalationStep(
        chain_id=chain_id,
        rule_id=rule_id,
        user_id=user_id,
        step_number=step_n,
        after_minutes=after_min,
        channel_id=channel_id,
        created_at=created_at,
    )


def get_escalation_chain(
    rule_id: str,
    *,
    user_id: str,
) -> list[EscalationStep]:
    """Return the chain for ``rule_id`` ordered by step_number ASC.

    Per-user scoped — alice's chain on rule X is NOT visible to bob.
    The empty-string user_id returns rows in the legacy bucket only
    (NOT every row in the table — that would leak chains across
    tenants). Callers wanting cross-user reads should iterate users.

    Returns an empty list on any internal error or when the rule
    has no chain configured. Never raises.
    """
    if not rule_id:
        return []
    try:
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM alert_escalation_chains
            WHERE rule_id = ? AND user_id = ?
            ORDER BY step_number ASC
            """,
            (rule_id, user_id),
        ).fetchall()
        return [_row_to_step(r) for r in rows]
    except Exception as exc:
        logger.warning(
            f"get_escalation_chain: SQLite read failed for "
            f"rule_id={rule_id!r}: {exc}"
        )
        return []


def delete_escalation_step(chain_id: str, *, user_id: str) -> bool:
    """Delete one step from a chain by chain_id.

    Per-user scoped — the DELETE matches only rows owned by
    ``user_id``. A caller passing another user's chain_id silently
    no-ops (returns False) rather than raising. The empty-string
    user_id can only delete legacy-bucket rows.

    Returns True iff one row was deleted. Never raises.
    """
    if not chain_id:
        return False
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM alert_escalation_chains "
                "WHERE chain_id = ? AND user_id = ?",
                (chain_id, user_id),
            )
        return int(cur.rowcount or 0) > 0
    except Exception as exc:
        logger.warning(
            f"delete_escalation_step: SQLite write failed for "
            f"chain_id={chain_id!r}: {exc}"
        )
        return False


def delete_chain(rule_id: str, *, user_id: str) -> int:
    """Delete EVERY step in a chain for one rule + user.

    Returns the number of rows removed (0 if no chain exists or on
    any internal error). Per-user scoped — alice cannot bulk-delete
    bob's chain by knowing the rule_id.

    Never raises.
    """
    if not rule_id:
        return 0
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM alert_escalation_chains "
                "WHERE rule_id = ? AND user_id = ?",
                (rule_id, user_id),
            )
        return int(cur.rowcount or 0)
    except Exception as exc:
        logger.warning(
            f"delete_chain: SQLite write failed for "
            f"rule_id={rule_id!r}: {exc}"
        )
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Escalation engine
# ─────────────────────────────────────────────────────────────────────────────

def get_alerts_due_for_escalation(
    *,
    now: Optional[datetime] = None,
    user_id: Optional[str] = None,
) -> list[tuple[dict, EscalationStep]]:
    """Find alerts whose next chain step is due to fire.

    For each unacknowledged alert that has a rule_id, looks up the
    next step in the rule's chain (the row at
    ``step_number = alerts.escalation_step + 1``) under the alert
    OWNER's user_id, and yields the pair iff
    ``(last_escalated_at or created_at) + step.after_minutes <= now``.

    The returned dicts mirror the column layout of the alerts table
    so the escalator can stamp ``last_escalated_at`` and
    ``escalation_step`` without re-querying. Each dict carries at
    minimum ``alert_id``, ``user_id``, ``rule_id``, ``severity``,
    ``escalation_step``, ``created_at``, and the columns
    ``_row_to_alert`` reads. The pair-with-step shape makes the
    caller's loop trivially correct: dispatch each pair, then
    stamp.

    ``user_id`` (optional) — when supplied, scopes the SELECT to
    that single user. None (default) returns due alerts across
    every user (the worker pass).

    ``now`` (optional) — defaults to current UTC. Monkeypatched in
    tests to exercise the after_minutes boundary deterministically.

    Returns an empty list on any internal error. Never raises.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    try:
        from state.db import get_connection
    except Exception as exc:
        logger.warning(f"get_alerts_due_for_escalation: db import failed: {exc}")
        return []

    try:
        conn = get_connection()
        # SELECT every unack'd alert that came from a rule (rule_id
        # IS NOT NULL AND != ''); we filter the chain lookup in
        # Python rather than as a JOIN because the chain may be
        # empty for many rules and a LEFT JOIN would still surface
        # those rows. The Python side is also where we compute the
        # after_minutes arithmetic against the alert's last
        # activity timestamp, which is a per-row computation that
        # does not benefit from being pushed to SQL.
        scope_clause = ""
        scope_params: tuple = ()
        if user_id:
            scope_clause = "AND user_id = ?"
            scope_params = (user_id,)

        rows = conn.execute(
            f"""
            SELECT * FROM alerts
            WHERE acknowledged = 0
              AND rule_id IS NOT NULL
              AND rule_id != ''
              {scope_clause}
            """,
            scope_params,
        ).fetchall()
    except Exception as exc:
        logger.warning(f"get_alerts_due_for_escalation: SELECT failed: {exc}")
        return []

    due: list[tuple[dict, EscalationStep]] = []
    for row in rows:
        try:
            alert = _row_to_alert_dict(row)
        except Exception as exc:
            # Row projection failure on one alert must not break
            # the loop. Continue to the next row.
            logger.warning(
                f"get_alerts_due_for_escalation: row projection failed: {exc}"
            )
            continue

        alert_rule_id = alert.get("rule_id") or ""
        alert_user_id = alert.get("user_id") or ""
        if not alert_rule_id:
            # Defensive — should be filtered by the SELECT, but the
            # legacy NULL/'' boundary in SQLite is fiddly enough
            # that a belt-and-braces check is cheap.
            continue

        # Walk the chain to the NEXT step. The chain owner is the
        # alert OWNER — alice's chain on rule X escalates alice's
        # alerts, not bob's, even when both users share the rule
        # definition. Look up only the row at step N+1 — the cheap
        # path uses the (rule_id, step_number) index.
        current_step = int(alert.get("escalation_step", 0) or 0)
        next_step_num = current_step + 1
        step = _lookup_step(
            conn, alert_rule_id, alert_user_id, next_step_num
        )
        if step is None:
            # Chain exhausted (or never configured). The alert
            # stays unacked but no more notifications will fire
            # — the operator's only path forward is to ACK.
            continue

        # Compute the after_minutes window. The first step measures
        # from created_at; later steps measure from the previous
        # step's fire stamp.
        anchor_iso = alert.get("last_escalated_at") or alert.get("created_at")
        if not anchor_iso:
            continue
        try:
            anchor = datetime.fromisoformat(anchor_iso)
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as exc:
            logger.warning(
                f"get_alerts_due_for_escalation: bad anchor timestamp "
                f"for alert_id={alert.get('alert_id')!r}: {exc}"
            )
            continue

        due_at = anchor + timedelta(minutes=step.after_minutes)
        if due_at <= now:
            due.append((alert, step))

    return due


def _lookup_step(
    conn: sqlite3.Connection,
    rule_id: str,
    user_id: str,
    step_number: int,
) -> Optional[EscalationStep]:
    """Return the chain step at ``step_number`` for one rule + user.

    Returns ``None`` when no such step exists. Helper kept
    private — call sites only need the get_alerts_due_for_escalation
    + escalate_alert pair plus the public CRUD helpers above.
    """
    try:
        row = conn.execute(
            """
            SELECT * FROM alert_escalation_chains
            WHERE rule_id = ? AND user_id = ? AND step_number = ?
            """,
            (rule_id, user_id, step_number),
        ).fetchone()
    except Exception as exc:
        logger.warning(
            f"_lookup_step: SELECT failed for rule_id={rule_id!r} "
            f"step={step_number}: {exc}"
        )
        return None
    if row is None:
        return None
    try:
        return _row_to_step(row)
    except Exception as exc:
        logger.warning(f"_lookup_step: row projection failed: {exc}")
        return None


def _row_to_alert_dict(row) -> dict:
    """Project a sqlite3.Row from the alerts table onto a dict.

    Carries the columns the escalator needs to act and dispatch:
    alert_id, user_id, rule_id, severity, escalation_step,
    created_at, last_escalated_at, plus the columns
    ``_row_to_alert`` reads for the dataclass projection. The
    dispatch path re-projects to ShippingAlert via _row_to_alert.
    """
    out: dict = {}
    # Copy every key the Row exposes so the alert dict carries
    # whatever payload columns the dispatch path needs (the
    # ShippingAlert columns plus the v24 escalation columns).
    try:
        keys = row.keys()
    except Exception:
        keys = []
    for k in keys:
        try:
            out[k] = row[k]
        except (KeyError, IndexError):
            continue
    return out


def escalate_alert(
    alert_id: str,
    step: EscalationStep,
    *,
    user_id: str,
) -> bool:
    """Dispatch one alert through ``step.channel_id`` and stamp the
    escalation state on success.

    Pipeline (each stage wrapped so a failure at any point returns
    False without raising):

      1. Load the alert row by alert_id, scoped to user_id (alice
         cannot escalate bob's alert by knowing its id).
      2. Load the delivery channel by channel_id, scoped to
         user_id. A missing channel returns False — the chain step
         points at a channel the operator has deleted.
      3. Dispatch via ``engine.alert_delivery.deliver_alert``. A
         failed dispatch logs the failure and returns False; the
         escalation_step is NOT bumped so the next pass will retry.
         A successful dispatch proceeds to step 4.
      4. UPDATE alerts SET last_escalated_at = NOW,
         escalation_step = step.step_number WHERE alert_id = ?
         AND user_id = ?. This advances the state machine so the
         NEXT pass picks up step N+1 (or exhausts the chain).

    Never raises. Returns True iff the dispatch succeeded AND the
    state-stamp UPDATE succeeded. The channel webhook URL / target
    is NEVER logged — only the channel_id and the
    DeliveryResult.success / status_code / error_msg trio go to
    the log trail.
    """
    if not alert_id:
        return False
    if not isinstance(step, EscalationStep):
        return False

    # Stage 1 — load the alert under user_id scope.
    try:
        from engine.alert_engine_v2 import _row_to_alert
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM alerts WHERE alert_id = ? AND user_id = ?",
            (alert_id, user_id),
        ).fetchone()
    except Exception as exc:
        logger.warning(
            f"escalate_alert: alert lookup failed for "
            f"alert_id={alert_id!r}: {exc}"
        )
        return False
    if row is None:
        logger.warning(
            f"escalate_alert: alert not found in user scope "
            f"(alert_id={alert_id!r}, user_id={user_id!r})"
        )
        return False

    try:
        shipping_alert = _row_to_alert(row)
    except Exception as exc:
        logger.warning(
            f"escalate_alert: row projection failed for "
            f"alert_id={alert_id!r}: {exc}"
        )
        return False

    # Stage 2 — load the channel under user_id scope. We load EVERY
    # channel and pick by channel_id rather than adding a
    # channel_id getter because load_channels already handles the
    # decryption + scope dance correctly. The chain is small
    # (typically < 10 channels per user) so the linear pick is
    # cheap.
    try:
        from engine.alert_delivery import load_channels

        all_channels = load_channels(user_id=user_id)
        channel = next(
            (c for c in all_channels if c.channel_id == step.channel_id),
            None,
        )
    except Exception as exc:
        logger.warning(
            f"escalate_alert: channel lookup failed for "
            f"channel_id={step.channel_id!r}: {exc}"
        )
        return False
    if channel is None:
        # Step points at a channel that no longer exists (deleted
        # by the operator, or scoped to a different user). The
        # escalation_step is NOT bumped — the operator can fix the
        # chain and the next pass will pick up the same step.
        logger.warning(
            f"escalate_alert: channel not found "
            f"(alert_id={alert_id!r}, channel_id={step.channel_id!r}, "
            f"step={step.step_number})"
        )
        return False

    # Stage 3 — dispatch. deliver_alert is contractually
    # non-raising but we wrap belt-and-braces. A non-success
    # DeliveryResult (network blip, channel disabled mid-flight,
    # quiet hours) does NOT bump the state machine so the next
    # pass retries naturally.
    try:
        from engine.alert_delivery import deliver_alert

        result = deliver_alert(shipping_alert, channel)
    except Exception as exc:
        logger.warning(
            f"escalate_alert: deliver_alert raised for "
            f"alert_id={alert_id!r} channel_id={step.channel_id!r}: {exc}"
        )
        return False
    if not getattr(result, "success", False):
        # error_msg is short and safe to log (no webhook URL in it
        # — deliver_alert's failure paths emit "timeout" / "HTTP
        # 502" / "channel in quiet hours" etc.).
        logger.warning(
            f"escalate_alert: dispatch failed for "
            f"alert_id={alert_id!r} channel_id={step.channel_id!r} "
            f"step={step.step_number}: "
            f"status={getattr(result, 'status_code', 0)} "
            f"error={getattr(result, 'error_msg', '')!r}"
        )
        return False

    # Stage 4 — advance the state machine. last_escalated_at
    # anchors the NEXT step's after_minutes window; escalation_step
    # records WHICH step has fired so the next pass picks step N+1.
    try:
        now_iso = _now_iso()
        with conn:
            conn.execute(
                "UPDATE alerts SET last_escalated_at = ?, "
                "escalation_step = ? "
                "WHERE alert_id = ? AND user_id = ?",
                (now_iso, step.step_number, alert_id, user_id),
            )
    except Exception as exc:
        logger.warning(
            f"escalate_alert: state stamp UPDATE failed for "
            f"alert_id={alert_id!r}: {exc}"
        )
        # The dispatch DID succeed even though the stamp failed.
        # We return False so the caller's counts reflect the
        # partially-broken state; the next pass will redeliver
        # because escalation_step never advanced. Idempotency at
        # the alert level — duplicate page is better than a missed
        # page.
        return False

    logger.info(
        f"escalate_alert: alert_id={alert_id!r} dispatched via "
        f"channel_id={step.channel_id!r} (step={step.step_number}, "
        f"user_id={user_id!r})"
    )
    return True


def run_escalation_pass(*, now: Optional[datetime] = None) -> dict:
    """End-to-end orchestrator: find every due alert, escalate it,
    and return the counts.

    Returns ``{"checked": N, "escalated": N, "failed": N}`` where
    each input alert is counted exactly once — ``checked`` is the
    total number of due alerts considered, ``escalated`` is the
    subset where the dispatch + stamp both succeeded, and
    ``failed`` covers every other outcome (missing channel,
    network failure, SQLite write failure, etc.).

    NEVER raises. A catastrophic failure (e.g. get_alerts_due
    cannot reach SQLite) returns all-zero counts so the caller's
    worker loop never breaks.

    The pass walks every user — there is no per-user scoping
    parameter here because the worker runs as a global job. The
    chain itself is per-user (every read filters on user_id) so a
    cross-user leak cannot happen at the SQL level.
    """
    counts = {"checked": 0, "escalated": 0, "failed": 0}
    try:
        due = get_alerts_due_for_escalation(now=now)
    except Exception as exc:
        logger.warning(f"run_escalation_pass: get_alerts_due raised: {exc}")
        return counts

    counts["checked"] = len(due)
    for alert, step in due:
        try:
            alert_id = alert.get("alert_id") or ""
            user_id = alert.get("user_id") or ""
            if not alert_id:
                counts["failed"] += 1
                continue
            ok = escalate_alert(alert_id, step, user_id=user_id)
            if ok:
                counts["escalated"] += 1
            else:
                counts["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            # Defence in depth — escalate_alert is contractually
            # non-raising but the orchestrator must NEVER let one
            # bad alert break the loop for the rest.
            logger.warning(
                f"run_escalation_pass: per-alert exception: {exc}"
            )
            counts["failed"] += 1

    return counts
