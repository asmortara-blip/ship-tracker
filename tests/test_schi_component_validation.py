"""Defining-property tests for engine/schi_component_validation.py.

Symmetric companion to test_ssi_component_validation.py. Pins the SCHI
validator's contract + verifies the canonical SCHI dimension list stays
in sync with engine.supply_chain_health.
"""
from __future__ import annotations

import pytest

from engine.schi_component_validation import (
    REDUNDANCY_THRESHOLD,
    SCHI_DIMENSION_WEIGHTS,
    SCHI_DIMENSIONS,
    CollinearityReport,
    ComponentScorecard,
    HorizonDecayReport,
    ValidationReport,
    compute_schi_collinearity,
    synthesize_dimension_history,
    validate_schi_components,
    validate_schi_horizons,
)


# ── 1. Sanity: canonical dimension list matches engine.supply_chain_health ─

def test_dimension_weights_match_supply_chain_health() -> None:
    """Drift catch: SCHI_DIMENSION_WEIGHTS must match the private _WEIGHTS
    in engine.supply_chain_health. If the source weights change without
    this list being updated, the validator's report becomes misleading
    (it would still ship a 'weight' column that no longer matches reality).
    """
    from engine.supply_chain_health import _WEIGHTS as live_weights

    assert SCHI_DIMENSION_WEIGHTS == live_weights, (
        "SCHI_DIMENSION_WEIGHTS in engine.schi_component_validation has "
        "drifted from engine.supply_chain_health._WEIGHTS — update the "
        "validator's canonical list."
    )
    # Also pin the dimension ordering matches the keys.
    assert tuple(SCHI_DIMENSIONS) == tuple(SCHI_DIMENSION_WEIGHTS.keys())


def test_schi_weights_sum_to_one() -> None:
    """Sanity check on the weight schema itself."""
    assert abs(sum(SCHI_DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9


# ── 2. Synthetic history shape + determinism ───────────────────────────────

def test_synth_history_is_deterministic() -> None:
    a = synthesize_dimension_history(n_days=60, seed=42)
    b = synthesize_dimension_history(n_days=60, seed=42)
    assert a == b


def test_synth_history_row_keys() -> None:
    rows = synthesize_dimension_history(n_days=30)
    for row in rows:
        assert "dimension_scores" in row
        assert "realized_move_pct" in row
        assert set(row["dimension_scores"].keys()) == set(SCHI_DIMENSIONS)
        for v in row["dimension_scores"].values():
            assert 0.0 <= v <= 1.0


# ── 3. validate_schi_components contract ──────────────────────────────────

def test_validate_returns_one_scorecard_per_dimension() -> None:
    report = validate_schi_components()
    assert isinstance(report, ValidationReport)
    assert {sc.component for sc in report.scorecards} == set(SCHI_DIMENSIONS)


def test_validate_uses_synth_when_history_empty() -> None:
    a = validate_schi_components(history=None)
    b = validate_schi_components(history=[])
    assert a.n_observations > 0
    assert b.n_observations > 0


def test_correlation_in_unit_interval() -> None:
    report = validate_schi_components()
    for sc in report.scorecards:
        assert -1.0 <= sc.correlation <= 1.0


def test_sign_agreement_in_unit_interval() -> None:
    report = validate_schi_components()
    for sc in report.scorecards:
        assert 0.0 <= sc.sign_agreement_rate <= 1.0
        assert abs(sc.edge - (sc.sign_agreement_rate - 0.5)) < 1e-9


def test_scorecards_carry_dimension_weight() -> None:
    report = validate_schi_components()
    for sc in report.scorecards:
        assert sc.weight == SCHI_DIMENSION_WEIGHTS[sc.component]


def test_strong_dimensions_outscore_weak_dimensions() -> None:
    """The synth seeds port_capacity at truth=0.80 and seasonal_factors at
    truth=0.20. The validator must reflect that ordering on the default
    120-day window — otherwise the generator and validator have drifted."""
    report = validate_schi_components()
    sa = {sc.component: sc.sign_agreement_rate for sc in report.scorecards}
    assert sa["port_capacity"] > sa["seasonal_factors"]


def test_validator_deterministic_across_runs() -> None:
    a = validate_schi_components(seed=99)
    b = validate_schi_components(seed=99)
    a_by = {sc.component: (sc.correlation, sc.sign_agreement_rate)
            for sc in a.scorecards}
    b_by = {sc.component: (sc.correlation, sc.sign_agreement_rate)
            for sc in b.scorecards}
    assert a_by == b_by


# ── 4. validate_schi_horizons contract ────────────────────────────────────

def test_horizons_returns_cell_per_dimension_per_horizon() -> None:
    report = validate_schi_horizons(horizons=(1, 7, 30))
    assert isinstance(report, HorizonDecayReport)
    pairs = {(c.component, c.horizon_days) for c in report.cells}
    expected = {(d, h) for d in SCHI_DIMENSIONS for h in (1, 7, 30)}
    assert pairs == expected


def test_horizons_dedupes_and_sorts() -> None:
    report = validate_schi_horizons(horizons=[30, 1, 7, 30, 1])
    assert report.horizons == [1, 7, 30]


def test_horizons_clamps_to_minimum_one() -> None:
    report = validate_schi_horizons(horizons=[0, -3, 5])
    assert report.horizons == [1, 5]


def test_horizons_rates_grid_shape() -> None:
    report = validate_schi_horizons(horizons=(1, 14, 60))
    grid = report.rates_grid()
    assert len(grid) == len(report.components)
    for row in grid:
        assert len(row) == len(report.horizons)
        for v in row:
            assert 0.0 <= v <= 1.0


def test_horizons_n_observations_decrease_with_horizon() -> None:
    report = validate_schi_horizons(horizons=(1, 30, 60))
    by_h_n = {c.horizon_days: c.n_observations for c in report.cells}
    assert by_h_n[1] >= by_h_n[30] >= by_h_n[60]


# ── 5. compute_schi_collinearity contract ──────────────────────────────────

def test_collinearity_returns_one_pair_per_combination() -> None:
    """6 dimensions → C(6, 2) = 15 pairs."""
    report = compute_schi_collinearity()
    assert isinstance(report, CollinearityReport)
    assert len(report.pairs) == 15


def test_collinearity_pair_correlations_in_unit_interval() -> None:
    report = compute_schi_collinearity()
    for pair in report.pairs:
        assert -1.0 <= pair.correlation <= 1.0


def test_collinearity_corr_matrix_symmetric_with_diagonal_one() -> None:
    report = compute_schi_collinearity()
    matrix = report.corr_matrix()
    n = len(report.components)
    assert len(matrix) == n
    for i in range(n):
        assert len(matrix[i]) == n
        assert matrix[i][i] == 1.0
        for j in range(n):
            assert abs(matrix[i][j] - matrix[j][i]) < 1e-9


def test_collinearity_runs_cleanly_on_synth() -> None:
    """Random-walk drift can show spurious correlations; the analyzer
    must still produce a well-formed report."""
    report = compute_schi_collinearity()
    assert len(report.components) == 6
    assert len(report.pairs) == 15
    assert isinstance(report.summary, str) and report.summary
