"""Defining-property tests for processing/ssi_component_validation.py.

The validator answers a question the SSI's static COMPONENT_WEIGHTS
cannot: of the six components, which actually predict forward freight-
rate moves? These tests pin the validator's contract: it must produce a
deterministic, bounded scorecard for every component, and the synthetic
generator must seed strong components to score higher than weak ones.
"""
from __future__ import annotations

import pytest

from processing.shipping_stress_index import COMPONENT_WEIGHTS
from processing.ssi_component_validation import (
    ComponentScorecard,
    ComponentValidationReport,
    synthesize_component_history,
    validate_ssi_components,
)


# ── 1. Synthetic history is deterministic ─────────────────────────────────

def test_synthesize_component_history_is_deterministic() -> None:
    """Same seed → byte-identical history. The whole backtest's
    reproducibility rides on this."""
    a = synthesize_component_history(n_days=60, seed=42)
    b = synthesize_component_history(n_days=60, seed=42)
    assert a == b


def test_synthesize_component_history_shape() -> None:
    """Each row carries scores for every SSI component + a realized move."""
    rows = synthesize_component_history(n_days=40, seed=42)
    assert len(rows) == 40
    for row in rows:
        assert "component_scores" in row
        assert "realized_move_pct" in row
        assert set(row["component_scores"].keys()) == set(COMPONENT_WEIGHTS.keys())
        for v in row["component_scores"].values():
            assert 0.0 <= v <= 1.0


# ── 2. Validator returns a scorecard per component ────────────────────────

def test_validate_returns_one_scorecard_per_component() -> None:
    report = validate_ssi_components()
    assert isinstance(report, ComponentValidationReport)
    component_keys = {sc.component for sc in report.scorecards}
    assert component_keys == set(COMPONENT_WEIGHTS.keys())


def test_validate_uses_synthetic_history_when_none_given() -> None:
    """Empty / None history must not crash — synth generator backfills it."""
    report = validate_ssi_components(history=None)
    assert report.n_observations > 0
    assert len(report.scorecards) == len(COMPONENT_WEIGHTS)

    report_empty = validate_ssi_components(history=[])
    assert report_empty.n_observations > 0


# ── 3. Numeric ranges are honoured (bounded outputs) ──────────────────────

def test_correlation_is_in_unit_interval() -> None:
    """Pearson r must always sit in [-1, 1] regardless of input quirks."""
    report = validate_ssi_components()
    for sc in report.scorecards:
        assert -1.0 <= sc.correlation <= 1.0


def test_sign_agreement_rate_in_unit_interval() -> None:
    """Sign-agreement is a fraction; must sit in [0, 1]."""
    report = validate_ssi_components()
    for sc in report.scorecards:
        assert 0.0 <= sc.sign_agreement_rate <= 1.0
        # And the edge is sa - 0.5, so it stays in [-0.5, 0.5]
        assert -0.5 <= sc.edge <= 0.5
        assert abs(sc.edge - (sc.sign_agreement_rate - 0.5)) < 1e-9


def test_scorecards_carry_component_weight() -> None:
    """Each scorecard surfaces the SSI weight for cross-reference."""
    report = validate_ssi_components()
    for sc in report.scorecards:
        assert sc.weight == COMPONENT_WEIGHTS[sc.component]


# ── 4. Synthetic-history truth ranking holds (the load-bearing property) ──

def test_strong_components_outscore_weak_components() -> None:
    """The synthetic generator seeds chokepoint at the highest "truth"
    coefficient and anomaly at the lowest. Across the default 120-day
    window the validator must score chokepoint ABOVE anomaly on
    sign-agreement — if it doesn't, the generator and validator have
    drifted apart and any UI surfacing this report is misleading.
    """
    report = validate_ssi_components()
    sa = {sc.component: sc.sign_agreement_rate for sc in report.scorecards}
    assert sa["chokepoint"] > sa["anomaly"], (
        f"chokepoint ({sa['chokepoint']:.3f}) should outscore "
        f"anomaly ({sa['anomaly']:.3f}) on the seeded synthetic history"
    )


def test_best_and_worst_are_drawn_from_components() -> None:
    """best/worst pointers must reference real component keys, and the
    best must be at least as predictive as the worst."""
    report = validate_ssi_components()
    keys = set(COMPONENT_WEIGHTS.keys())
    assert report.best_component in keys
    assert report.worst_component in keys
    by_name = {sc.component: sc for sc in report.scorecards}
    assert (
        by_name[report.best_component].sign_agreement_rate
        >= by_name[report.worst_component].sign_agreement_rate
    )


# ── 5. Determinism end-to-end ─────────────────────────────────────────────

def test_validator_is_deterministic_across_runs() -> None:
    """Two runs with the same seed must produce byte-identical scorecards."""
    a = validate_ssi_components(seed=99)
    b = validate_ssi_components(seed=99)
    assert a.summary == b.summary
    assert a.best_component == b.best_component
    a_by = {sc.component: (sc.correlation, sc.sign_agreement_rate)
            for sc in a.scorecards}
    b_by = {sc.component: (sc.correlation, sc.sign_agreement_rate)
            for sc in b.scorecards}
    assert a_by == b_by


# ── 6. Custom history is honoured ─────────────────────────────────────────

def test_validator_honours_caller_supplied_history() -> None:
    """When a real history is passed in, the validator uses it (not the
    synthetic backfill). Smoke test with a tiny hand-built series where
    one component cleanly leads the move and another opposes it."""
    history = [
        {"component_scores": {"chokepoint": 0.20, "congestion": 0.50,
                              "weather":    0.50, "rate":       0.50,
                              "vulnerability": 0.50, "anomaly":  0.50},
         "realized_move_pct": -1.0},
        {"component_scores": {"chokepoint": 0.30, "congestion": 0.50,
                              "weather":    0.50, "rate":       0.50,
                              "vulnerability": 0.50, "anomaly":  0.50},
         "realized_move_pct": 0.8},
        {"component_scores": {"chokepoint": 0.50, "congestion": 0.50,
                              "weather":    0.50, "rate":       0.50,
                              "vulnerability": 0.50, "anomaly":  0.50},
         "realized_move_pct": 1.4},
        {"component_scores": {"chokepoint": 0.65, "congestion": 0.50,
                              "weather":    0.50, "rate":       0.50,
                              "vulnerability": 0.50, "anomaly":  0.50},
         "realized_move_pct": 1.9},
    ]
    report = validate_ssi_components(history=history)
    assert report.n_observations == 4
    sa = {sc.component: sc.sign_agreement_rate for sc in report.scorecards}
    # chokepoint moves up monotonically with positive realized moves →
    # sign-agreement on chokepoint should be 1.0 (every delta is positive
    # and every move is positive in rows 2, 3, 4; rows 1's first delta
    # is positive and move is positive → all hits).
    assert sa["chokepoint"] == 1.0
