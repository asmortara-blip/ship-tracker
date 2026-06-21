"""Path-aware signal validation — stop-hit / MAE / MFE (rec R256).

Every alpha signal carries an ``entry_price`` / ``target_price`` / ``stop_loss``
and a quoted risk/reward (see :mod:`engine.alpha_engine`). The forward track
record in :mod:`state.signal_ledger`, however, marks each idea to the TERMINAL
close only. A signal counted a "win" at the horizon may have blown clean through
its stop mid-path — a real-money stop-out that terminal-only marking hides. This
module makes the record PATH-truthful, not just terminal-truthful: it walks the
look-ahead-free close path AFTER issue and computes the max ADVERSE / FAVORABLE
excursion and whether (and WHEN) the stop or target was crossed FIRST.

Honesty throughline (mirrors :mod:`engine.alpha_engine` /
:mod:`processing.book_pnl`):

  * Real prices or an honest empty — never a fabricated level. When the feed is
    dark for a ticker, or the post-issue window doesn't resolve, the result is
    ``basis="empty"`` with ``n_obs=0`` and no excursion claimed.
  * Look-ahead-free: only closes dated STRICTLY AFTER ``issue_date`` (and within
    ``window_days``) are walked — the exit leg can never peek at the issue day
    or earlier.
  * Split-safe (R127): excursions ride the TOTAL-RETURN basis. The forward raw
    close is put back on the ENTRY's basis via the look-ahead-free
    ``adj_factor`` ratio (``adj_now / adj_ref``) — exactly the device
    :func:`state.signal_ledger._signed_forward_return` uses — so a split in the
    window does NOT inject a phantom ~-50% excursion or a phantom stop-out. The
    raw close path is REUSED from :func:`processing.book_pnl._close_series`;
    prices are never re-derived here.
  * Never raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Direction vocabularies, kept in lock-step with state.signal_ledger so this
# module accepts the alpha_engine LONG/SHORT labels AND the cascade
# Bullish/Bearish labels interchangeably.
_LONG = {"bullish", "long", "buy"}
_SHORT = {"bearish", "short", "sell"}


def _dir_sign(direction: str) -> int:
    """+1 long, -1 short, 0 neutral/unknown (mirrors signal_ledger._dir_sign)."""
    d = (direction or "").strip().lower()
    if d in _LONG:
        return 1
    if d in _SHORT:
        return -1
    return 0


@dataclass(frozen=True)
class PathStats:
    """Path-aware excursion stats for one signal, relative to its issue close.

    ``mae_pct`` / ``mfe_pct`` are SIGNED so they read the same for long and
    short: an adverse move is reported as a NEGATIVE ``mae_pct`` (worst against
    the position) and a favorable move as a POSITIVE ``mfe_pct`` (best in the
    position's favor). For a LONG the adverse direction is DOWN; for a SHORT it
    is UP (the mirror). Both default to ``0.0`` when no forward observation is
    available.

    ``stop_hit`` / ``target_hit`` are FIRST-crossing flags (closed-bar): the
    forward close, put back on the entry's split-adjusted basis, reached or
    crossed the raw stop / target level. ``*_date`` is the ISO date of that
    first crossing. ``realized_at_stop_pct`` is the signed P&L-against-position
    at the stop bar (negative for a real stop-out) — ``None`` when the stop was
    never hit.

    ``basis`` is ``"real"`` when a real post-issue path was walked, else
    ``"empty"`` (dark prices / unresolved window): an honest empty, never
    fabricated.
    """

    ticker: str
    direction: str
    mae_pct: float                       # max ADVERSE excursion %, signed (<= 0)
    mfe_pct: float                       # max FAVORABLE excursion %, signed (>= 0)
    stop_hit: bool
    stop_hit_date: Optional[str]
    target_hit: bool
    target_hit_date: Optional[str]
    realized_at_stop_pct: Optional[float]
    n_obs: int
    basis: str                           # "real" | "empty"


def _empty(ticker: str, direction: str) -> PathStats:
    """The honest-empty result — no path resolved, nothing claimed."""
    return PathStats(
        ticker=str(ticker or ""),
        direction=str(direction or ""),
        mae_pct=0.0,
        mfe_pct=0.0,
        stop_hit=False,
        stop_hit_date=None,
        target_hit=False,
        target_hit_date=None,
        realized_at_stop_pct=None,
        n_obs=0,
        basis="empty",
    )


def path_excursions(
    stock_data,
    ticker: str,
    issue_date,
    issue_close,
    direction: str,
    *,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    window_days: int = 30,
) -> PathStats:
    """Walk the look-ahead-free forward close path and score path-aware stats.

    Computes, over closes dated strictly AFTER ``issue_date`` and within
    ``window_days`` calendar days of it, the max adverse / favorable excursion
    relative to the FROZEN ``issue_close`` in the direction of the position, and
    whether the ``stop_loss`` / ``target`` raw price levels were crossed and on
    which date FIRST.

    Args:
        stock_data:   dict[ticker -> DataFrame] — the normalized cache shape
                      :func:`processing.book_pnl._close_series` ingests.
        ticker:       the symbol to walk.
        issue_date:   the idea's issue date (anything ``pd.to_datetime`` parses).
        issue_close:  the FROZEN real issue close (the entry leg; raw price).
        direction:    LONG/Bullish/buy or SHORT/Bearish/sell (neutral → empty).
        stop_loss:    raw stop price level, or ``None`` to skip stop detection.
        target:       raw target price level, or ``None`` to skip target detect.
        window_days:  forward calendar-day horizon to walk (default 30).

    Returns:
        :class:`PathStats`. ``basis="empty"`` (and ``n_obs=0``) when prices are
        dark, ``issue_close`` is non-positive, the direction is neutral, or no
        post-issue close lands inside the window. Never raises.
    """
    sign = _dir_sign(direction)
    try:
        import pandas as pd

        from processing.book_pnl import _close_series

        if sign == 0:
            return _empty(ticker, direction)
        try:
            issue_close = float(issue_close)
        except (TypeError, ValueError):
            return _empty(ticker, direction)
        if not (issue_close > 0):
            return _empty(ticker, direction)

        # REUSE book_pnl._close_series for the look-ahead-free, deduped close
        # path — both RAW (the transactable level the stop/target sit on) and
        # ADJUSTED (close * adj_factor, the total-return level). Pulling both
        # from the same frame lets us recover the per-date adj_factor as
        # adj/raw and put each forward close back on the ENTRY's basis, exactly
        # as state.signal_ledger._signed_forward_return does.
        raw = _close_series(stock_data, ticker, adjusted=False)
        adj = _close_series(stock_data, ticker, adjusted=True)
        if (raw is None or adj is None or raw.empty
                or not isinstance(raw.index, pd.DatetimeIndex)):
            return _empty(ticker, direction)

        factor = (adj / raw.where(raw != 0)).dropna()
        if factor.empty:
            return _empty(ticker, direction)

        try:
            cutoff = pd.to_datetime(issue_date)
        except Exception:
            return _empty(ticker, direction)

        # adj_ref = the factor on which issue_close was observed: the latest
        # factor at/before issue (or the earliest known factor if issue
        # predates the cache — the best knowable basis, == 1.0 in the normal
        # fresh-freeze case). Identical convention to the ledger.
        at_or_before = factor[factor.index <= cutoff]
        adj_ref = (float(at_or_before.iloc[-1]) if not at_or_before.empty
                   else float(factor.iloc[0]))
        if not (adj_ref > 0):
            return _empty(ticker, direction)

        # Look-ahead-free forward window: strictly AFTER issue, within window.
        horizon_end = cutoff + pd.Timedelta(days=int(window_days))
        fwd_raw = raw[(raw.index > cutoff) & (raw.index <= horizon_end)]
        if fwd_raw.empty:
            return _empty(ticker, direction)

        n_obs = int(len(fwd_raw))

        mae_pct = 0.0          # most adverse (signed against position): <= 0
        mfe_pct = 0.0          # most favorable (signed for position): >= 0
        stop_hit = False
        stop_hit_date: Optional[str] = None
        target_hit = False
        target_hit_date: Optional[str] = None
        realized_at_stop_pct: Optional[float] = None

        # Pre-validate stop/target levels once (they're constant across the walk).
        stop_level: Optional[float] = None
        if stop_loss is not None:
            try:
                lvl = float(stop_loss)
                if lvl > 0:
                    stop_level = lvl
            except (TypeError, ValueError):
                stop_level = None
        target_level: Optional[float] = None
        if target is not None:
            try:
                lvl = float(target)
                if lvl > 0:
                    target_level = lvl
            except (TypeError, ValueError):
                target_level = None

        for ts, raw_close in fwd_raw.items():
            try:
                raw_close = float(raw_close)
            except (TypeError, ValueError):
                continue
            # Put this forward close back on the ENTRY's basis so a split in the
            # window nets out (R127). adj_now is the factor on this bar; default
            # to adj_ref (→ ratio 1.0, raw unchanged) if it's somehow missing.
            at_or_before_ts = factor[factor.index <= ts]
            adj_now = (float(at_or_before_ts.iloc[-1])
                       if not at_or_before_ts.empty else adj_ref)
            close_on_entry_basis = (raw_close * (adj_now / adj_ref)
                                    if adj_ref > 0 else raw_close)

            # Signed P&L against the position from the frozen entry. For a LONG
            # (sign +1) an up move is favorable; for a SHORT (sign -1) a down
            # move is favorable — multiplying by sign flips it.
            signed_pct = (close_on_entry_basis - issue_close) / issue_close * 100.0 * sign

            if signed_pct < mae_pct:
                mae_pct = signed_pct
            if signed_pct > mfe_pct:
                mfe_pct = signed_pct

            # Stop / target crossing on the SAME entry-basis level so they're
            # split-safe too. The stop/target are raw price levels anchored to
            # the raw entry; a LONG's stop is BELOW entry (cross = close <=
            # stop), a SHORT's stop is ABOVE entry (cross = close >= stop). The
            # ``sign * (stop - close) >= 0`` form captures BOTH directions (the
            # close moved to/through the stop AGAINST the position).
            if stop_level is not None and not stop_hit:
                if sign * (stop_level - close_on_entry_basis) >= 0:
                    stop_hit = True
                    stop_hit_date = ts.date().isoformat()
                    realized_at_stop_pct = signed_pct

            # Favorable cross: the close moved to/through the target FOR the
            # position (``sign * (close - target) >= 0``).
            if target_level is not None and not target_hit:
                if sign * (close_on_entry_basis - target_level) >= 0:
                    target_hit = True
                    target_hit_date = ts.date().isoformat()

        return PathStats(
            ticker=str(ticker or ""),
            direction=str(direction or ""),
            mae_pct=round(mae_pct, 4),
            mfe_pct=round(mfe_pct, 4),
            stop_hit=stop_hit,
            stop_hit_date=stop_hit_date,
            target_hit=target_hit,
            target_hit_date=target_hit_date,
            realized_at_stop_pct=(round(realized_at_stop_pct, 4)
                                  if realized_at_stop_pct is not None else None),
            n_obs=n_obs,
            basis="real",
        )
    except Exception:
        # House rule: never raise — degrade to an honest empty.
        return _empty(ticker, direction)
