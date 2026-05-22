"""source_health.py — periodic liveness/freshness probes for external feeds.

This module is the single source of truth for "is FRED degrading right
now?". A small probe per external data feed (FRED, yfinance, World Bank,
…) is invoked on a schedule from ``worker.scheduler.run_health_ping_job``;
each probe returns a tuple ``(status, error_msg)`` where status is one
of ``'up'``, ``'degraded'``, ``'down'``. The result lands in the
``data_source_health`` SQLite table (schema v12) so the platform can
answer the operational question without scrolling through logs.

Decision flow
-------------
1. The worker invokes ``ping_all_sources()``.
2. For each entry in ``_PROBES``, ``ping_source(name)`` runs the probe
   wrapped in ``time.perf_counter`` + try/except.
3. A successful probe returning ``('up', '')`` → one row at status='up'.
4. A successful probe returning ``('degraded', 'partial')`` → one row
   at status='degraded' with the explanation captured in ``error_msg``.
5. A raising probe → one row at status='down' with ``error_msg=str(exc)``.
6. ``_record_ping`` writes the row. ALL public functions are
   guaranteed not to raise — a health-ping failure must never break
   the worker.

Why a probe-per-source map rather than a generic "call this URL"?
Each feed has its own entry point (``fetch_macro_series``,
``fetch_all_stocks``, ``fetch_port_throughput``, …) with its own
timeout / retry semantics already baked in. The probe just calls the
existing entry point with the smallest possible payload (one series,
one ticker, one indicator) and inspects the return value. That keeps
the probe cheap AND verifies the same code path the production reader
uses — a 200 OK from a contrived "ping me" endpoint would not catch a
parse-error regression.

Timeouts
--------
Each probe relies on the underlying feed's existing HTTP timeouts —
``requests`` is configured with a per-request timeout inside each
``data/*_feed.py`` module, and yfinance / fredapi both inherit
``urllib3`` defaults. ``ping_source`` therefore does NOT add a wall-
clock alarm of its own; the probe returns within the feed's own budget
(typically <10s) or raises a timeout exception which the try/except
captures as ``status='down'``.

Aggregation
-----------
``get_health_summary(window_hours)`` returns a dict with totals,
per-source breakdowns (count / up_count / degraded_count / down_count /
avg_duration_ms / last_status / last_started_at), AND a top-level
``current_outages`` list naming every source whose LATEST ping was
'down'. ``prune_old_pings(retention_days)`` is the retention pass.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from loguru import logger


# ─── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class HealthPing:
    """One row in the ``data_source_health`` table.

    Attributes
    ----------
    ping_id:
        UUID4 string. Primary key in SQLite.
    source:
        Short canonical feed identifier — matches the keys in
        ``_PROBES`` (e.g. ``'fred'``, ``'yfinance'``, ``'worldbank'``).
    started_at:
        ISO-8601 UTC timestamp captured immediately before the probe
        ran. Stored verbatim so window queries can be string-comparison.
    duration_ms:
        Wall-clock duration of the probe in milliseconds, measured via
        ``time.perf_counter``. Always >= 0.
    status:
        One of ``'up'``, ``'degraded'``, ``'down'``.
    error_msg:
        Free-form short string. Empty when status is ``'up'``;
        otherwise carries the exception text or the degraded reason.
    """

    ping_id: str
    source: str
    started_at: str
    duration_ms: int
    status: str
    error_msg: str = ""


# ─── Probe registry ────────────────────────────────────────────────────────
#
# Each probe is a tiny function that calls the feed's existing entry
# point with the smallest possible payload and returns a tuple:
#
#   ('up',        ''                  ) — feed responded with usable data
#   ('degraded',  'partial / empty'   ) — feed responded but result was
#                                         empty/missing keys
#   ('down',      str(exc)            ) — only set by ping_source's
#                                         try/except; probes themselves
#                                         should NOT catch transport
#                                         exceptions — let them bubble.
#
# Probes MUST NOT do their own retries. The point of the health ping is
# to surface a single attempt's outcome.

def _probe_fred() -> tuple[str, str]:
    """Call ``fetch_macro_series`` for a single short-lookback series."""
    from data.fred_feed import fetch_macro_series

    # Lookback intentionally tiny — we only need a heartbeat, not data.
    series = fetch_macro_series(lookback_days=7)
    if not series:
        return ("degraded", "fetch_macro_series returned empty dict")
    return ("up", "")


def _probe_yfinance() -> tuple[str, str]:
    """Call ``fetch_all_stocks`` for one ticker with a minimal lookback."""
    from data.stock_feed import fetch_all_stocks

    result = fetch_all_stocks(tickers=["ZIM"], lookback_days=5)
    if not result:
        return ("degraded", "fetch_all_stocks returned empty dict")
    return ("up", "")


def _probe_worldbank() -> tuple[str, str]:
    """Call ``fetch_port_throughput`` — a single batched request."""
    from data.worldbank_feed import fetch_port_throughput

    result = fetch_port_throughput()
    if not result:
        return ("degraded", "fetch_port_throughput returned empty dict")
    return ("up", "")


def _probe_currency() -> tuple[str, str]:
    """Call ``fetch_fx_rates`` — one round-trip per pair via yfinance."""
    from data.currency_feed import fetch_fx_rates

    rates = fetch_fx_rates()
    if not rates:
        return ("degraded", "fetch_fx_rates returned empty dict")
    return ("up", "")


def _probe_newsapi() -> tuple[str, str]:
    """Call ``fetch_newsapi_articles`` for a minimal query."""
    from data.newsapi_feed import fetch_newsapi_articles

    articles = fetch_newsapi_articles()
    # Empty list is acceptable — NewsAPI sometimes legitimately has
    # zero matches in the lookback window. We only flag degraded if
    # the call returned a non-list shape.
    if articles is None:
        return ("degraded", "fetch_newsapi_articles returned None")
    return ("up", "")


def _probe_oecd() -> tuple[str, str]:
    """Call ``fetch_oecd_indicators`` for the cached payload."""
    from data.oecd_feed import fetch_oecd_indicators

    result = fetch_oecd_indicators()
    if not result:
        return ("degraded", "fetch_oecd_indicators returned empty dict")
    return ("up", "")


def _probe_imf() -> tuple[str, str]:
    """Call ``fetch_imf_data`` for the cached payload."""
    from data.imf_feed import fetch_imf_data

    result = fetch_imf_data()
    if not result:
        return ("degraded", "fetch_imf_data returned empty dict")
    return ("up", "")


def _probe_canal_panama() -> tuple[str, str]:
    """Call ``fetch_panama_stats`` for the Panama Canal liveness check."""
    from data.canal_feed import fetch_panama_stats

    stats = fetch_panama_stats()
    if stats is None:
        return ("degraded", "fetch_panama_stats returned None")
    return ("up", "")


def _probe_canal_suez() -> tuple[str, str]:
    """Call ``fetch_suez_stats`` for the Suez Canal liveness check."""
    from data.canal_feed import fetch_suez_stats

    stats = fetch_suez_stats()
    if stats is None:
        return ("degraded", "fetch_suez_stats returned None")
    return ("up", "")


# ``_PROBES`` is the canonical registry. The key is the ``source`` name
# stored in the database; the value is the callable invoked by
# ``ping_source``. Tests monkeypatch this dict to inject deterministic
# probes — never make a real HTTP call from tests.
_PROBES: dict[str, Callable[[], tuple[str, str]]] = {
    "fred":         _probe_fred,
    "yfinance":     _probe_yfinance,
    "worldbank":    _probe_worldbank,
    "currency":     _probe_currency,
    "newsapi":      _probe_newsapi,
    "oecd":         _probe_oecd,
    "imf":          _probe_imf,
    "canal_panama": _probe_canal_panama,
    "canal_suez":   _probe_canal_suez,
}


# ─── Internal: SQLite write ────────────────────────────────────────────────

def _record_ping(ping: HealthPing) -> None:
    """Persist one health-ping row to the ``data_source_health`` table.

    Best-effort — any exception (DB disconnect, schema drift, bad input)
    is caught and logged at debug level so a telemetry write failure
    never breaks the calling worker. ``ping_source`` callers can rely
    on this function NEVER raising.

    Parameters
    ----------
    ping:
        Fully-populated ``HealthPing`` dataclass instance.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        conn.execute(
            """
            INSERT INTO data_source_health
              (ping_id, source, started_at, duration_ms, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(ping.ping_id or ""),
                str(ping.source or ""),
                str(ping.started_at or ""),
                int(ping.duration_ms or 0),
                str(ping.status or "down"),
                str(ping.error_msg or ""),
            ),
        )
    except Exception as exc:
        # Telemetry failure must never propagate. Debug-level is
        # intentional — if every ping logs at warn, a hot DB hiccup
        # floods the log.
        logger.debug(f"source_health: _record_ping failed: {exc}")


# ─── Public API ────────────────────────────────────────────────────────────

def ping_source(source: str) -> HealthPing:
    """Run the probe registered for ``source`` and record the outcome.

    Always returns a populated ``HealthPing``. The function never
    raises — any probe exception is captured into ``error_msg`` and
    surfaced via ``status='down'``. If ``source`` is not in ``_PROBES``,
    the returned ping has ``status='down'`` and ``error_msg`` explains
    the missing registration; the row is still recorded so the
    operator can see the misconfiguration.

    Parameters
    ----------
    source:
        Short canonical feed identifier — must match a key in
        ``_PROBES``.

    Returns
    -------
    HealthPing
        Populated with ping_id, source, started_at, duration_ms,
        status, error_msg.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    perf_start = time.perf_counter()

    probe = _PROBES.get(source)
    if probe is None:
        elapsed_ms = int(round((time.perf_counter() - perf_start) * 1000))
        ping = HealthPing(
            ping_id=str(uuid.uuid4()),
            source=str(source or ""),
            started_at=started_at,
            duration_ms=max(0, elapsed_ms),
            status="down",
            error_msg=f"no probe registered for source={source!r}",
        )
        _record_ping(ping)
        return ping

    try:
        status, error_msg = probe()
        # Normalize — accept anything callers might hand back and squash
        # to the documented vocabulary. An unknown status falls through
        # to 'degraded' with a hint rather than 'up' / 'down' silently.
        status = str(status or "").lower()
        if status not in ("up", "degraded", "down"):
            error_msg = f"probe returned unknown status={status!r}; treating as degraded"
            status = "degraded"
        error_msg = str(error_msg or "")
    except Exception as exc:
        status = "down"
        error_msg = str(exc)

    elapsed_ms = int(round((time.perf_counter() - perf_start) * 1000))
    if elapsed_ms < 0:
        elapsed_ms = 0

    ping = HealthPing(
        ping_id=str(uuid.uuid4()),
        source=str(source or ""),
        started_at=started_at,
        duration_ms=elapsed_ms,
        status=status,
        error_msg=error_msg,
    )
    _record_ping(ping)
    return ping


def ping_all_sources() -> list[HealthPing]:
    """Run every probe in ``_PROBES`` and return the per-source pings.

    Iterates ``_PROBES`` in registration order; a failure in one probe
    does NOT short-circuit the others — ``ping_source`` itself guarantees
    it never raises, so the loop is straight-through.

    Returns
    -------
    list[HealthPing]
        One ``HealthPing`` per source in ``_PROBES``. Order matches the
        insertion order of the registry.
    """
    results: list[HealthPing] = []
    for source in list(_PROBES.keys()):
        try:
            ping = ping_source(source)
        except Exception as exc:
            # Defence-in-depth — ping_source already swallows everything,
            # but the public contract says we never raise.
            logger.debug(f"source_health: ping_all_sources unexpected raise: {exc}")
            ping = HealthPing(
                ping_id=str(uuid.uuid4()),
                source=str(source or ""),
                started_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=0,
                status="down",
                error_msg=f"ping_source unexpectedly raised: {exc}",
            )
            _record_ping(ping)
        results.append(ping)
    return results


def _empty_summary(window_hours: int) -> dict[str, Any]:
    """Zeroed summary dict — used when the DB has no rows in window."""
    return {
        "window_hours":     int(window_hours),
        "total_pings":      0,
        "by_source":        {},
        "current_outages":  [],
    }


def get_health_summary(window_hours: int = 24) -> dict[str, Any]:
    """Aggregate health-ping telemetry over the last ``window_hours`` hours.

    Returns a dict shaped for direct rendering in a tab or CLI:

    .. code-block:: python

        {
            "window_hours":  24,
            "total_pings":   72,
            "by_source": {
                "fred": {
                    "count":              24,
                    "up_count":           22,
                    "degraded_count":     1,
                    "down_count":         1,
                    "avg_duration_ms":    312.5,
                    "last_status":        "up",
                    "last_started_at":    "2026-05-22T12:00:00+00:00",
                },
                "yfinance": {...},
                ...
            },
            "current_outages": ["worldbank"],   # latest ping was 'down'
        }

    Empty DB → zeroed dict with the same keys. Never raises; on any DB
    failure returns the empty shape and logs at debug.

    Parameters
    ----------
    window_hours:
        Look-back window in hours. Default 24 (rolling daily).

    Returns
    -------
    dict
        See shape above.
    """
    try:
        if window_hours is None:
            window_hours = 24
        try:
            window_hours = int(window_hours)
        except (TypeError, ValueError):
            window_hours = 24
        if window_hours <= 0:
            return _empty_summary(window_hours)

        from state.db import get_connection
        conn = get_connection()

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat()

        rows = conn.execute(
            """
            SELECT ping_id, source, started_at, duration_ms, status, error_msg
            FROM data_source_health
            WHERE started_at >= ?
            ORDER BY started_at ASC
            """,
            (cutoff,),
        ).fetchall()

        if not rows:
            return _empty_summary(window_hours)

        # Bucket rows by source so we can compute totals + most-recent
        # status per source in a single Python pass. The volume here is
        # bounded (every probe runs at most once per worker tick), so a
        # Python-side aggregation is simpler than per-source SQL and
        # remains fast.
        buckets: dict[str, dict[str, Any]] = {}
        for r in rows:
            src = str(r["source"])
            b = buckets.setdefault(src, {
                "durations":      [],
                "up_count":       0,
                "degraded_count": 0,
                "down_count":     0,
                # Track the row with the latest started_at — rows are
                # already ordered ASC so the last we see is the latest.
                "last_status":      "",
                "last_started_at":  "",
            })
            try:
                d_ms = int(r["duration_ms"])
            except (TypeError, ValueError):
                d_ms = 0
            b["durations"].append(d_ms)
            status = str(r["status"] or "down")
            if status == "up":
                b["up_count"] += 1
            elif status == "degraded":
                b["degraded_count"] += 1
            else:
                b["down_count"] += 1
            b["last_status"] = status
            b["last_started_at"] = str(r["started_at"] or "")

        by_source: dict[str, dict[str, Any]] = {}
        current_outages: list[str] = []
        for src, b in buckets.items():
            durations = b["durations"]
            avg_ms = (sum(durations) / len(durations)) if durations else 0.0
            by_source[src] = {
                "count":            len(durations),
                "up_count":         int(b["up_count"]),
                "degraded_count":   int(b["degraded_count"]),
                "down_count":       int(b["down_count"]),
                "avg_duration_ms":  round(float(avg_ms), 2),
                "last_status":      b["last_status"],
                "last_started_at":  b["last_started_at"],
            }
            if b["last_status"] == "down":
                current_outages.append(src)

        # current_outages sorted alphabetically for deterministic output
        # — easier to diff in dashboards and tests.
        current_outages.sort()

        return {
            "window_hours":     window_hours,
            "total_pings":      sum(s["count"] for s in by_source.values()),
            "by_source":        by_source,
            "current_outages":  current_outages,
        }
    except Exception as exc:
        logger.debug(f"source_health: get_health_summary failed: {exc}")
        return _empty_summary(
            window_hours if isinstance(window_hours, int) else 24
        )


def prune_old_pings(retention_days: int = 30) -> int:
    """Delete ``data_source_health`` rows older than ``retention_days``.

    A hard cutoff retention pass: any row whose ``started_at`` is older
    than ``now - retention_days`` is removed. Returns the number of rows
    deleted. Wrapped in a single transaction (``with conn:``) so a
    failure mid-way rolls back.

    Best-effort — any exception is caught and logged at debug level so
    a retention failure never breaks the calling worker. Returns ``0``
    on any error.

    Parameters
    ----------
    retention_days:
        Keep rows newer than this many days. Default 30 — health pings
        accumulate at ~one row per source per worker tick, so the
        default mirrors ``perf_telemetry``'s 30-day window. A value of
        ``0`` means "delete everything" (cutoff is now). A negative
        value is treated as a no-op and returns ``0`` — protects
        against an accidental nuke from a CLI typo.

    Returns
    -------
    int
        Number of rows deleted. ``0`` when nothing matched, when the
        input was a negative no-op, or when an exception was caught.
    """
    try:
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            logger.debug(
                f"source_health: prune_old_pings got non-int "
                f"retention_days={retention_days!r}, treating as no-op"
            )
            return 0

        if retention_days < 0:
            logger.debug(
                f"source_health: prune_old_pings retention_days="
                f"{retention_days} < 0, treating as no-op"
            )
            return 0

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()

        from state.db import get_connection
        conn = get_connection()

        with conn:
            cur = conn.execute(
                "DELETE FROM data_source_health WHERE started_at < ?",
                (cutoff,),
            )
            deleted = int(cur.rowcount or 0)
        logger.debug(
            f"source_health: prune_old_pings deleted={deleted} rows "
            f"(retention_days={retention_days}, cutoff={cutoff})"
        )
        return deleted
    except Exception as exc:
        logger.debug(f"source_health: prune_old_pings failed: {exc}")
        return 0
