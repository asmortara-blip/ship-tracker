"""Conviction→P(hit) calibration from the frozen ledger (rec R033)."""

from __future__ import annotations

import pandas as pd
import pytest


# ── fit_calibration (pure) ───────────────────────────────────────────────────

def test_insufficient_marks_not_fitted() -> None:
    from processing.conviction_calibration import fit_calibration
    c = fit_calibration([0.7, 0.3], [1, 0], min_n=20)
    assert c.fitted is False and c.n == 2 and c.bins == []
    assert "need >= 20" in c.summary


def test_calibration_is_monotone_in_conviction() -> None:
    # Higher conviction wins more often -> isotonic P(hit) must be non-decreasing.
    from processing.conviction_calibration import fit_calibration
    scores, wins = [], []
    for s, p in [(0.1, 0), (0.2, 0), (0.3, 0), (0.4, 0),
                 (0.6, 1), (0.7, 1), (0.8, 1), (0.9, 1)]:
        scores += [s, s, s]
        wins += [p, p, p]
    c = fit_calibration(scores, wins, min_n=10, n_bins=5)
    assert c.fitted is True
    assert c.iso_y == sorted(c.iso_y)                 # non-decreasing
    assert c.brier_score < 0.1                         # near-perfectly separable


def test_reliability_bins_match_realized() -> None:
    from processing.conviction_calibration import fit_calibration
    # bin [0.6,0.8): two scores both winners -> realized hit-rate 1.0
    scores = [0.65] * 6 + [0.15] * 6
    wins = [1] * 6 + [0] * 6
    c = fit_calibration(scores, wins, min_n=8, n_bins=5)
    hi_bin = next(b for b in c.bins if b.score_lo == pytest.approx(0.6))
    lo_bin = next(b for b in c.bins if b.score_lo == pytest.approx(0.0))
    assert hi_bin.realized_hit_rate == 1.0 and hi_bin.n == 6
    assert lo_bin.realized_hit_rate == 0.0 and lo_bin.n == 6


def test_by_label_breakdown() -> None:
    from processing.conviction_calibration import fit_calibration
    scores = [0.7] * 5 + [0.3] * 5
    wins = [1, 1, 1, 1, 0] + [0, 0, 0, 0, 1]
    labels = ["High"] * 5 + ["Low"] * 5
    c = fit_calibration(scores, wins, labels, min_n=8)
    assert c.by_label["High"]["realized_hit_rate"] == pytest.approx(0.8)
    assert c.by_label["Low"]["realized_hit_rate"] == pytest.approx(0.2)
    assert c.by_label["High"]["calibrated_prob"] >= c.by_label["Low"]["calibrated_prob"]


def test_empty_input_not_fitted() -> None:
    from processing.conviction_calibration import fit_calibration
    c = fit_calibration([], [], min_n=20)
    assert c.fitted is False and c.n == 0


def test_degenerate_calibration_is_disclosed_not_overclaimed() -> None:
    # Win/loss independent of conviction -> flat isotonic -> the summary must NOT
    # claim a monotone mapping (review MED [7]); Brier labeled in-sample [6].
    from processing.conviction_calibration import fit_calibration
    scores = [0.2, 0.4, 0.6, 0.8] * 3
    wins = [1, 0] * 6                         # no relationship to score
    c = fit_calibration(scores, wins, min_n=8)
    assert c.fitted
    assert "does NOT separate" in c.summary
    assert "in-sample Brier" in c.summary


def test_scores_out_of_range_are_clamped_into_bins() -> None:
    # A score > 1 (shouldn't happen, but must not silently vanish) lands in the
    # top bin rather than dropping out of every bin (review LOW [8]).
    from processing.conviction_calibration import fit_calibration
    c = fit_calibration([1.5, 1.4, 0.1, 0.05, 0.9, 0.8], [1, 1, 0, 0, 1, 1],
                        min_n=4, n_bins=5)
    assert sum(b.n for b in c.bins) == 6        # every score is binned
    assert all(b.mean_conviction <= 1.0 for b in c.bins)


def test_n_bins_zero_does_not_raise() -> None:
    # max(1, n_bins) must guard the lo/hi division everywhere (review LOW [14]).
    from processing.conviction_calibration import fit_calibration
    c = fit_calibration([0.2, 0.8] * 4, [0, 1] * 4, min_n=4, n_bins=0)
    assert c.fitted and len(c.bins) >= 1


# ── calibrate_conviction (via the ledger) ────────────────────────────────────

class _Idea:
    def __init__(self, ticker, direction, price, conviction_score, conviction_label):
        self.ticker = ticker
        self.direction = direction
        self.price = price
        self.conviction_score = conviction_score
        self.conviction_label = conviction_label
        self.conviction_weight_set = "default"


def _stock(last_closes: dict):
    idx = pd.date_range("2026-07-01", periods=3, freq="B")  # forward of the 2026-06 issue dates (causal)
    return {t: pd.DataFrame({"close": [p * 0.9, p * 0.95, p]}, index=idx)
            for t, p in last_closes.items()}


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def test_calibrate_from_ledger(isolated_db) -> None:
    from processing.conviction_calibration import calibrate_conviction
    from state.signal_ledger import freeze_ideas
    # 4 High (win) + 4 Low (lose): high conviction should calibrate higher.
    freeze_ideas(
        [_Idea(f"H{i}", "Bullish", 100.0, 0.75, "High") for i in range(4)] +
        [_Idea(f"L{i}", "Bullish", 100.0, 0.30, "Low") for i in range(4)],
        issue_date="2026-06-01")
    stock = _stock({**{f"H{i}": 110.0 for i in range(4)},
                    **{f"L{i}": 90.0 for i in range(4)}})
    c = calibrate_conviction(stock, min_n=6)
    assert c.fitted and c.n == 8
    assert c.by_label["High"]["calibrated_prob"] >= c.by_label["Low"]["calibrated_prob"]


def test_calibrate_empty_ledger_not_fitted(isolated_db) -> None:
    from processing.conviction_calibration import calibrate_conviction
    c = calibrate_conviction({}, min_n=20)
    assert c.fitted is False and c.n == 0


def test_calibrate_never_raises_on_bad_input(isolated_db) -> None:
    from processing.conviction_calibration import calibrate_conviction
    c = calibrate_conviction(None)
    assert c.fitted is False
