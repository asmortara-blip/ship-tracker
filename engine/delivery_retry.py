"""engine/delivery_retry.py — persistent retry queue for failed deliveries (v26).

Pre-v26, when an outbound dispatch (Slack / email / SMS / webhook /
Discord / PagerDuty) hit a transient transport failure (HTTP 5xx,
network timeout, SMTP blip), the failure was logged once and the
alert was effectively dropped on the floor. Operators wanted the
"transient blip" path to be self-healing — the alert engine already
persists the alert to SQLite so the in-app surface is unaffected, but
the outbound delivery needs a queue so a 30-second Slack outage
doesn't silently lose pages.

This module persists the schema v26 ``delivery_retry_queue`` table
and exposes:

  * ``enqueue_for_retry``  — called by ``engine.alert_delivery.deliver_alert``
    after a retriable failure (the classifier ``_is_retriable`` decides).
    Idempotent on (alert_id, channel_id): re-enqueueing the same pair
    UPDATEs the existing pending row instead of stacking duplicates.
  * ``get_due_retries`` / ``mark_retry_attempt`` — the building blocks
    of ``run_retry_pass``: get the due rows, re-dispatch, mark
    succeeded / failed based on the outcome.
  * ``list_pending`` / ``list_failed`` / ``list_succeeded_recent`` —
    operator-facing reads for the UI / CLI / API. Per-user scoped.
  * ``cancel_retry`` / ``manual_retry`` — operator overrides. Per-user
    scoped (alice cannot cancel bob's retry).
  * ``cleanup_completed`` — daily prune of finalized rows older than
    the retention window.
  * ``run_retry_pass`` — the worker entry point. Walks every due
    pending retry, re-dispatches, marks the outcome. NEVER raises.

Exponential backoff
-------------------
``MAX_RETRIES = 5`` attempts; backoff is ``60s * 2^(attempt_count - 1)``:

  attempt 1 → 60s   (initial wait after enqueue)
  attempt 2 → 120s  (after the 1st retry attempt fails)
  attempt 3 → 240s
  attempt 4 → 480s
  attempt 5 → 960s

After the 5th attempt fails, ``final_status`` flips to ``'failed'``
permanently. The operator can hand-roll a ``manual_retry`` to reset
the next_attempt_at clock if they want one more shot.

Retriable classification
------------------------
``_is_retriable`` (in ``engine.alert_delivery``) decides whether a
``DeliveryResult`` should enter the queue. The contract:

  * HTTP 5xx server errors + 408 (request timeout) + 429 (rate-limited)
    → True (transport-layer hiccup that may self-heal)
  * Network / connection / timeout errors → True
  * HTTP 4xx client errors (404, 401, 403, ...) → False (operator
    misconfig won't fix itself with a retry — would burn budget for
    nothing)
  * Budget-exceeded / quiet-hours / "below threshold" suppressions
    → False (intentional throttles — retrying would defeat them)

Per-user scoping
----------------
Every helper that lists / cancels / manually-triggers retries filters
by user_id. Alice cannot cancel bob's retries even by guessing the
queue_id. The orchestrator ``run_retry_pass`` is global (no user_id
parameter) because it runs as a worker job; per-row dispatch loads
the channel under the row's owning user_id so cross-user leaks are
mechanically impossible.

Defensive contract
------------------
Every helper in this module NEVER raises. The orchestrator
``run_retry_pass`` wraps each per-row dispatch in its own try/except
so one malformed row cannot break the loop for the rest. Matches the
engine.alert_escalation / engine.alert_silences posture exactly.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Tuning constants
# ─────────────────────────────────────────────────────────────────────────────

# Maximum number of dispatch attempts before the row flips to
# ``final_status='failed'``. The first delivery (the one that
# triggered the enqueue) is NOT counted in attempt_count — the count
# tracks RETRY attempts. ``attempt_count >= MAX_RETRIES`` is the
# exhaust signal.
MAX_RETRIES = 5

# Base seconds for the exponential backoff. The Nth retry waits
# ``BACKOFF_BASE_SECONDS * 2 ** (N - 1)`` seconds before firing:
#   1 → 60s     2 → 120s    3 → 240s     4 → 480s    5 → 960s
BACKOFF_BASE_SECONDS = 60


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RetryEntry:
    """One row in the delivery_retry_queue table.

    Fields mirror the column layout 1:1. ``final_status`` is ``"pending"``
    while a retry is still in the queue and flips to either
    ``"succeeded"`` (after a successful re-dispatch) or ``"failed"``
    (after MAX_RETRIES exhaustion or operator cancel).
    """
    queue_id: str
    alert_id: str
    channel_id: str
    user_id: str
    attempt_count: int
    last_attempt_at: Optional[str]
    last_error: Optional[str]
    next_attempt_at: str
    enqueued_at: str
    final_status: str            # 'pending' | 'succeeded' | 'failed'
    final_at: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now(now: Optional[datetime] = None) -> datetime:
    """Return ``now`` (test-injected) or ``datetime.now(timezone.utc)``."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _now_iso(now: Optional[datetime] = None) -> str:
    return _now(now).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_entry(row) -> RetryEntry:
    """Project a sqlite3.Row onto the RetryEntry dataclass."""
    return RetryEntry(
        queue_id=row["queue_id"],
        alert_id=row["alert_id"] or "",
        channel_id=row["channel_id"] or "",
        user_id=row["user_id"] or "",
        attempt_count=int(row["attempt_count"] or 0),
        last_attempt_at=row["last_attempt_at"],
        last_error=row["last_error"],
        next_attempt_at=row["next_attempt_at"] or "",
        enqueued_at=row["enqueued_at"] or "",
        final_status=row["final_status"] or "pending",
        final_at=row["final_at"],
    )


