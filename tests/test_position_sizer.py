"""Tests for engine.position_sizer — conviction-to-weight sizing (R031).

Defining properties:
  * vol-target gives a bigger weight to the higher-conviction / lower-vol name;
  * the book-vol estimate ≈ ``book_vol_budget`` when the caps don't bind;
  * Bearish → negative weight, Neutral → no position;
  * per-name cap + gross cap are enforced;
  * fractional-Kelly is monotonic in conviction;
  * empty ideas / empty / None returns → empty book, no crash;
  * deterministic.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from engine.position_sizer import (
    SizedBook,
    SizedPosition,
    size_from_conviction,
)


# ── fixtures ────────────────────────────────────────────────────────────────

def _idea(ticker: str, direction: str, conviction: float):
    """Minimal EquityIdea-like object the sizer reads via getattr."""
    return SimpleNamespace(
        ticker=ticker, direction=direction, conviction_score=conviction
    )


def _panel(vols: dict[str, float], *, n: int = 300, seed: int = 0) -> pd.DataFrame:
    """Deterministic daily-returns panel with a prescribed per-name daily σ."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    data = {t: rng.normal(0.0, sd, n) for t, sd in vols.items()}
    return pd.DataFrame(data, index=idx)


# ── vol-target: inverse-vol × conviction tilt ───────────────────────────────

def test_vol_target_lower_vol_gets_more_weight_at_equal_conviction() -> None:
    # AAA low vol, BBB high vol, identical conviction → AAA bigger.
    df = _panel({"AAA": 0.008, "BBB": 0.030})
    ideas = [_idea("AAA", "Bullish", 0.8), _idea("BBB", "Bullish", 0.8)]
    book = size_from_conviction(
        ideas, df, book_vol_budget=0.04, max_weight=0.40, gross_cap=2.0,
    )
    w = book.weights()
    assert w["AAA"] > w["BBB"] > 0.0


def test_vol_target_higher_conviction_gets_more_weight_at_equal_vol() -> None:
    # Same vol, different conviction → higher conviction bigger.
    df = _panel({"AAA": 0.015, "BBB": 0.015})
    ideas = [_idea("AAA", "Bullish", 0.9), _idea("BBB", "Bullish", 0.3)]
    book = size_from_conviction(
        ideas, df, book_vol_budget=0.04, max_weight=0.40, gross_cap=2.0,
    )
    w = book.weights()
    assert w["AAA"] > w["BBB"] > 0.0


def test_vol_target_book_vol_hits_budget_when_caps_dont_bind() -> None:
    # Loose caps so the single rescale scalar is the only thing setting level →
    # the realized book-vol estimate should sit right at the budget.
    df = _panel({"AAA": 0.008, "BBB": 0.030, "CCC": 0.015})
    ideas = [
        _idea("AAA", "Bullish", 0.9),
        _idea("BBB", "Bullish", 0.7),
        _idea("CCC", "Bearish", 0.6),
    ]
    budget = 0.04
    book = size_from_conviction(
        ideas, df, book_vol_budget=budget, max_weight=0.50, gross_cap=3.0,
    )
    assert book.est_book_vol == pytest.approx(budget, abs=0.005)


# ── direction sign ──────────────────────────────────────────────────────────

def test_bearish_is_negative_neutral_is_skipped() -> None:
    df = _panel({"AAA": 0.012, "BBB": 0.012})
    ideas = [
        _idea("AAA", "Bearish", 0.7),
        _idea("BBB", "Bullish", 0.7),
        _idea("CCC", "Neutral", 0.9),  # never sized
    ]
    book = size_from_conviction(ideas, df, max_weight=0.40, gross_cap=2.0)
    w = book.weights()
    assert w["AAA"] < 0.0
    assert w["BBB"] > 0.0
    assert "CCC" not in w
    assert book.skipped.get("CCC", "").startswith("Neutral")


