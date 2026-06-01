"""processing/world_graph_criticality.py — systemic-node criticality detector.

Betweenness centrality answers a purely *structural* question: which node
carries the most shortest-path flow through the world graph? On its own that
flags the load-bearing hubs (Shanghai, Rotterdam, Suez) regardless of whether
anything is actually wrong with them. The operator-facing question is sharper:
*is the most systemically-central node also currently STRESSED?* — because a
disruption at a node that is BOTH central AND already strained is the one most
likely to cascade network-wide.

This module joins the two signals. It builds the world graph, computes
betweenness centrality, and — restricting attention to nodes that can actually
carry a stress signal — finds the single most-central such node and gates it on
both a centrality threshold and a stress threshold. If it clears both, one
``WorldGraphCriticalityAlert`` is returned; otherwise ``None``.

Why restrict to "stressable" geo nodes
--------------------------------------
The graph has six node types, but only two carry a meaningful per-node stress
signal:

  * **port** — stress = ``max(0, -supply_deficit_days)`` normalized to ~[0, 1]
    by dividing by 14 (a 2-week deficit is treated as fully stressed). A
    *positive* ``supply_deficit_days`` means surplus, so it floors at 0.
  * **chokepoint** — stress = ``risk_score`` (already ~[0, 1] from
    ``compute_chokepoint_risk_score``).

The abstract node types — commodity / route / company / vessel — have NO stress
signal. Crucially, on the FULL graph commodities and big transshipment ports
often top raw betweenness, so a naive "most-central node" check would surface a
node with no stress concept at all. By computing the candidate over the
stressable set we guarantee the alert is about a node where "stress" is
well-defined.

Threshold defaults
------------------
``betweenness_threshold=0.05`` / ``stress_threshold=0.30`` /
``critical_stress_threshold=0.60`` are deliberately MODEST — the smoke dataset
carries only chokepoint risk (no live port deficits), so a higher bar would
mean the alert never fires. On the current live graph the most-central
stressable node is a major transshipment port whose smoke-data stress is 0, so
the *literal* most-central node does not clear the stress gate (the alert is
silent until a real port deficit or a re-ranking shock lands) — see the module
test + the wiring report. All three thresholds are keyword-configurable so an
operator can tighten (fewer, higher-conviction alerts) or loosen (catch
earlier-stage stress) without code changes.

Pure function — no I/O of its own beyond what ``build_world_graph`` does, and
never raises: every failure path degrades to ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger


__all__ = [
    "DEFAULT_BETWEENNESS_THRESHOLD",
    "DEFAULT_STRESS_THRESHOLD",
    "DEFAULT_CRITICAL_STRESS_THRESHOLD",
    "PORT_DEFICIT_NORMALIZER_DAYS",
    "WorldGraphCriticalityAlert",
    "node_stress",
    "find_critical_stressed_node",
]


# Default thresholds — wired into the alert engine. See the module docstring
# for the rationale (smoke data carries modest stress; modest bars keep the
# alert from being permanently silent while staying configurable).
DEFAULT_BETWEENNESS_THRESHOLD: float = 0.05
DEFAULT_STRESS_THRESHOLD: float = 0.30
DEFAULT_CRITICAL_STRESS_THRESHOLD: float = 0.60

# A port's ``supply_deficit_days`` is normalized to ~[0, 1] by dividing by this
# many days — i.e. a 14-day (two-week) deficit is treated as fully stressed.
PORT_DEFICIT_NORMALIZER_DAYS: float = 14.0

# The node types that carry a per-node stress signal. The abstract types
# (commodity/route/company/vessel) have none — see module docstring.
STRESSABLE_NODE_TYPES: frozenset[str] = frozenset({"port", "chokepoint"})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class WorldGraphCriticalityAlert:
    """The single most-central stressed node, once it clears both gates.

    Carries enough for the alert engine to format a body without re-querying
    the graph: the node's id/label/type plus its (normalized) betweenness and
    stress, and a precomputed severity.
    """

    node_id: str                # namespaced graph id, e.g. "chokepoint:suez"
    label: str                  # human label, e.g. "Suez Canal"
    node_type: str              # "port" or "chokepoint"
    betweenness: float          # normalized to [0, 1] (divided by graph max)
    stress: float               # ~[0, 1]
    severity: str               # "CRITICAL" if stress >= critical else "HIGH"

    def summary(self) -> str:
        """One-line operator-facing rationale."""
        return (
            f"{self.label} is the most systemically-central stressed node "
            f"(betweenness {self.betweenness:.2f}, stress {self.stress:.2f}) "
            f"— a disruption here cascades network-wide."
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def node_stress(node) -> Optional[float]:
    """Per-node stress in ~[0, 1], or ``None`` for non-stressable node types.

    * port       → ``max(0, -supply_deficit_days) / PORT_DEFICIT_NORMALIZER_DAYS``
                   (a positive deficit value means surplus → 0 stress).
    * chokepoint → ``risk_score`` (already ~[0, 1]); clamped to >= 0.
    * anything else → ``None`` (no stress concept).

    Defensive: missing/non-numeric attrs degrade to 0.0 stress, never raise.
    """
    ntype = getattr(node, "node_type", "")
    attrs = getattr(node, "attrs", {}) or {}
    if ntype == "port":
        try:
            deficit = float(attrs.get("supply_deficit_days", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        norm = PORT_DEFICIT_NORMALIZER_DAYS if PORT_DEFICIT_NORMALIZER_DAYS > 0 else 1.0
        return max(0.0, -deficit) / norm
    if ntype == "chokepoint":
        try:
            return max(0.0, float(attrs.get("risk_score", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0
    return None


def _severity_for(stress: float, critical_threshold: float) -> str:
    """CRITICAL once stress reaches ``critical_threshold``, else HIGH.

    The fire-threshold contract means only stress >= ``stress_threshold``
    reaches this helper at all.
    """
    return "CRITICAL" if stress >= critical_threshold else "HIGH"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def find_critical_stressed_node(
    *,
    betweenness_threshold: float = DEFAULT_BETWEENNESS_THRESHOLD,
    stress_threshold: float = DEFAULT_STRESS_THRESHOLD,
    critical_stress_threshold: float = DEFAULT_CRITICAL_STRESS_THRESHOLD,
    container_type: str = "40FT_DRY",
) -> Optional[WorldGraphCriticalityAlert]:
    """Find the most-central stressable node + gate it on centrality & stress.

    Algorithm
    ---------
    1. Build the world graph (``include_vessels=False`` — vessels are leaf
       instances that add noise to centrality without changing the structural
       backbone).
    2. Compute betweenness centrality over all node ids + edges, then normalize
       to [0, 1] by dividing by the graph maximum (so the most-central node of
       any graph scores 1.0).
    3. Over the **stressable** node set (ports + chokepoints) pick the candidate
       with the highest normalized betweenness — the most systemically-central
       node where "stress" is even defined.
    4. Fire iff that candidate clears BOTH gates: normalized betweenness
       >= ``betweenness_threshold`` AND stress >= ``stress_threshold``. Severity
       is CRITICAL when stress >= ``critical_stress_threshold``, else HIGH.

    Parameters
    ----------
    betweenness_threshold:
        Minimum *normalized* betweenness for the candidate to count as
        "critically central". Default 0.05.
    stress_threshold:
        Minimum stress for the candidate to fire. Default 0.30.
    critical_stress_threshold:
        Stress at/above which severity escalates to CRITICAL. Default 0.60.
    container_type:
        Container slice passed through to ``build_world_graph``.

    Returns
    -------
    Optional[WorldGraphCriticalityAlert]
        The alert when the most-central stressable node clears both gates;
        otherwise ``None`` (including: empty/edgeless graph, no stressable
        nodes, candidate not central enough, or candidate not stressed enough).

    Never raises — any failure (import, build, metric) degrades to ``None``
    with a logged warning.
    """
    try:
        from processing.world_graph import build_world_graph
        from processing.world_graph_metrics import betweenness_centrality
    except Exception as exc:  # pragma: no cover - defensive import guard
        logger.debug(f"find_critical_stressed_node: import failed: {exc}")
        return None

    try:
        graph = build_world_graph(
            container_type=container_type, include_vessels=False,
        )
        node_ids = graph.node_ids()
        if not node_ids:
            return None

        btw = betweenness_centrality(node_ids, graph.edge_tuples())
        max_btw = max(btw.values()) if btw else 0.0

        # Walk the stressable nodes, tracking the one with the highest
        # NORMALIZED betweenness. We keep its (un-normalized → normalized)
        # betweenness + stress so the gate below reads cleanly.
        best_node = None
        best_btw_norm = -1.0
        best_stress = 0.0
        for nid in node_ids:
            node = graph.get_node(nid)
            if node is None or node.node_type not in STRESSABLE_NODE_TYPES:
                continue
            stress = node_stress(node)
            if stress is None:
                continue
            raw = float(btw.get(nid, 0.0) or 0.0)
            btw_norm = (raw / max_btw) if max_btw > 0 else 0.0
            if btw_norm > best_btw_norm:
                best_btw_norm = btw_norm
                best_node = node
                best_stress = stress

        if best_node is None:
            return None

        # Two-part gate: must be both critically central AND stressed.
        if best_btw_norm < betweenness_threshold:
            return None
        if best_stress < stress_threshold:
            return None

        return WorldGraphCriticalityAlert(
            node_id=best_node.node_id,
            label=best_node.label or best_node.node_id,
            node_type=best_node.node_type,
            betweenness=best_btw_norm,
            stress=best_stress,
            severity=_severity_for(best_stress, critical_stress_threshold),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"find_critical_stressed_node: compute failed: {exc}")
        return None
