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


def _stock_at(start: str, last_closes: dict):
    """ticker -> DataFrame dated from ``start`` whose LAST close is the value."""
    idx = pd.date_range(start, periods=3, freq="B")
    return {t: pd.DataFrame({"close": [p * 0.9, p * 0.95, p]}, index=idx)
            for t, p in last_closes.items()}


def _stock(last_closes: dict):
    """ticker -> DataFrame whose LAST close is the given value, dated forward of
    the 2026-06 issue dates so every mark is genuinely causal."""
    return _stock_at("2026-07-01", last_closes)


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


def test_mark_is_look_ahead_free_skips_close_at_or_before_issue() -> None:
    # The causal guarantee enforced in code (review HIGH): a close dated at or
    # BEFORE issue_date is non-causal and must NOT be scored.
    from state.signal_ledger import freeze_ideas, mark_ledger
    freeze_ideas([_Idea("ZIM", "Bullish", price=100.0)], issue_date="2026-06-01")
    stale = _stock_at("2026-01-01", {"ZIM": 110.0})    # 5 months BEFORE issue
    assert mark_ledger(stale) == []                     # no look-ahead mark
    # a strictly-later close IS marked
    fwd = _stock_at("2026-07-01", {"ZIM": 110.0})
    assert mark_ledger(fwd)[0]["signed_return_pct"] == pytest.approx(10.0)


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


# ── net-of-cost track record (R103 cost layer on the R004 ledger) ────────────

def test_track_record_net_of_cost_drag() -> None:
    from state.signal_ledger import freeze_ideas, track_record_summary
    freeze_ideas([_Idea("ZIM", "Bullish", price=100.0, conviction_label="High")],
                 issue_date="2026-06-01")
    summ = track_record_summary(_stock({"ZIM": 110.0}))  # +10% gross
    assert summ["mean_signed_return_pct"] == pytest.approx(10.0)
    # ZIM assumed round-trip cost = 32 bps = 0.32% → net 9.68%.
    assert summ["mean_net_signed_return_pct"] == pytest.approx(9.68)
    assert summ["cost_drag_pct"] == pytest.approx(0.32)
    assert summ["by_label"]["High"]["mean_net_signed_return_pct"] == pytest.approx(9.68)


def test_track_record_thin_winner_flips_to_loss_net_of_cost() -> None:
    # A +0.2% gross "win" is a LOSS once you pay the 0.40% default round-trip
    # cost — the whole point of showing net alongside gross.
    from state.signal_ledger import freeze_ideas, track_record_summary
    freeze_ideas([_Idea("WXYZ", "Bullish", price=100.0)], issue_date="2026-06-01")
    summ = track_record_summary(_stock({"WXYZ": 100.2}))  # +0.2% gross
    assert summ["hit_rate"] == pytest.approx(1.0)       # gross win
    assert summ["net_hit_rate"] == pytest.approx(0.0)   # net loss
    assert summ["mean_net_signed_return_pct"] < 0


def test_oos_scorecard_reports_net_significance() -> None:
    from state.signal_ledger import freeze_ideas, oos_scorecard
    freeze_ideas([_Idea(f"T{i}", "Bullish", price=100.0) for i in range(8)],
                 issue_date="2026-06-01")
    stock = _stock({f"T{i}": 100.0 * (1.08 + i * 0.01) for i in range(8)})  # +8..+15%
    sc = oos_scorecard(stock, min_n=5)
    # Net Sharpe is strictly below gross (mean shifted down by the cost), and
    # these large consistent winners still clear net of the assumed friction.
    assert sc["net_cross_sectional_sharpe"] < sc["cross_sectional_sharpe"]
    assert sc["net_is_significant"]
    assert "Net of assumed costs" in sc["verdict"]


def test_net_of_cost_winning_short_pays_borrow_on_top_of_round_trip() -> None:
    from state.signal_ledger import freeze_ideas, mark_ledger
    freeze_ideas([_Idea("SBLK", "Bearish", price=100.0)], issue_date="2026-06-01")
    marks = mark_ledger(_stock({"SBLK": 90.0}))  # short wins as the price falls
    assert len(marks) == 1
    m = marks[0]
    assert m["signed_return_pct"] == pytest.approx(10.0)  # +10% gross winning short
    assert m["win"] is True and m["net_win"] is True
    # Net = gross − 0.40% round-trip − short borrow (>0 over the held period), so
    # a short's net is strictly below the long-equivalent (gross − round-trip).
    assert m["net_signed_return_pct"] < 10.0 - 0.40