def test_neutral_does_not_consume_gross() -> None:
    df = _panel({"AAA": 0.012})
    only_neutral = [_idea("AAA", "Neutral", 0.9)]
    book = size_from_conviction(only_neutral, df)
    assert book.positions == []
    assert book.gross_exposure == 0.0


# ── caps ────────────────────────────────────────────────────────────────────

def test_per_name_cap_enforced() -> None:
    # Tiny budget would blow weights huge; max_weight must clamp each name.
    df = _panel({"AAA": 0.008, "BBB": 0.010})
    ideas = [_idea("AAA", "Bullish", 1.0), _idea("BBB", "Bullish", 1.0)]
    book = size_from_conviction(
        ideas, df, book_vol_budget=5.0, max_weight=0.15, gross_cap=2.0,
    )
    assert all(abs(p.target_weight) <= 0.15 + 1e-9 for p in book.positions)


def test_gross_cap_enforced() -> None:
    df = _panel({"AAA": 0.008, "BBB": 0.010, "CCC": 0.012})
    ideas = [
        _idea("AAA", "Bullish", 1.0),
        _idea("BBB", "Bullish", 1.0),
        _idea("CCC", "Bearish", 1.0),
    ]
    book = size_from_conviction(
        ideas, df, book_vol_budget=5.0, max_weight=0.15, gross_cap=0.30,
    )
    gross = sum(abs(p.target_weight) for p in book.positions)
    assert gross <= 0.30 + 1e-9
    assert book.gross_exposure == pytest.approx(gross, abs=1e-6)


def test_net_exposure_is_signed_sum() -> None:
    df = _panel({"AAA": 0.012, "BBB": 0.012})
    ideas = [_idea("AAA", "Bullish", 0.6), _idea("BBB", "Bearish", 0.6)]
    book = size_from_conviction(ideas, df, max_weight=0.40, gross_cap=2.0)
    w = book.weights()
    assert book.net_exposure == pytest.approx(w["AAA"] + w["BBB"], abs=1e-6)


# ── fractional Kelly ────────────────────────────────────────────────────────

def test_kelly_monotonic_in_conviction() -> None:
    df = _panel({"AAA": 0.015, "BBB": 0.015})
    low = size_from_conviction(
        [_idea("AAA", "Bullish", 0.3), _idea("BBB", "Bullish", 0.3)],
        df, mode="kelly", max_weight=1.0, gross_cap=10.0,
    )
    high = size_from_conviction(
        [_idea("AAA", "Bullish", 0.9), _idea("BBB", "Bullish", 0.9)],
        df, mode="kelly", max_weight=1.0, gross_cap=10.0,
    )
    assert high.weights()["AAA"] > low.weights()["AAA"] > 0.0


def test_kelly_lower_vol_gets_more_weight() -> None:
    # f* = edge/σ² → lower σ, bigger weight at equal conviction.
    df = _panel({"AAA": 0.008, "BBB": 0.030})
    book = size_from_conviction(
        [_idea("AAA", "Bullish", 0.7), _idea("BBB", "Bullish", 0.7)],
        df, mode="kelly", max_weight=1.0, gross_cap=10.0,
    )
    w = book.weights()
    assert w["AAA"] > w["BBB"] > 0.0


def test_kelly_fraction_scales_weights() -> None:
    df = _panel({"AAA": 0.015, "BBB": 0.015})
    ideas = [_idea("AAA", "Bullish", 0.8), _idea("BBB", "Bullish", 0.8)]
    quarter = size_from_conviction(
        ideas, df, mode="kelly", kelly_fraction=0.25, max_weight=1.0, gross_cap=10.0,
    )
    half = size_from_conviction(
        ideas, df, mode="kelly", kelly_fraction=0.50, max_weight=1.0, gross_cap=10.0,
    )
    # Half-Kelly = 2× quarter-Kelly before any cap binds (weights are rounded
    # to 6dp at the boundary, so allow a 6dp absolute slack on the doubling).
    assert half.weights()["AAA"] == pytest.approx(
        2.0 * quarter.weights()["AAA"], abs=2e-6
    )


# ── no real vol → skip, never fabricate ─────────────────────────────────────

