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
from processing.cost_model import DISCLAIMER as COST_DISCLAIMER
from processing.cost_model import net_of_cost_pct
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


def _latest_close_after(stock_data, ticker: str, after_date) -> Optional[float]:
    """Forward close strictly after ``after_date`` — the causal mark price."""
    try:
        from processing.book_pnl import _latest_close_after as _lca
        return _lca(stock_data, ticker, after_date)
    except Exception:
        return None


def _latest_date_after(stock_data, ticker: str, after_date):
    """Date of the forward close strictly after ``after_date`` (the mark date).

    Mirrors :func:`_latest_close_after` but returns the index timestamp, so the
    ledger can compute the holding period for the short-borrow cost.
    """
    try:
        import pandas as pd

        from processing.book_pnl import _close_series
        s = _close_series(stock_data, ticker)
        if s is None or s.empty or not isinstance(s.index, pd.DatetimeIndex):
            return None
        cutoff = pd.to_datetime(after_date)
        fwd = s[s.index > cutoff]
        return fwd.index[-1] if not fwd.empty else None
    except Exception:
        return None


def _signed_forward_return(stock_data, ticker: str, issue_date, issue_close, cur) -> Optional[float]:
    """Split-safe FORWARD return from FROZEN ``issue_close`` to ``cur``, or None.

    The entry leg is the FROZEN ``issue_close`` (the idea's real issued price);
    the exit leg is the raw forward close ``cur`` (already validated as strictly
    post-issue by the caller). The naive ``(cur - issue_close)/issue_close`` is
    corrupted by any split/large dividend BETWEEN issue and the mark — a 2:1
    split alone reads as ~-50%. R127 captures that corporate action in the
    look-ahead-free forward ``adj_factor``: the factor accrued over the holding
    window is ``adj_now / adj_ref`` where ``adj_ref`` is the factor on which
    ``issue_close`` was observed (the factor as-of ``issue_date``). Scaling the
    exit close by that ratio puts it back on the entry's basis, so the split nets
    out. ``adj_factor`` defaults to 1.0 (and the ratio to 1.0) when absent or
    when no action occurred, so this reduces EXACTLY to the raw forward return
    for fixtures / legacy / no-split frames. Returns None to signal "no usable
    adjusted basis" so the caller falls back to the raw return.
    """
    try:
        import pandas as pd

        from processing.book_pnl import _close_series
        # Raw + adjusted closes from the SAME frame so the per-date ratio
        # adj = adjusted/raw recovers the look-ahead-free adj_factor at each date.
        raw = _close_series(stock_data, ticker, adjusted=False)
        adj = _close_series(stock_data, ticker, adjusted=True)
        if (raw is None or adj is None or raw.empty
                or not isinstance(raw.index, pd.DatetimeIndex)):
            return None
        factor = (adj / raw.where(raw != 0)).dropna()
        if factor.empty:
            return None
        cutoff = pd.to_datetime(issue_date)
        # Factor on which issue_close sits: the latest factor at/before issue; if
        # issue predates the cached history, the earliest available factor (the
        # best knowable basis — equals 1.0 in the normal fresh-freeze case).
        at_or_before = factor[factor.index <= cutoff]
        adj_ref = float(at_or_before.iloc[-1]) if not at_or_before.empty else float(factor.iloc[0])
        adj_now = float(factor.iloc[-1])
        if adj_ref <= 0 or not (adj_now > 0):
            return None
        issue_close = float(issue_close)
        if issue_close <= 0:
            return None
        exit_on_entry_basis = float(cur) * (adj_now / adj_ref)
        return (exit_on_entry_basis - issue_close) / issue_close
    except Exception:
        return None


