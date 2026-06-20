"""processing/disruption_event_study.py — honest event study around REAL
shipping-disruption events, plus a lead/lag rolling-correlation tool.

Why this module is scoped the way it is
----------------------------------------
The headline question this platform keeps getting asked is *"do ships /
disruptions actually correlate with shipping-company stock moves?"*. The
temptation is to regress the platform's **modeled disruption signals** (the
Shipping Stress Index, congestion scores, voyage-delay estimates) against
equity returns and report a correlation. **That would be dishonest here.**
Those modeled signals are produced as *snapshots seeded off the current
date* — they are point-in-time estimates, NOT real historical time series.
You cannot build a trustworthy historical correlation from a series that does
not actually exist in history. Any number you got that way would be an
artifact of the seeding, not a measured relationship.

What *is* real on both axes:

  (a) the **dates** of real, well-documented past disruption events
      (Suez 2021, Red Sea / Houthi 2024, the Panama drought, …) — see
      :mod:`data.historical_events`; and
  (b) real **historical equity prices** for the shipping names.

So the trustworthy analysis is an **event study**: align REAL price series to
REAL event dates and describe how prices actually moved in the window around
each event. That is descriptive history grounded entirely in real data.

This module therefore provides:

  * :func:`event_study` — per-ticker price behaviour in a ``[-pre, +post]``
    trading-day window around one real event date (cumulative return,
    abnormal return vs a pre-event baseline, max drawdown / run-up).
  * :func:`aggregate_event_studies` — the *typical* per-ticker move averaged
    across several real events.
  * :func:`rolling_lead_lag_correlation` — a pure lead/lag rolling-correlation
    tool on two return series (e.g. two equities, or — clearly **illustrative
    only** — an equity vs a modeled signal).
  * :func:`summarize` — a short, honest UI string.

Honesty contract (read before wiring a correlation into the UI)
---------------------------------------------------------------
* The **event study** (real event dates × real prices) is the trustworthy,
  defensible analysis. Treat its output as *descriptive history*.
* Any correlation that involves a **modeled signal** (SSI / congestion /
  voyage delays) is **ILLUSTRATIVE ONLY** — the modeled signal is a
  current-date snapshot, not a real historical series, so the correlation is
  not a measured fact about the past. Label it as such wherever it surfaces.
* Nothing here is a forecast, and **none of it is investment advice.**

Purity
------
The compute functions take price / return data as plain arguments
(date-indexed ``pandas.Series`` or ``(dates, closes)`` pairs). They perform
**no I/O and touch no network** — yfinance / live feeds are the caller's job.
Functions are deterministic and numpy-safe: empty / short series,
divide-by-zero and NaN are all guarded, and **no compute function raises** on
bad per-ticker input — it skips that ticker instead. Dependencies are stdlib
+ numpy + pandas only.

This is **not investment advice.**
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

# A "price input" for one ticker may be either a date-indexed Series or a
# (dates, closes) pair. Both are coerced through :func:`_coerce_price_series`.
PriceLike = Union[pd.Series, Tuple[Sequence, Sequence]]

# Standard honesty disclaimer reused across the summary strings.
NOT_ADVICE = "Descriptive history, not a forecast or investment advice."

__all__ = [
    "NOT_ADVICE",
    "EventStudyResult",
    "EventStudySignificance",
    "event_study",
    "aggregate_event_studies",
    "event_study_significance",
    "rolling_lead_lag_correlation",
    "summarize",
    "real_event_dates",
]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EventStudyResult:
    """Per-ticker outcome of an :func:`event_study` over one event window.

    All returns are simple (arithmetic) returns expressed as fractions, e.g.
    ``-0.10`` for a 10% fall. ``event_close`` anchors them: every cumulative
    figure is measured *from the close on (or just before) the event date*.

    Fields
    ------
    ticker
        The equity symbol.
    n_pre, n_post
        Trading-day observations actually available before / after the event
        anchor inside the requested window (may be fewer than ``pre`` /
        ``post`` near the ends of the series).
    event_close
        The price used as the event anchor — the last close on or before the
        event date.
    cum_return_window
        Cumulative simple return from the event anchor to the **end** of the
        post window: ``close_end / event_close - 1``.
    abnormal_return
        ``cum_return_window`` minus the expected drift implied by the
        pre-event baseline mean daily return over the same number of post
        days. A negative value means the post-event move was *worse* than the
        stock's own pre-event trend — the disruption "abnormality".
    baseline_mean_daily_return
        Mean daily simple return over the pre-event window (the baseline drift
        the abnormal return is measured against). ``nan`` if no pre data.
    max_drawdown
        Most negative peak-to-trough move inside the **full** window
        (``<= 0``). ``0.0`` if the window only rose.
    max_runup
        Largest trough-to-peak move inside the full window (``>= 0``).
    """

    ticker: str
    n_pre: int
    n_post: int
    event_close: float
    cum_return_window: float
    abnormal_return: float
    baseline_mean_daily_return: float
    max_drawdown: float
    max_runup: float
    estimation_sigma: float = float("nan")   # std of pre-event daily returns (BMP scale)


# ---------------------------------------------------------------------------
# Coercion / cleaning helpers
# ---------------------------------------------------------------------------

def _coerce_price_series(prices: PriceLike) -> Optional[pd.Series]:
    """Coerce one ticker's price input to a clean, sorted, date-indexed Series.

    Accepts either a ``pandas.Series`` (any date-like index) or a
    ``(dates, closes)`` pair of equal-length sequences. Returns ``None`` for
    anything that cannot become a usable series — the caller skips that ticker
    rather than raising. The returned series is:

      * indexed by ``DatetimeIndex`` (normalised to midnight),
      * sorted ascending by date,
      * de-duplicated on the index (last value wins),
      * float-typed with non-finite / non-positive prices dropped.

    Non-positive prices are dropped because returns are computed as ratios; a
    zero or negative close is never a real equity print and would poison the
    pct-change math.
    """
    if prices is None:
        return None

    # (dates, closes) pair → Series.
    if isinstance(prices, tuple):
        if len(prices) != 2:
            return None
        dates, closes = prices
        try:
            ser = pd.Series(list(closes), index=list(dates))
        except (TypeError, ValueError):
            return None
    elif isinstance(prices, pd.Series):
        ser = prices.copy()
    else:
        return None

    if ser.empty:
        return None

    # Index → DatetimeIndex (normalised). Bad dates become NaT and are dropped.
    try:
        idx = pd.to_datetime(ser.index, errors="coerce")
    except (TypeError, ValueError):
        return None
    ser.index = idx
    ser = ser[~ser.index.isna()]
    if ser.empty:
        return None
    # Normalise to midnight so an event date (a calendar day) aligns cleanly.
    ser.index = ser.index.normalize()

    # Values → finite, positive floats.
    ser = pd.to_numeric(ser, errors="coerce").astype(float)
    ser = ser[np.isfinite(ser.to_numpy())]
    ser = ser[ser > 0.0]
    if ser.empty:
        return None

    # Sort + de-dup (keep last print on any duplicated calendar day).
    ser = ser.sort_index()
    ser = ser[~ser.index.duplicated(keep="last")]
    return ser


def _to_timestamp(event_date) -> Optional[pd.Timestamp]:
    """Coerce an event date (str / date / datetime / Timestamp) to a midnight
    :class:`pandas.Timestamp`, or ``None`` if it cannot be parsed."""
    if event_date is None:
        return None
    if isinstance(event_date, pd.Timestamp):
        ts = event_date
    elif isinstance(event_date, (date, datetime)):
        ts = pd.Timestamp(event_date)
    else:
        try:
            ts = pd.Timestamp(str(event_date))
        except (TypeError, ValueError):
            return None
    if ts is pd.NaT:
        return None
    try:
        return ts.normalize()
    except (TypeError, ValueError):
        return None


def _anchor_position(index: pd.DatetimeIndex, event_ts: pd.Timestamp) -> Optional[int]:
    """Return the integer position of the event anchor in ``index``.

    The anchor is the last observation **on or before** the event date (so a
    weekend / holiday event date snaps back to the prior trading day). Returns
    ``None`` if every observation is strictly after the event date (no anchor).
    """
    # searchsorted on the right gives the count of obs with index <= event_ts;
    # subtract 1 for the position of the last such obs.
    pos = int(index.searchsorted(event_ts, side="right")) - 1
    if pos < 0:
        return None
    return pos


# ---------------------------------------------------------------------------
# 1) Single-event study
# ---------------------------------------------------------------------------

def event_study(
    prices_by_ticker: Mapping[str, PriceLike],
    event_date,
    *,
    pre: int = 20,
    post: int = 20,
    min_pre: int = 2,
) -> dict[str, EventStudyResult]:
    """Study price behaviour in a window around one REAL event date.

    For each ticker in ``prices_by_ticker`` the function isolates the
    ``[-pre, +post]`` *trading-day* window centred on the event anchor (the
    last close on or before ``event_date``) and computes an
    :class:`EventStudyResult`.

    Parameters
    ----------
    prices_by_ticker
        ``ticker -> price input``; each value is a date-indexed
        ``pandas.Series`` or a ``(dates, closes)`` pair. Cleaned via
        :func:`_coerce_price_series`.
    event_date
        The real event's calendar date (``str`` / ``date`` / ``Timestamp``).
    pre, post
        Trading-day half-widths of the window. Negative values are clamped to
        ``0``.
    min_pre
        Minimum pre-event observations required to (a) keep the ticker at all
        and (b) compute a baseline. Tickers with fewer are skipped. Must be
        ``>= 1``; the baseline mean needs at least one pre-event return.

    Returns
    -------
    dict[str, EventStudyResult]
        One entry per ticker that had enough data. Tickers lacking data, an
        anchor, or enough pre-event history are **silently skipped** — the
        function never raises on per-ticker problems.

    Notes
    -----
    Uses real prices and a real event date → the output is trustworthy,
    descriptive history. It is **not** a forecast and **not** investment
    advice.
    """
    pre = max(0, int(pre))
    post = max(0, int(post))
    min_pre = max(1, int(min_pre))

    event_ts = _to_timestamp(event_date)
    out: dict[str, EventStudyResult] = {}
    if event_ts is None or not isinstance(prices_by_ticker, Mapping):
        return out

    for ticker, raw in prices_by_ticker.items():
        ser = _coerce_price_series(raw)
        if ser is None or len(ser) < 2:
            continue

        anchor = _anchor_position(ser.index, event_ts)
        if anchor is None:
            continue

        # Slice the trading-day window. lo..hi are inclusive positions.
        lo = max(0, anchor - pre)
        hi = min(len(ser) - 1, anchor + post)
        window = ser.iloc[lo : hi + 1]
        if window.empty:
            continue

        anchor_in_window = anchor - lo  # position of the event close in `window`
        n_pre = anchor_in_window               # obs strictly before the anchor
        n_post = (hi - anchor)                  # obs strictly after the anchor

        # Require a minimum pre-history (need >= min_pre prices before the
        # anchor → at least min_pre-1 pre returns, plus the anchor return).
        if n_pre < min_pre:
            continue

        event_close = float(ser.iloc[anchor])
        if not math.isfinite(event_close) or event_close <= 0.0:
            continue

        values = window.to_numpy(dtype=float)

        # --- cumulative window return: end vs the event anchor -------------
        end_close = float(values[-1])
        cum_return_window = end_close / event_close - 1.0

        # --- pre-event baseline GEOMETRIC mean daily return ----------------
        # Geometric (log) mean, NOT arithmetic: `abnormal_return` below
        # compounds this over the post window and subtracts it from the
        # GEOMETRIC `cum_return_window`, so both must be on the same footing.
        # An arithmetic mean carries a positive Jensen gap on a volatile-but-
        # net-flat pre-window, which would manufacture a phantom (negative)
        # abnormal return where nothing abnormal actually happened.
        pre_prices = values[: anchor_in_window + 1]  # include the anchor close
        if pre_prices.size >= 2:
            pre_rets = pre_prices[1:] / pre_prices[:-1] - 1.0
            pre_rets = pre_rets[np.isfinite(pre_rets) & (pre_rets > -1.0)]
            baseline_mean = (
                float(np.expm1(np.mean(np.log1p(pre_rets))))
                if pre_rets.size else float("nan")
            )
            estimation_sigma = (float(np.std(pre_rets, ddof=1))
                                if pre_rets.size >= 2 else float("nan"))
        else:
            baseline_mean = float("nan")
            estimation_sigma = float("nan")

        # --- abnormal return: observed window move minus expected drift ----
        # Expected drift = baseline mean daily return compounded over n_post
        # post-event days. If we have no baseline, the abnormal return is nan.
        if math.isfinite(baseline_mean) and n_post > 0:
            expected = (1.0 + baseline_mean) ** n_post - 1.0
            abnormal_return = cum_return_window - expected
        elif n_post == 0:
            # No post window → no abnormal move to speak of.
            abnormal_return = 0.0
        else:
            abnormal_return = float("nan")

        # --- max drawdown / run-up across the FULL window ------------------
        max_drawdown, max_runup = _drawdown_runup(values)

        out[ticker] = EventStudyResult(
            ticker=str(ticker),
            n_pre=int(n_pre),
            n_post=int(n_post),
            event_close=event_close,
            cum_return_window=float(cum_return_window),
            abnormal_return=float(abnormal_return),
            baseline_mean_daily_return=float(baseline_mean),
            max_drawdown=float(max_drawdown),
            max_runup=float(max_runup),
            estimation_sigma=float(estimation_sigma),
        )

    return out


def _drawdown_runup(values: np.ndarray) -> tuple[float, float]:
    """Return ``(max_drawdown, max_runup)`` over a price path.

    ``max_drawdown <= 0`` is the most negative peak→trough move; ``max_runup
    >= 0`` is the largest trough→peak move. A monotone path yields ``0.0`` for
    the side it never goes. Empty / single-point / non-finite paths give
    ``(0.0, 0.0)``.
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 2:
        return 0.0, 0.0
    running_max = np.maximum.accumulate(v)
    running_min = np.minimum.accumulate(v)
    # Drawdown: current vs the highest point seen so far (<= 0).
    dd = v / running_max - 1.0
    max_drawdown = float(np.min(dd))
    # Run-up: current vs the lowest point seen so far (>= 0).
    ru = v / running_min - 1.0
    max_runup = float(np.max(ru))
    return min(0.0, max_drawdown), max(0.0, max_runup)


