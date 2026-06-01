"""Defining-property tests for processing/world_graph_criticality.py.

We don't exercise the real ``build_world_graph`` here (that's an integration
concern); instead we monkeypatch ``build_world_graph`` +
``betweenness_centrality`` at their source modules so the compute logic runs
over a controlled tiny graph. This isolates the join-and-gate logic — node
selection, stress computation, normalization, the two-part gate, and the
severity ladder — from the live data.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from processing.world_graph_criticality import (
    DEFAULT_BETWEENNESS_THRESHOLD,
    DEFAULT_CRITICAL_STRESS_THRESHOLD,
    DEFAULT_STRESS_THRESHOLD,
    PORT_DEFICIT_NORMALIZER_DAYS,
    WorldGraphCriticalityAlert,
    find_critical_stressed_node,
    node_stress,
)


# ── Synthetic-graph fixture helpers ──────────────────────────────────────


def _node(node_id: str, node_type: str, label: str, **attrs) -> NS:
    """A minimal duck-typed WorldNode."""
    return NS(node_id=node_id, node_type=node_type, label=label, attrs=dict(attrs))


def _graph(nodes: list, btw: dict):
    """Build (fake_build_world_graph, fake_betweenness_centrality) callables.

    The returned graph stub exposes the three accessors the compute module
    uses: ``node_ids()``, ``edge_tuples()``, ``get_node(id)``.
    """
    index = {n.node_id: n for n in nodes}

    graph = NS(
        node_ids=lambda: list(index.keys()),
        edge_tuples=lambda: [],          # edges unused once betweenness is patched
        get_node=lambda nid: index.get(nid),
    )

    def fake_build(**kw):
        return graph

    def fake_betweenness(node_ids, edges, **kw):
        # Return the controlled raw betweenness, defaulting unseen nodes to 0.
        return {nid: float(btw.get(nid, 0.0)) for nid in node_ids}

    return fake_build, fake_betweenness


def _patch(monkeypatch, nodes: list, btw: dict) -> None:
    fake_build, fake_betweenness = _graph(nodes, btw)
    # The compute module imports both names *inside* the function from their
    # source modules, so patch them at the source.
    monkeypatch.setattr(
        "processing.world_graph.build_world_graph", fake_build,
    )
    monkeypatch.setattr(
        "processing.world_graph_metrics.betweenness_centrality", fake_betweenness,
    )


# ── node_stress ──────────────────────────────────────────────────────────


def test_node_stress_port_normalizes_deficit() -> None:
    """A port's stress is max(0, -supply_deficit_days)/14."""
    # A 14-day deficit (supply_deficit_days = -14) → fully stressed (1.0).
    n = _node("port:X", "port", "Port X", supply_deficit_days=-14.0)
    assert node_stress(n) == pytest.approx(1.0)
    # Half a window.
    n7 = _node("port:Y", "port", "Port Y", supply_deficit_days=-7.0)
    assert node_stress(n7) == pytest.approx(7.0 / PORT_DEFICIT_NORMALIZER_DAYS)


def test_node_stress_port_surplus_floors_at_zero() -> None:
    """A positive supply_deficit_days means surplus → 0 stress, not negative."""
    n = _node("port:S", "port", "Surplus Port", supply_deficit_days=5.0)
    assert node_stress(n) == 0.0


def test_node_stress_chokepoint_uses_risk_score() -> None:
    n = _node("chokepoint:c", "chokepoint", "Canal C", risk_score=0.8)
    assert node_stress(n) == pytest.approx(0.8)


def test_node_stress_abstract_types_return_none() -> None:
    """Commodity/route/company/vessel carry no stress signal."""
    for t in ("commodity", "route", "company", "vessel"):
        assert node_stress(_node(f"{t}:k", t, "k")) is None


def test_node_stress_missing_attrs_degrade_to_zero() -> None:
    """A stressable node with no relevant attr → 0.0 stress, no raise."""
    assert node_stress(_node("port:Z", "port", "Z")) == 0.0
    assert node_stress(_node("chokepoint:Z", "chokepoint", "Z")) == 0.0


# ── Defaults sanity ──────────────────────────────────────────────────────


