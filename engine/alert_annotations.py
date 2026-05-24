"""engine/alert_annotations.py — per-alert operator commentary threads.

Pre-v23 the only writable field on an alert was ``acknowledged_note``
— a single string set once at acknowledgement. Operators wanted a
real running thread of context as the response evolves: "escalated
to ops team", "monitoring overnight", "RCA in JIRA-1234". This
module persists the schema v23 ``alert_annotations`` table and
exposes the CRUD surface used by the alert-center UI, the operator
CLI, and the API.

Per-user scoping
----------------
Annotations are scoped to the OWNER of the alert (``user_id``).
Alice cannot see annotations on Bob's alerts; bob cannot list / edit
/ delete annotations attached to alice's alerts. The ``user_id``
filter lands on every read.

The optional ``author_user_id`` (defaults to ``user_id``) identifies
who actually WROTE the comment — usually equals the owner, but a
multi-user-share workflow may differ (a teammate granted shared
visibility leaves a note on someone else's alert). Edit / delete
authorisation matches the AUTHOR, not the owner — only the author
can mutate their own row.

Body handling
-------------
Bodies are stored VERBATIM (no HTML stripping, no markdown
rendering). The UI layer renders safely (st.text, NOT st.markdown)
— see ``ui.tab_alerts._render_alert_annotations_thread``. A body
longer than ``_MAX_BODY_LEN`` (4000 chars) is silently truncated at
write time so a pasted JIRA dump cannot blow up the row size.

The body is NEVER logged via ``logger.*`` anywhere in this module —
operators may paste sensitive context (customer names, ticket
numbers, credentials) and the audit log already captures the
mutation via ``record_audit``.

Defensive contract
------------------
Every helper NEVER raises. Reads return ``[]`` / ``None`` / ``{}``
on any internal error; writes return ``None`` / ``False``. The
contract matches the rest of the alert-engine helpers — operator
UX requires that a hiccup in the annotation layer cannot block the
underlying alert pipeline.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AlertAnnotation:
    """One row of the ``alert_annotations`` table.

    ``user_id`` is the OWNER of the alert (per-user scoping).
    ``author_user_id`` is the user who WROTE this comment — usually
    equals ``user_id`` but may differ in a multi-user-share
    workflow. ``edited_at`` is ``None`` on a never-edited row;
    flipped to NOW on a successful author-match edit.
    """
    annotation_id: str
    alert_id: str
    user_id: str
    author_user_id: str
    body: str
    created_at: str
    edited_at: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

# Hard cap on the body length stored in the row. A pasted JIRA dump
# or stack trace can easily exceed this — we silently truncate at
# write time so the row never grows unbounded. The choice is
# documented in the module docstring; downstream tests pin the
# truncate-not-reject behaviour.
_MAX_BODY_LEN: int = 4000


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_annotation(row) -> AlertAnnotation:
    """Project a sqlite3.Row onto the dataclass shape."""
    return AlertAnnotation(
        annotation_id=row["annotation_id"],
        alert_id=row["alert_id"],
        user_id=row["user_id"] or "",
        author_user_id=row["author_user_id"] or "",
        body=row["body"] or "",
        created_at=row["created_at"] or "",
        edited_at=row["edited_at"],
    )


def _truncate_body(body: object) -> Optional[str]:
    """Coerce + truncate a body to TEXT under ``_MAX_BODY_LEN`` chars.

    Returns ``None`` when the body is missing entirely (caller's
    responsibility to drop the write) or when coercion raises. The
    truncate is silent — callers do NOT get an error on overlong
    bodies; the row simply lands with the first ``_MAX_BODY_LEN``
    chars. This matches the operator UX expectation that a pasted
    blob does not bounce.
    """
    if body is None:
        return None
    try:
        s = str(body)
    except Exception:
        return None
    if len(s) > _MAX_BODY_LEN:
        s = s[:_MAX_BODY_LEN]
    return s


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

def add_annotation(
    alert_id: str,
    body: object,
    *,
    user_id: str,
    author_user_id: Optional[str] = None,
) -> Optional[AlertAnnotation]:
    """Persist a new annotation on ``alert_id`` and return it.

    ``user_id`` is the OWNER of the alert (per-user scoping — alice
    cannot leave a note that bob would see on his own list). The
    function does NOT verify that ``alert_id`` exists in the alerts
    table; the row is created regardless. This matches the
    audit_events pattern (audit rows are written even when the
    referenced entity is missing — operator history is more
    important than referential perfection).

    ``author_user_id`` (optional) — who wrote this comment. Defaults
    to ``user_id`` when omitted, which is the single-user case. The
    author is the only operator authorised to edit / delete this
    row later.

    The body is silently truncated at ``_MAX_BODY_LEN`` (4000) chars
    so a pasted blob cannot blow up the row size. An empty / None
    body returns ``None`` (the write is dropped — an empty
    annotation carries no signal).

    Returns the persisted AlertAnnotation on success, ``None`` on
    any failure. Never raises. Bodies are NEVER logged.
    """
    try:
        from state.db import get_connection

        if not alert_id:
            logger.warning("add_annotation: empty alert_id, dropping")
            return None
        if not user_id:
            logger.warning("add_annotation: empty user_id, dropping")
            return None

        safe_body = _truncate_body(body)
        if not safe_body:
            # Empty / None / whitespace-collapsing — no useful signal.
            # Strip-then-check so a body that's only whitespace also drops.
            return None
        if not safe_body.strip():
            return None

        author = author_user_id if author_user_id else user_id

        annotation = AlertAnnotation(
            annotation_id=_new_id(),
            alert_id=alert_id,
            user_id=user_id,
            author_user_id=author,
            body=safe_body,
            created_at=_now_iso(),
            edited_at=None,
        )

        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO alert_annotations
                  (annotation_id, alert_id, user_id, author_user_id,
                   body, created_at, edited_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation.annotation_id,
                    annotation.alert_id,
                    annotation.user_id,
                    annotation.author_user_id,
                    annotation.body,
                    annotation.created_at,
                    annotation.edited_at,
                ),
            )
        return annotation
    except Exception as exc:
        # Body is NOT included in the log message.
        logger.warning(
            f"add_annotation: SQLite insert failed for "
            f"alert_id={alert_id!r}: {exc}"
        )
        return None


def list_annotations(
    alert_id: str,
    *,
    user_id: str,
) -> list[AlertAnnotation]:
    """Return all annotations on ``alert_id`` in created_at ASC order.

    Per-user scoped — alice does not see annotations on bob's
    alerts even if they share the same alert_id by coincidence
    (alert_id is a UUID so collision is effectively impossible, but
    the user_id filter is a hard requirement of the design).

    Returns an empty list on any internal error or when the alert
    has no annotations. Never raises.
    """
    try:
        from state.db import get_connection

        if not alert_id:
            return []
        if not user_id:
            # An anonymous caller has no scope — return empty rather
            # than leaking every annotation in the table.
            return []

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM alert_annotations
            WHERE alert_id = ? AND user_id = ?
            ORDER BY created_at ASC
            """,
            (alert_id, user_id),
        ).fetchall()
        return [_row_to_annotation(r) for r in rows]
    except Exception as exc:
        logger.warning(
            f"list_annotations: SQLite read failed for "
            f"alert_id={alert_id!r}: {exc}"
        )
        return []


