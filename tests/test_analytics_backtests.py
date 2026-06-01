"""Defining-property tests for processing/analytics_backtests.py.

Pins the world-graph centrality-dominance validator's contract:

  * A PERFECT star (hubness=1.0) must put the hub at strict-max
    betweenness on every run, fragment on hub removal, and pass.
  * A HUBLESS ring (hubness=0.0) has a flat betweenness spectrum — no
    dominant node — so the dominance property collapses by construction.
  * The synthetic generator is deterministic for a fixed seed.
"""
from __future__ import annotations

from processing.analytics_backtests import (
    synthesize_star_graph,
    validate_graph_centrality_dominance,
)
from processing.world_graph_metrics import betweenness_centrality


def test_perfect_star_dominance_passes() -> None:
    """A perfect star (hubness=1.0) puts the hub 'C' at strict-max
    betweenness + fragments on removal — the validator must pass and
    every per-run hub must be 'C'."""
    result = validate_graph_centrality_dominance()
    assert result["passed"] is True
    assert all(pr["hub"] == "C" for pr in result["per_run"])


def test_hubless_ring_collapses() -> None:
    """A hubless symmetric ring (hubness=0.0) wires every leaf into a
    cycle with no spokes — the ring nodes share one betweenness value
    (a flat spectrum), so there is no dominant node and the dominance
    property collapses by construction. (The centre 'C' remains in the
    node list as an isolated 0.0 vertex — it carries no spokes at
    hubness=0 — so the equal-value check is over the ring leaves.)"""
    nodes, edges = synthesize_star_graph(n_leaves=6, hubness=0.0)
    btw = betweenness_centrality(nodes, edges)
    # Ring leaves all share one betweenness value → no strict-dominant node.
    ring_values = {round(v, 6) for k, v in btw.items() if k != "C"}
    assert len(ring_values) == 1


def test_synth_deterministic() -> None:
    """Same seed → byte-identical synthesized graph."""
    a = synthesize_star_graph(n_leaves=6, hubness=1.0, seed=20260601)
    b = synthesize_star_graph(n_leaves=6, hubness=1.0, seed=20260601)
    assert a == b