def test_default_thresholds_are_modest() -> None:
    """Documented defaults. The betweenness gate is modest (0.03) because the
    candidate is the most-central STRESSED node (gate-on-stress-first), so the
    gate only rejects stress on structurally-trivial nodes — a stressed primary
    chokepoint (Suez ~0.043) clears it."""
    assert DEFAULT_BETWEENNESS_THRESHOLD == 0.03
    assert DEFAULT_STRESS_THRESHOLD == 0.30
    assert DEFAULT_CRITICAL_STRESS_THRESHOLD == 0.60


# ── find_critical_stressed_node: the happy path ──────────────────────────


def test_central_stressed_node_fires_high(monkeypatch) -> None:
    """The most-central stressable node clears both gates → HIGH alert.

    HUB is the most central (raw 10 → normalized 1.0) and carries stress
    0.40 (>= 0.30 fire) but below 0.60 → HIGH. Other nodes are less central.
    """
    nodes = [
        _node("port:HUB", "port", "Hub Port", supply_deficit_days=-5.6),  # 5.6/14 = 0.40
        _node("port:LEAF", "port", "Leaf Port", supply_deficit_days=-14.0),  # stress 1.0 but not central
        _node("commodity:elec", "commodity", "Electronics"),  # no stress
    ]
    btw = {"port:HUB": 10.0, "port:LEAF": 2.0, "commodity:elec": 4.0}
    _patch(monkeypatch, nodes, btw)

    alert = find_critical_stressed_node()
    assert isinstance(alert, WorldGraphCriticalityAlert)
    assert alert.node_id == "port:HUB"
    assert alert.label == "Hub Port"
    assert alert.node_type == "port"
    assert alert.betweenness == pytest.approx(1.0)
    assert alert.stress == pytest.approx(0.40)
    assert alert.severity == "HIGH"


def test_central_stressed_node_fires_critical_at_high_stress(monkeypatch) -> None:
    """A chokepoint that is most-central and highly stressed → CRITICAL."""
    nodes = [
        _node("chokepoint:suez", "chokepoint", "Suez Canal", risk_score=0.95),
        _node("port:p", "port", "Port P", supply_deficit_days=-1.0),
    ]
    btw = {"chokepoint:suez": 8.0, "port:p": 1.0}
    _patch(monkeypatch, nodes, btw)

    alert = find_critical_stressed_node()
    assert alert is not None
    assert alert.node_id == "chokepoint:suez"
    assert alert.node_type == "chokepoint"
    assert alert.stress == pytest.approx(0.95)
    assert alert.severity == "CRITICAL"     # 0.95 >= 0.60


def test_summary_string_mentions_label_betweenness_and_stress(monkeypatch) -> None:
    nodes = [_node("chokepoint:suez", "chokepoint", "Suez Canal", risk_score=0.9)]
    btw = {"chokepoint:suez": 5.0}
    _patch(monkeypatch, nodes, btw)

    alert = find_critical_stressed_node()
    s = alert.summary()
    assert "Suez Canal" in s
    assert "betweenness" in s
    assert "stress" in s
    assert "cascades network-wide" in s


# ── find_critical_stressed_node: the gates ───────────────────────────────


def test_no_alert_when_no_node_is_stressed(monkeypatch) -> None:
    """The most-central node has zero stress (surplus port) → no alert, even
    though it is highly central. This is the live-smoke-graph case (the top
    stressable node by betweenness carries no deficit)."""
    nodes = [
        _node("port:HUB", "port", "Hub Port", supply_deficit_days=3.0),  # surplus → 0
        _node("commodity:elec", "commodity", "Electronics"),
    ]
    btw = {"port:HUB": 10.0, "commodity:elec": 6.0}
    _patch(monkeypatch, nodes, btw)
    assert find_critical_stressed_node() is None


def test_stressed_node_fires_even_when_a_more_central_node_is_unstressed(
    monkeypatch,
) -> None:
    """Gate-on-stress-FIRST: a STRESSED node is surfaced even when a more-central
    node is unstressed, as long as the stressed node clears the betweenness gate.
    This is the real cascade signal (e.g. a stressed Suez while Shanghai — more
    central — carries no live deficit). It replaces the old argmax-then-gate
    contract that left the alert effectively dead on live data."""
    nodes = [
        _node("port:HUB", "port", "Hub Port", supply_deficit_days=0.0),       # most central, no stress
        _node("port:SMALL", "port", "Small Port", supply_deficit_days=-14.0),  # stressed (1.0), less central
    ]
    btw = {"port:HUB": 10.0, "port:SMALL": 1.0}   # SMALL normalizes to 0.1 >= 0.03 gate
    _patch(monkeypatch, nodes, btw)
    alert = find_critical_stressed_node()
    assert alert is not None
    assert alert.node_id == "port:SMALL"          # the stressed node, not the central-but-calm hub
    assert alert.stress == pytest.approx(1.0)
    # ...but a structurally-trivial stressed node (below the gate) stays silent.
    assert find_critical_stressed_node(betweenness_threshold=0.5) is None


