"""Defining-property tests for processing/world_graph_metrics.py.

Each metric is checked against closed-form values on graphs whose
centralities are known analytically (star, path, cycle, complete graph),
plus a numpy-eigh reference for eigenvector centrality, plus the degenerate
inputs the module promises to survive (empty / single node / disconnected /
self-loops / parallel edges).
"""
from __future__ import annotations

import numpy as np
import pytest

from processing.world_graph_metrics import (
    betweenness_centrality,
    degree_centrality,
    eigenvector_centrality,
    resilience_after_removal,
)


# ── Graph fixtures ──────────────────────────────────────────────────────


def _star(n_leaves: int) -> tuple[list[str], list[tuple[str, str, float]]]:
    nodes = ["C"] + [f"L{i}" for i in range(n_leaves)]
    edges = [("C", f"L{i}", 1.0) for i in range(n_leaves)]
    return nodes, edges


def _path(n: int) -> tuple[list[str], list[tuple[str, str, float]]]:
    nodes = [f"P{i}" for i in range(n)]
    edges = [(f"P{i}", f"P{i+1}", 1.0) for i in range(n - 1)]
    return nodes, edges


def _cycle(n: int) -> tuple[list[str], list[tuple[str, str, float]]]:
    nodes = [f"N{i}" for i in range(n)]
    edges = [(f"N{i}", f"N{(i+1) % n}", 1.0) for i in range(n)]
    return nodes, edges


def _complete(n: int) -> tuple[list[str], list[tuple[str, str, float]]]:
    nodes = [f"K{i}" for i in range(n)]
    edges = [
        (f"K{i}", f"K{j}", 1.0)
        for i in range(n)
        for j in range(i + 1, n)
    ]
    return nodes, edges


def _numpy_eigvec(nodes, edges, weighted=True) -> dict[str, float]:
    """Reference dominant eigenvector via numpy.linalg.eigh (ground truth)."""
    idx = {nd: i for i, nd in enumerate(nodes)}
    n = len(nodes)
    A = np.zeros((n, n))
    for e in edges:
        u, v = str(e[0]), str(e[1])
        if u == v:
            continue
        w = float(e[2]) if len(e) >= 3 else 1.0
        A[idx[u], idx[v]] = w if weighted else 1.0
        A[idx[v], idx[u]] = w if weighted else 1.0
    vals, vecs = np.linalg.eigh(A)
    dom = np.abs(vecs[:, int(np.argmax(vals))])
    nrm = np.linalg.norm(dom)
    if nrm > 0:
        dom = dom / nrm
    return {nd: float(dom[idx[nd]]) for nd in nodes}


# ════════════════════════════════════════════════════════════════════════
# degree_centrality
# ════════════════════════════════════════════════════════════════════════


def test_degree_star_center_is_one_leaves_are_inverse_nm1() -> None:
    nodes, edges = _star(5)
    d = degree_centrality(nodes, edges)
    assert d["C"] == pytest.approx(1.0)
    for i in range(5):
        assert d[f"L{i}"] == pytest.approx(1.0 / (len(nodes) - 1))


def test_degree_complete_graph_all_one() -> None:
    nodes, edges = _complete(5)
    d = degree_centrality(nodes, edges)
    # Every node connects to all n-1 others → normalised degree 1.0.
    for nd in nodes:
        assert d[nd] == pytest.approx(1.0)


def test_degree_path_interior_double_endpoints() -> None:
    nodes, edges = _path(5)
    d = degree_centrality(nodes, edges)
    assert d["P0"] == pytest.approx(0.25)   # 1 / (5-1)
    assert d["P2"] == pytest.approx(0.5)    # 2 / (5-1)


def test_degree_weighted_normalises_by_max() -> None:
    nodes = ["A", "B", "C"]
    edges = [("A", "B", 3.0), ("B", "C", 1.0)]
    d = degree_centrality(nodes, edges, weighted=True)
    # weighted degrees: A=3, B=4, C=1 → /max(4)
    assert d["B"] == pytest.approx(1.0)
    assert d["A"] == pytest.approx(3.0 / 4.0)
    assert d["C"] == pytest.approx(1.0 / 4.0)


