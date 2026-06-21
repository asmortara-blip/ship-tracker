"""processing/cascade_ribbon.py — the end-to-end disruption-cascade ribbon.

One picture that answers the platform's headline question, left-to-right:
*how does a physical disruption flow all the way into an equity view?*

It stitches together two artefacts the platform already produces:

  * a :class:`processing.ssi_attribution.SSIAttributionReport` — which SSI
    *component* (chokepoint / congestion / weather / rate / vulnerability /
    anomaly) is driving each stressed *route*; and
  * the list of :class:`processing.disruption_cascade.EquityIdea` — each of
    which carries a ``cascade_chain`` of :class:`CascadeLink` hops tying
    stressed *routes* to the *ticker* the idea is for.

Chained, the ribbon reads:

    component  →  route  →  ticker

  * **component → route** — from the attribution. Each ``RouteContribution``
    names its ``dominant_driver`` (a human-readable component label); that
    route is fed by the matching ``ComponentContribution``. The flow value is
    the route's weighted contribution to the fleet-wide SSI, so a route doing
    more damage draws a thicker ribbon out of its driving component.
  * **route → ticker** — from each idea's ``cascade_chain``. Every
    ``CascadeLink`` carries a ``route_id`` and a fully-decomposed
    ``contribution`` (route stress × cargo share × exposure weight). The link's
    contribution is the flow from that route into the idea's ticker.

The route nodes are the join: the attribution's ``RouteContribution.route_id``
and the cascade link's ``route_id`` are the same registry route_ids, so a
single route node carries both the inbound component ribbon and the outbound
ticker ribbons.

Output is the standard Plotly Sankey index-array dict —
``{"labels", "source", "target", "value"}`` with integer ``source``/``target``
indices into ``labels`` — exactly the contract
``ui.tab_world_graph._build_sankey_figure`` consumes (the same one
``processing.supply_path.to_sankey`` emits).

Pure module — **no Streamlit, no Plotly imports.** Just the index arrays.
Tolerant of empty / degenerate inputs: an empty attribution and/or empty idea
list yields ``{"labels": [], "source": [], "target": [], "value": []}`` with
no crash. Every emitted flow is strictly positive; zero / negative flows are
dropped; duplicate edges are collapsed (their values summed); a node never
points to itself.
"""
from __future__ import annotations

from collections import OrderedDict

from processing.disruption_cascade import EquityIdea
from processing.shipping_stress_index import _DRIVER_LABELS
from processing.ssi_attribution import SSIAttributionReport


__all__ = ["to_cascade_sankey"]


# Inverse of the SSI module's _DRIVER_LABELS: human-readable dominant-driver
# label ("Chokepoint disruption") -> canonical component key ("chokepoint").
# A route's ``dominant_driver`` is the human label, while a component
# contribution is keyed by the canonical key; this maps a route's driver back
# to the SSI component node so the two reconcile onto one node. Lower-cased
# keys make the lookup case-insensitive.
_DRIVER_LABEL_TO_COMPONENT: dict[str, str] = {
    label.strip().lower(): key for key, label in _DRIVER_LABELS.items()
}


# A driver/component label can be carried verbatim from a route's
# ``dominant_driver`` (human-readable, e.g. "Chokepoint disruption") OR as a
# bare component key (e.g. "chokepoint"). We key the component nodes by their
# normalised lower-cased label so the two reconcile, while still surfacing the
# original label as the displayed node text.

# Node-key namespaces keep the three node kinds from ever colliding on a shared
# id (a route_id could in principle equal a ticker string).
_COMPONENT_NS = "component"
_ROUTE_NS = "route"
_TICKER_NS = "ticker"


def _empty() -> dict:
    """The honest-empty Sankey dict — same shape, all arrays empty."""
    return {"labels": [], "source": [], "target": [], "value": []}


