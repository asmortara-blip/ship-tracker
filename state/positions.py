"""Durable, per-user position ledger (schema v29; rec R096).

The portfolio book used to live only in ``st.session_state`` seeded from a
hardcoded default list — it evaporated on refresh and was identical for every
user. This module persists positions per ``user_id`` as a point-in-time
ledger: a write CLOSES the user's currently-open rows (stamps ``closed_at``)
and inserts the new set at ``version + 1`` rather than overwriting, so the book
is durable, per-user, and reconstructable as-of any past write.

This is the OMS-grade foundation that P&L, the daily blotter, book-level VaR,
and the conviction-to-weight sizer all build on. All functions fail soft and
are safe to call with an empty ``user_id`` (the dual-set ``''`` scope used
across Ship's multi-user tables).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from auth.ids import opaque_id
from state.db import get_connection

# The columns that make up a position as the UI/analytics layer sees it.
POSITION_FIELDS = ("ticker", "sector", "shares", "avg_cost", "beta")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_positions(user_id: str) -> list[dict]:
    """Return the user's currently-open positions (``closed_at IS NULL``).

    Each dict carries exactly ``POSITION_FIELDS`` so it is a drop-in for the
    legacy session-state list shape.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT ticker, sector, shares, avg_cost, beta FROM positions "
        "WHERE user_id = ? AND closed_at IS NULL ORDER BY ticker",
        (user_id or "",),
    ).fetchall()
    return [
        {
            "ticker": r["ticker"],
            "sector": r["sector"],
            "shares": r["shares"],
            "avg_cost": r["avg_cost"],
            "beta": r["beta"],
        }
        for r in rows
    ]


def current_version(user_id: str) -> int:
    """Highest ledger version written for the user (0 if none)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(version) AS v FROM positions WHERE user_id = ?",
        (user_id or "",),
    ).fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def replace_positions(user_id: str, positions: list[dict]) -> int:
    """Atomically replace the user's open book with ``positions``.

    Closes every currently-open row (stamps ``closed_at``) and inserts the new
    set as open rows at ``version + 1``. Closed rows are retained, so the
    ledger keeps full history. Returns the new version number.
    """
    uid = user_id or ""
    now = _now()
    new_version = current_version(uid) + 1
    conn = get_connection()
    with conn:  # single transaction: close-then-insert is all-or-nothing
        conn.execute(
            "UPDATE positions SET closed_at = ?, updated_at = ? "
            "WHERE user_id = ? AND closed_at IS NULL",
            (now, now, uid),
        )
        for p in positions or []:
            beta = p.get("beta")
            conn.execute(
                "INSERT INTO positions (position_id, user_id, ticker, sector, "
                "shares, avg_cost, beta, opened_at, closed_at, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    opaque_id(),
                    uid,
                    str(p.get("ticker", "")),
                    p.get("sector"),
                    _to_float(p.get("shares")),
                    _to_float(p.get("avg_cost")),
                    (None if beta is None else _to_float(beta)),
                    now,
                    new_version,
                    now,
                ),
            )
    return new_version


def position_history(user_id: str) -> list[dict]:
    """All ledger rows (open + closed) for the user, oldest version first.

    For audit / point-in-time reconstruction: closed rows carry the
    ``closed_at`` of the write that superseded them.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT ticker, sector, shares, avg_cost, beta, opened_at, closed_at, "
        "version FROM positions WHERE user_id = ? ORDER BY version, ticker",
        (user_id or "",),
    ).fetchall()
    return [dict(r) for r in rows]