# ---------------------------------------------------------------------------
# 2) Aggregate across several real events
# ---------------------------------------------------------------------------

def aggregate_event_studies(
    prices_by_ticker: Mapping[str, PriceLike],
    event_dates: Iterable,
    *,
    pre: int = 20,
    post: int = 20,
    min_pre: int = 2,
) -> dict[str, dict]:
    """Average a ticker's event-study response across several REAL events.

    Runs :func:`event_study` once per date in ``event_dates`` and, for each
    ticker, averages the numeric outcomes over the events where that ticker had
    usable data. This is the "what does this name *typically* do around a
    shipping disruption" summary.

    Returns
    -------
    dict[str, dict]
        ``ticker -> {n_events, mean_cum_return, mean_abnormal_return,
        mean_max_drawdown, mean_max_runup}``. NaN abnormal returns (events with
        no baseline) are excluded from ``mean_abnormal_return`` via nanmean;
        ``n_events`` counts every event the ticker had a result for. Tickers
        with no usable event are omitted.

    Like :func:`event_study`, this is descriptive history over real events and
    real prices — **not** a forecast and **not** investment advice.
    """
    # Collect per-ticker lists of per-event results.
    by_ticker: dict[str, list[EventStudyResult]] = {}
    for ev in event_dates if event_dates is not None else []:
        per = event_study(
            prices_by_ticker, ev, pre=pre, post=post, min_pre=min_pre
        )
        for ticker, res in per.items():
            by_ticker.setdefault(ticker, []).append(res)

    out: dict[str, dict] = {}
    for ticker, results in by_ticker.items():
        if not results:
            continue
        cum = np.array([r.cum_return_window for r in results], dtype=float)
        abn = np.array([r.abnormal_return for r in results], dtype=float)
        dd = np.array([r.max_drawdown for r in results], dtype=float)
        ru = np.array([r.max_runup for r in results], dtype=float)
        out[ticker] = {
            "n_events": int(len(results)),
            "mean_cum_return": _safe_nanmean(cum),
            "mean_abnormal_return": _safe_nanmean(abn),
            "mean_max_drawdown": _safe_nanmean(dd),
            "mean_max_runup": _safe_nanmean(ru),
        }
    return out


