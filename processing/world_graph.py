"""processing/world_graph.py — the unified shipping "world graph".

One typed, navigable model that ties together the entities this platform
already models — vessels, routes, ports, chokepoints, companies, commodities —
through the relationships its existing builders already compute (route
geometry, chokepoint constraints, voyage assignments, port↔company exposure,
cargo mix, port-to-port spillover). Pure-function over those builders: no new
data sources, no I/O beyond what the builders themselves do.

Design
------
* **Namespaced node ids.** Every node id is ``"{type}:{key}"`` (e.g.
  ``"port:CNSHA"``, ``"route:transpacific_eb"``, ``"chokepoint:hormuz"``,
  ``"company:ZIM"``, ``"commodity:electronics"``, ``"vessel:VY-..."``) so ids
  never collide across the six node types and every edge is unambiguous. Use
  :func:`node_id` to build one.
* **Defensive assembly.** Each source is wrapped in try/except and degrades to
  "contribute nothing" rather than failing the whole build — one missing feed
  (e.g. no persisted snapshots → no spillover edges) never breaks the graph.
* **Edges reference only existing nodes.** ``_add_edge`` upserts a minimal node
  for any endpoint not yet seen, so there are never dangling edges.

The graph is consumed by:
  * ``processing.world_graph_metrics`` — centrality / resilience / criticality
  * ``ui.tab_world_graph`` — the linked node-link graph + geographic map

Gotchas handled (see the data-contract inventory):
  * Chokepoints have no id FIELD — keyed by their dict key.
  * Vessel→chokepoint joins by chokepoint NAME → mapped back to the dict key.
  * ``Chokepoint.affected_routes`` contains phantom route ids → filtered against
    ``ROUTES_BY_ID``.
  * Spillover needs ≥2 persisted snapshots → absent ones yield no edges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


__all__ = [
    "NODE_TYPES",
    "WorldNode",
    "WorldEdge",
    "WorldGraph",
    "node_id",
    "build_world_graph",
]


# The six node types the world graph unifies.
NODE_TYPES: tuple[str, ...] = (
    "port", "route", "chokepoint", "vessel", "company", "commodity",
)


def node_id(node_type: str, key: str) -> str:
    """Build a namespaced node id, e.g. ``node_id("port", "CNSHA")``."""
    return f"{node_type}:{key}"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class WorldNode:
    """One entity in the world graph.

    ``lat``/``lon`` are populated only for geo-mappable types (port,
    chokepoint, vessel); routes/companies/commodities are abstract (None).
    ``attrs`` carries type-specific extras (e.g. a port's severity_label, a
    chokepoint's risk_level) for the UI + metrics to read without re-querying.
    """

    node_id: str
    node_type: str
    label: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    attrs: dict = field(default_factory=dict)


@dataclass
class WorldEdge:
    """One relationship between two nodes (by node_id)."""

    source: str
    target: str
    edge_type: str
    weight: float = 1.0
    directed: bool = True
    attrs: dict = field(default_factory=dict)


@dataclass
class WorldGraph:
    """The assembled graph + the helpers metrics / layout / UI need."""

    nodes: list[WorldNode] = field(default_factory=list)
    edges: list[WorldEdge] = field(default_factory=list)
    container_type: str = "40FT_DRY"

    # ── lookups ────────────────────────────────────────────────────────────
    def node_ids(self) -> list[str]:
        return [n.node_id for n in self.nodes]

    def get_node(self, nid: str) -> Optional[WorldNode]:
        return self._index().get(nid)

    def nodes_by_type(self, node_type: str) -> list[WorldNode]:
        return [n for n in self.nodes if n.node_type == node_type]

    def _index(self) -> dict[str, WorldNode]:
        # Built lazily; small graphs (a few hundred nodes) so no caching needed.
        return {n.node_id: n for n in self.nodes}

    # ── graph structure (for metrics, layout, blast-radius) ─────────────────
    def adjacency(self, *, undirected: bool = True) -> dict[str, set[str]]:
        """Adjacency sets keyed by node_id. Undirected by default (so a port's
        neighbourhood includes the routes pointing INTO it). Self-loops dropped."""
        adj: dict[str, set[str]] = {n.node_id: set() for n in self.nodes}
        for e in self.edges:
            if e.source == e.target:
                continue
            if e.source not in adj or e.target not in adj:
                continue
            adj[e.source].add(e.target)
            if undirected or not e.directed:
                adj[e.target].add(e.source)
        return adj

    def neighbors(self, nid: str, *, undirected: bool = True) -> set[str]:
        """1-hop neighbourhood of ``nid`` (excludes itself)."""
        return self.adjacency(undirected=undirected).get(nid, set())

    def blast_radius(self, nid: str, *, hops: int = 2) -> set[str]:
        """Node ids reachable from ``nid`` within ``hops`` (undirected) —
        the 'who's affected if this node is disrupted?' set. Excludes itself."""
        adj = self.adjacency(undirected=True)
        seen: set[str] = {nid}
        frontier = {nid}
        for _ in range(max(0, hops)):
            nxt: set[str] = set()
            for f in frontier:
                nxt |= adj.get(f, set())
            nxt -= seen
            if not nxt:
                break
            seen |= nxt
            frontier = nxt
        seen.discard(nid)
        return seen

    def edge_tuples(self) -> list[tuple[str, str, float]]:
        """``(source, target, weight)`` list — the form the numpy metrics take.
        Treated as undirected by the centrality functions; self-loops dropped."""
        return [
            (e.source, e.target, float(e.weight))
            for e in self.edges
            if e.source != e.target
        ]

    def summary(self) -> dict:
        """Counts for diagnostics / the UI header."""
        by_type: dict[str, int] = {t: 0 for t in NODE_TYPES}
        for n in self.nodes:
            by_type[n.node_type] = by_type.get(n.node_type, 0) + 1
        edge_by_type: dict[str, int] = {}
        for e in self.edges:
            edge_by_type[e.edge_type] = edge_by_type.get(e.edge_type, 0) + 1
        return {
            "n_nodes": len(self.nodes),
            "n_edges": len(self.edges),
            "nodes_by_type": by_type,
            "edges_by_type": edge_by_type,
            "container_type": self.container_type,
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_world_graph(
    *,
    container_type: str = "40FT_DRY",
    include_vessels: bool = True,
    include_spillover: bool = True,
) -> WorldGraph:
    """Assemble the unified world graph from the existing builders.

    Parameters
    ----------
    container_type:
        Which container slice to use for the port-supply + footprint data.
    include_vessels:
        Add the modeled voyage fleet as vessel nodes (~4-9 per route). They are
        leaf instances; set False for a cleaner structural backbone.
    include_spillover:
        Add port→port contagion edges from the spillover graph. Requires ≥2
        persisted daily snapshots; silently contributes nothing otherwise.

    Returns
    -------
    WorldGraph
        Never raises — every source is defensively wrapped.
    """
    nodes: dict[str, WorldNode] = {}
    edges: list[WorldEdge] = []

    def _add_node(
        ntype: str, key: str, label: str,
        lat: Optional[float] = None, lon: Optional[float] = None,
        **attrs,
    ) -> str:
        nid = node_id(ntype, key)
        existing = nodes.get(nid)
        if existing is None:
            nodes[nid] = WorldNode(
                node_id=nid, node_type=ntype, label=label,
                lat=lat, lon=lon, attrs=dict(attrs),
            )
        else:
            # Upsert: fill in coords/attrs a later source supplies; keep a real
            # label over a placeholder.
            if lat is not None and existing.lat is None:
                existing.lat = lat
            if lon is not None and existing.lon is None:
                existing.lon = lon
            if label and (not existing.label or existing.label == key):
                existing.label = label
            for k, v in attrs.items():
                existing.attrs.setdefault(k, v)
        return nid

    def _add_edge(
        src: str, dst: str, edge_type: str,
        weight: float = 1.0, directed: bool = True, **attrs,
    ) -> None:
        # Ensure both endpoints exist as (at worst) minimal placeholder nodes,
        # so there are never dangling edges.
        for nid in (src, dst):
            if nid not in nodes:
                ntype, _, key = nid.partition(":")
                nodes[nid] = WorldNode(
                    node_id=nid, node_type=ntype or "unknown",
                    label=key or nid,
                )
        edges.append(WorldEdge(
            source=src, target=dst, edge_type=edge_type,
            weight=float(weight), directed=directed, attrs=dict(attrs),
        ))

    # ── Ports + port→company (E7) + port→commodity (E9) ─────────────────────
    try:
        from processing.port_supply_lines import build_port_supply_chains

        for chain in build_port_supply_chains(container_type=container_type):
            p = chain.port
            pid = _add_node(
                "port", p.locode, getattr(p, "name", p.locode),
                lat=getattr(p, "lat", None), lon=getattr(p, "lon", None),
                region=getattr(p, "region", ""),
                supply_deficit_days=getattr(p, "supply_deficit_days", 0.0),
                severity_label=getattr(p, "severity_label", ""),
            )
            for ce in getattr(chain, "exposed_companies", []) or []:
                cid = node_id("company", ce.ticker)
                _add_edge(
                    pid, cid, "port_exposes_company",
                    weight=float(getattr(ce, "exposure_weight", 0.0) or 0.0),
                )
            for item in getattr(chain, "top_commodities", []) or []:
                # top_commodities entries are (hs_category, total_weight) tuples.
                try:
                    hs_cat, wt = item[0], float(item[1])
                except (TypeError, IndexError, ValueError):
                    continue
                _add_edge(
                    pid, node_id("commodity", hs_cat),
                    "port_carries_commodity", weight=wt, directed=False,
                )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"world_graph: port-supply assembly failed: {exc}")

    # ── Routes + route→origin/dest port (E1, E2) ────────────────────────────
    try:
        from routes.route_registry import ROUTES

        for r in ROUTES:
            rid = _add_node(
                "route", r.id, getattr(r, "name", r.id),
                origin_locode=getattr(r, "origin_locode", ""),
                dest_locode=getattr(r, "dest_locode", ""),
                transit_days=getattr(r, "transit_days", None),
            )
            if getattr(r, "origin_locode", ""):
                _add_edge(rid, node_id("port", r.origin_locode), "route_origin")
            if getattr(r, "dest_locode", ""):
                _add_edge(rid, node_id("port", r.dest_locode), "route_dest")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"world_graph: route assembly failed: {exc}")

    # ── Chokepoints + chokepoint→route (E3, phantom-filtered) ───────────────
    try:
        from processing.chokepoint_analyzer import (
            CHOKEPOINTS, compute_chokepoint_risk_score,
        )
        from routes.route_registry import ROUTES_BY_ID

        try:
            risk = compute_chokepoint_risk_score() or {}
        except Exception:
            risk = {}

        for key, cp in CHOKEPOINTS.items():
            cpid = _add_node(
                "chokepoint", key, getattr(cp, "name", key),
                lat=getattr(cp, "lat", None), lon=getattr(cp, "lon", None),
                risk_level=getattr(cp, "current_risk_level", ""),
                risk_score=float(risk.get(key, 0.0) or 0.0),
            )
            for route_ref in getattr(cp, "affected_routes", []) or []:
                if route_ref in ROUTES_BY_ID:  # drop phantom route ids
                    _add_edge(
                        cpid, node_id("route", route_ref),
                        "chokepoint_constrains",
                        weight=float(risk.get(key, 0.0) or 0.0),
                    )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"world_graph: chokepoint assembly failed: {exc}")

    # ── Vessels + vessel→route (E4) + vessel→chokepoint (E6, by name) ───────
    if include_vessels:
        try:
            from data.voyage_dataset import build_voyage_fleet
            from processing.chokepoint_analyzer import CHOKEPOINTS

            name_to_key = {
                getattr(cp, "name", ""): key for key, cp in CHOKEPOINTS.items()
            }
            for v in build_voyage_fleet() or []:
                vid = _add_node(
                    "vessel", v.voyage_id,
                    getattr(v, "vessel_name", v.voyage_id),
                    lat=getattr(v, "current_lat", None),
                    lon=getattr(v, "current_lon", None),
                    status=getattr(v, "status", ""),
                    delay_days=getattr(v, "delay_days", 0.0),
                    route_id=getattr(v, "route_id", ""),
                )
                if getattr(v, "route_id", ""):
                    _add_edge(vid, node_id("route", v.route_id), "vessel_sails")
                for cp_name in getattr(v, "chokepoints_on_route", []) or []:
                    key = name_to_key.get(cp_name)
                    if key:
                        _add_edge(
                            vid, node_id("chokepoint", key), "vessel_transits",
                        )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"world_graph: vessel assembly failed: {exc}")

    # ── Companies (nodes, with footprint metrics + profile names) ───────────
    try:
        from processing.company_profiler import COMPANY_PROFILES
    except Exception:
        COMPANY_PROFILES = {}
    try:
        from processing.port_supply_lines import build_company_port_footprints

        for fp in build_company_port_footprints(container_type=container_type):
            profile = COMPANY_PROFILES.get(fp.ticker, {}) if isinstance(
                COMPANY_PROFILES, dict) else {}
            _add_node(
                "company", fp.ticker,
                profile.get("name", fp.ticker) if isinstance(profile, dict)
                else fp.ticker,
                concentration_hhi=float(getattr(fp, "concentration_hhi", 0.0) or 0.0),
                total_exposure=float(getattr(fp, "total_exposure", 0.0) or 0.0),
                sector=profile.get("sector", "") if isinstance(profile, dict) else "",
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"world_graph: company-footprint assembly failed: {exc}")
    # Ensure every profiled ticker is present even if it had no footprint.
    if isinstance(COMPANY_PROFILES, dict):
        for ticker, profile in COMPANY_PROFILES.items():
            _add_node(
                "company", ticker,
                profile.get("name", ticker) if isinstance(profile, dict) else ticker,
            )

    # ── Commodities + commodity→route (E11) + commodity→company (E10) ───────
    try:
        from processing.cargo_analyzer import HS_CATEGORIES

        try:
            from processing.exposure_matrix import routes_for_commodity
            from routes.route_registry import ROUTES_BY_ID
        except Exception:
            routes_for_commodity = None
            ROUTES_BY_ID = {}

        for hs_cat, meta in HS_CATEGORIES.items():
            label = meta.get("label", hs_cat) if isinstance(meta, dict) else hs_cat
            cmid = _add_node("commodity", hs_cat, label)
            if routes_for_commodity is not None:
                try:
                    for route_ref in routes_for_commodity(hs_cat) or []:
                        if route_ref in ROUTES_BY_ID:
                            _add_edge(
                                cmid, node_id("route", route_ref),
                                "commodity_flows_route",
                            )
                except Exception:
                    pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"world_graph: commodity assembly failed: {exc}")

    try:
        from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE

        for ticker, vec in (COMPANY_COMMODITY_EXPOSURE or {}).items():
            if not isinstance(vec, dict):
                continue
            for hs_cat, wt in vec.items():
                w = float(wt or 0.0)
                if w > 0:
                    _add_edge(
                        node_id("commodity", hs_cat), node_id("company", ticker),
                        "commodity_drives_company", weight=w,
                    )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"world_graph: commodity→company assembly failed: {exc}")

    # ── Port→port spillover (E12) — needs ≥2 persisted snapshots ────────────
    if include_spillover:
        try:
            from processing.port_spillover_graph import build_spillover_graph
            from processing.port_supply_history import (
                list_snapshot_dates, load_snapshot,
            )

            dates = list_snapshot_dates() or []
            history = []
            for d in dates:
                try:
                    history.append(load_snapshot(d, container_type=container_type))
                except Exception:
                    continue
            if len(history) >= 2:
                sg = build_spillover_graph(history)
                for e in getattr(sg, "edges", []) or []:
                    _add_edge(
                        node_id("port", e.source_locode),
                        node_id("port", e.target_locode),
                        "port_spillover",
                        weight=float(getattr(e, "lift", 1.0) or 1.0),
                        support=float(getattr(e, "support", 0.0) or 0.0),
                    )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"world_graph: spillover assembly skipped: {exc}")

    graph = WorldGraph(
        nodes=list(nodes.values()),
        edges=edges,
        container_type=container_type,
    )
    logger.debug(
        "build_world_graph: {} nodes, {} edges ({})",
        len(graph.nodes), len(graph.edges), graph.summary()["nodes_by_type"],
    )
    return graph
