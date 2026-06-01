"""Tests for processing/world_graph.py — the unified shipping world graph.

Runs against the real registries (ports/routes/chokepoints/companies/
commodities + the modeled voyage fleet), the same way the other port-supply
tests do. The builder is network-free (all local builders), so these are fast.
"""
from __future__ import annotations

import pytest

from processing.world_graph import (
    NODE_TYPES,
    WorldGraph,
    build_world_graph,
    node_id,
)


@pytest.fixture(scope="module")
def graph() -> WorldGraph:
    return build_world_graph()


def test_build_returns_world_graph(graph: WorldGraph) -> None:
    assert isinstance(graph, WorldGraph)
    assert graph.nodes and graph.edges


def test_all_six_node_types_present(graph: WorldGraph) -> None:
    """Every node type is represented (the graph is genuinely 'unified')."""
    present = {n.node_type for n in graph.nodes}
    assert present == set(NODE_TYPES), f"missing/extra types: {present ^ set(NODE_TYPES)}"
    by_type = graph.summary()["nodes_by_type"]
    # The structural backbone (everything but vessels) must be non-empty.
    for t in ("port", "route", "chokepoint", "company", "commodity"):
        assert by_type[t] > 0, f"no {t} nodes"


def test_node_ids_are_namespaced(graph: WorldGraph) -> None:
    """Every id is 'type:key' with a known type — so ids never collide."""
    for n in graph.nodes:
        prefix, sep, key = n.node_id.partition(":")
        assert sep == ":" and key, f"bad node_id: {n.node_id!r}"
        assert prefix == n.node_type and prefix in NODE_TYPES
    # ids are unique
    assert len(graph.node_ids()) == len(set(graph.node_ids()))


def test_no_dangling_edges(graph: WorldGraph) -> None:
    """Every edge endpoint resolves to a real node (the _add_edge guarantee)."""
    ids = set(graph.node_ids())
    bad = [(e.source, e.target) for e in graph.edges
           if e.source not in ids or e.target not in ids]
    assert not bad, f"dangling edges: {bad[:5]}"


def test_routes_connect_their_origin_and_dest_ports(graph: WorldGraph) -> None:
    """Each route has a route_origin + route_dest edge to an existing port."""
    port_ids = {n.node_id for n in graph.nodes_by_type("port")}
    routes = graph.nodes_by_type("route")
    assert routes
    by_route_origin = {e.source for e in graph.edges if e.edge_type == "route_origin"}
    by_route_dest = {e.source for e in graph.edges if e.edge_type == "route_dest"}
    for r in routes:
        assert r.node_id in by_route_origin and r.node_id in by_route_dest
    # ...and those edges land on real ports.
    for e in graph.edges:
        if e.edge_type in ("route_origin", "route_dest"):
            assert e.target in port_ids


def test_chokepoint_edges_only_reference_real_routes(graph: WorldGraph) -> None:
    """Phantom route ids in Chokepoint.affected_routes are filtered out."""
    route_ids = {n.node_id for n in graph.nodes_by_type("route")}
    constrains = [e for e in graph.edges if e.edge_type == "chokepoint_constrains"]
    assert constrains  # there ARE chokepoint→route edges
    for e in constrains:
        assert e.target in route_ids, f"phantom route target {e.target}"


def test_include_vessels_false_drops_vessels() -> None:
    g = build_world_graph(include_vessels=False)
    assert not g.nodes_by_type("vessel")
    assert not [e for e in g.edges if e.edge_type in ("vessel_sails", "vessel_transits")]
    # ...but the backbone survives.
    assert g.nodes_by_type("port") and g.nodes_by_type("route")


def test_adjacency_covers_every_node_and_is_undirected(graph: WorldGraph) -> None:
    adj = graph.adjacency(undirected=True)
    assert set(adj) == set(graph.node_ids())
    # undirected symmetry: if a in adj[b] then b in adj[a]
    for a, nbrs in adj.items():
        for b in nbrs:
            assert a in adj[b], f"asymmetric adjacency {a}<->{b}"


def test_blast_radius_grows_with_hops_and_contains_neighbors(graph: WorldGraph) -> None:
    """blast_radius(n, 2) ⊇ neighbors(n) ⊇ {} and excludes the node itself."""
    # Pick a well-connected node — a chokepoint constrains routes which touch ports…
    chokes = graph.nodes_by_type("chokepoint")
    assert chokes
    cp = next((c for c in chokes if graph.neighbors(c.node_id)), chokes[0])
    nbrs = graph.neighbors(cp.node_id)
    br1 = graph.blast_radius(cp.node_id, hops=1)
    br2 = graph.blast_radius(cp.node_id, hops=2)
    assert cp.node_id not in br2
    assert nbrs == br1                     # 1-hop blast == direct neighbours
    assert nbrs <= br2                     # 2-hop is a superset
    assert len(br2) >= len(br1)


def test_geo_nodes_have_coords_abstract_nodes_do_not(graph: WorldGraph) -> None:
    """Ports/chokepoints/vessels are geo-mappable; routes/companies/commodities
    are abstract (no coords)."""
    for n in graph.nodes:
        if n.node_type in ("company", "commodity", "route"):
            assert n.lat is None and n.lon is None
    # at least the ports + chokepoints carry coords
    assert all(p.lat is not None and p.lon is not None
               for p in graph.nodes_by_type("port"))
    assert all(c.lat is not None and c.lon is not None
               for c in graph.nodes_by_type("chokepoint"))


def test_node_id_helper() -> None:
    assert node_id("port", "CNSHA") == "port:CNSHA"
    assert node_id("chokepoint", "hormuz") == "chokepoint:hormuz"


def test_summary_shape(graph: WorldGraph) -> None:
    s = graph.summary()
    assert set(s) == {"n_nodes", "n_edges", "nodes_by_type", "edges_by_type",
                      "container_type"}
    assert s["n_nodes"] == len(graph.nodes)
    assert s["n_edges"] == len(graph.edges)
