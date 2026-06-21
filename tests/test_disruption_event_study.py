"""Tests for processing.disruption_event_study.

Strategy: build synthetic price series with a *known* structure (a flat path
that steps down by an exact percent at a known date; a copy of a series shifted
by a known lag) and assert the module recovers that structure within tolerance.
Everything here runs fully offline — no yfinance, no network — because the
compute functions take price/return data as plain arguments.

The lead/lag sign convention under test is the one the module documents:
**positive lag means series A LEADS series B** (a_t correlated with b_{t+L}).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from processing.disruption_event_study import (
    NOT_ADVICE,
    EventStudyResult,
    aggregate_event_studies,
    event_study,
    real_event_dates,
    rolling_lead_lag_correlation,
    summarize,
)


# ─── Fixtures / helpers ──────────────────────────────────────────────────────

def _bdays(start: str, n: int) -> pd.DatetimeIndex:
    """`n` business days starting at `start` (so the index looks like a real
    trading calendar — weekends skipped)."""
    return pd.bdate_range(start=start, periods=n)


def _flat_then_step(
    *,
    start: str,
    n_pre: int,
    n_post: int,
    level: float = 100.0,
    step_pct: float = -0.10,
):
    """Build a flat price series that steps by `step_pct` AFTER the pivot.

    Returns ``(series, event_date)`` where ``event_date`` is the last flat
    trading day (the event anchor). The first post-pivot day is ``level *
    (1 + step_pct)`` and then flat again — so the cumulative window return from
    the anchor is exactly ``step_pct`` and, against a flat (zero-drift)
    baseline, the abnormal return is also ``step_pct``.
    """
    idx = _bdays(start, n_pre + n_post)
    values = np.empty(n_pre + n_post, dtype=float)
    values[:n_pre] = level
    values[n_pre:] = level * (1.0 + step_pct)
    ser = pd.Series(values, index=idx)
    event_date = idx[n_pre - 1]  # last flat day == the anchor
    return ser, event_date


# ─── 1) Step-down series: recover cumulative + negative abnormal return ──────

def test_event_study_recovers_known_step_down():
    ser, event_date = _flat_then_step(
        start="2021-01-01", n_pre=20, n_post=20, level=100.0, step_pct=-0.10
    )
    res = event_study({"SHIP": ser}, event_date, pre=20, post=20)

    assert "SHIP" in res
    r = res["SHIP"]
    assert isinstance(r, EventStudyResult)

    # Anchor is the last flat close at 100.
    assert r.event_close == pytest.approx(100.0)
    # Cumulative window return from the anchor to the end == the step (-10%).
    assert r.cum_return_window == pytest.approx(-0.10, abs=1e-9)
    # Baseline drift over the flat pre-window is zero.
    assert r.baseline_mean_daily_return == pytest.approx(0.0, abs=1e-12)
    # With zero baseline drift, the abnormal return equals the realised move.
    assert r.abnormal_return == pytest.approx(-0.10, abs=1e-9)
    assert r.abnormal_return < 0.0
    # The window fell 10% and never rose → drawdown ~ -10%, run-up 0.
    assert r.max_drawdown == pytest.approx(-0.10, abs=1e-9)
    assert r.max_runup == pytest.approx(0.0, abs=1e-12)
    # Counts: 19 pre-returns' worth of history before the anchor, 20 after.
    assert r.n_pre == 19
    assert r.n_post == 20


def test_event_study_step_up_is_positive_abnormal():
    ser, event_date = _flat_then_step(
        start="2021-01-01", n_pre=15, n_post=15, level=50.0, step_pct=+0.08
    )
    r = event_study({"UP": ser}, event_date, pre=15, post=15)["UP"]
    assert r.cum_return_window == pytest.approx(0.08, abs=1e-9)
    assert r.abnormal_return == pytest.approx(0.08, abs=1e-9)
    assert r.abnormal_return > 0.0
    assert r.max_runup == pytest.approx(0.08, abs=1e-9)
    assert r.max_drawdown == pytest.approx(0.0, abs=1e-12)


# ─── 2) Flat series → ~zero abnormal return ──────────────────────────────────

def test_flat_series_zero_abnormal_return():
    idx = _bdays("2022-03-01", 41)
    ser = pd.Series(np.full(41, 123.45), index=idx)
    event_date = idx[20]
    r = event_study({"FLAT": ser}, event_date, pre=20, post=20)["FLAT"]
    assert r.cum_return_window == pytest.approx(0.0, abs=1e-12)
    assert r.abnormal_return == pytest.approx(0.0, abs=1e-12)
    assert r.baseline_mean_daily_return == pytest.approx(0.0, abs=1e-12)
    assert r.max_drawdown == pytest.approx(0.0, abs=1e-12)
    assert r.max_runup == pytest.approx(0.0, abs=1e-12)


def test_event_study_subtracts_pre_event_drift():
    # A steadily *rising* series: the post move is positive, but so is the
    # baseline drift, so the ABNORMAL return should be ~0 (move was expected).
    idx = _bdays("2022-01-03", 41)
    daily = 0.01
    values = 100.0 * (1.0 + daily) ** np.arange(41)
    ser = pd.Series(values, index=idx)
    event_date = idx[20]
    r = event_study({"DRIFT": ser}, event_date, pre=20, post=20)["DRIFT"]
    assert r.baseline_mean_daily_return == pytest.approx(daily, abs=1e-9)
    # cum return over 20 post days at +1%/day is large and positive...
    assert r.cum_return_window > 0.2
    # ...but it's fully explained by the pre-event drift → abnormal ~ 0.
    assert r.abnormal_return == pytest.approx(0.0, abs=1e-6)


# ─── 3) Lead/lag: recover a known shift, assert the documented sign ──────────

def test_lead_lag_recovers_known_positive_lag():
    # b is a's return path shifted LATER by k days → a leads b by k.
    # Convention under test: positive lag == A leads B. So best_lag == +k.
    rng = np.random.default_rng(0)
    idx = _bdays("2020-01-01", 200)
    # Random-walk price for A so its returns are non-degenerate.
    a_ret = rng.normal(0.0, 0.01, size=200)
    a_price = 100.0 * np.cumprod(1.0 + a_ret)
    a = pd.Series(a_price, index=idx)

    k = 3
    # Build B's price so that B's return on day t equals A's return on day t-k
    # (B repeats A's move k days later → A leads B by k).
    b_ret = np.concatenate([np.zeros(k), a_ret[: 200 - k]])
    # Reconstruct a price path from those returns (first point arbitrary).
    b_price = 100.0 * np.cumprod(1.0 + b_ret)
    b = pd.Series(b_price, index=idx)

    out = rolling_lead_lag_correlation(a, b, max_lag=10)
    assert out["best_lag"] == k                       # documented sign: A leads B by +k
    assert out["best_corr"] == pytest.approx(1.0, abs=1e-6)
    # And the correlation at the wrong-sign lag should be far from 1.
    assert out["by_lag"][-k] < 0.9


def test_lead_lag_negative_when_b_leads_a():
    # Mirror image: make A repeat B's move k days later → B leads A → best_lag<0.
    rng = np.random.default_rng(7)
    idx = _bdays("2020-01-01", 180)
    b_ret = rng.normal(0.0, 0.012, size=180)
    b_price = 100.0 * np.cumprod(1.0 + b_ret)
    b = pd.Series(b_price, index=idx)
    k = 4
    a_ret = np.concatenate([np.zeros(k), b_ret[: 180 - k]])
    a_price = 100.0 * np.cumprod(1.0 + a_ret)
    a = pd.Series(a_price, index=idx)

    out = rolling_lead_lag_correlation(a, b, max_lag=10)
    assert out["best_lag"] == -k
    assert out["best_corr"] == pytest.approx(1.0, abs=1e-6)


def test_lead_lag_contemporaneous_is_zero_lag():
    rng = np.random.default_rng(11)
    idx = _bdays("2020-01-01", 120)
    ret = rng.normal(0.0, 0.01, size=120)
    price = 100.0 * np.cumprod(1.0 + ret)
    a = pd.Series(price, index=idx)
    b = pd.Series(price.copy(), index=idx)  # identical → best lag is 0
    out = rolling_lead_lag_correlation(a, b, max_lag=8)
    assert out["best_lag"] == 0
    assert out["best_corr"] == pytest.approx(1.0, abs=1e-9)


# ─── 4) Robustness: short / empty / missing data never raises ────────────────

def test_event_study_skips_insufficient_and_missing_data():
    idx_ok = _bdays("2021-06-01", 41)
    good = pd.Series(np.full(41, 10.0), index=idx_ok)
    short = pd.Series([10.0], index=[pd.Timestamp("2021-06-15")])
    empty = pd.Series([], dtype=float)

    prices = {
        "GOOD": good,
        "SHORT": short,            # one point → skipped
        "EMPTY": empty,            # empty → skipped
        "NONE": None,              # no data → skipped
        "JUNK": "not-a-series",    # unparseable → skipped
    }
    res = event_study(prices, idx_ok[20], pre=20, post=20)
    assert set(res.keys()) == {"GOOD"}


def test_event_study_event_before_all_data_returns_empty():
    idx = _bdays("2023-01-02", 30)
    ser = pd.Series(np.full(30, 5.0), index=idx)
    # Event date strictly before the first observation → no anchor → skipped.
    res = event_study({"X": ser}, "2020-01-01", pre=10, post=10)
    assert res == {}


def test_event_study_bad_event_date_returns_empty():
    idx = _bdays("2023-01-02", 30)
    ser = pd.Series(np.full(30, 5.0), index=idx)
    assert event_study({"X": ser}, "not-a-date", pre=5, post=5) == {}
    assert event_study({"X": ser}, None, pre=5, post=5) == {}


def test_event_study_accepts_dates_closes_tuple():
    idx = _bdays("2021-01-01", 41)
    closes = np.full(41, 80.0)
    closes[20:] = 80.0 * 0.95  # -5% after the anchor
    prices = {"TUP": (list(idx), list(closes))}
    r = event_study(prices, idx[19], pre=10, post=10)["TUP"]
    assert r.cum_return_window == pytest.approx(-0.05, abs=1e-9)


def test_lead_lag_short_and_empty_return_nan():
    # Fewer than min_overlap points → all-nan map, best_lag None.
    a = pd.Series([100.0, 101.0], index=_bdays("2021-01-01", 2))
    b = pd.Series([100.0, 99.0], index=_bdays("2021-01-01", 2))
    out = rolling_lead_lag_correlation(a, b, max_lag=5)
    assert out["best_lag"] is None
    assert math.isnan(out["best_corr"])
    assert all(math.isnan(v) for v in out["by_lag"].values())

    empty = pd.Series([], dtype=float)
    out2 = rolling_lead_lag_correlation(empty, empty, max_lag=5)
    assert out2["best_lag"] is None
    assert out2["n_overlap"] == 0


def test_lead_lag_constant_series_returns_nan():
    # Zero variance on one side → Pearson undefined → nan everywhere.
    idx = _bdays("2021-01-01", 60)
    flat = pd.Series(np.full(60, 50.0), index=idx)
    moving = pd.Series(50.0 + np.arange(60) * 0.1, index=idx)
    out = rolling_lead_lag_correlation(flat, moving, max_lag=5)
    assert out["best_lag"] is None
    assert all(math.isnan(v) for v in out["by_lag"].values())


# ─── 5) Aggregate over multiple events averages correctly ────────────────────

def test_aggregate_averages_two_events_by_hand():
    # One ticker, two events. Around event 1 it drops 10%, around event 2 it
    # drops 20%. Both have flat (zero-drift) baselines, so abnormal == cum.
    # Hand-built so the mean cum/abnormal == -0.15 and means are exact.
    idx = _bdays("2021-01-01", 80)
    values = np.full(80, 100.0)
    # Event 1 anchor at position 19, drop 10% from position 20..39.
    values[20:40] = 100.0 * 0.90
    # Recover to 100 at 40, then Event 2 anchor at position 59, drop 20%.
    values[40:60] = 100.0
    values[60:80] = 100.0 * 0.80
    ser = pd.Series(values, index=idx)

    ev1 = idx[19]
    ev2 = idx[59]
    agg = aggregate_event_studies({"AAA": ser}, [ev1, ev2], pre=10, post=10)

    assert "AAA" in agg
    stats = agg["AAA"]
    assert stats["n_events"] == 2
    assert stats["mean_cum_return"] == pytest.approx((-0.10 + -0.20) / 2.0, abs=1e-9)
    assert stats["mean_abnormal_return"] == pytest.approx(-0.15, abs=1e-9)
    assert stats["mean_max_drawdown"] == pytest.approx((-0.10 + -0.20) / 2.0, abs=1e-9)


def test_aggregate_empty_events_returns_empty():
    idx = _bdays("2021-01-01", 41)
    ser = pd.Series(np.full(41, 10.0), index=idx)
    assert aggregate_event_studies({"A": ser}, []) == {}
    assert aggregate_event_studies({"A": ser}, None) == {}


# ─── 6) summarize() is honest ────────────────────────────────────────────────

def test_summarize_states_real_data_and_not_advice():
    agg = {
        "AAA": {
            "n_events": 2,
            "mean_cum_return": -0.12,
            "mean_abnormal_return": -0.09,
            "mean_max_drawdown": -0.15,
            "mean_max_runup": 0.02,
        }
    }
    text = summarize(agg, n_events=2)
    low = text.lower()
    assert "real" in low                       # states real prices / real dates
    assert "event study" in low
    assert NOT_ADVICE in text                   # explicit not-advice line
    assert "advice" in low
    assert "AAA" in text


def test_summarize_flags_illustrative_modeled_signal():
    ll = {"best_lag": 3, "best_corr": 0.8, "by_lag": {}, "n_overlap": 50, "max_lag": 10}
    text = summarize(None, n_events=1, lead_lag=ll)
    low = text.lower()
    assert "illustrative" in low                # modeled-signal caveat present
    assert "snapshot" in low
    assert "leads the second by 3" in low       # documented positive-lag wording
    assert NOT_ADVICE in text


def test_summarize_empty_inputs_still_honest():
    text = summarize()
    assert NOT_ADVICE in text
    assert "real" in text.lower()


# ─── 7) real_event_dates() pulls the registry but the math takes args ────────

def test_real_event_dates_pulls_known_events():
    pairs = real_event_dates()
    assert pairs, "expected the historical-events registry to be populated"
    labels = " ".join(label.lower() for label, _ in pairs)
    # The registry's flagship events should be present.
    assert "suez" in labels
    assert "red sea" in labels or "houthi" in labels
    assert "panama" in labels
    # Each date is a real Timestamp.
    assert all(isinstance(ts, pd.Timestamp) for _, ts in pairs)


def test_real_event_dates_severity_filter():
    severe = real_event_dates(severity="severe")
    allev = real_event_dates()
    assert 0 < len(severe) <= len(allev)


def test_real_event_dates_feed_into_event_study():
    # End-to-end-ish (still offline): build a synthetic price series spanning a
    # real event date and confirm the registry date flows into the math.
    pairs = real_event_dates(chokepoint="suez")
    assert pairs
    _, suez_ts = pairs[0]
    idx = pd.bdate_range(end=suez_ts + pd.Timedelta(days=40), periods=60)
    ser = pd.Series(np.full(60, 30.0), index=idx)
    res = event_study({"ZIM": ser}, suez_ts, pre=10, post=10)
    # Flat series → zero abnormal return, but the ticker is processed (not skipped).
    assert "ZIM" in res
    assert res["ZIM"].abnormal_return == pytest.approx(0.0, abs=1e-12)


def test_abnormal_return_uses_geometric_baseline_no_phantom_on_volatile_flat() -> None:
    """Bug-hunt 2026-06-01: a volatile-but-net-flat pre-window followed by a flat
    post-window must yield ~0 abnormal return — an arithmetic-mean baseline
    carried a Jensen's-gap that manufactured a phantom negative (~-9.5%)."""
    pre = [100.0]
    for k in range(20):                       # alternating +10% / -10% → net ~flat
        pre.append(pre[-1] * (1.10 if k % 2 == 0 else 1.0 / 1.10))
    prices = pre + [pre[-1]] * 20             # dead-flat post-window
    dates = pd.bdate_range("2021-01-01", periods=len(prices))
    ser = pd.Series(prices, index=dates)
    event_ts = dates[len(pre) - 1]            # anchor at the end of the pre-window
    res = event_study({"X": ser}, event_ts, pre=20, post=20)["X"]
    assert abs(res.abnormal_return) < 0.01, f"phantom abnormal return: {res.abnormal_return}"


