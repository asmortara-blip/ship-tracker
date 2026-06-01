"""engine/report_scheduler.py — cron-driven report scheduling.

Lets operators configure auto-generated reports on a cron-like schedule
("daily 9am", "every Monday", "every 15 minutes") instead of clicking
"generate" by hand. Schedules persist in the ``report_schedules`` table
(schema v20) so they survive worker restarts.

Design
------
The cron parser is stdlib-only. No ``croniter`` / ``apscheduler``
dependency — a 5-field cron string is small enough to parse by hand and
adding a transitive dependency on this one feature would be overkill.
The parser supports:

* ``*``        — every value the field allows.
* ``*/N``      — every Nth value, starting from the field minimum.
* ``<int>``    — a single literal value.
* ``a,b,c``    — a comma-separated list of literal values (mixed with
                 ``*`` / ``*/N`` per-element).

The parser deliberately does NOT support:

* ``a-b``      — value ranges (e.g. ``1-5`` for Mon-Fri). Keep the
                 surface area small; an operator who wants weekdays
                 can write ``1,2,3,4,5``.
* ``L`` / ``#`` — extensions like "last day of month" or "second
                 Monday". Niche; would balloon the parser.
* per-field timezones — the entire schedule is UTC. The UI displays
                 in the user's tz via ``utils.tz`` at render time.

The five fields are:

    minute    hour    day_of_month    month    day_of_week
    0-59      0-23    1-31            1-12     0-6  (Sunday=0)

When both ``day_of_month`` and ``day_of_week`` are constrained (i.e.
both fields have explicit values, not ``*``), the next-fire computation
takes the OR — matches Vixie cron semantics: "fire if EITHER constraint
matches". When only one is constrained, only that one needs to match.

next_run_at is stored as ISO-8601 UTC text. Storing as text matches
every other timestamp column in the codebase and keeps the SQL layer
agnostic about timezone semantics.

Public surface
--------------
* :class:`ReportSchedule` — dataclass mirroring one DB row.
* :func:`save_schedule(schedule) -> bool` — upsert by ``schedule_id``.
* :func:`load_schedules(*, user_id=None, only_enabled=False) -> list`
  — per-user-scoped list with dual-set semantics (mirrors the v7
  scope-filter contract used by alerts/rules/reports).
* :func:`delete_schedule(schedule_id, *, user_id=None) -> bool`
  — per-user-scoped delete; crossing scope returns False.
* :func:`get_due_schedules(now=None) -> list` — every enabled schedule
  whose ``next_run_at`` is ``<= now``. NOT user-scoped — the worker is
  a global process that fires every user's due schedules.
* :func:`compute_next_run_at(cron_expr, *, base=None) -> datetime`
  — pure function. Returns the first datetime strictly AFTER ``base``
  that matches the cron expression.
* :func:`parse_cron_expr(expr) -> tuple` — returns the five
  component lists (minute, hour, dom, month, dow).
* :func:`validate_cron_expr(expr) -> tuple[bool, str]` — UI helper.
  Returns ``(True, '')`` on valid, ``(False, error_message)`` on
  invalid. Never raises.

The module catches every exception in the read/write helpers and falls
back to safe defaults (empty list, False, None) so a broken schedule
row never blocks the worker pipeline or the UI render.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from loguru import logger


# Per-field min/max bounds — used by ``parse_cron_expr`` to validate
# integer literals and by ``compute_next_run_at`` to enumerate the
# matching set. Sunday is 0 to match cron(8) / crontab(5) on every
# Unix; some calendars (ISO) call Monday 0, we explicitly do not.
_FIELD_BOUNDS = (
    (0, 59),    # minute
    (0, 23),    # hour
    (1, 31),    # day_of_month
    (1, 12),    # month
    (0, 6),     # day_of_week (Sun=0)
)

# Human-readable names so error messages from ``parse_cron_expr`` are
# self-explanatory ("hour value 25 is out of range" instead of "field 2
# value 25 is out of range").
_FIELD_NAMES = ("minute", "hour", "day_of_month", "month", "day_of_week")


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReportSchedule:
    """One row of the ``report_schedules`` table.

    Attributes mirror the SQL columns 1:1. ``last_run_*`` and
    ``next_run_at`` are Optional[str] because the DB stores NULL until
    the first fire (resp. until the first save computes the next time).
    """
    schedule_id: str
    user_id: str
    name: str
    cron_expr: str
    enabled: bool = True
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None
    last_run_message: Optional[str] = None
    next_run_at: Optional[str] = None
    created_at: str = ""
    updated_at: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
#  Cron parser (stdlib only)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_field(token: str, min_val: int, max_val: int, field_name: str) -> list[int]:
    """Parse one cron field into a sorted list of distinct ints.

    Supported forms (each comma-separated element is independent):
      * ``*``      → ``list(range(min_val, max_val + 1))``
      * ``*/N``    → every Nth value starting from ``min_val``
      * ``<int>``  → a single literal value (must be in [min, max])
      * ``a,b,c``  → union of each element's expansion

    Raises ``ValueError`` on:
      * Empty input (after strip).
      * Non-integer literal.
      * ``*/0`` or ``*/N`` with non-positive N.
      * Out-of-range literal.
      * Range syntax ``a-b`` (explicitly unsupported — fail loud so an
        operator who tried it sees the unambiguous error message and
        knows to expand into a comma-list).

    Returns the sorted, de-duplicated list of matching integers.
    """
    token = (token or "").strip()
    if not token:
        raise ValueError(f"{field_name}: empty field")
    out: set[int] = set()
    for elem in token.split(","):
        elem = elem.strip()
        if not elem:
            raise ValueError(f"{field_name}: empty list element")
        if elem == "*":
            out.update(range(min_val, max_val + 1))
            continue
        if elem.startswith("*/"):
            step_str = elem[2:].strip()
            if not step_str:
                raise ValueError(f"{field_name}: */N missing step")
            try:
                step = int(step_str)
            except ValueError as exc:
                raise ValueError(f"{field_name}: */N step is not an int: {step_str!r}") from exc
            if step <= 0:
                raise ValueError(f"{field_name}: */N step must be positive, got {step}")
            out.update(range(min_val, max_val + 1, step))
            continue
        # Range syntax is explicitly unsupported — call it out so the
        # caller knows to expand into a comma-list rather than silently
        # accepting a malformed field.
        if "-" in elem:
            raise ValueError(
                f"{field_name}: range syntax ({elem!r}) is not supported; "
                "expand into a comma-list"
            )
        try:
            value = int(elem)
        except ValueError as exc:
            raise ValueError(f"{field_name}: not an int: {elem!r}") from exc
        if value < min_val or value > max_val:
            raise ValueError(
                f"{field_name}: value {value} is out of range [{min_val},{max_val}]"
            )
        out.add(value)
    if not out:
        # Defensive — every successful branch above adds at least one
        # element, but a future refactor could regress this.
        raise ValueError(f"{field_name}: no matching values")
    return sorted(out)


def parse_cron_expr(expr: str) -> tuple[list[int], list[int], list[int], list[int], list[int]]:
    """Parse a 5-field cron expression into five sorted int lists.

    Returns a tuple ``(minutes, hours, days_of_month, months,
    days_of_week)`` where each element is the sorted list of distinct
    matching values for that field.

    Raises ``ValueError`` on:
      * ``None`` or non-string input.
      * Wrong arity (anything other than exactly 5 whitespace-separated
        tokens).
      * Any per-field error from :func:`_parse_field`.
    """
    if not isinstance(expr, str):
        raise ValueError(f"cron expression must be a string, got {type(expr).__name__}")
    tokens = expr.split()
    if len(tokens) != 5:
        raise ValueError(
            f"cron expression must have exactly 5 fields, got {len(tokens)}: {expr!r}"
        )
    parsed = []
    for token, (mn, mx), name in zip(tokens, _FIELD_BOUNDS, _FIELD_NAMES):
        parsed.append(_parse_field(token, mn, mx, name))
    return (parsed[0], parsed[1], parsed[2], parsed[3], parsed[4])


def validate_cron_expr(expr: str) -> tuple[bool, str]:
    """Inline-validation helper for the UI.

    Returns ``(True, '')`` when ``parse_cron_expr`` succeeds and
    ``(False, str(exc))`` otherwise. NEVER raises — the UI uses this
    on every keystroke and a thrown exception would render red across
    the whole panel.
    """
    try:
        parse_cron_expr(expr)
        return (True, "")
    except Exception as exc:
        return (False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
#  Next-fire computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_next_run_at(cron_expr: str, *, base: Optional[datetime] = None) -> datetime:
    """Return the first datetime strictly AFTER ``base`` that matches.

    ``base`` defaults to ``datetime.now(timezone.utc)``. If ``base`` is
    naive (no tzinfo) it is treated as UTC so the result is always
    tz-aware and comparable to other UTC ISO timestamps in the codebase.

    Algorithm
    ---------
    Brute-force per-minute scan from ``base + 1 minute``. The cron
    domain is bounded (the matching set in any field is at most 60
    elements; the wall-clock distance to the next fire is at most a
    year for every legal cron), so a linear walk is both correct AND
    cheap — typical schedules find their next fire in 1-60 iterations.
    The loop carries a safety cap (a year of minutes) to make a
    pathological misuse loud rather than infinite.

    Vixie-cron day handling
    -----------------------
    When BOTH ``day_of_month`` and ``day_of_week`` are constrained
    (i.e. neither field is ``*``), the day matches if EITHER
    constraint matches. When only one is constrained, only that one
    must match. ``*`` in both fields means every day matches.

    Raises ``ValueError`` when ``parse_cron_expr`` rejects ``cron_expr``
    or when (defensively) the per-minute walk exhausts its safety cap.
    """
    minutes, hours, doms, months, dows = parse_cron_expr(cron_expr)
    # Promoted to sets for O(1) membership checks inside the hot loop.
    minute_set = set(minutes)
    hour_set = set(hours)
    dom_set = set(doms)
    month_set = set(months)
    dow_set = set(dows)

    # Detect whether either day field is "every value" so we can pick
    # OR vs AND semantics for the day match. A field is "wildcard" when
    # its parsed set covers the full domain — we compare against the
    # bound rather than re-parsing the original token because callers
    # may build a schedule programmatically without the literal "*".
    dom_is_wild = (dom_set == set(range(_FIELD_BOUNDS[2][0], _FIELD_BOUNDS[2][1] + 1)))
    dow_is_wild = (dow_set == set(range(_FIELD_BOUNDS[4][0], _FIELD_BOUNDS[4][1] + 1)))

    def _day_matches(dt: datetime) -> bool:
        # Python's ``weekday()`` is Mon=0..Sun=6; cron's is Sun=0..Sat=6.
        # ``isoweekday() % 7`` gives Sun=0..Sat=6 cleanly without a
        # branchy mapping.
        cron_dow = dt.isoweekday() % 7
        in_dom = dt.day in dom_set
        in_dow = cron_dow in dow_set
        if dom_is_wild and dow_is_wild:
            return True
        if dom_is_wild:
            return in_dow
        if dow_is_wild:
            return in_dom
        # Both constrained — Vixie-cron OR semantics.
        return in_dom or in_dow

    if base is None:
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    # Strip seconds + microseconds so we walk a clean per-minute grid.
    # ``+ timedelta(minutes=1)`` guarantees we return a time STRICTLY
    # after ``base`` — the worker would otherwise re-fire a schedule
    # whose ``next_run_at`` equals the call time.
    candidate = base.replace(second=0, microsecond=0) + timedelta(minutes=1)

    # Safety cap — 366 days worth of minute steps. A legal 5-field cron
    # always matches at least once a year (the worst case is something
    # like ``0 0 29 2 *`` which matches once every four years on a
    # leap day — we cap at 4 years to cover it without going infinite).
    max_steps = 366 * 24 * 60 * 4
    for _ in range(max_steps):
        if (candidate.minute in minute_set
                and candidate.hour in hour_set
                and candidate.month in month_set
                and _day_matches(candidate)):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(
        f"compute_next_run_at: no match within 4 years for {cron_expr!r} from base {base.isoformat()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers (timestamps + row mapping)
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_schedule(row) -> ReportSchedule:
    return ReportSchedule(
        schedule_id=row["schedule_id"],
        user_id=row["user_id"] or "",
        name=row["name"] or "",
        cron_expr=row["cron_expr"] or "",
        enabled=bool(row["enabled"]),
        last_run_at=row["last_run_at"],
        last_run_status=row["last_run_status"],
        last_run_message=row["last_run_message"],
        next_run_at=row["next_run_at"],
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"],
    )


# ─────────────────────────────────────────────────────────────────────────────
#  CRUD
# ─────────────────────────────────────────────────────────────────────────────

def save_schedule(schedule: ReportSchedule) -> bool:
    """Insert or replace a schedule row.

    The next_run_at is recomputed on every save so an operator who
    edits a cron expression (or flips ``enabled`` back on) sees the
    new fire time immediately. created_at is preserved when the row
    already exists; updated_at is stamped to "now". Returns True on
    success, False on any failure — the worker / UI never crashes on
    a bad save.
    """
    try:
        from state.db import get_connection

        if not schedule.schedule_id:
            logger.warning("save_schedule: empty schedule_id")
            return False

        # Validate the cron expression up front. A row with an invalid
        # cron_expr would crash ``get_due_schedules`` on next read; we
        # refuse the save rather than poisoning the table.
        try:
            parse_cron_expr(schedule.cron_expr)
        except Exception as exc:
            logger.warning(
                f"save_schedule: invalid cron_expr {schedule.cron_expr!r}: {exc}"
            )
            return False

        # Compute next_run_at fresh on every save so an edit takes
        # effect immediately. If the schedule is disabled we still
        # compute it so re-enabling later does not require a save.
        try:
            next_dt = compute_next_run_at(schedule.cron_expr)
            next_iso = next_dt.isoformat()
        except Exception as exc:
            logger.warning(
                f"save_schedule: compute_next_run_at failed for "
                f"{schedule.cron_expr!r}: {exc}"
            )
            return False

        conn = get_connection()
        now_iso = _now_iso()

        # Preserve created_at across re-saves so the row's age is
        # stable. INSERT OR REPLACE drops the existing row entirely;
        # we re-issue the same created_at to compensate.
        existing = conn.execute(
            "SELECT created_at FROM report_schedules WHERE schedule_id = ?",
            (schedule.schedule_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else (schedule.created_at or now_iso)

        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_schedules
                  (schedule_id, user_id, name, cron_expr, enabled,
                   last_run_at, last_run_status, last_run_message,
                   next_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.schedule_id,
                    schedule.user_id or "",
                    schedule.name or "",
                    schedule.cron_expr,
                    1 if schedule.enabled else 0,
                    schedule.last_run_at,
                    schedule.last_run_status,
                    schedule.last_run_message,
                    next_iso,
                    created_at,
                    now_iso,
                ),
            )
        return True
    except Exception as exc:
        logger.error(f"save_schedule failed: {exc}")
        return False


def load_schedules(
    *,
    user_id: Optional[str] = None,
    only_enabled: bool = False,
) -> list[ReportSchedule]:
    """Return schedules sorted by next_run_at ascending.

    Per-user scoping mirrors the dual-set semantics every other domain
    module uses (see :func:`state.user_scope.scope_filter_sql`): a
    non-empty ``user_id`` returns the user's own rows PLUS legacy
    ``user_id=''`` rows; the empty string returns everything. When
    ``only_enabled`` is True the result is filtered to ``enabled=1``.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        clauses: list[str] = []
        params: list = list(scope_params)
        if only_enabled:
            clauses.append("enabled = 1")
        where = ""
        # ``scope_sql`` already starts with ``AND`` — the WHERE clause
        # must begin with ``1=1`` to give it something to AND with.
        # Add the enabled filter as another AND.
        prefix = "WHERE 1=1"
        scope_part = scope_sql
        enabled_part = " AND enabled = 1" if only_enabled else ""

        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM report_schedules {prefix} {scope_part}{enabled_part} "
            f"ORDER BY (next_run_at IS NULL) ASC, next_run_at ASC, created_at ASC",
            params,
        ).fetchall()
        return [_row_to_schedule(r) for r in rows]
    except Exception as exc:
        logger.error(f"load_schedules failed: {exc}")
        return []


