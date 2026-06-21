"""engine/audit_search.py — operator-facing search over ``audit_events``.

``auth.audit.query_audit`` covers the original four filter dimensions
(``user_id`` / ``action`` / ``entity_type`` / ``since``) and stops there
on purpose — it is the read substrate for the rest of the codebase and
its signature is consumed by the HTTP API, the operator CLI, and a
handful of audit-export helpers. Adding more filters to it would force
those consumers to widen their own surfaces in lock-step.

Operators reviewing the security log want a richer query:

* "show me every ``login_*`` action in the last 24 h" — an action prefix,
  not an exact verb (``query_audit`` only does exact).
* "find the event where user ``alice@x`` deleted ``report-abc``" — an
  ``entity_id`` filter (``query_audit`` does not expose one).
* "bracket the window: events between ``T-2h`` and ``T-1h``" — both
  ``since`` AND ``until`` (``query_audit`` only does ``since``).
* "search the JSON payload for the string ``rotated``" — a free-text
  grep over ``detail_json`` + ``action`` + ``entity_id``.

This module is the place that lives — a NEW SQL builder that issues its
own parametrised query against ``audit_events`` without modifying
``auth.audit`` and without breaking any existing reader. The dataclass
``AuditSearchQuery`` carries the (small) filter surface; ``search_audit``
runs the query and returns parsed rows + the total match count (so a
paginating UI can render "N matches (of M total)" without a second
round-trip).

Design contract
---------------
* **Every helper NEVER raises.** A DB outage, a malformed row, or a
  pathological ``text`` filter returns an empty result with the same
  shape so the calling UI panel can keep rendering the rest of the
  Data Health tab.
* **All values are bound, never interpolated.** Every user-supplied
  filter lands in the query via a ``?`` placeholder. The free-text
  filter ALSO escapes LIKE meta-characters (``%`` and ``_``) and uses
  ``ESCAPE '\\'`` so a search for a literal underscore matches the
  literal character.
* **No automatic per-user scoping.** ``search_audit`` returns whatever
  matches the supplied query. The UI that wraps it is responsible for
  passing ``current_user_id()`` explicitly when the operator does NOT
  have admin scope. That keeps this module a pure search helper — the
  scoping policy belongs upstream.
* **Sort order is fixed at created_at DESC.** Operators always want
  newest-first when reviewing an audit log; making this configurable
  buys nothing for the (single) UI consumer.

What this module does NOT do
----------------------------
* No write path. Recording is owned by ``auth.audit.record_audit``.
* No vault decryption. ``detail_json`` is returned verbatim — any
  redaction that callers want (e.g. masking Slack webhook URLs) is
  applied at recording time, not here.
* No telemetry / audit-of-the-audit. We do not log the query text —
  it may carry sensitive operational substrings (a username, a
  ticker, a key fragment) that should not bleed into the loguru log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


# ─── Public dataclasses ───────────────────────────────────────────────────

@dataclass
class AuditSearchQuery:
    """Filter set passed to :func:`search_audit` / :func:`search_audit_count`.

    Every field is optional — when ``None`` (or, for ``text``, an empty
    string) the corresponding WHERE-clause fragment is omitted. ``limit``
    is the only required parameter and is hard-capped at the SQL level
    so a caller passing ``limit=10**9`` cannot OOM the process.

    Attributes
    ----------
    user_id:
        Exact match on the event's ``user_id`` column. Pass the empty
        string ``""`` to match legacy "no user" rows (the system bucket);
        pass ``None`` (the default) for "any user".
    action:
        Exact match on the action verb (``"login_success"``,
        ``"save_rules"``, …). Mutually compatible with ``action_prefix``
        — both fragments AND together when both are set.
    action_prefix:
        Substring match anchored at the start of the action column
        (``"login_"`` matches ``"login_success"`` AND ``"login_failure"``).
        The prefix is treated literally — ``%`` and ``_`` inside the
        prefix are LIKE-escaped.
    entity_type:
        Exact match on the entity type (``"alert"`` / ``"rule"`` /
        ``"channel"`` / ``"report"`` / ``"user"`` / …).
    entity_id:
        Exact match on the entity id.
    since:
        ISO-8601 UTC string. Rows with ``created_at >= since`` are
        returned. Combine with ``until`` for a half-open window.
    until:
        ISO-8601 UTC string. Rows with ``created_at < until`` are
        returned. Half-open by convention (matches ``utils.audit_export``).
    text:
        Free-text substring search. Case-insensitive (lowered on both
        sides). Matches any row where the substring appears in
        ``detail_json`` OR ``action`` OR ``entity_id``. The empty
        string is treated as "no filter" (NOT "match everything").
    limit:
        Maximum number of rows to RETURN. The COUNT result is NOT
        affected — ``search_audit`` always reports the full match
        count so the UI can render "showing X of Y" pagination text.
    """
    user_id: Optional[str] = None
    action: Optional[str] = None
    action_prefix: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    text: Optional[str] = None
    limit: int = 100


@dataclass
class AuditSearchResult:
    """Return shape of :func:`search_audit`.

    Attributes
    ----------
    events:
        List of dicts — each row of ``audit_events`` with ``detail_json``
        parsed back to a Python dict. Empty list on no match OR on any
        internal error.
    total_matched:
        The number of rows that matched the WHERE clause BEFORE the
        ``LIMIT`` was applied. Lets the UI render "showing N of M".
        Always ``>= len(events)`` (equal when ``total_matched <= limit``).
    query:
        Echo of the input query so paginating callers do not have to
        thread the filter set through their own state.
    """
    events: list[dict] = field(default_factory=list)
    total_matched: int = 0
    query: Optional[AuditSearchQuery] = None


# ─── Internal helpers ─────────────────────────────────────────────────────

# Character used to escape LIKE meta-characters in user-supplied
# substrings. SQLite accepts any single character via ``ESCAPE '\\'``;
# backslash is the conventional choice and is harmless inside our
# detail_json payloads (those are JSON strings — an embedded backslash
# is already JSON-escaped to ``\\\\``).
_LIKE_ESC = "\\"


def _like_escape(value: str) -> str:
    """Escape LIKE meta-characters so the literal substring is matched.

    SQLite's LIKE treats ``%`` and ``_`` as wildcards. A naive
    ``column LIKE '%' || ? || '%'`` would let a user-supplied ``%``
    match any character — surprising at best, a footgun at worst. We
    pre-escape both meta-characters AND the escape character itself
    so the query is fully reversible.

    The caller must pair this with ``ESCAPE '\\'`` in the SQL fragment
    or SQLite will not honour the escapes.
    """
    if not value:
        return ""
    # Escape the escape character first so a downstream ``%`` → ``\%``
    # substitution does not re-escape our newly introduced backslash.
    return (
        value.replace(_LIKE_ESC, _LIKE_ESC + _LIKE_ESC)
        .replace("%", _LIKE_ESC + "%")
        .replace("_", _LIKE_ESC + "_")
    )


def _parse_detail(raw: Any) -> dict:
    """Parse the ``detail_json`` column back to a Python dict.

    Mirrors ``auth.audit._row_to_event``: a malformed payload (None,
    invalid JSON, non-dict at the top level) degrades to ``{}`` rather
    than raising — operator-facing UIs prefer a partial row to a 500.
    """
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except (TypeError, ValueError):
        return {}


def _build_where(query: AuditSearchQuery) -> tuple[str, list[Any]]:
    """Translate the filter set into a SQL fragment + parameter list.

    Returned tuple is ``(where_sql, params)`` where ``where_sql`` is the
    full ``WHERE ...`` clause (anchored with ``1=1`` so each fragment
    can prepend ``AND``) and ``params`` is the list of bind values in
    the order they appear.

    The function is the single source of truth for the search semantics
    — both ``search_audit`` and ``search_audit_count`` consume it so
    the two helpers can never drift.
    """
    clauses: list[str] = ["1=1"]
    params: list[Any] = []

    # ── Exact-match fragments. Each one is independently optional;
    # ``None`` means "no filter on this column". An explicit empty
    # string IS a valid filter (matches the legacy "no user" rows).
    if query.user_id is not None:
        clauses.append("user_id = ?")
        params.append(query.user_id)
    if query.action is not None:
        clauses.append("action = ?")
        params.append(query.action)
    if query.entity_type is not None:
        clauses.append("entity_type = ?")
        params.append(query.entity_type)
    if query.entity_id is not None:
        clauses.append("entity_id = ?")
        params.append(query.entity_id)

    # ── Time bracket. Half-open: ``since <= created_at < until``.
    # ``query_audit`` uses ``>=`` for ``since`` so we match that
    # convention here for consistency.
    if query.since is not None:
        clauses.append("created_at >= ?")
        params.append(query.since)
    if query.until is not None:
        clauses.append("created_at < ?")
        params.append(query.until)

    # ── action_prefix: LIKE 'prefix%' with the prefix LIKE-escaped so
    # a user-supplied ``%`` inside the prefix matches the literal char.
    if query.action_prefix:
        escaped = _like_escape(query.action_prefix)
        clauses.append("action LIKE ? ESCAPE '\\'")
        params.append(escaped + "%")

    # ── Free-text grep. Lowered on both sides so the match is
    # case-insensitive WITHOUT relying on a per-database COLLATE NOCASE
    # configuration. The three target columns are OR'd together; this
    # is the only place the WHERE-clause uses an inner OR group, so
    # we wrap it in parens to keep the AND chain unambiguous.
    if query.text:
        escaped = _like_escape(query.text.lower())
        pattern = "%" + escaped + "%"
        clauses.append(
            "(lower(detail_json) LIKE ? ESCAPE '\\' "
            "OR lower(action) LIKE ? ESCAPE '\\' "
            "OR lower(entity_id) LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern])

    return "WHERE " + " AND ".join(clauses), params


# ─── Public API: full search ──────────────────────────────────────────────

def search_audit(query: AuditSearchQuery) -> AuditSearchResult:
    """End-to-end search. Builds the WHERE clause, runs the query, returns
    parsed rows + the pre-LIMIT match count. NEVER raises.

    Args:
        query: The :class:`AuditSearchQuery` carrying the filter set.

    Returns:
        :class:`AuditSearchResult`. On any internal failure (DB outage,
        malformed schema, …) returns an empty result with the input
        query echoed back so the caller can still render a "no matches"
        message. ``total_matched`` is always ``0`` on the failure path.
    """
    # Validate the limit early — a non-positive cap is treated as "no
    # rows" (matches the contract of ``query_audit``). We still run
    # the COUNT so the UI can render "M matches" without rows.
    try:
        limit = int(query.limit)
    except (TypeError, ValueError):
        limit = 0

    try:
        from state.db import get_connection

        conn = get_connection()

        where_sql, params = _build_where(query)

        # ── COUNT first so total_matched is always populated even when
        # the LIMIT clips the row set. The COUNT itself is cheap on
        # the indexed columns; for the unindexed text-grep path it is
        # still the only way to honour the "show N of M" UI contract.
        count_sql = "SELECT COUNT(*) AS n FROM audit_events " + where_sql
        count_row = conn.execute(count_sql, tuple(params)).fetchone()
        total_matched = int(count_row["n"]) if count_row else 0

        events: list[dict] = []
        if limit > 0 and total_matched > 0:
            select_sql = (
                "SELECT event_id, created_at, user_id, action, "
                "entity_type, entity_id, detail_json "
                "FROM audit_events "
                + where_sql
                + " ORDER BY created_at DESC LIMIT ?"
            )
            select_params = list(params) + [limit]
            rows = conn.execute(select_sql, tuple(select_params)).fetchall()
            for row in rows:
                events.append({
                    "event_id": row["event_id"],
                    "created_at": row["created_at"],
                    "user_id": row["user_id"] or "",
                    "action": row["action"],
                    "entity_type": row["entity_type"] or "",
                    "entity_id": row["entity_id"] or "",
                    "detail_json": _parse_detail(row["detail_json"]),
                })

        return AuditSearchResult(
            events=events,
            total_matched=total_matched,
            query=query,
        )
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        # No echo of the query text — it may carry sensitive operational
        # substrings that should not bleed into the loguru log.
        logger.debug(f"engine.audit_search.search_audit: failed: {exc}")
        return AuditSearchResult(events=[], total_matched=0, query=query)


# ─── Public API: count-only ───────────────────────────────────────────────

def search_audit_count(query: AuditSearchQuery) -> int:
    """Count the matches without fetching the rows. NEVER raises.

    Useful for a paginating UI that wants to render "M matches" in the
    filter caption before the operator commits to the more expensive
    full fetch. The result is the SAME number ``search_audit`` would
    set on ``total_matched`` for the same query.

    Returns:
        Non-negative int. ``0`` on no match or on any internal failure.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        where_sql, params = _build_where(query)
        sql = "SELECT COUNT(*) AS n FROM audit_events " + where_sql
        row = conn.execute(sql, tuple(params)).fetchone()
        return int(row["n"]) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"engine.audit_search.search_audit_count: failed: {exc}"
        )
        return 0


