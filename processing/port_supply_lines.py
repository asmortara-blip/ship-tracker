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
    "PortExposureForCompany",
    "CompanyPortFootprint",
    "RouteArc",
    "SEVERITY_LABELS",
    "build_port_supply_chains",
    "build_company_port_footprints",
    "active_voyage_arcs",
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


@dataclass
class PortExposureForCompany:
    """How heavily one ticker is exposed to one port via the supply chain."""

    port_locode: str
    port_name: str
    region: str
    supply_deficit_days: float
    severity_label: str
    lat: float
    lon: float
    exposure_weight: float        # same units as CompanyExposure.exposure_weight


@dataclass
class CompanyPortFootprint:
    """Inverted view of the port supply chain — one company's footprint
    across every port it touches.

    The list is ordered by ``exposure_weight`` desc, so the ports the
    ticker is most dependent on come first. ``deficit_weighted_score``
    captures the company's blended exposure to *stressed* ports: each
    port contributes ``exposure × max(0, -supply_deficit_days)`` — a
    big number means the company has heavy exposure to ports that are
    currently short on containers.
    """

    ticker: str
    port_exposures: list[PortExposureForCompany] = field(default_factory=list)
    total_exposure: float = 0.0
    # Herfindahl-Hirschman concentration index over the FULL footprint
    # (every port the ticker touches), in [0, 1]. Computed here because
    # ``port_exposures`` is capped to the top-N for display — squaring only
    # the capped shares would overstate concentration. 0.0 when unknown.
    concentration_hhi: float = 0.0
    deficit_weighted_score: float = 0.0
    n_deficit_ports: int = 0
    summary: str = ""


@dataclass
class RouteArc:
    """One in-transit voyage rendered as a great-circle arc on the map.

    ``progress`` is in ``[0, 1]`` — the UI uses it to dim arcs that are
    near completion vs. fresh departures.
    """

    voyage_id: str
    route_id: str
    origin_locode: str
    origin_lat: float
    origin_lon: float
    dest_locode: str
    dest_lat: float
    dest_lon: float
    status: str
    progress: float


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


def build_company_port_footprints(
    *,
    container_type: str = "40FT_DRY",
    top_n_ports: int = 8,
) -> list[CompanyPortFootprint]:
    """Inverted view of the supply chain — per ticker, the ports it
    touches most heavily.

    Reuses ``build_port_supply_chains`` so the per-port exposure weights
    are computed exactly the same way as the forward view. For every
    ticker that appears in at least one port's exposure list, this
    function collects every port × exposure pair, sorts ports by
    exposure desc (top ``top_n_ports``), and computes a
    ``deficit_weighted_score`` that captures the company's blended
    exposure to *stressed* ports.

    The list is ordered by ``deficit_weighted_score`` desc — so the
    ticker most exposed to currently-stressed ports surfaces first.
    """
    chains = build_port_supply_chains(
        container_type=container_type,
        top_n_companies=999,   # we want every company per port for the invert
        top_n_commodities=999,
    )

    # Group exposures by ticker.
    by_ticker: dict[str, list[PortExposureForCompany]] = {}
    for chain in chains:
        for ce in chain.exposed_companies:
            entry = PortExposureForCompany(
                port_locode=chain.port.locode,
                port_name=chain.port.name,
                region=chain.port.region,
                supply_deficit_days=chain.port.supply_deficit_days,
                severity_label=chain.port.severity_label,
                lat=chain.port.lat,
                lon=chain.port.lon,
                exposure_weight=ce.exposure_weight,
            )
            by_ticker.setdefault(ce.ticker, []).append(entry)

    footprints: list[CompanyPortFootprint] = []
    for ticker, exposures in by_ticker.items():
        exposures.sort(key=lambda e: e.exposure_weight, reverse=True)
        capped = exposures[:max(1, int(top_n_ports))]
        total = sum(e.exposure_weight for e in exposures)
        # HHI over EVERY port the ticker touches (not the top-N display cap):
        # concentration is a property of the whole distribution, so the
        # shares must sum to 1 over the full footprint.
        hhi = (
            sum((e.exposure_weight / total) ** 2
                for e in exposures if e.exposure_weight > 0)
            if total > 0 else 0.0
        )
        deficit_score = sum(
            e.exposure_weight * max(0.0, -e.supply_deficit_days)
            for e in exposures
        )
        n_deficit = sum(1 for e in exposures if e.supply_deficit_days < 0)
        top_port_names = ", ".join(e.port_name for e in capped[:3])
        summary = (
            f"{ticker}: top exposure to {top_port_names} "
            f"({len(exposures)} ports total, {n_deficit} in deficit; "
            f"deficit-weighted score {deficit_score:.3f})."
        )
        footprints.append(CompanyPortFootprint(
            ticker=ticker,
            port_exposures=capped,
            total_exposure=round(total, 6),
            concentration_hhi=round(hhi, 6),
            deficit_weighted_score=round(deficit_score, 6),
            n_deficit_ports=n_deficit,
            summary=summary,
        ))

    footprints.sort(key=lambda f: f.deficit_weighted_score, reverse=True)
    return footprints


def active_voyage_arcs(
    *,
    fleet: Iterable | None = None,
    limit: int = 60,
) -> list[RouteArc]:
    """Return one RouteArc per in-transit voyage in the modeled fleet.

    Used by the world map to overlay great-circle origin→destination
    lines so the operator sees the literal supply lines flowing
    through the port network, not just per-port status markers.

    ``fleet`` defaults to the deterministic synthetic fleet built by
    ``data.voyage_dataset.build_voyage_fleet``. ``limit`` caps the
    number of arcs rendered so the map doesn't drown in lines when
    the fleet is dense (oldest-first; capped to the ``limit``
    voyages with the highest progress so the most-near-arrival arcs
    are preserved). Voyages with unknown origin / destination
    coordinates are skipped silently.
    """
    voyages = list(fleet or [])
    if not voyages:
        try:
            from data.voyage_dataset import build_voyage_fleet
            voyages = list(build_voyage_fleet())
        except Exception:
            return []

    # Index ports by locode for O(1) coordinate lookup.
    try:
        from ports.port_registry import PORTS
    except Exception:
        return []
    by_locode: dict[str, object] = {p.locode: p for p in PORTS}

    in_transit = [v for v in voyages
                  if str(getattr(v, "status", "")) != "Arrived"]
    in_transit.sort(
        key=lambda v: float(getattr(v, "progress_pct", 0.0) or 0.0),
        reverse=True,
    )
    in_transit = in_transit[:max(1, int(limit))]

    arcs: list[RouteArc] = []
    for v in in_transit:
        origin = by_locode.get(getattr(v, "origin_locode", ""))
        dest   = by_locode.get(getattr(v, "dest_locode", ""))
        if origin is None or dest is None:
            continue
        arcs.append(RouteArc(
            voyage_id=str(getattr(v, "voyage_id", "")),
            route_id=str(getattr(v, "route_id", "")),
            origin_locode=origin.locode,
            origin_lat=float(origin.lat),
            origin_lon=float(origin.lon),
            dest_locode=dest.locode,
            dest_lat=float(dest.lat),
            dest_lon=float(dest.lon),
            status=str(getattr(v, "status", "")),
            progress=float(getattr(v, "progress_pct", 0.0) or 0.0),
        ))
    return arcs