def _days_held(issue_date, mark_date) -> int:
    """Calendar days from ``issue_date`` to ``mark_date`` (>= 0), else 0."""
    if not issue_date or mark_date is None:
        return 0
    try:
        import pandas as pd
        return max(0, (pd.to_datetime(mark_date) - pd.to_datetime(issue_date)).days)
    except Exception:
        return 0


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
        # Causal/look-ahead-free: the mark MUST use a close dated strictly after
        # the idea's issue_date. A close at/before issue (a stale or same-session
        # stock_data feed) is non-causal and is skipped, never scored.
        cur = _latest_close_after(stock_data, r["ticker"], r.get("issue_date"))
        if cur is None or cur <= 0:
            continue
        # Return is the SPLIT-SAFE forward return on the total-return basis
        # (R127): a split between issue and now would make the raw
        # ``(cur - issue_close)/issue_close`` read as a fake ~-50%. ``cur`` /
        # ``issue_close`` are kept RAW for display (the real share prices).
        ret = _signed_forward_return(
            stock_data, r["ticker"], r.get("issue_date"), issue_close, cur)
        if ret is None:
            # Adjusted basis unavailable (e.g. no DatetimeIndex) → fall back to
            # the raw forward return so the row is still scored.
            ret = (cur - issue_close) / issue_close
        signed = ret * _dir_sign(r["direction"])
        signed_pct = signed * 100.0
        # Net of an ASSUMED trading cost: one round trip (entry+exit, paid long
        # or short) PLUS, for shorts, a borrow fee accrued over the holding
        # period. Gross is kept untouched; net is a conservative stress test —
        # see processing.cost_model.DISCLAIMER.
        is_short = _dir_sign(r["direction"]) < 0
        days_held = _days_held(
            r.get("issue_date"),
            _latest_date_after(stock_data, r["ticker"], r.get("issue_date")),
        )
        net_pct = net_of_cost_pct(
            signed_pct, r["ticker"], is_short=is_short, days_held=days_held)
        out.append({
            **r,
            "current_close": float(cur),
            "return_pct": ret * 100.0,
            "signed_return_pct": signed_pct,
            "net_signed_return_pct": net_pct,
            "win": signed > 0,
            "net_win": net_pct > 0,
        })
    return out


def track_record_summary(stock_data) -> dict:
    """Aggregate the marked ledger into an honest forward track record:
    overall hit-rate + mean signed return, and the same split by conviction."""
    marked = mark_ledger(stock_data)
    n = len(marked)
    if n == 0:
        return {"n": 0, "hit_rate": 0.0, "mean_signed_return_pct": 0.0,
                "net_hit_rate": 0.0, "mean_net_signed_return_pct": 0.0,
                "cost_drag_pct": 0.0, "cost_disclaimer": COST_DISCLAIMER,
                "by_label": {}}
    wins = sum(1 for m in marked if m["win"])
    net_wins = sum(1 for m in marked if m["net_win"])
    mean = sum(m["signed_return_pct"] for m in marked) / n
    mean_net = sum(m["net_signed_return_pct"] for m in marked) / n
    by_label: dict[str, dict] = {}
    for m in marked:
        lab = m.get("conviction_label") or "?"
        b = by_label.setdefault(
            lab, {"n": 0, "wins": 0, "net_wins": 0, "_sum": 0.0, "_net_sum": 0.0})
        b["n"] += 1
        b["wins"] += 1 if m["win"] else 0
        b["net_wins"] += 1 if m["net_win"] else 0
        b["_sum"] += m["signed_return_pct"]
        b["_net_sum"] += m["net_signed_return_pct"]
    for b in by_label.values():
        b["hit_rate"] = b["wins"] / b["n"] if b["n"] else 0.0
        b["net_hit_rate"] = b["net_wins"] / b["n"] if b["n"] else 0.0
        b["mean_signed_return_pct"] = b.pop("_sum") / b["n"] if b["n"] else 0.0
        b["mean_net_signed_return_pct"] = b.pop("_net_sum") / b["n"] if b["n"] else 0.0
    return {"n": n, "hit_rate": wins / n,
            "mean_signed_return_pct": mean,
            "net_hit_rate": net_wins / n,
            "mean_net_signed_return_pct": mean_net,
            # Cost drag = gross mean − net mean = the average round-trip cost paid.
            "cost_drag_pct": mean - mean_net,
            "cost_disclaimer": COST_DISCLAIMER,
            "by_label": by_label}


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

    # The same significance read NET of assumed round-trip costs — the honest
    # question is whether the edge survives friction, not just whether it exists
    # gross. (Costs are an assumed conservative stress test; see cost_disclaimer.)
    net_rets = [m["net_signed_return_pct"] / 100.0 for m in marks]
    mean_net_pct = (sum(m["net_signed_return_pct"] for m in marks) / n) if n else 0.0
    net_arr = _np.asarray(net_rets, dtype=float)
    net_sd = float(net_arr.std(ddof=1))
    net_sr = float(net_arr.mean() / net_sd) if net_sd > 0 else 0.0
    net_sk = float(_pd.Series(net_rets).skew())
    net_ku = float(_pd.Series(net_rets).kurt())
    if not (_math.isfinite(net_sk) and _math.isfinite(net_ku)):
        net_sk, net_ku = 0.0, 3.0
    else:
        net_ku += 3.0
    net_psr = probabilistic_sharpe_ratio(net_sr, n, skew=net_sk, kurt=net_ku, sr_benchmark=0.0)
    net_significant = net_psr >= threshold
    return {
        "n": n, "sufficient": True, "hit_rate": hit,
        "mean_signed_return_pct": mean_pct,
        "cross_sectional_sharpe": sr, "psr": psr, "is_significant": significant,
        "mean_net_signed_return_pct": mean_net_pct,
        "net_cross_sectional_sharpe": net_sr, "net_psr": net_psr,
        "net_is_significant": net_significant,
        "cost_disclaimer": COST_DISCLAIMER,
        "verdict": (
            f"Cross-sectional Sharpe {sr:+.2f} over {n} realized ideas; "
            f"PSR {psr:.0%} — "
            + ("statistically significant" if significant
               else "not yet significant (treat as noise)")
            + f". Net of assumed costs: Sharpe {net_sr:+.2f}, PSR {net_psr:.0%}"
            + (" — edge survives costs." if net_significant
               else " — does NOT clear net of costs.")
        ),
    }


