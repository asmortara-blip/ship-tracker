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
    REDUNDANCY_THRESHOLD,
    CollinearityReport,
    ComponentPair,
    ComponentScorecard,
    ComponentValidationReport,
    HorizonDecayReport,
    HorizonScorecard,
    compute_component_collinearity,
    synthesize_component_history,
    validate_ssi_components,
    validate_ssi_horizons,
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


# ── 7. Horizon-decay scan ─────────────────────────────────────────────────

def test_validate_horizons_returns_cell_per_component_per_horizon() -> None:
    """``validate_ssi_horizons`` must produce one HorizonScorecard cell
    for every (component, horizon) pair."""
    report = validate_ssi_horizons(horizons=(1, 7, 30))
    assert isinstance(report, HorizonDecayReport)
    assert len(report.cells) == len(COMPONENT_WEIGHTS) * 3
    pairs = {(c.component, c.horizon_days) for c in report.cells}
    expected = {(comp, h)
                for comp in COMPONENT_WEIGHTS
                for h in (1, 7, 30)}
    assert pairs == expected


def test_validate_horizons_dedupes_and_sorts_horizons() -> None:
    """Duplicate / out-of-order horizons must be de-duped and sorted."""
    report = validate_ssi_horizons(horizons=[30, 1, 7, 30, 1])
    assert report.horizons == [1, 7, 30]


def test_validate_horizons_clamps_horizons_at_minimum_one() -> None:
    """Zero / negative horizons must be clamped up to 1."""
    report = validate_ssi_horizons(horizons=[0, -3, 5])
    assert report.horizons == [1, 5]


def test_validate_horizons_rates_grid_shape_matches() -> None:
    """``rates_grid()`` materialises a components × horizons 2D list with
    every value in [0, 1]."""
    report = validate_ssi_horizons(horizons=(1, 14, 60))
    grid = report.rates_grid()
    assert len(grid) == len(report.components)
    for row in grid:
        assert len(row) == len(report.horizons)
        for v in row:
            assert 0.0 <= v <= 1.0


def test_validate_horizons_best_horizon_pointer_is_valid() -> None:
    """The best-horizon pointer must be one of the requested horizons,
    and the mean sign-agreement at that horizon must be at least as high
    as the mean at any other horizon."""
    report = validate_ssi_horizons(horizons=(1, 7, 14, 30))
    assert report.best_horizon_overall in report.horizons
    # Compute mean per horizon and verify best is the argmax.
    by_h: dict[int, list[float]] = {h: [] for h in report.horizons}
    for cell in report.cells:
        by_h[cell.horizon_days].append(cell.sign_agreement_rate)
    means = {h: sum(v) / len(v) for h, v in by_h.items()}
    best_mean = means[report.best_horizon_overall]
    assert best_mean == max(means.values())


def test_validate_horizons_observations_decrease_with_horizon() -> None:
    """For a fixed-length history, n_observations per cell must be
    non-increasing as the horizon grows (longer lookahead = fewer
    usable pairs)."""
    report = validate_ssi_horizons(horizons=(1, 30, 60))
    by_h_n: dict[int, int] = {}
    for cell in report.cells:
        # All cells at the same horizon share the same n_observations
        by_h_n[cell.horizon_days] = cell.n_observations
    assert by_h_n[1] >= by_h_n[30] >= by_h_n[60]


def test_validate_horizons_is_deterministic() -> None:
    """Same seed → same scorecard cells across runs."""
    a = validate_ssi_horizons(horizons=(1, 7, 30), seed=7)
    b = validate_ssi_horizons(horizons=(1, 7, 30), seed=7)
    a_keys = {(c.component, c.horizon_days, round(c.sign_agreement_rate, 6))
              for c in a.cells}
    b_keys = {(c.component, c.horizon_days, round(c.sign_agreement_rate, 6))
              for c in b.cells}
    assert a_keys == b_keys


# ── 8. Component collinearity analyzer ────────────────────────────────────

def _hist_with(component_series: dict[str, list[float]]) -> list[dict]:
    """Helper: build a history from per-component series of equal length."""
    components = list(component_series.keys())
    n = len(next(iter(component_series.values()), []))
    return [
        {
            "component_scores": {c: component_series[c][i] for c in components},
            "realized_move_pct": 0.0,
        }
        for i in range(n)
    ]


