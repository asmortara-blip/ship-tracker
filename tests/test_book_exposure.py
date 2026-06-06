"""Defining-property tests for processing.book_exposure (rec R008)."""
from __future__ import annotations

import pandas as pd
import pytest

from processing.book_exposure import (
    book_cascade_overlay,
    book_commodity_exposure,
    book_concentration,
    book_weights_detail,
)

_BOOK = [
    {"ticker": "ZIM", "shares": 100, "avg_cost": 10.0},
    {"ticker": "SBLK", "shares": 50, "avg_cost": 20.0},
]
_STOCK = {
    "ZIM": pd.DataFrame({"close": [18.0, 19.0, 20.0]}),    # mv = 100 × 20 = 2000
    "SBLK": pd.DataFrame({"close": [12.0, 13.0, 14.0]}),   # mv = 50 × 14 = 700
}


class _Idea:
    def __init__(self, ticker, direction, conviction, chain=None):
        self.ticker = ticker
        self.direction = direction
        self.conviction_score = conviction
        self.cascade_chain = chain or []


class _Link:
    def __init__(self, contribution):
        self.contribution = contribution


def test_priced_weights_sum_to_one_and_are_real() -> None:
    w, is_real = book_weights_detail(_BOOK, _STOCK)
    assert is_real is True
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["ZIM"] > w["SBLK"]          # heavier market value


def test_all_unpriced_falls_back_equal_weight_flagged() -> None:
    w, is_real = book_weights_detail(_BOOK, {})   # no prices → dark
    assert is_real is False
    assert w == pytest.approx({"ZIM": 0.5, "SBLK": 0.5})


def test_empty_book_is_honest_empty() -> None:
    assert book_weights_detail([], _STOCK) == ({}, False)
    assert book_commodity_exposure([], _STOCK) == {}
    assert book_concentration([], _STOCK)["n_priced"] == 0


def test_commodity_exposure_sums_to_one() -> None:
    ce = book_commodity_exposure(_BOOK, _STOCK)
    assert sum(ce.values()) == pytest.approx(1.0, abs=1e-3)


def test_concentration_single_name_is_fully_concentrated() -> None:
    c = book_concentration([{"ticker": "ZIM", "shares": 100, "avg_cost": 10.0}], _STOCK)
    assert c["hhi"] == pytest.approx(1.0)
    assert c["effective_n"] == pytest.approx(1.0)
    assert c["top_name_pct"] == pytest.approx(100.0)


def test_cascade_overlay_net_tilt_follows_heaviest_name() -> None:
    ov = book_cascade_overlay(
        _BOOK, [_Idea("ZIM", "Bullish", 0.8), _Idea("SBLK", "Bearish", 0.4)], _STOCK)
    assert ov.net_tilt == "Bullish"          # ZIM heavier and bullish
    assert ov.coverage == pytest.approx(1.0)
    assert ov.bullish_wt > ov.bearish_wt


def test_cascade_overlay_counts_uncovered_weight_based() -> None:
    ov = book_cascade_overlay(_BOOK, [_Idea("ZIM", "Bullish", 0.8)], _STOCK)
    assert ov.n_covered == 1 and ov.n_uncovered == 1
    # coverage is WEIGHT-based (ZIM's ~0.74 weight), not a 0.5 name count.
    assert ov.coverage == pytest.approx(0.7407, abs=1e-3)
    assert all(n.ticker == "ZIM" for n in ov.names)   # no fabricated SBLK


def test_cascade_overlay_uses_chain_contributions_when_present() -> None:
    idea = _Idea("ZIM", "Bullish", 0.5, chain=[_Link(0.3), _Link(0.2)])
    ov = book_cascade_overlay(
        [{"ticker": "ZIM", "shares": 100, "avg_cost": 10.0}], [idea], _STOCK)
    # cascade magnitude = Σ contributions (0.5), not the conviction score.
    assert ov.names[0].cascade_contribution == pytest.approx(0.5)


def test_cascade_overlay_empty_ideas_is_neutral() -> None:
    ov = book_cascade_overlay(_BOOK, [], _STOCK)
    assert ov.net_tilt == "Neutral"
    assert ov.coverage == 0.0
    assert ov.n_uncovered == 2