# ─── Public API: dropdown population helpers ──────────────────────────────

def get_distinct_actions(*, limit: int = 100) -> list[str]:
    """Return the distinct ``action`` values present in the table, sorted.

    Powers the UI panel's "action" selectbox. Caps at ``limit`` so a
    pathological dataset with thousands of action verbs does not blow
    up the dropdown — a real deployment has ~15 distinct verbs.

    Returns:
        Sorted list of action strings. Empty list on any internal
        failure or on a non-positive ``limit``.
    """
    if not isinstance(limit, int) or limit <= 0:
        return []
    try:
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT action FROM audit_events "
            "WHERE action IS NOT NULL AND action <> '' "
            "ORDER BY action ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [r["action"] for r in rows if r["action"]]
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"engine.audit_search.get_distinct_actions: failed: {exc}"
        )
        return []


def get_distinct_entity_types(*, limit: int = 50) -> list[str]:
    """Return the distinct non-empty ``entity_type`` values, sorted.

    Powers the UI panel's "entity type" selectbox. The empty string
    is filtered out — a row with no ``entity_type`` is the "untagged"
    bucket and the dropdown already exposes "(any)" for that case.

    Returns:
        Sorted list of entity-type strings. Empty list on any internal
        failure or on a non-positive ``limit``.
    """
    if not isinstance(limit, int) or limit <= 0:
        return []
    try:
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT entity_type FROM audit_events "
            "WHERE entity_type IS NOT NULL AND entity_type <> '' "
            "ORDER BY entity_type ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [r["entity_type"] for r in rows if r["entity_type"]]
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"engine.audit_search.get_distinct_entity_types: failed: {exc}"
        )
        return []


