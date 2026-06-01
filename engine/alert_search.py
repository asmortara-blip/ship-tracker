"""engine/alert_search.py — operator-facing search over the ``alerts`` table.

``engine.alert_engine_v2.load_alerts`` returns the user's recent alerts
ordered by ``created_at`` DESC and that is the right shape for the
default Alert Center table — operators scrolling the firehose want the
freshest rows first. What it does NOT cover is the "where did that one
firing about *Suez* go" workflow: ``load_alerts`` has no text filter,
no severity-tier minimum, no port_locode / route_id picker, and no
``until`` upper bound on the window.

This module is the place that lives — a NEW SQL builder that issues its
own parametrised query against ``alerts`` without modifying
``alert_engine_v2`` (which is consumed by half-a-dozen UI and worker
modules and is intentionally read-only from this file). The dataclass
``AlertSearchQuery`` carries the filter surface; ``search_alerts``
returns parsed rows + the pre-LIMIT match count so the calling panel
can render "N matches of M total" without a second round-trip.

Design contract
---------------
* **Every helper NEVER raises.** A DB outage, a malformed row, or a
  pathological ``text`` filter returns an empty result with the same
  shape so the calling UI panel can keep rendering the rest of the
  Alerts tab.
* **All values are bound, never interpolated.** Every user-supplied
  filter lands in the query via a ``?`` placeholder. The free-text
  filter ALSO escapes LIKE meta-characters (``%`` and ``_``) and uses
  ``ESCAPE '\\'`` so a search for a literal underscore matches the
  literal character.
* **Per-user scoping is opt-in but enforced when requested.** Pass
  ``user_id="alice"`` and only rows belonging to alice (PLUS legacy
  ``user_id=''`` rows — the dual-set semantics the rest of the codebase
  uses) are returned. The UI consumer is responsible for resolving the
  current operator's id and passing it explicitly; this module is a
  pure search helper.
* **severity_min is tier-aware.** Severity values are stored as text
  (``CRITICAL`` / ``HIGH`` / ``MEDIUM`` / ``LOW``) but operators want a
  natural "show me HIGH or worse" query. The tier ranks are translated
  to ``severity IN (X, Y, Z)`` so the SQL still uses an indexed equality
  check, not a ``CASE`` expression.
* **Sort order is fixed at ``created_at DESC``.** Operators always want
  newest-first when reviewing alerts; making this configurable buys
  nothing for the (single) UI consumer.

What this module does NOT do
----------------------------
* No write path. Alert recording is owned by
  ``engine.alert_engine_v2.save_alerts``.
* No acknowledge mutation. ``engine.alert_engine_v2.acknowledge_alert``
  owns that surface.
* No fire_count / last_fired_at projection. Those columns are absent
  from the result dict by design — the search panel is for finding
  alerts, not for inspecting their dedup state (that's the existing
  alert table's job). Keeping the dict narrow also keeps the CSV /
  JSONL download legible.
* No telemetry / audit-of-the-search. We do not log the query text —
  it may carry sensitive operational substrings (a username, a port
  locode, a key fragment) that should not bleed into the loguru log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


# ─── Severity tier ranks ──────────────────────────────────────────────────

# Numeric rank for each severity tier. CRITICAL is the most severe;
# LOW is the least. The ranks power ``severity_min`` — a query for
# ``severity_min="HIGH"`` picks every tier whose rank is >= the rank
# of ``HIGH`` (i.e. HIGH and CRITICAL).
#
# Stored as a flat dict (vs. an enum) so the SQL builder can compare
# ranks with a single ``>=`` check and so the value list returned to
# the IN clause is order-independent.
_SEVERITY_RANK: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


def _severities_at_or_above(min_tier: str) -> list[str]:
    """Return the list of severity strings at or above ``min_tier``.

    ``min_tier`` is upper-cased and looked up in the rank table. An
    unknown tier (typo, future severity not yet in the table) returns
    an empty list — the caller's WHERE clause then drops the
    severity_min predicate without crashing the query.

    Returned in DESCENDING rank order so the SQL builder's IN clause
    reads naturally ("CRITICAL or HIGH") but the order is not
    semantically load-bearing.
    """
    if not isinstance(min_tier, str):
        return []
    key = min_tier.strip().upper()
    if key not in _SEVERITY_RANK:
        return []
    threshold = _SEVERITY_RANK[key]
    return sorted(
        (k for k, v in _SEVERITY_RANK.items() if v >= threshold),
        key=lambda k: -_SEVERITY_RANK[k],
    )


# ─── Public dataclasses ───────────────────────────────────────────────────

@dataclass
class AlertSearchQuery:
    """Filter set passed to :func:`search_alerts` / :func:`search_alerts_count`.

    Every field is optional — when ``None`` (or, for ``text``, an empty
    string) the corresponding WHERE-clause fragment is omitted. ``limit``
    is the only required parameter and is hard-capped at the SQL level
    so a caller passing ``limit=10**9`` cannot OOM the process.

    Attributes
    ----------
    user_id:
        Owner filter. Pass an explicit user_id string to restrict the
        result set to that user's alerts (PLUS legacy ``user_id=''``
        rows — same dual-set semantics ``load_alerts`` uses). Pass
        ``None`` (the default) for "any user" — the UI panel is
        responsible for filling this in with the current operator's
        id when the operator does NOT have admin scope.
    severity:
        Exact match on the severity column (``CRITICAL`` / ``HIGH`` /
        ``MEDIUM`` / ``LOW``). Mutually compatible with ``severity_min``
        — both fragments AND together when both are set (the
        intersection is the ``severity`` exact match if it satisfies
        the minimum, else the empty set).
    severity_min:
        Tier-or-worse match. ``"HIGH"`` keeps HIGH and CRITICAL alerts.
        An unknown tier silently drops the predicate.
    alert_type:
        Exact match on the alert_type column (``BDI_MOVE`` /
        ``SIGNAL_FIRE`` / ``CONGESTION`` / ``RATE_SURGE`` /
        ``STOCK_MOVE`` / ``MACRO`` / …).
    ticker:
        Exact match on the ticker column.
    port_locode:
        Exact match on the port_locode column (UN/LOCODE format, e.g.
        ``USLAX``).
    route_id:
        Exact match on the route_id column.
    acknowledged:
        Tri-state filter. ``True`` keeps only ack'd rows; ``False``
        keeps only un-ack'd rows; ``None`` (default) keeps both.
    since:
        ISO-8601 UTC string. Rows with ``created_at >= since`` are
        returned.
    until:
        ISO-8601 UTC string. Rows with ``created_at < until`` are
        returned. Half-open by convention (matches
        ``utils.audit_export`` and the audit search panel).
    text:
        Free-text substring search. Case-insensitive (lowered on both
        sides). Matches any row where the substring appears in the
        concatenation ``title || ' ' || body`` (so "Suez" in either
        field is a hit). The empty string is treated as "no filter"
        (NOT "match everything").
    limit:
        Maximum number of rows to RETURN. The COUNT result is NOT
        affected — ``search_alerts`` always reports the full match
        count so the UI can render "showing X of Y" pagination text.
    """
    user_id: Optional[str] = None
    severity: Optional[str] = None
    severity_min: Optional[str] = None
    alert_type: Optional[str] = None
    ticker: Optional[str] = None
    port_locode: Optional[str] = None
    route_id: Optional[str] = None
    acknowledged: Optional[bool] = None
    since: Optional[str] = None
    until: Optional[str] = None
    text: Optional[str] = None
    limit: int = 100


@dataclass
class AlertSearchResult:
    """Return shape of :func:`search_alerts`.

    Attributes
    ----------
    alerts:
        List of dicts — each row of ``alerts`` projected to the
        column set documented in :func:`_row_to_dict`. Empty list on
        no match OR on any internal error.
    total_matched:
        The number of rows that matched the WHERE clause BEFORE the
        ``LIMIT`` was applied. Lets the UI render "showing N of M".
        Always ``>= len(alerts)`` (equal when
        ``total_matched <= limit``).
    query:
        Echo of the input query so paginating callers do not have to
        thread the filter set through their own state.
    """
    alerts: list[dict] = field(default_factory=list)
    total_matched: int = 0
    query: Optional[AlertSearchQuery] = None


# ─── Internal helpers ─────────────────────────────────────────────────────

# Character used to escape LIKE meta-characters in user-supplied
# substrings. SQLite accepts any single character via ``ESCAPE '\\'``;
# backslash is the conventional choice and is the same character
# ``engine.audit_search`` uses so operators get consistent semantics
# across the two search panels.
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


def _row_to_dict(row: Any) -> dict:
    """Project a sqlite3.Row from the ``alerts`` table to a dict.

    Mirrors ``engine.alert_engine_v2._row_to_alert`` for the canonical
    columns + adds ``user_id`` so the search panel can show which
    operator owns each row (useful in the admin "all users" view).

    NULLable string columns degrade to ``""``; numeric columns are
    coerced via ``float()`` / ``bool()``. Any column lookup that misses
    (pre-v7 row without ``user_id``) falls through to a safe default
    so the helper NEVER raises on a malformed row.
    """
    out: dict = {
        "alert_id":     row["alert_id"],
        "created_at":   row["created_at"],
        "alert_type":   row["alert_type"],
        "severity":     row["severity"],
        "title":        row["title"],
        "body":         row["body"],
        "ticker":       row["ticker"] or "",
        "route_id":     row["route_id"] or "",
        "port_locode":  row["port_locode"] or "",
        "value":        float(row["value"]),
        "threshold":    float(row["threshold"]),
        "change_pct":   float(row["change_pct"]),
        "acknowledged": bool(row["acknowledged"]),
    }
    # The v7 user_id column is nice-to-have for the admin "all users"
    # view. Same safe lookup pattern used by ``_row_to_alert_full``.
    try:
        out["user_id"] = row["user_id"] or ""
    except (IndexError, KeyError):
        out["user_id"] = ""
    return out


def _build_where(query: AlertSearchQuery) -> tuple[str, list[Any]]:
    """Translate the filter set into a SQL fragment + parameter list.

    Returned tuple is ``(where_sql, params)`` where ``where_sql`` is the
    full ``WHERE ...`` clause (anchored with ``1=1`` so each fragment
    can prepend ``AND``) and ``params`` is the list of bind values in
    the order they appear.

    The function is the single source of truth for the search semantics
    — both ``search_alerts`` and ``search_alerts_count`` consume it so
    the two helpers can never drift.
    """
    clauses: list[str] = ["1=1"]
    params: list[Any] = []

    # ── Per-user scope. Dual-set semantics: when a user_id is supplied,
    # rows belonging to that user PLUS legacy ``user_id=''`` rows are
    # returned together (same convention as ``load_alerts``). When
    # ``user_id`` is None the predicate is dropped entirely — the
    # caller wanted "all users" (admin view).
    if query.user_id is not None:
        clauses.append("(user_id = ? OR user_id = '')")
        params.append(query.user_id)

    # ── Exact-match fragments. Each one is independently optional;
    # ``None`` means "no filter on this column".
    if query.severity is not None:
        clauses.append("severity = ?")
        params.append(query.severity)
    if query.alert_type is not None:
        clauses.append("alert_type = ?")
        params.append(query.alert_type)
    if query.ticker is not None:
        clauses.append("ticker = ?")
        params.append(query.ticker)
    if query.port_locode is not None:
        clauses.append("port_locode = ?")
        params.append(query.port_locode)
    if query.route_id is not None:
        clauses.append("route_id = ?")
        params.append(query.route_id)

    # ── severity_min: translate the tier into an IN list. An unknown
    # tier returns an empty list and the predicate is dropped so the
    # query degrades to "no severity-min filter" rather than "no rows".
    if query.severity_min is not None:
        allowed = _severities_at_or_above(query.severity_min)
        if allowed:
            placeholders = ",".join("?" for _ in allowed)
            clauses.append(f"severity IN ({placeholders})")
            params.extend(allowed)

    # ── Acknowledged tri-state. Stored as INTEGER 0/1 in SQLite; bound
    # explicitly as int (vs. relying on the driver's bool → int coercion)
    # so the binding shape is identical across Python versions.
    if query.acknowledged is not None:
        clauses.append("acknowledged = ?")
        params.append(1 if query.acknowledged else 0)

    # ── Time bracket. Half-open: ``since <= created_at < until``.
    if query.since is not None:
        clauses.append("created_at >= ?")
        params.append(query.since)
    if query.until is not None:
        clauses.append("created_at < ?")
        params.append(query.until)

    # ── Free-text grep on ``title || ' ' || body``. The concatenation
    # lets a single LIKE clause cover both fields without needing the
    # query planner to do an OR-of-two-LIKEs (which is harder to
    # short-circuit). Lowered on both sides so the match is
    # case-insensitive WITHOUT relying on a per-database COLLATE
    # NOCASE configuration. The space separator prevents a false
    # match across the field boundary (a search for "endSuez" would
    # otherwise hit ``title="...end"`` + ``body="Suez..."``).
    if query.text:
        escaped = _like_escape(query.text.lower())
        pattern = "%" + escaped + "%"
        clauses.append(
            "lower(title || ' ' || body) LIKE ? ESCAPE '\\'"
        )
        params.append(pattern)

    return "WHERE " + " AND ".join(clauses), params


# ─── Public API: full search ──────────────────────────────────────────────

def search_alerts(query: AlertSearchQuery) -> AlertSearchResult:
    """End-to-end search. Builds the WHERE clause, runs the query, returns
    parsed rows + the pre-LIMIT match count. NEVER raises.

    Args:
        query: The :class:`AlertSearchQuery` carrying the filter set.

    Returns:
        :class:`AlertSearchResult`. On any internal failure (DB outage,
        malformed schema, …) returns an empty result with the input
        query echoed back so the caller can still render a "no matches"
        message. ``total_matched`` is always ``0`` on the failure path.
    """
    # Validate the limit early — a non-positive cap is treated as "no
    # rows" (matches the contract of ``audit_search.search_audit``).
    # We still run the COUNT so the UI can render "M matches" even when
    # the limit is zero.
    try:
        limit = int(query.limit)
    except (TypeError, ValueError):
        limit = 0

    try:
        from state.db import get_connection

        conn = get_connection()

        where_sql, params = _build_where(query)

        # ── COUNT first so total_matched is always populated even when
        # the LIMIT clips the row set.
        count_sql = "SELECT COUNT(*) AS n FROM alerts " + where_sql
        count_row = conn.execute(count_sql, tuple(params)).fetchone()
        total_matched = int(count_row["n"]) if count_row else 0

        alerts: list[dict] = []
        if limit > 0 and total_matched > 0:
            select_sql = (
                "SELECT * FROM alerts "
                + where_sql
                + " ORDER BY created_at DESC LIMIT ?"
            )
            select_params = list(params) + [limit]
            rows = conn.execute(select_sql, tuple(select_params)).fetchall()
            for row in rows:
                try:
                    alerts.append(_row_to_dict(row))
                except Exception as exc:  # noqa: BLE001
                    # A pathological row (NULL where the schema says
                    # NOT NULL, junk in the numeric columns) is skipped
                    # rather than failing the whole search. The
                    # operator still gets every well-formed row in the
                    # match set.
                    logger.debug(
                        f"engine.alert_search.search_alerts: skipping "
                        f"malformed row: {exc}"
                    )

        return AlertSearchResult(
            alerts=alerts,
            total_matched=total_matched,
            query=query,
        )
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        # No echo of the query text — it may carry sensitive operational
        # substrings that should not bleed into the loguru log.
        logger.debug(f"engine.alert_search.search_alerts: failed: {exc}")
        return AlertSearchResult(alerts=[], total_matched=0, query=query)


# ─── Public API: count-only ───────────────────────────────────────────────

def search_alerts_count(query: AlertSearchQuery) -> int:
    """Count the matches without fetching the rows. NEVER raises.

    Useful for a paginating UI that wants to render "M matches" in the
    filter caption before the operator commits to the more expensive
    full fetch. The result is the SAME number ``search_alerts`` would
    set on ``total_matched`` for the same query.

    Returns:
        Non-negative int. ``0`` on no match or on any internal failure.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        where_sql, params = _build_where(query)
        sql = "SELECT COUNT(*) AS n FROM alerts " + where_sql
        row = conn.execute(sql, tuple(params)).fetchone()
        return int(row["n"]) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"engine.alert_search.search_alerts_count: failed: {exc}"
        )
        return 0