def _compute_backoff_seconds(attempt_count: int) -> int:
    """Seconds to wait before the next retry given ``attempt_count``.

    ``attempt_count`` here is the value AFTER the just-failed attempt
    has been counted (i.e. 1 after the first retry attempt). The
    returned wait covers the gap until attempt N+1.

    The 1-indexed series: 1 → 60, 2 → 120, 3 → 240, 4 → 480, 5 → 960.
    ``attempt_count < 1`` is clamped to 1 so the function is total.
    """
    n = max(1, int(attempt_count))
    return BACKOFF_BASE_SECONDS * (2 ** (n - 1))


# ─────────────────────────────────────────────────────────────────────────────
#  Public API — enqueue / read / mutate
# ─────────────────────────────────────────────────────────────────────────────

def enqueue_for_retry(
    alert_id: str,
    channel_id: str,
    *,
    user_id: str,
    error_message: str = "",
    now: Optional[datetime] = None,
) -> Optional[RetryEntry]:
    """Enqueue a (alert_id, channel_id) pair for retry on the next
    worker pass.

    Idempotent on (alert_id, channel_id): if a row with
    ``final_status='pending'`` already exists for the same pair, the
    existing row is UPDATEd (last_error overwritten, next_attempt_at
    reset to NOW + 60s) instead of stacking a new row. This prevents
    the queue from growing unboundedly when a channel is repeatedly
    fed the same alert through different retry cycles.

    A finalized row (``succeeded`` / ``failed``) for the same pair is
    LEFT IN PLACE and a fresh pending row is inserted — that history is
    intentional ("this alert+channel pair has been failing on and off").

    Returns the persisted RetryEntry on success, ``None`` on any
    failure (missing args, SQLite error). NEVER raises.
    """
    if not alert_id or not channel_id:
        logger.warning(
            f"enqueue_for_retry: missing required field "
            f"(alert_id={alert_id!r}, channel_id={channel_id!r})"
        )
        return None

    now_iso = _now_iso(now)
    # First-retry wait — fixed at 60s regardless of MAX_RETRIES because
    # the just-failed attempt was the INITIAL delivery, not a retry, so
    # the next attempt sits at attempt_count=1 in the backoff series.
    next_iso = (_now(now) + timedelta(seconds=BACKOFF_BASE_SECONDS)).isoformat()
    error_message = (error_message or "")[:500]

    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            existing = conn.execute(
                "SELECT * FROM delivery_retry_queue "
                "WHERE alert_id = ? AND channel_id = ? "
                "AND final_status = 'pending' "
                "ORDER BY enqueued_at DESC LIMIT 1",
                (alert_id, channel_id),
            ).fetchone()
            if existing is not None:
                # Idempotent UPDATE — the row stays, attempt_count is
                # preserved (the just-failed initial delivery does
                # not count as a retry attempt), error_message is
                # refreshed, next_attempt_at is reset so the next
                # pass picks it up promptly.
                conn.execute(
                    "UPDATE delivery_retry_queue "
                    "SET last_error = ?, next_attempt_at = ? "
                    "WHERE queue_id = ?",
                    (error_message, next_iso, existing["queue_id"]),
                )
                row = conn.execute(
                    "SELECT * FROM delivery_retry_queue WHERE queue_id = ?",
                    (existing["queue_id"],),
                ).fetchone()
                return _row_to_entry(row) if row else None

            queue_id = _new_id()
            conn.execute(
                """
                INSERT INTO delivery_retry_queue
                  (queue_id, alert_id, channel_id, user_id,
                   attempt_count, last_attempt_at, last_error,
                   next_attempt_at, enqueued_at, final_status, final_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    queue_id, alert_id, channel_id, user_id or "",
                    0, None, error_message,
                    next_iso, now_iso, "pending", None,
                ),
            )
            row = conn.execute(
                "SELECT * FROM delivery_retry_queue WHERE queue_id = ?",
                (queue_id,),
            ).fetchone()
            return _row_to_entry(row) if row else None
    except Exception as exc:
        logger.warning(
            f"enqueue_for_retry: SQLite write failed for "
            f"alert_id={alert_id!r} channel_id={channel_id!r}: {exc}"
        )
        return None


def get_due_retries(
    *,
    now: Optional[datetime] = None,
    limit: int = 100,
) -> list[RetryEntry]:
    """Return up to ``limit`` pending retries whose next_attempt_at has
    arrived.

    Order: oldest ``next_attempt_at`` first so a row that has been
    waiting longest is picked up first. ``limit`` caps the batch so a
    huge backlog doesn't blow a single pass (the next pass picks up
    the tail).

    Returns ``[]`` on any failure. NEVER raises.
    """
    try:
        now_iso = _now_iso(now)
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM delivery_retry_queue "
            "WHERE final_status = 'pending' AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at ASC LIMIT ?",
            (now_iso, max(1, int(limit))),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]
    except Exception as exc:
        logger.warning(f"get_due_retries: SQLite read failed: {exc}")
        return []


def mark_retry_attempt(
    queue_id: str,
    *,
    success: bool,
    error_message: str = "",
    now: Optional[datetime] = None,
) -> bool:
    """Record the outcome of a retry attempt.

    Success path:
      ``attempt_count`` is bumped (the retry counts), ``final_status``
      is set to ``"succeeded"``, ``final_at`` is set to NOW. The row
      is left in place so the operator can audit "this pair eventually
      delivered on retry N".

    Failure path:
      ``attempt_count`` is bumped, ``last_attempt_at`` + ``last_error``
      are updated. If the new ``attempt_count`` has reached
      ``MAX_RETRIES``, ``final_status`` flips to ``"failed"`` and
      ``final_at`` is set. Otherwise ``next_attempt_at`` is computed
      via the exponential backoff and the row stays ``pending``.

    Returns True iff the row was found AND updated. NEVER raises.
    """
    if not queue_id:
        return False
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM delivery_retry_queue WHERE queue_id = ?",
            (queue_id,),
        ).fetchone()
        if row is None:
            return False
        new_attempt_count = int(row["attempt_count"] or 0) + 1
        now_iso = _now_iso(now)
        error_message = (error_message or "")[:500]
        with conn:
            if success:
                conn.execute(
                    "UPDATE delivery_retry_queue SET "
                    "attempt_count = ?, last_attempt_at = ?, "
                    "last_error = NULL, "
                    "final_status = 'succeeded', final_at = ? "
                    "WHERE queue_id = ?",
                    (new_attempt_count, now_iso, now_iso, queue_id),
                )
                return True
            if new_attempt_count >= MAX_RETRIES:
                conn.execute(
                    "UPDATE delivery_retry_queue SET "
                    "attempt_count = ?, last_attempt_at = ?, "
                    "last_error = ?, "
                    "final_status = 'failed', final_at = ? "
                    "WHERE queue_id = ?",
                    (
                        new_attempt_count, now_iso, error_message,
                        now_iso, queue_id,
                    ),
                )
                return True
            wait_s = _compute_backoff_seconds(new_attempt_count)
            next_iso = (_now(now) + timedelta(seconds=wait_s)).isoformat()
            conn.execute(
                "UPDATE delivery_retry_queue SET "
                "attempt_count = ?, last_attempt_at = ?, "
                "last_error = ?, next_attempt_at = ? "
                "WHERE queue_id = ?",
                (new_attempt_count, now_iso, error_message, next_iso, queue_id),
            )
            return True
    except Exception as exc:
        logger.warning(
            f"mark_retry_attempt: SQLite write failed for "
            f"queue_id={queue_id!r}: {exc}"
        )
        return False


def _list_by_status(
    statuses: tuple[str, ...],
    *,
    user_id: Optional[str] = None,
    limit: int = 100,
) -> list[RetryEntry]:
    """Shared backend for list_pending / list_failed / list_succeeded_recent.

    ``user_id=None`` returns rows from every user — the operator-wide
    surface for the worker introspection paths. A non-None ``user_id``
    (including the empty-string "legacy bucket") filters by that scope.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        placeholders = ",".join(["?"] * len(statuses))
        if user_id is None:
            sql = (
                f"SELECT * FROM delivery_retry_queue "
                f"WHERE final_status IN ({placeholders}) "
                f"ORDER BY enqueued_at DESC LIMIT ?"
            )
            params: tuple = tuple(statuses) + (max(1, int(limit)),)
        else:
            sql = (
                f"SELECT * FROM delivery_retry_queue "
                f"WHERE final_status IN ({placeholders}) AND user_id = ? "
                f"ORDER BY enqueued_at DESC LIMIT ?"
            )
            params = tuple(statuses) + (user_id, max(1, int(limit)))
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]
    except Exception as exc:
        logger.warning(f"_list_by_status: SQLite read failed: {exc}")
        return []