def test_degree_parallel_edges_merge_by_sum_unweighted() -> None:
    # Two parallel A-B edges collapse to one neighbour relationship.
    nodes = ["A", "B", "C"]
    edges = [("A", "B", 1.0), ("A", "B", 1.0), ("B", "C", 1.0)]
    d = degree_centrality(nodes, edges)
    # A has 1 distinct neighbour (B); B has 2 (A, C).
    assert d["A"] == pytest.approx(0.5)
    assert d["B"] == pytest.approx(1.0)


def test_degree_self_loops_dropped() -> None:
    nodes = ["A", "B"]
    d = degree_centrality(nodes, [("A", "A", 1.0), ("A", "B", 1.0)])
    assert d == {"A": pytest.approx(1.0), "B": pytest.approx(1.0)}


# ════════════════════════════════════════════════════════════════════════
# eigenvector_centrality
# ════════════════════════════════════════════════════════════════════════


def test_eigvec_star_center_max_leaves_equal() -> None:
    nodes, edges = _star(5)
    e = eigenvector_centrality(nodes, edges)
    assert all(e["C"] > e[f"L{i}"] for i in range(5))
    leaves = {round(e[f"L{i}"], 9) for i in range(5)}
    assert len(leaves) == 1


def test_eigvec_star_center_leaf_ratio_is_sqrt_nleaves() -> None:
    # Closed form: for a star with k leaves, lambda = sqrt(k) and the
    # centre/leaf eigenvector ratio equals lambda = sqrt(k).
    nodes, edges = _star(5)
    e = eigenvector_centrality(nodes, edges)
    assert (e["C"] / e["L0"]) == pytest.approx(np.sqrt(5.0), abs=1e-4)


@pytest.mark.parametrize("builder", ["path", "cycle", "complete", "star"])
def test_eigvec_matches_numpy_reference(builder: str) -> None:
    nodes, edges = {
        "path": _path(7),
        "cycle": _cycle(6),
        "complete": _complete(5),
        "star": _star(6),
    }[builder]
    got = eigenvector_centrality(nodes, edges, max_iter=2000, tol=1e-12)
    ref = _numpy_eigvec(nodes, edges)
    for nd in nodes:
        assert got[nd] == pytest.approx(ref[nd], abs=1e-5)


def test_eigvec_cycle_is_uniform() -> None:
    # A cycle is vertex-transitive → every node has identical centrality.
    nodes, edges = _cycle(6)
    e = eigenvector_centrality(nodes, edges)
    vals = list(e.values())
    assert max(vals) - min(vals) < 1e-6
    assert e[nodes[0]] == pytest.approx(1.0 / np.sqrt(6), abs=1e-5)


def test_eigvec_no_edges_all_zero() -> None:
    e = eigenvector_centrality(["A", "B", "C"], [])
    assert e == {"A": 0.0, "B": 0.0, "C": 0.0}


def test_eigvec_disconnected_finite_nonneg_and_picks_denser_component() -> None:
    # Component 1 = triangle (A,B,C); component 2 = single edge (D,E).
    # The triangle has the larger spectral radius (2 > 1), so it dominates
    # and the D-E component is driven toward zero (documented behaviour).
    nodes = ["A", "B", "C", "D", "E"]
    edges = [("A", "B"), ("B", "C"), ("A", "C"), ("D", "E")]
    e = eigenvector_centrality(nodes, edges)
    assert all(np.isfinite(v) and v >= 0.0 for v in e.values())
    assert min(e["A"], e["B"], e["C"]) > max(e["D"], e["E"])


def test_eigvec_never_raises_and_returns_unit_vector_on_bipartite() -> None:
    # Complete bipartite K_{2,3} is bipartite (oscillation trap); the shift
    # must make it converge to a finite unit vector.
    nodes = ["a0", "a1", "b0", "b1", "b2"]
    edges = [(a, b) for a in ("a0", "a1") for b in ("b0", "b1", "b2")]
    e = eigenvector_centrality(nodes, edges)
    norm = np.sqrt(sum(v * v for v in e.values()))
    assert norm == pytest.approx(1.0, abs=1e-6)
    ref = _numpy_eigvec(nodes, edges)
    for nd in nodes:
        assert e[nd] == pytest.approx(ref[nd], abs=1e-4)


