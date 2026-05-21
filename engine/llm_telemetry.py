"""llm_telemetry.py — record + summarize every Anthropic API call.

This module is the single source of truth for "how much have I spent on
Claude this week?". Every successful LLM call in the platform — daily
narration, per-tab editorial commentary, and any future LLM-backed
feature — pipes one row through ``record_call`` into the ``llm_calls``
SQLite table (schema v3). Tabs and ops dashboards then read the table
via ``get_usage_summary`` / ``get_recent_calls``.

Decision flow
-------------
1. Caller (engine module) finishes a successful Anthropic call and has
   ``model``, ``tokens_in``, ``tokens_out`` in hand from the response
   usage block.
2. Caller invokes ``record_call(source, model, tokens_in, tokens_out,
   ...)`` wrapped in try/except — telemetry MUST NEVER raise into the
   caller's hot path. ``record_call`` itself swallows every error and
   logs at debug level.
3. ``record_call`` stamps the row with a fresh UUID call_id, an ISO
   UTC ``created_at``, and a computed ``est_cost_usd`` derived from
   the per-model rate table below.
4. Aggregation helpers query the table on demand — no caching, no
   background jobs.

Cost rates
----------
``_COST_PER_MTOK`` carries one entry per supported model — USD per
million tokens for input and output, sourced from the Anthropic public
pricing page (https://www.anthropic.com/pricing). Pricing is a moving
target: this table records the rates AT THE TIME OF WRITING and is
intended to be edited when Anthropic publishes new rates. Unknown
models fall back to Haiku 4.5 rates (the cheapest tier) with a debug
log line so the operator notices but never gets a surprise crash.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


# ─── Cost table — USD per million tokens, public list pricing ──────────────
#
# Source: https://www.anthropic.com/pricing (verified 2026-05).
# Rates change occasionally — bump this dict when Anthropic publishes new
# prices. Keys are exact model IDs as returned by the SDK; if the SDK
# starts returning a new ID for the same family, add an alias rather
# than rewriting the existing entry (preserves historical cost rows).
_COST_PER_MTOK: dict[str, dict[str, float]] = {
    # Haiku 4.5 — cheapest production tier; default for commentary.
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0},
    # Sonnet 4.6 — mid-tier, ~3x Haiku input / 3x output.
    "claude-sonnet-4-6":         {"in": 3.0, "out": 15.0},
    # Opus 4.7 — top tier, ~15x Haiku.
    "claude-opus-4-7":           {"in": 15.0, "out": 75.0},
}

# Fallback when an unknown model ID lands in record_call. We pick the
# cheapest tier so an unrecognised model can never inflate the spend
# report — under-reporting on an unknown model is preferable to a panic
# from an over-estimated bill driven by a typo.
_DEFAULT_FALLBACK_MODEL: str = "claude-haiku-4-5-20251001"


# ─── Public API ────────────────────────────────────────────────────────────

def _estimate_cost(
    model: str, tokens_in: int, tokens_out: int,
) -> float:
    """Compute estimated cost in USD for one Anthropic call.

    Looks up the per-MTok rate for ``model`` in ``_COST_PER_MTOK``;
    falls back to Haiku rates (the cheapest tier) for unknown models
    with a debug log so operators notice but downstream code never
    raises. Rounded to 6 decimal places — sub-penny precision keeps
    sums readable while preserving signal for cheap (Haiku) calls.
    """
    rates = _COST_PER_MTOK.get(model)
    if rates is None:
        logger.debug(
            f"llm_telemetry: unknown model '{model}', falling back to "
            f"{_DEFAULT_FALLBACK_MODEL} rates for cost estimation"
        )
        rates = _COST_PER_MTOK[_DEFAULT_FALLBACK_MODEL]
    cost = (
        (tokens_in / 1_000_000.0) * rates["in"]
        + (tokens_out / 1_000_000.0) * rates["out"]
    )
    return round(cost, 6)


def record_call(
    source: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int = 0,
    tab_name: str = "",
) -> None:
    """Persist one LLM call to the ``llm_calls`` table.

    Best-effort — any exception (DB disconnect, schema drift, bad input)
    is caught and logged at debug level so a telemetry write failure
    never breaks the calling engine module. Callers should ALSO wrap
    this in try/except for belt-and-braces; ``record_call`` itself
    guarantees not to raise.

    Parameters
    ----------
    source:
        Short identifier for the caller — ``"commentary"`` /
        ``"narration"`` today. Stored verbatim; aggregations group by
        this column so caller-defined values flow straight into the
        summary breakdown.
    model:
        Exact model ID returned by the SDK (e.g.
        ``"claude-haiku-4-5-20251001"``). Drives the per-call cost
        estimate via the ``_COST_PER_MTOK`` table.
    tokens_in, tokens_out:
        Token counts from the response ``usage`` block. Non-negative
        integers; the column type is INTEGER NOT NULL DEFAULT 0.
    cached_tokens:
        Optional — token count served from Anthropic's prompt cache.
        Stored separately from ``tokens_in`` so future cost-model
        upgrades (cached tokens at a discounted rate) don't require a
        schema change. Default 0.
    tab_name:
        Optional — for ``source="commentary"`` rows, the tab the
        commentary was for. Empty string when not applicable.
    """
    try:
        from state.db import get_connection

        call_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        est_cost = _estimate_cost(model, int(tokens_in), int(tokens_out))

        conn = get_connection()
        conn.execute(
            """
            INSERT INTO llm_calls
              (call_id, created_at, source, tab_name, model,
               tokens_in, tokens_out, cached_tokens, est_cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                created_at,
                str(source or ""),
                str(tab_name or ""),
                str(model or ""),
                int(tokens_in or 0),
                int(tokens_out or 0),
                int(cached_tokens or 0),
                float(est_cost),
            ),
        )
    except Exception as exc:
        # Telemetry failure must never propagate. Debug-level is
        # intentional — if every call logs at warn, a transient DB
        # hiccup floods the log on a hot tab.
        logger.debug(f"llm_telemetry: record_call failed: {exc}")