def test_no_alert_when_candidate_below_betweenness_threshold(monkeypatch) -> None:
    """A stressed node that isn't central enough is filtered.

    Two nodes: the stressed one is the most-central stressable node but its
    normalized betweenness (4/10 = 0.4) is below a raised 0.5 gate."""
    nodes = [
        _node("port:STRESSED", "port", "Stressed Port", supply_deficit_days=-14.0),
        _node("commodity:big", "commodity", "Big Commodity"),  # most central overall, no stress
    ]
    btw = {"port:STRESSED": 4.0, "commodity:big": 10.0}
    _patch(monkeypatch, nodes, btw)
    # Normalized betweenness of the stressed port = 0.4; gate at 0.5 → no fire.
    assert find_critical_stressed_node(betweenness_threshold=0.5) is None
    # Lower the gate below 0.4 → it fires.
    assert find_critical_stressed_node(betweenness_threshold=0.3) is not None


def test_stress_threshold_tightening_suppresses(monkeypatch) -> None:
    """Raising stress_threshold above the candidate's stress suppresses it."""
    nodes = [_node("chokepoint:c", "chokepoint", "Canal C", risk_score=0.4)]
    btw = {"chokepoint:c": 5.0}
    _patch(monkeypatch, nodes, btw)
    assert find_critical_stressed_node(stress_threshold=0.30) is not None  # 0.4 >= 0.3
    assert find_critical_stressed_node(stress_threshold=0.50) is None      # 0.4 < 0.5


def test_critical_threshold_tuning_changes_severity(monkeypatch) -> None:
    """A stress-0.50 node is HIGH by default but CRITICAL if the critical gate
    drops to 0.45."""
    nodes = [_node("chokepoint:c", "chokepoint", "Canal C", risk_score=0.50)]
    btw = {"chokepoint:c": 5.0}
    _patch(monkeypatch, nodes, btw)
    assert find_critical_stressed_node().severity == "HIGH"               # 0.50 < 0.60
    assert find_critical_stressed_node(
        critical_stress_threshold=0.45).severity == "CRITICAL"            # 0.50 >= 0.45


# ── Defensive / degenerate ───────────────────────────────────────────────


def test_empty_graph_returns_none(monkeypatch) -> None:
    _patch(monkeypatch, [], {})
    assert find_critical_stressed_node() is None


def test_graph_with_no_stressable_nodes_returns_none(monkeypatch) -> None:
    """All-abstract graph (commodities/routes/companies) → no candidate."""
    nodes = [
        _node("commodity:a", "commodity", "A"),
        _node("route:r", "route", "R"),
        _node("company:Z", "company", "Z"),
    ]
    btw = {"commodity:a": 10.0, "route:r": 5.0, "company:Z": 3.0}
    _patch(monkeypatch, nodes, btw)
    assert find_critical_stressed_node() is None


def test_build_world_graph_raising_degrades_to_none(monkeypatch) -> None:
    """A blow-up inside the build path is swallowed → None, never raises."""
    def boom(**kw):
        raise RuntimeError("graph build exploded")

    monkeypatch.setattr("processing.world_graph.build_world_graph", boom)
    assert find_critical_stressed_node() is None


def test_zero_max_betweenness_does_not_divide_by_zero(monkeypatch) -> None:
    """An edgeless graph → all-zero betweenness → no fire, no ZeroDivisionError."""
    nodes = [_node("port:p", "port", "Port P", supply_deficit_days=-14.0)]
    btw = {"port:p": 0.0}
    _patch(monkeypatch, nodes, btw)
    # max_btw == 0 → normalized betweenness 0 → below any positive gate → None.
    assert find_critical_stressed_node() is None
