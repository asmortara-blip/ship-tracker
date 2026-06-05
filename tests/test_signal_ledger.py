"""Point-in-time signal ledger (schema v32; rec R004) — increment 1."""

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
                 conviction_label="High", weight_set="default"):
        self.ticker = ticker
        self.direction = direction
        self.price = price
        self.conviction_score = conviction_score
        self.conviction_label = conviction_label
        self.conviction_weight_set = weight_set


def _stock(last_closes: dict):
    """ticker -> DataFrame whose LAST close is the given value."""
    idx = pd.date_range("2026-01-01", periods=3, freq="B")
    return {t: pd.DataFrame({"close": [p * 0.9, p * 0.95, p]}, index=idx)
            for t, p in last_closes.items()}


def test_v32_table_exists() -> None:
    from state.db import SCHEMA_VERSION, get_connection
    cols = [r[1] for r in get_connection().execute("PRAGMA table_info(signal_ledger)")]
    assert SCHEMA_VERSION >= 32
    assert "issue_close" in cols and "direction" in cols


def test_freeze_skips_neutral_and_dedups() -> None:
    from state.signal_ledger import freeze_ideas, load_ledger
    ideas = [_Idea("ZIM", "Bullish"), _Idea("SBLK", "Bearish"), _Idea("DAC", "Neutral")]
    assert freeze_ideas(ideas, issue_date="2026-06-01") == 2  # Neutral skipped
    # idempotent: re-freezing the same (ticker, date, direction) inserts nothing
    assert freeze_ideas(ideas, issue_date="2026-06-01") == 0
    assert {r["ticker"] for r in load_ledger()} == {"ZIM", "SBLK"}


def test_freeze_uses_idea_price_as_issue_close() -> None:
    from state.signal_ledger import freeze_ideas, load_ledger
    freeze_ideas([_Idea("ZIM", "Bullish", price=14.20)], issue_date="2026-06-01")
    row = load_ledger(ticker="ZIM")[0]
    assert row["issue_close"] == pytest.approx(14.20)
    assert row["conviction_label"] == "High" and row["weight_set"] == "default"


def test_mark_long_and_short_pnl() -> None:
    from state.signal_ledger import freeze_ideas, mark_ledger
    freeze_ideas([_Idea("ZIM", "Bullish", price=100.0),
                  _Idea("SBLK", "Bearish", price=50.0)], issue_date="2026-06-01")
    marked = {m["ticker"]: m for m in mark_ledger(_stock({"ZIM": 110.0, "SBLK": 45.0}))}
    # long ZIM 100 -> 110 = +10%
    assert marked["ZIM"]["signed_return_pct"] == pytest.approx(10.0) and marked["ZIM"]["win"]
    # short SBLK 50 -> 45 = -10% price move = +10% for the short
    assert marked["SBLK"]["signed_return_pct"] == pytest.approx(10.0) and marked["SBLK"]["win"]


def test_mark_losing_short() -> None:
    from state.signal_ledger import freeze_ideas, mark_ledger
    freeze_ideas([_Idea("ZIM", "Bearish", price=100.0)], issue_date="2026-06-01")
    m = mark_ledger(_stock({"ZIM": 110.0}))[0]   # short but price rose +10%
    assert m["signed_return_pct"] == pytest.approx(-10.0) and not m["win"]


def test_mark_skips_unpriced() -> None:
    from state.signal_ledger import freeze_ideas, mark_ledger
    freeze_ideas([_Idea("ZIM", "Bullish", price=100.0)], issue_date="2026-06-01")
    assert mark_ledger({}) == []  # no current price -> not fabricated, just skipped


def test_track_record_summary_by_label() -> None:
    from state.signal_ledger import freeze_ideas, track_record_summary
    freeze_ideas([
        _Idea("ZIM", "Bullish", price=100.0, conviction_label="High"),
        _Idea("SBLK", "Bullish", price=100.0, conviction_label="Low"),
    ], issue_date="2026-06-01")
    summ = track_record_summary(_stock({"ZIM": 110.0, "SBLK": 90.0}))  # ZIM win, SBLK loss
    assert summ["n"] == 2
    assert summ["hit_rate"] == pytest.approx(0.5)
    assert summ["by_label"]["High"]["hit_rate"] == 1.0
    assert summ["by_label"]["Low"]["hit_rate"] == 0.0


# ── OOS significance scorecard (R004 x R101) ────────────────────────────────

def test_oos_scorecard_insufficient_history() -> None:
    from state.signal_ledger import freeze_ideas, oos_scorecard
    freeze_ideas([_Idea("ZIM", "Bullish", price=100.0)], issue_date="2026-06-01")
    sc = oos_scorecard(_stock({"ZIM": 110.0}), min_n=5)
    assert sc["sufficient"] is False and sc["n"] == 1 and sc["psr"] is None


def test_oos_scorecard_flags_consistent_winners_significant() -> None:
    from state.signal_ledger import freeze_ideas, oos_scorecard
    freeze_ideas([_Idea(f"T{i}", "Bullish", price=100.0) for i in range(8)],
                 issue_date="2026-06-01")
    stock = _stock({f"T{i}": 100.0 * (1.08 + i * 0.01) for i in range(8)})  # +8..+15%
    sc = oos_scorecard(stock, min_n=5)
    assert sc["sufficient"] and sc["n"] == 8 and sc["hit_rate"] == 1.0
    assert sc["psr"] > 0.9 and sc["is_significant"]


def test_oos_scorecard_noise_not_significant() -> None:
    from state.signal_ledger import freeze_ideas, oos_scorecard
    freeze_ideas([_Idea(f"T{i}", "Bullish", price=100.0) for i in range(8)],
                 issue_date="2026-06-01")
    # alternating +5% / -5% -> mean ~0 -> PSR ~0.5 -> not significant
    stock = _stock({f"T{i}": 100.0 * (1.0 + (0.05 if i % 2 else -0.05)) for i in range(8)})
    sc = oos_scorecard(stock, min_n=5)
    assert sc["sufficient"] and not sc["is_significant"]
