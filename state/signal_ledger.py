"""Point-in-time signal ledger (schema v32; rec R004).

Freezes each ``EquityIdea`` AS ISSUED and marks it forward on real closes — a
look-ahead-free, never-refit equity-idea track record. This is the keystone the
platform admitted it lacked: ``processing.signal_validation`` applies TODAY's
signal to PAST windows (look-ahead); this records what was actually called,
when, and at what price, then scores it ONLY against closes that came after.

Honesty: a position is frozen at its issue close + direction + conviction; the
mark uses real current closes. Nothing is refit, and a row with no real price
is skipped (never marked at a fabricated level).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from auth.ids import opaque_id
from state.db import get_connection, immediate_transaction

_LONG = {"bullish", "long", "buy"}
_SHORT = {"bearish", "short", "sell"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _dir_sign(direction: str) -> int:
    d = (direction or "").strip().lower()
    if d in _LONG:
        return 1
    if d in _SHORT:
        return -1
    return 0


def _latest_close(stock_data, ticker: str) -> Optional[float]:
    try:
        from processing.book_pnl import _latest_close as _lc
        return _lc(stock_data, ticker)
    except Exception:
        return None


def freeze_ideas(ideas, *, issue_date: Optional[str] = None, stock_data=None) -> int:
    """Freeze each idea as a ledger row, idempotent per (ticker, date, direction).

    ``issue_close`` comes from the idea's own ``price`` (its close at
    generation), falling back to the latest close in ``stock_data``. Neutral /
    blank-direction ideas are skipped (nothing to score forward). Returns the
    number of rows actually inserted (re-running the same day inserts 0).
    """
    issue_date = issue_date or _today()
    now = _now()
    inserted = 0
    conn = get_connection()
    with immediate_transaction(conn):
        for idea in ideas or []:
            ticker = str(getattr(idea, "ticker", "") or "")
            direction = str(getattr(idea, "direction", "") or "")
            if not ticker or _dir_sign(direction) == 0:
                continue
            close = float(getattr(idea, "price", 0.0) or 0.0)
            if close <= 0:
                alt = _latest_close(stock_data, ticker)
                close = float(alt) if alt else 0.0
            if close <= 0:
                # No real issue price -> the idea can never be marked forward.
                # Skip rather than record an un-scoreable row, so the ledger
                # holds only real, scoreable signals (keeps the cascade's
                # illustrative-on-sparse-data ideas out of the track record).
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO signal_ledger "
                "(ledger_id, ticker, direction, conviction_score, "
                " conviction_label, weight_set, issue_date, issue_close, frozen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    opaque_id(), ticker, direction,
                    float(getattr(idea, "conviction_score", 0.0) or 0.0),
                    str(getattr(idea, "conviction_label", "") or ""),
                    str(getattr(idea, "conviction_weight_set", "") or ""),
                    issue_date,
                    close,
                    now,
                ),
            )
            inserted += (getattr(cur, "rowcount", 0) or 0)
    return inserted


def load_ledger(*, ticker: Optional[str] = None, since: Optional[str] = None,
                limit: int = 2000) -> list[dict]:
    """Frozen ledger rows, newest issue first."""
    conn = get_connection()
    clauses, params = ["1=1"], []
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker)
    if since:
        clauses.append("issue_date >= ?")
        params.append(since)
    params.append(int(limit))
    rows = conn.execute(
        "SELECT ledger_id, ticker, direction, conviction_score, conviction_label, "
        "weight_set, issue_date, issue_close, frozen_at FROM signal_ledger "
        "WHERE " + " AND ".join(clauses)
        + " ORDER BY issue_date DESC, ticker LIMIT ?",
        tuple(params),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_ledger(stock_data) -> list[dict]:
    """Mark each frozen idea forward on real closes -> per-idea signed P&L.

    Un-refittable: ``issue_close`` + ``direction`` + conviction are FROZEN; only
    the current close updates. ``signed_return = forward return * direction
    sign``. Rows with no issue_close or no current real price are skipped.
    """
    out: list[dict] = []
    for r in load_ledger(limit=10_000):
        issue_close = r.get("issue_close")
        if not issue_close or issue_close <= 0:
            continue
        cur = _latest_close(stock_data, r["ticker"])
        if cur is None or cur <= 0:
            continue
        ret = (cur - issue_close) / issue_close
        signed = ret * _dir_sign(r["direction"])
        out.append({
            **r,
            "current_close": float(cur),
            "return_pct": ret * 100.0,
            "signed_return_pct": signed * 100.0,
            "win": signed > 0,
        })
    return out


def track_record_summary(stock_data) -> dict:
    """Aggregate the marked ledger into an honest forward track record:
    overall hit-rate + mean signed return, and the same split by conviction."""
    marked = mark_ledger(stock_data)
    n = len(marked)
    if n == 0:
        return {"n": 0, "hit_rate": 0.0, "mean_signed_return_pct": 0.0, "by_label": {}}
    wins = sum(1 for m in marked if m["win"])
    mean = sum(m["signed_return_pct"] for m in marked) / n
    by_label: dict[str, dict] = {}
    for m in marked:
        lab = m.get("conviction_label") or "?"
        b = by_label.setdefault(lab, {"n": 0, "wins": 0, "_sum": 0.0})
        b["n"] += 1
        b["wins"] += 1 if m["win"] else 0
        b["_sum"] += m["signed_return_pct"]
    for b in by_label.values():
        b["hit_rate"] = b["wins"] / b["n"] if b["n"] else 0.0
        b["mean_signed_return_pct"] = b.pop("_sum") / b["n"] if b["n"] else 0.0
    return {"n": n, "hit_rate": wins / n,
            "mean_signed_return_pct": mean, "by_label": by_label}


def oos_scorecard(stock_data, *, min_n: int = 5, threshold: float = 0.95) -> dict:
    """Out-of-sample significance of the FROZEN track record (R004 x R101).

    Treats the N realized signed returns (one per marked, never-refit idea) as a
    cross-section, computes a cross-sectional Sharpe-like ratio (mean / dispersion)
    and the **probabilistic Sharpe** P(true ratio > 0) given N and the higher
    moments. The honest "is the edge real, not luck" read the platform lacked —
    on real, look-ahead-free returns. ``sufficient=False`` below ``min_n``.
    """
    marks = mark_ledger(stock_data)
    n = len(marks)
    rets = [m["signed_return_pct"] / 100.0 for m in marks]
    hit = (sum(1 for r in rets if r > 0) / n) if n else 0.0
    mean_pct = (sum(m["signed_return_pct"] for m in marks) / n) if n else 0.0
    if n < min_n:
        return {
            "n": n, "sufficient": False, "hit_rate": hit,
            "mean_signed_return_pct": mean_pct, "psr": None, "is_significant": False,
            "verdict": f"Only {n} marked idea(s) — need >= {min_n} to assess significance.",
        }
    import math as _math

    import numpy as _np
    import pandas as _pd
    from processing.stat_significance import probabilistic_sharpe_ratio

    arr = _np.asarray(rets, dtype=float)
    sd = float(arr.std(ddof=1))
    sr = float(arr.mean() / sd) if sd > 0 else 0.0
    sk = float(_pd.Series(rets).skew())
    ku = float(_pd.Series(rets).kurt())
    if not (_math.isfinite(sk) and _math.isfinite(ku)):
        sk, ku = 0.0, 3.0
    else:
        ku += 3.0  # pandas .kurt() is excess; PSR wants full kurtosis
    psr = probabilistic_sharpe_ratio(sr, n, skew=sk, kurt=ku, sr_benchmark=0.0)
    significant = psr >= threshold
    return {
        "n": n, "sufficient": True, "hit_rate": hit,
        "mean_signed_return_pct": mean_pct,
        "cross_sectional_sharpe": sr, "psr": psr, "is_significant": significant,
        "verdict": (
            f"Cross-sectional Sharpe {sr:+.2f} over {n} realized ideas; "
            f"PSR {psr:.0%} — "
            + ("statistically significant." if significant
               else "not yet significant (treat as noise).")
        ),
    }