def _safe_nanmean(arr: np.ndarray) -> float:
    """``np.nanmean`` that returns ``nan`` (not a warning) for all-NaN/empty."""
    a = np.asarray(arr, dtype=float)
    if a.size == 0 or not np.any(np.isfinite(a)):
        return float("nan")
    return float(np.nanmean(a))


# ---------------------------------------------------------------------------
# 2b) Statistical significance — is the abnormal move real, not noise? (S8)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EventStudySignificance:
    """Inferential test of whether disruption events move shipping equities.

    The unit of inference is the EVENT, not the carrier. Within each event the
    per-carrier abnormal returns are standardized by their own estimation-window
    sigma (BMP-style, so different-volatility carriers are comparable) and
    averaged; the test then runs ACROSS events. Averaging within-event absorbs
    the strong cross-carrier correlation (every container line moves together on
    a Suez event) that makes a naive cross-sectional t over-reject — the honest
    treatment for a small, correlated cross-section, in the spirit of the
    Kolari-Pynnonen caution. Reports a Student-t p-value AND a bootstrap CI;
    ``significant`` requires BOTH (p<alpha and the CI excludes zero).

    ``basis="insufficient"`` (no verdict) when too few events yield usable
    cross-sections — never a fabricated p-value on an empty/thin surface.
    """

    n_events: int
    n_observations: int            # total (carrier, event) abnormal returns used
    mean_abnormal_return: float    # event-level mean RAW abnormal return (fraction)
    mean_standardized_ar: float    # event-level mean STANDARDIZED AR (BMP units)
    t_stat: float
    p_value: float                 # two-sided
    ci_low: float                  # bootstrap CI on mean_abnormal_return
    ci_high: float
    significant: bool              # p < alpha AND bootstrap CI excludes zero
    basis: str                     # "real" | "insufficient"
    note: str = ""