# ════════════════════════════════════════════════════════════════════════
# betweenness_centrality (Brandes)
# ════════════════════════════════════════════════════════════════════════


def test_betweenness_star_center_one_leaves_zero() -> None:
    nodes, edges = _star(5)
    b = betweenness_centrality(nodes, edges)
    assert b["C"] == pytest.approx(1.0)
    for i in range(5):
        assert b[f"L{i}"] == pytest.approx(0.0)


def test_betweenness_path_middle_highest_endpoints_zero() -> None:
    nodes, edges = _path(5)
    b = betweenness_centrality(nodes, edges)
    # Normalised closed form n=5: P2=4/6, P1=P3=3/6, P0=P4=0.
    assert b["P2"] == pytest.approx(4.0 / 6.0)
    assert b["P1"] == pytest.approx(3.0 / 6.0)
    assert b["P3"] == pytest.approx(3.0 / 6.0)
    assert b["P0"] == pytest.approx(0.0)
    assert b["P4"] == pytest.approx(0.0)
    assert b["P2"] > b["P1"] > b["P0"]


def test_betweenness_complete_graph_all_zero() -> None:
    # In a complete graph every pair is directly adjacent → no node lies
    # on any *other* pair's unique shortest path → betweenness 0 everywhere.
    nodes, edges = _complete(5)
    b = betweenness_centrality(nodes, edges)
    for nd in nodes:
        assert b[nd] == pytest.approx(0.0)


def test_betweenness_cycle_is_uniform() -> None:
    nodes, edges = _cycle(6)
    b = betweenness_centrality(nodes, edges)
    vals = list(b.values())
    assert max(vals) - min(vals) < 1e-9


def test_betweenness_unnormalized_star_raw_value() -> None:
    # Raw (un-normalised) undirected betweenness of a k=5 star centre is the
    # number of unordered leaf pairs = C(5,2) = 10.
    nodes, edges = _star(5)
    b = betweenness_centrality(nodes, edges, normalized=False)
    assert b["C"] == pytest.approx(10.0)


def test_betweenness_handles_disconnected_without_error() -> None:
    # Two disjoint paths; betweenness is computed per-component.
    nodes = ["A", "B", "C", "X", "Y", "Z"]
    edges = [("A", "B"), ("B", "C"), ("X", "Y"), ("Y", "Z")]
    b = betweenness_centrality(nodes, edges)
    # Each path's middle is the only positive node within its component.
    assert b["B"] > 0 and b["Y"] > 0
    assert b["A"] == pytest.approx(0.0) and b["Z"] == pytest.approx(0.0)


# ════════════════════════════════════════════════════════════════════════
# resilience_after_removal
# ════════════════════════════════════════════════════════════════════════


def test_resilience_remove_star_center_shatters_into_leaves() -> None:
    nodes, edges = _star(5)
    r = resilience_after_removal(nodes, edges, "C")
    assert r["n_removed"] == 1
    assert r["n_nodes_after"] == 5
    assert r["n_components"] == 5
    assert r["largest_component_size"] == 1
    assert r["largest_component_fraction"] == pytest.approx(0.2)
    assert r["component_sizes"] == [1, 1, 1, 1, 1]
    assert r["removed"] == ["C"]


def test_resilience_intact_graph_single_component() -> None:
    nodes, edges = _star(5)
    r = resilience_after_removal(nodes, edges, [])
    assert r["n_components"] == 1
    assert r["largest_component_fraction"] == pytest.approx(1.0)
    assert r["n_removed"] == 0


def test_resilience_remove_path_middle_splits_in_two() -> None:
    nodes, edges = _path(5)
    r = resilience_after_removal(nodes, edges, "P2")
    assert r["n_components"] == 2
    assert sorted(r["component_sizes"], reverse=True) == [2, 2]
    assert r["largest_component_fraction"] == pytest.approx(0.5)