# ─── Tamper-evident hash-chain verification (rec R100) ─────────────────────


@dataclass
class ChainVerification:
    """Result of walking the audit hash-chain.

    ``ok`` is True when every chained row recomputes to its stored ``row_hash``
    and links to its predecessor. ``n_unchained`` counts legacy pre-v31 rows
    (no ``row_hash``) that sit before the chain start. On a break,
    ``first_break_rowid`` + ``first_break_reason`` locate the earliest anomaly.
    """
    ok: bool
    n_total: int
    n_chained: int
    n_unchained: int
    head: str
    first_break_rowid: Optional[int] = None
    first_break_reason: str = ""
    head_matches_anchor: Optional[bool] = None  # None when no anchor supplied


def verify_chain(expected_head: Optional[str] = None) -> ChainVerification:
    """Verify the audit-event hash-chain end to end.

    Detects in-place edits of an INTERIOR row (its content no longer matches
    its hash), and deletions / reorders / insertions (a row whose ``prev_hash``
    no longer links to the previous chained row). Tolerates a sanctioned
    *prefix* prune: the first chained row's ``prev_hash`` is not checked
    against a predecessor (it may reference a pruned row).

    LIMITATION — tail tampering: editing-and-recomputing the LAST row, or
    truncating the newest N rows, leaves a shorter internally-consistent chain
    and is NOT detectable from the DB alone. Pass ``expected_head`` (a
    previously **out-of-band-anchored** head row_hash, e.g. from
    ``auth.audit.chain_head``) to catch it: a mismatch means the tail was
    edited or truncated. ``head_matches_anchor`` reports the outcome.

    Never raises — a read error returns ``ok=False`` with a reason.
    """
    try:
        from auth.audit import _compute_row_hash
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT rowid AS rid, event_id, created_at, user_id, action, "
            "entity_type, entity_id, detail_json, prev_hash, row_hash "
            "FROM audit_events ORDER BY rowid ASC"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"engine.audit_search.verify_chain: read failed: {exc}")
        return ChainVerification(False, 0, 0, 0, "", None, f"read failed: {exc}")

    n_total = len(rows)
    chained = [r for r in rows if (r["row_hash"] or "")]
    n_unchained = n_total - len(chained)
    head = (chained[-1]["row_hash"] or "") if chained else ""

    prev = None
    for i, r in enumerate(chained):
        recomputed = _compute_row_hash(
            r["prev_hash"] or "", r["event_id"] or "", r["created_at"] or "",
            r["user_id"] or "", r["action"] or "", r["entity_type"] or "",
            r["entity_id"] or "", r["detail_json"] or "{}",
        )
        if recomputed != (r["row_hash"] or ""):
            return ChainVerification(
                False, n_total, len(chained), n_unchained, head,
                int(r["rid"]), "row content does not match its hash (modified)",
            )
        if i > 0 and (r["prev_hash"] or "") != (prev["row_hash"] or ""):
            return ChainVerification(
                False, n_total, len(chained), n_unchained, head,
                int(r["rid"]),
                "prev_hash does not link to the previous row "
                "(insert / delete / reorder)",
            )
        prev = r

    # The internal chain is consistent. If an out-of-band head was anchored,
    # a mismatch here is a tail edit / truncation the in-DB walk cannot see.
    if expected_head is not None:
        matches = (head == expected_head)
        if not matches:
            return ChainVerification(
                False, n_total, len(chained), n_unchained, head, None,
                "head does not match the anchored value (tail edit / truncation)",
                head_matches_anchor=False,
            )
        return ChainVerification(
            True, n_total, len(chained), n_unchained, head, None, "",
            head_matches_anchor=True,
        )

    return ChainVerification(True, n_total, len(chained), n_unchained, head, None, "")


