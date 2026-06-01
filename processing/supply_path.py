"""processing/supply_path.py — "who-supplies-who, follow the path" tracing.

Directed supply-dependency tracing over the unified :mod:`processing.world_graph`.
The world graph already encodes the platform's typed entities (ports, routes,
chokepoints, vessels, companies, commodities) and the *directed* relationships
between them. This module walks those directed edges to answer two operator
questions:

  * **Upstream** — *what feeds this node?* Follow edges INTO the node (reverse
    direction). The upstream of a company is the commodities that drive it and
    the ports that expose it → the routes/chokepoints feeding those ports → and
    so on, hop by hop.
  * **Downstream** — *what does this node feed?* Follow edges OUT of the node.
    The downstream of a port is the companies it exposes and the commodities it
    carries → and onward.

It also finds bounded directed paths between two nodes, ranks a node's biggest
upstream dependencies, and reshapes any traced tree into the exact dict a
Plotly ``go.Sankey`` consumes.

HONESTY / WHAT THESE PATHS ARE NOT
----------------------------------
These linkages are **MODELED exposure relationships, not measured supply-chain
ground truth.** The platform's port↔company exposure weights and
commodity↔company drive weights are *illustrative* — derived from the modeled
footprint/exposure builders, not from verified bills of lading, supplier
disclosures, or shipment records. A traced "supply path" therefore shows how
exposure is *modeled to flow* through the graph, NOT a verified physical
who-ships-to-whom chain. Read every path as "under the platform's model, this
node's exposure is connected to these others", never as confirmed sourcing.

Design
------
* Pure functions, deterministic, stdlib + numpy only (no networkx, no I/O
  beyond optionally calling :func:`build_world_graph`). Matches the rest of
  ``processing/``.
* Cycle-safe: the world graph contains directed cycles (e.g.
  port→company and, via other lanes, paths that loop back), so every traversal
  tracks visited nodes and is bounded by ``max_hops``. Nothing here can hang.
* Edge direction is taken from :class:`processing.world_graph.WorldEdge`. An
  edge with ``directed=False`` (e.g. ``port_carries_commodity``) is treated as
  traversable in BOTH directions for upstream and downstream alike.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional, Union

from processing.world_graph import WorldGraph, build_world_graph


__all__ = [
    "PathLink",
    "SupplyPathTree",
    "trace_upstream",
    "trace_downstream",
    "supply_paths_between",
    "to_sankey",
    "rank_supply_dependencies",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathLink:
    """One directed link in a traced tree, oriented parent -> child.

    ``parent`` -> ``child`` always points in the direction of traversal as the
    UI would draw it (root at one side, dependencies fanning out). For an
    upstream trace the child is what *feeds* the parent; for a downstream trace
    the child is what the parent *feeds*. ``edge_type`` and ``weight`` are
    carried straight from the underlying :class:`WorldEdge`. ``hop`` is the
    distance from the root (the root's direct links are hop ``1``).
    """

    parent: str
    child: str
    edge_type: str
    weight: float
    hop: int


@dataclass
class SupplyPathTree:
    """A traced supply-dependency tree rooted at one node.

    Attributes
    ----------
    root:
        The node id the trace started from.
    direction:
        ``"upstream"`` (what feeds the root) or ``"downstream"`` (what the root
        feeds).
    links:
        The ``parent -> child`` :class:`PathLink` edges, in BFS order. A child
        is recorded the first time it is reached (shortest hop wins); the tree
        is therefore a spanning forest of first-discovery edges, never a
        re-expansion of an already-seen node.
    reached_by_hop:
        ``{hop: set(node_ids)}`` of nodes reached at exactly that hop distance.
        The root is excluded from every hop's set (a node never appears in its
        own reached set).
    max_hops:
        The bound the trace honoured.
    """

    root: str
    direction: str
    links: list[PathLink] = field(default_factory=list)
    reached_by_hop: dict[int, set[str]] = field(default_factory=dict)
    max_hops: int = 3

    # ── convenience views ───────────────────────────────────────────────────
    def reached(self) -> set[str]:
        """All node ids reached (any hop), excluding the root."""
        out: set[str] = set()
        for ids in self.reached_by_hop.values():
            out |= ids
        out.discard(self.root)
        return out

    def link_tuples(self) -> list[tuple[str, str]]:
        """``(parent, child)`` pairs — handy for assertions / quick rendering."""
        return [(l.parent, l.child) for l in self.links]


# ---------------------------------------------------------------------------
# Directed adjacency helpers
# ---------------------------------------------------------------------------


def _directed_adjacency(
    graph: WorldGraph,
) -> tuple[dict[str, list[WorldEdgeView]], dict[str, list[WorldEdgeView]]]:
    """Build forward + reverse directed adjacency over the graph's edges.

    Returns ``(out_adj, in_adj)`` where:
      * ``out_adj[u]`` lists edges usable when following OUT of ``u``
        (``u`` is the natural source, plus the other end of any undirected
        edge incident to ``u``).
      * ``in_adj[v]`` lists edges usable when following INTO ``v``
        (``v`` is the natural target, plus the other end of any undirected
        edge incident to ``v``).

    Each entry is a small view carrying the neighbour id + the edge's type and
    weight, oriented so the neighbour is the node you move TO. Self-loops are
    dropped. An undirected edge contributes to both directions on both ends so
    it is always traversable.
    """
    out_adj: dict[str, list[WorldEdgeView]] = defaultdict(list)
    in_adj: dict[str, list[WorldEdgeView]] = defaultdict(list)
    valid = set(graph.node_ids())
    for e in graph.edges:
        if e.source == e.target:
            continue
        if e.source not in valid or e.target not in valid:
            continue
        w = float(e.weight)
        # Following OUT of source lands on target.
        out_adj[e.source].append(WorldEdgeView(e.target, e.edge_type, w))
        # Following INTO target comes from source.
        in_adj[e.target].append(WorldEdgeView(e.source, e.edge_type, w))
        if not e.directed:
            # Undirected: also traversable the other way on both ends.
            out_adj[e.target].append(WorldEdgeView(e.source, e.edge_type, w))
            in_adj[e.source].append(WorldEdgeView(e.target, e.edge_type, w))
    return out_adj, in_adj


@dataclass(frozen=True)
class WorldEdgeView:
    """A direction-oriented edge view: where you move TO + the edge metadata."""

    neighbor: str
    edge_type: str
    weight: float


# ---------------------------------------------------------------------------
# Core traversal
# ---------------------------------------------------------------------------


def _trace(
    graph: WorldGraph,
    node_id: str,
    *,
    direction: str,
    max_hops: int,
) -> SupplyPathTree:
    """BFS one direction from ``node_id``, cycle-safe and hop-bounded.

    ``direction`` selects which adjacency to walk:
      * ``"upstream"``   → reverse edges (what FEEDS the root).
      * ``"downstream"`` → forward edges (what the root FEEDS).
    """
    out_adj, in_adj = _directed_adjacency(graph)
    adj = in_adj if direction == "upstream" else out_adj

    tree = SupplyPathTree(
        root=node_id, direction=direction, max_hops=int(max_hops),
    )
    # A node not in the graph simply yields an empty tree (never raises).
    if node_id not in set(graph.node_ids()):
        return tree

    hops = max(0, int(max_hops))
    visited: set[str] = {node_id}
    # frontier holds (node_id) at the current hop distance.
    frontier: list[str] = [node_id]
    for hop in range(1, hops + 1):
        nxt: list[str] = []
        reached_here: set[str] = set()
        for u in frontier:
            # Sort neighbours for deterministic link/visit ordering.
            for view in sorted(
                adj.get(u, ()), key=lambda v: (v.neighbor, v.edge_type),
            ):
                child = view.neighbor
                if child in visited:
                    # Cycle / re-convergence: record nothing, never re-expand.
                    continue
                visited.add(child)
                reached_here.add(child)
                tree.links.append(PathLink(
                    parent=u, child=child,
                    edge_type=view.edge_type, weight=view.weight, hop=hop,
                ))
                nxt.append(child)
        if reached_here:
            tree.reached_by_hop[hop] = reached_here
        if not nxt:
            break
        frontier = nxt
    return tree


def trace_upstream(
    graph: WorldGraph, node_id: str, *, max_hops: int = 3,
) -> SupplyPathTree:
    """Trace what FEEDS ``node_id`` — follow edges INTO it (reverse direction).

    E.g. the upstream of a company is the commodities that drive it and the
    ports that expose it (hop 1), then the routes/chokepoints feeding those
    ports and the routes those commodities flow on (hop 2), and so on. Bounded
    by ``max_hops`` and cycle-safe. See the module docstring: these are MODELED
    exposure links, not verified sourcing.
    """
    return _trace(graph, node_id, direction="upstream", max_hops=max_hops)


def trace_downstream(
    graph: WorldGraph, node_id: str, *, max_hops: int = 3,
) -> SupplyPathTree:
    """Trace what ``node_id`` FEEDS — follow edges OUT of it (forward direction).

    E.g. the downstream of a port is the companies it exposes and the
    commodities it carries (hop 1), then the companies those commodities drive
    and the routes they flow on (hop 2), and so on. Bounded by ``max_hops`` and
    cycle-safe.
    """
    return _trace(graph, node_id, direction="downstream", max_hops=max_hops)


# ---------------------------------------------------------------------------
# Paths between two nodes
# ---------------------------------------------------------------------------


def supply_paths_between(
    graph: WorldGraph, src_id: str, dst_id: str, *, max_hops: int = 4,
) -> list[list[str]]:
    """All directed node-id paths from ``src_id`` to ``dst_id``, bounded.

    Walks FORWARD directed edges (undirected edges are bidirectional). A "path"
    is a simple path (no repeated node), so cycles can never make it loop
    forever; ``max_hops`` additionally bounds the number of EDGES in any path.

    Returns
    -------
    list[list[str]]
        Each inner list is ``[src_id, ..., dst_id]``. De-duplicated and sorted
        for determinism. ``[]`` when ``src``/``dst`` are missing, equal, or no
        path within ``max_hops`` exists.
    """
    valid = set(graph.node_ids())
    if src_id not in valid or dst_id not in valid or src_id == dst_id:
        return []
    if int(max_hops) < 1:
        return []

    out_adj, _ = _directed_adjacency(graph)
    hops = int(max_hops)

    found: list[tuple[str, ...]] = []
    # Iterative DFS over simple paths; the on-path set prevents revisiting a
    # node within the SAME path (so cycles are harmless) while still allowing
    # different paths to share intermediate nodes.
    # stack frames: (current_node, path_so_far_tuple, on_path_set)
    stack: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
        (src_id, (src_id,), frozenset({src_id})),
    ]
    while stack:
        node, path, on_path = stack.pop()
        if len(path) - 1 >= hops:
            # No edge budget left to extend this path.
            continue
        for view in sorted(
            out_adj.get(node, ()), key=lambda v: (v.neighbor, v.edge_type),
        ):
            nxt = view.neighbor
            if nxt in on_path:
                continue  # simple-path constraint → cycle-safe
            new_path = path + (nxt,)
            if nxt == dst_id:
                found.append(new_path)
                # Do not extend past the destination.
                continue
            stack.append((nxt, new_path, on_path | {nxt}))

    # De-dup (different traversal orders can produce the same path) + sort.
    deduped = sorted(set(found))
    return [list(p) for p in deduped]


# ---------------------------------------------------------------------------
# Sankey shaping
# ---------------------------------------------------------------------------


def _links_from(tree_or_links: Union[SupplyPathTree, Iterable[PathLink]]) -> list[PathLink]:
    """Coerce the polymorphic input to a flat list of :class:`PathLink`."""
    if isinstance(tree_or_links, SupplyPathTree):
        return list(tree_or_links.links)
    return list(tree_or_links)


def to_sankey(
    tree_or_links: Union[SupplyPathTree, Iterable[PathLink]],
    graph: Optional[WorldGraph] = None,
) -> dict:
    """Reshape a traced tree (or raw links) into a Plotly ``go.Sankey`` dict.

    Parameters
    ----------
    tree_or_links:
        Either a :class:`SupplyPathTree` or any iterable of :class:`PathLink`.
    graph:
        Optional. If given, node labels are resolved via
        ``graph.get_node(nid).label`` (human-readable, e.g. "ZIM Integrated
        Shipping Services"). If omitted, the node id itself is used as the
        label — so this function is usable without re-passing the graph.

    Returns
    -------
    dict
        ``{"labels": [...], "source": [...], "target": [...], "value": [...]}``
        — exactly the parallel arrays a ``go.Sankey`` needs. ``source``/
        ``target`` are integer indices into ``labels`` (guaranteed
        ``0 <= i < len(labels)``); all four list-valued keys have matching
        length on the link arrays (``source``/``target``/``value``). An empty
        input yields empty arrays.
    """
    links = _links_from(tree_or_links)

    # Stable label ordering: first appearance across (parent, child) pairs.
    index: dict[str, int] = {}
    labels: list[str] = []

    def _label(nid: str) -> str:
        if graph is not None:
            node = graph.get_node(nid)
            if node is not None and node.label:
                return node.label
        return nid

    def _idx(nid: str) -> int:
        if nid not in index:
            index[nid] = len(labels)
            labels.append(_label(nid))
        return index[nid]

    source: list[int] = []
    target: list[int] = []
    value: list[float] = []
    for link in links:
        s = _idx(link.parent)
        t = _idx(link.child)
        source.append(s)
        target.append(t)
        # Edge weight drives flow thickness; default to 1.0 for missing/non-finite.
        w = link.weight
        try:
            w = float(w)
            if w != w or w in (float("inf"), float("-inf")):  # NaN / inf guard
                w = 1.0
        except (TypeError, ValueError):
            w = 1.0
        value.append(w if w > 0 else 1.0)

    return {
        "labels": labels,
        "source": source,
        "target": target,
        "value": value,
    }


# ---------------------------------------------------------------------------
# Dependency ranking
# ---------------------------------------------------------------------------


def rank_supply_dependencies(
    graph: WorldGraph, node_id: str, *, max_hops: int = 3,
) -> list[tuple[str, float]]:
    """Rank ``node_id``'s biggest UPSTREAM dependencies.

    Traces upstream (what feeds the node) and scores every reached node by the
    total MODELED weight on the links that discovered it within ``max_hops``,
    discounted by hop distance (a hop-2 dependency contributes less than a
    direct hop-1 one). The result lets the UI show "this company's biggest
    supply dependencies."

    Returns
    -------
    list[tuple[str, float]]
        ``(node_id, score)`` sorted by score descending, then node_id ascending
        for a stable order. Excludes the root. ``[]`` if nothing upstream.

    NOTE: scores rank MODELED exposure, not measured supply volume — see the
    module docstring.
    """
    tree = trace_upstream(graph, node_id, max_hops=max_hops)
    scores: dict[str, float] = defaultdict(float)
    for link in tree.links:
        w = link.weight
        try:
            w = float(w)
            if w != w or w in (float("inf"), float("-inf")):
                w = 1.0
        except (TypeError, ValueError):
            w = 1.0
        if w <= 0:
            w = 1.0
        # Discount by hop distance so nearer dependencies rank higher.
        scores[link.child] += w / float(link.hop)
    scores.pop(node_id, None)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
