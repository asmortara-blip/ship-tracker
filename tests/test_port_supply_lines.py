"""Defining-property tests for processing/port_supply_lines.py."""
from __future__ import annotations

import pytest

from processing.port_supply_lines import (
    PORT_SUPPLY_LINES_SOURCE,
    SEVERITY_LABELS,
    CompanyExposure,
    PortExposureChain,
    PortSupplyState,
    build_port_supply_chains,
)


# ── 1. Output shape ────────────────────────────────────────────────────────

def test_returns_one_chain_per_port_in_registry() -> None:
    """Every port in PORTS must produce one PortExposureChain — the join
    must not silently drop ports for which it has no equipment data."""
    from ports.port_registry import PORTS

    chains = build_port_supply_chains()
    assert len(chains) == len(PORTS)
    chain_locodes = {c.port.locode for c in chains}
    assert chain_locodes == {p.locode for p in PORTS}


def test_chain_structure_matches_dataclass_shape() -> None:
    chains = build_port_supply_chains()
    assert len(chains) > 0
    sample = chains[0]
    assert isinstance(sample, PortExposureChain)
    assert isinstance(sample.port, PortSupplyState)
    assert all(isinstance(c, CompanyExposure) for c in sample.exposed_companies)
    assert isinstance(sample.routes_touching, list)
    assert isinstance(sample.top_commodities, list)
    assert isinstance(sample.summary, str) and sample.summary


# ── 2. Severity labelling + ordering ──────────────────────────────────────

def test_severity_labels_match_documented_set() -> None:
    chains = build_port_supply_chains()
    for c in chains:
        assert c.port.severity_label in SEVERITY_LABELS


def test_chains_ordered_most_stressed_first() -> None:
    """The output must read most-stressed (most-negative deficit) first."""
    chains = build_port_supply_chains()
    deficits = [c.port.supply_deficit_days for c in chains]
    assert deficits == sorted(deficits)


def test_severity_band_breakpoints() -> None:
    """Pin the band thresholds — they're load-bearing for the UI colour ramp."""
    from processing.port_supply_lines import _severity_label
    assert _severity_label(-15.0) == "Critical Deficit"
    assert _severity_label(-10.0) == "Deficit"   # boundary: <-10 is critical, exactly -10 is Deficit
    assert _severity_label(-3.0)  == "Balanced"  # boundary: <-3 is Deficit, exactly -3 is Balanced
    assert _severity_label(0.0)   == "Balanced"
    assert _severity_label(3.0)   == "Balanced"  # upper edge inclusive
    assert _severity_label(5.0)   == "Surplus"
    assert _severity_label(10.0)  == "Surplus"   # upper edge inclusive
    assert _severity_label(15.0)  == "Heavy Surplus"


# ── 3. Per-port exposure correctness ──────────────────────────────────────

def test_exposed_companies_sorted_by_weight_desc() -> None:
    """Per-port `exposed_companies` must be ordered weight desc — the UI
    bar chart relies on this."""
    chains = build_port_supply_chains()
    for c in chains:
        if len(c.exposed_companies) < 2:
            continue
        weights = [ce.exposure_weight for ce in c.exposed_companies]
        assert weights == sorted(weights, reverse=True), (
            f"{c.port.locode}: companies out of order"
        )


def test_exposure_weights_are_nonnegative() -> None:
    """Exposure is a sum of products of non-negative weights — must be ≥ 0."""
    chains = build_port_supply_chains()
    for c in chains:
        for ce in c.exposed_companies:
            assert ce.exposure_weight >= 0.0


def test_top_commodities_sorted_by_weight_desc() -> None:
    chains = build_port_supply_chains()
    for c in chains:
        if len(c.top_commodities) < 2:
            continue
        weights = [w for _, w in c.top_commodities]
        assert weights == sorted(weights, reverse=True)


def test_port_with_no_routes_returns_empty_exposure() -> None:
    """A port with no routes registered must still produce a chain — just
    with empty routes / companies — rather than failing or being dropped."""
    chains = build_port_supply_chains()
    no_route_chains = [c for c in chains if not c.routes_touching]
    for c in no_route_chains:
        assert c.exposed_companies == []
        # Port state itself must still be populated.
        assert c.port.locode and c.port.name


# ── 4. Container-type knob honoured ───────────────────────────────────────

def test_container_type_parameter_changes_supply_state() -> None:
    """Different container types produce different per-port supply numbers."""
    dry_40 = {c.port.locode: c.port.supply_deficit_days
              for c in build_port_supply_chains(container_type="40FT_DRY")}
    reefer = {c.port.locode: c.port.supply_deficit_days
              for c in build_port_supply_chains(container_type="40FT_REEFER")}
    # At least one port must differ between the two container types —
    # otherwise the parameter has no effect.
    assert any(dry_40[k] != reefer[k] for k in dry_40.keys() & reefer.keys())


def test_container_type_propagates_into_port_state() -> None:
    """Each PortSupplyState carries the container_type it was computed for."""
    chains = build_port_supply_chains(container_type="40FT_REEFER")
    for c in chains:
        assert c.port.container_type == "40FT_REEFER"


# ── 5. top_n knobs honoured ───────────────────────────────────────────────

def test_top_n_companies_caps_the_list() -> None:
    chains = build_port_supply_chains(top_n_companies=3)
    for c in chains:
        assert len(c.exposed_companies) <= 3


def test_top_n_commodities_caps_the_list() -> None:
    chains = build_port_supply_chains(top_n_commodities=2)
    for c in chains:
        assert len(c.top_commodities) <= 2


# ── 6. Data source provenance ─────────────────────────────────────────────

def test_data_source_marker_exists() -> None:
    assert PORT_SUPPLY_LINES_SOURCE is not None
    # Reference fields the UI's source_footer reads.
    assert getattr(PORT_SUPPLY_LINES_SOURCE, "name", "") == "Port Supply Lines"