# ─── Public API: dropdown population helpers ──────────────────────────────

def _distinct_column(
    column: str,
    *,
    user_id: Optional[str],
    limit: int,
) -> list[str]:
    """Shared helper for the ``get_distinct_*`` family.

    Returns the distinct non-empty values of ``column`` in the alerts
    table, sorted ASC, capped at ``limit``. ``user_id`` applies the
    same dual-set scope as ``search_alerts``; ``None`` means "all
    users". NEVER raises — every failure path returns ``[]`` so a
    busted dropdown does not break the surrounding panel.

    ``column`` is NOT a user-supplied value — it is interpolated
    directly into the SQL. The caller passes a literal column name
    (``"alert_type"`` / ``"ticker"`` / …) from a closed set defined in
    this module, so there is no injection surface.
    """
    if not isinstance(limit, int) or limit <= 0:
        return []
    try:
        from state.db import get_connection

        conn = get_connection()
        clauses: list[str] = [f"{column} IS NOT NULL", f"{column} <> ''"]
        params: list[Any] = []
        if user_id is not None:
            clauses.append("(user_id = ? OR user_id = '')")
            params.append(user_id)
        where_sql = " AND ".join(clauses)
        sql = (
            f"SELECT DISTINCT {column} AS v FROM alerts "
            f"WHERE {where_sql} "
            f"ORDER BY {column} ASC LIMIT ?"
        )
        rows = conn.execute(sql, tuple(params + [int(limit)])).fetchall()
        return [r["v"] for r in rows if r["v"]]
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"engine.alert_search._distinct_column({column!r}): failed: {exc}"
        )
        return []