def reseal_chain() -> int:
    """Recompute the whole audit chain in rowid order. Returns rows resealed.

    A MAINTENANCE op for AFTER a sanctioned prune/redact (which breaks the
    chain by design): it restores a valid chain so future writes extend a clean
    head. Because it overwrites the hashes, it erases the chain-break evidence
    of those edits — so it is itself a privileged action and the caller SHOULD
    ``record_audit`` it. Never raises; returns 0 on error.
    """
    try:
        from auth.audit import _compute_row_hash
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT rowid AS rid, event_id, created_at, user_id, action, "
            "entity_type, entity_id, detail_json FROM audit_events "
            "ORDER BY rowid ASC"
        ).fetchall()
        prev = ""
        n = 0
        # Atomic re-seal: BEGIN IMMEDIATE so a crash mid-loop cannot leave a
        # half-resealed (broken) chain (connections are autocommit, so a bare
        # ``with conn`` would commit each UPDATE independently).
        conn.execute("BEGIN IMMEDIATE")
        try:
            for r in rows:
                rh = _compute_row_hash(
                    prev, r["event_id"] or "", r["created_at"] or "",
                    r["user_id"] or "", r["action"] or "", r["entity_type"] or "",
                    r["entity_id"] or "", r["detail_json"] or "{}",
                )
                conn.execute(
                    "UPDATE audit_events SET prev_hash = ?, row_hash = ? "
                    "WHERE rowid = ?",
                    (prev, rh, r["rid"]),
                )
                prev = rh
                n += 1
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
        return n
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"engine.audit_search.reseal_chain: failed: {exc}")
        return 0