def list_pending(
    *,
    user_id: Optional[str] = None,
    limit: int = 100,
) -> list[RetryEntry]:
    """List pending retries (final_status='pending'), newest enqueued first."""
    return _list_by_status(("pending",), user_id=user_id, limit=limit)


def list_failed(
    *,
    user_id: Optional[str] = None,
    limit: int = 100,
) -> list[RetryEntry]:
    """List permanently-failed retries (final_status='failed'), newest first."""
    return _list_by_status(("failed",), user_id=user_id, limit=limit)


def list_succeeded_recent(
    *,
    user_id: Optional[str] = None,
    limit: int = 100,
) -> list[RetryEntry]:
    """List succeeded retries (final_status='succeeded'), newest first."""
    return _list_by_status(("succeeded",), user_id=user_id, limit=limit)


def cancel_retry(
    queue_id: str,
    *,
    user_id: str,
    now: Optional[datetime] = None,
) -> bool:
    """Operator-triggered cancel — marks ``queue_id`` as failed with the
    reason ``"cancelled by operator"``.

    Per-user scoped: alice cannot cancel bob's retries even by guessing
    the queue_id. A cross-user attempt returns False (the UPDATE
    matches zero rows). A row that is already finalized (succeeded /
    failed) also returns False — re-cancelling has no meaning.

    NEVER raises.
    """
    if not queue_id:
        return False
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = _now_iso(now)
        with conn:
            cur = conn.execute(
                "UPDATE delivery_retry_queue SET "
                "final_status = 'failed', final_at = ?, "
                "last_error = 'cancelled by operator' "
                "WHERE queue_id = ? AND user_id = ? "
                "AND final_status = 'pending'",
                (now_iso, queue_id, user_id),
            )
            return int(cur.rowcount or 0) > 0
    except Exception as exc:
        logger.warning(
            f"cancel_retry: SQLite write failed for queue_id={queue_id!r}: {exc}"
        )
        return False