def edit_annotation(
    annotation_id: str,
    new_body: object,
    *,
    user_id: str,
    author_user_id: Optional[str] = None,
) -> bool:
    """Replace the body of one annotation. Returns True on a
    successful update.

    Author authorisation: the UPDATE matches rows where BOTH
    ``user_id`` AND ``author_user_id`` line up. An operator who is
    NOT the author of the row gets ``False`` — they cannot rewrite
    someone else's note. ``author_user_id`` defaults to ``user_id``
    when omitted (single-user case).

    On success, ``edited_at`` is stamped with the current UTC ISO
    timestamp so the UI can render "(edited)" next to the body. A
    no-op update (body unchanged) still stamps ``edited_at`` — the
    SQL UPDATE doesn't compare old vs new.

    Empty / None bodies are rejected (False) — an edit that empties
    the body should use ``delete_annotation`` instead. Bodies are
    silently truncated at ``_MAX_BODY_LEN`` chars on the way in.

    Returns False on:
      * empty annotation_id / user_id
      * empty / None new_body
      * row not in caller's scope (different user_id or
        author_user_id)
      * unknown annotation_id
      * any SQLite error

    Never raises. Bodies are NEVER logged.
    """
    try:
        from state.db import get_connection

        if not annotation_id:
            return False
        if not user_id:
            return False

        safe_body = _truncate_body(new_body)
        if not safe_body or not safe_body.strip():
            return False

        author = author_user_id if author_user_id else user_id

        conn = get_connection()
        # Lookup-then-update so we can distinguish "not yours" from
        # "wasn't there" — both still return False (no-leak: a
        # probing caller cannot enumerate other users' annotation
        # ids by 403 vs 404), but the lookup keeps the UPDATE from
        # silently no-opping when the scope mismatches.
        row = conn.execute(
            "SELECT annotation_id FROM alert_annotations "
            "WHERE annotation_id = ? AND user_id = ? "
            "AND author_user_id = ?",
            (annotation_id, user_id, author),
        ).fetchone()
        if row is None:
            return False

        edited_iso = _now_iso()
        with conn:
            conn.execute(
                "UPDATE alert_annotations SET body = ?, edited_at = ? "
                "WHERE annotation_id = ?",
                (safe_body, edited_iso, annotation_id),
            )
        return True
    except Exception as exc:
        logger.warning(
            f"edit_annotation: SQLite update failed for "
            f"annotation_id={annotation_id!r}: {exc}"
        )
        return False


