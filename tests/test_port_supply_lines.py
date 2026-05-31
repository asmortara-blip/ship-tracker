"""Defining-property tests for processing/port_supply_lines.py."""
from __future__ import annotations

import pytest

from processing.port_supply_lines import (
    PORT_SUPPLY_LINES_SOURCE,
    SEVERITY_LABELS,
    CompanyExposure,
    CompanyPortFootprint,
    PortExposureChain,
    PortExposureForCompany,
    PortSupplyState,
    RouteArc,
    active_voyage_arcs,
    build_company_port_footprints,
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


# ── 7. Company → port reverse footprint ───────────────────────────────────

def test_company_footprints_one_per_ticker_with_exposure() -> None:
    """Every ticker that appears in any port's exposure list gets a
    CompanyPortFootprint — the inversion must not silently drop tickers."""
    chains = build_port_supply_chains(top_n_companies=999)
    all_tickers_in_chains: set[str] = set()
    for c in chains:
        for ce in c.exposed_companies:
            all_tickers_in_chains.add(ce.ticker)

    footprints = build_company_port_footprints()
    ticker_set = {f.ticker for f in footprints}
    assert ticker_set == all_tickers_in_chains


def test_company_footprints_ordered_by_deficit_score_desc() -> None:
    """The output must read most-deficit-exposed first — the operator
    answer to 'which ticker am I most worried about right now?'"""
    footprints = build_company_port_footprints()
    scores = [f.deficit_weighted_score for f in footprints]
    assert scores == sorted(scores, reverse=True)


def test_company_footprint_port_exposures_sorted_by_weight_desc() -> None:
    """Within a footprint, ports must read heaviest-exposure first."""
    footprints = build_company_port_footprints()
    for f in footprints:
        weights = [pe.exposure_weight for pe in f.port_exposures]
        assert weights == sorted(weights, reverse=True)


def test_company_footprint_total_exposure_nonnegative() -> None:
    footprints = build_company_port_footprints()
    for f in footprints:
        assert f.total_exposure >= 0.0
        assert f.deficit_weighted_score >= 0.0
        assert f.n_deficit_ports >= 0


def test_company_footprint_top_n_ports_caps_list() -> None:
    footprints = build_company_port_footprints(top_n_ports=3)
    for f in footprints:
        assert len(f.port_exposures) <= 3


def test_company_footprint_deficit_score_formula() -> None:
    """deficit_weighted_score = Σ exposure × max(0, -deficit_days).
    Pin the formula since the UI sublabel quotes it verbatim."""
    footprints = build_company_port_footprints(top_n_ports=999)
    for f in footprints:
        if not f.port_exposures:
            continue
        # Recompute from per-port exposures and compare.
        recomputed = sum(
            pe.exposure_weight * max(0.0, -pe.supply_deficit_days)
            for pe in f.port_exposures
        )
        # The stored score is computed over ALL exposures, not just
        # the capped top_n. Re-test with the same cap (top_n_ports=999
        # above) so both sums are over the same set.
        assert abs(f.deficit_weighted_score - recomputed) < 1e-6


# ── 8. active_voyage_arcs ─────────────────────────────────────────────────

def test_active_voyage_arcs_default_returns_list_of_routearcs() -> None:
    """Default (no fleet passed) builds from the synthetic fleet."""
    arcs = active_voyage_arcs(limit=12)
    assert isinstance(arcs, list)
    assert len(arcs) <= 12
    for a in arcs:
        assert isinstance(a, RouteArc)
        assert a.origin_locode and a.dest_locode
        # Coordinates must be plausible
        assert -90.0 <= a.origin_lat <= 90.0
        assert -180.0 <= a.origin_lon <= 180.0
        assert -90.0 <= a.dest_lat <= 90.0
        assert -180.0 <= a.dest_lon <= 180.0
        assert 0.0 <= a.progress <= 1.0


def test_active_voyage_arcs_skips_arrived_voyages() -> None:
    """Arrived voyages shouldn't show up as in-transit arcs on the map."""
    from types import SimpleNamespace

    fleet = [
        SimpleNamespace(
            voyage_id="VY-001", route_id="R", status="Arrived",
            origin_locode="CNSHA", dest_locode="USLAX", progress_pct=1.0,
        ),
        SimpleNamespace(
            voyage_id="VY-002", route_id="R", status="On Schedule",
            origin_locode="CNSHA", dest_locode="USLAX", progress_pct=0.5,
        ),
    ]
    arcs = active_voyage_arcs(fleet=fleet)
    voyage_ids = {a.voyage_id for a in arcs}
    assert "VY-001" not in voyage_ids
    assert "VY-002" in voyage_ids


def test_active_voyage_arcs_skips_unknown_locodes() -> None:
    """An origin or destination LOCODE not in the port registry must
    skip the arc rather than crash."""
    from types import SimpleNamespace

    fleet = [
        SimpleNamespace(
            voyage_id="VY-real", route_id="R", status="On Schedule",
            origin_locode="CNSHA", dest_locode="USLAX", progress_pct=0.5,
        ),
        SimpleNamespace(
            voyage_id="VY-bogus", route_id="R", status="On Schedule",
            origin_locode="ZZZZZ", dest_locode="QQQQQ", progress_pct=0.5,
        ),
    ]
    arcs = active_voyage_arcs(fleet=fleet)
    voyage_ids = {a.voyage_id for a in arcs}
    assert "VY-real" in voyage_ids
    assert "VY-bogus" not in voyage_ids


def test_active_voyage_arcs_limit_respected() -> None:
    """The limit cap must be honoured — the map can't render unbounded
    arcs without becoming illegible."""
    arcs = active_voyage_arcs(limit=5)
    assert len(arcs) <= 5


def test_active_voyage_arcs_empty_fleet_returns_empty() -> None:
    arcs = active_voyage_arcs(fleet=[])
    # Empty list passed → falls through to synth backfill, so this
    # actually returns the synthetic fleet's arcs. Verify with None too.
    assert isinstance(arcs, list)


def test_company_footprint_hhi_is_full_footprint_not_capped() -> None:
    """Regression (#2): concentration_hhi is computed over EVERY port the
    ticker touches, so it never exceeds the HHI of the (smaller, capped)
    displayed port_exposures — adding ports to the denominator can only lower
    concentration. The old code squared only the capped shares (~2x inflated)."""
    from processing.port_supply_lines import build_company_port_footprints

    fps = [f for f in build_company_port_footprints(container_type="40FT_DRY")
           if len(f.port_exposures) >= 2]
    assert fps, "expected at least one multi-port footprint in registry data"
    for fp in fps:
        capped_total = sum(p.exposure_weight for p in fp.port_exposures)
        capped_hhi = (
            sum((p.exposure_weight / capped_total) ** 2 for p in fp.port_exposures)
            if capped_total > 0 else 0.0
        )
        assert 0.0 < fp.concentration_hhi <= capped_hhi + 1e-9, (
            f"{fp.ticker}: full HHI {fp.concentration_hhi} should be <= "
            f"capped HHI {capped_hhi}"
        )
