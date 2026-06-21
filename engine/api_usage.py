"""engine/api_usage.py — compute-weighted API-usage ledger (R112).

Every *authenticated* hit on ``worker/api_server.py`` is metered into an
atomic per-user / per-route-class / per-month counter stored in the
existing ``kv_state`` table (no schema bump). The point is operator
visibility + the substrate for a future per-user quota: a script that
pulls the cheap ``GET /api/v1/alerts`` 10k times/day is very different
from one that pulls the heavy ``…/supply-lines.xlsx`` export 10k
times/day, so we weight a hit by the *compute class* of its route, not
by a flat 1-per-request.

Design constraints (mirrors ``engine.alert_delivery``'s channel-usage
counter exactly):

* **Atomic increment.** Every bump is a single
  ``INSERT … ON CONFLICT(key) DO UPDATE SET value = value + ?``
  statement so two concurrent hits can't both read-then-write the same
  value and lose a count. ``kv_state.key`` is the PRIMARY KEY so the
  ON CONFLICT(key) clause fires.
* **Best-effort.** Metering is operator telemetry, NOT correctness. A
  kv_state write failure is swallowed (logged at debug); ``bump_usage``
  NEVER raises, so a metering hiccup can never affect the API response
  or the auth decision.
* **Month-keyed (UTC).** The bucket label is the current UTC year-month
  (``YYYY-MM``) so usage rolls over naturally at the month boundary
  without a cron job — last month's row just stops being written to.
* **No schema bump.** Re-uses ``kv_state`` (key/value/updated_at), the
  same table the channel-budget counter lives in.

Key shape: ``api_usage:<user_id>:<route_class>:<YYYY-MM>``.

The route weights are **modeled, not measured** — they're a coarse
ordering of relative cost (a cheap user-scoped GET vs an .xlsx workbook
render vs the full backtests-validator run), chosen so the weighted
total tracks "compute pressure" rather than raw request count. They are
a single source of truth in ``_ROUTE_WEIGHTS`` and easy to retune once
real per-route latency data exists.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Key shape
# ─────────────────────────────────────────────────────────────────────────────

# Prefix for every api-usage kv_state row. Distinct from the channel-
# budget prefix (``channel_usage:``) so the two ledgers never collide.
_API_USAGE_KEY_PREFIX = "api_usage"


def _current_year_month() -> str:
    """Current UTC year-month as ``"YYYY-MM"`` — the bucket label."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _usage_key(user_id: str, route_class: str, year_month: str) -> str:
    """Build the kv_state key for one user × route_class × month counter.

    Shape: ``api_usage:<user_id>:<route_class>:<YYYY-MM>``. ``user_id``
    is the resolved authenticated id (never empty on the metered path —
    only authed hits are metered). ``route_class`` comes from
    :func:`classify_route` so it is always one of the closed set of
    class labels below. Both are bound via SQL parameter substitution
    wherever this key is read or written.
    """
    return f"{_API_USAGE_KEY_PREFIX}:{user_id}:{route_class}:{year_month}"


# ─────────────────────────────────────────────────────────────────────────────
#  Route classifier — pure, testable, no DB
# ─────────────────────────────────────────────────────────────────────────────

# Route-class labels. A closed set so ``get_api_usage`` /
# ``check_api_quota`` can reason about the breakdown without surprises.
ROUTE_CLASS_CHEAP = "cheap"          # ordinary user-scoped GET/POST/etc.
ROUTE_CLASS_EXPORT = "export"        # .xlsx workbook render (heavy I/O + openpyxl)
ROUTE_CLASS_BACKTESTS = "backtests"  # full validator run (cached, but expensive cold)
ROUTE_CLASS_REPORT = "report"        # full report HTML / markdown body render
ROUTE_CLASS_ANALYTICS = "analytics"  # spillover-graph / snapshot scans over history