def _empty_summary(window_days: int) -> dict[str, Any]:
    """Return a zeroed summary dict — used when the DB has no rows in window."""
    return {
        "window_days": int(window_days),
        "total_calls": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "total_cost_usd": 0.0,
        "by_source": {},
        "by_model": {},
        "by_day": [],
    }


def get_usage_summary(window_days: int = 7) -> dict[str, Any]:
    """Aggregate LLM-call telemetry over the last ``window_days`` days.

    Returns a dict shaped for direct rendering in a tab or CLI:

    .. code-block:: python

        {
            "window_days": 7,
            "total_calls": 42,
            "total_tokens_in": 12345,
            "total_tokens_out": 678,
            "total_cost_usd": 0.123456,
            "by_source": {
                "commentary": {"calls": 30, "tokens_in": ..., "tokens_out": ..., "cost": ...},
                "narration":  {"calls": 12, ...},
            },
            "by_model": {
                "claude-haiku-4-5-20251001": {"calls": ..., "cost": ...},
                ...
            },
            "by_day": [
                {"date": "YYYY-MM-DD", "calls": int, "cost": float},
                ...
            ],   # ascending by date; only days WITH calls appear
        }

    Empty DB → zeroed dict with the same keys. Never raises; on any
    DB failure returns the empty shape and logs at debug.

    Parameters
    ----------
    window_days:
        Look-back window in days. Default 7 (rolling weekly). Pass 1
        for a daily snapshot, 30 for a monthly view.
    """
    try:
        # Treat None (or anything that doesn't cast cleanly) as the default
        # window. A literal 0 / negative MUST short-circuit to the empty
        # shape — without this check the cutoff would be "today or later",
        # which silently masks any older rows.
        if window_days is None:
            window_days = 7
        try:
            window_days = int(window_days)
        except (TypeError, ValueError):
            window_days = 7
        if window_days <= 0:
            return _empty_summary(window_days)

        from state.db import get_connection
        conn = get_connection()

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()

        # Totals — one round-trip, narrow projection.
        totals_row = conn.execute(
            """
            SELECT
                COUNT(*)              AS n,
                COALESCE(SUM(tokens_in),  0) AS sum_in,
                COALESCE(SUM(tokens_out), 0) AS sum_out,
                COALESCE(SUM(est_cost_usd), 0.0) AS sum_cost
            FROM llm_calls
            WHERE created_at >= ?
            """,
            (cutoff,),
        ).fetchone()

        if totals_row is None or int(totals_row["n"]) == 0:
            return _empty_summary(window_days)

        # By source.
        by_source_rows = conn.execute(
            """
            SELECT
                source,
                COUNT(*)              AS n,
                COALESCE(SUM(tokens_in),  0) AS sum_in,
                COALESCE(SUM(tokens_out), 0) AS sum_out,
                COALESCE(SUM(est_cost_usd), 0.0) AS sum_cost
            FROM llm_calls
            WHERE created_at >= ?
            GROUP BY source
            ORDER BY source
            """,
            (cutoff,),
        ).fetchall()
        by_source: dict[str, dict[str, Any]] = {}
        for r in by_source_rows:
            by_source[str(r["source"])] = {
                "calls":      int(r["n"]),
                "tokens_in":  int(r["sum_in"]),
                "tokens_out": int(r["sum_out"]),
                "cost":       round(float(r["sum_cost"]), 6),
            }

        # By model.
        by_model_rows = conn.execute(
            """
            SELECT
                model,
                COUNT(*)              AS n,
                COALESCE(SUM(tokens_in),  0) AS sum_in,
                COALESCE(SUM(tokens_out), 0) AS sum_out,
                COALESCE(SUM(est_cost_usd), 0.0) AS sum_cost
            FROM llm_calls
            WHERE created_at >= ?
            GROUP BY model
            ORDER BY model
            """,
            (cutoff,),
        ).fetchall()
        by_model: dict[str, dict[str, Any]] = {}
        for r in by_model_rows:
            by_model[str(r["model"])] = {
                "calls":      int(r["n"]),
                "tokens_in":  int(r["sum_in"]),
                "tokens_out": int(r["sum_out"]),
                "cost":       round(float(r["sum_cost"]), 6),
            }

        # By day — SQLite's substr(created_at, 1, 10) gives the ISO YYYY-MM-DD
        # prefix from every well-formed ISO 8601 timestamp this module writes.
        by_day_rows = conn.execute(
            """
            SELECT
                substr(created_at, 1, 10) AS day,
                COUNT(*)                  AS n,
                COALESCE(SUM(est_cost_usd), 0.0) AS sum_cost
            FROM llm_calls
            WHERE created_at >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (cutoff,),
        ).fetchall()
        by_day = [
            {
                "date":  str(r["day"]),
                "calls": int(r["n"]),
                "cost":  round(float(r["sum_cost"]), 6),
            }
            for r in by_day_rows
        ]

        return {
            "window_days":      window_days,
            "total_calls":      int(totals_row["n"]),
            "total_tokens_in":  int(totals_row["sum_in"]),
            "total_tokens_out": int(totals_row["sum_out"]),
            "total_cost_usd":   round(float(totals_row["sum_cost"]), 6),
            "by_source":        by_source,
            "by_model":         by_model,
            "by_day":           by_day,
        }
    except Exception as exc:
        logger.debug(f"llm_telemetry: get_usage_summary failed: {exc}")
        return _empty_summary(window_days if isinstance(window_days, int) else 7)


def get_recent_calls(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` LLM calls, newest first.

    Each row is a plain dict matching the column names of the
    ``llm_calls`` table, suitable for rendering in a debug UI grid or
    serializing as JSON. Empty DB → empty list. Never raises; on any
    DB failure returns ``[]`` and logs at debug.

    Parameters
    ----------
    limit:
        Maximum number of rows to return. Default 50; clipped to a
        minimum of 1.
    """
    try:
        limit = max(1, int(limit) if limit else 50)
        from state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT call_id, created_at, source, tab_name, model,
                   tokens_in, tokens_out, cached_tokens, est_cost_usd
            FROM llm_calls
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "call_id":       str(r["call_id"]),
                "created_at":    str(r["created_at"]),
                "source":        str(r["source"]),
                "tab_name":      str(r["tab_name"]),
                "model":         str(r["model"]),
                "tokens_in":     int(r["tokens_in"]),
                "tokens_out":    int(r["tokens_out"]),
                "cached_tokens": int(r["cached_tokens"]),
                "est_cost_usd":  round(float(r["est_cost_usd"]), 6),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug(f"llm_telemetry: get_recent_calls failed: {exc}")
        return []


# ─── Retention ─────────────────────────────────────────────────────────────

def prune_old_calls(retention_days: int = 90) -> int:
    """Delete ``llm_calls`` rows older than ``retention_days`` days.

    A hard cutoff retention pass: any row whose ``created_at`` is older
    than ``now - retention_days`` is removed. Returns the number of rows
    deleted. Wrapped in a single transaction (``with conn:``) so a
    failure mid-way rolls back rather than leaving a partial prune.

    Best-effort — any exception (DB disconnect, schema drift, bad input)
    is caught and logged at debug level so a retention failure never
    breaks the calling worker. Returns ``0`` on any error.

    Parameters
    ----------
    retention_days:
        Keep rows newer than this many days. Default 90. A value of
        ``0`` means "delete everything" (cutoff is now). A negative
        value is treated as a no-op and returns ``0`` — protects against
        an accidental nuke from a CLI typo like ``--retention-days -1``.

    Returns
    -------
    int
        Number of rows deleted. ``0`` when nothing matched, when the
        input was a negative no-op, or when an exception was caught.
    """
    try:
        # Coerce to int; bad input → no-op.
        try:
            retention_days = int(retention_days)
        except (TypeError, ValueError):
            logger.debug(
                f"llm_telemetry: prune_old_calls got non-int "
                f"retention_days={retention_days!r}, treating as no-op"
            )
            return 0

        # Negative window is explicitly a no-op — guards against a CLI
        # typo deleting the whole table.
        if retention_days < 0:
            logger.debug(
                f"llm_telemetry: prune_old_calls retention_days="
                f"{retention_days} < 0, treating as no-op"
            )
            return 0

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()

        from state.db import get_connection
        conn = get_connection()

        # Single transaction — `with conn` commits on clean exit, rolls
        # back on exception. The outer try/except below still catches
        # anything that escapes.
        with conn:
            cur = conn.execute(
                "DELETE FROM llm_calls WHERE created_at < ?",
                (cutoff,),
            )
            deleted = int(cur.rowcount or 0)
        logger.debug(
            f"llm_telemetry: prune_old_calls deleted={deleted} rows "
            f"(retention_days={retention_days}, cutoff={cutoff})"
        )
        return deleted
    except Exception as exc:
        logger.debug(f"llm_telemetry: prune_old_calls failed: {exc}")
        return 0


# ─── CLI entry point ───────────────────────────────────────────────────────

def _count_old_calls(retention_days: int) -> int:
    """Count rows that ``prune_old_calls`` WOULD delete. Used by --dry-run.

    Never raises; returns 0 on any error or on a negative
    ``retention_days`` (matching prune_old_calls' no-op semantics).
    """
    try:
        retention_days = int(retention_days)
        if retention_days < 0:
            return 0
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM llm_calls WHERE created_at < ?",
            (cutoff,),
        ).fetchone()
        return int(row["n"]) if row is not None else 0
    except Exception as exc:
        logger.debug(f"llm_telemetry: _count_old_calls failed: {exc}")
        return 0


def _main(argv: Optional[list] = None) -> int:
    """CLI entry point — prune old llm_calls rows.

    Returns the intended exit code (0 success / 1 error) so callers in
    tests can assert without triggering ``sys.exit``. The ``__main__``
    block below this function is what actually calls ``sys.exit``.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="engine.llm_telemetry",
        description=(
            "Prune llm_calls rows older than the retention window. "
            "Intended to be invoked from cron or the worker."
        ),
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="Keep rows newer than this many days (default 90).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what WOULD be deleted without actually deleting.",
    )

    try:
        args = parser.parse_args(argv)
        if args.dry_run:
            would_delete = _count_old_calls(args.retention_days)
            print(
                f"[dry-run] would delete {would_delete} llm_calls rows "
                f"(retention_days={args.retention_days})"
            )
        else:
            deleted = prune_old_calls(args.retention_days)
            print(
                f"pruned {deleted} llm_calls rows "
                f"(retention_days={args.retention_days})"
            )
        return 0
    except SystemExit:
        # argparse calls sys.exit on --help / bad args. Surface as 1
        # only if it wasn't a clean help-style exit (code 0).
        raise
    except Exception as exc:
        print(f"prune_old_calls CLI failed: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(_main())