def test_ticker_with_no_real_vol_is_skipped_not_fabricated() -> None:
    df = _panel({"AAA": 0.012})  # only AAA has history
    ideas = [_idea("AAA", "Bullish", 0.7), _idea("ZZZ", "Bullish", 0.9)]
    book = size_from_conviction(ideas, df, max_weight=0.40, gross_cap=2.0)
    w = book.weights()
    assert "AAA" in w
    assert "ZZZ" not in w
    assert "no real return vol" in book.skipped.get("ZZZ", "").lower()


def test_degenerate_flat_vol_is_skipped() -> None:
    # A name with two identical flat closes has σ ≈ 0 → must be skipped.
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    df = pd.DataFrame(
        {"AAA": np.random.default_rng(1).normal(0, 0.012, 300),
         "FLAT": np.zeros(300)},
        index=idx,
    )
    ideas = [_idea("AAA", "Bullish", 0.7), _idea("FLAT", "Bullish", 0.9)]
    book = size_from_conviction(ideas, df, max_weight=0.40, gross_cap=2.0)
    assert "FLAT" not in book.weights()
    assert "FLAT" in book.skipped


# ── empty / degenerate inputs ───────────────────────────────────────────────

def test_empty_ideas_returns_empty_book() -> None:
    df = _panel({"AAA": 0.012})
    book = size_from_conviction([], df)
    assert isinstance(book, SizedBook)
    assert book.positions == []
    assert book.gross_exposure == 0.0 and book.net_exposure == 0.0
    assert book.provenance  # still annotated


def test_none_ideas_returns_empty_book() -> None:
    book = size_from_conviction(None, _panel({"AAA": 0.012}))
    assert book.positions == []


def test_empty_returns_panel_returns_empty_book() -> None:
    ideas = [_idea("AAA", "Bullish", 0.7), _idea("BBB", "Bearish", 0.6)]
    book = size_from_conviction(ideas, pd.DataFrame())
    assert book.positions == []
    # Both directional names skipped for "no real vol".
    assert set(book.skipped) >= {"AAA", "BBB"}


def test_none_returns_panel_returns_empty_book() -> None:
    ideas = [_idea("AAA", "Bullish", 0.7)]
    book = size_from_conviction(ideas, None)
    assert book.positions == []


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        size_from_conviction([_idea("AAA", "Bullish", 0.5)], _panel({"AAA": 0.012}),
                             mode="bogus")


# ── covariance vs diagonal flag + book-vol respects correlation ─────────────

def test_uses_real_covariance_when_two_names_present() -> None:
    df = _panel({"AAA": 0.012, "BBB": 0.012})
    book = size_from_conviction(
        [_idea("AAA", "Bullish", 0.7), _idea("BBB", "Bullish", 0.7)],
        df, max_weight=0.40, gross_cap=2.0,
    )
    assert book.used_covariance is True


def test_single_name_falls_back_to_diagonal() -> None:
    df = _panel({"AAA": 0.012})
    book = size_from_conviction(
        [_idea("AAA", "Bullish", 0.7)], df, max_weight=0.40, gross_cap=2.0,
    )
    assert book.used_covariance is False
    assert len(book.positions) == 1


# ── determinism ─────────────────────────────────────────────────────────────

def test_deterministic() -> None:
    df = _panel({"AAA": 0.008, "BBB": 0.030, "CCC": 0.015})
    ideas = [
        _idea("AAA", "Bullish", 0.9),
        _idea("BBB", "Bullish", 0.7),
        _idea("CCC", "Bearish", 0.6),
    ]
    a = size_from_conviction(ideas, df)
    b = size_from_conviction(ideas, df)
    assert a == b


def test_constants_published_in_book() -> None:
    book = size_from_conviction([], _panel({"AAA": 0.012}))
    for key in (
        "book_vol_budget", "kelly_fraction", "max_weight", "gross_cap",
        "kelly_edge_full", "trading_days_per_year", "min_annual_vol",
    ):
        assert key in book.constants