def test_oos_scorecard_edge_dies_net_of_costs() -> None:
    from state.signal_ledger import freeze_ideas, oos_scorecard
    # 30 LONG ideas, gross returns mean 0.50% / sd 0.75% (a deterministic ramp):
    # comfortably significant GROSS, but the edge evaporates once you pay the
    # 0.40% default round-trip cost — the headline verdict flip.
    n = 30
    mid = (n - 1) / 2
    sd_units = (sum((i - mid) ** 2 for i in range(n)) / n) ** 0.5
    rets = [0.50 + 0.75 * ((i - mid) / sd_units) for i in range(n)]
    freeze_ideas([_Idea(f"L{i}", "Bullish", price=100.0) for i in range(n)],
                 issue_date="2026-06-01")
    stock = _stock({f"L{i}": 100.0 * (1.0 + rets[i] / 100.0) for i in range(n)})
    sc = oos_scorecard(stock, min_n=5)
    assert sc["net_psr"] < sc["psr"]
    assert sc["is_significant"] and not sc["net_is_significant"]
    assert "does NOT clear net of costs" in sc["verdict"]


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


# ── per-tier drawdown kill-switch (B2, on R004) ─────────────────────────────
# Drawdown is the order-INVARIANT underwater-from-cost of the equal-weight tier
# book (max(0, -mean signed return)) — NOT a compounded equity path whose
# running peak depends on the arbitrary mark order.

def test_tier_drawdown_all_winners_active() -> None:
    from state.signal_ledger import freeze_ideas, tier_drawdown
    freeze_ideas([_Idea(f"T{i}", "Bullish", price=100.0, conviction_label="High")
                  for i in range(5)], issue_date="2026-06-01")
    high = tier_drawdown(_stock({f"T{i}": 110.0 for i in range(5)}), min_n=5)["High"]
    assert high["n"] == 5 and high["hit_rate"] == 1.0
    assert high["mean_signed_return_pct"] == pytest.approx(10.0)
    assert high["drawdown_from_cost_pct"] == pytest.approx(0.0)  # all up -> no DD
    assert high["worst_signal_return_pct"] == pytest.approx(10.0)
    assert high["status"] == "ACTIVE"


def test_tier_drawdown_deep_losers_stand_down_via_drawdown() -> None:
    # 2 winners +5%, 3 losers -30% -> mean -16% -> 16% underwater > 15% limit,
    # but hit 2/5 = 0.40 is NOT below the floor -> isolates the drawdown trigger.
    from state.signal_ledger import freeze_ideas, tier_drawdown
    freeze_ideas([_Idea(f"W{i}", "Bullish", price=100.0, conviction_label="High")
                  for i in range(2)], issue_date="2026-06-01")
    freeze_ideas([_Idea(f"L{i}", "Bullish", price=100.0, conviction_label="High")
                  for i in range(3)], issue_date="2026-06-02")
    stock = _stock({**{f"W{i}": 105.0 for i in range(2)},
                    **{f"L{i}": 70.0 for i in range(3)}})
    high = tier_drawdown(stock, min_n=5, hit_floor=0.40, dd_threshold_pct=15.0)["High"]
    assert high["n"] == 5 and high["hit_rate"] == pytest.approx(0.40)
    assert high["mean_signed_return_pct"] == pytest.approx(-16.0)
    assert high["drawdown_from_cost_pct"] == pytest.approx(16.0)
    assert high["status"] == "STAND_DOWN"          # drawdown trigger (not hit)


def test_tier_drawdown_low_hit_rate_stand_down() -> None:
    # 1 small win, 4 small losses -> hit 0.2 < 0.40 floor, mean only -0.6% so
    # the drawdown is shallow (<15%) -> proves the hit-rate floor is its own trigger.
    from state.signal_ledger import freeze_ideas, tier_drawdown
    freeze_ideas([_Idea("WIN", "Bullish", price=100.0, conviction_label="Low")],
                 issue_date="2026-06-01")
    freeze_ideas([_Idea(f"L{i}", "Bullish", price=100.0, conviction_label="Low")
                  for i in range(4)], issue_date="2026-06-02")
    stock = _stock({"WIN": 101.0, **{f"L{i}": 99.0 for i in range(4)}})
    low = tier_drawdown(stock, min_n=5, hit_floor=0.40, dd_threshold_pct=15.0)["Low"]
    assert low["n"] == 5 and low["hit_rate"] == pytest.approx(0.2)
    assert low["drawdown_from_cost_pct"] < 15.0   # not the drawdown trigger
    assert low["status"] == "STAND_DOWN"           # the hit-rate floor trigger