def get_distinct_alert_types(
    *,
    user_id: Optional[str] = None,
    limit: int = 100,
) -> list[str]:
    """Return the distinct non-empty ``alert_type`` values, sorted ASC.

    Powers the UI panel's "alert type" selectbox. The empty string is
    filtered out — the schema has ``NOT NULL`` on ``alert_type`` so an
    empty value would already be junk, but the filter is cheap.

    Args:
        user_id: Owner filter (dual-set scoping; ``None`` = all users).
        limit:   Hard cap on the returned list (prevents an exotic
                 deployment with thousands of alert types from
                 blowing up the dropdown).

    Returns:
        Sorted list of alert-type strings. Empty list on any internal
        failure or on a non-positive ``limit``.
    """
    return _distinct_column("alert_type", user_id=user_id, limit=limit)


def get_distinct_tickers(
    *,
    user_id: Optional[str] = None,
    limit: int = 200,
) -> list[str]:
    """Return the distinct non-empty ``ticker`` values, sorted ASC.

    Powers the UI panel's "ticker" picker. The default limit is 200
    (vs. 100 for alert_type) because a real deployment tracks many
    more tickers than alert types.
    """
    return _distinct_column("ticker", user_id=user_id, limit=limit)


def get_distinct_route_ids(
    *,
    user_id: Optional[str] = None,
    limit: int = 200,
) -> list[str]:
    """Return the distinct non-empty ``route_id`` values, sorted ASC.

    Powers the UI panel's "route" picker.
    """
    return _distinct_column("route_id", user_id=user_id, limit=limit)


