"""Slim, SQLite-persistable InvestorReport snapshot.

The "What Changed" diff widget on the daily briefing tab needs two
InvestorReport instances to compare. Historically those snapshots only
lived in ``st.session_state``, which resets on every Streamlit restart —
the diff was effectively useless across deploys.

This module replaces the in-memory rotation with a slim, durable
snapshot. We never try to round-trip the full ``InvestorReport`` because
some of its nested fields hold pandas objects (``alpha.scorecard_df``,
``news_items``, ``digest``) that do not JSON-serialise cleanly. Instead
we extract just the fields ``processing.report_diff.compute_report_diff``
actually reads:

  * ``sentiment.overall_score``
  * ``sentiment.overall_label``  (kept for richer UI rendering later)
  * ``market.risk_level``
  * ``alpha.signals``            (mapped down to ``[{name, score}, ...]``)
  * ``freight.routes``           (mapped down to ``[{route_id,
                                  rate_usd_per_feu}, ...]``)
  * ``report_date``
  * ``generated_at``

A ``ReportSnapshot`` is a flat dataclass — JSON-encode via ``to_dict``,
decode via ``from_dict``. Plus four lightweight view objects
(``_SentimentView``, ``_AlphaView``, ``_MarketView``, ``_FreightView``)
hang off the snapshot so it satisfies the duck-typed contract
``compute_report_diff`` expects (``snapshot.sentiment.overall_score``
etc.). That means you can pass two snapshots straight into
``compute_report_diff`` without touching the diff module.

Public surface
--------------
``ReportSnapshot``
    The dataclass.

``extract_snapshot(report) -> ReportSnapshot``
    Pull the relevant fields off a full ``InvestorReport``-shaped object.
    Defensive — every getattr has a safe fallback so a malformed report
    yields a zeroed snapshot, never an exception.

``save_snapshot(snapshot) -> bool``
    INSERT into ``investor_report_snapshots``. Returns True on success,
    False on any error. Never raises.

``load_latest_snapshots(n=2) -> list[ReportSnapshot]``
    Return the N newest snapshots, sorted descending by ``generated_at``.
    Defaults to 2 (current + previous, exactly what the briefing diff
    needs). Honors user_id scoping via ``state.user_scope.scope_filter_sql``
    so authenticated users see their own snapshots AND legacy
    ``user_id=''`` rows.

``prune_old_snapshots(keep_n=30) -> int``
    Keep only the newest ``keep_n`` rows. Deletes the rest. Returns the
    count deleted. Designed to be invoked from the worker's daily cron
    so the table doesn't grow unbounded.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger


# ─── Lightweight views ─────────────────────────────────────────────────────
#
# These exist so a ReportSnapshot can be passed directly to
# ``compute_report_diff`` without touching the diff module. The diff
# code looks up things like ``prev.sentiment.overall_score`` via
# getattr; the views give those attribute paths something to resolve
# to. We deliberately match the same names used on the real
# ``InvestorReport`` dataclasses so the contract is identical.

class _SignalView:
    """Per-signal duck for the diff helper.

    ``compute_report_diff`` resolves the signal name via
    (``title`` / ``name`` / ``signal_name``) and the score via
    (``score`` / ``strength`` / ``conviction``). We expose ``title``
    + ``score`` because those are the two paths used by every other
    consumer in this codebase.
    """

    __slots__ = ("title", "name", "score")

    def __init__(self, name: str, score: float) -> None:
        self.title = name          # match the report-engine convention
        self.name = name           # also satisfies the alt-path
        self.score = score


class _SentimentView:
    """Sentiment duck — exposes ``overall_score`` and ``overall_label``."""

    __slots__ = ("overall_score", "overall_label")

    def __init__(self, overall_score: float, overall_label: str) -> None:
        self.overall_score = overall_score
        self.overall_label = overall_label


class _AlphaView:
    """Alpha duck — exposes ``signals`` as a list of ``_SignalView``."""

    __slots__ = ("signals",)

    def __init__(self, signals: list[dict]) -> None:
        # Wrap the dict list as _SignalView objects so attribute lookups
        # in compute_report_diff land on real attributes (the diff helper
        # ALSO accepts dicts, but we expose objects so the duck-typing
        # contract is fully exercised).
        self.signals = [
            _SignalView(
                name=str(s.get("name", "") or ""),
                score=float(s.get("score", 0.0) or 0.0),
            )
            for s in (signals or [])
            if isinstance(s, dict)
        ]


class _MarketView:
    """Market duck — exposes ``risk_level``."""

    __slots__ = ("risk_level",)

    def __init__(self, risk_level: str) -> None:
        self.risk_level = risk_level


class _FreightView:
    """Freight duck — exposes ``routes`` as a list of dicts.

    ``compute_report_diff._route_name`` / ``_route_rate`` already accept
    dict input (it's the engine's native shape for FreightRateSummary
    rows), so we keep them as dicts here rather than wrapping each one.
    """

    __slots__ = ("routes",)

    def __init__(self, routes: list[dict]) -> None:
        self.routes = list(routes or [])


# ─── ReportSnapshot dataclass ──────────────────────────────────────────────


@dataclass
class ReportSnapshot:
    """Slim, durable snapshot of an InvestorReport for diff persistence.

    Designed for cheap JSON round-trip and for direct consumption by
    ``processing.report_diff.compute_report_diff`` via the view
    properties below. None of the fields hold pandas objects — the
    snapshot is intentionally smaller than the full InvestorReport.
    """

    snapshot_id: str
    generated_at: str               # ISO-8601 UTC
    report_date: str
    sentiment_overall_score: float
    sentiment_label: str
    risk_level: str
    signals: list[dict] = field(default_factory=list)   # [{name, score}, ...]
    routes: list[dict] = field(default_factory=list)    # [{route_id, rate_usd_per_feu}, ...]

    # ── View properties for compute_report_diff ──────────────────────

    @property
    def sentiment(self) -> _SentimentView:
        return _SentimentView(
            overall_score=self.sentiment_overall_score,
            overall_label=self.sentiment_label,
        )

    @property
    def alpha(self) -> _AlphaView:
        return _AlphaView(signals=self.signals)

    @property
    def market(self) -> _MarketView:
        return _MarketView(risk_level=self.risk_level)

    @property
    def freight(self) -> _FreightView:
        return _FreightView(routes=self.routes)

    # ── JSON helpers ─────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Plain-dict representation suitable for ``json.dumps``."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "ReportSnapshot":
        """Reconstruct from a ``to_dict()`` payload.

        Tolerates missing keys — each falls back to the dataclass
        default. The defensive coercion exists so a malformed row read
        from SQLite (or an older snapshot format) still produces a
        usable object instead of raising.
        """
        payload = payload or {}
        return cls(
            snapshot_id=str(payload.get("snapshot_id", "") or ""),
            generated_at=str(payload.get("generated_at", "") or ""),
            report_date=str(payload.get("report_date", "") or ""),
            sentiment_overall_score=_safe_float(
                payload.get("sentiment_overall_score", 0.0)
            ),
            sentiment_label=str(payload.get("sentiment_label", "") or ""),
            risk_level=str(payload.get("risk_level", "") or ""),
            signals=[
                {"name": str(s.get("name", "") or ""),
                 "score": _safe_float(s.get("score", 0.0))}
                for s in (payload.get("signals") or [])
                if isinstance(s, dict)
            ],
            routes=[
                {"route_id": str(r.get("route_id", "") or ""),
                 "rate_usd_per_feu": _safe_float(r.get("rate_usd_per_feu", 0.0))}
                for r in (payload.get("routes") or [])
                if isinstance(r, dict)
            ],
        )


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Coerce to float, falling back to ``default`` on any failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ─── extract_snapshot ──────────────────────────────────────────────────────


def extract_snapshot(report: Any) -> ReportSnapshot:
    """Pull the diff-relevant fields off a full ``InvestorReport``.

    Every attribute access has a safe fallback — a malformed (or
    partially-built) report yields a zeroed snapshot, never an
    exception. The function does not import ``InvestorReport`` itself;
    it duck-types against whatever object the caller passes.

    The signal score uses ``score`` if present, otherwise falls back
    to ``strength`` (the real AlphaSignal dataclass uses ``strength``).
    The signal name uses ``title`` if present, otherwise falls back to
    ``signal_name`` (again, the real AlphaSignal field). The route
    rate uses ``rate_usd_per_feu`` if present, otherwise ``rate``
    (the engine's native field name in FreightRateSummary).
    """
    try:
        generated_at = str(getattr(report, "generated_at", "") or "")
        if not generated_at:
            generated_at = datetime.now(timezone.utc).isoformat()
        report_date = str(getattr(report, "report_date", "") or "")

        sent = getattr(report, "sentiment", None)
        sentiment_overall_score = _safe_float(getattr(sent, "overall_score", 0.0))
        sentiment_label = str(getattr(sent, "overall_label", "") or "")

        market = getattr(report, "market", None)
        risk_level = str(getattr(market, "risk_level", "") or "")

        alpha = getattr(report, "alpha", None)
        raw_signals = list(getattr(alpha, "signals", None) or [])
        signals: list[dict] = []
        for sig in raw_signals:
            # Name extraction: prefer ``title`` (used by some report-
            # rendering paths), fall back to ``signal_name`` (the real
            # AlphaSignal field), then ``name`` (dict-form input). Same
            # ordering as compute_report_diff so the round-trip is exact.
            name = ""
            for attr in ("title", "signal_name", "name"):
                v = getattr(sig, attr, None)
                if v is None and isinstance(sig, dict):
                    v = sig.get(attr)
                if isinstance(v, str) and v:
                    name = v
                    break
            # Score extraction: prefer ``score``, fall back to
            # ``strength`` (the real AlphaSignal field).
            score = 0.0
            for attr in ("score", "strength"):
                v = getattr(sig, attr, None)
                if v is None and isinstance(sig, dict):
                    v = sig.get(attr)
                if v is not None:
                    score = _safe_float(v)
                    break
            if name:
                signals.append({"name": name, "score": score})

        freight = getattr(report, "freight", None)
        raw_routes = list(getattr(freight, "routes", None) or [])
        routes: list[dict] = []
        for route in raw_routes:
            # route_id is the engine's native key on FreightRateSummary;
            # also tolerate dataclass-style attribute access for safety.
            rid = ""
            if isinstance(route, dict):
                rid = str(route.get("route_id", "") or route.get("route_name", "") or "")
            else:
                for attr in ("route_id", "route_name", "name"):
                    v = getattr(route, attr, None)
                    if isinstance(v, str) and v:
                        rid = v
                        break
            # rate_usd_per_feu is the standard name; engine uses ``rate``.
            rate = 0.0
            if isinstance(route, dict):
                for k in ("rate_usd_per_feu", "rate", "current_rate"):
                    if k in route:
                        rate = _safe_float(route.get(k))
                        break
            else:
                for attr in ("rate_usd_per_feu", "rate", "current_rate"):
                    v = getattr(route, attr, None)
                    if v is not None:
                        rate = _safe_float(v)
                        break
            if rid:
                routes.append({"route_id": rid, "rate_usd_per_feu": rate})

        return ReportSnapshot(
            snapshot_id=str(uuid.uuid4()),
            generated_at=generated_at,
            report_date=report_date,
            sentiment_overall_score=sentiment_overall_score,
            sentiment_label=sentiment_label,
            risk_level=risk_level,
            signals=signals,
            routes=routes,
        )
    except Exception as exc:
        logger.warning(f"report_snapshot.extract_snapshot: failed: {exc}")
        return ReportSnapshot(
            snapshot_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            report_date="",
            sentiment_overall_score=0.0,
            sentiment_label="",
            risk_level="",
            signals=[],
            routes=[],
        )


# ─── Persistence ───────────────────────────────────────────────────────────


def save_snapshot(snapshot: ReportSnapshot, *, user_id: str = "") -> bool:
    """INSERT a snapshot row into ``investor_report_snapshots``.

    Returns True on success, False on any error (never raises). The
    caller is expected to surface persistence failures via the regular
    Streamlit error path — the briefing tab continues to render even
    when persistence is offline.

    ``user_id`` defaults to ``""`` so legacy single-user code keeps
    behaving the same way. Authenticated callers pass the active
    user_id (see ``state.user_scope.current_user_id``).
    """
    try:
        from state.db import get_connection

        if snapshot is None:
            return False
        payload_json = json.dumps(snapshot.to_dict(), default=str)
        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO investor_report_snapshots
                  (snapshot_id, generated_at, report_date, payload_json, user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.generated_at,
                    snapshot.report_date,
                    payload_json,
                    user_id or "",
                ),
            )
        return True
    except Exception as exc:
        logger.warning(f"report_snapshot.save_snapshot: failed: {exc}")
        return False


def load_latest_snapshots(
    n: int = 2,
    *,
    user_id: str = "",
) -> list[ReportSnapshot]:
    """Return the ``n`` newest snapshots, sorted desc by ``generated_at``.

    Default ``n=2`` matches the briefing diff use case: current +
    previous. When fewer than ``n`` rows exist the result is shorter —
    callers must handle that case.

    ``user_id`` honours the same dual-set semantics as the other
    domain modules: an authenticated user sees their own rows AND
    legacy ``user_id=''`` rows, an empty-string call sees every row.
    """
    if n <= 0:
        return []
    try:
        from state.db import get_connection
        from state.user_scope import scope_filter_sql

        scope_sql, scope_params = scope_filter_sql(user_id or "")
        # The scope fragment starts with "AND" so we need a leading
        # always-true predicate in the WHERE clause for the empty-scope
        # case (so concatenation is uniform). Use "WHERE 1=1" to keep
        # the resulting SQL readable.
        sql = (
            "SELECT snapshot_id, generated_at, report_date, payload_json "
            f"FROM investor_report_snapshots WHERE 1=1 {scope_sql} "
            "ORDER BY generated_at DESC, snapshot_id DESC LIMIT ?"
        )
        params = (*scope_params, int(n))
        conn = get_connection()
        rows = conn.execute(sql, params).fetchall()

        out: list[ReportSnapshot] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                # Skip a row with a malformed payload rather than
                # bringing the diff widget down. Log so it can be
                # investigated.
                logger.warning(
                    f"report_snapshot.load_latest_snapshots: malformed "
                    f"payload for snapshot_id={row['snapshot_id']}; skipping"
                )
                continue
            snap = ReportSnapshot.from_dict(payload)
            # Ensure the DB-recorded metadata wins over whatever was
            # embedded in the JSON payload (defensive — they should be
            # identical, but row-level columns are the source of truth).
            snap.snapshot_id = str(row["snapshot_id"])
            snap.generated_at = str(row["generated_at"])
            snap.report_date = str(row["report_date"] or "")
            out.append(snap)
        return out
    except Exception as exc:
        logger.warning(f"report_snapshot.load_latest_snapshots: failed: {exc}")
        return []


def prune_old_snapshots(keep_n: int = 30) -> int:
    """Keep only the newest ``keep_n`` snapshot rows; delete the rest.

    The briefing diff only ever reads the two most-recent rows, so
    we don't need a long retention window — 30 rows of history is
    plenty for ad-hoc post-mortems and never weighs more than ~30 KB
    on disk.

    Returns the count of rows deleted (``0`` on no-op or any error).
    Never raises — designed for the daily worker cron where a prune
    failure must not block the rest of the job.

    Note: this is a global prune (no user_id scoping). A multi-user
    deployment would want per-user retention; this code path is
    sufficient for the current single-user installation.
    """
    if keep_n < 0:
        keep_n = 0
    try:
        from state.db import get_connection

        conn = get_connection()
        # Total rows first — if we're under the limit, nothing to do.
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM investor_report_snapshots"
        ).fetchone()["n"]
        if total <= keep_n:
            return 0

        # Delete every row not in the keep_n newest. Use a subquery
        # rather than ORDER BY ... LIMIT (which SQLite does not allow
        # in DELETE) — the subquery returns the snapshot_ids we want
        # to keep, and we delete everything else.
        with conn:
            cur = conn.execute(
                """
                DELETE FROM investor_report_snapshots
                WHERE snapshot_id NOT IN (
                    SELECT snapshot_id FROM investor_report_snapshots
                    ORDER BY generated_at DESC, snapshot_id DESC
                    LIMIT ?
                )
                """,
                (int(keep_n),),
            )
            deleted = int(cur.rowcount) if cur.rowcount is not None else 0
        return deleted
    except Exception as exc:
        logger.warning(f"report_snapshot.prune_old_snapshots: failed: {exc}")
        return 0