def _finite_positive(value) -> float:
    """Coerce *value* to a finite float, or 0.0 for NaN/inf/non-numeric.

    Used to gate every flow: only strictly-positive finite values become edges.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf guard
        return 0.0
    return v


def to_cascade_sankey(
    attribution: SSIAttributionReport | None,
    ideas: list[EquityIdea] | None,
) -> dict:
    """Build the component → route → ticker cascade ribbon Sankey dict.

    Parameters
    ----------
    attribution:
        A :class:`processing.ssi_attribution.SSIAttributionReport` (or ``None``).
        Supplies the **component → route** half of the ribbon from its
        ``component_contributions`` / ``route_contributions``: a route is wired
        to the component named by its ``dominant_driver``, with flow value equal
        to the route's ``weighted`` contribution to the fleet-wide SSI.
    ideas:
        The list of :class:`processing.disruption_cascade.EquityIdea` (or
        ``None``). Supplies the **route → ticker** half from each idea's
        ``cascade_chain``: every :class:`CascadeLink` wires its ``route_id`` to
        the idea's ``ticker`` with flow value equal to the link's
        ``contribution``.

    Returns
    -------
    dict
        ``{"labels": [...], "source": [...], "target": [...], "value": [...]}``
        — the parallel arrays a Plotly ``go.Sankey`` consumes.
        ``source``/``target`` are integer indices into ``labels`` (every index
        is guaranteed ``0 <= i < len(labels)``); the three link arrays
        (``source``/``target``/``value``) always share one length. Every
        ``value`` is strictly positive. Duplicate edges are collapsed (values
        summed). No node ever points to itself. Empty / degenerate input yields
        the all-empty dict.
    """
    # ── 1. Index node labels in a stable, left-to-right order ──────────────
    # Insertion order is: components first, then routes, then tickers — so the
    # Sankey lays out left (causes) to right (equity views) naturally.
    labels: list[str] = []
    index: dict[tuple[str, str], int] = {}

    def _node(namespace: str, key: str, display: str) -> int:
        """Return the index of (namespace, key), registering it on first sight.

        ``key`` is the de-dup identity (e.g. normalised component label or a
        route_id); ``display`` is the human-readable label shown on the node.
        """
        node_key = (namespace, key)
        idx = index.get(node_key)
        if idx is None:
            idx = len(labels)
            index[node_key] = idx
            labels.append(display)
        return idx

    # Accumulate edges keyed by (src_idx, tgt_idx) so duplicates sum.
    edges: "OrderedDict[tuple[int, int], float]" = OrderedDict()

    def _add_edge(src_idx: int, tgt_idx: int, value: float) -> None:
        v = _finite_positive(value)
        if v <= 0.0:
            return
        if src_idx == tgt_idx:
            # A node must never point to itself.
            return
        edges[(src_idx, tgt_idx)] = edges.get((src_idx, tgt_idx), 0.0) + v

    # ── 2. component → route (from the SSI attribution) ────────────────────
    # Map each component contribution's component key to a stable label so a
    # route's human-readable dominant_driver resolves to the same node.
    if attribution is not None:
        component_label_by_key: dict[str, str] = {}
        for comp in attribution.component_contributions or []:
            comp_name = str(getattr(comp, "component", "") or "").strip()
            if not comp_name:
                continue
            component_label_by_key[comp_name.lower()] = comp_name

        for route in attribution.route_contributions or []:
            route_id = str(getattr(route, "route_id", "") or "").strip()
            if not route_id:
                continue
            driver = str(getattr(route, "dominant_driver", "") or "").strip()
            if not driver:
                continue
            weighted = _finite_positive(getattr(route, "weighted", 0.0))
            if weighted <= 0.0:
                continue

            # Resolve the route's driver to its canonical SSI component node.
            # The driver is a human-readable label ("Chokepoint disruption");
            # the SSI _DRIVER_LABELS inverse maps it to the component key
            # ("chokepoint") so it lands on the SAME node as that component's
            # contribution. Fall back to the raw lower-cased driver text when
            # the label is unrecognised so an odd driver still produces a
            # (correctly-attributed) node rather than vanishing.
            driver_lc = driver.lower()
            comp_key = _DRIVER_LABEL_TO_COMPONENT.get(driver_lc, driver_lc)
            # Display the canonical component label when we have one (matches
            # the component-contribution node text), else the raw driver.
            comp_display = component_label_by_key.get(comp_key, driver)

            comp_idx = _node(_COMPONENT_NS, comp_key, comp_display)
            route_name = str(
                getattr(route, "route_name", "") or route_id
            ).strip() or route_id
            route_idx = _node(_ROUTE_NS, route_id, route_name)
            _add_edge(comp_idx, route_idx, weighted)

    # ── 3. route → ticker (from each idea's cascade_chain) ─────────────────
    # Prefer the route's attribution display name when the route node already
    # exists; otherwise fall back to the bare route_id (the cascade link does
    # not carry a route name).
    if ideas:
        for idea in ideas:
            ticker = str(getattr(idea, "ticker", "") or "").strip()
            if not ticker:
                continue
            chain = getattr(idea, "cascade_chain", None) or []
            for link in chain:
                route_id = str(getattr(link, "route_id", "") or "").strip()
                if not route_id:
                    continue
                contribution = _finite_positive(
                    getattr(link, "contribution", 0.0)
                )
                if contribution <= 0.0:
                    continue
                # Reuse the route node if the attribution already registered it
                # (so the inbound component ribbon and outbound ticker ribbons
                # share one node); else register it by route_id.
                route_idx = _node(_ROUTE_NS, route_id, route_id)
                ticker_idx = _node(_TICKER_NS, ticker, ticker)
                _add_edge(route_idx, ticker_idx, contribution)

    if not edges:
        return _empty()

    source: list[int] = []
    target: list[int] = []
    value: list[float] = []
    for (s, t), v in edges.items():
        source.append(s)
        target.append(t)
        value.append(round(v, 6))

    return {
        "labels": labels,
        "source": source,
        "target": target,
        "value": value,
    }
