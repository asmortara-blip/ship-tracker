"""Causal, look-ahead-free validation from the frozen signal ledger (rec R016)."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


class _Idea:
    def __init__(self, ticker, direction, price=100.0, conviction_score=0.7,
                 conviction_label="High"):
        self.ticker = ticker
        self.direction = direction
        self.price = price
        self.conviction_score = conviction_score
        self.conviction_label = conviction_label
        self.conviction_weight_set = "default"


def _stock(last_closes: dict):
    idx = pd.date_range("2026-01-01", periods=3, freq="B")
    return {t: pd.DataFrame({"close": [p * 0.9, p * 0.95, p]}, index=idx)
            for t, p in last_closes.items()}


def test_empty_ledger_is_valid_but_empty() -> None:
    from processing.signal_validation import build_ledger_validation_report
    rep = build_ledger_validation_report({})
    assert rep.n_signals_validated == 0 and rep.signals == [] and rep.tiers == []
    assert "No frozen" in rep.summary
    assert rep.source is not None and "causal" in rep.source.name.lower()


def test_all_winners_beat_zero_baseline_is_impossible_but_hit_is_one() -> None:
    from processing.signal_validation import build_ledger_validation_report
    from state.signal_ledger import freeze_ideas
    freeze_ideas([_Idea(f"T{i}", "Bullish", price=100.0) for i in range(4)],
                 issue_date="2026-06-01")
    rep = build_ledger_validation_report(_stock({f"T{i}": 110.0 for i in range(4)}))
    assert rep.n_signals_validated == 4
    assert rep.overall_hit_rate == 1.0
    # every name rose -> always-long base rate is also 1.0 -> zero edge (honest:
    # being long in a rising tape is no edge over long-everything).
    assert rep.overall_baseline_hit_rate == 1.0
    assert rep.overall_edge == 0.0
    assert rep.tiers[0].tier == "High" and rep.tiers[0].hit_rate == 1.0


def test_bearish_correct_calls_beat_always_long_baseline() -> None:
    # Names that FELL: a bearish call wins (signed +), but always-long loses ->
    # the tier shows positive edge vs the naive long-everything baseline.
    from processing.signal_validation import build_ledger_validation_report
    from state.signal_ledger import freeze_ideas
    freeze_ideas([_Idea(f"S{i}", "Bearish", price=100.0, conviction_label="High")
                  for i in range(4)], issue_date="2026-06-01")
    rep = build_ledger_validation_report(_stock({f"S{i}": 80.0 for i in range(4)}))
    assert rep.overall_hit_rate == 1.0            # all bearish calls correct
    assert rep.overall_baseline_hit_rate == 0.0   # nothing rose -> always-long 0%
    assert rep.overall_edge == 1.0                # full edge over naive long
    assert rep.tiers[0].directional_return > 0


def test_mixed_tiers_and_per_signal_shape() -> None:
    from processing.signal_validation import build_ledger_validation_report
    from state.signal_ledger import freeze_ideas
    freeze_ideas([
        _Idea("ZIM", "Bullish", price=100.0, conviction_label="High"),    # win
        _Idea("SBLK", "Bullish", price=100.0, conviction_label="Low"),    # loss
    ], issue_date="2026-06-01")
    rep = build_ledger_validation_report(_stock({"ZIM": 110.0, "SBLK": 90.0}))
    assert rep.overall_hit_rate == pytest.approx(0.5)
    by = {t.tier: t for t in rep.tiers}
    assert by["High"].hit_rate == 1.0 and by["Low"].hit_rate == 0.0
    # each frozen signal is exactly ONE causal observation (no window resampling)
    assert all(s.n_observations == 1 for s in rep.signals)
    assert {s.signal_id for s in rep.signals} == {"ZIM", "SBLK"}


def test_never_raises_on_bad_stock_data() -> None:
    from processing.signal_validation import build_ledger_validation_report
    rep = build_ledger_validation_report(None)
    assert rep.n_signals_validated == 0    # no marks -> empty but valid