def test_resilience_remove_multiple_nodes() -> None:
    # Removing both bridges of a path of 6 leaves fragments it.
    nodes, edges = _path(6)  # P0-P1-P2-P3-P4-P5
    r = resilience_after_removal(nodes, edges, ["P1", "P4"])
    assert r["n_removed"] == 2
    assert r["n_nodes_after"] == 4
    # Survivors: {P0}, {P2,P3}, {P5} → 3 components, largest size 2.
    assert r["n_components"] == 3
    assert r["largest_component_size"] == 2
    assert r["component_sizes"] == [2, 1, 1]


def test_resilience_remove_absent_node_is_ignored() -> None:
    nodes, edges = _path(4)
    r = resilience_after_removal(nodes, edges, ["NOPE", "ALSO_NOPE"])
    assert r["n_removed"] == 0
    assert r["n_nodes_after"] == 4
    assert r["n_components"] == 1
    assert r["removed"] == []


def test_resilience_remove_everything() -> None:
    nodes, edges = _star(3)
    r = resilience_after_removal(nodes, edges, nodes)
    assert r["n_nodes_after"] == 0
    assert r["n_components"] == 0
    assert r["largest_component_size"] == 0
    assert r["largest_component_fraction"] == pytest.approx(0.0)


def test_resilience_already_disconnected_graph() -> None:
    nodes = ["A", "B", "C", "D"]
    edges = [("A", "B"), ("C", "D")]
    r = resilience_after_removal(nodes, edges, [])
    assert r["n_components"] == 2
    assert r["largest_component_fraction"] == pytest.approx(0.5)


# ════════════════════════════════════════════════════════════════════════
# Cross-cutting degenerate inputs (must not raise)
# ════════════════════════════════════════════════════════════════════════


def test_empty_graph_all_functions() -> None:
    assert degree_centrality([], []) == {}
    assert eigenvector_centrality([], []) == {}
    assert betweenness_centrality([], []) == {}
    r = resilience_after_removal([], [], [])
    assert r["n_nodes_before"] == 0
    assert r["largest_component_fraction"] == pytest.approx(0.0)


def test_single_node_all_functions() -> None:
    assert degree_centrality(["A"], []) == {"A": 0.0}
    assert eigenvector_centrality(["A"], []) == {"A": 0.0}
    assert betweenness_centrality(["A"], []) == {"A": 0.0}
    r = resilience_after_removal(["A"], [], [])
    assert r["n_components"] == 1
    assert r["largest_component_fraction"] == pytest.approx(1.0)


def test_two_nodes_betweenness_zero() -> None:
    # n < 3 → no node can lie between a pair.
    b = betweenness_centrality(["A", "B"], [("A", "B")])
    assert b == {"A": 0.0, "B": 0.0}


def test_dangling_edge_endpoint_dropped() -> None:
    # Edge references a node not declared in `nodes` → silently dropped.
    d = degree_centrality(["A", "B"], [("A", "B"), ("A", "GHOST")])
    assert d["A"] == pytest.approx(1.0)
    assert d["B"] == pytest.approx(1.0)


def test_node_order_preserved_and_deduped() -> None:
    # Duplicate node ids collapse (first wins); dict order follows input.
    d = degree_centrality(["B", "A", "B"], [("A", "B")])
    assert list(d.keys()) == ["B", "A"]


def test_int_node_ids_coerced_to_str() -> None:
    # Callers may pass ints; everything keys on str.
    d = degree_centrality([1, 2, 3], [(1, 2, 1.0), (2, 3, 1.0)])
    assert d["2"] == pytest.approx(1.0)
    assert set(d.keys()) == {"1", "2", "3"}


def test_nonfinite_weight_falls_back_to_one() -> None:
    nodes = ["A", "B", "C"]
    edges = [("A", "B", float("nan")), ("B", "C", float("inf"))]
    # Both bad weights → 1.0; weighted degree B = 2.0 = max.
    d = degree_centrality(nodes, edges, weighted=True)
    assert d["B"] == pytest.approx(1.0)
    assert d["A"] == pytest.approx(0.5)
