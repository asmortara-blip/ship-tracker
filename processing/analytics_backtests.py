"""processing/analytics_backtests.py — validators for the 5 analytics modules.

Three of the new analytics modules ship with testable stability
properties suitable for the unified backtest gate. Each validator
returns the canonical dict shape the ``tools.backtests`` adapters
consume: ``{n_runs, ..., passed, summary, per_run}``.

  * **Cargo-flow JSD stability** — verifies the Jensen-Shannon
    divergence between an identical mix and itself is exactly 0, and
    that a known mix-swap produces a JSD within a tight tolerance
    of the expected value. A guard against silent regressions in the
    distance metric.
  * **Capacity-vs-demand persistence** — verifies that a synthetic
    surplus-then-balanced history correctly flags as alert-worthy
    when the surplus fraction exceeds the persistence threshold, and
    NOT alert-worthy when below.
  * **Port spillover graph recall** — generates a synthetic
    history where port B reliably follows port A within lag=2; the
    validator confirms the A→B edge is recovered with lift > 1.0 and
    the correct support fraction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


__all__ = [
    "validate_cargo_flow_jsd_stability",
    "validate_capacity_demand_persistence",
    "validate_spillover_graph_recall",
]


# ---------------------------------------------------------------------------
# Cargo-flow JSD stability
# ---------------------------------------------------------------------------


def validate_cargo_flow_jsd_stability(
    *,
    n_runs: int = 5,
    seed: int = 20260526,
    pass_threshold: float = 0.95,
) -> dict[str, Any]:
    """Verify Jensen-Shannon divergence behaves under known transforms.

    Per run:
      * Identity check: JSD(p, p) must equal 0.0.
      * Known swap: build two distributions {A:p, B:1-p} and {A:1-p, B:p}
        and check JSD lands within ±0.05 of the analytic value (for
        p=0.7 / 1-p=0.3, JSD ≈ 0.118 in log base 2).
      * Disjoint support: JSD({A:1}, {B:1}) must equal 1.0.

    A run "passes" when all three checks succeed.
    """
    from processing.cargo_flow_anomaly import jensen_shannon_divergence

    n_runs = max(1, int(n_runs))
    per_run: list[dict[str, Any]] = []
    passes = 0

    for i in range(n_runs):
        p_value = 0.7 + (i * 0.01)   # tiny variation per run for deterministic spread
        p_value = min(0.9, p_value)
        q_value = 1.0 - p_value

        # 1. Identity
        identity_p = {"A": p_value, "B": q_value}
        jsd_identity = jensen_shannon_divergence(identity_p, identity_p)
        identity_ok = abs(jsd_identity) < 1e-9

        # 2. Known swap — JSD of two symmetrically-swapped distributions
        # has a closed form: JSD = H((p+q)/2) - (H(p) + H(q))/2 in bits.
        # For [0.7, 0.3] vs [0.3, 0.7], the value is ~0.118.
        swap_a = {"A": p_value, "B": q_value}
        swap_b = {"A": q_value, "B": p_value}
        jsd_swap = jensen_shannon_divergence(swap_a, swap_b)
        # Closed-form expected
        def _h(x: float) -> float:
            if x <= 0 or x >= 1:
                return 0.0
            return -(x * math.log(x, 2) + (1 - x) * math.log(1 - x, 2))
        h_avg = _h(0.5)   # midpoint distribution
        expected = h_avg - 0.5 * (_h(p_value) + _h(q_value))
        swap_ok = abs(jsd_swap - expected) < 0.01

        # 3. Disjoint
        jsd_disjoint = jensen_shannon_divergence({"A": 1.0}, {"B": 1.0})
        disjoint_ok = abs(jsd_disjoint - 1.0) < 0.01

        all_three_pass = identity_ok and swap_ok and disjoint_ok
        if all_three_pass:
            passes += 1
        per_run.append({
            "run_index":     i,
            "identity_ok":   identity_ok,
            "swap_ok":       swap_ok,
            "disjoint_ok":   disjoint_ok,
            "jsd_swap":      round(jsd_swap, 4),
            "expected_swap": round(expected, 4),
            "passed":        all_three_pass,
        })

    pass_rate = passes / n_runs if n_runs else 0.0
    passed = pass_rate >= pass_threshold

    return {
        "n_runs":         n_runs,
        "passes":         passes,
        "pass_rate":      round(pass_rate, 4),
        "pass_threshold": float(pass_threshold),
        "passed":         bool(passed),
        "per_run":        per_run,
        "summary": (
            f"Cargo-flow JSD stability: {passes}/{n_runs} runs passed "
            f"(identity + swap + disjoint checks); pass rate "
            f"{pass_rate * 100:.0f}% (threshold {pass_threshold * 100:.0f}%)"
        ),
    }


# ---------------------------------------------------------------------------
# Capacity-vs-demand persistence
# ---------------------------------------------------------------------------


def validate_capacity_demand_persistence(
    *,
    n_runs: int = 5,
    seed: int = 20260526,
    pass_threshold: float = 0.95,
) -> dict[str, Any]:
    """Verify persistent divergence detection on synthetic histories.

    Per run:
      * Build a 10-day surplus history (capacity=1500, demand=1000) and
        verify it lands as ``capacity_surplus`` + ``alert_worthy=True``.
      * Build a 10-day balanced history (capacity=demand=1000) and
        verify it lands as ``balanced`` + ``alert_worthy=False``.

    A run passes when both expectations hold.
    """
    from processing.capacity_demand_divergence import (
        compute_route_divergence,
        summarize_persistent_divergence,
    )

    n_runs = max(1, int(n_runs))
    per_run: list[dict[str, Any]] = []
    passes = 0

    for i in range(n_runs):
        # Slight per-run perturbation so the synth isn't byte-identical
        # across runs — captures non-determinism if anything sneaks in.
        capacity_lift = 500 + (i * 10)
        surplus_points = [
            compute_route_divergence(
                route_id="surplus_route", date_iso=f"d{j}",
                capacity_teu=1000.0 + capacity_lift, demand_teu=1000.0,
            )
            for j in range(10)
        ]
        balanced_points = [
            compute_route_divergence(
                route_id="balanced_route", date_iso=f"d{j}",
                capacity_teu=1000.0, demand_teu=1000.0,
            )
            for j in range(10)
        ]
        surplus_r = summarize_persistent_divergence(surplus_points)
        balanced_r = summarize_persistent_divergence(balanced_points)

        surplus_ok = (
            surplus_r.direction == "capacity_surplus"
            and surplus_r.is_alert_worthy is True
        )
        balanced_ok = (
            balanced_r.direction == "balanced"
            and balanced_r.is_alert_worthy is False
        )
        both_ok = surplus_ok and balanced_ok
        if both_ok:
            passes += 1
        per_run.append({
            "run_index":           i,
            "surplus_direction":   surplus_r.direction,
            "surplus_band":        surplus_r.divergence_band,
            "surplus_alert":       surplus_r.is_alert_worthy,
            "balanced_direction":  balanced_r.direction,
            "balanced_alert":      balanced_r.is_alert_worthy,
            "passed":              both_ok,
        })

    pass_rate = passes / n_runs if n_runs else 0.0
    passed = pass_rate >= pass_threshold

    return {
        "n_runs":         n_runs,
        "passes":         passes,
        "pass_rate":      round(pass_rate, 4),
        "pass_threshold": float(pass_threshold),
        "passed":         bool(passed),
        "per_run":        per_run,
        "summary": (
            f"Capacity-demand persistence: {passes}/{n_runs} runs "
            f"correctly classified surplus + balanced histories; pass "
            f"rate {pass_rate * 100:.0f}% (threshold {pass_threshold * 100:.0f}%)"
        ),
    }


# ---------------------------------------------------------------------------
# Port spillover graph recall
# ---------------------------------------------------------------------------


def validate_spillover_graph_recall(
    *,
    n_runs: int = 5,
    seed: int = 20260526,
    pass_threshold: float = 0.95,
) -> dict[str, Any]:
    """Verify the spillover graph recovers a planted A → B edge.

    Per run:
      * Build a synthetic snapshot history where port A enters deficit
        on days {1, 4} and port B follows within lag=2 on days {2, 5}.
      * Run build_spillover_graph and assert an A → B edge exists with
        support == 1.0 and lift > 1.0.

    A run passes when the edge is correctly recovered.
    """
    from dataclasses import dataclass as _dc
    from processing.port_spillover_graph import build_spillover_graph

    @_dc
    class _Row:
        locode: str
        supply_deficit_days: float

    def _day(*pairs):
        return [_Row(loc, dd) for loc, dd in pairs]

    n_runs = max(1, int(n_runs))
    per_run: list[dict[str, Any]] = []
    passes = 0

    for i in range(n_runs):
        history = [
            _day(("A", +2.0), ("B", +2.0), ("C", +2.0)),
            _day(("A", -1.0), ("B", +2.0), ("C", +2.0)),   # A enters day 1
            _day(("A", -1.0), ("B", -1.0), ("C", +2.0)),   # B follows day 2
            _day(("A", +2.0), ("B", +2.0), ("C", +2.0)),
            _day(("A", -1.0), ("B", +2.0), ("C", +2.0)),   # A enters day 4
            _day(("A", -1.0), ("B", -1.0), ("C", +2.0)),   # B follows day 5
        ]
        graph = build_spillover_graph(
            history,
            lag_within_days=2, min_co_occurrences=2, min_lift=1.0,
        )
        ab_edge = next(
            (e for e in graph.edges
             if e.source_locode == "A" and e.target_locode == "B"),
            None,
        )
        edge_recovered = ab_edge is not None
        support_ok = bool(
            ab_edge is not None and abs(ab_edge.support - 1.0) < 1e-6
        )
        lift_ok = bool(ab_edge is not None and ab_edge.lift > 1.0)
        all_three = edge_recovered and support_ok and lift_ok
        if all_three:
            passes += 1
        per_run.append({
            "run_index":      i,
            "edge_recovered": edge_recovered,
            "support_ok":     support_ok,
            "lift_ok":        lift_ok,
            "lift":           round(ab_edge.lift, 4) if ab_edge else None,
            "passed":         all_three,
        })

    pass_rate = passes / n_runs if n_runs else 0.0
    passed = pass_rate >= pass_threshold

    return {
        "n_runs":         n_runs,
        "passes":         passes,
        "pass_rate":      round(pass_rate, 4),
        "pass_threshold": float(pass_threshold),
        "passed":         bool(passed),
        "per_run":        per_run,
        "summary": (
            f"Spillover graph recall: {passes}/{n_runs} runs correctly "
            f"recovered planted A→B edge with support=1.0 + lift>1.0; "
            f"pass rate {pass_rate * 100:.0f}% (threshold {pass_threshold * 100:.0f}%)"
        ),
    }