# ── S8: event_study_significance (inferential, cross-correlation-aware) ───────

from processing.disruption_event_study import (  # noqa: E402
    EventStudySignificance,
    event_study_significance,
)


def _build_events(n_events, jump, *, n_tickers=4, seed=0, spacing=120, pad=120):
    """Per-ticker business-day price series (gentle random walk) with a step of
    ``jump`` applied STRICTLY AFTER each event position, so the post-window
    captures it while the pre-window stays flat (abnormal_return ~ jump).
    Returns (prices_by_ticker, event_dates) with events on exact business days."""
    rng = np.random.default_rng(seed)
    total = pad * 2 + spacing * (n_events - 1) + 1
    idx = pd.bdate_range("2022-01-03", periods=total)
    pos = [pad + spacing * i for i in range(n_events)]
    eds = [idx[p] for p in pos]
    prices = {}
    for k in range(n_tickers):
        s = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.008, len(idx))),
                      index=idx)
        for p in pos:
            s.iloc[p + 1:] = s.iloc[p + 1:] * (1.0 + jump)   # step AFTER the anchor
        prices[f"T{k}"] = s
    return prices, eds


def test_significance_detects_a_planted_abnormal_move():
    prices, eds = _build_events(5, jump=0.10, n_tickers=4, seed=1)
    sig = event_study_significance(prices, eds, pre=25, post=25)
    assert isinstance(sig, EventStudySignificance)
    assert sig.basis == "real" and sig.n_events == 5
    assert sig.mean_abnormal_return > 0.05        # ~ the planted +10% (minus drift)
    assert sig.significant and sig.p_value < 0.05
    assert sig.ci_low > 0.0                        # CI excludes zero, correct side


