"""port_supply_lines.py — port supply state × company exposure joiner.

Builds the full chain that ties a port's container-supply state to the
publicly-traded shipping companies that ride supply lines through it:

  port (Shanghai → deficit 8d on 40FT_DRY)
    → routes touching the port (Trans-Pacific EB, Asia-Europe, …)
    → commodity mix carried on each route (Electronics 0.42, Textiles 0.18, …)
    → companies exposed to those commodities (ZIM 0.31, MATX 0.22, …)

This module is the single join across:

  * ``ports.port_registry.PORTS``                            — port coords + region
  * ``processing.equipment_tracker.REGIONAL_EQUIPMENT_STATUS`` — regional surplus / deficit
  * ``routes.route_registry.ROUTES``                         — origin/dest LOCODE pairs
  * ``processing.cargo_analyzer.get_route_cargo_mix``        — per-route cargo weights
  * ``processing.exposure_matrix.COMPANY_COMMODITY_EXPOSURE``— per-company commodity weights

The output is a list of ``PortExposureChain`` per port, ordered from
most-stressed to most-surplus. The UI layer renders this as a world map
plus a per-port drill-down (top exposed companies + cargo-mix
Sankey).

Transparent rule-based join — every weight is published, every aggregate
is a sum, no fitted model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from data.quality import DataSource


__all__ = [
    "PortSupplyState",
    "CompanyExposure",
    "PortExposureChain",
    "SEVERITY_LABELS",
    "build_port_supply_chains",
    "PORT_SUPPLY_LINES_SOURCE",
]


# Ordered weakest → strongest by *deficit severity* (most negative deficit
# first). The UI uses this ordering for the colour ramp.
SEVERITY_LABELS: tuple[str, ...] = (
    "Critical Deficit",   # < -10 days
    "Deficit",            # -10 to -3
    "Balanced",           # -3 to +3
    "Surplus",            # +3 to +10
    "Heavy Surplus",      # > +10
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PortSupplyState:
    """Per-port container-supply snapshot.

    The supply signal is read from the regional `REGIONAL_EQUIPMENT_STATUS`
    snapshot for the port's region — the equipment_tracker module currently
    publishes only regional resolution, so all ports in a region share the
    same `supply_deficit_days` figure. The UI surfaces this explicitly so
    nobody mistakes the regional roll-up for a per-port observation.
    """

    locode: str
    name: str
    region: str
    country_iso3: str
    lat: float
    lon: float
    supply_deficit_days: float    # positive = surplus, negative = deficit
    utilization_pct: float        # 0-100 (regional avg for this region+ctype)
    severity_label: str           # one of SEVERITY_LABELS
    container_type: str           # e.g. "40FT_DRY"


@dataclass
class CompanyExposure:
    """One ticker's exposure to a single port via the route/commodity chain.

    ``exposure_weight`` is the dimensionless sum of
    ``route_share × cargo_weight × company_weight`` across every route
    touching the port and every commodity that route carries. Larger
    values mean the company has a bigger fraction of its overall
    revenue base tied to traffic that flows through this port.
    """

    ticker: str
    exposure_weight: float
    via_commodities: list[str] = field(default_factory=list)   # top hs_categories
    via_routes:      list[str] = field(default_factory=list)   # route names


@dataclass
class PortExposureChain:
    """Full chain for one port: supply state + exposed companies + supporting routes."""

    port: PortSupplyState
    exposed_companies: list[CompanyExposure] = field(default_factory=list)
    routes_touching:   list[str] = field(default_factory=list)   # route names
    top_commodities:   list[tuple[str, float]] = field(default_factory=list)
                                                                 # (hs_category, total_weight)
    summary: str = ""


PORT_SUPPLY_LINES_SOURCE = DataSource.modeled(
    "Port Supply Lines",
    notes=(
        "Joins regional container-supply state from processing.equipment_tracker "
        "with route registry + cargo_analyzer cargo mix + exposure_matrix "
        "company weights to surface 'this port's deficit → these companies "
        "are exposed' chains."
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _severity_label(deficit_days: float) -> str:
    """Map a (signed) deficit-day count to a severity label.

    The bands are: < -10 / -10..-3 / -3..+3 / +3..+10 / > +10.
    """
    if deficit_days < -10:
        return "Critical Deficit"
    if deficit_days < -3:
        return "Deficit"
    if deficit_days <= 3:
        return "Balanced"
    if deficit_days <= 10:
        return "Surplus"
    return "Heavy Surplus"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_port_supply_chains(
    *,
    container_type: str = "40FT_DRY",
    top_n_companies: int = 8,
    top_n_commodities: int = 5,
) -> list[PortExposureChain]:
    """Build the full port supply-lines join.

    Parameters
    ----------
    container_type:
        Which container-type slice of the regional supply data to use.
        Must be a key in ``processing.equipment_tracker.CONTAINER_TYPES``.
        Defaults to ``"40FT_DRY"`` (the dominant container in volume).
    top_n_companies:
        Cap on the per-port exposed-companies list. Sorted by exposure
        weight descending.
    top_n_commodities:
        Cap on the per-port top-commodities summary. Sorted by aggregate
        weight descending.

    Returns
    -------
    list[PortExposureChain]
        Ordered from most-stressed (most-negative deficit) to
        most-surplus. Every port in ``ports.port_registry.PORTS`` is
        represented; ports whose region has no equipment data degrade
        to a "Balanced" / 0-day state and surface their route + company
        exposures regardless.
    """
    # Lazy imports so a failure in any downstream module doesn't block
    # this module's import — the join still produces a partial result.
    from ports.port_registry import PORTS
    from processing.equipment_tracker import REGIONAL_EQUIPMENT_STATUS
    from routes.route_registry import ROUTES

    try:
        from processing.cargo_analyzer import get_route_cargo_mix
    except Exception:  # pragma: no cover - defensive
        def get_route_cargo_mix(_rid: str, _td: dict) -> dict[str, float]:
            return {}

    try:
        from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE
    except Exception:  # pragma: no cover - defensive
        COMPANY_COMMODITY_EXPOSURE = {}  # type: ignore[assignment]

    # ── 1. Regional supply state lookup ────────────────────────────────────
    # Index regional equipment status by (region, container_type) so the
    # per-port join is O(1).
    by_region: dict[tuple[str, str], object] = {
        (e.region, e.container_type): e
        for e in REGIONAL_EQUIPMENT_STATUS
    }

    # ── 2. Index routes by port ────────────────────────────────────────────
    # Maps LOCODE → list of routes touching that port (as origin OR dest).
    routes_by_port: dict[str, list] = {}
    for route in ROUTES:
        for locode in (route.origin_locode, route.dest_locode):
            routes_by_port.setdefault(locode, []).append(route)

    # ── 3. Per-port exposure chain ─────────────────────────────────────────
    chains: list[PortExposureChain] = []
    for port in PORTS:
        # Look up regional equipment state for this port's region.
        equip = by_region.get((port.region, container_type))
        if equip is not None:
            deficit = float(getattr(equip, "days_surplus_deficit", 0.0) or 0.0)
            util = float(getattr(equip, "utilization_pct", 0.0) or 0.0)
        else:
            deficit, util = 0.0, 0.0

        port_state = PortSupplyState(
            locode=port.locode,
            name=port.name,
            region=port.region,
            country_iso3=port.country_iso3,
            lat=port.lat,
            lon=port.lon,
            supply_deficit_days=deficit,
            utilization_pct=util,
            severity_label=_severity_label(deficit),
            container_type=container_type,
        )

        # Routes touching this port + per-route cargo mix
        routes_here = routes_by_port.get(port.locode, [])
        route_names = [r.name for r in routes_here]
        # Aggregate cargo mix across all routes — equal weighting since
        # we don't have per-route volume here.
        commodity_totals: dict[str, float] = {}
        for r in routes_here:
            try:
                mix = get_route_cargo_mix(r.id, {}) or {}
            except Exception:
                mix = {}
            for hs, share in mix.items():
                commodity_totals[hs] = commodity_totals.get(hs, 0.0) + float(share)
        top_commodities = sorted(
            commodity_totals.items(), key=lambda kv: kv[1], reverse=True
        )[:top_n_commodities]

        # Per-company exposure via the chain
        # exposure = sum_over(routes, commodities) of
        #   route_share × cargo_weight[hs] × company_weight[hs]
        # We normalise route_share to 1.0 / N (equal weighting) since
        # the registry doesn't carry per-route volume.
        n_routes = max(1, len(routes_here))
        company_exposure: dict[str, float] = {}
        company_via_commodities: dict[str, set[str]] = {}
        for ticker, company_weights in COMPANY_COMMODITY_EXPOSURE.items():
            weight = 0.0
            via: set[str] = set()
            for hs, total_cargo_weight in commodity_totals.items():
                # Average cargo weight across routes for this commodity
                avg_cargo = total_cargo_weight / n_routes
                cw = float(company_weights.get(hs, 0.0))
                if cw <= 0.0:
                    continue
                weight += avg_cargo * cw
                via.add(hs)
            if weight > 0:
                company_exposure[ticker] = weight
                company_via_commodities[ticker] = via

        ranked_companies = sorted(
            company_exposure.items(), key=lambda kv: kv[1], reverse=True
        )[:top_n_companies]
        exposed = [
            CompanyExposure(
                ticker=ticker,
                exposure_weight=round(weight, 6),
                via_commodities=sorted(company_via_commodities[ticker])[:5],
                via_routes=route_names[:5],
            )
            for ticker, weight in ranked_companies
        ]

        summary = (
            f"{port_state.name} ({port.locode}, {port.region}) — "
            f"{port_state.severity_label}: {deficit:+.1f}d on {container_type}; "
            f"{len(routes_here)} route(s) touching; "
            f"{len(exposed)} companies exposed."
        )

        chains.append(PortExposureChain(
            port=port_state,
            exposed_companies=exposed,
            routes_touching=route_names,
            top_commodities=top_commodities,
            summary=summary,
        ))

    # Order most-stressed (most-negative deficit) first.
    chains.sort(key=lambda c: c.port.supply_deficit_days)
    return chains