def test_collinearity_returns_one_pair_per_unique_combination() -> None:
    """Six components → C(6, 2) = 15 unordered pairs."""
    report = compute_component_collinearity()
    assert isinstance(report, CollinearityReport)
    assert len(report.pairs) == 15


def test_collinearity_pair_correlations_in_unit_interval() -> None:
    """Every pair's correlation must sit in [-1, 1]."""
    report = compute_component_collinearity()
    for pair in report.pairs:
        assert -1.0 <= pair.correlation <= 1.0


def test_collinearity_corr_matrix_is_symmetric_and_diagonal_one() -> None:
    """The N×N matrix must be symmetric with 1.0 on the diagonal."""
    report = compute_component_collinearity()
    matrix = report.corr_matrix()
    n = len(report.components)
    assert len(matrix) == n
    for i in range(n):
        assert len(matrix[i]) == n
        assert matrix[i][i] == 1.0
        for j in range(n):
            assert abs(matrix[i][j] - matrix[j][i]) < 1e-9


def test_collinearity_detects_identical_series_as_redundant() -> None:
    """Two identical component series must read r = 1.0 and be flagged."""
    # Use a hand-built history with two perfectly-correlated components.
    base = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70]
    series = {
        "chokepoint":    base,
        "congestion":    base,         # identical to chokepoint → r = +1.0
        "weather":       [v + 0.05 for v in base],  # also positive but offset
        "rate":          list(reversed(base)),       # opposite → r = -1.0
        "vulnerability": [0.5] * 7,                   # flat → r undefined → 0.0
        "anomaly":       [0.3, 0.7, 0.2, 0.8, 0.4, 0.6, 0.5],  # noisy
    }
    report = compute_component_collinearity(history=_hist_with(series))
    by_pair = {
        tuple(sorted([p.component_a, p.component_b])): p
        for p in report.pairs
    }
    # Identical → exactly +1.0; reversed → exactly -1.0
    chokepoint_congestion = by_pair[("chokepoint", "congestion")]
    chokepoint_rate       = by_pair[("chokepoint", "rate")]
    chokepoint_weather    = by_pair[("chokepoint", "weather")]
    chokepoint_vuln       = by_pair[("chokepoint", "vulnerability")]
    assert abs(chokepoint_congestion.correlation - 1.0) < 1e-9
    assert abs(chokepoint_rate.correlation - (-1.0)) < 1e-9
    # weather is a +offset of chokepoint → also r = +1.0 (linear shift)
    assert abs(chokepoint_weather.correlation - 1.0) < 1e-9
    # flat series → correlation undefined → 0.0
    assert chokepoint_vuln.correlation == 0.0

    # Redundancy flag should fire for the |r| = 1.0 cases.
    assert chokepoint_congestion.redundant is True
    assert chokepoint_rate.redundant is True
    # Identical AND reversed pairs should both be flagged.
    assert any(p.redundant for p in report.redundant_pairs
               if {p.component_a, p.component_b} == {"chokepoint", "rate"})


def test_collinearity_runs_cleanly_on_synth() -> None:
    """The synth produces independent random walks per component, but
    cumulative random walks on a 120-day window can drift together by
    chance — the analyzer must run cleanly and produce a well-formed
    report regardless of whether spurious correlations show up.
    """
    report = compute_component_collinearity()
    # Well-formed report shape — 6 components, C(6, 2) = 15 unordered pairs
    assert len(report.components) == 6
    assert len(report.pairs) == 15
    # n_observations is consistent across every pair (same history)
    n_obs = {p.n_observations for p in report.pairs}
    assert len(n_obs) == 1
    # Summary surfaces a sentence either way
    assert isinstance(report.summary, str) and len(report.summary) > 0


def test_collinearity_is_deterministic() -> None:
    """Same seed → byte-identical pair correlations."""
    a = compute_component_collinearity(seed=11)
    b = compute_component_collinearity(seed=11)
    a_map = {(p.component_a, p.component_b): round(p.correlation, 6)
             for p in a.pairs}
    b_map = {(p.component_a, p.component_b): round(p.correlation, 6)
             for p in b.pairs}
    assert a_map == b_map