def delete_annotation(
    annotation_id: str,
    *,
    user_id: str,
    author_user_id: Optional[str] = None,
) -> bool:
    """Delete one annotation. Returns True iff a row was deleted.

    Same author-authorisation contract as ``edit_annotation``: the
    DELETE matches rows where BOTH ``user_id`` AND ``author_user_id``
    line up. Cross-author / cross-user attempts collapse to False
    so a probing caller cannot enumerate other users' annotation
    ids by error-code differential.

    Returns False on:
      * empty annotation_id / user_id
      * row not in caller's scope
      * unknown annotation_id
      * any SQLite error

    Never raises.
    """
    try:
        from state.db import get_connection

        if not annotation_id:
            return False
        if not user_id:
            return False

        author = author_user_id if author_user_id else user_id

        conn = get_connection()
        row = conn.execute(
            "SELECT annotation_id FROM alert_annotations "
            "WHERE annotation_id = ? AND user_id = ? "
            "AND author_user_id = ?",
            (annotation_id, user_id, author),
        ).fetchone()
        if row is None:
            return False
        with conn:
            conn.execute(
                "DELETE FROM alert_annotations WHERE annotation_id = ?",
                (annotation_id,),
            )
        return True
    except Exception as exc:
        logger.warning(
            f"delete_annotation: SQLite delete failed for "
            f"annotation_id={annotation_id!r}: {exc}"
        )
        return False


def count_annotations_per_alert(
    alert_ids: Iterable[str],
    *,
    user_id: str,
) -> dict[str, int]:
    """Return ``{alert_id: count}`` of annotations for each requested
    alert.

    Used by the alert table UI to render a "💬 N" badge next to each
    row without one query per alert. Alerts with zero annotations
    DO NOT appear in the result dict — callers default to 0 on a
    missing key (the typical case is "most alerts have zero
    annotations"; returning every requested id in the dict would
    waste memory on the empty-set common case).

    Per-user scoped — annotations on bob's alerts do not contribute
    to alice's counts even if she happens to know the alert_id.

    Returns ``{}`` on:
      * empty input
      * empty user_id
      * any SQLite error

    Never raises.
    """
    try:
        from state.db import get_connection

        if not user_id:
            return {}
        ids = [a for a in alert_ids if a]
        if not ids:
            return {}

        # SQLite placeholders need a (?, ?, ?) tuple of the right
        # arity. Cap to a reasonable batch size — 1000 — so a wild
        # caller cannot blow past SQLITE_MAX_VARIABLE_NUMBER (the
        # stdlib default is 999; we stay safely under).
        if len(ids) > 900:
            ids = ids[:900]

        placeholders = ",".join("?" * len(ids))
        sql = (
            f"SELECT alert_id, COUNT(*) AS n FROM alert_annotations "
            f"WHERE user_id = ? AND alert_id IN ({placeholders}) "
            f"GROUP BY alert_id"
        )
        conn = get_connection()
        rows = conn.execute(sql, (user_id, *ids)).fetchall()
        return {r["alert_id"]: int(r["n"] or 0) for r in rows}
    except Exception as exc:
        logger.warning(
            f"count_annotations_per_alert: SQLite read failed: {exc}"
        )
        return {}