# ─── KYC / screening audit replay (R128) ──────────────────────────────────
#
# The "show me exactly why this vessel / counterparty was cleared on that date"
# capability. ``auth.audit.record_screening`` persists one ``screening_run``
# event per compliance-risk-score / screening run, with the full decision basis
# (subject / inputs / list version / score / decision / operator / when) in the
# ``detail_json`` column. These two helpers reconstruct that basis for an
# auditor: one event by id (:func:`replay_screening`), or the time-ordered run
# history of one subject (:func:`screening_history`). Both are READ-only,
# best-effort, and NEVER raise — an auditor UI prefers an empty replay to a 500.

# The action verb + entity_type ``record_screening`` writes. Kept here (not
# imported from auth.audit) so the replay surface does not pull the write path
# into its import graph; the two strings are the contract between writer/reader.
_SCREENING_ACTION = "screening_run"
_SCREENING_ENTITY_TYPE = "screening"


def _flatten_screening_event(event: dict) -> dict:
    """Lift a ``screening_run`` audit event into a flat replay record.

    Merges the event envelope (event_id / created_at / user_id) with the
    recorded basis from ``detail_json`` so the auditor sees one dict carrying
    everything: WHO (``operator``), WHEN (``created_at``), the SUBJECT, the
    modeled INPUTS, the LIST VERSION, the SCORE, the DECISION, and the
    ``illustrative`` provenance flag. Missing basis keys degrade to honest
    empties rather than raising — a partial record still replays what it has.
    """
    basis = event.get("detail_json") or {}
    if not isinstance(basis, dict):
        basis = {}
    return {
        "event_id": event.get("event_id", ""),
        "created_at": event.get("created_at", ""),
        # The operator is the audited user_id; surface it under both the raw
        # column name and the auditor-facing alias so either lookup works.
        "user_id": event.get("user_id", "") or "",
        "operator": event.get("user_id", "") or "",
        "subject": basis.get("subject", event.get("entity_id", "") or ""),
        "inputs": basis.get("inputs", {}) if isinstance(basis.get("inputs"), dict) else {},
        "list_version": basis.get("list_version", ""),
        "score": basis.get("score", None),
        "decision": basis.get("decision", ""),
        "illustrative": bool(basis.get("illustrative", True)),
    }