def get_distinct_port_locodes(
    *,
    user_id: Optional[str] = None,
    limit: int = 200,
) -> list[str]:
    """Return the distinct non-empty ``port_locode`` values, sorted ASC.

    Powers the UI panel's "port" picker.
    """
    return _distinct_column("port_locode", user_id=user_id, limit=limit)


# ─── JSONL helper for the UI download button ──────────────────────────────

def alerts_to_jsonl(alerts: list[dict]) -> bytes:
    """Convert a list of alert dicts to UTF-8 JSONL bytes.

    Thin wrapper around the audit JSONL serialiser so the alert search
    panel can offer a "Download as JSONL" button with the same wire
    format (one ``json.dumps`` per row, ``\\n`` terminated, trailing
    newline). The wrapper exists so the UI does not have to know that
    the underlying helper lives in ``utils.audit_export`` — and so a
    later refactor that needs an alert-specific encoding (e.g.
    promoting numeric fields to typed floats) has one place to land.

    NEVER raises — defers entirely to ``utils.audit_export.rows_to_jsonl``
    which already absorbs per-row serialisation failures.
    """
    try:
        from utils.audit_export import rows_to_jsonl

        return rows_to_jsonl(alerts or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"engine.alert_search.alerts_to_jsonl: failed: {exc}"
        )
        return b""


__all__ = [
    "AlertSearchQuery",
    "AlertSearchResult",
    "search_alerts",
    "search_alerts_count",
    "get_distinct_alert_types",
    "get_distinct_tickers",
    "get_distinct_route_ids",
    "get_distinct_port_locodes",
    "alerts_to_jsonl",
]