def event_study_significance(
    prices_by_ticker,
    event_dates,
    *,
    pre: int = 20,
    post: int = 20,
    min_events: int = 3,
    n_boot: int = 2000,
    seed: int = 20260620,
    confidence: float = 0.95,
) -> EventStudySignificance:
    """Test whether the mean abnormal return around disruption events ≠ 0.

    Event-level aggregation of BMP-standardized abnormal returns + a Student-t
    test + a seeded bootstrap percentile CI. Returns ``basis="insufficient"``
    (no verdict) when fewer than ``min_events`` events yield a usable carrier
    cross-section. Deterministic (fixed bootstrap seed). Never raises.
    """
    alpha = 1.0 - float(confidence)
    event_sars: list[float] = []
    event_ars: list[float] = []
    n_obs = 0
    for ed in (event_dates or []):
        try:
            res = event_study(prices_by_ticker, ed, pre=pre, post=post)
        except Exception:
            continue
        sars, ars = [], []
        for r in res.values():
            if (math.isfinite(r.abnormal_return)
                    and math.isfinite(r.estimation_sigma)
                    and r.estimation_sigma > 0.0 and r.n_post > 0):
                se = r.estimation_sigma * math.sqrt(r.n_post)
                if se > 0.0:
                    sars.append(r.abnormal_return / se)
                    ars.append(r.abnormal_return)
        if sars:
            event_sars.append(float(np.mean(sars)))
            event_ars.append(float(np.mean(ars)))
            n_obs += len(sars)

    n = len(event_sars)
    if n < int(min_events):
        return EventStudySignificance(
            n_events=n, n_observations=n_obs, mean_abnormal_return=float("nan"),
            mean_standardized_ar=float("nan"), t_stat=0.0, p_value=1.0,
            ci_low=float("nan"), ci_high=float("nan"), significant=False,
            basis="insufficient",
            note=(f"Only {n} event(s) with a usable carrier cross-section "
                  f"(need >= {min_events}) — significance not evaluated."),
        )

    sar = np.asarray(event_sars, dtype=float)
    ar = np.asarray(event_ars, dtype=float)
    mean_sar = float(sar.mean())
    mean_ar = float(ar.mean())
    sd = float(sar.std(ddof=1))
    if sd > 0.0:
        t_stat = mean_sar / (sd / math.sqrt(n))
        try:
            from scipy.stats import t as _t
            p_value = float(2.0 * _t.sf(abs(t_stat), df=n - 1))
        except Exception:
            p_value = float(math.erfc(abs(t_stat) / math.sqrt(2.0)))
    else:
        t_stat, p_value = 0.0, 1.0

    # Seeded bootstrap percentile CI on the event-level mean RAW abnormal return.
    rng = np.random.default_rng(int(seed))
    boots = np.array([rng.choice(ar, size=n, replace=True).mean()
                      for _ in range(int(n_boot))], dtype=float)
    ci_low = float(np.quantile(boots, alpha / 2.0))
    ci_high = float(np.quantile(boots, 1.0 - alpha / 2.0))

    significant = bool(p_value < alpha and ci_low * ci_high > 0.0)
    return EventStudySignificance(
        n_events=n, n_observations=n_obs, mean_abnormal_return=mean_ar,
        mean_standardized_ar=mean_sar, t_stat=float(t_stat), p_value=p_value,
        ci_low=ci_low, ci_high=ci_high, significant=significant, basis="real",
        note=(f"{n} events x {n_obs} (carrier,event) abnormal returns; event-level "
              f"BMP-standardized t-test + {int(n_boot)}x bootstrap CI "
              f"(cross-carrier correlation absorbed by within-event averaging)."),
    )