def replay_screening(event_id: str) -> Optional[dict]:
    """Reconstruct the exact recorded basis of one past screening run. (R128)

    The regulator-facing replay of a single clearance: given the audit
    ``event_id`` of a ``screening_run``, return a flat dict carrying the full
    basis recorded at screen time — ``operator`` / ``created_at`` (when) /
    ``subject`` / ``inputs`` (the modeled screening inputs) / ``list_version`` /
    ``score`` / ``decision`` / ``illustrative``. This is an EXACT round-trip of
    what ``auth.audit.record_screening`` wrote: what was recorded is what
    replays.

    Args:
        event_id: The ``event_id`` of the ``screening_run`` audit row.

    Returns:
        The flat replay record, or ``None`` when the id is empty / unknown /
        not a screening event, or on any read error. Never raises.
    """
    if not event_id or not isinstance(event_id, str):
        return None
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT event_id, created_at, user_id, action, entity_type, "
            "entity_id, detail_json FROM audit_events "
            "WHERE event_id = ? AND action = ? LIMIT 1",
            (event_id, _SCREENING_ACTION),
        ).fetchone()
        if row is None:
            return None
        event = {
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "user_id": row["user_id"] or "",
            "action": row["action"],
            "entity_type": row["entity_type"] or "",
            "entity_id": row["entity_id"] or "",
            "detail_json": _parse_detail(row["detail_json"]),
        }
        return _flatten_screening_event(event)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"engine.audit_search.replay_screening: failed: {exc}")
        return None


def screening_history(subject: str, *, limit: int = 100) -> list[dict]:
    """Time-ordered (newest-first) screening-run history for one subject. (R128)

    "Show me every time this vessel / counterparty was screened." Returns the
    list of flat replay records (same shape as :func:`replay_screening`) for
    every ``screening_run`` whose recorded SUBJECT matches — letting an auditor
    see how a subject's clearance evolved across list versions and operators.

    Args:
        subject: The vessel IMO / counterparty id matched against the audit
                 ``entity_id`` (where ``record_screening`` stamps the subject).
        limit:   Cap on rows returned, hard-enforced in SQL. ``<= 0`` → ``[]``.

    Returns:
        List of flat replay records, ``created_at`` DESC. Empty list on no
        match, an empty subject, or any read error. Never raises.
    """
    if not subject or not isinstance(subject, str):
        return []
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return []
    try:
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT event_id, created_at, user_id, action, entity_type, "
            "entity_id, detail_json FROM audit_events "
            "WHERE action = ? AND entity_id = ? "
            # rowid (monotonic insert order) breaks ties so same-microsecond
            # created_at rows stay deterministically newest-first (review).
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (_SCREENING_ACTION, subject, cap),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"engine.audit_search.screening_history: failed: {exc}")
        return []

    out: list[dict] = []
    for row in rows:
        out.append(_flatten_screening_event({
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "user_id": row["user_id"] or "",
            "action": row["action"],
            "entity_type": row["entity_type"] or "",
            "entity_id": row["entity_id"] or "",
            "detail_json": _parse_detail(row["detail_json"]),
        }))
    return out


__all__ = [
    "AuditSearchQuery",
    "AuditSearchResult",
    "ChainVerification",
    "search_audit",
    "search_audit_count",
    "get_distinct_actions",
    "get_distinct_entity_types",
    "verify_chain",
    "reseal_chain",
    "replay_screening",
    "screening_history",
]