def manual_retry(
    queue_id: str,
    *,
    user_id: str,
    now: Optional[datetime] = None,
) -> bool:
    """Operator override — set ``next_attempt_at = NOW`` so the next worker
    pass picks the row up immediately. Used when an operator has fixed
    the underlying issue (e.g. corrected a webhook URL) and wants the
    backoff cleared.

    Per-user scoped + pending-only — a finalized row cannot be
    "re-pending'd" via this call; the operator must enqueue a fresh
    retry instead (or use the API to mark the row failed first).

    NEVER raises. Returns True iff the row was found AND updated.
    """
    if not queue_id:
        return False
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = _now_iso(now)
        with conn:
            cur = conn.execute(
                "UPDATE delivery_retry_queue SET next_attempt_at = ? "
                "WHERE queue_id = ? AND user_id = ? "
                "AND final_status = 'pending'",
                (now_iso, queue_id, user_id),
            )
            return int(cur.rowcount or 0) > 0
    except Exception as exc:
        logger.warning(
            f"manual_retry: SQLite write failed for queue_id={queue_id!r}: {exc}"
        )
        return False


def cleanup_completed(
    *,
    retention_days: int = 14,
    now: Optional[datetime] = None,
) -> int:
    """Delete finalized rows whose ``final_at`` is older than
    ``retention_days``.

    Pending rows are NEVER swept regardless of age (they may be sitting
    waiting on a long backoff). Only ``succeeded`` and ``failed`` rows
    with a non-null ``final_at`` are eligible.

    Returns the number of rows deleted (``0`` on no-op or any error).
    NEVER raises.
    """
    try:
        days = max(1, int(retention_days))
    except (TypeError, ValueError):
        days = 14
    try:
        cutoff = (_now(now) - timedelta(days=days)).isoformat()
        from state.db import get_connection

        conn = get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM delivery_retry_queue "
                "WHERE final_status IN ('succeeded', 'failed') "
                "AND final_at IS NOT NULL AND final_at < ?",
                (cutoff,),
            )
            return int(cur.rowcount or 0)
    except Exception as exc:
        logger.warning(f"cleanup_completed: SQLite write failed: {exc}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Orchestrator — the worker entry point
# ─────────────────────────────────────────────────────────────────────────────

def _redispatch_one(entry: RetryEntry) -> "DeliveryResult":  # noqa: F821
    """Look up the (alert, channel) pair for ``entry`` and call
    ``deliver_alert``.

    Lazy imports keep ``engine.alert_delivery`` off the module-load
    critical path. Returns a synthetic DeliveryResult on missing-row
    cases so the caller can mark + count the outcome uniformly.
    """
    from engine.alert_delivery import DeliveryResult, deliver_alert, load_channels
    from engine.alert_engine_v2 import _row_to_alert
    from state.db import get_connection

    # 1. Resolve the alert row under the entry's owning user_id scope.
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM alerts WHERE alert_id = ? AND user_id = ?",
            (entry.alert_id, entry.user_id),
        ).fetchone()
        if row is None:
            # Cross-user attempt or alert deleted between enqueue +
            # retry. Mark as permanent failure rather than leaving it
            # stuck in the queue indefinitely.
            return DeliveryResult(
                success=False, status_code=0,
                error_msg="alert not found in user scope",
            )
        alert = _row_to_alert(row)
    except Exception as exc:
        return DeliveryResult(
            success=False, status_code=0,
            error_msg=f"alert lookup failed: {exc}",
        )

    # 2. Resolve the channel under the same scope.
    try:
        channels = load_channels(user_id=entry.user_id)
        channel = next(
            (c for c in channels if c.channel_id == entry.channel_id), None,
        )
        if channel is None:
            return DeliveryResult(
                success=False, status_code=0,
                error_msg="channel not found in user scope",
            )
    except Exception as exc:
        return DeliveryResult(
            success=False, status_code=0,
            error_msg=f"channel lookup failed: {exc}",
        )

    # 3. Re-dispatch. ``deliver_alert`` is non-raising by contract;
    # the belt-and-braces try is in case a future change regresses
    # that property.
    try:
        return deliver_alert(alert, channel)
    except Exception as exc:
        return DeliveryResult(
            success=False, status_code=0,
            error_msg=f"deliver_alert raised: {exc}",
        )