# ---------------------------------------------------------------------------
# 3) Lead / lag rolling correlation
# ---------------------------------------------------------------------------

def _to_return_series(series: PriceLike) -> Optional[pd.Series]:
    """Coerce a price input to a clean, date-indexed **return** series.

    Returns the simple pct-change of the cleaned price series, with the leading
    NaN dropped. ``None`` if the input cannot be coerced or is too short.
    """
    ser = _coerce_price_series(series)
    if ser is None or len(ser) < 2:
        return None
    rets = ser.pct_change().dropna()
    if rets.empty:
        return None
    return rets


def rolling_lead_lag_correlation(
    series_a: PriceLike,
    series_b: PriceLike,
    *,
    max_lag: int = 10,
    min_overlap: int = 3,
) -> dict:
    """Lead/lag Pearson correlation of two series' **returns** across lags.

    Both inputs are converted to simple-return series (price pct-change) and
    aligned on their common date index. For each integer lag ``L`` in
    ``[-max_lag, +max_lag]`` we shift ``b`` and correlate.

    **Sign convention (positive lag means A LEADS B).** A positive lag ``L``
    pairs ``a`` at time ``t`` with ``b`` at time ``t + L``: i.e. today's move
    in ``a`` is compared with ``b``'s move ``L`` days *later*, so a high
    correlation at positive ``L`` means **A leads B by L days**. Equivalently
    we correlate ``a`` against ``b.shift(-L)``. Negative ``L`` means B leads A.

    Parameters
    ----------
    series_a, series_b
        Price inputs (Series or ``(dates, closes)``). Converted to returns.
    max_lag
        Largest absolute lag (days) to test. Negative is clamped to ``0`` (then
        only lag 0 is reported). Coerced to ``int``.
    min_overlap
        Minimum number of overlapping (non-NaN) pairs required to compute a
        correlation at a given lag; below this the lag's correlation is
        ``nan``. Floored at ``3`` because Pearson r on < 3 points is undefined
        / unstable.

    Returns
    -------
    dict
        ``{"by_lag": {L: r}, "best_lag": int|None, "best_corr": float,
        "n_overlap": int, "max_lag": int}``. ``by_lag`` maps every tested lag to
        its Pearson r (``nan`` where overlap is insufficient). ``best_lag`` is
        the lag with the largest ``|r|`` among finite entries (ties broken by
        smallest ``|lag|`` then smallest lag); ``None`` if no lag had a finite
        correlation. ``n_overlap`` is the number of aligned return pairs at lag
        0.

    This function never raises on bad input — it returns an all-``nan`` map.

    Caveat on interpretation
    -------------------------
    Correlating two *real* return series is a real measurement. But correlating
    a real return series against a **modeled signal** (SSI / congestion /
    voyage delays) is **ILLUSTRATIVE ONLY**: the modeled signal is a
    current-date snapshot, not a real historical series, so any lead/lag it
    produces is not a measured fact about the past. **Not investment advice.**
    """
    max_lag = max(0, int(max_lag))
    min_overlap = max(3, int(min_overlap))
    lags = list(range(-max_lag, max_lag + 1))

    empty = {
        "by_lag": {L: float("nan") for L in lags},
        "best_lag": None,
        "best_corr": float("nan"),
        "n_overlap": 0,
        "max_lag": max_lag,
    }

    ra = _to_return_series(series_a)
    rb = _to_return_series(series_b)
    if ra is None or rb is None:
        return empty

    # Align on the common date index up front.
    aligned = pd.concat([ra.rename("a"), rb.rename("b")], axis=1, join="inner")
    aligned = aligned.dropna()
    n_overlap = int(len(aligned))
    if n_overlap < min_overlap:
        return {**empty, "n_overlap": n_overlap}

    a = aligned["a"]
    b = aligned["b"]

    by_lag: dict[int, float] = {}
    for L in lags:
        # Positive L: A leads B  →  correlate a_t with b_{t+L}  →  b.shift(-L).
        shifted_b = b.shift(-L)
        pair = pd.concat([a, shifted_b], axis=1).dropna()
        if len(pair) < min_overlap:
            by_lag[L] = float("nan")
            continue
        by_lag[L] = _pearson_r(pair.iloc[:, 0].to_numpy(), pair.iloc[:, 1].to_numpy())

    # Pick best_lag = argmax |r| over finite entries; deterministic tie-break.
    finite = [(L, r) for L, r in by_lag.items() if math.isfinite(r)]
    if not finite:
        return {**empty, "by_lag": by_lag, "n_overlap": n_overlap}

    best_lag, best_corr = min(
        finite, key=lambda kv: (-abs(kv[1]), abs(kv[0]), kv[0])
    )
    return {
        "by_lag": by_lag,
        "best_lag": int(best_lag),
        "best_corr": float(best_corr),
        "n_overlap": n_overlap,
        "max_lag": max_lag,
    }