# Modeled per-class weights (NOT measured). One cheap hit costs 1; the
# heavy classes cost more so the weighted total tracks compute pressure
# rather than request count. Retune here once real latency data exists.
#
#   cheap     1   — a single user-scoped SELECT, the common case
#   report    3   — pulls + serializes a whole report body
#   analytics 4   — scans snapshot history / builds a lead-lag graph
#   export   10   — renders a 6-sheet .xlsx workbook (openpyxl, in-memory)
#   backtests 8   — runs every validator (cached ~300s, but expensive cold)
_ROUTE_WEIGHTS: dict[str, int] = {
    ROUTE_CLASS_CHEAP: 1,
    ROUTE_CLASS_REPORT: 3,
    ROUTE_CLASS_ANALYTICS: 4,
    ROUTE_CLASS_BACKTESTS: 8,
    ROUTE_CLASS_EXPORT: 10,
}


def route_weight(route_class: str) -> int:
    """Weight for a route_class; defaults to the cheap weight for any
    unknown label so a future route that forgets to register still
    meters as at-least-1 rather than 0."""
    return _ROUTE_WEIGHTS.get(route_class, _ROUTE_WEIGHTS[ROUTE_CLASS_CHEAP])


def classify_route(path: str) -> tuple[str, int]:
    """Classify a request path into ``(route_class, weight)``.

    Pure + side-effect-free so it can be unit-tested without a DB or an
    HTTP request. ``path`` is the URL path WITHOUT the query string
    (the caller splits it off). The match is order-sensitive: the most
    specific / heaviest signals are checked first so e.g. the ``.xlsx``
    export classifies as ``export`` even though its path also contains
    ``supply-lines`` (which would otherwise be ``analytics``).

    Unknown / unmatched paths fall through to ``cheap`` — a new route
    that forgets to register here still meters as 1, never 0.
    """
    p = (path or "").lower()

    # Heaviest first. The .xlsx export renders a multi-sheet workbook.
    if p.endswith(".xlsx") or ".xlsx" in p:
        return ROUTE_CLASS_EXPORT, route_weight(ROUTE_CLASS_EXPORT)

    # Backtests-health runs every validator (cached, but the cold run is
    # the most expensive single thing the API can trigger).
    if "/backtests/" in p or p.endswith("/backtests"):
        return ROUTE_CLASS_BACKTESTS, route_weight(ROUTE_CLASS_BACKTESTS)

    # Snapshot-history scans + the spillover lead-lag graph walk the
    # whole persisted snapshot series — materially heavier than a single
    # user-scoped row read.
    if "/spillover-graph" in p or "/snapshots" in p:
        return ROUTE_CLASS_ANALYTICS, route_weight(ROUTE_CLASS_ANALYTICS)

    # Full report body renders (HTML / markdown) pull + serialize a
    # whole report document.
    if "/reports/" in p and (
        p.endswith("/html") or "/html/" in p
        or p.endswith("/markdown") or "/markdown/" in p
    ):
        return ROUTE_CLASS_REPORT, route_weight(ROUTE_CLASS_REPORT)

    # Everything else — ordinary user-scoped reads/writes.
    return ROUTE_CLASS_CHEAP, route_weight(ROUTE_CLASS_CHEAP)


# ─────────────────────────────────────────────────────────────────────────────
#  Ledger writes / reads
# ─────────────────────────────────────────────────────────────────────────────

def bump_usage(user_id: str, route_class: str, *, weight: int = 1) -> None:
    """Atomically add ``weight`` to the ``user_id`` × ``route_class`` ×
    current-UTC-month counter in ``kv_state``.

    Best-effort: a kv_state write failure is swallowed (logged at
    debug). NEVER raises — a metering hiccup must never affect the API
    response or the auth decision. A non-positive ``weight`` or an empty
    ``user_id`` is a no-op (nothing to meter).
    """
    try:
        amt = int(weight)
    except (TypeError, ValueError):
        return
    if amt <= 0 or not user_id:
        return
    ym = _current_year_month()
    key = _usage_key(user_id, route_class, ym)
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        # Atomic single-statement increment-by-``amt`` — two concurrent
        # hits can't both read the same value and lose a count
        # (kv_state.key is the PRIMARY KEY, so ON CONFLICT(key) fires).
        with conn:
            conn.execute(
                "INSERT INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value = CAST(value AS INTEGER) + ?, "
                "updated_at = excluded.updated_at",
                (key, str(amt), now_iso, amt),
            )
    except Exception as exc:  # noqa: BLE001 — metering is best-effort
        logger.debug(f"api_usage.bump_usage: kv_state write failed: {exc}")


