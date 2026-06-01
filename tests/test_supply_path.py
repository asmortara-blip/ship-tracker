"""Defining-property tests for processing/supply_path.py.

Two test surfaces:

  1. **The real graph** — :func:`build_world_graph` is network-free for the
     structural backbone, so we assert the *modeled* directional facts that
     must hold: upstream of a company reaches commodities (the
     ``commodity_drives_company`` edge, reversed); downstream of a port reaches
     companies it exposes.
  2. **Tiny hand-built graphs** — small :class:`WorldGraph` instances with
     known topology let us assert exact paths, exact hop bounds, exact cycle
     termination, and the precise Sankey shape.

The tests check defining PROPERTIES (reachability, bounds, termination, array
invariants), not brittle exact counts on the real registries.
"""
from __future__ import annotations

import pytest

from processing.supply_path import (
    PathLink,
    SupplyPathTree,
    rank_supply_dependencies,
    supply_paths_between,
    to_sankey,
    trace_downstream,
    trace_upstream,
)
from processing.world_graph import (
    WorldEdge,
    WorldGraph,
    WorldNode,
    build_world_graph,
    node_id,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_graph() -> WorldGraph:
    """The real world graph (vessels off → smaller, still network-free)."""
    return build_world_graph(include_vessels=False)


def _node(nid: str) -> WorldNode:
    ntype, _, key = nid.partition(":")
    return WorldNode(node_id=nid, node_type=ntype, label=key.upper(), attrs={})


def _line_graph() -> WorldGraph:
    """A directed line: A -> B -> C -> D (all directed, weight encodes hop).

    Exercises forward traversal, path-finding, and hop bounds with zero
    ambiguity.
    """
    ids = ["commodity:A", "port:B", "route:C", "company:D"]
    nodes = [_node(i) for i in ids]
    edges = [
        WorldEdge("commodity:A", "port:B", "e1", weight=1.0, directed=True),
        WorldEdge("port:B", "route:C", "e2", weight=2.0, directed=True),
        WorldEdge("route:C", "company:D", "e3", weight=3.0, directed=True),
    ]
    return WorldGraph(nodes=nodes, edges=edges)


def _cyclic_graph() -> WorldGraph:
    """A directed cycle A -> B -> C -> A, plus a tail C -> D.

    Used to prove traversal terminates on cycles.
    """
    ids = ["port:A", "port:B", "port:C", "company:D"]
    nodes = [_node(i) for i in ids]
    edges = [
        WorldEdge("port:A", "port:B", "cyc", weight=1.0, directed=True),
        WorldEdge("port:B", "port:C", "cyc", weight=1.0, directed=True),
        WorldEdge("port:C", "port:A", "cyc", weight=1.0, directed=True),
        WorldEdge("port:C", "company:D", "tail", weight=1.0, directed=True),
    ]
    return WorldGraph(nodes=nodes, edges=edges)


def _disconnected_graph() -> WorldGraph:
    """Two components: A->B and X->Y, with no link between them."""
    ids = ["port:A", "company:B", "port:X", "company:Y"]
    nodes = [_node(i) for i in ids]
    edges = [
        WorldEdge("port:A", "company:B", "e", weight=1.0, directed=True),
        WorldEdge("port:X", "company:Y", "e", weight=1.0, directed=True),
    ]
    return WorldGraph(nodes=nodes, edges=edges)


# ── 1. Directional reachability on the real graph ─────────────────────────


def _a_company(graph: WorldGraph) -> str:
    """A company id that actually has a commodity_drives_company edge into it."""
    drivers = {
        e.target for e in graph.edges
        if e.edge_type == "commodity_drives_company"
    }
    assert drivers, "no commodity_drives_company edges in the real graph"
    return sorted(drivers)[0]


def _a_port_with_company(graph: WorldGraph) -> str:
    """A port id that exposes at least one company."""
    sources = {
        e.source for e in graph.edges
        if e.edge_type == "port_exposes_company"
    }
    assert sources, "no port_exposes_company edges in the real graph"
    return sorted(sources)[0]


def test_upstream_of_company_includes_a_commodity(real_graph: WorldGraph) -> None:
    """Upstream of a company must reach >=1 commodity.

    ``commodity_drives_company`` points commodity -> company; following edges
    INTO the company (upstream) must therefore surface its driving commodities.
    """
    company = _a_company(real_graph)
    tree = trace_upstream(real_graph, company, max_hops=3)
    reached = tree.reached()
    commodities = {n for n in reached if n.startswith("commodity:")}
    assert commodities, f"upstream of {company} reached no commodity: {reached}"
    # Root is never in its own reached set.
    assert company not in reached


def test_downstream_of_port_includes_a_company(real_graph: WorldGraph) -> None:
    """Downstream of a port must reach >=1 company it exposes.

    ``port_exposes_company`` points port -> company; following edges OUT of the
    port (downstream) must surface those companies.
    """
    port = _a_port_with_company(real_graph)
    tree = trace_downstream(real_graph, port, max_hops=2)
    reached = tree.reached()
    companies = {n for n in reached if n.startswith("company:")}
    assert companies, f"downstream of {port} reached no company: {reached}"
    assert port not in reached


def test_upstream_direct_commodity_at_hop_one(real_graph: WorldGraph) -> None:
    """A commodity driving a company is a DIRECT (hop-1) upstream dependency."""
    company = _a_company(real_graph)
    tree = trace_upstream(real_graph, company, max_hops=3)
    hop1 = tree.reached_by_hop.get(1, set())
    assert any(n.startswith("commodity:") for n in hop1), (
        f"no commodity at hop 1 upstream of {company}: {hop1}"
    )


def test_real_graph_traces_terminate(real_graph: WorldGraph) -> None:
    """The real graph has cycles (port->company plus other lanes); a trace from
    every node must terminate and stay within bounds."""
    for nid in real_graph.node_ids():
        up = trace_upstream(real_graph, nid, max_hops=3)
        down = trace_downstream(real_graph, nid, max_hops=3)
        assert nid not in up.reached()
        assert nid not in down.reached()
        for hop in list(up.reached_by_hop) + list(down.reached_by_hop):
            assert 1 <= hop <= 3


# ── 2. Cycle safety on a hand-built cyclic graph ───────────────────────────


def test_cyclic_graph_does_not_hang_and_is_bounded() -> None:
    g = _cyclic_graph()
    # Downstream from A around the A->B->C->A cycle (+ tail to D).
    tree = trace_downstream(g, "port:A", max_hops=10)
    reached = tree.reached()
    # Only B, C, D are reachable forward; A is the root (excluded). The cycle
    # must NOT cause A to reappear or the traversal to loop.
    assert reached == {"port:B", "port:C", "company:D"}
    assert "port:A" not in reached
    # Each non-root node discovered exactly once → one link each.
    assert len(tree.links) == 3
    # And paths over a cycle terminate too.
    paths = supply_paths_between(g, "port:A", "company:D", max_hops=10)
    assert paths == [["port:A", "port:B", "port:C", "company:D"]]


def test_cycle_self_reachability_excluded_both_directions() -> None:
    g = _cyclic_graph()
    up = trace_upstream(g, "port:A", max_hops=10)
    # Upstream of A around the reversed cycle reaches C and B but not itself.
    assert "port:A" not in up.reached()
    assert {"port:B", "port:C"} <= up.reached()


# ── 3. Paths between nodes ─────────────────────────────────────────────────


def test_paths_between_finds_known_multi_hop_path() -> None:
    g = _line_graph()
    paths = supply_paths_between(g, "commodity:A", "company:D", max_hops=4)
    assert paths == [["commodity:A", "port:B", "route:C", "company:D"]]


def test_paths_between_empty_when_disconnected() -> None:
    g = _disconnected_graph()
    assert supply_paths_between(g, "port:A", "company:Y", max_hops=4) == []
    # ... but the connected pair within a component is found.
    assert supply_paths_between(g, "port:A", "company:B", max_hops=4) == [
        ["port:A", "company:B"],
    ]


def test_paths_between_empty_for_missing_or_equal_nodes() -> None:
    g = _line_graph()
    assert supply_paths_between(g, "port:NOPE", "company:D", max_hops=4) == []
    assert supply_paths_between(g, "commodity:A", "port:NOPE", max_hops=4) == []
    assert supply_paths_between(g, "commodity:A", "commodity:A", max_hops=4) == []


def test_paths_between_respects_hop_bound() -> None:
    """The A->B->C->D path is length 3; a 2-hop budget must exclude it."""
    g = _line_graph()
    assert supply_paths_between(g, "commodity:A", "company:D", max_hops=2) == []
    assert supply_paths_between(g, "commodity:A", "route:C", max_hops=2) == [
        ["commodity:A", "port:B", "route:C"],
    ]


def test_paths_between_direction_is_forward_only() -> None:
    """Directed edges are not traversable backwards by supply_paths_between."""
    g = _line_graph()
    # D is downstream of A; there is no forward path D -> A.
    assert supply_paths_between(g, "company:D", "commodity:A", max_hops=4) == []


# ── 4. Sankey shaping ──────────────────────────────────────────────────────


def test_to_sankey_arrays_aligned_and_in_range(real_graph: WorldGraph) -> None:
    company = _a_company(real_graph)
    tree = trace_upstream(real_graph, company, max_hops=3)
    sk = to_sankey(tree, real_graph)
    assert set(sk) == {"labels", "source", "target", "value"}
    n = len(sk["source"])
    # Equal-length link arrays.
    assert len(sk["target"]) == n
    assert len(sk["value"]) == n
    # It is a non-trivial tree.
    assert n == len(tree.links) > 0
    # Every index points at a real label.
    n_labels = len(sk["labels"])
    for i in sk["source"] + sk["target"]:
        assert 0 <= i < n_labels
    # Every flow value is positive (default 1.0 floor).
    assert all(v > 0 for v in sk["value"])
    # Labels are the human labels, count == distinct nodes in the links.
    distinct = set()
    for link in tree.links:
        distinct.add(link.parent)
        distinct.add(link.child)
    assert n_labels == len(distinct)


def test_to_sankey_uses_graph_labels() -> None:
    g = _line_graph()
    tree = trace_downstream(g, "commodity:A", max_hops=3)
    sk = to_sankey(tree, g)
    # Hand graph labels are the upper-cased keys (see _node).
    assert set(sk["labels"]) == {"A", "B", "C", "D"}
    # Weights flow through as edge weights (1,2,3 on the line).
    assert sorted(sk["value"]) == [1.0, 2.0, 3.0]


def test_to_sankey_accepts_raw_links_without_graph() -> None:
    links = [
        PathLink("a", "b", "e", 2.0, 1),
        PathLink("b", "c", "e", 0.0, 2),  # zero weight → floored to 1.0
    ]
    sk = to_sankey(links)
    # No graph → labels are the ids themselves.
    assert sk["labels"] == ["a", "b", "c"]
    assert sk["source"] == [0, 1]
    assert sk["target"] == [1, 2]
    assert sk["value"] == [2.0, 1.0]


def test_to_sankey_empty_input() -> None:
    sk = to_sankey(SupplyPathTree(root="x", direction="upstream"))
    assert sk == {"labels": [], "source": [], "target": [], "value": []}
    assert to_sankey([]) == {"labels": [], "source": [], "target": [], "value": []}


def test_to_sankey_nonfinite_weight_floored() -> None:
    links = [
        PathLink("a", "b", "e", float("nan"), 1),
        PathLink("a", "c", "e", float("inf"), 1),
        PathLink("a", "d", "e", -5.0, 1),
    ]
    sk = to_sankey(links)
    assert sk["value"] == [1.0, 1.0, 1.0]


# ── 5. max_hops is respected ───────────────────────────────────────────────


def test_max_hops_respected_on_line() -> None:
    g = _line_graph()
    # From A, only B (hop 1) within 1 hop.
    t1 = trace_downstream(g, "commodity:A", max_hops=1)
    assert t1.reached() == {"port:B"}
    assert set(t1.reached_by_hop) == {1}
    # Within 2 hops: B, C.
    t2 = trace_downstream(g, "commodity:A", max_hops=2)
    assert t2.reached() == {"port:B", "route:C"}
    assert max(t2.reached_by_hop) == 2
    # Within 3 hops: the whole line.
    t3 = trace_downstream(g, "commodity:A", max_hops=3)
    assert t3.reached() == {"port:B", "route:C", "company:D"}
    assert max(t3.reached_by_hop) == 3


def test_max_hops_zero_or_negative_yields_empty() -> None:
    g = _line_graph()
    assert trace_downstream(g, "commodity:A", max_hops=0).reached() == set()
    assert trace_upstream(g, "company:D", max_hops=-3).reached() == set()


def test_hop_counts_never_exceed_max_on_real_graph(real_graph: WorldGraph) -> None:
    company = _a_company(real_graph)
    for mh in (1, 2, 3):
        tree = trace_upstream(real_graph, company, max_hops=mh)
        if tree.reached_by_hop:
            assert max(tree.reached_by_hop) <= mh
        for link in tree.links:
            assert 1 <= link.hop <= mh


# ── 6. Dependency ranking ──────────────────────────────────────────────────


def test_rank_supply_dependencies_orders_by_modeled_weight() -> None:
    """Hop-1 high-weight deps outrank hop-2 / lower-weight ones."""
    g = _line_graph()
    # Upstream of D: route:C (hop1, w3), port:B (hop2, w2), commodity:A (hop3, w1).
    ranked = rank_supply_dependencies(g, "company:D", max_hops=3)
    ids = [nid for nid, _ in ranked]
    assert ids[0] == "route:C"  # nearest + heaviest
    assert "company:D" not in ids  # root excluded
    assert set(ids) == {"route:C", "port:B", "commodity:A"}
    # Scores strictly descending.
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_supply_dependencies_real_graph_nonempty(real_graph: WorldGraph) -> None:
    company = _a_company(real_graph)
    ranked = rank_supply_dependencies(real_graph, company, max_hops=3)
    assert ranked, "expected modeled upstream dependencies for a real company"
    # Deterministic: descending score, ties broken by id ascending.
    for (n1, s1), (n2, s2) in zip(ranked, ranked[1:]):
        assert (s1 > s2) or (s1 == s2 and n1 < n2)
    assert company not in {nid for nid, _ in ranked}


def test_rank_empty_for_leaf_with_no_upstream() -> None:
    g = _line_graph()
    # commodity:A has nothing feeding it.
    assert rank_supply_dependencies(g, "commodity:A", max_hops=3) == []
