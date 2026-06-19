"""Defining-property tests for processing.sized_book_risk (R255).

The one property that motivates the whole module: a SIGNED long/short book's VaR
must let a short NET against a correlated long. A market-neutral pair (one name
+0.1, a perfectly-correlated name −0.1) shows ~ZERO book VaR; the SAME two names
both +0.1 show clearly additive VaR. A long-only renormalization (Σw = 1) can
never express the cancellation — that is exactly the gap R255 fills.

All panels are hand-built so the correlations are EXACT and deterministic. No
randomness anywhere (house ethos: no rng.normal).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.position_sizer import (
    SizedBook,
    SizedPosition,
    size_from_conviction,
)
from processing.book_pnl import returns_panel
from processing.sized_book_risk import SizedBookRisk, sized_book_var


# ---------------------------------------------------------------------------
# Builders — deterministic, hand-built. No rng.
# ---------------------------------------------------------------------------

def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="D")


def _book_from_weights(weights: dict[str, float], *, mode: str = "vol_target") -> SizedBook:
    """Construct a SizedBook directly from a signed {ticker: weight} map.

    Used where the defining property needs EXACT signed weights (±0.1) that the
    sizer's vol-budget rescale + caps would not preserve. The real SizedBook
    shape is honoured: signed ``SizedPosition.target_weight`` + reconciled
    gross/net so ``.weights()`` returns precisely the intended signed dict.
    """
    positions = [
        SizedPosition(
            ticker=t,
            direction="Bullish" if w >= 0 else "Bearish",
            sign=1 if w >= 0 else -1,
            conviction_score=0.8,
            annual_vol=0.3,
            target_weight=float(w),
        )
        for t, w in weights.items()
    ]
    gross = sum(abs(w) for w in weights.values())
    net = sum(weights.values())
    return SizedBook(
        positions=positions,
        est_book_vol=0.0,
        gross_exposure=round(gross, 6),
        net_exposure=round(net, 6),
        mode=mode,
        used_covariance=False,
        constants={},
        skipped={},
        provenance="hand-built test book",
    )


class _Idea:
    """Minimal duck-typed EquityIdea — size_from_conviction only reads ticker /
    direction / conviction_score via getattr (verified against the real class)."""

    def __init__(self, ticker: str, direction: str, conviction: float):
        self.ticker = ticker
        self.direction = direction
        self.conviction_score = conviction


def _identical_two_col_panel(n: int = 250, daily: float = 0.01) -> pd.DataFrame:
    """Two columns AAA / BBB that are byte-for-byte IDENTICAL → correlation 1.0,
    so a +0.1/−0.1 pair cancels EXACTLY and a +0.1/+0.1 pair is exactly additive.

    A deterministic alternating ±`daily` path (mean ~0, non-degenerate σ). No rng.
    """
    seq = np.array([daily if i % 2 == 0 else -daily for i in range(n)], dtype=float)
    return pd.DataFrame({"AAA": seq, "BBB": seq.copy()}, index=_dates(n))


# ---------------------------------------------------------------------------
# (a) THE defining property — market-neutral pair nets to ~zero; same-sign adds.
# ---------------------------------------------------------------------------

def test_market_neutral_pair_nets_to_near_zero_var():
    panel = _identical_two_col_panel()

    # +0.1 long AAA, −0.1 short a PERFECTLY-correlated BBB → exact hedge.
    neutral = _book_from_weights({"AAA": 0.1, "BBB": -0.1})
    r_neutral = sized_book_var(neutral, panel)

    assert r_neutral.basis == "real"
    # Net exposure is zero, and the book return series is identically zero, so
    # VaR and ES collapse to ~0 (loss-negative convention → min(0, ~0)).
    assert r_neutral.net_exposure == pytest.approx(0.0, abs=1e-12)
    assert r_neutral.var_pct == pytest.approx(0.0, abs=1e-9)
    assert r_neutral.cvar_pct == pytest.approx(0.0, abs=1e-9)


def test_same_sign_pair_has_clearly_additive_var():
    panel = _identical_two_col_panel()

    # SAME two names, both +0.1 long → no cancellation, full additive risk.
    both_long = _book_from_weights({"AAA": 0.1, "BBB": 0.1})
    r_both = sized_book_var(both_long, panel)

    assert r_both.basis == "real"
    assert r_both.net_exposure == pytest.approx(0.2)
    # Real, clearly-non-zero loss (loss-negative → strictly < 0).
    assert r_both.var_pct < -1e-4
    assert r_both.cvar_pct <= r_both.var_pct  # ES at least as deep as VaR


def test_hedge_is_the_property_long_only_cannot_express():
    """Side-by-side: the SAME names, identical |weights|, opposite sign on one
    leg — the signed read sees a hedge (≈0), the same-sign read sees real risk.
    This is the exact gap a Σw=1 long-only renormalization erases."""
    panel = _identical_two_col_panel()

    neutral = sized_book_var(_book_from_weights({"AAA": 0.1, "BBB": -0.1}), panel)
    gross_long = sized_book_var(_book_from_weights({"AAA": 0.1, "BBB": 0.1}), panel)

    # Same gross exposure on both books — only the sign of one leg differs.
    assert neutral.gross_exposure == pytest.approx(gross_long.gross_exposure)
    # Yet the hedged book's tail is vanishing relative to the additive book's.
    assert abs(neutral.var_pct) < 1e-6
    assert abs(gross_long.var_pct) > abs(neutral.var_pct) + 1e-4


def test_partial_hedge_sits_between_neutral_and_additive():
    """A −0.1/+0.05 (net-long, partially hedged) pair on identical columns must
    have a tail strictly between the exact hedge (0) and the additive book."""
    panel = _identical_two_col_panel()

    additive = sized_book_var(_book_from_weights({"AAA": 0.1, "BBB": 0.1}), panel)
    partial = sized_book_var(_book_from_weights({"AAA": 0.1, "BBB": -0.05}), panel)

    assert partial.basis == "real"
    assert 1e-5 < abs(partial.var_pct) < abs(additive.var_pct)
    # Net exposure on the partial book = 0.1 - 0.05 = 0.05.
    assert partial.net_exposure == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# (b) Exposure fields reconcile to the SizedBook.
# ---------------------------------------------------------------------------

def test_exposures_reconcile_to_sized_book():
    panel = _identical_two_col_panel()
    book = _book_from_weights({"AAA": 0.1, "BBB": -0.04})
    res = sized_book_var(book, panel)

    assert res.n_names == 2
    # gross = Σ|w|, net = Σw, net = long + short, gross = long - short.
    assert res.gross_exposure == pytest.approx(0.14)
    assert res.net_exposure == pytest.approx(0.06)
    assert res.long_exposure == pytest.approx(0.10)
    assert res.short_exposure == pytest.approx(-0.04)
    assert res.net_exposure == pytest.approx(res.long_exposure + res.short_exposure)
    assert res.gross_exposure == pytest.approx(res.long_exposure - res.short_exposure)
    # The signed weights actually used match the book over the scored names.
    assert res.weights == {"AAA": 0.1, "BBB": -0.04}


def test_reconciles_against_a_real_size_from_conviction_book():
    """Build the SizedBook the production way (size_from_conviction over real
    return-vol) and confirm sized_book_var's exposures match that SizedBook's own
    gross/net exactly over the scored names — incl. a genuine short leg."""
    # 3 tickers with real, distinct return columns so the sizer keeps them.
    n = 250
    rng_free = np.array([0.01 if i % 2 == 0 else -0.011 for i in range(n)], dtype=float)
    panel = pd.DataFrame(
        {
            "LONGA": rng_free,
            "LONGB": rng_free * 0.9,
            "SHRTC": rng_free * 1.1,
        },
        index=_dates(n),
    )
    ideas = [
        _Idea("LONGA", "Bullish", 0.9),
        _Idea("LONGB", "Bullish", 0.6),
        _Idea("SHRTC", "Bearish", 0.8),   # the short leg
    ]
    book = size_from_conviction(ideas, panel)
    # Sanity: the sizer produced a real long/short book (a short is present).
    assert any(w < 0 for w in book.weights().values())

    res = sized_book_var(book, panel)
    assert res.basis == "real"
    assert res.n_names == len(book.weights())
    # Exposures reconcile to the SizedBook's OWN gross/net (the source of truth).
    assert res.gross_exposure == pytest.approx(book.gross_exposure, abs=1e-6)
    assert res.net_exposure == pytest.approx(book.net_exposure, abs=1e-6)
    # And the short shows up as negative short_exposure on the risk read.
    assert res.short_exposure < 0.0


def test_real_panel_path_via_returns_panel_builder():
    """End-to-end with processing.book_pnl.returns_panel as the real panel
    source (log-returns over look-ahead-free closes), confirming the intended
    wiring works, not just a hand-rolled frame."""
    n = 200
    base = 100.0
    # Two deterministic close paths → real log-returns panel.
    closeA = pd.Series(
        [base * (1.01 ** i) for i in range(n)], index=_dates(n)
    )
    closeB = pd.Series(
        [base * (1.008 ** i) for i in range(n)], index=_dates(n)
    )
    stock_data = {
        "AAA": pd.DataFrame({"date": closeA.index, "close": closeA.values}),
        "BBB": pd.DataFrame({"date": closeB.index, "close": closeB.values}),
    }
    panel = returns_panel(stock_data, ["AAA", "BBB"], min_obs=60)
    assert not panel.empty and list(panel.columns) == ["AAA", "BBB"]

    book = _book_from_weights({"AAA": 0.1, "BBB": -0.1})
    res = sized_book_var(book, panel)
    assert res.basis == "real"
    assert res.n_names == 2
    assert res.n_observations > 0


# ---------------------------------------------------------------------------
# (c) Honest empty — no crash, no fabricated number.
# ---------------------------------------------------------------------------

def test_empty_book_is_honest_empty():
    panel = _identical_two_col_panel()
    res = sized_book_var(_book_from_weights({}), panel)
    assert res.basis == "empty"
    assert res.var_pct == 0.0 and res.cvar_pct == 0.0
    assert res.n_names == 0 and res.n_observations == 0
    assert res.gross_exposure == 0.0 and res.net_exposure == 0.0


def test_none_book_is_honest_empty():
    res = sized_book_var(None, _identical_two_col_panel())
    assert res.basis == "empty"
    assert res.var_pct == 0.0 and res.n_observations == 0


def test_empty_panel_is_honest_empty():
    book = _book_from_weights({"AAA": 0.1, "BBB": -0.1})
    res = sized_book_var(book, pd.DataFrame())
    assert res.basis == "empty"
    assert res.var_pct == 0.0 and res.cvar_pct == 0.0
    assert res.n_observations == 0


def test_none_panel_is_honest_empty():
    book = _book_from_weights({"AAA": 0.1})
    res = sized_book_var(book, None)
    assert res.basis == "empty"
    assert res.var_pct == 0.0


def test_no_overlap_is_honest_empty():
    panel = _identical_two_col_panel()  # columns AAA / BBB
    book = _book_from_weights({"ZZZ": 0.2, "YYY": -0.1})  # disjoint tickers
    res = sized_book_var(book, panel)
    assert res.basis == "empty"
    assert res.var_pct == 0.0 and res.n_names == 0


def test_too_few_observations_is_honest_empty_not_a_zero_var():
    """risk_lab needs >= 10 obs; fewer must yield basis='empty', NOT a real-
    looking zero VaR. (Honesty: a zero must never masquerade as a measurement.)"""
    short_panel = _identical_two_col_panel(n=6)  # 6 < 10
    book = _book_from_weights({"AAA": 0.1, "BBB": 0.1})
    res = sized_book_var(book, short_panel)
    assert res.basis == "empty"
    assert res.n_observations == 0
    assert res.var_pct == 0.0


# ---------------------------------------------------------------------------
# (d) Determinism + method/horizon plumbing + sign convention.
# ---------------------------------------------------------------------------

def test_deterministic_repeated_calls_are_identical():
    panel = _identical_two_col_panel()
    book = _book_from_weights({"AAA": 0.1, "BBB": -0.04})
    a = sized_book_var(book, panel)
    b = sized_book_var(book, panel)
    assert a == b  # frozen dataclass equality → bit-for-bit identical


def test_var_and_cvar_are_loss_negative_and_es_at_least_as_deep():
    panel = _identical_two_col_panel()
    book = _book_from_weights({"AAA": 0.1, "BBB": 0.05})
    res = sized_book_var(book, panel)
    assert res.var_pct <= 0.0
    assert res.cvar_pct <= res.var_pct  # ES is at or below VaR (deeper loss)


def test_horizon_scaling_increases_tail_by_sqrt():
    """A 4-day horizon scales the 1-day VaR by sqrt(4)=2 (risk_lab convention)."""
    panel = _identical_two_col_panel()
    book = _book_from_weights({"AAA": 0.1, "BBB": 0.1})
    one = sized_book_var(book, panel, horizon_days=1)
    four = sized_book_var(book, panel, horizon_days=4)
    assert one.var_pct < 0.0
    # ×√4 = ×2 exactly in the math; abs tol covers VaRResult's 6-dp rounding
    # (round(2x,6) vs 2·round(x,6) can differ by 1e-6).
    assert four.var_pct == pytest.approx(one.var_pct * 2.0, abs=2e-6)


def test_parametric_method_is_accepted_and_real():
    panel = _identical_two_col_panel()
    book = _book_from_weights({"AAA": 0.1, "BBB": 0.1})
    res = sized_book_var(book, panel, method="parametric")
    assert res.method == "parametric"
    assert res.basis == "real"
    assert res.var_pct < 0.0


def test_unknown_method_falls_back_to_default():
    # Invalid method normalizes to the module default (ewma, the live method).
    panel = _identical_two_col_panel()
    book = _book_from_weights({"AAA": 0.1, "BBB": 0.1})
    res = sized_book_var(book, panel, method="bogus")
    assert res.method == "ewma"
    assert res.basis == "real"


def test_default_method_is_ewma():
    panel = _identical_two_col_panel()
    book = _book_from_weights({"AAA": 0.1, "BBB": 0.1})
    res = sized_book_var(book, panel)
    assert res.method == "ewma" and res.basis == "real" and res.var_pct < 0.0


def test_returns_a_frozen_sized_book_risk_instance():
    panel = _identical_two_col_panel()
    res = sized_book_var(_book_from_weights({"AAA": 0.1}), panel)
    assert isinstance(res, SizedBookRisk)
    with pytest.raises(Exception):
        res.var_pct = -0.5  # frozen