def tier_drawdown(
    stock_data,
    *,
    min_n: int = 5,
    hit_floor: float = 0.40,
    dd_threshold_pct: float = 15.0,
) -> dict:
    """Per-conviction-tier realized track record + drawdown-from-cost off the ledger.

    Every marked signal is scored from its own issue close to the SAME current
    close, so the marks are co-terminal (overlapping), NOT a sequential
    holding-period series. Compounding them into an equity curve would make the
    running peak — and any peak-to-now drawdown — depend on the arbitrary order
    the marks happen to be in (and on the ticker tie-break when ideas are frozen
    in one batch on the same day). That would make the kill-switch
    non-deterministic, so we do NOT do it.

    Instead the tier is treated as an equal-weight book of its co-terminal
    signals. Its **drawdown from cost** — how far the average live signal is
    underwater = ``max(0, -mean_signed_return)`` — is order-invariant and
    well-defined. A tier with >= ``min_n`` marks AND (hit-rate below
    ``hit_floor`` OR drawdown-from-cost beyond ``dd_threshold_pct``) is flagged
    ``STAND_DOWN`` — the kill-switch signal.

    Returns ``{tier -> {n, hit_rate, mean_signed_return_pct,
    drawdown_from_cost_pct, worst_signal_return_pct, status}}``. Tiers below
    ``min_n`` stay ``ACTIVE`` (insufficient evidence to demote). The result is
    invariant to the order of the underlying marks.
    """
    by_tier: dict[str, list] = {}
    for m in mark_ledger(stock_data):
        by_tier.setdefault(m.get("conviction_label") or "?", []).append(m)

    out: dict[str, dict] = {}
    for tier, rows in by_tier.items():
        rets = [r["signed_return_pct"] / 100.0 for r in rows]
        n = len(rets)
        hit = (sum(1 for r in rets if r > 0) / n) if n else 0.0
        mean_pct = (sum(rets) / n * 100.0) if n else 0.0
        worst_pct = (min(rets) * 100.0) if rets else 0.0
        # Equal-weight tier book underwater-from-cost; order-invariant (the
        # terminal aggregate return does not depend on mark order, unlike a
        # compounded path's running peak).
        drawdown_from_cost_pct = max(0.0, -mean_pct)

        status = "ACTIVE"
        if n >= min_n and (
            hit < hit_floor or drawdown_from_cost_pct > dd_threshold_pct
        ):
            status = "STAND_DOWN"
        out[tier] = {
            "n": n, "hit_rate": hit, "mean_signed_return_pct": mean_pct,
            "drawdown_from_cost_pct": drawdown_from_cost_pct,
            "worst_signal_return_pct": worst_pct,
            "status": status,
        }
    return out