def get_schedule(schedule_id: str, *, user_id: Optional[str] = None) -> Optional[ReportSchedule]:
    """Return one schedule by id, scoped to the user.

    Returns None when the id is unknown OR belongs to a different user
    — the two cases collapse intentionally so a probing caller cannot
    enumerate other users' schedule ids by 404 vs 403.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        row = conn.execute(
            f"SELECT * FROM report_schedules WHERE schedule_id = ? {scope_sql}",
            (schedule_id, *scope_params),
        ).fetchone()
        if row is None:
            return None
        return _row_to_schedule(row)
    except Exception as exc:
        logger.error(f"get_schedule failed: {exc}")
        return None


def delete_schedule(schedule_id: str, *, user_id: Optional[str] = None) -> bool:
    """Remove one schedule by id, scoped to the user.

    Returns True iff a row was actually deleted (i.e. the id exists in
    the caller's scope). Cross-user deletes return False — alice
    cannot delete bob's schedule.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        # Look the row up first so we can return a meaningful boolean
        # (DELETE silently affects zero rows on a cross-user id; we
        # want to distinguish "deleted" from "wasn't yours").
        row = conn.execute(
            f"SELECT schedule_id FROM report_schedules WHERE schedule_id = ? {scope_sql}",
            (schedule_id, *scope_params),
        ).fetchone()
        if row is None:
            return False
        with conn:
            conn.execute(
                "DELETE FROM report_schedules WHERE schedule_id = ?",
                (schedule_id,),
            )
        return True
    except Exception as exc:
        logger.error(f"delete_schedule failed: {exc}")
        return False


def get_due_schedules(now: Optional[datetime] = None) -> list[ReportSchedule]:
    """Return every enabled schedule whose ``next_run_at`` is ``<= now``.

    NOT user-scoped — the worker is a global process and fires every
    user's due schedules. ``now`` defaults to ``datetime.now(timezone.utc)``;
    callers pass an explicit value in tests to make the result
    deterministic.
    """
    try:
        from state.db import get_connection

        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now_iso = now.isoformat()

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT * FROM report_schedules
            WHERE enabled = 1
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (now_iso,),
        ).fetchall()
        return [_row_to_schedule(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_due_schedules failed: {exc}")
        return []


def update_run_state(
    schedule_id: str,
    *,
    status: str,
    message: str,
    last_run_at: Optional[str] = None,
    next_run_at: Optional[str] = None,
) -> bool:
    """Update the bookkeeping columns after a fire.

    Used exclusively by :func:`worker.scheduler.run_report_scheduler_job`
    after attempting to generate the report. ``status`` should be
    ``'ok'`` or ``'error'``; ``message`` is the short human string
    (empty on success, ``str(exc)`` on failure). NOT user-scoped on
    purpose — the worker runs out-of-process and is trusted.

    A failure to update simply logs and returns False; the worker
    keeps iterating over the rest of the due list.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = _now_iso()
        with conn:
            conn.execute(
                """
                UPDATE report_schedules
                SET last_run_at = ?,
                    last_run_status = ?,
                    last_run_message = ?,
                    next_run_at = ?,
                    updated_at = ?
                WHERE schedule_id = ?
                """,
                (
                    last_run_at or now_iso,
                    status,
                    message,
                    next_run_at,
                    now_iso,
                    schedule_id,
                ),
            )
        return True
    except Exception as exc:
        logger.error(f"update_run_state failed: {exc}")
        return False


def new_schedule_id() -> str:
    """Mint a fresh UUID for a new schedule.

    Centralised so callers don't have to import uuid themselves; also
    makes it trivial to swap the id format later (KSUID, etc.) without
    a sweep through CLI / API / UI call sites.
    """
    return str(uuid.uuid4())
