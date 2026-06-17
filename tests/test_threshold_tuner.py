"""R043 — close the self-tuning loop: sweep → recommend → apply thresholds.

Pins the contract of the threshold tuner added to ``engine.alert_backtest``
(``sweep_thresholds`` / ``recommend_threshold`` / ``apply_recommended_threshold``)
and the reversible single-key write it applies through
(``engine.route_thresholds.set_threshold_for_key`` / ``ThresholdWriteResult``):

  1. A candidate threshold re-filters which historical fires are counted
     (``|predicted_change_pct| >= candidate``); fire-count is monotone
     non-increasing in the threshold; hit-rate is hits/fires over the
     firing subset. The garbage-tolerant grid is sorted + de-duped.
  2. recommend_threshold MAXIMIZES hit-rate SUBJECT TO a min-fire-count
     floor, tie-breaking to the LOWER threshold; when nothing clears the
     floor it honestly recommends nothing (no fabrication from thin data).
  3. The 'why' compares against the current override threshold when one
     exists, read from the same store the rate detector reads.
  4. set_threshold_for_key is validated (rejects empty key / NaN / inf) and
     reversible (carries the prior value); apply closes the loop so a later
     recommend sees the applied bar as 'current'.
"""
from __future__ import annotations

import math

import pytest

from engine.alert_backtest import (
    AlertOutcome,
    ThresholdRecommendation,
    ThresholdScore,
    apply_recommended_threshold,
    recommend_threshold,
    sweep_thresholds,
)
from engine.alert_backtest import _clean_candidates, _score_candidate
from engine.route_thresholds import (
    ThresholdWriteResult,
    load_route_thresholds,
    set_threshold_for_key,
)


# ── Per-test SQLite isolation (kv_state for the override store) ──────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ── Outcome builder ─────────────────────────────────────────────────────────

def _oc(predicted: float, hit: bool, alert_type: str = "STOCK_MOVE") -> AlertOutcome:
    """A scored AlertOutcome with a controlled predicted magnitude + hit."""
    return AlertOutcome(
        alert_id="x", alert_type=alert_type, severity="HIGH",
        created_at="2026-04-01T00:00:00+00:00",
        predicted_change_pct=predicted, realized_change_pct=predicted,
        realized_window_days=7, hit=hit, magnitude_ratio=1.0,
    )


# A book of STOCK_MOVE outcomes engineered so the BEST in-floor bar is 3.0:
#   • 6 small fires (|2.0|), 3 hits → 50% hit-rate
#   • 4 mid fires   (|10.0|), all hit → 100%
#   • 2 big fires   (|22.0|), all hit → 100% but only 2 fires (below the floor)
_BOOK = (
    [_oc(2.0, True) for _ in range(3)]
    + [_oc(2.0, False) for _ in range(3)]
    + [_oc(10.0, True) for _ in range(4)]
    + [_oc(22.0, True) for _ in range(2)]
)


# ── 1. Candidate grid hygiene ───────────────────────────────────────────────

def test_clean_candidates_drops_garbage_dedupes_sorts() -> None:
    out = _clean_candidates([5, "x", float("nan"), float("inf"), -3, 0, 5, 2, 8])
    assert out == [2.0, 5.0, 8.0]            # sorted, de-duped, positive-finite


def test_clean_candidates_empty_or_noniterable_falls_back_to_default() -> None:
    default = _clean_candidates(None)
    assert default and all(isinstance(c, float) for c in default)
    assert _clean_candidates([]) == default
    assert _clean_candidates(123) == default  # non-iterable → default grid


# ── 2. sweep / _score_candidate ─────────────────────────────────────────────

def test_score_candidate_counts_only_fires_at_or_above_threshold() -> None:
    s = _score_candidate(_BOOK, threshold=3.0)
    # The |2.0| fires drop; only |10.0|×4 + |22.0|×2 = 6 clear the 3.0 bar.
    assert s.fire_count == 6
    assert s.hit_count == 6
    assert s.hit_rate == 1.0
    assert s.threshold == 3.0


def test_sweep_fire_count_is_monotone_non_increasing_in_threshold() -> None:
    grid = sweep_thresholds("STOCK_MOVE", [1.0, 2.0, 3.0, 10.0, 15.0, 25.0],
                            outcomes=_BOOK)
    assert [s.threshold for s in grid] == [1.0, 2.0, 3.0, 10.0, 15.0, 25.0]
    fires = [s.fire_count for s in grid]
    assert fires == sorted(fires, reverse=True)   # never rises as the bar rises
    assert fires[0] == 12 and fires[-1] == 0      # all fire at 1.0; none at 25.0


def test_sweep_filters_by_alert_type() -> None:
    mixed = _BOOK + [_oc(10.0, True, alert_type="BDI_MOVE") for _ in range(5)]
    grid = sweep_thresholds("BDI_MOVE", [10.0], outcomes=mixed)
    assert grid[0].fire_count == 5            # only the BDI_MOVE outcomes counted


def test_sweep_empty_outcomes_yields_zero_fire_grid() -> None:
    grid = sweep_thresholds("STOCK_MOVE", [1.0, 5.0], outcomes=[])
    assert all(s.fire_count == 0 and s.hit_rate == 0.0 for s in grid)


# ── 3. recommend_threshold — maximize hit-rate s.t. the fire-count floor ────