def run_retry_pass(*, now: Optional[datetime] = None) -> dict:
    """Worker entry — process every due pending retry.

    For each row:
      * Re-dispatch via ``_redispatch_one``.
      * Mark the outcome via ``mark_retry_attempt``.
      * On success: count as ``succeeded``.
      * On failure that exhausts MAX_RETRIES: count as
        ``max_retries_exhausted``.
      * On other failure (still pending after backoff): count as
        ``failed``.

    Returns ``{processed, succeeded, failed, max_retries_exhausted}``.
    NEVER raises — a per-row exception is swallowed and counted as
    ``failed`` so one bad row cannot break the loop for the rest.
    """
    counts = {
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "max_retries_exhausted": 0,
    }
    try:
        due = get_due_retries(now=now)
    except Exception as exc:
        # get_due_retries is already non-raising but this belt-and-
        # braces guard means a regression there cannot take the
        # worker loop down.
        logger.warning(f"run_retry_pass: get_due_retries raised: {exc}")
        return counts

    for entry in due:
        counts["processed"] += 1
        # Snapshot the attempt_count BEFORE we mark, so we can tell
        # whether this attempt was the one that hit MAX_RETRIES.
        attempt_before = entry.attempt_count
        try:
            result = _redispatch_one(entry)
            ok = bool(getattr(result, "success", False))
            err = "" if ok else (getattr(result, "error_msg", "") or "")[:500]
            marked = mark_retry_attempt(
                entry.queue_id,
                success=ok,
                error_message=err,
                now=now,
            )
            if not marked:
                # The row vanished between get_due and mark — count
                # as failed but don't error out.
                counts["failed"] += 1
                continue
            if ok:
                counts["succeeded"] += 1
            else:
                # If THIS attempt was the one that crossed MAX_RETRIES
                # the row is now final_status='failed'. Otherwise it's
                # still pending with a longer backoff.
                if attempt_before + 1 >= MAX_RETRIES:
                    counts["max_retries_exhausted"] += 1
                else:
                    counts["failed"] += 1
        except Exception as exc:
            logger.warning(
                f"run_retry_pass: per-row exception for "
                f"queue_id={entry.queue_id!r}: {exc}"
            )
            counts["failed"] += 1

    return counts
