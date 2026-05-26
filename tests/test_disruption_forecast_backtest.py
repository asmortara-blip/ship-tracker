"""Defining-property tests for processing/disruption_forecast_backtest.py.

The backtest scores the 7d and 30d stress forecasts from
``processing.disruption_forecast`` against realized stress. These tests
pin the contract: deterministic, bounded, one scorecard per route, with
MAE and sign-agreement honouring their natural ranges.
"""
from __future__ import annotations

import pytest

from processing.disruption_forecast_backtest import (
    ForecastAccuracyReport,
    RouteAccuracyScorecard,
    backtest_disruption_forecast,
    synthesize_forecast_history,
)


# ── 1. Synthetic history shape and determinism ─────────────────────────────

def test_synthesize_forecast_history_is_deterministic() -> None:
    """Same seed → byte-identical history. The whole backtest's
    reproducibility rides on this."""
    a = synthesize_forecast_history(n_periods=10, n_routes=3, seed=42)
    b = synthesize_forecast_history(n_periods=10, n_routes=3, seed=42)
    assert a == b


def test_synthesize_forecast_history_carries_required_keys() -> None:
    """Every row must carry the six keys the backtest reads off it."""
    rows = synthesize_forecast_history(n_periods=4, n_routes=2)
    required = {"route_id", "current_stress",
                "forecast_7d", "forecast_30d",
                "realized_7d", "realized_30d"}
    for row in rows:
        assert required <= set(row.keys())


def test_synthesize_forecast_history_values_bounded() -> None:
    """All stress values must sit in [0, 1] regardless of noise draw."""
    rows = synthesize_forecast_history(
        n_periods=20, n_routes=4, forecast_noise=0.30,
    )
    for row in rows:
        for k in ("current_stress",
                  "forecast_7d", "forecast_30d",
                  "realized_7d", "realized_30d"):
            assert 0.0 <= row[k] <= 1.0


def test_synthesize_forecast_history_row_count() -> None:
    """n_periods × n_routes rows."""
    rows = synthesize_forecast_history(n_periods=5, n_routes=3)
    assert len(rows) == 15


# ── 2. Backtest returns one scorecard per route ────────────────────────────

def test_backtest_returns_one_scorecard_per_route() -> None:
    report = backtest_disruption_forecast()
    assert isinstance(report, ForecastAccuracyReport)
    # Default synth uses 6 routes
    assert len(report.scorecards) == 6
    route_ids = {s.route_id for s in report.scorecards}
    assert all(rid.startswith("ROUTE_") for rid in route_ids)


def test_backtest_uses_synthetic_history_when_none_given() -> None:
    """Empty / None history must not crash — synth generator backfills."""
    report = backtest_disruption_forecast(history=None)
    assert report.n_observations > 0
    assert len(report.scorecards) >= 1

    report_empty = backtest_disruption_forecast(history=[])
    assert report_empty.n_observations > 0


# ── 3. Numeric bounds ─────────────────────────────────────────────────────

def test_mae_is_nonnegative() -> None:
    """MAE is an absolute error — must always be >= 0."""
    report = backtest_disruption_forecast()
    for sc in report.scorecards:
        assert sc.mae_7d >= 0.0
        assert sc.mae_30d >= 0.0
    assert report.mean_mae_7d >= 0.0
    assert report.mean_mae_30d >= 0.0


def test_sign_agreement_rate_in_unit_interval() -> None:
    """Sign-agreement is a fraction; must sit in [0, 1]."""
    report = backtest_disruption_forecast()
    for sc in report.scorecards:
        assert 0.0 <= sc.sign_agreement_7d <= 1.0
        assert 0.0 <= sc.sign_agreement_30d <= 1.0
    assert 0.0 <= report.mean_sign_agreement_7d <= 1.0
    assert 0.0 <= report.mean_sign_agreement_30d <= 1.0


# ── 4. Best / worst pointers + ranking sanity ──────────────────────────────

def test_best_and_worst_route_pointers_are_valid() -> None:
    """best/worst must reference actual route ids; best route's combined
    MAE must be <= worst route's combined MAE."""
    report = backtest_disruption_forecast()
    by_route = {sc.route_id: sc for sc in report.scorecards}
    assert report.best_route in by_route
    assert report.worst_route in by_route
    best  = by_route[report.best_route]
    worst = by_route[report.worst_route]
    assert (best.mae_7d + best.mae_30d) <= (worst.mae_7d + worst.mae_30d)


# ── 5. Determinism end-to-end ─────────────────────────────────────────────

def test_backtest_is_deterministic_across_runs() -> None:
    """Same seed → byte-identical scorecards."""
    a = backtest_disruption_forecast(seed=99)
    b = backtest_disruption_forecast(seed=99)
    a_keys = {(s.route_id, round(s.mae_7d, 6), round(s.mae_30d, 6),
               round(s.sign_agreement_7d, 6),
               round(s.sign_agreement_30d, 6))
              for s in a.scorecards}
    b_keys = {(s.route_id, round(s.mae_7d, 6), round(s.mae_30d, 6),
               round(s.sign_agreement_7d, 6),
               round(s.sign_agreement_30d, 6))
              for s in b.scorecards}
    assert a_keys == b_keys
    assert a.summary == b.summary


# ── 6. Hand-built history yields exact arithmetic ─────────────────────────

def test_backtest_perfect_forecasts_score_zero_mae_and_one_sign_agreement() -> None:
    """A history where forecast == realized exactly must score MAE=0 and
    sign-agreement=1.0 (assuming neither delta is zero, which the helper
    skips)."""
    history = [
        {"route_id": "R", "current_stress": 0.30,
         "forecast_7d":  0.40, "forecast_30d":  0.50,
         "realized_7d":  0.40, "realized_30d":  0.50},
        {"route_id": "R", "current_stress": 0.50,
         "forecast_7d":  0.45, "forecast_30d":  0.40,
         "realized_7d":  0.45, "realized_30d":  0.40},
    ]
    report = backtest_disruption_forecast(history=history)
    assert len(report.scorecards) == 1
    sc = report.scorecards[0]
    assert sc.mae_7d == 0.0
    assert sc.mae_30d == 0.0
    assert sc.sign_agreement_7d == 1.0
    assert sc.sign_agreement_30d == 1.0


def test_backtest_systematic_wrong_direction_scores_zero_sign_agreement() -> None:
    """A history where every forecast points OPPOSITE to the realized
    direction must score sign-agreement = 0 at that horizon."""
    history = [
        {"route_id": "R", "current_stress": 0.50,
         "forecast_7d":  0.60,   # forecast UP from current 0.50
         "forecast_30d": 0.60,
         "realized_7d":  0.40,   # but realized went DOWN
         "realized_30d": 0.40},
        {"route_id": "R", "current_stress": 0.50,
         "forecast_7d":  0.40,   # forecast DOWN
         "forecast_30d": 0.40,
         "realized_7d":  0.60,   # realized went UP
         "realized_30d": 0.60},
    ]
    report = backtest_disruption_forecast(history=history)
    sc = report.scorecards[0]
    assert sc.sign_agreement_7d == 0.0
    assert sc.sign_agreement_30d == 0.0