def test_tier_drawdown_modest_loss_stays_active() -> None:
    # 3 winners +10%, 3 losers -20% -> mean -5% -> only 5% underwater, hit 50%.
    # The OLD compounded-equity metric called this 48.8% drawdown -> STAND_DOWN;
    # the honest order-invariant metric leaves it ACTIVE (edge not cratered).
    from state.signal_ledger import freeze_ideas, tier_drawdown
    freeze_ideas([_Idea(f"W{i}", "Bullish", price=100.0, conviction_label="High")
                  for i in range(3)], issue_date="2026-06-01")
    freeze_ideas([_Idea(f"L{i}", "Bullish", price=100.0, conviction_label="High")
                  for i in range(3)], issue_date="2026-06-02")
    stock = _stock({**{f"W{i}": 110.0 for i in range(3)},
                    **{f"L{i}": 80.0 for i in range(3)}})
    high = tier_drawdown(stock, min_n=5, dd_threshold_pct=15.0)["High"]
    assert high["mean_signed_return_pct"] == pytest.approx(-5.0)
    assert high["drawdown_from_cost_pct"] == pytest.approx(5.0)
    assert high["status"] == "ACTIVE"


def test_tier_drawdown_is_order_invariant() -> None:
    # The kill-switch must not depend on the arbitrary order of the marks (or
    # the ticker tie-break when ideas are frozen in one batch). Same return
    # multiset, opposite issue-date assignment -> identical drawdown + status.
    from state import db as _db
    from state.signal_ledger import freeze_ideas, tier_drawdown

    def _run(win_date, loss_date):
        _db.reset_for_tests()
        freeze_ideas([_Idea(f"W{i}", "Bullish", price=100.0, conviction_label="High")
                      for i in range(2)], issue_date=win_date)
        freeze_ideas([_Idea(f"L{i}", "Bullish", price=100.0, conviction_label="High")
                      for i in range(3)], issue_date=loss_date)
        stock = _stock({**{f"W{i}": 105.0 for i in range(2)},
                        **{f"L{i}": 70.0 for i in range(3)}})
        return tier_drawdown(stock, min_n=5)["High"]

    a = _run("2026-06-01", "2026-06-02")   # winners first
    b = _run("2026-06-02", "2026-06-01")   # losers first
    assert a["drawdown_from_cost_pct"] == pytest.approx(b["drawdown_from_cost_pct"])
    assert a["mean_signed_return_pct"] == pytest.approx(b["mean_signed_return_pct"])
    assert a["status"] == b["status"]


def test_tier_drawdown_below_min_n_stays_active() -> None:
    # 3 straight losers, but below min_n -> insufficient evidence to demote.
    from state.signal_ledger import freeze_ideas, tier_drawdown
    freeze_ideas([_Idea(f"L{i}", "Bullish", price=100.0, conviction_label="High")
                  for i in range(3)], issue_date="2026-06-01")
    high = tier_drawdown(_stock({f"L{i}": 80.0 for i in range(3)}), min_n=5)["High"]
    assert high["n"] == 3 and high["hit_rate"] == 0.0
    assert high["status"] == "ACTIVE"


def test_freeze_skips_unpriced_ideas() -> None:
    # An idea with no real issue price (price=0, no stock_data) can never be
    # marked forward, so it must NOT enter the ledger (keeps illustrative
    # cascade-on-sparse-data ideas out of the track record).
    from state.signal_ledger import freeze_ideas, load_ledger
    n = freeze_ideas([_Idea("ZIM", "Bullish", price=0.0)], issue_date="2026-06-01")
    assert n == 0 and load_ledger() == []
    # but with a stock_data fallback price it IS frozen
    n2 = freeze_ideas([_Idea("ZIM", "Bullish", price=0.0)],
                      issue_date="2026-06-02", stock_data=_stock({"ZIM": 14.2}))
    assert n2 == 1 and load_ledger()[0]["issue_close"] == pytest.approx(14.2)
