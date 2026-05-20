"""Tests for engine.fleet_utilization.

Two layers:
  1. Snapshot computation against lightweight Voyage stand-ins so the logic
     is exercised without paying the cost of building the full modeled fleet.
  2. Walk-forward backtest validated by injecting a known lag relationship
     between utilization and rate, then asserting the model recovers it.

All RNG seeding is via explicit integers — no `hash()` (process-salted).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from engine.fleet_utilization import (
    DEFAULT_CANDIDATE_LAGS,
    FleetUtilizationReport,
    RouteFleetMetrics,
    UtilizationBacktest,
    _CLASSIFICATION_SLACK,
    _CLASSIFICATION_TIGHT,
    classify_utilization,
    compute_fleet_utilization,
    walk_forward_backtest,
)


# ─── Voyage stand-in ────────────────────────────────────────────────────────

@dataclass
class _FakeVoyage:
    """Minimal duck-typed stand-in for data.voyage_dataset.Voyage."""
    route_id: str
    status: str = "On Schedule"
    progress_pct: float = 0.5
    delay_days: float = 0.0
    congestion_at_dest: float = 0.3


def _make_route_voyages(
    route_id: str,
    n_active: int = 5,
    n_arrived: int = 2,
    progress: float = 0.5,
    delay: float = 0.0,
    dest_cong: float = 0.3,
) -> list[_FakeVoyage]:
    """Build a deterministic list of voyages for one route."""
    active = [
        _FakeVoyage(
            route_id=route_id,
            status="On Schedule",
            progress_pct=progress,
            delay_days=delay,
            congestion_at_dest=dest_cong,
        )
        for _ in range(n_active)
    ]
    arrived = [
        _FakeVoyage(
            route_id=route_id,
            status="Arrived",
            progress_pct=1.0,
            delay_days=0.0,
            congestion_at_dest=dest_cong,
        )
        for _ in range(n_arrived)
    ]
    return active + arrived


# ─── classify_utilization ───────────────────────────────────────────────────

def test_classify_utilization_buckets() -> None:
    assert classify_utilization(0.0) == "Slack"
    assert classify_utilization(_CLASSIFICATION_SLACK - 0.01) == "Slack"
    assert classify_utilization(_CLASSIFICATION_SLACK) == "Balanced"
    assert classify_utilization((_CLASSIFICATION_SLACK + _CLASSIFICATION_TIGHT) / 2) == "Balanced"
    assert classify_utilization(_CLASSIFICATION_TIGHT) == "Tight"
    assert classify_utilization(1.0) == "Tight"


# ─── compute_fleet_utilization — structural ─────────────────────────────────

def test_empty_fleet_returns_well_formed_report() -> None:
    report = compute_fleet_utilization([])
    assert isinstance(report, FleetUtilizationReport)
    assert report.routes == []
    assert report.fleet_utilization == 0.0
    assert report.fleet_classification == "Slack"
    assert report.voyages_total == 0
    assert report.voyages_active == 0


def test_route_metrics_shape_and_bounds() -> None:
    fleet = _make_route_voyages("transpacific_eb", n_active=5, n_arrived=2)
    report = compute_fleet_utilization(fleet)
    assert len(report.routes) == 1
    rm = report.routes[0]
    assert isinstance(rm, RouteFleetMetrics)
    assert rm.route_id == "transpacific_eb"
    assert rm.voyages_total == 7
    assert rm.voyages_active == 5
    assert 0.0 <= rm.active_share <= 1.0
    assert 0.0 <= rm.utilization_score <= 1.0
    assert rm.classification in {"Tight", "Balanced", "Slack"}


def test_voyages_without_route_id_are_skipped() -> None:
    fleet = [
        _FakeVoyage(route_id="", status="On Schedule"),  # skipped
        _FakeVoyage(route_id="r1", status="On Schedule"),
        _FakeVoyage(route_id="r1", status="Arrived"),
    ]
    report = compute_fleet_utilization(fleet)
    assert len(report.routes) == 1
    assert report.routes[0].route_id == "r1"
    assert report.routes[0].voyages_total == 2


def test_all_arrived_route_scores_zero_and_slack() -> None:
    fleet = [
        _FakeVoyage(route_id="r1", status="Arrived"),
        _FakeVoyage(route_id="r1", status="Arrived"),
    ]
    report = compute_fleet_utilization(fleet)
    rm = report.routes[0]
    assert rm.voyages_active == 0
    assert rm.utilization_score == 0.0
    assert rm.classification == "Slack"


# ─── compute_fleet_utilization — monotonicity ───────────────────────────────

def test_higher_lock_in_yields_higher_score() -> None:
    """Holding everything else constant, voyages with lower progress (more
    capacity locked in) should produce a higher utilization score."""
    fleet_low_lock = _make_route_voyages("r1", n_active=5, progress=0.9, delay=0, dest_cong=0)
    fleet_high_lock = _make_route_voyages("r1", n_active=5, progress=0.1, delay=0, dest_cong=0)
    score_low = compute_fleet_utilization(fleet_low_lock).fleet_utilization
    score_high = compute_fleet_utilization(fleet_high_lock).fleet_utilization
    assert score_high > score_low


def test_higher_delay_yields_higher_score() -> None:
    fleet_calm = _make_route_voyages("r1", n_active=5, progress=0.5, delay=0)
    fleet_late = _make_route_voyages("r1", n_active=5, progress=0.5, delay=14)
    score_calm = compute_fleet_utilization(fleet_calm).fleet_utilization
    score_late = compute_fleet_utilization(fleet_late).fleet_utilization
    assert score_late > score_calm


def test_higher_forward_congestion_yields_higher_score() -> None:
    fleet_clear = _make_route_voyages("r1", n_active=5, progress=0.5, delay=0, dest_cong=0.1)
    fleet_jam = _make_route_voyages("r1", n_active=5, progress=0.5, delay=0, dest_cong=0.9)
    score_clear = compute_fleet_utilization(fleet_clear).fleet_utilization
    score_jam = compute_fleet_utilization(fleet_jam).fleet_utilization
    assert score_jam > score_clear


def test_higher_active_share_yields_higher_score() -> None:
    fleet_idle = _make_route_voyages("r1", n_active=2, n_arrived=8, progress=0.5)
    fleet_busy = _make_route_voyages("r1", n_active=8, n_arrived=2, progress=0.5)
    score_idle = compute_fleet_utilization(fleet_idle).fleet_utilization
    score_busy = compute_fleet_utilization(fleet_busy).fleet_utilization
    assert score_busy > score_idle


# ─── Fleet rollup: voyage-weighted ──────────────────────────────────────────

def test_fleet_rollup_is_voyage_weighted_across_routes() -> None:
    """Two routes with different score and different voyage counts — the
    fleet-wide rollup must weight by voyage count, not by route count."""
    # Route A: 20 voyages all active at low progress (high score)
    route_a = _make_route_voyages("a", n_active=20, n_arrived=0, progress=0.1, delay=10, dest_cong=0.8)
    # Route B: 2 voyages all arrived (zero score)
    route_b = _make_route_voyages("b", n_active=0, n_arrived=2)
    report = compute_fleet_utilization(route_a + route_b)

    score_a = next(r.utilization_score for r in report.routes if r.route_id == "a")
    score_b = next(r.utilization_score for r in report.routes if r.route_id == "b")
    expected = (score_a * 20 + score_b * 2) / 22
    assert report.fleet_utilization == pytest.approx(round(expected, 4), abs=1e-3)


# ─── Live integration: real Voyage fleet ─────────────────────────────────────

def test_compute_on_real_voyage_fleet_is_sane() -> None:
    """Sanity-check on the modeled voyage dataset — fleet score finite,
    classification valid, voyage counts non-negative and consistent."""
    from data.voyage_dataset import build_voyage_fleet

    fleet = build_voyage_fleet(seed=20260520, per_route=(3, 5))
    report = compute_fleet_utilization(fleet)
    assert report.voyages_total == len(fleet)
    assert 0 <= report.voyages_active <= report.voyages_total
    assert 0.0 <= report.fleet_utilization <= 1.0
    assert report.fleet_classification in {"Tight", "Balanced", "Slack"}
    assert math.isfinite(report.fleet_utilization)
    # The per-voyage counts add up.
    assert sum(r.voyages_total for r in report.routes) == report.voyages_total
    assert sum(r.voyages_active for r in report.routes) == report.voyages_active


# ─── Walk-forward backtest ──────────────────────────────────────────────────

def _make_lagged_pair(
    n: int = 400,
    true_lag: int = 7,
    r_target: float = 0.75,
    seed: int = 31,
) -> tuple[pd.Series, pd.Series]:
    """Synthetic utilization / rate pair where rate trails utilization by
    ``true_lag`` days on log returns, with controllable signal-to-noise."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    util_returns = rng.normal(0.0, 0.04, size=n)
    util_path = np.cumsum(util_returns)
    util_values = 0.5 * np.exp(util_path - util_path.mean())

    noise = rng.normal(0.0, 0.04, size=n)
    rate_returns = np.zeros(n)
    rate_returns[:true_lag] = noise[:true_lag]
    rate_returns[true_lag:] = (
        r_target * util_returns[:-true_lag] + (1 - r_target) * noise[true_lag:]
    )
    rate_path = np.cumsum(rate_returns)
    rate_values = 2000.0 * np.exp(rate_path - rate_path.mean())

    return (
        pd.Series(util_values, index=dates, name="util"),
        pd.Series(rate_values, index=dates, name="rate"),
    )