def test_no_signal_is_not_significant():
    prices, eds = _build_events(5, jump=0.0, n_tickers=4, seed=2)
    sig = event_study_significance(prices, eds, pre=25, post=25)
    assert sig.basis == "real" and not sig.significant   # noise -> no fabricated edge


def test_insufficient_events_is_honest_not_fabricated():
    prices, eds = _build_events(2, jump=0.10, seed=3)
    sig = event_study_significance(prices, eds, pre=25, post=25, min_events=3)
    assert sig.basis == "insufficient" and not sig.significant
    assert math.isnan(sig.mean_abnormal_return)


def test_significance_is_deterministic():
    prices, eds = _build_events(5, jump=0.06, seed=4)
    a = event_study_significance(prices, eds, pre=25, post=25)
    b = event_study_significance(prices, eds, pre=25, post=25)
    assert (a.t_stat, a.p_value, a.ci_low, a.ci_high) == \
           (b.t_stat, b.p_value, b.ci_low, b.ci_high)


def test_estimation_sigma_is_populated():
    prices, eds = _build_events(1, jump=0.0, seed=5)
    res = event_study(prices, eds[0], pre=25, post=25)
    assert res and all(math.isfinite(r.estimation_sigma) and r.estimation_sigma > 0
                       for r in res.values())