def _pearson_r(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation of two equal-length arrays, numpy-safe.

    Returns ``nan`` for fewer than 3 finite pairs or when either side is
    constant (zero variance → correlation undefined). Uses ``np.corrcoef`` to
    match the rest of the codebase, but guards the degenerate cases that make
    ``corrcoef`` emit ``nan`` / RuntimeWarnings.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if x.size < 3:
        return float("nan")
    # Zero variance on either side → Pearson r is undefined.
    if np.std(x) == 0.0 or np.std(y) == 0.0:
        return float("nan")
    r = float(np.corrcoef(x, y)[0, 1])
    if not math.isfinite(r):
        return float("nan")
    # Clamp tiny FP excursions outside [-1, 1].
    return float(max(-1.0, min(1.0, r)))


# ---------------------------------------------------------------------------
# 4) UI summary string
# ---------------------------------------------------------------------------

def summarize(
    aggregate: Mapping[str, dict] | None = None,
    *,
    n_events: int | None = None,
    lead_lag: Mapping | None = None,
) -> str:
    """Build a short, honest human-readable summary for the UI.

    The string always states (1) that the event study uses **real prices and
    real event dates**, (2) that it is **descriptive history, not a forecast or
    advice**, and (3) — when a ``lead_lag`` result is passed — flags that any
    correlation against a modeled signal is illustrative only.

    Parameters
    ----------
    aggregate
        Output of :func:`aggregate_event_studies` (``ticker -> stats``). Used
        to surface the average post-event move of a couple of names.
    n_events
        Number of real events the aggregate spanned (for the lede). If omitted,
        inferred from the max ``n_events`` in ``aggregate``.
    lead_lag
        Optional output of :func:`rolling_lead_lag_correlation`; if present its
        ``best_lag`` / ``best_corr`` are mentioned with the illustrative caveat.
    """
    lines: list[str] = []

    if n_events is None and aggregate:
        try:
            n_events = max(int(v.get("n_events", 0)) for v in aggregate.values())
        except (ValueError, TypeError):
            n_events = None

    if n_events:
        lines.append(
            f"Event study over {n_events} real shipping-disruption "
            f"{'event' if n_events == 1 else 'events'}, "
            f"using real historical equity prices and the real event dates."
        )
    else:
        lines.append(
            "Event study using real historical equity prices aligned to the "
            "real dates of past shipping-disruption events."
        )

    if aggregate:
        # Surface the names with the most negative typical abnormal move first.
        ranked = sorted(
            (
                (t, s)
                for t, s in aggregate.items()
                if isinstance(s, Mapping)
                and math.isfinite(float(s.get("mean_abnormal_return", float("nan"))))
            ),
            key=lambda kv: float(kv[1].get("mean_abnormal_return", 0.0)),
        )
        for ticker, stats in ranked[:3]:
            abn = float(stats.get("mean_abnormal_return", float("nan")))
            cum = float(stats.get("mean_cum_return", float("nan")))
            lines.append(
                f"  {ticker}: typical post-event move "
                f"{_pct(cum)} (abnormal {_pct(abn)} vs its pre-event trend)."
            )

    if lead_lag is not None and isinstance(lead_lag, Mapping):
        best_lag = lead_lag.get("best_lag")
        best_corr = lead_lag.get("best_corr", float("nan"))
        if best_lag is not None and math.isfinite(float(best_corr)):
            if int(best_lag) > 0:
                rel = f"the first series leads the second by {int(best_lag)} day(s)"
            elif int(best_lag) < 0:
                rel = f"the second series leads the first by {abs(int(best_lag))} day(s)"
            else:
                rel = "the two series move contemporaneously (no lead/lag)"
            lines.append(
                f"Lead/lag: strongest correlation r={float(best_corr):+.2f} where "
                f"{rel}."
            )
        lines.append(
            "Note: any lead/lag against a MODELED disruption signal "
            "(SSI / congestion / voyage delays) is ILLUSTRATIVE ONLY — that "
            "signal is a current-date snapshot, not a real historical series."
        )

    lines.append(NOT_ADVICE)
    return "\n".join(lines)


def _pct(x: float) -> str:
    """Format a fraction as a signed percent, or ``n/a`` if non-finite."""
    if x is None or not math.isfinite(float(x)):
        return "n/a"
    return f"{float(x) * 100.0:+.1f}%"


# ---------------------------------------------------------------------------
# Real-event helper (the only place that reads the registry)
# ---------------------------------------------------------------------------

def real_event_dates(
    *,
    severity: str | None = None,
    chokepoint: str | None = None,
) -> list[tuple[str, pd.Timestamp]]:
    """Return ``(label, start_date)`` pairs for the REAL historical events.

    Pulls from :mod:`data.historical_events` — the registry of real,
    well-documented past shipping disruptions. The MATH functions in this
    module take dates as plain arguments; this helper is the *only* thing that
    reads the registry, so callers can feed real events straight into
    :func:`event_study` / :func:`aggregate_event_studies` without coupling the
    math to the data module.

    Parameters
    ----------
    severity
        Optional filter (``"severe"`` / ``"major"`` / ``"moderate"``).
    chokepoint
        Optional filter — keep only events whose ``affected_chokepoints``
        include this key (e.g. ``"suez"``, ``"panama"``).

    Returns
    -------
    list[tuple[str, pandas.Timestamp]]
        ``(event name, start_date)`` in the registry's chronological order.
        Events whose ``start_date`` cannot be parsed are dropped. Returns an
        empty list (never raises) if the registry is unavailable.
    """
    try:
        from data.historical_events import EVENTS  # local import keeps math pure
    except Exception:  # pragma: no cover - registry should always import
        return []

    out: list[tuple[str, pd.Timestamp]] = []
    for ev in EVENTS:
        if severity is not None and getattr(ev, "severity", None) != severity:
            continue
        if chokepoint is not None and chokepoint not in (
            getattr(ev, "affected_chokepoints", None) or []
        ):
            continue
        ts = _to_timestamp(getattr(ev, "start_date", None))
        if ts is None:
            continue
        out.append((str(getattr(ev, "name", getattr(ev, "event_id", "?"))), ts))
    return out