def test_walk_forward_returns_finite_metrics() -> None:
    util, rate = _make_lagged_pair(n=400, true_lag=7, r_target=0.75, seed=41)
    bt = walk_forward_backtest(
        util, rate, train_window=90, test_window=14, step=14,
    )
    assert isinstance(bt, UtilizationBacktest)
    assert bt.n_windows > 0
    assert 0.0 <= bt.hit_rate <= 1.0
    assert math.isfinite(bt.avg_r_in_sample)
    assert math.isfinite(bt.avg_r_out_of_sample)
    assert bt.best_lag_days in DEFAULT_CANDIDATE_LAGS


def test_walk_forward_recovers_modal_lag() -> None:
    """When the true lag is 7 and signal is strong, the modal recovered lag
    should be 7 (or the nearest candidate)."""
    util, rate = _make_lagged_pair(n=500, true_lag=7, r_target=0.80, seed=51)
    bt = walk_forward_backtest(util, rate, train_window=120, test_window=14, step=10)
    assert bt.n_windows >= 5
    # Modal lag is the most common winner across windows; with r_target=0.8 it
    # should consistently land at 7.
    assert bt.best_lag_days == 7
    assert bt.avg_r_in_sample > 0.4


def test_walk_forward_beats_coin_flip_on_genuine_signal() -> None:
    util, rate = _make_lagged_pair(n=500, true_lag=7, r_target=0.80, seed=61)
    bt = walk_forward_backtest(util, rate, train_window=120, test_window=14, step=7)
    assert bt.n_windows >= 5
    assert bt.hit_rate >= 0.55


def test_walk_forward_empty_inputs() -> None:
    bt = walk_forward_backtest(pd.Series(dtype=float), pd.Series(dtype=float))
    assert bt.n_windows == 0
    assert bt.hit_rate == 0.0
    assert bt.best_lag_days == 0


def test_walk_forward_insufficient_history() -> None:
    util, rate = _make_lagged_pair(n=60, true_lag=7, r_target=0.75, seed=71)
    bt = walk_forward_backtest(util, rate, train_window=90, test_window=14)
    assert bt.n_windows == 0