def test_recommend_picks_best_hit_rate_above_floor_tiebreak_lower() -> None:
    rec = recommend_threshold("STOCK_MOVE", min_fire_count=5, outcomes=_BOOK)
    assert isinstance(rec, ThresholdRecommendation)
    # Eligible (≥5 fires): 1.0/2.0 at 0.75, and 3.0/5.0/8.0/10.0 at 1.0.
    # Max hit-rate 1.0 → tie-break to the LOWEST such threshold = 3.0.
    assert rec.recommended == 3.0
    assert rec.recommended_hit_rate == 1.0
    assert rec.recommended_fire_count == 6
    assert rec.candidates                      # the full swept grid is returned


def test_recommend_respects_a_higher_floor() -> None:
    """Raise the floor above every 100%-bar's fire-count and the recommender
    falls back to the best bar that still clears it (the 0.75, 12-fire bar)."""
    rec = recommend_threshold("STOCK_MOVE", min_fire_count=7, outcomes=_BOOK)
    # Only the 1.0 / 2.0 bars fire ≥7 (12 each); both at 0.75 → lower wins.
    assert rec.recommended == 1.0
    assert rec.recommended_hit_rate == 0.75


def test_recommend_returns_none_when_nothing_clears_floor() -> None:
    """Too-thin evidence → honest 'no recommendation', never fabricated."""
    thin = [_oc(10.0, True), _oc(22.0, True)]   # only 2 fires total
    rec = recommend_threshold("STOCK_MOVE", min_fire_count=5, outcomes=thin)
    assert rec.recommended is None
    assert "insufficient data" in rec.reason.lower()
    assert rec.min_fire_count == 5


def test_recommend_with_no_outcomes_is_honest_empty() -> None:
    rec = recommend_threshold("STOCK_MOVE", outcomes=[])
    assert rec.recommended is None
    assert rec.current_threshold is None        # no override on record


# ── 4. The 'why' compares against the current override ──────────────────────

def test_recommend_reads_current_override_for_the_delta() -> None:
    # Seed a current override for STOCK_MOVE at a permissive 2.0 bar.
    set_threshold_for_key("STOCK_MOVE", 2.0)
    rec = recommend_threshold("STOCK_MOVE", min_fire_count=5, outcomes=_BOOK)
    assert rec.current_threshold == 2.0
    # Current bar (2.0) scores 0.75 over 12 fires; recommended (3.0) scores 1.0.
    assert rec.current_hit_rate == pytest.approx(0.75)
    assert rec.current_fire_count == 12
    assert "pts" in rec.reason                  # the delta is reported


# ── 5. set_threshold_for_key — validated + reversible ───────────────────────

def test_set_threshold_writes_and_round_trips() -> None:
    res = set_threshold_for_key("RATE_SURGE", 6.5, severity="CRITICAL")
    assert isinstance(res, ThresholdWriteResult)
    assert res.ok is True
    assert res.had_prior is False and res.prior_value is None
    loaded = load_route_thresholds()["RATE_SURGE"]
    assert loaded.threshold_pct == 6.5
    assert loaded.severity == "CRITICAL"


def test_set_threshold_captures_prior_for_reversibility() -> None:
    set_threshold_for_key("STOCK_MOVE", 5.0)
    res = set_threshold_for_key("STOCK_MOVE", 9.0)
    assert res.had_prior is True
    assert res.prior_value == 5.0               # enough to undo the change
    # Undo restores the prior bar.
    set_threshold_for_key("STOCK_MOVE", res.prior_value)
    assert load_route_thresholds()["STOCK_MOVE"].threshold_pct == 5.0


def test_set_threshold_preserves_severity_when_not_given() -> None:
    set_threshold_for_key("BDI_MOVE", 4.0, severity="MEDIUM")
    set_threshold_for_key("BDI_MOVE", 7.0)      # no severity → preserve MEDIUM
    assert load_route_thresholds()["BDI_MOVE"].severity == "MEDIUM"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", None])
def test_set_threshold_rejects_non_finite_or_nonnumeric(bad) -> None:
    res = set_threshold_for_key("STOCK_MOVE", bad)
    assert res.ok is False
    assert "STOCK_MOVE" not in load_route_thresholds()   # nothing written


def test_set_threshold_rejects_empty_key() -> None:
    res = set_threshold_for_key("   ", 5.0)
    assert res.ok is False


# ── 6. apply closes the loop ────────────────────────────────────────────────

def test_apply_recommended_threshold_persists_and_is_seen_as_current() -> None:
    rec = recommend_threshold("STOCK_MOVE", min_fire_count=5, outcomes=_BOOK)
    assert rec.recommended == 3.0

    res = apply_recommended_threshold("STOCK_MOVE", rec.recommended)
    assert res is not None and res.ok is True
    assert load_route_thresholds()["STOCK_MOVE"].threshold_pct == 3.0

    # Re-running the recommendation now sees the applied bar as 'current'.
    rec2 = recommend_threshold("STOCK_MOVE", min_fire_count=5, outcomes=_BOOK)
    assert rec2.current_threshold == 3.0


def test_apply_bad_value_returns_not_ok_without_raising() -> None:
    res = apply_recommended_threshold("STOCK_MOVE", float("nan"))
    assert res is None or res.ok is False
    assert "STOCK_MOVE" not in load_route_thresholds()
