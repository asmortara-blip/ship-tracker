"""R256 — path-aware signal validation: stop-hit / MAE / MFE defining properties.

The forward track record (state.signal_ledger) marks every idea to the TERMINAL
close only. A signal counted a "win" at the horizon may have blown through its
stop mid-path. ``processing.signal_path_stats.path_excursions`` walks the
look-ahead-free close path AFTER issue and reports the max adverse / favorable
excursion + whether (and WHEN) the stop / target was crossed FIRST.

These tests pin the defining properties with hand-built deterministic price
paths matching the ``processing.book_pnl._close_series`` ingest shape
(DatetimeIndex close frames; the split case also carries ``adj_factor``):

  (a) a monotone-UP path for a LONG → mae_pct ~ 0 and stop_hit=False;
  (b) THE KEY PROPERTY: a path that DIPS BELOW the stop then RECOVERS to a
      terminal "win" → stop_hit=True with the correct first-crossing date,
      even though the terminal close looks like a win;
  (c) a SHORT's adverse direction is UP (the mirror of long);
  (d) a 2:1 split inside the window does NOT fabricate a phantom excursion /
      stop-out (R127 total-return basis);
  (e) dark prices → honest empty.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from processing.signal_path_stats import PathStats, path_excursions


# ---------------------------------------------------------------------------
# Fixture helpers — DatetimeIndex close frames (the _close_series ingest shape)
# ---------------------------------------------------------------------------

def _frame(prices, start: str = "2025-01-01") -> pd.DataFrame:
    """A close frame on a business-day DatetimeIndex (mirrors test_book_pnl)."""
    idx = pd.date_range(start, periods=len(prices), freq="B")
    return pd.DataFrame({"close": list(prices)}, index=idx)


def _dates(n: int, start: str = "2025-01-01"):
    return pd.date_range(start, periods=n, freq="B")


# ---------------------------------------------------------------------------
# (a) Monotone-UP path for a LONG -> no adverse excursion, stop never hit
# ---------------------------------------------------------------------------

def test_long_monotone_up_no_adverse_no_stop() -> None:
    # Issue at 100 on day 0; the forward path only ever rises.
    prices = [100.0, 101.0, 103.0, 106.0, 110.0, 115.0]
    sd = {"ZIM": _frame(prices)}
    issue_date = _dates(len(prices))[0]  # day 0; forward = days 1..5

    ps = path_excursions(
        sd, "ZIM", issue_date, issue_close=100.0, direction="LONG",
        stop_loss=92.0, target=120.0, window_days=30,
    )
    assert isinstance(ps, PathStats)
    assert ps.basis == "real"
    assert ps.n_obs == 5                      # five forward closes inside window
    # A strictly-rising LONG never goes adverse → MAE pinned at 0.0.
    assert ps.mae_pct == 0.0
    # Best favorable excursion is the terminal +15%.
    assert ps.mfe_pct == 15.0
    # Stop (92, below entry) was never touched; target (120) never reached.
    assert ps.stop_hit is False
    assert ps.stop_hit_date is None
    assert ps.realized_at_stop_pct is None
    assert ps.target_hit is False
    assert ps.target_hit_date is None


# ---------------------------------------------------------------------------
# (b) THE KEY PROPERTY — dip below the stop, then recover to a terminal "win"
# ---------------------------------------------------------------------------

def test_stop_hit_is_detected_even_when_terminal_close_is_a_win() -> None:
    """Entry 100, stop 92 (-8%). The path dips to 90 on day 3 (a real stop-out),
    then recovers to a terminal 110 (+10% — a "win" to terminal-only marking).
    path_excursions must flag stop_hit=True on the dip date, exposing the
    stop-out the terminal mark hides."""
    # day:    0      1      2      3*     4      5
    prices = [100.0, 99.0, 95.0, 90.0, 102.0, 110.0]
    idx = _dates(len(prices))
    sd = {"ZIM": _frame(prices)}
    issue_date = idx[0]
    dip_day = idx[3]                          # the first below-stop close

    ps = path_excursions(
        sd, "ZIM", issue_date, issue_close=100.0, direction="LONG",
        stop_loss=92.0, target=120.0, window_days=30,
    )
    assert ps.basis == "real"

    # Terminal close is a win — prove the trap exists.
    terminal_signed = (110.0 - 100.0) / 100.0 * 100.0
    assert terminal_signed > 0                # +10%, looks like a win

    # ...yet the stop WAS hit, first on the day price closed at 90 (<= 92).
    assert ps.stop_hit is True
    assert ps.stop_hit_date == dip_day.date().isoformat()
    # Realized P&L at the stop bar is the -10% drawdown (signed against the long).
    assert ps.realized_at_stop_pct == -10.0
    # MAE is the worst adverse excursion (the -10% trough), MFE the +10% terminal.
    assert ps.mae_pct == -10.0
    assert ps.mfe_pct == 10.0
    # The 120 target was never reached on this path.
    assert ps.target_hit is False


def test_target_hit_first_crossing_date() -> None:
    """A LONG that reaches its target reports target_hit on the FIRST crossing."""
    # day:    0      1      2      3      4
    prices = [100.0, 104.0, 108.0, 121.0, 119.0]
    idx = _dates(len(prices))
    sd = {"MATX": _frame(prices)}
    ps = path_excursions(
        sd, "MATX", idx[0], issue_close=100.0, direction="LONG",
        stop_loss=92.0, target=120.0, window_days=30,
    )
    assert ps.target_hit is True
    assert ps.target_hit_date == idx[3].date().isoformat()   # first close >= 120
    assert ps.stop_hit is False


# ---------------------------------------------------------------------------
# (c) A SHORT's adverse direction is UP (mirror of long)
# ---------------------------------------------------------------------------

def test_short_adverse_is_up_mirror_of_long() -> None:
    """For a SHORT, UP is adverse and DOWN is favorable. Entry 100, stop 108
    (+8%, ABOVE entry for a short). The path spikes to 110 on day 2 (stop-out
    for the short) then falls to a terminal 90."""
    # day:    0      1      2      3      4
    prices = [100.0, 105.0, 110.0, 98.0, 90.0]
    idx = _dates(len(prices))
    sd = {"ZIM": _frame(prices)}
    spike_day = idx[2]

    ps = path_excursions(
        sd, "ZIM", idx[0], issue_close=100.0, direction="SHORT",
        stop_loss=108.0, target=85.0, window_days=30,
    )
    assert ps.basis == "real"
    # Adverse = UP for a short: the +10% spike is the worst (most negative MAE).
    assert ps.mae_pct == -10.0                # signed against the short
    # Favorable = DOWN for a short: the terminal -10% from entry is +10% in favor.
    assert ps.mfe_pct == 10.0
    # The short's stop (108, ABOVE entry) was crossed when price hit 110.
    assert ps.stop_hit is True
    assert ps.stop_hit_date == spike_day.date().isoformat()
    assert ps.realized_at_stop_pct == -10.0   # 10% adverse against the short
    # Target (85) never reached (terminal only fell to 90).
    assert ps.target_hit is False


def test_long_and_short_mirror_on_same_path() -> None:
    """The same price path: a LONG's MAE equals the negative of a SHORT's MFE
    and vice-versa — the signed excursions are exact mirrors."""
    prices = [100.0, 96.0, 104.0, 98.0, 102.0]
    idx = _dates(len(prices))
    sd = {"DAC": _frame(prices)}
    lng = path_excursions(sd, "DAC", idx[0], 100.0, "LONG", window_days=30)
    sht = path_excursions(sd, "DAC", idx[0], 100.0, "SHORT", window_days=30)
    # LONG worst-down (-4%) == SHORT best-favorable (+4%); LONG best-up (+4%) ==
    # SHORT worst-adverse (-4%).
    assert lng.mae_pct == -sht.mfe_pct
    assert lng.mfe_pct == -sht.mae_pct


# ---------------------------------------------------------------------------
# (d) A split inside the window does NOT fabricate a phantom excursion (R127)
# ---------------------------------------------------------------------------

def _split_frame(start: str = "2025-01-01"):
    """A 2:1 split mid-window. RAW close halves at the split row; adj_factor
    doubles so ``close * adj_factor`` reconstructs the un-split total-return
    path. Mirrors tests/test_price_lookahead_consumers._split_frame, with date
    as a COLUMN (the real cached shape _close_series ingests).

    Underlying total-return path is a gentle RISE 100 -> 110 across the window,
    so the TRUE excursion for a long is favorable, never adverse. Naively, the
    RAW close halving from ~105 to ~52 at the split would read as a ~-50%
    catastrophe (and a stop-out). It must NOT.
    """
    n = 8
    split_at = 4
    # True (un-split) total-return path: monotone up from 100 to ~110.
    path = np.array([100.0, 102.0, 104.0, 106.0, 107.0, 108.0, 109.0, 110.0])
    raw = path.copy()
    raw[split_at:] = raw[split_at:] / 2.0     # RAW price halves on the split
    adj = np.ones(n)
    adj[split_at:] = 2.0                      # factor doubles -> raw*adj == path
    return pd.DataFrame({
        "date": pd.date_range(start, periods=n, freq="B"),
        "symbol": "ZIM",
        "close": raw,
        "adj_factor": adj,
    }), pd.date_range(start, periods=n, freq="B")


def test_split_does_not_fabricate_phantom_excursion_or_stop() -> None:
    sd_frame, idx = _split_frame()
    sd = {"ZIM": sd_frame}
    # Issue on day 0 at the un-split entry (100). The TRUE path only rises.
    ps = path_excursions(
        sd, "ZIM", idx[0], issue_close=100.0, direction="LONG",
        stop_loss=92.0, target=None, window_days=30,
    )
    assert ps.basis == "real"
    assert ps.n_obs == 7                       # days 1..7 are inside the window
    # On the TOTAL-RETURN basis the long never goes adverse → MAE ~ 0, despite
    # the raw close halving at the split. The phantom -50% is NOT fabricated.
    assert ps.mae_pct == 0.0
    # The -8% stop is NOT hit (the real path rose); a naive raw-close read would
    # have flagged a false stop-out at the split.
    assert ps.stop_hit is False
    assert ps.stop_hit_date is None
    # Favorable excursion tracks the real +10% rise (terminal 110 vs entry 100).
    assert ps.mfe_pct == 10.0


def test_split_neutral_when_adj_factor_absent_reduces_to_raw() -> None:
    """Fixture-neutrality: an adj_factor-ABSENT frame behaves as raw-close. With
    no split column, the same RAW levels are the path — so the raw halving DOES
    register (proving the split-safety above comes specifically from adj_factor,
    not from some unrelated smoothing)."""
    # Same RAW closes as the split frame but with NO adj_factor → raw is the path.
    raw = [100.0, 102.0, 104.0, 106.0, 53.5, 54.0, 54.5, 55.0]
    idx = _dates(len(raw))
    sd = {"ZIM": pd.DataFrame({"close": raw}, index=idx)}
    ps = path_excursions(
        sd, "ZIM", idx[0], issue_close=100.0, direction="LONG",
        stop_loss=92.0, target=None, window_days=30,
    )
    assert ps.basis == "real"
    # Without adj_factor the ~-46% drop is taken at face value → stop IS hit.
    assert ps.stop_hit is True
    assert ps.mae_pct < -40.0


# ---------------------------------------------------------------------------
# (e) Dark prices -> honest empty
# ---------------------------------------------------------------------------

def test_dark_prices_are_honest_empty() -> None:
    # Ticker not in the feed at all.
    ps = path_excursions({}, "ZIM", "2025-01-01", 100.0, "LONG",
                         stop_loss=92.0, target=120.0)
    assert ps.basis == "empty"
    assert ps.n_obs == 0
    assert ps.mae_pct == 0.0 and ps.mfe_pct == 0.0
    assert ps.stop_hit is False and ps.target_hit is False
    assert ps.stop_hit_date is None and ps.realized_at_stop_pct is None
    assert ps.ticker == "ZIM" and ps.direction == "LONG"


def test_empty_frame_is_honest_empty() -> None:
    ps = path_excursions({"ZIM": pd.DataFrame({"close": []})},
                         "ZIM", "2025-01-01", 100.0, "LONG")
    assert ps.basis == "empty"
    assert ps.n_obs == 0


def test_window_with_no_forward_close_is_honest_empty() -> None:
    """All closes are at/before the issue date → no causal forward obs → empty
    (look-ahead-free: a same-/prior-session close is never scored)."""
    prices = [100.0, 101.0, 102.0]
    idx = _dates(len(prices))
    sd = {"ZIM": _frame(prices)}
    # Issue strictly AFTER the last available close.
    ps = path_excursions(sd, "ZIM", idx[-1] + pd.Timedelta(days=5),
                         100.0, "LONG", stop_loss=92.0)
    assert ps.basis == "empty"
    assert ps.n_obs == 0


def test_window_days_bounds_the_forward_walk() -> None:
    """Only closes within window_days of issue are walked — a later adverse
    move beyond the window is NOT counted."""
    # 40 business days; a big drop only on the very last day (~day 39).
    prices = [100.0 + 0.01 * i for i in range(39)] + [60.0]
    idx = _dates(len(prices))
    sd = {"ZIM": _frame(prices)}
    # A 10-calendar-day window can't reach the day-39 crash.
    short = path_excursions(sd, "ZIM", idx[0], 100.0, "LONG",
                            stop_loss=92.0, window_days=10)
    assert short.stop_hit is False
    assert short.mae_pct == 0.0                # nothing adverse inside 10 days
    # A wide window DOES reach the crash.
    wide = path_excursions(sd, "ZIM", idx[0], 100.0, "LONG",
                           stop_loss=92.0, window_days=120)
    assert wide.stop_hit is True
    assert wide.mae_pct < -30.0


# ---------------------------------------------------------------------------
# Guards: neutral direction, non-positive issue close, never raises
# ---------------------------------------------------------------------------

def test_neutral_direction_is_empty() -> None:
    prices = [100.0, 90.0, 110.0]
    idx = _dates(len(prices))
    sd = {"ZIM": _frame(prices)}
    ps = path_excursions(sd, "ZIM", idx[0], 100.0, "NEUTRAL", stop_loss=92.0)
    assert ps.basis == "empty"
    assert ps.n_obs == 0


def test_nonpositive_issue_close_is_empty() -> None:
    prices = [100.0, 90.0, 110.0]
    idx = _dates(len(prices))
    sd = {"ZIM": _frame(prices)}
    assert path_excursions(sd, "ZIM", idx[0], 0.0, "LONG").basis == "empty"
    assert path_excursions(sd, "ZIM", idx[0], -5.0, "LONG").basis == "empty"


def test_no_stop_or_target_only_excursions() -> None:
    """With stop_loss=None and target=None the walk still returns MAE/MFE and
    never flags a crossing."""
    prices = [100.0, 95.0, 108.0, 102.0]
    idx = _dates(len(prices))
    sd = {"ZIM": _frame(prices)}
    ps = path_excursions(sd, "ZIM", idx[0], 100.0, "LONG",
                         stop_loss=None, target=None, window_days=30)
    assert ps.basis == "real"
    assert ps.stop_hit is False and ps.target_hit is False
    assert ps.realized_at_stop_pct is None
    assert ps.mae_pct == -5.0                  # the day-1 trough
    assert ps.mfe_pct == 8.0                    # the day-2 peak


def test_never_raises_on_garbage_input() -> None:
    """House rule: degrade to honest empty, never raise."""
    assert path_excursions(None, "ZIM", "2025-01-01", 100.0, "LONG").basis == "empty"
    assert path_excursions("not-a-dict", "ZIM", None, 100.0, "LONG").basis == "empty"
    assert path_excursions({"ZIM": 12345}, "ZIM", "2025-01-01", 100.0,
                           "LONG").basis == "empty"


def test_pathstats_is_frozen() -> None:
    ps = path_excursions({}, "ZIM", "2025-01-01", 100.0, "LONG")
    import dataclasses
    assert dataclasses.is_dataclass(ps)
    try:
        ps.mae_pct = 99.0          # type: ignore[misc]
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised, "PathStats must be a frozen dataclass"