def get_api_usage(user_id: str, *, month: Optional[str] = None) -> dict:
    """Return the per-route_class weighted-hit counts for ``user_id`` in
    ``month`` (default current UTC month).

    Returns a dict mapping ``route_class -> count`` (the stored weighted
    total for that class), only for classes that have a row this month.
    Returns ``{}`` for a brand-new user/month and ALSO on any internal
    failure. NEVER raises.

    The values are the *weighted* accumulations (a single .xlsx hit at
    weight 10 shows as 10 under ``export``), because that is what
    ``bump_usage`` writes — see ``check_api_quota`` for the rolled-up
    weighted total.
    """
    ym = month or _current_year_month()
    out: dict[str, int] = {}
    try:
        from state.db import get_connection

        conn = get_connection()
        prefix = f"{_API_USAGE_KEY_PREFIX}:{user_id}:"
        suffix = f":{ym}"
        # LIKE on the prefix narrows the scan to this user; we re-parse
        # the class out of the key and filter on the exact month suffix
        # in Python so a route_class containing odd chars can't be
        # spoofed by a LIKE wildcard.
        rows = conn.execute(
            "SELECT key, value FROM kv_state WHERE key LIKE ? || '%'",
            (prefix,),
        ).fetchall()
        for row in rows:
            k = row["key"]
            if not k.endswith(suffix):
                continue
            # key == api_usage:<uid>:<route_class>:<YYYY-MM>
            # strip prefix and suffix → route_class
            route_class = k[len(prefix):-len(suffix)]
            if not route_class:
                continue
            try:
                out[route_class] = int(row["value"])
            except (TypeError, ValueError):
                continue
    except Exception as exc:  # noqa: BLE001 — read is best-effort
        logger.debug(f"api_usage.get_api_usage: kv_state read failed: {exc}")
        return {}
    return out


# Default monthly weighted-usage ceiling. Modeled, deliberately generous:
# at the cheap weight (1) this allows 100k ordinary hits/month, or ~10k
# .xlsx exports — well above a normal polling script and tight enough to
# flag a runaway client. Callers can override per-request.
_DEFAULT_QUOTA_LIMIT = 100_000


def check_api_quota(
    user_id: str,
    *,
    month: Optional[str] = None,
    limit: int = _DEFAULT_QUOTA_LIMIT,
) -> dict:
    """Roll up ``user_id``'s weighted usage for ``month`` and compare it
    against ``limit``.

    Returns ``{"used": int, "limit": int, "over_quota": bool}`` where
    ``used`` is the sum across every route_class of the stored weighted
    counts (so a heavy-export user crosses the line faster than a
    cheap-GET user at the same raw request count). NEVER raises — on an
    internal failure ``used`` is 0 and ``over_quota`` is False (fail
    open: a backend hiccup must never lock a user out via this advisory
    check).

    This is an ADVISORY surface for operators / a future enforcement
    point. ``api_server`` does NOT block on it today — metering only.
    """
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = _DEFAULT_QUOTA_LIMIT
    if lim <= 0:
        lim = _DEFAULT_QUOTA_LIMIT
    usage = get_api_usage(user_id, month=month)
    used = 0
    for v in usage.values():
        try:
            used += int(v)
        except (TypeError, ValueError):
            continue
    return {"used": used, "limit": lim, "over_quota": used >= lim}
